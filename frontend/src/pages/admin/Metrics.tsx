import { useState, type FormEvent } from "react";

import type { Input, Output } from "../../api/actions";
import { ApiError, invoke, isProposal } from "../../api/client";
import { useQuery } from "../../api/useAction";
import {
  CheckboxGroup,
  Chip,
  DataTable,
  EmptyState,
  ErrorPanel,
  Loading,
  Notice,
  SelectField,
  TableSkeleton,
  TextField,
} from "../../components";
import { Page } from "../../shell/AppShell";
import { useMe } from "../../session/Session";

type Metric = Output<"metric.list">["metrics"][number];
type RegisterInput = Input<"metric.register">;
type Similar = { display_name: string; similarity: number; metrics: { metric_code: string }[] };

const PRODUCTS: { value: RegisterInput["products"][number]; label: string }[] = [
  { value: "scorecards", label: "Scorecards" },
  { value: "agent_sales", label: "Agent Performance: sales" },
  { value: "agent_service", label: "Agent Performance: service" },
  { value: "campaign", label: "Campaign Manager" },
  { value: "executive", label: "Executive" },
];
const DIRECTIONS = [
  { value: "higher_is_better", label: "Higher is better" },
  { value: "lower_is_better", label: "Lower is better" },
];
const AGGREGATIONS = ["sum", "average", "latest", "count", "ratio"].map((v) => ({ value: v, label: v[0].toUpperCase() + v.slice(1) }));
const UNITS = ["currency", "count", "percent", "days", "hours", "score"].map((v) => ({ value: v, label: v[0].toUpperCase() + v.slice(1) }));

const STATUS_TONE = { active: "up", draft: "info", inactive: "flat", deprecated: "flat" } as const;

/** Administer → Metric registry: register once, read on every surface (R0 exit 1). */
export function MetricsPage() {
  const me = useMe();
  const canManage = me.permissions.includes("metric.manage");
  const [registering, setRegistering] = useState(false);
  const [list, reload] = useQuery("metric.list", {});
  return (
    <Page
      title="Metric registry"
      actions={
        canManage ? (
          <button type="button" className="kg-btn kg-btn--primary" onClick={() => setRegistering(true)} aria-expanded={registering}>
            Register metric
          </button>
        ) : null
      }
    >
      {registering ? <RegisterForm onClose={() => setRegistering(false)} onRegistered={reload} /> : null}
      <section className="kg-card">
        {list.status === "loading" ? (
          <Loading label="Loading metrics">
            <TableSkeleton rows={6} columns={5} />
          </Loading>
        ) : list.status === "error" ? (
          <ErrorPanel error={list.error} retry={reload} what="Metrics" />
        ) : (
          <MetricTable metrics={list.data.metrics} asOf={list.data.as_of} canManage={canManage} />
        )}
      </section>
    </Page>
  );
}

export function MetricTable({ metrics, asOf, canManage }: { metrics: Metric[]; asOf: string; canManage: boolean }) {
  return (
    <DataTable
      caption={`Metrics in force on ${asOf}`}
      columns={[
        {
          key: "name",
          header: "Metric",
          render: (m) => (
            <>
              {m.display_name}
              <div className="kg-cap">
                {m.unit} · {m.aggregation} · {m.direction === "higher_is_better" ? "higher is better" : "lower is better"}
              </div>
            </>
          ),
        },
        { key: "code", header: "Code", render: (m) => <span className="kg-mono">{m.metric_code}</span> },
        { key: "products", header: "Products", render: (m) => (m.bindings ?? []).filter((b) => b.is_active).map((b) => b.product).join(", ") || "–" },
        { key: "collection", header: "Collected by", render: (m) => (m.collection_method === "manual_input" ? "Manual input" : "Feed") },
        { key: "status", header: "Status", render: (m) => <Chip tone={STATUS_TONE[m.status as keyof typeof STATUS_TONE] ?? "flat"}>{m.status}</Chip> },
      ]}
      rows={metrics}
      rowKey={(m) => m.metric_id}
      empty={
        <EmptyState kind="none" title="No metrics are registered yet">
          {canManage
            ? "Register the first one with Register metric, or load a starter pack."
            : "A Metric Owner registers metrics here. Scorecards and dashboards stay empty until they do."}
        </EmptyState>
      }
    />
  );
}

function RegisterForm({ onClose, onRegistered }: { onClose: () => void; onRegistered: () => void }) {
  const [form, setForm] = useState<RegisterInput>({
    display_name: "",
    metric_code: null,
    direction: "higher_is_better",
    aggregation: "sum",
    unit: "count",
    decimal_places: 0,
    collection_method: "feed",
    products: ["scorecards"],
    computation_note: "",
  });
  const [similar, setSimilar] = useState<Similar[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);

  const register = async (acknowledge: boolean) => {
    setBusy(true);
    setError(null);
    try {
      const out = await invoke("metric.register", { ...form, acknowledge_similar: acknowledge }, { allowProposal: true });
      setDone(isProposal(out) ? out.message : `${out.family.display_name} is registered as ${out.metrics.map((m) => m.metric_code).join(", ")}.`);
      setSimilar(null);
      onRegistered();
    } catch (e) {
      if (e instanceof ApiError && e.code === "conflict" && e.detail && typeof e.detail === "object" && "similar" in e.detail) {
        setSimilar((e.detail as { similar: Similar[] }).similar);
      } else {
        setError(e instanceof ApiError ? e.message : "The metric could not be registered.");
      }
    } finally {
      setBusy(false);
    }
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    void register(false);
  };

  if (done) {
    return (
      <Notice title="Done." role="status">
        {done}{" "}
        <button type="button" className="kg-btn kg-btn--link" onClick={onClose}>
          Close
        </button>
      </Notice>
    );
  }

  return (
    <section className="kg-card" aria-labelledby="register-heading">
      <h2 id="register-heading" className="kg-section" style={{ marginBottom: 14 }}>
        Register a metric
      </h2>
      <form className="kg-form" onSubmit={submit} noValidate>
        <div className="kg-form-row">
          <TextField label="Name" required value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
          <TextField
            label="Code (optional)"
            hint="Lower_snake. Made from the name when left empty."
            value={form.metric_code ?? ""}
            onChange={(e) => setForm({ ...form, metric_code: e.target.value || null })}
          />
        </div>
        <div className="kg-form-row">
          <SelectField label="Direction" value={form.direction} options={DIRECTIONS} onChange={(e) => setForm({ ...form, direction: e.target.value as RegisterInput["direction"] })} />
          <SelectField label="Aggregation" value={form.aggregation} options={AGGREGATIONS} onChange={(e) => setForm({ ...form, aggregation: e.target.value as RegisterInput["aggregation"] })} />
          <SelectField label="Unit" value={form.unit} options={UNITS} onChange={(e) => setForm({ ...form, unit: e.target.value as RegisterInput["unit"] })} />
          <SelectField
            label="Collected by"
            value={form.collection_method ?? "feed"}
            options={[
              { value: "feed", label: "A data feed" },
              { value: "manual_input", label: "Manual input" },
            ]}
            onChange={(e) => setForm({ ...form, collection_method: e.target.value as RegisterInput["collection_method"] })}
          />
        </div>
        <CheckboxGroup
          legend="Used by"
          options={PRODUCTS}
          value={form.products}
          onChange={(products) => setForm({ ...form, products: products as RegisterInput["products"] })}
          error={form.products.length ? null : "Choose at least one product."}
        />
        <TextField
          label="How it is computed (optional)"
          value={form.computation_note ?? ""}
          onChange={(e) => setForm({ ...form, computation_note: e.target.value })}
        />
        {similar ? (
          <Notice tone="warn" title="Similar metrics already exist." role="alert">
            {similar.map((s) => `${s.display_name} (${s.metrics.map((m) => m.metric_code).join(", ")})`).join("; ")}. If this is a different
            metric, register it anyway.{" "}
            <button type="button" className="kg-btn kg-btn--link" onClick={() => register(true)} disabled={busy}>
              Register anyway
            </button>
          </Notice>
        ) : null}
        {error ? (
          <p role="alert" style={{ color: "var(--neg-ink)", fontSize: 13 }}>
            {error}
          </p>
        ) : null}
        <div style={{ display: "flex", gap: 8 }}>
          <button type="submit" className="kg-btn kg-btn--primary" disabled={busy || !form.display_name || !form.products.length}>
            {busy ? "Registering…" : "Register"}
          </button>
          <button type="button" className="kg-btn" onClick={onClose}>
            Cancel
          </button>
        </div>
      </form>
    </section>
  );
}
