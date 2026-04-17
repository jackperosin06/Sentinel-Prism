"""Orchestrate raw + normalized persistence after dedup (Story 3.1)."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from sentinel_prism.db.repositories import captures as captures_repo
from sentinel_prism.services.connectors.scout_raw_item import ScoutRawItem
from sentinel_prism.services.ingestion.normalize import normalize_scout_item

logger = logging.getLogger(__name__)


async def persist_new_items_after_dedup(
    session: AsyncSession,
    *,
    source_id: uuid.UUID,
    source_name: str,
    jurisdiction: str,
    new_items: list[ScoutRawItem],
) -> None:
    """Insert raw capture + normalized row per deduped item (same transaction as caller).

    Atomic for the whole batch: a failure on item N rolls back prior items too
    (so dedup fingerprints registered in the same session are also rolled back
    and the batch is retried cleanly on the next poll). Per-item savepoint
    semantics are deferred — see ``_bmad-output/implementation-artifacts/deferred-work.md``.
    """

    if not new_items:
        # Short-circuit: empty batches do no work and avoid noisy zero-count logs.
        return

    for item in new_items:
        # Each scout item is expected to carry its own ``source_id`` matching
        # the caller's. A mismatch indicates a multiplexed-poller regression and
        # must not be persisted as silent audit evidence.
        if item.source_id != source_id:
            raise ValueError(
                "ScoutRawItem.source_id mismatch in persist batch "
                f"(item={item.source_id!r}, batch={source_id!r})"
            )

        raw_id = await captures_repo.insert_raw_capture(
            session, source_id=source_id, item=item
        )
        normalized = normalize_scout_item(
            item,
            source_id=source_id,
            source_name=source_name,
            jurisdiction=jurisdiction,
        )
        norm_id = await captures_repo.insert_normalized_update(
            session,
            raw_capture_id=raw_id,
            normalized=normalized,
        )
        logger.info(
            "capture_persisted",
            extra={
                # Namespaced to avoid collision with ``LogRecord`` built-ins if
                # future aggregators add reserved keys at the top level.
                "event": "capture_persisted",
                "ctx": {
                    "source_id": str(source_id),
                    "raw_capture_id": str(raw_id),
                    "normalized_update_id": str(norm_id),
                    "item_url": item.item_url,
                },
            },
        )
