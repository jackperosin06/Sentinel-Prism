"""Connector poll entrypoint — RSS/HTTP fetch (Story 2.3), dedup + failures (2.4)."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Literal

import httpx

from sentinel_prism.db.models import SourceType
from sentinel_prism.db.repositories import ingestion_dedup
from sentinel_prism.db.repositories import sources as sources_repo
from sentinel_prism.db.session import get_session_factory
from sentinel_prism.services.connectors.errors import ConnectorFetchFailed
from sentinel_prism.services.connectors.http_fetch import fetch_http_page_item
from sentinel_prism.services.connectors.rss_fetch import fetch_rss_items
from sentinel_prism.services.connectors.scout_raw_item import ScoutRawItem

logger = logging.getLogger(__name__)

PollTrigger = Literal["scheduled", "manual"]


async def execute_poll(
    source_id: uuid.UUID,
    *,
    trigger: PollTrigger,
) -> list[ScoutRawItem]:
    """Load ``Source``, fetch via RSS or HTTP connector, return new Scout raw items."""

    factory = get_session_factory()
    async with factory() as session:
        row = await sources_repo.get_source_by_id(session, source_id)

        # Check and extract all needed attributes *inside* the session context to avoid
        # DetachedInstanceError after session.close() (expire_on_close=True by default).
        if row is None:
            logger.info(
                "poll_skipped",
                extra={
                    "source_id": str(source_id),
                    "trigger": trigger,
                    "reason": "source_not_found",
                },
            )
            return []

        if not row.enabled:
            logger.info(
                "poll_skipped",
                extra={
                    "source_id": str(source_id),
                    "trigger": trigger,
                    "reason": "source_disabled",
                },
            )
            return []

        source_type: SourceType = row.source_type
        primary_url: str = row.primary_url

    fetched_at = datetime.now(timezone.utc)
    t0 = time.perf_counter()

    try:
        if source_type == SourceType.RSS:
            items = await fetch_rss_items(
                source_id=source_id,
                url=primary_url,
                fetched_at=fetched_at,
                trigger=trigger,
            )
        elif source_type == SourceType.HTTP:
            items = await fetch_http_page_item(
                source_id=source_id,
                url=primary_url,
                fetched_at=fetched_at,
                trigger=trigger,
            )
        else:
            msg = f"unsupported source_type: {source_type!r}"
            logger.warning(
                "poll_unsupported_source_type",
                extra={
                    "source_id": str(source_id),
                    "trigger": trigger,
                    "source_type": str(source_type),
                },
            )
            async with factory() as session:
                await sources_repo.record_poll_failure(
                    session,
                    source_id,
                    reason=msg,
                    error_class="UnsupportedSourceType",
                )
                await session.commit()
            return []
    except ConnectorFetchFailed as exc:
        async with factory() as session:
            await sources_repo.record_poll_failure(
                session,
                source_id,
                reason=str(exc),
                error_class=exc.error_class,
            )
            await session.commit()
        _u = httpx.URL(primary_url)
        logger.warning(
            "poll_connector_error",
            extra={
                "source_id": str(source_id),
                "trigger": trigger,
                "url_host": _u.host,
                "url_path": _u.path,
                "error_class": exc.error_class,
                "error": str(exc),
            },
        )
        return []
    except Exception as exc:
        _u = httpx.URL(primary_url)
        logger.warning(
            "poll_connector_error",
            extra={
                "source_id": str(source_id),
                "trigger": trigger,
                "url_host": _u.host,
                "url_path": _u.path,
                "error_class": type(exc).__name__,
                "error": str(exc),
            },
        )
        async with factory() as session:
            await sources_repo.record_poll_failure(
                session,
                source_id,
                reason=str(exc),
                error_class=type(exc).__name__,
            )
            await session.commit()
        return []

    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    async with factory() as session:
        await sources_repo.clear_poll_failure(session, source_id)
        new_items = await ingestion_dedup.register_new_items(session, source_id, items)
        await session.commit()

    logger.info(
        "poll_completed",
        extra={
            "source_id": str(source_id),
            "trigger": trigger,
            "source_type": source_type.value,
            "item_count": len(new_items),
            "fetch_latency_ms": elapsed_ms,
        },
    )
    return new_items
