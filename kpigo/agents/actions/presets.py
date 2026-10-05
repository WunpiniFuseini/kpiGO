"""Sales and Service presets as actions (PRD AP-4; Scope §8.2; Starter Packs §4.2).

``agent.preset`` is the module's page: its sections in order, each with the
metric it starts on and the ones it can switch to. The sections beyond the
leaderboard and the matrix each have their own read: ``agent.trend``,
``agent.heatmap`` and ``agent.distribution``. All of them read only the agents
the reader may see (AP-7).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, model_validator

from kpigo.action import ActionContext, InvalidInput, NotFound, action
from kpigo.agents import views
from kpigo.agents.actions.leaderboard import RankKeyOut
from kpigo.agents.actions.pace import (
    MetricCode,
    VisibilityOut,
    WindowOut,
    resolve_window,
    visibility_out,
)
from kpigo.agents.config import AgentProduct, Settings, settings_for
from kpigo.agents.daily import Agent, Pacer, Window, agents_on, window_for
from kpigo.agents.presets import Kind, Section, preset_for
from kpigo.agents.visibility import Visibility, visibility_for
from kpigo.hierarchy.models import DimMember
from kpigo.metrics.models import Metric


def _key_out(m: Metric) -> RankKeyOut:
    return RankKeyOut(
        key=m.metric_code,
        display_name=m.display_name,
        unit=m.unit,
        decimal_places=m.decimal_places,
        direction=m.direction,
    )


def _window_out(pacer: Pacer) -> WindowOut:
    w = pacer.window
    return WindowOut(
        kind=w.kind,
        start=w.start,
        end=w.end - timedelta(days=1),
        as_of=w.as_of,
        working_day=pacer.working_days_elapsed,
        working_days=pacer.working_days_total,
    )


@dataclass(frozen=True)
class InView:
    settings: Settings
    window: Window
    as_of: date
    agents: list[Agent]
    by_profile: dict[str, list[Metric]]
    seen: Visibility

    @property
    def metrics(self) -> list[Metric]:
        found = {
            m.metric_code: m for a in self.agents for m in self.by_profile.get(a.profile_code, [])
        }
        return list(found.values())

    def measured_on(self, metric: Metric) -> list[Agent]:
        return [
            a
            for a in self.agents
            if any(
                m.metric_code == metric.metric_code for m in self.by_profile.get(a.profile_code, [])
            )
        ]


def in_view(
    ctx: ActionContext,
    product: str,
    window: Literal["month", "week"] | None,
    as_of: date | None,
    region_code: str | None = None,
    branch_code: str | None = None,
) -> InView:
    kind, day = resolve_window(ctx, product, window, as_of)
    agents, by_profile = agents_on(ctx.org_id, product, day)
    seen = visibility_for(ctx, product, day)
    agents = seen.filter(agents)
    if region_code is not None:
        agents = [a for a in agents if a.region_code == region_code]
    if branch_code is not None:
        agents = [a for a in agents if a.branch_code == branch_code]
    return InView(
        settings=settings_for(ctx.org_id, product),
        window=window_for(kind, day),
        as_of=day,
        agents=agents,
        by_profile=by_profile,
        seen=seen,
    )


def _section(product: str, kind: Kind) -> Section:
    """The preset's section of this kind; any metric goes where the preset has none."""
    found = (s for s in preset_for(product).sections if s.kind == kind)
    return next(found, Section(kind, kind, kind.title(), ""))


def _metric(view: InView, product: str, kind: Kind, code: str | None) -> Metric | None:
    section = _section(product, kind)
    if code is None:
        return section.pick(view.metrics)
    found = {m.metric_code: m for m in section.options(view.metrics)}
    if code not in found:
        raise InvalidInput(
            f"Nobody in view is measured on '{code}' in {product} on {view.as_of}.",
            detail={"metric_codes": sorted(found)},
        )
    return found[code]


class _Read(BaseModel):
    product: AgentProduct
    as_of: date | None = None
    window: Literal["month", "week"] | None = None
    # Defaults to the preset section's metric.
    metric_code: MetricCode | None = None
    # Narrow to one region or branch.
    region_code: str | None = None
    branch_code: str | None = None


# ── agent.preset ─────────────────────────────────────────────────────────────


class AgentPresetIn(BaseModel):
    product: AgentProduct
    as_of: date | None = None


class AgentPresetSectionOut(BaseModel):
    key: str
    kind: Literal["leaderboard", "matrix", "trend", "heatmap", "distribution"]
    title: str
    caption: str
    # The metric it opens on; null when no metric in view suits it, and for the
    # leaderboard and matrix, which open on the module's ranking metric.
    metric: RankKeyOut | None
    metric_options: list[RankKeyOut]


class AgentPresetOut(BaseModel):
    product: str
    as_of: date
    sections: list[AgentPresetSectionOut]
    visibility: VisibilityOut


@action(
    name="agent.preset",
    summary="The Sales or Service page: its sections in order, each with its starting metric.",
    schema=AgentPresetIn,
    output=AgentPresetOut,
    permission="agent.view",
    read_only=True,
    module="agent_performance",
    example={"product": "agent_service"},
)
def preset(params: AgentPresetIn, ctx: ActionContext) -> AgentPresetOut:
    view = in_view(ctx, params.product, None, params.as_of)
    metrics = view.metrics
    sections = []
    for s in preset_for(params.product).sections:
        # The leaderboard and the matrix choose their own metric (module settings).
        own = s.kind in ("leaderboard", "matrix")
        chosen = None if own else s.pick(metrics)
        sections.append(
            AgentPresetSectionOut(
                key=s.key,
                kind=s.kind,
                title=s.title,
                caption=s.caption,
                metric=_key_out(chosen) if chosen else None,
                metric_options=[] if own else [_key_out(m) for m in s.options(metrics)],
            )
        )
    return AgentPresetOut(
        product=params.product,
        as_of=view.as_of,
        sections=sections,
        visibility=visibility_out(view.seen),
    )


# ── agent.trend ──────────────────────────────────────────────────────────────


class AgentTrendIn(_Read):
    pass


class AgentTrendPointOut(BaseModel):
    day: date
    working: bool
    # The day's total (sum or count), or the mean of the agents who reported.
    value: Decimal | None
    reported: int
    cumulative: Decimal | None
    # Where the targets expect the running total by the end of the day.
    expected: Decimal | None
    # Averages: the mean of the reporting agents' targets.
    target: Decimal | None


class AgentTrendOut(BaseModel):
    product: str
    window: WindowOut
    metric: RankKeyOut | None
    additive: bool
    agents: int
    reported: int
    currency_code: str | None
    mixed_currency: bool
    points: list[AgentTrendPointOut]
    visibility: VisibilityOut


@action(
    name="agent.trend",
    summary="Each day to date across the agents in view: the total or mean, against target.",
    schema=AgentTrendIn,
    output=AgentTrendOut,
    permission="agent.view",
    read_only=True,
    module="agent_performance",
    example={"product": "agent_service"},
)
def trend(params: AgentTrendIn, ctx: ActionContext) -> AgentTrendOut:
    view = in_view(
        ctx, params.product, params.window, params.as_of, params.region_code, params.branch_code
    )
    metric = _metric(view, params.product, "trend", params.metric_code)
    agents = view.measured_on(metric) if metric else []
    pacer, found = (
        views.series(ctx.org_id, params.product, view.window, view.settings, metric, agents)
        if metric
        else (Pacer(ctx.org_id, params.product, view.window, view.settings), [])
    )
    t = views.trend(pacer, metric, found) if metric else None
    return AgentTrendOut(
        product=params.product,
        window=_window_out(pacer),
        metric=_key_out(metric) if metric else None,
        additive=metric is not None and metric.aggregation in ("sum", "count"),
        agents=len(agents),
        reported=t.reported if t else 0,
        currency_code=t.currency_code if t else None,
        mixed_currency=t.mixed_currency if t else False,
        points=[
            AgentTrendPointOut(
                day=p.day,
                working=p.working,
                value=p.value,
                reported=p.reported,
                cumulative=p.cumulative,
                expected=p.expected,
                target=p.target,
            )
            for p in (t.points if t else [])
        ],
        visibility=visibility_out(view.seen),
    )


# ── agent.heatmap ────────────────────────────────────────────────────────────


class AgentHeatmapIn(_Read):
    level: Literal["region", "branch", "rm"] = "branch"

    @model_validator(mode="after")
    def _path(self) -> AgentHeatmapIn:
        if self.level == "rm" and self.branch_code is None and self.region_code is None:
            raise ValueError("an RM heatmap narrows to a region_code or branch_code")
        return self


class AgentHeatCellOut(BaseModel):
    # Null when nobody in the row reported that day (absent, not zero).
    value: Decimal | None
    achieved: Decimal | None
    rag: Literal["green", "amber", "red"] | None
    reported: int


class AgentHeatRowOut(BaseModel):
    key: str
    name: str
    region_code: str | None
    branch_code: str | None
    agents: int
    cells: list[AgentHeatCellOut]


class AgentHeatmapOut(BaseModel):
    product: str
    window: WindowOut
    metric: RankKeyOut | None
    level: str
    days: list[date]
    # Per day, on the org-wide calendar.
    working: list[bool]
    rows: list[AgentHeatRowOut]
    currency_code: str | None
    mixed_currency: bool
    visibility: VisibilityOut


@action(
    name="agent.heatmap",
    summary="Regions, branches or RMs by day: each day's figure against its target, with RAG.",
    schema=AgentHeatmapIn,
    output=AgentHeatmapOut,
    permission="agent.view",
    read_only=True,
    module="agent_performance",
    example={"product": "agent_service", "level": "branch"},
)
def heat(params: AgentHeatmapIn, ctx: ActionContext) -> AgentHeatmapOut:
    view = in_view(
        ctx, params.product, params.window, params.as_of, params.region_code, params.branch_code
    )
    if (params.region_code or params.branch_code) and not view.agents:
        raise NotFound(f"No {params.product} agents in view there on {view.as_of}.")
    metric = _metric(view, params.product, "heatmap", params.metric_code)
    agents = view.measured_on(metric) if metric else []
    if metric:
        pacer, found = views.series(
            ctx.org_id, params.product, view.window, view.settings, metric, agents
        )
    else:
        pacer, found = Pacer(ctx.org_id, params.product, view.window, view.settings), []
    if params.level == "rm":
        names = {a.subject_id: a.full_name for a in agents}
    else:
        names = dict(
            DimMember.objects.filter(org_id=ctx.org_id, dimension_type=params.level).values_list(
                "member_code", "member_name"
            )
        )
    h = views.heatmap(pacer, metric, found, params.level, names) if metric else None
    return AgentHeatmapOut(
        product=params.product,
        window=_window_out(pacer),
        metric=_key_out(metric) if metric else None,
        level=params.level,
        days=h.days if h else views.to_date_days(view.window),
        working=h.working if h else [],
        rows=[
            AgentHeatRowOut(
                key=r.key,
                name=r.name,
                region_code=r.region_code,
                branch_code=r.branch_code,
                agents=r.agents,
                cells=[
                    AgentHeatCellOut(
                        value=c.value, achieved=c.achieved, rag=c.rag, reported=c.reported
                    )
                    for c in r.cells
                ],
            )
            for r in (h.rows if h else [])
        ],
        currency_code=h.currency_code if h else None,
        mixed_currency=h.mixed_currency if h else False,
        visibility=visibility_out(view.seen),
    )


# ── agent.distribution ───────────────────────────────────────────────────────


class AgentDistributionIn(_Read):
    pass


class AgentBinOut(BaseModel):
    low: Decimal
    # Inclusive for the last bin only.
    high: Decimal
    agents: int
    on_target: int


class AgentDistributionOut(BaseModel):
    product: str
    window: WindowOut
    metric: RankKeyOut | None
    agents: int
    # Agents with a figure to date; the rest are not in any bin.
    reported: int
    bins: list[AgentBinOut]
    target: Decimal | None
    median: Decimal | None
    currency_code: str | None
    mixed_currency: bool
    visibility: VisibilityOut


@action(
    name="agent.distribution",
    summary="How the agents in view spread on one metric to date, in equal-width bins.",
    schema=AgentDistributionIn,
    output=AgentDistributionOut,
    permission="agent.view",
    read_only=True,
    module="agent_performance",
    example={"product": "agent_service"},
)
def distribution(params: AgentDistributionIn, ctx: ActionContext) -> AgentDistributionOut:
    view = in_view(
        ctx, params.product, params.window, params.as_of, params.region_code, params.branch_code
    )
    metric = _metric(view, params.product, "distribution", params.metric_code)
    agents = view.measured_on(metric) if metric else []
    if metric:
        pacer, found = views.series(
            ctx.org_id, params.product, view.window, view.settings, metric, agents
        )
    else:
        pacer, found = Pacer(ctx.org_id, params.product, view.window, view.settings), []
    d = views.distribution(pacer, metric, found) if metric else None
    return AgentDistributionOut(
        product=params.product,
        window=_window_out(pacer),
        metric=_key_out(metric) if metric else None,
        agents=len(agents),
        reported=d.reported if d else 0,
        bins=[
            AgentBinOut(low=b.low, high=b.high, agents=b.agents, on_target=b.on_target)
            for b in (d.bins if d else [])
        ],
        target=d.target if d else None,
        median=d.median if d else None,
        currency_code=d.currency_code if d else None,
        mixed_currency=d.mixed_currency if d else False,
        visibility=visibility_out(view.seen),
    )
