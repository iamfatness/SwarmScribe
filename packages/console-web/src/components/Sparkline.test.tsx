import { render, screen } from "@testing-library/react";
import { describe as group, expect, it } from "vitest";
import type { HistoryPoint } from "../api/types";
import { NOW, history } from "../test/fixtures";
import { Sparkline, describe, geometry } from "./Sparkline";

function point(minutesAgo: number, value: number | null, reachable = true): HistoryPoint {
  return {
    at: new Date(NOW - minutesAgo * 60_000).toISOString(),
    reachable,
    queued: null,
    leased: null,
    completed_last_hour: value,
    completed_last_day: null,
    failed_attempts_last_day: null,
    oldest_queued_age_s: null,
    followers_active: null,
  };
}

group("Sparkline", () => {
  it("draws one line through consecutive buckets", () => {
    const g = geometry([point(15, 1), point(10, 2), point(5, 4)], NOW);
    expect(g.segments).toHaveLength(1);
    expect(g.latest).toBe(4);
    expect(g.peak).toBe(4);
  });

  it("breaks the line at an unreachable bucket and marks it", () => {
    const g = geometry([point(20, 1), point(15, 2), point(10, null, false), point(5, 3), point(0, 3)], NOW);
    expect(g.segments).toHaveLength(2);
    expect(g.downCount).toBe(1);
    expect(describe(g)).toBe(
      "Jobs completed per hour over the last 24 hours: latest 3, highest 3. Unreachable in 1 five-minute period.",
    );
  });

  it("breaks the line across missing buckets and shows a lone point as a dot", () => {
    const g = geometry([point(60, 1), point(30, 2), point(25, 2)], NOW);
    expect(g.dots).toHaveLength(1);
    expect(g.segments).toHaveLength(1);
  });

  it("ignores points older than the window", () => {
    const g = geometry([point(25 * 60, 50), point(5, 1)], NOW);
    expect(g.peak).toBe(1);
  });

  it("says so when there is no history", () => {
    expect(describe(geometry([], NOW))).toBe("No throughput history yet.");
  });

  it("renders an image with its description as the accessible name", () => {
    render(<Sparkline points={history()} now={NOW} />);
    const chart = screen.getByRole("img");
    expect(chart).toHaveAccessibleName(
      /Jobs completed per hour over the last 24 hours: latest 1, highest 12\./,
    );
    expect(chart).toHaveAccessibleName(/Unreachable in 1 five-minute period\./);
  });

  it("handles a single point", () => {
    const g = geometry([point(5, 7)], NOW);
    expect(g.segments).toHaveLength(0);
    expect(g.dots).toHaveLength(1);
    expect(describe(g)).toContain("latest 7, highest 7");
  });

  it("handles all buckets null (reachable but no value)", () => {
    const g = geometry([point(10, null), point(5, null)], NOW);
    expect(g.segments).toHaveLength(0);
    expect(g.dots).toHaveLength(0);
    expect(g.latest).toBeNull();
    expect(describe(g)).toBe("No throughput history yet.");
    render(<Sparkline points={[point(5, null)]} now={NOW} />);
    expect(screen.getByRole("img")).toHaveAccessibleName("No throughput history yet.");
  });

  it("handles gaps at the start, middle and end", () => {
    const g = geometry(
      [
        point(60, null, false),
        point(55, null, false),
        point(50, 1),
        point(45, 2),
        point(40, null, false),
        point(35, 3),
        point(30, 4),
        point(25, null, false),
      ],
      NOW,
    );
    expect(g.segments).toHaveLength(2);
    expect(g.downCount).toBe(4);
    expect(g.latest).toBe(4);
    expect(describe(g)).toContain("Unreachable in 4 five-minute periods.");
  });

  it("handles only unreachable buckets", () => {
    const g = geometry([point(10, null, false), point(5, null, false)], NOW);
    expect(g.segments).toHaveLength(0);
    expect(describe(g)).toBe(
      "Jobs completed per hour over the last 24 hours: latest unknown, highest 0. Unreachable in 2 five-minute periods.",
    );
  });

  it("handles all zeros without dividing by zero", () => {
    const g = geometry([point(15, 0), point(10, 0), point(5, 0)], NOW);
    expect(g.peak).toBe(0);
    expect(g.latest).toBe(0);
    expect(g.segments[0]).not.toMatch(/NaN|Infinity/);
    expect(describe(g)).toContain("latest 0, highest 0");
  });

  it("handles a very large value", () => {
    const g = geometry([point(10, 1), point(5, 1e12)], NOW);
    expect(g.peak).toBe(1e12);
    expect(g.segments[0]).not.toMatch(/NaN|Infinity/);
  });

  it("uses no style attributes and marks outages by shape, not colour alone", () => {
    const { container } = render(
      <Sparkline points={[point(15, 1), point(10, null, false), point(5, 2)]} now={NOW} />,
    );
    expect(container.querySelector("[style]")).toBeNull();
    expect(container.querySelectorAll("rect.sparkline-down")).toHaveLength(1);
  });
});
