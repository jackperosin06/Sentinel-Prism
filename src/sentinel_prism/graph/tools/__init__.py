"""Tool adapters (e.g. public web search) — Story 3+."""

from sentinel_prism.graph.tools.context_format import format_web_context_for_llm
from sentinel_prism.graph.tools.factory import create_web_search_tool
from sentinel_prism.graph.tools.query_builder import (
    PUBLIC_SEARCH_FIELDS,
    build_public_web_search_query,
    normalized_keys_outside_allowlist,
)
from sentinel_prism.graph.tools.stub_search import NullWebSearchTool, StubWebSearchTool
from sentinel_prism.graph.tools.types import SearchToolProtocol, WebSearchSnippet

__all__ = [
    "PUBLIC_SEARCH_FIELDS",
    "NullWebSearchTool",
    "SearchToolProtocol",
    "StubWebSearchTool",
    "WebSearchSnippet",
    "build_public_web_search_query",
    "create_web_search_tool",
    "normalized_keys_outside_allowlist",
    "format_web_context_for_llm",
]

