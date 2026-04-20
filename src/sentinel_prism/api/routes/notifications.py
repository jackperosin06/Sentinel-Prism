"""In-app notification inbox API (Story 5.2 — FR24)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from sentinel_prism.api.deps import get_current_user
from sentinel_prism.db.models import InAppNotification, User
from sqlalchemy.ext.asyncio import AsyncSession

from sentinel_prism.db.repositories import in_app_notifications as in_app_repo
from sentinel_prism.db.session import get_db

router = APIRouter(prefix="/notifications", tags=["notifications"])

# Cap ``offset`` to prevent trivial DoS via a single authenticated user
# paging with a multi-billion skip. 10,000 is well beyond any realistic UI
# scroll depth for an in-app inbox.
_MAX_OFFSET = 10_000


class NotificationOut(BaseModel):
    # ``from_attributes=True`` lets us build this directly from an ORM row
    # via ``model_validate``, so new columns added to ``InAppNotification``
    # do not silently drop out of the API response (previously a hand-rolled
    # ``_to_out`` mapping made that possible).
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: uuid.UUID
    run_id: uuid.UUID
    item_url: str
    team_slug: str
    severity: str
    title: str
    body: str | None = None
    read_at: datetime | None = None
    created_at: datetime


class NotificationListOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[NotificationOut] = Field(default_factory=list)
    #: ``True`` when at least one more row exists beyond the returned page
    #: (computed via a ``limit+1`` probe in the repository).
    has_more: bool = False


@router.get("", response_model=NotificationListOut)
async def list_notifications(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=_MAX_OFFSET),
    unread: bool = Query(
        default=False,
        description=(
            "When true, only return notifications where ``read_at IS NULL``. "
            "Backed by partial index ``ix_in_app_notifications_user_unread``."
        ),
    ),
    current: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NotificationListOut:
    rows, has_more = await in_app_repo.list_for_user(
        db,
        user_id=current.id,
        limit=limit,
        offset=offset,
        unread_only=unread,
    )
    return NotificationListOut(
        items=[NotificationOut.model_validate(r) for r in rows],
        has_more=has_more,
    )


@router.patch("/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_notification_read(
    notification_id: uuid.UUID,
    current: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    ok = await in_app_repo.mark_read(
        db,
        notification_id=notification_id,
        user_id=current.id,
        read_at=datetime.now(timezone.utc),
    )
    if not ok:
        # No row owned by ``current`` with this id — same 404 for both
        # "does not exist" and "belongs to another user" (the latter is the
        # wrong-user boundary check; do not leak existence).
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Notification not found")
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
