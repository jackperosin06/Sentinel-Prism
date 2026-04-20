"""Story 5.4 — immediate vs digest policy split and digest enqueue."""

from __future__ import annotations

from types import SimpleNamespace
import uuid
import pytest

from sentinel_prism.services.notifications import digest_flush
from sentinel_prism.services.notifications.notification_policy import (
    NotificationPolicySettings,
    load_notification_policy,
    reload_notification_policy,
)
from sentinel_prism.services.notifications.scheduling import (
    process_routed_notification_deliveries,
    split_decisions_for_policy,
)
from sentinel_prism.workers import digest_scheduler as ds


def test_split_immediate_vs_digest() -> None:
    policy = NotificationPolicySettings(
        immediate_severities=frozenset({"critical", "high"}),
        digest_enabled=True,
        digest_flush_interval_seconds=900,
        max_external_immediate_per_run=50,
        digest_flush_batch_max=500,
    )
    decisions = [
        {
            "matched": True,
            "severity": "critical",
            "team_slug": "a",
            "item_url": "https://ex/1",
        },
        {
            "matched": True,
            "severity": "high",
            "team_slug": "a",
            "item_url": "https://ex/2",
        },
        {
            "matched": True,
            "severity": "medium",
            "team_slug": "a",
            "item_url": "https://ex/3",
        },
        {"matched": False, "severity": "low", "team_slug": "a", "item_url": "https://ex/4"},
    ]
    imm, dig = split_decisions_for_policy(decisions, policy)
    assert len(imm) == 2
    assert len(dig) == 1
    assert dig[0]["item_url"] == "https://ex/3"


def test_policy_critical_only(monkeypatch: pytest.MonkeyPatch) -> None:
    load_notification_policy.cache_clear()
    monkeypatch.setenv("NOTIFICATIONS_IMMEDIATE_SEVERITIES", "critical")
    policy = reload_notification_policy()
    assert policy.immediate_severities == frozenset({"critical"})
    load_notification_policy.cache_clear()


def test_policy_ignores_unknown_severities(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "NOTIFICATIONS_IMMEDIATE_SEVERITIES", "critical,unknown,high,not-real"
    )
    policy = reload_notification_policy()
    assert policy.immediate_severities == frozenset({"critical", "high"})
    load_notification_policy.cache_clear()


def test_policy_cap_uses_per_run_env_with_legacy_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NOTIFICATIONS_MAX_EXTERNAL_IMMEDIATE_PER_ROUTE", "12")
    monkeypatch.delenv("NOTIFICATIONS_MAX_EXTERNAL_IMMEDIATE_PER_RUN", raising=False)
    policy = reload_notification_policy()
    assert policy.max_external_immediate_per_run == 12

    monkeypatch.setenv("NOTIFICATIONS_MAX_EXTERNAL_IMMEDIATE_PER_RUN", "34")
    policy = reload_notification_policy()
    assert policy.max_external_immediate_per_run == 34
    # Backward-compatible alias remains available for older call sites.
    assert policy.max_external_immediate_per_route == 34
    load_notification_policy.cache_clear()


def test_digest_run_id_stable_when_new_rows_append() -> None:
    team = "team-alpha"
    a = uuid.UUID("00000000-0000-0000-0000-000000000001")
    b = uuid.UUID("00000000-0000-0000-0000-000000000002")
    c = uuid.UUID("00000000-0000-0000-0000-000000000003")
    first = digest_flush._digest_run_id_for_rows(team, [a, b])  # noqa: SLF001
    second = digest_flush._digest_run_id_for_rows(team, [a, b, c])  # noqa: SLF001
    assert first == second


@pytest.mark.asyncio
async def test_digest_smtp_requires_valid_recipients(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_claim(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return True, None

    async def fake_finalize(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return None

    async def fake_send(**_kwargs):  # type: ignore[no-untyped-def]
        return True, None, None

    monkeypatch.setattr(digest_flush, "_claim_attempt", fake_claim)
    monkeypatch.setattr(digest_flush, "_finalize_attempt", fake_finalize)
    monkeypatch.setattr(digest_flush, "send_smtp_email", fake_send)

    cfg = SimpleNamespace(
        smtp_host="smtp.test",
        smtp_port=587,
        smtp_user=None,
        smtp_password=None,
        smtp_from="noreply@test",
        smtp_use_tls=True,
    )

    ev, err = await digest_flush._digest_smtp(  # noqa: SLF001
        session_factory=SimpleNamespace(),
        cfg=cfg,
        digest_run_id=uuid.uuid4(),
        item_url="digest://batch/x",
        team_slug="alpha",
        members=[(uuid.uuid4(), "  "), (uuid.uuid4(), "")],
        body="body",
        title="title",
    )
    assert ev
    assert any(e["message"] == "smtp_no_valid_recipients" for e in err)


@pytest.mark.asyncio
async def test_digest_smtp_finalize_failure_surfaces_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_claim(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return True, None

    async def fake_finalize(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return "SQLAlchemyError: boom"

    async def fake_send(**_kwargs):  # type: ignore[no-untyped-def]
        return True, None, None

    monkeypatch.setattr(digest_flush, "_claim_attempt", fake_claim)
    monkeypatch.setattr(digest_flush, "_finalize_attempt", fake_finalize)
    monkeypatch.setattr(digest_flush, "send_smtp_email", fake_send)

    cfg = SimpleNamespace(
        smtp_host="smtp.test",
        smtp_port=587,
        smtp_user=None,
        smtp_password=None,
        smtp_from="noreply@test",
        smtp_use_tls=True,
    )

    ev, err = await digest_flush._digest_smtp(  # noqa: SLF001
        session_factory=SimpleNamespace(),
        cfg=cfg,
        digest_run_id=uuid.uuid4(),
        item_url="digest://batch/x",
        team_slug="alpha",
        members=[(uuid.uuid4(), "ops@example.test")],
        body="body",
        title="title",
    )
    assert ev == []
    assert any(e["message"] == "smtp_attempt_finalize_failed" for e in err)


@pytest.mark.asyncio
async def test_process_routed_notification_deliveries_surfaces_malformed_decisions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_in_app(**_kwargs):  # type: ignore[no-untyped-def]
        return [], []

    async def fake_external(**_kwargs):  # type: ignore[no-untyped-def]
        return [], []

    async def fake_digest(**_kwargs):  # type: ignore[no-untyped-def]
        return [], []

    monkeypatch.setattr(
        "sentinel_prism.services.notifications.scheduling.enqueue_critical_in_app_for_decisions",
        fake_in_app,
    )
    monkeypatch.setattr(
        "sentinel_prism.services.notifications.scheduling.enqueue_external_for_decisions",
        fake_external,
    )
    monkeypatch.setattr(
        "sentinel_prism.services.notifications.scheduling.enqueue_digest_decisions",
        fake_digest,
    )

    dev, err = await process_routed_notification_deliveries(
        session_factory=SimpleNamespace(),
        run_id=str(uuid.uuid4()),
        decisions=[
            {"matched": True, "severity": "", "team_slug": "a", "item_url": "https://x"},
            {"matched": True, "severity": "high", "team_slug": " ", "item_url": "https://y"},
            {"matched": True, "severity": "medium", "team_slug": "a", "item_url": ""},
            {"matched": True, "severity": "critical", "team_slug": "a", "item_url": "https://ok"},
        ],
    )
    assert dev == []
    assert any(e["message"] == "notification_decisions_malformed" for e in err)


@pytest.mark.asyncio
async def test_process_routed_notification_deliveries_catches_unhandled_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_in_app(**_kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("unexpected")

    monkeypatch.setattr(
        "sentinel_prism.services.notifications.scheduling.enqueue_critical_in_app_for_decisions",
        fake_in_app,
    )
    dev, err = await process_routed_notification_deliveries(
        session_factory=SimpleNamespace(),
        run_id=str(uuid.uuid4()),
        decisions=[
            {
                "matched": True,
                "severity": "critical",
                "team_slug": "a",
                "item_url": "https://ok",
            }
        ],
    )
    assert dev == []
    assert any(e["message"] == "notification_scheduling_unhandled" for e in err)


@pytest.mark.asyncio
async def test_digest_scheduler_start_rolls_back_on_add_job_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeScheduler:
        def __init__(self) -> None:
            self.started = False
            self.shutdown_called = False

        def start(self) -> None:
            self.started = True

        def add_job(self, *_args, **_kwargs) -> None:
            raise RuntimeError("boom")

        def shutdown(self, wait: bool = True) -> None:
            self.shutdown_called = True

    fake = FakeScheduler()
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x:y@localhost/db")
    monkeypatch.setattr(ds, "AsyncIOScheduler", lambda: fake)
    monkeypatch.setattr(
        ds,
        "load_notification_policy",
        lambda: NotificationPolicySettings(
            immediate_severities=frozenset({"critical", "high"}),
            digest_enabled=True,
            digest_flush_interval_seconds=900,
            max_external_immediate_per_run=50,
            digest_flush_batch_max=500,
        ),
    )
    scheduler = ds.DigestScheduler()
    with pytest.raises(RuntimeError, match="boom"):
        await scheduler.start()
    assert fake.shutdown_called is True
    assert scheduler.started is False


def test_reset_digest_scheduler_for_tests_shuts_down_live_scheduler() -> None:
    class FakeLiveScheduler:
        def __init__(self) -> None:
            self.shutdown_called = False

        def shutdown(self, wait: bool = False) -> None:
            self.shutdown_called = True

    live = FakeLiveScheduler()
    inst = ds.DigestScheduler()
    inst._scheduler = live  # noqa: SLF001
    inst._started = True  # noqa: SLF001
    ds._instance = inst  # noqa: SLF001
    ds.reset_digest_scheduler_for_tests()
    assert live.shutdown_called is True
    assert ds._instance is None  # noqa: SLF001


@pytest.mark.asyncio
async def test_flush_digest_queue_once_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    row_a = SimpleNamespace(
        id=uuid.uuid4(),
        team_slug="Alpha",
        severity="medium",
        title="T1",
        item_url="https://example/a",
    )
    row_b = SimpleNamespace(
        id=uuid.uuid4(),
        team_slug="alpha",
        severity="high",
        title="T2",
        item_url="https://example/b",
    )
    deleted_ids: list[uuid.UUID] = []

    class _Sess:
        async def commit(self) -> None:
            return None

    class _CM:
        async def __aenter__(self) -> _Sess:
            return _Sess()

        async def __aexit__(self, *_a: object) -> None:
            return None

    class _Factory:
        def __call__(self) -> _CM:
            return _CM()

    async def fake_list_pending(_s: object, *, limit: int):  # type: ignore[no-untyped-def]
        assert limit == 500
        return [row_a, row_b]

    async def fake_delete(_s: object, *, ids: list[uuid.UUID]):  # type: ignore[no-untyped-def]
        deleted_ids.extend(ids)
        return len(ids)

    async def fake_users(_s: object, *, team_slug: str):  # type: ignore[no-untyped-def]
        assert team_slug == "alpha"
        return [uuid.uuid4()]

    async def fake_enqueue_in_app(**_kwargs):  # type: ignore[no-untyped-def]
        return 1, []

    monkeypatch.setattr(
        digest_flush,
        "load_notification_policy",
        lambda: NotificationPolicySettings(
            immediate_severities=frozenset({"critical", "high"}),
            digest_enabled=True,
            digest_flush_interval_seconds=900,
            max_external_immediate_per_run=50,
            digest_flush_batch_max=500,
        ),
    )
    monkeypatch.setattr(digest_flush.digest_repo, "list_pending_batch", fake_list_pending)
    monkeypatch.setattr(digest_flush.digest_repo, "delete_by_ids", fake_delete)
    monkeypatch.setattr(digest_flush.in_app_repo, "list_user_ids_for_team_slug", fake_users)
    monkeypatch.setattr(digest_flush, "enqueue_in_app_message_for_team", fake_enqueue_in_app)
    monkeypatch.setattr(
        digest_flush,
        "load_external_notification_settings",
        lambda: SimpleNamespace(mode="none"),
    )

    dev, err = await digest_flush.flush_digest_queue_once(session_factory=_Factory())
    assert err == []
    assert any(e.get("channel") == "in_app" for e in dev)
    assert set(deleted_ids) == {row_a.id, row_b.id}
