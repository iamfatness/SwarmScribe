// The only code that calls the console. Every unsafe call carries X-CSRF-Token (from
// GET /api/session); the browser adds a same-origin Origin itself. Nothing is cached
// (cache: "no-store") and nothing is logged. A 401 from any /api route always means the
// console's own session ended (leader 401s arrive as 502/503), so it calls the
// unauthenticated handler, which sends the person to sign in.

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly retryAfter: number | null;

  constructor(status: number, code: string, message: string, retryAfter: number | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.retryAfter = retryAfter;
  }
}

export const NETWORK_ERROR = "network_error";
export const BAD_RESPONSE = "bad_response";
export const BAD_PATH = "bad_path";

const SAFE = new Set(["GET", "HEAD", "OPTIONS"]);
const ALLOWED_PATH = /^\/(?:api|auth)\//;
const DOT_SEGMENT = /(?:^|\/)\.\.?(?:\/|\?|#|$)/;
const CODE = /^[a-z][a-z0-9_]{0,63}$/;

let csrfToken: string | null = null;
let onUnauthenticated: () => void = () => undefined;

export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}

export function setUnauthenticatedHandler(handler: () => void): void {
  onUnauthenticated = handler;
}

/** Tests only: forget the CSRF token and the 401 handler. */
export function resetClientForTests(): void {
  csrfToken = null;
  onUnauthenticated = () => undefined;
}

export function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function retryAfterOf(response: Response): number | null {
  const value = response.headers.get("Retry-After");
  return value !== null && /^\d{1,5}$/.test(value) ? Number(value) : null;
}

async function errorFrom(response: Response): Promise<ApiError> {
  let code = `http_${response.status}`;
  let message = `The console answered HTTP ${response.status}.`;
  try {
    const body: unknown = await response.json();
    if (body !== null && typeof body === "object") {
      const { code: c, message: m } = body as Record<string, unknown>;
      if (typeof c === "string" && CODE.test(c) && typeof m === "string") {
        code = c;
        message = m;
      }
    }
  } catch {
    // Not JSON: keep the status-based code.
  }
  return new ApiError(response.status, code, message, retryAfterOf(response));
}

export async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  // Only the console's own routes: a stray absolute URL must never carry the CSRF token.
  if (!ALLOWED_PATH.test(path) || DOT_SEGMENT.test(path)) {
    throw new ApiError(0, BAD_PATH, "The request path is not a console route.");
  }
  method = method.toUpperCase();
  const headers: Record<string, string> = { Accept: "application/json" };
  if (!SAFE.has(method) && csrfToken !== null) headers["X-CSRF-Token"] = csrfToken;
  let payload: string | undefined;
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  let response: Response;
  try {
    response = await fetch(path, {
      method,
      headers,
      body: payload,
      credentials: "same-origin",
      cache: "no-store",
      signal,
    });
  } catch (error) {
    if (isAbort(error)) throw error;
    throw new ApiError(0, NETWORK_ERROR, "The console could not be reached.");
  }
  if (response.status === 401) {
    onUnauthenticated();
    throw await errorFrom(response);
  }
  if (!response.ok) throw await errorFrom(response);
  if (response.status === 204) return undefined as T;
  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiError(response.status, BAD_RESPONSE, "The console's answer could not be read.");
  }
}

export const api = {
  get: <T>(path: string, signal?: AbortSignal) => request<T>("GET", path, undefined, signal),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  del: (path: string) => request<undefined>("DELETE", path),
};

/** /api/leaders/<name>/<rest>; the name is encoded (leader names allow "." and "-"). */
export function leaderPath(name: string, rest: string): string {
  return `/api/leaders/${encodeURIComponent(name)}/${rest}`;
}

/** A query string from the set values only ("" when none). */
export function query(params: Record<string, string | number | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}
