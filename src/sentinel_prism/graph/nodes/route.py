"""Route node — apply mock routing tables; populate ``routing_decisions`` (Story 5.1)."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from sentinel_prism.observability import obs_ctx
from sentinel_prism.db.models import PipelineAuditAction, RoutingRule
from sentinel_prism.db.repositories import audit_events as audit_events_repo
from sentinel_prism.db.repositories import routing_rules as routing_rules_repo
from sentinel_prism.db.session import get_session_factory
from sentinel_prism.graph.replay_context import in_replay_mode
from sentinel_prism.graph.state import AgentState
from sentinel_prism.services.notifications.scheduling import (
    process_routed_notification_deliveries,
)
from sentinel_prism.services.routing.resolve import RoutingRuleView, resolve_routing_decision

logger = logging.getLogger(__name__)
_NODE_ID = "route"


def _safe_error_detail(exc: BaseException, *, limit: int = 200) -> str:
    text = str(exc).replace("\r", " ").replace("\n", " ").strip()
    if len(text) > limit:
        text = text[:limit] + "…"
    return text


def _norm_item_url(raw: Any) -> str:
    """Canonicalize ``item_url`` for dedupe.

    Mirrors :func:`sentinel_prism.graph.nodes.brief._classifications_by_url`
    (strip), so resumes that compare against ``routing_decisions`` already
    in state do not miss a match on trailing whitespace drift introduced by
    upstream serializers.
    """

    return str(raw or "").strip()


def _rule_view(row: RoutingRule) -> RoutingRuleView:
    return RoutingRuleView(
        id=row.id,
        priority=row.priority,
        rule_type=row.rule_type.value,
        impact_category=row.impact_category,
        severity_value=row.severity_value,
        team_slug=row.team_slug,
        channel_slug=row.channel_slug,
    )


async def node_route(state: AgentState) -> dict[str, Any]:
    """Evaluate mock routing rules for each in-scope classification row.

    Return contract (state deltas merged by LangGraph reducers):

    * ``routing_decisions``: list appended via ``operator.add``. Always a
      list, even on early returns and on error branches, so downstream
      consumers see a consistent shape.
    * ``errors``: optional list with structured envelopes (``step``,
      ``message``, ``error_class``, ``detail``). Present only when rule
      loading or audit persistence fails.

    ``ROUTING_APPLIED`` is emitted at most once per run, and only when at
    least one new routing decision was produced (Story 5.1 AC #5 — the
    empty-classifications path does not pollute the audit trail). The
    at-most-once guarantee is enforced both at the application layer
    (``has_audit_event_for_run``) and at the DB layer (partial unique
    index ``uq_audit_events_routing_applied_run_id``).
    """

    run_id = state.get("run_id")
    if not run_id or not str(run_id).strip():
        raise ValueError("AgentState.run_id is required but missing or empty")
    run_id = str(run_id).strip()
    ctx: dict[str, Any] = obs_ctx(node_id=_NODE_ID, run_id=run_id)

    sid_raw = state.get("source_id")
    src_uuid: uuid.UUID | None = None
    if sid_raw is not None:
        try:
            src_uuid = uuid.UUID(str(sid_raw).strip())
        except (ValueError, TypeError, AttributeError):
            src_uuid = None
    if src_uuid is not None:
        ctx = {**ctx, "source_id": str(src_uuid)}

    existing_urls = {
        _norm_item_url(d.get("item_url"))
        for d in state.get("routing_decisions", [])
        if isinstance(d, dict) and d.get("item_url")
    }
    existing_urls.discard("")

    classifications = state.get("classifications")
    if not isinstance(classifications, list) or not classifications:
        logger.info(
            "graph_route",
            extra={
                "event": "graph_route_skipped",
                "ctx": {**ctx, "reason": "no_classifications"},
            },
        )
        # D3 (Story 5.1 review): do NOT emit ``ROUTING_APPLIED`` on the
        # empty-classifications path — the audit trail should only record
        # runs that actually produced routing work. Also guarantees the
        # return type is a state-delta dict (not the list returned by
        # ``_emit_routing_audit_if_needed``) so the LangGraph reducer
        # merges cleanly.
        return {"routing_decisions": []}

    try:
        factory = get_session_factory()
        async with factory() as session:
            topic_rows = await routing_rules_repo.list_topic_rules_ordered(session)
            severity_rows = await routing_rules_repo.list_severity_rules_ordered(session)
    except Exception as exc:  # noqa: BLE001 — see below
        # Broadened from ``SQLAlchemyError`` to catch non-DB runtime faults
        # that can originate in ``get_session_factory()`` (e.g.
        # ``RuntimeError`` when the factory is uninitialized), async
        # context-manager entry, or repository-side coercions. Without this
        # the node would crash the entire graph instead of degrading to a
        # structured ``errors`` envelope as documented above.
        logger.warning(
            "graph_route",
            extra={
                "event": "routing_rules_load_failed",
                "ctx": {**ctx, "error_class": type(exc).__name__},
            },
        )
        return {
            "routing_decisions": [],
            "errors": [
                {
                    "step": "route",
                    "message": "routing_rules_load_failed",
                    "error_class": type(exc).__name__,
                    "detail": _safe_error_detail(exc),
                }
            ],
        }

    topic_rules = [_rule_view(r) for r in topic_rows]
    severity_rules = [_rule_view(r) for r in severity_rows]

    decisions: list[dict[str, Any]] = []
    skipped = 0
    missing_url_count = 0
    for cls in classifications:
        if not isinstance(cls, dict):
            continue
        url = _norm_item_url(cls.get("item_url"))
        if not url:
            missing_url_count += 1
            logger.info(
                "graph_route",
                extra={
                    "event": "graph_route_missing_item_url",
                    "ctx": {**ctx, "severity": cls.get("severity")},
                },
            )
            continue
        if url in existing_urls:
            skipped += 1
            continue
        decisions.append(
            resolve_routing_decision(
                cls,
                topic_rules=topic_rules,
                severity_rules=severity_rules,
            )
        )
        existing_urls.add(url)

    errors: list[dict[str, Any]] = []
    if missing_url_count:
        errors.append(
            {
                "step": "route",
                "message": "classifications_missing_item_url",
                "error_class": "MissingItemUrl",
                "detail": f"count={missing_url_count}",
            }
        )
    delivery_events_merge: list[dict[str, Any]] = []
    if decisions:
        if not in_replay_mode():
            try:
                dev, enqueue_errors = await process_routed_notification_deliveries(
                    session_factory=get_session_factory(),
                    run_id=run_id,
                    decisions=decisions,
                )
                delivery_events_merge.extend(dev)
                errors.extend(enqueue_errors)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "graph_route",
                    extra={
                        "event": "routed_notifications_failed",
                        "ctx": {**ctx, "error_class": type(exc).__name__},
                    },
                )
                errors.append(
                    {
                        "step": "routed_notifications",
                        "message": "routed_notifications_unhandled",
                        "error_class": type(exc).__name__,
                        "detail": _safe_error_detail(exc),
                    }
                )
            audit_extra = await _emit_routing_audit_if_needed(
                run_id=run_id,
                source_id=src_uuid,
                ctx=ctx,
                new_count=len(decisions),
                skipped_duplicates=skipped,
            )
            errors.extend(audit_extra)
    out: dict[str, Any] = {"routing_decisions": decisions}
    if delivery_events_merge:
        out["delivery_events"] = delivery_events_merge
    if errors:
        out["errors"] = errors
    logger.info(
        "graph_route",
        extra={
            "event": "graph_route_done",
            "ctx": {
                **ctx,
                "decisions": len(decisions),
                "skipped_duplicates": skipped,
                "missing_item_url": missing_url_count,
            },
        },
    )
    return out


async def _emit_routing_audit_if_needed(
    *,
    run_id: str,
    source_id: uuid.UUID | None,
    ctx: dict[str, Any],
    new_count: int,
    skipped_duplicates: int,
) -> list[dict[str, Any]]:
    """Emit ``ROUTING_APPLIED`` at most once per run (idempotent replays).

    Two layers of protection:

    1. Application: ``has_audit_event_for_run`` avoids the write when a row
       already exists, which is the common no-op-on-replay path.
    2. Database: the partial unique index
       ``uq_audit_events_routing_applied_run_id`` guarantees at most one
       row even under a TOCTOU race between concurrent ``node_route``
       invocations for the same ``run_id``. An ``IntegrityError`` from the
       race-losing insert is treated as idempotent success (not surfaced
       as an ``errors`` entry) so the pipeline does not observe a benign
       duplicate-write as a failure.
    """

    try:
        factory = get_session_factory()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "graph_route",
            extra={
                "event": "routing_audit_session_unavailable",
                "ctx": {**ctx, "error_class": type(exc).__name__},
            },
        )
        return [
            {
                "step": "audit_write",
                "message": "routing_audit_session_unavailable",
                "error_class": type(exc).__name__,
                "detail": _safe_error_detail(exc),
            }
        ]

    try:
        async with factory() as session:
            exists = await audit_events_repo.has_audit_event_for_run(
                session,
                run_id=run_id,
                action=PipelineAuditAction.ROUTING_APPLIED,
            )
            if exists:
                return []
            await audit_events_repo.append_audit_event(
                session,
                run_id=run_id,
                action=PipelineAuditAction.ROUTING_APPLIED,
                source_id=source_id,
                metadata={
                    "items_processed": new_count,
                    "skipped_duplicate_urls": skipped_duplicates,
                },
            )
            await session.commit()
    except IntegrityError:
        # The DB-side partial unique index rejected a concurrent second
        # insert — a peer ``node_route`` on the same ``run_id`` already
        # recorded ``ROUTING_APPLIED``. This is the exact condition the
        # audit is meant to be idempotent under, so treat it as success.
        logger.info(
            "graph_route",
            extra={
                "event": "routing_audit_already_recorded_by_peer",
                "ctx": ctx,
            },
        )
        return []
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "graph_route",
            extra={
                "event": "routing_audit_write_failed",
                "ctx": {**ctx, "error_class": type(exc).__name__},
            },
        )
        return [
            {
                "step": "audit_write",
                "message": "routing_audit_persist_failed",
                "error_class": type(exc).__name__,
                "detail": _safe_error_detail(exc),
            }
        ]
    return []
