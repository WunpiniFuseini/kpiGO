import { useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { ApiError, invoke } from "../api/client";
import { EmptyState, TextField } from "../components";
import { useSession } from "../session/Session";
import { AuthLayout } from "./AuthLayout";

/** Accepting an invitation sets the password of a local account and signs in. */
export function InviteAccept() {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const { signedIn } = useSession();
  const navigate = useNavigate();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [problems, setProblems] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  if (!token) {
    return (
      <AuthLayout title="Accept your invitation">
        <EmptyState kind="no-access" title="This link has no invitation in it" ask="your kpiGo Admin to resend the invitation">
          Open the link from your invitation email exactly as it was sent.
        </EmptyState>
      </AuthLayout>
    );
  }

  const mismatch = confirm.length > 0 && confirm !== password;
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (mismatch) return;
    setBusy(true);
    setError(null);
    setProblems([]);
    try {
      signedIn(await invoke("auth.invite.accept", { token, password }));
      navigate("/", { replace: true });
    } catch (e) {
      if (e instanceof ApiError) {
        setError(e.message);
        if (Array.isArray(e.detail)) setProblems(e.detail.filter((d): d is string => typeof d === "string"));
      } else setError("The invitation could not be accepted.");
      setBusy(false);
    }
  };

  return (
    <AuthLayout title="Accept your invitation">
      <form className="kg-form" onSubmit={submit} noValidate>
        <p style={{ color: "var(--ink-2)" }}>Choose a password for your kpiGo account. Use at least 12 characters; a short phrase works well.</p>
        <TextField label="New password" type="password" autoComplete="new-password" required minLength={12} value={password} onChange={(e) => setPassword(e.target.value)} />
        <TextField
          label="Confirm the password"
          type="password"
          autoComplete="new-password"
          required
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          error={mismatch ? "The two passwords do not match." : null}
        />
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
        <button type="submit" className="kg-btn kg-btn--primary kg-btn--block" disabled={busy || !password || mismatch}>
          {busy ? "Setting your password…" : "Set password and sign in"}
        </button>
        <p className="kg-cap">
          Already set up? <Link to="/login">Sign in</Link>.
        </p>
      </form>
    </AuthLayout>
  );
}
