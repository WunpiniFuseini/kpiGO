import type { Meta, StoryObj } from "@storybook/react-vite";

import { AdminBadge, Chip, DeltaChip } from "./Chip";

const meta: Meta<typeof DeltaChip> = { title: "Primitives/Chip", component: DeltaChip };
export default meta;
type Story = StoryObj<typeof DeltaChip>;

export const Improved: Story = { args: { value: 4.2 } };
export const Declined: Story = { args: { value: -3.1 } };
export const Unchanged: Story = { args: { value: 0 } };
export const NoPriorPeriod: Story = { args: { value: null } };
/** A fall in turnaround time is an improvement: the colour follows good or bad, the arrow follows the number. */
export const LowerIsBetter: Story = { args: { value: -12, higherIsBetter: false } };
export const StateChips: Story = {
  render: () => (
    <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
      <Chip tone="warn">Provisional</Chip>
      <Chip tone="info">you</Chip>
      <Chip tone="flat">Draft</Chip>
      <span>
        Product lines
        <AdminBadge />
      </span>
    </div>
  ),
};
