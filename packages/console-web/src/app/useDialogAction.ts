import { useCallback, useEffect, useRef } from "react";
import { useAction } from "./useAction";

export interface DialogAction {
  busy: boolean;
  error: unknown;
  clear: () => void;
  /** Closes the dialog once; a no-op after it has closed (by Close, Escape or unmount). */
  close: () => void;
  /**
   * Runs `work`, then closes the dialog if it succeeded and this instance is still open.
   * A second call while one runs is ignored. `work` runs to its end either way (an action
   * that happened must still be reported); only the close is instance-scoped.
   */
  submit: (work: () => Promise<unknown>) => Promise<boolean>;
}

/**
 * The action half of a dialog: busy state (the caller shows it with aria-disabled, never
 * disabled, so focus stays put), the error to show in place, a guard against double
 * submission, and a close that belongs to this instance. Once the dialog has closed a late
 * result can neither close again nor reach a dialog opened since.
 */
export function useDialogAction(onClose: () => void): DialogAction {
  const action = useAction();
  const closed = useRef(false);
  const running = useRef(false);
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  });
  useEffect(() => {
    closed.current = false;
    return () => {
      closed.current = true;
    };
  }, []);

  const close = useCallback(() => {
    if (closed.current) return;
    closed.current = true;
    onCloseRef.current();
  }, []);

  const { run } = action;
  const submit = useCallback(
    async (work: () => Promise<unknown>) => {
      if (running.current) return false;
      running.current = true;
      try {
        const ok = await run(work);
        if (ok) close();
        return ok;
      } finally {
        running.current = false;
      }
    },
    [run, close],
  );

  return { busy: action.busy, error: action.error, clear: action.clear, close, submit };
}
