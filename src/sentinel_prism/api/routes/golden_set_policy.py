"""Admin API for golden-set label policy and cadence (Story 7.4 — FR44, FR45)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from sentinel_prism.api.deps import get_current_user, get_db_for_admin
from sentinel_prism.db.models import GoldenSetRefreshCadence, User
from sentinel_prism.db.repositories import golden_set_policy as gsp_repo

router = APIRouter(
    prefix="/admin/golden-set-policy",
    tags=["admin", "golden-set-policy"],
)

MAX_LABEL_POLICY_LENGTH = gsp_repo.MAX_LABEL_POLICY_LENGTH

GOLDEN_SET_POLICY_DESCRIPTION = """
**Golden-set governance (FR44/FR45):** Draft changes do not affect the **active** policy
until an admin **applies**. Each apply increments ``active.version`` and appends
``audit_events`` with action ``golden_set_config_changed`` and sentinel ``run_id``
``GOLDEN_SET_CONFIG_AUDIT_RUN_ID``.

Audit metadata records **SHA-256 first 16 hex chars** and **character lengths** for
prior/new ``label_policy_text`` — not full policy text (NFR12).

**Cadence:** ``quarterly`` is the supported refresh intent in this version; additional
values may be added later. **``refresh_after_major_classification_change``** documents
whether eval refresh is also expected after major model/prompt changes (policy flag).
"""


class ActiveGoldenSetOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(..., ge=1)
    label_policy_text: str = Field(..., min_length=1, max_length=MAX_LABEL_POLICY_LENGTH)
    refresh_cadence: GoldenSetRefreshCadence
    refresh_after_major_classification_change: bool


class DraftGoldenSetOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label_policy_text: str = Field(..., min_length=1, max_length=MAX_LABEL_POLICY_LENGTH)
    refresh_cadence: GoldenSetRefreshCadence
    refresh_after_major_classification_change: bool
    reason: str | None = None


class GoldenSetPolicyStateOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active: ActiveGoldenSetOut
    draft: DraftGoldenSetOut | None = None


class GoldenSetPolicyDraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label_policy_text: str | None = Field(
        None, min_length=1, max_length=MAX_LABEL_POLICY_LENGTH
    )
    refresh_cadence: GoldenSetRefreshCadence | None = None
    refresh_after_major_classification_change: bool | None = None
    reason: str | None = Field(None, max_length=2000)


class GoldenSetPolicyApplyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(
        None,
        max_length=2000,
        description="Optional note recorded in audit metadata (in addition to draft reason).",
    )


class GoldenSetHistoryItemOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    created_at: datetime
    actor_user_id: UUID | None
    actor_email: str | None
    prior_version: int
    new_version: int
    prior_refresh_cadence: str
    new_refresh_cadence: str
    prior_refresh_after_major_classification_change: bool
    new_refresh_after_major_classification_change: bool
    reason: str | None = None


class GoldenSetHistoryOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[GoldenSetHistoryItemOut]


@router.get(
    "",
    response_model=GoldenSetPolicyStateOut,
    summary="Get golden-set policy (admin)",
    description=GOLDEN_SET_POLICY_DESCRIPTION,
)
async def get_golden_set_policy(
    db: Annotated[AsyncSession, Depends(get_db_for_admin)],
) -> GoldenSetPolicyStateOut:
    try:
        active, draft = await gsp_repo.get_state_for_admin(db)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    out_draft = (
        DraftGoldenSetOut(
            label_policy_text=draft.label_policy_text,
            refresh_cadence=GoldenSetRefreshCadence(draft.refresh_cadence),
            refresh_after_major_classification_change=draft.refresh_after_major_classification_change,
            reason=draft.reason,
        )
        if draft is not None
        else None
    )
    return GoldenSetPolicyStateOut(
        active=ActiveGoldenSetOut(
            version=active.version,
            label_policy_text=active.label_policy_text,
            refresh_cadence=GoldenSetRefreshCadence(active.refresh_cadence),
            refresh_after_major_classification_change=active.refresh_after_major_classification_change,
        ),
        draft=out_draft,
    )


@router.get(
    "/history",
    response_model=GoldenSetHistoryOut,
    summary="List golden-set policy apply history (admin)",
    description=GOLDEN_SET_POLICY_DESCRIPTION
    + "\n\nReturns apply events in **oldest-first** order (chronological).",
)
async def get_golden_set_history(
    db: Annotated[AsyncSession, Depends(get_db_for_admin)],
) -> GoldenSetHistoryOut:
    rows = await gsp_repo.list_apply_history(db)
    items = [
        GoldenSetHistoryItemOut(
            id=r.id,
            created_at=r.created_at,
            actor_user_id=r.actor_user_id,
            actor_email=r.actor_email,
            prior_version=r.prior_version,
            new_version=r.new_version,
            prior_refresh_cadence=r.prior_refresh_cadence,
            new_refresh_cadence=r.new_refresh_cadence,
            prior_refresh_after_major_classification_change=r.prior_refresh_after_major,
            new_refresh_after_major_classification_change=r.new_refresh_after_major,
            reason=r.reason,
        )
        for r in rows
    ]
    return GoldenSetHistoryOut(items=items)


@router.put(
    "/draft",
    response_model=GoldenSetPolicyStateOut,
    summary="Save golden-set policy draft (admin)",
    description=GOLDEN_SET_POLICY_DESCRIPTION,
)
async def put_golden_set_policy_draft(
    body: GoldenSetPolicyDraftIn,
    db: Annotated[AsyncSession, Depends(get_db_for_admin)],
) -> GoldenSetPolicyStateOut:
    try:
        await gsp_repo.save_draft(
            db,
            label_policy_text=body.label_policy_text,
            refresh_cadence=body.refresh_cadence.value if body.refresh_cadence else None,
            refresh_after_major_classification_change=body.refresh_after_major_classification_change,
            reason=body.reason,
        )
        await db.commit()
    except LookupError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    try:
        active, draft = await gsp_repo.get_state_for_admin(db)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    assert draft is not None
    return GoldenSetPolicyStateOut(
        active=ActiveGoldenSetOut(
            version=active.version,
            label_policy_text=active.label_policy_text,
            refresh_cadence=GoldenSetRefreshCadence(active.refresh_cadence),
            refresh_after_major_classification_change=active.refresh_after_major_classification_change,
        ),
        draft=DraftGoldenSetOut(
            label_policy_text=draft.label_policy_text,
            refresh_cadence=GoldenSetRefreshCadence(draft.refresh_cadence),
            refresh_after_major_classification_change=draft.refresh_after_major_classification_change,
            reason=draft.reason,
        ),
    )


@router.post(
    "/apply",
    response_model=ActiveGoldenSetOut,
    summary="Apply golden-set policy draft (admin)",
    description=GOLDEN_SET_POLICY_DESCRIPTION,
)
async def post_golden_set_policy_apply(
    db: Annotated[AsyncSession, Depends(get_db_for_admin)],
    admin: Annotated[User, Depends(get_current_user)],
    body: GoldenSetPolicyApplyIn | None = None,
) -> ActiveGoldenSetOut:
    try:
        active = await gsp_repo.apply_draft(
            db,
            actor_user_id=admin.id,
            audit_reason_override=body.reason if body is not None else None,
        )
        await db.commit()
    except LookupError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        await db.rollback()
        if str(exc) == "no_draft_to_apply":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No draft to apply. Save a draft first.",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return ActiveGoldenSetOut(
        version=active.version,
        label_policy_text=active.label_policy_text,
        refresh_cadence=GoldenSetRefreshCadence(active.refresh_cadence),
        refresh_after_major_classification_change=active.refresh_after_major_classification_change,
    )
