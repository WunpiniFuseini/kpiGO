"""Maker-checker decisions. Approving replays the stored payload through the same action."""

from django.db import transaction
from django.utils import timezone
from pydantic import BaseModel, Field

from kpigo.action import ActionContext, ApprovalGrant, Conflict, NotFound, action, invoke
from kpigo.action import registry as action_registry
from kpigo.action.identity import build_context
from kpigo.platform.models import ApprovalRequest


class ApprovalDecisionIn(BaseModel):
    approval_request_id: str = Field(min_length=1)
    reason: str = Field(default="", max_length=2000)


class ApprovalDecisionOut(BaseModel):
    approval_request_id: str
    action_name: str
    status: str
    result: dict[str, object] | None = None


def _load_pending(request_id: str, ctx: ActionContext) -> ApprovalRequest:
    try:
        request = ApprovalRequest.objects.select_for_update().get(
            request_id=request_id, org_id=ctx.org_id
        )
    except (ApprovalRequest.DoesNotExist, ValueError):
        raise NotFound("No such approval request.") from None
    if request.status != ApprovalRequest.Status.PENDING:
        raise Conflict(f"Approval request is already {request.status}.")
    if request.requested_by_id is not None and request.requested_by_id == ctx.user_id:
        raise Conflict("The maker of a request cannot also be its checker.")
    return request


@action(
    name="platform.approval.approve",
    summary="Approve a pending request and execute it with the identical payload.",
    schema=ApprovalDecisionIn,
    output=ApprovalDecisionOut,
    permission="platform.approval.decide",
    read_only=False,
    audit="approval.approved",
    example={"approval_request_id": "00000000-0000-0000-0000-000000000000"},
)
def approve(params: ApprovalDecisionIn, ctx: ActionContext) -> ApprovalDecisionOut:
    from django.contrib.auth import get_user_model

    with transaction.atomic():
        request = _load_pending(params.approval_request_id, ctx)
        target = action_registry.get(request.action_name)
        maker = (
            get_user_model()._default_manager.filter(pk=request.requested_by_id).first()
            if request.requested_by_id is not None
            else None
        )
        # The maker's own rights are re-checked at execution: approval does not
        # grant anything the maker no longer holds.
        maker_ctx = build_context(
            maker,
            caller=request.caller,  # type: ignore[arg-type]
            request_id=ctx.request_id,
            approval=ApprovalGrant(
                approval_request_id=str(request.request_id),
                action_name=request.action_name,
                approved_by_id=ctx.user_id,
            ),
        )
        output = invoke(target, request.payload, maker_ctx)
        now = timezone.now()
        request.status = ApprovalRequest.Status.APPROVED
        request.approved_by_id = ctx.user_id
        request.approved_at = now
        request.executed_at = now
        request.result = output.model_dump(mode="json")
        request.save()
    return ApprovalDecisionOut(
        approval_request_id=str(request.request_id),
        action_name=request.action_name,
        status=request.status,
        result=request.result,
    )


@action(
    name="platform.approval.reject",
    summary="Reject a pending request. Nothing is executed.",
    schema=ApprovalDecisionIn,
    output=ApprovalDecisionOut,
    permission="platform.approval.decide",
    read_only=False,
    audit="approval.rejected",
    example={"approval_request_id": "00000000-0000-0000-0000-000000000000", "reason": "No"},
)
def reject(params: ApprovalDecisionIn, ctx: ActionContext) -> ApprovalDecisionOut:
    request = _load_pending(params.approval_request_id, ctx)
    request.status = ApprovalRequest.Status.REJECTED
    request.approved_by_id = ctx.user_id
    request.approved_at = timezone.now()
    request.rejection_reason = params.reason
    request.save()
    return ApprovalDecisionOut(
        approval_request_id=str(request.request_id),
        action_name=request.action_name,
        status=request.status,
    )
