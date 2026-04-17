"""Shared RSS/HTTP + fallback fetch for poll and LangGraph scout node (Story 3.3)."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Literal

import httpx

from sentinel_prism.db.models import FallbackMode, SourceType
from sentinel_prism.services.connectors.errors import ConnectorFetchFailed
from sentinel_prism.services.connectors.html_fallback import fetch_html_page_items
from sentinel_prism.services.connectors.http_fetch import fetch_http_page_item
from sentinel_prism.services.connectors.rss_fetch import fetch_rss_items
from sentinel_prism.services.connectors.scout_raw_item import ScoutRawItem

logger = logging.getLogger(__name__)

PollTrigger = Literal["scheduled", "manual"]


def fallback_configured(mode: FallbackMode, url: str | None) -> bool:
    return bool(url) and mode != FallbackMode.NONE


async def fetch_by_source_type(
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


async def fetch_fallback_items(
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
        return await fetch_by_source_type(
            source_id=source_id,
            source_type=source_type,
            url=fallback_url,
            fetched_at=fetched_at,
            trigger=trigger,
        )
    raise ValueError(f"unsupported fallback_mode for fetch: {mode!r}")


class PrimaryAndFallbackFailed(Exception):
    """Both primary and fallback connectors raised :class:`ConnectorFetchFailed`."""

    def __init__(
        self,
        primary: ConnectorFetchFailed,
        fallback: ConnectorFetchFailed,
    ) -> None:
        super().__init__(f"primary: {primary}; fallback: {fallback}")
        self.primary = primary
        self.fallback = fallback


class FallbackFetchUnexpectedError(Exception):
    """Non-:class:`ConnectorFetchFailed` during fallback (operators need ``fetch_path=fallback``)."""

    def __init__(self, cause: Exception) -> None:
        self.cause = cause
        super().__init__(str(cause))


async def fetch_scout_items_with_fallback(
    *,
    source_id: uuid.UUID,
    source_type: SourceType,
    primary_url: str,
    fallback_mode: FallbackMode,
    fallback_url: str | None,
    fetched_at: datetime,
    trigger: PollTrigger,
) -> tuple[list[ScoutRawItem], Literal["primary", "fallback"]]:
    """Try primary URL, then optional fallback — same ordering as ``execute_poll`` fetch phase.

    On failure, raises:
    - :class:`ConnectorFetchFailed` — primary failed and fallback is not configured.
    - :class:`PrimaryAndFallbackFailed` — both paths failed with connector errors.
    - :class:`FallbackFetchUnexpectedError` — unexpected error during fallback.
    - Other exceptions from the primary path propagate (no fallback attempted).
    """

    if source_type not in (SourceType.RSS, SourceType.HTTP):
        raise ValueError(f"unsupported source_type: {source_type!r}")

    try:
        items = await fetch_by_source_type(
            source_id=source_id,
            source_type=source_type,
            url=primary_url,
            fetched_at=fetched_at,
            trigger=trigger,
        )
        return items, "primary"
    except ConnectorFetchFailed as primary_exc:
        if not fallback_configured(fallback_mode, fallback_url):
            raise primary_exc
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
        try:
            items = await fetch_fallback_items(
                source_id=source_id,
                source_type=source_type,
                mode=fallback_mode,
                fallback_url=fallback_url,
                fetched_at=fetched_at,
                trigger=trigger,
            )
            return items, "fallback"
        except ConnectorFetchFailed as fb_exc:
            raise PrimaryAndFallbackFailed(primary_exc, fb_exc) from fb_exc
        except Exception as fb_other_exc:
            raise FallbackFetchUnexpectedError(fb_other_exc) from fb_other_exc
