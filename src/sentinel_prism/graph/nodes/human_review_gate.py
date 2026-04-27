"""Human review gate — LangGraph interrupt for HITL (Story 3.5,4.2).

On resume, LangGraph re-executes this node from the top; projection upsert must
stay idempotent (``review_queue.upsert_pending`` preserves ``queued_at`` on
conflict — Story 4.2).

The first ``interrupt()`` call raises :class:`~langgraph.errors.GraphInterrupt`.
After the client resumes with :class:`~langgraph.types.Command`, the same
``interrupt()`` invocation returns the resume payload and execution continues.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from langgraph.types import Overwrite, interrupt

from sentinel_prism.graph.pipeline_review import (
    classification_summaries_for_queue,
    record_review_queue_projection,
)
from sentinel_prism.graph.replay_context import in_replay_mode
from sentinel_prism.graph.state import AgentState
from sentinel_prism.observability import obs_ctx

logger = logging.getLogger(__name__)
_NODE_ID = "human_review_gate"


def _classification_copy_list(state: AgentState) -> list[dict[str, Any]]:
    raw = state.get("classifications")
    if not isinstance(raw, list):
        return []
    return [dict(x) for x in raw if isinstance(x, dict)]


def _rejected_row(row: dict[str, Any]) -> dict[str, Any]:
    """Dismiss in-scope items from the review queue (Story 4.2 — reject path)."""

    return {
        **row,
        "in_scope": False,
        "severity": None,
        "impact_categories": [],
        "urgency": None,
        "rationale": "analyst_rejected",
        "confidence": 0.0,
        "needs_human_review": False,
        "rule_reasons": ["analyst_rejected"],
    }


def _apply_override_patches(
    rows: list[dict[str, Any]],
    patches: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Apply override patches to ``rows``; return (new rows, unmatched item_urls).

    Matching rules (resolved in Story 4.2 code review — decision-needed D1/D6):

    * If ``item_url`` is set, patch rows whose ``item_url`` equals it
      (strict equality after ``.strip()``). UI is expected to round-trip the
      classification row's ``item_url`` verbatim. No-op + surface as
      ``unmatched`` if nothing matches.
    * If ``item_url`` is absent, patch only ``in_scope is True`` rows. We
      deliberately do **not** fall back to "every row" — that would silently
      rewrite out-of-scope rows whose ``classification_dict_for_state``
      contract requires ``severity=None`` / ``urgency=None`` / etc.
    """

    out = [dict(r) for r in rows]
    unmatched: list[str] = []
    for p in patches:
        if not isinstance(p, dict):
            continue
        url = p.get("item_url")
        indices: list[int] = []
        if url:
            u = str(url).strip()
            for i, d in enumerate(out):
                if str(d.get("item_url") or "").strip() == u:
                    indices.append(i)
            if not indices:
                unmatched.append(u)
                continue
        else:
            for i, d in enumerate(out):
                if d.get("in_scope") is True:
                    indices.append(i)
            if not indices:
                # No in-scope rows to override. Drop the patch rather than
                # cascading to every row (which would corrupt out-of-scope
                # row shape). Report as unmatched so the caller can audit.
                unmatched.append("<no-in-scope-rows>")
                continue
        for i in indices:
            row = dict(out[i])
            if p.get("severity") is not None:
                row["severity"] = p["severity"]
            if p.get("confidence") is not None:
                row["confidence"] = p["confidence"]
            if p.get("rationale") is not None:
                row["rationale"] = p["rationale"]
            if p.get("impact_categories") is not None:
                cats = p["impact_categories"]
                row["impact_categories"] = list(cats) if isinstance(cats, list) else []
            if p.get("urgency") is not None:
                row["urgency"] = p["urgency"]
            row["needs_human_review"] = False
            out[i] = row
    return out, unmatched


def apply_human_review_resume(
    state: AgentState,
    resume: dict[str, Any],
) -> dict[str, Any]:
    """Merge analyst decision into checkpoint state (after ``interrupt`` returns).

    On any error path we always clear ``flags["needs_human_review"]=False`` so
    the run drops out of the human-review branch instead of re-interrupting
    forever — once the resume payload has been consumed the pending interrupt
    is gone and the route returns 404 on subsequent ``POST /resume`` calls.
    """

    flags: dict[str, bool] = dict(state.get("flags") or {})

    decision = resume.get("decision")
    if decision not in ("approve", "reject", "override"):
        flags["needs_human_review"] = False
        return {
            "errors": [
                {
                    "step": "human_review_gate",
                    "message": "invalid_resume_decision",
                    "error_class": "ValueError",
                    "detail": f"got {decision!r}",
                }
            ],
            "flags": flags,
        }

    # Defensive: if upstream corrupted ``classifications`` (not a list or
    # missing) we still clear the flag but surface an error row so operators
    # can triage instead of the run silently reporting "reviewed" with zero
    # row updates.
    raw_cls = state.get("classifications")
    if raw_cls is not None and not isinstance(raw_cls, list):
        flags["needs_human_review"] = False
        return {
            "errors": [
                {
                    "step": "human_review_gate",
                    "message": "classifications_not_list",
                    "error_class": "TypeError",
                    "detail": type(raw_cls).__name__,
                }
            ],
            "flags": flags,
        }

    cls_list = _classification_copy_list(state)

    if decision == "approve":
        for d in cls_list:
            d["needs_human_review"] = False
        flags["needs_human_review"] = False
        return {
            "classifications": Overwrite(value=cls_list),
            "flags": flags,
        }

    if decision == "reject":
        for i, d in enumerate(cls_list):
            if d.get("in_scope") is True:
                cls_list[i] = _rejected_row(d)
        flags["needs_human_review"] = False
        return {
            "classifications": Overwrite(value=cls_list),
            "flags": flags,
        }

    # override
    raw_patches = resume.get("overrides")
    patches = raw_patches if isinstance(raw_patches, list) else []
    cls_list, unmatched = _apply_override_patches(cls_list, patches)
    flags["needs_human_review"] = False
    result: dict[str, Any] = {
        "classifications": Overwrite(value=cls_list),
        "flags": flags,
    }
    if unmatched:
        # Record an error row per unmatched patch (append-reducer) so the
        # analyst / operator can see the override silently dropped.
        result["errors"] = [
            {
                "step": "human_review_gate",
                "message": "override_unmatched_item_url",
                "error_class": "ValueError",
                "detail": u,
            }
            for u in unmatched
        ]
    return result


async def node_human_review_gate(state: AgentState) -> dict[str, Any]:
    if in_replay_mode():
        # Replay must be non-destructive and must not interrupt; clear the flag
        # so the replay run can continue through the tail of the pipeline.
        flags = dict(state.get("flags") or {})
        flags["needs_human_review"] = False
        return {"flags": flags}

    run_id = state.get("run_id") or ""
    ctx: dict[str, Any] = obs_ctx(node_id=_NODE_ID, run_id=str(run_id).strip())
    sid_raw = state.get("source_id")
    # Defense in depth: ``new_pipeline_state`` already coerces ``source_id`` to
    # ``str``, but checkpoint restoration / direct graph invocations may inject
    # a raw ``uuid.UUID``. Normalize here so downstream logging + projection
    # code can assume a plain string.
    sid = str(sid_raw) if sid_raw is not None else None
    if sid is not None:
        ctx = {**ctx, "source_id": sid}

    logger.info(
        "graph_human_review_gate",
        extra={
            "event": "graph_human_review_gate_interrupt",
            "ctx": ctx,
        },
    )

    raw_cls = state.get("classifications")
    cls_dicts: list[dict[str, Any]] = (
        [x for x in raw_cls if isinstance(x, dict)]
        if isinstance(raw_cls, list)
        else []
    )
    summaries = classification_summaries_for_queue(cls_dicts)
    src_uuid: uuid.UUID | None = None
    if sid is not None:
        try:
            src_uuid = uuid.UUID(str(sid).strip())
        except (ValueError, TypeError, AttributeError):
            src_uuid = None
    # Capture the interrupt timestamp BEFORE ``interrupt()`` fires — LangGraph
    # re-executes this node top-to-bottom on resume, so calling ``datetime.now``
    # after the resume would bump ``queued_at`` on every round-trip. The
    # upstream ``upsert_pending`` conflict path deliberately preserves
    # ``queued_at`` to back-stop this.
    interrupted_at = datetime.now(tz=timezone.utc)
    await record_review_queue_projection(
        run_id=str(run_id),
        source_id=src_uuid,
        items_summary=summaries,
        queued_at=interrupted_at,
    )

    payload: dict[str, Any] = {
        "run_id": run_id,
        "step": "human_review_gate",
    }
    if sid is not None:
        payload["source_id"] = sid

    resume = interrupt(payload)
    if not isinstance(resume, dict):
        logger.warning(
            "graph_human_review_gate",
            extra={
                "event": "graph_human_review_gate_bad_resume_type",
                "ctx": {**ctx, "resume_type": type(resume).__name__},
            },
        )
        # Also clear ``flags["needs_human_review"]`` — the interrupt has been
        # consumed by this resume, so leaving the flag set would make the run
        # look permanently pending (route returns 404 on next /resume because
        # there is no interrupt, queue row stays up) without a recovery path.
        flags = dict(state.get("flags") or {})
        flags["needs_human_review"] = False
        return {
            "errors": [
                {
                    "step": "human_review_gate",
                    "message": "resume_payload_type",
                    "error_class": "TypeError",
                    "detail": type(resume).__name__,
                }
            ],
            "flags": flags,
        }

    updates = apply_human_review_resume(state, resume)
    logger.info(
        "graph_human_review_gate",
        extra={
            "event": "graph_human_review_gate_resumed",
            "ctx": {
                **ctx,
                "decision": resume.get("decision"),
            },
        },
    )
    return updates
