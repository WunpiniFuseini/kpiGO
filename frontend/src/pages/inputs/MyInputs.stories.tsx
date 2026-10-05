import type { Meta, StoryObj } from "@storybook/react-vite";
import { Route, Routes } from "react-router-dom";

import { AppShell } from "../../shell/AppShell";
import { useSession, type Me } from "../../session/Session";
import { serverError } from "../../stories/fixtures";
import {
  NOW,
  assignments,
  contributorMe,
  manualMetrics,
  noAssignments,
  people,
  tasksAllSubmitted,
  tasksDue,
  tasksLocked,
  tasksNone,
  tasksOtherMonth,
  tasksRestating,
} from "../../stories/inputFixtures";
import { withApp, type Handlers } from "../../stories/mockApi";
import { ManualInputView } from "../admin/ManualInput";
import { InputsView, MyInputsPage } from "./MyInputs";

function InShell({ children }: { children: React.ReactNode }) {
  const { state } = useSession();
  if (state.status !== "signed-in") return null;
  return (
    <AppShell me={state.me} onSignOut={() => {}}>
      <Routes>
        <Route path="*" element={children} />
      </Routes>
    </AppShell>
  );
}

const page = (handlers: Handlers, me: Me = contributorMe): Story => ({
  render: () => (
    <InShell>
      <MyInputsPage />
    </InShell>
  ),
  decorators: [withApp({ me, path: "/my-inputs", handlers })],
});

const meta: Meta = { title: "Pages/My inputs", parameters: { layout: "fullscreen" } };
export default meta;
type Story = StoryObj;

/** Three inputs: one not started (reminded), one saved as a draft, one submitted. */
export const Due: Story = page({ "input.task.list": { data: tasksDue } });
export const AllSubmitted: Story = page({ "input.task.list": { data: tasksAllSubmitted } });
/** Past the deadline: read-only, and a correction is a restatement. */
export const Locked: Story = page({ "input.task.list": { data: tasksLocked } });
export const Restating: Story = page({ "input.task.list": { data: tasksRestating } });
/** The one place an empty screen is good news. */
export const NothingDue: Story = page({ "input.task.list": { data: tasksNone } });
export const NothingDueButLastMonthOpen: Story = page({ "input.task.list": { data: tasksOtherMonth } });
export const Loading: Story = page({ "input.task.list": "pending" });
export const Failed: Story = page({ "input.task.list": { error: serverError } });
export const NoAccess: Story = page({}, {
  ...contributorMe,
  no_access: [{ page_key: "my_inputs", missing: "Your account has no input rights.", ask: "an Admin, under Administer → Users & access" }],
});
/** A value that is not a number is caught before it is sent. */
export const InvalidValue: Story = {
  render: () => (
    <div style={{ padding: 24 }}>
      <InputsView tasks={{ ...tasksDue, tasks: [{ ...tasksDue.tasks[0], state: "draft", value: "abc" }] }} onChanged={() => {}} onPeriod={() => {}} now={NOW} />
    </div>
  ),
};

const admin = (list: typeof assignments, metrics = manualMetrics): Story => ({
  render: () => (
    <div style={{ padding: 24, maxWidth: 1100 }}>
      <section className="kg-card">
        <ManualInputView list={list} metrics={metrics} users={people} onChanged={() => {}} />
      </section>
    </div>
  ),
});

/** Admin view: a named contributor, a line-manager slice that resolves to nobody, and an unassigned metric. */
export const AdminAssignments: Story = admin(assignments);
export const AdminNothingAssigned: Story = admin(noAssignments);
/** No mail relay on this install: reminders are in-app only, and the page says so. */
export const AdminEmailOff: Story = admin({ ...assignments, email_reminders: false });
export const AdminNoManualMetrics: Story = admin(noAssignments, []);
