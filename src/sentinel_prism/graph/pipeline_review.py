"""Persist review-queue projection from graph nodes (Story 4.1)."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from sentinel_prism.db.models import PipelineAuditAction
from sentinel_prism.db.repositories import review_queue as review_queue_repo
from sentinel_prism.db.session import get_session_factory
from sentinel_prism.graph.pipeline_audit import record_pipeline_audit_event
from sentinel_prism.graph.replay_context import in_replay_mode

logger = logging.getLogger(__name__)

_RATIONALE_LIST_MAX = 200
# Hard cap on how many per-item classification summaries we persist on the
# queue row. The full ``classifications`` list lives in the checkpoint and is
# returned from ``GET /runs/{run_id}``; the queue summary is triage-only and
# must not balloon the JSONB row for sources that emit hundreds of items.
_MAX_SUMMARY_ENTRIES = 50


def classification_summaries_for_queue(
    classifications: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Build bounded JSON summaries for ``review_queue_items.items_summary``.

    Truncates per-row rationale to ``_RATIONALE_LIST_MAX`` chars and caps the
    returned list at ``_MAX_SUMMARY_ENTRIES`` entries. Full classification
    detail remains accessible via the run-detail endpoint.
    """

    out: list[dict[str, Any]] = []
    for row in classifications or []:
        if not isinstance(row, dict):
            continue
        if len(out) >= _MAX_SUMMARY_ENTRIES:
            break
        raw_rat = row.get("rationale")
        rat_s = raw_rat if isinstance(raw_rat, str) else str(raw_rat) if raw_rat is not None else ""
        excerpt = rat_s[:_RATIONALE_LIST_MAX] + ("…" if len(rat_s) > _RATIONALE_LIST_MAX else "")
        raw_cats = row.get("impact_categories")
        impact_categories = list(raw_cats) if isinstance(raw_cats, list) else []
        out.append(
            {
                "item_url": row.get("item_url") or "",
                "source_id": row.get("source_id") or "",
                "in_scope": row.get("in_scope"),
                "severity": row.get("severity"),
                "confidence": row.get("confidence"),
                "needs_human_review": row.get("needs_human_review"),
                "rationale_excerpt": excerpt,
                "impact_categories": impact_categories,
                "urgency": row.get("urgency"),
            }
        )
    return out


async def _emit_projection_failure_audit(
    *,
    run_id: str,
    source_id: uuid.UUID | None,
    error_class: str,
    error_message: str,
) -> None:
    """Best-effort audit fallback when the projection row cannot be written.

    The audit row uses its own session (same pattern as ``record_pipeline_audit_event``);
    if the audit write itself fails we only log — the `interrupt()` path must
    still fire so the checkpoint is preserved.
    """

    try:
        await record_pipeline_audit_event(
            run_id=run_id,
            action=PipelineAuditAction.HUMAN_REVIEW_QUEUE_PROJECTION_FAILED,
            source_id=source_id,
            metadata={
                "error_class": error_class,
                "error_message": error_message[:512],
            },
        )
    except Exception as exc:  # pragma: no cover — defensive log-only branch
        logger.warning(
            "pipeline_review",
            extra={
                "event": "review_queue_projection_audit_fallback_failed",
                "ctx": {
                    "run_id": run_id,
                    "error_class": type(exc).__name__,
                },
            },
        )


async def record_review_queue_projection(
    *,
    run_id: str,
    source_id: uuid.UUID | None,
    items_summary: list[dict[str, Any]],
    queued_at: datetime | None = None,
) -> None:
    """Upsert queue row in a short session.

    On failure we (1) log a warning and (2) append an ``audit_events`` row
    with action ``HUMAN_REVIEW_QUEUE_PROJECTION_FAILED`` so the interrupted
    run is still discoverable via audit search. ``interrupt()`` must run
    unconditionally so the checkpoint persists — therefore we do not raise.
    """

    if in_replay_mode():
        return

    try:
        factory = get_session_factory()
        async with factory() as session:
            await review_queue_repo.upsert_pending(
                session,
                run_id=run_id,
                source_id=source_id,
                items_summary=items_summary,
                queued_at=queued_at,
            )
            await session.commit()
    except Exception as exc:
        logger.warning(
            "pipeline_review",
            extra={
                "event": "review_queue_projection_failed",
                "ctx": {
                    "run_id": run_id,
                    "error_class": type(exc).__name__,
                },
            },
        )
        await _emit_projection_failure_audit(
            run_id=run_id,
            source_id=source_id,
            error_class=type(exc).__name__,
            error_message=str(exc),
        )
