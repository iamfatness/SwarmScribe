import { describeError } from "../api/errors";
import { useCurrentLeader } from "../app/currentLeader";
import { Link } from "../app/router";

/**
 * An error as the person reads it, every sentence the console's own (api/errors.ts): a title,
 * a line saying what to do or how long to wait, and a retry where asking again can work.
 */
export function ErrorPanel({
  error,
  onRetry,
  retryLabel = "Try again",
}: {
  error: unknown;
  onRetry?: () => void;
  retryLabel?: string;
}) {
  const leader = useCurrentLeader();
  const { title, detail, retryable, toFleet } = describeError(
    error,
    leader === null ? undefined : { leader: { name: leader.name, role: leader.role } },
  );
  return (
    <div className="error-panel" role="alert">
      <p className="error-title">{title}</p>
      {(detail !== null || toFleet) && (
        <p className="error-detail">
          {detail}
          {toFleet && (
            <>
              {detail !== null && " "}
              <Link to="/">Go back to the fleet</Link>.
            </>
          )}
        </p>
      )}
      {onRetry !== undefined && retryable && (
        <button type="button" className="button" onClick={onRetry}>
          {retryLabel}
        </button>
      )}
    </div>
  );
}
