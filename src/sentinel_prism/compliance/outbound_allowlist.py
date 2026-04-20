"""Allowlisted backend modules that may import or use :mod:`httpx` directly (Story 5.5, FR41).

The human-readable inventory lives in ``docs/regulatory-outbound-allowlist.md``. When adding a
legitimate new ``httpx`` integration, update **this** frozenset and that document together so CI
and auditors stay aligned.
"""

from __future__ import annotations

# Paths relative to the ``sentinel_prism`` package directory (``src/sentinel_prism/``).
ALLOWED_HTTPX_SOURCE_FILES: frozenset[str] = frozenset(
    {
        "services/connectors/fetch_retry.py",
        "services/connectors/http_client.py",
        "services/connectors/http_fetch.py",
        "services/connectors/html_fallback.py",
        "services/connectors/poll.py",
        "services/connectors/rss_fetch.py",
        "services/connectors/scout_fetch.py",
        "services/notifications/adapters/slack.py",
    }
)
