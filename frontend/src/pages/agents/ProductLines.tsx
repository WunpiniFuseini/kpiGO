import { useState, type FormEvent } from "react";

import type { Output } from "../../api/actions";
import { ApiError, invoke, isProposal } from "../../api/client";
import { useQuery } from "../../api/useAction";
import { AdminBadge, Chip, EmptyState, ErrorPanel, Loading, Notice, SelectField, TableSkeleton, TextField } from "../../components";
import { formatDate } from "../../lib/format";
import { Page } from "../../shell/AppShell";
import { useMe } from "../../session/Session";

export type Registry = Output<"product_line.registry">;
type Line = Registry["in_matrix"][number];
type Message = { tone: "info" | "neg"; text: string } | null;

const NEW_GROUP = "";

/** Administer → Product lines: the full surface (Scope §8.5). */
export function ProductLinesPage() {
  const me = useMe();
  const [data, reload] = useQuery("product_line.registry", {});
  return (
    <Page title="Product lines">
      <section className="kg-card" aria-labelledby="lines-heading">
        <div className="kg-sechead">
          <div>
            <h2 id="lines-heading" className="kg-section">
              Product lines and groups
            </h2>
            <p className="kg-cap">Lines come from the actuals feed: a new product_line_code with data appears here as available. Name it, group it and switch it on, and the matrix shows it for everyone.</p>
          </div>
        </div>
        {data.status === "loading" ? (
          <Loading label="Loading product lines">
            <TableSkeleton rows={4} columns={4} />
          </Loading>
        ) : data.status === "error" ? (
          <ErrorPanel error={data.error} retry={reload} what="Product lines" />
        ) : (
          <ProductLinesView registry={data.data} canEdit={me.permissions.includes("product_line.manage")} full onChanged={reload} />
        )}
      </section>
    </Page>
  );
}

/** Quick settings on the Agent Performance page: an Admin-only disclosure. */
export function ProductLinesQuick() {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button type="button" className="kg-btn" aria-expanded={open} aria-controls="product-lines-quick" onClick={() => setOpen(!open)}>
        Product lines <AdminBadge />
      </button>
      {open ? (
        <section id="product-lines-quick" className="kg-card" aria-label="Product lines quick settings" style={{ marginTop: 10 }}>
          <QuickBody />
        </section>
      ) : null}
    </div>
  );
}

function QuickBody() {
  const [data, reload] = useQuery("product_line.registry", {});
  if (data.status === "loading") {
    return (
      <Loading label="Loading product lines">
        <TableSkeleton rows={3} columns={3} />
      </Loading>
    );
  }
  if (data.status === "error") return <ErrorPanel error={data.error} retry={reload} what="Product lines" />;
  return <ProductLinesView registry={data.data} canEdit onChanged={reload} />;
}

/** The registry from data: what stories and tests render. */
export function ProductLinesView({ registry, canEdit, full = false, onChanged }: { registry: Registry; canEdit: boolean; full?: boolean; onChanged?: () => void }) {
  const [message, setMessage] = useState<Message>(null);
  const [busy, setBusy] = useState(false);

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

  const lines = registry.in_matrix;
  const groupName = (code: string | null) => registry.groups.find((g) => g.code === code)?.display_name ?? code ?? "–";
  const move = (i: number, by: number) => {
    const codes = lines.map((l) => l.code);
    const [item] = codes.splice(i, 1);
    codes.splice(i + by, 0, item);
    void act(() => invoke("product_line.reorder", { codes }), "Order saved.");
  };

  return (
    <div className="kg-stack">
      {message ? (
        <Notice tone={message.tone} role={message.tone === "neg" ? "alert" : "status"}>
          {message.text}
        </Notice>
      ) : null}
      {registry.suggest_grouped ? (
        <p className="kg-cap" role="note">
          {lines.length} lines are in the matrix. The grouped view will be the readable one; check each line sits in a group that reads well collapsed.
        </p>
      ) : null}

      <h3 className="kg-eyebrow">In the matrix</h3>
      {lines.length ? (
        <div className="kg-table-wrap">
          <table className="kg-table">
            <caption className="sr-only">Product lines in the matrix, in the order it shows them.</caption>
            <thead>
              <tr>
                <th scope="col">Line</th>
                <th scope="col">Group</th>
                <th scope="col">Since</th>
                {canEdit ? <th scope="col">Change</th> : null}
              </tr>
            </thead>
            <tbody>
              {lines.map((l, i) => (
                <tr key={l.code}>
                  <th scope="row">
                    {l.display_name}
                    <span className="kg-msub">
                      {l.code}
                      {l.rag_green ? ` · RAG ${pct(l.rag_green)} / ${pct(l.rag_amber)}` : ""}
                      {l.effective_to ? ` · retiring ${formatDate(l.effective_to)}` : ""}
                    </span>
                  </th>
                  <td>{groupName(l.group_code)}</td>
                  <td>{formatDate(l.effective_from)}</td>
                  {canEdit ? (
                    <td>
                      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                        <button type="button" className="kg-btn" disabled={busy || i === 0} onClick={() => move(i, -1)} aria-label={`Move ${l.display_name} up`}>
                          Up
                        </button>
                        <button type="button" className="kg-btn" disabled={busy || i === lines.length - 1} onClick={() => move(i, 1)} aria-label={`Move ${l.display_name} down`}>
                          Down
                        </button>
                        {l.status === "active" ? (
                          <button
                            type="button"
                            className="kg-btn"
                            disabled={busy}
                            onClick={() => void act(() => invoke("product_line.retire", { code: l.code }, { allowProposal: true }), `${l.display_name} is switched off. Earlier periods still show it.`)}
                          >
                            Switch off
                          </button>
                        ) : null}
                      </div>
                      {full ? <LineEdits line={l} registry={registry} busy={busy} act={act} /> : null}
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState kind="none" title="No product line is in the matrix yet">
          {registry.available.length ? "Switch on a line the feed made available below." : "The matrix gets its columns from lines the actuals feed carries. Once a load has data for a product_line_code, it appears here to switch on."}
        </EmptyState>
      )}

      <h3 className="kg-eyebrow">Available from the feed</h3>
      {registry.available.length ? (
        <ul className="kg-stack" style={{ listStyle: "none", padding: 0, margin: 0 }}>
          {registry.available.map((l) => (
            <li key={l.code}>
              <Activate line={l} registry={registry} canEdit={canEdit} busy={busy} act={act} />
            </li>
          ))}
        </ul>
      ) : (
        <p className="kg-cap">Nothing new in the feed. A product shows up here after the first load that carries data for it.</p>
      )}

      {full ? <Groups registry={registry} canEdit={canEdit} busy={busy} act={act} /> : null}

      {full && registry.retired.length ? (
        <>
          <h3 className="kg-eyebrow">Retired</h3>
          <p className="kg-cap">
            {registry.retired.map((l) => `${l.display_name} (to ${l.effective_to ? formatDate(l.effective_to) : "–"})`).join(", ")}. Periods before each date still show them.
          </p>
        </>
      ) : null}
    </div>
  );
}

function pct(value: string | null): string {
  return value === null ? "–" : `${Math.round(Number(value) * 100)}%`;
}

type Act = (run: () => Promise<unknown>, done: string) => Promise<void>;

function Activate({ line, registry, canEdit, busy, act }: { line: Line; registry: Registry; canEdit: boolean; busy: boolean; act: Act }) {
  const groups = registry.groups.filter((g) => g.status === "active");
  const [name, setName] = useState(line.display_name);
  const [group, setGroup] = useState(groups.length === 1 ? groups[0].code : NEW_GROUP);
  const detected = line.first_detected_at ? `detected ${formatDate(line.first_detected_at)}` : "detected in a feed";
  const submit = (e: FormEvent) => {
    e.preventDefault();
    void act(
      () => invoke("product_line.activate", { code: line.code, display_name: name, ...(group ? { group_code: group } : {}) }, { allowProposal: true }),
      `${name} is in the matrix for everyone.`,
    );
  };
  if (!canEdit) {
    return (
      <span>
        <b>{line.code}</b> <span className="kg-cap">· {detected}</span>
      </span>
    );
  }
  return (
    <form onSubmit={submit} className="kg-form-row" style={{ display: "flex", gap: 10, alignItems: "flex-end", flexWrap: "wrap" }} aria-label={`Switch on ${line.code}`}>
      <div style={{ minWidth: 140 }}>
        <Chip tone="info">{line.code}</Chip>
        <span className="kg-msub">{detected}</span>
      </div>
      <div style={{ width: 180 }}>
        <TextField label="Name" value={name} required maxLength={120} onChange={(e) => setName(e.target.value)} />
      </div>
      <div style={{ width: 180 }}>
        <SelectField
          label="Group"
          value={group}
          required={groups.length > 1}
          onChange={(e) => setGroup(e.target.value)}
          options={[...(groups.length ? [] : [{ value: NEW_GROUP, label: "Products (new)" }]), ...(groups.length > 1 ? [{ value: NEW_GROUP, label: "Choose a group" }] : []), ...groups.map((g) => ({ value: g.code, label: g.display_name }))]}
        />
      </div>
      <button type="submit" className="kg-btn kg-btn--primary" disabled={busy || !name.trim() || (groups.length > 1 && !group)}>
        Switch on
      </button>
    </form>
  );
}

function LineEdits({ line, registry, busy, act }: { line: Line; registry: Registry; busy: boolean; act: Act }) {
  const others = registry.groups.filter((g) => g.status === "active" && g.code !== line.group_code);
  const [to, setTo] = useState(others[0]?.code ?? "");
  const [from, setFrom] = useState("");
  const [green, setGreen] = useState(line.rag_green ? String(Math.round(Number(line.rag_green) * 100)) : "");
  const [amber, setAmber] = useState(line.rag_amber ? String(Math.round(Number(line.rag_amber) * 100)) : "");
  return (
    <details style={{ marginTop: 8 }}>
      <summary>More for {line.display_name}</summary>
      <div className="kg-stack" style={{ marginTop: 8 }}>
        {others.length ? (
          <form
            style={{ display: "flex", gap: 8, alignItems: "flex-end", flexWrap: "wrap" }}
            onSubmit={(e) => {
              e.preventDefault();
              void act(() => invoke("product_line.move", { code: line.code, group_code: to, effective_from: from }, { allowProposal: true }), `${line.display_name} moves on ${formatDate(from)}. Earlier periods keep the old group.`);
            }}
          >
            <div style={{ width: 160 }}>
              <SelectField label="Move to group" value={to} onChange={(e) => setTo(e.target.value)} options={others.map((g) => ({ value: g.code, label: g.display_name }))} />
            </div>
            <div style={{ width: 160 }}>
              <TextField label="From" type="date" required value={from} onChange={(e) => setFrom(e.target.value)} />
            </div>
            <button type="submit" className="kg-btn" disabled={busy || !from}>
              Move
            </button>
          </form>
        ) : null}
        <form
          style={{ display: "flex", gap: 8, alignItems: "flex-end", flexWrap: "wrap" }}
          onSubmit={(e) => {
            e.preventDefault();
            const both = green && amber;
            void act(
              () => invoke("product_line.update", both ? { code: line.code, rag_green: String(Number(green) / 100), rag_amber: String(Number(amber) / 100) } : { code: line.code, clear_rag: true }),
              both ? `${line.display_name} has its own RAG thresholds.` : `${line.display_name} uses the module's RAG thresholds.`,
            );
          }}
        >
          <div style={{ width: 120 }}>
            <TextField label="Green from %" type="number" min={1} max={500} value={green} onChange={(e) => setGreen(e.target.value)} hint="Blank: module's" />
          </div>
          <div style={{ width: 120 }}>
            <TextField label="Amber from %" type="number" min={1} max={500} value={amber} onChange={(e) => setAmber(e.target.value)} />
          </div>
          <button type="submit" className="kg-btn" disabled={busy || Boolean(green) !== Boolean(amber)}>
            Save thresholds
          </button>
        </form>
      </div>
    </details>
  );
}

function Groups({ registry, canEdit, busy, act }: { registry: Registry; canEdit: boolean; busy: boolean; act: Act }) {
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  return (
    <>
      <h3 className="kg-eyebrow">Groups</h3>
      {registry.groups.length ? (
        <ul style={{ margin: 0, paddingLeft: 18 }}>
          {registry.groups.map((g) => (
            <li key={g.code}>
              <b>{g.display_name}</b> <span className="kg-cap">({g.code}{g.status === "retired" ? ", retired" : ""})</span>: {g.line_codes.length ? g.line_codes.join(", ") : "no lines"}
            </li>
          ))}
        </ul>
      ) : (
        <p className="kg-cap">No groups yet. The first line switched on without one goes into Products; add groups when the matrix needs to collapse.</p>
      )}
      {canEdit ? (
        <form
          style={{ display: "flex", gap: 8, alignItems: "flex-end", flexWrap: "wrap" }}
          onSubmit={(e) => {
            e.preventDefault();
            void act(() => invoke("product_group.set", { code, display_name: name, sort_order: (registry.groups.length + 1) * 10 }, { allowProposal: true }), `${name} added.`);
          }}
        >
          <div style={{ width: 150 }}>
            <TextField label="Group code" value={code} required onChange={(e) => setCode(e.target.value)} hint="Letters, digits and _ . - /" />
          </div>
          <div style={{ width: 180 }}>
            <TextField label="Group name" value={name} required onChange={(e) => setName(e.target.value)} />
          </div>
          <button type="submit" className="kg-btn" disabled={busy || !code || !name}>
            Add group
          </button>
        </form>
      ) : null}
    </>
  );
}
