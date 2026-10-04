import type { ReactNode } from "react";
import { describeError } from "../api/errors";
import type { FleetLeader } from "../api/types";
import { useFleet } from "../app/fleet";
import { useNavigate, useSearchParam } from "../app/router";
import { useNow } from "../app/useNow";
import { usePageTitle } from "../app/usePageTitle";
import { ErrorPanel } from "../components/ErrorPanel";
import { HealthBadge } from "../components/HealthBadge";
import { ThroughputChart } from "../components/ThroughputChart";
import {
  formatCount,
  formatDuration,
  formatPools,
  formatTime,
  labelPairs,
  oldestQueuedAge,
} from "../lib/format";

function matchesLabel(leader: FleetLeader, label: string | null): boolean {
  if (!label) return true;
  const at = label.indexOf("=");
  if (at < 1) return false;
  return leader.labels[label.slice(0, at)] === label.slice(at + 1);
}

function LabelFilter({ leaders, value }: { leaders: FleetLeader[]; value: string | null }) {
  const navigate = useNavigate();
  const pairs = [...new Set(leaders.flatMap((leader) => labelPairs(leader.labels)))].sort();
  if (value && !pairs.includes(value)) pairs.unshift(value);
  return (
    <label className="field-inline">
      Label
      <select
        value={value ?? ""}
        onChange={(event) => {
          const next = event.target.value;
          navigate(next ? `/?label=${encodeURIComponent(next)}` : "/", { replace: true });
        }}
      >
        <option value="">All leaders</option>
        {pairs.map((pair) => (
          <option key={pair} value={pair}>
            {pair}
          </option>
        ))}
      </select>
    </label>
  );
}

function HealthCell({ leader, now }: { leader: FleetLeader; now: number }) {
  const stale = leader.health !== "reachable" && leader.snapshot !== null;
  return (
    <>
      <HealthBadge leader={leader} />
      {stale && leader.snapshot !== null && (
        <span className="cell-note">Figures as of {formatTime(leader.snapshot.taken_at, now)}</span>
      )}
      {leader.summary === null && <span className="cell-note">No successful poll yet</span>}
      {leader.health === "unreachable" && leader.last_error && (
        <span className="cell-note">Last error: {leader.last_error}</span>
      )}
    </>
  );
}

function ScanErrors({ leader }: { leader: FleetLeader }) {
  const errors = leader.summary?.scan_errors ?? [];
  if (errors.length === 0) return <span className="muted">None</span>;
  return (
    <ul className="cell-list">
      {errors.map((item) => (
        <li key={item.location}>
          <strong>{item.location}</strong>: {item.error}
        </li>
      ))}
    </ul>
  );
}

/** The leader's name. C3b turns it into the link to the leader's drill-down. */
export function LeaderName({ leader }: { leader: FleetLeader }): ReactNode {
  return <span className="leader-name">{leader.name}</span>;
}

function LeaderRow({ leader, now }: { leader: FleetLeader; now: number }) {
  const s = leader.summary;
  const stale = leader.health !== "reachable" && s !== null;
  const age =
    s === null ? null : oldestQueuedAge(s.oldest_queued_age_s, leader.snapshot?.taken_at ?? null, now);
  return (
    <tr className={stale ? "is-stale" : undefined}>
      <th scope="row">
        <LeaderName leader={leader} />
        <ul className="labels" aria-label="Labels">
          {labelPairs(leader.labels).map((pair) => (
            <li key={pair} className="label-chip">
              {pair}
            </li>
          ))}
        </ul>
      </th>
      <td>
        <HealthCell leader={leader} now={now} />
      </td>
      <td className="num">{formatCount(s?.queued)}</td>
      <td className="num">{formatCount(s?.completed_last_hour)}</td>
      <td className="num">{formatCount(s?.completed_last_day)}</td>
      <td className="num">{formatCount(s?.failed_attempts_last_day)}</td>
      <td>{s === null ? "–" : formatPools(s.followers_active_by_pool)}</td>
      <td>{s === null ? "–" : age === null ? "Nothing queued" : formatDuration(age)}</td>
      <td>
        <ScanErrors leader={leader} />
      </td>
      <td>
        <ThroughputChart name={leader.name} now={now} />
      </td>
    </tr>
  );
}

export function FleetPage() {
  usePageTitle("Fleet");
  const { data, error, updatedAt, refresh } = useFleet();
  const label = useSearchParam("label");
  const now = useNow(5_000);

  if (data === undefined) {
    return (
      <>
        <h1>Fleet</h1>
        {error ? <ErrorPanel error={error} onRetry={refresh} /> : <p role="status">Loading the fleet…</p>}
      </>
    );
  }

  const shown = data.filter((leader) => matchesLabel(leader, label));
  return (
    <>
      <div className="page-head">
        <h1>Fleet</h1>
        <LabelFilter leaders={data} value={label} />
      </div>
      <p className="muted" aria-live="polite">
        {shown.length === data.length
          ? `${data.length} ${data.length === 1 ? "leader" : "leaders"}`
          : `${shown.length} of ${data.length} leaders`}
        {updatedAt !== null && ` · updated ${formatTime(new Date(updatedAt).toISOString(), now)}`}
      </p>
      {error !== null && (
        <p className="notice" role="alert">
          Could not refresh the fleet: {describeError(error).title} Showing the last figures.
        </p>
      )}
      {data.length === 0 ? (
        <p>You hold no role on any leader yet. Ask a console administrator for a grant.</p>
      ) : shown.length === 0 ? (
        <p>No leader has the label {label}.</p>
      ) : (
        <div className="table-scroll" role="region" aria-label="Leaders" tabIndex={0}>
          <table className="fleet-table">
            <caption className="visually-hidden">
              Leaders with their health, queue, throughput, followers and scan errors
            </caption>
            <thead>
              <tr>
                <th scope="col">Leader</th>
                <th scope="col">Health</th>
                <th scope="col">Queued</th>
                <th scope="col">Completed, last hour</th>
                <th scope="col">Completed, last day</th>
                <th scope="col">Failed attempts, last day</th>
                <th scope="col">Active followers by pool</th>
                <th scope="col">Oldest queued job (since created)</th>
                <th scope="col">Last scan errors</th>
                <th scope="col">Throughput, 24 hours</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((leader) => (
                <LeaderRow key={leader.name} leader={leader} now={now} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
