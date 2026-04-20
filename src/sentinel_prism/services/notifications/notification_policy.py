"""Env-backed notification scheduling policy (Story 5.4 — FR22).

Single source of truth for **immediate** vs **digest** severities and flush
cadence. Loaded once per process; tests may call :func:`reload_notification_policy`.
"""

from __future__ import annotations

import functools
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_DEFAULT_IMMEDIATE = "critical,high"
_CANONICAL_SEVERITIES = frozenset({"critical", "high", "medium", "low"})


@dataclass(frozen=True)
class NotificationPolicySettings:
    """Policy for immediate vs digest paths."""

    immediate_severities: frozenset[str]
    digest_enabled: bool
    digest_flush_interval_seconds: int
    max_external_immediate_per_run: int
    digest_flush_batch_max: int

    @property
    def max_external_immediate_per_route(self) -> int:
        """Backward-compatible alias for existing callers/tests."""
        return self.max_external_immediate_per_run


def _parse_severity_list(raw: str) -> frozenset[str]:
    parts = [p.strip().lower() for p in raw.split(",")]
    out = {p for p in parts if p}
    unknown = sorted(out - _CANONICAL_SEVERITIES)
    if unknown:
        logger.warning(
            "notification_policy_unknown_severities",
            extra={"ctx": {"unknown": unknown, "raw": raw}},
        )
    out = out & _CANONICAL_SEVERITIES
    if not out:
        return frozenset({"critical", "high"})
    return frozenset(out)


def _parse_bool(raw: str | None, default: bool) -> bool:
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _parse_int(raw: str | None, default: int, *, min_v: int, max_v: int) -> int:
    if raw is None or not str(raw).strip():
        return default
    try:
        v = int(str(raw).strip(), 10)
    except ValueError:
        logger.warning(
            "notification_policy_invalid_int",
            extra={"ctx": {"raw": raw, "default": default}},
        )
        return default
    return max(min_v, min(max_v, v))


@functools.lru_cache(maxsize=1)
def load_notification_policy() -> NotificationPolicySettings:
    """Load policy from environment (cached)."""

    imm = os.environ.get(
        "NOTIFICATIONS_IMMEDIATE_SEVERITIES", _DEFAULT_IMMEDIATE
    ).strip()
    immediate_severities = _parse_severity_list(imm)

    digest_enabled = _parse_bool(os.environ.get("NOTIFICATIONS_DIGEST_ENABLED"), True)
    interval = _parse_int(
        os.environ.get("NOTIFICATIONS_DIGEST_FLUSH_INTERVAL_SECONDS"),
        900,
        min_v=60,
        max_v=86400,
    )
    cap_raw = os.environ.get("NOTIFICATIONS_MAX_EXTERNAL_IMMEDIATE_PER_RUN")
    if cap_raw is None:
        cap_raw = os.environ.get("NOTIFICATIONS_MAX_EXTERNAL_IMMEDIATE_PER_ROUTE")
    cap = _parse_int(
        cap_raw,
        50,
        min_v=1,
        max_v=500,
    )
    batch = _parse_int(
        os.environ.get("NOTIFICATIONS_DIGEST_FLUSH_BATCH_MAX"),
        500,
        min_v=1,
        max_v=10000,
    )

    return NotificationPolicySettings(
        immediate_severities=immediate_severities,
        digest_enabled=digest_enabled,
        digest_flush_interval_seconds=interval,
        max_external_immediate_per_run=cap,
        digest_flush_batch_max=batch,
    )


def reload_notification_policy() -> NotificationPolicySettings:
    """Clear cache (tests) and return fresh policy."""

    load_notification_policy.cache_clear()
    return load_notification_policy()
