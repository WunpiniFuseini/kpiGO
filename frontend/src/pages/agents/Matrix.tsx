import { useState } from "react";

import type { Input, Output } from "../../api/actions";
import { invoke } from "../../api/client";
import { useQuery } from "../../api/useAction";
import { EmptyState, ErrorPanel, Loading, SelectField, Skeleton } from "../../components";
import { formatValue } from "../../lib/format";

export type ProductMatrix = Output<"agent.matrix">;
type Product = Input<"agent.matrix">["product"];
type Level = NonNullable<Input<"agent.matrix">["level"]>;
type View = NonNullable<Input<"agent.matrix">["view"]>;
type Cell = ProductMatrix["rows"][number]["cells"][number];
type MetricKey = NonNullable<ProductMatrix["metric"]>;

/** Where the drill is: a level and the path that leads to it. */
export type Drill = { level: Level; region_code?: string; branch_code?: string };

const RAG_TEXT: Record<string, string> = { green: "On track", amber: "Behind", red: "Off track" };
const RAG_TONE: Record<string, string> = { green: "up", amber: "warn", red: "down" };

function n(value: string | null | undefined): number | null {
  if (value === null || value === undefined || value === "") return null;
  const v = Number(value);
  return Number.isFinite(v) ? v : null;
}

function figure(value: string | null, key: MetricKey, currency: string | null): string {
  const v = n(value);
  if (v === null) return "–";
  return formatValue(v, { unit: key.unit ?? "count", decimals: key.decimal_places, currency: currency ?? "" });
}

/** Agent Performance, section 2: the product-line matrix (App Flow §4.1, PRD AP-5, AP-9, AP-11). */
export function MatrixSection({ product, asOf }: { product: Product; asOf?: string }) {
  const [drill, setDrill] = useState<Drill>({ level: "region" });
  const [metric, setMetric] = useState<string | undefined>(undefined);
  const [view, setView] = useState<View | undefined>(undefined);
  const [data, reload] = useQuery("agent.matrix", {
    product,
    ...(asOf ? { as_of: asOf } : {}),
    ...drill,
    ...(metric ? { metric_code: metric } : {}),
    ...(view ? { view } : {}),
  });
  const chooseView = (next: View) => {
    setView(next);
    // The choice is the reader's own default next time; failing to save it changes nothing here.
    invoke("preference.set", { key: "agent.matrix.view", value: next }).catch(() => undefined);
  };
  return (
    <section className="kg-card" aria-labelledby="matrix-heading">
      <div className="kg-sechead">
        <div>
          <h2 id="matrix-heading" className="kg-section">
            Product lines
          </h2>
          <p className="kg-cap">Actual against the target expected by now, per line.</p>
        </div>
      </div>
      {data.status === "loading" ? (
        <Loading label="Loading the product-line matrix">
          <Skeleton height={240} />
        </Loading>
      ) : data.status === "error" ? (
        <ErrorPanel error={data.error} retry={reload} what="The product-line matrix" />
      ) : (
        <MatrixView
          matrix={data.data}
          onDrill={setDrill}
          onMetric={setMetric}
          onView={chooseView}
        />
      )}
    </section>
  );
}

function CellView({ cell, metric }: { cell: Cell; metric: MetricKey }) {
  if (cell.mixed_currency) return <span className="kg-cap">Mixed currencies</span>;
  if (cell.actual === null) return <span className="kg-cap">Not reported</span>;
  const achieved = n(cell.achieved);
  return (
    <div className="kg-mcell">
      <b>{figure(cell.actual, metric, cell.currency_code)}</b>
      <small>{cell.target === null ? "No target" : `of ${figure(cell.target, metric, cell.currency_code)}`}</small>
      {achieved !== null && cell.rag ? (
        <span className={`kg-chip kg-chip--${RAG_TONE[cell.rag]}`}>
          {Math.round(achieved * 100)}% · {RAG_TEXT[cell.rag]}
        </span>
      ) : null}
      {cell.reported < cell.agents ? (
        <small>
          {cell.reported} of {cell.agents} reported
        </small>
      ) : null}
    </div>
  );
}

/** The matrix from data: what stories and tests render. */
export function MatrixView({
  matrix,
  onDrill,
  onMetric,
  onView,
}: {
  matrix: ProductMatrix;
  onDrill?: (d: Drill) => void;
  onMetric?: (code: string) => void;
  onView?: (v: View) => void;
}) {
  const metric = matrix.metric;
  const crumbs = matrix.breadcrumb;
  const crumbDrill = (i: number): Drill => {
    const c = crumbs[i];
    if (c.level === "all") return { level: "region" };
    if (c.level === "region") return { level: "branch", region_code: c.code ?? undefined };
    return { level: "rm", branch_code: c.code ?? undefined };
  };
  const controls = (
    <div style={{ display: "flex", gap: 12, alignItems: "flex-end", flexWrap: "wrap" }}>
      <nav aria-label="Matrix level">
        <ol className="kg-crumbs">
          {crumbs.map((c, i) =>
            i === crumbs.length - 1 ? (
              <li key={i} aria-current="page">
                {c.name}
              </li>
            ) : (
              <li key={i}>
                <button type="button" className="kg-linkbtn" onClick={() => onDrill?.(crumbDrill(i))}>
                  {c.name}
                </button>
              </li>
            ),
          )}
        </ol>
      </nav>
      <span className="kg-spacer" />
      {matrix.metric_options.length > 1 ? (
        <div style={{ width: 180 }}>
          <SelectField
            label="Metric"
            value={metric?.key ?? ""}
            onChange={(e) => onMetric?.(e.target.value)}
            options={matrix.metric_options.map((o) => ({ value: o.key, label: o.display_name }))}
          />
        </div>
      ) : null}
      {!matrix.no_lines ? (
        <div className="kg-seg" role="group" aria-label="Columns">
          {(["expanded", "grouped"] as const).map((v) => (
            <button key={v} type="button" aria-pressed={matrix.view === v} onClick={() => onView?.(v)}>
              {v === "expanded" ? "Lines" : "Groups"}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );

  if (!metric) {
    return (
      <EmptyState kind="none" title="No metric adds up across agents">
        The matrix sums agents' figures, so it shows only sum and count metrics. None of this module's profiles carries one. An Admin binds one in the Metric registry.
      </EmptyState>
    );
  }
  if (!matrix.rows.length) {
    return (
      <div className="kg-stack">
        {controls}
        <EmptyState kind="none" title={`Nobody here is measured on ${metric.display_name}`}>
          None of these agents' profiles carries {metric.display_name}. Choose another metric.
        </EmptyState>
      </div>
    );
  }
  const drillTo = (r: ProductMatrix["rows"][number]): Drill | null =>
    r.drill_level === "branch" && r.region_code
      ? { level: "branch", region_code: r.region_code }
      : r.drill_level === "rm" && r.branch_code
        ? { level: "rm", region_code: r.region_code ?? undefined, branch_code: r.branch_code }
        : null;
  const rowHead = matrix.level === "region" ? "Region" : matrix.level === "branch" ? "Branch" : "Agent";
  return (
    <div className="kg-stack">
      {controls}
      {matrix.no_lines ? (
        <p className="kg-cap" role="status">
          No product line is switched on, so only All products shows. An Admin switches lines on under Product lines.
        </p>
      ) : null}
      <div className="kg-matrix" tabIndex={0} role="region" aria-label={`${metric.display_name} by product line`}>
        <table className="kg-table">
          <thead>
            <tr>
              <th scope="col">{rowHead}</th>
              {matrix.columns.map((c) => (
                <th key={c.key} scope="col" className="is-num">
                  {c.name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {matrix.rows.map((r) => {
              const to = drillTo(r);
              return (
                <tr key={r.key}>
                  <th scope="row">
                    {to ? (
                      <button type="button" className="kg-linkbtn" onClick={() => onDrill?.(to)}>
                        {r.name}
                      </button>
                    ) : (
                      r.name
                    )}
                    <small className="kg-cap"> {r.agents === 1 ? "1 agent" : `${r.agents} agents`}</small>
                  </th>
                  {r.cells.map((cell, i) => (
                    <td key={matrix.columns[i].key} className="is-num">
                      <CellView cell={cell} metric={metric} />
                    </td>
                  ))}
                </tr>
              );
            })}
            {matrix.total && matrix.rows.length > 1 ? (
              <tr className="kg-total">
                <th scope="row">Total</th>
                {matrix.total.cells.map((cell, i) => (
                  <td key={matrix.columns[i].key} className="is-num">
                    <CellView cell={cell} metric={metric} />
                  </td>
                ))}
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
      <p className="kg-cap">
        Agents who reported nothing on a line add neither figure nor target to it.
        {matrix.view === "grouped" ? " A group's % is from its lines' totals, not an average of their percentages." : ""}
      </p>
    </div>
  );
}
