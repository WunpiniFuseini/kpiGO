import { useState, type FormEvent } from "react";

import type { Output } from "../../api/actions";
import { ApiError, invoke, isProposal } from "../../api/client";
import { useQuery } from "../../api/useAction";
import { Chip, DataTable, EmptyState, ErrorPanel, Loading, Notice, TableSkeleton, TextField } from "../../components";
import { currentPeriod, formatDateTime, formatPeriod, shiftPeriod } from "../../lib/format";
import { Page } from "../../shell/AppShell";
import { useMe } from "../../session/Session";

type Check = Output<"scorecard.close.check">;
type CloseIssue = Check["blockers"][number];
type Exclusion = Output<"scorecard.exclusion.list">["exclusions"][number];
type Snapshot = Output<"scorecard.snapshot.list">["snapshots"][number];

const STATUS_TONE: Record<string, "up" | "info" | "warn" | "flat"> = { open: "info", closing: "warn", closed: "up", restating: "warn" };

const KIND: Record<string, string> = {
  not_reported: "Not reported",
  no_target: "No target",
  no_fx_rate: "No FX rate",
  weights: "Weights",
  pending_overrides: "Pending overrides",
  feed: "Feed",
};

function monthValue(periodKey: string): string {
  return `${periodKey.slice(0, 4)}-${periodKey.slice(4)}`;
}

/** Administer → Business calendar: Scorecards period close and restatement (App Flow §7.2, §7.3). */
export function PeriodClosePage() {
  const me = useMe();
  const [period, setPeriod] = useState(shiftPeriod(currentPeriod(), -1));
  const [check, reloadCheck] = useQuery("scorecard.close.check", { period_key: period });
  const [exclusions, reloadExclusions] = useQuery("scorecard.exclusion.list", { period_key: period });
  const [snapshots, reloadSnapshots] = useQuery("scorecard.snapshot.list", { period_key: period });
  const reload = () => {
    reloadCheck();
    reloadExclusions();
    reloadSnapshots();
  };
  const canClose = me.permissions.includes("period.close");

  return (
    <Page title="Business calendar">
      <div className="kg-sechead">
        <div>
          <h2 className="kg-section">Scorecards period close</h2>
          <p className="kg-cap">Close freezes every scorecard for the month, inputs and all. After that only a restatement, with a reason, changes it.</p>
        </div>
        <span className="kg-spacer" />
        <div className="kg-form-row" style={{ maxWidth: 220 }}>
          <TextField
            label="Month"
            type="month"
            value={monthValue(period)}
            onChange={(e) => e.target.value && setPeriod(e.target.value.replace("-", ""))}
          />
        </div>
      </div>

      {check.status === "loading" ? (
        <section className="kg-card">
          <Loading label="Checking the period">
            <TableSkeleton rows={4} columns={4} />
          </Loading>
        </section>
      ) : check.status === "error" ? (
        <section className="kg-card">
          <ErrorPanel error={check.error} retry={reloadCheck} what="The close check" />
        </section>
      ) : (
        <CloseView check={check.data} canClose={canClose} onChanged={reload} />
      )}

      <section className="kg-card" aria-labelledby="exclusions-heading">
        <h2 id="exclusions-heading" className="kg-section">
          Exclusions
        </h2>
        {exclusions.status === "loading" ? (
          <Loading label="Loading exclusions">
            <TableSkeleton rows={2} columns={4} />
          </Loading>
        ) : exclusions.status === "error" ? (
          <ErrorPanel error={exclusions.error} retry={reloadExclusions} what="Exclusions" />
        ) : (
          <ExclusionsView
            exclusions={exclusions.data.exclusions}
            locked={check.status === "ready" && ["closed", "closing"].includes(check.data.status)}
            onChanged={reload}
          />
        )}
      </section>

      <section className="kg-card" aria-labelledby="versions-heading">
        <h2 id="versions-heading" className="kg-section">
          Versions
        </h2>
        {snapshots.status === "loading" ? (
          <Loading label="Loading versions">
            <TableSkeleton rows={2} columns={4} />
          </Loading>
        ) : snapshots.status === "error" ? (
          <ErrorPanel error={snapshots.error} retry={reloadSnapshots} what="Versions" />
        ) : (
          <VersionsView snapshots={snapshots.data.snapshots} period={period} />
        )}
      </section>
    </Page>
  );
}

export function CloseView({ check, canClose, onChanged }: { check: Check; canClose: boolean; onChanged: () => void }) {
  const [message, setMessage] = useState<{ tone: "info" | "neg"; text: string } | null>(null);
  const [excluding, setExcluding] = useState<CloseIssue | null>(null);
  const [restating, setRestating] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const closed = check.status === "closed";

  const fail = (e: unknown, fallback: string) => setMessage({ tone: "neg", text: e instanceof ApiError ? e.message : fallback });

  const close = async () => {
    setBusy(true);
    try {
      const out = await invoke("scorecard.period.close", { period_key: check.period_key }, { allowProposal: true });
      setMessage({
        tone: "info",
        text: isProposal(out) ? out.message : `${formatPeriod(check.period_key, true)} closed: version ${out.snapshot_version}, ${out.subjects} scorecard(s) frozen.`,
      });
      onChanged();
    } catch (e) {
      fail(e, "The period could not be closed.");
    } finally {
      setBusy(false);
    }
  };

  const exclude = async (event: FormEvent) => {
    event.preventDefault();
    if (!excluding?.metric_code) return;
    try {
      await invoke("scorecard.exclusion.add", { period_key: check.period_key, metric_code: excluding.metric_code, subject: null, reason });
      setMessage({ tone: "info", text: `${excluding.metric_code} excluded from ${formatPeriod(check.period_key, true)} wherever it is unscored.` });
      setExcluding(null);
      setReason("");
      onChanged();
    } catch (e) {
      fail(e, "The exclusion could not be recorded.");
    }
  };

  const restate = async (event: FormEvent) => {
    event.preventDefault();
    try {
      const out = await invoke("scorecard.period.restate", { period_key: check.period_key, reason }, { allowProposal: true });
      setMessage({ tone: "info", text: isProposal(out) ? out.message : `${formatPeriod(check.period_key, true)} is open for restatement.` });
      setRestating(false);
      setReason("");
      onChanged();
    } catch (e) {
      fail(e, "The period could not be reopened for restatement.");
    }
  };

  return (
    <section className="kg-card" aria-labelledby="close-heading">
      <div className="kg-sechead">
        <h2 id="close-heading" className="kg-section">
          {formatPeriod(check.period_key, true)}
        </h2>
        <Chip tone={STATUS_TONE[check.status] ?? "flat"}>{check.status}</Chip>
        {check.snapshot_version > 0 ? <span className="kg-cap">version {check.snapshot_version}</span> : null}
        <span className="kg-spacer" />
        {canClose && closed ? (
          <button type="button" className="kg-btn" aria-expanded={restating} onClick={() => setRestating(!restating)}>
            Restate
          </button>
        ) : null}
        {canClose && !closed && !check.refused ? (
          <button type="button" className="kg-btn kg-btn--primary" disabled={!check.ready || busy} onClick={() => void close()}>
            {busy ? "Closing…" : check.status === "restating" ? "Close restated period" : "Close period"}
          </button>
        ) : null}
      </div>

      {message ? (
        <Notice tone={message.tone} role={message.tone === "neg" ? "alert" : "status"}>
          {message.text}
        </Notice>
      ) : null}

      {check.refused ? (
        <EmptyState kind={closed ? "good" : "awaiting"} title={closed ? "This month is closed" : "This month cannot close yet"} headingLevel={3}>
          {check.refused}
        </EmptyState>
      ) : check.ready ? (
        <Notice tone="info" role="status" title="Ready to close.">
          {check.subjects} scorecard(s) will be frozen. {check.status === "restating" ? "The current version stays on record beside the new one." : ""}
        </Notice>
      ) : (
        <Notice tone="neg" title={`${check.blockers.length} thing(s) stop this month closing.`}>
          Load the missing data, or record an exclusion with a reason. Close freezes scores, so a partial month must be a decision, not an accident.
        </Notice>
      )}

      {check.blockers.length ? (
        <DataTable
          caption="What stops the close"
          columns={[
            { key: "kind", header: "Check", render: (i) => <Chip tone="down">{KIND[i.kind] ?? i.kind}</Chip> },
            { key: "message", header: "What is wrong", render: (i) => i.message },
            {
              key: "who",
              header: "Who",
              render: (i) => (i.subjects.length ? `${i.subjects.join(", ")}${i.count > i.subjects.length ? ` and ${i.count - i.subjects.length} more` : ""}` : "–"),
            },
            {
              key: "act",
              header: "Resolve",
              render: (i) =>
                canClose && i.metric_code ? (
                  <button type="button" className="kg-btn kg-btn--link" aria-label={`Exclude ${i.metric_code} where it is unscored`} onClick={() => setExcluding(i)}>
                    Exclude…
                  </button>
                ) : (
                  <span className="kg-cap">{i.kind === "weights" ? "Fix on the target workbench" : "–"}</span>
                ),
            },
          ]}
          rows={check.blockers}
          rowKey={(i) => `${i.kind}-${i.metric_code ?? i.profile_code}-${i.message}`}
          empty={null}
        />
      ) : null}

      {check.warnings.length ? (
        <DataTable
          caption="Worth a look before closing"
          columns={[
            { key: "kind", header: "Check", render: (i) => <Chip tone="warn">{KIND[i.kind] ?? i.kind}</Chip> },
            { key: "message", header: "Note", render: (i) => i.message },
          ]}
          rows={check.warnings}
          rowKey={(i) => `${i.kind}-${i.message}`}
          empty={null}
        />
      ) : null}

      {excluding ? (
        <form className="kg-form" onSubmit={exclude} aria-label={`Exclude ${excluding.metric_code}`}>
          <p className="kg-cap">
            {excluding.metric_code} will be left out of {formatPeriod(check.period_key, true)} for everyone it is unscored for. People with a score keep it.
          </p>
          <TextField label="Why exclude it?" required minLength={3} value={reason} onChange={(e) => setReason(e.target.value)} />
          <div style={{ display: "flex", gap: 8 }}>
            <button type="submit" className="kg-btn kg-btn--primary" disabled={reason.trim().length < 3}>
              Record exclusion
            </button>
            <button type="button" className="kg-btn" onClick={() => setExcluding(null)}>
              Cancel
            </button>
          </div>
        </form>
      ) : null}

      {restating ? (
        <form className="kg-form" onSubmit={restate} aria-label="Restate the period">
          <p className="kg-cap">The period reopens for corrected loads and approved overrides. Closing it again writes a new version; this one is kept.</p>
          <TextField label="Why restate it?" required minLength={3} value={reason} onChange={(e) => setReason(e.target.value)} />
          <div style={{ display: "flex", gap: 8 }}>
            <button type="submit" className="kg-btn kg-btn--primary" disabled={reason.trim().length < 3}>
              Reopen for restatement
            </button>
            <button type="button" className="kg-btn" onClick={() => setRestating(false)}>
              Cancel
            </button>
          </div>
        </form>
      ) : null}
    </section>
  );
}

export function ExclusionsView({ exclusions, locked, onChanged }: { exclusions: Exclusion[]; locked: boolean; onChanged: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const remove = async (id: string) => {
    try {
      await invoke("scorecard.exclusion.remove", { exclusion_id: id });
      onChanged();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The exclusion could not be removed.");
    }
  };
  return (
    <div className="kg-stack">
      {error ? (
        <Notice tone="neg" role="alert">
          {error}
        </Notice>
      ) : null}
      <DataTable
        caption="Exclusions for the month"
        captionHidden
        columns={[
          { key: "metric", header: "Metric", render: (x) => x.metric_name },
          { key: "who", header: "For", render: (x) => x.staff_no ?? "Everyone it is unscored for" },
          { key: "reason", header: "Reason", render: (x) => x.reason },
          { key: "when", header: "Recorded", render: (x) => formatDateTime(x.created_at) },
          {
            key: "act",
            header: "Actions",
            render: (x) =>
              locked ? (
                <span className="kg-cap">Part of the snapshot</span>
              ) : (
                <button type="button" className="kg-btn kg-btn--link" aria-label={`Remove the exclusion of ${x.metric_name}`} onClick={() => void remove(x.exclusion_id)}>
                  Remove
                </button>
              ),
          },
        ]}
        rows={exclusions}
        rowKey={(x) => x.exclusion_id}
        empty={
          <EmptyState kind="none" title="No exclusions" headingLevel={3}>
            Every metric counts this month. An exclusion is recorded only when an Admin decides a missing figure should not hold up the close.
          </EmptyState>
        }
      />
    </div>
  );
}

export function VersionsView({ snapshots, period }: { snapshots: Snapshot[]; period: string }) {
  return (
    <DataTable
      caption="Frozen versions, newest first"
      captionHidden
      columns={[
        { key: "v", header: "Version", numeric: true, render: (s) => s.snapshot_version },
        { key: "kind", header: "Kind", render: (s) => <Chip tone={s.kind === "restatement" ? "warn" : "up"}>{s.kind}</Chip> },
        { key: "when", header: "Written", render: (s) => formatDateTime(s.created_at) },
        { key: "people", header: "Scorecards", numeric: true, render: (s) => s.subjects.toLocaleString("en-GB") },
        { key: "reason", header: "Reason", render: (s) => s.reason || <span className="kg-cap">–</span> },
      ]}
      rows={snapshots}
      rowKey={(s) => s.snapshot_id}
      empty={
        <EmptyState kind="none" title={`${formatPeriod(period, true)} has not been closed`} headingLevel={3}>
          Until it closes, every scorecard for the month is provisional and computed from the latest data.
        </EmptyState>
      }
    />
  );
}
