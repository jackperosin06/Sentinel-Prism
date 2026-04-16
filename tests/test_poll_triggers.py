"""Scheduled / manual poll triggers (Story 2.2)."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text

from sentinel_prism.main import create_app
from sentinel_prism.services.sources.schedule import validate_cron_expression
from sentinel_prism.workers.poll_scheduler import poll_job_id, reset_poll_scheduler_for_tests

ROOT = Path(__file__).resolve().parents[1]


def test_validate_cron_accepts_five_field() -> None:
    assert validate_cron_expression(" 0 * * * * ") == "0 * * * *"


def test_validate_cron_rejects_invalid() -> None:
    with pytest.raises(ValueError, match="Invalid cron"):
        validate_cron_expression("not-valid-cron")


def test_poll_unauthenticated_returns_401(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "integration-test-jwt-secret-32chars-min")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

    app = create_app()
    transport = ASGITransport(app=app)

    async def _run() -> None:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(f"/sources/{uuid.uuid4()}/poll")
            assert r.status_code == 401

    asyncio.run(_run())


@pytest.mark.integration
async def test_poll_manual_rbac_cron_and_scheduler_job(
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
    reset_poll_scheduler_for_tests()

    suffix = uuid.uuid4().hex[:10]
    pw = "SecretPass1Ab"
    emails = {
        "viewer": f"pv{suffix}@example.com",
        "admin": f"pa{suffix}@example.com",
    }

    app = create_app()
    transport = ASGITransport(app=app)

    # httpx ASGITransport only sends ``http`` scope; FastAPI ``lifespan`` (poll scheduler
    # startup) runs only when lifespan ASGI events are delivered — same gap as uvicorn.
    async with LifespanManager(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            for em in emails.values():
                reg = await client.post(
                    "/auth/register", json={"email": em, "password": pw}
                )
                assert reg.status_code == 201, reg.text

        engine = create_engine(sync_url)
        with engine.begin() as conn:
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

        valid_payload = {
            "name": f"Poll test {suffix}",
            "jurisdiction": "EU",
            "source_type": "rss",
            "primary_url": "https://example.com/feed.xml",
            "schedule": "0 * * * *",
        }

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            h_viewer = {"Authorization": f"Bearer {tokens['viewer']}"}
            h_admin = {"Authorization": f"Bearer {tokens['admin']}"}

            bad_cron = await client.post(
                "/sources",
                headers=h_admin,
                json={**valid_payload, "name": f"Bad cron {suffix}", "schedule": "nope"},
            )
            assert bad_cron.status_code == 422

            cre = await client.post("/sources", headers=h_admin, json=valid_payload)
            assert cre.status_code == 201, cre.text
            sid = cre.json()["id"]
            source_uuid = uuid.UUID(sid)

            from sentinel_prism.workers.poll_scheduler import get_poll_scheduler

            sched = get_poll_scheduler()
            assert sched.started
            assert poll_job_id(source_uuid) in sched.poll_job_ids()

            poll_denied = await client.post(
                f"/sources/{sid}/poll",
                headers=h_viewer,
            )
            assert poll_denied.status_code == 403

            poll_ok = await client.post(f"/sources/{sid}/poll", headers=h_admin)
            assert poll_ok.status_code == 202, poll_ok.text
            body = poll_ok.json()
            assert body["status"] == "accepted"
            assert body["source_id"] == sid

            missing = await client.post(
                f"/sources/{uuid.uuid4()}/poll",
                headers=h_admin,
            )
            assert missing.status_code == 404

            disabled = await client.patch(
                f"/sources/{sid}",
                headers=h_admin,
                json={"enabled": False},
            )
            assert disabled.status_code == 200
            assert poll_job_id(source_uuid) not in sched.poll_job_ids()

            conflict = await client.post(f"/sources/{sid}/poll", headers=h_admin)
            assert conflict.status_code == 409

            bad_patch = await client.patch(
                f"/sources/{sid}",
                headers=h_admin,
                json={"schedule": "invalid"},
            )
            assert bad_patch.status_code == 422

            re_enable = await client.patch(
                f"/sources/{sid}",
                headers=h_admin,
                json={"enabled": True, "schedule": "15 * * * *"},
            )
            assert re_enable.status_code == 200

            poll_again = await client.post(f"/sources/{sid}/poll", headers=h_admin)
            assert poll_again.status_code == 202

            deleted = await client.delete(f"/sources/{sid}", headers=h_admin)
            assert deleted.status_code == 204
            assert poll_job_id(source_uuid) not in sched.poll_job_ids()

    reset_poll_scheduler_for_tests()
