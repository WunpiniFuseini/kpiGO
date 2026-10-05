import { useState } from "react";

import type { Input, Output } from "../../api/actions";
import { ApiError, invoke, isProposal } from "../../api/client";
import { useQuery } from "../../api/useAction";
import { AdminBadge, ErrorPanel, Loading, Notice, SelectField, TableSkeleton, TextField } from "../../components";

export type VisibilityRules = Output<"agent.visibility.list">;
export type ViewerVisibility = Output<"agent.preset">["visibility"];
type Product = Input<"agent.visibility.list">["product"];
type AppliesTo = Input<"agent.visibility.set">["applies_to"];
type Scope = Input<"agent.visibility.set">["scope"];
type Message = { tone: "info" | "neg"; text: string } | null;

export const SCOPES: { value: Scope; label: string }[] = [
  { value: "self", label: "Only themselves" },
  { value: "branch", label: "Their branch" },
  { value: "region", label: "Their region" },
  { value: "subtree", label: "Their team (hierarchy)" },
  { value: "all", label: "Everyone (keep open)" },
];

const SCOPE_TEXT: Record<string, string> = { self: "yourself", branch: "your branch", region: "your region", subtree: "your team" };

/** What the reader sees, when a rule narrowed it: said once, above the sections. */
export function VisibilityNote({ visibility }: { visibility: ViewerVisibility }) {
  if (!visibility.restricted) return null;
  const scopes = visibility.scopes.map((s) => SCOPE_TEXT[s] ?? s);
  const what = scopes.length > 1 ? `${scopes.slice(0, -1).join(", ")} and ${scopes[scopes.length - 1]}` : scopes[0];
  const why = visibility.because.map((b) => `${b.applies_to} ${b.applies_code}`).join(", ");
  return (
    <Notice title={`You see ${what}.`} role="status">
      Agent Performance is narrowed for your {why}. Your own figures always show. An Admin sets this under Who sees whom.
    </Notice>
  );
}

/** Quick settings: who sees whom in this module (AP-7). */
export function VisibilityQuick({ product }: { product: Product }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button type="button" className="kg-btn" aria-expanded={open} aria-controls="visibility-quick" onClick={() => setOpen(!open)}>
        Who sees whom <AdminBadge />
      </button>
      {open ? (
        <section id="visibility-quick" className="kg-card" aria-label="Who sees whom" style={{ marginTop: 10 }}>
          <QuickBody product={product} />
        </section>
      ) : null}
    </div>
  );
}

function QuickBody({ product }: { product: Product }) {
  const [data, reload] = useQuery("agent.visibility.list", { product });
  if (data.status === "loading") {
    return (
      <Loading label="Loading who sees whom">
        <TableSkeleton rows={2} columns={3} />
      </Loading>
    );
  }
  if (data.status === "error") return <ErrorPanel error={data.error} retry={reload} what="Who sees whom" />;
  return <VisibilityView rules={data.data} onChanged={reload} />;
}

/** The rules from data: what stories and tests render. */
export function VisibilityView({ rules, onChanged }: { rules: VisibilityRules; onChanged?: () => void }) {
  const [message, setMessage] = useState<Message>(null);
  const [busy, setBusy] = useState(false);
  const [appliesTo, setAppliesTo] = useState<AppliesTo>("role");
  const [code, setCode] = useState("");
  const [scope, setScope] = useState<Scope>("branch");
  const module = rules.product === "agent_service" ? "Service" : "Sales";

  async function act(run: () => Promise<unknown>, done: string) {
    setBusy(true);
    setMessage(null);
    try {
      const out = await run();
      setMessage({ tone: "info", text: isProposal(out) ? out.message : done });
      onChanged?.();
    } catch (e) {
      setMessage({ tone: "neg", text: e instanceof ApiError ? e.message : "That change did not go through." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="kg-stack">
      <p className="kg-cap">
        {rules.rules.length
          ? `${module} is open to everyone with the page, except as below. Where someone matches more than one rule, the widest applies.`
          : `${module} is open: everyone with the page sees every agent. Add a rule to narrow a role or a profile.`}
      </p>
      {rules.rules.length ? (
        <table className="kg-table">
          <caption className="sr-only">Visibility rules</caption>
          <thead>
            <tr>
              <th scope="col">Applies to</th>
              <th scope="col">Sees</th>
              <th scope="col">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {rules.rules.map((r) => (
              <tr key={`${r.applies_to}:${r.applies_code}`}>
                <th scope="row">
                  {r.applies_to === "role" ? "Role" : "Profile"} {r.applies_code}
                </th>
                <td>{SCOPES.find((s) => s.value === r.scope)?.label ?? r.scope}</td>
                <td className="is-num">
                  <button
                    type="button"
                    className="kg-btn"
                    disabled={busy}
                    aria-label={`Remove the rule for ${r.applies_to} ${r.applies_code}`}
                    onClick={() =>
                      void act(
                        () => invoke("agent.visibility.clear", { product: rules.product as Product, applies_to: r.applies_to as AppliesTo, applies_code: r.applies_code }, { allowProposal: true }),
                        `The ${r.applies_to} ${r.applies_code} is back to seeing everyone.`,
                      )
                    }
                  >
                    Remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      <form
        aria-label="Add a visibility rule"
        style={{ display: "flex", gap: 12, alignItems: "flex-end", flexWrap: "wrap" }}
        onSubmit={(e) => {
          e.preventDefault();
          void act(
            () => invoke("agent.visibility.set", { product: rules.product as Product, applies_to: appliesTo, applies_code: code.trim(), scope }, { allowProposal: true }),
            "Rule saved.",
          ).then(() => setCode(""));
        }}
      >
        <div style={{ width: 120 }}>
          <SelectField
            label="Applies to"
            value={appliesTo}
            onChange={(e) => setAppliesTo(e.target.value as AppliesTo)}
            options={[
              { value: "role", label: "Role" },
              { value: "profile", label: "Profile" },
            ]}
          />
        </div>
        <div style={{ width: 170 }}>
          <TextField label={appliesTo === "role" ? "Role code" : "Profile code"} value={code} onChange={(e) => setCode(e.target.value)} />
        </div>
        <div style={{ width: 200 }}>
          <SelectField label="Sees" value={scope} onChange={(e) => setScope(e.target.value as Scope)} options={SCOPES} />
        </div>
        <button type="submit" className="kg-btn kg-btn--primary" disabled={busy || !code.trim()}>
          Save rule
        </button>
      </form>
      {message ? (
        <Notice tone={message.tone} role={message.tone === "neg" ? "alert" : "status"}>
          {message.text}
        </Notice>
      ) : null}
    </div>
  );
}
