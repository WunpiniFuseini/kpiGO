import { useState, type FormEvent } from "react";

import type { Output } from "../../api/actions";
import { ApiError, invoke } from "../../api/client";
import { useQuery } from "../../api/useAction";
import { Chip, DataTable, EmptyState, ErrorPanel, Loading, Notice, SelectField, TableSkeleton, TextField } from "../../components";
import { currentPeriod, formatDate, formatDateTime, formatPeriod, shiftPeriod } from "../../lib/format";

export type Assignments = Output<"input.assignment.list">;
type Assignment = Assignments["assignments"][number];
type ManualMetric = { metric_code: string; display_name: string };
type Person = { user_id: string; display_name: string; roles: string[] };

const STATE: Record<string, { label: string; tone: "flat" | "info" | "up" | "warn" }> = {
  pending: { label: "Not started", tone: "flat" },
  draft: { label: "Draft only", tone: "warn" },
  submitted: { label: "Submitted", tone: "up" },
  restated: { label: "Restated", tone: "warn" },
};

const SCOPE_HINT: Record<string, string> = {
  subject: "A staff number, e.g. E1001.",
  profile: "A profile code, e.g. sme_rm. One value for everyone on it.",
  dimension: "A team as dimension:code, e.g. branch:ACC. One value for the whole team.",
};

/** Scorecard setup → Manual input: who enters each manual metric (PRD MI-1, MI-2, MI-2a). */
export function ManualInputSection() {
  const [period, setPeriod] = useState(shiftPeriod(currentPeriod(), -1));
  const [list, reload] = useQuery("input.assignment.list", { period_key: period });
  const [metrics] = useQuery("metric.list", { product: "scorecards", status: "active", as_of: null });
  const manual = metrics.status === "ready" ? metrics.data.metrics.filter((m) => m.collection_method === "manual_input") : [];
  return (
    <section className="kg-card" aria-labelledby="manual-heading">
      <div className="kg-sechead">
        <div>
          <h2 id="manual-heading" className="kg-section">
            Manual input
          </h2>
          <p className="kg-cap">Metrics no feed carries (CSAT, NPS, ESG) are entered by named contributors. Each slice has one contributor; their value lands on everyone in it.</p>
        </div>
        <span className="kg-spacer" />
        <div style={{ maxWidth: 200 }}>
          <TextField label="Month" type="month" value={`${period.slice(0, 4)}-${period.slice(4)}`} onChange={(e) => e.target.value && setPeriod(e.target.value.replace("-", ""))} />
        </div>
      </div>
      {list.status === "loading" ? (
        <Loading label="Loading manual input">
          <TableSkeleton rows={3} columns={5} />
        </Loading>
      ) : list.status === "error" ? (
        <ErrorPanel error={list.error} retry={reload} what="Manual input" />
      ) : (
        <ManualInputView
          list={list.data}
          metrics={manual.map((m) => ({ metric_code: m.metric_code, display_name: m.display_name }))}
          onChanged={reload}
        />
      )}
    </section>
  );
}

export function ManualInputView({
  list,
  metrics,
  onChanged,
  users,
}: {
  list: Assignments;
  metrics: ManualMetric[];
  onChanged: () => void;
  users?: Person[];
}) {
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const end = async (a: Assignment) => {
    const next = shiftPeriod(currentPeriod(), 1);
    try {
      await invoke("input.assignment.end", { assignment_id: a.assignment_id, effective_to: `${next.slice(0, 4)}-${next.slice(4)}-01` });
      onChanged();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The assignment could not be ended.");
    }
  };
  const month = formatPeriod(list.period_key, true);
  return (
    <div className="kg-stack">
      <p className="kg-cap">
        {month} inputs {list.locked ? "locked" : "are due by the end of"} {formatDate(new Date(new Date(list.due_at).getTime() - 1))}.{" "}
        {list.email_reminders
          ? "Contributors who still owe an input get one reminder, in kpiGo and by email."
          : "Contributors who still owe an input get one reminder in kpiGo. Email is off because no mail relay is set for this install; your infrastructure team can set one."}
      </p>
      {list.unassigned.length ? (
        <Notice tone="warn" title="Nobody is asked to enter:">
          {list.unassigned.map((m) => m.metric_name).join(", ")}. Until someone is, these metrics stay unreported and the month cannot close without excluding them.
        </Notice>
      ) : null}
      {error ? (
        <Notice tone="neg" role="alert">
          {error}
        </Notice>
      ) : null}
      <DataTable
        caption={`Manual input assignments, ${month}`}
        captionHidden
        columns={[
          { key: "metric", header: "Metric", render: (a) => a.metric_name },
          { key: "slice", header: "Slice", render: (a) => `${a.scope_label} (${a.members} ${a.members === 1 ? "person" : "people"})` },
          {
            key: "who",
            header: "Contributor",
            render: (a) => (
              <>
                {a.contributor_name ?? <Chip tone="warn">Nobody: no line manager</Chip>}
                {a.assignee_type === "role_relative" ? <span className="kg-msub">Their line manager, each month</span> : null}
              </>
            ),
          },
          {
            key: "state",
            header: month,
            render: (a) => (
              <>
                <Chip tone={STATE[a.state]?.tone ?? "flat"}>{STATE[a.state]?.label ?? a.state}</Chip>
                {a.submitted_at ? <span className="kg-msub">{formatDateTime(a.submitted_at)}</span> : null}
              </>
            ),
          },
          {
            key: "act",
            header: "Actions",
            render: (a) =>
              a.effective_to ? (
                <span className="kg-cap">Ends {formatDate(a.effective_to)}</span>
              ) : (
                <button type="button" className="kg-btn kg-btn--link" aria-label={`End ${a.metric_name} for ${a.scope_label} after this month`} onClick={() => void end(a)}>
                  End after this month
                </button>
              ),
          },
        ]}
        rows={list.assignments}
        rowKey={(a) => a.assignment_id}
        empty={
          <EmptyState kind="none" title="No manual input is assigned" headingLevel={3}>
            {metrics.length
              ? "Assign each manual-input metric to a contributor so its value is collected every month."
              : "No Scorecards metric is collected by manual input. Set a metric's collection to manual input in the metric registry first."}
          </EmptyState>
        }
      />
      {metrics.length ? (
        adding ? (
          <AssignForm metrics={metrics} users={users} onDone={() => { setAdding(false); onChanged(); }} onCancel={() => setAdding(false)} />
        ) : (
          <div>
            <button type="button" className="kg-btn" onClick={() => setAdding(true)}>
              Assign a contributor
            </button>
          </div>
        )
      ) : null}
    </div>
  );
}

function AssignForm({ metrics, users, onDone, onCancel }: { metrics: ManualMetric[]; users?: Person[]; onDone: () => void; onCancel: () => void }) {
  const [metric, setMetric] = useState(metrics[0]?.metric_code ?? "");
  const [scopeType, setScopeType] = useState<"subject" | "profile" | "dimension">("dimension");
  const [scopeCode, setScopeCode] = useState("");
  const [who, setWho] = useState<"user" | "role_relative">("user");
  const [user, setUser] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loaded] = useQuery("user.list", { status: "active", role_code: null, search: null });
  const people = users ?? (loaded.status === "ready" ? loaded.data.users : []);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    try {
      await invoke("input.assignment.create", {
        metric_code: metric,
        scope_type: scopeType,
        scope_code: scopeCode,
        assignee_type: who,
        assignee_user_id: who === "user" ? user : null,
        assignee_role: who === "role_relative" ? "line_manager_of" : null,
        effective_from: null,
      });
      onDone();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The assignment could not be saved.");
    }
  };
  return (
    <form className="kg-form" onSubmit={submit} aria-label="Assign a contributor">
      {error ? (
        <Notice tone="neg" role="alert">
          {error}
        </Notice>
      ) : null}
      <div className="kg-form-row">
        <SelectField label="Metric" value={metric} onChange={(e) => setMetric(e.target.value)} options={metrics.map((m) => ({ value: m.metric_code, label: m.display_name }))} />
        <SelectField
          label="Slice"
          value={scopeType}
          onChange={(e) => {
            const next = e.target.value as "subject" | "profile" | "dimension";
            setScopeType(next);
            if (next !== "subject") setWho("user");
          }}
          options={[
            { value: "dimension", label: "A team (branch, region…)" },
            { value: "profile", label: "Everyone on a profile" },
            { value: "subject", label: "One person" },
          ]}
        />
        <TextField label="Which" hint={SCOPE_HINT[scopeType]} required value={scopeCode} onChange={(e) => setScopeCode(e.target.value)} />
      </div>
      <div className="kg-form-row">
        <SelectField
          label="Who enters it"
          value={who}
          onChange={(e) => setWho(e.target.value as "user" | "role_relative")}
          options={[
            { value: "user", label: "A named person" },
            ...(scopeType === "subject" ? [{ value: "role_relative", label: "Their line manager, whoever it is that month" }] : []),
          ]}
        />
        {who === "user" ? (
          <SelectField
            label="Person"
            value={user}
            onChange={(e) => setUser(e.target.value)}
            hint="They need the Contributor role (or Line Manager or Admin)."
            options={[{ value: "", label: "Choose…" }, ...people.map((p) => ({ value: p.user_id, label: `${p.display_name}${p.roles.length ? ` (${p.roles.join(", ")})` : ""}` }))]}
          />
        ) : null}
      </div>
      <div style={{ display: "flex", gap: 8 }}>
        <button type="submit" className="kg-btn kg-btn--primary" disabled={!scopeCode.trim() || (who === "user" && !user)}>
          Assign
        </button>
        <button type="button" className="kg-btn" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}
