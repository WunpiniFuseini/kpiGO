import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";

import { CheckboxGroup, SelectField, TextField } from "./Field";

const meta: Meta = { title: "Primitives/Fields", decorators: [(Story) => <div className="kg-form" style={{ maxWidth: 420 }}>{Story()}</div>] };
export default meta;
type Story = StoryObj;

export const Text: Story = { render: () => <TextField label="Work email" type="email" hint="The address your invitation went to." /> };
export const WithError: Story = {
  render: () => <TextField label="Confirm the password" type="password" defaultValue="short" error="The two passwords do not match." />,
};
export const Select: Story = {
  render: () => (
    <SelectField
      label="Direction"
      options={[
        { value: "higher_is_better", label: "Higher is better" },
        { value: "lower_is_better", label: "Lower is better" },
      ]}
    />
  ),
};
export const Checkboxes: Story = {
  render: function Render() {
    const [value, setValue] = useState(["scorecards"]);
    return (
      <CheckboxGroup
        legend="Used by"
        value={value}
        onChange={setValue}
        options={[
          { value: "scorecards", label: "Scorecards" },
          { value: "executive", label: "Executive" },
          { value: "campaign", label: "Campaign Manager" },
        ]}
        error={value.length ? null : "Choose at least one product."}
      />
    );
  },
};
export const Buttons: Story = {
  render: () => (
    <div style={{ display: "flex", gap: 8 }}>
      <button type="button" className="kg-btn kg-btn--primary">
        Primary
      </button>
      <button type="button" className="kg-btn">
        Secondary
      </button>
      <button type="button" className="kg-btn kg-btn--primary" disabled>
        Disabled
      </button>
    </div>
  ),
};
