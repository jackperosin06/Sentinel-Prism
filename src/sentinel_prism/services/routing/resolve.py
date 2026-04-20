"""Deterministic routing resolution from mock DB rules (Story 5.1 — FR21).

**Precedence**

1. **Topic rules** (``rule_type == topic``): ordered by ``priority`` ascending,
   with ``id`` as a deterministic secondary key. The first rule whose
   ``impact_category`` is contained in the classification's
   ``impact_categories`` list sets ``team_slug`` and ``channel_slug``.

2. **Severity rules** (``rule_type == severity``): ordered by ``priority``
   ascending, ``id`` secondary. The first rule whose ``severity_value``
   equals the classification's normalized ``severity`` sets ``channel_slug``
   and, **only when no topic rule matched**, backfills ``team_slug`` from
   the severity row (so severity-only routing still produces a usable
   ``(team_slug, channel_slug)`` pair). When a topic rule matched,
   ``team_slug`` is preserved from that topic rule and the severity rule
   overrides ``channel_slug`` only.

3. If neither topic nor severity matches, ``matched`` is ``False`` and slugs
   are ``None`` with a ``no_match_reason`` code.

Out-of-scope rows (``in_scope`` resolves to false) short-circuit with
``no_match_reason="out_of_scope"`` and are not evaluated against severity
rules — callers that want severity-driven escalation on out-of-scope items
must flip ``in_scope`` upstream.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RoutingRuleView:
    """ORM-free snapshot for unit tests and pure resolution."""

    id: uuid.UUID
    priority: int
    rule_type: str
    impact_category: str | None
    severity_value: str | None
    team_slug: str
    channel_slug: str


_FALSY_STRS = frozenset({"false", "no", "n", "off"})


def _is_out_of_scope(raw: Any) -> bool:
    """Treat a classification's ``in_scope`` field as out-of-scope.

    Classifiers should emit a Python ``bool``, but JSON round-trips, legacy
    fixtures, and external producers can yield stringified booleans or
    numeric zero. A single identity check (``raw is False``) would let all
    of those bypass the guard and route as if in-scope. This normalizer
    recognizes the explicitly-false forms while preserving the original
    contract that a missing or ``None`` ``in_scope`` field defaults to
    in-scope (so classifiers that never emit the field keep working).
    """

    if raw is False:
        return True
    if raw is None:
        return False
    if isinstance(raw, str):
        return raw.strip().lower() in _FALSY_STRS
    # Catch numeric 0 without tripping on ``True`` (``bool`` is a subclass
    # of ``int``; ``True is False`` is False and ``True == 0`` is False, so
    # ``isinstance(raw, bool)`` already short-circuited the genuine-bool
    # cases above).
    if isinstance(raw, bool):
        return False
    if isinstance(raw, (int, float)) and raw == 0:
        return True
    return False


def _norm_severity(raw: Any) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip().lower()
    return s or None


def _norm_impact_categories(raw: Any) -> list[str]:
    """Return normalized, non-empty string categories.

    Skips non-string entries so a malformed classification (``None``, nested
    dicts, booleans) cannot coerce into bogus match keys like ``"none"`` /
    ``"true"`` via ``str(x)``.
    """

    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for x in raw:
        if not isinstance(x, str):
            continue
        norm = x.strip().lower()
        if norm:
            out.append(norm)
    return out


def resolve_routing_decision(
    classification: dict[str, Any],
    *,
    topic_rules: list[RoutingRuleView],
    severity_rules: list[RoutingRuleView],
) -> dict[str, Any]:
    """Return one ``routing_decisions`` entry (JSON-serializable)."""

    item_url = str(classification.get("item_url") or "")
    categories = _norm_impact_categories(classification.get("impact_categories"))
    sev = _norm_severity(classification.get("severity"))
    if _is_out_of_scope(classification.get("in_scope")):
        return {
            "item_url": item_url,
            "severity": sev,
            "impact_categories": categories,
            "matched": False,
            "team_slug": None,
            "channel_slug": None,
            "matched_topic_rule_id": None,
            "matched_severity_rule_id": None,
            "no_match_reason": "out_of_scope",
        }

    team_slug: str | None = None
    channel_slug: str | None = None
    topic_id: str | None = None
    sev_id: str | None = None

    cat_set = set(categories)
    for rule in topic_rules:
        ic = rule.impact_category
        if ic is not None and ic.strip().lower() in cat_set:
            team_slug = rule.team_slug
            channel_slug = rule.channel_slug
            topic_id = str(rule.id)
            break

    for rule in severity_rules:
        rv = rule.severity_value
        if rv is not None and sev is not None and rv.strip().lower() == sev:
            channel_slug = rule.channel_slug
            sev_id = str(rule.id)
            if team_slug is None:
                team_slug = rule.team_slug
            break

    matched = team_slug is not None or (
        sev_id is not None and channel_slug is not None
    )
    no_match_reason: str | None = None
    if not matched:
        if sev is None and not cat_set:
            no_match_reason = "no_severity_or_categories"
        else:
            no_match_reason = "no_rule_matched"

    return {
        "item_url": item_url,
        "severity": sev,
        "impact_categories": categories,
        "matched": matched,
        "team_slug": team_slug,
        "channel_slug": channel_slug,
        "matched_topic_rule_id": topic_id,
        "matched_severity_rule_id": sev_id,
        "no_match_reason": no_match_reason if not matched else None,
    }
