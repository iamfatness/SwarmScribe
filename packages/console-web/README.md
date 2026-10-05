# swarmscribe-console-web

The fleet console's web app: the fleet overview, the leader pages (Jobs, Pools and
followers, Locations, Consent, Join tokens) with their actions, and Administration (leaders,
who can do what, console administrators). React and TypeScript, built with Vite. The console
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

## What it looks like

Dark by default, on warm ink with honey amber; a designed light theme (paper page, ink rail)
follows the system or the Theme switch. A rail on the left runs the full height of the window:
the brand, Fleet, one link per leader you can see (flagged "no answer", "revoked" or "off"),
Administration for console administrators, and your name, Theme and Sign out pinned at the
bottom. Below 900px the rail becomes a sticky top bar with a Menu button.

- **Fleet:** four totals, a full-width "Needs a look" band (the worst five things, with "Show
  all N"), then a card per leader with its status, a 24-hour chart and four figures. Label
  filters are pills.
- **A leader:** breadcrumb, name and labels, status pill, a line on what your role switches
  off, five tabs. Job states are pills with counts from the leader's last check. Every table
  fits its region at 1280, 900 and 768 pixels with no sideways scroll, so the Actions column is
  not pinned.
- **Dialogs** are paper sheets with an amber offset shadow (ink sheets on the light theme).
  The join token is shown once, in its own dialog, and cannot be closed by accident.
- **Administration** is one page with three sections (Leaders, Who can do what, Console
  administrators), each with its own address.

Wording is the console's own, not the leader's: see the copy tables in the spec.

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
| `detail.css` | A leader's pages and Administration: list toolbars, job states, the one-time token. |

**The brand tokens are in `src/styles/tokens.css`, and no colour lives anywhere else**: not in
a component, not in another stylesheet (the mark's two brand constants in `shell.css` are the
only exception). A test fails on any hex colour outside that file. To change a colour, change
its token. `src/styles/tokens.test.ts` measures the
tokens: it fails when a text pair is under 4.5:1, a mark or control edge under 3:1, or the
two copies of a colour set differ. The dark theme is the default; the light theme follows
the system setting or the Theme switch in the rail.

There are no inline styles, no web fonts and no images from another origin. The logo is
inline SVG (`src/components/Brand.tsx`); the favicon is a hashed asset.

### The theme boot script

`index.html` loads a small classic (blocking) script, in `<head>`, before the page's first paint. It is built from
`src/theme-boot.ts` (Vite builds it on its own, with no imports left and a hashed name, so the
CSP still holds) and does what `main.tsx` does later: reads the stored theme and sets
`data-theme` on `<html>`. Without it, a person who chose Dark on a light system, or the
reverse, would see the system's theme flash before the app loaded. `scripts/check-dist.mjs`
fails the build if the script is missing from `index.html`.

### Adding a route

A path is a route only if its first segment is in `src/app/routePrefixes.json` (`/`,
`/leaders`, `/admin`, `/sign-in`); `App.tsx` sends any other path to the not-found page. The
Helm chart's Ingress paths are checked against the same file (`deploy/helm/swarmscribe-console/ci/check_render.py`),
so a prefix missing from it would 404 at the ingress even if a page existed. To add a page:
add its prefix to the file, add its path to `SignedInPage` in `src/App.tsx` (and a title in
`pageOf`), and add the prefix to the Ingress paths in the chart. `routes.test.ts` and
`routePrefixes.test.tsx` read the same file.

### Wording

The console says things its own way, and the spec's copy table is the list: a leader is
"answering" or "not answering", a job is "waiting", "with follower 1a2b3c4d", "finished" or
"failed" and can be "tried again", a location or a leader is "switched off", and roles are
"given". The leader's own words (queued, leased, retry, poll, grant, scope) stay in the API
and in addresses such as `?state=queued`; they are not shown to a person. A button's
accessible name always contains its visible text, word for word. A figure and its unit are
joined by a no-break space (`withUnit`).

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

This uses the same harness to photograph every page and dialog in both themes at 1280, 900
and 768 pixels wide, into `screens/` (not committed). Look at them after any change to layout or
styles: the unit tests and the accessibility scan pass layouts that are plainly wrong to
the eye.
