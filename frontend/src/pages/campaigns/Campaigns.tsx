import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { ApiError, invoke } from "../../api/client";
import { useQuery } from "../../api/useAction";
import { Chip, EmptyState, ErrorPanel, Loading, Notice, SelectField, TableSkeleton, TextField } from "../../components";
import { Page } from "../../shell/AppShell";
import { useMe } from "../../session/Session";
import { CampaignDetailView } from "./Detail";
import { EventFields } from "./EventFields";
import {
  OBJECTIVES,
  TABS,
  blankEvent,
  channelLabel,
  eventPayload,
  hasErrors,
  money,
  objectiveLabel,
  span,
  statusOf,
  validateEvent,
  type CampaignList,
  type CampaignSummary,
  type EventDraft,
  type Objective,
  type Reference,
  type Tab,
} from "./model";

// ── the list ────────────────────────────────────────────────────────────────

/** Campaign Manager → Campaigns: list with status tabs (App Flow §5.4). */
export function CampaignsPage() {
  const me = useMe();
  const canAuthor = me.permissions.includes("campaign.manage");
  const [data, reload] = useQuery("campaign.list", { tab: "all" });
  const [tab, setTab] = useState<Tab>("running");
  return (
    <Page
      title="Campaigns"
      actions={
        canAuthor ? (
          <Link className="kg-btn kg-btn--primary" to="/campaign/new">
            + New campaign
          </Link>
        ) : null
      }
    >
      {data.status === "loading" ? (
        <Loading label="Loading campaigns">
          <TableSkeleton rows={4} columns={3} />
        </Loading>
      ) : data.status === "error" ? (
        <ErrorPanel error={data.error} retry={reload} what="Campaigns" />
      ) : (
        <CampaignListView list={data.data} tab={tab} onTab={setTab} canAuthor={canAuthor} />
      )}
    </Page>
  );
}

export function CampaignListView({ list, tab, onTab, canAuthor }: { list: CampaignList; tab: Tab; onTab: (t: Tab) => void; canAuthor: boolean }) {
  if (!list.scoped) {
    return (
      <EmptyState kind="no-access" title="No campaigns are in your scope" ask="an Admin, under Administer → Users & access">
        Campaign data is shown only through a Campaign scope grant: by campaign, by product, or by an audience such as a segment or region.{canAuthor ? " Campaigns you create and own are always yours to see." : ""}
      </EmptyState>
    );
  }
  const counts = Object.fromEntries(TABS.map((t) => [t.value, list.campaigns.filter((c) => c.status === t.value).length]));
  const shown = list.campaigns.filter((c) => c.status === tab);
  return (
    <div className="kg-stack">
      <div className="kg-seg" role="group" aria-label="Campaign status">
        {TABS.map((t) => (
          <button key={t.value} type="button" aria-pressed={tab === t.value} onClick={() => onTab(t.value)}>
            {t.label} <b>{counts[t.value]}</b>
          </button>
        ))}
      </div>
      {shown.length ? (
        <ul className="kg-stack" style={{ listStyle: "none", padding: 0, margin: 0 }} aria-label={`${TABS.find((t) => t.value === tab)?.label} campaigns`}>
          {shown.map((c) => (
            <li key={c.campaign_id}>
              <CampaignListItem campaign={c} />
            </li>
          ))}
        </ul>
      ) : list.campaigns.length ? (
        <EmptyState kind="none" title={`No campaigns are ${tab === "draft" ? "in draft" : tab}`} action={<TabReset counts={counts} onTab={onTab} />}>
          {tab === "scheduled" ? "A published event that has not started yet shows here." : tab === "running" ? "A campaign runs from its first contact day until its last attribution window closes." : null}
        </EmptyState>
      ) : (
        <EmptyState
          kind="none"
          title="No campaigns yet"
          action={
            canAuthor ? (
              <Link className="kg-btn kg-btn--primary" to="/campaign/new">
                Create the first campaign
              </Link>
            ) : null
          }
        >
          Campaigns are authored here in kpiGo: a type, an objective, then one or more events, each with dates, a budget and an audience.
        </EmptyState>
      )}
    </div>
  );
}

function TabReset({ counts, onTab }: { counts: Record<string, number>; onTab: (t: Tab) => void }) {
  const other = TABS.find((t) => counts[t.value] > 0);
  if (!other) return null;
  return (
    <button type="button" className="kg-btn" onClick={() => onTab(other.value)}>
      Show {other.label.toLowerCase()} ({counts[other.value]})
    </button>
  );
}

/** One row of the list (Design Brief §5.6, `CampaignListItem`): what it is, when, how much, and its state in words. */
export function CampaignListItem({ campaign: c }: { campaign: CampaignSummary }) {
  const status = statusOf(c.status);
  const e = c.current_event;
  return (
    <article className="kg-card kg-camp" aria-labelledby={`camp-${c.campaign_id}`}>
      <span className="kg-camp__icon" aria-hidden="true">
        {objectiveLabel(c.objective).slice(0, 1)}
      </span>
      <div className="kg-camp__body">
        <h3 id={`camp-${c.campaign_id}`} className="kg-section">
          <Link to={`/campaign/${c.campaign_id}`}>{c.name}</Link>
        </h3>
        <p className="kg-cap">
          {c.code} · {c.campaign_type}
          {c.owner_name ? ` · owned by ${c.owner_name}` : ""}
        </p>
        <div className="kg-camp__tags">
          <Chip>{objectiveLabel(c.objective)}</Chip>
          {c.channels.length ? <Chip>{c.channels.map(channelLabel).join(" · ")}</Chip> : null}
          {e ? (
            <Chip>
              Event {e.sequence_no} of {c.event_count} · {span(e.period_start, e.period_end)}
            </Chip>
          ) : (
            <Chip>No events yet</Chip>
          )}
          {e ? <Chip>{e.attribution_window_days}-day window</Chip> : null}
          {e?.pending_budget ? <Chip tone="warn">Budget change awaiting approval</Chip> : null}
        </div>
      </div>
      <div className="kg-camp__right">
        <Chip tone={status.tone}>{status.label}</Chip>
        <p className="kg-cap">
          Budget {c.budgets.length ? c.budgets.map((b) => money(b.amount, b.currency)).join(" + ") : "–"}
          {c.event_count > 1 ? ` across ${c.event_count} events` : ""}
        </p>
      </div>
    </article>
  );
}

// ── create ──────────────────────────────────────────────────────────────────

interface CampaignDraft {
  code: string;
  name: string;
  campaign_type: string;
  objective: Objective;
  product_code: string;
  description: string;
  priority: string;
}

type CampaignErrors = Partial<Record<keyof CampaignDraft, string>>;
const CODE = /^[A-Za-z0-9][A-Za-z0-9_.\-/]*$/;

function validateCampaign(d: CampaignDraft): CampaignErrors {
  const errors: CampaignErrors = {};
  if (!d.code.trim()) errors.code = "Give the campaign a short code, e.g. SAVE-Q4.";
  else if (!CODE.test(d.code.trim()) || d.code.length > 64) errors.code = "Letters, digits and _ . - / only, starting with a letter or digit.";
  if (!d.name.trim()) errors.name = "Name the campaign.";
  if (!d.campaign_type.trim()) errors.campaign_type = "Say what kind of campaign it is, e.g. seasonal.";
  else if (!CODE.test(d.campaign_type.trim())) errors.campaign_type = "Letters, digits and _ . - / only.";
  if (d.priority.trim() && !(Number.isInteger(Number(d.priority)) && Number(d.priority) >= 1)) errors.priority = "A rank of 1 or more; 1 wins.";
  return errors;
}

/** Campaign Manager → New campaign (App Flow §5.1). */
export function NewCampaignPage() {
  const [ref, reload] = useQuery("campaign.builder.reference", {});
  return (
    <Page title="New campaign">
      {ref.status === "loading" ? (
        <Loading label="Loading the campaign builder">
          <TableSkeleton rows={6} columns={2} />
        </Loading>
      ) : ref.status === "error" ? (
        <ErrorPanel error={ref.error} retry={reload} what="The campaign builder" />
      ) : (
        <CampaignBuilder reference={ref.data} />
      )}
    </Page>
  );
}

/** The authoring form (Design Brief §5.6, `CampaignBuilder`): campaign details, then repeatable event blocks. */
export function CampaignBuilder({ reference, onCreated }: { reference: Reference; onCreated?: (id: string) => void }) {
  const navigate = useNavigate();
  const [campaign, setCampaign] = useState<CampaignDraft>({ code: "", name: "", campaign_type: "", objective: "deposit_growth", product_code: "", description: "", priority: "" });
  const [events, setEvents] = useState<EventDraft[]>([blankEvent(reference.default_currency)]);
  const [tried, setTried] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  const products = reference.dimensions.find((d) => d.dimension_type === "product")?.members ?? [];
  const window = reference.objectives.find((o) => o.objective === campaign.objective)?.default_window_days;
  const campaignErrors = tried ? validateCampaign(campaign) : {};
  const eventErrors = events.map((e) => (tried ? validateEvent(e) : {}));
  const set = <K extends keyof CampaignDraft>(key: K, v: CampaignDraft[K]) => setCampaign({ ...campaign, [key]: v });

  async function submit(e: FormEvent) {
    e.preventDefault();
    setTried(true);
    setFailure(null);
    if (hasErrors(validateCampaign(campaign)) || events.some((ev) => hasErrors(validateEvent(ev)))) return;
    setBusy(true);
    try {
      const out = await invoke("campaign.create", {
        code: campaign.code.trim(),
        name: campaign.name.trim(),
        campaign_type: campaign.campaign_type.trim(),
        objective: campaign.objective,
        ...(campaign.product_code ? { product_code: campaign.product_code } : {}),
        description: campaign.description,
        ...(campaign.priority.trim() ? { priority: Number(campaign.priority) } : {}),
        events: events.map(eventPayload),
      });
      if (onCreated) onCreated(out.campaign_id);
      else navigate(`/campaign/${out.campaign_id}`);
    } catch (err) {
      setFailure(err instanceof ApiError ? err.message : "The campaign was not saved.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="kg-stack" onSubmit={submit} noValidate aria-label="New campaign">
      <Notice title="Everything saves as a draft.">Nothing reaches a customer until you publish an event from the campaign page. A draft's budget can change freely; once published, a budget change {reference.budget_needs_approval ? "goes to an approver" : "takes effect at once"}.</Notice>
      {failure ? (
        <Notice tone="neg" role="alert">
          {failure}
        </Notice>
      ) : null}
      {tried && (hasErrors(campaignErrors) || eventErrors.some(hasErrors)) ? (
        <Notice tone="neg" role="alert">
          Some fields need attention before the campaign can be saved.
        </Notice>
      ) : null}

      <section className="kg-card kg-stack" aria-labelledby="campaign-details">
        <h2 id="campaign-details" className="kg-section">
          Campaign
        </h2>
        <div className="kg-form-row">
          <TextField label="Name" value={campaign.name} maxLength={120} required error={campaignErrors.name} onChange={(e) => set("name", e.target.value)} />
          <TextField label="Code" value={campaign.code} maxLength={64} required error={campaignErrors.code} hint="Your systems' tag, if they tag transactions to campaigns" onChange={(e) => set("code", e.target.value)} />
          <TextField label="Type" value={campaign.campaign_type} maxLength={64} required error={campaignErrors.campaign_type} hint="e.g. seasonal, always_on" onChange={(e) => set("campaign_type", e.target.value)} />
        </div>
        <div className="kg-form-row">
          <SelectField label="Objective" value={campaign.objective} hint="Decides which outcomes count. Fixed once an event is published." onChange={(e) => set("objective", e.target.value as Objective)} options={OBJECTIVES} />
          {products.length ? (
            <SelectField label="Product" value={campaign.product_code} onChange={(e) => set("product_code", e.target.value)} options={[{ value: "", label: "None" }, ...products.map((p) => ({ value: p.member_code, label: p.member_name }))]} />
          ) : (
            <TextField label="Product" value={campaign.product_code} hint="Optional" onChange={(e) => set("product_code", e.target.value)} />
          )}
          <TextField label="Priority" type="number" min={1} value={campaign.priority} error={campaignErrors.priority} hint="Optional. Used when the collision rule is priority: 1 wins." onChange={(e) => set("priority", e.target.value)} />
        </div>
        <TextField label="Description" value={campaign.description} maxLength={2000} hint="Optional: what it offers, and to whom" onChange={(e) => set("description", e.target.value)} />
      </section>

      {events.map((ev, i) => (
        <section key={i} className="kg-card kg-stack" aria-labelledby={`event-${i}`}>
          <div className="kg-row">
            <h2 id={`event-${i}`} className="kg-section">
              Event {i + 1}
            </h2>
            {events.length > 1 ? (
              <button type="button" className="kg-btn" onClick={() => setEvents(events.filter((_, j) => j !== i))}>
                Remove event {i + 1}
              </button>
            ) : null}
          </div>
          <EventFields value={ev} onChange={(next) => setEvents(events.map((x, j) => (j === i ? next : x)))} errors={eventErrors[i]} dimensions={reference.dimensions} currencies={reference.currencies} defaultWindow={window} />
        </section>
      ))}

      <div className="kg-row" style={{ justifyContent: "flex-start", flexWrap: "wrap" }}>
        <button type="button" className="kg-btn" onClick={() => setEvents([...events, blankEvent(events[events.length - 1]?.budget_currency ?? reference.default_currency)])}>
          + Add another event
        </button>
        <span className="kg-spacer" />
        <Link className="kg-btn" to="/campaign">
          Cancel
        </Link>
        <button type="submit" className="kg-btn kg-btn--primary" disabled={busy}>
          {busy ? "Saving…" : "Save as draft"}
        </button>
      </div>
    </form>
  );
}

// ── one campaign ────────────────────────────────────────────────────────────

/** Campaign Manager → a campaign: manage its events, budgets and audience (App Flow §5.2). */
export function CampaignPage() {
  const { campaignId = "" } = useParams();
  const me = useMe();
  const canManage = me.permissions.includes("campaign.manage");
  const [data, reload] = useQuery("campaign.get", { campaign_id: campaignId });
  const title = data.status === "ready" ? data.data.name : "Campaign";
  return (
    <Page
      title={title}
      actions={
        <Link className="kg-btn" to="/campaign">
          All campaigns
        </Link>
      }
    >
      {data.status === "loading" ? (
        <Loading label="Loading the campaign">
          <TableSkeleton rows={5} columns={3} />
        </Loading>
      ) : data.status === "error" ? (
        data.error.status === 404 ? (
          <EmptyState kind="no-access" title="This campaign is not in your scope" ask="an Admin, under Administer → Users & access">
            It may not exist, or no Campaign scope grant of yours covers it.
          </EmptyState>
        ) : (
          <ErrorPanel error={data.error} retry={reload} what="The campaign" />
        )
      ) : canManage ? (
        <ManagedCampaign campaign={data.data} onChanged={reload} />
      ) : (
        <CampaignDetailView campaign={data.data} canManage={false} />
      )}
    </Page>
  );
}

function ManagedCampaign({ campaign, onChanged }: { campaign: Parameters<typeof CampaignDetailView>[0]["campaign"]; onChanged: () => void }) {
  const [ref, reload] = useQuery("campaign.builder.reference", {});
  if (ref.status === "loading") {
    return (
      <Loading label="Loading the campaign">
        <TableSkeleton rows={5} columns={3} />
      </Loading>
    );
  }
  if (ref.status === "error") return <ErrorPanel error={ref.error} retry={reload} what="The campaign builder" />;
  return <CampaignDetailView campaign={campaign} reference={ref.data} canManage onChanged={onChanged} />;
}

