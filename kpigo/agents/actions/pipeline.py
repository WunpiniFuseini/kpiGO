"""The sales pipeline as actions (Scope §8.2; Starter Packs: the Sales preset).

``pipeline.stage.*`` lets an Admin build the funnel from snapshot metrics the
daily feed already carries; ``agent.pipeline`` reads it for the agents in view,
drilling region → branch → RM.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from django.utils import timezone
from pydantic import BaseModel, Field, model_validator

from kpigo.action import ActionContext, InvalidInput, NotFound, action
from kpigo.agents import pipeline as pl
from kpigo.agents.actions.matrix import CrumbOut
from kpigo.agents.actions.pace import MetricCode, VisibilityOut, WindowOut, visibility_out
from kpigo.agents.actions.presets import in_view, pacer_window_out
from kpigo.agents.config import AgentProduct
from kpigo.agents.daily import Pacer, in_force
from kpigo.agents.models import PipelineStage
from kpigo.hierarchy.models import DimMember
from kpigo.metrics.models import Metric
from kpigo.platform.vocab import Code

# ── stages ───────────────────────────────────────────────────────────────────


class PipelineStageOut(BaseModel):
    code: str
    display_name: str
    sort_order: int
    value_metric_code: str | None
    count_metric_code: str | None


def _stage_out(s: PipelineStage) -> PipelineStageOut:
    return PipelineStageOut(
        code=s.code,
        display_name=s.display_name,
        sort_order=s.sort_order,
        value_metric_code=s.value_metric_code,
        count_metric_code=s.count_metric_code,
    )


def stages_for(org_id: str, product: str) -> list[PipelineStage]:
    return list(
        PipelineStage.objects.filter(org_id=org_id, product=product).order_by("sort_order", "code")
    )


class PipelineMetricOut(BaseModel):
    metric_code: str
    display_name: str
    unit: str


class PipelineStageListIn(BaseModel):
    product: AgentProduct


class PipelineStageListOut(BaseModel):
    product: str
    stages: list[PipelineStageOut]
    # Snapshot (``latest``) metrics bound to the module: what a stage can read.
    candidates: list[PipelineMetricOut]


def _candidates(org_id: str, product: str) -> list[Metric]:
    today = timezone.localdate()
    return list(
        Metric.objects.filter(
            org_id=org_id,
            status="active",
            aggregation="latest",
            bindings__product=product,
            bindings__is_active=True,
        )
        .filter(in_force(today))
        .order_by("display_name", "metric_code")
        .distinct()
    )


@action(
    name="pipeline.stage.list",
    summary="A module's pipeline stages in order, and the snapshot metrics a stage can read.",
    schema=PipelineStageListIn,
    output=PipelineStageListOut,
    permission="agent.view",
    read_only=True,
    module="agent_performance",
    example={"product": "agent_sales"},
)
def list_stages(params: PipelineStageListIn, ctx: ActionContext) -> PipelineStageListOut:
    return PipelineStageListOut(
        product=params.product,
        stages=[_stage_out(s) for s in stages_for(ctx.org_id, params.product)],
        candidates=[
            PipelineMetricOut(metric_code=m.metric_code, display_name=m.display_name, unit=m.unit)
            for m in _candidates(ctx.org_id, params.product)
        ],
    )


class PipelineStageSetIn(BaseModel):
    product: AgentProduct
    code: Code
    display_name: str = Field(min_length=1, max_length=80)
    # Defaults to after the last stage.
    sort_order: int | None = None
    # What sits in the stage on a day: its value, its number of deals, or both.
    value_metric_code: MetricCode | None = None
    count_metric_code: MetricCode | None = None

    @model_validator(mode="after")
    def _one(self) -> PipelineStageSetIn:
        if self.value_metric_code is None and self.count_metric_code is None:
            raise ValueError("a stage reads a value metric, a count metric, or both")
        return self


@action(
    name="pipeline.stage.set",
    summary="Add or change a pipeline stage: its name, place, and the snapshot metrics it reads.",
    schema=PipelineStageSetIn,
    output=PipelineStageOut,
    permission="agent.config.manage",
    read_only=False,
    module="agent_performance",
    requires_approval="config_change",
    audit="pipeline.stage_set",
    config_change=True,
    example={
        "product": "agent_sales",
        "code": "proposal",
        "display_name": "Proposal",
        "value_metric_code": "pipeline_proposal_value",
    },
)
def set_stage(params: PipelineStageSetIn, ctx: ActionContext) -> PipelineStageOut:
    allowed = {m.metric_code: m for m in _candidates(ctx.org_id, params.product)}
    for field_name in ("value_metric_code", "count_metric_code"):
        code = getattr(params, field_name)
        if code is not None and code not in allowed:
            raise InvalidInput(
                f"'{code}' is not a snapshot metric bound to {params.product}. A stage reads "
                "an active metric with aggregation 'latest' that the daily feed carries.",
                detail={"field": field_name, "metric_codes": sorted(allowed)},
            )
    if params.count_metric_code is not None and allowed[params.count_metric_code].unit != "count":
        raise InvalidInput(f"'{params.count_metric_code}' is not measured in count.")
    existing = stages_for(ctx.org_id, params.product)
    order = params.sort_order
    if order is None:
        mine = next((s for s in existing if s.code == params.code), None)
        order = mine.sort_order if mine else (max((s.sort_order for s in existing), default=0) + 10)
    row, created = PipelineStage.objects.get_or_create(
        org_id=ctx.org_id,
        product=params.product,
        code=params.code,
        defaults={
            "display_name": params.display_name,
            "sort_order": order,
            "value_metric_code": params.value_metric_code,
            "count_metric_code": params.count_metric_code,
            "created_by": ctx.user_id,
            "updated_by": ctx.user_id,
        },
    )
    if not created:
        row.display_name = params.display_name
        row.sort_order = order
        row.value_metric_code = params.value_metric_code
        row.count_metric_code = params.count_metric_code
        row.updated_by = ctx.user_id
        row.updated_at = timezone.now()
        row.save()
    return _stage_out(row)


class PipelineStageRemoveIn(BaseModel):
    product: AgentProduct
    code: Code


class PipelineStageRemoveOut(BaseModel):
    removed: bool


@action(
    name="pipeline.stage.remove",
    summary="Take a stage out of the funnel. The facts it read stay where they are.",
    schema=PipelineStageRemoveIn,
    output=PipelineStageRemoveOut,
    permission="agent.config.manage",
    read_only=False,
    module="agent_performance",
    requires_approval="config_change",
    audit="pipeline.stage_remove",
    config_change=True,
    example={"product": "agent_sales", "code": "proposal"},
)
def remove_stage(params: PipelineStageRemoveIn, ctx: ActionContext) -> PipelineStageRemoveOut:
    deleted, _ = PipelineStage.objects.filter(
        org_id=ctx.org_id, product=params.product, code=params.code
    ).delete()
    if not deleted:
        raise NotFound(f"No pipeline stage '{params.code}' in {params.product}.")
    return PipelineStageRemoveOut(removed=True)


# ── agent.pipeline ───────────────────────────────────────────────────────────


class AgentPipelineIn(BaseModel):
    product: AgentProduct
    as_of: date | None = None
    window: Literal["month", "week"] | None = None
    level: Literal["region", "branch", "rm"] = "region"
    region_code: Code | None = None
    branch_code: Code | None = None

    @model_validator(mode="after")
    def _path(self) -> AgentPipelineIn:
        if self.level == "branch" and self.region_code is None:
            raise ValueError("a branch level drills from a region_code")
        if self.level == "rm" and self.branch_code is None:
            raise ValueError("an RM level drills from a branch_code")
        return self


class PipelineCellOut(BaseModel):
    # Null when nobody in the row has a snapshot (absent, not zero).
    value: Decimal | None
    count: Decimal | None
    # Since each agent's first snapshot in the window.
    value_change: Decimal | None
    count_change: Decimal | None
    reported: int
    # Out of 1.0, against the stage before; null on the first stage.
    conversion: Decimal | None
    currency_code: str | None
    mixed_currency: bool


class PipelineRowOut(BaseModel):
    key: str
    name: str
    level: Literal["region", "branch", "rm", "total"]
    drill_level: Literal["branch", "rm"] | None
    region_code: str | None
    branch_code: str | None
    agents: int
    cells: list[PipelineCellOut]


class AgentPipelineOut(BaseModel):
    product: str
    window: WindowOut
    level: str
    breadcrumb: list[CrumbOut]
    stages: list[PipelineStageOut]
    rows: list[PipelineRowOut]
    total: PipelineRowOut | None
    visibility: VisibilityOut


def _row_out(r: pl.Row) -> PipelineRowOut:
    drill: Literal["branch", "rm"] | None = (
        "branch" if r.level == "region" else "rm" if r.level == "branch" else None
    )
    return PipelineRowOut(
        key=r.key,
        name=r.name,
        level=r.level,
        drill_level=drill,
        region_code=r.region_code,
        branch_code=r.branch_code,
        agents=r.agents,
        cells=[
            PipelineCellOut(
                value=c.value,
                count=c.count,
                value_change=c.value_change,
                count_change=c.count_change,
                reported=c.reported,
                conversion=c.conversion,
                currency_code=c.currency_code,
                mixed_currency=c.mixed_currency,
            )
            for c in r.cells
        ],
    )


@action(
    name="agent.pipeline",
    summary="The sales pipeline by stage for the agents in view, drilling region → branch → RM.",
    schema=AgentPipelineIn,
    output=AgentPipelineOut,
    permission="agent.view",
    read_only=True,
    module="agent_performance",
    example={"product": "agent_sales", "level": "region"},
)
def pipeline(params: AgentPipelineIn, ctx: ActionContext) -> AgentPipelineOut:
    region = params.region_code if params.level != "region" else None
    branch = params.branch_code if params.level == "rm" else None
    view = in_view(ctx, params.product, params.window, params.as_of, region, branch)
    if params.level != "region" and not view.agents:
        raise NotFound(f"No {params.product} agents in view there on {view.as_of}.")
    names = dict(
        DimMember.objects.filter(
            org_id=ctx.org_id, dimension_type__in=("region", "branch")
        ).values_list("member_code", "member_name")
    )
    crumbs = [CrumbOut(level="all", code=None, name="All regions")]
    if region is not None:
        crumbs.append(CrumbOut(level="region", code=region, name=names.get(region, region)))
    if branch is not None:
        if region is None:
            found = next((a.region_code for a in view.agents if a.region_code), None)
            if found:
                crumbs.append(CrumbOut(level="region", code=found, name=names.get(found, found)))
        crumbs.append(CrumbOut(level="branch", code=branch, name=names.get(branch, branch)))
    stages = stages_for(ctx.org_id, params.product)
    if params.level == "rm":
        names = {a.subject_id: a.full_name for a in view.agents}
    built = (
        pl.build(ctx.org_id, view.window, stages, view.agents, params.level, names)
        if stages
        else pl.Pipeline(rows=[], total=None)
    )
    return AgentPipelineOut(
        product=params.product,
        window=pacer_window_out(Pacer(ctx.org_id, params.product, view.window, view.settings)),
        level=params.level,
        breadcrumb=crumbs,
        stages=[_stage_out(s) for s in stages],
        rows=[_row_out(r) for r in built.rows],
        total=_row_out(built.total) if built.total else None,
        visibility=visibility_out(view.seen),
    )
