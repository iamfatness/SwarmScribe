// What the person reads for each error code. Every sentence is ours: for a code this file
// knows, the server's own message is never shown. Those messages are written for a log
// ("this leader is disabled in the console", "that principal already has a grant on that
// scope") and speak the protocol's words. Where a message carries a fact the person needs
// (the role an action needs, the limit a leader sets, the location a new one overlaps), the
// fact is read out of it and said in our own sentence. Only a code this file has never heard
// of shows its text, beneath a calm title of ours and marked as the answer's own words.
// Codes come from the C3 handoff note
// (docs/superpowers/plans/2026-10-03-fleet-console-c3-notes.md), the console's
// errors.py/api/errors.py, the registry and grants modules, and the leader's admin routes.

import { ApiError, BAD_PATH, BAD_RESPONSE, NETWORK_ERROR, isAbort } from "./client";
import { ROLE_RANK } from "./roles";
import type { Role } from "./types";

export interface ErrorText {
  title: string;
  /** The line beneath the title: what to do next, or how long to wait. */
  detail: string | null;
  /** False when asking again cannot work until somebody changes something. */
  retryable: boolean;
  /** True when the way on is the fleet page: this leader is not there for the person. */
  toFleet: boolean;
}

/** What the page knows that the error does not: the leader it is about, and the person's role on it. */
export interface ErrorContext {
  leader?: { name: string; role: Role };
}

/**
 * A refusal the web app makes itself, before anything is sent (a name that is not a name, an
 * address without https://). Its message is our own copy, so it is shown beneath the title.
 */
export class OwnRefusal extends ApiError {
  constructor(code: string, message: string) {
    super(422, code, message);
    this.name = "OwnRefusal";
  }
}

interface Copy {
  title: string;
  detail?: string;
  retryable?: false;
  toFleet?: true;
}

/** Titles that name the leader when the page knows it ("us-1 is not answering right now."). */
const NAMED: Record<string, (leader: string | null) => string> = {
  leader_unreachable: (leader) => `${leader ?? "The leader"} is not answering right now.`,
  leader_disabled: (leader) => `${leader ?? "This leader"} is switched off in the console.`,
  leader_credential_revoked: (leader) => `${leader ?? "The leader"} revoked the console's credential.`,
  leader_credential_rejected: (leader) =>
    `${leader ?? "The leader"} does not accept the console's credential.`,
  leader_credential_unreadable: (leader) =>
    `The console cannot open the credential it holds for ${leader ?? "this leader"}.`,
};

const named = (code: string): string => (NAMED[code] as (leader: null) => string)(null);

const COPY: Record<string, Copy> = {
  // The console's own session and requests.
  unauthenticated: { title: "You are signed out. Sign in again." },
  csrf_failed: { title: "This page is out of date. Reload it, then try again." },
  forbidden: { title: "Your role does not allow this." },
  actor_not_representable: {
    title: "The leader cannot be told who you are, so the console will not act for you.",
  },
  invalid_request: {
    title: "The console did not accept that as it was sent.",
    detail: "Check what you entered, then try again.",
  },
  too_large: { title: "That is too much to send at once (over 64 KiB)." },
  unavailable: { title: "The console is not answering just now. Try again shortly." },
  internal: { title: "Something went wrong in the console." },
  method_not_allowed: { title: "The console does not take that kind of request." },
  not_found: { title: "There is nothing there." },
  conflict: { title: "That no longer fits how things stand. Refresh, then look again." },
  bad_request: { title: "The console refused that." },
  [BAD_RESPONSE]: {
    title: "The console's answer could not be read. Reload the page, then try again.",
  },
  [BAD_PATH]: { title: "Something went wrong in the console." },
  [NETWORK_ERROR]: { title: "The console could not be reached. Check your connection." },
  // Added by the proxy (handoff note "Codes the proxy adds").
  leader_not_found: {
    title: "You cannot see this leader.",
    detail: "It is no longer in the console, or you no longer have a role on it.",
    retryable: false,
    toFleet: true,
  },
  leader_unreachable: { title: named("leader_unreachable") },
  leader_credential_revoked: {
    title: named("leader_credential_revoked"),
    detail: "A console administrator must replace it.",
    retryable: false,
  },
  leader_credential_unreadable: {
    title: named("leader_credential_unreadable"),
    detail: "A console administrator must replace it.",
  },
  leader_credential_rejected: {
    title: named("leader_credential_rejected"),
    detail: "A console administrator can replace it under Administration.",
  },
  bad_gateway: {
    title: "The leader's answer could not be used.",
    detail: "If you were changing something, check whether it happened before you try again.",
  },
  leader_disabled: {
    title: named("leader_disabled"),
    detail: "A console administrator can switch it on under Administration.",
    retryable: false,
  },
  // Console administration.
  exists: { title: "That already exists." },
  last_admin: {
    title: "The last console administrator cannot be removed.",
    detail: "Add another one first.",
  },
  invalid_scope: {
    title: "That is not a way to say which leaders.",
    detail: "Write all, leader:<name> or label:<key>=<value>.",
  },
  invalid_labels: {
    title: "Those labels are not valid.",
    detail:
      "One key=value per line, such as region=eu. Keys are lowercase letters, digits and . _ - ; " +
      "values are printable characters without spaces; at most 32 labels.",
  },
  invalid_credential: {
    title: "That is not a console credential.",
    detail: "It is the 43-character value that swarmscribe-admin console create printed on the leader.",
  },
  invalid_name: {
    title: "That name is not valid.",
    detail: "Letters, digits, . _ - ; starts with a letter or digit; at most 100.",
  },
  invalid_url: {
    title: "The console may not call that address.",
    detail:
      "A leader's address starts with https:// and names the leader's machine. It cannot point " +
      "at this machine or at a cloud's metadata service, and it carries no user name, ? or #.",
  },
  invalid_principal: {
    title: "That is not a group, an address or a domain the console can use.",
  },
  credential_required: { title: "A new address needs the credential for that address too." },
  use_rotate: { title: "To change only the credential, use Replace credential." },
  unknown_provider: { title: "That way of signing in is not offered here." },
  // The leader's own action errors (passed through with their codes).
  not_retryable: { title: "Only failed or cancelled jobs can be tried again." },
  not_open: { title: "Only jobs that are waiting or being worked on can be changed." },
  already_open: { title: "This recording already has a job waiting or being worked on." },
  already_completed: { title: "This version of the recording already has a transcript." },
  not_consented: { title: "The recording is not consented, or is no longer there." },
  recording_changed: { title: "The recording changed after the job was made." },
  disabled: { title: "The location is switched off. Switch it on first." },
  overlaps: { title: "That location overlaps another location." },
  root_unavailable: { title: "The leader cannot use that folder." },
  invalid_root: { title: "That folder is not valid on the leader." },
  rate_limited: { title: "Too many requests. Wait a little, then try again." },
};

/** Every known code's title, as it reads when the page does not know which leader it is. */
export const ERROR_TITLES: Record<string, string> = Object.fromEntries(
  Object.entries(COPY).map(([code, copy]) => [code, copy.title]),
);

const ROLES = "(viewer|operator|admin)";
const NEEDS_ROLE = new RegExp(`\\bneeds the ${ROLES} role\\b`);
const LIMITED_TO = new RegExp(`\\bis limited to ${ROLES}\\b`);
const OVERLAPS = /\boverlaps location '([^']{1,100})'/;

const aRole = (role: string): string => `${role === "viewer" ? "a" : "an"} ${role}`;

/**
 * A refusal for want of a role. The role an action needs, and the limit a leader sets on this
 * console, are read out of the fixed sentence the console or the leader sends; nothing else of
 * it is shown. A leader can let a console act only up to a lower role than the person has
 * here (its administrator's choice when the console's credential was made), and then the
 * refusal is the leader's limit, not the person's role.
 */
function forbidden(message: string, context: ErrorContext | undefined): Pick<ErrorText, "title" | "detail"> {
  const needed = NEEDS_ROLE.exec(message)?.[1] as Role | undefined;
  const limit = LIMITED_TO.exec(message)?.[1];
  const needs = needed !== undefined ? `This needs ${aRole(needed)}.` : null;
  const held = context?.leader?.role;
  const leader = context?.leader?.name ?? "This leader";
  const change = "Whoever runs the leader can change that.";
  if (limit !== undefined) {
    return {
      title: `${leader} lets this console act only as ${aRole(limit)}.`,
      detail: needs === null ? change : `${needs} ${change}`,
    };
  }
  if (needed !== undefined && held !== undefined && ROLE_RANK[held] >= ROLE_RANK[needed]) {
    return {
      title: `${leader} lets this console act only up to a lower role.`,
      detail: `${needs} ${change}`,
    };
  }
  if (needs === null && /\bneeds a console administrator\b/.test(message)) {
    return { title: (COPY.forbidden as Copy).title, detail: "This needs a console administrator." };
  }
  return { title: (COPY.forbidden as Copy).title, detail: needs };
}

function titleForStatus(status: number): string {
  if (status === 403) return ERROR_TITLES.forbidden as string;
  if (status === 404) return ERROR_TITLES.not_found as string;
  if (status === 409) return ERROR_TITLES.conflict as string;
  if (status === 422) return ERROR_TITLES.invalid_request as string;
  if (status === 429) return ERROR_TITLES.rate_limited as string;
  if (status >= 500) return "The leader or the console did not answer.";
  return ERROR_TITLES.bad_request as string;
}

function join(...parts: (string | null | undefined)[]): string | null {
  const said = parts.filter((part): part is string => typeof part === "string" && part !== "");
  return said.length > 0 ? said.join(" ") : null;
}

export function describeError(error: unknown, context?: ErrorContext): ErrorText {
  if (error instanceof ApiError) {
    const wait = error.retryAfter !== null ? `Try again in ${error.retryAfter} seconds.` : null;
    const copy = COPY[error.code];
    if (copy === undefined) {
      // A code this file has never heard of: a calm title of ours by status, and the answer's
      // own text beneath it, marked as such. Never the text as the headline.
      const said =
        error.message !== "" && !error.message.startsWith("The console answered HTTP")
          ? `The answer said: ${error.message}`
          : null;
      return { title: titleForStatus(error.status), detail: join(said, wait), retryable: true, toFleet: false };
    }
    let title = NAMED[error.code]?.(context?.leader?.name ?? null) ?? copy.title;
    let detail: string | null = copy.detail ?? null;
    if (error instanceof OwnRefusal) {
      detail = error.message;
    } else if (error.code === "forbidden") {
      ({ title, detail } = forbidden(error.message, context));
    } else if (error.code === "overlaps") {
      const other = OVERLAPS.exec(error.message)?.[1];
      if (other !== undefined) detail = `It overlaps ${other}.`;
    }
    // "Try again shortly." gives way to the wait itself when it is known: said once.
    if (wait !== null) title = title.replace(/ Try again shortly\.$/, "");
    return {
      title,
      detail: join(detail, wait),
      retryable: copy.retryable !== false,
      toFleet: copy.toFleet === true,
    };
  }
  if (isAbort(error)) {
    return { title: "That request was stopped.", detail: null, retryable: true, toFleet: false };
  }
  return { title: ERROR_TITLES.internal as string, detail: null, retryable: true, toFleet: false };
}
