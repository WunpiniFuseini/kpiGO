import type { Me } from "../session/Session";
import type { Campaign, CampaignEvent, CampaignList, CampaignSummary, Reference } from "../pages/campaigns/model";
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
