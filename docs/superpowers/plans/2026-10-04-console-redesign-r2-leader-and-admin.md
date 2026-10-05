# Console Redesign R2: Leader Pages, Dialogs and Administration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the Ink console: rebuild a leader's page and its five tabs, the one-time join token dialog and Administration to the approved mockups, and replace every remaining protocol word (queued, leased, retry, poll, grant, scope) with the console's own.

**Architecture:** R1 left every class restyled, so this plan is mostly markup and words inside existing components, one new stylesheet (`src/styles/detail.css`) and one new component (`AdminPage.tsx`) that gives Administration's three sections a shared frame. No route, API call, role or security rule changes.

**Tech Stack:** React 19.3.0, TypeScript 6.0.3, Vite 8.3.2, Vitest 5.0.3 with Testing Library, Playwright 1.63.0 with @axe-core/playwright 4.13.0. No dependency is added or removed.

**Spec:** `docs/superpowers/specs/2026-10-04-console-redesign.md` (the authority; read sections 4.8 to 4.13, 5.4 to 5.7, 6 and 7 before Task 1). This plan's mockups, in `docs/superpowers/design/`: `LeaderJobs.dc.html`, `TokenDialog.dc.html`, `AdminLeaders.dc.html`.

**Precondition:** R1 (`docs/superpowers/plans/2026-10-04-console-redesign-r1-shell-and-fleet.md`) is complete on the `console-redesign` branch. All `npm` and `npx` commands run in `packages/console-web`.

**Node.** As R1: `engines` is `>=24.15 <25` with `engine-strict`; on Node 24.13 use `npm ci --engine-strict=false` for the session. Do not edit `.npmrc` or `engines`.

Before Task 1, in `packages/console-web`: `npm test` (**384 tests in 26 files** pass: R1's final total), `npm run typecheck`, `npm run lint`, `npm run build`, `npm run e2e -- --retries=0` (all pass). If anything fails before you have changed a line, stop and report it.

**Test-count rule.** Every "N tests pass" below is the previous total plus this task's new tests, starting from 384.

**What was checked when this plan was written, and what was not.** As R1: each task's end state was built from this plan's blocks and run through `tsc`, ESLint, the unit tests and the build, and its pages were scanned with axe in both themes at 1280 and 768 pixels against canned API answers. The end-to-end specs were **not run** against the real harness. You are the first to run them.

## How to read the code blocks

Every block names its file and says one of three things:

- **Create** `path`: the file is new. The block is the whole file.
- **Replace the whole of** `path` **with**: the block is the whole file as it must be after
  this task. Overwrite the file.
- **Change** `path`: the block is a unified diff against the file as the previous task left
  it. Apply it by hand, or save the block to a file and run
  `git apply --whitespace=nowarn <file>` from the repository root (the paths in the diff
  start at `packages/console-web/`). A hunk that does not apply means the file is not as
  the previous task left it: stop and find out why before going on.

Files use LF line endings. Blocks are fenced with four backticks because some files contain
three.

## Global Constraints

- Every constraint of R1 holds: no inline style, nothing off-origin, no `dangerouslySetInnerHTML`, no `console.*`, no storage outside `theme.ts`; `eslint.config.js`, `scripts/check-dist.mjs`, `vite.config.ts` and `.npmrc` are not edited; colours only in `tokens.css`; no dependency added; no file outside `packages/console-web` changed; no test deleted or weakened.
- Wording is the spec's section 6, character for character. The protocol's words (poll, queued, leased, completed as a state, retry, attempts, drain, enabled and disabled, grant, scope, principal, rotate, URL) are not shown to a person. They stay in the API and in addresses: `?state=queued` is unchanged.
- The leader's own text (a failure reason, a scan error, an error message) is shown as sent, as text.
- Loading lines begin with "Loading".
- A button's accessible name contains its visible text, word for word: "Try again" is named "Try again: job 1a2b3c4d", "Replace credential" is named "Replace credential for eu-1".
- Behaviour does not change: which actions show for which state and role, what each form validates and sends, when lists are read (on open, on Refresh and after an action; never on a timer), the dialog's rules, the one-time token's rules (shown once, never stored, cannot be dismissed by accident, focus starts on the token).
- A join token, a credential or the CSRF token never goes in a URL, in storage or in state that outlives its dialog. The security tests in `TokensTab.test.tsx` and `adminSecurity.test.tsx` keep every assertion they have.
- Zero axe violations on every page and dialog in both themes. No word broken across lines in a table cell. No sideways page scroll at 768px. The Actions column stays pinned.
- One primary button per view at most.
- Commit after each task. Do not push. End every commit message with the trailer shown in the task's Commit step.

## Review Focus

1. **A leader that has never been checked, and a check that leaves a state out of its job counts** → the state pills show no counts for the first, and 0 (not "undefined", not a missing pill) for the second. — Task 3, `src/pages/leader/JobsTab.test.tsx` ("shows no counts for a leader that has never been checked, and 0 for a state its check left out").
2. **A viewer on a tablet** → switched-off actions, with the line that says which role they need, stay inside the pinned column at both ends of the table's scroll, and the page does not scroll sideways. — Task 3, `e2e/tests/drilldown.spec.ts` ("a viewer's switched-off actions stay pinned and readable at tablet width").
3. **The one-time token on a phone (390px)** → the dialog fits the window; the token, Copy and "I have stored it" are all inside it and reachable; nothing in it scrolls sideways. — Task 5, `e2e/tests/tokens.spec.ts` ("on a phone the one-time token dialog fits…").
4. **Administration's list of leaders cannot be loaded** → the heading stays "Leaders" (never "0 leaders"), the error has its retry, "Add a leader" and the two-step callout are still there, and the retry brings the count. — Task 6, `src/pages/admin/admin.test.tsx` ("keeps the heading and the way to add a leader when the list cannot be loaded").
5. **Changing "Who" after typing** → what was typed is kept and the box is renamed for the new choice. — Task 6, `src/pages/admin/admin.test.tsx` ("keeps what was typed when the kind of who is changed, and renames the box").

## Decisions this plan makes

From the spec's section 9; the ones an implementer will trip over:

- **State pills replace the State select on Jobs.** They are toggle buttons; the address still carries `?state=failed`. Pressing the pressed pill clears the filter. A state in the address that has no pill is shown as its own pressed pill.
- **Counts on the pills come from the fleet's last check of the leader** (`leader.snapshot.status.jobs`), not from the list, and are left off while a location filter is on.
- **The jobs table keeps Priority and drops Detail**; the detail is the note under the recording. **The locations table goes from nine columns to six**; device, channels and scan interval become notes.
- **Administration is one page.** `AdminPage` renders one `AdminFrame` for all three addresses, so the frame is not rebuilt when the section changes and focus stays on the section's link. `pageOf` in `App.tsx` maps every `/admin…` path to one page for the same reason.
- **The three admin files keep their names** (`AdminLeadersPage.tsx` and so on) but now export a section (`AdminLeadersSection`), not a page.
- **Region names do not change** ("Job list", "Followers", "Pools", "Location list", "Join token list", "Registered leaders", "Grants", "Console administrators"). R1's pinned-column test finds tables by them.

## File Structure

| File | Responsibility |
| --- | --- |
| `src/api/errors.ts` | The error titles, in the console's words. |
| `src/components/ConfirmDialog.tsx` | The safe choice is "No, go back". |
| `src/components/ActionButton.tsx` | Gains `primary`. |
| `src/pages/leader/common.tsx` | `RefreshButton` also says when the list was loaded. |
| `src/styles/detail.css` | The list toolbar, job states, the one-time token's layout. |
| `src/pages/leader/LeaderPage.tsx` | Header with labels, the role line, health notes. |
| `src/pages/leader/JobsTab.tsx` | State pills with counts; `jobStateText`, `jobNote`, `STATE_PILLS`. |
| `src/pages/leader/PoolsTab.tsx` | `followerStateText`; "Wind down". |
| `src/pages/leader/LocationsTab.tsx` | Six columns; `channels`, `deviceText`; "Switch off". |
| `src/pages/leader/ConsentTab.tsx` | Words. |
| `src/pages/leader/TokensTab.tsx` | The one-time token dialog to the mockup. |
| `src/pages/admin/AdminPage.tsx` | One frame for Administration's three sections. |
| `src/pages/admin/AdminFrame.tsx` | `h1` "Administration", the opening paragraph, the sections' links. |
| `src/pages/admin/Admin{Leaders,Grants,Admins}Page.tsx` | Each exports its section. |
| `src/pages/admin/PrincipalFields.tsx` | "Who", and a box named for the choice. |


---

### Task 1: Error titles, and the confirmation's way out

**Files:**
- Modify: `packages/console-web/src/api/errors.ts`
- Modify: `packages/console-web/src/components/ConfirmDialog.tsx`
- Test: `packages/console-web/src/api/errors.test.ts`
- Test: `packages/console-web/src/app/session.test.tsx`
- Test: `packages/console-web/src/components/ActionButton.test.tsx`
- Test: `packages/console-web/src/pages/admin/adminSecurity.test.tsx`
- Test: `packages/console-web/src/pages/leader/JobsTab.test.tsx`
- Test: `packages/console-web/src/pages/leader/LocationsTab.test.tsx`
- Test: `packages/console-web/e2e/tests/a11y.spec.ts`
- Test: `packages/console-web/e2e/tests/admin.spec.ts`
- Test: `packages/console-web/e2e/tests/drilldown.spec.ts`
- Test: `packages/console-web/e2e/tests/tokens.spec.ts`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `ERROR_TITLES` in the console's words (spec section 6.2, "Error titles"); a confirmation whose safe button is named "No, go back".

Words only. `errors.ts` gets the titles of the spec's table; each keeps its meaning. The
confirmation dialog's first button, the one that takes focus, says "No, go back" instead of
"Close".

These strings are asserted in many places, so this task also updates every test that names
one of them, and nothing else in those files.

- [ ] **Step 1: Write the failing tests**

`errors.test.ts` gains a test that every title is a sentence and uses none of the protocol's
words.

<!-- file: src/api/errors.test.ts | patch -->
Change `packages/console-web/src/api/errors.test.ts`:

````diff
--- a/packages/console-web/src/api/errors.test.ts
+++ b/packages/console-web/src/api/errors.test.ts
@@ -27,10 +27,18 @@
     expect(describeError(new ApiError(400, code, "x")).title).toBe(ERROR_TITLES[code]);
   });
 
-  it("shows leader_not_found as not visible to you", () => {
+  it("shows leader_not_found as a leader the person cannot see", () => {
     expect(describeError(new ApiError(404, "leader_not_found", "no leader with that name")).title).toBe(
-      "This leader is not visible to you.",
+      "You cannot see this leader.",
     );
+  });
+
+  it("says every title as a sentence, in the console's own words", () => {
+    for (const [code, title] of Object.entries(ERROR_TITLES)) {
+      expect(title, code).toMatch(/^[A-Z].*\.$/);
+      // The leader's and the protocol's words stay out of what a person reads.
+      expect(title, code).not.toMatch(/\b(poll|leased|queued|retr(y|ied)|unreachable|principal|scope|URL)\b/i);
+    }
   });
 
   it("keeps the server's message as the detail and adds Retry-After", () => {
@@ -50,13 +58,13 @@
 
   it("falls back by status for an unknown code", () => {
     expect(describeError(new ApiError(409, "something_new", "it clashed")).title).toBe(
-      "That conflicts with the current state.",
+      "That no longer fits how things stand. Refresh, then look again.",
     );
     expect(describeError(new ApiError(504, "gateway_timeout", "slow")).title).toBe(
-      "The leader or the console failed to answer.",
+      "The leader or the console did not answer.",
     );
     expect(describeError(new ApiError(429, "slow_down", "wait")).title).toBe(
-      "Too many requests. Wait, then try again.",
+      "Too many requests. Wait a little, then try again.",
     );
   });
 
@@ -65,6 +73,6 @@
   });
 
   it("never throws on something that is not an ApiError", () => {
-    expect(describeError(new Error("boom")).title).toBe("The console hit an unexpected error.");
+    expect(describeError(new Error("boom")).title).toBe("Something went wrong in the console.");
   });
 });
````

<!-- file: src/app/session.test.tsx | patch -->
Change `packages/console-web/src/app/session.test.tsx`:

````diff
--- a/packages/console-web/src/app/session.test.tsx
+++ b/packages/console-web/src/app/session.test.tsx
@@ -83,7 +83,9 @@
         <Who />
       </SessionProvider>,
     );
-    expect(await screen.findByRole("alert")).toHaveTextContent("temporarily unavailable");
+    expect(await screen.findByRole("alert")).toHaveTextContent(
+      "The console is not answering just now. Try again shortly.",
+    );
     await userEvent.click(screen.getByRole("button", { name: "Try again" }));
     expect(await screen.findByText("signed in as person@example.org")).toBeInTheDocument();
   });
````

<!-- file: src/components/ActionButton.test.tsx | patch -->
Change `packages/console-web/src/components/ActionButton.test.tsx`:

````diff
--- a/packages/console-web/src/components/ActionButton.test.tsx
+++ b/packages/console-web/src/components/ActionButton.test.tsx
@@ -64,7 +64,7 @@
       />,
     );
     await userEvent.click(screen.getByRole("button", { name: "Revoke" }));
-    expect(await screen.findByRole("alert")).toHaveTextContent("The leader cannot be reached right now.");
+    expect(await screen.findByRole("alert")).toHaveTextContent("The leader is not answering right now.");
     expect(onClose).not.toHaveBeenCalled();
   });
 });
@@ -73,12 +73,12 @@
     render(<ConfirmDialog title="Cancel job?" message="m" confirmLabel="Cancel job" onConfirm={vi.fn()} onClose={vi.fn()} />);
     const dialog = screen.getByRole("alertdialog", { name: "Cancel job?" });
     expect(dialog).toHaveAttribute("aria-modal", "true");
-    expect(screen.getByRole("button", { name: "Close" })).toHaveFocus();
+    expect(screen.getByRole("button", { name: "No, go back" })).toHaveFocus();
   });
 
   it("wraps Tab and Shift+Tab inside the dialog", async () => {
     render(<ConfirmDialog title="T" message="m" confirmLabel="Go" onConfirm={vi.fn()} onClose={vi.fn()} />);
-    const close = screen.getByRole("button", { name: "Close" });
+    const close = screen.getByRole("button", { name: "No, go back" });
     const go = screen.getByRole("button", { name: "Go" });
     await userEvent.tab();
     expect(go).toHaveFocus();
@@ -155,7 +155,7 @@
     const first = screen.getByRole("alertdialog", { name: "First", hidden: true });
     expect(first).toHaveAttribute("inert");
     expect(screen.getByRole("alertdialog", { name: "Second" })).not.toHaveAttribute("inert");
-    expect(screen.getAllByRole("button", { name: "Close" }).at(-1)).toHaveFocus();
+    expect(screen.getAllByRole("button", { name: "No, go back" }).at(-1)).toHaveFocus();
   });
 });
 
````

<!-- file: src/pages/admin/adminSecurity.test.tsx | patch -->
Change `packages/console-web/src/pages/admin/adminSecurity.test.tsx`:

````diff
--- a/packages/console-web/src/pages/admin/adminSecurity.test.tsx
+++ b/packages/console-web/src/pages/admin/adminSecurity.test.tsx
@@ -61,7 +61,7 @@
     await userEvent.type(url, "http://us-1.leaders.example");
     await userEvent.click(submit);
     expect(await within(dialog).findByRole("alert")).toHaveTextContent(
-      "That leader URL is not allowed.",
+      "The console may not call that address.",
     );
 
     await userEvent.clear(url);
@@ -277,7 +277,7 @@
     await userEvent.type(within(form).getByRole("textbox", { name: "Principal" }), "a1");
     await userEvent.click(within(form).getByRole("button", { name: "Add grant" }));
     const alert = await within(form).findByRole("alert");
-    expect(alert).toHaveTextContent("That scope is not valid.");
+    expect(alert).toHaveTextContent("That is not a way to say which leaders.");
     expect(alert.querySelector("i")).toBeNull();
   });
 });
````

<!-- file: src/pages/leader/JobsTab.test.tsx | patch -->
Change `packages/console-web/src/pages/leader/JobsTab.test.tsx`:

````diff
--- a/packages/console-web/src/pages/leader/JobsTab.test.tsx
+++ b/packages/console-web/src/pages/leader/JobsTab.test.tsx
@@ -140,7 +140,7 @@
     const dialog = screen.getByRole("dialog", { name: "Priority of job 22222222" });
     await userEvent.click(within(dialog).getByRole("button", { name: "Set priority" }));
     expect(await within(dialog).findByRole("alert")).toHaveTextContent(
-      "Only queued or leased jobs can be changed.",
+      "Only jobs that are waiting or being worked on can be changed.",
     );
     expect(screen.getByRole("dialog", { name: "Priority of job 22222222" })).toBeInTheDocument();
   });
@@ -179,7 +179,7 @@
       headers: { "Retry-After": "15" },
     });
     expect(await screen.findByRole("alert")).toHaveTextContent(
-      "The leader cannot be reached right now.",
+      "The leader is not answering right now.",
     );
     expect(screen.getByText(/The console cannot reach eu-1/)).toBeInTheDocument();
     expect(screen.getByRole("link", { name: "Pools and followers" })).toBeInTheDocument();
@@ -195,7 +195,7 @@
       );
     await userEvent.click(await screen.findByRole("button", { name: "Retry job 11111111" }));
     expect(await screen.findByRole("alert")).toHaveTextContent(
-      "Only failed or cancelled jobs can be retried.",
+      "Only failed or cancelled jobs can be tried again.",
     );
   });
 
@@ -207,7 +207,7 @@
     const dialog = screen.getByRole("alertdialog", { name: "Cancel job 22222222?" });
     await userEvent.click(within(dialog).getByRole("button", { name: "Cancel job" }));
     expect(await within(dialog).findByRole("alert")).toHaveTextContent(
-      "Only queued or leased jobs can be changed.",
+      "Only jobs that are waiting or being worked on can be changed.",
     );
   });
 
````

<!-- file: src/pages/leader/LocationsTab.test.tsx | patch -->
Change `packages/console-web/src/pages/leader/LocationsTab.test.tsx`:

````diff
--- a/packages/console-web/src/pages/leader/LocationsTab.test.tsx
+++ b/packages/console-web/src/pages/leader/LocationsTab.test.tsx
@@ -205,7 +205,7 @@
       );
     await userEvent.click(await screen.findByRole("button", { name: "Scan now intake" }));
     expect(await screen.findByRole("alert")).toHaveTextContent(
-      "The location is disabled. Enable it first.",
+      "The location is switched off. Switch it on first.",
     );
   });
 
@@ -217,7 +217,7 @@
     const dialog = screen.getByRole("alertdialog");
     await userEvent.click(within(dialog).getByRole("button", { name: "Disable location" }));
     expect(await within(dialog).findByRole("alert")).toHaveTextContent(
-      "The leader cannot be reached right now.",
+      "The leader is not answering right now.",
     );
   });
 
````

- [ ] **Step 2: Run the tests to see them fail**

Run: `npx vitest run src/api/errors.test.ts src/app/session.test.tsx src/components/ActionButton.test.tsx src/pages/leader/JobsTab.test.tsx src/pages/leader/LocationsTab.test.tsx src/pages/admin/adminSecurity.test.tsx`

Expected: FAIL, `Tests  17 failed | 125 passed (142)`: four in `errors.test.ts` (the new sentence test and three titles), one in `session.test.tsx`, four in `ActionButton.test.tsx` (no button named "No, go back"; the old title for an unreachable leader), four in `JobsTab.test.tsx`, two in `LocationsTab.test.tsx` and two in `adminSecurity.test.tsx`, each expecting a new title and getting the old one.

- [ ] **Step 3: Write the implementation**


<!-- file: src/api/errors.ts | patch -->
Change `packages/console-web/src/api/errors.ts`:

````diff
--- a/packages/console-web/src/api/errors.ts
+++ b/packages/console-web/src/api/errors.ts
@@ -13,57 +13,57 @@
 
 export const ERROR_TITLES: Record<string, string> = {
   // The console's own session and requests.
-  unauthenticated: "Your session has ended. Sign in again.",
-  csrf_failed: "This page is out of date. Reload the console, then try again.",
+  unauthenticated: "You are signed out. Sign in again.",
+  csrf_failed: "This page is out of date. Reload it, then try again.",
   forbidden: "Your role does not allow this.",
   actor_not_representable:
-    "Your identity cannot be passed to the leader, so the console will not act for you.",
-  invalid_request: "The request was not accepted as sent.",
-  too_large: "The request is too large (over 64 KiB).",
-  unavailable: "The console is temporarily unavailable. Try again shortly.",
-  internal: "The console hit an unexpected error.",
-  method_not_allowed: "The console does not accept that request.",
-  not_found: "Not found.",
-  conflict: "That conflicts with the current state.",
-  bad_request: "The request was refused.",
-  [BAD_RESPONSE]: "The console's answer could not be read. Reload the console, then try again.",
-  [BAD_PATH]: "The console hit an unexpected error.",
+    "The leader cannot be told who you are, so the console will not act for you.",
+  invalid_request: "The console did not accept that as it was sent.",
+  too_large: "That is too much to send at once (over 64 KiB).",
+  unavailable: "The console is not answering just now. Try again shortly.",
+  internal: "Something went wrong in the console.",
+  method_not_allowed: "The console does not take that kind of request.",
+  not_found: "There is nothing there.",
+  conflict: "That no longer fits how things stand. Refresh, then look again.",
+  bad_request: "The console refused that.",
+  [BAD_RESPONSE]: "The console's answer could not be read. Reload the page, then try again.",
+  [BAD_PATH]: "Something went wrong in the console.",
   [NETWORK_ERROR]: "The console could not be reached. Check your connection.",
   // Added by the proxy (handoff note "Codes the proxy adds").
-  leader_not_found: "This leader is not visible to you.",
-  leader_unreachable: "The leader cannot be reached right now.",
+  leader_not_found: "You cannot see this leader.",
+  leader_unreachable: "The leader is not answering right now.",
   leader_credential_revoked:
     "The leader revoked the console's credential. A console administrator must replace it.",
   leader_credential_unreadable:
-    "The console cannot open its stored credential for this leader. A console administrator must replace it.",
+    "The console cannot open the credential it holds for this leader. A console administrator must replace it.",
   leader_credential_rejected: "The leader does not accept the console's credential.",
   bad_gateway:
-    "The leader's answer could not be used. If this was an action, check whether it happened before repeating it.",
-  leader_disabled: "This leader is disabled in the console.",
+    "The leader's answer could not be used. If you were changing something, check whether it happened before you try again.",
+  leader_disabled: "This leader is switched off in the console.",
   // Console administration.
   exists: "That already exists.",
   last_admin: "The last console administrator cannot be removed.",
-  invalid_scope: "That scope is not valid.",
+  invalid_scope: "That is not a way to say which leaders.",
   invalid_labels: "Those labels are not valid.",
   invalid_credential: "That is not a console credential.",
   invalid_name: "That name is not valid.",
-  invalid_url: "That leader URL is not allowed.",
-  invalid_principal: "That principal is not valid.",
-  credential_required: "A new URL needs the credential for that URL too.",
-  use_rotate: "Replace a credential with Rotate credential.",
-  unknown_provider: "That sign-in provider is not offered.",
+  invalid_url: "The console may not call that address.",
+  invalid_principal: "That is not a group, an address or a domain the console can use.",
+  credential_required: "A new address needs the credential for that address too.",
+  use_rotate: "To change only the credential, use Replace credential.",
+  unknown_provider: "That way of signing in is not offered here.",
   // The leader's own action errors (passed through with their codes).
-  not_retryable: "Only failed or cancelled jobs can be retried.",
-  not_open: "Only queued or leased jobs can be changed.",
-  already_open: "The recording already has a queued or leased job.",
-  already_completed: "This version of the recording was already transcribed.",
-  not_consented: "The recording is not consented or is no longer present.",
-  recording_changed: "The recording changed since the job was made.",
-  disabled: "The location is disabled. Enable it first.",
+  not_retryable: "Only failed or cancelled jobs can be tried again.",
+  not_open: "Only jobs that are waiting or being worked on can be changed.",
+  already_open: "This recording already has a job waiting or being worked on.",
+  already_completed: "This version of the recording already has a transcript.",
+  not_consented: "The recording is not consented, or is no longer there.",
+  recording_changed: "The recording changed after the job was made.",
+  disabled: "The location is switched off. Switch it on first.",
   overlaps: "That location overlaps another location.",
   root_unavailable: "The leader cannot use that folder.",
   invalid_root: "That folder is not valid on the leader.",
-  rate_limited: "Too many requests. Wait, then try again.",
+  rate_limited: "Too many requests. Wait a little, then try again.",
 };
 
 function titleForStatus(status: number): string {
@@ -72,7 +72,7 @@
   if (status === 409) return ERROR_TITLES.conflict as string;
   if (status === 422) return ERROR_TITLES.invalid_request as string;
   if (status === 429) return ERROR_TITLES.rate_limited as string;
-  if (status >= 500) return "The leader or the console failed to answer.";
+  if (status >= 500) return "The leader or the console did not answer.";
   return ERROR_TITLES.bad_request as string;
 }
 
@@ -86,6 +86,6 @@
     if (error.retryAfter !== null) parts.push(`Try again in ${error.retryAfter} seconds.`);
     return { title, detail: parts.length > 0 ? parts.join(" ") : null };
   }
-  if (isAbort(error)) return { title: "The request was cancelled.", detail: null };
+  if (isAbort(error)) return { title: "That request was stopped.", detail: null };
   return { title: ERROR_TITLES.internal as string, detail: null };
 }
````

<!-- file: src/components/ConfirmDialog.tsx | patch -->
Change `packages/console-web/src/components/ConfirmDialog.tsx`:

````diff
--- a/packages/console-web/src/components/ConfirmDialog.tsx
+++ b/packages/console-web/src/components/ConfirmDialog.tsx
@@ -5,7 +5,8 @@
 
 /**
  * Asks before a destructive action; shows the action's error in place if it fails. The safe
- * choice comes first, so the dialog puts focus on it and a stray Enter never confirms.
+ * choice ("No, go back") comes first, so the dialog puts focus on it and a stray Enter
+ * never confirms.
  *
  * The close and the action's result belong to this dialog instance (useDialogAction). While
  * the action runs the confirm button is aria-disabled, not disabled, so focus stays put and a
@@ -33,7 +34,7 @@
       {action.error !== null && <ErrorPanel error={action.error} />}
       <div className="dialog-buttons">
         <button type="button" className="button" onClick={action.close}>
-          Close
+          No, go back
         </button>
         <button
           type="button"
````

- [ ] **Step 4: Run the unit tests to see them pass**

Run: `npm test`

Expected: PASS. **385 tests pass** (384 + 1 (the sentence test in `errors.test.ts`)).

- [ ] **Step 5: Update the end-to-end tests**

Four specs name one of these strings: the confirmation's safe button in `a11y.spec.ts` and
`drilldown.spec.ts`, and an error title in `drilldown.spec.ts`, `tokens.spec.ts` and
`admin.spec.ts`.

<!-- file: e2e/tests/a11y.spec.ts | patch -->
Change `packages/console-web/e2e/tests/a11y.spec.ts`:

````diff
--- a/packages/console-web/e2e/tests/a11y.spec.ts
+++ b/packages/console-web/e2e/tests/a11y.spec.ts
@@ -80,7 +80,7 @@
         .first()
         .click();
       await expectAccessible(page, `cancel job confirm dialog (${theme})`);
-      await page.getByRole("button", { name: "Close" }).click();
+      await page.getByRole("button", { name: "No, go back" }).click();
       await page
         .getByRole("button", { name: /^Priority of job / })
         .first()
````

<!-- file: e2e/tests/admin.spec.ts | patch -->
Change `packages/console-web/e2e/tests/admin.spec.ts`:

````diff
--- a/packages/console-web/e2e/tests/admin.spec.ts
+++ b/packages/console-web/e2e/tests/admin.spec.ts
@@ -79,7 +79,7 @@
   await dialog.getByRole("textbox", { name: "Address (https://)" }).fill("https://localhost");
   await dialog.getByLabel("Console credential").fill(CREDENTIAL);
   await dialog.getByRole("button", { name: "Add leader" }).click();
-  await expect(dialog.getByRole("alert")).toContainText("That leader URL is not allowed.");
+  await expect(dialog.getByRole("alert")).toContainText("The console may not call that address.");
 });
 
 test("the last console administrator cannot be removed", async ({ page }) => {
````

<!-- file: e2e/tests/drilldown.spec.ts | patch -->
Change `packages/console-web/e2e/tests/drilldown.spec.ts`:

````diff
--- a/packages/console-web/e2e/tests/drilldown.spec.ts
+++ b/packages/console-web/e2e/tests/drilldown.spec.ts
@@ -31,7 +31,7 @@
   await page.keyboard.press("Enter");
   const dialog = page.getByRole("alertdialog", { name: /^Cancel job \w{8}\?$/ });
   await expect(dialog).toBeVisible();
-  await expect(dialog.getByRole("button", { name: "Close" })).toBeFocused();
+  await expect(dialog.getByRole("button", { name: "No, go back" })).toBeFocused();
   await page.keyboard.press("Tab");
   await expect(dialog.getByRole("button", { name: "Cancel job" })).toBeFocused();
   await page.keyboard.press("Enter");
@@ -152,7 +152,7 @@
   await signIn(page, "operator", "/leaders/us-1/jobs");
   await setLeaderMode(request, "us-1", "down");
   await page.getByRole("button", { name: "Refresh" }).click();
-  await expect(page.getByRole("alert")).toContainText("The leader cannot be reached right now.");
+  await expect(page.getByRole("alert")).toContainText("The leader is not answering right now.");
   await expect(page.getByRole("link", { name: "Locations" })).toBeVisible();
 
   await page.getByRole("navigation", { name: "Console" }).getByRole("link", { name: "Fleet" }).click();
````

<!-- file: e2e/tests/tokens.spec.ts | patch -->
Change `packages/console-web/e2e/tests/tokens.spec.ts`:

````diff
--- a/packages/console-web/e2e/tests/tokens.spec.ts
+++ b/packages/console-web/e2e/tests/tokens.spec.ts
@@ -215,7 +215,7 @@
   for (let i = 0; i < 5; i += 1) await page.keyboard.press("Escape");
   release();
   await expect(form).toBeVisible();
-  await expect(form.getByText("The leader cannot be reached right now.")).toBeVisible();
+  await expect(form.getByText("The leader is not answering right now.")).toBeVisible();
   await expect(form.getByRole("button", { name: "Create token" })).not.toHaveAttribute("aria-disabled", "true");
 
   fail = false;
````

- [ ] **Step 6: Type-check, lint, build and run the end-to-end tests**

Run, in `packages/console-web`:

```bash
npm run typecheck
npm run lint
npm test
npm run build
npx playwright test e2e/tests/a11y.spec.ts e2e/tests/drilldown.spec.ts e2e/tests/tokens.spec.ts e2e/tests/admin.spec.ts e2e/tests/dialogs.spec.ts --retries=0
```

Expected: `tsc` prints nothing. ESLint prints nothing (0 warnings). Vitest: **385 tests pass**, none
fail. The build ends with `dist/ ok: index.html and 3 hashed assets`. Playwright: every test in the named specs passes, with no retry.

If an end-to-end test fails, read what it was checking before changing it: the page is wrong
far more often than the test. Nothing may be left listening on ports 8900 or 8901 afterwards.

- [ ] **Step 7: Commit**

```bash
git add packages/console-web/src/api/errors.ts packages/console-web/src/components/ConfirmDialog.tsx packages/console-web/src/api/errors.test.ts packages/console-web/src/app/session.test.tsx packages/console-web/src/components/ActionButton.test.tsx packages/console-web/src/pages/admin/adminSecurity.test.tsx packages/console-web/src/pages/leader/JobsTab.test.tsx packages/console-web/src/pages/leader/LocationsTab.test.tsx packages/console-web/e2e/tests/a11y.spec.ts packages/console-web/e2e/tests/admin.spec.ts packages/console-web/e2e/tests/drilldown.spec.ts packages/console-web/e2e/tests/tokens.spec.ts
git commit -m "Console web app: error titles in the console's own words; confirmations offer No, go back

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

### Task 2: A leader's page: header, role line, and when a list was loaded

**Files:**
- Modify: `packages/console-web/src/components/ActionButton.tsx`
- Modify: `packages/console-web/src/pages/leader/LeaderPage.tsx`
- Modify: `packages/console-web/src/pages/leader/common.tsx`
- Modify: `packages/console-web/src/styles.css`
- Create: `packages/console-web/src/styles/detail.css`
- Test: `packages/console-web/src/pages/leader/JobsTab.test.tsx`
- Test: `packages/console-web/src/pages/leader/LeaderPage.test.tsx`
- Test: `packages/console-web/e2e/tests/drilldown.spec.ts`

**Interfaces:**
- Consumes: R1's classes (`.page-head`, `.page-title`, `.page-sub`, `.labels`, `.breadcrumb`, `.notice`, `.tab-list`, `.tab-link`, `.tab-panel`, `.status-pill`); `HealthBadge`; `labelPairs`, `formatTime` from `src/lib/format.ts`.
- Produces: `src/styles/detail.css` with `.toolbar`, `.refresh`, `.refresh-time`, `.job-state`, `.job-state-busy|bad|quiet`, `.token-row`, `.token-field`, `.token-status`, `.fact-row` (Tasks 3 and 5 use them); `RefreshButton({ read })` now renders `<span class="refresh">` holding "Loaded at {time}" and the Refresh button; `ActionButton` accepts `primary?: boolean` (a danger button stays danger).

The top of `docs/superpowers/design/LeaderJobs.dc.html`: the leader's labels beside its name,
and a line that says who the person is here and what that switches off, in three variants.
The health notes get the console's words.

`RefreshButton` gains "Loaded at {time}" beside it, outside any live region: a leader's lists
are never read on a timer, so a person should see how old the list is.

`detail.css` is written whole here, including what Tasks 3 and 5 use.

- [ ] **Step 1: Write the failing tests**


<!-- file: src/pages/leader/JobsTab.test.tsx | patch -->
Change `packages/console-web/src/pages/leader/JobsTab.test.tsx`:

````diff
--- a/packages/console-web/src/pages/leader/JobsTab.test.tsx
+++ b/packages/console-web/src/pages/leader/JobsTab.test.tsx
@@ -181,7 +181,7 @@
     expect(await screen.findByRole("alert")).toHaveTextContent(
       "The leader is not answering right now.",
     );
-    expect(screen.getByText(/The console cannot reach eu-1/)).toBeInTheDocument();
+    expect(screen.getByText(/eu-1 is not answering, so nothing here can be read or changed/)).toBeInTheDocument();
     expect(screen.getByRole("link", { name: "Pools and followers" })).toBeInTheDocument();
     expect(screen.getByRole("combobox", { name: "State" })).toBeEnabled();
   });
````

<!-- file: src/pages/leader/LeaderPage.test.tsx | patch -->
Change `packages/console-web/src/pages/leader/LeaderPage.test.tsx`:

````diff
--- a/packages/console-web/src/pages/leader/LeaderPage.test.tsx
+++ b/packages/console-web/src/pages/leader/LeaderPage.test.tsx
@@ -33,10 +33,11 @@
       await screen.findByRole("heading", { level: 1, name: "eu-1" }),
     ).toBeInTheDocument();
     expect(
-      await screen.findByText(
-        /Actions that need a higher role are shown disabled/,
-      ),
-    ).toHaveTextContent("Your role on eu-1: operator.");
+      await screen.findByText(/What needs an admin is shown, but switched off/),
+    ).toHaveTextContent("You are an operator here. What needs an admin is shown, but switched off.");
+    expect(
+      within(screen.getByRole("main")).getByRole("list", { name: "Labels" }),
+    ).toHaveTextContent("env=prodregion=eu");
     const tabs = screen.getByRole("navigation", { name: "eu-1 sections" });
     const pools = within(tabs).getByRole("link", {
       name: "Pools and followers",
@@ -48,7 +49,7 @@
   it("says a leader the person cannot see is not visible to them", async () => {
     renderApp("/leaders/secret-1/pools");
     expect(
-      await screen.findByText(/This leader is not visible to you/),
+      await screen.findByText(/You cannot see this leader/),
     ).toBeInTheDocument();
   });
 
@@ -63,6 +64,16 @@
       name: "eu-1",
     });
     await waitFor(() => expect(heading).toHaveFocus());
+  });
+
+  it("tells each role what is switched off for it", async () => {
+    renderApp("/leaders/eu-1/pools", { fleet: [leader({ role: "viewer" })] }).on(
+      "GET /api/leaders/eu-1/followers",
+      reply(200, []),
+    );
+    expect(await screen.findByText(/You are a/)).toHaveTextContent(
+      "You are a viewer here. What needs an operator or an admin is shown, but switched off.",
+    );
   });
 
   it("redirects a bare leader URL to its first tab", async () => {
@@ -86,8 +97,9 @@
       reply(503, { code: "leader_unreachable", message: "down" }),
     );
     expect(
-      await screen.findByText(/The console cannot reach eu-1/),
-    ).toHaveTextContent("The last successful poll was at");
+      await screen.findByText(/eu-1 is not answering, so nothing here can be read or changed/),
+    ).toHaveTextContent("It last answered at");
+    expect(within(screen.getByRole("main")).getByText("Not answering")).toBeInTheDocument();
     expect(
       screen.getByRole("heading", { level: 1, name: "eu-1" }),
     ).toBeInTheDocument();
````

- [ ] **Step 2: Run the tests to see them fail**

Run: `npx vitest run src/pages/leader/LeaderPage.test.tsx src/pages/leader/JobsTab.test.tsx`

Expected: FAIL, `Tests  5 failed | 23 passed (28)`: four in `LeaderPage.test.tsx` (no text "What needs an admin is shown, but switched off", "You cannot see this leader", "eu-1 is not answering, so nothing here can be read or changed", "You are a") and one in `JobsTab.test.tsx` (the same health note).

- [ ] **Step 3: Write the implementation**


<!-- file: src/components/ActionButton.tsx | patch -->
Change `packages/console-web/src/components/ActionButton.tsx`:

````diff
--- a/packages/console-web/src/components/ActionButton.tsx
+++ b/packages/console-web/src/components/ActionButton.tsx
@@ -13,6 +13,7 @@
   action,
   onClick,
   danger = false,
+  primary = false,
   busy = false,
   name,
   children,
@@ -22,8 +23,13 @@
   action: LeaderAction;
   onClick: () => void;
   danger?: boolean;
+  /** The one action a row is there for (Try again on a failed job). */
+  primary?: boolean;
   busy?: boolean;
-  /** The full accessible name when the visible text needs its row for context ("Retry job 1a2b3c4d"). */
+  /**
+   * The full accessible name when the visible text needs its row for context ("Cancel job
+   * 1a2b3c4d"). It must contain the visible text, word for word, so speech input finds it.
+   */
   name?: string;
   children: ReactNode;
 }) {
@@ -33,7 +39,7 @@
     <span className="action">
       <button
         type="button"
-        className={danger ? "button button-danger" : "button"}
+        className={danger ? "button button-danger" : primary ? "button button-primary" : "button"}
         disabled={!allowed}
         aria-disabled={busy || undefined}
         aria-label={name}
````

<!-- file: src/pages/leader/LeaderPage.tsx | patch -->
Change `packages/console-web/src/pages/leader/LeaderPage.tsx`:

````diff
--- a/packages/console-web/src/pages/leader/LeaderPage.tsx
+++ b/packages/console-web/src/pages/leader/LeaderPage.tsx
@@ -1,10 +1,10 @@
-import type { FleetLeader } from "../../api/types";
+import type { FleetLeader, Role } from "../../api/types";
 import { useFleet } from "../../app/fleet";
 import { Link } from "../../app/router";
 import { usePageTitle } from "../../app/usePageTitle";
 import { ErrorPanel } from "../../components/ErrorPanel";
 import { HealthBadge } from "../../components/HealthBadge";
-import { formatTime } from "../../lib/format";
+import { formatTime, labelPairs } from "../../lib/format";
 import { NotFoundPage } from "../NotFoundPage";
 import { ConsentTab } from "./ConsentTab";
 import { JobsTab } from "./JobsTab";
@@ -28,16 +28,21 @@
   }
 }
 
+/** What the person's role leaves switched off on this leader. */
+const ROLE_NOTE: Record<Role, string> = {
+  viewer: "What needs an operator or an admin is shown, but switched off.",
+  operator: "What needs an admin is shown, but switched off.",
+  admin: "Nothing here is switched off for you.",
+};
+
 function HealthNote({ leader }: { leader: FleetLeader }) {
   if (leader.health === "reachable") return null;
-  const since = leader.last_success_at
-    ? ` The last successful poll was at ${formatTime(leader.last_success_at)}.`
-    : "";
+  const since = leader.last_success_at ? ` It last answered at ${formatTime(leader.last_success_at)}.` : "";
   const text: Record<string, string> = {
-    unreachable: `The console cannot reach ${leader.name}; reads and actions will fail until it answers.${since}`,
+    unreachable: `${leader.name} is not answering, so nothing here can be read or changed until it does.${since}`,
     credential_revoked: `${leader.name} revoked the console's credential. A console administrator must replace it.${since}`,
-    disabled: `${leader.name} is disabled in the console; reads and actions are refused.${since}`,
-    pending: `${leader.name} has not answered a poll yet.`,
+    disabled: `${leader.name} is switched off in the console, so nothing is asked of it.${since}`,
+    pending: `${leader.name} has not answered a check yet.`,
   };
   return <p className="notice">{text[leader.health] ?? `Health: ${leader.health}.`}</p>;
 }
@@ -62,25 +67,35 @@
       <>
         <h1>{name}</h1>
         <p>
-          This leader is not visible to you: it is not registered, or you hold no role on it.{" "}
+          You cannot see this leader: the console does not know it, or you have no role on it.{" "}
           <Link to="/">Back to the fleet</Link>.
         </p>
       </>
     );
   }
 
+  const pairs = labelPairs(leader.labels);
   return (
     <>
       <p className="breadcrumb">
         <Link to="/">Fleet</Link> / {leader.name}
       </p>
       <div className="page-head">
-        <h1>{leader.name}</h1>
+        <div className="page-title">
+          <h1>{leader.name}</h1>
+          {pairs.length > 0 && (
+            <ul className="labels" aria-label="Labels">
+              {pairs.map((pair) => (
+                <li key={pair}>{pair}</li>
+              ))}
+            </ul>
+          )}
+        </div>
         <HealthBadge leader={leader} />
       </div>
-      <p className="muted">
-        Your role on {leader.name}: <strong>{leader.role}</strong>. Actions that need a higher role are shown
-        disabled.
+      <p className="page-sub">
+        You are {leader.role === "viewer" ? "a" : "an"} <strong>{leader.role}</strong> here.{" "}
+        {ROLE_NOTE[leader.role]}
       </p>
       <HealthNote leader={leader} />
       <nav aria-label={`${leader.name} sections`}>
````

<!-- file: src/pages/leader/common.tsx | patch -->
Change `packages/console-web/src/pages/leader/common.tsx`:

````diff
--- a/packages/console-web/src/pages/leader/common.tsx
+++ b/packages/console-web/src/pages/leader/common.tsx
@@ -3,6 +3,7 @@
 import type { FleetLeader } from "../../api/types";
 import { usePoll, type PollState } from "../../app/usePoll";
 import { ErrorPanel } from "../../components/ErrorPanel";
+import { formatTime } from "../../lib/format";
 
 /** What every drill-down tab gets. */
 export interface TabProps {
@@ -53,11 +54,20 @@
   );
 }
 
+/**
+ * Reads the list again, and says when it was last read: a leader's lists are not on a timer,
+ * so the person should see how old they are. The time is not in a live region.
+ */
 export function RefreshButton({ read }: { read: PollState<unknown> }) {
   return (
-    <button type="button" className="button" onClick={read.refresh} disabled={read.loading}>
-      Refresh
-    </button>
+    <span className="refresh">
+      {read.updatedAt !== null && (
+        <span className="refresh-time">Loaded at {formatTime(new Date(read.updatedAt).toISOString())}</span>
+      )}
+      <button type="button" className="button" onClick={read.refresh} disabled={read.loading}>
+        Refresh
+      </button>
+    </span>
   );
 }
 
````

<!-- file: src/styles.css | replace -->
Replace the whole of `packages/console-web/src/styles.css` with:

````css
/* SwarmScribe console styles. Served as one hashed file under the console's CSP
   (style-src 'self'): no inline styles anywhere, no fonts or images from elsewhere.
   Order matters: tokens first, then each layer builds on the one before.
   Spec: docs/superpowers/specs/2026-10-04-console-redesign.md */
@import "./styles/tokens.css";
@import "./styles/base.css";
@import "./styles/controls.css";
@import "./styles/surfaces.css";
@import "./styles/shell.css";
@import "./styles/fleet.css";
@import "./styles/signin.css";
@import "./styles/detail.css";
````

<!-- file: src/styles/detail.css | create -->
Create `packages/console-web/src/styles/detail.css`:

````css
/* A leader's own pages and Administration: the toolbar above a list, job states, the
   one-time join token.
   Mockups: docs/superpowers/design/LeaderJobs.dc.html, TokenDialog.dc.html, AdminLeaders.dc.html */

/* ---- The bar above a list: filters on the left, "Loaded at" and Refresh on the right ---- */

.toolbar,
.refresh {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--s-3);
}

.refresh-time {
  font-size: var(--fs-fine);
  color: var(--muted);
  white-space: nowrap;
}

/* ---- A job's state: words first, weight and colour second ---- */

.job-state {
  font-weight: 600;
}

.job-state-busy {
  color: var(--accent-text);
}

.job-state-bad {
  color: var(--bad);
}

.job-state-quiet {
  color: var(--muted);
}

/* ---- The join token, shown once ---- */

.token-row {
  display: flex;
  gap: var(--s-2);
  margin-top: 6px;
}

.token-field {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 48px;
  border: 2px solid var(--text);
  border-radius: var(--r-sm);
  font-size: var(--fs-body);
}

.token-row .button {
  min-height: 48px;
}

.token-status {
  min-height: 1.4rem;
  margin: 10px 0 0;
  font-size: var(--fs-small);
  font-weight: 600;
  color: var(--accent-text);
}

/* An empty status line stays in the page (a live region that appears from nothing is not
   read out reliably); it just takes no room. */
.token-status:empty {
  min-height: 0;
  margin: 0;
}

.fact-row {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--s-5);
  margin: var(--s-6) 0 0;
  padding-top: var(--s-5);
  border-top: 1px solid var(--line);
}

.fact-row dt {
  font-size: var(--fs-fine);
  color: var(--muted);
}

.fact-row dd {
  margin: 0;
  font-weight: 600;
  overflow-wrap: anywhere;
}

@media (max-width: 480px) {
  .fact-row {
    grid-template-columns: minmax(0, 1fr);
    gap: var(--s-2);
  }

  .token-row {
    flex-direction: column;
  }
}
````

- [ ] **Step 4: Run the unit tests to see them pass**

Run: `npm test`

Expected: PASS. **386 tests pass** (385 + 1 (the role line for a viewer)).

- [ ] **Step 5: Update the end-to-end tests**

One new test in `drilldown.spec.ts`: the role line, the labels and the rail's current leader.

<!-- file: e2e/tests/drilldown.spec.ts | patch -->
Change `packages/console-web/e2e/tests/drilldown.spec.ts`:

````diff
--- a/packages/console-web/e2e/tests/drilldown.spec.ts
+++ b/packages/console-web/e2e/tests/drilldown.spec.ts
@@ -176,6 +176,22 @@
   await expect(page.getByRole("heading", { level: 2, name: "Consent report" })).toBeVisible();
 });
 
+test("a leader's page says who the person is here and what its labels are", async ({ page }) => {
+  await signIn(page, "operator", "/leaders/eu-1/pools");
+  const main = page.getByRole("main");
+  await expect(main.getByText(/^You are an operator here\./)).toHaveText(
+    "You are an operator here. What needs an admin is shown, but switched off.",
+  );
+  await expect(main.getByRole("list", { name: "Labels" }).getByRole("listitem")).toHaveText([
+    "env=prod",
+    "region=eu",
+  ]);
+  await expect(main.getByText("Answering", { exact: true })).toBeVisible();
+  await expect(
+    page.getByRole("navigation", { name: "Console" }).getByRole("link", { name: "eu-1", exact: true }),
+  ).toHaveAttribute("aria-current", "page");
+});
+
 test("switching tabs keeps focus on the tab link", async ({ page }) => {
   await signIn(page, "viewer", "/leaders/eu-1/pools");
   const jobs = page.getByRole("navigation", { name: "eu-1 sections" }).getByRole("link", { name: "Jobs" });
````

- [ ] **Step 6: Type-check, lint, build and run the end-to-end tests**

Run, in `packages/console-web`:

```bash
npm run typecheck
npm run lint
npm test
npm run build
npx playwright test e2e/tests/drilldown.spec.ts e2e/tests/a11y.spec.ts e2e/tests/overview.spec.ts --retries=0
```

Expected: `tsc` prints nothing. ESLint prints nothing (0 warnings). Vitest: **386 tests pass**, none
fail. The build ends with `dist/ ok: index.html and 3 hashed assets`. Playwright: every test in the named specs passes, with no retry.

If an end-to-end test fails, read what it was checking before changing it: the page is wrong
far more often than the test. Nothing may be left listening on ports 8900 or 8901 afterwards.

- [ ] **Step 7: Commit**

```bash
git add packages/console-web/src/components/ActionButton.tsx packages/console-web/src/pages/leader/LeaderPage.tsx packages/console-web/src/pages/leader/common.tsx packages/console-web/src/styles.css packages/console-web/src/styles/detail.css packages/console-web/src/pages/leader/JobsTab.test.tsx packages/console-web/src/pages/leader/LeaderPage.test.tsx packages/console-web/e2e/tests/drilldown.spec.ts
git commit -m "Console web app: leader page header with labels and a plain role line; lists say when they were loaded

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

### Task 3: The jobs tab: state pills, states in words, Try again

**Files:**
- Modify: `packages/console-web/src/pages/leader/JobsTab.tsx`
- Test: `packages/console-web/src/pages/leader/JobsTab.test.tsx`
- Test: `packages/console-web/e2e/tests/a11y.spec.ts`
- Test: `packages/console-web/e2e/tests/drilldown.spec.ts`

**Interfaces:**
- Consumes: Task 2's `.toolbar`, `.job-state*`, `RefreshButton`, `ActionButton`'s `primary`; R1's `.pill-group`, `.pill`, `.pill-count`, `.section-head`, `.cell-note`; `formatCount`.
- Produces: `STATE_PILLS: { state: JobState; label: string }[]`, `jobStateText(job: Pick<JobOut, "state" | "leased_by">): { label: string; tone: "plain" | "busy" | "bad" | "quiet" }` and `jobNote(job: JobOut): string` exported from `src/pages/leader/JobsTab.tsx`; on the page: a group named "Show jobs that are" of toggle buttons, and buttons named "Try again: job {id}".

`docs/superpowers/design/LeaderJobs.dc.html`.

- The State select becomes pills. The filter still lives in the address and is still sent to
  the leader as `state=`. Counts come from the leader's last check and are left off while a
  location filter is on (they are per leader, not per location).
- The table: Job, State, Recording, Pool, Priority, Tries, Queued, Actions. State is words.
  The recording's key is on the first line and a note beneath says where it came from and,
  if anything happened, what.
- "Retry" becomes "Try again", the primary button on a failed job and a ghost button on a
  cancelled one.

- [ ] **Step 1: Write the failing tests**


<!-- file: src/pages/leader/JobsTab.test.tsx | patch -->
Change `packages/console-web/src/pages/leader/JobsTab.test.tsx`:

````diff
--- a/packages/console-web/src/pages/leader/JobsTab.test.tsx
+++ b/packages/console-web/src/pages/leader/JobsTab.test.tsx
@@ -1,10 +1,11 @@
-import { screen, waitFor, within } from "@testing-library/react";
+import { cleanup, screen, waitFor, within } from "@testing-library/react";
 import userEvent from "@testing-library/user-event";
 import { describe, expect, it } from "vitest";
-import type { JobOut } from "../../api/types";
+import type { JobOut, LeaderStatus } from "../../api/types";
 import { fail, reply } from "../../test/fetchMock";
 import { leader } from "../../test/fixtures";
 import { renderApp } from "../../test/renderApp";
+import { STATE_PILLS, jobNote, jobStateText } from "./JobsTab";
 
 const JOB_FAILED: JobOut = {
   id: "11111111-1111-4111-8111-111111111111",
@@ -33,13 +34,183 @@
 const PRIORITY = `POST /api/leaders/eu-1/jobs/${JOB_QUEUED.id}/priority`;
 const CANCEL = `POST /api/leaders/eu-1/jobs/${JOB_QUEUED.id}/cancel`;
 
+describe("job words", () => {
+  it("says each state in the console's words and keeps an unknown one as sent", () => {
+    expect(jobStateText({ state: "queued", leased_by: null })).toEqual({ label: "Waiting", tone: "plain" });
+    expect(
+      jobStateText({ state: "leased", leased_by: "77777777-7777-4777-8777-777777777777" }),
+    ).toEqual({ label: "With follower 77777777", tone: "busy" });
+    expect(jobStateText({ state: "leased", leased_by: null })).toEqual({
+      label: "Being worked on",
+      tone: "busy",
+    });
+    expect(jobStateText({ state: "completed", leased_by: null }).label).toBe("Finished");
+    expect(jobStateText({ state: "failed", leased_by: null })).toEqual({ label: "Failed", tone: "bad" });
+    expect(jobStateText({ state: "cancelled", leased_by: null }).label).toBe("Cancelled");
+    expect(jobStateText({ state: "paused", leased_by: null })).toEqual({ label: "paused", tone: "plain" });
+  });
+
+  it("notes where a recording came from, then what happened to the job", () => {
+    expect(jobNote(JOB_QUEUED)).toBe("From intake");
+    expect(jobNote(JOB_FAILED)).toBe("From intake · the engine stopped");
+    expect(jobNote({ ...JOB_QUEUED, state: "cancelled", cancelled_by: "sam@example.org" })).toBe(
+      "From intake · Cancelled by sam@example.org",
+    );
+    expect(jobNote({ ...JOB_QUEUED, state: "completed", no_speech: true })).toBe(
+      "From intake · No speech found",
+    );
+    expect(jobNote({ ...JOB_QUEUED, state: "completed", no_speech: false })).toBe("From intake");
+  });
+
+  it("has a pill for every state the leader knows", () => {
+    expect(STATE_PILLS.map((pill) => pill.state).sort()).toEqual(
+      ["cancelled", "completed", "failed", "leased", "queued"].sort(),
+    );
+  });
+});
+
 describe("jobs tab", () => {
+  it("shows each job's state, recording and tries in a table with the columns of the design", async () => {
+    const leased = {
+      ...JOB_QUEUED,
+      id: "44444444-4444-4444-8444-444444444444",
+      state: "leased",
+      attempts: 1,
+      leased_by: "77777777-7777-4777-8777-777777777777",
+    };
+    renderApp("/leaders/eu-1/jobs").on(JOBS, reply(200, [JOB_FAILED, JOB_QUEUED, leased]));
+    const region = await screen.findByRole("region", { name: "Job list" });
+    expect(
+      within(region)
+        .getAllByRole("columnheader")
+        .map((th) => th.textContent),
+    ).toEqual(["Job", "State", "Recording", "Pool", "Priority", "Tries", "Queued", "Actions"]);
+    const failed = within(region).getByRole("row", { name: /11111111/ });
+    expect(
+      within(failed)
+        .getAllByRole("cell")
+        .slice(0, 5)
+        .map((td) => td.textContent),
+    ).toEqual(["Failed", "incoming/a.wavFrom intake · the engine stopped", "default", "0", "3 of 3"]);
+    expect(within(failed).getByText("Failed")).toHaveClass("job-state-bad");
+    expect(within(region).getByRole("row", { name: /22222222/ })).toHaveTextContent("Waiting");
+    expect(within(region).getByRole("row", { name: /44444444/ })).toHaveTextContent("With follower 77777777");
+    // The one thing a failed job's row is for is the primary button.
+    expect(within(failed).getByRole("button", { name: "Try again: job 11111111" })).toHaveClass(
+      "button-primary",
+    );
+    expect(within(failed).getByRole("button", { name: "Try again: job 11111111" })).toHaveTextContent(
+      /^Try again$/,
+    );
+  });
+
+  it("counts each state on its pill from the leader's last check", async () => {
+    // The fixture leader's last check: 3 waiting, 1 being worked on, 10 finished, 1 failed, 0 cancelled.
+    renderApp("/leaders/eu-1/jobs").on(JOBS, reply(200, [JOB_FAILED]));
+    const pills = within(await screen.findByRole("group", { name: "Show jobs that are" }));
+    expect(pills.getAllByRole("button").map((pill) => pill.textContent)).toEqual([
+      "All 15",
+      "Waiting 3",
+      "Being worked on 1",
+      "Failed 1",
+      "Finished 10",
+      "Cancelled 0",
+    ]);
+    expect(pills.getByRole("button", { name: "All 15" })).toHaveAttribute("aria-pressed", "true");
+    expect(pills.getByRole("button", { name: "Failed 1" })).toHaveAttribute("aria-pressed", "false");
+  });
+
+  it("leaves the counts off when there is no check to count from, or a location filter is on", async () => {
+    renderApp("/leaders/eu-1/jobs?location=intake").on(
+      "GET /api/leaders/eu-1/jobs?location=intake&limit=100",
+      reply(200, [JOB_FAILED]),
+    );
+    const pills = within(await screen.findByRole("group", { name: "Show jobs that are" }));
+    expect(pills.getAllByRole("button").map((pill) => pill.textContent)).toEqual([
+      "All",
+      "Waiting",
+      "Being worked on",
+      "Failed",
+      "Finished",
+      "Cancelled",
+    ]);
+  });
+
+  it("shows no counts for a leader that has never been checked, and 0 for a state its check left out", async () => {
+    const never = leader({ health: "pending", summary: null, snapshot: null });
+    renderApp("/leaders/eu-1/jobs", { fleet: [never] }).on(JOBS, reply(200, [JOB_QUEUED]));
+    const bare = within(await screen.findByRole("group", { name: "Show jobs that are" }));
+    expect(bare.getAllByRole("button").map((pill) => pill.textContent)).toEqual([
+      "All",
+      "Waiting",
+      "Being worked on",
+      "Failed",
+      "Finished",
+      "Cancelled",
+    ]);
+    cleanup();
+
+    const base = leader();
+    const partial = leader({
+      snapshot: {
+        taken_at: base.snapshot?.taken_at ?? "",
+        status: { ...(base.snapshot?.status as LeaderStatus), jobs: { queued: 2 } },
+      },
+    });
+    renderApp("/leaders/eu-1/jobs", { fleet: [partial] }).on(JOBS, reply(200, [JOB_QUEUED]));
+    const counted = within(await screen.findByRole("group", { name: "Show jobs that are" }));
+    expect(counted.getAllByRole("button").map((pill) => pill.textContent)).toEqual([
+      "All 2",
+      "Waiting 2",
+      "Being worked on 0",
+      "Failed 0",
+      "Finished 0",
+      "Cancelled 0",
+    ]);
+  });
+
+  it("clears the state filter when its pill is pressed again, and keeps the location", async () => {
+    const mock = renderApp("/leaders/eu-1/jobs?state=failed&location=intake")
+      .on("GET /api/leaders/eu-1/jobs?state=failed&location=intake&limit=100", reply(200, [JOB_FAILED]))
+      .on("GET /api/leaders/eu-1/jobs?location=intake&limit=100", reply(200, [JOB_FAILED, JOB_QUEUED]));
+    await screen.findByRole("rowheader", { name: "11111111" });
+    const failed = screen.getByRole("button", { name: "Failed" });
+    expect(failed).toHaveAttribute("aria-pressed", "true");
+    expect(screen.getByRole("combobox", { name: "Location" })).toHaveValue("intake");
+    await userEvent.click(failed);
+    await screen.findByRole("rowheader", { name: "22222222" });
+    expect(window.location.search).toBe("?location=intake");
+    expect(failed).toHaveAttribute("aria-pressed", "false");
+    expect(failed).toHaveFocus();
+    expect(mock.callsTo("GET /api/leaders/eu-1/jobs?location=intake&limit=100")).toHaveLength(1);
+  });
+
+  it("shows a state from the address that it has no pill for, so it can be cleared", async () => {
+    renderApp("/leaders/eu-1/jobs?state=paused")
+      .on("GET /api/leaders/eu-1/jobs?state=paused&limit=100", reply(200, []))
+      .on(JOBS, reply(200, [JOB_QUEUED]));
+    const odd = await screen.findByRole("button", { name: "paused" });
+    expect(odd).toHaveAttribute("aria-pressed", "true");
+    expect(await screen.findByText("No jobs match.")).toBeInTheDocument();
+    await userEvent.click(odd);
+    await screen.findByRole("rowheader", { name: "22222222" });
+    expect(window.location.search).toBe("");
+    expect(screen.queryByRole("button", { name: "paused" })).not.toBeInTheDocument();
+  });
+
+  it("says when the list was loaded, outside any live region", async () => {
+    renderApp("/leaders/eu-1/jobs").on(JOBS, reply(200, [JOB_FAILED]));
+    const loaded = await screen.findByText(/^Loaded at /);
+    expect(loaded.closest("[aria-live], [role='status'], [role='alert'], [role='log']")).toBeNull();
+    expect(screen.getByRole("button", { name: "Refresh" })).toBeEnabled();
+  });
+
   it("filters jobs by state through the address and the leader query", async () => {
     const mock = renderApp("/leaders/eu-1/jobs")
       .on(JOBS, reply(200, [JOB_FAILED, JOB_QUEUED]))
       .on("GET /api/leaders/eu-1/jobs?state=failed&limit=100", reply(200, [JOB_FAILED]));
     await screen.findByRole("rowheader", { name: "22222222" });
-    await userEvent.selectOptions(screen.getByRole("combobox", { name: "State" }), "failed");
+    await userEvent.click(screen.getByRole("button", { name: /^Failed/ }));
     await waitFor(() =>
       expect(screen.queryByRole("rowheader", { name: "22222222" })).not.toBeInTheDocument(),
     );
@@ -55,8 +226,8 @@
         reply(200, { ...JOB_FAILED, state: "queued" }),
       )
       .on(CANCEL, reply(200, { ...JOB_QUEUED, state: "cancelled" }));
-    await userEvent.click(await screen.findByRole("button", { name: "Retry job 11111111" }));
-    expect(await screen.findByText("Job 11111111 is queued again.")).toBeInTheDocument();
+    await userEvent.click(await screen.findByRole("button", { name: "Try again: job 11111111" }));
+    expect(await screen.findByText("Job 11111111 is waiting again.")).toBeInTheDocument();
     await userEvent.click(screen.getByRole("button", { name: "Cancel job 22222222" }));
     const dialog = screen.getByRole("alertdialog", { name: "Cancel job 22222222?" });
     expect(mock.callsTo(CANCEL)).toHaveLength(0);
@@ -76,14 +247,14 @@
     renderApp("/leaders/eu-1/jobs").on(JOBS, reply(200, [JOB_FAILED, cancelled, leased, done]));
     await screen.findByRole("rowheader", { name: "55555555" });
     const names = screen.getAllByRole("button").map((b) => b.getAttribute("aria-label"));
-    expect(names).toContain("Retry job 11111111");
-    expect(names).toContain("Retry job 33333333");
+    expect(names).toContain("Try again: job 11111111");
+    expect(names).toContain("Try again: job 33333333");
     expect(names).toContain("Cancel job 44444444");
     expect(names).toContain("Priority of job 44444444");
     expect(names.filter((n) => n?.includes("55555555"))).toEqual([]);
     expect(names).not.toContain("Cancel job 11111111");
     expect(names).not.toContain("Priority of job 11111111");
-    expect(names).not.toContain("Retry job 44444444");
+    expect(names).not.toContain("Try again: job 44444444");
   });
 
   it("sets a job's priority as an integer", async () => {
@@ -150,7 +321,7 @@
       JOBS,
       reply(200, [JOB_FAILED]),
     );
-    const retry = await screen.findByRole("button", { name: "Retry job 11111111" });
+    const retry = await screen.findByRole("button", { name: "Try again: job 11111111" });
     expect(retry).toBeDisabled();
     expect(retry).toHaveAccessibleDescription("needs operator");
   });
@@ -160,8 +331,9 @@
       .on(JOBS, reply(200, []))
       .on("GET /api/leaders/eu-1/jobs?state=failed&limit=100", reply(200, []));
     expect(await screen.findByText("This leader has no jobs.")).toBeInTheDocument();
-    await userEvent.selectOptions(screen.getByRole("combobox", { name: "State" }), "failed");
-    expect(await screen.findByText("No jobs match this filter.")).toBeInTheDocument();
+    await userEvent.click(screen.getByRole("button", { name: /^Failed/ }));
+    expect(await screen.findByText("No jobs match.")).toBeInTheDocument();
+    expect(screen.getByRole("button", { name: /^Failed/ })).toHaveAttribute("aria-pressed", "true");
   });
 
   it("shows a long recording path whole", async () => {
@@ -183,7 +355,7 @@
     );
     expect(screen.getByText(/eu-1 is not answering, so nothing here can be read or changed/)).toBeInTheDocument();
     expect(screen.getByRole("link", { name: "Pools and followers" })).toBeInTheDocument();
-    expect(screen.getByRole("combobox", { name: "State" })).toBeEnabled();
+    expect(screen.getByRole("button", { name: /^Failed/ })).toBeEnabled();
   });
 
   it("passes the leader's own refusal through", async () => {
@@ -193,7 +365,7 @@
         `POST /api/leaders/eu-1/jobs/${JOB_FAILED.id}/retry`,
         fail(409, "not_retryable", "only failed or cancelled jobs can be retried"),
       );
-    await userEvent.click(await screen.findByRole("button", { name: "Retry job 11111111" }));
+    await userEvent.click(await screen.findByRole("button", { name: "Try again: job 11111111" }));
     expect(await screen.findByRole("alert")).toHaveTextContent(
       "Only failed or cancelled jobs can be tried again.",
     );
````

- [ ] **Step 2: Run the tests to see them fail**

Run: `npx vitest run src/pages/leader/JobsTab.test.tsx`

Expected: FAIL, `Tests  16 failed | 12 passed (28)`: `jobStateText`, `jobNote` and `STATE_PILLS` are not exported, there is no group named "Show jobs that are", no button named "Try again: job 11111111", and the old words are on the page.

- [ ] **Step 3: Write the implementation**


<!-- file: src/pages/leader/JobsTab.tsx | patch -->
Change `packages/console-web/src/pages/leader/JobsTab.tsx`:

````diff
--- a/packages/console-web/src/pages/leader/JobsTab.tsx
+++ b/packages/console-web/src/pages/leader/JobsTab.tsx
@@ -8,7 +8,7 @@
 import { ConfirmDialog } from "../../components/ConfirmDialog";
 import { Dialog } from "../../components/Dialog";
 import { ErrorPanel } from "../../components/ErrorPanel";
-import { formatTime } from "../../lib/format";
+import { formatCount, formatTime } from "../../lib/format";
 import {
   ActionNotice,
   ReadState,
@@ -21,6 +21,14 @@
 import { leaderUrl } from "./tabs";
 
 export const JOB_STATES: JobState[] = ["queued", "leased", "completed", "failed", "cancelled"];
+/** The filter pills, in the order a person works through them. The address keeps the leader's word. */
+export const STATE_PILLS: { state: JobState; label: string }[] = [
+  { state: "queued", label: "Waiting" },
+  { state: "leased", label: "Being worked on" },
+  { state: "failed", label: "Failed" },
+  { state: "completed", label: "Finished" },
+  { state: "cancelled", label: "Cancelled" },
+];
 /** The leader accepts 1 to 500; the table shows the newest 100. */
 export const JOB_LIMIT = 100;
 /** The leader's PriorityIn range: a strict integer from -1000 to 1000. */
@@ -105,12 +113,36 @@
   );
 }
 
-function detail(job: JobOut): string {
-  if (job.failure_reason) return job.failure_reason;
-  if (job.cancelled_by) return `Cancelled by ${job.cancelled_by}`;
-  if (job.leased_by) return `Leased by ${shortId(job.leased_by)}`;
-  if (job.no_speech) return "No speech found";
-  return "";
+type StateTone = "plain" | "busy" | "bad" | "quiet";
+
+/** A job's state in the console's words. An unknown state is shown as the leader sent it. */
+export function jobStateText(job: Pick<JobOut, "state" | "leased_by">): { label: string; tone: StateTone } {
+  switch (job.state) {
+    case "queued":
+      return { label: "Waiting", tone: "plain" };
+    case "leased":
+      return {
+        label: job.leased_by ? `With follower ${shortId(job.leased_by)}` : "Being worked on",
+        tone: "busy",
+      };
+    case "completed":
+      return { label: "Finished", tone: "quiet" };
+    case "failed":
+      return { label: "Failed", tone: "bad" };
+    case "cancelled":
+      return { label: "Cancelled", tone: "quiet" };
+    default:
+      return { label: job.state, tone: "plain" };
+  }
+}
+
+/** The line under a recording: where it came from, then what happened to the job, if anything. */
+export function jobNote(job: JobOut): string {
+  const parts = [`From ${job.location}`];
+  if (job.failure_reason) parts.push(job.failure_reason);
+  else if (job.cancelled_by) parts.push(`Cancelled by ${job.cancelled_by}`);
+  else if (job.no_speech) parts.push("No speech found");
+  return parts.join(" · ");
 }
 
 export function JobsTab({ leader }: TabProps) {
@@ -128,6 +160,11 @@
   const rows = useRowFocus(read, setNotice);
   const locations = (leader.snapshot?.status.locations ?? []).map((l) => l.name).sort();
   const filtered = Boolean(state || location);
+  // Counts come from the last check of the leader, so they cover every location and can be
+  // a few seconds behind the list: they are left out while a location filter is on.
+  const counts = location ? null : (leader.snapshot?.status.jobs ?? null);
+  const total = counts === null ? null : Object.values(counts).reduce((sum, n) => sum + n, 0);
+  const known = STATE_PILLS.some((pill) => pill.state === state);
 
   const setFilter = (next: { state?: string | null; location?: string | null }) => {
     const merged = { state, location, ...next };
@@ -137,55 +174,77 @@
   const onRetry = async (job: JobOut) => {
     setNotice(null);
     if (await retry.run(() => api.post(leaderPath(leader.name, `jobs/${job.id}/retry`)))) {
-      rows.done(`Job ${shortId(job.id)} is queued again.`);
+      rows.done(`Job ${shortId(job.id)} is waiting again.`);
     }
   };
 
   return (
     <div {...rows.props}>
-      <div className="filters">
-        <label className="field-inline">
-          State
-          <select
-            value={state ?? ""}
-            onChange={(event) => setFilter({ state: event.target.value || null })}
+      <div className="section-head">
+        <div className="pill-group" role="group" aria-label="Show jobs that are">
+          <button
+            type="button"
+            className="pill"
+            aria-pressed={!state}
+            onClick={() => setFilter({ state: null })}
           >
-            <option value="">All states</option>
-            {state && !JOB_STATES.includes(state as JobState) && (
-              <option value={state}>{state}</option>
-            )}
-            {JOB_STATES.map((s) => (
-              <option key={s} value={s}>
-                {s}
-              </option>
-            ))}
-          </select>
-        </label>
-        <label className="field-inline">
-          Location
-          <select
-            value={location ?? ""}
-            onChange={(event) => setFilter({ location: event.target.value || null })}
-          >
-            <option value="">All locations</option>
-            {location && !locations.includes(location) && (
-              <option value={location}>{location}</option>
-            )}
-            {locations.map((name) => (
-              <option key={name} value={name}>
-                {name}
-              </option>
-            ))}
-          </select>
-        </label>
-        <RefreshButton read={read} />
+            All
+            {total !== null && " "}
+            {total !== null && <span className="pill-count">{formatCount(total)}</span>}
+          </button>
+          {state && !known && (
+            <button
+              type="button"
+              className="pill"
+              aria-pressed="true"
+              onClick={() => setFilter({ state: null })}
+            >
+              {state}
+            </button>
+          )}
+          {STATE_PILLS.map((pill) => (
+            <button
+              key={pill.state}
+              type="button"
+              className="pill"
+              aria-pressed={state === pill.state}
+              onClick={() => setFilter({ state: state === pill.state ? null : pill.state })}
+            >
+              {pill.label}
+              {counts !== null && " "}
+              {counts !== null && (
+                <span className="pill-count">{formatCount(counts[pill.state] ?? 0)}</span>
+              )}
+            </button>
+          ))}
+        </div>
+        <div className="toolbar">
+          <label className="field-inline">
+            Location
+            <select
+              value={location ?? ""}
+              onChange={(event) => setFilter({ location: event.target.value || null })}
+            >
+              <option value="">All locations</option>
+              {location && !locations.includes(location) && (
+                <option value={location}>{location}</option>
+              )}
+              {locations.map((name) => (
+                <option key={name} value={name}>
+                  {name}
+                </option>
+              ))}
+            </select>
+          </label>
+          <RefreshButton read={read} />
+        </div>
       </div>
       <ActionNotice message={notice} />
       {retry.error !== null && <ErrorPanel error={retry.error} />}
       <ReadState read={read} what="jobs">
         {(jobs) =>
           jobs.length === 0 ? (
-            <p>{filtered ? "No jobs match this filter." : "This leader has no jobs."}</p>
+            <p>{filtered ? "No jobs match." : "This leader has no jobs."}</p>
           ) : (
             <>
               {jobs.length >= JOB_LIMIT && (
@@ -200,9 +259,8 @@
                       <th scope="col">Recording</th>
                       <th scope="col">Pool</th>
                       <th scope="col">Priority</th>
-                      <th scope="col">Attempts</th>
-                      <th scope="col">Created</th>
-                      <th scope="col">Detail</th>
+                      <th scope="col">Tries</th>
+                      <th scope="col">Queued</th>
                       <th scope="col">Actions</th>
                     </tr>
                   </thead>
@@ -212,9 +270,12 @@
                         <th scope="row">
                           <code>{shortId(job.id)}</code>
                         </th>
-                        <td className="nowrap">{job.state}</td>
+                        <td className={`nowrap job-state job-state-${jobStateText(job).tone}`}>
+                          {jobStateText(job).label}
+                        </td>
                         <td className="long">
-                          {job.location}: <span className="mono">{job.key}</span>
+                          <span className="mono">{job.key}</span>
+                          <span className="cell-note">{jobNote(job)}</span>
                         </td>
                         <td className="nowrap">{job.pool}</td>
                         <td className="num nowrap">{job.priority}</td>
@@ -222,17 +283,17 @@
                           {job.attempts} of {job.max_attempts}
                         </td>
                         <td className="nowrap">{formatTime(job.created_at)}</td>
-                        <td className="long">{detail(job)}</td>
                         <td className="actions">
                           {RETRYABLE.has(job.state) && (
                             <ActionButton
                               held={leader.role}
                               action="jobs.retry"
                               busy={retry.busy}
+                              primary={job.state === "failed"}
                               onClick={() => void onRetry(job)}
-                              name={`Retry job ${shortId(job.id)}`}
+                              name={`Try again: job ${shortId(job.id)}`}
                             >
-                              Retry
+                              Try again
                             </ActionButton>
                           )}
                           {OPEN.has(job.state) && (
@@ -269,7 +330,7 @@
       {cancelling !== null && (
         <ConfirmDialog
           title={`Cancel job ${shortId(cancelling.id)}?`}
-          message={`The job for ${cancelling.key} stops and stays stopped unless someone retries it.`}
+          message={`The job for ${cancelling.key} stops, and stays stopped unless someone tries it again.`}
           confirmLabel="Cancel job"
           onClose={() => setCancelling(null)}
           onConfirm={async () => {
````

- [ ] **Step 4: Run the unit tests to see them pass**

Run: `npm test`

Expected: PASS. **396 tests pass** (386 + 10 (three in "job words", seven in "jobs tab")).

- [ ] **Step 5: Update the end-to-end tests**

`drilldown.spec.ts`: the keyboard test tabs to "Try again: job …"; the filter test presses
the Failed pill and checks the counts; a new test reads the table; and the Review Focus test
of a viewer at tablet width. `a11y.spec.ts` gains a scan of a viewer's pages.

<!-- file: e2e/tests/a11y.spec.ts | patch -->
Change `packages/console-web/e2e/tests/a11y.spec.ts`:

````diff
--- a/packages/console-web/e2e/tests/a11y.spec.ts
+++ b/packages/console-web/e2e/tests/a11y.spec.ts
@@ -50,6 +50,20 @@
         page.getByRole("article", { name: "us-1" }).getByText("Not answering", { exact: true }),
       ).toBeVisible({ timeout: 30_000 });
       await expectAccessible(page, `fleet, one leader not answering (${theme})`);
+    });
+
+    test("a viewer's pages, with their switched-off actions, have no accessibility violations", async ({
+      page,
+    }) => {
+      await signIn(page, "viewer", "/leaders/eu-1/jobs");
+      await expect(page.getByRole("button", { name: /^Try again: job / }).first()).toBeDisabled();
+      await expectAccessible(page, `jobs as a viewer (${theme})`);
+      await page.getByRole("button", { name: /^Failed/ }).click();
+      await expect(page.getByRole("region", { name: "Job list" }).getByRole("row")).toHaveCount(2);
+      await expectAccessible(page, `jobs as a viewer, filtered (${theme})`);
+      await page.goto("/leaders/eu-1/tokens");
+      await expect(page.getByText(/Join tokens need the admin role/)).toBeVisible();
+      await expectAccessible(page, `join tokens as a viewer (${theme})`);
     });
 
     test("the join token dialogs have no accessibility violations", async ({ page }) => {
````

<!-- file: e2e/tests/drilldown.spec.ts | patch -->
Change `packages/console-web/e2e/tests/drilldown.spec.ts`:

````diff
--- a/packages/console-web/e2e/tests/drilldown.spec.ts
+++ b/packages/console-web/e2e/tests/drilldown.spec.ts
@@ -19,10 +19,10 @@
   const failed = page.getByRole("row").filter({ hasText: "the engine stopped" });
   await expect(failed).toBeVisible();
 
-  await tabTo(page, /^Retry job /);
-  await page.keyboard.press("Enter");
-  await expect(page.getByText(/^Job \w{8} is queued again\.$/)).toBeVisible();
-  // The Retry button went with the row's state; the focus must not fall back to the page top.
+  await tabTo(page, /^Try again: job /);
+  await page.keyboard.press("Enter");
+  await expect(page.getByText(/^Job \w{8} is waiting again\.$/)).toBeVisible();
+  // The Try again button went with the row's state; the focus must not fall back to the page top.
   await expect
     .poll(() => page.evaluate(() => document.activeElement?.closest("[role='region']")?.getAttribute("aria-label")))
     .toBe("Job list");
@@ -55,16 +55,79 @@
 
 test("the job filter narrows by state and survives a reload", async ({ page }) => {
   await signIn(page, "viewer", "/leaders/eu-1/jobs");
-  await page.getByRole("combobox", { name: "State" }).selectOption("failed");
+  const pills = page.getByRole("group", { name: "Show jobs that are" });
+  // The fake leader: two waiting, one being worked on, one failed, one finished, one cancelled.
+  await expect(pills.getByRole("button")).toHaveText([
+    "All 6",
+    "Waiting 2",
+    "Being worked on 1",
+    "Failed 1",
+    "Finished 1",
+    "Cancelled 1",
+  ]);
+  await expect(page.getByRole("region", { name: "Job list" }).getByRole("row")).toHaveCount(7);
+  await pills.getByRole("button", { name: /^Failed/ }).click();
   await expect(page).toHaveURL("/leaders/eu-1/jobs?state=failed");
   await expect(page.getByRole("region", { name: "Job list" }).getByRole("row")).toHaveCount(2);
   await page.reload();
-  await expect(page.getByRole("combobox", { name: "State" })).toHaveValue("failed");
+  await expect(pills.getByRole("button", { name: /^Failed/ })).toHaveAttribute("aria-pressed", "true");
+  await expect(pills.getByRole("button", { name: /^All/ })).toHaveAttribute("aria-pressed", "false");
+  await expect(page.getByRole("region", { name: "Job list" }).getByRole("row")).toHaveCount(2);
+});
+
+test("each job says its state in words and where its recording came from", async ({ page }) => {
+  await signIn(page, "viewer", "/leaders/eu-1/jobs");
+  const list = page.getByRole("region", { name: "Job list" });
+  await expect(list.getByRole("columnheader")).toHaveText([
+    "Job",
+    "State",
+    "Recording",
+    "Pool",
+    "Priority",
+    "Tries",
+    "Queued",
+    "Actions",
+  ]);
+  const failed = list.getByRole("row").filter({ hasText: "the engine stopped" });
+  await expect(failed.getByRole("cell").nth(0)).toHaveText("Failed");
+  await expect(failed.getByRole("cell").nth(1)).toHaveText(
+    "incoming/meeting-5.wavFrom intake · the engine stopped: out of memory",
+  );
+  await expect(failed.getByRole("cell").nth(4)).toHaveText("3 of 3");
+  await expect(list.getByRole("cell", { name: /^With follower \w{8}$/ })).toHaveCount(1);
+  await expect(list.getByRole("cell", { name: "Waiting", exact: true })).toHaveCount(2);
+  await expect(list.getByRole("row").filter({ hasText: "Cancelled by someone@example.org" })).toHaveCount(1);
+  await expect(page.getByText(/^Loaded at /)).toBeVisible();
+});
+
+test("a viewer's switched-off actions stay pinned and readable at tablet width", async ({ page }) => {
+  await page.setViewportSize({ width: 768, height: 1024 });
+  await signIn(page, "viewer", "/leaders/eu-1/jobs");
+  const region = page.getByRole("region", { name: "Job list" });
+  const button = region.getByRole("button", { name: /^Cancel job / }).first();
+  await expect(button).toBeDisabled();
+  const widest = await region.evaluate((el) => el.scrollWidth - el.clientWidth);
+  for (const left of [0, widest]) {
+    await region.evaluate((el, x) => {
+      el.scrollLeft = x;
+    }, left);
+    const frame = await region.boundingBox();
+    const box = await button.boundingBox();
+    // The note that says which role is needed sits under its button, inside the pinned cell.
+    const note = await region.getByText("needs operator").first().boundingBox();
+    if (frame === null || box === null || note === null) throw new Error("nothing to measure");
+    for (const part of [box, note]) {
+      expect(part.x).toBeGreaterThanOrEqual(frame.x);
+      expect(part.x + part.width).toBeLessThanOrEqual(frame.x + frame.width + 1);
+    }
+  }
+  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
+  expect(overflow).toBeLessThanOrEqual(0);
 });
 
 test("a viewer sees actions disabled with the role they need", async ({ page }) => {
   await signIn(page, "viewer", "/leaders/eu-1/jobs");
-  const retry = page.getByRole("button", { name: /^Retry job / }).first();
+  const retry = page.getByRole("button", { name: /^Try again: job / }).first();
   await expect(retry).toBeDisabled();
   await expect(retry).toHaveAccessibleDescription("needs operator");
   await page.getByRole("link", { name: "Join tokens" }).click();
````

- [ ] **Step 6: Type-check, lint, build and run the end-to-end tests**

Run, in `packages/console-web`:

```bash
npm run typecheck
npm run lint
npm test
npm run build
npx playwright test e2e/tests/drilldown.spec.ts e2e/tests/a11y.spec.ts e2e/tests/dialogs.spec.ts e2e/tests/overview.spec.ts --retries=0
```

Expected: `tsc` prints nothing. ESLint prints nothing (0 warnings). Vitest: **396 tests pass**, none
fail. The build ends with `dist/ ok: index.html and 3 hashed assets`. Playwright: every test in the named specs passes, with no retry.

If an end-to-end test fails, read what it was checking before changing it: the page is wrong
far more often than the test. Nothing may be left listening on ports 8900 or 8901 afterwards.

- [ ] **Step 7: Commit**

```bash
git add packages/console-web/src/pages/leader/JobsTab.tsx packages/console-web/src/pages/leader/JobsTab.test.tsx packages/console-web/e2e/tests/a11y.spec.ts packages/console-web/e2e/tests/drilldown.spec.ts
git commit -m "Console web app: jobs tab with state pills and counts, states in words, Try again

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

### Task 4: Pools and followers, locations, consent report

**Files:**
- Modify: `packages/console-web/src/pages/leader/ConsentTab.tsx`
- Modify: `packages/console-web/src/pages/leader/LocationsTab.tsx`
- Modify: `packages/console-web/src/pages/leader/PoolsTab.tsx`
- Test: `packages/console-web/src/pages/leader/ConsentTab.test.tsx`
- Test: `packages/console-web/src/pages/leader/LocationsTab.test.tsx`
- Test: `packages/console-web/src/pages/leader/PoolsTab.test.tsx`
- Test: `packages/console-web/e2e/tests/a11y.spec.ts`
- Test: `packages/console-web/e2e/tests/drilldown.spec.ts`

**Interfaces:**
- Consumes: Task 2's `ActionButton` `primary` and `RefreshButton`; R1's table classes (`.cell-note`, `.nowrap`, `.long`).
- Produces: `followerStateText(state: string): string` from `PoolsTab.tsx`; `channels(location: Pick<LocationOut, "channel_mode" | "channel_labels">): string` and `deviceText(device: string): string` from `LocationsTab.tsx`; buttons named "Wind down follower {id}", "Switch off {location}", "Switch on {location}"; a confirmation titled "Switch off {location}?" whose action is "Switch it off".

Three tabs, mostly words (spec section 6.2).

- **Pools and followers:** "Drain" becomes "Wind down" and its notice says what that means;
  states and column headings use the console's words; "1 job went", not "1 jobs went".
- **Locations:** the table goes from nine columns to six. Folder carries "Looks in …" and the
  channel layout as notes; Pool carries the device; Scanning carries how often. "Disable" and
  "Enable" become "Switch off" and "Switch on". The add-location form's labels and its
  validation messages do not change; only two option lists do.
- **Consent report:** three strings and two column headings.

- [ ] **Step 1: Write the failing tests**


<!-- file: src/pages/leader/ConsentTab.test.tsx | patch -->
Change `packages/console-web/src/pages/leader/ConsentTab.test.tsx`:

````diff
--- a/packages/console-web/src/pages/leader/ConsentTab.test.tsx
+++ b/packages/console-web/src/pages/leader/ConsentTab.test.tsx
@@ -29,7 +29,7 @@
     );
     expect(screen.getByText("intake: transcripts/b.json")).toBeInTheDocument();
     // Above the tables, where it is seen first, and below them.
-    expect(screen.getAllByText(/The report is cut short/)).toHaveLength(2);
+    expect(screen.getAllByText(/This list is cut short/)).toHaveLength(2);
     // Only the report was read, once, with no query the console does not need.
     expect(mock.callsTo(REPORT)).toHaveLength(1);
   });
@@ -44,7 +44,7 @@
         "No transcript was made from a recording that is no longer consented.",
       ),
     ).toBeInTheDocument();
-    expect(screen.getByText("This leader has no locations.")).toBeInTheDocument();
+    expect(screen.getByText("This leader has no locations yet.")).toBeInTheDocument();
     expect(screen.queryByText(/cut short/)).not.toBeInTheDocument();
   });
 
````

<!-- file: src/pages/leader/LocationsTab.test.tsx | patch -->
Change `packages/console-web/src/pages/leader/LocationsTab.test.tsx`:

````diff
--- a/packages/console-web/src/pages/leader/LocationsTab.test.tsx
+++ b/packages/console-web/src/pages/leader/LocationsTab.test.tsx
@@ -6,7 +6,7 @@
 import { leader } from "../../test/fixtures";
 import { renderApp } from "../../test/renderApp";
 import { EMPTY_FORM, validateLocation } from "./locationForm";
-import { locationBody } from "./LocationsTab";
+import { channels, deviceText, locationBody } from "./LocationsTab";
 
 const LOCATION: LocationOut = {
   id: "l1",
@@ -135,6 +135,50 @@
 });
 
 describe("locations tab", () => {
+  it("says channels, device and state in the console's words", async () => {
+    expect(channels({ channel_mode: "mono", channel_labels: ["Left", "Right"] })).toBe("Mono");
+    expect(channels({ channel_mode: "stereo_split", channel_labels: ["Agent", "Caller"] })).toBe(
+      "Stereo, one speaker per side: Agent, Caller",
+    );
+    expect(channels({ channel_mode: "auto", channel_labels: ["Left", "Right"] })).toBe(
+      "Automatic: Left, Right",
+    );
+    expect(channels({ channel_mode: "quad", channel_labels: ["A", "B"] })).toBe("quad: A, B");
+    expect(deviceText("any")).toBe("Any device");
+    expect(deviceText("cuda")).toBe("GPU (CUDA) only");
+    expect(deviceText("cpu")).toBe("CPU only");
+    expect(deviceText("tpu")).toBe("tpu");
+    renderApp("/leaders/eu-1/locations").on(
+      LOCATIONS,
+      reply(200, [
+        { ...LOCATION, input_prefix: "incoming/", required_device: "cuda", scan_requested: true },
+        DISABLED,
+      ]),
+    );
+    const region = await screen.findByRole("region", { name: "Location list" });
+    expect(
+      within(region)
+        .getAllByRole("columnheader")
+        .map((th) => th.textContent),
+    ).toEqual(["Location", "Folder", "Pool", "Scanning", "Last scan", "Actions"]);
+    const intake = within(region).getByRole("row", { name: /^intake/ });
+    expect(
+      within(intake)
+        .getAllByRole("cell")
+        .slice(0, 4)
+        .map((td) => td.textContent),
+    ).toEqual([
+      "/srv/intakeLooks in incoming/Mono",
+      "defaultGPU (CUDA) only",
+      "OnEvery 15 min",
+      "Not yetA scan is asked forthe root folder is not readable",
+    ]);
+    const archive = within(region).getByRole("row", { name: /^archive/ });
+    expect(within(archive).getAllByRole("cell")[1]).toHaveTextContent("defaultAny device");
+    expect(within(archive).getAllByRole("cell")[2]).toHaveTextContent("Switched offEvery 15 min");
+    expect(within(archive).getByRole("button", { name: "Switch on archive" })).toHaveTextContent(/^Switch on$/);
+  });
+
   it("requests a scan, and asks before disabling", async () => {
     const mock = renderApp("/leaders/eu-1/locations", { fleet: [leader({ role: "admin" })] })
       .on(LOCATIONS, reply(200, [LOCATION]))
@@ -148,15 +192,15 @@
       );
     expect(await screen.findByText("the root folder is not readable")).toBeInTheDocument();
     await userEvent.click(screen.getByRole("button", { name: "Scan now intake" }));
-    expect(await screen.findByText("A scan of intake is requested.")).toBeInTheDocument();
-    await userEvent.click(screen.getByRole("button", { name: "Disable intake" }));
-    const dialog = screen.getByRole("alertdialog", { name: "Disable intake?" });
+    expect(await screen.findByText("A scan of intake is asked for.")).toBeInTheDocument();
+    await userEvent.click(screen.getByRole("button", { name: "Switch off intake" }));
+    const dialog = screen.getByRole("alertdialog", { name: "Switch off intake?" });
     expect(mock.callsTo("POST /api/leaders/eu-1/locations/intake/disable")).toHaveLength(0);
-    await userEvent.click(within(dialog).getByRole("button", { name: "Disable location" }));
+    await userEvent.click(within(dialog).getByRole("button", { name: "Switch it off" }));
     await waitFor(() =>
       expect(mock.callsTo("POST /api/leaders/eu-1/locations/intake/disable")).toHaveLength(1),
     );
-    expect(await screen.findByText("intake is disabled.")).toBeInTheDocument();
+    expect(await screen.findByText("intake is switched off.")).toBeInTheDocument();
   });
 
   it("enables a disabled location and offers no scan for it", async () => {
@@ -166,8 +210,8 @@
         "POST /api/leaders/eu-1/locations/archive/enable",
         reply(200, { ...DISABLED, enabled: true }),
       );
-    await userEvent.click(await screen.findByRole("button", { name: "Enable archive" }));
-    expect(await screen.findByText("archive is enabled.")).toBeInTheDocument();
+    await userEvent.click(await screen.findByRole("button", { name: "Switch on archive" }));
+    expect(await screen.findByText("archive is switched on.")).toBeInTheDocument();
     expect(mock.callsTo("POST /api/leaders/eu-1/locations/archive/enable")).toHaveLength(1);
     expect(screen.queryByRole("button", { name: "Scan now archive" })).not.toBeInTheDocument();
   });
@@ -177,7 +221,7 @@
       LOCATIONS,
       reply(200, [LOCATION]),
     );
-    expect(await screen.findByRole("button", { name: "Disable intake" })).toBeDisabled();
+    expect(await screen.findByRole("button", { name: "Switch off intake" })).toBeDisabled();
     const add = screen.getByRole("button", { name: "Add location" });
     expect(add).toBeDisabled();
     expect(add).toHaveAccessibleDescription("needs admin");
@@ -193,7 +237,7 @@
     );
     expect(await screen.findByRole("button", { name: "Scan now intake" })).toBeEnabled();
     expect(screen.getByRole("button", { name: "Add location" })).toBeDisabled();
-    expect(screen.getByRole("button", { name: "Disable intake" })).toBeDisabled();
+    expect(screen.getByRole("button", { name: "Switch off intake" })).toBeDisabled();
   });
 
   it("announces a refused scan where the person is", async () => {
@@ -213,9 +257,9 @@
     renderApp("/leaders/eu-1/locations", { fleet: [leader({ role: "admin" })] })
       .on(LOCATIONS, reply(200, [LOCATION]))
       .on("POST /api/leaders/eu-1/locations/intake/disable", fail(503, "leader_unreachable"));
-    await userEvent.click(await screen.findByRole("button", { name: "Disable intake" }));
+    await userEvent.click(await screen.findByRole("button", { name: "Switch off intake" }));
     const dialog = screen.getByRole("alertdialog");
-    await userEvent.click(within(dialog).getByRole("button", { name: "Disable location" }));
+    await userEvent.click(within(dialog).getByRole("button", { name: "Switch it off" }));
     expect(await within(dialog).findByRole("alert")).toHaveTextContent(
       "The leader is not answering right now.",
     );
````

<!-- file: src/pages/leader/PoolsTab.test.tsx | patch -->
Change `packages/console-web/src/pages/leader/PoolsTab.test.tsx`:

````diff
--- a/packages/console-web/src/pages/leader/PoolsTab.test.tsx
+++ b/packages/console-web/src/pages/leader/PoolsTab.test.tsx
@@ -5,6 +5,7 @@
 import { reply } from "../../test/fetchMock";
 import { leader } from "../../test/fixtures";
 import { renderApp } from "../../test/renderApp";
+import { followerStateText } from "./PoolsTab";
 
 const FOLLOWER: FollowerOut = {
   id: "33333333-3333-4333-8333-333333333333",
@@ -17,6 +18,49 @@
 };
 
 describe("pools and followers tab", () => {
+  it("says followers' states and the pools' columns in the console's words", async () => {
+    expect(followerStateText("active")).toBe("At work");
+    expect(followerStateText("draining")).toBe("Winding down");
+    expect(followerStateText("revoked")).toBe("Revoked");
+    expect(followerStateText("gone")).toBe("Gone");
+    expect(followerStateText("asleep")).toBe("asleep");
+    renderApp("/leaders/eu-1/pools").on(
+      "GET /api/leaders/eu-1/followers",
+      reply(200, [FOLLOWER, { ...FOLLOWER, id: "44444444-4444-4444-8444-444444444444", state: "draining" }]),
+    );
+    const followers = await screen.findByRole("region", { name: "Followers" });
+    expect(within(followers).getByRole("row", { name: /33333333/ })).toHaveTextContent("At work");
+    expect(within(followers).getByRole("row", { name: /44444444/ })).toHaveTextContent("Winding down");
+    expect(
+      within(followers)
+        .getAllByRole("columnheader")
+        .map((th) => th.textContent),
+    ).toEqual(["Follower", "Pool", "State", "Device", "Working on", "Last seen", "Actions"]);
+    const pools = screen.getByRole("region", { name: "Pools" });
+    expect(
+      within(pools)
+        .getAllByRole("columnheader")
+        .map((th) => th.textContent),
+    ).toEqual(["Pool", "Waiting", "Being worked on", "Followers at work", "Winding down", "Revoked", "Gone"]);
+    expect(screen.getByText(/^From the check at /)).toBeInTheDocument();
+  });
+
+  it("says one job, not 1 jobs, when a revoked follower held one", async () => {
+    renderApp("/leaders/eu-1/pools", { fleet: [leader({ role: "admin" })] })
+      .on("GET /api/leaders/eu-1/followers", reply(200, [FOLLOWER]))
+      .on(
+        `POST /api/leaders/eu-1/followers/${FOLLOWER.id}/revoke`,
+        reply(200, { id: FOLLOWER.id, state: "revoked", released: 1 }),
+      );
+    await userEvent.click(await screen.findByRole("button", { name: "Revoke follower 33333333" }));
+    await userEvent.click(
+      within(screen.getByRole("alertdialog")).getByRole("button", { name: "Revoke follower" }),
+    );
+    expect(
+      await screen.findByText("Follower 33333333 is revoked. 1 job went back to waiting."),
+    ).toBeInTheDocument();
+  });
+
   it("shows pools from the latest poll and drains a follower", async () => {
     let drained = false;
     const mock = renderApp("/leaders/eu-1/pools")
@@ -32,10 +76,12 @@
       within(pools).getByRole("rowheader", { name: "gpu" }),
     ).toBeInTheDocument();
     await userEvent.click(
-      await screen.findByRole("button", { name: "Drain follower 33333333" }),
+      await screen.findByRole("button", { name: "Wind down follower 33333333" }),
     );
     expect(
-      await screen.findByText("Follower 33333333 is draining."),
+      await screen.findByText(
+        "Follower 33333333 is winding down: it finishes what it has and takes nothing new.",
+      ),
     ).toBeInTheDocument();
     expect(
       mock.callsTo(`POST /api/leaders/eu-1/followers/${FOLLOWER.id}/drain`)[0]
@@ -43,7 +89,7 @@
     ).toBe("csrf-token-1");
     await waitFor(() =>
       expect(
-        screen.queryByRole("button", { name: "Drain follower 33333333" }),
+        screen.queryByRole("button", { name: "Wind down follower 33333333" }),
       ).not.toBeInTheDocument(),
     );
   });
@@ -74,7 +120,7 @@
     );
     expect(
       await screen.findByText(
-        /is revoked; 2 leased jobs went back to the queue/,
+        /is revoked\. 2 jobs went back to waiting\./,
       ),
     ).toBeInTheDocument();
     await waitFor(() =>
@@ -100,7 +146,7 @@
     expect(revoke).toBeDisabled();
     expect(revoke).toHaveAccessibleDescription("needs admin");
     await userEvent.click(
-      screen.getByRole("button", { name: "Drain follower 33333333" }),
+      screen.getByRole("button", { name: "Wind down follower 33333333" }),
     );
     expect(await screen.findByRole("alert")).toBeInTheDocument();
   });
````

- [ ] **Step 2: Run the tests to see them fail**

Run: `npx vitest run src/pages/leader/PoolsTab.test.tsx src/pages/leader/LocationsTab.test.tsx src/pages/leader/ConsentTab.test.tsx`

Expected: FAIL, `Tests  13 failed | 59 passed (72)`: five in `PoolsTab.test.tsx` (`followerStateText` is not exported; no button named "Wind down follower 33333333"), six in `LocationsTab.test.tsx` (`channels` and `deviceText` are not exported; no button named "Switch off intake") and two in `ConsentTab.test.tsx`.

- [ ] **Step 3: Write the implementation**


<!-- file: src/pages/leader/ConsentTab.tsx | patch -->
Change `packages/console-web/src/pages/leader/ConsentTab.tsx`:

````diff
--- a/packages/console-web/src/pages/leader/ConsentTab.tsx
+++ b/packages/console-web/src/pages/leader/ConsentTab.tsx
@@ -8,8 +8,8 @@
     <>
       <div className="section-head">
         <p className="muted">
-          Recordings by consent state, and transcripts made from recordings that are no longer
-          consented.
+          How many recordings are consented, and the transcripts that were made from a recording
+          no longer consented.
         </p>
         <RefreshButton read={read} />
       </div>
@@ -18,12 +18,12 @@
           <>
             {report.truncated && (
               <p className="notice">
-                The report is cut short; the leader holds more flagged transcripts.
+                This list is cut short: the leader holds more transcripts to review than it sent.
               </p>
             )}
             <h3>By location</h3>
             {report.locations.length === 0 ? (
-              <p>This leader has no locations.</p>
+              <p>This leader has no locations yet.</p>
             ) : (
               <div
                 className="table-scroll"
@@ -70,8 +70,8 @@
                     <tr>
                       <th scope="col">Job</th>
                       <th scope="col">Recording</th>
-                      <th scope="col">Completed</th>
-                      <th scope="col">Outputs</th>
+                      <th scope="col">Finished</th>
+                      <th scope="col">Files written</th>
                     </tr>
                   </thead>
                   <tbody>
@@ -101,7 +101,7 @@
             )}
             {report.truncated && (
               <p className="notice">
-                The report is cut short; the leader holds more flagged transcripts.
+                This list is cut short: the leader holds more transcripts to review than it sent.
               </p>
             )}
           </>
````

<!-- file: src/pages/leader/LocationsTab.tsx | patch -->
Change `packages/console-web/src/pages/leader/LocationsTab.tsx`:

````diff
--- a/packages/console-web/src/pages/leader/LocationsTab.tsx
+++ b/packages/console-web/src/pages/leader/LocationsTab.tsx
@@ -172,9 +172,9 @@
               set({ required_device: event.target.value as RequiredDevice | "" })
             }
           >
-            <option value="">Any (default)</option>
-            <option value="cuda">CUDA GPU</option>
-            <option value="cpu">CPU</option>
+            <option value="">Any device (the default)</option>
+            <option value="cuda">GPU (CUDA) only</option>
+            <option value="cpu">CPU only</option>
           </select>
         </label>
         <TextField
@@ -192,7 +192,7 @@
             onChange={(event) => set({ channel_mode: event.target.value as ChannelMode })}
           >
             <option value="mono">Mono</option>
-            <option value="stereo_split">Stereo, one speaker per channel</option>
+            <option value="stereo_split">Stereo, one speaker per side</option>
             <option value="auto">Automatic</option>
           </select>
         </label>
@@ -243,9 +243,28 @@
   );
 }
 
-function channels(location: LocationOut): string {
-  if (location.channel_mode === "mono") return "mono";
-  return `${location.channel_mode} (${location.channel_labels.join(", ")})`;
+const CHANNEL_MODES: Record<string, string> = {
+  mono: "Mono",
+  stereo_split: "Stereo, one speaker per side",
+  auto: "Automatic",
+};
+
+const DEVICES: Record<string, string> = {
+  any: "Any device",
+  cuda: "GPU (CUDA) only",
+  cpu: "CPU only",
+};
+
+/** Which followers may take a location's recordings; an unknown device as the leader sent it. */
+export function deviceText(device: string): string {
+  return DEVICES[device] ?? device;
+}
+
+/** "Mono", "Stereo, one speaker per side: Agent, Caller"; an unknown mode as the leader sent it. */
+export function channels(location: Pick<LocationOut, "channel_mode" | "channel_labels">): string {
+  const mode = CHANNEL_MODES[location.channel_mode] ?? location.channel_mode;
+  if (location.channel_mode === "mono") return mode;
+  return `${mode}: ${location.channel_labels.join(", ")}`;
 }
 
 export function LocationsTab({ leader }: TabProps) {
@@ -269,7 +288,7 @@
   return (
     <div {...rows.props}>
       <div className="section-head">
-        <ActionButton held={leader.role} action="locations.add" onClick={() => setAdding(true)}>
+        <ActionButton held={leader.role} action="locations.add" primary onClick={() => setAdding(true)}>
           Add location
         </ActionButton>
         <RefreshButton read={read} />
@@ -279,19 +298,16 @@
       <ReadState read={read} what="locations">
         {(locations) =>
           locations.length === 0 ? (
-            <p>This leader has no locations.</p>
+            <p>This leader has no locations yet.</p>
           ) : (
             <div className="table-scroll" role="region" aria-label="Location list" tabIndex={0}>
-              <table className="wide">
+              <table className="medium">
                 <thead>
                   <tr>
                     <th scope="col">Location</th>
                     <th scope="col">Folder</th>
                     <th scope="col">Pool</th>
-                    <th scope="col">Device</th>
-                    <th scope="col">Channels</th>
-                    <th scope="col">Scan every</th>
-                    <th scope="col">Enabled</th>
+                    <th scope="col">Scanning</th>
                     <th scope="col">Last scan</th>
                     <th scope="col">Actions</th>
                   </tr>
@@ -303,18 +319,24 @@
                       <td className="long">
                         <span className="mono">{location.root ?? "–"}</span>
                         {location.input_prefix !== "" && (
-                          <span className="cell-note">Input prefix {location.input_prefix}</span>
+                          <span className="cell-note">Looks in {location.input_prefix}</span>
                         )}
+                        <span className="cell-note">{channels(location)}</span>
                       </td>
-                      <td className="nowrap">{location.pool}</td>
-                      <td className="nowrap">{location.required_device}</td>
-                      <td>{channels(location)}</td>
-                      <td>{formatDuration(location.scan_interval_s)}</td>
-                      <td className="nowrap">{location.enabled ? "Yes" : "No"}</td>
+                      <td>
+                        <span className="nowrap">{location.pool}</span>
+                        <span className="cell-note nowrap">{deviceText(location.required_device)}</span>
+                      </td>
+                      <td>
+                        <span className="nowrap">{location.enabled ? "On" : "Switched off"}</span>
+                        <span className="cell-note nowrap">
+                          Every {formatDuration(location.scan_interval_s)}
+                        </span>
+                      </td>
                       <td className="long">
-                        {location.last_scan_at ? formatTime(location.last_scan_at) : "Never"}
+                        {location.last_scan_at ? formatTime(location.last_scan_at) : "Not yet"}
                         {location.scan_requested && (
-                          <span className="cell-note">Scan requested</span>
+                          <span className="cell-note">A scan is asked for</span>
                         )}
                         {location.last_scan_error && (
                           <span className="cell-note error-text">{location.last_scan_error}</span>
@@ -330,7 +352,7 @@
                               void post(
                                 location,
                                 "ingest",
-                                `A scan of ${location.name} is requested.`,
+                                `A scan of ${location.name} is asked for.`,
                               )
                             }
                             name={`Scan now ${location.name}`}
@@ -344,9 +366,9 @@
                             action="locations.disable"
                             danger
                             onClick={() => setDisabling(location)}
-                            name={`Disable ${location.name}`}
+                            name={`Switch off ${location.name}`}
                           >
-                            Disable
+                            Switch off
                           </ActionButton>
                         ) : (
                           <ActionButton
@@ -354,11 +376,11 @@
                             action="locations.enable"
                             busy={action.busy}
                             onClick={() =>
-                              void post(location, "enable", `${location.name} is enabled.`)
+                              void post(location, "enable", `${location.name} is switched on.`)
                             }
-                            name={`Enable ${location.name}`}
+                            name={`Switch on ${location.name}`}
                           >
-                            Enable
+                            Switch on
                           </ActionButton>
                         )}
                       </td>
@@ -382,17 +404,17 @@
       )}
       {disabling !== null && (
         <ConfirmDialog
-          title={`Disable ${disabling.name}?`}
+          title={`Switch off ${disabling.name}?`}
           message={
-            "The leader stops scanning this location for new recordings until it is enabled " +
-            "again. Jobs already made are not affected."
+            "The leader stops looking in this location for new recordings until it is switched " +
+            "on again. Jobs already made carry on."
           }
-          confirmLabel="Disable location"
+          confirmLabel="Switch it off"
           onClose={() => setDisabling(null)}
           onConfirm={async () => {
             setNotice(null);
             await api.post(path(disabling, "disable"));
-            rows.done(`${disabling.name} is disabled.`);
+            rows.done(`${disabling.name} is switched off.`);
           }}
         />
       )}
````

<!-- file: src/pages/leader/PoolsTab.tsx | patch -->
Change `packages/console-web/src/pages/leader/PoolsTab.tsx`:

````diff
--- a/packages/console-web/src/pages/leader/PoolsTab.tsx
+++ b/packages/console-web/src/pages/leader/PoolsTab.tsx
@@ -9,24 +9,36 @@
 import { ActionNotice, ReadState, RefreshButton, shortId, useLeaderRead, type TabProps } from "./common";
 import { useRowFocus } from "./rowFocus";
 
+const FOLLOWER_STATES: Record<string, string> = {
+  active: "At work",
+  draining: "Winding down",
+  revoked: "Revoked",
+  gone: "Gone",
+};
+
+/** A follower's state in the console's words; an unknown state as the leader sent it. */
+export function followerStateText(state: string): string {
+  return FOLLOWER_STATES[state] ?? state;
+}
+
 function PoolTable({ leader }: TabProps) {
   const status = leader.snapshot?.status;
-  if (status === undefined) return <p>No successful poll yet, so pool figures are not known.</p>;
+  if (status === undefined) return <p>No check has worked yet, so the pools are not known.</p>;
   const names = [
     ...new Set([...status.pools.map((p) => p.pool), ...status.follower_pools.map((p) => p.pool)]),
   ].sort();
   return (
     <>
-      <p className="muted">From the poll at {formatTime(leader.snapshot?.taken_at ?? "")}.</p>
+      <p className="muted">From the check at {formatTime(leader.snapshot?.taken_at ?? "")}.</p>
       <div className="table-scroll" role="region" aria-label="Pools" tabIndex={0}>
         <table className="medium">
           <thead>
             <tr>
               <th scope="col">Pool</th>
-              <th scope="col">Queued</th>
-              <th scope="col">Leased</th>
-              <th scope="col">Active followers</th>
-              <th scope="col">Draining</th>
+              <th scope="col">Waiting</th>
+              <th scope="col">Being worked on</th>
+              <th scope="col">Followers at work</th>
+              <th scope="col">Winding down</th>
               <th scope="col">Revoked</th>
               <th scope="col">Gone</th>
             </tr>
@@ -65,7 +77,9 @@
     setNotice(null);
     const ok = await drain.run(() => api.post(leaderPath(leader.name, `followers/${follower.id}/drain`)));
     if (ok) {
-      rows.done(`Follower ${shortId(follower.id)} is draining.`);
+      rows.done(
+        `Follower ${shortId(follower.id)} is winding down: it finishes what it has and takes nothing new.`,
+      );
     }
   };
 
@@ -82,7 +96,7 @@
       <ReadState read={read} what="followers">
         {(followers) =>
           followers.length === 0 ? (
-            <p>No followers have joined this leader.</p>
+            <p>No follower has joined this leader yet.</p>
           ) : (
             <div className="table-scroll" role="region" aria-label="Followers" tabIndex={0}>
               <table className="medium">
@@ -92,7 +106,7 @@
                     <th scope="col">Pool</th>
                     <th scope="col">State</th>
                     <th scope="col">Device</th>
-                    <th scope="col">Leases</th>
+                    <th scope="col">Working on</th>
                     <th scope="col">Last seen</th>
                     <th scope="col">Actions</th>
                   </tr>
@@ -104,7 +118,7 @@
                         <code>{shortId(follower.id)}</code>
                       </th>
                       <td className="nowrap">{follower.pool}</td>
-                      <td className="nowrap">{follower.state}</td>
+                      <td className="nowrap">{followerStateText(follower.state)}</td>
                       <td className="nowrap">{follower.device ?? "–"}</td>
                       <td className="num">{formatCount(follower.leases)}</td>
                       <td>{formatTime(follower.last_seen_at)}</td>
@@ -115,9 +129,9 @@
                             action="followers.drain"
                             busy={drain.busy}
                             onClick={() => void onDrain(follower)}
-                            name={`Drain follower ${shortId(follower.id)}`}
+                            name={`Wind down follower ${shortId(follower.id)}`}
                           >
-                            Drain
+                            Wind down
                           </ActionButton>
                         )}
                         {(follower.state === "active" || follower.state === "draining") && (
@@ -143,7 +157,7 @@
       {revoking !== null && (
         <ConfirmDialog
           title={`Revoke follower ${shortId(revoking.id)}?`}
-          message="The follower can no longer take work and its leased jobs go back to the queue. It needs a new join token to come back."
+          message="The follower can take no more work, and the jobs it holds go back to waiting. It needs a new join token to come back."
           confirmLabel="Revoke follower"
           onClose={() => setRevoking(null)}
           onConfirm={async () => {
@@ -151,7 +165,9 @@
               leaderPath(leader.name, `followers/${revoking.id}/revoke`),
             );
             rows.done(
-              `Follower ${shortId(revoking.id)} is revoked; ${answer.released} leased jobs went back to the queue.`,
+              `Follower ${shortId(revoking.id)} is revoked. ${
+                answer.released === 1 ? "1 job went" : `${answer.released} jobs went`
+              } back to waiting.`,
             );
           }}
         />
````

- [ ] **Step 4: Run the unit tests to see them pass**

Run: `npm test`

Expected: PASS. **399 tests pass** (396 + 2 in `PoolsTab.test.tsx` + 1 in `LocationsTab.test.tsx`).

- [ ] **Step 5: Update the end-to-end tests**

`drilldown.spec.ts`: the follower and location tests use the new words. `a11y.spec.ts`: the location confirmation is opened by "Switch off intake".

<!-- file: e2e/tests/a11y.spec.ts | patch -->
Change `packages/console-web/e2e/tests/a11y.spec.ts`:

````diff
--- a/packages/console-web/e2e/tests/a11y.spec.ts
+++ b/packages/console-web/e2e/tests/a11y.spec.ts
@@ -111,8 +111,14 @@
       await expect(page.getByRole("dialog").getByRole("alert").first()).toBeVisible();
       await expectAccessible(page, `add location dialog with errors (${theme})`);
       await page.keyboard.press("Escape");
-      await page.getByRole("button", { name: "Disable intake" }).click();
-      await expectAccessible(page, `disable location confirm dialog (${theme})`);
+      await page.getByRole("button", { name: "Switch off intake" }).click();
+      await expectAccessible(page, `switch off location confirm dialog (${theme})`);
+    });
+
+    test("administration's refusal of a viewer has no accessibility violations", async ({ page }) => {
+      await signIn(page, "viewer", "/admin/leaders");
+      await expect(page.getByText(/Administration is for console administrators/)).toBeVisible();
+      await expectAccessible(page, `administration as a viewer (${theme})`);
     });
 
     test("the administration dialogs have no accessibility violations", async ({ page }) => {
````

<!-- file: e2e/tests/drilldown.spec.ts | patch -->
Change `packages/console-web/e2e/tests/drilldown.spec.ts`:

````diff
--- a/packages/console-web/e2e/tests/drilldown.spec.ts
+++ b/packages/console-web/e2e/tests/drilldown.spec.ts
@@ -140,10 +140,15 @@
     page.getByRole("region", { name: "Pools" }).getByRole("rowheader", { name: "gpu" }),
   ).toBeVisible();
   await page
-    .getByRole("button", { name: /^Drain follower / })
+    .getByRole("button", { name: /^Wind down follower / })
     .first()
     .click();
-  await expect(page.getByText(/^Follower \w{8} is draining\.$/)).toBeVisible();
+  await expect(
+    page.getByText(/^Follower \w{8} is winding down: it finishes what it has and takes nothing new\.$/),
+  ).toBeVisible();
+  await expect(
+    page.getByRole("region", { name: "Followers" }).getByRole("cell", { name: "Winding down" }),
+  ).toHaveCount(2);
   await expect(page.getByRole("button", { name: /^Revoke follower / }).first()).toBeDisabled();
 });
 
@@ -156,7 +161,7 @@
     .first();
   await row.getByRole("button", { name: /^Revoke follower / }).click();
   await page.getByRole("alertdialog").getByRole("button", { name: "Revoke follower" }).click();
-  await expect(page.getByText(/is revoked; 1 leased jobs went back to the queue\./)).toBeVisible();
+  await expect(page.getByText(/is revoked\. 1 job went back to waiting\./)).toBeVisible();
 });
 
 test("an admin adds, disables, enables and scans locations", async ({ page }) => {
@@ -171,25 +176,30 @@
   await dialog.getByRole("textbox", { name: "Right channel label" }).fill("Caller");
   await dialog.getByRole("button", { name: "Add location" }).click();
   await expect(page.getByText("Location calls is added.")).toBeVisible();
-  await expect(page.getByRole("row", { name: /calls/ })).toContainText("stereo_split (Agent, Caller)");
-
-  await page.getByRole("button", { name: "Disable archive" }).click();
-  await page.getByRole("alertdialog").getByRole("button", { name: "Disable location" }).click();
-  await expect(page.getByText("archive is disabled.")).toBeVisible();
-  await page.getByRole("button", { name: "Enable archive" }).click();
-  await expect(page.getByText("archive is enabled.")).toBeVisible();
+  await expect(page.getByRole("row", { name: /^calls/ })).toContainText(
+    "Stereo, one speaker per side: Agent, Caller",
+  );
+
+  await page.getByRole("button", { name: "Switch off archive" }).click();
+  await page.getByRole("alertdialog").getByRole("button", { name: "Switch it off" }).click();
+  await expect(page.getByText("archive is switched off.")).toBeVisible();
+  await expect(page.getByRole("row", { name: /^archive/ }).getByRole("cell").nth(2)).toHaveText(
+    "Switched offEvery 15 min",
+  );
+  await page.getByRole("button", { name: "Switch on archive" }).click();
+  await expect(page.getByText("archive is switched on.")).toBeVisible();
   await page.getByRole("button", { name: "Scan now intake" }).click();
-  await expect(page.getByText("A scan of intake is requested.")).toBeVisible();
-  await expect(page.getByRole("row", { name: /intake/ })).toContainText("Scan requested");
+  await expect(page.getByText("A scan of intake is asked for.")).toBeVisible();
+  await expect(page.getByRole("row", { name: /^intake/ })).toContainText("A scan is asked for");
 });
 
 test("a leader's own refusal is shown and focus stays in the dialog: us-1 caps the console at operator", async ({
   page,
 }) => {
   await signIn(page, "admin", "/leaders/us-1/locations");
-  await page.getByRole("button", { name: "Disable intake" }).click();
+  await page.getByRole("button", { name: "Switch off intake" }).click();
   const dialog = page.getByRole("alertdialog");
-  const confirm = dialog.getByRole("button", { name: "Disable location" });
+  const confirm = dialog.getByRole("button", { name: "Switch it off" });
   await confirm.click();
   await expect(dialog.getByRole("alert")).toContainText("Your role does not allow this.");
   await expect(dialog.getByRole("alert")).toContainText("this needs the admin role");
````

- [ ] **Step 6: Type-check, lint, build and run the end-to-end tests**

Run, in `packages/console-web`:

```bash
npm run typecheck
npm run lint
npm test
npm run build
npx playwright test e2e/tests/drilldown.spec.ts e2e/tests/a11y.spec.ts e2e/tests/overview.spec.ts --retries=0
```

Expected: `tsc` prints nothing. ESLint prints nothing (0 warnings). Vitest: **399 tests pass**, none
fail. The build ends with `dist/ ok: index.html and 3 hashed assets`. Playwright: every test in the named specs passes, with no retry.

If an end-to-end test fails, read what it was checking before changing it: the page is wrong
far more often than the test. Nothing may be left listening on ports 8900 or 8901 afterwards.

- [ ] **Step 7: Commit**

```bash
git add packages/console-web/src/pages/leader/ConsentTab.tsx packages/console-web/src/pages/leader/LocationsTab.tsx packages/console-web/src/pages/leader/PoolsTab.tsx packages/console-web/src/pages/leader/ConsentTab.test.tsx packages/console-web/src/pages/leader/LocationsTab.test.tsx packages/console-web/src/pages/leader/PoolsTab.test.tsx packages/console-web/e2e/tests/a11y.spec.ts packages/console-web/e2e/tests/drilldown.spec.ts
git commit -m "Console web app: followers wind down, locations switch off and fit in six columns, consent report wording

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

### Task 5: Join tokens, and the token shown once

**Files:**
- Modify: `packages/console-web/src/pages/leader/TokensTab.tsx`
- Test: `packages/console-web/src/pages/leader/TokensTab.test.tsx`
- Test: `packages/console-web/e2e/tests/a11y.spec.ts`
- Test: `packages/console-web/e2e/tests/tokens.spec.ts`

**Interfaces:**
- Consumes: Task 2's `.token-row`, `.token-field`, `.token-status`, `.fact-row`, `ActionButton` `primary`; R1's dialog sheet; `formatCount`, `formatTime`.
- Produces: a dialog named "Here is the join token. It is shown once.", described by its paragraph, holding the textbox "Join token", the buttons "Copy" and "I have stored it", a status line, and the facts Pool, Can be used, Expires.

`docs/superpowers/design/TokenDialog.dc.html`.

Only the dialog's markup and words change. Every rule about the token stays exactly as it
is, and `TokensTab.test.tsx` keeps every assertion about it: the plaintext is in this
dialog's props and nowhere else; focus starts on the token, selected; until it is copied,
Escape and "I have stored it" both ask first; tokens are shown one at a time.

The label is now a `<label for>` beside a row that holds the field and Copy, so the field
keeps its name "Join token". The status line starts at "Not copied yet.". The second status
line (the question) is always in the page and takes no room while empty: a live region that
appears from nothing is not read out reliably.

- [ ] **Step 1: Write the failing tests**


<!-- file: src/pages/leader/TokensTab.test.tsx | patch -->
Change `packages/console-web/src/pages/leader/TokensTab.test.tsx`:

````diff
--- a/packages/console-web/src/pages/leader/TokensTab.test.tsx
+++ b/packages/console-web/src/pages/leader/TokensTab.test.tsx
@@ -60,7 +60,7 @@
   await user.clear(uses);
   await user.type(uses, "2");
   await user.click(within(form).getByRole("button", { name: "Create token" }));
-  return screen.findByRole("dialog", { name: "Join token created" });
+  return screen.findByRole("dialog", { name: "Here is the join token. It is shown once." });
 }
 
 describe("join tokens tab: role", () => {
@@ -82,7 +82,7 @@
   it("lists tokens by state without any plaintext, and offers revoke only for a live one", async () => {
     renderApp("/leaders/eu-1/tokens", ADMIN).on(LIST, reply(200, [TOKEN, REVOKED, EXPIRED, USED]));
     const table = await screen.findByRole("region", { name: "Join token list" });
-    expect(within(table).getByRole("row", { name: /44444444/ })).toHaveTextContent("Usable");
+    expect(within(table).getByRole("row", { name: /44444444/ })).toHaveTextContent("Can be used");
     expect(within(table).getByRole("row", { name: /55550000/ })).toHaveTextContent("Revoked");
     expect(within(table).getByRole("row", { name: /66660000/ })).toHaveTextContent("Expired");
     expect(within(table).getByRole("row", { name: /77770000/ })).toHaveTextContent("Used up");
@@ -164,11 +164,50 @@
     await user.click(await screen.findByRole("button", { name: "Create join token" }));
     await user.click(screen.getByRole("button", { name: "Create token" }));
     expect(await within(screen.getByRole("dialog")).findByRole("alert")).toBeInTheDocument();
-    expect(screen.queryByRole("dialog", { name: "Join token created" })).not.toBeInTheDocument();
+    expect(screen.queryByRole("dialog", { name: "Here is the join token. It is shown once." })).not.toBeInTheDocument();
   });
 });
 
 describe("join tokens tab: the plaintext is shown once", () => {
+  it("says what the token is for, that it is not copied yet, and its pool, uses and expiry", async () => {
+    const user = userEvent.setup();
+    renderApp("/leaders/eu-1/tokens", ADMIN)
+      .on(LIST, reply(200, [TOKEN]))
+      .on(CREATE, reply(201, CREATED));
+    const shown = await createToken(user);
+    expect(within(shown).getByRole("heading", { level: 2 })).toHaveTextContent(
+      "Here is the join token. It is shown once.",
+    );
+    expect(shown).toHaveAccessibleDescription(
+      "Copy it now and give it to the machine that will join the gpu pool. After you close this, nobody can read it again, including you.",
+    );
+    expect(within(shown).getByText("Not copied yet.")).toHaveAttribute("role", "status");
+    const facts: Record<string, string> = {};
+    for (const term of within(shown).getAllByRole("term")) {
+      facts[term.textContent ?? ""] = term.nextElementSibling?.textContent ?? "";
+    }
+    expect(Object.keys(facts)).toEqual(["Pool", "Can be used", "Expires"]);
+    expect(facts.Pool).toBe("gpu");
+    expect(facts["Can be used"]).toBe("2 times");
+    expect(facts.Expires).toMatch(/2026/);
+    // The token, then Copy, then the way out: Copy is the dialog's one primary button.
+    expect(within(shown).getAllByRole("button").map((button) => button.textContent)).toEqual([
+      "Copy",
+      "I have stored it",
+    ]);
+    expect(within(shown).getByRole("button", { name: "Copy" })).toHaveClass("button-primary");
+    expect(shown.querySelector("[style]")).toBeNull();
+  });
+
+  it("says once, not 1 times, for a token that can be used a single time", async () => {
+    const user = userEvent.setup();
+    renderApp("/leaders/eu-1/tokens", ADMIN)
+      .on(LIST, reply(200, [TOKEN]))
+      .on(CREATE, reply(201, { ...CREATED, max_uses: 1 }));
+    const shown = await createToken(user);
+    expect(within(shown).getByText("Can be used").nextElementSibling).toHaveTextContent(/^once$/);
+  });
+
   it("shows it in a dialog with the copy button, focus on the selected token", async () => {
     const user = userEvent.setup();
     const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue(undefined);
@@ -182,10 +221,10 @@
     expect(field).toHaveFocus();
     const input = field as HTMLInputElement;
     expect([input.selectionStart, input.selectionEnd]).toEqual([0, SECRET.length]);
-    expect(shown).toHaveAccessibleDescription(/only time the token is shown/);
-    await user.click(within(shown).getByRole("button", { name: "Copy token" }));
+    expect(shown).toHaveAccessibleDescription(/nobody can read it again, including you/);
+    await user.click(within(shown).getByRole("button", { name: "Copy" }));
     expect(writeText).toHaveBeenCalledExactlyOnceWith(SECRET);
-    expect(within(shown).getByText("Copied to the clipboard.")).toBeInTheDocument();
+    expect(within(shown).getByText("Copied.")).toBeInTheDocument();
   });
 
   it("is gone from the page, React state, URL, storage, title and logs once closed", async () => {
@@ -206,7 +245,7 @@
     expect(window.location.href).not.toContain("sst_plaintext");
     expect(document.title).not.toContain("sst_plaintext");
 
-    await user.click(within(shown).getByRole("button", { name: "Copy token" }));
+    await user.click(within(shown).getByRole("button", { name: "Copy" }));
     await user.click(within(shown).getByRole("button", { name: "I have stored it" }));
     expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
     expect(await screen.findByText("Join token 55555555 is created.")).toBeInTheDocument();
@@ -235,7 +274,7 @@
       .on(CREATE, reply(201, CREATED));
     const shown = await createToken(user);
     await user.keyboard("{Escape}");
-    expect(screen.getByRole("dialog", { name: "Join token created" })).toBeInTheDocument();
+    expect(screen.getByRole("dialog", { name: "Here is the join token. It is shown once." })).toBeInTheDocument();
     expect(
       within(shown).getByText(
         /The token is not shown again\. To close without copying it, press Escape again/,
@@ -254,7 +293,7 @@
       .on(LIST, reply(200, []))
       .on(CREATE, reply(201, CREATED));
     const shown = await createToken(user);
-    await user.click(within(shown).getByRole("button", { name: "Copy token" }));
+    await user.click(within(shown).getByRole("button", { name: "Copy" }));
     await user.keyboard("{Escape}");
     expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
   });
@@ -290,8 +329,8 @@
       .on(CREATE, reply(201, CREATED));
     const shown = await createToken(user);
     arrange();
-    await user.click(within(shown).getByRole("button", { name: "Copy token" }));
-    const status = await within(shown).findByText("Copying failed: select the token and copy it.");
+    await user.click(within(shown).getByRole("button", { name: "Copy" }));
+    const status = await within(shown).findByText("Copying did not work. Select the token and copy it yourself.");
     expect(status.closest("[role=status]")).not.toBeNull();
     const field = within(shown).getByRole("textbox", { name: "Join token" }) as HTMLInputElement;
     expect(field).toHaveValue(SECRET);
@@ -310,12 +349,12 @@
     const form = screen.getByRole("dialog");
     await user.click(within(form).getByRole("textbox", { name: "Pool" }));
     await user.keyboard("{Enter}");
-    const shown = await screen.findByRole("dialog", { name: "Join token created" });
+    const shown = await screen.findByRole("dialog", { name: "Here is the join token. It is shown once." });
     await user.keyboard("{Enter}");
-    expect(screen.getByRole("dialog", { name: "Join token created" })).toBeInTheDocument();
+    expect(screen.getByRole("dialog", { name: "Here is the join token. It is shown once." })).toBeInTheDocument();
     expect(mock.callsTo(CREATE)).toHaveLength(1);
     await user.click(within(shown).getByRole("button", { name: "I have stored it" }));
-    expect(screen.getByRole("dialog", { name: "Join token created" })).toBeInTheDocument();
+    expect(screen.getByRole("dialog", { name: "Here is the join token. It is shown once." })).toBeInTheDocument();
     expect(within(shown).getByText(/not shown again/)).toBeInTheDocument();
     await user.click(within(shown).getByRole("button", { name: "I have stored it" }));
     expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
@@ -332,7 +371,7 @@
     await user.click(await screen.findByRole("button", { name: "Create join token" }));
     await user.click(within(screen.getByRole("dialog")).getByRole("textbox", { name: "Pool" }));
     await user.keyboard("{Enter>5}");
-    await screen.findByRole("dialog", { name: "Join token created" });
+    await screen.findByRole("dialog", { name: "Here is the join token. It is shown once." });
     await user.keyboard("{/Enter}");
     expect(mock.callsTo(CREATE)).toHaveLength(1);
   });
@@ -344,11 +383,11 @@
       .on(CREATE, reply(201, CREATED));
     const shown = await createToken(user);
     vi.spyOn(navigator.clipboard, "writeText").mockRejectedValue(new Error("no"));
-    await user.click(within(shown).getByRole("button", { name: "Copy token" }));
-    await within(shown).findByText("Copying failed: select the token and copy it.");
-    await user.keyboard("{Escape}");
-    expect(screen.getByRole("dialog", { name: "Join token created" })).toBeInTheDocument();
-    expect(within(shown).getByText("Copying failed: select the token and copy it.")).toBeVisible();
+    await user.click(within(shown).getByRole("button", { name: "Copy" }));
+    await within(shown).findByText("Copying did not work. Select the token and copy it yourself.");
+    await user.keyboard("{Escape}");
+    expect(screen.getByRole("dialog", { name: "Here is the join token. It is shown once." })).toBeInTheDocument();
+    expect(within(shown).getByText("Copying did not work. Select the token and copy it yourself.")).toBeVisible();
     const question = within(shown).getByText(/not shown again/);
     expect(question.closest("[role=status]")).not.toBeNull();
     await user.keyboard("{Escape}");
@@ -378,7 +417,7 @@
       screen.getByRole("dialog", { name: "Create a join token for eu-1" }),
     ).toBeInTheDocument();
     release();
-    const shown = await screen.findByRole("dialog", { name: "Join token created" });
+    const shown = await screen.findByRole("dialog", { name: "Here is the join token. It is shown once." });
     expect(within(shown).getByRole("textbox", { name: "Join token" })).toHaveValue(SECRET);
   });
 
@@ -399,17 +438,17 @@
       );
     }
     render(<Harness />);
-    const first = await screen.findByRole("dialog", { name: "Join token created" });
-    await user.click(within(first).getByRole("button", { name: "Copy token" }));
-    expect(within(first).getByText("Copied to the clipboard.")).toBeInTheDocument();
+    const first = await screen.findByRole("dialog", { name: "Here is the join token. It is shown once." });
+    await user.click(within(first).getByRole("button", { name: "Copy" }));
+    expect(within(first).getByText("Copied.")).toBeInTheDocument();
     // A second token arrives while the first is showing: the first stays.
     act(() => setQueue((q) => [...q, second]));
     expect(within(first).getByRole("textbox", { name: "Join token" })).toHaveValue(SECRET);
     expect(reactStateHolds("sst_second")).toBe(true);
     await user.click(within(first).getByRole("button", { name: "I have stored it" }));
-    const next = await screen.findByRole("dialog", { name: "Join token created" });
+    const next = await screen.findByRole("dialog", { name: "Here is the join token. It is shown once." });
     expect(within(next).getByRole("textbox", { name: "Join token" })).toHaveValue("sst_second");
-    expect(within(next).queryByText("Copied to the clipboard.")).not.toBeInTheDocument();
+    expect(within(next).queryByText("Copied.")).not.toBeInTheDocument();
     expect(document.body.innerHTML).not.toContain(SECRET);
     await user.click(within(next).getByRole("button", { name: "I have stored it" }));
     expect(screen.getByText(/not shown again/)).toBeInTheDocument();
````

- [ ] **Step 2: Run the tests to see them fail**

Run: `npx vitest run src/pages/leader/TokensTab.test.tsx`

Expected: FAIL, `Tests  16 failed | 11 passed (27)`: there is no dialog named "Here is the join token. It is shown once.", so every test that opens it fails; the list test expects "Can be used".

- [ ] **Step 3: Write the implementation**


<!-- file: src/pages/leader/TokensTab.tsx | patch -->
Change `packages/console-web/src/pages/leader/TokensTab.tsx`:

````diff
--- a/packages/console-web/src/pages/leader/TokensTab.tsx
+++ b/packages/console-web/src/pages/leader/TokensTab.tsx
@@ -7,7 +7,7 @@
 import { ConfirmDialog } from "../../components/ConfirmDialog";
 import { Dialog } from "../../components/Dialog";
 import { ErrorPanel } from "../../components/ErrorPanel";
-import { formatTime } from "../../lib/format";
+import { formatCount, formatTime } from "../../lib/format";
 import {
   ActionNotice,
   ReadState,
@@ -217,6 +217,7 @@
  * drops it when the dialog closes, and nothing writes it to the URL, storage, the title or a
  * log. It cannot be dismissed by accident: until the token is copied, Escape and "I have
  * stored it" both ask first and close on the second go; focus starts on the token itself.
+ * Layout: docs/superpowers/design/TokenDialog.dc.html.
  */
 export function TokenCreatedDialog({
   created,
@@ -229,6 +230,7 @@
   const [asked, setAsked] = useState(false);
   const tokenRef = useRef<HTMLInputElement>(null);
   const warningId = useId();
+  const tokenId = useId();
 
   // Focus starts on the token, selected: a stray Enter then does nothing, and a copy by hand
   // is one keystroke. (Dialog focuses its first control; this runs after it.)
@@ -254,9 +256,9 @@
     else setAsked(true);
   };
 
-  let status = "";
-  if (copied === "yes") status = "Copied to the clipboard.";
-  else if (copied === "failed") status = "Copying failed: select the token and copy it.";
+  let status = "Not copied yet.";
+  if (copied === "yes") status = "Copied.";
+  else if (copied === "failed") status = "Copying did not work. Select the token and copy it yourself.";
   // The question is its own announcement, shown beside a failure text, never replaced by it.
   const question = asked
     ? "The token is not shown again. To close without copying it, press Escape again or " +
@@ -264,35 +266,55 @@
     : "";
 
   return (
-    <Dialog title="Join token created" onClose={requestClose} describedBy={warningId}>
-      <p id={warningId}>
-        <strong>This is the only time the token is shown.</strong> Copy it now and give it to
-        whoever starts the follower. Pool {created.pool}; up to {created.max_uses}{" "}
-        {created.max_uses === 1 ? "use" : "uses"}; expires {formatTime(created.expires_at)}.
+    <Dialog
+      title="Here is the join token. It is shown once."
+      onClose={requestClose}
+      describedBy={warningId}
+    >
+      <p id={warningId} className="muted">
+        Copy it now and give it to the machine that will join the <strong>{created.pool}</strong>{" "}
+        pool. After you close this, nobody can read it again, including you.
       </p>
-      <label className="field">
+      <label className="field" htmlFor={tokenId}>
         Join token
+      </label>
+      <div className="token-row">
         <input
+          id={tokenId}
           ref={tokenRef}
-          className="mono"
+          className="mono token-field"
           readOnly
           autoComplete="off"
           spellCheck={false}
           value={created.token}
           onFocus={(event) => event.target.select()}
         />
-      </label>
-      <p className="action-notice" role="status">
+        <button type="button" className="button button-primary" onClick={() => void copy()}>
+          Copy
+        </button>
+      </div>
+      <p className="token-status" role="status">
         {status}
       </p>
-      <p className="action-notice" role="status">
+      <p className="token-status" role="status">
         {question}
       </p>
+      <dl className="fact-row">
+        <div>
+          <dt>Pool</dt>
+          <dd>{created.pool}</dd>
+        </div>
+        <div>
+          <dt>Can be used</dt>
+          <dd>{created.max_uses === 1 ? "once" : `${formatCount(created.max_uses)} times`}</dd>
+        </div>
+        <div>
+          <dt>Expires</dt>
+          <dd>{formatTime(created.expires_at)}</dd>
+        </div>
+      </dl>
       <div className="dialog-buttons">
-        <button type="button" className="button" onClick={() => void copy()}>
-          Copy token
-        </button>
-        <button type="button" className="button button-primary" onClick={requestClose}>
+        <button type="button" className="button" onClick={requestClose}>
           I have stored it
         </button>
       </div>
@@ -321,7 +343,7 @@
   if (token.revoked) return "Revoked";
   if (Date.parse(token.expires_at) <= now) return "Expired";
   if (token.uses >= token.max_uses) return "Used up";
-  return "Usable";
+  return "Can be used";
 }
 
 function TokenList({ leader }: TabProps) {
@@ -337,7 +359,7 @@
   return (
     <div {...rows.props}>
       <div className="section-head">
-        <ActionButton held={leader.role} action="tokens.create" onClick={() => setCreating(true)}>
+        <ActionButton held={leader.role} action="tokens.create" primary onClick={() => setCreating(true)}>
           Create join token
         </ActionButton>
         <RefreshButton read={read} />
@@ -349,7 +371,7 @@
           // describes the list as the leader returned it.
           const now = read.updatedAt ?? 0;
           return tokens.length === 0 ? (
-            <p>No join tokens.</p>
+            <p>No join tokens yet.</p>
           ) : (
             <div className="table-scroll" role="region" aria-label="Join token list" tabIndex={0}>
               <table className="medium">
@@ -358,9 +380,9 @@
                     <th scope="col">Token</th>
                     <th scope="col">Pool</th>
                     <th scope="col">State</th>
-                    <th scope="col">Uses</th>
+                    <th scope="col">Used</th>
                     <th scope="col">Expires</th>
-                    <th scope="col">Created by</th>
+                    <th scope="col">Made by</th>
                     <th scope="col">Actions</th>
                   </tr>
                 </thead>
@@ -423,7 +445,7 @@
       {revoking !== null && (
         <ConfirmDialog
           title={`Revoke join token ${shortId(revoking.id)}?`}
-          message="No new follower can join with it. Followers that already joined are not affected."
+          message="No new follower can join with it. Followers that already joined carry on."
           confirmLabel="Revoke token"
           onClose={() => setRevoking(null)}
           onConfirm={async () => {
````

- [ ] **Step 4: Run the unit tests to see them pass**

Run: `npm test`

Expected: PASS. **401 tests pass** (399 + 2 (the facts, and "once")).

- [ ] **Step 5: Update the end-to-end tests**

`tokens.spec.ts` uses the dialog's new name and words, checks the facts, and gains the Review Focus test at phone width. `a11y.spec.ts` finds the dialog by its new name.

<!-- file: e2e/tests/a11y.spec.ts | patch -->
Change `packages/console-web/e2e/tests/a11y.spec.ts`:

````diff
--- a/packages/console-web/e2e/tests/a11y.spec.ts
+++ b/packages/console-web/e2e/tests/a11y.spec.ts
@@ -71,7 +71,7 @@
       await page.getByRole("button", { name: "Create join token" }).click();
       await expectAccessible(page, `create token dialog (${theme})`);
       await page.getByRole("dialog").getByRole("button", { name: "Create token" }).click();
-      const shown = page.getByRole("dialog", { name: "Join token created" });
+      const shown = page.getByRole("dialog", { name: "Here is the join token. It is shown once." });
       await expect(shown).toBeVisible();
       await expectAccessible(page, `token created dialog (${theme})`);
       await shown.getByRole("button", { name: "I have stored it" }).click();
````

<!-- file: e2e/tests/tokens.spec.ts | patch -->
Change `packages/console-web/e2e/tests/tokens.spec.ts`:

````diff
--- a/packages/console-web/e2e/tests/tokens.spec.ts
+++ b/packages/console-web/e2e/tests/tokens.spec.ts
@@ -44,7 +44,7 @@
   await page.keyboard.type("gpu");
   await page.keyboard.press("Enter");
 
-  const shown = page.getByRole("dialog", { name: "Join token created" });
+  const shown = page.getByRole("dialog", { name: "Here is the join token. It is shown once." });
   const field = shown.getByRole("textbox", { name: "Join token" });
   await expect(field).toHaveValue(/^sst_/);
   // Focus starts on the token itself, selected: Enter, even twice, dismisses nothing.
@@ -54,9 +54,15 @@
   await expect(shown).toBeVisible();
   expect(posts.count()).toBe(1);
 
+  // Not copied yet, and what the token is good for, before anything is pressed.
+  await expect(shown.getByText("Not copied yet.")).toBeVisible();
+  await expect(shown.getByRole("term")).toHaveText(["Pool", "Can be used", "Expires"]);
+  await expect(shown.getByRole("definition").nth(0)).toHaveText("gpu");
+  await expect(shown.getByRole("definition").nth(1)).toHaveText("once");
+
   const plaintext = await field.inputValue();
-  await shown.getByRole("button", { name: "Copy token" }).click();
-  await expect(shown.getByText("Copied to the clipboard.")).toBeVisible();
+  await shown.getByRole("button", { name: "Copy", exact: true }).click();
+  await expect(shown.getByText("Copied.")).toBeVisible();
   expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(plaintext);
 
   // Copied: one "I have stored it" closes it.
@@ -68,6 +74,35 @@
   await expect(page.getByRole("button", { name: "Create join token" })).toBeFocused();
 });
 
+test("on a phone the one-time token dialog fits: nothing is cut off and every button is in reach", async ({
+  page,
+}) => {
+  await page.setViewportSize({ width: 390, height: 844 });
+  await signIn(page, "admin", "/leaders/eu-1/tokens");
+  await openCreate(page);
+  await page.keyboard.press("Enter");
+  const shown = page.getByRole("dialog", { name: "Here is the join token. It is shown once." });
+  await expect(shown.getByRole("textbox", { name: "Join token" })).toHaveValue(/^sst_/);
+  const frame = await shown.boundingBox();
+  if (frame === null) throw new Error("the dialog is not on the page");
+  expect(frame.x).toBeGreaterThanOrEqual(0);
+  expect(frame.x + frame.width).toBeLessThanOrEqual(390);
+  for (const part of [
+    shown.getByRole("textbox", { name: "Join token" }),
+    shown.getByRole("button", { name: "Copy", exact: true }),
+    shown.getByRole("button", { name: "I have stored it" }),
+  ]) {
+    await part.scrollIntoViewIfNeeded();
+    const box = await part.boundingBox();
+    if (box === null) throw new Error("a control is not on the page");
+    expect(box.x).toBeGreaterThanOrEqual(frame.x);
+    expect(box.x + box.width).toBeLessThanOrEqual(frame.x + frame.width);
+    await expect(part).toBeInViewport();
+  }
+  // Nothing inside the dialog scrolls sideways either.
+  expect(await shown.evaluate((el) => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(0);
+});
+
 test("a held Enter creates exactly one token", async ({ page }) => {
   await signIn(page, "admin", "/leaders/eu-1/tokens");
   const posts = countPosts(page);
@@ -75,7 +110,7 @@
   await expect(form.getByRole("textbox", { name: "Pool" })).toBeFocused();
   for (let i = 0; i < 7; i += 1) await page.keyboard.down("Enter");
   await page.keyboard.up("Enter");
-  const shown = page.getByRole("dialog", { name: "Join token created" });
+  const shown = page.getByRole("dialog", { name: "Here is the join token. It is shown once." });
   await expect(shown).toBeVisible();
   // More repeats after the one-time dialog is up: it must stay.
   await page.keyboard.down("Enter");
@@ -93,7 +128,7 @@
   await signIn(page, "admin", "/leaders/eu-1/tokens");
   await openCreate(page);
   await page.keyboard.press("Enter");
-  const shown = page.getByRole("dialog", { name: "Join token created" });
+  const shown = page.getByRole("dialog", { name: "Here is the join token. It is shown once." });
   const plaintext = await shown.getByRole("textbox", { name: "Join token" }).inputValue();
   await page.keyboard.press("Escape");
   await expect(shown).toBeVisible();
@@ -107,7 +142,7 @@
   await signIn(page, "admin", "/leaders/eu-1/tokens");
   await openCreate(page);
   await page.keyboard.press("Enter");
-  const shown = page.getByRole("dialog", { name: "Join token created" });
+  const shown = page.getByRole("dialog", { name: "Here is the join token. It is shown once." });
   await expect(shown).toBeVisible();
   const stored = shown.getByRole("button", { name: "I have stored it" });
   await stored.click();
@@ -121,7 +156,7 @@
   await signIn(page, "admin", "/leaders/eu-1/tokens");
   await openCreate(page);
   await page.keyboard.press("Enter");
-  const shown = page.getByRole("dialog", { name: "Join token created" });
+  const shown = page.getByRole("dialog", { name: "Here is the join token. It is shown once." });
   await expect(shown).toBeVisible();
   await page.evaluate(() => {
     Object.defineProperty(navigator.clipboard, "writeText", {
@@ -129,8 +164,8 @@
       value: () => Promise.reject(new Error("denied")),
     });
   });
-  await shown.getByRole("button", { name: "Copy token" }).click();
-  await expect(shown.getByText("Copying failed: select the token and copy it.")).toBeVisible();
+  await shown.getByRole("button", { name: "Copy", exact: true }).click();
+  await expect(shown.getByText("Copying did not work. Select the token and copy it yourself.")).toBeVisible();
   await page.keyboard.press("Escape");
   await expect(shown).toBeVisible();
   await expect(shown.getByText(/The token is not shown again/)).toBeVisible();
@@ -161,7 +196,7 @@
   await expect(form).toBeVisible();
 
   release();
-  const shown = page.getByRole("dialog", { name: "Join token created" });
+  const shown = page.getByRole("dialog", { name: "Here is the join token. It is shown once." });
   await expect(shown.getByRole("textbox", { name: "Join token" })).toHaveValue(/^sst_/);
   await expect(form).toHaveCount(0);
 });
@@ -188,7 +223,7 @@
   await expect(form).toBeVisible();
 
   release();
-  const shown = page.getByRole("dialog", { name: "Join token created" });
+  const shown = page.getByRole("dialog", { name: "Here is the join token. It is shown once." });
   await expect(shown.getByRole("textbox", { name: "Join token" })).toHaveValue(/^sst_/);
   await expect(form).toHaveCount(0);
 });
@@ -220,7 +255,7 @@
 
   fail = false;
   await form.getByRole("button", { name: "Create token" }).click();
-  const shown = page.getByRole("dialog", { name: "Join token created" });
-  await expect(shown.getByRole("textbox", { name: "Join token" })).toHaveValue(/^sst_/);
-});
-
+  const shown = page.getByRole("dialog", { name: "Here is the join token. It is shown once." });
+  await expect(shown.getByRole("textbox", { name: "Join token" })).toHaveValue(/^sst_/);
+});
+
````

- [ ] **Step 6: Type-check, lint, build and run the end-to-end tests**

Run, in `packages/console-web`:

```bash
npm run typecheck
npm run lint
npm test
npm run build
npx playwright test e2e/tests/tokens.spec.ts e2e/tests/a11y.spec.ts e2e/tests/overview.spec.ts --retries=0
```

Expected: `tsc` prints nothing. ESLint prints nothing (0 warnings). Vitest: **401 tests pass**, none
fail. The build ends with `dist/ ok: index.html and 3 hashed assets`. Playwright: every test in the named specs passes, with no retry.

If an end-to-end test fails, read what it was checking before changing it: the page is wrong
far more often than the test. Nothing may be left listening on ports 8900 or 8901 afterwards.

- [ ] **Step 7: Commit**

```bash
git add packages/console-web/src/pages/leader/TokensTab.tsx packages/console-web/src/pages/leader/TokensTab.test.tsx packages/console-web/e2e/tests/a11y.spec.ts packages/console-web/e2e/tests/tokens.spec.ts
git commit -m "Console web app: the one-time join token dialog to the design; token list wording

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

### Task 6: Administration: one page, three sections

**Files:**
- Modify: `packages/console-web/src/App.tsx`
- Create: `packages/console-web/src/pages/admin/AdminPage.tsx`
- Modify: `packages/console-web/src/pages/admin/AdminFrame.tsx`
- Modify: `packages/console-web/src/pages/admin/PrincipalFields.tsx`
- Modify: `packages/console-web/src/pages/admin/AdminLeadersPage.tsx`
- Modify: `packages/console-web/src/pages/admin/AdminGrantsPage.tsx`
- Modify: `packages/console-web/src/pages/admin/AdminAdminsPage.tsx`
- Test: `packages/console-web/src/pages/admin/admin.test.tsx`
- Test: `packages/console-web/src/pages/admin/adminSecurity.test.tsx`
- Test: `packages/console-web/e2e/screens/capture.spec.ts`
- Test: `packages/console-web/e2e/tests/a11y.spec.ts`
- Test: `packages/console-web/e2e/tests/admin.spec.ts`
- Test: `packages/console-web/e2e/tests/overview.spec.ts`

**Interfaces:**
- Consumes: R1's classes (`.page-sub`, `.tab-list`, `.tab-link`, `.section-head`, `.sheet`, `.callout`, `.form-panel`); `countOf` from `src/lib/format.ts`; `Layout`'s `pageOf` prop.
- Produces: `AdminPage({ section: "leaders" | "grants" | "admins" })` from `src/pages/admin/AdminPage.tsx`; `AdminLeadersSection`, `AdminGrantsSection`, `AdminAdminsSection` (the `…Page` exports are gone); `pageOf` exported from `src/App.tsx`, mapping every `/admin…` path to `"/admin"`; `PRINCIPAL_KINDS: { value; label; field; hint }[]`; on the page: `h1` "Administration", a navigation named "Administration" with links "Leaders", "Who can do what", "Console administrators".

`docs/superpowers/design/AdminLeaders.dc.html`.

**Why `AdminPage` exists.** The three admin pages were three different components, each
wrapping its own `AdminFrame`. React replaces the whole tree when the component type at a
position changes, so the frame, and the link the person had just activated, were thrown away
on every section change and focus was lost. `AdminPage` is one component for all three
addresses: the frame stays and only the section beneath it is replaced. With `pageOf`
treating all of `/admin…` as one page, `Layout` leaves focus on the link, as it does for a
leader's tabs. Arriving from elsewhere still moves focus to the `h1`.

Then the words: grants become roles that are given, a scope is "On which leaders", a
principal is "Who" and its box is named for the choice. The stored forms (`all`,
`label:region=eu`, `domain:example.org`) are shown as they are.

The Leaders section gains a heading that counts ("3 leaders"; "Leaders" until the list has
loaded) and the callout "Adding a leader takes two steps".

- [ ] **Step 1: Write the failing tests**


<!-- file: src/pages/admin/admin.test.tsx | patch -->
Change `packages/console-web/src/pages/admin/admin.test.tsx`:

````diff
--- a/packages/console-web/src/pages/admin/admin.test.tsx
+++ b/packages/console-web/src/pages/admin/admin.test.tsx
@@ -62,11 +62,139 @@
   });
 });
 
+describe("administration frame", () => {
+  it("is one page named Administration, with its three sections as links", async () => {
+    renderApp("/admin/leaders", { session: ADMIN_SESSION }).on("GET /api/admin/leaders", reply(200, [LEADER]));
+    expect(await screen.findByRole("heading", { level: 1, name: "Administration" })).toBeInTheDocument();
+    expect(
+      screen.getByText(/Console administrators decide which leaders this console talks to/),
+    ).toHaveTextContent("Being one gives you no role on any leader by itself.");
+    const sections = within(screen.getByRole("navigation", { name: "Administration" }));
+    expect(sections.getAllByRole("link").map((link) => [link.textContent, link.getAttribute("href")])).toEqual([
+      ["Leaders", "/admin/leaders"],
+      ["Who can do what", "/admin/grants"],
+      ["Console administrators", "/admin/admins"],
+    ]);
+    expect(sections.getByRole("link", { name: "Leaders" })).toHaveAttribute("aria-current", "page");
+    await waitFor(() => expect(document.title).toBe("Administration: Leaders · SwarmScribe console"));
+    expect(await screen.findByRole("heading", { level: 2, name: "1 leader" })).toBeInTheDocument();
+  });
+
+  it("keeps focus on the section link when only the section changes", async () => {
+    renderApp("/admin/leaders", { session: ADMIN_SESSION })
+      .on("GET /api/admin/leaders", reply(200, [LEADER]))
+      .on("GET /api/admin/grants", reply(200, [GRANT]));
+    const sections = within(await screen.findByRole("navigation", { name: "Administration" }));
+    const who = sections.getByRole("link", { name: "Who can do what" });
+    await userEvent.click(who);
+    expect(await screen.findByRole("heading", { level: 2, name: "Who can do what" })).toBeInTheDocument();
+    expect(who).toHaveAttribute("aria-current", "page");
+    expect(who).toHaveFocus();
+    expect(screen.getByRole("heading", { level: 1, name: "Administration" })).not.toHaveFocus();
+  });
+
+  it("moves focus to the Administration heading when arriving from the fleet", async () => {
+    renderApp("/", { session: ADMIN_SESSION, fleet: [] }).on("GET /api/admin/leaders", reply(200, [LEADER]));
+    await userEvent.click(await screen.findByRole("link", { name: "Administration" }));
+    const heading = await screen.findByRole("heading", { level: 1, name: "Administration" });
+    await waitFor(() => expect(heading).toHaveFocus());
+  });
+
+  it("explains the two steps of adding a leader, beside the list", async () => {
+    renderApp("/admin/leaders", { session: ADMIN_SESSION }).on("GET /api/admin/leaders", reply(200, []));
+    const steps = await screen.findByRole("region", { name: "Adding a leader takes two steps" });
+    expect(within(steps).getAllByRole("listitem")).toHaveLength(2);
+    expect(within(steps).getByText("swarmscribe-admin console create")).toBeInTheDocument();
+    expect(await screen.findByText("This console talks to no leaders yet.")).toBeInTheDocument();
+    expect(screen.getByRole("heading", { level: 2, name: "0 leaders" })).toBeInTheDocument();
+  });
+
+  it("shows a leader's state and credential, and names each row's buttons", async () => {
+    const revoked = {
+      ...LEADER,
+      name: "us-1",
+      base_url: "https://us-1.leaders.example",
+      enabled: false,
+      credential_revoked: true,
+    };
+    renderApp("/admin/leaders", { session: ADMIN_SESSION }).on(
+      "GET /api/admin/leaders",
+      reply(200, [LEADER, revoked]),
+    );
+    const region = await screen.findByRole("region", { name: "Registered leaders" });
+    expect(
+      within(region)
+        .getAllByRole("columnheader")
+        .map((th) => th.textContent),
+    ).toEqual(["Leader", "Address", "Labels", "State", "Credential", "Actions"]);
+    const eu = within(region).getByRole("row", { name: /eu-1/ });
+    expect(within(eu).getAllByRole("cell")[2]).toHaveTextContent(/^On$/);
+    expect(
+      within(eu)
+        .getAllByRole("button")
+        .map((button) => [button.textContent, button.getAttribute("aria-label")]),
+    ).toEqual([
+      ["Edit", "Edit eu-1"],
+      ["Replace credential", "Replace credential for eu-1"],
+      ["Remove", "Remove eu-1"],
+    ]);
+    const us = within(region).getByRole("row", { name: /us-1/ });
+    expect(within(us).getAllByRole("cell")[2]).toHaveTextContent(/^Switched off$/);
+    expect(us).toHaveTextContent("Revoked by the leader");
+  });
+
+  it("keeps the heading and the way to add a leader when the list cannot be loaded", async () => {
+    let n = 0;
+    renderApp("/admin/leaders", { session: ADMIN_SESSION }).on("GET /api/admin/leaders", () =>
+      ++n === 1 ? fail(503, "unavailable", "down") : reply(200, [LEADER]),
+    );
+    expect(await screen.findByRole("alert")).toHaveTextContent(
+      "The console is not answering just now. Try again shortly.",
+    );
+    // Not "0 leaders": the console does not know how many there are.
+    expect(screen.getByRole("heading", { level: 2, name: "Leaders" })).toBeInTheDocument();
+    expect(screen.getByRole("button", { name: "Add a leader" })).toBeEnabled();
+    expect(screen.getByRole("region", { name: "Adding a leader takes two steps" })).toBeInTheDocument();
+    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
+    expect(await screen.findByRole("heading", { level: 2, name: "1 leader" })).toBeInTheDocument();
+  });
+
+  it("keeps what was typed when the kind of who is changed, and renames the box", async () => {
+    renderApp("/admin/admins", { session: ADMIN_SESSION }).on(
+      "GET /api/admin/console-admins",
+      reply(200, [ADMIN]),
+    );
+    const form = await screen.findByRole("form", { name: "Add a console administrator" });
+    await userEvent.type(within(form).getByRole("textbox", { name: "Group object ID" }), "example.org");
+    await userEvent.selectOptions(within(form).getByRole("combobox", { name: "Who" }), "domain");
+    expect(within(form).queryByRole("textbox", { name: "Group object ID" })).not.toBeInTheDocument();
+    expect(within(form).getByRole("textbox", { name: "Domain" })).toHaveValue("example.org");
+  });
+
+  it("names the box for each kind of who, with its hint", async () => {
+    renderApp("/admin/grants", { session: ADMIN_SESSION }).on("GET /api/admin/grants", reply(200, []));
+    const form = await screen.findByRole("form", { name: "Give a role" });
+    const who = within(form).getByRole("combobox", { name: "Who" });
+    const expected: [string, string, RegExp][] = [
+      ["entra_group", "Group object ID", /object ID in Entra ID/],
+      ["google_group", "Group address", /group's email address/],
+      ["email", "Email address", /Google account/],
+      ["domain", "Domain", /example\.org/],
+    ];
+    for (const [kind, label, hint] of expected) {
+      await userEvent.selectOptions(who, kind);
+      expect(within(form).getByRole("textbox", { name: label })).toHaveAccessibleDescription(hint);
+    }
+    expect(within(form).getByRole("textbox", { name: "On which leaders" })).toHaveValue("all");
+    expect(await screen.findByText("Nobody has been given a role yet.")).toBeInTheDocument();
+  });
+});
+
 describe("administration pages", () => {
   it("refuses a person who is not a console administrator, without asking", async () => {
     const mock = renderApp("/admin/grants");
     expect(
-      await screen.findByText(/Console administration needs a console administrator/),
+      await screen.findByText(/Administration is for console administrators/),
     ).toBeInTheDocument();
     expect(mock.callsTo("GET /api/admin/grants")).toHaveLength(0);
     expect(screen.queryByRole("link", { name: "Administration" })).not.toBeInTheDocument();
@@ -78,7 +206,7 @@
     async (path) => {
       const mock = renderApp(path);
       expect(
-        await screen.findByText(/Console administration needs a console administrator/),
+        await screen.findByText(/Administration is for console administrators/),
       ).toBeInTheDocument();
       expect(screen.queryByRole("button", { name: /Add / })).not.toBeInTheDocument();
       expect(mock.calls.filter((call) => call.url.startsWith("/api/admin"))).toHaveLength(0);
@@ -97,7 +225,7 @@
     const mock = renderApp("/admin/leaders", { session: ADMIN_SESSION })
       .on("GET /api/admin/leaders", reply(200, [LEADER]))
       .on("POST /api/admin/leaders", reply(201, { ...LEADER, name: "us-1" }));
-    await userEvent.click(await screen.findByRole("button", { name: "Add leader" }));
+    await userEvent.click(await screen.findByRole("button", { name: "Add a leader" }));
     const dialog = screen.getByRole("dialog", { name: "Add a leader" });
     await userEvent.type(within(dialog).getByRole("textbox", { name: "Name" }), "us-1");
     const url = within(dialog).getByRole("textbox", { name: "Address (https://)" });
@@ -108,7 +236,7 @@
     expect(credential).toHaveAttribute("type", "password");
     expect(credential).toHaveAttribute("autocomplete", "off");
     await userEvent.type(credential, CREDENTIAL);
-    await userEvent.click(within(dialog).getByRole("button", { name: "Add leader" }));
+    await userEvent.click(within(dialog).getByRole("button", { name: "Add this leader" }));
     await waitFor(() =>
       expect(mock.callsTo("POST /api/admin/leaders")[0]?.body).toEqual({
         name: "us-1",
@@ -157,10 +285,10 @@
       .on("GET /api/admin/grants", reply(200, [GRANT]))
       .on("POST /api/admin/grants", reply(201, { ...GRANT, id: "g2", role: "viewer", scope: "all" }))
       .on(`DELETE /api/admin/grants/${GRANT.id}`, reply(204));
-    const form = await screen.findByRole("form", { name: "Add a grant" });
-    await userEvent.selectOptions(within(form).getByRole("combobox", { name: "Principal kind" }), "domain");
-    await userEvent.type(within(form).getByRole("textbox", { name: "Principal" }), "example.org");
-    await userEvent.click(within(form).getByRole("button", { name: "Add grant" }));
+    const form = await screen.findByRole("form", { name: "Give a role" });
+    await userEvent.selectOptions(within(form).getByRole("combobox", { name: "Who" }), "domain");
+    await userEvent.type(within(form).getByRole("textbox", { name: "Domain" }), "example.org");
+    await userEvent.click(within(form).getByRole("button", { name: "Give the role" }));
     await waitFor(() =>
       expect(mock.callsTo("POST /api/admin/grants")[0]?.body).toEqual({
         role: "viewer",
@@ -169,8 +297,8 @@
         principal: "example.org",
       }),
     );
-    await userEvent.click(screen.getByRole("button", { name: "Remove grant: operator on label:region=eu for domain:example.org" }));
-    await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Remove grant" }));
+    await userEvent.click(screen.getByRole("button", { name: "Remove operator on label:region=eu from domain:example.org" }));
+    await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Remove the role" }));
     await waitFor(() => expect(mock.callsTo(`DELETE /api/admin/grants/${GRANT.id}`)).toHaveLength(1));
   });
 
````

<!-- file: src/pages/admin/adminSecurity.test.tsx | patch -->
Change `packages/console-web/src/pages/admin/adminSecurity.test.tsx`:

````diff
--- a/packages/console-web/src/pages/admin/adminSecurity.test.tsx
+++ b/packages/console-web/src/pages/admin/adminSecurity.test.tsx
@@ -33,8 +33,8 @@
 const PATCH = "PATCH /api/admin/leaders/eu-1";
 
 async function openRotate() {
-  await userEvent.click(await screen.findByRole("button", { name: "Rotate credential for eu-1" }));
-  return screen.getByRole("dialog", { name: "Rotate the credential for eu-1" });
+  await userEvent.click(await screen.findByRole("button", { name: "Replace credential for eu-1" }));
+  return screen.getByRole("dialog", { name: "Replace the credential for eu-1" });
 }
 
 describe("leader administration requests", () => {
@@ -43,9 +43,9 @@
       LIST,
       reply(200, [LEADER]),
     );
-    await userEvent.click(await screen.findByRole("button", { name: "Add leader" }));
+    await userEvent.click(await screen.findByRole("button", { name: "Add a leader" }));
     const dialog = screen.getByRole("dialog", { name: "Add a leader" });
-    const submit = within(dialog).getByRole("button", { name: "Add leader" });
+    const submit = within(dialog).getByRole("button", { name: "Add this leader" });
     const name = within(dialog).getByRole("textbox", { name: "Name" });
     const url = within(dialog).getByRole("textbox", { name: "Address (https://)" });
     const labels = within(dialog).getByRole("textbox", { name: "Labels" });
@@ -106,7 +106,7 @@
       .on(PATCH, reply(200, { ...LEADER, enabled: false }));
     await userEvent.click(await screen.findByRole("button", { name: "Edit eu-1" }));
     const dialog = screen.getByRole("dialog", { name: "Edit eu-1" });
-    await userEvent.click(within(dialog).getByRole("checkbox", { name: "Enabled" }));
+    await userEvent.click(within(dialog).getByRole("checkbox", { name: "Switched on" }));
     await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
     await waitFor(() => expect(mock.callsTo(PATCH)[0]?.body).toEqual({ enabled: false }));
     expect(await screen.findByText("Leader eu-1 is saved.")).toBeInTheDocument();
@@ -165,7 +165,7 @@
     renderApp("/admin/leaders", { session: ADMIN_SESSION })
       .on(LIST, reply(200, [LEADER]))
       .on("POST /api/admin/leaders", fail(409, "exists", "a leader named '<b>x</b>' exists"));
-    await userEvent.click(await screen.findByRole("button", { name: "Add leader" }));
+    await userEvent.click(await screen.findByRole("button", { name: "Add a leader" }));
     const dialog = screen.getByRole("dialog", { name: "Add a leader" });
     await userEvent.type(within(dialog).getByRole("textbox", { name: "Name" }), "eu-1");
     await userEvent.type(
@@ -173,7 +173,7 @@
       "eu-1.leaders.example",
     );
     await userEvent.type(within(dialog).getByLabelText("Console credential"), CREDENTIAL);
-    await userEvent.click(within(dialog).getByRole("button", { name: "Add leader" }));
+    await userEvent.click(within(dialog).getByRole("button", { name: "Add this leader" }));
     const alert = await within(dialog).findByRole("alert");
     expect(alert).toHaveTextContent("That already exists.");
     expect(alert.querySelector("b")).toBeNull();
@@ -233,7 +233,7 @@
     const mock = renderApp("/admin/leaders", { session: ADMIN_SESSION })
       .on(LIST, reply(200, [LEADER]))
       .on("POST /api/admin/leaders", reply(201, LEADER));
-    await userEvent.click(await screen.findByRole("button", { name: "Add leader" }));
+    await userEvent.click(await screen.findByRole("button", { name: "Add a leader" }));
     const dialog = screen.getByRole("dialog", { name: "Add a leader" });
     await userEvent.type(within(dialog).getByRole("textbox", { name: "Name" }), "us-1");
     await userEvent.type(
@@ -241,7 +241,7 @@
       "us-1.leaders.example",
     );
     await userEvent.type(within(dialog).getByLabelText("Console credential"), CREDENTIAL);
-    await userEvent.click(within(dialog).getByRole("button", { name: "Add leader" }));
+    await userEvent.click(within(dialog).getByRole("button", { name: "Add this leader" }));
     await waitFor(() => expect(mock.callsTo("POST /api/admin/leaders")).toHaveLength(1));
     await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
     expect(reactStateHolds(CREDENTIAL)).toBe(false);
@@ -255,10 +255,10 @@
       .on("POST /api/admin/console-admins", fail(409, "exists", "already"));
     const form = await screen.findByRole("form", { name: "Add a console administrator" });
     await userEvent.selectOptions(
-      within(form).getByRole("combobox", { name: "Principal kind" }),
+      within(form).getByRole("combobox", { name: "Who" }),
       "email",
     );
-    await userEvent.type(within(form).getByRole("textbox", { name: "Principal" }), "a@example.org");
+    await userEvent.type(within(form).getByRole("textbox", { name: "Email address" }), "a@example.org");
     await userEvent.click(within(form).getByRole("button", { name: "Add administrator" }));
     await waitFor(() =>
       expect(mock.callsTo("POST /api/admin/console-admins")[0]?.body).toEqual({
@@ -273,9 +273,9 @@
     renderApp("/admin/grants", { session: ADMIN_SESSION })
       .on("GET /api/admin/grants", reply(200, []))
       .on("POST /api/admin/grants", fail(422, "invalid_scope", "scope <i>x</i>"));
-    const form = await screen.findByRole("form", { name: "Add a grant" });
-    await userEvent.type(within(form).getByRole("textbox", { name: "Principal" }), "a1");
-    await userEvent.click(within(form).getByRole("button", { name: "Add grant" }));
+    const form = await screen.findByRole("form", { name: "Give a role" });
+    await userEvent.type(within(form).getByRole("textbox", { name: "Group object ID" }), "a1");
+    await userEvent.click(within(form).getByRole("button", { name: "Give the role" }));
     const alert = await within(form).findByRole("alert");
     expect(alert).toHaveTextContent("That is not a way to say which leaders.");
     expect(alert.querySelector("i")).toBeNull();
````

- [ ] **Step 2: Run the tests to see them fail**

Run: `npx vitest run src/pages/admin/admin.test.tsx src/pages/admin/adminSecurity.test.tsx`

Expected: FAIL: 22 tests fail across the two files (14 in `admin.test.tsx`, 8 in `adminSecurity.test.tsx`): no heading "Administration", no button "Add a leader", no form "Give a role", no combobox "Who", no button "Replace credential for eu-1".

- [ ] **Step 3: Write the implementation**


<!-- file: src/App.tsx | patch -->
Change `packages/console-web/src/App.tsx`:

````diff
--- a/packages/console-web/src/App.tsx
+++ b/packages/console-web/src/App.tsx
@@ -5,9 +5,7 @@
 import { isRouted } from "./app/routes";
 import { SessionProvider } from "./app/session";
 import { Layout } from "./components/Layout";
-import { AdminAdminsPage } from "./pages/admin/AdminAdminsPage";
-import { AdminGrantsPage } from "./pages/admin/AdminGrantsPage";
-import { AdminLeadersPage } from "./pages/admin/AdminLeadersPage";
+import { AdminPage } from "./pages/admin/AdminPage";
 import { FleetPage } from "./pages/FleetPage";
 import { LeaderPage } from "./pages/leader/LeaderPage";
 import { leaderUrl } from "./pages/leader/tabs";
@@ -16,10 +14,12 @@
 
 /**
  * The page a path belongs to, for moving focus: switching tabs inside one leader's
- * drill-down stays on the page (focus stays on the tab link that was activated); going
- * from the fleet to a leader, or from one leader to another, changes the page.
+ * drill-down, or between the sections of Administration, stays on the page (focus stays on
+ * the tab link that was activated); going from the fleet to a leader, or from one leader to
+ * another, changes the page.
  */
-function pageOf(pathname: string): string {
+export function pageOf(pathname: string): string {
+  if (pathname === "/admin" || pathname.startsWith("/admin/")) return "/admin";
   const leader = matchPath("/leaders/:name/:tab", pathname) ?? matchPath("/leaders/:name", pathname);
   return leader === null ? pathname : `/leaders/${leader.name as string}`;
 }
@@ -40,9 +40,10 @@
   const bare = matchPath("/leaders/:name", pathname);
   if (bare !== null) return <Redirect to={leaderUrl(bare.name as string)} />;
   if (pathname === "/admin") return <Redirect to="/admin/leaders" />;
-  if (pathname === "/admin/leaders") return <AdminLeadersPage />;
-  if (pathname === "/admin/grants") return <AdminGrantsPage />;
-  if (pathname === "/admin/admins") return <AdminAdminsPage />;
+  // One component for all three, so its frame is not rebuilt when the section changes.
+  if (pathname === "/admin/leaders") return <AdminPage section="leaders" />;
+  if (pathname === "/admin/grants") return <AdminPage section="grants" />;
+  if (pathname === "/admin/admins") return <AdminPage section="admins" />;
   return <NotFoundPage />;
 }
 
````

<!-- file: src/pages/admin/AdminPage.tsx | create -->
Create `packages/console-web/src/pages/admin/AdminPage.tsx`:

````tsx
import { AdminAdminsSection } from "./AdminAdminsPage";
import { AdminFrame } from "./AdminFrame";
import { AdminGrantsSection } from "./AdminGrantsPage";
import { AdminLeadersSection } from "./AdminLeadersPage";

export type AdminSection = "leaders" | "grants" | "admins";

const TITLES: Record<AdminSection, string> = {
  leaders: "Leaders",
  grants: "Who can do what",
  admins: "Console administrators",
};

/**
 * Administration is one page with three sections. The frame (heading, sections' links) is
 * the same element for all three, so switching section keeps focus on the link that was
 * activated; only the section beneath it is replaced.
 */
export function AdminPage({ section }: { section: AdminSection }) {
  return (
    <AdminFrame title={TITLES[section]}>
      {section === "leaders" && <AdminLeadersSection />}
      {section === "grants" && <AdminGrantsSection />}
      {section === "admins" && <AdminAdminsSection />}
    </AdminFrame>
  );
}
````

<!-- file: src/pages/admin/AdminFrame.tsx | patch -->
Change `packages/console-web/src/pages/admin/AdminFrame.tsx`:

````diff
--- a/packages/console-web/src/pages/admin/AdminFrame.tsx
+++ b/packages/console-web/src/pages/admin/AdminFrame.tsx
@@ -7,7 +7,7 @@
 
 export const ADMIN_PAGES = [
   { path: "/admin/leaders", label: "Leaders" },
-  { path: "/admin/grants", label: "Grants" },
+  { path: "/admin/grants", label: "Who can do what" },
   { path: "/admin/admins", label: "Console administrators" },
 ] as const;
 
@@ -18,19 +18,26 @@
 }
 
 /**
- * The administration pages' frame: their own navigation, and a plain refusal for a person
- * who is not a console administrator (the API would answer 403 anyway). The children are
- * rendered only for an administrator, so nothing is requested for anyone else.
+ * The frame of Administration: one page with three sections, each its own address. It has
+ * one heading, what console administrators are for, the sections' navigation, and a plain
+ * refusal for a person who is not a console administrator (the API would answer 403
+ * anyway). The children are rendered only for an administrator, so nothing is requested
+ * for anyone else. `title` names the section in the browser tab.
+ * Mockup: docs/superpowers/design/AdminLeaders.dc.html.
  */
 export function AdminFrame({ title, children }: { title: string; children: ReactNode }) {
   const { session } = useSession();
   const { pathname } = useLocation();
-  usePageTitle(title);
+  usePageTitle(`Administration: ${title}`);
   return (
     <>
-      <h1>{title}</h1>
+      <h1>Administration</h1>
       {session.console_admin ? (
         <>
+          <p className="page-sub">
+            Console administrators decide which leaders this console talks to and who may use
+            them. Being one gives you no role on any leader by itself.
+          </p>
           <nav aria-label="Administration">
             <ul className="tab-list">
               {ADMIN_PAGES.map((page) => (
@@ -49,7 +56,7 @@
           {children}
         </>
       ) : (
-        <p>Console administration needs a console administrator. Ask one to add you.</p>
+        <p>Administration is for console administrators. Ask one to add you.</p>
       )}
     </>
   );
````

<!-- file: src/pages/admin/PrincipalFields.tsx | patch -->
Change `packages/console-web/src/pages/admin/PrincipalFields.tsx`:

````diff
--- a/packages/console-web/src/pages/admin/PrincipalFields.tsx
+++ b/packages/console-web/src/pages/admin/PrincipalFields.tsx
@@ -1,14 +1,39 @@
 import { useId } from "react";
 import type { PrincipalKind } from "../../api/types";
 
-export const PRINCIPAL_KINDS: { value: PrincipalKind; label: string; hint: string }[] = [
-  { value: "entra_group", label: "Entra ID group", hint: "the group's object ID (a GUID)" },
-  { value: "google_group", label: "Google group", hint: "the group's email address" },
-  { value: "email", label: "Email address", hint: "a Google account's address" },
-  { value: "domain", label: "Domain", hint: "a Google Workspace domain, such as example.org" },
+/**
+ * The four ways to name who gets a role or becomes a console administrator. `label` is the
+ * choice as a person reads it, `field` the name of the box that follows it, `hint` what to
+ * put there.
+ */
+export const PRINCIPAL_KINDS: { value: PrincipalKind; label: string; field: string; hint: string }[] = [
+  {
+    value: "entra_group",
+    label: "An Entra ID group",
+    field: "Group object ID",
+    hint: "The group's object ID in Entra ID, a GUID.",
+  },
+  {
+    value: "google_group",
+    label: "A Google group",
+    field: "Group address",
+    hint: "The group's email address.",
+  },
+  {
+    value: "email",
+    label: "One person, by email",
+    field: "Email address",
+    hint: "The address of a Google account.",
+  },
+  {
+    value: "domain",
+    label: "Everyone at a domain",
+    field: "Domain",
+    hint: "A Google Workspace domain, such as example.org.",
+  },
 ];
 
-/** Who a grant or a console administrator entry names: a kind and the principal itself. */
+/** Who a role or a console administrator entry names: a kind, then the name itself. */
 export function PrincipalFields({
   kind,
   principal,
@@ -21,11 +46,11 @@
   onPrincipal: (principal: string) => void;
 }) {
   const hintId = useId();
-  const hint = PRINCIPAL_KINDS.find((k) => k.value === kind)?.hint ?? "";
+  const chosen = PRINCIPAL_KINDS.find((k) => k.value === kind);
   return (
     <>
       <label className="field">
-        Principal kind
+        Who
         <select value={kind} onChange={(e) => onKind(e.target.value as PrincipalKind)}>
           {PRINCIPAL_KINDS.map((k) => (
             <option key={k.value} value={k.value}>
@@ -36,7 +61,7 @@
       </label>
       <div className="field">
         <label className="field">
-          Principal
+          {chosen?.field ?? "Name"}
           <input
             required
             autoComplete="off"
@@ -47,7 +72,7 @@
           />
         </label>
         <span id={hintId} className="field-help">
-          {hint}
+          {chosen?.hint ?? ""}
         </span>
       </div>
     </>
````

<!-- file: src/pages/admin/AdminLeadersPage.tsx | patch -->
Change `packages/console-web/src/pages/admin/AdminLeadersPage.tsx`:

````diff
--- a/packages/console-web/src/pages/admin/AdminLeadersPage.tsx
+++ b/packages/console-web/src/pages/admin/AdminLeadersPage.tsx
@@ -5,10 +5,9 @@
 import { ConfirmDialog } from "../../components/ConfirmDialog";
 import { Dialog } from "../../components/Dialog";
 import { ErrorPanel } from "../../components/ErrorPanel";
-import { formatTime } from "../../lib/format";
+import { countOf, formatTime } from "../../lib/format";
 import { ActionNotice, ReadState } from "../leader/common";
 import {
-  AdminFrame,
   labelsAreValid,
   labelsText,
   parseLabels,
@@ -49,7 +48,7 @@
 
 function checkAddress(address: string): void {
   if (!/^https:\/\/[^/\s]/i.test(address) || address.length > MAX_URL) {
-    refuse("invalid_url", "A leader URL is an https:// URL.");
+    refuse("invalid_url", "A leader's address starts with https://.");
   }
 }
 
@@ -205,10 +204,10 @@
             checked={enabled}
             onChange={(e) => setEnabled(e.target.checked)}
           />
-          Enabled
+          Switched on
         </label>
         {action.error !== null && <ErrorPanel error={action.error} />}
-        <DialogButtons busy={action.busy} submitLabel="Add leader" onCancel={action.close} />
+        <DialogButtons busy={action.busy} submitLabel="Add this leader" onCancel={action.close} />
       </form>
     </Dialog>
   );
@@ -291,7 +290,7 @@
             checked={enabled}
             onChange={(e) => setEnabled(e.target.checked)}
           />
-          Enabled
+          Switched on
         </label>
         {action.error !== null && <ErrorPanel error={action.error} />}
         <DialogButtons busy={action.busy} submitLabel="Save" onCancel={action.close} />
@@ -325,10 +324,10 @@
     });
   };
   return (
-    <Dialog title={`Rotate the credential for ${leader.name}`} onClose={action.close}>
+    <Dialog title={`Replace the credential for ${leader.name}`} onClose={action.close}>
       <form className="form-grid" noValidate onSubmit={submit}>
         <p>
-          Create a new console credential on the leader first, replace it here, then revoke the old
+          Make a new console credential on the leader first, paste it here, then revoke the old
           one on the leader.
         </p>
         <CredentialField
@@ -349,11 +348,13 @@
 
 type Open = { kind: "add" } | { kind: "edit" | "rotate" | "remove"; leader: LeaderOut } | null;
 
-function LeadersContent() {
+/** The Leaders section of Administration (pages/admin/AdminPage.tsx frames it). */
+export function AdminLeadersSection() {
   const read = useAdminList<LeaderOut>("/api/admin/leaders");
   const [open, setOpen] = useState<Open>(null);
   const [notice, setNotice] = useState<string | null>(null);
   const rows = useRowFocus(read, setNotice);
+  const calloutId = useId();
   const close = () => setOpen(null);
   const done = (message: string) => {
     setNotice(message);
@@ -363,19 +364,20 @@
   return (
     <div {...rows.props}>
       <div className="section-head">
+        <h2>{read.data === undefined ? "Leaders" : countOf(read.data.length, "leader")}</h2>
         <button
           type="button"
           className="button button-primary"
           onClick={() => setOpen({ kind: "add" })}
         >
-          Add leader
+          Add a leader
         </button>
       </div>
       <ActionNotice message={notice} />
       <ReadState read={read} what="leaders">
         {(leaders) =>
           leaders.length === 0 ? (
-            <p>No leaders are registered.</p>
+            <p>This console talks to no leaders yet.</p>
           ) : (
             <div className="table-scroll" role="region" aria-label="Registered leaders" tabIndex={0}>
               <table className="wide">
@@ -384,7 +386,7 @@
                     <th scope="col">Leader</th>
                     <th scope="col">Address</th>
                     <th scope="col">Labels</th>
-                    <th scope="col">Enabled</th>
+                    <th scope="col">State</th>
                     <th scope="col">Credential</th>
                     <th scope="col">Actions</th>
                   </tr>
@@ -395,7 +397,7 @@
                       <th scope="row">{leader.name}</th>
                       <td className="mono long">{leader.base_url}</td>
                       <td className="mono long">{labelsText(leader.labels) || "–"}</td>
-                      <td className="nowrap">{leader.enabled ? "Yes" : "No"}</td>
+                      <td className="nowrap">{leader.enabled ? "On" : "Switched off"}</td>
                       <td>
                         {leader.credential_revoked ? (
                           <span className="badge badge-bad">Revoked by the leader</span>
@@ -419,9 +421,9 @@
                           type="button"
                           className="button"
                           onClick={() => setOpen({ kind: "rotate", leader })}
-                          aria-label={`Rotate credential for ${leader.name}`}
+                          aria-label={`Replace credential for ${leader.name}`}
                         >
-                          Rotate credential
+                          Replace credential
                         </button>
                         <button
                           type="button"
@@ -460,7 +462,7 @@
       {open?.kind === "remove" && (
         <ConfirmDialog
           title={`Remove ${open.leader.name}?`}
-          message="The console forgets this leader, its history and the grants that name it. The leader itself is not changed; revoke the console's credential there too."
+          message="The console forgets this leader, its history and the roles given on it by name. The leader itself is not changed: revoke the console's credential there too."
           confirmLabel="Remove leader"
           onClose={close}
           onConfirm={async () => {
@@ -469,14 +471,20 @@
           }}
         />
       )}
+      <section className="sheet callout" aria-labelledby={calloutId}>
+        <h2 id={calloutId}>Adding a leader takes two steps</h2>
+        <ol>
+          <li>
+            On the leader, an admin runs <code>swarmscribe-admin console create</code> and copies the
+            credential it prints.
+          </li>
+          <li>
+            Here, choose <strong>Add a leader</strong> and paste the address and that credential. The
+            credential is never shown again.
+          </li>
+        </ol>
+      </section>
     </div>
   );
 }
 
-export function AdminLeadersPage() {
-  return (
-    <AdminFrame title="Leaders">
-      <LeadersContent />
-    </AdminFrame>
-  );
-}
````

<!-- file: src/pages/admin/AdminGrantsPage.tsx | patch -->
Change `packages/console-web/src/pages/admin/AdminGrantsPage.tsx`:

````diff
--- a/packages/console-web/src/pages/admin/AdminGrantsPage.tsx
+++ b/packages/console-web/src/pages/admin/AdminGrantsPage.tsx
@@ -6,7 +6,7 @@
 import { ErrorPanel } from "../../components/ErrorPanel";
 import { formatTime } from "../../lib/format";
 import { ActionNotice, ReadState } from "../leader/common";
-import { AdminFrame, useAdminList } from "./AdminFrame";
+import { useAdminList } from "./AdminFrame";
 import { PrincipalFields } from "./PrincipalFields";
 import { useRowFocus } from "../leader/rowFocus";
 
@@ -35,8 +35,8 @@
     });
   };
   return (
-    <form className="form-grid form-panel" aria-label="Add a grant" onSubmit={submit}>
-      <h2>Add a grant</h2>
+    <form className="form-grid form-panel" aria-label="Give a role" onSubmit={submit}>
+      <h2>Give a role</h2>
       <label className="field">
         Role
         <select value={role} onChange={(e) => setRole(e.target.value as Role)}>
@@ -47,7 +47,7 @@
       </label>
       <div className="field">
         <label className="field">
-          Scope
+          On which leaders
           <input
             required
             autoComplete="off"
@@ -58,7 +58,7 @@
           />
         </label>
         <span id={scopeHelp} className="field-help">
-          all, leader:&lt;name&gt; or label:&lt;key&gt;=&lt;value&gt;
+          Write all, leader:&lt;name&gt; or label:&lt;key&gt;=&lt;value&gt;.
         </span>
       </div>
       <PrincipalFields
@@ -74,14 +74,15 @@
           className="button button-primary"
           aria-disabled={action.busy || undefined}
         >
-          Add grant
+          Give the role
         </button>
       </div>
     </form>
   );
 }
 
-function GrantsContent() {
+/** The "Who can do what" section of Administration (pages/admin/AdminPage.tsx frames it). */
+export function AdminGrantsSection() {
   const read = useAdminList<GrantOut>("/api/admin/grants");
   const [removing, setRemoving] = useState<GrantOut | null>(null);
   const [notice, setNotice] = useState<string | null>(null);
@@ -89,25 +90,28 @@
 
   return (
     <div {...rows.props}>
-      <p className="muted">
-        A person's role on a leader is the highest grant whose scope matches it. Adding or removing a
-        grant applies at once. A person's group membership is read at sign-in, so a change to it
-        applies at their next sign-in.
+      <div className="section-head">
+        <h2>Who can do what</h2>
+      </div>
+      <p className="page-sub">
+        A person's role on a leader is the highest one given to them that covers it. Giving or
+        removing a role takes effect at once. Group membership is read when a person signs in, so
+        a change to a group shows the next time they do.
       </p>
       <ActionNotice message={notice} />
-      <ReadState read={read} what="grants">
+      <ReadState read={read} what="the roles">
         {(grants) =>
           grants.length === 0 ? (
-            <p>No grants.</p>
+            <p>Nobody has been given a role yet.</p>
           ) : (
             <div className="table-scroll" role="region" aria-label="Grants" tabIndex={0}>
               <table className="medium">
                 <thead>
                   <tr>
-                    <th scope="col">Principal</th>
+                    <th scope="col">Who</th>
                     <th scope="col">Role</th>
-                    <th scope="col">Scope</th>
-                    <th scope="col">Added</th>
+                    <th scope="col">On which leaders</th>
+                    <th scope="col">Given</th>
                     <th scope="col">Actions</th>
                   </tr>
                 </thead>
@@ -127,7 +131,7 @@
                           type="button"
                           className="button button-danger"
                           onClick={() => setRemoving(grant)}
-                          aria-label={`Remove grant: ${grant.role} on ${grant.scope} for ${grant.principal_kind}:${grant.principal}`}
+                          aria-label={`Remove ${grant.role} on ${grant.scope} from ${grant.principal_kind}:${grant.principal}`}
                         >
                           Remove
                         </button>
@@ -143,20 +147,20 @@
       <AddGrantForm
         onDone={(grant) => {
           setNotice(
-            `Grant added: ${grant.role} on ${grant.scope} for ${grant.principal_kind}:${grant.principal}.`,
+            `${grant.principal_kind}:${grant.principal} is now ${grant.role} on ${grant.scope}.`,
           );
           read.refresh();
         }}
       />
       {removing !== null && (
         <ConfirmDialog
-          title="Remove this grant?"
-          message={`${removing.principal_kind}:${removing.principal} loses ${removing.role} on ${removing.scope} at once. Group membership is read at sign-in.`}
-          confirmLabel="Remove grant"
+          title="Remove this role?"
+          message={`${removing.principal_kind}:${removing.principal} stops being ${removing.role} on ${removing.scope} at once.`}
+          confirmLabel="Remove the role"
           onClose={() => setRemoving(null)}
           onConfirm={async () => {
             await api.del(`/api/admin/grants/${encodeURIComponent(removing.id)}`);
-            rows.done("Grant removed.");
+            rows.done("The role is removed.");
           }}
         />
       )}
@@ -164,10 +168,3 @@
   );
 }
 
-export function AdminGrantsPage() {
-  return (
-    <AdminFrame title="Grants">
-      <GrantsContent />
-    </AdminFrame>
-  );
-}
````

<!-- file: src/pages/admin/AdminAdminsPage.tsx | patch -->
Change `packages/console-web/src/pages/admin/AdminAdminsPage.tsx`:

````diff
--- a/packages/console-web/src/pages/admin/AdminAdminsPage.tsx
+++ b/packages/console-web/src/pages/admin/AdminAdminsPage.tsx
@@ -6,7 +6,7 @@
 import { ErrorPanel } from "../../components/ErrorPanel";
 import { formatTime } from "../../lib/format";
 import { ActionNotice, ReadState } from "../leader/common";
-import { AdminFrame, useAdminList } from "./AdminFrame";
+import { useAdminList } from "./AdminFrame";
 import { PrincipalFields } from "./PrincipalFields";
 import { useRowFocus } from "../leader/rowFocus";
 
@@ -53,7 +53,8 @@
   );
 }
 
-function AdminsContent() {
+/** The Console administrators section of Administration (pages/admin/AdminPage.tsx frames it). */
+export function AdminAdminsSection() {
   const read = useAdminList<ConsoleAdminOut>("/api/admin/console-admins");
   const [removing, setRemoving] = useState<ConsoleAdminOut | null>(null);
   const [notice, setNotice] = useState<string | null>(null);
@@ -61,10 +62,9 @@
 
   return (
     <div {...rows.props}>
-      <p className="muted">
-        Console administrators manage leaders and grants. That gives them no role on any leader
-        unless a grant does.
-      </p>
+      <div className="section-head">
+        <h2>Console administrators</h2>
+      </div>
       <ActionNotice message={notice} />
       <ReadState read={read} what="console administrators">
         {(admins) => (
@@ -77,7 +77,7 @@
             <table className="medium">
               <thead>
                 <tr>
-                  <th scope="col">Principal</th>
+                  <th scope="col">Who</th>
                   <th scope="col">Added</th>
                   <th scope="col">Actions</th>
                 </tr>
@@ -110,19 +110,19 @@
       </ReadState>
       <AddAdminForm
         onDone={(admin) => {
-          setNotice(`${admin.principal_kind}:${admin.principal} is a console administrator.`);
+          setNotice(`${admin.principal_kind}:${admin.principal} is now a console administrator.`);
           read.refresh();
         }}
       />
       {removing !== null && (
         <ConfirmDialog
           title="Remove this console administrator?"
-          message={`${removing.principal_kind}:${removing.principal} can no longer manage leaders and grants. The last administrator cannot be removed.`}
+          message={`${removing.principal_kind}:${removing.principal} can no longer add leaders or give roles. The last administrator cannot be removed.`}
           confirmLabel="Remove administrator"
           onClose={() => setRemoving(null)}
           onConfirm={async () => {
             await api.del(`/api/admin/console-admins/${encodeURIComponent(removing.id)}`);
-            rows.done("Console administrator removed.");
+            rows.done("The console administrator is removed.");
           }}
         />
       )}
@@ -130,10 +130,3 @@
   );
 }
 
-export function AdminAdminsPage() {
-  return (
-    <AdminFrame title="Console administrators">
-      <AdminsContent />
-    </AdminFrame>
-  );
-}
````

- [ ] **Step 4: Run the unit tests to see them pass**

Run: `npm test`

Expected: PASS. **409 tests pass** (401 + 8 in `admin.test.tsx` (six in "administration frame" plus the two Review Focus tests)).

- [ ] **Step 5: Update the end-to-end tests**

`admin.spec.ts`: the new names, the heading's count, the callout, and focus staying on the
section's link. `a11y.spec.ts`: all three addresses have the `h1` "Administration", and a
viewer's refusal is scanned. `overview.spec.ts` and the screenshot spec open the add-leader
dialog by its new button names.

<!-- file: e2e/screens/capture.spec.ts | patch -->
Change `packages/console-web/e2e/screens/capture.spec.ts`:

````diff
--- a/packages/console-web/e2e/screens/capture.spec.ts
+++ b/packages/console-web/e2e/screens/capture.spec.ts
@@ -92,7 +92,7 @@
       await page.getByRole("button", { name: "I have stored it" }).click();
 
       await page.goto("/admin/leaders");
-      await page.getByRole("button", { name: "Add leader" }).click();
+      await page.getByRole("button", { name: "Add a leader" }).click();
       await shot("dialog-add-leader", false);
       await page.keyboard.press("Escape");
 
````

<!-- file: e2e/tests/a11y.spec.ts | patch -->
Change `packages/console-web/e2e/tests/a11y.spec.ts`:

````diff
--- a/packages/console-web/e2e/tests/a11y.spec.ts
+++ b/packages/console-web/e2e/tests/a11y.spec.ts
@@ -8,9 +8,9 @@
   { path: "/leaders/eu-1/locations", heading: "eu-1" },
   { path: "/leaders/eu-1/tokens", heading: "eu-1" },
   { path: "/leaders/eu-1/consent", heading: "eu-1" },
-  { path: "/admin/leaders", heading: "Leaders" },
-  { path: "/admin/grants", heading: "Grants" },
-  { path: "/admin/admins", heading: "Console administrators" },
+  { path: "/admin/leaders", heading: "Administration" },
+  { path: "/admin/grants", heading: "Administration" },
+  { path: "/admin/admins", heading: "Administration" },
   { path: "/no-such-page", heading: "Page not found" },
 ];
 
@@ -123,14 +123,14 @@
 
     test("the administration dialogs have no accessibility violations", async ({ page }) => {
       await signIn(page, "admin", "/admin/leaders");
-      await page.getByRole("button", { name: "Add leader" }).click();
+      await page.getByRole("button", { name: "Add a leader" }).click();
       await expectAccessible(page, `add leader dialog (${theme})`);
       await page.keyboard.press("Escape");
       await page.getByRole("button", { name: "Edit eu-1" }).click();
       await expectAccessible(page, `edit leader dialog (${theme})`);
       await page.keyboard.press("Escape");
-      await page.getByRole("button", { name: "Rotate credential for eu-1" }).click();
-      await expectAccessible(page, `rotate credential dialog (${theme})`);
+      await page.getByRole("button", { name: "Replace credential for eu-1" }).click();
+      await expectAccessible(page, `replace credential dialog (${theme})`);
       await page.keyboard.press("Escape");
       await page.getByRole("button", { name: "Remove eu-1" }).click();
       await expectAccessible(page, `remove leader confirm dialog (${theme})`);
````

<!-- file: e2e/tests/admin.spec.ts | patch -->
Change `packages/console-web/e2e/tests/admin.spec.ts`:

````diff
--- a/packages/console-web/e2e/tests/admin.spec.ts
+++ b/packages/console-web/e2e/tests/admin.spec.ts
@@ -8,50 +8,59 @@
 }) => {
   await signIn(page, "admin");
   await page.getByRole("link", { name: "Administration" }).click();
-  await page.getByRole("link", { name: "Grants" }).click();
-  const form = page.getByRole("form", { name: "Add a grant" });
+  await expect(page.getByRole("heading", { level: 1, name: "Administration" })).toBeFocused();
+  const who = page.getByRole("link", { name: "Who can do what" });
+  await who.click();
+  // A section of the same page: focus stays on its link, as on a leader's tabs.
+  await expect(who).toBeFocused();
+  await expect(who).toHaveAttribute("aria-current", "page");
+  await expect(page.getByRole("heading", { level: 2, name: "Who can do what" })).toBeVisible();
+  const form = page.getByRole("form", { name: "Give a role" });
   await form.getByRole("combobox", { name: "Role" }).selectOption("operator");
-  await form.getByRole("textbox", { name: "Scope" }).fill("label:region=eu");
-  await form.getByRole("combobox", { name: "Principal kind" }).selectOption("domain");
-  await form.getByRole("textbox", { name: "Principal" }).fill("example.org");
-  await form.getByRole("button", { name: "Add grant" }).click();
-  await expect(
-    page.getByText("Grant added: operator on label:region=eu for domain:example.org."),
-  ).toBeVisible();
+  await form.getByRole("textbox", { name: "On which leaders" }).fill("label:region=eu");
+  await form.getByRole("combobox", { name: "Who" }).selectOption("domain");
+  await form.getByRole("textbox", { name: "Domain" }).fill("example.org");
+  await form.getByRole("button", { name: "Give the role" }).click();
+  await expect(page.getByText("domain:example.org is now operator on label:region=eu.")).toBeVisible();
 
   // A second grant for the same principal: the two remove buttons still differ by name.
   await form.getByRole("combobox", { name: "Role" }).selectOption("viewer");
-  await form.getByRole("textbox", { name: "Scope" }).fill("all");
-  await form.getByRole("textbox", { name: "Principal" }).fill("example.org");
-  await form.getByRole("button", { name: "Add grant" }).click();
-  await expect(page.getByText("Grant added: viewer on all for domain:example.org.")).toBeVisible();
-  const removers = page.getByRole("button", { name: /^Remove grant: .* for domain:example\.org$/ });
+  await form.getByRole("textbox", { name: "On which leaders" }).fill("all");
+  await form.getByRole("textbox", { name: "Domain" }).fill("example.org");
+  await form.getByRole("button", { name: "Give the role" }).click();
+  await expect(page.getByText("domain:example.org is now viewer on all.")).toBeVisible();
+  const removers = page.getByRole("button", { name: /^Remove .* from domain:example\.org$/ });
   await expect(removers).toHaveCount(2);
   const names = await removers.evaluateAll((els) => els.map((el) => el.getAttribute("aria-label")));
   expect(new Set(names).size).toBe(2);
 
-  const operatorGrant = "Remove grant: operator on label:region=eu for domain:example.org";
+  const operatorGrant = "Remove operator on label:region=eu from domain:example.org";
   await page.getByRole("button", { name: operatorGrant }).click();
-  await page.getByRole("alertdialog").getByRole("button", { name: "Remove grant" }).click();
-  await expect(page.getByText("Grant removed.")).toBeVisible();
+  await page.getByRole("alertdialog").getByRole("button", { name: "Remove the role" }).click();
+  await expect(page.getByText("The role is removed.")).toBeVisible();
   await expect(page.getByRole("button", { name: operatorGrant })).toHaveCount(0);
   await expect(removers).toHaveCount(1);
 });
 
 test("a console administrator registers, rotates and removes a leader", async ({ page }) => {
   await signIn(page, "admin", "/admin/leaders");
-  await page.getByRole("button", { name: "Add leader" }).click();
+  await expect(page.getByRole("heading", { level: 2, name: "2 leaders" })).toBeVisible();
+  await expect(page.getByRole("region", { name: "Adding a leader takes two steps" })).toContainText(
+    "swarmscribe-admin console create",
+  );
+  await page.getByRole("button", { name: "Add a leader" }).click();
   const dialog = page.getByRole("dialog", { name: "Add a leader" });
   await dialog.getByRole("textbox", { name: "Name" }).fill("ap-1");
   await dialog.getByRole("textbox", { name: "Address (https://)" }).fill("https://ap-1.leaders.example");
   await dialog.getByRole("textbox", { name: "Labels" }).fill("region=ap");
   await dialog.getByLabel("Console credential").fill(CREDENTIAL);
-  await dialog.getByRole("button", { name: "Add leader" }).click();
+  await dialog.getByRole("button", { name: "Add this leader" }).click();
   await expect(page.getByText("Leader ap-1 is added.")).toBeVisible();
+  await expect(page.getByRole("heading", { level: 2, name: "3 leaders" })).toBeVisible();
   expect(await page.content()).not.toContain(CREDENTIAL);
 
-  await page.getByRole("button", { name: "Rotate credential for ap-1" }).click();
-  const rotate = page.getByRole("dialog", { name: "Rotate the credential for ap-1" });
+  await page.getByRole("button", { name: "Replace credential for ap-1" }).click();
+  const rotate = page.getByRole("dialog", { name: "Replace the credential for ap-1" });
   await rotate.getByLabel("New console credential").fill(CREDENTIAL);
   await rotate.getByRole("button", { name: "Replace credential" }).click();
   await expect(page.getByText("The credential for ap-1 is replaced.")).toBeVisible();
@@ -73,12 +82,12 @@
 
 test("the console refuses a leader address it may not call", async ({ page }) => {
   await signIn(page, "admin", "/admin/leaders");
-  await page.getByRole("button", { name: "Add leader" }).click();
+  await page.getByRole("button", { name: "Add a leader" }).click();
   const dialog = page.getByRole("dialog", { name: "Add a leader" });
   await dialog.getByRole("textbox", { name: "Name" }).fill("local-1");
   await dialog.getByRole("textbox", { name: "Address (https://)" }).fill("https://localhost");
   await dialog.getByLabel("Console credential").fill(CREDENTIAL);
-  await dialog.getByRole("button", { name: "Add leader" }).click();
+  await dialog.getByRole("button", { name: "Add this leader" }).click();
   await expect(dialog.getByRole("alert")).toContainText("The console may not call that address.");
 });
 
@@ -94,5 +103,5 @@
   await signIn(page, "operator");
   await expect(page.getByRole("link", { name: "Administration" })).toHaveCount(0);
   await page.goto("/admin/leaders");
-  await expect(page.getByText(/Console administration needs a console administrator/)).toBeVisible();
+  await expect(page.getByText(/Administration is for console administrators/)).toBeVisible();
 });
````

<!-- file: e2e/tests/overview.spec.ts | patch -->
Change `packages/console-web/e2e/tests/overview.spec.ts`:

````diff
--- a/packages/console-web/e2e/tests/overview.spec.ts
+++ b/packages/console-web/e2e/tests/overview.spec.ts
@@ -221,13 +221,13 @@
   const name = `L${"o".repeat(99)}`;
   const label = `note=${"v".repeat(200)}`;
   await signIn(page, "admin", "/admin/leaders");
-  await page.getByRole("button", { name: "Add leader" }).click();
+  await page.getByRole("button", { name: "Add a leader" }).click();
   const dialog = page.getByRole("dialog", { name: "Add a leader" });
   await dialog.getByRole("textbox", { name: "Name" }).fill(name);
   await dialog.getByRole("textbox", { name: "Address (https://)" }).fill("https://long.leaders.example");
   await dialog.getByRole("textbox", { name: "Labels" }).fill(label);
   await dialog.getByLabel("Console credential").fill("c".repeat(20) + "_-" + "D".repeat(21));
-  await dialog.getByRole("button", { name: "Add leader" }).click();
+  await dialog.getByRole("button", { name: "Add this leader" }).click();
   await expect(dialog).toHaveCount(0);
 
   for (const width of [1280, 768, 390]) {
````

- [ ] **Step 6: Type-check, lint, build and run the end-to-end tests**

Run, in `packages/console-web`:

```bash
npm run typecheck
npm run lint
npm test
npm run build
npm run e2e -- --retries=0
```

Expected: `tsc` prints nothing. ESLint prints nothing (0 warnings). Vitest: **409 tests pass**, none
fail. The build ends with `dist/ ok: index.html and 3 hashed assets`. Playwright: the whole end-to-end suite passes, with no retry.

If an end-to-end test fails, read what it was checking before changing it: the page is wrong
far more often than the test. Nothing may be left listening on ports 8900 or 8901 afterwards.

- [ ] **Step 7: Commit**

```bash
git add packages/console-web/src/App.tsx packages/console-web/src/pages/admin/AdminPage.tsx packages/console-web/src/pages/admin/AdminFrame.tsx packages/console-web/src/pages/admin/PrincipalFields.tsx packages/console-web/src/pages/admin/AdminLeadersPage.tsx packages/console-web/src/pages/admin/AdminGrantsPage.tsx packages/console-web/src/pages/admin/AdminAdminsPage.tsx packages/console-web/src/pages/admin/admin.test.tsx packages/console-web/src/pages/admin/adminSecurity.test.tsx packages/console-web/e2e/screens/capture.spec.ts packages/console-web/e2e/tests/a11y.spec.ts packages/console-web/e2e/tests/admin.spec.ts packages/console-web/e2e/tests/overview.spec.ts
git commit -m "Console web app: Administration as one page with three sections; roles are given, not granted

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

### Task 7: README, and screenshots to look at

**Files:**
- Modify: `packages/console-web/README.md`

**Interfaces:**
- Consumes: everything above; `npm run screens` from R1.
- Produces: nothing new in code.

The README says how the console talks. Then every screen is photographed and looked at.

- [ ] **Step 1: Update the README**

<!-- file: README.md | patch -->
Change `packages/console-web/README.md`:

````diff
--- a/packages/console-web/README.md
+++ b/packages/console-web/README.md
@@ -1,8 +1,8 @@
 # swarmscribe-console-web
 
 The fleet console's web app: the fleet overview, the leader pages (Jobs, Pools and
-followers, Locations, Consent, Join tokens) with their actions, and the Administration pages
-(leaders, grants, console administrators). React and TypeScript, built with Vite. The console
+followers, Locations, Consent, Join tokens) with their actions, and Administration (leaders,
+who can do what, console administrators). React and TypeScript, built with Vite. The console
 (`packages/console`) serves the built files; this package has no server of its own.
 
 ## Requirements
@@ -52,6 +52,7 @@
 | `shell.css` | The rail, the top bar at narrow widths, page header, tabs. |
 | `fleet.css` | The fleet overview: totals, leader cards, the chart. |
 | `signin.css` | The sign-in page. |
+| `detail.css` | A leader's pages and Administration: list toolbars, job states, the one-time token. |
 
 A colour is never written in a component or in any file but `tokens.css` (the mark's two
 brand constants in `shell.css` are the exception). `src/styles/tokens.test.ts` measures the
@@ -61,6 +62,13 @@
 
 There are no inline styles, no web fonts and no images from another origin. The logo is
 inline SVG (`src/components/Brand.tsx`); the favicon is a hashed asset.
+
+The console says things its own way, and the spec's copy table is the list: a leader is
+"answering" or "not answering", a job is "waiting", "with follower 1a2b3c4d", "finished" or
+"failed" and can be "tried again", a location or a leader is "switched off", and roles are
+"given". The leader's own words (queued, leased, retry, poll, grant, scope) stay in the API
+and in addresses such as `?state=queued`; they are not shown to a person. A button's
+accessible name always contains its visible text, word for word.
 
 ## End-to-end tests
 
````

- [ ] **Step 2: Take the screenshots and LOOK at them**

Run, in `packages/console-web`:

```bash
npm run build
npm run screens
```

Expected: 4 tests pass and `screens/` holds about 100 PNG files.

Open them. This step is not done until a person, or an agent that can see images, has
looked at every one, in both themes and at both widths. For each image: nothing cut off at
the right edge, nothing on top of anything else, text readable against what is behind it.
Then, against the mockups in `docs/superpowers/design/`:

- **`leader-jobs-*`** beside `LeaderJobs.dc.html`: breadcrumb, name with labels, status pill,
  the role line, tabs with an amber rule under Jobs, pills with counts, the table with State
  in words and a note under each recording, "Try again" filled on the failed job.
- **`leader-jobs-failed-*`**: the Failed pill is the filled one.
- **`leader-pools-*`, `leader-locations-*`, `leader-tokens-*`, `leader-consent-*`**: "Loaded
  at" beside Refresh; the locations table shows all six columns at 1280 without scrolling.
- **`dialog-token-shown-*`** beside `TokenDialog.dc.html`: serif title, the token in a
  bordered field with Copy beside it, "Not copied yet.", three facts, "I have stored it" at
  the bottom right. **`dialog-token-asking-*`**: the question is shown under the status.
- **`dialog-confirm-*`**: "No, go back" first, the red action second.
- **`dialog-form-errors-*`**: each error is under its field in red, and the field has a
  thicker red edge.
- **`admin-leaders-*`** beside `AdminLeaders.dc.html`: "Administration", the paragraph, three
  section links, "3 leaders" with "Add a leader" on the right, the table, the callout sheet.
- **`admin-grants-*`, `admin-admins-*`**: the section heading, the table, the form in a panel.
- **`leader-jobs-as-viewer-*`**: every action has a dashed edge and "needs operator" beneath.
- **At 768**: the Actions column is at the right edge of each table with its buttons
  stacked, and never covers more than half the table.
- **Light theme throughout**: paper page, ink rail, ink dialogs and callouts with the amber
  shadow; nothing left in the dark theme's colours.

Write down anything that looks wrong, fix it in the stylesheet (never with an inline style),
rerun this step, and say in the commit message what was fixed. If nothing was wrong, say
that the screenshots were looked at.

- [ ] **Step 3: Type-check, lint, build and run the end-to-end tests**

Run, in `packages/console-web`:

```bash
npm run typecheck
npm run lint
npm test
npm run build
npm run e2e -- --retries=0
```

Expected: `tsc` prints nothing. ESLint prints nothing (0 warnings). Vitest: **409 tests pass**, none
fail. The build ends with `dist/ ok: index.html and 3 hashed assets`. Playwright: the whole end-to-end suite passes, with no retry.

If an end-to-end test fails, read what it was checking before changing it: the page is wrong
far more often than the test. Nothing may be left listening on ports 8900 or 8901 afterwards.

- [ ] **Step 4: Commit**

```bash
git add packages/console-web/README.md
git commit -m "Console web app: README says how the console talks; redesign screenshots looked at

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

## Self-review

Run when the plan was written:

1. **Spec coverage.** 4.8 to 4.13: R1's stylesheet plus `detail.css` (Task 2). 5.4 header:
   Task 2; Jobs: Task 3; Pools, Locations, Consent: Task 4; Join tokens and 5.5: Task 5. 5.6:
   Task 6. 5.7 (other dialogs): Task 1 for the confirmation, R1 for the sheet. 6.2: Shared
   pieces and Error titles in Task 1; A leader's page in Task 2; Jobs in Task 3; Pools,
   Locations, Consent in Task 4; Join tokens in Task 5; Administration in Task 6. 7.3 (focus
   stays on a section's link): Task 6. 7.8 for a viewer: Task 3. 7.12 (names contain the
   visible label): Tasks 3, 4 and 6. Section 12's screenshots: Task 7.
2. **Placeholders.** None.
3. **Names.** `jobStateText`, `jobNote`, `STATE_PILLS`, `followerStateText`, `channels`,
   `deviceText`, `AdminPage`, `AdminLeadersSection`, `AdminGrantsSection`,
   `AdminAdminsSection`, `pageOf` and `PRINCIPAL_KINDS` are spelled the same where they are
   made, used and tested. Every class used is in R1's stylesheet or Task 2's `detail.css`.
4. **Review Focus.** Five items, each with a named test in a named task.
5. **Replay.** The plan's blocks were applied in order to R1's final tree and the result
   compared with the tree the tasks were verified on: identical.

## Execution

Plan complete and saved to
`docs/superpowers/plans/2026-10-04-console-redesign-r2-leader-and-admin.md`. Two ways to run it:

- **Subagent-driven** (recommended): a fresh subagent per task and a fresh reviewer after
  each. Seven tasks that touch the same test files in turn; a reviewer per task catches a
  string changed in the wrong one.
- **Native:** one session does every task, then one reviewer checks the branch.

Either way, Task 7's screenshots are looked at before the branch is called done.
