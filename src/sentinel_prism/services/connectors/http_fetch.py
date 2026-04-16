"""Minimal HTTP GET connector for ``SourceType.HTTP`` (Story 2.3, retry Story 2.4)."""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

import httpx

from sentinel_prism.services.connectors.fetch_retry import run_http_attempt_with_retry
from sentinel_prism.services.connectors.http_client import connector_async_client
from sentinel_prism.services.connectors.scout_raw_item import ScoutRawItem

logger = logging.getLogger(__name__)

# Streaming byte cap; SNIPPET_MAX_CHARS controls how much is stored in the DTO.
MAX_HTTP_BODY_BYTES = 2 * 1024 * 1024
SNIPPET_MAX_CHARS = 8_192


async def fetch_http_page_item(
    *,
    source_id: UUID,
    url: str,
    fetched_at: datetime,
    trigger: str,
    client: httpx.AsyncClient | None = None,
) -> list[ScoutRawItem]:
    """Stream ``url`` and return a single ``ScoutRawItem`` with status, headers, snippet.

    **5xx responses** are retried then raise `ConnectorFetchFailed` if still failing.
    **4xx responses** are captured as items (``http_status`` carries the code for
    downstream health metrics in Story 2.6) without retry.
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
                if http_status_code >= 500:
                    resp.raise_for_status()
                total_bytes = 0
                async for chunk in resp.aiter_bytes(chunk_size=65_536):
                    total_bytes += len(chunk)
                    if total_bytes > MAX_HTTP_BODY_BYTES:
                        logger.warning(
                            "http_body_truncated",
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
            failure_label="http_fetch",
        )
    finally:
        if own_client:
            try:
                await client.aclose()
            except Exception:  # pragma: no cover
                pass

    try:
        text = raw.decode(encoding or "utf-8", errors="replace")
    except LookupError:
        text = raw.decode("utf-8", errors="replace")
    snippet = text[:SNIPPET_MAX_CHARS]

    item = ScoutRawItem(
        source_id=source_id,
        item_url=final_url,
        fetched_at=fetched_at,
        title=None,
        published_at=None,
        summary=None,
        http_status=http_status_code,
        content_type=content_type_val,
        body_snippet=snippet if snippet else None,
    )
    return [item]
