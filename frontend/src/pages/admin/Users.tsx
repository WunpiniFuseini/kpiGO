import { useState, type FormEvent } from "react";

import type { Output } from "../../api/actions";
import { ApiError, invoke, isProposal } from "../../api/client";
import { useQuery } from "../../api/useAction";
import {
  CheckboxGroup,
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

type User = Output<"user.list">["users"][number];
type Status = "invited" | "active" | "disabled";

const STATUS_TONE = { active: "up", invited: "info", disabled: "flat" } as const;

/** Administer → Users & access: who has an account, and inviting someone (App Flow §7.5). */
export function UsersPage() {
  const me = useMe();
  const canManage = me.permissions.includes("user.manage");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<"" | Status>("");
  const [inviting, setInviting] = useState(false);
  const [list, reload] = useQuery("user.list", { search: search || null, status: status || null });

  return (
    <Page
      title="Users & access"
      actions={
        canManage ? (
          <button type="button" className="kg-btn kg-btn--primary" onClick={() => setInviting(true)} aria-expanded={inviting}>
            Invite user
          </button>
        ) : null
      }
    >
      {inviting ? (
        <InviteForm
          onClose={() => setInviting(false)}
          onInvited={() => {
            reload();
          }}
        />
      ) : null}
      <section className="kg-card">
        <form className="kg-form-row" role="search" onSubmit={(e) => e.preventDefault()} style={{ marginBottom: 16 }}>
          <TextField label="Search name or email" type="search" value={search} onChange={(e) => setSearch(e.target.value)} />
          <SelectField
            label="Status"
            value={status}
            onChange={(e) => setStatus(e.target.value as "" | Status)}
            options={[
              { value: "", label: "Any status" },
              { value: "active", label: "Active" },
              { value: "invited", label: "Invited" },
              { value: "disabled", label: "Disabled" },
            ]}
          />
        </form>
        {list.status === "loading" ? (
          <Loading label="Loading users">
            <TableSkeleton rows={5} columns={5} />
          </Loading>
        ) : list.status === "error" ? (
          <ErrorPanel error={list.error} retry={reload} what="Users" />
        ) : (
          <UserTable
            users={list.data.users}
            filtered={Boolean(search || status)}
            onReset={() => {
              setSearch("");
              setStatus("");
            }}
          />
        )}
      </section>
    </Page>
  );
}

export function UserTable({ users, filtered, onReset }: { users: User[]; filtered: boolean; onReset?: () => void }) {
  return (
    <DataTable
      caption="Users"
      captionHidden
      columns={[
        {
          key: "name",
          header: "Name",
          render: (u) => (
            <>
              {u.display_name}
              <div className="kg-cap">{u.email}</div>
            </>
          ),
        },
        { key: "roles", header: "Roles", render: (u) => (u.roles.length ? u.roles.join(", ") : <span className="kg-cap">No role</span>) },
        { key: "status", header: "Status", render: (u) => <Chip tone={STATUS_TONE[u.status as Status] ?? "flat"}>{u.status}</Chip> },
        { key: "provider", header: "Sign-in", render: (u) => u.auth_provider },
        {
          key: "last",
          header: "Last sign-in",
          render: (u) => (u.last_login_at ? formatDateTime(u.last_login_at) : <span className="kg-cap">Never</span>),
        },
      ]}
      rows={users}
      rowKey={(u) => u.user_id}
      empty={
        filtered ? (
          <EmptyState
            kind="none"
            title="No users match these filters"
            action={
              onReset ? (
                <button type="button" className="kg-btn" onClick={onReset}>
                  Clear filters
                </button>
              ) : null
            }
          />
        ) : (
          <EmptyState kind="none" title="No users yet">
            Invite the first person with Invite user.
          </EmptyState>
        )
      }
    />
  );
}

function InviteForm({ onClose, onInvited }: { onClose: () => void; onInvited: () => void }) {
  const [roles] = useQuery("role.list", {});
  const [form, setForm] = useState({ email: "", display_name: "", role_codes: [] as string[], auth_provider: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<
    { kind: "invited"; out: Output<"user.invite"> } | { kind: "proposed"; message: string } | null
  >(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const out = await invoke(
        "user.invite",
        {
          email: form.email,
          display_name: form.display_name,
          role_codes: form.role_codes,
          auth_provider: (form.auth_provider || null) as "local" | null,
        },
        { allowProposal: true },
      );
      if (isProposal(out)) setResult({ kind: "proposed", message: out.message });
      else setResult({ kind: "invited", out });
      onInvited();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The invitation could not be sent.");
    } finally {
      setBusy(false);
    }
  };

  if (result?.kind === "proposed") {
    return (
      <Notice title="Sent for approval." role="status">
        {result.message} A second Admin approves it before the invitation exists.{" "}
        <button type="button" className="kg-btn--link kg-btn" onClick={onClose}>
          Close
        </button>
      </Notice>
    );
  }
  if (result?.kind === "invited") {
    const link = result.out.invite_token ? `${window.location.origin}/invite?token=${result.out.invite_token}` : null;
    return (
      <section className="kg-card kg-stack" aria-label="Invitation created">
        <h2 className="kg-section">{result.out.user.display_name} is invited</h2>
        <p style={{ color: "var(--ink-2)" }}>{result.out.next_step}</p>
        {link ? (
          <div className="kg-field">
            <span className="kg-eyebrow">Invitation link (shown once)</span>
            <span className="kg-mono" style={{ padding: 10, background: "var(--sunken)", border: "1px solid var(--line)", borderRadius: 8 }}>
              {link}
            </span>
            <span className="kg-hint">Send it to them directly. kpiGo does not send email.</span>
          </div>
        ) : null}
        <div>
          <button type="button" className="kg-btn" onClick={onClose}>
            Done
          </button>
        </div>
      </section>
    );
  }

  const roleOptions = roles.status === "ready" ? roles.data.roles.map((r) => ({ value: r.code, label: r.name })) : [];
  return (
    <section className="kg-card" aria-labelledby="invite-heading">
      <h2 id="invite-heading" className="kg-section" style={{ marginBottom: 14 }}>
        Invite a user
      </h2>
      <form className="kg-form" onSubmit={submit} noValidate>
        <div className="kg-form-row">
          <TextField label="Work email" type="email" required value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
          <TextField label="Name" required value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
          <SelectField
            label="Sign-in method"
            value={form.auth_provider}
            onChange={(e) => setForm({ ...form, auth_provider: e.target.value })}
            options={[
              { value: "", label: "This install's default" },
              { value: "local", label: "Password (kpiGo account)" },
              { value: "ldap", label: "Directory password (LDAP)" },
              { value: "oidc", label: "Single sign-on (OIDC)" },
              { value: "saml", label: "Single sign-on (SAML)" },
            ]}
          />
        </div>
        {roles.status === "error" ? (
          <ErrorPanel error={roles.error} what="Roles" />
        ) : (
          <CheckboxGroup legend="Roles" options={roleOptions} value={form.role_codes} onChange={(role_codes) => setForm({ ...form, role_codes })} />
        )}
        <p className="kg-cap">
          Without a role they can sign in but see nothing. Scorecard visibility follows the hierarchy; Executive and Campaign data need a
          scope grant.
        </p>
        {error ? (
          <p role="alert" style={{ color: "var(--neg-ink)", fontSize: 13 }}>
            {error}
          </p>
        ) : null}
        <div style={{ display: "flex", gap: 8 }}>
          <button type="submit" className="kg-btn kg-btn--primary" disabled={busy || !form.email || !form.display_name}>
            {busy ? "Inviting…" : "Invite"}
          </button>
          <button type="button" className="kg-btn" onClick={onClose}>
            Cancel
          </button>
        </div>
      </form>
    </section>
  );
}
