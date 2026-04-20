"""Load mock routing rules for Epic 5 (Story 5.1)."""

from __future__ import annotations

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
