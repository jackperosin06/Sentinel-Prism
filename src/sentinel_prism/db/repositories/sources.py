"""Async persistence for regulatory sources (Story 2.1)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sentinel_prism.db.models import Source, SourceType


async def list_sources(session: AsyncSession) -> list[Source]:
    """All sources ordered by ``created_at`` ascending (stable list)."""

    result = await session.execute(
        select(Source).order_by(Source.created_at.asc(), Source.id.asc())
    )
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
    enabled: bool = True,
    extra_metadata: dict[str, Any] | None = None,
) -> Source:
    row = Source(
        name=name,
        jurisdiction=jurisdiction,
        source_type=source_type,
        primary_url=primary_url,
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
