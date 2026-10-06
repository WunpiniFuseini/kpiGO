import type { Meta, StoryObj } from "@storybook/react-vite";

import { EmptyState } from "../../components/EmptyState";
import {
  barData,
  bulletData,
  drilledBarData,
  emptyData,
  funnelData,
  gaugeData,
  kpiData,
  lineData,
  pendingData,
  pieData,
  rankedData,
  tableData,
} from "../../stories/executiveFixtures";
import { WidgetBody } from "./renderers";
import type { WidgetData } from "./widgetData";

/** One widget drawn on the card it sits on, as on the dashboard. */
function Tile({ data }: { data: WidgetData }) {
  return (
    <div style={{ background: "var(--canvas-exec)", padding: 20 }}>
      <section className="kg-card kg-widget" style={{ maxWidth: 560 }} aria-label={data.title}>
        <div className="kg-row">
          <h3 className="kg-section">{data.title}</h3>
        </div>
        <div className="kg-widget__body">
          {data.empty ? (
            <EmptyState kind="none" title="Nothing to show here yet" headingLevel={3}>
              {data.empty}
            </EmptyState>
          ) : (
            <WidgetBody data={data} onDrill={() => {}} />
          )}
        </div>
      </section>
    </div>
  );
}

const meta: Meta<typeof Tile> = { title: "Executive/Widgets", component: Tile, parameters: { layout: "fullscreen" } };
export default meta;
type Story = StoryObj<typeof Tile>;

/** Figure, delta against the prior period, target caption. */
export const KpiCard: Story = { args: { data: kpiData } };
/** Each metric's actual bar against its target marker, coloured by standing. */
export const Bullet: Story = { args: { data: bulletData } };
/** A single value on an arc, coloured by its band. */
export const Gauge: Story = { args: { data: gaugeData } };
/** A metric rolled up across regions, actual beside target. */
export const Bar: Story = { args: { data: barData } };
/** One metric across its comparison periods, with the target as a dashed line. */
export const Line: Story = { args: { data: lineData } };
/** Composition of a whole across members. */
export const Pie: Story = { args: { data: pieData } };
/** Members ordered by value, with pace against target. */
export const RankedList: Story = { args: { data: rankedData } };
/** Members by metrics, each cell with its RAG standing. */
export const Table: Story = { args: { data: tableData } };
/** Ordered stages with drop-off from the first. */
export const Funnel: Story = { args: { data: funnelData } };
/** A breakdown drilled into a region, with the breadcrumb (page provides the crumb UI). */
export const Drilled: Story = { args: { data: drilledBarData } };
/** The server says why there is nothing: the grant the viewer lacks. */
export const Empty: Story = { args: { data: emptyData } };
/** A campaign result whose value flow is wired in a later step. */
export const Pending: Story = { args: { data: pendingData } };
