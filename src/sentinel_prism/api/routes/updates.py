"""Update explorer API (Story 6.2 — FR9, FR31, FR40; Story 7.1 — FR26, FR27)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sentinel_prism.api.deps import require_roles
from sentinel_prism.db.models import (
    NormalizedUpdateRow,
    RawCapture,
    UpdateFeedbackKind,
    User,
    UserRole,
)
from sentinel_prism.db.repositories import feedback as feedback_repo
from sentinel_prism.db.repositories import updates as updates_repo
from sentinel_prism.db.repositories.updates import ExplorerSort
from sentinel_prism.db.session import get_db

router = APIRouter(prefix="/updates", tags=["updates"])

_MAX_OFFSET = 50_000

ExplorerStatus = Literal["in_human_review", "briefed", "processed"]


class UpdateListItemOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    raw_capture_id: uuid.UUID
    source_id: uuid.UUID
    source_name: str
    jurisdiction: str
    title: str | None = None
    published_at: datetime | None = None
    item_url: str
    document_type: str
    body_snippet: str | None = None
    run_id: uuid.UUID | None = None
    created_at: datetime
    explorer_status: ExplorerStatus
    derived_severity: str | None = Field(
        default=None,
        description=(
            "Severity when derivable from briefing member, in-app notification, "
            "or digest queue (see Story 6.2 Dev Notes). Null when unknown."
        ),
    )


class UpdateListOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[UpdateListItemOut]
    total: int
    limit: int
    offset: int
    sort: ExplorerSort
    default_sort: ExplorerSort = Field(
        default="created_at_desc",
        description="Default ordering when ``sort`` is omitted: newest ``created_at`` first.",
    )


class NormalizedPayloadOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    raw_capture_id: uuid.UUID
    source_id: uuid.UUID
    source_name: str
    jurisdiction: str
    title: str | None = None
    published_at: datetime | None = None
    item_url: str
    document_type: str
    body_snippet: str | None = None
    summary: str | None = None
    extra_metadata: dict[str, Any] | None = None
    parser_confidence: float | None = None
    extraction_quality: float | None = None
    run_id: uuid.UUID | None = None
    created_at: datetime


class ClassificationOverlayOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: str | None = None
    impact_categories: list[str] = Field(default_factory=list)
    confidence: float | None = None


class UpdateDetailOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    normalized: NormalizedPayloadOut
    raw_payload: Any = Field(
        description="``raw_captures.payload`` JSON (Scout-compatible).",
    )
    classification: ClassificationOverlayOut | None = Field(
        default=None,
        description="Populated when a matching briefing member exists.",
    )


_MAX_FEEDBACK_COMMENT = 10_000


class UpdateFeedbackCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: UpdateFeedbackKind
    comment: str = Field(
        ...,
        min_length=1,
        max_length=_MAX_FEEDBACK_COMMENT,
        description="User explanation (trimmed; empty-after-trim is rejected).",
    )

    @field_validator("comment")
    @classmethod
    def _strip_comment(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("comment must not be empty or whitespace only")
        return s


class UpdateFeedbackCreatedOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    created_at: datetime
    normalized_update_id: uuid.UUID
    run_id: uuid.UUID | None
    kind: str
    classification_snapshot: dict[str, Any] | None = None


@router.get("", response_model=UpdateListOut)
async def list_updates(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=_MAX_OFFSET),
    sort: ExplorerSort = Query(
        default="created_at_desc",
        description=(
            "``created_at_desc`` is the default explorer ordering (newest ingest first)."
        ),
    ),
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    published_from: datetime | None = None,
    published_to: datetime | None = None,
    jurisdiction: str | None = None,
    source_id: uuid.UUID | None = None,
    source_name_contains: str | None = Query(
        default=None,
        description="Case-insensitive ``ILIKE`` substring match on ``source_name``.",
    ),
    document_type: str | None = None,
    severity: str | None = Query(
        default=None,
        description=(
            "Filter by derived severity (case-insensitive). Rows with **unknown** "
            "severity are **excluded** unless ``include_unknown_severity`` is true."
        ),
    ),
    include_unknown_severity: bool = Query(
        default=False,
        description=(
            "When ``severity`` is set, also include rows whose severity cannot be derived."
        ),
    ),
    explorer_status: ExplorerStatus | None = None,
    _current: User = Depends(
        require_roles(UserRole.VIEWER, UserRole.ANALYST, UserRole.ADMIN)
    ),
    db: AsyncSession = Depends(get_db),
) -> UpdateListOut:
    if created_from is not None and created_to is not None and created_from > created_to:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="created_from must be before or equal to created_to",
        )
    if (
        published_from is not None
        and published_to is not None
        and published_from > published_to
    ):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="published_from must be before or equal to published_to",
        )

    page = await updates_repo.fetch_updates_page(
        db,
        limit=limit,
        offset=offset,
        sort=sort,
        created_from=created_from,
        created_to=created_to,
        published_from=published_from,
        published_to=published_to,
        jurisdiction=jurisdiction,
        source_id=source_id,
        source_name_contains=source_name_contains,
        document_type=document_type,
        severity=severity,
        include_unknown_severity=include_unknown_severity,
        explorer_status=explorer_status,
    )
    return UpdateListOut(
        items=[
            UpdateListItemOut(
                id=r.id,
                raw_capture_id=r.raw_capture_id,
                source_id=r.source_id,
                source_name=r.source_name,
                jurisdiction=r.jurisdiction,
                title=r.title,
                published_at=r.published_at,
                item_url=r.item_url,
                document_type=r.document_type,
                body_snippet=r.body_snippet,
                run_id=r.run_id,
                created_at=r.created_at,
                explorer_status=r.explorer_status,  # type: ignore[arg-type]
                derived_severity=r.derived_severity,
            )
            for r in page.items
        ],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
        sort=page.sort,
        default_sort=page.default_sort,
    )


@router.post(
    "/{normalized_update_id}/feedback",
    response_model=UpdateFeedbackCreatedOut,
    status_code=status.HTTP_201_CREATED,
)
async def post_update_feedback(
    normalized_update_id: uuid.UUID,
    body: UpdateFeedbackCreate,
    current: User = Depends(require_roles(UserRole.ANALYST, UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> UpdateFeedbackCreatedOut:
    row = await db.scalar(
        select(NormalizedUpdateRow).where(NormalizedUpdateRow.id == normalized_update_id)
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Update not found")

    overlay = await updates_repo.fetch_classification_overlay(
        db,
        run_id=row.run_id,
        normalized_update_id=row.id,
    )
    created = await feedback_repo.insert_feedback(
        db,
        user_id=current.id,
        normalized_update_id=row.id,
        run_id=row.run_id,
        classification_snapshot=overlay,
        kind=body.kind,
        comment=body.comment,
    )
    await db.commit()
    return UpdateFeedbackCreatedOut(
        id=created.id,
        created_at=created.created_at,
        normalized_update_id=created.normalized_update_id,
        run_id=created.run_id,
        kind=created.kind.value,
        classification_snapshot=created.classification_snapshot,
    )


@router.get("/{normalized_update_id}", response_model=UpdateDetailOut)
async def get_update_detail(
    normalized_update_id: uuid.UUID,
    _current: User = Depends(
        require_roles(UserRole.VIEWER, UserRole.ANALYST, UserRole.ADMIN)
    ),
    db: AsyncSession = Depends(get_db),
) -> UpdateDetailOut:
    row = await db.scalar(
        select(NormalizedUpdateRow).where(
            NormalizedUpdateRow.id == normalized_update_id
        )
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Update not found")

    raw = await db.get(RawCapture, row.raw_capture_id)
    if raw is None:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Raw capture missing for normalized update",
        )

    overlay = await updates_repo.fetch_classification_overlay(
        db,
        run_id=row.run_id,
        normalized_update_id=row.id,
    )
    classification = None
    if overlay:
        classification = ClassificationOverlayOut(
            severity=overlay.get("severity"),
            impact_categories=list(overlay.get("impact_categories") or []),
            confidence=overlay.get("confidence"),
        )

    return UpdateDetailOut(
        normalized=NormalizedPayloadOut(
            id=row.id,
            raw_capture_id=row.raw_capture_id,
            source_id=row.source_id,
            source_name=row.source_name,
            jurisdiction=row.jurisdiction,
            title=row.title,
            published_at=row.published_at,
            item_url=row.item_url,
            document_type=row.document_type,
            body_snippet=row.body_snippet,
            summary=row.summary,
            extra_metadata=row.extra_metadata,
            parser_confidence=row.parser_confidence,
            extraction_quality=row.extraction_quality,
            run_id=row.run_id,
            created_at=row.created_at,
        ),
        raw_payload=raw.payload,
        classification=classification,
    )
