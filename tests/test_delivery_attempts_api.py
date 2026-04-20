"""Admin delivery attempts API (Story 5.3 — NFR10)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from sentinel_prism.api.deps import get_current_user, get_db_for_admin
from sentinel_prism.db.models import (
    NotificationDeliveryChannel,
    NotificationDeliveryOutcome,
    User,
    UserRole,
)
from sentinel_prism.main import create_app


def _admin_user() -> User:
    return User(
        id=uuid.uuid4(),
        email="admin@test.local",
        password_hash="x",
        role=UserRole.ADMIN,
        team_slug=None,
        is_active=True,
    )


def _analyst_user() -> User:
    return User(
        id=uuid.uuid4(),
        email="a@test.local",
        password_hash="x",
        role=UserRole.ANALYST,
        team_slug="team-a",
        is_active=True,
    )


@pytest.mark.asyncio
async def test_delivery_attempts_unauthorized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")
    app = create_app()
    transport = ASGITransport(app=app)
    async with LifespanManager(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/admin/delivery-attempts")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_delivery_attempts_forbidden_for_analyst(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

    app = create_app()
    app.dependency_overrides[get_current_user] = _analyst_user
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get("/admin/delivery-attempts")
        assert r.status_code == 403
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_delivery_attempts_admin_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

    captured: dict[str, object] = {}

    class _Row:
        id = uuid.uuid4()
        run_id = uuid.uuid4()
        item_url = "https://reg/item"
        channel = NotificationDeliveryChannel.SMTP
        outcome = NotificationDeliveryOutcome.SUCCESS
        error_class = None
        detail = None
        provider_message_id = None
        recipient_descriptor = "u@test.local"
        created_at = datetime.now(timezone.utc)

    async def fake_list(_db: object, **kwargs: object) -> tuple[list, bool]:
        captured.update(kwargs)
        return ([_Row()], False)

    monkeypatch.setattr(
        "sentinel_prism.api.routes.delivery_attempts.delivery_repo.list_attempts",
        fake_list,
    )

    async def fake_admin_db() -> None:
        yield MagicMock()

    app = create_app()
    app.dependency_overrides[get_current_user] = _admin_user
    app.dependency_overrides[get_db_for_admin] = fake_admin_db
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get(
                    "/admin/delivery-attempts",
                    params={"outcome": "success", "run_id": str(_Row.run_id)},
                )
        assert r.status_code == 200
        body = r.json()
        assert body["has_more"] is False
        assert len(body["items"]) == 1
        assert body["items"][0]["outcome"] == "success"
        assert captured.get("outcome") == "success"
        assert captured.get("run_id") == _Row.run_id
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_delivery_attempts_naive_datetime_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

    async def fake_admin_db() -> None:
        yield MagicMock()

    app = create_app()
    app.dependency_overrides[get_current_user] = _admin_user
    app.dependency_overrides[get_db_for_admin] = fake_admin_db
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get(
                    "/admin/delivery-attempts",
                    params={"created_after": "2026-04-21T12:00:00"},
                )
        assert r.status_code == 422
        assert "timezone-aware" in r.json()["detail"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_delivery_attempts_inverted_range_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

    async def fake_admin_db() -> None:
        yield MagicMock()

    app = create_app()
    app.dependency_overrides[get_current_user] = _admin_user
    app.dependency_overrides[get_db_for_admin] = fake_admin_db
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get(
                    "/admin/delivery-attempts",
                    params={
                        "created_after": "2026-04-22T00:00:00+00:00",
                        "created_before": "2026-04-21T00:00:00+00:00",
                    },
                )
        assert r.status_code == 422
        assert "created_after" in r.json()["detail"]
    finally:
        app.dependency_overrides.clear()
