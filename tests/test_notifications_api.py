"""In-app notifications API (Story 5.2 — FR24)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from sentinel_prism.api.deps import get_current_user
from sentinel_prism.db.models import InAppNotification, User, UserRole
from sentinel_prism.db.session import get_db as session_get_db
from sentinel_prism.main import create_app


def _analyst_user() -> User:
    return User(
        id=uuid.uuid4(),
        email="a@test.local",
        password_hash="x",
        role=UserRole.ANALYST,
        team_slug="team-a",
        is_active=True,
    )


def _viewer_user() -> User:
    return User(
        id=uuid.uuid4(),
        email="v@test.local",
        password_hash="x",
        role=UserRole.VIEWER,
        team_slug="team-a",
        is_active=True,
    )


def _make_row(
    *,
    user_id: uuid.UUID,
    title: str = "Critical routed update",
    read_at: datetime | None = None,
) -> InAppNotification:
    """Build a real ``InAppNotification`` ORM instance (not a ``SimpleNamespace``)
    so ``NotificationOut.model_validate`` exercises the actual attribute surface
    and would catch a missing / renamed column."""

    return InAppNotification(
        id=uuid.uuid4(),
        user_id=user_id,
        run_id=uuid.uuid4(),
        item_url="https://reg/item",
        team_slug="team-a",
        severity="critical",
        title=title,
        body="https://reg/item",
        read_at=read_at,
        created_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_list_notifications_unauthorized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")
    app = create_app()
    transport = ASGITransport(app=app)
    async with LifespanManager(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/notifications")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_list_notifications_scopes_query_to_current_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC #5 — the list endpoint must pass ``user_id=current.id`` to the
    repository. This test captures the args the repo is called with and
    asserts the scoping, so a refactor that drops the ``user_id=`` kwarg
    (or swaps it for a different user) would fail here."""

    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

    async def fake_session() -> None:
        yield MagicMock()

    current = _analyst_user()

    captured: dict[str, object] = {}

    async def capture_list(_db: object, **kwargs: object) -> tuple[list, bool]:
        captured.update(kwargs)
        return ([_make_row(user_id=current.id)], False)

    monkeypatch.setattr(
        "sentinel_prism.api.routes.notifications.in_app_repo.list_for_user",
        capture_list,
    )

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: current
    app.dependency_overrides[session_get_db] = fake_session
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get("/notifications")
        assert r.status_code == 200
        assert captured.get("user_id") == current.id
        assert captured.get("unread_only") is False
        body = r.json()
        assert body["has_more"] is False
        assert len(body["items"]) == 1
        # Ensure model_validate(from_attributes=True) surfaces the new
        # fields in the response (guards against silent schema drift).
        item = body["items"][0]
        assert item["severity"] == "critical"
        assert item["team_slug"] == "team-a"
        assert item["run_id"]
        assert item["item_url"] == "https://reg/item"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_list_notifications_forwards_unread_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``?unread=true`` must flow through to ``list_for_user(unread_only=True)``."""

    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

    async def fake_session() -> None:
        yield MagicMock()

    current = _analyst_user()
    captured: dict[str, object] = {}

    async def capture_list(_db: object, **kwargs: object) -> tuple[list, bool]:
        captured.update(kwargs)
        return ([], False)

    monkeypatch.setattr(
        "sentinel_prism.api.routes.notifications.in_app_repo.list_for_user",
        capture_list,
    )

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: current
    app.dependency_overrides[session_get_db] = fake_session
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get("/notifications?unread=true&limit=25")
        assert r.status_code == 200
        assert captured.get("unread_only") is True
        assert captured.get("limit") == 25
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_list_notifications_exposes_has_more(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The repo's ``has_more`` flag must round-trip through the response."""

    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

    async def fake_session() -> None:
        yield MagicMock()

    current = _analyst_user()

    async def list_with_more(_db: object, **_k: object) -> tuple[list, bool]:
        return ([_make_row(user_id=current.id)], True)

    monkeypatch.setattr(
        "sentinel_prism.api.routes.notifications.in_app_repo.list_for_user",
        list_with_more,
    )

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: current
    app.dependency_overrides[session_get_db] = fake_session
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get("/notifications")
        body = r.json()
        assert body["has_more"] is True
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_list_notifications_rejects_excessive_offset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``offset`` must be capped to prevent trivial DoS via a multi-billion
    skip."""

    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

    async def fake_session() -> None:
        yield MagicMock()

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _analyst_user()
    app.dependency_overrides[session_get_db] = fake_session
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get("/notifications?offset=2000000000")
        assert r.status_code == 422
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_mark_read_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

    async def fake_session() -> None:
        yield MagicMock()

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _analyst_user()
    app.dependency_overrides[session_get_db] = fake_session
    monkeypatch.setattr(
        "sentinel_prism.api.routes.notifications.in_app_repo.mark_read",
        AsyncMock(return_value=False),
    )
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.patch(f"/notifications/{uuid.uuid4()}/read")
        assert r.status_code == 404
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_mark_read_other_users_notification_returns_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wrong-user boundary: user A attempting to mark user B's notification
    must receive 404 (do not leak existence). The repo is responsible for
    scoping by ``user_id``; this test asserts the route passes the current
    user's id through and surfaces ``False`` as 404."""

    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

    async def fake_session() -> None:
        yield MagicMock()

    current = _analyst_user()
    captured: dict[str, object] = {}

    async def scoped_mark_read(_db: object, **kwargs: object) -> bool:
        captured.update(kwargs)
        # Simulate the repo contract: the notification belongs to some
        # other user, so ``mark_read`` returns ``False`` for ``current``.
        return False

    monkeypatch.setattr(
        "sentinel_prism.api.routes.notifications.in_app_repo.mark_read",
        scoped_mark_read,
    )
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: current
    app.dependency_overrides[session_get_db] = fake_session
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                target = uuid.uuid4()
                r = await client.patch(f"/notifications/{target}/read")
        assert r.status_code == 404
        assert captured.get("user_id") == current.id
        assert captured.get("notification_id") == target
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_mark_read_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

    async def fake_session() -> None:
        m = MagicMock()
        m.commit = AsyncMock()
        yield m

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _analyst_user()
    app.dependency_overrides[session_get_db] = fake_session
    monkeypatch.setattr(
        "sentinel_prism.api.routes.notifications.in_app_repo.mark_read",
        AsyncMock(return_value=True),
    )
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                nid = uuid.uuid4()
                r = await client.patch(f"/notifications/{nid}/read")
        assert r.status_code == 204
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_viewer_can_read_own_notifications(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC #5 — all authenticated roles (viewer/analyst/admin) can read
    their own notifications. This is the analogue of a 403 boundary test
    for routes that intentionally do not restrict by role: a viewer MUST
    NOT receive 403 when listing their own inbox."""

    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

    async def fake_session() -> None:
        yield MagicMock()

    viewer = _viewer_user()

    async def list_empty(_db: object, **_k: object) -> tuple[list, bool]:
        return ([], False)

    monkeypatch.setattr(
        "sentinel_prism.api.routes.notifications.in_app_repo.list_for_user",
        list_empty,
    )

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: viewer
    app.dependency_overrides[session_get_db] = fake_session
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get("/notifications")
        assert r.status_code == 200
    finally:
        app.dependency_overrides.clear()
