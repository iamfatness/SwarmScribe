// The console's /api answers, field for field. Sources of truth:
// - packages/console/src/swarmscribe_console/api/session.py   (SessionOut)
// - packages/console/src/swarmscribe_console/api/fleet.py     (FleetLeader, HistoryPoint)
// - packages/console/src/swarmscribe_console/api/models.py    (admin bodies and answers)
// - packages/leader/src/swarmscribe_leader/api/admin_models.py (proxied leader bodies)
// Datetimes arrive as ISO 8601 strings; UUIDs as strings.

export type Role = "viewer" | "operator" | "admin";
export type Health = "disabled" | "credential_revoked" | "unreachable" | "pending" | "reachable";

export interface ErrorBody {
  code: string;
  message: string;
}

export interface SessionInfo {
  provider: string;
  issuer: string;
  subject: string;
  email: string | null;
  console_admin: boolean;
  csrf_token: string;
  expires_at: string;
  idle_expires_at: string;
}

export interface ProvidersOut {
  providers: string[];
}

// ---- Leader status (C1 + C1b Status), as stored in a snapshot ----

export interface PoolQueue {
  pool: string;
  queued: number;
  leased: number;
}

export interface PoolFollowers {
  pool: string;
  active: number;
  draining: number;
  revoked: number;
  gone: number;
}

export interface LocationStatus {
  name: string;
  backend: string;
  enabled: boolean;
  last_scan_at: string | null;
  last_scan_error: string | null;
  scan_requested: boolean;
  recordings: number;
  consented: number;
}

export interface LeaderStatus {
  jobs: Record<string, number>;
  pools: PoolQueue[];
  followers: Record<string, number>;
  follower_pools: PoolFollowers[];
  completed_last_hour: number;
  completed_last_day: number;
  oldest_queued_age_s: number | null;
  failed_attempts_last_day: number;
  locations: LocationStatus[];
}

// ---- Fleet ----

export interface ScanError {
  location: string;
  error: string;
}

export interface FleetSummary {
  queued: number;
  completed_last_hour: number;
  completed_last_day: number;
  failed_attempts_last_day: number;
  /** As of snapshot.taken_at, by the leader's clock; null when nothing is queued. */
  oldest_queued_age_s: number | null;
  followers_active_by_pool: Record<string, number>;
  scan_errors: ScanError[];
}

export interface SnapshotOut {
  taken_at: string;
  status: LeaderStatus;
}

export interface FleetLeader {
  name: string;
  labels: Record<string, string>;
  role: Role;
  health: Health;
  enabled: boolean;
  last_polled_at: string | null;
  last_success_at: string | null;
  last_error: string | null;
  consecutive_failures: number;
  summary: FleetSummary | null;
  snapshot: SnapshotOut | null;
}

export interface HistoryPoint {
  at: string;
  reachable: boolean;
  queued: number | null;
  leased: number | null;
  completed_last_hour: number | null;
  completed_last_day: number | null;
  failed_attempts_last_day: number | null;
  oldest_queued_age_s: number | null;
  followers_active: number | null;
}

// ---- Proxied leader reads and actions (/api/leaders/{name}/...) ----

export type JobState = "queued" | "leased" | "completed" | "failed" | "cancelled";
export type FollowerState = "active" | "draining" | "revoked" | "gone";
export type RequiredDevice = "any" | "cuda" | "cpu";
export type ChannelMode = "mono" | "stereo_split" | "auto";

export interface LocationOut {
  id: string;
  name: string;
  backend: string;
  root: string | null;
  input_prefix: string;
  output_prefix: string;
  pool: string;
  required_device: string;
  scan_interval_s: number;
  enabled: boolean;
  last_scan_at: string | null;
  last_scan_error: string | null;
  scan_requested: boolean;
  channel_mode: string;
  channel_labels: string[];
}

export interface LocationIn {
  name: string;
  root: string;
  input_prefix?: string;
  output_prefix?: string;
  pool?: string;
  required_device?: RequiredDevice;
  scan_interval_s?: number;
  channel_mode?: ChannelMode;
  channel_labels?: [string, string];
}

export interface JobOut {
  id: string;
  state: string;
  location: string;
  key: string;
  priority: number;
  attempts: number;
  max_attempts: number;
  pool: string;
  leased_by: string | null;
  failure_reason: string | null;
  cancelled_by: string | null;
  no_speech: boolean | null;
  created_at: string;
  completed_at: string | null;
}

export interface PriorityIn {
  priority: number;
}

export interface FollowerOut {
  id: string;
  pool: string;
  state: string;
  device: string | null;
  last_seen_at: string;
  created_at: string;
  leases: number;
}

export interface FollowerRevoked {
  id: string;
  state: string;
  released: number;
}

export interface TokenOut {
  id: string;
  pool: string;
  expires_at: string;
  max_uses: number;
  uses: number;
  revoked: boolean;
  created_by: string;
  created_at: string;
}

export interface TokenIn {
  pool?: string;
  expires_in_seconds?: number;
  max_uses?: number;
}

/** The only answer that carries a join token's plaintext. Never stored; see TokenCreatedDialog. */
export interface TokenCreated {
  id: string;
  token: string;
  pool: string;
  expires_at: string;
  max_uses: number;
}

export interface ScanRequested {
  name: string;
  requested_at: string;
}

export interface ConsentCounts {
  name: string;
  consented: number;
  not_consented: number;
  withdrawn: number;
  missing: number;
}

export interface FlaggedOutputs {
  job_id: string;
  location: string;
  key: string;
  completed_at: string | null;
  output_location: string;
  outputs: string[];
}

export interface ConsentReport {
  locations: ConsentCounts[];
  flagged: FlaggedOutputs[];
  truncated: boolean;
}

// ---- Console administration (/api/admin/...) ----

export interface LeaderOut {
  name: string;
  base_url: string;
  labels: Record<string, string>;
  enabled: boolean;
  added_by: string;
  created_at: string;
  credential_updated_at: string;
  credential_updated_by: string;
  credential_revoked: boolean;
  credential_revoked_at: string | null;
}

// labels and enabled have backend defaults ({} and true), so a body may omit them.
export interface LeaderIn {
  name: string;
  base_url: string;
  labels?: Record<string, string>;
  credential: string;
  enabled?: boolean;
}

/** Only the fields being changed. `credential` only together with a new base_url. */
export interface LeaderEdit {
  base_url?: string;
  labels?: Record<string, string>;
  enabled?: boolean;
  credential?: string;
}

export interface CredentialIn {
  credential: string;
}

export type PrincipalKind = "entra_group" | "google_group" | "email" | "domain";

export interface GrantOut {
  id: string;
  role: string;
  scope: string;
  principal_kind: string;
  principal: string;
  created_by: string;
  created_at: string;
}

export interface GrantIn {
  role: Role;
  scope: string;
  principal_kind: PrincipalKind;
  principal: string;
}

export interface ConsoleAdminOut {
  id: string;
  principal_kind: string;
  principal: string;
  created_by: string;
  created_at: string;
}

export interface ConsoleAdminIn {
  principal_kind: PrincipalKind;
  principal: string;
}
