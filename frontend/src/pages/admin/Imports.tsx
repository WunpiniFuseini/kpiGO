import { useMemo, useRef, useState, type ChangeEvent } from "react";

import type { Input, Output } from "../../api/actions";
import { ApiError, invoke } from "../../api/client";
import { useMutation, useQuery } from "../../api/useAction";
import {
  CardSkeleton,
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
import { formatDateTime } from "../../lib/format";
import { Page } from "../../shell/AppShell";
import { useMe } from "../../session/Session";

type Draft = Output<"import.draft.get">;
type Metric = Draft["proposals"]["metrics"][number];
type Subject = Draft["proposals"]["subjects"][number];
type ApplyOut = Output<"import.draft.apply">;
type Outcome = ApplyOut["outcomes"][number];
type KeyedOutcome = Outcome & { _key: string };

type MetricRow = Metric & { include: boolean };
type SubjectRow = Subject & { include: boolean };
type MetricEdit = NonNullable<Input<"import.draft.apply">["metrics"]>[number];
type SubjectEdit = NonNullable<Input<"import.draft.apply">["subjects"]>[number];

const DIRECTIONS = [
  { value: "higher_is_better", label: "Higher is better" },
  { value: "lower_is_better", label: "Lower is better" },
];
const UNITS = ["currency", "count", "percent", "days", "hours", "score"].map((u) => ({ value: u, label: u }));
const AGGREGATIONS = ["sum", "average", "latest", "count", "ratio"].map((a) => ({ value: a, label: a }));

const KIND_LABEL: Record<string, string> = {
  scorecard: "KPI scorecard",
  roster: "Staff roster",
  powerbi: "Power BI model",
  unknown: "Not recognised",
};
const OUTCOME_TONE: Record<string, "up" | "warn" | "info" | "flat"> = {
  registered: "up",
  exists: "info",
  pending_approval: "warn",
  failed: "warn",
};

/** Which preview action reads this file, by extension: spreadsheets vs. Power BI models. */
export function previewActionFor(filename: string): "import.spreadsheet.preview" | "import.powerbi.preview" {
  const lower = filename.toLowerCase();
  if (/\.(pbit|pbix|bim|tmdl)$/.test(lower) || lower.endsWith(".json")) return "import.powerbi.preview";
  return "import.spreadsheet.preview";
}

function readAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("The file could not be read."));
    reader.onload = () => {
      // A data URL is "data:<type>;base64,<payload>"; keep the payload.
      const result = String(reader.result);
      resolve(result.slice(result.indexOf(",") + 1));
    };
    reader.readAsDataURL(file);
  });
}

/**
 * Administer → Import data (R6): bring a bank's existing KPI scorecard, staff roster or
 * Power BI model in as a reviewable draft, correct what the assistant inferred, then
 * register the metrics and people through the ordinary actions.
 */
export function ImportsPage() {
  const me = useMe();
  const canView = me.permissions.includes("import.view");
  const canManage = me.permissions.includes("import.manage");
  const [drafts, reloadDrafts] = useQuery("import.draft.list", {});
  const [selected, setSelected] = useState<string | null>(null);

  if (!canView) {
    return (
      <Page title="Import data">
        <EmptyState kind="no-access" title="You cannot see imports" ask="an Admin, under Administer → Users & access">
          Importing config from a spreadsheet or Power BI model is an Admin task.
        </EmptyState>
      </Page>
    );
  }

  const onPreviewed = (id: string) => {
    reloadDrafts();
    setSelected(id);
  };
  const onReviewed = () => {
    reloadDrafts();
  };

  return (
    <Page title="Import data">
      {canManage ? <UploadCard onPreviewed={onPreviewed} /> : null}
      {selected ? (
        <ReviewPanel importId={selected} canManage={canManage} onClose={() => setSelected(null)} onReviewed={onReviewed} />
      ) : null}
      <section className="kg-card" aria-labelledby="drafts-heading">
        <h2 id="drafts-heading" className="kg-section" style={{ marginBottom: 6 }}>
          Import drafts
        </h2>
        <p className="kg-cap" style={{ marginBottom: 14 }}>
          Each upload is kept as a draft. Nothing is written until you review it and apply it.
        </p>
        {drafts.status === "loading" ? (
          <Loading label="Loading import drafts">
            <TableSkeleton rows={3} columns={5} />
          </Loading>
        ) : drafts.status === "error" ? (
          <ErrorPanel error={drafts.error} retry={reloadDrafts} what="import drafts" />
        ) : (
          <DraftTable drafts={drafts.data.drafts} selectedId={selected} onSelect={setSelected} canManage={canManage} />
        )}
      </section>
    </Page>
  );
}

export function UploadCard({ onPreviewed }: { onPreviewed: (importId: string) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const pick = (event: ChangeEvent<HTMLInputElement>) => {
    setError(null);
    setFile(event.target.files?.[0] ?? null);
  };

  const submit = async () => {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const content_base64 = await readAsBase64(file);
      const action = previewActionFor(file.name);
      const out = await invoke(action, { file: { filename: file.name, content_base64 } });
      setFile(null);
      if (inputRef.current) inputRef.current.value = "";
      onPreviewed(out.import_id);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The file could not be read.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="kg-card kg-stack" aria-labelledby="upload-heading">
      <h2 id="upload-heading" className="kg-section">
        Upload a file to import
      </h2>
      <p style={{ color: "var(--ink-2)", marginTop: 0 }}>
        A KPI scorecard (.csv or .xlsx), a staff roster, or a Power BI model (.pbit, model.bim, .tmdl). A .pbix is not read
        directly — save it as a .pbit template first.
      </p>
      <div className="kg-field">
        <label htmlFor="import-file">Choose a file</label>
        <input
          id="import-file"
          ref={inputRef}
          className="kg-input"
          type="file"
          accept=".csv,.xlsx,.pbit,.bim,.tmdl,.json,.zip"
          onChange={pick}
          disabled={busy}
        />
      </div>
      {error ? (
        <p role="alert" style={{ color: "var(--neg-ink)", fontSize: 13 }}>
          {error}
        </p>
      ) : null}
      <div>
        <button type="button" className="kg-btn kg-btn--primary" onClick={submit} disabled={busy || !file}>
          {busy ? "Reading…" : "Read and preview"}
        </button>
      </div>
    </section>
  );
}

export function DraftTable({
  drafts,
  selectedId,
  onSelect,
  canManage,
}: {
  drafts: Draft[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  canManage: boolean;
}) {
  return (
    <DataTable
      caption="Import drafts"
      captionHidden
      columns={[
        { key: "filename", header: "File", render: (d) => d.filename },
        { key: "kind", header: "Kind", render: (d) => KIND_LABEL[d.kind] ?? d.kind },
        {
          key: "proposes",
          header: "Proposes",
          render: (d) =>
            d.summary.metrics || d.summary.subjects
              ? [d.summary.metrics ? `${d.summary.metrics} metrics` : null, d.summary.subjects ? `${d.summary.subjects} people` : null]
                  .filter(Boolean)
                  .join(", ")
              : "—",
        },
        { key: "status", header: "Status", render: (d) => <Chip tone={d.status === "applied" ? "up" : "flat"}>{d.status}</Chip> },
        { key: "created", header: "Uploaded", render: (d) => (d.created_at ? formatDateTime(d.created_at) : "—") },
        {
          key: "open",
          header: "Review",
          render: (d) => (
            <button
              type="button"
              className="kg-btn"
              aria-label={`Review the import ${d.filename}`}
              aria-pressed={selectedId === d.import_id}
              onClick={() => onSelect(d.import_id)}
            >
              {d.status === "drafted" && canManage ? "Review" : "View"}
            </button>
          ),
        },
      ]}
      rows={drafts}
      rowKey={(d) => d.import_id}
      empty={
        <EmptyState kind="none" title="No imports yet">
          {canManage ? "Upload a scorecard, roster or Power BI model above to get started." : "An Admin imports data here."}
        </EmptyState>
      }
    />
  );
}

function ReviewPanel({
  importId,
  canManage,
  onClose,
  onReviewed,
}: {
  importId: string;
  canManage: boolean;
  onClose: () => void;
  onReviewed: () => void;
}) {
  const [draft, reload] = useQuery("import.draft.get", { import_id: importId });
  if (draft.status === "loading") {
    return (
      <section className="kg-card">
        <Loading label="Loading the import">
          <CardSkeleton />
        </Loading>
      </section>
    );
  }
  if (draft.status === "error") {
    return (
      <section className="kg-card">
        <ErrorPanel error={draft.error} retry={reload} what="this import" />
      </section>
    );
  }
  return (
    <DraftReview
      draft={draft.data}
      canManage={canManage}
      onClose={onClose}
      onChanged={() => {
        reload();
        onReviewed();
      }}
    />
  );
}

export function DraftReview({
  draft,
  canManage,
  onClose,
  onChanged,
}: {
  draft: Draft;
  canManage: boolean;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [metrics, setMetrics] = useState<MetricRow[]>(() => draft.proposals.metrics.map((m) => ({ ...m, include: true })));
  const [subjects, setSubjects] = useState<SubjectRow[]>(() => draft.proposals.subjects.map((s) => ({ ...s, include: true })));
  const [apply, runApply] = useMutation("import.draft.apply");
  const [discard, runDiscard] = useMutation("import.draft.discard");
  const editable = canManage && draft.status === "drafted";

  const chosen = useMemo(
    () => metrics.filter((m) => m.include).length + subjects.filter((s) => s.include).length,
    [metrics, subjects],
  );

  const onApply = async () => {
    const payload: Input<"import.draft.apply"> = {
      import_id: draft.import_id,
      metrics: metrics.map(
        (m): MetricEdit => ({
          source_row: m.source_row,
          include: m.include,
          display_name: m.display_name,
          metric_code: m.metric_code,
          direction: m.direction as MetricEdit["direction"],
          aggregation: m.aggregation as MetricEdit["aggregation"],
          unit: m.unit as MetricEdit["unit"],
          is_percentage: m.is_percentage,
          decimal_places: m.decimal_places,
          description: m.description,
        }),
      ),
      subjects: subjects.map(
        (s): SubjectEdit => ({
          source_row: s.source_row,
          include: s.include,
          staff_no: s.staff_no,
          full_name: s.full_name,
          email: s.email,
        }),
      ),
    };
    const out = await runApply(payload);
    if (out) onChanged();
  };

  const onDiscard = async () => {
    const out = await runDiscard({ import_id: draft.import_id });
    if (out) {
      onChanged();
      onClose();
    }
  };

  if (apply.status === "done") {
    return <ApplyReport out={apply.data} onClose={onClose} />;
  }

  return (
    <section className="kg-card kg-stack" aria-labelledby="review-heading">
      <div className="kg-row" style={{ justifyContent: "space-between" }}>
        <h2 id="review-heading" className="kg-section">
          Review “{draft.filename}”
        </h2>
        <button type="button" className="kg-btn" onClick={onClose} aria-label="Close this review">
          Close
        </button>
      </div>

      {draft.kind === "unknown" ? (
        <EmptyState kind="error" title="kpiGo could not read this file">
          {draft.proposals.message}
        </EmptyState>
      ) : (
        <>
          <MappingSummary draft={draft} />
          {draft.proposals.warnings.length ? (
            <Notice tone="warn" title={draft.proposals.warnings.length === 1 ? "One thing to check." : "A few things to check."}>
              <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
                {draft.proposals.warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </Notice>
          ) : null}

          {metrics.length ? (
            <MetricReview rows={metrics} editable={editable} onChange={setMetrics} />
          ) : null}
          {subjects.length ? (
            <SubjectReview rows={subjects} editable={editable} onChange={setSubjects} />
          ) : null}
          {draft.proposals.targets.length ? (
            <p className="kg-cap">
              {draft.proposals.targets.length} target/weight {draft.proposals.targets.length === 1 ? "value was" : "values were"} read
              and will be loaded in the target workbench once these metrics are active.
            </p>
          ) : null}

          {apply.status === "error" ? (
            <p role="alert" style={{ color: "var(--neg-ink)", fontSize: 13 }}>
              {apply.error.message}
            </p>
          ) : null}

          {editable ? (
            <div style={{ display: "flex", gap: 8 }}>
              <button type="button" className="kg-btn kg-btn--primary" onClick={onApply} disabled={apply.status === "running" || chosen === 0}>
                {apply.status === "running" ? "Applying…" : `Apply ${chosen} ${chosen === 1 ? "item" : "items"}`}
              </button>
              <button type="button" className="kg-btn" onClick={onDiscard} disabled={discard.status === "running"}>
                {discard.status === "running" ? "Discarding…" : "Discard"}
              </button>
            </div>
          ) : (
            <p className="kg-cap">
              This import is {draft.status}
              {draft.applied_at ? ` — applied ${formatDateTime(draft.applied_at)}` : ""}.
            </p>
          )}
        </>
      )}
    </section>
  );
}

function MappingSummary({ draft }: { draft: Draft }) {
  const entries = Object.entries(draft.proposals.mapping);
  if (!entries.length && !draft.proposals.unmapped_columns.length) return null;
  return (
    <div className="kg-field">
      <span className="kg-eyebrow">What kpiGo read</span>
      <p className="kg-cap" style={{ marginTop: 2 }}>
        {entries.map(([role, column]) => (
          <Chip key={role} tone="info">
            {role.replace(/_/g, " ")}: {column}
          </Chip>
        ))}
        {draft.proposals.unmapped_columns.length ? (
          <span style={{ marginLeft: 6 }}>Columns left out: {draft.proposals.unmapped_columns.join(", ")}.</span>
        ) : null}
      </p>
    </div>
  );
}

export function MetricReview({
  rows,
  editable,
  onChange,
}: {
  rows: MetricRow[];
  editable: boolean;
  onChange: (rows: MetricRow[]) => void;
}) {
  const set = (row: MetricRow, patch: Partial<MetricRow>) => onChange(rows.map((r) => (r === row ? { ...r, ...patch } : r)));

  return (
    <fieldset className="kg-stack" style={{ border: 0, padding: 0, margin: 0 }}>
      <legend className="kg-eyebrow">Proposed metrics ({rows.length})</legend>
      {rows.map((row) => (
        <div
          key={row.source_row}
          style={{ opacity: row.include ? 1 : 0.55, border: "1px solid var(--line)", borderRadius: 8, padding: 12 }}
        >
          <div className="kg-row" style={{ justifyContent: "space-between", gap: 10 }}>
            <label className="kg-check" style={{ fontWeight: 600 }}>
              <input
                type="checkbox"
                checked={row.include}
                disabled={!editable}
                onChange={(e) => set(row, { include: e.target.checked })}
                aria-label={`Import the metric ${row.display_name}`}
              />
              {row.display_name}
            </label>
            {row.inferred.length ? (
              <span className="kg-cap" title="kpiGo guessed these — check them">
                Guessed: {row.inferred.join(", ")}
              </span>
            ) : null}
          </div>
          {row.include ? (
            <div className="kg-form-row" style={{ marginTop: 10 }}>
              <TextField
                label="Display name"
                value={row.display_name}
                disabled={!editable}
                onChange={(e) => set(row, { display_name: e.target.value })}
              />
              <TextField
                label="Code"
                value={row.metric_code}
                disabled={!editable}
                onChange={(e) => set(row, { metric_code: e.target.value })}
              />
              <SelectField
                label="Direction"
                value={row.direction}
                disabled={!editable}
                options={DIRECTIONS}
                onChange={(e) => set(row, { direction: e.target.value })}
              />
              <SelectField
                label="Unit"
                value={row.unit}
                disabled={!editable}
                options={UNITS}
                onChange={(e) => set(row, { unit: e.target.value, is_percentage: e.target.value === "percent" })}
              />
              <SelectField
                label="Aggregation"
                value={row.aggregation}
                disabled={!editable}
                options={AGGREGATIONS}
                onChange={(e) => set(row, { aggregation: e.target.value })}
              />
              <TextField
                label="Decimals"
                type="number"
                min={0}
                max={6}
                value={String(row.decimal_places)}
                disabled={!editable}
                onChange={(e) => set(row, { decimal_places: Math.max(0, Math.min(6, Number(e.target.value) || 0)) })}
              />
            </div>
          ) : null}
        </div>
      ))}
    </fieldset>
  );
}

export function SubjectReview({
  rows,
  editable,
  onChange,
}: {
  rows: SubjectRow[];
  editable: boolean;
  onChange: (rows: SubjectRow[]) => void;
}) {
  const set = (row: SubjectRow, patch: Partial<SubjectRow>) => onChange(rows.map((r) => (r === row ? { ...r, ...patch } : r)));
  return (
    <fieldset className="kg-stack" style={{ border: 0, padding: 0, margin: 0 }}>
      <legend className="kg-eyebrow">Proposed people ({rows.length})</legend>
      {rows.map((row) => (
        <div
          key={row.source_row}
          style={{ opacity: row.include ? 1 : 0.55, border: "1px solid var(--line)", borderRadius: 8, padding: 12 }}
        >
          <label className="kg-check" style={{ fontWeight: 600 }}>
            <input
              type="checkbox"
              checked={row.include}
              disabled={!editable}
              onChange={(e) => set(row, { include: e.target.checked })}
              aria-label={`Import the person ${row.full_name}`}
            />
            {row.full_name} ({row.staff_no})
          </label>
          {row.include ? (
            <div className="kg-form-row" style={{ marginTop: 10 }}>
              <TextField label="Staff no" value={row.staff_no} disabled={!editable} onChange={(e) => set(row, { staff_no: e.target.value })} />
              <TextField label="Full name" value={row.full_name} disabled={!editable} onChange={(e) => set(row, { full_name: e.target.value })} />
              <TextField
                label="Email"
                type="email"
                value={row.email}
                disabled={!editable}
                hint={row.email ? undefined : "No email was in the file; add one to register this person."}
                onChange={(e) => set(row, { email: e.target.value })}
              />
            </div>
          ) : null}
        </div>
      ))}
    </fieldset>
  );
}

export function ApplyReport({ out, onClose }: { out: ApplyOut; onClose: () => void }) {
  const counts = out.draft.applied ?? {};
  return (
    <section className="kg-card kg-stack" aria-labelledby="applied-heading">
      <h2 id="applied-heading" className="kg-section">
        Imported “{out.draft.filename}”
      </h2>
      <p style={{ color: "var(--ink-2)", marginTop: 0 }}>
        {summarise(counts)} Registered metrics start as drafts; activate them in the metric registry.
      </p>
      {out.outcomes.length ? (
        <DataTable
          caption="What was applied"
          columns={[
            { key: "kind", header: "Kind", render: (o: KeyedOutcome) => o.kind },
            { key: "ref", header: "Reference", render: (o: KeyedOutcome) => <span className="kg-mono">{o.ref}</span> },
            { key: "outcome", header: "Outcome", render: (o: KeyedOutcome) => <Chip tone={OUTCOME_TONE[o.outcome] ?? "flat"}>{o.outcome.replace(/_/g, " ")}</Chip> },
            { key: "detail", header: "Detail", render: (o: KeyedOutcome) => o.detail || "—" },
          ]}
          rows={out.outcomes.map((o, i) => ({ ...o, _key: `${i}` }))}
          rowKey={(o: KeyedOutcome) => o._key}
          empty={null}
        />
      ) : null}
      <div>
        <button type="button" className="kg-btn kg-btn--primary" onClick={onClose}>
          Done
        </button>
      </div>
    </section>
  );
}

function summarise(counts: Record<string, Record<string, number>>): string {
  const parts: string[] = [];
  for (const [kind, byOutcome] of Object.entries(counts)) {
    const registered = byOutcome.registered ?? 0;
    if (registered) parts.push(`${registered} ${kind} registered`);
    const pending = byOutcome.pending_approval ?? 0;
    if (pending) parts.push(`${pending} ${kind} awaiting approval`);
    const exists = byOutcome.exists ?? 0;
    if (exists) parts.push(`${exists} ${kind} already existed`);
    const failed = byOutcome.failed ?? 0;
    if (failed) parts.push(`${failed} ${kind} could not be registered`);
  }
  return parts.length ? `${parts.join(", ")}.` : "Nothing was applied.";
}
