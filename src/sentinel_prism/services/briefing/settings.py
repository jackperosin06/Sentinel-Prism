"""Grouping dimensions for briefing generation (Story 4.3 — FR18).

MVP configuration is env-driven and read once per ``node_brief`` invocation.

``BRIEFING_GROUPING_DIMENSIONS``
    JSON array of dimension names (ordered). Allowed values:
    ``date_bucket``, ``jurisdiction``, ``severity``, ``topic``.
    Must be non-empty and duplicate-free. Unset falls back to the default
    ordering ``["date_bucket","jurisdiction","severity","topic"]``.
    Example: ``BRIEFING_GROUPING_DIMENSIONS='["severity","jurisdiction"]'``.

``BRIEFING_DATE_BUCKET``
    ``day`` (default) or ``month``. Applies to the ``date_bucket`` dimension
    only. Buckets use the normalized row's ``published_at`` rendered in UTC
    (see :func:`sentinel_prism.graph.nodes.brief._bucket_dt`); when
    ``published_at`` is missing or unparseable, the bucket is the literal
    string ``"unknown"``. Unknown values for this env var raise ``ValueError``
    — operators must correct the configuration rather than silently defaulting.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

_VALID_DIMENSIONS = frozenset({"date_bucket", "jurisdiction", "severity", "topic"})
_VALID_DATE_BUCKETS = frozenset({"day", "month"})

_DEFAULT_DIMENSIONS: tuple[str, ...] = (
    "date_bucket",
    "jurisdiction",
    "severity",
    "topic",
)


@dataclass(frozen=True)
class BriefingGroupingSettings:
    """Ordered dimension keys used when partitioning updates into briefing groups."""

    dimensions: tuple[str, ...]
    date_bucket_granularity: str  # "day" | "month"


def load_briefing_grouping_settings() -> BriefingGroupingSettings:
    """Read grouping config from the environment (call per ``node_brief`` for tests)."""

    raw = os.environ.get("BRIEFING_GROUPING_DIMENSIONS", "").strip()
    if not raw:
        dims: tuple[str, ...] = _DEFAULT_DIMENSIONS
    else:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "BRIEFING_GROUPING_DIMENSIONS must be valid JSON array of strings"
            ) from exc
        if not isinstance(parsed, list) or any(not isinstance(x, str) for x in parsed):
            raise ValueError(
                "BRIEFING_GROUPING_DIMENSIONS must be a JSON array of strings"
            )
        if not parsed:
            raise ValueError(
                "BRIEFING_GROUPING_DIMENSIONS must not be empty; "
                f"allowed: {sorted(_VALID_DIMENSIONS)}"
            )
        bad = [x for x in parsed if x not in _VALID_DIMENSIONS]
        if bad:
            raise ValueError(
                f"Unknown briefing dimension(s): {bad}; "
                f"allowed: {sorted(_VALID_DIMENSIONS)}"
            )
        if len(set(parsed)) != len(parsed):
            dupes = sorted({x for x in parsed if parsed.count(x) > 1})
            raise ValueError(
                f"BRIEFING_GROUPING_DIMENSIONS must not contain duplicates; "
                f"repeated: {dupes}"
            )
        dims = tuple(parsed)

    gran = os.environ.get("BRIEFING_DATE_BUCKET", "day").strip().lower()
    if gran not in _VALID_DATE_BUCKETS:
        raise ValueError(
            f"BRIEFING_DATE_BUCKET={gran!r} is invalid; "
            f"allowed: {sorted(_VALID_DATE_BUCKETS)}"
        )

    return BriefingGroupingSettings(
        dimensions=dims,
        date_bucket_granularity=gran,
    )
