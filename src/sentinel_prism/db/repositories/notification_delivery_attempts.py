"""External notification delivery log (Story 5.3 — FR23).

**Idempotency contract (code-review 2026-04-21):**

External sends must be at-most-once per
``(run_id, item_url, channel, recipient_descriptor)``. The orchestrator
achieves this by atomically claiming the key with
:func:`claim_attempt_pending` (``INSERT ... ON CONFLICT DO NOTHING``),
committing the claim, then performing the network send, then
finalizing the row with :func:`finalize_attempt_outcome`. A concurrent
invocation that loses the race sees ``False`` from the claim call and
does not send.

Rows left in ``pending`` indicate a crash between claim and finalize —
operators can inspect and replay. Callers MUST NOT read-then-write for
idempotency (the previous check-then-send-then-insert pattern had a
TOCTOU window that allowed double-sends on concurrent graph retries).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sqlalchemy.ext.asyncio import AsyncSession

from sentinel_prism.db.models import (
    NotificationDeliveryAttempt,
    NotificationDeliveryChannel,
    NotificationDeliveryOutcome,
)


async def claim_attempt_pending(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    item_url: str,
    channel: NotificationDeliveryChannel,
    recipient_descriptor: str,
) -> bool:
    """Insert a ``pending`` row; return ``True`` if *we* created it.

    Uses ``INSERT ... ON CONFLICT DO NOTHING`` on the
    ``uq_notification_delivery_attempts_idempotent`` unique constraint so
    two concurrent callers cannot both observe "row absent" and both
    proceed to send. The caller is expected to ``session.commit()`` this
    write before performing any external I/O so the claim is durable
    before the network call.
    """

    stmt = (
        pg_insert(NotificationDeliveryAttempt)
        .values(
            id=uuid.uuid4(),
            run_id=run_id,
            item_url=item_url,
            channel=channel,
            outcome=NotificationDeliveryOutcome.PENDING,
            recipient_descriptor=recipient_descriptor,
        )
        .on_conflict_do_nothing(
            constraint="uq_notification_delivery_attempts_idempotent"
        )
    )
    result = await session.execute(stmt)
    return (result.rowcount or 0) > 0


async def finalize_attempt_outcome(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    item_url: str,
    channel: NotificationDeliveryChannel,
    recipient_descriptor: str,
    outcome: NotificationDeliveryOutcome,
    error_class: str | None = None,
    detail: str | None = None,
    provider_message_id: str | None = None,
) -> bool:
    """UPDATE the claimed ``pending`` row to its terminal ``outcome``.

    Only transitions rows away from ``pending`` so a peer that already
    finalized (e.g., a delayed retry colliding with our claim, or a
    previous run's row the caller should never have claimed) is not
    silently overwritten. Returns ``True`` when one row was updated.
    """

    stmt = (
        update(NotificationDeliveryAttempt)
        .where(
            NotificationDeliveryAttempt.run_id == run_id,
            NotificationDeliveryAttempt.item_url == item_url,
            NotificationDeliveryAttempt.channel == channel,
            NotificationDeliveryAttempt.recipient_descriptor == recipient_descriptor,
            NotificationDeliveryAttempt.outcome == NotificationDeliveryOutcome.PENDING,
        )
        .values(
            outcome=outcome,
            error_class=error_class,
            detail=detail,
            provider_message_id=provider_message_id,
        )
    )
    result = await session.execute(stmt)
    return (result.rowcount or 0) > 0


async def list_attempts(
    session: AsyncSession,
    *,
    limit: int = 100,
    offset: int = 0,
    outcome: str | None = None,
    run_id: uuid.UUID | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
) -> tuple[list[NotificationDeliveryAttempt], bool]:
    """Return a page of attempts plus ``has_more`` (limit+1 probe).

    ``created_after`` is inclusive (``>=``); ``created_before`` is
    **exclusive** (``<``) so clients paging with
    ``created_before=<last seen created_at>`` do not receive the
    boundary row twice on the next page. Ties at identical
    microseconds break on ``id DESC`` via the composite
    ``ORDER BY``.
    """

    stmt = select(NotificationDeliveryAttempt)
    if outcome is not None:
        stmt = stmt.where(NotificationDeliveryAttempt.outcome == outcome)
    if run_id is not None:
        stmt = stmt.where(NotificationDeliveryAttempt.run_id == run_id)
    if created_after is not None:
        stmt = stmt.where(NotificationDeliveryAttempt.created_at >= created_after)
    if created_before is not None:
        stmt = stmt.where(NotificationDeliveryAttempt.created_at < created_before)
    stmt = (
        stmt.order_by(
            NotificationDeliveryAttempt.created_at.desc(),
            NotificationDeliveryAttempt.id.desc(),
        )
        .limit(limit + 1)
        .offset(offset)
    )
    result = await session.scalars(stmt)
    rows = list(result.all())
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]
    return rows, has_more
