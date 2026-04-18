"""Review-queue listing and run-detail APIs (Story 4.1).

Architecture §3.6 — ``GET /review-queue`` lists runs interrupted at
``human_review_gate``; ``GET /runs/{run_id}`` returns a checkpoint projection
plus an audit tail for triage.

NFR12 trust boundary (documented here and in Dev Notes):
``classifications`` and ``normalized_updates`` are returned under the graph's
own output contract — the nodes that produce them are responsible for not
emitting raw prompts / non-public web-search payloads. ``errors`` and
``llm_trace`` sit closer to provider boundaries, so this module allowlists
them through :class:`ErrorDetailRow` and :func:`_safe_llm_trace` before
serializing them on the wire.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from sentinel_prism.api.deps import require_roles
from sentinel_prism.db.models import User, UserRole
from sentinel_prism.db.repositories import audit_events as audit_events_repo
from sentinel_prism.db.repositories import review_queue as review_queue_repo
from sentinel_prism.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/runs", tags=["runs"])
review_queue_router = APIRouter(prefix="/review-queue", tags=["review-queue"])

_ALLOWED_LLM_TRACE_KEYS = frozenset({"model_id", "prompt_version", "status"})
_ERROR_DETAIL_MAX = 512


def get_compiled_regulatory_graph(request: Request) -> CompiledStateGraph:
    """Return the app-scoped compiled graph (Story 4.1 lifespan)."""

    g = getattr(request.app.state, "regulatory_graph", None)
    if g is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Regulatory pipeline graph is not initialized",
        )
    return g


def _safe_llm_trace(trace: Any) -> dict[str, Any] | None:
    if not isinstance(trace, dict):
        return None
    return {k: trace[k] for k in _ALLOWED_LLM_TRACE_KEYS if k in trace}


class ClassificationListRow(BaseModel):
    """Triage row materialized from ``review_queue_items.items_summary``."""

    model_config = ConfigDict(extra="ignore")

    item_url: str = ""
    source_id: UUID | None = None
    in_scope: bool | None = None
    severity: str | None = None
    confidence: float | None = None
    needs_human_review: bool | None = None
    rationale_excerpt: str = ""
    impact_categories: list[str] = Field(default_factory=list)
    urgency: str | None = None


class ReviewQueueListItemOut(BaseModel):
    run_id: UUID
    source_id: UUID | None = None
    queued_at: datetime
    classifications: list[ClassificationListRow]


class ReviewQueueListOut(BaseModel):
    items: list[ReviewQueueListItemOut]


class AuditEventOut(BaseModel):
    id: UUID
    created_at: datetime
    action: str
    metadata: dict[str, Any] | None = None


class ErrorDetailRow(BaseModel):
    """Allowlisted error projection for run detail (NFR12).

    Provider / driver exceptions can carry SQL parameters, API keys, or raw
    upstream URLs inside ``str(exc)``. We only forward a small fixed field set
    and drop every other key the graph nodes might have stored — even if a
    node regresses and appends sensitive state, it will not leak past this
    boundary. ``detail`` is truncated defensively.
    """

    model_config = ConfigDict(extra="ignore")

    step: str = ""
    message: str = ""
    error_class: str = ""
    detail: str | None = None


def _safe_error(entry: Any) -> ErrorDetailRow | None:
    if not isinstance(entry, dict):
        return None
    try:
        row = ErrorDetailRow.model_validate(entry)
    except ValidationError:
        return None
    if row.detail is not None and len(row.detail) > _ERROR_DETAIL_MAX:
        row = row.model_copy(update={"detail": row.detail[:_ERROR_DETAIL_MAX] + "…"})
    return row


class RunDetailOut(BaseModel):
    run_id: str
    source_id: str | None = None
    flags: dict[str, bool] = Field(default_factory=dict)
    classifications: list[dict[str, Any]] = Field(default_factory=list)
    normalized_updates: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[ErrorDetailRow] = Field(default_factory=list)
    llm_trace: dict[str, Any] | None = None
    audit_events_tail: list[AuditEventOut] = Field(default_factory=list)


@review_queue_router.get("", response_model=ReviewQueueListOut)
async def list_review_queue(
    limit: int = Query(
        review_queue_repo.LIST_PENDING_DEFAULT_LIMIT,
        ge=1,
        le=review_queue_repo.LIST_PENDING_MAX_LIMIT,
        description="Max review-queue rows to return (newest first).",
    ),
    offset: int = Query(
        0,
        ge=0,
        description="Pagination offset, newest-first.",
    ),
    _user: User = Depends(require_roles(UserRole.ANALYST, UserRole.ADMIN)),
    session: AsyncSession = Depends(get_db),
) -> ReviewQueueListOut:
    """List runs currently awaiting human review (AC #1)."""

    rows = await review_queue_repo.list_pending_review_items(
        session, limit=limit, offset=offset
    )
    items: list[ReviewQueueListItemOut] = []
    for row in rows:
        raw_summary = row.items_summary or []
        cls_models: list[ClassificationListRow] = []
        for entry in raw_summary:
            if not isinstance(entry, dict):
                continue
            try:
                cls_models.append(ClassificationListRow.model_validate(entry))
            except ValidationError as exc:
                # NFR8 — do not let one corrupt JSONB row poison the whole
                # response. Skip the entry and surface it via structured log
                # so the underlying data can be repaired.
                logger.warning(
                    "review_queue_api",
                    extra={
                        "event": "list_review_queue_bad_summary_entry",
                        "ctx": {
                            "run_id": str(row.run_id),
                            "error_count": len(exc.errors()),
                        },
                    },
                )
        items.append(
            ReviewQueueListItemOut(
                run_id=row.run_id,
                source_id=row.source_id,
                queued_at=row.queued_at,
                classifications=cls_models,
            )
        )

    logger.info(
        "review_queue_api",
        extra={
            "event": "list_review_queue",
            "ctx": {
                "count": len(items),
                "limit": limit,
                "offset": offset,
                "sample_run_ids": [str(it.run_id) for it in items[:5]],
            },
        },
    )
    return ReviewQueueListOut(items=items)


@router.get("/{run_id}", response_model=RunDetailOut)
async def get_run_detail(
    run_id: UUID,
    request: Request,
    _user: User = Depends(require_roles(UserRole.ANALYST, UserRole.ADMIN)),
    session: AsyncSession = Depends(get_db),
) -> RunDetailOut:
    pending = await review_queue_repo.get_pending_by_run_id(session, run_id)
    if pending is None:
        logger.info(
            "runs_api",
            extra={
                "event": "get_run_detail_not_in_queue",
                "ctx": {"run_id": str(run_id)},
            },
        )
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail="Run is not in the review queue",
        )
    graph = get_compiled_regulatory_graph(request)
    cfg: dict[str, Any] = {"configurable": {"thread_id": str(run_id)}}
    snap = await graph.aget_state(cfg)
    # ``aget_state`` may return a StateSnapshot whose ``values`` is ``None``
    # (e.g. with MemorySaver after process restart). Guard defensively so we
    # surface a 404 rather than a 500 on ``None.get``.
    values = getattr(snap, "values", None) or {}
    if not values:
        logger.info(
            "runs_api",
            extra={
                "event": "get_run_detail_no_checkpoint",
                "ctx": {"run_id": str(run_id)},
            },
        )
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail="No checkpoint state found for this run",
        )

    raw_flags = values.get("flags")
    flags: dict[str, bool] = (
        {k: bool(v) for k, v in raw_flags.items()}
        if isinstance(raw_flags, dict)
        else {}
    )
    run_id_str = str(values.get("run_id") or run_id)
    src = values.get("source_id")
    source_id_str = str(src) if src is not None else None

    raw_cls = values.get("classifications") or []
    classifications = [x for x in raw_cls if isinstance(x, dict)]

    raw_norm = values.get("normalized_updates") or []
    normalized_updates = [x for x in raw_norm if isinstance(x, dict)]

    raw_err = values.get("errors") or []
    errors: list[ErrorDetailRow] = []
    for entry in raw_err:
        safe = _safe_error(entry)
        if safe is not None:
            errors.append(safe)

    llm_trace = _safe_llm_trace(values.get("llm_trace"))

    audit_rows = await audit_events_repo.list_recent_for_run(
        session, run_id=run_id_str, limit=20
    )
    audit_out = [
        AuditEventOut(
            id=ev.id,
            created_at=ev.created_at,
            action=ev.action.value if hasattr(ev.action, "value") else str(ev.action),
            metadata=dict(ev.event_metadata) if ev.event_metadata else None,
        )
        for ev in audit_rows
    ]

    logger.info(
        "runs_api",
        extra={
            "event": "get_run_detail_ok",
            "ctx": {
                "run_id": run_id_str,
                "classification_count": len(classifications),
                "error_count": len(errors),
                "audit_tail_count": len(audit_out),
            },
        },
    )
    return RunDetailOut(
        run_id=run_id_str,
        source_id=source_id_str,
        flags=flags,
        classifications=classifications,
        normalized_updates=normalized_updates,
        errors=errors,
        llm_trace=llm_trace,
        audit_events_tail=audit_out,
    )
