"""Append-only audit event persistence (Story 3.8)."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sentinel_prism.db.models import AuditEvent, PipelineAuditAction

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
