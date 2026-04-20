"""Dashboard aggregates (Story 6.1 — FR30)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sentinel_prism.db.models import (
    AuditEvent,
    NormalizedUpdateRow,
    PipelineAuditAction,
    ReviewQueueItem,
    Source,
)

_SEVERITY_BUCKETS = frozenset({"critical", "high", "medium", "low", "none", "other"})


@dataclass(frozen=True)
class TopSourceRow:
    source_id: UUID
    name: str
    items_ingested_total: int


@dataclass(frozen=True)
class DashboardSummary:
    severity_counts: dict[str, int]
    new_items_count: int
    new_items_window_hours: int
    review_queue_count: int
    top_sources: list[TopSourceRow]
    top_sources_metric: str


def default_new_items_window_hours() -> int:
    raw = os.environ.get("DASHBOARD_NEW_ITEMS_HOURS", "24").strip()
    try:
        h = int(raw)
    except ValueError:
        h = 24
    return max(1, min(h, 24 * 90))


def _merge_severity_histograms(metadatas: list[Any]) -> dict[str, int]:
    def _coerce_meta(raw: Any) -> dict[str, Any] | None:
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError):
                return None
            return parsed if isinstance(parsed, dict) else None
        return None

    total: dict[str, int] = {}
    for raw_meta in metadatas:
        meta = _coerce_meta(raw_meta)
        if not meta:
            continue
        hist = meta.get("severity_histogram")
        if not isinstance(hist, dict):
            continue
        for k, v in hist.items():
            key = (k if isinstance(k, str) else str(k)).strip().lower()
            if key not in _SEVERITY_BUCKETS:
                key = "other"
            try:
                iv = int(v)
            except (TypeError, ValueError):
                continue
            if iv < 0:
                continue
            total[key] = total.get(key, 0) + iv
    return dict(sorted(total.items()))


async def fetch_dashboard_summary(
    session: AsyncSession,
    *,
    new_items_window_hours: int | None = None,
    top_sources_limit: int = 5,
) -> DashboardSummary:
    """Load overview metrics in a small number of round-trips.

    **Severity:** For each ``run_id``, only the latest ``PIPELINE_CLASSIFY_COMPLETED``
    row (by ``created_at``, then ``id``) contributes its ``severity_histogram``,
    so LangGraph retries do not double-count items (Story 6.1 Option A).
    """

    hours = default_new_items_window_hours() if new_items_window_hours is None else new_items_window_hours
    hours = max(1, min(int(hours), 24 * 90))
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    rn = (
        func.row_number()
        .over(
            partition_by=AuditEvent.run_id,
            order_by=(AuditEvent.created_at.desc(), AuditEvent.id.desc()),
        )
        .label("rn")
    )
    classified = (
        select(AuditEvent.event_metadata, rn)
        .where(AuditEvent.action == PipelineAuditAction.PIPELINE_CLASSIFY_COMPLETED)
    ).subquery()

    hist_stmt = select(classified.c.event_metadata).where(classified.c.rn == 1)
    hist_res = await session.execute(hist_stmt)
    severity_counts = _merge_severity_histograms([row[0] for row in hist_res.all()])

    new_items_stmt = select(func.count()).select_from(NormalizedUpdateRow).where(
        NormalizedUpdateRow.created_at >= since
    )
    new_items_count = int(await session.scalar(new_items_stmt) or 0)

    rq_stmt = select(func.count()).select_from(ReviewQueueItem)
    review_queue_count = int(await session.scalar(rq_stmt) or 0)

    lim = max(1, min(int(top_sources_limit), 50))
    top_stmt = (
        select(Source.id, Source.name, Source.items_ingested_total)
        .order_by(Source.items_ingested_total.desc(), Source.id.asc())
        .limit(lim)
    )
    top_res = await session.execute(top_stmt)
    top_sources = [
        TopSourceRow(source_id=r[0], name=r[1], items_ingested_total=int(r[2]))
        for r in top_res.all()
    ]

    return DashboardSummary(
        severity_counts=severity_counts,
        new_items_count=new_items_count,
        new_items_window_hours=hours,
        review_queue_count=review_queue_count,
        top_sources=top_sources,
        top_sources_metric="items_ingested_total",
    )
