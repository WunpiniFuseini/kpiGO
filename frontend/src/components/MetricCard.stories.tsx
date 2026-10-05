import type { Meta, StoryObj } from "@storybook/react-vite";

import { MetricCard } from "./MetricCard";

const meta: Meta<typeof MetricCard> = {
  title: "Primitives/MetricCard",
  component: MetricCard,
  args: { label: "Deposits", value: "GHS 12.4M", target: "GHS 13.0M", delta: 4.2, asOf: "2026-10-04T06:00:00Z", feed: "finance_deposits_daily" },
  decorators: [(Story) => <div style={{ maxWidth: 300 }}>{Story()}</div>],
};
export default meta;
type Story = StoryObj<typeof MetricCard>;

export const Ready: Story = {};
export const WithTrend: Story = { args: { trend: [9.1, 9.8, 10.2, 10.0, 11.3, 11.9, 12.4] } };
export const Declined: Story = { args: { delta: -2.7, value: "GHS 11.1M" } };
export const LowerIsBetter: Story = { args: { label: "Complaint turnaround", value: "18.5 h", target: "24 h", delta: -8, higherIsBetter: false } };
export const Small: Story = { args: { small: true } };
export const Loading: Story = { args: { state: "loading" } };
export const Stale: Story = { args: { state: "stale", asOf: "2026-10-01T06:00:00Z" } };
export const AwaitingData: Story = {
  args: { state: "awaiting", value: null, reason: "Deposits data hasn't arrived from the finance feed, due 06:00 daily." },
};
export const Quarantined: Story = { args: { state: "quarantined", asOf: "2026-10-03T06:00:00Z" } };
export const NoAccess: Story = { args: { state: "no-access", reason: "You have no Executive data-scope grant. Ask an Admin." } };
