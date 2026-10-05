import { useId, useState, type FormEvent } from "react";
import { api, leaderPath, query } from "../../api/client";
import type { JobOut, JobState } from "../../api/types";
import { useNavigate, useSearchParam } from "../../app/router";
import { useAction } from "../../app/useAction";
import { useDialogAction } from "../../app/useDialogAction";
import { ActionButton } from "../../components/ActionButton";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { Dialog } from "../../components/Dialog";
import { BreakPath } from "../../components/BreakPath";
import { ErrorPanel } from "../../components/ErrorPanel";
import { formatCount, formatTime } from "../../lib/format";
import {
  ActionNotice,
  ReadState,
  RefreshButton,
  shortId,
  useLeaderRead,
  type TabProps,
} from "./common";
import { useRowFocus } from "./rowFocus";
import { leaderUrl } from "./tabs";

export const JOB_STATES: JobState[] = ["queued", "leased", "completed", "failed", "cancelled"];
/** The filter pills, in the order a person works through them. The address keeps the leader's word. */
export const STATE_PILLS: { state: JobState; label: string }[] = [
  { state: "queued", label: "Waiting" },
  { state: "leased", label: "Being worked on" },
  { state: "failed", label: "Failed" },
  { state: "completed", label: "Finished" },
  { state: "cancelled", label: "Cancelled" },
];
/** The leader accepts 1 to 500; the table shows the newest 100. */
export const JOB_LIMIT = 100;
/** The leader's PriorityIn range: a strict integer from -1000 to 1000. */
const PRIORITY_MIN = -1000;
const PRIORITY_MAX = 1000;
const PRIORITY_REFUSAL = `Enter a whole number from ${PRIORITY_MIN} to ${PRIORITY_MAX}.`;
const OPEN = new Set(["queued", "leased"]);
const RETRYABLE = new Set(["failed", "cancelled"]);

/** The priority as a number, or null when the text is not a whole number in the range. */
function parsePriority(text: string): number | null {
  if (!/^-?\d+$/.test(text.trim())) return null;
  const value = Number(text);
  return value >= PRIORITY_MIN && value <= PRIORITY_MAX ? value : null;
}

function PriorityDialog({
  leaderName,
  job,
  onClose,
  onDone,
}: {
  leaderName: string;
  job: JobOut;
  onClose: () => void;
  onDone: () => void;
}) {
  const [value, setValue] = useState(String(job.priority));
  const [invalid, setInvalid] = useState(false);
  const action = useDialogAction(onClose);
  const helpId = useId();
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const priority = parsePriority(value);
    if (priority === null) {
      setInvalid(true);
      return;
    }
    setInvalid(false);
    void action.submit(async () => {
      await api.post(leaderPath(leaderName, `jobs/${job.id}/priority`), { priority });
      onDone();
    });
  };
  return (
    <Dialog title={`Priority of job ${shortId(job.id)}`} onClose={action.close}>
      <form noValidate onSubmit={submit}>
        <label className="field">
          Priority
          <input
            type="number"
            step={1}
            value={value}
            aria-invalid={invalid || undefined}
            aria-describedby={helpId}
            onChange={(event) => setValue(event.target.value)}
          />
        </label>
        <p id={helpId} className="field-help">
          A whole number from {PRIORITY_MIN} to {PRIORITY_MAX}; higher runs first.
        </p>
        {invalid && (
          <div className="error-panel" role="alert">
            <p className="error-title">{PRIORITY_REFUSAL}</p>
          </div>
        )}
        {action.error !== null && <ErrorPanel error={action.error} />}
        <div className="dialog-buttons">
          <button type="button" className="button" onClick={action.close}>
            Cancel
          </button>
          <button
            type="submit"
            className="button button-primary"
            aria-disabled={action.busy || undefined}
          >
            Set priority
          </button>
        </div>
      </form>
    </Dialog>
  );
}

type StateTone = "plain" | "busy" | "bad" | "quiet";

/** A job's state in the console's words. An unknown state is shown as the leader sent it. */
export function jobStateText(job: Pick<JobOut, "state" | "leased_by">): {
  label: string;
  tone: StateTone;
} {
  switch (job.state) {
    case "queued":
      return { label: "Waiting", tone: "plain" };
    case "leased":
      return {
        label: job.leased_by ? `With follower ${shortId(job.leased_by)}` : "Being worked on",
        tone: "busy",
      };
    case "completed":
      return { label: "Finished", tone: "quiet" };
    case "failed":
      return { label: "Failed", tone: "bad" };
    case "cancelled":
      return { label: "Cancelled", tone: "quiet" };
    default:
      return { label: job.state, tone: "plain" };
  }
}

/**
 * The line under a recording: where it came from, then what happened to the job, if anything.
 * It carries everything the old Detail column did: the failure, who cancelled it, the follower
 * that held it (a leased job says that in its state instead) and "No speech found".
 */
export function jobNote(job: JobOut): string {
  const parts = [`From ${job.location}`];
  if (job.failure_reason) parts.push(job.failure_reason);
  else if (job.cancelled_by) parts.push(`Cancelled by ${job.cancelled_by}`);
  else if (job.leased_by && job.state !== "leased") {
    parts.push(`Held by follower ${shortId(job.leased_by)}`);
  } else if (job.no_speech) parts.push("No speech found");
  return parts.join(" \u00b7 ");
}

export function JobsTab({ leader }: TabProps) {
  const navigate = useNavigate();
  const state = useSearchParam("state");
  const location = useSearchParam("location");
  const read = useLeaderRead<JobOut[]>(
    leader.name,
    `jobs${query({ state, location, limit: JOB_LIMIT })}`,
  );
  const retry = useAction();
  const [cancelling, setCancelling] = useState<JobOut | null>(null);
  const [prioritising, setPrioritising] = useState<JobOut | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const rows = useRowFocus(read, setNotice);
  const locations = (leader.snapshot?.status.locations ?? []).map((l) => l.name).sort();
  const filtered = Boolean(state || location);
  // Counts come from the last check of the leader, so they cover every location and can be
  // a few seconds behind the list: they are left out while a location filter is on.
  const counts = location ? null : (leader.snapshot?.status.jobs ?? null);
  const total = counts === null ? null : Object.values(counts).reduce((sum, n) => sum + n, 0);
  const known = STATE_PILLS.some((pill) => pill.state === state);

  const setFilter = (next: { state?: string | null; location?: string | null }) => {
    const merged = { state, location, ...next };
    navigate(`${leaderUrl(leader.name, "jobs")}${query(merged)}`, { replace: true });
  };

  const onRetry = async (job: JobOut) => {
    setNotice(null);
    if (await retry.run(() => api.post(leaderPath(leader.name, `jobs/${job.id}/retry`)))) {
      rows.done(`Job ${shortId(job.id)} is waiting again.`);
    }
  };

  return (
    <div {...rows.props}>
      <div className="section-head">
        <div className="pill-group" role="group" aria-label="Show jobs that are">
          <button
            type="button"
            className="pill"
            aria-pressed={!state}
            onClick={() => setFilter({ state: null })}
          >
            All
            {total !== null && " "}
            {total !== null && <span className="pill-count">{formatCount(total)}</span>}
          </button>
          {state && !known && (
            <button
              type="button"
              className="pill"
              aria-pressed="true"
              onClick={() => setFilter({ state: null })}
            >
              {state}
            </button>
          )}
          {STATE_PILLS.map((pill) => (
            <button
              key={pill.state}
              type="button"
              className="pill"
              aria-pressed={state === pill.state}
              onClick={() => setFilter({ state: state === pill.state ? null : pill.state })}
            >
              {pill.label}
              {counts !== null && " "}
              {counts !== null && (
                <span className="pill-count">{formatCount(counts[pill.state] ?? 0)}</span>
              )}
            </button>
          ))}
        </div>
        <div className="toolbar">
          <label className="field-inline">
            Location
            <select
              value={location ?? ""}
              onChange={(event) => setFilter({ location: event.target.value || null })}
            >
              <option value="">All locations</option>
              {location && !locations.includes(location) && (
                <option value={location}>{location}</option>
              )}
              {locations.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>
          <RefreshButton read={read} />
        </div>
      </div>
      <ActionNotice message={notice} />
      {retry.error !== null && <ErrorPanel error={retry.error} />}
      <ReadState read={read} what="jobs">
        {(jobs) =>
          jobs.length === 0 ? (
            <p>{filtered ? "No jobs match." : "This leader has no jobs."}</p>
          ) : (
            <>
              {jobs.length >= JOB_LIMIT && (
                <p className="muted">Showing the newest {JOB_LIMIT} jobs. Filter to narrow them.</p>
              )}
              <div className="table-scroll" role="region" aria-label="Job list" tabIndex={0}>
                <table className="wide">
                  <thead>
                    <tr>
                      <th scope="col">Job</th>
                      <th scope="col">State</th>
                      <th scope="col">Recording</th>
                      <th scope="col">Pool</th>
                      <th scope="col" className="num">Priority</th>
                      <th scope="col" className="num">Tries</th>
                      <th scope="col">Queued</th>
                      <th scope="col">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {jobs.map((job) => (
                      <tr key={job.id} data-row={job.id}>
                        <th scope="row">
                          <code>{shortId(job.id)}</code>
                        </th>
                        <td className={`job-state job-state-${jobStateText(job).tone}`}>
                          {jobStateText(job).label}
                        </td>
                        <td className="long">
                          <span className="mono">
                            <BreakPath text={job.key} />
                          </span>
                          <span className="cell-note">{jobNote(job)}</span>
                        </td>
                        <td className="nowrap">{job.pool}</td>
                        <td className="num nowrap">{job.priority}</td>
                        <td className="num nowrap">
                          {job.attempts} of {job.max_attempts}
                        </td>
                        <td className="nowrap">{formatTime(job.created_at)}</td>
                        <td className="actions">
                          {RETRYABLE.has(job.state) && (
                            <ActionButton
                              held={leader.role}
                              action="jobs.retry"
                              busy={retry.busy}
                              primary={job.state === "failed"}
                              onClick={() => void onRetry(job)}
                              name={`Try again: job ${shortId(job.id)}`}
                            >
                              Try again
                            </ActionButton>
                          )}
                          {OPEN.has(job.state) && (
                            <>
                              <ActionButton
                                held={leader.role}
                                action="jobs.priority"
                                onClick={() => setPrioritising(job)}
                                name={`Priority of job ${shortId(job.id)}`}
                              >
                                Priority
                              </ActionButton>
                              <ActionButton
                                held={leader.role}
                                action="jobs.cancel"
                                danger
                                onClick={() => setCancelling(job)}
                                name={`Cancel job ${shortId(job.id)}`}
                              >
                                Cancel
                              </ActionButton>
                            </>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )
        }
      </ReadState>
      {cancelling !== null && (
        <ConfirmDialog
          title={`Cancel job ${shortId(cancelling.id)}?`}
          message={`The job for ${cancelling.key} stops, and stays stopped unless someone tries it again.`}
          confirmLabel="Cancel job"
          onClose={() => setCancelling(null)}
          onConfirm={async () => {
            setNotice(null);
            await api.post(leaderPath(leader.name, `jobs/${cancelling.id}/cancel`));
            rows.done(`Job ${shortId(cancelling.id)} is cancelled.`);
          }}
        />
      )}
      {prioritising !== null && (
        <PriorityDialog
          leaderName={leader.name}
          job={prioritising}
          onClose={() => setPrioritising(null)}
          onDone={() => {
            rows.done(`Priority of job ${shortId(prioritising.id)} is set.`);
          }}
        />
      )}
    </div>
  );
}
