"""Campaign Manager's own records (Schema §10, Scope §9.1).

Campaign Manager is a system of record: campaigns, their events, budgets and
audiences are authored here, not fed. The audience is criteria over the
dimension hierarchy (``dim_member``), never a customer list: kpiGo holds no
customer master, and the outcome feed supplies who actually transacted.

An event's ``period_start``/``period_end`` are inclusive contact days (the
attribution rule reads ``period_start < activity_date <= period_end + window``),
unlike the half-open effective periods elsewhere: an event is a run, not a row
in force.

Three feeds come in from the DE team: outcomes (what customers did), the
customer population (counts by dimension, for reach estimates) and contacts
(who was contacted, delivered and responded). ``campaign_attribution`` is
computed from outcomes and events (``kpigo.campaigns.attribution``).
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
# Multi-touch collision rules (PRD CM-12), one per org (CM-11). Last touch is the default.
ATTRIBUTION_RULES = ("last_touch", "first_touch", "priority", "split_even")
# How an outcome reached an event: the client's campaign tag, or the audience criteria.
MATCH_VIA = ("tag", "criteria")
# Why an attribution row has the rule it has: a collision rule, the only candidate, or
# the customer sat in the event's control group and so cannot be credited to it.
RULES_APPLIED = ("single", "holdout", *ATTRIBUTION_RULES)
# Campaign value headline (Scope §9.2): incremental by default, gross always beside it.
VALUE_BASES = ("incremental", "gross")
# A customer's baseline: clean, absent because they have no history (new to bank),
# or contaminated by an earlier event that reached them in the baseline window.
BASELINE_CONFIDENCE = ("high", "new_customer", "low_contaminated_baseline")
# The largest control group an event may hold out, in percent.
MAX_HOLDOUT_PCT = 50
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
    # The share of the audience the DE team holds out as a control group, in percent.
    # Null: no control group. Who was held out comes in on the contact feed.
    holdout_pct = models.SmallIntegerField(null=True)
    channels = ArrayField(models.TextField(), default=list)
    state = models.TextField(db_default="draft")
    # Bumped on every change to a published event; each bump writes a version row.
    version = models.IntegerField(db_default=1)
    # Set when a change to a live event's period or audience re-opens attribution,
    # from this day; the change re-attributes in the same transaction and clears
    # it. Null: nothing to redo.
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
            models.CheckConstraint(
                condition=models.Q(holdout_pct__isnull=True)
                | models.Q(holdout_pct__gte=1, holdout_pct__lte=MAX_HOLDOUT_PCT),
                name="campaign_event_holdout_valid",
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


# ── fed by the DE team (Schema §10) ──────────────────────────────────────────


class CustomerDims(models.Model):
    """The customer's dimensions as the feed supplied them; blank where unknown."""

    segment_code = models.TextField(null=True)
    product_code = models.TextField(null=True)
    region_code = models.TextField(null=True)
    branch_code = models.TextField(null=True)

    class Meta:
        abstract = True


class CampaignOutcome(Stamped, CustomerDims):
    """What a customer did on a day: one row of the outcome feed, conformed.

    ``customer_ref`` is the client's opaque key; kpiGo holds no customer master.
    ``campaign_code`` is the client's own tag where their system has one, and
    ``campaign`` the campaign it names, if any.
    """

    outcome_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    customer_ref = models.TextField()
    metric = models.ForeignKey("metrics.Metric", on_delete=models.PROTECT, related_name="+")
    campaign_code = models.TextField(null=True)
    campaign = models.ForeignKey(Campaign, on_delete=models.PROTECT, null=True, related_name="+")
    activity_date = models.DateField()
    activity_value = models.DecimalField(max_digits=18, decimal_places=4)
    currency_code = models.CharField(max_length=3, null=True)
    source_ref = models.TextField(null=True)
    run_id = models.UUIDField()
    loaded_at = models.DateTimeField()

    class Meta:
        db_table = "campaign_outcome"
        constraints = [
            models.UniqueConstraint(
                fields=["org_id", "customer_ref", "metric", "activity_date", "source_ref"],
                name="campaign_outcome_grain",
                nulls_distinct=False,
            ),
            models.CheckConstraint(
                condition=models.Q(activity_value__gte=0), name="campaign_outcome_value_valid"
            ),
        ]
        indexes = [
            models.Index(fields=["org_id", "activity_date"], name="campaign_outcome_day"),
            models.Index(
                fields=["customer_ref", "activity_date"], name="campaign_outcome_customer"
            ),
            models.Index(
                fields=["campaign"],
                name="campaign_outcome_tagged",
                condition=models.Q(campaign__isnull=False),
            ),
            models.Index(fields=["run_id"], name="campaign_outcome_run"),
        ]

    def __str__(self) -> str:
        return f"{self.customer_ref} {self.metric_id} {self.activity_date}"


class CampaignPopulation(Stamped, CustomerDims):
    """How many customers share a combination of dimensions on a day. Counts, never names."""

    population_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    snapshot_date = models.DateField()
    customer_count = models.BigIntegerField()
    run_id = models.UUIDField()
    loaded_at = models.DateTimeField()

    class Meta:
        db_table = "campaign_population"
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "org_id",
                    "snapshot_date",
                    "segment_code",
                    "product_code",
                    "region_code",
                    "branch_code",
                ],
                name="campaign_population_grain",
                nulls_distinct=False,
            ),
            models.CheckConstraint(
                condition=models.Q(customer_count__gte=0), name="campaign_population_count_valid"
            ),
        ]
        indexes = [models.Index(fields=["org_id", "snapshot_date"], name="campaign_population_day")]

    def __str__(self) -> str:
        return f"{self.snapshot_date} {self.customer_count}"


class CampaignContact(Stamped):
    """One contact of a customer by an event, on a channel and day.

    A ``holdout`` row is the opposite: the customer was in the audience and was
    deliberately not contacted, as the event's control group. Its channel is "".
    """

    contact_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    event = models.ForeignKey(CampaignEvent, on_delete=models.CASCADE, related_name="contacts")
    customer_ref = models.TextField()
    # "" on a holdout row: nobody was contacted.
    channel = models.TextField()
    contact_date = models.DateField()
    holdout = models.BooleanField(db_default=False)
    # Null: the source did not say.
    delivered = models.BooleanField(null=True)
    responded = models.BooleanField(null=True)
    run_id = models.UUIDField()
    loaded_at = models.DateTimeField()

    class Meta:
        db_table = "campaign_contact"
        constraints = [
            models.UniqueConstraint(
                fields=["event", "customer_ref", "channel", "contact_date"],
                name="campaign_contact_grain",
            ),
            models.CheckConstraint(
                condition=models.Q(holdout=False, channel__in=CHANNELS)
                | models.Q(holdout=True, channel=""),
                name="campaign_contact_channel_fits",
            ),
        ]
        indexes = [models.Index(fields=["run_id"], name="campaign_contact_run")]

    def __str__(self) -> str:
        return f"{self.event_id} {self.customer_ref} {self.contact_date}"


# ── computed ─────────────────────────────────────────────────────────────────


class CampaignAttribution(Stamped):
    """An event an outcome matched, and what it was credited (TDD §7.2).

    Every candidate event is kept, so an event's outcome reach counts customers
    who transacted in its audience and window even when another event won the
    collision: ``credited`` is false and ``attributed_value`` zero for those.
    Invariant: per outcome, the credited values sum to at most its value.
    """

    pk = models.CompositePrimaryKey("outcome_id", "event_id")
    org_id = models.UUIDField()
    outcome = models.ForeignKey(CampaignOutcome, on_delete=models.CASCADE, related_name="+")
    event = models.ForeignKey(CampaignEvent, on_delete=models.CASCADE, related_name="+")
    credited = models.BooleanField()
    attributed_value = models.DecimalField(max_digits=18, decimal_places=4)
    share = models.DecimalField(max_digits=9, decimal_places=8)
    rule_applied = models.TextField()
    # How many events competed for the outcome.
    candidates = models.IntegerField()
    via = models.TextField()
    computed_at = models.DateTimeField()

    class Meta:
        db_table = "campaign_attribution"
        constraints = [
            one_of("rule_applied", RULES_APPLIED, "campaign_attribution_rule"),
            one_of("via", MATCH_VIA, "campaign_attribution_via_valid"),
            models.CheckConstraint(
                condition=models.Q(attributed_value__gte=0, share__gte=0, share__lte=1),
                name="campaign_attribution_value_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(credited=True) | models.Q(attributed_value=0, share=0),
                name="campaign_attribution_uncredited_zero",
            ),
        ]
        indexes = [models.Index(fields=["event", "credited"], name="campaign_attribution_event")]

    def __str__(self) -> str:
        return f"{self.outcome_id} -> {self.event_id} {self.attributed_value}"


class CampaignBaseline(Stamped):
    """A converted customer's pre-period baseline for an event (Scope §9.2).

    Same customer, same metric, over an equal-length window immediately before the
    event's span. ``gross`` is what the event was credited for the customer;
    ``baseline`` is their pre-period value scaled by the share of their in-span
    value the event was credited (so a split or a lost collision subtracts only its
    part). ``incremental`` is ``gross - baseline``, negative when the month was
    worse, and null when withheld: a contaminated baseline is not published.
    """

    pk = models.CompositePrimaryKey("event_id", "customer_ref", "metric_id", "currency_code")
    org_id = models.UUIDField()
    event = models.ForeignKey(CampaignEvent, on_delete=models.CASCADE, related_name="+")
    customer_ref = models.TextField()
    metric = models.ForeignKey("metrics.Metric", on_delete=models.PROTECT, related_name="+")
    # "" when the outcomes carried no currency.
    currency_code = models.CharField(max_length=3)
    gross = models.DecimalField(max_digits=18, decimal_places=4)
    baseline = models.DecimalField(max_digits=18, decimal_places=4, null=True)
    incremental = models.DecimalField(max_digits=18, decimal_places=4, null=True)
    confidence = models.TextField()
    # The earlier event whose contacts fell in the baseline window, when contaminated.
    contaminated_by = models.ForeignKey(
        CampaignEvent, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    computed_at = models.DateTimeField()

    class Meta:
        db_table = "campaign_baseline"
        constraints = [
            one_of("confidence", BASELINE_CONFIDENCE, "campaign_baseline_confidence_valid"),
            models.CheckConstraint(
                condition=~models.Q(confidence="low_contaminated_baseline")
                | models.Q(incremental__isnull=True),
                name="campaign_baseline_contaminated_withheld",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.event_id} {self.customer_ref} {self.confidence}"
