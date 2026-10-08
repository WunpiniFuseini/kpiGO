import { useState, type FormEvent } from "react";

import type { Input, Output } from "../../api/actions";
import { ApiError, invoke } from "../../api/client";
import { useQuery } from "../../api/useAction";
import { Chip, EmptyState, ErrorPanel, Loading, Notice, SelectField, TableSkeleton, TextField } from "../../components";
import { formatDateTime } from "../../lib/format";
import { Page } from "../../shell/AppShell";

type Request = Output<"platform.approval.list">["requests"][number];
type Status = NonNullable<Input<"platform.approval.list">["status"]>;

const STATUS: Record<string, { tone: "up" | "info" | "warn" | "down" | "flat"; label: string }> = {
  pending: { tone: "warn", label: "Waiting" },
  approved: { tone: "up", label: "Approved" },
  rejected: { tone: "down", label: "Rejected" },
  failed: { tone: "down", label: "Failed" },
};

type Kind = "approve" | "reject" | "confirm" | "withdraw";

const VERB: Record<Kind, { action: keyof typeof ACTIONS; label: string; needsReason: boolean }> = {
  approve: { action: "approve", label: "Approve", needsReason: false },
  reject: { action: "reject", label: "Reject", needsReason: true },
  confirm: { action: "confirm", label: "Confirm", needsReason: false },
  withdraw: { action: "withdraw", label: "Withdraw", needsReason: true },
};

const ACTIONS = {
  approve: "platform.approval.approve",
  reject: "platform.approval.reject",
  confirm: "platform.approval.confirm",
  withdraw: "platform.approval.withdraw",
} as const;

/** What the person asked for, spelled out so a checker can see exactly what will run. */
export function PayloadFacts({ payload }: { payload: Record<string, unknown> }) {
  const entries = Object.entries(payload);
  if (entries.length === 0) {
    return <p className="kg-cap">This action takes no parameters.</p>;
  }
  return (
    <dl className="kg-facts">
      {entries.map(([key, value]) => (
        <div key={key}>
          <dt className="kg-cap">{key}</dt>
          <dd className="kg-mono">{typeof value === "object" && value !== null ? JSON.stringify(value) : String(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

/** One request: what it does, who asked, the exact payload and the decisions open to this viewer. */
export function ApprovalCard({ request, busy, onDecide }: { request: Request; busy: boolean; onDecide: (kind: Kind) => void }) {
  const status = STATUS[request.status] ?? { tone: "flat" as const, label: request.status };
  const buttons: Kind[] = [];
  if (request.can_decide) buttons.push("approve", "reject");
  if (request.can_confirm) buttons.push("confirm");
  if (request.can_withdraw) buttons.push("withdraw");

  return (
    <article className="kg-card" aria-labelledby={`req-${request.approval_request_id}`}>
      <div className="kg-sechead">
        <div>
          <h3 id={`req-${request.approval_request_id}`} className="kg-section">
            {request.action_summary || request.action_name}
          </h3>
          <p className="kg-cap">
            <code>{request.action_name}</code>
          </p>
        </div>
        <span className="kg-spacer" />
        <Chip tone={status.tone}>{status.label}</Chip>
        {request.caller === "agent" ? <Chip tone="info">Assistant proposal</Chip> : null}
      </div>
      <p className="kg-cap">
        Requested by {request.mine ? "you" : request.requested_by_name || "—"} · {formatDateTime(request.requested_at)}
      </p>
      <PayloadFacts payload={request.payload} />
      {request.status === "rejected" && request.rejection_reason ? (
        <p className="kg-cap">Reason: {request.rejection_reason}</p>
      ) : null}
      {request.decided_by_name && request.status !== "pending" ? (
        <p className="kg-cap">
          {status.label} by {request.decided_by_name}
          {request.decided_at ? ` · ${formatDateTime(request.decided_at)}` : ""}
        </p>
      ) : null}
      {request.status === "pending" && request.mine && !request.can_confirm ? (
        <p className="kg-cap">Someone else must approve this.</p>
      ) : null}
      {buttons.length ? (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {buttons.map((kind) => (
            <button
              key={kind}
              type="button"
              className={`kg-btn${kind === "approve" || kind === "confirm" ? " kg-btn--primary" : ""}`}
              disabled={busy}
              aria-label={`${VERB[kind].label} ${request.action_name}`}
              onClick={() => onDecide(kind)}
            >
              {VERB[kind].label}
            </button>
          ))}
        </div>
      ) : null}
    </article>
  );
}

export function ApprovalsView({
  requests,
  scope,
  status,
  onChanged,
}: {
  requests: Request[];
  scope: "org" | "own";
  status: Status | "";
  onChanged: () => void;
}) {
  const [deciding, setDeciding] = useState<{ id: string; kind: Kind } | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ tone: "info" | "neg"; text: string } | null>(null);

  const start = (id: string) => (kind: Kind) => {
    setMessage(null);
    if (VERB[kind].needsReason) {
      setDeciding({ id, kind });
      setReason("");
    } else {
      void run(id, kind, "");
    }
  };

  const run = async (id: string, kind: Kind, why: string) => {
    setBusy(true);
    try {
      const out = await invoke(ACTIONS[kind], { approval_request_id: id, reason: why });
      setMessage({ tone: "info", text: `Request ${out.status}.` });
      setDeciding(null);
      setReason("");
      onChanged();
    } catch (e) {
      setMessage({ tone: "neg", text: e instanceof ApiError ? e.message : "The request could not be updated." });
    } finally {
      setBusy(false);
    }
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (deciding) void run(deciding.id, deciding.kind, reason);
  };

  if (requests.length === 0) {
    return status === "pending" ? (
      <EmptyState kind="none" title="Nothing is waiting">
        {scope === "org"
          ? "Proposals and change requests appear here until a decider approves or rejects them."
          : "Changes you or your assistant propose appear here until they are approved."}
      </EmptyState>
    ) : (
      <EmptyState kind="none" title="Nothing to show">
        No request matches this filter.
      </EmptyState>
    );
  }

  return (
    <div className="kg-stack">
      {message ? (
        <Notice tone={message.tone} role={message.tone === "neg" ? "alert" : "status"}>
          {message.text}
        </Notice>
      ) : null}
      {requests.map((r) => (
        <ApprovalCard key={r.approval_request_id} request={r} busy={busy} onDecide={start(r.approval_request_id)} />
      ))}
      {deciding ? (
        <form className="kg-form" onSubmit={submit} aria-label="Give a reason">
          <TextField
            label="Why?"
            required
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            hint={deciding.kind === "withdraw" ? "Recorded with the request. Nothing runs." : "Recorded with the request and shown to whoever asked."}
          />
          <div style={{ display: "flex", gap: 8 }}>
            <button type="submit" className="kg-btn kg-btn--primary" disabled={busy || reason.trim().length < 3}>
              {VERB[deciding.kind].label}
            </button>
            <button type="button" className="kg-btn" onClick={() => setDeciding(null)} disabled={busy}>
              Cancel
            </button>
          </div>
        </form>
      ) : null}
    </div>
  );
}

/**
 * Administer → Approvals (R7): the maker-checker queue. A decider sees the whole
 * organisation's pending requests and approves or rejects them; everyone sees the
 * changes they or their assistant proposed, to confirm or withdraw their own.
 */
export function ApprovalsPage() {
  const [status, setStatus] = useState<Status | "">("pending");
  const [mine, setMine] = useState(false);
  const [list, reload] = useQuery("platform.approval.list", { ...(status ? { status } : {}), mine });

  return (
    <Page title="Approvals">
      <div className="kg-form-row" style={{ maxWidth: 420 }}>
        <SelectField
          label="Show"
          value={status}
          onChange={(e) => setStatus(e.target.value as Status | "")}
          options={[
            { value: "pending", label: "Waiting" },
            { value: "approved", label: "Approved" },
            { value: "rejected", label: "Rejected" },
            { value: "failed", label: "Failed" },
          ]}
        />
        {list.status === "ready" && list.data.scope === "org" ? (
          <SelectField
            label="Whose"
            value={mine ? "mine" : "all"}
            onChange={(e) => setMine(e.target.value === "mine")}
            options={[
              { value: "all", label: "Everyone's" },
              { value: "mine", label: "Only mine" },
            ]}
          />
        ) : null}
      </div>
      {list.status === "loading" ? (
        <Loading label="Loading the approval queue">
          <TableSkeleton rows={3} columns={1} />
        </Loading>
      ) : list.status === "error" ? (
        <ErrorPanel error={list.error} retry={reload} what="Approvals" />
      ) : (
        <ApprovalsView requests={list.data.requests} scope={list.data.scope} status={status} onChanged={reload} />
      )}
    </Page>
  );
}
