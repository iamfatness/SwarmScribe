import { describe, expect, it } from "vitest";
import { ApiError } from "./client";
import { ERROR_TITLES, OwnRefusal, describeError } from "./errors";

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

  it("says how long to wait, and nothing of the server's own sentence", () => {
    const text = describeError(
      new ApiError(503, "leader_unreachable", "leader eu-1 cannot be reached; try again", 15),
    );
    expect(text.detail).toBe("Try again in 15 seconds.");
  });

  it("has no line beneath a leader that is not answering when no wait is known", () => {
    const text = describeError(new ApiError(503, "leader_unreachable", "cannot be reached; try again", null));
    expect(text.detail).toBeNull();
  });

  it("names the leader when the page knows which one it is", () => {
    const here = { leader: { name: "us-1", role: "admin" as const } };
    expect(describeError(new ApiError(503, "leader_unreachable", "x", 15), here)).toMatchObject({
      title: "us-1 is not answering right now.",
      detail: "Try again in 15 seconds.",
      retryable: true,
    });
    expect(describeError(new ApiError(409, "leader_disabled", "this leader is disabled in the console"), here)).toEqual({
      title: "us-1 is switched off in the console.",
      detail: "A console administrator can switch it on under Administration.",
      retryable: false,
      toFleet: false,
    });
  });

  it("offers no retry for a switched-off leader: asking again cannot work", () => {
    expect(describeError(new ApiError(409, "leader_disabled", "x")).retryable).toBe(false);
    expect(describeError(new ApiError(503, "leader_unreachable", "x")).retryable).toBe(true);
  });

  it("sends a person whose leader is gone back to the fleet", () => {
    expect(describeError(new ApiError(404, "leader_not_found", "no leader with that name"))).toEqual({
      title: "You cannot see this leader.",
      detail: "It is no longer in the console, or you no longer have a role on it.",
      retryable: false,
      toFleet: true,
    });
  });

  it("says what to do about the last console administrator", () => {
    const text = describeError(
      new ApiError(409, "last_admin", "this is the last console administrator; add another first"),
    );
    expect(text.title).toBe("The last console administrator cannot be removed.");
    expect(text.detail).toBe("Add another one first.");
  });

  it("says the role a refused action needs, in the console's own words", () => {
    const text = describeError(
      new ApiError(403, "forbidden", "this needs the admin role on eu-1; you have operator"),
    );
    expect(text.title).toBe("Your role does not allow this.");
    expect(text.detail).toBe("This needs an admin.");
    expect(describeError(new ApiError(403, "forbidden", "this needs a console administrator")).detail).toBe(
      "This needs a console administrator.",
    );
    expect(describeError(new ApiError(403, "forbidden", "something else")).detail).toBeNull();
  });

  it("says it is the leader's limit when the leader names one", () => {
    const text = describeError(
      new ApiError(403, "forbidden", "this needs the admin role; console main is limited to operator"),
      { leader: { name: "us-1", role: "admin" } },
    );
    expect(text.title).toBe("us-1 lets this console act only as an operator.");
    expect(text.detail).toBe("This needs an admin. Whoever runs the leader can change that.");
  });

  it("says it is the leader's limit when the person's own role would have been enough", () => {
    const here = { leader: { name: "us-1", role: "admin" as const } };
    const text = describeError(new ApiError(403, "forbidden", "this needs the admin role"), here);
    expect(text.title).toBe("us-1 lets this console act only up to a lower role.");
    expect(text.detail).toBe("This needs an admin. Whoever runs the leader can change that.");
    // An operator refused something that needs an admin is refused for their own role.
    expect(
      describeError(new ApiError(403, "forbidden", "this needs the admin role"), {
        leader: { name: "us-1", role: "operator" },
      }).title,
    ).toBe("Your role does not allow this.");
  });

  it("names the location a new one overlaps, in its own sentence", () => {
    const text = describeError(
      new ApiError(409, "overlaps", "the folder overlaps location 'intake' (/srv/in); locations must not nest"),
    );
    expect(text.title).toBe("That location overlaps another location.");
    expect(text.detail).toBe("It overlaps intake.");
  });

  it("never shows the server's sentence for a code it knows", () => {
    const SERVER = "zz-server-text is disabled; retry the poll";
    for (const code of Object.keys(ERROR_TITLES)) {
      for (const context of [undefined, { leader: { name: "us-1", role: "viewer" as const } }]) {
        const text = describeError(new ApiError(409, code, SERVER, 15), context);
        expect(`${text.title} ${text.detail ?? ""}`, code).not.toContain("zz-server-text");
        // The protocol's words stay out of the line beneath too.
        expect(text.detail ?? "", code).not.toMatch(/\b(poll|leased|queued|disabled|unreachable|principal|scope|grant|URL)\b/i);
      }
    }
  });

  it("shows what the web app itself says when it refuses before sending", () => {
    const text = describeError(new OwnRefusal("invalid_name", "Letters, digits, . _ - ; at most 100."));
    expect(text.title).toBe("That name is not valid.");
    expect(text.detail).toBe("Letters, digits, . _ - ; at most 100.");
  });

  it("does not say Try again twice when the console is not answering", () => {
    const text = describeError(new ApiError(503, "unavailable", "the database is down", 30));
    expect(`${text.title} ${text.detail}`).toBe(
      "The console is not answering just now. Try again in 30 seconds.",
    );
    expect(describeError(new ApiError(503, "unavailable", "x")).title).toBe(
      "The console is not answering just now. Try again shortly.",
    );
  });

  it("keeps an unknown code's text beneath a calm title, never as the title", () => {
    const text = describeError(new ApiError(409, "something_new", "it clashed"));
    expect(text.title).toBe("That no longer fits how things stand. Refresh, then look again.");
    expect(text.detail).toBe("The answer said: it clashed");
    expect(describeError(new ApiError(418, "teapot", "short and stout")).title).toBe("The console refused that.");
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
