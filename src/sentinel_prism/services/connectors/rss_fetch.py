"""Fetch and parse RSS / Atom feeds (Story 2.3, retry Story 2.4)."""

from __future__ import annotations

import asyncio
import calendar
import logging
import time
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import feedparser
import httpx

from sentinel_prism.services.connectors.errors import ConnectorFetchFailed
from sentinel_prism.services.connectors.fetch_retry import run_http_attempt_with_retry
from sentinel_prism.services.connectors.http_client import connector_async_client
from sentinel_prism.services.connectors.scout_raw_item import ScoutRawItem

logger = logging.getLogger(__name__)

# Cap feed body size before passing to feedparser (Story 2.4 adds per-source limits).
MAX_RSS_BODY_BYTES = 10 * 1024 * 1024  # 10 MB — large regulatory digests can be several MB
MAX_RSS_ENTRIES = 1_000  # guard against adversarial / misconfigured feeds


def _struct_time_to_utc(st: time.struct_time | None) -> datetime | None:
    if st is None:
        return None
    return datetime.fromtimestamp(calendar.timegm(st), tz=timezone.utc)


def _entry_link(entry: dict[str, Any] | Any, source_id: UUID, idx: int) -> str:
    link = entry.get("link") if hasattr(entry, "get") else getattr(entry, "link", None)
    if link:
        return str(link)
    links = entry.get("links") if hasattr(entry, "get") else getattr(entry, "links", None)
    if links and isinstance(links, list) and links:
        first = links[0]
        href = first.get("href") if isinstance(first, dict) else getattr(first, "href", None)
        if href:
            return str(href)
    # Synthesised fallback — index-stable only within a single fetch; Story 2.4 dedup
    # uses content fields so reordering still collides on the same entry text.
    return f"urn:sentinel-prism:feed-item:{source_id}:{idx}"


def _entry_title(entry: dict[str, Any] | Any) -> str | None:
    t = entry.get("title") if hasattr(entry, "get") else getattr(entry, "title", None)
    return str(t).strip() if t else None


def _entry_summary(entry: dict[str, Any] | Any) -> str | None:
    for key in ("summary", "description"):
        v = entry.get(key) if hasattr(entry, "get") else getattr(entry, key, None)
        if v:
            s = str(v).strip()
            if s:
                return s
    return None


def _entry_published(entry: dict[str, Any] | Any) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        st = entry.get(key) if hasattr(entry, "get") else getattr(entry, key, None)
        if st:
            return _struct_time_to_utc(st)
    return None


async def fetch_rss_items(
    *,
    source_id: UUID,
    url: str,
    fetched_at: datetime,
    trigger: str,
    client: httpx.AsyncClient | None = None,
) -> list[ScoutRawItem]:
    """Stream ``url``, parse RSS or Atom (in thread), return ``ScoutRawItem`` rows.

    **4xx responses** raise ``ConnectorFetchFailed`` immediately (no retry) — a 4xx
    means the feed endpoint is broken for this source.  This differs intentionally
    from ``fetch_http_page_item``, which captures 4xx as items for health observability.
    """

    own_client = client is None
    if client is None:
        client = connector_async_client()

    try:

        async def _one_fetch() -> tuple[bytes, str]:
            chunks: list[bytes] = []
            total_bytes = 0
            content_type = ""
            async with client.stream("GET", url) as resp:
                content_type = resp.headers.get("content-type", "")
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes(chunk_size=65_536):
                    total_bytes += len(chunk)
                    if total_bytes > MAX_RSS_BODY_BYTES:
                        u = httpx.URL(url)
                        logger.warning(
                            "rss_body_truncated",
                            extra={
                                "source_id": str(source_id),
                                "trigger": trigger,
                                "url_host": u.host,
                                "max_bytes": MAX_RSS_BODY_BYTES,
                            },
                        )
                        break
                    chunks.append(chunk)
            return b"".join(chunks), content_type

        raw, content_type = await run_http_attempt_with_retry(
            source_id=source_id,
            trigger=trigger,
            url=url,
            operation=_one_fetch,
            failure_label="rss_fetch",
        )
    finally:
        if own_client:
            try:
                await client.aclose()
            except Exception:  # pragma: no cover
                pass

    # feedparser is synchronous and CPU-bound — offload to thread pool.
    try:
        parsed = await asyncio.to_thread(
            feedparser.parse, raw, response_headers={"content-type": content_type}
        )
    except Exception as exc:
        logger.warning(
            "rss_parse_failed",
            extra={
                "source_id": str(source_id),
                "trigger": trigger,
                "error_class": type(exc).__name__,
                "error": str(exc),
            },
        )
        raise ConnectorFetchFailed(
            f"rss_fetch: feedparser failed: {exc}", error_class=type(exc).__name__
        ) from exc

    if getattr(parsed, "bozo", False):
        logger.warning(
            "rss_parse_bozo",
            extra={
                "source_id": str(source_id),
                "trigger": trigger,
                "bozo_exception": repr(getattr(parsed, "bozo_exception", None)),
            },
        )

    items: list[ScoutRawItem] = []
    for idx, entry in enumerate(parsed.entries[:MAX_RSS_ENTRIES]):
        try:
            items.append(
                ScoutRawItem(
                    source_id=source_id,
                    item_url=_entry_link(entry, source_id, idx),
                    fetched_at=fetched_at,
                    title=_entry_title(entry),
                    published_at=_entry_published(entry),
                    summary=_entry_summary(entry),
                )
            )
        except Exception as exc:
            logger.warning(
                "rss_entry_parse_error",
                extra={
                    "source_id": str(source_id),
                    "trigger": trigger,
                    "entry_idx": idx,
                    "error_class": type(exc).__name__,
                    "error": str(exc),
                },
            )
            continue

    return items
