"""Conditional routing after ``classify`` (Story 3.5).

``route_after_classify`` reads **only** ``flags["needs_human_review"]``, which
``node_classify`` maintains with OR semantics across classification rows. Do not
re-derive routing by scanning ``classifications`` here — that would duplicate
policy and risk drifting from classify.

**Source of truth:** aggregate flag on ``AgentState.flags`` (Story 3.4).
"""

from __future__ import annotations

from sentinel_prism.graph.state import AgentState

# Return values must match keys in ``add_conditional_edges`` path map (``graph.py``).
CLASSIFY_NEXT_HUMAN_REVIEW = "human_review_gate"
CLASSIFY_NEXT_CONTINUE = "end"


def route_after_classify(state: AgentState) -> str:
    flags = state.get("flags") or {}
    if flags.get("needs_human_review"):
        return CLASSIFY_NEXT_HUMAN_REVIEW
    return CLASSIFY_NEXT_CONTINUE
