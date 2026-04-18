"""Review queue, run detail, and resume API (Stories 4.1–4.2)."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from langgraph.types import Command

from sentinel_prism.api.deps import get_current_user
from sentinel_prism.db.models import FallbackMode, SourceType, User, UserRole
from sentinel_prism.db.session import get_db as session_get_db
from sentinel_prism.graph import compile_regulatory_pipeline_graph, new_pipeline_state
from sentinel_prism.graph.checkpoints import dev_memory_checkpointer
from sentinel_prism.main import create_app
from sentinel_prism.services.connectors.scout_raw_item import ScoutRawItem
from sentinel_prism.services.llm.classification import StructuredClassification


def _viewer_user() -> User:
    return User(
        id=uuid.uuid4(),
        email="viewer@test.local",
        password_hash="x",
        role=UserRole.VIEWER,
        is_active=True,
    )


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


# ----------------------------------------------------------------------------
# GET /review-queue — AC #1, AC #4 (RBAC), AC #5 (typed response), AC #7 (auth tests)
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_review_queue_unauthorized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")
    app = create_app()
    transport = ASGITransport(app=app)
    async with LifespanManager(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/review-queue")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_list_review_queue_viewer_forbidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _viewer_user()
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get("/review-queue")
        assert r.status_code == 403
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_list_review_queue_analyst_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

    async def fake_analyst_session() -> None:
        yield MagicMock()

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _analyst_user()
    app.dependency_overrides[session_get_db] = fake_analyst_session
    monkeypatch.setattr(
        "sentinel_prism.db.repositories.review_queue.list_pending_review_items",
        AsyncMock(return_value=[]),
    )
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get("/review-queue")
        assert r.status_code == 200
        assert r.json() == {"items": []}
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_list_review_queue_skips_corrupt_summary_entries(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One bad ``items_summary`` row must not poison the whole listing."""

    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

    async def fake_analyst_session() -> None:
        yield MagicMock()

    run_id = uuid.uuid4()
    queued_at = datetime.now(timezone.utc)
    row = SimpleNamespace(
        run_id=run_id,
        source_id=None,
        queued_at=queued_at,
        items_summary=[
            # Valid row.
            {
                "item_url": "https://ex/ok",
                "in_scope": True,
                "severity": "medium",
                "confidence": 0.4,
                "needs_human_review": True,
                "rationale_excerpt": "why",
                "impact_categories": ["labeling"],
                "urgency": "informational",
            },
            # Corrupt row — ``confidence`` is a list, ``impact_categories``
            # is a dict. Pydantic validation will reject it; the endpoint must
            # skip and still return the valid one.
            {
                "item_url": "https://ex/bad",
                "confidence": [1, 2],
                "impact_categories": {"not": "a list"},
            },
        ],
    )

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _analyst_user()
    app.dependency_overrides[session_get_db] = fake_analyst_session
    monkeypatch.setattr(
        "sentinel_prism.db.repositories.review_queue.list_pending_review_items",
        AsyncMock(return_value=[row]),
    )
    transport = ASGITransport(app=app)
    caplog.set_level(logging.WARNING)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get("/review-queue")
        assert r.status_code == 200
        body = r.json()
        assert len(body["items"]) == 1
        item = body["items"][0]
        assert len(item["classifications"]) == 1
        assert item["classifications"][0]["item_url"] == "https://ex/ok"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_list_review_queue_forwards_pagination_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

    async def fake_analyst_session() -> None:
        yield MagicMock()

    captured: dict[str, object] = {}

    async def _list(_session: object, *, limit: int, offset: int) -> list[object]:
        captured["limit"] = limit
        captured["offset"] = offset
        return []

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _analyst_user()
    app.dependency_overrides[session_get_db] = fake_analyst_session
    monkeypatch.setattr(
        "sentinel_prism.db.repositories.review_queue.list_pending_review_items",
        _list,
    )
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get("/review-queue?limit=25&offset=10")
        assert r.status_code == 200
        assert captured == {"limit": 25, "offset": 10}
    finally:
        app.dependency_overrides.clear()


# ----------------------------------------------------------------------------
# GET /runs/{run_id} — AC #2 (checkpoint projection), AC #4 (RBAC), AC #7 (auth tests)
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_run_detail_unauthorized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")
    app = create_app()
    transport = ASGITransport(app=app)
    async with LifespanManager(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get(f"/runs/{uuid.uuid4()}")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_get_run_detail_viewer_forbidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _viewer_user()
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get(f"/runs/{uuid.uuid4()}")
        assert r.status_code == 403
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_run_detail_not_in_queue_returns_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

    async def fake_analyst_session() -> None:
        yield MagicMock()

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _analyst_user()
    app.dependency_overrides[session_get_db] = fake_analyst_session
    monkeypatch.setattr(
        "sentinel_prism.db.repositories.review_queue.get_pending_by_run_id",
        AsyncMock(return_value=None),
    )
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get(f"/runs/{uuid.uuid4()}")
        assert r.status_code == 404
        assert "queue" in r.json()["detail"].lower()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_run_detail_analyst_ok_allowlists_errors_and_llm_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Detail endpoint must return a typed projection and filter NFR12-sensitive fields."""

    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

    async def fake_analyst_session() -> None:
        yield MagicMock()

    run_id = uuid.uuid4()
    queued_at = datetime.now(timezone.utc)
    pending_row = SimpleNamespace(
        run_id=run_id,
        source_id=None,
        queued_at=queued_at,
        items_summary=[],
    )

    snap_values = {
        "run_id": str(run_id),
        "source_id": None,
        "flags": {"needs_human_review": True},
        "classifications": [{"item_url": "https://x/1", "confidence": 0.2}],
        "normalized_updates": [{"item_url": "https://x/1"}],
        "errors": [
            {
                "step": "classify",
                "message": "llm_transient",
                "error_class": "TimeoutError",
                "detail": "a" * 2000,
                # Sensitive/unknown keys must be dropped by ErrorDetailRow.
                "raw_prompt": "SECRET PROMPT SHOULD NOT LEAK",
                "provider_secret": "sk-live-should-not-leak",
            }
        ],
        "llm_trace": {
            "model_id": "gpt-test",
            "prompt_version": "v1",
            "status": "ok",
            # NFR12: raw prompt bodies must be filtered out even if a node
            # regresses and sets them.
            "prompt": "SECRET PROMPT",
            "web_search_raw": {"tavily": "payload"},
        },
    }
    snap = SimpleNamespace(values=snap_values)

    graph_mock = MagicMock()
    graph_mock.aget_state = AsyncMock(return_value=snap)

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _analyst_user()
    app.dependency_overrides[session_get_db] = fake_analyst_session
    monkeypatch.setattr(
        "sentinel_prism.db.repositories.review_queue.get_pending_by_run_id",
        AsyncMock(return_value=pending_row),
    )
    monkeypatch.setattr(
        "sentinel_prism.db.repositories.audit_events.list_recent_for_run",
        AsyncMock(return_value=[]),
    )
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            # Lifespan assigns a real compiled graph to ``app.state``; swap it
            # for the mock after startup so ``aget_state`` returns our snapshot.
            app.state.regulatory_graph = graph_mock
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get(f"/runs/{run_id}")
        assert r.status_code == 200
        body = r.json()

        assert body["run_id"] == str(run_id)
        assert body["flags"] == {"needs_human_review": True}
        assert body["classifications"] == [{"item_url": "https://x/1", "confidence": 0.2}]
        assert body["normalized_updates"] == [{"item_url": "https://x/1"}]

        # AC #5 + NFR12 — errors go through ErrorDetailRow; unknown keys dropped.
        assert len(body["errors"]) == 1
        err = body["errors"][0]
        assert err["step"] == "classify"
        assert err["message"] == "llm_transient"
        assert err["error_class"] == "TimeoutError"
        assert err["detail"] is not None
        assert len(err["detail"]) <= 513  # 512 + ellipsis
        assert err["detail"].endswith("…")
        assert "raw_prompt" not in err
        assert "provider_secret" not in err

        # NFR12 — llm_trace only carries allowlisted keys.
        assert body["llm_trace"] == {
            "model_id": "gpt-test",
            "prompt_version": "v1",
            "status": "ok",
        }
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_run_detail_no_checkpoint_returns_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MemorySaver + restart split-brain — projection exists, checkpoint does not."""

    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

    async def fake_analyst_session() -> None:
        yield MagicMock()

    run_id = uuid.uuid4()
    pending_row = SimpleNamespace(
        run_id=run_id,
        source_id=None,
        queued_at=datetime.now(timezone.utc),
        items_summary=[],
    )

    graph_mock = MagicMock()
    graph_mock.aget_state = AsyncMock(return_value=SimpleNamespace(values=None))

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _analyst_user()
    app.dependency_overrides[session_get_db] = fake_analyst_session
    monkeypatch.setattr(
        "sentinel_prism.db.repositories.review_queue.get_pending_by_run_id",
        AsyncMock(return_value=pending_row),
    )
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            app.state.regulatory_graph = graph_mock
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get(f"/runs/{run_id}")
        assert r.status_code == 404
        assert "checkpoint" in r.json()["detail"].lower()
    finally:
        app.dependency_overrides.clear()


# ----------------------------------------------------------------------------
# Graph + checkpointer integration (AC #7 — shared checkpointer, listing surfaces run)
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_interrupt_leaves_checkpoint_and_projection_hook(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
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
        item_url="https://news.example/r",
        fetched_at=datetime.now(timezone.utc),
        title="Review me",
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
    assert len(recorded) == 1
    assert recorded[0]["run_id"] == run_id
    assert isinstance(recorded[0]["items_summary"], list)
    assert len(recorded[0]["items_summary"]) == 1
    # AC #1 — ``queued_at`` must come from the workflow-interrupted timestamp
    # captured inside ``human_review_gate`` (not ``func.now()`` at DB upsert).
    interrupted_at = recorded[0].get("queued_at")
    assert isinstance(interrupted_at, datetime)
    assert interrupted_at.tzinfo is not None

    snap = await graph.aget_state(config)
    assert snap.values.get("run_id") == run_id
    assert snap.interrupts


@pytest.mark.asyncio
async def test_interrupt_projection_ends_up_listable_via_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC #7 — after interrupt, ``list_pending_review_items`` surfaces the run.

    Uses an in-memory fake repository instead of mocking the projection hook
    to a no-op, so the real ``record_review_queue_projection`` → repo → list
    path is exercised end-to-end without Postgres.
    """

    class _FakeQueue:
        def __init__(self) -> None:
            self.rows: dict[uuid.UUID, SimpleNamespace] = {}

        async def upsert_pending(
            self,
            _session: object,
            *,
            run_id: str,
            source_id: uuid.UUID | None,
            items_summary: list[dict[str, object]],
            queued_at: datetime | None = None,
        ) -> None:
            rid = uuid.UUID(str(run_id).strip())
            self.rows[rid] = SimpleNamespace(
                run_id=rid,
                source_id=source_id,
                queued_at=queued_at or datetime.now(timezone.utc),
                items_summary=list(items_summary),
            )

        async def list_pending_review_items(
            self,
            _session: object,
            *,
            limit: int = 50,
            offset: int = 0,
        ) -> list[SimpleNamespace]:
            rows = sorted(
                self.rows.values(), key=lambda r: r.queued_at, reverse=True
            )
            return rows[offset : offset + limit]

    fake = _FakeQueue()
    monkeypatch.setattr(
        "sentinel_prism.db.repositories.review_queue.upsert_pending",
        fake.upsert_pending,
    )
    monkeypatch.setattr(
        "sentinel_prism.db.repositories.review_queue.list_pending_review_items",
        fake.list_pending_review_items,
    )

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
        item_url="https://news.example/r2",
        fetched_at=datetime.now(timezone.utc),
        title="Listable",
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

    listed = await fake.list_pending_review_items(MagicMock())
    assert [str(r.run_id) for r in listed] == [run_id]
    assert listed[0].source_id == uuid.UUID(source_id)
    assert listed[0].items_summary


# ----------------------------------------------------------------------------
# POST /runs/{run_id}/resume — Story 4.2
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_run_unauthorized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")
    app = create_app()
    transport = ASGITransport(app=app)
    async with LifespanManager(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                f"/runs/{uuid.uuid4()}/resume",
                json={"decision": "approve", "note": ""},
            )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_resume_run_viewer_forbidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _viewer_user()
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.post(
                    f"/runs/{uuid.uuid4()}/resume",
                    json={"decision": "approve", "note": ""},
                )
        assert r.status_code == 403
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_resume_run_not_in_queue_returns_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

    async def fake_analyst_session() -> None:
        yield MagicMock()

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _analyst_user()
    app.dependency_overrides[session_get_db] = fake_analyst_session
    monkeypatch.setattr(
        "sentinel_prism.db.repositories.review_queue.get_pending_by_run_id",
        AsyncMock(return_value=None),
    )
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.post(
                    f"/runs/{uuid.uuid4()}/resume",
                    json={"decision": "approve", "note": ""},
                )
        assert r.status_code == 404
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_resume_run_not_interrupted_returns_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

    async def fake_analyst_session() -> None:
        yield MagicMock()

    run_id = uuid.uuid4()
    pending_row = SimpleNamespace(
        run_id=run_id,
        source_id=uuid.uuid4(),
        queued_at=datetime.now(timezone.utc),
        items_summary=[],
    )
    snap = SimpleNamespace(
        values={"run_id": str(run_id), "flags": {}},
        interrupts=(),
    )
    graph_mock = MagicMock()
    graph_mock.aget_state = AsyncMock(return_value=snap)

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _analyst_user()
    app.dependency_overrides[session_get_db] = fake_analyst_session
    monkeypatch.setattr(
        "sentinel_prism.db.repositories.review_queue.get_pending_by_run_id",
        AsyncMock(return_value=pending_row),
    )
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            app.state.regulatory_graph = graph_mock
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.post(
                    f"/runs/{run_id}/resume",
                    json={"decision": "approve", "note": ""},
                )
        assert r.status_code == 404
        assert "waiting" in r.json()["detail"].lower()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_resume_run_override_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

    analyst = _analyst_user()
    session_mock = MagicMock()
    session_mock.commit = AsyncMock()

    async def fake_analyst_session() -> None:
        yield session_mock

    run_id = uuid.uuid4()
    src = uuid.uuid4()
    pending_row = SimpleNamespace(
        run_id=run_id,
        source_id=src,
        queued_at=datetime.now(timezone.utc),
        items_summary=[],
    )
    snap_pre = SimpleNamespace(
        values={
            "run_id": str(run_id),
            "flags": {"needs_human_review": True},
            "classifications": [
                {
                    "item_url": "https://ex/item",
                    "in_scope": True,
                    "severity": "high",
                    "urgency": "immediate",
                    "confidence": 0.3,
                    "rationale": "model uncertain",
                    "impact_categories": ["labeling"],
                    "needs_human_review": True,
                }
            ],
        },
        interrupts=(SimpleNamespace(),),
    )
    snap_post = SimpleNamespace(
        values={"run_id": str(run_id), "flags": {"needs_human_review": False}},
        interrupts=(),
    )
    graph_mock = MagicMock()
    graph_mock.aget_state = AsyncMock(side_effect=[snap_pre, snap_post])
    graph_mock.ainvoke = AsyncMock(return_value={})

    append_mock = AsyncMock(return_value=uuid.uuid4())
    delete_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "sentinel_prism.api.routes.runs.audit_events_repo.append_audit_event",
        append_mock,
    )
    monkeypatch.setattr(
        "sentinel_prism.db.repositories.review_queue.delete_pending_by_run_id",
        delete_mock,
    )

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: analyst
    app.dependency_overrides[session_get_db] = fake_analyst_session
    monkeypatch.setattr(
        "sentinel_prism.db.repositories.review_queue.get_pending_by_run_id",
        AsyncMock(return_value=pending_row),
    )
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            app.state.regulatory_graph = graph_mock
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.post(
                    f"/runs/{run_id}/resume",
                    json={
                        "decision": "override",
                        "note": "severity too high",
                        "overrides": [
                            {
                                "severity": "low",
                                "confidence": 0.95,
                                "item_url": "https://ex/item",
                            }
                        ],
                    },
                )
        assert r.status_code == 200
        body = r.json()
        assert body["run_id"] == str(run_id)
        assert body["decision"] == "override"
        assert body["status"] == "completed"

        graph_mock.ainvoke.assert_awaited_once()
        cmd = graph_mock.ainvoke.await_args.args[0]
        assert isinstance(cmd, Command)
        assert cmd.resume["decision"] == "override"
        assert cmd.resume["note"] == "severity too high"
        assert cmd.resume["overrides"][0]["severity"] == "low"

        append_mock.assert_awaited_once()
        call_kw = append_mock.await_args.kwargs
        assert call_kw["actor_user_id"] == analyst.id
        assert call_kw["source_id"] == src
        # AC #3 — note + decision persisted in audit metadata; override path
        # additionally records a field-level patch summary (Story 4.2 code
        # review — resolved decision-needed D2).
        audit_meta = call_kw["metadata"]
        assert audit_meta["decision"] == "override"
        assert audit_meta["note"] == "severity too high"
        assert "override_patches" in audit_meta
        patches_meta = audit_meta["override_patches"]
        assert isinstance(patches_meta, list) and len(patches_meta) == 1
        assert patches_meta[0]["severity"] == "low"
        assert patches_meta[0]["confidence"] == 0.95
        assert patches_meta[0]["item_url"] == "https://ex/item"
        delete_mock.assert_awaited_once()
        session_mock.commit.assert_awaited_once()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_resume_run_override_requires_note_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

    async def fake_analyst_session() -> None:
        yield MagicMock()

    run_id = uuid.uuid4()
    pending_row = SimpleNamespace(
        run_id=run_id,
        source_id=None,
        queued_at=datetime.now(timezone.utc),
        items_summary=[],
    )
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _analyst_user()
    app.dependency_overrides[session_get_db] = fake_analyst_session
    monkeypatch.setattr(
        "sentinel_prism.db.repositories.review_queue.get_pending_by_run_id",
        AsyncMock(return_value=pending_row),
    )
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.post(
                    f"/runs/{run_id}/resume",
                    json={
                        "decision": "override",
                        "note": "",
                        "overrides": [{"severity": "low"}],
                    },
                )
        assert r.status_code == 422
    finally:
        app.dependency_overrides.clear()
