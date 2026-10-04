import { useCallback, type ReactNode } from "react";
import { api, leaderPath } from "../../api/client";
import type { FleetLeader } from "../../api/types";
import { usePoll, type PollState } from "../../app/usePoll";
import { ErrorPanel } from "../../components/ErrorPanel";

/** What every drill-down tab gets. */
export interface TabProps {
  leader: FleetLeader;
}

/**
 * A live read through the console's proxy: loaded when the tab opens, again on refresh()
 * and after each action. Not on a timer: every read of a leader by a person is audited by
 * the leader, so a timer would fill its audit log.
 */
export function useLeaderRead<T>(name: string, rest: string): PollState<T> {
  const load = useCallback(
    (signal: AbortSignal) => api.get<T>(leaderPath(name, rest), signal),
    [name, rest],
  );
  return usePoll(load, null, `${name}/${rest}`);
}

/** The result of the last action, read out by screen readers when it changes. */
export function ActionNotice({ message }: { message: string | null }) {
  return (
    <p className="action-notice" role="status">
      {message ?? ""}
    </p>
  );
}

/** A tab's read: the error with a retry, a loading line, or the content. */
export function ReadState<T>({
  read,
  what,
  children,
}: {
  read: PollState<T>;
  what: string;
  children: (data: T) => ReactNode;
}) {
  if (read.data === undefined) {
    if (read.error !== null) return <ErrorPanel error={read.error} onRetry={read.refresh} />;
    return <p role="status">Loading {what}…</p>;
  }
  return (
    <>
      {read.error !== null && <ErrorPanel error={read.error} onRetry={read.refresh} retryLabel="Reload" />}
      {children(read.data)}
    </>
  );
}

export function RefreshButton({ read }: { read: PollState<unknown> }) {
  return (
    <button type="button" className="button" onClick={read.refresh} disabled={read.loading}>
      Refresh
    </button>
  );
}

export function shortId(id: string): string {
  return id.slice(0, 8);
}
