"""Persist and query ingestion fingerprints (Story 2.4 — FR3)."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from sentinel_prism.db.models import SourceIngestedFingerprint
from sentinel_prism.services.connectors.fingerprint import content_fingerprint_for_item
from sentinel_prism.services.connectors.scout_raw_item import ScoutRawItem

logger = logging.getLogger(__name__)


async def register_new_items(
    session: AsyncSession,
    source_id: uuid.UUID,
    items: list[ScoutRawItem],
) -> list[ScoutRawItem]:
    """Return only items whose fingerprint was **new** for this source (inserted now).

    Duplicate fingerprints in ``items`` collapse to the first occurrence. Conflicts
    with existing DB rows are skipped via ``ON CONFLICT DO NOTHING``.

    Items that raise during fingerprinting are skipped with a warning rather than
    aborting the entire batch.
    """

    if not items:
        return []

    ordered_unique: list[tuple[str, ScoutRawItem]] = []
    seen: set[str] = set()
    for it in items:
        try:
            fp = content_fingerprint_for_item(it)
        except Exception as exc:
            logger.warning(
                "fingerprint_error_skipping_item",
                extra={
                    "source_id": str(source_id),
                    "item_url": it.item_url,
                    "error_class": type(exc).__name__,
                    "error": str(exc),
                },
            )
            continue
        if fp in seen:
            continue
        seen.add(fp)
        ordered_unique.append((fp, it))

    if not ordered_unique:
        return []

    now = datetime.now(timezone.utc)
    rows = [
        {
            "id": uuid.uuid4(),
            "source_id": source_id,
            "fingerprint": fp,
            "item_url": it.item_url,
            "first_seen_at": now,
        }
        for fp, it in ordered_unique
    ]

    stmt = insert(SourceIngestedFingerprint).values(rows)
    stmt = stmt.on_conflict_do_nothing(
        index_elements=["source_id", "fingerprint"],
    ).returning(SourceIngestedFingerprint.fingerprint)

    result = await session.execute(stmt)
    inserted_fps = set(result.scalars().all())

    return [it for fp, it in ordered_unique if fp in inserted_fps]
