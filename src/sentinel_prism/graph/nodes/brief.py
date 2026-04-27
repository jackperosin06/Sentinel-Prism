"""Briefing node — group in-scope classifications into persisted briefings (Story 4.3)."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import groupby
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from sentinel_prism.db.models import NormalizedUpdateRow, PipelineAuditAction
from sentinel_prism.db.repositories import briefings as briefings_repo
from sentinel_prism.db.session import get_session_factory
from sentinel_prism.graph.pipeline_audit import record_pipeline_audit_event
from sentinel_prism.graph.replay_context import in_replay_mode
from sentinel_prism.graph.state import AgentState
from sentinel_prism.observability import obs_ctx
from sentinel_prism.services.briefing.settings import (
    BriefingGroupingSettings,
    load_briefing_grouping_settings,
)

logger = logging.getLogger(__name__)
_NODE_ID = "brief"


@dataclass(frozen=True)
class _NormClsMember:
    norm: dict[str, Any]
    normalized_update_id: uuid.UUID | None
    cls: dict[str, Any]
    effective_dt: datetime | None


def _safe_error_detail(exc: BaseException, *, limit: int = 200) -> str:
    text = str(exc).replace("\r", " ").replace("\n", " ").strip()
    if len(text) > limit:
        text = text[:limit] + "…"
    return text


def _bucket_dt(dt: datetime, granularity: str) -> str:
    # Naive datetimes are treated as already-UTC (project convention). Aware
    # datetimes in any other offset MUST be converted before formatting —
    # otherwise a ``+05:30`` instant representing ``2026-04-17T20:30Z`` buckets
    # as ``2026-04-18``, breaking the "UTC day" grouping semantics.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    if granularity == "month":
        return f"{dt.year:04d}-{dt.month:02d}"
    return f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d}"


def _date_bucket_from_norm_dict(norm: dict[str, Any], granularity: str) -> str:
    iso = norm.get("published_at")
    if isinstance(iso, str) and iso.strip():
        try:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            return _bucket_dt(dt, granularity)
        except ValueError:
            pass
    return "unknown"


def _dim_value(
    dim: str,
    member: _NormClsMember,
    settings: BriefingGroupingSettings,
) -> str:
    norm, cls = member.norm, member.cls
    if dim == "date_bucket":
        if member.effective_dt is not None:
            return _bucket_dt(member.effective_dt, settings.date_bucket_granularity)
        return _date_bucket_from_norm_dict(norm, settings.date_bucket_granularity)
    if dim == "jurisdiction":
        return str(norm.get("jurisdiction") or "")
    if dim == "severity":
        return str(cls.get("severity") or "none")
    if dim == "topic":
        cats = cls.get("impact_categories")
        if isinstance(cats, list) and cats:
            return ",".join(sorted(str(x) for x in cats))
        return str(norm.get("document_type") or "unknown")
    return ""


def _group_key(
    member: _NormClsMember,
    settings: BriefingGroupingSettings,
) -> tuple[str, ...]:
    return tuple(_dim_value(d, member, settings) for d in settings.dimensions)


def _classifications_by_url(state: AgentState) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    raw = state.get("classifications")
    if not isinstance(raw, list):
        return out
    for c in raw:
        if not isinstance(c, dict):
            continue
        u = str(c.get("item_url") or "").strip()
        if u:
            out[u] = c
    return out


def _orm_to_norm_dict(r: NormalizedUpdateRow) -> dict[str, Any]:
    return {
        "source_id": str(r.source_id),
        "source_name": r.source_name,
        "jurisdiction": r.jurisdiction,
        "item_url": r.item_url,
        "title": r.title,
        "published_at": r.published_at.isoformat() if r.published_at else None,
        "document_type": r.document_type,
        "body_snippet": r.body_snippet,
        "summary": r.summary,
    }


async def _load_norm_cls_members(
    run_id: str,
    state: AgentState,
) -> list[_NormClsMember]:
    """Return in-scope members for ``run_id``.

    Decision 1, Story 4.3: the DB is authoritative. When ``normalized_updates``
    rows exist for this run, the filtered-by-scope result is returned verbatim
    (possibly empty). The ``state["normalized_updates"]`` fallback fires only
    when the DB has **no rows at all** for the run — e.g. tests that never
    persisted through the normalize node.
    """

    rid = uuid.UUID(str(run_id).strip())
    by_url = _classifications_by_url(state)
    factory = get_session_factory()
    async with factory() as session:
        res = await session.scalars(
            select(NormalizedUpdateRow)
            .where(NormalizedUpdateRow.run_id == rid)
            .order_by(NormalizedUpdateRow.created_at.asc())
        )
        orm_rows = list(res.all())

    if orm_rows:
        members: list[_NormClsMember] = []
        for r in orm_rows:
            url = str(r.item_url).strip()
            cls = by_url.get(url)
            if not cls or cls.get("in_scope") is False:
                continue
            eff = r.published_at or r.created_at
            members.append(_NormClsMember(_orm_to_norm_dict(r), r.id, cls, eff))
        return members

    norms = state.get("normalized_updates")
    if not isinstance(norms, list):
        return []
    members = []
    for n in norms:
        if not isinstance(n, dict):
            continue
        url = str(n.get("item_url") or "").strip()
        if not url:
            continue
        cls = by_url.get(url)
        if not cls or cls.get("in_scope") is False:
            continue
        members.append(_NormClsMember(dict(n), None, cls, None))
    return members


def _member_payload(m: _NormClsMember) -> dict[str, Any]:
    n, c = m.norm, m.cls
    snippet = n.get("body_snippet")
    snippet_out = str(snippet)[:500] if snippet else None
    cats = c.get("impact_categories")
    return {
        "normalized_update_id": str(m.normalized_update_id)
        if m.normalized_update_id
        else None,
        "item_url": str(n.get("item_url") or ""),
        "title": n.get("title"),
        "body_snippet": snippet_out,
        "jurisdiction": str(n.get("jurisdiction") or ""),
        "document_type": str(n.get("document_type") or ""),
        "severity": c.get("severity"),
        "confidence": c.get("confidence"),
        "impact_categories": list(cats)
        if isinstance(cats, list)
        else [],
    }


_SECTION_SEVERITY_RANK: dict[str, int] = {"high": 3, "medium": 2, "low": 1}


def _rationale_rank(m: _NormClsMember) -> tuple[int, float, int]:
    """Sort key for picking the best rationale: severity → confidence → length.

    Decision 2, Story 4.3: replace the previous ``max(..., key=len)`` which
    could surface a verbose-but-irrelevant rationale over a decisive one.
    """

    c = m.cls
    sev = c.get("severity")
    sev_rank = _SECTION_SEVERITY_RANK.get(
        str(sev).lower() if isinstance(sev, str) else "",
        0,
    )
    conf_raw = c.get("confidence")
    if isinstance(conf_raw, (int, float)) and not isinstance(conf_raw, bool):
        conf = float(conf_raw)
    else:
        conf = 0.0
    rationale = c.get("rationale")
    length = len(rationale) if isinstance(rationale, str) else 0
    return sev_rank, conf, length


def _build_sections(members: list[_NormClsMember]) -> dict[str, str]:
    parts_what: list[str] = []
    categories: set[str] = set()
    severities: set[str] = set()
    confidences: list[float] = []
    urgencies: list[str] = []

    for m in members:
        n, c = m.norm, m.cls
        title = n.get("title") or n.get("item_url") or "Update"
        dt = n.get("document_type") or ""
        jur = n.get("jurisdiction") or ""
        parts_what.append(f"• {title} ({dt}, {jur})")
        cats = c.get("impact_categories")
        if isinstance(cats, list):
            categories.update(str(x) for x in cats)
        sev = c.get("severity")
        if sev:
            severities.add(str(sev))
        u = c.get("urgency")
        if u:
            urgencies.append(str(u))
        conf = c.get("confidence")
        if isinstance(conf, (int, float)) and not isinstance(conf, bool):
            confidences.append(float(conf))

    # Cap ``what_changed`` to match the ``why_it_matters`` 8000-char bound so
    # mega-groups cannot blow up the persisted JSONB / API response (Story
    # 4.3 review finding).
    what_changed = "\n".join(parts_what)[:8000]

    top = max(members, key=_rationale_rank, default=None)
    if top is not None and isinstance(top.cls.get("rationale"), str) and top.cls["rationale"]:
        why_it_matters = str(top.cls["rationale"])
    else:
        why_it_matters = "No additional rationale recorded."
    if categories:
        who = f"Teams monitoring {', '.join(sorted(categories))} impacts"
        if severities:
            who += f" (severity: {', '.join(sorted(severities))})"
    elif severities:
        who = f"Teams monitoring {', '.join(sorted(severities))} severity items"
    else:
        who = "Regulatory operations and impacted therapeutic area leads"

    if confidences:
        lo, hi = min(confidences), max(confidences)
        if lo == hi:
            conf_text = f"Model confidence {lo:.2f} (0–1 scale)."
        else:
            conf_text = (
                f"Model confidence between {lo:.2f} and {hi:.2f} (0–1 scale)."
            )
    else:
        conf_text = "Confidence not available."

    if any(u == "immediate" for u in urgencies):
        actions = (
            "Treat as time-sensitive: assign an owner and confirm applicability "
            "to your portfolio within the same business day."
        )
    elif any(u == "time_bound" for u in urgencies):
        actions = (
            "Review against internal deadlines and route to the accountable function."
        )
    else:
        actions = (
            "Acknowledge in the explorer and file under your jurisdiction's "
            "watch list if relevant."
        )

    return {
        "what_changed": what_changed,
        "why_it_matters": why_it_matters[:8000],
        "who_should_care": who,
        "confidence": conf_text,
        "suggested_actions": actions,
    }


def _build_groups_json(
    members: Iterable[_NormClsMember],
    settings: BriefingGroupingSettings,
) -> list[dict[str, Any]]:
    members_list = list(members)
    if not members_list:
        return []

    def sort_key(m: _NormClsMember) -> tuple[str, ...]:
        return _group_key(m, settings)

    sorted_ms = sorted(members_list, key=sort_key)
    groups_out: list[dict[str, Any]] = []
    for _, grp in groupby(sorted_ms, key=sort_key):
        group_members = list(grp)
        first = group_members[0]
        labels = {
            d: _dim_value(d, first, settings) for d in settings.dimensions
        }
        sections = _build_sections(group_members)
        mem_payloads = [_member_payload(m) for m in group_members]
        groups_out.append(
            {
                "dimensions": labels,
                "sections": sections,
                "members": mem_payloads,
            }
        )
    return groups_out


async def node_brief(state: AgentState) -> dict[str, Any]:
    run_id_raw = state.get("run_id") or ""
    run_id = str(run_id_raw).strip()
    ctx: dict[str, Any] = obs_ctx(node_id=_NODE_ID, run_id=run_id)
    if not run_id:
        return {
            "errors": [
                {
                    "step": "brief",
                    "message": "missing_run_id",
                    "error_class": "ValueError",
                    "detail": "empty",
                }
            ]
        }

    try:
        settings = load_briefing_grouping_settings()
    except ValueError as exc:
        logger.warning(
            "graph_brief",
            extra={
                "event": "briefing_config_invalid",
                "ctx": {**ctx, "error_class": type(exc).__name__},
            },
        )
        return {
            "errors": [
                {
                    "step": "brief",
                    "message": "briefing_config_invalid",
                    "error_class": type(exc).__name__,
                    "detail": _safe_error_detail(exc),
                }
            ]
        }

    # ``_load_norm_cls_members`` normalizes ``run_id`` via ``uuid.UUID`` (raises
    # ``ValueError`` on malformed input) and runs a DB query that can raise
    # ``SQLAlchemyError`` on transient outages. Both must surface as a
    # structured brief-step error row rather than crashing the graph.
    try:
        members = await _load_norm_cls_members(run_id, state)
    except ValueError as exc:
        logger.warning(
            "graph_brief",
            extra={
                "event": "briefing_invalid_run_id",
                "ctx": {**ctx, "error_class": type(exc).__name__},
            },
        )
        return {
            "errors": [
                {
                    "step": "brief",
                    "message": "briefing_invalid_run_id",
                    "error_class": type(exc).__name__,
                    "detail": _safe_error_detail(exc),
                }
            ]
        }
    except SQLAlchemyError as exc:
        logger.warning(
            "graph_brief",
            extra={
                "event": "briefing_load_failed",
                "ctx": {**ctx, "error_class": type(exc).__name__},
            },
        )
        return {
            "errors": [
                {
                    "step": "brief",
                    "message": "briefing_load_failed",
                    "error_class": type(exc).__name__,
                    "detail": _safe_error_detail(exc),
                }
            ]
        }

    if not members:
        logger.info(
            "graph_brief",
            extra={
                "event": "graph_brief_skipped",
                "ctx": {**ctx, "reason": "no_in_scope_members"},
            },
        )
        return {}

    groups_out = _build_groups_json(members, settings)
    if not groups_out:
        return {}

    sid_raw = state.get("source_id")
    src_uuid: uuid.UUID | None = None
    if sid_raw is not None:
        try:
            src_uuid = uuid.UUID(str(sid_raw).strip())
        except (ValueError, TypeError, AttributeError):
            src_uuid = None
    if src_uuid is not None:
        ctx = {**ctx, "source_id": str(src_uuid)}

    if in_replay_mode():
        # Replay is non-destructive — do not persist briefings or emit audits.
        bid = uuid.uuid4()
        created = False
    else:
        # Narrow to ``SQLAlchemyError`` so programming errors (TypeError, KeyError,
        # etc.) surface at ERROR with full traceback rather than being buried under
        # the generic ``briefing_persist_failed`` warning alongside transient DB
        # errors.
        try:
            factory = get_session_factory()
            async with factory() as session:
                bid, created = await briefings_repo.upsert_briefing_for_run(
                    session,
                    run_id=run_id,
                    source_id=src_uuid,
                    grouping_dimensions=list(settings.dimensions),
                    groups=groups_out,
                )
                await session.commit()
        except SQLAlchemyError as exc:
            logger.warning(
                "graph_brief",
                extra={
                    "event": "briefing_persist_failed",
                    "ctx": {
                        **ctx,
                        "error_class": type(exc).__name__,
                    },
                },
            )
            return {
                "errors": [
                    {
                        "step": "brief",
                        "message": "briefing_persist_failed",
                        "error_class": type(exc).__name__,
                        "detail": _safe_error_detail(exc),
                    }
                ]
            }
        except Exception as exc:
            logger.error(
                "graph_brief",
                extra={
                    "event": "briefing_persist_unexpected",
                    "ctx": {
                        **ctx,
                        "error_class": type(exc).__name__,
                    },
                },
                exc_info=True,
            )
            return {
                "errors": [
                    {
                        "step": "brief",
                        "message": "briefing_persist_unexpected",
                        "error_class": type(exc).__name__,
                        "detail": _safe_error_detail(exc),
                    }
                ]
            }

    # Decision 4, Story 4.3: emit ``BRIEFING_GENERATED`` exactly once per run.
    # On conflict-update (``created is False``) skip the audit write so
    # operators relying on "1 audit row = 1 authored briefing" stay correct.
    audit_errs: list[dict[str, Any]] = []
    if created and not in_replay_mode():
        audit_errs = await record_pipeline_audit_event(
            run_id=run_id,
            action=PipelineAuditAction.BRIEFING_GENERATED,
            source_id=src_uuid,
            metadata={
                "briefing_id": str(bid),
                "group_count": len(groups_out),
            },
        )

    logger.info(
        "graph_brief",
        extra={
            "event": "graph_brief_done",
            "ctx": {
                **ctx,
                "briefing_id": str(bid),
                "group_count": len(groups_out),
                "briefing_created": created,
            },
        },
    )

    row: dict[str, Any] = {
        "run_id": run_id,
        "briefing_id": str(bid),
        "group_count": len(groups_out),
    }
    if in_replay_mode():
        row["groups"] = groups_out
    out: dict[str, Any] = {"briefings": [row]}
    if audit_errs:
        out["errors"] = audit_errs
    return out
