import type { Input, Output } from "../../api/actions";
import type { ChipTone } from "../../components/Chip";
import { formatDate, formatValue } from "../../lib/format";

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
  };
}

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
  };
}
