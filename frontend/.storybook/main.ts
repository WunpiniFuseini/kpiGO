import type { StorybookConfig } from "@storybook/react-vite";

// Storybook is the contract (Design Brief §12): a component is done when every
// state, including quarantined, stale and no-access, has a story here.
const config: StorybookConfig = {
  stories: ["../src/**/*.stories.@(ts|tsx)"],
  addons: ["@storybook/addon-a11y", "@storybook/addon-docs"],
  framework: { name: "@storybook/react-vite", options: {} },
  core: { disableTelemetry: true },
};
export default config;
