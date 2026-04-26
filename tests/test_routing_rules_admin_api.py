"""Admin routing rules API (Story 6.3 — FR32, FR33)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from sentinel_prism.api.deps import get_current_user, get_db_for_admin
from sentinel_prism.db.audit_constants import ROUTING_CONFIG_AUDIT_RUN_ID
from sentinel_prism.db.models import (
    PipelineAuditAction,
    RoutingRule,
    RoutingRuleType,
    User,
    UserRole,
)
from sentinel_prism.main import create_app

TEST_RULE_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")


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


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("GET", "/admin/routing-rules", None),
        (
            "POST",
            "/admin/routing-rules",
            {
                "rule_type": "topic",
                "priority": 1,
                "impact_category": "gmp",
                "team_slug": "qa",
                "channel_slug": "regulatory",
            },
        ),
        (
            "PATCH",
            f"/admin/routing-rules/{TEST_RULE_ID}",
            {"priority": 2},
        ),
        ("DELETE", f"/admin/routing-rules/{TEST_RULE_ID}", None),
    ],
)
@pytest.mark.asyncio
async def test_routing_rules_unauthorized(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    payload: dict | None,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")
    app = create_app()
    transport = ASGITransport(app=app)
    async with LifespanManager(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.request(method, path, json=payload)
    assert r.status_code == 401


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("GET", "/admin/routing-rules", None),
        (
            "POST",
            "/admin/routing-rules",
            {
                "rule_type": "topic",
                "priority": 1,
                "impact_category": "gmp",
                "team_slug": "qa",
                "channel_slug": "regulatory",
            },
        ),
        (
            "PATCH",
            f"/admin/routing-rules/{TEST_RULE_ID}",
            {"priority": 2},
        ),
        ("DELETE", f"/admin/routing-rules/{TEST_RULE_ID}", None),
    ],
)
@pytest.mark.asyncio
async def test_routing_rules_forbidden_for_analyst(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    payload: dict | None,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")
    app = create_app()
    app.dependency_overrides[get_current_user] = _analyst_user
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.request(method, path, json=payload)
        assert r.status_code == 403
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_routing_rules_list_admin_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

    rid = uuid.uuid4()
    row = RoutingRule(
        id=rid,
        priority=1,
        rule_type=RoutingRuleType.TOPIC,
        impact_category="labeling",
        severity_value=None,
        team_slug="qa",
        channel_slug="slack-reg",
        created_at=datetime.now(timezone.utc),
    )

    async def fake_list(_db: object, **kwargs: object) -> list:
        assert kwargs.get("rule_type") is None
        return [row]

    monkeypatch.setattr(
        "sentinel_prism.api.routes.routing_rules.rules_repo.list_rules_admin",
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
                r = await client.get("/admin/routing-rules")
        assert r.status_code == 200
        body = r.json()
        assert len(body["items"]) == 1
        assert body["items"][0]["id"] == str(rid)
        assert body["items"][0]["impact_category"] == "labeling"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_routing_rules_create_rejects_whitespace_key(
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
                r = await client.post(
                    "/admin/routing-rules",
                    json={
                        "rule_type": "topic",
                        "priority": 1,
                        "impact_category": "   ",
                        "team_slug": "t",
                        "channel_slug": "c",
                    },
                )
        assert r.status_code == 422
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_routing_rules_create_writes_audit_with_sentinel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

    admin = _admin_user()
    new_id = uuid.uuid4()

    async def fake_create(_db: object, **kwargs: object) -> RoutingRule:
        assert kwargs["rule_type"] == RoutingRuleType.SEVERITY
        assert kwargs["severity_value"] == "critical"
        return RoutingRule(
            id=new_id,
            priority=2,
            rule_type=RoutingRuleType.SEVERITY,
            impact_category=None,
            severity_value="critical",
            team_slug="all",
            channel_slug="pager",
            created_at=datetime.now(timezone.utc),
        )

    captured_audit: dict = {}

    async def capture_append_audit(
        session: object,
        *,
        run_id: object,
        action: object,
        source_id: object,
        metadata: dict | None,
        actor_user_id: object,
    ) -> uuid.UUID:
        captured_audit["run_id"] = run_id
        captured_audit["action"] = action
        captured_audit["source_id"] = source_id
        captured_audit["metadata"] = metadata
        captured_audit["actor_user_id"] = actor_user_id
        return uuid.uuid4()

    monkeypatch.setattr(
        "sentinel_prism.api.routes.routing_rules.rules_repo.create_rule",
        fake_create,
    )
    monkeypatch.setattr(
        "sentinel_prism.db.repositories.audit_events.append_audit_event",
        capture_append_audit,
    )

    mock_session = MagicMock()
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()

    async def fake_admin_db() -> None:
        yield mock_session

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: admin
    app.dependency_overrides[get_db_for_admin] = fake_admin_db
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.post(
                    "/admin/routing-rules",
                    json={
                        "rule_type": "severity",
                        "priority": 2,
                        "severity_value": "CRITICAL ",
                        "team_slug": "all",
                        "channel_slug": "pager",
                    },
                )
        assert r.status_code == 201, r.text
        assert captured_audit["run_id"] == ROUTING_CONFIG_AUDIT_RUN_ID
        assert captured_audit["action"] == PipelineAuditAction.ROUTING_CONFIG_CHANGED
        assert captured_audit["actor_user_id"] == admin.id
        assert captured_audit["source_id"] is None
        assert captured_audit["metadata"] == {
            "op": "create",
            "rule_id": str(new_id),
            "rule_type": "severity",
        }
        mock_session.commit.assert_awaited_once()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_routing_rules_create_rejects_overlong_db_bound_key(
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
                r = await client.post(
                    "/admin/routing-rules",
                    json={
                        "rule_type": "severity",
                        "priority": 1,
                        "severity_value": "x" * 33,
                        "team_slug": "t",
                        "channel_slug": "c",
                    },
                )
        assert r.status_code == 422
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_routing_rules_create_rejects_out_of_range_priority(
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
                r = await client.post(
                    "/admin/routing-rules",
                    json={
                        "rule_type": "topic",
                        "priority": 2147483648,
                        "impact_category": "gmp",
                        "team_slug": "t",
                        "channel_slug": "c",
                    },
                )
        assert r.status_code == 422
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_routing_rules_patch_admin_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

    admin = _admin_user()
    rule_id = uuid.uuid4()
    existing = RoutingRule(
        id=rule_id,
        priority=1,
        rule_type=RoutingRuleType.TOPIC,
        impact_category="gmp",
        severity_value=None,
        team_slug="qa",
        channel_slug="c1",
        created_at=datetime.now(timezone.utc),
    )

    async def fake_get(_db: object, rid: uuid.UUID) -> RoutingRule | None:
        assert rid == rule_id
        return existing

    audit_calls: list[dict] = []

    async def capture_audit(session: object, **kwargs: object) -> uuid.UUID:
        audit_calls.append(kwargs)
        return uuid.uuid4()

    monkeypatch.setattr(
        "sentinel_prism.api.routes.routing_rules.rules_repo.get_rule_by_id",
        fake_get,
    )
    monkeypatch.setattr(
        "sentinel_prism.api.routes.routing_rules.audit_repo.append_routing_config_audit",
        capture_audit,
    )

    mock_session = MagicMock()
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()

    async def fake_admin_db() -> None:
        yield mock_session

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: admin
    app.dependency_overrides[get_db_for_admin] = fake_admin_db
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.patch(
                    f"/admin/routing-rules/{rule_id}",
                    json={
                        "priority": 3,
                        "impact_category": " GMP ",
                        "team_slug": " qa-team ",
                        "channel_slug": " c2 ",
                    },
                )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["priority"] == 3
        assert body["impact_category"] == "gmp"
        assert body["team_slug"] == "qa-team"
        assert body["channel_slug"] == "c2"
        assert audit_calls[0]["actor_user_id"] == admin.id
        assert audit_calls[0]["op"] == "update"
        mock_session.commit.assert_awaited_once()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_routing_rules_patch_rejects_cross_type_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

    rule_id = uuid.uuid4()
    existing = RoutingRule(
        id=rule_id,
        priority=1,
        rule_type=RoutingRuleType.TOPIC,
        impact_category="gmp",
        severity_value=None,
        team_slug="qa",
        channel_slug="c1",
        created_at=datetime.now(timezone.utc),
    )

    async def fake_get(_db: object, rid: uuid.UUID) -> RoutingRule | None:
        assert rid == rule_id
        return existing

    monkeypatch.setattr(
        "sentinel_prism.api.routes.routing_rules.rules_repo.get_rule_by_id",
        fake_get,
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
                r = await client.patch(
                    f"/admin/routing-rules/{rule_id}",
                    json={"severity_value": "high"},
                )
        assert r.status_code == 422
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_routing_rules_delete_admin_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

    admin = _admin_user()
    rule_id = uuid.uuid4()
    existing = RoutingRule(
        id=rule_id,
        priority=1,
        rule_type=RoutingRuleType.SEVERITY,
        impact_category=None,
        severity_value="critical",
        team_slug="qa",
        channel_slug="pager",
        created_at=datetime.now(timezone.utc),
    )

    async def fake_get(_db: object, rid: uuid.UUID) -> RoutingRule | None:
        assert rid == rule_id
        return existing

    deleted: list[RoutingRule] = []

    async def fake_delete(_db: object, rule: RoutingRule) -> None:
        deleted.append(rule)

    audit_calls: list[dict] = []

    async def capture_audit(session: object, **kwargs: object) -> uuid.UUID:
        audit_calls.append(kwargs)
        return uuid.uuid4()

    monkeypatch.setattr(
        "sentinel_prism.api.routes.routing_rules.rules_repo.get_rule_by_id",
        fake_get,
    )
    monkeypatch.setattr(
        "sentinel_prism.api.routes.routing_rules.rules_repo.delete_rule",
        fake_delete,
    )
    monkeypatch.setattr(
        "sentinel_prism.api.routes.routing_rules.audit_repo.append_routing_config_audit",
        capture_audit,
    )

    mock_session = MagicMock()
    mock_session.commit = AsyncMock()

    async def fake_admin_db() -> None:
        yield mock_session

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: admin
    app.dependency_overrides[get_db_for_admin] = fake_admin_db
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.delete(f"/admin/routing-rules/{rule_id}")
        assert r.status_code == 204
        assert deleted == [existing]
        assert audit_calls[0]["actor_user_id"] == admin.id
        assert audit_calls[0]["op"] == "delete"
        assert audit_calls[0]["rule_id"] == rule_id
        assert audit_calls[0]["rule_type"] == "severity"
        mock_session.commit.assert_awaited_once()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_append_routing_config_audit_uses_sentinel_run_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sentinel_prism.db.repositories import audit_events as audit_mod

    captured: dict = {}

    async def fake_append(
        session: object,
        *,
        run_id: object,
        action: object,
        source_id: object,
        metadata: dict | None,
        actor_user_id: object,
    ) -> uuid.UUID:
        captured["run_id"] = run_id
        captured["action"] = action
        captured["actor_user_id"] = actor_user_id
        return uuid.uuid4()

    monkeypatch.setattr(audit_mod, "append_audit_event", fake_append)
    session = MagicMock()
    uid = uuid.uuid4()
    await audit_mod.append_routing_config_audit(
        session,
        actor_user_id=uid,
        op="delete",
        rule_id=uuid.uuid4(),
        rule_type="topic",
    )
    assert captured["run_id"] == ROUTING_CONFIG_AUDIT_RUN_ID
    assert captured["action"] == PipelineAuditAction.ROUTING_CONFIG_CHANGED
    assert captured["actor_user_id"] == uid
