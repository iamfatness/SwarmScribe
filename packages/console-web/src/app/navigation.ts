// Full-page navigations, kept apart so tests can replace them (jsdom cannot navigate).
// Leaving the app through a full load drops every piece of in-memory state, which is what
// should happen when a session ends.

// C0 controls, DEL and C1 controls. Browsers strip tabs and newlines inside URLs, so
// "/<tab>/evil.example" would otherwise become "//evil.example".
// eslint-disable-next-line no-control-regex -- matching control characters is the point
const CONTROL = /[\u0000-\u001f\u007f-\u009f]/;

const LOCAL_BASE = "https://console.invalid";

/** A path on this console to come back to after sign-in, or "/". */
export function safeReturnTo(value: string | null): string {
  if (
    !value ||
    value.length > 512 ||
    !value.startsWith("/") ||
    value.startsWith("//") ||
    value.includes("\\") ||
    CONTROL.test(value)
  ) {
    return "/";
  }
  if (value.startsWith("/sign-in") || value.startsWith("/api") || value.startsWith("/auth")) return "/";
  // Belt and braces: whatever the browser makes of it must stay on this origin.
  try {
    if (new URL(value, LOCAL_BASE).origin !== LOCAL_BASE) return "/";
  } catch {
    return "/";
  }
  return value;
}

export function signInUrl(returnTo: string): string {
  const back = safeReturnTo(returnTo);
  return back === "/" ? "/sign-in" : `/sign-in?return_to=${encodeURIComponent(back)}`;
}

export function goToSignIn(): void {
  window.location.assign(signInUrl(window.location.pathname + window.location.search));
}

export function goToSignedOut(): void {
  window.location.assign("/sign-in?signed_out=1");
}
