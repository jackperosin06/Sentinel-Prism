"""Environment-backed web search settings (Story 3.7, NFR12 / FR43)."""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DEFAULT_WEB_SEARCH_MAX_RESULTS = 5
WEB_SEARCH_MAX_RESULTS_LOWER = 1
WEB_SEARCH_MAX_RESULTS_UPPER = 15
DEFAULT_TAVILY_TIMEOUT = 20.0
TAVILY_TIMEOUT_LOWER = 5.0
TAVILY_TIMEOUT_UPPER = 120.0


def _env_truthy(name: str) -> bool:
    raw = os.getenv(name, "").strip().lower()
    return raw in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class WebSearchSettings:
    """Feature flag and Tavily call knobs (no secrets)."""

    enabled: bool
    max_results: int
    tavily_timeout: float


def get_tavily_api_key_for_search() -> str | None:
    """Resolve API key without logging it.

    Prefer ``SENTINEL_TAVILY_API_KEY`` when set; else ``TAVILY_API_KEY`` (Tavily SDK default).
    """

    for env_name in ("SENTINEL_TAVILY_API_KEY", "TAVILY_API_KEY"):
        v = os.getenv(env_name, "").strip()
        if v:
            return v
    return None


def get_web_search_settings() -> WebSearchSettings:
    """Read env fresh on each call (tests use ``monkeypatch.setenv``)."""

    enabled = _env_truthy("SENTINEL_WEB_SEARCH_ENABLED")

    raw_mr = os.getenv(
        "SENTINEL_WEB_SEARCH_MAX_RESULTS", str(DEFAULT_WEB_SEARCH_MAX_RESULTS)
    ).strip() or str(DEFAULT_WEB_SEARCH_MAX_RESULTS)
    try:
        max_results = int(raw_mr)
    except ValueError:
        logger.warning(
            "web_search_settings",
            extra={
                "event": "web_search_max_results_parse_error",
                "ctx": {"raw": raw_mr, "fallback": DEFAULT_WEB_SEARCH_MAX_RESULTS},
            },
        )
        max_results = DEFAULT_WEB_SEARCH_MAX_RESULTS

    clamped_mr = max(
        WEB_SEARCH_MAX_RESULTS_LOWER,
        min(max_results, WEB_SEARCH_MAX_RESULTS_UPPER),
    )
    if clamped_mr != max_results:
        logger.warning(
            "web_search_settings",
            extra={
                "event": "web_search_max_results_clamped",
                "ctx": {
                    "requested": max_results,
                    "clamped": clamped_mr,
                    "bounds": [
                        WEB_SEARCH_MAX_RESULTS_LOWER,
                        WEB_SEARCH_MAX_RESULTS_UPPER,
                    ],
                },
            },
        )

    raw_to = os.getenv(
        "SENTINEL_TAVILY_TIMEOUT", str(DEFAULT_TAVILY_TIMEOUT)
    ).strip() or str(DEFAULT_TAVILY_TIMEOUT)
    try:
        tavily_timeout = float(raw_to)
    except ValueError:
        logger.warning(
            "web_search_settings",
            extra={
                "event": "tavily_timeout_parse_error",
                "ctx": {"raw": raw_to, "fallback": DEFAULT_TAVILY_TIMEOUT},
            },
        )
        tavily_timeout = DEFAULT_TAVILY_TIMEOUT

    # ``float('nan')`` / ``float('inf')`` parse successfully but break the
    # min/max clamp below (NaN comparisons always return False, producing
    # NaN/inf output and misleading clamp logs). Reject non-finite values.
    if not math.isfinite(tavily_timeout):
        logger.warning(
            "web_search_settings",
            extra={
                "event": "tavily_timeout_non_finite",
                "ctx": {"raw": raw_to, "fallback": DEFAULT_TAVILY_TIMEOUT},
            },
        )
        tavily_timeout = DEFAULT_TAVILY_TIMEOUT

    clamped_to = max(
        TAVILY_TIMEOUT_LOWER, min(tavily_timeout, TAVILY_TIMEOUT_UPPER)
    )
    if clamped_to != tavily_timeout:
        logger.warning(
            "web_search_settings",
            extra={
                "event": "tavily_timeout_clamped",
                "ctx": {
                    "requested": tavily_timeout,
                    "clamped": clamped_to,
                    "bounds": [TAVILY_TIMEOUT_LOWER, TAVILY_TIMEOUT_UPPER],
                },
            },
        )

    return WebSearchSettings(
        enabled=enabled,
        max_results=clamped_mr,
        tavily_timeout=clamped_to,
    )
