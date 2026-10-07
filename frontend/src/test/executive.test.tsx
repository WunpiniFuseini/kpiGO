import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { ROUTES, type ActionName } from "../api/actions";
import { setTransport } from "../api/client";
import { ExecutiveDashboardPage } from "../pages/executive/Dashboard";
import { WidgetBody } from "../pages/executive/renderers";
import type { WidgetData } from "../pages/executive/widgetData";
import { bandIndex, figure, gradeValue, standingFor } from "../pages/executive/widgetData";
import { SessionProvider, type Me } from "../session/Session";
import {
  barData,
  dashboard,
  emptyDashboard,
  kpiData,
  rankedData,
  tableData,
} from "../stories/executiveFixtures";
import { adminMe, executiveNoGrantMe } from "../stories/fixtures";

function body(data: WidgetData) {
  return render(<WidgetBody data={data} onDrill={() => {}} />);
}

function serveExec(me: Me, widgets: unknown, tile: unknown = kpiData) {
  const calls: { action: ActionName | undefined; url: string }[] = [];
  setTransport(async (url) => {
    const action = (Object.keys(ROUTES) as ActionName[]).find((n) => ROUTES[n].path === url.split("?")[0]);
    calls.push({ action, url });
    const payload =
      action === "auth.me"
        ? me
        : action === "executive.view.list"
          ? { views: [] }
          : action === "widget.dashboard"
            ? widgets
            : action === "widget.data"
              ? tile
              : {};
    return new Response(JSON.stringify(payload), { status: 200 });
  });
  return calls;
}

function mountDashboard(me: Me, widgets: unknown, tile: unknown = kpiData) {
  const calls = serveExec(me, widgets, tile);
  render(
    <MemoryRouter initialEntries={["/executive"]}>
      <SessionProvider initial={{ status: "signed-in", me }}>
        <ExecutiveDashboardPage />
      </SessionProvider>
    </MemoryRouter>,
  );
  return calls;
}

describe("widget figures", () => {
  it("reads a series value off a metric, and null when not reported", () => {
    const org = kpiData.metrics[0].org!;
    expect(figure(org, "actual")).toBe(42_600_000);
    expect(figure(org, "budget")).toBeNull();
  });

  it("grades on achievement: actual over target, placed on the bands", () => {
    const th = barData.thresholds!;
    // 150 against 130 is 1.15 → the third band (On Target at 1.0), below Exemplary (1.2).
    expect(gradeValue(th, 150, 130)).toBeCloseTo(1.1538, 3);
    expect(bandIndex(th, 1.15)).toBe(2);
    expect(standingFor(th, 150, 130)?.label).toBe("On Target");
    // No target on an achievement basis cannot be graded.
    expect(standingFor(th, 150, null)).toBeNull();
  });
});

describe("widget renderers", () => {
  it("a KPI card shows the figure and its target", () => {
    body(kpiData);
    expect(screen.getByText("GHS 42,600,000")).toBeInTheDocument();
    expect(screen.getByText(/Target/)).toBeInTheDocument();
  });

  it("a ranked list orders members by value, highest first", () => {
    body(rankedData);
    const items = within(screen.getByRole("list", { name: /CASA growth by region/ })).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("South");
    expect(items[1]).toHaveTextContent("North");
  });

  it("a table is a members-by-metrics grid with a RAG standing per cell", () => {
    body(tableData);
    expect(screen.getByRole("columnheader", { name: "Revenue" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "CASA" })).toBeInTheDocument();
    expect(screen.getByRole("rowheader", { name: "North" })).toBeInTheDocument();
    // Colour is never alone: each graded cell carries its band label for a screen reader.
    expect(screen.getAllByText("On Target").length).toBeGreaterThan(0);
  });

  it("a chart keeps its figures one click away in a table", () => {
    body(barData);
    fireEvent.click(screen.getByRole("button", { name: "Table" }));
    const table = screen.getByRole("table");
    expect(within(table).getByRole("rowheader", { name: "North" })).toBeInTheDocument();
    expect(within(table).getByRole("rowheader", { name: "South" })).toBeInTheDocument();
  });
});

describe("the dashboard page", () => {
  it("lays out the placed widgets and reads each one's figures", async () => {
    const calls = mountDashboard(adminMe, dashboard);
    await waitFor(() => expect(screen.getByRole("heading", { name: "Total revenue" })).toBeInTheDocument());
    expect(screen.getByRole("heading", { name: "CASA by region" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Regions by metric" })).toBeInTheDocument();
    expect(calls.some((c) => c.action === "widget.data")).toBe(true);
  });

  it("tells a viewer without the grant which grant they lack", async () => {
    mountDashboard(executiveNoGrantMe, dashboard);
    await waitFor(() => expect(screen.getByText(/You cannot see Executive data yet/)).toBeInTheDocument());
    expect(screen.getByText(/no Executive data-scope grant/)).toBeInTheDocument();
  });

  it("points an Admin at the Widgets admin when nothing is placed", async () => {
    mountDashboard(adminMe, emptyDashboard);
    await waitFor(() => expect(screen.getByText(/No widgets on the dashboard yet/)).toBeInTheDocument());
  });
});
