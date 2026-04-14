"""Auth: password policy, hashing, and API integration (Postgres optional)."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from sentinel_prism.api.routes.auth import RegisterRequest
from sentinel_prism.main import create_app
from sentinel_prism.services.auth.passwords import hash_password, verify_password

ROOT = Path(__file__).resolve().parents[1]


def test_password_policy_too_short() -> None:
    with pytest.raises(ValidationError) as exc:
        RegisterRequest(email="a@b.com", password="short1Aa")
    assert "12" in str(exc.value).lower() or "password" in str(exc.value).lower()


def test_password_policy_requires_classes() -> None:
    with pytest.raises(ValidationError):
        RegisterRequest(email="a@b.com", password="nouppercase12345")
    with pytest.raises(ValidationError):
        RegisterRequest(email="a@b.com", password="NOLOWERCASE12345")
    with pytest.raises(ValidationError):
        RegisterRequest(email="a@b.com", password="NoDigitsHereAb")


def test_hash_verify_roundtrip() -> None:
    h = hash_password("ValidPass12345")
    assert h != "ValidPass12345"
    assert verify_password("ValidPass12345", h)
    assert not verify_password("wrong", h)


@pytest.mark.integration
async def test_register_login_me_and_unauthorized(monkeypatch: pytest.MonkeyPatch) -> None:
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

    # Fresh engine per process: sentinel_prism.db.session caches engine; reload module state.
    import sentinel_prism.db.session as session_mod

    session_mod._engine = None  # type: ignore[attr-defined]
    session_mod._session_factory = None  # type: ignore[attr-defined]

    app = create_app()
    suffix = uuid.uuid4().hex[:12]
    email = f"user{suffix}@example.com"
    password = f"SecretPass1{suffix[:4]}"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        reg = await client.post(
            "/auth/register", json={"email": email, "password": password}
        )
        assert reg.status_code == 201, reg.text
        user_id = reg.json()["id"]

        bad_me = await client.get("/auth/me")
        assert bad_me.status_code == 401

        login = await client.post(
            "/auth/login", json={"email": email, "password": password}
        )
        assert login.status_code == 200, login.text
        body = login.json()
        assert body["user_id"] == user_id
        assert "access_token" in body

        me = await client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {body['access_token']}"},
        )
        assert me.status_code == 200
        assert me.json()["id"] == user_id
        assert me.json()["email"] == email.lower()


@pytest.mark.integration
async def test_login_with_stub_provider_always_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_url = os.environ.get("DATABASE_URL", "").strip()
    sync_url = os.environ.get("ALEMBIC_SYNC_URL", "").strip()
    if not db_url or not sync_url:
        pytest.skip("DATABASE_URL and ALEMBIC_SYNC_URL required for integration")

    monkeypatch.setenv("JWT_SECRET", "integration-test-jwt-secret-32chars-min")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")
    monkeypatch.setenv("AUTH_PROVIDER", "stub")

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

    app = create_app()
    suffix = uuid.uuid4().hex[:12]
    email = f"stub{suffix}@example.com"
    password = f"SecretPass1{suffix[:4]}"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        reg = await client.post(
            "/auth/register", json={"email": email, "password": password}
        )
        assert reg.status_code == 201, reg.text

        login = await client.post(
            "/auth/login", json={"email": email, "password": password}
        )
        assert login.status_code == 401
