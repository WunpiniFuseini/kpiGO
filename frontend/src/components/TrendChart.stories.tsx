import type { Meta, StoryObj } from "@storybook/react-vite";
import { expect, userEvent, within } from "storybook/test";

import { EmptyState } from "./EmptyState";
import { CardSkeleton } from "./Skeleton";
import { TrendChart } from "./TrendChart";

const points = ["May", "Jun", "Jul", "Aug", "Sep", "Oct"].map((label, i) => ({ label, value: [9.1, 9.8, 10.2, 10.0, 11.3, 12.4][i] }));
const fmt = (v: number) => `${v.toFixed(1)}M`;

const meta: Meta<typeof TrendChart> = {
  title: "Primitives/TrendChart",
  component: TrendChart,
  args: { title: "Deposits, GHS", points, target: 12, format: fmt },
};
export default meta;
type Story = StoryObj<typeof TrendChart>;

export const WithTarget: Story = {};
export const NoTarget: Story = { args: { target: null } };
/** A month the feed never delivered is a gap, not a zero (absent is not zero). */
export const MissingPeriod: Story = {
  args: { points: points.map((p, i) => (i === 3 ? { ...p, value: null } : p)) },
};
export const TableView: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByRole("button", { name: "Table" }));
    await expect(canvas.getByRole("table")).toBeInTheDocument();
  },
};
export const Loading: Story = { render: () => <CardSkeleton /> };
export const AwaitingData: Story = {
  render: () => (
    <EmptyState kind="awaiting" title="No deposits history yet">
      The finance deposits feed has not loaded a month yet. It is due on the 3rd working day.
    </EmptyState>
  ),
};
