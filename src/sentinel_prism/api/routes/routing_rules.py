"""Admin API for mock routing / escalation rules (Story 6.3 — FR32, FR33).

**Routing vs escalation:** Both are stored in ``routing_rules``. *Topic* rows
map ``impact_category`` → ``team_slug`` / ``channel_slug``. *Severity* rows
(escalation) map ``severity_value`` → channel (and team backfill per
``services.routing.resolve``).

**Audit:** Mutations append ``audit_events`` with action
``routing_config_changed`` and a fixed sentinel ``run_id`` —
:data:`~sentinel_prism.db.audit_constants.ROUTING_CONFIG_AUDIT_RUN_ID` — so
config history is queryable without a pipeline ``run_id``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from sentinel_prism.api.deps import get_current_user, get_db_for_admin
from sentinel_prism.db.models import RoutingRuleType, User
from sentinel_prism.db.repositories import audit_events as audit_repo
from sentinel_prism.db.repositories import routing_rules as rules_repo

router = APIRouter(prefix="/admin/routing-rules", tags=["admin", "routing"])

ROUTING_RULES_DESCRIPTION = """
Admin-only CRUD for the persisted `routing_rules` mock table.

The UI manages two rule kinds:
- `topic`: `impact_category` -> `team_slug` / `channel_slug`
- `severity`: escalation rules, `severity_value` -> `channel_slug` with team backfill per
  the routing resolver

Mutating calls append `audit_events` with action `routing_config_changed` and the fixed
`ROUTING_CONFIG_AUDIT_RUN_ID` sentinel because configuration changes are not pipeline runs.
"""

MIN_POSTGRES_INT = -(2**31)
MAX_POSTGRES_INT = 2**31 - 1


class RoutingRuleOut(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: uuid.UUID
    priority: int
    rule_type: RoutingRuleType
    impact_category: str | None
    severity_value: str | None
    team_slug: str
    channel_slug: str
    created_at: datetime


class RoutingRuleListOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[RoutingRuleOut]


class RoutingRuleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_type: RoutingRuleType = Field(
        ...,
        description="`topic` uses impact_category; `severity` is escalation via severity_value.",
    )
    priority: int = Field(
        ...,
        ge=MIN_POSTGRES_INT,
        le=MAX_POSTGRES_INT,
        description="Lower values are evaluated first; stored as PostgreSQL INTEGER.",
    )
    impact_category: str | None = Field(
        None,
        max_length=128,
        description="Required for topic rules; normalized to trimmed lowercase.",
    )
    severity_value: str | None = Field(
        None,
        max_length=32,
        description="Required for severity/escalation rules; normalized to trimmed lowercase.",
    )
    team_slug: str = Field(..., min_length=1, max_length=128)
    channel_slug: str = Field(..., min_length=1, max_length=128)

    @model_validator(mode="after")
    def xor_keys(self) -> RoutingRuleCreate:
        if self.rule_type == RoutingRuleType.TOPIC:
            if self.impact_category is None or not str(self.impact_category).strip():
                raise ValueError("impact_category is required for topic rules")
            if self.severity_value is not None and str(self.severity_value).strip():
                raise ValueError("severity_value must be omitted for topic rules")
        else:
            if self.severity_value is None or not str(self.severity_value).strip():
                raise ValueError("severity_value is required for severity (escalation) rules")
            if self.impact_category is not None and str(self.impact_category).strip():
                raise ValueError("impact_category must be omitted for severity rules")
        return self


class RoutingRulePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    priority: int | None = Field(None, ge=MIN_POSTGRES_INT, le=MAX_POSTGRES_INT)
    impact_category: str | None = Field(None, max_length=128)
    severity_value: str | None = Field(None, max_length=32)
    team_slug: str | None = Field(None, min_length=1, max_length=128)
    channel_slug: str | None = Field(None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def at_least_one(self) -> RoutingRulePatch:
        if self.model_fields_set.isdisjoint(
            {"priority", "impact_category", "severity_value", "team_slug", "channel_slug"}
        ):
            raise ValueError("at least one field must be provided")
        return self


def _http422(msg: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=msg)


@router.get(
    "",
    response_model=RoutingRuleListOut,
    summary="List routing rules (admin)",
    description=ROUTING_RULES_DESCRIPTION,
)
async def list_routing_rules(
    db: Annotated[AsyncSession, Depends(get_db_for_admin)],
    _admin: Annotated[User, Depends(get_current_user)],
    rule_type: Annotated[
        RoutingRuleType | None,
        Query(description="Filter by topic or severity; omit for all."),
    ] = None,
) -> RoutingRuleListOut:
    rows = await rules_repo.list_rules_admin(db, rule_type=rule_type)
    return RoutingRuleListOut(items=[RoutingRuleOut.model_validate(r) for r in rows])


@router.post(
    "",
    response_model=RoutingRuleOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create routing rule (admin)",
    description=ROUTING_RULES_DESCRIPTION,
)
async def create_routing_rule(
    body: RoutingRuleCreate,
    db: Annotated[AsyncSession, Depends(get_db_for_admin)],
    admin: Annotated[User, Depends(get_current_user)],
) -> RoutingRuleOut:
    try:
        team = rules_repo.normalize_slug(body.team_slug, field_label="team_slug")
        channel = rules_repo.normalize_slug(body.channel_slug, field_label="channel_slug")
        if body.rule_type == RoutingRuleType.TOPIC:
            ic = rules_repo.normalize_routing_key(
                body.impact_category or "", field_label="impact_category"
            )
            sv = None
        else:
            ic = None
            sv = rules_repo.normalize_routing_key(
                body.severity_value or "", field_label="severity_value"
            )
    except ValueError as exc:
        raise _http422(str(exc)) from exc

    row = await rules_repo.create_rule(
        db,
        priority=body.priority,
        rule_type=body.rule_type,
        impact_category=ic,
        severity_value=sv,
        team_slug=team,
        channel_slug=channel,
    )
    await audit_repo.append_routing_config_audit(
        db,
        actor_user_id=admin.id,
        op="create",
        rule_id=row.id,
        rule_type=row.rule_type.value,
    )
    await db.commit()
    await db.refresh(row)
    return RoutingRuleOut.model_validate(row)


@router.patch(
    "/{rule_id}",
    response_model=RoutingRuleOut,
    summary="Update routing rule (admin)",
    description=ROUTING_RULES_DESCRIPTION,
)
async def patch_routing_rule(
    rule_id: uuid.UUID,
    body: RoutingRulePatch,
    db: Annotated[AsyncSession, Depends(get_db_for_admin)],
    admin: Annotated[User, Depends(get_current_user)],
) -> RoutingRuleOut:
    row = await rules_repo.get_rule_by_id(db, rule_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")

    if row.rule_type == RoutingRuleType.TOPIC and body.severity_value is not None:
        raise _http422("cannot set severity_value on a topic rule")
    if row.rule_type == RoutingRuleType.SEVERITY and body.impact_category is not None:
        raise _http422("cannot set impact_category on a severity rule")

    patch_dump = body.model_dump(exclude_unset=True)
    before = {
        "priority": row.priority,
        "impact_category": row.impact_category,
        "severity_value": row.severity_value,
        "team_slug": row.team_slug,
        "channel_slug": row.channel_slug,
    }

    try:
        if "priority" in patch_dump:
            row.priority = patch_dump["priority"]
        if "team_slug" in patch_dump:
            row.team_slug = rules_repo.normalize_slug(
                patch_dump["team_slug"], field_label="team_slug"
            )
        if "channel_slug" in patch_dump:
            row.channel_slug = rules_repo.normalize_slug(
                patch_dump["channel_slug"], field_label="channel_slug"
            )
        if "impact_category" in patch_dump:
            row.impact_category = rules_repo.normalize_routing_key(
                patch_dump["impact_category"] or "", field_label="impact_category"
            )
        if "severity_value" in patch_dump:
            row.severity_value = rules_repo.normalize_routing_key(
                patch_dump["severity_value"] or "", field_label="severity_value"
            )
    except ValueError as exc:
        raise _http422(str(exc)) from exc

    await audit_repo.append_routing_config_audit(
        db,
        actor_user_id=admin.id,
        op="update",
        rule_id=row.id,
        rule_type=row.rule_type.value,
        metadata={"before": before},
    )
    await db.commit()
    await db.refresh(row)
    return RoutingRuleOut.model_validate(row)


@router.delete(
    "/{rule_id}",
    summary="Delete routing rule (admin)",
    description=ROUTING_RULES_DESCRIPTION,
)
async def delete_routing_rule(
    rule_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db_for_admin)],
    admin: Annotated[User, Depends(get_current_user)],
) -> Response:
    row = await rules_repo.get_rule_by_id(db, rule_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")
    rt = row.rule_type.value
    rid = row.id
    await rules_repo.delete_rule(db, row)
    await audit_repo.append_routing_config_audit(
        db,
        actor_user_id=admin.id,
        op="delete",
        rule_id=rid,
        rule_type=rt,
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
