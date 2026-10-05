"""The widget bundle and the rules a definition must satisfy (Scope §10.4, PRD EX-5–EX-8).

kpiGo ships a fixed bundle of widget types; an Admin chooses which renders which
metric. Each type declares what it can draw: how many metrics, whether it takes a
dimension breakdown, whether it compares against other series, whether its
metrics must add up (a pie of ratios is meaningless) or share a unit, and whether
it carries threshold bands. A definition that breaks a rule is refused when it is
placed, never discovered at draw time, and the same table goes to the UI so it can
grey out a combination before anyone tries it.

``check`` is the single source of those rules; ``resolve`` checks a definition's
metrics against the registry (active, bound to ``executive``, and fit for the
source each is drawn from).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from django.db.models import Q
from pydantic import BaseModel, Field, StringConstraints, model_validator

from kpigo.campaigns.models import CampaignPublishedMetric
from kpigo.hierarchy.models import Dimension
from kpigo.ingestion.validator import CUSTOMER_DIMENSIONS
from kpigo.metrics.actions.metric import MetricCode
from kpigo.metrics.models import Metric

WidgetType = Literal[
    "kpi_card", "bullet", "gauge", "bar", "line", "pie", "ranked_list", "table", "funnel"
]
# Where a metric's figures come from (Scope §10.1): fed at executive level, rolled up
# from Scorecards / Agent Performance subjects, or a campaign result published as a metric.
Source = Literal["independent", "rollup", "campaign"]
SOURCES: tuple[str, ...] = ("independent", "rollup", "campaign")
# Actual is always drawn; the others are comparisons (EX-4). Forecast is opt-in by
# choosing it: kpiGo never generates one, it shows the client's forecast feed.
Series = Literal["actual", "target", "forecast", "budget", "prior", "prior_year"]
SERIES: tuple[str, ...] = ("actual", "target", "forecast", "budget", "prior", "prior_year")
DimensionRule = Literal["never", "optional", "required"]

# Roll-up aggregates subjects up the dimensions their assignment carries.
ROLLUP_DIMENSIONS = ("branch", "region", "segment")
# Campaign outcomes describe the customer by these.
CAMPAIGN_DIMENSIONS: tuple[str, ...] = tuple(CUSTOMER_DIMENSIONS)
ROLLUP_PRODUCTS = frozenset({"scorecards", "agent_sales", "agent_service"})
ADDITIVE = frozenset({"sum", "count"})
GRID_COLUMNS = 12

Title = Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)]
DimensionType = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)]


@dataclass(frozen=True)
class TypeSpec:
    type: str
    label: str
    renders: str
    min_metrics: int
    max_metrics: int
    # Whether a dimension breakdown is taken with one metric, and with several.
    dimension_single: DimensionRule
    dimension_multi: DimensionRule
    # Draws comparison series (target, forecast...) beside the actual.
    comparisons: bool
    # Needs at least one comparison to draw at all (a bullet's marker).
    needs_comparison: bool
    # Every metric must be additive (sum or count) and share one unit.
    additive_only: bool
    one_unit: bool
    # Carries threshold bands, so a per-widget override means something.
    thresholds: bool
    default_w: int
    default_h: int


TYPES: dict[str, TypeSpec] = {
    t.type: t
    for t in (
        TypeSpec(
            "kpi_card",
            "KPI card",
            "Figure, delta chip, comparison caption, optional sparkline",
            1,
            1,
            "never",
            "never",
            True,
            False,
            False,
            False,
            True,
            3,
            2,
        ),
        TypeSpec(
            "bullet",
            "Bullet",
            "Actual bar against a target marker and threshold bands",
            1,
            6,
            "optional",
            "never",
            True,
            True,
            False,
            False,
            True,
            6,
            3,
        ),
        TypeSpec(
            "gauge",
            "Gauge",
            "Single value on an arc with threshold bands",
            1,
            1,
            "never",
            "never",
            True,
            False,
            False,
            False,
            True,
            3,
            3,
        ),
        TypeSpec(
            "bar",
            "Bar",
            "A metric across dimension members, or several metrics side by side",
            1,
            6,
            "required",
            "optional",
            True,
            False,
            False,
            True,
            False,
            6,
            4,
        ),
        TypeSpec(
            "line",
            "Line",
            "One or more series over time",
            1,
            4,
            "never",
            "never",
            True,
            False,
            False,
            True,
            False,
            6,
            4,
        ),
        TypeSpec(
            "pie",
            "Pie / donut",
            "Composition of a whole: one metric across members, or several metrics",
            1,
            8,
            "required",
            "never",
            False,
            False,
            True,
            True,
            False,
            4,
            4,
        ),
        TypeSpec(
            "ranked_list",
            "Ranked list",
            "Ordered members with values",
            1,
            1,
            "required",
            "never",
            True,
            False,
            False,
            False,
            False,
            4,
            4,
        ),
        TypeSpec(
            "table",
            "Table",
            "Members by metrics grid with RAG",
            1,
            8,
            "required",
            "required",
            True,
            False,
            False,
            False,
            True,
            12,
            4,
        ),
        TypeSpec(
            "funnel",
            "Funnel",
            "Ordered stages with drop-off",
            2,
            8,
            "never",
            "never",
            False,
            False,
            True,
            True,
            False,
            4,
            4,
        ),
    )
}


# ── the declarative definition (EX-10) ───────────────────────────────────────


class WidgetMetricIn(BaseModel):
    metric_code: MetricCode
    # Omitted: chosen from the metric's bindings (campaign result, else roll-up, else fed).
    source: Source | None = None


class ThresholdBandIn(BaseModel):
    label: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
    # The band starts here: % of target achieved (1.0 = on target) or the metric's own value.
    threshold: Decimal = Field(ge=Decimal("-1e14"), le=Decimal("1e14"))


class WidgetThresholdsIn(BaseModel):
    """``metric``: the metric's target and the client's rating bands. ``override``: these bands."""

    source: Literal["metric", "override"] = "metric"
    basis: Literal["achievement", "value"] | None = None
    bands: list[ThresholdBandIn] = Field(default_factory=list, max_length=6)
    # Why this widget reads differently from the metric, shown on the widget.
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] = ""

    @model_validator(mode="after")
    def _shape(self) -> WidgetThresholdsIn:
        if self.source == "metric":
            if self.bands or self.basis is not None or self.note:
                raise ValueError("Thresholds from the metric take no bands, basis or note.")
            return self
        if self.basis is None:
            raise ValueError("An override says whether bands are on % achieved or on the value.")
        if len(self.bands) < 2:
            raise ValueError("An override needs at least two bands.")
        values = [b.threshold for b in self.bands]
        if values != sorted(values) or len(set(values)) != len(values):
            raise ValueError("Bands go from the lowest threshold to the highest, no repeats.")
        if len({b.label for b in self.bands}) != len(self.bands):
            raise ValueError("Each band needs its own label.")
        return self


class WidgetLayoutIn(BaseModel):
    """Position on the dashboard's twelve-column grid."""

    x: int = Field(ge=0, le=GRID_COLUMNS - 1)
    y: int = Field(ge=0, le=500)
    w: int = Field(ge=1, le=GRID_COLUMNS)
    h: int = Field(ge=1, le=12)

    @model_validator(mode="after")
    def _fits(self) -> WidgetLayoutIn:
        if self.x + self.w > GRID_COLUMNS:
            raise ValueError(f"The widget runs off the {GRID_COLUMNS}-column grid.")
        return self


class WidgetOptionsIn(BaseModel):
    # KPI card: draw the last twelve months under the figure.
    sparkline: bool = False
    # Ranked list, bar and table: how many members to show before "and N more".
    top_n: int = Field(default=10, ge=3, le=50)


@dataclass(frozen=True)
class ResolvedMetric:
    metric_code: str
    source: str
    display_name: str
    unit: str
    aggregation: str
    direction: str


def canonical_series(series: list[str]) -> list[str]:
    """Actual first, then the comparisons in their fixed order, each once."""
    chosen = set(series) | {"actual"}
    return [s for s in SERIES if s in chosen]


def check(
    widget_type: str,
    metrics: list[ResolvedMetric],
    dimension: str | None,
    series: list[str],
    thresholds: WidgetThresholdsIn,
) -> list[str]:
    """Why this combination cannot render; empty when it can (EX-7)."""
    spec = TYPES[widget_type]
    problems: list[str] = []
    n = len(metrics)
    if n < spec.min_metrics or n > spec.max_metrics:
        span = (
            f"exactly {spec.min_metrics}"
            if spec.min_metrics == spec.max_metrics
            else f"{spec.min_metrics} to {spec.max_metrics}"
        )
        problems.append(f"A {spec.label} shows {span} metric{'s' if spec.max_metrics > 1 else ''}.")
    rule = spec.dimension_single if n == 1 else spec.dimension_multi
    if rule == "never" and dimension is not None:
        problems.append(
            f"A {spec.label} with {'one metric' if n == 1 else 'several metrics'} takes no "
            "dimension breakdown."
        )
    if rule == "required" and dimension is None:
        problems.append(
            f"A {spec.label} with {'one metric' if n == 1 else 'several metrics'} needs a "
            "dimension to break down by."
        )
    comparisons = [s for s in series if s != "actual"]
    if comparisons and not spec.comparisons:
        problems.append(f"A {spec.label} draws the actual only; it takes no comparison series.")
    if spec.needs_comparison and not comparisons:
        problems.append(f"A {spec.label} needs a comparison series (target, usually) to mark.")
    if spec.additive_only:
        odd = [m.metric_code for m in metrics if m.aggregation not in ADDITIVE]
        if odd:
            problems.append(
                f"A {spec.label} adds its parts up, so every metric must be a sum or a count; "
                f"{', '.join(odd)} is not."
            )
    if spec.one_unit and len({m.unit for m in metrics}) > 1:
        problems.append(f"A {spec.label} draws one scale, so its metrics must share a unit.")
    if thresholds.source == "override" and not spec.thresholds:
        problems.append(f"A {spec.label} draws no threshold bands, so it takes no override.")
    return problems


def _in_force(org_id: str, code: str, today: date) -> Metric | None:
    return (
        Metric.objects.filter(org_id=org_id, metric_code=code, effective_from__lte=today)
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=today))
        .prefetch_related("bindings")
        .first()
    )


def resolve(
    org_id: str, metrics: list[WidgetMetricIn], dimension: str | None, today: date
) -> tuple[list[ResolvedMetric], list[str]]:
    """Each metric with its source settled, and why any of them cannot be placed."""
    problems: list[str] = []
    resolved: list[ResolvedMetric] = []
    codes = [m.metric_code for m in metrics]
    if len(set(codes)) != len(codes):
        problems.append("A metric appears more than once on the widget.")
    published = set(
        CampaignPublishedMetric.objects.filter(org_id=org_id, status="active").values_list(
            "metric__metric_code", flat=True
        )
    )
    if (
        dimension is not None
        and not Dimension.objects.filter(org_id=org_id, dimension_type=dimension).exists()
    ):
        problems.append(f"No dimension '{dimension}' is defined.")
    for m in metrics:
        metric = _in_force(org_id, m.metric_code, today)
        if metric is None:
            problems.append(f"No metric '{m.metric_code}' is in force today.")
            continue
        if metric.status != "active":
            problems.append(f"Metric '{m.metric_code}' is {metric.status}, not active.")
            continue
        products = {b.product for b in metric.bindings.all() if b.is_active}
        if "executive" not in products:
            problems.append(
                f"Metric '{m.metric_code}' is not bound to Executive; bind it in the registry "
                "first."
            )
            continue
        source = m.source or (
            "campaign"
            if m.metric_code in published
            else "rollup"
            if products & ROLLUP_PRODUCTS
            else "independent"
        )
        if source == "rollup":
            if not products & ROLLUP_PRODUCTS:
                problems.append(
                    f"Metric '{m.metric_code}' is not bound to Scorecards or Agent Performance, "
                    "so there is nothing to roll up."
                )
            if dimension is not None and dimension not in ROLLUP_DIMENSIONS:
                problems.append(
                    f"Metric '{m.metric_code}' rolls up through people's assignments, which "
                    f"carry {', '.join(ROLLUP_DIMENSIONS)}, not '{dimension}'."
                )
        if source == "campaign":
            if m.metric_code not in published:
                problems.append(
                    f"Metric '{m.metric_code}' is not a campaign result published as a metric."
                )
            if dimension is not None and dimension not in CAMPAIGN_DIMENSIONS:
                problems.append(
                    f"Campaign outcomes describe customers by {', '.join(CAMPAIGN_DIMENSIONS)}, "
                    f"not '{dimension}'."
                )
        resolved.append(
            ResolvedMetric(
                metric_code=metric.metric_code,
                source=source,
                display_name=metric.display_name,
                unit=metric.unit,
                aggregation=metric.aggregation,
                direction=metric.direction,
            )
        )
    return resolved, problems
