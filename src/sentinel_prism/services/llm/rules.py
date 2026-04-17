"""Deterministic in-scope rules before LLM classify (Story 3.4 — FR11).

**Jurisdiction policy (MVP, permissive):** ``None`` / empty / whitespace-only
jurisdiction strings are treated as **in-scope**. Rationale: ingestion sources
do not always tag jurisdiction reliably, and the allowlist is intended to
**reject known-but-disallowed** regions — not to gate items with *unknown*
provenance. Such items still reach the LLM, which will set low confidence or
flag ``needs_human_review`` when context is insufficient. This keeps the
deterministic gate conservative about what it rejects; tighten later via
env-driven allowlist configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, FrozenSet

# MVP: explicit allowlist; extend via env-driven config in a later story if needed.
JURISDICTION_ALLOWLIST: FrozenSet[str] = frozenset(
    {"US", "EU", "UK", "GLOBAL", "CA", "JP", "CH"}
)

DOCUMENT_TYPES_EXCLUDED: FrozenSet[str] = frozenset({"spam", "noise"})


@dataclass(frozen=True)
class RuleOutcome:
    """Result of rule evaluation on a normalized update dict."""

    in_scope: bool
    reasons: tuple[str, ...]


def _jurisdiction_allowed(raw: str | None) -> bool:
    if raw is None:
        return True
    j = raw.strip().upper()
    if not j:
        return True
    if j in JURISDICTION_ALLOWLIST:
        return True
    if "-" in j:
        prefix = j.split("-", 1)[0]
        if prefix in JURISDICTION_ALLOWLIST:
            return True
    return False


def evaluate_classification_rules(update: dict[str, Any]) -> RuleOutcome:
    """Return whether the update is eligible for LLM classification.

    Out-of-scope updates skip the LLM entirely (AC #2).
    """

    reasons: list[str] = []
    jurisdiction = update.get("jurisdiction")
    if not _jurisdiction_allowed(
        jurisdiction if isinstance(jurisdiction, str) else None
    ):
        reasons.append("jurisdiction_not_in_allowlist")
        return RuleOutcome(False, tuple(reasons))

    doc_raw = update.get("document_type")
    doc = (
        doc_raw.strip().lower()
        if isinstance(doc_raw, str) and doc_raw.strip()
        else ""
    )
    if doc in DOCUMENT_TYPES_EXCLUDED:
        reasons.append("document_type_excluded")
        return RuleOutcome(False, tuple(reasons))

    title = update.get("title")
    summary = update.get("summary")
    body = update.get("body_snippet")
    has_text = bool(
        (isinstance(title, str) and title.strip())
        or (isinstance(summary, str) and summary.strip())
        or (isinstance(body, str) and body.strip())
    )
    if not has_text:
        reasons.append("insufficient_content")
        return RuleOutcome(False, tuple(reasons))

    return RuleOutcome(True, ())
