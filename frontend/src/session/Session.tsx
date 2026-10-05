import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import type { Output } from "../api/actions";
import { ApiError, invoke, setSignedOutHandler } from "../api/client";

export type Me = Output<"auth.me">;

export type SessionState =
  | { status: "loading" }
  | { status: "signed-out"; reason: "none" | "expired" }
  | { status: "signed-in"; me: Me }
  | { status: "error"; error: ApiError };

interface SessionApi {
  state: SessionState;
  /** After a sign-in action returns the shell. */
  signedIn: (me: Me) => void;
  signOut: () => Promise<void>;
  refresh: () => void;
}

const SessionContext = createContext<SessionApi | null>(null);

export function SessionProvider({ children, initial }: { children: ReactNode; initial?: SessionState }) {
  const [state, setState] = useState<SessionState>(initial ?? { status: "loading" });
  const [tick, setTick] = useState(initial ? -1 : 0);

  useEffect(() => {
    if (tick < 0) return;
    let live = true;
    invoke("auth.me", {}).then(
      (me) => live && setState({ status: "signed-in", me }),
      (error: ApiError) => {
        if (!live) return;
        if (error.status === 401 || error.status === 403) setState({ status: "signed-out", reason: "none" });
        else setState({ status: "error", error });
      },
    );
    return () => {
      live = false;
    };
  }, [tick]);

  useEffect(() => {
    setSignedOutHandler(() => setState({ status: "signed-out", reason: "expired" }));
    return () => setSignedOutHandler(null);
  }, []);

  const signedIn = useCallback((me: Me) => setState({ status: "signed-in", me }), []);
  const refresh = useCallback(() => setTick((t) => Math.max(t, 0) + 1), []);
  const signOut = useCallback(async () => {
    try {
      await invoke("auth.logout", {});
    } finally {
      setState({ status: "signed-out", reason: "none" });
    }
  }, []);

  const value = useMemo(() => ({ state, signedIn, signOut, refresh }), [state, signedIn, signOut, refresh]);
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionApi {
  const api = useContext(SessionContext);
  if (!api) throw new Error("useSession outside SessionProvider");
  return api;
}

/** The signed-in shell; only valid under RequireSession. */
export function useMe(): Me {
  const { state } = useSession();
  if (state.status !== "signed-in") throw new Error("useMe without a session");
  return state.me;
}
