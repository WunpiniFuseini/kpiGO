import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { ROUTES, type ActionName } from "../api/actions";
import { setTransport } from "../api/client";
import { WidgetForm, WidgetsPage, WidgetsTable } from "../pages/admin/Widgets";
import {
  dimensionRule,
  effectiveSeries,
  executiveMetrics,
  metricBlocked,
  typeByKey,
  validateDraft,
  type WidgetDraft,
} from "../pages/admin/widgetForm";
import { SessionProvider, type Me } from "../session/Session";
import { adminMe } from "../stories/fixtures";
import { dimensions, execMetrics, types, widgetList } from "../stories/widgetsAdminFixtures";

const byKey = typeByKey(types);
const byCode = new Map(execMetrics.metrics.map((m) => [m.metric_code, m]));
const dimOpts = dimensions.dimensions.map((d) => ({ value: d.dimension_type, label: d.display_name }));
const execMetricRows = executiveMetrics(execMetrics.metrics);

function draft(over: Partial<WidgetDraft> = {}): WidgetDraft {
  return { widget_key: "w1", title: "", widget_type: "kpi_card", metrics: ["ex_revenue"], dimension: "", series: ["actual"], ...over };
}

describe("widget form rules", () => {
  it("reads the breakdown rule from the metric count", () => {
    expect(dimensionRule(byKey.get("bar")!, 1)).toBe("required");
    expect(dimensionRule(byKey.get("bar")!, 2)).toBe("optional");
    expect(dimensionRule(byKey.get("kpi_card")!, 1)).toBe("never");
  });

  it("only offers metrics bound to Executive", () => {
    expect(execMetricRows.map((m) => m.metric_code)).not.toContain("sc_only");
    expect(execMetricRows.map((m) => m.metric_code)).toContain("ex_revenue");
  });

  it("blocks a non-additive metric on an additive-only type, and a second unit on a one-unit type", () => {
    const funnel = byKey.get("funnel")!;
    expect(metricBlocked(funnel, [], byCode.get("ex_cti")!)).toMatch(/sum or a count/);
    const bar = byKey.get("bar")!;
    expect(metricBlocked(bar, [byCode.get("ex_revenue")!], byCode.get("ex_leads")!)).toMatch(/one unit/);
  });

  it("validates a draft against its type", () => {
    expect(validateDraft(draft(), byKey.get("kpi_card")!, byCode)).toEqual([]);
    expect(validateDraft(draft({ widget_type: "kpi_card", dimension: "region" }), byKey.get("kpi_card")!, byCode)).toContain(
      "A KPI card does not break down by a dimension.",
    );
    expect(validateDraft(draft({ widget_type: "bar", dimension: "" }), byKey.get("bar")!, byCode)).toContain(
      "A Bar breaks down by a dimension; choose one.",
    );
    const bulletProblems = validateDraft(draft({ widget_type: "bullet", series: ["actual"] }), byKey.get("bullet")!, byCode);
    expect(bulletProblems.some((p) => /comparison series/.test(p))).toBe(true);
  });

  it("stores only the actual for a type with no comparisons", () => {
    expect(effectiveSeries(byKey.get("pie")!, ["actual", "target"])).toEqual(["actual"]);
    expect(effectiveSeries(byKey.get("line")!, ["target"])).toEqual(["actual", "target"]);
  });
});

describe("the widgets table", () => {
  const noop = () => {};
  it("offers Place for a feed-registered key and Edit/History for a placed one", () => {
    render(
      <WidgetsTable widgets={widgetList.widgets} types={types} canManage onPlace={noop} onEdit={noop} onThresholds={noop} onHistory={noop} onChanged={noop} />,
    );
    expect(screen.getByRole("button", { name: "Place" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Edit" }).length).toBe(2);
    // A bar draws no threshold bands, so it offers no Thresholds action; the KPI card does.
    expect(screen.getAllByRole("button", { name: "Thresholds" }).length).toBe(1);
  });

  it("shows no actions to a viewer who cannot manage", () => {
    render(
      <WidgetsTable widgets={widgetList.widgets} types={types} canManage={false} onPlace={noop} onEdit={noop} onThresholds={noop} onHistory={noop} onChanged={noop} />,
    );
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
  });
});

describe("the widget form", () => {
  const noop = () => {};
  function renderForm(extra: Partial<Parameters<typeof WidgetForm>[0]> = {}) {
    return render(<WidgetForm types={types} metrics={execMetricRows} dimensions={dimOpts} existingKeys={[]} onClose={noop} onSaved={noop} {...extra} />);
  }

  it("disables the breakdown for a type that never breaks down", () => {
    renderForm();
    expect(screen.getByLabelText("Breakdown")).toBeDisabled();
  });

  it("switching to a breakdown type enables the dimension and blocks submit until one is chosen", () => {
    renderForm({ prefillKey: "w" });
    fireEvent.change(screen.getByLabelText("Type"), { target: { value: "bar" } });
    const breakdown = screen.getByLabelText("Breakdown");
    expect(breakdown).toBeEnabled();
    expect(screen.getByText("A Bar breaks down by a dimension; choose one.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Place widget" })).toBeDisabled();
    fireEvent.change(breakdown, { target: { value: "region" } });
    expect(screen.queryByText("A Bar breaks down by a dimension; choose one.")).not.toBeInTheDocument();
  });
});

function serve(me: Me, replies: Partial<Record<ActionName, unknown>>) {
  setTransport(async (url) => {
    const action = (Object.keys(ROUTES) as ActionName[]).find((n) => ROUTES[n].path === url.split("?")[0]);
    const payload = action === "auth.me" ? me : action ? (replies[action] ?? {}) : {};
    return new Response(JSON.stringify(payload), { status: 200 });
  });
}

describe("the widgets page", () => {
  const manageMe: Me = { ...adminMe, permissions: [...adminMe.permissions, "widget.manage"] };

  it("lists the placed widgets and opens the place form", async () => {
    serve(manageMe, { "widget.list": widgetList, "metric.list": execMetrics, "dimension.list": dimensions });
    render(
      <MemoryRouter initialEntries={["/admin/widgets"]}>
        <SessionProvider initial={{ status: "signed-in", me: manageMe }}>
          <WidgetsPage />
        </SessionProvider>
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByRole("rowheader", { name: /Total revenue/ })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Place widget" }));
    expect(screen.getByRole("heading", { name: "Place a widget" })).toBeInTheDocument();
    expect(screen.getByLabelText("Widget key")).toBeInTheDocument();
  });
});
