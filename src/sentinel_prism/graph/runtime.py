"""App-wide reference to the compiled regulatory graph for non-HTTP call sites (e.g. post-poll).

``FastAPI`` stores the same object on ``app.state.regulatory_graph``; workers and
:func:`sentinel_prism.services.connectors.poll.execute_poll` have no
``Request``, so they read the graph via :func:`get_regulatory_graph`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

_regulatory_graph: CompiledStateGraph | None = None


def set_regulatory_graph(g: CompiledStateGraph) -> None:
    global _regulatory_graph
    _regulatory_graph = g


def get_regulatory_graph() -> CompiledStateGraph | None:
    return _regulatory_graph
