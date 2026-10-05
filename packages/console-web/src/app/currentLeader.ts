import { createContext, useContext } from "react";
import type { FleetLeader } from "../api/types";

/**
 * The leader whose page this is, for what is shown inside it: an error there can name the
 * leader and knows the person's role on it, without every tab passing both down. Null
 * anywhere that is not a leader's page.
 */
export const CurrentLeader = createContext<FleetLeader | null>(null);

export function useCurrentLeader(): FleetLeader | null {
  return useContext(CurrentLeader);
}
