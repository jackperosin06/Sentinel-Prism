"""Audit event search API (Story 8.1 — FR34)."""

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

from sentinel_prism.api.deps import get_current_user, get_db
from sentinel_prism.db.models import (
    AuditEvent,
    NormalizedUpdateRow,
    PipelineAuditAction,
    RawCapture,
    Source,
    SourceType,
    User,
    UserRole,
)
from sentinel_prism.db.repositories import audit_events as audit_events_repo
from sentinel_prism.main import create_app

FIXED_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
ROOT = Path(__file__).resolve().parents[1]


def _user(role: UserRole) -> User:
    return User(
        id=FIXED_ID,
        email="audit-search@test.local",
        password_hash="x",
        role=role,
        team_slug=None,
        is_active=True,
    )


@pytest.mark.asyncio
async def test_audit_search_unauthenticated_401(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")
    app = create_app()
    transport = ASGITransport(app=app)
    async with LifespanManager(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/audit-events")
    assert r.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.ANALYST, UserRole.VIEWER])
async def test_audit_search_authenticated_roles_200(
    monkeypatch: pytest.MonkeyPatch,
    role: UserRole,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

    async def fake_search(*_a: object, **_k: object) -> tuple[list[AuditEvent], int]:
        return [], 0

    monkeypatch.setattr(audit_events_repo, "search_audit_events", fake_search)

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _user(role)

    fake_session = MagicMock()
    fake_session.scalar = AsyncMock(return_value=None)

    async def fake_db() -> object:
        yield fake_session

    app.dependency_overrides[get_db] = fake_db
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get("/audit-events")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["items"] == []
        assert body["total"] == 0
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_audit_search_invalid_action_400(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _user(UserRole.VIEWER)

    async def fake_db() -> object:
        yield MagicMock()

    app.dependency_overrides[get_db] = fake_db
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get("/audit-events?action=not_a_real_action")
        assert r.status_code == 400
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_audit_search_naive_datetime_400(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _user(UserRole.VIEWER)

    async def fake_db() -> object:
        yield MagicMock()

    app.dependency_overrides[get_db] = fake_db
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get("/audit-events?created_after=2026-04-27T00:00:00")
        assert r.status_code == 400
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_audit_search_created_range_order_400(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _user(UserRole.VIEWER)

    async def fake_db() -> object:
        yield MagicMock()

    app.dependency_overrides[get_db] = fake_db
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get(
                    "/audit-events"
                    "?created_after=2026-04-28T00:00:00%2B00:00"
                    "&created_before=2026-04-27T00:00:00%2B00:00"
                )
        assert r.status_code == 400
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_audit_search_normalized_update_not_found_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _user(UserRole.ANALYST)

    fake_session = MagicMock()
    fake_session.scalar = AsyncMock(return_value=None)

    async def fake_db() -> object:
        yield fake_session

    app.dependency_overrides[get_db] = fake_db
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                missing = uuid.UUID("00000000-0000-0000-0000-000000000099")
                r = await client.get(f"/audit-events?normalized_update_id={missing}")
        assert r.status_code == 404
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_audit_search_run_id_mismatch_normalized_update_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")
    rid = uuid.uuid4()
    nu = MagicMock()
    nu.run_id = rid
    nu.source_id = uuid.uuid4()
    nu.created_at = datetime.now(timezone.utc)

    fake_session = MagicMock()
    fake_session.scalar = AsyncMock(return_value=nu)

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _user(UserRole.ANALYST)

    async def fake_db() -> object:
        yield fake_session

    app.dependency_overrides[get_db] = fake_db
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                other = uuid.uuid4()
                r = await client.get(
                    f"/audit-events?normalized_update_id={uuid.uuid4()}&run_id={other}"
                )
        assert r.status_code == 400
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_audit_search_run_id_with_normalized_update_no_run_on_row_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")
    nu = MagicMock()
    nu.run_id = None
    nu.source_id = uuid.uuid4()
    nu.created_at = datetime.now(timezone.utc)

    fake_session = MagicMock()
    fake_session.scalar = AsyncMock(return_value=nu)

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _user(UserRole.ANALYST)

    async def fake_db() -> object:
        yield fake_session

    app.dependency_overrides[get_db] = fake_db
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get(
                    f"/audit-events?normalized_update_id={uuid.uuid4()}&run_id={uuid.uuid4()}"
                )
        assert r.status_code == 400
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_audit_search_integration_filters_and_pagination(
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
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session_mod._engine = engine  # type: ignore[attr-defined]
    session_mod._session_factory = factory  # type: ignore[attr-defined]

    suffix = uuid.uuid4().hex[:8]
    email = f"audit-{suffix}@example.com"
    password = "SecretPass1Ab"
    user_uuid: uuid.UUID | None = None
    nu_id: uuid.UUID | None = None
    nu_no_run_id: uuid.UUID | None = None
    rc_id: uuid.UUID | None = None
    rc2_id: uuid.UUID | None = None
    source_id = uuid.uuid4()
    run_a = uuid.uuid4()
    run_b = uuid.uuid4()
    t_base = datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc)

    app = create_app()
    transport = ASGITransport(app=app)
    sync_engine = create_engine(sync_url)

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            reg = await client.post(
                "/auth/register", json={"email": email, "password": password}
            )
            assert reg.status_code == 201, reg.text

        with sync_engine.begin() as conn:
            conn.execute(
                text("UPDATE users SET role = 'viewer' WHERE email = :e"),
                {"e": email},
            )
            uid_row = conn.execute(
                text("SELECT id FROM users WHERE email = :e"),
                {"e": email},
            ).one()
            user_uuid = uuid.UUID(str(uid_row[0]))

        actor = user_uuid

        run_x = uuid.uuid4()
        rc2_id = uuid.uuid4()
        nu_no_run_id = uuid.uuid4()

        # SQLAlchemy may emit INSERTs in FK-unsafe order when multiple pending parents exist
        # (e.g. raw_captures before sources, or normalized_updates before raw_captures).
        # Flush after each parent row. Heuristic raw→normalized stays in a second transaction
        # so it is not batched with pending AuditEvents.
        async with factory() as session:
            session.add(
                Source(
                    id=source_id,
                    name=f"SRC-{suffix}",
                    jurisdiction="US",
                    source_type=SourceType.RSS,
                    primary_url=f"https://example.test/{suffix}/feed.xml",
                    schedule="0 * * * *",
                )
            )
            await session.flush()
            rc_id = uuid.uuid4()
            session.add(
                RawCapture(
                    id=rc_id,
                    source_id=source_id,
                    captured_at=t_base,
                    item_url=f"https://example.test/{suffix}/item",
                    payload={"x": suffix},
                )
            )
            await session.flush()
            nu_id = uuid.uuid4()
            session.add(
                NormalizedUpdateRow(
                    id=nu_id,
                    raw_capture_id=rc_id,
                    source_id=source_id,
                    source_name=f"SRC-{suffix}",
                    jurisdiction="US",
                    title="t",
                    published_at=None,
                    item_url=f"https://example.test/{suffix}/item",
                    document_type="guidance",
                    run_id=run_a,
                    created_at=t_base,
                )
            )
            await session.flush()
            await audit_events_repo.append_audit_event(
                session,
                run_id=run_a,
                action=PipelineAuditAction.PIPELINE_SCOUT_COMPLETED,
                source_id=source_id,
                metadata={"k": "a"},
            )
            await audit_events_repo.append_audit_event(
                session,
                run_id=run_a,
                action=PipelineAuditAction.PIPELINE_NORMALIZE_COMPLETED,
                source_id=source_id,
                metadata={"k": "b"},
            )
            await audit_events_repo.append_audit_event(
                session,
                run_id=run_b,
                action=PipelineAuditAction.BRIEFING_GENERATED,
                source_id=source_id,
                metadata={"k": "c"},
            )
            await audit_events_repo.append_audit_event(
                session,
                run_id=run_b,
                action=PipelineAuditAction.HUMAN_REVIEW_APPROVED,
                source_id=source_id,
                metadata={"k": "d"},
                actor_user_id=actor,
            )
            await session.commit()

        async with factory() as session:
            session.add(
                RawCapture(
                    id=rc2_id,
                    source_id=source_id,
                    captured_at=t_base,
                    item_url=f"https://example.test/{suffix}/item-heuristic",
                    payload={"x": f"{suffix}-heuristic"},
                )
            )
            await session.flush()
            session.add(
                NormalizedUpdateRow(
                    id=nu_no_run_id,
                    raw_capture_id=rc2_id,
                    source_id=source_id,
                    source_name=f"SRC-{suffix}",
                    jurisdiction="US",
                    title="heuristic-nu",
                    published_at=None,
                    item_url=f"https://example.test/{suffix}/item-heuristic",
                    document_type="guidance",
                    run_id=None,
                    created_at=t_base,
                )
            )
            await session.flush()
            aid_in = await audit_events_repo.append_audit_event(
                session,
                run_id=run_x,
                action=PipelineAuditAction.PIPELINE_CLASSIFY_COMPLETED,
                source_id=source_id,
                metadata={"window": "in"},
            )
            aid_out = await audit_events_repo.append_audit_event(
                session,
                run_id=run_x,
                action=PipelineAuditAction.BRIEFING_GENERATED,
                source_id=source_id,
                metadata={"window": "out"},
            )
            assert aid_in is not None and aid_out is not None
            await session.execute(
                text("UPDATE audit_events SET created_at = :t WHERE id = CAST(:id AS uuid)"),
                {"t": t_base, "id": str(aid_in)},
            )
            await session.execute(
                text("UPDATE audit_events SET created_at = :t WHERE id = CAST(:id AS uuid)"),
                {
                    "t": t_base + timedelta(hours=25),
                    "id": str(aid_out),
                },
            )
            await session.commit()

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            login = await client.post(
                "/auth/login",
                json={"email": email, "password": password},
            )
            assert login.status_code == 200, login.text
            token = login.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}

            r_all = await client.get("/audit-events?limit=10&offset=0", headers=headers)
            assert r_all.status_code == 200, r_all.text
            body = r_all.json()
            assert body["total"] >= 4
            assert len(body["items"]) >= 4

            r_run = await client.get(
                f"/audit-events?run_id={run_a}&limit=50",
                headers=headers,
            )
            assert r_run.status_code == 200
            jr = r_run.json()
            assert jr["total"] == 2
            assert {x["action"] for x in jr["items"]} == {
                PipelineAuditAction.PIPELINE_SCOUT_COMPLETED.value,
                PipelineAuditAction.PIPELINE_NORMALIZE_COMPLETED.value,
            }
            with sync_engine.begin() as conn:
                want_ids = [
                    row[0]
                    for row in conn.execute(
                        text(
                            "SELECT id::text FROM audit_events WHERE run_id = CAST(:r AS uuid) "
                            "ORDER BY created_at DESC, id DESC"
                        ),
                        {"r": str(run_a)},
                    )
                ]
            assert [x["id"] for x in jr["items"]] == want_ids

            r_act = await client.get(
                "/audit-events?action=human_review_approved",
                headers=headers,
            )
            assert r_act.status_code == 200
            ja = r_act.json()
            assert ja["total"] >= 1
            assert all(x["action"] == "human_review_approved" for x in ja["items"])

            r_actor = await client.get(
                f"/audit-events?actor_user_id={actor}",
                headers=headers,
            )
            assert r_actor.status_code == 200
            assert r_actor.json()["total"] >= 1

            # Audit rows from append_audit_event use server ``now()``, not ``t_base``; bracket
            # wall-clock time so time-range filters include this test's inserts.
            now_utc = datetime.now(timezone.utc)
            t0 = (now_utc - timedelta(hours=1)).isoformat()
            t1 = (now_utc + timedelta(hours=1)).isoformat()
            r_time = await client.get(
                "/audit-events",
                params={
                    "source_id": str(source_id),
                    "created_after": t0,
                    "created_before": t1,
                },
                headers=headers,
            )
            assert r_time.status_code == 200
            assert r_time.json()["total"] >= 2

            r_page = await client.get(
                "/audit-events",
                params={
                    "limit": 1,
                    "offset": 0,
                    "source_id": str(source_id),
                    "created_after": t0,
                    "created_before": t1,
                },
                headers=headers,
            )
            assert r_page.status_code == 200
            jp = r_page.json()
            assert jp["total"] >= 2
            assert len(jp["items"]) == 1

            r_nu = await client.get(
                f"/audit-events?normalized_update_id={nu_id}",
                headers=headers,
            )
            assert r_nu.status_code == 200
            jn = r_nu.json()
            assert jn["total"] == 2

            r_nu_null_run = await client.get(
                f"/audit-events?normalized_update_id={nu_no_run_id}",
                headers=headers,
            )
            assert r_nu_null_run.status_code == 200
            jh = r_nu_null_run.json()
            assert jh["total"] == 1
            assert jh["items"][0]["action"] == PipelineAuditAction.PIPELINE_CLASSIFY_COMPLETED.value

            r_nu_conflict = await client.get(
                f"/audit-events?normalized_update_id={nu_no_run_id}&run_id={run_x}",
                headers=headers,
            )
            assert r_nu_conflict.status_code == 400

            r_bad = await client.get(
                "/audit-events?normalized_update_id=00000000-0000-0000-0000-000000000099",
                headers=headers,
            )
            assert r_bad.status_code == 404

            # Analyst token: still 200 (403 never for authenticated roles).
            with sync_engine.begin() as conn:
                conn.execute(
                    text("UPDATE users SET role = 'analyst' WHERE email = :e"),
                    {"e": email},
                )
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            login2 = await client.post(
                "/auth/login",
                json={"email": email, "password": password},
            )
            tok2 = login2.json()["access_token"]
            h2 = {"Authorization": f"Bearer {tok2}"}
            r_an = await client.get("/audit-events?limit=1", headers=h2)
            assert r_an.status_code == 200

    finally:
        try:
            with sync_engine.begin() as conn:
                conn.execute(text("DELETE FROM audit_events WHERE source_id = :s"), {"s": str(source_id)})
                if nu_id is not None:
                    conn.execute(
                        text("DELETE FROM normalized_updates WHERE id = :n"),
                        {"n": str(nu_id)},
                    )
                if nu_no_run_id is not None:
                    conn.execute(
                        text("DELETE FROM normalized_updates WHERE id = :n"),
                        {"n": str(nu_no_run_id)},
                    )
                if rc_id is not None:
                    conn.execute(
                        text("DELETE FROM raw_captures WHERE id = :r"),
                        {"r": str(rc_id)},
                    )
                if rc2_id is not None:
                    conn.execute(
                        text("DELETE FROM raw_captures WHERE id = :r"),
                        {"r": str(rc2_id)},
                    )
                conn.execute(text("DELETE FROM sources WHERE id = :s"), {"s": str(source_id)})
                if user_uuid is not None:
                    conn.execute(text("DELETE FROM users WHERE id = :u"), {"u": str(user_uuid)})
        except Exception:
            pass
        await engine.dispose()
        session_mod._engine = None  # type: ignore[attr-defined]
        session_mod._session_factory = None  # type: ignore[attr-defined]
