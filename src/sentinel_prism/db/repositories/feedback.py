"""Persist user feedback on normalized updates (Story 7.1 — FR26, FR27)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from sentinel_prism.db.models import UpdateFeedback, UpdateFeedbackKind


async def insert_feedback(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    normalized_update_id: uuid.UUID,
    run_id: uuid.UUID | None,
    classification_snapshot: dict[str, Any] | None,
    kind: UpdateFeedbackKind,
    comment: str,
) -> UpdateFeedback:
    row = UpdateFeedback(
        id=uuid.uuid4(),
        user_id=user_id,
        normalized_update_id=normalized_update_id,
        run_id=run_id,
        classification_snapshot=classification_snapshot,
        kind=kind,
        comment=comment,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row
