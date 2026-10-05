import { useId } from "react";
import { useDialogAction } from "../app/useDialogAction";
import { Dialog } from "./Dialog";
import { ErrorPanel } from "./ErrorPanel";

/**
 * Asks before a destructive action; shows the action's error in place if it fails. The safe
 * choice ("No, go back") comes first, so the dialog puts focus on it and a stray Enter
 * never confirms.
 *
 * The close and the action's result belong to this dialog instance (useDialogAction). While
 * the action runs the confirm button is aria-disabled, not disabled, so focus stays put and a
 * failure is announced where the person is.
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
  const action = useDialogAction(onClose);
  const messageId = useId();

  return (
    <Dialog title={title} onClose={action.close} role="alertdialog" describedBy={messageId}>
      <p id={messageId}>{message}</p>
      {action.error !== null && <ErrorPanel error={action.error} />}
      <div className="dialog-buttons">
        <button type="button" className="button" onClick={action.close}>
          No, go back
        </button>
        <button
          type="button"
          className="button button-danger"
          aria-disabled={action.busy || undefined}
          onClick={() => void action.submit(onConfirm)}
        >
          {action.busy ? "Working…" : confirmLabel}
        </button>
      </div>
    </Dialog>
  );
}
