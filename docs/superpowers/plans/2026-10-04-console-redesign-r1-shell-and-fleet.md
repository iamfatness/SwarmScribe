# Console Redesign R1: Shell, Fleet Overview and Sign-in Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the whole fleet console the SwarmScribe "Ink console" look in both themes, and rebuild its frame (a left rail, a top bar with a menu on narrow screens), the fleet overview (totals, a card per leader, "Needs a look") and the sign-in page to the approved mockups.

**Architecture:** The single stylesheet becomes seven files under `src/styles/` built on two sets of colour tokens (ink and paper) that a surface chooses between; every class the app already uses keeps its name and gets the new look, so pages this plan does not restructure are restyled without being touched. `Layout.tsx` becomes the rail, `FleetPage.tsx` becomes cards, `SignInPage.tsx` becomes two panels. No route, API call, role or security rule changes.

**Tech Stack:** React 19.3.0, TypeScript 6.0.3, Vite 8.3.2, Vitest 5.0.3 with Testing Library, Playwright 1.63.0 with @axe-core/playwright 4.13.0. No dependency is added or removed.

**Spec:** `docs/superpowers/specs/2026-10-04-console-redesign.md` (the authority; read sections 3, 4, 5.1 to 5.3, 6 and 7 before Task 1). The approved mockups are in `docs/superpowers/design/`: `InkConsole.dc.html` (fleet) and `SignIn.dc.html` are this plan's.

**Follow-on plan:** `docs/superpowers/plans/2026-10-04-console-redesign-r2-leader-and-admin.md` (the leader's pages, the dialogs' wording, Administration). After this plan those pages wear the brand but keep their old structure and words. That is a working, shippable app.

**Precondition:** the work happens on the `console-redesign` branch. All `npm` and `npx` commands run in `packages/console-web`.

**Node.** `engines` is `>=24.15 <25` and `.npmrc` sets `engine-strict=true`. On Node 24.13 `npm ci` stops with `EBADENGINE`; either upgrade Node or, for this session only, run `npm ci --engine-strict=false` (and pass the same flag to any `npm install`). Do not edit `.npmrc` or `package.json` `engines`.

Before Task 1, in `packages/console-web`: `npm ci`, then `npm test` (**291 tests in 24 files** pass), `npm run typecheck`, `npm run lint`, `npm run build`, and `npm run e2e -- --retries=0` (all pass). `npx playwright install chromium` once if Playwright has never run on this machine. If anything fails before you have changed a line, stop and report it.

**Test-count rule.** Every "N tests pass" below is the previous total plus this task's new tests, starting from 291. If your count differs, find out why before going on.

**What was checked when this plan was written, and what was not.** Each task's end state was built from this plan's own blocks and run through `tsc`, ESLint, the unit tests and the build, and its built pages were scanned with axe in both themes at 1280 and 768 pixels against canned API answers. The end-to-end specs were written against the same pages but were **not run** against the real harness. You are the first to run them. Where one fails, the page and the spec's intent decide, not the spec's exact line.

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

- No inline style: no `style` prop, no `style=""`, no `<style>`. The console's CSP is `style-src 'self'`. Colours, sizes and positions are classes and custom properties in `src/styles/`.
- No font, image, script or stylesheet from another origin; no web font at all. System font stacks only.
- No `dangerouslySetInnerHTML`. No `console.*`. No storage access outside `src/app/theme.ts`.
- `eslint.config.js`, `scripts/check-dist.mjs`, `vite.config.ts` and `.npmrc` are not edited. `npm run build` must end with `dist/ ok`.
- A colour is written only in `src/styles/tokens.css` (the logo's two constants in `src/styles/shell.css` are the exception). Brand constants: amber `#f5b83d`, ink `#14110d`, ink-2 `#1c1813`, paper `#f3ead9`.
- Every text pair is at least 4.5:1 and every mark, focus ring and control edge at least 3:1, in both colour sets. `src/styles/tokens.test.ts` measures this; do not weaken it.
- The dark theme is the designed default; the light theme follows the system setting or the Theme switch. Both must look finished.
- Behaviour does not change: routes, API calls (their paths, methods, bodies and timing), roles, polling, focus management and the dialog's rules stay as they are. The one reduction: a leader that is not answering has no chart, so its history is not fetched.
- Wording is the spec's section 6, character for character. Loading lines begin with "Loading".
- A button's accessible name contains its visible text, word for word.
- No test is deleted or weakened. A test changes only where the structure or wording it checks changed, and then it checks the same thing about the new structure.
- Zero axe violations (WCAG 2.1 A and AA) on every page and dialog in both themes. No word broken across lines in a table cell or card. No sideways page scroll at 768px.
- No dependency is added. No file outside `packages/console-web` is changed: not the backend, not the Helm chart, not the docs.
- Commit after each task. Do not push. End every commit message with the trailer shown in the task's Commit step.

## Review Focus

Inputs and conditions the spec implies and a person will meet, with the test that pins each:

1. **A leader name of 100 characters with nothing to break on, and a label value of 200** → the card, the rail, the pills and the leader's page wrap the text; the page never scrolls sideways at 1280, 768 or 390 pixels. — Task 3, `e2e/tests/overview.spec.ts` ("the longest leader name and a very long label never push the page sideways"); the styles that make it true are in Task 1 (`controls.css`, `surfaces.css`, `fleet.css`).
2. **A theme chosen against the system's setting** (Dark on a light system, Light on a dark one), and a reload → the page takes the chosen set, a sheet takes the opposite one, the rail stays ink, and the choice survives the reload. — Task 2, `e2e/tests/theme.spec.ts`.
3. **The fleet cannot be loaded at all** → the rail still offers the brand, Theme and Sign out, lists no leaders and claims no roles; the error with its retry is in the main region. — Task 2, `src/shell.test.tsx` ("still offers the brand, the theme and sign-out when the fleet cannot be loaded").
4. **A leader with nothing waiting and no followers, and a leader whose health the console does not know** → "No followers at work · nothing waiting"; the unknown health is shown as the leader's own word, with no chart, no invented figures and no history request. — Task 3, `src/pages/FleetPage.test.tsx`.
5. **The list of ways to sign in cannot be loaded** → the error is in the sign-in card, the card still says "Sign in", and no provider button or loading line is left behind. — Task 4, `src/pages/SignInPage.test.tsx` ("says so in the card when the ways to sign in cannot be loaded").

## Decisions this plan makes

The spec's section 9 lists them with reasons (M1 to M20). The ones an implementer will trip over:

- **Class names are kept.** `.button`, `.table-scroll`, `.dialog`, `.tab-link`, `.field`, `.notice`, `.error-panel` and the rest keep their names and are restyled. Do not rename a class to make it "cleaner": R2's pages still use them.
- **`.sheet` is the opposite colour set**, not "paper". On the dark page it is paper; on the light page it is ink. Never give a sheet a literal colour.
- **The fleet overview has no table.** Do not add one "for accessibility": the spec's section 7.11 says what makes the cards accessible, and the tests check it.
- **"Needs a look" is the first thing in the grid.**
- **The sign-in page's `h1` is "Sign in"**, inside the card. The large serif line is a paragraph.
- **The sign-in tests move** from `src/shell.test.tsx` to `src/pages/SignInPage.test.tsx` in Task 2, unchanged, and are rewritten for the new page in Task 4.

## File Structure

| File | Responsibility |
| --- | --- |
| `src/styles.css` | Only the ordered list of `@import`s. |
| `src/styles/tokens.css` | Every token: the two colour sets, type, space, shape, shadow. |
| `src/styles/base.css` | Page defaults, headings, links, focus rings, helpers, the skip link. |
| `src/styles/controls.css` | Buttons, pills, fields, form layout. |
| `src/styles/surfaces.css` | Panels, tables (and the pinned Actions column), sheets, dialogs, callouts, status marks and pills, notices, error panels, labels. |
| `src/styles/shell.css` | The app grid, the rail, the top bar, page header, breadcrumb, tabs, the logo's colours. |
| `src/styles/fleet.css` | Totals, the card grid, "Needs a look", leader cards, the chart. |
| `src/styles/signin.css` | The sign-in page. |
| `src/styles/tokens.test.ts` | Measures the tokens' contrast and checks the two copies of each set agree. |
| `src/components/Brand.tsx` | The mark (inline SVG) and the wordmark. |
| `src/assets/favicon.svg` | The mark, as the page's icon (emitted as a hashed asset). |
| `src/components/Layout.tsx` | The frame: rail, menu, main region, focus after navigation. |
| `src/components/HealthBadge.tsx` | A leader's health as words, a tone and a mark. |
| `src/components/Sparkline.tsx` | The 24-hour chart and its text alternative. |
| `src/lib/format.ts` | Adds `formatTries` and `countOf`. |
| `src/pages/FleetPage.tsx` | The overview: filter, totals, "Needs a look", cards. |
| `src/pages/SignInPage.tsx` | The sign-in page. |
| `e2e/screens/capture.spec.ts`, `playwright.screens.config.ts` | `npm run screens`: screenshots for a person to look at. |


---

### Task 1: Tokens and the restyled base

**Files:**
- Modify: `packages/console-web/src/styles.css`
- Create: `packages/console-web/src/styles/tokens.css`
- Create: `packages/console-web/src/styles/base.css`
- Create: `packages/console-web/src/styles/controls.css`
- Create: `packages/console-web/src/styles/surfaces.css`
- Create: `packages/console-web/src/styles/shell.css`
- Create: `packages/console-web/src/styles/fleet.css`
- Create: `packages/console-web/src/styles/signin.css`
- Create: `packages/console-web/src/components/Brand.tsx`
- Create: `packages/console-web/src/assets/favicon.svg`
- Modify: `packages/console-web/index.html`
- Modify: `packages/console-web/src/components/Dialog.tsx`
- Test: `packages/console-web/src/styles/tokens.test.ts` (new)

**Interfaces:**
- Consumes: nothing from other tasks. `src/app/theme.ts` (unchanged) sets `data-theme="light|dark"` on `<html>` or leaves it off.
- Produces: the tokens of spec section 3 as CSS custom properties (`--ground`, `--panel`, `--panel-2`, `--text`, `--muted`, `--line`, `--edge`, `--accent`, `--accent-text`, `--accent-hover`, `--accent-wash`, `--primary-bg`, `--primary-fg`, `--bad`, `--bad-wash`, `--field-bg`, `--mark-quiet`, `--focus`, `--amber`, and the type, space and shape tokens); the surface classes `.on-ink` (always the ink set) and `.sheet` (the opposite set), `.sheet-lifted` (the amber offset shadow); every component class later tasks use, already styled: `.rail`, `.rail-top`, `.rail-panel[data-open]`, `.rail-foot`, `.menu-button`, `.brand`, `.brand-mark`, `.wordmark`, `.nav-list`, `.nav-sublist`, `.nav-gap`, `.nav-link`, `.nav-flag`, `.nav-flag-quiet`, `.who`, `.who-name`, `.who-line`, `.app`, `.app-main`, `.idle-notice`, `.page-head`, `.page-title`, `.page-sub`, `.pill-group`, `.pill`, `.pill-count`, `.mark`, `.mark-hex`, `.mark-quiet`, `.mark-ring`, `.mark-bad`, `.status-pill`, `.status-pill-ok|warn|bad|muted`, `.stat-row`, `.stat-tile`, `.stat-note`, `.fleet-head`, `.fleet-grid`, `.needs-look`, `.needs-look-list`, `.sheet-title`, `.leader-card`, `.leader-card-ok|warn|bad|muted`, `.leader-card-head`, `.leader-card-title`, `.leader-card-name`, `.leader-card-chart`, `.figure-row`, `.leader-card-foot`, `.leader-card-what`, `.leader-card-more`, `.leader-card-link`, `.sparkline`, `.sparkline-axis`, `.sparkline-line`, `.sparkline-dot`, `.sparkline-down`, `.signin`, `.signin-brand`, `.signin-logo`, `.signin-pitch`, `.signin-lede`, `.signin-host`, `.signin-side`, `.signin-card`, `.signin-title`, `.signin-note`, `.provider-list`, `.button-wide`, `.long`, `.callout`; `BrandMark({ size?: number })` and `Wordmark()` from `src/components/Brand.tsx`; `<dialog class="dialog sheet sheet-lifted">` from `Dialog.tsx`.

This task replaces the stylesheet and nothing else of substance. Every class the app
already uses is restyled, so after it the whole app is in the new colours and type with its
old structure: an unstyled-looking header until Task 2, a plain fleet table until Task 3, a
plain sign-in page until Task 4. That is expected. Everything still works and every existing
test still passes unchanged.

The stylesheet is written whole here, including the classes that Tasks 2 to 4 start using,
so those tasks do not touch CSS.

Why two colour sets and how the selectors choose between them is explained at the top of
`tokens.css` and in the spec's section 3.1. Read both before typing: the order and the
specificity of those selector lists are what make "a sheet is the opposite of the page"
true in all four combinations of system setting and Theme choice.

- [ ] **Step 1: Write the failing test for the tokens**

The test reads `src/styles/tokens.css` as text, finds each block marked `/* token-set: ink */`
or `/* token-set: paper */`, and measures contrast with the WCAG formula. It is the contract
the stylesheet is written against.

<!-- file: src/styles/tokens.test.ts | create -->
Create `packages/console-web/src/styles/tokens.test.ts`:

````ts
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

// The colour tokens are the contrast contract (spec section 3 and 7): this test measures
// them, so a token cannot drift below WCAG AA without a failing test. npm runs vitest from
// packages/console-web.
const CSS = readFileSync(join(process.cwd(), "src", "styles", "tokens.css"), "utf8");

type Tokens = Record<string, string>;

/** Every block that follows a "token-set: <name>" comment, as { token: value }. */
function sets(name: string): Tokens[] {
  const found: Tokens[] = [];
  const marker = new RegExp(`/\\* token-set: ${name} \\*/[^{]*\\{([^}]*)\\}`, "g");
  for (const match of CSS.matchAll(marker)) {
    const tokens: Tokens = {};
    for (const line of (match[1] ?? "").matchAll(/(--[a-z0-9-]+|color-scheme):\s*([^;]+);/g)) {
      tokens[line[1] as string] = (line[2] as string).trim();
    }
    found.push(tokens);
  }
  return found;
}

function luminance(hex: string): number {
  const value = /^#([0-9a-f]{6})$/i.exec(hex)?.[1];
  if (value === undefined) throw new Error(`${hex} is not a six-digit hex colour`);
  const [r, g, b] = [0, 2, 4].map((at) => {
    const channel = parseInt(value.slice(at, at + 2), 16) / 255;
    return channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
  }) as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string): number {
  const [light, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (light + 0.05) / (dark + 0.05);
}

// [foreground, background]: body-size text, so 4.5:1.
const TEXT_PAIRS: [string, string][] = [
  ["--text", "--ground"],
  ["--text", "--panel"],
  ["--text", "--panel-2"],
  ["--text", "--accent-wash"],
  ["--text", "--bad-wash"],
  ["--text", "--field-bg"],
  ["--muted", "--ground"],
  ["--muted", "--panel"],
  ["--muted", "--panel-2"],
  ["--muted", "--accent-wash"],
  ["--accent-text", "--ground"],
  ["--accent-text", "--panel"],
  ["--accent-text", "--panel-2"],
  ["--accent-hover", "--ground"],
  ["--accent-hover", "--panel"],
  ["--bad", "--ground"],
  ["--bad", "--panel"],
  ["--bad", "--panel-2"],
  ["--bad", "--bad-wash"],
  ["--primary-fg", "--primary-bg"],
];

// Marks, chart lines, focus rings and the edges that make a field or a switched-off button
// findable: 3:1 (WCAG 1.4.11).
const MARK_PAIRS: [string, string][] = [
  ["--accent", "--ground"],
  ["--accent", "--panel"],
  ["--accent", "--accent-wash"],
  ["--focus", "--ground"],
  ["--focus", "--panel"],
  ["--edge", "--ground"],
  ["--edge", "--panel"],
  ["--edge", "--field-bg"],
  ["--bad", "--panel"],
  ["--primary-bg", "--ground"],
  ["--primary-bg", "--panel"],
];

describe("design tokens", () => {
  it.each(["ink", "paper"])("writes the %s set the same way everywhere it appears", (name) => {
    const copies = sets(name);
    expect(copies).toHaveLength(2);
    expect(copies[1]).toEqual(copies[0]);
    expect(Object.keys(copies[0] ?? {}).length).toBeGreaterThan(15);
  });

  it("gives both sets the same token names", () => {
    const [ink] = sets("ink");
    const [paper] = sets("paper");
    expect(Object.keys(paper ?? {}).sort()).toEqual(Object.keys(ink ?? {}).sort());
  });

  it("makes ink dark and paper light", () => {
    expect(sets("ink")[0]?.["color-scheme"]).toBe("dark");
    expect(sets("paper")[0]?.["color-scheme"]).toBe("light");
  });

  for (const name of ["ink", "paper"]) {
    const tokens = sets(name)[0] ?? {};
    it.each(TEXT_PAIRS)(`${name}: %s on %s is at least 4.5:1`, (fg, bg) => {
      expect(contrast(tokens[fg] as string, tokens[bg] as string)).toBeGreaterThanOrEqual(4.5);
    });
    it.each(MARK_PAIRS)(`${name}: %s against %s is at least 3:1`, (fg, bg) => {
      expect(contrast(tokens[fg] as string, tokens[bg] as string)).toBeGreaterThanOrEqual(3);
    });
  }

  it("keeps the brand constants", () => {
    expect(CSS).toMatch(/--amber:\s*#f5b83d;/);
    const [ink] = sets("ink");
    const [paper] = sets("paper");
    expect(ink?.["--ground"]).toBe("#14110d");
    expect(ink?.["--text"]).toBe("#f3ead9");
    expect(paper?.["--ground"]).toBe("#f3ead9");
    // The skip link and the logo are amber with ink text in both themes.
    expect(contrast("#14110d", "#f5b83d")).toBeGreaterThanOrEqual(4.5);
  });

  it("loads no font and no image from anywhere", () => {
    expect(CSS).not.toMatch(/url\(|@font-face|@import/);
  });
});
````

- [ ] **Step 2: Run the tests to see them fail**

Run: `npx vitest run src/styles/tokens.test.ts`

Expected: FAIL. The file does not exist yet:
`Error: ENOENT: no such file or directory, open '…/src/styles/tokens.css'`, and Vitest reports
`Test Files  1 failed (1)`, `Tests  no tests`.

- [ ] **Step 3: Write the stylesheet, the brand component, the favicon and the dialog's surface class**

Create the seven CSS files exactly as given. Do not "tidy" a value into a token or a token
into a value: the few literal pixel values are the mockups' own. `src/styles.css` shrinks to
the list of imports; Vite inlines them into one hashed file.

`index.html` gets the brand's icon (Vite emits it as `assets/favicon-<hash>.svg`, which
`check-dist.mjs` accepts) and a theme colour. `Dialog.tsx` changes by one line.

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
````

<!-- file: src/styles/tokens.css | create -->
Create `packages/console-web/src/styles/tokens.css`:

````css
/* SwarmScribe console: design tokens.
   Spec: docs/superpowers/specs/2026-10-04-console-redesign.md, section 3.

   There are two colour sets. "Ink" is the dark ground of the brand (honey amber on warm
   ink); "paper" is its light counterpart. A surface picks a set and everything inside it
   reads the same token names:

     dark theme (the designed default)   page = ink,   .sheet = paper
     light theme                         page = paper, .sheet = ink
     .on-ink (the rail, the sign-in brand panel)        always ink

   Each set is written twice (once for the plain selector, once inside the
   prefers-color-scheme block); src/styles/tokens.test.ts fails when the copies differ and
   when a text pair drops below 4.5:1 or a control edge, mark or focus ring below 3:1.
   Washes are solid colours, not alpha, so that test can measure them. */

:root {
  /* Not themed: brand constants, type, space, shape. */
  --amber: #f5b83d;
  --serif: "Iowan Old Style", "Palatino Linotype", Palatino, "Book Antiqua", Georgia, serif;
  --sans: "Segoe UI", system-ui, -apple-system, "Helvetica Neue", Arial, sans-serif;
  --mono: ui-monospace, "Cascadia Mono", Consolas, Menlo, monospace;
  --wordmark-serif: Georgia, "Times New Roman", serif;

  --fs-display: clamp(2rem, 4.2vw, 3rem);
  --fs-h1: 2.125rem;
  --fs-sheet-title: 1.75rem;
  --fs-callout-title: 1.5rem;
  --fs-h2: 1.25rem;
  --fs-h3: 1.125rem;
  --fs-body: 0.9375rem;
  --fs-small: 0.875rem;
  --fs-fine: 0.8125rem;
  --fs-mono-fine: 0.78125rem;
  --fs-figure: 1.875rem;
  --fs-figure-small: 1.125rem;

  --s-1: 4px;
  --s-2: 8px;
  --s-3: 12px;
  --s-4: 14px;
  --s-5: 16px;
  --s-6: 20px;
  --s-7: 24px;
  --s-8: 28px;
  --s-9: 36px;
  --s-10: 56px;

  --r-sm: 4px;
  --r: 6px;
  --r-lg: 8px;
  --r-pill: 999px;

  --h-control: 44px;
  --h-row: 36px;
  --rail-width: 248px;

  --shadow-offset: 8px 8px 0 var(--amber);
  --shadow-drop: 0 30px 60px -30px rgb(0 0 0 / 0.8);
}

/* token-set: ink */
:root,
.on-ink,
:root[data-theme="light"] .sheet {
  color-scheme: dark;
  --ground: #14110d;
  --panel: #1c1813;
  --panel-2: #26211a;
  --text: #f3ead9;
  --muted: #b5a993;
  --line: rgb(243 234 217 / 0.14);
  --edge: #7d7566;
  --accent: #f5b83d;
  --accent-text: #f5b83d;
  --accent-hover: #ffd27a;
  --accent-wash: #3a2e19;
  --primary-bg: #f5b83d;
  --primary-fg: #14110d;
  --bad: #ff9d8f;
  --bad-wash: #372822;
  --field-bg: #100d0a;
  --mark-quiet: #5c5243;
  --focus: #f5b83d;
}

/* token-set: paper */
.sheet,
:root[data-theme="light"] {
  color-scheme: light;
  --ground: #f3ead9;
  --panel: #fffaf0;
  --panel-2: #e7dcc6;
  --text: #1c1712;
  --muted: #5c5243;
  --line: rgb(28 23 18 / 0.16);
  --edge: #5c5243;
  --accent: #855400;
  --accent-text: #6b4300;
  --accent-hover: #3f2800;
  --accent-wash: #e6d8bf;
  --primary-bg: #14110d;
  --primary-fg: #f3ead9;
  --bad: #8a1c14;
  --bad-wash: #ebdac9;
  --field-bg: #fffaf0;
  --mark-quiet: #b5a993;
  --focus: #855400;
}

@media (prefers-color-scheme: light) {
  /* token-set: paper */
  :root:not([data-theme="dark"]) {
    color-scheme: light;
    --ground: #f3ead9;
    --panel: #fffaf0;
    --panel-2: #e7dcc6;
    --text: #1c1712;
    --muted: #5c5243;
    --line: rgb(28 23 18 / 0.16);
    --edge: #5c5243;
    --accent: #855400;
    --accent-text: #6b4300;
    --accent-hover: #3f2800;
    --accent-wash: #e6d8bf;
    --primary-bg: #14110d;
    --primary-fg: #f3ead9;
    --bad: #8a1c14;
    --bad-wash: #ebdac9;
    --field-bg: #fffaf0;
    --mark-quiet: #b5a993;
    --focus: #855400;
  }

  /* token-set: ink */
  :root:not([data-theme="dark"]) .on-ink,
  :root:not([data-theme="dark"]) .sheet {
    color-scheme: dark;
    --ground: #14110d;
    --panel: #1c1813;
    --panel-2: #26211a;
    --text: #f3ead9;
    --muted: #b5a993;
    --line: rgb(243 234 217 / 0.14);
    --edge: #7d7566;
    --accent: #f5b83d;
    --accent-text: #f5b83d;
    --accent-hover: #ffd27a;
    --accent-wash: #3a2e19;
    --primary-bg: #f5b83d;
    --primary-fg: #14110d;
    --bad: #ff9d8f;
    --bad-wash: #372822;
    --field-bg: #100d0a;
    --mark-quiet: #5c5243;
    --focus: #f5b83d;
  }
}
````

<!-- file: src/styles/base.css | create -->
Create `packages/console-web/src/styles/base.css`:

````css
/* Page defaults, type and the helpers every screen uses. No inline styles anywhere: the
   console's CSP is style-src 'self'. */

*,
*::before,
*::after {
  box-sizing: border-box;
}

html {
  background: var(--ground);
  color: var(--text);
  font-family: var(--sans);
  font-size: 100%;
  line-height: 1.5;
  -webkit-text-size-adjust: 100%;
}

body {
  margin: 0;
  min-width: 320px;
  font-size: var(--fs-body);
}

/* A surface that picks a colour set (tokens.css) also takes that set's text colour. */
.on-ink,
.sheet {
  color: var(--text);
}

h1 {
  margin: 0;
  font-family: var(--serif);
  font-size: var(--fs-h1);
  font-weight: 400;
  line-height: 1.1;
  letter-spacing: -0.015em;
  overflow-wrap: anywhere;
}

h2 {
  margin: var(--s-7) 0 var(--s-2);
  font-size: var(--fs-h3);
  font-weight: 600;
  line-height: 1.3;
}

h3 {
  margin: var(--s-7) 0 var(--s-2);
  font-size: var(--fs-body);
  font-weight: 600;
}

p {
  margin: 0 0 var(--s-3);
}

a {
  color: var(--accent-text);
  text-underline-offset: 3px;
  text-decoration-thickness: 1px;
}

a:hover {
  color: var(--accent-hover);
}

:focus-visible {
  outline: 3px solid var(--focus);
  outline-offset: 2px;
  border-radius: 3px;
}

main:focus,
h1:focus {
  outline: none;
}

/* A heading that took focus after a navigation shows it, without the full ring. */
h1:focus-visible {
  text-decoration: underline;
  text-decoration-color: var(--accent);
  text-decoration-thickness: 2px;
  text-underline-offset: 6px;
}

code,
.mono {
  font-family: var(--mono);
  font-size: 0.9em;
}

code {
  color: var(--accent-text);
}

strong {
  font-weight: 600;
}

.muted {
  color: var(--muted);
}

/* Text that may break anywhere: a path, an address, an id, a leader's own error text. */
.long {
  overflow-wrap: anywhere;
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

.skip-link {
  position: absolute;
  left: var(--s-5);
  top: -60px;
  z-index: 20;
  padding: var(--s-2) var(--s-4);
  border-radius: var(--r);
  background: var(--amber);
  color: #14110d;
  font-weight: 600;
}

.skip-link:hover {
  color: #14110d;
}

.skip-link:focus {
  top: var(--s-3);
}

@media (prefers-reduced-motion: reduce) {
  * {
    scroll-behavior: auto;
    transition: none;
  }
}
````

<!-- file: src/styles/controls.css | create -->
Create `packages/console-web/src/styles/controls.css`:

````css
/* Buttons, pills and form fields. A control is 44px tall; a button inside a table row is
   36px. A switched-off control keeps its place and says why beside it (ActionButton). */

.button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: var(--h-control);
  padding: 0 var(--s-5);
  border: 1px solid var(--edge);
  border-radius: var(--r);
  background: transparent;
  color: var(--text);
  font: inherit;
  text-decoration: none;
  white-space: nowrap;
  cursor: pointer;
}

.button:hover:not(:disabled):not([aria-disabled="true"]) {
  background: var(--panel-2);
  color: var(--text);
}

.button-primary {
  border-color: var(--primary-bg);
  background: var(--primary-bg);
  color: var(--primary-fg);
  font-weight: 600;
}

/* As specific as the .button hover above, which would otherwise repaint a primary button
   with the ghost hover colour under its own text. */
.button-primary:hover:not(:disabled):not([aria-disabled="true"]) {
  background: var(--primary-bg);
  color: var(--primary-fg);
  text-decoration: underline;
}

.button-danger {
  border-color: var(--bad);
  color: var(--bad);
}

.button-danger:hover:not(:disabled):not([aria-disabled="true"]) {
  background: var(--bad-wash);
  color: var(--bad);
}

/* Switched off: dashed edge and muted text, never colour alone. */
.button:disabled,
.button[aria-disabled="true"] {
  border-style: dashed;
  border-color: var(--edge);
  background: transparent;
  color: var(--muted);
  font-weight: 400;
  cursor: not-allowed;
}

.button-wide {
  width: 100%;
}

/* Filter pills: toggle buttons (aria-pressed) in a labelled group. */
.pill-group {
  display: flex;
  flex-wrap: wrap;
  gap: var(--s-2);
  min-width: 0;
  max-width: 100%;
}

.pill {
  display: inline-flex;
  align-items: center;
  gap: var(--s-2);
  min-height: var(--h-control);
  padding: 0 var(--s-4);
  border: 1px solid var(--edge);
  border-radius: var(--r-pill);
  background: transparent;
  color: var(--text);
  font: inherit;
  text-align: left;
  /* One line, unless a label is longer than the page is wide: then it breaks, not the page. */
  min-width: 0;
  max-width: 100%;
  overflow-wrap: anywhere;
  cursor: pointer;
}

.pill:hover {
  background: var(--panel-2);
}

.pill[aria-pressed="true"] {
  border-color: var(--primary-bg);
  background: var(--primary-bg);
  color: var(--primary-fg);
  font-weight: 600;
}

.pill-count {
  font-family: var(--mono);
  font-size: var(--fs-fine);
}

select,
input,
textarea {
  min-height: var(--h-control);
  padding: 0 var(--s-3);
  border: 1px solid var(--edge);
  border-radius: var(--r);
  background: var(--field-bg);
  color: var(--text);
  font: inherit;
}

textarea {
  padding: var(--s-2) var(--s-3);
  line-height: 1.5;
}

input[type="checkbox"] {
  width: 20px;
  height: 20px;
  min-height: 0;
  padding: 0;
  accent-color: var(--accent);
}

input[aria-invalid="true"],
textarea[aria-invalid="true"] {
  border-color: var(--bad);
  border-width: 2px;
}

.field {
  display: grid;
  gap: 6px;
  font-size: var(--fs-small);
  font-weight: 600;
}

.field > input,
.field > select,
.field > textarea {
  font-size: var(--fs-body);
  font-weight: 400;
}

.field-inline,
.theme-select {
  display: inline-flex;
  align-items: center;
  gap: var(--s-2);
  font-size: var(--fs-small);
  color: var(--muted);
}

.field-inline > select,
.theme-select > select {
  color: var(--text);
}

.field-check {
  display: flex;
  align-items: center;
  gap: var(--s-2);
}

.field-help {
  margin: 0;
  font-size: var(--fs-fine);
  font-weight: 400;
  color: var(--muted);
}

.error-text {
  margin: 6px 0 0;
  font-size: var(--fs-small);
  color: var(--bad);
}

.form-grid {
  display: grid;
  gap: var(--s-4);
}

.form-panel {
  max-width: 36rem;
  margin-top: var(--s-8);
  padding: var(--s-6) var(--s-7);
  border: 1px solid var(--line);
  border-radius: var(--r-lg);
  background: var(--panel);
}

.form-panel h2 {
  margin-top: 0;
}
````

<!-- file: src/styles/surfaces.css | create -->
Create `packages/console-web/src/styles/surfaces.css`:

````css
/* Panels and tables (on the page's own colour set), sheets (the opposite set: paper on ink,
   ink on paper), status marks and the lines that report what happened. */

/* ---- Panels and tables ---- */

.panel {
  padding: var(--s-6) 22px;
  border: 1px solid var(--line);
  border-radius: var(--r-lg);
  background: var(--panel);
}

.table-scroll {
  max-width: 100%;
  overflow-x: auto;
  border: 1px solid var(--line);
  border-radius: var(--r-lg);
  background: var(--panel);
}

table {
  width: 100%;
  border-collapse: collapse;
}

th,
td {
  padding: var(--s-4) var(--s-3);
  text-align: left;
  vertical-align: top;
  /* Cells wrap at word boundaries only, so a narrow screen scrolls the table sideways
     instead of crushing its columns. A column of long unbroken text opts in with .long. */
  overflow-wrap: normal;
  word-break: normal;
}

th:first-child,
td:first-child {
  padding-left: var(--s-5);
}

th:last-child,
td:last-child {
  padding-right: var(--s-5);
}

thead th {
  padding-top: 10px;
  padding-bottom: 10px;
  font-size: var(--fs-mono-fine);
  font-weight: 600;
  color: var(--muted);
  white-space: nowrap;
}

tbody tr {
  border-top: 1px solid var(--line);
}

tbody th {
  font-weight: 400;
}

/* A row's name (a leader, a location, an id) stays on one line: a hyphen in "eu-1" is not
   a place to wrap. A row header of long unbroken text opts out with .long. */
tbody th:not(.long) {
  white-space: nowrap;
}

td.num {
  font-family: var(--mono);
  font-size: var(--fs-fine);
  font-variant-numeric: tabular-nums;
  text-align: right;
}

.nowrap {
  white-space: nowrap;
}

td.long,
th.long,
.cell-note {
  overflow-wrap: anywhere;
}

td.long,
th.long {
  min-width: 12rem;
}

table.medium {
  min-width: 44rem;
}

table.wide {
  min-width: 54rem;
}

.cell-note,
.cell-list {
  max-width: 28rem;
}

.cell-note {
  display: block;
  font-size: var(--fs-fine);
  color: var(--muted);
}

.cell-note.error-text {
  margin: 0;
  color: var(--bad);
}

.cell-list {
  margin: 0;
  padding-left: var(--s-5);
  font-size: var(--fs-small);
}

/* The Actions column stays in reach while a wide table scrolls sideways: it is pinned to
   the right edge of the scrolling region, over the columns that slide beneath it. */
td.actions,
table:has(td.actions) thead th:last-child {
  position: sticky;
  right: 0;
  background: var(--panel);
  box-shadow: -1px 0 0 var(--line);
}

td.actions {
  padding-top: 10px;
  padding-bottom: 10px;
  white-space: nowrap;
}

td.actions .button {
  min-height: var(--h-row);
  padding: 0 var(--s-3);
}

td.actions > .button,
td.actions > .action {
  margin-right: 6px;
}

td.actions > :last-child {
  margin-right: 0;
}

.action {
  display: inline-flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
}

.needs-role {
  font-size: var(--fs-mono-fine);
  color: var(--muted);
}

/* ---- Sheets: dialogs, callouts, the sign-in card ---- */

.sheet {
  border-radius: var(--r);
  background: var(--ground);
}

.sheet-lifted {
  box-shadow: var(--shadow-drop), var(--shadow-offset);
}

.callout {
  max-width: 720px;
  margin-top: 26px;
  padding: var(--s-6) var(--s-7);
}

.callout h2,
.sheet-title {
  margin: 0 0 var(--s-2);
  font-family: var(--serif);
  font-size: var(--fs-callout-title);
  font-weight: 400;
  line-height: 1.15;
}

.callout ol,
.callout ul {
  margin: 0;
  padding-left: var(--s-6);
}

.callout li {
  padding: var(--s-1) 0;
}

.dialog {
  width: min(560px, calc(100vw - 48px));
  max-height: calc(100vh - 48px);
  margin: auto;
  padding: var(--s-8) 30px 26px;
  overflow-y: auto;
  border: 0;
  overscroll-behavior: contain;
  overflow-wrap: anywhere;
}

/* The offset shadow is drawn outside the box: leave it room at the dialog's edge. */
.dialog.sheet-lifted {
  max-width: calc(100vw - 48px);
}

html:has(dialog[open]) {
  overflow: hidden;
}

.dialog::backdrop {
  background: rgb(10 8 6 / 0.65);
}

.dialog-title {
  margin: 0 0 var(--s-3);
  font-family: var(--serif);
  font-size: var(--fs-sheet-title);
  font-weight: 400;
  line-height: 1.15;
  letter-spacing: -0.01em;
}

.dialog-buttons {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: var(--s-2);
  margin-top: var(--s-7);
}

/* ---- Status marks: a hexagon for "fine", a ring for "not". Shape, then words. ---- */

.mark {
  display: inline-block;
  flex: none;
}

.mark-hex {
  width: 9px;
  height: 10px;
  background: var(--accent);
  clip-path: polygon(50% 0, 100% 25%, 100% 75%, 50% 100%, 0 75%, 0 25%);
}

.mark-hex.mark-quiet {
  background: var(--mark-quiet);
}

.mark-ring {
  width: 11px;
  height: 11px;
  border: 2px solid var(--muted);
  border-radius: 50%;
}

.mark-ring.mark-bad {
  border-color: var(--bad);
}

/* Windows high contrast drops backgrounds: give the hexagon an outline it keeps. */
@media (forced-colors: active) {
  .mark-hex {
    clip-path: none;
    border: 2px solid CanvasText;
    background: CanvasText;
  }
}

.status-pill,
.badge {
  display: inline-flex;
  align-items: center;
  gap: var(--s-2);
  padding: var(--s-1) 10px;
  border: 1px solid var(--edge);
  border-radius: var(--r-pill);
  font-size: var(--fs-fine);
  font-weight: 600;
  white-space: nowrap;
}

.status-pill-ok,
.badge-ok {
  border-color: var(--accent);
}

.status-pill-warn,
.badge-warn {
  border-color: var(--accent);
  border-style: dashed;
}

.status-pill-bad,
.badge-bad {
  border-color: var(--bad);
  color: var(--bad);
}

.status-pill-muted,
.badge-muted {
  color: var(--muted);
}

/* ---- Lines that report: notices, errors, the result of the last action ---- */

.notice {
  overflow-wrap: break-word;
  margin: var(--s-3) 0;
  padding: var(--s-3) var(--s-5);
  border-left: 3px solid var(--accent);
  border-radius: var(--r-sm);
  background: var(--accent-wash);
  color: var(--text);
}

.error-panel {
  margin: var(--s-3) 0;
  padding: var(--s-3) var(--s-5);
  border-left: 3px solid var(--bad);
  border-radius: var(--r-sm);
  background: var(--bad-wash);
  color: var(--text);
  overflow-wrap: anywhere;
}

.error-panel p {
  margin: 0 0 var(--s-2);
}

.error-panel p:last-child {
  margin-bottom: 0;
}

.error-title {
  font-weight: 600;
  color: var(--bad);
}

.action-notice {
  display: flex;
  align-items: center;
  gap: var(--s-2);
  min-height: 1.5rem;
  margin: var(--s-2) 0;
  font-weight: 600;
  overflow-wrap: anywhere;
}

.action-notice:not(:empty)::before {
  content: "";
  flex: none;
  width: 9px;
  height: 10px;
  background: var(--accent);
  clip-path: polygon(50% 0, 100% 25%, 100% 75%, 50% 100%, 0 75%, 0 25%);
}

/* ---- Narrow screens: a row's actions stack, so the pinned column stays narrow ---- */

@media (max-width: 900px) {
  td.actions > .button,
  td.actions > .action {
    display: flex;
    width: 100%;
    margin: 0 0 6px;
  }

  td.actions > .action {
    align-items: stretch;
  }

  td.actions > :last-child {
    margin-bottom: 0;
  }
}

/* ---- Small lists of labels ---- */

.labels {
  display: flex;
  flex-wrap: wrap;
  gap: 0 var(--s-2);
  min-width: 0;
  max-width: 100%;
  margin: 0;
  padding: 0;
  list-style: none;
  font-family: var(--mono);
  font-size: var(--fs-mono-fine);
  color: var(--muted);
}

.labels li {
  min-width: 0;
  overflow-wrap: anywhere;
}

.labels li + li::before {
  content: "· ";
}
````

<!-- file: src/styles/shell.css | create -->
Create `packages/console-web/src/styles/shell.css`:

````css
/* The frame around every signed-in page: the rail (a top bar with a menu on narrow
   screens), the main column, the page header and the tab strip. */

.app {
  display: grid;
  grid-template-columns: var(--rail-width) minmax(0, 1fr);
  min-height: 100vh;
}

.app-main {
  min-width: 0;
}

main {
  display: block;
  max-width: min(100%, 1400px);
  padding: 30px var(--s-9) var(--s-10);
}

/* ---- Rail ---- */

.rail {
  position: sticky;
  top: 0;
  display: flex;
  flex-direction: column;
  gap: var(--s-8);
  height: 100vh;
  padding: 22px 18px;
  overflow-y: auto;
  border-right: 1px solid var(--line);
  background: var(--panel);
}

.rail-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--s-3);
}

.brand {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  color: var(--text);
  text-decoration: none;
}

.brand:hover {
  color: var(--text);
}

.brand-mark {
  display: block;
  flex: none;
}

.wordmark {
  font-size: 1.25rem;
  font-weight: 700;
  letter-spacing: -0.02em;
}

.wordmark em {
  font-family: var(--wordmark-serif);
  font-weight: 400;
  color: var(--amber);
  letter-spacing: -0.01em;
}

.menu-button {
  display: none;
}

.rail-panel {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: var(--s-8);
  min-height: 0;
}

.nav-list,
.nav-sublist {
  display: flex;
  flex-direction: column;
  gap: var(--s-1);
  margin: 0;
  padding: 0;
  list-style: none;
}

.nav-sublist {
  margin-top: var(--s-1);
}

.nav-gap {
  margin-top: var(--s-3);
}

.nav-link {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: var(--h-control);
  padding: 0 var(--s-3);
  border-radius: var(--r);
  color: var(--muted);
  text-decoration: none;
  overflow-wrap: anywhere;
}

.nav-link:hover {
  background: var(--panel-2);
  color: var(--text);
}

.nav-sublist .nav-link {
  padding-left: 31px;
}

.nav-link[aria-current="page"] {
  background: var(--accent-wash);
  color: var(--text);
  font-weight: 600;
}

.nav-flag {
  margin-left: auto;
  font-size: 0.75rem;
  font-weight: 400;
  color: var(--bad);
  white-space: nowrap;
}

.nav-flag-quiet {
  color: var(--muted);
}

.rail-foot {
  display: grid;
  gap: var(--s-3);
  margin-top: auto;
  padding-top: var(--s-5);
  border-top: 1px solid var(--line);
  font-size: var(--fs-small);
  color: var(--muted);
}

.who {
  margin: 0;
  overflow-wrap: anywhere;
}

.who-name {
  display: block;
  color: var(--text);
}

.theme-select {
  justify-content: space-between;
}

/* ---- Page header ---- */

.page-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--s-3) var(--s-7);
}

.page-title {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: var(--s-1) var(--s-4);
  min-width: 0;
}

.page-sub {
  max-width: 64ch;
  margin: 6px 0 0;
  color: var(--muted);
  overflow-wrap: break-word;
}

.page-sub strong {
  color: var(--text);
}

.page-message {
  padding: var(--s-9);
}

.breadcrumb {
  margin: 0 0 6px;
  font-size: var(--fs-small);
  color: var(--muted);
  overflow-wrap: break-word;
}

/* ---- Tabs (links, each its own address) ---- */

.tab-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 26px;
  margin: 18px 0 var(--s-7);
  padding: 0;
  border-bottom: 1px solid var(--line);
  list-style: none;
}

.tab-link {
  display: inline-flex;
  align-items: center;
  min-height: var(--h-control);
  padding: 0 2px;
  border-bottom: 2px solid transparent;
  color: var(--muted);
  text-decoration: none;
}

.tab-link:hover {
  color: var(--text);
}

.tab-link[aria-current="page"] {
  border-bottom-color: var(--accent);
  color: var(--text);
  font-weight: 600;
}

.tab-panel > h2:first-child {
  margin-top: 0;
}

.section-head,
.filters {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--s-3) var(--s-5);
  margin: var(--s-4) 0 var(--s-1);
}

.filters {
  justify-content: flex-start;
}

.section-head h2,
.section-head h3,
.section-head p {
  margin: 0;
}

/* ---- Narrow screens (a tablet held upright is 768px): the rail becomes a top bar ---- */

@media (max-width: 900px) {
  .app {
    display: block;
  }

  .rail {
    position: static;
    height: auto;
    gap: 0;
    padding: var(--s-3) var(--s-5);
    overflow-y: visible;
    border-right: 0;
    border-bottom: 1px solid var(--line);
  }

  .menu-button {
    display: inline-flex;
  }

  .rail-panel {
    gap: var(--s-6);
    padding: var(--s-5) 0 var(--s-2);
  }

  .rail-panel[data-open="false"] {
    display: none;
  }

  main {
    padding: var(--s-7) var(--s-5) var(--s-10);
  }

  .page-message {
    padding: var(--s-7) var(--s-5);
  }
}

/* ---- The mark (components/Brand.tsx). Brand constants: the same in both themes. ---- */

.brand-hex {
  fill: var(--amber);
  stroke: var(--amber);
  stroke-width: 4;
  stroke-linejoin: round;
}

.brand-nib {
  fill: #14110d;
  stroke: #14110d;
  stroke-width: 2;
  stroke-linejoin: round;
}

.brand-slit {
  fill: none;
  stroke: var(--amber);
  stroke-width: 2.4;
  stroke-linecap: round;
}

.brand-eye {
  fill: var(--amber);
}

.who-line {
  display: block;
}

.idle-notice {
  margin: var(--s-5) var(--s-9) 0;
}

@media (max-width: 900px) {
  .idle-notice {
    margin: var(--s-3) var(--s-5) 0;
  }
}
````

<!-- file: src/styles/fleet.css | create -->
Create `packages/console-web/src/styles/fleet.css`:

````css
/* The fleet overview: totals, the "Needs a look" sheet, one card per leader and its chart.
   Mockup: docs/superpowers/design/InkConsole.dc.html */

.fleet-head {
  margin-bottom: 26px;
}

/* ---- Totals ---- */

.stat-row {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--s-4);
  margin: 0;
}

.stat-tile {
  padding: var(--s-5) 18px;
  border: 1px solid var(--line);
  border-radius: var(--r-lg);
  background: var(--panel);
}

.stat-tile dt {
  font-size: var(--fs-fine);
  color: var(--muted);
}

.stat-tile dd {
  margin: 2px 0 0;
  font-family: var(--mono);
  font-size: var(--fs-figure);
  line-height: 1.2;
}

.stat-note {
  overflow-wrap: break-word;
  margin: var(--s-2) 0 0;
  font-size: var(--fs-fine);
  color: var(--muted);
}

/* ---- The grid of cards ---- */

.fleet-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--s-4);
  margin-top: 26px;
}

.needs-look {
  align-self: start;
  min-width: 0;
  overflow-wrap: break-word;
  padding: var(--s-6) 22px;
  border-radius: var(--r-lg);
}

.needs-look .sheet-title {
  margin-bottom: 10px;
}

.needs-look-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.needs-look-list li {
  padding: 10px 0;
  border-top: 1px solid var(--line);
}

/* A name or a label longer than the card breaks inside it; ordinary words never do. */
.leader-card {
  min-width: 0;
  overflow-wrap: break-word;
}

.leader-card-bad {
  border-color: var(--bad);
}

.leader-card-head {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--s-2) var(--s-3);
}

.leader-card-title {
  min-width: 0;
}

.leader-card-name {
  margin: 0;
  font-size: var(--fs-h2);
  font-weight: 600;
  overflow-wrap: anywhere;
}

.leader-card-name a {
  color: var(--text);
  text-decoration: none;
}

.leader-card-name a:hover {
  color: var(--text);
  text-decoration: underline;
  text-decoration-color: var(--accent);
}

.leader-card-chart {
  min-height: 64px;
  margin: var(--s-5) 0 var(--s-4);
  color: var(--muted);
}

.figure-row {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin: 0;
}

.figure-row dt {
  font-size: var(--fs-mono-fine);
  color: var(--muted);
}

.figure-row dd {
  margin: 0;
  font-family: var(--mono);
  font-size: var(--fs-figure-small);
}

.leader-card-foot {
  margin: var(--s-4) 0 0;
  padding-top: var(--s-3);
  border-top: 1px solid var(--line);
  font-size: var(--fs-small);
  color: var(--muted);
}

.leader-card-foot strong {
  font-weight: 400;
  color: var(--text);
}

.leader-card-what {
  margin: var(--s-5) 0 0;
}

.leader-card-more {
  margin: 6px 0 0;
  font-size: var(--fs-small);
  color: var(--muted);
}

.leader-card-link {
  margin: var(--s-4) 0 0;
  font-weight: 600;
}

/* ---- Throughput chart: one wide line, stretched to the card ---- */

.sparkline {
  display: block;
  width: 100%;
  height: 64px;
}

.sparkline-axis {
  stroke: var(--edge);
  stroke-width: 1;
  vector-effect: non-scaling-stroke;
}

.sparkline-line {
  fill: none;
  stroke: var(--accent);
  stroke-width: 2;
  stroke-linejoin: round;
  stroke-linecap: round;
  vector-effect: non-scaling-stroke;
}

/* A lone point: a zero-length stroke with round caps stays round however the chart is
   stretched (a <circle> would become an ellipse). */
.sparkline-dot {
  fill: none;
  stroke: var(--accent);
  stroke-width: 5;
  stroke-linecap: round;
  vector-effect: non-scaling-stroke;
}

.sparkline-down {
  fill: var(--bad);
}

@media (max-width: 640px) {
  .fleet-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .stat-row {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
````

<!-- file: src/styles/signin.css | create -->
Create `packages/console-web/src/styles/signin.css`:

````css
/* The sign-in page: the brand on ink beside the sign-in card.
   Mockup: docs/superpowers/design/SignIn.dc.html */

.signin {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  min-height: 100vh;
}

.signin-brand {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: 40px;
  padding: var(--s-10) 64px;
  background: var(--ground);
}

.signin-logo .wordmark {
  font-size: 1.625rem;
}

.signin-pitch {
  margin: 0 0 18px;
  font-family: var(--serif);
  font-size: var(--fs-display);
  line-height: 1.08;
  letter-spacing: -0.015em;
  text-wrap: balance;
}

.signin-lede {
  max-width: 46ch;
  margin: 0;
  font-size: 1.0625rem;
  color: var(--muted);
}

.signin-host {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 0;
  font-size: var(--fs-small);
  color: var(--muted);
  overflow-wrap: anywhere;
}

.signin-side {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--s-10) 64px;
  background: var(--panel);
}

.signin-card {
  width: min(400px, 100%);
  padding: 32px 32px var(--s-8);
}

.signin-title {
  margin-bottom: 6px;
  font-size: var(--fs-sheet-title);
}

.provider-list {
  display: grid;
  gap: 10px;
  margin: 22px 0 0;
  padding: 0;
  list-style: none;
}

.provider-list .button {
  min-height: 48px;
  font-weight: 600;
}

.signin-note {
  margin: 22px 0 0;
  padding-top: var(--s-5);
  border-top: 1px solid var(--line);
  font-size: var(--fs-small);
  color: var(--muted);
}

@media (max-width: 900px) {
  .signin {
    grid-template-columns: minmax(0, 1fr);
    grid-template-rows: auto 1fr;
  }

  .signin-brand,
  .signin-side {
    padding: var(--s-9) var(--s-7);
  }

  .signin-brand {
    gap: var(--s-7);
  }

  .signin-side {
    align-items: flex-start;
  }
}
````

<!-- file: src/components/Brand.tsx | create -->
Create `packages/console-web/src/components/Brand.tsx`:

````tsx
// The SwarmScribe mark and wordmark, drawn inline (the CSP allows no image from elsewhere,
// and an inline SVG costs no request). The shapes are the public site's logo.svg; the
// colours come from classes in styles/shell.css, never from a style attribute.

export function BrandMark({ size = 30 }: { size?: number }) {
  return (
    <svg
      className="brand-mark"
      width={size}
      height={size}
      viewBox="0 0 64 64"
      aria-hidden="true"
      focusable="false"
    >
      <path className="brand-hex" d="M32 4 56.25 18v28L32 60 7.75 46V18z" />
      <path className="brand-nib" d="M32 51 21 30l4-14h14l4 14z" />
      <path className="brand-slit" d="M32 51V33" />
      <circle className="brand-eye" cx="32" cy="30.5" r="3" />
    </svg>
  );
}

/** "Swarm" in bold sans, "Scribe" in italic serif amber: one word to a screen reader. */
export function Wordmark() {
  return (
    <span className="wordmark">
      Swarm<em>Scribe</em>
    </span>
  );
}
````

<!-- file: src/assets/favicon.svg | create -->
Create `packages/console-web/src/assets/favicon.svg`:

````xml
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" aria-label="SwarmScribe">
  <path d="M32 4 56.25 18v28L32 60 7.75 46V18z" fill="#f5b83d" stroke="#f5b83d" stroke-width="4" stroke-linejoin="round"/>
  <path d="M32 51 21 30l4-14h14l4 14z" fill="#14110d" stroke="#14110d" stroke-width="2" stroke-linejoin="round"/>
  <path d="M32 51V33" stroke="#f5b83d" stroke-width="2.4" stroke-linecap="round"/>
  <circle cx="32" cy="30.5" r="3" fill="#f5b83d"/>
</svg>
````

<!-- file: index.html | replace -->
Replace the whole of `packages/console-web/index.html` with:

````html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <meta name="color-scheme" content="dark light" />
    <meta name="theme-color" content="#14110d" />
    <link rel="icon" type="image/svg+xml" href="/src/assets/favicon.svg" />
    <title>SwarmScribe console</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
````

<!-- file: src/components/Dialog.tsx | patch -->
Change `packages/console-web/src/components/Dialog.tsx`:

````diff
--- a/packages/console-web/src/components/Dialog.tsx
+++ b/packages/console-web/src/components/Dialog.tsx
@@ -69,7 +69,7 @@
   return createPortal(
     <dialog
       ref={ref}
-      className="dialog"
+      className="dialog sheet sheet-lifted"
       role={role}
       aria-modal="true"
       aria-labelledby={titleId}
````

- [ ] **Step 4: Run the unit tests to see them pass**

Run: `npm test`

Expected: PASS. **359 tests pass** (291 + 68 in `tokens.test.ts`). If a contrast test fails,
a token was mistyped: compare the hex with the spec's table in section 3.2. Do not change a
threshold.

- [ ] **Step 5: Type-check, lint, build and run the end-to-end tests**

Run, in `packages/console-web`:

```bash
npm run typecheck
npm run lint
npm test
npm run build
npm run e2e -- --retries=0
```

Expected: `tsc` prints nothing. ESLint prints nothing (0 warnings). Vitest: **359 tests pass**, none
fail. The build ends with `dist/ ok: index.html and 3 hashed assets`. Playwright: the whole end-to-end suite passes, with no retry.

No spec changed in this task, so all of them must pass as they did before Task 1. This is
the proof that the restyle broke nothing: axe in both themes, no broken words, no sideways
scroll, the dialogs.

Then look. Open `dist/` through the harness or the console and check, in both themes: text
is readable everywhere, buttons look like buttons, a dialog is a paper sheet with an amber
offset shadow on the dark page and an ink sheet on the light one. The header will look
bare; that is Task 2.

If an end-to-end test fails, read what it was checking before changing it: the page is wrong
far more often than the test. Nothing may be left listening on ports 8900 or 8901 afterwards.

- [ ] **Step 6: Commit**

```bash
git add packages/console-web/src/styles.css packages/console-web/src/styles/tokens.css packages/console-web/src/styles/base.css packages/console-web/src/styles/controls.css packages/console-web/src/styles/surfaces.css packages/console-web/src/styles/shell.css packages/console-web/src/styles/fleet.css packages/console-web/src/styles/signin.css packages/console-web/src/components/Brand.tsx packages/console-web/src/assets/favicon.svg packages/console-web/index.html packages/console-web/src/components/Dialog.tsx packages/console-web/src/styles/tokens.test.ts
git commit -m "Console web app: brand tokens in two colour sets, and the whole app restyled on them

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

### Task 2: The rail, and the top bar with its menu

**Files:**
- Modify: `packages/console-web/src/App.tsx`
- Modify: `packages/console-web/src/components/Layout.tsx`
- Test: `packages/console-web/src/shell.test.tsx`
- Test: `packages/console-web/src/pages/SignInPage.test.tsx` (new)
- Test: `packages/console-web/src/pages/leader/LeaderPage.test.tsx`
- Test: `packages/console-web/e2e/tests/a11y.spec.ts`
- Test: `packages/console-web/e2e/tests/admin.spec.ts`
- Test: `packages/console-web/e2e/tests/drilldown.spec.ts`
- Test: `packages/console-web/e2e/tests/overview.spec.ts`
- Test: `packages/console-web/e2e/tests/support.ts`
- Test: `packages/console-web/e2e/tests/theme.spec.ts` (new)

**Interfaces:**
- Consumes: Task 1's classes (`.app`, `.app-main`, `.rail`, `.on-ink`, `.rail-top`, `.rail-panel`, `.rail-foot`, `.menu-button`, `.nav-*`, `.who*`, `.mark*`, `.idle-notice`, `.button-wide`) and `BrandMark`, `Wordmark` from `src/components/Brand.tsx`; `useFleet(): PollState<FleetLeader[]>` from `src/app/fleet.tsx`; `leaderUrl(name: string, tab?: TabId): string` from `src/pages/leader/tabs.ts`; `useSession()`, `useLocation()`, `Link` (all unchanged).
- Produces: `Layout({ pageOf?: (pathname: string) => string; children: ReactNode })` (the `nav` prop and the `NavItem` type are gone); `roleSummary(leaders: FleetLeader[]): string` exported from `src/components/Layout.tsx`; the landmarks later tests rely on: a `banner` holding a link named "SwarmScribe console", a `navigation` named "Console" with a list named "Leaders", a button named "Menu" with `aria-expanded` and `aria-controls`, and `main#main`.

`Layout.tsx` becomes the frame of the spec's sections 4.1, 4.2 and 5.1. It now reads the
fleet itself (it is already rendered inside `FleetProvider`) to list the leaders under Fleet,
so `App.tsx` stops passing navigation items.

Three things to get right:

- **The menu is open for one address.** Its state is the pathname it was opened at, so
  following any link closes it with no effect to run and no handler on the links.
- **Escape** closes the menu and returns focus to the Menu button, but only when no dialog is
  open: a dialog's own Escape comes first.
- **The focus effect is unchanged.** Moving to another page still puts focus on the new
  page's `h1`; copy that effect as it is.

The panel under the brand is always in the document. CSS hides it below 900px unless
`data-open="true"`, so in jsdom (no CSS) every link is always "visible". The end-to-end
tests are what check the hiding.

The sign-in page's three tests move out of `src/shell.test.tsx` into
`src/pages/SignInPage.test.tsx`, word for word; Task 4 rewrites them with the page.

- [ ] **Step 1: Write the failing tests**

`shell.test.tsx` is rewritten for the rail. `SignInPage.test.tsx` is new but holds the three
existing sign-in tests unchanged (they pass before and after this task). In
`LeaderPage.test.tsx` two lookups of a leader's link are scoped to the main region, because
the rail now has a link with the same name.

<!-- file: src/shell.test.tsx | replace -->
Replace the whole of `packages/console-web/src/shell.test.tsx` with:

````tsx
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { resetSessionEndedForTests } from "./app/navigation";
import * as navigation from "./app/navigation";
import { roleSummary } from "./components/Layout";
import { fail, mockFetch, reply } from "./test/fetchMock";
import { SESSION, history, leader } from "./test/fixtures";
import { renderApp } from "./test/renderApp";

afterEach(() => {
  resetSessionEndedForTests();
  vi.restoreAllMocks();
});

function signedInConsole() {
  return mockFetch()
    .on("GET /api/session", reply(200, SESSION))
    .on("GET /api/fleet", reply(200, [leader()]))
    .on("GET /api/leaders/eu-1/history?hours=24", reply(200, history()))
    .on("POST /api/session/logout", reply(204));
}

const DOWN = leader({ name: "us-1", health: "unreachable", consecutive_failures: 3 });

describe("the signed-in shell", () => {
  it("has a skip link, the brand, the person, a theme switch and sign-out", async () => {
    signedInConsole();
    render(<App />);
    expect(await screen.findByRole("heading", { level: 1, name: "Fleet" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Skip to main content" })).toHaveAttribute("href", "#main");
    const banner = within(screen.getByRole("banner"));
    expect(banner.getByRole("link", { name: "SwarmScribe console" })).toHaveAttribute("href", "/");
    expect(banner.getByText("person@example.org")).toBeInTheDocument();
    expect(await banner.findByText("Operator on 1 leader")).toBeInTheDocument();
    expect(banner.getByRole("combobox", { name: "Theme" })).toBeInTheDocument();
    expect(banner.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
    expect(banner.getByRole("link", { name: "Fleet", current: "page" })).toBeInTheDocument();
  });

  it("comes before the main region and holds the skip link's target", async () => {
    signedInConsole();
    const { container } = render(<App />);
    await screen.findByRole("heading", { level: 1, name: "Fleet" });
    const main = screen.getByRole("main");
    expect(main).toHaveAttribute("id", "main");
    expect(main).toHaveAttribute("tabindex", "-1");
    const banner = screen.getByRole("banner");
    expect(banner.compareDocumentPosition(main) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(container.querySelector("[style]")).toBeNull();
  });

  it("lists each visible leader under Fleet and marks the one being looked at", async () => {
    renderApp("/leaders/eu-1/pools", { fleet: [leader(), DOWN] }).on(
      "GET /api/leaders/eu-1/followers",
      reply(200, []),
    );
    const nav = within(await screen.findByRole("navigation", { name: "Console" }));
    const leaders = within(await nav.findByRole("list", { name: "Leaders" }));
    expect(leaders.getAllByRole("link").map((link) => link.textContent)).toEqual(["eu-1", "us-1 no answer"]);
    expect(leaders.getByRole("link", { name: "eu-1" })).toHaveAttribute("aria-current", "page");
    expect(leaders.getByRole("link", { name: "eu-1" })).toHaveAttribute("href", "/leaders/eu-1/pools");
    expect(leaders.getByRole("link", { name: "us-1 no answer" })).not.toHaveAttribute("aria-current");
    expect(nav.getByRole("link", { name: "Fleet" })).not.toHaveAttribute("aria-current");
    // A viewer of leaders is not offered Administration.
    expect(nav.queryByRole("link", { name: "Administration" })).not.toBeInTheDocument();
  });

  it("offers Administration to a console administrator and marks it when there", async () => {
    renderApp("/admin/leaders", { session: { ...SESSION, console_admin: true } }).on(
      "GET /api/admin/leaders",
      reply(200, []),
    );
    const banner = within(await screen.findByRole("banner"));
    expect(await banner.findByRole("link", { name: "Administration" })).toHaveAttribute("aria-current", "page");
    expect(banner.getByRole("link", { name: "Administration" })).toHaveAttribute("href", "/admin/leaders");
    expect(banner.getByText("Console administrator")).toBeInTheDocument();
    expect(banner.getByRole("link", { name: "Fleet" })).not.toHaveAttribute("aria-current");
  });

  it("opens and closes the menu from its button, and Escape hands focus back to it", async () => {
    signedInConsole();
    render(<App />);
    await screen.findByRole("heading", { level: 1, name: "Fleet" });
    const menu = screen.getByRole("button", { name: "Menu" });
    const panel = document.getElementById(menu.getAttribute("aria-controls") ?? "");
    expect(panel).not.toBeNull();
    expect(menu).toHaveAttribute("aria-expanded", "false");
    expect(panel).toHaveAttribute("data-open", "false");

    await userEvent.click(menu);
    expect(menu).toHaveAttribute("aria-expanded", "true");
    expect(panel).toHaveAttribute("data-open", "true");
    await userEvent.click(menu);
    expect(menu).toHaveAttribute("aria-expanded", "false");

    await userEvent.click(menu);
    await userEvent.tab();
    await userEvent.keyboard("{Escape}");
    expect(menu).toHaveAttribute("aria-expanded", "false");
    expect(menu).toHaveFocus();
  });

  it("closes the menu when a link in it is followed", async () => {
    renderApp("/", { fleet: [leader()] })
      .on("GET /api/leaders/eu-1/history?hours=24", reply(200, history()))
      .on("GET /api/leaders/eu-1/followers", reply(200, []));
    await screen.findByRole("heading", { level: 1, name: "Fleet" });
    const menu = screen.getByRole("button", { name: "Menu" });
    await userEvent.click(menu);
    const nav = within(screen.getByRole("navigation", { name: "Console" }));
    await userEvent.click(await nav.findByRole("link", { name: "eu-1" }));
    await screen.findByRole("heading", { level: 1, name: "eu-1" });
    expect(menu).toHaveAttribute("aria-expanded", "false");
  });

  it("still offers the brand, the theme and sign-out when the fleet cannot be loaded", async () => {
    mockFetch()
      .on("GET /api/session", reply(200, SESSION))
      .on("GET /api/fleet", fail(503, "unavailable", "down"));
    render(<App />);
    expect(await within(await screen.findByRole("main")).findByRole("alert")).toBeInTheDocument();
    const banner = within(screen.getByRole("banner"));
    expect(banner.getByRole("link", { name: "Fleet", current: "page" })).toBeInTheDocument();
    expect(banner.queryByRole("list", { name: "Leaders" })).not.toBeInTheDocument();
    // No fleet, so no claim about roles: only who is signed in.
    expect(banner.getByText("person@example.org")).toBeInTheDocument();
    expect(banner.queryByText(/leader/)).not.toBeInTheDocument();
    expect(banner.getByRole("combobox", { name: "Theme" })).toBeInTheDocument();
    expect(banner.getByRole("button", { name: "Sign out" })).toBeEnabled();
  });

  it("signs out, leaving the app even when the logout request fails", async () => {
    signedInConsole().on("POST /api/session/logout", fail(500, "internal", "boom"));
    const out = vi.spyOn(navigation, "goToSignedOut").mockImplementation(() => undefined);
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "Sign out" }));
    await vi.waitFor(() => expect(out).toHaveBeenCalledTimes(1));
  });

  it("shows the not-found page and moves focus to its heading after navigating there", async () => {
    signedInConsole();
    render(<App />);
    await screen.findByRole("heading", { level: 1, name: "Fleet" });
    window.history.pushState(null, "", "/nowhere");
    window.dispatchEvent(new PopStateEvent("popstate"));
    const heading = await screen.findByRole("heading", { level: 1, name: "Page not found" });
    await vi.waitFor(() => expect(heading).toHaveFocus());
    await userEvent.click(screen.getByRole("link", { name: "Go to the fleet overview" }));
    const fleet = await screen.findByRole("heading", { level: 1, name: "Fleet" });
    await vi.waitFor(() => expect(fleet).toHaveFocus());
  });
});

describe("roleSummary", () => {
  it("says which roles the person holds, highest first", () => {
    expect(roleSummary([])).toBe("No role on any leader yet");
    expect(roleSummary([leader({ role: "admin" })])).toBe("Admin on 1 leader");
    expect(roleSummary([leader({ role: "admin" }), leader({ role: "admin" })])).toBe("Admin on 2 leaders");
    expect(
      roleSummary([leader({ role: "viewer" }), leader({ role: "admin" }), leader({ role: "admin" })]),
    ).toBe("Admin on 2, viewer on 1 leader");
    expect(
      roleSummary([leader({ role: "viewer" }), leader({ role: "operator" }), leader({ role: "admin" })]),
    ).toBe("Admin on 1, operator on 1, viewer on 1 leader");
  });
});
````

<!-- file: src/pages/SignInPage.test.tsx | create -->
Create `packages/console-web/src/pages/SignInPage.test.tsx`:

````tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import { fail, mockFetch, reply } from "../test/fetchMock";
import { SESSION } from "../test/fixtures";

// The sign-in page sits outside the session: these render the whole app at /sign-in.

describe("the sign-in page", () => {
  it("lists the providers with a safe return_to and makes no session-bound calls", async () => {
    window.history.replaceState(null, "", "/sign-in?return_to=%2F%3Flabel%3Denv%253Dprod");
    const mock = mockFetch()
      .on("GET /auth/providers", reply(200, { providers: ["entra", "google"] }))
      .on("GET /api/session", fail(401, "unauthenticated"));
    render(<App />);
    const link = await screen.findByRole("link", { name: "Sign in with Microsoft Entra ID" });
    expect(link.getAttribute("href")).toBe("/auth/login?provider=entra&return_to=%2F%3Flabel%3Denv%253Dprod");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Sign in");
    expect(screen.queryByText(/already signed in/)).not.toBeInTheDocument();
    expect(mock.callsTo("GET /api/fleet")).toHaveLength(0);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("drops a hostile return_to", async () => {
    window.history.replaceState(null, "", "/sign-in?return_to=%2F%2Fevil.example");
    mockFetch()
      .on("GET /auth/providers", reply(200, { providers: ["google"] }))
      .on("GET /api/session", fail(401, "unauthenticated"));
    render(<App />);
    const link = await screen.findByRole("link", { name: "Sign in with Google" });
    expect(link.getAttribute("href")).toBe("/auth/login?provider=google");
  });

  it("offers a way back to a person who is already signed in", async () => {
    window.history.replaceState(null, "", "/sign-in");
    mockFetch()
      .on("GET /auth/providers", reply(200, { providers: ["google"] }))
      .on("GET /api/session", reply(200, SESSION));
    render(<App />);
    expect(await screen.findByText(/already signed in as person@example.org/)).toBeInTheDocument();
  });
});
````

<!-- file: src/pages/leader/LeaderPage.test.tsx | patch -->
Change `packages/console-web/src/pages/leader/LeaderPage.test.tsx`:

````diff
--- a/packages/console-web/src/pages/leader/LeaderPage.test.tsx
+++ b/packages/console-web/src/pages/leader/LeaderPage.test.tsx
@@ -56,7 +56,8 @@
     renderApp("/")
       .on("GET /api/leaders/eu-1/history?hours=24", reply(200, history()))
       .on("GET /api/leaders/eu-1/followers", reply(200, []));
-    await userEvent.click(await screen.findByRole("link", { name: "eu-1" }));
+    const fleet = within(await screen.findByRole("main"));
+    await userEvent.click(await fleet.findByRole("link", { name: "eu-1" }));
     const heading = await screen.findByRole("heading", {
       level: 1,
       name: "eu-1",
@@ -133,7 +134,7 @@
       .on("GET /api/leaders/eu-1/followers", reply(200, []));
     const page = within(await screen.findByRole("main"));
     await userEvent.click(await page.findByRole("link", { name: "Fleet" }));
-    await userEvent.click(await screen.findByRole("link", { name: "us-1" }));
+    await userEvent.click(await page.findByRole("link", { name: "us-1" }));
     await userEvent.click(await screen.findByRole("link", { name: "Jobs" }));
     expect(await screen.findByText("This leader has no jobs.")).toBeInTheDocument();
     release();
````

- [ ] **Step 2: Run the tests to see them fail**

Run: `npx vitest run src/shell.test.tsx src/pages/SignInPage.test.tsx src/pages/leader/LeaderPage.test.tsx`

Expected: FAIL, `Tests  6 failed | 16 passed (22)`. The six are all in `src/shell.test.tsx`:
no element with the text "Operator on 1 leader", no navigation named "Console", no text
"Console administrator", no button named "Menu" (twice), and
`TypeError: roleSummary is not a function`. The moved sign-in tests and the leader page's
tests pass already.

- [ ] **Step 3: Write the implementation**


<!-- file: src/App.tsx | patch -->
Change `packages/console-web/src/App.tsx`:

````diff
--- a/packages/console-web/src/App.tsx
+++ b/packages/console-web/src/App.tsx
@@ -3,8 +3,8 @@
 import { FleetProvider } from "./app/fleet";
 import { RouterProvider, matchPath, useLocation, useNavigate } from "./app/router";
 import { isRouted } from "./app/routes";
-import { SessionProvider, useSession } from "./app/session";
-import { Layout, type NavItem } from "./components/Layout";
+import { SessionProvider } from "./app/session";
+import { Layout } from "./components/Layout";
 import { AdminAdminsPage } from "./pages/admin/AdminAdminsPage";
 import { AdminGrantsPage } from "./pages/admin/AdminGrantsPage";
 import { AdminLeadersPage } from "./pages/admin/AdminLeadersPage";
@@ -13,18 +13,6 @@
 import { leaderUrl } from "./pages/leader/tabs";
 import { NotFoundPage } from "./pages/NotFoundPage";
 import { SignInPage } from "./pages/SignInPage";
-
-const FLEET: NavItem = {
-  to: "/",
-  label: "Fleet",
-  match: (pathname) => pathname === "/" || pathname.startsWith("/leaders/"),
-};
-
-const ADMIN: NavItem = {
-  to: "/admin/leaders",
-  label: "Administration",
-  match: (pathname) => pathname.startsWith("/admin"),
-};
 
 /**
  * The page a path belongs to, for moving focus: switching tabs inside one leader's
@@ -59,11 +47,9 @@
 }
 
 function SignedIn() {
-  const { session } = useSession();
-  const nav = session.console_admin ? [FLEET, ADMIN] : [FLEET];
   return (
     <FleetProvider>
-      <Layout nav={nav} pageOf={pageOf}>
+      <Layout pageOf={pageOf}>
         <SignedInPage />
       </Layout>
     </FleetProvider>
````

<!-- file: src/components/Layout.tsx | replace -->
Replace the whole of `packages/console-web/src/components/Layout.tsx` with:

````tsx
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import type { FleetLeader, Role } from "../api/types";
import { isIdle, onResume } from "../app/activity";
import { useFleet } from "../app/fleet";
import { Link, useLocation } from "../app/router";
import { useSession } from "../app/session";
import { readTheme, saveTheme, type ThemeChoice } from "../app/theme";
import { leaderUrl } from "../pages/leader/tabs";
import { BrandMark, Wordmark } from "./Brand";

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

/** Shown once background checks have stopped because the person has been away. */
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
    <p className="notice idle-notice" role="status">
      Checks are paused because you have been away. Press a key or click to start them again.
    </p>
  );
}

const ROLES: Role[] = ["admin", "operator", "viewer"];

/** "Admin on 2 leaders", "Admin on 2, viewer on 1 leader": the roles the person holds. */
export function roleSummary(leaders: FleetLeader[]): string {
  const parts = ROLES.map((role) => ({
    role,
    n: leaders.filter((leader) => leader.role === role).length,
  })).filter((part) => part.n > 0);
  if (parts.length === 0) return "No role on any leader yet";
  const text = parts
    .map((part, i) =>
      i === parts.length - 1
        ? `${part.role} on ${part.n} ${part.n === 1 ? "leader" : "leaders"}`
        : `${part.role} on ${part.n}`,
    )
    .join(", ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** What the rail says beside a leader that needs attention; nothing while it answers. */
function railFlag(leader: FleetLeader): { text: string; quiet: boolean } | null {
  switch (leader.health) {
    case "unreachable":
      return { text: "no answer", quiet: false };
    case "credential_revoked":
      return { text: "revoked", quiet: false };
    case "disabled":
      return { text: "off", quiet: true };
    default:
      return null;
  }
}

/** The leader a drill-down address names, lower-cased; null anywhere else. */
function leaderOf(pathname: string): string | null {
  const segments = pathname.split("/");
  if (segments[1] !== "leaders" || !segments[2]) return null;
  try {
    return decodeURIComponent(segments[2]).toLowerCase();
  } catch {
    return null;
  }
}

/**
 * The frame of every signed-in page: the rail (brand, Fleet with each visible leader beneath
 * it, Administration for a console administrator, then the person, the theme and sign-out)
 * and the main region. Below 900px the rail is a top bar and everything under the brand
 * sits behind a Menu button (a disclosure, not a dialog: the page stays usable).
 */
export function Layout({
  pageOf = (pathname) => pathname,
  children,
}: {
  /** Names the page a path belongs to; focus moves only when this changes (not on a tab switch). */
  pageOf?: (pathname: string) => string;
  children: ReactNode;
}) {
  const { session, signOut } = useSession();
  const { pathname } = useLocation();
  const { data: leaders } = useFleet();
  const page = pageOf(pathname);
  const main = useRef<HTMLElement>(null);
  const first = useRef(true);
  const menuButton = useRef<HTMLButtonElement>(null);
  const panelId = useId();
  // The menu is open for one address: following a link closes it, with no effect to run.
  const [openAt, setOpenAt] = useState<string | null>(null);
  const open = openAt === pathname;

  // After an in-app navigation, move focus to the new page's main heading (or the main
  // region when a page has none), so keyboard and screen-reader users start at its content
  // and hear its title, not the link they left behind. The first render keeps the browser's
  // own start of page. A heading is not focusable by default, hence tabindex -1.
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    const heading = main.current?.querySelector("h1");
    if (heading) {
      heading.tabIndex = -1;
      heading.focus();
    } else {
      main.current?.focus();
    }
  }, [page]);

  // Escape closes the open menu and hands focus back to its button. A dialog's own Escape
  // comes first: with one open, this does nothing.
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || document.querySelector("dialog[open]") !== null) return;
      setOpenAt(null);
      menuButton.current?.focus();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  const currentLeader = leaderOf(pathname);
  const onFleet = pathname === "/";
  const onAdmin = pathname === "/admin" || pathname.startsWith("/admin/");
  return (
    <div className="app">
      <a className="skip-link" href="#main">
        Skip to main content
      </a>
      <header className="rail on-ink">
        <div className="rail-top">
          <Link to="/" className="brand" aria-label="SwarmScribe console">
            <BrandMark />
            <Wordmark />
          </Link>
          <button
            ref={menuButton}
            type="button"
            className="button menu-button"
            aria-expanded={open}
            aria-controls={panelId}
            onClick={() => setOpenAt(open ? null : pathname)}
          >
            Menu
          </button>
        </div>
        <div id={panelId} className="rail-panel" data-open={open ? "true" : "false"}>
          <nav aria-label="Console">
            <ul className="nav-list">
              <li>
                <Link to="/" className="nav-link" aria-current={onFleet ? "page" : undefined}>
                  <span className={onFleet ? "mark mark-hex" : "mark mark-hex mark-quiet"} aria-hidden="true" />
                  Fleet
                </Link>
                {leaders !== undefined && leaders.length > 0 && (
                  <ul className="nav-sublist" aria-label="Leaders">
                    {leaders.map((leader) => {
                      const flag = railFlag(leader);
                      const current = currentLeader === leader.name.toLowerCase();
                      return (
                        <li key={leader.name}>
                          <Link
                            to={leaderUrl(leader.name)}
                            className="nav-link"
                            aria-current={current ? "page" : undefined}
                          >
                            {leader.name}
                            {flag !== null && " "}
                            {flag !== null && (
                              <span className={flag.quiet ? "nav-flag nav-flag-quiet" : "nav-flag"}>
                                {flag.text}
                              </span>
                            )}
                          </Link>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </li>
              {session.console_admin && (
                <li className="nav-gap">
                  <Link
                    to="/admin/leaders"
                    className="nav-link"
                    aria-current={onAdmin ? "page" : undefined}
                  >
                    <span
                      className={onAdmin ? "mark mark-hex" : "mark mark-hex mark-quiet"}
                      aria-hidden="true"
                    />
                    Administration
                  </Link>
                </li>
              )}
            </ul>
          </nav>
          <div className="rail-foot">
            <p className="who">
              <span className="who-name">{session.email ?? session.subject}</span>
              {session.console_admin && <span className="who-line">Console administrator</span>}
              {leaders !== undefined && <span className="who-line">{roleSummary(leaders)}</span>}
            </p>
            <ThemeSelect />
            <button type="button" className="button button-wide" onClick={() => void signOut()}>
              Sign out
            </button>
          </div>
        </div>
      </header>
      <div className="app-main">
        <IdleNotice />
        <main id="main" ref={main} tabIndex={-1}>
          {children}
        </main>
      </div>
    </div>
  );
}
````

- [ ] **Step 4: Run the unit tests to see them pass**

Run: `npm test`

Expected: PASS. **366 tests pass** (359 + 7 new in `shell.test.tsx`; the three sign-in tests
only moved).

- [ ] **Step 5: Update the end-to-end tests**

What changes for a browser: the navigation is named "Console", not "Main"; at tablet width
Sign out is behind the Menu button; a leader's name is now a link in the rail as well as on
the overview, so a lookup by that name is scoped to the main region. `signIn` in
`support.ts` waits for the brand link instead of Sign out, which is hidden at narrow widths.
`theme.spec.ts` is new: it reads the painted colours with each Theme choice against each
system setting.

<!-- file: e2e/tests/a11y.spec.ts | patch -->
Change `packages/console-web/e2e/tests/a11y.spec.ts`:

````diff
--- a/packages/console-web/e2e/tests/a11y.spec.ts
+++ b/packages/console-web/e2e/tests/a11y.spec.ts
@@ -97,5 +97,18 @@
       await page.getByRole("button", { name: "Remove eu-1" }).click();
       await expectAccessible(page, `remove leader confirm dialog (${theme})`);
     });
+
+    test("the top bar and its open menu have no accessibility violations at tablet width", async ({
+      page,
+    }) => {
+      await page.setViewportSize({ width: 768, height: 1024 });
+      await signIn(page, "admin");
+      await expect(page.getByRole("heading", { level: 1, name: "Fleet" })).toBeVisible();
+      await expect(page.getByText(/^Loading/)).toHaveCount(0);
+      await expectAccessible(page, `fleet at tablet width (${theme})`);
+      await page.getByRole("button", { name: "Menu" }).click();
+      await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
+      await expectAccessible(page, `fleet at tablet width, menu open (${theme})`);
+    });
   });
 }
````

<!-- file: e2e/tests/admin.spec.ts | patch -->
Change `packages/console-web/e2e/tests/admin.spec.ts`:

````diff
--- a/packages/console-web/e2e/tests/admin.spec.ts
+++ b/packages/console-web/e2e/tests/admin.spec.ts
@@ -59,7 +59,7 @@
 
   // The new leader has no fake behind it: it shows in the fleet (after the next 10 s
   // refresh) without figures, and the rest of the page is unaffected.
-  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Fleet" }).click();
+  await page.getByRole("navigation", { name: "Console" }).getByRole("link", { name: "Fleet" }).click();
   await expect(page.getByRole("rowheader", { name: /ap-1/ })).toBeVisible({ timeout: 20_000 });
 
   await page.getByRole("link", { name: "Administration" }).click();
````

<!-- file: e2e/tests/drilldown.spec.ts | patch -->
Change `packages/console-web/e2e/tests/drilldown.spec.ts`:

````diff
--- a/packages/console-web/e2e/tests/drilldown.spec.ts
+++ b/packages/console-web/e2e/tests/drilldown.spec.ts
@@ -155,8 +155,8 @@
   await expect(page.getByRole("alert")).toContainText("The leader cannot be reached right now.");
   await expect(page.getByRole("link", { name: "Locations" })).toBeVisible();
 
-  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Fleet" }).click();
-  await page.getByRole("link", { name: "eu-1" }).click();
+  await page.getByRole("navigation", { name: "Console" }).getByRole("link", { name: "Fleet" }).click();
+  await page.getByRole("main").getByRole("link", { name: "eu-1", exact: true }).click();
   await page.getByRole("link", { name: "Jobs" }).click();
   await page
     .getByRole("button", { name: /^Cancel job / })
````

<!-- file: e2e/tests/overview.spec.ts | patch -->
Change `packages/console-web/e2e/tests/overview.spec.ts`:

````diff
--- a/packages/console-web/e2e/tests/overview.spec.ts
+++ b/packages/console-web/e2e/tests/overview.spec.ts
@@ -43,7 +43,16 @@
   await expect(page.getByRole("rowheader", { name: /eu-1/ })).toBeVisible();
   const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
   expect(overflow).toBeLessThanOrEqual(0);
+  // Sign out is behind the Menu button at this width.
+  const menu = page.getByRole("button", { name: "Menu" });
+  await expect(menu).toHaveAttribute("aria-expanded", "false");
+  await expect(page.getByRole("button", { name: "Sign out" })).toBeHidden();
+  await menu.click();
+  await expect(menu).toHaveAttribute("aria-expanded", "true");
   await expect(page.getByRole("button", { name: "Sign out" })).toBeInViewport();
+  await page.keyboard.press("Escape");
+  await expect(menu).toBeFocused();
+  await expect(page.getByRole("button", { name: "Sign out" })).toBeHidden();
   await expect(page.getByRole("region", { name: "Leaders" })).toBeVisible();
 });
 
````

<!-- file: e2e/tests/support.ts | patch -->
Change `packages/console-web/e2e/tests/support.ts`:

````diff
--- a/packages/console-web/e2e/tests/support.ts
+++ b/packages/console-web/e2e/tests/support.ts
@@ -69,7 +69,9 @@
   expect(answer.ok()).toBe(true);
   const { callback } = (await answer.json()) as { callback: string };
   await page.goto(callback);
-  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
+  // The brand link is in the signed-in frame at every width (Sign out is behind the Menu
+  // button on a narrow screen), and nowhere on the sign-in page.
+  await expect(page.getByRole("link", { name: "SwarmScribe console" })).toBeVisible();
 }
 
 /** Zero WCAG 2.1 A/AA violations on the page as it is now. */
````

<!-- file: e2e/tests/theme.spec.ts | create -->
Create `packages/console-web/e2e/tests/theme.spec.ts`:

````ts
import type { Page } from "@playwright/test";
import { expect, signIn, test } from "./support";

// The two colour sets, as the browser paints them (src/styles/tokens.css).
const INK = "rgb(20, 17, 13)";
const PAPER = "rgb(243, 234, 217)";

function ground(page: Page): Promise<string> {
  return page.evaluate(() => getComputedStyle(document.documentElement).backgroundColor);
}

/** Opens a confirmation and reports its background: a sheet is the opposite of the page. */
async function sheet(page: Page): Promise<string> {
  await page
    .getByRole("button", { name: /^Cancel job / })
    .first()
    .click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toBeVisible();
  const colour = await dialog.evaluate((el) => getComputedStyle(el).backgroundColor);
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  return colour;
}

test.describe("on a system set to light", () => {
  test.use({ colorScheme: "light" });

  test("the console is light until Dark is chosen, and the choice survives a reload", async ({ page }) => {
    await signIn(page, "operator", "/leaders/eu-1/jobs");
    expect(await ground(page)).toBe(PAPER);
    expect(await sheet(page)).toBe(INK);

    await page.getByRole("combobox", { name: "Theme" }).selectOption("dark");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    expect(await ground(page)).toBe(INK);
    expect(await sheet(page)).toBe(PAPER);

    await page.reload();
    await expect(page.getByRole("combobox", { name: "Theme" })).toHaveValue("dark");
    expect(await ground(page)).toBe(INK);

    await page.getByRole("combobox", { name: "Theme" }).selectOption("system");
    await expect(page.locator("html")).not.toHaveAttribute("data-theme", /./);
    expect(await ground(page)).toBe(PAPER);
  });
});

test.describe("on a system set to dark", () => {
  test.use({ colorScheme: "dark" });

  test("the console is dark until Light is chosen", async ({ page }) => {
    await signIn(page, "operator", "/leaders/eu-1/jobs");
    expect(await ground(page)).toBe(INK);
    expect(await sheet(page)).toBe(PAPER);

    await page.getByRole("combobox", { name: "Theme" }).selectOption("light");
    expect(await ground(page)).toBe(PAPER);
    expect(await sheet(page)).toBe(INK);
    // The rail is ink in both themes.
    const rail = await page.getByRole("banner").evaluate((el) => getComputedStyle(el).color);
    expect(rail).toBe(PAPER);
  });
});

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

Expected: `tsc` prints nothing. ESLint prints nothing (0 warnings). Vitest: **366 tests pass**, none
fail. The build ends with `dist/ ok: index.html and 3 hashed assets`. Playwright: the whole end-to-end suite passes, with no retry.

Then look at any page at 1280 and at 768 pixels wide in
both themes: the rail on the left with the logo, Fleet, the leaders and (as an admin)
Administration; below 900px a bar with the logo and Menu, and the menu opening beneath it.

If an end-to-end test fails, read what it was checking before changing it: the page is wrong
far more often than the test. Nothing may be left listening on ports 8900 or 8901 afterwards.

- [ ] **Step 7: Commit**

```bash
git add packages/console-web/src/App.tsx packages/console-web/src/components/Layout.tsx packages/console-web/src/shell.test.tsx packages/console-web/src/pages/SignInPage.test.tsx packages/console-web/src/pages/leader/LeaderPage.test.tsx packages/console-web/e2e/tests/a11y.spec.ts packages/console-web/e2e/tests/admin.spec.ts packages/console-web/e2e/tests/drilldown.spec.ts packages/console-web/e2e/tests/overview.spec.ts packages/console-web/e2e/tests/support.ts packages/console-web/e2e/tests/theme.spec.ts
git commit -m "Console web app: the rail, with each leader under Fleet and a menu at narrow widths

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

### Task 3: The fleet overview: totals, a card per leader, Needs a look

**Files:**
- Modify: `packages/console-web/src/components/HealthBadge.tsx`
- Modify: `packages/console-web/src/components/Sparkline.tsx`
- Modify: `packages/console-web/src/components/ThroughputChart.tsx`
- Modify: `packages/console-web/src/lib/format.ts`
- Modify: `packages/console-web/src/pages/FleetPage.tsx`
- Test: `packages/console-web/src/lib/format.test.ts`
- Test: `packages/console-web/src/components/Sparkline.test.tsx`
- Test: `packages/console-web/src/pages/FleetPage.test.tsx`
- Test: `packages/console-web/e2e/tests/a11y.spec.ts`
- Test: `packages/console-web/e2e/tests/admin.spec.ts`
- Test: `packages/console-web/e2e/tests/overview.spec.ts`
- Test: `packages/console-web/e2e/tests/sign-in.spec.ts`

**Interfaces:**
- Consumes: Task 1's classes (`.fleet-head`, `.page-sub`, `.pill-group`, `.pill`, `.stat-row`, `.stat-tile`, `.stat-note`, `.fleet-grid`, `.sheet`, `.needs-look`, `.needs-look-list`, `.sheet-title`, `.panel`, `.leader-card*`, `.figure-row`, `.labels`, `.status-pill*`, `.mark*`, `.sparkline*`, `.long`, `.visually-hidden`); `FLEET_REFRESH_MS` and `useFleet()` from `src/app/fleet.tsx`; `leaderUrl(name, tab?)`; `describeError(error).title`.
- Produces: `formatTries(count: number): string` ("one try", "three tries", "12 tries") and `countOf(count: number, noun: string): string` ("1 leader", "3 followers") from `src/lib/format.ts`; `formatPools` now joins with ", "; `healthText(leader): { label: string; tone: Tone }`, `type Tone = "ok" | "warn" | "bad" | "muted"`, `StatusMark({ tone })` and `HealthBadge({ leader })` from `src/components/HealthBadge.tsx`; `Sparkline` drawn 400 by 64 (`WIDTH`, `HEIGHT` exported) with the new wording from `describe(g)`; `LABEL_PILL_LIMIT = 6` and `concerns(leaders: FleetLeader[])` exported from `src/pages/FleetPage.tsx`; on the page: a region named "Totals", a region named "Needs a look", one `article` per leader named by its heading, and a group named "Show leaders with the label".

The overview of the spec's section 5.2, to `docs/superpowers/design/InkConsole.dc.html`.
The table goes; each leader is an `<article>`.

What to hold on to while writing it:

- **One `h1` element from loading to loaded.** The page used to return a different tree
  while loading, which replaced the heading and dropped any focus on it. The heading is now
  rendered once and everything else is conditional beneath it. A test pins this.
- **The count of leaders is in a polite live region; the time of the last check is not.**
- **A leader that is not answering has no chart**, so `ThroughputChart` is not rendered for
  it and its history is not requested.
- **Totals are over the leaders shown** (after the label filter), over those that have
  figures at all; with none, a dash, never a zero.
- **Figures are a `<dl>`.** The short visible labels ("Last hour") carry visually hidden
  words (", finished") so each number is read with what it counts.
- **`HealthBadge` and `Sparkline` change their words here**, which also changes the status
  shown on a leader's own page. That is intended.

- [ ] **Step 1: Write the failing tests**

`FleetPage.test.tsx` is rewritten: each of the old tests has its counterpart (figures, a
leader not answering, revoked, never answered, the live regions, a failed history, the label
filter twice, a failed refresh, no leaders, a failed first load) and there are new ones for
totals, "Needs a look", the pill limit, a label nobody has, the stable heading, and the two
Review Focus cases. The old "names the table and keeps it in a focusable scroll region" has
no table to check; its place is taken by "uses no table and no style attribute" and by the
card's own structure being checked in every other test.

<!-- file: src/lib/format.test.ts | patch -->
Change `packages/console-web/src/lib/format.test.ts`:

````diff
--- a/packages/console-web/src/lib/format.test.ts
+++ b/packages/console-web/src/lib/format.test.ts
@@ -2,7 +2,9 @@
 import {
   formatCount,
   formatDuration,
+  countOf,
   formatPools,
+  formatTries,
   formatTime,
   labelPairs,
   oldestQueuedAge,
@@ -29,7 +31,7 @@
   });
 
   it("formats pools and labels", () => {
-    expect(formatPools({ gpu: 1, default: 2 })).toBe("default 2 · gpu 1");
+    expect(formatPools({ gpu: 1, default: 2 })).toBe("default 2, gpu 1");
     expect(formatPools({})).toBe("none");
     expect(labelPairs({ region: "eu", env: "prod" })).toEqual(["env=prod", "region=eu"]);
     expect(formatCount(null)).toBe("–");
@@ -52,6 +54,23 @@
     expect(formatPools({ a: Number.NaN })).toBe("a –");
   });
 
+  it("says a small number of tries in words and a large one in digits", () => {
+    expect(formatTries(1)).toBe("one try");
+    expect(formatTries(3)).toBe("three tries");
+    expect(formatTries(9)).toBe("nine tries");
+    expect(formatTries(10)).toBe("10 tries");
+    expect(formatTries(0)).toBe("no tries");
+    expect(formatTries(Number.NaN)).toBe("no tries");
+    expect(formatTries(-2)).toBe("no tries");
+    expect(formatTries(2.9)).toBe("two tries");
+  });
+
+  it("counts a noun in the singular and the plural", () => {
+    expect(countOf(1, "leader")).toBe("1 leader");
+    expect(countOf(0, "follower")).toBe("0 followers");
+    expect(countOf(1234, "follower")).toBe(`${formatCount(1234)} followers`);
+  });
+
   it("formats a recent time as a clock and an old one with its date", () => {
     const now = Date.parse("2026-10-04T12:00:00Z");
     expect(formatTime("2026-10-04T11:59:00Z", now)).toMatch(/\d/);
````

<!-- file: src/components/Sparkline.test.tsx | patch -->
Change `packages/console-web/src/components/Sparkline.test.tsx`:

````diff
--- a/packages/console-web/src/components/Sparkline.test.tsx
+++ b/packages/console-web/src/components/Sparkline.test.tsx
@@ -31,7 +31,7 @@
     expect(g.segments).toHaveLength(2);
     expect(g.downCount).toBe(1);
     expect(describe(g)).toBe(
-      "Jobs completed per hour over the last 24 hours: latest 3, highest 3. Unreachable in 1 five-minute period.",
+      "Finished per hour over the last 24 hours: latest 3, highest 3. No answer in 1 five-minute period.",
     );
   });
 
@@ -47,16 +47,16 @@
   });
 
   it("says so when there is no history", () => {
-    expect(describe(geometry([], NOW))).toBe("No throughput history yet.");
+    expect(describe(geometry([], NOW))).toBe("No history yet.");
   });
 
   it("renders an image with its description as the accessible name", () => {
     render(<Sparkline points={history()} now={NOW} />);
     const chart = screen.getByRole("img");
     expect(chart).toHaveAccessibleName(
-      /Jobs completed per hour over the last 24 hours: latest 1, highest 12\./,
+      /Finished per hour over the last 24 hours: latest 1, highest 12\./,
     );
-    expect(chart).toHaveAccessibleName(/Unreachable in 1 five-minute period\./);
+    expect(chart).toHaveAccessibleName(/No answer in 1 five-minute period\./);
   });
 
   it("handles a single point", () => {
@@ -71,9 +71,9 @@
     expect(g.segments).toHaveLength(0);
     expect(g.dots).toHaveLength(0);
     expect(g.latest).toBeNull();
-    expect(describe(g)).toBe("No throughput history yet.");
+    expect(describe(g)).toBe("No history yet.");
     render(<Sparkline points={[point(5, null)]} now={NOW} />);
-    expect(screen.getByRole("img")).toHaveAccessibleName("No throughput history yet.");
+    expect(screen.getByRole("img")).toHaveAccessibleName("No history yet.");
   });
 
   it("handles gaps at the start, middle and end", () => {
@@ -93,14 +93,14 @@
     expect(g.segments).toHaveLength(2);
     expect(g.downCount).toBe(4);
     expect(g.latest).toBe(4);
-    expect(describe(g)).toContain("Unreachable in 4 five-minute periods.");
+    expect(describe(g)).toContain("No answer in 4 five-minute periods.");
   });
 
   it("handles only unreachable buckets", () => {
     const g = geometry([point(10, null, false), point(5, null, false)], NOW);
     expect(g.segments).toHaveLength(0);
     expect(describe(g)).toBe(
-      "Jobs completed per hour over the last 24 hours: latest unknown, highest 0. Unreachable in 2 five-minute periods.",
+      "Finished per hour over the last 24 hours: latest unknown, highest 0. No answer in 2 five-minute periods.",
     );
   });
 
@@ -125,4 +125,28 @@
     expect(container.querySelector("[style]")).toBeNull();
     expect(container.querySelectorAll("rect.sparkline-down")).toHaveLength(1);
   });
+
+  it("is drawn 400 by 64 and stretched by the stylesheet, with lone points as round strokes", () => {
+    const { container } = render(<Sparkline points={[point(60, 1), point(30, 2), point(25, 2)]} now={NOW} />);
+    const svg = container.querySelector("svg");
+    expect(svg).toHaveAttribute("viewBox", "0 0 400 64");
+    expect(svg).toHaveAttribute("preserveAspectRatio", "none");
+    expect(svg).toHaveClass("sparkline");
+    // A circle would stretch into an ellipse; a zero-length round-capped stroke does not.
+    expect(container.querySelector("circle")).toBeNull();
+    expect(container.querySelectorAll("path.sparkline-dot")).toHaveLength(1);
+    expect(container.querySelector("path.sparkline-dot")?.getAttribute("d")).toMatch(/^M[\d.]+ [\d.]+h0\.01$/);
+  });
+
+  it("keeps every point inside the drawing", () => {
+    const g = geometry([point(24 * 60, 5), point(12 * 60, 9), point(0, 1)], NOW);
+    for (const d of g.segments) {
+      for (const [, x, y] of d.matchAll(/[ML]([\d.]+) ([\d.]+)/g)) {
+        expect(Number(x)).toBeGreaterThanOrEqual(0);
+        expect(Number(x)).toBeLessThanOrEqual(400);
+        expect(Number(y)).toBeGreaterThanOrEqual(0);
+        expect(Number(y)).toBeLessThanOrEqual(64);
+      }
+    }
+  });
 });
````

<!-- file: src/pages/FleetPage.test.tsx | replace -->
Replace the whole of `packages/console-web/src/pages/FleetPage.test.tsx` with:

````tsx
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setLastInputForTests } from "../app/activity";
import { FleetProvider } from "../app/fleet";
import { RouterProvider } from "../app/router";
import { fail, mockFetch, reply, type FetchMock } from "../test/fetchMock";
import { NOW, STATUS, history, leader } from "../test/fixtures";
import { FleetPage, LABEL_PILL_LIMIT, concerns } from "./FleetPage";

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

/** A leader's card: the article named by its heading. */
async function findCard(name: string): Promise<HTMLElement> {
  return screen.findByRole("article", { name });
}

function queryCard(name: string): HTMLElement | null {
  return screen.queryByRole("article", { name });
}

/** A definition list as { term: value }, the way a screen reader pairs them. */
function figures(list: HTMLElement): Record<string, string> {
  const pairs: Record<string, string> = {};
  for (const term of within(list).getAllByRole("term")) {
    pairs[term.textContent ?? ""] = term.nextElementSibling?.textContent ?? "";
  }
  return pairs;
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

  it("shows one card per leader with the overview figures", async () => {
    withHistory(mockFetch().on("GET /api/fleet", reply(200, [EU])), "eu-1");
    renderFleet();
    const card = await findCard("eu-1");
    expect(within(card).getByText("Answering")).toBeInTheDocument();
    expect(figures(card)).toEqual({
      Waiting: "3",
      "Last hour, finished": "7",
      "Last day, finished": "30",
      "Failed tries, last day": "1",
    });
    // 420 s at the snapshot, taken 10 s before NOW.
    expect(within(card).getByText(/oldest waiting 7 min/)).toHaveTextContent(
      "3 followers · default 2, gpu 1 · oldest waiting 7 min",
    );
    expect(within(card).getByRole("list", { name: "Labels" })).toHaveTextContent("env=prodregion=eu");
    expect(await within(card).findByRole("img")).toHaveAccessibleName(/^eu-1: Finished per hour/);
  });

  it("links each card's heading to the leader", async () => {
    withHistory(mockFetch().on("GET /api/fleet", reply(200, [EU])), "eu-1");
    renderFleet();
    const card = await findCard("eu-1");
    const heading = within(card).getByRole("heading", { level: 2, name: "eu-1" });
    expect(within(heading).getByRole("link", { name: "eu-1" })).toHaveAttribute("href", "/leaders/eu-1/pools");
  });

  it("shows a leader that is not answering with its last figures and keeps the others working", async () => {
    withHistory(mockFetch().on("GET /api/fleet", reply(200, [EU, US])), "eu-1", "us-1");
    renderFleet();
    const us = await findCard("us-1");
    expect(us).toHaveClass("leader-card-bad");
    expect(within(us).getByText("Not answering")).toBeInTheDocument();
    expect(within(us).getByText(/^No answer since .+, after four tries\.$/)).toBeInTheDocument();
    expect(within(us).getByText(/The last figures are from/)).toHaveTextContent(
      /: 3 waiting, 3 followers\. Recordings already claimed keep going; this console just cannot see them\.$/,
    );
    expect(within(us).getByText("Last error: connect_error")).toBeInTheDocument();
    expect(within(us).getByRole("link", { name: "See what us-1 last reported" })).toHaveAttribute(
      "href",
      "/leaders/us-1/pools",
    );
    // No chart and no fresh figures for a leader that is not answering.
    expect(within(us).queryByRole("img")).not.toBeInTheDocument();
    expect(within(us).queryByRole("term")).not.toBeInTheDocument();
    const eu = await findCard("eu-1");
    expect(within(eu).getByText("Answering")).toBeInTheDocument();
  });

  it("shows a revoked leader's card with its snapshot time", async () => {
    const revoked = leader({ name: "rev-1", health: "credential_revoked" });
    withHistory(mockFetch().on("GET /api/fleet", reply(200, [revoked])), "rev-1");
    renderFleet();
    const card = await findCard("rev-1");
    expect(within(card).getByText("Credential revoked")).toBeInTheDocument();
    expect(within(card).getByText(/The last figures are from/)).toBeInTheDocument();
    expect(within(card).getByText(/A console administrator must replace it\.$/)).toBeInTheDocument();
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
    const card = await findCard("new-1");
    expect(within(card).getByText("Not answering yet, two tries")).toBeInTheDocument();
    expect(within(card).getByText("No answer yet, after two tries.")).toBeInTheDocument();
    expect(within(card).getByText("Figures appear after the first check that works.")).toBeInTheDocument();
    expect(within(card).queryByRole("link", { name: /last reported/ })).not.toBeInTheDocument();
    // Nothing to add up: the totals show a dash, never a made-up zero.
    expect(figures(screen.getByRole("region", { name: "Totals" }))).toEqual({
      "Waiting now": "–",
      "Finished, last hour": "–",
      "Finished, last day": "–",
      "Followers at work": "–",
    });
  });

  it("shows a switched-off leader without asking for its history", async () => {
    const off = leader({ name: "off-1", health: "disabled", enabled: false });
    const mock = mockFetch().on("GET /api/fleet", reply(200, [off]));
    renderFleet();
    const card = await findCard("off-1");
    expect(within(card).getByText("Switched off")).toBeInTheDocument();
    expect(
      within(card).getByText("This leader is switched off in the console, so nothing is asked of it."),
    ).toBeInTheDocument();
    expect(mock.callsTo("GET /api/leaders/off-1/history?hours=24")).toHaveLength(0);
  });

  it("says so when a leader has no followers at work and nothing waiting", async () => {
    const idle = leader({
      summary: {
        ...(EU.summary as NonNullable<typeof EU.summary>),
        queued: 0,
        oldest_queued_age_s: null,
        followers_active_by_pool: {},
      },
    });
    withHistory(mockFetch().on("GET /api/fleet", reply(200, [idle])), "eu-1");
    renderFleet();
    const card = await findCard("eu-1");
    expect(within(card).getByText(/nothing waiting/)).toHaveTextContent(
      /^No followers at work · nothing waiting$/,
    );
    expect(figures(screen.getByRole("region", { name: "Totals" }))["Followers at work"]).toBe("0");
  });

  it("shows a health it does not know as the leader's own word, without a chart or made-up figures", async () => {
    const odd = leader({ name: "odd-1", health: "migrating" as never });
    const mock = mockFetch().on("GET /api/fleet", reply(200, [odd]));
    renderFleet();
    const card = await findCard("odd-1");
    expect(within(card).getByText("migrating")).toBeInTheDocument();
    expect(within(card).getByText("The console has no figures for this leader.")).toBeInTheDocument();
    expect(within(card).queryByRole("term")).not.toBeInTheDocument();
    expect(mock.callsTo("GET /api/leaders/odd-1/history?hours=24")).toHaveLength(0);
  });

  it("adds up the totals and says when they include old figures", async () => {
    withHistory(mockFetch().on("GET /api/fleet", reply(200, [EU, US])), "eu-1", "us-1");
    renderFleet();
    await findCard("us-1");
    const totals = screen.getByRole("region", { name: "Totals" });
    expect(figures(totals)).toEqual({
      "Waiting now": "6",
      "Finished, last hour": "14",
      "Finished, last day": "60",
      "Followers at work": "6",
    });
    expect(
      within(totals).getByText(
        "These include the last figures from us-1, which the console cannot check right now.",
      ),
    ).toBeInTheDocument();
  });

  it("lists what needs a look: scan errors, failed tries and a revoked credential", async () => {
    const revoked = leader({ name: "rev-1", health: "credential_revoked" });
    withHistory(mockFetch().on("GET /api/fleet", reply(200, [EU, revoked])), "eu-1", "rev-1");
    renderFleet();
    const section = await screen.findByRole("region", { name: "Needs a look" });
    const items = within(section)
      .getAllByRole("listitem")
      .map((item) => item.textContent);
    expect(items).toEqual([
      "eu-1 could not scan archive. the root folder is not readable",
      "rev-1 revoked this console's credential. A console administrator must replace it.",
      "rev-1 could not scan archive. the root folder is not readable",
      "2 tries failed in the last day. 1 on eu-1, 1 on rev-1.",
    ]);
    expect(within(section).getAllByRole("link", { name: "eu-1 could not scan archive." })[0]).toHaveAttribute(
      "href",
      "/leaders/eu-1/locations",
    );
    // It comes before the leaders' cards, so it is never below the fold.
    const first = section.parentElement?.firstElementChild;
    expect(first).toBe(section);
  });

  it("shows no Needs a look section when nothing does", async () => {
    const calm = leader({
      summary: { ...(EU.summary as NonNullable<typeof EU.summary>), failed_attempts_last_day: 0, scan_errors: [] },
    });
    withHistory(mockFetch().on("GET /api/fleet", reply(200, [calm])), "eu-1");
    renderFleet();
    await findCard("eu-1");
    expect(screen.queryByRole("region", { name: "Needs a look" })).not.toBeInTheDocument();
    expect(concerns([calm])).toEqual([]);
  });

  it("keeps the time of the last check out of every live region", async () => {
    withHistory(mockFetch().on("GET /api/fleet", reply(200, [EU])), "eu-1");
    renderFleet();
    const checked = await screen.findByText(/Checked at .+, and every 10 s/);
    expect(checked.closest("[aria-live], [role='status'], [role='alert'], [role='log']")).toBeNull();
    // The count stays in a polite live region: it changes when the filter does.
    expect(screen.getByText("1 leader").closest("[role='status'], [aria-live='polite']")).not.toBeNull();
  });

  it("keeps the cards when one leader's history request fails", async () => {
    const usUp = leader({ name: "us-1", labels: { env: "prod", region: "us" } });
    const mock = mockFetch().on("GET /api/fleet", reply(200, [EU, usUp]));
    mock.on("GET /api/leaders/eu-1/history?hours=24", reply(200, history()));
    mock.on("GET /api/leaders/us-1/history?hours=24", fail(502, "leader_unreachable", "down"));
    renderFleet();
    const us = await findCard("us-1");
    expect(await within(us).findByText("No history to show")).toBeInTheDocument();
    // The card keeps its figures; only its chart is missing.
    expect(figures(us).Waiting).toBe("3");
    const eu = await findCard("eu-1");
    expect(await within(eu).findByRole("img")).toHaveAccessibleName(/eu-1/);
  });

  it("filters by label with pills and keeps the filter in the address", async () => {
    vi.useRealTimers();
    withHistory(mockFetch().on("GET /api/fleet", reply(200, [EU, US])), "eu-1", "us-1");
    renderFleet();
    await findCard("us-1");
    const pills = screen.getByRole("group", { name: "Show leaders with the label" });
    expect(within(pills).getByRole("button", { name: "All leaders" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(within(pills).getByRole("button", { name: "region = us" }));
    expect(queryCard("eu-1")).not.toBeInTheDocument();
    expect(queryCard("us-1")).toBeInTheDocument();
    expect(window.location.search).toBe("?label=region%3Dus");
    expect(screen.getByText(/1 of 2 leaders/)).toBeInTheDocument();
    expect(within(pills).getByRole("button", { name: "region = us" })).toHaveAttribute("aria-pressed", "true");
    expect(within(pills).getByRole("button", { name: "All leaders" })).toHaveAttribute("aria-pressed", "false");
    // The totals follow the filter.
    expect(figures(screen.getByRole("region", { name: "Totals" }))["Waiting now"]).toBe("3");
    // Pressing the chosen pill again clears the filter.
    await userEvent.click(within(pills).getByRole("button", { name: "region = us" }));
    expect(window.location.search).toBe("");
    expect(queryCard("eu-1")).toBeInTheDocument();
  });

  it("filters by a label whose value holds an equals sign", async () => {
    vi.useRealTimers();
    const odd = leader({ name: "odd-1", labels: { team: "a=b" } });
    withHistory(mockFetch().on("GET /api/fleet", reply(200, [EU, odd])), "eu-1", "odd-1");
    renderFleet();
    await findCard("odd-1");
    await userEvent.click(screen.getByRole("button", { name: "team = a=b" }));
    expect(queryCard("odd-1")).toBeInTheDocument();
    expect(queryCard("eu-1")).not.toBeInTheDocument();
    expect(window.location.search).toBe("?label=team%3Da%3Db");
  });

  it("offers a select instead of pills when there are many labels", async () => {
    vi.useRealTimers();
    const labels = Object.fromEntries(
      Array.from({ length: LABEL_PILL_LIMIT + 1 }, (_, i) => [`k${i}`, "v"]),
    );
    const many = leader({ name: "many-1", labels });
    withHistory(mockFetch().on("GET /api/fleet", reply(200, [EU, many])), "eu-1", "many-1");
    renderFleet();
    await findCard("many-1");
    expect(screen.queryByRole("group", { name: "Show leaders with the label" })).not.toBeInTheDocument();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Label" }), "k0=v");
    expect(queryCard("eu-1")).not.toBeInTheDocument();
    expect(window.location.search).toBe("?label=k0%3Dv");
  });

  it("keeps a label from the address that no leader has, so it can be cleared", async () => {
    vi.useRealTimers();
    window.history.replaceState(null, "", "/?label=region%3Dmoon");
    withHistory(mockFetch().on("GET /api/fleet", reply(200, [EU])), "eu-1");
    renderFleet();
    expect(await screen.findByText("No leader has the label region=moon.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "region = moon" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: "All leaders" }));
    expect(await findCard("eu-1")).toBeInTheDocument();
  });

  it("keeps the last figures and says so when a check fails", async () => {
    vi.useRealTimers();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    // The real clock is long past the fixed NOW; without this the person counts as idle
    // and polling is paused (usePoll skips refreshes while idle).
    setLastInputForTests(Date.now());
    let n = 0;
    withHistory(
      mockFetch().on("GET /api/fleet", () =>
        ++n === 1 ? reply(200, [EU]) : fail(503, "unavailable", "down"),
      ),
      "eu-1",
    );
    renderFleet();
    expect(await findCard("eu-1")).toBeInTheDocument();
    await act(() => vi.advanceTimersByTimeAsync(10_000));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /^The last check did not work: .+ These are the last figures\.$/,
    );
    expect(queryCard("eu-1")).toBeInTheDocument();
  });

  it("says when the person sees no leaders", async () => {
    mockFetch().on("GET /api/fleet", reply(200, []));
    renderFleet();
    expect(await screen.findByText(/You have no role on any leader yet/)).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Totals" })).not.toBeInTheDocument();
  });

  it("shows the error with a retry when the first load fails", async () => {
    mockFetch().on("GET /api/fleet", fail(503, "unavailable", "service temporarily unavailable"));
    renderFleet();
    expect(await screen.findByRole("alert")).toHaveTextContent("temporarily unavailable");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("keeps one heading element from loading to loaded, so focus on it is not lost", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    withHistory(
      mockFetch().on("GET /api/fleet", async () => {
        await gate;
        return reply(200, [EU]);
      }),
      "eu-1",
    );
    renderFleet();
    const heading = await screen.findByRole("heading", { level: 1, name: "Fleet" });
    expect(screen.getByRole("status")).toHaveTextContent("Loading the fleet…");
    heading.tabIndex = -1;
    heading.focus();
    release();
    await findCard("eu-1");
    expect(screen.getByRole("heading", { level: 1, name: "Fleet" })).toBe(heading);
    expect(heading).toHaveFocus();
  });

  it("uses no table and no style attribute", async () => {
    withHistory(mockFetch().on("GET /api/fleet", reply(200, [EU, US])), "eu-1", "us-1");
    const { container } = renderFleet();
    await findCard("us-1");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(container.querySelector("[style]")).toBeNull();
  });
});
````

- [ ] **Step 2: Run the tests to see them fail**

Run: `npx vitest run src/lib/format.test.ts src/components/Sparkline.test.tsx src/pages/FleetPage.test.tsx`

Expected: FAIL in all three files (`Test Files  3 failed (3)`). In `format.test.ts`:
`expected 'default 2 · gpu 1' to be 'default 2, gpu 1'`, `TypeError: formatTries is not a
function`, `TypeError: countOf is not a function`. In `Sparkline.test.tsx`: the old wording
against the new (`expected 'No throughput history yet.' to be 'No history yet.'`) and the new
drawing test. In `FleetPage.test.tsx` nearly every test fails: there is no `article` to find,
the page is still a table, and `concerns` is not exported.

- [ ] **Step 3: Write the implementation**

Order: `format.ts`, then `HealthBadge.tsx`, `Sparkline.tsx` and `ThroughputChart.tsx`, then
`FleetPage.tsx`.

<!-- file: src/components/HealthBadge.tsx | replace -->
Replace the whole of `packages/console-web/src/components/HealthBadge.tsx` with:

````tsx
import type { FleetLeader } from "../api/types";
import { formatTries } from "../lib/format";

export type Tone = "ok" | "warn" | "bad" | "muted";

type HealthOf = Pick<FleetLeader, "health" | "consecutive_failures">;

/** A leader's health in the console's words, and how loudly to show it. */
export function healthText(leader: HealthOf): { label: string; tone: Tone } {
  const failures = leader.consecutive_failures;
  switch (leader.health) {
    case "reachable":
      return failures > 0
        ? {
            label: `Answering, ${failures === 1 ? "1 missed check" : `${failures} missed checks`}`,
            tone: "warn",
          }
        : { label: "Answering", tone: "ok" };
    case "pending":
      return failures > 0
        ? { label: `Not answering yet, ${formatTries(failures)}`, tone: "warn" }
        : { label: "Waiting for the first check", tone: "muted" };
    case "unreachable":
      return { label: "Not answering", tone: "bad" };
    case "credential_revoked":
      return { label: "Credential revoked", tone: "bad" };
    case "disabled":
      return { label: "Switched off", tone: "muted" };
    default:
      return { label: String(leader.health), tone: "muted" };
  }
}

/** The mark beside a status: a hexagon while the leader answers, a ring when it does not. */
export function StatusMark({ tone }: { tone: Tone }) {
  if (tone === "ok") return <span className="mark mark-hex" aria-hidden="true" />;
  if (tone === "warn") return <span className="mark mark-hex mark-quiet" aria-hidden="true" />;
  return (
    <span className={tone === "bad" ? "mark mark-ring mark-bad" : "mark mark-ring"} aria-hidden="true" />
  );
}

/** Health as a shape and words in a pill: colour is never the only signal. */
export function HealthBadge({ leader }: { leader: HealthOf }) {
  const { label, tone } = healthText(leader);
  return (
    <span className={`status-pill status-pill-${tone}`}>
      <StatusMark tone={tone} />
      {label}
    </span>
  );
}
````

<!-- file: src/components/Sparkline.tsx | patch -->
Change `packages/console-web/src/components/Sparkline.tsx`:

````diff
--- a/packages/console-web/src/components/Sparkline.tsx
+++ b/packages/console-web/src/components/Sparkline.tsx
@@ -2,14 +2,16 @@
 import type { HistoryPoint } from "../api/types";
 import { formatCount } from "../lib/format";
 
-// Hand-written SVG (no chart library: the CSP forbids injected <style>). Plots
+// Hand-written SVG (no chart library: the CSP forbids injected <style>). The drawing is
+// 400 by 64 units and is stretched to the width of its card (styles/fleet.css); strokes do
+// not scale, so the line keeps its weight at any width. Plots
 // completed_last_hour from each 5-minute history bucket: each point is how many jobs the
 // leader completed in the hour before that snapshot, so the line is the leader's rolling
 // hourly throughput. The line breaks where a bucket is missing or the leader was
 // unreachable; unreachable buckets are also marked along the bottom edge.
 
-export const WIDTH = 160;
-export const HEIGHT = 36;
+export const WIDTH = 400;
+export const HEIGHT = 64;
 const PAD = 3;
 const WINDOW_MS = 24 * 60 * 60 * 1000;
 const GAP_MS = 10 * 60 * 1000; // more than one missing 5-minute bucket breaks the line
@@ -75,17 +77,17 @@
 }
 
 export function describe(g: Geometry): string {
-  if (g.latest === null && g.downCount === 0) return "No throughput history yet.";
+  if (g.latest === null && g.downCount === 0) return "No history yet.";
   const parts = [
-    `Jobs completed per hour over the last 24 hours: latest ${g.latest === null ? "unknown" : formatCount(g.latest)}, highest ${formatCount(g.peak)}.`,
+    `Finished per hour over the last 24 hours: latest ${g.latest === null ? "unknown" : formatCount(g.latest)}, highest ${formatCount(g.peak)}.`,
   ];
   if (g.downCount > 0) {
-    parts.push(`Unreachable in ${g.downCount} five-minute ${g.downCount === 1 ? "period" : "periods"}.`);
+    parts.push(`No answer in ${g.downCount} five-minute ${g.downCount === 1 ? "period" : "periods"}.`);
   }
   return parts.join(" ");
 }
 
-/** `name` (the leader) leads the accessible name, so each chart in a table is told apart. */
+/** `name` (the leader) leads the accessible name, so each card's chart is told apart. */
 export function Sparkline({ points, now, name }: { points: HistoryPoint[]; now: number; name?: string }) {
   const titleId = useId();
   const g = geometry(points, now);
@@ -105,7 +107,7 @@
         <path key={d} className="sparkline-line" d={d} />
       ))}
       {g.dots.map((p) => (
-        <circle key={`${p.x},${p.y}`} className="sparkline-dot" cx={p.x} cy={p.y} r={1.5} />
+        <path key={`${p.x},${p.y}`} className="sparkline-dot" d={`M${p.x} ${p.y}h0.01`} />
       ))}
       {g.down.map((dx) => (
         <rect key={dx} className="sparkline-down" x={dx - 1.5} y={HEIGHT - PAD} width={3} height={PAD} />
````

<!-- file: src/components/ThroughputChart.tsx | replace -->
Replace the whole of `packages/console-web/src/components/ThroughputChart.tsx` with:

````tsx
import { useCallback } from "react";
import { api, leaderPath } from "../api/client";
import type { HistoryPoint } from "../api/types";
import { usePoll } from "../app/usePoll";
import { Sparkline } from "./Sparkline";

/** History moves in 5-minute buckets, so it is read every 5 minutes. */
export const HISTORY_REFRESH_MS = 5 * 60 * 1000;

/**
 * One leader's 24-hour throughput, from GET /api/leaders/{name}/history?hours=24. A failed
 * request only affects this chart: the card and the page stay.
 */
export function ThroughputChart({ name, now }: { name: string; now: number }) {
  const load = useCallback(
    (signal: AbortSignal) => api.get<HistoryPoint[]>(leaderPath(name, "history?hours=24"), signal),
    [name],
  );
  const { data, error } = usePoll(load, HISTORY_REFRESH_MS, `history:${name}`);
  if (data === undefined) {
    return <span className="muted">{error ? "No history to show" : "Loading…"}</span>;
  }
  return <Sparkline points={data} now={now} name={name} />;
}
````

<!-- file: src/lib/format.ts | patch -->
Change `packages/console-web/src/lib/format.ts`:

````diff
--- a/packages/console-web/src/lib/format.ts
+++ b/packages/console-web/src/lib/format.ts
@@ -44,10 +44,10 @@
   return ageAtSnapshot + since;
 }
 
-/** "default 2 · gpu 1", pools sorted by name; "none" when empty. */
+/** "default 2, gpu 1", pools sorted by name; "none" when empty. */
 export function formatPools(byPool: Record<string, number>): string {
   const entries = Object.entries(byPool).sort(([a], [b]) => a.localeCompare(b));
-  return entries.length === 0 ? "none" : entries.map(([pool, n]) => `${pool} ${formatCount(n)}`).join(" · ");
+  return entries.length === 0 ? "none" : entries.map(([pool, n]) => `${pool} ${formatCount(n)}`).join(", ");
 }
 
 /** Labels as "key=value" strings, sorted. */
@@ -56,3 +56,16 @@
     .sort(([a], [b]) => a.localeCompare(b))
     .map(([key, value]) => `${key}=${value}`);
 }
+
+const SMALL_NUMBERS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"];
+
+/** "one try", "three tries", "12 tries": small counts as words, the way a person says them. */
+export function formatTries(count: number): string {
+  const n = Number.isFinite(count) ? Math.max(0, Math.floor(count)) : 0;
+  return `${SMALL_NUMBERS[n] ?? formatCount(n)} ${n === 1 ? "try" : "tries"}`;
+}
+
+/** "1 leader", "3 followers": a count with its noun (the plural adds an s). */
+export function countOf(count: number, noun: string): string {
+  return `${formatCount(count)} ${count === 1 ? noun : `${noun}s`}`;
+}
````

<!-- file: src/pages/FleetPage.tsx | replace -->
Replace the whole of `packages/console-web/src/pages/FleetPage.tsx` with:

````tsx
import { useId } from "react";
import { describeError } from "../api/errors";
import type { FleetLeader } from "../api/types";
import { FLEET_REFRESH_MS, useFleet } from "../app/fleet";
import { Link, useNavigate, useSearchParam } from "../app/router";
import { useNow } from "../app/useNow";
import { usePageTitle } from "../app/usePageTitle";
import { ErrorPanel } from "../components/ErrorPanel";
import { HealthBadge, healthText } from "../components/HealthBadge";
import { ThroughputChart } from "../components/ThroughputChart";
import {
  countOf,
  formatCount,
  formatDuration,
  formatPools,
  formatTime,
  formatTries,
  labelPairs,
  oldestQueuedAge,
} from "../lib/format";
import { leaderUrl } from "./leader/tabs";

/** With more label pairs than this the pills would fill the page: a select takes over. */
export const LABEL_PILL_LIMIT = 6;

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
  const choose = (next: string) =>
    navigate(next ? `/?label=${encodeURIComponent(next)}` : "/", { replace: true });
  if (pairs.length === 0) return null;
  if (pairs.length > LABEL_PILL_LIMIT) {
    return (
      <label className="field-inline">
        Label
        <select value={value ?? ""} onChange={(event) => choose(event.target.value)}>
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
  return (
    <div className="pill-group" role="group" aria-label="Show leaders with the label">
      <button type="button" className="pill" aria-pressed={!value} onClick={() => choose("")}>
        All leaders
      </button>
      {pairs.map((pair) => (
        <button
          key={pair}
          type="button"
          className="pill"
          aria-pressed={value === pair}
          onClick={() => choose(value === pair ? "" : pair)}
        >
          {pair.replace("=", " = ")}
        </button>
      ))}
    </div>
  );
}

function followersAtWork(leader: FleetLeader): number {
  return Object.values(leader.summary?.followers_active_by_pool ?? {}).reduce((sum, n) => sum + n, 0);
}

/** "a", "a and b", "a, b and c". */
function listOf(names: string[]): string {
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} and ${names.at(-1) ?? ""}`;
}

function Totals({ leaders }: { leaders: FleetLeader[] }) {
  const known = leaders.filter((leader) => leader.summary !== null);
  const sum = (pick: (leader: FleetLeader) => number): number | null =>
    known.length === 0 ? null : known.reduce((total, leader) => total + pick(leader), 0);
  const old = known.filter((leader) => leader.health !== "reachable").map((leader) => leader.name);
  return (
    <section aria-label="Totals">
      <dl className="stat-row">
        <div className="stat-tile">
          <dt>Waiting now</dt>
          <dd>{formatCount(sum((leader) => leader.summary?.queued ?? 0))}</dd>
        </div>
        <div className="stat-tile">
          <dt>Finished, last hour</dt>
          <dd>{formatCount(sum((leader) => leader.summary?.completed_last_hour ?? 0))}</dd>
        </div>
        <div className="stat-tile">
          <dt>Finished, last day</dt>
          <dd>{formatCount(sum((leader) => leader.summary?.completed_last_day ?? 0))}</dd>
        </div>
        <div className="stat-tile">
          <dt>Followers at work</dt>
          <dd>{formatCount(sum(followersAtWork))}</dd>
        </div>
      </dl>
      {old.length > 0 && (
        <p className="stat-note">
          These include the last figures from {listOf(old)}, which the console cannot check right now.
        </p>
      )}
    </section>
  );
}

interface Concern {
  key: string;
  lead: string;
  rest: string;
  /** Where the lead links to, when one place explains it. */
  to?: string;
}

/** What a person should look at: scan errors, a revoked credential, failed tries. */
export function concerns(leaders: FleetLeader[]): Concern[] {
  const found: Concern[] = [];
  for (const leader of leaders) {
    if (leader.health === "credential_revoked") {
      found.push({
        key: `${leader.name}/revoked`,
        lead: `${leader.name} revoked this console's credential.`,
        rest: "A console administrator must replace it.",
      });
    }
    for (const item of leader.summary?.scan_errors ?? []) {
      found.push({
        key: `${leader.name}/scan/${item.location}`,
        lead: `${leader.name} could not scan ${item.location}.`,
        rest: item.error,
        to: leaderUrl(leader.name, "locations"),
      });
    }
  }
  const failing = leaders.filter((leader) => (leader.summary?.failed_attempts_last_day ?? 0) > 0);
  const failed = failing.reduce((sum, leader) => sum + (leader.summary?.failed_attempts_last_day ?? 0), 0);
  if (failed > 0) {
    found.push({
      key: "failed",
      lead: `${formatCount(failed)} ${failed === 1 ? "try" : "tries"} failed in the last day.`,
      rest: `${failing
        .map((leader) => `${formatCount(leader.summary?.failed_attempts_last_day)} on ${leader.name}`)
        .join(", ")}.`,
    });
  }
  return found;
}

function NeedsALook({ leaders }: { leaders: FleetLeader[] }) {
  const headingId = useId();
  const items = concerns(leaders);
  if (items.length === 0) return null;
  return (
    <section className="sheet needs-look" aria-labelledby={headingId}>
      <h2 id={headingId} className="sheet-title">
        Needs a look
      </h2>
      <ul className="needs-look-list">
        {items.map((item) => (
          <li key={item.key}>
            <strong>{item.to === undefined ? item.lead : <Link to={item.to}>{item.lead}</Link>}</strong>{" "}
            <span className="muted long">{item.rest}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function lastFigures(leader: FleetLeader, now: number): string | null {
  if (leader.summary === null || leader.snapshot === null) return null;
  return (
    `The last figures are from ${formatTime(leader.snapshot.taken_at, now)}: ` +
    `${formatCount(leader.summary.queued)} waiting, ${countOf(followersAtWork(leader), "follower")}.`
  );
}

/** A leader the console has no fresh figures for: what happened, and what is still known. */
function QuietLeader({ leader, now }: { leader: FleetLeader; now: number }) {
  const tries = formatTries(leader.consecutive_failures);
  const figures = lastFigures(leader, now);
  let what: string;
  let more: string | null = null;
  switch (leader.health) {
    case "unreachable":
      what =
        leader.last_success_at === null
          ? `No answer yet, after ${tries}.`
          : `No answer since ${formatTime(leader.last_success_at, now)}, after ${tries}.`;
      more = "Recordings already claimed keep going; this console just cannot see them.";
      break;
    case "credential_revoked":
      what = "This leader revoked the console's credential. A console administrator must replace it.";
      more = "The leader itself keeps working; this console just cannot see it.";
      break;
    case "disabled":
      what = "This leader is switched off in the console, so nothing is asked of it.";
      break;
    case "pending":
      what =
        leader.consecutive_failures > 0
          ? `No answer yet, after ${tries}.`
          : "The first check has not come back yet.";
      break;
    default:
      what = "The console has no figures for this leader.";
  }
  return (
    <>
      <p className="leader-card-what">{what}</p>
      {figures === null ? (
        <p className="leader-card-more">Figures appear after the first check that works.</p>
      ) : (
        <p className="leader-card-more">
          {figures}
          {more !== null && ` ${more}`}
        </p>
      )}
      {leader.health === "unreachable" && leader.last_error && (
        <p className="leader-card-more long">Last error: {leader.last_error}</p>
      )}
      {leader.snapshot !== null && (
        <p className="leader-card-link">
          <Link to={leaderUrl(leader.name)}>See what {leader.name} last reported</Link>
        </p>
      )}
    </>
  );
}

function LeaderFigures({ leader, now }: { leader: FleetLeader; now: number }) {
  const s = leader.summary;
  if (s === null) return null;
  const age = oldestQueuedAge(s.oldest_queued_age_s, leader.snapshot?.taken_at ?? null, now);
  const followers = followersAtWork(leader);
  return (
    <>
      <div className="leader-card-chart">
        <ThroughputChart name={leader.name} now={now} />
      </div>
      <dl className="figure-row">
        <div>
          <dt>Waiting</dt>
          <dd>{formatCount(s.queued)}</dd>
        </div>
        <div>
          <dt>
            Last hour<span className="visually-hidden">, finished</span>
          </dt>
          <dd>{formatCount(s.completed_last_hour)}</dd>
        </div>
        <div>
          <dt>
            Last day<span className="visually-hidden">, finished</span>
          </dt>
          <dd>{formatCount(s.completed_last_day)}</dd>
        </div>
        <div>
          <dt>
            Failed<span className="visually-hidden"> tries, last day</span>
          </dt>
          <dd>{formatCount(s.failed_attempts_last_day)}</dd>
        </div>
      </dl>
      <p className="leader-card-foot">
        {followers === 0 ? (
          <strong>No followers at work</strong>
        ) : (
          <>
            <strong>{countOf(followers, "follower")}</strong> · {formatPools(s.followers_active_by_pool)}
          </>
        )}{" "}
        · {age === null ? "nothing waiting" : `oldest waiting ${formatDuration(age)}`}
      </p>
    </>
  );
}

function LeaderCard({ leader, now }: { leader: FleetLeader; now: number }) {
  const headingId = useId();
  const { tone } = healthText(leader);
  const fresh = leader.health === "reachable" && leader.summary !== null;
  const pairs = labelPairs(leader.labels);
  return (
    <article className={`panel leader-card leader-card-${tone}`} aria-labelledby={headingId}>
      <div className="leader-card-head">
        <div className="leader-card-title">
          <h2 id={headingId} className="leader-card-name">
            <Link to={leaderUrl(leader.name)}>{leader.name}</Link>
          </h2>
          {pairs.length > 0 && (
            <ul className="labels" aria-label="Labels">
              {pairs.map((pair) => (
                <li key={pair}>{pair}</li>
              ))}
            </ul>
          )}
        </div>
        <HealthBadge leader={leader} />
      </div>
      {fresh ? <LeaderFigures leader={leader} now={now} /> : <QuietLeader leader={leader} now={now} />}
    </article>
  );
}

export function FleetPage() {
  usePageTitle("Fleet");
  const { data, error, updatedAt, refresh } = useFleet();
  const label = useSearchParam("label");
  const now = useNow(5_000);

  // One heading element for the loading and the loaded page: focus put on it after a
  // navigation survives the fleet arriving.
  const shown = (data ?? []).filter((leader) => matchesLabel(leader, label));
  return (
    <>
      <div className="page-head fleet-head">
        <div>
          <h1>Fleet</h1>
          {data !== undefined && (
            <p className="page-sub">
              <span role="status">
                {shown.length === data.length
                  ? countOf(data.length, "leader")
                  : `${shown.length} of ${data.length} leaders`}
              </span>
              {updatedAt !== null && (
                <span>
                  {" "}
                  · Checked at {formatTime(new Date(updatedAt).toISOString(), now)}, and every{" "}
                  {FLEET_REFRESH_MS / 1000} s
                </span>
              )}
            </p>
          )}
        </div>
        {data !== undefined && <LabelFilter leaders={data} value={label} />}
      </div>
      {data === undefined ? (
        error ? (
          <ErrorPanel error={error} onRetry={refresh} />
        ) : (
          <p role="status">Loading the fleet…</p>
        )
      ) : (
        <>
          {error !== null && (
            <p className="notice" role="alert">
              The last check did not work: {describeError(error).title} These are the last figures.
            </p>
          )}
          {data.length === 0 ? (
            <p>You have no role on any leader yet. Ask a console administrator to give you one.</p>
          ) : shown.length === 0 ? (
            <p>No leader has the label {label}.</p>
          ) : (
            <>
              <Totals leaders={shown} />
              <div className="fleet-grid">
                <NeedsALook leaders={shown} />
                {shown.map((leader) => (
                  <LeaderCard key={leader.name} leader={leader} now={now} />
                ))}
              </div>
            </>
          )}
        </>
      )}
    </>
  );
}
````

- [ ] **Step 4: Run the unit tests to see them pass**

Run: `npm test`

Expected: PASS. **380 tests pass** (366 + 2 in `format.test.ts` + 2 in `Sparkline.test.tsx` +
10 in `FleetPage.test.tsx`).

- [ ] **Step 5: Update the end-to-end tests**

`overview.spec.ts` is rewritten for cards: the same checks as before (each leader's figures
and chart, the label filter, a leader going down while the other stays up, the layout at
tablet width, the keyboard, no broken words and no sideways scroll on every page) on the new
structure, plus the totals and "Needs a look", the cards two across at 768px and stacked at
390px, and the long-name case. The broken-word walk now also covers cards, the rail and the
page header.

In `admin.spec.ts` and `sign-in.spec.ts` a leader on the overview is an `article`, not a row
header. `a11y.spec.ts` gains a scan of the overview filtered and with a leader down.

<!-- file: e2e/tests/a11y.spec.ts | patch -->
Change `packages/console-web/e2e/tests/a11y.spec.ts`:

````diff
--- a/packages/console-web/e2e/tests/a11y.spec.ts
+++ b/packages/console-web/e2e/tests/a11y.spec.ts
@@ -1,4 +1,4 @@
-import { THEMES, expect, expectAccessible, signIn, test } from "./support";
+import { THEMES, expect, expectAccessible, setLeaderMode, signIn, test } from "./support";
 
 // Every page the app has, in both themes, with zero axe violations (WCAG 2.1 A and AA).
 const SIGNED_IN_PAGES: { path: string; heading: string }[] = [
@@ -32,6 +32,22 @@
         await expect(page.getByText(/^Loading/)).toHaveCount(0);
         await expectAccessible(page, `${path} (${theme})`);
       }
+    });
+
+    test("the fleet overview has no accessibility violations in any of its states", async ({
+      page,
+      request,
+    }) => {
+      await signIn(page, "admin");
+      await expect(page.getByRole("article", { name: "eu-1" }).getByRole("img")).toBeVisible();
+      await page.getByRole("button", { name: "region = us" }).click();
+      await expectAccessible(page, `fleet, filtered (${theme})`);
+      await page.getByRole("button", { name: "All leaders" }).click();
+      await setLeaderMode(request, "us-1", "down");
+      await expect(
+        page.getByRole("article", { name: "us-1" }).getByText("Not answering", { exact: true }),
+      ).toBeVisible({ timeout: 30_000 });
+      await expectAccessible(page, `fleet, one leader not answering (${theme})`);
     });
 
     test("the join token dialogs have no accessibility violations", async ({ page }) => {
````

<!-- file: e2e/tests/admin.spec.ts | patch -->
Change `packages/console-web/e2e/tests/admin.spec.ts`:

````diff
--- a/packages/console-web/e2e/tests/admin.spec.ts
+++ b/packages/console-web/e2e/tests/admin.spec.ts
@@ -60,7 +60,10 @@
   // The new leader has no fake behind it: it shows in the fleet (after the next 10 s
   // refresh) without figures, and the rest of the page is unaffected.
   await page.getByRole("navigation", { name: "Console" }).getByRole("link", { name: "Fleet" }).click();
-  await expect(page.getByRole("rowheader", { name: /ap-1/ })).toBeVisible({ timeout: 20_000 });
+  await expect(page.getByRole("article", { name: "ap-1" })).toBeVisible({ timeout: 20_000 });
+  await expect(page.getByRole("article", { name: "ap-1" })).toContainText(
+    "Figures appear after the first check that works.",
+  );
 
   await page.getByRole("link", { name: "Administration" }).click();
   await page.getByRole("button", { name: "Remove ap-1" }).click();
````

<!-- file: e2e/tests/overview.spec.ts | replace -->
Replace the whole of `packages/console-web/e2e/tests/overview.spec.ts` with:

````ts
import type { Page } from "@playwright/test";
import { expect, setLeaderMode, signIn, test } from "./support";

/** How far the page is wider than the window: more than 0 means it scrolls sideways. */
function sidewaysOverflow(page: Page): Promise<number> {
  return page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
}

/**
 * Words that wrap in the middle, in every table cell and every card on the page. Text that
 * opts in to breaking anywhere (.long: a path, an address, an id, a leader's own error text)
 * is the only text allowed to.
 */
function brokenWords(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const broken: string[] = [];
    const roots = document.querySelectorAll("th, td, .leader-card, .stat-tile, .needs-look, .rail, .page-head");
    for (const root of roots) {
      const cell = root.matches("th, td");
      if (cell && (root.classList.contains("long") || root.querySelector(".long"))) continue;
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      for (let node = walker.nextNode(); node !== null; node = walker.nextNode()) {
        if (node.parentElement?.closest(".long, .visually-hidden")) continue;
        const text = node.textContent ?? "";
        for (const match of text.matchAll(/\S+/g)) {
          const range = document.createRange();
          range.setStart(node, match.index);
          range.setEnd(node, match.index + match[0].length);
          // A word that wraps in the middle has client rects on more than one line.
          const tops = new Set(Array.from(range.getClientRects()).map((r) => Math.round(r.top)));
          if (tops.size > 1) broken.push(`${root.tagName} "${match[0]}"`);
        }
      }
    }
    for (const head of document.querySelectorAll("thead th")) {
      const wraps = getComputedStyle(head).whiteSpace;
      if (wraps !== "nowrap") broken.push(`header ${head.textContent} is ${wraps}`);
    }
    return broken;
  });
}

test("the overview shows each leader's figures and 24-hour chart", async ({ page }) => {
  await signIn(page, "viewer");
  const eu = page.getByRole("article", { name: "eu-1" });
  await expect(eu.getByText("Answering", { exact: true })).toBeVisible();
  // The fake eu-1: two waiting jobs, 7 finished in the hour, 30 in the day, one failure.
  await expect(eu.getByRole("term")).toHaveText([
    "Waiting",
    "Last hour, finished",
    "Last day, finished",
    "Failed tries, last day",
  ]);
  await expect(eu.getByRole("definition")).toHaveText(["2", "7", "30", "1"]);
  await expect(eu.getByText(/oldest waiting/)).toHaveText(
    /^2 followers · default 1, gpu 1 · oldest waiting 1\d min$/,
  );
  await expect(eu.getByRole("list", { name: "Labels" }).getByRole("listitem")).toHaveText([
    "env=prod",
    "region=eu",
  ]);
  await expect(eu.getByRole("img")).toHaveAccessibleName(
    /^eu-1: Finished per hour over the last 24 hours/,
  );
  const us = page.getByRole("article", { name: "us-1" });
  await expect(us.getByRole("img")).toHaveAccessibleName(/No answer in 4 five-minute periods/);
});

test("the overview adds up the fleet and lists what needs a look", async ({ page }) => {
  await signIn(page, "viewer");
  const totals = page.getByRole("region", { name: "Totals" });
  await expect(totals.getByRole("term")).toHaveText([
    "Waiting now",
    "Finished, last hour",
    "Finished, last day",
    "Followers at work",
  ]);
  await expect(totals.getByRole("definition")).toHaveText(["4", "10", "42", "4"]);
  const look = page.getByRole("region", { name: "Needs a look" });
  await expect(look.getByRole("heading", { level: 2, name: "Needs a look" })).toBeVisible();
  await expect(look.getByRole("listitem")).toHaveText([
    "eu-1 could not scan archive. the root folder is not readable",
    "2 tries failed in the last day. 1 on eu-1, 1 on us-1.",
  ]);
  await look.getByRole("link", { name: "eu-1 could not scan archive." }).click();
  await expect(page).toHaveURL("/leaders/eu-1/locations");
});

test("the label filter narrows the cards", async ({ page }) => {
  await signIn(page, "viewer");
  const pills = page.getByRole("group", { name: "Show leaders with the label" });
  await expect(pills.getByRole("button", { name: "All leaders" })).toHaveAttribute("aria-pressed", "true");
  await pills.getByRole("button", { name: "region = us" }).click();
  await expect(page.getByRole("article", { name: "us-1" })).toBeVisible();
  await expect(page.getByRole("article", { name: "eu-1" })).toHaveCount(0);
  await expect(page).toHaveURL("/?label=region%3Dus");
  await expect(pills.getByRole("button", { name: "region = us" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText("1 of 2 leaders")).toBeVisible();
});

test("a leader that stops answering is shown not answering while the other keeps answering", async ({
  page,
  request,
}) => {
  await signIn(page, "viewer");
  await setLeaderMode(request, "us-1", "down");
  const us = page.getByRole("article", { name: "us-1" });
  await expect(us.getByText("Not answering", { exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(us.getByText(/^No answer since .+, after \w+ tries\.$/)).toBeVisible();
  await expect(us.getByText(/The last figures are from/)).toBeVisible();
  await expect(us.getByRole("link", { name: "See what us-1 last reported" })).toBeVisible();
  await expect(
    page.getByRole("navigation", { name: "Console" }).getByRole("link", { name: "us-1 no answer" }),
  ).toBeVisible();
  await expect(
    page.getByRole("article", { name: "eu-1" }).getByText("Answering", { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("region", { name: "Totals" })).toContainText(
    "These include the last figures from us-1, which the console cannot check right now.",
  );
});

test("the layout holds at tablet width: the rail is a top bar with a menu", async ({ page }) => {
  await page.setViewportSize({ width: 768, height: 1024 });
  await signIn(page, "viewer");
  const eu = page.getByRole("article", { name: "eu-1" });
  await expect(eu).toBeVisible();
  expect(await sidewaysOverflow(page)).toBeLessThanOrEqual(0);

  // Everything under the brand sits behind the Menu button until it is asked for.
  const menu = page.getByRole("button", { name: "Menu" });
  await expect(menu).toBeVisible();
  await expect(menu).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByRole("button", { name: "Sign out" })).toBeHidden();
  await expect(page.getByRole("navigation", { name: "Console" })).toBeHidden();

  await menu.click();
  await expect(menu).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByRole("navigation", { name: "Console" }).getByRole("link", { name: "Fleet" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign out" })).toBeInViewport();
  await expect(page.getByRole("combobox", { name: "Theme" })).toBeVisible();
  expect(await sidewaysOverflow(page)).toBeLessThanOrEqual(0);

  await page.keyboard.press("Escape");
  await expect(menu).toHaveAttribute("aria-expanded", "false");
  await expect(menu).toBeFocused();
  await expect(page.getByRole("button", { name: "Sign out" })).toBeHidden();

  // Following a link from the menu closes it.
  await menu.click();
  await page.getByRole("navigation", { name: "Console" }).getByRole("link", { name: "eu-1" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "eu-1" })).toBeVisible();
  await expect(menu).toHaveAttribute("aria-expanded", "false");
});

test("at tablet width the cards sit two across and nothing is cut off", async ({ page }) => {
  await page.setViewportSize({ width: 768, height: 1024 });
  await signIn(page, "viewer");
  const look = await page.getByRole("region", { name: "Needs a look" }).boundingBox();
  const eu = await page.getByRole("article", { name: "eu-1" }).boundingBox();
  const us = await page.getByRole("article", { name: "us-1" }).boundingBox();
  if (look === null || eu === null || us === null) throw new Error("a card is not on the page");
  // "Needs a look" first, the first leader beside it, the next leader on the row below.
  expect(Math.round(eu.y)).toBe(Math.round(look.y));
  expect(eu.x).toBeGreaterThan(look.x + look.width);
  expect(us.y).toBeGreaterThanOrEqual(look.y + look.height);
  for (const box of [look, eu, us]) {
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(768);
  }
  // The chart fills its card and keeps its height.
  const chart = await page.getByRole("article", { name: "eu-1" }).getByRole("img").boundingBox();
  if (chart === null) throw new Error("the chart is not on the page");
  expect(Math.round(chart.height)).toBe(64);
  expect(chart.width).toBeGreaterThan(eu.width * 0.75);
  expect(await brokenWords(page)).toEqual([]);
});

test("at phone width everything stacks and the page still does not scroll sideways", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page, "viewer");
  const eu = await page.getByRole("article", { name: "eu-1" }).boundingBox();
  const us = await page.getByRole("article", { name: "us-1" }).boundingBox();
  if (eu === null || us === null) throw new Error("a card is not on the page");
  expect(us.y).toBeGreaterThanOrEqual(eu.y + eu.height);
  expect(await sidewaysOverflow(page)).toBeLessThanOrEqual(0);
});

test("the overview works from the keyboard alone", async ({ page }) => {
  await signIn(page, "viewer");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to main content" })).toBeFocused();
  await page.keyboard.press("Enter");
  await page.keyboard.press("Tab");
  // The skip link lands past the rail: the first stop is the first control of the page.
  await expect(page.getByRole("button", { name: "All leaders" })).toBeFocused();
  await page.keyboard.press("Tab");
  await page.keyboard.press("Space");
  await expect(page).toHaveURL("/?label=env%3Dprod");
  await expect(page.getByRole("button", { name: "env = prod" })).toBeFocused();

  // Tab reaches a leader's own link without ever leaving the main region.
  const card = page.getByRole("article", { name: "eu-1" }).getByRole("link", { name: "eu-1", exact: true });
  for (let i = 0; i < 12; i += 1) {
    await page.keyboard.press("Tab");
    expect(await page.evaluate(() => document.activeElement?.closest("main") !== null)).toBe(true);
    if (await card.evaluate((el) => el === document.activeElement)) break;
  }
  await expect(card).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL("/leaders/eu-1/pools");
  await expect(page.getByRole("heading", { level: 1, name: "eu-1" })).toBeFocused();

  await page.getByRole("combobox", { name: "Theme" }).focus();
  await page.keyboard.press("ArrowDown");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
});

test("the longest leader name and a very long label never push the page sideways", async ({ page }) => {
  // The registry allows a name of 100 characters and a label value of 255, with no spaces.
  const name = `L${"o".repeat(99)}`;
  const label = `note=${"v".repeat(200)}`;
  await signIn(page, "admin", "/admin/leaders");
  await page.getByRole("button", { name: "Add leader" }).click();
  const dialog = page.getByRole("dialog", { name: "Add a leader" });
  await dialog.getByRole("textbox", { name: "Name" }).fill(name);
  await dialog.getByRole("textbox", { name: "Address (https://)" }).fill("https://long.leaders.example");
  await dialog.getByRole("textbox", { name: "Labels" }).fill(label);
  await dialog.getByLabel("Console credential").fill("c".repeat(20) + "_-" + "D".repeat(21));
  await dialog.getByRole("button", { name: "Add leader" }).click();
  await expect(dialog).toHaveCount(0);

  for (const width of [1280, 768, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/");
    // The new leader has no fake behind it: its card appears at the next 10 s refresh.
    await expect(page.getByRole("article", { name })).toBeVisible({ timeout: 20_000 });
    expect(await sidewaysOverflow(page), `fleet at ${width}`).toBeLessThanOrEqual(0);
    await page.getByRole("button", { name: `note = ${"v".repeat(200)}` }).click();
    await expect(page.getByRole("article", { name: "eu-1" })).toHaveCount(0);
    expect(await sidewaysOverflow(page), `fleet, filtered, at ${width}`).toBeLessThanOrEqual(0);
    const menu = page.getByRole("button", { name: "Menu" });
    if (await menu.isVisible()) {
      await menu.click();
      await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
      expect(await sidewaysOverflow(page), `menu at ${width}`).toBeLessThanOrEqual(0);
    }
    await page.goto(`/leaders/${name}/pools`);
    await expect(page.getByRole("heading", { level: 1, name })).toBeVisible();
    expect(await sidewaysOverflow(page), `leader page at ${width}`).toBeLessThanOrEqual(0);
  }
});

const TABLE_PAGES = [
  "/leaders/eu-1/jobs",
  "/leaders/eu-1/pools",
  "/leaders/eu-1/locations",
  "/leaders/eu-1/consent",
  "/leaders/eu-1/tokens",
  "/admin/leaders",
  "/admin/grants",
  "/admin/admins",
];

test("no table or card breaks a word across lines, and the page never scrolls sideways at tablet width", async ({
  page,
}) => {
  await page.setViewportSize({ width: 768, height: 1024 });
  await signIn(page, "admin");
  await expect(page.getByRole("article", { name: "eu-1" })).toBeVisible();
  expect(await brokenWords(page), "/").toEqual([]);
  expect(await sidewaysOverflow(page), "/").toBeLessThanOrEqual(0);
  for (const path of TABLE_PAGES) {
    await page.goto(path);
    await expect(page.getByRole("table").first()).toBeVisible();
    expect(await brokenWords(page), path).toEqual([]);
    expect(await sidewaysOverflow(page), path).toBeLessThanOrEqual(0);
  }
});
````

<!-- file: e2e/tests/sign-in.spec.ts | replace -->
Replace the whole of `packages/console-web/e2e/tests/sign-in.spec.ts` with:

````ts
import { endAllSessions, expect, signIn, test } from "./support";

test("a person without a session is sent to sign in and comes back to the page they asked for", async ({
  page,
}) => {
  await signIn(page, "viewer", "/?label=region%3Deu");
  await expect(page).toHaveURL("/?label=region%3Deu");
  await expect(page.getByRole("heading", { name: "Fleet" })).toBeVisible();
  await expect(page.getByRole("article", { name: "eu-1" })).toBeVisible();
  await expect(page.getByRole("article", { name: "us-1" })).toHaveCount(0);
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
````

- [ ] **Step 6: Type-check, lint, build and run the end-to-end tests**

Run, in `packages/console-web`:

```bash
npm run typecheck
npm run lint
npm test
npm run build
npx playwright test e2e/tests/overview.spec.ts e2e/tests/a11y.spec.ts e2e/tests/sign-in.spec.ts e2e/tests/admin.spec.ts e2e/tests/drilldown.spec.ts --retries=0
```

Expected: `tsc` prints nothing. ESLint prints nothing (0 warnings). Vitest: **380 tests pass**, none
fail. The build ends with `dist/ ok: index.html and 3 hashed assets`. Playwright: every test in the named specs passes, with no retry.

Then look at the overview at 1280 and 768 pixels wide in both themes, beside
`docs/superpowers/design/InkConsole.dc.html`: four totals, "Needs a look" as a sheet first in
the grid, a card per leader with a wide chart, four figures and a footer line.

If an end-to-end test fails, read what it was checking before changing it: the page is wrong
far more often than the test. Nothing may be left listening on ports 8900 or 8901 afterwards.

- [ ] **Step 7: Commit**

```bash
git add packages/console-web/src/components/HealthBadge.tsx packages/console-web/src/components/Sparkline.tsx packages/console-web/src/components/ThroughputChart.tsx packages/console-web/src/lib/format.ts packages/console-web/src/pages/FleetPage.tsx packages/console-web/src/lib/format.test.ts packages/console-web/src/components/Sparkline.test.tsx packages/console-web/src/pages/FleetPage.test.tsx packages/console-web/e2e/tests/a11y.spec.ts packages/console-web/e2e/tests/admin.spec.ts packages/console-web/e2e/tests/overview.spec.ts packages/console-web/e2e/tests/sign-in.spec.ts
git commit -m "Console web app: fleet overview as totals, a card per leader and Needs a look

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

### Task 4: The sign-in page

**Files:**
- Modify: `packages/console-web/src/pages/SignInPage.tsx`
- Test: `packages/console-web/src/pages/SignInPage.test.tsx`
- Test: `packages/console-web/e2e/tests/a11y.spec.ts`
- Test: `packages/console-web/e2e/tests/sign-in.spec.ts`
- Test: `packages/console-web/e2e/tests/support.ts`

**Interfaces:**
- Consumes: Task 1's classes (`.signin*`, `.on-ink`, `.sheet`, `.sheet-lifted`, `.sheet-title`, `.provider-list`, `.button`, `.button-primary`, `.button-wide`, `.brand`, `.mark`, `.mark-hex`, `.muted`) and `BrandMark`, `Wordmark`.
- Produces: the sign-in page of spec section 5.3: a `main` region holding the `h1` "Sign in" and one link per provider named "Continue with Microsoft", "Continue with Google" or "Continue with {provider id}".

`docs/superpowers/design/SignIn.dc.html`. Two halves: the brand on ink (in both themes) and
the card, a lifted sheet.

The page's logic does not change at all: the same two requests, the same safe `return_to`,
the same plain links to `/auth/login`. Only the markup and the words change. The first
provider is the primary button and the rest are ghost buttons.

The note at the bottom of the card is deliberately general. The drawing says "8 hours" and
"an hour", but both are server settings this page cannot read (spec section 10).

- [ ] **Step 1: Write the failing tests**

The three moved tests are updated to the new words, and four are added: where things are on
the page, a provider the console has no name for, no provider at all, and the Review Focus
case of the providers request failing.

<!-- file: src/pages/SignInPage.test.tsx | replace -->
Replace the whole of `packages/console-web/src/pages/SignInPage.test.tsx` with:

````tsx
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import { fail, mockFetch, reply } from "../test/fetchMock";
import { SESSION } from "../test/fixtures";

// The sign-in page sits outside the session: these render the whole app at /sign-in.

describe("the sign-in page", () => {
  it("lists the providers with a safe return_to and makes no session-bound calls", async () => {
    window.history.replaceState(null, "", "/sign-in?return_to=%2F%3Flabel%3Denv%253Dprod");
    const mock = mockFetch()
      .on("GET /auth/providers", reply(200, { providers: ["entra", "google"] }))
      .on("GET /api/session", fail(401, "unauthenticated"));
    render(<App />);
    const link = await screen.findByRole("link", { name: "Continue with Microsoft" });
    expect(link.getAttribute("href")).toBe("/auth/login?provider=entra&return_to=%2F%3Flabel%3Denv%253Dprod");
    expect(screen.getByRole("link", { name: "Continue with Google" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Sign in");
    expect(screen.queryByText(/already signed in/)).not.toBeInTheDocument();
    expect(mock.callsTo("GET /api/fleet")).toHaveLength(0);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("puts the sign-in card in the main region and the brand beside it", async () => {
    window.history.replaceState(null, "", "/sign-in");
    mockFetch()
      .on("GET /auth/providers", reply(200, { providers: ["entra"] }))
      .on("GET /api/session", fail(401, "unauthenticated"));
    const { container } = render(<App />);
    const main = within(await screen.findByRole("main"));
    expect(main.getByRole("heading", { level: 1, name: "Sign in" })).toBeInTheDocument();
    expect(await main.findByRole("link", { name: "Continue with Microsoft" })).toBeInTheDocument();
    expect(screen.getByText("Every leader you look after, in one place.")).toBeInTheDocument();
    expect(screen.getByText(window.location.host)).toBeInTheDocument();
    // Nothing on this page needs a session: no rail, no sign-out.
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Sign out" })).not.toBeInTheDocument();
    expect(container.querySelector("[style]")).toBeNull();
  });

  it("names a provider it does not know by its own id", async () => {
    window.history.replaceState(null, "", "/sign-in");
    mockFetch()
      .on("GET /auth/providers", reply(200, { providers: ["okta"] }))
      .on("GET /api/session", fail(401, "unauthenticated"));
    render(<App />);
    expect(await screen.findByRole("link", { name: "Continue with okta" })).toHaveAttribute(
      "href",
      "/auth/login?provider=okta",
    );
  });

  it("says so when no way to sign in is set up", async () => {
    window.history.replaceState(null, "", "/sign-in?signed_out=1");
    mockFetch()
      .on("GET /auth/providers", reply(200, { providers: [] }))
      .on("GET /api/session", fail(401, "unauthenticated"));
    render(<App />);
    expect(await screen.findByText(/No way to sign in is set up on this console/)).toBeInTheDocument();
    expect(screen.getByText("You are signed out.")).toHaveAttribute("role", "status");
  });

  it("says so in the card when the ways to sign in cannot be loaded", async () => {
    window.history.replaceState(null, "", "/sign-in");
    mockFetch()
      .on("GET /auth/providers", fail(503, "unavailable", "down"))
      .on("GET /api/session", fail(401, "unauthenticated"));
    render(<App />);
    const main = within(await screen.findByRole("main"));
    expect(await main.findByRole("alert")).toBeInTheDocument();
    expect(main.getByRole("heading", { level: 1, name: "Sign in" })).toBeInTheDocument();
    expect(main.queryByRole("link", { name: /^Continue with/ })).not.toBeInTheDocument();
    expect(main.queryByText(/^Loading/)).not.toBeInTheDocument();
  });

  it("drops a hostile return_to", async () => {
    window.history.replaceState(null, "", "/sign-in?return_to=%2F%2Fevil.example");
    mockFetch()
      .on("GET /auth/providers", reply(200, { providers: ["google"] }))
      .on("GET /api/session", fail(401, "unauthenticated"));
    render(<App />);
    const link = await screen.findByRole("link", { name: "Continue with Google" });
    expect(link.getAttribute("href")).toBe("/auth/login?provider=google");
  });

  it("offers a way back to a person who is already signed in", async () => {
    window.history.replaceState(null, "", "/sign-in");
    mockFetch()
      .on("GET /auth/providers", reply(200, { providers: ["google"] }))
      .on("GET /api/session", reply(200, SESSION));
    render(<App />);
    expect(await screen.findByText(/already signed in as person@example.org/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to the console" })).toHaveAttribute("href", "/");
  });
});
````

- [ ] **Step 2: Run the tests to see them fail**

Run: `npx vitest run src/pages/SignInPage.test.tsx`

Expected: FAIL, `Tests  7 failed (7)`: no link named "Continue with Microsoft", no heading
named "Sign in", and so on. The page still has the old words.

- [ ] **Step 3: Write the implementation**


<!-- file: src/pages/SignInPage.tsx | replace -->
Replace the whole of `packages/console-web/src/pages/SignInPage.tsx` with:

````tsx
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ProvidersOut, SessionInfo } from "../api/types";
import { safeReturnTo } from "../app/navigation";
import { useSearchParam } from "../app/router";
import { usePageTitle } from "../app/usePageTitle";
import { BrandMark, Wordmark } from "../components/Brand";
import { ErrorPanel } from "../components/ErrorPanel";

const PROVIDER_NAMES: Record<string, string> = { entra: "Microsoft", google: "Google" };

/**
 * Sign-in starts with a full-page visit to /auth/login (the console redirects to the
 * provider and back), so these are plain links, not in-app navigations. This page sits
 * outside SessionProvider: it probes GET /api/session itself, and a 401 there simply means
 * "signed out" (the navigation module does not reload on /sign-in).
 *
 * Two panels: the brand on ink (in both themes), and the sign-in card, a lifted sheet.
 */
export function SignInPage() {
  usePageTitle("Sign in");
  const returnTo = safeReturnTo(useSearchParam("return_to"));
  const signedOut = useSearchParam("signed_out") === "1";
  const [providers, setProviders] = useState<string[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [signedInAs, setSignedInAs] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    api
      .get<ProvidersOut>("/auth/providers", controller.signal)
      .then((answer) => setProviders(answer.providers))
      .catch((caught: unknown) => {
        if (!controller.signal.aborted) setError(caught);
      });
    // Already signed in? Offer the way back. Any failure (401 included) means "not signed in".
    api
      .get<SessionInfo>("/api/session", controller.signal)
      .then((session) => setSignedInAs(session.email ?? session.subject))
      .catch(() => undefined);
    return () => controller.abort();
  }, []);

  return (
    <div className="signin">
      <header className="signin-brand on-ink">
        <span className="brand signin-logo">
          <BrandMark size={40} />
          <Wordmark />
        </span>
        <div>
          <p className="signin-pitch">Every leader you look after, in one place.</p>
          <p className="signin-lede">
            See what is waiting, what finished and what needs a look. Everything you change here is
            recorded under your name, on the leader as well as here.
          </p>
        </div>
        <p className="signin-host">
          <span className="mark mark-hex" aria-hidden="true" />
          {window.location.host}
        </p>
      </header>
      <main id="main" className="signin-side">
        <div className="sheet sheet-lifted signin-card">
          <h1 className="sheet-title signin-title">Sign in</h1>
          <p className="muted">Use the account your organisation gave you.</p>
          {signedOut && <p role="status">You are signed out.</p>}
          {signedInAs !== null && (
            <p role="status">
              You are already signed in as {signedInAs}. <a href={returnTo}>Go to the console</a>.
            </p>
          )}
          {error !== null && <ErrorPanel error={error} />}
          {providers === null && error === null && <p>Loading the ways to sign in…</p>}
          {providers !== null && providers.length === 0 && (
            <p>No way to sign in is set up on this console. Tell whoever runs it.</p>
          )}
          {providers !== null && providers.length > 0 && (
            <ul className="provider-list">
              {providers.map((provider, i) => {
                const params = new URLSearchParams({ provider });
                if (returnTo !== "/") params.set("return_to", returnTo);
                return (
                  <li key={provider}>
                    <a
                      className={i === 0 ? "button button-primary button-wide" : "button button-wide"}
                      href={`/auth/login?${params.toString()}`}
                    >
                      Continue with {PROVIDER_NAMES[provider] ?? provider}
                    </a>
                  </li>
                );
              })}
            </ul>
          )}
          <p className="signin-note">
            The console signs you out after a spell with no activity, and after a longer fixed time
            whatever you are doing.
          </p>
        </div>
      </main>
    </div>
  );
}
````

- [ ] **Step 4: Run the unit tests to see them pass**

Run: `npm test`

Expected: PASS. **384 tests pass** (380 + 4 new in `SignInPage.test.tsx`).

- [ ] **Step 5: Update the end-to-end tests**

`signIn` in `support.ts` clicks "Continue with Microsoft". `sign-in.spec.ts` checks the new
words and gains a test of the two halves side by side at 1280px and stacked at 768px.
`a11y.spec.ts` scans the sign-in page at tablet width too.

<!-- file: e2e/tests/a11y.spec.ts | patch -->
Change `packages/console-web/e2e/tests/a11y.spec.ts`:

````diff
--- a/packages/console-web/e2e/tests/a11y.spec.ts
+++ b/packages/console-web/e2e/tests/a11y.spec.ts
@@ -20,8 +20,10 @@
 
     test("the sign-in page has no accessibility violations", async ({ page }) => {
       await page.goto("/sign-in?signed_out=1");
-      await expect(page.getByRole("link", { name: "Sign in with Microsoft Entra ID" })).toBeVisible();
+      await expect(page.getByRole("link", { name: "Continue with Microsoft" })).toBeVisible();
       await expectAccessible(page, `sign-in (${theme})`);
+      await page.setViewportSize({ width: 768, height: 1024 });
+      await expectAccessible(page, `sign-in at tablet width (${theme})`);
     });
 
     test("every signed-in page has no accessibility violations", async ({ page }) => {
````

<!-- file: e2e/tests/sign-in.spec.ts | patch -->
Change `packages/console-web/e2e/tests/sign-in.spec.ts`:

````diff
--- a/packages/console-web/e2e/tests/sign-in.spec.ts
+++ b/packages/console-web/e2e/tests/sign-in.spec.ts
@@ -14,7 +14,7 @@
   await signIn(page, "viewer");
   await page.getByRole("button", { name: "Sign out" }).click();
   await expect(page).toHaveURL("/sign-in?signed_out=1");
-  await expect(page.getByText("You have signed out.")).toBeVisible();
+  await expect(page.getByText("You are signed out.")).toBeVisible();
   await page.goto("/");
   await expect(page).toHaveURL(/\/sign-in/);
 });
@@ -26,5 +26,25 @@
   await signIn(page, "viewer");
   await endAllSessions(request);
   await expect(page).toHaveURL(/\/sign-in/, { timeout: 20_000 });
-  await expect(page.getByRole("heading", { name: "Sign in to the SwarmScribe console" })).toBeVisible();
+  await expect(page.getByRole("heading", { level: 1, name: "Sign in" })).toBeVisible();
 });
+
+test("the sign-in page shows the brand beside the card, and stacks them at tablet width", async ({ page }) => {
+  await page.goto("/sign-in");
+  const card = page.getByRole("main");
+  await expect(card.getByRole("heading", { level: 1, name: "Sign in" })).toBeVisible();
+  await expect(card.getByRole("link", { name: "Continue with Microsoft" })).toBeVisible();
+  const pitch = page.getByText("Every leader you look after, in one place.");
+  await expect(pitch).toBeVisible();
+  await expect(page.getByText("localhost:8900")).toBeVisible();
+  const wide = { pitch: await pitch.boundingBox(), card: await card.boundingBox() };
+  if (wide.pitch === null || wide.card === null) throw new Error("the sign-in page is not laid out");
+  expect(wide.card.x).toBeGreaterThan(wide.pitch.x + wide.pitch.width);
+
+  await page.setViewportSize({ width: 768, height: 1024 });
+  const narrow = { pitch: await pitch.boundingBox(), card: await card.boundingBox() };
+  if (narrow.pitch === null || narrow.card === null) throw new Error("the sign-in page is not laid out");
+  expect(narrow.card.y).toBeGreaterThan(narrow.pitch.y + narrow.pitch.height);
+  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
+  expect(overflow).toBeLessThanOrEqual(0);
+});
````

<!-- file: e2e/tests/support.ts | patch -->
Change `packages/console-web/e2e/tests/support.ts`:

````diff
--- a/packages/console-web/e2e/tests/support.ts
+++ b/packages/console-web/e2e/tests/support.ts
@@ -60,7 +60,7 @@
   });
   await page.goto(path);
   await expect(page).toHaveURL(/\/sign-in/);
-  await page.getByRole("link", { name: "Sign in with Microsoft Entra ID" }).click();
+  await page.getByRole("link", { name: "Continue with Microsoft" }).click();
   await expect(page.getByText("Microsoft sign-in")).toBeVisible();
   expect(redirect.status).toBe(302);
   expect(redirect.location.startsWith(ENTRA)).toBe(true);
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

Expected: `tsc` prints nothing. ESLint prints nothing (0 warnings). Vitest: **384 tests pass**, none
fail. The build ends with `dist/ ok: index.html and 3 hashed assets`. Playwright: the whole end-to-end suite passes, with no retry.

The whole suite, because every spec signs in through this page.

Then look at `/sign-in` at 1280 and 768 pixels wide in both themes, beside
`docs/superpowers/design/SignIn.dc.html`.

If an end-to-end test fails, read what it was checking before changing it: the page is wrong
far more often than the test. Nothing may be left listening on ports 8900 or 8901 afterwards.

- [ ] **Step 7: Commit**

```bash
git add packages/console-web/src/pages/SignInPage.tsx packages/console-web/src/pages/SignInPage.test.tsx packages/console-web/e2e/tests/a11y.spec.ts packages/console-web/e2e/tests/sign-in.spec.ts packages/console-web/e2e/tests/support.ts
git commit -m "Console web app: sign-in page with the brand beside the sign-in card

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

### Task 5: Pinned Actions check, screenshots to look at, README

**Files:**
- Modify: `packages/console-web/.gitignore`
- Modify: `packages/console-web/package.json`
- Create: `packages/console-web/playwright.screens.config.ts`
- Modify: `packages/console-web/tsconfig.json`
- Test: `packages/console-web/e2e/screens/capture.spec.ts` (new)
- Test: `packages/console-web/e2e/tests/overview.spec.ts`
- Modify: `packages/console-web/README.md`

**Interfaces:**
- Consumes: everything above. `signIn`, `setLeaderMode`, `expect` and `test` from `e2e/tests/support.ts`.
- Produces: `npm run screens` (Playwright with `playwright.screens.config.ts`, running `e2e/screens/capture.spec.ts`), writing PNG files to `packages/console-web/screens/`; an end-to-end test that the Actions column stays in reach.

Three things close the plan.

1. **The C3 follow-up gets its test.** Task 1's stylesheet already pins the Actions column
   (`td.actions` and its header are `position: sticky; right: 0`, and below 900px a row's
   buttons stack). This task adds the end-to-end test that it works on every table with row
   actions, however far the table is scrolled.
2. **`npm run screens`.** A second Playwright config runs one spec that only takes
   screenshots: every page and dialog, in both themes, at 1280 and 768 pixels wide. It is
   kept out of `npm run e2e`.
3. **The README** says how the styles are organised and how to take the screenshots.

`.gitignore` gets `/screens/` with the leading slash. Without it the pattern would also
ignore the new `e2e/screens/` directory and the capture spec would never be committed.

- [ ] **Step 1: Add the screens script and its configuration**


<!-- file: .gitignore | replace -->
Replace the whole of `packages/console-web/.gitignore` with:

````gitignore
node_modules/
dist/
test-results/
playwright-report/
blob-report/
/screens/
````

<!-- file: package.json | patch -->
Change `packages/console-web/package.json`:

````diff
--- a/packages/console-web/package.json
+++ b/packages/console-web/package.json
@@ -13,7 +13,8 @@
     "typecheck": "tsc -p tsconfig.json",
     "lint": "eslint . --max-warnings 0",
     "test": "vitest run",
-    "e2e": "playwright test"
+    "e2e": "playwright test",
+    "screens": "playwright test -c playwright.screens.config.ts"
   },
   "dependencies": {
     "react": "19.3.0",
````

<!-- file: playwright.screens.config.ts | create -->
Create `packages/console-web/playwright.screens.config.ts`:

````ts
import { defineConfig } from "@playwright/test";
import base from "./playwright.config";

// `npm run screens`: the same harness and browser as the end-to-end tests, but it runs
// e2e/screens/capture.spec.ts, which only takes screenshots (into screens/) for a person to
// look at. Kept out of `npm run e2e` so the test run stays a test run.
export default defineConfig({
  ...base,
  testDir: "e2e/screens",
  retries: 0,
  reporter: "list",
});
````

<!-- file: tsconfig.json | replace -->
Replace the whole of `packages/console-web/tsconfig.json` with:

````json
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
  "include": ["src", "e2e", "vite.config.ts", "playwright.config.ts", "playwright.screens.config.ts"]
}
````

- [ ] **Step 2: Run the unit tests to see them pass**

Run: `npm test`

Expected: PASS. **384 tests pass**, as after Task 4: nothing under `src/` changed.

- [ ] **Step 3: Add the pinned-column test and the screenshot spec**

`package.json`, `tsconfig.json`, `.gitignore` and `playwright.screens.config.ts` come first
(they are in this task's files), then the two specs.

<!-- file: e2e/screens/capture.spec.ts | create -->
Create `packages/console-web/e2e/screens/capture.spec.ts`:

````ts
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import type { Page } from "@playwright/test";
import { expect, setLeaderMode, signIn, test } from "../tests/support";

// Not a test of behaviour: it photographs every screen in both themes at desktop and tablet
// width, into packages/console-web/screens/, for a person to LOOK at. jsdom and axe pass
// layouts that are plainly wrong to the eye (a cut-off column, text under a pinned cell, a
// card stretched to nothing), so a redesign is not done until these have been looked at.
// Run with: npm run screens   (the same harness as npm run e2e; build first).

const OUT = join(process.cwd(), "screens");
const WIDTHS = [1280, 768] as const;
const THEMES = ["dark", "light"] as const;

const PAGES: [name: string, path: string][] = [
  ["fleet", "/"],
  ["fleet-filtered", "/?label=region%3Dus"],
  ["leader-pools", "/leaders/eu-1/pools"],
  ["leader-jobs", "/leaders/eu-1/jobs"],
  ["leader-jobs-failed", "/leaders/eu-1/jobs?state=failed"],
  ["leader-locations", "/leaders/eu-1/locations"],
  ["leader-tokens", "/leaders/eu-1/tokens"],
  ["leader-consent", "/leaders/eu-1/consent"],
  ["admin-leaders", "/admin/leaders"],
  ["admin-grants", "/admin/grants"],
  ["admin-admins", "/admin/admins"],
  ["not-found", "/no-such-page"],
];

async function settled(page: Page): Promise<void> {
  await expect(page.getByText(/^Loading/)).toHaveCount(0);
  await page.waitForLoadState("networkidle");
}

for (const theme of THEMES) {
  for (const width of WIDTHS) {
    test(`screens: ${theme} at ${width}px`, async ({ page, request }) => {
      test.setTimeout(240_000);
      mkdirSync(OUT, { recursive: true });
      const shot = async (name: string, fullPage = true) => {
        await page.screenshot({ path: join(OUT, `${name}-${theme}-${width}.png`), fullPage });
      };
      await page.emulateMedia({ colorScheme: theme });
      await page.setViewportSize({ width, height: 900 });

      await page.goto("/sign-in?signed_out=1");
      await expect(page.getByRole("link", { name: "Continue with Microsoft" })).toBeVisible();
      await shot("sign-in");

      await signIn(page, "admin");
      for (const [name, path] of PAGES) {
        await page.goto(path);
        await settled(page);
        await shot(name);
      }

      // The rail as a top bar, with its menu open (tablet width only).
      const menu = page.getByRole("button", { name: "Menu" });
      await page.goto("/");
      await settled(page);
      if (await menu.isVisible()) {
        await menu.click();
        await shot("menu-open");
      }

      // Dialogs: a confirmation, a form with its errors showing, and the one-time token.
      await page.goto("/leaders/eu-1/jobs");
      await page
        .getByRole("button", { name: /^Cancel job / })
        .first()
        .click();
      await expect(page.getByRole("alertdialog")).toBeVisible();
      await shot("dialog-confirm", false);
      await page.keyboard.press("Escape");

      await page.goto("/leaders/eu-1/locations");
      await page.getByRole("button", { name: "Add location" }).click();
      await page.getByRole("dialog").getByRole("button", { name: "Add location" }).click();
      await expect(page.getByRole("dialog").getByRole("alert").first()).toBeVisible();
      await shot("dialog-form-errors", false);
      await page.keyboard.press("Escape");

      await page.goto("/leaders/eu-1/tokens");
      await page.getByRole("button", { name: "Create join token" }).click();
      await shot("dialog-token-form", false);
      await page.getByRole("dialog").getByRole("button", { name: "Create token" }).click();
      await expect(page.getByRole("textbox", { name: "Join token" })).toHaveValue(/^sst_/);
      await shot("dialog-token-shown", false);
      await page.getByRole("button", { name: "I have stored it" }).click();
      await shot("dialog-token-asking", false);
      await page.getByRole("button", { name: "I have stored it" }).click();

      await page.goto("/admin/leaders");
      await page.getByRole("button", { name: "Add leader" }).click();
      await shot("dialog-add-leader", false);
      await page.keyboard.press("Escape");

      // A viewer: what switched-off actions look like.
      await page.context().clearCookies();
      await signIn(page, "viewer", "/leaders/eu-1/jobs");
      await settled(page);
      await shot("leader-jobs-as-viewer");

      // One leader down: its card, its place in the rail, and its own page.
      await setLeaderMode(request, "us-1", "down");
      await page.goto("/");
      await expect(
        page.getByRole("article", { name: "us-1" }).getByText("Not answering", { exact: true }),
      ).toBeVisible({ timeout: 30_000 });
      await shot("fleet-one-down");
      await page.goto("/leaders/us-1/jobs");
      await expect(page.getByRole("alert").first()).toBeVisible();
      await shot("leader-down");
    });
  }
}
````

<!-- file: e2e/tests/overview.spec.ts | patch -->
Change `packages/console-web/e2e/tests/overview.spec.ts`:

````diff
--- a/packages/console-web/e2e/tests/overview.spec.ts
+++ b/packages/console-web/e2e/tests/overview.spec.ts
@@ -251,6 +251,56 @@
   }
 });
 
+// Every table with row actions: its page and the name of its scrolling region.
+const ACTION_TABLES: [path: string, region: string][] = [
+  ["/leaders/eu-1/jobs", "Job list"],
+  ["/leaders/eu-1/pools", "Followers"],
+  ["/leaders/eu-1/locations", "Location list"],
+  ["/leaders/eu-1/tokens", "Join token list"],
+  ["/admin/leaders", "Registered leaders"],
+  ["/admin/grants", "Grants"],
+  ["/admin/admins", "Console administrators"],
+];
+
+test("the Actions column stays in reach at tablet width however far a table is scrolled", async ({
+  page,
+}) => {
+  await page.setViewportSize({ width: 768, height: 1024 });
+  await signIn(page, "admin");
+  for (const [path, name] of ACTION_TABLES) {
+    await page.goto(path);
+    const region = page.getByRole("region", { name });
+    await expect(region).toBeVisible();
+    const button = region.locator("td.actions button").first();
+    const header = region.getByRole("columnheader", { name: "Actions" });
+    const widest = await region.evaluate((el) => el.scrollWidth - el.clientWidth);
+    for (const left of [0, Math.floor(widest / 2), widest]) {
+      await region.evaluate((el, x) => {
+        el.scrollLeft = x;
+      }, left);
+      const frame = await region.boundingBox();
+      for (const pinned of [button, header]) {
+        const box = await pinned.boundingBox();
+        if (frame === null || box === null) throw new Error(`${path}: nothing to measure`);
+        expect(box.x, `${path} at ${left}`).toBeGreaterThanOrEqual(frame.x);
+        expect(box.x + box.width, `${path} at ${left}`).toBeLessThanOrEqual(frame.x + frame.width + 1);
+      }
+      // Nothing is painted over the button: the point at its middle is the button itself.
+      const onTop = await button.evaluate((el) => {
+        const r = el.getBoundingClientRect();
+        const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
+        return hit !== null && el.contains(hit);
+      });
+      expect(onTop, `${path} at ${left}`).toBe(true);
+    }
+    // The pinned column leaves the first column readable: it never covers the whole table.
+    const cell = await region.locator("td.actions").first().boundingBox();
+    const frame = await region.boundingBox();
+    if (cell === null || frame === null) throw new Error(`${path}: nothing to measure`);
+    expect(cell.width, path).toBeLessThan(frame.width / 2);
+  }
+});
+
 const TABLE_PAGES = [
   "/leaders/eu-1/jobs",
   "/leaders/eu-1/pools",
````

- [ ] **Step 4: Update the README**

<!-- file: README.md | patch -->
Change `packages/console-web/README.md`:

````diff
--- a/packages/console-web/README.md
+++ b/packages/console-web/README.md
@@ -34,6 +34,34 @@
 and no storage access outside the theme module. A join token, a credential or the CSRF token
 never goes in a URL, in storage or in state that outlives its dialog.
 
+## Look and wording
+
+The console wears the SwarmScribe brand: honey amber on warm ink, paper sheets, serif
+headings, system fonts only. The design, every token and every string are in
+`docs/superpowers/specs/2026-10-04-console-redesign.md`; the approved mockups are beside it in
+`docs/superpowers/design/`.
+
+`src/styles.css` only imports the files in `src/styles/`, in order:
+
+| File | Holds |
+| --- | --- |
+| `tokens.css` | Colour, type, space and shape tokens. Two colour sets, ink and paper. |
+| `base.css` | Page defaults, headings, links, focus rings, the skip link. |
+| `controls.css` | Buttons, filter pills, form fields. |
+| `surfaces.css` | Panels, tables, sheets (dialogs, callouts), status marks, notices. |
+| `shell.css` | The rail, the top bar at narrow widths, page header, tabs. |
+| `fleet.css` | The fleet overview: totals, leader cards, the chart. |
+| `signin.css` | The sign-in page. |
+
+A colour is never written in a component or in any file but `tokens.css` (the mark's two
+brand constants in `shell.css` are the exception). `src/styles/tokens.test.ts` measures the
+tokens: it fails when a text pair is under 4.5:1, a mark or control edge under 3:1, or the
+two copies of a colour set differ. The dark theme is the default; the light theme follows
+the system setting or the Theme switch in the rail.
+
+There are no inline styles, no web fonts and no images from another origin. The logo is
+inline SVG (`src/components/Brand.tsx`); the favicon is a hashed asset.
+
 ## End-to-end tests
 
 ```
@@ -46,3 +74,15 @@
 `SWARMSCRIBE_TEST_DATABASE_URL` or the local pgserver, plus a control server on
 `http://127.0.0.1:8901` that only the tests use. `E2E_HARNESS_COMMAND` replaces the command
 Playwright starts. Nothing may be left listening on 8900 or 8901 afterwards.
+
+## Screenshots to look at
+
+```
+npm run build
+npm run screens
+```
+
+This uses the same harness to photograph every page and dialog in both themes at 1280 and
+768 pixels wide, into `screens/` (not committed). Look at them after any change to layout or
+styles: the unit tests and the accessibility scan pass layouts that are plainly wrong to
+the eye.
````

- [ ] **Step 5: Take the screenshots and LOOK at them**

Run, in `packages/console-web`:

```bash
npm run build
npm run screens
```

Expected: 4 tests pass (dark and light, each at 1280 and 768) and `screens/` holds about 100
PNG files named `<screen>-<theme>-<width>.png`.

Open them. This step is not done until a person, or an agent that can see images, has
looked at every one. jsdom and axe pass layouts that are plainly wrong to the eye. For each
image check:

- Nothing is cut off at the right edge, and nothing sits on top of anything else.
- Text is readable against what is behind it, in both themes.
- **Fleet** (`fleet-*`, `fleet-filtered-*`, `fleet-one-down-*`): matches
  `docs/superpowers/design/InkConsole.dc.html`: totals, "Needs a look" first, cards two
  across with a wide chart. The card of the leader that is down has a red edge and no chart.
- **Sign-in** (`sign-in-*`): matches `docs/superpowers/design/SignIn.dc.html`; stacked at 768.
- **Top bar** (`menu-open-*-768`): the menu lists Fleet, the leaders, Administration, the
  person, Theme and Sign out.
- **Leader pages and Administration** (`leader-*`, `admin-*`): old structure, new look. At
  768 the Actions column is at the right edge with its buttons stacked.
- **Dialogs** (`dialog-*`): a paper sheet with the amber offset shadow in the dark theme, an
  ink sheet in the light theme; the shadow is not clipped.
- **A viewer** (`leader-jobs-as-viewer-*`): switched-off buttons have a dashed edge and say
  which role they need.

Write down anything that looks wrong, fix it in the stylesheet (never with an inline style),
rerun this step, and say in the commit message what was fixed. If nothing was wrong, say
that the screenshots were looked at.

- [ ] **Step 6: Type-check, lint, build and run the end-to-end tests**

Run, in `packages/console-web`:

```bash
npm run typecheck
npm run lint
npm test
npm run build
npm run e2e -- --retries=0
```

Expected: `tsc` prints nothing. ESLint prints nothing (0 warnings). Vitest: **384 tests pass**, none
fail. The build ends with `dist/ ok: index.html and 3 hashed assets`. Playwright: the whole end-to-end suite passes, with no retry.

The whole suite, one last time. It includes the new test, "the Actions column
stays in reach at tablet width however far a table is scrolled".

If an end-to-end test fails, read what it was checking before changing it: the page is wrong
far more often than the test. Nothing may be left listening on ports 8900 or 8901 afterwards.

- [ ] **Step 7: Commit**

```bash
git add packages/console-web/.gitignore packages/console-web/package.json packages/console-web/playwright.screens.config.ts packages/console-web/tsconfig.json packages/console-web/e2e/screens/capture.spec.ts packages/console-web/e2e/tests/overview.spec.ts packages/console-web/README.md
git commit -m "Console web app: pinned Actions column test, npm run screens, README for the redesign

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

## Self-review

Run when the plan was written:

1. **Spec coverage.** Sections 3 (tokens) and 4 (components): Task 1, measured by
   `tokens.test.ts`. 4.1, 4.2, 5.1 (rail, top bar, frame): Task 2. 4.5 to 4.7, 5.2 (fleet):
   Task 3. 5.3 (sign-in): Task 4. 6.2's Frame, Health, Fleet overview and Sign-in tables:
   Tasks 2 to 4, each string asserted by a unit or end-to-end test. 7.1 to 7.3, 7.6 to 7.11,
   7.15, 7.16: Tasks 1 to 5. 7.8 (Actions in reach): styles in Task 1, test in Task 5.
   Section 12's screenshots: Task 5. Sections 5.4 to 5.7 and the rest of 6.2 are R2's.
2. **Placeholders.** None: every step has its code or its command and expected output.
3. **Names.** `roleSummary`, `formatTries`, `countOf`, `healthText`, `StatusMark`,
   `HealthBadge`, `LABEL_PILL_LIMIT`, `concerns`, `BrandMark`, `Wordmark` are spelled the
   same where they are made and where they are used; every class a component uses is in
   Task 1's stylesheet.
4. **Review Focus.** Five items, each with a named test in a named task.
5. **Replay.** The plan's blocks were applied in order to a clean checkout and the result
   compared with the tree the tasks were verified on: identical.

## Execution

Plan complete and saved to
`docs/superpowers/plans/2026-10-04-console-redesign-r1-shell-and-fleet.md`. Two ways to run it:

- **Subagent-driven** (recommended): a fresh subagent per task and a fresh reviewer after
  each. The tasks hand each other only class names and a few function signatures, all in
  each task's Interfaces block, and a mistake here is seen by everyone who opens the
  console.
- **Native:** one session does every task, then one reviewer checks the branch.

Either way, Task 5's screenshots are looked at before the branch is called done.
