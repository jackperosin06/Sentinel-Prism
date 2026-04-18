"""Briefing list/detail API (Story 4.3 — FR19)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from sentinel_prism.api.deps import get_current_user
from sentinel_prism.db.models import User, UserRole
from sentinel_prism.db.session import get_db as session_get_db
from sentinel_prism.main import create_app


def _viewer_user() -> User:
    return User(
        id=uuid.uuid4(),
        email="viewer@test.local",
        password_hash="x",
        role=UserRole.VIEWER,
        is_active=True,
    )


@pytest.mark.asyncio
async def test_list_briefings_unauthorized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")
    app = create_app()
    transport = ASGITransport(app=app)
    async with LifespanManager(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/briefings")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_list_briefings_viewer_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

    async def fake_session() -> None:
        yield MagicMock()

    bid = uuid.uuid4()
    rid = uuid.uuid4()
    created = datetime.now(timezone.utc)
    row = SimpleNamespace(
        id=bid,
        run_id=rid,
        created_at=created,
        groups=[
            {
                "dimensions": {"severity": "high"},
                "sections": {"what_changed": "• Item (doc, EU)"},
                "members": [],
            }
        ],
    )

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _viewer_user()
    app.dependency_overrides[session_get_db] = fake_session
    monkeypatch.setattr(
        "sentinel_prism.db.repositories.briefings.list_briefings",
        AsyncMock(return_value=[row]),
    )
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get("/briefings")
        assert r.status_code == 200
        body = r.json()
        assert len(body["items"]) == 1
        assert body["items"][0]["id"] == str(bid)
        assert body["items"][0]["run_id"] == str(rid)
        assert body["items"][0]["group_count"] == 1
        assert "Item" in body["items"][0]["summary"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_briefing_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

    async def fake_session() -> None:
        yield MagicMock()

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _viewer_user()
    app.dependency_overrides[session_get_db] = fake_session
    monkeypatch.setattr(
        "sentinel_prism.db.repositories.briefings.get_briefing_by_id",
        AsyncMock(return_value=None),
    )
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get(f"/briefings/{uuid.uuid4()}")
        assert r.status_code == 404
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_briefing_detail_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

    async def fake_session() -> None:
        yield MagicMock()

    bid = uuid.uuid4()
    rid = uuid.uuid4()
    created = datetime.now(timezone.utc)
    row = SimpleNamespace(
        id=bid,
        run_id=rid,
        created_at=created,
        grouping_dimensions=["severity", "jurisdiction"],
        groups=[
            {
                "dimensions": {"severity": "high", "jurisdiction": "EU"},
                "sections": {
                    "what_changed": "• T (guidance, EU)",
                    "why_it_matters": "rationale",
                    "who_should_care": "Teams",
                    "confidence": "Model confidence 0.80 (0–1 scale).",
                    "suggested_actions": "Acknowledge",
                },
                "members": [
                    {
                        "normalized_update_id": str(uuid.uuid4()),
                        "item_url": "https://ex/u",
                        "title": "T",
                        "jurisdiction": "EU",
                        "document_type": "guidance",
                        "severity": "high",
                        "confidence": 0.8,
                        "impact_categories": ["labeling"],
                    }
                ],
            }
        ],
    )

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _viewer_user()
    app.dependency_overrides[session_get_db] = fake_session
    monkeypatch.setattr(
        "sentinel_prism.db.repositories.briefings.get_briefing_by_id",
        AsyncMock(return_value=row),
    )
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get(f"/briefings/{bid}")
        assert r.status_code == 200
        data = r.json()
        assert data["id"] == str(bid)
        assert data["grouping_dimensions"] == ["severity", "jurisdiction"]
        assert len(data["groups"]) == 1
        g = data["groups"][0]
        assert g["dimensions"]["severity"] == "high"
        assert g["sections"]["why_it_matters"] == "rationale"
        assert len(g["members"]) == 1
        assert g["members"][0]["item_url"] == "https://ex/u"
    finally:
        app.dependency_overrides.clear()
