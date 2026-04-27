"""Kick off the regulatory graph after a successful poll has persisted new rows (FR36)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from sentinel_prism.db.models import NormalizedUpdateRow
from sentinel_prism.db.session import get_session_factory
from sentinel_prism.graph.runtime import get_regulatory_graph
from sentinel_prism.graph.state import new_post_poll_pipeline_state, PipelineTrigger
from sentinel_prism.services.ingestion.normalize import (
    normalized_update_orm_to_pipeline_state_dict,
)

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

logger = logging.getLogger(__name__)


def _get_graph() -> CompiledStateGraph | None:
    return get_regulatory_graph()


async def run_regulatory_pipeline_after_ingest(
    *,
    source_id: uuid.UUID,
    trigger: PipelineTrigger,
    normalized_update_ids: list[uuid.UUID],
) -> None:
    """Run ``classify → (human review?) → brief → route`` for persisted ingest.

    **Pre:** ``execute_poll`` has committed raw + normalized rows for ``source_id``;
    ``run_id`` on those rows is still ``NULL``. This assigns a fresh ``run_id``,
    commits, and invokes the graph with ``ingestion_entry`` = ``post_poll`` so
    ``scout`` / ``normalize`` are skipped.

    If the app has not yet registered a compiled graph (e.g. unit tests), this
    is a no-op and logs a warning.
    """

    if not normalized_update_ids:
        return
    g = _get_graph()
    if g is None:
        logger.warning(
            "regulatory_post_poll_skipped",
            extra={
                "event": "regulatory_post_poll_skipped",
                "source_id": str(source_id),
                "trigger": trigger,
                "reason": "regulatory_graph_unavailable",
                "ingested_count": len(normalized_update_ids),
            },
        )
        return

    run_id = uuid.uuid4()
    factory = get_session_factory()
    norms: list[dict[str, Any]] = []
    try:
        async with factory() as session:
            rows: list[NormalizedUpdateRow] = []
            for nid in normalized_update_ids:
                r = await session.get(NormalizedUpdateRow, nid)
                if r is None or r.source_id != source_id:
                    continue
                r.run_id = run_id
                rows.append(r)
            if not rows:
                logger.info(
                    "regulatory_post_poll_no_rows",
                    extra={
                        "event": "regulatory_post_poll_no_rows",
                        "source_id": str(source_id),
                        "run_id": str(run_id),
                    },
                )
                return
            norms = [normalized_update_orm_to_pipeline_state_dict(r) for r in rows]
            await session.commit()
    except Exception:
        logger.exception(
            "regulatory_post_poll_assign_run_id_failed",
            extra={
                "source_id": str(source_id),
                "run_id": str(run_id),
            },
        )
        return

    st = new_post_poll_pipeline_state(
        run_id,
        source_id=source_id,
        trigger=trigger,
        normalized_updates=norms,
    )
    cfg: dict = {"configurable": {"thread_id": str(run_id)}}
    try:
        await g.ainvoke(st, cfg)
    except Exception:
        logger.exception(
            "regulatory_post_poll_ainvoke_failed",
            extra={
                "source_id": str(source_id),
                "run_id": str(run_id),
            },
        )


def schedule_regulatory_pipeline_after_ingest(
    *,
    source_id: uuid.UUID,
    trigger: PipelineTrigger,
    normalized_update_ids: list[uuid.UUID],
    _create_task: Callable[[Awaitable[object]], object] = asyncio.create_task,  # tests
) -> None:
    """Run :func:`run_regulatory_pipeline_after_ingest` in a background task (non-blocking for poll)."""

    if not normalized_update_ids:
        return

    async def _go() -> None:
        await run_regulatory_pipeline_after_ingest(
            source_id=source_id,
            trigger=trigger,
            normalized_update_ids=normalized_update_ids,
        )

    _create_task(_go())
