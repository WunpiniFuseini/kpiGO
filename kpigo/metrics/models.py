"""Metric registry (Schema §2).

A family is the named concept ("Total deposits"); its unique normalised name is
the hard guard against duplicates. A metric is one versioned definition in a
family. Rows sharing a ``metric_code`` are the effective periods of one metric:
changing direction, aggregation, unit or target scope on an active metric closes
the current row and opens a new one (PRD MR-7), so historical scores stay
reproducible against the definition they were computed under. Feeds reference
``metric_code``; conform resolves it to the row in force for the period.
"""

import uuid
from typing import Literal, get_args

from django.contrib.postgres.indexes import GinIndex
from django.db import models

from kpigo.platform.db import Stamped, Tracked, no_overlap, one_of, valid_range
from kpigo.platform.vocab import PRODUCTS

Direction = Literal["higher_is_better", "lower_is_better"]
Aggregation = Literal["sum", "average", "latest", "count", "ratio"]
Unit = Literal["currency", "count", "percent", "days", "hours", "score"]
TargetScope = Literal["profile", "subject"]
CollectionMethod = Literal["feed", "manual_input"]
MetricStatus = Literal["draft", "active", "inactive", "deprecated"]

DIRECTIONS: tuple[str, ...] = get_args(Direction)
AGGREGATIONS: tuple[str, ...] = get_args(Aggregation)
UNITS: tuple[str, ...] = get_args(Unit)
TARGET_SCOPES: tuple[str, ...] = get_args(TargetScope)
COLLECTION_METHODS: tuple[str, ...] = get_args(CollectionMethod)
METRIC_STATUSES: tuple[str, ...] = get_args(MetricStatus)
METRIC_CODE_PATTERN = r"^[a-z][a-z0-9_]*$"

# Changing any of these on an active metric opens a new effective period (MR-7).
DEFINITIONAL_FIELDS = ("direction", "aggregation", "unit", "target_scope")


class MetricFamily(Tracked):
    family_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    display_name = models.TextField()
    normalised_name = models.TextField()
    description = models.TextField(db_default="")
    owner_user_id = models.BigIntegerField(null=True)

    class Meta:
        db_table = "metric_family"
        constraints = [
            models.UniqueConstraint(
                fields=["org_id", "normalised_name"], name="metric_family_name_unique"
            )
        ]
        indexes = [
            GinIndex(
                fields=["normalised_name"],
                name="metric_family_name_trgm",
                opclasses=["gin_trgm_ops"],
            )
        ]

    def __str__(self) -> str:
        return str(self.display_name)


class Metric(Tracked):
    metric_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    family = models.ForeignKey(MetricFamily, on_delete=models.PROTECT, related_name="metrics")
    metric_code = models.TextField()
    display_name = models.TextField()
    direction = models.TextField()
    aggregation = models.TextField()
    unit = models.TextField()
    decimal_places = models.SmallIntegerField(db_default=0)
    is_percentage = models.BooleanField(db_default=False)
    target_scope = models.TextField(db_default="profile")
    collection_method = models.TextField(db_default="feed")
    status = models.TextField(db_default="draft")
    computation_note = models.TextField(db_default="")
    effective_from = models.DateField()
    effective_to = models.DateField(null=True)
    # The effective period this one replaced, for the definition's lineage.
    supersedes = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, related_name="superseded_by"
    )

    class Meta:
        db_table = "metric"
        constraints = [
            one_of("direction", DIRECTIONS, "metric_direction_valid"),
            one_of("aggregation", AGGREGATIONS, "metric_aggregation_valid"),
            one_of("unit", UNITS, "metric_unit_valid"),
            one_of("target_scope", TARGET_SCOPES, "metric_target_scope_valid"),
            one_of("collection_method", COLLECTION_METHODS, "metric_collection_method_valid"),
            one_of("status", METRIC_STATUSES, "metric_status_valid"),
            models.CheckConstraint(
                condition=models.Q(decimal_places__gte=0, decimal_places__lte=6),
                name="metric_decimal_places_range",
            ),
            models.CheckConstraint(
                condition=models.Q(metric_code__regex=METRIC_CODE_PATTERN),
                name="metric_code_format",
            ),
            valid_range("metric_range_valid"),
            no_overlap("metric_code_no_overlap", "org_id", "metric_code"),
        ]
        indexes = [models.Index(fields=["org_id", "metric_code"], name="metric_code_lookup")]

    def __str__(self) -> str:
        return f"{self.metric_code} from {self.effective_from}"


class MetricBinding(Stamped):
    pk = models.CompositePrimaryKey("metric_id", "product")
    metric = models.ForeignKey(Metric, on_delete=models.PROTECT, related_name="bindings")
    product = models.TextField()
    is_active = models.BooleanField(db_default=True)

    class Meta:
        db_table = "metric_binding"
        constraints = [one_of("product", PRODUCTS, "metric_binding_product_valid")]

    def __str__(self) -> str:
        return f"{self.metric_id} @ {self.product}"


class MetricProfileAssignment(Stamped):
    """Which metrics appear for which profile, per product."""

    pk = models.CompositePrimaryKey("metric_id", "profile_code", "product", "effective_from")
    metric = models.ForeignKey(Metric, on_delete=models.PROTECT, related_name="profiles")
    profile_code = models.TextField()
    product = models.TextField()
    effective_from = models.DateField()
    effective_to = models.DateField(null=True)

    class Meta:
        db_table = "metric_profile_assignment"
        constraints = [
            one_of("product", PRODUCTS, "metric_profile_product_valid"),
            valid_range("metric_profile_range_valid"),
            no_overlap("metric_profile_no_overlap", "metric", "profile_code", "product"),
        ]

    def __str__(self) -> str:
        return f"{self.metric_id} → {self.profile_code} ({self.product})"
