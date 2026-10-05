import { useState } from "react";

import { invoke } from "../../api/client";
import { CheckboxGroup, Chip, Loading, SelectField, Skeleton, TextField } from "../../components";
import type { QueryState } from "../../api/useAction";
import { formatDate } from "../../lib/format";
import {
  METRIC_PRODUCTS,
  RESULT_KINDS,
  type Campaign,
  type MetricProduct,
  type PublishedMetric,
  type PublishedMetrics,
  type ResultKind,
} from "./model";

type Act = (run: () => Promise<unknown>, done: string, proposed?: string) => Promise<boolean>;

const productLabel = (p: string) => METRIC_PRODUCTS.find((x) => x.value === p)?.label ?? p;
const kindLabel = (k: string) => RESULT_KINDS.find((x) => x.value === k)?.label ?? k;

/**
 * Publishing a campaign result into the registry as a metric (PRD CM-19, App Flow §5.2 "Publish").
 * Shows what the campaign has published, with the lineage, and — for a manager — a form to
 * publish another result or withdraw one.
 */
export function PublishPanel({ campaign, published, canManage, busy, act }: { campaign: Campaign; published: QueryState<PublishedMetrics>; canManage: boolean; busy: boolean; act: Act }) {
  if (published.status === "loading") {
    return (
      <Loading label="Loading the published metrics">
        <Skeleton height={72} />
      </Loading>
    );
  }
  if (published.status === "error") return <p className="kg-cap">The published metrics could not be loaded just now.</p>;
  const rows = published.data.published;
  const active = rows.filter((r) => r.status === "active");
  const takenKinds = new Set(active.map((r) => r.result_kind));
  return (
    <section className="kg-card kg-stack" aria-labelledby="publish">
      <h2 id="publish" className="kg-section">
        Published as metrics
      </h2>
      <p className="kg-cap">A campaign result can feed another module as a registry metric. Publishing is explicit, and the metric keeps a link back to this campaign.</p>
      {rows.length ? (
        <ul className="kg-stack" style={{ listStyle: "none", padding: 0, margin: 0 }} aria-label="Published metrics">
          {rows.map((r) => (
            <PublishedRow key={r.published_id} row={r} canManage={canManage} busy={busy} act={act} />
          ))}
        </ul>
      ) : (
        <p className="kg-cap">Nothing from this campaign is published as a metric yet.</p>
      )}
      {canManage ? <PublishForm campaign={campaign} takenKinds={takenKinds} busy={busy} act={act} /> : null}
    </section>
  );
}

function PublishedRow({ row: r, canManage, busy, act }: { row: PublishedMetric; canManage: boolean; busy: boolean; act: Act }) {
  const withdrawn = r.status === "withdrawn";
  return (
    <li className="kg-row" style={{ alignItems: "flex-start" }}>
      <div className="kg-stack" style={{ gap: 4 }}>
        <div className="kg-row" style={{ gap: 8 }}>
          <b>{r.display_name}</b>
          <Chip tone={withdrawn ? "flat" : "info"}>{withdrawn ? "Withdrawn" : "Live"}</Chip>
          <Chip>Lineage · {r.campaign_code}</Chip>
        </div>
        <p className="kg-cap">
          {kindLabel(r.result_kind)} · {r.products.length ? r.products.map(productLabel).join(", ") : "no products"} · code {r.metric_code}
          {withdrawn && r.withdrawn_at ? ` · withdrawn ${formatDate(r.withdrawn_at)}` : ""}
        </p>
      </div>
      {canManage && !withdrawn ? (
        <button type="button" className="kg-btn" disabled={busy} onClick={() => void act(() => invoke("campaign.metric.withdraw", { published_id: r.published_id }, { allowProposal: true }), `${r.display_name} is withdrawn.`, `Withdrawing ${r.display_name} needs approval; the request has gone to an approver.`)}>
          Withdraw
        </button>
      ) : null}
    </li>
  );
}

function PublishForm({ campaign, takenKinds, busy, act }: { campaign: Campaign; takenKinds: Set<string>; busy: boolean; act: Act }) {
  const free = RESULT_KINDS.filter((k) => !takenKinds.has(k.value));
  const [kind, setKind] = useState<ResultKind | "">(free[0]?.value ?? "");
  const [products, setProducts] = useState<MetricProduct[]>(["scorecards"]);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  if (!free.length) return <p className="kg-cap">Every result is already published. Withdraw one to change what it publishes.</p>;
  const hint = RESULT_KINDS.find((k) => k.value === kind)?.hint;
  const submit = () => {
    if (!kind) return setError("Choose a result to publish.");
    if (!products.length) return setError("Choose at least one module for the metric.");
    setError(null);
    void act(
      () => invoke("campaign.metric.publish", { campaign_id: campaign.campaign_id, result_kind: kind, products, display_name: name.trim() || undefined }, { allowProposal: true }),
      `${kindLabel(kind)} is published as a metric.`,
      `Publishing ${kindLabel(kind)} needs approval; the request has gone to an approver.`,
    ).then((ok) => {
      if (ok) {
        setName("");
        const next = RESULT_KINDS.filter((k) => !takenKinds.has(k.value) && k.value !== kind);
        setKind(next[0]?.value ?? "");
      }
    });
  };
  return (
    <div className="kg-stack kg-card" aria-label="Publish a result">
      <h3 className="kg-eyebrow">Publish a result</h3>
      <SelectField label="Result" hint={hint} value={kind} options={free.map((k) => ({ value: k.value, label: k.label }))} onChange={(e) => setKind(e.target.value as ResultKind)} />
      <CheckboxGroup legend="Modules the metric feeds" options={METRIC_PRODUCTS.map((p) => ({ value: p.value, label: p.label }))} value={products} onChange={(next) => setProducts(next as MetricProduct[])} />
      <TextField label="Name (optional)" hint="Defaults to the result and the campaign code." value={name} onChange={(e) => setName(e.target.value)} placeholder={`${kindLabel(kind || "attributed_value")}: ${campaign.code}`} />
      {error ? <p className="kg-field-error">{error}</p> : null}
      <div>
        <button type="button" className="kg-btn kg-btn--primary" disabled={busy} onClick={submit}>
          Publish
        </button>
      </div>
    </div>
  );
}
