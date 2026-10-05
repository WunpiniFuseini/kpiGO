"""The product-line matrix as an action (App Flow §4.1 section 2; PRD AP-5, AP-9, AP-11).

``agent.matrix`` returns one level of the drill (regions, a region's branches, or
a branch's RMs) for one additive metric: each row × each line (or product group)
with actual, target to date and % achieved. The expanded or grouped view is the
reader's own preference (``preference.set`` ``agent.matrix.view``) unless the call
names one. Open by default (AP-7), like the leaderboard.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, model_validator

from kpigo.access.identity import app_user_for
from kpigo.access.preferences import preference
from kpigo.action import ActionContext, InvalidInput, NotFound, action
from kpigo.agents import matrix as mx
from kpigo.agents.actions.leaderboard import RankKeyOut
from kpigo.agents.actions.pace import MetricCode, WindowOut, resolve_window
from kpigo.agents.config import AgentProduct, settings_for
from kpigo.agents.daily import Pacer, agents_on, window_for
from kpigo.agents.lines import lines_between
from kpigo.agents.pace import ADDITIVE
from kpigo.hierarchy.models import DimMember, ProductLine
from kpigo.metrics.models import Metric
from kpigo.platform.vocab import Code


class MatrixIn(BaseModel):
    product: AgentProduct
    as_of: date | None = None
    window: Literal["month", "week"] | None = None
    # An additive (sum or count) metric; defaults to the module's ranking metric.
    metric_code: MetricCode | None = None
    level: Literal["region", "branch", "rm"] = "region"
    # The drill path: a region for its branches, a branch for its RMs.
    region_code: Code | None = None
    branch_code: Code | None = None
    # Defaults to the reader's saved preference.
    view: Literal["expanded", "grouped"] | None = None

    @model_validator(mode="after")
    def _path(self) -> MatrixIn:
        if self.level == "branch" and self.region_code is None:
            raise ValueError("a branch level drills from a region_code")
        if self.level == "rm" and self.branch_code is None:
            raise ValueError("an RM level drills from a branch_code")
        return self


class MatrixColumnOut(BaseModel):
    # A line code, a product group code, or "" for All products.
    key: str
    kind: Literal["line", "group", "all"]
    name: str
    group_code: str | None


class MatrixCellOut(BaseModel):
    # Null when no agent in the row reported on this column (absent, not zero).
    actual: Decimal | None
    # What the targets expect by the as-of day, over agents that reported.
    target: Decimal | None
    # Out of 1.0, from the sums; null with no target.
    achieved: Decimal | None
    rag: Literal["green", "amber", "red"] | None
    agents: int
    reported: int
    currency_code: str | None
    # Agents' targets are in different currencies: no sum is shown.
    mixed_currency: bool


class MatrixRowOut(BaseModel):
    key: str
    name: str
    level: Literal["region", "branch", "rm", "total"]
    # Where clicking the row drills to; null at the RM level and on the total.
    drill_level: Literal["branch", "rm"] | None
    region_code: str | None
    branch_code: str | None
    agents: int
    cells: list[MatrixCellOut]


class CrumbOut(BaseModel):
    level: Literal["all", "region", "branch"]
    code: str | None
    name: str


class MatrixOut(BaseModel):
    product: str
    window: WindowOut
    metric: RankKeyOut | None
    # Additive metrics the module's agents are measured on.
    metric_options: list[RankKeyOut]
    level: str
    view: str
    breadcrumb: list[CrumbOut]
    columns: list[MatrixColumnOut]
    rows: list[MatrixRowOut]
    total: MatrixRowOut | None
    # No line is switched on: only All products shows.
    no_lines: bool


def _key_out(m: Metric) -> RankKeyOut:
    return RankKeyOut(
        key=m.metric_code,
        display_name=m.display_name,
        unit=m.unit,
        decimal_places=m.decimal_places,
        direction=m.direction,
    )


def _row_out(r: mx.Row) -> MatrixRowOut:
    drill: Literal["branch", "rm"] | None = (
        "branch" if r.level == "region" else "rm" if r.level == "branch" else None
    )
    return MatrixRowOut(
        key=r.key,
        name=r.name,
        level=r.level,
        drill_level=drill,
        region_code=r.region_code,
        branch_code=r.branch_code,
        agents=r.agents,
        cells=[
            MatrixCellOut(
                actual=c.actual,
                target=c.target,
                achieved=c.achieved,
                rag=c.rag,
                agents=c.agents,
                reported=c.reported,
                currency_code=c.currency_code,
                mixed_currency=c.mixed_currency,
            )
            for c in r.cells
        ],
    )


@action(
    name="agent.matrix",
    summary="Product-line matrix: actual, target and % achieved per line, drilling region → RM.",
    schema=MatrixIn,
    output=MatrixOut,
    permission="agent.view",
    read_only=True,
    module="agent_performance",
    example={"product": "agent_sales", "level": "region"},
)
def matrix(params: MatrixIn, ctx: ActionContext) -> MatrixOut:
    s = settings_for(ctx.org_id, params.product)
    kind, as_of = resolve_window(ctx, params.product, params.window, params.as_of)
    window = window_for(kind, as_of)
    account = app_user_for(ctx.user, ctx.org_id)
    view: mx.View = params.view or (
        "grouped" if preference(account, "agent.matrix.view") == "grouped" else "expanded"
    )

    agents, by_profile = agents_on(ctx.org_id, params.product, as_of)
    additive = {
        m.metric_code: m for ms in by_profile.values() for m in ms if m.aggregation in ADDITIVE
    }
    options = [
        _key_out(m)
        for m in sorted(additive.values(), key=lambda m: (m.display_name.lower(), m.metric_code))
    ]
    code = params.metric_code or (s.rank_metric_code if s.rank_metric_code in additive else None)
    if params.metric_code is not None and code not in additive:
        raise InvalidInput(
            f"'{params.metric_code}' is not an additive {params.product} metric on {as_of}; "
            "the matrix sums agents' figures.",
            detail={"metric_codes": sorted(additive)},
        )
    if code is None and options:
        code = options[0].key
    metric = additive.get(code) if code else None

    # The drill path narrows the agents; names come from the dimension members.
    names = dict(
        DimMember.objects.filter(
            org_id=ctx.org_id, dimension_type__in=("region", "branch")
        ).values_list("member_code", "member_name")
    )
    breadcrumb = [CrumbOut(level="all", code=None, name="All regions")]
    scoped = list(agents)
    if params.level in ("branch", "rm") and params.region_code is not None:
        scoped = [a for a in scoped if a.region_code == params.region_code]
        breadcrumb.append(
            CrumbOut(
                level="region",
                code=params.region_code,
                name=names.get(params.region_code, params.region_code),
            )
        )
    if params.level == "rm" and params.branch_code is not None:
        scoped = [a for a in scoped if a.branch_code == params.branch_code]
        if params.region_code is None:
            region = next((a.region_code for a in scoped if a.region_code), None)
            if region:
                breadcrumb.append(
                    CrumbOut(level="region", code=region, name=names.get(region, region))
                )
        breadcrumb.append(
            CrumbOut(
                level="branch",
                code=params.branch_code,
                name=names.get(params.branch_code, params.branch_code),
            )
        )
    if params.level != "region" and not scoped:
        place = params.branch_code if params.level == "rm" else params.region_code
        raise NotFound(f"No {params.product} agents in '{place}' on {as_of}.")
    if metric is not None:
        scoped = [
            a for a in scoped if any(m.metric_code == code for m in by_profile[a.profile_code])
        ]

    lines = lines_between(ctx.org_id, window.start, as_of, params.product)
    line_rag = {
        code: (green, amber)
        for code, green, amber in ProductLine.objects.filter(
            org_id=ctx.org_id, code__in=[line.code for line in lines]
        ).values_list("code", "rag_green", "rag_amber")
    }
    row_names = {a.subject_id: a.full_name for a in scoped} if params.level == "rm" else names
    built = (
        mx.build(
            org_id=ctx.org_id,
            product=params.product,
            window=window,
            settings=s,
            metric=metric,
            agents=scoped,
            lines=lines,
            line_rag=line_rag,
            view=view,
            level=params.level,
            row_names=row_names,
        )
        if metric is not None
        else mx.Matrix(columns=mx.columns_for(lines, view, line_rag), rows=[], total=None)
    )
    pacer = Pacer(ctx.org_id, params.product, window, s)
    return MatrixOut(
        product=params.product,
        window=WindowOut(
            kind=window.kind,
            start=window.start,
            end=window.days[-1],
            as_of=window.as_of,
            working_day=pacer.working_days_elapsed,
            working_days=pacer.working_days_total,
        ),
        metric=_key_out(metric) if metric is not None else None,
        metric_options=options,
        level=params.level,
        view=view,
        breadcrumb=breadcrumb,
        columns=[
            MatrixColumnOut(key=c.key, kind=c.kind, name=c.name, group_code=c.group_code)
            for c in built.columns
        ],
        rows=[_row_out(r) for r in built.rows],
        total=_row_out(built.total) if built.total else None,
        no_lines=not lines,
    )
