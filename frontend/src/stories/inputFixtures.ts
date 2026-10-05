/** Story data for manual input: a contributor's month and the Admin's assignments. */
import type { Output } from "../api/actions";
import type { Me } from "../session/Session";
import { adminMe, staffMe } from "./fixtures";

type Tasks = Output<"input.task.list">;
type Task = Tasks["tasks"][number];
type Assignments = Output<"input.assignment.list">;
type Ladder = Output<"input.ladder.get">;
type Escalated = Output<"input.escalation.list">;

/** A line manager: enters their own inputs and is told when their team's are overdue. */
export const managerMe: Me = {
  ...staffMe,
  user: { ...staffMe.user, display_name: "Efua Owusu", roles: ["line_manager"] },
  permissions: ["auth.session", "input.submit", "input.followup"],
  pages: [{ page_key: "my_inputs", label: "My inputs", group: "modules", access: "edit" }] as Me["pages"],
  home: "my_inputs",
};

/** An executive named as a stakeholder: nothing to enter, only escalations to see. */
export const stakeholderMe: Me = {
  ...staffMe,
  user: { ...staffMe.user, display_name: "Ama Boateng", roles: ["executive"] },
  permissions: ["auth.session", "input.followup"],
  pages: [{ page_key: "my_inputs", label: "My inputs", group: "modules", access: "view" }] as Me["pages"],
  home: "my_inputs",
};

export const contributorMe: Me = {
  ...staffMe,
  user: { ...staffMe.user, display_name: "Kofi Asante", subject_id: null, roles: ["contributor"] },
  permissions: ["auth.session", "input.submit"],
  pages: [{ page_key: "my_inputs", label: "My inputs", group: "modules", access: "edit" }] as Me["pages"],
  home: "my_inputs",
};

export const inputAdminMe: Me = {
  ...adminMe,
  permissions: [...adminMe.permissions, "scorecard.config.view", "scorecard.config.manage", "input.manage", "input.submit", "user.view"],
};

function task(id: string, bits: Partial<Task>): Task {
  return {
    assignment_id: `0000000${id}-0000-4000-8000-000000000000`,
    metric_code: "csat",
    metric_name: "Customer satisfaction",
    unit: "count",
    decimal_places: 1,
    direction: "higher_is_better",
    description: "Average score out of 5 from the monthly branch survey.",
    scope_type: "dimension",
    scope_label: "Branch ACC",
    members: 14,
    state: "pending",
    value: null,
    note: "",
    submitted_at: null,
    version: null,
    reminded_at: null,
    escalation_step: 0,
    ...bits,
  };
}

export const NOW = new Date("2026-10-05T09:00:00Z");

export const tasksDue: Tasks = {
  period_key: "202609",
  due_at: "2026-10-08T00:00:00Z",
  locked: false,
  period_status: "open",
  restating: false,
  tasks: [
    task("1", { reminded_at: "2026-10-05T06:00:00Z", escalation_step: 1 }),
    task("2", { metric_code: "nps", metric_name: "Net promoter score", unit: "count", decimal_places: 0, description: "", state: "draft", value: "38.0000", note: "Dipped after the outage." }),
    task("3", {
      metric_code: "esg_score",
      metric_name: "ESG compliance",
      unit: "percent",
      description: "Share of checklist items met.",
      scope_type: "profile",
      scope_label: "Everyone on sme_rm",
      members: 31,
      state: "submitted",
      value: "71.0000",
      submitted_at: "2026-10-03T14:20:00Z",
      version: 1,
    }),
  ],
  other_periods: [],
};

export const tasksAllSubmitted: Tasks = {
  ...tasksDue,
  tasks: tasksDue.tasks.map((t) => ({ ...t, state: "submitted", value: t.value ?? "4.2000", submitted_at: "2026-10-04T10:00:00Z", version: 1 })),
};
export const tasksLocked: Tasks = { ...tasksAllSubmitted, due_at: "2026-10-03T00:00:00Z", locked: true };
export const tasksRestating: Tasks = { ...tasksLocked, period_status: "restating", restating: true };
export const tasksNone: Tasks = { ...tasksDue, tasks: [] };
export const tasksOtherMonth: Tasks = { ...tasksNone, period_key: "202610", due_at: "2026-11-07T00:00:00Z", other_periods: ["202609"] };

export const assignments: Assignments = {
  period_key: "202609",
  due_at: "2026-10-08T00:00:00Z",
  locked: false,
  assignments: [
    {
      assignment_id: "a0000001-0000-4000-8000-000000000000",
      metric_code: "csat",
      metric_name: "Customer satisfaction",
      scope_type: "dimension",
      scope_code: "branch:ACC",
      scope_label: "Branch ACC",
      assignee_type: "user",
      assignee_user_id: "6f1c0d6e-1a4f-4b4e-9b1a-0c2d3e4f5a61",
      assignee_role: null,
      contributor_name: "Kofi Asante",
      members: 14,
      effective_from: "2026-01-01",
      effective_to: null,
      state: "submitted",
      submitted_at: "2026-10-03T14:20:00Z",
      escalation_step: 0,
      stakeholder_user_ids: [],
      stakeholder_names: [],
    },
    {
      assignment_id: "a0000002-0000-4000-8000-000000000000",
      metric_code: "nps",
      metric_name: "Net promoter score",
      scope_type: "subject",
      scope_code: "1b2c3d4e-5f60-4718-8a9b-0c1d2e3f4a5b",
      scope_label: "Yaw Mensah · RM-0188",
      assignee_type: "role_relative",
      assignee_user_id: null,
      assignee_role: "line_manager_of",
      contributor_name: null,
      members: 1,
      effective_from: "2026-01-01",
      effective_to: null,
      state: "pending",
      submitted_at: null,
      escalation_step: 2,
      stakeholder_user_ids: ["6f1c0d6e-1a4f-4b4e-9b1a-0c2d3e4f5a62"],
      stakeholder_names: ["Efua Owusu"],
    },
  ],
  unassigned: [{ metric_code: "esg_score", metric_name: "ESG compliance" }],
  email_reminders: true,
};

export const noAssignments: Assignments = { ...assignments, assignments: [], unassigned: [] };

export const manualMetrics = [
  { metric_code: "csat", display_name: "Customer satisfaction" },
  { metric_code: "nps", display_name: "Net promoter score" },
  { metric_code: "esg_score", display_name: "ESG compliance" },
];

export const people = [
  { user_id: "6f1c0d6e-1a4f-4b4e-9b1a-0c2d3e4f5a61", display_name: "Kofi Asante", roles: ["contributor"] },
  { user_id: "6f1c0d6e-1a4f-4b4e-9b1a-0c2d3e4f5a62", display_name: "Efua Owusu", roles: ["line_manager"] },
];

export const ladder: Ladder = {
  contributor_working_days_before: 2,
  manager_working_days: 0,
  stakeholder_working_days: 1,
  stakeholders: [],
  default_stakeholders: [{ user_id: "6f1c0d6e-1a4f-4b4e-9b1a-0c2d3e4f5a70", display_name: "Abena Darko" }],
  period_key: "202609",
  due_on: "2026-10-07",
  contributor_on: "2026-10-05",
  manager_on: "2026-10-07",
  stakeholders_on: "2026-10-08",
  email: true,
};

export const ladderNamedNoManager: Ladder = {
  ...ladder,
  manager_working_days: null,
  manager_on: null,
  stakeholders: [{ user_id: "6f1c0d6e-1a4f-4b4e-9b1a-0c2d3e4f5a62", display_name: "Efua Owusu" }],
  default_stakeholders: [{ user_id: "6f1c0d6e-1a4f-4b4e-9b1a-0c2d3e4f5a62", display_name: "Efua Owusu" }],
  email: false,
};

export const escalated: Escalated = {
  items: [
    {
      assignment_id: "a0000001-0000-4000-8000-000000000000",
      period_key: "202609",
      metric_code: "csat",
      metric_name: "Customer satisfaction",
      scope_label: "Branch ACC",
      contributor_name: "Kofi Asante",
      step: 2,
      escalated_at: "2026-10-07T06:00:00Z",
      due_at: "2026-10-08T00:00:00Z",
      locked: false,
      state: "draft",
    },
    {
      assignment_id: "a0000002-0000-4000-8000-000000000000",
      period_key: "202609",
      metric_code: "nps",
      metric_name: "Net promoter score",
      scope_label: "Yaw Mensah · RM-0188",
      contributor_name: null,
      step: 3,
      escalated_at: "2026-10-08T06:00:00Z",
      due_at: "2026-10-08T00:00:00Z",
      locked: true,
      state: "pending",
    },
  ],
};

export const nothingEscalated: Escalated = { items: [] };
