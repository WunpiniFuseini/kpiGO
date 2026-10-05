"""Working days and the business date of a timestamp (PRD AD-5, AD-11).

The default working week is Monday to Friday. ``calendar_day`` rows are the
exceptions: holidays, and working weekends. A regional row overrides the
org-wide one for that region.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from django.db.models import Q

from kpigo.periods.models import CalendarDay
from kpigo.platform.config import org_settings, reporting_zone

# Bounds the search for the next working day; a year with no working day is a
# misconfigured calendar, not a long weekend.
_MAX_SCAN_DAYS = 366


def _overrides(org_id: str, first: date, last: date, region_code: str | None) -> dict[date, bool]:
    rows = CalendarDay.objects.filter(org_id=org_id, date__gte=first, date__lte=last).filter(
        Q(region_code__isnull=True) | Q(region_code=region_code)
    )
    found: dict[date, bool] = {}
    # Org-wide first, so a regional row for the same date replaces it.
    for row in sorted(rows, key=lambda r: r.region_code is not None):
        found[row.date] = row.is_working_day
    return found


def is_working_day(org_id: str, day: date, region_code: str | None = None) -> bool:
    override = _overrides(org_id, day, day, region_code).get(day)
    return override if override is not None else day.weekday() < 5


def next_working_day(org_id: str, day: date, region_code: str | None = None) -> date:
    """``day`` itself if it is a working day, else the next one."""
    overrides = _overrides(org_id, day, day + timedelta(days=_MAX_SCAN_DAYS), region_code)
    for offset in range(_MAX_SCAN_DAYS + 1):
        candidate = day + timedelta(days=offset)
        working = overrides.get(candidate, candidate.weekday() < 5)
        if working:
            return candidate
    raise ValueError(f"No working day within a year of {day}; check the calendar.")


def working_days_between(
    org_id: str, first: date, end: date, region_code: str | None = None
) -> int:
    """Working days in ``[first, end)``."""
    if end <= first:
        return 0
    overrides = _overrides(org_id, first, end - timedelta(days=1), region_code)
    days = (end - first).days
    return sum(
        1
        for offset in range(days)
        if overrides.get(
            first + timedelta(days=offset), (first + timedelta(days=offset)).weekday() < 5
        )
    )


def business_date(org_id: str, at: datetime, region_code: str | None = None) -> date:
    """The business day an instant belongs to.

    The instant is read in the reporting timezone; at or after the business-day
    cutoff it belongs to the next day; a non-working day rolls forward to the
    next working day.
    """
    local = at.astimezone(reporting_zone(org_id))
    day = local.date()
    cutoff = org_settings(org_id).business_day_cutoff
    if cutoff is not None and local.time().replace(tzinfo=None) >= cutoff:
        day += timedelta(days=1)
    return next_working_day(org_id, day, region_code)


class WorkingCalendar:
    """Working days over a date range for several regions, read once.

    Pacing asks the same question for thousands of agents a day; this reads the
    calendar's exceptions for ``[first, end)`` in one query and answers from memory.
    A day outside the range falls back to the default working week.
    """

    def __init__(self, org_id: str, first: date, end: date) -> None:
        self._org: dict[date, bool] = {}
        self._regional: dict[str, dict[date, bool]] = {}
        if end > first:
            for row in CalendarDay.objects.filter(
                org_id=org_id, date__gte=first, date__lt=end
            ).values_list("date", "is_working_day", "region_code"):
                day, working, region = row
                if region is None:
                    self._org[day] = working
                else:
                    self._regional.setdefault(region, {})[day] = working

    def is_working(self, day: date, region_code: str | None = None) -> bool:
        regional = self._regional.get(region_code or "", {}) if region_code else {}
        if day in regional:
            return regional[day]
        if day in self._org:
            return self._org[day]
        return day.weekday() < 5

    def working_days(self, first: date, end: date, region_code: str | None = None) -> list[date]:
        """The working days in ``[first, end)``, in order."""
        return [
            first + timedelta(days=i)
            for i in range((end - first).days)
            if self.is_working(first + timedelta(days=i), region_code)
        ]
