"""Human review gate — LangGraph interrupt for HITL (Story 3.5).

On resume, LangGraph re-executes this node from the top; keep payload/logging
idempotent or coordinate with Epic 4 resume semantics.
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.types import interrupt

from sentinel_prism.graph.state import AgentState

logger = logging.getLogger(__name__)


async def node_human_review_gate(state: AgentState) -> dict[str, Any]:
    run_id = state.get("run_id") or ""
    ctx: dict[str, Any] = {"run_id": run_id}
    sid_raw = state.get("source_id")
    # Defense in depth: ``new_pipeline_state`` already coerces to ``str`` at the graph
    # boundary, but hand-built / checkpoint-restored state may present a ``uuid.UUID``.
    # Keep payloads and logs JSON-serializable and aligned with classification-row keys.
    sid = str(sid_raw) if sid_raw is not None else None
    if sid is not None:
        ctx["source_id"] = sid

    logger.info(
        "graph_human_review_gate",
        extra={
            "event": "graph_human_review_gate_interrupt",
            "ctx": ctx,
        },
    )

    payload: dict[str, Any] = {
        "run_id": run_id,
        "step": "human_review_gate",
    }
    if sid is not None:
        payload["source_id"] = sid

    interrupt(payload)
    return {}
