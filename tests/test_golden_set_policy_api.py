"""Golden-set policy admin API (Story 7.4 — FR44, FR45)."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sentinel_prism.api.deps import get_current_user, get_db_for_admin
from sentinel_prism.api.routes import golden_set_policy as gsp_mod
from sentinel_prism.db.audit_constants import GOLDEN_SET_CONFIG_AUDIT_RUN_ID
from sentinel_prism.db.models import AuditEvent, PipelineAuditAction, User, UserRole
from sentinel_prism.db.repositories import golden_set_policy as gsp_repo
from sentinel_prism.db.repositories.golden_set_policy import (
    ActiveGoldenSetPolicy,
    GoldenSetHistoryRow,
)
from sentinel_prism.main import create_app

FIXED_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
ROOT = Path(__file__).resolve().parents[1]


def _user(role: UserRole) -> User:
    return User(
        id=FIXED_ID,
        email="gsp@test.local",
        password_hash="x",
        role=role,
        team_slug=None,
        is_active=True,
    )


def _admin() -> User:
    return _user(UserRole.ADMIN)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "json_body"),
    [
        ("GET", "/admin/golden-set-policy", None),
        ("GET", "/admin/golden-set-policy/history", None),
        (
            "PUT",
            "/admin/golden-set-policy/draft",
            {"label_policy_text": "x", "refresh_cadence": "quarterly"},
        ),
        ("POST", "/admin/golden-set-policy/apply", {}),
    ],
)
@pytest.mark.parametrize("role", [UserRole.ANALYST, UserRole.VIEWER])
async def test_golden_set_policy_forbidden_non_admin(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    json_body: dict[str, object] | None,
    role: UserRole,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _user(role)
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.request(method, path, json=json_body)
        assert r.status_code == 403
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_golden_set_policy_get_ok_mocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

    async def fake_state(
        _db: object,
    ) -> tuple[ActiveGoldenSetPolicy, None]:
        return (
            ActiveGoldenSetPolicy(
                2,
                "policy-text",
                "quarterly",
                True,
            ),
            None,
        )

    history_id = uuid.UUID("00000000-0000-0000-0000-000000000002")
    monkeypatch.setattr(gsp_mod.gsp_repo, "get_state_for_admin", fake_state)
    monkeypatch.setattr(
        gsp_mod.gsp_repo,
        "list_apply_history",
        AsyncMock(
            return_value=[
                GoldenSetHistoryRow(
                    id=history_id,
                    created_at=datetime(2026, 4, 27, 1, 0, tzinfo=timezone.utc),
                    actor_user_id=FIXED_ID,
                    actor_email="gsp@test.local",
                    prior_version=1,
                    new_version=2,
                    prior_refresh_cadence="quarterly",
                    new_refresh_cadence="quarterly",
                    prior_refresh_after_major=True,
                    new_refresh_after_major=False,
                    reason="policy refresh",
                )
            ]
        ),
    )

    app = create_app()
    app.dependency_overrides[get_current_user] = _admin

    async def fake_admin_db() -> object:
        yield MagicMock()

    app.dependency_overrides[get_db_for_admin] = fake_admin_db
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get("/admin/golden-set-policy")
                h = await client.get("/admin/golden-set-policy/history")
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["active"]["version"] == 2
        assert j["active"]["label_policy_text"] == "policy-text"
        assert j["active"]["refresh_cadence"] == "quarterly"
        assert j["active"]["refresh_after_major_classification_change"] is True
        assert j["draft"] is None
        assert h.status_code == 200
        assert h.json()["items"] == [
            {
                "id": str(history_id),
                "created_at": "2026-04-27T01:00:00Z",
                "actor_user_id": str(FIXED_ID),
                "actor_email": "gsp@test.local",
                "prior_version": 1,
                "new_version": 2,
                "prior_refresh_cadence": "quarterly",
                "new_refresh_cadence": "quarterly",
                "prior_refresh_after_major_classification_change": True,
                "new_refresh_after_major_classification_change": False,
                "reason": "policy refresh",
            }
        ]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_golden_set_policy_apply_no_draft_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

    async def boom_apply(*_a: object, **_k: object) -> ActiveGoldenSetPolicy:
        raise ValueError("no_draft_to_apply")

    monkeypatch.setattr(gsp_mod.gsp_repo, "apply_draft", boom_apply)

    app = create_app()
    app.dependency_overrides[get_current_user] = _admin

    sess = MagicMock()
    sess.commit = AsyncMock()
    sess.rollback = AsyncMock()

    async def fake_admin_db() -> object:
        yield sess

    app.dependency_overrides[get_db_for_admin] = fake_admin_db
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.post("/admin/golden-set-policy/apply", json={})
        assert r.status_code == 400
        assert "draft" in r.json()["detail"].lower()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_golden_set_policy_apply_accepts_empty_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

    applied: dict[str, object] = {}

    async def fake_apply(*_a: object, **kwargs: object) -> ActiveGoldenSetPolicy:
        applied.update(kwargs)
        return ActiveGoldenSetPolicy(3, "p", "quarterly", False)

    monkeypatch.setattr(gsp_mod.gsp_repo, "apply_draft", fake_apply)

    app = create_app()
    app.dependency_overrides[get_current_user] = _admin

    sess = MagicMock()
    sess.commit = AsyncMock()
    sess.rollback = AsyncMock()

    async def fake_admin_db() -> object:
        yield sess

    app.dependency_overrides[get_db_for_admin] = fake_admin_db
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.post("/admin/golden-set-policy/apply")
        assert r.status_code == 200, r.text
        assert applied["audit_reason_override"] is None
        assert r.json()["version"] == 3
    finally:
        app.dependency_overrides.clear()


def test_golden_set_policy_repository_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="must not be blank"):
        gsp_repo._validate_label_policy("   ")
    with pytest.raises(ValueError, match="exceeds"):
        gsp_repo._validate_label_policy("x" * (gsp_repo.MAX_LABEL_POLICY_LENGTH + 1))
    with pytest.raises(ValueError, match="refresh_cadence"):
        gsp_repo._validate_cadence("yearly")


@pytest.mark.asyncio
async def test_golden_set_policy_save_draft_preserves_existing_draft_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = MagicMock()
    row.label_policy_text = "active label"
    row.refresh_cadence = "quarterly"
    row.refresh_after_major_classification_change = True
    row.draft_label_policy_text = "existing draft label"
    row.draft_refresh_cadence = "quarterly"
    row.draft_refresh_after_major = False
    row.draft_reason = "old reason"

    async def fake_fetch(_session: object, *, with_for_update: bool = False) -> object:
        assert with_for_update is True
        return row

    monkeypatch.setattr(gsp_repo, "fetch_singleton", fake_fetch)
    session = MagicMock()
    session.flush = AsyncMock()

    await gsp_repo.save_draft(
        session,
        label_policy_text=None,
        refresh_cadence=None,
        refresh_after_major_classification_change=None,
        reason="updated reason",
    )

    assert row.draft_label_policy_text == "existing draft label"
    assert row.draft_refresh_cadence == "quarterly"
    assert row.draft_refresh_after_major is False
    assert row.draft_reason == "updated reason"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_golden_set_policy_apply_integration_audit() -> None:
    db_url = os.environ.get("DATABASE_URL")
    sync_url = os.environ.get("ALEMBIC_SYNC_URL")
    if not db_url or not sync_url:
        pytest.skip("DATABASE_URL and ALEMBIC_SYNC_URL required for integration")

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

    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    sync_engine = create_engine(sync_url)

    suffix = uuid.uuid4().hex[:8]
    user_id = uuid.uuid4()

    _SEED_LABEL = (
        "Golden-set reference labels are owned by Regulatory Affairs. Disputes are "
        "resolved in review with the compliance lead. This placeholder policy applies "
        "until an admin replaces it with the approved label criteria (FR44)."
    )
    new_label = f"integration-golden-label-{suffix} unique text for eval."

    try:
        async with factory() as session:
            session.add(
                User(
                    id=user_id,
                    email=f"gsp-{suffix}@example.com",
                    password_hash="x",
                    role=UserRole.ADMIN,
                    team_slug=None,
                    is_active=True,
                )
            )
            await session.commit()

        async with factory() as session:
            before = await gsp_repo.fetch_singleton(session)
            assert before is not None
            v0 = before.version

        async with factory() as session:
            await gsp_repo.save_draft(
                session,
                label_policy_text=new_label,
                refresh_cadence="quarterly",
                refresh_after_major_classification_change=True,
                reason="integration draft",
            )
            active = await gsp_repo.apply_draft(
                session, actor_user_id=user_id, audit_reason_override=None
            )
            await session.commit()
            assert active.version == v0 + 1
            assert active.label_policy_text == new_label

        async with factory() as session:
            res = await session.scalars(
                select(AuditEvent).where(
                    AuditEvent.run_id == GOLDEN_SET_CONFIG_AUDIT_RUN_ID,
                    AuditEvent.action == PipelineAuditAction.GOLDEN_SET_CONFIG_CHANGED,
                    AuditEvent.actor_user_id == user_id,
                )
            )
            rows = list(res.all())
            assert len(rows) >= 1
            meta = rows[-1].event_metadata or {}
            assert meta.get("new_version") == active.version
            assert meta.get("prior_version") == v0
            assert meta.get("op") == "apply"
            assert "new_label_policy_sha256_16" in meta
            assert meta.get("reason") == "integration draft"
    finally:
        try:
            with sync_engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM audit_events WHERE run_id = :r AND actor_user_id = :u"),
                    {"r": GOLDEN_SET_CONFIG_AUDIT_RUN_ID, "u": user_id},
                )
                conn.execute(text("DELETE FROM users WHERE id = :i"), {"i": user_id})
                conn.execute(
                    text(
                        "UPDATE golden_set_policy SET version = 1, "
                        "label_policy_text = :lab, "
                        "refresh_cadence = 'quarterly', "
                        "refresh_after_major_classification_change = true, "
                        "draft_label_policy_text = NULL, "
                        "draft_refresh_cadence = NULL, draft_refresh_after_major = NULL, "
                        "draft_reason = NULL "
                        "WHERE id = 1"
                    ),
                    {"lab": _SEED_LABEL},
                )
        finally:
            sync_engine.dispose()
            await engine.dispose()
