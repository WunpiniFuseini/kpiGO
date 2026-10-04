"""Feed cadences: five-field cron expressions, read in the org's reporting timezone."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from celery.schedules import crontab

# A cadence missed for longer than this is simply due now, not replayed minute by minute.
LOOKBACK = timedelta(days=1)


def parse_cron(expression: str) -> crontab:
    fields = expression.split()
    if len(fields) != 5:
        raise ValueError("A cadence is five cron fields: minute hour day month weekday.")
    minute, hour, day_of_month, month, day_of_week = fields
    try:
        return crontab(
            minute=minute,
            hour=hour,
            day_of_month=day_of_month,
            month_of_year=month,
            day_of_week=day_of_week,
        )
    except Exception as exc:
        raise ValueError(f"'{expression}' is not a valid cadence: {exc}") from None


def _matches(cron: crontab, at: datetime) -> bool:
    # Celery expands each field to the set of values it matches; weekday 0 is Sunday.
    expanded: Any = cron
    return (
        at.minute in expanded.minute
        and at.hour in expanded.hour
        and at.day in expanded.day_of_month
        and at.month in expanded.month_of_year
        and at.isoweekday() % 7 in expanded.day_of_week
    )


def is_due(expression: str, last: datetime | None, now: datetime, zone: ZoneInfo) -> bool:
    """Whether a scheduled minute falls after ``last`` and at or before ``now``."""
    cron = parse_cron(expression)
    local_now = now.astimezone(zone).replace(second=0, microsecond=0)
    if last is None or now - last > LOOKBACK:
        start = local_now - LOOKBACK
    else:
        start = last.astimezone(zone).replace(second=0, microsecond=0)
    at = start + timedelta(minutes=1)
    while at <= local_now:
        if _matches(cron, at):
            return True
        at += timedelta(minutes=1)
    return False
