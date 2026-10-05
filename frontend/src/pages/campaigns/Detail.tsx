import { useState, type FormEvent, type ReactNode } from "react";

import { ApiError, invoke, isProposal } from "../../api/client";
import { useQuery, type QueryState } from "../../api/useAction";
import { Chip, EmptyState, ErrorPanel, Loading, Notice, SelectField, Skeleton, TextField } from "../../components";
import { formatDate, formatDateTime, formatDelta } from "../../lib/format";
import { EventFields } from "./EventFields";
import {
  blankEvent,
  BASES,
  budgetError,
  channelLabel,
  controlText,
  count,
  describeEstimate,
  describeAudience,
  draftOf,
  eventPayload,
  hasErrors,
  money,
  objectiveLabel,
  percent,
  roiText,
  RULES,
  shareOf,
  span,
  statusOf,
  validateEvent,
  withheldText,
  type Campaign,
  type CampaignEvent,
  type EventDraft,
  type EventReach,
  type EventValue,
  type Reach,
  type Reference,
  type ValueReport,
  type Winbacks,
} from "./model";

type Message = { tone: "info" | "neg"; text: string } | null;
type Act = (run: () => Promise<unknown>, done: string, proposed?: string) => Promise<boolean>;

/** One campaign: its details and every event, with the changes each state allows (App Flow §5.2). */
export function CampaignDetailView({
  campaign,
  reference,
  canManage,
  onChanged,
  reach,
  value,
  winbacks,
}: {
  campaign: Campaign;
  reference?: Reference;
  canManage: boolean;
  onChanged?: () => void;
  /** Each event's reach and funnel; absent, the panels are left out. */
  reach?: QueryState<Reach>;
  /** Each event's value, control group and return; absent, the cards are left out. */
  value?: QueryState<ValueReport>;
  /** Each event's win-backs; absent, or for a campaign that earns none, the panels are left out. */
  winbacks?: QueryState<Winbacks>;
}) {
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
      {value?.status === "ready" && value.data.basis_changed_at ? (
        <Notice tone="info" title={`Campaign value leads with ${BASES[value.data.basis]} value.`}>
          The basis was changed on {formatDateTime(value.data.basis_changed_at)}. Reports from before then led with {BASES[value.data.basis === "gross" ? "incremental" : "gross"]} value.
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
        campaign.events.map((e) => <EventPanel key={e.event_id} event={e} campaign={campaign} reference={reference} canManage={editable && !closed} busy={busy} act={act} reach={reach} value={value} winbacks={winbacks} />)
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

function EventPanel({ event: e, campaign, reference, canManage, busy, act, reach, value, winbacks }: { event: CampaignEvent; campaign: Campaign; reference?: Reference; canManage: boolean; busy: boolean; act: Act; reach?: QueryState<Reach>; value?: QueryState<ValueReport>; winbacks?: QueryState<Winbacks> }) {
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
        <Fact term="Control group">{e.holdout_pct === null ? "None" : `${e.holdout_pct}% held out`}</Fact>
        {!draft ? <Fact term="Version">{e.version}</Fact> : null}
      </dl>
      {e.pending_budget ? (
        <Notice tone="warn" title="Budget change awaiting approval.">
          {money(e.pending_budget.budget_amount, e.pending_budget.budget_currency)} was requested on {formatDateTime(e.pending_budget.requested_at)}. The budget stays at {money(e.budget_amount, e.budget_currency)} until a second person approves it.
        </Notice>
      ) : null}
      {reach ? <ReachPanel event={e} campaign={campaign} reach={reach} reference={reference} /> : null}
      {value && !draft ? <ValuePanel event={e} value={value} /> : null}
      {winbacks && !draft && campaign.objective === "attrition_winback" ? <WinbackPanel event={e} winbacks={winbacks} /> : null}

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

/** Targeted, then who the event got to and who acted: the estimate beside outcome reach (PRD CM-15). */
function ReachPanel({ event: e, campaign, reach, reference }: { event: CampaignEvent; campaign: Campaign; reach: QueryState<Reach>; reference?: Reference }) {
  const title = `reach-${e.event_id}`;
  if (reach.status === "loading") {
    return (
      <Loading label={`Loading the reach of ${e.event_name}`}>
        <Skeleton height={64} />
      </Loading>
    );
  }
  if (reach.status === "error") return <p className="kg-cap">The reach of this event could not be loaded just now.</p>;
  const all = reach.data;
  const r = all.events.find((x) => x.event_id === e.event_id);
  if (!r) return null;
  const draft = e.state === "draft";
  return (
    <section className="kg-stack" aria-labelledby={title}>
      <h3 id={title} className="kg-eyebrow">
        Reach
      </h3>
      {draft ? (
        <p className="kg-cap">Estimated audience: {describeEstimate(r.estimate, reference?.dimensions)} Contacts and outcomes count once the event is published.</p>
      ) : (
        <>
          <Funnel r={r} />
          <ReachNotes all={all} r={r} objective={campaign.objective} />
        </>
      )}
    </section>
  );
}

type Stage = { label: string; value: number | null; note: string };

function Funnel({ r }: { r: EventReach }) {
  const targeted = r.estimate.targeted;
  const stages: Stage[] = [
    { label: "Targeted (estimate)", value: targeted, note: targeted === null ? "Not sized" : r.estimate.population ? `of ${count(r.estimate.population)} customers` : "" },
    { label: "Contacted", value: r.contacted, note: "" },
    { label: "Delivered", value: r.delivered, note: "" },
    { label: "Responded", value: r.responded, note: "" },
    { label: "Outcome reach", value: r.matched_customers, note: "in the audience, acted in the window" },
    { label: "Converted", value: r.converted_customers, note: "credited to this event" },
  ];
  const top = Math.max(targeted ?? 0, ...stages.map((s) => s.value ?? 0));
  return (
    <ol className="kg-funnel" aria-label="Reach funnel">
      {stages.map((s) => (
        <li key={s.label}>
          <span className="kg-funnel__label">{s.label}</span>
          <span className="kg-funnel__bar" aria-hidden="true">
            {s.value !== null && top > 0 ? <span style={{ width: `${Math.max(1, (s.value / top) * 100)}%` }} /> : null}
          </span>
          <span className="kg-funnel__value">
            <b>{s.value === null ? "Not fed" : count(s.value)}</b>
            {s.value !== null && targeted !== null && s.label !== "Targeted (estimate)" ? <span className="kg-cap"> {shareOf(s.value, targeted)} of targeted</span> : null}
            {s.note ? <span className="kg-cap"> {s.note}</span> : null}
          </span>
        </li>
      ))}
    </ol>
  );
}

function ReachNotes({ all, r, objective }: { all: Reach; r: EventReach; objective: string }) {
  const notes: string[] = [];
  if (r.estimate.targeted === null) notes.push(describeEstimate(r.estimate));
  else if (r.estimate.as_of) notes.push(`Targeted is sized from the customer population on ${formatDate(r.estimate.as_of)}.`);
  if (!all.contacts_fed) notes.push("No contact feed has loaded yet, so contacted, delivered and responded are not known.");
  else if (r.contacted !== null && (r.delivered === null || r.responded === null)) notes.push("The contact feed does not say whether contacts were delivered or answered.");
  if (r.held_out) notes.push(`${count(r.held_out)} ${r.held_out === 1 ? "customer was" : "customers were"} held out as the control group and are not counted as contacted.`);
  if (!all.outcome_metric_codes.length) notes.push(`The ${objectiveLabel(objective).toLowerCase()} objective counts no outcome metrics yet, so nothing is attributed. An Admin names them in the objective settings.`);
  else if (!all.outcomes_fed) notes.push("No outcomes have loaded yet.");
  if (r.matched_customers !== null && r.converted_customers !== null && r.matched_customers > r.converted_customers) {
    const lost = r.matched_customers - r.converted_customers;
    notes.push(`${count(lost)} ${lost === 1 ? "customer's outcome went" : "customers' outcomes went"} to another event under the ${RULES[all.attribution_rule]} rule.`);
  }
  return (
    <>
      {r.attributed.length ? (
        <p>
          Attributed value: <b>{r.attributed.map((a) => money(a.amount, a.currency ?? "")).join(" · ")}</b>
          {r.credited_outcomes !== null ? <span className="kg-cap"> from {count(r.credited_outcomes)} outcomes</span> : null}
        </p>
      ) : null}
      {notes.map((n) => (
        <p key={n} className="kg-cap">
          {n}
        </p>
      ))}
    </>
  );
}

/** Who came back, and whether they stayed: provisional until the retention window passes (Scope §9.4). */
function WinbackPanel({ event: e, winbacks }: { event: CampaignEvent; winbacks: QueryState<Winbacks> }) {
  const title = `winbacks-${e.event_id}`;
  if (winbacks.status === "loading") {
    return (
      <Loading label={`Loading the win-backs of ${e.event_name}`}>
        <Skeleton height={48} />
      </Loading>
    );
  }
  if (winbacks.status === "error") return <p className="kg-cap">The win-backs of this event could not be loaded just now.</p>;
  const all = winbacks.data;
  const c = all.events.find((x) => x.event_id === e.event_id)?.counts ?? null;
  const days = all.retention_days;
  return (
    <section className="kg-stack kg-roi" aria-labelledby={title}>
      <h3 id={title} className="kg-eyebrow">
        Win-backs
      </h3>
      {!all.fed ? (
        <p className="kg-cap">No win-back feed has loaded yet. The data team's win-back feed says which customers came back, by your own definition.</p>
      ) : c === null || c.qualified === 0 ? (
        <p className="kg-cap">No win-backs have been credited to this event yet.</p>
      ) : (
        <>
          <dl className="kg-facts">
            <Fact term="Confirmed">
              <b>{count(c.confirmed)}</b>
            </Fact>
            <Fact term="Provisional">{count(c.provisional)}</Fact>
            <Fact term="Lapsed">{count(c.lapsed)}</Fact>
            <Fact term="Won back in total">{count(c.qualified)}</Fact>
          </dl>
          <p className="kg-cap">
            A win-back is confirmed once it has held for {days} days, or sooner when the data team confirms it.
            {c.next_confirmation ? ` The next provisional win-backs confirm on ${formatDate(c.next_confirmation)}.` : ""}
            {c.lapsed ? ` Lapsed win-backs are customers a later load said no longer qualify.` : ""}
          </p>
        </>
      )}
    </section>
  );
}

function ValuePanel({ event: e, value }: { event: CampaignEvent; value: QueryState<ValueReport> }) {
  if (value.status === "loading") {
    return (
      <Loading label={`Loading the value of ${e.event_name}`}>
        <Skeleton height={96} />
      </Loading>
    );
  }
  if (value.status === "error") return <p className="kg-cap">The value of this event could not be loaded just now.</p>;
  const v = value.data.events.find((x) => x.event_id === e.event_id);
  return v ? <BudgetRoiCard value={v} basis={value.data.basis} name={e.event_name} /> : null;
}

/**
 * What the event was worth against what it cost (Design Brief, `BudgetRoiCard`).
 * The basis leads, with the other figure beside it and the method named; a
 * withheld figure reads "—" with the reason, never zero.
 */
export function BudgetRoiCard({ value: v, basis, name }: { value: EventValue; basis: ValueReport["basis"]; name: string }) {
  const title = `value-${v.event_id}`;
  const cash = (amount: string | null) => (amount === null ? "—" : money(amount, v.currency));
  const lead = basis === "incremental" ? v.incremental : v.gross;
  const beside = basis === "incremental" ? v.gross : v.incremental;
  const leadLabel = basis === "incremental" ? "Incremental value" : "Gross value";
  const besideLabel = basis === "incremental" ? "Gross" : "Incremental";
  const notes = [withheldText(v), roiText(v)].filter(Boolean);
  const c = v.control;
  const lift = controlText(v);
  return (
    <section className="kg-stack kg-roi" aria-labelledby={title}>
      <h3 id={title} className="kg-eyebrow">
        Value and return
      </h3>
      {v.gross === null ? (
        <p className="kg-cap">{withheldText(v)}</p>
      ) : (
        <>
          <div className="kg-roi__lead">
            <div>
              <span className="kg-cap">{leadLabel}</span>
              <b className="kg-roi__figure" aria-label={`${leadLabel} of ${name}: ${lead === null ? "withheld" : cash(lead)}`}>
                {cash(lead)}
              </b>
            </div>
            <div>
              <span className="kg-cap">{besideLabel}</span>
              <b>{cash(beside)}</b>
            </div>
            <div>
              <span className="kg-cap">Budget</span>
              <b>{cash(v.budget)}</b>
            </div>
          </div>
          <dl className="kg-facts">
            <Fact term={`Return (${BASES[basis]})`}>{percent(v.roi)}</Fact>
            <Fact term="Return on gross">{percent(v.gross_roi)}</Fact>
            <Fact term="Cost per converted customer">{cash(v.cost_per_outcome)}</Fact>
            <Fact term="Budget used">{v.utilisation === null ? "No spend fed" : percent(v.utilisation, 0)}</Fact>
            <Fact term="Converted customers">
              {v.converted_customers === null ? "—" : count(v.converted_customers)}
              {v.new_customers ? <span className="kg-cap"> · {count(v.new_customers)} new</span> : null}
            </Fact>
          </dl>
          <p className="kg-cap">
            Incremental is gross less each converted customer's own value from {formatDate(v.baseline_start)} to {formatDate(v.baseline_end)}, the same length of time before the event. New customers have no earlier value, so theirs counts in full.
          </p>
          {v.other_currencies.length ? <p className="kg-cap">Also credited, not in the budget's currency and not converted: {v.other_currencies.map((a) => (a.currency ? money(a.amount, a.currency) : `${count(Number(a.amount))} (no currency)`)).join(" · ")}.</p> : null}
          {notes.map((n) => (
            <p key={n} className="kg-cap">
              {n}
            </p>
          ))}
        </>
      )}
      {c.treated !== null && c.control ? (
        <div role="group" aria-label="Control group">
          <dl className="kg-facts">
            <Fact term="Contacted">
              {count(c.treated)}
              {c.treated_rate !== null ? <span className="kg-cap"> · {percent(c.treated_rate)} acted</span> : null}
            </Fact>
            <Fact term="Held out">
              {count(c.control)}
              {c.actual_pct !== null ? <span className="kg-cap"> ({c.actual_pct}%{c.planned_pct ? `, planned ${c.planned_pct}%` : ""})</span> : null}
              {c.control_rate !== null ? <span className="kg-cap"> · {percent(c.control_rate)} acted</span> : null}
            </Fact>
            <Fact term="Lift">{c.lift_points === null ? "—" : `${formatDelta(Number(c.lift_points), 2)} points`}</Fact>
            <Fact term="Incremental by control group">{cash(c.incremental)}</Fact>
          </dl>
        </div>
      ) : null}
      {lift ? <p className="kg-cap">{lift}</p> : null}
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
          ...(started ? {} : { holdout_pct: p.holdout_pct ?? 0 }),
          ...(draft ? { budget_amount: p.budget_amount, budget_currency: p.budget_currency } : {}),
        }),
      draft ? "Draft saved." : "Saved. The change is versioned, and if its dates or audience changed, outcomes are attributed to this event again.",
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
