import { useCallback, type ReactNode } from "react";
import { ApiError, api } from "../../api/client";
import { Link, useLocation } from "../../app/router";
import { useSession } from "../../app/session";
import { usePageTitle } from "../../app/usePageTitle";
import { usePoll, type PollState } from "../../app/usePoll";

export const ADMIN_PAGES = [
  { path: "/admin/leaders", label: "Leaders" },
  { path: "/admin/grants", label: "Grants" },
  { path: "/admin/admins", label: "Console administrators" },
] as const;

/** A console administration list: loaded on open and after each change, never on a timer. */
export function useAdminList<T>(path: string): PollState<T[]> {
  const load = useCallback((signal: AbortSignal) => api.get<T[]>(path, signal), [path]);
  return usePoll(load, null, path);
}

/**
 * The administration pages' frame: their own navigation, and a plain refusal for a person
 * who is not a console administrator (the API would answer 403 anyway). The children are
 * rendered only for an administrator, so nothing is requested for anyone else.
 */
export function AdminFrame({ title, children }: { title: string; children: ReactNode }) {
  const { session } = useSession();
  const { pathname } = useLocation();
  usePageTitle(title);
  return (
    <>
      <h1>{title}</h1>
      {session.console_admin ? (
        <>
          <nav aria-label="Administration">
            <ul className="tab-list">
              {ADMIN_PAGES.map((page) => (
                <li key={page.path}>
                  <Link
                    to={page.path}
                    className="tab-link"
                    aria-current={pathname === page.path ? "page" : undefined}
                  >
                    {page.label}
                  </Link>
                </li>
              ))}
            </ul>
          </nav>
          {children}
        </>
      ) : (
        <p>Console administration needs a console administrator. Ask one to add you.</p>
      )}
    </>
  );
}

/** "key=value" lines to labels; null when a line is not of that form. */
export function parseLabels(text: string): Record<string, string> | null {
  const labels: Record<string, string> = {};
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (line === "") continue;
    const at = line.indexOf("=");
    if (at < 1 || at === line.length - 1) return null;
    labels[line.slice(0, at).trim()] = line.slice(at + 1).trim();
  }
  return labels;
}

export function labelsText(labels: Record<string, string>): string {
  return Object.entries(labels)
    .map(([key, value]) => `${key}=${value}`)
    .join("\n");
}

const MAX_LABELS = 32;
const LABEL_KEY = /^[a-z0-9][a-z0-9._-]{0,62}$/;
const LABEL_VALUE = /^[!-~]{1,255}$/;

/**
 * The registry's label rules (leaders.validate_labels): at most 32; keys lowercase letters,
 * digits and . _ - (so never "="); values 1 to 255 printable ASCII characters, no spaces.
 */
export function labelsAreValid(labels: Record<string, string>): boolean {
  const entries = Object.entries(labels);
  return (
    entries.length <= MAX_LABELS &&
    entries.every(([key, value]) => LABEL_KEY.test(key) && LABEL_VALUE.test(value))
  );
}

/** The same label sets, whatever the order of their lines. */
export function sameLabels(a: Record<string, string>, b: Record<string, string>): boolean {
  const left = Object.entries(a).sort(([x], [y]) => (x < y ? -1 : x > y ? 1 : 0));
  const right = Object.entries(b).sort(([x], [y]) => (x < y ? -1 : x > y ? 1 : 0));
  return (
    left.length === right.length &&
    left.every(([key, value], i) => key === right[i]?.[0] && value === right[i]?.[1])
  );
}

/**
 * Runs a request that carries a credential. If the answer's text were ever to quote it, the
 * text is dropped and only our own title for the error code is shown: a credential must not
 * come back out in an error message.
 */
export async function withSecret<T>(secret: string, work: () => Promise<T>): Promise<T> {
  try {
    return await work();
  } catch (caught) {
    if (secret !== "" && caught instanceof ApiError && caught.message.includes(secret)) {
      throw new ApiError(caught.status, caught.code, "", caught.retryAfter);
    }
    throw caught;
  }
}
