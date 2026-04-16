"""Shared HTTP settings for connectors (Story 2.3)."""

from __future__ import annotations

from typing import Any

import httpx

CONNECTOR_USER_AGENT = "SentinelPrism/0.1 (regulatory-ingestion-connector)"

# Reasonable defaults for public regulator feeds (Story 2.4 adds retry/backoff).
CONNECTOR_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
CONNECTOR_MAX_REDIRECTS = 10


def connector_async_client(**kwargs: Any) -> httpx.AsyncClient:
    """Build an ``AsyncClient`` for RSS/HTTP fetches.

    Extra ``kwargs`` are forwarded (e.g. ``transport=`` for tests).  ``headers``
    in ``kwargs`` are **merged** with the default ``User-Agent``; they do not
    replace it.
    """

    default_headers = {"User-Agent": CONNECTOR_USER_AGENT}
    extra_headers: dict[str, str] = kwargs.pop("headers", {}) or {}
    merged_headers = {**default_headers, **extra_headers}

    params: dict[str, Any] = {
        "timeout": CONNECTOR_TIMEOUT,
        "follow_redirects": True,
        "max_redirects": CONNECTOR_MAX_REDIRECTS,
        "headers": merged_headers,
    }
    params.update(kwargs)
    return httpx.AsyncClient(**params)
