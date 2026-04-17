"""LangGraph retry policies for pipeline nodes (Story 3.6)."""

from __future__ import annotations

from langgraph.types import RetryPolicy

from sentinel_prism.services.llm.classification_retry import is_transient_classification_error
from sentinel_prism.services.llm.settings import get_classification_retry_settings


def classify_node_retry_policy() -> RetryPolicy:
    """Retry transient LLM / HTTP failures on the ``classify`` node without new ``run_id``."""

    s = get_classification_retry_settings()
    return RetryPolicy(
        initial_interval=s.initial_interval,
        backoff_factor=s.backoff_factor,
        max_interval=s.max_interval,
        max_attempts=s.max_attempts,
        jitter=s.jitter,
        retry_on=is_transient_classification_error,
    )
