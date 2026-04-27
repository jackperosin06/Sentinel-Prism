"""Operator observability endpoints (Story 8.3 — NFR8/NFR9)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from sentinel_prism.api.deps import require_roles
from sentinel_prism.db.models import Source, UserRole
from sentinel_prism.db.repositories import sources as sources_repo
from sentinel_prism.db.session import get_db

router = APIRouter(prefix="/ops", tags=["ops"])


class LastPollFailurePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    at: datetime
    reason: str
    error_class: str


class SourceMetricsResponse(BaseModel):
    """Per-source ingestion health (NFR9) exposed to operators."""

    model_config = ConfigDict(extra="forbid")

    source_id: uuid.UUID
    name: str
    poll_attempts_success: int
    poll_attempts_failed: int
    items_ingested_total: int
    success_rate: float | None = Field(
        default=None,
        description="``poll_attempts_success / (success + failed)``; null when no attempts yet.",
    )
    error_rate: float | None = Field(
        default=None,
        description="``poll_attempts_failed / (success + failed)``; null when no attempts yet.",
    )
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    last_success_latency_ms: int | None = None
    last_success_fetch_path: Literal["primary", "fallback"] | None = None
    last_poll_failure: LastPollFailurePayload | None = None


def _parse_last_poll_failure(raw: object) -> LastPollFailurePayload | None:
    if not (
        isinstance(raw, dict)
        and "at" in raw
        and "reason" in raw
        and "error_class" in raw
    ):
        return None
    raw_at = raw["at"]
    at_dt: datetime | None
    if isinstance(raw_at, datetime):
        at_dt = raw_at
    elif isinstance(raw_at, str):
        s = raw_at.strip()
        if s.endswith("Z"):
            s = f"{s[:-1]}+00:00"
        try:
            at_dt = datetime.fromisoformat(s)
        except ValueError:
            return None
    else:
        return None
    return LastPollFailurePayload(
        at=at_dt,
        reason=str(raw["reason"])[:4000],
        error_class=str(raw["error_class"])[:255],
    )


def _metrics_from_source(row: Source) -> SourceMetricsResponse:
    s = int(row.poll_attempts_success)
    f = int(row.poll_attempts_failed)
    total = s + f
    raw_meta_raw = row.extra_metadata
    raw_meta: dict[str, object] = raw_meta_raw if isinstance(raw_meta_raw, dict) else {}
    lp = _parse_last_poll_failure(raw_meta.get("last_poll_failure"))
    fetch_path: Literal["primary", "fallback"] | None
    if row.last_success_fetch_path in ("primary", "fallback"):
        fetch_path = row.last_success_fetch_path  # type: ignore[assignment]
    else:
        fetch_path = None
    return SourceMetricsResponse(
        source_id=row.id,
        name=row.name,
        poll_attempts_success=s,
        poll_attempts_failed=f,
        items_ingested_total=int(row.items_ingested_total),
        success_rate=(s / total) if total else None,
        error_rate=(f / total) if total else None,
        last_success_at=row.last_success_at,
        last_failure_at=row.last_failure_at,
        last_success_latency_ms=row.last_success_latency_ms,
        last_success_fetch_path=fetch_path,
        last_poll_failure=lp,
    )


_Db = Annotated[AsyncSession, Depends(get_db)]
_Role = Depends(require_roles(UserRole.ANALYST, UserRole.ADMIN))


@router.get(
    "/source-metrics",
    response_model=list[SourceMetricsResponse],
    summary="Per-source ingestion metrics (operator)",
    description=(
        "Returns **NFR9** counters and derived rates per source. "
        "Paginated via ``limit`` (default 100, max 500) and ``offset``; "
        "stable ordering by ``created_at`` ascending."
    ),
    dependencies=[_Role],
)
async def list_ops_source_metrics(
    db: _Db,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[SourceMetricsResponse]:
    rows = await sources_repo.list_sources(db, limit=limit, offset=offset)
    return [_metrics_from_source(r) for r in rows]

