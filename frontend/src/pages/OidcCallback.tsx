import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { ApiError, invoke } from "../api/client";
import { EmptyState, Skeleton } from "../components";
import { useSession } from "../session/Session";
import { AuthLayout } from "./AuthLayout";
import { safeNext } from "./Login";

/** Where the identity provider returns the browser (KPIGO_OIDC_REDIRECT_URI = https://<host>/auth/callback). */
export function OidcCallback() {
  const [params] = useSearchParams();
  const { signedIn } = useSession();
  const navigate = useNavigate();
  const [error, setError] = useState<ApiError | string | null>(null);
  const started = useRef(false);

  const code = params.get("code");
  const state = params.get("state");
  const providerError = params.get("error_description") ?? params.get("error");

  useEffect(() => {
    if (started.current || !code || !state) return;
    started.current = true; // a code is single-use; React's dev double-effect must not spend it twice
    invoke("auth.oidc.complete", { code, state }).then(
      (result) => {
        signedIn(result);
        navigate(safeNext(result.next), { replace: true });
      },
      (e: unknown) => setError(e instanceof ApiError ? e : "Sign-in could not be completed."),
    );
  }, [code, state, signedIn, navigate]);

  const failure = providerError ?? (!code || !state ? "The sign-in response was incomplete." : error);
  return (
    <AuthLayout title="Signing you in">
      {failure ? (
        <EmptyState
          kind="error"
          title="Sign-in did not complete"
          reference={failure instanceof ApiError ? failure.reference : undefined}
          action={
            <Link className="kg-btn" to="/login">
              Back to sign in
            </Link>
          }
        >
          {failure instanceof ApiError ? failure.message : failure}
        </EmptyState>
      ) : (
        <div role="status" aria-busy="true" className="kg-stack">
          <span className="sr-only">Completing sign-in</span>
          <Skeleton height={14} width="60%" />
          <Skeleton height={14} width="40%" />
        </div>
      )}
    </AuthLayout>
  );
}
