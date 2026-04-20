"""Pure routing rule resolution (Story 5.1)."""

from __future__ import annotations

import uuid

from sentinel_prism.services.routing.resolve import RoutingRuleView, resolve_routing_decision


def _topic(
    *,
    ic: str,
    team: str = "t1",
    channel: str = "c1",
    prio: int = 1,
) -> RoutingRuleView:
    return RoutingRuleView(
        id=uuid.uuid4(),
        priority=prio,
        rule_type="topic",
        impact_category=ic,
        severity_value=None,
        team_slug=team,
        channel_slug=channel,
    )


def _sev(
    *,
    sev: str,
    team: str = "st",
    channel: str = "cs",
    prio: int = 1,
) -> RoutingRuleView:
    return RoutingRuleView(
        id=uuid.uuid4(),
        priority=prio,
        rule_type="severity",
        impact_category=None,
        severity_value=sev,
        team_slug=team,
        channel_slug=channel,
    )


def _cls(
    *,
    url: str = "https://x/a",
    severity: str = "medium",
    categories: list[str] | None = None,
    in_scope: bool = True,
) -> dict:
    return {
        "item_url": url,
        "severity": severity,
        "impact_categories": categories or ["labeling"],
        "in_scope": in_scope,
    }


def test_topic_match_sets_team_and_channel() -> None:
    topic = [_topic(ic="labeling", team="pharma", channel="chan-a")]
    out = resolve_routing_decision(_cls(), topic_rules=topic, severity_rules=[])
    assert out["matched"] is True
    assert out["team_slug"] == "pharma"
    assert out["channel_slug"] == "chan-a"
    assert out["matched_topic_rule_id"] is not None
    assert out["matched_severity_rule_id"] is None


def test_severity_overrides_channel_only() -> None:
    topic = [_topic(ic="labeling", team="pharma", channel="chan-topic")]
    severity = [_sev(sev="high", team="ignored", channel="chan-sev")]
    out = resolve_routing_decision(
        _cls(severity="high"),
        topic_rules=topic,
        severity_rules=severity,
    )
    assert out["matched"] is True
    assert out["team_slug"] == "pharma"
    assert out["channel_slug"] == "chan-sev"
    assert out["matched_severity_rule_id"] is not None


def test_topic_priority_first_wins() -> None:
    t1 = _topic(ic="labeling", team="first", channel="c1", prio=1)
    t2 = _topic(ic="labeling", team="second", channel="c2", prio=2)
    out = resolve_routing_decision(
        _cls(),
        topic_rules=[t1, t2],
        severity_rules=[],
    )
    assert out["team_slug"] == "first"


def test_severity_only_match() -> None:
    sev = [_sev(sev="critical", team="oncall", channel="pager")]
    out = resolve_routing_decision(
        _cls(severity="critical", categories=[]),
        topic_rules=[],
        severity_rules=sev,
    )
    assert out["matched"] is True
    assert out["team_slug"] == "oncall"
    assert out["channel_slug"] == "pager"


def test_no_match_emits_reason() -> None:
    out = resolve_routing_decision(
        _cls(severity="medium", categories=["unknown-topic"]),
        topic_rules=[_topic(ic="labeling")],
        severity_rules=[_sev(sev="low")],
    )
    assert out["matched"] is False
    assert out["no_match_reason"] == "no_rule_matched"


def test_out_of_scope_classification() -> None:
    out = resolve_routing_decision(
        _cls(in_scope=False),
        topic_rules=[_topic(ic="labeling")],
        severity_rules=[],
    )
    assert out["matched"] is False
    assert out["no_match_reason"] == "out_of_scope"


def test_no_severity_or_categories() -> None:
    out = resolve_routing_decision(
        {
            "item_url": "u",
            "in_scope": True,
            "severity": None,
            "impact_categories": [],
        },
        topic_rules=[],
        severity_rules=[],
    )
    assert out["matched"] is False
    assert out["no_match_reason"] == "no_severity_or_categories"


def test_output_severity_is_normalized() -> None:
    """Story 5.1 review P11: echo the normalized severity, not the raw input.

    Downstream dedup and grouping compare on the decision's ``severity``
    field; if matching uses ``_norm_severity`` but the output echoes the
    raw ``"HIGH"``, the same logical severity splits into two buckets.
    """

    out = resolve_routing_decision(
        _cls(severity="  HIGH  "),
        topic_rules=[],
        severity_rules=[_sev(sev="high", team="t", channel="c")],
    )
    assert out["severity"] == "high"


def test_non_string_impact_categories_are_filtered() -> None:
    """Story 5.1 review P6: only string entries contribute to matching.

    Without the ``isinstance(x, str)`` guard, ``None`` / ``True`` / nested
    dicts would coerce through ``str(x).strip().lower()`` into synthetic
    categories like ``"none"`` / ``"true"`` and could match a legitimate
    rule with that key.
    """

    out = resolve_routing_decision(
        {
            "item_url": "u",
            "in_scope": True,
            "severity": "medium",
            "impact_categories": [None, True, {"k": 1}, 42, "  Labeling  "],
        },
        topic_rules=[_topic(ic="labeling", team="t", channel="c")],
        severity_rules=[],
    )
    assert out["impact_categories"] == ["labeling"]
    assert out["matched"] is True
    assert out["team_slug"] == "t"


def test_in_scope_stringified_false_treated_as_out_of_scope() -> None:
    """Story 5.1 review P5: ``"false"`` / ``0`` / ``None`` should not route.

    A JSON round-trip or legacy producer can yield non-``bool`` falsy
    values. The identity check ``in_scope is False`` let them route as
    in-scope; normalization closes that gap.
    """

    for falsy in ("false", "False", "FALSE", "no", "off", 0):
        out = resolve_routing_decision(
            {
                "item_url": "u",
                "in_scope": falsy,
                "severity": "medium",
                "impact_categories": ["labeling"],
            },
            topic_rules=[_topic(ic="labeling", team="t", channel="c")],
            severity_rules=[],
        )
        assert out["matched"] is False, f"{falsy!r} should short-circuit"
        assert out["no_match_reason"] == "out_of_scope"

    # Preserve original contract: missing / None in_scope defaults to in-scope.
    for benign in (None, True, 1, "true"):
        out = resolve_routing_decision(
            {
                "item_url": "u",
                "in_scope": benign,
                "severity": "medium",
                "impact_categories": ["labeling"],
            },
            topic_rules=[_topic(ic="labeling", team="t", channel="c")],
            severity_rules=[],
        )
        assert out["matched"] is True, f"{benign!r} should NOT short-circuit"


def test_severity_only_match_backfills_team_from_severity_row() -> None:
    """Story 5.1 review D1: document + test the severity ``team_slug`` backfill.

    Per the resolved Decision 1 (keep behavior, document it), a severity
    rule **does** supply ``team_slug`` when no topic rule matched. This
    locks in the contract so a future refactor cannot silently regress.
    """

    sev = [_sev(sev="critical", team="oncall", channel="pager")]
    out = resolve_routing_decision(
        _cls(severity="critical", categories=[]),
        topic_rules=[],
        severity_rules=sev,
    )
    assert out["team_slug"] == "oncall"
    assert out["channel_slug"] == "pager"
    assert out["matched_severity_rule_id"] is not None
    assert out["matched_topic_rule_id"] is None
