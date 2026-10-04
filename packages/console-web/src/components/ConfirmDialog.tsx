import { useAction } from "../app/useAction";
import { Dialog } from "./Dialog";
import { ErrorPanel } from "./ErrorPanel";

/**
 * Asks before a destructive action; shows the action's error in place if it fails. The safe
 * choice comes first, so the dialog puts focus on it and a stray Enter never confirms.
 */
export function ConfirmDialog({
  title,
  message,
  confirmLabel,
  onConfirm,
  onClose,
}: {
  title: string;
  message: string;
  confirmLabel: string;
  onConfirm: () => Promise<unknown>;
  onClose: () => void;
}) {
  const action = useAction();
  return (
    <Dialog title={title} onClose={onClose}>
      <p>{message}</p>
      {action.error !== null && <ErrorPanel error={action.error} />}
      <div className="dialog-buttons">
        <button type="button" className="button" onClick={onClose}>
          Close
        </button>
        <button
          type="button"
          className="button button-danger"
          disabled={action.busy}
          onClick={async () => {
            if (await action.run(onConfirm)) onClose();
          }}
        >
          {action.busy ? "Working…" : confirmLabel}
        </button>
      </div>
    </Dialog>
  );
}
