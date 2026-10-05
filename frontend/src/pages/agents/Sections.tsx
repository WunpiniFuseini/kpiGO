import { useState } from "react";

import type { Input, Output } from "../../api/actions";
import { useQuery } from "../../api/useAction";
import { EmptyState, ErrorPanel, Loading, SelectField, Skeleton } from "../../components";
import { TrendChart } from "../../components/TrendChart";
import { formatValue } from "../../lib/format";

export type Preset = Output<"agent.preset">;
export type PresetSection = Preset["sections"][number];
export type Trend = Output<"agent.trend">;
export type Heatmap = Output<"agent.heatmap">;
export type Distribution = Output<"agent.distribution">;
type Product = Input<"agent.trend">["product"];
type MetricKey = NonNullable<Trend["metric"]>;

const RAG_TEXT: Record<string, string> = { green: "On track", amber: "Behind", red: "Off track" };
const RAG_TONE: Record<string, string> = { green: "up", amber: "warn", red: "down" };
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function n(value: string | null | undefined): number | null {
  if (value === null || value === undefined || value === "") return null;
  const v = Number(value);
  return Number.isFinite(v) ? v : null;
}

function fmt(key: MetricKey, currency: string | null, decimals?: number): (v: number) => string {
  return (v) => formatValue(v, { unit: key.unit ?? "count", decimals: decimals ?? key.decimal_places, currency: currency ?? "" });
}

/** "2 Jun", from an ISO day. */
export function shortDay(iso: string): string {
  const [, m, d] = iso.split("-").map(Number);
  return `${d} ${MONTHS[m - 1]}`;
}

/** One preset section's card: heading, metric choice, and its body. */
function SectionCard({ section, metric, onMetric, children }: { section: PresetSection; metric: string | undefined; onMetric: (code: string) => void; children: React.ReactNode }) {
  const id = `section-${section.key}`;
  return (
    <section className="kg-card" aria-labelledby={id}>
      <div className="kg-sechead">
        <div>
          <h2 id={id} className="kg-section">
            {section.title}
          </h2>
          <p className="kg-cap">{section.caption}</p>
        </div>
        <span className="kg-spacer" />
        {section.metric_options.length > 1 ? (
          <div style={{ width: 200 }}>
            <SelectField label="Metric" value={metric ?? section.metric?.key ?? ""} onChange={(e) => onMetric(e.target.value)} options={section.metric_options.map((o) => ({ value: o.key, label: o.display_name }))} />
          </div>
        ) : null}
      </div>
      {children}
    </section>
  );
}

function NoMetric({ section }: { section: PresetSection }) {
  return (
    <EmptyState kind="none" title={`Nothing to show in ${section.title}`}>
      None of the metrics in this module suits this view yet. An Admin binds metrics to the module in the Metric registry.
    </EmptyState>
  );
}

/** A preset section that reads one of the section actions. */
export function PresetSectionView({ product, section }: { product: Product; section: PresetSection }) {
  const [metric, setMetric] = useState<string | undefined>(undefined);
  return (
    <SectionCard section={section} metric={metric} onMetric={setMetric}>
      {!section.metric ? (
        <NoMetric section={section} />
      ) : section.kind === "trend" ? (
        <TrendBody product={product} metric={metric ?? section.metric.key} title={section.title} />
      ) : section.kind === "heatmap" ? (
        <HeatmapBody product={product} metric={metric ?? section.metric.key} />
      ) : (
        <DistributionBody product={product} metric={metric ?? section.metric.key} />
      )}
    </SectionCard>
  );
}

function TrendBody({ product, metric, title }: { product: Product; metric: string; title: string }) {
  const [data, reload] = useQuery("agent.trend", { product, metric_code: metric });
  if (data.status === "loading") return <Loading label={`Loading ${title}`}><Skeleton height={220} /></Loading>;
  if (data.status === "error") return <ErrorPanel error={data.error} retry={reload} what={title} />;
  return <TrendView trend={data.data} title={title} />;
}

function HeatmapBody({ product, metric }: { product: Product; metric: string }) {
  const [data, reload] = useQuery("agent.heatmap", { product, metric_code: metric });
  if (data.status === "loading") return <Loading label="Loading the heatmap"><Skeleton height={220} /></Loading>;
  if (data.status === "error") return <ErrorPanel error={data.error} retry={reload} what="The heatmap" />;
  return <HeatmapView heatmap={data.data} />;
}

function DistributionBody({ product, metric }: { product: Product; metric: string }) {
  const [data, reload] = useQuery("agent.distribution", { product, metric_code: metric });
  if (data.status === "loading") return <Loading label="Loading the distribution"><Skeleton height={180} /></Loading>;
  if (data.status === "error") return <ErrorPanel error={data.error} retry={reload} what="The distribution" />;
  return <DistributionView distribution={data.data} />;
}

function MixedCurrency() {
  return (
    <EmptyState kind="none" title="Agents' targets are in different currencies">
      These figures cannot be added up without a common currency. Narrow the view to agents who share one.
    </EmptyState>
  );
}

/** Each day to date: the running total against where the targets expect it, or the daily mean. */
export function TrendView({ trend, title }: { trend: Trend; title: string }) {
  const metric = trend.metric;
  if (!metric) return null;
  if (trend.mixed_currency) return <MixedCurrency />;
  if (!trend.reported) {
    return (
      <EmptyState kind="none" title={`Nothing reported on ${metric.display_name} yet`}>
        No agent in view has a figure this {trend.window.kind} to {shortDay(trend.window.as_of)}. Days appear as the daily feed loads them.
      </EmptyState>
    );
  }
  const format = fmt(metric, trend.currency_code);
  const last = trend.points[trend.points.length - 1];
  const points = trend.points.map((p) => ({ label: shortDay(p.day), value: n(trend.additive ? p.cumulative : p.value) }));
  const target = trend.additive ? null : n(last?.target ?? null);
  const cumulative = n(last?.cumulative ?? null);
  const expected = n(last?.expected ?? null);
  return (
    <div className="kg-stack">
      {trend.additive && cumulative !== null ? (
        <p className="kg-cap" role="status">
          {format(cumulative)} to date{expected !== null ? ` against ${format(expected)} the targets expect by ${shortDay(trend.window.as_of)}` : ", with no target to compare"}. {trend.reported} of {trend.agents} agents reported.
        </p>
      ) : (
        <p className="kg-cap">
          The mean of the agents who reported each day. {trend.reported} of {trend.agents} agents reported.
        </p>
      )}
      <TrendChart title={trend.additive ? `${metric.display_name}, running total` : `${title}: ${metric.display_name}`} points={points} target={target} format={format} />
    </div>
  );
}

/** Entities by day, each cell the day's figure against its target, its state in words. */
export function HeatmapView({ heatmap }: { heatmap: Heatmap }) {
  const metric = heatmap.metric;
  if (!metric) return null;
  if (heatmap.mixed_currency) return <MixedCurrency />;
  if (!heatmap.rows.length) {
    return (
      <EmptyState kind="none" title={`Nobody in view is measured on ${metric.display_name}`}>
        Choose another metric, or ask an Admin to assign this one to a profile.
      </EmptyState>
    );
  }
  const format = fmt(metric, heatmap.currency_code, Math.max(1, metric.decimal_places));
  const head = heatmap.level === "region" ? "Region" : heatmap.level === "branch" ? "Branch" : "Agent";
  return (
    <div className="kg-stack">
      <div className="kg-matrix" tabIndex={0} role="region" aria-label={`${metric.display_name} by day`}>
        <table className="kg-table kg-heat">
          <thead>
            <tr>
              <th scope="col">{head}</th>
              {heatmap.days.map((d, i) => (
                <th key={d} scope="col" className="is-num">
                  {shortDay(d)}
                  {heatmap.working[i] === false ? <small className="kg-cap"> day off</small> : null}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {heatmap.rows.map((r) => (
              <tr key={r.key}>
                <th scope="row">
                  {r.name}
                  <small className="kg-cap"> {r.agents === 1 ? "1 agent" : `${r.agents} agents`}</small>
                </th>
                {r.cells.map((c, i) => {
                  const v = n(c.value);
                  return (
                    <td key={heatmap.days[i]} className={`is-num${c.rag ? ` kg-heat--${RAG_TONE[c.rag]}` : ""}`}>
                      {v === null ? (
                        <span className="kg-cap">–</span>
                      ) : (
                        <>
                          <b>{format(v)}</b>
                          <small>{c.rag ? RAG_TEXT[c.rag] : "No target"}</small>
                        </>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="kg-cap">A dash is a day with nothing reported, not a zero.</p>
    </div>
  );
}

/** How agents spread on their figure to date, bin by bin. */
export function DistributionView({ distribution }: { distribution: Distribution }) {
  const metric = distribution.metric;
  if (!metric) return null;
  if (distribution.mixed_currency) return <MixedCurrency />;
  if (!distribution.bins.length) {
    return (
      <EmptyState kind="none" title={`Nothing reported on ${metric.display_name} yet`}>
        No agent in view has a figure this {distribution.window.kind} to date.
      </EmptyState>
    );
  }
  const format = fmt(metric, distribution.currency_code, Math.max(1, metric.decimal_places));
  const most = Math.max(...distribution.bins.map((b) => b.agents), 1);
  const target = n(distribution.target);
  const median = n(distribution.median);
  const unreported = distribution.agents - distribution.reported;
  return (
    <div className="kg-stack">
      <p className="kg-cap" role="status">
        {median !== null ? `Median ${format(median)}` : ""}
        {target !== null ? ` against a target of ${format(target)}` : ""}. {distribution.reported} of {distribution.agents} agents reported
        {unreported ? `; the other ${unreported === 1 ? "one is" : `${unreported} are`} not counted as zero` : ""}.
      </p>
      <div className="kg-table-wrap">
        <table className="kg-table kg-dist">
          <caption className="sr-only">Agents by {metric.display_name} to date</caption>
          <thead>
            <tr>
              <th scope="col">{metric.display_name}</th>
              <th scope="col">Agents</th>
              <th scope="col" className="is-num">
                On target
              </th>
            </tr>
          </thead>
          <tbody>
            {distribution.bins.map((b) => (
              <tr key={b.low}>
                <th scope="row">
                  {format(Number(b.low))} to {format(Number(b.high))}
                </th>
                <td>
                  <div className="kg-dist__bar">
                    <span aria-hidden="true" style={{ width: `${(b.agents / most) * 100}%` }} />
                    <b>{b.agents}</b>
                  </div>
                </td>
                <td className="is-num">{b.agents ? `${b.on_target} of ${b.agents}` : "–"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
