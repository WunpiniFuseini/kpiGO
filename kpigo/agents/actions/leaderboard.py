"""The leaderboard summary and custom cohorts (PRD AP-3, App Flow §4.1 section 1).

``agent.leaderboard`` is the first section of the Agent Performance page: the
summary cards and the ranked list for one cohort. It reads
``mv_leaderboard_daily``, which ingestion refreshes after every daily load;
``agent.leaderboard.refresh`` refreshes it on demand. Open by default (AP-7).
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from django.db.models import Q
from django.utils import timezone
from pydantic import BaseModel, Field, StringConstraints

from kpigo.access.identity import app_user_for
from kpigo.action import ActionContext, Conflict, InvalidInput, NotFound, action
from kpigo.agents import leaderboard as lb
from kpigo.agents.actions.pace import (
    AgentOut,
    MetricCode,
    PaceBandOut,
    WindowOut,
    agent_out,
    resolve_window,
    window_out,
)
from kpigo.agents.config import AgentProduct, CohortType, settings_for
from kpigo.agents.daily import agents_on, board, window_for
from kpigo.agents.models import AgentCohort, AgentCohortMember
from kpigo.agents.pace import ADDITIVE, q, ratio
from kpigo.hierarchy.models import Subject
from kpigo.ingestion.conform import refresh_daily_totals
from kpigo.metrics.models import Metric
from kpigo.platform.vocab import Code
from kpigo.scorecards.bands import bands_for, lookup

COMPOSITE = "composite"


class CohortOptionOut(BaseModel):
    code: str
    name: str
    agents: int


class RankKeyOut(BaseModel):
    # A metric_code, or "composite" (the mean of capped paces).
    key: str
    display_name: str
    unit: str | None
    decimal_places: int
    direction: str


class SummaryOut(BaseModel):
    agents: int
    # Agents at or above pace on the ranking key.
    on_pace: int
    # For an additive ranking metric: the cohort's value to date, what the
    # target expected by now, their pace, and the as-of day's own figure.
    value_to_date: Decimal | None
    target_to_date: Decimal | None
    pace: Decimal | None
    short_of_pace: Decimal | None
    day_value: Decimal | None
    currency_code: str | None


class LeaderboardRowOut(BaseModel):
    rank: int | None
    agent: AgentOut
    is_you: bool
    # paced | not_reported | no_target | no_fx_rate | not_started (of the ranking
    # metric), or for the composite: paced when any metric paced, else not_reported.
    state: str
    value: Decimal | None
    pace: Decimal | None
    band: PaceBandOut | None
    tiebreak_value: Decimal | None


class LeaderboardIn(BaseModel):
    product: AgentProduct
    as_of: date | None = None
    window: Literal["month", "week"] | None = None
    # Defaults to the module's setting.
    cohort_type: CohortType | None = None
    # Defaults to the caller's own cohort, else the first by name.
    cohort_code: str | None = Field(default=None, max_length=64)
    # A metric_code or "composite"; defaults to the module's setting.
    rank_by: MetricCode | None = None
    # Rows beyond this are left out, except the caller's own.
    limit: int = Field(default=100, ge=1, le=1000)


class LeaderboardOut(BaseModel):
    product: str
    window: WindowOut
    cohort_type: str
    cohort: CohortOptionOut | None
    cohorts: list[CohortOptionOut]
    rank_by: RankKeyOut | None
    tiebreak: RankKeyOut | None
    # Metrics the module's agents are measured on, for the "Rank by" choice.
    rank_options: list[RankKeyOut]
    summary: SummaryOut
    rows: list[LeaderboardRowOut]
    # Rows in the cohort, of which ``rows`` shows the first ``limit``.
    total: int


OVERALL = RankKeyOut(
    key=COMPOSITE,
    display_name="Overall pace",
    unit=None,
    decimal_places=2,
    direction="higher_is_better",
)


def _key_out(metric_code: str | None, metrics: dict[str, Metric]) -> RankKeyOut | None:
    if metric_code == COMPOSITE:
        return OVERALL
    m = metrics.get(metric_code) if metric_code else None
    if m is None:
        return None
    return RankKeyOut(
        key=m.metric_code,
        display_name=m.display_name,
        unit=m.unit,
        decimal_places=m.decimal_places,
        direction=m.direction,
    )


@action(
    name="agent.leaderboard",
    summary="Agents ranked within a cohort, with the summary cards (month or week to date).",
    schema=LeaderboardIn,
    output=LeaderboardOut,
    permission="agent.view",
    read_only=True,
    module="agent_performance",
    example={"product": "agent_sales", "cohort_type": "region"},
)
def leaderboard(params: LeaderboardIn, ctx: ActionContext) -> LeaderboardOut:
    s = settings_for(ctx.org_id, params.product)
    kind, as_of = resolve_window(ctx, params.product, params.window, params.as_of)
    window = window_for(kind, as_of)
    cohort_type = params.cohort_type or s.cohort_type

    agents, by_profile = agents_on(ctx.org_id, params.product, as_of)
    metrics = {m.metric_code: m for ms in by_profile.values() for m in ms}
    ordered = sorted(metrics.values(), key=lambda m: (m.display_name.lower(), m.metric_code))
    options = [k for k in (_key_out(m.metric_code, metrics) for m in ordered) if k is not None]
    if metrics:
        options.append(OVERALL)

    rank_by = params.rank_by or s.rank_metric_code or COMPOSITE
    if rank_by != COMPOSITE and rank_by not in metrics:
        if params.rank_by is not None:
            raise InvalidInput(
                f"Nobody in {params.product} is measured on '{rank_by}' on {as_of}.",
                detail={"rank_by": [*sorted(metrics), COMPOSITE]},
            )
        rank_by = COMPOSITE  # the configured metric left every profile: fall back
    tiebreak = s.tiebreak_metric_code if s.tiebreak_metric_code in metrics else None

    found = lb.cohorts(ctx.org_id, params.product, cohort_type, agents, as_of)
    account = app_user_for(ctx.user, ctx.org_id)
    mine = str(account.subject_id) if account and account.subject_id else None
    chosen: lb.Cohort | None = None
    if params.cohort_code is not None:
        chosen = next((c for c in found if c.code == params.cohort_code), None)
        if chosen is None:
            raise NotFound(f"No {cohort_type} '{params.cohort_code}' has agents on {as_of}.")
    elif found:
        chosen = next((c for c in found if mine in c.subject_ids), found[0])

    b = board(
        ctx.org_id,
        params.product,
        window,
        subject_ids=sorted(chosen.subject_ids) if chosen else [],
    )
    rows = lb.rank(
        b.agents,
        rank_metric_code=None if rank_by == COMPOSITE else rank_by,
        tiebreak_metric_code=tiebreak,
        cap=s.pace_cap,
    )
    bands = bands_for(ctx.org_id, params.product)

    shown = [r for i, r in enumerate(rows) if i < params.limit or r.agent.agent.subject_id == mine]
    out_rows = []
    for r in shown:
        state = (
            r.primary.pace.state
            if r.primary is not None
            else ("paced" if r.pace is not None else "not_reported")
        )
        band = lookup(bands, r.pace) if r.pace is not None else None
        out_rows.append(
            LeaderboardRowOut(
                rank=r.rank,
                agent=agent_out(r.agent),
                is_you=r.agent.agent.subject_id == mine,
                state=state,
                value=r.value,
                pace=r.pace,
                band=PaceBandOut(label=band.label, ramp_position=band.ramp_position)
                if band
                else None,
                tiebreak_value=r.tiebreak,
            )
        )

    return LeaderboardOut(
        product=params.product,
        window=window_out(b),
        cohort_type=cohort_type,
        cohort=CohortOptionOut(code=chosen.code, name=chosen.name, agents=len(chosen.subject_ids))
        if chosen
        else None,
        cohorts=[
            CohortOptionOut(code=c.code, name=c.name, agents=len(c.subject_ids)) for c in found
        ],
        rank_by=_key_out(rank_by, metrics),
        tiebreak=_key_out(tiebreak, metrics),
        rank_options=options,
        summary=_summary(rows, rank_by, metrics, s.pace_cap),
        rows=out_rows,
        total=len(rows),
    )


def _summary(
    rows: list[lb.Row], rank_by: str, metrics: dict[str, Metric], cap: Decimal
) -> SummaryOut:
    on_pace = sum(1 for r in rows if r.pace is not None and r.pace >= 1)
    empty = SummaryOut(
        agents=len(rows),
        on_pace=on_pace,
        value_to_date=None,
        target_to_date=None,
        pace=None,
        short_of_pace=None,
        day_value=None,
        currency_code=None,
    )
    m = metrics.get(rank_by)
    if m is None or m.aggregation not in ADDITIVE:
        return empty
    reported = [
        r.primary for r in rows if r.primary is not None and r.primary.pace.actual is not None
    ]
    if not reported:
        return empty
    value = sum((p.pace.actual for p in reported if p.pace.actual is not None), Decimal(0))
    paced = [p for p in reported if p.pace.target_to_date is not None and p.pace.state == "paced"]
    expected = sum((p.pace.target_to_date for p in paced if p.pace.target_to_date), Decimal(0))
    paced_value = sum((p.pace.actual for p in paced if p.pace.actual is not None), Decimal(0))
    direction = m.direction
    pace = ratio(paced_value, expected, direction, cap) if paced and expected > 0 else None
    gap = None
    if pace is not None:
        gap = expected - paced_value if direction != "lower_is_better" else paced_value - expected
    day = [p.day_value for p in reported if p.day_value is not None]
    return SummaryOut(
        agents=len(rows),
        on_pace=on_pace,
        value_to_date=q(value),
        target_to_date=q(expected) if paced else None,
        pace=q(pace),
        short_of_pace=q(max(gap, Decimal(0))) if gap is not None else None,
        day_value=q(sum(day, Decimal(0))) if day else None,
        currency_code=next((p.currency_code for p in reported if p.currency_code), None),
    )


# ── refresh ──────────────────────────────────────────────────────────────────


class LeaderboardRefreshIn(BaseModel):
    pass


class LeaderboardRefreshOut(BaseModel):
    refreshed_at: str


@action(
    name="agent.leaderboard.refresh",
    summary="Refresh the daily totals the leaderboard reads (done after every daily load).",
    schema=LeaderboardRefreshIn,
    output=LeaderboardRefreshOut,
    permission="agent.retention.manage",
    read_only=False,
    module="agent_performance",
    audit="agent.leaderboard_refreshed",
    example={},
)
def refresh(params: LeaderboardRefreshIn, ctx: ActionContext) -> LeaderboardRefreshOut:
    refresh_daily_totals()
    return LeaderboardRefreshOut(refreshed_at=timezone.now().isoformat())


# ── custom cohorts ───────────────────────────────────────────────────────────


class CohortMemberOut(BaseModel):
    subject_id: uuid.UUID
    staff_no: str
    full_name: str
    effective_from: date
    effective_to: date | None


class CohortOut(BaseModel):
    cohort_id: uuid.UUID
    product: str
    code: str
    name: str
    status: str
    members: list[CohortMemberOut]


def _cohort_out(c: AgentCohort, day: date) -> CohortOut:
    rows = (
        c.members.filter(effective_from__lte=day)
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=day))
        .select_related("subject")
        .order_by("subject__full_name")
    )
    return CohortOut(
        cohort_id=c.cohort_id,
        product=c.product,
        code=c.code,
        name=c.name,
        status=c.status,
        members=[
            CohortMemberOut(
                subject_id=m.subject_id,
                staff_no=m.subject.staff_no,
                full_name=m.subject.full_name,
                effective_from=m.effective_from,
                effective_to=m.effective_to,
            )
            for m in rows
        ],
    )


class CohortListIn(BaseModel):
    product: AgentProduct
    as_of: date | None = None


class CohortListOut(BaseModel):
    cohorts: list[CohortOut]


@action(
    name="agent.cohort.list",
    summary="Custom leaderboard cohorts and who is in them on a day.",
    schema=CohortListIn,
    output=CohortListOut,
    permission="agent.view",
    read_only=True,
    module="agent_performance",
    example={"product": "agent_sales"},
)
def list_cohorts(params: CohortListIn, ctx: ActionContext) -> CohortListOut:
    day = params.as_of or timezone.localdate()
    rows = AgentCohort.objects.filter(org_id=ctx.org_id, product=params.product).order_by("name")
    return CohortListOut(cohorts=[_cohort_out(c, day) for c in rows])


class CohortSetIn(BaseModel):
    product: AgentProduct
    code: Code
    name: Annotated[str, StringConstraints(min_length=1, max_length=120, strip_whitespace=True)]
    # Who is in it from ``effective_from``; anyone not listed leaves that day.
    subject_ids: list[uuid.UUID] = Field(max_length=5000)
    effective_from: date
    status: Literal["active", "retired"] = "active"


@action(
    name="agent.cohort.set",
    summary="Create or change a custom leaderboard cohort and its members from a date.",
    schema=CohortSetIn,
    output=CohortOut,
    permission="agent.config.manage",
    read_only=False,
    module="agent_performance",
    audit="agent.cohort_set",
    config_change=True,
    example={
        "product": "agent_sales",
        "code": "top_sme",
        "name": "SME high flyers",
        "subject_ids": [],
        "effective_from": "2026-11-01",
    },
)
def set_cohort(params: CohortSetIn, ctx: ActionContext) -> CohortOut:
    wanted = {str(s) for s in params.subject_ids}
    known = {
        str(s)
        for s in Subject.objects.filter(
            org_id=ctx.org_id, subject_id__in=sorted(wanted)
        ).values_list("subject_id", flat=True)
    }
    if wanted - known:
        raise InvalidInput("Unknown subjects.", detail={"unknown": sorted(wanted - known)})
    cohort, created = AgentCohort.objects.get_or_create(
        org_id=ctx.org_id,
        product=params.product,
        code=params.code,
        defaults={"name": params.name, "status": params.status, "created_by": ctx.user_id},
    )
    if not created:
        cohort.name = params.name
        cohort.status = params.status
        cohort.updated_by = ctx.user_id
        cohort.updated_at = timezone.now()
        cohort.save()
    day = params.effective_from
    current = {
        str(m.subject_id): m
        for m in cohort.members.select_for_update().filter(
            Q(effective_to__isnull=True) | Q(effective_to__gt=day)
        )
    }
    for subject_id, m in current.items():
        if m.effective_from > day:
            raise Conflict(
                "A membership change is already scheduled after that date; set the cohort "
                f"from {m.effective_from} or later."
            )
        if subject_id not in wanted:
            m.effective_to = day
            m.updated_by = ctx.user_id
            m.updated_at = timezone.now()
            m.save()
    for subject_id in sorted(wanted - set(current)):
        AgentCohortMember.objects.create(
            cohort=cohort,
            subject_id=subject_id,
            effective_from=day,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
        )
    return _cohort_out(cohort, day)
