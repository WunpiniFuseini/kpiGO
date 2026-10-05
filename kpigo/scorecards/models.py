"""Scorecard configuration (Schema §3) and targets (Schema §6).

The taxonomy is the client's own: one to three levels above the metric
("Objective → Goal → metric"). The matrix, the summary groupings and the PDF all
render from it, so no client needs code. A placement puts a metric under a node,
by ``metric_code`` rather than ``metric_id`` so it survives a metric opening a
new effective period (MR-7); a profile-specific placement wins over the default.

Targets are versioned rows. ``draft`` rows live only on the workbench;
publishing is a batch event that turns them ``published`` and the version they
replace ``superseded``. Nothing is updated in place once published, so a
cycle's targets can be reasoned about, and reverted, as a unit.
"""

import uuid

from django.contrib.postgres.fields import ArrayField
from django.db import models

from kpigo.metrics.models import Metric
from kpigo.periods.models import PerformanceCycle
from kpigo.platform.db import Stamped, Tracked, no_overlap, one_of
from kpigo.platform.vocab import PERIOD_KEY_PATTERN, PRODUCTS

TEMPLATE_STATUSES = ("draft", "active", "inactive")
DENOMINATOR_POLICIES = ("reduced", "redistribute")

TARGET_SCOPE_TYPES = ("profile", "subject")
SERIES_TYPES = ("target", "forecast", "budget")
TARGET_TYPES = ("monthly", "yearly", "cumulative", "quarterly", "prorated")
TARGET_STATES = ("draft", "published", "superseded")
TARGET_SOURCES = ("upload", "copy_forward", "workbench")
BATCH_STATUSES = ("published", "reverted")


def _period_check(name: str, field: str = "period_key") -> models.CheckConstraint:
    return models.CheckConstraint(
        condition=models.Q(**{f"{field}__regex": PERIOD_KEY_PATTERN}), name=name
    )


# ── taxonomy (Schema §3) ─────────────────────────────────────────────────────


class ScorecardTemplate(Tracked):
    """The client's taxonomy. One template is active per org at a time."""

    template_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    name = models.TextField()
    level_count = models.SmallIntegerField()
    status = models.TextField(db_default="draft")

    class Meta:
        db_table = "scorecard_template"
        constraints = [
            models.UniqueConstraint(fields=["org_id", "name"], name="scorecard_template_name"),
            models.UniqueConstraint(
                fields=["org_id"],
                condition=models.Q(status="active"),
                name="scorecard_template_one_active",
            ),
            models.CheckConstraint(
                condition=models.Q(level_count__gte=1, level_count__lte=3),
                name="scorecard_template_levels_1_to_3",
            ),
            one_of("status", TEMPLATE_STATUSES, "scorecard_template_status_valid"),
        ]

    def __str__(self) -> str:
        return str(self.name)


class ScorecardLevel(Tracked):
    """The label of one level, e.g. 1 = "Strategic objective", 2 = "Value driver"."""

    pk = models.CompositePrimaryKey("template_id", "level_no")
    template = models.ForeignKey(ScorecardTemplate, on_delete=models.PROTECT, related_name="levels")
    level_no = models.SmallIntegerField()
    label = models.TextField()

    class Meta:
        db_table = "scorecard_level"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(level_no__gte=1, level_no__lte=3),
                name="scorecard_level_no_range",
            )
        ]

    def __str__(self) -> str:
        return f"{self.template_id} L{self.level_no} {self.label}"


class ScorecardNode(Tracked):
    node_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    template = models.ForeignKey(ScorecardTemplate, on_delete=models.PROTECT, related_name="nodes")
    level_no = models.SmallIntegerField()
    parent = models.ForeignKey("self", on_delete=models.PROTECT, null=True, related_name="children")
    label = models.TextField()
    sort_order = models.IntegerField(db_default=0)

    class Meta:
        db_table = "scorecard_node"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(level_no__gte=1, level_no__lte=3),
                name="scorecard_node_level_range",
            ),
            # A top-level node has no parent; every other node has one.
            models.CheckConstraint(
                condition=models.Q(level_no=1, parent__isnull=True)
                | models.Q(level_no__gt=1, parent__isnull=False),
                name="scorecard_node_parent_by_level",
            ),
            models.UniqueConstraint(
                fields=["template", "parent", "label"],
                name="scorecard_node_label_unique",
                nulls_distinct=False,
            ),
        ]

    def __str__(self) -> str:
        return str(self.label)


class ScorecardPlacement(Tracked):
    """Where a metric sits in the taxonomy (Schema §3 ``scorecard_assignment``).

    ``profile_code`` null is the default placement; a profile's own row wins.
    """

    placement_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    template = models.ForeignKey(
        ScorecardTemplate, on_delete=models.PROTECT, related_name="placements"
    )
    node = models.ForeignKey(ScorecardNode, on_delete=models.PROTECT, related_name="placements")
    metric_code = models.TextField()
    profile_code = models.TextField(null=True)
    sort_order = models.IntegerField(db_default=0)

    class Meta:
        db_table = "scorecard_assignment"
        constraints = [
            models.UniqueConstraint(
                fields=["template", "metric_code", "profile_code"],
                name="scorecard_assignment_unique",
                nulls_distinct=False,
            )
        ]

    def __str__(self) -> str:
        return f"{self.metric_code} → {self.node_id} ({self.profile_code or 'default'})"


class RatingBand(Tracked):
    """A grade: label, the total score it starts at, and its step on the grade ramp.

    ``ramp_position`` keeps colour ordinal and monotonic: adding a band inserts a
    step rather than recolouring the others. No rows for a product means the
    standard four (``kpigo.scorecards.bands.DEFAULT_BANDS``).
    """

    band_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    product = models.TextField()
    label = models.TextField()
    threshold = models.DecimalField(max_digits=8, decimal_places=4)
    ramp_position = models.SmallIntegerField()
    colour_hex = models.TextField(null=True)
    sort_order = models.IntegerField(db_default=0)

    class Meta:
        db_table = "rating_band"
        constraints = [
            one_of("product", PRODUCTS, "rating_band_product_valid"),
            models.UniqueConstraint(
                fields=["org_id", "product", "label"], name="rating_band_label_unique"
            ),
            models.UniqueConstraint(
                fields=["org_id", "product", "threshold"], name="rating_band_threshold_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(ramp_position__gte=1, ramp_position__lte=7),
                name="rating_band_ramp_range",
            ),
            models.CheckConstraint(
                condition=models.Q(colour_hex__isnull=True)
                | models.Q(colour_hex__regex=r"^#[0-9A-Fa-f]{6}$"),
                name="rating_band_colour_hex",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.label} ≥ {self.threshold}"


class ScorecardSettings(Tracked):
    """Per-org scoring settings. No row means the defaults below."""

    org_id = models.UUIDField(primary_key=True)
    # Weights for a profile in a period must sum to this, within the tolerance (SC-10).
    weight_total = models.DecimalField(max_digits=8, decimal_places=3, db_default=100)
    weight_tolerance = models.DecimalField(max_digits=8, decimal_places=3, db_default=0.5)
    # A cap below weight × min ratio, or above weight × max ratio, is implausible.
    cap_min_ratio = models.DecimalField(max_digits=6, decimal_places=3, db_default=1)
    cap_max_ratio = models.DecimalField(max_digits=6, decimal_places=3, db_default=3)
    # ``reduced``: unscored metrics leave the denominator (the default, SC-7).
    # ``redistribute``: their weight spreads over the scored metrics; explicit opt-in.
    denominator_policy = models.TextField(db_default="reduced")
    # Manual input (MI-7): due at the end of this working day of the following
    # month, on the business calendar; the contributor is reminded this many working
    # days before.
    input_due_working_day = models.SmallIntegerField(db_default=5)
    input_reminder_working_days = models.SmallIntegerField(db_default=2)
    # The escalation ladder (MI-8): the contributor's line manager is told this
    # many working days from the due day (negative = before it), then the
    # stakeholders. Null switches that rung off.
    input_manager_working_days = models.SmallIntegerField(null=True, db_default=0)
    input_stakeholder_working_days = models.SmallIntegerField(null=True, db_default=1)
    # Accounts (user_id) told at the last rung; empty means everyone who manages input.
    input_stakeholders = models.JSONField(
        db_default=models.Value([], output_field=models.JSONField())
    )

    class Meta:
        db_table = "scorecard_settings"
        constraints = [
            one_of("denominator_policy", DENOMINATOR_POLICIES, "scorecard_settings_policy_valid"),
            models.CheckConstraint(
                condition=models.Q(weight_total__gt=0, weight_tolerance__gte=0),
                name="scorecard_settings_weights_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(cap_min_ratio__gt=0)
                & models.Q(cap_max_ratio__gte=models.F("cap_min_ratio")),
                name="scorecard_settings_cap_ratios_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(input_due_working_day__gte=1, input_due_working_day__lte=20)
                & models.Q(input_reminder_working_days__gte=0, input_reminder_working_days__lte=10),
                name="scorecard_settings_input_schedule_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(input_manager_working_days__isnull=True)
                    | models.Q(
                        input_manager_working_days__gte=-10, input_manager_working_days__lte=10
                    )
                )
                & (
                    models.Q(input_stakeholder_working_days__isnull=True)
                    | models.Q(
                        input_stakeholder_working_days__gte=-10,
                        input_stakeholder_working_days__lte=10,
                    )
                ),
                name="scorecard_settings_input_ladder_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"scorecard settings {self.org_id}"


# ── targets (Schema §6) ──────────────────────────────────────────────────────


class TargetPublishBatch(Stamped):
    """One publish: a cycle's (or a period's) targets made live together."""

    batch_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    cycle = models.ForeignKey(
        PerformanceCycle, on_delete=models.PROTECT, null=True, related_name="+"
    )
    period_keys = ArrayField(models.CharField(max_length=6))
    published_at = models.DateTimeField()
    published_by = models.BigIntegerField(null=True)
    weight_check_result = models.JSONField(db_default=models.Value({}, models.JSONField()))
    row_count = models.IntegerField()
    note = models.TextField(db_default="")
    status = models.TextField(db_default="published")
    reverted_at = models.DateTimeField(null=True)
    reverted_by = models.BigIntegerField(null=True)

    class Meta:
        db_table = "target_publish_batch"
        constraints = [one_of("status", BATCH_STATUSES, "target_publish_batch_status_valid")]

    def __str__(self) -> str:
        return f"batch {self.batch_id} ({self.row_count} rows)"


class Target(Tracked):
    """``draft → published → superseded`` (App Flow §8).

    A trigger refuses a row whose ``scope_type`` contradicts the metric's
    ``target_scope``: the check that stops a client half-populating both shapes.
    For ``subject`` scope, ``scope_code`` is the subject_id.
    """

    target_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    metric = models.ForeignKey(Metric, on_delete=models.PROTECT, related_name="+")
    scope_type = models.TextField()
    scope_code = models.TextField()
    period_key = models.CharField(max_length=6)
    series_type = models.TextField(db_default="target")
    target_value = models.DecimalField(max_digits=18, decimal_places=4)
    target_type = models.TextField(db_default="monthly")
    weight = models.DecimalField(max_digits=6, decimal_places=3, null=True)
    cap = models.DecimalField(max_digits=6, decimal_places=3, null=True)
    currency_code = models.CharField(max_length=3, null=True)
    # An Agent Performance target for one product line (Scope §8.5); "" is the
    # metric's own target across every line, the only kind Scorecards reads.
    product_line_code = models.TextField(db_default="")
    version = models.IntegerField()
    state = models.TextField(db_default="draft")
    source = models.TextField(db_default="workbench")
    published_at = models.DateTimeField(null=True)
    published_by = models.BigIntegerField(null=True)
    batch = models.ForeignKey(
        TargetPublishBatch, on_delete=models.PROTECT, null=True, related_name="targets"
    )

    class Meta:
        db_table = "target"
        constraints = [
            one_of("scope_type", TARGET_SCOPE_TYPES, "target_scope_type_valid"),
            one_of("series_type", SERIES_TYPES, "target_series_type_valid"),
            one_of("target_type", TARGET_TYPES, "target_target_type_valid"),
            one_of("state", TARGET_STATES, "target_state_valid"),
            one_of("source", TARGET_SOURCES, "target_source_valid"),
            _period_check("target_period_key_valid"),
            models.UniqueConstraint(
                fields=[
                    "metric",
                    "scope_type",
                    "scope_code",
                    "period_key",
                    "series_type",
                    "product_line_code",
                    "version",
                ],
                name="target_line_version_unique",
            ),
            # At most one draft and one live version per target key.
            models.UniqueConstraint(
                fields=[
                    "metric",
                    "scope_type",
                    "scope_code",
                    "period_key",
                    "series_type",
                    "product_line_code",
                ],
                condition=models.Q(state="draft"),
                name="target_line_one_draft",
            ),
            models.UniqueConstraint(
                fields=[
                    "metric",
                    "scope_type",
                    "scope_code",
                    "period_key",
                    "series_type",
                    "product_line_code",
                ],
                condition=models.Q(state="published"),
                name="target_line_one_published",
            ),
            models.CheckConstraint(
                condition=models.Q(weight__isnull=True) | models.Q(weight__gte=0),
                name="target_weight_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(cap__isnull=True) | models.Q(cap__gte=0),
                name="target_cap_non_negative",
            ),
            models.CheckConstraint(
                condition=~models.Q(state="draft") | models.Q(batch__isnull=True),
                name="target_draft_unbatched",
            ),
        ]
        indexes = [
            models.Index(fields=["org_id", "period_key", "state"], name="target_period_state"),
            models.Index(fields=["scope_type", "scope_code"], name="target_scope"),
        ]

    def __str__(self) -> str:
        return f"{self.metric_id} {self.scope_type}:{self.scope_code} {self.period_key} v{self.version}"


# ── overrides (Schema §6, PRD SC-5) ──────────────────────────────────────────

OVERRIDE_SCOPE_TYPES = ("subject", "profile", "dimension")
OVERRIDE_CHANGE_TYPES = ("target", "weight", "cap", "actual", "target_type")
OVERRIDE_STATUSES = ("pending", "approved", "rejected", "withdrawn", "revoked")
# Dimension overrides reach a subject through these fields of the assignment in force.
OVERRIDE_DIMENSIONS = ("branch", "region", "segment", "portfolio")


class Override(Tracked):
    """A period-ranged exception to a target, weight, cap, actual or target type.

    Requested by one user and approved by another: ``reason`` and the approver
    are mandatory, and only ``approved`` rows move a score. Precedence is
    subject > profile > dimension for the overlapping periods only. For
    ``dimension`` scope, ``scope_code`` is ``<dimension>:<member_code>``.
    """

    override_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    scope_type = models.TextField()
    scope_code = models.TextField()
    metric = models.ForeignKey(Metric, on_delete=models.PROTECT, related_name="+")
    product = models.TextField(db_default="scorecards")
    period_from = models.CharField(max_length=6)
    # Null: the single period ``period_from``.
    period_to = models.CharField(max_length=6, null=True)
    change_type = models.TextField()
    override_value = models.DecimalField(max_digits=18, decimal_places=4, null=True)
    override_text = models.TextField(null=True)
    reason = models.TextField()
    status = models.TextField(db_default="pending")
    requested_by = models.BigIntegerField(null=True)
    approved_by = models.BigIntegerField(null=True)
    approved_at = models.DateTimeField(null=True)
    decision_note = models.TextField(db_default="")
    ended_by = models.BigIntegerField(null=True)
    ended_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "override"
        constraints = [
            one_of("scope_type", OVERRIDE_SCOPE_TYPES, "override_scope_type_valid"),
            one_of("change_type", OVERRIDE_CHANGE_TYPES, "override_change_type_valid"),
            one_of("status", OVERRIDE_STATUSES, "override_status_valid"),
            one_of("product", PRODUCTS, "override_product_valid"),
            models.CheckConstraint(
                condition=models.Q(period_from__regex=PERIOD_KEY_PATTERN)
                & (
                    models.Q(period_to__isnull=True) | models.Q(period_to__regex=PERIOD_KEY_PATTERN)
                ),
                name="override_period_key_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(period_to__isnull=True)
                | models.Q(period_to__gte=models.F("period_from")),
                name="override_period_range_valid",
            ),
            models.CheckConstraint(condition=~models.Q(reason=""), name="override_reason_required"),
            # target_type carries text; every other change carries a number.
            models.CheckConstraint(
                condition=(
                    models.Q(change_type="target_type", override_text__in=TARGET_TYPES)
                    | (
                        ~models.Q(change_type="target_type")
                        & models.Q(override_value__isnull=False)
                    )
                ),
                name="override_value_shape",
            ),
            # Approved means a second person approved it.
            models.CheckConstraint(
                condition=~models.Q(status="approved")
                | (
                    models.Q(approved_by__isnull=False, approved_at__isnull=False)
                    & ~models.Q(approved_by=models.F("requested_by"))
                ),
                name="override_approved_by_other",
            ),
        ]
        indexes = [
            models.Index(fields=["org_id", "product", "status"], name="override_status"),
            models.Index(fields=["scope_type", "scope_code"], name="override_scope"),
        ]

    def __str__(self) -> str:
        return f"{self.change_type} {self.scope_type}:{self.scope_code} {self.period_from}"


# ── close: exclusions and frozen snapshots (Schema §9, PRD SC-9, SC-12, SC-13) ──

SNAPSHOT_KINDS = ("close", "restatement")
SCORE_STATES = ("scored", "zero_actual", "not_reported", "no_target", "no_fx_rate", "excluded")


class ScoreExclusion(Stamped):
    """An explicit decision to leave a metric out of a period's scores, with a reason.

    Close is blocked while any metric is unscored (SC-9): the Admin chases the
    feed or records one of these. ``subject`` null excludes the metric for
    everyone whose scorecard carries it in the period.
    """

    exclusion_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    product = models.TextField(db_default="scorecards")
    period_key = models.CharField(max_length=6)
    metric = models.ForeignKey(Metric, on_delete=models.PROTECT, related_name="+")
    subject = models.ForeignKey(
        "hierarchy.Subject", on_delete=models.PROTECT, null=True, related_name="+"
    )
    reason = models.TextField()

    class Meta:
        db_table = "score_exclusion"
        constraints = [
            one_of("product", PRODUCTS, "score_exclusion_product_valid"),
            _period_check("score_exclusion_period_key_valid"),
            models.CheckConstraint(
                condition=~models.Q(reason=""), name="score_exclusion_reason_required"
            ),
            models.UniqueConstraint(
                fields=["org_id", "product", "period_key", "metric", "subject"],
                name="score_exclusion_unique",
                nulls_distinct=False,
            ),
        ]

    def __str__(self) -> str:
        return f"exclude {self.metric_id} {self.period_key} {self.subject_id or 'all'}"


class ScoreSnapshot(Stamped):
    """One close or restatement of a period: the version every row below belongs to."""

    snapshot_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    product = models.TextField(db_default="scorecards")
    period_key = models.CharField(max_length=6)
    snapshot_version = models.IntegerField()
    kind = models.TextField()
    # Why the period was restated; empty for its first close.
    reason = models.TextField(db_default="")
    subjects = models.IntegerField()

    class Meta:
        db_table = "score_snapshot"
        constraints = [
            one_of("product", PRODUCTS, "score_snapshot_product_valid"),
            one_of("kind", SNAPSHOT_KINDS, "score_snapshot_kind_valid"),
            _period_check("score_snapshot_period_key_valid"),
            models.UniqueConstraint(
                fields=["org_id", "product", "period_key", "snapshot_version"],
                name="score_snapshot_version_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(kind="close", snapshot_version=1)
                | models.Q(kind="restatement", snapshot_version__gt=1),
                name="score_snapshot_kind_matches_version",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.product} {self.period_key} v{self.snapshot_version}"


class ScoreTotal(models.Model):
    """A subject's frozen total for a period version. Immutable but for ``is_current``."""

    score_total_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    snapshot = models.ForeignKey(ScoreSnapshot, on_delete=models.PROTECT, related_name="totals")
    subject = models.ForeignKey("hierarchy.Subject", on_delete=models.PROTECT, related_name="+")
    assignment = models.ForeignKey(
        "hierarchy.Assignment", on_delete=models.PROTECT, related_name="+"
    )
    product = models.TextField(db_default="scorecards")
    period_key = models.CharField(max_length=6)
    profile_code = models.TextField()
    policy = models.TextField()
    months_elapsed = models.SmallIntegerField()
    quarters_elapsed = models.SmallIntegerField()
    cycle_months = models.SmallIntegerField()
    total_score = models.DecimalField(max_digits=18, decimal_places=6)
    total_cap = models.DecimalField(max_digits=18, decimal_places=6)
    weight_scored = models.DecimalField(max_digits=18, decimal_places=4)
    weight_expected = models.DecimalField(max_digits=18, decimal_places=4)
    graded_score = models.DecimalField(max_digits=18, decimal_places=6, null=True)
    achievement_pct = models.DecimalField(max_digits=18, decimal_places=6, null=True)
    metrics_scored = models.SmallIntegerField()
    metrics_total = models.SmallIntegerField()
    not_reported = models.SmallIntegerField()
    no_target = models.SmallIntegerField()
    excluded = models.SmallIntegerField()
    # The band as it stood at close, so a later change to the bands moves nothing.
    band_id = models.UUIDField(null=True)
    band_label = models.TextField(null=True)
    band_threshold = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    band_ramp_position = models.SmallIntegerField(null=True)
    band_colour_hex = models.TextField(null=True)
    snapshot_version = models.IntegerField()
    is_current = models.BooleanField(db_default=True)
    computed_at = models.DateTimeField()

    class Meta:
        db_table = "score_total"
        constraints = [
            models.UniqueConstraint(
                fields=["subject", "product", "period_key", "snapshot_version"],
                name="score_total_version_unique",
            ),
            models.UniqueConstraint(
                fields=["subject", "product", "period_key"],
                condition=models.Q(is_current=True),
                name="score_total_one_current",
            ),
        ]
        indexes = [
            models.Index(
                fields=["org_id", "product", "period_key"],
                condition=models.Q(is_current=True),
                name="score_total_current",
            )
        ]

    def __str__(self) -> str:
        return f"{self.subject_id} {self.period_key} v{self.snapshot_version}"


class ScoreHistory(models.Model):
    """A metric's frozen score with every input it was computed from (provenance)."""

    score_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    snapshot = models.ForeignKey(ScoreSnapshot, on_delete=models.PROTECT, related_name="scores")
    subject = models.ForeignKey("hierarchy.Subject", on_delete=models.PROTECT, related_name="+")
    assignment = models.ForeignKey(
        "hierarchy.Assignment", on_delete=models.PROTECT, related_name="+"
    )
    metric = models.ForeignKey(Metric, on_delete=models.PROTECT, related_name="+")
    product = models.TextField(db_default="scorecards")
    period_key = models.CharField(max_length=6)
    sort_order = models.SmallIntegerField()
    state = models.TextField()
    target = models.ForeignKey(Target, on_delete=models.PROTECT, null=True, related_name="+")
    target_version = models.IntegerField(null=True)
    target_scope = models.TextField(null=True)
    base_target = models.DecimalField(max_digits=18, decimal_places=4, null=True)
    target_type = models.TextField(null=True)
    target_currency = models.CharField(max_length=3, null=True)
    target_value = models.DecimalField(max_digits=18, decimal_places=4, null=True)
    reported_actual = models.DecimalField(max_digits=18, decimal_places=4, null=True)
    actual_currency = models.CharField(max_length=3, null=True)
    fx_rate_applied = models.DecimalField(max_digits=24, decimal_places=10, null=True)
    actual_value = models.DecimalField(max_digits=18, decimal_places=4, null=True)
    run_ids = ArrayField(models.UUIDField(), default=list)
    weight = models.DecimalField(max_digits=18, decimal_places=4, null=True)
    cap = models.DecimalField(max_digits=18, decimal_places=4, null=True)
    pct_achieved = models.DecimalField(max_digits=18, decimal_places=6, null=True)
    score = models.DecimalField(max_digits=18, decimal_places=6, null=True)
    override_ids = ArrayField(models.UUIDField(), default=list)
    # The overrides as they applied: change, scope, value and reason.
    overrides = models.JSONField(default=list)
    exclusion_reason = models.TextField(null=True)
    computed_at = models.DateTimeField()
    snapshot_version = models.IntegerField()
    is_current = models.BooleanField(db_default=True)

    class Meta:
        db_table = "score_history"
        constraints = [
            one_of("state", SCORE_STATES, "score_history_state_valid"),
            models.UniqueConstraint(
                fields=["subject", "metric", "product", "period_key", "snapshot_version"],
                name="score_history_version_unique",
            ),
        ]
        indexes = [
            models.Index(
                fields=["subject", "product", "period_key"],
                condition=models.Q(is_current=True),
                name="score_history_current",
            )
        ]

    def __str__(self) -> str:
        return f"{self.subject_id} {self.metric_id} {self.period_key} v{self.snapshot_version}"


# ── acknowledgement, queries and commentary (Schema §9, PRD SC-14–SC-16) ──────

INTERACTION_TYPES = ("acknowledgement", "query", "manager_comment")
INTERACTION_VISIBILITY = ("subject", "managers")
QUERY_OUTCOMES = ("explained", "adjusted")


class ScorecardInteraction(Tracked):
    """What people say about a scorecard: seen, queried, commented on.

    An acknowledgement records "seen", never "agreed": forcing agreement makes
    the record worthless. A query never reopens a period; its resolution either
    explains the standing figure or points at the override that will restate it.
    """

    interaction_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    subject = models.ForeignKey("hierarchy.Subject", on_delete=models.PROTECT, related_name="+")
    period_key = models.CharField(max_length=6)
    product = models.TextField(db_default="scorecards")
    interaction_type = models.TextField()
    metric = models.ForeignKey(Metric, on_delete=models.PROTECT, null=True, related_name="+")
    body = models.TextField(db_default="")
    author_user_id = models.BigIntegerField(null=True)
    # manager_comment: ``subject`` (default) or ``managers`` only.
    visibility = models.TextField(db_default="subject")
    # acknowledgement: the version seen.
    snapshot_version = models.IntegerField(null=True)
    # query: the line manager it was routed to when raised.
    routed_to = models.ForeignKey(
        "hierarchy.Subject", on_delete=models.PROTECT, null=True, related_name="+"
    )
    resolved_at = models.DateTimeField(null=True)
    resolved_by = models.BigIntegerField(null=True)
    outcome = models.TextField(null=True)
    resolution = models.TextField(null=True)
    resulting_override = models.ForeignKey(
        Override, on_delete=models.PROTECT, null=True, related_name="+"
    )

    class Meta:
        db_table = "scorecard_interaction"
        constraints = [
            one_of("interaction_type", INTERACTION_TYPES, "scorecard_interaction_type_valid"),
            one_of("visibility", INTERACTION_VISIBILITY, "scorecard_interaction_visibility_valid"),
            one_of("product", PRODUCTS, "scorecard_interaction_product_valid"),
            _period_check("scorecard_interaction_period_key_valid"),
            models.CheckConstraint(
                condition=models.Q(outcome__isnull=True) | models.Q(outcome__in=QUERY_OUTCOMES),
                name="scorecard_interaction_outcome_valid",
            ),
            models.CheckConstraint(
                condition=~models.Q(interaction_type="query") | models.Q(metric__isnull=False),
                name="scorecard_interaction_query_has_metric",
            ),
            models.CheckConstraint(
                condition=models.Q(resolved_at__isnull=True)
                | models.Q(interaction_type="query", outcome__isnull=False),
                name="scorecard_interaction_resolution_shape",
            ),
            models.UniqueConstraint(
                fields=["subject", "product", "period_key", "snapshot_version"],
                condition=models.Q(interaction_type="acknowledgement"),
                name="scorecard_interaction_one_ack_per_version",
            ),
        ]
        indexes = [
            models.Index(fields=["subject", "period_key"], name="scorecard_interaction_subject"),
            models.Index(
                fields=["org_id", "interaction_type"],
                condition=models.Q(resolved_at__isnull=True),
                name="scorecard_interaction_open",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.interaction_type} {self.subject_id} {self.period_key}"


# ── manual metric input (Schema §7.1, PRD MI-1–MI-13) ─────────────────────────

INPUT_SCOPE_TYPES = ("subject", "profile", "dimension")
INPUT_ASSIGNEE_TYPES = ("user", "role_relative")
INPUT_ROLES = ("line_manager_of",)
INPUT_STATES = ("draft", "submitted", "restated")


class InputAssignment(Tracked):
    """Who enters a manual-input metric for one slice of the scope.

    Keyed by ``metric_code`` so it survives the metric versioning (MR-7). A slice
    is one subject, a profile (every holder of it) or a dimension member
    (``branch:ACC``): one value for the whole slice, conformed to each member.
    ``role_relative`` (``line_manager_of``) resolves against the reporting lines
    in force for the period, so staff changes need no re-assignment.
    """

    assignment_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    metric_code = models.TextField()
    scope_type = models.TextField()
    scope_code = models.TextField()
    assignee_type = models.TextField()
    assignee_user = models.ForeignKey(
        "access.AppUser", on_delete=models.PROTECT, null=True, related_name="+"
    )
    assignee_role = models.TextField(null=True)
    # ``{"stakeholder_user_ids": [...]}``: who the last rung of the ladder tells
    # for this slice, in place of the org's stakeholders.
    escalation_config = models.JSONField(
        db_default=models.Value({}, output_field=models.JSONField())
    )
    effective_from = models.DateField()
    effective_to = models.DateField(null=True)

    class Meta:
        db_table = "input_assignment"
        constraints = [
            one_of("scope_type", INPUT_SCOPE_TYPES, "input_assignment_scope_valid"),
            one_of("assignee_type", INPUT_ASSIGNEE_TYPES, "input_assignment_assignee_type_valid"),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        assignee_type="user",
                        assignee_user__isnull=False,
                        assignee_role__isnull=True,
                    )
                    | models.Q(
                        assignee_type="role_relative",
                        assignee_user__isnull=True,
                        assignee_role__in=INPUT_ROLES,
                        scope_type="subject",
                    )
                ),
                name="input_assignment_assignee_shape",
            ),
            models.CheckConstraint(
                condition=models.Q(effective_to__isnull=True)
                | models.Q(effective_to__gt=models.F("effective_from")),
                name="input_assignment_range_valid",
            ),
            # One contributor owns a slice at a time.
            no_overlap(
                "input_assignment_no_overlap", "org_id", "metric_code", "scope_type", "scope_code"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.metric_code} {self.scope_type}:{self.scope_code}"


class InputSubmission(Tracked):
    """A contributor's value for a slice and a month, versioned.

    ``draft`` is saved but not conformed; ``submitted`` is conformed to
    ``fact_actual_monthly`` for every member of the slice. ``pending`` (no row)
    and ``locked`` (past the deadline) are read, not stored. A change after the
    deadline is a ``restated`` row with the next version, made only while the
    month is being restated; the prior version stays.
    """

    submission_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    input_assignment = models.ForeignKey(
        InputAssignment, on_delete=models.PROTECT, related_name="submissions"
    )
    metric = models.ForeignKey(Metric, on_delete=models.PROTECT, related_name="+")
    metric_code = models.TextField()
    scope_type = models.TextField()
    scope_code = models.TextField()
    period_key = models.CharField(max_length=6)
    value = models.DecimalField(max_digits=18, decimal_places=4, null=True)
    note = models.TextField(db_default="")
    submitted_by = models.BigIntegerField(null=True)
    submitted_at = models.DateTimeField(null=True)
    state = models.TextField(db_default="draft")
    approval_request_id = models.UUIDField(null=True)
    version = models.IntegerField(db_default=1)
    is_current = models.BooleanField(db_default=True)

    class Meta:
        db_table = "input_submission"
        constraints = [
            one_of("state", INPUT_STATES, "input_submission_state_valid"),
            one_of("scope_type", INPUT_SCOPE_TYPES, "input_submission_scope_valid"),
            _period_check("input_submission_period_key_valid"),
            models.CheckConstraint(
                condition=models.Q(state="draft")
                | models.Q(value__isnull=False, submitted_at__isnull=False),
                name="input_submission_submitted_shape",
            ),
            models.UniqueConstraint(
                fields=[
                    "org_id",
                    "metric_code",
                    "scope_type",
                    "scope_code",
                    "period_key",
                    "version",
                ],
                name="input_submission_version_unique",
            ),
            models.UniqueConstraint(
                fields=["org_id", "metric_code", "scope_type", "scope_code", "period_key"],
                condition=models.Q(is_current=True),
                name="input_submission_one_current",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.metric_code} {self.scope_code} {self.period_key} v{self.version}"


class InputSchedule(Stamped):
    """When a slice's input is due for a month, and how far the chase has gone."""

    schedule_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    input_assignment = models.ForeignKey(
        InputAssignment, on_delete=models.PROTECT, related_name="schedules"
    )
    period_key = models.CharField(max_length=6)
    due_at = models.DateTimeField()
    last_reminder_step = models.SmallIntegerField(db_default=0)
    last_reminded_at = models.DateTimeField(null=True)
    # Who the reminder went to: the contributor, resolved when it was sent.
    reminded_user = models.ForeignKey(
        "access.AppUser", on_delete=models.PROTECT, null=True, related_name="+"
    )

    class Meta:
        db_table = "input_schedule"
        constraints = [
            _period_check("input_schedule_period_key_valid"),
            models.UniqueConstraint(
                fields=["input_assignment", "period_key"], name="input_schedule_one_per_period"
            ),
        ]
        indexes = [
            models.Index(
                fields=["period_key", "due_at"],
                condition=models.Q(last_reminder_step__lt=3),
                name="input_schedule_open",
            )
        ]

    def __str__(self) -> str:
        return f"{self.input_assignment_id} {self.period_key}"


ESCALATION_STEPS = {1: "contributor", 2: "line_manager", 3: "stakeholders"}


class InputEscalation(Stamped):
    """One rung of the ladder reaching one person for one slice and month (MI-8).

    ``recipient`` is null when the rung resolved to nobody (a contributor with
    no line manager): recorded, so the gap is visible rather than silent.
    """

    escalation_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    schedule = models.ForeignKey(InputSchedule, on_delete=models.PROTECT, related_name="steps")
    step = models.SmallIntegerField()
    recipient = models.ForeignKey(
        "access.AppUser", on_delete=models.PROTECT, null=True, related_name="+"
    )
    # Who owed the input when this rung went out, for the message and the record.
    contributor = models.ForeignKey(
        "access.AppUser", on_delete=models.PROTECT, null=True, related_name="+"
    )
    sent_at = models.DateTimeField()
    # Null: no relay configured; false: the relay refused it or there is no address.
    emailed = models.BooleanField(null=True)

    class Meta:
        db_table = "input_escalation"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(step__in=list(ESCALATION_STEPS)),
                name="input_escalation_step_valid",
            ),
            models.UniqueConstraint(
                fields=["schedule", "step", "recipient"], name="input_escalation_once"
            ),
        ]
        indexes = [models.Index(fields=["recipient", "sent_at"], name="input_escalation_to")]

    def __str__(self) -> str:
        return f"{self.schedule_id} step {self.step}"
