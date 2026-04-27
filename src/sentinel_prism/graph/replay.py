"""Replay helpers for operator debugging (Story 8.2)."""

from __future__ import annotations

from dataclasses import dataclass

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from sentinel_prism.graph.nodes.brief import node_brief
from sentinel_prism.graph.nodes.human_review_gate import node_human_review_gate
from sentinel_prism.graph.nodes.route import node_route
from sentinel_prism.graph.state import AgentState


@dataclass(frozen=True)
class ReplayPlan:
    graph: CompiledStateGraph
    replayed_nodes: list[str]


def compile_replay_tail_graph(*, from_node: str) -> ReplayPlan:
    """Tail graph replay plan (Story 8.2).

    Offline/deterministic replay: do not invoke the `classify` node (which may
    call LLM + web search). Replay is executed under
    :func:`sentinel_prism.graph.replay_context.replay_mode` so side effects are
    suppressed.

    Supported segments:
    - `classify` (logical start): replay begins from `human_review_gate` using
      checkpointed `classifications`.
    - `human_review_gate`: begins at `human_review_gate`.
    - `brief`: begins at `brief`.
    """

    requested = from_node.strip()
    start = requested
    if requested == "classify":
        start = "human_review_gate"
    if start not in {"human_review_gate", "brief"}:
        raise ValueError(f"unsupported from_node for replay plan: {from_node!r}")

    builder: StateGraph = StateGraph(AgentState)
    builder.add_node("human_review_gate", node_human_review_gate)
    builder.add_node("brief", node_brief)
    builder.add_node("route", node_route)

    builder.add_edge(START, start)
    if start == "human_review_gate":
        builder.add_edge("human_review_gate", "brief")
    builder.add_edge("brief", "route")
    builder.add_edge("route", END)
    graph = builder.compile()

    if requested == "brief":
        replayed_nodes = ["brief", "route"]
    elif requested == "human_review_gate":
        replayed_nodes = ["human_review_gate", "brief", "route"]
    else:
        replayed_nodes = ["classify", "human_review_gate", "brief", "route"]
    return ReplayPlan(graph=graph, replayed_nodes=replayed_nodes)

