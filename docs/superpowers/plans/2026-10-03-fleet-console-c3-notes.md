# Fleet console C3 (web app): notes handed over from C2b

What the console backend (C1, C1b, C2a, C2b) guarantees and what the web app must respect. Source of truth is the code and `README.md` ("Leaders in the console"); this list is the shortcut for whoever writes the C3 plan.

## Errors

- **Not-found codes.**
  - `leader_not_found` (404): the leader is unknown or the person holds no grant on it. Show it as "not visible to you". The two cases are indistinguishable by design.
  - Under `/api/leaders/<name>/...`, `not_found` is an action the console does not offer, or the leader's own 404 (unknown job, follower or token). It never means the leader is missing.
  - Under `/api/admin/...`, `not_found` is the registry's (unknown leader, grant or administrator).
- **A 401 from any `/api` route is always the console's own session.** Leader 401s become `503 leader_credential_revoked` or `502 leader_credential_rejected`.
- **Codes the proxy adds:**

  | Code | Status | Notes |
  |---|---|---|
  | `leader_unreachable` | 503 | `Retry-After: 15` |
  | `leader_credential_revoked` | 503 | an administrator must replace the credential |
  | `leader_credential_unreadable` | 503 | the stored credential cannot be opened |
  | `leader_credential_rejected` | 502 | |
  | `bad_gateway` | 502 | on a POST, whether the action happened is unknown (also used for unexpected console-side errors; the audit outcome is `bad_gateway` or `error`) |
  | `leader_disabled` | 409 | |
  | `forbidden` | 403 | console role below the route's; the message names the role needed |
  | `actor_not_representable` | 403 | the person's identity cannot be sent to the leader |
  | `invalid_request` | 422 | fixed text, never an echo of input |
  | `too_large` | 413 | body over 64 KiB |

  Leader errors pass through with the leader's own status and code (including 403, 409, 429, 5xx) and a numeric `Retry-After` of at most 3600. A leader 422 keeps its code, but its message is the fixed text "the leader did not accept the request as sent".

## Fleet data

- **Health values:** `disabled`, `credential_revoked`, `unreachable`, `pending`, `reachable`.
  - `pending` can carry `consecutive_failures` of 1 or 2: the leader is failing but has never succeeded (or was just rotated or re-pointed and not yet polled).
  - A leader with an earlier success shows `reachable` until its third failure.
  - `summary` and `snapshot` are the latest successful poll, so they can be stale for revoked or unreachable leaders. Show `snapshot.taken_at`.
  - `oldest_queued_age_s` is as of `taken_at` (the leader's own clock for the job ages), and is null when nothing is queued. Add the time since `taken_at` when showing it.
- **History:** `hours` is 1 to 24. Buckets are 5 minutes and show the last snapshot in each. The first bucket can start up to 5 minutes before the window. A failed bucket has `reachable=false` and null figures.
- **Disabling actions:** use `role` from `/api/fleet` against the allow-list roles in `packages/console/src/swarmscribe_console/proxy.py`. `admin` is needed for join tokens (list, create, revoke), follower revoke and location add, enable and disable; `operator` for job retry, cancel and priority, follower drain and location scan.

## Handling secrets

- **Join token:** the plaintext is in the `201` body of `POST /api/leaders/<name>/tokens`, once, with `Cache-Control: no-store`. Never put it in a URL, router state, `localStorage`, query cache that persists, or logs.
- **Every unsafe `/api` call** needs `X-CSRF-Token` (from `GET /api/session`) and a same-origin `Origin`.

## Serving and CSP

- `script-src 'self'` and `style-src 'self'`: no inline `<script>` or `<style>`, and no `style=""` attributes in served HTML. CSSOM `element.style` is fine. Check that the chart library does not inject `<style>` tags.
- `img-src 'self' data:`, `connect-src 'self'`, `font-src 'self'`: no CDNs and no external fonts.
- **App routes must be extensionless and outside `/api` and `/auth`.** A dot in the last path segment returns 404 instead of `index.html`.
- **Caching:** `index.html` and the fallback are `Cache-Control: no-cache`. Only filenames that look hashed (a `.` or `-`, then 8 or more letters, digits or underscores with at least one digit, then the extension, as in `index-Dk3f9aB2.js`) get a one-year `immutable` header; everything else is `no-cache`. Configure the bundler to emit content-hashed filenames in that shape.
