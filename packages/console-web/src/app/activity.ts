// Whether the person is still at the console. Background refreshes stop after IDLE_AFTER_MS
// without keyboard, pointer or wheel input, so an open tab does not keep the session alive
// past the console's one-hour idle timeout (fleet console spec 5.2). Input resumes them.

export const IDLE_AFTER_MS = 55 * 60 * 1000;

const EVENTS = ["keydown", "pointerdown", "wheel", "touchstart"] as const;
let lastInput = Date.now();
const resumeListeners = new Set<() => void>();

export function isIdle(now: number = Date.now()): boolean {
  return now - lastInput >= IDLE_AFTER_MS;
}

export function noteActivity(now: number = Date.now()): void {
  const wasIdle = isIdle(now);
  lastInput = now;
  if (wasIdle) for (const listener of resumeListeners) listener();
}

/** Called when input arrives after an idle spell. Returns the unsubscribe function. */
export function onResume(listener: () => void): () => void {
  resumeListeners.add(listener);
  return () => resumeListeners.delete(listener);
}

export function startActivityTracking(): () => void {
  const note = () => noteActivity();
  for (const name of EVENTS) window.addEventListener(name, note, { passive: true, capture: true });
  return () => {
    for (const name of EVENTS) window.removeEventListener(name, note, { capture: true });
  };
}

/** Tests only: pretend the last input was at `at`. */
export function setLastInputForTests(at: number): void {
  lastInput = at;
}
