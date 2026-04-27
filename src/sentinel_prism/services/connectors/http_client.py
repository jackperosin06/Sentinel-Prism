"""Shared HTTP settings for connectors (Story 2.3)."""

from __future__ import annotations

from typing import Any

import httpx

CONNECTOR_USER_AGENT = (
    "Mozilla/5.0 (compatible; SentinelPrism/1.0; regulatory-ingestion-bot)"
)

# Standard RSS/Atom Accept header — improves compatibility with CDN WAFs (e.g. EMA/Cloudfront)
# that inspect Accept before deciding whether to serve the feed or return a challenge page.
_CONNECTOR_DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": CONNECTOR_USER_AGENT,
    "Accept": (
        "application/rss+xml, application/atom+xml, application/xml;q=0.9, "
        "text/xml;q=0.8, */*;q=0.5"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Reasonable defaults for public regulator feeds (Story 2.4 adds retry/backoff).
CONNECTOR_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
CONNECTOR_MAX_REDIRECTS = 10


def connector_async_client(**kwargs: Any) -> httpx.AsyncClient:
    """Build an ``AsyncClient`` for RSS/HTTP fetches.

    Extra ``kwargs`` are forwarded (e.g. ``transport=`` for tests).  ``headers``
    in ``kwargs`` are **merged** with the defaults; they do not replace them.
    """

    extra_headers: dict[str, str] = kwargs.pop("headers", {}) or {}
    merged_headers = {**_CONNECTOR_DEFAULT_HEADERS, **extra_headers}

    params: dict[str, Any] = {
        "timeout": CONNECTOR_TIMEOUT,
        "follow_redirects": True,
        "max_redirects": CONNECTOR_MAX_REDIRECTS,
        "headers": merged_headers,
    }
    params.update(kwargs)
    return httpx.AsyncClient(**params)
