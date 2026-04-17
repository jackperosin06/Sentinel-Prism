"""Regulatory source registry (Admin — Story 2.1)."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from sentinel_prism.api.deps import (
    PollExecutor,
    get_db_for_admin,
    get_poll_executor,
)
from sentinel_prism.db.models import FallbackMode, Source, SourceType
from sentinel_prism.db.repositories import sources as sources_repo
from sentinel_prism.services.sources.schedule import (
    SCHEDULE_FIELD_DESCRIPTION,
    validate_cron_expression,
)
from sentinel_prism.workers.poll_scheduler import get_poll_scheduler

router = APIRouter(
    prefix="/sources",
    tags=["sources"],
)


_URL_RE = re.compile(r"^https?://[^\s\x00-\x1f\x7f]+\Z", re.IGNORECASE)


def _validate_url(v: str, *, field: str = "URL") -> str:
    # Reject leading/trailing whitespace and embedded control chars (CR/LF/TAB/NUL)
    # before the regex check so malformed values cannot reach httpx.URL() at poll time.
    if v != v.strip() or not _URL_RE.match(v):
        raise ValueError(f"{field} must be a valid HTTP or HTTPS URL")
    return v


def _validate_fallback_pair(mode: FallbackMode, url: str | None) -> None:
    """``fallback_mode`` and ``fallback_url`` must agree (Story 2.5)."""

    if mode == FallbackMode.NONE:
        if url is not None and str(url).strip():
            raise ValueError("fallback_url must be omitted when fallback_mode is none")
    elif not url or not str(url).strip():
        raise ValueError("fallback_url is required when fallback_mode is not none")


class SourceCreate(BaseModel):
    """Create body — ``schedule`` is a five-field cron (UTC)."""

    name: str = Field(..., min_length=1, max_length=512)
    jurisdiction: str = Field(..., min_length=1, max_length=256)
    source_type: SourceType
    primary_url: str = Field(
        ..., min_length=1, description="Primary feed or page URL (HTTP/HTTPS)."
    )
    schedule: str = Field(
        ...,
        min_length=1,
        max_length=512,
        description=SCHEDULE_FIELD_DESCRIPTION,
    )
    fallback_url: str | None = Field(
        None,
        description="Alternate URL when primary fails (requires non-none fallback_mode).",
    )
    fallback_mode: FallbackMode = FallbackMode.NONE
    enabled: bool = True
    extra_metadata: dict | None = None

    @field_validator("primary_url")
    @classmethod
    def primary_url_must_be_http(cls, v: str) -> str:
        return _validate_url(v, field="primary_url")

    @field_validator("fallback_url")
    @classmethod
    def fallback_url_must_be_http(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return _validate_url(v, field="fallback_url")

    @field_validator("schedule")
    @classmethod
    def schedule_must_be_valid_cron(cls, v: str) -> str:
        return validate_cron_expression(v)

    @model_validator(mode="after")
    def _fallback_consistency(self) -> SourceCreate:
        _validate_fallback_pair(self.fallback_mode, self.fallback_url)
        return self


class SourceUpdate(BaseModel):
    """Partial update — only sent fields are applied."""

    name: str | None = Field(None, min_length=1, max_length=512)
    jurisdiction: str | None = Field(None, min_length=1, max_length=256)
    source_type: SourceType | None = None
    primary_url: str | None = Field(
        None, min_length=1, description="Primary feed or page URL (HTTP/HTTPS)."
    )
    schedule: str | None = Field(
        None,
        min_length=1,
        max_length=512,
        description=SCHEDULE_FIELD_DESCRIPTION,
    )
    fallback_url: str | None = Field(
        None,
        description="Alternate URL when primary fails (requires non-none fallback_mode).",
    )
    fallback_mode: FallbackMode | None = None
    enabled: bool | None = None
    extra_metadata: dict | None = None

    @field_validator("primary_url")
    @classmethod
    def primary_url_must_be_http(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return _validate_url(v, field="primary_url")

    @field_validator("fallback_url")
    @classmethod
    def fallback_url_must_be_http(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return _validate_url(v, field="fallback_url")

    @field_validator("schedule")
    @classmethod
    def schedule_must_be_valid_cron(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return validate_cron_expression(v)


class LastPollFailurePayload(BaseModel):
    """Subset of ``Source.extra_metadata['last_poll_failure']`` (Stories 2.4–2.6)."""

    model_config = ConfigDict(extra="forbid")

    at: datetime
    reason: str
    error_class: str


class SourceMetricsResponse(BaseModel):
    """Per-source ingestion health (Story 2.6 — NFR9)."""

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
    last_success_fetch_path: Literal["primary", "fallback"] | None = Field(
        default=None,
        description="``primary`` or ``fallback`` for the last successful poll.",
    )
    last_poll_failure: LastPollFailurePayload | None = None


def _parse_last_poll_failure(raw: object) -> LastPollFailurePayload | None:
    """Best-effort parse of legacy / current ``last_poll_failure`` metadata; drop on failure."""

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
        try:
            at_dt = datetime.fromisoformat(raw_at)
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
    raw_meta = row.extra_metadata or {}
    lp = _parse_last_poll_failure(raw_meta.get("last_poll_failure"))
    # Coerce the DB column (free-form ``String(16)``) to the Literal the response schema
    # advertises; unknown values become ``None`` rather than failing serialization.
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


class SourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    jurisdiction: str
    source_type: SourceType
    primary_url: str
    fallback_url: str | None
    fallback_mode: FallbackMode
    schedule: str
    enabled: bool
    extra_metadata: dict | None
    created_at: datetime
    updated_at: datetime


_Db = Annotated[AsyncSession, Depends(get_db_for_admin)]


@router.get(
    "",
    response_model=list[SourceResponse],
    summary="List sources",
    description="Returns all sources ordered by **created_at** ascending (oldest first).",
)
async def list_sources(
    db: _Db,
) -> list[SourceResponse]:
    rows = await sources_repo.list_sources(db)
    return [SourceResponse.model_validate(r) for r in rows]


@router.post(
    "",
    response_model=SourceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_source(
    db: _Db,
    body: SourceCreate,
) -> SourceResponse:
    row = await sources_repo.create_source(
        db,
        name=body.name,
        jurisdiction=body.jurisdiction,
        source_type=body.source_type,
        primary_url=body.primary_url,
        schedule=body.schedule,
        fallback_url=body.fallback_url,
        fallback_mode=body.fallback_mode,
        enabled=body.enabled,
        extra_metadata=body.extra_metadata,
    )
    await db.commit()
    await get_poll_scheduler().refresh_jobs_for_source(db, row.id)
    return SourceResponse.model_validate(row)


@router.get(
    "/metrics",
    response_model=list[SourceMetricsResponse],
    summary="List ingestion metrics for all sources",
    description=(
        "Returns **NFR9** counters and derived rates per source, plus "
        "``last_poll_failure`` from metadata when present. Paginated via "
        "``limit`` (default 100, max 500) and ``offset``; stable ordering by "
        "``created_at`` ascending (Story 2.6)."
    ),
)
async def list_source_metrics(
    db: _Db,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[SourceMetricsResponse]:
    rows = await sources_repo.list_sources(db, limit=limit, offset=offset)
    return [_metrics_from_source(r) for r in rows]


@router.post(
    "/{source_id}/poll",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger a manual poll",
    description=(
        "Runs the same connector poll entrypoint as the in-process scheduler "
        "(Story 2.2). Disabled sources return **409**."
    ),
)
async def trigger_manual_poll(
    db: _Db,
    source_id: uuid.UUID,
    execute_poll: Annotated[PollExecutor, Depends(get_poll_executor)],
) -> dict[str, str]:
    row = await sources_repo.get_source_by_id(db, source_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Source not found")
    if not row.enabled:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Source is disabled",
        )
    await execute_poll(source_id, trigger="manual")
    return {"status": "accepted", "source_id": str(source_id)}


@router.get(
    "/{source_id}/metrics",
    response_model=SourceMetricsResponse,
    summary="Ingestion metrics for one source",
)
async def get_source_metrics(
    db: _Db,
    source_id: uuid.UUID,
) -> SourceMetricsResponse:
    row = await sources_repo.get_source_by_id(db, source_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Source not found")
    return _metrics_from_source(row)


@router.get(
    "/{source_id}",
    response_model=SourceResponse,
)
async def get_source(
    db: _Db,
    source_id: uuid.UUID,
) -> SourceResponse:
    row = await sources_repo.get_source_by_id(db, source_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Source not found")
    return SourceResponse.model_validate(row)


@router.patch(
    "/{source_id}",
    response_model=SourceResponse,
)
async def patch_source(
    db: _Db,
    source_id: uuid.UUID,
    body: SourceUpdate,
) -> SourceResponse:
    data = body.model_dump(exclude_unset=True)
    if not data:
        row = await sources_repo.get_source_by_id(db, source_id)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Source not found")
        return SourceResponse.model_validate(row)

    # Explicit null for fallback_mode would violate the NOT NULL column at commit time;
    # reject at the API edge with a clear 422 instead of returning a 500 from the DB.
    if "fallback_mode" in data and data["fallback_mode"] is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="fallback_mode cannot be null; use \"none\" to clear",
        )

    if "fallback_mode" in data or "fallback_url" in data:
        existing = await sources_repo.get_source_by_id(db, source_id)
        if existing is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Source not found")

        # Strict parity with SourceCreate: if the client explicitly sets mode=none
        # alongside a non-null fallback_url, reject; do not silently rewrite the URL.
        if (
            "fallback_mode" in data
            and data["fallback_mode"] == FallbackMode.NONE
            and "fallback_url" in data
            and data["fallback_url"] is not None
            and str(data["fallback_url"]).strip()
        ):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="fallback_url must be omitted when fallback_mode is none",
            )

        # Setting mode=none without sending fallback_url clears the stored URL.
        if (
            "fallback_mode" in data
            and data["fallback_mode"] == FallbackMode.NONE
            and "fallback_url" not in data
        ):
            data["fallback_url"] = None

        mode = (
            data["fallback_mode"]
            if "fallback_mode" in data
            else existing.fallback_mode
        )
        url = (
            data["fallback_url"] if "fallback_url" in data else existing.fallback_url
        )

        # Nudge the caller when they try to null fallback_url without also clearing mode.
        if (
            "fallback_url" in data
            and data["fallback_url"] is None
            and "fallback_mode" not in data
            and existing.fallback_mode != FallbackMode.NONE
        ):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="to clear fallback_url, also set fallback_mode to none",
            )

        try:
            _validate_fallback_pair(mode, url)
        except ValueError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc

    row = await sources_repo.update_source_fields(db, source_id, data)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Source not found")
    await db.commit()
    await get_poll_scheduler().refresh_jobs_for_source(db, source_id)
    return SourceResponse.model_validate(row)


@router.delete(
    "/{source_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a source",
    response_class=Response,
)
async def delete_source(
    db: _Db,
    source_id: uuid.UUID,
) -> Response:
    deleted = await sources_repo.delete_source(db, source_id)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Source not found")
    await db.commit()
    await get_poll_scheduler().refresh_jobs_for_source(db, source_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
