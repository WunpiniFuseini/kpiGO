"""Who sees whom in Agent Performance, as actions (PRD AP-7).

The module is open by default. ``agent.visibility.set`` narrows a role, or the
agents of a profile, to their hierarchy subtree, their own region or branch, or
themselves; ``all`` keeps them open even when another of their rules narrows.
It is an access change, so it goes through approval when maker-checker is on.
"""

from __future__ import annotations

from typing import Literal

from django.utils import timezone
from pydantic import BaseModel

from kpigo.access.models import Role
from kpigo.action import ActionContext, InvalidInput, NotFound, action
from kpigo.action.roles import SYSTEM_ROLES
from kpigo.agents.config import AgentProduct
from kpigo.agents.models import AgentVisibilityRule
from kpigo.hierarchy.models import Assignment
from kpigo.metrics.models import MetricProfileAssignment
from kpigo.platform.vocab import Code

AppliesTo = Literal["role", "profile"]
Scope = Literal["all", "subtree", "region", "branch", "self"]


class VisibilityRuleOut(BaseModel):
    product: str
    applies_to: str
    applies_code: str
    scope: str


class VisibilityListIn(BaseModel):
    product: AgentProduct


class VisibilityListOut(BaseModel):
    product: str
    # No rules: everyone with the page sees every agent.
    rules: list[VisibilityRuleOut]


def _out(r: AgentVisibilityRule) -> VisibilityRuleOut:
    return VisibilityRuleOut(
        product=r.product, applies_to=r.applies_to, applies_code=r.applies_code, scope=r.scope
    )


@action(
    name="agent.visibility.list",
    summary="Who sees whom in an Agent Performance module: open unless a rule narrows it.",
    schema=VisibilityListIn,
    output=VisibilityListOut,
    permission="agent.config.manage",
    read_only=True,
    module="agent_performance",
    example={"product": "agent_sales"},
)
def list_rules(params: VisibilityListIn, ctx: ActionContext) -> VisibilityListOut:
    rows = AgentVisibilityRule.objects.filter(org_id=ctx.org_id, product=params.product).order_by(
        "applies_to", "applies_code"
    )
    return VisibilityListOut(product=params.product, rules=[_out(r) for r in rows])


class VisibilitySetIn(BaseModel):
    product: AgentProduct
    applies_to: AppliesTo
    # A role code, or a profile code the module measures.
    applies_code: Code
    scope: Scope


def _check(ctx: ActionContext, params: VisibilitySetIn) -> None:
    if params.applies_to == "role":
        known = (
            params.applies_code in SYSTEM_ROLES
            or Role.objects.filter(org_id=ctx.org_id, code=params.applies_code).exists()
        )
        if not known:
            raise NotFound(f"No role '{params.applies_code}'.")
        return
    measured = MetricProfileAssignment.objects.filter(
        product=params.product, profile_code=params.applies_code, metric__org_id=ctx.org_id
    ).exists()
    if not measured:
        assigned = Assignment.objects.filter(
            org_id=ctx.org_id, profile_code=params.applies_code
        ).exists()
        raise InvalidInput(
            f"Profile '{params.applies_code}' carries no {params.product} metrics."
            if assigned
            else f"No profile '{params.applies_code}'."
        )


@action(
    name="agent.visibility.set",
    summary="Narrow (or keep open) which agents a role or profile sees in a module.",
    schema=VisibilitySetIn,
    output=VisibilityRuleOut,
    permission="agent.config.manage",
    read_only=False,
    module="agent_performance",
    requires_approval="access_change",
    audit="agent.visibility_set",
    example={
        "product": "agent_sales",
        "applies_to": "role",
        "applies_code": "staff",
        "scope": "branch",
    },
)
def set_rule(params: VisibilitySetIn, ctx: ActionContext) -> VisibilityRuleOut:
    _check(ctx, params)
    row, created = AgentVisibilityRule.objects.get_or_create(
        org_id=ctx.org_id,
        product=params.product,
        applies_to=params.applies_to,
        applies_code=params.applies_code,
        defaults={"scope": params.scope, "created_by": ctx.user_id, "updated_by": ctx.user_id},
    )
    if not created and row.scope != params.scope:
        row.scope = params.scope
        row.updated_by = ctx.user_id
        row.updated_at = timezone.now()
        row.save()
    return _out(row)


class VisibilityClearIn(BaseModel):
    product: AgentProduct
    applies_to: AppliesTo
    applies_code: Code


class VisibilityClearOut(BaseModel):
    cleared: bool


@action(
    name="agent.visibility.clear",
    summary="Remove a visibility rule: that role or profile goes back to the module's default.",
    schema=VisibilityClearIn,
    output=VisibilityClearOut,
    permission="agent.config.manage",
    read_only=False,
    module="agent_performance",
    requires_approval="access_change",
    audit="agent.visibility_clear",
    example={"product": "agent_sales", "applies_to": "role", "applies_code": "staff"},
)
def clear_rule(params: VisibilityClearIn, ctx: ActionContext) -> VisibilityClearOut:
    deleted, _ = AgentVisibilityRule.objects.filter(
        org_id=ctx.org_id,
        product=params.product,
        applies_to=params.applies_to,
        applies_code=params.applies_code,
    ).delete()
    if not deleted:
        raise NotFound(
            f"No rule for {params.applies_to} '{params.applies_code}' in {params.product}."
        )
    return VisibilityClearOut(cleared=True)
