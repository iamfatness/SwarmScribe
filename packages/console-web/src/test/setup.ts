import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";
import { resetClientForTests } from "../api/client";
import { setLastInputForTests } from "../app/activity";
import { resetSessionEndedForTests } from "../app/navigation";

// jsdom has no <dialog> modality and no matchMedia; the app uses both. This stands in for the
// parts of showModal() the dialog tests rely on (the browser provides them in production):
// the rest of the page and any earlier dialog inert, first control focused, Tab kept inside,
// Escape raising a cancelable `cancel` event that closes unless prevented.
if (typeof HTMLDialogElement !== "undefined" && !HTMLDialogElement.prototype.showModal) {
  const FOCUSABLE =
    'a[href], button:not([disabled]), input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])';
  // Open modals, oldest first: only the newest is reachable; everything else is inert.
  const stack: HTMLDialogElement[] = [];
  const syncInert = () => {
    const top = stack.at(-1);
    for (const child of Array.from(document.body.children)) {
      if (top === undefined || child === top) child.removeAttribute("inert");
      else child.setAttribute("inert", "");
    }
  };
  HTMLDialogElement.prototype.showModal = function showModal(this: HTMLDialogElement) {
    this.setAttribute("open", "");
    stack.push(this);
    syncInert();
    const items = () => Array.from(this.querySelectorAll<HTMLElement>(FOCUSABLE));
    (items()[0] ?? this).focus();
    this.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        const cancel = new Event("cancel", { cancelable: true });
        this.dispatchEvent(cancel);
        if (!cancel.defaultPrevented) this.close();
      } else if (event.key === "Tab") {
        const list = items();
        const first = list[0];
        const last = list.at(-1);
        if (first === undefined || last === undefined) return;
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    });
  };
  HTMLDialogElement.prototype.close = function close(this: HTMLDialogElement) {
    if (!this.hasAttribute("open")) return;
    this.removeAttribute("open");
    stack.splice(stack.indexOf(this), 1);
    syncInert();
    this.dispatchEvent(new Event("close"));
  };
}
if (!window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      addListener: () => undefined,
      removeListener: () => undefined,
      dispatchEvent: () => false,
    }) as MediaQueryList;
}

afterEach(() => {
  cleanup();
  // Module-level state that a test (or renderApp) may have set: reset it so one test's 401
  // or idle clock cannot silently stop the next test's polls.
  resetSessionEndedForTests();
  resetClientForTests();
  setLastInputForTests(Date.now());
  window.history.replaceState(null, "", "/");
});
