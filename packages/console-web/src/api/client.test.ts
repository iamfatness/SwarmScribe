import { afterEach, describe, expect, it, vi } from "vitest";
import { fail, mockFetch, reply } from "../test/fetchMock";
import { ApiError, api, leaderPath, query, request, setCsrfToken, setUnauthenticatedHandler } from "./client";

afterEach(() => {
  setCsrfToken(null);
  setUnauthenticatedHandler(() => undefined);
});

describe("api client", () => {
  it("sends the CSRF token on unsafe methods only, and never caches", async () => {
    const mock = mockFetch()
      .on("GET /api/fleet", reply(200, []))
      .on("POST /api/leaders/eu-1/jobs/j/cancel", reply(200, { id: "j" }));
    setCsrfToken("csrf-1");
    await api.get("/api/fleet");
    await api.post("/api/leaders/eu-1/jobs/j/cancel");
    const [get, post] = mock.calls;
    expect(get?.headers["X-CSRF-Token"]).toBeUndefined();
    expect(post?.headers["X-CSRF-Token"]).toBe("csrf-1");
    const init = vi.mocked(fetch).mock.calls[0]?.[1];
    expect(init?.cache).toBe("no-store");
    expect(init?.credentials).toBe("same-origin");
  });

  it("sends a JSON body with its content type", async () => {
    const mock = mockFetch().on("POST /api/admin/grants", reply(201, { id: "g" }));
    await api.post("/api/admin/grants", { role: "viewer" });
    expect(mock.calls[0]?.body).toEqual({ role: "viewer" });
    expect(mock.calls[0]?.headers["Content-Type"]).toBe("application/json");
  });

  it("turns an error answer into an ApiError with code, message and Retry-After", async () => {
    mockFetch().on("GET /api/leaders/eu-1/jobs", {
      status: 503,
      body: { code: "leader_unreachable", message: "leader eu-1 cannot be reached; try again" },
      headers: { "Retry-After": "15" },
    });
    const error = await api.get("/api/leaders/eu-1/jobs").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 503,
      code: "leader_unreachable",
      message: "leader eu-1 cannot be reached; try again",
      retryAfter: 15,
    });
  });

  it("keeps a status-based code when the error body is not {code, message}", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("<html>bad gateway</html>", { status: 502 })),
    );
    const error = await api.get("/api/fleet").catch((e: unknown) => e);
    expect(error).toMatchObject({ status: 502, code: "http_502" });
  });

  it("calls the unauthenticated handler on any 401", async () => {
    mockFetch().on("GET /api/fleet", fail(401, "unauthenticated", "sign in to the console first"));
    const handler = vi.fn();
    setUnauthenticatedHandler(handler);
    await expect(api.get("/api/fleet")).rejects.toMatchObject({ code: "unauthenticated" });
    expect(handler).toHaveBeenCalledOnce();
  });

  it("reports a network failure as network_error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Promise.reject(new TypeError("Failed to fetch"))),
    );
    await expect(api.get("/api/fleet")).rejects.toMatchObject({ status: 0, code: "network_error" });
  });

  it("answers undefined for 204", async () => {
    mockFetch().on("DELETE /api/admin/grants/g", reply(204));
    await expect(api.del("/api/admin/grants/g")).resolves.toBeUndefined();
  });

  it("turns a 2xx answer that is not JSON into bad_response, not a SyntaxError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("<html>proxy</html>", { status: 200 })),
    );
    const error = await api.get("/api/fleet").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 200, code: "bad_response" });
  });

  it("sends the CSRF token on PUT, PATCH and DELETE, whatever the method's case", async () => {
    const mock = mockFetch()
      .on("PUT /api/a", reply(200, {}))
      .on("PATCH /api/b", reply(200, {}))
      .on("DELETE /api/c", reply(204))
      .on("POST /api/d", reply(200, {}))
      .on("GET /api/e", reply(200, {}));
    setCsrfToken("csrf-2");
    await api.put("/api/a", {});
    await api.patch("/api/b", {});
    await api.del("/api/c");
    await request("post", "/api/d");
    await request("get", "/api/e");
    expect(mock.calls.map((c) => c.headers["X-CSRF-Token"])).toEqual([
      "csrf-2",
      "csrf-2",
      "csrf-2",
      "csrf-2",
      undefined,
    ]);
    expect(mock.calls[3]?.method).toBe("POST");
  });

  it("refuses a path outside /api/ and /auth/ before calling fetch", async () => {
    const mock = mockFetch();
    setCsrfToken("csrf-3");
    for (const path of ["https://evil.test/api/x", "//evil.test/api/x", "/other", "api/fleet", "/api/../x"]) {
      await expect(api.post(path, {})).rejects.toMatchObject({ code: "bad_path" });
    }
    expect(mock.calls).toHaveLength(0);
  });

  it("builds leader paths and query strings", () => {
    expect(leaderPath("eu-1.prod", "jobs")).toBe("/api/leaders/eu-1.prod/jobs");
    expect(query({ state: "failed", location: "", limit: 50, x: null })).toBe("?state=failed&limit=50");
    expect(query({})).toBe("");
  });
});
