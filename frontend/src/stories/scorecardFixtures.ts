/** Story data for the Scorecards configuration screens: targets, taxonomy, profiles, bands. */
import type { Output } from "../api/actions";
import type { Me } from "../session/Session";
import { adminMe } from "./fixtures";

export const configMe: Me = {
  ...adminMe,
  permissions: [
    ...adminMe.permissions,
    "scorecard.view",
    "scorecard.config.view",
    "scorecard.config.manage",
    "target.view",
    "target.manage",
    "target.publish",
    "override.view",
    "override.request",
    "override.approve",
    "period.close",
  ],
};

/** A Metric Owner's colleague who may look but not change anything. */
export const viewerMe: Me = { ...adminMe, permissions: ["auth.session", "scorecard.config.view", "target.view", "metric.view"] };

const PERIODS = Array.from({ length: 12 }, (_, i) => `2027${String(i + 1).padStart(2, "0")}`);
const METRICS = [
  { code: "casa_growth", scope: "profile" },
  { code: "ntb_accounts", scope: "profile" },
  { code: "service_tat", scope: "profile" },
  { code: "fee_income", scope: "subject" },
];

type Cell = Output<"target.coverage">["cells"][number];

function cellFor(profile: string, code: string, scope: string, period: string, i: number): Cell {
  const month = Number(period.slice(4));
  const expected = scope === "subject" ? 5 : 1;
  if (month <= 6) return { profile_code: profile, metric_code: code, period_key: period, target_scope: scope, state: "published", expected, published: expected, drafts: 0 };
  if (month <= 9 && i !== 2) return { profile_code: profile, metric_code: code, period_key: period, target_scope: scope, state: "draft", expected, published: 0, drafts: expected };
  if (scope === "subject" && month <= 11) return { profile_code: profile, metric_code: code, period_key: period, target_scope: scope, state: "partial", expected, published: 0, drafts: 3 };
  if (month === 7) return { profile_code: profile, metric_code: code, period_key: period, target_scope: scope, state: "revision", expected, published: expected, drafts: expected };
  return { profile_code: profile, metric_code: code, period_key: period, target_scope: scope, state: "missing", expected, published: 0, drafts: 0 };
}

export const coverage: Output<"target.coverage"> = {
  cycle_name: "January to December",
  period_keys: PERIODS,
  profiles: ["sme_rm", "teller"],
  cells: ["sme_rm", "teller"].flatMap((profile) =>
    METRICS.flatMap((m, i) => PERIODS.map((p) => cellFor(profile, m.code, m.scope, p, i))),
  ),
  weights: ["sme_rm", "teller"].flatMap((profile) =>
    PERIODS.map((p) => {
      const month = Number(p.slice(4));
      const off = profile === "teller" && month === 8;
      return {
        profile_code: profile,
        period_key: p,
        weight_sum: off ? "95.000" : month <= 9 ? "100.000" : "60.000",
        expected: "100.000",
        complete: month <= 9,
        ok: month <= 9 && !off,
        missing: month <= 9 ? [] : ["fee_income for E3", "service_tat"],
        subjects_off: off ? [{ staff_no: "E2", weight_sum: "95.000" }] : [],
      };
    }),
  ),
  complete: false,
  gaps: 14,
  drafts: 24,
};

export const coverageEmpty: Output<"target.coverage"> = { ...coverage, profiles: [], cells: [], weights: [], gaps: 0, drafts: 0 };

export const coverageComplete: Output<"target.coverage"> = {
  ...coverage,
  cells: coverage.cells.map((c) => ({ ...c, state: "published", published: c.expected, drafts: 0 })),
  weights: coverage.weights.map((w) => ({ ...w, weight_sum: "100.000", complete: true, ok: true, missing: [], subjects_off: [] })),
  complete: true,
  gaps: 0,
  drafts: 0,
};

export const drafts: Output<"target.list"> = {
  truncated: false,
  targets: [
    ["casa_growth", "CASA balance growth", "profile", "sme_rm", "sme_rm", "202707", "4200000", "40", "60", 1],
    ["ntb_accounts", "New-to-bank accounts", "profile", "sme_rm", "sme_rm", "202707", "45", "20", "30", 2],
    ["fee_income", "Fee and commission income", "subject", "6f1c", "E2 Ama Boateng", "202707", "180000", "25", "37", 1],
  ].map(([code, name, scope, scopeCode, label, period, value, weight, cap, version], i) => ({
    target_id: `00000000-0000-0000-0000-00000000000${i}`,
    metric_code: code as string,
    metric_name: name as string,
    scope_type: scope as string,
    scope_code: scopeCode as string,
    scope_label: label as string,
    period_key: period as string,
    series_type: "target",
    product_line_code: "",
    target_value: value as string,
    target_type: "monthly",
    weight: weight as string,
    cap: cap as string,
    currency_code: null,
    version: version as number,
    state: "draft",
    source: "upload",
    batch_id: null,
    published_at: null,
  })),
};

export const noDrafts: Output<"target.list"> = { truncated: false, targets: [] };

export const batches: Output<"target.batch.list"> = {
  batches: [
    {
      batch_id: "10000000-0000-0000-0000-000000000002",
      period_keys: ["202801", "202812"],
      published_at: "2026-10-03T09:12:00Z",
      published_by: 1,
      row_count: 96,
      note: "FY2028 draft cycle",
      status: "published",
      reverted_at: null,
      weight_check_result: {},
    },
    {
      batch_id: "10000000-0000-0000-0000-000000000001",
      period_keys: ["202601", "202606"],
      published_at: "2025-12-15T14:30:00Z",
      published_by: 1,
      row_count: 288,
      note: "",
      status: "published",
      reverted_at: null,
      weight_check_result: {},
    },
    {
      batch_id: "10000000-0000-0000-0000-000000000000",
      period_keys: ["202601"],
      published_at: "2025-12-14T11:00:00Z",
      published_by: 1,
      row_count: 48,
      note: "Wrong uplift",
      status: "reverted",
      reverted_at: "2025-12-14T11:20:00Z",
      weight_check_result: {},
    },
  ],
};

export const noBatches: Output<"target.batch.list"> = { batches: [] };

export const sheetResult: Output<"target.upload"> = {
  rows_read: 48,
  rows_valid: 45,
  errors: 2,
  warnings: 1,
  written: 0,
  accepted: false,
  findings: [
    { code: "scope_mismatch", message: "'casa_growth' takes profile-level targets, not subject-level.", severity: "error", row_no: 4, column: "scope_type", value: "subject" },
    { code: "cap_below_weight", message: "Cap 5 is below 1 × weight 15: a subject exactly on target could not earn the full weight.", severity: "error", row_no: 9, column: "cap", value: "5" },
    { code: "not_on_profile", message: "'ntb_accounts' is not on profile teller's scorecard in 202701.", severity: "warning", row_no: 12, column: "scope_code", value: "teller" },
  ],
};

const T = "20000000-0000-0000-0000-000000000000";
const node = (n: number) => `20000000-0000-0000-0000-00000000000${n}`;

export const template: Output<"scorecard.template.get"> = {
  template: {
    template_id: T,
    name: "Bank scorecard",
    level_count: 2,
    status: "active",
    levels: [
      { level_no: 1, label: "Strategic objective" },
      { level_no: 2, label: "Value driver" },
    ],
    nodes: [
      { node_id: node(1), level_no: 1, parent_id: null, label: "Grow the balance sheet", sort_order: 0 },
      { node_id: node(2), level_no: 2, parent_id: node(1), label: "Deposits", sort_order: 0 },
      { node_id: node(3), level_no: 2, parent_id: node(1), label: "New clients", sort_order: 1 },
      { node_id: node(4), level_no: 1, parent_id: null, label: "Serve reliably", sort_order: 1 },
      { node_id: node(5), level_no: 2, parent_id: node(4), label: "Speed", sort_order: 0 },
    ],
    placements: [
      { placement_id: node(6), node_id: node(2), metric_code: "casa_growth", metric_name: "CASA balance growth", profile_code: null, sort_order: 0 },
      { placement_id: node(7), node_id: node(3), metric_code: "ntb_accounts", metric_name: "New-to-bank accounts", profile_code: null, sort_order: 0 },
      { placement_id: node(8), node_id: node(5), metric_code: "service_tat", metric_name: "Service TAT", profile_code: null, sort_order: 0 },
      { placement_id: node(9), node_id: node(5), metric_code: "casa_growth", metric_name: "CASA balance growth", profile_code: "teller", sort_order: 1 },
    ],
  },
};

export const noTemplate: Output<"scorecard.template.get"> = { template: null };

export const scorecardMetrics: Output<"metric.list"> = {
  as_of: "2026-10-05",
  metrics: ["casa_growth:CASA balance growth", "ntb_accounts:New-to-bank accounts", "service_tat:Service TAT", "fee_income:Fee and commission income"].map(
    (pair, i) => {
      const [code, name] = pair.split(":");
      return {
        metric_id: `30000000-0000-0000-0000-00000000000${i}`,
        family_id: `30000000-0000-0000-0000-00000000001${i}`,
        metric_code: code,
        display_name: name,
        direction: code === "service_tat" ? "lower_is_better" : "higher_is_better",
        aggregation: "sum",
        unit: "count",
        decimal_places: 0,
        is_percentage: false,
        target_scope: code === "fee_income" ? "subject" : "profile",
        collection_method: "feed",
        status: "active",
        computation_note: "",
        effective_from: "2025-01-01",
        effective_to: null,
        supersedes_id: null,
        bindings: [{ product: "scorecards", is_active: true }],
        profiles: [],
      };
    },
  ),
};

export const profiles: Output<"scorecard.profile.list"> = {
  period_key: "202610",
  profiles: [
    {
      profile_code: "sme_rm",
      member_count: 28,
      metrics: [
        { metric_code: "casa_growth", display_name: "CASA balance growth", target_scope: "profile", unit: "currency", direction: "higher_is_better", collection_method: "feed", path: ["Grow the balance sheet", "Deposits"] },
        { metric_code: "fee_income", display_name: "Fee and commission income", target_scope: "subject", unit: "currency", direction: "higher_is_better", collection_method: "feed", path: [] },
        { metric_code: "csat", display_name: "Customer satisfaction", target_scope: "profile", unit: "score", direction: "higher_is_better", collection_method: "manual_input", path: ["Serve reliably", "Speed"] },
      ],
    },
    { profile_code: "teller", member_count: 64, metrics: [] },
  ],
};

export const noProfiles: Output<"scorecard.profile.list"> = { period_key: "202610", profiles: [] };

export const bands: Output<"band.list"> = {
  is_default: true,
  bands: [
    { band_id: null, label: "Needs Focus", threshold: "0", ramp_position: 1, colour_hex: null },
    { band_id: null, label: "Gaining Momentum", threshold: "0.75", ramp_position: 2, colour_hex: null },
    { band_id: null, label: "On Target", threshold: "1.0", ramp_position: 3, colour_hex: null },
    { band_id: null, label: "Exemplary", threshold: "1.2", ramp_position: 4, colour_hex: null },
  ],
};

export const scorecardSettings: Output<"scorecard.settings.get"> = {
  weight_total: "100.000",
  weight_tolerance: "0.500",
  cap_min_ratio: "1.000",
  cap_max_ratio: "3.000",
  denominator_policy: "reduced",
  input_due_working_day: 5,
  input_reminder_working_days: 2,
};

// ── overrides ───────────────────────────────────────────────────────────────

type OverrideRow = Output<"override.list">["overrides"][number];

const override = (o: Partial<OverrideRow> & Pick<OverrideRow, "override_id">): OverrideRow => ({
  scope_type: "subject",
  scope_code: "4f9a1c2e-0000-4000-8000-000000000001",
  scope_label: "E1042 · Ama Mensah",
  metric_code: "casa_growth",
  metric_name: "CASA balance growth",
  period_from: "202610",
  period_to: null,
  change_type: "target",
  override_value: "80.0000",
  override_text: null,
  reason: "Branch closed for refurbishment for two weeks.",
  status: "pending",
  requested_by: 7,
  requested_by_name: "Kofi Boateng",
  requested_at: "2026-10-03T09:12:00Z",
  approved_by: null,
  approved_by_name: null,
  approved_at: null,
  decision_note: "",
  ended_by: null,
  ended_at: null,
  mine: false,
  ...o,
});

export const overridesPending: Output<"override.list"> = {
  pending: 3,
  truncated: false,
  overrides: [
    override({ override_id: "a1" }),
    override({
      override_id: "a2",
      scope_type: "profile",
      scope_code: "sme_rm",
      scope_label: "sme_rm",
      metric_code: "service_tat",
      metric_name: "Service TAT",
      period_from: "202610",
      period_to: "202612",
      change_type: "weight",
      override_value: "10.0000",
      reason: "Queue system outage; TAT is not measurable this quarter.",
      mine: true,
      requested_by_name: "You",
    }),
    override({
      override_id: "a3",
      scope_type: "dimension",
      scope_code: "branch:ACC",
      scope_label: "branch:ACC",
      change_type: "target_type",
      override_value: null,
      override_text: "prorated",
      reason: "Branch opened mid-cycle.",
    }),
  ],
};

export const overridesApproved: Output<"override.list"> = {
  pending: 0,
  truncated: false,
  overrides: [
    override({
      override_id: "b1",
      status: "approved",
      approved_by: 3,
      approved_by_name: "Esi Owusu",
      approved_at: "2026-10-04T11:00:00Z",
    }),
  ],
};

export const noOverrides: Output<"override.list"> = { pending: 0, truncated: false, overrides: [] };

// ── period close ────────────────────────────────────────────────────────────

type Check = Output<"scorecard.close.check">;

export const closeBlocked: Check = {
  period_key: "202609",
  status: "open",
  snapshot_version: 0,
  ready: false,
  refused: null,
  subjects: 214,
  blockers: [
    {
      kind: "not_reported", owed_by: [],
      metric_code: "fee_income",
      profile_code: null,
      subjects: ["E1042", "E1077"],
      count: 2,
      message: "fee_income: no actual reported for 2 people. Load it, or exclude it with a reason.",
    },
    {
      kind: "no_target", owed_by: [],
      metric_code: "ntb_accounts",
      profile_code: null,
      subjects: Array.from({ length: 25 }, (_, i) => `E${2000 + i}`),
      count: 31,
      message: "ntb_accounts: no target published for 31 people. Load it, or exclude it with a reason.",
    },
    {
      kind: "weights", owed_by: [],
      metric_code: null,
      profile_code: "sme_rm",
      subjects: ["E1042"],
      count: 1,
      message: "sme_rm: weights sum to 105 for 1 person, not 100.",
    },
  ],
  warnings: [
    {
      kind: "pending_overrides", owed_by: [],
      metric_code: null,
      profile_code: null,
      subjects: [],
      count: 1,
      message: "1 override request(s) for this period are still awaiting approval; close freezes scores without them.",
    },
    { kind: "feed", owed_by: [], metric_code: null, profile_code: null, subjects: [], count: 0, message: "Feed 'monthly' was due for this period and has no successful load for it." },
  ],
};

export const closeReady: Check = { ...closeBlocked, ready: true, blockers: [], warnings: [] };
export const closeRestating: Check = { ...closeReady, status: "restating", snapshot_version: 1 };
export const closeClosed: Check = {
  ...closeReady,
  status: "closed",
  snapshot_version: 2,
  ready: false,
  refused: "202609 is closed. Restate it to change its scores.",
  subjects: 0,
};
export const closeFuture: Check = { ...closeReady, period_key: "202612", ready: false, refused: "202612 has not started yet.", subjects: 0 };

export const exclusions: Output<"scorecard.exclusion.list"> = {
  exclusions: [
    {
      exclusion_id: "x1",
      period_key: "202609",
      metric_code: "fee_income",
      metric_name: "Fee and commission income",
      subject_id: null,
      staff_no: null,
      reason: "Fee system outage for the last week of September.",
      created_at: "2026-10-03T10:00:00Z",
      created_by: 1,
    },
  ],
};
export const noExclusions: Output<"scorecard.exclusion.list"> = { exclusions: [] };

export const snapshots: Output<"scorecard.snapshot.list"> = {
  period_key: "202609",
  status: "closed",
  snapshots: [
    {
      snapshot_id: "s2",
      period_key: "202609",
      snapshot_version: 2,
      kind: "restatement",
      reason: "Corrected CASA balances for the Kumasi branches.",
      subjects: 214,
      created_at: "2026-10-04T15:30:00Z",
      created_by: 1,
    },
    { snapshot_id: "s1", period_key: "202609", snapshot_version: 1, kind: "close", reason: "", subjects: 214, created_at: "2026-10-02T09:00:00Z", created_by: 1 },
  ],
};
export const noSnapshots: Output<"scorecard.snapshot.list"> = { period_key: "202609", status: "open", snapshots: [] };
