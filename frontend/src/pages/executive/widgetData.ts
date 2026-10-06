/**
 * Reading an Executive widget's figures for display: pulling a series value out
 * of a metric (organisation-level or per member), working the achievement
 * against the metric's target, and placing it on the client's rating bands.
 *
 * The API sends every figure as a decimal string; here it becomes a number for
 * ECharts and formatting, or null when nothing is reported (absent is not zero).
 */
import type { Output } from "../../api/actions";
import { formatValue } from "../../lib/format";

export type WidgetData = Output<"widget.data">;
export type MetricData = WidgetData["metrics"][number];
export type MemberData = MetricData["members"][number];
export type SeriesFig = NonNullable<MetricData["org"]>[number];
export type SeriesType = WidgetData["series"][number];
export type Thresholds = NonNullable<WidgetData["thresholds"]>;
export type GradeTone = 1 | 2 | 3 | 4;

/** A decimal string from the API as a number, or null when not reported. */
export function num(value: string | null | undefined): number | null {
  if (value === null || value === undefined || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

/** The figure for one series on a metric's organisation line, or a member's. */
export function figure(series: SeriesFig[], type: SeriesType): number | null {
  return num(series.find((s) => s.series_type === type)?.value);
}

/** How many decimals a unit reads best at. */
export function decimalsFor(unit: string): number {
  if (unit === "percent" || unit === "ratio" || unit === "score") return 1;
  return 0;
}

/** Format a figure the way its metric should read; a null shows as an en dash. */
export function formatFigure(
  value: number | null,
  unit: string,
  currency: string | null,
): string {
  if (value === null) return "–";
  return formatValue(value, { unit, decimals: decimalsFor(unit), currency: currency ?? "" });
}

/**
 * The value a widget's thresholds are read against: the achievement ratio
 * (actual ÷ target) when the bands are on achievement, else the figure itself.
 * No target on an achievement basis means there is nothing to grade.
 */
export function gradeValue(
  thresholds: Thresholds,
  actual: number | null,
  target: number | null,
): number | null {
  if (actual === null) return null;
  if (thresholds.basis === "achievement") {
    if (target === null || target === 0) return null;
    return actual / target;
  }
  return actual;
}

/** The band a graded value falls in: the highest whose threshold it reaches. */
export function bandIndex(thresholds: Thresholds, value: number | null): number | null {
  const bands = thresholds.bands;
  if (value === null || bands.length === 0) return null;
  let chosen = 0;
  bands.forEach((b, i) => {
    if (value >= Number(b.threshold)) chosen = i;
  });
  return chosen;
}

/**
 * The grade ramp tone (1 lowest … 4 highest) for a band, spread across however
 * many bands the client configured — colour is never the sole carrier, so the
 * band's own label always travels with it.
 */
export function toneForBand(index: number, count: number): GradeTone {
  if (count <= 1) return 3;
  const t = Math.round((index / (count - 1)) * 3) + 1;
  return Math.min(4, Math.max(1, t)) as GradeTone;
}

export interface Standing {
  tone: GradeTone;
  label: string;
}

/** The standing (tone + band label) for a metric's figure, or null when ungraded. */
export function standingFor(
  thresholds: Thresholds | null,
  actual: number | null,
  target: number | null,
): Standing | null {
  if (!thresholds) return null;
  const value = gradeValue(thresholds, actual, target);
  const index = bandIndex(thresholds, value);
  if (index === null) return null;
  return { tone: toneForBand(index, thresholds.bands.length), label: thresholds.bands[index].label };
}

/** A readable label for a comparison series, for legends and tables. */
export function seriesLabel(type: SeriesType): string {
  const names: Record<string, string> = {
    actual: "Actual",
    target: "Target",
    forecast: "Forecast",
    budget: "Budget",
    prior: "Prior period",
    prior_year: "Prior year",
  };
  return names[type] ?? type;
}
