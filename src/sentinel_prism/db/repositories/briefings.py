"""Briefing persistence (Story 4.3)."""

from __future__ import annotations

import uuid
from typing import Any
from uuid import UUID

from sqlalchemy import literal_column, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from sentinel_prism.db.models import Briefing

LIST_BRIEFINGS_MAX_LIMIT = 200
LIST_BRIEFINGS_DEFAULT_LIMIT = 50


async def upsert_briefing_for_run(
    session: AsyncSession,
    *,
    run_id: str | UUID,
    source_id: UUID | None,
    grouping_dimensions: list[str],
    groups: list[dict[str, Any]],
) -> tuple[uuid.UUID, bool]:
    """Atomically insert-or-update the briefing row for ``run_id``.

    Uses Postgres ``INSERT ... ON CONFLICT (run_id) DO UPDATE ... RETURNING`` so
    two concurrent callers both complete without racing on ``uq_briefings_run_id``.
    Returns ``(briefing_id, created)`` where ``created`` is ``True`` on first
    insert and ``False`` on conflict-update — callers use this to fire
    ``BRIEFING_GENERATED`` exactly once per run (Story 4.3 Decision 4).

    The ``xmax = 0`` trick distinguishes insert (fresh heap tuple, xmax 0) from
    update (prior tuple's xmax is the updating transaction id).
    """

    rid = uuid.UUID(str(run_id).strip())
    values: dict[str, Any] = {
        "run_id": rid,
        "source_id": source_id,
        "grouping_dimensions": list(grouping_dimensions),
        "groups": list(groups),
    }
    base = pg_insert(Briefing).values(**values)
    stmt = base.on_conflict_do_update(
        index_elements=["run_id"],
        set_={
            "source_id": base.excluded.source_id,
            "grouping_dimensions": base.excluded.grouping_dimensions,
            "groups": base.excluded.groups,
        },
    ).returning(Briefing.id, literal_column("(xmax = 0)").label("created"))
    row = (await session.execute(stmt)).one()
    return row.id, bool(row.created)


async def list_briefings(
    session: AsyncSession,
    *,
    limit: int = LIST_BRIEFINGS_DEFAULT_LIMIT,
    offset: int = 0,
) -> list[Briefing]:
    lim = max(1, min(int(limit), LIST_BRIEFINGS_MAX_LIMIT))
    off = max(0, int(offset))
    res = await session.scalars(
        select(Briefing)
        # Secondary ``id DESC`` tiebreaker — ``created_at`` alone can tie at
        # microsecond granularity (batch re-runs, scripted backfills), causing
        # Postgres to return different orderings per query and clients paging
        # on offset to drop or duplicate rows across page boundaries.
        .order_by(Briefing.created_at.desc(), Briefing.id.desc())
        .limit(lim)
        .offset(off)
    )
    return list(res.all())


async def get_briefing_by_id(
    session: AsyncSession,
    briefing_id: str | UUID,
) -> Briefing | None:
    bid = uuid.UUID(str(briefing_id).strip())
    return await session.get(Briefing, bid)
