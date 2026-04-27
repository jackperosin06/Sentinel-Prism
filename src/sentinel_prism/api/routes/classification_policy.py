"""Admin API for governed classification threshold and system prompt (Story 7.3 — FR29)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from sentinel_prism.api.deps import get_current_user, get_db_for_admin
from sentinel_prism.db.models import User
from sentinel_prism.db.repositories import classification_policy as policy_repo

router = APIRouter(
    prefix="/admin/classification-policy",
    tags=["admin", "classification-policy"],
)

MAX_SYSTEM_PROMPT_LENGTH = policy_repo.MAX_SYSTEM_PROMPT_LENGTH

CLASSIFICATION_POLICY_DESCRIPTION = """
**Governed policy (FR29):** Draft changes do not affect pipeline classification until an
admin calls **apply**. Each apply increments ``active.version`` and appends ``audit_events``
with action ``classification_config_changed`` and sentinel ``run_id`` (see architecture notes).

Audit metadata records **SHA-256 first 16 hex chars** and **character lengths** for prior/new
``system_prompt`` — not full prompt text (NFR12).
"""


class ActivePolicyOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(..., ge=1)
    low_confidence_threshold: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Human-review flag uses strict ``confidence < threshold`` (plus severity critical).",
    )
    system_prompt: str = Field(..., min_length=1, max_length=MAX_SYSTEM_PROMPT_LENGTH)


class DraftOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    low_confidence_threshold: float = Field(..., ge=0.0, le=1.0)
    system_prompt: str = Field(..., min_length=1, max_length=MAX_SYSTEM_PROMPT_LENGTH)
    reason: str | None = None


class ClassificationPolicyStateOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active: ActivePolicyOut
    draft: DraftOut | None = None


class ClassificationPolicyDraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    low_confidence_threshold: float | None = Field(None, ge=0.0, le=1.0)
    system_prompt: str | None = Field(
        None, min_length=1, max_length=MAX_SYSTEM_PROMPT_LENGTH
    )
    reason: str | None = Field(None, max_length=2000)


class ClassificationPolicyApplyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(
        None,
        max_length=2000,
        description="Optional note recorded in audit metadata (in addition to draft reason).",
    )


@router.get(
    "",
    response_model=ClassificationPolicyStateOut,
    summary="Get classification policy (admin)",
    description=CLASSIFICATION_POLICY_DESCRIPTION,
)
async def get_classification_policy(
    db: Annotated[AsyncSession, Depends(get_db_for_admin)],
) -> ClassificationPolicyStateOut:
    try:
        active, draft = await policy_repo.get_state_for_admin(db)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    out_draft = (
        DraftOut(
            low_confidence_threshold=draft.low_confidence_threshold,
            system_prompt=draft.system_prompt,
            reason=draft.reason,
        )
        if draft is not None
        else None
    )
    return ClassificationPolicyStateOut(
        active=ActivePolicyOut(
            version=active.version,
            low_confidence_threshold=active.low_confidence_threshold,
            system_prompt=active.system_prompt,
        ),
        draft=out_draft,
    )


@router.put(
    "/draft",
    response_model=ClassificationPolicyStateOut,
    summary="Save classification policy draft (admin)",
    description=CLASSIFICATION_POLICY_DESCRIPTION,
)
async def put_classification_policy_draft(
    body: ClassificationPolicyDraftIn,
    db: Annotated[AsyncSession, Depends(get_db_for_admin)],
) -> ClassificationPolicyStateOut:
    try:
        await policy_repo.save_draft(
            db,
            low_confidence_threshold=body.low_confidence_threshold,
            system_prompt=body.system_prompt,
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
        active, draft = await policy_repo.get_state_for_admin(db)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    assert draft is not None
    return ClassificationPolicyStateOut(
        active=ActivePolicyOut(
            version=active.version,
            low_confidence_threshold=active.low_confidence_threshold,
            system_prompt=active.system_prompt,
        ),
        draft=DraftOut(
            low_confidence_threshold=draft.low_confidence_threshold,
            system_prompt=draft.system_prompt,
            reason=draft.reason,
        ),
    )


@router.post(
    "/apply",
    response_model=ActivePolicyOut,
    summary="Apply draft classification policy (admin)",
    description=CLASSIFICATION_POLICY_DESCRIPTION,
)
async def post_classification_policy_apply(
    db: Annotated[AsyncSession, Depends(get_db_for_admin)],
    admin: Annotated[User, Depends(get_current_user)],
    body: ClassificationPolicyApplyIn | None = None,
) -> ActivePolicyOut:
    try:
        active = await policy_repo.apply_draft(
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
    return ActivePolicyOut(
        version=active.version,
        low_confidence_threshold=active.low_confidence_threshold,
        system_prompt=active.system_prompt,
    )
