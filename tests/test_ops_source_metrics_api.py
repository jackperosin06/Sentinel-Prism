"""Ops / observability endpoints (Story 8.3 — NFR8/NFR9)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from sentinel_prism.api.deps import get_current_user, get_db
from sentinel_prism.db.models import Source, SourceType, User, UserRole
from sentinel_prism.db.repositories import sources as sources_repo
from sentinel_prism.main import create_app


FIXED_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")


def _user(role: UserRole) -> User:
    return User(
        id=FIXED_ID,
        email="ops@test.local",
        password_hash="x",
        role=role,
        team_slug=None,
        is_active=True,
    )


def _source(name: str, *, success: int, failed: int, total: int) -> Source:
    return Source(
        id=uuid.uuid4(),
        name=name,
        jurisdiction="US",
        source_type=SourceType.RSS,
        primary_url="https://example.com/rss",
        schedule="0 * * * *",
        poll_attempts_success=success,
        poll_attempts_failed=failed,
        items_ingested_total=total,
        enabled=True,
        extra_metadata=None,
    )


@pytest.mark.asyncio
async def test_ops_source_metrics_unauthenticated_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")
    app = create_app()
    transport = ASGITransport(app=app)
    async with LifespanManager(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/ops/source-metrics")
    assert r.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.ANALYST])
async def test_ops_source_metrics_admin_and_analyst_200(
    monkeypatch: pytest.MonkeyPatch,
    role: UserRole,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

    async def fake_list_sources(*_a: object, **_k: object) -> list[Source]:
        return [
            _source("Alpha", success=10, failed=0, total=123),
            _source("Beta", success=5, failed=5, total=55),
        ]

    monkeypatch.setattr(sources_repo, "list_sources", fake_list_sources)

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
                r = await client.get("/ops/source-metrics?limit=100&offset=0")
        assert r.status_code == 200, r.text
        body = r.json()
        assert isinstance(body, list)
        assert {x["name"] for x in body} == {"Alpha", "Beta"}
        # NFR8 request_id should be present on every HTTP response.
        rid = r.headers.get("x-request-id")
        assert rid is not None
        uuid.UUID(rid)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_ops_source_metrics_viewer_403(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _user(UserRole.VIEWER)

    fake_session = MagicMock()
    fake_session.scalar = AsyncMock(return_value=None)

    async def fake_db() -> object:
        yield fake_session

    app.dependency_overrides[get_db] = fake_db
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get("/ops/source-metrics")
        assert r.status_code == 403
    finally:
        app.dependency_overrides.clear()

