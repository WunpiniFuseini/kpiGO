import { useState, type FormEvent } from "react";

import type { Output } from "../../api/actions";
import { ApiError, invoke, isProposal } from "../../api/client";
import { useQuery } from "../../api/useAction";
import {
  Chip,
  DataTable,
  EmptyState,
  ErrorPanel,
  Loading,
  Notice,
  TableSkeleton,
  TextField,
} from "../../components";
import { readAsBase64 } from "../../lib/files";
import { currentPeriod, formatDateTime, formatDecimal, formatPeriod, shiftPeriod } from "../../lib/format";
import { Page } from "../../shell/AppShell";
import { useMe } from "../../session/Session";
import { OverridesSection } from "./Overrides";

type Coverage = Output<"target.coverage">;
type Cell = Coverage["cells"][number];
type Weight = Coverage["weights"][number];
type TargetRow = Output<"target.list">["targets"][number];
type Batch = Output<"target.batch.list">["batches"][number];
type SheetResult = Output<"target.upload">;

/** Administer → Targets: the target workbench (PRD SC-10, SC-11; App Flow §7.4). */
export function TargetsPage() {
  const me = useMe();
  const canManage = me.permissions.includes("target.manage");
  const canPublish = me.permissions.includes("target.publish");
  const [anchor, setAnchor] = useState(currentPeriod());
  const [panel, setPanel] = useState<"none" | "upload" | "copy">("none");
  const [coverage, reloadCoverage] = useQuery("target.coverage", { period_key: anchor });
  const [drafts, reloadDrafts] = useQuery("target.list", { state: "draft", limit: 5000 });
  const [batches, reloadBatches] = useQuery("target.batch.list", {});
  const reloadAll = () => {
    reloadCoverage();
    reloadDrafts();
    reloadBatches();
  };
  const periods = coverage.status === "ready" ? coverage.data.period_keys : [];

  return (
    <Page
      title="Targets"
      actions={
        canManage ? (
          <>
            <button type="button" className="kg-btn" onClick={() => setPanel(panel === "copy" ? "none" : "copy")} aria-expanded={panel === "copy"}>
              Copy forward
            </button>
            <button
              type="button"
              className="kg-btn kg-btn--primary"
              onClick={() => setPanel(panel === "upload" ? "none" : "upload")}
              aria-expanded={panel === "upload"}
            >
              Upload sheet
            </button>
          </>
        ) : null
      }
    >
      {panel === "upload" ? <UploadPanel onClose={() => setPanel("none")} onSaved={reloadAll} /> : null}
      {panel === "copy" ? <CopyForwardPanel anchor={anchor} onClose={() => setPanel("none")} onSaved={reloadAll} /> : null}

      <div className="kg-sechead">
        <div>
          <h2 className="kg-section">Coverage</h2>
          <p className="kg-cap">
            {coverage.status === "ready"
              ? `${coverage.data.cycle_name} · ${formatPeriod(periods[0])} to ${formatPeriod(periods[periods.length - 1])}`
              : "Profile × metric × month: what is set, missing or still in draft."}
          </p>
        </div>
        <span className="kg-spacer" />
        <div className="kg-seg" role="group" aria-label="Cycle year">
          <button type="button" onClick={() => setAnchor(shiftPeriod(periods[0] ?? anchor, -12))}>
            <span aria-hidden="true">◀</span> Previous cycle
          </button>
          <button type="button" onClick={() => setAnchor(shiftPeriod(periods[0] ?? anchor, 12))}>
            Next cycle <span aria-hidden="true">▶</span>
          </button>
        </div>
      </div>

      {coverage.status === "loading" ? (
        <section className="kg-card">
          <Loading label="Loading target coverage">
            <TableSkeleton rows={5} columns={8} />
          </Loading>
        </section>
      ) : coverage.status === "error" ? (
        <section className="kg-card">
          <ErrorPanel error={coverage.error} retry={reloadCoverage} what="Target coverage" />
        </section>
      ) : (
        <CoverageView coverage={coverage.data} canManage={canManage} />
      )}

      <section className="kg-card" aria-labelledby="drafts-heading">
        <h2 id="drafts-heading" className="kg-section">
          Drafts
        </h2>
        {drafts.status === "loading" ? (
          <Loading label="Loading drafts">
            <TableSkeleton rows={4} columns={6} />
          </Loading>
        ) : drafts.status === "error" ? (
          <ErrorPanel error={drafts.error} retry={reloadDrafts} what="Drafts" />
        ) : (
          <DraftsView
            drafts={drafts.data.targets.filter((t) => periods.length === 0 || periods.includes(t.period_key))}
            periods={periods}
            canManage={canManage}
            canPublish={canPublish}
            onChanged={reloadAll}
          />
        )}
      </section>

      <section className="kg-card" aria-labelledby="batches-heading">
        <h2 id="batches-heading" className="kg-section">
          Publish history
        </h2>
        {batches.status === "loading" ? (
          <Loading label="Loading publish history">
            <TableSkeleton rows={3} columns={5} />
          </Loading>
        ) : batches.status === "error" ? (
          <ErrorPanel error={batches.error} retry={reloadBatches} what="Publish history" />
        ) : (
          <BatchesView batches={batches.data.batches} canPublish={canPublish} onReverted={reloadAll} />
        )}
      </section>

      {me.permissions.includes("override.view") ? (
        <OverridesSection canRequest={me.permissions.includes("override.request")} canApprove={me.permissions.includes("override.approve")} />
      ) : null}
    </Page>
  );
}

// ── coverage ────────────────────────────────────────────────────────────────

const CELL: Record<string, { tone: "up" | "info" | "warn" | "down" | "flat"; label: string; words: string }> = {
  published: { tone: "up", label: "Set", words: "published" },
  revision: { tone: "info", label: "Set · draft", words: "published, with a draft revision" },
  draft: { tone: "info", label: "Draft", words: "draft only, not live" },
  partial: { tone: "warn", label: "Partial", words: "set for some people only" },
  missing: { tone: "down", label: "Missing", words: "no target" },
};

export function CoverageMark({ cell }: { cell: Cell }) {
  if (cell.expected === 0) {
    return <Chip tone="flat">No one</Chip>;
  }
  const look = CELL[cell.state] ?? CELL.missing;
  const counts = cell.target_scope === "subject" && cell.state === "partial" ? ` ${cell.published + cell.drafts}/${cell.expected}` : "";
  return (
    <Chip tone={look.tone}>
      <span aria-hidden="true">
        {look.label}
        {counts}
      </span>
      <span className="sr-only">
        {look.words}
        {cell.target_scope === "subject" ? `, ${cell.published} of ${cell.expected} people published, ${cell.drafts} in draft` : ""}
      </span>
    </Chip>
  );
}

export function WeightMark({ weight }: { weight: Weight | undefined }) {
  if (!weight) return <span className="kg-cap">–</span>;
  const sum = formatDecimal(weight.weight_sum);
  if (!weight.complete) {
    return (
      <span className="kg-cap" title={weight.missing.join(", ")}>
        {sum}
        <span className="sr-only">, incomplete: {weight.missing.length} target(s) still missing</span>
      </span>
    );
  }
  if (weight.ok) {
    return (
      <Chip tone="up">
        {sum}
        <span className="sr-only"> of {formatDecimal(weight.expected)}, weights sum correctly</span>
      </Chip>
    );
  }
  return (
    <Chip tone="down">
      {sum}
      <span className="sr-only"> but must sum to {formatDecimal(weight.expected)}</span>
      {weight.subjects_off.length ? <span aria-hidden="true"> · {weight.subjects_off.length} people</span> : null}
    </Chip>
  );
}

export function CoverageView({ coverage, canManage }: { coverage: Coverage; canManage: boolean }) {
  if (coverage.cells.length === 0) {
    return (
      <section className="kg-card">
        <EmptyState kind="none" title="No profile has Scorecards metrics in this cycle">
          Targets are set per profile and metric. Give a profile its metrics under Administer → Scorecard setup, then
          upload or copy forward its targets here.
        </EmptyState>
      </section>
    );
  }
  const total = coverage.cells.length;
  const set = coverage.cells.filter((c) => c.state === "published" || c.state === "revision").length;
  const failing = coverage.weights.filter((w) => w.complete && !w.ok).length;
  return (
    <>
      <div className="kg-grid" role="list" aria-label="Coverage summary">
        <SummaryFigure label="Targets live" value={`${set} / ${total}`} note={coverage.complete ? "The cycle is fully covered" : "Cells with a published target"} />
        <SummaryFigure label="Gaps" value={String(coverage.gaps)} note={coverage.gaps ? "Missing or set for only some people" : "None"} />
        <SummaryFigure label="Drafts waiting" value={String(coverage.drafts)} note={coverage.drafts ? "Publish to make them live" : "Nothing waiting"} />
        <SummaryFigure label="Weight checks failing" value={String(failing)} note={failing ? "Publishing is blocked for these" : "Every complete profile sums correctly"} />
      </div>
      {coverage.profiles.map((profile) => (
        <ProfileCoverage key={profile} profile={profile} coverage={coverage} canManage={canManage} />
      ))}
    </>
  );
}

function SummaryFigure({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div className="kg-card" role="listitem">
      <div className="kg-eyebrow">{label}</div>
      <div className="kg-fig kg-fig--sm">{value}</div>
      <div className="kg-cap">{note}</div>
    </div>
  );
}

function ProfileCoverage({ profile, coverage, canManage }: { profile: string; coverage: Coverage; canManage: boolean }) {
  const cells = coverage.cells.filter((c) => c.profile_code === profile);
  const metrics = [...new Set(cells.map((c) => c.metric_code))];
  const at = (metric: string, period: string) => cells.find((c) => c.metric_code === metric && c.period_key === period);
  const weightAt = (period: string) => coverage.weights.find((w) => w.profile_code === profile && w.period_key === period);
  const off = coverage.weights.filter((w) => w.profile_code === profile && w.complete && !w.ok);
  return (
    <section className="kg-card" aria-label={`Coverage for profile ${profile}`}>
      <div className="kg-table-wrap">
        <table className="kg-table kg-coverage">
          <caption className="kg-section">
            Profile <span className="kg-mono">{profile}</span>
          </caption>
          <thead>
            <tr>
              <th scope="col">Metric</th>
              {coverage.period_keys.map((p) => (
                <th key={p} scope="col">
                  {formatPeriod(p).slice(0, 3)}
                  <span className="sr-only"> {p.slice(0, 4)}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {metrics.map((metric) => (
              <tr key={metric}>
                <th scope="row">
                  <span className="kg-mono">{metric}</span>
                  {at(metric, coverage.period_keys[0])?.target_scope === "subject" ? <div className="kg-cap">a target per person</div> : null}
                </th>
                {coverage.period_keys.map((p) => {
                  const cell = at(metric, p);
                  return <td key={p}>{cell ? <CoverageMark cell={cell} /> : <span className="kg-cap">–</span>}</td>;
                })}
              </tr>
            ))}
            <tr className="kg-total">
              <th scope="row">Weight total</th>
              {coverage.period_keys.map((p) => (
                <td key={p}>
                  <WeightMark weight={weightAt(p)} />
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>
      {off.length ? (
        <Notice tone="neg" title="Weights do not add up.">
          {off
            .map(
              (w) =>
                `${formatPeriod(w.period_key)}: ${formatDecimal(w.weight_sum)} of ${formatDecimal(w.expected)}` +
                (w.subjects_off.length ? ` (${w.subjects_off.map((s) => `${s.staff_no} ${formatDecimal(s.weight_sum)}`).join(", ")})` : ""),
            )
            .join("; ")}
          . {canManage ? "Fix the weights in a corrected sheet; publishing stays blocked until they sum." : "Publishing stays blocked until they sum."}
        </Notice>
      ) : null}
    </section>
  );
}

// ── drafts and publish ──────────────────────────────────────────────────────

function PublishFailure({ error }: { error: ApiError }) {
  const detail = (error.detail ?? {}) as {
    weight_checks?: Weight[];
    blocked?: { metric_code: string; scope_code: string; period_key: string; reason: string }[];
  };
  return (
    <Notice tone="neg" title="Nothing was published." role="alert">
      {error.message}
      {detail.weight_checks?.length ? (
        <ul>
          {detail.weight_checks.map((w) => (
            <li key={`${w.profile_code}-${w.period_key}`}>
              {w.profile_code}, {formatPeriod(w.period_key)}: weights sum to {formatDecimal(w.weight_sum)}, not {formatDecimal(w.expected)}
              {w.subjects_off.length ? ` (${w.subjects_off.map((s) => s.staff_no).join(", ")})` : ""}
            </li>
          ))}
        </ul>
      ) : null}
      {detail.blocked?.length ? (
        <ul>
          {detail.blocked.slice(0, 10).map((b) => (
            <li key={`${b.metric_code}-${b.scope_code}-${b.period_key}`}>
              {b.metric_code} for {b.scope_code}, {formatPeriod(b.period_key)}:{" "}
              {b.reason === "closed" ? "the period is closed" : "the period has started; raise an override instead"}
            </li>
          ))}
        </ul>
      ) : null}
    </Notice>
  );
}

export function DraftsView({
  drafts,
  periods,
  canManage,
  canPublish,
  onChanged,
}: {
  drafts: TargetRow[];
  periods: string[];
  canManage: boolean;
  canPublish: boolean;
  onChanged: () => void;
}) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<{ ok: string } | { error: ApiError } | null>(null);
  const shown = drafts.slice(0, 200);
  const draftPeriods = [...new Set(drafts.map((d) => d.period_key))].sort();

  const publish = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setOutcome(null);
    try {
      const out = await invoke("target.publish", { period_keys: draftPeriods, note }, { allowProposal: true });
      setOutcome({
        ok: isProposal(out)
          ? `${out.message} The drafts stay on the workbench until a second person approves.`
          : `Published ${out.batch.row_count} targets as one batch${out.superseded ? `, replacing ${out.superseded} earlier versions` : ""}.`,
      });
      setNote("");
      onChanged();
    } catch (e) {
      setOutcome({ error: e instanceof ApiError ? e : new ApiError(0, "client_error", "Publishing failed.", null, "local") });
    } finally {
      setBusy(false);
    }
  };

  const discard = async () => {
    if (!window.confirm(`Discard ${drafts.length} draft target(s)? Published targets are not touched.`)) return;
    setBusy(true);
    setOutcome(null);
    try {
      const out = await invoke("target.draft.discard", { target_ids: drafts.map((d) => d.target_id) });
      setOutcome({ ok: `Discarded ${out.discarded} draft(s).` });
      onChanged();
    } catch (e) {
      setOutcome({ error: e instanceof ApiError ? e : new ApiError(0, "client_error", "Discarding failed.", null, "local") });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="kg-stack">
      {outcome && "ok" in outcome ? (
        <Notice title="Done." role="status">
          {outcome.ok}
        </Notice>
      ) : null}
      {outcome && "error" in outcome ? <PublishFailure error={outcome.error} /> : null}
      <DataTable
        caption={`${drafts.length} draft target(s) in this cycle`}
        captionHidden
        columns={[
          {
            key: "metric",
            header: "Metric",
            render: (t) => (
              <>
                {t.metric_name}
                <div className="kg-cap">{t.target_type} target</div>
              </>
            ),
          },
          { key: "scope", header: "For", render: (t) => (t.scope_type === "profile" ? <span className="kg-mono">{t.scope_label}</span> : t.scope_label) },
          { key: "period", header: "Month", render: (t) => formatPeriod(t.period_key) },
          { key: "value", header: "Target", numeric: true, render: (t) => formatDecimal(t.target_value) },
          { key: "weight", header: "Weight", numeric: true, render: (t) => formatDecimal(t.weight) },
          { key: "cap", header: "Cap", numeric: true, render: (t) => formatDecimal(t.cap) },
          {
            key: "version",
            header: "Version",
            render: (t) => (t.version > 1 ? <Chip tone="info">revises v{t.version - 1}</Chip> : <Chip tone="flat">new</Chip>),
          },
        ]}
        rows={shown}
        rowKey={(t) => t.target_id}
        empty={
          <EmptyState kind="good" title="No drafts are waiting">
            {periods.length
              ? `Nothing is waiting to publish for ${formatPeriod(periods[0])} to ${formatPeriod(periods[periods.length - 1])}.`
              : "Nothing is waiting to publish."}{" "}
            {canManage ? "Upload a sheet or copy last cycle forward to start." : ""}
          </EmptyState>
        }
      />
      {drafts.length > shown.length ? <p className="kg-cap">Showing the first {shown.length} of {drafts.length} drafts.</p> : null}
      {drafts.length && (canPublish || canManage) ? (
        <form className="kg-form" onSubmit={publish}>
          {canPublish ? (
            <TextField label="Note for the publish record (optional)" value={note} maxLength={2000} onChange={(e) => setNote(e.target.value)} />
          ) : null}
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {canPublish ? (
              <button type="submit" className="kg-btn kg-btn--primary" disabled={busy}>
                {busy ? "Working…" : `Publish ${drafts.length} draft(s)`}
              </button>
            ) : null}
            {canManage ? (
              <button type="button" className="kg-btn" onClick={() => void discard()} disabled={busy}>
                Discard drafts
              </button>
            ) : null}
          </div>
          <p className="kg-cap">
            Publishing checks that each profile&apos;s weights sum to the expected total, then makes every draft live as one versioned batch.
            A month that has started cannot be revised here: that is an override, with a reason and an approver.
          </p>
        </form>
      ) : null}
    </div>
  );
}

// ── publish history ─────────────────────────────────────────────────────────

export function BatchesView({ batches, canPublish, onReverted }: { batches: Batch[]; canPublish: boolean; onReverted: () => void }) {
  const [reverting, setReverting] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [message, setMessage] = useState<{ tone: "info" | "neg"; text: string } | null>(null);
  const now = currentPeriod();

  const revert = async (event: FormEvent) => {
    event.preventDefault();
    if (!reverting) return;
    try {
      const out = await invoke("target.batch.revert", { batch_id: reverting, reason }, { allowProposal: true });
      setMessage({
        tone: "info",
        text: isProposal(out) ? out.message : `Reverted. ${out.restored} earlier version(s) are live again.`,
      });
      setReverting(null);
      setReason("");
      onReverted();
    } catch (e) {
      setMessage({ tone: "neg", text: e instanceof ApiError ? e.message : "The batch could not be reverted." });
    }
  };

  return (
    <div className="kg-stack">
      {message ? (
        <Notice tone={message.tone} role={message.tone === "neg" ? "alert" : "status"}>
          {message.text}
        </Notice>
      ) : null}
      <DataTable
        caption="Publish batches, newest first"
        captionHidden
        columns={[
          { key: "when", header: "Published", render: (b) => formatDateTime(b.published_at) },
          {
            key: "periods",
            header: "Months",
            render: (b) =>
              b.period_keys.length > 1
                ? `${formatPeriod(b.period_keys[0])} to ${formatPeriod(b.period_keys[b.period_keys.length - 1])}`
                : formatPeriod(b.period_keys[0]),
          },
          { key: "rows", header: "Targets", numeric: true, render: (b) => b.row_count.toLocaleString("en-GB") },
          { key: "note", header: "Note", render: (b) => b.note || <span className="kg-cap">–</span> },
          {
            key: "status",
            header: "Status",
            render: (b) =>
              b.status === "reverted" ? (
                <Chip tone="flat">reverted {b.reverted_at ? formatDateTime(b.reverted_at) : ""}</Chip>
              ) : canPublish && b.period_keys.every((p) => p > now) ? (
                <button type="button" className="kg-btn kg-btn--link" onClick={() => setReverting(b.batch_id)}>
                  Revert
                </button>
              ) : (
                <Chip tone="up">live</Chip>
              ),
          },
        ]}
        rows={batches}
        rowKey={(b) => b.batch_id}
        empty={
          <EmptyState kind="none" title="Nothing has been published yet">
            Scorecards score nothing until targets are published: a metric with no target is excluded and flagged as a configuration gap.
          </EmptyState>
        }
      />
      {reverting ? (
        <form className="kg-form" onSubmit={revert} aria-label="Revert a publish batch">
          <TextField label="Why revert this batch?" required value={reason} onChange={(e) => setReason(e.target.value)} />
          <div style={{ display: "flex", gap: 8 }}>
            <button type="submit" className="kg-btn kg-btn--primary" disabled={!reason.trim()}>
              Revert batch
            </button>
            <button type="button" className="kg-btn" onClick={() => setReverting(null)}>
              Cancel
            </button>
          </div>
        </form>
      ) : null}
    </div>
  );
}

// ── upload and copy forward ─────────────────────────────────────────────────

export function SheetFindings({ result }: { result: SheetResult }) {
  return (
    <div className="kg-stack">
      <Notice tone={result.accepted ? "info" : "neg"} role={result.accepted ? "status" : "alert"} title={result.accepted ? "The sheet is clean." : "The sheet has errors."}>
        {result.rows_read} row(s) read, {result.rows_valid} valid, {result.errors} error(s), {result.warnings} warning(s).{" "}
        {result.written ? `${result.written} draft(s) saved.` : result.accepted ? "Nothing has been saved yet." : "Nothing was saved: a sheet is all or nothing."}
      </Notice>
      <DataTable
        caption="Findings"
        columns={[
          { key: "row", header: "Row", numeric: true, render: (f) => f.row_no ?? "–" },
          { key: "severity", header: "Severity", render: (f) => <Chip tone={f.severity === "error" ? "down" : "warn"}>{f.severity}</Chip> },
          { key: "column", header: "Column", render: (f) => (f.column ? <span className="kg-mono">{f.column}</span> : "–") },
          { key: "value", header: "Value", render: (f) => f.value ?? "–" },
          { key: "message", header: "What to fix", render: (f) => f.message },
        ]}
        rows={result.findings}
        rowKey={(f) => `${f.row_no}-${f.column}-${f.code}`}
        empty={<p className="kg-cap">No findings.</p>}
      />
    </div>
  );
}

function UploadPanel({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<SheetResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const send = async (checkOnly: boolean) => {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const content = await readAsBase64(file);
      const out = await invoke("target.upload", { sheet: { filename: file.name, content_base64: content }, check_only: checkOnly });
      setResult(out);
      if (out.written) onSaved();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The sheet could not be read.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="kg-card" aria-labelledby="upload-heading">
      <h2 id="upload-heading" className="kg-section">
        Upload a target sheet
      </h2>
      <p className="kg-cap">
        A CSV or XLSX with the columns metric_code, scope_type, scope_code, period_key, target_value, and optionally series_type, target_type,
        weight, cap and currency_code. For a per-person metric, scope_code is the staff number. The sheet is checked before anything is
        written, and saved as drafts only when every row is clean.
      </p>
      <form
        className="kg-form"
        onSubmit={(e) => {
          e.preventDefault();
          void send(true);
        }}
      >
        <div className="kg-field">
          <label htmlFor="target-sheet">Sheet</label>
          <input
            id="target-sheet"
            type="file"
            accept=".csv,.xlsx"
            className="kg-input"
            onChange={(e) => {
              setFile(e.target.files?.[0] ?? null);
              setResult(null);
            }}
          />
        </div>
        {error ? (
          <p role="alert" style={{ color: "var(--neg-ink)", fontSize: 13 }}>
            {error}
          </p>
        ) : null}
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button type="submit" className="kg-btn" disabled={!file || busy}>
            {busy ? "Checking…" : "Check sheet"}
          </button>
          <button type="button" className="kg-btn kg-btn--primary" disabled={!file || busy || !result?.accepted || result.written > 0} onClick={() => void send(false)}>
            Save as drafts
          </button>
          <button type="button" className="kg-btn" onClick={onClose}>
            Close
          </button>
        </div>
      </form>
      {result ? <SheetFindings result={result} /> : null}
    </section>
  );
}

function monthValue(periodKey: string): string {
  return `${periodKey.slice(0, 4)}-${periodKey.slice(4, 6)}`;
}

function CopyForwardPanel({ anchor, onClose, onSaved }: { anchor: string; onClose: () => void; onSaved: () => void }) {
  const lastYearStart = `${Number(anchor.slice(0, 4)) - 1}01`;
  const [from, setFrom] = useState(monthValue(lastYearStart));
  const [to, setTo] = useState(monthValue(shiftPeriod(lastYearStart, 11)));
  const [uplift, setUplift] = useState("0");
  const [result, setResult] = useState<SheetResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const send = async (checkOnly: boolean) => {
    setBusy(true);
    setError(null);
    try {
      const out = await invoke("target.copy_forward", {
        from_period: from.replace("-", ""),
        to_period: to.replace("-", ""),
        months: 12,
        uplift_pct: uplift || "0",
        check_only: checkOnly,
      });
      setResult(out);
      if (out.written) onSaved();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The targets could not be copied.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="kg-card" aria-labelledby="copy-heading">
      <h2 id="copy-heading" className="kg-section">
        Copy last cycle forward
      </h2>
      <p className="kg-cap">
        Published targets for the months chosen are copied twelve months on as drafts, with weights and caps carried over and the uplift
        applied to every target value.
      </p>
      <form
        className="kg-form"
        onSubmit={(e) => {
          e.preventDefault();
          void send(true);
        }}
      >
        <div className="kg-form-row">
          <TextField label="From month" type="month" value={from} onChange={(e) => setFrom(e.target.value)} required />
          <TextField label="To month" type="month" value={to} onChange={(e) => setTo(e.target.value)} required />
          <TextField label="Uplift, %" type="number" step="0.1" value={uplift} onChange={(e) => setUplift(e.target.value)} hint="5 adds 5% to every target." />
        </div>
        {error ? (
          <p role="alert" style={{ color: "var(--neg-ink)", fontSize: 13 }}>
            {error}
          </p>
        ) : null}
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button type="submit" className="kg-btn" disabled={busy || !from || !to}>
            {busy ? "Checking…" : "Preview"}
          </button>
          <button type="button" className="kg-btn kg-btn--primary" disabled={busy || !result?.accepted || result.written > 0} onClick={() => void send(false)}>
            Save as drafts
          </button>
          <button type="button" className="kg-btn" onClick={onClose}>
            Close
          </button>
        </div>
      </form>
      {result ? <SheetFindings result={result} /> : null}
    </section>
  );
}
