"""Pluggable web search tool (Story 3.7)."""

from __future__ import annotations

import logging
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sentinel_prism.graph.nodes.classify import node_classify
from sentinel_prism.graph.state import new_pipeline_state
from sentinel_prism.graph.tools import (
    NullWebSearchTool,
    StubWebSearchTool,
    build_public_web_search_query,
    create_web_search_tool,
    format_web_context_for_llm,
    normalized_keys_outside_allowlist,
)
from sentinel_prism.graph.tools.types import WebSearchSnippet
from sentinel_prism.services.llm.classification import StructuredClassification
from sentinel_prism.services.search.settings import get_web_search_settings


def test_build_public_query_allowlist_ignores_internal_fields() -> None:
    row = {
        "title": "Guidance update",
        "summary": "EU label",
        "tenant_internal_note": "SECRET",
        "api_key": "sk-live",
        "item_url": "https://ema.europa.eu/x",
    }
    assert "SECRET" not in build_public_web_search_query(row)
    assert "sk-live" not in build_public_web_search_query(row)
    assert "Guidance" in build_public_web_search_query(row)
    assert "ema.europa.eu" in build_public_web_search_query(row)
    assert "tenant_internal_note" in normalized_keys_outside_allowlist(row)


@pytest.mark.asyncio
async def test_null_search_tool_returns_empty() -> None:
    tool = NullWebSearchTool()
    assert await tool.search("anything", max_results=3) == []


@pytest.mark.asyncio
async def test_stub_search_tool_respects_max_results() -> None:
    hits: list[WebSearchSnippet] = [
        {"title": "a", "url": "https://a", "snippet": "1"},
        {"title": "b", "url": "https://b", "snippet": "2"},
    ]
    tool = StubWebSearchTool(hits)
    out = await tool.search("q", max_results=1)
    assert len(out) == 1 and out[0]["title"] == "a"


def test_format_web_context_for_llm_non_empty() -> None:
    text = format_web_context_for_llm(
        [{"title": "T", "url": "https://u", "snippet": "S"}]
    )
    assert "public web context" in text
    assert "https://u" in text


@pytest.mark.asyncio
async def test_create_web_search_tool_disabled_is_null(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SENTINEL_WEB_SEARCH_ENABLED", raising=False)
    tool = create_web_search_tool()
    assert await tool.search("x") == []


@pytest.mark.asyncio
async def test_create_web_search_tool_enabled_without_key_is_null(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    monkeypatch.setenv("SENTINEL_WEB_SEARCH_ENABLED", "1")
    monkeypatch.delenv("SENTINEL_TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    tool = create_web_search_tool()
    assert await tool.search("x") == []
    assert any(
        getattr(r, "event", None) == "web_search_enabled_missing_api_key"
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_node_classify_enrichment_off_no_web_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SENTINEL_WEB_SEARCH_ENABLED", raising=False)

    captured: dict[str, str | None] = {}

    class CaptureLlm:
        model_id = "stub"

        async def classify(
            self,
            *_a: object,
            web_context: str | None = None,
            **_k: object,
        ) -> StructuredClassification:
            captured["web_context"] = web_context
            return StructuredClassification(
                severity="medium",
                impact_categories=["labeling"],
                urgency="informational",
                rationale="ok",
                confidence=0.9,
            )

    monkeypatch.setattr(
        "sentinel_prism.graph.nodes.classify.build_classification_llm",
        lambda: CaptureLlm(),
    )
    state = new_pipeline_state(uuid.uuid4())
    state["normalized_updates"] = [
        {
            "source_id": str(uuid.uuid4()),
            "item_url": "https://n/1",
            "jurisdiction": "EU",
            "document_type": "guidance",
            "title": "Safety communication",
            "summary": None,
            "body_snippet": None,
        }
    ]
    out = await node_classify(state)
    assert captured.get("web_context") is None
    assert "web_search" not in out.get("llm_trace", {})


@pytest.mark.asyncio
async def test_node_classify_injected_stub_passes_web_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SENTINEL_WEB_SEARCH_ENABLED", raising=False)

    captured: dict[str, str | None] = {}

    class CaptureLlm:
        model_id = "stub"

        async def classify(
            self,
            *_a: object,
            web_context: str | None = None,
            **_k: object,
        ) -> StructuredClassification:
            captured["web_context"] = web_context
            return StructuredClassification(
                severity="medium",
                impact_categories=["labeling"],
                urgency="informational",
                rationale="ok",
                confidence=0.9,
            )

    monkeypatch.setattr(
        "sentinel_prism.graph.nodes.classify.build_classification_llm",
        lambda: CaptureLlm(),
    )
    state = new_pipeline_state(uuid.uuid4())
    state["normalized_updates"] = [
        {
            "source_id": str(uuid.uuid4()),
            "item_url": "https://n/1",
            "jurisdiction": "EU",
            "document_type": "guidance",
            "title": "Safety communication",
            "summary": None,
            "body_snippet": None,
        }
    ]
    stub = StubWebSearchTool(
        [{"title": "Hit", "url": "https://ex", "snippet": "More info"}]
    )
    out = await node_classify(state, _web_search_tool=stub)
    wc = captured.get("web_context") or ""
    assert "public web context" in wc
    assert "https://ex" in wc
    assert out["llm_trace"]["web_search"]["attempts"] == 1
    assert out["llm_trace"]["web_search"]["errors"] == 0


@pytest.mark.asyncio
async def test_node_classify_web_search_error_continue_without_context(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    monkeypatch.delenv("SENTINEL_WEB_SEARCH_ENABLED", raising=False)

    captured: dict[str, str | None] = {}

    class CaptureLlm:
        model_id = "stub"

        async def classify(
            self,
            *_a: object,
            web_context: str | None = None,
            **_k: object,
        ) -> StructuredClassification:
            captured["web_context"] = web_context
            return StructuredClassification(
                severity="low",
                impact_categories=["other"],
                urgency="informational",
                rationale="ok",
                confidence=0.8,
            )

    class BoomSearch:
        async def search(self, *_a: object, **_k: object) -> list[WebSearchSnippet]:
            raise ConnectionError("tavily down")

    monkeypatch.setattr(
        "sentinel_prism.graph.nodes.classify.build_classification_llm",
        lambda: CaptureLlm(),
    )
    state = new_pipeline_state(uuid.uuid4())
    state["normalized_updates"] = [
        {
            "source_id": str(uuid.uuid4()),
            "item_url": "https://n/1",
            "jurisdiction": "EU",
            "document_type": "x",
            "title": "T",
            "summary": None,
            "body_snippet": None,
        }
    ]
    out = await node_classify(state, _web_search_tool=BoomSearch())
    assert captured.get("web_context") is None
    assert out["llm_trace"]["web_search"]["errors"] == 1
    assert out["errors"][0]["step"] == "classify_web_search"
    assert any(
        getattr(r, "event", None) == "graph_classify_web_search_error"
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_tavily_adapter_parses_results() -> None:
    from sentinel_prism.graph.tools.tavily_search import TavilyWebSearch

    mock_client = MagicMock()
    mock_client.search = AsyncMock(
        return_value={
            "results": [
                {"title": "A", "url": "https://a", "content": "body"},
            ]
        }
    )
    mock_client.close = AsyncMock()

    with patch(
        "tavily.AsyncTavilyClient",
        return_value=mock_client,
    ):
        tool = TavilyWebSearch(api_key="k", default_max_results=3, timeout=10.0)
        hits = await tool.search("query")

    assert len(hits) == 1
    assert hits[0]["snippet"] == "body"
    mock_client.close.assert_awaited_once()


def test_get_web_search_settings_max_results_clamp_warns(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    monkeypatch.setenv("SENTINEL_WEB_SEARCH_MAX_RESULTS", "99")
    s = get_web_search_settings()
    assert s.max_results == 15
    assert any(
        getattr(r, "event", None) == "web_search_max_results_clamped"
        for r in caplog.records
    )
