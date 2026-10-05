"""Objectives: the default window each one opens and the outcome metrics it counts.

Attribution credits an outcome only when its metric is registered for the
campaign's objective (Scope §9.2, rule 4). The Banking pack (Starter Packs §6.1)
supplies the windows; an Admin names the outcome metrics, which are registry
metrics bound to the ``campaign`` product like any other.
"""

from __future__ import annotations

from django.utils import timezone
from pydantic import BaseModel, Field

from kpigo.access.identity import in_force
from kpigo.action import ActionContext, InvalidInput, action
from kpigo.campaigns.actions.campaigns import Objective
from kpigo.campaigns.authoring import DEFAULT_WINDOWS
from kpigo.campaigns.models import OBJECTIVES, CampaignObjective
from kpigo.metrics.actions.metric import MetricCode
from kpigo.metrics.models import Metric


class CampaignOutcomeMetricOut(BaseModel):
    metric_code: str
    display_name: str
    unit: str


class CampaignObjectiveOut(BaseModel):
    objective: str
    default_window_days: int
    outcome_metric_codes: list[str]
    # False while the objective runs on the pack's defaults.
    configured: bool


class CampaignObjectiveListIn(BaseModel):
    pass


class CampaignObjectiveListOut(BaseModel):
    objectives: list[CampaignObjectiveOut]
    # Active metrics bound to the campaign product: what an objective can count.
    candidates: list[CampaignOutcomeMetricOut]


def outcome_candidates(org_id: str) -> list[Metric]:
    return list(
        Metric.objects.filter(
            org_id=org_id,
            status="active",
            bindings__product="campaign",
            bindings__is_active=True,
        )
        .filter(in_force(timezone.localdate()))
        .order_by("display_name", "metric_code")
        .distinct()
    )


def objectives_for(org_id: str) -> list[CampaignObjectiveOut]:
    stored = {o.objective: o for o in CampaignObjective.objects.filter(org_id=org_id)}
    out = []
    for objective in OBJECTIVES:
        row = stored.get(objective)
        out.append(
            CampaignObjectiveOut(
                objective=objective,
                default_window_days=row.default_window_days
                if row
                else DEFAULT_WINDOWS.get(objective, 30),
                outcome_metric_codes=list(row.outcome_metric_codes) if row else [],
                configured=row is not None,
            )
        )
    return out


def _list(org_id: str) -> CampaignObjectiveListOut:
    return CampaignObjectiveListOut(
        objectives=objectives_for(org_id),
        candidates=[
            CampaignOutcomeMetricOut(
                metric_code=m.metric_code, display_name=m.display_name, unit=m.unit
            )
            for m in outcome_candidates(org_id)
        ],
    )


@action(
    name="campaign.objective.list",
    summary="Each objective's default attribution window and the outcome metrics it counts.",
    schema=CampaignObjectiveListIn,
    output=CampaignObjectiveListOut,
    permission="campaign.view",
    read_only=True,
    module="campaign",
    example={},
)
def list_objectives(
    params: CampaignObjectiveListIn, ctx: ActionContext
) -> CampaignObjectiveListOut:
    return _list(ctx.org_id)


class CampaignObjectiveSetIn(BaseModel):
    objective: Objective
    default_window_days: int = Field(ge=0, le=730)
    outcome_metric_codes: list[MetricCode] = Field(default_factory=list, max_length=20)


@action(
    name="campaign.objective.set",
    summary="Set an objective's default window and the outcome metrics attribution counts for it.",
    schema=CampaignObjectiveSetIn,
    output=CampaignObjectiveListOut,
    permission="campaign.config.manage",
    read_only=False,
    module="campaign",
    requires_approval="config_change",
    audit="campaign.objective.set",
    config_change=True,
    example={
        "objective": "deposit_growth",
        "default_window_days": 30,
        "outcome_metric_codes": ["cmp_deposit_value"],
    },
)
def set_objective(params: CampaignObjectiveSetIn, ctx: ActionContext) -> CampaignObjectiveListOut:
    allowed = {m.metric_code for m in outcome_candidates(ctx.org_id)}
    unknown = sorted(set(params.outcome_metric_codes) - allowed)
    if unknown:
        raise InvalidInput(
            f"Not active metrics bound to the campaign product: {', '.join(unknown)}. "
            "Register them in the metric registry with the campaign product first.",
            detail={"field": "outcome_metric_codes", "metric_codes": sorted(allowed)},
        )
    codes = list(dict.fromkeys(params.outcome_metric_codes))
    CampaignObjective.objects.update_or_create(
        org_id=ctx.org_id,
        objective=params.objective,
        defaults={
            "default_window_days": params.default_window_days,
            "outcome_metric_codes": codes,
            "updated_by": ctx.user_id,
            "updated_at": timezone.now(),
        },
        create_defaults={
            "default_window_days": params.default_window_days,
            "outcome_metric_codes": codes,
            "created_by": ctx.user_id,
            "updated_by": ctx.user_id,
        },
    )
    return _list(ctx.org_id)
