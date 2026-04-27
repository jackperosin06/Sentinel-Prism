"""Append-only audit event persistence (Story 3.8)."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, select, true as sql_true
from sqlalchemy.ext.asyncio import AsyncSession

from sentinel_prism.db.audit_constants import (
    CLASSIFICATION_CONFIG_AUDIT_RUN_ID,
    GOLDEN_SET_CONFIG_AUDIT_RUN_ID,
    ROUTING_CONFIG_AUDIT_RUN_ID,
)
from sentinel_prism.db.models import AuditEvent, NormalizedUpdateRow, PipelineAuditAction

logger = logging.getLogger(__name__)

# Public: imported by ``graph.nodes.scout`` so the cap stays defined in one
# place. Bounded sample list protects NFR12 (no raw captures in metadata).
ITEM_URL_SAMPLES_CAP = 10

# Per-URL length cap — a malicious or malformed source could otherwise push
# multi-kilobyte URLs into JSONB. 512 chars covers legitimate regulatory URLs
# with deep query strings while bounding row size.
_MAX_URL_LENGTH = 512

# Soft ceiling on total serialized metadata size. Exceeding this does NOT
# raise (the audit row must always persist per AC #3) — we emit a warning so
# operators can spot metadata bloat during story 3.8 roll-out and Epic 8.
_MAX_METADATA_BYTES = 8192

# When ``normalized_updates.run_id`` is null, bound audit rows by source + time
# around the update's ``created_at`` (Story 8.1 — FR34).
_NORMALIZED_UPDATE_AUDIT_HALF_WINDOW = timedelta(hours=24)

DEFAULT_SEARCH_LIMIT = 50
MAX_SEARCH_LIMIT = 200
MAX_SEARCH_OFFSET = 50_000


def _parse_run_id(run_id: str | UUID) -> UUID | None:
    if isinstance(run_id, UUID):
        return run_id
    try:
        return UUID(str(run_id).strip())
    except (ValueError, TypeError, AttributeError):
        logger.warning(
            "audit_events",
            extra={
                "event": "audit_run_id_invalid",
                "ctx": {"run_id": run_id},
            },
        )
        return None


def _coerce_action(action: str | PipelineAuditAction) -> PipelineAuditAction:
    if isinstance(action, PipelineAuditAction):
        return action
    return PipelineAuditAction(action)


def _trim_metadata(meta: dict[str, Any] | None) -> dict[str, Any] | None:
    if meta is None:
        return None
    if not isinstance(meta, dict):
        # Fail loudly at the write boundary. ``JSONB`` would silently accept
        # lists/scalars, but the column typing and spec contract demand a dict.
        raise TypeError(
            f"audit metadata must be dict or None, got {type(meta).__name__}"
        )
    out = dict(meta)
    samples = out.get("item_url_samples")
    if isinstance(samples, list):
        trimmed: list[str] = []
        for s in samples[:ITEM_URL_SAMPLES_CAP]:
            text = s if isinstance(s, str) else str(s)
            trimmed.append(text[:_MAX_URL_LENGTH])
        out["item_url_samples"] = trimmed
    try:
        size_bytes = len(json.dumps(out, default=str).encode("utf-8"))
    except (TypeError, ValueError):
        size_bytes = 0
    if size_bytes > _MAX_METADATA_BYTES:
        logger.warning(
            "audit_events",
            extra={
                "event": "audit_metadata_oversize",
                "ctx": {
                    "size_bytes": size_bytes,
                    "limit_bytes": _MAX_METADATA_BYTES,
                    "keys": sorted(out.keys()),
                },
            },
        )
    return out


async def has_audit_event_for_run(
    session: AsyncSession,
    *,
    run_id: str | UUID,
    action: PipelineAuditAction | str,
) -> bool:
    """Return whether an audit row already exists for this run and action."""

    rid = _parse_run_id(run_id)
    if rid is None:
        return False
    act = _coerce_action(action)
    res = await session.execute(
        select(AuditEvent.id)
        .where(AuditEvent.run_id == rid, AuditEvent.action == act)
        .limit(1)
    )
    return res.scalar_one_or_none() is not None


async def append_audit_event(
    session: AsyncSession,
    *,
    run_id: str | UUID,
    action: str | PipelineAuditAction,
    source_id: UUID | None,
    metadata: dict[str, Any] | None = None,
    actor_user_id: UUID | None = None,
) -> UUID | None:
    """Insert one audit row (INSERT only). Returns ``None`` if ``run_id`` is not a UUID."""

    rid = _parse_run_id(run_id)
    if rid is None:
        return None
    act = _coerce_action(action)
    row = AuditEvent(
        run_id=rid,
        action=act,
        source_id=source_id,
        actor_user_id=actor_user_id,
        event_metadata=_trim_metadata(metadata),
    )
    session.add(row)
    await session.flush()
    return row.id


async def append_routing_config_audit(
    session: AsyncSession,
    *,
    actor_user_id: uuid.UUID,
    op: str,
    rule_id: uuid.UUID,
    rule_type: str,
    metadata: dict[str, Any] | None = None,
) -> uuid.UUID | None:
    """Append a routing mock-table config audit row (Story 6.3 — FR33).

    Uses :data:`~sentinel_prism.db.audit_constants.ROUTING_CONFIG_AUDIT_RUN_ID`
    for ``run_id`` because configuration changes are not tied to a pipeline run.
    """

    meta: dict[str, Any] = {
        "op": op,
        "rule_id": str(rule_id),
        "rule_type": rule_type,
    }
    if metadata:
        meta.update(metadata)
    return await append_audit_event(
        session,
        run_id=ROUTING_CONFIG_AUDIT_RUN_ID,
        action=PipelineAuditAction.ROUTING_CONFIG_CHANGED,
        source_id=None,
        metadata=meta,
        actor_user_id=actor_user_id,
    )


async def append_classification_config_audit(
    session: AsyncSession,
    *,
    actor_user_id: uuid.UUID,
    prior_version: int,
    new_version: int,
    prior_threshold: float,
    new_threshold: float,
    prior_prompt_sha256_prefix: str,
    new_prompt_sha256_prefix: str,
    prior_prompt_length: int,
    new_prompt_length: int,
    reason: str | None,
) -> uuid.UUID | None:
    """Append classification policy apply audit (Story 7.3 — FR29, NFR13)."""

    meta: dict[str, Any] = {
        "op": "apply",
        "prior_version": prior_version,
        "new_version": new_version,
        "prior_low_confidence_threshold": prior_threshold,
        "new_low_confidence_threshold": new_threshold,
        "prior_system_prompt_sha256_16": prior_prompt_sha256_prefix,
        "new_system_prompt_sha256_16": new_prompt_sha256_prefix,
        "prior_system_prompt_length": prior_prompt_length,
        "new_system_prompt_length": new_prompt_length,
    }
    if reason is not None:
        meta["reason"] = reason
    return await append_audit_event(
        session,
        run_id=CLASSIFICATION_CONFIG_AUDIT_RUN_ID,
        action=PipelineAuditAction.CLASSIFICATION_CONFIG_CHANGED,
        source_id=None,
        metadata=meta,
        actor_user_id=actor_user_id,
    )


async def append_golden_set_config_audit(
    session: AsyncSession,
    *,
    actor_user_id: uuid.UUID,
    prior_version: int,
    new_version: int,
    prior_refresh_cadence: str,
    new_refresh_cadence: str,
    prior_refresh_after_major: bool,
    new_refresh_after_major: bool,
    prior_label_sha256_prefix: str,
    new_label_sha256_prefix: str,
    prior_label_length: int,
    new_label_length: int,
    reason: str | None,
) -> uuid.UUID | None:
    """Append golden-set policy apply audit (Story 7.4 — FR45, NFR13)."""

    meta: dict[str, Any] = {
        "op": "apply",
        "prior_version": prior_version,
        "new_version": new_version,
        "prior_refresh_cadence": prior_refresh_cadence,
        "new_refresh_cadence": new_refresh_cadence,
        "prior_refresh_after_major_classification_change": prior_refresh_after_major,
        "new_refresh_after_major_classification_change": new_refresh_after_major,
        "prior_label_policy_sha256_16": prior_label_sha256_prefix,
        "new_label_policy_sha256_16": new_label_sha256_prefix,
        "prior_label_policy_length": prior_label_length,
        "new_label_policy_length": new_label_length,
    }
    if reason is not None:
        meta["reason"] = reason
    return await append_audit_event(
        session,
        run_id=GOLDEN_SET_CONFIG_AUDIT_RUN_ID,
        action=PipelineAuditAction.GOLDEN_SET_CONFIG_CHANGED,
        source_id=None,
        metadata=meta,
        actor_user_id=actor_user_id,
    )


async def list_recent_for_run(
    session: AsyncSession,
    *,
    run_id: str | UUID,
    limit: int = 20,
) -> list[AuditEvent]:
    """Return newest audit rows for ``run_id`` (read-only; Epic 8 may extend)."""

    rid = _parse_run_id(run_id)
    if rid is None:
        return []
    lim = max(1, min(limit, 100))
    res = await session.scalars(
        select(AuditEvent)
        .where(AuditEvent.run_id == rid)
        .order_by(AuditEvent.created_at.desc())
        .limit(lim)
    )
    return list(res.all())


def _clamp_search_pagination(*, limit: int, offset: int) -> tuple[int, int]:
    lim = max(1, min(limit, MAX_SEARCH_LIMIT))
    off = max(0, min(offset, MAX_SEARCH_OFFSET))
    return lim, off


def _audit_search_conditions(
    *,
    run_id: UUID | None,
    source_id: UUID | None,
    actor_user_id: UUID | None,
    created_after: datetime | None,
    created_before: datetime | None,
    action: PipelineAuditAction | None,
    normalized_update_row: NormalizedUpdateRow | None,
) -> list[Any]:
    clauses: list[Any] = []
    if run_id is not None:
        clauses.append(AuditEvent.run_id == run_id)
    if source_id is not None:
        clauses.append(AuditEvent.source_id == source_id)
    if actor_user_id is not None:
        clauses.append(AuditEvent.actor_user_id == actor_user_id)
    if created_after is not None:
        clauses.append(AuditEvent.created_at >= created_after)
    if created_before is not None:
        clauses.append(AuditEvent.created_at <= created_before)
    if action is not None:
        clauses.append(AuditEvent.action == action)

    if normalized_update_row is not None:
        nu = normalized_update_row
        if nu.run_id is not None:
            clauses.append(AuditEvent.run_id == nu.run_id)
        else:
            clauses.append(AuditEvent.source_id == nu.source_id)
            t0 = nu.created_at - _NORMALIZED_UPDATE_AUDIT_HALF_WINDOW
            t1 = nu.created_at + _NORMALIZED_UPDATE_AUDIT_HALF_WINDOW
            clauses.append(AuditEvent.created_at >= t0)
            clauses.append(AuditEvent.created_at <= t1)

    return clauses


async def search_audit_events(
    session: AsyncSession,
    *,
    run_id: UUID | None = None,
    source_id: UUID | None = None,
    actor_user_id: UUID | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    action: PipelineAuditAction | None = None,
    normalized_update_row: NormalizedUpdateRow | None = None,
    limit: int = DEFAULT_SEARCH_LIMIT,
    offset: int = 0,
) -> tuple[list[AuditEvent], int]:
    """Paginated audit search (Story 8.1 — FR34).

    When ``normalized_update_row`` is set, filters narrow to audits for that
    update: by ``run_id`` when the row has one; otherwise ``source_id`` plus
    ``created_at`` within ±24h of the update's ``created_at`` (inclusive bounds).
    These predicates are **and**-merged with the explicit filter arguments.
    """

    lim, off = _clamp_search_pagination(limit=limit, offset=offset)
    cond = _audit_search_conditions(
        run_id=run_id,
        source_id=source_id,
        actor_user_id=actor_user_id,
        created_after=created_after,
        created_before=created_before,
        action=action,
        normalized_update_row=normalized_update_row,
    )
    where_expr = and_(*cond) if cond else sql_true()

    count_stmt = select(func.count()).select_from(AuditEvent).where(where_expr)
    total = int(await session.scalar(count_stmt) or 0)

    list_stmt = (
        select(AuditEvent)
        .where(where_expr)
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .limit(lim)
        .offset(off)
    )
    res = await session.scalars(list_stmt)
    return list(res.all()), total
