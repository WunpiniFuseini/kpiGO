import type { Meta, StoryObj } from "@storybook/react-vite";
import { Route, Routes } from "react-router-dom";

import { AppShell } from "../../shell/AppShell";
import { useSession, type Me } from "../../session/Session";
import {
  approvalQueue,
  approverMe,
  decidedApprovals,
  myProposals,
  myProposalsSecondPerson,
  noApprovals,
  noMyProposals,
  proposerMe,
  serverError,
} from "../../stories/fixtures";
import { withApp, type Handlers } from "../../stories/mockApi";
import { ApprovalsPage } from "./Approvals";

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

const page = ({ me = approverMe, handlers = {} }: { me?: Me; handlers?: Handlers }) => ({
  render: () => (
    <InShell>
      <ApprovalsPage />
    </InShell>
  ),
  decorators: [withApp({ me, path: "/admin/approvals", handlers })],
});

const meta: Meta = { title: "Pages/Approvals", parameters: { layout: "fullscreen" } };
export default meta;
type Story = StoryObj;

/** A decider's view: the whole organisation's waiting requests, with approve and reject. */
export const Queue: Story = page({ handlers: { "platform.approval.list": { data: approvalQueue } } });
/** A person's own proposals: confirm or withdraw what they (or their assistant) asked for. */
export const MyProposals: Story = page({ me: proposerMe, handlers: { "platform.approval.list": { data: myProposals } } });
/** A maker-checker class is on, so the requester may only withdraw; a second person approves. */
export const NeedsSecondPerson: Story = page({ me: proposerMe, handlers: { "platform.approval.list": { data: myProposalsSecondPerson } } });
/** Already decided: who approved or rejected each, and why. */
export const Decided: Story = page({ handlers: { "platform.approval.list": { data: decidedApprovals } } });
/** Nothing is waiting for a decider. */
export const Empty: Story = page({ handlers: { "platform.approval.list": { data: noApprovals } } });
/** A person with no proposals of their own. */
export const EmptyOwn: Story = page({ me: proposerMe, handlers: { "platform.approval.list": { data: noMyProposals } } });
export const ApprovalsLoading: Story = page({ handlers: { "platform.approval.list": "pending" } });
export const ApprovalsError: Story = page({ handlers: { "platform.approval.list": { error: serverError } } });
