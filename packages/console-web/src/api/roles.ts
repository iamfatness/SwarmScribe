// The console role each proxied leader route needs: a copy of the allow-list in
// packages/console/src/swarmscribe_console/proxy.py (ROUTES). roles.test.ts reads that file
// and fails when the two differ. The web app disables what the person's role for a leader
// (FleetLeader.role) does not reach and names the role needed; the console and then the
// leader still check (a leader's own cap can refuse what the console allows).

import type { Role } from "./types";

export const ROLE_RANK: Record<Role, number> = { viewer: 0, operator: 1, admin: 2 };

export const ACTION_ROLE = {
  "status.view": "viewer",
  "locations.view": "viewer",
  "jobs.view": "viewer",
  "followers.view": "viewer",
  "tokens.view": "admin",
  "consent.view": "viewer",
  "locations.add": "admin",
  "locations.enable": "admin",
  "locations.disable": "admin",
  "locations.ingest": "operator",
  "jobs.retry": "operator",
  "jobs.cancel": "operator",
  "jobs.priority": "operator",
  "followers.drain": "operator",
  "followers.revoke": "admin",
  "tokens.create": "admin",
  "tokens.revoke": "admin",
} as const satisfies Record<string, Role>;

export type LeaderAction = keyof typeof ACTION_ROLE;

export function atLeast(held: Role, needed: Role): boolean {
  return ROLE_RANK[held] >= ROLE_RANK[needed];
}

export function neededRole(action: LeaderAction): Role {
  return ACTION_ROLE[action];
}

export function can(held: Role, action: LeaderAction): boolean {
  return atLeast(held, ACTION_ROLE[action]);
}
