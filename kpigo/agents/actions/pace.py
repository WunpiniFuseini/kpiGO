"""Agent Performance pacing and settings (PRD AP-1, AP-2, AP-4).

``agent.pace`` is one agent's month (or week) to date on every metric of their
profile, paced against elapsed working days. Agent Performance is open by
default (AP-7): any holder of ``agent.view`` may read any agent; the per-role
restriction lands with the presets.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Literal

from django.utils import timezone
from pydantic import BaseModel, Field, StringConstraints, model_validator

from kpigo.access.identity import app_user_for
from kpigo.action import ActionContext, Conflict, InvalidInput, NotFound, action
from kpigo.agents.config import AgentProduct, CohortType, Grain, rag, settings_for
from kpigo.agents.daily import AgentPace, Board, board, default_as_of, window_for
from kpigo.agents.models import AgentSettings
from kpigo.metrics.models import METRIC_CODE_PATTERN, MetricBinding
from kpigo.scorecards.bands import bands_for, lookup

EXAMPLE_ID = "00000000-0000-0000-0000-000000000000"
WindowKind = Literal["month", "week"]
MetricCode = Annotated[str, StringConstraints(pattern=METRIC_CODE_PATTERN, max_length=80)]


# ── shared output pieces ─────────────────────────────────────────────────────


class PaceBandOut(BaseModel):
    label: str
    ramp_position: int


class MetricPaceOut(BaseModel):
    metric_id: uuid.UUID
    metric_code: str
    display_name: str
    direction: str
    aggregation: str
    unit: str
    decimal_places: int
    currency_code: str | None
    # paced | not_reported | no_target | no_fx_rate | not_started
    state: str
    actual: Decimal | None
    # What the target expects by the as-of day (the target itself when not additive).
    target_to_date: Decimal | None
    window_target: Decimal | None
    # Out of 1.0 against target to date; null unless paced.
    pace: Decimal | None
    short_of_pace: Decimal | None
    projected: Decimal | None
    days_reported: int
    target_id: uuid.UUID | None
    target_version: int | None
    target_type: str | None
    # The pace bar reads the grade ramp, as the scorecard does (Design Brief §5.3).
    band: PaceBandOut | None
    rag: Literal["green", "amber", "red"] | None


class WindowOut(BaseModel):
    kind: WindowKind
    start: date
    # Last day of the window (inclusive), for display.
    end: date
    as_of: date
    # Working day N of M, on the org-wide calendar.
    working_day: int
    working_days: int


class AgentOut(BaseModel):
    subject_id: uuid.UUID
    staff_no: str
    full_name: str
    role_code: str
    profile_code: str
    branch_code: str | None
    region_code: str | None


def window_out(b: Board) -> WindowOut:
    return WindowOut(
        kind=b.window.kind,
        start=b.window.start,
        end=b.window.end - timedelta(days=1),
        as_of=b.window.as_of,
        working_day=b.working_days_elapsed,
        working_days=b.working_days_total,
    )


def agent_out(a: AgentPace) -> AgentOut:
    g = a.agent
    return AgentOut(
        subject_id=uuid.UUID(g.subject_id),
        staff_no=g.staff_no,
        full_name=g.full_name,
        role_code=g.role_code,
        profile_code=g.profile_code,
        branch_code=g.branch_code,
        region_code=g.region_code,
    )


def metrics_out(b: Board, a: AgentPace, org_id: str) -> list[MetricPaceOut]:
    bands = bands_for(org_id, b.product)
    out = []
    for mp in a.metrics:
        p, m, t = mp.pace, mp.metric, mp.target
        band = lookup(bands, p.pace) if p.state == "paced" else None
        out.append(
            MetricPaceOut(
                metric_id=m.metric_id,
                metric_code=m.metric_code,
                display_name=m.display_name,
                direction=m.direction,
                aggregation=m.aggregation,
                unit=m.unit,
                decimal_places=m.decimal_places,
                currency_code=mp.currency_code,
                state=p.state,
                actual=p.actual,
                target_to_date=p.target_to_date,
                window_target=p.window_target,
                pace=p.pace,
                short_of_pace=p.short_of_pace,
                projected=p.projected,
                days_reported=p.days_reported,
                target_id=uuid.UUID(t.target_id) if t else None,
                target_version=t.version if t else None,
                target_type=t.target_type if t else None,
                band=PaceBandOut(label=band.label, ramp_position=band.ramp_position)
                if band
                else None,
                rag=rag(b.settings, p.pace) if p.state == "paced" else None,
            )
        )
    return out


def resolve_window(
    ctx: ActionContext, product: str, kind: WindowKind | None, as_of: date | None
) -> tuple[WindowKind, date]:
    return (
        kind or settings_for(ctx.org_id, product).window,
        as_of or default_as_of(ctx.org_id, product),
    )


# ── agent.pace ───────────────────────────────────────────────────────────────


class AgentPaceIn(BaseModel):
    product: AgentProduct
    # Whose pace; defaults to the caller's own.
    subject_id: uuid.UUID | None = None
    # Defaults to the latest day loaded for the product.
    as_of: date | None = None
    # Defaults to the product's grain: month for daily, week for the weekly opt-in.
    window: WindowKind | None = None


class AgentPaceOut(BaseModel):
    product: str
    window: WindowOut
    agent: AgentOut
    metrics: list[MetricPaceOut]


@action(
    name="agent.pace",
    summary="One agent's month or week to date on each metric, paced by working day.",
    schema=AgentPaceIn,
    output=AgentPaceOut,
    permission="agent.view",
    read_only=True,
    module="agent_performance",
    example={"product": "agent_sales", "subject_id": EXAMPLE_ID},
)
def agent_pace(params: AgentPaceIn, ctx: ActionContext) -> AgentPaceOut:
    subject_id = str(params.subject_id) if params.subject_id else None
    if subject_id is None:
        account = app_user_for(ctx.user, ctx.org_id)
        if account is None or account.subject_id is None:
            raise Conflict(
                "Your account is not linked to a person in the hierarchy, so you have no "
                "pace of your own. Pick an agent instead."
            )
        subject_id = str(account.subject_id)
    kind, as_of = resolve_window(ctx, params.product, params.window, params.as_of)
    b = board(ctx.org_id, params.product, window_for(kind, as_of), subject_ids=[subject_id])
    if not b.agents:
        raise NotFound(
            f"That person is not measured in {params.product} on {as_of}: their profile "
            "carries none of its metrics."
        )
    a = b.agents[0]
    return AgentPaceOut(
        product=params.product,
        window=window_out(b),
        agent=agent_out(a),
        metrics=metrics_out(b, a, ctx.org_id),
    )


# ── settings ─────────────────────────────────────────────────────────────────


class AgentSettingsGetIn(BaseModel):
    product: AgentProduct


class AgentSettingsOut(BaseModel):
    product: str
    grain: str
    pace_cap: Decimal
    rag_green: Decimal
    rag_amber: Decimal
    # The leaderboard: null ranks by the composite (mean of capped paces).
    rank_metric_code: str | None
    tiebreak_metric_code: str | None
    cohort_type: str


def _settings_out(org_id: str, product: str) -> AgentSettingsOut:
    s = settings_for(org_id, product)
    return AgentSettingsOut(
        product=product,
        grain=s.grain,
        pace_cap=s.pace_cap,
        rag_green=s.rag_green,
        rag_amber=s.rag_amber,
        rank_metric_code=s.rank_metric_code,
        tiebreak_metric_code=s.tiebreak_metric_code,
        cohort_type=s.cohort_type,
    )


@action(
    name="agent.settings.get",
    summary="An Agent Performance module's grain, thresholds and leaderboard ranking.",
    schema=AgentSettingsGetIn,
    output=AgentSettingsOut,
    permission="agent.view",
    read_only=True,
    module="agent_performance",
    example={"product": "agent_sales"},
)
def get_settings(params: AgentSettingsGetIn, ctx: ActionContext) -> AgentSettingsOut:
    return _settings_out(ctx.org_id, params.product)


class AgentSettingsSetIn(BaseModel):
    product: AgentProduct
    grain: Grain = "daily"
    pace_cap: Decimal = Field(default=Decimal(2), ge=1, le=10)
    rag_green: Decimal = Field(default=Decimal(1), gt=0, le=5)
    rag_amber: Decimal = Field(default=Decimal("0.85"), gt=0, le=5)
    rank_metric_code: MetricCode | None = None
    tiebreak_metric_code: MetricCode | None = None
    cohort_type: CohortType = "profile"

    @model_validator(mode="after")
    def _ordered(self) -> AgentSettingsSetIn:
        if self.rag_amber > self.rag_green:
            raise ValueError("rag_amber must not be above rag_green")
        if self.tiebreak_metric_code is not None and (
            self.tiebreak_metric_code == self.rank_metric_code
        ):
            raise ValueError("the tie-break metric must differ from the ranking metric")
        return self


@action(
    name="agent.settings.set",
    summary="Set an Agent Performance module's grain (daily, or the weekly opt-in) and thresholds.",
    schema=AgentSettingsSetIn,
    output=AgentSettingsOut,
    permission="agent.config.manage",
    read_only=False,
    module="agent_performance",
    requires_approval="config_change",
    audit="agent.settings_set",
    config_change=True,
    example={
        "product": "agent_sales",
        "grain": "daily",
        "pace_cap": "2",
        "rag_green": "1",
        "rag_amber": "0.85",
        "cohort_type": "profile",
    },
)
def set_settings(params: AgentSettingsSetIn, ctx: ActionContext) -> AgentSettingsOut:
    named = {c for c in (params.rank_metric_code, params.tiebreak_metric_code) if c}
    bound = set(
        MetricBinding.objects.filter(
            product=params.product,
            is_active=True,
            metric__org_id=ctx.org_id,
            metric__metric_code__in=sorted(named),
        ).values_list("metric__metric_code", flat=True)
    )
    if named - bound:
        raise InvalidInput(
            f"Not a {params.product} metric: {', '.join(sorted(named - bound))}.",
            detail={"unbound": sorted(named - bound)},
        )
    row, _ = AgentSettings.objects.get_or_create(
        org_id=ctx.org_id, product=params.product, defaults={"created_by": ctx.user_id}
    )
    row.grain = params.grain
    row.pace_cap = params.pace_cap
    row.rag_green = params.rag_green
    row.rag_amber = params.rag_amber
    row.rank_metric_code = params.rank_metric_code
    row.tiebreak_metric_code = params.tiebreak_metric_code
    row.cohort_type = params.cohort_type
    row.updated_by = ctx.user_id
    row.updated_at = timezone.now()
    row.save()
    return _settings_out(ctx.org_id, params.product)
