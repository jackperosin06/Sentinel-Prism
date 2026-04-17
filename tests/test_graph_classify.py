"""Classify node and graph wiring (Story 3.4)."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sentinel_prism.db.models import FallbackMode, SourceType
from sentinel_prism.graph import compile_regulatory_pipeline_graph, new_pipeline_state
from sentinel_prism.graph.nodes.classify import node_classify
from sentinel_prism.services.connectors.scout_raw_item import ScoutRawItem
from sentinel_prism.services.llm.classification import (
    StructuredClassification,
    build_classification_llm,
)


def _fake_row() -> SimpleNamespace:
    return SimpleNamespace(
        enabled=True,
        source_type=SourceType.RSS,
        primary_url="https://ex.test/f",
        fallback_url=None,
        fallback_mode=FallbackMode.NONE,
        name="Src",
        jurisdiction="EU",
    )


def _session_factory_patch() -> MagicMock:
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=MagicMock())
    cm.__aexit__ = AsyncMock(return_value=None)
    return MagicMock(return_value=cm)


def _normalized_stub(**overrides: object) -> dict:
    base = {
        "source_id": str(uuid.uuid4()),
        "item_url": "https://n/1",
        "jurisdiction": "EU",
        "document_type": "unknown",
        "title": "Label change",
        "summary": None,
        "body_snippet": None,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_node_classify_out_of_scope_skips_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*_a: object, **_k: object) -> StructuredClassification:
        raise AssertionError("LLM must not run when rules reject")

    monkeypatch.setattr(
        "sentinel_prism.graph.nodes.classify.build_classification_llm",
        lambda: type(
            "X",
            (),
            {"classify": staticmethod(boom)},
        )(),
    )

    state = new_pipeline_state(uuid.uuid4())
    state["normalized_updates"] = [
        _normalized_stub(jurisdiction="ZZ-REJECT", title="x"),
    ]
    out = await node_classify(state)
    assert len(out["classifications"]) == 1
    row = out["classifications"][0]
    assert row["in_scope"] is False
    assert row["rationale"] == "rules_rejected"
    assert row["severity"] is None
    assert row["urgency"] is None


@pytest.mark.asyncio
async def test_node_classify_llm_error_emits_placeholder_row(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)

    class BoomLlm:
        model_id = "stub"

        async def classify(self, *_a: object, **_k: object) -> StructuredClassification:
            raise RuntimeError("provider down")

    with patch(
        "sentinel_prism.graph.nodes.classify.build_classification_llm",
        return_value=BoomLlm(),
    ):
        state = new_pipeline_state(uuid.uuid4())
        state["normalized_updates"] = [_normalized_stub()]
        out = await node_classify(state)

    assert out["errors"][0]["message"] == "llm_error"
    assert out["errors"][0]["step"] == "classify"
    assert len(out["classifications"]) == 1
    row = out["classifications"][0]
    assert row["in_scope"] is True
    assert row["severity"] is None
    assert row["urgency"] is None
    assert row["impact_categories"] == []
    assert row["rationale"] == "llm_error"
    assert row["confidence"] == 0.0
    assert row["needs_human_review"] is True
    assert out["flags"]["needs_human_review"] is True
    assert out.get("llm_trace", {}).get("status") == "all_failed"
    err_ev = [r for r in caplog.records if getattr(r, "event", None) == "graph_classify_llm_error"]
    assert len(err_ev) == 1
    assert err_ev[0].ctx["run_id"] == state["run_id"]


@pytest.mark.asyncio
async def test_node_classify_skips_non_mapping_items(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _never(*_a: object, **_k: object) -> StructuredClassification:
        raise AssertionError("LLM must not run for non-mapping items")

    monkeypatch.setattr(
        "sentinel_prism.graph.nodes.classify.build_classification_llm",
        lambda: type("X", (), {"model_id": "stub", "classify": staticmethod(_never)})(),
    )

    state = new_pipeline_state(uuid.uuid4())
    state["normalized_updates"] = ["bad-item"]  # type: ignore[list-item]
    out = await node_classify(state)
    assert out["classifications"] == []
    assert out["errors"][0]["step"] == "classify"
    assert out["errors"][0]["error_class"] == "TypeError"
    assert out["errors"][0]["message"] == "normalized_update_not_a_mapping"
    # Schema: ``llm_trace`` is always emitted when the node processed items; the
    # ``no_attempt`` status signals that no LLM call was actually made.
    assert out["llm_trace"]["status"] == "no_attempt"
    assert out["llm_trace"]["last_node"] == "classify"


class _BenignStubLlm:
    model_id = "stub"

    async def classify(self, *_a: object, **_k: object) -> StructuredClassification:
        return StructuredClassification(
            severity="medium",
            impact_categories=["labeling"],
            urgency="informational",
            rationale="stub_llm",
            confidence=0.85,
        )


@pytest.mark.asyncio
async def test_node_classify_handles_flags_set_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "sentinel_prism.graph.nodes.classify.build_classification_llm",
        lambda: _BenignStubLlm(),
    )

    state = new_pipeline_state(uuid.uuid4())
    state["flags"] = None  # type: ignore[typeddict-item]
    state["normalized_updates"] = [_normalized_stub()]
    out = await node_classify(state)
    assert len(out["classifications"]) == 1


@pytest.mark.asyncio
async def test_needs_human_review_triggers_on_low_confidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LowConf:
        model_id = "stub"

        async def classify(self, *_a: object, **_k: object) -> StructuredClassification:
            return StructuredClassification(
                severity="low",
                impact_categories=["labeling"],
                urgency="informational",
                rationale="uncertain",
                confidence=0.2,
            )

    monkeypatch.setattr(
        "sentinel_prism.graph.nodes.classify.build_classification_llm",
        lambda: LowConf(),
    )

    state = new_pipeline_state(uuid.uuid4())
    state["normalized_updates"] = [_normalized_stub()]
    out = await node_classify(state)
    assert out["classifications"][0]["needs_human_review"] is True
    assert out["flags"]["needs_human_review"] is True


@pytest.mark.asyncio
async def test_needs_human_review_triggers_on_critical_severity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Crit:
        model_id = "stub"

        async def classify(self, *_a: object, **_k: object) -> StructuredClassification:
            return StructuredClassification(
                severity="critical",
                impact_categories=["safety"],
                urgency="immediate",
                rationale="recall",
                confidence=0.95,
            )

    monkeypatch.setattr(
        "sentinel_prism.graph.nodes.classify.build_classification_llm",
        lambda: Crit(),
    )

    state = new_pipeline_state(uuid.uuid4())
    state["normalized_updates"] = [_normalized_stub()]
    out = await node_classify(state)
    assert out["classifications"][0]["needs_human_review"] is True
    assert out["flags"]["needs_human_review"] is True


@pytest.mark.asyncio
async def test_needs_human_review_stays_false_on_benign_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Benign:
        model_id = "stub"

        async def classify(self, *_a: object, **_k: object) -> StructuredClassification:
            return StructuredClassification(
                severity="medium",
                impact_categories=["labeling"],
                urgency="informational",
                rationale="routine",
                confidence=0.9,
            )

    monkeypatch.setattr(
        "sentinel_prism.graph.nodes.classify.build_classification_llm",
        lambda: Benign(),
    )

    state = new_pipeline_state(uuid.uuid4())
    state["normalized_updates"] = [_normalized_stub()]
    out = await node_classify(state)
    assert out["classifications"][0]["needs_human_review"] is False
    assert "flags" not in out or not out["flags"].get("needs_human_review")


@pytest.mark.asyncio
async def test_graph_classify_matches_normalized_count() -> None:
    run_id = str(uuid.uuid4())
    source_id = str(uuid.uuid4())
    row = _fake_row()
    i1 = ScoutRawItem(
        source_id=uuid.UUID(source_id),
        item_url="https://a/1",
        fetched_at=datetime.now(timezone.utc),
        title="One",
    )
    i2 = ScoutRawItem(
        source_id=uuid.UUID(source_id),
        item_url="https://a/2",
        fetched_at=datetime.now(timezone.utc),
        title="Two",
    )
    factory = _session_factory_patch()
    graph = compile_regulatory_pipeline_graph()
    config = {"configurable": {"thread_id": run_id}}

    with (
        patch(
            "sentinel_prism.graph.nodes.scout.get_session_factory",
            return_value=factory,
        ),
        patch(
            "sentinel_prism.graph.nodes.normalize.get_session_factory",
            return_value=factory,
        ),
        patch(
            "sentinel_prism.graph.nodes.scout.sources_repo.get_source_by_id",
            new_callable=AsyncMock,
            return_value=row,
        ),
        patch(
            "sentinel_prism.graph.nodes.normalize.sources_repo.get_source_by_id",
            new_callable=AsyncMock,
            return_value=row,
        ),
        patch(
            "sentinel_prism.graph.nodes.scout.fetch_scout_items_with_fallback",
            new_callable=AsyncMock,
            return_value=([i1, i2], "primary"),
        ),
    ):
        out = await graph.ainvoke(
            new_pipeline_state(run_id, source_id=source_id),
            config,
        )

    assert len(out["normalized_updates"]) == 2
    assert len(out["classifications"]) == 2
    for nu, cl in zip(out["normalized_updates"], out["classifications"], strict=True):
        assert cl["source_id"] == nu["source_id"]
        assert cl["item_url"] == nu["item_url"]
        assert cl["in_scope"] is True
        assert cl["severity"] == "medium"


@pytest.mark.asyncio
async def test_build_classification_llm_returns_stub_without_key() -> None:
    llm = build_classification_llm()
    out = await llm.classify(
        _normalized_stub(),
        model_id="stub",
        prompt_version="mvp-1",
    )
    assert out.rationale == "stub_llm"
