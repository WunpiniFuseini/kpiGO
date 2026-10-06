/**
 * The nine widget renderers (the bundle, Scope §10). Each takes one widget's
 * computed figures and draws it; a breakdown widget can drill a member down the
 * dimension. The figure types (KPI card, ranked list, table) reuse the shared
 * components; the chart types draw on {@link ExecChart}.
 *
 * Colour is never the sole carrier: a graded mark always carries its band label
 * (a RAG cell, a bullet) and a movement is a signed figure, not just a hue.
 */
import { GradePill } from "../../components/GradePill";
import { MetricCard } from "../../components/MetricCard";
import { RankedList, type RankedItem } from "../../components/RankedList";
import { gradeColour } from "../../lib/chartTheme";
import { ExecChart } from "./ExecChart";
import {
  type MemberData,
  type MetricData,
  type SeriesType,
  type WidgetData,
  decimalsFor,
  figure,
  formatFigure,
  gradeValue,
  seriesLabel,
  standingFor,
} from "./widgetData";

export interface RenderProps {
  data: WidgetData;
  /** Drill a breakdown member down the dimension (only when it has children). */
  onDrill?: (memberCode: string) => void;
}

// Comparison series that sit on a short timeline, oldest first (for a line).
const TIMELINE: SeriesType[] = ["prior_year", "prior", "actual", "forecast"];

function orgSeries(metric: MetricData): NonNullable<MetricData["org"]> {
  return metric.org ?? [];
}

function memberFigure(member: MemberData, type: SeriesType): number | null {
  const found = member.series.find((s) => s.series_type === type);
  return found?.value === null || found?.value === undefined ? null : Number(found.value);
}

function unitOf(data: WidgetData): string {
  return data.metrics[0]?.unit ?? "count";
}

function currencyOf(data: WidgetData): string | null {
  return data.reporting_currency;
}

/** A metric with no figures (a campaign result not yet wired) says so, plainly. */
function Pending({ note }: { note: string }) {
  return <p className="kg-cap">{note}</p>;
}

// ── KPI card ───────────────────────────────────────────────────────────────

function KpiCard({ data }: RenderProps) {
  const metric = data.metrics[0];
  if (metric.pending) return <Pending note={metric.pending} />;
  const org = orgSeries(metric);
  const actual = figure(org, "actual");
  const target = figure(org, "target");
  const prior = figure(org, "prior") ?? figure(org, "prior_year");
  const delta = prior !== null && prior !== 0 && actual !== null ? ((actual - prior) / Math.abs(prior)) * 100 : null;
  const trend =
    data.metrics.length === 1
      ? ([figure(org, "prior_year"), figure(org, "prior"), actual].filter((v) => v !== null) as number[])
      : undefined;
  const currency = currencyOf(data);
  return (
    <MetricCard
      label={metric.display_name}
      value={actual === null ? null : formatFigure(actual, metric.unit, currency)}
      target={target === null ? null : formatFigure(target, metric.unit, currency)}
      delta={delta === null ? undefined : delta}
      higherIsBetter={metric.direction === "higher_is_better"}
      trend={trend && trend.length > 1 ? trend : undefined}
      state={actual === null ? "awaiting" : "ready"}
      reason={actual === null ? "Not reported for this period." : undefined}
    />
  );
}

// ── bullet ───────────────────────────────────────────────────────────────

interface BulletRow {
  key: string;
  label: string;
  actual: number | null;
  target: number | null;
  tone: 1 | 2 | 3 | 4 | null;
  band: string | null;
}

function Bullet({ data }: RenderProps) {
  const unit = unitOf(data);
  const currency = currencyOf(data);
  const rows: BulletRow[] = [];
  // With a dimension and one metric, a row per member; otherwise a row per metric.
  if (data.dimension && data.metrics.length === 1) {
    const metric = data.metrics[0];
    for (const m of metric.members) {
      const actual = memberFigure(m, "actual");
      const target = memberFigure(m, "target");
      const standing = standingFor(data.thresholds, actual, target);
      rows.push({ key: m.member_code, label: m.member_name, actual, target, tone: standing?.tone ?? null, band: standing?.label ?? null });
    }
  } else {
    for (const metric of data.metrics) {
      const org = orgSeries(metric);
      const actual = figure(org, "actual");
      const target = figure(org, "target");
      const standing = standingFor(data.thresholds, actual, target);
      rows.push({ key: metric.metric_code, label: metric.display_name, actual, target, tone: standing?.tone ?? null, band: standing?.label ?? null });
    }
  }
  const scale = Math.max(1, ...rows.flatMap((r) => [r.actual ?? 0, r.target ?? 0])) * 1.1;
  return (
    <ul className="kg-bullets" aria-label={`${data.title}: actual against target`}>
      {rows.map((r) => (
        <li key={r.key} className="kg-bullet">
          <div className="kg-row">
            <span className="kg-bullet__label">{r.label}</span>
            <span className="kg-bullet__fig num">{formatFigure(r.actual, unit, currency)}</span>
          </div>
          <div className="kg-bullet__track" aria-hidden="true">
            <span className="kg-bullet__bar" data-grade={r.tone ?? undefined} style={{ width: `${Math.min(100, ((r.actual ?? 0) / scale) * 100)}%` }} />
            {r.target === null ? null : <span className="kg-bullet__target" style={{ left: `${Math.min(100, (r.target / scale) * 100)}%` }} />}
          </div>
          <p className="kg-cap">
            {r.band ? <GradePill tone={r.tone ?? 3} label={r.band} /> : null}{" "}
            {r.target === null ? "No target" : <>Target <span className="num">{formatFigure(r.target, unit, currency)}</span></>}
          </p>
        </li>
      ))}
    </ul>
  );
}

// ── gauge ───────────────────────────────────────────────────────────────

function Gauge({ data }: RenderProps) {
  const metric = data.metrics[0];
  if (metric.pending) return <Pending note={metric.pending} />;
  const org = orgSeries(metric);
  const actual = figure(org, "actual");
  const target = figure(org, "target");
  const unit = unitOf(data);
  const currency = currencyOf(data);
  const max = Math.max(actual ?? 0, target ?? 0, 1) * 1.25;
  const standing = standingFor(data.thresholds, actual, target);
  const colour = standing ? gradeColour(standing.tone) : undefined;
  const option = {
    series: [
      {
        type: "gauge",
        min: 0,
        max,
        progress: { show: true, width: 14, itemStyle: colour ? { color: colour } : undefined },
        axisLine: { lineStyle: { width: 14 } },
        axisLabel: { show: false },
        axisTick: { show: false },
        splitLine: { show: false },
        pointer: { show: false },
        detail: { valueAnimation: false, formatter: () => formatFigure(actual, unit, currency), fontSize: 22, color: "#101828", offsetCenter: [0, "20%"] },
        data: [{ value: actual ?? 0 }],
      },
    ],
  };
  const table = (
    <table className="kg-table">
      <caption className="sr-only">{metric.display_name} value and target</caption>
      <tbody>
        <tr><th scope="row">Actual</th><td className="is-num">{formatFigure(actual, unit, currency)}</td></tr>
        <tr><th scope="row">Target</th><td className="is-num">{formatFigure(target, unit, currency)}</td></tr>
        {standing ? <tr><th scope="row">Standing</th><td>{standing.label}</td></tr> : null}
      </tbody>
    </table>
  );
  const label = `${metric.display_name}: ${formatFigure(actual, unit, currency)}${standing ? `, ${standing.label}` : ""}, on a gauge to ${formatFigure(max, unit, currency)}.`;
  if (actual === null) return <Pending note="Not reported for this period." />;
  return <ExecChart title={metric.display_name} option={option} ariaLabel={label} table={table} height={180} />;
}

// ── bar ───────────────────────────────────────────────────────────────

function Bar({ data, onDrill }: RenderProps) {
  const unit = unitOf(data);
  const currency = currencyOf(data);
  const shownSeries = data.series.filter((s) => s === "actual" || s === "target" || s === "budget" || s === "forecast");
  const breakdown = Boolean(data.dimension) && data.metrics.length === 1;
  const metric = data.metrics[0];

  const categories = breakdown ? metric.members.map((m) => m.member_name) : data.metrics.map((m) => m.display_name);
  const codes = breakdown ? metric.members.map((m) => m.member_code) : data.metrics.map((m) => m.metric_code);
  const drillable = breakdown ? new Set(metric.members.filter((m) => m.has_children).map((m) => m.member_name)) : new Set<string>();

  const series = breakdown
    ? shownSeries.map((s) => ({ name: seriesLabel(s), type: "bar", data: metric.members.map((m) => memberFigure(m, s)) }))
    : [{ name: seriesLabel("actual"), type: "bar", data: data.metrics.map((m) => figure(orgSeries(m), "actual")) }];

  const option = {
    grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
    legend: breakdown && shownSeries.length > 1 ? { top: 0 } : { show: false },
    tooltip: { trigger: "axis", valueFormatter: (v: unknown) => (typeof v === "number" ? formatFigure(v, unit, currency) : "–") },
    xAxis: { type: "category", data: categories },
    yAxis: { type: "value", axisLabel: { formatter: (v: number) => formatFigure(v, unit, currency) } },
    series,
  };
  const table = (
    <table className="kg-table">
      <caption className="sr-only">{data.title} by {breakdown ? data.dimension : "metric"}</caption>
      <thead>
        <tr>
          <th scope="col">{breakdown ? "Member" : "Metric"}</th>
          {(breakdown ? shownSeries : (["actual"] as SeriesType[])).map((s) => (
            <th scope="col" className="is-num" key={s}>{seriesLabel(s)}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {categories.map((name, i) => (
          <tr key={codes[i]}>
            <th scope="row">{name}</th>
            {(breakdown ? shownSeries : (["actual"] as SeriesType[])).map((s) => {
              const v = breakdown ? memberFigure(metric.members[i], s) : figure(orgSeries(data.metrics[i]), "actual");
              return <td className="is-num" key={s}>{formatFigure(v, unit, currency)}</td>;
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
  const drill = breakdown && onDrill
    ? (name: string) => {
        const idx = categories.indexOf(name);
        if (idx >= 0 && metric.members[idx]?.has_children) onDrill(codes[idx]);
      }
    : undefined;
  const hint = drillable.size > 0 ? " Select a bar to drill into it." : "";
  const label = `${data.title}: bar chart of ${categories.length} ${breakdown ? data.dimension : "metrics"}.${hint}`;
  return <ExecChart title={data.title} option={option} ariaLabel={label} table={table} onSelect={drill} />;
}

// ── line ───────────────────────────────────────────────────────────────

function Line({ data }: RenderProps) {
  const unit = unitOf(data);
  const currency = currencyOf(data);
  const points = TIMELINE.filter((s) => data.series.includes(s));
  const single = data.metrics.length === 1;
  const target = single ? figure(orgSeries(data.metrics[0]), "target") : null;
  const budget = single ? figure(orgSeries(data.metrics[0]), "budget") : null;
  const markData = [
    ...(data.series.includes("target") && target !== null ? [{ yAxis: target, name: "Target" }] : []),
    ...(data.series.includes("budget") && budget !== null ? [{ yAxis: budget, name: "Budget" }] : []),
  ];
  const option = {
    grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
    legend: data.metrics.length > 1 ? { top: 0 } : { show: false },
    tooltip: { trigger: "axis", valueFormatter: (v: unknown) => (typeof v === "number" ? formatFigure(v, unit, currency) : "–") },
    xAxis: { type: "category", data: points.map((s) => seriesLabel(s)) },
    yAxis: { type: "value", axisLabel: { formatter: (v: number) => formatFigure(v, unit, currency) } },
    series: data.metrics.map((m) => ({
      name: m.display_name,
      type: "line",
      connectNulls: false,
      data: points.map((s) => figure(orgSeries(m), s)),
      markLine: markData.length && single ? { symbol: "none", silent: true, lineStyle: { type: "dashed", color: "#475467", width: 1 }, label: { formatter: (p: { name: string }) => p.name, color: "#475467" }, data: markData } : undefined,
    })),
  };
  const table = (
    <table className="kg-table">
      <caption className="sr-only">{data.title} across comparison periods</caption>
      <thead>
        <tr><th scope="col">Metric</th>{points.map((s) => <th scope="col" className="is-num" key={s}>{seriesLabel(s)}</th>)}</tr>
      </thead>
      <tbody>
        {data.metrics.map((m) => (
          <tr key={m.metric_code}>
            <th scope="row">{m.display_name}</th>
            {points.map((s) => <td className="is-num" key={s}>{formatFigure(figure(orgSeries(m), s), unit, currency)}</td>)}
          </tr>
        ))}
      </tbody>
    </table>
  );
  const label = `${data.title}: line chart of ${data.metrics.length} metric${data.metrics.length === 1 ? "" : "s"} across ${points.length} comparison point${points.length === 1 ? "" : "s"}.`;
  return <ExecChart title={data.title} option={option} ariaLabel={label} table={table} />;
}

// ── pie ───────────────────────────────────────────────────────────────

function Pie({ data, onDrill }: RenderProps) {
  const unit = unitOf(data);
  const currency = currencyOf(data);
  const metric = data.metrics[0];
  const slices = metric.members.map((m) => ({ name: m.member_name, value: memberFigure(m, "actual") ?? 0, code: m.member_code, has: m.has_children }));
  const total = slices.reduce((t, s) => t + s.value, 0);
  const option = {
    tooltip: { trigger: "item", valueFormatter: (v: unknown) => (typeof v === "number" ? formatFigure(v, unit, currency) : "–") },
    legend: { bottom: 0, type: "scroll" },
    series: [{ type: "pie", radius: ["45%", "70%"], top: 0, bottom: 24, label: { show: false }, data: slices.map((s) => ({ name: s.name, value: s.value })) }],
  };
  const table = (
    <table className="kg-table">
      <caption className="sr-only">{metric.display_name} by {data.dimension}</caption>
      <thead><tr><th scope="col">Member</th><th scope="col" className="is-num">Value</th><th scope="col" className="is-num">Share</th></tr></thead>
      <tbody>
        {slices.map((s) => (
          <tr key={s.code}>
            <th scope="row">{s.name}</th>
            <td className="is-num">{formatFigure(s.value, unit, currency)}</td>
            <td className="is-num">{total > 0 ? `${((s.value / total) * 100).toFixed(1)}%` : "–"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
  const drill = onDrill ? (name: string) => { const s = slices.find((x) => x.name === name); if (s?.has) onDrill(s.code); } : undefined;
  const label = `${metric.display_name}: donut of ${slices.length} ${data.dimension} members making up ${formatFigure(total, unit, currency)}.`;
  return <ExecChart title={metric.display_name} option={option} ariaLabel={label} table={table} onSelect={drill} />;
}

// ── ranked list ───────────────────────────────────────────────────────────────

function Ranked({ data, onDrill }: RenderProps) {
  const metric = data.metrics[0];
  const unit = unitOf(data);
  const currency = currencyOf(data);
  const items: RankedItem[] = [...metric.members]
    .map((m) => {
      const actual = memberFigure(m, "actual");
      const target = memberFigure(m, "target");
      const standing = standingFor(data.thresholds, actual, target);
      const pace = data.thresholds ? gradeValue(data.thresholds, actual, target) : null;
      return {
        member: m,
        item: {
          id: m.member_code,
          name: m.member_name,
          value: formatFigure(actual, unit, currency),
          pace: data.thresholds?.basis === "achievement" ? pace : null,
          tone: standing?.tone,
          band: standing?.label,
          noPace: data.thresholds?.basis === "achievement" && pace === null ? "No target" : undefined,
          sort: actual ?? -Infinity,
        } satisfies RankedItem & { sort: number },
      };
    })
    .sort((a, b) => b.item.sort - a.item.sort)
    .map(({ item }, i) => ({ ...item, rank: i + 1 }));
  const select = onDrill
    ? (item: RankedItem) => { const m = metric.members.find((x) => x.member_code === item.id); if (m?.has_children) onDrill(m.member_code); }
    : undefined;
  return <RankedList items={items} label={`${metric.display_name} by ${data.dimension}`} onSelect={select} />;
}

// ── table ───────────────────────────────────────────────────────────────

function Grid({ data, onDrill }: RenderProps) {
  const currency = currencyOf(data);
  const base = data.metrics[0];
  return (
    <div className="kg-table-wrap">
      <table className="kg-table">
        <caption className="sr-only">{data.title}: {data.dimension} by metric</caption>
        <thead>
          <tr>
            <th scope="col">{data.dimension}</th>
            {data.metrics.map((m) => <th scope="col" className="is-num" key={m.metric_code}>{m.display_name}</th>)}
          </tr>
        </thead>
        <tbody>
          {base.members.map((member, rowIdx) => (
            <tr key={member.member_code}>
              <th scope="row">
                {onDrill && member.has_children ? (
                  <button type="button" className="kg-linkish" onClick={() => onDrill(member.member_code)}>{member.member_name}</button>
                ) : member.member_name}
              </th>
              {data.metrics.map((m) => {
                const mem = m.members[rowIdx];
                const actual = mem ? memberFigure(mem, "actual") : null;
                const target = mem ? memberFigure(mem, "target") : null;
                const standing = standingFor(data.thresholds, actual, target);
                return (
                  <td className="is-num" key={m.metric_code}>
                    <span className="num">{formatFigure(actual, m.unit, currency)}</span>
                    {standing ? <span className="kg-rag" data-grade={standing.tone}> <span className="sr-only">{standing.label}</span></span> : null}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── funnel ───────────────────────────────────────────────────────────────

function Funnel({ data }: RenderProps) {
  const unit = unitOf(data);
  const currency = currencyOf(data);
  const stages = data.metrics.map((m) => ({ name: m.display_name, value: figure(orgSeries(m), "actual") ?? 0, code: m.metric_code }));
  const top = stages[0]?.value ?? 0;
  const option = {
    tooltip: { trigger: "item", valueFormatter: (v: unknown) => (typeof v === "number" ? formatFigure(v, unit, currency) : "–") },
    series: [{ type: "funnel", sort: "none", top: 8, bottom: 8, left: 8, right: 8, gap: 2, minSize: "14%", label: { show: true, position: "inside", formatter: (p: { name: string }) => p.name, color: "#ffffff" }, data: stages.map((s) => ({ name: s.name, value: s.value })) }],
  };
  const table = (
    <table className="kg-table">
      <caption className="sr-only">{data.title}: stages and drop-off</caption>
      <thead><tr><th scope="col">Stage</th><th scope="col" className="is-num">Value</th><th scope="col" className="is-num">Of first</th></tr></thead>
      <tbody>
        {stages.map((s) => (
          <tr key={s.code}>
            <th scope="row">{s.name}</th>
            <td className="is-num">{formatFigure(s.value, unit, currency)}</td>
            <td className="is-num">{top > 0 ? `${((s.value / top) * 100).toFixed(0)}%` : "–"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
  const label = `${data.title}: funnel of ${stages.length} stages from ${formatFigure(top, unit, currency)}.`;
  return <ExecChart title={data.title} option={option} ariaLabel={label} table={table} />;
}

// ── dispatch ───────────────────────────────────────────────────────────────

const RENDERERS: Record<WidgetData["widget_type"], (p: RenderProps) => JSX.Element> = {
  kpi_card: KpiCard,
  bullet: Bullet,
  gauge: Gauge,
  bar: Bar,
  line: Line,
  pie: Pie,
  ranked_list: Ranked,
  table: Grid,
  funnel: Funnel,
};

/** Draw a widget's figures by its type. */
export function WidgetBody(props: RenderProps): JSX.Element {
  const Renderer = RENDERERS[props.data.widget_type];
  return <Renderer {...props} />;
}

// used for deciding decimals in a couple of callers/tests
export { decimalsFor };
