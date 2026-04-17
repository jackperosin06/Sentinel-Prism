"""Unit tests for graph scout/normalize nodes (Story 3.3)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sentinel_prism.db.models import FallbackMode, SourceType
from sentinel_prism.graph import compile_regulatory_pipeline_graph, new_pipeline_state
from sentinel_prism.graph.nodes.normalize import node_normalize
from sentinel_prism.graph.nodes.scout import node_scout
from sentinel_prism.db.repositories import captures as captures_repo
from sentinel_prism.services.connectors.scout_raw_item import ScoutRawItem


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


@pytest.mark.asyncio
async def test_node_scout_missing_source_id_appends_error() -> None:
    state = new_pipeline_state(uuid.uuid4())
    out = await node_scout(state)
    assert out["errors"][0]["message"] == "source_id_required"
    assert "raw_items" not in out or out.get("raw_items") is None


@pytest.mark.asyncio
async def test_node_normalize_empty_raw_returns_empty_partial() -> None:
    factory = _session_factory_patch()
    row = _fake_row()
    run_id = str(uuid.uuid4())
    sid = str(uuid.uuid4())
    state = new_pipeline_state(run_id, source_id=sid)
    state["raw_items"] = []

    with (
        patch("sentinel_prism.graph.nodes.normalize.get_session_factory", return_value=factory),
        patch(
            "sentinel_prism.graph.nodes.normalize.sources_repo.get_source_by_id",
            new_callable=AsyncMock,
            return_value=row,
        ),
    ):
        out = await node_normalize(state)

    assert out == {}


@pytest.mark.asyncio
async def test_graph_multi_item_fetch_exercises_list_reducers() -> None:
    """Scout/normalize partial updates append multi-element lists (operator.add channels)."""

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

    assert len(out["raw_items"]) == 2
    assert len(out["normalized_updates"]) == 2
    assert {out["raw_items"][0]["item_url"], out["raw_items"][1]["item_url"]} == {
        "https://a/1",
        "https://a/2",
    }


@pytest.mark.asyncio
async def test_scout_serializes_with_scout_raw_item_payload() -> None:
    """Raw items in state match captures repo payload (Story 3.1 alignment)."""

    run_id = str(uuid.uuid4())
    source_id = str(uuid.uuid4())
    row = _fake_row()
    item = ScoutRawItem(
        source_id=uuid.UUID(source_id),
        item_url="https://z/z",
        fetched_at=datetime.now(timezone.utc),
        title="Z",
    )
    factory = _session_factory_patch()
    expected = captures_repo.scout_raw_item_payload(item)

    with (
        patch("sentinel_prism.graph.nodes.scout.get_session_factory", return_value=factory),
        patch(
            "sentinel_prism.graph.nodes.scout.sources_repo.get_source_by_id",
            new_callable=AsyncMock,
            return_value=row,
        ),
        patch(
            "sentinel_prism.graph.nodes.scout.fetch_scout_items_with_fallback",
            new_callable=AsyncMock,
            return_value=([item], "primary"),
        ),
    ):
        out = await node_scout(new_pipeline_state(run_id, source_id=source_id))

    assert out["raw_items"] == [expected]
