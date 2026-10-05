"""Business calendar (Schema §4): cycles, working days, period status, deadlines."""

import uuid

from django.db import models

from kpigo.platform.db import Stamped, Tracked, no_overlap, one_of, valid_range
from kpigo.platform.vocab import PERIOD_KEY_PATTERN, PRODUCTS

PERIOD_STATUSES = ("open", "closing", "closed", "restating")


def _period_key_check(name: str) -> models.CheckConstraint:
    return models.CheckConstraint(
        condition=models.Q(period_key__regex=PERIOD_KEY_PATTERN), name=name
    )


class PerformanceCycle(Tracked):
    cycle_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    name = models.TextField()
    start_month = models.SmallIntegerField()
    end_month = models.SmallIntegerField()
    status = models.TextField(db_default="active")

    class Meta:
        db_table = "performance_cycle"
        constraints = [
            models.UniqueConstraint(
                fields=["org_id", "name"], name="performance_cycle_name_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(start_month__gte=1, start_month__lte=12)
                & models.Q(end_month__gte=1, end_month__lte=12),
                name="performance_cycle_months_valid",
            ),
            one_of("status", ("active", "inactive"), "performance_cycle_status_valid"),
        ]

    def __str__(self) -> str:
        return str(self.name)


class CycleBinding(Stamped):
    """Which cycle a product follows, effective-dated; one cycle per product at a time."""

    binding_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    product = models.TextField()
    cycle = models.ForeignKey(PerformanceCycle, on_delete=models.PROTECT, related_name="bindings")
    effective_from = models.DateField()
    effective_to = models.DateField(null=True)

    class Meta:
        db_table = "cycle_binding"
        constraints = [
            one_of("product", PRODUCTS, "cycle_binding_product_valid"),
            valid_range("cycle_binding_range_valid"),
            no_overlap("cycle_binding_no_overlap", "org_id", "product"),
        ]

    def __str__(self) -> str:
        return f"{self.product} → {self.cycle_id}"


class CalendarDay(Tracked):
    """An exception to the default working week (Mon–Fri): a holiday or a working weekend.

    ``region_code`` null is the org-wide calendar; a regional row overrides it.
    """

    day_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    date = models.DateField()
    is_working_day = models.BooleanField()
    holiday_name = models.TextField(null=True)
    region_code = models.TextField(null=True)

    class Meta:
        db_table = "calendar_day"
        constraints = [
            models.UniqueConstraint(
                fields=["org_id", "date", "region_code"],
                name="calendar_day_unique",
                nulls_distinct=False,
            )
        ]

    def __str__(self) -> str:
        return f"{self.date} {'working' if self.is_working_day else 'closed'}"


class PeriodStatus(Tracked):
    """``open → closing → closed → restating → closed`` (App Flow §8)."""

    pk = models.CompositePrimaryKey("org_id", "product", "period_key")
    org_id = models.UUIDField()
    product = models.TextField()
    period_key = models.CharField(max_length=6)
    status = models.TextField(db_default="open")
    closed_at = models.DateTimeField(null=True)
    closed_by = models.BigIntegerField(null=True)
    snapshot_version = models.IntegerField(db_default=0)
    # Why the period was last restated; carried onto the snapshot the restatement closes.
    status_reason = models.TextField(null=True)
    auto_close_at = models.DateTimeField(null=True)
    grace_until = models.DateTimeField(null=True)

    class Meta:
        db_table = "period_status"
        constraints = [
            one_of("product", PRODUCTS, "period_status_product_valid"),
            one_of("status", PERIOD_STATUSES, "period_status_status_valid"),
            _period_key_check("period_status_period_key_valid"),
        ]

    def __str__(self) -> str:
        return f"{self.product} {self.period_key} {self.status}"


class PeriodDeadline(Tracked):
    """When a feed's data for a period is due. ``feed_id`` references ``feed`` (Workstream C)."""

    pk = models.CompositePrimaryKey("org_id", "feed_id", "period_key")
    org_id = models.UUIDField()
    feed_id = models.UUIDField()
    period_key = models.CharField(max_length=6)
    due_at = models.DateTimeField()

    class Meta:
        db_table = "period_deadline"
        constraints = [_period_key_check("period_deadline_period_key_valid")]

    def __str__(self) -> str:
        return f"{self.feed_id} {self.period_key} due {self.due_at}"
