"""AgentState: shared LangGraph state for Epic 3 (Story 3.2).

We use **TypedDict** + :class:`typing.Annotated` reducers (not Pydantic) for the
whole epic so checkpoint serialization stays predictable and merge rules stay
explicit.

**List channels** use :func:`operator.add` for append-only merges across branches
or retries. Callers must pass empty ``list`` values on the first ``invoke`` /
``ainvoke`` for those keys (see :func:`new_pipeline_state`).

**Reference:** Architecture §3.2, Story 3.2 AC #1.
"""

from __future__ import annotations

import operator
import uuid
from typing import Annotated, Any, Literal, NotRequired, TypedDict

PipelineTrigger = Literal["scheduled", "manual"]


IngestionEntry = Literal["default", "post_poll"]


class AgentState(TypedDict):
    """Canonical pipeline state; all orchestration flows through this shape (FR36)."""

    run_id: str
    tenant_id: NotRequired[str]
    source_id: NotRequired[str]
    trigger: NotRequired[PipelineTrigger]
    # ``post_poll`` skips scout+normalize: ingest has already written rows; ``run_id``
    # is set on those rows before ainvoke. Omitted/``default`` runs full graph from scout.
    ingestion_entry: NotRequired[IngestionEntry]
    raw_items: Annotated[list[dict[str, Any]], operator.add]
    normalized_updates: Annotated[list[dict[str, Any]], operator.add]
    classifications: Annotated[list[dict[str, Any]], operator.add]
    routing_decisions: Annotated[list[dict[str, Any]], operator.add]
    briefings: Annotated[list[dict[str, Any]], operator.add]
    delivery_events: Annotated[list[dict[str, Any]], operator.add]
    errors: Annotated[list[dict[str, Any]], operator.add]
    flags: dict[str, bool]
    llm_trace: NotRequired[dict[str, Any]]


def new_pipeline_state(
    run_id: uuid.UUID | str,
    *,
    tenant_id: str | None = None,
    source_id: uuid.UUID | str | None = None,
    trigger: PipelineTrigger | None = None,
) -> AgentState:
    """Build initial state for a new run (empty list channels, empty flags).

    ``run_id`` is normalized to ``str`` at the graph boundary for JSON/checkpoint
    compatibility and alignment with nullable UUID columns on domain rows (Story 3.1).

    ``source_id`` (Story 3.3) identifies the :class:`~sentinel_prism.db.models.Source`
    row for ``scout`` / ``normalize`` when running the regulatory pipeline. The
    stored value is stripped so state / checkpoint and downstream UUID parsing
    agree on a single canonical form.

    ``trigger`` (Story 3.3) carries the run initiation mode through to connector
    logs and any ``trigger``-bucketed metric; omit (or pass ``None``) to let
    ``node_scout`` default to ``"manual"`` — the historical MVP behaviour.

    Raises:
        TypeError: if ``run_id`` is not a :class:`uuid.UUID` or :class:`str`.
        ValueError: if ``run_id`` is an empty or whitespace-only string (an empty
            ``thread_id`` would collide across runs and corrupt checkpoints).
    """

    if isinstance(run_id, uuid.UUID):
        rid = str(run_id)
    elif isinstance(run_id, str):
        if not run_id.strip():
            raise ValueError("run_id must be a non-empty string")
        rid = run_id
    else:
        raise TypeError(
            f"run_id must be uuid.UUID or str, got {type(run_id).__name__}"
        )
    state: AgentState = {
        "run_id": rid,
        "raw_items": [],
        "normalized_updates": [],
        "classifications": [],
        "routing_decisions": [],
        "briefings": [],
        "delivery_events": [],
        "errors": [],
        "flags": {},
    }
    if tenant_id is not None:
        state["tenant_id"] = tenant_id
    if source_id is not None:
        sid_raw = str(source_id) if isinstance(source_id, uuid.UUID) else source_id
        sid = sid_raw.strip()
        if not sid:
            raise ValueError("source_id must be a non-empty string when provided")
        state["source_id"] = sid
    if trigger is not None:
        if trigger not in ("scheduled", "manual"):
            raise ValueError(
                f"trigger must be 'scheduled' or 'manual', got {trigger!r}"
            )
        state["trigger"] = trigger
    return state


def new_post_poll_pipeline_state(
    run_id: uuid.UUID | str,
    *,
    source_id: uuid.UUID | str,
    trigger: PipelineTrigger,
    normalized_updates: list[dict[str, Any]],
) -> AgentState:
    """Initial state for a run that continues from persisted ingest (poll) rows.

    Skips ``scout`` and ``normalize``; :class:`NormalizedUpdateRow` must already
    be committed with ``run_id`` set to the same value as this state's ``run_id``
    (``thread_id``) so ``brief`` can load from the DB.
    """

    base = new_pipeline_state(run_id, source_id=source_id, trigger=trigger)
    return {
        **base,
        "ingestion_entry": "post_poll",
        "normalized_updates": list(normalized_updates),
    }
