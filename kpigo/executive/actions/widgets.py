"""Placing and configuring Executive widgets (PRD EX-5–EX-10, App Flow §6).

Only an Admin changes a widget, and a change applies to everyone (Scope §10.4):
viewers get filters, not layout control. Every change writes a new version of the
definition; the previous one stays for the history panel. A threshold override is
the one change that can go through maker-checker (``widget_change``, off by
default, PRD AD-3).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from django.db import transaction
from pydantic import BaseModel, Field, StringConstraints

from kpigo.action import ActionContext, Conflict, InvalidInput, NotFound, action
from kpigo.executive import widgets as w
from kpigo.executive.models import WIDGET_KEY_PATTERN, WidgetDefinition
from kpigo.ingestion.reference import org_today

WidgetKey = Annotated[str, StringConstraints(pattern=WIDGET_KEY_PATTERN)]
State = Literal["available", "placed", "removed"]
DEFAULT_SERIES: tuple[w.Series, ...] = ("actual", "target")


# ── outputs ──────────────────────────────────────────────────────────────────


class WidgetMetricOut(BaseModel):
    metric_code: str
    source: w.Source


class ThresholdBandOut(BaseModel):
    label: str
    threshold: Decimal


class WidgetThresholdsOut(BaseModel):
    source: Literal["metric", "override"]
    basis: Literal["achievement", "value"] | None
    bands: list[ThresholdBandOut]
    note: str


class WidgetLayoutOut(BaseModel):
    x: int
    y: int
    w: int
    h: int


class WidgetOptionsOut(BaseModel):
    sparkline: bool
    top_n: int


class WidgetOut(BaseModel):
    widget_key: str
    version: int
    state: State
    title: str
    widget_type: w.WidgetType | None
    metrics: list[WidgetMetricOut]
    dimension: str | None
    series: list[w.Series]
    thresholds: WidgetThresholdsOut
    layout: WidgetLayoutOut | None
    options: WidgetOptionsOut
    change: str
    changed_at: datetime
    changed_by: int | None
    approval_request_id: str | None
    first_detected_at: datetime | None

    @classmethod
    def of(cls, row: WidgetDefinition) -> WidgetOut:
        d: dict[str, Any] = row.definition or {}
        thresholds = d.get("thresholds") or {"source": "metric"}
        return cls(
            widget_key=row.widget_key,
            version=row.version,
            state=row.state,
            title=row.title,
            widget_type=row.widget_type,
            metrics=[WidgetMetricOut(**m) for m in d.get("metrics", [])],
            dimension=d.get("dimension"),
            series=d.get("series", ["actual"]),
            thresholds=WidgetThresholdsOut(
                source=thresholds.get("source", "metric"),
                basis=thresholds.get("basis"),
                bands=[ThresholdBandOut(**b) for b in thresholds.get("bands", [])],
                note=thresholds.get("note", ""),
            ),
            layout=WidgetLayoutOut(**d["layout"]) if d.get("layout") else None,
            options=WidgetOptionsOut(**(w.WidgetOptionsIn(**d.get("options", {})).model_dump())),
            change=row.change,
            changed_at=row.created_at,
            changed_by=row.created_by,
            approval_request_id=str(row.approval_request_id) if row.approval_request_id else None,
            first_detected_at=row.first_detected_at,
        )


class WidgetTypeOut(BaseModel):
    """What one type in the bundle can draw, so the UI greys out what it cannot."""

    type: w.WidgetType
    label: str
    renders: str
    min_metrics: int
    max_metrics: int
    dimension_single: w.DimensionRule
    dimension_multi: w.DimensionRule
    comparisons: bool
    needs_comparison: bool
    additive_only: bool
    one_unit: bool
    thresholds: bool
    default_w: int
    default_h: int


def _types() -> list[WidgetTypeOut]:
    return [WidgetTypeOut(**vars(t)) for t in w.TYPES.values()]


# ── helpers ──────────────────────────────────────────────────────────────────


def _current(ctx: ActionContext, key: str, *, lock: bool = False) -> WidgetDefinition:
    query = WidgetDefinition.objects.filter(org_id=ctx.org_id, widget_key=key, is_current=True)
    if lock:
        query = query.select_for_update()
    found = query.first()
    if found is None:
        raise NotFound(f"No widget '{key}'.")
    return found


def _check_version(row: WidgetDefinition, expected: int | None) -> None:
    if expected is not None and expected != row.version:
        raise Conflict(
            f"Widget '{row.widget_key}' changed since you opened it (now version {row.version}). "
            "Reload and try again.",
            detail={"version": row.version},
        )


def _validated(
    ctx: ActionContext,
    widget_type: str,
    metrics: list[w.WidgetMetricIn],
    dimension: str | None,
    series: list[str],
    thresholds: w.WidgetThresholdsIn,
) -> list[w.ResolvedMetric]:
    resolved, problems = w.resolve(ctx.org_id, metrics, dimension, org_today(ctx.org_id))
    if not problems:
        problems = w.check(widget_type, resolved, dimension, series, thresholds)
    if problems:
        raise InvalidInput(" ".join(problems), detail={"problems": problems})
    return resolved


def _next_free_row(ctx: ActionContext) -> int:
    bottom = 0
    for d in WidgetDefinition.objects.filter(
        org_id=ctx.org_id, is_current=True, state="placed"
    ).values_list("definition", flat=True):
        layout = (d or {}).get("layout")
        if layout:
            bottom = max(bottom, int(layout["y"]) + int(layout["h"]))
    return bottom


def _write(
    ctx: ActionContext,
    previous: WidgetDefinition | None,
    key: str,
    *,
    state: str,
    title: str,
    widget_type: str | None,
    definition: dict[str, Any],
    change: str,
) -> WidgetDefinition:
    """Retire the current version and write the next. Nothing is updated in place."""
    version = 1
    first_detected_at = None
    if previous is not None:
        previous.is_current = False
        previous.save(update_fields=["is_current"])
        version = previous.version + 1
        first_detected_at = previous.first_detected_at
    return WidgetDefinition.objects.create(
        org_id=ctx.org_id,
        widget_key=key,
        version=version,
        is_current=True,
        state=state,
        title=title,
        widget_type=widget_type,
        definition=definition,
        change=change,
        approval_request_id=ctx.approval.approval_request_id if ctx.approval else None,
        first_detected_at=first_detected_at,
        created_by=ctx.user_id,
    )


def _definition(
    resolved: list[w.ResolvedMetric],
    dimension: str | None,
    series: list[str],
    thresholds: w.WidgetThresholdsIn,
    layout: w.WidgetLayoutIn,
    options: w.WidgetOptionsIn,
) -> dict[str, Any]:
    return {
        "metrics": [{"metric_code": m.metric_code, "source": m.source} for m in resolved],
        "dimension": dimension,
        "series": series,
        "thresholds": thresholds.model_dump(mode="json"),
        "layout": layout.model_dump(),
        "options": options.model_dump(),
    }


# ── reads ────────────────────────────────────────────────────────────────────


class WidgetListIn(BaseModel):
    include_removed: bool = False


class WidgetListOut(BaseModel):
    widgets: list[WidgetOut]
    types: list[WidgetTypeOut]


@action(
    name="widget.list",
    summary="Every widget key: placed, available from a feed, and optionally removed.",
    schema=WidgetListIn,
    output=WidgetListOut,
    permission="widget.manage",
    read_only=True,
    module="executive",
    example={},
)
def list_widgets(params: WidgetListIn, ctx: ActionContext) -> WidgetListOut:
    rows = WidgetDefinition.objects.filter(org_id=ctx.org_id, is_current=True)
    if not params.include_removed:
        rows = rows.exclude(state="removed")
    order = {"placed": 0, "available": 1, "removed": 2}
    widgets = sorted(rows, key=lambda r: (order[r.state], r.widget_key))
    return WidgetListOut(widgets=[WidgetOut.of(r) for r in widgets], types=_types())


class WidgetDashboardIn(BaseModel):
    pass


class WidgetDashboardOut(BaseModel):
    widgets: list[WidgetOut]
    types: list[WidgetTypeOut]


@action(
    name="widget.dashboard",
    summary="The Executive dashboard's placed widgets, in grid order.",
    schema=WidgetDashboardIn,
    output=WidgetDashboardOut,
    permission="executive.view",
    read_only=True,
    module="executive",
    example={},
)
def dashboard(params: WidgetDashboardIn, ctx: ActionContext) -> WidgetDashboardOut:
    rows = list(WidgetDefinition.objects.filter(org_id=ctx.org_id, is_current=True, state="placed"))

    def position(r: WidgetDefinition) -> tuple[int, int, str]:
        layout = (r.definition or {}).get("layout") or {}
        return (int(layout.get("y", 0)), int(layout.get("x", 0)), r.widget_key)

    return WidgetDashboardOut(
        widgets=[WidgetOut.of(r) for r in sorted(rows, key=position)], types=_types()
    )


class WidgetHistoryIn(BaseModel):
    widget_key: WidgetKey


class WidgetHistoryOut(BaseModel):
    widget_key: str
    versions: list[WidgetOut]


@action(
    name="widget.history",
    summary="Every version of a widget's definition, newest first.",
    schema=WidgetHistoryIn,
    output=WidgetHistoryOut,
    permission="widget.manage",
    read_only=True,
    module="executive",
    example={"widget_key": "revenue"},
)
def history(params: WidgetHistoryIn, ctx: ActionContext) -> WidgetHistoryOut:
    rows = WidgetDefinition.objects.filter(
        org_id=ctx.org_id, widget_key=params.widget_key
    ).order_by("-version")
    if not rows.exists():
        raise NotFound(f"No widget '{params.widget_key}'.")
    return WidgetHistoryOut(widget_key=params.widget_key, versions=[WidgetOut.of(r) for r in rows])


# ── changes ──────────────────────────────────────────────────────────────────


class PlaceWidgetIn(BaseModel):
    widget_key: WidgetKey
    title: w.Title = ""
    widget_type: w.WidgetType
    metrics: list[w.WidgetMetricIn] = Field(min_length=1, max_length=8)
    dimension: w.DimensionType | None = None
    series: list[w.Series] = Field(default_factory=lambda: list(DEFAULT_SERIES))
    # Omitted: the full width a type takes by default, under the last widget.
    layout: w.WidgetLayoutIn | None = None
    options: w.WidgetOptionsIn = Field(default_factory=w.WidgetOptionsIn)


@action(
    name="widget.place",
    summary="Place a widget on the Executive dashboard: metrics, type, breakdown and series.",
    schema=PlaceWidgetIn,
    output=WidgetOut,
    permission="widget.manage",
    read_only=False,
    module="executive",
    audit="widget.placed",
    config_change=True,
    example={
        "widget_key": "revenue",
        "title": "Revenue",
        "widget_type": "kpi_card",
        "metrics": [{"metric_code": "ex_revenue"}],
    },
)
def place(params: PlaceWidgetIn, ctx: ActionContext) -> WidgetOut:
    series = w.canonical_series(list(params.series))
    spec = w.TYPES[params.widget_type]
    if not spec.comparisons:
        series = ["actual"]
    thresholds = w.WidgetThresholdsIn()
    with transaction.atomic():
        previous = (
            WidgetDefinition.objects.select_for_update()
            .filter(org_id=ctx.org_id, widget_key=params.widget_key, is_current=True)
            .first()
        )
        if previous is not None and previous.state == "placed":
            raise Conflict(
                f"Widget '{params.widget_key}' is already on the dashboard; change it instead."
            )
        resolved = _validated(
            ctx, params.widget_type, params.metrics, params.dimension, series, thresholds
        )
        layout = params.layout or w.WidgetLayoutIn(
            x=0, y=_next_free_row(ctx), w=spec.default_w, h=spec.default_h
        )
        title = params.title or (resolved[0].display_name if len(resolved) == 1 else "")
        if not title:
            raise InvalidInput("Give a widget showing several metrics a title.")
        row = _write(
            ctx,
            previous,
            params.widget_key,
            state="placed",
            title=title,
            widget_type=params.widget_type,
            definition=_definition(
                resolved, params.dimension, series, thresholds, layout, params.options
            ),
            change="placed",
        )
        ctx.audit(
            "widget.placed.detail",
            widget_key=row.widget_key,
            version=row.version,
            widget_type=row.widget_type,
            metrics=[m.metric_code for m in resolved],
        )
    return WidgetOut.of(row)


class UpdateWidgetIn(BaseModel):
    widget_key: WidgetKey
    # The version the Admin was looking at; a stale one is refused, not overwritten.
    expected_version: int | None = Field(default=None, ge=1)
    title: w.Title | None = None
    widget_type: w.WidgetType | None = None
    metrics: list[w.WidgetMetricIn] | None = Field(default=None, min_length=1, max_length=8)
    dimension: w.DimensionType | None = None
    # Set to clear the breakdown (``dimension`` null alone means "leave it").
    clear_dimension: bool = False
    series: list[w.Series] | None = None
    layout: w.WidgetLayoutIn | None = None
    options: w.WidgetOptionsIn | None = None


@action(
    name="widget.update",
    summary="Change a placed widget: its type, metrics, breakdown, series, title or position.",
    schema=UpdateWidgetIn,
    output=WidgetOut,
    permission="widget.manage",
    read_only=False,
    module="executive",
    audit="widget.updated",
    config_change=True,
    example={"widget_key": "revenue", "widget_type": "line"},
)
def update(params: UpdateWidgetIn, ctx: ActionContext) -> WidgetOut:
    with transaction.atomic():
        row = _current(ctx, params.widget_key, lock=True)
        _check_version(row, params.expected_version)
        if row.state != "placed":
            raise Conflict(f"Widget '{row.widget_key}' is not on the dashboard; place it first.")
        d: dict[str, Any] = row.definition
        widget_type = params.widget_type or str(row.widget_type)
        metrics = (
            params.metrics
            if params.metrics is not None
            else [w.WidgetMetricIn(**m) for m in d["metrics"]]
        )
        dimension = None if params.clear_dimension else (params.dimension or d.get("dimension"))
        series = w.canonical_series(
            list(params.series if params.series is not None else d["series"])
        )
        if not w.TYPES[widget_type].comparisons:
            series = ["actual"]
        thresholds = w.WidgetThresholdsIn(**d.get("thresholds", {}))
        resolved = _validated(ctx, widget_type, metrics, dimension, series, thresholds)
        layout = params.layout or w.WidgetLayoutIn(**d["layout"])
        options = params.options or w.WidgetOptionsIn(**d.get("options", {}))
        definition = _definition(resolved, dimension, series, thresholds, layout, options)
        title = row.title if params.title is None else params.title
        if not title:
            raise InvalidInput("A widget needs a title.")
        if definition == d and title == row.title and widget_type == row.widget_type:
            return WidgetOut.of(row)
        change = "type_changed" if widget_type != row.widget_type else "updated"
        new = _write(
            ctx,
            row,
            row.widget_key,
            state="placed",
            title=title,
            widget_type=widget_type,
            definition=definition,
            change=change,
        )
        ctx.audit(
            "widget.updated.detail",
            widget_key=new.widget_key,
            version=new.version,
            change=change,
            widget_type=new.widget_type,
            previous_type=row.widget_type,
        )
    return WidgetOut.of(new)


class SetThresholdsIn(BaseModel):
    widget_key: WidgetKey
    expected_version: int | None = Field(default=None, ge=1)
    thresholds: w.WidgetThresholdsIn


@action(
    name="widget.thresholds.set",
    summary="Override a widget's threshold bands, or return them to the metric's.",
    schema=SetThresholdsIn,
    output=WidgetOut,
    permission="widget.manage",
    read_only=False,
    module="executive",
    requires_approval="widget_change",
    audit="widget.thresholds_changed",
    config_change=True,
    example={
        "widget_key": "cost_to_income",
        "thresholds": {
            "source": "override",
            "basis": "value",
            "bands": [
                {"label": "Within tolerance", "threshold": "0"},
                {"label": "Above tolerance", "threshold": "0.035"},
            ],
            "note": "Board tolerance is 3.5%.",
        },
    },
)
def set_thresholds(params: SetThresholdsIn, ctx: ActionContext) -> WidgetOut:
    with transaction.atomic():
        row = _current(ctx, params.widget_key, lock=True)
        _check_version(row, params.expected_version)
        if row.state != "placed":
            raise Conflict(f"Widget '{row.widget_key}' is not on the dashboard; place it first.")
        spec = w.TYPES[str(row.widget_type)]
        if params.thresholds.source == "override" and not spec.thresholds:
            raise InvalidInput(f"A {spec.label} draws no threshold bands, so it takes no override.")
        d = dict(row.definition)
        before = d.get("thresholds", {"source": "metric"})
        after = params.thresholds.model_dump(mode="json")
        if before == after:
            return WidgetOut.of(row)
        d["thresholds"] = after
        new = _write(
            ctx,
            row,
            row.widget_key,
            state="placed",
            title=row.title,
            widget_type=row.widget_type,
            definition=d,
            change="thresholds_changed",
        )
        ctx.audit(
            "widget.thresholds_changed.detail",
            widget_key=new.widget_key,
            version=new.version,
            before=before,
            after=after,
        )
    return WidgetOut.of(new)


class RemoveWidgetIn(BaseModel):
    widget_key: WidgetKey
    expected_version: int | None = Field(default=None, ge=1)


@action(
    name="widget.remove",
    summary="Take a widget off the dashboard for everyone; its history is kept.",
    schema=RemoveWidgetIn,
    output=WidgetOut,
    permission="widget.manage",
    read_only=False,
    module="executive",
    audit="widget.removed",
    config_change=True,
    example={"widget_key": "revenue"},
)
def remove(params: RemoveWidgetIn, ctx: ActionContext) -> WidgetOut:
    with transaction.atomic():
        row = _current(ctx, params.widget_key, lock=True)
        _check_version(row, params.expected_version)
        if row.state != "placed":
            raise Conflict(f"Widget '{row.widget_key}' is not on the dashboard.")
        new = _write(
            ctx,
            row,
            row.widget_key,
            state="removed",
            title=row.title,
            widget_type=row.widget_type,
            definition=row.definition,
            change="removed",
        )
    return WidgetOut.of(new)
