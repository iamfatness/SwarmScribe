# Fleet Console C3a — Web App: Shell, Fleet Overview and CI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A React + TypeScript single-page app in `packages/console-web`, built to `dist/` and served by the console backend under its Content Security Policy, in which a person signs in, sees every leader they hold a role on (health, queue depth, completions in the last hour and day, failures in the last day, active followers by pool, the oldest queued job's age "since created", last scan errors, a 24-hour throughput chart), filters them by label, and is sent back to sign-in when the session ends; with unit and component tests, Playwright end-to-end tests against the real console with two fake leaders and a fake Entra ID, an axe scan of every page in both themes, and two CI jobs.

**Architecture:** One Vite build with no runtime dependencies beyond React. `src/api/client.ts` is the only code that calls the console (CSRF header on unsafe calls, `{code, message}` errors as `ApiError`, any 401 → sign-in). `src/app/` holds the primitives: a small history-API router, the session provider, `usePoll` (interval refresh that pauses while the tab is hidden or the person is idle), the theme and the fleet context that polls `GET /api/fleet` every 10 s. Pages are plain components; the chart is hand-written SVG. The end-to-end harness (`e2e/harness/serve.py`) starts the real `create_app` with the test seams it already has (`fetch`, `idp_transport`, `leader_transport`) plus a separate control server on another port, so no test-only route exists in the console or the bundle.

**Tech Stack:** Node 24, Vite 8.3.2, React 19.3.0, TypeScript 6.0.3, Vitest 5.0.3 with Testing Library and jsdom, ESLint 9.39.5 (typescript-eslint, react-hooks, jsx-a11y), Playwright 1.63.0 with @axe-core/playwright 4.13.0; Python 3.11+ and uv for the harness.

**Spec:** `docs/superpowers/specs/2026-10-03-fleet-console-design.md` (approved; the authority) — section 6 and section 9's web app line are C3's scope, with sections 1 (success criteria), 5.2, 5.4 and 7 where they bind the browser. The backend contract is `docs/superpowers/plans/2026-10-03-fleet-console-c3-notes.md` (the C2b handoff) and the code in `packages/console/src/swarmscribe_console/api/`. This is the first of two plans; C3b is `docs/superpowers/plans/2026-10-04-fleet-console-c3b-web-app-actions.md`.

**Precondition:** C1, C1b, C2a and C2b are merged to `main`, and this work branches from `main`:

```bash
git switch main
git pull
git switch -c fleet-console-c3a
```

Before Task 1, confirm `node --version` prints `v24.x`, `python -m uv run pytest packages/console -q` passes, and `packages/console/src/swarmscribe_console/static.py` and `packages/console/src/swarmscribe_console/proxy.py` exist.

## Why two plans

C3 is thirteen tasks. C3a (this plan, six tasks) is the scaffold, the signed-in shell, the fleet overview with history, the end-to-end harness and CI: a working, deployable read-only console. C3b (seven tasks) adds the leader drill-down with every action, the administration pages and their end-to-end tests. C3b needs C3a merged; C3a needs nothing from C3b.

## Global Constraints

- Spec 6, verbatim: "**Fleet overview:** a row per leader — reachable/unreachable, queue depth, jobs completed last hour/day, failures last day, followers active by pool, oldest queued job age, last scan errors — filterable by label; a small 24-hour throughput chart per leader."; "Accessible (WCAG 2.1 AA: keyboard, contrast, labels), responsive down to tablet width, light and dark themes."
- Spec 9, verbatim: "Web app: component tests for overview and drill-down; end-to-end with Playwright against a console and two fake leaders; an automated accessibility scan (axe) on every page."
- Spec 1, verbatim: "refreshed at least every 30 seconds"; "A leader that stops answering is shown as unreachable within one minute; nothing else in the console stops working."
- Spec 5.2, verbatim: "8-hour lifetime, idle timeout 1 hour; CSRF token required on every state-changing request."
- Spec 7, verbatim: "Content Security Policy restricts scripts to the console's own origin."
- The CSP the console sends (`packages/console/src/swarmscribe_console/api/security.py`): `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'`. So: no inline `<script>` or `<style>`, no `style=""` attribute, no CDN, no external font, nothing fetched from another origin.
- Handoff note: every unsafe `/api` call needs `X-CSRF-Token` (from `GET /api/session`) and a same-origin `Origin`; a 401 from any `/api` route is always the console's own session; app routes are extensionless and outside `/api` and `/auth` (a dot in the last path segment returns 404 instead of `index.html`); only filenames that look hashed (a `.` or `-`, then 8 or more letters, digits or underscores with at least one digit, then the extension) get the one-year `immutable` header.
- Handoff note, health values: `disabled`, `credential_revoked`, `unreachable`, `pending`, `reachable`; `summary` and `snapshot` are the latest successful poll and can be stale — show `snapshot.taken_at`; `oldest_queued_age_s` is as of `taken_at` and null when nothing is queued — add the time since `taken_at` when showing it, labelled "since created".
- TypeScript types mirror the backend's response models field for field (`api/session.py` `SessionOut`, `api/fleet.py` `FleetLeader`/`FleetSummary`/`HistoryPoint`, `api/models.py`, and the leader's `api/admin_models.py`). Datetimes are ISO strings.
- Dependency versions are exact (no `^` or `~`), as listed in Task 1's `package.json`. Do not add a dependency this plan does not name.
- Nothing is stored in the browser except the theme choice. No `console.*` calls. No `style` props and no `dangerouslySetInnerHTML` (lint enforces all three).
- The code in this plan was written against `noUncheckedIndexedAccess`, typescript-eslint `strict`, `eslint-plugin-jsx-a11y` `strict` and `eslint-plugin-react-hooks` 7 (which refuses `Date.now()` during render and `setState` called synchronously in an effect). If lint flags code copied from this plan, the copy differs from the plan: compare before changing behaviour.
- npm commands run in `packages/console-web`. On this Windows machine `uv` is not on PATH: Python commands are written `python -m uv run …` and run from the repository root.
- No file under `packages/console/` or `packages/leader/` changes in this plan.
- Commits end with a blank line and `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Do not push.

## Review Focus

Conditions the spec implies but does not spell out. Each line names what a reasonable person expects and the task whose tests pin it.

1. **Stale figures shown as current.** A leader that is unreachable, revoked or disabled still has a `summary` (the last successful poll). Its row shows the figures muted with "Figures as of <time>" and its real health, never as live numbers. — Task 5 (`FleetPage.test.tsx`), Task 6 (`overview.spec.ts`).
2. **A session that ends while the page is open** (idle timeout, 8-hour lifetime, sign-out elsewhere, server restart) → the next request's 401 sends the person to `/sign-in?return_to=<where they were>` by a full page load, so no data from the ended session stays on screen. — Task 2 (`client.test.ts`), Task 3 (`session.test.tsx`), Task 6 (`sign-in.spec.ts`).
3. **A tab left open for hours** → background refreshes stop after 55 minutes without keyboard or pointer input, so the console's one-hour idle timeout still ends the session; input resumes them. — Task 3 (`usePoll.test.tsx`).
4. **Names that need care in a URL**: a leader named `eu-1.prod` (dots are allowed), a label value containing `=` (allowed), a filter that must survive a reload. — Task 2 (`leaderPath`), Task 3 (`matchPath`), Task 5 (the `team=a=b` filter test).
5. **Clock skew** between the browser and the console (the snapshot's `taken_at` in the browser's future) → the oldest-queued age never goes below the leader's own figure. — Task 4 (`format.test.ts`).
6. **The console answering something that is not `{code, message}`**, or not answering at all (a proxy's HTML 502, the network down) → an `ApiError` with a status-based code and a readable message; the overview keeps its last figures and says the refresh failed. — Task 2 (`client.test.ts`), Task 5 (`FleetPage.test.tsx`).
7. **A CSP violation in the built app** (an inline style from a library, a hash-less asset name) → the build fails (`scripts/check-dist.mjs`), and every end-to-end test fails if the browser reports a Content Security Policy error. — Task 1, Task 6 (`support.ts` guard).

## Decisions this plan makes (the spec is silent)

- **Location.** `packages/console-web/`, beside the backend it is served by. The root `pyproject.toml` workspace is `packages/*`, and uv refuses a member without a `pyproject.toml`, so Task 1 adds `exclude = ["packages/console-web"]` to `[tool.uv.workspace]` (checked: `uv lock --locked` is unchanged by it).
- **Toolchain, pinned exactly:** vite 8.3.2, @vitejs/plugin-react 6.1.1, react and react-dom 19.3.0, typescript 6.0.3, vitest 5.0.3, jsdom 30.1.2, @testing-library/react 16.3.3, @testing-library/dom 10.4.2, @testing-library/user-event 14.6.7, @testing-library/jest-dom 7.0.1, eslint 9.39.5, @eslint/js 9.39.5, typescript-eslint 8.71.0, eslint-plugin-react-hooks 7.1.1, eslint-plugin-jsx-a11y 6.10.2, globals 17.13.0, @playwright/test 1.63.0, @axe-core/playwright 4.13.0, @types/react and @types/react-dom 19.3.0, @types/node 24.19.1.
- **TypeScript 6.0.3, not 7; ESLint 9, not 10.** typescript-eslint 8.71.0 requires `typescript <6.1.0`, and eslint-plugin-jsx-a11y 6.10.2 supports ESLint up to 9. Move both when those two packages do.
- **No router library.** The app has a handful of fixed routes and no nested data loading; `src/app/router.tsx` is about a hundred lines on the History API with its own tests.
- **No data-fetching library.** Plain `fetch` in `src/api/client.ts` and one hook, `usePoll`.
- **The chart is hand-written SVG** (`src/components/Sparkline.tsx`): the CSP forbids libraries that inject `<style>`, and a sparkline does not need one.
- **What the chart plots.** `completed_last_hour` from each 5-minute history bucket, as a line: each point is the leader's completions in the hour before that snapshot. The line breaks where a bucket is missing or the leader was unreachable, and unreachable buckets are marked along the bottom edge. Its accessible name states the latest and highest values and the count of unreachable periods.
- **Build output contract.** `npm run build` writes `packages/console-web/dist/`: `index.html` and `assets/<name>-<16 hex characters>.<ext>` (Rollup `hashCharacters: "hex"`), which matches the console's "looks hashed" rule; `scripts/check-dist.mjs` runs after every build and refuses one that the console would serve wrongly (inline script or style, a `style` attribute, an off-origin URL, an un-hashed asset name, or harness or test code in the bundle). `SWARMSCRIBE_CONSOLE_STATIC_DIR` points at that folder. C4 copies `dist/` into the image and sets the variable; nothing else about packaging is decided here.
- **No Vite dev server workflow.** The dev server injects inline scripts and runs on another origin (the CSRF `Origin` check would refuse it). Development is `npm run watch` (rebuilds `dist/`) with the end-to-end harness serving it.
- **Refresh.** `GET /api/fleet` every 10 s for the whole app (it reads the console's own database). Ten, not fifteen: the poller records a dead leader's third failure up to about 50 s after its death, so 10 s keeps "shown as unreachable within one minute". History is read every 5 minutes (its buckets are 5 minutes). Refreshes pause while the tab is hidden.
- **Idle.** After 55 minutes without keyboard, pointer, wheel or touch input the app stops its background refreshes and says so; the next input resumes them. Without this, an open tab would keep `last_seen_at` fresh for the full 8 hours and the spec's one-hour idle timeout would never apply (see "Spec problems").
- **Session end.** Any 401 from `/api` → `window.location.assign("/sign-in?return_to=<path and query>")`: a full load, so all in-memory state is dropped. `return_to` is checked in the browser (a path on this origin, not `/sign-in`, `/api` or `/auth`) and again by the backend's `safe_return_to`.
- **Error text.** Each error code has a fixed title of ours (`src/api/errors.ts`); the server's own message, which is fixed text by design, is shown as the detail, always as text. `leader_not_found` reads "This leader is not visible to you." An unknown code falls back by status.
- **Roles.** `src/api/roles.ts` is a copy of the allow-list's roles in `proxy.py`; `roles.test.ts` reads `proxy.py` and fails when they differ. (Used by C3b; it lives here with the API layer.)
- **Themes.** Colour tokens as CSS custom properties; `prefers-color-scheme` decides unless the person picks Light or Dark in the header, which is stored in `localStorage` under `swarmscribe-console-theme` and applied as `<html data-theme>`. System font stack; no web fonts.
- **Health wording.** `reachable` → "Reachable" (or "Reachable (1 failed poll)"); `pending` → "Waiting for first poll" or "Not answering yet (N failed polls)"; `unreachable` → "Unreachable"; `credential_revoked` → "Credential revoked"; `disabled` → "Disabled in the console". Health is always words in a badge; colour is never the only signal.
- **Tablet width.** The overview is a real `<table>` inside a focusable, horizontally scrolling region; the page itself never scrolls sideways at 768 px.
- **Label filter.** One label (`key=value`) at a time, chosen from the labels the visible leaders have, kept in the address as `?label=`.
- **Leader names in C3a are plain text.** C3b turns them into links to the drill-down.
- **End-to-end harness.** `packages/console-web/e2e/harness/serve.py` runs the real `create_app` on `http://localhost:8900` with `fetch`/`idp_transport` answered by an in-memory Entra ID and `leader_transport` by two in-memory, stateful leaders (`eu-1`, capped at admin; `us-1`, capped at operator), the real poller every 2 s, and a console database `swarmscribe_console_e2e` on `SWARMSCRIBE_TEST_DATABASE_URL` or the repository's pgserver. It reuses `packages/console/tests/console_testkit.py` for keys, the credential, the Entra ids and the database helpers; the fake Entra ID and fake leaders are the harness's own (the testkit's `FakeLeader` is stateless and the fake identity providers live in `conftest.py`).
- **Signing in end to end.** The console's authorization endpoint is the real `login.microsoftonline.com`, so Playwright answers that navigation itself (`page.route`), asks the harness's control server to authorize a persona for that URL, and visits the callback. Personas: `viewer`, `operator`, `admin` (also a console administrator).
- **No test-only route ships.** The control server is a second app on port 8901 in the harness process; `create_app` is not changed; the harness lives outside `packages/console/src`; `check-dist.mjs` refuses a bundle that mentions the harness.
- **Chromium only** for end-to-end tests. The session cookie is `__Host-` and `Secure`; Chromium accepts that on `http://localhost` (verified), which is how the harness is reached.
- **CI.** Job `web`: `npm ci`, type-check, lint, unit tests, build. Job `web-e2e`: Postgres service, `uv sync`, `npm ci`, Playwright's Chromium, build, `npm run e2e` (Playwright starts the harness through `E2E_HARNESS_COMMAND`).

## Questions for the owner

None blocks this plan: each has a recommendation, and the plan is written to it.

1. **Visual identity.** The plan uses a neutral operations palette with SwarmScribe's amber as the accent on an ink-dark dark theme (tokens in `src/styles.css`, every pair checked for AA contrast) and the system font. *Recommendation: accept; a brand pass can change the tokens alone.*
2. **Default theme.** *Recommendation: follow the system setting (as planned), with the header switch.*
3. **Tablet layout of the overview.** A table that scrolls inside its own region (planned) or a card per leader below about 1000 px. *Recommendation: the table; cards lose the column comparison operators come for.*
4. **Idle timeout and polling.** Stop background refreshes in the browser after 55 minutes idle (planned), or change the backend so background reads do not refresh `last_seen_at`. *Recommendation: the browser-side stop now; consider the backend change as a C2 follow-up, because a modified client could still keep a session alive for its 8 hours.*
5. **Throughput metric.** The rolling "completed in the last hour" line (planned) or completions per 5-minute bucket, which needs a new figure from the leader. *Recommendation: the rolling line.*
6. **Label filter.** One label at a time (planned) or several combined. *Recommendation: one.*
7. **Browsers under test.** Chromium only (planned) or Firefox and WebKit too. *Recommendation: Chromium only; the app uses only standard platform features, and other engines would need the harness behind TLS.*

## Spec problems found

- **Idle timeout is defeated by a polling page.** Spec 5.2 sets a one-hour idle timeout; spec 1 requires refresh at least every 30 seconds. `sessions.find_session` refreshes `last_seen_at` on any request, so an open overview never goes idle. Resolved in the browser (see "Idle"); owner question 4.
- **"Within one minute" needs a refresh faster than the poll interval.** Three failed polls take up to about 50 s (3 × 15 s + the 5 s timeout); a 15 s page refresh could show it at 65 s. Resolved with a 10 s refresh.
- **Spec 9 names "two fake leaders" but no identity provider**, and the console's provider endpoints are fixed to Entra ID and Google. The end-to-end tests therefore intercept the browser's visit to Entra ID. No backend change is needed.
- **"24-hour throughput chart"** has no per-interval figure behind it: history carries `completed_last_hour` and `completed_last_day` only. The chart plots the rolling hourly figure; owner question 5.
- **The uv workspace glob `packages/*`** does not allow a non-Python package under `packages/`; resolved with an `exclude`.

## File Structure

```
pyproject.toml                                   MOD  workspace exclude (Task 1)
.github/workflows/ci.yml                         MOD  web job (Task 1); web-e2e job (Task 6)
README.md                                        MOD  web app build and tests (Task 6)
packages/console-web/
  package.json, package-lock.json, .npmrc, .gitignore   NEW (Task 1)
  tsconfig.json, vite.config.ts, eslint.config.js       NEW (Task 1)
  index.html                                     NEW  (Task 1)
  scripts/check-dist.mjs                         NEW  refuses an unservable build (Task 1)
  playwright.config.ts                           NEW  (Task 6)
  src/
    main.tsx                                     NEW  (Task 1); theme applied (Task 5)
    App.tsx                                      NEW  stub (Task 1); routes (Task 5)
    App.test.tsx                                 NEW  smoke test (Task 1); removed (Task 5)
    styles.css                                   NEW  tokens, themes, layout (Task 1)
    test/setup.ts                                NEW  jest-dom, dialog and matchMedia stand-ins (Task 1)
    test/fetchMock.ts                            NEW  a stand-in console for component tests (Task 2)
    test/fixtures.ts                             NEW  session, leader and history fixtures (Task 3)
    api/types.ts                                 NEW  the backend's models (Task 2)
    api/client.ts                                NEW  fetch, CSRF, ApiError, 401 handler (Task 2)
    api/errors.ts                                NEW  what each error code reads as (Task 2)
    api/roles.ts                                 NEW  the allow-list's roles (Task 2)
    app/router.tsx                               NEW  History-API router (Task 3)
    app/navigation.ts                            NEW  full-page leaves: sign-in, signed-out (Task 3)
    app/theme.ts                                 NEW  light, dark, system (Task 3)
    app/activity.ts                              NEW  idle detection (Task 3)
    app/usePoll.ts                               NEW  load now, then on an interval (Task 3)
    app/session.tsx                              NEW  GET /api/session, CSRF token, sign-out (Task 3)
    components/ErrorPanel.tsx                    NEW  an error as the person reads it (Task 3)
    lib/format.ts                                NEW  counts, durations, times, ages (Task 4)
    components/Sparkline.tsx                     NEW  the 24-hour chart (Task 4)
    app/useNow.ts, app/usePageTitle.ts           NEW  (Task 5)
    app/fleet.tsx                                NEW  GET /api/fleet every 10 s (Task 5)
    components/HealthBadge.tsx                   NEW  (Task 5)
    components/ThroughputChart.tsx               NEW  history → Sparkline (Task 5)
    components/Layout.tsx                        NEW  header, navigation, theme, idle notice (Task 5)
    pages/SignInPage.tsx, pages/NotFoundPage.tsx NEW  (Task 5)
    pages/FleetPage.tsx                          NEW  the overview (Task 5)
  e2e/
    harness/fake_entra.py                        NEW  in-memory Entra ID (Task 6)
    harness/fake_leaders.py                      NEW  two stateful in-memory leaders (Task 6)
    harness/serve.py                             NEW  console + control server (Task 6)
    tests/support.ts                             NEW  sign-in, control calls, axe, CSP guard (Task 6)
    tests/sign-in.spec.ts, overview.spec.ts, a11y.spec.ts   NEW (Task 6)
```

Test files sit beside what they test (`client.test.ts`, `FleetPage.test.tsx`, …) and are listed in each task.

---

### Task 1: Package, toolchain and the build contract

**Files:**
- Modify: `pyproject.toml` (the `[tool.uv.workspace]` table)
- Modify: `.github/workflows/ci.yml` (append the `web` job)
- Create: `packages/console-web/package.json`, `.npmrc`, `.gitignore`, `tsconfig.json`, `vite.config.ts`, `eslint.config.js`, `index.html`, `scripts/check-dist.mjs`
- Create: `packages/console-web/src/main.tsx`, `src/App.tsx`, `src/styles.css`, `src/test/setup.ts`
- Test: `packages/console-web/src/App.test.tsx`
- Generated and committed: `packages/console-web/package-lock.json`

**Interfaces:**
- Consumes: the console's static-file rules (`static.py`: `index.html` fallback, the `_HASHED` pattern) and CSP (`api/security.py`).
- Produces: npm scripts `build` (Vite build, then `scripts/check-dist.mjs`), `watch`, `typecheck`, `lint`, `test`, `e2e`; `dist/index.html` and `dist/assets/<name>-<16 hex>.<ext>`; `export function App(): JSX.Element` in `src/App.tsx` (a stub here; Task 5 replaces it); the CSS class names in `src/styles.css` that later tasks use.

- [ ] **Step 1: Keep the new folder out of the uv workspace**

In `pyproject.toml`, replace

```toml
[tool.uv.workspace]
members = ["packages/*"]
```

with

```toml
[tool.uv.workspace]
members = ["packages/*"]
# The web app is an npm package; uv refuses a workspace member without a pyproject.toml.
exclude = ["packages/console-web"]
```

Run: `python -m uv lock --locked`
Expected: `Resolved … packages` and exit code 0 (the lock file does not change).

- [ ] **Step 2: Write `packages/console-web/package.json`, `.npmrc` and `.gitignore`**

`packages/console-web/package.json`:

```json
{
  "name": "swarmscribe-console-web",
  "private": true,
  "version": "0.1.0",
  "description": "SwarmScribe fleet console web app (served by swarmscribe-console)",
  "type": "module",
  "engines": {
    "node": ">=24 <25"
  },
  "scripts": {
    "build": "vite build && node scripts/check-dist.mjs",
    "watch": "vite build --watch",
    "typecheck": "tsc -p tsconfig.json",
    "lint": "eslint . --max-warnings 0",
    "test": "vitest run",
    "e2e": "playwright test"
  },
  "dependencies": {
    "react": "19.3.0",
    "react-dom": "19.3.0"
  },
  "devDependencies": {
    "@axe-core/playwright": "4.13.0",
    "@eslint/js": "9.39.5",
    "@playwright/test": "1.63.0",
    "@testing-library/dom": "10.4.2",
    "@testing-library/jest-dom": "7.0.1",
    "@testing-library/react": "16.3.3",
    "@testing-library/user-event": "14.6.7",
    "@types/node": "24.19.1",
    "@types/react": "19.3.0",
    "@types/react-dom": "19.3.0",
    "@vitejs/plugin-react": "6.1.1",
    "eslint": "9.39.5",
    "eslint-plugin-jsx-a11y": "6.10.2",
    "eslint-plugin-react-hooks": "7.1.1",
    "globals": "17.13.0",
    "jsdom": "30.1.2",
    "typescript": "6.0.3",
    "typescript-eslint": "8.71.0",
    "vite": "8.3.2",
    "vitest": "5.0.3"
  }
}
```

`packages/console-web/.npmrc`:

```ini
engine-strict=true
save-exact=true
```

`packages/console-web/.gitignore`:

```gitignore
node_modules/
dist/
test-results/
playwright-report/
blob-report/
```

- [ ] **Step 3: Install and check the workspace still resolves**

Run (in `packages/console-web`): `npm install`
Expected: it finishes without `ERESOLVE` or peer-dependency errors and writes `package-lock.json`.

Run (repository root): `python -m uv lock --locked`
Expected: exit code 0. (Without Step 1 this fails with "Workspace member … is missing a `pyproject.toml`".)

- [ ] **Step 4: Write the TypeScript, Vite and ESLint configuration and `index.html`**

`packages/console-web/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2023",
    "lib": ["ES2023", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "jsx": "react-jsx",
    "strict": true,
    "noUncheckedIndexedAccess": true,
    "exactOptionalPropertyTypes": false,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "isolatedModules": true,
    "verbatimModuleSyntax": true,
    "skipLibCheck": true,
    "noEmit": true,
    "types": ["vite/client", "node", "@testing-library/jest-dom/vitest"]
  },
  "include": ["src", "e2e", "vite.config.ts", "playwright.config.ts"]
}
```

`packages/console-web/vite.config.ts`:

```ts
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The console serves dist/ (SWARMSCRIBE_CONSOLE_STATIC_DIR) under a CSP with no inline
// script or style, and caches for a year only names that look content-hashed: a "-" then
// 8+ letters/digits/underscores with at least one digit, then the extension. Hex hashes of
// 16 characters fit that shape; scripts/check-dist.mjs refuses a build where one does not.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    assetsDir: "assets",
    assetsInlineLimit: 0,
    cssCodeSplit: false,
    modulePreload: { polyfill: false },
    sourcemap: false,
    rollupOptions: {
      output: {
        hashCharacters: "hex",
        entryFileNames: "assets/[name]-[hash:16].js",
        chunkFileNames: "assets/[name]-[hash:16].js",
        assetFileNames: "assets/[name]-[hash:16][extname]",
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    restoreMocks: true,
    unstubGlobals: true,
  },
});
```

`packages/console-web/eslint.config.js`:

```js
import js from "@eslint/js";
import jsxA11y from "eslint-plugin-jsx-a11y";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "node_modules", "playwright-report", "test-results", "e2e/harness"] },
  js.configs.recommended,
  ...tseslint.configs.strict,
  {
    files: ["src/**/*.{ts,tsx}"],
    ...jsxA11y.flatConfigs.strict,
    languageOptions: { globals: { ...globals.browser } },
    plugins: { "react-hooks": reactHooks, ...jsxA11y.flatConfigs.strict.plugins },
    rules: {
      ...jsxA11y.flatConfigs.strict.rules,
      ...reactHooks.configs.recommended.rules,
      // A scrolling table region must be focusable for keyboard users (axe
      // scrollable-region-focusable); tabpanel is the rule's own default.
      "jsx-a11y/no-noninteractive-tabindex": ["error", { tags: [], roles: ["region", "tabpanel"] }],
      "no-console": "error",
      "no-restricted-syntax": [
        "error",
        {
          selector: "JSXAttribute[name.name='style']",
          message: "No style props: the console's CSP forbids inline styles. Use a class.",
        },
        {
          selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']",
          message: "Never set HTML: render text.",
        },
      ],
      "no-restricted-globals": [
        "error",
        { name: "localStorage", message: "Use src/app/theme.ts; nothing else is stored." },
        { name: "sessionStorage", message: "Nothing is stored in sessionStorage." },
      ],
    },
  },
  {
    files: ["e2e/**/*.ts", "*.config.ts", "scripts/**/*.mjs"],
    languageOptions: { globals: { ...globals.node } },
  },
);
```

`packages/console-web/index.html` (the icon is an empty `data:` URL so the browser asks for no `/favicon.ico`, which the console would answer with 404):

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <meta name="color-scheme" content="light dark" />
    <link rel="icon" href="data:," />
    <title>SwarmScribe console</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 5: Write the test setup and the failing smoke test**

`packages/console-web/src/test/setup.ts`:

```ts
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// jsdom has no <dialog> modality and no matchMedia; the app uses both.
if (typeof HTMLDialogElement !== "undefined" && !HTMLDialogElement.prototype.showModal) {
  HTMLDialogElement.prototype.showModal = function showModal(this: HTMLDialogElement) {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function close(this: HTMLDialogElement) {
    if (!this.hasAttribute("open")) return;
    this.removeAttribute("open");
    this.dispatchEvent(new Event("close"));
  };
}
if (!window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      addListener: () => undefined,
      removeListener: () => undefined,
      dispatchEvent: () => false,
    }) as MediaQueryList;
}

afterEach(() => {
  cleanup();
  window.history.replaceState(null, "", "/");
});
```

`packages/console-web/src/App.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { App } from "./App";

it("renders the console's name", () => {
  render(<App />);
  expect(screen.getByRole("heading", { level: 1, name: "SwarmScribe console" })).toBeInTheDocument();
});
```

- [ ] **Step 6: Run the test to verify it fails**

Run: `npm test`
Expected: FAIL — `Failed to resolve import "./App"`.

- [ ] **Step 7: Write the stub app, the entry point and the stylesheet**

`packages/console-web/src/App.tsx` (Task 5 replaces this file):

```tsx
export function App() {
  return (
    <main id="main">
      <h1>SwarmScribe console</h1>
    </main>
  );
}
```

`packages/console-web/src/main.tsx`:

```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./styles.css";

const root = document.getElementById("root");
if (root === null) throw new Error("index.html has no #root");
createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
```

`packages/console-web/src/styles.css` (the whole C3a stylesheet; later tasks in this plan only use its classes):

```css
/* SwarmScribe console. Served as a file under the console's CSP (style-src 'self'): no
   inline styles anywhere. Colours are tokens; every text pair is at least 4.5:1 and every
   border, focus ring and chart mark at least 3:1, in both themes. */

:root {
  color-scheme: light;
  --bg: #ffffff;
  --surface: #f6f7f9;
  --surface-2: #eceef2;
  --text: #1a1d23;
  --muted: #555d68;
  --border: #8b93a0;
  --rule: #d5d9e0;
  --accent: #8a5300;
  --accent-on: #ffffff;
  --focus: #0b57d0;
  --ok-bg: #e6f4ea;
  --ok-fg: #0d652d;
  --warn-bg: #fef3d6;
  --warn-fg: #7a4a00;
  --bad-bg: #fde8e8;
  --bad-fg: #a31515;
  --muted-bg: #eceef2;
  --muted-fg: #3f4651;
  --radius: 6px;
  --font: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  --mono: ui-monospace, "Cascadia Mono", "Segoe UI Mono", Menlo, Consolas, monospace;
}

@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --bg: #10131a;
    --surface: #171b24;
    --surface-2: #212734;
    --text: #e8eaee;
    --muted: #a4acb9;
    --border: #5d6779;
    --rule: #2c3442;
    --accent: #f2b544;
    --accent-on: #1a1200;
    --focus: #8ab4f8;
    --ok-bg: #12301e;
    --ok-fg: #7ee2a1;
    --warn-bg: #3a2a08;
    --warn-fg: #f6c76b;
    --bad-bg: #3d1418;
    --bad-fg: #ff9b9b;
    --muted-bg: #262c38;
    --muted-fg: #c5cad3;
  }
}

:root[data-theme="dark"] {
  color-scheme: dark;
  --bg: #10131a;
  --surface: #171b24;
  --surface-2: #212734;
  --text: #e8eaee;
  --muted: #a4acb9;
  --border: #5d6779;
  --rule: #2c3442;
  --accent: #f2b544;
  --accent-on: #1a1200;
  --focus: #8ab4f8;
  --ok-bg: #12301e;
  --ok-fg: #7ee2a1;
  --warn-bg: #3a2a08;
  --warn-fg: #f6c76b;
  --bad-bg: #3d1418;
  --bad-fg: #ff9b9b;
  --muted-bg: #262c38;
  --muted-fg: #c5cad3;
}

*,
*::before,
*::after {
  box-sizing: border-box;
}

html {
  background: var(--bg);
  color: var(--text);
  font-family: var(--font);
  font-size: 100%;
  line-height: 1.5;
}

body {
  margin: 0;
  min-width: 320px;
}

h1 {
  font-size: 1.6rem;
  margin: 0 0 0.5rem;
}

h2 {
  font-size: 1.25rem;
  margin: 1.5rem 0 0.5rem;
}

a {
  color: var(--accent);
}

:focus-visible {
  outline: 3px solid var(--focus);
  outline-offset: 2px;
}

main:focus {
  outline: none;
}

code,
.mono {
  font-family: var(--mono);
  font-size: 0.9em;
}

.muted {
  color: var(--muted);
}

.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  margin: -1px;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
}

/* ---- Skip link, header, navigation ---- */

.skip-link {
  position: absolute;
  left: 0.5rem;
  top: -3rem;
  padding: 0.5rem 1rem;
  background: var(--accent);
  color: var(--accent-on);
  z-index: 10;
}

.skip-link:focus {
  top: 0.5rem;
}

.app-header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem 1.5rem;
  padding: 0.75rem 1.5rem;
  border-bottom: 1px solid var(--rule);
  background: var(--surface);
}

.brand {
  font-weight: 700;
  color: var(--text);
  text-decoration: none;
}

.nav-list {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem 1rem;
  list-style: none;
  margin: 0;
  padding: 0;
}

.nav-link {
  color: var(--text);
  text-decoration: none;
  padding: 0.25rem 0;
  border-bottom: 3px solid transparent;
}

.nav-link[aria-current="page"] {
  border-bottom-color: var(--accent);
  font-weight: 600;
}

.header-tools {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem 1rem;
  margin-left: auto;
}

.who {
  color: var(--muted);
  overflow-wrap: anywhere;
}

main {
  display: block;
  padding: 1.25rem 1.5rem 3rem;
  max-width: 100%;
}

.page-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  justify-content: space-between;
  gap: 0.5rem 1rem;
}

.page-message {
  padding: 2rem 1.5rem;
}

/* ---- Controls ---- */

.button {
  display: inline-block;
  font: inherit;
  padding: 0.35rem 0.85rem;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg);
  color: var(--text);
  cursor: pointer;
  text-decoration: none;
}

.button:hover:not(:disabled) {
  background: var(--surface-2);
}

.button-primary {
  background: var(--accent);
  border-color: var(--accent);
  color: var(--accent-on);
}

.button-primary:hover:not(:disabled) {
  background: var(--accent);
  filter: brightness(1.1);
}

.button-danger {
  border-color: var(--bad-fg);
  color: var(--bad-fg);
}

.button:disabled {
  cursor: not-allowed;
  border-style: dashed;
  color: var(--muted);
  background: var(--surface);
}

select,
input,
textarea {
  font: inherit;
  color: var(--text);
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 0.3rem 0.5rem;
}

.theme-select,
.field-inline {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
}

/* ---- Badges, notices, errors ---- */

.badge {
  display: inline-block;
  padding: 0.1rem 0.5rem;
  border-radius: 999px;
  font-size: 0.875rem;
  font-weight: 600;
  white-space: nowrap;
}

.badge-ok {
  background: var(--ok-bg);
  color: var(--ok-fg);
}

.badge-warn {
  background: var(--warn-bg);
  color: var(--warn-fg);
}

.badge-bad {
  background: var(--bad-bg);
  color: var(--bad-fg);
}

.badge-muted {
  background: var(--muted-bg);
  color: var(--muted-fg);
}

.notice {
  margin: 0.75rem 1.5rem;
  padding: 0.5rem 0.75rem;
  border-left: 4px solid var(--warn-fg);
  background: var(--warn-bg);
  color: var(--warn-fg);
}

main .notice {
  margin: 0.75rem 0;
}

.error-panel {
  margin: 0.75rem 0;
  padding: 0.75rem 1rem;
  border-left: 4px solid var(--bad-fg);
  background: var(--bad-bg);
  color: var(--bad-fg);
}

.error-panel p {
  margin: 0 0 0.5rem;
}

.error-title {
  font-weight: 700;
}

/* ---- Tables ---- */

.table-scroll {
  max-width: 100%;
  overflow-x: auto;
  border: 1px solid var(--rule);
  border-radius: var(--radius);
}

table {
  border-collapse: collapse;
  width: 100%;
}

th,
td {
  text-align: left;
  vertical-align: top;
  padding: 0.5rem 0.75rem;
  border-bottom: 1px solid var(--rule);
}

thead th {
  background: var(--surface);
  font-size: 0.875rem;
  font-weight: 600;
}

tbody th {
  font-weight: 400;
}

td.num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.fleet-table thead th {
  min-width: 6rem;
}

.leader-name {
  font-weight: 700;
}

.is-stale td {
  color: var(--muted);
}

.cell-note {
  display: block;
  font-size: 0.8125rem;
  color: var(--muted);
}

.cell-list {
  margin: 0;
  padding-left: 1rem;
  font-size: 0.875rem;
}

.labels {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem;
  list-style: none;
  margin: 0.25rem 0 0;
  padding: 0;
}

.label-chip {
  font-family: var(--mono);
  font-size: 0.75rem;
  padding: 0 0.4rem;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--muted-fg);
  background: var(--muted-bg);
}

/* ---- Sparkline ---- */

.sparkline {
  display: block;
  width: 160px;
  height: 36px;
}

.sparkline-axis {
  stroke: var(--border);
  stroke-width: 1;
}

.sparkline-line {
  fill: none;
  stroke: var(--accent);
  stroke-width: 1.5;
  vector-effect: non-scaling-stroke;
}

.sparkline-dot {
  fill: var(--accent);
}

.sparkline-down {
  fill: var(--bad-fg);
}

/* ---- Sign-in ---- */

.sign-in {
  max-width: 32rem;
  margin: 4rem auto;
  padding: 0 1.5rem;
}

.provider-list {
  list-style: none;
  padding: 0;
  display: grid;
  gap: 0.75rem;
}

.provider-list .button {
  display: block;
  text-align: center;
  padding: 0.6rem 1rem;
}

/* ---- Narrow screens (tablet portrait is 768px) ---- */

@media (max-width: 900px) {
  .app-header,
  main {
    padding-left: 1rem;
    padding-right: 1rem;
  }

  .header-tools {
    margin-left: 0;
  }
}
```

- [ ] **Step 8: Run the test, the type-check and the linter**

Run: `npm test`
Expected: PASS — 1 test.

Run: `npm run typecheck`
Expected: no output, exit code 0.

Run: `npm run lint`
Expected: no output, exit code 0.

- [ ] **Step 9: Write `scripts/check-dist.mjs` and build**

`packages/console-web/scripts/check-dist.mjs`:

```js
// Refuses a build the console would serve wrongly. Run after `vite build` (npm run build).
// - index.html: no inline <script> or <style>, no style="" attribute, nothing off-origin.
// - every file under dist/assets has a name the console caches as content-hashed
//   (packages/console/src/swarmscribe_console/static.py, _HASHED).
// - nothing from the end-to-end harness or a test reached the bundle.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { basename, join } from "node:path";
import { fileURLToPath } from "node:url";

const DIST = fileURLToPath(new URL("../dist/", import.meta.url));
const HASHED = /[.-](?=[A-Za-z0-9_]*\d)[A-Za-z0-9_]{8,}\.[A-Za-z0-9]+/;
const FORBIDDEN_IN_BUNDLE = ["/control/", "e2e/harness", "fake_leaders", "@testing-library"];
const problems = [];

const html = readFileSync(join(DIST, "index.html"), "utf8");
for (const match of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)) {
  const [, attributes, body] = match;
  if (!/\bsrc=/.test(attributes) || body.trim() !== "") problems.push("index.html has an inline <script>");
}
if (/<style\b/i.test(html)) problems.push("index.html has an inline <style>");
if (/\sstyle\s*=/i.test(html)) problems.push("index.html has a style attribute");
if (/(src|href)\s*=\s*"(https?:)?\/\//i.test(html)) problems.push("index.html loads something off-origin");

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? walk(path) : [path];
  });
}
const assets = walk(join(DIST, "assets"));
if (assets.length === 0) problems.push("dist/assets is empty");
for (const path of assets) {
  const name = basename(path);
  if (!HASHED.test(name)) problems.push(`${name} is not named like a content-hashed file`);
  if (/\.(js|css)$/.test(name)) {
    const text = readFileSync(path, "utf8");
    for (const needle of FORBIDDEN_IN_BUNDLE) {
      if (text.includes(needle)) problems.push(`${name} contains ${JSON.stringify(needle)}`);
    }
  }
}

if (problems.length > 0) {
  console.error("dist/ is not servable by the console:\n- " + problems.join("\n- "));
  process.exit(1);
}
console.log(`dist/ ok: index.html and ${assets.length} hashed assets`);
```

Run: `npm run build`
Expected: Vite lists `dist/index.html`, `dist/assets/style-<16 hex>.css` and `dist/assets/index-<16 hex>.js`, then `dist/ ok: index.html and 2 hashed assets`.

- [ ] **Step 10: Prove the checker refuses a bad build**

Run (in `packages/console-web`):

```bash
node -e "require('node:fs').writeFileSync('dist/assets/plain.js', '')"
node scripts/check-dist.mjs
```

Expected: exit code 1 and `- plain.js is not named like a content-hashed file`.

Run: `npm run build`
Expected: `dist/ ok: index.html and 2 hashed assets` (the build empties `dist/` first).

- [ ] **Step 11: Add the `web` job to CI**

Append to `.github/workflows/ci.yml`, under `jobs:` (after `compose-e2e`, same indentation as the other jobs):

```yaml
  web:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: packages/console-web
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 24
          cache: npm
          cache-dependency-path: packages/console-web/package-lock.json
      - run: npm ci
      - run: npm run typecheck
      - run: npm run lint
      - run: npm test
      - run: npm run build
```

Run (repository root): `python -m uv run python -c "import yaml; print(list(yaml.safe_load(open('.github/workflows/ci.yml'))['jobs']))"`
Expected: `['test', 'compose-e2e', 'web']`.

Run: `python -m uv run ruff check .`
Expected: `All checks passed!` (ruff skips `node_modules` by default).

- [ ] **Step 12: Commit**

```bash
git add pyproject.toml .github/workflows/ci.yml packages/console-web
git commit -m "Console web app: package, pinned toolchain, build contract and CI job

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Check `git status` shows no `node_modules` or `dist` staged.

---

### Task 2: The API layer — types, client, error texts, role map

**Files:**
- Create: `packages/console-web/src/api/types.ts`, `src/api/client.ts`, `src/api/errors.ts`, `src/api/roles.ts`
- Create: `packages/console-web/src/test/fetchMock.ts`
- Test: `packages/console-web/src/api/client.test.ts`, `src/api/errors.test.ts`, `src/api/roles.test.ts`

**Interfaces:**
- Consumes: the console's routes and models (`api/session.py`, `api/fleet.py`, `api/models.py`, the leader's `api/admin_models.py`), its error body `{code, message}` and `Retry-After`, and the allow-list in `packages/console/src/swarmscribe_console/proxy.py`.
- Produces:
  - `src/api/types.ts`: `Role`, `Health`, `ErrorBody`, `SessionInfo`, `ProvidersOut`, `LeaderStatus` (with `PoolQueue`, `PoolFollowers`, `LocationStatus`), `FleetSummary`, `ScanError`, `SnapshotOut`, `FleetLeader`, `HistoryPoint`, and the proxied and admin models C3b uses (`JobOut`, `FollowerOut`, `LocationOut`, `LocationIn`, `TokenOut`, `TokenIn`, `TokenCreated`, `ConsentReport`, `LeaderOut`, `LeaderIn`, `LeaderEdit`, `CredentialIn`, `GrantOut`, `GrantIn`, `ConsoleAdminOut`, `ConsoleAdminIn`, …).
  - `src/api/client.ts`: `class ApiError extends Error { status: number; code: string; retryAfter: number | null }`; `NETWORK_ERROR = "network_error"`; `setCsrfToken(token: string | null): void`; `setUnauthenticatedHandler(handler: () => void): void`; `isAbort(error: unknown): boolean`; `request<T>(method, path, body?, signal?): Promise<T>`; `api.get<T>(path, signal?)`, `api.post<T>(path, body?)`, `api.put<T>(path, body?)`, `api.patch<T>(path, body?)`, `api.del(path)`; `leaderPath(name: string, rest: string): string`; `query(params: Record<string, string | number | null | undefined>): string`.
  - `src/api/errors.ts`: `ERROR_TITLES: Record<string, string>`; `describeError(error: unknown): { title: string; detail: string | null }`.
  - `src/api/roles.ts`: `ROLE_RANK`, `ACTION_ROLE`, `type LeaderAction`, `atLeast(held: Role, needed: Role): boolean`, `neededRole(action: LeaderAction): Role`, `can(held: Role, action: LeaderAction): boolean`.
  - `src/test/fetchMock.ts`: `mockFetch(): FetchMock` with `.on(route: "METHOD /path", handlerOrReply)`, `.calls`, `.callsTo(route)`; `reply(status, body?, headers?)`; `fail(status, code, message?)`.

- [ ] **Step 1: Write the types**

`packages/console-web/src/api/types.ts`:

```ts
// The console's /api answers, field for field. Sources of truth:
// - packages/console/src/swarmscribe_console/api/session.py   (SessionOut)
// - packages/console/src/swarmscribe_console/api/fleet.py     (FleetLeader, HistoryPoint)
// - packages/console/src/swarmscribe_console/api/models.py    (admin bodies and answers)
// - packages/leader/src/swarmscribe_leader/api/admin_models.py (proxied leader bodies)
// Datetimes arrive as ISO 8601 strings; UUIDs as strings.

export type Role = "viewer" | "operator" | "admin";
export type Health = "disabled" | "credential_revoked" | "unreachable" | "pending" | "reachable";

export interface ErrorBody {
  code: string;
  message: string;
}

export interface SessionInfo {
  provider: string;
  issuer: string;
  subject: string;
  email: string | null;
  console_admin: boolean;
  csrf_token: string;
  expires_at: string;
  idle_expires_at: string;
}

export interface ProvidersOut {
  providers: string[];
}

// ---- Leader status (C1 + C1b Status), as stored in a snapshot ----

export interface PoolQueue {
  pool: string;
  queued: number;
  leased: number;
}

export interface PoolFollowers {
  pool: string;
  active: number;
  draining: number;
  revoked: number;
  gone: number;
}

export interface LocationStatus {
  name: string;
  backend: string;
  enabled: boolean;
  last_scan_at: string | null;
  last_scan_error: string | null;
  scan_requested: boolean;
  recordings: number;
  consented: number;
}

export interface LeaderStatus {
  jobs: Record<string, number>;
  pools: PoolQueue[];
  followers: Record<string, number>;
  follower_pools: PoolFollowers[];
  completed_last_hour: number;
  completed_last_day: number;
  oldest_queued_age_s: number | null;
  failed_attempts_last_day: number;
  locations: LocationStatus[];
}

// ---- Fleet ----

export interface ScanError {
  location: string;
  error: string;
}

export interface FleetSummary {
  queued: number;
  completed_last_hour: number;
  completed_last_day: number;
  failed_attempts_last_day: number;
  /** As of snapshot.taken_at, by the leader's clock; null when nothing is queued. */
  oldest_queued_age_s: number | null;
  followers_active_by_pool: Record<string, number>;
  scan_errors: ScanError[];
}

export interface SnapshotOut {
  taken_at: string;
  status: LeaderStatus;
}

export interface FleetLeader {
  name: string;
  labels: Record<string, string>;
  role: Role;
  health: Health;
  enabled: boolean;
  last_polled_at: string | null;
  last_success_at: string | null;
  last_error: string | null;
  consecutive_failures: number;
  summary: FleetSummary | null;
  snapshot: SnapshotOut | null;
}

export interface HistoryPoint {
  at: string;
  reachable: boolean;
  queued: number | null;
  leased: number | null;
  completed_last_hour: number | null;
  completed_last_day: number | null;
  failed_attempts_last_day: number | null;
  oldest_queued_age_s: number | null;
  followers_active: number | null;
}

// ---- Proxied leader reads and actions (/api/leaders/{name}/...) ----

export type JobState = "queued" | "leased" | "completed" | "failed" | "cancelled";
export type FollowerState = "active" | "draining" | "revoked" | "gone";
export type RequiredDevice = "any" | "cuda" | "cpu";
export type ChannelMode = "mono" | "stereo_split" | "auto";

export interface LocationOut {
  id: string;
  name: string;
  backend: string;
  root: string | null;
  input_prefix: string;
  output_prefix: string;
  pool: string;
  required_device: string;
  scan_interval_s: number;
  enabled: boolean;
  last_scan_at: string | null;
  last_scan_error: string | null;
  scan_requested: boolean;
  channel_mode: string;
  channel_labels: string[];
}

export interface LocationIn {
  name: string;
  root: string;
  input_prefix?: string;
  output_prefix?: string;
  pool?: string;
  required_device?: RequiredDevice;
  scan_interval_s?: number;
  channel_mode?: ChannelMode;
  channel_labels?: [string, string];
}

export interface JobOut {
  id: string;
  state: string;
  location: string;
  key: string;
  priority: number;
  attempts: number;
  max_attempts: number;
  pool: string;
  leased_by: string | null;
  failure_reason: string | null;
  cancelled_by: string | null;
  no_speech: boolean | null;
  created_at: string;
  completed_at: string | null;
}

export interface PriorityIn {
  priority: number;
}

export interface FollowerOut {
  id: string;
  pool: string;
  state: string;
  device: string | null;
  last_seen_at: string;
  created_at: string;
  leases: number;
}

export interface FollowerRevoked {
  id: string;
  state: string;
  released: number;
}

export interface TokenOut {
  id: string;
  pool: string;
  expires_at: string;
  max_uses: number;
  uses: number;
  revoked: boolean;
  created_by: string;
  created_at: string;
}

export interface TokenIn {
  pool?: string;
  expires_in_seconds?: number;
  max_uses?: number;
}

/** The only answer that carries a join token's plaintext. Never stored; see TokenCreatedDialog. */
export interface TokenCreated {
  id: string;
  token: string;
  pool: string;
  expires_at: string;
  max_uses: number;
}

export interface ScanRequested {
  name: string;
  requested_at: string;
}

export interface ConsentCounts {
  name: string;
  consented: number;
  not_consented: number;
  withdrawn: number;
  missing: number;
}

export interface FlaggedOutputs {
  job_id: string;
  location: string;
  key: string;
  completed_at: string | null;
  output_location: string;
  outputs: string[];
}

export interface ConsentReport {
  locations: ConsentCounts[];
  flagged: FlaggedOutputs[];
  truncated: boolean;
}

// ---- Console administration (/api/admin/...) ----

export interface LeaderOut {
  name: string;
  base_url: string;
  labels: Record<string, string>;
  enabled: boolean;
  added_by: string;
  created_at: string;
  credential_updated_at: string;
  credential_updated_by: string;
  credential_revoked: boolean;
  credential_revoked_at: string | null;
}

export interface LeaderIn {
  name: string;
  base_url: string;
  labels: Record<string, string>;
  credential: string;
  enabled: boolean;
}

/** Only the fields being changed. `credential` only together with a new base_url. */
export interface LeaderEdit {
  base_url?: string;
  labels?: Record<string, string>;
  enabled?: boolean;
  credential?: string;
}

export interface CredentialIn {
  credential: string;
}

export type PrincipalKind = "entra_group" | "google_group" | "email" | "domain";

export interface GrantOut {
  id: string;
  role: string;
  scope: string;
  principal_kind: string;
  principal: string;
  created_by: string;
  created_at: string;
}

export interface GrantIn {
  role: Role;
  scope: string;
  principal_kind: PrincipalKind;
  principal: string;
}

export interface ConsoleAdminOut {
  id: string;
  principal_kind: string;
  principal: string;
  created_by: string;
  created_at: string;
}

export interface ConsoleAdminIn {
  principal_kind: PrincipalKind;
  principal: string;
}
```

- [ ] **Step 2: Write the fetch stand-in for tests**

`packages/console-web/src/test/fetchMock.ts`:

```ts
import { vi } from "vitest";

// A stand-in for the console in component tests. Routes are "METHOD /path?query" (the query
// exactly as sent, or omitted to match any query). A route's handler answers with
// reply(status, body, headers) or throws. Every call is kept in `calls`.

export interface Call {
  method: string;
  url: string;
  headers: Record<string, string>;
  body: unknown;
}

export interface Reply {
  status: number;
  body?: unknown;
  headers?: Record<string, string>;
}

type Handler = (call: Call) => Reply | Promise<Reply>;

export function reply(status: number, body?: unknown, headers?: Record<string, string>): Reply {
  return { status, body, headers };
}

export function fail(status: number, code: string, message = code): Reply {
  return { status, body: { code, message } };
}

export interface FetchMock {
  calls: Call[];
  on: (route: string, handler: Handler | Reply) => FetchMock;
  callsTo: (route: string) => Call[];
}

export function mockFetch(): FetchMock {
  const routes = new Map<string, Handler>();
  const calls: Call[] = [];
  const key = (method: string, url: string) => `${method} ${url}`;

  const fetchStub = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = (init?.method ?? "GET").toUpperCase();
    const headers = Object.fromEntries(
      Object.entries((init?.headers as Record<string, string> | undefined) ?? {}),
    );
    const body = typeof init?.body === "string" ? (JSON.parse(init.body) as unknown) : undefined;
    const call: Call = { method, url, headers, body };
    calls.push(call);
    if (init?.signal?.aborted) throw new DOMException("aborted", "AbortError");
    const handler = routes.get(key(method, url)) ?? routes.get(key(method, url.split("?")[0] ?? url));
    if (handler === undefined) {
      return new Response(JSON.stringify({ code: "not_found", message: `no mock for ${method} ${url}` }), {
        status: 404,
        headers: { "Content-Type": "application/json" },
      });
    }
    const answer = await handler(call);
    const init2: ResponseInit = { status: answer.status, headers: answer.headers };
    if (answer.status === 204 || answer.body === undefined) return new Response(null, init2);
    return new Response(JSON.stringify(answer.body), {
      ...init2,
      headers: { "Content-Type": "application/json", ...(answer.headers ?? {}) },
    });
  });
  vi.stubGlobal("fetch", fetchStub);

  const mock: FetchMock = {
    calls,
    on(route, handler) {
      routes.set(route, typeof handler === "function" ? handler : () => handler);
      return mock;
    },
    callsTo(route) {
      return calls.filter((call) => key(call.method, call.url) === route);
    },
  };
  return mock;
}
```

- [ ] **Step 3: Write the failing client test**

`packages/console-web/src/api/client.test.ts`:

```ts
import { afterEach, describe, expect, it, vi } from "vitest";
import { fail, mockFetch, reply } from "../test/fetchMock";
import { ApiError, api, leaderPath, query, setCsrfToken, setUnauthenticatedHandler } from "./client";

afterEach(() => {
  setCsrfToken(null);
  setUnauthenticatedHandler(() => undefined);
});

describe("api client", () => {
  it("sends the CSRF token on unsafe methods only, and never caches", async () => {
    const mock = mockFetch()
      .on("GET /api/fleet", reply(200, []))
      .on("POST /api/leaders/eu-1/jobs/j/cancel", reply(200, { id: "j" }));
    setCsrfToken("csrf-1");
    await api.get("/api/fleet");
    await api.post("/api/leaders/eu-1/jobs/j/cancel");
    const [get, post] = mock.calls;
    expect(get?.headers["X-CSRF-Token"]).toBeUndefined();
    expect(post?.headers["X-CSRF-Token"]).toBe("csrf-1");
    const init = vi.mocked(fetch).mock.calls[0]?.[1];
    expect(init?.cache).toBe("no-store");
    expect(init?.credentials).toBe("same-origin");
  });

  it("sends a JSON body with its content type", async () => {
    const mock = mockFetch().on("POST /api/admin/grants", reply(201, { id: "g" }));
    await api.post("/api/admin/grants", { role: "viewer" });
    expect(mock.calls[0]?.body).toEqual({ role: "viewer" });
    expect(mock.calls[0]?.headers["Content-Type"]).toBe("application/json");
  });

  it("turns an error answer into an ApiError with code, message and Retry-After", async () => {
    mockFetch().on("GET /api/leaders/eu-1/jobs", {
      status: 503,
      body: { code: "leader_unreachable", message: "leader eu-1 cannot be reached; try again" },
      headers: { "Retry-After": "15" },
    });
    const error = await api.get("/api/leaders/eu-1/jobs").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 503,
      code: "leader_unreachable",
      message: "leader eu-1 cannot be reached; try again",
      retryAfter: 15,
    });
  });

  it("keeps a status-based code when the error body is not {code, message}", async () => {
    mockFetch().on("GET /api/fleet", reply(502, "<html>bad gateway</html>"));
    const error = await api.get("/api/fleet").catch((e: unknown) => e);
    expect(error).toMatchObject({ status: 502, code: "http_502" });
  });

  it("calls the unauthenticated handler on any 401", async () => {
    mockFetch().on("GET /api/fleet", fail(401, "unauthenticated", "sign in to the console first"));
    const handler = vi.fn();
    setUnauthenticatedHandler(handler);
    await expect(api.get("/api/fleet")).rejects.toMatchObject({ code: "unauthenticated" });
    expect(handler).toHaveBeenCalledOnce();
  });

  it("reports a network failure as network_error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Promise.reject(new TypeError("Failed to fetch"))),
    );
    await expect(api.get("/api/fleet")).rejects.toMatchObject({ status: 0, code: "network_error" });
  });

  it("answers undefined for 204", async () => {
    mockFetch().on("DELETE /api/admin/grants/g", reply(204));
    await expect(api.del("/api/admin/grants/g")).resolves.toBeUndefined();
  });

  it("builds leader paths and query strings", () => {
    expect(leaderPath("eu-1.prod", "jobs")).toBe("/api/leaders/eu-1.prod/jobs");
    expect(query({ state: "failed", location: "", limit: 50, x: null })).toBe("?state=failed&limit=50");
    expect(query({})).toBe("");
  });
});
```

- [ ] **Step 4: Run it to verify it fails**

Run: `npx vitest run src/api/client.test.ts`
Expected: FAIL — `Failed to resolve import "./client"`.

- [ ] **Step 5: Write the client**

`packages/console-web/src/api/client.ts`:

```ts
// The only code that calls the console. Every unsafe call carries X-CSRF-Token (from
// GET /api/session); the browser adds a same-origin Origin itself. Nothing is cached
// (cache: "no-store") and nothing is logged. A 401 from any /api route always means the
// console's own session ended (leader 401s arrive as 502/503), so it calls the
// unauthenticated handler, which sends the person to sign in.

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly retryAfter: number | null;

  constructor(status: number, code: string, message: string, retryAfter: number | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.retryAfter = retryAfter;
  }
}

export const NETWORK_ERROR = "network_error";

const UNSAFE = new Set(["POST", "PUT", "PATCH", "DELETE"]);
const CODE = /^[a-z][a-z0-9_]{0,63}$/;

let csrfToken: string | null = null;
let onUnauthenticated: () => void = () => undefined;

export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}

export function setUnauthenticatedHandler(handler: () => void): void {
  onUnauthenticated = handler;
}

export function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function retryAfterOf(response: Response): number | null {
  const value = response.headers.get("Retry-After");
  return value !== null && /^\d{1,5}$/.test(value) ? Number(value) : null;
}

async function errorFrom(response: Response): Promise<ApiError> {
  let code = `http_${response.status}`;
  let message = `The console answered HTTP ${response.status}.`;
  try {
    const body: unknown = await response.json();
    if (body !== null && typeof body === "object") {
      const { code: c, message: m } = body as Record<string, unknown>;
      if (typeof c === "string" && CODE.test(c) && typeof m === "string") {
        code = c;
        message = m;
      }
    }
  } catch {
    // Not JSON: keep the status-based code.
  }
  return new ApiError(response.status, code, message, retryAfterOf(response));
}

export async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (UNSAFE.has(method) && csrfToken !== null) headers["X-CSRF-Token"] = csrfToken;
  let payload: string | undefined;
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  let response: Response;
  try {
    response = await fetch(path, {
      method,
      headers,
      body: payload,
      credentials: "same-origin",
      cache: "no-store",
      signal,
    });
  } catch (error) {
    if (isAbort(error)) throw error;
    throw new ApiError(0, NETWORK_ERROR, "The console could not be reached.");
  }
  if (response.status === 401) {
    onUnauthenticated();
    throw await errorFrom(response);
  }
  if (!response.ok) throw await errorFrom(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  get: <T>(path: string, signal?: AbortSignal) => request<T>("GET", path, undefined, signal),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  del: (path: string) => request<undefined>("DELETE", path),
};

/** /api/leaders/<name>/<rest>; the name is encoded (leader names allow "." and "-"). */
export function leaderPath(name: string, rest: string): string {
  return `/api/leaders/${encodeURIComponent(name)}/${rest}`;
}

/** A query string from the set values only ("" when none). */
export function query(params: Record<string, string | number | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}
```

- [ ] **Step 6: Run the client test**

Run: `npx vitest run src/api/client.test.ts`
Expected: PASS — 8 tests.

- [ ] **Step 7: Write the failing error-text test**

`packages/console-web/src/api/errors.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { ApiError } from "./client";
import { ERROR_TITLES, describeError } from "./errors";

// Every code the C3 handoff note names, plus the console's own session codes.
const HANDOFF_CODES = [
  "leader_not_found",
  "not_found",
  "unauthenticated",
  "leader_unreachable",
  "leader_credential_revoked",
  "leader_credential_unreadable",
  "leader_credential_rejected",
  "bad_gateway",
  "leader_disabled",
  "forbidden",
  "actor_not_representable",
  "invalid_request",
  "too_large",
  "csrf_failed",
  "unavailable",
];

describe("describeError", () => {
  it.each(HANDOFF_CODES)("has its own title for %s", (code) => {
    expect(ERROR_TITLES[code]).toBeTruthy();
    expect(describeError(new ApiError(400, code, "x")).title).toBe(ERROR_TITLES[code]);
  });

  it("shows leader_not_found as not visible to you", () => {
    expect(describeError(new ApiError(404, "leader_not_found", "no leader with that name")).title).toBe(
      "This leader is not visible to you.",
    );
  });

  it("keeps the server's message as the detail and adds Retry-After", () => {
    const text = describeError(
      new ApiError(503, "leader_unreachable", "leader eu-1 cannot be reached; try again", 15),
    );
    expect(text.detail).toBe("leader eu-1 cannot be reached; try again Try again in 15 seconds.");
  });

  it("names the role a forbidden action needs, from the console's message", () => {
    const text = describeError(
      new ApiError(403, "forbidden", "this needs the admin role on eu-1; you have operator"),
    );
    expect(text.title).toBe("Your role does not allow this.");
    expect(text.detail).toContain("admin role");
  });

  it("falls back by status for an unknown code", () => {
    expect(describeError(new ApiError(409, "something_new", "it clashed")).title).toBe(
      "That conflicts with the current state.",
    );
    expect(describeError(new ApiError(504, "gateway_timeout", "slow")).title).toBe(
      "The leader or the console failed to answer.",
    );
    expect(describeError(new ApiError(429, "slow_down", "wait")).title).toBe(
      "Too many requests. Wait, then try again.",
    );
  });

  it("drops the generic HTTP text when the body had no code", () => {
    expect(describeError(new ApiError(502, "http_502", "The console answered HTTP 502.")).detail).toBeNull();
  });

  it("never throws on something that is not an ApiError", () => {
    expect(describeError(new Error("boom")).title).toBe("The console hit an unexpected error.");
  });
});
```

- [ ] **Step 8: Run it to verify it fails**

Run: `npx vitest run src/api/errors.test.ts`
Expected: FAIL — `Failed to resolve import "./errors"`.

- [ ] **Step 9: Write the error texts**

`packages/console-web/src/api/errors.ts`:

```ts
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
```

- [ ] **Step 10: Run the error-text test**

Run: `npx vitest run src/api/errors.test.ts`
Expected: PASS — 21 tests.

- [ ] **Step 11: Write the failing role test**

`packages/console-web/src/api/roles.test.ts`:

```ts
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { ACTION_ROLE, atLeast, can } from "./roles";

// npm runs vitest from packages/console-web.
const PROXY_PY = resolve(process.cwd(), "../console/src/swarmscribe_console/proxy.py");

describe("roles", () => {
  it("matches the console's proxy allow-list exactly", () => {
    const source = readFileSync(PROXY_PY, "utf8");
    const routes = [
      ...source.matchAll(
        /ProxyRoute\(\s*"(?:GET|POST)",\s*"[^"]+",\s*"(viewer|operator|admin)",\s*"([a-z.]+)"/g,
      ),
    ];
    expect(routes.length).toBeGreaterThan(0);
    const fromPython = Object.fromEntries(routes.map(([, role, action]) => [action, role]));
    expect(ACTION_ROLE).toEqual(fromPython);
  });

  it("orders roles viewer < operator < admin", () => {
    expect(atLeast("admin", "operator")).toBe(true);
    expect(atLeast("operator", "admin")).toBe(false);
    expect(atLeast("viewer", "viewer")).toBe(true);
  });

  it("answers per action", () => {
    expect(can("operator", "jobs.retry")).toBe(true);
    expect(can("operator", "tokens.create")).toBe(false);
    expect(can("viewer", "consent.view")).toBe(true);
  });
});
```

- [ ] **Step 12: Run it to verify it fails**

Run: `npx vitest run src/api/roles.test.ts`
Expected: FAIL — `Failed to resolve import "./roles"`.

- [ ] **Step 13: Write the role map**

`packages/console-web/src/api/roles.ts`:

```ts
// The console role each proxied leader route needs: a copy of the allow-list in
// packages/console/src/swarmscribe_console/proxy.py (ROUTES). roles.test.ts reads that file
// and fails when the two differ. The web app disables what the person's role for a leader
// (FleetLeader.role) does not reach and names the role needed; the console and then the
// leader still check (a leader's own cap can refuse what the console allows).

import type { Role } from "./types";

export const ROLE_RANK: Record<Role, number> = { viewer: 0, operator: 1, admin: 2 };

export const ACTION_ROLE = {
  "status.view": "viewer",
  "locations.view": "viewer",
  "jobs.view": "viewer",
  "followers.view": "viewer",
  "tokens.view": "admin",
  "consent.view": "viewer",
  "locations.add": "admin",
  "locations.enable": "admin",
  "locations.disable": "admin",
  "locations.ingest": "operator",
  "jobs.retry": "operator",
  "jobs.cancel": "operator",
  "jobs.priority": "operator",
  "followers.drain": "operator",
  "followers.revoke": "admin",
  "tokens.create": "admin",
  "tokens.revoke": "admin",
} as const satisfies Record<string, Role>;

export type LeaderAction = keyof typeof ACTION_ROLE;

export function atLeast(held: Role, needed: Role): boolean {
  return ROLE_RANK[held] >= ROLE_RANK[needed];
}

export function neededRole(action: LeaderAction): Role {
  return ACTION_ROLE[action];
}

export function can(held: Role, action: LeaderAction): boolean {
  return atLeast(held, ACTION_ROLE[action]);
}
```

- [ ] **Step 14: Run everything**

Run: `npm test`
Expected: PASS — 33 tests in 4 files.

Run: `npm run typecheck` then `npm run lint`
Expected: both exit 0 with no output.

- [ ] **Step 15: Commit**

```bash
git add packages/console-web/src
git commit -m "Console web app: API client with CSRF and typed errors, error texts, role map

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: App primitives — router, session, polling, idle, theme

**Files:**
- Create: `packages/console-web/src/app/router.tsx`, `src/app/navigation.ts`, `src/app/theme.ts`, `src/app/activity.ts`, `src/app/usePoll.ts`, `src/app/session.tsx`
- Create: `packages/console-web/src/components/ErrorPanel.tsx`, `src/test/fixtures.ts`
- Test: `packages/console-web/src/app/router.test.tsx`, `src/app/usePoll.test.tsx`, `src/app/session.test.tsx`

**Interfaces:**
- Consumes: `api`, `ApiError`, `isAbort`, `setCsrfToken`, `setUnauthenticatedHandler` from `src/api/client.ts`; `describeError` from `src/api/errors.ts`; the types of Task 2; `mockFetch`, `reply`, `fail` from `src/test/fetchMock.ts`.
- Produces:
  - `src/app/router.tsx`: `RouterProvider({ children })`; `useLocation(): { pathname: string; search: string }`; `useNavigate(): (to: string, options?: { replace?: boolean }) => void`; `useSearchParam(name: string): string | null`; `matchPath(pattern: string, pathname: string): Record<string, string> | null`; `Link({ to, children, ...anchorProps })`.
  - `src/app/navigation.ts`: `safeReturnTo(value: string | null): string`; `signInUrl(returnTo: string): string`; `goToSignIn(): void`; `goToSignedOut(): void`.
  - `src/app/theme.ts`: `type ThemeChoice = "system" | "light" | "dark"`; `THEME_KEY`; `readTheme()`, `applyTheme(choice)`, `saveTheme(choice)`.
  - `src/app/activity.ts`: `IDLE_AFTER_MS`; `isIdle(now?)`; `noteActivity(now?)`; `onResume(listener): () => void`; `startActivityTracking(): () => void`; `setLastInputForTests(at: number)`.
  - `src/app/usePoll.ts`: `interface PollState<T> { data: T | undefined; error: unknown; loading: boolean; updatedAt: number | null; refresh: () => void }`; `usePoll<T>(load: (signal: AbortSignal) => Promise<T>, intervalMs: number | null, key: string): PollState<T>`.
  - `src/app/session.tsx`: `SessionProvider({ children })`; `useSession(): { session: SessionInfo; signOut: () => Promise<void> }`.
  - `src/components/ErrorPanel.tsx`: `ErrorPanel({ error, onRetry?, retryLabel? })` (renders `role="alert"`).
  - `src/test/fixtures.ts`: `NOW`, `SESSION`, `STATUS`, `leader(overrides?)`, `history()`.

- [ ] **Step 1: Write the failing router test**

`packages/console-web/src/app/router.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { Link, RouterProvider, matchPath, useLocation } from "./router";

function Where() {
  const { pathname, search } = useLocation();
  return <p>at {pathname + search}</p>;
}

describe("router", () => {
  it("matches patterns and decodes parameters", () => {
    expect(matchPath("/leaders/:name/:tab", "/leaders/eu-1.prod/jobs")).toEqual({
      name: "eu-1.prod",
      tab: "jobs",
    });
    expect(matchPath("/leaders/:name/:tab", "/leaders/a%20b/jobs")).toEqual({ name: "a b", tab: "jobs" });
    expect(matchPath("/leaders/:name/:tab", "/leaders/eu-1")).toBeNull();
    expect(matchPath("/leaders/:name/:tab", "/leaders//jobs")).toBeNull();
    expect(matchPath("/leaders/:name/:tab", "/leaders/%E0%A4%A/jobs")).toBeNull();
    expect(matchPath("/", "/")).toEqual({});
  });

  it("navigates on a plain click without reloading", async () => {
    render(
      <RouterProvider>
        <Link to="/leaders/eu-1/jobs?state=failed">Jobs</Link>
        <Where />
      </RouterProvider>,
    );
    await userEvent.click(screen.getByRole("link", { name: "Jobs" }));
    expect(screen.getByText("at /leaders/eu-1/jobs?state=failed")).toBeInTheDocument();
    expect(window.location.pathname).toBe("/leaders/eu-1/jobs");
  });
});
```

Run: `npx vitest run src/app/router.test.tsx`
Expected: FAIL — `Failed to resolve import "./router"`.

- [ ] **Step 2: Write the router**

`packages/console-web/src/app/router.tsx`:

```tsx
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type AnchorHTMLAttributes,
  type MouseEvent,
  type ReactNode,
} from "react";

// A small history-API router: the app has a handful of fixed routes and no nested data
// loading, so it does not need a router library. Routes are extensionless and outside /api
// and /auth (the console serves index.html only for those; see static.py). A leader
// drill-down URL always ends with its tab, so a leader name with a dot is never the last
// segment.

export interface AppLocation {
  pathname: string;
  search: string;
}

interface RouterValue {
  location: AppLocation;
  navigate: (to: string, options?: { replace?: boolean }) => void;
}

const RouterContext = createContext<RouterValue | null>(null);

function current(): AppLocation {
  return { pathname: window.location.pathname, search: window.location.search };
}

export function RouterProvider({ children }: { children: ReactNode }) {
  const [location, setLocation] = useState<AppLocation>(current);
  useEffect(() => {
    const onPop = () => setLocation(current());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  const navigate = useCallback((to: string, options?: { replace?: boolean }) => {
    if (options?.replace) window.history.replaceState(null, "", to);
    else window.history.pushState(null, "", to);
    setLocation(current());
  }, []);
  const value = useMemo(() => ({ location, navigate }), [location, navigate]);
  return <RouterContext.Provider value={value}>{children}</RouterContext.Provider>;
}

function useRouter(): RouterValue {
  const value = useContext(RouterContext);
  if (value === null) throw new Error("useRouter outside RouterProvider");
  return value;
}

export function useLocation(): AppLocation {
  return useRouter().location;
}

export function useNavigate(): RouterValue["navigate"] {
  return useRouter().navigate;
}

export function useSearchParam(name: string): string | null {
  return new URLSearchParams(useLocation().search).get(name);
}

/**
 * The parameters of `pattern` ("/leaders/:name/:tab") in `pathname`, decoded, or null.
 * A segment that does not decode does not match.
 */
export function matchPath(pattern: string, pathname: string): Record<string, string> | null {
  const want = pattern.split("/");
  const got = pathname.split("/");
  if (want.length !== got.length) return null;
  const params: Record<string, string> = {};
  for (let i = 0; i < want.length; i += 1) {
    const w = want[i] as string;
    const g = got[i] as string;
    if (w.startsWith(":")) {
      if (g === "") return null;
      try {
        params[w.slice(1)] = decodeURIComponent(g);
      } catch {
        return null;
      }
    } else if (w !== g) {
      return null;
    }
  }
  return params;
}

type LinkProps = Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "href"> & { to: string; children: ReactNode };

export function Link({ to, onClick, children, ...rest }: LinkProps) {
  const navigate = useNavigate();
  const handle = (event: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(event);
    if (
      event.defaultPrevented ||
      event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey
    ) {
      return;
    }
    event.preventDefault();
    navigate(to);
  };
  return (
    <a href={to} onClick={handle} {...rest}>
      {children}
    </a>
  );
}
```

Run: `npx vitest run src/app/router.test.tsx`
Expected: PASS — 2 tests.

- [ ] **Step 3: Write the idle tracker and the failing polling test**

`packages/console-web/src/app/activity.ts`:

```ts
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
```

`packages/console-web/src/app/usePoll.test.tsx`:

```tsx
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import { IDLE_AFTER_MS, noteActivity, setLastInputForTests } from "./activity";
import { usePoll } from "./usePoll";

function Probe({
  load,
  interval,
  id,
}: {
  load: (s: AbortSignal) => Promise<string>;
  interval: number | null;
  id: string;
}) {
  const { data, error, loading } = usePoll(load, interval, id);
  return (
    <p>
      {loading ? "loading" : "ready"}|{data ?? "-"}|{error instanceof ApiError ? error.code : "-"}
    </p>
  );
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
  });
}

describe("usePoll", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    setLastInputForTests(Date.now());
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("loads at once and again every interval", async () => {
    let n = 0;
    const load = vi.fn(async () => `v${++n}`);
    render(<Probe load={load} interval={10_000} id="a" />);
    await flush();
    expect(screen.getByText("ready|v1|-")).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(screen.getByText("ready|v2|-")).toBeInTheDocument();
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("keeps the last good data when a load fails", async () => {
    let n = 0;
    const load = vi.fn(async () => {
      n += 1;
      if (n === 2) throw new ApiError(503, "unavailable", "down");
      return `v${n}`;
    });
    render(<Probe load={load} interval={10_000} id="a" />);
    await flush();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(screen.getByText("ready|v1|unavailable")).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(screen.getByText("ready|v3|-")).toBeInTheDocument();
  });

  it("stops loading while the person is idle and resumes on input", async () => {
    const load = vi.fn(async () => "v");
    render(<Probe load={load} interval={10_000} id="a" />);
    await flush();
    expect(load).toHaveBeenCalledTimes(1);
    setLastInputForTests(Date.now() - IDLE_AFTER_MS);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(load).toHaveBeenCalledTimes(1);
    await act(async () => {
      noteActivity();
      await Promise.resolve();
    });
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("drops the old key's data when the key changes", async () => {
    const load = vi.fn(async () => "v");
    const never = vi.fn(() => new Promise<string>(() => undefined));
    const { rerender } = render(<Probe load={load} interval={null} id="a" />);
    await flush();
    expect(screen.getByText("ready|v|-")).toBeInTheDocument();
    rerender(<Probe load={never} interval={null} id="b" />);
    expect(screen.getByText("loading|-|-")).toBeInTheDocument();
  });
});
```

Run: `npx vitest run src/app/usePoll.test.tsx`
Expected: FAIL — `Failed to resolve import "./usePoll"`.

- [ ] **Step 4: Write `usePoll`**

`packages/console-web/src/app/usePoll.ts`:

```ts
import { useCallback, useEffect, useEffectEvent, useState } from "react";
import { isAbort } from "../api/client";
import { isIdle, onResume } from "./activity";

export interface PollState<T> {
  data: T | undefined;
  /** The last load's error; data, if any, is from the last successful load. */
  error: unknown;
  loading: boolean;
  /** When data last loaded (ms since the epoch), or null. */
  updatedAt: number | null;
  refresh: () => void;
}

interface Held<T> {
  key: string;
  data: T | undefined;
  error: unknown;
  loading: boolean;
  updatedAt: number | null;
}

/**
 * Loads now, then every `intervalMs` (null: load once, and again on refresh()). A change of
 * `key` starts over without the old key's data. Loads are skipped while the tab is hidden or
 * the person is idle (activity.ts), and run at once when either ends. A failed load keeps the
 * last good data and sets `error`; the next tick tries again.
 */
export function usePoll<T>(
  load: (signal: AbortSignal) => Promise<T>,
  intervalMs: number | null,
  key: string,
): PollState<T> {
  const [held, setHeld] = useState<Held<T>>({
    key,
    data: undefined,
    error: null,
    loading: true,
    updatedAt: null,
  });
  const [round, setRound] = useState(0);
  const loadNow = useEffectEvent((signal: AbortSignal) => load(signal));

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let stopped = false;
    let first = true;

    const schedule = () => {
      if (!stopped && intervalMs !== null) timer = setTimeout(() => void run(), intervalMs);
    };
    const run = async () => {
      clearTimeout(timer);
      const paused = document.visibilityState === "hidden" || isIdle();
      if (paused && !first) {
        schedule();
        return;
      }
      first = false;
      try {
        const data = await loadNow(controller.signal);
        if (stopped) return;
        setHeld({ key, data, error: null, loading: false, updatedAt: Date.now() });
      } catch (error) {
        if (stopped || isAbort(error)) return;
        setHeld((prev) => ({
          key,
          data: prev.key === key ? prev.data : undefined,
          error,
          loading: false,
          updatedAt: prev.key === key ? prev.updatedAt : null,
        }));
      }
      schedule();
    };
    const runIfVisible = () => {
      if (document.visibilityState === "visible") void run();
    };

    void run();
    document.addEventListener("visibilitychange", runIfVisible);
    const unsubscribe = onResume(() => void run());
    return () => {
      stopped = true;
      controller.abort();
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", runIfVisible);
      unsubscribe();
    };
  }, [key, intervalMs, round]);

  const refresh = useCallback(() => setRound((n) => n + 1), []);
  if (held.key !== key) return { data: undefined, error: null, loading: true, updatedAt: null, refresh };
  return { data: held.data, error: held.error, loading: held.loading, updatedAt: held.updatedAt, refresh };
}
```

Run: `npx vitest run src/app/usePoll.test.tsx`
Expected: PASS — 4 tests.

- [ ] **Step 5: Write the navigation helpers, the theme and the fixtures**

`packages/console-web/src/app/navigation.ts`:

```ts
// Full-page navigations, kept apart so tests can replace them (jsdom cannot navigate).
// Leaving the app through a full load drops every piece of in-memory state, which is what
// should happen when a session ends.

/** A path on this console to come back to after sign-in, or "/". */
export function safeReturnTo(value: string | null): string {
  if (!value || !value.startsWith("/") || value.startsWith("//") || value.includes("\\")) return "/";
  if (value.startsWith("/sign-in") || value.startsWith("/api") || value.startsWith("/auth")) return "/";
  return value.length > 512 ? "/" : value;
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
```

`packages/console-web/src/app/theme.ts`:

```ts
// Light, dark, or the system's choice (the default). The choice is the only thing the app
// keeps in the browser; it is applied as <html data-theme="light|dark">, and styles.css
// follows prefers-color-scheme when the attribute is absent.

export type ThemeChoice = "system" | "light" | "dark";

export const THEME_KEY = "swarmscribe-console-theme";

function storage(): Storage | null {
  try {
    // eslint-disable-next-line no-restricted-globals -- the theme is the one stored preference
    return localStorage;
  } catch {
    return null;
  }
}

export function readTheme(): ThemeChoice {
  const value = storage()?.getItem(THEME_KEY);
  return value === "light" || value === "dark" ? value : "system";
}

export function applyTheme(choice: ThemeChoice): void {
  const root = document.documentElement;
  if (choice === "system") delete root.dataset.theme;
  else root.dataset.theme = choice;
}

export function saveTheme(choice: ThemeChoice): void {
  try {
    if (choice === "system") storage()?.removeItem(THEME_KEY);
    else storage()?.setItem(THEME_KEY, choice);
  } catch {
    // Storage refused (private mode): the choice lasts for this page only.
  }
  applyTheme(choice);
}
```

`packages/console-web/src/test/fixtures.ts`:

```ts
import type { FleetLeader, HistoryPoint, LeaderStatus, SessionInfo } from "../api/types";

export const NOW = Date.parse("2026-10-04T12:00:00Z");

export const SESSION: SessionInfo = {
  provider: "entra",
  issuer: "https://login.microsoftonline.com/tenant/v2.0",
  subject: "person-1",
  email: "person@example.org",
  console_admin: false,
  csrf_token: "csrf-token-1",
  expires_at: "2026-10-04T20:00:00Z",
  idle_expires_at: "2026-10-04T13:00:00Z",
};

export const STATUS: LeaderStatus = {
  jobs: { queued: 3, leased: 1, completed: 10, failed: 1, cancelled: 0 },
  pools: [
    { pool: "default", queued: 2, leased: 1 },
    { pool: "gpu", queued: 1, leased: 0 },
  ],
  followers: { active: 3, draining: 1, revoked: 0, gone: 0 },
  follower_pools: [
    { pool: "default", active: 2, draining: 1, revoked: 0, gone: 0 },
    { pool: "gpu", active: 1, draining: 0, revoked: 0, gone: 0 },
  ],
  completed_last_hour: 7,
  completed_last_day: 30,
  oldest_queued_age_s: 420,
  failed_attempts_last_day: 1,
  locations: [
    {
      name: "intake",
      backend: "local",
      enabled: true,
      last_scan_at: "2026-10-04T11:59:00Z",
      last_scan_error: null,
      scan_requested: false,
      recordings: 12,
      consented: 10,
    },
    {
      name: "archive",
      backend: "local",
      enabled: true,
      last_scan_at: "2026-10-04T11:58:00Z",
      last_scan_error: "the root folder is not readable",
      scan_requested: false,
      recordings: 0,
      consented: 0,
    },
  ],
};

export function leader(overrides: Partial<FleetLeader> = {}): FleetLeader {
  return {
    name: "eu-1",
    labels: { env: "prod", region: "eu" },
    role: "operator",
    health: "reachable",
    enabled: true,
    last_polled_at: "2026-10-04T11:59:50Z",
    last_success_at: "2026-10-04T11:59:50Z",
    last_error: null,
    consecutive_failures: 0,
    summary: {
      queued: 3,
      completed_last_hour: 7,
      completed_last_day: 30,
      failed_attempts_last_day: 1,
      oldest_queued_age_s: 420,
      followers_active_by_pool: { default: 2, gpu: 1 },
      scan_errors: [{ location: "archive", error: "the root folder is not readable" }],
    },
    snapshot: { taken_at: "2026-10-04T11:59:50Z", status: STATUS },
    ...overrides,
  };
}

/** 24 hours of 5-minute buckets ending at NOW, with one unreachable bucket. */
export function history(): HistoryPoint[] {
  const points: HistoryPoint[] = [];
  for (let i = 287; i >= 0; i -= 1) {
    const at = new Date(NOW - i * 5 * 60 * 1000).toISOString();
    const down = i === 100;
    points.push({
      at,
      reachable: !down,
      queued: down ? null : 3,
      leased: down ? null : 1,
      completed_last_hour: down ? null : (i % 12) + 1,
      completed_last_day: down ? null : 30,
      failed_attempts_last_day: down ? null : 1,
      oldest_queued_age_s: down ? null : 420,
      followers_active: down ? null : 3,
    });
  }
  return points;
}
```

- [ ] **Step 6: Write the failing session test**

`packages/console-web/src/app/session.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { fail, mockFetch, reply } from "../test/fetchMock";
import { SESSION } from "../test/fixtures";
import * as navigation from "./navigation";
import { SessionProvider, useSession } from "./session";

function Who() {
  const { session, signOut } = useSession();
  return (
    <>
      <p>signed in as {session.email}</p>
      <button type="button" onClick={() => void signOut()}>
        Sign out
      </button>
    </>
  );
}

describe("SessionProvider", () => {
  it("loads the session and sends its CSRF token on later unsafe calls", async () => {
    const mock = mockFetch().on("GET /api/session", reply(200, SESSION)).on("POST /api/x", reply(200, {}));
    render(
      <SessionProvider>
        <Who />
      </SessionProvider>,
    );
    expect(await screen.findByText("signed in as person@example.org")).toBeInTheDocument();
    await api.post("/api/x");
    expect(mock.callsTo("POST /api/x")[0]?.headers["X-CSRF-Token"]).toBe(SESSION.csrf_token);
  });

  it("sends a person without a session to sign in", async () => {
    mockFetch().on("GET /api/session", fail(401, "unauthenticated"));
    const goToSignIn = vi.spyOn(navigation, "goToSignIn").mockImplementation(() => undefined);
    render(
      <SessionProvider>
        <Who />
      </SessionProvider>,
    );
    await vi.waitFor(() => expect(goToSignIn).toHaveBeenCalled());
  });

  it("signs out with the CSRF token and leaves for the signed-out page", async () => {
    const mock = mockFetch()
      .on("GET /api/session", reply(200, SESSION))
      .on("POST /api/session/logout", reply(204));
    const goToSignedOut = vi.spyOn(navigation, "goToSignedOut").mockImplementation(() => undefined);
    render(
      <SessionProvider>
        <Who />
      </SessionProvider>,
    );
    await userEvent.click(await screen.findByRole("button", { name: "Sign out" }));
    await vi.waitFor(() => expect(goToSignedOut).toHaveBeenCalled());
    expect(mock.callsTo("POST /api/session/logout")[0]?.headers["X-CSRF-Token"]).toBe(SESSION.csrf_token);
  });

  it("offers a retry when the console is down", async () => {
    let n = 0;
    mockFetch().on("GET /api/session", () =>
      ++n === 1 ? fail(503, "unavailable", "down") : reply(200, SESSION),
    );
    render(
      <SessionProvider>
        <Who />
      </SessionProvider>,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("temporarily unavailable");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("signed in as person@example.org")).toBeInTheDocument();
  });
});

describe("safeReturnTo", () => {
  it("keeps console paths and refuses anything else", () => {
    expect(navigation.safeReturnTo("/leaders/eu-1/jobs?state=failed")).toBe(
      "/leaders/eu-1/jobs?state=failed",
    );
    expect(navigation.safeReturnTo("//evil.example")).toBe("/");
    expect(navigation.safeReturnTo("https://evil.example")).toBe("/");
    expect(navigation.safeReturnTo("/\\evil")).toBe("/");
    expect(navigation.safeReturnTo("/sign-in?x=1")).toBe("/");
    expect(navigation.safeReturnTo(null)).toBe("/");
    expect(navigation.signInUrl("/admin/grants")).toBe("/sign-in?return_to=%2Fadmin%2Fgrants");
  });
});
```

Run: `npx vitest run src/app/session.test.tsx`
Expected: FAIL — `Failed to resolve import "./session"`.

- [ ] **Step 7: Write the error panel and the session provider**

`packages/console-web/src/components/ErrorPanel.tsx`:

```tsx
import { describeError } from "../api/errors";

/** An error as the person reads it: our title, the server's own text, and a retry. */
export function ErrorPanel({
  error,
  onRetry,
  retryLabel = "Try again",
}: {
  error: unknown;
  onRetry?: () => void;
  retryLabel?: string;
}) {
  const { title, detail } = describeError(error);
  return (
    <div className="error-panel" role="alert">
      <p className="error-title">{title}</p>
      {detail !== null && <p className="error-detail">{detail}</p>}
      {onRetry !== undefined && (
        <button type="button" className="button" onClick={onRetry}>
          {retryLabel}
        </button>
      )}
    </div>
  );
}
```

`packages/console-web/src/app/session.tsx`:

```tsx
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, setCsrfToken, setUnauthenticatedHandler } from "../api/client";
import type { SessionInfo } from "../api/types";
import { ErrorPanel } from "../components/ErrorPanel";
import { goToSignIn, goToSignedOut } from "./navigation";

interface SessionValue {
  session: SessionInfo;
  signOut: () => Promise<void>;
}

const SessionContext = createContext<SessionValue | null>(null);

export function useSession(): SessionValue {
  const value = useContext(SessionContext);
  if (value === null) throw new Error("useSession outside SessionProvider");
  return value;
}

type Loaded =
  | { state: "loading" }
  | { state: "ready"; session: SessionInfo }
  | { state: "failed"; error: unknown };

/**
 * Loads GET /api/session once, keeps its CSRF token for unsafe calls, and sends any 401
 * (a session that ended or expired) to the sign-in page.
 */
export function SessionProvider({ children }: { children: ReactNode }) {
  const [loaded, setLoaded] = useState<Loaded>({ state: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    setUnauthenticatedHandler(goToSignIn);
    const controller = new AbortController();
    api
      .get<SessionInfo>("/api/session", controller.signal)
      .then((session) => {
        setCsrfToken(session.csrf_token);
        setLoaded({ state: "ready", session });
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setLoaded({ state: "failed", error });
      });
    return () => controller.abort();
  }, [attempt]);

  const signOut = useCallback(async () => {
    try {
      await api.post("/api/session/logout");
    } finally {
      setCsrfToken(null);
      goToSignedOut();
    }
  }, []);

  const value = useMemo(
    () => (loaded.state === "ready" ? { session: loaded.session, signOut } : null),
    [loaded, signOut],
  );

  if (loaded.state === "loading") {
    return (
      <p className="page-message" role="status">
        Loading the console…
      </p>
    );
  }
  if (loaded.state === "failed" || value === null) {
    return (
      <main className="page-message">
        <h1>SwarmScribe console</h1>
        <ErrorPanel
          error={loaded.state === "failed" ? loaded.error : null}
          onRetry={() => {
            setLoaded({ state: "loading" });
            setAttempt((n) => n + 1);
          }}
        />
      </main>
    );
  }
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}
```

`session.tsx` imports `goToSignIn` and `goToSignedOut` by name and the test replaces them with `vi.spyOn(navigation, …)`; this works because Vitest's module namespace is live for spied exports. If a future Vitest stops allowing it, call them as `navigation.goToSignIn()` through a namespace import; do not change `navigation.ts`.

- [ ] **Step 8: Run everything**

Run: `npm test`
Expected: PASS — 44 tests in 7 files.

Run: `npm run typecheck` then `npm run lint`
Expected: both exit 0 with no output.

- [ ] **Step 9: Commit**

```bash
git add packages/console-web/src
git commit -m "Console web app: router, session with CSRF and sign-out, polling that stops when idle, theme

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Formatting and the 24-hour chart

**Files:**
- Create: `packages/console-web/src/lib/format.ts`, `src/components/Sparkline.tsx`
- Test: `packages/console-web/src/lib/format.test.ts`, `src/components/Sparkline.test.tsx`

**Interfaces:**
- Consumes: `HistoryPoint` from `src/api/types.ts`; `NOW`, `history()` from `src/test/fixtures.ts`; the `.sparkline*` classes in `src/styles.css`.
- Produces:
  - `src/lib/format.ts`: `formatCount(value: number | null | undefined): string` ("–" for none); `formatDuration(seconds: number): string`; `formatTime(iso: string, now?: number): string`; `oldestQueuedAge(ageAtSnapshot: number | null, takenAt: string | null, now: number): number | null`; `formatPools(byPool: Record<string, number>): string`; `labelPairs(labels: Record<string, string>): string[]`.
  - `src/components/Sparkline.tsx`: `geometry(points: HistoryPoint[], now: number): Geometry`; `describe(g: Geometry): string`; `Sparkline({ points, now })` (an `<svg role="img">` whose accessible name is `describe(...)`); constants `WIDTH`, `HEIGHT`.

- [ ] **Step 1: Write the failing format test**

`packages/console-web/src/lib/format.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { formatCount, formatDuration, formatPools, labelPairs, oldestQueuedAge } from "./format";

describe("format", () => {
  it("formats durations", () => {
    expect(formatDuration(0)).toBe("0 s");
    expect(formatDuration(59.9)).toBe("59 s");
    expect(formatDuration(60)).toBe("1 min");
    expect(formatDuration(3600)).toBe("1 h");
    expect(formatDuration(3600 + 5 * 60)).toBe("1 h 5 min");
    expect(formatDuration(2 * 86400 + 4 * 3600)).toBe("2 d 4 h");
    expect(formatDuration(-5)).toBe("0 s");
  });

  it("adds the time since the snapshot to the oldest queued age", () => {
    const takenAt = "2026-10-04T10:00:00Z";
    const now = Date.parse("2026-10-04T10:00:30Z");
    expect(oldestQueuedAge(420, takenAt, now)).toBe(450);
    expect(oldestQueuedAge(null, takenAt, now)).toBeNull();
    // A snapshot "in the future" (clock skew) adds nothing rather than subtracting.
    expect(oldestQueuedAge(420, "2026-10-04T10:01:00Z", now)).toBe(420);
  });

  it("formats pools and labels", () => {
    expect(formatPools({ gpu: 1, default: 2 })).toBe("default 2 · gpu 1");
    expect(formatPools({})).toBe("none");
    expect(labelPairs({ region: "eu", env: "prod" })).toEqual(["env=prod", "region=eu"]);
    expect(formatCount(null)).toBe("–");
  });
});
```

Run: `npx vitest run src/lib/format.test.ts`
Expected: FAIL — `Failed to resolve import "./format"`.

- [ ] **Step 2: Write the formatters**

`packages/console-web/src/lib/format.ts`:

```ts
// Display helpers. Times are shown in the browser's locale and time zone.

const COUNT = new Intl.NumberFormat();
const CLOCK = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const DATE_TIME = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });
const DAY_MS = 24 * 60 * 60 * 1000;

export function formatCount(value: number | null | undefined): string {
  return value === null || value === undefined ? "–" : COUNT.format(value);
}

/** 45 s, 12 min, 3 h 5 min, 2 d 4 h. */
export function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
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
  if (ageAtSnapshot === null) return null;
  const taken = takenAt === null ? Number.NaN : new Date(takenAt).getTime();
  const since = Number.isNaN(taken) ? 0 : Math.max(0, (now - taken) / 1000);
  return ageAtSnapshot + since;
}

/** "default 2 · gpu 1", pools sorted by name; "none" when empty. */
export function formatPools(byPool: Record<string, number>): string {
  const entries = Object.entries(byPool).sort(([a], [b]) => a.localeCompare(b));
  return entries.length === 0 ? "none" : entries.map(([pool, n]) => `${pool} ${formatCount(n)}`).join(" · ");
}

/** Labels as "key=value" strings, sorted. */
export function labelPairs(labels: Record<string, string>): string[] {
  return Object.entries(labels)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([key, value]) => `${key}=${value}`);
}
```

Run: `npx vitest run src/lib/format.test.ts`
Expected: PASS — 3 tests.

- [ ] **Step 3: Write the failing chart test**

`packages/console-web/src/components/Sparkline.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe as group, expect, it } from "vitest";
import type { HistoryPoint } from "../api/types";
import { NOW, history } from "../test/fixtures";
import { Sparkline, describe, geometry } from "./Sparkline";

function point(minutesAgo: number, value: number | null, reachable = true): HistoryPoint {
  return {
    at: new Date(NOW - minutesAgo * 60_000).toISOString(),
    reachable,
    queued: null,
    leased: null,
    completed_last_hour: value,
    completed_last_day: null,
    failed_attempts_last_day: null,
    oldest_queued_age_s: null,
    followers_active: null,
  };
}

group("Sparkline", () => {
  it("draws one line through consecutive buckets", () => {
    const g = geometry([point(15, 1), point(10, 2), point(5, 4)], NOW);
    expect(g.segments).toHaveLength(1);
    expect(g.latest).toBe(4);
    expect(g.peak).toBe(4);
  });

  it("breaks the line at an unreachable bucket and marks it", () => {
    const g = geometry([point(20, 1), point(15, 2), point(10, null, false), point(5, 3), point(0, 3)], NOW);
    expect(g.segments).toHaveLength(2);
    expect(g.downCount).toBe(1);
    expect(describe(g)).toBe(
      "Jobs completed per hour over the last 24 hours: latest 3, highest 3. Unreachable in 1 five-minute period.",
    );
  });

  it("breaks the line across missing buckets and shows a lone point as a dot", () => {
    const g = geometry([point(60, 1), point(30, 2), point(25, 2)], NOW);
    expect(g.dots).toHaveLength(1);
    expect(g.segments).toHaveLength(1);
  });

  it("ignores points older than the window", () => {
    const g = geometry([point(25 * 60, 50), point(5, 1)], NOW);
    expect(g.peak).toBe(1);
  });

  it("says so when there is no history", () => {
    expect(describe(geometry([], NOW))).toBe("No throughput history yet.");
  });

  it("renders an image with its description as the accessible name", () => {
    render(<Sparkline points={history()} now={NOW} />);
    const chart = screen.getByRole("img");
    expect(chart).toHaveAccessibleName(
      /Jobs completed per hour over the last 24 hours: latest 1, highest 12\./,
    );
    expect(chart).toHaveAccessibleName(/Unreachable in 1 five-minute period\./);
  });
});
```

Run: `npx vitest run src/components/Sparkline.test.tsx`
Expected: FAIL — `Failed to resolve import "./Sparkline"`.

- [ ] **Step 4: Write the chart**

`packages/console-web/src/components/Sparkline.tsx`:

```tsx
import { useId } from "react";
import type { HistoryPoint } from "../api/types";

// Hand-written SVG (no chart library: the CSP forbids injected <style>). Plots
// completed_last_hour from each 5-minute history bucket: each point is how many jobs the
// leader completed in the hour before that snapshot, so the line is the leader's rolling
// hourly throughput. The line breaks where a bucket is missing or the leader was
// unreachable; unreachable buckets are also marked along the bottom edge.

export const WIDTH = 160;
export const HEIGHT = 36;
const PAD = 3;
const WINDOW_MS = 24 * 60 * 60 * 1000;
const GAP_MS = 10 * 60 * 1000; // more than one missing 5-minute bucket breaks the line

export interface Geometry {
  segments: string[];
  dots: { x: number; y: number }[];
  down: number[];
  latest: number | null;
  peak: number;
  downCount: number;
}

export function geometry(points: HistoryPoint[], now: number): Geometry {
  const start = now - WINDOW_MS;
  // The first bucket can start up to 5 minutes before the window (history is bucketed).
  const inWindow = points.filter((p) => {
    const at = Date.parse(p.at);
    return !Number.isNaN(at) && at >= start - GAP_MS / 2;
  });
  const x = (t: number) =>
    PAD + ((Math.min(Math.max(t, start), now) - start) / WINDOW_MS) * (WIDTH - 2 * PAD);
  const values = inWindow.map((p) => (p.reachable ? p.completed_last_hour : null));
  const peak = Math.max(1, ...values.filter((v): v is number => v !== null));
  const y = (v: number) => HEIGHT - PAD - (v / peak) * (HEIGHT - 2 * PAD);

  const runs: { x: number; y: number }[][] = [];
  let run: { x: number; y: number }[] = [];
  let lastAt = Number.NEGATIVE_INFINITY;
  const down: number[] = [];
  let latest: number | null = null;
  for (const point of inWindow) {
    const at = Date.parse(point.at);
    const value = point.reachable ? point.completed_last_hour : null;
    if (!point.reachable) down.push(Number(x(at).toFixed(1)));
    if (value === null || at - lastAt > GAP_MS) {
      if (run.length > 0) runs.push(run);
      run = [];
    }
    if (value !== null) {
      run.push({ x: Number(x(at).toFixed(1)), y: Number(y(value).toFixed(1)) });
      latest = value;
    }
    lastAt = at;
  }
  if (run.length > 0) runs.push(run);

  return {
    segments: runs
      .filter((r) => r.length > 1)
      .map((r) => r.map((p, i) => `${i === 0 ? "M" : "L"}${p.x} ${p.y}`).join(" ")),
    dots: runs.filter((r) => r.length === 1).map((r) => r[0] as { x: number; y: number }),
    down,
    latest,
    peak: values.some((v) => v !== null) ? peak : 0,
    downCount: down.length,
  };
}

export function describe(g: Geometry): string {
  if (g.latest === null && g.downCount === 0) return "No throughput history yet.";
  const parts = [
    `Jobs completed per hour over the last 24 hours: latest ${g.latest ?? "unknown"}, highest ${g.peak}.`,
  ];
  if (g.downCount > 0) {
    parts.push(`Unreachable in ${g.downCount} five-minute ${g.downCount === 1 ? "period" : "periods"}.`);
  }
  return parts.join(" ");
}

export function Sparkline({ points, now }: { points: HistoryPoint[]; now: number }) {
  const titleId = useId();
  const g = geometry(points, now);
  return (
    <svg
      className="sparkline"
      role="img"
      aria-labelledby={titleId}
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      width={WIDTH}
      height={HEIGHT}
      preserveAspectRatio="none"
    >
      <title id={titleId}>{describe(g)}</title>
      <line className="sparkline-axis" x1={PAD} x2={WIDTH - PAD} y1={HEIGHT - PAD} y2={HEIGHT - PAD} />
      {g.segments.map((d) => (
        <path key={d} className="sparkline-line" d={d} />
      ))}
      {g.dots.map((p) => (
        <circle key={`${p.x},${p.y}`} className="sparkline-dot" cx={p.x} cy={p.y} r={1.5} />
      ))}
      {g.down.map((dx) => (
        <rect key={dx} className="sparkline-down" x={dx - 0.75} y={HEIGHT - PAD} width={1.5} height={PAD} />
      ))}
    </svg>
  );
}
```

Run: `npx vitest run src/components/Sparkline.test.tsx`
Expected: PASS — 6 tests.

- [ ] **Step 5: Run everything and commit**

Run: `npm test`
Expected: PASS — 53 tests in 9 files.

Run: `npm run typecheck` then `npm run lint`
Expected: both exit 0 with no output.

```bash
git add packages/console-web/src
git commit -m "Console web app: display formatting and the hand-written 24-hour throughput chart

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: The signed-in shell and the fleet overview

**Files:**
- Create: `packages/console-web/src/app/useNow.ts`, `src/app/usePageTitle.ts`, `src/app/fleet.tsx`
- Create: `packages/console-web/src/components/HealthBadge.tsx`, `src/components/ThroughputChart.tsx`, `src/components/Layout.tsx`
- Create: `packages/console-web/src/pages/SignInPage.tsx`, `src/pages/NotFoundPage.tsx`, `src/pages/FleetPage.tsx`
- Modify: `packages/console-web/src/App.tsx` (replace the stub), `src/main.tsx` (apply the theme)
- Delete: `packages/console-web/src/App.test.tsx` (the stub's smoke test)
- Test: `packages/console-web/src/pages/FleetPage.test.tsx`

**Interfaces:**
- Consumes: everything Tasks 2–4 produce (`api`, `leaderPath`, `describeError`, router hooks and `Link`, `SessionProvider`/`useSession`, `usePoll`/`PollState`, `isIdle`/`onResume`/`startActivityTracking`, `readTheme`/`saveTheme`/`applyTheme`, `safeReturnTo`, `ErrorPanel`, the formatters, `Sparkline`, the fixtures and `mockFetch`).
- Produces:
  - `src/app/fleet.tsx`: `FLEET_REFRESH_MS = 10_000`; `FleetProvider({ children })`; `useFleet(): PollState<FleetLeader[]>`.
  - `src/app/useNow.ts`: `useNow(everyMs: number): number`. `src/app/usePageTitle.ts`: `usePageTitle(title: string): void`.
  - `src/components/HealthBadge.tsx`: `healthText(leader): { label: string; tone: "ok" | "warn" | "bad" | "muted" }`; `HealthBadge({ leader })`.
  - `src/components/ThroughputChart.tsx`: `HISTORY_REFRESH_MS`; `ThroughputChart({ name, now })`.
  - `src/components/Layout.tsx`: `interface NavItem { to: string; label: string; match: (pathname: string) => boolean }`; `Layout({ nav, children })` (header, "Skip to main content", theme select, Sign out, `<main id="main">`).
  - `src/pages/FleetPage.tsx`: `FleetPage()`; `LeaderName({ leader })` (plain text here; C3b makes it a link).
  - `src/App.tsx`: `App()` with routes `/sign-in` → `SignInPage`, `/` → `FleetPage`, anything else → `NotFoundPage`.

- [ ] **Step 1: Write the failing overview test**

`packages/console-web/src/pages/FleetPage.test.tsx`:

```tsx
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setLastInputForTests } from "../app/activity";
import { FleetProvider } from "../app/fleet";
import { RouterProvider } from "../app/router";
import { fail, mockFetch, reply, type FetchMock } from "../test/fetchMock";
import { NOW, STATUS, history, leader } from "../test/fixtures";
import { FleetPage } from "./FleetPage";

function renderFleet() {
  return render(
    <RouterProvider>
      <FleetProvider>
        <FleetPage />
      </FleetProvider>
    </RouterProvider>,
  );
}

function withHistory(mock: FetchMock, ...names: string[]): FetchMock {
  for (const name of names) mock.on(`GET /api/leaders/${name}/history?hours=24`, reply(200, history()));
  return mock;
}

const EU = leader();
const US = leader({
  name: "us-1",
  labels: { env: "prod", region: "us" },
  health: "unreachable",
  consecutive_failures: 4,
  last_error: "connect_error",
  last_success_at: "2026-10-04T11:50:00Z",
  snapshot: { taken_at: "2026-10-04T11:50:00Z", status: STATUS },
});

describe("FleetPage", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(NOW);
    setLastInputForTests(NOW);
  });
  afterEach(() => vi.useRealTimers());

  it("shows one row per leader with the overview figures", async () => {
    withHistory(mockFetch().on("GET /api/fleet", reply(200, [EU])), "eu-1");
    renderFleet();
    const row = (await screen.findByRole("rowheader", { name: /eu-1/ })).closest("tr") as HTMLElement;
    const cells = within(row)
      .getAllByRole("cell")
      .map((cell) => cell.textContent);
    expect(cells.slice(0, 8)).toEqual([
      "Reachable",
      "3",
      "7",
      "30",
      "1",
      "default 2 · gpu 1",
      // 420 s at the snapshot, taken 10 s before NOW.
      "7 min",
      "archive: the root folder is not readable",
    ]);
    expect(screen.getByRole("columnheader", { name: "Oldest queued job (since created)" })).toBeInTheDocument();
    expect(await within(row).findByRole("img")).toHaveAccessibleName(/Jobs completed per hour/);
  });

  it("shows an unreachable leader with stale figures and keeps the others working", async () => {
    withHistory(mockFetch().on("GET /api/fleet", reply(200, [EU, US])), "eu-1", "us-1");
    renderFleet();
    const usRow = (await screen.findByRole("rowheader", { name: /us-1/ })).closest("tr") as HTMLElement;
    expect(usRow).toHaveClass("is-stale");
    expect(within(usRow).getByText("Unreachable")).toBeInTheDocument();
    expect(within(usRow).getByText(/Figures as of/)).toBeInTheDocument();
    expect(within(usRow).getByText("Last error: connect_error")).toBeInTheDocument();
    const euRow = screen.getByRole("rowheader", { name: /eu-1/ }).closest("tr") as HTMLElement;
    expect(within(euRow).getByText("Reachable")).toBeInTheDocument();
  });

  it("shows a leader that never answered without figures", async () => {
    const pending = leader({
      name: "new-1",
      health: "pending",
      consecutive_failures: 2,
      summary: null,
      snapshot: null,
    });
    withHistory(mockFetch().on("GET /api/fleet", reply(200, [pending])), "new-1");
    renderFleet();
    const row = (await screen.findByRole("rowheader", { name: /new-1/ })).closest("tr") as HTMLElement;
    expect(within(row).getByText("Not answering yet (2 failed polls)")).toBeInTheDocument();
    expect(within(row).getByText("No successful poll yet")).toBeInTheDocument();
  });

  it("filters by label and keeps the filter in the address", async () => {
    vi.useRealTimers();
    withHistory(mockFetch().on("GET /api/fleet", reply(200, [EU, US])), "eu-1", "us-1");
    renderFleet();
    await screen.findByRole("rowheader", { name: /us-1/ });
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Label" }), "region=us");
    expect(screen.queryByRole("rowheader", { name: /eu-1/ })).not.toBeInTheDocument();
    expect(screen.getByRole("rowheader", { name: /us-1/ })).toBeInTheDocument();
    expect(window.location.search).toBe("?label=region%3Dus");
    expect(screen.getByText(/1 of 2 leaders/)).toBeInTheDocument();
  });

  it("filters by a label whose value holds an equals sign", async () => {
    vi.useRealTimers();
    const odd = leader({ name: "odd-1", labels: { team: "a=b" } });
    withHistory(mockFetch().on("GET /api/fleet", reply(200, [EU, odd])), "eu-1", "odd-1");
    renderFleet();
    await screen.findByRole("rowheader", { name: /odd-1/ });
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Label" }), "team=a=b");
    expect(screen.getByRole("rowheader", { name: /odd-1/ })).toBeInTheDocument();
    expect(screen.queryByRole("rowheader", { name: /eu-1/ })).not.toBeInTheDocument();
    expect(window.location.search).toBe("?label=team%3Da%3Db");
  });

  it("keeps the last figures and says so when a refresh fails", async () => {
    vi.useRealTimers();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let n = 0;
    withHistory(
      mockFetch().on("GET /api/fleet", () =>
        ++n === 1 ? reply(200, [EU]) : fail(503, "unavailable", "down"),
      ),
      "eu-1",
    );
    renderFleet();
    expect(await screen.findByRole("rowheader", { name: /eu-1/ })).toBeInTheDocument();
    await act(() => vi.advanceTimersByTimeAsync(10_000));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not refresh the fleet");
    expect(screen.getByRole("rowheader", { name: /eu-1/ })).toBeInTheDocument();
  });

  it("says when the person sees no leaders", async () => {
    mockFetch().on("GET /api/fleet", reply(200, []));
    renderFleet();
    expect(await screen.findByText(/You hold no role on any leader yet/)).toBeInTheDocument();
  });

  it("shows the error with a retry when the first load fails", async () => {
    mockFetch().on("GET /api/fleet", fail(503, "unavailable", "service temporarily unavailable"));
    renderFleet();
    expect(await screen.findByRole("alert")).toHaveTextContent("temporarily unavailable");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});
```

Run: `npx vitest run src/pages/FleetPage.test.tsx`
Expected: FAIL — `Failed to resolve import "../app/fleet"`.

- [ ] **Step 2: Write the small hooks and the fleet context**

`packages/console-web/src/app/useNow.ts`:

```ts
import { useEffect, useState } from "react";

/** The current time, updated every `everyMs`, for ages shown on screen. */
export function useNow(everyMs: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), everyMs);
    return () => clearInterval(timer);
  }, [everyMs]);
  return now;
}
```

`packages/console-web/src/app/usePageTitle.ts`:

```ts
import { useEffect } from "react";

export function usePageTitle(title: string): void {
  useEffect(() => {
    document.title = `${title} · SwarmScribe console`;
  }, [title]);
}
```

`packages/console-web/src/app/fleet.tsx`:

```tsx
import { createContext, useContext, type ReactNode } from "react";
import { api } from "../api/client";
import type { FleetLeader } from "../api/types";
import { usePoll, type PollState } from "./usePoll";

/**
 * Every 10 s. The poller records a dead leader's third failure within about 50 s of its
 * death, so a 10 s refresh shows it unreachable inside spec 1's one minute.
 */
export const FLEET_REFRESH_MS = 10_000;

const FleetContext = createContext<PollState<FleetLeader[]> | null>(null);

const loadFleet = (signal: AbortSignal) => api.get<FleetLeader[]>("/api/fleet", signal);

/** GET /api/fleet for the whole app: the overview and each leader's drill-down header. */
export function FleetProvider({ children }: { children: ReactNode }) {
  const fleet = usePoll(loadFleet, FLEET_REFRESH_MS, "fleet");
  return <FleetContext.Provider value={fleet}>{children}</FleetContext.Provider>;
}

export function useFleet(): PollState<FleetLeader[]> {
  const value = useContext(FleetContext);
  if (value === null) throw new Error("useFleet outside FleetProvider");
  return value;
}
```

- [ ] **Step 3: Write the health badge and the chart loader**

`packages/console-web/src/components/HealthBadge.tsx`:

```tsx
import type { FleetLeader } from "../api/types";

type Tone = "ok" | "warn" | "bad" | "muted";

export function healthText(leader: Pick<FleetLeader, "health" | "consecutive_failures">): {
  label: string;
  tone: Tone;
} {
  const failures = leader.consecutive_failures;
  const failed = failures === 1 ? "1 failed poll" : `${failures} failed polls`;
  switch (leader.health) {
    case "reachable":
      return failures > 0
        ? { label: `Reachable (${failed})`, tone: "warn" }
        : { label: "Reachable", tone: "ok" };
    case "pending":
      return failures > 0
        ? { label: `Not answering yet (${failed})`, tone: "warn" }
        : { label: "Waiting for first poll", tone: "muted" };
    case "unreachable":
      return { label: "Unreachable", tone: "bad" };
    case "credential_revoked":
      return { label: "Credential revoked", tone: "bad" };
    case "disabled":
      return { label: "Disabled in the console", tone: "muted" };
    default:
      return { label: String(leader.health), tone: "muted" };
  }
}

/** Health as words in a coloured badge: the colour is never the only signal. */
export function HealthBadge({ leader }: { leader: Pick<FleetLeader, "health" | "consecutive_failures"> }) {
  const { label, tone } = healthText(leader);
  return <span className={`badge badge-${tone}`}>{label}</span>;
}
```

`packages/console-web/src/components/ThroughputChart.tsx`:

```tsx
import { useCallback } from "react";
import { api, leaderPath } from "../api/client";
import type { HistoryPoint } from "../api/types";
import { usePoll } from "../app/usePoll";
import { Sparkline } from "./Sparkline";

/** History moves in 5-minute buckets, so it is read every 5 minutes. */
export const HISTORY_REFRESH_MS = 5 * 60 * 1000;

/** One leader's 24-hour throughput, from GET /api/leaders/{name}/history?hours=24. */
export function ThroughputChart({ name, now }: { name: string; now: number }) {
  const load = useCallback(
    (signal: AbortSignal) => api.get<HistoryPoint[]>(leaderPath(name, "history?hours=24"), signal),
    [name],
  );
  const { data, error } = usePoll(load, HISTORY_REFRESH_MS, `history:${name}`);
  if (data === undefined) {
    return <span className="muted">{error ? "History unavailable" : "Loading…"}</span>;
  }
  return <Sparkline points={data} now={now} />;
}
```

- [ ] **Step 4: Write the layout**

`packages/console-web/src/components/Layout.tsx`:

```tsx
import { useEffect, useRef, useState, type ReactNode } from "react";
import { isIdle, onResume } from "../app/activity";
import { Link, useLocation } from "../app/router";
import { useSession } from "../app/session";
import { readTheme, saveTheme, type ThemeChoice } from "../app/theme";

export interface NavItem {
  to: string;
  label: string;
  /** Whether the item is the current section for this path. */
  match: (pathname: string) => boolean;
}

function ThemeSelect() {
  const [choice, setChoice] = useState<ThemeChoice>(readTheme);
  return (
    <label className="theme-select">
      Theme
      <select
        value={choice}
        onChange={(event) => {
          const next = event.target.value as ThemeChoice;
          setChoice(next);
          saveTheme(next);
        }}
      >
        <option value="system">System</option>
        <option value="light">Light</option>
        <option value="dark">Dark</option>
      </select>
    </label>
  );
}

/** Shown once background refreshes have stopped because the person has been idle. */
function IdleNotice() {
  const [idle, setIdle] = useState(false);
  useEffect(() => {
    const timer = setInterval(() => setIdle(isIdle()), 30_000);
    const unsubscribe = onResume(() => setIdle(false));
    return () => {
      clearInterval(timer);
      unsubscribe();
    };
  }, []);
  if (!idle) return null;
  return (
    <p className="notice" role="status">
      Updates are paused because you have been inactive. Press any key or click to resume.
    </p>
  );
}

export function Layout({ nav, children }: { nav: NavItem[]; children: ReactNode }) {
  const { session, signOut } = useSession();
  const { pathname } = useLocation();
  const main = useRef<HTMLElement>(null);
  const first = useRef(true);

  // After an in-app navigation, move focus to the new page so keyboard and screen-reader
  // users start at its content (not on the link they left behind).
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    main.current?.focus();
  }, [pathname]);

  return (
    <>
      <a className="skip-link" href="#main">
        Skip to main content
      </a>
      <header className="app-header">
        <Link to="/" className="brand">
          SwarmScribe console
        </Link>
        <nav aria-label="Main">
          <ul className="nav-list">
            {nav.map((item) => {
              const current = item.match(pathname);
              return (
                <li key={item.to}>
                  <Link to={item.to} className="nav-link" aria-current={current ? "page" : undefined}>
                    {item.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
        <div className="header-tools">
          <span className="who">{session.email ?? session.subject}</span>
          <ThemeSelect />
          <button type="button" className="button" onClick={() => void signOut()}>
            Sign out
          </button>
        </div>
      </header>
      <IdleNotice />
      <main id="main" ref={main} tabIndex={-1}>
        {children}
      </main>
    </>
  );
}
```

- [ ] **Step 5: Write the sign-in and not-found pages**

`packages/console-web/src/pages/SignInPage.tsx`:

```tsx
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ProvidersOut } from "../api/types";
import { safeReturnTo } from "../app/navigation";
import { useSearchParam } from "../app/router";
import { usePageTitle } from "../app/usePageTitle";
import { ErrorPanel } from "../components/ErrorPanel";

const PROVIDER_NAMES: Record<string, string> = { entra: "Microsoft Entra ID", google: "Google" };

/**
 * Sign-in starts with a full-page visit to /auth/login (the console redirects to the
 * provider and back), so these are plain links, not in-app navigations.
 */
export function SignInPage() {
  usePageTitle("Sign in");
  const returnTo = safeReturnTo(useSearchParam("return_to"));
  const signedOut = useSearchParam("signed_out") === "1";
  const [providers, setProviders] = useState<string[] | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    const controller = new AbortController();
    api
      .get<ProvidersOut>("/auth/providers", controller.signal)
      .then((answer) => setProviders(answer.providers))
      .catch((caught: unknown) => {
        if (!controller.signal.aborted) setError(caught);
      });
    return () => controller.abort();
  }, []);

  return (
    <main id="main" className="sign-in">
      <h1>Sign in to the SwarmScribe console</h1>
      {signedOut && <p role="status">You have signed out.</p>}
      {error !== null && <ErrorPanel error={error} />}
      {providers === null && error === null && <p>Loading sign-in options…</p>}
      {providers !== null && providers.length === 0 && <p>No sign-in provider is configured.</p>}
      {providers !== null && providers.length > 0 && (
        <ul className="provider-list">
          {providers.map((provider) => {
            const params = new URLSearchParams({ provider });
            if (returnTo !== "/") params.set("return_to", returnTo);
            return (
              <li key={provider}>
                <a className="button button-primary" href={`/auth/login?${params.toString()}`}>
                  Sign in with {PROVIDER_NAMES[provider] ?? provider}
                </a>
              </li>
            );
          })}
        </ul>
      )}
    </main>
  );
}
```

`packages/console-web/src/pages/NotFoundPage.tsx`:

```tsx
import { Link } from "../app/router";
import { usePageTitle } from "../app/usePageTitle";

export function NotFoundPage() {
  usePageTitle("Not found");
  return (
    <>
      <h1>Page not found</h1>
      <p>
        There is no console page at this address. <Link to="/">Go to the fleet overview</Link>.
      </p>
    </>
  );
}
```

- [ ] **Step 6: Write the overview page**

`packages/console-web/src/pages/FleetPage.tsx`:

```tsx
import type { ReactNode } from "react";
import { describeError } from "../api/errors";
import type { FleetLeader } from "../api/types";
import { useFleet } from "../app/fleet";
import { useNavigate, useSearchParam } from "../app/router";
import { useNow } from "../app/useNow";
import { usePageTitle } from "../app/usePageTitle";
import { ErrorPanel } from "../components/ErrorPanel";
import { HealthBadge } from "../components/HealthBadge";
import { ThroughputChart } from "../components/ThroughputChart";
import {
  formatCount,
  formatDuration,
  formatPools,
  formatTime,
  labelPairs,
  oldestQueuedAge,
} from "../lib/format";

function matchesLabel(leader: FleetLeader, label: string | null): boolean {
  if (!label) return true;
  const at = label.indexOf("=");
  if (at < 1) return false;
  return leader.labels[label.slice(0, at)] === label.slice(at + 1);
}

function LabelFilter({ leaders, value }: { leaders: FleetLeader[]; value: string | null }) {
  const navigate = useNavigate();
  const pairs = [...new Set(leaders.flatMap((leader) => labelPairs(leader.labels)))].sort();
  if (value && !pairs.includes(value)) pairs.unshift(value);
  return (
    <label className="field-inline">
      Label
      <select
        value={value ?? ""}
        onChange={(event) => {
          const next = event.target.value;
          navigate(next ? `/?label=${encodeURIComponent(next)}` : "/", { replace: true });
        }}
      >
        <option value="">All leaders</option>
        {pairs.map((pair) => (
          <option key={pair} value={pair}>
            {pair}
          </option>
        ))}
      </select>
    </label>
  );
}

function HealthCell({ leader, now }: { leader: FleetLeader; now: number }) {
  const stale = leader.health !== "reachable" && leader.snapshot !== null;
  return (
    <>
      <HealthBadge leader={leader} />
      {stale && leader.snapshot !== null && (
        <span className="cell-note">Figures as of {formatTime(leader.snapshot.taken_at, now)}</span>
      )}
      {leader.summary === null && <span className="cell-note">No successful poll yet</span>}
      {leader.health === "unreachable" && leader.last_error && (
        <span className="cell-note">Last error: {leader.last_error}</span>
      )}
    </>
  );
}

function ScanErrors({ leader }: { leader: FleetLeader }) {
  const errors = leader.summary?.scan_errors ?? [];
  if (errors.length === 0) return <span className="muted">None</span>;
  return (
    <ul className="cell-list">
      {errors.map((item) => (
        <li key={item.location}>
          <strong>{item.location}</strong>: {item.error}
        </li>
      ))}
    </ul>
  );
}

/** The leader's name. C3b turns it into the link to the leader's drill-down. */
export function LeaderName({ leader }: { leader: FleetLeader }): ReactNode {
  return <span className="leader-name">{leader.name}</span>;
}

function LeaderRow({ leader, now }: { leader: FleetLeader; now: number }) {
  const s = leader.summary;
  const stale = leader.health !== "reachable" && s !== null;
  const age =
    s === null ? null : oldestQueuedAge(s.oldest_queued_age_s, leader.snapshot?.taken_at ?? null, now);
  return (
    <tr className={stale ? "is-stale" : undefined}>
      <th scope="row">
        <LeaderName leader={leader} />
        <ul className="labels" aria-label="Labels">
          {labelPairs(leader.labels).map((pair) => (
            <li key={pair} className="label-chip">
              {pair}
            </li>
          ))}
        </ul>
      </th>
      <td>
        <HealthCell leader={leader} now={now} />
      </td>
      <td className="num">{formatCount(s?.queued)}</td>
      <td className="num">{formatCount(s?.completed_last_hour)}</td>
      <td className="num">{formatCount(s?.completed_last_day)}</td>
      <td className="num">{formatCount(s?.failed_attempts_last_day)}</td>
      <td>{s === null ? "–" : formatPools(s.followers_active_by_pool)}</td>
      <td>{s === null ? "–" : age === null ? "Nothing queued" : formatDuration(age)}</td>
      <td>
        <ScanErrors leader={leader} />
      </td>
      <td>
        <ThroughputChart name={leader.name} now={now} />
      </td>
    </tr>
  );
}

export function FleetPage() {
  usePageTitle("Fleet");
  const { data, error, updatedAt, refresh } = useFleet();
  const label = useSearchParam("label");
  const now = useNow(5_000);

  if (data === undefined) {
    return (
      <>
        <h1>Fleet</h1>
        {error ? <ErrorPanel error={error} onRetry={refresh} /> : <p role="status">Loading the fleet…</p>}
      </>
    );
  }

  const shown = data.filter((leader) => matchesLabel(leader, label));
  return (
    <>
      <div className="page-head">
        <h1>Fleet</h1>
        <LabelFilter leaders={data} value={label} />
      </div>
      <p className="muted" aria-live="polite">
        {shown.length === data.length
          ? `${data.length} ${data.length === 1 ? "leader" : "leaders"}`
          : `${shown.length} of ${data.length} leaders`}
        {updatedAt !== null && ` · updated ${formatTime(new Date(updatedAt).toISOString(), now)}`}
      </p>
      {error !== null && (
        <p className="notice" role="alert">
          Could not refresh the fleet: {describeError(error).title} Showing the last figures.
        </p>
      )}
      {data.length === 0 ? (
        <p>You hold no role on any leader yet. Ask a console administrator for a grant.</p>
      ) : shown.length === 0 ? (
        <p>No leader has the label {label}.</p>
      ) : (
        <div className="table-scroll" role="region" aria-label="Leaders" tabIndex={0}>
          <table className="fleet-table">
            <thead>
              <tr>
                <th scope="col">Leader</th>
                <th scope="col">Health</th>
                <th scope="col">Queued</th>
                <th scope="col">Completed, last hour</th>
                <th scope="col">Completed, last day</th>
                <th scope="col">Failed attempts, last day</th>
                <th scope="col">Active followers by pool</th>
                <th scope="col">Oldest queued job (since created)</th>
                <th scope="col">Last scan errors</th>
                <th scope="col">Throughput, 24 hours</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((leader) => (
                <LeaderRow key={leader.name} leader={leader} now={now} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
```

Run: `npx vitest run src/pages/FleetPage.test.tsx`
Expected: PASS — 8 tests.

- [ ] **Step 7: Replace the stub app and apply the theme at start**

Delete the stub's test (the overview test and Task 6's end-to-end tests replace it):

```bash
git rm packages/console-web/src/App.test.tsx
```

Replace `packages/console-web/src/App.tsx` with:

```tsx
import { useEffect } from "react";
import { startActivityTracking } from "./app/activity";
import { FleetProvider } from "./app/fleet";
import { RouterProvider, useLocation } from "./app/router";
import { SessionProvider } from "./app/session";
import { Layout, type NavItem } from "./components/Layout";
import { FleetPage } from "./pages/FleetPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { SignInPage } from "./pages/SignInPage";

const NAV: NavItem[] = [{ to: "/", label: "Fleet", match: (pathname) => pathname === "/" }];

function SignedInPage() {
  const { pathname } = useLocation();
  if (pathname === "/") return <FleetPage />;
  return <NotFoundPage />;
}

function Routes() {
  const { pathname } = useLocation();
  if (pathname === "/sign-in") return <SignInPage />;
  return (
    <SessionProvider>
      <FleetProvider>
        <Layout nav={NAV}>
          <SignedInPage />
        </Layout>
      </FleetProvider>
    </SessionProvider>
  );
}

export function App() {
  useEffect(() => startActivityTracking(), []);
  return (
    <RouterProvider>
      <Routes />
    </RouterProvider>
  );
}
```

Replace `packages/console-web/src/main.tsx` with:

```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { applyTheme, readTheme } from "./app/theme";
import "./styles.css";

applyTheme(readTheme());

const root = document.getElementById("root");
if (root === null) throw new Error("index.html has no #root");
createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
```

- [ ] **Step 8: Run everything and build**

Run: `npm test`
Expected: PASS — 60 tests in 9 files.

Run: `npm run typecheck` then `npm run lint`
Expected: both exit 0 with no output.

Run: `npm run build`
Expected: `dist/ ok: index.html and 2 hashed assets`.

- [ ] **Step 9: Commit**

```bash
git add -A packages/console-web/src
git commit -m "Console web app: signed-in shell, sign-in page and the fleet overview

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: End-to-end harness, Playwright tests, the axe scan and CI

**Files:**
- Create: `packages/console-web/e2e/harness/fake_entra.py`, `e2e/harness/fake_leaders.py`, `e2e/harness/serve.py`
- Create: `packages/console-web/playwright.config.ts`, `e2e/tests/support.ts`
- Test: `packages/console-web/e2e/tests/sign-in.spec.ts`, `e2e/tests/overview.spec.ts`, `e2e/tests/a11y.spec.ts`
- Modify: `.github/workflows/ci.yml` (append the `web-e2e` job)
- Modify: `README.md` (the "**Web app.**" paragraph under "Leaders in the console"; the "Develop" section)

**Interfaces:**
- Consumes: `swarmscribe_console.app.create_app(settings, *, fetch, idp_transport, graph, google_groups, leader_transport)`; `swarmscribe_console.leaders.add_leader`, `grants.add_grant`, `grants.add_console_admin`; `swarmscribe_console.proxy.match_route`; `swarmscribe_console.db.models.Base`, `Leader`, `Snapshot`; `swarmscribe_console.db.migrate.upgrade`; from `packages/console/tests/console_testkit.py`: `CREDENTIAL`, `ENTRA_CLIENT`, `ENTRA_ISSUER`, `ENTRA_SECRET`, `ENTRA_TENANT`, `GROUPS`, `TEST_KEY`, `recreate`, `with_database`; the built `dist/`.
- Produces:
  - The harness: the console on `http://localhost:8900`; the control server on `http://127.0.0.1:8901` with `POST /control/reset`, `POST /control/authorize` (`{location, persona}` → `{callback}`), `POST /control/leaders/{name}/mode` (`{mode: "ok" | "down"}`), `POST /control/sessions/expire`. Leaders `eu-1` (labels `region=eu`, `env=prod`; capped at admin) and `us-1` (`region=us`, `env=prod`; capped at operator). Personas `viewer`, `operator`, `admin`.
  - `e2e/tests/support.ts`: `test` (a Playwright `test` whose automatic `guard` fixture resets the world before each test and fails it on a CSP violation or page error), `expect`, `CONTROL`, `type Persona`, `resetWorld(request)`, `setLeaderMode(request, name, mode)`, `endAllSessions(request)`, `signIn(page, persona, path?)`, `expectAccessible(page, context)`, `THEMES`.
  - The fake leaders answer every allow-listed route in the leader's own response shapes and keep state between calls; C3b's end-to-end tests rely on that: each leader starts with 6 jobs (2 queued, 1 leased, 1 completed, 1 failed with reason "the engine stopped: out of memory", 1 cancelled), 3 followers (2 active, 1 draining; the first holds the leased job), locations `intake` and `archive` (`archive` has the scan error "the root folder is not readable" on `eu-1` only), and one join token.

- [ ] **Step 1: Write the fake Entra ID**

`packages/console-web/e2e/harness/fake_entra.py`:

```python
"""Microsoft Entra ID as the console sees it, for the end-to-end harness only.

The console's authorization endpoint is the real Entra host, so the browser never reaches
it: the Playwright test stops that navigation, asks the harness's control server to "sign
in" a persona at that URL (`authorize`), and visits the callback it returns. The console
fetches discovery and keys through `fetch` and exchanges the code through `transport`, both
handed to create_app, so nothing leaves the machine."""

import base64
import hashlib
import json
import secrets
import time
import uuid
from urllib.parse import parse_qsl, urlencode

import httpx
import jwt
from console_testkit import ENTRA_CLIENT, ENTRA_ISSUER, ENTRA_SECRET, ENTRA_TENANT, GROUPS
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

JWKS_URL = f"https://login.microsoftonline.com/{ENTRA_TENANT}/discovery/v2.0/keys"
KEY_ID = "e2e-key-1"

# Personas the end-to-end tests sign in as: their Entra groups decide their console roles
# (serve.py grants viewer, operator and admin on every leader, and console administration).
PERSONAS: dict[str, list[str]] = {
    "viewer": [GROUPS["viewer"]],
    "operator": [GROUPS["operator"]],
    "admin": [GROUPS["admin"], GROUPS["console"]],
}


class UnknownPersona(Exception):
    pass


class FakeEntra:
    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.codes: dict[str, dict] = {}

    async def fetch(self, url: str) -> dict:
        if url == f"{ENTRA_ISSUER}/.well-known/openid-configuration":
            return {"issuer": ENTRA_ISSUER, "jwks_uri": JWKS_URL}
        if url == JWKS_URL:
            jwk = json.loads(RSAAlgorithm.to_jwk(self.key.public_key()))
            jwk.update(kid=KEY_ID, use="sig", alg="RS256")
            return {"keys": [jwk]}
        raise KeyError(url)

    def authorize(self, location: str, persona: str) -> str:
        """The persona signs in at `location` (the console's redirect to Entra). Returns the
        path and query of the console callback the browser is sent back to."""
        if persona not in PERSONAS:
            raise UnknownPersona(persona)
        params = dict(httpx.URL(location).params)
        code = secrets.token_urlsafe(24)
        self.codes[code] = {
            "persona": persona,
            "nonce": params["nonce"],
            "challenge": params["code_challenge"],
            "redirect_uri": params["redirect_uri"],
        }
        back = httpx.URL(params["redirect_uri"])
        return f"{back.path}?{urlencode({'code': code, 'state': params['state']})}"

    def _id_token(self, persona: str, nonce: str) -> str:
        now = int(time.time())
        claims = {
            "iss": ENTRA_ISSUER,
            "aud": ENTRA_CLIENT,
            "tid": ENTRA_TENANT,
            "sub": f"e2e-{persona}",
            "oid": str(uuid.uuid5(uuid.NAMESPACE_URL, f"e2e-{persona}")),
            "email": f"{persona}@example.org",
            "groups": PERSONAS[persona],
            "nonce": nonce,
            "iat": now,
            "nbf": now,
            "exp": now + 3600,
        }
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": KEY_ID})

    async def _token_endpoint(self, request: httpx.Request) -> httpx.Response:
        form = dict(parse_qsl((await request.aread()).decode()))
        grant = self.codes.pop(form.get("code", ""), None)
        if grant is None:
            return httpx.Response(400, json={"error": "invalid_grant"})
        verifier = form.get("code_verifier", "")
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        if (
            form.get("grant_type") != "authorization_code"
            or challenge != grant["challenge"]
            or form.get("redirect_uri") != grant["redirect_uri"]
            or form.get("client_id") != ENTRA_CLIENT
            or form.get("client_secret") != ENTRA_SECRET
        ):
            return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(
            200,
            json={
                "token_type": "Bearer",
                "expires_in": 3600,
                "id_token": self._id_token(grant["persona"], grant["nonce"]),
                "access_token": f"access-{secrets.token_urlsafe(16)}",
            },
        )

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._token_endpoint)
```

- [ ] **Step 2: Write the fake leaders**

`packages/console-web/e2e/harness/fake_leaders.py`:

```python
"""Two leaders as the console sees them over HTTPS, in memory, for the end-to-end harness.

Each leader keeps locations, jobs, followers and join tokens, and answers every route of the
console's allow-list in the shapes of swarmscribe_leader.api.admin_models, so actions change
what the next read shows. A leader applies its console credential's cap the way C1 does
(effective role = the lower of the asserted role and the cap) and then the route's role, so
an action the console allows can still meet the leader's own 403 (us-1 is capped at
operator). `modes[name] = "down"` makes a leader refuse connections."""

import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from console_testkit import CREDENTIAL
from swarmscribe_console.proxy import match_route
from swarmscribe_leader.auth.roles import at_least

CAPS = {"eu-1": "admin", "us-1": "operator"}
ROLES = ("viewer", "operator", "admin")
JOB_STATES = ("queued", "leased", "completed", "failed", "cancelled")
FOLLOWER_STATES = ("active", "draining", "revoked", "gone")
SCAN_ERROR = "the root folder is not readable"
_ADMIN_PREFIX = "/v1/admin/"


def _iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def _id(leader: str, kind: str, n: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"e2e/{leader}/{kind}/{n}"))


def _error(status: int, code: str, message: str) -> httpx.Response:
    return httpx.Response(status, json={"code": code, "message": message})


class Leader:
    def __init__(self, name: str) -> None:
        now = datetime.now(UTC)
        self.name = name
        self.locations: dict[str, dict[str, Any]] = {}
        archive_error = SCAN_ERROR if name == "eu-1" else None
        for n, (loc, error) in enumerate([("intake", None), ("archive", archive_error)]):
            self.locations[loc] = {
                "id": _id(name, "location", n),
                "name": loc,
                "backend": "local",
                "root": f"/srv/{loc}",
                "input_prefix": "",
                "output_prefix": "transcripts/",
                "pool": "default",
                "required_device": "any",
                "scan_interval_s": 900,
                "enabled": True,
                "last_scan_at": _iso(now - timedelta(minutes=2)),
                "last_scan_error": error,
                "scan_requested": False,
                "channel_mode": "mono",
                "channel_labels": ["Left", "Right"],
            }
        self.followers: dict[str, dict[str, Any]] = {}
        follower_specs = [
            ("default", "active", "cuda"),
            ("gpu", "active", "cuda"),
            ("default", "draining", "cpu"),
        ]
        for n, (pool, state, device) in enumerate(follower_specs):
            fid = _id(name, "follower", n)
            self.followers[fid] = {
                "id": fid,
                "pool": pool,
                "state": state,
                "device": device,
                "last_seen_at": _iso(now - timedelta(seconds=20)),
                "created_at": _iso(now - timedelta(days=3)),
                "leases": 0,
            }
        first_follower = next(iter(self.followers))
        self.jobs: dict[str, dict[str, Any]] = {}
        job_specs = [
            ("queued", "default", 900, None),
            ("queued", "gpu", 300, None),
            ("leased", "default", 600, None),
            ("completed", "default", 7200, None),
            ("failed", "default", 5400, "the engine stopped: out of memory"),
            ("cancelled", "default", 3600, None),
        ]
        for n, (state, pool, age, reason) in enumerate(job_specs):
            jid = _id(name, "job", n)
            completed_at = None
            if state == "completed":
                completed_at = _iso(now - timedelta(seconds=age - 60))
            self.jobs[jid] = {
                "id": jid,
                "state": state,
                "location": "intake",
                "key": f"incoming/meeting-{n + 1}.wav",
                "priority": 0,
                "attempts": 3 if state == "failed" else (1 if state != "queued" else 0),
                "max_attempts": 3,
                "pool": pool,
                "leased_by": first_follower if state == "leased" else None,
                "failure_reason": reason,
                "cancelled_by": "someone@example.org" if state == "cancelled" else None,
                "no_speech": False if state == "completed" else None,
                "created_at": _iso(now - timedelta(seconds=age)),
                "completed_at": completed_at,
            }
        self.followers[first_follower]["leases"] = 1
        self.tokens: dict[str, dict[str, Any]] = {}
        tid = _id(name, "token", 0)
        self.tokens[tid] = {
            "id": tid,
            "pool": "default",
            "expires_at": _iso(now + timedelta(days=6)),
            "max_uses": 5,
            "uses": 1,
            "revoked": False,
            "created_by": "admin@example.org",
            "created_at": _iso(now - timedelta(days=1)),
        }

    # ---- reads ----

    def status(self) -> dict[str, Any]:
        now = datetime.now(UTC)
        jobs = {state: 0 for state in JOB_STATES}
        pools: dict[str, dict[str, Any]] = {}
        oldest: int | None = None
        for job in self.jobs.values():
            jobs[job["state"]] += 1
            pool = pools.setdefault(job["pool"], {"pool": job["pool"], "queued": 0, "leased": 0})
            if job["state"] in ("queued", "leased"):
                pool[job["state"]] += 1
            if job["state"] == "queued":
                age = int((now - datetime.fromisoformat(job["created_at"])).total_seconds())
                oldest = age if oldest is None else max(oldest, age)
        followers = {state: 0 for state in FOLLOWER_STATES}
        by_pool: dict[str, dict[str, Any]] = {}
        for follower in self.followers.values():
            followers[follower["state"]] += 1
            entry = by_pool.setdefault(
                follower["pool"],
                {"pool": follower["pool"], "active": 0, "draining": 0, "revoked": 0, "gone": 0},
            )
            entry[follower["state"]] += 1
        return {
            "jobs": jobs,
            "pools": sorted(pools.values(), key=lambda p: p["pool"]),
            "followers": followers,
            "follower_pools": sorted(by_pool.values(), key=lambda p: p["pool"]),
            "completed_last_hour": 7 if self.name == "eu-1" else 3,
            "completed_last_day": 30 if self.name == "eu-1" else 12,
            "oldest_queued_age_s": oldest,
            "failed_attempts_last_day": jobs["failed"],
            "locations": [
                {
                    "name": loc["name"],
                    "backend": loc["backend"],
                    "enabled": loc["enabled"],
                    "last_scan_at": loc["last_scan_at"],
                    "last_scan_error": loc["last_scan_error"],
                    "scan_requested": loc["scan_requested"],
                    "recordings": 12 if loc["name"] == "intake" else 0,
                    "consented": 10 if loc["name"] == "intake" else 0,
                }
                for loc in self.locations.values()
            ],
        }

    def list_jobs(self, query: dict[str, str]) -> list[dict[str, Any]]:
        found = [
            job
            for job in self.jobs.values()
            if job["state"] == query.get("state", job["state"])
            and job["location"] == query.get("location", job["location"])
        ]
        found.sort(key=lambda job: job["created_at"], reverse=True)
        return found[: int(query.get("limit", "50"))]

    def consent_report(self) -> dict[str, Any]:
        completed = [job for job in self.jobs.values() if job["state"] == "completed"]
        counts = [("intake", 10, 1, 1), ("archive", 0, 0, 0)]
        return {
            "locations": [
                {
                    "name": name,
                    "consented": consented,
                    "not_consented": refused,
                    "withdrawn": withdrawn,
                    "missing": 0,
                }
                for name, consented, refused, withdrawn in counts
            ],
            "flagged": [
                {
                    "job_id": job["id"],
                    "location": job["location"],
                    "key": job["key"],
                    "completed_at": job["completed_at"],
                    "output_location": "intake",
                    "outputs": [f"transcripts/{job['key'].rsplit('/', 1)[-1]}.json"],
                }
                for job in completed[:1]
            ],
            "truncated": False,
        }

    # ---- actions ----

    def job_action(self, action: str, job_id: str, actor: str, body: Any) -> httpx.Response:
        job = self.jobs.get(job_id)
        if job is None:
            return _error(404, "not_found", "no such job")
        if action == "jobs.retry":
            if job["state"] not in ("failed", "cancelled"):
                return _error(
                    409, "not_retryable", "only failed or cancelled jobs can be retried"
                )
            job.update(state="queued", attempts=0, failure_reason=None, cancelled_by=None)
            job["created_at"] = _iso(datetime.now(UTC))
        elif job["state"] not in ("queued", "leased"):
            return _error(409, "not_open", "the job is not queued or leased")
        elif action == "jobs.cancel":
            job.update(state="cancelled", cancelled_by=actor, leased_by=None)
        else:
            job["priority"] = body["priority"]
        return httpx.Response(200, json=job)

    def follower_action(self, action: str, follower_id: str) -> httpx.Response:
        follower = self.followers.get(follower_id)
        if follower is None:
            return _error(404, "not_found", "no such follower")
        if action == "followers.drain":
            if follower["state"] == "active":
                follower["state"] = "draining"
            return httpx.Response(200, json=follower)
        released = 0
        for job in self.jobs.values():
            if job["state"] == "leased" and job["leased_by"] == follower_id:
                job.update(state="queued", leased_by=None)
                released += 1
        follower.update(state="revoked", leases=0)
        answer = {"id": follower_id, "state": "revoked", "released": released}
        return httpx.Response(200, json=answer)

    def location_action(self, action: str, name: str, body: Any) -> httpx.Response:
        if action == "locations.add":
            if body["name"] in self.locations:
                return _error(409, "exists", f"a location named {body['name']!r} already exists")
            location = {
                "id": str(uuid.uuid4()),
                "name": body["name"],
                "backend": "local",
                "root": body["root"],
                "input_prefix": body.get("input_prefix", ""),
                "output_prefix": body.get("output_prefix", "transcripts/"),
                "pool": body.get("pool", "default"),
                "required_device": body.get("required_device", "any"),
                "scan_interval_s": body.get("scan_interval_s", 900),
                "enabled": True,
                "last_scan_at": None,
                "last_scan_error": None,
                "scan_requested": False,
                "channel_mode": body.get("channel_mode", "mono"),
                "channel_labels": body.get("channel_labels", ["Left", "Right"]),
            }
            self.locations[location["name"]] = location
            return httpx.Response(201, json=location)
        location = self.locations.get(name)
        if location is None:
            return _error(404, "not_found", f"no location named {name!r}")
        if action == "locations.ingest":
            if not location["enabled"]:
                return _error(409, "disabled", f"location {name!r} is disabled; enable it first")
            location["scan_requested"] = True
            answer = {"name": name, "requested_at": _iso(datetime.now(UTC))}
            return httpx.Response(202, json=answer)
        location["enabled"] = action == "locations.enable"
        return httpx.Response(200, json=location)

    def token_action(
        self, action: str, token_id: str | None, actor: str, body: Any
    ) -> httpx.Response:
        if action == "tokens.create":
            now = datetime.now(UTC)
            tid = str(uuid.uuid4())
            lifetime = timedelta(seconds=body.get("expires_in_seconds", 7 * 86400))
            token = {
                "id": tid,
                "pool": body.get("pool", "default"),
                "expires_at": _iso(now + lifetime),
                "max_uses": body.get("max_uses", 1),
                "uses": 0,
                "revoked": False,
                "created_by": actor,
                "created_at": _iso(now),
            }
            self.tokens[tid] = token
            plaintext = "sst_" + secrets.token_urlsafe(32)
            created = {key: token[key] for key in ("id", "pool", "expires_at", "max_uses")}
            return httpx.Response(201, json={**created, "token": plaintext})
        token = self.tokens.get(token_id or "")
        if token is None:
            return _error(404, "not_found", "no such join token")
        token["revoked"] = True
        return httpx.Response(200, json=token)


class FakeLeaders:
    def __init__(self) -> None:
        self.modes: dict[str, str] = {}
        self.leaders: dict[str, Leader] = {}
        self.reset()

    def reset(self) -> None:
        self.modes = {}
        self.leaders = {name: Leader(name) for name in CAPS}

    async def handler(self, request: httpx.Request) -> httpx.Response:
        name = (request.url.host or "").split(".", 1)[0]
        leader = self.leaders.get(name)
        if leader is None or self.modes.get(name) == "down":
            raise httpx.ConnectError("connection refused", request=request)
        if request.headers.get("authorization") != f"Console {CREDENTIAL}":
            return _error(401, "unauthorized", "unknown console credential")
        asserted = request.headers.get("x-swarmscribe-actor-role", "viewer")
        cap = CAPS[name]
        role = asserted if ROLES.index(asserted) <= ROLES.index(cap) else cap
        actor_header = request.headers.get("x-swarmscribe-actor", "")
        actor = actor_header.rsplit(" ", 1)[-1]
        path = request.url.path
        if not path.startswith(_ADMIN_PREFIX):
            return _error(404, "not_found", "Not Found")
        found = match_route(request.method, path[len(_ADMIN_PREFIX) :])
        if found is None:
            return _error(404, "not_found", "Not Found")
        route, params = found
        if not at_least(role, route.role):
            return _error(403, "forbidden", f"this needs the {route.role} role")
        raw = await request.aread()
        body = json.loads(raw) if raw else {}
        query = dict(request.url.params)
        action = route.action
        if action == "status.view":
            return httpx.Response(200, json=leader.status())
        if action == "locations.view":
            return httpx.Response(200, json=list(leader.locations.values()))
        if action == "jobs.view":
            return httpx.Response(200, json=leader.list_jobs(query))
        if action == "followers.view":
            state = query.get("state")
            followers = [f for f in leader.followers.values() if state in (None, f["state"])]
            return httpx.Response(200, json=followers)
        if action == "tokens.view":
            return httpx.Response(200, json=list(leader.tokens.values()))
        if action == "consent.view":
            return httpx.Response(200, json=leader.consent_report())
        if action.startswith("jobs."):
            return leader.job_action(action, params["job_id"], actor, body)
        if action.startswith("followers."):
            return leader.follower_action(action, params["follower_id"])
        if action.startswith("locations."):
            return leader.location_action(action, params.get("location", ""), body)
        return leader.token_action(action, params.get("token_id"), actor, body)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)
```

- [ ] **Step 3: Write the harness entry point**

`packages/console-web/e2e/harness/serve.py`:

```python
"""The fleet console as the end-to-end tests meet it: the real console app (create_app) on
http://localhost:8900 serving the built web app, with an in-memory Entra ID and two
in-memory leaders in place of the network, its own Postgres database, and the real poller
(every 2 s, so "unreachable" arrives in seconds).

A separate control server on http://127.0.0.1:8901 lets the tests reset the world, sign a
persona in, take a leader down and end every session. It is a different port and a
different app: nothing here is part of the console package or the built web app.

Run from the repository root (Playwright's webServer does this):

    python -m uv run python packages/console-web/e2e/harness/serve.py

The database server is SWARMSCRIBE_TEST_DATABASE_URL (as for pytest) or, when unset, the
repository's pgserver in .pgdata."""

import asyncio
import logging
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
WEB_ROOT = REPO_ROOT / "packages" / "console-web"
# console_testkit: the console tests' shared constants (keys, credential, Entra ids, groups).
sys.path.insert(0, str(REPO_ROOT / "packages" / "console" / "tests"))

import uvicorn  # noqa: E402
from console_testkit import (  # noqa: E402
    CREDENTIAL,
    ENTRA_CLIENT,
    ENTRA_SECRET,
    ENTRA_TENANT,
    GROUPS,
    TEST_KEY,
    recreate,
    with_database,
)
from fake_entra import FakeEntra, UnknownPersona  # noqa: E402
from fake_leaders import CAPS, FakeLeaders  # noqa: E402
from sqlalchemy import select, text  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402
from starlette.routing import Route  # noqa: E402
from swarmscribe_console import grants, leaders  # noqa: E402
from swarmscribe_console.app import create_app  # noqa: E402
from swarmscribe_console.config import Settings  # noqa: E402
from swarmscribe_console.db.migrate import upgrade  # noqa: E402
from swarmscribe_console.db.models import Base, Leader, Snapshot  # noqa: E402

E2E_DATABASE = "swarmscribe_console_e2e"
CONSOLE_PORT = 8900
CONTROL_PORT = 8901
PUBLIC_URL = f"http://localhost:{CONSOLE_PORT}"
LEADERS = {
    "eu-1": {"region": "eu", "env": "prod"},
    "us-1": {"region": "us", "env": "prod"},
}
KEEP_TABLES = {"alembic_version"}
logger = logging.getLogger("e2e-harness")


def admin_database_url() -> str:
    url = os.environ.get("SWARMSCRIBE_TEST_DATABASE_URL")
    if url:
        return url
    import pgserver

    return pgserver.get_server(str(REPO_ROOT / ".pgdata"), cleanup_mode="stop").get_uri()


class NoDirectory:
    """Group lookups are never needed: every persona's groups are in its token."""

    async def member_object_ids(self, user_object_id: str) -> set[str]:
        return set()

    async def group_emails(self, email: str) -> set[str]:
        return set()


class Harness:
    def __init__(self, database_url: str) -> None:
        self.entra = FakeEntra()
        self.fakes = FakeLeaders()
        settings = Settings(
            database_url=database_url,
            public_url=PUBLIC_URL,
            key=TEST_KEY,
            entra_tenant_id=ENTRA_TENANT,
            entra_client_id=ENTRA_CLIENT,
            entra_client_secret=ENTRA_SECRET,
            static_dir=WEB_ROOT / "dist",
            poll_interval_seconds=2.0,
            poll_timeout_seconds=1.0,
            poll_tick_seconds=0.5,
        )
        self.console = create_app(
            settings,
            fetch=self.entra.fetch,
            idp_transport=self.entra.transport,
            graph=NoDirectory(),
            google_groups=NoDirectory(),
            leader_transport=self.fakes.transport,
        )

    @property
    def sessionmaker(self):
        return self.console.state.sessionmaker

    async def reset(self) -> None:
        """Empty every console table, re-register the two leaders with grants and 24 hours
        of history, restore the fake leaders, and wait for both leaders' first poll."""
        self.fakes.reset()
        tables = [
            table.name
            for table in reversed(Base.metadata.sorted_tables)
            if table.name not in KEEP_TABLES
        ]
        async with self.console.state.engine.begin() as conn:
            await conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
        keys = self.console.state.keys
        async with self.sessionmaker() as session:
            for name, labels in LEADERS.items():
                await leaders.add_leader(
                    session,
                    keys,
                    name=name,
                    base_url=f"https://{name}.leaders.example",
                    labels=labels,
                    credential=CREDENTIAL,
                    enabled=True,
                    actor="e2e",
                )
            for role in ("viewer", "operator", "admin"):
                await grants.add_grant(
                    session,
                    role=role,
                    scope="all",
                    principal_kind="entra_group",
                    principal=GROUPS[role],
                    actor="e2e",
                )
            await grants.add_console_admin(
                session, principal_kind="entra_group", principal=GROUPS["console"], actor="e2e"
            )
            await session.commit()
        await self._seed_history()
        await self._wait_for_first_polls()

    async def _seed_history(self) -> None:
        now = datetime.now(UTC)
        async with self.sessionmaker() as session:
            for row in (await session.scalars(select(Leader))).all():
                status = self.fakes.leaders[row.name].status()
                for i in range(288, 0, -1):
                    taken = now - timedelta(minutes=5 * i)
                    down = row.name == "us-1" and 100 <= i < 104
                    point = dict(status, completed_last_hour=(i % 12) + 1)
                    session.add(
                        Snapshot(
                            leader_id=row.id,
                            taken_at=taken,
                            reachable=not down,
                            outcome="connect_error" if down else "ok",
                            status=None if down else point,
                        )
                    )
            await session.commit()

    async def _wait_for_first_polls(self) -> None:
        for _ in range(100):
            async with self.sessionmaker() as session:
                rows = (await session.scalars(select(Leader))).all()
            if rows and all(row.last_success_at is not None for row in rows):
                return
            await asyncio.sleep(0.1)
        raise RuntimeError("the leaders were not polled within 10 seconds")

    async def end_sessions(self) -> None:
        async with self.console.state.engine.begin() as conn:
            await conn.execute(text("DELETE FROM sessions"))


def control_app(harness: Harness) -> Starlette:
    async def reset(_request: Request) -> JSONResponse:
        await harness.reset()
        return JSONResponse({"ok": True})

    async def authorize(request: Request) -> JSONResponse:
        body = await request.json()
        try:
            callback = harness.entra.authorize(str(body["location"]), str(body["persona"]))
        except (KeyError, UnknownPersona):
            return JSONResponse({"error": "unknown persona or location"}, status_code=400)
        return JSONResponse({"callback": callback})

    async def leader_mode(request: Request) -> JSONResponse:
        name = request.path_params["name"]
        mode = (await request.json()).get("mode")
        if name not in CAPS or mode not in ("ok", "down"):
            return JSONResponse({"error": "unknown leader or mode"}, status_code=400)
        harness.fakes.modes[name] = mode
        return JSONResponse({"ok": True})

    async def expire_sessions(_request: Request) -> JSONResponse:
        await harness.end_sessions()
        return JSONResponse({"ok": True})

    return Starlette(
        routes=[
            Route("/control/reset", reset, methods=["POST"]),
            Route("/control/authorize", authorize, methods=["POST"]),
            Route("/control/leaders/{name}/mode", leader_mode, methods=["POST"]),
            Route("/control/sessions/expire", expire_sessions, methods=["POST"]),
        ]
    )


async def main() -> None:
    if not (WEB_ROOT / "dist" / "index.html").is_file():
        raise SystemExit("build the web app first: npm run build (in packages/console-web)")
    admin_url = admin_database_url()
    await recreate(admin_url, E2E_DATABASE)
    database_url = with_database(admin_url, E2E_DATABASE)
    await asyncio.to_thread(upgrade, database_url)
    harness = Harness(database_url)
    apps = ((harness.console, CONSOLE_PORT), (control_app(harness), CONTROL_PORT))
    servers = [
        uvicorn.Server(
            uvicorn.Config(
                app, host="127.0.0.1", port=port, access_log=False, log_level="warning"
            )
        )
        for app, port in apps
    ]
    serving = [asyncio.create_task(server.serve()) for server in servers]
    while not all(server.started for server in servers):
        await asyncio.sleep(0.05)
    await harness.reset()
    print(f"e2e harness ready: console {PUBLIC_URL}, control http://127.0.0.1:{CONTROL_PORT}")
    sys.stdout.flush()
    await asyncio.gather(*serving)


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())
```

- [ ] **Step 4: Lint the harness and start it once by hand**

Run (repository root): `python -m uv run ruff check packages/console-web/e2e/harness`
Expected: `All checks passed!`

Run (in `packages/console-web`): `npm run build`, then from the repository root, in a second terminal: `python -m uv run python packages/console-web/e2e/harness/serve.py`
Expected: after a few seconds, `e2e harness ready: console http://localhost:8900, control http://127.0.0.1:8901`.

Check, while it runs:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8900/api/session
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8900/sign-in
curl -s -X POST http://127.0.0.1:8901/control/reset
```

Expected: `401`, `200`, `{"ok":true}`. Stop the harness (Ctrl+C).

- [ ] **Step 5: Write the Playwright configuration and the shared helpers**

`packages/console-web/playwright.config.ts`:

```ts
import { defineConfig, devices } from "@playwright/test";

// End-to-end tests run against the real console (e2e/harness/serve.py) serving dist/, with
// an in-memory Entra ID and two in-memory leaders. Tests share one database and reset it,
// so they run one at a time. Build first: npm run build.
const harness =
  process.env.E2E_HARNESS_COMMAND ?? "python -m uv run python packages/console-web/e2e/harness/serve.py";

export default defineConfig({
  testDir: "e2e/tests",
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    // Must be the console's public_url exactly: the CSRF check compares Origin with it.
    baseURL: "http://localhost:8900",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: harness,
    cwd: "../..",
    url: "http://localhost:8900/sign-in",
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
    stdout: "pipe",
    stderr: "pipe",
  },
});
```

`packages/console-web/e2e/tests/support.ts`:

```ts
import AxeBuilder from "@axe-core/playwright";
import { test as base, expect, type APIRequestContext, type Page } from "@playwright/test";

export { expect };

/** The harness's control server (e2e/harness/serve.py); never part of the console. */
export const CONTROL = "http://127.0.0.1:8901";
export type Persona = "viewer" | "operator" | "admin";

const ENTRA = "https://login.microsoftonline.com/";

export async function resetWorld(request: APIRequestContext): Promise<void> {
  const answer = await request.post(`${CONTROL}/control/reset`);
  expect(answer.ok()).toBe(true);
}

export async function setLeaderMode(
  request: APIRequestContext,
  name: string,
  mode: "ok" | "down",
): Promise<void> {
  const answer = await request.post(`${CONTROL}/control/leaders/${name}/mode`, { data: { mode } });
  expect(answer.ok()).toBe(true);
}

export async function endAllSessions(request: APIRequestContext): Promise<void> {
  const answer = await request.post(`${CONTROL}/control/sessions/expire`);
  expect(answer.ok()).toBe(true);
}

/**
 * The whole browser sign-in: the console's sign-in page, its redirect to Entra ID (stopped
 * here and answered by the harness for `persona`), and the callback, which refreshes to the
 * page the person asked for.
 */
export async function signIn(page: Page, persona: Persona, path = "/"): Promise<void> {
  await page.route(`${ENTRA}**`, (route) =>
    route.fulfill({ status: 200, contentType: "text/html", body: "<p>Microsoft sign-in</p>" }),
  );
  await page.goto(path);
  await expect(page).toHaveURL(/\/sign-in/);
  const toEntra = page.waitForRequest((request) => request.url().startsWith(ENTRA));
  await page.getByRole("link", { name: "Sign in with Microsoft Entra ID" }).click();
  const location = (await toEntra).url();
  const answer = await page.request.post(`${CONTROL}/control/authorize`, { data: { location, persona } });
  expect(answer.ok()).toBe(true);
  const { callback } = (await answer.json()) as { callback: string };
  await page.goto(callback);
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
}

/** Zero WCAG 2.1 A/AA violations on the page as it is now. */
export async function expectAccessible(page: Page, context: string): Promise<void> {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  const found = results.violations.map(
    (violation) =>
      `${context}: ${violation.id} at ${violation.nodes.map((node) => node.target.join(" ")).join(", ")}`,
  );
  expect(found).toEqual([]);
}

export const THEMES = ["light", "dark"] as const;

/**
 * Every test fails on a Content Security Policy violation or an uncaught page error, and
 * starts from a freshly reset console.
 */
export const test = base.extend<{ guard: undefined }>({
  guard: [
    async ({ page, request }, use) => {
      const problems: string[] = [];
      page.on("console", (message) => {
        if (message.type() === "error" && message.text().includes("Content Security Policy")) {
          problems.push(message.text());
        }
      });
      page.on("pageerror", (error) => problems.push(`page error: ${error.message}`));
      await resetWorld(request);
      await use(undefined);
      expect(problems).toEqual([]);
    },
    { auto: true },
  ],
});
```

- [ ] **Step 6: Write the end-to-end tests**

`packages/console-web/e2e/tests/sign-in.spec.ts`:

```ts
import { endAllSessions, expect, signIn, test } from "./support";

test("a person without a session is sent to sign in and comes back to the page they asked for", async ({
  page,
}) => {
  await signIn(page, "viewer", "/?label=region%3Deu");
  await expect(page).toHaveURL("/?label=region%3Deu");
  await expect(page.getByRole("heading", { name: "Fleet" })).toBeVisible();
  await expect(page.getByRole("rowheader", { name: /eu-1/ })).toBeVisible();
  await expect(page.getByRole("rowheader", { name: /us-1/ })).toHaveCount(0);
});

test("signing out ends the session", async ({ page }) => {
  await signIn(page, "viewer");
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL("/sign-in?signed_out=1");
  await expect(page.getByText("You have signed out.")).toBeVisible();
  await page.goto("/");
  await expect(page).toHaveURL(/\/sign-in/);
});

test("a session that ends on the server sends the person to sign in at the next refresh", async ({
  page,
  request,
}) => {
  await signIn(page, "viewer");
  await endAllSessions(request);
  await expect(page).toHaveURL(/\/sign-in/, { timeout: 20_000 });
  await expect(page.getByRole("heading", { name: "Sign in to the SwarmScribe console" })).toBeVisible();
});
```

`packages/console-web/e2e/tests/overview.spec.ts`:

```ts
import { expect, setLeaderMode, signIn, test } from "./support";

test("the overview shows each leader's figures and 24-hour chart", async ({ page }) => {
  await signIn(page, "viewer");
  const eu = page.getByRole("row", { name: /eu-1/ });
  await expect(eu.getByText("Reachable", { exact: true })).toBeVisible();
  // The fake eu-1: two queued jobs, 7 completed in the hour, 30 in the day, one failure.
  await expect(eu.getByRole("cell").nth(1)).toHaveText("2");
  await expect(eu.getByRole("cell").nth(2)).toHaveText("7");
  await expect(eu.getByRole("cell").nth(3)).toHaveText("30");
  await expect(eu.getByRole("cell").nth(4)).toHaveText("1");
  await expect(eu.getByRole("cell").nth(5)).toHaveText("default 1 · gpu 1");
  await expect(eu.getByRole("cell").nth(6)).toHaveText(/^1\d min$/);
  await expect(eu.getByText("archive: the root folder is not readable")).toBeVisible();
  await expect(eu.getByRole("img")).toHaveAccessibleName(/Jobs completed per hour over the last 24 hours/);
  const us = page.getByRole("row", { name: /us-1/ });
  await expect(us.getByRole("img")).toHaveAccessibleName(/Unreachable in 4 five-minute periods/);
});

test("the label filter narrows the rows", async ({ page }) => {
  await signIn(page, "viewer");
  await page.getByRole("combobox", { name: "Label" }).selectOption("region=us");
  await expect(page.getByRole("rowheader", { name: /us-1/ })).toBeVisible();
  await expect(page.getByRole("rowheader", { name: /eu-1/ })).toHaveCount(0);
  await expect(page).toHaveURL("/?label=region%3Dus");
});

test("a leader that stops answering is shown unreachable while the other stays reachable", async ({
  page,
  request,
}) => {
  await signIn(page, "viewer");
  await setLeaderMode(request, "us-1", "down");
  const us = page.getByRole("row", { name: /us-1/ });
  await expect(us.getByText("Unreachable", { exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(us.getByText(/Figures as of/)).toBeVisible();
  await expect(page.getByRole("row", { name: /eu-1/ }).getByText("Reachable", { exact: true })).toBeVisible();
});

test("the layout holds at tablet width", async ({ page }) => {
  await page.setViewportSize({ width: 768, height: 1024 });
  await signIn(page, "viewer");
  await expect(page.getByRole("rowheader", { name: /eu-1/ })).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  await expect(page.getByRole("button", { name: "Sign out" })).toBeInViewport();
  await expect(page.getByRole("region", { name: "Leaders" })).toBeVisible();
});

test("the overview works from the keyboard alone", async ({ page }) => {
  await signIn(page, "viewer");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to main content" })).toBeFocused();
  await page.keyboard.press("Enter");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("combobox", { name: "Label" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("region", { name: "Leaders" })).toBeFocused();
  await page.getByRole("combobox", { name: "Theme" }).focus();
  await page.keyboard.press("ArrowDown");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
});
```

`packages/console-web/e2e/tests/a11y.spec.ts`:

```ts
import { THEMES, expect, expectAccessible, signIn, test } from "./support";

// Every page the app has, in both themes, with zero axe violations (WCAG 2.1 A and AA).
// C3b adds the drill-down tabs and the administration pages to SIGNED_IN_PAGES.
const SIGNED_IN_PAGES: { path: string; heading: string }[] = [
  { path: "/", heading: "Fleet" },
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
  });
}
```

- [ ] **Step 7: Run the end-to-end tests**

Run (in `packages/console-web`):

```bash
npx playwright install chromium
npm run build
npm run e2e
```

Expected: Playwright starts the harness (`[WebServer] e2e harness ready: …`) and reports `12 passed`. The unreachable test and the session-end test each take about 11 seconds (they wait for the poller and for the page's own 10 s refresh).

If an axe test fails on `color-contrast`, change the colour token in `src/styles.css` (both the `prefers-color-scheme` block and the `[data-theme]` block), never a per-component override, and rerun.

Run: `npm run typecheck` then `npm run lint` then `npm test`
Expected: all pass (the unit tests still report 60).

- [ ] **Step 8: Add the `web-e2e` job to CI**

Append to `.github/workflows/ci.yml`, after the `web` job:

```yaml
  web-e2e:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_PASSWORD: postgres
        ports:
          - 5432:5432
        options: >-
          --health-cmd pg_isready
          --health-interval 5s
          --health-timeout 5s
          --health-retries 10
    env:
      SWARMSCRIBE_TEST_DATABASE_URL: postgresql://postgres:postgres@localhost:5432/postgres
      # Playwright's webServer starts the harness from the repository root.
      E2E_HARNESS_COMMAND: uv run python packages/console-web/e2e/harness/serve.py
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync
      - uses: actions/setup-node@v4
        with:
          node-version: 24
          cache: npm
          cache-dependency-path: packages/console-web/package-lock.json
      - name: Install, build and run the end-to-end tests
        working-directory: packages/console-web
        run: |
          npm ci
          npx playwright install --with-deps chromium
          npm run build
          npm run e2e
      - name: Keep the Playwright report
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: playwright-report
          path: packages/console-web/playwright-report
          retention-days: 7
```

Run (repository root): `python -m uv run python -c "import yaml; print(list(yaml.safe_load(open('.github/workflows/ci.yml'))['jobs']))"`
Expected: `['test', 'compose-e2e', 'web', 'web-e2e']`.

- [ ] **Step 9: Update the README**

In `README.md`, under "### Leaders in the console", replace the paragraph that starts `**Web app.** \`SWARMSCRIBE_CONSOLE_STATIC_DIR\` points at the built web app (C3):` (through `/api\` and \`/auth\` never do.`) with:

````markdown
**Web app.** The web app is `packages/console-web` (React and TypeScript, built
with Vite). `npm run build` there writes `packages/console-web/dist`; point
`SWARMSCRIBE_CONSOLE_STATIC_DIR` at that folder (it must hold `index.html`;
blank means none). The console serves it under its Content Security Policy
(scripts and styles from its own origin only, no inline). A path that is not a
file and has no extension gets `index.html` so the app's own routes survive a
reload; `/api` and `/auth` never do. Every built asset is named
`<name>-<16 hex characters>.<ext>` and cached for a year; `index.html` is
revalidated on every load. The overview refreshes every 10 seconds and stops
refreshing after 55 minutes without input, so the one-hour idle timeout still
applies to an open tab.
````

In `README.md`, under "## Develop", after the first code block, add:

````markdown
The web app (Node 24):

```
cd packages/console-web
npm ci
npm run typecheck && npm run lint && npm test
npm run build                      # dist/, checked by scripts/check-dist.mjs
npx playwright install chromium    # once
npm run e2e                        # starts e2e/harness/serve.py, then Playwright
```

The end-to-end harness runs the real console on `http://localhost:8900` with an
in-memory Entra ID and two in-memory leaders (`eu-1`, `us-1`), on
`SWARMSCRIBE_TEST_DATABASE_URL` or the local pgserver, and a control server on
`http://127.0.0.1:8901` that only the tests use. `E2E_HARNESS_COMMAND` replaces
the command Playwright starts it with (CI uses `uv run python …`). While working
on the app, `npm run watch` rebuilds `dist/` and `npx playwright test --ui`
drives it; the Vite dev server is not used, because the console's CSP and CSRF
checks apply only to the built app on the console's own origin.
````

- [ ] **Step 10: Run the backend's checks and commit**

Run (repository root): `python -m uv run ruff check .`
Expected: `All checks passed!`

Run: `python -m uv run pytest packages/console -q`
Expected: the same pass count as before this plan (no backend file changed; pytest does not collect anything under `packages/console-web`).

```bash
git add .github/workflows/ci.yml README.md packages/console-web
git commit -m "Console web app: end-to-end harness, Playwright tests with axe in both themes, CI job

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Self-review

**Spec coverage (C3a's share).**
- Spec 6 overview — a row per leader with reachable/unreachable (Task 5 `HealthBadge`, `FleetPage`), queue depth, completed last hour and day, failures last day, followers active by pool, oldest queued job age labelled "since created", last scan errors (Task 5 `LeaderRow`), filterable by label (Task 5 `LabelFilter`), a 24-hour throughput chart (Task 4 `Sparkline`, Task 5 `ThroughputChart`).
- Spec 6 "Accessible (WCAG 2.1 AA: keyboard, contrast, labels), responsive down to tablet width, light and dark themes" — tokens and focus rings (Task 1 `styles.css`), skip link, focus on navigation, labelled controls (Task 5 `Layout`), the 768 px and keyboard tests and the axe scan of every page in both themes (Task 6).
- Spec 1 "refreshed at least every 30 seconds" and "unreachable within one minute; nothing else stops working" — 10 s refresh (Task 5 `fleet.tsx`), the stale-row test (Task 5), the leader-down test (Task 6).
- Spec 5.2 session — CSRF header on unsafe calls (Task 2), sign-in, sign-out and 401 → sign-in (Task 3, Task 6); the idle rule (Task 3).
- Spec 7 CSP — no inline script or style and hashed assets (Task 1), a CSP guard on every end-to-end test (Task 6).
- Spec 9 web app line — component tests for the overview (Task 5), Playwright against a console and two fake leaders (Task 6), axe on every page (Task 6). The drill-down's component tests and pages are C3b.
- Handoff note — every error code has a message (Task 2 `errors.test.ts`); health values, stale `summary`, `taken_at` and the queue-age rule (Task 4, Task 5); role map against `proxy.py` (Task 2); extensionless routes outside `/api` and `/auth` (Task 3 router; routes `/`, `/sign-in`); hashed names (Task 1).
- Not in C3a, by design: the drill-down tabs, every action, the join-token dialog and the administration pages (C3b); packaging (C4).

**Placeholder scan.** No TBD or "similar to" steps; every file is given whole. Two files are written twice on purpose and shown whole both times: `src/App.tsx` and `src/main.tsx` (stub in Task 1, final in Task 5).

**Type consistency.** `PollState<T>` (Task 3) is what `useFleet()` returns (Task 5) and what the tests' `Probe` reads. `NavItem.match` (Task 5 `Layout`) is what `App.tsx` supplies. `ApiError(status, code, message, retryAfter)` is constructed the same way in the client, the tests and `describeError`. `leader()`, `history()`, `STATUS`, `SESSION`, `NOW` (Task 3 fixtures) are used with those names in Tasks 4 and 5. `mockFetch().on(route, handlerOrReply)`, `reply`, `fail`, `callsTo` are used as defined in Task 2. The harness's control routes (Task 6 `serve.py`) are the ones `support.ts` calls. `FleetLeader.health` values in `HealthBadge` are the five the backend's `health_of` returns.

**Review Focus.** Each of the seven lines names its test: stale figures (`FleetPage.test.tsx` "unreachable leader with stale figures", `overview.spec.ts`); session end (`client.test.ts` 401, `session.test.tsx`, `sign-in.spec.ts`); idle (`usePoll.test.tsx`); names in URLs (`client.test.ts` `leaderPath`, `router.test.tsx`, `FleetPage.test.tsx` `team=a=b`); clock skew (`format.test.ts`); non-JSON and network errors (`client.test.ts`, `FleetPage.test.tsx` refresh fails); CSP (`check-dist.mjs`, the `guard` fixture).

**How this plan's code was checked.** Every file in this plan was built and run in a scratch copy of the repository while the plan was written: type-check and lint clean, 60 unit and component tests passing, the build accepted by `check-dist.mjs`, and the 12 end-to-end tests passing against the real console backend with a local Postgres, including the axe scans in both themes. The file contents were then transcribed into this document; the commands in each task are the check that the transcription is faithful.
