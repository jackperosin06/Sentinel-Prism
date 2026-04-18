"""Public web search tool types (Story 3.7)."""

from __future__ import annotations

from typing import Protocol, TypedDict, runtime_checkable


class WebSearchSnippet(TypedDict, total=False):
    """Normalized search hit for LLM context (vendor-agnostic)."""

    title: str
    url: str
    snippet: str


@runtime_checkable
class SearchToolProtocol(Protocol):
    """Pluggable async search (Tavily, DuckDuckGo-style, stubs, …)."""

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
    ) -> list[WebSearchSnippet]: ...
