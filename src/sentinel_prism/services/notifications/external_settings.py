"""Environment-driven external notification channel config (Story 5.3 — FR25, NFR3).

Logs a WARNING when env values are malformed rather than silently
coercing to defaults, so a misconfigured sandbox does not look like an
intentional disable from the operator's perspective.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Literal


logger = logging.getLogger(__name__)


ExternalChannelMode = Literal["none", "smtp", "slack"]

_VALID_MODES: tuple[str, ...] = ("none", "smtp", "slack", "slack_webhook")


@dataclass(frozen=True, slots=True)
class ExternalNotificationSettings:
    """Sandbox-only outbound settings — all secrets from environment."""

    mode: ExternalChannelMode
    smtp_host: str | None
    smtp_port: int
    smtp_user: str | None
    smtp_password: str | None
    smtp_from: str | None
    smtp_use_tls: bool
    slack_webhook_url: str | None


def load_external_notification_settings() -> ExternalNotificationSettings:
    raw_mode = (os.environ.get("NOTIFICATIONS_EXTERNAL_CHANNEL") or "none").strip().lower()
    if raw_mode in ("", "none", "off", "disabled"):
        mode: ExternalChannelMode = "none"
    elif raw_mode == "smtp":
        mode = "smtp"
    elif raw_mode in ("slack", "slack_webhook"):
        mode = "slack"
    else:
        logger.warning(
            "external_notification_settings",
            extra={
                "event": "external_channel_unknown_mode",
                "ctx": {
                    "raw": raw_mode,
                    "valid": list(_VALID_MODES),
                    "effective_mode": "none",
                },
            },
        )
        mode = "none"

    smtp_port = _parse_smtp_port(os.environ.get("NOTIFICATIONS_SMTP_PORT"))
    smtp_use_tls = _parse_use_tls(os.environ.get("NOTIFICATIONS_SMTP_USE_TLS"))
    slack_webhook_url = _validate_webhook_url(
        _empty_to_none(os.environ.get("NOTIFICATIONS_SLACK_WEBHOOK_URL")),
        active=(mode == "slack"),
    )

    return ExternalNotificationSettings(
        mode=mode,
        smtp_host=_empty_to_none(os.environ.get("NOTIFICATIONS_SMTP_HOST")),
        smtp_port=smtp_port,
        smtp_user=_empty_to_none(os.environ.get("NOTIFICATIONS_SMTP_USER")),
        smtp_password=_empty_to_none(os.environ.get("NOTIFICATIONS_SMTP_PASSWORD")),
        smtp_from=_empty_to_none(os.environ.get("NOTIFICATIONS_SMTP_FROM")),
        smtp_use_tls=smtp_use_tls,
        slack_webhook_url=slack_webhook_url,
    )


def _parse_smtp_port(raw: str | None) -> int:
    if raw is None:
        return 587
    s = raw.strip()
    if not s:
        return 587
    try:
        port = int(s)
    except ValueError:
        logger.warning(
            "external_notification_settings",
            extra={
                "event": "external_smtp_port_invalid",
                "ctx": {"raw": s, "effective_port": 587},
            },
        )
        return 587
    if port < 1 or port > 65535:
        logger.warning(
            "external_notification_settings",
            extra={
                "event": "external_smtp_port_out_of_range",
                "ctx": {"raw": s, "effective_port": 587},
            },
        )
        return 587
    return port


def _parse_use_tls(raw: str | None) -> bool:
    if raw is None:
        return True
    s = raw.strip().lower()
    if s in ("0", "false", "no", "off"):
        return False
    return True


def _validate_webhook_url(url: str | None, *, active: bool) -> str | None:
    """Log a warning on scheme/shape problems, but still return the URL.

    Returning ``None`` would make a misconfigured operator see "no
    webhook configured" which is even more confusing than letting the
    downstream send fail with a transport error. The adapter will scrub
    secrets from any failure detail, so a broken URL never leaks.
    Validation is best-effort and only fires when Slack mode is active.
    """

    if url is None:
        return None
    stripped = url.strip()
    if not stripped:
        return None
    if not active:
        return stripped
    if not stripped.lower().startswith("https://"):
        logger.warning(
            "external_notification_settings",
            extra={
                "event": "external_slack_webhook_scheme_unsafe",
                "ctx": {"scheme_required": "https"},
            },
        )
    return stripped


def _empty_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    s = value.strip()
    return s or None
