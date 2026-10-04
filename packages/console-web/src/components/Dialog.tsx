import { useEffect, useId, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

/**
 * A modal <dialog> rendered at the end of <body>. Mounted means open: render it only while
 * it should show.
 *
 * The browser's showModal() provides the modality: the rest of the page, and any dialog
 * beneath this one, is inert; Tab stays inside; Escape fires `cancel`, which is the one path
 * to `onClose`; focus moves to the first control (a confirmation's safe "Close" comes first).
 * What the browser does not give us, this adds: on close or unmount, focus returns to what
 * had it on open, or to the main region when that is no longer in the page.
 */
export function Dialog({
  title,
  onClose,
  children,
  role = "dialog",
  describedBy,
  dismissable = true,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  role?: "dialog" | "alertdialog";
  /** The id of the element that describes the dialog, when it has one. */
  describedBy?: string;
  /**
   * False while the dialog must stay: it then cannot be closed by Escape, however often it is
   * pressed. Chromium closes a dialog natively on a later Escape even when `cancel` is
   * prevented, so a `close` that nobody asked for reopens it at once, its state intact.
   */
  dismissable?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const stays = useRef(!dismissable);
  useEffect(() => {
    stays.current = !dismissable;
  }, [dismissable]);

  useEffect(() => {
    const dialog = ref.current;
    if (dialog === null) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    let leaving = false;
    let inside: HTMLElement | null = null;
    const remember = (event: FocusEvent) => {
      if (event.target instanceof HTMLElement) inside = event.target;
    };
    const reopen = () => {
      if (leaving || !stays.current || !dialog.isConnected) return;
      dialog.showModal();
      if (inside !== null && inside.isConnected && dialog.contains(inside)) inside.focus();
    };
    dialog.addEventListener("focusin", remember);
    dialog.addEventListener("close", reopen);
    if (!dialog.open) dialog.showModal();
    return () => {
      leaving = true;
      dialog.removeEventListener("focusin", remember);
      dialog.removeEventListener("close", reopen);
      if (dialog.open) dialog.close();
      if (opener !== null && opener.isConnected) opener.focus();
      else document.getElementById("main")?.focus();
    };
  }, []);

  return createPortal(
    <dialog
      ref={ref}
      className="dialog"
      role={role}
      aria-modal="true"
      aria-labelledby={titleId}
      aria-describedby={describedBy}
      // closedby="none" (where supported) keeps Escape from closing it at all.
      {...(dismissable ? {} : { closedby: "none" })}
      onCancel={(event) => {
        event.preventDefault();
        if (dismissable) onClose();
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
