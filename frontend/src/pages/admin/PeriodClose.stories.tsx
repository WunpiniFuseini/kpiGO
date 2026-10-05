import type { Meta, StoryObj } from "@storybook/react-vite";
import { Route, Routes } from "react-router-dom";

import { AppShell } from "../../shell/AppShell";
import { useSession } from "../../session/Session";
import { forbidden, serverError } from "../../stories/fixtures";
import { withApp, type Handlers } from "../../stories/mockApi";
import {
  closeBlocked,
  closeClosed,
  closeFuture,
  closeReady,
  closeRestating,
  configMe,
  exclusions,
  noExclusions,
  noSnapshots,
  snapshots,
} from "../../stories/scorecardFixtures";
import { PeriodClosePage } from "./PeriodClose";

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

const page = (handlers: Handlers): Story => ({
  render: () => (
    <InShell>
      <PeriodClosePage />
    </InShell>
  ),
  decorators: [withApp({ me: configMe, path: "/admin/calendar", handlers })],
});

const meta: Meta = { title: "Pages/Period close", parameters: { layout: "fullscreen" } };
export default meta;
type Story = StoryObj;

/** Blockers name the metric and the people; an exclusion needs a reason. */
export const Blocked: Story = page({
  "scorecard.close.check": { data: closeBlocked },
  "scorecard.exclusion.list": { data: noExclusions },
  "scorecard.snapshot.list": { data: noSnapshots },
});
export const Ready: Story = page({
  "scorecard.close.check": { data: closeReady },
  "scorecard.exclusion.list": { data: exclusions },
  "scorecard.snapshot.list": { data: noSnapshots },
});
export const Restating: Story = page({
  "scorecard.close.check": { data: closeRestating },
  "scorecard.exclusion.list": { data: exclusions },
  "scorecard.snapshot.list": { data: { ...snapshots, snapshots: snapshots.snapshots.slice(1) } },
});
/** Closed and restated once: both versions stay on record. */
export const Closed: Story = page({
  "scorecard.close.check": { data: closeClosed },
  "scorecard.exclusion.list": { data: exclusions },
  "scorecard.snapshot.list": { data: snapshots },
});
export const NotStarted: Story = page({
  "scorecard.close.check": { data: closeFuture },
  "scorecard.exclusion.list": { data: noExclusions },
  "scorecard.snapshot.list": { data: noSnapshots },
});
export const Loading: Story = page({
  "scorecard.close.check": "pending",
  "scorecard.exclusion.list": "pending",
  "scorecard.snapshot.list": "pending",
});
export const Failed: Story = page({
  "scorecard.close.check": { error: serverError },
  "scorecard.exclusion.list": { error: serverError },
  "scorecard.snapshot.list": { error: serverError },
});
export const NoPermission: Story = page({
  "scorecard.close.check": { error: forbidden },
  "scorecard.exclusion.list": { error: forbidden },
  "scorecard.snapshot.list": { error: forbidden },
});
