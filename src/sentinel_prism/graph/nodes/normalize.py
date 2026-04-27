"""Normalize node — ``raw_items`` → ``normalized_updates`` (Story 3.3)."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sentinel_prism.observability import obs_ctx
from sentinel_prism.db.models import PipelineAuditAction
from sentinel_prism.db.repositories import sources as sources_repo
from sentinel_prism.db.session import get_session_factory
from sentinel_prism.graph.pipeline_audit import record_pipeline_audit_event
from sentinel_prism.graph.state import AgentState
from sentinel_prism.services.connectors.scout_raw_item import scout_raw_item_from_payload
from sentinel_prism.services.ingestion.normalize import (
    normalize_scout_item,
    normalized_update_to_state_dict,
)

logger = logging.getLogger(__name__)
_NODE_ID = "normalize"


async def node_normalize(state: AgentState) -> dict[str, Any]:
    run_id = state.get("run_id")
    if not run_id or not str(run_id).strip():
        raise ValueError("AgentState.run_id is required but missing or empty")
    run_id = str(run_id).strip()

    sid = state.get("source_id")
    if not sid or not str(sid).strip():
        logger.info(
            "graph_normalize",
            extra={
                "event": "graph_normalize_skipped",
                "ctx": obs_ctx(node_id=_NODE_ID, run_id=run_id, reason="source_id_required"),
            },
        )
        return {
            "errors": [{"step": "normalize", "message": "source_id_required"}],
        }

    try:
        source_uuid = uuid.UUID(str(sid).strip())
    except ValueError:
        return {
            "errors": [
                {
                    "step": "normalize",
                    "message": "invalid_source_id",
                    "detail": str(sid),
                }
            ],
        }

    raws: list[dict[str, Any]] = list(state.get("raw_items") or [])
    if not raws:
        logger.info(
            "graph_normalize",
            extra={
                "event": "graph_normalize_empty_raw",
                "ctx": obs_ctx(node_id=_NODE_ID, run_id=run_id, source_id=str(source_uuid)),
            },
        )
        # AC #3: empty-but-successful completion still emits an audit row so
        # Epic 8 forensics see every node completion, not only non-empty ones.
        audit_errs = await record_pipeline_audit_event(
            run_id=str(run_id),
            action=PipelineAuditAction.PIPELINE_NORMALIZE_COMPLETED,
            source_id=source_uuid,
            metadata={"normalized_count": 0},
        )
        return {"errors": audit_errs} if audit_errs else {}

    try:
        factory = get_session_factory()
        async with factory() as session:
            row = await sources_repo.get_source_by_id(session, source_uuid)
    except Exception as exc:
        # Architecture §5: errors are appended, not swallowed silently.
        # Uncaught factory / session errors would otherwise abort the graph run.
        logger.warning(
            "graph_normalize",
            extra={
                "event": "graph_normalize_db_error",
                "ctx": obs_ctx(
                    node_id=_NODE_ID,
                    run_id=run_id,
                    source_id=str(source_uuid),
                    error_class=type(exc).__name__,
                ),
            },
        )
        return {
            "errors": [
                {
                    "step": "normalize",
                    "message": "db_error",
                    "error_class": type(exc).__name__,
                    "detail": str(exc),
                }
            ],
        }

    if row is None:
        return {
            "errors": [
                {
                    "step": "normalize",
                    "message": "source_not_found",
                    "source_id": str(source_uuid),
                }
            ],
        }

    source_name = row.name
    jurisdiction = row.jurisdiction

    norms: list[dict[str, Any]] = []
    err_accum: list[dict[str, Any]] = []
    for raw in raws:
        try:
            item = scout_raw_item_from_payload(raw)
        except Exception as exc:
            err_accum.append(
                {
                    "step": "normalize",
                    "message": "raw_item_decode_failed",
                    "detail": str(exc),
                    "error_class": type(exc).__name__,
                }
            )
            continue
        nu = normalize_scout_item(
            item,
            source_id=source_uuid,
            source_name=source_name,
            jurisdiction=jurisdiction,
        )
        norms.append(normalized_update_to_state_dict(nu))

    logger.info(
        "graph_normalize",
        extra={
            "event": "graph_normalize_done",
            "ctx": obs_ctx(
                node_id=_NODE_ID,
                run_id=run_id,
                source_id=str(source_uuid),
                normalized_count=len(norms),
            ),
        },
    )
    audit_errs = await record_pipeline_audit_event(
        run_id=str(run_id),
        action=PipelineAuditAction.PIPELINE_NORMALIZE_COMPLETED,
        source_id=source_uuid,
        metadata={"normalized_count": len(norms)},
    )
    out: dict[str, Any] = {"normalized_updates": norms}
    errs = list(err_accum) + list(audit_errs)
    if errs:
        out["errors"] = errs
    return out
