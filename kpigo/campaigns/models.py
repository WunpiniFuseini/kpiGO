"""Campaign Manager's own records (Schema §10, Scope §9.1).

Campaign Manager is a system of record: campaigns, their events, budgets and
audiences are authored here, not fed. The audience is criteria over the
dimension hierarchy (``dim_member``), never a customer list: kpiGo holds no
customer master, and the outcome feed supplies who actually transacted.

An event's ``period_start``/``period_end`` are inclusive contact days (the
attribution rule reads ``activity_date <= period_end + window``), unlike the
half-open effective periods elsewhere: an event is a run, not a row in force.
"""

import uuid

from django.contrib.postgres.fields import ArrayField
from django.db import models

from kpigo.platform.db import Stamped, Tracked, one_of

OBJECTIVES = (
    "acquisition",
    "deposit_growth",
    "activation",
    "cross_sell",
    "attrition_winback",
    "collections",
    "awareness",
)
CHANNELS = ("sms", "email", "call", "ussd", "branch", "app_push", "whatsapp")
CAMPAIGN_STATUSES = ("active", "closed")
# What is stored. ``scheduled``/``running`` are read from the dates of a live event.
EVENT_STATES = ("draft", "live", "paused", "closed")
EVENT_CHANGES = (
    "created",
    "updated",
    "audience",
    "budget",
    "published",
    "paused",
    "resumed",
    "closed",
)


class Campaign(Tracked):
    campaign_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    # The client's own tag where their system has one: the outcome feed's campaign_code.
    code = models.TextField()
    name = models.TextField()
    campaign_type = models.TextField()
    objective = models.TextField()
    product_code = models.TextField(null=True)
    owner_user_id = models.BigIntegerField(null=True)
    description = models.TextField(db_default="")
    # The ``priority`` collision rule: the lower rank wins. Null ranks last.
    priority = models.IntegerField(null=True)
    status = models.TextField(db_default="active")
    closed_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "campaign"
        constraints = [
            models.UniqueConstraint(fields=["org_id", "code"], name="campaign_code_unique"),
            one_of("objective", OBJECTIVES, "campaign_objective_valid"),
            one_of("status", CAMPAIGN_STATUSES, "campaign_status_valid"),
            models.CheckConstraint(
                condition=models.Q(priority__isnull=True) | models.Q(priority__gte=1),
                name="campaign_priority_positive",
            ),
        ]

    def __str__(self) -> str:
        return str(self.code)


class CampaignEvent(Tracked):
    """One run of a campaign. Budget is per event; a recurring campaign has many."""

    event_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    campaign = models.ForeignKey(Campaign, on_delete=models.PROTECT, related_name="events")
    event_name = models.TextField()
    sequence_no = models.IntegerField()
    period_start = models.DateField()
    period_end = models.DateField()
    attribution_window_days = models.IntegerField()
    budget_amount = models.DecimalField(max_digits=18, decimal_places=2)
    budget_currency = models.CharField(max_length=3)
    # Optional, where the client feeds spend; utilisation reads it.
    spend_to_date = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    channels = ArrayField(models.TextField(), default=list)
    state = models.TextField(db_default="draft")
    # Bumped on every change to a published event; each bump writes a version row.
    version = models.IntegerField(db_default=1)
    # Set when a change to a live event's period or audience re-opens attribution:
    # the next outcome load re-attributes from this day. Null: nothing to redo.
    reattribute_from = models.DateField(null=True)
    published_at = models.DateTimeField(null=True)
    closed_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "campaign_event"
        constraints = [
            models.UniqueConstraint(
                fields=["campaign", "sequence_no"], name="campaign_event_sequence_unique"
            ),
            one_of("state", EVENT_STATES, "campaign_event_state_valid"),
            models.CheckConstraint(
                condition=models.Q(period_end__gte=models.F("period_start")),
                name="campaign_event_period_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(attribution_window_days__gte=0),
                name="campaign_event_window_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(budget_amount__gte=0), name="campaign_event_budget_valid"
            ),
            models.CheckConstraint(
                condition=models.Q(budget_currency__regex=r"^[A-Z]{3}$"),
                name="campaign_event_currency_iso",
            ),
            models.CheckConstraint(
                condition=models.Q(channels__contained_by=list(CHANNELS)),
                name="campaign_event_channels_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["org_id", "period_start"], name="campaign_event_start"),
        ]

    def __str__(self) -> str:
        return f"{self.campaign_id} #{self.sequence_no}"


class CampaignAudience(Stamped):
    """One criterion: who the event is for. Same dimension ORs, different dimensions AND."""

    audience_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    event = models.ForeignKey(CampaignEvent, on_delete=models.CASCADE, related_name="audience")
    dimension_type = models.TextField()
    member_code = models.TextField()

    class Meta:
        db_table = "campaign_audience"
        constraints = [
            models.UniqueConstraint(
                fields=["event", "dimension_type", "member_code"],
                name="campaign_audience_unique",
            )
        ]
        indexes = [
            models.Index(fields=["dimension_type", "member_code"], name="campaign_audience_member")
        ]

    def __str__(self) -> str:
        return f"{self.event_id} {self.dimension_type}={self.member_code}"


class CampaignEventVersion(Stamped):
    """The event as it stood after each change: period, window, budget, channels, audience."""

    version_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    event = models.ForeignKey(CampaignEvent, on_delete=models.CASCADE, related_name="versions")
    version_no = models.IntegerField()
    change = models.TextField()
    snapshot = models.JSONField()
    # Set when the change came through maker-checker (a live budget).
    approval_request_id = models.UUIDField(null=True)

    class Meta:
        db_table = "campaign_event_version"
        constraints = [
            models.UniqueConstraint(
                fields=["event", "version_no"], name="campaign_event_version_unique"
            ),
            one_of("change", EVENT_CHANGES, "campaign_event_version_change_valid"),
        ]

    def __str__(self) -> str:
        return f"{self.event_id} v{self.version_no} {self.change}"


class CampaignObjective(Tracked):
    """Per objective: the default attribution window and the outcome metrics it counts.

    Attribution only credits an outcome whose metric is registered for the
    campaign's objective (Scope §9.2, rule 4). No row means the Banking pack's
    default window and no outcome metrics yet.
    """

    pk = models.CompositePrimaryKey("org_id", "objective")
    org_id = models.UUIDField()
    objective = models.TextField()
    default_window_days = models.IntegerField()
    outcome_metric_codes = ArrayField(models.TextField(), default=list)

    class Meta:
        db_table = "campaign_objective"
        constraints = [
            one_of("objective", OBJECTIVES, "campaign_objective_objective_valid"),
            models.CheckConstraint(
                condition=models.Q(default_window_days__gte=0),
                name="campaign_objective_window_valid",
            ),
        ]

    def __str__(self) -> str:
        return str(self.objective)
