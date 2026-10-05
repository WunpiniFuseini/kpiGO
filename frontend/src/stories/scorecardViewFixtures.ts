/** Story data for the Scorecard page: one RM's month in every state it can be in. */
import type { Output } from "../api/actions";
import type { Me } from "../session/Session";
import { staffMe } from "./fixtures";

type Card = Output<"scorecard.compute">;
type MetricRow = Card["metrics"][number];
type Thread = Output<"scorecard.interaction.list">;
type Interaction = Thread["queries"][number];

export const SUBJECT = "8a7b6c5d-4e3f-4a1b-9c8d-7e6f5a4b3c2d";
export const REPORT = "1b2c3d4e-5f60-4718-8a9b-0c1d2e3f4a5b";
const MANAGER = "2c3d4e5f-6071-4829-9bac-1d2e3f4a5b6c";

export const rmMe: Me = {
  ...staffMe,
  user: { ...staffMe.user, subject_id: SUBJECT, display_name: "Ama Boateng" },
  permissions: ["auth.session", "scorecard.view", "scorecard.acknowledge", "scorecard.query"],
};

export const managerMe: Me = {
  ...rmMe,
  user: { ...rmMe.user, subject_id: MANAGER, display_name: "Kwame Wellington", roles: ["line_manager"] },
  permissions: [...rmMe.permissions, "scorecard.query.resolve", "scorecard.comment", "override.view", "override.request"],
};

const BANDS: Card["bands"] = [
  { label: "Needs Focus", threshold: "0", ramp_position: 1, colour_hex: null },
  { label: "Gaining Momentum", threshold: "0.75", ramp_position: 2, colour_hex: null },
  { label: "On Target", threshold: "1.0", ramp_position: 3, colour_hex: null },
  { label: "Exemplary", threshold: "1.2", ramp_position: 4, colour_hex: null },
];

function metric(
  code: string,
  name: string,
  path: string,
  bits: Partial<MetricRow> & { target: string | null; actual: string | null; weight: string; cap: string; pct: string | null; score: string | null },
): MetricRow {
  const { target, actual, weight, cap, pct, score, ...rest } = bits;
  return {
    metric_id: `00000000-0000-4000-8000-${code.length.toString().padStart(12, "0")}`,
    metric_code: code,
    display_name: name,
    direction: "higher_is_better",
    unit: "currency",
    decimal_places: 0,
    path: [path],
    state: score === null ? "not_reported" : "scored",
    target_id: target === null ? null : "5f6e7d8c-9b0a-4c1d-8e2f-3a4b5c6d7e8f",
    target_version: target === null ? null : 2,
    target_scope: target === null ? null : "profile",
    base_target: target,
    target_type: "monthly",
    target_currency: "GHS",
    target_value: target,
    reported_actual: actual,
    actual_currency: actual === null ? null : "GHS",
    fx_rate: null,
    actual_value: actual,
    run_id: actual === null ? null : "c0ffee00-1234-4abc-8def-0123456789ab",
    weight,
    cap,
    pct_achieved: pct,
    score,
    overrides: [],
    exclusion_reason: null,
    ...rest,
  };
}

const METRICS: MetricRow[] = [
  metric("casa_growth", "CASA balance growth", "Grow the balance sheet", { target: "4200000", actual: "4860000", weight: "20", cap: "30", pct: "1.157143", score: "0.231429" }),
  metric("ntb_accounts", "New-to-bank accounts", "Grow the balance sheet", {
    unit: "count",
    target_currency: null,
    actual_currency: null,
    target: "45",
    actual: "38",
    weight: "15",
    cap: "22.5",
    pct: "0.844444",
    score: "0.126667",
  }),
  metric("service_tat", "Service TAT", "Serve reliably", {
    unit: "days",
    direction: "lower_is_better",
    decimal_places: 1,
    target_currency: null,
    actual_currency: null,
    target: "3.0",
    actual: "2.4",
    weight: "15",
    cap: "22.5",
    pct: "1.25",
    score: "0.1875",
  }),
  metric("fee_income", "Fee and commission income", "Grow income", {
    target_type: "yearly",
    base_target: "2160000",
    target: "180000",
    actual: "172400",
    reported_actual: "14300",
    actual_currency: "USD",
    fx_rate: "12.055944",
    weight: "15",
    cap: "22.5",
    pct: "0.957778",
    score: "0.143667",
    target_scope: "subject",
    overrides: [
      {
        override_id: "9e8d7c6b-5a49-4382-9716-05f4e3d2c1b0",
        change_type: "target",
        scope_type: "subject",
        scope_code: SUBJECT,
        value: "180000",
        text: null,
        reason: "Maternity phase-back, June to August.",
      },
    ],
  }),
  metric("digital_activation", "Digital activation rate", "Deepen relationships", {
    unit: "percent",
    target_currency: null,
    actual_currency: null,
    target: "70",
    actual: null,
    weight: "15",
    cap: "22.5",
    pct: null,
    score: null,
  }),
];

export const cardClosed: Card = {
  subject_id: SUBJECT,
  staff_no: "RM-0142",
  full_name: "Ama Boateng",
  period_key: "202609",
  phase: "locked",
  period_status: "closed",
  source: "snapshot",
  snapshot_version: 1,
  restated_at: null,
  restatement_reason: null,
  assigned: true,
  assignment_id: "7a6b5c4d-3e2f-4a1b-8c9d-0e1f2a3b4c5d",
  profile_code: "sme_rm",
  policy: "reduced_denominator",
  months_elapsed: 9,
  quarters_elapsed: 3,
  cycle_months: 12,
  metrics: METRICS.map((m) => (m.state === "not_reported" ? { ...m, state: "excluded", exclusion_reason: "Digital platform outage for the whole month." } : m)),
  total_score: "0.689261",
  total_cap: "120",
  weight_scored: "65",
  weight_expected: "65",
  graded_score: "1.060402",
  achievement_pct: "0.574384",
  metrics_scored: 4,
  metrics_total: 5,
  not_reported: 0,
  no_target: 0,
  excluded: 1,
  band: BANDS[2],
  next_band: null,
  bands: BANDS,
  statement: "4 of 5 metrics scored · 1 excluded",
};

export const cardLive: Card = {
  ...cardClosed,
  period_key: "202610",
  phase: "open",
  period_status: "open",
  source: "live",
  snapshot_version: null,
  metrics: METRICS,
  weight_expected: "80",
  graded_score: "1.060402",
  excluded: 0,
  not_reported: 1,
  next_band: BANDS[3],
  statement: "4 of 5 metrics scored · 1 awaiting data",
};

export const cardRestated: Card = {
  ...cardClosed,
  snapshot_version: 2,
  restated_at: "2026-10-18T09:30:00Z",
  restatement_reason: "Corrected CASA balances after the core-banking reversal.",
};

export const cardNotGraded: Card = {
  ...cardLive,
  metrics: METRICS.map((m) => ({ ...m, state: "not_reported", actual_value: null, reported_actual: null, run_id: null, pct_achieved: null, score: null })),
  total_score: "0",
  weight_scored: "0",
  graded_score: null,
  achievement_pct: null,
  metrics_scored: 0,
  not_reported: 5,
  band: null,
  next_band: null,
  statement: "0 of 5 metrics scored · 5 awaiting data",
};

export const cardUnassigned: Card = {
  ...cardLive,
  assigned: false,
  assignment_id: null,
  profile_code: null,
  metrics: [],
  band: null,
  statement: "No role in force for this period, so there is no scorecard.",
};

export const cardFuture: Card = { ...cardUnassigned, period_key: "202612", phase: "future", assigned: true, profile_code: "sme_rm" };
export const cardNoMetrics: Card = { ...cardLive, metrics: [], metrics_total: 0, metrics_scored: 0, band: null, statement: "0 of 0 metrics scored" };

function interaction(bits: Partial<Interaction>): Interaction {
  return {
    interaction_id: "3c4d5e6f-7081-4a2b-b4c5-d6e7f8091a2b",
    interaction_type: "query",
    subject_id: SUBJECT,
    staff_no: "RM-0142",
    full_name: "Ama Boateng",
    period_key: "202609",
    metric_code: "ntb_accounts",
    metric_name: "New-to-bank accounts",
    body: "Three accounts opened on the 30th are missing.",
    author_user_id: 7,
    author_name: "Ama Boateng",
    visibility: "subject",
    snapshot_version: null,
    routed_to_id: MANAGER,
    routed_to_name: "Kwame Wellington",
    created_at: "2026-10-03T10:12:00Z",
    resolved_at: null,
    resolved_by_name: null,
    outcome: null,
    resolution: null,
    resulting_override_id: null,
    mine: true,
    ...bits,
  };
}

export const openQuery = interaction({});
export const answeredQuery = interaction({
  interaction_id: "4d5e6f70-8192-4a3b-8c5d-e6f708192a3b",
  metric_code: "casa_growth",
  metric_name: "CASA balance growth",
  body: "The balance looks lower than my statement.",
  resolved_at: "2026-10-04T08:00:00Z",
  resolved_by_name: "Kwame Wellington",
  outcome: "explained",
  resolution: "The figure is the month-end balance after the reversal on the 30th; it stands.",
});
const comment = interaction({
  interaction_id: "5e6f7081-92a3-4b4c-9d6e-f708192a3b4c",
  interaction_type: "manager_comment",
  metric_code: null,
  metric_name: null,
  body: "Strong month on CASA. Keep the TAT discipline going into Q4.",
  author_name: "Kwame Wellington",
  routed_to_id: null,
  routed_to_name: null,
  mine: false,
});

export const threadToAcknowledge: Thread = {
  period_key: "202609",
  is_self: true,
  snapshot_version: 1,
  acknowledged: false,
  acknowledged_at: null,
  acknowledged_version: null,
  can_acknowledge: true,
  can_query: true,
  can_comment: false,
  queries: [openQuery, answeredQuery],
  comments: [comment],
  note: null,
};

export const threadAcknowledged: Thread = { ...threadToAcknowledge, acknowledged: true, acknowledged_at: "2026-10-02T07:45:00Z", can_acknowledge: false };
export const threadRestated: Thread = { ...threadToAcknowledge, snapshot_version: 2, acknowledged_version: 1 };
export const threadOpenPeriod: Thread = {
  ...threadToAcknowledge,
  period_key: "202610",
  snapshot_version: null,
  can_acknowledge: false,
  queries: [],
  comments: [],
  note: "You can acknowledge this scorecard once the period is closed and published.",
};
export const threadManager: Thread = {
  ...threadToAcknowledge,
  is_self: false,
  can_acknowledge: false,
  can_query: false,
  can_comment: true,
  queries: [{ ...openQuery, mine: false }],
  comments: [comment, { ...comment, interaction_id: "6f708192-a3b4-4c5d-8e6f-0819203a4b5c", body: "Watch the activation trend; raise it at the Q4 review.", visibility: "managers" }],
};

export const history: Output<"scorecard.history"> = {
  subject_id: SUBJECT,
  points: [
    ["202606", "0.812", "Gaining Momentum", 2],
    ["202607", "0.934", "Gaining Momentum", 2],
    ["202608", "1.018", "On Target", 3],
    ["202609", "1.060402", "On Target", 3],
  ].map(([period_key, graded_score, band_label, band_ramp_position]) => ({
    period_key: period_key as string,
    source: "snapshot",
    snapshot_version: 1,
    total_score: graded_score as string,
    graded_score: graded_score as string,
    achievement_pct: graded_score as string,
    metrics_scored: 5,
    metrics_total: 5,
    band_label: band_label as string,
    band_ramp_position: band_ramp_position as number,
  })),
  missing: ["202604", "202605"],
};

export const historyLive: Output<"scorecard.history"> = {
  ...history,
  points: [
    ...history.points,
    { ...history.points[3], period_key: "202610", source: "live", snapshot_version: null, metrics_scored: 4 },
  ],
};

export const team: Output<"scorecard.period.list"> = {
  period_key: "202609",
  phase: "locked",
  period_status: "closed",
  source: "snapshot",
  snapshot_version: 1,
  restated_at: null,
  total: 2,
  rows: [SUBJECT, REPORT].map((subject_id, i) => ({
    subject_id,
    staff_no: i ? "RM-0188" : "RM-0142",
    full_name: i ? "Yaw Mensah" : "Ama Boateng",
    profile_code: "sme_rm",
    total_score: "0.689261",
    graded_score: "1.060402",
    achievement_pct: "0.574384",
    metrics_scored: 4,
    metrics_total: 5,
    not_reported: 0,
    no_target: 0,
    excluded: 1,
    band: BANDS[2],
  })),
};

export const queue: Output<"scorecard.query.list"> = { mine: [], queue: [{ ...openQuery, mine: false }], routed_to_me: 1 };
export const emptyQueue: Output<"scorecard.query.list"> = { mine: [], queue: [], routed_to_me: 0 };
export const overridesForQuery: Output<"override.list"> = { pending: 0, truncated: false, overrides: [] };
