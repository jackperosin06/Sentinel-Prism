"""Search / web-research configuration (Story 3.7)."""

from sentinel_prism.services.search.settings import (
    WebSearchSettings,
    get_tavily_api_key_for_search,
    get_web_search_settings,
)

__all__ = [
    "WebSearchSettings",
    "get_tavily_api_key_for_search",
    "get_web_search_settings",
]
