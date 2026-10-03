# SwarmScribe — Fleet Console Spec

Date: 2026-10-03
Status: Approved design, written for review
Parents: `2026-10-02-swarmscribe-architecture-design.md`,
`2026-10-02-leader-design.md` (section 10 sign-in and roles).
Roadmap: item 10 (web dashboard) is delivered first as this fleet console;
playback, search and sharing views join it with roadmap items 8 and 10.

## 1. Purpose

One web console to monitor and operate every SwarmScribe leader deployment —
for example one per customer, region or environment — with a fleet overview
and a drill-down into each leader, taking the same admin actions as
`swarmscribe-admin`.

### Success criteria

- An operator signs in once (Entra ID or Google) and sees every leader they
  hold a role on, with health, queue depth, followers per pool, throughput
  and failures, refreshed at least every 30 seconds.
- Every admin action available in `swarmscribe-admin` can be taken from the
  console, against the right leader, subject to the person's console role for
  that leader AND the leader's own cap for the console.
- Every action is audited in the console and in the leader, naming the person.
- A leader that stops answering is shown as unreachable within one minute;
  nothing else in the console stops working.
- The console never needs inbound connections from leaders, and leaders never
  share a database with it.

### Out of scope (first release)

Alerting and notifications; recording playback, transcript search and sharing
(roadmap items 8 and 10, later); cross-leader bulk actions; multi-tenant
separation inside one console (one console serves one organisation).

## 2. Decisions

| Topic | Decision |
|---|---|
| Scope | Many leader deployments in one console |
| Actions | Monitoring plus every admin CLI action |
| Federation | Central console service polls leaders; drill-down and actions are proxied by it |
| Identity | People sign in to the console; leaders trust the console's own credential for delegated actions |
| Stack | Python FastAPI backend with its own Postgres; React + TypeScript single-page app served by the same service |

## 3. Components

| Component | Responsibility |
|---|---|
| `swarmscribe-console` backend | Sign-in, sessions, console roles, leader registry, poller, history store, action proxy, audit, static files |
| Console web app | Fleet overview, leader drill-down, action dialogs |
| Leader (change) | Console credentials with a role cap; delegated-actor requests; audit "via console" |

## 4. Leader changes

- **Console credentials.** A leader admin runs
  `swarmscribe-admin console create --name <console> --max-role viewer|operator|admin`.
  The leader stores the credential hashed (like follower credentials) with its
  cap and shows it once. `console list|revoke` manage them.
- **Delegated requests.** The console calls `/v1/admin/*` with
  `Authorization: Console <credential>` and headers
  `X-SwarmScribe-Actor: <issuer> <subject> <email>` and
  `X-SwarmScribe-Actor-Role: viewer|operator|admin`. The leader:
  - authenticates the console credential (revoked → 401);
  - computes the effective role as the lower of the asserted role and the
    credential's cap;
  - applies the same role checks as for a signed-in person;
  - audits with actor `<email> (<issuer> <subject>) via console <name>`.
- Console credentials are accepted only on `/v1/admin/*`, never on follower
  routes; follower credentials and ID tokens are unchanged.
- The actor headers are ignored on requests not authenticated as a console.

## 5. Console backend

### 5.1 Data (its own Postgres)

| Table | Holds |
|---|---|
| `leaders` | name (unique), base URL (https), labels (map), enabled, credential (encrypted), added_by |
| `role_grants` | role (viewer/operator/admin), scope (`leader:<name>` or `label:<key>=<value>` or `all`), principal (Entra group id, Google group, email, domain) |
| `console_admins` | principals allowed to manage leaders and grants |
| `snapshots` | per leader: time, reachable, status payload (queue, jobs by state, followers by pool/state, locations with last scan error, throughput) |
| `audit_log` | actor, action, leader, target, outcome, time |

Leader credentials are encrypted at rest (AES-GCM) with a key from
`SWARMSCRIBE_CONSOLE_KEY` (or a KMS key in deployment); never returned by the
API.

### 5.2 Sign-in and roles

- Browser sign-in with OIDC authorization code + PKCE against Entra ID or
  Google (same providers, validation rules and ASCII/Workspace rules as the
  leader spec section 10).
- Session: server-side session id in an `HttpOnly`, `Secure`,
  `SameSite=Strict` cookie, 8-hour lifetime, idle timeout 1 hour; CSRF token
  required on every state-changing request.
- A person's role for a leader is the highest grant whose scope matches the
  leader (by name, by one of its labels, or `all`). Console admins manage the
  registry and grants only; they hold no leader role unless granted one.

### 5.3 Poller

- One loop per console replica, made exclusive per leader with an advisory
  lock (same pattern as the leader's background loops).
- Every 15 s per leader: `GET /v1/admin/status` and the followers/pools
  summary, using the console credential with actor `system:poller` and role
  `viewer`. Timeout 5 s.
- Stores the latest snapshot and keeps 24 hours of history (older rows
  deleted hourly). Three consecutive failures mark the leader unreachable.

### 5.4 Proxy API (for the web app)

- `GET /api/fleet` — leaders the person can see, with their latest snapshot.
- `GET /api/leaders/{name}/...` — read endpoints proxied live to that leader
  (jobs, followers, locations, tokens, consent report), with the person as
  actor and their role.
- `POST /api/leaders/{name}/...` — the admin actions, proxied the same way.
  Join-token plaintext from `tokens create` is passed through to the browser
  once and never stored or logged by the console.
- Leader errors are passed through with their code; the console adds
  `leader_unreachable` (503) when a leader cannot be reached.

## 6. Web app

- **Fleet overview:** a row per leader — reachable/unreachable, queue depth,
  jobs completed last hour/day, failures last day, followers active by pool,
  oldest queued job age, last scan errors — filterable by label; a small
  24-hour throughput chart per leader.
- **Leader drill-down:** tabs for pools and followers (drain/revoke), jobs
  (filter by state; retry/cancel/priority), locations (add/enable/disable,
  ingest), join tokens (create/list/revoke), consent report.
- Actions the person's role does not allow are shown disabled with the
  required role.
- Accessible (WCAG 2.1 AA: keyboard, contrast, labels), responsive down to
  tablet width, light and dark themes.

## 7. Security

- TLS everywhere; leader URLs must be https.
- The console holds leader credentials (encrypted) and session data; it never
  holds people's ID or refresh tokens after sign-in completes.
- Every proxied action is authorised twice: by the console grant, then by the
  leader's cap and role checks.
- Logs never contain cookies, CSRF tokens, leader credentials, join tokens or
  link URLs.
- Content Security Policy restricts scripts to the console's own origin.

## 8. Deployment

- Container image `swarmscribe-console` (backend serving the built web app).
- Helm values: replicas, Postgres URL, console key, OIDC client settings,
  ingress with TLS.
- The console runs anywhere that can reach the leaders over HTTPS; leaders
  need no inbound access from it beyond their normal admin API.

## 9. Testing

- Leader: console credential creation, revocation, role cap
  (asserted admin + cap operator → operator), actor audit format, console
  credential refused on follower routes, actor headers ignored otherwise.
- Console backend: grant matching by leader/label/all; session cookie flags,
  CSRF enforcement, idle timeout; poller with a fake leader (healthy, slow,
  failing three times → unreachable); proxy forwarding actor and role; token
  plaintext passed once and absent from logs; credentials encrypted at rest.
- Web app: component tests for overview and drill-down; end-to-end with
  Playwright against a console and two fake leaders; an automated
  accessibility scan (axe) on every page.
- Compose: console + two real leaders, one leader killed → shown unreachable,
  the other still operable.

## 10. Build plans

1. **C1 — Leader:** console credentials, delegated actor and role cap,
   `swarmscribe-admin console` commands.
2. **C2 — Console backend:** data, sign-in and sessions, grants, poller,
   proxy, audit.
3. **C3 — Web app:** overview, drill-down, actions, accessibility.
4. **C4 — Packaging:** image, Helm values, Compose test.
