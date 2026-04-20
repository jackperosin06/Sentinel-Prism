"""In-app notification persistence (Story 5.2 — FR24)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from sentinel_prism.db.models import InAppNotification, User


async def list_user_ids_for_team_slug(
    session: AsyncSession, *, team_slug: str
) -> list[uuid.UUID]:
    """Return active users whose ``team_slug`` matches (case-insensitive).

    The functional index ``ix_users_team_slug_lower`` backs the ``lower(...)``
    predicate, so this does not seq-scan the users table even as membership
    grows (see migration ``e6f7a8b9c0d1_*``).
    """

    norm = team_slug.strip().lower()
    if not norm:
        return []
    stmt = select(User.id).where(
        User.is_active.is_(True),
        User.team_slug.is_not(None),
        func.lower(User.team_slug) == norm,
    )
    result = await session.scalars(stmt)
    return list(result.all())


async def list_active_users_for_team_slug(
    session: AsyncSession, *, team_slug: str
) -> list[tuple[uuid.UUID, str]]:
    """Return ``(user_id, email)`` for active users matching ``team_slug`` (case-insensitive).

    Used by external notification delivery (Story 5.3) alongside
    :func:`list_user_ids_for_team_slug` so SMTP sends target the same members
    as in-app rows without duplicating the membership predicate.
    """

    norm = team_slug.strip().lower()
    if not norm:
        return []
    stmt = select(User.id, User.email).where(
        User.is_active.is_(True),
        User.team_slug.is_not(None),
        func.lower(User.team_slug) == norm,
    )
    result = await session.execute(stmt)
    return [(row[0], row[1]) for row in result.all()]


async def insert_notification_ignore_conflict(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    run_id: uuid.UUID,
    item_url: str,
    team_slug: str,
    severity: str,
    title: str,
    body: str | None,
) -> bool:
    """Insert one row; return True if a new row was written."""

    stmt = (
        pg_insert(InAppNotification)
        .values(
            id=uuid.uuid4(),
            user_id=user_id,
            run_id=run_id,
            item_url=item_url,
            team_slug=team_slug,
            severity=severity,
            title=title,
            body=body,
        )
        .on_conflict_do_nothing(constraint="uq_in_app_notifications_run_item_user")
    )
    result = await session.execute(stmt)
    return (result.rowcount or 0) > 0


async def list_for_user(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    limit: int = 50,
    offset: int = 0,
    unread_only: bool = False,
) -> tuple[list[InAppNotification], bool]:
    """Return a page of notifications for ``user_id`` plus a ``has_more`` flag.

    ``has_more`` is computed with a ``limit+1`` probe so pagination clients
    can tell when they have reached the end without issuing a second
    ``COUNT(*)`` round-trip. The returned list is always trimmed to
    ``limit`` rows.

    A stable ``(created_at DESC, id DESC)`` sort avoids page-flip duplicates
    when multiple rows share a microsecond (Story 5.2 batch inserts from a
    single enqueue tick typically collide on ``server_default=now()``).

    ``unread_only=True`` restricts the query to ``read_at IS NULL`` and is
    backed by the partial index ``ix_in_app_notifications_user_unread``.
    """

    stmt = select(InAppNotification).where(
        InAppNotification.user_id == user_id
    )
    if unread_only:
        stmt = stmt.where(InAppNotification.read_at.is_(None))
    stmt = (
        stmt.order_by(
            InAppNotification.created_at.desc(),
            InAppNotification.id.desc(),
        )
        .limit(limit + 1)
        .offset(offset)
    )
    result = await session.scalars(stmt)
    rows = list(result.all())
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]
    return rows, has_more


async def get_for_user(
    session: AsyncSession, *, notification_id: uuid.UUID, user_id: uuid.UUID
) -> InAppNotification | None:
    stmt = select(InAppNotification).where(
        InAppNotification.id == notification_id,
        InAppNotification.user_id == user_id,
    )
    return await session.scalar(stmt)


async def mark_read(
    session: AsyncSession,
    *,
    notification_id: uuid.UUID,
    user_id: uuid.UUID,
    read_at: datetime,
) -> bool:
    """Mark a notification read atomically.

    Uses a single ``UPDATE ... WHERE id=? AND user_id=? AND read_at IS NULL``
    so concurrent PATCH calls cannot race (last-writer-wins on ``read_at``),
    and a concurrent CASCADE delete cannot turn the operation into a
    ``StaleDataError`` on flush. Returns ``True`` when the notification
    exists and is now read (including the idempotent "already read" case);
    ``False`` when no such row exists for ``user_id``.
    """

    update_stmt = (
        update(InAppNotification)
        .where(
            InAppNotification.id == notification_id,
            InAppNotification.user_id == user_id,
            InAppNotification.read_at.is_(None),
        )
        .values(read_at=read_at)
    )
    result = await session.execute(update_stmt)
    if (result.rowcount or 0) > 0:
        return True

    # Either the row does not exist, or it was already read. Distinguish the
    # two so the API can return 404 for real ownership / ID mismatches
    # (which is also the wrong-user boundary check).
    exists_stmt = select(InAppNotification.id).where(
        InAppNotification.id == notification_id,
        InAppNotification.user_id == user_id,
    )
    existing = await session.scalar(exists_stmt)
    return existing is not None
