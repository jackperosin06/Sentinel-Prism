"""LLM adapters — classification (Story 3.4)."""

from sentinel_prism.services.llm.classification import (
    ClassificationLLM,
    StructuredClassification,
    build_classification_llm,
    classification_dict_for_state,
)
from sentinel_prism.services.llm.rules import RuleOutcome, evaluate_classification_rules
from sentinel_prism.services.llm.settings import get_classification_llm_settings

__all__ = [
    "ClassificationLLM",
    "RuleOutcome",
    "StructuredClassification",
    "build_classification_llm",
    "classification_dict_for_state",
    "evaluate_classification_rules",
    "get_classification_llm_settings",
]
