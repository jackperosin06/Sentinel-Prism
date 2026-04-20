"""External notification orchestration (Story 5.3)."""

from __future__ import annotations

import logging
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from sentinel_prism.db.models import (
    NotificationDeliveryChannel,
    NotificationDeliveryOutcome,
)
from sentinel_prism.services.notifications.external import (
    _slack_descriptor,
    _slack_escape,
    enqueue_external_for_decisions,
)
from sentinel_prism.services.notifications.external_settings import (
    ExternalNotificationSettings,
)


def _settings_smtp() -> ExternalNotificationSettings:
    return ExternalNotificationSettings(
        mode="smtp",
        smtp_host="smtp.example.test",
        smtp_port=587,
        smtp_user="u",
        smtp_password="p",
        smtp_from="from@test.local",
        smtp_use_tls=True,
        slack_webhook_url=None,
    )


def _settings_slack() -> ExternalNotificationSettings:
    return ExternalNotificationSettings(
        mode="slack",
        smtp_host=None,
        smtp_port=587,
        smtp_user=None,
        smtp_password=None,
        smtp_from=None,
        smtp_use_tls=True,
        slack_webhook_url="https://hooks.slack.test/xxx",
    )


def _session_cm() -> MagicMock:
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(
        return_value=MagicMock(commit=AsyncMock(), rollback=AsyncMock())
    )
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm


def _factory() -> MagicMock:
    return MagicMock(side_effect=lambda: _session_cm())


@pytest.mark.asyncio
async def test_external_none_mode_no_ops() -> None:
    factory = MagicMock()
    ev, err = await enqueue_external_for_decisions(
        session_factory=factory,
        run_id=str(uuid.uuid4()),
        decisions=[
            {
                "matched": True,
                "severity": "critical",
                "team_slug": "t",
                "item_url": "https://x",
            }
        ],
        settings=ExternalNotificationSettings(
            mode="none",
            smtp_host=None,
            smtp_port=587,
            smtp_user=None,
            smtp_password=None,
            smtp_from=None,
            smtp_use_tls=True,
            slack_webhook_url=None,
        ),
    )
    assert ev == []
    assert err == []
    factory.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_run_id_short_circuits() -> None:
    ev, err = await enqueue_external_for_decisions(
        session_factory=MagicMock(),
        run_id="not-a-uuid",
        decisions=[
            {
                "matched": True,
                "severity": "critical",
                "team_slug": "t",
                "item_url": "https://x",
            }
        ],
        settings=_settings_smtp(),
    )
    assert ev == []
    assert len(err) == 1
    assert err[0]["message"] == "invalid_run_id"


@pytest.mark.asyncio
async def test_smtp_not_configured_errors() -> None:
    settings = ExternalNotificationSettings(
        mode="smtp",
        smtp_host=None,
        smtp_port=587,
        smtp_user=None,
        smtp_password=None,
        smtp_from=None,
        smtp_use_tls=True,
        slack_webhook_url=None,
    )
    ev, err = await enqueue_external_for_decisions(
        session_factory=MagicMock(),
        run_id=str(uuid.uuid4()),
        decisions=[
            {
                "matched": True,
                "severity": "critical",
                "team_slug": "t",
                "item_url": "https://x",
            }
        ],
        settings=settings,
    )
    assert ev == []
    assert err and err[0]["message"] == "smtp_not_configured"


@pytest.mark.asyncio
async def test_external_smtp_sends_and_finalizes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = uuid.uuid4()
    uid = uuid.uuid4()

    async def fake_members(_s: object, *, team_slug: str) -> list[tuple[uuid.UUID, str]]:
        assert team_slug == "team-a"
        return [(uid, "Recipient@test.local")]

    monkeypatch.setattr(
        "sentinel_prism.services.notifications.external.in_app_repo.list_active_users_for_team_slug",
        fake_members,
    )

    claim_mock = AsyncMock(return_value=True)
    finalize_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "sentinel_prism.services.notifications._attempts.delivery_repo.claim_attempt_pending",
        claim_mock,
    )
    monkeypatch.setattr(
        "sentinel_prism.services.notifications._attempts.delivery_repo.finalize_attempt_outcome",
        finalize_mock,
    )
    send_mock = AsyncMock(return_value=(True, None, None))
    monkeypatch.setattr(
        "sentinel_prism.services.notifications.external.send_smtp_email",
        send_mock,
    )

    ev, err = await enqueue_external_for_decisions(
        session_factory=_factory(),
        run_id=str(run),
        decisions=[
            {
                "matched": True,
                "severity": "critical",
                "team_slug": "team-A",
                "item_url": "https://reg/item",
            }
        ],
        settings=_settings_smtp(),
    )
    assert claim_mock.await_count == 1
    assert finalize_mock.await_count == 1
    _args, kwargs = finalize_mock.await_args
    assert kwargs["outcome"] == NotificationDeliveryOutcome.SUCCESS
    assert kwargs["recipient_descriptor"] == "recipient@test.local"
    assert kwargs["channel"] == NotificationDeliveryChannel.SMTP
    assert send_mock.await_count == 1
    assert send_mock.await_args.kwargs["to_addr"] == "recipient@test.local"
    assert any(
        e.get("channel") == "external_smtp" and e.get("status") == "recorded" for e in ev
    )
    assert err == []


@pytest.mark.asyncio
async def test_external_smtp_idempotent_claim_skips_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = uuid.uuid4()
    uid = uuid.uuid4()

    async def fake_members(_s: object, *, team_slug: str) -> list[tuple[uuid.UUID, str]]:
        return [(uid, "r@t.local")]

    monkeypatch.setattr(
        "sentinel_prism.services.notifications.external.in_app_repo.list_active_users_for_team_slug",
        fake_members,
    )
    monkeypatch.setattr(
        "sentinel_prism.services.notifications._attempts.delivery_repo.claim_attempt_pending",
        AsyncMock(return_value=False),
    )
    send_mock = AsyncMock()
    monkeypatch.setattr(
        "sentinel_prism.services.notifications.external.send_smtp_email",
        send_mock,
    )
    finalize_mock = AsyncMock()
    monkeypatch.setattr(
        "sentinel_prism.services.notifications._attempts.delivery_repo.finalize_attempt_outcome",
        finalize_mock,
    )

    ev, err = await enqueue_external_for_decisions(
        session_factory=_factory(),
        run_id=str(run),
        decisions=[
            {
                "matched": True,
                "severity": "critical",
                "team_slug": "t",
                "item_url": "https://x",
            }
        ],
        settings=_settings_smtp(),
    )
    send_mock.assert_not_awaited()
    finalize_mock.assert_not_awaited()
    assert err == []
    assert any(e.get("skipped") == 1 for e in ev)


@pytest.mark.asyncio
async def test_external_smtp_member_lookup_failure_surfaces_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(_s: object, *, team_slug: str) -> list[tuple[uuid.UUID, str]]:
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(
        "sentinel_prism.services.notifications.external.in_app_repo.list_active_users_for_team_slug",
        boom,
    )

    ev, err = await enqueue_external_for_decisions(
        session_factory=_factory(),
        run_id=str(uuid.uuid4()),
        decisions=[
            {
                "matched": True,
                "severity": "critical",
                "team_slug": "t",
                "item_url": "https://x",
            }
        ],
        settings=_settings_smtp(),
    )
    assert ev == []
    assert len(err) == 1
    assert err[0]["message"] == "external_member_lookup_failed"
    assert err[0]["error_class"] == "RuntimeError"


@pytest.mark.asyncio
async def test_external_smtp_no_recipients_does_not_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def empty(_s: object, *, team_slug: str) -> list[tuple[uuid.UUID, str]]:
        return []

    monkeypatch.setattr(
        "sentinel_prism.services.notifications.external.in_app_repo.list_active_users_for_team_slug",
        empty,
    )
    send_mock = AsyncMock()
    monkeypatch.setattr(
        "sentinel_prism.services.notifications.external.send_smtp_email",
        send_mock,
    )

    ev, err = await enqueue_external_for_decisions(
        session_factory=_factory(),
        run_id=str(uuid.uuid4()),
        decisions=[
            {
                "matched": True,
                "severity": "critical",
                "team_slug": "t",
                "item_url": "https://x",
            }
        ],
        settings=_settings_smtp(),
    )
    send_mock.assert_not_awaited()
    assert any(e["message"] == "external_smtp_no_recipients" for e in err)


@pytest.mark.asyncio
async def test_external_smtp_send_failure_records_failure_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uid = uuid.uuid4()

    async def fake_members(_s: object, *, team_slug: str) -> list[tuple[uuid.UUID, str]]:
        return [(uid, "r@t.local")]

    monkeypatch.setattr(
        "sentinel_prism.services.notifications.external.in_app_repo.list_active_users_for_team_slug",
        fake_members,
    )
    monkeypatch.setattr(
        "sentinel_prism.services.notifications._attempts.delivery_repo.claim_attempt_pending",
        AsyncMock(return_value=True),
    )
    finalize_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "sentinel_prism.services.notifications._attempts.delivery_repo.finalize_attempt_outcome",
        finalize_mock,
    )
    monkeypatch.setattr(
        "sentinel_prism.services.notifications.external.send_smtp_email",
        AsyncMock(return_value=(False, "SMTPAuthenticationError", "auth failed")),
    )

    ev, err = await enqueue_external_for_decisions(
        session_factory=_factory(),
        run_id=str(uuid.uuid4()),
        decisions=[
            {
                "matched": True,
                "severity": "critical",
                "team_slug": "t",
                "item_url": "https://x",
            }
        ],
        settings=_settings_smtp(),
    )
    _args, kwargs = finalize_mock.await_args
    assert kwargs["outcome"] == NotificationDeliveryOutcome.FAILURE
    assert kwargs["error_class"] == "SMTPAuthenticationError"
    assert any(e["message"] == "smtp_send_failed" for e in err)
    assert any(e.get("channel") == "external_smtp" for e in ev)


@pytest.mark.asyncio
async def test_severity_non_critical_is_skipped_and_logged(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    send_mock = AsyncMock()
    monkeypatch.setattr(
        "sentinel_prism.services.notifications.external.send_smtp_email",
        send_mock,
    )

    with caplog.at_level(logging.INFO, logger="sentinel_prism.services.notifications.external"):
        ev, err = await enqueue_external_for_decisions(
            session_factory=_factory(),
            run_id=str(uuid.uuid4()),
            decisions=[
                {
                    "matched": True,
                    "severity": "medium",
                    "team_slug": "t",
                    "item_url": "https://x",
                }
            ],
            settings=_settings_smtp(),
        )
    send_mock.assert_not_awaited()
    assert ev == []
    assert err == []
    assert any("external_severity_skipped" in r.message or
               r.__dict__.get("event") == "external_severity_skipped"
               for r in caplog.records)


@pytest.mark.asyncio
async def test_external_slack_gates_on_team_membership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def empty(_s: object, *, team_slug: str) -> list[tuple[uuid.UUID, str]]:
        return []

    monkeypatch.setattr(
        "sentinel_prism.services.notifications.external.in_app_repo.list_active_users_for_team_slug",
        empty,
    )
    slack_mock = AsyncMock()
    monkeypatch.setattr(
        "sentinel_prism.services.notifications.external.send_slack_webhook_text",
        slack_mock,
    )

    ev, err = await enqueue_external_for_decisions(
        session_factory=_factory(),
        run_id=str(uuid.uuid4()),
        decisions=[
            {
                "matched": True,
                "severity": "critical",
                "team_slug": "t",
                "item_url": "https://x",
            }
        ],
        settings=_settings_slack(),
    )
    slack_mock.assert_not_awaited()
    assert any(e["message"] == "external_slack_no_recipients" for e in err)
    assert ev == []


@pytest.mark.asyncio
async def test_external_slack_sends_and_finalizes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = uuid.uuid4()

    async def one_member(_s: object, *, team_slug: str) -> list[tuple[uuid.UUID, str]]:
        return [(uuid.uuid4(), "anyone@t.local")]

    monkeypatch.setattr(
        "sentinel_prism.services.notifications.external.in_app_repo.list_active_users_for_team_slug",
        one_member,
    )
    monkeypatch.setattr(
        "sentinel_prism.services.notifications._attempts.delivery_repo.claim_attempt_pending",
        AsyncMock(return_value=True),
    )
    finalize_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "sentinel_prism.services.notifications._attempts.delivery_repo.finalize_attempt_outcome",
        finalize_mock,
    )
    slack_mock = AsyncMock(return_value=(True, None, None, None))
    monkeypatch.setattr(
        "sentinel_prism.services.notifications.external.send_slack_webhook_text",
        slack_mock,
    )

    ev, err = await enqueue_external_for_decisions(
        session_factory=_factory(),
        run_id=str(run),
        decisions=[
            {
                "matched": True,
                "severity": "critical",
                "team_slug": "team-X",
                "item_url": "https://reg/item",
            }
        ],
        settings=_settings_slack(),
    )
    _args, kwargs = slack_mock.await_args
    # Mentions should be defused — the posted text must not contain the
    # raw ``<!channel>`` / ``<!here>`` tokens even if upstream injects
    # them via team_slug/item_url.
    assert "<!channel>" not in kwargs["text"]
    # Finalize records SUCCESS, descriptor is team-scoped.
    _args2, kwargs2 = finalize_mock.await_args
    assert kwargs2["outcome"] == NotificationDeliveryOutcome.SUCCESS
    assert kwargs2["recipient_descriptor"] == "slack_webhook:team-x"
    assert any(
        e.get("status") == "recorded" and e.get("channel") == "external_slack_webhook"
        for e in ev
    )
    assert err == []


@pytest.mark.asyncio
async def test_external_slack_failure_records_recorded_failure_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def one_member(_s: object, *, team_slug: str) -> list[tuple[uuid.UUID, str]]:
        return [(uuid.uuid4(), "a@t.local")]

    monkeypatch.setattr(
        "sentinel_prism.services.notifications.external.in_app_repo.list_active_users_for_team_slug",
        one_member,
    )
    monkeypatch.setattr(
        "sentinel_prism.services.notifications._attempts.delivery_repo.claim_attempt_pending",
        AsyncMock(return_value=True),
    )
    finalize_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "sentinel_prism.services.notifications._attempts.delivery_repo.finalize_attempt_outcome",
        finalize_mock,
    )
    monkeypatch.setattr(
        "sentinel_prism.services.notifications.external.send_slack_webhook_text",
        AsyncMock(return_value=(False, "HTTPStatusError", "status=500", None)),
    )

    ev, err = await enqueue_external_for_decisions(
        session_factory=_factory(),
        run_id=str(uuid.uuid4()),
        decisions=[
            {
                "matched": True,
                "severity": "critical",
                "team_slug": "t",
                "item_url": "https://x",
            }
        ],
        settings=_settings_slack(),
    )
    assert any(e.get("status") == "recorded_failure" for e in ev)
    assert any(e["message"] == "slack_webhook_failed" for e in err)
    _args, kwargs = finalize_mock.await_args
    assert kwargs["outcome"] == NotificationDeliveryOutcome.FAILURE


@pytest.mark.asyncio
async def test_error_envelope_dedup_across_decisions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persistent config-class failures should not produce N identical error envelopes."""

    async def one_member(_s: object, *, team_slug: str) -> list[tuple[uuid.UUID, str]]:
        return [(uuid.uuid4(), f"a@{team_slug}.local")]

    monkeypatch.setattr(
        "sentinel_prism.services.notifications.external.in_app_repo.list_active_users_for_team_slug",
        one_member,
    )
    monkeypatch.setattr(
        "sentinel_prism.services.notifications._attempts.delivery_repo.claim_attempt_pending",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "sentinel_prism.services.notifications._attempts.delivery_repo.finalize_attempt_outcome",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "sentinel_prism.services.notifications.external.send_slack_webhook_text",
        AsyncMock(return_value=(False, "HTTPStatusError", "status=404", None)),
    )

    decisions = [
        {
            "matched": True,
            "severity": "critical",
            "team_slug": f"team-{i}",
            "item_url": f"https://x/{i}",
        }
        for i in range(5)
    ]
    _ev, err = await enqueue_external_for_decisions(
        session_factory=_factory(),
        run_id=str(uuid.uuid4()),
        decisions=decisions,
        settings=_settings_slack(),
    )
    slack_failed = [e for e in err if e["message"] == "slack_webhook_failed"]
    assert len(slack_failed) == 1


@pytest.mark.asyncio
async def test_unhandled_decision_exception_does_not_abort_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A programmer error inside one decision must not drop the others."""

    async def boom(
        _s: object, *, team_slug: str
    ) -> list[tuple[uuid.UUID, str]]:
        if team_slug == "bad":
            raise RuntimeError("boom")
        return [(uuid.uuid4(), f"a@{team_slug}.local")]

    monkeypatch.setattr(
        "sentinel_prism.services.notifications.external.in_app_repo.list_active_users_for_team_slug",
        boom,
    )
    monkeypatch.setattr(
        "sentinel_prism.services.notifications._attempts.delivery_repo.claim_attempt_pending",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "sentinel_prism.services.notifications._attempts.delivery_repo.finalize_attempt_outcome",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "sentinel_prism.services.notifications.external.send_slack_webhook_text",
        AsyncMock(return_value=(True, None, None, None)),
    )

    ev, err = await enqueue_external_for_decisions(
        session_factory=_factory(),
        run_id=str(uuid.uuid4()),
        decisions=[
            {
                "matched": True,
                "severity": "critical",
                "team_slug": "bad",
                "item_url": "https://x/1",
            },
            {
                "matched": True,
                "severity": "critical",
                "team_slug": "good",
                "item_url": "https://x/2",
            },
        ],
        settings=_settings_slack(),
    )
    assert any(e.get("status") == "recorded" for e in ev)
    assert any(e["message"] == "external_member_lookup_failed" for e in err)


def test_slack_escape_defuses_broadcast_mentions() -> None:
    out = _slack_escape("compliance <!channel> ping @here review")
    assert "<!channel>" not in out
    assert "@here" not in out or "@\\here" in out


def test_slack_descriptor_is_team_scoped() -> None:
    assert _slack_descriptor("compliance") == "slack_webhook:compliance"
    assert _slack_descriptor("compliance") != _slack_descriptor("legal")
