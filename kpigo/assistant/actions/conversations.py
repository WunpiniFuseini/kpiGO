"""Asking the assistant, reading back conversations, and the token budget (R7).

``assistant.ask`` is the only way in. It runs the tool loop in
:mod:`kpigo.assistant.runtime` as the asking user, with ``caller="agent"`` on
every action it calls. With no model connected it refuses with the reason, and
nothing else in kpiGo is affected (PRD AG-8).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Sum
from pydantic import BaseModel, Field

from kpigo.action import ActionContext, Conflict, NotFound, action
from kpigo.assistant import inference, runtime
from kpigo.assistant.models import (
    AssistantBudget,
    AssistantConversation,
    AssistantUsage,
)

EXAMPLE_ID = "00000000-0000-0000-0000-000000000000"
StepOutcome = Literal["ok", "proposed", "refused", "error"]


class AssistantStepOut(BaseModel):
    action_name: str
    params: dict[str, Any]
    outcome: StepOutcome
    result: dict[str, Any] | None = None
    error: str = ""
    approval_request_id: str | None = None


class AssistantAskIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    # Continue a conversation; omit to start a new one.
    conversation_id: uuid.UUID | None = None


class AssistantAskOut(BaseModel):
    conversation_id: uuid.UUID
    reply: str
    stopped: Literal["answered", "max_steps", "budget", "model_error", "deadline"]
    steps: list[AssistantStepOut]
    input_tokens: int
    output_tokens: int


def _own(conversation_id: uuid.UUID | str, ctx: ActionContext) -> AssistantConversation:
    try:
        return AssistantConversation.objects.get(
            conversation_id=conversation_id, org_id=ctx.org_id, user_id=ctx.user_id or -1
        )
    except (AssistantConversation.DoesNotExist, ValueError, DjangoValidationError):
        # Someone else's conversation is indistinguishable from none.
        raise NotFound("No such conversation.") from None


@action(
    name="assistant.ask",
    agent_forbidden=True,
    summary="Ask the assistant a question; it reads through your own actions and only proposes changes.",
    schema=AssistantAskIn,
    output=AssistantAskOut,
    permission="assistant.use",
    read_only=False,
    audit="assistant.asked",
    example={"message": "Why is Ama's TAT below target this month?"},
)
def ask(params: AssistantAskIn, ctx: ActionContext) -> AssistantAskOut:
    if ctx.user_id is None:
        raise Conflict("The assistant runs as a signed-in person.")
    try:
        provider = inference.get_provider()
    except inference.NotConfigured as exc:
        raise Conflict(f"The assistant is off: {exc}") from None
    conversation = _own(params.conversation_id, ctx) if params.conversation_id else None
    reason = runtime.budget_state(ctx.org_id, ctx.user_id).exhausted
    if reason:
        raise Conflict(reason)
    turn = runtime.ask(ctx, params.message, conversation, provider)
    ctx.audit(
        "assistant.turn",
        conversation_id=str(turn.conversation.conversation_id),
        stopped=turn.stopped,
        actions=[s.action_name for s in turn.steps],
        input_tokens=turn.input_tokens,
        output_tokens=turn.output_tokens,
    )
    return AssistantAskOut(
        conversation_id=turn.conversation.conversation_id,
        reply=turn.reply,
        stopped=turn.stopped,
        steps=[
            AssistantStepOut(
                action_name=s.action_name,
                params=s.params,
                outcome=s.outcome,
                result=s.result,
                error=s.error,
                approval_request_id=s.approval_request_id,
            )
            for s in turn.steps
        ],
        input_tokens=turn.input_tokens,
        output_tokens=turn.output_tokens,
    )


# --- reading back ------------------------------------------------------------


class AssistantConversationListIn(BaseModel):
    limit: int = Field(default=50, ge=1, le=200)


class AssistantConversationSummaryOut(BaseModel):
    conversation_id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime


class AssistantConversationListOut(BaseModel):
    conversations: list[AssistantConversationSummaryOut]


@action(
    name="assistant.conversation.list",
    summary="Your assistant conversations, most recent first.",
    schema=AssistantConversationListIn,
    output=AssistantConversationListOut,
    permission="assistant.use",
    read_only=True,
    example={"limit": 20},
)
def list_conversations(
    params: AssistantConversationListIn, ctx: ActionContext
) -> AssistantConversationListOut:
    rows = AssistantConversation.objects.filter(
        org_id=ctx.org_id, user_id=ctx.user_id or -1
    ).order_by("-updated_at")[: params.limit]
    return AssistantConversationListOut(
        conversations=[
            AssistantConversationSummaryOut(
                conversation_id=r.conversation_id,
                title=r.title,
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in rows
        ]
    )


class AssistantConversationIn(BaseModel):
    conversation_id: uuid.UUID


class AssistantMessageOut(BaseModel):
    seq: int
    role: Literal["user", "assistant", "tool"]
    content: str
    action_name: str = ""
    params: dict[str, Any] | None = None
    outcome: StepOutcome | None = None
    result: dict[str, Any] | None = None
    approval_request_id: str | None = None
    created_at: datetime


class AssistantConversationOut(BaseModel):
    conversation_id: uuid.UUID
    title: str
    messages: list[AssistantMessageOut]


@action(
    name="assistant.conversation.get",
    summary="One of your assistant conversations, with each action it ran and the result.",
    schema=AssistantConversationIn,
    output=AssistantConversationOut,
    permission="assistant.use",
    read_only=True,
    example={"conversation_id": EXAMPLE_ID},
)
def get_conversation(
    params: AssistantConversationIn, ctx: ActionContext
) -> AssistantConversationOut:
    conversation = _own(params.conversation_id, ctx)
    return AssistantConversationOut(
        conversation_id=conversation.conversation_id,
        title=conversation.title,
        messages=[
            AssistantMessageOut(
                seq=m.seq,
                role=m.role,
                # A tool turn's text is what the model saw; the chat shows the result.
                content="" if m.role == "tool" else m.content,
                action_name=m.action_name,
                params=m.params,
                outcome=m.outcome,
                result=m.result,
                approval_request_id=str(m.approval_request_id) if m.approval_request_id else None,
                created_at=m.created_at,
            )
            for m in conversation.messages.order_by("seq")
            # Search and schema look-ups are the model's working, not the user's.
            if m.role != "tool" or m.action_name
        ],
    )


class AssistantConversationDeleteOut(BaseModel):
    conversation_id: uuid.UUID
    deleted: bool


@action(
    name="assistant.conversation.delete",
    agent_forbidden=True,
    summary="Delete one of your assistant conversations. Its audit trail stays.",
    schema=AssistantConversationIn,
    output=AssistantConversationDeleteOut,
    permission="assistant.use",
    read_only=False,
    audit="assistant.conversation_deleted",
    example={"conversation_id": EXAMPLE_ID},
)
def delete_conversation(
    params: AssistantConversationIn, ctx: ActionContext
) -> AssistantConversationDeleteOut:
    conversation = _own(params.conversation_id, ctx)
    conversation.delete()
    return AssistantConversationDeleteOut(conversation_id=params.conversation_id, deleted=True)


# --- metering and the budget (PRD AG-7) --------------------------------------


class AssistantUsageIn(BaseModel):
    pass


class AssistantUserUsageOut(BaseModel):
    user_id: int | None
    display_name: str
    tokens: int


class AssistantUsageOut(BaseModel):
    month_tokens: int
    monthly_limit: int | None
    today_tokens: int
    user_daily_limit: int | None
    # Why no more questions can be asked right now; empty while within budget.
    exhausted: str
    # This month's tokens by person: only for those who manage the assistant.
    by_user: list[AssistantUserUsageOut] | None = None


@action(
    name="assistant.usage",
    summary="Tokens used this month and today against the organisation's assistant budget.",
    schema=AssistantUsageIn,
    output=AssistantUsageOut,
    permission="assistant.use",
    read_only=True,
    example={},
)
def usage(params: AssistantUsageIn, ctx: ActionContext) -> AssistantUsageOut:
    state = runtime.budget_state(ctx.org_id, ctx.user_id)
    by_user: list[AssistantUserUsageOut] | None = None
    if ctx.has("assistant.manage"):
        from django.utils import timezone

        from kpigo.access.models import AppUser

        month_start = timezone.localtime().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        rows = (
            AssistantUsage.objects.filter(org_id=ctx.org_id, created_at__gte=month_start)
            .values("user_id")
            .annotate(i=Sum("input_tokens"), o=Sum("output_tokens"))
        )
        names = dict(
            AppUser.objects.filter(auth_user_id__in=[r["user_id"] for r in rows]).values_list(
                "auth_user_id", "display_name"
            )
        )
        by_user = sorted(
            (
                AssistantUserUsageOut(
                    user_id=r["user_id"],
                    display_name=names.get(r["user_id"], ""),
                    tokens=int(r["i"] or 0) + int(r["o"] or 0),
                )
                for r in rows
            ),
            key=lambda u: -u.tokens,
        )
    return AssistantUsageOut(
        month_tokens=state.monthly_used,
        monthly_limit=state.monthly_limit,
        today_tokens=state.daily_used,
        user_daily_limit=state.daily_limit,
        exhausted=state.exhausted,
        by_user=by_user,
    )


class AssistantBudgetIn(BaseModel):
    # Null removes a cap.
    monthly_tokens: int | None = Field(default=None, ge=0)
    user_daily_tokens: int | None = Field(default=None, ge=0)


class AssistantBudgetOut(BaseModel):
    monthly_tokens: int | None
    user_daily_tokens: int | None


@action(
    name="assistant.budget.set",
    agent_forbidden=True,
    summary="Cap the assistant's tokens per month for the organisation and per person per day.",
    schema=AssistantBudgetIn,
    output=AssistantBudgetOut,
    permission="assistant.manage",
    read_only=False,
    audit="assistant.budget_set",
    example={"monthly_tokens": 5_000_000, "user_daily_tokens": 200_000},
)
def set_budget(params: AssistantBudgetIn, ctx: ActionContext) -> AssistantBudgetOut:
    budget, _ = AssistantBudget.objects.update_or_create(
        org_id=ctx.org_id,
        defaults={
            "monthly_tokens": params.monthly_tokens,
            "user_daily_tokens": params.user_daily_tokens,
            "updated_by": ctx.user_id,
        },
        create_defaults={
            "monthly_tokens": params.monthly_tokens,
            "user_daily_tokens": params.user_daily_tokens,
            "created_by": ctx.user_id,
            "updated_by": ctx.user_id,
        },
    )
    return AssistantBudgetOut(
        monthly_tokens=budget.monthly_tokens, user_daily_tokens=budget.user_daily_tokens
    )
