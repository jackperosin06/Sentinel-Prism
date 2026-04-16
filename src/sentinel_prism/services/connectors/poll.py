"""Connector poll entrypoint — stub until Story 2.3 (RSS/HTTP fetch)."""

from __future__ import annotations

import logging
import uuid
from typing import Literal

logger = logging.getLogger(__name__)

PollTrigger = Literal["scheduled", "manual"]


async def execute_poll(
    source_id: uuid.UUID,
    *,
    trigger: PollTrigger,
) -> None:
    """Invoke the Scout connector for ``source_id`` (logs only in Story 2.2)."""

    logger.info(
        "poll_stub",
        extra={
            "source_id": str(source_id),
            "trigger": trigger,
        },
    )
