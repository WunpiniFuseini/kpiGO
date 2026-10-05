import type { Meta, StoryObj } from "@storybook/react-vite";

import { GradeBanner, type Band } from "./GradeBanner";
import { GradePill } from "./GradePill";

const bands: Band[] = [
  { label: "Needs Focus", from: 0, tone: 1 },
  { label: "Gaining Momentum", from: 70, tone: 2 },
  { label: "On Target", from: 85, tone: 3 },
  { label: "Exemplary", from: 100, tone: 4 },
];

const meta: Meta<typeof GradeBanner> = {
  title: "Primitives/GradeBanner",
  component: GradeBanner,
  args: { bands, periodLabel: "October 2026" },
};
export default meta;
type Story = StoryObj<typeof GradeBanner>;

export const NeedsFocus: Story = { args: { current: 0, score: 61.4 } };
export const GainingMomentum: Story = { args: { current: 1, score: 78.2 } };
export const OnTarget: Story = { args: { current: 2, score: 91.7 } };
export const Exemplary: Story = { args: { current: 3, score: 104.3 } };
export const Provisional: Story = { args: { current: 2, score: 88.0, provisional: true } };
export const Restated: Story = { args: { current: 1, score: 79.9, restatedOn: "12 Nov 2026" } };
export const NotGradedYet: Story = {
  args: { current: null, score: null, emptyReason: "Deposits data hasn't arrived from the finance feed, due 06:00 daily." },
};
/** A client with three bands gets a banner that fits; labels come from their configuration. */
export const ThreeBands: Story = {
  args: {
    bands: [
      { label: "Below", from: 0, tone: 1 },
      { label: "Meets", from: 90, tone: 3 },
      { label: "Exceeds", from: 110, tone: 4 },
    ],
    current: 1,
    score: 96.5,
  },
};
export const Pills: Story = {
  render: () => (
    <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
      {bands.map((b) => (
        <GradePill key={b.label} tone={b.tone} label={b.label} />
      ))}
    </div>
  ),
};
