import { describe, expect, it } from "vitest";
import {
  formatCount,
  formatDuration,
  countOf,
  describeLastError,
  formatPools,
  formatTries,
  formatTime,
  labelPairs,
  oldestQueuedAge,
} from "./format";

// The words as a person reads them: the no-break spaces (a figure and its unit) are spaces.
// src/lib/nbsp.test.ts checks the no-break space itself.
const words = (text: string): string => text.replaceAll("\u00a0", " ");

describe("format", () => {
  it("formats durations", () => {
    expect(words(formatDuration(0))).toBe("0 s");
    expect(words(formatDuration(59.9))).toBe("59 s");
    expect(words(formatDuration(60))).toBe("1 min");
    expect(words(formatDuration(3600))).toBe("1 h");
    expect(words(formatDuration(3600 + 5 * 60))).toBe("1 h 5 min");
    expect(words(formatDuration(2 * 86400 + 4 * 3600))).toBe("2 d 4 h");
    expect(words(formatDuration(-5))).toBe("0 s");
  });

  it("adds the time since the snapshot to the oldest queued age", () => {
    const takenAt = "2026-10-04T10:00:00Z";
    const now = Date.parse("2026-10-04T10:00:30Z");
    expect(oldestQueuedAge(420, takenAt, now)).toBe(450);
    expect(oldestQueuedAge(null, takenAt, now)).toBeNull();
    // A snapshot "in the future" (clock skew) adds nothing rather than subtracting.
    expect(oldestQueuedAge(420, "2026-10-04T10:01:00Z", now)).toBe(420);
  });

  it("formats pools and labels", () => {
    expect(formatPools({ gpu: 1, default: 2 })).toBe("default 2, gpu 1");
    expect(formatPools({})).toBe("none");
    expect(labelPairs({ region: "eu", env: "prod" })).toEqual(["env=prod", "region=eu"]);
    expect(formatCount(null)).toBe("–");
  });

  it("never prints NaN or undefined", () => {
    expect(formatCount(undefined)).toBe("–");
    expect(formatCount(Number.NaN)).toBe("–");
    expect(formatCount(Number.POSITIVE_INFINITY)).toBe("–");
    expect(formatCount(-3)).toBe("-3");
    expect(formatCount(1_234_567_890_123)).not.toMatch(/NaN|undefined/);
    expect(words(formatDuration(Number.NaN))).toBe("0 s");
    expect(words(formatDuration(Number.POSITIVE_INFINITY))).toBe("0 s");
    expect(words(formatDuration(-Infinity))).toBe("0 s");
    expect(formatDuration(1e15)).not.toMatch(/NaN|undefined|Infinity/);
    expect(formatTime("garbage", 0)).toBe("garbage");
    expect(oldestQueuedAge(5, "garbage", 0)).toBe(5);
    expect(oldestQueuedAge(5, null, 0)).toBe(5);
    expect(oldestQueuedAge(Number.NaN, null, 0)).toBeNull();
    expect(formatPools({ a: Number.NaN })).toBe("a –");
  });

  it("says a small number of tries in words and a large one in digits", () => {
    expect(words(formatTries(1))).toBe("one try");
    expect(words(formatTries(3))).toBe("three tries");
    expect(words(formatTries(9))).toBe("nine tries");
    expect(words(formatTries(10))).toBe("10 tries");
    expect(words(formatTries(0))).toBe("no tries");
    expect(words(formatTries(Number.NaN))).toBe("no tries");
    expect(words(formatTries(-2))).toBe("no tries");
    expect(words(formatTries(2.9))).toBe("two tries");
  });

  it("counts a noun in the singular and the plural", () => {
    expect(words(countOf(1, "leader"))).toBe("1 leader");
    expect(words(countOf(0, "follower"))).toBe("0 followers");
    expect(words(countOf(1234, "follower"))).toBe(`${formatCount(1234)} followers`);
  });

  it("formats a recent time as a clock and an old one with its date", () => {
    const now = Date.parse("2026-10-04T12:00:00Z");
    expect(formatTime("2026-10-04T11:59:00Z", now)).toMatch(/\d/);
    expect(formatTime("2026-09-01T11:59:00Z", now)).toMatch(/2026/);
  });

  it("says a leader's last error in words and never shows its code", () => {
    expect(describeLastError("connect_error")).toBe("It could not be reached.");
    expect(describeLastError("timeout")).toBe("It took too long to answer.");
    expect(describeLastError("http_502")).toBe("It answered with an error (502).");
    expect(describeLastError("something_new")).toBe("The check did not work.");
    expect(describeLastError("something_new")).not.toContain("_");
  });
});
