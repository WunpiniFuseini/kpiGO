import { useId, useMemo, useState } from "react";

import type { Output } from "../../api/actions";
import { ApiError, invoke, isProposal } from "../../api/client";
import { useQuery } from "../../api/useAction";
import { Chip, DataTable, EmptyState, ErrorPanel, Loading, Notice, TableSkeleton } from "../../components";
import { formatDate, formatDateTime, formatPeriod } from "../../lib/format";
import { Page } from "../../shell/AppShell";
import { useMe } from "../../session/Session";

export type Tasks = Output<"input.task.list">;
export type Escalated = Output<"input.escalation.list">;
type Task = Tasks["tasks"][number];

const STATE: Record<string, { label: string; tone: "flat" | "info" | "up" | "warn" }> = {
  pending: { label: "Not started", tone: "flat" },
  draft: { label: "Draft saved", tone: "info" },
  submitted: { label: "Submitted", tone: "up" },
  restated: { label: "Restated", tone: "warn" },
};

/** The last moment to enter, said the way people read a deadline: the due day itself. */
function dueDay(dueAt: string): string {
  return formatDate(new Date(new Date(dueAt).getTime() - 1));
}

function daysLeft(dueAt: string, now: Date): number {
  return Math.ceil((new Date(dueAt).getTime() - now.getTime()) / 86_400_000);
}

/** My inputs: the contributor's landing page (Design Brief §5 ContributorTasklist, InputGrid). */
export function MyInputsPage() {
  const me = useMe();
  const blocked = me.no_access.find((n) => n.page_key === "my_inputs");
  const submits = me.permissions.includes("input.submit");
  const follows = me.permissions.includes("input.followup");
  return (
    <Page title="My inputs">
      {blocked ? (
        <EmptyState kind="no-access" title="You cannot enter inputs yet" ask={blocked.ask}>
          {blocked.missing}
        </EmptyState>
      ) : (
        <div className="kg-stack">
          {submits ? <MyTasks /> : null}
          {follows ? <EscalatedSection alone={!submits} /> : null}
        </div>
      )}
    </Page>
  );
}

function MyTasks() {
  const [period, setPeriod] = useState<string | null>(null);
  const [tasks, reload] = useQuery("input.task.list", { period_key: period });
  return tasks.status === "loading" ? (
    <section className="kg-card">
      <Loading label="Loading your inputs">
        <TableSkeleton rows={4} columns={4} />
      </Loading>
    </section>
  ) : tasks.status === "error" ? (
    <section className="kg-card">
      <ErrorPanel error={tasks.error} retry={reload} what="Your inputs" />
    </section>
  ) : (
    <InputsView key={`${tasks.data.period_key}-${tasks.data.tasks.map((t) => `${t.state}${t.version}`).join()}`} tasks={tasks.data} onChanged={reload} onPeriod={setPeriod} />
  );
}

function EscalatedSection({ alone }: { alone: boolean }) {
  const [list, reload] = useQuery("input.escalation.list", {});
  if (list.status === "loading") {
    return alone ? (
      <section className="kg-card">
        <Loading label="Loading escalated inputs">
          <TableSkeleton rows={3} columns={5} />
        </Loading>
      </section>
    ) : null;
  }
  if (list.status === "error") {
    return (
      <section className="kg-card">
        <ErrorPanel error={list.error} retry={reload} what="Inputs escalated to you" />
      </section>
    );
  }
  return <EscalatedView escalated={list.data} alone={alone} />;
}

const RUNG: Record<number, string> = {
  2: "You are their line manager",
  3: "You are a stakeholder",
};

/** Inputs other people owe that the escalation ladder has brought to you (PRD MI-8). */
export function EscalatedView({ escalated, alone }: { escalated: Escalated; alone: boolean }) {
  if (!escalated.items.length) {
    // Beside a contributor's own grid, an empty list is noise; on its own it says why it is empty.
    return alone ? (
      <section className="kg-card">
        <EmptyState kind="good" title="Nothing has been escalated to you">
          When someone in your team, or a slice you are a stakeholder for, misses an input, it appears here until it is submitted.
        </EmptyState>
      </section>
    ) : null;
  }
  return (
    <section className="kg-card" aria-labelledby="escalated-heading">
      <h2 id="escalated-heading" className="kg-section">
        Escalated to you
      </h2>
      <p className="kg-cap">These inputs are still owed. kpiGo told you because the contributor has not submitted them; each leaves this list once it is submitted.</p>
      <DataTable
        caption="Inputs escalated to you"
        captionHidden
        columns={[
          { key: "metric", header: "Metric", render: (i) => i.metric_name },
          { key: "slice", header: "For", render: (i) => i.scope_label },
          { key: "who", header: "Owed by", render: (i) => i.contributor_name ?? <Chip tone="warn">Nobody: no line manager</Chip> },
          { key: "month", header: "Month", render: (i) => formatPeriod(i.period_key, true) },
          {
            key: "why",
            header: "Why you",
            render: (i) => (
              <>
                {RUNG[i.step] ?? "Escalated"}
                <span className="kg-msub">Told {formatDate(i.escalated_at)}</span>
              </>
            ),
          },
          {
            key: "state",
            header: "Status",
            render: (i) =>
              i.locked ? (
                <Chip tone="down">Missed the deadline</Chip>
              ) : (
                <>
                  <Chip tone="warn">{i.state === "draft" ? "Draft only" : "Not submitted"}</Chip>
                  <span className="kg-msub">Due by the end of {dueDay(i.due_at)}</span>
                </>
              ),
          },
        ]}
        rows={escalated.items}
        rowKey={(i) => `${i.assignment_id}-${i.period_key}`}
        empty={null}
      />
    </section>
  );
}

type Draft = { value: string; note: string };

function problem(task: Task, value: string): string | null {
  if (value.trim() === "") return null;
  const n = Number(value);
  if (!Number.isFinite(n)) return "Enter a number, e.g. 4.2.";
  const places = value.includes(".") ? value.split(".")[1].length : 0;
  if (places > 4) return "Use at most 4 decimal places.";
  if (Math.abs(n) >= 1e14) return "That number is too large to store.";
  if (task.unit === "percent" && (n < 0 || n > 100)) return "A percentage is between 0 and 100.";
  return null;
}

/** The tasklist and grid for one month, from data: what stories and tests render. */
export function InputsView({ tasks, onChanged, onPeriod, now = new Date() }: { tasks: Tasks; onChanged: () => void; onPeriod: (p: string) => void; now?: Date }) {
  const initial = useMemo(() => Object.fromEntries(tasks.tasks.map((t) => [t.assignment_id, { value: t.value === null ? "" : Number.isFinite(Number(t.value)) ? String(Number(t.value)) : t.value, note: t.note }])), [tasks]);
  const [drafts, setDrafts] = useState<Record<string, Draft>>(initial);
  const [message, setMessage] = useState<{ tone: "info" | "neg"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const month = formatPeriod(tasks.period_key, true);
  const editable = !tasks.locked || tasks.restating;
  const left = daysLeft(tasks.due_at, now);

  const changed = (t: Task) => drafts[t.assignment_id].value !== initial[t.assignment_id].value || drafts[t.assignment_id].note !== initial[t.assignment_id].note;
  const errors = Object.fromEntries(tasks.tasks.map((t) => [t.assignment_id, problem(t, drafts[t.assignment_id].value)]));
  const invalid = Object.values(errors).some(Boolean);
  // Submit everything filled in that is new or changed; drafts only for what was never submitted.
  const toSubmit = tasks.tasks.filter((t) => drafts[t.assignment_id].value.trim() !== "" && (t.state === "pending" || t.state === "draft" || changed(t)));
  const toSave = tasks.tasks.filter((t) => (t.state === "pending" || t.state === "draft") && changed(t));
  const outstanding = tasks.tasks.filter((t) => t.state === "pending" || t.state === "draft").length;

  const send = async (submit: boolean) => {
    setBusy(true);
    setMessage(null);
    const rows = submit ? toSubmit : toSave;
    const entries = rows.map((t) => ({ assignment_id: t.assignment_id, value: drafts[t.assignment_id].value.trim() || null, note: drafts[t.assignment_id].note }));
    try {
      if (submit) {
        const out = await invoke("input.submit", { period_key: tasks.period_key, entries }, { allowProposal: true });
        setMessage({ tone: "info", text: isProposal(out) ? out.message : `${rows.length} input(s) submitted for ${month}.` });
      } else {
        await invoke("input.save", { period_key: tasks.period_key, entries });
        setMessage({ tone: "info", text: `${rows.length} draft(s) saved. Nothing reaches a scorecard until you submit.` });
      }
      onChanged();
    } catch (e) {
      setMessage({ tone: "neg", text: e instanceof ApiError ? e.message : "Your inputs could not be sent. Nothing was lost; try again." });
    } finally {
      setBusy(false);
    }
  };

  if (tasks.tasks.length === 0) {
    return (
      <>
        <EmptyState kind="good" title={`Nothing due for ${month}`}>
          You have no inputs to enter this month. When an Admin asks you for one, it appears here with its deadline.
        </EmptyState>
        <OtherPeriods tasks={tasks} onPeriod={onPeriod} />
      </>
    );
  }

  return (
    <div className="kg-stack">
      <section className="kg-card kg-due" aria-labelledby="due-heading">
        <div className="kg-sechead">
          <div>
            <h2 id="due-heading" className="kg-section">
              {outstanding ? `${outstanding} of ${tasks.tasks.length} input(s) still to submit for ${month}` : `All ${tasks.tasks.length} input(s) submitted for ${month}`}
            </h2>
            <p className="kg-cap">
              Due by the end of {dueDay(tasks.due_at)}. What you enter feeds the scorecards of the people in each row; they see it only once the month closes.
            </p>
          </div>
          <span className="kg-spacer" />
          {tasks.locked ? <Chip tone={tasks.restating ? "warn" : "flat"}>{tasks.restating ? "Being restated" : "Locked"}</Chip> : <Chip tone={left <= 2 ? "warn" : "info"}>{left <= 0 ? "Due today" : `${left} day(s) left`}</Chip>}
        </div>
        {tasks.locked && !tasks.restating ? (
          <Notice tone="warn" title="The deadline has passed.">
            These values are locked. A correction is now a restatement: ask an Admin to restate {month}.
          </Notice>
        ) : null}
        {tasks.restating ? (
          <Notice tone="warn" title={`${month} is being restated.`}>
            Submitting a change writes a new version; the earlier value stays on record.
          </Notice>
        ) : null}
        {message ? (
          <Notice tone={message.tone} role={message.tone === "neg" ? "alert" : "status"}>
            {message.text}
          </Notice>
        ) : null}
      </section>

      <section className="kg-card" aria-labelledby="grid-heading">
        <h2 id="grid-heading" className="sr-only">
          Inputs for {month}
        </h2>
        <div className="kg-table-wrap">
          <table className="kg-table kg-igrid">
            <caption className="sr-only">Enter a value for each metric; a note is optional.</caption>
            <thead>
              <tr>
                <th scope="col">Metric</th>
                <th scope="col">For</th>
                <th scope="col" className="is-num">
                  Value
                </th>
                <th scope="col">Note (optional)</th>
                <th scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              {tasks.tasks.map((t) => (
                <InputRow
                  key={t.assignment_id}
                  task={t}
                  draft={drafts[t.assignment_id]}
                  error={errors[t.assignment_id]}
                  editable={editable}
                  onChange={(d) => setDrafts({ ...drafts, [t.assignment_id]: d })}
                />
              ))}
            </tbody>
          </table>
        </div>
        {editable ? (
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 14, flexWrap: "wrap" }}>
            <button type="button" className="kg-btn kg-btn--primary" disabled={busy || invalid || toSubmit.length === 0} onClick={() => void send(true)}>
              {busy ? "Sending…" : toSubmit.length ? `Submit ${toSubmit.length}` : "Submit"}
            </button>
            {tasks.restating ? null : (
              <button type="button" className="kg-btn" disabled={busy || invalid || toSave.length === 0} onClick={() => void send(false)}>
                Save draft
              </button>
            )}
            <span className="kg-cap">Drafts stay private to you. Submitted values can be changed until the deadline.</span>
          </div>
        ) : null}
      </section>
      <OtherPeriods tasks={tasks} onPeriod={onPeriod} />
    </div>
  );
}

function InputRow({ task: t, draft, error, editable, onChange }: { task: Task; draft: Draft; error: string | null; editable: boolean; onChange: (d: Draft) => void }) {
  const id = useId();
  const state = STATE[t.state] ?? { label: t.state, tone: "flat" as const };
  return (
    <tr>
      <th scope="row">
        <span className="kg-mname">
          {t.metric_name}
        </span>
        <span className="kg-msub">
          {t.unit} · {t.direction === "lower_is_better" ? "lower is better" : "higher is better"}
          {t.description ? ` · ${t.description}` : ""}
        </span>
      </th>
      <td>
        {t.scope_label}
        <span className="kg-msub">
          Lands on {t.members} {t.members === 1 ? "person" : "people"}
        </span>
      </td>
      <td className="is-num">
        <input
          className="kg-input kg-ival num"
          inputMode="decimal"
          aria-label={`Value for ${t.metric_name}, ${t.scope_label}`}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${id}-err` : undefined}
          value={draft.value}
          readOnly={!editable}
          onChange={(e) => onChange({ ...draft, value: e.target.value })}
        />
        {error ? (
          <span id={`${id}-err`} className="kg-field-error" role="alert">
            {error}
          </span>
        ) : null}
      </td>
      <td>
        <input
          className="kg-input"
          aria-label={`Note for ${t.metric_name}, ${t.scope_label}`}
          value={draft.note}
          maxLength={1000}
          readOnly={!editable}
          onChange={(e) => onChange({ ...draft, note: e.target.value })}
        />
      </td>
      <td>
        <Chip tone={state.tone}>{state.label}</Chip>
        <span className="kg-msub">
          {t.submitted_at ? `${formatDateTime(t.submitted_at)}${t.version && t.version > 1 ? ` · v${t.version}` : ""}` : chased(t)}
        </span>
      </td>
    </tr>
  );
}

/** How far up the escalation ladder an unsubmitted input has gone, in the contributor's words. */
function chased(t: Task): string {
  if (t.escalation_step >= 3) return "Overdue: the stakeholders were told";
  if (t.escalation_step === 2) return "Your line manager was told";
  return t.reminded_at ? `Reminder sent ${formatDate(t.reminded_at)}` : "";
}

function OtherPeriods({ tasks, onPeriod }: { tasks: Tasks; onPeriod: (p: string) => void }) {
  if (!tasks.other_periods.length) return null;
  return (
    <Notice tone="info" title="Also open:">
      {tasks.other_periods.map((p, i) => (
        <span key={p}>
          {i ? ", " : ""}
          <button type="button" className="kg-btn kg-btn--link" onClick={() => onPeriod(p)}>
            {formatPeriod(p, true)}
          </button>
        </span>
      ))}{" "}
      still has inputs you owe.
    </Notice>
  );
}
