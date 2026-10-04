import { useCallback, useEffect, useEffectEvent, useState } from "react";
import { ApiError, isAbort } from "../api/client";
import { isIdle, onResume } from "./activity";
import { sessionEnded } from "./navigation";

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
 * Loads now, then every `intervalMs`. A change of `key` starts over without the old key's
 * data. Loads are skipped while the tab is hidden or the person is idle (activity.ts), and run
 * at once when either ends. A failed load keeps the last good data and sets `error`; the next
 * tick tries again.
 *
 * With a null interval the load is on demand: once on mount, then only on refresh() or a
 * changed key, never on the tab becoming visible or on resume from idle (every read of a
 * leader is an audit row on the leader).
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
    let inFlight = false;

    // Exactly one timer at a time, and never once the poll is over.
    const schedule = () => {
      clearTimeout(timer);
      if (!stopped && intervalMs !== null) timer = setTimeout(() => void run(), intervalMs);
    };
    const run = async () => {
      // Never overlap requests: a resume during a load is covered by that load's reschedule.
      if (stopped || inFlight || sessionEnded()) return;
      clearTimeout(timer);
      const paused = document.visibilityState === "hidden" || isIdle();
      if (paused && !first) {
        schedule();
        return;
      }
      first = false;
      inFlight = true;
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
        // A 401 is terminal: the session is over, so this poll never fires again.
        if (error instanceof ApiError && error.status === 401) {
          stopped = true;
          return;
        }
      } finally {
        inFlight = false;
      }
      schedule();
    };
    const runIfVisible = () => {
      if (document.visibilityState === "visible") void run();
    };

    void run();
    // On demand (null interval): only mount, refresh() and a changed key load.
    const onDemand = intervalMs === null;
    if (!onDemand) document.addEventListener("visibilitychange", runIfVisible);
    const unsubscribe = onDemand ? () => undefined : onResume(() => void run());
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
