"""Outbound notification adapters (Story 5.3)."""

from sentinel_prism.services.notifications.adapters.slack import send_slack_webhook_text
from sentinel_prism.services.notifications.adapters.smtp import send_smtp_email

__all__ = ["send_slack_webhook_text", "send_smtp_email"]
