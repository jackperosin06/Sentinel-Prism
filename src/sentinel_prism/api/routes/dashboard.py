"""Overview dashboard API (Story 6.1 — FR30, FR40)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from sentinel_prism.api.deps import get_current_user
from sentinel_prism.db.models import User
from sentinel_prism.db.repositories import dashboard as dashboard_repo
from sentinel_prism.db.session import get_db
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


class TopSourceOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: uuid.UUID
    name: str
    metric: str = Field(
        default="items_ingested_total",
        description="Metric used for ranking (see ``top_sources_metric`` on the parent).",
    )
    value: int = Field(description="Value of ``metric`` for this source.")


class DashboardSummaryOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity_counts: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Aggregated severity histogram from latest classify audit per run "
            "(``severity_histogram`` in ``PIPELINE_CLASSIFY_COMPLETED`` metadata)."
        ),
    )
    new_items_count: int
    new_items_window_hours: int = Field(
        description="Counts ``normalized_updates`` with ``created_at`` within this many hours (UTC)."
    )
    review_queue_count: int
    top_sources: list[TopSourceOut]
    top_sources_metric: str = "items_ingested_total"


@router.get("/summary", response_model=DashboardSummaryOut)
async def get_dashboard_summary(
    new_items_window_hours: int | None = Query(
        default=None,
        ge=1,
        le=24 * 90,
        description=(
            "Override hours for the new-items widget. When omitted, uses "
            "``DASHBOARD_NEW_ITEMS_HOURS`` or defaults to 24."
        ),
    ),
    top_sources_limit: int = Query(default=5, ge=1, le=50),
    _current: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DashboardSummaryOut:
    """Authenticated console users (viewer and above) — same as notifications list."""

    summary = await dashboard_repo.fetch_dashboard_summary(
        db,
        new_items_window_hours=new_items_window_hours,
        top_sources_limit=top_sources_limit,
    )
    return DashboardSummaryOut(
        severity_counts=summary.severity_counts,
        new_items_count=summary.new_items_count,
        new_items_window_hours=summary.new_items_window_hours,
        review_queue_count=summary.review_queue_count,
        top_sources=[
            TopSourceOut(
                source_id=row.source_id,
                name=row.name,
                metric=summary.top_sources_metric,
                value=row.items_ingested_total,
            )
            for row in summary.top_sources
        ],
        top_sources_metric=summary.top_sources_metric,
    )
