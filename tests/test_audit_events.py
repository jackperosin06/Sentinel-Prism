"""Audit events repository and pipeline instrumentation (Story 3.8)."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sentinel_prism.db.models import (
    AuditEvent,
    Briefing,
    FallbackMode,
    PipelineAuditAction,
    Source,
    SourceType,
)
from sentinel_prism.db.repositories import audit_events as audit_events_repo
from sentinel_prism.graph import compile_regulatory_pipeline_graph, new_pipeline_state
from sentinel_prism.services.connectors.scout_raw_item import ScoutRawItem
from sentinel_prism.services.llm.classification import build_classification_llm

ROOT = Path(__file__).resolve().parents[1]


def _fake_source_row() -> SimpleNamespace:
    return SimpleNamespace(
        enabled=True,
        source_type=SourceType.RSS,
        primary_url="https://ex.test/f",
        fallback_url=None,
        fallback_mode=FallbackMode.NONE,
        name="Audit Src",
        jurisdiction="EU",
    )


def _session_factory_patch() -> MagicMock:
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=MagicMock())
    cm.__aexit__ = AsyncMock(return_value=None)
    return MagicMock(return_value=cm)


@pytest.mark.asyncio
async def test_append_audit_event_inserts_only_via_add_and_flush() -> None:
    session = MagicMock()
    added: list[AuditEvent] = []

    def _capture(row: AuditEvent) -> None:
        added.append(row)

    session.add.side_effect = _capture

    async def _flush() -> None:
        for row in added:
            if row.id is None:
                row.id = uuid.uuid4()

    session.flush = AsyncMock(side_effect=_flush)
    run_uuid = uuid.uuid4()
    src = uuid.uuid4()
    new_id = await audit_events_repo.append_audit_event(
        session,
        run_id=run_uuid,
        action=PipelineAuditAction.PIPELINE_SCOUT_COMPLETED,
        source_id=src,
        metadata={"raw_item_count": 2, "trigger": "manual"},
    )
    assert new_id is not None
    session.add.assert_called_once()
    row = session.add.call_args[0][0]
    assert isinstance(row, AuditEvent)
    assert row.run_id == run_uuid
    assert row.action == PipelineAuditAction.PIPELINE_SCOUT_COMPLETED
    assert row.source_id == src
    assert row.event_metadata == {"raw_item_count": 2, "trigger": "manual"}
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_append_audit_event_invalid_run_id_skips_insert(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    session = MagicMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    out = await audit_events_repo.append_audit_event(
        session,
        run_id="not-a-uuid",
        action=PipelineAuditAction.PIPELINE_SCOUT_COMPLETED,
        source_id=None,
        metadata=None,
    )
    assert out is None
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_append_audit_event_trims_item_url_samples() -> None:
    session = MagicMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    many = [f"https://x/{i}" for i in range(25)]
    await audit_events_repo.append_audit_event(
        session,
        run_id=uuid.uuid4(),
        action=PipelineAuditAction.PIPELINE_SCOUT_COMPLETED,
        source_id=None,
        metadata={"item_url_samples": many},
    )
    row = session.add.call_args[0][0]
    assert len(row.event_metadata["item_url_samples"]) == 10


@pytest.mark.asyncio
async def test_append_audit_event_caps_per_url_length() -> None:
    session = MagicMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    giant_url = "https://ex/" + ("a" * 4000)
    await audit_events_repo.append_audit_event(
        session,
        run_id=uuid.uuid4(),
        action=PipelineAuditAction.PIPELINE_SCOUT_COMPLETED,
        source_id=None,
        metadata={"item_url_samples": [giant_url]},
    )
    row = session.add.call_args[0][0]
    trimmed = row.event_metadata["item_url_samples"][0]
    assert len(trimmed) <= audit_events_repo._MAX_URL_LENGTH


@pytest.mark.asyncio
async def test_list_recent_for_run_filters_and_limits() -> None:
    """Story 4.1 — detail endpoint relies on this repo helper for the audit tail."""

    session = MagicMock()

    class _Scalars:
        def __init__(self, rows: list[AuditEvent]) -> None:
            self._rows = rows

        def all(self) -> list[AuditEvent]:
            return list(self._rows)

    captured: dict[str, Any] = {}

    async def _scalars(stmt: Any) -> _Scalars:
        captured["stmt"] = stmt
        return _Scalars(
            [
                AuditEvent(
                    run_id=uuid.uuid4(),
                    action=PipelineAuditAction.PIPELINE_SCOUT_COMPLETED,
                    source_id=None,
                    actor_user_id=None,
                    event_metadata=None,
                )
            ]
        )

    session.scalars = AsyncMock(side_effect=_scalars)

    target = uuid.uuid4()
    out = await audit_events_repo.list_recent_for_run(session, run_id=target, limit=5)
    assert len(out) == 1
    # Repo must bound ``limit`` defensively (spec caps at 100).
    out = await audit_events_repo.list_recent_for_run(session, run_id=target, limit=999)
    assert session.scalars.await_count == 2
    # Invalid UUID path returns [] without touching the session.
    session.scalars.reset_mock()
    out = await audit_events_repo.list_recent_for_run(
        session, run_id="not-a-uuid", limit=5
    )
    assert out == []
    assert session.scalars.await_count == 0


@pytest.mark.asyncio
async def test_projection_failure_emits_fallback_audit_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D1 resolution: upsert failure must still produce a discoverable audit row."""

    from sentinel_prism.graph import pipeline_review

    class _BoomSession:
        async def __aenter__(self) -> "_BoomSession":
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def commit(self) -> None:
            return None

    def _boom_factory() -> Any:
        return lambda: _BoomSession()

    async def _upsert_boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("db is down")

    captured: list[dict[str, Any]] = []

    async def _capture_audit(**kwargs: Any) -> list[dict[str, Any]]:
        captured.append(dict(kwargs))
        return []

    monkeypatch.setattr(
        "sentinel_prism.graph.pipeline_review.get_session_factory",
        _boom_factory,
    )
    monkeypatch.setattr(
        "sentinel_prism.db.repositories.review_queue.upsert_pending",
        _upsert_boom,
    )
    monkeypatch.setattr(
        "sentinel_prism.graph.pipeline_review.record_pipeline_audit_event",
        _capture_audit,
    )

    await pipeline_review.record_review_queue_projection(
        run_id=str(uuid.uuid4()),
        source_id=None,
        items_summary=[],
    )

    assert len(captured) == 1
    assert captured[0]["action"] == PipelineAuditAction.HUMAN_REVIEW_QUEUE_PROJECTION_FAILED
    meta = captured[0]["metadata"]
    assert meta["error_class"] == "RuntimeError"
    assert "db is down" in meta["error_message"]


@pytest.mark.asyncio
async def test_append_audit_event_rejects_non_dict_metadata() -> None:
    session = MagicMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    with pytest.raises(TypeError):
        await audit_events_repo.append_audit_event(
            session,
            run_id=uuid.uuid4(),
            action=PipelineAuditAction.PIPELINE_SCOUT_COMPLETED,
            source_id=None,
            metadata=["not", "a", "dict"],  # type: ignore[arg-type]
        )
    session.add.assert_not_called()


# --- Per-node metadata schema assertions (AC #5) -----------------------------
# These tests invoke each pipeline node with the real audit path mocked so we
# can capture and assert the exact metadata payload each node emits. This
# locks the spec's documented key sets (AC #1 `metadata` whitelist, subtask
# bullets) against regression without requiring Postgres.


def _node_source_row() -> SimpleNamespace:
    return SimpleNamespace(
        enabled=True,
        source_type=SourceType.RSS,
        primary_url="https://ex.test/f",
        fallback_url=None,
        fallback_mode=FallbackMode.NONE,
        name="Audit Node Src",
        jurisdiction="EU",
    )


def _business_session_factory_patch() -> MagicMock:
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=MagicMock())
    cm.__aexit__ = AsyncMock(return_value=None)
    return MagicMock(return_value=cm)


@pytest.mark.asyncio
async def test_node_scout_audit_metadata_schema() -> None:
    from sentinel_prism.graph.nodes import scout as scout_node

    run_id = str(uuid.uuid4())
    source_id = str(uuid.uuid4())
    fetched = datetime.now(timezone.utc)
    items = [
        ScoutRawItem(
            source_id=uuid.UUID(source_id),
            item_url="https://reg.example/a",
            fetched_at=fetched,
            title="A",
        ),
    ]
    captured: dict[str, Any] = {}

    async def _capture(**kwargs: Any) -> list[dict[str, Any]]:
        captured.update(kwargs)
        return []

    state = new_pipeline_state(run_id, source_id=source_id, trigger="manual")
    with (
        patch(
            "sentinel_prism.graph.nodes.scout.get_session_factory",
            return_value=_business_session_factory_patch(),
        ),
        patch(
            "sentinel_prism.graph.nodes.scout.sources_repo.get_source_by_id",
            new_callable=AsyncMock,
            return_value=_node_source_row(),
        ),
        patch(
            "sentinel_prism.graph.nodes.scout.fetch_scout_items_with_fallback",
            new_callable=AsyncMock,
            return_value=(items, "primary"),
        ),
        patch.object(scout_node, "record_pipeline_audit_event", side_effect=_capture),
    ):
        await scout_node.node_scout(state)

    assert captured["action"] == PipelineAuditAction.PIPELINE_SCOUT_COMPLETED
    assert captured["run_id"] == run_id
    assert isinstance(captured["source_id"], uuid.UUID)
    meta = captured["metadata"]
    assert set(meta.keys()) <= {
        "raw_item_count",
        "trigger",
        "fetch_outcome",
        "item_url_samples",
    }
    assert meta["raw_item_count"] == 1
    assert meta["trigger"] == "manual"
    assert meta["fetch_outcome"] == "primary"
    assert "completed_at" not in meta  # per Decision 1 — rely on created_at
    assert meta["item_url_samples"] == ["https://reg.example/a"]


@pytest.mark.asyncio
async def test_node_normalize_audit_metadata_schema() -> None:
    from sentinel_prism.graph.nodes import normalize as normalize_node

    run_id = str(uuid.uuid4())
    source_id = str(uuid.uuid4())
    captured: list[dict[str, Any]] = []

    async def _capture(**kwargs: Any) -> list[dict[str, Any]]:
        captured.append(kwargs)
        return []

    state = new_pipeline_state(run_id, source_id=source_id)
    state["raw_items"] = [
        {
            "source_id": source_id,
            "item_url": "https://reg.example/a",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "title": "A",
            "payload": {"title": "A"},
            "fingerprint": "fp-1",
        },
    ]
    with (
        patch(
            "sentinel_prism.graph.nodes.normalize.get_session_factory",
            return_value=_business_session_factory_patch(),
        ),
        patch(
            "sentinel_prism.graph.nodes.normalize.sources_repo.get_source_by_id",
            new_callable=AsyncMock,
            return_value=_node_source_row(),
        ),
        patch.object(
            normalize_node, "record_pipeline_audit_event", side_effect=_capture
        ),
    ):
        await normalize_node.node_normalize(state)

    assert len(captured) == 1
    kw = captured[0]
    assert kw["action"] == PipelineAuditAction.PIPELINE_NORMALIZE_COMPLETED
    assert kw["run_id"] == run_id
    meta = kw["metadata"]
    assert set(meta.keys()) == {"normalized_count"}
    assert meta["normalized_count"] >= 0
    assert "completed_at" not in meta


@pytest.mark.asyncio
async def test_node_normalize_empty_raw_still_emits_audit() -> None:
    from sentinel_prism.graph.nodes import normalize as normalize_node

    run_id = str(uuid.uuid4())
    source_id = str(uuid.uuid4())
    captured: list[dict[str, Any]] = []

    async def _capture(**kwargs: Any) -> list[dict[str, Any]]:
        captured.append(kwargs)
        return []

    state = new_pipeline_state(run_id, source_id=source_id)
    state["raw_items"] = []
    with patch.object(
        normalize_node, "record_pipeline_audit_event", side_effect=_capture
    ):
        out = await normalize_node.node_normalize(state)

    assert out == {}
    assert len(captured) == 1
    kw = captured[0]
    assert kw["action"] == PipelineAuditAction.PIPELINE_NORMALIZE_COMPLETED
    assert kw["metadata"] == {"normalized_count": 0}


@pytest.mark.asyncio
async def test_node_classify_audit_metadata_schema() -> None:
    from sentinel_prism.graph.nodes import classify as classify_node

    run_id = str(uuid.uuid4())
    source_id = str(uuid.uuid4())
    captured: list[dict[str, Any]] = []

    async def _capture(**kwargs: Any) -> list[dict[str, Any]]:
        captured.append(kwargs)
        return []

    state = new_pipeline_state(run_id, source_id=source_id)
    state["normalized_updates"] = [
        {
            "source_id": source_id,
            "item_url": "https://reg.example/a",
            "title": "A",
            "summary": "s",
            "jurisdiction": "EU",
            "document_type": "guidance",
        }
    ]
    with patch.object(
        classify_node, "record_pipeline_audit_event", side_effect=_capture
    ):
        await classify_node.node_classify(state)

    assert len(captured) == 1
    kw = captured[0]
    assert kw["action"] == PipelineAuditAction.PIPELINE_CLASSIFY_COMPLETED
    meta = kw["metadata"]
    assert set(meta.keys()) <= {"classification_count", "llm_trace"}
    assert "completed_at" not in meta
    llm_trace = meta["llm_trace"]
    assert {"status", "model_id", "prompt_version"} <= set(llm_trace.keys())
    if "web_search" in llm_trace:
        # AC: ``tool_injected`` is a test-only DI hook and MUST NOT land
        # in the append-only audit row (kept in ``out["llm_trace"]`` only).
        assert "tool_injected" not in llm_trace["web_search"]


@pytest.mark.asyncio
async def test_node_classify_empty_norms_emits_audit_with_zero_count() -> None:
    from sentinel_prism.graph.nodes import classify as classify_node

    run_id = str(uuid.uuid4())
    source_id = str(uuid.uuid4())
    captured: list[dict[str, Any]] = []

    async def _capture(**kwargs: Any) -> list[dict[str, Any]]:
        captured.append(kwargs)
        return []

    state = new_pipeline_state(run_id, source_id=source_id)
    state["normalized_updates"] = []
    with patch.object(
        classify_node, "record_pipeline_audit_event", side_effect=_capture
    ):
        out = await classify_node.node_classify(state)

    assert out == {}
    assert len(captured) == 1
    kw = captured[0]
    assert kw["action"] == PipelineAuditAction.PIPELINE_CLASSIFY_COMPLETED
    assert kw["metadata"]["classification_count"] == 0
    assert kw["metadata"]["llm_trace"]["status"] == "no_attempt"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_regulatory_graph_writes_audit_events_when_db_available() -> None:
    db_url = os.environ.get("DATABASE_URL", "").strip()
    sync_url = os.environ.get("ALEMBIC_SYNC_URL", "").strip()
    if not db_url or not sync_url:
        pytest.skip("DATABASE_URL and ALEMBIC_SYNC_URL required for integration")

    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "ALEMBIC_SYNC_URL": sync_url}
    mig = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(ROOT / "alembic.ini"),
            "upgrade",
            "head",
        ],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert mig.returncode == 0, mig.stderr

    engine = create_async_engine(db_url)
    setup_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    suffix = uuid.uuid4().hex[:8]
    run_id = str(uuid.uuid4())
    fetched = datetime.now(timezone.utc)
    async with setup_factory() as setup_sess:
        src_row = Source(
            name=f"audit-graph-{suffix}",
            jurisdiction="EU",
            source_type=SourceType.RSS,
            primary_url="https://example.com/feed",
            schedule="0 * * * *",
            enabled=True,
        )
        setup_sess.add(src_row)
        await setup_sess.commit()
        source_id = str(src_row.id)

    row_stub = _fake_source_row()
    items = [
        ScoutRawItem(
            source_id=uuid.UUID(source_id),
            item_url="https://reg.example/audit-1",
            fetched_at=fetched,
            title="A1",
        ),
    ]
    mock_sf = _session_factory_patch()
    graph = compile_regulatory_pipeline_graph()
    config = {"configurable": {"thread_id": run_id}}

    # NOTE: ``sentinel_prism.graph.pipeline_audit.get_session_factory`` is
    # intentionally NOT patched here — the audit writer uses the real async
    # session factory so this integration test exercises the end-to-end
    # ``audit_events`` persistence path. The scout/normalize business-session
    # patches below only swap out the upstream node sessions that are
    # unrelated to audit-event persistence. Do not "harmonize" by adding a
    # pipeline_audit patch; that would defeat the test.
    with (
        patch(
            "sentinel_prism.graph.nodes.scout.get_session_factory",
            return_value=mock_sf,
        ),
        patch(
            "sentinel_prism.graph.nodes.normalize.get_session_factory",
            return_value=mock_sf,
        ),
        patch(
            "sentinel_prism.graph.nodes.scout.sources_repo.get_source_by_id",
            new_callable=AsyncMock,
            return_value=row_stub,
        ),
        patch(
            "sentinel_prism.graph.nodes.normalize.sources_repo.get_source_by_id",
            new_callable=AsyncMock,
            return_value=row_stub,
        ),
        patch(
            "sentinel_prism.graph.nodes.scout.fetch_scout_items_with_fallback",
            new_callable=AsyncMock,
            return_value=(items, "primary"),
        ),
        patch(
            "sentinel_prism.graph.nodes.classify.build_classification_llm",
            return_value=build_classification_llm(),
        ),
    ):
        await graph.ainvoke(
            new_pipeline_state(run_id, source_id=source_id, trigger="manual"),
            config,
        )

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    run_uuid = uuid.UUID(run_id)
    source_uuid = uuid.UUID(source_id)
    try:
        async with factory() as session:
            for action in (
                PipelineAuditAction.PIPELINE_SCOUT_COMPLETED,
                PipelineAuditAction.PIPELINE_NORMALIZE_COMPLETED,
                PipelineAuditAction.PIPELINE_CLASSIFY_COMPLETED,
                PipelineAuditAction.BRIEFING_GENERATED,
            ):
                n = await session.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(
                        AuditEvent.run_id == run_uuid,
                        AuditEvent.action == action,
                    )
                )
                assert int(n or 0) >= 1
        # Clean up the rows we inserted so the shared integration DB does not
        # accumulate pollution across runs.
        async with factory() as cleanup_sess:
            await cleanup_sess.execute(
                delete(Briefing).where(Briefing.run_id == run_uuid)
            )
            await cleanup_sess.execute(
                delete(AuditEvent).where(AuditEvent.run_id == run_uuid)
            )
            await cleanup_sess.execute(
                delete(Source).where(Source.id == source_uuid)
            )
            await cleanup_sess.commit()
    finally:
        await engine.dispose()
