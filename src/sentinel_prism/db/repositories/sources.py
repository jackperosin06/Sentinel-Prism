"""Async persistence for regulatory sources (Story 2.1)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy import func, literal_column, select, type_coerce, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from sentinel_prism.db.models import FallbackMode, Source, SourceType

FetchPath = Literal["primary", "fallback"]
_VALID_FETCH_PATHS: frozenset[str] = frozenset({"primary", "fallback"})


async def list_sources(
    session: AsyncSession,
    *,
    limit: int | None = None,
    offset: int | None = None,
) -> list[Source]:
    """Sources ordered by ``created_at`` ascending; optional pagination (Story 2.6)."""

    stmt = select(Source).order_by(Source.created_at.asc(), Source.id.asc())
    if offset is not None and offset > 0:
        stmt = stmt.offset(offset)
    if limit is not None:
        stmt = stmt.limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_source_by_id(
    session: AsyncSession, source_id: uuid.UUID
) -> Source | None:
    return await session.get(Source, source_id)


async def create_source(
    session: AsyncSession,
    *,
    name: str,
    jurisdiction: str,
    source_type: SourceType,
    primary_url: str,
    schedule: str,
    fallback_url: str | None = None,
    fallback_mode: FallbackMode = FallbackMode.NONE,
    enabled: bool = True,
    extra_metadata: dict[str, Any] | None = None,
) -> Source:
    row = Source(
        name=name,
        jurisdiction=jurisdiction,
        source_type=source_type,
        primary_url=primary_url,
        fallback_url=fallback_url,
        fallback_mode=fallback_mode,
        schedule=schedule,
        enabled=enabled,
        extra_metadata=extra_metadata,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


async def update_source_fields(
    session: AsyncSession,
    source_id: uuid.UUID,
    fields: dict[str, Any],
) -> Source | None:
    """Apply only keys present in ``fields`` (from ``model_dump(exclude_unset=True)``)."""

    row = await get_source_by_id(session, source_id)
    if row is None:
        return None
    for key, value in fields.items():
        setattr(row, key, value)
    await session.flush()
    await session.refresh(row)
    return row


async def delete_source(session: AsyncSession, source_id: uuid.UUID) -> bool:
    row = await get_source_by_id(session, source_id)
    if row is None:
        return False
    await session.delete(row)
    await session.flush()
    return True


async def record_poll_failure(
    session: AsyncSession,
    source_id: uuid.UUID,
    *,
    reason: str,
    error_class: str,
) -> None:
    """Atomic merge of ``last_poll_failure`` + failure counter bump (Story 2.4 — FR4; 2.6 race-safe)."""

    now = datetime.now(timezone.utc)
    # Store a JSON-native timestamp (not ``isoformat()``) so the DB and the API response
    # share a single ``datetime`` contract — no ad-hoc string parsing in the serializer.
    patch = {
        "last_poll_failure": {
            "at": now.isoformat(),
            "reason": reason[:4000],
            "error_class": error_class[:255],
        }
    }
    # Postgres JSONB ``||`` merges top-level keys *only* when both sides are objects;
    # any scalar (including JSON ``null``) is silently promoted to a 1-element array
    # and the result becomes ``[null, {...}]``. ``coalesce`` guards SQL NULL, so we
    # also ``nullif(..., 'null'::jsonb)`` to downgrade the JSON-null scalar — this
    # keeps legacy rows written before ``JSONB(none_as_null=True)`` was set on the
    # column from poisoning the merge. Counters use ``col = col + :n`` so concurrent
    # pollers cannot lose increments (Story 2.6 review finding P1).
    # ``literal_column`` (not ``type_coerce("null", JSONB)``) because JSONB's
    # bind_processor would ``json.dumps("null")`` → ``'"null"'`` (a JSON string),
    # not the JSON ``null`` scalar we need to strip.
    json_null = literal_column("'null'::jsonb")
    current = func.nullif(Source.extra_metadata, json_null)
    jsonb_expr = func.coalesce(current, type_coerce({}, JSONB)).op("||")(
        type_coerce(patch, JSONB)
    )
    stmt = (
        update(Source)
        .where(Source.id == source_id)
        .values(
            poll_attempts_failed=Source.poll_attempts_failed + 1,
            last_failure_at=now,
            extra_metadata=jsonb_expr,
        )
    )
    await session.execute(stmt)
    await session.flush()


async def record_poll_success_metrics(
    session: AsyncSession,
    source_id: uuid.UUID,
    *,
    items_new_count: int,
    latency_ms: int,
    fetch_path: FetchPath,
    fetched_at: datetime,
) -> None:
    """Atomic success-counter bump on the ``execute_poll`` success tail (Story 2.6 — NFR9)."""

    # Reject unknown ``fetch_path`` values loudly rather than silently truncating to 16 chars;
    # the column constraint is defensive, ``fetch_path`` is always "primary" or "fallback"
    # in ``execute_poll``.
    if fetch_path not in _VALID_FETCH_PATHS:
        raise ValueError(
            f"fetch_path must be 'primary' or 'fallback'; got {fetch_path!r}"
        )
    stmt = (
        update(Source)
        .where(Source.id == source_id)
        .values(
            poll_attempts_success=Source.poll_attempts_success + 1,
            items_ingested_total=Source.items_ingested_total + int(items_new_count),
            # ``fetched_at`` from ``execute_poll`` captures the moment of fetch,
            # not dedup completion — dashboards on ``now - last_success_at`` stay accurate.
            last_success_at=fetched_at,
            last_success_latency_ms=int(latency_ms),
            last_success_fetch_path=fetch_path,
        )
    )
    await session.execute(stmt)
    await session.flush()


async def disable_source(session: AsyncSession, source_id: uuid.UUID) -> None:
    """Atomically flip ``enabled`` to ``False`` (Story 2.6 — unsupported source_type auto-disable)."""

    stmt = update(Source).where(Source.id == source_id).values(enabled=False)
    await session.execute(stmt)
    await session.flush()


async def clear_poll_failure(session: AsyncSession, source_id: uuid.UUID) -> None:
    row = await get_source_by_id(session, source_id)
    if row is None:
        return
    meta = dict(row.extra_metadata or {})
    if "last_poll_failure" not in meta:
        return
    del meta["last_poll_failure"]
    # Explicit check: empty dict → NULL column; non-empty dict preserves other keys.
    row.extra_metadata = meta if meta else None
