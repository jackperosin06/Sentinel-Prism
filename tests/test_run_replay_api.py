"""Workflow replay API (Story 8.2)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from sentinel_prism.api.deps import get_current_user
from sentinel_prism.db.models import FallbackMode, SourceType, User, UserRole
from sentinel_prism.graph import compile_regulatory_pipeline_graph, new_pipeline_state
from sentinel_prism.graph.checkpoints import dev_memory_checkpointer
from sentinel_prism.main import create_app
from sentinel_prism.services.connectors.scout_raw_item import ScoutRawItem
from sentinel_prism.services.llm.classification import StructuredClassification


pytestmark = pytest.mark.graph_db_stubbed


def _analyst_user() -> User:
    return User(
        id=uuid.uuid4(),
        email="analyst@test.local",
        password_hash="x",
        role=UserRole.ANALYST,
        is_active=True,
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


class _LowConf:
    model_id = "stub"

    async def classify(self, *_a: object, **_k: object) -> StructuredClassification:
        return StructuredClassification(
            severity="medium",
            impact_categories=["labeling"],
            urgency="informational",
            rationale="uncertain",
            confidence=0.2,
        )


@pytest.mark.asyncio
async def test_replay_unauthorized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")
    app = create_app()
    transport = ASGITransport(app=app)
    async with LifespanManager(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(f"/runs/{uuid.uuid4()}/replay", json={"from_node": "classify", "to_node": "route"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_replay_requires_persistent_checkpointer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("PIPELINE_CHECKPOINTER", "memory")

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _analyst_user()
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.post(
                    f"/runs/{uuid.uuid4()}/replay",
                    json={"from_node": "classify", "to_node": "route"},
                )
        assert r.status_code in (409, 503)
        assert "checkpointer" in r.json()["detail"].lower() or "checkpoint" in r.json()["detail"].lower()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_replay_runs_from_checkpoint_is_non_destructive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay uses checkpoint as input and suppresses side effects."""

    # AC #5 gate is config-driven in production, but unit tests shouldn't try to
    # connect a real Postgres checkpointer during app lifespan.
    monkeypatch.setattr(
        "sentinel_prism.api.routes.runs.use_postgres_pipeline_checkpointer",
        lambda: True,
    )
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

    # Force classify to take the human-review branch (so we cover gate behaviour),
    # but replay must not project to review queue nor interrupt.
    monkeypatch.setattr(
        "sentinel_prism.graph.nodes.classify.build_classification_llm",
        lambda: _LowConf(),
    )

    run_id = str(uuid.uuid4())
    source_id = str(uuid.uuid4())
    row = _fake_row()
    item = ScoutRawItem(
        source_id=uuid.UUID(source_id),
        item_url="https://news.example/replay",
        fetched_at=datetime.now(timezone.utc),
        title="Replay me",
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
    assert out.get("__interrupt__")  # confirms checkpoint exists

    # Side effects that MUST NOT run during replay.
    record_projection = AsyncMock()
    record_audit = AsyncMock(return_value=[])
    process_deliveries = AsyncMock(return_value=([], []))
    upsert_brief = AsyncMock(return_value=(uuid.uuid4(), True))

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _analyst_user()
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            app.state.regulatory_graph = graph
            with (
                patch(
                    "sentinel_prism.graph.pipeline_review.record_review_queue_projection",
                    record_projection,
                ),
                patch(
                    "sentinel_prism.graph.pipeline_audit.record_pipeline_audit_event",
                    record_audit,
                ),
                patch(
                    "sentinel_prism.services.notifications.scheduling.process_routed_notification_deliveries",
                    process_deliveries,
                ),
                patch(
                    "sentinel_prism.db.repositories.briefings.upsert_briefing_for_run",
                    upsert_brief,
                ),
            ):
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    r = await client.post(
                        f"/runs/{run_id}/replay",
                        json={"from_node": "classify", "to_node": "route"},
                    )
        assert r.status_code == 200
        body = r.json()
        assert body["original_run_id"] == run_id
        assert uuid.UUID(body["replay_run_id"])  # valid UUID
        assert body["replay_run_id"] != run_id
        assert body["replayed_nodes"] == ["classify", "human_review_gate", "brief", "route"]
        assert body["status"] in ("completed", "partial")

        # Replay must suppress side-effect writers.
        record_projection.assert_not_awaited()
        record_audit.assert_not_awaited()
        process_deliveries.assert_not_awaited()
        upsert_brief.assert_not_awaited()

        # Original checkpoint must remain intact.
        snap = await graph.aget_state(config)
        assert snap.values.get("run_id") == run_id
    finally:
        app.dependency_overrides.clear()

