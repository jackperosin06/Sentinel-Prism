"""Digest queue enqueue and flush (Story 5.4 — FR22).

``channel_slug`` on routing decisions is logged on queue rows but external
delivery still follows :envvar:`NOTIFICATIONS_EXTERNAL_CHANNEL` (same as
Story 5.3) until per-channel routing is expanded.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sentinel_prism.db.models import NotificationDeliveryChannel, NotificationDeliveryOutcome
from sentinel_prism.db.repositories import digest_queue as digest_repo
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
from sentinel_prism.services.notifications.external_settings import load_external_notification_settings
from sentinel_prism.services.notifications.in_app import (
    enqueue_in_app_message_for_team,
    highest_severity,
)
from sentinel_prism.services.notifications.notification_policy import load_notification_policy

logger = logging.getLogger(__name__)

SessionMaker = async_sessionmaker[AsyncSession]

_MAX_BODY_CHARS = 8000


def _truncate_body_lines(lines: list[str]) -> tuple[str, int]:
    if not lines:
        return "", 0
    out: list[str] = []
    for idx, line in enumerate(lines):
        candidate = "\n".join(out + [line])
        remaining = len(lines) - idx - 1
        suffix = "" if remaining == 0 else f"\n… (+{remaining} more)"
        if len(candidate + suffix) > _MAX_BODY_CHARS:
            text = "\n".join(out)
            if not text:
                text = line[: _MAX_BODY_CHARS - 1] + "…"
                return text, remaining
            return text + suffix, remaining
        out.append(line)
    return "\n".join(out), 0


async def enqueue_digest_decisions(
    *,
    session_factory: SessionMaker,
    run_id: str,
    decisions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Persist digest-bound routing decisions (idempotent per run+url+team)."""

    delivery_events: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    rid_raw = str(run_id).strip()
    try:
        rid = uuid.UUID(rid_raw)
    except (ValueError, TypeError):
        return (
            [],
            [
                {
                    "step": "digest_enqueue",
                    "message": "invalid_run_id",
                    "error_class": "ValueError",
                    "detail": "run_id is not a valid UUID",
                }
            ],
        )

    inserted = 0
    skipped_malformed = 0
    try:
        async with session_factory() as session:
            for d in decisions:
                if not isinstance(d, dict) or not d.get("matched"):
                    skipped_malformed += 1
                    continue
                ts = d.get("team_slug")
                if ts is None or not str(ts).strip():
                    skipped_malformed += 1
                    continue
                team_slug = str(ts).strip()
                url = str(d.get("item_url") or "").strip()
                if not url:
                    skipped_malformed += 1
                    continue
                sev = str(d.get("severity") or "").strip().lower()
                if not sev:
                    skipped_malformed += 1
                    continue
                ch = d.get("channel_slug")
                ch_norm = str(ch).strip() if ch is not None else None
                title = str(d.get("title") or "").strip() or None
                ok = await digest_repo.enqueue_digest_item_ignore_conflict(
                    session,
                    run_id=rid,
                    item_url=url,
                    team_slug=team_slug,
                    channel_slug=ch_norm,
                    severity=sev,
                    title=title,
                )
                if ok:
                    inserted += 1
            await session.commit()
    except SQLAlchemyError as exc:
        logger.warning(
            "digest_enqueue",
            extra={"event": "digest_enqueue_failed", "error_class": type(exc).__name__},
        )
        errors.append(
            {
                "step": "digest_enqueue",
                "message": "digest_enqueue_persist_failed",
                "error_class": type(exc).__name__,
                "detail": str(exc)[:200],
            }
        )
        return delivery_events, errors

    if skipped_malformed:
        logger.warning(
            "digest_enqueue",
            extra={
                "event": "digest_enqueue_skipped_malformed",
                "ctx": {"run_id": str(rid), "count": skipped_malformed},
            },
        )
        errors.append(
            {
                "step": "digest_enqueue",
                "message": "digest_enqueue_skipped_malformed",
                "error_class": "InvalidDecision",
                "detail": f"count={skipped_malformed}",
            }
        )

    if inserted:
        logger.info(
            "digest_enqueue",
            extra={
                "event": "digest_enqueued",
                "ctx": {"run_id": str(rid), "rows": inserted},
            },
        )
        delivery_events.append(
            {
                "channel": "digest_queue",
                "status": "recorded",
                "run_id": str(rid),
                "rows_enqueued": inserted,
            }
        )
    return delivery_events, errors


def _digest_run_id_for_rows(team_slug_key: str, row_ids: list[uuid.UUID]) -> uuid.UUID:
    """Build a stable digest run id for retries of the same team batch.

    We anchor on team + the oldest row id in the batch so retries that include
    newly-enqueued rows keep the same digest run id for previously pending rows.
    """
    if not row_ids:
        return uuid.uuid5(uuid.NAMESPACE_URL, f"digest:{team_slug_key}:empty")
    anchor = min(str(x) for x in row_ids)
    key = f"digest:{team_slug_key}:{anchor}"
    return uuid.uuid5(uuid.NAMESPACE_URL, key)


def _build_digest_body(rows: list[Any]) -> str:
    lines: list[str] = []
    for r in rows:
        title = (getattr(r, "title", None) or "").strip()
        if title:
            lines.append(f"- [{r.severity}] {title} — {r.item_url}")
        else:
            lines.append(f"- [{r.severity}] {r.item_url}")
    body, _omitted = _truncate_body_lines(lines)
    return body


async def flush_digest_queue_once(
    *,
    session_factory: SessionMaker,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Claim pending rows, deliver batched in-app + external, delete rows.

    Uses deterministic ``digest_run_id`` from row ids so replay after partial
    failure does not duplicate in-app rows (unique constraint).
    """

    policy = load_notification_policy()
    delivery_events: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    async with session_factory() as session:
        pending = await digest_repo.list_pending_batch(
            session, limit=policy.digest_flush_batch_max
        )
    if not pending:
        return delivery_events, errors

    logger.info(
        "digest_flush",
        extra={
            "event": "digest_flush_start",
            "ctx": {"pending": len(pending)},
        },
    )

    by_team: dict[str, list[Any]] = defaultdict(list)
    display_team_slug: dict[str, str] = {}
    for row in pending:
        key = str(row.team_slug).strip().lower()
        if not key:
            errors.append(
                {
                    "step": "digest_flush",
                    "message": "digest_row_missing_team_slug",
                    "error_class": "InvalidRow",
                    "detail": f"id={row.id}",
                }
            )
            partial = True
            continue
        by_team[key].append(row)
        display_team_slug.setdefault(key, str(row.team_slug).strip())

    partial = False
    deleted_total = 0

    for team_slug_key, rows in by_team.items():
        team_slug = display_team_slug.get(team_slug_key, team_slug_key)
        row_ids = [r.id for r in rows]
        digest_run_id = _digest_run_id_for_rows(team_slug_key, row_ids)
        body = _build_digest_body(rows)
        title = f"Digest: {len(rows)} routed update(s)"
        item_url = f"digest://batch/{digest_run_id}"

        try:
            async with session_factory() as session:
                user_ids = await in_app_repo.list_user_ids_for_team_slug(
                    session, team_slug=team_slug_key
                )
        except Exception as exc:  # noqa: BLE001
            partial = True
            logger.warning(
                "digest_flush",
                extra={
                    "event": "digest_flush_partial_failure",
                    "ctx": {
                        "team_slug": team_slug,
                        "error_class": type(exc).__name__,
                        "phase": "in_app_lookup",
                    },
                },
            )
            errors.append(
                {
                    "step": "digest_flush",
                    "message": "digest_in_app_lookup_failed",
                    "error_class": type(exc).__name__,
                    "detail": str(exc)[:200],
                }
            )
            continue

        if not user_ids:
            partial = True
            logger.warning(
                "digest_flush",
                extra={
                    "event": "digest_flush_no_recipients",
                    "ctx": {"team_slug": team_slug},
                },
            )
            errors.append(
                {
                    "step": "digest_flush",
                    "message": "digest_no_recipients",
                    "error_class": "NoRecipients",
                    "detail": f"team_slug={team_slug}",
                }
            )
            continue

        chosen_severity = highest_severity([str(r.severity) for r in rows])
        try:
            async with session_factory() as session:
                in_app_inserted, in_app_errors = await enqueue_in_app_message_for_team(
                    session=session,
                    run_id=digest_run_id,
                    team_slug=team_slug,
                    item_url=item_url,
                    severity=chosen_severity,
                    title=title,
                    body=body,
                )
                await session.commit()
        except SQLAlchemyError as exc:
            partial = True
            errors.append(
                {
                    "step": "digest_flush",
                    "message": "digest_in_app_insert_failed",
                    "error_class": type(exc).__name__,
                    "detail": str(exc)[:200],
                }
            )
            continue
        if in_app_errors:
            partial = True
            errors.extend(in_app_errors)
            continue

        if in_app_inserted or user_ids:
            delivery_events.append(
                {
                    "channel": "in_app",
                    "status": "recorded" if in_app_inserted else "no_new_rows",
                    "run_id": str(digest_run_id),
                    "digest_rows": len(rows),
                    "rows_inserted": in_app_inserted,
                    "kind": "digest_batch",
                }
            )

        cfg = load_external_notification_settings()
        ext_failed = False
        if cfg.mode != "none":
            ext_ev, ext_err = await _deliver_digest_external(
                session_factory=session_factory,
                cfg=cfg,
                digest_run_id=digest_run_id,
                item_url=item_url,
                team_slug=team_slug,
                team_slug_key=team_slug_key,
                body=body,
                title=title,
            )
            delivery_events.extend(ext_ev)
            errors.extend(ext_err)
            if ext_err:
                partial = True
                ext_failed = True

        if ext_failed:
            continue

        try:
            async with session_factory() as session:
                deleted = await digest_repo.delete_by_ids(session, ids=row_ids)
                await session.commit()
            deleted_total += deleted
        except SQLAlchemyError as exc:
            partial = True
            logger.warning(
                "digest_flush",
                extra={
                    "event": "digest_flush_delete_failed",
                    "error_class": type(exc).__name__,
                },
            )
            errors.append(
                {
                    "step": "digest_flush",
                    "message": "digest_queue_delete_failed",
                    "error_class": type(exc).__name__,
                    "detail": str(exc)[:200],
                }
            )

    logger.info(
        "digest_flush",
        extra={
            "event": "digest_flush_complete",
            "ctx": {"deleted": deleted_total, "partial": partial},
        },
    )

    return delivery_events, errors


async def _deliver_digest_external(
    *,
    session_factory: SessionMaker,
    cfg: Any,
    digest_run_id: uuid.UUID,
    item_url: str,
    team_slug: str,
    team_slug_key: str,
    body: str,
    title: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    out_ev: list[dict[str, Any]] = []
    out_err: list[dict[str, Any]] = []

    if cfg.mode == "none":
        return out_ev, out_err

    try:
        async with session_factory() as session:
            members = await in_app_repo.list_active_users_for_team_slug(
                session, team_slug=team_slug_key
            )
    except Exception as exc:  # noqa: BLE001
        out_err.append(
            {
                "step": "digest_external",
                "message": "external_member_lookup_failed",
                "error_class": type(exc).__name__,
                "detail": str(exc)[:200],
            }
        )
        return out_ev, out_err

    if not members:
        out_err.append(
            {
                "step": "digest_external",
                "message": "external_no_recipients",
                "error_class": "NoRecipients",
                "detail": f"team_slug={team_slug}",
            }
        )
        return out_ev, out_err

    if cfg.mode == "smtp":
        if not cfg.smtp_host or not cfg.smtp_from:
            out_err.append(
                {
                    "step": "digest_external",
                    "message": "smtp_not_configured",
                    "error_class": "ConfigurationError",
                    "detail": "NOTIFICATIONS_SMTP_HOST and NOTIFICATIONS_SMTP_FROM required",
                }
            )
            return out_ev, out_err
        return await _digest_smtp(
            session_factory=session_factory,
            cfg=cfg,
            digest_run_id=digest_run_id,
            item_url=item_url,
            team_slug=team_slug,
            members=members,
            body=body,
            title=title,
        )
    if cfg.mode == "slack":
        if not cfg.slack_webhook_url:
            out_err.append(
                {
                    "step": "digest_external",
                    "message": "slack_webhook_not_configured",
                    "error_class": "ConfigurationError",
                    "detail": "NOTIFICATIONS_SLACK_WEBHOOK_URL required",
                }
            )
            return out_ev, out_err
        return await _digest_slack(
            session_factory=session_factory,
            cfg=cfg,
            digest_run_id=digest_run_id,
            item_url=item_url,
            team_slug=team_slug,
            team_slug_key=team_slug_key,
            body=body,
            title=title,
        )
    return out_ev, out_err


async def _digest_smtp(
    *,
    session_factory: SessionMaker,
    cfg: Any,
    digest_run_id: uuid.UUID,
    item_url: str,
    team_slug: str,
    members: list[tuple[uuid.UUID, str]],
    body: str,
    title: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    out_ev: list[dict[str, Any]] = []
    out_err: list[dict[str, Any]] = []
    recorded = 0
    skipped = 0
    for _uid, email in members:
        desc = (email or "").strip().lower()
        if not desc:
            skipped += 1
            continue
        claimed, claim_err = await _claim_attempt(
            session_factory,
            run_id=digest_run_id,
            item_url=item_url,
            channel=NotificationDeliveryChannel.SMTP,
            recipient_descriptor=desc,
        )
        if claim_err is not None:
            out_err.append(
                {
                    "step": "digest_external",
                    "message": "smtp_attempt_claim_failed",
                    "error_class": claim_err.split(":", 1)[0],
                    "detail": _safe_detail(claim_err) or "",
                }
            )
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
            subject=f"[Sentinel Prism] {title} ({team_slug})",
            body=f"Digest batch\nTeam: {team_slug}\n\n{body}\n",
            use_tls=cfg.smtp_use_tls,
        )
        outcome = (
            NotificationDeliveryOutcome.SUCCESS if ok else NotificationDeliveryOutcome.FAILURE
        )
        finalize_err = await _finalize_attempt(
            session_factory,
            run_id=digest_run_id,
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
                    "step": "digest_external",
                    "message": "smtp_attempt_finalize_failed",
                    "error_class": finalize_err.split(":", 1)[0],
                    "detail": _safe_detail(finalize_err) or "",
                }
            )
            continue
        if ok:
            recorded += 1
        else:
            out_err.append(
                {
                    "step": "digest_external",
                    "message": "smtp_send_failed",
                    "error_class": err_class or "SmtpError",
                    "detail": _safe_detail(detail) or "",
                }
            )
    if skipped and recorded == 0:
        out_err.append(
            {
                "step": "digest_external",
                "message": "smtp_no_valid_recipients",
                "error_class": "NoValidRecipients",
                "detail": f"team_slug={team_slug}",
            }
        )
    if recorded or skipped:
        out_ev.append(
            {
                "channel": "external_smtp",
                "status": "recorded" if recorded else "no_new_rows",
                "run_id": str(digest_run_id),
                "kind": "digest_batch",
                "attempts": recorded,
                "skipped": skipped,
            }
        )
    return out_ev, out_err


async def _digest_slack(
    *,
    session_factory: SessionMaker,
    cfg: Any,
    digest_run_id: uuid.UUID,
    item_url: str,
    team_slug: str,
    team_slug_key: str,
    body: str,
    title: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    out_ev: list[dict[str, Any]] = []
    out_err: list[dict[str, Any]] = []
    descriptor = _slack_descriptor(team_slug_key)
    claimed, claim_err = await _claim_attempt(
        session_factory,
        run_id=digest_run_id,
        item_url=item_url,
        channel=NotificationDeliveryChannel.SLACK_WEBHOOK,
        recipient_descriptor=descriptor,
    )
    if claim_err is not None:
        out_err.append(
            {
                "step": "digest_external",
                "message": "slack_attempt_claim_failed",
                "error_class": claim_err.split(":", 1)[0],
                "detail": _safe_detail(claim_err) or "",
            }
        )
        return out_ev, out_err
    if not claimed:
        out_err.append(
            {
                "step": "digest_external",
                "message": "slack_attempt_not_claimed",
                "error_class": "NotClaimed",
                "detail": "digest attempt already claimed; deferring row deletion",
            }
        )
        return out_ev, out_err

    text = (
        f"*Sentinel Prism* — {_slack_escape(title)}\n"
        f"*Team:* {_slack_escape(team_slug)}\n"
        f"{_slack_escape(body)}"
    )
    ok, err_class, detail, _hint = await send_slack_webhook_text(
        webhook_url=cfg.slack_webhook_url or "",
        text=text,
    )
    outcome = NotificationDeliveryOutcome.SUCCESS if ok else NotificationDeliveryOutcome.FAILURE
    await _finalize_attempt(
        session_factory,
        run_id=digest_run_id,
        item_url=item_url,
        channel=NotificationDeliveryChannel.SLACK_WEBHOOK,
        recipient_descriptor=descriptor,
        outcome=outcome,
        error_class=err_class,
        detail=_safe_detail(detail),
        provider_message_id=None,
    )
    out_ev.append(
        {
            "channel": "external_slack_webhook",
            "status": "recorded" if ok else "recorded_failure",
            "run_id": str(digest_run_id),
            "kind": "digest_batch",
            "attempts": 1,
            "outcome": outcome.value,
        }
    )
    if not ok:
        out_err.append(
            {
                "step": "digest_external",
                "message": "slack_webhook_failed",
                "error_class": err_class or "SlackError",
                "detail": _safe_detail(detail) or "",
            }
        )
    return out_ev, out_err
