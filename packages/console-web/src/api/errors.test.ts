import { describe, expect, it } from "vitest";
import { ApiError } from "./client";
import { ERROR_TITLES, describeError } from "./errors";

// Every code the C3 handoff note names, plus the console's own session codes.
const HANDOFF_CODES = [
  "leader_not_found",
  "not_found",
  "unauthenticated",
  "leader_unreachable",
  "leader_credential_revoked",
  "leader_credential_unreadable",
  "leader_credential_rejected",
  "bad_gateway",
  "leader_disabled",
  "forbidden",
  "actor_not_representable",
  "invalid_request",
  "too_large",
  "csrf_failed",
  "unavailable",
];

describe("describeError", () => {
  it.each(HANDOFF_CODES)("has its own title for %s", (code) => {
    expect(ERROR_TITLES[code]).toBeTruthy();
    expect(describeError(new ApiError(400, code, "x")).title).toBe(ERROR_TITLES[code]);
  });

  it("shows leader_not_found as a leader the person cannot see", () => {
    expect(describeError(new ApiError(404, "leader_not_found", "no leader with that name")).title).toBe(
      "You cannot see this leader.",
    );
  });

  it("says every title as a sentence, in the console's own words", () => {
    for (const [code, title] of Object.entries(ERROR_TITLES)) {
      expect(title, code).toMatch(/^[A-Z].*\.$/);
      // The leader's and the protocol's words stay out of what a person reads.
      expect(title, code).not.toMatch(/\b(poll|leased|queued|retr(y|ied)|unreachable|principal|scope|URL)\b/i);
    }
  });

  it("keeps the server's message as the detail and adds Retry-After", () => {
    const text = describeError(
      new ApiError(503, "leader_unreachable", "leader eu-1 cannot be reached; try again", 15),
    );
    expect(text.detail).toBe("leader eu-1 cannot be reached; try again Try again in 15 seconds.");
  });

  it("names the role a forbidden action needs, from the console's message", () => {
    const text = describeError(
      new ApiError(403, "forbidden", "this needs the admin role on eu-1; you have operator"),
    );
    expect(text.title).toBe("Your role does not allow this.");
    expect(text.detail).toContain("admin role");
  });

  it("falls back by status for an unknown code", () => {
    expect(describeError(new ApiError(409, "something_new", "it clashed")).title).toBe(
      "That no longer fits how things stand. Refresh, then look again.",
    );
    expect(describeError(new ApiError(504, "gateway_timeout", "slow")).title).toBe(
      "The leader or the console did not answer.",
    );
    expect(describeError(new ApiError(429, "slow_down", "wait")).title).toBe(
      "Too many requests. Wait a little, then try again.",
    );
  });

  it("drops the generic HTTP text when the body had no code", () => {
    expect(describeError(new ApiError(502, "http_502", "The console answered HTTP 502.")).detail).toBeNull();
  });

  it("never throws on something that is not an ApiError", () => {
    expect(describeError(new Error("boom")).title).toBe("Something went wrong in the console.");
  });
});
