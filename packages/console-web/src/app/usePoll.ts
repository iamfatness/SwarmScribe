import { useCallback, useEffect, useEffectEvent, useState } from "react";
import { isAbort } from "../api/client";
import { isIdle, onResume } from "./activity";

export interface PollState<T> {
  data: T | undefined;
  /** The last load's error; data, if any, is from the last successful load. */
  error: unknown;
  loading: boolean;
  /** When data last loaded (ms since the epoch), or null. */
  updatedAt: number | null;
  refresh: () => void;
}

interface Held<T> {
  key: string;
  data: T | undefined;
  error: unknown;
  loading: boolean;
  updatedAt: number | null;
}

/**
 * Loads now, then every `intervalMs` (null: load once, and again on refresh()). A change of
 * `key` starts over without the old key's data. Loads are skipped while the tab is hidden or
 * the person is idle (activity.ts), and run at once when either ends. A failed load keeps the
 * last good data and sets `error`; the next tick tries again.
 */
export function usePoll<T>(
  load: (signal: AbortSignal) => Promise<T>,
  intervalMs: number | null,
  key: string,
): PollState<T> {
  const [held, setHeld] = useState<Held<T>>({
    key,
    data: undefined,
    error: null,
    loading: true,
    updatedAt: null,
  });
  const [round, setRound] = useState(0);
  const loadNow = useEffectEvent((signal: AbortSignal) => load(signal));

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let stopped = false;
    let first = true;

    const schedule = () => {
      if (!stopped && intervalMs !== null) timer = setTimeout(() => void run(), intervalMs);
    };
    const run = async () => {
      clearTimeout(timer);
      const paused = document.visibilityState === "hidden" || isIdle();
      if (paused && !first) {
        schedule();
        return;
      }
      first = false;
      try {
        const data = await loadNow(controller.signal);
        if (stopped) return;
        setHeld({ key, data, error: null, loading: false, updatedAt: Date.now() });
      } catch (error) {
        if (stopped || isAbort(error)) return;
        setHeld((prev) => ({
          key,
          data: prev.key === key ? prev.data : undefined,
          error,
          loading: false,
          updatedAt: prev.key === key ? prev.updatedAt : null,
        }));
      }
      schedule();
    };
    const runIfVisible = () => {
      if (document.visibilityState === "visible") void run();
    };

    void run();
    document.addEventListener("visibilitychange", runIfVisible);
    const unsubscribe = onResume(() => void run());
    return () => {
      stopped = true;
      controller.abort();
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", runIfVisible);
      unsubscribe();
    };
  }, [key, intervalMs, round]);

  const refresh = useCallback(() => setRound((n) => n + 1), []);
  if (held.key !== key) return { data: undefined, error: null, loading: true, updatedAt: null, refresh };
  return { data: held.data, error: held.error, loading: held.loading, updatedAt: held.updatedAt, refresh };
}
