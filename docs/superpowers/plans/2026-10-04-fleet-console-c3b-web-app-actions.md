# Fleet Console C3b — Web App: Leader Drill-down, Actions and Administration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In the console web app, a person opens a leader and works on it through five tabs — pools and followers (drain, revoke), jobs (filter by state; retry, cancel, priority), locations (add, enable, disable, scan), join tokens (create, list, revoke; the plaintext shown once in a dialog with copy) and the consent report — with every action their role does not reach shown disabled with the role it needs; and console administrators manage leaders (add, edit, rotate the credential, remove), grants and console administrators. Component tests cover each tab and page; Playwright covers the actions end to end, keyboard-only, with an axe scan of every page and dialog in both themes.

**Architecture:** Each drill-down tab is its own URL (`/leaders/<name>/<tab>`) and its own component reading live through the console's proxy (`/api/leaders/<name>/…`) when it opens, on Refresh and after each action — never on a timer. The leader's header (health, the person's role) comes from the fleet context C3a already polls. Actions go through three shared pieces: `ActionButton` (disabled below the allow-list's role, naming it), `useAction` (busy and error state) and a native `<dialog>` wrapper (`Dialog`, `ConfirmDialog`). Administration pages call `/api/admin/…` directly and render nothing but a refusal for a person who is not a console administrator.

**Tech Stack:** As C3a: React 19.3.0, TypeScript 6.0.3, Vite 8.3.2, Vitest 5.0.3 with Testing Library, Playwright 1.63.0 with @axe-core/playwright 4.13.0. No new dependency.

**Spec:** `docs/superpowers/specs/2026-10-03-fleet-console-design.md` (approved; the authority) — section 6 (drill-down, role handling, accessibility) and section 9's web app line, with sections 5.1, 5.2, 5.4 and 7. The backend contract is `docs/superpowers/plans/2026-10-03-fleet-console-c3-notes.md`, `packages/console/src/swarmscribe_console/proxy.py` (the allow-list), `api/proxy.py`, `api/admin.py`, `api/models.py`, and the leader's `packages/leader/src/swarmscribe_leader/api/admin_models.py`. This plan builds on C3a: `docs/superpowers/plans/2026-10-04-fleet-console-c3a-web-app-overview.md` — read its "Decisions" and "Global Constraints" first; they all still hold.

**Precondition:** C3a is merged to `main`, and this work branches from `main`:

```bash
git switch main
git pull
git switch -c fleet-console-c3b
```

**Node floor.** Node 24.15 or later in the 24 line (`engines` is `>=24.15 <25`, `.npmrc` sets `engine-strict=true`, and jsdom's dependencies need 24.15). On an older Node `npm ci` stops with `EBADENGINE`: upgrade Node; do not relax `engine-strict`.

Before Task 1, in `packages/console-web`: `npm ci`, then `npm test` (**118 tests in 12 files** pass: C3a's final total), `npm run typecheck`, `npm run lint`, `npm run build`, `npm run e2e` (12 pass).

**Test-count rule.** Every "Expected: PASS — N tests in M files" below is *the previous total + this task's new tests*, starting from 118 in 12 files. If a count differs, compare against the previous task's total plus the number of tests the task adds (named in its Expected line); a difference means a test was dropped or duplicated, not that the number is stale. Running tally: Task 1 +4 (122, 13 files); Task 2 +4 (126, 15); Task 3 +7 (133, 16); Task 4 +3 (136, 17); Task 5 +3 (139, 19); Task 6 +9 (148, 20).

**Test isolation.** C3a's `src/test/setup.ts` resets, after every test, the session-ended latch, the 401 handler, the CSRF token and the last-input time, as well as the DOM and the address. A test that answers a 401 or fakes the clock therefore cannot stop a later test's polls. Do not add per-file resets for these; if `setup.ts` lacks one of the four, add it there first.

## Global Constraints

- Every constraint of C3a holds (the CSP, exact dependency pins and no new dependency, nothing stored in the browser but the theme, no `console.*`, no `style` props, no `dangerouslySetInnerHTML`, the lint and type-check settings, npm commands run in `packages/console-web`, `python -m uv run …` from the repository root, no backend file changes, the commit trailer, do not push).
- Spec 6, verbatim: "**Leader drill-down:** tabs for pools and followers (drain/revoke), jobs (filter by state; retry/cancel/priority), locations (add/enable/disable, ingest), join tokens (create/list/revoke), consent report."; "Actions the person's role does not allow are shown disabled with the required role."; "Accessible (WCAG 2.1 AA: keyboard, contrast, labels), responsive down to tablet width, light and dark themes."
- Spec 5.4, verbatim: "Join-token plaintext from `tokens create` is passed through to the browser once and never stored or logged by the console."; "Leader errors are passed through with their code; the console adds `leader_unreachable` (503) when a leader cannot be reached."
- Spec 5.2, verbatim: "Console admins manage the registry and grants only; they hold no leader role unless granted one."
- Spec 1, verbatim: "A leader that stops answering is shown as unreachable within one minute; nothing else in the console stops working."
- Handoff note, join token: "the plaintext is in the `201` body of `POST /api/leaders/<name>/tokens`, once, with `Cache-Control: no-store`. Never put it in a URL, router state, `localStorage`, query cache that persists, or logs."
- Handoff note, roles: "`admin` is needed for join tokens (list, create, revoke), follower revoke and location add, enable and disable; `operator` for job retry, cancel and priority, follower drain and location scan." The source is `src/api/roles.ts` (C3a), which `roles.test.ts` holds equal to `proxy.py`.
- Handoff note, errors: under `/api/leaders/<name>/…`, `leader_not_found` means not visible to the person; `not_found` is an action the console does not offer or the leader's own 404; on a POST answered `bad_gateway`, whether the action happened is unknown.
- Request bodies are the leader's own models: `PriorityIn {priority: int, -1000..1000, strict}`; `TokenIn {pool, expires_in_seconds 60..7776000, max_uses 1..10000}`; `LocationIn {name, root, input_prefix, output_prefix, pool, required_device any|cuda|cpu, scan_interval_s 30..604800, channel_mode mono|stereo_split|auto, channel_labels}` — `channel_labels` may be sent only with a split mode (the leader refuses it with `mono`). Unknown fields are refused, so send only these.
- Admin bodies are the console's models (`api/models.py`): `LeaderIn`, `LeaderEdit` (a `credential` only together with a `base_url` that really changes, else `use_rotate`; a changed `base_url` without one is `credential_required`), `CredentialIn`, `GrantIn`, `ConsoleAdminIn`.

## Review Focus

1. **The join token's plaintext** → visible only in the one dialog; after it closes it is in no DOM node, URL, storage or React state, and nothing logs it. — Task 5 (`TokensTab.test.tsx`), Task 7 (`drilldown.spec.ts`).
2. **A stray Enter or double click on a destructive action** → every destructive action asks first; the confirm dialog puts focus on "Close", and the confirm button is disabled while the request runs. — Task 1 (`ConfirmDialog`), Task 7 (the keyboard test asserts where focus starts).
3. **A leader that refuses what the console allowed** (its credential's cap is lower than the person's console role), or answers a conflict (`not_retryable`, `not_open`, `disabled`) → the leader's own code and message are shown where the action was taken; nothing is retried. — Task 3 (`JobsTab.test.tsx` passthrough), Task 7 (`us-1` capped at operator).
4. **A leader that goes away mid-use** → the tab shows the error with a retry; the header, the other tabs' links and the other leaders keep working; an action answered `bad_gateway` says the outcome is unknown. — Task 3 (`JobsTab.test.tsx` unreachable), Task 7 (leader-down test); the wording is C3a's `errors.ts`.
5. **A console credential typed into the browser** → a password field with autocomplete off, never rendered back, gone when its dialog closes; malformed labels are refused before anything is sent. — Task 6 (`admin.test.tsx`), Task 7 (`admin.spec.ts`).
6. **A person below the role** → the control is disabled and names the role; the tokens tab does not even ask the leader; a person who is not a console administrator gets no administration link and no administration request. — Task 1 (`ActionButton.test.tsx`), Task 3, Task 5, Task 6.
7. **A leader name with a dot, or a drill-down address reloaded** → URLs always end with the tab, `/leaders/<name>` redirects to its first tab, and a reload lands on the same tab with its filters. — Task 2 (`tabs.ts`, `App.tsx`), Task 7 (reload test).

## Decisions this plan makes (the spec is silent)

- **Tabs are links, not ARIA tabs.** Each tab is a URL, so the tab strip is a `<nav>` of links with `aria-current="page"`; a reload or a shared link opens the same tab.
- **Drill-down URLs always end with the tab** (`/leaders/<name>/pools`), because the console serves `index.html` only when the last path segment has no dot and leader names may contain dots. `/leaders/<name>` redirects to `/leaders/<name>/pools`.
- **No timed refresh of the tabs.** The leader audits every read a person makes, so a timer would fill its audit log. A tab loads when it opens, on its Refresh button and after each action. The header's health and role refresh every 10 s with the fleet (C3a).
- **Pools come from the latest poll** (the snapshot's `pools` and `follower_pools`, with its time shown); there is no pools route on the leader. Followers, jobs, locations, tokens and the consent report are read live.
- **Which actions show for which state.** Jobs: Retry on `failed` and `cancelled`; Priority and Cancel on `queued` and `leased` (the leader's own rules; anything else is its `409`). Followers: Drain on `active`; Revoke on `active` and `draining`. Locations: Scan now and Disable when enabled; Enable when disabled. Tokens: Revoke unless already revoked.
- **Confirmation.** Asked before: cancel job, revoke follower, disable location, revoke token, remove leader, remove grant, remove console administrator. Not asked before: retry, priority (its own dialog), drain, scan, enable, and the add/create forms.
- **Row actions carry their row in their accessible name** (`aria-label="Retry job 1a2b3c4d"`) while showing the short verb.
- **Short ids.** Jobs, followers and tokens are shown by the first 8 characters of their UUID.
- **Jobs list:** the newest 100 (`limit=100`), filtered by state and by location (the location names come from the latest poll); both filters live in the address (`?state=…&location=…`).
- **Join tokens below admin.** The tab says "Join tokens need the admin role on <leader>. Your role is <role>." and sends nothing (the list itself needs admin).
- **Token form:** pool, days until expiry (1 to 90, sent as seconds), uses (1 to 10000). The created dialog shows the token in a read-only field that selects itself on focus, a Copy button (`navigator.clipboard.writeText`, with a "select and copy" fallback message), and Done.
- **Location form:** all of `LocationIn`; the two channel labels appear only for a split mode and are sent only then.
- **Administration is three pages** — `/admin/leaders`, `/admin/grants`, `/admin/admins` — under an "Administration" item shown only to console administrators (`SessionInfo.console_admin`).
- **Labels are edited as text**, one `key=value` per line. **Editing a leader sends only what changed**; the credential field appears, and is required, only when the address changes. Rotation is its own dialog (`PUT …/credential`).
- **Credentials** are typed into `type="password"`, `autocomplete="off"` fields and never displayed; the list shows only when and by whom the credential was set, or "Revoked by the leader".

## Questions for the owner

None blocks this plan; each has a recommendation and the plan is written to it.

1. **Refreshing the drill-down tabs.** On open, on Refresh and after actions only (planned), or also every 60 s. *Recommendation: as planned; every timed read is an audit row on the leader.*
2. **Which actions ask for confirmation** (list under "Decisions"). *Recommendation: as planned. Drain is not confirmed because it is reversible only by the follower finishing, but it loses no work.*
3. **Wording.** The health, role and error sentences are plain-English drafts ("needs operator", "Figures as of", "This leader is not visible to you."). *Recommendation: accept, then adjust copy in `errors.ts` and the pages as operators use it.*
4. **Manual assistive-technology pass.** axe finds the mechanical failures; it does not prove a screen-reader session works. *Recommendation: one manual pass with NVDA on the overview, one tab and one dialog before release, tracked outside this plan.*
5. **The last-administrator gap** (C2 follow-up M3: an administrator can add an entry that no sign-in can produce and then remove their own). The admins page shows the API's `last_admin` refusal but cannot detect that case. *Recommendation: fix in the backend as the follow-up describes; no UI change here.*

## Spec problems found

- **Spec 6 "pools and followers (drain/revoke)"** names pools as if they were listed live; the leader has no pools route, and the allow-list has none. Pool figures therefore come from the latest poll (at most about 15 s old), with its time shown.
- **Spec 1 "Every admin action available in `swarmscribe-admin` can be taken from the console."** The leader's `console create|list|revoke`, `whoami` and `login-config` are not on the console's allow-list (C2b ruling: person-only on the leader). The web app offers exactly the allow-list.
- **"Never kept in state after the dialog closes"** can be met for the app's own state, the DOM, the URL and storage, and is tested; the browser's memory of a fetched response until garbage collection is outside the app's control.

## File Structure

```
packages/console-web/
  src/
    styles.css                          MOD  dialogs, tabs, forms, action notes (Task 1)
    app/useAction.ts                    NEW  busy and error state of one action (Task 1)
    components/Dialog.tsx               NEW  modal <dialog> wrapper (Task 1)
    components/ConfirmDialog.tsx        NEW  ask before a destructive action (Task 1)
    components/ActionButton.tsx         NEW  role-gated action button (Task 1)
    test/renderApp.tsx                  NEW  the whole app at a path, signed in (Task 2)
    pages/leader/tabs.ts                NEW  the tab list and leaderUrl (Task 2; grows in Tasks 3–5)
    pages/leader/common.tsx             NEW  useLeaderRead, ReadState, ActionNotice (Task 2)
    pages/leader/PoolsTab.tsx           NEW  (Task 2)
    pages/leader/LeaderPage.tsx         NEW  header, tab strip, tab content (Task 2; grows in Tasks 3–5)
    pages/leader/JobsTab.tsx            NEW  (Task 3)
    pages/leader/LocationsTab.tsx       NEW  (Task 4)
    pages/leader/TokensTab.tsx          NEW  (Task 5)
    pages/leader/ConsentTab.tsx         NEW  (Task 5)
    pages/admin/AdminFrame.tsx          NEW  admin navigation, refusal, label parsing (Task 6)
    pages/admin/PrincipalFields.tsx     NEW  (Task 6)
    pages/admin/AdminLeadersPage.tsx    NEW  (Task 6)
    pages/admin/AdminGrantsPage.tsx     NEW  (Task 6)
    pages/admin/AdminAdminsPage.tsx     NEW  (Task 6)
    pages/FleetPage.tsx                 MOD  leader names link to the drill-down (Task 2)
    App.tsx                             MOD  leader routes (Task 2); admin routes and navigation (Task 6)
  e2e/tests/
    drilldown.spec.ts                   NEW  (Task 7)
    admin.spec.ts                       NEW  (Task 7)
    a11y.spec.ts                        MOD  every page and dialog (Task 7)
```

Test files sit beside what they test and are listed in each task.

---

### Task 1: Action primitives — dialog, confirmation, role-gated button

**Files:**
- Create: `packages/console-web/src/app/useAction.ts`, `src/components/Dialog.tsx`, `src/components/ConfirmDialog.tsx`, `src/components/ActionButton.tsx`
- Modify: `packages/console-web/src/styles.css` (append)
- Test: `packages/console-web/src/components/ActionButton.test.tsx`

**Interfaces:**
- Consumes (C3a): `can`, `neededRole`, `LeaderAction` from `src/api/roles.ts`; `Role` from `src/api/types.ts`; `ApiError` from `src/api/client.ts`; `ErrorPanel` from `src/components/ErrorPanel.tsx`; the `<dialog>` stand-in in `src/test/setup.ts`.
- Produces:
  - `src/app/useAction.ts`: `useAction(): { busy: boolean; error: unknown; run: (work: () => Promise<unknown>) => Promise<boolean>; clear: () => void }` — `run` never throws and resolves `true` on success.
  - `src/components/Dialog.tsx`: `Dialog({ title, onClose, children })` — mounted means open; Escape calls `onClose`; focus returns to the opener on unmount.
  - `src/components/ConfirmDialog.tsx`: `ConfirmDialog({ title, message, confirmLabel, onConfirm: () => Promise<unknown>, onClose })` — buttons "Close" (first, so focused) and `confirmLabel`; on failure it stays open and shows the error.
  - `src/components/ActionButton.tsx`: `ActionButton({ held: Role, action: LeaderAction, onClick, danger?, busy?, name?, children })` — `name` becomes the button's `aria-label`; below the role it is disabled and described by "needs <role>".
  - CSS classes: `breadcrumb`, `tab-list`, `tab-link`, `tab-panel`, `section-head`, `filters`, `actions`, `action`, `needs-role`, `action-notice`, `error-text`, `dialog`, `dialog-title`, `dialog-buttons`, `form-grid`, `form-panel`, `field`, `field-check`, `field-help`.

- [ ] **Step 1: Write the failing test**

`packages/console-web/src/components/ActionButton.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import { ActionButton } from "./ActionButton";
import { ConfirmDialog } from "./ConfirmDialog";

describe("ActionButton", () => {
  it("is enabled when the role reaches the action", async () => {
    const onClick = vi.fn();
    render(
      <ActionButton held="operator" action="jobs.retry" onClick={onClick}>
        Retry
      </ActionButton>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onClick).toHaveBeenCalledOnce();
    expect(screen.queryByText(/needs/)).not.toBeInTheDocument();
  });

  it("is disabled and names the role needed otherwise", () => {
    render(
      <ActionButton held="operator" action="tokens.create" onClick={vi.fn()}>
        Create join token
      </ActionButton>,
    );
    const button = screen.getByRole("button", { name: "Create join token" });
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleDescription("needs admin");
  });
});

describe("ConfirmDialog", () => {
  it("runs the action and closes", async () => {
    const onConfirm = vi.fn(async () => undefined);
    const onClose = vi.fn();
    render(
      <ConfirmDialog
        title="Cancel job?"
        message="The job stops."
        confirmLabel="Cancel job"
        onConfirm={onConfirm}
        onClose={onClose}
      />,
    );
    expect(screen.getByRole("dialog", { name: "Cancel job?" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Cancel job" }));
    expect(onConfirm).toHaveBeenCalledOnce();
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("stays open and shows the error when the action fails", async () => {
    const onClose = vi.fn();
    render(
      <ConfirmDialog
        title="Revoke?"
        message="m"
        confirmLabel="Revoke"
        onConfirm={async () => {
          throw new ApiError(503, "leader_unreachable", "leader eu-1 cannot be reached; try again", 15);
        }}
        onClose={onClose}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Revoke" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The leader cannot be reached right now.");
    expect(onClose).not.toHaveBeenCalled();
  });
});
```

Run: `npx vitest run src/components/ActionButton.test.tsx`
Expected: FAIL — `Failed to resolve import "./ActionButton"`.

- [ ] **Step 2: Write `useAction` and the dialog**

`packages/console-web/src/app/useAction.ts`:

```ts
import { useCallback, useState } from "react";

export interface ActionState {
  busy: boolean;
  error: unknown;
  /** Runs `work`; resolves true when it succeeded. Never throws. */
  run: (work: () => Promise<unknown>) => Promise<boolean>;
  clear: () => void;
}

/** One action's busy and error state, for a button or a dialog's submit. */
export function useAction(): ActionState {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const run = useCallback(async (work: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await work();
      return true;
    } catch (caught) {
      setError(caught);
      return false;
    } finally {
      setBusy(false);
    }
  }, []);
  const clear = useCallback(() => setError(null), []);
  return { busy, error, run, clear };
}
```

`packages/console-web/src/components/Dialog.tsx`:

```tsx
import { useEffect, useId, useRef, type ReactNode } from "react";

/**
 * A modal <dialog>: the browser traps focus and closes it on Escape. Mounted means open:
 * render it only while it should show. On close, focus returns to what had it before.
 */
export function Dialog({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const dialog = ref.current;
    if (dialog === null) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (!dialog.open) dialog.showModal();
    return () => {
      if (dialog.open) dialog.close();
      opener?.focus();
    };
  }, []);

  return (
    <dialog
      ref={ref}
      className="dialog"
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
    >
      <h2 id={titleId} className="dialog-title">
        {title}
      </h2>
      {children}
    </dialog>
  );
}
```

- [ ] **Step 3: Write the confirmation dialog and the action button**

`packages/console-web/src/components/ConfirmDialog.tsx`:

```tsx
import { useAction } from "../app/useAction";
import { Dialog } from "./Dialog";
import { ErrorPanel } from "./ErrorPanel";

/**
 * Asks before a destructive action; shows the action's error in place if it fails. The safe
 * choice comes first, so the dialog puts focus on it and a stray Enter never confirms.
 */
export function ConfirmDialog({
  title,
  message,
  confirmLabel,
  onConfirm,
  onClose,
}: {
  title: string;
  message: string;
  confirmLabel: string;
  onConfirm: () => Promise<unknown>;
  onClose: () => void;
}) {
  const action = useAction();
  return (
    <Dialog title={title} onClose={onClose}>
      <p>{message}</p>
      {action.error !== null && <ErrorPanel error={action.error} />}
      <div className="dialog-buttons">
        <button type="button" className="button" onClick={onClose}>
          Close
        </button>
        <button
          type="button"
          className="button button-danger"
          disabled={action.busy}
          onClick={async () => {
            if (await action.run(onConfirm)) onClose();
          }}
        >
          {action.busy ? "Working…" : confirmLabel}
        </button>
      </div>
    </Dialog>
  );
}
```

`packages/console-web/src/components/ActionButton.tsx`:

```tsx
import { useId, type ReactNode } from "react";
import { can, neededRole, type LeaderAction } from "../api/roles";
import type { Role } from "../api/types";

/**
 * A leader action's button. When the person's console role for the leader is below the
 * action's (the console's allow-list), the button is disabled and says which role it needs;
 * the console and the leader would refuse it anyway.
 */
export function ActionButton({
  held,
  action,
  onClick,
  danger = false,
  busy = false,
  name,
  children,
}: {
  /** The person's console role on this leader (FleetLeader.role). */
  held: Role;
  action: LeaderAction;
  onClick: () => void;
  danger?: boolean;
  busy?: boolean;
  /** The full accessible name when the visible text needs its row for context ("Retry job 1a2b3c4d"). */
  name?: string;
  children: ReactNode;
}) {
  const noteId = useId();
  const allowed = can(held, action);
  return (
    <span className="action">
      <button
        type="button"
        className={danger ? "button button-danger" : "button"}
        disabled={!allowed || busy}
        aria-label={name}
        aria-describedby={allowed ? undefined : noteId}
        onClick={onClick}
      >
        {children}
      </button>
      {!allowed && (
        <span id={noteId} className="needs-role">
          needs {neededRole(action)}
        </span>
      )}
    </span>
  );
}
```

Run: `npx vitest run src/components/ActionButton.test.tsx`
Expected: PASS — 4 tests.

- [ ] **Step 4: Append the styles**

Append to the end of `packages/console-web/src/styles.css`:

```css

/* ---- Drill-down and administration (C3b) ---- */

.breadcrumb {
  margin: 0 0 0.25rem;
  color: var(--muted);
}

.tab-list {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem;
  list-style: none;
  margin: 1rem 0 0;
  padding: 0;
  border-bottom: 1px solid var(--rule);
}

.tab-link {
  display: inline-block;
  padding: 0.5rem 0.9rem;
  color: var(--text);
  text-decoration: none;
  border-bottom: 3px solid transparent;
}

.tab-link[aria-current="page"] {
  border-bottom-color: var(--accent);
  font-weight: 600;
}

.tab-panel h2 {
  margin-top: 1rem;
}

h3 {
  font-size: 1.05rem;
  margin: 1.25rem 0 0.5rem;
}

.section-head,
.filters {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem 1rem;
  margin: 0.75rem 0;
}

.filters {
  justify-content: flex-start;
}

.section-head h3,
.section-head p {
  margin: 0;
}

td.actions {
  white-space: nowrap;
}

.action {
  display: inline-flex;
  flex-direction: column;
  align-items: flex-start;
  margin-right: 0.5rem;
}

.needs-role {
  font-size: 0.75rem;
  color: var(--muted);
}

.action-notice {
  min-height: 1.5rem;
  margin: 0.25rem 0;
  color: var(--ok-fg);
  font-weight: 600;
}

.error-text {
  color: var(--bad-fg);
}

.dialog {
  width: min(36rem, calc(100vw - 2rem));
  max-height: calc(100vh - 2rem);
  overflow-y: auto;
  padding: 1.25rem 1.5rem;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg);
  color: var(--text);
}

.dialog::backdrop {
  background: rgb(0 0 0 / 0.5);
}

.dialog-title {
  margin-top: 0;
}

.dialog-buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin-top: 1rem;
}

.form-grid {
  display: grid;
  gap: 0.75rem;
}

.form-panel {
  max-width: 36rem;
  margin-top: 2rem;
  padding: 1rem 1.25rem;
  border: 1px solid var(--rule);
  border-radius: var(--radius);
  background: var(--surface);
}

.form-panel h2 {
  margin-top: 0;
}

.field {
  display: grid;
  gap: 0.25rem;
}

.field-check {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.field-help {
  font-size: 0.8125rem;
  color: var(--muted);
}
```

- [ ] **Step 5: Run everything and commit**

Run: `npm test`
Expected: PASS — 122 tests in 13 files (118 + 4 new in `ActionButton.test.tsx`).

Run: `npm run typecheck` then `npm run lint`
Expected: both exit 0 with no output.

```bash
git add packages/console-web/src
git commit -m "Console web app: dialog, confirmation and role-gated action button

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The leader page with the pools and followers tab

**Files:**
- Create: `packages/console-web/src/pages/leader/tabs.ts`, `src/pages/leader/common.tsx`, `src/pages/leader/PoolsTab.tsx`, `src/pages/leader/LeaderPage.tsx`, `src/test/renderApp.tsx`
- Modify: `packages/console-web/src/App.tsx` (leader routes), `src/pages/FleetPage.tsx` (`LeaderName` becomes a link)
- Test: `packages/console-web/src/pages/leader/LeaderPage.test.tsx`, `src/pages/leader/PoolsTab.test.tsx`

**Interfaces:**
- Consumes: Task 1's `ActionButton`, `ConfirmDialog`, `useAction`; C3a's `api`, `leaderPath`, `usePoll`/`PollState`, `useFleet`, `Link`, `matchPath`, `useNavigate`, `usePageTitle`, `ErrorPanel`, `HealthBadge`, `NotFoundPage`, `formatCount`, `formatTime`, `mockFetch`, `reply`, the fixtures; types `FleetLeader`, `FollowerOut`, `FollowerRevoked`.
- Produces:
  - `src/pages/leader/tabs.ts`: `TABS` (a readonly list of `{ id, label }`; this task: `pools` only), `type TabId`, `leaderUrl(name: string, tab?: TabId): string` (default `"pools"`).
  - `src/pages/leader/common.tsx`: `interface TabProps { leader: FleetLeader }`; `useLeaderRead<T>(name: string, rest: string): PollState<T>` (GET `/api/leaders/<name>/<rest>`, no timer); `ActionNotice({ message: string | null })` (`role="status"`); `ReadState<T>({ read, what, children: (data: T) => ReactNode })`; `RefreshButton({ read })`; `shortId(id: string): string` (first 8 characters).
  - `src/pages/leader/LeaderPage.tsx`: `LeaderPage({ name: string, tab: string })`. Its `TabContent` switch has one `case` per entry of `TABS`; Tasks 3–5 add theirs.
  - `src/test/renderApp.tsx`: `renderApp(path: string, options?: { fleet?: FleetLeader[]; session?: SessionInfo }): FetchMock` — renders `<App />` at `path` with `GET /api/session` and `GET /api/fleet` answered.

- [ ] **Step 1: Write the test helper and the failing tests**

`packages/console-web/src/test/renderApp.tsx`:

```tsx
import { render } from "@testing-library/react";
import { App } from "../App";
import type { FleetLeader, SessionInfo } from "../api/types";
import { mockFetch, reply, type FetchMock } from "./fetchMock";
import { SESSION, leader } from "./fixtures";

/**
 * The whole app at `path`, signed in, with GET /api/session and GET /api/fleet answered.
 * Returns the fetch mock so a test can add the routes it needs.
 */
export function renderApp(
  path: string,
  { fleet = [leader()], session = SESSION }: { fleet?: FleetLeader[]; session?: SessionInfo } = {},
): FetchMock {
  window.history.replaceState(null, "", path);
  const mock = mockFetch()
    .on("GET /api/session", reply(200, session))
    .on("GET /api/fleet", reply(200, fleet));
  render(<App />);
  return mock;
}
```

`packages/console-web/src/pages/leader/LeaderPage.test.tsx`:

```tsx
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { reply } from "../../test/fetchMock";
import { history } from "../../test/fixtures";
import { renderApp } from "../../test/renderApp";

describe("leader drill-down", () => {
  it("shows the leader, the person's role and the tabs", async () => {
    renderApp("/leaders/eu-1/pools").on("GET /api/leaders/eu-1/followers", reply(200, []));
    expect(await screen.findByRole("heading", { level: 1, name: "eu-1" })).toBeInTheDocument();
    expect(await screen.findByText(/Actions that need a higher role are shown disabled/)).toHaveTextContent(
      "Your role on eu-1: operator.",
    );
    const tabs = screen.getByRole("navigation", { name: "eu-1 sections" });
    const pools = within(tabs).getByRole("link", { name: "Pools and followers" });
    expect(pools).toHaveAttribute("aria-current", "page");
    expect(pools).toHaveAttribute("href", "/leaders/eu-1/pools");
  });

  it("says a leader the person cannot see is not visible to them", async () => {
    renderApp("/leaders/secret-1/pools");
    expect(await screen.findByText(/This leader is not visible to you/)).toBeInTheDocument();
  });

  it("moves focus to the leader's heading when the page changes from the fleet to a leader", async () => {
    renderApp("/")
      .on("GET /api/leaders/eu-1/history?hours=24", reply(200, history()))
      .on("GET /api/leaders/eu-1/followers", reply(200, []));
    await userEvent.click(await screen.findByRole("link", { name: "eu-1" }));
    const heading = await screen.findByRole("heading", { level: 1, name: "eu-1" });
    await waitFor(() => expect(heading).toHaveFocus());
  });
});
```

`packages/console-web/src/pages/leader/PoolsTab.test.tsx`:

```tsx
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { FollowerOut } from "../../api/types";
import { reply } from "../../test/fetchMock";
import { renderApp } from "../../test/renderApp";

const FOLLOWER: FollowerOut = {
  id: "33333333-3333-4333-8333-333333333333",
  pool: "default",
  state: "active",
  device: "cuda",
  last_seen_at: "2026-10-04T11:59:40Z",
  created_at: "2026-10-01T00:00:00Z",
  leases: 1,
};

describe("pools and followers tab", () => {
  it("shows pools from the latest poll and drains a follower", async () => {
    let drained = false;
    const mock = renderApp("/leaders/eu-1/pools")
      .on("GET /api/leaders/eu-1/followers", () =>
        reply(200, [{ ...FOLLOWER, state: drained ? "draining" : "active" }]),
      )
      .on(`POST /api/leaders/eu-1/followers/${FOLLOWER.id}/drain`, () => {
        drained = true;
        return reply(200, { ...FOLLOWER, state: "draining" });
      });
    const pools = await screen.findByRole("region", { name: "Pools" });
    expect(within(pools).getByRole("rowheader", { name: "gpu" })).toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: "Drain follower 33333333" }));
    expect(await screen.findByText("Follower 33333333 is draining.")).toBeInTheDocument();
    expect(
      mock.callsTo(`POST /api/leaders/eu-1/followers/${FOLLOWER.id}/drain`)[0]?.headers["X-CSRF-Token"],
    ).toBe("csrf-token-1");
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Drain follower 33333333" })).not.toBeInTheDocument(),
    );
  });
});
```

Run: `npx vitest run src/pages/leader`
Expected: FAIL — both files fail (the app has no leader route yet: "Unable to find … eu-1" / "Pools"). This task adds 4 tests: 3 in `LeaderPage.test.tsx` (the header and tabs, a leader not visible, focus moving to the `h1` when the page changes) and 1 in `PoolsTab.test.tsx`.

- [ ] **Step 2: Write the tab list and the shared tab pieces**

`packages/console-web/src/pages/leader/tabs.ts`:

```ts
// The drill-down's tabs. Each is its own URL, /leaders/<name>/<tab>, always ending with the
// tab so a leader name with a dot is never the last segment (the console answers 404, not
// index.html, for a last segment with a dot).

export const TABS = [
  { id: "pools", label: "Pools and followers" },
] as const;

export type TabId = (typeof TABS)[number]["id"];

export function leaderUrl(name: string, tab: TabId = "pools"): string {
  return `/leaders/${encodeURIComponent(name)}/${tab}`;
}
```

`packages/console-web/src/pages/leader/common.tsx`:

```tsx
import { useCallback, type ReactNode } from "react";
import { api, leaderPath } from "../../api/client";
import type { FleetLeader } from "../../api/types";
import { usePoll, type PollState } from "../../app/usePoll";
import { ErrorPanel } from "../../components/ErrorPanel";

/** What every drill-down tab gets. */
export interface TabProps {
  leader: FleetLeader;
}

/**
 * A live read through the console's proxy: loaded when the tab opens, again on refresh()
 * and after each action. Not on a timer: every read of a leader by a person is audited by
 * the leader, so a timer would fill its audit log.
 */
export function useLeaderRead<T>(name: string, rest: string): PollState<T> {
  const load = useCallback(
    (signal: AbortSignal) => api.get<T>(leaderPath(name, rest), signal),
    [name, rest],
  );
  return usePoll(load, null, `${name}/${rest}`);
}

/** The result of the last action, read out by screen readers when it changes. */
export function ActionNotice({ message }: { message: string | null }) {
  return (
    <p className="action-notice" role="status">
      {message ?? ""}
    </p>
  );
}

/** A tab's read: the error with a retry, a loading line, or the content. */
export function ReadState<T>({
  read,
  what,
  children,
}: {
  read: PollState<T>;
  what: string;
  children: (data: T) => ReactNode;
}) {
  if (read.data === undefined) {
    if (read.error !== null) return <ErrorPanel error={read.error} onRetry={read.refresh} />;
    return <p role="status">Loading {what}…</p>;
  }
  return (
    <>
      {read.error !== null && <ErrorPanel error={read.error} onRetry={read.refresh} retryLabel="Reload" />}
      {children(read.data)}
    </>
  );
}

export function RefreshButton({ read }: { read: PollState<unknown> }) {
  return (
    <button type="button" className="button" onClick={read.refresh} disabled={read.loading}>
      Refresh
    </button>
  );
}

export function shortId(id: string): string {
  return id.slice(0, 8);
}
```

- [ ] **Step 3: Write the pools and followers tab**

`packages/console-web/src/pages/leader/PoolsTab.tsx`:

```tsx
import { useState } from "react";
import { api, leaderPath } from "../../api/client";
import type { FollowerOut, FollowerRevoked } from "../../api/types";
import { useAction } from "../../app/useAction";
import { ActionButton } from "../../components/ActionButton";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { ErrorPanel } from "../../components/ErrorPanel";
import { formatCount, formatTime } from "../../lib/format";
import { ActionNotice, ReadState, RefreshButton, shortId, useLeaderRead, type TabProps } from "./common";

function PoolTable({ leader }: TabProps) {
  const status = leader.snapshot?.status;
  if (status === undefined) return <p>No successful poll yet, so pool figures are not known.</p>;
  const names = [
    ...new Set([...status.pools.map((p) => p.pool), ...status.follower_pools.map((p) => p.pool)]),
  ].sort();
  return (
    <>
      <p className="muted">From the poll at {formatTime(leader.snapshot?.taken_at ?? "")}.</p>
      <div className="table-scroll" role="region" aria-label="Pools" tabIndex={0}>
        <table>
          <thead>
            <tr>
              <th scope="col">Pool</th>
              <th scope="col">Queued</th>
              <th scope="col">Leased</th>
              <th scope="col">Active followers</th>
              <th scope="col">Draining</th>
              <th scope="col">Revoked</th>
              <th scope="col">Gone</th>
            </tr>
          </thead>
          <tbody>
            {names.map((name) => {
              const queue = status.pools.find((p) => p.pool === name);
              const followers = status.follower_pools.find((p) => p.pool === name);
              return (
                <tr key={name}>
                  <th scope="row">{name}</th>
                  <td className="num">{formatCount(queue?.queued ?? 0)}</td>
                  <td className="num">{formatCount(queue?.leased ?? 0)}</td>
                  <td className="num">{formatCount(followers?.active ?? 0)}</td>
                  <td className="num">{formatCount(followers?.draining ?? 0)}</td>
                  <td className="num">{formatCount(followers?.revoked ?? 0)}</td>
                  <td className="num">{formatCount(followers?.gone ?? 0)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

export function PoolsTab({ leader }: TabProps) {
  const read = useLeaderRead<FollowerOut[]>(leader.name, "followers");
  const drain = useAction();
  const [revoking, setRevoking] = useState<FollowerOut | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const onDrain = async (follower: FollowerOut) => {
    setNotice(null);
    const ok = await drain.run(() => api.post(leaderPath(leader.name, `followers/${follower.id}/drain`)));
    if (ok) {
      setNotice(`Follower ${shortId(follower.id)} is draining.`);
      read.refresh();
    }
  };

  return (
    <>
      <h3>Pools</h3>
      <PoolTable leader={leader} />
      <div className="section-head">
        <h3>Followers</h3>
        <RefreshButton read={read} />
      </div>
      <ActionNotice message={notice} />
      {drain.error !== null && <ErrorPanel error={drain.error} />}
      <ReadState read={read} what="followers">
        {(followers) =>
          followers.length === 0 ? (
            <p>No followers have joined this leader.</p>
          ) : (
            <div className="table-scroll" role="region" aria-label="Followers" tabIndex={0}>
              <table>
                <thead>
                  <tr>
                    <th scope="col">Follower</th>
                    <th scope="col">Pool</th>
                    <th scope="col">State</th>
                    <th scope="col">Device</th>
                    <th scope="col">Leases</th>
                    <th scope="col">Last seen</th>
                    <th scope="col">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {followers.map((follower) => (
                    <tr key={follower.id}>
                      <th scope="row">
                        <code>{shortId(follower.id)}</code>
                      </th>
                      <td>{follower.pool}</td>
                      <td>{follower.state}</td>
                      <td>{follower.device ?? "–"}</td>
                      <td className="num">{formatCount(follower.leases)}</td>
                      <td>{formatTime(follower.last_seen_at)}</td>
                      <td className="actions">
                        {follower.state === "active" && (
                          <ActionButton
                            held={leader.role}
                            action="followers.drain"
                            busy={drain.busy}
                            onClick={() => void onDrain(follower)}
                            name={`Drain follower ${shortId(follower.id)}`}
                          >
                            Drain
                          </ActionButton>
                        )}
                        {(follower.state === "active" || follower.state === "draining") && (
                          <ActionButton
                            held={leader.role}
                            action="followers.revoke"
                            danger
                            onClick={() => setRevoking(follower)}
                            name={`Revoke follower ${shortId(follower.id)}`}
                          >
                            Revoke
                          </ActionButton>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </ReadState>
      {revoking !== null && (
        <ConfirmDialog
          title={`Revoke follower ${shortId(revoking.id)}?`}
          message="The follower can no longer take work and its leased jobs go back to the queue. It needs a new join token to come back."
          confirmLabel="Revoke follower"
          onClose={() => setRevoking(null)}
          onConfirm={async () => {
            const answer = await api.post<FollowerRevoked>(
              leaderPath(leader.name, `followers/${revoking.id}/revoke`),
            );
            setNotice(
              `Follower ${shortId(revoking.id)} is revoked; ${answer.released} leased jobs went back to the queue.`,
            );
            read.refresh();
          }}
        />
      )}
    </>
  );
}
```

- [ ] **Step 4: Write the leader page**

`packages/console-web/src/pages/leader/LeaderPage.tsx`:

```tsx
import type { FleetLeader } from "../../api/types";
import { useFleet } from "../../app/fleet";
import { Link } from "../../app/router";
import { usePageTitle } from "../../app/usePageTitle";
import { ErrorPanel } from "../../components/ErrorPanel";
import { HealthBadge } from "../../components/HealthBadge";
import { formatTime } from "../../lib/format";
import { NotFoundPage } from "../NotFoundPage";
import { PoolsTab } from "./PoolsTab";
import { TABS, leaderUrl, type TabId } from "./tabs";

function TabContent({ tab, leader }: { tab: TabId; leader: FleetLeader }) {
  switch (tab) {
    case "pools":
      return <PoolsTab leader={leader} />;
  }
}

function HealthNote({ leader }: { leader: FleetLeader }) {
  if (leader.health === "reachable") return null;
  const since = leader.last_success_at
    ? ` The last successful poll was at ${formatTime(leader.last_success_at)}.`
    : "";
  const text: Record<string, string> = {
    unreachable: `The console cannot reach ${leader.name}; reads and actions will fail until it answers.${since}`,
    credential_revoked: `${leader.name} revoked the console's credential. A console administrator must replace it.`,
    disabled: `${leader.name} is disabled in the console; reads and actions are refused.`,
    pending: `${leader.name} has not answered a poll yet.`,
  };
  return <p className="notice">{text[leader.health] ?? `Health: ${leader.health}.`}</p>;
}

export function LeaderPage({ name, tab }: { name: string; tab: string }) {
  const { data, error, refresh } = useFleet();
  const current = TABS.find((t) => t.id === tab);
  usePageTitle(current ? `${name}: ${current.label}` : "Not found");
  if (current === undefined) return <NotFoundPage />;

  if (data === undefined) {
    return (
      <>
        <h1>{name}</h1>
        {error ? <ErrorPanel error={error} onRetry={refresh} /> : <p role="status">Loading…</p>}
      </>
    );
  }
  const leader = data.find((l) => l.name.toLowerCase() === name.toLowerCase());
  if (leader === undefined) {
    return (
      <>
        <h1>{name}</h1>
        <p>
          This leader is not visible to you: it is not registered, or you hold no role on it.{" "}
          <Link to="/">Back to the fleet</Link>.
        </p>
      </>
    );
  }

  return (
    <>
      <p className="breadcrumb">
        <Link to="/">Fleet</Link> / {leader.name}
      </p>
      <div className="page-head">
        <h1>{leader.name}</h1>
        <HealthBadge leader={leader} />
      </div>
      <p className="muted">
        Your role on {leader.name}: <strong>{leader.role}</strong>. Actions that need a higher role are shown
        disabled.
      </p>
      <HealthNote leader={leader} />
      <nav aria-label={`${leader.name} sections`}>
        <ul className="tab-list">
          {TABS.map((t) => (
            <li key={t.id}>
              <Link
                to={leaderUrl(leader.name, t.id)}
                className="tab-link"
                aria-current={t.id === tab ? "page" : undefined}
              >
                {t.label}
              </Link>
            </li>
          ))}
        </ul>
      </nav>
      <section className="tab-panel" aria-labelledby="tab-title">
        <h2 id="tab-title">{current.label}</h2>
        <TabContent key={`${leader.name}/${current.id}`} tab={current.id} leader={leader} />
      </section>
    </>
  );
}
```

- [ ] **Step 5: Route to it and link to it**

Edit `packages/console-web/src/App.tsx` **in place**; do not replace the file (C3a's comments, the `/sign-in` branch outside `SessionProvider` and `startActivityTracking` must survive, and `shell.test.tsx` must keep passing). Four edits:

1. Replace the line `import { RouterProvider, useLocation } from "./app/router";` with `import { RouterProvider, matchPath, useLocation, useNavigate } from "./app/router";`, and add `import { LeaderPage } from "./pages/leader/LeaderPage";` and `import { leaderUrl } from "./pages/leader/tabs";` between the `./pages/FleetPage` and `./pages/NotFoundPage` imports.
2. Replace the `NAV` constant with:

```tsx
const FLEET: NavItem = {
  to: "/",
  label: "Fleet",
  match: (pathname) => pathname === "/" || pathname.startsWith("/leaders/"),
};

/**
 * The page a path belongs to, for moving focus: switching tabs inside one leader's
 * drill-down stays on the page (focus stays on the tab link that was activated); going
 * from the fleet to a leader, or from one leader to another, changes the page.
 */
function pageOf(pathname: string): string {
  const leader = matchPath("/leaders/:name/:tab", pathname) ?? matchPath("/leaders/:name", pathname);
  return leader === null ? pathname : `/leaders/${leader.name as string}`;
}

function Redirect({ to }: { to: string }) {
  const navigate = useNavigate();
  useEffect(() => navigate(to, { replace: true }), [navigate, to]);
  return null;
}
```

3. Replace the body of `SignedInPage` with:

```tsx
function SignedInPage() {
  const { pathname } = useLocation();
  if (pathname === "/") return <FleetPage />;
  const drill = matchPath("/leaders/:name/:tab", pathname);
  if (drill !== null) return <LeaderPage name={drill.name as string} tab={drill.tab as string} />;
  const bare = matchPath("/leaders/:name", pathname);
  if (bare !== null) return <Redirect to={leaderUrl(bare.name as string)} />;
  return <NotFoundPage />;
}
```

4. In `Routes`, replace `<Layout nav={NAV}>` with `<Layout nav={[FLEET]} pageOf={pageOf}>`.

**Focus on a tab switch (decision).** C3a's `Layout` moves focus to the page's `h1` on every path change. With each tab its own path, that would pull focus off the tab link a keyboard user just activated and show the `h1`'s focus ring on every tab change. A tab switch keeps focus where it is (on the tab link, which then carries `aria-current="page"`); the move to the `h1` happens only when the *page* changes. `Layout` takes an optional `pageOf(pathname)` that names the page a path belongs to (default: the path itself, which is C3a's behaviour), and its effect depends on that name, not on the path. The `/leaders/<name>` redirect to the first tab does not change the page either.

In `packages/console-web/src/components/Layout.tsx`, replace

```tsx
export function Layout({ nav, children }: { nav: NavItem[]; children: ReactNode }) {
  const { session, signOut } = useSession();
  const { pathname } = useLocation();
```

with

```tsx
export function Layout({
  nav,
  pageOf = (pathname) => pathname,
  children,
}: {
  nav: NavItem[];
  /** Names the page a path belongs to; focus moves only when this changes (not on a tab switch). */
  pageOf?: (pathname: string) => string;
  children: ReactNode;
}) {
  const { session, signOut } = useSession();
  const { pathname } = useLocation();
  const page = pageOf(pathname);
```

and replace the effect's dependency list `}, [pathname]);` with `}, [page]);`. (`pathname` is still used below for the nav's `aria-current`.) The two tests for this are in Step 1 of this task (focus moves when the page changes) and Task 3, Step 3b (focus stays on a tab switch).

In `packages/console-web/src/pages/FleetPage.tsx`, replace

```tsx
/** The leader's name. C3b turns it into the link to the leader's drill-down. */
export function LeaderName({ leader }: { leader: FleetLeader }): ReactNode {
  return <span className="leader-name">{leader.name}</span>;
}
```

with

```tsx
/** The leader's name, linking to its drill-down. */
export function LeaderName({ leader }: { leader: FleetLeader }): ReactNode {
  return (
    <Link to={leaderUrl(leader.name)} className="leader-name">
      {leader.name}
    </Link>
  );
}
```

and in the same file's imports replace `import { useNavigate, useSearchParam } from "../app/router";` with `import { Link, useNavigate, useSearchParam } from "../app/router";`, and add `import { leaderUrl } from "./leader/tabs";` after the `../lib/format` import.

- [ ] **Step 6: Run everything and commit**

Run: `npm test`
Expected: PASS — 126 tests in 15 files (122 + 4 new: 3 in `LeaderPage.test.tsx`, 1 in `PoolsTab.test.tsx`; `shell.test.tsx` and the C3a overview tests still pass: a row header's name still contains the leader's name).

Run: `npm run typecheck` then `npm run lint`
Expected: both exit 0 with no output.

```bash
git add packages/console-web/src
git commit -m "Console web app: leader drill-down page with pools and followers, drain and revoke

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The jobs tab — filter, retry, cancel, priority

**Files:**
- Create: `packages/console-web/src/pages/leader/JobsTab.tsx`
- Modify: `packages/console-web/src/pages/leader/tabs.ts` (add `jobs`), `src/pages/leader/LeaderPage.tsx` (add its case)
- Test: `packages/console-web/src/pages/leader/JobsTab.test.tsx`

**Interfaces:**
- Consumes: Task 1's `ActionButton`, `ConfirmDialog`, `Dialog`, `useAction`; Task 2's `TabProps`, `useLeaderRead`, `ReadState`, `RefreshButton`, `ActionNotice`, `shortId`, `leaderUrl`, `renderApp`; C3a's `api`, `leaderPath`, `query`, `useNavigate`, `useSearchParam`, `ErrorPanel`, `formatTime`; types `JobOut`, `JobState`.
- Produces: `JobsTab({ leader })`; `JOB_STATES`; `JOB_LIMIT = 100`. Calls `GET jobs?state=&location=&limit=100`, `POST jobs/{id}/retry`, `POST jobs/{id}/cancel`, `POST jobs/{id}/priority` with `{ priority: <integer> }`.

- [ ] **Step 1: Write the failing test**

`packages/console-web/src/pages/leader/JobsTab.test.tsx`:

```tsx
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { JobOut } from "../../api/types";
import { fail, reply } from "../../test/fetchMock";
import { leader } from "../../test/fixtures";
import { renderApp } from "../../test/renderApp";

const JOB_FAILED: JobOut = {
  id: "11111111-1111-4111-8111-111111111111",
  state: "failed",
  location: "intake",
  key: "incoming/a.wav",
  priority: 0,
  attempts: 3,
  max_attempts: 3,
  pool: "default",
  leased_by: null,
  failure_reason: "the engine stopped",
  cancelled_by: null,
  no_speech: null,
  created_at: "2026-10-04T11:00:00Z",
  completed_at: null,
};
const JOB_QUEUED: JobOut = {
  ...JOB_FAILED,
  id: "22222222-2222-4222-8222-222222222222",
  state: "queued",
  attempts: 0,
  failure_reason: null,
};
const JOBS = "GET /api/leaders/eu-1/jobs?limit=100";

describe("jobs tab", () => {
  it("filters jobs by state through the address and the leader query", async () => {
    const mock = renderApp("/leaders/eu-1/jobs")
      .on(JOBS, reply(200, [JOB_FAILED, JOB_QUEUED]))
      .on("GET /api/leaders/eu-1/jobs?state=failed&limit=100", reply(200, [JOB_FAILED]));
    await screen.findByRole("rowheader", { name: "22222222" });
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "State" }), "failed");
    await waitFor(() =>
      expect(screen.queryByRole("rowheader", { name: "22222222" })).not.toBeInTheDocument(),
    );
    expect(window.location.search).toBe("?state=failed");
    expect(mock.callsTo("GET /api/leaders/eu-1/jobs?state=failed&limit=100")).toHaveLength(1);
  });

  it("retries a failed job and cancels a queued one after confirming", async () => {
    const mock = renderApp("/leaders/eu-1/jobs")
      .on(JOBS, reply(200, [JOB_FAILED, JOB_QUEUED]))
      .on(
        `POST /api/leaders/eu-1/jobs/${JOB_FAILED.id}/retry`,
        reply(200, { ...JOB_FAILED, state: "queued" }),
      )
      .on(
        `POST /api/leaders/eu-1/jobs/${JOB_QUEUED.id}/cancel`,
        reply(200, { ...JOB_QUEUED, state: "cancelled" }),
      );
    await userEvent.click(await screen.findByRole("button", { name: "Retry job 11111111" }));
    expect(await screen.findByText("Job 11111111 is queued again.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Cancel job 22222222" }));
    const dialog = screen.getByRole("dialog", { name: "Cancel job 22222222?" });
    expect(mock.callsTo(`POST /api/leaders/eu-1/jobs/${JOB_QUEUED.id}/cancel`)).toHaveLength(0);
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel job" }));
    expect(await screen.findByText("Job 22222222 is cancelled.")).toBeInTheDocument();
    expect(mock.callsTo(`POST /api/leaders/eu-1/jobs/${JOB_QUEUED.id}/cancel`)).toHaveLength(1);
  });

  it("sets a job's priority as an integer", async () => {
    const mock = renderApp("/leaders/eu-1/jobs")
      .on(JOBS, reply(200, [JOB_QUEUED]))
      .on(
        `POST /api/leaders/eu-1/jobs/${JOB_QUEUED.id}/priority`,
        reply(200, { ...JOB_QUEUED, priority: 5 }),
      );
    await userEvent.click(await screen.findByRole("button", { name: "Priority of job 22222222" }));
    const input = screen.getByRole("spinbutton", { name: /Priority/ });
    await userEvent.clear(input);
    await userEvent.type(input, "5");
    await userEvent.click(screen.getByRole("button", { name: "Set priority" }));
    await waitFor(() =>
      expect(mock.callsTo(`POST /api/leaders/eu-1/jobs/${JOB_QUEUED.id}/priority`)[0]?.body).toEqual({
        priority: 5,
      }),
    );
  });

  it("disables actions above the person's role and names the role", async () => {
    renderApp("/leaders/eu-1/jobs", { fleet: [leader({ role: "viewer" })] }).on(
      JOBS,
      reply(200, [JOB_FAILED]),
    );
    const retry = await screen.findByRole("button", { name: "Retry job 11111111" });
    expect(retry).toBeDisabled();
    expect(retry).toHaveAccessibleDescription("needs operator");
  });

  it("shows an unreachable leader's error in the tab and keeps the page", async () => {
    renderApp("/leaders/eu-1/jobs", {
      fleet: [leader({ health: "unreachable", consecutive_failures: 3 })],
    }).on(JOBS, {
      status: 503,
      body: { code: "leader_unreachable", message: "leader eu-1 cannot be reached; try again" },
      headers: { "Retry-After": "15" },
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("The leader cannot be reached right now.");
    expect(screen.getByText(/The console cannot reach eu-1/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Pools and followers" })).toBeInTheDocument();
  });

  it("passes the leader's own refusal through", async () => {
    renderApp("/leaders/eu-1/jobs")
      .on(JOBS, reply(200, [JOB_FAILED]))
      .on(
        `POST /api/leaders/eu-1/jobs/${JOB_FAILED.id}/retry`,
        fail(409, "not_retryable", "only failed or cancelled jobs can be retried"),
      );
    await userEvent.click(await screen.findByRole("button", { name: "Retry job 11111111" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Only failed or cancelled jobs can be retried.",
    );
  });
});
```

Run: `npx vitest run src/pages/leader/JobsTab.test.tsx`
Expected: FAIL — all 6 (there is no `jobs` tab: the page is "Page not found").

- [ ] **Step 2: Write the jobs tab**

`packages/console-web/src/pages/leader/JobsTab.tsx`:

```tsx
import { useState, type FormEvent } from "react";
import { api, leaderPath, query } from "../../api/client";
import type { JobOut, JobState } from "../../api/types";
import { useNavigate, useSearchParam } from "../../app/router";
import { useAction } from "../../app/useAction";
import { ActionButton } from "../../components/ActionButton";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { Dialog } from "../../components/Dialog";
import { ErrorPanel } from "../../components/ErrorPanel";
import { formatTime } from "../../lib/format";
import { ActionNotice, ReadState, RefreshButton, shortId, useLeaderRead, type TabProps } from "./common";
import { leaderUrl } from "./tabs";

export const JOB_STATES: JobState[] = ["queued", "leased", "completed", "failed", "cancelled"];
export const JOB_LIMIT = 100;
const OPEN = new Set(["queued", "leased"]);
const RETRYABLE = new Set(["failed", "cancelled"]);

function PriorityDialog({
  leaderName,
  job,
  onClose,
  onDone,
}: {
  leaderName: string;
  job: JobOut;
  onClose: () => void;
  onDone: () => void;
}) {
  const [value, setValue] = useState(String(job.priority));
  const action = useAction();
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const priority = Number(value);
    const ok = await action.run(() =>
      api.post(leaderPath(leaderName, `jobs/${job.id}/priority`), { priority }),
    );
    if (ok) {
      onDone();
      onClose();
    }
  };
  return (
    <Dialog title={`Priority of job ${shortId(job.id)}`} onClose={onClose}>
      <form onSubmit={(event) => void submit(event)}>
        <label className="field">
          Priority (−1000 to 1000; higher runs first)
          <input
            type="number"
            required
            min={-1000}
            max={1000}
            step={1}
            value={value}
            onChange={(event) => setValue(event.target.value)}
          />
        </label>
        {action.error !== null && <ErrorPanel error={action.error} />}
        <div className="dialog-buttons">
          <button type="submit" className="button button-primary" disabled={action.busy}>
            Set priority
          </button>
          <button type="button" className="button" onClick={onClose}>
            Cancel
          </button>
        </div>
      </form>
    </Dialog>
  );
}

function detail(job: JobOut): string {
  if (job.failure_reason) return job.failure_reason;
  if (job.cancelled_by) return `Cancelled by ${job.cancelled_by}`;
  if (job.leased_by) return `Leased by ${shortId(job.leased_by)}`;
  if (job.no_speech) return "No speech found";
  return "";
}

export function JobsTab({ leader }: TabProps) {
  const navigate = useNavigate();
  const state = useSearchParam("state");
  const location = useSearchParam("location");
  const read = useLeaderRead<JobOut[]>(leader.name, `jobs${query({ state, location, limit: JOB_LIMIT })}`);
  const retry = useAction();
  const [cancelling, setCancelling] = useState<JobOut | null>(null);
  const [prioritising, setPrioritising] = useState<JobOut | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const locations = (leader.snapshot?.status.locations ?? []).map((l) => l.name).sort();

  const setFilter = (next: { state?: string | null; location?: string | null }) => {
    const merged = { state, location, ...next };
    navigate(`${leaderUrl(leader.name, "jobs")}${query(merged)}`, { replace: true });
  };

  const onRetry = async (job: JobOut) => {
    setNotice(null);
    if (await retry.run(() => api.post(leaderPath(leader.name, `jobs/${job.id}/retry`)))) {
      setNotice(`Job ${shortId(job.id)} is queued again.`);
      read.refresh();
    }
  };

  return (
    <>
      <div className="filters">
        <label className="field-inline">
          State
          <select value={state ?? ""} onChange={(event) => setFilter({ state: event.target.value || null })}>
            <option value="">All states</option>
            {JOB_STATES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label className="field-inline">
          Location
          <select
            value={location ?? ""}
            onChange={(event) => setFilter({ location: event.target.value || null })}
          >
            <option value="">All locations</option>
            {location && !locations.includes(location) && <option value={location}>{location}</option>}
            {locations.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <RefreshButton read={read} />
      </div>
      <ActionNotice message={notice} />
      {retry.error !== null && <ErrorPanel error={retry.error} />}
      <ReadState read={read} what="jobs">
        {(jobs) =>
          jobs.length === 0 ? (
            <p>No jobs match.</p>
          ) : (
            <>
              {jobs.length >= JOB_LIMIT && <p className="muted">Showing the newest {JOB_LIMIT} jobs.</p>}
              <div className="table-scroll" role="region" aria-label="Jobs" tabIndex={0}>
                <table>
                  <thead>
                    <tr>
                      <th scope="col">Job</th>
                      <th scope="col">State</th>
                      <th scope="col">Recording</th>
                      <th scope="col">Pool</th>
                      <th scope="col">Priority</th>
                      <th scope="col">Attempts</th>
                      <th scope="col">Created</th>
                      <th scope="col">Detail</th>
                      <th scope="col">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {jobs.map((job) => (
                      <tr key={job.id}>
                        <th scope="row">
                          <code>{shortId(job.id)}</code>
                        </th>
                        <td>{job.state}</td>
                        <td>
                          {job.location}: <span className="mono">{job.key}</span>
                        </td>
                        <td>{job.pool}</td>
                        <td className="num">{job.priority}</td>
                        <td className="num">
                          {job.attempts} of {job.max_attempts}
                        </td>
                        <td>{formatTime(job.created_at)}</td>
                        <td>{detail(job)}</td>
                        <td className="actions">
                          {RETRYABLE.has(job.state) && (
                            <ActionButton
                              held={leader.role}
                              action="jobs.retry"
                              busy={retry.busy}
                              onClick={() => void onRetry(job)}
                              name={`Retry job ${shortId(job.id)}`}
                            >
                              Retry
                            </ActionButton>
                          )}
                          {OPEN.has(job.state) && (
                            <>
                              <ActionButton
                                held={leader.role}
                                action="jobs.priority"
                                onClick={() => setPrioritising(job)}
                                name={`Priority of job ${shortId(job.id)}`}
                              >
                                Priority
                              </ActionButton>
                              <ActionButton
                                held={leader.role}
                                action="jobs.cancel"
                                danger
                                onClick={() => setCancelling(job)}
                                name={`Cancel job ${shortId(job.id)}`}
                              >
                                Cancel
                              </ActionButton>
                            </>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )
        }
      </ReadState>
      {cancelling !== null && (
        <ConfirmDialog
          title={`Cancel job ${shortId(cancelling.id)}?`}
          message={`The job for ${cancelling.key} stops and is not retried unless someone retries it.`}
          confirmLabel="Cancel job"
          onClose={() => setCancelling(null)}
          onConfirm={async () => {
            await api.post(leaderPath(leader.name, `jobs/${cancelling.id}/cancel`));
            setNotice(`Job ${shortId(cancelling.id)} is cancelled.`);
            read.refresh();
          }}
        />
      )}
      {prioritising !== null && (
        <PriorityDialog
          leaderName={leader.name}
          job={prioritising}
          onClose={() => setPrioritising(null)}
          onDone={() => {
            setNotice(`Priority of job ${shortId(prioritising.id)} is set.`);
            read.refresh();
          }}
        />
      )}
    </>
  );
}
```

- [ ] **Step 3: Add the tab**

In `packages/console-web/src/pages/leader/tabs.ts`, replace

```ts
export const TABS = [
  { id: "pools", label: "Pools and followers" },
] as const;
```

with

```ts
export const TABS = [
  { id: "pools", label: "Pools and followers" },
  { id: "jobs", label: "Jobs" },
] as const;
```

In `packages/console-web/src/pages/leader/LeaderPage.tsx`, add `import { JobsTab } from "./JobsTab";` before the `./PoolsTab` import, and replace

```tsx
    case "pools":
      return <PoolsTab leader={leader} />;
  }
```

with

```tsx
    case "pools":
      return <PoolsTab leader={leader} />;
    case "jobs":
      return <JobsTab leader={leader} />;
  }
```

- [ ] **Step 3b: Test that a tab switch keeps focus on the tab link**

Append this test inside the `describe` of `packages/console-web/src/pages/leader/LeaderPage.test.tsx` (the `userEvent`, `waitFor`, `within`, `history` and `reply` imports are already there from Task 2):

```tsx
  it("keeps focus on the tab link when only the tab changes", async () => {
    renderApp("/leaders/eu-1/pools")
      .on("GET /api/leaders/eu-1/followers", reply(200, []))
      .on("GET /api/leaders/eu-1/jobs", reply(200, []));
    const tabs = await screen.findByRole("navigation", { name: "eu-1 sections" });
    const jobs = within(tabs).getByRole("link", { name: "Jobs" });
    await userEvent.click(jobs);
    await waitFor(() => expect(jobs).toHaveAttribute("aria-current", "page"));
    expect(jobs).toHaveFocus();
    expect(screen.getByRole("heading", { level: 1, name: "eu-1" })).not.toHaveFocus();
  });
```

Register the exact jobs route the real `JobsTab` requests (Step 2 above, including its query string) in place of `GET /api/leaders/eu-1/jobs` if they differ. Run `npx vitest run src/pages/leader/LeaderPage.test.tsx`: it passes here, and fails (focus on the `h1`) if `Layout` is not given `pageOf`. Include the file in this task's commit.

- [ ] **Step 4: Run everything and commit**

Run: `npm test`
Expected: PASS — 133 tests in 16 files (126 + 7 new: 6 in `JobsTab.test.tsx`, 1 in `LeaderPage.test.tsx`).

Run: `npm run typecheck` then `npm run lint`
Expected: both exit 0 with no output. (If the type-check reports "Function lacks ending return statement" in `TabContent`, a `TABS` entry has no `case`.)

```bash
git add packages/console-web/src
git commit -m "Console web app: jobs tab with state and location filters, retry, cancel and priority

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The locations tab — add, enable, disable, scan

**Files:**
- Create: `packages/console-web/src/pages/leader/LocationsTab.tsx`
- Modify: `packages/console-web/src/pages/leader/tabs.ts` (add `locations`), `src/pages/leader/LeaderPage.tsx` (add its case)
- Test: `packages/console-web/src/pages/leader/LocationsTab.test.tsx`

**Interfaces:**
- Consumes: as Task 3, plus `formatDuration`; types `LocationOut`, `LocationIn`, `ChannelMode`, `RequiredDevice`.
- Produces: `LocationsTab({ leader })`; `locationBody(form): LocationIn` (trims text, makes `scan_interval_s` a number, includes `channel_labels` only when `channel_mode` is not `mono`). Calls `GET locations`, `POST locations`, `POST locations/{name}/enable|disable|ingest`.

- [ ] **Step 1: Write the failing test**

`packages/console-web/src/pages/leader/LocationsTab.test.tsx`:

```tsx
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { LocationOut } from "../../api/types";
import { reply } from "../../test/fetchMock";
import { leader } from "../../test/fixtures";
import { renderApp } from "../../test/renderApp";
import { locationBody } from "./LocationsTab";

describe("locations tab", () => {
  const LOCATION: LocationOut = {
    id: "l1",
    name: "intake",
    backend: "local",
    root: "/srv/intake",
    input_prefix: "",
    output_prefix: "transcripts/",
    pool: "default",
    required_device: "any",
    scan_interval_s: 900,
    enabled: true,
    last_scan_at: null,
    last_scan_error: "the root folder is not readable",
    scan_requested: false,
    channel_mode: "mono",
    channel_labels: ["Left", "Right"],
  };

  it("sends channel labels only with a split mode", () => {
    const base = {
      name: " n ",
      root: "/srv/x",
      input_prefix: "",
      output_prefix: "transcripts/",
      pool: "default",
      required_device: "any" as const,
      scan_interval_s: "900",
      left: "Host",
      right: "Guest",
    };
    expect(locationBody({ ...base, channel_mode: "mono" })).not.toHaveProperty("channel_labels");
    expect(locationBody({ ...base, channel_mode: "stereo_split" })).toMatchObject({
      name: "n",
      scan_interval_s: 900,
      channel_labels: ["Host", "Guest"],
    });
  });

  it("requests a scan, and asks before disabling", async () => {
    const mock = renderApp("/leaders/eu-1/locations", { fleet: [leader({ role: "admin" })] })
      .on("GET /api/leaders/eu-1/locations", reply(200, [LOCATION]))
      .on(
        "POST /api/leaders/eu-1/locations/intake/ingest",
        reply(202, { name: "intake", requested_at: "2026-10-04T12:00:00Z" }),
      )
      .on("POST /api/leaders/eu-1/locations/intake/disable", reply(200, { ...LOCATION, enabled: false }));
    expect(await screen.findByText("the root folder is not readable")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Scan now intake" }));
    expect(await screen.findByText("A scan of intake is requested.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Disable intake" }));
    await userEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Disable location" }),
    );
    await waitFor(() =>
      expect(mock.callsTo("POST /api/leaders/eu-1/locations/intake/disable")).toHaveLength(1),
    );
  });

  it("adds a location from the form", async () => {
    const mock = renderApp("/leaders/eu-1/locations", { fleet: [leader({ role: "admin" })] })
      .on("GET /api/leaders/eu-1/locations", reply(200, [LOCATION]))
      .on("POST /api/leaders/eu-1/locations", reply(201, { ...LOCATION, id: "l2", name: "calls" }));
    await userEvent.click(await screen.findByRole("button", { name: "Add location" }));
    const dialog = screen.getByRole("dialog", { name: "Add a location to eu-1" });
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Name" }), "calls");
    await userEvent.type(
      within(dialog).getByRole("textbox", { name: "Folder on the leader (absolute path)" }),
      "/srv/calls",
    );
    await userEvent.click(within(dialog).getByRole("button", { name: "Add location" }));
    await waitFor(() =>
      expect(mock.callsTo("POST /api/leaders/eu-1/locations")[0]?.body).toEqual({
        name: "calls",
        root: "/srv/calls",
        input_prefix: "",
        output_prefix: "transcripts/",
        pool: "default",
        required_device: "any",
        scan_interval_s: 900,
        channel_mode: "mono",
      }),
    );
    expect(await screen.findByText("Location calls is added.")).toBeInTheDocument();
  });
});
```

Run: `npx vitest run src/pages/leader/LocationsTab.test.tsx`
Expected: FAIL — `Failed to resolve import "./LocationsTab"`.

- [ ] **Step 2: Write the locations tab**

`packages/console-web/src/pages/leader/LocationsTab.tsx`:

```tsx
import { useState, type FormEvent } from "react";
import { api, leaderPath } from "../../api/client";
import type { ChannelMode, LocationIn, LocationOut, RequiredDevice } from "../../api/types";
import { useAction } from "../../app/useAction";
import { ActionButton } from "../../components/ActionButton";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { Dialog } from "../../components/Dialog";
import { ErrorPanel } from "../../components/ErrorPanel";
import { formatDuration, formatTime } from "../../lib/format";
import { ActionNotice, ReadState, RefreshButton, useLeaderRead, type TabProps } from "./common";

/** The leader's NAME_PATTERN (admin_models.py), for the browser's own check. */
const NAME_PATTERN = "[A-Za-z0-9][A-Za-z0-9._\\-]{0,99}";

interface LocationForm {
  name: string;
  root: string;
  input_prefix: string;
  output_prefix: string;
  pool: string;
  required_device: RequiredDevice;
  scan_interval_s: string;
  channel_mode: ChannelMode;
  left: string;
  right: string;
}

const EMPTY: LocationForm = {
  name: "",
  root: "",
  input_prefix: "",
  output_prefix: "transcripts/",
  pool: "default",
  required_device: "any",
  scan_interval_s: "900",
  channel_mode: "mono",
  left: "Left",
  right: "Right",
};

/** The leader's LocationIn from the form; channel labels only with a split mode. */
export function locationBody(form: LocationForm): LocationIn {
  const body: LocationIn = {
    name: form.name.trim(),
    root: form.root.trim(),
    input_prefix: form.input_prefix.trim(),
    output_prefix: form.output_prefix.trim(),
    pool: form.pool.trim(),
    required_device: form.required_device,
    scan_interval_s: Number(form.scan_interval_s),
    channel_mode: form.channel_mode,
  };
  if (form.channel_mode !== "mono") body.channel_labels = [form.left.trim(), form.right.trim()];
  return body;
}

function AddLocationDialog({
  leaderName,
  onClose,
  onDone,
}: {
  leaderName: string;
  onClose: () => void;
  onDone: (name: string) => void;
}) {
  const [form, setForm] = useState<LocationForm>(EMPTY);
  const action = useAction();
  const set = (patch: Partial<LocationForm>) => setForm((prev) => ({ ...prev, ...patch }));
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const body = locationBody(form);
    if (await action.run(() => api.post(leaderPath(leaderName, "locations"), body))) {
      onDone(body.name);
      onClose();
    }
  };
  return (
    <Dialog title={`Add a location to ${leaderName}`} onClose={onClose}>
      <form className="form-grid" onSubmit={(event) => void submit(event)}>
        <label className="field">
          Name
          <input
            required
            pattern={NAME_PATTERN}
            value={form.name}
            onChange={(e) => set({ name: e.target.value })}
          />
        </label>
        <label className="field">
          Folder on the leader (absolute path)
          <input required value={form.root} onChange={(e) => set({ root: e.target.value })} />
        </label>
        <label className="field">
          Input prefix (a relative folder ending in /, or empty)
          <input value={form.input_prefix} onChange={(e) => set({ input_prefix: e.target.value })} />
        </label>
        <label className="field">
          Output prefix
          <input value={form.output_prefix} onChange={(e) => set({ output_prefix: e.target.value })} />
        </label>
        <label className="field">
          Pool
          <input
            required
            pattern={NAME_PATTERN}
            value={form.pool}
            onChange={(e) => set({ pool: e.target.value })}
          />
        </label>
        <label className="field">
          Required device
          <select
            value={form.required_device}
            onChange={(e) => set({ required_device: e.target.value as RequiredDevice })}
          >
            <option value="any">Any</option>
            <option value="cuda">CUDA GPU</option>
            <option value="cpu">CPU</option>
          </select>
        </label>
        <label className="field">
          Scan interval in seconds (30 to 604800)
          <input
            type="number"
            required
            min={30}
            max={604800}
            step={1}
            value={form.scan_interval_s}
            onChange={(e) => set({ scan_interval_s: e.target.value })}
          />
        </label>
        <label className="field">
          Channels
          <select
            value={form.channel_mode}
            onChange={(e) => set({ channel_mode: e.target.value as ChannelMode })}
          >
            <option value="mono">Mono</option>
            <option value="stereo_split">Stereo, one speaker per channel</option>
            <option value="auto">Automatic</option>
          </select>
        </label>
        {form.channel_mode !== "mono" && (
          <>
            <label className="field">
              Left channel label
              <input required value={form.left} onChange={(e) => set({ left: e.target.value })} />
            </label>
            <label className="field">
              Right channel label
              <input required value={form.right} onChange={(e) => set({ right: e.target.value })} />
            </label>
          </>
        )}
        {action.error !== null && <ErrorPanel error={action.error} />}
        <div className="dialog-buttons">
          <button type="submit" className="button button-primary" disabled={action.busy}>
            Add location
          </button>
          <button type="button" className="button" onClick={onClose}>
            Cancel
          </button>
        </div>
      </form>
    </Dialog>
  );
}

function channels(location: LocationOut): string {
  if (location.channel_mode === "mono") return "mono";
  return `${location.channel_mode} (${location.channel_labels.join(", ")})`;
}

export function LocationsTab({ leader }: TabProps) {
  const read = useLeaderRead<LocationOut[]>(leader.name, "locations");
  const action = useAction();
  const [adding, setAdding] = useState(false);
  const [disabling, setDisabling] = useState<LocationOut | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const post = async (location: LocationOut, verb: "enable" | "ingest", done: string) => {
    setNotice(null);
    const path = leaderPath(leader.name, `locations/${encodeURIComponent(location.name)}/${verb}`);
    if (await action.run(() => api.post(path))) {
      setNotice(done);
      read.refresh();
    }
  };

  return (
    <>
      <div className="section-head">
        <ActionButton held={leader.role} action="locations.add" onClick={() => setAdding(true)}>
          Add location
        </ActionButton>
        <RefreshButton read={read} />
      </div>
      <ActionNotice message={notice} />
      {action.error !== null && <ErrorPanel error={action.error} />}
      <ReadState read={read} what="locations">
        {(locations) =>
          locations.length === 0 ? (
            <p>This leader has no locations.</p>
          ) : (
            <div className="table-scroll" role="region" aria-label="Locations" tabIndex={0}>
              <table>
                <thead>
                  <tr>
                    <th scope="col">Location</th>
                    <th scope="col">Folder</th>
                    <th scope="col">Pool</th>
                    <th scope="col">Device</th>
                    <th scope="col">Channels</th>
                    <th scope="col">Scan every</th>
                    <th scope="col">Enabled</th>
                    <th scope="col">Last scan</th>
                    <th scope="col">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {locations.map((location) => (
                    <tr key={location.id}>
                      <th scope="row">{location.name}</th>
                      <td className="mono">
                        {location.root ?? "–"}
                        {location.input_prefix}
                      </td>
                      <td>{location.pool}</td>
                      <td>{location.required_device}</td>
                      <td>{channels(location)}</td>
                      <td>{formatDuration(location.scan_interval_s)}</td>
                      <td>{location.enabled ? "Yes" : "No"}</td>
                      <td>
                        {location.last_scan_at ? formatTime(location.last_scan_at) : "Never"}
                        {location.scan_requested && <span className="cell-note">Scan requested</span>}
                        {location.last_scan_error && (
                          <span className="cell-note error-text">{location.last_scan_error}</span>
                        )}
                      </td>
                      <td className="actions">
                        {location.enabled && (
                          <ActionButton
                            held={leader.role}
                            action="locations.ingest"
                            busy={action.busy}
                            onClick={() =>
                              void post(location, "ingest", `A scan of ${location.name} is requested.`)
                            }
                            name={`Scan now ${location.name}`}
                          >
                            Scan now
                          </ActionButton>
                        )}
                        {location.enabled ? (
                          <ActionButton
                            held={leader.role}
                            action="locations.disable"
                            danger
                            onClick={() => setDisabling(location)}
                            name={`Disable ${location.name}`}
                          >
                            Disable
                          </ActionButton>
                        ) : (
                          <ActionButton
                            held={leader.role}
                            action="locations.enable"
                            busy={action.busy}
                            onClick={() => void post(location, "enable", `${location.name} is enabled.`)}
                            name={`Enable ${location.name}`}
                          >
                            Enable
                          </ActionButton>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </ReadState>
      {adding && (
        <AddLocationDialog
          leaderName={leader.name}
          onClose={() => setAdding(false)}
          onDone={(name) => {
            setNotice(`Location ${name} is added.`);
            read.refresh();
          }}
        />
      )}
      {disabling !== null && (
        <ConfirmDialog
          title={`Disable ${disabling.name}?`}
          message="The leader stops scanning this location for new recordings until it is enabled again. Jobs already made are not affected."
          confirmLabel="Disable location"
          onClose={() => setDisabling(null)}
          onConfirm={async () => {
            await api.post(
              leaderPath(leader.name, `locations/${encodeURIComponent(disabling.name)}/disable`),
            );
            setNotice(`${disabling.name} is disabled.`);
            read.refresh();
          }}
        />
      )}
    </>
  );
}
```

- [ ] **Step 3: Add the tab**

In `packages/console-web/src/pages/leader/tabs.ts`, add `{ id: "locations", label: "Locations" },` as the line after `{ id: "jobs", label: "Jobs" },`.

In `packages/console-web/src/pages/leader/LeaderPage.tsx`, add `import { LocationsTab } from "./LocationsTab";` after the `./JobsTab` import, and after

```tsx
    case "jobs":
      return <JobsTab leader={leader} />;
```

add

```tsx
    case "locations":
      return <LocationsTab leader={leader} />;
```

- [ ] **Step 4: Run everything and commit**

Run: `npm test`
Expected: PASS — 136 tests in 17 files (133 + 3 new in `LocationsTab.test.tsx`).

Run: `npm run typecheck` then `npm run lint`
Expected: both exit 0 with no output.

```bash
git add packages/console-web/src
git commit -m "Console web app: locations tab with add, enable, disable and scan

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Join tokens (plaintext shown once) and the consent report

**Files:**
- Create: `packages/console-web/src/pages/leader/TokensTab.tsx`, `src/pages/leader/ConsentTab.tsx`
- Modify: `packages/console-web/src/pages/leader/tabs.ts` (add `tokens`, `consent`), `src/pages/leader/LeaderPage.tsx` (add their cases)
- Test: `packages/console-web/src/pages/leader/TokensTab.test.tsx`, `src/pages/leader/ConsentTab.test.tsx`

**Interfaces:**
- Consumes: as Task 3, plus `can` from `src/api/roles.ts` and `formatCount`; types `TokenOut`, `TokenIn`, `TokenCreated`, `ConsentReport`.
- Produces: `TokensTab({ leader })`; `TokenCreatedDialog({ created: TokenCreated, onClose })`; `ConsentTab({ leader })`. Calls `GET tokens` (only when the role reaches `tokens.view`), `POST tokens` with `{ pool, expires_in_seconds, max_uses }`, `POST tokens/{id}/revoke`, `GET consent/report`.
- The plaintext's lifetime: the `POST` answer → `TokensTab`'s `created` state → `TokenCreatedDialog`'s props. `onClose` sets `created` to `null`, which unmounts the dialog. It is passed nowhere else.

- [ ] **Step 1: Write the failing tests**

`packages/console-web/src/pages/leader/TokensTab.test.tsx`:

```tsx
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { TokenCreated, TokenOut } from "../../api/types";
import { reply } from "../../test/fetchMock";
import { leader } from "../../test/fixtures";
import { renderApp } from "../../test/renderApp";

const TOKEN: TokenOut = {
  id: "44444444-4444-4444-8444-444444444444",
  pool: "default",
  expires_at: "2099-01-01T00:00:00Z",
  max_uses: 5,
  uses: 1,
  revoked: false,
  created_by: "admin@example.org",
  created_at: "2026-10-03T00:00:00Z",
};

describe("join tokens tab", () => {
  it("tells a person below admin that tokens need admin, without asking the leader", async () => {
    const mock = renderApp("/leaders/eu-1/tokens");
    expect(
      await screen.findByText("Join tokens need the admin role on eu-1. Your role is operator."),
    ).toBeInTheDocument();
    expect(mock.callsTo("GET /api/leaders/eu-1/tokens")).toHaveLength(0);
  });

  it("shows a new token's plaintext once and drops it when the dialog closes", async () => {
    const storageWrites = vi.spyOn(Storage.prototype, "setItem");
    const created: TokenCreated = {
      id: "55555555-5555-4555-8555-555555555555",
      token: "sst_plaintext-secret-value",
      pool: "gpu",
      expires_at: "2026-10-11T12:00:00Z",
      max_uses: 2,
    };
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const mock = renderApp("/leaders/eu-1/tokens", { fleet: [leader({ role: "admin" })] })
      .on("GET /api/leaders/eu-1/tokens", reply(200, [TOKEN]))
      .on("POST /api/leaders/eu-1/tokens", reply(201, created));
    await userEvent.click(await screen.findByRole("button", { name: "Create join token" }));
    const form = screen.getByRole("dialog", { name: "Create a join token for eu-1" });
    const pool = within(form).getByRole("textbox", { name: "Pool" });
    await userEvent.clear(pool);
    await userEvent.type(pool, "gpu");
    const uses = within(form).getByRole("spinbutton", { name: "Uses (1 to 10000)" });
    await userEvent.clear(uses);
    await userEvent.type(uses, "2");
    await userEvent.click(within(form).getByRole("button", { name: "Create token" }));

    const shown = await screen.findByRole("dialog", { name: "Join token created" });
    expect(within(shown).getByRole("textbox", { name: "Join token" })).toHaveValue(
      "sst_plaintext-secret-value",
    );
    expect(mock.callsTo("POST /api/leaders/eu-1/tokens")[0]?.body).toEqual({
      pool: "gpu",
      expires_in_seconds: 7 * 86400,
      max_uses: 2,
    });
    await userEvent.click(within(shown).getByRole("button", { name: "Copy token" }));
    expect(writeText).toHaveBeenCalledWith("sst_plaintext-secret-value");
    expect(within(shown).getByText("Copied to the clipboard.")).toBeInTheDocument();

    await userEvent.click(within(shown).getByRole("button", { name: "Done" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(document.body.innerHTML).not.toContain("sst_plaintext-secret-value");
    expect(window.location.href).not.toContain("sst_plaintext");
    // C3a's lint bans window.localStorage and sessionStorage in src/, tests included, so
    // watch every write through a spy on the prototype instead (set before the flow starts).
    expect(JSON.stringify(storageWrites.mock.calls)).not.toContain("sst_plaintext");
    expect(await screen.findByText("Join token 55555555 is created.")).toBeInTheDocument();
    expect(mock.callsTo("GET /api/leaders/eu-1/tokens").length).toBeGreaterThanOrEqual(2);
  });
});
```

`packages/console-web/src/pages/leader/ConsentTab.test.tsx`:

```tsx
import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ConsentReport } from "../../api/types";
import { reply } from "../../test/fetchMock";
import { renderApp } from "../../test/renderApp";

describe("consent tab", () => {
  it("shows counts by location and transcripts to review", async () => {
    const report: ConsentReport = {
      locations: [{ name: "intake", consented: 10, not_consented: 1, withdrawn: 2, missing: 0 }],
      flagged: [
        {
          job_id: "66666666-6666-4666-8666-666666666666",
          location: "intake",
          key: "incoming/b.wav",
          completed_at: "2026-10-04T10:00:00Z",
          output_location: "intake",
          outputs: ["transcripts/b.json"],
        },
      ],
      truncated: true,
    };
    renderApp("/leaders/eu-1/consent").on("GET /api/leaders/eu-1/consent/report", reply(200, report));
    const byLocation = await screen.findByRole("region", { name: "Consent by location" });
    expect(within(byLocation).getByRole("row", { name: /intake/ })).toHaveTextContent("intake10120");
    expect(screen.getByText("intake: transcripts/b.json")).toBeInTheDocument();
    expect(screen.getByText(/The report is cut short/)).toBeInTheDocument();
  });
});
```

Run: `npx vitest run src/pages/leader/TokensTab.test.tsx src/pages/leader/ConsentTab.test.tsx`
Expected: FAIL — all 3 (no such tabs: "Page not found").

- [ ] **Step 2: Write the join tokens tab**

`packages/console-web/src/pages/leader/TokensTab.tsx`:

```tsx
import { useState, type FormEvent } from "react";
import { api, leaderPath } from "../../api/client";
import { can } from "../../api/roles";
import type { TokenCreated, TokenIn, TokenOut } from "../../api/types";
import { useAction } from "../../app/useAction";
import { ActionButton } from "../../components/ActionButton";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { Dialog } from "../../components/Dialog";
import { ErrorPanel } from "../../components/ErrorPanel";
import { formatTime } from "../../lib/format";
import { ActionNotice, ReadState, RefreshButton, shortId, useLeaderRead, type TabProps } from "./common";

const DAY_S = 86_400;
/** The leader's NAME_PATTERN (admin_models.py), for the browser's own check. */
const NAME_PATTERN = "[A-Za-z0-9][A-Za-z0-9._\\-]{0,99}";

function CreateTokenDialog({
  leaderName,
  onClose,
  onCreated,
}: {
  leaderName: string;
  onClose: () => void;
  onCreated: (created: TokenCreated) => void;
}) {
  const [pool, setPool] = useState("default");
  const [days, setDays] = useState("7");
  const [uses, setUses] = useState("1");
  const action = useAction();
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const body: TokenIn = {
      pool: pool.trim(),
      expires_in_seconds: Number(days) * DAY_S,
      max_uses: Number(uses),
    };
    // Held only until it is handed to the one-time dialog.
    const answer: { created: TokenCreated | null } = { created: null };
    const ok = await action.run(async () => {
      answer.created = await api.post<TokenCreated>(leaderPath(leaderName, "tokens"), body);
    });
    if (ok && answer.created !== null) onCreated(answer.created);
  };
  return (
    <Dialog title={`Create a join token for ${leaderName}`} onClose={onClose}>
      <form className="form-grid" onSubmit={(event) => void submit(event)}>
        <label className="field">
          Pool
          <input required pattern={NAME_PATTERN} value={pool} onChange={(e) => setPool(e.target.value)} />
        </label>
        <label className="field">
          Expires after (days, 1 to 90)
          <input
            type="number"
            required
            min={1}
            max={90}
            step={1}
            value={days}
            onChange={(e) => setDays(e.target.value)}
          />
        </label>
        <label className="field">
          Uses (1 to 10000)
          <input
            type="number"
            required
            min={1}
            max={10000}
            step={1}
            value={uses}
            onChange={(e) => setUses(e.target.value)}
          />
        </label>
        {action.error !== null && <ErrorPanel error={action.error} />}
        <div className="dialog-buttons">
          <button type="submit" className="button button-primary" disabled={action.busy}>
            Create token
          </button>
          <button type="button" className="button" onClick={onClose}>
            Cancel
          </button>
        </div>
      </form>
    </Dialog>
  );
}

/**
 * The join token's plaintext, shown once. It lives only in this dialog's props: the parent
 * drops it when the dialog closes, and nothing writes it to the URL, storage or a log.
 */
export function TokenCreatedDialog({ created, onClose }: { created: TokenCreated; onClose: () => void }) {
  const [copied, setCopied] = useState<"no" | "yes" | "failed">("no");
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(created.token);
      setCopied("yes");
    } catch {
      setCopied("failed");
    }
  };
  const copiedText =
    copied === "yes"
      ? "Copied to the clipboard."
      : copied === "failed"
        ? "Copying failed: select the token and copy it."
        : "";
  return (
    <Dialog title="Join token created" onClose={onClose}>
      <p>
        <strong>This is the only time the token is shown.</strong> Copy it now and give it to whoever starts
        the follower. Pool {created.pool}; up to {created.max_uses} {created.max_uses === 1 ? "use" : "uses"};
        expires {formatTime(created.expires_at)}.
      </p>
      <label className="field">
        Join token
        <input className="mono" readOnly value={created.token} onFocus={(event) => event.target.select()} />
      </label>
      <p className="action-notice" role="status">
        {copiedText}
      </p>
      <div className="dialog-buttons">
        <button type="button" className="button button-primary" onClick={() => void copy()}>
          Copy token
        </button>
        <button type="button" className="button" onClick={onClose}>
          Done
        </button>
      </div>
    </Dialog>
  );
}

function tokenState(token: TokenOut, now: number): string {
  if (token.revoked) return "Revoked";
  if (Date.parse(token.expires_at) <= now) return "Expired";
  if (token.uses >= token.max_uses) return "Used up";
  return "Usable";
}

function TokenList({ leader }: TabProps) {
  const read = useLeaderRead<TokenOut[]>(leader.name, "tokens");
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<TokenCreated | null>(null);
  const [revoking, setRevoking] = useState<TokenOut | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  return (
    <>
      <div className="section-head">
        <ActionButton held={leader.role} action="tokens.create" onClick={() => setCreating(true)}>
          Create join token
        </ActionButton>
        <RefreshButton read={read} />
      </div>
      <ActionNotice message={notice} />
      <ReadState read={read} what="join tokens">
        {(tokens) => {
          // "Now" is when the list was read: render stays pure, and the state column
          // describes the list as the leader returned it.
          const now = read.updatedAt ?? 0;
          return tokens.length === 0 ? (
            <p>No join tokens.</p>
          ) : (
            <div className="table-scroll" role="region" aria-label="Join tokens" tabIndex={0}>
              <table>
                <thead>
                  <tr>
                    <th scope="col">Token</th>
                    <th scope="col">Pool</th>
                    <th scope="col">State</th>
                    <th scope="col">Uses</th>
                    <th scope="col">Expires</th>
                    <th scope="col">Created by</th>
                    <th scope="col">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {tokens.map((token) => (
                    <tr key={token.id}>
                      <th scope="row">
                        <code>{shortId(token.id)}</code>
                      </th>
                      <td>{token.pool}</td>
                      <td>{tokenState(token, now)}</td>
                      <td className="num">
                        {token.uses} of {token.max_uses}
                      </td>
                      <td>{formatTime(token.expires_at)}</td>
                      <td>{token.created_by}</td>
                      <td className="actions">
                        {!token.revoked && (
                          <ActionButton
                            held={leader.role}
                            action="tokens.revoke"
                            danger
                            onClick={() => setRevoking(token)}
                            name={`Revoke token ${shortId(token.id)}`}
                          >
                            Revoke
                          </ActionButton>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        }}
      </ReadState>
      {creating && (
        <CreateTokenDialog
          leaderName={leader.name}
          onClose={() => setCreating(false)}
          onCreated={(token) => {
            setCreating(false);
            setCreated(token);
          }}
        />
      )}
      {created !== null && (
        <TokenCreatedDialog
          created={created}
          onClose={() => {
            setNotice(`Join token ${shortId(created.id)} is created.`);
            setCreated(null);
            read.refresh();
          }}
        />
      )}
      {revoking !== null && (
        <ConfirmDialog
          title={`Revoke join token ${shortId(revoking.id)}?`}
          message="No new follower can join with it. Followers that already joined are not affected."
          confirmLabel="Revoke token"
          onClose={() => setRevoking(null)}
          onConfirm={async () => {
            await api.post(leaderPath(leader.name, `tokens/${revoking.id}/revoke`));
            setNotice(`Join token ${shortId(revoking.id)} is revoked.`);
            read.refresh();
          }}
        />
      )}
    </>
  );
}

export function TokensTab({ leader }: TabProps) {
  // Listing join tokens needs admin on the leader; below it the console would refuse, so
  // the tab says so instead of asking.
  if (!can(leader.role, "tokens.view")) {
    return (
      <p>
        Join tokens need the admin role on {leader.name}. Your role is {leader.role}.
      </p>
    );
  }
  return <TokenList leader={leader} />;
}
```

- [ ] **Step 3: Write the consent report tab**

`packages/console-web/src/pages/leader/ConsentTab.tsx`:

```tsx
import type { ConsentReport } from "../../api/types";
import { formatCount, formatTime } from "../../lib/format";
import { ReadState, RefreshButton, shortId, useLeaderRead, type TabProps } from "./common";

export function ConsentTab({ leader }: TabProps) {
  const read = useLeaderRead<ConsentReport>(leader.name, "consent/report");
  return (
    <>
      <div className="section-head">
        <p className="muted">
          Recordings by consent state, and transcripts made from recordings that are no longer consented.
        </p>
        <RefreshButton read={read} />
      </div>
      <ReadState read={read} what="the consent report">
        {(report) => (
          <>
            <h3>By location</h3>
            <div className="table-scroll" role="region" aria-label="Consent by location" tabIndex={0}>
              <table>
                <thead>
                  <tr>
                    <th scope="col">Location</th>
                    <th scope="col">Consented</th>
                    <th scope="col">Not consented</th>
                    <th scope="col">Withdrawn</th>
                    <th scope="col">Missing</th>
                  </tr>
                </thead>
                <tbody>
                  {report.locations.map((row) => (
                    <tr key={row.name}>
                      <th scope="row">{row.name}</th>
                      <td className="num">{formatCount(row.consented)}</td>
                      <td className="num">{formatCount(row.not_consented)}</td>
                      <td className="num">{formatCount(row.withdrawn)}</td>
                      <td className="num">{formatCount(row.missing)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <h3>Transcripts to review</h3>
            {report.flagged.length === 0 ? (
              <p>No transcript was made from a recording that is no longer consented.</p>
            ) : (
              <div className="table-scroll" role="region" aria-label="Transcripts to review" tabIndex={0}>
                <table>
                  <thead>
                    <tr>
                      <th scope="col">Job</th>
                      <th scope="col">Recording</th>
                      <th scope="col">Completed</th>
                      <th scope="col">Outputs</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.flagged.map((row) => (
                      <tr key={row.job_id}>
                        <th scope="row">
                          <code>{shortId(row.job_id)}</code>
                        </th>
                        <td>
                          {row.location}: <span className="mono">{row.key}</span>
                        </td>
                        <td>{row.completed_at ? formatTime(row.completed_at) : "–"}</td>
                        <td>
                          <ul className="cell-list">
                            {row.outputs.map((output) => (
                              <li key={output} className="mono">
                                {row.output_location}: {output}
                              </li>
                            ))}
                          </ul>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {report.truncated && (
              <p className="notice">The report is cut short; the leader holds more flagged transcripts.</p>
            )}
          </>
        )}
      </ReadState>
    </>
  );
}
```

- [ ] **Step 4: Add the tabs**

In `packages/console-web/src/pages/leader/tabs.ts`, add after `{ id: "locations", label: "Locations" },`:

```ts
  { id: "tokens", label: "Join tokens" },
  { id: "consent", label: "Consent report" },
```

In `packages/console-web/src/pages/leader/LeaderPage.tsx`, add `import { ConsentTab } from "./ConsentTab";` before the `./JobsTab` import and `import { TokensTab } from "./TokensTab";` after the `./tabs` import, and after

```tsx
    case "locations":
      return <LocationsTab leader={leader} />;
```

add

```tsx
    case "tokens":
      return <TokensTab leader={leader} />;
    case "consent":
      return <ConsentTab leader={leader} />;
```

- [ ] **Step 5: Run everything and commit**

Run: `npm test`
Expected: PASS — 139 tests in 19 files (136 + 3 new in `TokensTab.test.tsx` and `ConsentTab.test.tsx`).

Run: `npm run typecheck` then `npm run lint`
Expected: both exit 0 with no output.

```bash
git add packages/console-web/src
git commit -m "Console web app: join tokens with one-time plaintext dialog, consent report

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Administration — leaders, grants, console administrators

**Files:**
- Create: `packages/console-web/src/pages/admin/AdminFrame.tsx`, `src/pages/admin/PrincipalFields.tsx`, `src/pages/admin/AdminLeadersPage.tsx`, `src/pages/admin/AdminGrantsPage.tsx`, `src/pages/admin/AdminAdminsPage.tsx`
- Modify: `packages/console-web/src/App.tsx` (admin routes and navigation item)
- Test: `packages/console-web/src/pages/admin/admin.test.tsx`

**Interfaces:**
- Consumes: Task 1's `ConfirmDialog`, `Dialog`, `useAction`; Task 2's `ActionNotice`, `ReadState`, `renderApp`; C3a's `api`, `ApiError`, `usePoll`/`PollState`, `useSession`, `Link`, `useLocation`, `usePageTitle`, `ErrorPanel`, `formatTime`; types `LeaderOut`, `LeaderIn`, `LeaderEdit`, `CredentialIn`, `GrantOut`, `GrantIn`, `ConsoleAdminOut`, `ConsoleAdminIn`, `PrincipalKind`, `Role`.
- Produces:
  - `src/pages/admin/AdminFrame.tsx`: `ADMIN_PAGES`; `useAdminList<T>(path: string): PollState<T[]>`; `AdminFrame({ title, children })` (renders `children` only for a console administrator); `parseLabels(text: string): Record<string, string> | null`; `labelsText(labels): string`.
  - `src/pages/admin/PrincipalFields.tsx`: `PRINCIPAL_KINDS`; `PrincipalFields({ kind, principal, onKind, onPrincipal })`.
  - `src/pages/admin/AdminLeadersPage.tsx`: `AdminLeadersPage()`; `editBody(leader: LeaderOut, form): LeaderEdit`.
  - `src/pages/admin/AdminGrantsPage.tsx`: `AdminGrantsPage()`. `src/pages/admin/AdminAdminsPage.tsx`: `AdminAdminsPage()`.
  - Routes `/admin` (redirects to `/admin/leaders`), `/admin/leaders`, `/admin/grants`, `/admin/admins`; the "Administration" navigation item for console administrators.
- Calls: `GET|POST /api/admin/leaders`, `PATCH|DELETE /api/admin/leaders/{name}`, `PUT /api/admin/leaders/{name}/credential`, `GET|POST /api/admin/grants`, `DELETE /api/admin/grants/{id}`, `GET|POST /api/admin/console-admins`, `DELETE /api/admin/console-admins/{id}`.

- [ ] **Step 1: Write the failing test**

`packages/console-web/src/pages/admin/admin.test.tsx`:

```tsx
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { ConsoleAdminOut, GrantOut, LeaderOut } from "../../api/types";
import { fail, reply } from "../../test/fetchMock";
import { SESSION } from "../../test/fixtures";
import { renderApp } from "../../test/renderApp";
import { parseLabels } from "./AdminFrame";
import { editBody } from "./AdminLeadersPage";

const ADMIN_SESSION = { ...SESSION, console_admin: true };
const CREDENTIAL = "c".repeat(20) + "_-" + "D".repeat(21);
const LEADER: LeaderOut = {
  name: "eu-1",
  base_url: "https://eu-1.leaders.example",
  labels: { region: "eu" },
  enabled: true,
  added_by: "admin@example.org",
  created_at: "2026-10-01T00:00:00Z",
  credential_updated_at: "2026-10-01T00:00:00Z",
  credential_updated_by: "admin@example.org",
  credential_revoked: false,
  credential_revoked_at: null,
};
const GRANT: GrantOut = {
  id: "77777777-7777-4777-8777-777777777777",
  role: "operator",
  scope: "label:region=eu",
  principal_kind: "domain",
  principal: "example.org",
  created_by: "admin@example.org",
  created_at: "2026-10-02T00:00:00Z",
};
const ADMIN: ConsoleAdminOut = {
  id: "88888888-8888-4888-8888-888888888888",
  principal_kind: "entra_group",
  principal: "a1a1a1a1-0000-4000-8000-0000000000c0",
  created_by: "swarmscribe-console cli",
  created_at: "2026-10-01T00:00:00Z",
};

describe("administration helpers", () => {
  it("parses labels one key=value per line", () => {
    expect(parseLabels("region=eu\n env = prod \n\n")).toEqual({ region: "eu", env: "prod" });
    expect(parseLabels("")).toEqual({});
    expect(parseLabels("region")).toBeNull();
    expect(parseLabels("=eu")).toBeNull();
    expect(parseLabels("region=")).toBeNull();
  });

  it("sends only what changed, and the credential only with a new address", () => {
    const same = { baseUrl: LEADER.base_url, labels: { region: "eu" }, enabled: true, credential: "" };
    expect(editBody(LEADER, same)).toEqual({});
    expect(editBody(LEADER, { ...same, enabled: false })).toEqual({ enabled: false });
    expect(editBody(LEADER, { ...same, labels: { region: "us" } })).toEqual({ labels: { region: "us" } });
    expect(
      editBody(LEADER, { ...same, baseUrl: "https://eu-2.leaders.example", credential: CREDENTIAL }),
    ).toEqual({
      base_url: "https://eu-2.leaders.example",
      credential: CREDENTIAL,
    });
  });
});

describe("administration pages", () => {
  it("refuses a person who is not a console administrator, without asking", async () => {
    const mock = renderApp("/admin/grants");
    expect(
      await screen.findByText(/Console administration needs a console administrator/),
    ).toBeInTheDocument();
    expect(mock.callsTo("GET /api/admin/grants")).toHaveLength(0);
    expect(screen.queryByRole("link", { name: "Administration" })).not.toBeInTheDocument();
  });

  it("shows Administration in the main navigation for a console administrator", async () => {
    renderApp("/", { session: ADMIN_SESSION, fleet: [] });
    expect(await screen.findByRole("link", { name: "Administration" })).toHaveAttribute(
      "href",
      "/admin/leaders",
    );
  });

  it("adds a leader with a sealed credential field and parsed labels", async () => {
    const mock = renderApp("/admin/leaders", { session: ADMIN_SESSION })
      .on("GET /api/admin/leaders", reply(200, [LEADER]))
      .on("POST /api/admin/leaders", reply(201, { ...LEADER, name: "us-1" }));
    await userEvent.click(await screen.findByRole("button", { name: "Add leader" }));
    const dialog = screen.getByRole("dialog", { name: "Add a leader" });
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Name" }), "us-1");
    const url = within(dialog).getByRole("textbox", { name: "Address (https://)" });
    await userEvent.clear(url);
    await userEvent.type(url, "https://us-1.leaders.example");
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Labels" }), "region=us");
    const credential = within(dialog).getByLabelText("Console credential");
    expect(credential).toHaveAttribute("type", "password");
    expect(credential).toHaveAttribute("autocomplete", "off");
    await userEvent.type(credential, CREDENTIAL);
    await userEvent.click(within(dialog).getByRole("button", { name: "Add leader" }));
    await waitFor(() =>
      expect(mock.callsTo("POST /api/admin/leaders")[0]?.body).toEqual({
        name: "us-1",
        base_url: "https://us-1.leaders.example",
        labels: { region: "us" },
        credential: CREDENTIAL,
        enabled: true,
      }),
    );
    expect(await screen.findByText("Leader us-1 is added.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(document.body.innerHTML).not.toContain(CREDENTIAL);
  });

  it("refuses malformed labels before sending anything", async () => {
    const mock = renderApp("/admin/leaders", { session: ADMIN_SESSION }).on(
      "GET /api/admin/leaders",
      reply(200, [LEADER]),
    );
    await userEvent.click(await screen.findByRole("button", { name: "Edit eu-1" }));
    const dialog = screen.getByRole("dialog", { name: "Edit eu-1" });
    const labels = within(dialog).getByRole("textbox", { name: "Labels" });
    await userEvent.clear(labels);
    await userEvent.type(labels, "no equals sign");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Those labels are not valid.");
    expect(mock.calls.filter((call) => call.method === "PATCH")).toHaveLength(0);
  });

  it("asks for the credential when the address changes", async () => {
    renderApp("/admin/leaders", { session: ADMIN_SESSION }).on(
      "GET /api/admin/leaders",
      reply(200, [LEADER]),
    );
    await userEvent.click(await screen.findByRole("button", { name: "Edit eu-1" }));
    const dialog = screen.getByRole("dialog", { name: "Edit eu-1" });
    expect(
      within(dialog).queryByLabelText("Console credential for the new address"),
    ).not.toBeInTheDocument();
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Address (https://)" }), "x");
    expect(within(dialog).getByLabelText("Console credential for the new address")).toBeRequired();
  });

  it("adds and removes a grant", async () => {
    const mock = renderApp("/admin/grants", { session: ADMIN_SESSION })
      .on("GET /api/admin/grants", reply(200, [GRANT]))
      .on("POST /api/admin/grants", reply(201, { ...GRANT, id: "g2", role: "viewer", scope: "all" }))
      .on(`DELETE /api/admin/grants/${GRANT.id}`, reply(204));
    const form = await screen.findByRole("form", { name: "Add a grant" });
    await userEvent.selectOptions(within(form).getByRole("combobox", { name: "Principal kind" }), "domain");
    await userEvent.type(within(form).getByRole("textbox", { name: "Principal" }), "example.org");
    await userEvent.click(within(form).getByRole("button", { name: "Add grant" }));
    await waitFor(() =>
      expect(mock.callsTo("POST /api/admin/grants")[0]?.body).toEqual({
        role: "viewer",
        scope: "all",
        principal_kind: "domain",
        principal: "example.org",
      }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Remove grant for example.org" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Remove grant" }));
    await waitFor(() => expect(mock.callsTo(`DELETE /api/admin/grants/${GRANT.id}`)).toHaveLength(1));
  });

  it("shows the last-administrator refusal", async () => {
    renderApp("/admin/admins", { session: ADMIN_SESSION })
      .on("GET /api/admin/console-admins", reply(200, [ADMIN]))
      .on(
        `DELETE /api/admin/console-admins/${ADMIN.id}`,
        fail(409, "last_admin", "the last console administrator cannot be removed"),
      );
    await userEvent.click(await screen.findByRole("button", { name: `Remove ${ADMIN.principal}` }));
    await userEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Remove administrator" }),
    );
    expect(await within(screen.getByRole("dialog")).findByRole("alert")).toHaveTextContent(
      "The last console administrator cannot be removed.",
    );
  });
});
```

Run: `npx vitest run src/pages/admin`
Expected: FAIL — `Failed to resolve import "./AdminFrame"`.

- [ ] **Step 2: Write the frame and the principal fields**

`packages/console-web/src/pages/admin/AdminFrame.tsx`:

```tsx
import { useCallback, type ReactNode } from "react";
import { api } from "../../api/client";
import { Link, useLocation } from "../../app/router";
import { useSession } from "../../app/session";
import { usePageTitle } from "../../app/usePageTitle";
import { usePoll, type PollState } from "../../app/usePoll";

export const ADMIN_PAGES = [
  { path: "/admin/leaders", label: "Leaders" },
  { path: "/admin/grants", label: "Grants" },
  { path: "/admin/admins", label: "Console administrators" },
] as const;

/** A console administration list: loaded on open and after each change. */
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
```

`packages/console-web/src/pages/admin/PrincipalFields.tsx`:

```tsx
import { useId } from "react";
import type { PrincipalKind } from "../../api/types";

export const PRINCIPAL_KINDS: { value: PrincipalKind; label: string; hint: string }[] = [
  { value: "entra_group", label: "Entra ID group", hint: "the group's object ID (a GUID)" },
  { value: "google_group", label: "Google group", hint: "the group's email address" },
  { value: "email", label: "Email address", hint: "a Google account's address" },
  { value: "domain", label: "Domain", hint: "a Google Workspace domain, such as example.org" },
];

/** Who a grant or a console administrator entry names: a kind and the principal itself. */
export function PrincipalFields({
  kind,
  principal,
  onKind,
  onPrincipal,
}: {
  kind: PrincipalKind;
  principal: string;
  onKind: (kind: PrincipalKind) => void;
  onPrincipal: (principal: string) => void;
}) {
  const hintId = useId();
  const hint = PRINCIPAL_KINDS.find((k) => k.value === kind)?.hint ?? "";
  return (
    <>
      <label className="field">
        Principal kind
        <select value={kind} onChange={(e) => onKind(e.target.value as PrincipalKind)}>
          {PRINCIPAL_KINDS.map((k) => (
            <option key={k.value} value={k.value}>
              {k.label}
            </option>
          ))}
        </select>
      </label>
      <div className="field">
        <label className="field">
          Principal
          <input
            required
            value={principal}
            onChange={(e) => onPrincipal(e.target.value)}
            aria-describedby={hintId}
          />
        </label>
        <span id={hintId} className="field-help">
          {hint}
        </span>
      </div>
    </>
  );
}
```

Help text sits outside its `<label>` (and is tied to the input with `aria-describedby`) so that the field's accessible name is the label alone.

- [ ] **Step 3: Write the leaders page**

`packages/console-web/src/pages/admin/AdminLeadersPage.tsx`:

```tsx
import { useId, useState, type FormEvent } from "react";
import { ApiError, api } from "../../api/client";
import type { CredentialIn, LeaderEdit, LeaderIn, LeaderOut } from "../../api/types";
import { useAction } from "../../app/useAction";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { Dialog } from "../../components/Dialog";
import { ErrorPanel } from "../../components/ErrorPanel";
import { formatTime } from "../../lib/format";
import { ActionNotice, ReadState } from "../leader/common";
import { AdminFrame, labelsText, parseLabels, useAdminList } from "./AdminFrame";

const LABELS_HELP = "One key=value per line, such as region=eu.";
const BAD_LABELS = new ApiError(422, "invalid_labels", LABELS_HELP);
const leaderApi = (name: string) => `/api/admin/leaders/${encodeURIComponent(name)}`;

function CredentialField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
}) {
  const helpId = useId();
  return (
    <div className="field">
      <label className="field">
        {label}
        <input
          type="password"
          autoComplete="off"
          spellCheck={false}
          required
          value={value}
          onChange={(e) => onChange(e.target.value)}
          aria-describedby={helpId}
        />
      </label>
      <span id={helpId} className="field-help">
        The 43-character value that `swarmscribe-admin console create` printed. It is never shown again.
      </span>
    </div>
  );
}

function LabelsField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const helpId = useId();
  return (
    <div className="field">
      <label className="field">
        Labels
        <textarea rows={3} value={value} onChange={(e) => onChange(e.target.value)} aria-describedby={helpId} />
      </label>
      <span id={helpId} className="field-help">
        {LABELS_HELP}
      </span>
    </div>
  );
}

function AddLeaderDialog({ onClose, onDone }: { onClose: () => void; onDone: (name: string) => void }) {
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("https://");
  const [labels, setLabels] = useState("");
  const [credential, setCredential] = useState("");
  const [enabled, setEnabled] = useState(true);
  const action = useAction();
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const parsed = parseLabels(labels);
    const ok = await action.run(async () => {
      if (parsed === null) throw BAD_LABELS;
      const body: LeaderIn = {
        name: name.trim(),
        base_url: baseUrl.trim(),
        labels: parsed,
        credential,
        enabled,
      };
      await api.post<LeaderOut>("/api/admin/leaders", body);
    });
    if (ok) {
      onDone(name.trim());
      onClose();
    }
  };
  return (
    <Dialog title="Add a leader" onClose={onClose}>
      <form className="form-grid" onSubmit={(event) => void submit(event)}>
        <label className="field">
          Name
          <input required value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="field">
          Address (https://)
          <input type="url" required value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
        </label>
        <LabelsField value={labels} onChange={setLabels} />
        <CredentialField label="Console credential" value={credential} onChange={setCredential} />
        <label className="field-check">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
          Enabled
        </label>
        {action.error !== null && <ErrorPanel error={action.error} />}
        <div className="dialog-buttons">
          <button type="submit" className="button button-primary" disabled={action.busy}>
            Add leader
          </button>
          <button type="button" className="button" onClick={onClose}>
            Cancel
          </button>
        </div>
      </form>
    </Dialog>
  );
}

/** Only changed fields are sent; a new address needs the credential for it (C2a rule). */
export function editBody(
  leader: LeaderOut,
  form: { baseUrl: string; labels: Record<string, string>; enabled: boolean; credential: string },
): LeaderEdit {
  const body: LeaderEdit = {};
  if (form.baseUrl.trim() !== leader.base_url) {
    body.base_url = form.baseUrl.trim();
    body.credential = form.credential;
  }
  if (labelsText(form.labels) !== labelsText(leader.labels)) body.labels = form.labels;
  if (form.enabled !== leader.enabled) body.enabled = form.enabled;
  return body;
}

function EditLeaderDialog({
  leader,
  onClose,
  onDone,
}: {
  leader: LeaderOut;
  onClose: () => void;
  onDone: () => void;
}) {
  const [baseUrl, setBaseUrl] = useState(leader.base_url);
  const [labels, setLabels] = useState(labelsText(leader.labels));
  const [enabled, setEnabled] = useState(leader.enabled);
  const [credential, setCredential] = useState("");
  const action = useAction();
  const urlChanged = baseUrl.trim() !== leader.base_url;
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const parsed = parseLabels(labels);
    const ok = await action.run(async () => {
      if (parsed === null) throw BAD_LABELS;
      const body = editBody(leader, { baseUrl, labels: parsed, enabled, credential });
      if (Object.keys(body).length > 0) await api.patch<LeaderOut>(leaderApi(leader.name), body);
    });
    if (ok) {
      onDone();
      onClose();
    }
  };
  return (
    <Dialog title={`Edit ${leader.name}`} onClose={onClose}>
      <form className="form-grid" onSubmit={(event) => void submit(event)}>
        <label className="field">
          Address (https://)
          <input type="url" required value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
        </label>
        {urlChanged && (
          <CredentialField
            label="Console credential for the new address"
            value={credential}
            onChange={setCredential}
          />
        )}
        <LabelsField value={labels} onChange={setLabels} />
        <label className="field-check">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
          Enabled
        </label>
        {action.error !== null && <ErrorPanel error={action.error} />}
        <div className="dialog-buttons">
          <button type="submit" className="button button-primary" disabled={action.busy}>
            Save
          </button>
          <button type="button" className="button" onClick={onClose}>
            Cancel
          </button>
        </div>
      </form>
    </Dialog>
  );
}

function RotateDialog({
  leader,
  onClose,
  onDone,
}: {
  leader: LeaderOut;
  onClose: () => void;
  onDone: () => void;
}) {
  const [credential, setCredential] = useState("");
  const action = useAction();
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const body: CredentialIn = { credential };
    if (await action.run(() => api.put<LeaderOut>(`${leaderApi(leader.name)}/credential`, body))) {
      onDone();
      onClose();
    }
  };
  return (
    <Dialog title={`Rotate the credential for ${leader.name}`} onClose={onClose}>
      <form className="form-grid" onSubmit={(event) => void submit(event)}>
        <p>
          Create a new console credential on the leader first, replace it here, then revoke the old one on
          the leader.
        </p>
        <CredentialField label="New console credential" value={credential} onChange={setCredential} />
        {action.error !== null && <ErrorPanel error={action.error} />}
        <div className="dialog-buttons">
          <button type="submit" className="button button-primary" disabled={action.busy}>
            Replace credential
          </button>
          <button type="button" className="button" onClick={onClose}>
            Cancel
          </button>
        </div>
      </form>
    </Dialog>
  );
}

type Open = { kind: "add" } | { kind: "edit" | "rotate" | "remove"; leader: LeaderOut } | null;

function LeadersContent() {
  const read = useAdminList<LeaderOut>("/api/admin/leaders");
  const [open, setOpen] = useState<Open>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const close = () => setOpen(null);
  const done = (message: string) => {
    setNotice(message);
    read.refresh();
  };

  return (
    <>
      <div className="section-head">
        <button type="button" className="button button-primary" onClick={() => setOpen({ kind: "add" })}>
          Add leader
        </button>
      </div>
      <ActionNotice message={notice} />
      <ReadState read={read} what="leaders">
        {(leaders) =>
          leaders.length === 0 ? (
            <p>No leaders are registered.</p>
          ) : (
            <div className="table-scroll" role="region" aria-label="Registered leaders" tabIndex={0}>
              <table>
                <thead>
                  <tr>
                    <th scope="col">Leader</th>
                    <th scope="col">Address</th>
                    <th scope="col">Labels</th>
                    <th scope="col">Enabled</th>
                    <th scope="col">Credential</th>
                    <th scope="col">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {leaders.map((leader) => (
                    <tr key={leader.name}>
                      <th scope="row">{leader.name}</th>
                      <td className="mono">{leader.base_url}</td>
                      <td className="mono">{labelsText(leader.labels) || "–"}</td>
                      <td>{leader.enabled ? "Yes" : "No"}</td>
                      <td>
                        {leader.credential_revoked ? (
                          <span className="badge badge-bad">Revoked by the leader</span>
                        ) : (
                          <span>
                            Set {formatTime(leader.credential_updated_at)} by {leader.credential_updated_by}
                          </span>
                        )}
                      </td>
                      <td className="actions">
                        <button
                          type="button"
                          className="button"
                          onClick={() => setOpen({ kind: "edit", leader })}
                          aria-label={`Edit ${leader.name}`}
                        >
                          Edit
                        </button>
                        <button
                          type="button"
                          className="button"
                          onClick={() => setOpen({ kind: "rotate", leader })}
                          aria-label={`Rotate credential for ${leader.name}`}
                        >
                          Rotate credential
                        </button>
                        <button
                          type="button"
                          className="button button-danger"
                          onClick={() => setOpen({ kind: "remove", leader })}
                          aria-label={`Remove ${leader.name}`}
                        >
                          Remove
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </ReadState>
      {open?.kind === "add" && (
        <AddLeaderDialog onClose={close} onDone={(name) => done(`Leader ${name} is added.`)} />
      )}
      {open?.kind === "edit" && (
        <EditLeaderDialog
          leader={open.leader}
          onClose={close}
          onDone={() => done(`Leader ${open.leader.name} is saved.`)}
        />
      )}
      {open?.kind === "rotate" && (
        <RotateDialog
          leader={open.leader}
          onClose={close}
          onDone={() => done(`The credential for ${open.leader.name} is replaced.`)}
        />
      )}
      {open?.kind === "remove" && (
        <ConfirmDialog
          title={`Remove ${open.leader.name}?`}
          message="The console forgets this leader, its history and the grants that name it. The leader itself is not changed; revoke the console's credential there too."
          confirmLabel="Remove leader"
          onClose={close}
          onConfirm={async () => {
            await api.del(leaderApi(open.leader.name));
            done(`Leader ${open.leader.name} is removed.`);
          }}
        />
      )}
    </>
  );
}

export function AdminLeadersPage() {
  return (
    <AdminFrame title="Leaders">
      <LeadersContent />
    </AdminFrame>
  );
}
```

- [ ] **Step 4: Write the grants and administrators pages**

`packages/console-web/src/pages/admin/AdminGrantsPage.tsx`:

```tsx
import { useId, useState, type FormEvent } from "react";
import { api } from "../../api/client";
import type { GrantIn, GrantOut, PrincipalKind, Role } from "../../api/types";
import { useAction } from "../../app/useAction";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { ErrorPanel } from "../../components/ErrorPanel";
import { formatTime } from "../../lib/format";
import { ActionNotice, ReadState } from "../leader/common";
import { AdminFrame, useAdminList } from "./AdminFrame";
import { PrincipalFields } from "./PrincipalFields";

function AddGrantForm({ onDone }: { onDone: (grant: GrantOut) => void }) {
  const [role, setRole] = useState<Role>("viewer");
  const [scope, setScope] = useState("all");
  const [kind, setKind] = useState<PrincipalKind>("entra_group");
  const [principal, setPrincipal] = useState("");
  const scopeHelp = useId();
  const action = useAction();
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const body: GrantIn = { role, scope: scope.trim(), principal_kind: kind, principal: principal.trim() };
    const answer: { grant: GrantOut | null } = { grant: null };
    const ok = await action.run(async () => {
      answer.grant = await api.post<GrantOut>("/api/admin/grants", body);
    });
    if (ok && answer.grant !== null) {
      setPrincipal("");
      onDone(answer.grant);
    }
  };
  return (
    <form className="form-grid form-panel" aria-label="Add a grant" onSubmit={(event) => void submit(event)}>
      <h2>Add a grant</h2>
      <label className="field">
        Role
        <select value={role} onChange={(e) => setRole(e.target.value as Role)}>
          <option value="viewer">viewer</option>
          <option value="operator">operator</option>
          <option value="admin">admin</option>
        </select>
      </label>
      <div className="field">
        <label className="field">
          Scope
          <input required value={scope} onChange={(e) => setScope(e.target.value)} aria-describedby={scopeHelp} />
        </label>
        <span id={scopeHelp} className="field-help">
          all, leader:&lt;name&gt; or label:&lt;key&gt;=&lt;value&gt;
        </span>
      </div>
      <PrincipalFields kind={kind} principal={principal} onKind={setKind} onPrincipal={setPrincipal} />
      {action.error !== null && <ErrorPanel error={action.error} />}
      <div className="dialog-buttons">
        <button type="submit" className="button button-primary" disabled={action.busy}>
          Add grant
        </button>
      </div>
    </form>
  );
}

function GrantsContent() {
  const read = useAdminList<GrantOut>("/api/admin/grants");
  const [removing, setRemoving] = useState<GrantOut | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  return (
    <>
      <p className="muted">
        A person's role on a leader is the highest grant whose scope matches it. Changes apply at their next
        sign-in.
      </p>
      <ActionNotice message={notice} />
      <ReadState read={read} what="grants">
        {(grants) =>
          grants.length === 0 ? (
            <p>No grants.</p>
          ) : (
            <div className="table-scroll" role="region" aria-label="Grants" tabIndex={0}>
              <table>
                <thead>
                  <tr>
                    <th scope="col">Principal</th>
                    <th scope="col">Role</th>
                    <th scope="col">Scope</th>
                    <th scope="col">Added</th>
                    <th scope="col">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {grants.map((grant) => (
                    <tr key={grant.id}>
                      <th scope="row" className="mono">
                        {grant.principal_kind}:{grant.principal}
                      </th>
                      <td>{grant.role}</td>
                      <td className="mono">{grant.scope}</td>
                      <td>
                        {formatTime(grant.created_at)} by {grant.created_by}
                      </td>
                      <td className="actions">
                        <button
                          type="button"
                          className="button button-danger"
                          onClick={() => setRemoving(grant)}
                          aria-label={`Remove grant for ${grant.principal}`}
                        >
                          Remove
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </ReadState>
      <AddGrantForm
        onDone={(grant) => {
          setNotice(
            `Grant added: ${grant.role} on ${grant.scope} for ${grant.principal_kind}:${grant.principal}.`,
          );
          read.refresh();
        }}
      />
      {removing !== null && (
        <ConfirmDialog
          title="Remove this grant?"
          message={`${removing.principal_kind}:${removing.principal} loses ${removing.role} on ${removing.scope} at their next sign-in.`}
          confirmLabel="Remove grant"
          onClose={() => setRemoving(null)}
          onConfirm={async () => {
            await api.del(`/api/admin/grants/${removing.id}`);
            setNotice("Grant removed.");
            read.refresh();
          }}
        />
      )}
    </>
  );
}

export function AdminGrantsPage() {
  return (
    <AdminFrame title="Grants">
      <GrantsContent />
    </AdminFrame>
  );
}
```

`packages/console-web/src/pages/admin/AdminAdminsPage.tsx`:

```tsx
import { useState, type FormEvent } from "react";
import { api } from "../../api/client";
import type { ConsoleAdminIn, ConsoleAdminOut, PrincipalKind } from "../../api/types";
import { useAction } from "../../app/useAction";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { ErrorPanel } from "../../components/ErrorPanel";
import { formatTime } from "../../lib/format";
import { ActionNotice, ReadState } from "../leader/common";
import { AdminFrame, useAdminList } from "./AdminFrame";
import { PrincipalFields } from "./PrincipalFields";

function AddAdminForm({ onDone }: { onDone: (admin: ConsoleAdminOut) => void }) {
  const [kind, setKind] = useState<PrincipalKind>("entra_group");
  const [principal, setPrincipal] = useState("");
  const action = useAction();
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const body: ConsoleAdminIn = { principal_kind: kind, principal: principal.trim() };
    const answer: { admin: ConsoleAdminOut | null } = { admin: null };
    const ok = await action.run(async () => {
      answer.admin = await api.post<ConsoleAdminOut>("/api/admin/console-admins", body);
    });
    if (ok && answer.admin !== null) {
      setPrincipal("");
      onDone(answer.admin);
    }
  };
  return (
    <form
      className="form-grid form-panel"
      aria-label="Add a console administrator"
      onSubmit={(event) => void submit(event)}
    >
      <h2>Add a console administrator</h2>
      <PrincipalFields kind={kind} principal={principal} onKind={setKind} onPrincipal={setPrincipal} />
      {action.error !== null && <ErrorPanel error={action.error} />}
      <div className="dialog-buttons">
        <button type="submit" className="button button-primary" disabled={action.busy}>
          Add administrator
        </button>
      </div>
    </form>
  );
}

function AdminsContent() {
  const read = useAdminList<ConsoleAdminOut>("/api/admin/console-admins");
  const [removing, setRemoving] = useState<ConsoleAdminOut | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  return (
    <>
      <p className="muted">
        Console administrators manage leaders and grants. That gives them no role on any leader unless a
        grant does.
      </p>
      <ActionNotice message={notice} />
      <ReadState read={read} what="console administrators">
        {(admins) => (
          <div className="table-scroll" role="region" aria-label="Console administrators" tabIndex={0}>
            <table>
              <thead>
                <tr>
                  <th scope="col">Principal</th>
                  <th scope="col">Added</th>
                  <th scope="col">Actions</th>
                </tr>
              </thead>
              <tbody>
                {admins.map((admin) => (
                  <tr key={admin.id}>
                    <th scope="row" className="mono">
                      {admin.principal_kind}:{admin.principal}
                    </th>
                    <td>
                      {formatTime(admin.created_at)} by {admin.created_by}
                    </td>
                    <td className="actions">
                      <button
                        type="button"
                        className="button button-danger"
                        onClick={() => setRemoving(admin)}
                        aria-label={`Remove ${admin.principal}`}
                      >
                        Remove
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </ReadState>
      <AddAdminForm
        onDone={(admin) => {
          setNotice(`${admin.principal_kind}:${admin.principal} is a console administrator.`);
          read.refresh();
        }}
      />
      {removing !== null && (
        <ConfirmDialog
          title="Remove this console administrator?"
          message={`${removing.principal_kind}:${removing.principal} can no longer manage leaders and grants. The last administrator cannot be removed.`}
          confirmLabel="Remove administrator"
          onClose={() => setRemoving(null)}
          onConfirm={async () => {
            await api.del(`/api/admin/console-admins/${removing.id}`);
            setNotice("Console administrator removed.");
            read.refresh();
          }}
        />
      )}
    </>
  );
}

export function AdminAdminsPage() {
  return (
    <AdminFrame title="Console administrators">
      <AdminsContent />
    </AdminFrame>
  );
}
```

- [ ] **Step 5: Route to the pages**

Edit `packages/console-web/src/App.tsx` **in place** (it holds Task 2's edits); do not replace the file, and keep `shell.test.tsx` passing. Four edits:

1. Change `import { SessionProvider } from "./app/session";` to `import { SessionProvider, useSession } from "./app/session";`, and add the three admin page imports (`./pages/admin/AdminAdminsPage`, `./pages/admin/AdminGrantsPage`, `./pages/admin/AdminLeadersPage`) before the `./pages/FleetPage` import.
2. After the `FLEET` constant add:

```tsx
const ADMIN: NavItem = {
  to: "/admin/leaders",
  label: "Administration",
  match: (pathname) => pathname.startsWith("/admin"),
};
```

3. In `SignedInPage`, before `return <NotFoundPage />;` add:

```tsx
  if (pathname === "/admin") return <Redirect to="/admin/leaders" />;
  if (pathname === "/admin/leaders") return <AdminLeadersPage />;
  if (pathname === "/admin/grants") return <AdminGrantsPage />;
  if (pathname === "/admin/admins") return <AdminAdminsPage />;
```

4. Add a component that picks the navigation, and use it in `Routes` inside the `SessionProvider` branch (`<SessionProvider><SignedIn /></SessionProvider>`), replacing the inline `FleetProvider`/`Layout` block; the `/sign-in` branch stays outside it, as in C3a:

```tsx
function SignedIn() {
  const { session } = useSession();
  const nav = session.console_admin ? [FLEET, ADMIN] : [FLEET];
  return (
    <FleetProvider>
      <Layout nav={nav} pageOf={pageOf}>
        <SignedInPage />
      </Layout>
    </FleetProvider>
  );
}
```

- [ ] **Step 6: Run everything and commit**

Run: `npm test`
Expected: PASS — 148 tests in 20 files (139 + 9 new in the administration tests).

Run: `npm run typecheck` then `npm run lint` then `npm run build`
Expected: all exit 0; the build ends with `dist/ ok: index.html and 2 hashed assets`.

```bash
git add packages/console-web/src
git commit -m "Console web app: administration pages for leaders, grants and console administrators

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: End-to-end — actions by keyboard, administration, axe on every page and dialog

**Files:**
- Create: `packages/console-web/e2e/tests/drilldown.spec.ts`, `e2e/tests/admin.spec.ts`
- Modify: `packages/console-web/e2e/tests/a11y.spec.ts` (replace: every page, plus the dialogs)

**Interfaces:**
- Consumes: C3a's harness and `e2e/tests/support.ts` (`test`, `expect`, `signIn`, `setLeaderMode`, `expectAccessible`, `THEMES`). The harness's fake leaders, per leader: jobs — 2 queued (one in pool `gpu`), 1 leased by the first follower, 1 completed (`incoming/meeting-4.wav`), 1 failed ("the engine stopped: out of memory"), 1 cancelled; followers — `default`/active/cuda (holds the leased job), `gpu`/active/cuda, `default`/draining/cpu; locations `intake` and `archive`; one join token; `us-1` caps the console at operator. Personas: `viewer`, `operator`, `admin` (also a console administrator).
- Produces: nothing later tasks use.

- [ ] **Step 1: Write the drill-down tests**

`packages/console-web/e2e/tests/drilldown.spec.ts`:

```ts
import type { Page } from "@playwright/test";
import { expect, setLeaderMode, signIn, test } from "./support";

/** Presses Tab until the focused element's accessible name matches, as a keyboard user would. */
async function tabTo(page: Page, name: RegExp, limit = 60): Promise<void> {
  for (let i = 0; i < limit; i += 1) {
    await page.keyboard.press("Tab");
    const focused = await page.evaluate(() => {
      const el = document.activeElement as HTMLElement | null;
      return el?.getAttribute("aria-label") ?? el?.textContent ?? "";
    });
    if (name.test(focused.trim())) return;
  }
  throw new Error(`Tab never reached ${name}`);
}

test("an operator retries a failed job and cancels a queued one with the keyboard alone", async ({ page }) => {
  await signIn(page, "operator", "/leaders/eu-1/jobs");
  const failed = page.getByRole("row").filter({ hasText: "the engine stopped" });
  await expect(failed).toBeVisible();

  await tabTo(page, /^Retry job /);
  await page.keyboard.press("Enter");
  await expect(page.getByText(/^Job \w{8} is queued again\.$/)).toBeVisible();

  await page.getByRole("combobox", { name: "State" }).focus();
  await page.keyboard.press("Home");
  await tabTo(page, /^Cancel job /);
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", { name: /^Cancel job \w{8}\?$/ });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Close" })).toBeFocused();
  await page.keyboard.press("Tab");
  await page.keyboard.press("Enter");
  await expect(dialog).toHaveCount(0);
  await expect(page.getByText(/^Job \w{8} is cancelled\.$/)).toBeVisible();
});

test("the job filter narrows by state and survives a reload", async ({ page }) => {
  await signIn(page, "viewer", "/leaders/eu-1/jobs");
  await page.getByRole("combobox", { name: "State" }).selectOption("failed");
  await expect(page).toHaveURL("/leaders/eu-1/jobs?state=failed");
  await expect(page.getByRole("region", { name: "Jobs" }).getByRole("row")).toHaveCount(2);
  await page.reload();
  await expect(page.getByRole("combobox", { name: "State" })).toHaveValue("failed");
});

test("a viewer sees actions disabled with the role they need", async ({ page }) => {
  await signIn(page, "viewer", "/leaders/eu-1/jobs");
  const retry = page.getByRole("button", { name: /^Retry job / }).first();
  await expect(retry).toBeDisabled();
  await expect(retry).toHaveAccessibleDescription("needs operator");
  await page.getByRole("link", { name: "Join tokens" }).click();
  await expect(page.getByText("Join tokens need the admin role on eu-1. Your role is viewer.")).toBeVisible();
});

test("an operator drains a follower but cannot revoke one", async ({ page }) => {
  await signIn(page, "operator", "/leaders/eu-1/pools");
  await expect(
    page.getByRole("region", { name: "Pools" }).getByRole("rowheader", { name: "gpu" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: /^Drain follower / })
    .first()
    .click();
  await expect(page.getByText(/^Follower \w{8} is draining\.$/)).toBeVisible();
  await expect(page.getByRole("button", { name: /^Revoke follower / }).first()).toBeDisabled();
});

test("an admin revokes a follower and its leased job goes back to the queue", async ({ page }) => {
  await signIn(page, "admin", "/leaders/eu-1/pools");
  const row = page
    .getByRole("region", { name: "Followers" })
    .getByRole("row")
    .filter({ hasText: "cuda" })
    .first();
  await row.getByRole("button", { name: /^Revoke follower / }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Revoke follower" }).click();
  await expect(page.getByText(/is revoked; 1 leased jobs went back to the queue\./)).toBeVisible();
});

test("an admin adds, disables, enables and scans locations", async ({ page }) => {
  await signIn(page, "admin", "/leaders/eu-1/locations");
  await expect(page.getByText("the root folder is not readable")).toBeVisible();
  await page.getByRole("button", { name: "Add location" }).click();
  const dialog = page.getByRole("dialog", { name: "Add a location to eu-1" });
  await dialog.getByRole("textbox", { name: "Name" }).fill("calls");
  await dialog.getByRole("textbox", { name: "Folder on the leader (absolute path)" }).fill("/srv/calls");
  await dialog.getByRole("combobox", { name: "Channels" }).selectOption("stereo_split");
  await dialog.getByRole("textbox", { name: "Left channel label" }).fill("Agent");
  await dialog.getByRole("textbox", { name: "Right channel label" }).fill("Caller");
  await dialog.getByRole("button", { name: "Add location" }).click();
  await expect(page.getByText("Location calls is added.")).toBeVisible();
  await expect(page.getByRole("row", { name: /calls/ })).toContainText("stereo_split (Agent, Caller)");

  await page.getByRole("button", { name: "Disable archive" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Disable location" }).click();
  await expect(page.getByText("archive is disabled.")).toBeVisible();
  await page.getByRole("button", { name: "Enable archive" }).click();
  await expect(page.getByText("archive is enabled.")).toBeVisible();
  await page.getByRole("button", { name: "Scan now intake" }).click();
  await expect(page.getByText("A scan of intake is requested.")).toBeVisible();
  await expect(page.getByRole("row", { name: /intake/ })).toContainText("Scan requested");
});

test("a join token's plaintext is shown once, copied, and gone when the dialog closes", async ({
  page,
  context,
}) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await signIn(page, "admin", "/leaders/eu-1/tokens");
  const rows = page.getByRole("region", { name: "Join tokens" }).getByRole("row");
  await expect(rows).toHaveCount(2);

  await tabTo(page, /^Create join token$/);
  await page.keyboard.press("Enter");
  const form = page.getByRole("dialog", { name: "Create a join token for eu-1" });
  await expect(form.getByRole("textbox", { name: "Pool" })).toBeFocused();
  await page.keyboard.press("Control+A");
  await page.keyboard.type("gpu");
  await page.keyboard.press("Enter");

  const shown = page.getByRole("dialog", { name: "Join token created" });
  const field = shown.getByRole("textbox", { name: "Join token" });
  await expect(field).toHaveValue(/^sst_/);
  const plaintext = await field.inputValue();
  await shown.getByRole("button", { name: "Copy token" }).click();
  await expect(shown.getByText("Copied to the clipboard.")).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(plaintext);

  await shown.getByRole("button", { name: "Done" }).click();
  await expect(shown).toHaveCount(0);
  await expect(rows).toHaveCount(3);
  expect(await page.content()).not.toContain(plaintext);
  expect(page.url()).not.toContain(plaintext);
});

test("a leader's own refusal is shown: us-1 caps the console at operator", async ({ page }) => {
  await signIn(page, "admin", "/leaders/us-1/locations");
  await page.getByRole("button", { name: "Disable intake" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "Disable location" }).click();
  await expect(dialog.getByRole("alert")).toContainText("Your role does not allow this.");
  await expect(dialog.getByRole("alert")).toContainText("this needs the admin role");
});

test("the consent report shows counts and transcripts to review", async ({ page }) => {
  await signIn(page, "viewer", "/leaders/eu-1/consent");
  await expect(
    page.getByRole("region", { name: "Consent by location" }).getByRole("row", { name: /intake/ }),
  ).toBeVisible();
  await expect(page.getByRole("region", { name: "Transcripts to review" })).toContainText(
    "transcripts/meeting-4.wav.json",
  );
});

test("with one leader down its tabs report it, and the other leader stays operable", async ({
  page,
  request,
}) => {
  await signIn(page, "operator", "/leaders/us-1/jobs");
  await setLeaderMode(request, "us-1", "down");
  await page.getByRole("button", { name: "Refresh" }).click();
  await expect(page.getByRole("alert")).toContainText("The leader cannot be reached right now.");
  await expect(page.getByRole("link", { name: "Locations" })).toBeVisible();

  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Fleet" }).click();
  await page.getByRole("link", { name: "eu-1" }).click();
  await page.getByRole("link", { name: "Jobs" }).click();
  await page
    .getByRole("button", { name: /^Cancel job / })
    .first()
    .click();
  await page.getByRole("dialog").getByRole("button", { name: "Cancel job" }).click();
  await expect(page.getByText(/^Job \w{8} is cancelled\.$/)).toBeVisible();
});

test("a drill-down address survives a reload and a leader name without a tab redirects", async ({
  page,
}) => {
  await signIn(page, "viewer", "/leaders/eu-1");
  await expect(page).toHaveURL("/leaders/eu-1/pools");
  await page.getByRole("link", { name: "Consent report" }).click();
  await page.reload();
  await expect(page.getByRole("heading", { level: 2, name: "Consent report" })).toBeVisible();
});
```

- [ ] **Step 2: Write the administration tests**

`packages/console-web/e2e/tests/admin.spec.ts`:

```ts
import { expect, signIn, test } from "./support";

/** The console tests' credential shape (console_testkit.CREDENTIAL): 43 URL-safe characters. */
const CREDENTIAL = "c".repeat(20) + "_-" + "D".repeat(21);

test("a console administrator adds and removes a grant", async ({ page }) => {
  await signIn(page, "admin");
  await page.getByRole("link", { name: "Administration" }).click();
  await page.getByRole("link", { name: "Grants" }).click();
  const form = page.getByRole("form", { name: "Add a grant" });
  await form.getByRole("combobox", { name: "Role" }).selectOption("operator");
  await form.getByRole("textbox", { name: "Scope" }).fill("label:region=eu");
  await form.getByRole("combobox", { name: "Principal kind" }).selectOption("domain");
  await form.getByRole("textbox", { name: "Principal" }).fill("example.org");
  await form.getByRole("button", { name: "Add grant" }).click();
  await expect(
    page.getByText("Grant added: operator on label:region=eu for domain:example.org."),
  ).toBeVisible();

  await page.getByRole("button", { name: "Remove grant for example.org" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Remove grant" }).click();
  await expect(page.getByText("Grant removed.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Remove grant for example.org" })).toHaveCount(0);
});

test("a console administrator registers, rotates and removes a leader", async ({ page }) => {
  await signIn(page, "admin", "/admin/leaders");
  await page.getByRole("button", { name: "Add leader" }).click();
  const dialog = page.getByRole("dialog", { name: "Add a leader" });
  await dialog.getByRole("textbox", { name: "Name" }).fill("ap-1");
  await dialog.getByRole("textbox", { name: "Address (https://)" }).fill("https://ap-1.leaders.example");
  await dialog.getByRole("textbox", { name: "Labels" }).fill("region=ap");
  await dialog.getByLabel("Console credential").fill(CREDENTIAL);
  await dialog.getByRole("button", { name: "Add leader" }).click();
  await expect(page.getByText("Leader ap-1 is added.")).toBeVisible();
  expect(await page.content()).not.toContain(CREDENTIAL);

  await page.getByRole("button", { name: "Rotate credential for ap-1" }).click();
  const rotate = page.getByRole("dialog", { name: "Rotate the credential for ap-1" });
  await rotate.getByLabel("New console credential").fill(CREDENTIAL);
  await rotate.getByRole("button", { name: "Replace credential" }).click();
  await expect(page.getByText("The credential for ap-1 is replaced.")).toBeVisible();

  // The new leader has no fake behind it: it shows in the fleet (after the next 10 s
  // refresh) without figures, and the rest of the page is unaffected.
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Fleet" }).click();
  await expect(page.getByRole("rowheader", { name: /ap-1/ })).toBeVisible({ timeout: 20_000 });

  await page.getByRole("link", { name: "Administration" }).click();
  await page.getByRole("button", { name: "Remove ap-1" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Remove leader" }).click();
  await expect(page.getByText("Leader ap-1 is removed.")).toBeVisible();
});

test("the console refuses a leader address it may not call", async ({ page }) => {
  await signIn(page, "admin", "/admin/leaders");
  await page.getByRole("button", { name: "Add leader" }).click();
  const dialog = page.getByRole("dialog", { name: "Add a leader" });
  await dialog.getByRole("textbox", { name: "Name" }).fill("local-1");
  await dialog.getByRole("textbox", { name: "Address (https://)" }).fill("https://localhost");
  await dialog.getByLabel("Console credential").fill(CREDENTIAL);
  await dialog.getByRole("button", { name: "Add leader" }).click();
  await expect(dialog.getByRole("alert")).toContainText("That leader URL is not allowed.");
});

test("the last console administrator cannot be removed", async ({ page }) => {
  await signIn(page, "admin", "/admin/admins");
  await page.getByRole("button", { name: /^Remove / }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "Remove administrator" }).click();
  await expect(dialog.getByRole("alert")).toContainText("The last console administrator cannot be removed.");
});

test("a person who is not a console administrator has no administration", async ({ page }) => {
  await signIn(page, "operator");
  await expect(page.getByRole("link", { name: "Administration" })).toHaveCount(0);
  await page.goto("/admin/leaders");
  await expect(page.getByText(/Console administration needs a console administrator/)).toBeVisible();
});
```

- [ ] **Step 3: Scan every page and dialog**

Replace `packages/console-web/e2e/tests/a11y.spec.ts` with:

```ts
import { THEMES, expect, expectAccessible, signIn, test } from "./support";

// Every page the app has, in both themes, with zero axe violations (WCAG 2.1 A and AA).
const SIGNED_IN_PAGES: { path: string; heading: string }[] = [
  { path: "/", heading: "Fleet" },
  { path: "/leaders/eu-1/pools", heading: "eu-1" },
  { path: "/leaders/eu-1/jobs", heading: "eu-1" },
  { path: "/leaders/eu-1/locations", heading: "eu-1" },
  { path: "/leaders/eu-1/tokens", heading: "eu-1" },
  { path: "/leaders/eu-1/consent", heading: "eu-1" },
  { path: "/admin/leaders", heading: "Leaders" },
  { path: "/admin/grants", heading: "Grants" },
  { path: "/admin/admins", heading: "Console administrators" },
  { path: "/no-such-page", heading: "Page not found" },
];

for (const theme of THEMES) {
  test.describe(`${theme} theme`, () => {
    test.use({ colorScheme: theme });

    test("the sign-in page has no accessibility violations", async ({ page }) => {
      await page.goto("/sign-in?signed_out=1");
      await expect(page.getByRole("link", { name: "Sign in with Microsoft Entra ID" })).toBeVisible();
      await expectAccessible(page, `sign-in (${theme})`);
    });

    test("every signed-in page has no accessibility violations", async ({ page }) => {
      await signIn(page, "admin");
      for (const { path, heading } of SIGNED_IN_PAGES) {
        await page.goto(path);
        await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
        await expect(page.getByText(/^Loading/)).toHaveCount(0);
        await expectAccessible(page, `${path} (${theme})`);
      }
    });

    test("the dialogs have no accessibility violations", async ({ page }) => {
      await signIn(page, "admin", "/leaders/eu-1/tokens");
      await page.getByRole("button", { name: "Create join token" }).click();
      await expectAccessible(page, `create token dialog (${theme})`);
      await page.getByRole("dialog").getByRole("button", { name: "Create token" }).click();
      await expect(page.getByRole("dialog", { name: "Join token created" })).toBeVisible();
      await expectAccessible(page, `token created dialog (${theme})`);
      await page.getByRole("button", { name: "Done" }).click();

      await page
        .getByRole("button", { name: /^Revoke token / })
        .first()
        .click();
      await expectAccessible(page, `confirm dialog (${theme})`);
      await page.getByRole("button", { name: "Close" }).click();

      await page.goto("/leaders/eu-1/locations");
      await page.getByRole("button", { name: "Add location" }).click();
      await expectAccessible(page, `add location dialog (${theme})`);

      await page.goto("/admin/leaders");
      await page.getByRole("button", { name: "Add leader" }).click();
      await expectAccessible(page, `add leader dialog (${theme})`);
    });
  });
}
```

- [ ] **Step 4: Run the end-to-end tests**

Run (in `packages/console-web`):

```bash
npm run build
npm run e2e
```

Expected: `30 passed` — 6 in `a11y.spec.ts`, 5 in `admin.spec.ts`, 11 in `drilldown.spec.ts`, 5 in `overview.spec.ts`, 3 in `sign-in.spec.ts`. The whole run takes about a minute and a half.

If an axe test fails on `color-contrast`, change the token in `src/styles.css` (both theme blocks), never a per-component override. If a test cannot find a row-action button, check the button's `aria-label`: the tests look buttons up by their full accessible name.

Run: `npm run typecheck` then `npm run lint` then `npm test`
Expected: all pass (148 unit and component tests: the Task 6 total).

- [ ] **Step 5: Run the backend's checks and commit**

Run (repository root): `python -m uv run ruff check .`
Expected: `All checks passed!`

```bash
git add packages/console-web/e2e
git commit -m "Console web app: end-to-end tests for leader actions, administration and axe on every page and dialog

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

CI needs no change: C3a's `web` and `web-e2e` jobs run these tests.

---

## Self-review

**Spec coverage (C3b's share).**
- Spec 6 drill-down — pools and followers with drain and revoke (Task 2); jobs with the state filter and retry, cancel, priority (Task 3); locations with add, enable, disable and ingest (Task 4); join tokens with create, list, revoke (Task 5); consent report (Task 5).
- Spec 6 "Actions the person's role does not allow are shown disabled with the required role" — `ActionButton` (Task 1), used by every action in Tasks 2–5, from `FleetLeader.role` and the allow-list copy in `roles.ts`; the tokens tab's refusal below admin (Task 5).
- Spec 5.4 token plaintext once — `TokenCreatedDialog` and the lifetime described in Task 5's Interfaces; unit and end-to-end tests that it is gone from the DOM, the URL and storage.
- Spec 5.4 leader errors passed through, `leader_unreachable` — shown where the action was taken (Tasks 1, 3; Task 7's `us-1` and leader-down tests).
- Spec 5.1/5.2 administration — leaders add, edit, rotate, remove; grants; console administrators (Task 6), only for console administrators.
- Spec 6 accessibility, tablet width, themes — native `<dialog>` with focus on the safe button, labelled fields with help text outside the label, row-specific accessible names, focusable scroll regions (Tasks 1–6); the keyboard-only flows and the axe scan of every page and dialog in both themes (Task 7). The 768 px check is C3a's and still runs.
- Spec 9 web app line — component tests for the drill-down (Tasks 2–5) and administration (Task 6); Playwright against the console and two fake leaders (Task 7); axe on every page (Task 7).
- Spec 1 "nothing else in the console stops working" — Task 3's unreachable-tab test; Task 7's leader-down test operates the other leader.

**Placeholder scan.** No TBD or "similar to" steps. Files that grow across tasks are changed by exact edits: `tabs.ts` and `LeaderPage.tsx` (Tasks 3, 4, 5). `App.tsx` is edited in place both times it changes (Tasks 2, 6), never replaced.

**Type consistency.** `ActionButton`'s props are `held`, `action`, `onClick`, `danger`, `busy`, `name` in Task 1 and in every use (Tasks 2–5). `useAction().run` returns `Promise<boolean>` and every caller awaits it. `TabProps { leader }` is what `LeaderPage` passes each tab. `useLeaderRead<T>(name, rest)` returns C3a's `PollState<T>`, which `ReadState` and `RefreshButton` take. `leaderUrl(name, tab?)` is used by `App.tsx`, `FleetPage.tsx`, `LeaderPage.tsx` and `JobsTab.tsx` with `TabId` values that exist by then (`"jobs"` from Task 3). `renderApp(path, { fleet, session })` is used with those option names in every test. Request bodies match the models named in Global Constraints: `{ priority }`, `{ pool, expires_in_seconds, max_uses }`, `locationBody(...)`, `LeaderIn`, `LeaderEdit` from `editBody(...)`, `{ credential }`, `GrantIn`, `ConsoleAdminIn`. The tab ids in `tabs.ts` after Task 5 (`pools`, `jobs`, `locations`, `tokens`, `consent`) are the cases of `TabContent` and the paths in `a11y.spec.ts`.

**Review Focus.** Each of the seven lines names its test: the token (`TokensTab.test.tsx`, `drilldown.spec.ts`); confirmation focus (`drilldown.spec.ts` asserts "Close" is focused; `ConfirmDialog` disables while busy); leader refusals (`JobsTab.test.tsx` `not_retryable`, `drilldown.spec.ts` `us-1`); leader down (`JobsTab.test.tsx`, `drilldown.spec.ts`); credentials and labels (`admin.test.tsx`, `admin.spec.ts`); below the role (`ActionButton.test.tsx`, `JobsTab.test.tsx`, `TokensTab.test.tsx`, `admin.test.tsx`); URLs (`drilldown.spec.ts` reload and redirect test).

**How this plan's code was checked.** As C3a: every file was built and run in a scratch copy of the repository on top of C3a's code — type-check and lint clean, 148 unit and component tests passing (118 from C3a plus this plan's 30), and all 30 end-to-end tests passing against the real console backend, including the axe scans of every page and dialog in both themes — and then transcribed into this document. Three small restructurings were made during transcription and were not re-run: `LabelsField` and the `channels()` helper were extracted, and the pool-name pattern became a named constant in `TokensTab.tsx`. The commands in each task are the check.
