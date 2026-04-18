"""LangGraph pipeline: AgentState, scout/normalize, checkpoint, logging (Story 3.3)."""

from __future__ import annotations

import logging
import operator
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sentinel_prism.db.models import FallbackMode, SourceType
from sentinel_prism.graph import (
    compile_regulatory_pipeline_graph,
    dev_memory_checkpointer,
    new_pipeline_state,
)
from sentinel_prism.graph.graph import build_regulatory_pipeline_graph
from sentinel_prism.services.connectors.scout_raw_item import ScoutRawItem


def _fake_source_row() -> SimpleNamespace:
    return SimpleNamespace(
        enabled=True,
        source_type=SourceType.RSS,
        primary_url="https://example.com/feed.xml",
        fallback_url=None,
        fallback_mode=FallbackMode.NONE,
        name="Test Source",
        jurisdiction="US-CA",
    )


def _patch_session_factory() -> MagicMock:
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=MagicMock())
    session_cm.__aexit__ = AsyncMock(return_value=None)
    factory = MagicMock(return_value=session_cm)
    return factory


@pytest.mark.asyncio
async def test_compile_pipeline_round_trip_checkpoint() -> None:
    cp = dev_memory_checkpointer()
    graph = compile_regulatory_pipeline_graph(checkpointer=cp)
    run_id = str(uuid.uuid4())
    source_id = str(uuid.uuid4())
    row = _fake_source_row()
    item = ScoutRawItem(
        source_id=uuid.UUID(source_id),
        item_url="https://news.example/a",
        fetched_at=datetime.now(timezone.utc),
        title="Hello",
    )
    factory = _patch_session_factory()

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
        state = new_pipeline_state(run_id, tenant_id="tenant-a", source_id=source_id)
        config = {"configurable": {"thread_id": run_id}}
        out = await graph.ainvoke(state, config)
        snap = await graph.aget_state(config)

    assert out["run_id"] == run_id
    assert out["tenant_id"] == "tenant-a"
    assert len(out["raw_items"]) == 1
    assert out["raw_items"][0]["item_url"] == item.item_url
    assert len(out["normalized_updates"]) == 1
    assert out["normalized_updates"][0]["title"] == "Hello"
    assert out["normalized_updates"][0]["source_name"] == "Test Source"
    assert len(out["classifications"]) == 1
    clf0 = out["classifications"][0]
    assert clf0["in_scope"] is True
    assert clf0["item_url"] == item.item_url
    assert clf0["source_id"] == source_id
    assert clf0["needs_human_review"] is False
    assert clf0["severity"] == "medium"
    assert clf0["confidence"] == pytest.approx(0.85)
    assert out.get("briefings")
    assert int(out["briefings"][0].get("group_count", 0)) >= 1
    assert snap.values["run_id"] == run_id
    assert len(snap.values["normalized_updates"]) == 1
    assert len(snap.values["classifications"]) == 1


@pytest.mark.asyncio
async def test_pipeline_logs_run_id_on_scout_and_normalize(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    graph = compile_regulatory_pipeline_graph()
    run_id = str(uuid.uuid4())
    source_id = str(uuid.uuid4())
    row = _fake_source_row()
    item = ScoutRawItem(
        source_id=uuid.UUID(source_id),
        item_url="https://news.example/b",
        fetched_at=datetime.now(timezone.utc),
        title="T2",
    )
    factory = _patch_session_factory()

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
        await graph.ainvoke(
            new_pipeline_state(run_id, source_id=source_id),
            {"configurable": {"thread_id": run_id}},
        )

    scout_done = [
        r for r in caplog.records if getattr(r, "event", None) == "graph_scout_fetched"
    ]
    norm_done = [
        r for r in caplog.records if getattr(r, "event", None) == "graph_normalize_done"
    ]
    clf_done = [
        r for r in caplog.records if getattr(r, "event", None) == "graph_classify_llm_done"
    ]
    assert len(scout_done) == 1
    assert scout_done[0].ctx["run_id"] == run_id
    assert len(norm_done) == 1
    assert norm_done[0].ctx["run_id"] == run_id
    assert len(clf_done) == 1
    assert clf_done[0].ctx["run_id"] == run_id
    assert clf_done[0].ctx["model_id"] == "stub"
    assert "prompt_version" in clf_done[0].ctx


def test_new_pipeline_state_normalizes_uuid() -> None:
    u = uuid.uuid4()
    s = new_pipeline_state(u)
    assert s["run_id"] == str(u)
    assert s["raw_items"] == []
    assert s["flags"] == {}


def test_new_pipeline_state_with_source_id() -> None:
    sid = uuid.uuid4()
    s = new_pipeline_state(uuid.uuid4(), source_id=sid)
    assert s["source_id"] == str(sid)


def test_new_pipeline_state_rejects_empty_run_id() -> None:
    with pytest.raises(ValueError):
        new_pipeline_state("")
    with pytest.raises(ValueError):
        new_pipeline_state("   ")


def test_new_pipeline_state_rejects_non_string_non_uuid_run_id() -> None:
    with pytest.raises(TypeError):
        new_pipeline_state(42)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        new_pipeline_state(None)  # type: ignore[arg-type]


def test_build_regulatory_pipeline_graph_takes_no_kwargs() -> None:
    """Builder has no checkpointer kwarg — persistence is attached at compile.

    Enforce the contract both ways: passing ``checkpointer=`` to the builder
    must raise ``TypeError`` (it belongs on :func:`compile_regulatory_pipeline_graph`),
    and the no-arg call must still produce a compilable builder.
    """

    with pytest.raises(TypeError):
        build_regulatory_pipeline_graph(  # type: ignore[call-arg]
            checkpointer=dev_memory_checkpointer(),
        )

    builder = build_regulatory_pipeline_graph()
    g = builder.compile(checkpointer=dev_memory_checkpointer())
    assert g is not None


def test_list_channels_annotated_with_operator_add() -> None:
    """AC #4: every append-merged channel on ``AgentState`` uses ``operator.add``.

    This inspects the actual ``TypedDict`` annotations rather than asserting
    stdlib ``operator.add`` behaviour, so a future channel redefinition that
    silently drops the reducer is caught before a node emits partial updates.
    See ``test_graph_scout_normalize.test_graph_multi_item_fetch_exercises_list_reducers``
    for the runtime round-trip covering the same AC.
    """

    import typing

    from sentinel_prism.graph.state import AgentState

    hints = typing.get_type_hints(AgentState, include_extras=True)
    list_channels = (
        "raw_items",
        "normalized_updates",
        "classifications",
        "routing_decisions",
        "briefings",
        "delivery_events",
        "errors",
    )
    for key in list_channels:
        args = typing.get_args(hints[key])
        assert args, f"{key} channel annotation is not an Annotated[...]"
        assert args[1] is operator.add, (
            f"{key} channel must use operator.add reducer, got {args[1]!r}"
        )
