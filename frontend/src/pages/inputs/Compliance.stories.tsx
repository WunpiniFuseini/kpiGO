import type { Meta, StoryObj } from "@storybook/react-vite";
import { Route, Routes } from "react-router-dom";

import { AppShell } from "../../shell/AppShell";
import { useSession, type Me } from "../../session/Session";
import { serverError } from "../../stories/fixtures";
import { compliance, complianceAllGood, complianceEmpty, complianceTeamEmpty, managerMe } from "../../stories/inputFixtures";
import { withApp, type Handlers } from "../../stories/mockApi";
import { CompliancePage } from "./Compliance";

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

const complianceMe: Me = {
  ...managerMe,
  pages: [...managerMe.pages, { page_key: "input_compliance", label: "Input compliance", group: "modules", access: "view" }] as Me["pages"],
};

const page = (handlers: Handlers, me: Me = complianceMe): Story => ({
  render: () => (
    <InShell>
      <CompliancePage />
    </InShell>
  ),
  decorators: [withApp({ me, path: "/input-compliance", handlers })],
});

const meta: Meta = { title: "Pages/Input compliance", parameters: { layout: "fullscreen" } };
export default meta;
type Story = StoryObj;

/** A chronically late contributor comes first, flagged in words as well as colour. */
export const Default: Story = page({ "input.compliance": { data: compliance } });
export const EveryoneOnTime: Story = page({ "input.compliance": { data: complianceAllGood } });
/** Empty for an Admin: nothing was assigned. */
export const NothingAsked: Story = page({ "input.compliance": { data: complianceEmpty } });
/** Empty for a manager: none of their team owed an input. */
export const NothingAskedOfYourTeam: Story = page({ "input.compliance": { data: complianceTeamEmpty } });
export const Loading: Story = page({ "input.compliance": "pending" });
export const Failed: Story = page({ "input.compliance": { error: serverError } });
export const NoAccess: Story = page({}, {
  ...complianceMe,
  no_access: [{ page_key: "input_compliance", missing: "Your account is not linked to a person in the hierarchy.", ask: "an Admin, under Administer → Users & access" }],
});
