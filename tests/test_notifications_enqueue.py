"""In-app notification enqueue from routing decisions (Story 5.2).

Covers (mocked session, no DB):

* Severity gate (critical-only, including log on skip of other severities)
* Matched-false decisions are skipped
* Canonical team_slug casing preservation
* Zero-recipients surfaces an ``errors[]`` envelope
* Idempotent replay emits ``delivery_events status="no_new_rows"``
* Delivery events are NOT appended when the commit itself fails

Note: targeting (``list_user_ids_for_team_slug``) is stubbed here so each
case stays a pure unit test. The repo query is covered separately against
a real session in the integration suite.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from sentinel_prism.services.notifications.in_app import (
    IN_APP_ALLOWED_SEVERITIES,
    IN_APP_MIN_SEVERITY,
    enqueue_critical_in_app_for_decisions,
)


def _session_factory_stub(
    *,
    commit: AsyncMock | None = None,
    begin_nested_raises: BaseException | None = None,
) -> MagicMock:
    """Build a ``session_factory`` that yields a session whose ``commit`` is
    an ``AsyncMock`` and whose ``begin_nested()`` is a trivial async CM.

    ``begin_nested_raises`` simulates an error in the savepoint context
    (e.g., FK violation on insert) to exercise the per-user isolation in
    the service loop.
    """

    commit_mock = commit or AsyncMock()

    class _NestedCM:
        async def __aenter__(self) -> None:
            if begin_nested_raises is not None:
                raise begin_nested_raises
            return None

        async def __aexit__(self, *_a: object) -> None:
            return None

    class _Sess:
        def __init__(self) -> None:
            self.commit = commit_mock

        def begin_nested(self) -> _NestedCM:
            return _NestedCM()

    class _CM:
        async def __aenter__(self) -> _Sess:
            return _Sess()

        async def __aexit__(self, *_a: object) -> None:
            return None

    return MagicMock(return_value=_CM())


@pytest.mark.asyncio
async def test_enqueue_skips_non_critical_severity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Severity gate blocks ``high`` even when recipients DO exist.

    Previously the stub returned ``[]`` for recipients, which made the
    assertion tautological: it would pass even if the severity check were
    deleted entirely. Now we return a real recipient so the only thing
    preventing insertion is the severity gate.
    """

    inserts: list[dict[str, object]] = []
    recipient = uuid.uuid4()

    async def one_user(*_a: object, **_k: object) -> list[uuid.UUID]:
        return [recipient]

    async def capture_insert(_s: object, **kwargs: object) -> bool:
        inserts.append(dict(kwargs))
        return True

    monkeypatch.setattr(
        "sentinel_prism.services.notifications.in_app.in_app_repo.list_user_ids_for_team_slug",
        one_user,
    )
    monkeypatch.setattr(
        "sentinel_prism.services.notifications.in_app.in_app_repo.insert_notification_ignore_conflict",
        capture_insert,
    )

    dev, err = await enqueue_critical_in_app_for_decisions(
        session_factory=_session_factory_stub(),
        run_id=str(uuid.uuid4()),
        decisions=[
            {
                "matched": True,
                "severity": "high",
                "team_slug": "alpha",
                "item_url": "https://ex/a",
            }
        ],
    )
    assert inserts == []
    assert dev == []
    assert err == []


@pytest.mark.asyncio
async def test_enqueue_skips_unmatched_decisions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC #2 — a critical decision that did not match a routing rule must
    not enqueue, even when the severity and team_slug shape is otherwise
    valid. Guards the ``if not d.get("matched"): continue`` branch against
    accidental removal."""

    inserts: list[dict[str, object]] = []

    async def one_user(*_a: object, **_k: object) -> list[uuid.UUID]:
        return [uuid.uuid4()]

    async def capture_insert(_s: object, **kwargs: object) -> bool:
        inserts.append(dict(kwargs))
        return True

    monkeypatch.setattr(
        "sentinel_prism.services.notifications.in_app.in_app_repo.list_user_ids_for_team_slug",
        one_user,
    )
    monkeypatch.setattr(
        "sentinel_prism.services.notifications.in_app.in_app_repo.insert_notification_ignore_conflict",
        capture_insert,
    )

    dev, err = await enqueue_critical_in_app_for_decisions(
        session_factory=_session_factory_stub(),
        run_id=str(uuid.uuid4()),
        decisions=[
            {
                "matched": False,
                "severity": IN_APP_MIN_SEVERITY,
                "team_slug": "alpha",
                "item_url": "https://ex/a",
            }
        ],
    )
    assert inserts == []
    assert dev == []
    assert err == []


@pytest.mark.asyncio
async def test_enqueue_no_recipients_surfaces_error_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Critical decision whose team has zero active members must not
    silently drop — the service must emit a non-fatal ``errors[]`` entry
    so operators can detect pager-level routing misconfiguration."""

    inserts: list[dict[str, object]] = []

    async def no_users(*_a: object, **_k: object) -> list[uuid.UUID]:
        return []

    async def capture_insert(_s: object, **kwargs: object) -> bool:
        inserts.append(dict(kwargs))
        return True

    monkeypatch.setattr(
        "sentinel_prism.services.notifications.in_app.in_app_repo.list_user_ids_for_team_slug",
        no_users,
    )
    monkeypatch.setattr(
        "sentinel_prism.services.notifications.in_app.in_app_repo.insert_notification_ignore_conflict",
        capture_insert,
    )

    dev, err = await enqueue_critical_in_app_for_decisions(
        session_factory=_session_factory_stub(),
        run_id=str(uuid.uuid4()),
        decisions=[
            {
                "matched": True,
                "severity": IN_APP_MIN_SEVERITY,
                "team_slug": "ghost-team",
                "item_url": "https://ex/a",
            }
        ],
    )
    assert inserts == []
    assert dev == []
    assert len(err) == 1
    assert err[0]["message"] == "in_app_no_recipients"
    assert "ghost-team" in err[0]["detail"]


@pytest.mark.asyncio
async def test_enqueue_inserts_preserve_canonical_team_slug_casing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Team lookup is case-insensitive (``lower(team_slug)``), but the
    persisted row uses the original casing from the routing decision so
    audit/UI reflect the rule-author's canonical slug."""

    uid = uuid.uuid4()
    run = uuid.uuid4()

    lookup_args: dict[str, str] = {}

    async def one_user(_s: object, *, team_slug: str) -> list[uuid.UUID]:
        lookup_args["team_slug"] = team_slug
        return [uid]

    inserts: list[dict[str, object]] = []

    async def capture_insert(_s: object, **kwargs: object) -> bool:
        inserts.append(dict(kwargs))
        return True

    monkeypatch.setattr(
        "sentinel_prism.services.notifications.in_app.in_app_repo.list_user_ids_for_team_slug",
        one_user,
    )
    monkeypatch.setattr(
        "sentinel_prism.services.notifications.in_app.in_app_repo.insert_notification_ignore_conflict",
        capture_insert,
    )

    dev, err = await enqueue_critical_in_app_for_decisions(
        session_factory=_session_factory_stub(),
        run_id=str(run),
        decisions=[
            {
                "matched": True,
                "severity": IN_APP_MIN_SEVERITY,
                "team_slug": "Team-Alpha",
                "item_url": "https://ex/critical-item",
            }
        ],
    )
    assert err == []
    assert len(inserts) == 1
    assert inserts[0]["user_id"] == uid
    assert inserts[0]["run_id"] == run
    # Canonical casing persisted…
    assert inserts[0]["team_slug"] == "Team-Alpha"
    # …while the query is case-folded for membership matching.
    assert lookup_args["team_slug"] == "team-alpha"
    assert dev[0]["channel"] == "in_app"
    assert dev[0]["status"] == "recorded"
    assert dev[0]["rows_inserted"] == 1


@pytest.mark.asyncio
async def test_enqueue_replay_emits_no_new_rows_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When every INSERT hits the unique constraint (``rowcount == 0``),
    the service must still emit a ``delivery_events`` entry with
    ``status="no_new_rows"`` so the audit trail shows "considered, all
    duplicates" rather than silent."""

    async def one_user(*_a: object, **_k: object) -> list[uuid.UUID]:
        return [uuid.uuid4()]

    async def no_rows_inserted(_s: object, **_k: object) -> bool:
        return False

    monkeypatch.setattr(
        "sentinel_prism.services.notifications.in_app.in_app_repo.list_user_ids_for_team_slug",
        one_user,
    )
    monkeypatch.setattr(
        "sentinel_prism.services.notifications.in_app.in_app_repo.insert_notification_ignore_conflict",
        no_rows_inserted,
    )

    dev, err = await enqueue_critical_in_app_for_decisions(
        session_factory=_session_factory_stub(),
        run_id=str(uuid.uuid4()),
        decisions=[
            {
                "matched": True,
                "severity": IN_APP_MIN_SEVERITY,
                "team_slug": "alpha",
                "item_url": "https://ex/already-seen",
            }
        ],
    )
    assert err == []
    assert len(dev) == 1
    assert dev[0]["status"] == "no_new_rows"
    assert dev[0]["rows_inserted"] == 0


@pytest.mark.asyncio
async def test_enqueue_commit_failure_does_not_emit_delivery_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ``session.commit()`` itself raises, ``delivery_events`` must NOT
    claim rows were persisted — the transaction rolled back and no rows
    exist. Regression guard for the most critical code-review finding."""

    async def one_user(*_a: object, **_k: object) -> list[uuid.UUID]:
        return [uuid.uuid4()]

    async def row_written(_s: object, **_k: object) -> bool:
        return True

    monkeypatch.setattr(
        "sentinel_prism.services.notifications.in_app.in_app_repo.list_user_ids_for_team_slug",
        one_user,
    )
    monkeypatch.setattr(
        "sentinel_prism.services.notifications.in_app.in_app_repo.insert_notification_ignore_conflict",
        row_written,
    )

    commit_mock = AsyncMock(side_effect=SQLAlchemyError("simulated commit failure"))
    factory = _session_factory_stub(commit=commit_mock)

    dev, err = await enqueue_critical_in_app_for_decisions(
        session_factory=factory,
        run_id=str(uuid.uuid4()),
        decisions=[
            {
                "matched": True,
                "severity": IN_APP_MIN_SEVERITY,
                "team_slug": "alpha",
                "item_url": "https://ex/a",
            }
        ],
    )
    assert dev == []
    assert len(err) == 1
    assert err[0]["message"] == "in_app_enqueue_persist_failed"
    commit_mock.assert_awaited()


def test_allowed_severities_constant_has_critical() -> None:
    """Smoke check: the renamed constant still contains ``critical``."""

    assert "critical" in IN_APP_ALLOWED_SEVERITIES
    assert IN_APP_MIN_SEVERITY == "critical"
