import type { Meta, StoryObj } from "@storybook/react-vite";
import { Route, Routes } from "react-router-dom";

import { AppShell } from "../../shell/AppShell";
import { useSession, type Me } from "../../session/Session";
import { agentMe, agentPace, leaderboard, leaderboardComposite, leaderboardNoCohorts, leaderboardUnconfigured } from "../../stories/agentFixtures";
import { serverError } from "../../stories/fixtures";
import { withApp, type Handlers } from "../../stories/mockApi";
import { AgentPerformancePage } from "./AgentPerformance";

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

const page = (handlers: Handlers, me: Me = agentMe): Story => ({
  render: () => (
    <InShell>
      <AgentPerformancePage />
    </InShell>
  ),
  decorators: [withApp({ me, path: "/agent-performance", handlers: { "agent.pace": { data: agentPace }, ...handlers } })],
});

const meta: Meta = { title: "Pages/Agent Performance", parameters: { layout: "fullscreen" } };
export default meta;
type Story = StoryObj;

/** Ranked by value booked within Greater Accra; a tie broken by accounts opened, one agent unranked. */
export const Default: Story = page({ "agent.leaderboard": { data: leaderboard } });
/** Ranked by overall pace: no cohort total to add up, so only the on-pace card. */
export const OverallPace: Story = page({ "agent.leaderboard": { data: leaderboardComposite } });
/** Empty: no metric is bound to the module yet. */
export const NotConfigured: Story = page({ "agent.leaderboard": { data: leaderboardUnconfigured } });
/** Empty: custom cohorts chosen, none has members on the day. */
export const NoCustomCohorts: Story = page({ "agent.leaderboard": { data: leaderboardNoCohorts } });
export const Loading: Story = page({ "agent.leaderboard": "pending" });
export const Failed: Story = page({ "agent.leaderboard": { error: serverError } });
export const NoAccess: Story = page({}, {
  ...agentMe,
  no_access: [{ page_key: "agent_performance", missing: "Your roles do not include Agent Performance.", ask: "an Admin, under Administer → Users & access" }],
});
