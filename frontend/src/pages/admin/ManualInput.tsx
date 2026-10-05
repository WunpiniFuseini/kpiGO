import { useState, type FormEvent } from "react";

import type { Output } from "../../api/actions";
import { ApiError, invoke, isProposal } from "../../api/client";
import { useQuery } from "../../api/useAction";
import { CheckboxGroup, Chip, DataTable, EmptyState, ErrorPanel, Loading, Notice, SelectField, TableSkeleton, TextField } from "../../components";
import { currentPeriod, formatDate, formatDateTime, formatPeriod, shiftPeriod } from "../../lib/format";

export type Assignments = Output<"input.assignment.list">;
export type Ladder = Output<"input.ladder.get">;
type Assignment = Assignments["assignments"][number];
type ManualMetric = { metric_code: string; display_name: string };
type Person = { user_id: string; display_name: string; roles: string[] };

const STATE: Record<string, { label: string; tone: "flat" | "info" | "up" | "warn" }> = {
  pending: { label: "Not started", tone: "flat" },
  draft: { label: "Draft only", tone: "warn" },
  submitted: { label: "Submitted", tone: "up" },
  restated: { label: "Restated", tone: "warn" },
};

/** How far up the escalation ladder an unsubmitted input has gone. */
const CHASED: Record<number, string> = {
  1: "Contributor reminded",
  2: "Their line manager told",
  3: "Stakeholders told",
};

// Roles that can see what is escalated to them; the server checks the actual grant.
const FOLLOWUP_ROLES = ["admin", "executive", "line_manager"];

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
      <LadderSection period={period} onChanged={reload} />
    </section>
  );
}

function LadderSection({ period, onChanged }: { period: string; onChanged: () => void }) {
  const [ladder, reload] = useQuery("input.ladder.get", { period_key: period });
  if (ladder.status === "loading") {
    return (
      <Loading label="Loading the escalation ladder">
        <TableSkeleton rows={3} columns={2} />
      </Loading>
    );
  }
  if (ladder.status === "error") return <ErrorPanel error={ladder.error} retry={reload} what="The escalation ladder" />;
  return (
    <LadderView
      ladder={ladder.data}
      onChanged={() => {
        reload();
        onChanged();
      }}
    />
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
  const [stakeholdersFor, setStakeholdersFor] = useState<Assignment | null>(null);
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
          ? "An input still owed climbs the escalation ladder below, in kpiGo and by email."
          : "An input still owed climbs the escalation ladder below, in kpiGo only. Email is off because no mail relay is set for this install; your infrastructure team can set one."}
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
                {a.stakeholder_names.length ? <span className="kg-msub">Overdue goes to {a.stakeholder_names.join(", ")}</span> : null}
              </>
            ),
          },
          {
            key: "state",
            header: month,
            render: (a) => (
              <>
                <Chip tone={STATE[a.state]?.tone ?? "flat"}>{STATE[a.state]?.label ?? a.state}</Chip>
                {a.submitted_at ? <span className="kg-msub">{formatDateTime(a.submitted_at)}</span> : CHASED[a.escalation_step] ? <span className="kg-msub">{CHASED[a.escalation_step]}</span> : null}
              </>
            ),
          },
          {
            key: "act",
            header: "Actions",
            render: (a) => (
              <>
                {a.effective_to ? (
                  <span className="kg-cap">Ends {formatDate(a.effective_to)}</span>
                ) : (
                  <button type="button" className="kg-btn kg-btn--link" aria-label={`End ${a.metric_name} for ${a.scope_label} after this month`} onClick={() => void end(a)}>
                    End after this month
                  </button>
                )}{" "}
                <button type="button" className="kg-btn kg-btn--link" aria-label={`Who hears when ${a.metric_name} for ${a.scope_label} is overdue`} onClick={() => setStakeholdersFor(a)}>
                  Overdue contacts
                </button>
              </>
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
      {stakeholdersFor ? (
        <StakeholdersForm
          key={stakeholdersFor.assignment_id}
          assignment={stakeholdersFor}
          users={users}
          onDone={() => {
            setStakeholdersFor(null);
            onChanged();
          }}
          onCancel={() => setStakeholdersFor(null)}
        />
      ) : null}
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
  const people = usePeople(users);
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

function usePeople(users?: Person[]): Person[] {
  const [loaded] = useQuery("user.list", { status: "active", role_code: null, search: null });
  return users ?? (loaded.status === "ready" ? loaded.data.users : []);
}

function StakeholderPicker({ people, value, onChange, legend }: { people: Person[]; value: string[]; onChange: (next: string[]) => void; legend: string }) {
  const options = people.filter((p) => value.includes(p.user_id) || p.roles.some((r) => FOLLOWUP_ROLES.includes(r)));
  return options.length ? (
    <CheckboxGroup legend={legend} options={options.map((p) => ({ value: p.user_id, label: p.display_name }))} value={value} onChange={onChange} />
  ) : (
    <p className="kg-cap">Nobody can be named yet: a stakeholder needs the Admin, Executive or Line Manager role.</p>
  );
}

function StakeholdersForm({ assignment: a, users, onDone, onCancel }: { assignment: Assignment; users?: Person[]; onDone: () => void; onCancel: () => void }) {
  const people = usePeople(users);
  const [chosen, setChosen] = useState<string[]>(a.stakeholder_user_ids);
  const [error, setError] = useState<string | null>(null);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    try {
      await invoke("input.assignment.set_stakeholders", { assignment_id: a.assignment_id, stakeholder_user_ids: chosen });
      onDone();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The overdue contacts could not be saved.");
    }
  };
  return (
    <form className="kg-form" onSubmit={submit} aria-label={`Overdue contacts for ${a.metric_name}, ${a.scope_label}`}>
      <h3 className="kg-section">
        Who hears when {a.metric_name} for {a.scope_label} is overdue
      </h3>
      <p className="kg-cap">Choose nobody to use the stakeholders on the ladder below.</p>
      {error ? (
        <Notice tone="neg" role="alert">
          {error}
        </Notice>
      ) : null}
      <StakeholderPicker legend="Stakeholders for this slice" people={people} value={chosen} onChange={setChosen} />
      <div style={{ display: "flex", gap: 8 }}>
        <button type="submit" className="kg-btn kg-btn--primary">
          Save
        </button>
        <button type="button" className="kg-btn" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function rungDay(day: string | null): string {
  return day ? formatDate(day) : "Off";
}

/** The escalation ladder (PRD MI-8): when an input still owed reaches whom, as dates for one month. */
export function LadderView({ ladder, onChanged, users }: { ladder: Ladder; onChanged: () => void; users?: Person[] }) {
  const [editing, setEditing] = useState(false);
  const month = formatPeriod(ladder.period_key, true);
  const told = ladder.default_stakeholders.map((s) => s.display_name).join(", ");
  return (
    <div className="kg-stack" style={{ marginTop: 18 }}>
      <div>
        <h3 className="kg-section" id="ladder-heading">
          Escalation ladder
        </h3>
        <p className="kg-cap">
          Each working day kpiGo checks what is still owed and climbs one rung at a time. {ladder.email ? "Each person gets one email a day listing everything that reached them." : "Email is off, so people see it in kpiGo only."}
        </p>
      </div>
      <table className="kg-table" aria-labelledby="ladder-heading">
        <thead>
          <tr>
            <th scope="col">Who is told</th>
            <th scope="col">When</th>
            <th scope="col">For {month}</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <th scope="row">The contributor</th>
            <td>{ladder.contributor_working_days_before ? `${ladder.contributor_working_days_before} working day(s) before the due day` : "On the due day"}</td>
            <td>{formatDate(ladder.contributor_on)}</td>
          </tr>
          <tr>
            <th scope="row">Their line manager</th>
            <td>{offset(ladder.manager_working_days)}</td>
            <td>{rungDay(ladder.manager_on)}</td>
          </tr>
          <tr>
            <th scope="row">
              Stakeholders
              <span className="kg-msub">{ladder.stakeholders.length ? told : `Not named, so everyone who manages input: ${told || "nobody yet"}`}</span>
            </th>
            <td>{offset(ladder.stakeholder_working_days)}</td>
            <td>{rungDay(ladder.stakeholders_on)}</td>
          </tr>
        </tbody>
      </table>
      <p className="kg-cap">
        Due by the end of {formatDate(ladder.due_on)}. The contributor and line manager are only chased while the input can still be entered; after the deadline only the stakeholders hear it went unreported.
      </p>
      {editing ? (
        <LadderForm
          ladder={ladder}
          users={users}
          onDone={() => {
            setEditing(false);
            onChanged();
          }}
          onCancel={() => setEditing(false)}
        />
      ) : (
        <div>
          <button type="button" className="kg-btn" onClick={() => setEditing(true)}>
            Change the ladder
          </button>
        </div>
      )}
    </div>
  );
}

function offset(days: number | null): string {
  if (days === null) return "Off";
  if (days === 0) return "On the due day";
  return days < 0 ? `${-days} working day(s) before the due day` : `${days} working day(s) after the due day`;
}

const OFFSETS = [
  { value: "off", label: "Off" },
  ...Array.from({ length: 21 }, (_, i) => i - 10).map((d) => ({ value: String(d), label: offset(d) })),
];

function LadderForm({ ladder, users, onDone, onCancel }: { ladder: Ladder; users?: Person[]; onDone: () => void; onCancel: () => void }) {
  const people = usePeople(users);
  const [before, setBefore] = useState(String(ladder.contributor_working_days_before));
  const [manager, setManager] = useState(ladder.manager_working_days === null ? "off" : String(ladder.manager_working_days));
  const [stake, setStake] = useState(ladder.stakeholder_working_days === null ? "off" : String(ladder.stakeholder_working_days));
  const [chosen, setChosen] = useState<string[]>(ladder.stakeholders.map((s) => s.user_id));
  const [message, setMessage] = useState<{ tone: "info" | "neg"; text: string } | null>(null);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    try {
      const out = await invoke(
        "input.ladder.set",
        {
          contributor_working_days_before: Number(before),
          manager_working_days: manager === "off" ? null : Number(manager),
          stakeholder_working_days: stake === "off" ? null : Number(stake),
          stakeholder_user_ids: chosen,
        },
        { allowProposal: true },
      );
      if (isProposal(out)) {
        setMessage({ tone: "info", text: out.message });
        return;
      }
      onDone();
    } catch (e) {
      setMessage({ tone: "neg", text: e instanceof ApiError ? e.message : "The ladder could not be saved." });
    }
  };
  return (
    <form className="kg-form" onSubmit={submit} aria-label="Change the escalation ladder">
      {message ? (
        <Notice tone={message.tone} role={message.tone === "neg" ? "alert" : "status"}>
          {message.text}
        </Notice>
      ) : null}
      <div className="kg-form-row">
        <SelectField
          label="Remind the contributor"
          value={before}
          onChange={(e) => setBefore(e.target.value)}
          options={Array.from({ length: 11 }, (_, i) => ({ value: String(i), label: i ? `${i} working day(s) before the due day` : "On the due day" }))}
        />
        <SelectField label="Tell their line manager" value={manager} onChange={(e) => setManager(e.target.value)} options={OFFSETS} />
        <SelectField label="Tell the stakeholders" value={stake} onChange={(e) => setStake(e.target.value)} options={OFFSETS} hint="The ladder climbs in order: contributor, line manager, stakeholders." />
      </div>
      <StakeholderPicker legend="Stakeholders (choose nobody to tell everyone who manages input)" people={people} value={chosen} onChange={setChosen} />
      <div style={{ display: "flex", gap: 8 }}>
        <button type="submit" className="kg-btn kg-btn--primary">
          Save ladder
        </button>
        <button type="button" className="kg-btn" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}
