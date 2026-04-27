"""Admin feedback and review metrics (Story 7.2 — FR28)."""

from __future__ import annotations

import io
import csv
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from sentinel_prism.api.deps import get_db_for_admin
from sentinel_prism.db.repositories import feedback_metrics as feedback_metrics_repo
from sentinel_prism.db.repositories.feedback_metrics import FeedbackMetricsSnapshot

router = APIRouter(prefix="/admin/feedback-metrics", tags=["admin", "feedback"])


def _validate_window(
    since: datetime | None, until: datetime | None
) -> tuple[datetime | None, datetime | None]:
    for name, value in (("since", since), ("until", until)):
        if value is not None and (
            value.tzinfo is None or value.tzinfo.utcoffset(value) is None
        ):
            raise HTTPException(
                status_code=400,
                detail=f"{name} must include a timezone offset, e.g. +00:00 for UTC.",
            )
    if since is not None and until is not None and since > until:
        raise HTTPException(
            status_code=400,
            detail="since must be earlier than or equal to until.",
        )
    return since, until


class FeedbackMetricsOut(BaseModel):
    """Aggregates for the console; ``human_review_override_rate`` is ``null`` when there are no review decisions in the window."""

    model_config = ConfigDict(extra="forbid")

    since: datetime | None = Field(
        default=None, description="Inclusive lower bound on ``created_at`` (UTC)."
    )
    until: datetime | None = Field(
        default=None, description="Inclusive upper bound on ``created_at`` (UTC)."
    )
    kind_counts: dict[str, int] = Field(
        description="Count of user feedback rows per ``UpdateFeedbackKind``."
    )
    kind_percent: dict[str, float] = Field(
        description="Share of each kind vs total user feedback (0–100, two decimals; all zero when no feedback)."
    )
    total_feedback: int
    human_review_approved: int
    human_review_rejected: int
    human_review_overridden: int
    human_review_decisions_total: int
    human_review_override_rate: float | None = Field(
        default=None,
        description=(
            "``human_review_overridden / human_review_decisions_total``; "
            "``null`` when ``human_review_decisions_total == 0`` (no review decisions in window)."
        ),
    )


def _snapshot_to_out(snap: FeedbackMetricsSnapshot) -> FeedbackMetricsOut:
    total = sum(snap.kind_counts.values())
    kind_percent: dict[str, float] = {}
    for k, v in snap.kind_counts.items():
        if total > 0:
            kind_percent[k] = round(100.0 * float(v) / float(total), 2)
        else:
            kind_percent[k] = 0.0
    dtotal = (
        snap.human_review_approved
        + snap.human_review_rejected
        + snap.human_review_overridden
    )
    rate: float | None
    if dtotal == 0:
        rate = None
    else:
        rate = round(float(snap.human_review_overridden) / float(dtotal), 6)
    return FeedbackMetricsOut(
        since=snap.since,
        until=snap.until,
        kind_counts=snap.kind_counts,
        kind_percent=kind_percent,
        total_feedback=total,
        human_review_approved=snap.human_review_approved,
        human_review_rejected=snap.human_review_rejected,
        human_review_overridden=snap.human_review_overridden,
        human_review_decisions_total=dtotal,
        human_review_override_rate=rate,
    )


def _metrics_csv_bytes(out: FeedbackMetricsOut) -> str:
    buffer = io.StringIO()
    w = csv.writer(buffer)
    w.writerow(["section", "key", "value"])
    w.writerow(["window", "since", "" if out.since is None else out.since.isoformat()])
    w.writerow(["window", "until", "" if out.until is None else out.until.isoformat()])
    w.writerow(["summary", "total_feedback", out.total_feedback])
    for k in sorted(out.kind_counts.keys()):
        w.writerow(["feedback_kind", k, out.kind_counts[k]])
        w.writerow(["feedback_kind_percent", k, out.kind_percent.get(k, 0.0)])
    w.writerow(["human_review", "approved", out.human_review_approved])
    w.writerow(["human_review", "rejected", out.human_review_rejected])
    w.writerow(["human_review", "overridden", out.human_review_overridden])
    w.writerow(["human_review", "decisions_total", out.human_review_decisions_total])
    w.writerow(
        [
            "human_review",
            "override_rate",
            ""
            if out.human_review_override_rate is None
            else out.human_review_override_rate,
        ]
    )
    return buffer.getvalue()


@router.get(
    "",
    response_model=FeedbackMetricsOut,
    summary="Aggregated feedback and human-review metrics (admin only, JSON)",
)
async def get_feedback_metrics(
    since: datetime | None = Query(
        default=None, description="Filter ``created_at`` ≥ this instant (inclusive)."
    ),
    until: datetime | None = Query(
        default=None, description="Filter ``created_at`` ≤ this instant (inclusive)."
    ),
    db: AsyncSession = Depends(get_db_for_admin),
) -> FeedbackMetricsOut:
    since, until = _validate_window(since, until)
    snap = await feedback_metrics_repo.fetch_feedback_metrics(
        db, since=since, until=until
    )
    return _snapshot_to_out(snap)


@router.get(
    "/export",
    response_class=Response,
    summary="Same aggregates as ``GET /admin/feedback-metrics`` as CSV (admin only).",
)
async def export_feedback_metrics(
    since: datetime | None = Query(
        default=None, description="Filter ``created_at`` ≥ this instant (inclusive)."
    ),
    until: datetime | None = Query(
        default=None, description="Filter ``created_at`` ≤ this instant (inclusive)."
    ),
    db: AsyncSession = Depends(get_db_for_admin),
) -> Response:
    since, until = _validate_window(since, until)
    snap = await feedback_metrics_repo.fetch_feedback_metrics(
        db, since=since, until=until
    )
    out = _snapshot_to_out(snap)
    body = _metrics_csv_bytes(out)
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="feedback_metrics.csv"',
        },
    )
