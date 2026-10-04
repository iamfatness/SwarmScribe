import { createContext, useContext, type ReactNode } from "react";
import { api } from "../api/client";
import type { FleetLeader } from "../api/types";
import { usePoll, type PollState } from "./usePoll";

/**
 * Every 10 s. The poller records a dead leader's third failure within about 50 s of its
 * death, so a 10 s refresh shows it unreachable inside spec 1's one minute.
 */
export const FLEET_REFRESH_MS = 10_000;

const FleetContext = createContext<PollState<FleetLeader[]> | null>(null);

const loadFleet = (signal: AbortSignal) => api.get<FleetLeader[]>("/api/fleet", signal);

/** GET /api/fleet for the whole app: the overview and each leader's drill-down header. */
export function FleetProvider({ children }: { children: ReactNode }) {
  const fleet = usePoll(loadFleet, FLEET_REFRESH_MS, "fleet");
  return <FleetContext.Provider value={fleet}>{children}</FleetContext.Provider>;
}

export function useFleet(): PollState<FleetLeader[]> {
  const value = useContext(FleetContext);
  if (value === null) throw new Error("useFleet outside FleetProvider");
  return value;
}
