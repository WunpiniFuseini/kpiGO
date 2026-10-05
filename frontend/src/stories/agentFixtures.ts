/** Story data for Agent Performance: a Sales leaderboard as of 17 June, working day 12 of 21. */
import type { Output } from "../api/actions";
import type { Leaderboard, AgentPace } from "../pages/agents/AgentPerformance";
import type { Me } from "../session/Session";
import { staffMe } from "./fixtures";

export const agentMe: Me = {
  ...staffMe,
  permissions: ["auth.session", "agent.view"],
  pages: [{ page_key: "agent_performance", label: "Agent Performance", group: "modules", access: "view" }] as Me["pages"],
  home: "agent_performance",
};

const window = { kind: "month", start: "2026-06-01", end: "2026-06-30", as_of: "2026-06-17", working_day: 12, working_days: 21 } as const;

const valueBooked = { key: "value_booked", display_name: "Value booked", unit: "currency", decimal_places: 0, direction: "higher_is_better" };
const accounts = { key: "accounts_opened", display_name: "Accounts opened", unit: "count", decimal_places: 0, direction: "higher_is_better" };
const overall = { key: "composite", display_name: "Overall pace", unit: null, decimal_places: 2, direction: "higher_is_better" };

function agent(id: string, name: string, branch: string, region: string) {
  return { subject_id: id, staff_no: id.slice(-3), full_name: name, role_code: "rm", profile_code: "sme_rm", branch_code: branch, region_code: region };
}

const band = (ramp_position: number) => ({ label: ["", "Needs Focus", "Gaining Momentum", "On Target", "Exemplary"][ramp_position], ramp_position });

export const leaderboard: Leaderboard = {
  product: "agent_sales",
  window,
  cohort_type: "region",
  cohort: { code: "GA", name: "Greater Accra", agents: 5 },
  cohorts: [
    { code: "AS", name: "Ashanti", agents: 3 },
    { code: "GA", name: "Greater Accra", agents: 5 },
  ],
  rank_by: valueBooked,
  tiebreak: accounts,
  rank_options: [accounts, valueBooked, overall],
  summary: {
    agents: 5,
    on_pace: 2,
    value_to_date: "5035.0000",
    target_to_date: "4800.0000",
    pace: "1.0490",
    short_of_pace: "0.0000",
    day_value: "940.0000",
    currency_code: "GHS",
  },
  rows: [
    { rank: 1, agent: agent("00000000-0000-0000-0000-000000000101", "Abena Ofori", "Accra Central", "GA"), is_you: false, state: "paced", value: "1650.0000", pace: "1.3750", band: band(4), tiebreak_value: "19.0000" },
    { rank: 2, agent: agent("00000000-0000-0000-0000-000000000102", "Kwesi Mensah", "Osu", "GA"), is_you: false, state: "paced", value: "1320.0000", pace: "1.1000", band: band(3), tiebreak_value: "15.0000" },
    { rank: 3, agent: agent("00000000-0000-0000-0000-000000000103", "Kofi Boateng", "Accra Central", "GA"), is_you: true, state: "paced", value: "1320.0000", pace: "1.1000", band: band(3), tiebreak_value: "12.0000" },
    { rank: 4, agent: agent("00000000-0000-0000-0000-000000000104", "Esi Quaye", "Tema", "GA"), is_you: false, state: "paced", value: "745.0000", pace: "0.6208", band: band(1), tiebreak_value: null },
    { rank: null, agent: agent("00000000-0000-0000-0000-000000000105", "Yaw Darko", "Osu", "GA"), is_you: false, state: "not_reported", value: null, pace: null, band: null, tiebreak_value: null },
  ],
  total: 5,
};

/** Ranked by the composite: no additive total, so only the on-pace card. */
export const leaderboardComposite: Leaderboard = {
  ...leaderboard,
  rank_by: overall,
  tiebreak: null,
  summary: { ...leaderboard.summary, value_to_date: null, target_to_date: null, pace: null, short_of_pace: null, day_value: null, currency_code: null },
  rows: leaderboard.rows.map((r, i) => ({ ...r, rank: i < 4 ? i + 1 : null, value: r.pace, tiebreak_value: null })),
};

/** Nobody in the module: no metric is bound to a profile. */
export const leaderboardUnconfigured: Leaderboard = {
  ...leaderboard,
  cohort_type: "profile",
  cohort: null,
  cohorts: [],
  rank_by: overall,
  tiebreak: null,
  rank_options: [],
  summary: { agents: 0, on_pace: 0, value_to_date: null, target_to_date: null, pace: null, short_of_pace: null, day_value: null, currency_code: null },
  rows: [],
  total: 0,
};

/** Custom cohorts chosen, but none has members on the day. */
export const leaderboardNoCohorts: Leaderboard = {
  ...leaderboardUnconfigured,
  cohort_type: "cohort",
  rank_by: valueBooked,
  rank_options: [accounts, valueBooked, overall],
};

export const agentPace: AgentPace = {
  product: "agent_sales",
  window,
  agent: agent("00000000-0000-0000-0000-000000000103", "Kofi Boateng", "Accra Central", "GA"),
  metrics: [
    metric("value_booked", "Value booked", "currency", "1320.0000", "1200.0000", "1.1000", "GHS"),
    metric("accounts_opened", "Accounts opened", "count", "12.0000", "12.0000", "1.0000", null),
    { ...metric("service_tat", "Service TAT", "hours", null, "4.0000", null, null), state: "not_reported" },
  ],
};

function metric(code: string, name: string, unit: string, actual: string | null, toDate: string, pace: string | null, currency: string | null): AgentPace["metrics"][number] {
  return {
    metric_id: `00000000-0000-0000-0000-0000000002${code.length.toString().padStart(2, "0")}`,
    metric_code: code,
    display_name: name,
    direction: "higher_is_better",
    aggregation: "sum",
    unit,
    decimal_places: 0,
    currency_code: currency,
    state: "paced",
    actual,
    target_to_date: toDate,
    window_target: null,
    pace,
    short_of_pace: null,
    projected: null,
    days_reported: 9,
    target_id: null,
    target_version: null,
    target_type: null,
    band: pace ? band(3) : null,
    rag: pace ? "green" : null,
  };
}

// ── product lines ───────────────────────────────────────────────────────────

type Registry = Output<"product_line.registry">;
type RegistryLine = Registry["in_matrix"][number];

function line(code: string, name: string, group: string | null, status: string, extra: Partial<RegistryLine> = {}): RegistryLine {
  return {
    line_id: `00000000-0000-0000-0000-0000000003${String(code.length).padStart(2, "0")}`,
    code,
    display_name: name,
    sort_order: 10,
    module: "agent_performance",
    status,
    group_code: group,
    first_detected_at: "2026-05-02T06:10:00Z",
    effective_from: "2026-05-01",
    effective_to: null,
    rag_green: null,
    rag_amber: null,
    ...extra,
  };
}

export const productLineAdminMe: Me = {
  ...agentMe,
  user: { ...agentMe.user, display_name: "Ama Mensah", roles: ["admin"] },
  permissions: ["auth.session", "agent.view", "dimension.view", "product_line.manage"],
  pages: [
    { page_key: "agent_performance", label: "Agent Performance", group: "modules", access: "view" },
    { page_key: "admin.product_lines", label: "Product lines", group: "administer", access: "edit" },
  ] as Me["pages"],
};

export const registry: Registry = {
  as_of: "2026-10-05",
  groups: [
    { code: "lending", display_name: "Lending", sort_order: 10, module: "agent_performance", status: "active", line_codes: ["MORTGAGE", "LOANS"] },
    { code: "cards", display_name: "Cards and payments", sort_order: 20, module: "agent_performance", status: "active", line_codes: ["CARDS"] },
  ],
  in_matrix: [
    line("MORTGAGE", "Mortgages", "lending", "active"),
    line("LOANS", "Personal loans", "lending", "active", { rag_green: "1.100", rag_amber: "0.900" }),
    line("CARDS", "Cards", "cards", "retired", { effective_to: "2026-11-01" }),
  ],
  available: [line("BANCA", "BANCA", null, "available", { first_detected_at: "2026-10-03T05:40:00Z", effective_from: "2026-10-02" })],
  retired: [line("OVERDRAFT", "Overdrafts", "lending", "retired", { effective_to: "2026-04-01" })],
  suggest_grouped: false,
};

/** Nothing switched on yet: what the feed carries is waiting. */
export const registryFresh: Registry = {
  ...registry,
  groups: [],
  in_matrix: [],
  retired: [],
  available: [
    line("CARDS", "CARDS", null, "available"),
    line("LOANS", "LOANS", null, "available", { first_detected_at: "2026-05-03T06:10:00Z" }),
  ],
};

/** The feed has carried no product line codes at all. */
export const registryEmpty: Registry = { ...registryFresh, available: [] };

// ── product-line matrix ─────────────────────────────────────────────────────

type ProductMatrix = Output<"agent.matrix">;
type MatrixCell = ProductMatrix["rows"][number]["cells"][number];

function cell(actual: string | null, target: string | null, achieved: string | null, rag: MatrixCell["rag"], agents: number, reported = agents): MatrixCell {
  return { actual, target, achieved, rag, agents, reported, currency_code: actual === null ? null : "GHS", mixed_currency: false };
}
const absent = (agents: number) => cell(null, null, null, null, agents, 0);

function mrow(key: string, name: string, level: ProductMatrix["rows"][number]["level"], agents: number, cells: MatrixCell[], region: string | null = null, branch: string | null = null): ProductMatrix["rows"][number] {
  const drill_level = level === "region" ? "branch" : level === "branch" ? "rm" : null;
  return { key, name, level, drill_level, region_code: region, branch_code: branch, agents, cells };
}

const lineColumns: ProductMatrix["columns"] = [
  { key: "MORTGAGE", kind: "line", name: "Mortgages", group_code: "lending" },
  { key: "LOANS", kind: "line", name: "Personal loans", group_code: "lending" },
  { key: "CARDS", kind: "line", name: "Cards", group_code: "cards" },
  { key: "", kind: "all", name: "All products", group_code: null },
];

/** Regions on value booked, month to date: one line nobody in Ashanti sold. */
export const matrix: ProductMatrix = {
  product: "agent_sales",
  window,
  metric: valueBooked,
  metric_options: [accounts, valueBooked],
  level: "region",
  view: "expanded",
  breadcrumb: [{ level: "all", code: null, name: "All regions" }],
  columns: lineColumns,
  rows: [
    mrow("AS", "Ashanti", "region", 3, [cell("42000", "36000", "1.1667", "green", 3), cell("8000", "12000", "0.6667", "red", 2, 2), absent(3), cell("51000", "54000", "0.9444", "amber", 3)], "AS"),
    mrow("GA", "Greater Accra", "region", 5, [cell("61000", "60000", "1.0167", "green", 5), cell("19000", "20000", "0.9500", "amber", 5, 4), cell("3200", "4000", "0.8000", "red", 5, 2), cell("85000", "90000", "0.9444", "amber", 5)], "GA"),
  ],
  total: mrow("total", "Total", "total", 8, [cell("103000", "96000", "1.0729", "green", 8), cell("27000", "32000", "0.8438", "red", 8, 6), cell("3200", "4000", "0.8000", "red", 8, 2), cell("136000", "144000", "0.9444", "amber", 8)]),
  no_lines: false,
};

/** The same regions with lines summed into their groups. */
export const matrixGrouped: ProductMatrix = {
  ...matrix,
  view: "grouped",
  columns: [
    { key: "lending", kind: "group", name: "Lending", group_code: "lending" },
    { key: "cards", kind: "group", name: "Cards and payments", group_code: "cards" },
    { key: "", kind: "all", name: "All products", group_code: null },
  ],
  rows: matrix.rows.map((r) => ({ ...r, cells: [r.key === "AS" ? cell("50000", "48000", "1.0417", "green", 3) : cell("80000", "80000", "1.0000", "green", 5), r.cells[2], r.cells[3]] })),
  total: { ...matrix.total!, cells: [cell("130000", "128000", "1.0156", "green", 8), matrix.total!.cells[2], matrix.total!.cells[3]] },
};

/** Drilled to one branch's RMs. */
export const matrixRms: ProductMatrix = {
  ...matrix,
  level: "rm",
  breadcrumb: [
    { level: "all", code: null, name: "All regions" },
    { level: "region", code: "GA", name: "Greater Accra" },
    { level: "branch", code: "GA-01", name: "Accra Central" },
  ],
  rows: [
    mrow("s1", "Abena Owusu", "rm", 1, [cell("14000", "12000", "1.1667", "green", 1), cell("5000", "4000", "1.2500", "green", 1), absent(1), cell("19000", "18000", "1.0556", "green", 1)], "GA", "GA-01"),
    mrow("s2", "Kofi Boateng", "rm", 1, [cell("9000", "12000", "0.7500", "red", 1), absent(1), cell("1200", "800", "1.5000", "green", 1), cell("10200", "18000", "0.5667", "red", 1)], "GA", "GA-01"),
  ],
  total: mrow("total", "Total", "total", 2, [cell("23000", "24000", "0.9583", "amber", 2), cell("5000", "4000", "1.2500", "green", 2, 1), cell("1200", "800", "1.5000", "green", 2, 1), cell("29200", "36000", "0.8111", "red", 2)]),
};

/** No line switched on yet: only All products. */
export const matrixNoLines: ProductMatrix = {
  ...matrix,
  columns: [lineColumns[3]],
  rows: matrix.rows.map((r) => ({ ...r, cells: [r.cells[3]] })),
  total: { ...matrix.total!, cells: [matrix.total!.cells[3]] },
  no_lines: true,
};

/** No sum or count metric in the module. */
export const matrixNoMetric: ProductMatrix = { ...matrix, metric: null, metric_options: [], rows: [], total: null };
