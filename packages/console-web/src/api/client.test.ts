import { afterEach, describe, expect, it, vi } from "vitest";
import { fail, mockFetch, reply } from "../test/fetchMock";
import { ApiError, api, leaderPath, query, setCsrfToken, setUnauthenticatedHandler } from "./client";

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
    mockFetch().on("GET /api/fleet", reply(502, "<html>bad gateway</html>"));
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

  it("builds leader paths and query strings", () => {
    expect(leaderPath("eu-1.prod", "jobs")).toBe("/api/leaders/eu-1.prod/jobs");
    expect(query({ state: "failed", location: "", limit: 50, x: null })).toBe("?state=failed&limit=50");
    expect(query({})).toBe("");
  });
});
