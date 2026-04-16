"""Source registry API (Story 2.1) — integration optional."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text

from sentinel_prism.main import create_app

ROOT = Path(__file__).resolve().parents[1]


def test_sources_unauthenticated_returns_401(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "integration-test-jwt-secret-32chars-min")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

    app = create_app()
    transport = ASGITransport(app=app)

    async def _run() -> None:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/sources")
            assert r.status_code == 401
            r2 = await client.get(f"/sources/{uuid.uuid4()}")
            assert r2.status_code == 401

    asyncio.run(_run())


@pytest.mark.integration
async def test_sources_admin_crud_and_rbac_matrix(
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

    suffix = uuid.uuid4().hex[:10]
    pw = "SecretPass1Ab"
    emails = {
        "viewer": f"v{suffix}@example.com",
        "analyst": f"a{suffix}@example.com",
        "admin": f"m{suffix}@example.com",
    }

    app = create_app()
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for em in emails.values():
            reg = await client.post(
                "/auth/register", json={"email": em, "password": pw}
            )
            assert reg.status_code == 201, reg.text

    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE users SET role = 'analyst' WHERE email = :e"),
            {"e": emails["analyst"]},
        )
        conn.execute(
            text("UPDATE users SET role = 'admin' WHERE email = :e"),
            {"e": emails["admin"]},
        )

    tokens: dict[str, str] = {}
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for key, em in emails.items():
            login = await client.post(
                "/auth/login", json={"email": em, "password": pw}
            )
            assert login.status_code == 200, login.text
            tokens[key] = login.json()["access_token"]

    payload = {
        "name": f"EMA RSS {suffix}",
        "jurisdiction": "EU",
        "source_type": "rss",
        "primary_url": "https://example.com/feed.xml",
        "schedule": "0 * * * *",
    }

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # viewer / analyst: 403 on all source routes
        for key in ("viewer", "analyst"):
            h = {"Authorization": f"Bearer {tokens[key]}"}
            assert (await client.get("/sources", headers=h)).status_code == 403
            assert (
                await client.post("/sources", headers=h, json=payload)
            ).status_code == 403
            assert (
                await client.get(f"/sources/{uuid.uuid4()}", headers=h)
            ).status_code == 403
            assert (
                await client.patch(
                    f"/sources/{uuid.uuid4()}",
                    headers=h,
                    json={"name": "x"},
                )
            ).status_code == 403
            assert (
                await client.post(
                    f"/sources/{uuid.uuid4()}/poll",
                    headers=h,
                )
            ).status_code == 403
            assert (
                await client.delete(f"/sources/{uuid.uuid4()}", headers=h)
            ).status_code == 403

        h_admin = {"Authorization": f"Bearer {tokens['admin']}"}

        bad = await client.post("/sources", headers=h_admin, json={"name": "only"})
        assert bad.status_code == 422

        cre = await client.post("/sources", headers=h_admin, json=payload)
        assert cre.status_code == 201, cre.text
        body = cre.json()
        sid = body["id"]
        assert body["name"] == payload["name"]
        assert body["enabled"] is True

        lst = await client.get("/sources", headers=h_admin)
        assert lst.status_code == 200
        source_ids = [s["id"] for s in lst.json()]
        assert sid in source_ids

        one = await client.get(f"/sources/{sid}", headers=h_admin)
        assert one.status_code == 200
        assert one.json()["primary_url"] == payload["primary_url"]

        missing = await client.get(
            f"/sources/{uuid.uuid4()}",
            headers=h_admin,
        )
        assert missing.status_code == 404

        updated_name = f"EMA RSS Updated {suffix}"
        patched = await client.patch(
            f"/sources/{sid}",
            headers=h_admin,
            json={"name": updated_name, "enabled": False},
        )
        assert patched.status_code == 200
        assert patched.json()["name"] == updated_name
        assert patched.json()["enabled"] is False

        # admin DELETE: 404 for unknown, 204 for known
        ghost_delete = await client.delete(
            f"/sources/{uuid.uuid4()}", headers=h_admin
        )
        assert ghost_delete.status_code == 404

        deleted = await client.delete(f"/sources/{sid}", headers=h_admin)
        assert deleted.status_code == 204

        gone = await client.get(f"/sources/{sid}", headers=h_admin)
        assert gone.status_code == 404
