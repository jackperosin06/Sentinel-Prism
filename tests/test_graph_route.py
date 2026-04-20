"""Route graph node — mock rules → ``routing_decisions`` (Story 5.1)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from sentinel_prism.db.models import RoutingRuleType
from sentinel_prism.graph import new_pipeline_state
from sentinel_prism.graph.nodes.route import node_route


def _topic_row(
    *,
    ic: str,
    team: str = "team-x",
    channel: str = "ch-x",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        priority=1,
        rule_type=RoutingRuleType.TOPIC,
        impact_category=ic,
        severity_value=None,
        team_slug=team,
        channel_slug=channel,
    )


@pytest.mark.asyncio
async def test_node_route_populates_routing_decisions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_topic(_s: object) -> list[SimpleNamespace]:
        return [_topic_row(ic="labeling")]

    async def fake_sev(_s: object) -> list[SimpleNamespace]:
        return []

    monkeypatch.setattr(
        "sentinel_prism.graph.nodes.route.routing_rules_repo.list_topic_rules_ordered",
        fake_topic,
    )
    monkeypatch.setattr(
        "sentinel_prism.graph.nodes.route.routing_rules_repo.list_severity_rules_ordered",
        fake_sev,
    )

    st = new_pipeline_state(uuid.uuid4())
    st["classifications"] = [
        {
            "item_url": "https://ex/item",
            "in_scope": True,
            "severity": "medium",
            "impact_categories": ["labeling"],
        }
    ]
    out = await node_route(st)
    assert "routing_decisions" in out
    assert len(out["routing_decisions"]) == 1
    d0 = out["routing_decisions"][0]
    assert d0["matched"] is True
    assert d0["team_slug"] == "team-x"
    assert d0["channel_slug"] == "ch-x"


@pytest.mark.asyncio
async def test_node_route_skips_duplicate_item_urls_in_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"topic": 0}

    async def fake_topic(_s: object) -> list[SimpleNamespace]:
        calls["topic"] += 1
        return [_topic_row(ic="labeling")]

    async def fake_sev(_s: object) -> list[SimpleNamespace]:
        return []

    monkeypatch.setattr(
        "sentinel_prism.graph.nodes.route.routing_rules_repo.list_topic_rules_ordered",
        fake_topic,
    )
    monkeypatch.setattr(
        "sentinel_prism.graph.nodes.route.routing_rules_repo.list_severity_rules_ordered",
        fake_sev,
    )

    st = new_pipeline_state(uuid.uuid4())
    url = "https://ex/dup"
    st["classifications"] = [
        {"item_url": url, "in_scope": True, "severity": "low", "impact_categories": ["labeling"]},
    ]
    st["routing_decisions"] = [
        {
            "item_url": url,
            "matched": True,
            "team_slug": "old",
            "channel_slug": "old",
        }
    ]
    out = await node_route(st)
    assert out["routing_decisions"] == []
    assert calls["topic"] == 1


@pytest.mark.asyncio
async def test_node_route_empty_classifications_returns_dict_and_skips_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Story 5.1 review D3 + former Critical P1.

    Empty classifications must (a) return a state-delta ``dict`` (not the
    ``list`` returned by ``_emit_routing_audit_if_needed``), so the
    LangGraph reducer merges cleanly, and (b) NOT emit ``ROUTING_APPLIED``
    — the audit trail should only record runs that actually produced
    routing work.
    """

    audit_called = {"n": 0}

    async def fake_emit(*args, **kwargs):  # type: ignore[no-untyped-def]
        audit_called["n"] += 1
        return []

    monkeypatch.setattr(
        "sentinel_prism.graph.nodes.route._emit_routing_audit_if_needed",
        fake_emit,
    )

    st = new_pipeline_state(uuid.uuid4())
    st["classifications"] = []

    out = await node_route(st)
    assert isinstance(out, dict)
    assert out == {"routing_decisions": []}
    assert audit_called["n"] == 0

    st["classifications"] = None
    out = await node_route(st)
    assert isinstance(out, dict)
    assert out == {"routing_decisions": []}
    assert audit_called["n"] == 0


@pytest.mark.asyncio
async def test_node_route_skips_audit_when_all_items_deduped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review D3: only emit ``ROUTING_APPLIED`` when at least one new decision.

    A replay where every classification URL is already in
    ``routing_decisions`` should be a silent no-op on the audit trail —
    the original run already wrote ``ROUTING_APPLIED``.
    """

    async def fake_topic(_s: object) -> list[SimpleNamespace]:
        return [_topic_row(ic="labeling")]

    async def fake_sev(_s: object) -> list[SimpleNamespace]:
        return []

    monkeypatch.setattr(
        "sentinel_prism.graph.nodes.route.routing_rules_repo.list_topic_rules_ordered",
        fake_topic,
    )
    monkeypatch.setattr(
        "sentinel_prism.graph.nodes.route.routing_rules_repo.list_severity_rules_ordered",
        fake_sev,
    )

    audit_called = {"n": 0}

    async def fake_emit(*args, **kwargs):  # type: ignore[no-untyped-def]
        audit_called["n"] += 1
        return []

    monkeypatch.setattr(
        "sentinel_prism.graph.nodes.route._emit_routing_audit_if_needed",
        fake_emit,
    )

    st = new_pipeline_state(uuid.uuid4())
    url = "https://ex/dup"
    st["classifications"] = [
        {"item_url": url, "in_scope": True, "severity": "low", "impact_categories": ["labeling"]},
    ]
    st["routing_decisions"] = [{"item_url": url, "matched": True}]

    out = await node_route(st)
    assert out["routing_decisions"] == []
    assert audit_called["n"] == 0


@pytest.mark.asyncio
async def test_node_route_normalizes_item_url_against_existing_decisions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review P7: trailing whitespace on ``item_url`` must still dedupe."""

    async def fake_topic(_s: object) -> list[SimpleNamespace]:
        return [_topic_row(ic="labeling")]

    async def fake_sev(_s: object) -> list[SimpleNamespace]:
        return []

    monkeypatch.setattr(
        "sentinel_prism.graph.nodes.route.routing_rules_repo.list_topic_rules_ordered",
        fake_topic,
    )
    monkeypatch.setattr(
        "sentinel_prism.graph.nodes.route.routing_rules_repo.list_severity_rules_ordered",
        fake_sev,
    )

    st = new_pipeline_state(uuid.uuid4())
    st["classifications"] = [
        {
            "item_url": "  https://ex/dup  ",
            "in_scope": True,
            "severity": "low",
            "impact_categories": ["labeling"],
        }
    ]
    st["routing_decisions"] = [{"item_url": "https://ex/dup", "matched": True}]

    out = await node_route(st)
    assert out["routing_decisions"] == []


@pytest.mark.asyncio
async def test_node_route_missing_item_url_is_reported_not_silently_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review P8: classifications without a URL must leave an observable trace."""

    async def fake_topic(_s: object) -> list[SimpleNamespace]:
        return []

    async def fake_sev(_s: object) -> list[SimpleNamespace]:
        return []

    monkeypatch.setattr(
        "sentinel_prism.graph.nodes.route.routing_rules_repo.list_topic_rules_ordered",
        fake_topic,
    )
    monkeypatch.setattr(
        "sentinel_prism.graph.nodes.route.routing_rules_repo.list_severity_rules_ordered",
        fake_sev,
    )

    st = new_pipeline_state(uuid.uuid4())
    st["classifications"] = [
        {"item_url": "", "in_scope": True, "severity": "low", "impact_categories": []},
        {"in_scope": True, "severity": "low", "impact_categories": []},
    ]

    out = await node_route(st)
    assert out["routing_decisions"] == []
    assert "errors" in out
    err_messages = {e["message"] for e in out["errors"]}
    assert "classifications_missing_item_url" in err_messages


@pytest.mark.asyncio
async def test_node_route_rule_load_sqlalchemy_error_returns_structured_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review P10 / P12: SQLAlchemyError branch returns both keys.

    Rule loading failures must produce ``{"routing_decisions": [],
    "errors": [...]}`` — the ``routing_decisions`` key must always be
    present so downstream consumers see a consistent shape.
    """

    async def boom(_s: object) -> list[SimpleNamespace]:
        raise SQLAlchemyError("connection lost")

    monkeypatch.setattr(
        "sentinel_prism.graph.nodes.route.routing_rules_repo.list_topic_rules_ordered",
        boom,
    )

    st = new_pipeline_state(uuid.uuid4())
    st["classifications"] = [
        {"item_url": "u", "in_scope": True, "severity": "low", "impact_categories": []}
    ]

    out = await node_route(st)
    assert out["routing_decisions"] == []
    assert "errors" in out
    assert out["errors"][0]["step"] == "route"
    assert out["errors"][0]["message"] == "routing_rules_load_failed"
    assert out["errors"][0]["error_class"] == "SQLAlchemyError"


@pytest.mark.asyncio
async def test_node_route_rule_load_non_db_exception_does_not_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review P4: broaden beyond ``SQLAlchemyError``.

    ``get_session_factory()`` raising ``RuntimeError`` (or any other
    non-DB exception) must still produce a structured ``errors`` entry
    instead of bubbling out of the node and crashing the whole graph.
    """

    def _raise_runtime() -> None:
        raise RuntimeError("session factory not initialized")

    monkeypatch.setattr(
        "sentinel_prism.graph.nodes.route.get_session_factory",
        _raise_runtime,
    )

    st = new_pipeline_state(uuid.uuid4())
    st["classifications"] = [
        {"item_url": "u", "in_scope": True, "severity": "low", "impact_categories": []}
    ]

    out = await node_route(st)
    assert out["routing_decisions"] == []
    assert out["errors"][0]["error_class"] == "RuntimeError"


@pytest.mark.asyncio
async def test_node_route_audit_write_failure_surfaces_error_and_preserves_decisions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review P10: the audit-write-failure branch must be exercised.

    Decisions remain persisted in state; an ``errors[]`` entry exposes
    the audit failure so operators can spot the divergence (tracked as a
    deferred architectural concern — see deferred-work.md Story 5.1).
    """

    async def fake_topic(_s: object) -> list[SimpleNamespace]:
        return [_topic_row(ic="labeling")]

    async def fake_sev(_s: object) -> list[SimpleNamespace]:
        return []

    monkeypatch.setattr(
        "sentinel_prism.graph.nodes.route.routing_rules_repo.list_topic_rules_ordered",
        fake_topic,
    )
    monkeypatch.setattr(
        "sentinel_prism.graph.nodes.route.routing_rules_repo.list_severity_rules_ordered",
        fake_sev,
    )

    async def fake_has(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return False

    async def fake_append(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise SQLAlchemyError("audit flush failed")

    monkeypatch.setattr(
        "sentinel_prism.graph.nodes.route.audit_events_repo.has_audit_event_for_run",
        fake_has,
    )
    monkeypatch.setattr(
        "sentinel_prism.graph.nodes.route.audit_events_repo.append_audit_event",
        fake_append,
    )

    st = new_pipeline_state(uuid.uuid4())
    st["classifications"] = [
        {
            "item_url": "https://ex/x",
            "in_scope": True,
            "severity": "low",
            "impact_categories": ["labeling"],
        }
    ]

    out = await node_route(st)
    assert len(out["routing_decisions"]) == 1
    assert "errors" in out
    assert out["errors"][0]["step"] == "audit_write"
    assert out["errors"][0]["message"] == "routing_audit_persist_failed"


@pytest.mark.asyncio
async def test_node_route_audit_integrity_error_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review P3: a race-losing unique-index violation must not surface as error.

    The partial unique index ``uq_audit_events_routing_applied_run_id``
    is specifically there to collapse a TOCTOU between two concurrent
    ``node_route`` runs on the same run_id — when the losing side sees
    ``IntegrityError`` the audit is already recorded by the peer, so the
    pipeline observes clean success.
    """

    async def fake_topic(_s: object) -> list[SimpleNamespace]:
        return [_topic_row(ic="labeling")]

    async def fake_sev(_s: object) -> list[SimpleNamespace]:
        return []

    monkeypatch.setattr(
        "sentinel_prism.graph.nodes.route.routing_rules_repo.list_topic_rules_ordered",
        fake_topic,
    )
    monkeypatch.setattr(
        "sentinel_prism.graph.nodes.route.routing_rules_repo.list_severity_rules_ordered",
        fake_sev,
    )

    async def fake_has(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return False

    async def fake_append(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise IntegrityError("insert", {}, Exception("unique"))

    monkeypatch.setattr(
        "sentinel_prism.graph.nodes.route.audit_events_repo.has_audit_event_for_run",
        fake_has,
    )
    monkeypatch.setattr(
        "sentinel_prism.graph.nodes.route.audit_events_repo.append_audit_event",
        fake_append,
    )

    st = new_pipeline_state(uuid.uuid4())
    st["classifications"] = [
        {
            "item_url": "https://ex/x",
            "in_scope": True,
            "severity": "low",
            "impact_categories": ["labeling"],
        }
    ]

    out = await node_route(st)
    assert len(out["routing_decisions"]) == 1
    assert "errors" not in out


@pytest.mark.asyncio
async def test_compiled_pipeline_brief_route_end_wiring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review P13: assert the compiled graph has ``brief → route → END``.

    The per-node unit tests invoke ``node_route`` directly; this test
    compiles ``build_regulatory_pipeline_graph`` and inspects the
    resulting graph's edges so a topology regression (edge drop, wrong
    direction, double-ended route) surfaces at import time.
    """

    from langgraph.graph import END

    from sentinel_prism.graph.graph import build_regulatory_pipeline_graph

    builder = build_regulatory_pipeline_graph()
    assert "route" in builder.nodes, "route node must be registered"
    assert "brief" in builder.nodes, "brief node must be registered"

    edge_pairs = {(src, dst) for src, dst in builder.edges}
    assert ("brief", "route") in edge_pairs, "brief must feed route"
    assert ("route", END) in edge_pairs, "route must be terminal"
    # No direct brief → END shortcut remains after Story 5.1.
    assert ("brief", END) not in edge_pairs, "brief → END must be replaced by brief → route → END"


@pytest.mark.asyncio
async def test_node_route_invokes_in_app_enqueue_and_merges_delivery_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Story 5.2 review P27 — ``node_route`` must call
    ``enqueue_critical_in_app_for_decisions`` once the routing decisions
    are resolved, forward the decisions verbatim, merge the returned
    ``delivery_events`` onto the node's output, and extend ``errors[]``
    with any enqueue error envelopes. This integration test closes the
    coverage gap that per-file unit tests for the service and the node
    individually leave open."""

    async def fake_topic(_s: object) -> list[SimpleNamespace]:
        return [_topic_row(ic="labeling")]

    async def fake_sev(_s: object) -> list[SimpleNamespace]:
        return []

    monkeypatch.setattr(
        "sentinel_prism.graph.nodes.route.routing_rules_repo.list_topic_rules_ordered",
        fake_topic,
    )
    monkeypatch.setattr(
        "sentinel_prism.graph.nodes.route.routing_rules_repo.list_severity_rules_ordered",
        fake_sev,
    )

    enqueue_calls: dict[str, object] = {}

    async def fake_enqueue(
        *, session_factory: object, run_id: str, decisions: list[dict[str, object]]
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        enqueue_calls["run_id"] = run_id
        enqueue_calls["decisions"] = decisions
        return (
            [
                {
                    "channel": "in_app",
                    "status": "recorded",
                    "run_id": run_id,
                    "rows_inserted": 1,
                }
            ],
            [
                {
                    "step": "in_app_notifications",
                    "message": "in_app_no_recipients",
                    "error_class": "NoRecipients",
                    "detail": "team_slug=other-team",
                }
            ],
        )

    monkeypatch.setattr(
        "sentinel_prism.graph.nodes.route.enqueue_critical_in_app_for_decisions",
        fake_enqueue,
    )

    run_id = uuid.uuid4()
    st = new_pipeline_state(run_id)
    st["classifications"] = [
        {
            "item_url": "https://ex/criti",
            "in_scope": True,
            "severity": "critical",
            "impact_categories": ["labeling"],
        }
    ]
    out = await node_route(st)

    # Enqueue invoked with the resolved decisions and the run_id as str.
    assert enqueue_calls.get("run_id") == str(run_id)
    assert isinstance(enqueue_calls.get("decisions"), list)
    assert len(enqueue_calls["decisions"]) == 1  # type: ignore[arg-type]

    # delivery_events surfaces into the node output for LangGraph merge.
    assert out.get("delivery_events") == [
        {
            "channel": "in_app",
            "status": "recorded",
            "run_id": str(run_id),
            "rows_inserted": 1,
        }
    ]

    # Enqueue errors extend ``errors[]`` alongside any routing errors.
    errs = out.get("errors") or []
    assert any(e.get("message") == "in_app_no_recipients" for e in errs)
