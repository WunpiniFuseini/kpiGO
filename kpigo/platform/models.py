"""Platform tables (Schema §13): audit_log, approval_request, action_idempotency."""

import uuid

from django.db import models


class AuditLog(models.Model):
    """Append-only. A database trigger rejects UPDATE and DELETE (migration 0002)."""

    audit_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    occurred_at = models.DateTimeField(auto_now_add=True)
    actor_user_id = models.BigIntegerField(null=True)
    caller = models.TextField()
    action_name = models.TextField()
    event = models.TextField()
    object_type = models.TextField(blank=True, default="")
    object_id = models.TextField(blank=True, default="")
    payload = models.JSONField(default=dict)
    request_id = models.TextField()
    ip_address = models.GenericIPAddressField(null=True)

    class Meta:
        db_table = "audit_log"
        indexes = [
            models.Index(fields=["org_id", "-occurred_at"], name="audit_log_org_time"),
            models.Index(fields=["object_type", "object_id"], name="audit_log_object"),
            models.Index(fields=["request_id"], name="audit_log_request"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(caller__in=["ui", "http", "cli", "job", "agent", "mcp"]),
                name="audit_log_caller_valid",
            )
        ]

    def __str__(self) -> str:
        return f"{self.occurred_at:%Y-%m-%d %H:%M:%S} {self.action_name} {self.event}"


class ApprovalRequest(models.Model):
    """A mutating action held for a checker. ``payload`` is the identical validated input."""

    class Status(models.TextChoices):
        PENDING = "pending"
        APPROVED = "approved"
        REJECTED = "rejected"
        FAILED = "failed"

    request_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    action_name = models.TextField()
    payload = models.JSONField()
    caller = models.TextField()
    requested_by_id = models.BigIntegerField(null=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    status = models.TextField(default=Status.PENDING)
    approved_by_id = models.BigIntegerField(null=True)
    approved_at = models.DateTimeField(null=True)
    rejection_reason = models.TextField(blank=True, default="")
    executed_at = models.DateTimeField(null=True)
    result = models.JSONField(null=True)

    class Meta:
        db_table = "approval_request"
        indexes = [models.Index(fields=["org_id", "status"], name="approval_request_status")]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=["pending", "approved", "rejected", "failed"]),
                name="approval_request_status_valid",
            )
        ]

    def __str__(self) -> str:
        return f"{self.action_name} ({self.status})"


class ActionIdempotency(models.Model):
    """The stored result of a mutating action keyed by its declared idempotency key."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    action_name = models.TextField()
    key = models.TextField()
    result = models.JSONField()
    request_id = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "action_idempotency"
        constraints = [
            models.UniqueConstraint(
                fields=["org_id", "action_name", "key"], name="action_idempotency_unique"
            )
        ]

    def __str__(self) -> str:
        return f"{self.action_name}:{self.key}"
