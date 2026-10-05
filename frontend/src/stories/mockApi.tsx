import type { Decorator } from "@storybook/react-vite";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import type { ActionName, Output } from "../api/actions";
import { ROUTES } from "../api/actions";
import { setTransport } from "../api/client";
import { SessionProvider, type Me, type SessionState } from "../session/Session";

/** A canned reply for an action: data, an error, or never (loading forever). */
export type Reply<N extends ActionName = ActionName> =
  | { data: Output<N>; status?: number }
  | { error: { status: number; error: string; message: string; detail?: unknown } }
  | "pending";

export type Handlers = Partial<{ [N in ActionName]: Reply<N> }>;

function pathToAction(url: string): ActionName | undefined {
  const path = url.split("?")[0];
  return (Object.keys(ROUTES) as ActionName[]).find((name) => ROUTES[name].path === path);
}

/** Stories never touch a server: every action call resolves from the handlers. */
export function installMockApi(handlers: Handlers): void {
  setTransport(async (url) => {
    const name = pathToAction(url);
    const reply = name ? handlers[name] : undefined;
    if (reply === "pending") return new Promise<Response>(() => {});
    if (!reply) {
      return new Response(JSON.stringify({ error: "not_found", message: `No story handler for ${name ?? url}.` }), { status: 404 });
    }
    if ("error" in reply) return new Response(JSON.stringify(reply.error), { status: reply.error.status });
    return new Response(JSON.stringify(reply.data), { status: reply.status ?? 200 });
  });
}

/** Wraps a story in a router at `path`, a session and the mock API. */
export function withApp({
  handlers = {},
  me,
  session,
  path = "/",
  route = "*",
}: {
  handlers?: Handlers;
  me?: Me;
  session?: SessionState;
  path?: string;
  route?: string;
}): Decorator {
  return (Story) => {
    installMockApi({ ...(me ? { "auth.me": { data: me } } : {}), ...handlers });
    const initial: SessionState = session ?? (me ? { status: "signed-in", me } : { status: "signed-out", reason: "none" });
    return (
      <MemoryRouter initialEntries={[path]}>
        <SessionProvider initial={initial}>
          <Routes>
            <Route path={route} element={<Story />} />
          </Routes>
        </SessionProvider>
      </MemoryRouter>
    );
  };
}
