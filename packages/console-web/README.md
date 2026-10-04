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
