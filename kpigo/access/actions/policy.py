"""Maker-checker per action class (PRD AD-3): configurable, off by default (budgets on)."""

from __future__ import annotations

from django.conf import settings
from django.utils import timezone
from pydantic import BaseModel

from kpigo.action import ActionContext, action
from kpigo.platform.models import APPROVAL_CLASSES, APPROVAL_DEFAULT_ON, ApprovalPolicy


class ApprovalPolicyOut(BaseModel):
    approval_class: str
    enabled: bool
    forced_by_install: bool


class ApprovalPolicyListIn(BaseModel):
    pass


class ApprovalPolicyListOut(BaseModel):
    policies: list[ApprovalPolicyOut]


def _policies(org_id: str) -> list[ApprovalPolicyOut]:
    stored = dict(
        ApprovalPolicy.objects.filter(org_id=org_id).values_list("approval_class", "enabled")
    )
    forced = set(getattr(settings, "KPIGO_APPROVAL_CLASSES_ENABLED", ()))
    return [
        ApprovalPolicyOut(
            approval_class=c,
            enabled=c in forced or bool(stored.get(c, c in APPROVAL_DEFAULT_ON)),
            forced_by_install=c in forced,
        )
        for c in APPROVAL_CLASSES
    ]


@action(
    name="approval.policy.list",
    summary="Which action classes need a second Admin's approval.",
    schema=ApprovalPolicyListIn,
    output=ApprovalPolicyListOut,
    permission="approval.policy.manage",
    read_only=True,
    http={"method": "GET", "path": "/approval-policies"},
    example={},
)
def list_policies(params: ApprovalPolicyListIn, ctx: ActionContext) -> ApprovalPolicyListOut:
    return ApprovalPolicyListOut(policies=_policies(ctx.org_id))


class SetIn(BaseModel):
    approval_class: str
    enabled: bool


@action(
    name="approval.policy.set",
    summary="Turn maker-checker on or off for an action class.",
    schema=SetIn,
    output=ApprovalPolicyListOut,
    permission="approval.policy.manage",
    read_only=False,
    # Switching a control off is itself a configuration change a checker sees.
    requires_approval="config_change",
    audit="approval.policy.set",
    example={"approval_class": "access_change", "enabled": True},
)
def set_policy(params: SetIn, ctx: ActionContext) -> ApprovalPolicyListOut:
    from kpigo.action import InvalidInput

    if params.approval_class not in APPROVAL_CLASSES:
        raise InvalidInput(
            f"Unknown action class '{params.approval_class}'.",
            detail={"classes": list(APPROVAL_CLASSES)},
        )
    ApprovalPolicy.objects.update_or_create(
        org_id=ctx.org_id,
        approval_class=params.approval_class,
        defaults={
            "enabled": params.enabled,
            "updated_by": ctx.user_id,
            "updated_at": timezone.now(),
        },
        create_defaults={
            "enabled": params.enabled,
            "created_by": ctx.user_id,
            "updated_by": ctx.user_id,
        },
    )
    return ApprovalPolicyListOut(policies=_policies(ctx.org_id))
