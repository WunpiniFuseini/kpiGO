import type { Me } from "../session/Session";
import type { Board, BoardMoney, BoardRow, Campaign, CampaignEvent, CampaignList, CampaignSummary, Estimate, EventReach, EventValue, Reach, Reconciliation, Reference, ValueReport, Winbacks } from "../pages/campaigns/model";
import { adminMe } from "./fixtures";

export const campaignManagerMe: Me = {
  ...adminMe,
  user: { ...adminMe.user, display_name: "Efua Owusu", email: "efua.owusu@bank.example", roles: ["campaign_manager"] },
  permissions: ["auth.session", "campaign.view", "campaign.manage", "dimension.view"],
  pages: [{ page_key: "campaign", label: "Campaign Manager", group: "modules", access: "edit" }] as Me["pages"],
  home: "campaign",
};

export const campaignViewerMe: Me = {
  ...campaignManagerMe,
  user: { ...campaignManagerMe.user, display_name: "Yaw Darko", roles: ["executive"] },
  permissions: ["auth.session", "campaign.view"],
  pages: [{ page_key: "campaign", label: "Campaign Manager", group: "modules", access: "view" }] as Me["pages"],
};

export const reference: Reference = {
  currencies: ["GHS", "USD"],
  default_currency: "GHS",
  dimensions: [
    {
      dimension_type: "segment",
      display_name: "Segment",
      members: [
        { member_code: "retail", member_name: "Retail", parent_code: null },
        { member_code: "mass", member_name: "Mass market", parent_code: "retail" },
        { member_code: "affluent", member_name: "Affluent", parent_code: "retail" },
        { member_code: "sme", member_name: "SME", parent_code: null },
      ],
    },
    {
      dimension_type: "product",
      display_name: "Product",
      members: [
        { member_code: "savings", member_name: "Savings", parent_code: null },
        { member_code: "cards", member_name: "Cards", parent_code: null },
      ],
    },
    {
      dimension_type: "region",
      display_name: "Region",
      members: [
        { member_code: "GA", member_name: "Greater Accra", parent_code: null },
        { member_code: "AS", member_name: "Ashanti", parent_code: null },
      ],
    },
  ],
  objectives: [
    { objective: "acquisition", default_window_days: 45 },
    { objective: "deposit_growth", default_window_days: 30 },
    { objective: "activation", default_window_days: 30 },
    { objective: "cross_sell", default_window_days: 60 },
    { objective: "attrition_winback", default_window_days: 90 },
    { objective: "collections", default_window_days: 30 },
    { objective: "awareness", default_window_days: 30 },
  ],
  channels: ["sms", "email", "call", "ussd", "branch", "app_push", "whatsapp"],
  budget_needs_approval: true,
};

export function campaignEvent(over: Partial<CampaignEvent> = {}): CampaignEvent {
  return {
    event_id: "e1000000-0000-4000-8000-000000000001",
    campaign_id: "c1000000-0000-4000-8000-000000000001",
    sequence_no: 1,
    event_name: "October SMS wave",
    period_start: "2026-10-01",
    period_end: "2026-10-31",
    attribution_window_days: 30,
    window_end: "2026-11-30",
    budget_amount: "25000.00",
    budget_currency: "GHS",
    spend_to_date: null,
    channels: ["sms", "app_push"],
    audience: [
      { dimension_type: "segment", member_code: "retail" },
      { dimension_type: "region", member_code: "GA" },
    ],
    holdout_pct: 10,
    state: "live",
    status: "running",
    version: 2,
    reattribute_from: null,
    pending_budget: null,
    ...over,
  };
}

export const campaignDetail: Campaign = {
  campaign_id: "c1000000-0000-4000-8000-000000000001",
  code: "CASA-Q4",
  name: "Q4 CASA Balance Drive",
  campaign_type: "seasonal",
  objective: "deposit_growth",
  product_code: "savings",
  owner_user_id: 7,
  owner_name: "Efua Owusu",
  description: "Top-up prompt to dormant-leaning savers across SMS and app push.",
  priority: 2,
  status: "running",
  budget_needs_approval: true,
  events: [
    campaignEvent({
      pending_budget: { approval_request_id: "a1000000-0000-4000-8000-000000000001", budget_amount: "40000", budget_currency: "GHS", requested_by_id: 7, requested_at: "2026-10-04T09:30:00Z" },
    }),
    campaignEvent({
      event_id: "e1000000-0000-4000-8000-000000000002",
      sequence_no: 2,
      event_name: "November SMS wave",
      period_start: "2026-11-01",
      period_end: "2026-11-30",
      window_end: "2026-12-30",
      state: "draft",
      status: "draft",
      version: 1,
      audience: [{ dimension_type: "segment", member_code: "retail" }],
    }),
  ],
};

export const campaignAllDrafts: Campaign = {
  ...campaignDetail,
  status: "draft",
  events: [campaignEvent({ state: "draft", status: "draft", version: 1, audience: [] })],
};

export const campaignNoEvents: Campaign = { ...campaignDetail, status: "draft", events: [] };

function summary(over: Partial<CampaignSummary>): CampaignSummary {
  return {
    campaign_id: "c1000000-0000-4000-8000-000000000001",
    code: "CASA-Q4",
    name: "Q4 CASA Balance Drive",
    campaign_type: "seasonal",
    objective: "deposit_growth",
    product_code: "savings",
    owner_user_id: 7,
    owner_name: "Efua Owusu",
    status: "running",
    event_count: 4,
    current_event: campaignEvent({ sequence_no: 3 }),
    channels: ["sms", "app_push"],
    budgets: [{ currency: "GHS", amount: "100000.00" }],
    ...over,
  };
}

export const campaignList: CampaignList = {
  scoped: true,
  campaigns: [
    summary({}),
    summary({
      campaign_id: "c1000000-0000-4000-8000-000000000002",
      code: "WINBACK-CA",
      name: "Lapsed Current Account Win-back",
      campaign_type: "always_on",
      objective: "attrition_winback",
      event_count: 2,
      channels: ["call", "sms"],
      current_event: campaignEvent({ sequence_no: 2, event_name: "July calls", period_start: "2026-09-08", period_end: "2026-10-06", attribution_window_days: 90, channels: ["call", "sms"], pending_budget: { approval_request_id: "a1", budget_amount: "9000", budget_currency: "GHS", requested_by_id: 7, requested_at: "2026-10-04T09:30:00Z" } }),
      budgets: [{ currency: "GHS", amount: "18000.00" }],
    }),
    summary({
      campaign_id: "c1000000-0000-4000-8000-000000000003",
      code: "CARD-ACT",
      name: "Card Activation Push",
      objective: "activation",
      status: "scheduled",
      event_count: 1,
      current_event: campaignEvent({ period_start: "2026-11-01", period_end: "2026-11-30", status: "scheduled" }),
    }),
    summary({
      campaign_id: "c1000000-0000-4000-8000-000000000004",
      code: "SME-WC",
      name: "SME Working Capital Offer",
      objective: "cross_sell",
      status: "draft",
      event_count: 0,
      current_event: null,
      channels: [],
      budgets: [],
    }),
  ],
};

export const campaignListEmpty: CampaignList = { scoped: true, campaigns: [] };
export const campaignListNoScope: CampaignList = { scoped: false, campaigns: [] };

// ── reach ───────────────────────────────────────────────────────────────────

export const estimate: Estimate = { targeted: 48200, population: 312000, as_of: "2026-09-30", reason: null, dimensions: [] };
export const estimateNoPopulation: Estimate = { targeted: null, population: null, as_of: null, reason: "no_population", dimensions: [] };
export const estimateNotBrokenDown: Estimate = { targeted: null, population: 312000, as_of: "2026-09-30", reason: "dimension_not_in_population", dimensions: ["region"] };

function eventReach(over: Partial<EventReach>): EventReach {
  return {
    event_id: "e1000000-0000-4000-8000-000000000001",
    estimate,
    contacted: null,
    held_out: null,
    delivered: null,
    responded: null,
    matched_customers: null,
    converted_customers: null,
    credited_outcomes: null,
    attributed: [],
    ...over,
  };
}

/** Event 1 running with every feed in; event 2 a draft, sized but not yet counting. */
export const campaignReach: Reach = {
  campaign_id: campaignDetail.campaign_id,
  attribution_rule: "last_touch",
  outcome_metric_codes: ["cmp_deposit_value"],
  outcomes_fed: true,
  contacts_fed: true,
  events: [
    eventReach({
      contacted: 41250,
      held_out: 4500,
      delivered: 39800,
      responded: 5120,
      matched_customers: 2310,
      converted_customers: 1985,
      credited_outcomes: 2140,
      attributed: [{ currency: "GHS", amount: "4182500.0000" }],
    }),
    eventReach({ event_id: "e1000000-0000-4000-8000-000000000002", estimate: { ...estimate, targeted: 61000 } }),
  ],
};

/** Nothing fed yet and no outcome metrics named: every figure says why it is missing. */
export const campaignReachNothingFed: Reach = {
  ...campaignReach,
  outcome_metric_codes: [],
  outcomes_fed: false,
  contacts_fed: false,
  events: campaignReach.events.map((e) => eventReach({ event_id: e.event_id, estimate: estimateNoPopulation })),
};

// ── value and return ────────────────────────────────────────────────────────

const noControl: EventValue["control"] = {
  planned_pct: null,
  treated: null,
  control: null,
  actual_pct: null,
  treated_rate: null,
  control_rate: null,
  lift_points: null,
  incremental: null,
  small: false,
  reason: "no_contacts_fed",
};

export function eventValue(over: Partial<EventValue> = {}): EventValue {
  return {
    event_id: "e1000000-0000-4000-8000-000000000001",
    currency: "GHS",
    budget: "25000.00",
    spend_to_date: null,
    baseline_start: "2026-08-02",
    baseline_end: "2026-10-01",
    gross: "4182500.0000",
    other_currencies: [],
    baseline: "3610000.0000",
    incremental: "572500.0000",
    withheld: null,
    converted_customers: 1985,
    new_customers: 212,
    contaminated_customers: 0,
    roi: "21.9000",
    gross_roi: "166.3000",
    roi_reason: null,
    cost_per_outcome: "12.59",
    utilisation: null,
    control: {
      planned_pct: 10,
      treated: 41250,
      control: 4500,
      actual_pct: "9.8",
      treated_rate: "0.0481",
      control_rate: "0.0342",
      lift_points: "1.39",
      incremental: "498000.00",
      small: false,
      reason: null,
    },
    ...over,
  };
}

/** Event 1 measured both ways; event 2 a draft. */
export const campaignValue: ValueReport = {
  campaign_id: campaignDetail.campaign_id,
  basis: "incremental",
  basis_changed_at: null,
  events: [
    eventValue(),
    eventValue({
      event_id: "e1000000-0000-4000-8000-000000000002",
      gross: null,
      baseline: null,
      incremental: null,
      withheld: "not_published",
      converted_customers: null,
      new_customers: null,
      contaminated_customers: null,
      roi: null,
      gross_roi: null,
      roi_reason: "not_published",
      cost_per_outcome: null,
      control: { ...noControl, reason: "not_published" },
    }),
  ],
};

/** Another event reached some customers in the baseline window: incremental is withheld, gross stands. */
export const eventValueContaminated = eventValue({
  baseline: null,
  incremental: null,
  withheld: "contaminated_baseline",
  contaminated_customers: 143,
  roi: null,
  roi_reason: "contaminated_baseline",
  control: { ...noControl, planned_pct: 10, treated: 41250, control: 0, reason: "no_control_group" },
});

/** A control group too small to lean on. */
export const eventValueSmallControl = eventValue({
  control: { ...eventValue().control, control: 18, actual_pct: "0.1", small: true },
});

export const eventValueNothingFed = eventValue({
  gross: null,
  baseline: null,
  incremental: null,
  withheld: "no_outcomes_fed",
  converted_customers: null,
  new_customers: null,
  contaminated_customers: null,
  roi: null,
  gross_roi: null,
  roi_reason: "no_outcomes_fed",
  cost_per_outcome: null,
  control: noControl,
});

/** Gross leads since the basis was changed: every value screen carries the banner. */
export const campaignValueGross: ValueReport = {
  ...campaignValue,
  basis: "gross",
  basis_changed_at: "2026-10-03T09:12:00Z",
  events: [eventValue({ roi: "166.3000" }), campaignValue.events[1]],
};

// ── win-backs ───────────────────────────────────────────────────────────────

/** An attrition win-back campaign: event 1 running, event 2 a draft. */
export const campaignWinbackDetail: Campaign = {
  ...campaignDetail,
  code: "LAPSED-CA",
  name: "Lapsed Current Account Win-back",
  objective: "attrition_winback",
  description: "Outbound calling to accounts dormant 90+ days, waived reactivation fee.",
};

export const campaignWinbacks: Winbacks = {
  campaign_id: campaignDetail.campaign_id,
  retention_days: 90,
  fed: true,
  earns_winbacks: true,
  events: [
    { event_id: "e1000000-0000-4000-8000-000000000001", counts: { qualified: 754, provisional: 558, confirmed: 184, lapsed: 12, next_confirmation: "2026-10-09" } },
    { event_id: "e1000000-0000-4000-8000-000000000002", counts: null },
  ],
};

export const campaignWinbacksNothingFed: Winbacks = {
  ...campaignWinbacks,
  fed: false,
  events: campaignWinbacks.events.map((e) => ({ ...e, counts: null })),
};

// ── tracking board ──────────────────────────────────────────────────────────

function boardMoney(over: Partial<BoardMoney> = {}): BoardMoney {
  return {
    currency: "GHS",
    budget: "118000.00",
    spend: "86400.00",
    gross: "412300.0000",
    incremental: "198600.0000",
    roi: "0.6831",
    gross_roi: "2.4941",
    measured_events: 3,
    withheld_events: 0,
    ...over,
  };
}

function boardRow(campaign_id: string, over: Partial<BoardRow> = {}): BoardRow {
  return { campaign_id, events_in_play: 1, contacted: 41200, converted: 3180, money: [boardMoney()], winbacks: null, ...over };
}

/** Q4 to date: three events in play, the win-back campaign's counts, and a USD budget summed apart. */
export const campaignBoard: Board = {
  start: "2026-10-01",
  end: "2026-10-05",
  basis: "incremental",
  outcomes_fed: true,
  contacts_fed: true,
  money: [boardMoney(), boardMoney({ currency: "USD", budget: "5000.00", spend: null, gross: "7200.0000", incremental: "2100.0000", roi: "-0.5800", gross_roi: "0.4400", measured_events: 1 })],
  winbacks: { qualified: 754, provisional: 558, confirmed: 184, lapsed: 12, next_confirmation: "2026-10-09" },
  campaigns: [
    boardRow("c1000000-0000-4000-8000-000000000001", { events_in_play: 2 }),
    boardRow("c1000000-0000-4000-8000-000000000002", {
      contacted: 2900,
      converted: 754,
      money: [boardMoney({ budget: "18000.00", spend: "12100.00", gross: "96400.0000", incremental: null, roi: null, gross_roi: "4.3556", measured_events: 1, withheld_events: 1 })],
      winbacks: { qualified: 754, provisional: 558, confirmed: 184, lapsed: 12, next_confirmation: "2026-10-09" },
    }),
    boardRow("c1000000-0000-4000-8000-000000000003", { events_in_play: 0, contacted: null, converted: null, money: [] }),
    boardRow("c1000000-0000-4000-8000-000000000004", { events_in_play: 0, contacted: null, converted: null, money: [] }),
  ],
};

/** One event's baseline is contaminated, so the quarter's incremental value is withheld. */
export const campaignBoardWithheld: Board = {
  ...campaignBoard,
  money: [boardMoney({ incremental: null, roi: null, withheld_events: 1 })],
  winbacks: null,
};

/** Nothing runs or attributes this quarter. */
export const campaignBoardEmpty: Board = {
  ...campaignBoard,
  money: [],
  winbacks: null,
  campaigns: campaignBoard.campaigns.map((r) => ({ ...r, events_in_play: 0, contacted: null, converted: null, money: [], winbacks: null })),
};

// ── reconciliation ──────────────────────────────────────────────────────────

/** Event 1 won most of what it matched and lost some to another campaign under last touch. */
export const campaignReconciliation: Reconciliation = {
  campaign_id: campaignDetail.campaign_id,
  span_start: "2026-10-01",
  span_end: "2026-11-30",
  lines: [
    { metric_code: "deposit_value", currency: "GHS", source_total: "1284000.0000", source_outcomes: 9120, credited_here: "412300.0000", credited_elsewhere: "96400.0000", unattributed: "775300.0000" },
    { metric_code: "new_accounts", currency: null, source_total: "2210.0000", source_outcomes: 2210, credited_here: "640.0000", credited_elsewhere: "112.0000", unattributed: "1458.0000" },
  ],
  events: [
    {
      event_id: "e1000000-0000-4000-8000-000000000001",
      credited: "412940.0000",
      credited_outcomes: 3180,
      lost: "38200.0000",
      lost_outcomes: 214,
      held_out_outcomes: 162,
      by_rule: { single: 2961, last_touch: 433, holdout: 162 },
    },
  ],
  contaminated: [
    { customer_ref: "CUST-004812", event_id: "e1000000-0000-4000-8000-000000000001", contaminated_by: "e9000000-0000-4000-8000-000000000001" },
    { customer_ref: "CUST-019377", event_id: "e1000000-0000-4000-8000-000000000001", contaminated_by: null },
  ],
  contaminated_total: 2,
  invariant_holds: true,
};

/** Published, but no outcome of the objective's metrics has loaded for its span. */
export const campaignReconciliationNoOutcomes: Reconciliation = { ...campaignReconciliation, lines: [], contaminated: [], contaminated_total: 0, events: campaignReconciliation.events.map((e) => ({ ...e, credited: "0.0000", credited_outcomes: 0, lost: "0.0000", lost_outcomes: 0, held_out_outcomes: 0, by_rule: {} })) };

/** Nothing published, so there is no span to reconcile. */
export const campaignReconciliationNothingPublished: Reconciliation = { ...campaignReconciliationNoOutcomes, span_start: null, span_end: null, events: [] };
