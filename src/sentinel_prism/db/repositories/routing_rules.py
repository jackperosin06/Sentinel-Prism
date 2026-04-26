"""Load mock routing rules for Epic 5 (Story 5.1); admin CRUD for Story 6.3."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sentinel_prism.db.models import RoutingRule, RoutingRuleType


async def list_topic_rules_ordered(session: AsyncSession) -> list[RoutingRule]:
    """Topic rules: lower ``priority`` first, ``id`` as deterministic tie-break.

    Story 5.1 AC #2 (determinism): two rows with equal ``priority`` must not
    produce a non-deterministic order — the storage engine is free to return
    ties in any sequence. Ordering by ``id`` (the immutable UUID PK) as the
    secondary key keeps resolution identical across runs even when operators
    insert rules at the same priority band.
    """

    result = await session.execute(
        select(RoutingRule)
        .where(RoutingRule.rule_type == RoutingRuleType.TOPIC)
        .order_by(RoutingRule.priority.asc(), RoutingRule.id.asc())
    )
    return list(result.scalars().all())


async def list_severity_rules_ordered(session: AsyncSession) -> list[RoutingRule]:
    """Severity rules: lower ``priority`` first, ``id`` as deterministic tie-break."""

    result = await session.execute(
        select(RoutingRule)
        .where(RoutingRule.rule_type == RoutingRuleType.SEVERITY)
        .order_by(RoutingRule.priority.asc(), RoutingRule.id.asc())
    )
    return list(result.scalars().all())


def normalize_routing_key(value: str, *, field_label: str) -> str:
    """Lowercase + trim; reject empty — matches DB CHECK on routing rule keys."""

    text = value.strip().lower()
    if not text:
        raise ValueError(f"{field_label} must be non-empty after trim")
    return text


def normalize_slug(value: str, *, field_label: str) -> str:
    """Trim team/channel slug; require non-empty for admin saves (Story 6.3)."""

    text = value.strip()
    if not text:
        raise ValueError(f"{field_label} must be non-empty")
    if len(text) > 128:
        raise ValueError(f"{field_label} must be at most 128 characters")
    return text


async def list_rules_admin(
    session: AsyncSession,
    *,
    rule_type: RoutingRuleType | None = None,
) -> list[RoutingRule]:
    """All rules (or one type) for admin UI — same ordering as Story 5.1 lists."""

    q = select(RoutingRule)
    if rule_type is not None:
        q = q.where(RoutingRule.rule_type == rule_type)
    q = q.order_by(RoutingRule.rule_type.asc(), RoutingRule.priority.asc(), RoutingRule.id.asc())
    result = await session.execute(q)
    return list(result.scalars().all())


async def get_rule_by_id(session: AsyncSession, rule_id: uuid.UUID) -> RoutingRule | None:
    return await session.get(RoutingRule, rule_id)


async def create_rule(
    session: AsyncSession,
    *,
    priority: int,
    rule_type: RoutingRuleType,
    impact_category: str | None,
    severity_value: str | None,
    team_slug: str,
    channel_slug: str,
) -> RoutingRule:
    row = RoutingRule(
        priority=priority,
        rule_type=rule_type,
        impact_category=impact_category,
        severity_value=severity_value,
        team_slug=team_slug,
        channel_slug=channel_slug,
    )
    session.add(row)
    await session.flush()
    return row


async def delete_rule(session: AsyncSession, rule: RoutingRule) -> None:
    await session.delete(rule)
    await session.flush()
