import type { Meta, StoryObj } from "@storybook/react-vite";
import { Route, Routes } from "react-router-dom";

import { AppShell } from "../../shell/AppShell";
import { type Me, useSession } from "../../session/Session";
import { adminMe, serverError } from "../../stories/fixtures";
import {
  dimensions,
  execMetrics,
  history,
  placedWidget,
  types,
  widgetList,
  widgetListEmpty,
  widgetListWithRemoved,
} from "../../stories/widgetsAdminFixtures";
import { withApp, type Handlers } from "../../stories/mockApi";
import { HistoryPanel, ThresholdsForm, WidgetForm, WidgetsPage } from "./Widgets";

const manageMe: Me = {
  ...adminMe,
  permissions: [...adminMe.permissions, "widget.manage"],
  pages: [...adminMe.pages, { page_key: "admin.widgets", label: "Widgets", group: "administer", access: "edit" }],
};
const viewOnlyMe: Me = { ...manageMe, permissions: adminMe.permissions.filter((p) => p !== "widget.manage") };

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

const page = (handlers: Handlers, me: Me = manageMe): StoryObj => ({
  render: () => (
    <InShell>
      <WidgetsPage />
    </InShell>
  ),
  decorators: [
    withApp({
      me,
      path: "/admin/widgets",
      handlers: { "widget.list": { data: widgetList }, "metric.list": { data: execMetrics }, "dimension.list": { data: dimensions }, ...handlers },
    }),
  ],
});

const meta: Meta = { title: "Pages/Admin Widgets", parameters: { layout: "fullscreen" } };
export default meta;
type Story = StoryObj;

/** Placed widgets, one available from a feed, with the manage actions. */
export const Default: Story = page({});
/** Removed widgets shown too. */
export const WithRemoved: Story = page({ "widget.list": { data: widgetListWithRemoved } });
/** Nothing placed yet. */
export const Empty: Story = page({ "widget.list": { data: widgetListEmpty } });
export const Loading: Story = page({ "widget.list": "pending" });
export const Failed: Story = page({ "widget.list": { error: serverError } });
/** A viewer without widget.manage sees the list, no actions. */
export const ReadOnly: Story = page({}, viewOnlyMe);

// The forms on their own, for per-state coverage.
function FormFrame({ children }: { children: React.ReactNode }) {
  return <div style={{ padding: 20, maxWidth: 760 }}>{children}</div>;
}

export const PlaceForm: Story = {
  render: () => (
    <FormFrame>
      <WidgetForm types={types} metrics={execMetrics.metrics.filter((m) => m.metric_code.startsWith("ex_"))} dimensions={dimensions.dimensions.map((d) => ({ value: d.dimension_type, label: d.display_name }))} existingKeys={["revenue"]} onClose={() => {}} onSaved={() => {}} />
    </FormFrame>
  ),
};

/** A breakdown type (bar): the dimension is required, series offered. */
export const PlaceBarForm: Story = {
  render: () => (
    <FormFrame>
      <WidgetForm types={types} metrics={execMetrics.metrics.filter((m) => m.metric_code.startsWith("ex_"))} dimensions={dimensions.dimensions.map((d) => ({ value: d.dimension_type, label: d.display_name }))} existingKeys={[]} prefillKey="casa_region" onClose={() => {}} onSaved={() => {}} />
    </FormFrame>
  ),
};

export const EditForm: Story = {
  render: () => (
    <FormFrame>
      <WidgetForm types={types} metrics={execMetrics.metrics.filter((m) => m.metric_code.startsWith("ex_"))} dimensions={dimensions.dimensions.map((d) => ({ value: d.dimension_type, label: d.display_name }))} existingKeys={[]} existing={placedWidget} onClose={() => {}} onSaved={() => {}} />
    </FormFrame>
  ),
};

export const ThresholdsOverride: Story = {
  render: () => (
    <FormFrame>
      <ThresholdsForm types={types} widget={{ ...placedWidget, widget_type: "gauge", thresholds: { source: "override", basis: "value", bands: [{ label: "Within", threshold: "0" }, { label: "Above", threshold: "0.035" }], note: "Board tolerance 3.5%." } }} onClose={() => {}} onSaved={() => {}} />
    </FormFrame>
  ),
};

export const ThresholdsFromMetric: Story = {
  render: () => (
    <FormFrame>
      <ThresholdsForm types={types} widget={{ ...placedWidget, widget_type: "bullet" }} onClose={() => {}} onSaved={() => {}} />
    </FormFrame>
  ),
};

export const History: Story = {
  render: () => (
    <FormFrame>
      <HistoryPanel widgetKey="revenue" onClose={() => {}} />
    </FormFrame>
  ),
  decorators: [withApp({ me: manageMe, handlers: { "widget.history": { data: history } } })],
};
