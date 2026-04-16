"""Regulatory source registry (Admin — Story 2.1)."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from sentinel_prism.api.deps import (
    PollExecutor,
    get_db_for_admin,
    get_poll_executor,
)
from sentinel_prism.db.models import SourceType
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


_URL_RE = re.compile(r"^https?://\S+", re.IGNORECASE)


def _validate_url(v: str) -> str:
    if not _URL_RE.match(v):
        raise ValueError("primary_url must be a valid HTTP or HTTPS URL")
    return v


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
    enabled: bool = True
    extra_metadata: dict | None = None

    @field_validator("primary_url")
    @classmethod
    def primary_url_must_be_http(cls, v: str) -> str:
        return _validate_url(v)

    @field_validator("schedule")
    @classmethod
    def schedule_must_be_valid_cron(cls, v: str) -> str:
        return validate_cron_expression(v)


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
    enabled: bool | None = None
    extra_metadata: dict | None = None

    @field_validator("primary_url")
    @classmethod
    def primary_url_must_be_http(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return _validate_url(v)

    @field_validator("schedule")
    @classmethod
    def schedule_must_be_valid_cron(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return validate_cron_expression(v)


class SourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    jurisdiction: str
    source_type: SourceType
    primary_url: str
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
        enabled=body.enabled,
        extra_metadata=body.extra_metadata,
    )
    await db.commit()
    await get_poll_scheduler().refresh_jobs_for_source(db, row.id)
    return SourceResponse.model_validate(row)


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
