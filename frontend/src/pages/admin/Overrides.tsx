import { useState, type FormEvent } from "react";

import type { Input, Output } from "../../api/actions";
import { ApiError, invoke } from "../../api/client";
import { useQuery } from "../../api/useAction";
import { Chip, DataTable, EmptyState, ErrorPanel, Loading, Notice, SelectField, TableSkeleton, TextField } from "../../components";
import { currentPeriod, formatDateTime, formatDecimal, formatPeriod } from "../../lib/format";

type OverrideRow = Output<"override.list">["overrides"][number];
type Status = NonNullable<Input<"override.list">["status"]>;
type Request = Input<"override.request">;

const STATUS: Record<string, { tone: "up" | "info" | "warn" | "down" | "flat"; label: string }> = {
  pending: { tone: "warn", label: "pending" },
  approved: { tone: "up", label: "approved" },
  rejected: { tone: "down", label: "rejected" },
  withdrawn: { tone: "flat", label: "withdrawn" },
  revoked: { tone: "flat", label: "revoked" },
};

const CHANGE: Record<string, string> = {
  target: "Target",
  weight: "Weight",
  cap: "Cap",
  actual: "Actual",
  target_type: "Target type",
};

const SCOPE: Record<string, string> = { subject: "Person", profile: "Profile", dimension: "Dimension" };

function periods(o: OverrideRow): string {
  return o.period_to && o.period_to !== o.period_from ? `${formatPeriod(o.period_from)} to ${formatPeriod(o.period_to)}` : formatPeriod(o.period_from);
}

function change(o: OverrideRow): string {
  const value = o.change_type === "target_type" ? o.override_text : o.override_value != null ? formatDecimal(o.override_value) : "–";
  return `${CHANGE[o.change_type] ?? o.change_type} → ${value}`;
}

/** Overrides on the Targets page (PRD SC-5): request, approve by a second person, revoke. */
export function OverridesSection({ canRequest, canApprove }: { canRequest: boolean; canApprove: boolean }) {
  const [status, setStatus] = useState<Status | "">("pending");
  const [requesting, setRequesting] = useState(false);
  const [list, reload] = useQuery("override.list", status ? { status } : {});

  return (
    <section className="kg-card" aria-labelledby="overrides-heading">
      <div className="kg-sechead">
        <div>
          <h2 id="overrides-heading" className="kg-section">
            Overrides
          </h2>
          <p className="kg-cap">Exceptions to a target, weight, cap, actual or target type. A second person approves each one; a person beats a profile beats a dimension.</p>
        </div>
        <span className="kg-spacer" />
        {canRequest ? (
          <button type="button" className="kg-btn" aria-expanded={requesting} onClick={() => setRequesting(!requesting)}>
            Request override
          </button>
        ) : null}
      </div>
      {requesting ? (
        <RequestForm
          onClose={() => setRequesting(false)}
          onSaved={() => {
            setStatus("pending");
            reload();
          }}
        />
      ) : null}
      <div className="kg-form-row" style={{ maxWidth: 260 }}>
        <SelectField
          label="Show"
          value={status}
          onChange={(e) => setStatus(e.target.value as Status | "")}
          options={[
            { value: "pending", label: "Pending approval" },
            { value: "approved", label: "Approved" },
            { value: "", label: "All" },
          ]}
        />
      </div>
      {list.status === "loading" ? (
        <Loading label="Loading overrides">
          <TableSkeleton rows={3} columns={6} />
        </Loading>
      ) : list.status === "error" ? (
        <ErrorPanel error={list.error} retry={reload} what="Overrides" />
      ) : (
        <OverridesView overrides={list.data.overrides} status={status} canApprove={canApprove} onChanged={reload} />
      )}
    </section>
  );
}

type Decision = { id: string; kind: "approve" | "reject" | "revoke" | "withdraw" };

export function OverridesView({
  overrides,
  status,
  canApprove,
  onChanged,
}: {
  overrides: OverrideRow[];
  status: Status | "";
  canApprove: boolean;
  onChanged: () => void;
}) {
  const [deciding, setDeciding] = useState<Decision | null>(null);
  const [note, setNote] = useState("");
  const [message, setMessage] = useState<{ tone: "info" | "neg"; text: string } | null>(null);
  const needsNote = deciding !== null && (deciding.kind === "reject" || deciding.kind === "revoke");

  const decide = async (event: FormEvent) => {
    event.preventDefault();
    if (!deciding) return;
    try {
      const out =
        deciding.kind === "approve"
          ? await invoke("override.approve", { override_id: deciding.id, note })
          : deciding.kind === "withdraw"
            ? await invoke("override.withdraw", { override_id: deciding.id, note })
            : await invoke(deciding.kind === "reject" ? "override.reject" : "override.revoke", { override_id: deciding.id, note });
      setMessage({ tone: "info", text: `Override ${out.status}.` });
      setDeciding(null);
      setNote("");
      onChanged();
    } catch (e) {
      setMessage({ tone: "neg", text: e instanceof ApiError ? e.message : "The override could not be updated." });
    }
  };

  const actions = (o: OverrideRow) => {
    const buttons: { kind: Decision["kind"]; label: string }[] = [];
    if (o.status === "pending" && o.mine) buttons.push({ kind: "withdraw", label: "Withdraw" });
    if (o.status === "pending" && canApprove && !o.mine) buttons.push({ kind: "approve", label: "Approve" }, { kind: "reject", label: "Reject" });
    if (o.status === "approved" && canApprove) buttons.push({ kind: "revoke", label: "Revoke" });
    if (buttons.length === 0) {
      return o.status === "pending" && o.mine && canApprove ? <span className="kg-cap">Needs someone else to approve</span> : <span className="kg-cap">–</span>;
    }
    return (
      <span style={{ display: "inline-flex", gap: 8 }}>
        {buttons.map((b) => (
          <button
            key={b.kind}
            type="button"
            className="kg-btn kg-btn--link"
            aria-label={`${b.label} the ${CHANGE[o.change_type]?.toLowerCase()} override for ${o.scope_label}, ${o.metric_name}`}
            onClick={() => {
              setDeciding({ id: o.override_id, kind: b.kind });
              setNote("");
            }}
          >
            {b.label}
          </button>
        ))}
      </span>
    );
  };

  return (
    <div className="kg-stack">
      {message ? (
        <Notice tone={message.tone} role={message.tone === "neg" ? "alert" : "status"}>
          {message.text}
        </Notice>
      ) : null}
      <DataTable
        caption="Overrides, newest first"
        captionHidden
        columns={[
          {
            key: "who",
            header: "Applies to",
            render: (o) => (
              <span>
                <span className="kg-cap">{SCOPE[o.scope_type]}</span> {o.scope_label}
              </span>
            ),
          },
          { key: "metric", header: "Metric", render: (o) => o.metric_name },
          { key: "months", header: "Months", render: periods },
          { key: "change", header: "Change", render: change },
          { key: "reason", header: "Reason", render: (o) => o.reason },
          {
            key: "by",
            header: "Requested",
            render: (o) => (
              <span>
                {o.requested_by_name ?? "–"}
                <br />
                <span className="kg-cap">{formatDateTime(o.requested_at)}</span>
              </span>
            ),
          },
          {
            key: "status",
            header: "Status",
            render: (o) => (
              <span>
                <Chip tone={STATUS[o.status]?.tone ?? "flat"}>{STATUS[o.status]?.label ?? o.status}</Chip>
                {o.approved_by_name ? <span className="kg-cap"> by {o.approved_by_name}</span> : null}
              </span>
            ),
          },
          { key: "actions", header: "Actions", render: actions },
        ]}
        rows={overrides}
        rowKey={(o) => o.override_id}
        empty={
          status === "pending" ? (
            <EmptyState kind="none" title="Nothing is waiting for approval">
              Requested overrides appear here until someone other than the requester approves or rejects them.
            </EmptyState>
          ) : (
            <EmptyState kind="none" title="No overrides">
              Every score is using its published target, weight, cap and reported actual.
            </EmptyState>
          )
        }
      />
      {deciding ? (
        <form className="kg-form" onSubmit={decide} aria-label="Decide on an override">
          <TextField
            label={needsNote ? "Why?" : "Note (optional)"}
            required={needsNote}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            hint={deciding.kind === "revoke" ? "Scores go back to the inputs beneath the override." : undefined}
          />
          <div style={{ display: "flex", gap: 8 }}>
            <button type="submit" className="kg-btn kg-btn--primary" disabled={needsNote && note.trim().length < 3}>
              {deciding.kind[0].toUpperCase() + deciding.kind.slice(1)}
            </button>
            <button type="button" className="kg-btn" onClick={() => setDeciding(null)}>
              Cancel
            </button>
          </div>
        </form>
      ) : null}
    </div>
  );
}

function RequestForm({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState({
    scope_type: "subject" as Request["scope_type"],
    scope_code: "",
    metric_code: "",
    from: currentPeriod(),
    to: "",
    change_type: "target" as Request["change_type"],
    value: "",
    target_type: "monthly" as NonNullable<Request["target_type"]>,
    reason: "",
  });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (key: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });
  const isType = form.change_type === "target_type";

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await invoke("override.request", {
        scope_type: form.scope_type,
        scope_code: form.scope_code,
        metric_code: form.metric_code,
        period_from: form.from,
        period_to: form.to || null,
        change_type: form.change_type,
        override_value: isType ? null : form.value,
        target_type: isType ? form.target_type : null,
        reason: form.reason,
      });
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The override could not be requested.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="kg-form" onSubmit={submit} aria-label="Request an override">
      <div className="kg-form-row">
        <SelectField
          label="Applies to"
          value={form.scope_type}
          onChange={set("scope_type")}
          options={[
            { value: "subject", label: "One person" },
            { value: "profile", label: "A profile" },
            { value: "dimension", label: "A branch, region, segment or portfolio" },
          ]}
        />
        <TextField
          label={form.scope_type === "subject" ? "Staff number" : form.scope_type === "profile" ? "Profile code" : "Dimension and code"}
          hint={form.scope_type === "dimension" ? "For example branch:ACC or region:NORTH." : undefined}
          value={form.scope_code}
          onChange={set("scope_code")}
          required
        />
        <TextField label="Metric code" value={form.metric_code} onChange={set("metric_code")} required />
      </div>
      <div className="kg-form-row">
        <TextField label="From period" hint="YYYYMM" pattern="[0-9]{6}" value={form.from} onChange={set("from")} required />
        <TextField label="To period" hint="Leave empty for one month." pattern="[0-9]{6}" value={form.to} onChange={set("to")} />
        <SelectField
          label="Change"
          value={form.change_type}
          onChange={set("change_type")}
          options={Object.entries(CHANGE).map(([value, label]) => ({ value, label }))}
        />
        {isType ? (
          <SelectField
            label="New target type"
            value={form.target_type}
            onChange={set("target_type")}
            options={["monthly", "yearly", "cumulative", "quarterly", "prorated"].map((v) => ({ value: v, label: v }))}
          />
        ) : (
          <TextField label="New value" type="number" step="any" value={form.value} onChange={set("value")} required />
        )}
      </div>
      <TextField label="Reason" value={form.reason} onChange={set("reason")} required minLength={3} hint="Recorded with the override and shown wherever it changes a score." />
      {error ? (
        <p role="alert" style={{ color: "var(--neg-ink)", fontSize: 13 }}>
          {error}
        </p>
      ) : null}
      <div style={{ display: "flex", gap: 8 }}>
        <button type="submit" className="kg-btn kg-btn--primary" disabled={busy}>
          {busy ? "Requesting…" : "Request"}
        </button>
        <button type="button" className="kg-btn" onClick={onClose}>
          Cancel
        </button>
      </div>
    </form>
  );
}
