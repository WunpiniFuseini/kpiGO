import type { Output } from "../api/actions";

type Draft = Output<"import.draft.get">;
type ApplyOut = Output<"import.draft.apply">;

function metric(
  source_row: number,
  display_name: string,
  metric_code: string,
  direction: "higher_is_better" | "lower_is_better",
  unit: string,
  inferred: string[],
): Draft["proposals"]["metrics"][number] {
  return {
    source_row,
    display_name,
    metric_code,
    direction,
    aggregation: unit === "percent" || unit === "score" ? "average" : "sum",
    unit,
    is_percentage: unit === "percent",
    decimal_places: unit === "count" ? 0 : 2,
    description: "",
    inferred,
  };
}

export const scorecardDraft: Draft = {
  import_id: "11111111-1111-1111-1111-111111111111",
  kind: "scorecard",
  status: "drafted",
  filename: "rm-scorecard.csv",
  summary: { metrics: 3, targets: 3, subjects: 0, warnings: 1 },
  proposals: {
    kind: "scorecard",
    mapping: { metric_name: "KPI", weight: "Weight", target: "Target" },
    unmapped_columns: ["Owner"],
    metrics: [
      metric(1, "Total Deposits", "total_deposits", "higher_is_better", "currency", ["aggregation", "unit"]),
      metric(2, "Cost to Income Ratio", "cost_to_income_ratio", "lower_is_better", "percent", ["aggregation", "direction", "unit"]),
      metric(3, "Customer Complaints", "customer_complaints", "lower_is_better", "count", ["aggregation", "direction", "unit"]),
    ],
    targets: [
      { source_row: 1, metric_code: "total_deposits", target_value: "5000000", weight: "40" },
      { source_row: 2, metric_code: "cost_to_income_ratio", target_value: "45", weight: "20" },
      { source_row: 3, metric_code: "customer_complaints", target_value: "12", weight: "20" },
    ],
    subjects: [],
    warnings: ["The 3 weights add up to 80, not 100. Review them."],
    message: "",
  },
  applied: null,
  created_at: "2026-10-08T15:00:00Z",
  applied_at: null,
};

export const rosterDraft: Draft = {
  import_id: "22222222-2222-2222-2222-222222222222",
  kind: "roster",
  status: "drafted",
  filename: "staff.xlsx",
  summary: { metrics: 0, targets: 0, subjects: 2, warnings: 1 },
  proposals: {
    kind: "roster",
    mapping: { staff_no: "Staff No", full_name: "Full Name", email: "Email" },
    unmapped_columns: [],
    metrics: [],
    targets: [],
    subjects: [
      { source_row: 1, staff_no: "E1001", full_name: "Ama Mensah", email: "ama.mensah@bank.example" },
      { source_row: 2, staff_no: "E1002", full_name: "Kofi Owusu", email: "" },
    ],
    warnings: ["Row 2: no email was read; add one to register this person."],
    message: "",
  },
  applied: null,
  created_at: "2026-10-08T14:00:00Z",
  applied_at: null,
};

export const unknownDraft: Draft = {
  import_id: "33333333-3333-3333-3333-333333333333",
  kind: "unknown",
  status: "drafted",
  filename: "mystery.csv",
  summary: { metrics: 0, targets: 0, subjects: 0, warnings: 0 },
  proposals: {
    kind: "unknown",
    mapping: {},
    unmapped_columns: ["colour", "shape"],
    metrics: [],
    targets: [],
    subjects: [],
    warnings: [],
    message:
      "kpiGo could not tell what this sheet holds. For a scorecard, give it a column named Metric (or KPI) and at least one of Weight, Target or Direction. Columns found: colour, shape.",
  },
  applied: null,
  created_at: "2026-10-08T13:00:00Z",
  applied_at: null,
};

export const appliedDraft: Draft = {
  ...scorecardDraft,
  import_id: "44444444-4444-4444-4444-444444444444",
  filename: "applied-scorecard.csv",
  status: "applied",
  applied: { metrics: { registered: 2, exists: 1 } },
  applied_at: "2026-10-08T16:00:00Z",
};

export const importDrafts: Draft[] = [scorecardDraft, rosterDraft, appliedDraft];

export const applyReport: ApplyOut = {
  draft: appliedDraft,
  outcomes: [
    { kind: "metric", ref: "total_deposits", outcome: "registered", detail: "" },
    { kind: "metric", ref: "cost_to_income_ratio", outcome: "exists", detail: "A metric named 'Cost to Income Ratio' already exists." },
    { kind: "metric", ref: "customer_complaints", outcome: "registered", detail: "" },
  ],
};
