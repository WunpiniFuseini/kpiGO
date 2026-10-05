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
from kpigo.platform.db import Stamped, Tracked, one_of
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
                    "version",
                ],
                name="target_version_unique",
            ),
            # At most one draft and one live version per target key.
            models.UniqueConstraint(
                fields=["metric", "scope_type", "scope_code", "period_key", "series_type"],
                condition=models.Q(state="draft"),
                name="target_one_draft",
            ),
            models.UniqueConstraint(
                fields=["metric", "scope_type", "scope_code", "period_key", "series_type"],
                condition=models.Q(state="published"),
                name="target_one_published",
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
