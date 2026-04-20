"""Classify node — ``normalized_updates`` → ``classifications`` (Story 3.4)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import OrderedDict
from typing import Any, Mapping

from sentinel_prism.db.models import PipelineAuditAction
from sentinel_prism.graph.tools.context_format import format_web_context_for_llm
from sentinel_prism.graph.tools.factory import create_web_search_tool
from sentinel_prism.graph.tools.query_builder import build_public_web_search_query
from sentinel_prism.graph.tools.types import SearchToolProtocol
from sentinel_prism.graph.pipeline_audit import record_pipeline_audit_event
from sentinel_prism.graph.state import AgentState
from sentinel_prism.services.llm.classification import (
    build_classification_llm,
    classification_dict_for_llm_error,
    classification_dict_for_state,
)
from sentinel_prism.services.llm.classification_retry import is_transient_classification_error
from sentinel_prism.services.llm.rules import evaluate_classification_rules
from sentinel_prism.services.llm.settings import get_classification_llm_settings
from sentinel_prism.services.search.settings import get_web_search_settings

logger = logging.getLogger(__name__)


# Per-run web-search memoization (Story 3.7 review): LangGraph's ``RetryPolicy``
# re-executes the whole ``node_classify`` body on a transient LLM failure
# (Story 3.6), which would otherwise re-issue Tavily searches for every item in
# the batch. This process-local LRU caches ``format_web_context_for_llm``
# output keyed by ``(run_id, source_id, item_url)`` so retry passes within the
# same run reuse the first pass's results. Bounded to avoid unbounded memory
# growth across long-lived worker processes.
_WEB_SEARCH_CACHE_MAX_ENTRIES = 1000
_web_search_cache: "OrderedDict[tuple[str, str, str], str | None]" = OrderedDict()
_SEVERITY_BUCKETS = frozenset({"critical", "high", "medium", "low", "none", "other"})


def _web_search_cache_get(key: tuple[str, str, str]) -> tuple[bool, str | None]:
    """Return ``(hit, value)``. ``hit=False`` means missing."""

    if key in _web_search_cache:
        value = _web_search_cache.pop(key)
        _web_search_cache[key] = value  # LRU: move to newest
        return True, value
    return False, None


def _web_search_cache_put(key: tuple[str, str, str], value: str | None) -> None:
    _web_search_cache[key] = value
    while len(_web_search_cache) > _WEB_SEARCH_CACHE_MAX_ENTRIES:
        _web_search_cache.popitem(last=False)


def _severity_histogram_from_classifications(
    classifications: list[dict[str, Any]],
) -> dict[str, int]:
    """Bounded count-only histogram for audit metadata (Story 6.1 — FR30, NFR12)."""

    counts: dict[str, int] = {}
    for row in classifications:
        if not isinstance(row, dict):
            continue
        raw = row.get("severity")
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            key = "none"
        else:
            key = str(raw).strip().lower()
        if key not in _SEVERITY_BUCKETS:
            key = "other"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _safe_error_detail(exc: BaseException, *, limit: int = 200) -> str:
    """Render an exception for logs/state without leaking outbound query text.

    HTTP client exceptions routinely embed the failing URL (which here contains
    the concatenated title / summary / body_snippet from NFR12 allow-listed
    fields) in ``str(exc)``. Truncate aggressively and strip newlines so log
    lines stay single-line and state payloads stay small.
    """

    text = str(exc).replace("\r", " ").replace("\n", " ").strip()
    if len(text) > limit:
        text = text[:limit] + "…"
    return text


async def node_classify(
    state: AgentState,
    *,
    _web_search_tool: SearchToolProtocol | None = None,
) -> dict[str, Any]:
    run_id = state.get("run_id")
    if not run_id or not str(run_id).strip():
        raise ValueError("AgentState.run_id is required but missing or empty")

    settings = get_classification_llm_settings()
    ws_settings = get_web_search_settings()
    use_web_enrichment = ws_settings.enabled or (_web_search_tool is not None)
    search_tool = (
        _web_search_tool
        if _web_search_tool is not None
        else create_web_search_tool(settings=ws_settings)
    )

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

    # Parse source_id once so both the empty-input early-return and the main
    # completion path can attribute their audit rows to the same source.
    _src = state.get("source_id")
    source_uuid: uuid.UUID | None = None
    if _src:
        try:
            source_uuid = uuid.UUID(str(_src).strip())
        except ValueError:
            source_uuid = None

    norms: list[Any] = list(state.get("normalized_updates") or [])
    if not norms:
        logger.info(
            "graph_classify",
            extra={
                "event": "graph_classify_empty",
                "ctx": {"run_id": run_id},
            },
        )
        # AC #3: empty-but-successful completion still emits an audit row.
        # ``llm_trace.status = no_attempt`` mirrors the status the non-empty
        # path would set when no LLM calls are made.
        audit_errs = await record_pipeline_audit_event(
            run_id=str(run_id),
            action=PipelineAuditAction.PIPELINE_CLASSIFY_COMPLETED,
            source_id=source_uuid,
            metadata={
                "classification_count": 0,
                "severity_histogram": {},
                "llm_trace": {
                    "status": "no_attempt",
                    "model_id": model_id,
                    "prompt_version": prompt_version,
                },
            },
        )
        return {"errors": audit_errs} if audit_errs else {}

    classifications: list[dict[str, Any]] = []
    err_accum: list[dict[str, Any]] = []
    any_review = bool((state.get("flags") or {}).get("needs_human_review"))
    llm_success_count = 0
    llm_error_count = 0
    web_search_attempts = 0
    web_search_errors = 0
    web_search_cache_hits = 0
    run_id_str = str(run_id)

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

        web_context: str | None = None
        if use_web_enrichment:
            q = build_public_web_search_query(normalized)
            if q:
                # Memoize per-run by ``(run_id, source_id, item_url)`` so a
                # LangGraph full-node retry after a transient LLM error does
                # not re-issue Tavily searches for every item in the batch
                # (Story 3.6 retry amplification). Items without a usable
                # ``source_id`` or ``item_url`` bypass the cache (first pass
                # still runs; retries simply re-query those).
                _source_id = str(normalized.get("source_id") or "").strip()
                _item_url = str(normalized.get("item_url") or "").strip()
                cache_key: tuple[str, str, str] | None = (
                    (run_id_str, _source_id, _item_url)
                    if _source_id and _item_url
                    else None
                )
                cache_hit = False
                if cache_key is not None:
                    cache_hit, cached_ctx = _web_search_cache_get(cache_key)
                    if cache_hit:
                        web_context = cached_ctx
                        web_search_cache_hits += 1

                if not cache_hit:
                    web_search_attempts += 1
                    try:
                        # Defense in depth: a pluggable tool may ignore its own
                        # timeout. ``asyncio.wait_for`` bounds the await so a
                        # misbehaving adapter cannot stall the node.
                        snippets = await asyncio.wait_for(
                            search_tool.search(
                                q, max_results=ws_settings.max_results
                            ),
                            timeout=ws_settings.tavily_timeout,
                        )
                        web_context = format_web_context_for_llm(snippets) or None
                        if cache_key is not None:
                            _web_search_cache_put(cache_key, web_context)
                    except Exception as exc:
                        web_search_errors += 1
                        _safe_detail = _safe_error_detail(exc)
                        logger.warning(
                            "graph_classify",
                            extra={
                                "event": "graph_classify_web_search_error",
                                "ctx": {
                                    "run_id": run_id,
                                    "step": "classify_web_search",
                                    "error_class": type(exc).__name__,
                                    "detail": _safe_detail,
                                    "item_url": normalized.get("item_url"),
                                },
                            },
                        )
                        err_accum.append(
                            {
                                "step": "classify_web_search",
                                "message": "web_search_error",
                                "error_class": type(exc).__name__,
                                "detail": _safe_detail,
                            }
                        )
                        # Catch-and-continue: do not re-raise — avoids multiplying Tavily
                        # traffic under LangGraph full-node RetryPolicy (Story 3.6).
                        # Intentionally not cached: a retry should get a fresh attempt.

        try:
            llm_out = await llm.classify(
                normalized,
                model_id=model_id,
                prompt_version=prompt_version,
                web_context=web_context,
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
    if (
        ws_settings.enabled
        or web_search_attempts
        or web_search_errors
        or web_search_cache_hits
        or _web_search_tool is not None
    ):
        out["llm_trace"]["web_search"] = {
            "feature_enabled": ws_settings.enabled,
            "tool_injected": _web_search_tool is not None,
            "attempts": web_search_attempts,
            "errors": web_search_errors,
            "cache_hits": web_search_cache_hits,
        }
    if any_review:
        merged_flags = dict(state.get("flags") or {})
        merged_flags["needs_human_review"] = True
        out["flags"] = merged_flags
    if err_accum:
        out["errors"] = err_accum

    llm_meta: dict[str, Any] = {
        "status": _status,
        "model_id": model_id,
        "prompt_version": prompt_version,
    }
    if (
        ws_settings.enabled
        or web_search_attempts
        or web_search_errors
        or web_search_cache_hits
        or _web_search_tool is not None
    ):
        # ``tool_injected`` is a test-only DI hook (see ``out["llm_trace"]``);
        # do not persist it into the append-only compliance audit row.
        llm_meta["web_search"] = {
            "feature_enabled": ws_settings.enabled,
            "attempts": web_search_attempts,
            "errors": web_search_errors,
            "cache_hits": web_search_cache_hits,
        }

    audit_errs = await record_pipeline_audit_event(
        run_id=str(run_id),
        action=PipelineAuditAction.PIPELINE_CLASSIFY_COMPLETED,
        source_id=source_uuid,
        metadata={
            "classification_count": len(classifications),
            "severity_histogram": _severity_histogram_from_classifications(
                classifications
            ),
            "llm_trace": llm_meta,
        },
    )
    if audit_errs:
        merged_errs = list(out.get("errors") or []) + audit_errs
        out["errors"] = merged_errs

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
