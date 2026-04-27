"""Classification policy admin API and repository (Story 7.3 — FR29)."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sentinel_prism.api.deps import get_current_user, get_db_for_admin
from sentinel_prism.api.routes import classification_policy as cp_mod
from sentinel_prism.db.audit_constants import CLASSIFICATION_CONFIG_AUDIT_RUN_ID
from sentinel_prism.db.models import AuditEvent, PipelineAuditAction, User, UserRole
from sentinel_prism.db.repositories import classification_policy as policy_repo
from sentinel_prism.db.repositories.classification_policy import (
    ActiveClassificationPolicy,
)
from sentinel_prism.graph.nodes import classify as classify_node
from sentinel_prism.main import create_app
from sentinel_prism.services.llm.classification import (
    CLASSIFICATION_SYSTEM_PROMPT,
    LOW_CONFIDENCE_THRESHOLD,
    StructuredClassification,
    classification_dict_for_state,
)
from sentinel_prism.services.llm.rules import RuleOutcome

FIXED_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
ROOT = Path(__file__).resolve().parents[1]


def _user(role: UserRole) -> User:
    return User(
        id=FIXED_ID,
        email="cp@test.local",
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
        ("GET", "/admin/classification-policy", None),
        (
            "PUT",
            "/admin/classification-policy/draft",
            {"low_confidence_threshold": 0.4},
        ),
        ("POST", "/admin/classification-policy/apply", {}),
    ],
)
@pytest.mark.parametrize("role", [UserRole.ANALYST, UserRole.VIEWER])
async def test_classification_policy_forbidden_non_admin(
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
async def test_classification_policy_get_ok_mocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

    async def fake_state(_db: object) -> tuple[ActiveClassificationPolicy, None]:
        return ActiveClassificationPolicy(3, 0.42, "prompt-body"), None

    monkeypatch.setattr(cp_mod.policy_repo, "get_state_for_admin", fake_state)

    app = create_app()
    app.dependency_overrides[get_current_user] = _admin

    async def fake_admin_db() -> object:
        yield MagicMock()

    app.dependency_overrides[get_db_for_admin] = fake_admin_db
    transport = ASGITransport(app=app)
    try:
        async with LifespanManager(app):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get("/admin/classification-policy")
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["active"]["version"] == 3
        assert j["active"]["low_confidence_threshold"] == 0.42
        assert j["active"]["system_prompt"] == "prompt-body"
        assert j["draft"] is None
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_classification_policy_save_partial_threshold_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

    saved: dict[str, object] = {}

    async def fake_save(_db: object, **kwargs: object) -> None:
        saved.update(kwargs)

    async def fake_state(_db: object) -> tuple[ActiveClassificationPolicy, object]:
        active = ActiveClassificationPolicy(2, 0.5, "active-prompt")
        draft = policy_repo.DraftClassificationPolicy(0.3, "active-prompt", None)
        return active, draft

    monkeypatch.setattr(cp_mod.policy_repo, "save_draft", fake_save)
    monkeypatch.setattr(cp_mod.policy_repo, "get_state_for_admin", fake_state)

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
                r = await client.put(
                    "/admin/classification-policy/draft",
                    json={"low_confidence_threshold": 0.3},
                )
        assert r.status_code == 200, r.text
        assert saved["low_confidence_threshold"] == 0.3
        assert saved["system_prompt"] is None
        assert r.json()["draft"]["system_prompt"] == "active-prompt"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_classification_policy_apply_no_draft_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

    async def boom_apply(*_a: object, **_k: object) -> ActiveClassificationPolicy:
        raise ValueError("no_draft_to_apply")

    monkeypatch.setattr(cp_mod.policy_repo, "apply_draft", boom_apply)

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
                r = await client.post("/admin/classification-policy/apply", json={})
        assert r.status_code == 400
        assert "draft" in r.json()["detail"].lower()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_classification_policy_apply_accepts_empty_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-32-characters-minimum")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")

    applied: dict[str, object] = {}

    async def fake_apply(*_a: object, **kwargs: object) -> ActiveClassificationPolicy:
        applied.update(kwargs)
        return ActiveClassificationPolicy(4, 0.31, "new-prompt")

    monkeypatch.setattr(cp_mod.policy_repo, "apply_draft", fake_apply)

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
                r = await client.post("/admin/classification-policy/apply")
        assert r.status_code == 200, r.text
        assert applied["audit_reason_override"] is None
        assert r.json()["version"] == 4
    finally:
        app.dependency_overrides.clear()


def test_classification_dict_respects_threshold_for_review() -> None:
    ro = RuleOutcome(in_scope=True, reasons=())
    llm = StructuredClassification(
        severity="medium",
        impact_categories=["labeling"],
        urgency="informational",
        rationale="x",
        confidence=0.85,
    )
    norm = {
        "source_id": str(uuid.uuid4()),
        "item_url": "https://x",
    }
    low = classification_dict_for_state(
        normalized=norm,
        rule_outcome=ro,
        llm=llm,
        low_confidence_threshold=0.8,
    )
    assert low["needs_human_review"] is False

    high = classification_dict_for_state(
        normalized=norm,
        rule_outcome=ro,
        llm=llm,
        low_confidence_threshold=0.9,
    )
    assert high["needs_human_review"] is True


def test_classification_dict_critical_always_review() -> None:
    ro = RuleOutcome(in_scope=True, reasons=())
    llm = StructuredClassification(
        severity="critical",
        impact_categories=["safety"],
        urgency="immediate",
        rationale="x",
        confidence=0.99,
    )
    norm = {"source_id": "s", "item_url": "https://x"}
    row = classification_dict_for_state(
        normalized=norm,
        rule_outcome=ro,
        llm=llm,
        low_confidence_threshold=0.0,
    )
    assert row["needs_human_review"] is True


def test_classification_policy_repository_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="between 0 and 1"):
        policy_repo._validate_threshold(1.1)
    with pytest.raises(ValueError, match="between 0 and 1"):
        policy_repo._validate_threshold(float("nan"))
    with pytest.raises(ValueError, match="must not be blank"):
        policy_repo._validate_system_prompt("   ")
    with pytest.raises(ValueError, match="exceeds"):
        policy_repo._validate_system_prompt("x" * (policy_repo.MAX_SYSTEM_PROMPT_LENGTH + 1))


@pytest.mark.asyncio
async def test_node_classify_uses_loaded_policy_threshold_and_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSessionFactory:
        def __call__(self) -> "FakeSessionFactory":
            return self

        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(
            self,
            exc_type: object,
            exc: object,
            tb: object,
        ) -> None:
            return None

    class CapturingLlm:
        model_id = "capture"

        def __init__(self) -> None:
            self.system_prompts: list[str | None] = []

        async def classify(
            self,
            normalized: dict[str, object],
            *,
            model_id: str,
            prompt_version: str,
            web_context: str | None = None,
            system_prompt: str | None = None,
        ) -> StructuredClassification:
            _ = normalized, model_id, prompt_version, web_context
            self.system_prompts.append(system_prompt)
            return StructuredClassification(
                severity="medium",
                impact_categories=["labeling"],
                urgency="informational",
                rationale="db-policy",
                confidence=0.85,
            )

    async def fake_policy(_session: object) -> ActiveClassificationPolicy:
        return ActiveClassificationPolicy(7, 0.9, "db-system-prompt")

    async def fake_audit_event(**_kwargs: object) -> list[object]:
        return []

    llm = CapturingLlm()
    monkeypatch.setattr(classify_node, "get_session_factory", lambda: FakeSessionFactory())
    monkeypatch.setattr(
        classify_node.classification_policy_repo,
        "get_active_runtime",
        fake_policy,
    )
    monkeypatch.setattr(classify_node, "build_classification_llm", lambda: llm)
    monkeypatch.setattr(classify_node, "record_pipeline_audit_event", fake_audit_event)
    monkeypatch.setenv("WEB_SEARCH_ENABLED", "false")

    out = await classify_node.node_classify(
        {
            "run_id": str(uuid.uuid4()),
            "source_id": str(uuid.uuid4()),
            "normalized_updates": [
                {
                    "source_id": str(uuid.uuid4()),
                    "item_url": "https://example.com/update",
                    "jurisdiction": "US",
                    "document_type": "guidance",
                    "title": "Labeling update",
                }
            ],
        }
    )

    assert llm.system_prompts == ["db-system-prompt"]
    assert out["classifications"][0]["needs_human_review"] is True


@pytest.mark.asyncio
@pytest.mark.integration
async def test_classification_policy_apply_integration_audit() -> None:
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

    try:
        async with factory() as session:
            session.add(
                User(
                    id=user_id,
                    email=f"cp-{suffix}@example.com",
                    password_hash="x",
                    role=UserRole.ADMIN,
                    team_slug=None,
                    is_active=True,
                )
            )
            await session.commit()

        async with factory() as session:
            before = await policy_repo.fetch_singleton(session)
            assert before is not None
            v0 = before.version

        async with factory() as session:
            await policy_repo.save_draft(
                session,
                low_confidence_threshold=0.61,
                system_prompt="integration-test-prompt-unique",
                reason="qa draft",
            )
            active = await policy_repo.apply_draft(
                session, actor_user_id=user_id, audit_reason_override=None
            )
            await session.commit()
            assert active.version == v0 + 1
            assert active.low_confidence_threshold == 0.61
            assert active.system_prompt == "integration-test-prompt-unique"

        async with factory() as session:
            res = await session.scalars(
                select(AuditEvent).where(
                    AuditEvent.run_id == CLASSIFICATION_CONFIG_AUDIT_RUN_ID,
                    AuditEvent.action
                    == PipelineAuditAction.CLASSIFICATION_CONFIG_CHANGED,
                    AuditEvent.actor_user_id == user_id,
                )
            )
            rows = list(res.all())
            assert len(rows) >= 1
            meta = rows[-1].event_metadata or {}
            assert meta.get("new_version") == active.version
            assert meta.get("prior_version") == v0
            assert meta.get("op") == "apply"
            assert "new_system_prompt_sha256_16" in meta
            assert meta.get("reason") == "qa draft"
    finally:
        try:
            with sync_engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM audit_events WHERE run_id = :r AND actor_user_id = :u"),
                    {"r": CLASSIFICATION_CONFIG_AUDIT_RUN_ID, "u": user_id},
                )
                conn.execute(text("DELETE FROM users WHERE id = :i"), {"i": user_id})
                conn.execute(
                    text(
                        "UPDATE classification_policy SET version = 1, "
                        "low_confidence_threshold = :th, system_prompt = :prompt, "
                        "draft_low_confidence_threshold = NULL, "
                        "draft_system_prompt = NULL, draft_reason = NULL WHERE id = 1"
                    ),
                    {"th": LOW_CONFIDENCE_THRESHOLD, "prompt": CLASSIFICATION_SYSTEM_PROMPT},
                )
        finally:
            sync_engine.dispose()
            await engine.dispose()
