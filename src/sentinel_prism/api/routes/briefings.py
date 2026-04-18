"""Briefing list and detail APIs (Story 4.3 — FR19)."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from sentinel_prism.api.deps import require_roles
from sentinel_prism.db.models import User, UserRole
from sentinel_prism.db.repositories import briefings as briefings_repo
from sentinel_prism.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/briefings", tags=["briefings"])


class BriefingSectionsOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    what_changed: str
    why_it_matters: str
    who_should_care: str
    # Writer always emits a string (sentinel ``"Confidence not available."`` when
    # no member classifications carry a confidence), so the schema is
    # non-nullable. Clients must not branch on ``null`` (Decision 5, Story 4.3).
    confidence: str
    suggested_actions: str | None = None


class BriefingMemberOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    normalized_update_id: UUID | None = None
    item_url: str
    title: str | None = None
    body_snippet: str | None = None
    jurisdiction: str = ""
    document_type: str = ""
    severity: str | None = None
    confidence: float | None = None
    impact_categories: list[str] = Field(default_factory=list)


class BriefingGroupOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    dimensions: dict[str, str] = Field(default_factory=dict)
    sections: BriefingSectionsOut
    members: list[BriefingMemberOut] = Field(default_factory=list)


class BriefingListItemOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    run_id: UUID
    created_at: datetime
    group_count: int
    summary: str = ""


class BriefingListOut(BaseModel):
    items: list[BriefingListItemOut]


class BriefingDetailOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    run_id: UUID
    created_at: datetime
    grouping_dimensions: list[str]
    groups: list[BriefingGroupOut]


_SEVERITY_RANK: dict[str, int] = {"high": 3, "medium": 2, "low": 1}


def _group_severity_rank(group: dict[str, Any]) -> int:
    """Highest severity rank across a group's members (unknown → 0)."""

    members = group.get("members") if isinstance(group, dict) else None
    if not isinstance(members, list):
        return 0
    best = 0
    for m in members:
        if not isinstance(m, dict):
            continue
        sev = m.get("severity")
        if not isinstance(sev, str):
            continue
        best = max(best, _SEVERITY_RANK.get(sev.lower(), 0))
    return best


def _group_member_count(group: dict[str, Any]) -> int:
    members = group.get("members") if isinstance(group, dict) else None
    return len(members) if isinstance(members, list) else 0


def _summary_from_groups(groups: list[dict[str, Any]]) -> str:
    """Pick the summary from the most-severe group (ties → largest by count).

    Decision 3, Story 4.3: if groups are sorted alphabetically by dimension
    tuple, the "first group" choice is arbitrary — reordering env
    ``BRIEFING_GROUPING_DIMENSIONS`` silently changes the card summary. Rank
    by (max severity within group, member count) descending so the most
    operationally important group drives the card.
    """

    if not groups:
        return ""
    candidates = [g for g in groups if isinstance(g, dict)]
    if not candidates:
        return ""
    best = max(
        candidates,
        key=lambda g: (_group_severity_rank(g), _group_member_count(g)),
    )
    sections = best.get("sections")
    if isinstance(sections, dict):
        w = sections.get("what_changed")
        if isinstance(w, str) and w.strip():
            return w.strip()[:240]
    return ""


@router.get("", response_model=BriefingListOut)
async def list_briefings(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _user: User = Depends(
        require_roles(UserRole.VIEWER, UserRole.ANALYST, UserRole.ADMIN)
    ),
    session=Depends(get_db),
) -> BriefingListOut:
    rows = await briefings_repo.list_briefings(session, limit=limit, offset=offset)
    items: list[BriefingListItemOut] = []
    for r in rows:
        groups = r.groups if isinstance(r.groups, list) else []
        items.append(
            BriefingListItemOut(
                id=r.id,
                run_id=r.run_id,
                created_at=r.created_at,
                group_count=len(groups),
                summary=_summary_from_groups(groups),
            )
        )
    logger.info(
        "list_briefings",
        extra={
            "event": "list_briefings_ok",
            "ctx": {"count": len(items), "limit": limit, "offset": offset},
        },
    )
    return BriefingListOut(items=items)


@router.get("/{briefing_id}", response_model=BriefingDetailOut)
async def get_briefing(
    briefing_id: uuid.UUID,
    _user: User = Depends(
        require_roles(UserRole.VIEWER, UserRole.ANALYST, UserRole.ADMIN)
    ),
    session=Depends(get_db),
) -> BriefingDetailOut:
    row = await briefings_repo.get_briefing_by_id(session, briefing_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Briefing not found",
        )

    dims = row.grouping_dimensions
    if not isinstance(dims, list):
        dims = []
    dim_list = [str(x) for x in dims]

    raw_groups = row.groups if isinstance(row.groups, list) else []
    groups_out: list[BriefingGroupOut] = []
    for g in raw_groups:
        if not isinstance(g, dict):
            continue
        dim_map = g.get("dimensions")
        if not isinstance(dim_map, dict):
            dim_map = {}
        dim_clean = {str(k): str(v) for k, v in dim_map.items()}
        sec_raw = g.get("sections")
        if not isinstance(sec_raw, dict):
            sec_raw = {}
        raw_confidence = sec_raw.get("confidence")
        sections = BriefingSectionsOut(
            what_changed=str(sec_raw.get("what_changed") or ""),
            why_it_matters=str(sec_raw.get("why_it_matters") or ""),
            who_should_care=str(sec_raw.get("who_should_care") or ""),
            # Legacy rows (pre-Decision-5) may have NULL ``confidence`` — coerce
            # to the documented sentinel so the schema's non-null contract
            # holds even for back-compatible reads.
            confidence=str(raw_confidence)
            if raw_confidence is not None
            else "Confidence not available.",
            suggested_actions=sec_raw.get("suggested_actions")
            if sec_raw.get("suggested_actions") is not None
            else None,
        )
        mem_raw = g.get("members")
        members: list[BriefingMemberOut] = []
        if isinstance(mem_raw, list):
            for m in mem_raw:
                if not isinstance(m, dict):
                    continue
                nu = m.get("normalized_update_id")
                nu_uuid = None
                if nu:
                    try:
                        nu_uuid = uuid.UUID(str(nu))
                    except ValueError:
                        nu_uuid = None
                ic = m.get("impact_categories")
                members.append(
                    BriefingMemberOut(
                        normalized_update_id=nu_uuid,
                        item_url=str(m.get("item_url") or ""),
                        title=m.get("title") if m.get("title") is not None else None,
                        body_snippet=m.get("body_snippet")
                        if m.get("body_snippet") is not None
                        else None,
                        jurisdiction=str(m.get("jurisdiction") or ""),
                        document_type=str(m.get("document_type") or ""),
                        severity=m.get("severity"),
                        confidence=m.get("confidence")
                        if isinstance(m.get("confidence"), (int, float))
                        and not isinstance(m.get("confidence"), bool)
                        else None,
                        impact_categories=list(ic)
                        if isinstance(ic, list)
                        else [],
                    )
                )
        groups_out.append(
            BriefingGroupOut(
                dimensions=dim_clean,
                sections=sections,
                members=members,
            )
        )

    return BriefingDetailOut(
        id=row.id,
        run_id=row.run_id,
        created_at=row.created_at,
        grouping_dimensions=dim_list,
        groups=groups_out,
    )
