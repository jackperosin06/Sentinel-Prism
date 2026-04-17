"""Minimal check that LangGraph and LangChain Core import in this environment."""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    src = Path(__file__).resolve().parent / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    import langchain_core  # noqa: F401
    import langgraph  # noqa: F401
    import langgraph.checkpoint.memory  # noqa: F401
    import langgraph.graph  # noqa: F401

    import sentinel_prism.graph.nodes.scout  # noqa: F401
    import sentinel_prism.graph.nodes.normalize  # noqa: F401

    print(
        "imports ok: langgraph, langgraph.graph, langgraph.checkpoint.memory, "
        "langchain_core, graph.nodes"
    )


if __name__ == "__main__":
    main()
