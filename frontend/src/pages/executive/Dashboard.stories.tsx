import type { Decorator, Meta, StoryObj } from "@storybook/react-vite";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import type { ActionName } from "../../api/actions";
import { ROUTES } from "../../api/actions";
import { setTransport } from "../../api/client";
import { AppShell } from "../../shell/AppShell";
import { SessionProvider, type Me } from "../../session/Session";
import { byKey, dashboard, emptyDashboard } from "../../stories/executiveFixtures";
import { adminMe, executiveNoGrantMe, serverError } from "../../stories/fixtures";
import { ExecutiveDashboardPage } from "./Dashboard";

type DashReply = "ok" | "pending" | "error" | "empty";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status });
}

function nameFor(url: string): ActionName | undefined {
  const path = url.split("?")[0];
  return (Object.keys(ROUTES) as ActionName[]).find((n) => ROUTES[n].path === path);
}

/** A transport that serves the dashboard and each tile's data by widget_key. */
function execApp({ me = adminMe, dash = "ok", dataError = false }: { me?: Me; dash?: DashReply; dataError?: boolean }): Decorator {
  return (Story) => {
    setTransport(async (url) => {
      const name = nameFor(url);
      if (name === "auth.me") return json(me);
      if (name === "executive.view.list") return json({ views: [] });
      if (name === "widget.dashboard") {
        if (dash === "pending") return new Promise<Response>(() => {});
        if (dash === "error") return json(serverError, 500);
        return json(dash === "empty" ? emptyDashboard : dashboard);
      }
      if (name === "widget.data") {
        if (dataError) return json(serverError, 500);
        const key = new URLSearchParams(url.split("?")[1] ?? "").get("widget_key") ?? "";
        const data = byKey[key];
        if (data) return json(data);
        return json({ ...byKey.revenue, widget_key: key, empty: "You need an Executive data grant for the whole organisation to see this widget." });
      }
      return json({ error: "not_found", message: `No handler for ${name ?? url}.` }, 404);
    });
    return (
      <MemoryRouter initialEntries={["/executive"]}>
        <SessionProvider initial={{ status: "signed-in", me }}>
          <AppShell me={me} onSignOut={() => {}}>
            <Routes>
              <Route path="*" element={<Story />} />
            </Routes>
          </AppShell>
        </SessionProvider>
      </MemoryRouter>
    );
  };
}

const meta: Meta = { title: "Pages/Executive", parameters: { layout: "fullscreen" } };
export default meta;
type Story = StoryObj;

const page = (opts: Parameters<typeof execApp>[0] = {}): Story => ({
  render: () => <ExecutiveDashboardPage />,
  decorators: [execApp(opts)],
});

/** One widget of every type across the periwinkle canvas. */
export const Default: Story = page();
/** The dashboard is still loading its layout. */
export const Loading: Story = page({ dash: "pending" });
/** The layout failed to load, with a reference to quote. */
export const Failed: Story = page({ dash: "error" });
/** No widgets placed yet: an Admin is pointed at the Widgets admin. */
export const Empty: Story = page({ dash: "empty" });
/** A viewer without the Executive data grant is told which grant they lack. */
export const NoAccess: Story = page({ me: executiveNoGrantMe });
/** A widget's own figures failed to load, within an otherwise fine dashboard. */
export const WidgetFailed: Story = page({ dataError: true });
