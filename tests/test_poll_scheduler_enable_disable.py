"""Scheduler enable/disable behavior (Story 2.5 — AC6 verification)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from sentinel_prism.db.models import FallbackMode, SourceType
from sentinel_prism.workers.poll_scheduler import (
    PollScheduler,
    poll_job_id,
    reset_poll_scheduler_for_tests,
)


def _row(
    *,
    source_id: uuid.UUID | None = None,
    enabled: bool = True,
    schedule: str = "0 * * * *",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=source_id or uuid.uuid4(),
        name="n",
        jurisdiction="j",
        source_type=SourceType.RSS,
        primary_url="https://a.com/f.xml",
        schedule=schedule,
        fallback_url=None,
        fallback_mode=FallbackMode.NONE,
        enabled=enabled,
        extra_metadata=None,
    )


@pytest.fixture
def scheduler() -> PollScheduler:
    """A PollScheduler with an in-memory APScheduler but no DB/cron loop started."""

    reset_poll_scheduler_for_tests()
    sched = PollScheduler()
    sched._scheduler = AsyncIOScheduler()  # type: ignore[attr-defined]
    sched._started = True  # type: ignore[attr-defined]
    yield sched
    # Clean up without touching the event loop (no jobs were actually fired).
    sched._started = False  # type: ignore[attr-defined]
    sched._scheduler = None  # type: ignore[attr-defined]
    reset_poll_scheduler_for_tests()


@pytest.mark.asyncio
async def test_disabling_source_removes_the_scheduled_job(
    scheduler: PollScheduler,
) -> None:
    enabled = _row(enabled=True)
    await scheduler._upsert_job_for_source_row(enabled)
    assert poll_job_id(enabled.id) in scheduler.poll_job_ids()

    disabled = _row(source_id=enabled.id, enabled=False)
    await scheduler._upsert_job_for_source_row(disabled)
    assert poll_job_id(enabled.id) not in scheduler.poll_job_ids()


@pytest.mark.asyncio
async def test_reenabling_source_re_adds_the_scheduled_job(
    scheduler: PollScheduler,
) -> None:
    sid = uuid.uuid4()
    await scheduler._upsert_job_for_source_row(_row(source_id=sid, enabled=False))
    assert poll_job_id(sid) not in scheduler.poll_job_ids()

    await scheduler._upsert_job_for_source_row(_row(source_id=sid, enabled=True))
    assert poll_job_id(sid) in scheduler.poll_job_ids()


@pytest.mark.asyncio
async def test_invalid_cron_on_enabled_source_does_not_register_job(
    scheduler: PollScheduler,
) -> None:
    sid = uuid.uuid4()
    await scheduler._upsert_job_for_source_row(
        _row(source_id=sid, enabled=True, schedule="not-a-cron")
    )
    assert poll_job_id(sid) not in scheduler.poll_job_ids()
