import type { Meta, StoryObj } from "@storybook/react-vite";

import { providersNone, providersPassword, providersSso, setupDone, setupNeeded } from "../stories/fixtures";
import { withApp } from "../stories/mockApi";
import { InviteAccept } from "./InviteAccept";
import { Login } from "./Login";
import { OidcCallback } from "./OidcCallback";
import { Setup } from "./Setup";

const meta: Meta = { title: "Pages/Sign-in", parameters: { layout: "fullscreen" } };
export default meta;
type Story = StoryObj;

const login = (handlers: Parameters<typeof withApp>[0]) => ({ render: () => <Login />, decorators: [withApp(handlers)] });

export const Password: Story = login({ handlers: { "auth.providers": { data: providersPassword }, "setup.status": { data: setupDone } } });
export const SingleSignOn: Story = login({ handlers: { "auth.providers": { data: providersSso }, "setup.status": { data: setupDone } } });
export const SsoRefused: Story = login({
  path: "/login?error=sso_refused",
  handlers: { "auth.providers": { data: providersSso }, "setup.status": { data: setupDone } },
});
export const SessionExpired: Story = login({
  session: { status: "signed-out", reason: "expired" },
  handlers: { "auth.providers": { data: providersPassword }, "setup.status": { data: setupDone } },
});
export const FirstRun: Story = login({ handlers: { "auth.providers": { data: providersPassword }, "setup.status": { data: setupNeeded } } });
export const NoMethodConfigured: Story = login({ handlers: { "auth.providers": { data: providersNone }, "setup.status": { data: setupDone } } });
export const Loading: Story = login({ handlers: { "auth.providers": "pending", "setup.status": "pending" } });
export const Unreachable: Story = login({
  handlers: { "auth.providers": { error: { status: 502, error: "http_502", message: "Bad gateway." } }, "setup.status": { data: setupDone } },
});

export const SetupFirstAdmin: Story = { render: () => <Setup />, decorators: [withApp({ handlers: { "setup.status": { data: setupNeeded } } })] };
export const SetupNoToken: Story = {
  render: () => <Setup />,
  decorators: [withApp({ handlers: { "setup.status": { data: { ...setupNeeded, setup_token_configured: false } } } })],
};
export const InviteSetPassword: Story = { render: () => <InviteAccept />, decorators: [withApp({ path: "/invite?token=abcdefghijklmnop" })] };
export const InviteLinkBroken: Story = { render: () => <InviteAccept />, decorators: [withApp({ path: "/invite" })] };
export const OidcCompleting: Story = {
  render: () => <OidcCallback />,
  decorators: [withApp({ path: "/auth/callback?code=abc&state=xyz", handlers: { "auth.oidc.complete": "pending" } })],
};
export const OidcFailed: Story = {
  render: () => <OidcCallback />,
  decorators: [withApp({ path: "/auth/callback?error=access_denied&error_description=The+user+cancelled+sign-in." })],
};
