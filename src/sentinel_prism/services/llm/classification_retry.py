"""Transient error detection for classification LLM retries (Story 3.6)."""

from __future__ import annotations

import logging

from langgraph.types import default_retry_on

logger = logging.getLogger(__name__)


def _load_openai_transient_classes() -> tuple[type, ...]:
    """Import openai SDK transient error classes per-symbol.

    Historically the openai SDK has renamed/moved these classes across majors
    (e.g. v0 → v1). A single import would either fully succeed or raise
    ``ImportError`` — masking partial breakage where some names are missing.
    Import each symbol independently, log missing ones at WARNING, and return
    whichever are present. Returns ``()`` when ``openai`` is not installed.
    """

    try:
        import openai  # noqa: F401
    except ImportError:
        return ()

    # Each entry: the attribute name on ``openai`` to look up. Covers connection,
    # timeout, rate-limit, and 5xx/internal classes per spec line 16
    # ("retryable HTTP/5xx").
    wanted = (
        "APIConnectionError",
        "APITimeoutError",
        "RateLimitError",
        "APIStatusError",
        "InternalServerError",
    )
    found: list[type] = []
    missing: list[str] = []
    import openai as _openai

    for name in wanted:
        cls = getattr(_openai, name, None)
        if isinstance(cls, type):
            found.append(cls)
        else:
            missing.append(name)

    if missing:
        logger.warning(
            "classification_retry",
            extra={
                "event": "openai_retry_names_missing",
                "ctx": {
                    "missing": missing,
                    "found": [c.__name__ for c in found],
                },
            },
        )

    return tuple(found)


# Loaded once at import time; re-imports during exception classification would
# add noticeable overhead on every error path.
_OPENAI_TRANSIENT_CLASSES: tuple[type, ...] = _load_openai_transient_classes()


def is_transient_classification_error(exc: BaseException) -> bool:
    """Return True when LangGraph (or operators) should retry the classify node.

    Aligns with :func:`langgraph.types.default_retry_on` for HTTP/network semantics,
    but treats :class:`TimeoutError` (a subclass of :class:`OSError`) as **retryable**,
    matching typical LLM client timeout behavior.

    Optional ``openai`` SDK errors (connection, timeout, rate-limit, 5xx/internal
    server) are included when that package is installed.
    """

    if not isinstance(exc, Exception):
        return False

    # ``default_retry_on`` classifies ``TimeoutError`` as non-retryable only because
    # it subclasses ``OSError``; for LLM calls we want the opposite.
    if isinstance(exc, TimeoutError):
        return True

    if default_retry_on(exc):
        return True

    if _OPENAI_TRANSIENT_CLASSES and isinstance(exc, _OPENAI_TRANSIENT_CLASSES):
        return True

    return False


__all__ = ["is_transient_classification_error"]
