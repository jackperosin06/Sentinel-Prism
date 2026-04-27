"""Golden-set label policy singleton — active + draft (Story 7.4 — FR44, FR45)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sentinel_prism.db.audit_constants import GOLDEN_SET_CONFIG_AUDIT_RUN_ID
from sentinel_prism.db.models import (
    AuditEvent,
    GoldenSetPolicy,
    GoldenSetRefreshCadence,
    PipelineAuditAction,
    User,
)
from sentinel_prism.db.repositories import audit_events as audit_repo
from sentinel_prism.db.repositories.classification_policy import prompt_sha256_prefix

GOLDEN_SET_SINGLETON_ID = 1

MAX_LABEL_POLICY_LENGTH = 32768


def _validate_cadence(value: str) -> str:
    allowed = {c.value for c in GoldenSetRefreshCadence}
    if value not in allowed:
        raise ValueError(f"refresh_cadence must be one of: {', '.join(sorted(allowed))}")
    return value


def _validate_label_policy(value: str) -> str:
    if not value.strip():
        raise ValueError("label_policy_text must not be blank")
    if len(value) > MAX_LABEL_POLICY_LENGTH:
        raise ValueError(
            f"label_policy_text exceeds {MAX_LABEL_POLICY_LENGTH} characters"
        )
    return value


@dataclass(frozen=True, slots=True)
class ActiveGoldenSetPolicy:
    version: int
    label_policy_text: str
    refresh_cadence: str
    refresh_after_major_classification_change: bool


@dataclass(frozen=True, slots=True)
class DraftGoldenSetPolicy:
    label_policy_text: str
    refresh_cadence: str
    refresh_after_major_classification_change: bool
    reason: str | None


@dataclass(frozen=True, slots=True)
class GoldenSetHistoryRow:
    """One apply event for admin history (chronological ordering in API)."""

    id: uuid.UUID
    created_at: datetime
    actor_user_id: uuid.UUID | None
    actor_email: str | None
    prior_version: int
    new_version: int
    prior_refresh_cadence: str
    new_refresh_cadence: str
    prior_refresh_after_major: bool
    new_refresh_after_major: bool
    reason: str | None


async def fetch_singleton(
    session: AsyncSession,
    *,
    with_for_update: bool = False,
) -> GoldenSetPolicy | None:
    stmt = select(GoldenSetPolicy).where(GoldenSetPolicy.id == GOLDEN_SET_SINGLETON_ID)
    if with_for_update:
        stmt = stmt.with_for_update()
    res = await session.scalars(stmt)
    return res.one_or_none()


async def get_state_for_admin(
    session: AsyncSession,
) -> tuple[ActiveGoldenSetPolicy, DraftGoldenSetPolicy | None]:
    row = await fetch_singleton(session)
    if row is None:
        raise LookupError("golden_set_policy row missing; run migrations")
    active = ActiveGoldenSetPolicy(
        version=row.version,
        label_policy_text=row.label_policy_text,
        refresh_cadence=row.refresh_cadence,
        refresh_after_major_classification_change=row.refresh_after_major_classification_change,
    )
    if row.draft_label_policy_text is None:
        return active, None
    assert row.draft_refresh_cadence is not None
    assert row.draft_refresh_after_major is not None
    draft = DraftGoldenSetPolicy(
        label_policy_text=row.draft_label_policy_text,
        refresh_cadence=row.draft_refresh_cadence,
        refresh_after_major_classification_change=row.draft_refresh_after_major,
        reason=row.draft_reason,
    )
    return active, draft


async def save_draft(
    session: AsyncSession,
    *,
    label_policy_text: str | None,
    refresh_cadence: str | None,
    refresh_after_major_classification_change: bool | None,
    reason: str | None,
) -> None:
    row = await fetch_singleton(session, with_for_update=True)
    if row is None:
        raise LookupError("golden_set_policy row missing; run migrations")

    base_label = row.draft_label_policy_text or row.label_policy_text
    base_cadence = row.draft_refresh_cadence or row.refresh_cadence
    base_post = (
        row.draft_refresh_after_major
        if row.draft_refresh_after_major is not None
        else row.refresh_after_major_classification_change
    )

    merged_label = _validate_label_policy(
        base_label if label_policy_text is None else label_policy_text
    )
    merged_cadence = _validate_cadence(
        base_cadence if refresh_cadence is None else refresh_cadence
    )
    merged_post = (
        base_post
        if refresh_after_major_classification_change is None
        else refresh_after_major_classification_change
    )
    if not isinstance(merged_post, bool):
        raise ValueError("refresh_after_major_classification_change must be boolean")

    row.draft_label_policy_text = merged_label
    row.draft_refresh_cadence = merged_cadence
    row.draft_refresh_after_major = merged_post
    row.draft_reason = reason
    await session.flush()


async def apply_draft(
    session: AsyncSession,
    *,
    actor_user_id: uuid.UUID,
    audit_reason_override: str | None = None,
) -> ActiveGoldenSetPolicy:
    row = await fetch_singleton(session, with_for_update=True)
    if row is None:
        raise LookupError("golden_set_policy row missing; run migrations")
    if row.draft_label_policy_text is None:
        raise ValueError("no_draft_to_apply")

    assert row.draft_refresh_cadence is not None
    assert row.draft_refresh_after_major is not None

    prior_v = row.version
    prior_label = row.label_policy_text
    prior_cadence = row.refresh_cadence
    prior_post = row.refresh_after_major_classification_change

    new_label = _validate_label_policy(row.draft_label_policy_text)
    new_cadence = _validate_cadence(row.draft_refresh_cadence)
    new_post = row.draft_refresh_after_major
    if not isinstance(new_post, bool):
        raise ValueError("invalid draft_refresh_after_major")

    draft_reason = row.draft_reason
    reason_for_audit = (
        audit_reason_override if audit_reason_override is not None else draft_reason
    )

    row.label_policy_text = new_label
    row.refresh_cadence = new_cadence
    row.refresh_after_major_classification_change = new_post
    row.version = row.version + 1
    row.draft_label_policy_text = None
    row.draft_refresh_cadence = None
    row.draft_refresh_after_major = None
    row.draft_reason = None
    await session.flush()

    await audit_repo.append_golden_set_config_audit(
        session,
        actor_user_id=actor_user_id,
        prior_version=prior_v,
        new_version=row.version,
        prior_refresh_cadence=prior_cadence,
        new_refresh_cadence=new_cadence,
        prior_refresh_after_major=prior_post,
        new_refresh_after_major=new_post,
        prior_label_sha256_prefix=prompt_sha256_prefix(prior_label),
        new_label_sha256_prefix=prompt_sha256_prefix(new_label),
        prior_label_length=len(prior_label),
        new_label_length=len(new_label),
        reason=reason_for_audit,
    )
    await session.flush()

    return ActiveGoldenSetPolicy(
        version=row.version,
        label_policy_text=row.label_policy_text,
        refresh_cadence=row.refresh_cadence,
        refresh_after_major_classification_change=row.refresh_after_major_classification_change,
    )


async def list_apply_history(
    session: AsyncSession,
    *,
    limit: int = 100,
) -> list[GoldenSetHistoryRow]:
    """Return golden-set **apply** audit rows oldest-first (NFR13)."""

    lim = max(1, min(limit, 500))
    stmt = (
        select(AuditEvent, User.email)
        .outerjoin(User, AuditEvent.actor_user_id == User.id)
        .where(
            AuditEvent.run_id == GOLDEN_SET_CONFIG_AUDIT_RUN_ID,
            AuditEvent.action == PipelineAuditAction.GOLDEN_SET_CONFIG_CHANGED,
        )
        .order_by(AuditEvent.created_at.desc())
        .limit(lim)
    )
    res = await session.execute(stmt)
    out: list[GoldenSetHistoryRow] = []
    for ev, email in reversed(res.all()):
        meta = ev.event_metadata or {}
        reason = meta.get("reason")
        if reason is not None and not isinstance(reason, str):
            reason = str(reason)
        pv = meta.get("prior_version")
        nv = meta.get("new_version")
        if not isinstance(pv, int) or not isinstance(nv, int):
            continue
        prc = meta.get("prior_refresh_cadence")
        nrc = meta.get("new_refresh_cadence")
        prm = meta.get("prior_refresh_after_major_classification_change")
        nrm = meta.get("new_refresh_after_major_classification_change")
        if not isinstance(prc, str) or not isinstance(nrc, str):
            continue
        if not isinstance(prm, bool) or not isinstance(nrm, bool):
            continue
        out.append(
            GoldenSetHistoryRow(
                id=ev.id,
                created_at=ev.created_at,
                actor_user_id=ev.actor_user_id,
                actor_email=email,
                prior_version=pv,
                new_version=nv,
                prior_refresh_cadence=prc,
                new_refresh_cadence=nrc,
                prior_refresh_after_major=prm,
                new_refresh_after_major=nrm,
                reason=reason,
            )
        )
    return out
