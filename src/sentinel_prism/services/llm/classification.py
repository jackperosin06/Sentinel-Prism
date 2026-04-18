"""Structured classification schema, LLM protocol, and factory (Story 3.4)."""

from __future__ import annotations

import logging
import os
from typing import Any, Literal, Protocol, runtime_checkable

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from sentinel_prism.services.llm.rules import RuleOutcome

logger = logging.getLogger(__name__)

Severity = Literal["critical", "high", "medium", "low"]
Urgency = Literal["immediate", "time_bound", "informational"]

# Canonical ``impact_categories`` vocabulary (FR13). Values in the LLM output that
# are not on this list are preserved as-is on the classification row; operators can
# re-bucket later, but downstream aggregations SHOULD prefer these tokens.
IMPACT_CATEGORIES_VOCAB: tuple[str, ...] = (
    "safety",
    "labeling",
    "manufacturing",
    "deadlines",
    "reporting",
    "licensing",
    "pricing",
    "other",
)

# ``needs_human_review`` policy (AC #1 — documented here and mirrored in story Dev Notes):
# an in-scope row is flagged for human review when the model's confidence falls below
# this threshold OR the severity is "critical". Threshold is strict ``<`` so exactly
# ``0.5`` is **not** flagged — revisit once real model scores are observed.
LOW_CONFIDENCE_THRESHOLD: float = 0.5

CLASSIFICATION_SYSTEM_PROMPT = (
    "You are a regulatory monitoring classifier. Given a normalized public-source "
    "update, assign severity, impact_categories, urgency, a short rationale, and a "
    "confidence score in [0,1]. Use only the provided fields; do not invent citations.\n"
    "\n"
    "`impact_categories` MUST be drawn from: "
    + ", ".join(IMPACT_CATEGORIES_VOCAB)
    + ". Use `other` when none fit."
)


class StructuredClassification(BaseModel):
    """LLM output shape for in-scope items (validated before merging into AgentState)."""

    severity: Severity
    impact_categories: list[str] = Field(default_factory=list)
    urgency: Urgency
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)


def format_classification_user_message(
    normalized: dict[str, Any],
    *,
    web_context: str | None = None,
) -> str:
    lines = [
        f"item_url: {normalized.get('item_url', '')}",
        f"jurisdiction: {normalized.get('jurisdiction', '')}",
        f"document_type: {normalized.get('document_type', '')}",
        f"title: {normalized.get('title', '')}",
        f"summary: {normalized.get('summary', '')}",
        f"body_snippet: {normalized.get('body_snippet', '')}",
    ]
    base = "\n".join(lines)
    extra = (web_context or "").strip()
    if extra:
        return f"{base}\n\n{extra}"
    return base


def classification_dict_for_state(
    *,
    normalized: dict[str, Any],
    rule_outcome: RuleOutcome,
    llm: StructuredClassification | None,
) -> dict[str, Any]:
    """Build one checkpoint-safe classification dict (AC #1).

    **Out-of-scope:** ``severity`` and ``urgency`` are JSON ``null`` (``None``); \
    ``impact_categories`` is ``[]``; ``rationale`` is ``rules_rejected``.
    """

    source_id = normalized.get("source_id")
    item_url = normalized.get("item_url")
    base: dict[str, Any] = {
        "source_id": "" if source_id is None else str(source_id),
        "item_url": "" if item_url is None else str(item_url),
        "in_scope": rule_outcome.in_scope,
        "rule_reasons": list(rule_outcome.reasons),
    }

    if not rule_outcome.in_scope:
        base.update(
            {
                "severity": None,
                "impact_categories": [],
                "urgency": None,
                "rationale": "rules_rejected",
                "confidence": 0.0,
                "needs_human_review": False,
            }
        )
        return base

    if llm is None:
        raise ValueError(
            "llm is required when rule_outcome.in_scope is True; caller must either "
            "pass the structured LLM output or use classification_dict_for_llm_error"
        )

    needs = llm.confidence < LOW_CONFIDENCE_THRESHOLD or llm.severity == "critical"
    base.update(
        {
            "severity": llm.severity,
            "impact_categories": list(llm.impact_categories),
            "urgency": llm.urgency,
            "rationale": llm.rationale,
            "confidence": llm.confidence,
            "needs_human_review": needs,
        }
    )
    return base


def classification_dict_for_llm_error(
    *,
    normalized: dict[str, Any],
    rule_outcome: RuleOutcome,
) -> dict[str, Any]:
    """Placeholder row emitted when the LLM call fails for an in-scope item (AC #1).

    Preserves the 1:1 ``normalized_updates`` → ``classifications`` invariant on the
    happy path without silently swallowing the failure. ``needs_human_review=True``
    forces downstream review.
    """

    source_id = normalized.get("source_id")
    item_url = normalized.get("item_url")
    return {
        "source_id": "" if source_id is None else str(source_id),
        "item_url": "" if item_url is None else str(item_url),
        "in_scope": rule_outcome.in_scope,
        "rule_reasons": list(rule_outcome.reasons),
        "severity": None,
        "impact_categories": [],
        "urgency": None,
        "rationale": "llm_error",
        "confidence": 0.0,
        "needs_human_review": True,
    }


@runtime_checkable
class ClassificationLLM(Protocol):
    model_id: str

    async def classify(
        self,
        normalized: dict[str, Any],
        *,
        model_id: str,
        prompt_version: str,
        web_context: str | None = None,
    ) -> StructuredClassification: ...


class StubClassificationLLM:
    """Deterministic offline classifier for CI and local dev."""

    def __init__(self, *, model_id: str = "stub") -> None:
        self.model_id = model_id

    async def classify(
        self,
        normalized: dict[str, Any],
        *,
        model_id: str,
        prompt_version: str,
        web_context: str | None = None,
    ) -> StructuredClassification:
        _ = normalized, model_id, prompt_version, web_context
        return StructuredClassification(
            severity="medium",
            impact_categories=["labeling"],
            urgency="informational",
            rationale="stub_llm",
            confidence=0.85,
        )


class LangChainStructuredClassificationLlm:
    """Optional OpenAI path when ``langchain-openai`` and ``OPENAI_API_KEY`` are available."""

    def __init__(
        self,
        chain: Any,
        *,
        model_id: str,
    ) -> None:
        self._chain = chain
        self.model_id = model_id

    async def classify(
        self,
        normalized: dict[str, Any],
        *,
        model_id: str,
        prompt_version: str,
        web_context: str | None = None,
    ) -> StructuredClassification:
        _ = model_id, prompt_version
        messages = [
            SystemMessage(content=CLASSIFICATION_SYSTEM_PROMPT),
            HumanMessage(
                content=format_classification_user_message(
                    normalized, web_context=web_context
                )
            ),
        ]
        out = await self._chain.ainvoke(messages)
        if isinstance(out, StructuredClassification):
            return out
        raise TypeError(
            f"structured output expected StructuredClassification, got {type(out)}"
        )


def build_classification_llm() -> ClassificationLLM:
    """Return stub by default; OpenAI + structured output when configured.

    Does **not** add a hard dependency on ``langchain-openai`` — import is lazy.

    The returned LLM exposes the **actual** ``model_id`` it will use, so structured
    logs and ``llm_trace`` reflect reality rather than a statically-configured env var.
    """

    # Allow operators to override the stub's reported model_id without changing code.
    stub_model_id = (
        os.getenv("SENTINEL_CLASSIFICATION_MODEL_ID", "stub").strip() or "stub"
    )

    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        return StubClassificationLLM(model_id=stub_model_id)

    try:
        from langchain_openai import ChatOpenAI
    except ImportError:
        logger.warning(
            "classification_llm_openai_unavailable",
            extra={
                "event": "classification_llm_openai_unavailable",
                "ctx": {"reason": "langchain_openai_not_installed"},
            },
        )
        return StubClassificationLLM(model_id=stub_model_id)

    model_name = os.getenv("SENTINEL_OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
    chat = ChatOpenAI(model=model_name, temperature=0)
    chain = chat.with_structured_output(StructuredClassification)
    return LangChainStructuredClassificationLlm(chain, model_id=model_name)
