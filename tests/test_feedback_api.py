"""User feedback on updates API (Story 7.1 — FR26, FR27)."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from sentinel_prism.api.deps import get_current_user
from sentinel_prism.db.models import (
    Briefing,
    NormalizedUpdateRow,
    RawCapture,
    Source,
    SourceType,
    UpdateFeedbackKind,
    User,
    UserRole,
)
from sentinel_prism.db.session import get_db
from sentinel_prism.main import create_app

ROOT = Path(__file__).resolve().parents[1]


def _viewer() -> User:
    return User(
        id=uuid.uuid4(),
        email="v@test.local",
        password_hash="x",
        role=UserRole.VIEWER,
        team_slug=None,
        is_active=True,
    )


def _analyst() -> User:
    return User(
        id=uuid.uuid4(),
        email="a@test.local",
        password_hash="x",
        role=UserRole.ANALYST,
        team_slug="t1",
        is_active=True,
    )


def _admin() -> User:
    return User(
        id=uuid.uuid4(),
        email="adm@test.local",
        password_hash="x",
        role=UserRole.ADMIN,
        team_slug=None,
        is_active=True,
    )


def _norm_row(nid: uuid.UUID, run_id: uuid.UUID | None) -> NormalizedUpdateRow:
    rid = uuid.uuid4()
    return NormalizedUpdateRow(
        id=nid,
        raw_capture_id=uuid.uuid4(),
        source_id=uuid.uuid4(),
        source_name="S",
        jurisdiction="US",
        item_url="https://ex.test/x",
        document_type="g",
        title="T",
        run_id=run_id,
        created_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_post_feedback_unauthorized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")
    app = create_app()
    transport = ASGITransport(app=app)
    async with LifespanManager(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                f"/updates/{uuid.uuid4()}/feedback",
                json={"kind": "incorrect_severity", "comment": "x"},
            )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_post_feedback_forbidden_for_viewer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")
    app = create_app()
    app.dependency_overrides[get_current_user] = _viewer
    app.dependency_overrides[get_db] = _fake_get_db_empty
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.post(
                    f"/updates/{uuid.uuid4()}/feedback",
                    json={"kind": "incorrect_severity", "comment": "note"},
                )
        assert r.status_code == 403
    finally:
        app.dependency_overrides.clear()


async def _fake_get_db_empty() -> object:
    s = MagicMock()
    s.scalar = AsyncMock(return_value=None)
    s.commit = AsyncMock()
    yield s


@pytest.mark.asyncio
async def test_post_feedback_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")
    app = create_app()
    app.dependency_overrides[get_current_user] = _analyst

    async def fake_get_db() -> object:
        s = MagicMock()
        s.scalar = AsyncMock(return_value=None)
        s.commit = AsyncMock()
        yield s

    app.dependency_overrides[get_db] = fake_get_db
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.post(
                    f"/updates/{uuid.uuid4()}/feedback",
                    json={"kind": "incorrect_severity", "comment": "missing row"},
                )
        assert r.status_code == 404
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_post_feedback_validation_empty_comment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")
    app = create_app()
    app.dependency_overrides[get_current_user] = _analyst
    app.dependency_overrides[get_db] = _fake_get_db_empty
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.post(
                    f"/updates/{uuid.uuid4()}/feedback",
                    json={"kind": "incorrect_severity", "comment": "   "},
                )
        assert r.status_code == 422
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_post_feedback_validation_bad_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")
    app = create_app()
    app.dependency_overrides[get_current_user] = _analyst
    app.dependency_overrides[get_db] = _fake_get_db_empty
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.post(
                    f"/updates/{uuid.uuid4()}/feedback",
                    json={"kind": "not_a_valid_kind", "comment": "x"},
                )
        assert r.status_code == 422
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_post_feedback_analyst_persists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")
    from sentinel_prism.api.routes import updates as updates_mod

    nid = uuid.uuid4()
    run_id = uuid.uuid4()
    nrow = _norm_row(nid, run_id)
    current = _analyst()

    captured: dict[str, object] = {}

    async def fake_overlay(
        _db: object,
        *,
        run_id: object,
        normalized_update_id: object,
    ) -> dict:
        return {"severity": "high", "impact_categories": ["x"], "confidence": 0.5}

    async def fake_insert(
        _session: object,
        **kwargs: object,
    ) -> object:
        captured.update(kwargs)
        return SimpleFeedbackRow(
            id=uuid.uuid4(),
            created_at=datetime.now(timezone.utc),
            normalized_update_id=kwargs["normalized_update_id"],
            run_id=kwargs["run_id"],
            kind=kwargs["kind"],
            classification_snapshot=kwargs["classification_snapshot"],
        )

    class SimpleFeedbackRow:
        def __init__(
            self,
            *,
            id: uuid.UUID,
            created_at: datetime,
            normalized_update_id: uuid.UUID,
            run_id: uuid.UUID | None,
            kind: UpdateFeedbackKind,
            classification_snapshot: object,
        ) -> None:
            self.id = id
            self.created_at = created_at
            self.normalized_update_id = normalized_update_id
            self.run_id = run_id
            self.kind = kind
            self.classification_snapshot = classification_snapshot

    monkeypatch.setattr(
        updates_mod.updates_repo, "fetch_classification_overlay", fake_overlay
    )
    monkeypatch.setattr(updates_mod.feedback_repo, "insert_feedback", fake_insert)

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: current

    async def fake_get_db() -> object:
        s = MagicMock()
        s.scalar = AsyncMock(return_value=nrow)
        s.commit = AsyncMock()
        yield s

    app.dependency_overrides[get_db] = fake_get_db
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.post(
                    f"/updates/{nid}/feedback",
                    json={"kind": "false_positive", "comment": "  not relevant  "},
                )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["normalized_update_id"] == str(nid)
        assert body["run_id"] == str(run_id)
        assert body["kind"] == "false_positive"
        assert body["classification_snapshot"]["severity"] == "high"
        assert captured["user_id"] == current.id
        assert captured["kind"] == UpdateFeedbackKind.FALSE_POSITIVE
        assert captured["comment"] == "not relevant"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_post_feedback_persists_row_in_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_url = os.environ.get("DATABASE_URL", "").strip()
    sync_url = os.environ.get("ALEMBIC_SYNC_URL", "").strip()
    if not db_url or not sync_url:
        pytest.skip("DATABASE_URL and ALEMBIC_SYNC_URL required for integration")

    monkeypatch.setenv("JWT_SECRET", "integration-test-jwt-secret-32chars-min")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

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

    import sentinel_prism.db.session as session_mod

    session_mod._engine = None  # type: ignore[attr-defined]
    session_mod._session_factory = None  # type: ignore[attr-defined]

    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    session_mod._engine = engine  # type: ignore[attr-defined]
    session_mod._session_factory = factory  # type: ignore[attr-defined]

    suffix = uuid.uuid4().hex[:8]
    user_id = uuid.uuid4()
    source_id = uuid.uuid4()
    raw_id = uuid.uuid4()
    norm_id = uuid.uuid4()
    run_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    current = User(
        id=user_id,
        email=f"feedback-{suffix}@example.com",
        password_hash="x",
        role=UserRole.ANALYST,
        team_slug="team-a",
        is_active=True,
    )

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: current
    transport = ASGITransport(app=app)
    sync_engine = create_engine(sync_url)

    try:
        async with factory() as session:
            session.add(current)
            session.add(
                Source(
                    id=source_id,
                    name=f"SRC-FEEDBACK-{suffix}",
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
                    payload={"title": "Raw title", "body": "raw body"},
                    run_id=run_id,
                )
            )
            await session.flush()
            session.add(
                NormalizedUpdateRow(
                    id=norm_id,
                    raw_capture_id=raw_id,
                    source_id=source_id,
                    source_name=f"SRC-FEEDBACK-{suffix}",
                    jurisdiction="US",
                    item_url=f"https://example.test/{suffix}/item",
                    document_type="guidance",
                    title="Norm title",
                    run_id=run_id,
                    created_at=now,
                )
            )
            session.add(
                Briefing(
                    id=uuid.uuid4(),
                    run_id=run_id,
                    source_id=source_id,
                    created_at=now,
                    grouping_dimensions=["severity"],
                    groups=[
                        {
                            "dimensions": {"severity": "high"},
                            "sections": {
                                "what_changed": "x",
                                "why_it_matters": "y",
                                "who_should_care": "z",
                                "confidence": "0.87",
                                "suggested_actions": None,
                            },
                            "members": [
                                {
                                    "normalized_update_id": str(norm_id),
                                    "item_url": f"https://example.test/{suffix}/item",
                                    "title": "Norm title",
                                    "severity": "high",
                                    "confidence": 0.87,
                                    "impact_categories": ["labeling"],
                                }
                            ],
                        }
                    ],
                )
            )
            await session.commit()

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                f"/updates/{norm_id}/feedback",
                json={"kind": "incorrect_relevance", "comment": "  real row  "},
            )
        assert r.status_code == 201, r.text
        body = r.json()
        feedback_id = uuid.UUID(body["id"])

        with sync_engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT user_id, normalized_update_id, run_id, kind, comment, "
                    "classification_snapshot, created_at "
                    "FROM update_feedback WHERE id = :id"
                ),
                {"id": feedback_id},
            ).mappings().one()

        assert str(row["user_id"]) == str(user_id)
        assert str(row["normalized_update_id"]) == str(norm_id)
        assert str(row["run_id"]) == str(run_id)
        assert row["kind"] == "incorrect_relevance"
        assert row["comment"] == "real row"
        assert row["classification_snapshot"]["severity"] == "high"
        assert row["created_at"] is not None
    finally:
        try:
            with sync_engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM update_feedback WHERE normalized_update_id = :i"),
                    {"i": norm_id},
                )
                conn.execute(
                    text("DELETE FROM briefings WHERE run_id = :r"),
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
                conn.execute(
                    text("DELETE FROM sources WHERE id = :i"),
                    {"i": source_id},
                )
                conn.execute(text("DELETE FROM users WHERE id = :i"), {"i": user_id})
        finally:
            app.dependency_overrides.clear()
            sync_engine.dispose()
            await engine.dispose()
            session_mod._engine = None  # type: ignore[attr-defined]
            session_mod._session_factory = None  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_post_feedback_admin_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")
    from sentinel_prism.api.routes import updates as updates_mod

    nid = uuid.uuid4()
    nrow = _norm_row(nid, None)
    current = _admin()

    async def fake_overlay(
        _db: object,
        *,
        run_id: object,
        normalized_update_id: object,
    ) -> None:
        return None

    async def fake_insert(_session: object, **kwargs: object) -> object:
        class R:
            id = uuid.uuid4()
            created_at = datetime.now(timezone.utc)
            normalized_update_id = kwargs["normalized_update_id"]
            run_id = kwargs["run_id"]
            kind = kwargs["kind"]
            classification_snapshot = kwargs["classification_snapshot"]

        return R()

    monkeypatch.setattr(
        updates_mod.updates_repo, "fetch_classification_overlay", fake_overlay
    )
    monkeypatch.setattr(updates_mod.feedback_repo, "insert_feedback", fake_insert)

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: current

    async def fake_get_db() -> object:
        s = MagicMock()
        s.scalar = AsyncMock(return_value=nrow)
        s.commit = AsyncMock()
        yield s

    app.dependency_overrides[get_db] = fake_get_db
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.post(
                    f"/updates/{nid}/feedback",
                    json={"kind": "incorrect_relevance", "comment": "y"},
                )
        assert r.status_code == 201
        assert r.json()["classification_snapshot"] is None
    finally:
        app.dependency_overrides.clear()
