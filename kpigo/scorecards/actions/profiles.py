"""Profile-to-metric assignment for Scorecards (Engineering Plan §4, weeks 1–2).

A profile's scorecard is the set of Scorecards metrics assigned to it
(``metric_profile_assignment``), effective-dated, so a change made for next
month leaves this month's scorecard as it was.
"""

from __future__ import annotations

from collections import Counter
from datetime import date

from django.db.models import Q
from pydantic import BaseModel, Field

from kpigo.action import ActionContext, Conflict, InvalidInput, action
from kpigo.hierarchy.scope import current_period_key
from kpigo.metrics.actions.metric import MetricCode
from kpigo.metrics.models import Metric, MetricProfileAssignment
from kpigo.platform.vocab import Code, PeriodKey
from kpigo.scorecards import roster
from kpigo.scorecards.taxonomy import Placer


class ProfileMetricOut(BaseModel):
    metric_code: str
    display_name: str
    target_scope: str
    unit: str
    direction: str
    collection_method: str
    # The taxonomy path it renders under, top level first; empty when unplaced.
    path: list[str]


class ProfileCardOut(BaseModel):
    profile_code: str
    member_count: int
    metrics: list[ProfileMetricOut]


class ProfileCardListIn(BaseModel):
    # Defaults to the current period.
    period_key: PeriodKey | None = None


class ProfileCardListOut(BaseModel):
    period_key: str
    profiles: list[ProfileCardOut]


@action(
    name="scorecard.profile.list",
    summary="Each profile's scorecard metrics and headcount in a period.",
    schema=ProfileCardListIn,
    output=ProfileCardListOut,
    permission="scorecard.config.view",
    read_only=True,
    module="scorecards",
    example={"period_key": "202610"},
)
def list_profiles(params: ProfileCardListIn, ctx: ActionContext) -> ProfileCardListOut:
    period_key = params.period_key or current_period_key(ctx.org_id)
    metrics = roster.profile_metrics(ctx.org_id, period_key)
    counts = Counter(
        roster.assignments_in_force(ctx.org_id, period_key).values_list("profile_code", flat=True)
    )
    placer = Placer.active(ctx.org_id)
    profiles = sorted(set(metrics) | set(counts) | set(roster.known_profiles(ctx.org_id)))
    return ProfileCardListOut(
        period_key=period_key,
        profiles=[
            ProfileCardOut(
                profile_code=code,
                member_count=counts.get(code, 0),
                metrics=[
                    ProfileMetricOut(
                        metric_code=m.metric_code,
                        display_name=m.display_name,
                        target_scope=m.target_scope,
                        unit=m.unit,
                        direction=m.direction,
                        collection_method=m.collection_method,
                        path=placer.path(m.metric_code, code),
                    )
                    for m in metrics.get(code, [])
                ],
            )
            for code in profiles
        ],
    )


class ProfileSetIn(BaseModel):
    profile_code: Code
    # The profile's full Scorecards metric set from ``effective_from``.
    metric_codes: list[MetricCode] = Field(max_length=200)
    effective_from: date


class ProfileSetOut(BaseModel):
    profile_code: str
    effective_from: date
    added: list[str]
    removed: list[str]


@action(
    name="scorecard.profile.set_metrics",
    summary="Set a profile's Scorecards metrics from a date; earlier months keep theirs.",
    schema=ProfileSetIn,
    output=ProfileSetOut,
    permission="scorecard.config.manage",
    read_only=False,
    module="scorecards",
    requires_approval="metric_change",
    audit="scorecard.profile_metrics_set",
    config_change=True,
    example={
        "profile_code": "retail_rm",
        "metric_codes": ["total_deposits"],
        "effective_from": "2026-11-01",
    },
)
def set_profile_metrics(params: ProfileSetIn, ctx: ActionContext) -> ProfileSetOut:
    if len(set(params.metric_codes)) != len(params.metric_codes):
        raise InvalidInput("Each metric may appear once.")
    starts = params.effective_from
    current: dict[str, Metric] = {}
    rows = Metric.objects.filter(
        org_id=ctx.org_id,
        metric_code__in=params.metric_codes,
        bindings__product="scorecards",
        bindings__is_active=True,
    ).filter(Q(effective_to__isnull=True) | Q(effective_to__gt=starts))
    for m in rows.order_by("effective_from"):
        # The version in force on the date, else the one that starts after it.
        current.setdefault(m.metric_code, m)
    unknown = sorted(set(params.metric_codes) - set(current))
    if unknown:
        raise InvalidInput(
            "Not Scorecards metrics in force on that date.", detail={"metric_codes": unknown}
        )
    running = (
        MetricProfileAssignment.objects.select_for_update()
        .filter(product="scorecards", profile_code=params.profile_code, metric__org_id=ctx.org_id)
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=starts))
        .select_related("metric")
    )
    have = {row.metric.metric_code for row in running}
    removed = sorted(have - set(params.metric_codes))
    added = sorted(set(params.metric_codes) - have)
    if not added and not removed:
        raise Conflict("The profile already has exactly these metrics from that date.")
    for row in running:
        if row.metric.metric_code not in removed:
            continue
        if row.effective_from >= starts:
            row.delete()
        else:
            MetricProfileAssignment.objects.filter(pk=row.pk).update(effective_to=starts)
    for code in added:
        metric = current[code]
        MetricProfileAssignment.objects.create(
            metric=metric,
            profile_code=params.profile_code,
            product="scorecards",
            effective_from=max(starts, metric.effective_from),
            created_by=ctx.user_id,
        )
    return ProfileSetOut(
        profile_code=params.profile_code, effective_from=starts, added=added, removed=removed
    )
