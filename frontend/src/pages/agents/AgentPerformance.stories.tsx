import type { Meta, StoryObj } from "@storybook/react-vite";
import { Route, Routes } from "react-router-dom";

import { AppShell } from "../../shell/AppShell";
import { useSession, type Me } from "../../session/Session";
import { agentAdminMe, agentMe, agentPace, distribution, heatmap, leaderboard, matrix, presetRestricted, presetSales, presetService, trend, trendAverage, visibilityRules, matrixGrouped, matrixNoLines, matrixNoMetric, matrixRms, leaderboardComposite, leaderboardNoCohorts, leaderboardUnconfigured } from "../../stories/agentFixtures";
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
  decorators: [withApp({ me, path: "/agent-performance", handlers: { "agent.preset": { data: presetSales }, "agent.trend": { data: trend }, "agent.pace": { data: agentPace }, "agent.matrix": { data: matrix }, "preference.set": { data: { key: "agent.matrix.view", value: "grouped", allowed: ["expanded", "grouped"] } }, ...handlers } })],
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
export const Loading: Story = page({ "agent.leaderboard": "pending", "agent.preset": "pending" });
export const Failed: Story = page({ "agent.leaderboard": { error: serverError } });
/** The preset itself failed to load. */
export const PresetFailed: Story = page({ "agent.preset": { error: serverError } });
export const NoAccess: Story = page({}, {
  ...agentMe,
  no_access: [{ page_key: "agent_performance", missing: "Your roles do not include Agent Performance.", ask: "an Admin, under Administer → Users & access" }],
});
/** The product-line matrix grouped into product groups (the reader's saved view). */
export const MatrixGrouped: Story = page({ "agent.leaderboard": { data: leaderboard }, "agent.matrix": { data: matrixGrouped } });
/** Drilled to one branch's RMs, with the breadcrumb back up. */
export const MatrixRms: Story = page({ "agent.leaderboard": { data: leaderboard }, "agent.matrix": { data: matrixRms } });
/** No product line switched on: only All products. */
export const MatrixNoLines: Story = page({ "agent.leaderboard": { data: leaderboard }, "agent.matrix": { data: matrixNoLines } });
/** No sum or count metric to add up. */
export const MatrixNoMetric: Story = page({ "agent.leaderboard": { data: leaderboard }, "agent.matrix": { data: matrixNoMetric } });
export const MatrixLoading: Story = page({ "agent.leaderboard": { data: leaderboard }, "agent.matrix": "pending" });
export const MatrixFailed: Story = page({ "agent.leaderboard": { data: leaderboard }, "agent.matrix": { error: serverError } });
/** Service: SLA heatmap, TAT distribution, queue trend, then the leaderboard. */
export const Service: Story = page({
  "agent.preset": { data: presetService },
  "agent.heatmap": { data: heatmap },
  "agent.distribution": { data: distribution },
  "agent.trend": { data: trendAverage },
  "agent.leaderboard": { data: leaderboard },
});
/** A Staff member whose role is narrowed to their branch. */
export const Restricted: Story = page({ "agent.preset": { data: presetRestricted }, "agent.leaderboard": { data: leaderboard } });
/** An Admin sees the Who sees whom and Product lines quick settings. */
export const AdminQuickSettings: Story = page({ "agent.leaderboard": { data: leaderboard }, "agent.visibility.list": { data: visibilityRules } }, agentAdminMe);
