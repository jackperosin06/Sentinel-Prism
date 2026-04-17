"""Raw capture and normalized update persistence (Story 3.1)."""

from __future__ import annotations

import uuid
from dataclasses import asdict, fields
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from sentinel_prism.db.models import NormalizedUpdateRow, RawCapture
from sentinel_prism.services.connectors.scout_raw_item import ScoutRawItem
from sentinel_prism.services.ingestion.normalize import NormalizedUpdate, _clean_text


def _jsonable(value: Any) -> Any:
    """Shallow JSONB coercion for primitives found on ``ScoutRawItem`` fields.

    Strings are scrubbed (NULs / invalid UTF-8) to match the same safety rules
    applied to the normalized row — the raw JSONB payload would otherwise be
    the *first* place asyncpg rejects the entire poll transaction.
    """

    if isinstance(value, str):
        return _clean_text(value) if value else value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def scout_raw_item_payload(item: ScoutRawItem) -> dict[str, Any]:
    """JSON-serializable dict reconstructible to scout fields (FR7).

    Uses :func:`dataclasses.asdict` so every current *and* future ``ScoutRawItem``
    field is captured automatically — hard-coding the field list would silently
    drop audit data when the DTO gains a header/ETag/etc. A paired round-trip
    test in ``tests/test_captures_persist.py`` verifies the payload rehydrates
    into an equivalent ``ScoutRawItem``.
    """

    # ``asdict`` recursively converts nested dataclasses, but ScoutRawItem is
    # flat. We iterate the declared fields explicitly so the intent is obvious
    # and a ``dict``-shaped field (hypothetical future ``headers``) still works.
    raw = asdict(item)
    # Preserve the field order SQLAlchemy / auditors will see in DB dumps.
    ordered = {f.name: raw[f.name] for f in fields(item)}
    return {name: _jsonable(value) for name, value in ordered.items()}


async def insert_raw_capture(
    session: AsyncSession,
    *,
    source_id: uuid.UUID,
    item: ScoutRawItem,
) -> uuid.UUID:
    # Defence-in-depth: the caller (``persist_new_items_after_dedup``) also
    # asserts this, but the repo must not accept an implicit cross-source write.
    if item.source_id != source_id:
        raise ValueError(
            "ScoutRawItem.source_id does not match insert source_id "
            f"({item.source_id!r} vs {source_id!r})"
        )
    row = RawCapture(
        source_id=source_id,
        captured_at=item.fetched_at,
        item_url=item.item_url,
        payload=scout_raw_item_payload(item),
        run_id=None,
    )
    session.add(row)
    await session.flush()
    return row.id


async def insert_normalized_update(
    session: AsyncSession,
    *,
    raw_capture_id: uuid.UUID,
    normalized: NormalizedUpdate,
) -> uuid.UUID:
    row = NormalizedUpdateRow(
        raw_capture_id=raw_capture_id,
        source_id=normalized.source_id,
        source_name=normalized.source_name,
        jurisdiction=normalized.jurisdiction,
        title=normalized.title,
        published_at=normalized.published_at,
        item_url=normalized.item_url,
        document_type=normalized.document_type,
        body_snippet=normalized.body_snippet,
        summary=normalized.summary,
        extra_metadata=normalized.extra_metadata,
        parser_confidence=normalized.parser_confidence,
        extraction_quality=normalized.extraction_quality,
        run_id=None,
    )
    session.add(row)
    await session.flush()
    return row.id
