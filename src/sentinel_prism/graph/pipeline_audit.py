"""Persist pipeline audit rows from graph nodes (Story 3.8)."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sentinel_prism.db.models import PipelineAuditAction
from sentinel_prism.db.repositories import audit_events as audit_events_repo
from sentinel_prism.db.session import get_session_factory

logger = logging.getLogger(__name__)


def _safe_error_detail(exc: BaseException, *, limit: int = 200) -> str:
    """Render an exception for state.errors / logs without leaking sensitive text.

    SQLAlchemy / driver exceptions routinely embed full SQL plus parameter values
    (item URLs, serialized metadata) in ``str(exc)`` — NFR12 forbids that payload
    from flowing back into ``AgentState``. Strip newlines and truncate so the
    entry stays single-line and bounded.
    """

    text = str(exc).replace("\r", " ").replace("\n", " ").strip()
    if len(text) > limit:
        text = text[:limit] + "…"
    return text


async def record_pipeline_audit_event(
    *,
    run_id: str,
    action: PipelineAuditAction,
    source_id: uuid.UUID | None,
    metadata: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Append one audit event in a short session.

    Returns entries to merge into ``AgentState.errors`` when persistence fails.
    Invalid ``run_id`` (non-UUID) is logged in the repository and does not produce
    errors here — the pipeline continues (AC #1).
    """

    try:
        factory = get_session_factory()
        async with factory() as session:
            new_id = await audit_events_repo.append_audit_event(
                session,
                run_id=run_id,
                action=action,
                source_id=source_id,
                metadata=metadata,
                actor_user_id=None,
            )
            if new_id is None:
                return []
            await session.commit()
    except Exception as exc:
        logger.warning(
            "pipeline_audit",
            extra={
                "event": "audit_write_failed",
                "ctx": {
                    "run_id": run_id,
                    "action": action.value,
                    "error_class": type(exc).__name__,
                },
            },
        )
        return [
            {
                "step": "audit_write",
                "message": "audit_persist_failed",
                "error_class": type(exc).__name__,
                "detail": _safe_error_detail(exc),
            }
        ]
    return []
