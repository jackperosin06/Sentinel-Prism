"""Review queue projection persistence (Story 4.1)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from sentinel_prism.db.models import ReviewQueueItem

# Hard ceiling on the list endpoint's page size. Enforced on the repo side so
# every caller (API route, future admin tools) is bounded identically.
LIST_PENDING_MAX_LIMIT = 200
LIST_PENDING_DEFAULT_LIMIT = 50


async def upsert_pending(
    session: AsyncSession,
    *,
    run_id: str | UUID,
    source_id: UUID | None,
    items_summary: list[dict[str, Any]],
    queued_at: datetime | None = None,
) -> None:
    """Insert or replace the queue row for ``run_id`` (one open review per run).

    ``queued_at`` is the workflow-interrupted timestamp per AC #1 when supplied;
    callers should pass ``datetime.now(tz=UTC)`` captured immediately before
    ``interrupt()`` fires. When ``None`` the DB server clock is used (backwards
    compatible, less faithful to AC #1).
    """

    rid = uuid.UUID(str(run_id).strip())
    values: dict[str, Any] = {
        "run_id": rid,
        "source_id": source_id,
        "items_summary": items_summary,
    }
    if queued_at is not None:
        values["queued_at"] = queued_at
    base = insert(ReviewQueueItem).values(**values)
    # On conflict, refresh triage JSON only — preserve the original ``queued_at``
    # so LangGraph's re-execution of ``human_review_gate`` on resume does not
    # bump the interrupt timestamp (Story 4.2).
    update_set: dict[str, Any] = {
        "source_id": base.excluded.source_id,
        "items_summary": base.excluded.items_summary,
    }
    stmt = base.on_conflict_do_update(
        index_elements=["run_id"],
        set_=update_set,
    )
    await session.execute(stmt)


async def list_pending_review_items(
    session: AsyncSession,
    *,
    limit: int = LIST_PENDING_DEFAULT_LIMIT,
    offset: int = 0,
) -> list[ReviewQueueItem]:
    """Return items awaiting review, newest first, bounded by ``limit``/``offset``."""

    lim = max(1, min(int(limit), LIST_PENDING_MAX_LIMIT))
    off = max(0, int(offset))
    res = await session.scalars(
        select(ReviewQueueItem)
        .order_by(ReviewQueueItem.queued_at.desc())
        .limit(lim)
        .offset(off)
    )
    return list(res.all())


async def delete_pending_by_run_id(
    session: AsyncSession,
    *,
    run_id: str | UUID,
) -> bool:
    """Remove the queue projection row for ``run_id`` after a successful resume.

    Raises ``ValueError`` on a malformed ``run_id`` string — callers should
    parse / validate before calling (the route typed arg already guarantees a
    ``UUID``). Returning ``False`` would conflate "row not found" with "bad
    input", hiding caller bugs in operator logs.
    """

    if isinstance(run_id, UUID):
        rid = run_id
    else:
        rid = uuid.UUID(str(run_id).strip())
    row = await session.get(ReviewQueueItem, rid)
    if row is None:
        return False
    await session.delete(row)
    return True


async def get_pending_by_run_id(
    session: AsyncSession,
    run_id: str | UUID,
) -> ReviewQueueItem | None:
    try:
        rid = uuid.UUID(str(run_id).strip()) if not isinstance(run_id, UUID) else run_id
    except (ValueError, TypeError, AttributeError):
        return None
    return await session.get(ReviewQueueItem, rid)
