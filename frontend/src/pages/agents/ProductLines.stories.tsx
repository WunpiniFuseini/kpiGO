import type { Meta, StoryObj } from "@storybook/react-vite";
import { Route, Routes } from "react-router-dom";

import { AppShell } from "../../shell/AppShell";
import { useSession, type Me } from "../../session/Session";
import { leaderboard, matrix, presetSales, productLineAdminMe, trend, registry, registryEmpty, registryFresh } from "../../stories/agentFixtures";
import { serverError } from "../../stories/fixtures";
import { withApp, type Handlers } from "../../stories/mockApi";
import { AgentPerformancePage } from "./AgentPerformance";
import { ProductLinesPage } from "./ProductLines";

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

const page = (handlers: Handlers, me: Me = productLineAdminMe): Story => ({
  render: () => (
    <InShell>
      <ProductLinesPage />
    </InShell>
  ),
  decorators: [withApp({ me, path: "/admin/product-lines", handlers })],
});

const meta: Meta = { title: "Pages/Product lines", parameters: { layout: "fullscreen" } };
export default meta;
type Story = StoryObj;

/** Two groups, a line retiring next month, one line the feed has just made available. */
export const Default: Story = page({ "product_line.registry": { data: registry } });
/** The first load carried two product codes; nothing is switched on yet. */
export const WaitingOnTheAdmin: Story = page({ "product_line.registry": { data: registryFresh } });
/** No product line has ever come through the feed. */
export const NothingInTheFeed: Story = page({ "product_line.registry": { data: registryEmpty } });
export const Loading: Story = page({ "product_line.registry": "pending" });
export const Failed: Story = page({ "product_line.registry": { error: serverError } });

/** Quick settings on the Agent Performance page, for an Admin. */
export const QuickSettings: Story = {
  render: () => (
    <InShell>
      <AgentPerformancePage />
    </InShell>
  ),
  decorators: [withApp({ me: productLineAdminMe, path: "/agent-performance", handlers: { "agent.preset": { data: presetSales }, "agent.leaderboard": { data: leaderboard }, "agent.matrix": { data: matrix }, "agent.trend": { data: trend }, "product_line.registry": { data: registry } } })],
};
