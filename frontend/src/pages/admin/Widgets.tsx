/**
 * Administer → Widgets (App Flow 6.1): the one place the Executive dashboard is
 * composed. An Admin places a widget end to end — key, type, metrics, breakdown
 * and series — and the form greys out whatever the chosen type cannot draw, so a
 * combination the server would refuse cannot be submitted. Thresholds get their
 * own override editor (maker-checker), and every widget keeps its version
 * history.
 */
import { type FormEvent, useMemo, useState } from "react";

import type { Output } from "../../api/actions";
import { ApiError, invoke, isProposal } from "../../api/client";
import { useQuery } from "../../api/useAction";
import { Chip, EmptyState, ErrorPanel, Loading, Notice, SelectField, TableSkeleton, TextField } from "../../components";
import { formatDateTime } from "../../lib/format";
import { Page } from "../../shell/AppShell";
import { useMe } from "../../session/Session";
import {
  ALL_SERIES,
  type MetricRow,
  type SeriesType,
  type TypeSpec,
  type WidgetDraft,
  dimensionRule,
  effectiveSeries,
  executiveMetrics,
  metricBlocked,
  typeByKey,
  validateDraft,
} from "./widgetForm";

type WidgetRow = Output<"widget.list">["widgets"][number];
type Dim = { value: string; label: string };

const FALLBACK_DIMENSIONS: Dim[] = [
  { value: "region", label: "Region" },
  { value: "branch", label: "Branch" },
  { value: "segment", label: "Segment" },
];

const STATE_TONE = { placed: "up", available: "info", removed: "flat" } as const;

type Mode =
  | { kind: "place"; widgetKey?: string }
  | { kind: "edit"; widget: WidgetRow }
  | { kind: "thresholds"; widget: WidgetRow }
  | { kind: "history"; widgetKey: string }
  | null;

export function WidgetsPage() {
  const me = useMe();
  const canManage = me.permissions.includes("widget.manage");
  const [includeRemoved, setIncludeRemoved] = useState(false);
  const [mode, setMode] = useState<Mode>(null);
  const [list, reload] = useQuery("widget.list", { include_removed: includeRemoved });
  const [metrics] = useQuery("metric.list", {});
  const [dims] = useQuery("dimension.list", {});

  const dimensions: Dim[] =
    dims.status === "ready"
      ? dims.data.dimensions.map((d) => ({ value: d.dimension_type, label: d.display_name }))
      : FALLBACK_DIMENSIONS;
  const metricRows = metrics.status === "ready" ? executiveMetrics(metrics.data.metrics) : [];

  const close = () => setMode(null);
  const onChanged = () => {
    reload();
    close();
  };

  return (
    <Page
      title="Widgets"
      actions={
        canManage ? (
          <button type="button" className="kg-btn kg-btn--primary" onClick={() => setMode({ kind: "place" })} aria-expanded={mode?.kind === "place"}>
            Place widget
          </button>
        ) : null
      }
    >
      {canManage && list.status === "ready" && mode?.kind === "place" ? (
        <WidgetForm types={list.data.types} metrics={metricRows} dimensions={dimensions} existingKeys={list.data.widgets.map((w) => w.widget_key)} prefillKey={mode.widgetKey} onClose={close} onSaved={onChanged} />
      ) : null}
      {canManage && list.status === "ready" && mode?.kind === "edit" ? (
        <WidgetForm types={list.data.types} metrics={metricRows} dimensions={dimensions} existingKeys={[]} existing={mode.widget} onClose={close} onSaved={onChanged} />
      ) : null}
      {canManage && list.status === "ready" && mode?.kind === "thresholds" ? (
        <ThresholdsForm types={list.data.types} widget={mode.widget} onClose={close} onSaved={onChanged} />
      ) : null}
      {mode?.kind === "history" ? <HistoryPanel widgetKey={mode.widgetKey} onClose={close} /> : null}

      <section className="kg-card">
        <div className="kg-row" style={{ marginBottom: 12 }}>
          <h2 className="kg-section">Widgets</h2>
          <label className="kg-check-inline">
            <input type="checkbox" checked={includeRemoved} onChange={(e) => setIncludeRemoved(e.target.checked)} /> Show removed
          </label>
        </div>
        {list.status === "loading" ? (
          <Loading label="Loading widgets">
            <TableSkeleton rows={5} columns={5} />
          </Loading>
        ) : list.status === "error" ? (
          <ErrorPanel error={list.error} retry={reload} what="Widgets" />
        ) : (
          <WidgetsTable
            widgets={list.data.widgets}
            types={list.data.types}
            canManage={canManage}
            onPlace={(key) => setMode({ kind: "place", widgetKey: key })}
            onEdit={(widget) => setMode({ kind: "edit", widget })}
            onThresholds={(widget) => setMode({ kind: "thresholds", widget })}
            onHistory={(key) => setMode({ kind: "history", widgetKey: key })}
            onChanged={reload}
          />
        )}
      </section>
    </Page>
  );
}

export function WidgetsTable({
  widgets,
  types,
  canManage,
  onPlace,
  onEdit,
  onThresholds,
  onHistory,
  onChanged,
}: {
  widgets: WidgetRow[];
  types: TypeSpec[];
  canManage: boolean;
  onPlace: (key: string) => void;
  onEdit: (w: WidgetRow) => void;
  onThresholds: (w: WidgetRow) => void;
  onHistory: (key: string) => void;
  onChanged: () => void;
}) {
  const byType = useMemo(() => typeByKey(types), [types]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (widgets.length === 0) {
    return (
      <EmptyState kind="none" title="No widgets yet">
        {canManage
          ? "Place the first with Place widget. A metric bound to Executive, a type, and you have a dashboard."
          : "An Admin composes the Executive dashboard here."}
      </EmptyState>
    );
  }

  const remove = async (w: WidgetRow) => {
    setBusy(w.widget_key);
    setError(null);
    try {
      await invoke("widget.remove", { widget_key: w.widget_key, expected_version: w.version }, { allowProposal: true });
      onChanged();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The widget could not be removed.");
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      {error ? <Notice tone="neg" title="That did not go through." role="alert">{error}</Notice> : null}
      <div className="kg-table-wrap">
        <table className="kg-table">
          <caption className="sr-only">Widgets on the Executive dashboard</caption>
          <thead>
            <tr>
              <th scope="col">Widget</th>
              <th scope="col">Type</th>
              <th scope="col">Metrics</th>
              <th scope="col">Breakdown</th>
              <th scope="col">Status</th>
              {canManage ? <th scope="col">Actions</th> : null}
            </tr>
          </thead>
          <tbody>
            {widgets.map((w) => {
              const spec = w.widget_type ? byType.get(w.widget_type) : undefined;
              return (
                <tr key={w.widget_key}>
                  <th scope="row">
                    {w.title || w.widget_key}
                    <div className="kg-cap kg-mono">{w.widget_key}</div>
                  </th>
                  <td>{spec?.label ?? (w.widget_type || "—")}</td>
                  <td>{w.metrics.map((m) => m.metric_code).join(", ") || "—"}</td>
                  <td>{w.dimension || "—"}</td>
                  <td><Chip tone={STATE_TONE[w.state]}>{w.state}</Chip></td>
                  {canManage ? (
                    <td>
                      <div className="kg-actions">
                        {w.state === "available" ? (
                          <button type="button" className="kg-btn kg-btn--link" onClick={() => onPlace(w.widget_key)}>Place</button>
                        ) : null}
                        {w.state === "placed" ? (
                          <>
                            <button type="button" className="kg-btn kg-btn--link" onClick={() => onEdit(w)}>Edit</button>
                            {spec?.thresholds ? <button type="button" className="kg-btn kg-btn--link" onClick={() => onThresholds(w)}>Thresholds</button> : null}
                            <button type="button" className="kg-btn kg-btn--link" onClick={() => void remove(w)} disabled={busy === w.widget_key}>
                              {busy === w.widget_key ? "Removing…" : "Remove"}
                            </button>
                          </>
                        ) : null}
                        <button type="button" className="kg-btn kg-btn--link" onClick={() => onHistory(w.widget_key)}>History</button>
                      </div>
                    </td>
                  ) : null}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

export function WidgetForm({
  types,
  metrics,
  dimensions,
  existing,
  prefillKey,
  existingKeys,
  onClose,
  onSaved,
}: {
  types: TypeSpec[];
  metrics: MetricRow[];
  dimensions: Dim[];
  existing?: WidgetRow;
  prefillKey?: string;
  existingKeys: string[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const byCode = useMemo(() => new Map(metrics.map((m) => [m.metric_code, m])), [metrics]);
  const [draft, setDraft] = useState<WidgetDraft>(() => ({
    widget_key: existing?.widget_key ?? prefillKey ?? "",
    title: existing?.title ?? "",
    widget_type: existing?.widget_type ?? "kpi_card",
    metrics: existing ? existing.metrics.map((m) => m.metric_code) : [],
    dimension: existing?.dimension ?? "",
    series: (existing?.series ?? ["actual", "target"]) as SeriesType[],
  }));
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const spec = typeByKey(types).get(draft.widget_type)!;
  const rule = dimensionRule(spec, draft.metrics.length);
  const chosen = draft.metrics.map((c) => byCode.get(c)).filter((m): m is MetricRow => Boolean(m));
  const problems = validateDraft(draft, spec, byCode);
  const keyTaken = !existing && existingKeys.includes(draft.widget_key) && draft.widget_key !== prefillKey;

  const toggleMetric = (code: string) => {
    setDraft((d) => ({ ...d, metrics: d.metrics.includes(code) ? d.metrics.filter((c) => c !== code) : [...d.metrics, code] }));
  };
  const toggleSeries = (s: SeriesType) => {
    if (s === "actual") return;
    setDraft((d) => ({ ...d, series: d.series.includes(s) ? d.series.filter((x) => x !== s) : [...d.series, s] }));
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const series = effectiveSeries(spec, draft.series);
    const metricsIn = draft.metrics.map((metric_code) => ({ metric_code }));
    try {
      if (existing) {
        const out = await invoke(
          "widget.update",
          { widget_key: draft.widget_key, expected_version: existing.version, title: draft.title || null, widget_type: draft.widget_type, metrics: metricsIn, dimension: draft.dimension || null, clear_dimension: !draft.dimension, series },
          { allowProposal: true },
        );
        setDone(isProposal(out) ? out.message : `${out.title} is updated.`);
      } else {
        const out = await invoke(
          "widget.place",
          { widget_key: draft.widget_key, title: draft.title, widget_type: draft.widget_type, metrics: metricsIn, dimension: draft.dimension || null, series },
          { allowProposal: true },
        );
        setDone(isProposal(out) ? out.message : `${out.title} is on the dashboard.`);
      }
      onSaved();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The widget could not be saved.");
    } finally {
      setBusy(false);
    }
  };

  if (done) {
    return (
      <Notice title="Done." role="status">
        {done} <button type="button" className="kg-btn kg-btn--link" onClick={onClose}>Close</button>
      </Notice>
    );
  }

  const atMax = draft.metrics.length >= spec.max_metrics;
  return (
    <section className="kg-card" aria-labelledby="widget-form-heading">
      <h2 id="widget-form-heading" className="kg-section" style={{ marginBottom: 14 }}>
        {existing ? `Edit ${existing.title || existing.widget_key}` : "Place a widget"}
      </h2>
      <form className="kg-form" onSubmit={(e) => void submit(e)} noValidate>
        <div className="kg-form-row">
          <TextField
            label="Widget key"
            hint="Lower_snake. The feed and the dashboard share it."
            value={draft.widget_key}
            disabled={Boolean(existing) || Boolean(prefillKey)}
            error={keyTaken ? "A widget already uses this key." : null}
            onChange={(e) => setDraft({ ...draft, widget_key: e.target.value })}
          />
          <TextField label="Title" hint="Optional for one metric; required for several." value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} />
          <SelectField
            label="Type"
            value={draft.widget_type}
            options={types.map((t) => ({ value: t.type, label: t.label }))}
            onChange={(e) => setDraft({ ...draft, widget_type: e.target.value as TypeSpec["type"] })}
          />
        </div>
        <p className="kg-cap">{spec.renders}.</p>

        <fieldset className="kg-fieldset">
          <legend>Metrics ({spec.min_metrics === spec.max_metrics ? spec.min_metrics : `${spec.min_metrics}–${spec.max_metrics}`})</legend>
          {metrics.length === 0 ? (
            <p className="kg-cap">No metric is bound to Executive yet. Bind one in the Metric registry first.</p>
          ) : (
            <ul className="kg-pick">
              {metrics.map((m) => {
                const selected = draft.metrics.includes(m.metric_code);
                const reason = selected ? null : metricBlocked(spec, chosen, m);
                const disabled = Boolean(reason) || (!selected && atMax);
                return (
                  <li key={m.metric_code}>
                    <label className={disabled ? "is-disabled" : ""} title={reason ?? undefined}>
                      <input type="checkbox" checked={selected} disabled={disabled} onChange={() => toggleMetric(m.metric_code)} />
                      {m.display_name} <span className="kg-cap">({m.unit} · {m.aggregation})</span>
                      {reason ? <span className="kg-cap"> — {reason}</span> : null}
                    </label>
                  </li>
                );
              })}
            </ul>
          )}
        </fieldset>

        <div className="kg-form-row">
          <SelectField
            label="Breakdown"
            hint={rule === "never" ? "This type does not break down." : rule === "required" ? "Required for this type." : "Optional."}
            value={draft.dimension}
            disabled={rule === "never"}
            options={[{ value: "", label: rule === "required" ? "Choose a dimension…" : "None" }, ...dimensions]}
            onChange={(e) => setDraft({ ...draft, dimension: e.target.value })}
          />
        </div>

        <fieldset className="kg-fieldset">
          <legend>Series{spec.needs_comparison ? " (needs a comparison)" : ""}</legend>
          {!spec.comparisons ? (
            <p className="kg-cap">This type draws the actual only.</p>
          ) : (
            <ul className="kg-pick kg-pick--inline">
              {ALL_SERIES.map((s) => (
                <li key={s.value}>
                  <label className={s.value === "actual" ? "is-disabled" : ""}>
                    <input type="checkbox" checked={s.value === "actual" || draft.series.includes(s.value)} disabled={s.value === "actual"} onChange={() => toggleSeries(s.value)} />
                    {s.label}
                  </label>
                </li>
              ))}
            </ul>
          )}
        </fieldset>

        {problems.length > 0 ? (
          <Notice tone="warn" title="Not ready yet.">
            <ul className="kg-problems">{problems.map((p) => <li key={p}>{p}</li>)}</ul>
          </Notice>
        ) : null}
        {error ? <p role="alert" style={{ color: "var(--neg-ink)", fontSize: 13 }}>{error}</p> : null}

        <div style={{ display: "flex", gap: 8 }}>
          <button type="submit" className="kg-btn kg-btn--primary" disabled={busy || problems.length > 0 || keyTaken}>
            {busy ? "Saving…" : existing ? "Save changes" : "Place widget"}
          </button>
          <button type="button" className="kg-btn" onClick={onClose}>Cancel</button>
        </div>
      </form>
    </section>
  );
}

type Band = { label: string; threshold: string };

export function ThresholdsForm({ types, widget, onClose, onSaved }: { types: TypeSpec[]; widget: WidgetRow; onClose: () => void; onSaved: () => void }) {
  const spec = typeByKey(types).get(widget.widget_type ?? "")!;
  const stored = widget.thresholds;
  const [source, setSource] = useState<"metric" | "override">(stored.source);
  const [basis, setBasis] = useState<"achievement" | "value">(stored.basis ?? "achievement");
  const [bands, setBands] = useState<Band[]>(stored.source === "override" && stored.bands.length ? stored.bands.map((b) => ({ label: b.label, threshold: String(b.threshold) })) : [{ label: "", threshold: "0" }, { label: "", threshold: "1" }]);
  const [note, setNote] = useState(stored.note ?? "");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const setBand = (i: number, patch: Partial<Band>) => setBands((b) => b.map((row, j) => (j === i ? { ...row, ...patch } : row)));
  const addBand = () => setBands((b) => [...b, { label: "", threshold: "" }]);
  const removeBand = (i: number) => setBands((b) => b.filter((_, j) => j !== i));

  const problems: string[] = [];
  if (source === "override") {
    if (bands.length < 2) problems.push("An override needs at least two bands.");
    if (bands.length > 6) problems.push("An override has at most six bands.");
    if (bands.some((b) => !b.label.trim())) problems.push("Each band needs a label.");
    if (bands.some((b) => b.threshold.trim() === "" || Number.isNaN(Number(b.threshold)))) problems.push("Each threshold is a number.");
    const nums = bands.map((b) => Number(b.threshold));
    if (nums.every((n) => !Number.isNaN(n)) && nums.some((n, i) => i > 0 && n <= nums[i - 1])) problems.push("Thresholds go from lowest to highest, no repeats.");
    if (new Set(bands.map((b) => b.label.trim())).size !== bands.length) problems.push("Each band needs its own label.");
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const thresholds = source === "metric" ? { source: "metric" as const } : { source: "override" as const, basis, bands: bands.map((b) => ({ label: b.label.trim(), threshold: b.threshold })), note: note.trim() };
    try {
      const out = await invoke("widget.thresholds.set", { widget_key: widget.widget_key, expected_version: widget.version, thresholds }, { allowProposal: true });
      setDone(isProposal(out) ? out.message : "The thresholds are updated.");
      onSaved();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The thresholds could not be saved.");
    } finally {
      setBusy(false);
    }
  };

  if (done) {
    return <Notice title="Done." role="status">{done} <button type="button" className="kg-btn kg-btn--link" onClick={onClose}>Close</button></Notice>;
  }

  return (
    <section className="kg-card" aria-labelledby="th-heading">
      <h2 id="th-heading" className="kg-section" style={{ marginBottom: 6 }}>Thresholds · {widget.title || widget.widget_key}</h2>
      {spec && !spec.thresholds ? (
        <Notice tone="warn" title="This type draws no bands.">A {spec.label} takes no threshold override.</Notice>
      ) : null}
      <form className="kg-form" onSubmit={(e) => void submit(e)} noValidate>
        <SelectField
          label="Source"
          value={source}
          options={[{ value: "metric", label: "The metric's rating bands" }, { value: "override", label: "Override for this widget" }]}
          onChange={(e) => setSource(e.target.value as "metric" | "override")}
        />
        {source === "override" ? (
          <>
            <SelectField
              label="Bands are on"
              value={basis}
              options={[{ value: "achievement", label: "% of target achieved (1.0 = on target)" }, { value: "value", label: "The metric's own value" }]}
              onChange={(e) => setBasis(e.target.value as "achievement" | "value")}
            />
            <fieldset className="kg-fieldset">
              <legend>Bands (lowest threshold first)</legend>
              {bands.map((b, i) => (
                <div className="kg-form-row" key={i}>
                  <TextField label={`Label ${i + 1}`} value={b.label} onChange={(e) => setBand(i, { label: e.target.value })} />
                  <TextField label="Starts at" value={b.threshold} inputMode="decimal" onChange={(e) => setBand(i, { threshold: e.target.value })} />
                  <button type="button" className="kg-btn kg-btn--link" onClick={() => removeBand(i)} disabled={bands.length <= 2} aria-label={`Remove band ${i + 1}`}>Remove</button>
                </div>
              ))}
              <button type="button" className="kg-btn" onClick={addBand} disabled={bands.length >= 6}>Add band</button>
            </fieldset>
            <TextField label="Note (shown on the widget)" value={note} onChange={(e) => setNote(e.target.value)} />
          </>
        ) : (
          <p className="kg-cap">The widget reads the metric's target and the client's rating bands.</p>
        )}
        {problems.length > 0 ? <Notice tone="warn" title="Not ready yet."><ul className="kg-problems">{problems.map((p) => <li key={p}>{p}</li>)}</ul></Notice> : null}
        {error ? <p role="alert" style={{ color: "var(--neg-ink)", fontSize: 13 }}>{error}</p> : null}
        <div style={{ display: "flex", gap: 8 }}>
          <button type="submit" className="kg-btn kg-btn--primary" disabled={busy || problems.length > 0}>{busy ? "Saving…" : "Save thresholds"}</button>
          <button type="button" className="kg-btn" onClick={onClose}>Cancel</button>
        </div>
      </form>
    </section>
  );
}

export function HistoryPanel({ widgetKey, onClose }: { widgetKey: string; onClose: () => void }) {
  const [history, reload] = useQuery("widget.history", { widget_key: widgetKey });
  return (
    <section className="kg-card" aria-labelledby="history-heading">
      <div className="kg-row" style={{ marginBottom: 12 }}>
        <h2 id="history-heading" className="kg-section">History · {widgetKey}</h2>
        <button type="button" className="kg-btn kg-btn--link" onClick={onClose}>Close</button>
      </div>
      {history.status === "loading" ? (
        <Loading label="Loading history"><TableSkeleton rows={3} columns={3} /></Loading>
      ) : history.status === "error" ? (
        <ErrorPanel error={history.error} retry={reload} what="The history" />
      ) : (
        <ol className="kg-history">
          {history.data.versions.map((v) => (
            <li key={v.version}>
              <b>v{v.version}</b> · {CHANGE_LABEL[v.change] ?? v.change} · {v.widget_type ?? "—"}
              <div className="kg-cap">{formatDateTime(v.changed_at)}{v.approval_request_id ? " · pending approval" : ""}</div>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

const CHANGE_LABEL: Record<string, string> = {
  placed: "Placed",
  updated: "Updated",
  type_changed: "Type changed",
  thresholds_changed: "Thresholds changed",
  removed: "Removed",
  registered: "Registered from a feed",
};
