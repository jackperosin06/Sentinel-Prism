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

    import sentinel_prism.db.repositories.audit_events  # noqa: F401
    import sentinel_prism.graph.nodes.classify  # noqa: F401
    import sentinel_prism.graph.nodes.human_review_gate  # noqa: F401
    import sentinel_prism.graph.nodes.scout  # noqa: F401
    import sentinel_prism.graph.nodes.route  # noqa: F401
    import sentinel_prism.graph.nodes.normalize  # noqa: F401
    import sentinel_prism.api.routes.delivery_attempts  # noqa: F401
    import sentinel_prism.api.routes.notifications  # noqa: F401
    import sentinel_prism.api.routes.runs  # noqa: F401
    import sentinel_prism.graph.pipeline_audit  # noqa: F401
    import sentinel_prism.graph.pipeline_review  # noqa: F401
    import sentinel_prism.graph.retry  # noqa: F401
    import sentinel_prism.graph.routing  # noqa: F401
    import sentinel_prism.graph.tools.factory  # noqa: F401
    import sentinel_prism.services.notifications.adapters.slack  # noqa: F401
    import sentinel_prism.services.notifications.adapters.smtp  # noqa: F401
    import sentinel_prism.services.notifications._attempts  # noqa: F401
    import sentinel_prism.services.notifications.external  # noqa: F401
    import sentinel_prism.services.notifications.digest_flush  # noqa: F401
    import sentinel_prism.services.notifications.external_settings  # noqa: F401
    import sentinel_prism.services.notifications.in_app  # noqa: F401
    import sentinel_prism.services.notifications.notification_policy  # noqa: F401
    import sentinel_prism.services.notifications.scheduling  # noqa: F401
    import sentinel_prism.workers.digest_scheduler  # noqa: F401
    import sentinel_prism.services.search.settings  # noqa: F401
    import sentinel_prism.services.llm.classification  # noqa: F401
    import sentinel_prism.services.llm.classification_retry  # noqa: F401
    import sentinel_prism.services.llm.rules  # noqa: F401
    import sentinel_prism.services.llm.settings  # noqa: F401

    print(
        "imports ok: langgraph, langgraph.graph, langgraph.checkpoint.memory, "
        "langchain_core, graph.nodes"
    )


if __name__ == "__main__":
    main()
