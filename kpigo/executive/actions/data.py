"""Reading an Executive widget's figures (PRD EX-1–EX-4, Scope §10).

``widget.data`` returns, for one placed widget and period, each metric's series:
a single organisation figure, or one per dimension member when the widget breaks
down. Data scope is applied member by member — no grant means no data, and the
empty state names the grant the viewer is missing (§14.1). A breakdown widget
drills down the dimension's hierarchy by naming a parent; a breadcrumb comes back
with it. Thresholds come resolved, from the metric's target and the client's
bands or from a per-widget override, and every figure carries its provenance.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, StringConstraints

from kpigo.action import ActionContext, NotFound, action
from kpigo.executive import compute as c
from kpigo.executive import widgets as w
from kpigo.executive.models import WidgetDefinition
from kpigo.hierarchy.models import DimMember
from kpigo.metrics.models import Metric
from kpigo.platform.vocab import PeriodKey
from kpigo.scorecards.bands import bands_for

ALL = "*"
MemberCode = Annotated[str, StringConstraints(min_length=1, max_length=64)]


# ── outputs ──────────────────────────────────────────────────────────────────


class SeriesOut(BaseModel):
    series_type: w.Series
    value: Decimal | None
    currency: str | None = None
    # Roll-up only: distinct subjects summed, and those dropped for a missing FX rate.
    subjects: int | None = None
    skipped_no_fx: int = 0


class MemberDataOut(BaseModel):
    member_code: str
    member_name: str
    # Whether the member has children to drill into.
    has_children: bool
    series: list[SeriesOut]


class MetricDataOut(BaseModel):
    metric_code: str
    display_name: str
    source: w.Source
    unit: str
    aggregation: str
    direction: str
    # One of the two is populated: ``org`` when the widget has no breakdown, else ``members``.
    org: list[SeriesOut] | None
    members: list[MemberDataOut]
    # A campaign result's value flow is wired in a later step; until then it says so.
    pending: str | None
    run_id: str | None


class DataBandOut(BaseModel):
    label: str
    threshold: Decimal


class ThresholdsOut(BaseModel):
    source: Literal["metric", "override"]
    # ``achievement``: bands are on % of target (1.0 = on target). ``value``: on the figure.
    basis: Literal["achievement", "value"]
    bands: list[DataBandOut]
    note: str


class BreadcrumbOut(BaseModel):
    member_code: str
    member_name: str


class WidgetDataOut(BaseModel):
    widget_key: str
    version: int
    widget_type: w.WidgetType
    title: str
    period_key: str
    dimension: str | None
    # The path drilled into, root first; empty at the top level.
    breadcrumb: list[BreadcrumbOut]
    reporting_currency: str | None
    series: list[w.Series]
    metrics: list[MetricDataOut]
    thresholds: ThresholdsOut | None
    # Set when the viewer sees nothing: the reason names the grant they need.
    empty: str | None


# ── inputs ───────────────────────────────────────────────────────────────────


class WidgetDataIn(BaseModel):
    widget_key: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
    period_key: PeriodKey
    # Drill: show the children of this member of the widget's dimension. Omitted → roots.
    drill_to: MemberCode | None = None


# ── scope ────────────────────────────────────────────────────────────────────


def _granted(ctx: ActionContext, dimension: str | None) -> tuple[bool, set[str]]:
    """Whole-org access, and the explicitly granted members of ``dimension``.

    No grant means no data (§14.1): a deny is the starting point, never a wildcard.
    """
    grants = [g for g in ctx.data_scopes if g.module == "executive"]
    whole_org = any(g.member_code == ALL for g in grants)
    members: set[str] = set()
    if dimension is not None:
        named = {g.member_code for g in grants if g.dimension_type == dimension}
        if named:
            members = c._descendants(ctx.org_id, dimension, named)
    return whole_org, members


def _missing_grant(ctx: ActionContext, dimension: str | None) -> str:
    held = [
        f"{g.dimension_type}:{g.member_code}" for g in ctx.data_scopes if g.module == "executive"
    ]
    have = f" You have {', '.join(sorted(held))}." if held else ""
    if dimension is None:
        return (
            "This widget shows the whole organisation, which needs an Executive grant across "
            f"all of a dimension.{have}"
        )
    return f"You have no Executive data scope for {dimension}.{have}"


# ── member listing and drill ─────────────────────────────────────────────────


def _members_at(
    org_id: str, dimension: str, drill_to: str | None, visible: set[str] | None
) -> tuple[list[tuple[str, str, bool]], list[tuple[str, str]]]:
    """The members to show (code, name, has_children) and the breadcrumb to ``drill_to``.

    ``visible`` is the set a scoped viewer may see (``None`` for whole-org access).
    At the top level a scoped viewer sees the top-most members of their grant,
    even a leaf they were granted directly, so a region head granted one region
    sees it without having to be granted its parent.
    """
    rows = list(
        DimMember.objects.filter(org_id=org_id, dimension_type=dimension).values_list(
            "member_code", "member_name", "parent_code"
        )
    )
    name = {code: nm for code, nm, _ in rows}
    parent = {code: (p or None) for code, _, p in rows}

    def in_scope(code: str) -> bool:
        return visible is None or code in visible

    def top_most(code: str) -> bool:
        p = parent.get(code)
        return p is None or (visible is not None and p not in visible)

    has_children: dict[str, bool] = {}
    for code, _, p in rows:
        if p and in_scope(code):
            has_children[str(p)] = True

    if drill_to is None:
        shown = [
            (code, nm, has_children.get(code, False))
            for code, nm, _ in rows
            if in_scope(code) and top_most(code)
        ]
    else:
        shown = [
            (code, nm, has_children.get(code, False))
            for code, nm, _ in rows
            if parent.get(code) == drill_to and in_scope(code)
        ]
    shown.sort(key=lambda r: r[0])
    crumb: list[tuple[str, str]] = []
    node = drill_to
    while node is not None and node in name:
        crumb.append((node, name[node]))
        node = parent.get(node)
    crumb.reverse()
    return shown, crumb


# ── thresholds ───────────────────────────────────────────────────────────────


def _thresholds(ctx: ActionContext, widget: WidgetDefinition) -> ThresholdsOut:
    d: dict[str, Any] = widget.definition
    stored = d.get("thresholds") or {"source": "metric"}
    if stored.get("source") == "override":
        return ThresholdsOut(
            source="override",
            basis=stored["basis"],
            bands=[DataBandOut(**b) for b in stored.get("bands", [])],
            note=stored.get("note", ""),
        )
    # From the metric: the client's rating bands, read as % of target achieved.
    bands = bands_for(ctx.org_id, "scorecards")
    return ThresholdsOut(
        source="metric",
        basis="achievement",
        bands=[DataBandOut(label=b.label, threshold=b.threshold) for b in bands],
        note="",
    )


# ── the action ───────────────────────────────────────────────────────────────


def _series_out(f: c.SeriesFigure) -> SeriesOut:
    return SeriesOut(
        series_type=f.series_type,
        value=f.value,
        currency=f.currency,
        subjects=f.subjects,
        skipped_no_fx=f.skipped_no_fx,
    )


def _compute_metric(
    ctx: ActionContext,
    widget: WidgetDefinition,
    metric: Metric,
    source: str,
    period_key: str,
    series: list[str],
    dimension: str | None,
    shown_members: list[tuple[str, str, bool]],
    reporting: str | None,
) -> MetricDataOut:
    base = MetricDataOut(
        metric_code=metric.metric_code,
        display_name=metric.display_name,
        source=source,
        unit=metric.unit,
        aggregation=metric.aggregation,
        direction=metric.direction,
        org=None,
        members=[],
        pending=None,
        run_id=None,
    )
    if source == "campaign":
        base.pending = "Campaign results feed the Executive dashboard in a later release."
        return base

    org_id = ctx.org_id
    if source == "independent":
        base.run_id = _independent_run(org_id, widget.widget_key, metric, period_key)

    def series_for(dim: str, member: str, subject_ids: set[str]) -> list[SeriesOut]:
        out: list[SeriesOut] = []
        for s in series:
            if source == "rollup":
                figure = c.rollup_series(org_id, metric, subject_ids, period_key, s, reporting)
            else:
                figure = c.independent_series(
                    org_id, widget.widget_key, metric, period_key, dim, member, s, reporting
                )
            out.append(_series_out(figure))
        return out

    if dimension is None:
        subjects = c.all_subjects(org_id, period_key) if source == "rollup" else set()
        base.org = series_for("", "", subjects)
        return base

    placements = (
        c.subjects_by_member(org_id, dimension, [m for m, _, _ in shown_members], period_key)
        if source == "rollup"
        else {}
    )
    for code, member_name, has_children in shown_members:
        base.members.append(
            MemberDataOut(
                member_code=code,
                member_name=member_name,
                has_children=has_children,
                series=series_for(dimension, code, placements.get(code, set())),
            )
        )
    return base


def _independent_run(org_id: str, widget_key: str, metric: Metric, period_key: str) -> str | None:
    rows = c.independent_rows(org_id, widget_key, metric, period_key)
    runs = sorted({str(r.run_id) for r in rows.values() if r.run_id is not None})
    return runs[-1] if runs else None


@action(
    name="widget.data",
    summary="An Executive widget's figures for a period: org-level or by dimension member.",
    schema=WidgetDataIn,
    output=WidgetDataOut,
    permission="executive.view",
    read_only=True,
    module="executive",
    example={"widget_key": "revenue", "period_key": "202610"},
)
def widget_data(params: WidgetDataIn, ctx: ActionContext) -> WidgetDataOut:
    widget = WidgetDefinition.objects.filter(
        org_id=ctx.org_id, widget_key=params.widget_key, is_current=True, state="placed"
    ).first()
    if widget is None:
        raise NotFound(f"No widget '{params.widget_key}' is on the dashboard.")
    spec: dict[str, Any] = widget.definition
    dimension: str | None = spec.get("dimension")
    series: list[str] = spec.get("series", ["actual"])
    reporting = c.reporting_currency(ctx.org_id)
    thresholds = _thresholds(ctx, widget)
    whole_org, granted = _granted(ctx, dimension)

    empty: str | None = None
    breadcrumb: list[tuple[str, str]] = []
    shown: list[tuple[str, str, bool]] = []
    if dimension is None:
        if not whole_org:
            empty = _missing_grant(ctx, None)
    else:
        visible = None if whole_org else granted
        if params.drill_to is not None and visible is not None and params.drill_to not in visible:
            empty = _missing_grant(ctx, dimension)
        else:
            shown, breadcrumb = _members_at(ctx.org_id, dimension, params.drill_to, visible)
            if not shown and not whole_org:
                empty = _missing_grant(ctx, dimension)

    metrics: list[MetricDataOut] = []
    if empty is None:
        for m in spec.get("metrics", []):
            metric = _in_force(ctx.org_id, m["metric_code"], _today(ctx.org_id))
            if metric is None:
                continue
            metrics.append(
                _compute_metric(
                    ctx,
                    widget,
                    metric,
                    m.get("source", "independent"),
                    params.period_key,
                    series,
                    dimension,
                    shown,
                    reporting,
                )
            )

    return WidgetDataOut(
        widget_key=widget.widget_key,
        version=widget.version,
        widget_type=widget.widget_type,
        title=widget.title,
        period_key=params.period_key,
        dimension=dimension,
        breadcrumb=[BreadcrumbOut(member_code=code, member_name=nm) for code, nm in breadcrumb],
        reporting_currency=reporting,
        series=series,
        metrics=metrics,
        thresholds=thresholds if empty is None else None,
        empty=empty,
    )


def _today(org_id: str) -> date:
    from kpigo.ingestion.reference import org_today

    return org_today(org_id)


def _in_force(org_id: str, code: str, today: date) -> Metric | None:
    from django.db.models import Q

    return (
        Metric.objects.filter(org_id=org_id, metric_code=code, effective_from__lte=today)
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=today))
        .first()
    )
