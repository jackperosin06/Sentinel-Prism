"""Human review interrupt + Command(resume) (Story 4.2)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.types import Command

from sentinel_prism.db.models import FallbackMode, SourceType
from sentinel_prism.graph import compile_regulatory_pipeline_graph, new_pipeline_state
from sentinel_prism.graph.checkpoints import dev_memory_checkpointer
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


@pytest.mark.asyncio
async def test_resume_approve_clears_needs_human_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    recorded: list[dict[str, object]] = []

    async def capture(**kwargs: object) -> None:
        recorded.append(dict(kwargs))

    monkeypatch.setattr(
        "sentinel_prism.graph.nodes.human_review_gate.record_review_queue_projection",
        capture,
    )

    run_id = str(uuid.uuid4())
    source_id = str(uuid.uuid4())
    row = _fake_row()
    item = ScoutRawItem(
        source_id=uuid.UUID(source_id),
        item_url="https://news.example/resume-approve",
        fetched_at=datetime.now(timezone.utc),
        title="T",
    )
    factory = _session_factory_patch()
    shared = dev_memory_checkpointer()
    graph = compile_regulatory_pipeline_graph(checkpointer=shared)
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

    assert out.get("__interrupt__")
    snap_mid = await graph.aget_state(config)
    assert snap_mid.values.get("flags", {}).get("needs_human_review") is True

    await graph.ainvoke(
        Command(resume={"decision": "approve", "note": "", "overrides": []}),
        config,
    )

    snap = await graph.aget_state(config)
    assert snap.values.get("flags", {}).get("needs_human_review") is False
    cls_rows = snap.values.get("classifications") or []
    assert cls_rows
    assert all(not r.get("needs_human_review") for r in cls_rows if isinstance(r, dict))


@pytest.mark.asyncio
async def test_resume_reject_marks_out_of_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    monkeypatch.setattr(
        "sentinel_prism.graph.nodes.human_review_gate.record_review_queue_projection",
        AsyncMock(),
    )

    run_id = str(uuid.uuid4())
    source_id = str(uuid.uuid4())
    row = _fake_row()
    item_url = "https://news.example/resume-reject"
    item = ScoutRawItem(
        source_id=uuid.UUID(source_id),
        item_url=item_url,
        fetched_at=datetime.now(timezone.utc),
        title="T",
    )
    factory = _session_factory_patch()
    shared = dev_memory_checkpointer()
    graph = compile_regulatory_pipeline_graph(checkpointer=shared)
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
        await graph.ainvoke(
            new_pipeline_state(run_id, source_id=source_id),
            config,
        )

    await graph.ainvoke(
        Command(
            resume={
                "decision": "reject",
                "note": "not relevant",
                "overrides": [],
            }
        ),
        config,
    )

    snap = await graph.aget_state(config)
    cls_rows = [r for r in (snap.values.get("classifications") or []) if isinstance(r, dict)]
    assert cls_rows
    row0 = cls_rows[0]
    assert row0.get("in_scope") is False
    assert row0.get("rationale") == "analyst_rejected"
    assert snap.values.get("flags", {}).get("needs_human_review") is False


@pytest.mark.asyncio
async def test_resume_override_updates_severity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    monkeypatch.setattr(
        "sentinel_prism.graph.nodes.human_review_gate.record_review_queue_projection",
        AsyncMock(),
    )

    run_id = str(uuid.uuid4())
    source_id = str(uuid.uuid4())
    row = _fake_row()
    item_url = "https://news.example/resume-override"
    item = ScoutRawItem(
        source_id=uuid.UUID(source_id),
        item_url=item_url,
        fetched_at=datetime.now(timezone.utc),
        title="T",
    )
    factory = _session_factory_patch()
    shared = dev_memory_checkpointer()
    graph = compile_regulatory_pipeline_graph(checkpointer=shared)
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
        await graph.ainvoke(
            new_pipeline_state(run_id, source_id=source_id),
            config,
        )

    await graph.ainvoke(
        Command(
            resume={
                "decision": "override",
                "note": "downgrade",
                "overrides": [
                    {
                        "item_url": item_url,
                        "severity": "low",
                        "confidence": 0.92,
                    }
                ],
            }
        ),
        config,
    )

    snap = await graph.aget_state(config)
    cls_rows = [r for r in (snap.values.get("classifications") or []) if isinstance(r, dict)]
    assert cls_rows[0].get("severity") == "low"
    assert cls_rows[0].get("confidence") == 0.92
    assert cls_rows[0].get("needs_human_review") is False
    assert snap.values.get("flags", {}).get("needs_human_review") is False
