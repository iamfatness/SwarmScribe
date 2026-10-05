import type { FleetLeader, Role } from "../../api/types";
import { CurrentLeader } from "../../app/currentLeader";
import { useFleet } from "../../app/fleet";
import { Link } from "../../app/router";
import { usePageTitle } from "../../app/usePageTitle";
import { ErrorPanel } from "../../components/ErrorPanel";
import { HealthBadge } from "../../components/HealthBadge";
import { formatTime, labelPairs } from "../../lib/format";
import { NotFoundPage } from "../NotFoundPage";
import { ConsentTab } from "./ConsentTab";
import { JobsTab } from "./JobsTab";
import { LocationsTab } from "./LocationsTab";
import { PoolsTab } from "./PoolsTab";
import { TABS, leaderUrl, type TabId } from "./tabs";
import { TokensTab } from "./TokensTab";

function TabContent({ tab, leader }: { tab: TabId; leader: FleetLeader }) {
  switch (tab) {
    case "pools":
      return <PoolsTab leader={leader} />;
    case "jobs":
      return <JobsTab leader={leader} />;
    case "locations":
      return <LocationsTab leader={leader} />;
    case "tokens":
      return <TokensTab leader={leader} />;
    case "consent":
      return <ConsentTab leader={leader} />;
  }
}

/**
 * What the person's role leaves switched off on this leader. An admin is promised nothing: a
 * leader can let this console act only up to a lower role, and the console learns that limit
 * only when the leader refuses something (the refusal then says so: api/errors.ts).
 */
const ROLE_NOTE: Record<Role, string | null> = {
  viewer: "What needs an operator or an admin is shown, but switched off.",
  operator: "What needs an admin is shown, but switched off.",
  admin: null,
};

function HealthNote({ leader }: { leader: FleetLeader }) {
  if (leader.health === "reachable") return null;
  const since = leader.last_success_at ? ` It last answered at ${formatTime(leader.last_success_at)}.` : "";
  const text: Record<string, string> = {
    unreachable: `${leader.name} is not answering, so nothing here can be read or changed until it does.${since}`,
    credential_revoked: `${leader.name} revoked the console's credential. A console administrator must replace it.${since}`,
    disabled: `${leader.name} is switched off in the console, so nothing is asked of it.${since}`,
    pending: `${leader.name} has not answered a check yet.`,
  };
  return <p className="notice">{text[leader.health] ?? `Health: ${leader.health}.`}</p>;
}

export function LeaderPage({ name, tab }: { name: string; tab: string }) {
  const { data, error, refresh } = useFleet();
  const current = TABS.find((t) => t.id === tab);
  usePageTitle(current ? `${name}: ${current.label}` : "Not found");
  if (current === undefined) return <NotFoundPage />;

  if (data === undefined) {
    return (
      <>
        <h1>{name}</h1>
        {error ? <ErrorPanel error={error} onRetry={refresh} /> : <p role="status">Loading…</p>}
      </>
    );
  }
  const leader = data.find((l) => l.name.toLowerCase() === name.toLowerCase());
  if (leader === undefined) {
    return (
      <>
        <h1>{name}</h1>
        <p>
          You cannot see this leader: the console does not know it, or you have no role on it.{" "}
          <Link to="/">Back to the fleet</Link>.
        </p>
      </>
    );
  }

  const pairs = labelPairs(leader.labels);
  return (
    <>
      <p className="breadcrumb">
        <Link to="/">Fleet</Link> / {leader.name}
      </p>
      <div className="page-head">
        <div className="page-title">
          <h1>{leader.name}</h1>
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
      <p className="page-sub role-line">
        You are {leader.role === "viewer" ? "a" : "an"} <strong>{leader.role}</strong> here.
        {ROLE_NOTE[leader.role] !== null && ` ${ROLE_NOTE[leader.role]}`}
      </p>
      <HealthNote leader={leader} />
      <nav aria-label={`${leader.name} sections`}>
        <ul className="tab-list">
          {TABS.map((t) => (
            <li key={t.id}>
              <Link
                to={leaderUrl(leader.name, t.id)}
                className="tab-link"
                aria-current={t.id === tab ? "page" : undefined}
              >
                {t.label}
              </Link>
            </li>
          ))}
        </ul>
      </nav>
      <section className="tab-panel" aria-labelledby="tab-title">
        <h2 id="tab-title">{current.label}</h2>
        <CurrentLeader.Provider value={leader}>
          <TabContent key={`${leader.name}/${current.id}`} tab={current.id} leader={leader} />
        </CurrentLeader.Provider>
      </section>
    </>
  );
}
