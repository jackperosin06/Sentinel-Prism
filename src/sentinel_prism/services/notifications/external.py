"""External channel delivery (email / Slack webhook) with durable attempt log (Story 5.3).

**MVP policy (AC #7 — documented in code):**

* Severity gate matches in-app: only
  :func:`sentinel_prism.services.notifications.notification_policy.load_notification_policy`
  **immediate** severities (default ``critical`` + ``high``). All other severities are
  skipped with an observability log for taxonomy drift parity with
  :mod:`sentinel_prism.services.notifications.in_app` (AC #5 /
  "extend, don't duplicate").
* Team targeting matches in-app: both SMTP and Slack paths resolve
  recipients via :func:`in_app_repo.list_active_users_for_team_slug`
  and decline to send when the team has zero active members. This
  preserves the "mirror in-app severity gate + same team membership"
  rule declared in the story's Completion Notes for both channels
  (not only SMTP).
* ``channel_slug`` on ``routing_decisions`` is intentionally
  **ignored** in MVP — the single knob is
  ``NOTIFICATIONS_EXTERNAL_CHANNEL`` (``none``/``smtp``/``slack``).
  Per-team fan-out by ``channel_slug`` is reserved for Story 5.4
  (digest vs immediate scheduling).

**Idempotency (code-review 2026-04-21):**

External sends are at-most-once per
``(run_id, item_url, channel, recipient_descriptor)``. Previously this
was enforced with a check-then-send-then-insert sequence which had a
TOCTOU window: two concurrent ``node_route`` invocations could both
observe "row absent" and both fire a real send before the unique
constraint rejected one of the subsequent inserts. The new flow is:

1. Claim the idempotency key by inserting a ``pending`` row via
   ``INSERT ... ON CONFLICT DO NOTHING``, then COMMIT. A peer that
   lost the race sees ``rowcount == 0`` and exits early without
   sending.
2. Release the session before the external I/O so the async DB pool
   is not pinned for the duration of the SMTP/HTTP round-trip.
3. Send via the adapter.
4. Open a fresh short session and UPDATE the row to the terminal
   outcome (``success`` or ``failure``). Rows left in ``pending``
   indicate a crash between steps 1 and 4 and are visible to admins.

**PII envelope (AC #6 carve-out):** ``recipient_descriptor`` stores
the SMTP recipient email (lower-cased) so operators can audit which
mailbox actually received a sandbox send. This is an explicit
operational carve-out from AC #6 — see the docstring on
:class:`sentinel_prism.db.models.NotificationDeliveryAttempt`.

**Boundary compliance:** graph nodes call this service; this service
does not import ``graph.*``. Vendor/protocol code lives in
:mod:`sentinel_prism.services.notifications.adapters`.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sentinel_prism.db.models import (
    NotificationDeliveryChannel,
    NotificationDeliveryOutcome,
)
from sentinel_prism.db.repositories import in_app_notifications as in_app_repo
from sentinel_prism.services.notifications._attempts import (
    claim_attempt as _claim_attempt,
    finalize_attempt as _finalize_attempt,
    safe_detail as _safe_detail,
    slack_descriptor as _slack_descriptor,
    slack_escape as _slack_escape,
)
from sentinel_prism.services.notifications.adapters.slack import send_slack_webhook_text
from sentinel_prism.services.notifications.adapters.smtp import send_smtp_email
from sentinel_prism.services.notifications.external_settings import (
    ExternalNotificationSettings,
    load_external_notification_settings,
)
from sentinel_prism.services.notifications.notification_policy import (
    load_notification_policy,
)

logger = logging.getLogger(__name__)

SessionMaker = async_sessionmaker[AsyncSession]

_MAX_ITEM_URL_CHARS = 2048
def _norm_item_url(raw: Any) -> str:
    return str(raw or "").strip()


def _truncate(value: str, *, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)] + "…"


async def enqueue_external_for_decisions(
    *,
    session_factory: SessionMaker,
    run_id: str,
    decisions: list[dict[str, Any]],
    settings: ExternalNotificationSettings | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Send sandbox email or Slack webhook; persist one row per attempt (FR23, FR25).

    Returns ``(delivery_events, errors)`` — failures are non-fatal to
    routing decisions. See the module docstring for the MVP policy and
    the two-phase idempotency flow.
    """

    cfg = settings or load_external_notification_settings()
    delivery_events: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    if cfg.mode == "none":
        return delivery_events, errors

    rid_raw = str(run_id).strip()
    try:
        rid = uuid.UUID(rid_raw)
    except (ValueError, TypeError):
        return (
            [],
            [
                {
                    "step": "external_notifications",
                    "message": "invalid_run_id",
                    "error_class": "ValueError",
                    "detail": "run_id is not a valid UUID",
                }
            ],
        )

    if cfg.mode == "smtp":
        if not cfg.smtp_host or not cfg.smtp_from:
            return (
                [],
                [
                    {
                        "step": "external_notifications",
                        "message": "smtp_not_configured",
                        "error_class": "ConfigurationError",
                        "detail": "NOTIFICATIONS_SMTP_HOST and NOTIFICATIONS_SMTP_FROM required",
                    }
                ],
            )
    elif cfg.mode == "slack":
        if not cfg.slack_webhook_url:
            return (
                [],
                [
                    {
                        "step": "external_notifications",
                        "message": "slack_webhook_not_configured",
                        "error_class": "ConfigurationError",
                        "detail": "NOTIFICATIONS_SLACK_WEBHOOK_URL required",
                    }
                ],
            )

    # P-review — dedupe repeated failure envelopes across decisions in one
    # run so a persistently-broken webhook does not produce N identical
    # ``slack_webhook_failed`` entries in ``errors[]`` (one per decision).
    error_envelope_keys: set[tuple[str, str]] = set()

    def _append_error(envelope: dict[str, Any]) -> None:
        key = (
            str(envelope.get("message") or ""),
            str(envelope.get("error_class") or ""),
        )
        if key in error_envelope_keys:
            return
        error_envelope_keys.add(key)
        errors.append(envelope)

    for d in decisions:
        if not isinstance(d, dict):
            continue
        if not d.get("matched"):
            continue
        sev_raw = str(d.get("severity") or "").strip().lower()
        if sev_raw not in load_notification_policy().immediate_severities:
            if sev_raw:
                logger.info(
                    "external_notifications",
                    extra={
                        "event": "external_severity_skipped",
                        "ctx": {
                            "run_id": str(rid),
                            "severity": sev_raw,
                            "mode": cfg.mode,
                        },
                    },
                )
            continue
        ts = d.get("team_slug")
        if ts is None or not str(ts).strip():
            continue
        team_slug_canonical = str(ts).strip()
        team_slug_key = team_slug_canonical.lower()
        url = _truncate(_norm_item_url(d.get("item_url")), limit=_MAX_ITEM_URL_CHARS)
        if not url:
            continue

        try:
            if cfg.mode == "smtp":
                ev, err = await _deliver_smtp_decision(
                    session_factory=session_factory,
                    cfg=cfg,
                    run_id=rid,
                    item_url=url,
                    team_slug=team_slug_canonical,
                    team_slug_key=team_slug_key,
                    severity=sev_raw,
                )
            else:
                ev, err = await _deliver_slack_decision(
                    session_factory=session_factory,
                    cfg=cfg,
                    run_id=rid,
                    item_url=url,
                    team_slug=team_slug_canonical,
                    team_slug_key=team_slug_key,
                    severity=sev_raw,
                )
        except Exception as exc:  # noqa: BLE001 — final safety net per AC #5
            logger.warning(
                "external_notifications",
                extra={
                    "event": "external_decision_failed",
                    "ctx": {
                        "run_id": str(rid),
                        "mode": cfg.mode,
                        "team_slug": team_slug_canonical,
                        "error_class": type(exc).__name__,
                    },
                },
            )
            _append_error(
                {
                    "step": "external_notifications",
                    "message": "external_decision_failed",
                    "error_class": type(exc).__name__,
                    "detail": _safe_detail(str(exc)) or "",
                }
            )
            continue

        delivery_events.extend(ev)
        for e in err:
            _append_error(e)

    return delivery_events, errors


async def _deliver_smtp_decision(
    *,
    session_factory: SessionMaker,
    cfg: ExternalNotificationSettings,
    run_id: uuid.UUID,
    item_url: str,
    team_slug: str,
    team_slug_key: str,
    severity: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    out_ev: list[dict[str, Any]] = []
    out_err: list[dict[str, Any]] = []

    try:
        async with session_factory() as session:
            members = await in_app_repo.list_active_users_for_team_slug(
                session, team_slug=team_slug_key
            )
    except Exception as exc:  # noqa: BLE001 — DB blip must not abort node_route
        logger.warning(
            "external_notifications",
            extra={
                "event": "external_member_lookup_failed",
                "ctx": {
                    "run_id": str(run_id),
                    "team_slug": team_slug,
                    "error_class": type(exc).__name__,
                },
            },
        )
        out_err.append(
            {
                "step": "external_notifications",
                "message": "external_member_lookup_failed",
                "error_class": type(exc).__name__,
                "detail": _safe_detail(str(exc)) or "",
            }
        )
        return out_ev, out_err

    if not members:
        logger.warning(
            "external_notifications",
            extra={
                "event": "external_smtp_no_recipients",
                "ctx": {"run_id": str(run_id), "team_slug": team_slug},
            },
        )
        out_err.append(
            {
                "step": "external_notifications",
                "message": "external_smtp_no_recipients",
                "error_class": "NoRecipients",
                "detail": f"team_slug={team_slug}",
            }
        )
        return out_ev, out_err

    recorded = 0
    skipped = 0
    failed = 0
    for _uid, email in members:
        desc = (email or "").strip().lower()
        if not desc:
            continue

        claimed, claim_err = await _claim_attempt(
            session_factory,
            run_id=run_id,
            item_url=item_url,
            channel=NotificationDeliveryChannel.SMTP,
            recipient_descriptor=desc,
        )
        if claim_err is not None:
            out_err.append(
                {
                    "step": "external_notifications",
                    "message": "smtp_attempt_claim_failed",
                    "error_class": claim_err.split(":", 1)[0],
                    "detail": _safe_detail(claim_err) or "",
                }
            )
            failed += 1
            continue
        if not claimed:
            skipped += 1
            continue

        ok, err_class, detail = await send_smtp_email(
            host=cfg.smtp_host or "",
            port=cfg.smtp_port,
            user=cfg.smtp_user,
            password=cfg.smtp_password,
            from_addr=cfg.smtp_from or "",
            to_addr=desc,
            subject=(
                f"[Sentinel Prism] {severity.capitalize()} routed update ({team_slug})"
            ),
            body=(
                f"Severity: {severity}\n"
                f"Team: {team_slug}\n"
                f"Item: {item_url}\n"
            ),
            use_tls=cfg.smtp_use_tls,
        )

        outcome = (
            NotificationDeliveryOutcome.SUCCESS
            if ok
            else NotificationDeliveryOutcome.FAILURE
        )
        finalize_err = await _finalize_attempt(
            session_factory,
            run_id=run_id,
            item_url=item_url,
            channel=NotificationDeliveryChannel.SMTP,
            recipient_descriptor=desc,
            outcome=outcome,
            error_class=err_class,
            detail=_safe_detail(detail),
            provider_message_id=None,
        )
        if finalize_err is not None:
            out_err.append(
                {
                    "step": "external_notifications",
                    "message": "smtp_attempt_finalize_failed",
                    "error_class": finalize_err.split(":", 1)[0],
                    "detail": _safe_detail(finalize_err) or "",
                }
            )
            failed += 1
            continue

        recorded += 1
        if not ok:
            out_err.append(
                {
                    "step": "external_notifications",
                    "message": "smtp_send_failed",
                    "error_class": err_class or "SmtpError",
                    "detail": _safe_detail(detail) or "",
                }
            )

    if recorded or skipped or failed:
        ev: dict[str, Any] = {
            "channel": "external_smtp",
            "status": "recorded" if recorded else "no_new_rows",
            "run_id": str(run_id),
            "attempts": recorded,
            "skipped": skipped,
        }
        if failed:
            ev["failed_to_persist"] = failed
        out_ev.append(ev)

    return out_ev, out_err


async def _deliver_slack_decision(
    *,
    session_factory: SessionMaker,
    cfg: ExternalNotificationSettings,
    run_id: uuid.UUID,
    item_url: str,
    team_slug: str,
    team_slug_key: str,
    severity: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    out_ev: list[dict[str, Any]] = []
    out_err: list[dict[str, Any]] = []

    # P-review — mirror the in-app membership gate: only post to Slack if the
    # team actually has active members. Prevents the Slack channel from
    # receiving routed notifications for a stale/empty team that would get
    # zero in-app rows.
    try:
        async with session_factory() as session:
            members = await in_app_repo.list_active_users_for_team_slug(
                session, team_slug=team_slug_key
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "external_notifications",
            extra={
                "event": "external_member_lookup_failed",
                "ctx": {
                    "run_id": str(run_id),
                    "team_slug": team_slug,
                    "error_class": type(exc).__name__,
                },
            },
        )
        out_err.append(
            {
                "step": "external_notifications",
                "message": "external_member_lookup_failed",
                "error_class": type(exc).__name__,
                "detail": _safe_detail(str(exc)) or "",
            }
        )
        return out_ev, out_err

    if not members:
        logger.warning(
            "external_notifications",
            extra={
                "event": "external_slack_no_recipients",
                "ctx": {"run_id": str(run_id), "team_slug": team_slug},
            },
        )
        out_err.append(
            {
                "step": "external_notifications",
                "message": "external_slack_no_recipients",
                "error_class": "NoRecipients",
                "detail": f"team_slug={team_slug}",
            }
        )
        return out_ev, out_err

    descriptor = _slack_descriptor(team_slug_key)

    claimed, claim_err = await _claim_attempt(
        session_factory,
        run_id=run_id,
        item_url=item_url,
        channel=NotificationDeliveryChannel.SLACK_WEBHOOK,
        recipient_descriptor=descriptor,
    )
    if claim_err is not None:
        out_err.append(
            {
                "step": "external_notifications",
                "message": "slack_attempt_claim_failed",
                "error_class": claim_err.split(":", 1)[0],
                "detail": _safe_detail(claim_err) or "",
            }
        )
        return out_ev, out_err

    if not claimed:
        out_ev.append(
            {
                "channel": "external_slack_webhook",
                "status": "no_new_rows",
                "run_id": str(run_id),
                "skipped": 1,
            }
        )
        return out_ev, out_err

    text = (
        f"*Sentinel Prism* — {severity} routed update\n"
        f"*Severity:* {_slack_escape(severity)}\n"
        f"*Team:* {_slack_escape(team_slug)}\n"
        f"*Item:* {_slack_escape(item_url)}"
    )

    ok, err_class, detail, hint = await send_slack_webhook_text(
        webhook_url=cfg.slack_webhook_url or "",
        text=text,
    )

    outcome = (
        NotificationDeliveryOutcome.SUCCESS
        if ok
        else NotificationDeliveryOutcome.FAILURE
    )
    finalize_err = await _finalize_attempt(
        session_factory,
        run_id=run_id,
        item_url=item_url,
        channel=NotificationDeliveryChannel.SLACK_WEBHOOK,
        recipient_descriptor=descriptor,
        outcome=outcome,
        error_class=err_class,
        detail=_safe_detail(detail),
        provider_message_id=_safe_detail(hint),
    )
    if finalize_err is not None:
        out_err.append(
            {
                "step": "external_notifications",
                "message": "slack_attempt_finalize_failed",
                "error_class": finalize_err.split(":", 1)[0],
                "detail": _safe_detail(finalize_err) or "",
            }
        )
        return out_ev, out_err

    out_ev.append(
        {
            "channel": "external_slack_webhook",
            "status": "recorded" if ok else "recorded_failure",
            "run_id": str(run_id),
            "attempts": 1,
            "outcome": outcome.value,
        }
    )
    if not ok:
        out_err.append(
            {
                "step": "external_notifications",
                "message": "slack_webhook_failed",
                "error_class": err_class or "SlackError",
                "detail": _safe_detail(detail) or "",
            }
        )

    return out_ev, out_err
