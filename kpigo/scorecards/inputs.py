"""Manual metric input: who owes what, by when, and how it lands (TDD §5.5).

A manual-input metric is collected from people, not a feed. What differs is the
collection workflow, not the storage: a submitted value conforms to
``fact_actual_monthly`` for every member of the slice, and from there the
engine, the missing-data rule and period close treat it like any actual.

The deadline is a working day of the following month on the business calendar
(MI-7). Before it, values are freely editable; from it, they are locked, and a
correction is a restatement: a new version, made only while the month is being
restated (MI-5).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from kpigo.access.models import AppUser
from kpigo.hierarchy.models import Assignment, ReportingEdge
from kpigo.ingestion.models import FactActualMonthly
from kpigo.ingestion.validator import VALUE_BOUND, VALUE_PLACES
from kpigo.periods.business import is_working_day
from kpigo.platform.config import reporting_zone
from kpigo.scorecards import roster
from kpigo.scorecards.config import settings_for
from kpigo.scorecards.cycles import last_day, shift
from kpigo.scorecards.models import InputAssignment, InputSubmission
from kpigo.scorecards.scoring import dimension_keys

# Bounds the working-day scan: a month with no working days is a broken calendar.
_SCAN = 62


def _nth_working_day(org_id: str, first: date, n: int) -> date:
    seen = 0
    for offset in range(_SCAN):
        day = first + timedelta(days=offset)
        if is_working_day(org_id, day):
            seen += 1
            if seen == n:
                return day
    raise ValueError(f"Fewer than {n} working days after {first}; check the business calendar.")


def due_day(org_id: str, period_key: str) -> date:
    """The working day of the following month an input is due on (end of that day)."""
    following = shift(period_key, 1)
    first = date(int(following[:4]), int(following[4:]), 1)
    return _nth_working_day(org_id, first, settings_for(org_id).input_due_working_day)


def due_at(org_id: str, period_key: str) -> datetime:
    """The moment inputs lock: the start of the day after the due day, in reporting time."""
    zone = reporting_zone(org_id)
    return datetime.combine(due_day(org_id, period_key) + timedelta(days=1), time(), tzinfo=zone)


def working_days_from(org_id: str, day: date, n: int) -> date:
    """The working day ``n`` working days after ``day`` (before it when negative)."""
    step = 1 if n > 0 else -1
    left = abs(n)
    for _ in range(_SCAN * 2):
        if left == 0:
            return day
        day += timedelta(days=step)
        if is_working_day(org_id, day):
            left -= 1
    raise ValueError(f"No working day {n} working days from {day}; check the business calendar.")


def remind_on(org_id: str, period_key: str) -> date:
    """The working day the contributor is reminded: N working days before the due day."""
    back = settings_for(org_id).input_reminder_working_days
    return working_days_from(org_id, due_day(org_id, period_key), -back)


def ladder(org_id: str, period_key: str) -> dict[int, date | None]:
    """The day each rung of the escalation ladder is due for a month (MI-8).

    1 reminds the contributor, 2 tells their line manager, 3 tells the
    stakeholders; a rung switched off is None.
    """
    s = settings_for(org_id)
    due = due_day(org_id, period_key)
    out: dict[int, date | None] = {1: remind_on(org_id, period_key)}
    for step, offset in ((2, s.input_manager_working_days), (3, s.input_stakeholder_working_days)):
        out[step] = None if offset is None else working_days_from(org_id, due, offset)
    return out


def locked(org_id: str, period_key: str, now: datetime | None = None) -> bool:
    return (now or timezone.now()) >= due_at(org_id, period_key)


def check_value(value: Decimal) -> str | None:
    """The ingestion domain gate on one value (same bound and precision as a feed)."""
    if abs(value) >= VALUE_BOUND:
        return "The value is outside the storable range (|value| < 10^14)."
    exponent = value.as_tuple().exponent
    if isinstance(exponent, int) and -exponent > VALUE_PLACES:
        return f"Use at most {VALUE_PLACES} decimal places."
    return None


# ── who is in a slice, and who owes it ──────────────────────────────────────


def in_force_on(day: date) -> Q:
    return roster.in_force(day)


def assignments_in_force(org_id: str, period_key: str) -> list[InputAssignment]:
    return list(
        InputAssignment.objects.filter(org_id=org_id)
        .filter(in_force_on(last_day(period_key)))
        .select_related("assignee_user")
        .order_by("metric_code", "scope_type", "scope_code")
    )


def members(org_id: str, period_key: str, scope_type: str, scope_code: str) -> list[Assignment]:
    """The people a slice's value lands on: hierarchy assignments in force at month end."""
    found = roster.assignments_in_force(org_id, period_key).select_related("subject")
    if scope_type == "subject":
        return list(found.filter(subject_id=scope_code))
    if scope_type == "profile":
        return list(found.filter(profile_code=scope_code).order_by("subject__staff_no"))
    return [a for a in found.order_by("subject__staff_no") if scope_code in dimension_keys(a)]


def solid_manager_subject(org_id: str, subject_id: str, period_key: str) -> str | None:
    edge = (
        ReportingEdge.objects.filter(
            org_id=org_id, subject_id=subject_id, relationship_type="solid"
        )
        .filter(in_force_on(last_day(period_key)))
        .first()
    )
    return str(edge.manager_id) if edge else None


def contributor(ia: InputAssignment, period_key: str) -> AppUser | None:
    """The account that owes this slice for the month, resolved now."""
    if ia.assignee_type == "user":
        return ia.assignee_user
    manager = solid_manager_subject(str(ia.org_id), ia.scope_code, period_key)
    if manager is None:
        return None
    return AppUser.objects.filter(org_id=ia.org_id, subject_id=manager, status="active").first()


def line_manager_of(account: AppUser, day: date) -> AppUser | None:
    """The account of whoever this account's subject reports to (solid line) on ``day``.

    Resolved against the hierarchy as it stands when the ladder runs, so a
    manager who changed mid-month is the one told. A contributor who is not a
    tracked subject (MI-13) has no line manager.
    """
    if account.subject_id is None:
        return None
    edge = (
        ReportingEdge.objects.filter(
            org_id=account.org_id, subject_id=account.subject_id, relationship_type="solid"
        )
        .filter(in_force_on(day))
        .first()
    )
    if edge is None:
        return None
    return AppUser.objects.filter(
        org_id=account.org_id, subject_id=edge.manager_id, status="active"
    ).first()


def stakeholders(ia: InputAssignment, managers: list[AppUser]) -> list[AppUser]:
    """Who the last rung tells for a slice: its own list, else the org's, else ``managers``."""
    org_id = str(ia.org_id)
    ids = (ia.escalation_config or {}).get("stakeholder_user_ids") or list(
        settings_for(org_id).input_stakeholders
    )
    if ids:
        found = list(
            AppUser.objects.filter(org_id=org_id, user_id__in=ids, status="active").order_by(
                "display_name"
            )
        )
        if found:
            return found
    return managers


def owed_by(account: AppUser, period_key: str) -> list[InputAssignment]:
    """The slices this account enters for the month: named, or as line manager."""
    org_id = str(account.org_id)
    rows = assignments_in_force(org_id, period_key)
    reports: set[str] = set()
    if account.subject_id:
        reports = {
            str(s)
            for s in ReportingEdge.objects.filter(
                org_id=org_id, manager_id=account.subject_id, relationship_type="solid"
            )
            .filter(in_force_on(last_day(period_key)))
            .values_list("subject_id", flat=True)
        }
    return [
        ia
        for ia in rows
        if (ia.assignee_type == "user" and ia.assignee_user_id == account.user_id)
        or (ia.assignee_type == "role_relative" and ia.scope_code in reports)
    ]


def current(
    org_id: str, period_key: str, slices: Iterable[InputAssignment]
) -> dict[str, InputSubmission]:
    """The current submission per input assignment id, for the month."""
    keys = [(ia.metric_code, ia.scope_type, ia.scope_code) for ia in slices]
    if not keys:
        return {}
    q = Q()
    for code, scope_type, scope_code in keys:
        q |= Q(metric_code=code, scope_type=scope_type, scope_code=scope_code)
    by_key = {
        (s.metric_code, s.scope_type, s.scope_code): s
        for s in InputSubmission.objects.filter(
            q, org_id=org_id, period_key=period_key, is_current=True
        )
    }
    out: dict[str, InputSubmission] = {}
    for ia in slices:
        found = by_key.get((ia.metric_code, ia.scope_type, ia.scope_code))
        if found is not None:
            out[str(ia.assignment_id)] = found
    return out


def conform(submission: InputSubmission, now: datetime) -> int:
    """Write the value as each slice member's actual; returns how many rows landed."""
    org_id = str(submission.org_id)
    rows = members(org_id, submission.period_key, submission.scope_type, submission.scope_code)
    assert submission.value is not None
    for a in rows:
        FactActualMonthly.objects.update_or_create(
            metric=submission.metric,
            subject_id=a.subject_id,
            period_key=submission.period_key,
            defaults={
                "org_id": org_id,
                "assignment": a,
                "actual_value": submission.value,
                "currency_code": None,
                "run_id": None,
                "loaded_at": now,
            },
        )
    return len(rows)


@dataclass(frozen=True)
class Provenance:
    contributor: str | None
    submitted_at: datetime
    note: str
    version: int


def provenance(
    org_id: str,
    period_key: str,
    subject_id: str,
    metric_codes: Iterable[str],
    as_of: datetime | None,
) -> dict[str, Provenance]:
    """Who entered each manual value landing on a subject (MI-11), as of a moment."""
    codes = list(metric_codes)
    if not codes:
        return {}
    a = roster.member_of(org_id, subject_id, period_key)
    if a is None:
        return {}
    assignment = Assignment.objects.get(assignment_id=a.assignment_id)
    slices = Q(scope_type="subject", scope_code=subject_id) | Q(
        scope_type="profile", scope_code=a.profile_code
    )
    dims = dimension_keys(assignment)
    if dims:
        slices |= Q(scope_type="dimension", scope_code__in=sorted(dims))
    rows = InputSubmission.objects.filter(
        slices, org_id=org_id, period_key=period_key, metric_code__in=codes
    ).exclude(state="draft")
    if as_of is not None:
        rows = rows.filter(submitted_at__lte=as_of)
    latest: dict[str, InputSubmission] = {}
    for s in rows.order_by("submitted_at"):
        latest[s.metric_code] = s
    names = dict(
        AppUser.objects.filter(
            org_id=org_id, auth_user_id__in={s.submitted_by for s in latest.values()}
        ).values_list("auth_user_id", "display_name")
    )
    return {
        code: Provenance(
            contributor=names.get(s.submitted_by) if s.submitted_by else None,
            submitted_at=s.submitted_at,  # type: ignore[arg-type]
            note=s.note,
            version=s.version,
        )
        for code, s in latest.items()
    }


def unsubmitted(org_id: str, period_key: str) -> dict[str, list[str]]:
    """metric_code → names of the contributors still owing a submitted value (MI-9)."""
    slices = assignments_in_force(org_id, period_key)
    have = current(org_id, period_key, slices)
    out: dict[str, list[str]] = defaultdict(list)
    for ia in slices:
        sub = have.get(str(ia.assignment_id))
        if sub is not None and sub.state != "draft":
            continue
        who = contributor(ia, period_key)
        out[ia.metric_code].append(who.display_name if who else "nobody (no line manager)")
    return {code: sorted(set(names)) for code, names in out.items()}
