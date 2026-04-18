"""Scout node — fetch raw items into ``AgentState.raw_items`` (Story 3.3)."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sentinel_prism.db.models import PipelineAuditAction, SourceType
from sentinel_prism.db.repositories import captures as captures_repo
from sentinel_prism.db.repositories import sources as sources_repo
from sentinel_prism.db.repositories.audit_events import ITEM_URL_SAMPLES_CAP
from sentinel_prism.db.session import get_session_factory
from sentinel_prism.graph.pipeline_audit import record_pipeline_audit_event
from sentinel_prism.graph.state import AgentState
from sentinel_prism.services.connectors.errors import ConnectorFetchFailed
from sentinel_prism.services.connectors.scout_fetch import (
    FallbackFetchUnexpectedError,
    PrimaryAndFallbackFailed,
    fetch_scout_items_with_fallback,
)

logger = logging.getLogger(__name__)


async def node_scout(state: AgentState) -> dict[str, Any]:
    run_id = state.get("run_id")
    if not run_id or not str(run_id).strip():
        raise ValueError("AgentState.run_id is required but missing or empty")

    sid = state.get("source_id")
    if not sid or not str(sid).strip():
        logger.info(
            "graph_scout",
            extra={
                "event": "graph_scout_skipped",
                "ctx": {"run_id": run_id, "reason": "source_id_required"},
            },
        )
        return {
            "errors": [{"step": "scout", "message": "source_id_required"}],
        }

    try:
        source_uuid = uuid.UUID(str(sid).strip())
    except ValueError:
        return {
            "errors": [
                {
                    "step": "scout",
                    "message": "invalid_source_id",
                    "detail": str(sid),
                }
            ],
        }

    trigger = state.get("trigger") or "manual"

    try:
        factory = get_session_factory()
        async with factory() as session:
            row = await sources_repo.get_source_by_id(session, source_uuid)
    except Exception as exc:
        # Architecture §5: errors are appended, not swallowed silently.
        # Uncaught factory / session errors would otherwise abort the graph run.
        logger.warning(
            "graph_scout",
            extra={
                "event": "graph_scout_db_error",
                "ctx": {
                    "run_id": run_id,
                    "source_id": str(source_uuid),
                    "error_class": type(exc).__name__,
                },
            },
        )
        return {
            "errors": [
                {
                    "step": "scout",
                    "message": "db_error",
                    "error_class": type(exc).__name__,
                    "detail": str(exc),
                }
            ],
        }

    if row is None:
        logger.info(
            "graph_scout",
            extra={
                "event": "graph_scout_skipped",
                "ctx": {"run_id": run_id, "reason": "source_not_found"},
            },
        )
        return {
            "errors": [
                {
                    "step": "scout",
                    "message": "source_not_found",
                    "source_id": str(source_uuid),
                }
            ],
        }

    if not row.enabled:
        logger.info(
            "graph_scout",
            extra={
                "event": "graph_scout_skipped",
                "ctx": {"run_id": run_id, "reason": "source_disabled"},
            },
        )
        return {
            "errors": [
                {
                    "step": "scout",
                    "message": "source_disabled",
                    "source_id": str(source_uuid),
                }
            ],
        }

    source_type: SourceType = row.source_type
    primary_url: str = row.primary_url
    fallback_url: str | None = row.fallback_url
    fallback_mode = row.fallback_mode

    if source_type not in (SourceType.RSS, SourceType.HTTP):
        logger.info(
            "graph_scout",
            extra={
                "event": "graph_scout_skipped",
                "ctx": {
                    "run_id": run_id,
                    "reason": "unsupported_source_type",
                    "source_type": str(source_type),
                },
            },
        )
        return {
            "errors": [
                {
                    "step": "scout",
                    "message": "unsupported_source_type",
                    "source_type": str(source_type),
                }
            ],
        }

    fetched_at = datetime.now(timezone.utc)
    try:
        items, outcome = await fetch_scout_items_with_fallback(
            source_id=source_uuid,
            source_type=source_type,
            primary_url=primary_url,
            fallback_mode=fallback_mode,
            fallback_url=fallback_url,
            fetched_at=fetched_at,
            trigger=trigger,
        )
    except ConnectorFetchFailed as exc:
        logger.warning(
            "graph_scout",
            extra={
                "event": "graph_scout_fetch_failed",
                "ctx": {
                    "run_id": run_id,
                    "source_id": str(source_uuid),
                    "error_class": exc.error_class,
                    "fetch_path": "primary",
                },
            },
        )
        return {
            "errors": [
                {
                    "step": "scout",
                    "message": "connector_fetch_failed",
                    "error_class": exc.error_class,
                    "detail": str(exc),
                    "fetch_path": "primary",
                }
            ],
        }
    except PrimaryAndFallbackFailed as both:
        logger.warning(
            "graph_scout",
            extra={
                "event": "graph_scout_fetch_failed",
                "ctx": {
                    "run_id": run_id,
                    "source_id": str(source_uuid),
                    "primary_error_class": both.primary.error_class,
                    "fallback_error_class": both.fallback.error_class,
                    "fetch_path": "both",
                },
            },
        )
        return {
            "errors": [
                {
                    "step": "scout",
                    "message": "primary_and_fallback_failed",
                    "primary_error_class": both.primary.error_class,
                    "fallback_error_class": both.fallback.error_class,
                }
            ],
        }
    except FallbackFetchUnexpectedError as wrapped:
        exc = wrapped.cause
        logger.warning(
            "graph_scout",
            extra={
                "event": "graph_scout_fetch_failed",
                "ctx": {
                    "run_id": run_id,
                    "source_id": str(source_uuid),
                    "error_class": type(exc).__name__,
                    "fetch_path": "fallback",
                },
            },
        )
        return {
            "errors": [
                {
                    "step": "scout",
                    "message": "fallback_unexpected_error",
                    "error_class": type(exc).__name__,
                    "detail": str(exc),
                    "fetch_path": "fallback",
                }
            ],
        }
    except Exception as exc:
        logger.warning(
            "graph_scout",
            extra={
                "event": "graph_scout_fetch_failed",
                "ctx": {
                    "run_id": run_id,
                    "source_id": str(source_uuid),
                    "error_class": type(exc).__name__,
                    "fetch_path": "primary",
                },
            },
        )
        return {
            "errors": [
                {
                    "step": "scout",
                    "message": "fetch_failed",
                    "error_class": type(exc).__name__,
                    "detail": str(exc),
                    "fetch_path": "primary",
                }
            ],
        }

    raw_payloads = [captures_repo.scout_raw_item_payload(it) for it in items]
    logger.info(
        "graph_scout",
        extra={
            "event": "graph_scout_fetched",
            "ctx": {
                "run_id": run_id,
                "source_id": str(source_uuid),
                "item_count": len(items),
                "fetch_outcome": outcome,
            },
        },
    )
    samples: list[str] = []
    for p in raw_payloads[:ITEM_URL_SAMPLES_CAP]:
        if isinstance(p, dict):
            u = p.get("item_url")
            if isinstance(u, str) and u.strip():
                samples.append(u.strip())
        if len(samples) >= ITEM_URL_SAMPLES_CAP:
            break
    meta: dict[str, Any] = {
        "raw_item_count": len(raw_payloads),
        "trigger": trigger,
        "fetch_outcome": outcome,
    }
    if samples:
        meta["item_url_samples"] = samples
    audit_errs = await record_pipeline_audit_event(
        run_id=str(run_id),
        action=PipelineAuditAction.PIPELINE_SCOUT_COMPLETED,
        source_id=source_uuid,
        metadata=meta,
    )
    out: dict[str, Any] = {"raw_items": raw_payloads}
    if audit_errs:
        out["errors"] = audit_errs
    return out
