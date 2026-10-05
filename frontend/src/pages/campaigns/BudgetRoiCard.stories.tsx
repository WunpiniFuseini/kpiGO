import type { Meta, StoryObj } from "@storybook/react-vite";

import { eventValue, eventValueContaminated, eventValueNothingFed, eventValueSmallControl } from "../../stories/campaignFixtures";
import { BudgetRoiCard } from "./Detail";

const meta: Meta<typeof BudgetRoiCard> = {
  title: "Campaigns/BudgetRoiCard",
  component: BudgetRoiCard,
  args: { value: eventValue(), basis: "incremental", name: "October SMS wave" },
  decorators: [
    (Story) => (
      <div className="kg-card" style={{ maxWidth: 760 }}>
        <Story />
      </div>
    ),
  ],
};
export default meta;
type Story = StoryObj<typeof BudgetRoiCard>;

/** Incremental leads, gross beside it, with the control group's lift. */
export const Measured: Story = {};
/** The org reports on gross: gross leads, incremental beside it. */
export const GrossBasis: Story = { args: { basis: "gross", value: eventValue({ roi: "166.3000" }) } };
/** Another event reached customers in the baseline window: incremental reads "—" with the reason. */
export const Contaminated: Story = { args: { value: eventValueContaminated } };
/** The feed's history starts inside the baseline window. */
export const ShortHistory: Story = { args: { value: eventValue({ baseline: null, incremental: null, withheld: "history_too_short", roi: null, roi_reason: "history_too_short" }) } };
/** Fewer than 30 held out: the lift is shown as indicative. */
export const SmallControl: Story = { args: { value: eventValueSmallControl } };
/** Value credited in another currency than the budget's. */
export const OtherCurrency: Story = { args: { value: eventValue({ other_currencies: [{ currency: "USD", amount: "1250.0000" }], utilisation: "0.8400", spend_to_date: "21000.00" }) } };
/** No outcomes loaded yet. */
export const NothingFed: Story = { args: { value: eventValueNothingFed } };
