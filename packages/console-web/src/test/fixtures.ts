import type { FleetLeader, HistoryPoint, LeaderStatus, SessionInfo } from "../api/types";

export const NOW = Date.parse("2026-10-04T12:00:00Z");

export const SESSION: SessionInfo = {
  provider: "entra",
  issuer: "https://login.microsoftonline.com/tenant/v2.0",
  subject: "person-1",
  email: "person@example.org",
  console_admin: false,
  csrf_token: "csrf-token-1",
  expires_at: "2026-10-04T20:00:00Z",
  idle_expires_at: "2026-10-04T13:00:00Z",
};

export const STATUS: LeaderStatus = {
  jobs: { queued: 3, leased: 1, completed: 10, failed: 1, cancelled: 0 },
  pools: [
    { pool: "default", queued: 2, leased: 1 },
    { pool: "gpu", queued: 1, leased: 0 },
  ],
  followers: { active: 3, draining: 1, revoked: 0, gone: 0 },
  follower_pools: [
    { pool: "default", active: 2, draining: 1, revoked: 0, gone: 0 },
    { pool: "gpu", active: 1, draining: 0, revoked: 0, gone: 0 },
  ],
  completed_last_hour: 7,
  completed_last_day: 30,
  oldest_queued_age_s: 420,
  failed_attempts_last_day: 1,
  locations: [
    {
      name: "intake",
      backend: "local",
      enabled: true,
      last_scan_at: "2026-10-04T11:59:00Z",
      last_scan_error: null,
      scan_requested: false,
      recordings: 12,
      consented: 10,
    },
    {
      name: "archive",
      backend: "local",
      enabled: true,
      last_scan_at: "2026-10-04T11:58:00Z",
      last_scan_error: "the root folder is not readable",
      scan_requested: false,
      recordings: 0,
      consented: 0,
    },
  ],
};

export function leader(overrides: Partial<FleetLeader> = {}): FleetLeader {
  return {
    name: "eu-1",
    labels: { env: "prod", region: "eu" },
    role: "operator",
    health: "reachable",
    enabled: true,
    last_polled_at: "2026-10-04T11:59:50Z",
    last_success_at: "2026-10-04T11:59:50Z",
    last_error: null,
    consecutive_failures: 0,
    summary: {
      queued: 3,
      completed_last_hour: 7,
      completed_last_day: 30,
      failed_attempts_last_day: 1,
      oldest_queued_age_s: 420,
      followers_active_by_pool: { default: 2, gpu: 1 },
      scan_errors: [{ location: "archive", error: "the root folder is not readable" }],
    },
    snapshot: { taken_at: "2026-10-04T11:59:50Z", status: STATUS },
    ...overrides,
  };
}

/** 24 hours of 5-minute buckets ending at NOW, with one unreachable bucket. */
export function history(): HistoryPoint[] {
  const points: HistoryPoint[] = [];
  for (let i = 287; i >= 0; i -= 1) {
    const at = new Date(NOW - i * 5 * 60 * 1000).toISOString();
    const down = i === 100;
    points.push({
      at,
      reachable: !down,
      queued: down ? null : 3,
      leased: down ? null : 1,
      completed_last_hour: down ? null : (i % 12) + 1,
      completed_last_day: down ? null : 30,
      failed_attempts_last_day: down ? null : 1,
      oldest_queued_age_s: down ? null : 420,
      followers_active: down ? null : 3,
    });
  }
  return points;
}
