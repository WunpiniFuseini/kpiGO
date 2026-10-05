import type { Input, Output } from "../../api/actions";
import type { ChipTone } from "../../components/Chip";
import { formatDate, formatDelta, formatValue } from "../../lib/format";

export type CampaignList = Output<"campaign.list">;
export type CampaignSummary = CampaignList["campaigns"][number];
export type Campaign = Output<"campaign.get">;
export type CampaignEvent = Campaign["events"][number];
export type Reference = Output<"campaign.builder.reference">;
export type Dimension = Reference["dimensions"][number];
export type Criterion = { dimension_type: string; member_code: string };
export type Objective = Input<"campaign.create">["objective"];
export type Channel = NonNullable<NonNullable<Input<"campaign.create">["events"]>[number]["channels"]>[number];
export type Tab = "running" | "scheduled" | "paused" | "draft" | "closed";
export type Reach = Output<"campaign.reach">;
export type EventReach = Reach["events"][number];
export type Estimate = Output<"campaign.audience.estimate">;
export type Rule = Reach["attribution_rule"];
export type ValueReport = Output<"campaign.value">;
export type EventValue = ValueReport["events"][number];
export type Basis = ValueReport["basis"];
export type Winbacks = Output<"campaign.winbacks">;
export type WinbackCounts = NonNullable<Winbacks["events"][number]["counts"]>;
export type Board = Output<"campaign.board">;
export type BoardRow = Board["campaigns"][number];
export type BoardMoney = Board["money"][number];
export type Reconciliation = Output<"campaign.reconciliation">;

export const OBJECTIVES: { value: Objective; label: string }[] = [
  { value: "deposit_growth", label: "Deposit growth" },
  { value: "acquisition", label: "Acquisition" },
  { value: "activation", label: "Activation" },
  { value: "cross_sell", label: "Cross-sell" },
  { value: "attrition_winback", label: "Attrition win-back" },
  { value: "collections", label: "Collections" },
  { value: "awareness", label: "Awareness" },
];

export const CHANNELS: { value: Channel; label: string }[] = [
  { value: "sms", label: "SMS" },
  { value: "email", label: "Email" },
  { value: "call", label: "Call" },
  { value: "ussd", label: "USSD" },
  { value: "app_push", label: "App push" },
  { value: "whatsapp", label: "WhatsApp" },
  { value: "branch", label: "Branch" },
];

export const TABS: { value: Tab; label: string }[] = [
  { value: "running", label: "Running" },
  { value: "scheduled", label: "Scheduled" },
  { value: "paused", label: "Paused" },
  { value: "draft", label: "Drafts" },
  { value: "closed", label: "Closed" },
];

export function objectiveLabel(code: string): string {
  return OBJECTIVES.find((o) => o.value === code)?.label ?? code;
}

export function channelLabel(code: string): string {
  return CHANNELS.find((c) => c.value === code)?.label ?? code;
}

/** Status is state, not standing: it uses the state chips, never the grade ramp, and always says itself in words. */
const STATUS: Record<string, { label: string; tone: ChipTone }> = {
  draft: { label: "Draft", tone: "flat" },
  scheduled: { label: "Scheduled", tone: "info" },
  running: { label: "Running", tone: "up" },
  paused: { label: "Paused", tone: "warn" },
  closed: { label: "Closed", tone: "flat" },
};

export function statusOf(code: string): { label: string; tone: ChipTone } {
  return STATUS[code] ?? { label: code, tone: "flat" };
}

export function money(amount: string, currency: string): string {
  return formatValue(Number(amount), { unit: "currency", currency, decimals: 0 });
}

export function span(start: string, end: string): string {
  return start === end ? formatDate(start) : `${formatDate(start)} – ${formatDate(end)}`;
}

/** "segment: Retail (and below) · region: GA": how the criteria combine, in words. */
export function describeAudience(criteria: Criterion[], dimensions: Dimension[] = []): string {
  if (!criteria.length) return "No audience yet";
  const byType = new Map<string, string[]>();
  for (const c of criteria) {
    const dim = dimensions.find((d) => d.dimension_type === c.dimension_type);
    const member = dim?.members.find((m) => m.member_code === c.member_code);
    const below = dim?.members.some((m) => m.parent_code === c.member_code) ? " and below" : "";
    const names = byType.get(c.dimension_type) ?? [];
    names.push(`${member?.member_name ?? c.member_code}${below}`);
    byType.set(c.dimension_type, names);
  }
  return Array.from(byType.entries())
    .map(([type, names]) => {
      const label = dimensions.find((d) => d.dimension_type === type)?.display_name ?? type;
      return `${label}: ${names.join(" or ")}`;
    })
    .join(", and ");
}

// ── reach ───────────────────────────────────────────────────────────────────

export const RULES: Record<Rule, string> = {
  last_touch: "last touch",
  first_touch: "first touch",
  priority: "campaign priority",
  split_even: "even split",
};

export function count(n: number): string {
  return formatValue(n, { unit: "count" });
}

/** What share `part` is of `whole`, in words; empty when either is unknown. */
export function shareOf(part: number | null, whole: number | null): string {
  if (part === null || whole === null || whole <= 0) return "";
  const pct = (part / whole) * 100;
  return `${pct < 1 && pct > 0 ? "under 1" : Math.round(pct)}%`;
}

/** The reach estimate in a sentence, or why there is none. */
export function describeEstimate(e: Estimate, dimensions: Dimension[] = []): string {
  const asOf = e.as_of ? ` on ${formatDate(e.as_of)}` : "";
  if (e.targeted !== null) {
    const of = e.population ? ` of ${count(e.population)} (${shareOf(e.targeted, e.population)})` : "";
    return `About ${count(e.targeted)} customers${of}, from the customer population${asOf}.`;
  }
  switch (e.reason) {
    case "no_population":
      return "No customer population has been fed yet, so the audience cannot be sized. The DE team's population feed supplies the counts.";
    case "dimension_not_in_population": {
      const names = e.dimensions.map((d) => dimensions.find((x) => x.dimension_type === d)?.display_name ?? d);
      return `The customer population${asOf} is not broken down by ${names.join(" or ")}, so this audience cannot be sized.`;
    }
    default:
      return e.population ? `Add a criterion to size the audience. The population${asOf} holds ${count(e.population)} customers.` : "Add a criterion to size the audience.";
  }
}

// ── the event form ──────────────────────────────────────────────────────────

export interface EventDraft {
  event_name: string;
  period_start: string;
  period_end: string;
  /** Blank: the objective's default. */
  attribution_window_days: string;
  budget_amount: string;
  budget_currency: string;
  channels: string[];
  audience: Criterion[];
  /** Blank: no control group. */
  holdout_pct: string;
}

export type EventErrors = Partial<Record<keyof EventDraft, string>>;

export function blankEvent(currency: string | null | undefined): EventDraft {
  return {
    event_name: "",
    period_start: "",
    period_end: "",
    attribution_window_days: "",
    budget_amount: "",
    budget_currency: currency ?? "",
    channels: [],
    audience: [],
    holdout_pct: "",
  };
}

export function draftOf(e: CampaignEvent): EventDraft {
  return {
    event_name: e.event_name,
    period_start: e.period_start,
    period_end: e.period_end,
    attribution_window_days: String(e.attribution_window_days),
    budget_amount: e.budget_amount,
    budget_currency: e.budget_currency,
    channels: e.channels,
    audience: e.audience,
    holdout_pct: e.holdout_pct === null ? "" : String(e.holdout_pct),
  };
}

export const MAX_HOLDOUT_PCT = 50;

const AMOUNT = /^\d{1,16}(\.\d{1,2})?$/;

export function budgetError(value: string): string | undefined {
  const v = value.trim().replace(/,/g, "");
  if (!v) return "Give the event a budget. Use 0 if it costs nothing.";
  if (!AMOUNT.test(v)) return "A budget is a positive amount with at most two decimals.";
  return undefined;
}

/** Inline validation on dates and budget (Design Brief §5.6). `withBudget` is false for a live event. */
export function validateEvent(d: EventDraft, { withBudget = true }: { withBudget?: boolean } = {}): EventErrors {
  const errors: EventErrors = {};
  if (!d.event_name.trim()) errors.event_name = "Name the event, e.g. “October SMS wave”.";
  if (!d.period_start) errors.period_start = "Choose the first contact day.";
  if (!d.period_end) errors.period_end = "Choose the last contact day.";
  else if (d.period_start && d.period_end < d.period_start) errors.period_end = "The event ends on or after the day it starts.";
  if (d.attribution_window_days.trim()) {
    const w = Number(d.attribution_window_days);
    if (!Number.isInteger(w) || w < 0 || w > 730) errors.attribution_window_days = "A window is 0 to 730 whole days.";
  }
  if (d.holdout_pct.trim()) {
    const h = Number(d.holdout_pct);
    if (!Number.isInteger(h) || h < 1 || h > MAX_HOLDOUT_PCT) errors.holdout_pct = `A control group is 1 to ${MAX_HOLDOUT_PCT}% of the audience, in whole numbers.`;
  }
  if (withBudget) {
    const b = budgetError(d.budget_amount);
    if (b) errors.budget_amount = b;
    if (!d.budget_currency) errors.budget_currency = "Choose a currency.";
  }
  return errors;
}

export function hasErrors(errors: object): boolean {
  return Object.values(errors).some(Boolean);
}

/** The payload campaign.create and campaign.event.add take. */
export function eventPayload(d: EventDraft) {
  return {
    event_name: d.event_name.trim(),
    period_start: d.period_start,
    period_end: d.period_end,
    ...(d.attribution_window_days.trim() ? { attribution_window_days: Number(d.attribution_window_days) } : {}),
    budget_amount: d.budget_amount.trim().replace(/,/g, ""),
    budget_currency: d.budget_currency,
    channels: d.channels as Channel[],
    audience: d.audience,
    ...(d.holdout_pct.trim() ? { holdout_pct: Number(d.holdout_pct) } : {}),
  };
}

// ── value and return ────────────────────────────────────────────────────────

export const BASES: Record<Basis, string> = { incremental: "incremental", gross: "gross" };

/** A ratio such as "-0.9972" as a signed percentage, "−99.7%". */
export function percent(ratio: string | null, decimals = 1): string {
  if (ratio === null) return "—";
  return `${formatDelta(Number(ratio) * 100, decimals).replace(/^\+/, "")}%`;
}

/** Why incremental value is not shown; the gross figure still stands. */
export function withheldText(v: EventValue): string {
  switch (v.withheld) {
    case "contaminated_baseline": {
      const n = v.contaminated_customers ?? 0;
      return `Withheld: another event reached ${count(n)} of these ${n === 1 ? "customer" : "customers"} in the baseline window, so their earlier value does not show what happens without a campaign.`;
    }
    case "history_too_short":
      return `Withheld: the outcome feed does not reach back to ${formatDate(v.baseline_start)}, so the baseline would be incomplete.`;
    case "no_outcomes_fed":
      return "No outcomes have loaded yet.";
    case "not_published":
      return "Value is measured once the event is published.";
    default:
      return "";
  }
}

export function roiText(v: EventValue): string {
  switch (v.roi_reason) {
    case "no_budget":
      return "No budget, so no return to measure.";
    case "no_value_in_budget_currency":
      return `None of the credited value is in ${v.currency}, the budget's currency, so a return cannot be worked out.`;
    default:
      return "";
  }
}

/** The control group in a sentence: why there is no lift, or how far to lean on it. */
export function controlText(v: EventValue): string {
  const c = v.control;
  switch (c.reason) {
    case "no_contacts_fed":
      return c.planned_pct ? `A ${c.planned_pct}% control group is planned. The contact feed marks who was held out; none has loaded yet.` : "";
    case "no_control_group":
      return c.planned_pct ? `A ${c.planned_pct}% control group was planned, but the contact feed marks no one as held out.` : "No control group: every customer in the feed was contacted. The baseline is the only measure of lift.";
    case "no_outcomes_fed":
      return "The control group is known; lift follows once outcomes load.";
    default:
      return c.small ? "The control group has fewer than 30 customers, so read the lift as indicative only." : "";
  }
}
