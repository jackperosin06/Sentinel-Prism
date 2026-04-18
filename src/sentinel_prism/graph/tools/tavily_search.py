"""Tavily-backed :class:`~sentinel_prism.graph.tools.types.SearchToolProtocol` (Story 3.7)."""

from __future__ import annotations

import logging
from typing import Any

from sentinel_prism.graph.tools.types import WebSearchSnippet

logger = logging.getLogger(__name__)

_TIMEOUT_LOWER = 1.0
_TIMEOUT_UPPER = 120.0
_MAX_RESULTS_LOWER = 1


def _clamp_max_results(value: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return _MAX_RESULTS_LOWER
    return max(_MAX_RESULTS_LOWER, n)


def _clamp_timeout(value: float) -> float:
    try:
        t = float(value)
    except (TypeError, ValueError):
        return _TIMEOUT_UPPER
    # Reject non-finite values (nan / inf) — would make ``min``/``max`` unreliable.
    if t != t or t in (float("inf"), float("-inf")):
        return _TIMEOUT_UPPER
    return max(_TIMEOUT_LOWER, min(t, _TIMEOUT_UPPER))


class TavilyWebSearch:
    """Async Tavily search; uses ``AsyncTavilyClient`` (non-blocking I/O)."""

    def __init__(
        self,
        *,
        api_key: str,
        default_max_results: int = 5,
        timeout: float = 20.0,
    ) -> None:
        self._api_key = api_key
        self._default_max_results = _clamp_max_results(default_max_results)
        self._timeout = _clamp_timeout(timeout)

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
    ) -> list[WebSearchSnippet]:
        # Lazy import: keeps ``import sentinel_prism.graph.tools`` light when Tavily unused.
        from tavily import AsyncTavilyClient

        q = (query or "").strip()
        if not q:
            return []

        mr = _clamp_max_results(
            max_results if max_results and max_results > 0 else self._default_max_results
        )
        client = AsyncTavilyClient(api_key=self._api_key)
        search_exc: BaseException | None = None
        try:
            raw: Any = await client.search(
                q,
                max_results=mr,
                timeout=self._timeout,
            )
        except BaseException as exc:
            search_exc = exc
            raise
        finally:
            # ``close()`` must never shadow the original search failure and must
            # not hang the node: swallow its exceptions and downgrade to a log.
            try:
                await client.close()
            except Exception as close_exc:
                logger.warning(
                    "tavily_search",
                    extra={
                        "event": "tavily_client_close_error",
                        "ctx": {
                            "error_class": type(close_exc).__name__,
                            # Don't log the original search exception detail
                            # here — caller's ``except`` will record it.
                            "had_search_error": search_exc is not None,
                        },
                    },
                )

        if not isinstance(raw, dict):
            logger.warning(
                "tavily_search",
                extra={
                    "event": "tavily_unexpected_response_shape",
                    "ctx": {"type": type(raw).__name__},
                },
            )
            return []

        items = raw.get("results") or []
        if not isinstance(items, list):
            logger.warning(
                "tavily_search",
                extra={
                    "event": "tavily_unexpected_results_shape",
                    "ctx": {"type": type(items).__name__},
                },
            )
            return []

        out: list[WebSearchSnippet] = []
        for row in items:
            if not isinstance(row, dict):
                continue
            content = row.get("content")
            if content is None:
                content = row.get("snippet")
            out.append(
                {
                    "title": str(row.get("title") or ""),
                    "url": str(row.get("url") or ""),
                    "snippet": str(content or ""),
                }
            )
        return out
