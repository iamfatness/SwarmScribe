// What the person reads for each error code. The title is ours; the detail is the
// console's or leader's own message, which is fixed text by design (never an echo of input)
// and is always rendered as text. Codes come from the C3 handoff note
// (docs/superpowers/plans/2026-10-03-fleet-console-c3-notes.md), the console's
// errors.py/api/errors.py, the registry and grants modules, and the leader's admin routes.

import { ApiError, NETWORK_ERROR, isAbort } from "./client";

export interface ErrorText {
  title: string;
  detail: string | null;
}

export const ERROR_TITLES: Record<string, string> = {
  // The console's own session and requests.
  unauthenticated: "Your session has ended. Sign in again.",
  csrf_failed: "This page is out of date. Reload the console, then try again.",
  forbidden: "Your role does not allow this.",
  actor_not_representable:
    "Your identity cannot be passed to the leader, so the console will not act for you.",
  invalid_request: "The request was not accepted as sent.",
  too_large: "The request is too large (over 64 KiB).",
  unavailable: "The console is temporarily unavailable. Try again shortly.",
  internal: "The console hit an unexpected error.",
  method_not_allowed: "The console does not accept that request.",
  not_found: "Not found.",
  conflict: "That conflicts with the current state.",
  bad_request: "The request was refused.",
  [NETWORK_ERROR]: "The console could not be reached. Check your connection.",
  // Added by the proxy (handoff note "Codes the proxy adds").
  leader_not_found: "This leader is not visible to you.",
  leader_unreachable: "The leader cannot be reached right now.",
  leader_credential_revoked:
    "The leader revoked the console's credential. A console administrator must replace it.",
  leader_credential_unreadable:
    "The console cannot open its stored credential for this leader. A console administrator must replace it.",
  leader_credential_rejected: "The leader does not accept the console's credential.",
  bad_gateway:
    "The leader's answer could not be used. If this was an action, check whether it happened before repeating it.",
  leader_disabled: "This leader is disabled in the console.",
  // Console administration.
  exists: "That already exists.",
  last_admin: "The last console administrator cannot be removed.",
  invalid_scope: "That scope is not valid.",
  invalid_labels: "Those labels are not valid.",
  invalid_credential: "That is not a console credential.",
  invalid_name: "That name is not valid.",
  invalid_url: "That leader URL is not allowed.",
  invalid_principal: "That principal is not valid.",
  credential_required: "A new URL needs the credential for that URL too.",
  use_rotate: "Replace a credential with Rotate credential.",
  unknown_provider: "That sign-in provider is not offered.",
  // The leader's own action errors (passed through with their codes).
  not_retryable: "Only failed or cancelled jobs can be retried.",
  not_open: "Only queued or leased jobs can be changed.",
  already_open: "The recording already has a queued or leased job.",
  already_completed: "This version of the recording was already transcribed.",
  not_consented: "The recording is not consented or is no longer present.",
  recording_changed: "The recording changed since the job was made.",
  disabled: "The location is disabled. Enable it first.",
  overlaps: "That location overlaps another location.",
  root_unavailable: "The leader cannot use that folder.",
  invalid_root: "That folder is not valid on the leader.",
  rate_limited: "Too many requests. Wait, then try again.",
};

function titleForStatus(status: number): string {
  if (status === 403) return ERROR_TITLES.forbidden as string;
  if (status === 404) return ERROR_TITLES.not_found as string;
  if (status === 409) return ERROR_TITLES.conflict as string;
  if (status === 422) return ERROR_TITLES.invalid_request as string;
  if (status === 429) return ERROR_TITLES.rate_limited as string;
  if (status >= 500) return "The leader or the console failed to answer.";
  return ERROR_TITLES.bad_request as string;
}

export function describeError(error: unknown): ErrorText {
  if (error instanceof ApiError) {
    const title = ERROR_TITLES[error.code] ?? titleForStatus(error.status);
    const parts: string[] = [];
    if (error.message && error.message !== title && !error.message.startsWith("The console answered HTTP")) {
      parts.push(error.message);
    }
    if (error.retryAfter !== null) parts.push(`Try again in ${error.retryAfter} seconds.`);
    return { title, detail: parts.length > 0 ? parts.join(" ") : null };
  }
  if (isAbort(error)) return { title: "The request was cancelled.", detail: null };
  return { title: ERROR_TITLES.internal as string, detail: null };
}
