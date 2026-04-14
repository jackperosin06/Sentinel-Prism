"""RBAC: dependency unit tests and integration matrix (Postgres optional)."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text

from sentinel_prism.api.deps import get_current_user
from sentinel_prism.api.routes import rbac_demo
from sentinel_prism.db.models import User, UserRole

ROOT = Path(__file__).resolve().parents[1]


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


def _admin_user() -> User:
    return User(
        id=uuid.uuid4(),
        email="admin@test.local",
        password_hash="x",
        role=UserRole.ADMIN,
        is_active=True,
    )


@pytest.fixture
def rbac_app() -> FastAPI:
    """Minimal app with only rbac_demo routes — not part of the production app."""
    app = FastAPI()
    app.include_router(rbac_demo.router)
    return app


@pytest.mark.parametrize(
    ("user_factory", "path", "expected"),
    [
        (_viewer_user, "/rbac-demo/admin-only", 403),
        (_analyst_user, "/rbac-demo/admin-only", 403),
        (_admin_user, "/rbac-demo/admin-only", 200),
        (_viewer_user, "/rbac-demo/analyst-or-above", 403),
        (_analyst_user, "/rbac-demo/analyst-or-above", 200),
        (_admin_user, "/rbac-demo/analyst-or-above", 200),
        (_viewer_user, "/rbac-demo/authenticated", 200),
        (_analyst_user, "/rbac-demo/authenticated", 200),
        (_admin_user, "/rbac-demo/authenticated", 200),
    ],
)
def test_rbac_demo_dependency_matrix(
    rbac_app: FastAPI,
    user_factory: Callable[[], User],
    path: str,
    expected: int,
) -> None:
    user = user_factory()

    async def _override() -> User:
        return user

    rbac_app.dependency_overrides[get_current_user] = _override
    transport = ASGITransport(app=rbac_app)

    async def _run() -> None:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get(path)
            assert r.status_code == expected, r.text

    try:
        asyncio.run(_run())
    finally:
        rbac_app.dependency_overrides.clear()


@pytest.mark.integration
async def test_rbac_integration_role_matrix(monkeypatch: pytest.MonkeyPatch) -> None:
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

    # Integration test app: include rbac_demo routes (not part of production app)
    from sentinel_prism.main import create_app

    app = create_app()
    app.include_router(rbac_demo.router)
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

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # viewer: 403 on restricted routes, 200 on any-authenticated
        h = {"Authorization": f"Bearer {tokens['viewer']}"}
        r = await client.get("/rbac-demo/admin-only", headers=h)
        assert r.status_code == 403
        assert r.json()["detail"] == "Insufficient permissions"
        r = await client.get("/rbac-demo/analyst-or-above", headers=h)
        assert r.status_code == 403
        assert r.json()["detail"] == "Insufficient permissions"
        assert (await client.get("/rbac-demo/authenticated", headers=h)).status_code == 200

        # analyst: 403 on admin-only, 200 on analyst-or-above and authenticated
        h = {"Authorization": f"Bearer {tokens['analyst']}"}
        assert (await client.get("/rbac-demo/admin-only", headers=h)).status_code == 403
        assert (await client.get("/rbac-demo/analyst-or-above", headers=h)).status_code == 200
        assert (await client.get("/rbac-demo/authenticated", headers=h)).status_code == 200

        # admin: 200 on all routes
        h = {"Authorization": f"Bearer {tokens['admin']}"}
        assert (await client.get("/rbac-demo/admin-only", headers=h)).status_code == 200
        assert (await client.get("/rbac-demo/analyst-or-above", headers=h)).status_code == 200
        assert (await client.get("/rbac-demo/authenticated", headers=h)).status_code == 200

        # unauthenticated: 401
        assert (await client.get("/rbac-demo/admin-only")).status_code == 401
