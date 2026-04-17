"""Connector poll entrypoint — RSS/HTTP fetch (Story 2.3), dedup (2.4), fallback (2.5)."""

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
from sentinel_prism.services.ingestion.persist import persist_new_items_after_dedup
from sentinel_prism.services.connectors.errors import ConnectorFetchFailed
from sentinel_prism.services.connectors.scout_fetch import (
    FallbackFetchUnexpectedError,
    PrimaryAndFallbackFailed,
    fetch_scout_items_with_fallback,
)
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
        fallback_url: str | None = row.fallback_url
        fallback_mode = row.fallback_mode
        # Snapshot audit-trail primitives while the row is still attached so we
        # do not re-fetch in the success tail (no second round-trip, no TOCTOU
        # against an admin rename / jurisdiction change mid-poll).
        source_name: str = row.name
        source_jurisdiction: str = row.jurisdiction

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

    try:
        items, fetch_outcome = await fetch_scout_items_with_fallback(
            source_id=source_id,
            source_type=source_type,
            primary_url=primary_url,
            fallback_mode=fallback_mode,
            fallback_url=fallback_url,
            fetched_at=fetched_at,
            trigger=trigger,
        )
    except ConnectorFetchFailed as primary_exc:
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
    except PrimaryAndFallbackFailed as both:
        primary_exc = both.primary
        fb_exc = both.fallback
        assert fallback_url is not None
        _pu = httpx.URL(primary_url)
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
                error_class=f"{primary_exc.error_class}|{fb_exc.error_class}",
            )
            await session.commit()
        return []
    except FallbackFetchUnexpectedError as wrapped:
        fb_other_exc = wrapped.cause
        assert fallback_url is not None
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

    # Post-fetch persistence (clear prior failure, register fingerprints, persist).
    # If any step raises (transient DB error, persist failure), attribute to the
    # path we just succeeded on and record a poll failure so the source health
    # signal stays consistent. ``failed_stage`` lets operators separate dedup
    # faults from persist faults in the log/metric reason.
    #
    # Reason-string contract (persisted on ``sources.last_poll_failure``):
    #   f"{failed_stage} after {fetch_outcome} success: {exc}"
    # Known stages (each distinguishable by prefix for dashboards / alerts):
    #   - "clear_poll_failure after <outcome> success: ..." — sentinel clear step
    #   - "dedup after <outcome> success: ..."             — register_new_items
    #   - "persist after <outcome> success: ..."           — persist_new_items_after_dedup
    #   - "metrics after <outcome> success: ..."           — record_poll_success_metrics
    # The "dedup after" prefix is preserved for Story 2.4 compatibility; any new
    # stage must extend this comment and any downstream filters.
    failed_stage: str = "clear_poll_failure"
    try:
        async with factory() as session:
            await sources_repo.clear_poll_failure(session, source_id)
            failed_stage = "dedup"
            new_items = await ingestion_dedup.register_new_items(
                session, source_id, items
            )
            failed_stage = "persist"
            await persist_new_items_after_dedup(
                session,
                source_id=source_id,
                source_name=source_name,
                jurisdiction=source_jurisdiction,
                new_items=new_items,
            )
            failed_stage = "metrics"
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
            # Keep legacy event name for existing dashboards/alerts; include the
            # stage-specific event name in structured context for migration.
            "poll_dedup_failed",
            extra={
                "event_name": f"poll_{failed_stage}_failed",
                "source_id": str(source_id),
                "trigger": trigger,
                "fetch_outcome": fetch_outcome,
                "failed_stage": failed_stage,
                "error_class": type(dedup_exc).__name__,
                "error": str(dedup_exc),
            },
        )
        async with factory() as session:
            await sources_repo.record_poll_failure(
                session,
                source_id,
                reason=f"{failed_stage} after {fetch_outcome} success: {dedup_exc}",
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
