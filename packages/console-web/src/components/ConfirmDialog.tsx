import { useEffect, useId, useRef } from "react";
import { useAction } from "../app/useAction";
import { Dialog } from "./Dialog";
import { ErrorPanel } from "./ErrorPanel";

/**
 * Asks before a destructive action; shows the action's error in place if it fails. The safe
 * choice comes first, so the dialog puts focus on it and a stray Enter never confirms.
 *
 * The close and the action's result belong to this dialog instance: once it has closed (by
 * Close, Escape or unmount) a late result can neither close again nor reach a dialog opened
 * since. While the action runs the confirm button is aria-disabled, not disabled, so focus
 * stays put and a failure is announced where the person is.
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
  const messageId = useId();
  const closed = useRef(false);
  useEffect(() => {
    closed.current = false;
    return () => {
      closed.current = true;
    };
  }, []);

  function close() {
    if (closed.current) return;
    closed.current = true;
    onClose();
  }

  return (
    <Dialog title={title} onClose={close} role="alertdialog" describedBy={messageId}>
      <p id={messageId}>{message}</p>
      {action.error !== null && <ErrorPanel error={action.error} />}
      <div className="dialog-buttons">
        <button type="button" className="button" onClick={close}>
          Close
        </button>
        <button
          type="button"
          className="button button-danger"
          aria-disabled={action.busy || undefined}
          onClick={async () => {
            if (action.busy) return;
            const ok = await action.run(onConfirm);
            if (ok && !closed.current) close();
          }}
        >
          {action.busy ? "Working…" : confirmLabel}
        </button>
      </div>
    </Dialog>
  );
}
