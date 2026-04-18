"""Regulatory update pipeline: StateGraph build and compile (Epic 3)."""

from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from sentinel_prism.graph.checkpoints import dev_memory_checkpointer
from sentinel_prism.graph.nodes.brief import node_brief
from sentinel_prism.graph.nodes.classify import node_classify
from sentinel_prism.graph.nodes.human_review_gate import node_human_review_gate
from sentinel_prism.graph.nodes.normalize import node_normalize
from sentinel_prism.graph.nodes.scout import node_scout
from sentinel_prism.graph.retry import classify_node_retry_policy
from sentinel_prism.graph.routing import (
    CLASSIFY_NEXT_CONTINUE,
    CLASSIFY_NEXT_HUMAN_REVIEW,
    route_after_classify,
)
from sentinel_prism.graph.state import AgentState


def build_regulatory_pipeline_graph() -> StateGraph:
    """Construct the regulatory pipeline graph (nodes and edges only, not compiled).

    Persistence is attached at compile time — see
    :func:`compile_regulatory_pipeline_graph`.
    """

    builder: StateGraph = StateGraph(AgentState)
    builder.add_node("scout", node_scout)
    builder.add_node("normalize", node_normalize)
    builder.add_node(
        "classify",
        node_classify,
        retry_policy=classify_node_retry_policy(),
    )
    builder.add_node("human_review_gate", node_human_review_gate)
    builder.add_node("brief", node_brief)
    builder.add_edge(START, "scout")
    builder.add_edge("scout", "normalize")
    builder.add_edge("normalize", "classify")
    builder.add_conditional_edges(
        "classify",
        route_after_classify,
        {
            CLASSIFY_NEXT_HUMAN_REVIEW: "human_review_gate",
            CLASSIFY_NEXT_CONTINUE: "brief",
        },
    )
    builder.add_edge("human_review_gate", "brief")
    builder.add_edge("brief", END)
    return builder


def compile_regulatory_pipeline_graph(
    *,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    """Compile the pipeline with a checkpointer (defaults to :func:`dev_memory_checkpointer`)."""

    cp = checkpointer if checkpointer is not None else dev_memory_checkpointer()
    return build_regulatory_pipeline_graph().compile(checkpointer=cp)
