import type { Meta, StoryObj } from "@storybook/react-vite";
import { Route, Routes } from "react-router-dom";

import { AppShell } from "../../shell/AppShell";
import { useSession, type Me } from "../../session/Session";
import { adminMe, apiTokens, mcpOff, mcpOn, noApiTokens, serverError, tokenIssued } from "../../stories/fixtures";
import { withApp, type Handlers } from "../../stories/mockApi";
import { IntegrationsPage, IssuedToken } from "./Integrations";

function InShell({ children }: { children: React.ReactNode }) {
  const { state } = useSession();
  if (state.status !== "signed-in") return null;
  return (
    <AppShell me={state.me} onSignOut={() => {}}>
      <Routes>
        <Route path="*" element={children} />
      </Routes>
    </AppShell>
  );
}

const page = (element: React.ReactNode, { me = adminMe, handlers = {} }: { me?: Me; handlers?: Handlers }) => ({
  render: () => <InShell>{element}</InShell>,
  decorators: [withApp({ me, path: "/admin/integrations", handlers })],
});

const meta: Meta = { title: "Pages/Integrations", parameters: { layout: "fullscreen" } };
export default meta;
type Story = StoryObj;

const ready: Handlers = { "mcp.status": { data: mcpOn }, "apitoken.list": { data: apiTokens } };

export const Integrations: Story = page(<IntegrationsPage />, { handlers: ready });
/** The install has switched MCP off: the page says so and who turns it on. */
export const McpOff: Story = page(<IntegrationsPage />, { handlers: { ...ready, "mcp.status": { data: mcpOff } } });
/** No token yet: the table says how to issue one. */
export const NoTokens: Story = page(<IntegrationsPage />, { handlers: { ...ready, "apitoken.list": { data: noApiTokens } } });
/** Someone who may see tokens but not issue or revoke them. */
export const ViewOnly: Story = page(<IntegrationsPage />, {
  me: { ...adminMe, permissions: adminMe.permissions.filter((p) => p !== "apitoken.manage") },
  handlers: ready,
});
export const IntegrationsLoading: Story = page(<IntegrationsPage />, { handlers: { "mcp.status": "pending", "apitoken.list": "pending" } });
export const IntegrationsError: Story = page(<IntegrationsPage />, {
  handlers: { "mcp.status": { error: serverError }, "apitoken.list": { error: serverError } },
});
/** Straight after issuing: the token, once, with ready-to-paste client settings. */
export const TokenIssued: Story = page(<IssuedToken issued={tokenIssued} endpoint={mcpOn.endpoint_url} onClose={() => {}} />, {
  handlers: ready,
});
