import { useId, useState, type FormEvent } from "react";
import { api, leaderPath, query } from "../../api/client";
import type { JobOut, JobState } from "../../api/types";
import { useNavigate, useSearchParam } from "../../app/router";
import { useAction } from "../../app/useAction";
import { useDialogAction } from "../../app/useDialogAction";
import { ActionButton } from "../../components/ActionButton";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { Dialog } from "../../components/Dialog";
import { ErrorPanel } from "../../components/ErrorPanel";
import { formatTime } from "../../lib/format";
import {
  ActionNotice,
  ReadState,
  RefreshButton,
  shortId,
  useLeaderRead,
  type TabProps,
} from "./common";
import { leaderUrl } from "./tabs";

export const JOB_STATES: JobState[] = ["queued", "leased", "completed", "failed", "cancelled"];
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

function detail(job: JobOut): string {
  if (job.failure_reason) return job.failure_reason;
  if (job.cancelled_by) return `Cancelled by ${job.cancelled_by}`;
  if (job.leased_by) return `Leased by ${shortId(job.leased_by)}`;
  if (job.no_speech) return "No speech found";
  return "";
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
  const locations = (leader.snapshot?.status.locations ?? []).map((l) => l.name).sort();
  const filtered = Boolean(state || location);

  const setFilter = (next: { state?: string | null; location?: string | null }) => {
    const merged = { state, location, ...next };
    navigate(`${leaderUrl(leader.name, "jobs")}${query(merged)}`, { replace: true });
  };

  const onRetry = async (job: JobOut) => {
    setNotice(null);
    if (await retry.run(() => api.post(leaderPath(leader.name, `jobs/${job.id}/retry`)))) {
      setNotice(`Job ${shortId(job.id)} is queued again.`);
      read.refresh();
    }
  };

  return (
    <>
      <div className="filters">
        <label className="field-inline">
          State
          <select
            value={state ?? ""}
            onChange={(event) => setFilter({ state: event.target.value || null })}
          >
            <option value="">All states</option>
            {state && !JOB_STATES.includes(state as JobState) && (
              <option value={state}>{state}</option>
            )}
            {JOB_STATES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
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
      <ActionNotice message={notice} />
      {retry.error !== null && <ErrorPanel error={retry.error} />}
      <ReadState read={read} what="jobs">
        {(jobs) =>
          jobs.length === 0 ? (
            <p>{filtered ? "No jobs match this filter." : "This leader has no jobs."}</p>
          ) : (
            <>
              {jobs.length >= JOB_LIMIT && (
                <p className="muted">Showing the newest {JOB_LIMIT} jobs. Filter to narrow them.</p>
              )}
              <div className="table-scroll" role="region" aria-label="Jobs" tabIndex={0}>
                <table>
                  <thead>
                    <tr>
                      <th scope="col">Job</th>
                      <th scope="col">State</th>
                      <th scope="col">Recording</th>
                      <th scope="col">Pool</th>
                      <th scope="col">Priority</th>
                      <th scope="col">Attempts</th>
                      <th scope="col">Created</th>
                      <th scope="col">Detail</th>
                      <th scope="col">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {jobs.map((job) => (
                      <tr key={job.id}>
                        <th scope="row">
                          <code>{shortId(job.id)}</code>
                        </th>
                        <td>{job.state}</td>
                        <td>
                          {job.location}: <span className="mono">{job.key}</span>
                        </td>
                        <td>{job.pool}</td>
                        <td className="num">{job.priority}</td>
                        <td className="num">
                          {job.attempts} of {job.max_attempts}
                        </td>
                        <td>{formatTime(job.created_at)}</td>
                        <td>{detail(job)}</td>
                        <td className="actions">
                          {RETRYABLE.has(job.state) && (
                            <ActionButton
                              held={leader.role}
                              action="jobs.retry"
                              busy={retry.busy}
                              onClick={() => void onRetry(job)}
                              name={`Retry job ${shortId(job.id)}`}
                            >
                              Retry
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
          message={`The job for ${cancelling.key} stops and stays stopped unless someone retries it.`}
          confirmLabel="Cancel job"
          onClose={() => setCancelling(null)}
          onConfirm={async () => {
            setNotice(null);
            await api.post(leaderPath(leader.name, `jobs/${cancelling.id}/cancel`));
            setNotice(`Job ${shortId(cancelling.id)} is cancelled.`);
            read.refresh();
          }}
        />
      )}
      {prioritising !== null && (
        <PriorityDialog
          leaderName={leader.name}
          job={prioritising}
          onClose={() => setPrioritising(null)}
          onDone={() => {
            setNotice(`Priority of job ${shortId(prioritising.id)} is set.`);
            read.refresh();
          }}
        />
      )}
    </>
  );
}
