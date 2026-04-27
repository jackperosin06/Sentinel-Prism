"""Admin feedback metrics API (Story 7.2 — FR28)."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sentinel_prism.api.deps import get_current_user, get_db_for_admin
from sentinel_prism.api.routes import feedback_metrics as feedback_metrics_mod
from sentinel_prism.db.models import (
    AuditEvent,
    NormalizedUpdateRow,
    PipelineAuditAction,
    RawCapture,
    Source,
    SourceType,
    UpdateFeedback,
    UpdateFeedbackKind,
    User,
    UserRole,
)
from sentinel_prism.db.repositories.feedback_metrics import (
    FeedbackMetricsSnapshot,
    fetch_feedback_metrics,
)
from sentinel_prism.main import create_app

FIXED_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
ROOT = Path(__file__).resolve().parents[1]


def _user(role: UserRole) -> User:
    return User(
        id=FIXED_ID,
        email="m@test.local",
        password_hash="x",
        role=role,
        team_slug=None,
        is_active=True,
    )


def _admin() -> User:
    return _user(UserRole.ADMIN)


def _analyst() -> User:
    return _user(UserRole.ANALYST)


def _viewer() -> User:
    return _user(UserRole.VIEWER)


@pytest.mark.asyncio
async def test_feedback_metrics_get_forbidden_non_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")
    app = create_app()
    app.dependency_overrides[get_current_user] = _analyst
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get("/admin/feedback-metrics")
        assert r.status_code == 403
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_feedback_metrics_get_forbidden_viewer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")
    app = create_app()
    app.dependency_overrides[get_current_user] = _viewer
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get("/admin/feedback-metrics")
        assert r.status_code == 403
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [UserRole.ANALYST, UserRole.VIEWER])
async def test_feedback_metrics_export_forbidden_non_admin(
    monkeypatch: pytest.MonkeyPatch, role: UserRole
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _user(role)
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get("/admin/feedback-metrics/export")
        assert r.status_code == 403
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_feedback_metrics_get_admin_json_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

    snap = FeedbackMetricsSnapshot(
        kind_counts={
            "incorrect_relevance": 1,
            "incorrect_severity": 2,
            "false_positive": 0,
            "false_negative": 1,
        },
        human_review_approved=2,
        human_review_rejected=1,
        human_review_overridden=1,
        since=datetime(2025, 1, 1, tzinfo=timezone.utc),
        until=datetime(2025, 1, 31, tzinfo=timezone.utc),
    )

    async def fake_fetch(
        _session: object, *, since: object, until: object
    ) -> FeedbackMetricsSnapshot:
        assert since == snap.since
        assert until == snap.until
        return snap

    monkeypatch.setattr(
        feedback_metrics_mod.feedback_metrics_repo,
        "fetch_feedback_metrics",
        fake_fetch,
    )

    app = create_app()
    app.dependency_overrides[get_current_user] = _admin

    async def fake_admin_db() -> object:
        s = MagicMock()
        s.execute = AsyncMock()
        yield s

    app.dependency_overrides[get_db_for_admin] = fake_admin_db
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get(
                    "/admin/feedback-metrics",
                    params={
                        "since": "2025-01-01T00:00:00+00:00",
                        "until": "2025-01-31T00:00:00+00:00",
                    },
                )
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["total_feedback"] == 4
        assert j["kind_counts"]["incorrect_severity"] == 2
        assert j["kind_percent"]["false_negative"] == 25.0
        assert j["human_review_decisions_total"] == 4
        assert j["human_review_override_rate"] == 0.25
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("params", "detail"),
    [
        (
            {
                "since": "2025-02-01T00:00:00+00:00",
                "until": "2025-01-01T00:00:00+00:00",
            },
            "since must be earlier than or equal to until.",
        ),
        (
            {"since": "2025-01-01T00:00:00"},
            "since must include a timezone offset, e.g. +00:00 for UTC.",
        ),
    ],
)
async def test_feedback_metrics_rejects_invalid_windows(
    monkeypatch: pytest.MonkeyPatch, params: dict[str, str], detail: str
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")
    app = create_app()
    app.dependency_overrides[get_current_user] = _admin

    async def fake_admin_db() -> object:
        yield MagicMock()

    app.dependency_overrides[get_db_for_admin] = fake_admin_db
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get("/admin/feedback-metrics", params=params)
        assert r.status_code == 400
        assert r.json()["detail"] == detail
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_feedback_metrics_get_admin_zero_reviews_null_rate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

    snap = FeedbackMetricsSnapshot(
        kind_counts={
            "incorrect_relevance": 0,
            "incorrect_severity": 0,
            "false_positive": 0,
            "false_negative": 0,
        },
        human_review_approved=0,
        human_review_rejected=0,
        human_review_overridden=0,
        since=None,
        until=None,
    )

    async def fake_fetch(
        _session: object, *, since: object, until: object
    ) -> FeedbackMetricsSnapshot:
        return snap

    monkeypatch.setattr(
        feedback_metrics_mod.feedback_metrics_repo,
        "fetch_feedback_metrics",
        fake_fetch,
    )
    app = create_app()
    app.dependency_overrides[get_current_user] = _admin

    async def fake_admin_db() -> object:
        yield MagicMock()

    app.dependency_overrides[get_db_for_admin] = fake_admin_db
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get("/admin/feedback-metrics")
        assert r.status_code == 200
        j = r.json()
        assert j["human_review_decisions_total"] == 0
        assert j["human_review_override_rate"] is None
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_feedback_metrics_export_csv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")
    snap = FeedbackMetricsSnapshot(
        kind_counts={
            "incorrect_relevance": 1,
            "incorrect_severity": 0,
            "false_positive": 0,
            "false_negative": 0,
        },
        human_review_approved=0,
        human_review_rejected=0,
        human_review_overridden=0,
        since=None,
        until=None,
    )

    async def fake_fetch(
        _session: object, *, since: object, until: object
    ) -> FeedbackMetricsSnapshot:
        return snap

    monkeypatch.setattr(
        feedback_metrics_mod.feedback_metrics_repo,
        "fetch_feedback_metrics",
        fake_fetch,
    )
    app = create_app()
    app.dependency_overrides[get_current_user] = _admin

    async def fake_admin_db() -> object:
        yield MagicMock()

    app.dependency_overrides[get_db_for_admin] = fake_admin_db
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get("/admin/feedback-metrics/export")
        assert r.status_code == 200
        assert "text/csv" in r.headers.get("content-type", "")
        text = r.text
        assert "override_rate" in text
        assert "incorrect_relevance" in text
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_fetch_feedback_metrics_aggregates_persisted_rows() -> None:
    db_url = os.environ.get("DATABASE_URL")
    sync_url = os.environ.get("ALEMBIC_SYNC_URL")
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
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    sync_engine = create_engine(sync_url)

    suffix = uuid.uuid4().hex[:8]
    user_id = uuid.uuid4()
    source_id = uuid.uuid4()
    raw_id = uuid.uuid4()
    norm_id = uuid.uuid4()
    run_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=1)
    until = now + timedelta(hours=1)

    try:
        async with factory() as session:
            session.add(
                User(
                    id=user_id,
                    email=f"metrics-{suffix}@example.com",
                    password_hash="x",
                    role=UserRole.ADMIN,
                    team_slug=None,
                    is_active=True,
                )
            )
            session.add(
                Source(
                    id=source_id,
                    name=f"SRC-METRICS-{suffix}",
                    jurisdiction="US",
                    source_type=SourceType.RSS,
                    primary_url=f"https://example.test/{suffix}/feed.xml",
                    schedule="0 * * * *",
                    items_ingested_total=0,
                )
            )
            await session.flush()
            session.add(
                RawCapture(
                    id=raw_id,
                    source_id=source_id,
                    captured_at=now,
                    item_url=f"https://example.test/{suffix}/item",
                    payload={"title": "Raw title"},
                    run_id=run_id,
                )
            )
            await session.flush()
            session.add(
                NormalizedUpdateRow(
                    id=norm_id,
                    raw_capture_id=raw_id,
                    source_id=source_id,
                    source_name=f"SRC-METRICS-{suffix}",
                    jurisdiction="US",
                    item_url=f"https://example.test/{suffix}/item",
                    document_type="guidance",
                    title="Metrics update",
                    run_id=run_id,
                    created_at=now,
                )
            )
            await session.flush()
            session.add_all(
                [
                    UpdateFeedback(
                        id=uuid.uuid4(),
                        user_id=user_id,
                        normalized_update_id=norm_id,
                        run_id=run_id,
                        kind=UpdateFeedbackKind.INCORRECT_RELEVANCE,
                        comment="inside one",
                        created_at=now,
                    ),
                    UpdateFeedback(
                        id=uuid.uuid4(),
                        user_id=user_id,
                        normalized_update_id=norm_id,
                        run_id=run_id,
                        kind=UpdateFeedbackKind.INCORRECT_RELEVANCE,
                        comment="inside two",
                        created_at=now + timedelta(minutes=5),
                    ),
                    UpdateFeedback(
                        id=uuid.uuid4(),
                        user_id=user_id,
                        normalized_update_id=norm_id,
                        run_id=run_id,
                        kind=UpdateFeedbackKind.FALSE_NEGATIVE,
                        comment="inside three",
                        created_at=now + timedelta(minutes=10),
                    ),
                    UpdateFeedback(
                        id=uuid.uuid4(),
                        user_id=user_id,
                        normalized_update_id=norm_id,
                        run_id=run_id,
                        kind=UpdateFeedbackKind.INCORRECT_SEVERITY,
                        comment="outside",
                        created_at=now - timedelta(days=1),
                    ),
                ]
            )
            session.add_all(
                [
                    AuditEvent(
                        id=uuid.uuid4(),
                        run_id=run_id,
                        action=PipelineAuditAction.HUMAN_REVIEW_APPROVED,
                        source_id=source_id,
                        actor_user_id=user_id,
                        event_metadata={"fixture": "inside"},
                        created_at=now,
                    ),
                    AuditEvent(
                        id=uuid.uuid4(),
                        run_id=run_id,
                        action=PipelineAuditAction.HUMAN_REVIEW_OVERRIDDEN,
                        source_id=source_id,
                        actor_user_id=user_id,
                        event_metadata={"fixture": "inside"},
                        created_at=now + timedelta(minutes=1),
                    ),
                    AuditEvent(
                        id=uuid.uuid4(),
                        run_id=run_id,
                        action=PipelineAuditAction.HUMAN_REVIEW_REJECTED,
                        source_id=source_id,
                        actor_user_id=user_id,
                        event_metadata={"fixture": "outside"},
                        created_at=now - timedelta(days=1),
                    ),
                ]
            )
            await session.commit()

        async with factory() as session:
            snap = await fetch_feedback_metrics(session, since=since, until=until)

        assert snap.kind_counts == {
            "incorrect_relevance": 2,
            "incorrect_severity": 0,
            "false_positive": 0,
            "false_negative": 1,
        }
        assert snap.human_review_approved == 1
        assert snap.human_review_rejected == 0
        assert snap.human_review_overridden == 1
    finally:
        try:
            with sync_engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM update_feedback WHERE normalized_update_id = :i"),
                    {"i": norm_id},
                )
                conn.execute(
                    text("DELETE FROM audit_events WHERE run_id = :r"),
                    {"r": run_id},
                )
                conn.execute(
                    text("DELETE FROM normalized_updates WHERE id = :i"),
                    {"i": norm_id},
                )
                conn.execute(
                    text("DELETE FROM raw_captures WHERE id = :i"),
                    {"i": raw_id},
                )
                conn.execute(text("DELETE FROM sources WHERE id = :i"), {"i": source_id})
                conn.execute(text("DELETE FROM users WHERE id = :i"), {"i": user_id})
        finally:
            sync_engine.dispose()
            await engine.dispose()
