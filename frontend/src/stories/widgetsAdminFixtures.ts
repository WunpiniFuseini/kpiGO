/** Story and test data for the Widgets admin (Administer → Widgets). */
import type { Output } from "../api/actions";
import type { TypeSpec } from "../pages/admin/widgetForm";

type WidgetOut = Output<"widget.list">["widgets"][number];

function spec(
  type: string,
  label: string,
  renders: string,
  min_metrics: number,
  max_metrics: number,
  dimension_single: string,
  dimension_multi: string,
  comparisons: boolean,
  needs_comparison: boolean,
  additive_only: boolean,
  one_unit: boolean,
  thresholds: boolean,
  default_w: number,
  default_h: number,
): TypeSpec {
  return { type, label, renders, min_metrics, max_metrics, dimension_single, dimension_multi, comparisons, needs_comparison, additive_only, one_unit, thresholds, default_w, default_h } as TypeSpec;
}

/** The capability matrix, mirroring kpigo/executive/widgets.py TYPES. */
export const types: TypeSpec[] = [
  spec("kpi_card", "KPI card", "Figure, delta chip, comparison caption, optional sparkline", 1, 1, "never", "never", true, false, false, false, true, 3, 2),
  spec("bullet", "Bullet", "Actual bar against a target marker and threshold bands", 1, 6, "optional", "never", true, true, false, false, true, 6, 3),
  spec("gauge", "Gauge", "Single value on an arc with threshold bands", 1, 1, "never", "never", true, false, false, false, true, 3, 3),
  spec("bar", "Bar", "A metric across dimension members, or several metrics side by side", 1, 6, "required", "optional", true, false, false, true, false, 6, 4),
  spec("line", "Line", "One or more series over time", 1, 4, "never", "never", true, false, false, true, false, 6, 4),
  spec("pie", "Pie / donut", "Composition of a whole: one metric across members, or several metrics", 1, 8, "required", "never", false, false, true, true, false, 4, 4),
  spec("ranked_list", "Ranked list", "Ordered members with values", 1, 1, "required", "never", true, false, false, false, false, 4, 4),
  spec("table", "Table", "Members by metrics grid with RAG", 1, 8, "required", "required", true, false, false, false, true, 12, 4),
  spec("funnel", "Funnel", "Ordered stages with drop-off", 2, 8, "never", "never", false, false, true, true, false, 4, 4),
];

function metric(code: string, name: string, unit: string, aggregation: string): Output<"metric.list">["metrics"][number] {
  return {
    metric_id: `00000000-0000-4000-8000-${code.padEnd(12, "0").slice(0, 12).replace(/[^0-9a-f]/g, "0")}`,
    family_id: "00000000-0000-4000-8000-000000000001",
    metric_code: code,
    display_name: name,
    direction: "higher_is_better",
    aggregation,
    unit,
    decimal_places: 0,
    is_percentage: unit === "percent",
    target_scope: "profile",
    collection_method: "feed",
    status: "active",
    computation_note: "",
    effective_from: "2026-10-01",
    effective_to: null,
    supersedes_id: null,
    bindings: [{ product: "executive", is_active: true }],
    profiles: [],
  };
}

export const execMetrics: Output<"metric.list"> = {
  as_of: "2026-10-06",
  metrics: [
    metric("ex_revenue", "Total revenue", "currency", "sum"),
    metric("ex_casa", "CASA growth", "currency", "sum"),
    metric("ex_leads", "Leads", "count", "sum"),
    metric("ex_cti", "Cost-to-income", "percent", "average"),
    metric("ex_nps", "Net promoter score", "score", "average"),
    { ...metric("sc_only", "Scorecards only", "currency", "sum"), bindings: [{ product: "scorecards", is_active: true }] },
  ],
};

export const dimensions: Output<"dimension.list"> = {
  dimensions: [
    { dimension_type: "region", display_name: "Region", is_custom: false, sort_order: 1 },
    { dimension_type: "branch", display_name: "Branch", is_custom: false, sort_order: 2 },
    { dimension_type: "segment", display_name: "Segment", is_custom: true, sort_order: 3 },
  ] as Output<"dimension.list">["dimensions"],
};

function widget(over: Partial<WidgetOut> & Pick<WidgetOut, "widget_key" | "state">): WidgetOut {
  return {
    version: 1,
    title: over.title ?? "",
    widget_type: "kpi_card",
    metrics: [{ metric_code: "ex_revenue", source: "independent" }],
    dimension: null,
    series: ["actual", "target"],
    thresholds: { source: "metric", basis: null, bands: [], note: "" },
    layout: { x: 0, y: 0, w: 3, h: 2 },
    options: { sparkline: false, top_n: 10 },
    change: "placed",
    changed_at: "2026-10-05T10:00:00Z",
    changed_by: 1,
    approval_request_id: null,
    first_detected_at: null,
    ...over,
  };
}

export const placedWidget = widget({ widget_key: "revenue", state: "placed", title: "Total revenue", widget_type: "kpi_card" });
export const placedBar = widget({ widget_key: "casa_region", state: "placed", title: "CASA by region", widget_type: "bar", dimension: "region", metrics: [{ metric_code: "ex_casa", source: "rollup" }] });
export const availableWidget = widget({ widget_key: "new_from_feed", state: "available", title: "", widget_type: null, metrics: [], series: ["actual"], layout: null, change: "registered", first_detected_at: "2026-10-06T06:00:00Z" });
export const removedWidget = widget({ widget_key: "old_pie", state: "removed", title: "Old mix", widget_type: "pie", change: "removed" });

export const widgetList: Output<"widget.list"> = { types, widgets: [placedWidget, placedBar, availableWidget] };
export const widgetListWithRemoved: Output<"widget.list"> = { types, widgets: [placedWidget, placedBar, availableWidget, removedWidget] };
export const widgetListEmpty: Output<"widget.list"> = { types, widgets: [] };

export const history: Output<"widget.history"> = {
  widget_key: "revenue",
  versions: [
    widget({ widget_key: "revenue", state: "placed", title: "Total revenue", version: 3, change: "thresholds_changed", changed_at: "2026-10-06T09:00:00Z" }),
    widget({ widget_key: "revenue", state: "placed", title: "Total revenue", version: 2, change: "type_changed", widget_type: "bullet", changed_at: "2026-10-05T14:00:00Z" }),
    widget({ widget_key: "revenue", state: "placed", title: "Revenue", version: 1, change: "placed", changed_at: "2026-10-05T10:00:00Z" }),
  ],
};
