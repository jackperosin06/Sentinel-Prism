"""APScheduler interval job for digest queue flush (Story 5.4 — FR22)."""

from __future__ import annotations

import logging
import os

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from sentinel_prism.db.session import get_session_factory
from sentinel_prism.observability import obs_ctx
from sentinel_prism.services.notifications.digest_flush import flush_digest_queue_once
from sentinel_prism.services.notifications.notification_policy import load_notification_policy

logger = logging.getLogger(__name__)

_instance: DigestScheduler | None = None


def get_digest_scheduler() -> DigestScheduler:
    global _instance
    if _instance is None:
        _instance = DigestScheduler()
    return _instance


def reset_digest_scheduler_for_tests() -> None:
    global _instance
    if _instance is not None and _instance._scheduler is not None:
        try:
            _instance._scheduler.shutdown(wait=False)
        except Exception:
            logger.exception("digest_scheduler test reset shutdown failed")
        _instance._scheduler = None
        _instance._started = False
    _instance = None


class DigestScheduler:
    """Runs :func:`flush_digest_queue_once` on a fixed interval."""

    def __init__(self) -> None:
        self._scheduler: AsyncIOScheduler | None = None
        self._started = False

    @property
    def started(self) -> bool:
        return self._started

    async def start(self) -> bool:
        if self._started and self._scheduler is not None:
            return True
        if not os.environ.get("DATABASE_URL", "").strip():
            logger.info("Digest scheduler not started (DATABASE_URL unset)")
            return False
        policy = load_notification_policy()
        if not policy.digest_enabled:
            logger.info("Digest scheduler not started (NOTIFICATIONS_DIGEST_ENABLED=0)")
            return False
        self._scheduler = AsyncIOScheduler()
        try:
            self._scheduler.start()
            self._scheduler.add_job(
                self._flush_job,
                "interval",
                seconds=policy.digest_flush_interval_seconds,
                id="digest_flush",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=min(300, policy.digest_flush_interval_seconds),
            )
        except Exception:
            try:
                self._scheduler.shutdown(wait=False)
            except Exception:
                logger.exception("digest_scheduler startup rollback failed")
            self._scheduler = None
            self._started = False
            raise
        self._started = True
        logger.info(
            "Digest scheduler started",
            extra={
                "event": "digest_scheduler_started",
                "ctx": obs_ctx(interval_seconds=policy.digest_flush_interval_seconds),
            },
        )
        return True

    async def shutdown(self) -> None:
        if not self._started or self._scheduler is None:
            return
        self._scheduler.shutdown(wait=True)
        self._scheduler = None
        self._started = False
        logger.info(
            "Digest scheduler stopped",
            extra={"event": "digest_scheduler_stopped", "ctx": obs_ctx()},
        )

    async def _flush_job(self) -> None:
        try:
            factory = get_session_factory()
            logger.info(
                "digest_flush_job_start",
                extra={"event": "digest_flush_job_start", "ctx": obs_ctx()},
            )
            await flush_digest_queue_once(session_factory=factory)
        except Exception:
            logger.exception("digest_flush_job_failed")
