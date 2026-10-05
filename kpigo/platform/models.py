"""Platform tables.

Schema §13: audit_log, approval_request, action_idempotency.
Schema §4: org_settings (with the config_version counter), currency, fx_rate.
"""

import uuid

from django.db import models

from kpigo.platform.db import Stamped, Tracked, one_of


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


class OrgSettings(Tracked):
    """One row per org. ``config_version`` is bumped by every configuration change.

    Cache keys embed ``config_version`` (TDD §5), so a change to metrics, hierarchy,
    calendar or money settings invalidates without targeted eviction. Actions never
    bump it by hand: declaring ``config_change=True`` makes the pipeline do it.
    """

    org_id = models.UUIDField(primary_key=True)
    reporting_currency = models.CharField(max_length=3, null=True)
    reporting_timezone = models.TextField(db_default="UTC")
    # Activity at or after this local time counts toward the next business day.
    # Null means the business day ends at midnight.
    business_day_cutoff = models.TimeField(null=True)
    # Campaign Manager's multi-touch collision rule, one per org (PRD CM-11, CM-12).
    attribution_rule = models.TextField(db_default="last_touch")
    # Campaign value headline (Scope §9.2): incremental, gross beside it; or gross.
    campaign_value_basis = models.TextField(db_default="incremental")
    # When the basis last changed: affected views say so.
    value_basis_changed_at = models.DateTimeField(null=True)
    config_version = models.BigIntegerField(db_default=0)

    class Meta:
        db_table = "org_settings"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(config_version__gte=0), name="org_settings_version_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    attribution_rule__in=("last_touch", "first_touch", "priority", "split_even")
                ),
                name="org_settings_attribution_rule_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(campaign_value_basis__in=("incremental", "gross")),
                name="org_settings_value_basis_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"org {self.org_id} (config v{self.config_version})"


class Currency(Stamped):
    pk = models.CompositePrimaryKey("org_id", "code")
    org_id = models.UUIDField()
    code = models.CharField(max_length=3)
    name = models.TextField()
    minor_units = models.SmallIntegerField(db_default=2)
    is_active = models.BooleanField(db_default=True)

    class Meta:
        db_table = "currency"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(code__regex=r"^[A-Z]{3}$"), name="currency_code_iso"
            ),
            models.CheckConstraint(
                condition=models.Q(minor_units__gte=0, minor_units__lte=4),
                name="currency_minor_units_range",
            ),
        ]

    def __str__(self) -> str:
        return str(self.code)


FX_RATE_TYPES = ("average", "closing", "budget")


class FxRate(Tracked):
    """``rate`` converts one unit of ``from_currency`` into ``to_currency`` for a period."""

    pk = models.CompositePrimaryKey(
        "org_id", "from_currency", "to_currency", "period_key", "rate_type"
    )
    org_id = models.UUIDField()
    from_currency = models.CharField(max_length=3)
    to_currency = models.CharField(max_length=3)
    period_key = models.CharField(max_length=6)
    rate_type = models.TextField()
    rate = models.DecimalField(max_digits=24, decimal_places=10)

    class Meta:
        db_table = "fx_rate"
        constraints = [
            one_of("rate_type", FX_RATE_TYPES, "fx_rate_type_valid"),
            models.CheckConstraint(condition=models.Q(rate__gt=0), name="fx_rate_positive"),
            models.CheckConstraint(
                condition=~models.Q(from_currency=models.F("to_currency")),
                name="fx_rate_distinct_currencies",
            ),
            models.CheckConstraint(
                condition=models.Q(period_key__regex=r"^[0-9]{4}(0[1-9]|1[0-2])$"),
                name="fx_rate_period_key_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.from_currency}->{self.to_currency} {self.period_key} {self.rate_type}"


APPROVAL_CLASSES = (
    "metric_change",
    "hierarchy_change",
    "calendar_change",
    "period_close",
    "config_change",
    "access_change",
    "target_publish",
    "manual_input",
    "budget",
)
# Classes that ship switched on: no policy row means on, and an Admin may turn
# them off. Campaign budgets are money, so a change waits for a checker (CM-5).
APPROVAL_DEFAULT_ON = ("budget",)


class ApprovalPolicy(Tracked):
    """Maker-checker on or off for one action class (PRD AD-3).

    No row means off, except for the classes in ``APPROVAL_DEFAULT_ON``.
    """

    pk = models.CompositePrimaryKey("org_id", "approval_class")
    org_id = models.UUIDField()
    approval_class = models.TextField()
    enabled = models.BooleanField(db_default=False)

    class Meta:
        db_table = "approval_policy"
        constraints = [one_of("approval_class", APPROVAL_CLASSES, "approval_policy_class_valid")]

    def __str__(self) -> str:
        return f"{self.approval_class}: {'on' if self.enabled else 'off'}"
