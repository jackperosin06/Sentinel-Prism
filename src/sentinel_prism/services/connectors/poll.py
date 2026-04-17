"""Connector poll entrypoint — RSS/HTTP fetch (Story 2.3), dedup (2.4), fallback (2.5)."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Literal

import httpx

from sentinel_prism.db.models import FallbackMode, SourceType
from sentinel_prism.db.repositories import ingestion_dedup
from sentinel_prism.db.repositories import sources as sources_repo
from sentinel_prism.db.session import get_session_factory
from sentinel_prism.services.connectors.errors import ConnectorFetchFailed
from sentinel_prism.services.connectors.html_fallback import fetch_html_page_items
from sentinel_prism.services.connectors.http_fetch import fetch_http_page_item
from sentinel_prism.services.connectors.rss_fetch import fetch_rss_items
from sentinel_prism.services.connectors.scout_raw_item import ScoutRawItem

logger = logging.getLogger(__name__)

PollTrigger = Literal["scheduled", "manual"]


def _fallback_configured(mode: FallbackMode, url: str | None) -> bool:
    # ``_fetch_fallback`` raises for unknown modes — do not silently treat a future
    # ``FallbackMode`` variant as "not configured"; fall through so the ValueError
    # from ``_fetch_fallback`` surfaces when a new mode is wired in upstream.
    return bool(url) and mode != FallbackMode.NONE


async def _fetch_by_source_type(
    *,
    source_id: uuid.UUID,
    source_type: SourceType,
    url: str,
    fetched_at: datetime,
    trigger: PollTrigger,
) -> list[ScoutRawItem]:
    if source_type == SourceType.RSS:
        return await fetch_rss_items(
            source_id=source_id,
            url=url,
            fetched_at=fetched_at,
            trigger=trigger,
        )
    if source_type == SourceType.HTTP:
        return await fetch_http_page_item(
            source_id=source_id,
            url=url,
            fetched_at=fetched_at,
            trigger=trigger,
        )
    raise ValueError(f"unsupported source_type: {source_type!r}")


async def _fetch_fallback(
    *,
    source_id: uuid.UUID,
    source_type: SourceType,
    mode: FallbackMode,
    fallback_url: str,
    fetched_at: datetime,
    trigger: PollTrigger,
) -> list[ScoutRawItem]:
    if mode == FallbackMode.HTML_PAGE:
        return await fetch_html_page_items(
            source_id=source_id,
            url=fallback_url,
            fetched_at=fetched_at,
            trigger=trigger,
        )
    if mode == FallbackMode.SAME_AS_PRIMARY:
        return await _fetch_by_source_type(
            source_id=source_id,
            source_type=source_type,
            url=fallback_url,
            fetched_at=fetched_at,
            trigger=trigger,
        )
    raise ValueError(f"unsupported fallback_mode for fetch: {mode!r}")


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
        fallback_url: str | None = row.fallback_url
        fallback_mode: FallbackMode = row.fallback_mode

    # Guard unsupported source_type *before* the primary/fallback try block so an
    # unrelated exception raised inside the try is not mis-logged as a connector error.
    if source_type not in (SourceType.RSS, SourceType.HTTP):
        msg = f"unsupported source_type: {source_type!r}"
        logger.warning(
            "poll_unsupported_source_type",
            extra={
                "source_id": str(source_id),
                "trigger": trigger,
                "source_type": str(source_type),
            },
        )
        # Record one failure, then auto-disable so the scheduler stops polling this
        # mistyped row on every tick (Story 2.6 review Decision 1). Operators see one
        # ``last_poll_failure`` + ``enabled=False`` and ``error_rate`` stays accurate.
        async with factory() as session:
            await sources_repo.record_poll_failure(
                session,
                source_id,
                reason=msg,
                error_class="UnsupportedSourceType",
            )
            await sources_repo.disable_source(session, source_id)
            await session.commit()
        logger.warning(
            "source_auto_disabled",
            extra={
                "source_id": str(source_id),
                "trigger": trigger,
                "reason": "unsupported_source_type",
                "source_type": str(source_type),
            },
        )
        return []

    fetched_at = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    fetch_outcome: Literal["primary", "fallback"] | None = None

    # Primary fetch — scoped narrowly so only primary-path errors are attributed to it.
    try:
        items = await _fetch_by_source_type(
            source_id=source_id,
            source_type=source_type,
            url=primary_url,
            fetched_at=fetched_at,
            trigger=trigger,
        )
        fetch_outcome = "primary"
    except ConnectorFetchFailed as primary_exc:
        if not _fallback_configured(fallback_mode, fallback_url):
            async with factory() as session:
                await sources_repo.record_poll_failure(
                    session,
                    source_id,
                    reason=str(primary_exc),
                    error_class=primary_exc.error_class,
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
                    "error_class": primary_exc.error_class,
                    "error": str(primary_exc),
                    "fetch_path": "primary",
                },
            )
            return []

        assert fallback_url is not None
        _pu = httpx.URL(primary_url)
        logger.info(
            "poll_primary_failed_try_fallback",
            extra={
                "source_id": str(source_id),
                "trigger": trigger,
                "primary_error_class": primary_exc.error_class,
                "primary_url_host": _pu.host,
                "primary_url_path": _pu.path,
                "fallback_mode": fallback_mode.value,
            },
        )
        # Fallback fetch — scoped so every failure here carries fetch_path=fallback
        # and the fallback URL context (not the primary URL).
        try:
            items = await _fetch_fallback(
                source_id=source_id,
                source_type=source_type,
                mode=fallback_mode,
                fallback_url=fallback_url,
                fetched_at=fetched_at,
                trigger=trigger,
            )
            fetch_outcome = "fallback"
        except ConnectorFetchFailed as fb_exc:
            _fu = httpx.URL(fallback_url)
            logger.warning(
                "poll_fetch_both_failed",
                extra={
                    "source_id": str(source_id),
                    "trigger": trigger,
                    "outcome": "both_failed",
                    "primary_error_class": primary_exc.error_class,
                    "fallback_error_class": fb_exc.error_class,
                    "primary_url_host": _pu.host,
                    "primary_url_path": _pu.path,
                    "fallback_url_host": _fu.host,
                    "fallback_url_path": _fu.path,
                },
            )
            async with factory() as session:
                await sources_repo.record_poll_failure(
                    session,
                    source_id,
                    reason=f"primary: {primary_exc}; fallback: {fb_exc}",
                    # Persist BOTH classes so operators filtering on error_class see
                    # the full failure signature (structured log already separates them).
                    error_class=f"{primary_exc.error_class}|{fb_exc.error_class}",
                )
                await session.commit()
            return []
        except Exception as fb_other_exc:
            # Non-ConnectorFetchFailed raised during fallback (e.g. unknown mode) —
            # attribute to the fallback path so operators do not chase primary.
            _fu = httpx.URL(fallback_url)
            logger.warning(
                "poll_connector_error",
                extra={
                    "source_id": str(source_id),
                    "trigger": trigger,
                    "url_host": _fu.host,
                    "url_path": _fu.path,
                    "error_class": type(fb_other_exc).__name__,
                    "error": str(fb_other_exc),
                    "fetch_path": "fallback",
                },
            )
            async with factory() as session:
                await sources_repo.record_poll_failure(
                    session,
                    source_id,
                    reason=f"fallback: {fb_other_exc}",
                    error_class=type(fb_other_exc).__name__,
                )
                await session.commit()
            return []
    except Exception as exc:
        # Primary-only catch-all (non-ConnectorFetchFailed, by contract means "do not
        # try fallback" — Story 2.5 AC2). Log with fetch_path=primary.
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
                "fetch_path": "primary",
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

    # Post-fetch persistence (clear prior failure, register fingerprints).
    # If dedup raises (transient DB error), attribute to the path we just succeeded on
    # and record a poll failure so the source health signal stays consistent.
    try:
        async with factory() as session:
            await sources_repo.clear_poll_failure(session, source_id)
            new_items = await ingestion_dedup.register_new_items(
                session, source_id, items
            )
            # Explicit invariant check — ``assert`` would vanish under ``python -O``
            # and then pass ``fetch_path=None`` into ``record_poll_success_metrics``
            # (Story 2.6 review P6).
            if fetch_outcome is None:
                raise RuntimeError(
                    "internal invariant: fetch_outcome must be set before success tail"
                )
            await sources_repo.record_poll_success_metrics(
                session,
                source_id,
                items_new_count=len(new_items),
                latency_ms=elapsed_ms,
                fetch_path=fetch_outcome,
                fetched_at=fetched_at,
            )
            await session.commit()
    except Exception as dedup_exc:
        logger.warning(
            "poll_dedup_failed",
            extra={
                "source_id": str(source_id),
                "trigger": trigger,
                "fetch_outcome": fetch_outcome,
                "error_class": type(dedup_exc).__name__,
                "error": str(dedup_exc),
            },
        )
        async with factory() as session:
            await sources_repo.record_poll_failure(
                session,
                source_id,
                reason=f"dedup after {fetch_outcome} success: {dedup_exc}",
                error_class=type(dedup_exc).__name__,
            )
            await session.commit()
        return []

    logger.info(
        "poll_fetch_outcome",
        extra={
            "source_id": str(source_id),
            "trigger": trigger,
            "outcome": fetch_outcome,
        },
    )
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
