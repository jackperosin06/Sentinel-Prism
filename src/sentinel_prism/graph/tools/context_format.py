"""Format search snippets for optional LLM user-message enrichment (Story 3.7)."""

from __future__ import annotations

from typing import Mapping

from sentinel_prism.graph.tools.types import WebSearchSnippet


def format_web_context_for_llm(snippets: list[WebSearchSnippet]) -> str:
    """Human-readable block appended to the classification user message."""

    if not snippets:
        return ""

    # Defensive parsing: the ``WebSearchSnippet`` TypedDict is advisory, not
    # enforced at runtime. Skip non-mapping entries and hits that carry no
    # usable ``title``/``url``/``snippet`` — header-only noise would waste
    # LLM tokens and could bias classification toward an empty web context.
    usable: list[tuple[str, str, str]] = []
    for hit in snippets:
        if not isinstance(hit, Mapping):
            continue
        title = str(hit.get("title") or "").strip()
        url = str(hit.get("url") or "").strip()
        snip = str(hit.get("snippet") or "").strip()
        if not (title or url or snip):
            continue
        usable.append((title, url, snip))

    if not usable:
        return ""

    lines: list[str] = [
        "--- public web context (supplementary; may be incomplete) ---",
    ]
    for i, (title, url, snip) in enumerate(usable, start=1):
        display_title = title or "(no title)"
        if len(snip) > 500:
            snip = snip[:500] + "…"
        lines.append(f"{i}. {display_title}")
        if url:
            lines.append(f"   URL: {url}")
        if snip:
            lines.append(f"   {snip}")
    return "\n".join(lines)
