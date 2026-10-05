import { useId, useState, type ReactNode } from "react";
import { describeError } from "../api/errors";
import type { FleetLeader } from "../api/types";
import { FLEET_REFRESH_MS, useFleet } from "../app/fleet";
import { Link, useNavigate, useSearchParam } from "../app/router";
import { useNow } from "../app/useNow";
import { usePageTitle } from "../app/usePageTitle";
import { ErrorPanel } from "../components/ErrorPanel";
import { HealthBadge, healthText } from "../components/HealthBadge";
import { ThroughputChart } from "../components/ThroughputChart";
import {
  countOf,
  describeLastError,
  formatCount,
  formatDuration,
  formatPools,
  formatTime,
  formatTries,
  labelPairs,
  oldestQueuedAge,
  withUnit,
} from "../lib/format";
import { leaderUrl } from "./leader/tabs";

/** With more label pairs than this the pills would fill the page: a select takes over. */
export const LABEL_PILL_LIMIT = 6;

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
  const choose = (next: string) =>
    navigate(next ? `/?label=${encodeURIComponent(next)}` : "/", { replace: true });
  if (pairs.length === 0) return null;
  if (pairs.length > LABEL_PILL_LIMIT) {
    return (
      <label className="field-inline">
        Label
        <select value={value ?? ""} onChange={(event) => choose(event.target.value)}>
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
  return (
    <div className="pill-group" role="group" aria-label="Show leaders with the label">
      <button type="button" className="pill" aria-pressed={!value} onClick={() => choose("")}>
        All leaders
      </button>
      {pairs.map((pair) => (
        <button
          key={pair}
          type="button"
          className="pill"
          aria-pressed={value === pair}
          onClick={() => choose(value === pair ? "" : pair)}
        >
          {pair.replace("=", " = ")}
        </button>
      ))}
    </div>
  );
}

function followersAtWork(leader: FleetLeader): number {
  return Object.values(leader.summary?.followers_active_by_pool ?? {}).reduce((sum, n) => sum + n, 0);
}

/** "a", "a and b", "a, b and c". */
function listOf(names: string[]): string {
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} and ${names.at(-1) ?? ""}`;
}

function Totals({ leaders }: { leaders: FleetLeader[] }) {
  const known = leaders.filter((leader) => leader.summary !== null);
  const sum = (pick: (leader: FleetLeader) => number): number | null =>
    known.length === 0 ? null : known.reduce((total, leader) => total + pick(leader), 0);
  const old = known.filter((leader) => leader.health !== "reachable").map((leader) => leader.name);
  return (
    <section aria-label="Totals">
      <dl className="stat-row">
        <div className="stat-tile">
          <dt>Waiting now</dt>
          <dd>{formatCount(sum((leader) => leader.summary?.queued ?? 0))}</dd>
        </div>
        <div className="stat-tile">
          <dt>Finished, last hour</dt>
          <dd>{formatCount(sum((leader) => leader.summary?.completed_last_hour ?? 0))}</dd>
        </div>
        <div className="stat-tile">
          <dt>Finished, last day</dt>
          <dd>{formatCount(sum((leader) => leader.summary?.completed_last_day ?? 0))}</dd>
        </div>
        <div className="stat-tile">
          <dt>Followers at work</dt>
          <dd>{formatCount(sum(followersAtWork))}</dd>
        </div>
      </dl>
      {old.length > 0 && (
        <p className="stat-note">
          These include the last figures from {listOf(old)}, which the console cannot check right now.
        </p>
      )}
    </section>
  );
}

interface Concern {
  key: string;
  /** 0 is the most serious: the order the items are listed in. */
  rank: number;
  lead: string;
  rest: string;
  /** Where the lead links to, when one place explains it. */
  to?: string;
}

/** "1 on eu-1, 2 on us-1": the first five leaders, then how many more there are. */
function failedOn(failing: FleetLeader[]): string {
  const part = (leader: FleetLeader) => `${formatCount(leader.summary?.failed_attempts_last_day)} on ${leader.name}`;
  const shown = failing.slice(0, 5).map(part).join(", ");
  const more = failing.length - 5;
  return more > 0 ? `${shown} and ${more} more leaders.` : `${shown}.`;
}

/**
 * What a person should look at, most serious first: a revoked credential, a leader that is
 * not answering, failed tries, then scan errors. Within a kind the fleet's own order holds.
 */
export function concerns(leaders: FleetLeader[]): Concern[] {
  const found: Concern[] = [];
  for (const leader of leaders) {
    if (leader.health === "credential_revoked") {
      found.push({
        key: `${leader.name}/revoked`,
        rank: 0,
        lead: `${leader.name} revoked this console's credential.`,
        rest: "A console administrator must replace it.",
      });
    }
    if (leader.health === "unreachable") {
      found.push({
        key: `${leader.name}/unreachable`,
        rank: 1,
        lead: `${leader.name} is not answering.`,
        rest: "The figures here are from the last time it answered.",
      });
    }
    for (const item of leader.summary?.scan_errors ?? []) {
      found.push({
        key: `${leader.name}/scan/${item.location}`,
        rank: 3,
        lead: `${leader.name} could not scan ${item.location}.`,
        rest: item.error,
        to: leaderUrl(leader.name, "locations"),
      });
    }
  }
  const failing = leaders.filter((leader) => (leader.summary?.failed_attempts_last_day ?? 0) > 0);
  const failed = failing.reduce((sum, leader) => sum + (leader.summary?.failed_attempts_last_day ?? 0), 0);
  if (failed > 0) {
    found.push({
      key: "failed",
      rank: 2,
      lead: `${formatCount(failed)} ${failed === 1 ? "try" : "tries"} failed in the last day.`,
      rest: failedOn(failing),
    });
  }
  return found.sort((a, b) => a.rank - b.rank);
}

/** How many items "Needs a look" shows before the rest are opened with a button. */
export const NEEDS_LOOK_SHOWN = 6;

/** A name too long to fit a line cannot be kept whole: it breaks like any long word. */
const NAME_KEPT_WHOLE = 24;

/** Text with each leader's name kept whole: a name like eu-1 never breaks at its hyphen. */
function withNames(text: string, names: string[]): ReactNode {
  const kept = names.filter((name) => name.length <= NAME_KEPT_WHOLE);
  if (kept.length === 0) return text;
  const escaped = kept
    .slice()
    .sort((a, b) => b.length - a.length)
    .map((name) => name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  // Only a whole name: not part of a longer word or name.
  const pattern = new RegExp(`(?<![\\w-])(${escaped.join("|")})(?![\\w-])`);
  return text.split(pattern).map((part, i) =>
    // The odd pieces are the captured names.
    i % 2 === 1 ? (
      <span key={i} className="nowrap">
        {part}
      </span>
    ) : (
      part
    ),
  );
}

function NeedsALook({ leaders }: { leaders: FleetLeader[] }) {
  const headingId = useId();
  const listId = useId();
  const [all, setAll] = useState(false);
  const [said, setSaid] = useState("");
  const items = concerns(leaders);
  if (items.length === 0) return null;
  const names = leaders.map((leader) => leader.name);
  const more = items.length > NEEDS_LOOK_SHOWN;
  const listed = all ? items : items.slice(0, NEEDS_LOOK_SHOWN);
  const toggle = () => {
    setSaid(
      all
        ? `Showing the first ${NEEDS_LOOK_SHOWN} of ${items.length}.`
        : `Showing all ${items.length} things to look at.`,
    );
    setAll(!all);
  };
  return (
    <section className="sheet needs-look" aria-labelledby={headingId}>
      <h2 id={headingId} className="sheet-title">
        Needs a look
      </h2>
      <ul id={listId} className="needs-look-list">
        {listed.map((item) => (
          <li key={item.key}>
            <strong>
              {item.to === undefined ? (
                withNames(item.lead, names)
              ) : (
                <Link to={item.to}>{withNames(item.lead, names)}</Link>
              )}
            </strong>{" "}
            <span className="muted long">{withNames(item.rest, names)}</span>
          </li>
        ))}
      </ul>
      {more && (
        <button
          type="button"
          className="button needs-look-toggle"
          aria-expanded={all}
          aria-controls={listId}
          onClick={toggle}
        >
          {all ? "Show fewer" : `Show all ${items.length}`}
        </button>
      )}
      <p className="visually-hidden" role="status">
        {said}
      </p>
    </section>
  );
}

function lastFigures(leader: FleetLeader, now: number): string | null {
  if (leader.summary === null || leader.snapshot === null) return null;
  return (
    `The last figures are from ${formatTime(leader.snapshot.taken_at, now)}: ` +
    `${formatCount(leader.summary.queued)} waiting, ${countOf(followersAtWork(leader), "follower")}.`
  );
}

/** A leader the console has no fresh figures for: what happened, and what is still known. */
function QuietLeader({ leader, now }: { leader: FleetLeader; now: number }) {
  const tries = formatTries(leader.consecutive_failures);
  const figures = lastFigures(leader, now);
  let what: string;
  let more: string | null = null;
  switch (leader.health) {
    case "unreachable":
      what =
        leader.last_success_at === null
          ? `No answer yet, after ${tries}.`
          : `No answer since ${formatTime(leader.last_success_at, now)}, after ${tries}.`;
      more = "Recordings already claimed keep going; this console just cannot see them.";
      break;
    case "credential_revoked":
      what = "This leader revoked the console's credential. A console administrator must replace it.";
      more = "The leader itself keeps working; this console just cannot see it.";
      break;
    case "disabled":
      what = "This leader is switched off in the console, so nothing is asked of it.";
      break;
    case "pending":
      what =
        leader.consecutive_failures > 0
          ? `No answer yet, after ${tries}.`
          : "The first check has not come back yet.";
      break;
    default:
      what = "The console has no figures for this leader.";
  }
  return (
    <>
      <p className="leader-card-what">{what}</p>
      {figures === null ? (
        <p className="leader-card-more">Figures appear after the first check that works.</p>
      ) : (
        <p className="leader-card-more">
          {figures}
          {more !== null && ` ${more}`}
        </p>
      )}
      {leader.health === "unreachable" && leader.last_error && (
        <p className="leader-card-more long">Last error: {describeLastError(leader.last_error)}</p>
      )}
      {leader.snapshot !== null && (
        <p className="leader-card-link">
          <Link to={leaderUrl(leader.name)}>See what {leader.name} last reported</Link>
        </p>
      )}
    </>
  );
}

function LeaderFigures({ leader, now }: { leader: FleetLeader; now: number }) {
  const s = leader.summary;
  if (s === null) return null;
  const age = oldestQueuedAge(s.oldest_queued_age_s, leader.snapshot?.taken_at ?? null, now);
  const followers = followersAtWork(leader);
  return (
    <>
      <div className="leader-card-chart">
        <ThroughputChart name={leader.name} now={now} />
      </div>
      <dl className="figure-row">
        <div>
          <dt>Waiting</dt>
          <dd>{formatCount(s.queued)}</dd>
        </div>
        <div>
          <dt>
            Last hour<span className="visually-hidden">, finished</span>
          </dt>
          <dd>{formatCount(s.completed_last_hour)}</dd>
        </div>
        <div>
          <dt>
            Last day<span className="visually-hidden">, finished</span>
          </dt>
          <dd>{formatCount(s.completed_last_day)}</dd>
        </div>
        <div>
          <dt>
            Failed<span className="visually-hidden"> tries, last day</span>
          </dt>
          <dd>{formatCount(s.failed_attempts_last_day)}</dd>
        </div>
      </dl>
      <p className="leader-card-foot">
        {followers === 0 ? (
          <strong>No followers at work</strong>
        ) : (
          <>
            <strong>{countOf(followers, "follower")}</strong> · {formatPools(s.followers_active_by_pool)}
          </>
        )}{" "}
        · {age === null ? "nothing waiting" : `oldest waiting ${formatDuration(age)}`}
      </p>
    </>
  );
}

function LeaderCard({ leader, now }: { leader: FleetLeader; now: number }) {
  const headingId = useId();
  const { tone } = healthText(leader);
  const fresh = leader.health === "reachable" && leader.summary !== null;
  const pairs = labelPairs(leader.labels);
  return (
    <article className={`panel leader-card leader-card-${tone}`} aria-labelledby={headingId}>
      <div className="leader-card-head">
        <div className="leader-card-title">
          <h2 id={headingId} className="leader-card-name">
            <Link to={leaderUrl(leader.name)}>{leader.name}</Link>
          </h2>
          {pairs.length > 0 && (
            <ul className="labels" aria-label="Labels">
              {pairs.map((pair) => (
                <li key={pair}>{pair}</li>
              ))}
            </ul>
          )}
        </div>
        <HealthBadge leader={leader} />
      </div>
      {fresh ? <LeaderFigures leader={leader} now={now} /> : <QuietLeader leader={leader} now={now} />}
    </article>
  );
}

/** Empty shapes where the totals and cards will be, so the page does not jump when they arrive. */
function FleetPlaceholder() {
  return (
    <div aria-hidden="true">
      <div className="stat-row">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="stat-tile placeholder">
            <span className="placeholder-bar placeholder-bar-short" />
            <span className="placeholder-bar placeholder-bar-figure" />
          </div>
        ))}
      </div>
      <div className="fleet-grid">
        {[0, 1].map((i) => (
          <div key={i} className="panel leader-card placeholder">
            <span className="placeholder-bar placeholder-bar-short" />
            <span className="placeholder-bar placeholder-bar-chart" />
            <span className="placeholder-bar" />
          </div>
        ))}
      </div>
    </div>
  );
}

export function FleetPage() {
  usePageTitle("Fleet");
  const { data, error, updatedAt, refresh } = useFleet();
  const label = useSearchParam("label");
  const now = useNow(5_000);

  // One heading element for the loading and the loaded page: focus put on it after a
  // navigation survives the fleet arriving.
  const shown = (data ?? []).filter((leader) => matchesLabel(leader, label));
  return (
    <>
      <div className="page-head fleet-head">
        <div>
          <h1>Fleet</h1>
          {data !== undefined && (
            <p className="page-sub">
              <span role="status">
                {shown.length === data.length
                  ? countOf(data.length, "leader")
                  : `${shown.length} of ${data.length} leaders`}
              </span>
              {updatedAt !== null && (
                <span>
                  {" "}
                  · Checked at {formatTime(new Date(updatedAt).toISOString(), now)}, and every{" "}
                  {withUnit(FLEET_REFRESH_MS / 1000, "s")}
                </span>
              )}
            </p>
          )}
        </div>
        {data !== undefined && <LabelFilter leaders={data} value={label} />}
      </div>
      {data === undefined ? (
        error ? (
          <ErrorPanel error={error} onRetry={refresh} />
        ) : (
          <>
            <p role="status" className="muted fleet-loading">
              Loading the fleet…
            </p>
            <FleetPlaceholder />
          </>
        )
      ) : (
        <>
          {error !== null && (
            <p className="notice fleet-notice" role="alert">
              The last check did not work: {describeError(error).title} These are the last figures.
            </p>
          )}
          {data.length === 0 ? (
            <div className="panel empty-state">
              <p>You have no role on any leader yet. Ask a console administrator to give you one.</p>
            </div>
          ) : shown.length === 0 ? (
            <div className="panel empty-state">
              <p>No leader has the label {label}.</p>
            </div>
          ) : (
            <>
              <Totals leaders={shown} />
              <div className="fleet-grid">
                <NeedsALook leaders={shown} />
                {shown.map((leader) => (
                  <LeaderCard key={leader.name} leader={leader} now={now} />
                ))}
              </div>
            </>
          )}
        </>
      )}
    </>
  );
}
