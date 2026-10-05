// Display helpers. Times are shown in the browser's locale and time zone.

const COUNT = new Intl.NumberFormat();
const CLOCK = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const DATE_TIME = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });
const DAY_MS = 24 * 60 * 60 * 1000;

/** A count with locale grouping; an en dash for null, undefined, NaN or infinity. */
export function formatCount(value: number | null | undefined): string {
  return value === null || value === undefined || !Number.isFinite(value) ? "–" : COUNT.format(value);
}

/** 45 s, 12 min, 3 h 5 min, 2 d 4 h. Negative and non-finite inputs read as 0 s. */
export function formatDuration(seconds: number): string {
  const s = Number.isFinite(seconds) ? Math.max(0, Math.floor(seconds)) : 0;
  if (s < 60) return `${s} s`;
  const minutes = Math.floor(s / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return minutes % 60 ? `${hours} h ${minutes % 60} min` : `${hours} h`;
  const days = Math.floor(hours / 24);
  return hours % 24 ? `${days} d ${hours % 24} h` : `${days} d`;
}

/** A time: the clock alone within the last day, the date and time otherwise. */
export function formatTime(iso: string, now: number = Date.now()): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return iso;
  return Math.abs(now - at.getTime()) < DAY_MS ? CLOCK.format(at) : DATE_TIME.format(at);
}

/**
 * The oldest queued job's age now: its age when the snapshot was taken (by the leader's
 * clock) plus the time since the snapshot (by ours). Null when nothing was queued.
 */
export function oldestQueuedAge(
  ageAtSnapshot: number | null,
  takenAt: string | null,
  now: number,
): number | null {
  if (ageAtSnapshot === null || !Number.isFinite(ageAtSnapshot)) return null;
  const taken = takenAt === null ? Number.NaN : new Date(takenAt).getTime();
  const since = Number.isNaN(taken) ? 0 : Math.max(0, (now - taken) / 1000);
  return ageAtSnapshot + since;
}

/** "default 2, gpu 1", pools sorted by name; "none" when empty. */
export function formatPools(byPool: Record<string, number>): string {
  const entries = Object.entries(byPool).sort(([a], [b]) => a.localeCompare(b));
  return entries.length === 0 ? "none" : entries.map(([pool, n]) => `${pool} ${formatCount(n)}`).join(", ");
}

/** Labels as "key=value" strings, sorted. */
export function labelPairs(labels: Record<string, string>): string[] {
  return Object.entries(labels)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([key, value]) => `${key}=${value}`);
}

const SMALL_NUMBERS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"];

/** "one try", "three tries", "12 tries": small counts as words, the way a person says them. */
export function formatTries(count: number): string {
  const n = Number.isFinite(count) ? Math.max(0, Math.floor(count)) : 0;
  return `${SMALL_NUMBERS[n] ?? formatCount(n)} ${n === 1 ? "try" : "tries"}`;
}

/** "1 leader", "3 followers": a count with its noun (the plural adds an s). */
export function countOf(count: number, noun: string): string {
  return `${formatCount(count)} ${count === 1 ? noun : `${noun}s`}`;
}

const LAST_ERRORS: Record<string, string> = {
  timeout: "It took too long to answer.",
  connect_error: "It could not be reached.",
  transport_error: "The connection to it broke.",
  bad_response: "Its answer could not be understood.",
  credential_unreadable: "The console cannot open its stored credential for it.",
  credential_rejected: "It does not accept the console's credential.",
  credential_revoked: "It revoked the console's credential.",
  forbidden: "It refused the console's request.",
  invalid_target: "Its address is not one the console may call.",
  locked: "The console was busy with another check.",
  error: "Something went wrong that the console did not expect.",
};

/** The last failed check in words: the leader's own code is never shown as copy. */
export function describeLastError(code: string): string {
  const known = LAST_ERRORS[code];
  if (known !== undefined) return known;
  const http = /^http_(\d{3})$/.exec(code);
  if (http !== null) return `It answered with an error (${http[1]}).`;
  return "The check did not work.";
}
