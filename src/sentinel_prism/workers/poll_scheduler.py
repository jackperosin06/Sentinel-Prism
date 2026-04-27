"""In-process poll scheduler (Story 2.2 — FR2)."""

from __future__ import annotations

import logging
import os
import uuid

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.ext.asyncio import AsyncSession

from sentinel_prism.observability import obs_ctx
from sentinel_prism.db.models import Source
from sentinel_prism.db.repositories import sources as sources_repo
from sentinel_prism.db.session import get_session_factory

logger = logging.getLogger(__name__)

_instance: PollScheduler | None = None


def poll_job_id(source_id: uuid.UUID) -> str:
    return f"poll:{source_id}"


def get_poll_scheduler() -> PollScheduler:
    global _instance
    if _instance is None:
        _instance = PollScheduler()
    return _instance


def reset_poll_scheduler_for_tests() -> None:
    """Clear singleton (integration / unit tests only)."""

    global _instance
    _instance = None


class PollScheduler:
    """Registers one APScheduler job per enabled source (5-field cron, UTC)."""

    def __init__(self) -> None:
        self._scheduler: AsyncIOScheduler | None = None
        self._started = False

    @property
    def started(self) -> bool:
        return self._started

    async def start(self) -> bool:
        if not os.environ.get("DATABASE_URL", "").strip():
            logger.info("Poll scheduler not started (DATABASE_URL unset)")
            return False
        self._scheduler = AsyncIOScheduler()
        self._scheduler.start()
        try:
            await self.sync_all_sources()
        except BaseException:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
            raise
        self._started = True
        logger.info(
            "Poll scheduler started",
            extra={"event": "poll_scheduler_started", "ctx": obs_ctx()},
        )
        return True

    async def shutdown(self) -> None:
        if not self._started or self._scheduler is None:
            return
        self._scheduler.shutdown(wait=True)
        self._scheduler = None
        self._started = False
        logger.info(
            "Poll scheduler stopped",
            extra={"event": "poll_scheduler_stopped", "ctx": obs_ctx()},
        )

    def poll_job_ids(self) -> frozenset[str]:
        """Registered poll job ids (``poll:<uuid>``) — for tests and operators."""

        if not self._started or self._scheduler is None:
            return frozenset()
        return frozenset(
            j.id for j in self._scheduler.get_jobs() if j.id.startswith("poll:")
        )

    def _remove_job_if_exists(self, job_id: str) -> None:
        if not self._started or self._scheduler is None:
            return
        try:
            self._scheduler.remove_job(job_id)
        except JobLookupError:
            pass

    async def _run_scheduled_poll(self, source_id: uuid.UUID) -> None:
        from sentinel_prism.services.connectors.poll import execute_poll

        factory = get_session_factory()
        async with factory() as session:
            row = await sources_repo.get_source_by_id(session, source_id)
            if row is None or not row.enabled:
                return
        logger.info(
            "poll_scheduled_fire",
            extra={
                "event": "poll_scheduled_fire",
                "ctx": obs_ctx(source_id=str(source_id)),
            },
        )
        await execute_poll(source_id, trigger="scheduled")

    async def _upsert_job_for_source_row(self, row: Source) -> None:
        if self._scheduler is None:
            return
        job_id = poll_job_id(row.id)
        if not row.enabled:
            self._remove_job_if_exists(job_id)
            return
        try:
            trigger = CronTrigger.from_crontab(row.schedule.strip())
        except Exception:
            logger.warning(
                "Skipping poll job for source %s: invalid cron %r",
                row.id,
                row.schedule,
            )
            self._remove_job_if_exists(job_id)
            return
        self._scheduler.add_job(
            self._run_scheduled_poll,
            trigger=trigger,
            id=job_id,
            replace_existing=True,
            args=(row.id,),
        )

    async def sync_all_sources(self) -> None:
        if self._scheduler is None:
            return
        factory = get_session_factory()
        async with factory() as session:
            rows = await sources_repo.list_sources(session)
        seen: set[uuid.UUID] = {r.id for r in rows}
        for row in rows:
            await self._upsert_job_for_source_row(row)
        for job in self._scheduler.get_jobs():
            if not job.id.startswith("poll:"):
                continue
            try:
                uid = uuid.UUID(job.id.removeprefix("poll:"))
            except ValueError:
                continue
            if uid not in seen:
                self._remove_job_if_exists(job.id)

    async def refresh_jobs_for_source(self, session: AsyncSession, source_id: uuid.UUID) -> None:
        if not self._started or self._scheduler is None:
            return
        row = await sources_repo.get_source_by_id(session, source_id)
        if row is None:
            self._remove_job_if_exists(poll_job_id(source_id))
            return
        await self._upsert_job_for_source_row(row)
