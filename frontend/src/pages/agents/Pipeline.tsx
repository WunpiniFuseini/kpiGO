import { useState } from "react";

import type { Input, Output } from "../../api/actions";
import { ApiError, invoke, isProposal } from "../../api/client";
import { useQuery } from "../../api/useAction";
import { AdminBadge, EmptyState, ErrorPanel, Loading, Notice, SelectField, Skeleton, TableSkeleton, TextField } from "../../components";
import { formatValue } from "../../lib/format";
import type { Drill } from "./Matrix";

export type AgentPipeline = Output<"agent.pipeline">;
export type PipelineStages = Output<"pipeline.stage.list">;
type Product = Input<"agent.pipeline">["product"];
type Cell = AgentPipeline["rows"][number]["cells"][number];
type Message = { tone: "info" | "neg"; text: string } | null;

function n(value: string | null | undefined): number | null {
  if (value === null || value === undefined || value === "") return null;
  const v = Number(value);
  return Number.isFinite(v) ? v : null;
}

function money(value: string | null, currency: string | null): string {
  const v = n(value);
  return v === null ? "–" : formatValue(v, { unit: "currency", currency: currency ?? "" });
}

function deals(value: string | null): string {
  const v = n(value);
  return v === null ? "–" : formatValue(v, { unit: "count" });
}

function change(value: string | null, format: (v: string) => string): string {
  const v = n(value);
  if (v === null) return "–";
  if (v === 0) return "unchanged";
  return `${v > 0 ? "up" : "down"} ${format(String(Math.abs(v)))}`;
}

/** Sales section: the pipeline by stage (Scope §8.2). */
export function PipelineSection({ product, title = "Pipeline", caption = "" }: { product: Product; title?: string; caption?: string }) {
  const [drill, setDrill] = useState<Drill>({ level: "region" });
  const [data, reload] = useQuery("agent.pipeline", { product, ...drill });
  return (
    <section className="kg-card" aria-labelledby="pipeline-heading">
      <div className="kg-sechead">
        <div>
          <h2 id="pipeline-heading" className="kg-section">
            {title}
          </h2>
          <p className="kg-cap">{caption}</p>
        </div>
      </div>
      {data.status === "loading" ? (
        <Loading label="Loading the pipeline">
          <Skeleton height={220} />
        </Loading>
      ) : data.status === "error" ? (
        <ErrorPanel error={data.error} retry={reload} what="The pipeline" />
      ) : (
        <PipelineView pipeline={data.data} onDrill={setDrill} />
      )}
    </section>
  );
}

function CellText({ cell }: { cell: Cell }) {
  if (cell.mixed_currency) return <span className="kg-cap">Mixed currencies</span>;
  if (!cell.reported) return <span className="kg-cap">Nothing in stage</span>;
  return (
    <div className="kg-mcell">
      {cell.value !== null ? <b>{money(cell.value, cell.currency_code)}</b> : null}
      {cell.count !== null ? <small>{deals(cell.count)} deals</small> : null}
    </div>
  );
}

/** The pipeline from data: what stories and tests render. */
export function PipelineView({ pipeline, onDrill }: { pipeline: AgentPipeline; onDrill?: (d: Drill) => void }) {
  if (!pipeline.stages.length) {
    return (
      <EmptyState kind="none" title="No pipeline stages yet">
        The pipeline reads what sits in each stage from the daily feed. Your data team adds one snapshot metric per stage (its value, its number of deals, or both, with aggregation latest) to the daily view they already
        expose, and an Admin orders them into stages under Pipeline stages.
      </EmptyState>
    );
  }
  const total = pipeline.total;
  if (!total || !total.cells.some((c) => c.reported)) {
    return (
      <EmptyState kind="none" title="Nothing in the pipeline yet">
        No agent in view has a pipeline snapshot this {pipeline.window.kind}. Stages fill in as the daily feed loads them.
      </EmptyState>
    );
  }
  const biggest = Math.max(...total.cells.map((c) => n(c.value) ?? n(c.count) ?? 0), 1);
  const crumbs = pipeline.breadcrumb;
  const crumbDrill = (i: number): Drill => {
    const c = crumbs[i];
    if (c.level === "all") return { level: "region" };
    if (c.level === "region") return { level: "branch", region_code: c.code ?? undefined };
    return { level: "rm", branch_code: c.code ?? undefined };
  };
  const drillTo = (r: AgentPipeline["rows"][number]): Drill | null =>
    r.drill_level === "branch" && r.region_code
      ? { level: "branch", region_code: r.region_code }
      : r.drill_level === "rm" && r.branch_code
        ? { level: "rm", region_code: r.region_code ?? undefined, branch_code: r.branch_code }
        : null;
  const rowHead = pipeline.level === "region" ? "Region" : pipeline.level === "branch" ? "Branch" : "Agent";
  return (
    <div className="kg-stack">
      <nav aria-label="Pipeline level">
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
      <div className="kg-table-wrap">
        <table className="kg-table kg-dist">
          <caption className="sr-only">Pipeline by stage</caption>
          <thead>
            <tr>
              <th scope="col">Stage</th>
              <th scope="col">In stage now</th>
              <th scope="col" className="is-num">
                From the stage before
              </th>
              <th scope="col" className="is-num">
                Since the start of the {pipeline.window.kind}
              </th>
            </tr>
          </thead>
          <tbody>
            {pipeline.stages.map((s, i) => {
              const c = total.cells[i];
              const size = n(c.value) ?? n(c.count) ?? 0;
              const conversion = n(c.conversion);
              return (
                <tr key={s.code}>
                  <th scope="row">{s.display_name}</th>
                  <td>
                    <div className="kg-dist__bar">
                      <span aria-hidden="true" style={{ width: `${(size / biggest) * 100}%` }} />
                      <CellText cell={c} />
                    </div>
                  </td>
                  <td className="is-num">{i === 0 ? "–" : conversion === null ? "Not comparable" : `${Math.round(conversion * 100)}%`}</td>
                  <td className="is-num">
                    {c.value_change !== null ? change(c.value_change, (v) => money(v, c.currency_code)) : change(c.count_change, (v) => `${deals(v)} deals`)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="kg-matrix" tabIndex={0} role="region" aria-label="Pipeline by stage and place">
        <table className="kg-table">
          <thead>
            <tr>
              <th scope="col">{rowHead}</th>
              {pipeline.stages.map((s) => (
                <th key={s.code} scope="col" className="is-num">
                  {s.display_name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {pipeline.rows.map((r) => {
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
                  {r.cells.map((c, i) => (
                    <td key={pipeline.stages[i].code} className="is-num">
                      <CellText cell={c} />
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="kg-cap">Each agent's latest snapshot in the {pipeline.window.kind}; agents with none add nothing, not zero.</p>
    </div>
  );
}

/** Quick settings: the stages, from snapshot metrics the daily feed carries. */
export function PipelineStagesQuick({ product }: { product: Product }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button type="button" className="kg-btn" aria-expanded={open} aria-controls="pipeline-stages-quick" onClick={() => setOpen(!open)}>
        Pipeline stages <AdminBadge />
      </button>
      {open ? (
        <section id="pipeline-stages-quick" className="kg-card" aria-label="Pipeline stages" style={{ marginTop: 10 }}>
          <StagesBody product={product} />
        </section>
      ) : null}
    </div>
  );
}

function StagesBody({ product }: { product: Product }) {
  const [data, reload] = useQuery("pipeline.stage.list", { product });
  if (data.status === "loading") {
    return (
      <Loading label="Loading pipeline stages">
        <TableSkeleton rows={3} columns={3} />
      </Loading>
    );
  }
  if (data.status === "error") return <ErrorPanel error={data.error} retry={reload} what="Pipeline stages" />;
  return <PipelineStagesView stages={data.data} onChanged={reload} />;
}

const NONE = "";

/** The stage list from data: what stories and tests render. */
export function PipelineStagesView({ stages, onChanged }: { stages: PipelineStages; onChanged?: () => void }) {
  const [message, setMessage] = useState<Message>(null);
  const [busy, setBusy] = useState(false);
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [valueMetric, setValueMetric] = useState(NONE);
  const [countMetric, setCountMetric] = useState(NONE);
  const product = stages.product as Product;
  const label = (c: string | null) => (c ? (stages.candidates.find((m) => m.metric_code === c)?.display_name ?? c) : "–");

  async function act(run: () => Promise<unknown>, done: string) {
    setBusy(true);
    setMessage(null);
    try {
      const out = await run();
      setMessage({ tone: "info", text: isProposal(out) ? out.message : done });
      onChanged?.();
    } catch (e) {
      setMessage({ tone: "neg", text: e instanceof ApiError ? e.message : "That change did not go through." });
    } finally {
      setBusy(false);
    }
  }

  if (!stages.candidates.length && !stages.stages.length) {
    return (
      <EmptyState kind="none" title="No snapshot metrics to build stages from">
        A stage reads a metric with aggregation latest, bound to this module, that the daily feed carries: for example pipeline_proposal_value and pipeline_proposal_count. Register them in the Metric registry and add them to
        the daily view.
      </EmptyState>
    );
  }
  const options = [{ value: NONE, label: "None" }, ...stages.candidates.map((m) => ({ value: m.metric_code, label: m.display_name }))];
  const counts = [{ value: NONE, label: "None" }, ...stages.candidates.filter((m) => m.unit === "count").map((m) => ({ value: m.metric_code, label: m.display_name }))];
  return (
    <div className="kg-stack">
      {stages.stages.length ? (
        <div className="kg-table-wrap">
          <table className="kg-table">
            <caption className="sr-only">Pipeline stages in order</caption>
            <thead>
              <tr>
                <th scope="col">Stage</th>
                <th scope="col">Value from</th>
                <th scope="col">Deals from</th>
                <th scope="col">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {stages.stages.map((s) => (
                <tr key={s.code}>
                  <th scope="row">{s.display_name}</th>
                  <td>{label(s.value_metric_code)}</td>
                  <td>{label(s.count_metric_code)}</td>
                  <td className="is-num">
                    <button
                      type="button"
                      className="kg-btn"
                      disabled={busy}
                      aria-label={`Remove ${s.display_name}`}
                      onClick={() => void act(() => invoke("pipeline.stage.remove", { product, code: s.code }, { allowProposal: true }), `${s.display_name} is out of the funnel.`)}
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="kg-cap">No stages yet. Add them in funnel order, first stage first.</p>
      )}
      <form
        aria-label="Add a pipeline stage"
        style={{ display: "flex", gap: 12, alignItems: "flex-end", flexWrap: "wrap" }}
        onSubmit={(e) => {
          e.preventDefault();
          void act(
            () =>
              invoke(
                "pipeline.stage.set",
                {
                  product,
                  code: code.trim(),
                  display_name: name.trim(),
                  ...(valueMetric ? { value_metric_code: valueMetric } : {}),
                  ...(countMetric ? { count_metric_code: countMetric } : {}),
                },
                { allowProposal: true },
              ),
            "Stage saved.",
          ).then(() => {
            setCode("");
            setName("");
          });
        }}
      >
        <div style={{ width: 120 }}>
          <TextField label="Code" value={code} onChange={(e) => setCode(e.target.value)} />
        </div>
        <div style={{ width: 150 }}>
          <TextField label="Name" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div style={{ width: 190 }}>
          <SelectField label="Value from" value={valueMetric} onChange={(e) => setValueMetric(e.target.value)} options={options} />
        </div>
        <div style={{ width: 170 }}>
          <SelectField label="Deals from" value={countMetric} onChange={(e) => setCountMetric(e.target.value)} options={counts} />
        </div>
        <button type="submit" className="kg-btn kg-btn--primary" disabled={busy || !code.trim() || !name.trim() || (!valueMetric && !countMetric)}>
          Save stage
        </button>
      </form>
      {message ? (
        <Notice tone={message.tone} role={message.tone === "neg" ? "alert" : "status"}>
          {message.text}
        </Notice>
      ) : null}
    </div>
  );
}
