import type { FleetLeader } from "../../api/types";
import { useFleet } from "../../app/fleet";
import { Link } from "../../app/router";
import { usePageTitle } from "../../app/usePageTitle";
import { ErrorPanel } from "../../components/ErrorPanel";
import { HealthBadge } from "../../components/HealthBadge";
import { formatTime } from "../../lib/format";
import { NotFoundPage } from "../NotFoundPage";
import { PoolsTab } from "./PoolsTab";
import { TABS, leaderUrl, type TabId } from "./tabs";

function TabContent({ tab, leader }: { tab: TabId; leader: FleetLeader }) {
  switch (tab) {
    case "pools":
      return <PoolsTab leader={leader} />;
  }
}

function HealthNote({ leader }: { leader: FleetLeader }) {
  if (leader.health === "reachable") return null;
  const since = leader.last_success_at
    ? ` The last successful poll was at ${formatTime(leader.last_success_at)}.`
    : "";
  const text: Record<string, string> = {
    unreachable: `The console cannot reach ${leader.name}; reads and actions will fail until it answers.${since}`,
    credential_revoked: `${leader.name} revoked the console's credential. A console administrator must replace it.${since}`,
    disabled: `${leader.name} is disabled in the console; reads and actions are refused.${since}`,
    pending: `${leader.name} has not answered a poll yet.`,
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
          This leader is not visible to you: it is not registered, or you hold no role on it.{" "}
          <Link to="/">Back to the fleet</Link>.
        </p>
      </>
    );
  }

  return (
    <>
      <p className="breadcrumb">
        <Link to="/">Fleet</Link> / {leader.name}
      </p>
      <div className="page-head">
        <h1>{leader.name}</h1>
        <HealthBadge leader={leader} />
      </div>
      <p className="muted">
        Your role on {leader.name}: <strong>{leader.role}</strong>. Actions that need a higher role are shown
        disabled.
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
        <TabContent key={`${leader.name}/${current.id}`} tab={current.id} leader={leader} />
      </section>
    </>
  );
}
