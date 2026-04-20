"""Admin API for external notification delivery log (Story 5.3 — NFR10, FR23)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from sentinel_prism.api.deps import get_db_for_admin
from sentinel_prism.db.models import (
    NotificationDeliveryChannel,
    NotificationDeliveryOutcome,
)
from sentinel_prism.db.repositories import notification_delivery_attempts as delivery_repo
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/admin/delivery-attempts", tags=["admin", "delivery"])

_MAX_OFFSET = 10_000


class DeliveryAttemptOut(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: uuid.UUID
    run_id: uuid.UUID
    item_url: str
    channel: NotificationDeliveryChannel
    outcome: NotificationDeliveryOutcome
    error_class: str | None = None
    detail: str | None = None
    provider_message_id: str | None = None
    recipient_descriptor: str
    created_at: datetime


class DeliveryAttemptListOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[DeliveryAttemptOut] = Field(default_factory=list)
    has_more: bool = False


def _to_utc_aware(value: datetime | None, *, label: str) -> datetime | None:
    """Normalize an optional datetime to UTC-aware.

    The backing column is ``TIMESTAMP WITH TIME ZONE``; comparing against
    a tz-naive ``datetime`` either raises in asyncpg or silently
    interprets the value in the session timezone. Reject naive inputs
    with a 422 so clients get a clear contract rather than off-by-tz
    results.
    """

    if value is None:
        return None
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"{label} must be timezone-aware (ISO 8601 with offset, "
                "e.g. 2026-04-21T12:00:00Z)"
            ),
        )
    return value.astimezone(timezone.utc)


@router.get("", response_model=DeliveryAttemptListOut)
async def list_delivery_attempts(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=_MAX_OFFSET),
    outcome: NotificationDeliveryOutcome | None = Query(
        default=None,
        description="Filter by outcome (pending, success, failure, skipped).",
    ),
    run_id: uuid.UUID | None = Query(default=None),
    created_after: datetime | None = Query(default=None),
    created_before: datetime | None = Query(default=None),
    db: AsyncSession = Depends(get_db_for_admin),
) -> DeliveryAttemptListOut:
    after_utc = _to_utc_aware(created_after, label="created_after")
    before_utc = _to_utc_aware(created_before, label="created_before")
    if after_utc is not None and before_utc is not None and after_utc > before_utc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="created_after must be <= created_before",
        )

    oc = outcome.value if outcome is not None else None
    rows, has_more = await delivery_repo.list_attempts(
        db,
        limit=limit,
        offset=offset,
        outcome=oc,
        run_id=run_id,
        created_after=after_utc,
        created_before=before_utc,
    )
    return DeliveryAttemptListOut(
        items=[DeliveryAttemptOut.model_validate(r) for r in rows],
        has_more=has_more,
    )
