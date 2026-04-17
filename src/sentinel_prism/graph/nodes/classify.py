"""Classify node — ``normalized_updates`` → ``classifications`` (Story 3.4)."""

from __future__ import annotations

import logging
from typing import Any, Mapping

from sentinel_prism.graph.state import AgentState
from sentinel_prism.services.llm.classification import (
    build_classification_llm,
    classification_dict_for_llm_error,
    classification_dict_for_state,
)
from sentinel_prism.services.llm.classification_retry import is_transient_classification_error
from sentinel_prism.services.llm.rules import evaluate_classification_rules
from sentinel_prism.services.llm.settings import get_classification_llm_settings

logger = logging.getLogger(__name__)


async def node_classify(state: AgentState) -> dict[str, Any]:
    run_id = state.get("run_id")
    if not run_id or not str(run_id).strip():
        raise ValueError("AgentState.run_id is required but missing or empty")

    settings = get_classification_llm_settings()
    llm = build_classification_llm()
    # Prefer the LLM's **actual** model_id (e.g. real OpenAI model name) over the
    # settings default so logs and llm_trace reflect the call that was made.
    # Guard against non-string / blank values so downstream JSON consumers always
    # see a string in ``llm_trace.model_id``.
    _raw_model_id = getattr(llm, "model_id", None)
    model_id = (
        _raw_model_id
        if isinstance(_raw_model_id, str) and _raw_model_id.strip()
        else settings.model_id
    )
    prompt_version = settings.prompt_version

    norms: list[Any] = list(state.get("normalized_updates") or [])
    if not norms:
        logger.info(
            "graph_classify",
            extra={
                "event": "graph_classify_empty",
                "ctx": {"run_id": run_id},
            },
        )
        return {}

    classifications: list[dict[str, Any]] = []
    err_accum: list[dict[str, Any]] = []
    any_review = bool((state.get("flags") or {}).get("needs_human_review"))
    llm_success_count = 0
    llm_error_count = 0

    for item in norms:
        if not isinstance(item, Mapping):
            err_accum.append(
                {
                    "step": "classify",
                    "message": "normalized_update_not_a_mapping",
                    "error_class": "TypeError",
                    "detail": type(item).__name__,
                }
            )
            continue

        normalized: dict[str, Any] = dict(item)
        rule_outcome = evaluate_classification_rules(normalized)

        if not rule_outcome.in_scope:
            classifications.append(
                classification_dict_for_state(
                    normalized=normalized,
                    rule_outcome=rule_outcome,
                    llm=None,
                )
            )
            continue

        try:
            llm_out = await llm.classify(
                normalized,
                model_id=model_id,
                prompt_version=prompt_version,
            )
        except Exception as exc:
            if is_transient_classification_error(exc):
                logger.warning(
                    "graph_classify",
                    extra={
                        "event": "graph_classify_llm_transient",
                        "ctx": {
                            "run_id": run_id,
                            "step": "classify",
                            "model_id": model_id,
                            "prompt_version": prompt_version,
                            "error_class": type(exc).__name__,
                            "detail": str(exc),
                            "item_url": normalized.get("item_url"),
                        },
                    },
                )
                raise
            logger.warning(
                "graph_classify",
                extra={
                    "event": "graph_classify_llm_error",
                    "ctx": {
                        "run_id": run_id,
                        "model_id": model_id,
                        "prompt_version": prompt_version,
                        "error_class": type(exc).__name__,
                        "item_url": normalized.get("item_url"),
                    },
                },
            )
            err_accum.append(
                {
                    "step": "classify",
                    "message": "llm_error",
                    "error_class": type(exc).__name__,
                    "detail": str(exc),
                }
            )
            # Preserve AC #1 1:1 invariant: emit a placeholder row flagged for review
            # so downstream joins on ``source_id`` / ``item_url`` never lose an item.
            classifications.append(
                classification_dict_for_llm_error(
                    normalized=normalized,
                    rule_outcome=rule_outcome,
                )
            )
            any_review = True
            llm_error_count += 1
            continue

        logger.info(
            "graph_classify",
            extra={
                "event": "graph_classify_llm_done",
                "ctx": {
                    "run_id": run_id,
                    "model_id": model_id,
                    "prompt_version": prompt_version,
                    "item_url": normalized.get("item_url"),
                },
            },
        )

        row = classification_dict_for_state(
            normalized=normalized,
            rule_outcome=rule_outcome,
            llm=llm_out,
        )
        classifications.append(row)
        llm_success_count += 1
        if row.get("needs_human_review"):
            any_review = True

    out: dict[str, Any] = {
        "classifications": classifications,
    }
    # ``llm_trace.status`` semantics:
    #   - ``ok``         — at least one successful LLM call and no LLM errors.
    #   - ``partial``    — at least one successful LLM call AND at least one LLM error.
    #   - ``all_failed`` — every attempted LLM call errored (no successes).
    #   - ``no_attempt`` — no LLM call was attempted (e.g. only non-Mapping items or
    #                      all items out of scope); ``llm_trace`` is still emitted so
    #                      downstream consumers can rely on a stable schema.
    if llm_success_count > 0 and llm_error_count == 0:
        _status = "ok"
    elif llm_success_count > 0 and llm_error_count > 0:
        _status = "partial"
    elif llm_error_count > 0:
        _status = "all_failed"
    else:
        _status = "no_attempt"
    out["llm_trace"] = {
        "model_id": model_id,
        "prompt_version": prompt_version,
        "last_node": "classify",
        "status": _status,
    }
    if any_review:
        merged_flags = dict(state.get("flags") or {})
        merged_flags["needs_human_review"] = True
        out["flags"] = merged_flags
    if err_accum:
        out["errors"] = err_accum

    logger.info(
        "graph_classify",
        extra={
            "event": "graph_classify_done",
            "ctx": {
                "run_id": run_id,
                "classification_count": len(classifications),
            },
        },
    )
    return out
