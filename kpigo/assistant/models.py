"""The assistant's conversations, their metering and the org's budget (R7, PRD AG-7).

A conversation belongs to one user and is only ever read back by them. Every
model call is metered in ``assistant_usage`` so the org's token budget can be
enforced and reported; the rows store counts, never prompt or answer text.
"""

from __future__ import annotations

import uuid

from django.db import models

from kpigo.platform.db import Stamped, Tracked, one_of

MESSAGE_ROLES = ("user", "assistant", "tool")
# What became of a tool call: ran, became a proposal, was refused, or failed.
TOOL_OUTCOMES = ("ok", "proposed", "refused", "error")


class AssistantConversation(Tracked):
    conversation_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    # The Django user it belongs to; the assistant runs as them.
    user_id = models.BigIntegerField()
    title = models.TextField()

    class Meta:
        db_table = "assistant_conversation"
        indexes = [
            models.Index(fields=["org_id", "user_id", "-updated_at"], name="assistant_conv_user"),
        ]

    def __str__(self) -> str:
        return self.title


class AssistantMessage(Stamped):
    message_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        AssistantConversation,
        on_delete=models.CASCADE,
        related_name="messages",
        db_column="conversation_id",
    )
    org_id = models.UUIDField()
    seq = models.IntegerField()
    role = models.TextField()
    content = models.TextField(db_default="")
    # An assistant turn's requested calls: [{"id", "name", "arguments"}].
    tool_calls = models.JSONField(null=True)
    # A tool turn: which call it answers, the action it ran and what happened.
    tool_call_id = models.TextField(db_default="")
    action_name = models.TextField(db_default="")
    params = models.JSONField(null=True)
    outcome = models.TextField(null=True)
    # The action's structured output, kept for inline rendering in the chat.
    result = models.JSONField(null=True)
    approval_request_id = models.UUIDField(null=True)

    class Meta:
        db_table = "assistant_message"
        constraints = [
            one_of("role", MESSAGE_ROLES, "assistant_message_role_valid"),
            models.CheckConstraint(
                condition=models.Q(outcome__isnull=True) | models.Q(outcome__in=TOOL_OUTCOMES),
                name="assistant_message_outcome_valid",
            ),
            models.UniqueConstraint(fields=["conversation", "seq"], name="assistant_message_seq"),
        ]

    def __str__(self) -> str:
        return f"{self.role} #{self.seq}"


class AssistantUsage(Stamped):
    """One model call: who, which model, how many tokens. Never the text."""

    usage_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    user_id = models.BigIntegerField(null=True)
    conversation_id = models.UUIDField(null=True)
    provider = models.TextField()
    model = models.TextField()
    input_tokens = models.IntegerField(db_default=0)
    output_tokens = models.IntegerField(db_default=0)

    class Meta:
        db_table = "assistant_usage"
        indexes = [
            models.Index(fields=["org_id", "-created_at"], name="assistant_usage_org_time"),
            models.Index(fields=["org_id", "user_id", "-created_at"], name="assistant_usage_user"),
        ]

    def __str__(self) -> str:
        return f"{self.model}: {self.input_tokens}+{self.output_tokens}"


class AssistantBudget(Tracked):
    """The org's token caps. No row, or a null cap, means uncapped."""

    org_id = models.UUIDField(primary_key=True)
    monthly_tokens = models.BigIntegerField(null=True)
    user_daily_tokens = models.BigIntegerField(null=True)

    class Meta:
        db_table = "assistant_budget"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(monthly_tokens__isnull=True) | models.Q(monthly_tokens__gte=0),
                name="assistant_budget_monthly_nonneg",
            ),
            models.CheckConstraint(
                condition=models.Q(user_daily_tokens__isnull=True)
                | models.Q(user_daily_tokens__gte=0),
                name="assistant_budget_daily_nonneg",
            ),
        ]

    def __str__(self) -> str:
        return f"budget {self.org_id}"
