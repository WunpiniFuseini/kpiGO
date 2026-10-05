"""Who is measured on what in a period (PRD HH-2).

Everything resolves on the period's last day: the assignment in force then
decides a subject's profile, the profile assignment decides the metric set, and
the metric row in force then is the definition scored. This is the same rule
ingestion's missing-row check and the visibility closure use.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from django.db.models import Q, QuerySet

from kpigo.hierarchy.models import Assignment
from kpigo.metrics.models import Metric, MetricProfileAssignment
from kpigo.scorecards.cycles import last_day

PRODUCT = "scorecards"


def in_force(day: date) -> Q:
    return Q(effective_from__lte=day) & (Q(effective_to__isnull=True) | Q(effective_to__gt=day))


def metric_in_force(org_id: str, metric_code: str, period_key: str) -> Metric | None:
    return (
        Metric.objects.filter(org_id=org_id, metric_code=metric_code)
        .filter(in_force(last_day(period_key)))
        .first()
    )


def metrics_in_force(org_id: str, codes: Iterable[str], period_key: str) -> dict[str, Metric]:
    rows = Metric.objects.filter(org_id=org_id, metric_code__in=list(codes)).filter(
        in_force(last_day(period_key))
    )
    return {m.metric_code: m for m in rows}


def profile_metrics(
    org_id: str, period_key: str, profiles: Iterable[str] | None = None
) -> dict[str, list[Metric]]:
    """Active scorecard metrics per profile, in force on the period's last day."""
    day = last_day(period_key)
    rows = (
        MetricProfileAssignment.objects.filter(
            product=PRODUCT,
            metric__org_id=org_id,
            metric__status="active",
            metric__bindings__product=PRODUCT,
            metric__bindings__is_active=True,
        )
        .filter(in_force(day))
        .filter(
            Q(metric__effective_from__lte=day)
            & (Q(metric__effective_to__isnull=True) | Q(metric__effective_to__gt=day))
        )
        .select_related("metric")
    )
    if profiles is not None:
        rows = rows.filter(profile_code__in=list(profiles))
    out: dict[str, list[Metric]] = defaultdict(list)
    for row in rows.order_by("profile_code", "metric__metric_code"):
        out[row.profile_code].append(row.metric)
    return dict(out)


def assignments_in_force(org_id: str, period_key: str) -> QuerySet[Assignment]:
    return Assignment.objects.filter(org_id=org_id, subject__status="active").filter(
        in_force(last_day(period_key))
    )


@dataclass(frozen=True)
class Member:
    subject_id: str
    assignment_id: str
    profile_code: str
    staff_no: str
    full_name: str
    cycle_id: str | None


def profile_members(
    org_id: str, period_key: str, profiles: Iterable[str] | None = None
) -> dict[str, list[Member]]:
    rows = assignments_in_force(org_id, period_key).select_related("subject")
    if profiles is not None:
        rows = rows.filter(profile_code__in=list(profiles))
    out: dict[str, list[Member]] = defaultdict(list)
    for a in rows.order_by("profile_code", "subject__staff_no"):
        out[a.profile_code].append(
            Member(
                subject_id=str(a.subject_id),
                assignment_id=str(a.assignment_id),
                profile_code=a.profile_code,
                staff_no=a.subject.staff_no,
                full_name=a.subject.full_name,
                cycle_id=str(a.cycle_id) if a.cycle_id else None,
            )
        )
    return dict(out)


def member_of(org_id: str, subject_id: str, period_key: str) -> Member | None:
    a = (
        assignments_in_force(org_id, period_key)
        .filter(subject_id=subject_id)
        .select_related("subject")
        .first()
    )
    if a is None:
        return None
    return Member(
        subject_id=str(a.subject_id),
        assignment_id=str(a.assignment_id),
        profile_code=a.profile_code,
        staff_no=a.subject.staff_no,
        full_name=a.subject.full_name,
        cycle_id=str(a.cycle_id) if a.cycle_id else None,
    )


def known_profiles(org_id: str) -> list[str]:
    """Every profile code the org uses, on an assignment or a scorecard metric."""
    codes = set(
        Assignment.objects.filter(org_id=org_id).values_list("profile_code", flat=True).distinct()
    )
    codes |= set(
        MetricProfileAssignment.objects.filter(metric__org_id=org_id, product=PRODUCT)
        .values_list("profile_code", flat=True)
        .distinct()
    )
    return sorted(codes)
