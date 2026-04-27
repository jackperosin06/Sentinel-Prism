"""Review-queue listing, run-detail, and resume APIs (Stories 4.1–4.2).

Architecture §3.6 — ``GET /review-queue`` lists runs interrupted at
``human_review_gate``; ``GET /runs/{run_id}`` returns a checkpoint projection
plus an audit tail for triage; ``POST /runs/{run_id}/resume`` applies an analyst
decision via LangGraph ``Command(resume=...)`` (**FR17**).

NFR12 trust boundary (documented here and in Dev Notes):
``classifications`` and ``normalized_updates`` are returned under the graph's
own output contract — the nodes that produce them are responsible for not
emitting raw prompts / non-public web-search payloads. ``errors`` and
``llm_trace`` sit closer to provider boundaries, so this module allowlists
them through :class:`ErrorDetailRow` and :func:`_safe_llm_trace` before
serializing them on the wire.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from sqlalchemy.ext.asyncio import AsyncSession

from sentinel_prism.api.deps import require_roles
from sentinel_prism.db.models import PipelineAuditAction, User, UserRole
from sentinel_prism.db.repositories import audit_events as audit_events_repo
from sentinel_prism.db.repositories import review_queue as review_queue_repo
from sentinel_prism.db.session import get_db
from sentinel_prism.graph.checkpoints import use_postgres_pipeline_checkpointer
from sentinel_prism.graph.replay import compile_replay_tail_graph
from sentinel_prism.graph.replay_context import replay_mode
from sentinel_prism.services.llm.classification import IMPACT_CATEGORIES_VOCAB

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/runs", tags=["runs"])
review_queue_router = APIRouter(prefix="/review-queue", tags=["review-queue"])

_ALLOWED_LLM_TRACE_KEYS = frozenset({"model_id", "prompt_version", "status"})
_ERROR_DETAIL_MAX = 512

# Resume API bounds (Story 4.2 code review).
# ``_NOTE_MAX`` caps analyst-submitted free text to keep audit JSONB rows bounded;
# ``_ITEM_URL_MAX`` mirrors common browser URL limits; ``_RATIONALE_MAX`` aligns
# with how LLM rationales are already sized; ``_OVERRIDES_LIST_MAX`` and
# ``_IMPACT_CATEGORIES_LIST_MAX`` defuse DoS / audit-bloat from an authenticated
# analyst or compromised token.
_NOTE_MAX = 4000
_ITEM_URL_MAX = 2048
_RATIONALE_MAX = 4000
_OVERRIDES_LIST_MAX = 100
_IMPACT_CATEGORIES_LIST_MAX = 16
# Cap how many override summaries we drop into ``audit_events.metadata`` —
# enough to reconstruct a normal analyst session, small enough that a
# pathological 100-patch request cannot balloon audit storage.
_OVERRIDE_AUDIT_ROWS_MAX = 20
# Upper bound per-row snapshot fields so a pre-reject snapshot or an override
# summary cannot carry runaway rationale strings into audit storage.
_AUDIT_FIELD_MAX = 512
_PRE_REJECT_SNAPSHOT_MAX = 20
# Hard timeout on the LangGraph resume call. The human-review node is the only
# interrupt point in the current graph and the downstream tail is cheap (audit
# append + queue delete), so 30s is a generous headroom; tune via ops if real
# telemetry shows otherwise.
_GRAPH_RESUME_TIMEOUT_S = 30.0
_GRAPH_REPLAY_TIMEOUT_S = 60.0


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


class ReviewDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    OVERRIDE = "override"


_IMPACT_VOCAB = frozenset(IMPACT_CATEGORIES_VOCAB)


class ClassificationPatchIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    item_url: str | None = Field(default=None, max_length=_ITEM_URL_MAX)
    severity: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    rationale: str | None = Field(default=None, max_length=_RATIONALE_MAX)
    impact_categories: list[str] | None = Field(
        default=None, max_length=_IMPACT_CATEGORIES_LIST_MAX
    )
    urgency: str | None = None

    @field_validator("severity")
    @classmethod
    def _severity_vocab(cls, v: str | None) -> str | None:
        if v is None:
            return None
        allowed = frozenset({"critical", "high", "medium", "low"})
        if v not in allowed:
            raise ValueError("severity must be critical, high, medium, or low")
        return v

    @field_validator("urgency")
    @classmethod
    def _urgency_vocab(cls, v: str | None) -> str | None:
        if v is None:
            return None
        allowed = frozenset({"immediate", "time_bound", "informational"})
        if v not in allowed:
            raise ValueError("urgency must be immediate, time_bound, or informational")
        return v

    @field_validator("impact_categories")
    @classmethod
    def _impact_vocab(cls, v: list[str] | None) -> list[str] | None:
        # Analyst overrides are a higher-trust, lower-volume path than LLM
        # output — refuse unknown tokens so manual overrides cannot dilute
        # downstream aggregations (FR13).
        if v is None:
            return None
        bad = [t for t in v if t not in _IMPACT_VOCAB]
        if bad:
            raise ValueError(
                "impact_categories tokens must be from "
                f"{sorted(_IMPACT_VOCAB)}; got {bad!r}"
            )
        return v

    @model_validator(mode="after")
    def _require_any_field(self) -> ClassificationPatchIn:
        # All-None patch is a no-op; reject so analysts don't silently apply
        # an "override" that changes nothing (and we don't audit a phantom
        # decision).
        fields = (
            self.severity,
            self.confidence,
            self.rationale,
            self.impact_categories,
            self.urgency,
        )
        if all(f is None for f in fields):
            raise ValueError(
                "each override patch must set at least one of: "
                "severity, confidence, rationale, impact_categories, urgency"
            )
        return self


class ResumeRunBody(BaseModel):
    decision: ReviewDecision
    note: str = Field(default="", max_length=_NOTE_MAX)
    overrides: list[ClassificationPatchIn] = Field(
        default_factory=list, max_length=_OVERRIDES_LIST_MAX
    )

    @model_validator(mode="after")
    def _validate_decision_shape(self) -> ResumeRunBody:
        if self.decision in (ReviewDecision.REJECT, ReviewDecision.OVERRIDE):
            if not self.note.strip():
                raise ValueError("note is required for reject and override decisions")
        if self.decision == ReviewDecision.OVERRIDE:
            if not self.overrides:
                raise ValueError("at least one override patch is required")
            # Reject duplicate item_url entries: the graph applies patches in
            # order and the last write silently wins; surface as 422 so the
            # client (or future bulk-edit UX) collapses duplicates explicitly.
            seen: set[str] = set()
            dups: list[str] = []
            for p in self.overrides:
                if p.item_url is None:
                    continue
                key = p.item_url.strip()
                if not key:
                    continue
                if key in seen:
                    dups.append(key)
                else:
                    seen.add(key)
            if dups:
                raise ValueError(
                    f"duplicate item_url in overrides: {sorted(set(dups))!r}"
                )
        else:
            if self.overrides:
                raise ValueError(
                    "overrides are only allowed for override decisions"
                )
        return self


class ResumeRunOut(BaseModel):
    run_id: UUID
    decision: ReviewDecision
    status: str = "completed"


class ReplayRunIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_node: str = Field(
        default="classify",
        description="First node to replay (minimum supported: classify).",
    )
    to_node: str = Field(
        default="route",
        description="Final node to replay (minimum supported: route).",
    )

    @model_validator(mode="after")
    def _validate_nodes(self) -> "ReplayRunIn":
        allowed = frozenset(
            {"scout", "normalize", "classify", "human_review_gate", "brief", "route"}
        )
        if self.from_node not in allowed:
            raise ValueError(f"from_node must be one of {sorted(allowed)}")
        if self.to_node not in allowed:
            raise ValueError(f"to_node must be one of {sorted(allowed)}")
        return self


class ReplayRunOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    original_run_id: str
    replay_run_id: str
    replayed_nodes: list[str]
    started_at: datetime
    finished_at: datetime
    status: str
    errors: list[ErrorDetailRow] = Field(default_factory=list)


def _audit_action_for_decision(decision: ReviewDecision) -> PipelineAuditAction:
    if decision == ReviewDecision.APPROVE:
        return PipelineAuditAction.HUMAN_REVIEW_APPROVED
    if decision == ReviewDecision.REJECT:
        return PipelineAuditAction.HUMAN_REVIEW_REJECTED
    return PipelineAuditAction.HUMAN_REVIEW_OVERRIDDEN


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


@router.post("/{run_id}/resume", response_model=ResumeRunOut)
async def resume_run_after_review(
    run_id: UUID,
    body: ResumeRunBody,
    request: Request,
    user: User = Depends(require_roles(UserRole.ANALYST, UserRole.ADMIN)),
    session: AsyncSession = Depends(get_db),
) -> ResumeRunOut:
    """Resume the regulatory graph after human review (**FR17** — Story 4.2)."""

    pending = await review_queue_repo.get_pending_by_run_id(session, run_id)
    if pending is None:
        logger.info(
            "runs_api",
            extra={
                "event": "resume_run_not_in_queue",
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
    values = getattr(snap, "values", None) or {}
    if not values:
        logger.info(
            "runs_api",
            extra={
                "event": "resume_run_no_checkpoint",
                "ctx": {"run_id": str(run_id)},
            },
        )
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail="No checkpoint state found for this run",
        )

    interrupts = getattr(snap, "interrupts", None) or ()
    if len(interrupts) == 0:
        logger.info(
            "runs_api",
            extra={
                "event": "resume_run_not_interrupted",
                "ctx": {"run_id": str(run_id)},
            },
        )
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail="Run is not waiting for human review",
        )

    resume_payload: dict[str, Any] = {
        "decision": body.decision.value,
        "note": body.note.strip(),
        "overrides": [
            p.model_dump(exclude_none=True) for p in body.overrides
        ],
    }

    # Snapshot the in-scope classifications BEFORE resume so the reject path
    # can attach a pre-reject forensic trail to audit metadata — the
    # ``_rejected_row`` transform destroys original severity / urgency /
    # rationale / confidence / impact_categories in state.
    pre_reject_snapshot: list[dict[str, Any]] = []
    if body.decision == ReviewDecision.REJECT:
        raw_pre = values.get("classifications") or []
        for row in raw_pre:
            if not isinstance(row, dict):
                continue
            if row.get("in_scope") is True:
                pre_reject_snapshot.append(_snapshot_row(row))
            if len(pre_reject_snapshot) >= _PRE_REJECT_SNAPSHOT_MAX:
                break

    logger.info(
        "runs_api",
        extra={
            "event": "resume_run_start",
            "ctx": {
                "run_id": str(run_id),
                "decision": body.decision.value,
                "user_id": str(user.id),
            },
        },
    )

    try:
        await asyncio.wait_for(
            graph.ainvoke(Command(resume=resume_payload), cfg),
            timeout=_GRAPH_RESUME_TIMEOUT_S,
        )
    except asyncio.CancelledError:
        # Client disconnect or request cancellation — never masquerade as an
        # upstream-gateway failure; let cancellation propagate so FastAPI /
        # the worker handle it.
        raise
    except HTTPException:
        raise
    except asyncio.TimeoutError:
        logger.warning(
            "runs_api",
            extra={
                "event": "resume_run_graph_timeout",
                "ctx": {
                    "run_id": str(run_id),
                    "timeout_s": _GRAPH_RESUME_TIMEOUT_S,
                },
            },
        )
        raise HTTPException(
            status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Regulatory pipeline resume timed out",
        ) from None
    except Exception:
        # Any other graph-internal failure (programming error, checkpointer
        # error, node exception) is an internal server error — 500, not 502.
        # The compiled graph runs in-process; there is no upstream gateway.
        logger.exception(
            "runs_api",
            extra={
                "event": "resume_run_graph_failed",
                "ctx": {"run_id": str(run_id)},
            },
        )
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Regulatory pipeline resume failed",
        ) from None

    # Defensive: the current ``regulatory`` graph ends at ``human_review_gate
    # → END`` so a well-formed resume always clears the interrupt. If a future
    # node adds another HITL gate, a successful ``ainvoke`` can leave the
    # graph re-interrupted — do not claim ``status="completed"`` or delete
    # the queue row in that case.
    snap_after = await graph.aget_state(cfg)
    still_interrupted = bool(getattr(snap_after, "interrupts", None) or ())

    note_excerpt = body.note.strip()[:_NOTE_MAX]
    meta: dict[str, Any] = {
        "decision": body.decision.value,
        "note": note_excerpt,
    }
    if body.decision == ReviewDecision.OVERRIDE:
        patch_summary: list[dict[str, Any]] = []
        for p in body.overrides[:_OVERRIDE_AUDIT_ROWS_MAX]:
            entry: dict[str, Any] = {}
            if p.item_url is not None:
                entry["item_url"] = p.item_url
            if p.severity is not None:
                entry["severity"] = p.severity
            if p.confidence is not None:
                entry["confidence"] = p.confidence
            if p.urgency is not None:
                entry["urgency"] = p.urgency
            if p.rationale is not None:
                entry["rationale"] = p.rationale[:_AUDIT_FIELD_MAX]
            if p.impact_categories is not None:
                entry["impact_categories"] = list(p.impact_categories)
            patch_summary.append(entry)
        if patch_summary:
            meta["override_patches"] = patch_summary
    if body.decision == ReviewDecision.REJECT and pre_reject_snapshot:
        meta["pre_reject_snapshot"] = pre_reject_snapshot
    if still_interrupted:
        meta["graph_status"] = "re_interrupted"

    action = _audit_action_for_decision(body.decision)
    await audit_events_repo.append_audit_event(
        session,
        run_id=run_id,
        action=action,
        source_id=pending.source_id,
        metadata=meta,
        actor_user_id=user.id,
    )

    deleted = False
    if not still_interrupted:
        deleted = await review_queue_repo.delete_pending_by_run_id(
            session, run_id=run_id
        )
    await session.commit()

    if not still_interrupted and not deleted:
        logger.warning(
            "runs_api",
            extra={
                "event": "resume_run_queue_row_missing_after_graph",
                "ctx": {"run_id": str(run_id)},
            },
        )
    if still_interrupted:
        logger.warning(
            "runs_api",
            extra={
                "event": "resume_run_graph_re_interrupted",
                "ctx": {"run_id": str(run_id)},
            },
        )

    logger.info(
        "runs_api",
        extra={
            "event": "resume_run_ok",
            "ctx": {
                "run_id": str(run_id),
                "decision": body.decision.value,
                "user_id": str(user.id),
                "graph_status": (
                    "re_interrupted" if still_interrupted else "completed"
                ),
            },
        },
    )
    return ResumeRunOut(
        run_id=run_id,
        decision=body.decision,
        status="re_interrupted" if still_interrupted else "completed",
    )


def _snapshot_row(row: dict[str, Any]) -> dict[str, Any]:
    """Compact forensic snapshot of a classification row for reject audit."""

    rationale = row.get("rationale")
    if isinstance(rationale, str) and len(rationale) > _AUDIT_FIELD_MAX:
        rationale = rationale[:_AUDIT_FIELD_MAX]
    cats_raw = row.get("impact_categories") or []
    cats = [
        str(x)[:_AUDIT_FIELD_MAX]
        for x in cats_raw[:_IMPACT_CATEGORIES_LIST_MAX]
        if isinstance(x, (str, int, float))
    ]
    return {
        "item_url": str(row.get("item_url") or "")[:_ITEM_URL_MAX] or None,
        "severity": row.get("severity"),
        "urgency": row.get("urgency"),
        "confidence": row.get("confidence"),
        "rationale": rationale,
        "impact_categories": cats,
    }


@router.post("/{run_id}/replay", response_model=ReplayRunOut)
async def replay_run_from_checkpoint(
    run_id: UUID,
    body: ReplayRunIn,
    request: Request,
    _user: User = Depends(require_roles(UserRole.ANALYST, UserRole.ADMIN)),
) -> ReplayRunOut:
    """Replay a tail segment from persisted checkpoint state (Story 8.2)."""

    if not use_postgres_pipeline_checkpointer():
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Replay requires a persistent checkpointer (Postgres). Set PIPELINE_CHECKPOINTER=postgres and DATABASE_URL.",
        )

    started_at = datetime.now(timezone.utc)

    def _unsupported(message: str) -> ReplayRunOut:
        return ReplayRunOut(
            original_run_id=str(run_id),
            replay_run_id="",
            replayed_nodes=[],
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
            status="unsupported",
            errors=[
                ErrorDetailRow(
                    step="replay",
                    message="unsupported",
                    error_class="UnsupportedReplaySegment",
                    detail=message,
                )
            ],
        )

    if body.to_node != "route":
        return _unsupported("Only to_node='route' is supported.")
    if body.from_node == "normalize":
        return _unsupported("Replay from 'normalize' is not supported (offline replay uses checkpointed classifications).")
    if body.from_node not in {"classify", "human_review_gate", "brief"}:
        return _unsupported("Only from_node in {'classify','human_review_gate','brief'} is supported.")

    graph = get_compiled_regulatory_graph(request)
    original_cfg: dict[str, Any] = {"configurable": {"thread_id": str(run_id)}}
    snap = await graph.aget_state(original_cfg)
    values = getattr(snap, "values", None) or {}
    if not values:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail="No checkpoint state found for this run",
        )

    replay_id = uuid.uuid4()
    replay_cfg: dict[str, Any] = {"configurable": {"thread_id": str(replay_id)}}

    state_seed: dict[str, Any] = dict(values)
    state_seed["run_id"] = str(replay_id)
    flags = dict(state_seed.get("flags") or {})
    flags["replay_mode"] = True
    flags["replay_original_run_id"] = str(run_id)
    state_seed["flags"] = flags

    plan = compile_replay_tail_graph(from_node=body.from_node)
    try:
        with replay_mode():
            out = await asyncio.wait_for(
                plan.graph.ainvoke(state_seed, replay_cfg),
                timeout=_GRAPH_REPLAY_TIMEOUT_S,
            )
        finished_at = datetime.now(timezone.utc)
        status_out = "completed"
    except asyncio.TimeoutError:
        finished_at = datetime.now(timezone.utc)
        status_out = "failed"
        out = {
            "errors": [
                {
                    "step": "replay",
                    "message": "replay_timeout",
                    "error_class": "TimeoutError",
                    "detail": f"timeout_s={_GRAPH_REPLAY_TIMEOUT_S}",
                }
            ]
        }
    except Exception as exc:  # noqa: BLE001
        finished_at = datetime.now(timezone.utc)
        status_out = "failed"
        out = {
            "errors": [
                {
                    "step": "replay",
                    "message": "replay_failed",
                    "error_class": type(exc).__name__,
                    "detail": str(exc)[:200],
                }
            ]
        }

    raw_err = out.get("errors") or []
    errors: list[ErrorDetailRow] = []
    for entry in raw_err:
        safe = _safe_error(entry)
        if safe is not None:
            errors.append(safe)

    return ReplayRunOut(
        original_run_id=str(run_id),
        replay_run_id=str(replay_id),
        replayed_nodes=plan.replayed_nodes,
        started_at=started_at,
        finished_at=finished_at,
        status=(
            "partial" if status_out == "completed" and errors else status_out
        ),
        errors=errors,
    )
