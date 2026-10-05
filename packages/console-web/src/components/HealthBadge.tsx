import type { FleetLeader } from "../api/types";
import { formatTries } from "../lib/format";

export type Tone = "ok" | "warn" | "bad" | "muted";

type HealthOf = Pick<FleetLeader, "health" | "consecutive_failures">;

/** A leader's health in the console's words, and how loudly to show it. */
export function healthText(leader: HealthOf): { label: string; tone: Tone } {
  const failures = leader.consecutive_failures;
  switch (leader.health) {
    case "reachable":
      return failures > 0
        ? {
            label: `Answering, ${failures === 1 ? "1 missed check" : `${failures} missed checks`}`,
            tone: "warn",
          }
        : { label: "Answering", tone: "ok" };
    case "pending":
      return failures > 0
        ? { label: `Not answering yet, ${formatTries(failures)}`, tone: "warn" }
        : { label: "Waiting for the first check", tone: "muted" };
    case "unreachable":
      return { label: "Not answering", tone: "bad" };
    case "credential_revoked":
      return { label: "Credential revoked", tone: "bad" };
    case "disabled":
      return { label: "Switched off", tone: "muted" };
    default:
      return { label: String(leader.health), tone: "muted" };
  }
}

/** The mark beside a status: a hexagon while the leader answers, a ring when it does not. */
export function StatusMark({ tone }: { tone: Tone }) {
  if (tone === "ok") return <span className="mark mark-hex" aria-hidden="true" />;
  if (tone === "warn") return <span className="mark mark-hex mark-quiet" aria-hidden="true" />;
  return (
    <span className={tone === "bad" ? "mark mark-ring mark-bad" : "mark mark-ring"} aria-hidden="true" />
  );
}

/** Health as a shape and words in a pill: colour is never the only signal. */
export function HealthBadge({ leader }: { leader: HealthOf }) {
  const { label, tone } = healthText(leader);
  return (
    <span className={`status-pill status-pill-${tone}`}>
      <StatusMark tone={tone} />
      {label}
    </span>
  );
}
