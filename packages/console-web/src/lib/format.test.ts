import { describe, expect, it } from "vitest";
import {
  formatCount,
  formatDuration,
  formatPools,
  formatTime,
  labelPairs,
  oldestQueuedAge,
} from "./format";

describe("format", () => {
  it("formats durations", () => {
    expect(formatDuration(0)).toBe("0 s");
    expect(formatDuration(59.9)).toBe("59 s");
    expect(formatDuration(60)).toBe("1 min");
    expect(formatDuration(3600)).toBe("1 h");
    expect(formatDuration(3600 + 5 * 60)).toBe("1 h 5 min");
    expect(formatDuration(2 * 86400 + 4 * 3600)).toBe("2 d 4 h");
    expect(formatDuration(-5)).toBe("0 s");
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
    expect(formatPools({ gpu: 1, default: 2 })).toBe("default 2 · gpu 1");
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
    expect(formatDuration(Number.NaN)).toBe("0 s");
    expect(formatDuration(Number.POSITIVE_INFINITY)).toBe("0 s");
    expect(formatDuration(-Infinity)).toBe("0 s");
    expect(formatDuration(1e15)).not.toMatch(/NaN|undefined|Infinity/);
    expect(formatTime("garbage", 0)).toBe("garbage");
    expect(oldestQueuedAge(5, "garbage", 0)).toBe(5);
    expect(oldestQueuedAge(5, null, 0)).toBe(5);
    expect(oldestQueuedAge(Number.NaN, null, 0)).toBeNull();
    expect(formatPools({ a: Number.NaN })).toBe("a –");
  });

  it("formats a recent time as a clock and an old one with its date", () => {
    const now = Date.parse("2026-10-04T12:00:00Z");
    expect(formatTime("2026-10-04T11:59:00Z", now)).toMatch(/\d/);
    expect(formatTime("2026-09-01T11:59:00Z", now)).toMatch(/2026/);
  });
});
