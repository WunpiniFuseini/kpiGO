/**
 * What a widget type can draw, turned into what the Place/Edit form lets an
 * Admin choose (App Flow 6.1, Scope §10): each type declares its metric count,
 * whether it breaks down by a dimension, whether it draws comparison series and
 * threshold bands, and whether its metrics must be additive and share a unit.
 * The form greys out the combinations a type cannot draw and validates the rest,
 * so a change the server would refuse cannot be submitted.
 */
import type { Output } from "../../api/actions";

export type TypeSpec = Output<"widget.list">["types"][number];
export type MetricRow = Output<"metric.list">["metrics"][number];
export type SeriesType = "actual" | "target" | "forecast" | "budget" | "prior" | "prior_year";
export type DimensionRule = "never" | "optional" | "required";

export const ALL_SERIES: { value: SeriesType; label: string }[] = [
  { value: "actual", label: "Actual" },
  { value: "target", label: "Target" },
  { value: "forecast", label: "Forecast" },
  { value: "budget", label: "Budget" },
  { value: "prior", label: "Prior period" },
  { value: "prior_year", label: "Prior year" },
];

const ADDITIVE = new Set(["sum", "count"]);

export interface WidgetDraft {
  widget_key: string;
  title: string;
  widget_type: TypeSpec["type"];
  metrics: string[];
  dimension: string;
  series: SeriesType[];
}

export function typeByKey(types: TypeSpec[]): Map<string, TypeSpec> {
  return new Map(types.map((t) => [t.type, t]));
}

/** The breakdown rule for a type at the chosen metric count (one vs several). */
export function dimensionRule(spec: TypeSpec, metricCount: number): DimensionRule {
  return (metricCount > 1 ? spec.dimension_multi : spec.dimension_single) as DimensionRule;
}

/** A metric is bound to Executive and active, so a widget may draw it. */
export function executiveMetrics(metrics: MetricRow[]): MetricRow[] {
  return metrics.filter(
    (m) => m.status === "active" && (m.bindings ?? []).some((b) => b.product === "executive" && b.is_active),
  );
}

/** Why a metric cannot join the current selection, or null when it can. */
export function metricBlocked(spec: TypeSpec, chosen: MetricRow[], metric: MetricRow): string | null {
  if (spec.additive_only && !ADDITIVE.has(metric.aggregation)) {
    return "This type adds its metrics up, so each must be a sum or a count.";
  }
  if (spec.one_unit && chosen.length > 0 && chosen[0].unit !== metric.unit && !chosen.some((m) => m.metric_code === metric.metric_code)) {
    return `This type draws one unit; it already shows ${chosen[0].unit}.`;
  }
  return null;
}

/** The problems with a draft, in reading order; empty means it can be submitted. */
export function validateDraft(draft: WidgetDraft, spec: TypeSpec, byCode: Map<string, MetricRow>): string[] {
  const problems: string[] = [];
  if (!/^[a-z][a-z0-9_]{0,63}$/.test(draft.widget_key)) {
    problems.push("A widget key is lower_snake_case, starting with a letter.");
  }
  if (draft.metrics.length < spec.min_metrics || draft.metrics.length > spec.max_metrics) {
    problems.push(
      spec.min_metrics === spec.max_metrics
        ? `A ${spec.label} shows exactly ${spec.min_metrics} metric${spec.min_metrics === 1 ? "" : "s"}.`
        : `A ${spec.label} shows ${spec.min_metrics} to ${spec.max_metrics} metrics.`,
    );
  }
  const rule = dimensionRule(spec, draft.metrics.length);
  if (rule === "never" && draft.dimension) problems.push(`A ${spec.label} does not break down by a dimension.`);
  if (rule === "required" && !draft.dimension) problems.push(`A ${spec.label} breaks down by a dimension; choose one.`);
  if (spec.needs_comparison && !draft.series.some((s) => s !== "actual")) {
    problems.push(`A ${spec.label} needs a comparison series, such as the target.`);
  }
  const chosen = draft.metrics.map((c) => byCode.get(c)).filter((m): m is MetricRow => Boolean(m));
  if (spec.one_unit && new Set(chosen.map((m) => m.unit)).size > 1) {
    problems.push("This type draws one unit; the chosen metrics differ.");
  }
  if (spec.additive_only && chosen.some((m) => !ADDITIVE.has(m.aggregation))) {
    problems.push("This type adds its metrics up, so each must be a sum or a count.");
  }
  if (draft.metrics.length > 1 && !draft.title.trim()) {
    problems.push("Give a widget showing several metrics a title.");
  }
  return problems;
}

/** The series actually stored for a type: none but actual when it draws no comparisons. */
export function effectiveSeries(spec: TypeSpec, series: SeriesType[]): SeriesType[] {
  if (!spec.comparisons) return ["actual"];
  return series.includes("actual") ? series : ["actual", ...series];
}
