"""Aggregated feedback and human-review metrics for admin (Story 7.2 — FR28)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sentinel_prism.db.models import (
    AuditEvent,
    PipelineAuditAction,
    UpdateFeedback,
    UpdateFeedbackKind,
)

_REVIEW_ACTIONS: tuple[PipelineAuditAction, ...] = (
    PipelineAuditAction.HUMAN_REVIEW_APPROVED,
    PipelineAuditAction.HUMAN_REVIEW_REJECTED,
    PipelineAuditAction.HUMAN_REVIEW_OVERRIDDEN,
)


@dataclass(frozen=True)
class FeedbackMetricsSnapshot:
    """Raw counts for JSON/CSV; API layer may derive percentages and override rate."""

    kind_counts: dict[str, int]
    human_review_approved: int
    human_review_rejected: int
    human_review_overridden: int
    since: datetime | None
    until: datetime | None


def _default_kind_counts() -> dict[str, int]:
    return {m.value: 0 for m in UpdateFeedbackKind}


async def fetch_feedback_metrics(
    session: AsyncSession,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> FeedbackMetricsSnapshot:
    """Aggregate user feedback by kind and human review outcomes from audit in one window."""

    kc = _default_kind_counts()
    stmt_fb = select(UpdateFeedback.kind, func.count()).group_by(UpdateFeedback.kind)
    if since is not None:
        stmt_fb = stmt_fb.where(UpdateFeedback.created_at >= since)
    if until is not None:
        stmt_fb = stmt_fb.where(UpdateFeedback.created_at <= until)
    res_fb = await session.execute(stmt_fb)
    for kind, n in res_fb.all():
        key = kind.value if isinstance(kind, UpdateFeedbackKind) else str(kind)
        if key in kc:
            kc[key] = int(n)

    # Human review: count each action separately
    counts = {a.value: 0 for a in _REVIEW_ACTIONS}
    stmt_a = select(AuditEvent.action, func.count()).where(
        AuditEvent.action.in_(_REVIEW_ACTIONS)
    )
    if since is not None:
        stmt_a = stmt_a.where(AuditEvent.created_at >= since)
    if until is not None:
        stmt_a = stmt_a.where(AuditEvent.created_at <= until)
    stmt_a = stmt_a.group_by(AuditEvent.action)
    res_a = await session.execute(stmt_a)
    for act, n in res_a.all():
        av = act.value if isinstance(act, PipelineAuditAction) else str(act)
        if av in counts:
            counts[av] = int(n)

    return FeedbackMetricsSnapshot(
        kind_counts=kc,
        human_review_approved=counts[PipelineAuditAction.HUMAN_REVIEW_APPROVED.value],
        human_review_rejected=counts[PipelineAuditAction.HUMAN_REVIEW_REJECTED.value],
        human_review_overridden=counts[PipelineAuditAction.HUMAN_REVIEW_OVERRIDDEN.value],
        since=since,
        until=until,
    )
