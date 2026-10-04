import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";
import { resetClientForTests } from "../api/client";
import { setLastInputForTests } from "../app/activity";
import { resetSessionEndedForTests } from "../app/navigation";

// jsdom has no <dialog> modality and no matchMedia; the app uses both.
if (typeof HTMLDialogElement !== "undefined" && !HTMLDialogElement.prototype.showModal) {
  HTMLDialogElement.prototype.showModal = function showModal(this: HTMLDialogElement) {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function close(this: HTMLDialogElement) {
    if (!this.hasAttribute("open")) return;
    this.removeAttribute("open");
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
