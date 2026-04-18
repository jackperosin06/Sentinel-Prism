"""Construct a search tool from settings (Story 3.7)."""

from __future__ import annotations

import logging

from sentinel_prism.graph.tools.stub_search import NullWebSearchTool
from sentinel_prism.graph.tools.tavily_search import TavilyWebSearch
from sentinel_prism.graph.tools.types import SearchToolProtocol
from sentinel_prism.services.search.settings import (
    WebSearchSettings,
    get_tavily_api_key_for_search,
    get_web_search_settings,
)

logger = logging.getLogger(__name__)


def create_web_search_tool(
    *,
    settings: WebSearchSettings | None = None,
) -> SearchToolProtocol:
    """Return Tavily when enabled + key present; otherwise :class:`NullWebSearchTool`."""

    s = settings if settings is not None else get_web_search_settings()
    if not s.enabled:
        return NullWebSearchTool()

    key = get_tavily_api_key_for_search()
    if not key:
        logger.warning(
            "web_search_factory",
            extra={
                "event": "web_search_enabled_missing_api_key",
                "ctx": {"hint": "set SENTINEL_TAVILY_API_KEY or TAVILY_API_KEY"},
            },
        )
        return NullWebSearchTool()

    return TavilyWebSearch(
        api_key=key,
        default_max_results=s.max_results,
        timeout=s.tavily_timeout,
    )
