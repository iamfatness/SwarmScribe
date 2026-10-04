import { useCallback, useEffect, useRef, type SyntheticEvent } from "react";
import type { PollState } from "../../app/usePoll";

interface Mark {
  rowId: string;
  regionLabel: string | null;
}

const FOCUSABLE = "button:not(:disabled):not([aria-disabled='true']), a[href]";

/** The row control or table region that has the focus, as a mark; null for anything else. */
function markOf(target: EventTarget | null): Mark | null {
  if (!(target instanceof Element)) return null;
  const row = target.closest<HTMLElement>("tr[data-row]");
  if (row === null || row.dataset.row === undefined) return null;
  const region = row.closest("[role='region']");
  return { rowId: row.dataset.row, regionLabel: region?.getAttribute("aria-label") ?? null };
}

function focusIsLost(): boolean {
  const active = document.activeElement;
  return (
    active === null ||
    active === document.body ||
    !active.isConnected ||
    active.id === "main"
  );
}

/** Puts the focus where a person who just acted on a row would want it next. */
function restore(mark: Mark) {
  const region =
    mark.regionLabel === null
      ? null
      : Array.from(document.querySelectorAll<HTMLElement>("[role='region']")).find(
          (el) => el.getAttribute("aria-label") === mark.regionLabel,
        ) ?? null;
  const row =
    region === null
      ? null
      : Array.from(region.querySelectorAll<HTMLElement>("tr[data-row]")).find(
          (el) => el.dataset.row === mark.rowId,
        ) ?? null;
  if (row !== null) {
    const control = row.querySelector<HTMLElement>(FOCUSABLE);
    if (control !== null) {
      control.focus();
      return;
    }
    row.tabIndex = -1;
    row.focus();
    return;
  }
  if (region !== null) {
    region.focus();
    return;
  }
  document.getElementById("main")?.focus();
}

/**
 * Focus after a row action, shared by every table with row actions. The button that was
 * pressed often goes away once the row changes state (Drain, Retry, Revoke, Remove), and the
 * browser then drops the focus on <body>. The wrapper `props` note which row the person was
 * last on (by focus or click, also from a dialog's opener); `done(message)` announces the
 * result in the status region, reloads, and, once the new rows are in, moves the focus to the
 * row's first remaining action, or to the row itself, or to the table region if the row is
 * gone. A focus that already sits somewhere real (the same button, still there) is left alone.
 */
export function useRowFocus(read: PollState<unknown>, setNotice: (message: string | null) => void) {
  const last = useRef<Mark | null>(null);
  const pending = useRef<Mark | null>(null);
  const { updatedAt, error, refresh } = read;

  const note = useCallback((event: SyntheticEvent) => {
    const mark = markOf(event.target);
    if (mark !== null) last.current = mark;
  }, []);

  useEffect(() => {
    const mark = pending.current;
    if (mark === null) return;
    pending.current = null;
    if (focusIsLost()) restore(mark);
  }, [updatedAt, error]);

  const done = useCallback(
    (message: string) => {
      setNotice(message);
      pending.current = last.current;
      refresh();
    },
    [setNotice, refresh],
  );

  return { props: { onFocusCapture: note, onClickCapture: note }, done };
}
