"""Executive Dashboard widgets (Schema §11, Scope §10.4).

A widget is a ``widget_key`` and a declarative definition: which metrics it
shows, how (the type from the preset bundle), broken down by which dimension,
against which comparison series, with which thresholds, where on the grid.
Definitions are versioned config, never code: every change writes a new row and
the old one stays, so a placement, a type change or a threshold override is an
auditable, revertible, shippable piece of configuration (EX-10).

A key can exist before anyone places it. When a ``tmpl_widget_data`` load
carries a key kpiGo has not seen, the key is registered ``available`` with no
type, the same handshake product lines and dimension members use, so the DE
team can feed first and the Admin places the widget once the data is there
(App Flow §6.1). ``removed`` takes a widget off the dashboard and keeps its
history; placing it again starts from its last definition.
"""

import uuid

from django.db import models

from kpigo.access.models import AppUser
from kpigo.metrics.models import Metric
from kpigo.platform.db import Stamped, Tracked, one_of
from kpigo.platform.vocab import PERIOD_KEY_PATTERN

WIDGET_STATES = ("available", "placed", "removed")
WIDGET_TYPES = (
    "kpi_card",
    "bullet",
    "gauge",
    "bar",
    "line",
    "pie",
    "ranked_list",
    "table",
    "funnel",
)
# What a version changed, for the history panel and the audit trail.
WIDGET_CHANGES = (
    "detected",
    "declared",
    "placed",
    "updated",
    "type_changed",
    "thresholds_changed",
    "removed",
)
WIDGET_KEY_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"


class WidgetDefinition(Stamped):
    """One version of a widget. Rows are never updated except to retire ``is_current``."""

    widget_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    widget_key = models.TextField()
    version = models.IntegerField()
    is_current = models.BooleanField(db_default=True)
    state = models.TextField()
    title = models.TextField(db_default="")
    # Null only while the key is ``available``: nobody has chosen how it renders.
    widget_type = models.TextField(null=True)
    # metrics, dimension, series, thresholds, layout, options (kpigo.executive.widgets.Spec).
    definition = models.JSONField(db_default=models.Value({}, output_field=models.JSONField()))
    change = models.TextField()
    # Set when the change came through maker-checker (a threshold override).
    approval_request_id = models.UUIDField(null=True)
    # When ingestion first saw the key, for keys a feed registered.
    first_detected_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "widget_definition"
        constraints = [
            models.UniqueConstraint(
                fields=["org_id", "widget_key", "version"], name="widget_definition_version_unique"
            ),
            models.UniqueConstraint(
                fields=["org_id", "widget_key"],
                condition=models.Q(is_current=True),
                name="widget_definition_one_current",
            ),
            one_of("state", WIDGET_STATES, "widget_definition_state_valid"),
            one_of("change", WIDGET_CHANGES, "widget_definition_change_valid"),
            models.CheckConstraint(
                condition=models.Q(widget_type__isnull=True)
                | models.Q(widget_type__in=list(WIDGET_TYPES)),
                name="widget_definition_type_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(state="available") | models.Q(widget_type__isnull=False),
                name="widget_definition_typed_unless_available",
            ),
            models.CheckConstraint(
                condition=models.Q(widget_key__regex=WIDGET_KEY_PATTERN),
                name="widget_definition_key_format",
            ),
            models.CheckConstraint(
                condition=models.Q(version__gte=1), name="widget_definition_version_positive"
            ),
        ]
        indexes = [models.Index(fields=["org_id", "state"], name="widget_definition_state")]

    def __str__(self) -> str:
        return f"{self.widget_key} v{self.version} ({self.state})"


# What an executive manual input changed, for provenance and the audit trail.
INPUT_CHANGES = ("entered", "restated")


class ExecutiveManualInput(Tracked):
    """A hand-entered actual for an independent executive metric (PRD MI-4, TDD §5.5).

    Independent-mode executive metrics (cost-to-income, NPS, capital ratios) are
    fed via ``tmpl_actual_dimensional`` or entered by hand here. A submission
    conforms to ``fact_actual_dimensional`` for its slice — an organisation-level
    figure (``dimension_type`` and ``member_code`` both '') or one dimension
    member — exactly as a feed lands, and carries the contributor, moment and
    note in place of a feed run (MI-11). It is versioned: a correction after the
    input deadline is a ``restated`` row with the next version, and the prior one
    stays (MI-5).
    """

    input_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    metric = models.ForeignKey(Metric, on_delete=models.PROTECT, related_name="+")
    # Kept beside the FK so provenance survives the metric versioning (MR-7).
    metric_code = models.TextField()
    dimension_type = models.TextField(db_default="")
    member_code = models.TextField(db_default="")
    period_key = models.CharField(max_length=6)
    value = models.DecimalField(max_digits=18, decimal_places=4)
    currency_code = models.CharField(max_length=3, null=True)
    note = models.TextField(db_default="")
    change = models.TextField(db_default="entered")
    submitted_by = models.BigIntegerField(null=True)
    submitted_at = models.DateTimeField()
    approval_request_id = models.UUIDField(null=True)
    version = models.IntegerField(db_default=1)
    is_current = models.BooleanField(db_default=True)

    class Meta:
        db_table = "executive_manual_input"
        constraints = [
            one_of("change", INPUT_CHANGES, "executive_manual_input_change_valid"),
            models.CheckConstraint(
                condition=models.Q(period_key__regex=PERIOD_KEY_PATTERN),
                name="executive_manual_input_period_key_valid",
            ),
            # Org-level (both '') or a member of a named dimension (both set), never one.
            models.CheckConstraint(
                condition=models.Q(dimension_type="", member_code="")
                | (~models.Q(dimension_type="") & ~models.Q(member_code="")),
                name="executive_manual_input_whole_dimension",
            ),
            models.CheckConstraint(
                condition=models.Q(version__gte=1),
                name="executive_manual_input_version_positive",
            ),
            models.UniqueConstraint(
                fields=[
                    "org_id",
                    "metric_code",
                    "dimension_type",
                    "member_code",
                    "period_key",
                    "version",
                ],
                name="executive_manual_input_version_unique",
            ),
            models.UniqueConstraint(
                fields=["org_id", "metric_code", "dimension_type", "member_code", "period_key"],
                condition=models.Q(is_current=True),
                name="executive_manual_input_one_current",
            ),
        ]
        indexes = [
            models.Index(
                fields=["org_id", "metric_code", "period_key"], name="executive_input_metric_period"
            )
        ]

    def __str__(self) -> str:
        slot = f"{self.dimension_type}:{self.member_code}" if self.dimension_type else "org"
        return f"{self.metric_code} {slot} {self.period_key} v{self.version}"


class ExecutiveSavedView(Tracked):
    """A reader's named snapshot of the dashboard's filter state (PRD EX-9, Scope §10.5).

    The one dashboard is shared, but how a reader is looking at it — which period
    and how far each breakdown widget is drilled — is personal. A saved view holds
    that filter state under a name so the reader returns to the same vantage: what
    one reader saves binds nobody else, the way view preferences do. ``state`` is
    the opaque filter payload the dashboard writes and reads back (its period and a
    per-widget drill path); it names nothing the registry owns, so it survives a
    widget being renamed or removed — the dashboard ignores a key it no longer has.
    """

    view_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    app_user = models.ForeignKey(AppUser, on_delete=models.CASCADE, related_name="executive_views")
    name = models.TextField()
    state = models.JSONField()

    class Meta:
        db_table = "executive_saved_view"
        constraints = [
            models.UniqueConstraint(
                fields=["app_user", "name"], name="executive_saved_view_name_unique"
            )
        ]
        indexes = [models.Index(fields=["org_id", "app_user"], name="executive_view_owner")]

    def __str__(self) -> str:
        return f"{self.app_user_id} view {self.name!r}"
