/**
 * The one way the UI talks to kpiGo: invoke a registered action by name.
 *
 * Routes and types are generated from the action registry's OpenAPI document,
 * so the UI cannot call something that is not an action (CLAUDE.md, the
 * governing rule). Every call carries a request id, and every error message
 * shows it, so a user can quote it to support.
 */
import { ROUTES, type ActionName, type Input, type Output } from "./actions";

export interface Proposal {
  approval_request_id: string;
  action_name: string;
  status: string;
  message: string;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly detail: unknown;
  readonly requestId: string;

  constructor(status: number, code: string, message: string, detail: unknown, requestId: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.detail = detail;
    this.requestId = requestId;
  }

  /** The reference a user quotes to support: the request id the audit log records. */
  get reference(): string {
    return this.requestId.slice(0, 12).toUpperCase();
  }
}

export function newRequestId(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

function csrfToken(): string | undefined {
  const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : undefined;
}

export type Transport = (url: string, init: RequestInit) => Promise<Response>;

let transport: Transport = (url, init) => fetch(url, init);

/** Swap the network for stories and tests. Returns the previous transport. */
export function setTransport(next: Transport): Transport {
  const previous = transport;
  transport = next;
  return previous;
}

type SignedOutHandler = (error: ApiError) => void;
let onSignedOut: SignedOutHandler | null = null;

/** Called when a signed-in call comes back 401: the session ended or expired. */
export function setSignedOutHandler(handler: SignedOutHandler | null): void {
  onSignedOut = handler;
}

export type Result<N extends ActionName> = Output<N> | Proposal;

export async function invoke<N extends ActionName>(name: N, input: Input<N>): Promise<Output<N>>;
export async function invoke<N extends ActionName>(
  name: N,
  input: Input<N>,
  options: { allowProposal: true },
): Promise<Result<N>>;
export async function invoke<N extends ActionName>(
  name: N,
  input: Input<N>,
  options?: { allowProposal?: boolean },
): Promise<Result<N>> {
  const route = ROUTES[name];
  const params: Record<string, unknown> = { ...(input as Record<string, unknown>) };
  let path: string = route.path;
  for (const key of route.pathParams as readonly string[]) {
    path = path.replace(`{${key}}`, encodeURIComponent(String(params[key])));
    delete params[key];
  }
  const requestId = newRequestId();
  const headers: Record<string, string> = { Accept: "application/json", "X-Request-ID": requestId };
  const init: RequestInit = { method: route.method, headers, credentials: "same-origin" };
  if (route.method === "GET") {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value === undefined || value === null) continue;
      for (const item of Array.isArray(value) ? value : [value]) query.append(key, String(item));
    }
    const text = query.toString();
    if (text) path += `?${text}`;
  } else {
    headers["Content-Type"] = "application/json";
    const token = csrfToken();
    if (token) headers["X-CSRFToken"] = token;
    init.body = JSON.stringify(params);
  }

  let response: Response;
  try {
    response = await transport(path, init);
  } catch {
    throw new ApiError(0, "unreachable", "kpiGo could not be reached. Check your connection and try again.", null, requestId);
  }
  const body: unknown = await response.json().catch(() => null);
  if (response.status === 202 && !options?.allowProposal) {
    const proposal = body as Proposal;
    throw new ApiError(202, "proposed", proposal?.message ?? "Submitted for approval.", proposal, requestId);
  }
  if (!response.ok) {
    const error = (body ?? {}) as { error?: string; message?: string; detail?: unknown };
    const failure = new ApiError(
      response.status,
      error.error ?? `http_${response.status}`,
      error.message ?? "Something went wrong on the server.",
      error.detail ?? null,
      requestId,
    );
    if (response.status === 401 && !route.public) onSignedOut?.(failure);
    throw failure;
  }
  return body as Result<N>;
}

export function isProposal(value: unknown): value is Proposal {
  return typeof value === "object" && value !== null && "approval_request_id" in value;
}
