"""Per-source ingestion metrics API (Story 2.6 — NFR9) — integration optional."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sentinel_prism.db.models import Source, SourceType
from sentinel_prism.db.repositories import sources as sources_repo
from sentinel_prism.main import create_app

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.integration
async def test_metrics_api_reflects_counters_and_failure_metadata(
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
    suffix = uuid.uuid4().hex[:10]
    pw = "SecretPass1Ab"

    app = create_app()
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        reg = await client.post(
            "/auth/register",
            json={"email": f"adm{suffix}@example.com", "password": pw},
        )
        assert reg.status_code == 201, reg.text

    from sqlalchemy import create_engine, text

    eng = create_engine(sync_url)
    with eng.begin() as conn:
        conn.execute(
            text("UPDATE users SET role = 'admin' WHERE email = :e"),
            {"e": f"adm{suffix}@example.com"},
        )

    token: str
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post(
            "/auth/login",
            json={"email": f"adm{suffix}@example.com", "password": pw},
        )
        assert login.status_code == 200, login.text
        token = login.json()["access_token"]

    sid: uuid.UUID
    async with factory() as session:
        src = Source(
            name=f"metrics-src-{suffix}",
            jurisdiction="EU",
            source_type=SourceType.RSS,
            primary_url="https://example.com/feed.xml",
            schedule="0 * * * *",
            enabled=True,
        )
        session.add(src)
        await session.commit()
        sid = src.id

        await sources_repo.record_poll_failure(
            session,
            sid,
            reason="connector down",
            error_class="HTTPStatusError",
        )
        await session.commit()

    headers = {"Authorization": f"Bearer {token}"}
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(f"/sources/{sid}/metrics", headers=headers)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["poll_attempts_failed"] == 1
        assert data["poll_attempts_success"] == 0
        assert data["items_ingested_total"] == 0
        assert data["success_rate"] == 0.0
        assert data["error_rate"] == 1.0
        assert data["last_success_at"] is None
        assert data["last_poll_failure"] is not None
        assert data["last_poll_failure"]["error_class"] == "HTTPStatusError"

        r_list = await client.get("/sources/metrics", headers=headers)
        assert r_list.status_code == 200, r_list.text
        by_id = {x["source_id"]: x for x in r_list.json()}
        assert str(sid) in by_id
        assert by_id[str(sid)]["poll_attempts_failed"] == 1

    from datetime import datetime, timezone

    async with factory() as session:
        await sources_repo.clear_poll_failure(session, sid)
        await sources_repo.record_poll_success_metrics(
            session,
            sid,
            items_new_count=2,
            latency_ms=150,
            fetch_path="primary",
            fetched_at=datetime.now(timezone.utc),
        )
        await session.commit()

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r2 = await client.get(f"/sources/{sid}/metrics", headers=headers)
        assert r2.status_code == 200, r2.text
        d2 = r2.json()
        assert d2["poll_attempts_success"] == 1
        assert d2["poll_attempts_failed"] == 1
        assert d2["items_ingested_total"] == 2
        assert d2["last_success_latency_ms"] == 150
        assert d2["last_success_fetch_path"] == "primary"
        assert d2["last_success_at"] is not None
        assert d2["last_poll_failure"] is None
        assert abs(d2["success_rate"] - 0.5) < 1e-9
        assert abs(d2["error_rate"] - 0.5) < 1e-9

    await engine.dispose()


def test_metrics_routes_require_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "integration-test-jwt-secret-32chars-min")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")
    app = create_app()
    transport = ASGITransport(app=app)

    async def _run() -> None:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/sources/metrics")
            assert r.status_code == 401
            r2 = await client.get(f"/sources/{uuid.uuid4()}/metrics")
            assert r2.status_code == 401

    asyncio.run(_run())
