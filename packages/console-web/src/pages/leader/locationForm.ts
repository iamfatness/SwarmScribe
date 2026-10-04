// The add-location form's data and rules. The rules mirror what the console enforces
// (ConsoleLocationIn in packages/console/.../api/proxy.py) and the leader's own LocationIn
// (packages/leader/.../api/admin_models.py, storage validate_key, protocol channel labels),
// so the person hears about a mistake beside the field instead of from a refused request.
// The leader stays the authority: a value this accepts can still be refused there.

import type { ChannelMode, LocationIn, RequiredDevice } from "../../api/types";

export interface LocationForm {
  name: string;
  root: string;
  input_prefix: string;
  output_prefix: string;
  pool: string;
  /** "" leaves the leader's default (any). */
  required_device: RequiredDevice | "";
  /** "" leaves the leader's default (900). */
  scan_interval_s: string;
  channel_mode: ChannelMode;
  left: string;
  right: string;
}

/** Fields the person has not set are empty: the leader applies its own defaults to them. */
export const EMPTY_FORM: LocationForm = {
  name: "",
  root: "",
  input_prefix: "",
  output_prefix: "",
  pool: "",
  required_device: "",
  scan_interval_s: "",
  channel_mode: "mono",
  left: "Left",
  right: "Right",
};

export type LocationField =
  | "name"
  | "root"
  | "input_prefix"
  | "output_prefix"
  | "pool"
  | "scan_interval_s"
  | "left"
  | "right";

export type LocationErrors = Partial<Record<LocationField, string>>;

/** The order the fields appear in, for moving focus to the first mistake. */
export const FIELD_ORDER: LocationField[] = [
  "name",
  "root",
  "input_prefix",
  "output_prefix",
  "pool",
  "scan_interval_s",
  "left",
  "right",
];

const NAME_RULE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/;
const NAME_MESSAGE =
  "Start with a letter or digit, then use only letters, digits, dots, underscores and hyphens, " +
  "up to 100 characters.";
const ROOT_MESSAGE =
  "Enter an absolute folder path on the leader, such as /srv/intake or C:\\recordings " +
  "(up to 1000 characters).";
const PREFIX_MESSAGE =
  "Leave empty, or enter a relative folder ending in one /, such as incoming/ " +
  "(no .. or empty parts, no colon or backslash).";
const INTERVAL_MESSAGE = "Enter a whole number of seconds from 30 to 604800, or leave it empty.";
const LABEL_LIMIT = 40;
const MIN_INTERVAL = 30;
const MAX_INTERVAL = 7 * 86400;
const MAX_PATH = 1000;

const WINDOWS_DRIVE = /^[A-Za-z]:[\\/]/;
const WINDOWS_UNC = /^(?:\\\\|\/\/)[^\\/]+[\\/]+[^\\/]+/;
const UNPRINTABLE = /[\p{C}\p{Zl}\p{Zp}]/u;

function length(text: string): number {
  return [...text].length;
}

/** A character below U+0020, or U+007F when `del` is set. */
function hasControl(text: string, del = false): boolean {
  return [...text].some((ch) => {
    const code = ch.codePointAt(0) ?? 0;
    return code < 0x20 || (del && code === 0x7f);
  });
}

/** Absolute under POSIX or Windows rules: the console accepts either, the leader decides. */
function isAbsoluteRoot(root: string): boolean {
  if (length(root) < 1 || length(root) > MAX_PATH || hasControl(root)) return false;
  return root.startsWith("/") || WINDOWS_DRIVE.test(root) || WINDOWS_UNC.test(root);
}

/** The leader's prefix rule: empty, or a storage key plus one trailing slash. */
function isPrefix(prefix: string): boolean {
  if (prefix === "") return true;
  if (!prefix.endsWith("/") || length(prefix) > MAX_PATH) return false;
  const key = prefix.slice(0, -1);
  if (key === "" || key.startsWith("/") || key.includes("\\") || key.includes(":")) return false;
  if (hasControl(key, true)) return false;
  return key
    .split("/")
    .every((part) => part !== "" && part !== "." && part !== ".." && !/[. ]$/.test(part));
}

function labelProblem(label: string): string | null {
  if (length(label) < 1 || length(label) > LABEL_LIMIT) {
    return `Enter 1 to ${LABEL_LIMIT} characters.`;
  }
  if (label !== label.trim()) return "Remove the space at the start or end.";
  if (UNPRINTABLE.test(label)) return "Remove control characters and line breaks.";
  return null;
}

/** What is wrong with the form, by field; empty when it can be sent. */
export function validateLocation(form: LocationForm): LocationErrors {
  const errors: LocationErrors = {};
  if (!NAME_RULE.test(form.name.trim())) errors.name = NAME_MESSAGE;
  if (!isAbsoluteRoot(form.root.trim())) errors.root = ROOT_MESSAGE;
  if (!isPrefix(form.input_prefix.trim())) errors.input_prefix = PREFIX_MESSAGE;
  if (!isPrefix(form.output_prefix.trim())) errors.output_prefix = PREFIX_MESSAGE;
  if (form.pool.trim() !== "" && !NAME_RULE.test(form.pool.trim())) errors.pool = NAME_MESSAGE;
  const interval = form.scan_interval_s.trim();
  if (interval !== "") {
    const seconds = Number(interval);
    if (!/^\d+$/.test(interval) || seconds < MIN_INTERVAL || seconds > MAX_INTERVAL) {
      errors.scan_interval_s = INTERVAL_MESSAGE;
    }
  }
  if (form.channel_mode !== "mono") {
    const left = labelProblem(form.left);
    const right = labelProblem(form.right);
    if (left !== null) errors.left = left;
    if (right !== null) errors.right = right;
    if (left === null && right === null && form.left.toLowerCase() === form.right.toLowerCase()) {
      errors.right = "The two labels must differ, ignoring upper and lower case.";
    }
  }
  return errors;
}

/**
 * The request body: only what the person set, so the leader applies its own defaults to the
 * rest. Channel labels go with a split mode only (the leader refuses them with mono); they
 * are sent as typed, since a space at either end is a mistake the form already reported.
 */
export function locationBody(form: LocationForm): LocationIn {
  const body: LocationIn = { name: form.name.trim(), root: form.root.trim() };
  const input = form.input_prefix.trim();
  const output = form.output_prefix.trim();
  const pool = form.pool.trim();
  const interval = form.scan_interval_s.trim();
  if (input !== "") body.input_prefix = input;
  if (output !== "") body.output_prefix = output;
  if (pool !== "") body.pool = pool;
  if (form.required_device !== "") body.required_device = form.required_device;
  if (interval !== "") body.scan_interval_s = Number(interval);
  if (form.channel_mode !== "mono") {
    body.channel_mode = form.channel_mode;
    body.channel_labels = [form.left, form.right];
  }
  return body;
}
