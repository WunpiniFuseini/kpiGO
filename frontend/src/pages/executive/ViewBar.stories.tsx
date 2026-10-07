import type { Decorator, Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";

import { ROUTES, type ActionName } from "../../api/actions";
import { setTransport } from "../../api/client";
import { ViewBar, type DashboardState } from "./ViewBar";

type ViewRow = { name: string; state: DashboardState; created_at: string; updated_at: string };

function row(name: string, period: string, drill: Record<string, string> = {}): ViewRow {
  return { name, state: { period_key: period, drill }, created_at: "", updated_at: "" };
}

function nameFor(url: string): ActionName | undefined {
  const path = url.split("?")[0];
  return (Object.keys(ROUTES) as ActionName[]).find((n) => ROUTES[n].path === path);
}

/** Serve the saved-view list, and echo saves/deletes so the bar behaves live. */
function serve(initial: ViewRow[]): Decorator {
  return () => {
    let views = [...initial];
    setTransport(async (url, init) => {
      const name = nameFor(url);
      const body = init?.body ? JSON.parse(String(init.body)) : {};
      if (name === "executive.view.list") return json({ views });
      if (name === "executive.view.save") {
        const saved = row(body.name, body.state.period_key, body.state.drill);
        views = [...views.filter((v) => v.name !== body.name), saved];
        return json(saved);
      }
      if (name === "executive.view.delete") {
        views = views.filter((v) => v.name !== body.name);
        return json({ name: body.name, deleted: true });
      }
      return json({ error: "not_found", message: `No handler for ${name ?? url}.` }, 404);
    });
    return <Harness />;
  };
}

function Harness() {
  const [state, setState] = useState<DashboardState>({ period_key: "202610", drill: {} });
  return (
    <div style={{ display: "flex", gap: 12, alignItems: "flex-end", padding: 16 }}>
      <ViewBar state={state} onApply={setState} />
      <span style={{ fontSize: 13 }}>
        Period {state.period_key}; {Object.keys(state.drill).length} drilled
      </span>
    </div>
  );
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status });
}

const meta: Meta<typeof ViewBar> = { title: "Pages/Executive/ViewBar" };
export default meta;
type Story = StoryObj<typeof ViewBar>;

/** No saved views yet: only the Save control is offered. */
export const Empty: Story = { decorators: [serve([])] };

/** A few saved views to pick from and delete. */
export const WithViews: Story = {
  decorators: [serve([row("Q4 by region", "202610", { nps_by_region: "south" }), row("Last month", "202609")])],
};
