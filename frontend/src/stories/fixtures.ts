/** Story data. Typed by the generated API types, so a backend change breaks the stories at build time. */
import type { Output } from "../api/actions";
import type { Me } from "../session/Session";

const PAGES: Me["pages"] = [
  { page_key: "scorecards", label: "Scorecards", group: "modules", access: "view" },
  { page_key: "agent_performance", label: "Agent Performance", group: "modules", access: "view" },
  { page_key: "campaign", label: "Campaign Manager", group: "modules", access: "view" },
  { page_key: "executive", label: "Executive", group: "modules", access: "view" },
  { page_key: "admin.users", label: "Users & access", group: "administer", access: "edit" },
  { page_key: "admin.metrics", label: "Metric registry", group: "administer", access: "edit" },
  { page_key: "admin.targets", label: "Targets", group: "administer", access: "edit" },
  { page_key: "admin.scorecard_setup", label: "Scorecard setup", group: "administer", access: "edit" },
  { page_key: "admin.calendar", label: "Business calendar", group: "administer", access: "edit" },
  { page_key: "admin.data_integration", label: "Data integration", group: "administer", access: "edit" },
  { page_key: "admin.integrations", label: "Integrations", group: "administer", access: "edit" },
  { page_key: "admin.approvals", label: "Approvals", group: "administer", access: "edit" },
  { page_key: "admin.health", label: "Health", group: "administer", access: "edit" },
  { page_key: "admin.audit", label: "Audit log", group: "administer", access: "edit" },
];

const base = {
  licence: { state: "active", mode: "full", message: "Licensed until 30 Jun 2027.", days_left: null },
  session: { idle_timeout_seconds: 1800, max_age_seconds: 43200 },
} satisfies Pick<Me, "licence" | "session">;

export const adminMe: Me = {
  ...base,
  user: {
    user_id: "6f1c0d6e-1a4f-4b4e-9b1a-0c2d3e4f5a60",
    email: "ama.mensah@bank.example",
    display_name: "Ama Mensah",
    auth_provider: "local",
    status: "active",
    subject_id: "8a7b6c5d-4e3f-4a1b-9c8d-7e6f5a4b3c2d",
    roles: ["admin"],
    last_login_at: "2026-10-04T08:12:00Z",
  },
  permissions: ["auth.session", "user.view", "user.manage", "metric.view", "metric.manage", "system.health.view", "apitoken.view", "apitoken.manage"],
  pages: PAGES,
  home: "scorecards",
  no_access: [],
};

export const staffMe: Me = {
  ...adminMe,
  user: { ...adminMe.user, display_name: "Kofi Boateng", email: "kofi.boateng@bank.example", roles: ["staff"] },
  permissions: ["auth.session", "scorecard.view"],
  pages: [PAGES[0]],
  home: "scorecards",
};

export const staffUnlinkedMe: Me = {
  ...staffMe,
  user: { ...staffMe.user, subject_id: null },
  no_access: [
    {
      page_key: "scorecards",
      missing: "Your account is not linked to a person in the hierarchy.",
      ask: "an Admin, under Administer → Users & access",
    },
  ],
};

export const executiveNoGrantMe: Me = {
  ...adminMe,
  user: { ...adminMe.user, display_name: "Efua Owusu", roles: ["executive"] },
  pages: PAGES.filter((p) => p.group === "modules" && p.page_key !== "campaign"),
  home: "executive",
  no_access: [{ page_key: "executive", missing: "You have no Executive data-scope grant.", ask: "an Admin, under Administer → Users & access" }],
};

export const noPagesMe: Me = { ...staffMe, user: { ...staffMe.user, roles: [] }, pages: [], home: null };

export const graceMe: Me = {
  ...adminMe,
  licence: { state: "grace", mode: "full", message: "The licence expired on 1 Oct 2026. Renew it to keep full use.", days_left: 27 },
};

export const readOnlyMe: Me = {
  ...adminMe,
  licence: { state: "read_only", mode: "read_only", message: "The licence grace period has ended: kpiGo is read-only until it is renewed.", days_left: 12 },
};

export const providersPassword: Output<"auth.providers"> = {
  password: { enabled: true, label: "Email and password" },
  oidc: { enabled: false, label: "Sign in with your organisation" },
  saml: { enabled: false, label: "Sign in with SAML" },
};

export const providersSso: Output<"auth.providers"> = {
  ...providersPassword,
  oidc: { enabled: true, label: "Sign in with Microsoft Entra ID" },
};

export const providersNone: Output<"auth.providers"> = {
  password: { enabled: false, label: "Email and password" },
  oidc: { enabled: false, label: "Sign in with your organisation" },
  saml: { enabled: false, label: "Sign in with SAML" },
};

export const setupDone: Output<"setup.status"> = {
  needs_admin: false,
  setup_token_configured: true,
  licence_state: "active",
  licence_message: "Licensed.",
  install_fingerprint: "9f2c41d7e0b8a6c35d1e4f7a2b9c8d0e6f1a3b5c7d9e2f4a6b8c0d1e3f5a7b9",
  product_version: "0.1.0",
};

export const setupNeeded: Output<"setup.status"> = { ...setupDone, needs_admin: true, licence_state: "unlicensed" };

export const healthOk: Output<"system.health"> = {
  status: "ok",
  checked_at: "2026-10-04T09:30:00Z",
  maintenance_mode: false,
  services: [
    { name: "database", status: "ok", detail: "Connected; schema current." },
    { name: "queue", status: "ok", detail: "0 jobs waiting." },
    { name: "workers", status: "ok", detail: "2 answering." },
  ],
  database: {
    size_bytes: 1_843_200_000,
    pending_migrations: 0,
    largest_tables: [
      { table: "fact_actual_daily_202610", bytes: 612_000_000, rows_estimate: 4_210_000 },
      { table: "fact_actual_monthly", bytes: 214_000_000, rows_estimate: 1_120_000 },
      { table: "audit_log", bytes: 96_000_000, rows_estimate: 402_000 },
    ],
  },
  queue_depth: 0,
  feeds: { total: 6, fresh: 5, stale: 1, never_loaded: 0, quarantined: [], failed: [] },
  licence: { state: "active", mode: "full", message: "Licensed until 30 Jun 2027.", restart_required: false },
  backup: {
    last_at: "2026-10-04T02:00:00Z",
    last_outcome: "ok",
    last_ok_at: "2026-10-04T02:00:00Z",
  },
  update: null,
  version: { product_version: "0.1.0", migrations: {} },
};

export const healthDegraded: Output<"system.health"> = {
  ...healthOk,
  status: "degraded",
  maintenance_mode: true,
  services: [
    { name: "database", status: "ok", detail: "Connected; schema current." },
    { name: "queue", status: "degraded", detail: "418 jobs waiting." },
    { name: "workers", status: "down", detail: "No worker answered within 2 seconds." },
  ],
  queue_depth: 418,
  feeds: { total: 6, fresh: 3, stale: 1, never_loaded: 0, quarantined: ["finance_deposits_monthly"], failed: ["cards_daily"] },
  licence: { state: "grace", mode: "full", message: "The licence expired on 1 Oct 2026.", restart_required: true },
};

export const healthEmpty: Output<"system.health"> = {
  ...healthOk,
  feeds: { total: 0, fresh: 0, stale: 0, never_loaded: 0, quarantined: [], failed: [] },
};

export const users: Output<"user.list"> = {
  users: [
    { ...adminMe.user },
    {
      user_id: "1d2e3f40-5a6b-4c7d-8e9f-0a1b2c3d4e5f",
      email: "kofi.boateng@bank.example",
      display_name: "Kofi Boateng",
      auth_provider: "oidc",
      status: "active",
      subject_id: null,
      roles: ["line_manager", "staff"],
      last_login_at: "2026-10-03T16:40:00Z",
    },
    {
      user_id: "2e3f4051-6b7c-4d8e-9f0a-1b2c3d4e5f60",
      email: "akosua.asante@bank.example",
      display_name: "Akosua Asante",
      auth_provider: "local",
      status: "invited",
      subject_id: null,
      roles: ["contributor"],
      last_login_at: null,
    },
    {
      user_id: "3f405162-7c8d-4e9f-8a1b-2c3d4e5f6071",
      email: "yaw.darko@bank.example",
      display_name: "Yaw Darko",
      auth_provider: "ldap",
      status: "disabled",
      subject_id: null,
      roles: [],
      last_login_at: "2026-08-19T10:02:00Z",
    },
  ],
};

export const roles: Output<"role.list"> = {
  page_keys: [],
  assignable_permissions: [],
  roles: [
    ["admin", "Admin"],
    ["line_manager", "Line Manager"],
    ["metric_owner", "Metric Owner"],
    ["data_steward", "Data Steward"],
    ["contributor", "Contributor"],
    ["staff", "Relationship Manager / Service Officer"],
  ].map(([code, name]) => ({ code, name, description: "", is_system: true, cloned_from: null, permissions: [], pages: {}, users: 0 })),
};

const metric = (code: string, name: string, extra: Partial<Output<"metric.list">["metrics"][number]> = {}) => ({
  metric_id: `00000000-0000-4000-8000-${code.padEnd(12, "0").slice(0, 12).replace(/[^0-9a-f]/g, "a")}`,
  family_id: "00000000-0000-4000-8000-000000000001",
  metric_code: code,
  display_name: name,
  direction: "higher_is_better",
  aggregation: "sum",
  unit: "currency",
  decimal_places: 0,
  is_percentage: false,
  target_scope: "profile",
  collection_method: "feed",
  status: "active",
  computation_note: "",
  effective_from: "2026-10-01",
  effective_to: null,
  supersedes_id: null,
  bindings: [{ product: "scorecards", is_active: true }],
  profiles: [],
  ...extra,
});

export const metrics: Output<"metric.list"> = {
  as_of: "2026-10-04",
  metrics: [
    metric("deposits_growth", "Deposits growth", { bindings: [{ product: "scorecards", is_active: true }, { product: "executive", is_active: true }] }),
    metric("loan_disbursement", "Loan disbursement"),
    metric("nps", "Net promoter score", { unit: "score", aggregation: "average", collection_method: "manual_input" }),
    metric("complaint_tat", "Complaint turnaround", { unit: "hours", aggregation: "average", direction: "lower_is_better", status: "draft" }),
  ],
};

export const forbidden = { status: 403, error: "permission_denied", message: "Your roles do not include user.view." };
export const serverError = { status: 500, error: "http_500", message: "Server error." };

export const mcpOn: Output<"mcp.status"> = {
  enabled: true,
  endpoint_path: "/api/mcp",
  endpoint_url: "https://kpigo.bank.example/api/mcp",
  protocol_versions: ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"],
  tools: [
    { name: "metric_list", action: "metric.list", summary: "List metric definitions." },
    { name: "platform_hello", action: "platform.hello", summary: "Say hello. Proves an action is reachable on every surface." },
    { name: "scorecard_compute", action: "scorecard.compute", summary: "Compute a subject's scorecard for a period." },
  ],
  message: "Point the assistant at this address with an API token issued here. It sees exactly what you see in kpiGo, through read-only tools, and every call is audited.",
};

export const mcpOff: Output<"mcp.status"> = {
  ...mcpOn,
  enabled: false,
  endpoint_url: null,
  message: "The MCP endpoint is off for this install (KPIGO_MCP_ENABLED=0). Your infrastructure team turns it on in the install's settings.",
};

export const apiTokens: Output<"apitoken.list"> = {
  tokens: [
    { name: "claude-desktop", prefix: "kpigo_Xa3f9Q", status: "active", expires_at: "2027-01-06T09:00:00Z", last_used_at: "2026-10-07T16:42:00Z", created_at: "2026-10-08T09:00:00Z" },
    { name: "reporting-etl", prefix: "kpigo_b81Kpz", status: "revoked", expires_at: null, last_used_at: null, created_at: "2026-09-01T09:00:00Z" },
  ],
};

export const noApiTokens: Output<"apitoken.list"> = { tokens: [] };

export const tokenIssued: Output<"apitoken.issue"> = {
  token: { name: "claude-desktop", prefix: "kpigo_Xa3f9Q", status: "active", expires_at: "2027-01-06T09:00:00Z", last_used_at: null, created_at: "2026-10-08T09:00:00Z" },
  secret: "kpigo_Xa3f9Q-example-token-not-real",
  message: "Store this token now; it is not shown again. Send it as 'Authorization: Bearer <token>'. It reaches read-only actions only and carries your own permissions and visibility.",
};

// --- Approvals (R7) ----------------------------------------------------------

type ApprovalRequest = Output<"platform.approval.list">["requests"][number];

const baseRequest: ApprovalRequest = {
  approval_request_id: "a0000000-0000-0000-0000-000000000001",
  action_name: "metric.register",
  action_summary: "Register a new metric.",
  caller: "agent",
  payload: { metric_code: "TAT_CARDS", name: "Turnaround, cards", direction: "lower_is_better", unit: "days" },
  status: "pending",
  requested_by_id: 42,
  requested_by_name: "Ama Mensah",
  requested_at: "2026-10-08T14:20:00Z",
  decided_by_name: "",
  decided_at: null,
  rejection_reason: "",
  result: null,
  mine: false,
  can_decide: true,
  can_confirm: false,
  can_withdraw: false,
};

// A decider's org queue: someone else's assistant proposal, and a person's own request.
export const approvalQueue: Output<"platform.approval.list"> = {
  scope: "org",
  requests: [
    baseRequest,
    {
      ...baseRequest,
      approval_request_id: "a0000000-0000-0000-0000-000000000002",
      action_name: "target.batch.load",
      action_summary: "Load a batch of targets.",
      caller: "http",
      payload: { period_key: "202611", rows: 140 },
      requested_by_name: "Kwame Asante",
      requested_at: "2026-10-08T13:05:00Z",
    },
  ],
};

// A maker-checker class is on: the requester cannot confirm their own, only withdraw.
export const myProposals: Output<"platform.approval.list"> = {
  scope: "own",
  requests: [
    {
      ...baseRequest,
      approval_request_id: "a0000000-0000-0000-0000-000000000003",
      requested_by_name: "Kofi Boateng",
      mine: true,
      can_decide: false,
      can_confirm: true,
      can_withdraw: true,
    },
  ],
};

// Own queue where the class needs a second person: confirm is closed, withdraw stays.
export const myProposalsSecondPerson: Output<"platform.approval.list"> = {
  scope: "own",
  requests: [{ ...myProposals.requests[0], can_confirm: false }],
};

export const decidedApprovals: Output<"platform.approval.list"> = {
  scope: "org",
  requests: [
    {
      ...baseRequest,
      approval_request_id: "a0000000-0000-0000-0000-000000000004",
      status: "approved",
      caller: "http",
      decided_by_name: "Efua Owusu",
      decided_at: "2026-10-08T15:00:00Z",
      result: { metric_code: "TAT_CARDS", version: 1 },
      can_decide: false,
    },
    {
      ...baseRequest,
      approval_request_id: "a0000000-0000-0000-0000-000000000005",
      status: "rejected",
      decided_by_name: "Efua Owusu",
      decided_at: "2026-10-08T15:02:00Z",
      rejection_reason: "The metric already exists under a different code.",
      can_decide: false,
    },
  ],
};

export const noApprovals: Output<"platform.approval.list"> = { scope: "org", requests: [] };
export const noMyProposals: Output<"platform.approval.list"> = { scope: "own", requests: [] };

// An Admin who may decide the whole organisation's queue.
export const approverMe: Me = {
  ...adminMe,
  permissions: [...adminMe.permissions, "platform.approval.decide", "approval.request.view", "approval.request.own"],
};

// Someone who only has their own proposals (no decide).
export const proposerMe: Me = {
  ...staffMe,
  permissions: ["auth.session", "scorecard.view", "approval.request.view", "approval.request.own", "assistant.use"],
};
