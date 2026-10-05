"""Ingestion tables (Schema §7 landing, §8 facts, §12 ingestion operations).

``tmpl_*`` tables hold what a load read, untouched, as text, with its
``run_id``: the raw evidence a rejection report and a diff point at. Conform
reads them and writes the canonical ``fact_*`` tables. ``fact_actual_daily`` is
range-partitioned by month in SQL (migration 0002), so it has no model here.
"""

import uuid

from django.db import models

from kpigo.hierarchy.models import Assignment, Subject
from kpigo.ingestion.validator import (
    DRIVERS,
    GATES,
    LOADABLE_TEMPLATES,
    TEMPLATES,
    WIDGET_SERIES,
)
from kpigo.metrics.models import Metric
from kpigo.platform.db import Stamped, Tracked, one_of
from kpigo.platform.vocab import PERIOD_KEY_PATTERN

CONNECTION_STATUSES = ("active", "disabled")
FEED_MODES = ("pull", "drop", "upload")
FEED_STATUSES = ("active", "paused")
FRESHNESS_STATES = ("never_loaded", "fresh", "stale")
RUN_TRIGGERS = ("manual", "schedule", "drop", "upload")
# App Flow §8: queued → running → validated → committed | quarantined | failed.
RUN_STATES = ("queued", "running", "validated", "committed", "quarantined", "failed")
RUN_OUTCOMES = ("success", "quarantined", "failed")
SEVERITIES = ("error", "warning")


class CredentialSecret(Tracked):
    """A Fernet-encrypted secret. ``connection.secret_ref`` points here; nothing else reads it."""

    secret_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    ciphertext = models.TextField()

    class Meta:
        db_table = "credential_secret"

    def __str__(self) -> str:
        return f"secret {self.secret_id}"


class Connection(Tracked):
    """A read-only source database. kpiGo stores where and as whom, never what SQL to run."""

    connection_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    name = models.TextField()
    driver = models.TextField()
    host = models.TextField()
    port = models.IntegerField()
    database = models.TextField()
    username = models.TextField()
    secret_ref = models.UUIDField(null=True)
    # statement_timeout_seconds, row_cap: limits on every read through this connection.
    options = models.JSONField(default=dict)
    status = models.TextField(db_default="active")

    class Meta:
        db_table = "connection"
        constraints = [
            models.UniqueConstraint(fields=["org_id", "name"], name="connection_name_unique"),
            one_of("driver", DRIVERS, "connection_driver_valid"),
            one_of("status", CONNECTION_STATUSES, "connection_status_valid"),
            models.CheckConstraint(
                condition=models.Q(port__gt=0, port__lt=65536), name="connection_port_valid"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.driver})"


class Feed(Tracked):
    feed_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    name = models.TextField()
    template_name = models.TextField()
    mode = models.TextField()
    connection = models.ForeignKey(
        Connection, on_delete=models.PROTECT, null=True, related_name="feeds"
    )
    source_object = models.TextField(null=True)
    drop_path = models.TextField(null=True)
    cadence_cron = models.TextField(null=True)
    owner_user_id = models.BigIntegerField(null=True)
    deadline_offset_hours = models.IntegerField(null=True)
    expected_row_min = models.IntegerField(null=True)
    expected_row_max = models.IntegerField(null=True)
    # Volume gate: warn, then reject, past this % from the trailing average.
    volume_warn_pct = models.IntegerField(db_default=25)
    volume_reject_pct = models.IntegerField(db_default=75)
    # Stale once the last good load is older than this (null: deadlines only).
    freshness_tolerance_hours = models.IntegerField(null=True)
    status = models.TextField(db_default="active")
    last_run_id = models.UUIDField(null=True)
    last_success_at = models.DateTimeField(null=True)
    last_scheduled_at = models.DateTimeField(null=True)
    freshness_state = models.TextField(db_default="never_loaded")
    stale_reason = models.TextField(null=True)
    stale_since = models.DateTimeField(null=True)

    class Meta:
        db_table = "feed"
        constraints = [
            models.UniqueConstraint(fields=["org_id", "name"], name="feed_name_unique"),
            one_of("template_name", LOADABLE_TEMPLATES, "feed_template_valid"),
            one_of("mode", FEED_MODES, "feed_mode_valid"),
            one_of("status", FEED_STATUSES, "feed_status_valid"),
            one_of("freshness_state", FRESHNESS_STATES, "feed_freshness_valid"),
            models.CheckConstraint(
                condition=~models.Q(mode="pull")
                | models.Q(connection__isnull=False, source_object__isnull=False),
                name="feed_pull_has_source",
            ),
            models.CheckConstraint(
                condition=~models.Q(mode="drop") | models.Q(drop_path__isnull=False),
                name="feed_drop_has_path",
            ),
            models.CheckConstraint(
                condition=models.Q(volume_warn_pct__gte=0)
                & models.Q(volume_reject_pct__gte=models.F("volume_warn_pct")),
                name="feed_volume_thresholds_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.template_name}, {self.mode})"


class FeedRun(Stamped):
    run_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    feed = models.ForeignKey(Feed, on_delete=models.PROTECT, related_name="runs")
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True)
    trigger = models.TextField()
    is_dry_run = models.BooleanField()
    restatement = models.BooleanField(db_default=False)
    state = models.TextField(db_default="running")
    outcome = models.TextField(null=True)
    source_name = models.TextField(db_default="")
    rows_read = models.IntegerField(db_default=0)
    rows_accepted = models.IntegerField(db_default=0)
    rows_rejected = models.IntegerField(db_default=0)
    warnings = models.IntegerField(db_default=0)
    content_hash = models.TextField(null=True)
    periods = models.JSONField(default=list)
    gate_summary = models.JSONField(default=dict)
    diff_summary = models.JSONField(null=True)
    error_text = models.TextField(null=True)
    # The earlier successful run whose facts this one replaced.
    supersedes_run_id = models.UUIDField(null=True)

    class Meta:
        db_table = "feed_run"
        constraints = [
            one_of("trigger", RUN_TRIGGERS, "feed_run_trigger_valid"),
            one_of("state", RUN_STATES, "feed_run_state_valid"),
            models.CheckConstraint(
                condition=models.Q(outcome__isnull=True) | models.Q(outcome__in=RUN_OUTCOMES),
                name="feed_run_outcome_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["feed", "-started_at"], name="feed_run_recent"),
            models.Index(
                fields=["feed", "content_hash"],
                name="feed_run_success_hash",
                condition=models.Q(is_dry_run=False, outcome="success"),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.run_id} {'dry run' if self.is_dry_run else 'load'} {self.state}"


class FeedRejection(Stamped):
    """One finding of a run: the row, column and rule (PRD IN-8). Warnings are kept too."""

    pk = models.CompositePrimaryKey("run_id", "seq")
    run = models.ForeignKey(FeedRun, on_delete=models.CASCADE, related_name="rejections")
    seq = models.IntegerField()
    row_no = models.IntegerField(null=True)
    column_name = models.TextField(null=True)
    gate = models.TextField()
    rule = models.TextField()
    severity = models.TextField()
    value = models.TextField(null=True)
    message = models.TextField()

    class Meta:
        db_table = "feed_rejection"
        constraints = [
            one_of("gate", GATES, "feed_rejection_gate_valid"),
            one_of("severity", SEVERITIES, "feed_rejection_severity_valid"),
        ]

    def __str__(self) -> str:
        return f"{self.run_id} row {self.row_no} {self.rule}"


# ── landing templates (Schema §7) ────────────────────────────────────────────


class Landing(Stamped):
    """Raw rows of one run. Every template column is text: landing never coerces."""

    org_id = models.UUIDField()
    run = models.ForeignKey(FeedRun, on_delete=models.CASCADE, related_name="+")
    row_no = models.IntegerField()

    class Meta:
        abstract = True


def _text() -> models.TextField:  # type: ignore[type-arg]
    return models.TextField(null=True)


class TmplSubject(Landing):
    pk = models.CompositePrimaryKey("run_id", "row_no")
    staff_no = _text()
    full_name = _text()
    email = _text()
    portfolio_code = _text()
    staff_ref = _text()
    status = _text()

    class Meta:
        db_table = "tmpl_subject"


class TmplAssignment(Landing):
    pk = models.CompositePrimaryKey("run_id", "row_no")
    staff_no = _text()
    role_code = _text()
    profile_code = _text()
    manager_staff_no = _text()
    relationship_type = _text()
    branch_code = _text()
    region_code = _text()
    segment_code = _text()
    effective_from = _text()
    effective_to = _text()

    class Meta:
        db_table = "tmpl_assignment"


class TmplDimension(Landing):
    pk = models.CompositePrimaryKey("run_id", "row_no")
    dimension_type = _text()
    member_code = _text()
    member_name = _text()
    parent_code = _text()

    class Meta:
        db_table = "tmpl_dimension"


class TmplTarget(Landing):
    pk = models.CompositePrimaryKey("run_id", "row_no")
    metric_code = _text()
    scope_type = _text()
    scope_code = _text()
    period_key = _text()
    series_type = _text()
    target_value = _text()
    target_type = _text()
    weight = _text()
    cap = _text()
    currency_code = _text()

    class Meta:
        db_table = "tmpl_target"


class TmplActualMonthly(Landing):
    pk = models.CompositePrimaryKey("run_id", "row_no")
    metric_code = _text()
    subject_ref = _text()
    period_key = _text()
    actual_value = _text()
    currency_code = _text()

    class Meta:
        db_table = "tmpl_actual_monthly"


class TmplActualDaily(Landing):
    pk = models.CompositePrimaryKey("run_id", "row_no")
    metric_code = _text()
    subject_ref = _text()
    activity_date = _text()
    product_line_code = _text()
    actual_value = _text()
    currency_code = _text()

    class Meta:
        db_table = "tmpl_actual_daily"


class TmplActualDimensional(Landing):
    pk = models.CompositePrimaryKey("run_id", "row_no")
    metric_code = _text()
    dimension_type = _text()
    member_code = _text()
    period_key = _text()
    actual_value = _text()
    currency_code = _text()

    class Meta:
        db_table = "tmpl_actual_dimensional"


class TmplWidgetData(Landing):
    pk = models.CompositePrimaryKey("run_id", "row_no")
    widget_key = _text()
    metric_code = _text()
    period_key = _text()
    dimension_type = _text()
    member_code = _text()
    series_type = _text()
    value = _text()
    currency_code = _text()

    class Meta:
        db_table = "tmpl_widget_data"


class TmplCampaignOutcome(Landing):
    pk = models.CompositePrimaryKey("run_id", "row_no")
    customer_ref = _text()
    metric_code = _text()
    campaign_code = _text()
    activity_date = _text()
    activity_value = _text()
    currency_code = _text()
    source_ref = _text()
    segment_code = _text()
    product_code = _text()
    region_code = _text()
    branch_code = _text()

    class Meta:
        db_table = "tmpl_campaign_outcome"


class TmplCampaignPopulation(Landing):
    pk = models.CompositePrimaryKey("run_id", "row_no")
    snapshot_date = _text()
    segment_code = _text()
    product_code = _text()
    region_code = _text()
    branch_code = _text()
    customer_count = _text()

    class Meta:
        db_table = "tmpl_campaign_population"


class TmplCampaignContact(Landing):
    pk = models.CompositePrimaryKey("run_id", "row_no")
    customer_ref = _text()
    campaign_code = _text()
    channel = _text()
    contact_date = _text()
    delivered = _text()
    responded = _text()
    holdout = _text()

    class Meta:
        db_table = "tmpl_campaign_contact"


class TmplCampaignWinback(Landing):
    pk = models.CompositePrimaryKey("run_id", "row_no")
    customer_ref = _text()
    campaign_code = _text()
    qualified_at = _text()
    winback_flag = _text()
    account_status = _text()
    retention_confirmed_at = _text()
    segment_code = _text()
    product_code = _text()
    region_code = _text()
    branch_code = _text()

    class Meta:
        db_table = "tmpl_campaign_winback"


LANDING: dict[str, type[Landing]] = {
    "subject": TmplSubject,
    "assignment": TmplAssignment,
    "dimension": TmplDimension,
    "target": TmplTarget,
    "actual_monthly": TmplActualMonthly,
    "actual_daily": TmplActualDaily,
    "actual_dimensional": TmplActualDimensional,
    "widget_data": TmplWidgetData,
    "campaign_outcome": TmplCampaignOutcome,
    "campaign_population": TmplCampaignPopulation,
    "campaign_contact": TmplCampaignContact,
    "campaign_winback": TmplCampaignWinback,
}
assert set(LANDING) == set(TEMPLATES), "every template needs a landing table"


# ── facts (Schema §8) ────────────────────────────────────────────────────────


def _period_check(name: str) -> models.CheckConstraint:
    return models.CheckConstraint(
        condition=models.Q(period_key__regex=PERIOD_KEY_PATTERN), name=name
    )


class FactActualMonthly(Stamped):
    """A subject's actual for a month. A missing row is absent, never zero."""

    pk = models.CompositePrimaryKey("metric_id", "subject_id", "period_key")
    org_id = models.UUIDField()
    metric = models.ForeignKey(Metric, on_delete=models.PROTECT, related_name="+")
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name="+")
    assignment = models.ForeignKey(Assignment, on_delete=models.PROTECT, related_name="+")
    period_key = models.CharField(max_length=6)
    actual_value = models.DecimalField(max_digits=18, decimal_places=4)
    currency_code = models.CharField(max_length=3, null=True)
    # The feed run that wrote it; null for manual input (MI-11 provenance instead).
    run_id = models.UUIDField(null=True)
    loaded_at = models.DateTimeField()

    class Meta:
        db_table = "fact_actual_monthly"
        constraints = [_period_check("fact_actual_monthly_period_key_valid")]
        indexes = [
            models.Index(fields=["subject", "period_key"], name="fact_monthly_subject_period"),
            models.Index(fields=["run_id"], name="fact_monthly_run"),
        ]

    def __str__(self) -> str:
        return f"{self.metric_id} {self.subject_id} {self.period_key}"


class FactActualDimensional(Stamped):
    pk = models.CompositePrimaryKey("metric_id", "dimension_type", "member_code", "period_key")
    org_id = models.UUIDField()
    metric = models.ForeignKey(Metric, on_delete=models.PROTECT, related_name="+")
    dimension_type = models.TextField()
    member_code = models.TextField()
    period_key = models.CharField(max_length=6)
    actual_value = models.DecimalField(max_digits=18, decimal_places=4)
    currency_code = models.CharField(max_length=3, null=True)
    run_id = models.UUIDField(null=True)
    loaded_at = models.DateTimeField()

    class Meta:
        db_table = "fact_actual_dimensional"
        constraints = [_period_check("fact_actual_dimensional_period_key_valid")]
        indexes = [models.Index(fields=["run_id"], name="fact_dimensional_run")]

    def __str__(self) -> str:
        return f"{self.metric_id} {self.dimension_type}:{self.member_code} {self.period_key}"


class FactWidgetData(Stamped):
    """One series value for one metric of one Executive widget (Scope §10.5).

    Pre-shaped by the client's DE team and read as it is. ``dimension_type`` and
    ``member_code`` are both '' for an organisation-level value.
    """

    fact_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    widget_key = models.TextField()
    metric = models.ForeignKey(Metric, on_delete=models.PROTECT, related_name="+")
    period_key = models.CharField(max_length=6)
    dimension_type = models.TextField(db_default="")
    member_code = models.TextField(db_default="")
    series_type = models.TextField()
    value = models.DecimalField(max_digits=18, decimal_places=4)
    currency_code = models.CharField(max_length=3, null=True)
    run_id = models.UUIDField(null=True)
    loaded_at = models.DateTimeField()

    class Meta:
        db_table = "fact_widget_data"
        constraints = [
            _period_check("fact_widget_data_period_key_valid"),
            one_of("series_type", WIDGET_SERIES, "fact_widget_data_series_valid"),
            models.CheckConstraint(
                condition=models.Q(dimension_type="", member_code="")
                | (~models.Q(dimension_type="") & ~models.Q(member_code="")),
                name="fact_widget_data_whole_dimension",
            ),
            models.UniqueConstraint(
                fields=[
                    "org_id",
                    "widget_key",
                    "metric",
                    "period_key",
                    "dimension_type",
                    "member_code",
                    "series_type",
                ],
                name="fact_widget_data_grain",
            ),
        ]
        indexes = [
            models.Index(fields=["org_id", "widget_key", "period_key"], name="fact_widget_key"),
            models.Index(fields=["run_id"], name="fact_widget_run"),
        ]

    def __str__(self) -> str:
        return f"{self.widget_key} {self.metric_id} {self.period_key} {self.series_type}"


# ── daily retention (PRD AP-12, Scope §8.4) ─────────────────────────────────

ARCHIVE_STATUSES = ("archived", "restored")


class DailyArchive(Tracked):
    """A month of ``fact_actual_daily`` rolled up to monthly and moved to the archive.

    Install-wide, not per org: a partition holds every org's month. The daily rows
    are never deleted; the partition is detached into the archive schema, and
    ``restored`` re-attaches it.
    """

    month = models.DateField(primary_key=True)
    partition_name = models.TextField()
    archive_schema = models.TextField()
    status = models.TextField(db_default="archived")
    daily_rows = models.BigIntegerField()
    # Monthly rows the roll-up wrote; a month already reported monthly is kept as it was.
    rolled_up_rows = models.BigIntegerField()
    # Metric × subject pairs left out of the roll-up because their days mixed currencies.
    mixed_currency_pairs = models.BigIntegerField(db_default=0)
    archived_at = models.DateTimeField()
    restored_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "daily_archive"
        constraints = [one_of("status", ARCHIVE_STATUSES, "daily_archive_status_valid")]

    def __str__(self) -> str:
        return f"{self.partition_name} {self.status}"
