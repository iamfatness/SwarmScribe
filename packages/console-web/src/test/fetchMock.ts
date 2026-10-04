import { vi } from "vitest";

// A stand-in for the console in component tests. Routes are "METHOD /path?query" (the query
// exactly as sent, or omitted to match any query). A route's handler answers with
// reply(status, body, headers) or throws. Every call is kept in `calls`.

export interface Call {
  method: string;
  url: string;
  headers: Record<string, string>;
  body: unknown;
}

export interface Reply {
  status: number;
  body?: unknown;
  headers?: Record<string, string>;
}

type Handler = (call: Call) => Reply | Promise<Reply>;

export function reply(status: number, body?: unknown, headers?: Record<string, string>): Reply {
  return { status, body, headers };
}

export function fail(status: number, code: string, message = code): Reply {
  return { status, body: { code, message } };
}

export interface FetchMock {
  calls: Call[];
  on: (route: string, handler: Handler | Reply) => FetchMock;
  callsTo: (route: string) => Call[];
}

export function mockFetch(): FetchMock {
  const routes = new Map<string, Handler>();
  const calls: Call[] = [];
  const key = (method: string, url: string) => `${method} ${url}`;

  const fetchStub = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = (init?.method ?? "GET").toUpperCase();
    const headers = Object.fromEntries(
      Object.entries((init?.headers as Record<string, string> | undefined) ?? {}),
    );
    const body = typeof init?.body === "string" ? (JSON.parse(init.body) as unknown) : undefined;
    const call: Call = { method, url, headers, body };
    calls.push(call);
    if (init?.signal?.aborted) throw new DOMException("aborted", "AbortError");
    const handler = routes.get(key(method, url)) ?? routes.get(key(method, url.split("?")[0] ?? url));
    if (handler === undefined) {
      return new Response(JSON.stringify({ code: "not_found", message: `no mock for ${method} ${url}` }), {
        status: 404,
        headers: { "Content-Type": "application/json" },
      });
    }
    const answer = await handler(call);
    const init2: ResponseInit = { status: answer.status, headers: answer.headers };
    if (answer.status === 204 || answer.body === undefined) return new Response(null, init2);
    return new Response(JSON.stringify(answer.body), {
      ...init2,
      headers: { "Content-Type": "application/json", ...(answer.headers ?? {}) },
    });
  });
  vi.stubGlobal("fetch", fetchStub);

  const mock: FetchMock = {
    calls,
    on(route, handler) {
      routes.set(route, typeof handler === "function" ? handler : () => handler);
      return mock;
    },
    callsTo(route) {
      return calls.filter((call) => key(call.method, call.url) === route);
    },
  };
  return mock;
}
