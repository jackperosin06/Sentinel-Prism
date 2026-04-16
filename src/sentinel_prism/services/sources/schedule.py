"""Cron schedule validation for source polling (Story 2.2)."""

from __future__ import annotations

from apscheduler.triggers.cron import CronTrigger

SCHEDULE_FIELD_DESCRIPTION = (
    "Poll schedule as **five-field cron** (minute hour day-of-month month day-of-week), "
    "UTC — e.g. `0 * * * *` for hourly. APScheduler / standard Unix-style fields."
)


def validate_cron_expression(schedule: str) -> str:
    """Return stripped schedule if it is a valid 5-field crontab; raise ``ValueError`` otherwise."""

    s = schedule.strip()
    if not s:
        raise ValueError("schedule must be non-empty")
    try:
        CronTrigger.from_crontab(s)
    except Exception as e:
        raise ValueError(f"Invalid cron expression: {e}") from e
    return s
