import { useCallback, type ReactNode } from "react";
import { ApiError, api, leaderPath } from "../../api/client";
import type { FleetLeader, Health } from "../../api/types";
import { useCurrentLeader } from "../../app/currentLeader";
import { usePoll, type PollState } from "../../app/usePoll";
import { ErrorPanel } from "../../components/ErrorPanel";
import { formatTime } from "../../lib/format";

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

/**
 * The error a read gets when the fleet already shows why: the leader is not answering, is
 * switched off, or revoked the console's credential. The notice at the top of the page has
 * said so, so the tab says only that there is nothing to show, and what would change that.
 */
const EXPLAINED: Partial<
  Record<Health, { code: string; line: (name: string) => string; retry: boolean }>
> = {
  unreachable: {
    code: "leader_unreachable",
    line: (name) => `Nothing to show until ${name} answers.`,
    retry: true,
  },
  disabled: {
    code: "leader_disabled",
    line: (name) =>
      `Nothing to show while ${name} is switched off. ` +
      "A console administrator can switch it on under Administration.",
    retry: false,
  },
  credential_revoked: {
    code: "leader_credential_revoked",
    line: (name) => `Nothing to show until the console's credential for ${name} is replaced.`,
    retry: false,
  },
};

/** A read's failure: one quiet line when the page has already said why, the error if not. */
function ReadError({ read, retryLabel }: { read: PollState<unknown>; retryLabel?: string }) {
  const leader = useCurrentLeader();
  const { error } = read;
  const explained = leader === null ? undefined : EXPLAINED[leader.health];
  if (
    leader !== null &&
    explained !== undefined &&
    error instanceof ApiError &&
    error.code === explained.code
  ) {
    const wait = error.retryAfter !== null ? ` Try again in ${error.retryAfter} seconds.` : "";
    return (
      <div className="quiet-note">
        <p role="status">
          {explained.line(leader.name)}
          {explained.retry && wait}
        </p>
        {explained.retry && (
          <button type="button" className="button" onClick={read.refresh}>
            Try again
          </button>
        )}
      </div>
    );
  }
  return <ErrorPanel error={error} onRetry={read.refresh} retryLabel={retryLabel} />;
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
    if (read.error !== null) return <ReadError read={read} />;
    return <p role="status">Loading {what}…</p>;
  }
  return (
    <>
      {read.error !== null && <ReadError read={read} retryLabel="Reload" />}
      {children(read.data)}
    </>
  );
}

/**
 * Reads the list again, and says when it was last read: a leader's lists are not on a timer,
 * so the person should see how old they are. The time is not in a live region.
 */
export function RefreshButton({ read }: { read: PollState<unknown> }) {
  return (
    <span className="refresh">
      {read.updatedAt !== null && (
        <span className="refresh-time">Loaded at {formatTime(new Date(read.updatedAt).toISOString())}</span>
      )}
      <button type="button" className="button" onClick={read.refresh} disabled={read.loading}>
        Refresh
      </button>
    </span>
  );
}

export function shortId(id: string): string {
  return id.slice(0, 8);
}
