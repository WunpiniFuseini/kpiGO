import { render, screen } from "@testing-library/react";

import { clientSnippets, endpointOf, IssuedToken, McpCard, TokenTable } from "../pages/admin/Integrations";
import { apiTokens, mcpOff, mcpOn, tokenIssued } from "../stories/fixtures";

describe("integrations", () => {
  it("connects to the public URL when set, else to where kpiGo was opened", () => {
    expect(endpointOf(mcpOn, "https://ignored")).toBe("https://kpigo.bank.example/api/mcp");
    expect(endpointOf({ ...mcpOn, endpoint_url: null }, "https://kpigo.local")).toBe("https://kpigo.local/api/mcp");
  });

  it("says the endpoint is off and shows no address", () => {
    render(<McpCard status={mcpOff} />);
    expect(screen.getByText("Off")).toBeInTheDocument();
    expect(screen.queryByText("Endpoint")).not.toBeInTheDocument();
    expect(screen.getByText(/KPIGO_MCP_ENABLED=0/)).toBeInTheDocument();
  });

  it("offers revoke only on active tokens, and only to a manager", () => {
    const { rerender } = render(<TokenTable tokens={apiTokens.tokens} canManage onChanged={() => {}} />);
    expect(screen.getByRole("button", { name: "Revoke the token claude-desktop" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Revoke the token reporting-etl" })).not.toBeInTheDocument();
    rerender(<TokenTable tokens={apiTokens.tokens} canManage={false} onChanged={() => {}} />);
    expect(screen.queryByRole("button", { name: /^Revoke/ })).not.toBeInTheDocument();
  });

  it("says why an empty token list is empty", () => {
    render(<TokenTable tokens={[]} canManage onChanged={() => {}} />);
    expect(screen.getByText("No API tokens yet")).toBeInTheDocument();
  });

  it("puts the new token into every client snippet as a bearer header", () => {
    for (const s of clientSnippets("https://k/api/mcp", "kpigo_secret")) {
      expect(s.text).toContain("https://k/api/mcp");
      expect(s.text).toContain("Bearer kpigo_secret");
    }
    render(<IssuedToken issued={tokenIssued} endpoint="https://k/api/mcp" onClose={() => {}} />);
    expect(screen.getByText("Token (shown once)")).toBeInTheDocument();
  });
});
