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

from kpigo.platform.db import Stamped, one_of

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
