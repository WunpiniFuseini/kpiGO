import { useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";

import { ApiError, invoke } from "../api/client";
import { useQuery } from "../api/useAction";
import { ErrorPanel, Notice, Skeleton, TextField } from "../components";
import { useSession } from "../session/Session";
import { AuthLayout } from "./AuthLayout";

/**
 * First run (App Flow §2), the part R0 ships: create the first Admin with the
 * install's one-time setup token. The licence, directory, calendar and first
 * feed steps follow in the Admin's own screens.
 */
export function Setup() {
  const [status, reload] = useQuery("setup.status", {});
  const { signedIn } = useSession();
  const navigate = useNavigate();
  const [form, setForm] = useState({ setup_token: "", email: "", display_name: "", password: "" });
  const [error, setError] = useState<string | null>(null);
  const [problems, setProblems] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  if (status.status === "loading") {
    return (
      <AuthLayout title="Set up kpiGo">
        <div role="status" aria-busy="true" className="kg-stack">
          <span className="sr-only">Checking this install</span>
          <Skeleton height={14} width="70%" />
          <Skeleton height={36} />
          <Skeleton height={36} />
        </div>
      </AuthLayout>
    );
  }
  if (status.status === "error") {
    return (
      <AuthLayout title="Set up kpiGo">
        <ErrorPanel error={status.error} retry={reload} what="This install's status" />
      </AuthLayout>
    );
  }
  if (!status.data.needs_admin) return <Navigate to="/login" replace />;

  const set = (key: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setProblems([]);
    try {
      signedIn(await invoke("setup.bootstrap", form));
      navigate("/", { replace: true });
    } catch (e) {
      setBusy(false);
      if (e instanceof ApiError) {
        setError(e.message);
        if (Array.isArray(e.detail)) setProblems(e.detail.filter((d): d is string => typeof d === "string"));
      } else setError("The Admin could not be created.");
    }
  };

  return (
    <AuthLayout title="Set up kpiGo">
      <form className="kg-form" onSubmit={submit} noValidate>
        <p style={{ color: "var(--ink-2)" }}>Create the first Admin. That Admin then invites everyone else and works through the rest of setup.</p>
        {!status.data.setup_token_configured ? (
          <Notice tone="warn" title="No setup token.">
            Your IT team sets <code>KPIGO_SETUP_TOKEN</code> on the server and restarts kpiGo. Then enter it here.
          </Notice>
        ) : null}
        <TextField label="Setup token" type="password" autoComplete="off" required hint="The one-time token your IT team set when installing kpiGo." value={form.setup_token} onChange={set("setup_token")} />
        <TextField label="Your name" autoComplete="name" required value={form.display_name} onChange={set("display_name")} />
        <TextField label="Work email" type="email" autoComplete="username" required value={form.email} onChange={set("email")} />
        <TextField label="Password" type="password" autoComplete="new-password" required minLength={12} hint="At least 12 characters." value={form.password} onChange={set("password")} />
        {error ? (
          <div role="alert" style={{ color: "var(--neg-ink)", fontSize: 13 }}>
            {error}
            {problems.length ? (
              <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
                {problems.map((p) => (
                  <li key={p}>{p}</li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}
        <button type="submit" className="kg-btn kg-btn--primary kg-btn--block" disabled={busy || !form.setup_token || !form.email || !form.password || !form.display_name}>
          {busy ? "Creating the Admin…" : "Create Admin and sign in"}
        </button>
        <div className="kg-cap">
          Install fingerprint, for your licence: <span className="kg-mono">{status.data.install_fingerprint}</span>
          <br />
          Version {status.data.product_version}
        </div>
      </form>
    </AuthLayout>
  );
}
