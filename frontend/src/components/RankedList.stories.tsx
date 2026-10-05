import type { Meta, StoryObj } from "@storybook/react-vite";

import { EmptyState } from "./EmptyState";
import { RankedList, type RankedItem } from "./RankedList";

const items: RankedItem[] = [
  { id: "1", name: "Abena Ofori", sub: "Accra Central · Sales", value: "GHS 1.92M", pace: 1.18, tone: 4, band: "Exemplary" },
  { id: "2", name: "Kwesi Mensah", sub: "Kumasi · Sales", value: "GHS 1.61M", pace: 1.02, tone: 3, band: "On Target" },
  { id: "3", name: "Kofi Boateng", sub: "Accra Central · Sales", value: "GHS 1.40M", pace: 0.91, tone: 3, band: "On Target", isYou: true },
  { id: "4", name: "Esi Quaye", sub: "Takoradi · Sales", value: "GHS 1.12M", pace: 0.78, tone: 2, band: "Gaining Momentum" },
  { id: "5", name: "Yaw Darko", sub: "Tamale · Sales", value: "GHS 0.74M", pace: 0.52, tone: 1, band: "Needs Focus" },
];

const meta: Meta<typeof RankedList> = {
  title: "Primitives/RankedList",
  component: RankedList,
  args: { items, label: "Leaderboard, deposits, October 2026" },
  decorators: [(Story) => <div className="kg-card" style={{ maxWidth: 640 }}>{Story()}</div>],
};
export default meta;
type Story = StoryObj<typeof RankedList>;

export const Leaderboard: Story = {};
export const Empty: Story = {
  render: () => (
    <EmptyState kind="none" title="No agents match these filters">
      Nobody in Takoradi has a sales profile this month.
    </EmptyState>
  ),
};
