"""Classification policy singleton — active + draft (Story 7.3 — FR29)."""

from __future__ import annotations

import hashlib
import math
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sentinel_prism.db.models import ClassificationPolicy
from sentinel_prism.db.repositories import audit_events as audit_repo

CLASSIFICATION_POLICY_SINGLETON_ID = 1

MAX_SYSTEM_PROMPT_LENGTH = 32768


def _validate_threshold(value: float) -> float:
    if not math.isfinite(value) or value < 0.0 or value > 1.0:
        raise ValueError("low_confidence_threshold must be between 0 and 1")
    return value


def _validate_system_prompt(value: str) -> str:
    if not value.strip():
        raise ValueError("system_prompt must not be blank")
    if len(value) > MAX_SYSTEM_PROMPT_LENGTH:
        raise ValueError(
            f"system_prompt exceeds {MAX_SYSTEM_PROMPT_LENGTH} characters"
        )
    return value


def prompt_sha256_prefix(text: str, *, length: int = 16) -> str:
    """First ``length`` hex chars of SHA-256 (audit metadata only)."""

    h = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return h[:length]


@dataclass(frozen=True, slots=True)
class ActiveClassificationPolicy:
    version: int
    low_confidence_threshold: float
    system_prompt: str


@dataclass(frozen=True, slots=True)
class DraftClassificationPolicy:
    low_confidence_threshold: float
    system_prompt: str
    reason: str | None


async def fetch_singleton(
    session: AsyncSession,
    *,
    with_for_update: bool = False,
) -> ClassificationPolicy | None:
    stmt = select(ClassificationPolicy).where(
        ClassificationPolicy.id == CLASSIFICATION_POLICY_SINGLETON_ID
    )
    if with_for_update:
        stmt = stmt.with_for_update()
    res = await session.scalars(stmt)
    return res.one_or_none()


async def get_active_runtime(
    session: AsyncSession,
) -> ActiveClassificationPolicy | None:
    """Return active policy for classify node, or ``None`` if row missing."""

    row = await fetch_singleton(session)
    if row is None:
        return None
    return ActiveClassificationPolicy(
        version=row.version,
        low_confidence_threshold=row.low_confidence_threshold,
        system_prompt=row.system_prompt,
    )


async def get_state_for_admin(
    session: AsyncSession,
) -> tuple[ActiveClassificationPolicy, DraftClassificationPolicy | None]:
    row = await fetch_singleton(session)
    if row is None:
        raise LookupError("classification_policy row missing; run migrations")
    active = ActiveClassificationPolicy(
        version=row.version,
        low_confidence_threshold=row.low_confidence_threshold,
        system_prompt=row.system_prompt,
    )
    if (
        row.draft_low_confidence_threshold is None
        or row.draft_system_prompt is None
    ):
        return active, None
    draft = DraftClassificationPolicy(
        low_confidence_threshold=row.draft_low_confidence_threshold,
        system_prompt=row.draft_system_prompt,
        reason=row.draft_reason,
    )
    return active, draft


async def save_draft(
    session: AsyncSession,
    *,
    low_confidence_threshold: float | None,
    system_prompt: str | None,
    reason: str | None,
) -> None:
    row = await fetch_singleton(session, with_for_update=True)
    if row is None:
        raise LookupError("classification_policy row missing; run migrations")
    row.draft_low_confidence_threshold = _validate_threshold(
        row.low_confidence_threshold
        if low_confidence_threshold is None
        else low_confidence_threshold
    )
    row.draft_system_prompt = _validate_system_prompt(
        row.system_prompt if system_prompt is None else system_prompt
    )
    row.draft_reason = reason
    await session.flush()


async def apply_draft(
    session: AsyncSession,
    *,
    actor_user_id: uuid.UUID,
    audit_reason_override: str | None = None,
) -> ActiveClassificationPolicy:
    row = await fetch_singleton(session, with_for_update=True)
    if row is None:
        raise LookupError("classification_policy row missing; run migrations")
    if row.draft_low_confidence_threshold is None or row.draft_system_prompt is None:
        raise ValueError("no_draft_to_apply")

    prior_v = row.version
    prior_th = row.low_confidence_threshold
    prior_prompt = row.system_prompt
    new_th = row.draft_low_confidence_threshold
    new_prompt = row.draft_system_prompt
    draft_reason = row.draft_reason
    reason_for_audit = (
        audit_reason_override if audit_reason_override is not None else draft_reason
    )

    assert new_th is not None
    assert new_prompt is not None
    new_th = _validate_threshold(new_th)
    new_prompt = _validate_system_prompt(new_prompt)

    row.low_confidence_threshold = new_th
    row.system_prompt = new_prompt
    row.version = row.version + 1
    row.draft_low_confidence_threshold = None
    row.draft_system_prompt = None
    row.draft_reason = None
    await session.flush()

    await audit_repo.append_classification_config_audit(
        session,
        actor_user_id=actor_user_id,
        prior_version=prior_v,
        new_version=row.version,
        prior_threshold=prior_th,
        new_threshold=new_th,
        prior_prompt_sha256_prefix=prompt_sha256_prefix(prior_prompt),
        new_prompt_sha256_prefix=prompt_sha256_prefix(new_prompt),
        prior_prompt_length=len(prior_prompt),
        new_prompt_length=len(new_prompt),
        reason=reason_for_audit,
    )
    await session.flush()

    return ActiveClassificationPolicy(
        version=row.version,
        low_confidence_threshold=row.low_confidence_threshold,
        system_prompt=row.system_prompt,
    )
