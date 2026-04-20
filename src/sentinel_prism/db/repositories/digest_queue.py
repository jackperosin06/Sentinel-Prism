"""Digest notification queue persistence (Story 5.4)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from sentinel_prism.db.models import NotificationDigestQueueItem


async def enqueue_digest_item_ignore_conflict(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    item_url: str,
    team_slug: str,
    channel_slug: str | None,
    severity: str,
    title: str | None,
) -> bool:
    """Insert one digest queue row; return True if inserted."""

    stmt = (
        pg_insert(NotificationDigestQueueItem)
        .values(
            id=uuid.uuid4(),
            run_id=run_id,
            item_url=item_url,
            team_slug=team_slug,
            channel_slug=channel_slug,
            severity=severity,
            title=title,
        )
        .on_conflict_do_nothing(
            constraint="uq_notification_digest_queue_run_item_team",
        )
    )
    result = await session.execute(stmt)
    rc = getattr(result, "rowcount", None)
    if rc is None:
        return False
    return (rc or 0) > 0


async def list_pending_batch(
    session: AsyncSession, *, limit: int
) -> list[NotificationDigestQueueItem]:
    """Oldest-first pending rows (no status column — rows are deleted after flush)."""

    stmt = (
        select(NotificationDigestQueueItem)
        .order_by(
            NotificationDigestQueueItem.created_at.asc(),
            NotificationDigestQueueItem.id.asc(),
        )
        .limit(limit)
    )
    result = await session.scalars(stmt)
    return list(result.all())


async def delete_by_ids(
    session: AsyncSession, *, ids: Sequence[uuid.UUID]
) -> int:
    """Remove flushed rows."""

    if not ids:
        return 0
    stmt = delete(NotificationDigestQueueItem).where(
        NotificationDigestQueueItem.id.in_(list(ids))
    )
    result = await session.execute(stmt)
    return int(result.rowcount or 0)
