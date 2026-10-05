// What the person reads for each error code. The title is ours; the detail is the
// console's or leader's own message, which is fixed text by design (never an echo of input)
// and is always rendered as text. Codes come from the C3 handoff note
// (docs/superpowers/plans/2026-10-03-fleet-console-c3-notes.md), the console's
// errors.py/api/errors.py, the registry and grants modules, and the leader's admin routes.

import { ApiError, BAD_PATH, BAD_RESPONSE, NETWORK_ERROR, isAbort } from "./client";

export interface ErrorText {
  title: string;
  detail: string | null;
}

export const ERROR_TITLES: Record<string, string> = {
  // The console's own session and requests.
  unauthenticated: "You are signed out. Sign in again.",
  csrf_failed: "This page is out of date. Reload it, then try again.",
  forbidden: "Your role does not allow this.",
  actor_not_representable:
    "The leader cannot be told who you are, so the console will not act for you.",
  invalid_request: "The console did not accept that as it was sent.",
  too_large: "That is too much to send at once (over 64 KiB).",
  unavailable: "The console is not answering just now. Try again shortly.",
  internal: "Something went wrong in the console.",
  method_not_allowed: "The console does not take that kind of request.",
  not_found: "There is nothing there.",
  conflict: "That no longer fits how things stand. Refresh, then look again.",
  bad_request: "The console refused that.",
  [BAD_RESPONSE]: "The console's answer could not be read. Reload the page, then try again.",
  [BAD_PATH]: "Something went wrong in the console.",
  [NETWORK_ERROR]: "The console could not be reached. Check your connection.",
  // Added by the proxy (handoff note "Codes the proxy adds").
  leader_not_found: "You cannot see this leader.",
  leader_unreachable: "The leader is not answering right now.",
  leader_credential_revoked:
    "The leader revoked the console's credential. A console administrator must replace it.",
  leader_credential_unreadable:
    "The console cannot open the credential it holds for this leader. A console administrator must replace it.",
  leader_credential_rejected: "The leader does not accept the console's credential.",
  bad_gateway:
    "The leader's answer could not be used. If you were changing something, check whether it happened before you try again.",
  leader_disabled: "This leader is switched off in the console.",
  // Console administration.
  exists: "That already exists.",
  last_admin: "The last console administrator cannot be removed.",
  invalid_scope: "That is not a way to say which leaders.",
  invalid_labels: "Those labels are not valid.",
  invalid_credential: "That is not a console credential.",
  invalid_name: "That name is not valid.",
  invalid_url: "The console may not call that address.",
  invalid_principal: "That is not a group, an address or a domain the console can use.",
  credential_required: "A new address needs the credential for that address too.",
  use_rotate: "To change only the credential, use Replace credential.",
  unknown_provider: "That way of signing in is not offered here.",
  // The leader's own action errors (passed through with their codes).
  not_retryable: "Only failed or cancelled jobs can be tried again.",
  not_open: "Only jobs that are waiting or being worked on can be changed.",
  already_open: "This recording already has a job waiting or being worked on.",
  already_completed: "This version of the recording already has a transcript.",
  not_consented: "The recording is not consented, or is no longer there.",
  recording_changed: "The recording changed after the job was made.",
  disabled: "The location is switched off. Switch it on first.",
  overlaps: "That location overlaps another location.",
  root_unavailable: "The leader cannot use that folder.",
  invalid_root: "That folder is not valid on the leader.",
  rate_limited: "Too many requests. Wait a little, then try again.",
};

function titleForStatus(status: number): string {
  if (status === 403) return ERROR_TITLES.forbidden as string;
  if (status === 404) return ERROR_TITLES.not_found as string;
  if (status === 409) return ERROR_TITLES.conflict as string;
  if (status === 422) return ERROR_TITLES.invalid_request as string;
  if (status === 429) return ERROR_TITLES.rate_limited as string;
  if (status >= 500) return "The leader or the console did not answer.";
  return ERROR_TITLES.bad_request as string;
}

export function describeError(error: unknown): ErrorText {
  if (error instanceof ApiError) {
    const title = ERROR_TITLES[error.code] ?? titleForStatus(error.status);
    const parts: string[] = [];
    if (error.message && error.message !== title && !error.message.startsWith("The console answered HTTP")) {
      // The server's own text may already end "; try again". With a Retry-After the panel says
      // how long, so that tail goes rather than being said twice.
      parts.push(
        error.retryAfter === null ? error.message : error.message.replace(/[;,.]?\s*try again\.?$/i, "."),
      );
    }
    if (error.retryAfter !== null) parts.push(`Try again in ${error.retryAfter} seconds.`);
    return { title, detail: parts.length > 0 ? parts.join(" ") : null };
  }
  if (isAbort(error)) return { title: "That request was stopped.", detail: null };
  return { title: ERROR_TITLES.internal as string, detail: null };
}
