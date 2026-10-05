import type { Meta, StoryObj } from "@storybook/react-vite";
import { Route, Routes } from "react-router-dom";

import { AppShell } from "../../shell/AppShell";
import { useSession, type Me } from "../../session/Session";
import { serverError, staffUnlinkedMe } from "../../stories/fixtures";
import { withApp, type Handlers } from "../../stories/mockApi";
import {
  cardClosed,
  cardFuture,
  cardLive,
  cardNoMetrics,
  cardNotGraded,
  cardRestated,
  cardUnassigned,
  emptyQueue,
  history,
  historyLive,
  managerMe,
  overridesForQuery,
  queue,
  rmMe,
  team,
  threadAcknowledged,
  threadManager,
  threadOpenPeriod,
  threadRestated,
  threadToAcknowledge,
} from "../../stories/scorecardViewFixtures";
import { ProvenancePanel, ScorecardPage } from "./Scorecard";

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

const own: Handlers = {
  "scorecard.period.list": { data: { ...team, rows: team.rows.slice(0, 1), total: 1 } },
  "scorecard.query.list": { data: emptyQueue },
  "scorecard.history": { data: history },
};

const page = (handlers: Handlers, me: Me = rmMe): Story => ({
  render: () => (
    <InShell>
      <ScorecardPage />
    </InShell>
  ),
  decorators: [withApp({ me, path: "/scorecards", handlers: { ...own, ...handlers } })],
});

const meta: Meta = { title: "Pages/Scorecard", parameters: { layout: "fullscreen" } };
export default meta;
type Story = StoryObj;

/** A closed month: frozen, graded and waiting for the RM to say they have seen it. */
export const ClosedToAcknowledge: Story = page({
  "scorecard.compute": { data: cardClosed },
  "scorecard.interaction.list": { data: threadToAcknowledge },
});
export const Acknowledged: Story = page({
  "scorecard.compute": { data: cardClosed },
  "scorecard.interaction.list": { data: threadAcknowledged },
});
/** Restated after acknowledgement: the reason shows, and acknowledgement is asked again. */
export const Restated: Story = page({
  "scorecard.compute": { data: cardRestated },
  "scorecard.interaction.list": { data: threadRestated },
});
/** The open month: provisional, one metric awaiting data and left out rather than zeroed. */
export const ProvisionalOpenMonth: Story = page({
  "scorecard.compute": { data: cardLive },
  "scorecard.interaction.list": { data: threadOpenPeriod },
  "scorecard.history": { data: historyLive },
});
export const NothingScoredYet: Story = page({
  "scorecard.compute": { data: cardNotGraded },
  "scorecard.interaction.list": { data: threadOpenPeriod },
  "scorecard.history": { data: { ...history, points: [], missing: [] } },
});
export const NoRoleThisMonth: Story = page({
  "scorecard.compute": { data: cardUnassigned },
  "scorecard.interaction.list": { data: threadOpenPeriod },
});
export const MonthNotStarted: Story = page({
  "scorecard.compute": { data: cardFuture },
  "scorecard.interaction.list": { data: threadOpenPeriod },
});
export const ProfileWithoutMetrics: Story = page({
  "scorecard.compute": { data: cardNoMetrics },
  "scorecard.interaction.list": { data: threadOpenPeriod },
});
export const Loading: Story = page({ "scorecard.compute": "pending", "scorecard.interaction.list": "pending", "scorecard.history": "pending" });
export const Failed: Story = page({ "scorecard.compute": { error: serverError }, "scorecard.interaction.list": { error: serverError } });
/** Signed in but not linked to a person: NO ACCESS names the missing link and who fixes it. */
export const NoAccess: Story = page({}, staffUnlinkedMe);
/** A line manager: a person picker, commentary they can add, and the queries routed to them. */
export const ManagerView: Story = page(
  {
    "scorecard.period.list": { data: team },
    "scorecard.compute": { data: cardClosed },
    "scorecard.interaction.list": { data: threadManager },
    "scorecard.query.list": { data: queue },
    "override.list": { data: overridesForQuery },
  },
  managerMe,
);
export const NobodyToShow: Story = page(
  {
    "scorecard.period.list": { data: { ...team, rows: [], total: 0 } },
    "scorecard.query.list": { data: emptyQueue },
  },
  { ...managerMe, user: { ...managerMe.user, subject_id: null } },
);

const panel = (metric: number, facet: "target" | "actual" | "score"): Story => ({
  render: () => (
    <div style={{ padding: 24, maxWidth: 880 }}>
      <ProvenancePanel card={cardLive} metric={cardLive.metrics[metric]} facet={facet} onClose={() => {}} />
    </div>
  ),
});

/** Provenance for a yearly target with an override and an actual converted from USD. */
export const ProvenanceOverrideAndFx: Story = panel(3, "target");
export const ProvenanceLowerIsBetter: Story = panel(2, "score");
export const ProvenanceAwaitingData: Story = panel(4, "actual");
