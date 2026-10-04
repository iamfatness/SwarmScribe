import { useId } from "react";
import type { HistoryPoint } from "../api/types";
import { formatCount } from "../lib/format";

// Hand-written SVG (no chart library: the CSP forbids injected <style>). Plots
// completed_last_hour from each 5-minute history bucket: each point is how many jobs the
// leader completed in the hour before that snapshot, so the line is the leader's rolling
// hourly throughput. The line breaks where a bucket is missing or the leader was
// unreachable; unreachable buckets are also marked along the bottom edge.

export const WIDTH = 160;
export const HEIGHT = 36;
const PAD = 3;
const WINDOW_MS = 24 * 60 * 60 * 1000;
const GAP_MS = 10 * 60 * 1000; // more than one missing 5-minute bucket breaks the line

export interface Geometry {
  segments: string[];
  dots: { x: number; y: number }[];
  down: number[];
  latest: number | null;
  peak: number;
  downCount: number;
}

export function geometry(points: HistoryPoint[], now: number): Geometry {
  const start = now - WINDOW_MS;
  // The first bucket can start up to 5 minutes before the window (history is bucketed).
  const inWindow = points.filter((p) => {
    const at = Date.parse(p.at);
    return !Number.isNaN(at) && at >= start - GAP_MS / 2;
  });
  const x = (t: number) =>
    PAD + ((Math.min(Math.max(t, start), now) - start) / WINDOW_MS) * (WIDTH - 2 * PAD);
  const valueOf = (p: HistoryPoint): number | null =>
    p.reachable && p.completed_last_hour !== null && Number.isFinite(p.completed_last_hour)
      ? Math.max(0, p.completed_last_hour)
      : null;
  const values = inWindow.map(valueOf).filter((v): v is number => v !== null);
  const peak = values.length > 0 ? values.reduce((a, b) => Math.max(a, b), 0) : 0;
  const scale = Math.max(1, peak); // all-zero history draws a flat line, not a divide by zero
  const y = (v: number) => HEIGHT - PAD - (v / scale) * (HEIGHT - 2 * PAD);

  const runs: { x: number; y: number }[][] = [];
  let run: { x: number; y: number }[] = [];
  let lastAt = Number.NEGATIVE_INFINITY;
  const down: number[] = [];
  let latest: number | null = null;
  for (const point of inWindow) {
    const at = Date.parse(point.at);
    const value = valueOf(point);
    if (!point.reachable) down.push(Number(x(at).toFixed(1)));
    if (value === null || at - lastAt > GAP_MS) {
      if (run.length > 0) runs.push(run);
      run = [];
    }
    if (value !== null) {
      run.push({ x: Number(x(at).toFixed(1)), y: Number(y(value).toFixed(1)) });
      latest = value;
    }
    lastAt = at;
  }
  if (run.length > 0) runs.push(run);

  return {
    segments: runs
      .filter((r) => r.length > 1)
      .map((r) => r.map((p, i) => `${i === 0 ? "M" : "L"}${p.x} ${p.y}`).join(" ")),
    dots: runs.filter((r) => r.length === 1).map((r) => r[0] as { x: number; y: number }),
    down,
    latest,
    peak,
    downCount: down.length,
  };
}

export function describe(g: Geometry): string {
  if (g.latest === null && g.downCount === 0) return "No throughput history yet.";
  const parts = [
    `Jobs completed per hour over the last 24 hours: latest ${g.latest === null ? "unknown" : formatCount(g.latest)}, highest ${formatCount(g.peak)}.`,
  ];
  if (g.downCount > 0) {
    parts.push(`Unreachable in ${g.downCount} five-minute ${g.downCount === 1 ? "period" : "periods"}.`);
  }
  return parts.join(" ");
}

/** `name` (the leader) leads the accessible name, so each chart in a table is told apart. */
export function Sparkline({ points, now, name }: { points: HistoryPoint[]; now: number; name?: string }) {
  const titleId = useId();
  const g = geometry(points, now);
  return (
    <svg
      className="sparkline"
      role="img"
      aria-labelledby={titleId}
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      width={WIDTH}
      height={HEIGHT}
      preserveAspectRatio="none"
    >
      <title id={titleId}>{name === undefined ? describe(g) : `${name}: ${describe(g)}`}</title>
      <line className="sparkline-axis" x1={PAD} x2={WIDTH - PAD} y1={HEIGHT - PAD} y2={HEIGHT - PAD} />
      {g.segments.map((d) => (
        <path key={d} className="sparkline-line" d={d} />
      ))}
      {g.dots.map((p) => (
        <circle key={`${p.x},${p.y}`} className="sparkline-dot" cx={p.x} cy={p.y} r={1.5} />
      ))}
      {g.down.map((dx) => (
        <rect key={dx} className="sparkline-down" x={dx - 0.75} y={HEIGHT - PAD} width={1.5} height={PAD} />
      ))}
    </svg>
  );
}
