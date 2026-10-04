import { useEffect, useId, useRef, type KeyboardEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function focusableIn(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((el) => !el.hasAttribute("inert"));
}

/** Open dialogs, oldest first; only the last is reachable. */
const open: HTMLDialogElement[] = [];
/** The elements this module made inert, so it never clears an inert it did not set. */
const marked = new Set<HTMLElement>();

function syncInert() {
  const top = open.at(-1);
  for (const child of Array.from(marked)) {
    if (child === top || top === undefined || !child.isConnected) {
      child.removeAttribute("inert");
      child.removeAttribute("aria-hidden");
      marked.delete(child);
    }
  }
  if (top === undefined) return;
  for (const child of Array.from(document.body.children)) {
    if (child === top || !(child instanceof HTMLElement) || marked.has(child)) continue;
    if (child.hasAttribute("inert") || ["SCRIPT", "STYLE", "LINK"].includes(child.tagName)) continue;
    child.setAttribute("inert", "");
    child.setAttribute("aria-hidden", "true");
    marked.add(child);
  }
}

/**
 * A modal <dialog>, rendered at the end of <body>. Mounted means open: render it only while
 * it should show.
 *
 * - Focus moves in on open (the first control, so a confirmation's safe "Close" comes first)
 *   and Tab / Shift+Tab wrap inside it; the browser's modal does this too, but we do not
 *   depend on that alone.
 * - Escape closes it through `onClose`.
 * - Everything else on the page, including a dialog already open beneath this one, is made
 *   inert and hidden from assistive technology while it shows, so a second dialog stacks on
 *   top and the first cannot be reached.
 * - On close, focus returns to what had it on open, or to the main region when that is gone.
 */
export function Dialog({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const dialog = ref.current;
    if (dialog === null) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;

    open.push(dialog);
    syncInert();

    if (!dialog.open) dialog.showModal();
    (focusableIn(dialog)[0] ?? dialog).focus();

    return () => {
      open.splice(open.indexOf(dialog), 1);
      syncInert();
      if (dialog.open) dialog.close();
      if (opener !== null && opener.isConnected) opener.focus();
      else document.getElementById("main")?.focus();
    };
  }, []);

  function onKeyDown(event: KeyboardEvent<HTMLDialogElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key !== "Tab") return;
    const dialog = ref.current;
    if (dialog === null) return;
    const items = focusableIn(dialog);
    if (items.length === 0) {
      event.preventDefault();
      return;
    }
    const first = items[0];
    const last = items.at(-1);
    if (first === undefined || last === undefined) return;
    const active = document.activeElement;
    if (event.shiftKey && (active === first || active === dialog)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  }

  return createPortal(
    // eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions
    <dialog
      ref={ref}
      className="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      tabIndex={-1}
      onKeyDown={onKeyDown}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
    >
      <h2 id={titleId} className="dialog-title">
        {title}
      </h2>
      {children}
    </dialog>,
    document.body,
  );
}
