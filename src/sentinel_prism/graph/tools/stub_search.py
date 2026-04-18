"""Offline search tool implementations for CI and tests (Story 3.7)."""

from __future__ import annotations

from sentinel_prism.graph.tools.types import WebSearchSnippet


class NullWebSearchTool:
    """No-op adapter: never calls the network."""

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
    ) -> list[WebSearchSnippet]:
        _ = query, max_results
        return []


class StubWebSearchTool:
    """Deterministic hits for tests (ignores query text)."""

    def __init__(self, snippets: list[WebSearchSnippet] | None = None) -> None:
        # Copy the input list so post-construction mutation by the caller does
        # not bleed into subsequent ``search()`` calls / other tests.
        self._snippets: list[WebSearchSnippet] = (
            list(snippets)
            if snippets
            else [
                {
                    "title": "Stub hit",
                    "url": "https://example.com/stub",
                    "snippet": "Fixture snippet for tests.",
                }
            ]
        )

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
    ) -> list[WebSearchSnippet]:
        _ = query
        return list(self._snippets[: max(0, max_results)])
