"""Shared notification-delivery attempt helpers.

Used by both immediate external delivery and digest flush delivery.
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from sentinel_prism.db.models import NotificationDeliveryChannel, NotificationDeliveryOutcome
from sentinel_prism.db.repositories import notification_delivery_attempts as delivery_repo

_MAX_DETAIL_CHARS = 500
_SLACK_MENTION_RE = re.compile(r"<[!@][^>]*>")


def slack_escape(text: str) -> str:
    """Defuse Slack mention/control syntax in interpolated text."""
    safe = _SLACK_MENTION_RE.sub("[mention-redacted]", text or "")
    safe = re.sub(r"@(channel|here|everyone)\b", r"@\\\1", safe, flags=re.IGNORECASE)
    return safe


def safe_detail(text: str | None) -> str | None:
    """Normalize and truncate a detail string for DB/log persistence."""
    if text is None:
        return None
    cleaned = text.replace("\r", " ").replace("\n", " ").strip()
    if not cleaned:
        return None
    if len(cleaned) <= _MAX_DETAIL_CHARS:
        return cleaned
    return cleaned[: _MAX_DETAIL_CHARS - 1] + "…"


def slack_descriptor(team_slug_key: str) -> str:
    """Stable idempotency descriptor per (team, slack webhook)."""
    return f"slack_webhook:{team_slug_key}"


async def claim_attempt(
    session_factory: async_sessionmaker,
    *,
    run_id: uuid.UUID,
    item_url: str,
    channel: NotificationDeliveryChannel,
    recipient_descriptor: str,
) -> tuple[bool, str | None]:
    """Return ``(claimed, error_detail)`` for pending-attempt claim."""
    try:
        async with session_factory() as session:
            try:
                claimed = await delivery_repo.claim_attempt_pending(
                    session,
                    run_id=run_id,
                    item_url=item_url,
                    channel=channel,
                    recipient_descriptor=recipient_descriptor,
                )
                await session.commit()
                return claimed, None
            except SQLAlchemyError as exc:
                await session.rollback()
                return False, f"{type(exc).__name__}: {str(exc)[:200]}"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {str(exc)[:200]}"


async def finalize_attempt(
    session_factory: async_sessionmaker,
    *,
    run_id: uuid.UUID,
    item_url: str,
    channel: NotificationDeliveryChannel,
    recipient_descriptor: str,
    outcome: NotificationDeliveryOutcome,
    error_class: str | None,
    detail: str | None,
    provider_message_id: str | None,
) -> str | None:
    """Return ``None`` on success, or an error detail string."""
    try:
        async with session_factory() as session:
            try:
                await delivery_repo.finalize_attempt_outcome(
                    session,
                    run_id=run_id,
                    item_url=item_url,
                    channel=channel,
                    recipient_descriptor=recipient_descriptor,
                    outcome=outcome,
                    error_class=error_class,
                    detail=detail,
                    provider_message_id=provider_message_id,
                )
                await session.commit()
                return None
            except SQLAlchemyError as exc:
                await session.rollback()
                return f"{type(exc).__name__}: {str(exc)[:200]}"
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {str(exc)[:200]}"
