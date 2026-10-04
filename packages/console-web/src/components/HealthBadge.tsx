import type { FleetLeader } from "../api/types";

type Tone = "ok" | "warn" | "bad" | "muted";

export function healthText(leader: Pick<FleetLeader, "health" | "consecutive_failures">): {
  label: string;
  tone: Tone;
} {
  const failures = leader.consecutive_failures;
  const failed = failures === 1 ? "1 failed poll" : `${failures} failed polls`;
  switch (leader.health) {
    case "reachable":
      return failures > 0
        ? { label: `Reachable (${failed})`, tone: "warn" }
        : { label: "Reachable", tone: "ok" };
    case "pending":
      return failures > 0
        ? { label: `Not answering yet (${failed})`, tone: "warn" }
        : { label: "Waiting for first poll", tone: "muted" };
    case "unreachable":
      return { label: "Unreachable", tone: "bad" };
    case "credential_revoked":
      return { label: "Credential revoked", tone: "bad" };
    case "disabled":
      return { label: "Disabled in the console", tone: "muted" };
    default:
      return { label: String(leader.health), tone: "muted" };
  }
}

/** Health as words in a coloured badge: the colour is never the only signal. */
export function HealthBadge({ leader }: { leader: Pick<FleetLeader, "health" | "consecutive_failures"> }) {
  const { label, tone } = healthText(leader);
  return <span className={`badge badge-${tone}`}>{label}</span>;
}
