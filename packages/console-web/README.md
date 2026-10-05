# swarmscribe-console-web

The fleet console's web app: the fleet overview, the leader pages (Jobs, Pools and
followers, Locations, Consent, Join tokens) with their actions, and the Administration pages
(leaders, grants, console administrators). React and TypeScript, built with Vite. The console
(`packages/console`) serves the built files; this package has no server of its own.

## Requirements

Node 24.15 or later in the 24 line. `.npmrc` sets `engine-strict`, so `npm ci` refuses an older
Node: upgrade Node rather than relaxing that.

## Develop

```
npm ci
npm run typecheck
npm run lint
npm test            # unit and component tests (Vitest, jsdom)
npm run watch       # rebuilds dist/ on every change
```

There is no Vite dev server on purpose: the console's CSP and CSRF checks apply only to the
built app on the console's own origin, so work against a rebuilt `dist/`.

## Build contract

`npm run build` runs `vite build`, then `scripts/check-dist.mjs`, which fails the build unless
`dist/` holds `index.html` and hashed assets named `<name>-<16 hex characters>.<ext>`, with no
inline script or style and nothing from another origin. Point
`SWARMSCRIBE_CONSOLE_STATIC_DIR` at `dist/`.

Source rules enforced by lint: no `style` props, no `dangerouslySetInnerHTML`, no `console.*`,
and no storage access outside the theme module. A join token, a credential or the CSRF token
never goes in a URL, in storage or in state that outlives its dialog.

## Look and wording

The console wears the SwarmScribe brand: honey amber on warm ink, paper sheets, serif
headings, system fonts only. The design, every token and every string are in
`docs/superpowers/specs/2026-10-04-console-redesign.md`; the approved mockups are beside it in
`docs/superpowers/design/`.

`src/styles.css` only imports the files in `src/styles/`, in order:

| File | Holds |
| --- | --- |
| `tokens.css` | Colour, type, space and shape tokens. Two colour sets, ink and paper. |
| `base.css` | Page defaults, headings, links, focus rings, the skip link. |
| `controls.css` | Buttons, filter pills, form fields. |
| `surfaces.css` | Panels, tables, sheets (dialogs, callouts), status marks, notices. |
| `shell.css` | The rail, the top bar at narrow widths, page header, tabs. |
| `fleet.css` | The fleet overview: totals, leader cards, the chart. |
| `signin.css` | The sign-in page. |

A colour is never written in a component or in any file but `tokens.css` (the mark's two
brand constants in `shell.css` are the exception). `src/styles/tokens.test.ts` measures the
tokens: it fails when a text pair is under 4.5:1, a mark or control edge under 3:1, or the
two copies of a colour set differ. The dark theme is the default; the light theme follows
the system setting or the Theme switch in the rail.

There are no inline styles, no web fonts and no images from another origin. The logo is
inline SVG (`src/components/Brand.tsx`); the favicon is a hashed asset.

## End-to-end tests

```
npx playwright install chromium    # once
npm run e2e
```

Playwright starts `e2e/harness/serve.py`: the real console on `http://localhost:8900`, an
in-memory Entra ID and two in-memory leaders (`eu-1`, `us-1`), on
`SWARMSCRIBE_TEST_DATABASE_URL` or the local pgserver, plus a control server on
`http://127.0.0.1:8901` that only the tests use. `E2E_HARNESS_COMMAND` replaces the command
Playwright starts. Nothing may be left listening on 8900 or 8901 afterwards.

## Screenshots to look at

```
npm run build
npm run screens
```

This uses the same harness to photograph every page and dialog in both themes at 1280 and
768 pixels wide, into `screens/` (not committed). Look at them after any change to layout or
styles: the unit tests and the accessibility scan pass layouts that are plainly wrong to
the eye.
