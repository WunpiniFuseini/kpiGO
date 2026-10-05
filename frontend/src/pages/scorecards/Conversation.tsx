import { useId, useState, type FormEvent } from "react";

import { ApiError, invoke } from "../../api/client";
import { useQuery } from "../../api/useAction";
import { Chip, DataTable, EmptyState, ErrorPanel, GradePill, Loading, Notice, SelectField, TableSkeleton } from "../../components";
import { formatDate, formatDateTime, formatPeriod } from "../../lib/format";
import { useMe } from "../../session/Session";
import { points, toneFor, type Card, type History, type Interaction, type QueryList as Queries, type ScoreBand, type Thread } from "./model";

function errorText(e: unknown, fallback: string): string {
  return e instanceof ApiError ? e.message : fallback;
}

function TextArea({ label, value, onChange, hint }: { label: string; value: string; onChange: (v: string) => void; hint?: string }) {
  const id = useId();
  return (
    <div className="kg-field">
      <label htmlFor={id}>{label}</label>
      <textarea id={id} className="kg-textarea" rows={3} required minLength={3} value={value} onChange={(e) => onChange(e.target.value)} aria-describedby={hint ? `${id}-hint` : undefined} />
      {hint ? (
        <span id={`${id}-hint`} className="kg-hint">
          {hint}
        </span>
      ) : null}
    </div>
  );
}

/** Acknowledgement, queries and manager commentary for the scorecard on screen. */
export function Conversation({ card, thread, onChanged }: { card: Card; thread: Thread | null; onChanged: () => void }) {
  if (thread === null) {
    return (
      <section className="kg-card">
        <Loading label="Loading acknowledgement and queries">
          <TableSkeleton rows={2} columns={3} />
        </Loading>
      </section>
    );
  }
  return (
    <>
      <AckBar thread={thread} onChanged={onChanged} />
      <section className="kg-card" aria-labelledby="queries-heading">
        <h2 id="queries-heading" className="kg-section">
          Queries
        </h2>
        <p className="kg-cap">
          A query asks about one figure. It goes to the line manager and never reopens the month: the answer either explains the figure or points to the override that will change it.
        </p>
        <QueryList queries={thread.queries} self={thread.is_self} onChanged={onChanged} />
        {thread.can_query ? <RaiseQuery card={card} onChanged={onChanged} /> : null}
      </section>
      <section className="kg-card" aria-labelledby="comments-heading">
        <h2 id="comments-heading" className="kg-section">
          Manager commentary
        </h2>
        {thread.comments.length ? (
          <ul className="kg-thread">
            {thread.comments.map((c) => (
              <li key={c.interaction_id}>
                <p>{c.body}</p>
                <span className="kg-cap">
                  {c.author_name ?? "A manager"} · {formatDateTime(c.created_at)}
                  {c.visibility === "managers" ? " · managers only" : ""}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState kind="none" title="No commentary yet" headingLevel={3}>
            {thread.is_self ? "When your manager comments on this month, it appears here." : "Nobody has commented on this month yet."}
          </EmptyState>
        )}
        {thread.can_comment ? <AddComment card={card} onChanged={onChanged} /> : null}
      </section>
    </>
  );
}

export function AckBar({ thread, onChanged }: { thread: Thread; onChanged: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const acknowledge = async () => {
    setBusy(true);
    try {
      await invoke("scorecard.acknowledge", { period_key: thread.period_key });
      onChanged();
    } catch (e) {
      setError(errorText(e, "The acknowledgement could not be recorded."));
    } finally {
      setBusy(false);
    }
  };
  const who = thread.is_self ? "You" : "They";
  return (
    <section className="kg-card kg-ack" aria-label="Acknowledgement">
      {error ? (
        <Notice tone="neg" role="alert">
          {error}
        </Notice>
      ) : null}
      {thread.acknowledged ? (
        <p>
          <Chip tone="up">Acknowledged</Chip> {who} acknowledged version {thread.snapshot_version} on {formatDate(thread.acknowledged_at ?? "")}.
          <span className="kg-cap"> Acknowledging means seen, not agreed.</span>
        </p>
      ) : thread.snapshot_version === null ? (
        <p className="kg-cap">{thread.note ?? "Acknowledgement opens once the month is closed and published."}</p>
      ) : (
        <div className="kg-row" style={{ gap: 12, flexWrap: "wrap" }}>
          <p style={{ margin: 0, flex: 1 }}>
            {thread.acknowledged_version ? (
              <>
                <Chip tone="warn">Restated</Chip> {who} acknowledged version {thread.acknowledged_version}; this month has been restated since.{" "}
              </>
            ) : (
              <>
                <Chip tone="flat">Not acknowledged</Chip>{" "}
              </>
            )}
            <span className="kg-cap">Acknowledging records that the scorecard has been seen. It does not mean agreement: raise a query for that.</span>
          </p>
          {thread.can_acknowledge ? (
            <button type="button" className="kg-btn kg-btn--primary" disabled={busy} onClick={() => void acknowledge()}>
              {busy ? "Recording…" : `Acknowledge version ${thread.snapshot_version}`}
            </button>
          ) : null}
        </div>
      )}
    </section>
  );
}

function status(q: Interaction) {
  if (!q.resolved_at) return <Chip tone="warn">Open</Chip>;
  return <Chip tone={q.outcome === "adjusted" ? "info" : "up"}>{q.outcome === "adjusted" ? "Adjusted" : "Explained"}</Chip>;
}

export function QueryList({ queries, self, onChanged, showWho = false }: { queries: Interaction[]; self: boolean; onChanged: () => void; showWho?: boolean }) {
  const me = useMe();
  const [resolving, setResolving] = useState<string | null>(null);
  const canResolve = !self && me.permissions.includes("scorecard.query.resolve");
  return (
    <>
      <DataTable
        caption="Queries"
        captionHidden
        columns={[
          { key: "metric", header: "Figure", render: (q) => (showWho ? `${q.full_name} · ${q.metric_name}` : (q.metric_name ?? "–")) },
          { key: "body", header: "Question", render: (q) => q.body },
          { key: "routed", header: "With", render: (q) => q.routed_to_name ?? "Admin queue (no line manager)" },
          { key: "raised", header: "Raised", render: (q) => `${formatPeriod(q.period_key)} · ${formatDateTime(q.created_at)}` },
          {
            key: "status",
            header: "Status",
            render: (q) => (
              <div className="kg-stack" style={{ gap: 4, alignItems: "flex-start" }}>
                {status(q)}
                {q.resolution ? (
                  <span className="kg-cap">
                    {q.resolution}
                    {q.resolved_by_name ? ` (${q.resolved_by_name})` : ""}
                  </span>
                ) : null}
                {canResolve && !q.resolved_at ? (
                  <button type="button" className="kg-btn kg-btn--link" aria-expanded={resolving === q.interaction_id} onClick={() => setResolving(resolving === q.interaction_id ? null : q.interaction_id)}>
                    Answer…
                  </button>
                ) : null}
              </div>
            ),
          },
        ]}
        rows={queries}
        rowKey={(q) => q.interaction_id}
        empty={
          <EmptyState kind="good" title="No queries" headingLevel={3}>
            {self ? "You have not queried any figure this month." : "Nobody has queried a figure here."}
          </EmptyState>
        }
      />
      {resolving ? (
        <ResolveQuery
          query={queries.find((q) => q.interaction_id === resolving)!}
          onDone={() => {
            setResolving(null);
            onChanged();
          }}
          onCancel={() => setResolving(null)}
        />
      ) : null}
    </>
  );
}

function RaiseQuery({ card, onChanged }: { card: Card; onChanged: () => void }) {
  const [metric, setMetric] = useState(card.metrics[0]?.metric_code ?? "");
  const [body, setBody] = useState("");
  const [error, setError] = useState<string | null>(null);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    try {
      await invoke("scorecard.query.raise", { period_key: card.period_key, metric_code: metric, body });
      setBody("");
      setError(null);
      onChanged();
    } catch (e) {
      setError(errorText(e, "The query could not be sent."));
    }
  };
  return (
    <form className="kg-form" onSubmit={submit} aria-label="Query a figure">
      <h3 className="kg-eyebrow">Query a figure</h3>
      {error ? (
        <Notice tone="neg" role="alert">
          {error}
        </Notice>
      ) : null}
      <SelectField label="Metric" value={metric} onChange={(e) => setMetric(e.target.value)} options={card.metrics.map((m) => ({ value: m.metric_code, label: m.display_name }))} />
      <TextArea label="What looks wrong?" value={body} onChange={setBody} hint="Say what you expected and why. Your line manager sees it." />
      <div>
        <button type="submit" className="kg-btn kg-btn--primary" disabled={body.trim().length < 3}>
          Send query
        </button>
      </div>
    </form>
  );
}

function ResolveQuery({ query, onDone, onCancel }: { query: Interaction; onDone: () => void; onCancel: () => void }) {
  const [outcome, setOutcome] = useState<"explained" | "adjusted">("explained");
  const [resolution, setResolution] = useState("");
  const [override, setOverride] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [overrides] = useQuery("override.list", {
    status: null,
    metric_code: query.metric_code,
    scope_type: "subject",
    period_key: query.period_key,
    subject_id: query.subject_id,
    limit: 50,
  });
  const usable = overrides.status === "ready" ? overrides.data.overrides.filter((o) => o.status === "pending" || o.status === "approved") : [];
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    try {
      await invoke("scorecard.query.resolve", {
        interaction_id: query.interaction_id,
        outcome,
        resolution,
        override_id: outcome === "adjusted" ? override || null : null,
      });
      onDone();
    } catch (e) {
      setError(errorText(e, "The answer could not be recorded."));
    }
  };
  return (
    <form className="kg-form" onSubmit={submit} aria-label={`Answer the query on ${query.metric_name}`}>
      <h3 className="kg-eyebrow">
        Answer {query.full_name} on {query.metric_name}
      </h3>
      {error ? (
        <Notice tone="neg" role="alert">
          {error}
        </Notice>
      ) : null}
      <SelectField
        label="Outcome"
        value={outcome}
        onChange={(e) => setOutcome(e.target.value as "explained" | "adjusted")}
        options={[
          { value: "explained", label: "Explained: the figure stands" },
          { value: "adjusted", label: "Adjusted: an override changes it" },
        ]}
      />
      {outcome === "adjusted" ? (
        usable.length ? (
          <SelectField
            label="Override"
            value={override}
            onChange={(e) => setOverride(e.target.value)}
            options={[{ value: "", label: "Choose the override…" }, ...usable.map((o) => ({ value: o.override_id, label: `${o.change_type} → ${o.override_value ?? o.override_text} (${o.status})` }))]}
          />
        ) : (
          <Notice tone="warn">
            There is no pending or approved override on {query.metric_name} for {query.full_name} in {formatPeriod(query.period_key)}. Request one under Targets → Overrides first, then answer here.
          </Notice>
        )
      ) : null}
      <TextArea label="Answer" value={resolution} onChange={setResolution} hint={`${query.full_name} sees this answer.`} />
      <div style={{ display: "flex", gap: 8 }}>
        <button type="submit" className="kg-btn kg-btn--primary" disabled={resolution.trim().length < 3 || (outcome === "adjusted" && !override)}>
          Send answer
        </button>
        <button type="button" className="kg-btn" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function AddComment({ card, onChanged }: { card: Card; onChanged: () => void }) {
  const [body, setBody] = useState("");
  const [visibility, setVisibility] = useState<"subject" | "managers">("subject");
  const [error, setError] = useState<string | null>(null);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    try {
      await invoke("scorecard.comment.add", { subject_id: card.subject_id, period_key: card.period_key, body, visibility });
      setBody("");
      setError(null);
      onChanged();
    } catch (e) {
      setError(errorText(e, "The comment could not be added."));
    }
  };
  return (
    <form className="kg-form" onSubmit={submit} aria-label="Add a comment">
      {error ? (
        <Notice tone="neg" role="alert">
          {error}
        </Notice>
      ) : null}
      <TextArea label={`Comment on ${card.full_name}'s month`} value={body} onChange={setBody} />
      <SelectField
        label="Who sees it"
        value={visibility}
        onChange={(e) => setVisibility(e.target.value as "subject" | "managers")}
        options={[
          { value: "subject", label: `${card.full_name} and their managers` },
          { value: "managers", label: "Managers only" },
        ]}
      />
      <div>
        <button type="submit" className="kg-btn kg-btn--primary" disabled={body.trim().length < 3}>
          Add comment
        </button>
      </div>
    </form>
  );
}

/** Line managers and Admins: open queries on people they can see, theirs first. */
export function QueryQueue() {
  const [list, reload] = useQuery("scorecard.query.list", { status: "open", period_key: null });
  return (
    <section className="kg-card" aria-labelledby="queue-heading">
      <h2 id="queue-heading" className="kg-section">
        Queries to answer
      </h2>
      {list.status === "loading" ? (
        <Loading label="Loading queries">
          <TableSkeleton rows={2} columns={5} />
        </Loading>
      ) : list.status === "error" ? (
        <ErrorPanel error={list.error} retry={reload} what="Queries" />
      ) : (
        <QueueView list={list.data} onChanged={reload} />
      )}
    </section>
  );
}

export function QueueView({ list, onChanged }: { list: Queries; onChanged: () => void }) {
  return (
    <>
      {list.routed_to_me ? <p className="kg-cap">{list.routed_to_me} routed to you as line manager.</p> : null}
      <QueryList queries={list.queue} self={false} onChanged={onChanged} showWho />
    </>
  );
}

/** Period by period: frozen where closed, provisional where open, and gaps said out loud. */
export function HistoryView({ history, bands }: { history: History | null; bands: ScoreBand[] }) {
  return (
    <section className="kg-card" aria-labelledby="history-heading">
      <h2 id="history-heading" className="kg-section">
        History
      </h2>
      {history === null ? (
        <Loading label="Loading history">
          <TableSkeleton rows={3} columns={4} />
        </Loading>
      ) : (
        <>
          <DataTable
            caption="Score by month"
            captionHidden
            columns={[
              { key: "period", header: "Month", render: (p) => formatPeriod(p.period_key) },
              {
                key: "band",
                header: "Band",
                render: (p) => {
                  const i = bands.findIndex((b) => b.label === p.band_label);
                  return p.band_label ? <GradePill tone={toneFor(i >= 0 ? i : (p.band_ramp_position ?? 1) - 1, bands.length)} label={p.band_label} /> : <Chip tone="flat">Not graded</Chip>;
                },
              },
              { key: "points", header: "Graded points", numeric: true, render: (p) => <span className="num">{points(p.graded_score)}</span> },
              { key: "metrics", header: "Metrics scored", numeric: true, render: (p) => `${p.metrics_scored} / ${p.metrics_total}` },
              {
                key: "source",
                header: "Footing",
                render: (p) => (p.source === "snapshot" ? <Chip tone="up">{`Closed · v${p.snapshot_version}`}</Chip> : <Chip tone="info">Provisional</Chip>),
              },
            ]}
            rows={[...history.points].reverse()}
            rowKey={(p) => p.period_key}
            empty={
              <EmptyState kind="none" title="No earlier months" headingLevel={3}>
                There is no scorecard history in this window yet.
              </EmptyState>
            }
          />
          {history.missing.length ? <p className="kg-cap">No scorecard in {history.missing.map((k) => formatPeriod(k)).join(", ")}: no role was in force, so nothing was scored.</p> : null}
        </>
      )}
    </section>
  );
}
