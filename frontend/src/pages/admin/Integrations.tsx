import { useState, type FormEvent } from "react";

import type { Output } from "../../api/actions";
import { ApiError, invoke } from "../../api/client";
import { useQuery } from "../../api/useAction";
import {
  CardSkeleton,
  Chip,
  DataTable,
  EmptyState,
  ErrorPanel,
  Loading,
  Notice,
  SelectField,
  TableSkeleton,
  TextField,
} from "../../components";
import { formatDateTime } from "../../lib/format";
import { Page } from "../../shell/AppShell";
import { useMe } from "../../session/Session";

type McpStatus = Output<"mcp.status">;
type Token = Output<"apitoken.list">["tokens"][number];
type Issued = Output<"apitoken.issue">;

const TOKEN_TONE = { active: "up", revoked: "flat" } as const;

/** The address an MCP client connects to: the configured public URL, else where kpiGo was opened. */
export function endpointOf(status: McpStatus, origin: string = window.location.origin): string {
  return status.endpoint_url ?? `${origin}${status.endpoint_path}`;
}

/**
 * Administer → Integrations (R6): connect a client's own AI assistant over MCP, and the API
 * tokens that assistant (or any read integration) signs in with.
 */
export function IntegrationsPage() {
  const me = useMe();
  const canManage = me.permissions.includes("apitoken.manage");
  const [status, reloadStatus] = useQuery("mcp.status", {});
  const [tokens, reloadTokens] = useQuery("apitoken.list", {});
  const [issuing, setIssuing] = useState(false);

  return (
    <Page
      title="Integrations"
      actions={
        canManage ? (
          <button type="button" className="kg-btn kg-btn--primary" onClick={() => setIssuing(true)} aria-expanded={issuing}>
            Issue a token
          </button>
        ) : null
      }
    >
      {status.status === "loading" ? (
        <Loading label="Checking the MCP endpoint">
          <CardSkeleton />
        </Loading>
      ) : status.status === "error" ? (
        <ErrorPanel error={status.error} retry={reloadStatus} what="MCP status" />
      ) : (
        <McpCard status={status.data} />
      )}
      {issuing ? (
        <IssueForm
          endpoint={status.status === "ready" ? endpointOf(status.data) : null}
          onClose={() => setIssuing(false)}
          onIssued={reloadTokens}
        />
      ) : null}
      <section className="kg-card" aria-labelledby="tokens-heading">
        <h2 id="tokens-heading" className="kg-section" style={{ marginBottom: 6 }}>
          Your API tokens
        </h2>
        <p className="kg-cap" style={{ marginBottom: 14 }}>
          A token signs in as you: it carries your permissions and visibility, and reaches read-only actions only.
        </p>
        {tokens.status === "loading" ? (
          <Loading label="Loading tokens">
            <TableSkeleton rows={3} columns={5} />
          </Loading>
        ) : tokens.status === "error" ? (
          <ErrorPanel error={tokens.error} retry={reloadTokens} what="API tokens" />
        ) : (
          <TokenTable tokens={tokens.data.tokens} canManage={canManage} onChanged={reloadTokens} />
        )}
      </section>
    </Page>
  );
}

export function McpCard({ status }: { status: McpStatus }) {
  const endpoint = endpointOf(status);
  return (
    <section className="kg-card kg-stack" aria-labelledby="mcp-heading">
      <div className="kg-row" style={{ justifyContent: "flex-start", gap: 10 }}>
        <h2 id="mcp-heading" className="kg-section">
          AI assistants (MCP)
        </h2>
        <Chip tone={status.enabled ? "up" : "flat"}>{status.enabled ? "On" : "Off"}</Chip>
      </div>
      <p style={{ color: "var(--ink-2)" }}>{status.message}</p>
      {status.enabled ? (
        <>
          <div className="kg-field">
            <span className="kg-eyebrow">Endpoint</span>
            <span className="kg-mono" style={MONO_BOX}>
              {endpoint}
            </span>
            <span className="kg-hint">Streamable HTTP · protocol {status.protocol_versions.join(", ")}</span>
          </div>
          {status.tools.length ? (
            <details>
              <summary>
                A token you issue offers {status.tools.length} read-only {status.tools.length === 1 ? "tool" : "tools"}
              </summary>
              <DataTable
                caption="Tools"
                captionHidden
                columns={[
                  { key: "name", header: "Tool", render: (t) => <span className="kg-mono">{t.name}</span> },
                  { key: "summary", header: "What it reads", render: (t) => t.summary },
                ]}
                rows={status.tools}
                rowKey={(t) => t.name}
                empty={null}
              />
            </details>
          ) : (
            <p className="kg-cap">
              Your account holds no read permission an assistant could use, so a token you issue would offer no tools.
            </p>
          )}
        </>
      ) : null}
    </section>
  );
}

export function TokenTable({ tokens, canManage, onChanged }: { tokens: Token[]; canManage: boolean; onChanged: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const revoke = async (name: string) => {
    setBusy(name);
    setError(null);
    try {
      await invoke("apitoken.revoke", { name });
      onChanged();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The token could not be revoked.");
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      {error ? (
        <p role="alert" style={{ color: "var(--neg-ink)", fontSize: 13, marginBottom: 8 }}>
          {error}
        </p>
      ) : null}
      <DataTable
        caption="API tokens"
        captionHidden
        columns={[
          {
            key: "name",
            header: "Name",
            render: (t) => (
              <>
                {t.name}
                <div className="kg-cap kg-mono">{t.prefix}…</div>
              </>
            ),
          },
          { key: "status", header: "Status", render: (t) => <Chip tone={TOKEN_TONE[t.status as keyof typeof TOKEN_TONE] ?? "flat"}>{t.status}</Chip> },
          { key: "expires", header: "Expires", render: (t) => (t.expires_at ? formatDateTime(t.expires_at) : <span className="kg-cap">Never</span>) },
          { key: "used", header: "Last used", render: (t) => (t.last_used_at ? formatDateTime(t.last_used_at) : <span className="kg-cap">Never</span>) },
          {
            key: "revoke",
            header: "Revoke",
            render: (t) =>
              canManage && t.status === "active" ? (
                <button
                  type="button"
                  className="kg-btn"
                  aria-label={`Revoke the token ${t.name}`}
                  onClick={() => revoke(t.name)}
                  disabled={busy !== null}
                >
                  {busy === t.name ? "Revoking…" : "Revoke"}
                </button>
              ) : null,
          },
        ]}
        rows={tokens}
        rowKey={(t) => t.name}
        empty={
          <EmptyState kind="none" title="No API tokens yet">
            {canManage ? "Issue one with Issue a token to connect an assistant or a reporting tool." : "An Admin issues tokens here."}
          </EmptyState>
        }
      />
    </>
  );
}

const MONO_BOX = { padding: 10, background: "var(--sunken)", border: "1px solid var(--line)", borderRadius: 8, overflowWrap: "anywhere" } as const;

/** Ready-to-paste client settings. The token appears in them only on the screen that issued it. */
export function clientSnippets(endpoint: string, secret: string): { label: string; hint: string; text: string }[] {
  return [
    {
      label: "Any MCP client",
      hint: "Add a remote (Streamable HTTP) server with this address and header.",
      text: `URL: ${endpoint}\nHeader: Authorization: Bearer ${secret}`,
    },
    {
      label: "VS Code / GitHub Copilot (mcp.json)",
      hint: "In the workspace's .vscode/mcp.json, or the user-level MCP settings.",
      text: JSON.stringify(
        { servers: { kpigo: { type: "http", url: endpoint, headers: { Authorization: `Bearer ${secret}` } } } },
        null,
        2,
      ),
    },
    {
      label: "Claude Desktop (claude_desktop_config.json)",
      hint: "Uses the mcp-remote bridge, which needs Node.js on the desktop.",
      text: JSON.stringify(
        {
          mcpServers: {
            kpigo: {
              command: "npx",
              args: ["mcp-remote", endpoint, "--header", "Authorization:${KPIGO_AUTH}"],
              env: { KPIGO_AUTH: `Bearer ${secret}` },
            },
          },
        },
        null,
        2,
      ),
    },
  ];
}

function IssueForm({ endpoint, onClose, onIssued }: { endpoint: string | null; onClose: () => void; onIssued: () => void }) {
  const [form, setForm] = useState({ name: "", days: "90" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [issued, setIssued] = useState<Issued | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const out = await invoke("apitoken.issue", { name: form.name, expires_in_days: form.days ? Number(form.days) : null });
      setIssued(out);
      onIssued();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The token could not be issued.");
    } finally {
      setBusy(false);
    }
  };

  if (issued) return <IssuedToken issued={issued} endpoint={endpoint} onClose={onClose} />;

  return (
    <section className="kg-card" aria-labelledby="issue-heading">
      <h2 id="issue-heading" className="kg-section" style={{ marginBottom: 14 }}>
        Issue an API token
      </h2>
      <form className="kg-form" onSubmit={submit} noValidate>
        <div className="kg-form-row">
          <TextField
            label="Name"
            hint="What uses it, e.g. claude-desktop or reporting-etl."
            required
            maxLength={64}
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
          <SelectField
            label="Expires"
            value={form.days}
            onChange={(e) => setForm({ ...form, days: e.target.value })}
            options={[
              { value: "30", label: "In 30 days" },
              { value: "90", label: "In 90 days" },
              { value: "365", label: "In a year" },
              { value: "", label: "Never" },
            ]}
          />
        </div>
        {error ? (
          <p role="alert" style={{ color: "var(--neg-ink)", fontSize: 13 }}>
            {error}
          </p>
        ) : null}
        <div style={{ display: "flex", gap: 8 }}>
          <button type="submit" className="kg-btn kg-btn--primary" disabled={busy || !form.name.trim()}>
            {busy ? "Issuing…" : "Issue"}
          </button>
          <button type="button" className="kg-btn" onClick={onClose}>
            Cancel
          </button>
        </div>
      </form>
    </section>
  );
}

export function IssuedToken({ issued, endpoint, onClose }: { issued: Issued; endpoint: string | null; onClose: () => void }) {
  return (
    <section className="kg-card kg-stack" aria-label="Token issued">
      <h2 className="kg-section">Token “{issued.token.name}” is ready</h2>
      <Notice tone="warn" title="Copy it now.">
        {issued.message}
      </Notice>
      <div className="kg-field">
        <span className="kg-eyebrow">Token (shown once)</span>
        <span className="kg-mono" style={MONO_BOX}>
          {issued.secret}
        </span>
      </div>
      {endpoint ? (
        <>
          <h3 className="kg-eyebrow">Connect an assistant</h3>
          {clientSnippets(endpoint, issued.secret).map((s) => (
            <div className="kg-field" key={s.label}>
              <span className="kg-eyebrow">{s.label}</span>
              <pre className="kg-mono" style={{ ...MONO_BOX, margin: 0, whiteSpace: "pre-wrap" }}>
                {s.text}
              </pre>
              <span className="kg-hint">{s.hint}</span>
            </div>
          ))}
        </>
      ) : null}
      <div>
        <button type="button" className="kg-btn" onClick={onClose}>
          Done
        </button>
      </div>
    </section>
  );
}
