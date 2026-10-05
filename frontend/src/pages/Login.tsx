import { useState, type FormEvent } from "react";
import { Link, Navigate, useSearchParams } from "react-router-dom";

import { ApiError, invoke } from "../api/client";
import { useQuery } from "../api/useAction";
import { Notice, Skeleton, TextField } from "../components";
import { useSession } from "../session/Session";
import { AuthLayout } from "./AuthLayout";

const SSO_ERRORS: Record<string, string> = {
  sso_refused: "Your organisation's sign-in did not complete. Try again, or ask your IT team if it keeps happening.",
};

/** Sign-in (App Flow §1): SSO first where configured, local or directory password as fallback. */
export function Login() {
  const { state, signedIn } = useSession();
  const [params] = useSearchParams();
  const next = safeNext(params.get("next"));
  const [providers] = useQuery("auth.providers", {});
  const [setup] = useQuery("setup.status", {});
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (state.status === "signed-in") return <Navigate to={next} replace />;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      signedIn(await invoke("auth.login", { email, password }));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Sign-in failed.");
    } finally {
      setBusy(false);
    }
  };

  const sso = async (kind: "auth.oidc.start" | "auth.saml.start") => {
    setBusy(true);
    setError(null);
    try {
      const { redirect_url } = await invoke(kind, { next });
      window.location.assign(redirect_url);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Single sign-on could not start.");
      setBusy(false);
    }
  };

  const ssoError = SSO_ERRORS[params.get("error") ?? ""];
  const expired = state.status === "signed-out" && state.reason === "expired";

  return (
    <AuthLayout title="Sign in">
      <div className="kg-stack">
        {expired ? <Notice role="status">Your session ended. Sign in again to carry on.</Notice> : null}
        {ssoError ? <Notice tone="neg" role="alert">{ssoError}</Notice> : null}
        {setup.status === "ready" && setup.data.needs_admin ? (
          <Notice tone="warn" title="First run.">
            This install has no Admin yet. <Link to="/setup">Create the first Admin</Link>.
          </Notice>
        ) : null}
        {providers.status === "loading" ? (
          <div role="status" aria-busy="true" className="kg-stack">
            <span className="sr-only">Loading sign-in options</span>
            <Skeleton height={36} />
            <Skeleton height={36} />
          </div>
        ) : providers.status === "error" ? (
          <Notice tone="neg" role="alert">
            kpiGo could not be reached. Reference {providers.error.reference}.
          </Notice>
        ) : (
          <>
            {providers.data.oidc.enabled ? (
              <button type="button" className="kg-btn kg-btn--primary kg-btn--block" disabled={busy} onClick={() => sso("auth.oidc.start")}>
                {providers.data.oidc.label}
              </button>
            ) : null}
            {providers.data.saml.enabled ? (
              <button type="button" className="kg-btn kg-btn--primary kg-btn--block" disabled={busy} onClick={() => sso("auth.saml.start")}>
                {providers.data.saml.label}
              </button>
            ) : null}
            {providers.data.password.enabled ? (
              <>
                {providers.data.oidc.enabled || providers.data.saml.enabled ? (
                  <div className="kg-auth__or">or with a password</div>
                ) : null}
                <form className="kg-form" onSubmit={submit} noValidate>
                  <TextField label="Work email" type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
                  <TextField label="Password" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
                  {error ? (
                    <p className="kg-field-error" role="alert" style={{ color: "var(--neg-ink)", fontSize: 13 }}>
                      {error}
                    </p>
                  ) : null}
                  <button type="submit" className={`kg-btn kg-btn--block${providers.data.oidc.enabled || providers.data.saml.enabled ? "" : " kg-btn--primary"}`} disabled={busy || !email || !password}>
                    {busy ? "Signing in…" : "Sign in"}
                  </button>
                </form>
              </>
            ) : error ? (
              <p role="alert" style={{ color: "var(--neg-ink)", fontSize: 13 }}>
                {error}
              </p>
            ) : null}
            {!providers.data.password.enabled && !providers.data.oidc.enabled && !providers.data.saml.enabled ? (
              <Notice tone="warn">No sign-in method is configured on this install. Your IT team sets one in the kpiGo settings.</Notice>
            ) : null}
          </>
        )}
        <p className="kg-cap">Forgotten your password? Ask your kpiGo Admin to resend your invitation.</p>
      </div>
    </AuthLayout>
  );
}

/** Only same-site paths: an open redirect after sign-in is a phishing aid. */
export function safeNext(value: string | null): string {
  if (!value || !value.startsWith("/") || value.startsWith("//") || value.startsWith("/\\")) return "/";
  return value;
}
