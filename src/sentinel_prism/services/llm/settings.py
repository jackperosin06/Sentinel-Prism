"""Environment-backed defaults for classification LLM (Story 3.4)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DEFAULT_PROMPT_VERSION = "mvp-1"
DEFAULT_CLASSIFICATION_MAX_ATTEMPTS = 3
CLASSIFICATION_MAX_ATTEMPTS_LOWER_BOUND = 2
CLASSIFICATION_MAX_ATTEMPTS_UPPER_BOUND = 10


@dataclass(frozen=True)
class ClassificationLlmSettings:
    """Logical model id and prompt version for audit logs / ``llm_trace`` (not secrets)."""

    model_id: str
    prompt_version: str


def get_classification_llm_settings() -> ClassificationLlmSettings:
    """Read env vars fresh on each call.

    Intentionally not cached: environment overrides (including ``monkeypatch.setenv``
    in tests) must take effect without a process restart.
    """

    return ClassificationLlmSettings(
        model_id=os.getenv("SENTINEL_CLASSIFICATION_MODEL_ID", "stub").strip()
        or "stub",
        prompt_version=(
            os.getenv("SENTINEL_CLASSIFICATION_PROMPT_VERSION", DEFAULT_PROMPT_VERSION)
            .strip()
            or DEFAULT_PROMPT_VERSION
        ),
    )


@dataclass(frozen=True)
class ClassificationRetrySettings:
    """LangGraph :class:`~langgraph.types.RetryPolicy` knobs for ``classify`` (Story 3.6)."""

    max_attempts: int
    initial_interval: float = 0.5
    backoff_factor: float = 2.0
    max_interval: float = 128.0
    jitter: bool = True


def get_classification_retry_settings() -> ClassificationRetrySettings:
    """Env-backed retry policy for the classify node (read fresh each call).

    Invalid / out-of-range overrides are coerced to the documented bounds and a
    WARNING is logged so misconfigurations surface in operator logs rather than
    being silently absorbed.
    """

    env_name = "SENTINEL_CLASSIFICATION_MAX_ATTEMPTS"
    raw = os.getenv(env_name, str(DEFAULT_CLASSIFICATION_MAX_ATTEMPTS)).strip() or str(
        DEFAULT_CLASSIFICATION_MAX_ATTEMPTS
    )
    try:
        n = int(raw)
    except ValueError:
        logger.warning(
            "classification_retry_settings",
            extra={
                "event": "classification_max_attempts_parse_error",
                "ctx": {
                    "env": env_name,
                    "raw": raw,
                    "fallback": DEFAULT_CLASSIFICATION_MAX_ATTEMPTS,
                },
            },
        )
        n = DEFAULT_CLASSIFICATION_MAX_ATTEMPTS

    # Story AC: N >= 2; cap to avoid runaway invocations.
    clamped = max(
        CLASSIFICATION_MAX_ATTEMPTS_LOWER_BOUND,
        min(n, CLASSIFICATION_MAX_ATTEMPTS_UPPER_BOUND),
    )
    if clamped != n:
        logger.warning(
            "classification_retry_settings",
            extra={
                "event": "classification_max_attempts_clamped",
                "ctx": {
                    "env": env_name,
                    "requested": n,
                    "clamped": clamped,
                    "bounds": [
                        CLASSIFICATION_MAX_ATTEMPTS_LOWER_BOUND,
                        CLASSIFICATION_MAX_ATTEMPTS_UPPER_BOUND,
                    ],
                },
            },
        )

    return ClassificationRetrySettings(max_attempts=clamped)
