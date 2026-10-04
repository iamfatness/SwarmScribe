import { describeError } from "../api/errors";

/** An error as the person reads it: our title, the server's own text, and a retry. */
export function ErrorPanel({
  error,
  onRetry,
  retryLabel = "Try again",
}: {
  error: unknown;
  onRetry?: () => void;
  retryLabel?: string;
}) {
  const { title, detail } = describeError(error);
  return (
    <div className="error-panel" role="alert">
      <p className="error-title">{title}</p>
      {detail !== null && <p className="error-detail">{detail}</p>}
      {onRetry !== undefined && (
        <button type="button" className="button" onClick={onRetry}>
          {retryLabel}
        </button>
      )}
    </div>
  );
}
