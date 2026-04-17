"""HTML page fallback fetch (Story 2.5 — FR5).

Uses ``html.parser`` (stdlib) via BeautifulSoup — no extra binary parser dependency.
CPU-bound parse runs in ``asyncio.to_thread`` like ``feedparser`` in ``rss_fetch``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from uuid import UUID

import httpx
from bs4 import BeautifulSoup

from sentinel_prism.services.connectors.errors import ConnectorFetchFailed
from sentinel_prism.services.connectors.fetch_retry import run_http_attempt_with_retry
from sentinel_prism.services.connectors.http_client import connector_async_client
from sentinel_prism.services.connectors.http_fetch import MAX_HTTP_BODY_BYTES
from sentinel_prism.services.connectors.http_fetch import SNIPPET_MAX_CHARS as HTML_SNIPPET_MAX_CHARS
from sentinel_prism.services.connectors.scout_raw_item import ScoutRawItem

logger = logging.getLogger(__name__)


def _parse_html_to_items(
    raw: bytes,
    *,
    http_status: int,
    content_type: str | None,
    encoding: str | None,
    final_url: str,
    source_id: UUID,
    fetched_at: datetime,
) -> list[ScoutRawItem]:
    """Parse HTML bytes into at least one ``ScoutRawItem`` (page-level snapshot)."""

    # Pass the server-declared encoding so non-UTF-8 pages (windows-1252, latin-1, …)
    # are decoded correctly — matching how ``http_fetch`` handles the body.
    soup = BeautifulSoup(raw, "html.parser", from_encoding=encoding or None)
    title_el = soup.find("title")
    title = title_el.get_text(strip=True) if title_el else None
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    snippet = (text[:HTML_SNIPPET_MAX_CHARS] if text else None) or None
    return [
        ScoutRawItem(
            source_id=source_id,
            item_url=final_url,
            fetched_at=fetched_at,
            title=title,
            published_at=None,
            summary=None,
            http_status=http_status,
            content_type=content_type,
            body_snippet=snippet,
        )
    ]


async def fetch_html_page_items(
    *,
    source_id: UUID,
    url: str,
    fetched_at: datetime,
    trigger: str,
    client: httpx.AsyncClient | None = None,
) -> list[ScoutRawItem]:
    """GET ``url``, parse HTML off-thread, return page snapshot as ``ScoutRawItem`` rows.

    **4xx** responses raise during ``raise_for_status()`` (aligned with RSS connector:
    broken alternate endpoint is a hard failure for this path).
    """

    own_client = client is None
    if client is None:
        client = connector_async_client()

    try:

        async def _one_fetch() -> tuple[int, str | None, str | None, str, bytes]:
            chunks: list[bytes] = []
            async with client.stream("GET", url) as resp:
                http_status_code = resp.status_code
                content_type_val = resp.headers.get("content-type")
                encoding = resp.encoding
                final_url = str(resp.url)
                resp.raise_for_status()
                # Content-type guard: a 2xx response with a non-HTML body would otherwise
                # be "parsed" into garbage ScoutRawItems and logged as a fallback success.
                # Reject here so the poll is logged as ``poll_fetch_both_failed`` instead.
                if content_type_val and "html" not in content_type_val.lower():
                    raise ConnectorFetchFailed(
                        f"html_fallback: unexpected content_type {content_type_val!r}",
                        error_class="UnexpectedContentType",
                    )
                total_bytes = 0
                async for chunk in resp.aiter_bytes(chunk_size=65_536):
                    total_bytes += len(chunk)
                    if total_bytes > MAX_HTTP_BODY_BYTES:
                        logger.warning(
                            "html_fallback_body_truncated",
                            extra={
                                "source_id": str(source_id),
                                "trigger": trigger,
                                "url_host": httpx.URL(url).host,
                                "max_bytes": MAX_HTTP_BODY_BYTES,
                            },
                        )
                        break
                    chunks.append(chunk)
                raw = b"".join(chunks)
                return http_status_code, content_type_val, encoding, final_url, raw

        (
            http_status_code,
            content_type_val,
            encoding,
            final_url,
            raw,
        ) = await run_http_attempt_with_retry(
            source_id=source_id,
            trigger=trigger,
            url=url,
            operation=_one_fetch,
            failure_label="html_fallback",
        )
    finally:
        if own_client:
            try:
                await client.aclose()
            except Exception:  # pragma: no cover
                pass

    try:
        return await asyncio.to_thread(
            _parse_html_to_items,
            raw,
            http_status=http_status_code,
            content_type=content_type_val,
            encoding=encoding,
            final_url=final_url,
            source_id=source_id,
            fetched_at=fetched_at,
        )
    except Exception as exc:
        logger.warning(
            "html_fallback_parse_failed",
            extra={
                "source_id": str(source_id),
                "trigger": trigger,
                "error_class": type(exc).__name__,
                "error": str(exc),
            },
        )
        raise ConnectorFetchFailed(
            f"html_fallback parse failed: {exc}",
            error_class=type(exc).__name__,
        ) from exc
