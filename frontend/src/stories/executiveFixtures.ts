/** Story and test data for the Executive dashboard, typed by the generated API. */
import type { Output } from "../api/actions";
import type { MemberData, MetricData, SeriesType, WidgetData } from "../pages/executive/widgetData";

type Placed = Output<"widget.dashboard">["widgets"][number];

const BANDS = [
  { label: "Needs Focus", threshold: "0" },
  { label: "Gaining Momentum", threshold: "0.75" },
  { label: "On Target", threshold: "1.0" },
  { label: "Exemplary", threshold: "1.2" },
];

function seriesFigs(values: Partial<Record<SeriesType, number | null>>, currency: string | null = "GHS") {
  return Object.entries(values).map(([series_type, v]) => ({
    series_type: series_type as SeriesType,
    value: v === null || v === undefined ? null : String(v),
    currency,
    subjects: null,
    skipped_no_fx: 0,
  }));
}

function metric(over: Partial<MetricData> & Pick<MetricData, "metric_code" | "display_name">): MetricData {
  return {
    source: "independent",
    unit: "currency",
    aggregation: "sum",
    direction: "higher_is_better",
    org: null,
    members: [],
    pending: null,
    run_id: "run-1",
    ...over,
  };
}

function member(code: string, name: string, values: Partial<Record<SeriesType, number | null>>, has_children = false): MemberData {
  return { member_code: code, member_name: name, has_children, series: seriesFigs(values) };
}

export function widgetData(over: Partial<WidgetData> & Pick<WidgetData, "widget_key" | "widget_type">): WidgetData {
  return {
    version: 1,
    title: over.title ?? over.widget_key,
    period_key: "202610",
    dimension: null,
    breadcrumb: [],
    reporting_currency: "GHS",
    series: ["actual", "target"],
    thresholds: { source: "metric", basis: "achievement", bands: BANDS, note: "" },
    empty: null,
    metrics: [],
    ...over,
  };
}

// One fixture per widget type, each a healthy reading.

export const kpiData = widgetData({
  widget_key: "revenue",
  widget_type: "kpi_card",
  title: "Total revenue",
  series: ["actual", "target", "prior"],
  metrics: [metric({ metric_code: "ex_revenue", display_name: "Total revenue", org: seriesFigs({ actual: 42_600_000, target: 39_300_000, prior: 38_100_000, prior_year: 31_000_000 }) })],
});

export const bulletData = widgetData({
  widget_key: "cti",
  widget_type: "bullet",
  title: "Cost-to-income",
  metrics: [
    metric({ metric_code: "ex_cti", display_name: "Cost-to-income", unit: "percent", direction: "lower_is_better", org: seriesFigs({ actual: 52, target: 48 }, null) }),
    metric({ metric_code: "ex_casa", display_name: "CASA growth", org: seriesFigs({ actual: 120, target: 100 }) }),
  ],
});

export const gaugeData = widgetData({
  widget_key: "nps",
  widget_type: "gauge",
  title: "Net promoter score",
  metrics: [metric({ metric_code: "ex_nps", display_name: "Net promoter score", unit: "score", aggregation: "average", org: seriesFigs({ actual: 61, target: 55 }, null) })],
});

export const barData = widgetData({
  widget_key: "casa_region",
  widget_type: "bar",
  title: "CASA by region",
  dimension: "region",
  series: ["actual", "target"],
  metrics: [
    metric({
      metric_code: "ex_casa",
      display_name: "CASA growth",
      source: "rollup",
      members: [member("north", "North", { actual: 90, target: 120 }, true), member("south", "South", { actual: 150, target: 130 }, true)],
    }),
  ],
});

export const lineData = widgetData({
  widget_key: "revenue_trend",
  widget_type: "line",
  title: "Revenue trend",
  series: ["prior_year", "prior", "actual", "target"],
  metrics: [metric({ metric_code: "ex_revenue", display_name: "Total revenue", org: seriesFigs({ prior_year: 31_000_000, prior: 38_100_000, actual: 42_600_000, target: 39_300_000 }) })],
});

export const pieData = widgetData({
  widget_key: "revenue_mix",
  widget_type: "pie",
  title: "Revenue by region",
  dimension: "region",
  series: ["actual"],
  thresholds: null,
  metrics: [
    metric({
      metric_code: "ex_revenue",
      display_name: "Total revenue",
      source: "rollup",
      members: [member("north", "North", { actual: 18_000_000 }, true), member("south", "South", { actual: 24_600_000 }, true)],
    }),
  ],
});

export const rankedData = widgetData({
  widget_key: "casa_ranked",
  widget_type: "ranked_list",
  title: "CASA growth by region",
  dimension: "region",
  metrics: [
    metric({
      metric_code: "ex_casa",
      display_name: "CASA growth",
      source: "rollup",
      members: [member("south", "South", { actual: 150, target: 130 }), member("north", "North", { actual: 90, target: 120 })],
    }),
  ],
});

export const tableData = widgetData({
  widget_key: "scorecard_grid",
  widget_type: "table",
  title: "Regions by metric",
  dimension: "region",
  series: ["actual"],
  metrics: [
    metric({ metric_code: "ex_revenue", display_name: "Revenue", source: "rollup", members: [member("north", "North", { actual: 18_000_000, target: 20_000_000 }), member("south", "South", { actual: 24_600_000, target: 22_000_000 })] }),
    metric({ metric_code: "ex_casa", display_name: "CASA", source: "rollup", members: [member("north", "North", { actual: 90, target: 120 }), member("south", "South", { actual: 150, target: 130 })] }),
  ],
});

export const funnelData = widgetData({
  widget_key: "pipeline",
  widget_type: "funnel",
  title: "Acquisition funnel",
  series: ["actual"],
  thresholds: null,
  metrics: [
    metric({ metric_code: "ex_leads", display_name: "Leads", unit: "count", org: seriesFigs({ actual: 4200 }, null) }),
    metric({ metric_code: "ex_qualified", display_name: "Qualified", unit: "count", org: seriesFigs({ actual: 2100 }, null) }),
    metric({ metric_code: "ex_accounts", display_name: "Accounts opened", unit: "count", org: seriesFigs({ actual: 980 }, null) }),
  ],
});

/** A breakdown drilled one level, so a breadcrumb shows. */
export const drilledBarData = widgetData({
  widget_key: "casa_region",
  widget_type: "bar",
  title: "CASA by region",
  dimension: "region",
  breadcrumb: [{ member_code: "south", member_name: "South" }],
  metrics: [
    metric({
      metric_code: "ex_casa",
      display_name: "CASA growth",
      source: "rollup",
      members: [member("GA", "Greater Accra", { actual: 100, target: 90 }), member("AS", "Ashanti", { actual: 50, target: 60 })],
    }),
  ],
});

/** The server's named empty state (no grant for the whole organisation). */
export const emptyData = widgetData({
  widget_key: "rev_total",
  widget_type: "kpi_card",
  title: "Total revenue",
  empty: "You need an Executive data grant for the whole organisation to see this widget.",
  thresholds: null,
});

/** A campaign metric whose value flow is not wired yet. */
export const pendingData = widgetData({
  widget_key: "camp",
  widget_type: "kpi_card",
  title: "Campaign uplift",
  thresholds: null,
  metrics: [metric({ metric_code: "camp_uplift", display_name: "Campaign uplift", source: "campaign", org: null, pending: "This campaign result's value flow is wired in a later step." })],
});

function placed(key: string, title: string, w: number, h = 3): Placed {
  return {
    widget_key: key,
    version: 1,
    state: "placed",
    title,
    widget_type: "kpi_card",
    metrics: [],
    dimension: null,
    series: ["actual"],
    thresholds: { source: "metric", basis: "achievement", bands: BANDS, note: "" },
    layout: { x: 0, y: 0, w, h },
    options: { sparkline: false, top_n: 10 },
    change: "placed",
    changed_at: "2026-10-05T10:00:00Z",
    changed_by: 1,
    approval_request_id: null,
    first_detected_at: null,
  };
}

/** A dashboard with one widget of every type placed across the grid. */
export const dashboard: Output<"widget.dashboard"> = {
  types: [],
  widgets: [
    placed("revenue", "Total revenue", 3),
    placed("nps", "Net promoter score", 3),
    placed("cti", "Cost-to-income", 6),
    placed("casa_region", "CASA by region", 6),
    placed("revenue_trend", "Revenue trend", 6),
    placed("revenue_mix", "Revenue by region", 4),
    placed("casa_ranked", "CASA growth by region", 4),
    placed("pipeline", "Acquisition funnel", 4),
    placed("scorecard_grid", "Regions by metric", 12),
  ],
};

/** widget.data keyed by widget_key, for a page story that fetches each tile. */
export const byKey: Record<string, WidgetData> = {
  revenue: kpiData,
  nps: gaugeData,
  cti: bulletData,
  casa_region: barData,
  revenue_trend: lineData,
  revenue_mix: pieData,
  casa_ranked: rankedData,
  pipeline: funnelData,
  scorecard_grid: tableData,
};

export const emptyDashboard: Output<"widget.dashboard"> = { types: [], widgets: [] };
