import { ApiError, invoke, setSignedOutHandler, setTransport, type Transport } from "../api/client";

function respond(status: number, body: unknown): { transport: Transport; calls: [string, RequestInit][] } {
  const calls: [string, RequestInit][] = [];
  return {
    calls,
    transport: async (url, init) => {
      calls.push([url, init]);
      return new Response(JSON.stringify(body), { status });
    },
  };
}

afterEach(() => {
  setSignedOutHandler(null);
  document.cookie = "csrftoken=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
});

describe("invoke", () => {
  it("sends a read as GET with its input as the query and a request id", async () => {
    const { transport, calls } = respond(200, { users: [] });
    setTransport(transport);
    await invoke("user.list", { search: "ama", status: null });
    const [url, init] = calls[0];
    expect(url).toBe("/api/v1/users?search=ama");
    expect(init.method).toBe("GET");
    expect((init.headers as Record<string, string>)["X-Request-ID"]).toMatch(/^[0-9a-f]{32}$/);
  });

  it("sends a write as JSON with the CSRF token", async () => {
    document.cookie = "csrftoken=tok123";
    const { transport, calls } = respond(200, { signed_out: true });
    setTransport(transport);
    await invoke("auth.logout", {});
    const [url, init] = calls[0];
    const headers = init.headers as Record<string, string>;
    expect(url).toBe("/api/v1/auth/logout");
    expect(init.method).toBe("POST");
    expect(headers["X-CSRFToken"]).toBe("tok123");
    expect(headers["Content-Type"]).toBe("application/json");
    expect(init.body).toBe("{}");
  });

  it("turns an error body into an ApiError with a reference to quote", async () => {
    setTransport(respond(409, { error: "conflict", message: "Already exists.", detail: { a: 1 } }).transport);
    const error = await invoke("auth.logout", {}).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    const api = error as ApiError;
    expect([api.status, api.code, api.message]).toEqual([409, "conflict", "Already exists."]);
    expect(api.reference).toMatch(/^[0-9A-F]{12}$/);
  });

  it("reports a signed-out session only for signed-in actions", async () => {
    const handler = vi.fn();
    setSignedOutHandler(handler);
    setTransport(respond(401, { error: "not_authenticated", message: "Sign in." }).transport);
    await invoke("auth.login", { email: "a@b.c", password: "x" }).catch(() => {});
    expect(handler).not.toHaveBeenCalled();
    await invoke("auth.me", {}).catch(() => {});
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("treats a maker-checker proposal as an error unless the caller expects one", async () => {
    const proposal = { approval_request_id: "r1", action_name: "user.invite", status: "pending", message: "Submitted for approval." };
    setTransport(respond(202, proposal).transport);
    const input = { email: "a@b.example", display_name: "A", role_codes: [] };
    await expect(invoke("user.invite", input)).rejects.toMatchObject({ status: 202, code: "proposed" });
    await expect(invoke("user.invite", input, { allowProposal: true })).resolves.toEqual(proposal);
  });

  it("says kpiGo is unreachable when the network fails", async () => {
    setTransport(async () => {
      throw new TypeError("Failed to fetch");
    });
    await expect(invoke("auth.providers", {})).rejects.toMatchObject({ code: "unreachable", status: 0 });
  });
});
