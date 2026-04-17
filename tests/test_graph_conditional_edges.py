"""Conditional edges after classify — review vs continue (Story 3.5)."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sentinel_prism.db.models import FallbackMode, SourceType
from sentinel_prism.graph import compile_regulatory_pipeline_graph, new_pipeline_state
from sentinel_prism.graph.routing import (
    CLASSIFY_NEXT_CONTINUE,
    CLASSIFY_NEXT_HUMAN_REVIEW,
    route_after_classify,
)
from sentinel_prism.services.connectors.scout_raw_item import ScoutRawItem
from sentinel_prism.services.llm.classification import StructuredClassification


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


def _empty_state_with_flags(flags: object) -> dict[str, object]:
    """Builder for router tests.

    Returns a plain ``dict`` rather than a typed ``AgentState`` because the router
    exercises the defensive ``state.get("flags") or {}`` idiom against values that
    deliberately violate the TypedDict contract (``None`` or missing key).
    """

    state: dict[str, object] = {
        "run_id": "r1",
        "raw_items": [],
        "normalized_updates": [],
        "classifications": [],
        "routing_decisions": [],
        "briefings": [],
        "delivery_events": [],
        "errors": [],
    }
    # Sentinel ``...`` means "do not set the key at all" — exercises the
    # ``state.get("flags")`` absent-key branch.
    if flags is not ...:
        state["flags"] = flags
    return state


def test_route_after_classify_uses_flags_only() -> None:
    assert (
        route_after_classify(_empty_state_with_flags({"needs_human_review": True}))
        == CLASSIFY_NEXT_HUMAN_REVIEW
    )
    assert route_after_classify(_empty_state_with_flags({})) == CLASSIFY_NEXT_CONTINUE
    assert (
        route_after_classify(_empty_state_with_flags({"needs_human_review": False}))
        == CLASSIFY_NEXT_CONTINUE
    )


def test_route_after_classify_handles_flags_none_and_absent() -> None:
    # ``flags`` key absent from state: ``state.get("flags")`` returns ``None``; router
    # must treat as continue.
    assert route_after_classify(_empty_state_with_flags(...)) == CLASSIFY_NEXT_CONTINUE
    # ``flags`` explicitly ``None`` (e.g. bad checkpoint merge): same behavior via the
    # ``or {}`` defensive idiom.
    assert route_after_classify(_empty_state_with_flags(None)) == CLASSIFY_NEXT_CONTINUE


@pytest.mark.asyncio
async def test_pipeline_continue_path_no_interrupt(
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

    run_id = str(uuid.uuid4())
    source_id = str(uuid.uuid4())
    row = _fake_row()
    item = ScoutRawItem(
        source_id=uuid.UUID(source_id),
        item_url="https://news.example/c",
        fetched_at=datetime.now(timezone.utc),
        title="Hello",
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
            return_value=([item], "primary"),
        ),
    ):
        out = await graph.ainvoke(
            new_pipeline_state(run_id, source_id=source_id),
            config,
        )

    assert "__interrupt__" not in out
    assert out["run_id"] == run_id
    flags = out.get("flags") or {}
    assert flags.get("needs_human_review", False) is False
    assert out["classifications"][0]["needs_human_review"] is False


@pytest.mark.asyncio
async def test_pipeline_review_path_emits_interrupt(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caplog.set_level(logging.INFO)

    class LowConf:
        model_id = "stub"

        async def classify(self, *_a: object, **_k: object) -> StructuredClassification:
            return StructuredClassification(
                severity="medium",
                impact_categories=["labeling"],
                urgency="informational",
                rationale="uncertain",
                confidence=0.2,
            )

    monkeypatch.setattr(
        "sentinel_prism.graph.nodes.classify.build_classification_llm",
        lambda: LowConf(),
    )

    run_id = str(uuid.uuid4())
    source_id = str(uuid.uuid4())
    row = _fake_row()
    item = ScoutRawItem(
        source_id=uuid.UUID(source_id),
        item_url="https://news.example/r",
        fetched_at=datetime.now(timezone.utc),
        title="Review me",
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
            return_value=([item], "primary"),
        ),
    ):
        out = await graph.ainvoke(
            new_pipeline_state(run_id, source_id=source_id),
            config,
        )

    intr = out.get("__interrupt__")
    assert intr is not None
    assert len(intr) == 1
    val = intr[0].value
    assert val["run_id"] == run_id
    assert val["step"] == "human_review_gate"
    assert val["source_id"] == source_id

    # Tie routing back to classify policy: row-level flag must agree with the aggregate
    # that drove the interrupt branch, not just the top-level ``flags`` channel.
    classifications = out.get("classifications") or []
    assert len(classifications) == 1
    assert classifications[0]["needs_human_review"] is True
    assert out["flags"].get("needs_human_review") is True

    gate_logs = [
        r
        for r in caplog.records
        if getattr(r, "event", None) == "graph_human_review_gate_interrupt"
    ]
    assert len(gate_logs) == 1
    assert gate_logs[0].ctx["run_id"] == run_id


@pytest.mark.asyncio
async def test_node_classify_source_id_uuid_coerced_to_str_on_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Classification join key matches string form when normalized row uses UUID."""

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

    from sentinel_prism.graph.nodes.classify import node_classify

    sid = uuid.uuid4()
    state = new_pipeline_state(uuid.uuid4())
    state["normalized_updates"] = [
        {
            "source_id": sid,
            "item_url": "https://x/y",
            "jurisdiction": "EU",
            "document_type": "guidance",
            "title": "t",
            "summary": None,
            "body_snippet": None,
        }
    ]
    out = await node_classify(state)
    row = out["classifications"][0]
    assert row["source_id"] == str(sid)
    assert row["item_url"] == "https://x/y"
