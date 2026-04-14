"""Temporary exemplar routes for RBAC (Story 1.4). Not product API — remove or replace when domain routes exist."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from sentinel_prism.api.deps import get_current_user, require_roles
from sentinel_prism.db.models import User, UserRole

router = APIRouter(prefix="/rbac-demo", tags=["rbac-demo"])


@router.get("/admin-only", summary="Admin role only (403 for analyst/viewer)")
async def admin_only(_user: User = Depends(require_roles(UserRole.ADMIN))) -> dict[str, str]:
    return {"scope": "admin"}


@router.get(
    "/analyst-or-above",
    summary="Analyst or Admin (403 for viewer)",
)
async def analyst_or_above(
    _user: User = Depends(require_roles(UserRole.ANALYST, UserRole.ADMIN)),
) -> dict[str, str]:
    return {"scope": "analyst_or_admin"}


@router.get(
    "/authenticated",
    summary="Any authenticated role (viewer, analyst, admin)",
)
async def any_authenticated(user: User = Depends(get_current_user)) -> dict[str, str]:
    return {"scope": "authenticated", "role": user.role.value}
