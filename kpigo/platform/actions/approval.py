"""Maker-checker decisions. Approving replays the stored payload through the same action."""

from datetime import datetime
from typing import Any, Literal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from pydantic import BaseModel, Field

from kpigo.action import ActionContext, ApprovalGrant, Conflict, NotFound, action, invoke
from kpigo.action import registry as action_registry
from kpigo.action.identity import build_context
from kpigo.action.pipeline import approval_enabled
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


def _execute(request: ApprovalRequest, ctx: ActionContext) -> ApprovalDecisionOut:
    """Replay the stored payload through the same action, as its maker."""
    from django.contrib.auth import get_user_model

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
    return _decision(request)


def _decision(request: ApprovalRequest) -> ApprovalDecisionOut:
    return ApprovalDecisionOut(
        approval_request_id=str(request.request_id),
        action_name=request.action_name,
        status=request.status,
        result=request.result,
    )


@action(
    name="platform.approval.approve",
    agent_forbidden=True,
    summary="Approve a pending request and execute it with the identical payload.",
    schema=ApprovalDecisionIn,
    output=ApprovalDecisionOut,
    permission="platform.approval.decide",
    read_only=False,
    audit="approval.approved",
    example={"approval_request_id": "00000000-0000-0000-0000-000000000000"},
)
def approve(params: ApprovalDecisionIn, ctx: ActionContext) -> ApprovalDecisionOut:
    with transaction.atomic():
        request = _load_pending(params.approval_request_id, ctx)
        return _execute(request, ctx)


@action(
    name="platform.approval.reject",
    agent_forbidden=True,
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
    return _decision(request)


# --- Proposals the assistant made, and the queue ------------------------------
#
# The assistant runs as its user (TDD §13), so a proposal it makes is that user's
# request. They may confirm it themselves (it is exactly what they could have run)
# unless maker-checker is on for that kind of change, when a second person decides
# through platform.approval.approve as for any other request.


def _own_pending(request_id: str, ctx: ActionContext) -> ApprovalRequest:
    try:
        request = ApprovalRequest.objects.select_for_update().get(
            request_id=request_id, org_id=ctx.org_id
        )
    except (ApprovalRequest.DoesNotExist, ValueError, DjangoValidationError):
        raise NotFound("No such approval request.") from None
    if ctx.user_id is None or request.requested_by_id != ctx.user_id:
        # Someone else's request is not theirs to see, let alone act on.
        raise NotFound("No such approval request.")
    if request.status != ApprovalRequest.Status.PENDING:
        raise Conflict(f"Approval request is already {request.status}.")
    return request


def needs_second_person(request: ApprovalRequest) -> bool:
    """Whether only a different checker may approve this request."""
    if request.caller != "agent":
        return True
    target = action_registry.find(request.action_name)
    if target is None:
        return True
    return target.requires_approval is not None and approval_enabled(
        target.requires_approval, str(request.org_id)
    )


@action(
    name="platform.approval.confirm",
    agent_forbidden=True,
    summary="Confirm a change your assistant proposed, running the identical payload as you.",
    schema=ApprovalDecisionIn,
    output=ApprovalDecisionOut,
    permission="approval.request.own",
    read_only=False,
    audit="approval.confirmed",
    example={"approval_request_id": "00000000-0000-0000-0000-000000000000"},
)
def confirm(params: ApprovalDecisionIn, ctx: ActionContext) -> ApprovalDecisionOut:
    with transaction.atomic():
        request = _own_pending(params.approval_request_id, ctx)
        if needs_second_person(request):
            raise Conflict("This change needs approval from a second person.")
        return _execute(request, ctx)


@action(
    name="platform.approval.withdraw",
    summary="Withdraw a request you made that is still pending. Nothing is executed.",
    schema=ApprovalDecisionIn,
    output=ApprovalDecisionOut,
    permission="approval.request.own",
    read_only=False,
    audit="approval.withdrawn",
    example={"approval_request_id": "00000000-0000-0000-0000-000000000000"},
)
def withdraw(params: ApprovalDecisionIn, ctx: ActionContext) -> ApprovalDecisionOut:
    request = _own_pending(params.approval_request_id, ctx)
    request.status = ApprovalRequest.Status.REJECTED
    request.approved_by_id = ctx.user_id
    request.approved_at = timezone.now()
    request.rejection_reason = params.reason or "Withdrawn by the requester."
    request.save()
    return _decision(request)


ApprovalStatus = Literal["pending", "approved", "rejected", "failed"]


class ApprovalListIn(BaseModel):
    status: ApprovalStatus | None = "pending"
    # Only the caller's own requests, even for someone who decides the org's.
    mine: bool = False
    limit: int = Field(default=100, ge=1, le=500)


class ApprovalRequestOut(BaseModel):
    approval_request_id: str
    action_name: str
    action_summary: str
    caller: str
    payload: dict[str, Any]
    status: str
    requested_by_id: int | None
    requested_by_name: str
    requested_at: datetime
    decided_by_name: str
    decided_at: datetime | None
    rejection_reason: str
    result: dict[str, Any] | None
    mine: bool
    # What this caller may do with it now.
    can_decide: bool
    can_confirm: bool
    can_withdraw: bool


class ApprovalListOut(BaseModel):
    requests: list[ApprovalRequestOut]
    # Whether the caller sees the whole org's queue or only their own requests.
    scope: Literal["org", "own"]


class ApprovalGetIn(BaseModel):
    approval_request_id: str = Field(min_length=1)


def _names(user_ids: set[int]) -> dict[int, str]:
    from kpigo.access.models import AppUser

    return dict(
        AppUser.objects.filter(auth_user_id__in=user_ids).values_list(
            "auth_user_id", "display_name"
        )
    )


def _summaries(requests: list[ApprovalRequest], ctx: ActionContext) -> list[ApprovalRequestOut]:
    deciders = ctx.has("platform.approval.decide")
    ids = {r.requested_by_id for r in requests} | {r.approved_by_id for r in requests}
    names = _names({i for i in ids if i is not None})
    out: list[ApprovalRequestOut] = []
    for r in requests:
        target = action_registry.find(r.action_name)
        mine = ctx.user_id is not None and r.requested_by_id == ctx.user_id
        pending = r.status == ApprovalRequest.Status.PENDING
        out.append(
            ApprovalRequestOut(
                approval_request_id=str(r.request_id),
                action_name=r.action_name,
                action_summary=target.summary if target is not None else "",
                caller=r.caller,
                payload=r.payload,
                status=r.status,
                requested_by_id=r.requested_by_id,
                requested_by_name=names.get(r.requested_by_id or -1, ""),
                requested_at=r.requested_at,
                decided_by_name=names.get(r.approved_by_id or -1, ""),
                decided_at=r.approved_at,
                rejection_reason=r.rejection_reason,
                result=r.result,
                mine=mine,
                can_decide=pending and deciders and not mine,
                can_confirm=pending
                and mine
                and target is not None
                and ctx.has("approval.request.own")
                and not needs_second_person(r),
                can_withdraw=pending and mine and ctx.has("approval.request.own"),
            )
        )
    return out


def _visible(ctx: ActionContext, *, mine: bool) -> Any:
    rows = ApprovalRequest.objects.filter(org_id=ctx.org_id)
    if mine or not ctx.has("platform.approval.decide"):
        rows = rows.filter(requested_by_id=ctx.user_id) if ctx.user_id is not None else rows.none()
    return rows


@action(
    name="platform.approval.list",
    summary="Requests awaiting (or past) approval: the org's queue for checkers, else your own.",
    schema=ApprovalListIn,
    output=ApprovalListOut,
    permission="approval.request.view",
    read_only=True,
    example={"status": "pending"},
)
def list_requests(params: ApprovalListIn, ctx: ActionContext) -> ApprovalListOut:
    rows = _visible(ctx, mine=params.mine)
    if params.status is not None:
        rows = rows.filter(status=params.status)
    requests = list(rows.order_by("-requested_at")[: params.limit])
    scope: Literal["org", "own"] = (
        "org" if ctx.has("platform.approval.decide") and not params.mine else "own"
    )
    return ApprovalListOut(requests=_summaries(requests, ctx), scope=scope)


@action(
    name="platform.approval.get",
    summary="One approval request with the exact payload it will run.",
    schema=ApprovalGetIn,
    output=ApprovalRequestOut,
    permission="approval.request.view",
    read_only=True,
    example={"approval_request_id": "00000000-0000-0000-0000-000000000000"},
)
def get_request(params: ApprovalGetIn, ctx: ActionContext) -> ApprovalRequestOut:
    try:
        request = _visible(ctx, mine=False).get(request_id=params.approval_request_id)
    except (ApprovalRequest.DoesNotExist, ValueError, DjangoValidationError):
        raise NotFound("No such approval request.") from None
    return _summaries([request], ctx)[0]
