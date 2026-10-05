"""The per-subject scoring path (TDD §4.3).

Scores are computed at query time for open periods. Everything resolves on the
period's last day through the assignment in force then (PRD HH-2): its profile
decides the metric set, its cycle (else the product's) the target arithmetic,
its branch, region, segment and portfolio which dimension overrides reach it.
This path is the readable reference; ``bulk`` must agree with it exactly.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from django.db.models import Q

from kpigo.hierarchy.models import Assignment
from kpigo.ingestion.models import FactActualMonthly
from kpigo.metrics.models import Metric
from kpigo.platform.config import MissingRate, fx_rate
from kpigo.scorecards import roster
from kpigo.scorecards.bands import bands_for
from kpigo.scorecards.config import settings_for
from kpigo.scorecards.cycles import cycle_for
from kpigo.scorecards.engine import (
    ActualIn,
    AppliedOverride,
    CyclePosition,
    MetricIn,
    SubjectScore,
    TargetIn,
    score_metric,
    total,
    winning,
)
from kpigo.scorecards.models import OVERRIDE_DIMENSIONS, Override, Target

PRODUCT = "scorecards"


def micros(stamp: datetime | None) -> int:
    return 0 if stamp is None else int(stamp.timestamp() * 1_000_000)


def position(org_id: str, period_key: str, cycle_id: str | None) -> CyclePosition:
    cycle = cycle_for(org_id, PRODUCT, period_key, cycle_id)
    return CyclePosition(
        months=cycle.months,
        months_elapsed=cycle.month_no(period_key),
        quarters_elapsed=cycle.quarter_no(period_key),
    )


def dimension_keys(a: Assignment) -> set[str]:
    """``<dimension>:<member>`` for each dimension the assignment carries."""
    out: set[str] = set()
    for dim in OVERRIDE_DIMENSIONS:
        value = getattr(a, f"{dim}_code")
        if value:
            out.add(f"{dim}:{value}")
    return out


def applicable_overrides(org_id: str, period_key: str) -> Q:
    """Approved scorecard overrides whose period range covers ``period_key``."""
    return Q(
        org_id=org_id,
        product=PRODUCT,
        status="approved",
        period_from__lte=period_key,
    ) & (Q(period_to__gte=period_key) | Q(period_to__isnull=True, period_from=period_key))


def applied(o: Override) -> AppliedOverride:
    return AppliedOverride(
        override_id=str(o.override_id),
        change_type=o.change_type,
        scope_type=o.scope_type,
        scope_code=o.scope_code,
        value=Decimal(o.override_value) if o.override_value is not None else None,
        text=o.override_text,
        reason=o.reason,
    )


def score_subject(org_id: str, subject_id: str, period_key: str) -> SubjectScore | None:
    """A subject's live scorecard for a period; None when no assignment is in force."""
    a = roster.assignments_in_force(org_id, period_key).filter(subject_id=subject_id).first()
    if a is None:
        return None
    metrics: list[Metric] = roster.profile_metrics(org_id, period_key, [a.profile_code]).get(
        a.profile_code, []
    )
    ids = [m.metric_id for m in metrics]

    targets = {
        (str(t.metric_id), t.scope_type): t
        for t in Target.objects.filter(
            org_id=org_id,
            metric_id__in=ids,
            period_key=period_key,
            series_type="target",
            state="published",
        ).filter(
            Q(scope_type="profile", scope_code=a.profile_code)
            | Q(scope_type="subject", scope_code=str(subject_id))
        )
    }
    facts = {
        str(f.metric_id): f
        for f in FactActualMonthly.objects.filter(
            org_id=org_id, subject_id=subject_id, period_key=period_key, metric_id__in=ids
        )
    }

    dims = dimension_keys(a)
    candidates: dict[str, list[tuple[AppliedOverride, int]]] = defaultdict(list)
    rows = (
        Override.objects.filter(applicable_overrides(org_id, period_key))
        .filter(metric__metric_code__in=[m.metric_code for m in metrics])
        .filter(
            Q(scope_type="subject", scope_code=str(subject_id))
            | Q(scope_type="profile", scope_code=a.profile_code)
            | Q(scope_type="dimension", scope_code__in=sorted(dims))
        )
        .select_related("metric")
    )
    for o in rows:
        candidates[o.metric.metric_code].append((applied(o), micros(o.approved_at)))

    pos = position(org_id, period_key, str(a.cycle_id) if a.cycle_id else None)
    scored = []
    for m in metrics:
        t = targets.get((str(m.metric_id), m.target_scope))
        f = facts.get(str(m.metric_id))
        target = (
            TargetIn(
                target_id=str(t.target_id),
                version=t.version,
                scope_type=t.scope_type,
                value=Decimal(t.target_value),
                target_type=t.target_type,
                weight=Decimal(t.weight) if t.weight is not None else None,
                cap=Decimal(t.cap) if t.cap is not None else None,
                currency_code=t.currency_code,
            )
            if t is not None
            else None
        )
        actual = (
            ActualIn(
                value=Decimal(f.actual_value),
                currency_code=f.currency_code,
                run_id=str(f.run_id) if f.run_id else None,
            )
            if f is not None
            else None
        )
        scored.append(
            score_metric(
                MetricIn(
                    metric_id=str(m.metric_id),
                    metric_code=m.metric_code,
                    display_name=m.display_name,
                    direction=m.direction,
                    unit=m.unit,
                    decimal_places=m.decimal_places,
                    target=target,
                    actual=actual,
                    overrides=winning(candidates.get(m.metric_code, [])),
                    fx_rate=_rate(org_id, period_key, actual, target),
                ),
                pos,
            )
        )

    return total(
        subject_id=str(subject_id),
        assignment_id=str(a.assignment_id),
        profile_code=a.profile_code,
        period_key=period_key,
        pos=pos,
        policy=settings_for(org_id).denominator_policy,
        metrics=scored,
        bands=bands_for(org_id),
    )


def _rate(
    org_id: str, period_key: str, actual: ActualIn | None, target: TargetIn | None
) -> Decimal | None:
    """Actuals convert into the target's currency at the period's average rate."""
    if actual is None or target is None:
        return Decimal(1)
    src, dst = actual.currency_code, target.currency_code
    if not src or not dst or src == dst:
        return Decimal(1)
    try:
        return fx_rate(org_id, src, dst, period_key, "average")
    except MissingRate:
        return None
