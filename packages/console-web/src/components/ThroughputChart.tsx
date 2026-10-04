import { useCallback } from "react";
import { api, leaderPath } from "../api/client";
import type { HistoryPoint } from "../api/types";
import { usePoll } from "../app/usePoll";
import { Sparkline } from "./Sparkline";

/** History moves in 5-minute buckets, so it is read every 5 minutes. */
export const HISTORY_REFRESH_MS = 5 * 60 * 1000;

/**
 * One leader's 24-hour throughput, from GET /api/leaders/{name}/history?hours=24. A failed
 * request only affects this cell: the row and the table stay.
 */
export function ThroughputChart({ name, now }: { name: string; now: number }) {
  const load = useCallback(
    (signal: AbortSignal) => api.get<HistoryPoint[]>(leaderPath(name, "history?hours=24"), signal),
    [name],
  );
  const { data, error } = usePoll(load, HISTORY_REFRESH_MS, `history:${name}`);
  if (data === undefined) {
    return <span className="muted">{error ? "History unavailable" : "Loading…"}</span>;
  }
  return <Sparkline points={data} now={now} name={name} />;
}
