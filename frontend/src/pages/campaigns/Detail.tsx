import { useState, type FormEvent, type ReactNode } from "react";

import { ApiError, invoke, isProposal } from "../../api/client";
import { useQuery } from "../../api/useAction";
import { Chip, EmptyState, ErrorPanel, Loading, Notice, SelectField, Skeleton, TextField } from "../../components";
import { formatDate, formatDateTime } from "../../lib/format";
import { EventFields } from "./EventFields";
import {
  blankEvent,
  budgetError,
  channelLabel,
  describeAudience,
  draftOf,
  eventPayload,
  hasErrors,
  money,
  objectiveLabel,
  span,
  statusOf,
  validateEvent,
  type Campaign,
  type CampaignEvent,
  type EventDraft,
  type Reference,
} from "./model";

type Message = { tone: "info" | "neg"; text: string } | null;
type Act = (run: () => Promise<unknown>, done: string, proposed?: string) => Promise<boolean>;

/** One campaign: its details and every event, with the changes each state allows (App Flow §5.2). */
export function CampaignDetailView({ campaign, reference, canManage, onChanged }: { campaign: Campaign; reference?: Reference; canManage: boolean; onChanged?: () => void }) {
  const [message, setMessage] = useState<Message>(null);
  const [busy, setBusy] = useState(false);
  const closed = campaign.status === "closed" && campaign.events.every((e) => e.state === "closed" || e.status === "closed");
  const editable = canManage && reference !== undefined;

  const act: Act = async (run, done, proposed) => {
    setBusy(true);
    setMessage(null);
    try {
      const out = await run();
      setMessage({ tone: "info", text: isProposal(out) ? (proposed ?? out.message) : done });
      onChanged?.();
      return true;
    } catch (e) {
      setMessage({ tone: "neg", text: e instanceof ApiError ? e.message : "That change did not go through." });
      return false;
    } finally {
      setBusy(false);
    }
  };

  const status = statusOf(campaign.status);
  return (
    <div className="kg-stack">
      {message ? (
        <Notice tone={message.tone} role={message.tone === "neg" ? "alert" : "status"}>
          {message.text}
        </Notice>
      ) : null}

      <section className="kg-card kg-stack" aria-labelledby="campaign-summary">
        <div className="kg-row">
          <h2 id="campaign-summary" className="kg-section">
            {campaign.code} · {objectiveLabel(campaign.objective)}
          </h2>
          <Chip tone={status.tone}>{status.label}</Chip>
        </div>
        {campaign.description ? <p>{campaign.description}</p> : null}
        <dl className="kg-facts">
          <Fact term="Type">{campaign.campaign_type}</Fact>
          <Fact term="Product">{campaign.product_code ?? "–"}</Fact>
          <Fact term="Owner">{campaign.owner_name ?? "–"}</Fact>
          <Fact term="Priority">{campaign.priority ?? "–"}</Fact>
          <Fact term="Events">{campaign.events.length}</Fact>
        </dl>
        {editable && campaign.status !== "closed" ? <CampaignEdits campaign={campaign} busy={busy} act={act} /> : null}
      </section>

      {campaign.events.length ? (
        campaign.events.map((e) => <EventPanel key={e.event_id} event={e} campaign={campaign} reference={reference} canManage={editable && !closed} busy={busy} act={act} />)
      ) : (
        <EmptyState kind="none" title="This campaign has no events yet">
          An event is one run: its dates, its budget and who it is for. {editable ? "Add the first one below." : null}
        </EmptyState>
      )}

      {editable && campaign.status !== "closed" && reference ? <AddEvent campaign={campaign} reference={reference} busy={busy} act={act} /> : null}
    </div>
  );
}

function Fact({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div>
      <dt className="kg-cap">{term}</dt>
      <dd>{children}</dd>
    </div>
  );
}

function CampaignEdits({ campaign, busy, act }: { campaign: Campaign; busy: boolean; act: Act }) {
  const [name, setName] = useState(campaign.name);
  const [type, setType] = useState(campaign.campaign_type);
  const [description, setDescription] = useState(campaign.description);
  const [priority, setPriority] = useState(campaign.priority === null ? "" : String(campaign.priority));
  const [confirming, setConfirming] = useState(false);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    void act(
      () =>
        invoke("campaign.update", {
          campaign_id: campaign.campaign_id,
          name,
          campaign_type: type,
          description,
          ...(priority.trim() ? { priority: Number(priority) } : { clear_priority: true }),
        }),
      "Campaign details saved.",
    );
  };
  return (
    <details>
      <summary>Edit campaign details or close it</summary>
      <form className="kg-stack" style={{ marginTop: 10 }} onSubmit={submit} aria-label="Campaign details">
        <div className="kg-form-row">
          <TextField label="Name" value={name} required maxLength={120} onChange={(e) => setName(e.target.value)} />
          <TextField label="Type" value={type} required maxLength={64} onChange={(e) => setType(e.target.value)} />
          <TextField label="Priority" type="number" min={1} value={priority} hint="Blank: no rank" onChange={(e) => setPriority(e.target.value)} />
        </div>
        <TextField label="Description" value={description} maxLength={2000} onChange={(e) => setDescription(e.target.value)} />
        <div className="kg-row" style={{ justifyContent: "flex-start", flexWrap: "wrap" }}>
          <button type="submit" className="kg-btn kg-btn--primary" disabled={busy || !name.trim() || !type.trim()}>
            Save details
          </button>
          <span className="kg-spacer" />
          {confirming ? (
            <>
              <span className="kg-cap">Closing ends every event. It cannot be undone.</span>
              <button type="button" className="kg-btn" onClick={() => setConfirming(false)}>
                Keep it open
              </button>
              <button type="button" className="kg-btn kg-btn--primary" disabled={busy} onClick={() => void act(() => invoke("campaign.close", { campaign_id: campaign.campaign_id }), "The campaign is closed.")}>
                Close campaign
              </button>
            </>
          ) : (
            <button type="button" className="kg-btn" onClick={() => setConfirming(true)}>
              Close campaign…
            </button>
          )}
        </div>
      </form>
    </details>
  );
}

type Panel = "edit" | "budget" | "repeat" | null;

function EventPanel({ event: e, campaign, reference, canManage, busy, act }: { event: CampaignEvent; campaign: Campaign; reference?: Reference; canManage: boolean; busy: boolean; act: Act }) {
  const [panel, setPanel] = useState<Panel>(null);
  const [confirm, setConfirm] = useState<"close" | "delete" | null>(null);
  const status = statusOf(e.status);
  const draft = e.state === "draft";
  const open = canManage && e.state !== "closed" && e.status !== "closed";
  const toggle = (p: Panel) => setPanel(panel === p ? null : p);
  const ev = { event_id: e.event_id };

  return (
    <section className="kg-card kg-stack" aria-labelledby={`ev-${e.event_id}`}>
      <div className="kg-row">
        <h2 id={`ev-${e.event_id}`} className="kg-section">
          Event {e.sequence_no} · {e.event_name}
        </h2>
        <Chip tone={status.tone}>{status.label}</Chip>
      </div>
      <dl className="kg-facts">
        <Fact term="Contact days">{span(e.period_start, e.period_end)}</Fact>
        <Fact term="Attribution window">
          {e.attribution_window_days} days, to {formatDate(e.window_end)}
        </Fact>
        <Fact term="Budget">
          {money(e.budget_amount, e.budget_currency)}
          {e.spend_to_date !== null ? ` · ${money(e.spend_to_date, e.budget_currency)} spent` : ""}
        </Fact>
        <Fact term="Channels">{e.channels.length ? e.channels.map(channelLabel).join(", ") : "–"}</Fact>
        <Fact term="Audience">{describeAudience(e.audience, reference?.dimensions)}</Fact>
        {!draft ? <Fact term="Version">{e.version}</Fact> : null}
      </dl>
      {e.pending_budget ? (
        <Notice tone="warn" title="Budget change awaiting approval.">
          {money(e.pending_budget.budget_amount, e.pending_budget.budget_currency)} was requested on {formatDateTime(e.pending_budget.requested_at)}. The budget stays at {money(e.budget_amount, e.budget_currency)} until a second person approves it.
        </Notice>
      ) : null}
      {e.reattribute_from && !draft ? <p className="kg-cap">The dates or audience changed, so attribution is redone from {formatDate(e.reattribute_from)} on the next outcome load.</p> : null}

      {open ? (
        <div className="kg-row" style={{ justifyContent: "flex-start", flexWrap: "wrap" }}>
          {draft ? (
            <button type="button" className="kg-btn kg-btn--primary" disabled={busy || !e.audience.length} title={e.audience.length ? undefined : "Add an audience first"} onClick={() => void act(() => invoke("campaign.event.publish", ev), `${e.event_name} is published. It is ${new Date(e.period_start) > new Date() ? "scheduled" : "running"}.`)}>
              Publish
            </button>
          ) : null}
          <button type="button" className="kg-btn" aria-expanded={panel === "edit"} onClick={() => toggle("edit")}>
            {draft ? "Edit" : "Change dates, channels or audience"}
          </button>
          {!draft ? (
            <button type="button" className="kg-btn" aria-expanded={panel === "budget"} onClick={() => toggle("budget")}>
              Change budget
            </button>
          ) : null}
          {e.state === "live" ? (
            <button type="button" className="kg-btn" disabled={busy} onClick={() => void act(() => invoke("campaign.event.status", { ...ev, to: "pause" }), `${e.event_name} is paused.`)}>
              Pause
            </button>
          ) : null}
          {e.state === "paused" ? (
            <button type="button" className="kg-btn" disabled={busy} onClick={() => void act(() => invoke("campaign.event.status", { ...ev, to: "resume" }), `${e.event_name} is running again.`)}>
              Resume
            </button>
          ) : null}
          <button type="button" className="kg-btn" aria-expanded={panel === "repeat"} onClick={() => toggle("repeat")}>
            Repeat…
          </button>
          {confirm ? (
            <>
              <span className="kg-cap">{confirm === "delete" ? "Delete this draft?" : "Closing is final."}</span>
              <button type="button" className="kg-btn" onClick={() => setConfirm(null)}>
                Cancel
              </button>
              <button
                type="button"
                className="kg-btn kg-btn--primary"
                disabled={busy}
                onClick={() => void act(() => (confirm === "delete" ? invoke("campaign.event.remove", ev) : invoke("campaign.event.status", { ...ev, to: "close" })), confirm === "delete" ? `${e.event_name} is deleted.` : `${e.event_name} is closed.`)}
              >
                {confirm === "delete" ? "Delete draft" : "Close event"}
              </button>
            </>
          ) : (
            <button type="button" className="kg-btn" onClick={() => setConfirm(draft ? "delete" : "close")}>
              {draft ? "Delete…" : "Close…"}
            </button>
          )}
        </div>
      ) : null}

      {open && reference && panel === "edit" ? <EventEditor event={e} reference={reference} objective={campaign.objective} busy={busy} act={act} onDone={() => setPanel(null)} /> : null}
      {open && reference && panel === "budget" ? <BudgetForm event={e} reference={reference} busy={busy} act={act} onDone={() => setPanel(null)} /> : null}
      {open && panel === "repeat" ? <RepeatForm event={e} busy={busy} act={act} onDone={() => setPanel(null)} /> : null}
      {!draft ? <History eventId={e.event_id} /> : null}
    </section>
  );
}

function EventEditor({ event: e, reference, objective, busy, act, onDone }: { event: CampaignEvent; reference: Reference; objective: string; busy: boolean; act: Act; onDone: () => void }) {
  const draft = e.state === "draft";
  const started = !draft && e.status !== "scheduled";
  const [value, setValue] = useState<EventDraft>(draftOf(e));
  const [tried, setTried] = useState(false);
  const errors = tried ? validateEvent(value, { withBudget: draft }) : {};
  if (!draft && tried && !value.audience.length) errors.audience = "A published event keeps at least one audience criterion.";
  const window = reference.objectives.find((o) => o.objective === objective)?.default_window_days;
  const submit = async (ev: FormEvent) => {
    ev.preventDefault();
    setTried(true);
    if (hasErrors(validateEvent(value, { withBudget: draft })) || (!draft && !value.audience.length)) return;
    const p = eventPayload(value);
    const ok = await act(
      () =>
        invoke("campaign.event.update", {
          event_id: e.event_id,
          event_name: p.event_name,
          ...(started ? {} : { period_start: p.period_start }),
          period_end: p.period_end,
          attribution_window_days: p.attribution_window_days ?? e.attribution_window_days,
          channels: p.channels,
          audience: p.audience,
          ...(draft ? { budget_amount: p.budget_amount, budget_currency: p.budget_currency } : {}),
        }),
      draft ? "Draft saved." : "Saved. The change is versioned; attribution is redone for this event on the next outcome load if its dates or audience changed.",
    );
    if (ok) onDone();
  };
  return (
    <form className="kg-stack" onSubmit={(ev) => void submit(ev)} noValidate aria-label={`Edit ${e.event_name}`} style={{ borderTop: "1px solid var(--line)", paddingTop: 12 }}>
      {!draft ? <p className="kg-cap">The budget changes separately, through Change budget.</p> : null}
      <EventFields value={value} onChange={setValue} errors={errors} dimensions={reference.dimensions} currencies={reference.currencies} defaultWindow={window} mode={draft ? "draft" : "live"} started={started} />
      <div className="kg-row" style={{ justifyContent: "flex-start" }}>
        <button type="submit" className="kg-btn kg-btn--primary" disabled={busy}>
          Save
        </button>
        <button type="button" className="kg-btn" onClick={onDone}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function BudgetForm({ event: e, reference, busy, act, onDone }: { event: CampaignEvent; reference: Reference; busy: boolean; act: Act; onDone: () => void }) {
  const [amount, setAmount] = useState(e.budget_amount);
  const [currency, setCurrency] = useState(e.budget_currency);
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);
  const error = tried ? budgetError(amount) : undefined;
  const now = money(e.budget_amount, e.budget_currency);
  const submit = async (ev: FormEvent) => {
    ev.preventDefault();
    setTried(true);
    if (budgetError(amount)) return;
    const value = amount.trim().replace(/,/g, "");
    const ok = await act(
      () => invoke("campaign.event.budget.set", { event_id: e.event_id, budget_amount: value, budget_currency: currency, reason }, { allowProposal: true }),
      `The budget is now ${money(value, currency)}.`,
      `Sent for approval. The budget stays at ${now} until a second person approves ${money(value, currency)}.`,
    );
    if (ok) onDone();
  };
  return (
    <form className="kg-stack" onSubmit={(ev) => void submit(ev)} noValidate aria-label={`Change the budget of ${e.event_name}`} style={{ borderTop: "1px solid var(--line)", paddingTop: 12 }}>
      {reference.budget_needs_approval ? (
        <Notice tone="warn" title="This change goes to an approver.">
          The budget stays at {now} until someone other than you approves it. Budget changes are money, so they are checked by default.
        </Notice>
      ) : (
        <Notice title="This change takes effect at once.">Budget approval is switched off for this install. The change is still versioned and audited.</Notice>
      )}
      <div className="kg-form-row">
        <TextField label="New budget" inputMode="decimal" value={amount} required error={error} onChange={(ev) => setAmount(ev.target.value)} />
        <SelectField label="Currency" value={currency} onChange={(ev) => setCurrency(ev.target.value)} options={reference.currencies.map((c) => ({ value: c, label: c }))} />
      </div>
      <TextField label="Reason" value={reason} maxLength={2000} hint="What the approver reads" onChange={(ev) => setReason(ev.target.value)} />
      <div className="kg-row" style={{ justifyContent: "flex-start" }}>
        <button type="submit" className="kg-btn kg-btn--primary" disabled={busy || Boolean(e.pending_budget)}>
          {reference.budget_needs_approval ? "Send for approval" : "Change budget"}
        </button>
        <button type="button" className="kg-btn" onClick={onDone}>
          Cancel
        </button>
        {e.pending_budget ? <span className="kg-cap">A change is already waiting for approval.</span> : null}
      </div>
    </form>
  );
}

function RepeatForm({ event: e, busy, act, onDone }: { event: CampaignEvent; busy: boolean; act: Act; onDone: () => void }) {
  const [every, setEvery] = useState<"week" | "month" | "quarter">("month");
  const [count, setCount] = useState("3");
  const n = Number(count);
  const valid = Number.isInteger(n) && n >= 1 && n <= 24;
  return (
    <form
      className="kg-stack"
      aria-label={`Repeat ${e.event_name}`}
      style={{ borderTop: "1px solid var(--line)", paddingTop: 12 }}
      onSubmit={(ev) => {
        ev.preventDefault();
        void act(() => invoke("campaign.event.repeat", { event_id: e.event_id, every, count: n }), `${n} draft ${n === 1 ? "copy" : "copies"} added. Check each one's budget and dates, then publish.`).then((ok) => ok && onDone());
      }}
    >
      <p className="kg-cap">Copies keep this event's budget, window, channels and audience, on the following {every}s. They start as drafts.</p>
      <div className="kg-form-row">
        <SelectField label="Every" value={every} onChange={(ev) => setEvery(ev.target.value as typeof every)} options={[{ value: "week", label: "Week" }, { value: "month", label: "Month" }, { value: "quarter", label: "Quarter" }]} />
        <TextField label="How many" type="number" min={1} max={24} value={count} error={valid ? undefined : "1 to 24"} onChange={(ev) => setCount(ev.target.value)} />
      </div>
      <div className="kg-row" style={{ justifyContent: "flex-start" }}>
        <button type="submit" className="kg-btn kg-btn--primary" disabled={busy || !valid}>
          Add copies
        </button>
        <button type="button" className="kg-btn" onClick={onDone}>
          Cancel
        </button>
      </div>
    </form>
  );
}

const CHANGE_TEXT: Record<string, string> = {
  published: "Published",
  updated: "Dates, window, channels or name changed",
  audience: "Audience changed",
  budget: "Budget changed",
  paused: "Paused",
  resumed: "Resumed",
  closed: "Closed",
};

function History({ eventId }: { eventId: string }) {
  const [open, setOpen] = useState(false);
  return (
    <details onToggle={(ev) => setOpen((ev.target as HTMLDetailsElement).open)}>
      <summary>Change history</summary>
      {open ? <HistoryList eventId={eventId} /> : null}
    </details>
  );
}

function HistoryList({ eventId }: { eventId: string }) {
  const [data, reload] = useQuery("campaign.event.history", { event_id: eventId });
  if (data.status === "loading") {
    return (
      <Loading label="Loading the change history">
        <Skeleton height={60} />
      </Loading>
    );
  }
  if (data.status === "error") return <ErrorPanel error={data.error} retry={reload} what="The change history" />;
  return (
    <ol style={{ margin: "8px 0 0", paddingLeft: 18 }}>
      {data.data.versions.map((v) => {
        const s = v.snapshot as { budget_amount?: string; budget_currency?: string; period_start?: string; period_end?: string };
        return (
          <li key={v.version_no}>
            <b>v{v.version_no}</b> {CHANGE_TEXT[v.change] ?? v.change} · {formatDateTime(v.created_at)}
            <span className="kg-cap">
              {" "}
              · {s.period_start && s.period_end ? span(s.period_start, s.period_end) : ""}
              {s.budget_amount && s.budget_currency ? ` · ${money(s.budget_amount, s.budget_currency)}` : ""}
              {v.approval_request_id ? " · approved by a checker" : ""}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function AddEvent({ campaign, reference, busy, act }: { campaign: Campaign; reference: Reference; busy: boolean; act: Act }) {
  const last = campaign.events[campaign.events.length - 1];
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState<EventDraft>(blankEvent(last?.budget_currency ?? reference.default_currency));
  const [tried, setTried] = useState(false);
  const errors = tried ? validateEvent(value) : {};
  const window = reference.objectives.find((o) => o.objective === campaign.objective)?.default_window_days;
  if (!open) {
    return (
      <div>
        <button type="button" className="kg-btn" onClick={() => setOpen(true)}>
          + Add an event
        </button>
      </div>
    );
  }
  return (
    <form
      className="kg-card kg-stack"
      noValidate
      aria-label="New event"
      onSubmit={(ev) => {
        ev.preventDefault();
        setTried(true);
        if (hasErrors(validateEvent(value))) return;
        void act(() => invoke("campaign.event.add", { campaign_id: campaign.campaign_id, ...eventPayload(value) }), `${value.event_name} is added as a draft.`).then((ok) => {
          if (ok) {
            setOpen(false);
            setTried(false);
            setValue(blankEvent(value.budget_currency));
          }
        });
      }}
    >
      <h2 className="kg-section">New event</h2>
      <EventFields value={value} onChange={setValue} errors={errors} dimensions={reference.dimensions} currencies={reference.currencies} defaultWindow={window} />
      <div className="kg-row" style={{ justifyContent: "flex-start" }}>
        <button type="submit" className="kg-btn kg-btn--primary" disabled={busy}>
          Add as draft
        </button>
        <button type="button" className="kg-btn" onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
    </form>
  );
}
