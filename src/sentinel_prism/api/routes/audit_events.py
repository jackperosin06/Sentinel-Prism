"""Audit event search API (Story 8.1 — FR34)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sentinel_prism.api.deps import require_roles
from sentinel_prism.db.models import (
    AuditEvent,
    NormalizedUpdateRow,
    PipelineAuditAction,
    User,
    UserRole,
)
from sentinel_prism.db.repositories import audit_events as audit_events_repo
from sentinel_prism.db.session import get_db

router = APIRouter(prefix="/audit-events", tags=["audit-events"])

_MAX_OFFSET = audit_events_repo.MAX_SEARCH_OFFSET


def _validate_datetimes(
    created_after: datetime | None, created_before: datetime | None
) -> tuple[datetime | None, datetime | None]:
    """Require timezone-aware bounds; ``created_after`` / ``created_before`` are inclusive."""

    for name, value in (("created_after", created_after), ("created_before", created_before)):
        if value is not None and (
            value.tzinfo is None or value.tzinfo.utcoffset(value) is None
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"{name} must include a timezone offset, e.g. +00:00 for UTC "
                    "(inclusive bound on audit ``created_at``)."
                ),
            )
    if (
        created_after is not None
        and created_before is not None
        and created_after > created_before
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="created_after must be earlier than or equal to created_before.",
        )
    return created_after, created_before


def _parse_action(value: str | None) -> PipelineAuditAction | None:
    if value is None or value == "":
        return None
    try:
        return PipelineAuditAction(value)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid pipeline audit action: {value!r}.",
        ) from e


class AuditEventItemOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    created_at: datetime
    run_id: uuid.UUID
    action: str = Field(description="Persisted :class:`PipelineAuditAction` value.")
    source_id: uuid.UUID | None = None
    actor_user_id: uuid.UUID | None = None
    metadata: dict[str, Any] | None = Field(
        default=None,
        description="Bounded JSON from the ``audit_events.metadata`` column (NFR12).",
    )


class AuditEventSearchOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AuditEventItemOut]
    total: int
    limit: int
    offset: int


def _row_to_out(row: AuditEvent) -> AuditEventItemOut:
    act = row.action
    action_str = act.value if isinstance(act, PipelineAuditAction) else str(act)
    return AuditEventItemOut(
        id=row.id,
        created_at=row.created_at,
        run_id=row.run_id,
        action=action_str,
        source_id=row.source_id,
        actor_user_id=row.actor_user_id,
        metadata=row.event_metadata,
    )


@router.get("", response_model=AuditEventSearchOut)
async def search_audit_events(
    _user: User = Depends(
        require_roles(UserRole.ADMIN, UserRole.ANALYST, UserRole.VIEWER)
    ),
    db: AsyncSession = Depends(get_db),
    run_id: uuid.UUID | None = Query(default=None),
    source_id: uuid.UUID | None = Query(default=None),
    actor_user_id: uuid.UUID | None = Query(default=None),
    created_after: datetime | None = Query(
        default=None,
        description="Inclusive lower bound on ``audit_events.created_at`` (timezone required).",
    ),
    created_before: datetime | None = Query(
        default=None,
        description="Inclusive upper bound on ``audit_events.created_at`` (timezone required).",
    ),
    action: str | None = Query(
        default=None,
        description="Filter by :class:`PipelineAuditAction` value (e.g. ``human_review_approved``).",
    ),
    normalized_update_id: uuid.UUID | None = Query(
        default=None,
        description=(
            "Resolve ``normalized_updates`` row; if it has ``run_id``, filter audits by that run; "
            "otherwise filter by matching ``source_id`` and ``created_at`` within ±24h of the "
            "update's ``created_at`` (inclusive), **and** with any other query filters except "
            "``run_id`` (``run_id`` is rejected with 400 when the update has no ``run_id``)."
        ),
    ),
    limit: int = Query(default=50, ge=1, le=audit_events_repo.MAX_SEARCH_LIMIT),
    offset: int = Query(default=0, ge=0, le=_MAX_OFFSET),
) -> AuditEventSearchOut:
    created_after, created_before = _validate_datetimes(created_after, created_before)
    action_enum = _parse_action(action)

    nu_row: NormalizedUpdateRow | None = None
    if normalized_update_id is not None:
        nu_row = await db.scalar(
            select(NormalizedUpdateRow).where(
                NormalizedUpdateRow.id == normalized_update_id
            )
        )
        if nu_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="normalized_update not found",
            )
        if run_id is not None and nu_row.run_id is not None and nu_row.run_id != run_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "run_id query does not match the resolved normalized_update.run_id "
                    f"({nu_row.run_id})."
                ),
            )
        if run_id is not None and nu_row.run_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "run_id cannot be combined with normalized_update_id when the update has no "
                    "run_id; omit run_id to use source_id and a ±24h window around the update "
                    "created_at."
                ),
            )

    rows, total = await audit_events_repo.search_audit_events(
        db,
        run_id=run_id,
        source_id=source_id,
        actor_user_id=actor_user_id,
        created_after=created_after,
        created_before=created_before,
        action=action_enum,
        normalized_update_row=nu_row,
        limit=limit,
        offset=offset,
    )
    return AuditEventSearchOut(
        items=[_row_to_out(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )
