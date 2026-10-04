import { useCallback, useState } from "react";

export interface ActionState {
  busy: boolean;
  error: unknown;
  /** Runs `work`; resolves true when it succeeded. Never throws. */
  run: (work: () => Promise<unknown>) => Promise<boolean>;
  clear: () => void;
}

/** One action's busy and error state, for a button or a dialog's submit. */
export function useAction(): ActionState {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const run = useCallback(async (work: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await work();
      return true;
    } catch (caught) {
      setError(caught);
      return false;
    } finally {
      setBusy(false);
    }
  }, []);
  const clear = useCallback(() => setError(null), []);
  return { busy, error, run, clear };
}
