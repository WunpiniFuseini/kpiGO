"""The assistant's runtime: a tool loop over the action registry (TDD §13, App Flow §11).

The assistant has no privileges of its own. It runs in the asking user's own
``ActionContext`` with ``caller="agent"``, so every call passes the same
pipeline: their permissions, their data scope, their audit trail. Read actions
execute; a mutating action becomes an approval request (the pipeline's rule, not
this module's), and ``agent_forbidden`` actions are refused outright.

Tools are generated from the registry, but the model sees three of them rather
than one per action: ``find_actions`` searches the catalogue the user may use,
``describe_action`` returns one action's input schema, and ``run_action`` invokes
it. The catalogue grows with every release and runs past what providers accept
as a tool list (and what a small local model can hold in context); three tools
stay constant while the registry remains the only source of what can be done.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import Max, Sum
from django.utils import timezone

from kpigo.action import (
    ActionContext,
    ActionError,
    OutOfScope,
    PermissionDenied,
    Proposal,
    invoke,
    registry,
)
from kpigo.action.definition import ActionDefinition
from kpigo.assistant import inference
from kpigo.assistant.inference import Message, Provider, ToolCall, ToolSpec
from kpigo.assistant.models import (
    AssistantBudget,
    AssistantConversation,
    AssistantMessage,
    AssistantUsage,
)

logger = logging.getLogger("kpigo.assistant")

# What the model is shown of one action's output; the full output is kept for the chat.
RESULT_CHARS = 12_000
# History replayed to the model on each turn.
HISTORY_MESSAGES = 40
SEARCH_LIMIT = 25

FIND = "find_actions"
DESCRIBE = "describe_action"
RUN = "run_action"

TOOLS = [
    ToolSpec(
        name=FIND,
        description=(
            "Search the kpiGo actions available to this user. Returns names, one-line "
            "summaries and whether each reads data or proposes a change."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Words to match, e.g. 'scorecard compute' or 'feed run'.",
                }
            },
            "required": ["query"],
        },
    ),
    ToolSpec(
        name=DESCRIBE,
        description="The input schema and an example payload for one action.",
        parameters={
            "type": "object",
            "properties": {"action": {"type": "string", "description": "The action's name."}},
            "required": ["action"],
        },
    ),
    ToolSpec(
        name=RUN,
        description=(
            "Run one action with its parameters. Read actions return data. Actions that "
            "change anything are never applied: they become a proposal the user approves."
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "The action's name."},
                "params": {"type": "object", "description": "Input matching its schema."},
            },
            "required": ["action", "params"],
        },
    ),
]

SYSTEM_PROMPT = """\
You are the assistant inside kpiGo, a performance-tracking system, working for {name}.
Today is {today}.

Answer only from what kpiGo's actions return; never invent or estimate figures.
Find actions with find_actions, read an action's schema with describe_action, then
call run_action. You can use only the actions this user could use themselves.

Read actions run immediately. You never change anything yourself: an action that
would change data becomes a proposal the user reviews in their approvals queue, so
say plainly that it is proposed, not done. Some actions (closing a period,
publishing targets, deciding overrides, changing access) are never available to you.

A missing value is not zero: if a metric has no data, say so rather than treat it as 0.
Keep answers short, and name the action whose result you used."""


@dataclass
class Step:
    """One tool call the model made and what came of it."""

    action_name: str
    params: dict[str, Any]
    outcome: str  # ok | proposed | refused | error
    result: dict[str, Any] | None = None
    error: str = ""
    approval_request_id: str | None = None


@dataclass
class Turn:
    conversation: AssistantConversation
    reply: str
    stopped: str  # answered | max_steps | budget | model_error | deadline
    steps: list[Step] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


# --- the catalogue -------------------------------------------------------------


def catalogue(ctx: ActionContext) -> dict[str, ActionDefinition]:
    """The actions this user's assistant may use: theirs, minus prohibitions and itself."""
    return {
        d.name: d
        for d in registry
        if d.agent_allowed
        and d.permission in ctx.permissions
        and not d.name.startswith("assistant.")
    }


def _kind(d: ActionDefinition) -> str:
    return "read" if d.read_only else "proposes a change"


def find(allowed: dict[str, ActionDefinition], query: str) -> dict[str, Any]:
    words = [w for w in re.split(r"[^a-z0-9]+", query.lower()) if w]
    scored: list[tuple[int, str]] = []
    for name, d in allowed.items():
        haystack = f"{name} {d.summary}".lower()
        score = sum(1 for w in words if w in haystack)
        if score or not words:
            scored.append((score, name))
    scored.sort(key=lambda s: (-s[0], s[1]))
    hits = [
        {"action": name, "summary": allowed[name].summary, "kind": _kind(allowed[name])}
        for _, name in scored[:SEARCH_LIMIT]
    ]
    return {"actions": hits, "more": max(0, len(scored) - SEARCH_LIMIT)}


def describe(allowed: dict[str, ActionDefinition], name: str) -> dict[str, Any]:
    d = allowed.get(name)
    if d is None:
        return {"error": f"No action '{name}' is available to you."}
    return {
        "action": d.name,
        "summary": d.summary,
        "kind": _kind(d),
        "parameters": d.schema.model_json_schema(),
        "example": d.example or {},
    }


# --- running one call ----------------------------------------------------------


def run_action(
    allowed: dict[str, ActionDefinition], name: str, params: Any, agent_ctx: ActionContext
) -> Step:
    if not isinstance(params, dict):
        params = {}
    d = allowed.get(name)
    if d is None:
        refused = registry.find(name)
        reason = (
            "is never run by the assistant"
            if refused is not None and not refused.agent_allowed
            else "is not available to you"
        )
        return Step(name, params, "refused", error=f"'{name}' {reason}.")
    try:
        with transaction.atomic():
            output = invoke(d, params, agent_ctx)
    except (PermissionDenied, OutOfScope) as exc:
        return Step(name, params, "refused", error=exc.message)
    except ActionError as exc:
        return Step(name, params, "error", error=exc.message)
    except Exception as exc:  # A failing action must not end the conversation.
        logger.exception("assistant tool %s failed", name)
        return Step(name, params, "error", error=f"The action failed ({type(exc).__name__}).")
    result = output.model_dump(mode="json")
    if isinstance(output, Proposal):
        return Step(
            name,
            params,
            "proposed",
            result=result,
            approval_request_id=output.approval_request_id,
        )
    return Step(name, params, "ok", result=result)


def _for_model(step: Step) -> str:
    payload: dict[str, Any] = {"outcome": step.outcome}
    if step.error:
        payload["error"] = step.error
    if step.outcome == "proposed":
        payload["note"] = "Not applied. The user must approve this proposal."
    if step.result is not None:
        payload["result"] = step.result
    text = json.dumps(payload, default=str)
    if len(text) > RESULT_CHARS:
        text = text[:RESULT_CHARS] + '..." [truncated: narrow the query for the rest]'
    return text


def _call(
    allowed: dict[str, ActionDefinition], call: ToolCall, agent_ctx: ActionContext
) -> tuple[str, Step | None]:
    """The text the model sees for a tool call, and the action step if one ran."""
    args = call.arguments
    if call.name == FIND:
        return json.dumps(find(allowed, str(args.get("query", "")))), None
    if call.name == DESCRIBE:
        return json.dumps(describe(allowed, str(args.get("action", ""))), default=str), None
    if call.name == RUN:
        step = run_action(allowed, str(args.get("action", "")), args.get("params"), agent_ctx)
        return _for_model(step), step
    return json.dumps({"error": f"Unknown tool '{call.name}'."}), None


# --- budget ----------------------------------------------------------------------


@dataclass(frozen=True)
class BudgetState:
    monthly_limit: int | None
    monthly_used: int
    daily_limit: int | None
    daily_used: int

    @property
    def exhausted(self) -> str:
        """Why no more tokens may be spent, or ''."""
        if self.monthly_limit is not None and self.monthly_used >= self.monthly_limit:
            return "The organisation's monthly assistant budget is used up."
        if self.daily_limit is not None and self.daily_used >= self.daily_limit:
            return "Your daily assistant allowance is used up; it resets tomorrow."
        return ""


def _tokens(rows: Any) -> int:
    total = rows.aggregate(i=Sum("input_tokens"), o=Sum("output_tokens"))
    return int(total["i"] or 0) + int(total["o"] or 0)


def budget_state(org_id: str, user_id: int | None) -> BudgetState:
    budget = AssistantBudget.objects.filter(org_id=org_id).first()
    now = timezone.localtime()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    usage = AssistantUsage.objects.filter(org_id=org_id)
    return BudgetState(
        monthly_limit=budget.monthly_tokens if budget else None,
        monthly_used=_tokens(usage.filter(created_at__gte=month_start)),
        daily_limit=budget.user_daily_tokens if budget else None,
        daily_used=_tokens(
            usage.filter(
                user_id=user_id,
                created_at__gte=day_start,
                created_at__lt=day_start + timedelta(days=1),
            )
        ),
    )


# --- conversations ----------------------------------------------------------------


def _history(conversation: AssistantConversation) -> list[Message]:
    rows = list(conversation.messages.order_by("-seq")[:HISTORY_MESSAGES])[::-1]
    # Never open on a tool result whose call was cut off.
    while rows and rows[0].role != "user":
        rows.pop(0)
    out: list[Message] = []
    for r in rows:
        if r.role == "tool":
            out.append(Message(role="tool", content=r.content, tool_call_id=r.tool_call_id))
        elif r.role == "assistant":
            calls = tuple(
                ToolCall(id=c["id"], name=c["name"], arguments=c.get("arguments") or {})
                for c in (r.tool_calls or [])
            )
            out.append(Message(role="assistant", content=r.content, tool_calls=calls))
        else:
            out.append(Message(role="user", content=r.content))
    return out


class _Writer:
    def __init__(self, conversation: AssistantConversation, user_id: int | None) -> None:
        self.conversation = conversation
        self.user_id = user_id
        last = conversation.messages.aggregate(m=Max("seq"))["m"]
        self.seq = int(last) if last is not None else 0

    def add(self, role: str, content: str = "", **fields: Any) -> None:
        self.seq += 1
        AssistantMessage.objects.create(
            conversation=self.conversation,
            org_id=self.conversation.org_id,
            seq=self.seq,
            role=role,
            content=content,
            created_by=self.user_id,
            **fields,
        )


def _display_name(ctx: ActionContext) -> str:
    from kpigo.access.models import AppUser

    if ctx.user_id is None:
        return "the user"
    name = AppUser.objects.filter(auth_user_id=ctx.user_id).values_list("display_name", flat=True)
    return name.first() or "the user"


def ask(
    ctx: ActionContext,
    text: str,
    conversation: AssistantConversation | None = None,
    provider: Provider | None = None,
) -> Turn:
    """Answer one user message, running tools until the model replies or a limit is hit."""
    provider = provider or inference.get_provider()
    max_steps = int(getattr(settings, "KPIGO_ASSISTANT_MAX_STEPS", 8))
    max_tokens = int(getattr(settings, "KPIGO_ASSISTANT_MAX_OUTPUT_TOKENS", 1024))
    deadline = time.monotonic() + float(getattr(settings, "KPIGO_ASSISTANT_DEADLINE_SECONDS", 240))

    if conversation is None:
        conversation = AssistantConversation.objects.create(
            org_id=ctx.org_id,
            user_id=ctx.user_id or 0,
            title=text.strip().splitlines()[0][:120] if text.strip() else "Conversation",
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
        )
    history = _history(conversation)
    writer = _Writer(conversation, ctx.user_id)
    writer.add("user", text)

    allowed = catalogue(ctx)
    # The assistant's own context: the human's, re-labelled. Nothing is added (AG-4).
    agent_ctx = dataclasses.replace(
        ctx, caller="agent", pending_audit=[], dry_run=False, approval=None
    )
    system = SYSTEM_PROMPT.format(name=_display_name(ctx), today=timezone.localdate().isoformat())
    messages = [
        Message(role="system", content=system),
        *history,
        Message(role="user", content=text),
    ]
    turn = Turn(conversation=conversation, reply="", stopped="answered")

    for _ in range(max_steps):
        reason = budget_state(ctx.org_id, ctx.user_id).exhausted
        if reason:
            turn.stopped, turn.reply = "budget", reason
            break
        if time.monotonic() > deadline:
            turn.stopped = "deadline"
            turn.reply = "This took too long, so I stopped. Try a narrower question."
            break
        try:
            completion = provider.complete(messages, TOOLS, max_tokens=max_tokens)
        except inference.InferenceError as exc:
            turn.stopped, turn.reply = "model_error", f"The model could not answer: {exc}"
            break
        AssistantUsage.objects.create(
            org_id=ctx.org_id,
            user_id=ctx.user_id,
            conversation_id=conversation.conversation_id,
            provider=provider.name,
            model=provider.model,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            created_by=ctx.user_id,
        )
        turn.input_tokens += completion.input_tokens
        turn.output_tokens += completion.output_tokens
        if not completion.tool_calls:
            turn.reply = completion.text.strip() or "(The model returned an empty answer.)"
            break
        # Give every call an id the provider can match its result to.
        calls = tuple(
            c if c.id else dataclasses.replace(c, id=f"call_{uuid.uuid4().hex[:12]}")
            for c in completion.tool_calls
        )
        messages.append(Message(role="assistant", content=completion.text, tool_calls=calls))
        writer.add(
            "assistant",
            completion.text,
            tool_calls=[{"id": c.id, "name": c.name, "arguments": c.arguments} for c in calls],
        )
        for call in calls:
            content, step = _call(allowed, call, agent_ctx)
            messages.append(Message(role="tool", content=content, tool_call_id=call.id))
            fields: dict[str, Any] = {"tool_call_id": call.id}
            if step is not None:
                turn.steps.append(step)
                fields.update(
                    action_name=step.action_name,
                    params=step.params,
                    outcome=step.outcome,
                    result=step.result,
                    approval_request_id=step.approval_request_id,
                )
            writer.add("tool", content, **fields)
    else:
        turn.stopped = "max_steps"
        turn.reply = (
            f"I stopped after {max_steps} steps without a final answer. Try a narrower question."
        )

    writer.add("assistant", turn.reply)
    conversation.updated_at = timezone.now()
    conversation.updated_by = ctx.user_id
    conversation.save(update_fields=["updated_at", "updated_by"])
    return turn
