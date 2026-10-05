import type { Meta, StoryObj } from "@storybook/react-vite";
import { Route, Routes } from "react-router-dom";

import { AppShell } from "../shell/AppShell";
import { useSession, type Me } from "../session/Session";
import {
  adminMe,
  executiveNoGrantMe,
  forbidden,
  healthDegraded,
  healthEmpty,
  healthOk,
  metrics,
  noPagesMe,
  roles,
  serverError,
  staffUnlinkedMe,
  users,
} from "../stories/fixtures";
import { withApp, type Handlers } from "../stories/mockApi";
import { App } from "../App";
import { HealthPage } from "./admin/Health";
import { MetricsPage } from "./admin/Metrics";
import { UsersPage } from "./admin/Users";
import { ModulePage } from "./ModulePage";

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

const page = (element: React.ReactNode, { me = adminMe, path, handlers = {} }: { me?: Me; path: string; handlers?: Handlers }) => ({
  render: () => <InShell>{element}</InShell>,
  decorators: [withApp({ me, path, handlers })],
});

const meta: Meta = { title: "Pages/App", parameters: { layout: "fullscreen" } };
export default meta;
type Story = StoryObj;

export const Health: Story = page(<HealthPage />, { path: "/admin/health", handlers: { "system.health": { data: healthOk } } });
export const HealthDegraded: Story = page(<HealthPage />, { path: "/admin/health", handlers: { "system.health": { data: healthDegraded } } });
export const HealthNoFeeds: Story = page(<HealthPage />, { path: "/admin/health", handlers: { "system.health": { data: healthEmpty } } });
export const HealthLoading: Story = page(<HealthPage />, { path: "/admin/health", handlers: { "system.health": "pending" } });
export const HealthError: Story = page(<HealthPage />, { path: "/admin/health", handlers: { "system.health": { error: serverError } } });

export const Users: Story = page(<UsersPage />, { path: "/admin/users", handlers: { "user.list": { data: users }, "role.list": { data: roles } } });
export const UsersLoading: Story = page(<UsersPage />, { path: "/admin/users", handlers: { "user.list": "pending" } });
export const UsersNoPermission: Story = page(<UsersPage />, { path: "/admin/users", handlers: { "user.list": { error: forbidden } } });

export const Metrics: Story = page(<MetricsPage />, { path: "/admin/metrics", handlers: { "metric.list": { data: metrics } } });
export const MetricsEmpty: Story = page(<MetricsPage />, {
  path: "/admin/metrics",
  handlers: { "metric.list": { data: { as_of: "2026-10-04", metrics: [] } } },
});

export const ModuleNotBuilt: Story = page(<ModulePage pageKey="scorecards" title="Scorecards" />, { path: "/scorecards" });
/** Page allowed, scope absent: names the missing grant and who to ask (App Flow §9). */
export const NoAccessUnlinked: Story = page(<ModulePage pageKey="scorecards" title="Scorecards" />, { me: staffUnlinkedMe, path: "/scorecards" });
export const NoAccessExecutiveGrant: Story = page(<ModulePage pageKey="executive" title="Executive" exec />, {
  me: executiveNoGrantMe,
  path: "/executive",
});
/** Signed in, but no role opens a page. */
export const NoPages: Story = { render: () => <App />, decorators: [withApp({ me: noPagesMe, path: "/" })] };
