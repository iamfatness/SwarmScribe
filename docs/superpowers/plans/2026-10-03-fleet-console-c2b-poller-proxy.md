# Fleet Console C2b — Poller, Fleet View and Action Proxy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The console polls every registered leader's `GET /v1/admin/status` every 15 seconds as `system:poller`/viewer (one replica per leader, through an advisory lock), keeps 24 hours of snapshots, marks a leader unreachable after three consecutive failures and stops polling a leader whose credential was revoked until it is replaced; serves `GET /api/fleet` and a 24-hour history; and proxies an explicit allow-list of leader admin reads and actions with the signed-in person as actor and their console role, passing leader errors through, answering `503 leader_unreachable` when a leader cannot be reached, auditing every action in the console, and passing join-token plaintext to the browser once without storing or logging it.

**Architecture:** `leader_client.LeaderClient` is the only code that talks to leaders: one shared `httpx.AsyncClient` per app, `Authorization: Console <credential>` plus C1's two delegation headers, no redirects, a 16 MiB answer cap and an `asyncio.timeout` around the whole call. `poller.poll_due_leaders` runs every second from the app's lifespan: it picks leaders not polled for 14 s, takes a per-leader Postgres session advisory lock (`run_exclusive` keyed by leader id), re-checks the leader inside the lock, calls the leader, and records a `Snapshot` and the leader's poll state; a separate hourly step prunes old snapshots and ended sessions. `api/fleet.py` joins each visible leader (C2a's grant matching) with its latest successful snapshot. `proxy.py` holds the allow-list (method, leader route template, required console role, audit action, forwarded query names, body or not); `api/proxy.py` is one catch-all route that matches the request against it, refuses below the route's role before contacting the leader, and maps leader answers.

**Tech Stack:** Python 3.11+, uv workspace, FastAPI/Starlette (`StaticFiles`), httpx, SQLAlchemy 2.0 async on Postgres ≥ 14 (`date_bin`), pytest + pytest-asyncio, ruff.

**Spec:** `docs/superpowers/specs/2026-10-03-fleet-console-design.md` (approved; the authority) — sections 5.3 and 5.4 are this plan's scope, with sections 1 (success criteria), 7, 8 and 9 where they apply. The leader-side contract is C1: `docs/superpowers/plans/2026-10-03-fleet-console-c1-leader.md` ("Decisions", "Spec amendments" 1–5) and `docs/superpowers/plans/2026-10-03-fleet-console-c1-followups.md`. This plan builds on C2a: `docs/superpowers/plans/2026-10-03-fleet-console-c2a-console-core.md` — read its "Decisions" first.

**Precondition:** C2a and C1b (`docs/superpowers/plans/2026-10-03-fleet-console-c1b-leader-additions.md`: the `credential_revoked` code and the two status fields) are merged to `main` (so is C1, PR #5), and this work branches from `main`:

```bash
git switch main
git pull
git switch -c fleet-console-c2b
```

Before Task 1, confirm `python -m uv run pytest packages/console -q` passes, that `packages/console/tests/console_testkit.py` and `packages/console/src/swarmscribe_console/leaders.py` exist, that `RevokedConsoleCredential.code == "credential_revoked"` in `packages/leader/src/swarmscribe_leader/auth/consoles.py`, and that the leader's `Status` model has `completed_last_day` and `oldest_queued_age_s`.

## Global Constraints

- Spec 5.3, verbatim: "One loop per console replica, made exclusive per leader with an advisory lock (same pattern as the leader's background loops)"; "Every 15 s per leader: `GET /v1/admin/status` and the followers/pools summary, using the console credential with actor `system:poller` and role `viewer`. Timeout 5 s."; "Stores the latest snapshot and keeps 24 hours of history (older rows deleted hourly). Three consecutive failures mark the leader unreachable."
- C1 amendment 2: followers by pool come from the status call (`follower_pools`), so the poller makes **one** call per leader per poll.
- C1 follow-up: "The console must stop polling a leader once it gets 401 'revoked', and show that leader as revoked." C1 amendment 3: rotation replaces the stored credential in place (C2a `PUT /api/admin/leaders/{name}/credential`).
- C1b (owner ruling 2026-10-03): a revoked credential's 401 has code `credential_revoked`; an unknown credential's has `unauthorized`. The console detects revocation **by that code only**, never by message text.
- C1b (owner ruling 2026-10-03): status carries `completed_last_day` and `oldest_queued_age_s` (null when nothing is queued); spec 6's overview row ("jobs completed last hour/day, failures last day, followers active by pool, oldest queued job age, last scan errors") is built from the status payload alone.
- Spec 5.4, verbatim: "`GET /api/fleet` — leaders the person can see, with their latest snapshot"; "`GET /api/leaders/{name}/...` — read endpoints proxied live to that leader (jobs, followers, locations, tokens, consent report), with the person as actor and their role"; "`POST /api/leaders/{name}/...` — the admin actions, proxied the same way. Join-token plaintext from `tokens create` is passed through to the browser once and never stored or logged by the console."; "Leader errors are passed through with their code; the console adds `leader_unreachable` (503) when a leader cannot be reached."
- C1 header format (amendment 4): `Authorization: Console <credential>`; `X-SwarmScribe-Actor` is `<issuer> <subject> <email>` — single spaces, visible ASCII, `https://` issuer ≤ 255, subject ≤ 255, email ≤ 254 with one `@` or `-` — or exactly `system:poller` (which must send role `viewer`); `X-SwarmScribe-Actor-Role` is exactly `viewer`, `operator` or `admin`. The console sends `-` for a missing or unsendable email and refuses to proxy for an issuer or subject it cannot send.
- Spec 7: "Every proxied action is authorised twice: by the console grant, then by the leader's cap and role checks"; "Logs never contain cookies, CSRF tokens, leader credentials, join tokens or link URLs"; "Content Security Policy restricts scripts to the console's own origin."
- Spec 1: "A leader that stops answering is shown as unreachable within one minute; nothing else in the console stops working."
- The proxy is an explicit allow-list; nothing outside it reaches a leader.
- C2a's rules hold: every `/api` route takes `Person` (CSRF on state-changing methods), errors are `{code, message}`, test helpers live in `console_testkit.py` (never import from `conftest`, never add `tests/__init__.py`).
- Dependency rule: as C2a (`swarmscribe_console` imports `swarmscribe_protocol` and `swarmscribe_leader` as libraries; no leader file changes).
- On this Windows machine `uv` is not on PATH: every command is written `python -m uv run …`.
- If `ruff check` flags import order or line length in code copied from this plan, run `python -m uv run ruff check --fix --select I packages/console` or wrap the line without changing behaviour.
- Commits end with a blank line and `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Do not push.

## Review Focus

Hostile and malformed input the spec implies but does not spell out. Each line names the behaviour a reasonable person expects and the task whose tests pin it.

1. **Path tricks in proxy URLs** (`..` as a leader name, `%2F` inside a name, `%2E%2E` segments after the name, a non-UUID job id, `..%2F` inside a location, routes not on the list such as `consoles`, `whoami` or `v1/admin/status`) → `404`, and the fake leader receives **no** request. — Task 4.
2. **Header injection through actor values** (an email or subject holding `\r\n X-Evil: 1`, spaces, non-ASCII, over-long parts, a non-https issuer) → an unsendable email becomes `-`; an unsendable issuer or subject is `403 actor_not_representable` with no leader call; the leader never sees an extra header; every header the console builds passes C1's own `parse_delegation`. — Task 1, Task 4.
3. **A revoked credential** → the leader is marked revoked once (audited), never polled again until the credential is replaced, shown as `credential_revoked`, and proxied calls answer `503 leader_credential_revoked` without contacting it; a credential rotated while a poll is in flight is not marked by that poll's stale answer; and a 401 that only *says* "revoked" without the code `credential_revoked` (or a 403 with the code, or the code in another case) is not a revocation. — Task 1, Task 2, Task 3, Task 4.
4. **A slow or dead leader** → unreachable within the poll timeout (5 s) or proxy timeout, counted toward the three failures, shown `unreachable` after the third; other leaders keep being polled and proxied. — Task 2, Task 3, Task 4.
5. **Join-token plaintext** → returned once in the `201` body with `Cache-Control: no-store`; absent from every table, every audit entry and every log record at DEBUG level. — Task 4, Task 5.
6. **A leader answering garbage** (non-JSON, a redirect, more than 16 MiB, a status payload of the wrong shape, with absurd or negative numbers, with counts as text, or without C1b's `completed_last_day`/`oldest_queued_age_s`; an error without `{code, message}`) → the poller records `bad_response` and stores no status; the proxy answers `502 bad_gateway`; no redirect is followed. A `null` queue age (nothing queued) is valid and shown as null. — Task 1, Task 2, Task 3, Task 4.
7. **Probing for leaders without a grant** → a leader the person holds no role on is answered byte-for-byte like one that does not exist (`404 leader_not_found`), in the fleet, the history and the proxy. — Task 3, Task 4.
8. **The allow-list drifting from the leader** → a test compares the allow-list with the leader's own `/v1/admin` router (method, path and required role), so a leader route added or changed without the console is a test failure. — Task 4.
9. **Two console replicas** → one poll per leader per interval, never two; a lock held elsewhere skips the leader. — Task 2.
10. **A leader 401 reaching the browser** → never passed through as 401 (the web app would think its own session ended): revoked becomes `503 leader_credential_revoked`, any other 401 `502 leader_credential_rejected`. — Task 4.

## Owner rulings 2026-10-03

- **Shared code: import the leader as a library now** (no separate identity package yet) — owner ruling 2026-10-03. C2b imports `auth.consoles` constants and `background.run_periodically` from it.
- **Private leader addresses are allowed** (loopback, link-local/metadata and localhost stay refused; C4 adds an egress policy) — owner ruling 2026-10-03.
- **Group membership is read at sign-in; up to 8 hours' delay is accepted** — owner ruling 2026-10-03.
- **Revocation code:** the leader answers a revoked credential with `401 credential_revoked` (C1b); the console detects revocation by that code only — owner ruling 2026-10-03.
- **Overview fields:** the leader's status adds `completed_last_day` and `oldest_queued_age_s` (C1b); the console's overview uses them — owner ruling 2026-10-03.
- **The console does not audit proxied reads** (the leader audits them; the console audits every proxied action) — owner ruling 2026-10-03 (accepted with the recommendations).

## Decisions this plan makes (the spec is silent)

- **Cadence.** The poll loop ticks every second (`poll_tick_seconds = 1`). A leader is due when it was last polled at least 14 s ago (`poll_interval_seconds − 1 s`), so each leader is polled every 15 s ± 1 s. Up to 8 leaders are polled at once per replica (`poll_concurrency`). A leader is unreachable after `3 × 15 s + 5 s ≈ 50 s`, inside spec 1's minute.
- **Exclusivity.** Per-leader Postgres session advisory lock with key `(0x53430001 << 32) + leader.id`; the leader row is re-read inside the lock and skipped if another replica polled it in the last 14 s. Pruning uses key `0x53430002 << 32`. The console's own `background.run_exclusive(engine, key: int, work)` copies the leader's lock handling (the leader's version takes only named keys); `run_periodically` is imported from the leader.
- **Snapshots.** One row per poll, success or not. `reachable` is that poll's success; `status` is stored only on success, after validation against the shape of C1's `Status` (counts are integers from 0 to 10¹²; extra fields kept). The fleet's "latest snapshot" is the latest successful one.
- **Poll outcomes.** `ok`; failures counted toward unreachable: `timeout`, `connect_error`, `bad_response`, `credential_rejected` (401 that is not "revoked"), `forbidden` (403), `http_<status>`, `credential_unreadable` (the console key cannot open the stored credential). `credential_revoked` is not a failure: it marks the leader and stops polling.
- **"Revoked" is recognised by the code `credential_revoked` on a 401, and by nothing else** (owner ruling 2026-10-03, built by C1b). Message text is never matched: it is fragile, and no leader without the code runs in production. A 401 with any other code is `credential_rejected`.
- **The status shape requires C1b's two fields:** `completed_last_day` (0 to 10¹²) and `oldest_queued_age_s` (0 to 10¹² or null). An answer without them is `bad_response`, as C1b is a precondition.
- **Overview summary.** Each fleet row carries `summary`, derived from the latest successful snapshot only: `queued` (jobs queued), `completed_last_hour`, `completed_last_day`, `failed_attempts_last_day`, `oldest_queued_age_s` (as of the snapshot's `taken_at`; the web app may add the time since), `followers_active_by_pool` (`{pool: active}`), and `scan_errors` (`[{location, error}]` for locations whose last scan failed). Null when there is no successful snapshot.
- **Health** shown per leader: `disabled` (console-disabled), `credential_revoked`, `unreachable` (3+ consecutive failures), `pending` (never answered yet), else `reachable`, with `last_error` and `consecutive_failures` alongside.
- **A poll answer about an old credential** (rotated while the poll was in flight) is dropped whole: no snapshot is stored and nothing changes on the leader. *Controller ruling, replacing the earlier text ("adds its snapshot but changes nothing"): a snapshot describing the old credential or URL must not appear as the new one's history. `poller.py` and `test_poller.py` pin this; do not "fix" the code back to the earlier text.*
- **Revocation is audited once** as `leader.credential_revoked`, actor `system:poller` (or the person whose proxied call met it).
- **Pruning** runs hourly under its own lock: snapshots older than 24 h, ended sessions, abandoned sign-ins.
- **Fleet:** `GET /api/fleet` lists enabled and disabled leaders the person holds a role on, sorted by name, each with `role`, `health`, poll state, `summary` (above) and `snapshot: {taken_at, status} | null`. **History:** `GET /api/leaders/{name}/history?hours=1..24` returns 5-minute buckets (last snapshot in each bucket) with `reachable`, `queued`, `leased`, `completed_last_hour`, `completed_last_day`, `failed_attempts_last_day`, `oldest_queued_age_s`, `followers_active`. Needs Postgres ≥ 14 (`date_bin`).
- **No grant = not found.** A leader the person holds no role on gets the same `404 leader_not_found` as an unknown name.
- **The allow-list** (console role = the leader's role for that route, so the console refuses first):

  | Method | Leader route (`/v1/admin/…`) | Role | Audit action | Query passed on | Body |
  |---|---|---|---|---|---|
  | GET | `status` | viewer | — | — | — |
  | GET | `locations` | viewer | — | — | — |
  | GET | `jobs` | viewer | — | `state`, `location`, `limit` | — |
  | GET | `followers` | viewer | — | `state` | — |
  | GET | `tokens` | admin | — | — | — |
  | GET | `consent/report` | viewer | — | `location`, `limit` | — |
  | POST | `locations` | admin | `locations.add` | — | yes |
  | POST | `locations/{location}/enable` | admin | `locations.enable` | — | — |
  | POST | `locations/{location}/disable` | admin | `locations.disable` | — | — |
  | POST | `locations/{location}/ingest` | operator | `locations.ingest` | — | — |
  | POST | `jobs/{job_id}/retry` | operator | `jobs.retry` | — | — |
  | POST | `jobs/{job_id}/cancel` | operator | `jobs.cancel` | — | — |
  | POST | `jobs/{job_id}/priority` | operator | `jobs.priority` | — | yes |
  | POST | `followers/{follower_id}/drain` | operator | `followers.drain` | — | — |
  | POST | `followers/{follower_id}/revoke` | admin | `followers.revoke` | — | — |
  | POST | `tokens` | admin | `tokens.create` | — | yes |
  | POST | `tokens/{token_id}/revoke` | admin | `tokens.revoke` | — | — |

  `{job_id}`, `{follower_id}`, `{token_id}` must be UUIDs; `{location}` must match the leader's name pattern. Not offered: `whoami` and `login-config` (the console's own concerns) and `consoles…` (person-only on the leader).
- **Query parameters:** only the listed names, each at most once (twice is `422`), at most 200 printable characters. **Bodies:** only on body routes; a JSON object of at most 64 KiB (`413 too_large` above; `422` for anything but an object).
- **Proxy timeout 10 s** (`proxy_timeout_seconds`); the 5 s poll timeout is the spec's and applies to the poller only.
- **Proxied answers:** 2xx passes through with its status and JSON body; a 4xx/5xx carrying `{code, message}` (code `[a-z][a-z0-9_]{0,63}`) passes through with its status, code, message (≤ 500 characters) and a numeric `Retry-After` (≤ 3600); anything else is `502 bad_gateway`. A leader 401 is never passed through (Review Focus 10). Unreachable is `503 leader_unreachable` with `Retry-After: 15`. A console-disabled leader is `409 leader_disabled`; an unreadable stored credential `503 leader_credential_unreadable`.
- **Audit:** every proxied `POST` writes one console audit entry: actor, action (table above), leader name, target (`job_id=…` etc.), outcome (`ok` or the error code), detail `{role}` plus, for `tokens.create`, the token's `id` and `pool` — never the plaintext. Proxied reads are not audited in the console (owner ruling above).
- **Static files** (`SWARMSCRIBE_CONSOLE_STATIC_DIR`, a folder holding `index.html`): served at `/` under the C2a security headers; an extensionless path outside `/api/` and `/auth/` that is not a file gets `index.html` (the web app's own routes).
- **Credential sealing context.** C2a seals a leader's credential bound to its name and normalised URL. Every `open_credential` call in this plan passes `leaders.sealing_context(row.name, row.base_url)`, read from the same row as the sealed bytes; the bare name raises `CredentialUnreadable`. A leader's URL change comes with the credential (C2a `credential_required`), so the sealed value always matches the row, and it resets `consecutive_failures`, `last_error` and `last_polled_at` and clears `credential_revoked_at`: the poller's health and revoked tests start from that state after an edit.
- **Edit `create_app`, never replace it.** Task 2's `app.py` changes are edits to C2a's file (see Task 2, Step 6). `assert_guarded(app.routes)` stays the last statement, after every router and the static mount, and nothing that contains `/api` routes is mounted under `/`.

## File Structure

```
packages/console/src/swarmscribe_console/
  config.py               MOD  poll/proxy settings (Task 1); static_dir (Task 5)
  errors.py               MOD  leader-facing errors (Task 1)
  leader_client.py        NEW  actor headers, LeaderClient, is_revoked (Task 1)
  background.py           NEW  run_exclusive by numeric key (Task 2)
  poller.py               NEW  poll_leader, poll_due_leaders, mark_revoked, prune (Task 2)
  app.py                  MOD  leader client, background loops (Task 2); fleet (Task 3); proxy (Task 4); static (Task 5)
  api/fleet.py            NEW  /api/fleet, history, leader_for (Task 3)
  proxy.py                NEW  the allow-list (Task 4)
  api/proxy.py            NEW  the catch-all proxy route (Task 4)
  static.py               NEW  SpaFiles (Task 5)
packages/console/tests/
  console_testkit.py      MOD  STATUS, FakeLeader (Task 1)
  conftest.py             MOD  fake_leader, leader_client (Task 1); app fixture (Task 2); Factory.snapshot (Task 3)
  test_leader_client.py   NEW  (Task 1)
  test_poller.py          NEW  (Task 2)
  test_fleet.py           NEW  (Task 3)
  test_proxy.py           NEW  (Task 4)
  test_static_and_logs.py NEW  (Task 5)
README.md                 MOD  leaders, rotation, poller, proxy (Task 5)
```

---

### Task 1: The leader client — C1 headers, timeouts, no redirects

**Files:**
- Modify: `packages/console/src/swarmscribe_console/config.py`, `packages/console/src/swarmscribe_console/errors.py`
- Create: `packages/console/src/swarmscribe_console/leader_client.py`
- Modify: `packages/console/tests/console_testkit.py`, `packages/console/tests/conftest.py`
- Test: `packages/console/tests/test_leader_client.py`

**Interfaces:**
- Consumes: `errors.Forbidden`, `ConsoleError`, `Unavailable` (C2a Task 1); from the leader: `auth.consoles.POLLER_ACTOR`, `MAX_ISSUER_CHARS`, `MAX_SUBJECT_CHARS`, `MAX_EMAIL_CHARS` (C1).
- Produces:
  - `Settings` fields `poll_interval_seconds: float = 15.0`, `poll_timeout_seconds: float = 5.0`, `poll_tick_seconds: float = 1.0`, `poll_concurrency: int = 8`, `unreachable_after_failures: int = 3`, `history_hours: int = 24`, `prune_interval_seconds: float = 3600.0`, `proxy_timeout_seconds: float = 10.0`.
  - `errors.LeaderUnavailable` (503 `leader_unreachable`, `retry_after=15`), `CredentialRevoked` (503 `leader_credential_revoked`), `CredentialRejected` (502 `leader_credential_rejected`), `CredentialUnreadableError` (503 `leader_credential_unreadable`), `BadGateway` (502 `bad_gateway`), `PayloadTooLarge` (413 `too_large`), `PassedThrough(status, code, message, retry_after=None)`.
  - `leader_client.person_actor(issuer: str, subject: str, email: str | None) -> str` (raises `ActorNotRepresentable`, 403 `actor_not_representable`); `POLLER_ACTOR` (re-exported); `REVOKED_CODE = "credential_revoked"`; `MAX_BODY_BYTES = 16 MiB`.
  - `leader_client.LeaderTarget(name, base_url, credential)` (credential hidden from repr); `LeaderReply(status: int, body: Any, retry_after: str | None)`; exceptions `LeaderUnreachable(reason)` (`reason` is `"timeout"` or `"connect_error"`) and `LeaderBadAnswer`.
  - `leader_client.LeaderClient(transport=None)` with `async call(target, method, path, *, actor, role, timeout, params=None, json_body=None) -> LeaderReply` and `async aclose()`.
  - `leader_client.is_revoked(reply: LeaderReply) -> bool` (a 401 whose code is `credential_revoked`; nothing else).
  - `console_testkit.STATUS` (a C1 `Status` body), `console_testkit.FakeLeader` (`requests`, `bodies`, `modes[host]` in `ok|slow|down|revoked|unknown`, `delay`, `replies[(method, path)] = (status, body, headers)`, `status`, `on_request`, `transport`); fixtures `fake_leader`, `leader_client`.

- [ ] **Step 1: Add the settings and errors**

In `packages/console/src/swarmscribe_console/config.py`, add after `login_attempt_seconds`:

```python
    # The poller and the proxy (fleet console spec 5.3, 5.4).
    poll_interval_seconds: float = Field(default=15.0, gt=0)
    poll_timeout_seconds: float = Field(default=5.0, gt=0)
    poll_tick_seconds: float = Field(default=1.0, gt=0)
    poll_concurrency: int = Field(default=8, gt=0)
    unreachable_after_failures: int = Field(default=3, gt=0)
    history_hours: int = Field(default=24, gt=0, le=24 * 7)
    prune_interval_seconds: float = Field(default=3600.0, gt=0)
    proxy_timeout_seconds: float = Field(default=10.0, gt=0)
```

Append to `packages/console/src/swarmscribe_console/errors.py`:

```python
class PayloadTooLarge(ConsoleError):
    status = 413
    code = "too_large"


class BadGateway(ConsoleError):
    """A leader answered something the console cannot pass on."""

    status = 502
    code = "bad_gateway"


class CredentialRejected(ConsoleError):
    """The leader does not know the stored credential (a 401 that is not "revoked")."""

    status = 502
    code = "leader_credential_rejected"


class LeaderUnavailable(Unavailable):
    status = 503
    code = "leader_unreachable"
    retry_after = 15


class CredentialRevoked(ConsoleError):
    """The leader revoked this console's credential; a console administrator must replace
    it (PUT /api/admin/leaders/{name}/credential)."""

    status = 503
    code = "leader_credential_revoked"


class CredentialUnreadableError(ConsoleError):
    status = 503
    code = "leader_credential_unreadable"


class PassedThrough(ConsoleError):
    """A leader's own error, passed to the browser with its status and code."""

    def __init__(self, status: int, code: str, message: str, retry_after: int | None = None):
        super().__init__(message, code=code)
        self.status = status
        self.retry_after = retry_after
```

- [ ] **Step 2: Add the fake leader**

Append to `packages/console/tests/console_testkit.py` (add `import asyncio`, `import copy` and `from collections.abc import Awaitable, Callable` and `from typing import Any` to its imports):

```python
STATUS = {
    "jobs": {"queued": 3, "leased": 1, "completed": 10, "failed": 0, "cancelled": 0},
    "pools": [{"pool": "default", "queued": 3, "leased": 1}],
    "followers": {"active": 2, "draining": 0, "revoked": 0, "gone": 0},
    "follower_pools": [{"pool": "default", "active": 2, "draining": 0, "revoked": 0, "gone": 0}],
    "completed_last_hour": 7,
    "completed_last_day": 30,
    "oldest_queued_age_s": 420,
    "failed_attempts_last_day": 1,
    "locations": [],
}
# C1 + C1b: a revoked credential's code is credential_revoked; an unknown one's unauthorized.
REVOKED_BODY = {
    "code": "credential_revoked",
    "message": "this console credential has been revoked",
}
UNKNOWN_BODY = {"code": "unauthorized", "message": "unknown console credential"}


class FakeLeader:
    """Leaders as the console sees them over HTTPS, told apart by host. Records every
    request. `modes[host]` makes a host slow, down, revoked or unknown-credential;
    `replies[(method, path)] = (status, body, headers)` programs an answer (a dict or list
    body is JSON, bytes are sent as they are); otherwise GET .../v1/admin/status answers
    `status` and anything else is a leader-style 404."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.bodies: list[bytes] = []
        self.modes: dict[str, str] = {}
        self.replies: dict[tuple[str, str], tuple[int, Any, dict[str, str]]] = {}
        self.status: dict[str, Any] = copy.deepcopy(STATUS)
        self.delay = 1.0
        self.on_request: Callable[[httpx.Request], Awaitable[None]] | None = None

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.bodies.append(await request.aread())
        if self.on_request is not None:
            await self.on_request(request)
        mode = self.modes.get(request.url.host, "ok")
        if mode == "down":
            raise httpx.ConnectError("connection refused", request=request)
        if mode == "slow":
            await asyncio.sleep(self.delay)
        challenge = {"WWW-Authenticate": 'Console error="invalid_token"'}
        if mode == "revoked":
            return httpx.Response(401, json=REVOKED_BODY, headers=challenge)
        if mode == "unknown":
            return httpx.Response(401, json=UNKNOWN_BODY, headers=challenge)
        reply = self.replies.get((request.method, request.url.path))
        if reply is not None:
            status, body, headers = reply
            if isinstance(body, bytes):
                return httpx.Response(status, content=body, headers=headers)
            return httpx.Response(status, json=body, headers=headers)
        if request.method == "GET" and request.url.path.endswith("/v1/admin/status"):
            return httpx.Response(200, json=self.status)
        return httpx.Response(404, json={"code": "not_found", "message": "Not Found"})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)
```

In `packages/console/tests/conftest.py`, add `FakeLeader` to the `console_testkit` import list, add `from swarmscribe_console.leader_client import LeaderClient  # noqa: E402`, and append:

```python
@pytest.fixture
def fake_leader():
    return FakeLeader()


@pytest.fixture
async def leader_client(fake_leader):
    made = LeaderClient(transport=fake_leader.transport)
    yield made
    await made.aclose()
```

- [ ] **Step 3: Write the failing tests**

Create `packages/console/tests/test_leader_client.py`:

```python
import logging
import time

import pytest
from swarmscribe_console.leader_client import (
    MAX_BODY_BYTES,
    POLLER_ACTOR,
    ActorNotRepresentable,
    LeaderBadAnswer,
    LeaderReply,
    LeaderTarget,
    LeaderUnreachable,
    is_revoked,
    person_actor,
)
from swarmscribe_leader.auth.consoles import parse_delegation

ISSUER = "https://login.microsoftonline.com/0f0e0d0c-0b0a-4908-8706-050403020100/v2.0"
CREDENTIAL = "k" * 21 + "_" + "Q" * 21
HOST = "eu-1.leaders.example"
TARGET = LeaderTarget(name="eu-1", base_url=f"https://{HOST}", credential=CREDENTIAL)


# --- who the console says it acts for ---------------------------------------------------


def test_a_person_is_sent_as_issuer_subject_and_email():
    actor = person_actor(ISSUER, "entra-person-1", "person@example.org")
    assert actor == f"{ISSUER} entra-person-1 person@example.org"
    who, role = parse_delegation([actor], ["operator"])
    assert (who.issuer, who.subject, who.email, role) == (
        ISSUER,
        "entra-person-1",
        "person@example.org",
        "operator",
    )


@pytest.mark.parametrize(
    "email",
    [
        None,
        "",
        "pérson@example.org",
        "per son@example.org",
        "a@b@example.org",
        "no-at-sign",
        "x\r\nX-Evil: 1@example.org",
        "a" * 250 + "@x.org",
    ],
    ids=["none", "empty", "non-ascii", "space", "two-at", "no-at", "crlf", "too-long"],
)
def test_an_email_the_leader_would_refuse_is_sent_as_a_dash(email):
    actor = person_actor(ISSUER, "sub-1", email)
    assert actor == f"{ISSUER} sub-1 -"
    who, _ = parse_delegation([actor], ["viewer"])
    assert who.email is None


@pytest.mark.parametrize(
    ("issuer", "subject"),
    [
        ("http://issuer.example", "s"),
        ("https://", "s"),
        ("https://iss uer.example", "s"),
        (ISSUER, "has space"),
        (ISSUER, "sübject"),
        (ISSUER, ""),
        (ISSUER, "s" * 256),
        (ISSUER, "s\r\nX-Evil: 1"),
        ("https://" + "i" * 250, "s"),
    ],
    ids=[
        "http-issuer",
        "bare-https",
        "issuer-space",
        "subject-space",
        "subject-non-ascii",
        "empty-subject",
        "long-subject",
        "subject-crlf",
        "long-issuer",
    ],
)
def test_an_identity_the_leader_cannot_take_is_refused(issuer, subject):
    with pytest.raises(ActorNotRepresentable) as raised:
        person_actor(issuer, subject, "a@example.org")
    assert (raised.value.status, raised.value.code) == (403, "actor_not_representable")


def test_the_poller_actor_is_c1s():
    who, role = parse_delegation([POLLER_ACTOR], ["viewer"])
    assert who.is_poller and role == "viewer"


# --- calls ------------------------------------------------------------------------------


async def test_a_call_sends_the_console_credential_and_the_delegation(leader_client, fake_leader):
    fake_leader.replies[("GET", "/v1/admin/jobs")] = (200, [{"id": "j-1"}], {})
    actor = person_actor(ISSUER, "entra-person-1", "person@example.org")
    reply = await leader_client.call(
        TARGET,
        "GET",
        "/v1/admin/jobs",
        actor=actor,
        role="operator",
        timeout=2,
        params={"state": "queued"},
    )
    assert (reply.status, reply.body) == (200, [{"id": "j-1"}])
    (request,) = fake_leader.requests
    assert str(request.url) == f"https://{HOST}/v1/admin/jobs?state=queued"
    assert request.headers["authorization"] == f"Console {CREDENTIAL}"
    assert request.headers["x-swarmscribe-actor"] == actor
    assert request.headers["x-swarmscribe-actor-role"] == "operator"
    parse_delegation(
        request.headers.get_list("x-swarmscribe-actor"),
        request.headers.get_list("x-swarmscribe-actor-role"),
    )


async def test_a_json_body_is_sent_as_json(leader_client, fake_leader):
    await leader_client.call(
        TARGET,
        "POST",
        "/v1/admin/jobs/x/priority",
        actor=POLLER_ACTOR,
        role="viewer",
        timeout=2,
        json_body={"priority": 5},
    )
    assert fake_leader.bodies[0] in (b'{"priority": 5}', b'{"priority":5}')
    assert fake_leader.requests[0].headers["content-type"] == "application/json"


async def test_a_leader_under_a_path_prefix_is_called_under_it(leader_client, fake_leader):
    target = LeaderTarget("eu-1", f"https://{HOST}/swarmscribe", CREDENTIAL)
    reply = await leader_client.call(
        target, "GET", "/v1/admin/status", actor=POLLER_ACTOR, role="viewer", timeout=2
    )
    assert reply.status == 200
    assert fake_leader.requests[0].url.path == "/swarmscribe/v1/admin/status"


async def test_a_redirect_is_never_followed(leader_client, fake_leader):
    fake_leader.replies[("GET", "/v1/admin/status")] = (
        302,
        b"",
        {"Location": "https://elsewhere.example/steal"},
    )
    reply = await leader_client.call(
        TARGET, "GET", "/v1/admin/status", actor=POLLER_ACTOR, role="viewer", timeout=2
    )
    assert reply.status == 302
    assert len(fake_leader.requests) == 1


async def test_a_slow_leader_is_unreachable_once_the_timeout_passes(leader_client, fake_leader):
    fake_leader.modes[HOST] = "slow"
    fake_leader.delay = 2.0
    started = time.monotonic()
    with pytest.raises(LeaderUnreachable) as raised:
        await leader_client.call(
            TARGET, "GET", "/v1/admin/status", actor=POLLER_ACTOR, role="viewer", timeout=0.2
        )
    assert raised.value.reason == "timeout"
    assert time.monotonic() - started < 1.5


async def test_a_refused_connection_is_unreachable(leader_client, fake_leader):
    fake_leader.modes[HOST] = "down"
    with pytest.raises(LeaderUnreachable) as raised:
        await leader_client.call(
            TARGET, "GET", "/v1/admin/status", actor=POLLER_ACTOR, role="viewer", timeout=2
        )
    assert raised.value.reason == "connect_error"


async def test_an_oversized_answer_is_refused(leader_client, fake_leader):
    fake_leader.replies[("GET", "/v1/admin/status")] = (
        200,
        b"[" + b"0," * (MAX_BODY_BYTES // 2) + b"0]",
        {"Content-Type": "application/json"},
    )
    with pytest.raises(LeaderBadAnswer):
        await leader_client.call(
            TARGET, "GET", "/v1/admin/status", actor=POLLER_ACTOR, role="viewer", timeout=10
        )


async def test_a_non_json_answer_has_no_body(leader_client, fake_leader):
    fake_leader.replies[("GET", "/v1/admin/status")] = (
        500,
        b"<html>oops</html>",
        {"Content-Type": "text/html"},
    )
    reply = await leader_client.call(
        TARGET, "GET", "/v1/admin/status", actor=POLLER_ACTOR, role="viewer", timeout=2
    )
    assert (reply.status, reply.body) == (500, None)


REVOKED_TEXT = "this console credential has been revoked"


@pytest.mark.parametrize(
    ("reply", "revoked"),
    [
        (LeaderReply(401, {"code": "credential_revoked", "message": REVOKED_TEXT}, None), True),
        (LeaderReply(401, {"code": "credential_revoked", "message": "other"}, None), True),
        (LeaderReply(401, {"code": "unauthorized", "message": REVOKED_TEXT}, None), False),
        (LeaderReply(401, {"code": "unauthorized", "message": "unknown"}, None), False),
        (LeaderReply(403, {"code": "credential_revoked", "message": REVOKED_TEXT}, None), False),
        (LeaderReply(401, {"code": "CREDENTIAL_REVOKED", "message": "x"}, None), False),
        (LeaderReply(401, ["credential_revoked"], None), False),
        (LeaderReply(401, None, None), False),
    ],
    ids=[
        "c1b",
        "code-any-message",
        "message-alone",
        "unknown",
        "not-401",
        "other-case",
        "not-an-object",
        "no-body",
    ],
)
def test_revocation_is_recognised_by_the_code_only(reply, revoked):
    assert is_revoked(reply) is revoked


async def test_the_credential_never_reaches_the_logs(leader_client, fake_leader, caplog):
    caplog.set_level(logging.DEBUG)
    await leader_client.call(
        TARGET, "GET", "/v1/admin/status", actor=POLLER_ACTOR, role="viewer", timeout=2
    )
    fake_leader.modes[HOST] = "down"
    with pytest.raises(LeaderUnreachable):
        await leader_client.call(
            TARGET, "GET", "/v1/admin/status", actor=POLLER_ACTOR, role="viewer", timeout=2
        )
    assert CREDENTIAL not in caplog.text
    assert CREDENTIAL not in repr(TARGET)
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `python -m uv run pytest packages/console/tests/test_leader_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'swarmscribe_console.leader_client'` (raised by conftest).

- [ ] **Step 5: Write the leader client**

Create `packages/console/src/swarmscribe_console/leader_client.py`:

```python
"""Calls from the console to a leader's /v1/admin API, in C1's contract.

Every call carries `Authorization: Console <credential>`, `X-SwarmScribe-Actor` and
`X-SwarmScribe-Actor-Role` in exactly the form the leader's parse_delegation accepts:
`<issuer> <subject> <email>` in visible ASCII separated by single spaces (the email `-` when
the person has none the leader would take), or `system:poller` with role viewer. Redirects
are never followed, answers over MAX_BODY_BYTES are refused, and a call that has not finished
within its timeout is unreachable whatever the transport does. Nothing here logs."""

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any

import httpx
from swarmscribe_leader.auth.consoles import (
    MAX_EMAIL_CHARS,
    MAX_ISSUER_CHARS,
    MAX_SUBJECT_CHARS,
    POLLER_ACTOR,
)

from .errors import Forbidden

__all__ = [
    "MAX_BODY_BYTES",
    "POLLER_ACTOR",
    "REVOKED_CODE",
    "ActorNotRepresentable",
    "LeaderBadAnswer",
    "LeaderClient",
    "LeaderReply",
    "LeaderTarget",
    "LeaderUnreachable",
    "is_revoked",
    "person_actor",
]

MAX_BODY_BYTES = 16 * 1024 * 1024
# C1b: a revoked console credential's 401 carries this code (an unknown one: unauthorized).
# Owner ruling 2026-10-03: the code alone decides; message text is never matched.
REVOKED_CODE = "credential_revoked"
_VISIBLE = re.compile(r"[!-~]+")
_EMAIL = re.compile(r"[^@]+@[^@]+")


class ActorNotRepresentable(Forbidden):
    code = "actor_not_representable"


def person_actor(issuer: str, subject: str, email: str | None) -> str:
    """X-SwarmScribe-Actor for a signed-in person. An email the leader would refuse is sent
    as `-`; an issuer or subject it would refuse cannot be sent at all."""
    if not (
        issuer.startswith("https://")
        and issuer != "https://"
        and len(issuer) <= MAX_ISSUER_CHARS
        and _VISIBLE.fullmatch(issuer)
    ):
        raise ActorNotRepresentable(
            "your identity provider's issuer cannot be sent to a leader"
        )
    if not (len(subject) <= MAX_SUBJECT_CHARS and _VISIBLE.fullmatch(subject)):
        raise ActorNotRepresentable("your account's identifier cannot be sent to a leader")
    sendable = (
        email is not None
        and len(email) <= MAX_EMAIL_CHARS
        and _VISIBLE.fullmatch(email) is not None
        and _EMAIL.fullmatch(email) is not None
    )
    return f"{issuer} {subject} {email if sendable else '-'}"


@dataclass(frozen=True)
class LeaderTarget:
    name: str
    base_url: str
    credential: str = field(repr=False)


@dataclass(frozen=True)
class LeaderReply:
    status: int
    body: Any  # the parsed JSON answer, or None when it was not JSON
    retry_after: str | None


class LeaderUnreachable(Exception):
    """The leader did not answer: `reason` is "timeout" or "connect_error"."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class LeaderBadAnswer(Exception):
    """The leader answered more than MAX_BODY_BYTES."""


class LeaderClient:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self._client = httpx.AsyncClient(
            transport=transport, follow_redirects=False, timeout=httpx.Timeout(None)
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def call(
        self,
        target: LeaderTarget,
        method: str,
        path: str,
        *,
        actor: str,
        role: str,
        timeout: float,
        params: dict[str, str] | None = None,
        json_body: Any = None,
    ) -> LeaderReply:
        headers = {
            "Authorization": f"Console {target.credential}",
            "X-SwarmScribe-Actor": actor,
            "X-SwarmScribe-Actor-Role": role,
            "Accept": "application/json",
        }
        chunks: list[bytes] = []
        try:
            async with asyncio.timeout(timeout):
                async with self._client.stream(
                    method,
                    target.base_url + path,
                    headers=headers,
                    params=params,
                    json=json_body,
                    timeout=timeout,
                ) as response:
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > MAX_BODY_BYTES:
                            raise LeaderBadAnswer(f"leader {target.name} answered too much")
                        chunks.append(chunk)
        except TimeoutError:
            raise LeaderUnreachable("timeout") from None
        except httpx.TimeoutException:
            raise LeaderUnreachable("timeout") from None
        except httpx.HTTPError:
            raise LeaderUnreachable("connect_error") from None
        raw = b"".join(chunks)
        body: Any = None
        if raw and "json" in response.headers.get("content-type", ""):
            try:
                body = json.loads(raw)
            except ValueError:
                body = None
        return LeaderReply(response.status_code, body, response.headers.get("retry-after"))


def is_revoked(reply: LeaderReply) -> bool:
    """A 401 meaning this console's credential was revoked (C1b), as opposed to unknown."""
    body = reply.body
    return reply.status == 401 and isinstance(body, dict) and body.get("code") == REVOKED_CODE
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m uv run pytest packages/console/tests/test_leader_client.py -v`
Expected: PASS.

Run: `python -m uv run ruff check packages/console`
Expected: no findings.

- [ ] **Step 7: Commit**

```bash
git add packages/console/src/swarmscribe_console/config.py packages/console/src/swarmscribe_console/errors.py packages/console/src/swarmscribe_console/leader_client.py packages/console/tests/console_testkit.py packages/console/tests/conftest.py packages/console/tests/test_leader_client.py
git commit -m "Console: leader client speaking C1's delegation headers, with timeouts

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The poller — every 15 s per leader, exclusive, revoked stops it

**Files:**
- Create: `packages/console/src/swarmscribe_console/background.py`, `packages/console/src/swarmscribe_console/poller.py`
- Modify: `packages/console/src/swarmscribe_console/app.py`
- Modify: `packages/console/tests/conftest.py` (the `app` fixture)
- Test: `packages/console/tests/test_poller.py`

**Interfaces:**
- Consumes: `Leader`, `Snapshot` (C2a Task 2); `ConsoleKeys`, `CredentialUnreadable` (C2a Task 1); `leaders.sealing_context(name, base_url)` (C2a Task 6: the context every `open_credential` call passes, never the bare name); `audit.record` (C2a Task 2); `sessions.prune_expired` (C2a Task 4); `leaders.replace_credential` (C2a Task 6, tests only); `LeaderClient`, `LeaderTarget`, `LeaderUnreachable`, `LeaderBadAnswer`, `is_revoked`, `POLLER_ACTOR` (Task 1); `swarmscribe_leader.background.run_periodically`.
- Produces:
  - `background.run_exclusive(engine, key: int, work: Callable[[], Awaitable[None]]) -> bool`.
  - `poller.POLL_LOCK_BASE = 0x53430001 << 32`, `poller.PRUNE_LOCK = 0x53430002 << 32`, `poller.STATUS_PATH = "/v1/admin/status"`.
  - `poller.PollerConfig(interval: timedelta, timeout: float, unreachable_after: int, history: timedelta, concurrency: int)` with `from_settings(settings)`.
  - `async poller.poll_leader(sessionmaker, client, keys, leader_id, *, now, config) -> str` (an outcome, or `"skipped"`).
  - `async poller.poll_due_leaders(engine, sessionmaker, client, keys, *, now, config) -> dict[str, str]` (leader name → outcome, `"locked"` when another replica holds it).
  - `async poller.mark_revoked(session, leader, sealed: bytes, *, now, actor) -> bool`.
  - `async poller.prune(sessionmaker, *, now, history, session_idle) -> int`.
  - `app.create_app(settings, *, background=True, fetch=None, idp_transport=None, graph=None, google_groups=None, leader_transport=None)`; `app.state.leader_client`, `app.state.poller_config`.

- [ ] **Step 1: Point the test app at the fake leader, without background loops**

In `packages/console/tests/conftest.py`, replace the `app` fixture with:

```python
@pytest.fixture
async def app(engine, make_settings, idp, graph, google_groups, fake_leader):
    application = create_app(
        make_settings(),
        background=False,
        fetch=idp.fetch,
        idp_transport=idp.transport,
        graph=graph,
        google_groups=google_groups,
        leader_transport=fake_leader.transport,
    )
    async with application.router.lifespan_context(application):
        yield application
```

- [ ] **Step 2: Write the failing tests**

Create `packages/console/tests/test_poller.py`:

```python
import asyncio
import dataclasses
from datetime import timedelta

import pytest
from console_testkit import CREDENTIAL, STATUS
from sqlalchemy import select, text
from swarmscribe_console.app import create_app
from swarmscribe_console.crypto import ConsoleKeys
from swarmscribe_console.db.models import AuditEntry, ConsoleSession, Leader, Snapshot
from swarmscribe_console.leaders import replace_credential, sealing_context
from swarmscribe_console.poller import POLL_LOCK_BASE, PollerConfig, poll_due_leaders, prune
from swarmscribe_leader.auth.consoles import parse_delegation
from swarmscribe_leader.clock import utcnow

HOST = "eu-1.leaders.example"
ROTATED = "R" * 43
CONFIG = PollerConfig(
    interval=timedelta(seconds=15),
    timeout=0.2,
    unreachable_after=3,
    history=timedelta(hours=24),
    concurrency=8,
)


@pytest.fixture
def poll(engine, sessionmaker, keys, leader_client):
    async def run(now, **overrides):
        config = dataclasses.replace(CONFIG, **overrides)
        return await poll_due_leaders(
            engine, sessionmaker, leader_client, keys, now=now, config=config
        )

    return run


async def _leader(sessionmaker, name="eu-1") -> Leader:
    async with sessionmaker() as session:
        return (await session.scalars(select(Leader).where(Leader.name == name))).one()


async def _snapshots(sessionmaker) -> list[Snapshot]:
    async with sessionmaker() as session:
        return list((await session.scalars(select(Snapshot).order_by(Snapshot.id))).all())


async def test_a_healthy_leader_is_polled_as_the_poller_and_its_status_kept(
    poll, factory, fake_leader, sessionmaker
):
    await factory.leader("eu-1")
    now = utcnow()
    assert await poll(now) == {"eu-1": "ok"}
    (request,) = fake_leader.requests
    assert str(request.url) == f"https://{HOST}/v1/admin/status"
    assert request.headers["authorization"] == f"Console {CREDENTIAL}"
    assert request.headers["x-swarmscribe-actor"] == "system:poller"
    assert request.headers["x-swarmscribe-actor-role"] == "viewer"
    who, role = parse_delegation(
        request.headers.get_list("x-swarmscribe-actor"),
        request.headers.get_list("x-swarmscribe-actor-role"),
    )
    assert who.is_poller and role == "viewer"
    (snapshot,) = await _snapshots(sessionmaker)
    assert (snapshot.reachable, snapshot.outcome, snapshot.taken_at) == (True, "ok", now)
    assert snapshot.status == STATUS
    assert snapshot.status["follower_pools"][0]["pool"] == "default"
    leader = await _leader(sessionmaker)
    assert (leader.last_polled_at, leader.last_success_at) == (now, now)
    assert (leader.consecutive_failures, leader.last_error) == (0, None)


async def test_a_leader_is_polled_at_most_once_per_interval(poll, factory, fake_leader):
    await factory.leader("eu-1")
    now = utcnow()
    assert await poll(now) == {"eu-1": "ok"}
    assert await poll(now + timedelta(seconds=5)) == {}
    assert await poll(now + timedelta(seconds=14)) == {"eu-1": "ok"}
    assert len(fake_leader.requests) == 2


async def test_a_slow_leader_times_out_as_a_failure(poll, factory, fake_leader, sessionmaker):
    await factory.leader("eu-1")
    fake_leader.modes[HOST] = "slow"
    fake_leader.delay = 2.0
    loop = asyncio.get_running_loop()
    started = loop.time()
    assert await poll(utcnow()) == {"eu-1": "timeout"}
    assert loop.time() - started < 1.5
    (snapshot,) = await _snapshots(sessionmaker)
    assert (snapshot.reachable, snapshot.outcome, snapshot.status) == (False, "timeout", None)
    assert (await _leader(sessionmaker)).consecutive_failures == 1


async def test_three_failures_in_a_row_count_and_one_success_clears_them(
    poll, factory, fake_leader, sessionmaker
):
    await factory.leader("eu-1")
    fake_leader.modes[HOST] = "down"
    start = utcnow()
    for step in range(3):
        outcome = await poll(start + timedelta(seconds=15 * step))
        assert outcome == {"eu-1": "connect_error"}
    leader = await _leader(sessionmaker)
    assert (leader.consecutive_failures, leader.last_error) == (3, "connect_error")
    assert leader.last_success_at is None
    del fake_leader.modes[HOST]
    assert await poll(start + timedelta(seconds=45)) == {"eu-1": "ok"}
    assert (await _leader(sessionmaker)).consecutive_failures == 0


def _without(field: str) -> dict:
    return {key: value for key, value in STATUS.items() if key != field}


async def test_nothing_queued_is_a_valid_status(poll, factory, fake_leader, sessionmaker):
    await factory.leader("eu-1")
    fake_leader.status["oldest_queued_age_s"] = None
    assert await poll(utcnow()) == {"eu-1": "ok"}
    (snapshot,) = await _snapshots(sessionmaker)
    assert snapshot.status["oldest_queued_age_s"] is None
    assert snapshot.status["completed_last_day"] == 30


@pytest.mark.parametrize(
    ("reply", "outcome"),
    [
        ((200, {"hello": 1}, {}), "bad_response"),
        ((200, {**STATUS, "completed_last_hour": -1}, {}), "bad_response"),
        ((200, {**STATUS, "completed_last_hour": 10**15}, {}), "bad_response"),
        ((200, _without("completed_last_day"), {}), "bad_response"),
        ((200, _without("oldest_queued_age_s"), {}), "bad_response"),
        ((200, {**STATUS, "oldest_queued_age_s": -5}, {}), "bad_response"),
        ((200, {**STATUS, "oldest_queued_age_s": "420"}, {}), "bad_response"),
        ((200, b"not json", {"Content-Type": "text/plain"}), "bad_response"),
        ((500, {"code": "internal", "message": "internal error"}, {}), "http_500"),
        ((403, {"code": "forbidden", "message": "no"}, {}), "forbidden"),
        ((302, b"", {"Location": "https://elsewhere.example/"}), "http_302"),
        (
            (401, {"code": "unauthorized", "message": "this console credential has been revoked"}, {}),  # noqa: E501
            "credential_rejected",
        ),
    ],
    ids=[
        "wrong-shape",
        "negative",
        "absurd",
        "no-completed-last-day",
        "no-queue-age",
        "negative-queue-age",
        "queue-age-as-text",
        "not-json",
        "500",
        "403",
        "redirect",
        "revoked-message-without-the-code",
    ],
)
async def test_odd_answers_are_failures_and_store_no_status(
    poll, factory, fake_leader, sessionmaker, reply, outcome
):
    await factory.leader("eu-1")
    fake_leader.replies[("GET", "/v1/admin/status")] = reply
    assert await poll(utcnow()) == {"eu-1": outcome}
    (snapshot,) = await _snapshots(sessionmaker)
    assert (snapshot.reachable, snapshot.status) == (False, None)
    assert (await _leader(sessionmaker)).consecutive_failures == 1


async def test_an_unknown_credential_is_a_failure_and_polling_goes_on(
    poll, factory, fake_leader, sessionmaker
):
    await factory.leader("eu-1")
    fake_leader.modes[HOST] = "unknown"
    now = utcnow()
    assert await poll(now) == {"eu-1": "credential_rejected"}
    assert await poll(now + timedelta(seconds=15)) == {"eu-1": "credential_rejected"}
    leader = await _leader(sessionmaker)
    assert (leader.consecutive_failures, leader.credential_revoked_at) == (2, None)


async def test_a_revoked_credential_stops_polling_until_it_is_replaced(
    poll, factory, fake_leader, sessionmaker, keys
):
    await factory.leader("eu-1")
    fake_leader.modes[HOST] = "revoked"
    now = utcnow()
    assert await poll(now) == {"eu-1": "credential_revoked"}
    leader = await _leader(sessionmaker)
    assert leader.credential_revoked_at == now
    assert leader.consecutive_failures == 0
    for later in (15, 30, 300):
        assert await poll(now + timedelta(seconds=later)) == {}
    assert len(fake_leader.requests) == 1
    async with sessionmaker() as session:
        (entry,) = (await session.scalars(select(AuditEntry))).all()
    assert (entry.actor, entry.action, entry.leader) == (
        "system:poller",
        "leader.credential_revoked",
        "eu-1",
    )

    async with sessionmaker() as session:
        await replace_credential(session, keys, "eu-1", ROTATED, now=utcnow(), actor="t")
        await session.commit()
    del fake_leader.modes[HOST]
    assert await poll(now + timedelta(seconds=301)) == {"eu-1": "ok"}
    assert fake_leader.requests[-1].headers["authorization"] == f"Console {ROTATED}"


async def test_a_credential_rotated_during_a_poll_is_not_marked_by_its_answer(
    poll, factory, fake_leader, sessionmaker, keys
):
    await factory.leader("eu-1")

    async def rotate(_request):
        async with sessionmaker() as session:
            await replace_credential(session, keys, "eu-1", ROTATED, now=utcnow(), actor="t")
            await session.commit()

    fake_leader.on_request = rotate
    fake_leader.modes[HOST] = "revoked"
    assert await poll(utcnow()) == {"eu-1": "credential_revoked"}
    leader = await _leader(sessionmaker)
    assert leader.credential_revoked_at is None
    assert leader.last_polled_at is None  # the rotation's reset stands
    # Sealed bound to the name and URL: open with the row's own sealing context.
    context = sealing_context(leader.name, leader.base_url)
    assert keys.open_credential(context, leader.credential) == ROTATED
    assert len(await _snapshots(sessionmaker)) == 1


async def test_disabled_leaders_are_not_polled(poll, factory, fake_leader):
    await factory.leader("eu-1", enabled=False)
    assert await poll(utcnow()) == {}
    assert fake_leader.requests == []


async def test_a_credential_the_key_cannot_open_is_a_failure_with_no_call(
    poll, factory, fake_leader, sessionmaker
):
    factory.keys = ConsoleKeys(bytes(32))  # sealed with another key
    await factory.leader("eu-1")
    assert await poll(utcnow()) == {"eu-1": "credential_unreadable"}
    assert fake_leader.requests == []
    assert (await _leader(sessionmaker)).last_error == "credential_unreadable"


async def test_every_due_leader_is_polled_in_one_round(poll, factory, fake_leader):
    for name in ("eu-1", "us-1", "ap-1"):
        await factory.leader(name)
    fake_leader.modes["us-1.leaders.example"] = "down"
    assert await poll(utcnow(), concurrency=2) == {
        "ap-1": "ok",
        "eu-1": "ok",
        "us-1": "connect_error",
    }


async def test_two_replicas_poll_a_leader_once(poll, factory, fake_leader):
    await factory.leader("eu-1")
    now = utcnow()
    first, second = await asyncio.gather(poll(now), poll(now))
    # The loser either found the lock taken ("locked") or, once it got the lock, found the
    # leader just polled (skipped, so absent from its answer).
    outcomes = [first.get("eu-1"), second.get("eu-1")]
    assert outcomes.count("ok") == 1
    assert set(outcomes) <= {"ok", "locked", None}
    assert len(fake_leader.requests) == 1


async def test_a_leader_locked_by_another_replica_is_skipped(poll, factory, fake_leader, engine):
    leader = await factory.leader("eu-1")
    async with engine.connect() as other_replica:
        key = POLL_LOCK_BASE + leader.id
        await other_replica.execute(text("select pg_advisory_lock(:key)"), {"key": key})
        assert await poll(utcnow()) == {"eu-1": "locked"}
        await other_replica.execute(text("select pg_advisory_unlock(:key)"), {"key": key})
        await other_replica.commit()
    assert fake_leader.requests == []


async def test_history_older_than_a_day_and_ended_sessions_are_pruned(
    sessionmaker, factory, client
):
    leader = await factory.leader("eu-1")
    now = utcnow()
    async with sessionmaker() as session:
        for hours in (25, 23):
            session.add(
                Snapshot(
                    leader_id=leader.id,
                    taken_at=now - timedelta(hours=hours),
                    reachable=True,
                    outcome="ok",
                    status=STATUS,
                )
            )
        await session.commit()
    await factory.person(client, now=now - timedelta(hours=9))
    removed = await prune(
        sessionmaker, now=now, history=timedelta(hours=24), session_idle=timedelta(hours=1)
    )
    assert removed == 2
    assert [s.taken_at for s in await _snapshots(sessionmaker)] == [now - timedelta(hours=23)]
    async with sessionmaker() as session:
        assert (await session.scalars(select(ConsoleSession))).all() == []


async def test_the_app_polls_in_the_background_and_stops_cleanly(
    engine, make_settings, idp, fake_leader, factory
):
    await factory.leader("eu-1")
    application = create_app(
        make_settings(poll_tick_seconds=0.05),
        background=True,
        fetch=idp.fetch,
        idp_transport=idp.transport,
        leader_transport=fake_leader.transport,
    )
    async with application.router.lifespan_context(application):
        for _ in range(100):
            if fake_leader.requests:
                break
            await asyncio.sleep(0.05)
    assert fake_leader.requests
    assert fake_leader.requests[0].url.path == "/v1/admin/status"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m uv run pytest packages/console/tests/test_poller.py -v`
Expected: FAIL with `TypeError: create_app() got an unexpected keyword argument 'background'` (raised by the `app` fixture) or `ModuleNotFoundError: No module named 'swarmscribe_console.poller'`.

- [ ] **Step 4: Write the advisory-lock helper**

Create `packages/console/src/swarmscribe_console/background.py`:

```python
"""Advisory-lock exclusivity for the console's background work. The same pattern as the
leader's background.run_exclusive, keyed by number so that each leader has its own lock."""

from collections.abc import Awaitable, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def run_exclusive(
    engine: AsyncEngine, key: int, work: Callable[[], Awaitable[None]]
) -> bool:
    """Run `work` only if no other replica holds the session advisory lock `key`.

    The lock outlives the transaction that took it, so that transaction is committed at once
    and the lock connection sits idle while `work` runs. If anything interrupts the acquire
    or the unlock (including cancellation), the connection is invalidated rather than
    returned to the pool, so the server session and its lock end instead of leaking."""
    async with engine.connect() as conn:
        clean = False
        try:
            got = await conn.scalar(text("select pg_try_advisory_lock(:key)"), {"key": key})
            await conn.commit()
            if not got:
                clean = True
                return False
            try:
                await work()
            finally:
                await conn.execute(text("select pg_advisory_unlock(:key)"), {"key": key})
                await conn.commit()
                clean = True
            return True
        finally:
            if not clean:
                await conn.invalidate()
```

- [ ] **Step 5: Write the poller**

Create `packages/console/src/swarmscribe_console/poller.py`:

```python
"""The poller (fleet console spec 5.3).

Each replica runs one loop. Every second it picks the enabled leaders not polled for 14 s and
whose credential is not revoked; for each it takes that leader's advisory lock, re-reads the
leader inside the lock (another replica may just have polled it), calls GET /v1/admin/status
as system:poller with role viewer (C1: one call carries the followers by pool too), and
records a snapshot and the leader's poll state. Three consecutive failures mean unreachable.
A 401 "revoked" marks the leader revoked and stops polling it until a console administrator
replaces the credential (C1 follow-up). Snapshots older than 24 hours are pruned hourly."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from . import audit
from .background import run_exclusive
from .config import Settings
from .crypto import ConsoleKeys, CredentialUnreadable
from .db.models import Leader, Snapshot
from .leader_client import (
    POLLER_ACTOR,
    LeaderBadAnswer,
    LeaderClient,
    LeaderReply,
    LeaderTarget,
    LeaderUnreachable,
    is_revoked,
)
from .leaders import sealing_context
from .sessions import prune_expired

logger = logging.getLogger(__name__)

POLL_LOCK_BASE = 0x53430001 << 32  # + leader id
PRUNE_LOCK = 0x53430002 << 32
STATUS_PATH = "/v1/admin/status"
DUE_SLACK = timedelta(seconds=1)

Count = Annotated[int, Field(ge=0, le=10**12)]


class StatusPayload(BaseModel):
    """The shape of C1/C1b's Status, checked before a leader's answer is stored. Strict: a
    count must be a JSON integer (not "420", not true). Extra fields are kept, so a newer
    leader's additions reach the web app."""

    model_config = ConfigDict(extra="allow", strict=True)

    jobs: dict[str, Count]
    pools: list[dict[str, Any]]
    followers: dict[str, Count]
    follower_pools: list[dict[str, Any]] = Field(default_factory=list)
    completed_last_hour: Count
    completed_last_day: Count  # C1b; required (C1b is this plan's precondition)
    oldest_queued_age_s: Count | None  # C1b; None when nothing is queued, but present
    failed_attempts_last_day: Count
    locations: list[dict[str, Any]]


@dataclass(frozen=True)
class PollerConfig:
    interval: timedelta
    timeout: float
    unreachable_after: int
    history: timedelta
    concurrency: int

    @classmethod
    def from_settings(cls, settings: Settings) -> "PollerConfig":
        return cls(
            interval=timedelta(seconds=settings.poll_interval_seconds),
            timeout=settings.poll_timeout_seconds,
            unreachable_after=settings.unreachable_after_failures,
            history=timedelta(hours=settings.history_hours),
            concurrency=settings.poll_concurrency,
        )


def classify(reply: LeaderReply) -> tuple[str, dict[str, Any] | None]:
    if reply.status == 200:
        try:
            return "ok", StatusPayload.model_validate(reply.body).model_dump(mode="json")
        except ValidationError:
            return "bad_response", None
    if is_revoked(reply):
        return "credential_revoked", None
    if reply.status == 401:
        return "credential_rejected", None
    if reply.status == 403:
        return "forbidden", None
    return f"http_{reply.status}", None


async def mark_revoked(
    session: AsyncSession, leader: Leader, sealed: bytes, *, now: datetime, actor: str
) -> bool:
    """Mark the leader's credential revoked, once, and only if it is still the credential the
    leader refused (one replaced meanwhile is never marked). The caller commits."""
    if leader.credential != sealed or leader.credential_revoked_at is not None:
        return False
    leader.credential_revoked_at = now
    leader.last_error = "credential_revoked"
    audit.record(
        session,
        actor=actor,
        action="leader.credential_revoked",
        leader=leader.name,
        outcome="credential_revoked",
    )
    return True


async def _record(
    sessionmaker: Any,
    leader_id: int,
    sealed: bytes,
    outcome: str,
    payload: dict[str, Any] | None,
    *,
    now: datetime,
) -> None:
    async with sessionmaker() as session:
        leader = await session.get(Leader, leader_id, with_for_update=True, populate_existing=True)
        if leader is None:  # removed while this poll was out
            return
        session.add(
            Snapshot(
                leader_id=leader_id,
                taken_at=now,
                reachable=outcome == "ok",
                outcome=outcome,
                status=payload,
            )
        )
        if leader.credential != sealed:
            # Rotated while this poll was out: the answer was about the old credential.
            await session.commit()
            return
        leader.last_polled_at = now
        if outcome == "ok":
            leader.consecutive_failures = 0
            leader.last_error = None
            leader.last_success_at = now
        elif outcome == "credential_revoked":
            await mark_revoked(session, leader, sealed, now=now, actor=POLLER_ACTOR)
        else:
            leader.consecutive_failures += 1
            leader.last_error = outcome
        await session.commit()


async def poll_leader(
    sessionmaker: Any,
    client: LeaderClient,
    keys: ConsoleKeys,
    leader_id: int,
    *,
    now: datetime,
    config: PollerConfig,
) -> str:
    """Poll one leader if it is still due. Call it holding the leader's lock."""
    async with sessionmaker() as session:
        leader = await session.get(Leader, leader_id)
        if leader is None or not leader.enabled or leader.credential_revoked_at is not None:
            return "skipped"
        if (
            leader.last_polled_at is not None
            and now - leader.last_polled_at < config.interval - DUE_SLACK
        ):
            return "skipped"
        name, base_url, sealed = leader.name, leader.base_url, leader.credential
    payload: dict[str, Any] | None = None
    try:
        # The credential is sealed bound to the name and URL (C2a `leaders.sealing_context`),
        # both read from the same locked row as `sealed`. A bare name never opens it.
        context = sealing_context(name, base_url)
        target = LeaderTarget(name, base_url, keys.open_credential(context, sealed))
        reply = await client.call(
            target, "GET", STATUS_PATH, actor=POLLER_ACTOR, role="viewer", timeout=config.timeout
        )
        outcome, payload = classify(reply)
    except CredentialUnreadable:
        outcome = "credential_unreadable"
    except LeaderUnreachable as exc:
        outcome = exc.reason
    except LeaderBadAnswer:
        outcome = "bad_response"
    await _record(sessionmaker, leader_id, sealed, outcome, payload, now=now)
    return outcome


async def poll_due_leaders(
    engine: AsyncEngine,
    sessionmaker: Any,
    client: LeaderClient,
    keys: ConsoleKeys,
    *,
    now: datetime,
    config: PollerConfig,
) -> dict[str, str]:
    """One round: every due leader, each under its own advisory lock."""
    due_before = now - (config.interval - DUE_SLACK)
    async with sessionmaker() as session:
        rows = (
            await session.execute(
                select(Leader.id, Leader.name)
                .where(
                    Leader.enabled.is_(True),
                    Leader.credential_revoked_at.is_(None),
                    or_(Leader.last_polled_at.is_(None), Leader.last_polled_at <= due_before),
                )
                .order_by(Leader.last_polled_at.asc().nulls_first(), Leader.id)
            )
        ).all()
    limit = asyncio.Semaphore(config.concurrency)

    async def one(leader_id: int) -> str:
        async with limit:
            outcome = "locked"

            async def work() -> None:
                nonlocal outcome
                outcome = await poll_leader(
                    sessionmaker, client, keys, leader_id, now=now, config=config
                )

            await run_exclusive(engine, POLL_LOCK_BASE + leader_id, work)
            return outcome

    results = await asyncio.gather(*(one(row.id) for row in rows), return_exceptions=True)
    outcomes: dict[str, str] = {}
    for row, result in zip(rows, results, strict=True):
        if isinstance(result, BaseException):
            logger.error("polling leader %s failed: %s", row.name, type(result).__name__)
            outcomes[row.name] = "error"
        elif result != "skipped":
            outcomes[row.name] = result
    return outcomes


async def prune(
    sessionmaker: Any, *, now: datetime, history: timedelta, session_idle: timedelta
) -> int:
    """Delete snapshots older than `history`, ended sessions and abandoned sign-ins."""
    async with sessionmaker() as session:
        snapshots = await session.execute(delete(Snapshot).where(Snapshot.taken_at < now - history))
        removed = snapshots.rowcount + await prune_expired(session, now=now, idle=session_idle)
        await session.commit()
    return removed
```

- [ ] **Step 6: Run the poller and the prune step from the app**

Edit C2a's `packages/console/src/swarmscribe_console/app.py` **in place**. Do not replace the file or rebuild `create_app` from scratch: C2a's version carries four things that a rewrite would silently drop, and each has a test.

- `ContainErrors` and `_ConsoleApp.build_middleware_stack`, which put `SecurityHeaders` and `ContainErrors` outside Starlette's error middleware (a 500 carries the security headers and is logged once, by type, route and traceback frames only). The app is built with `_ConsoleApp(...)`, not `FastAPI(...)`, and there is no `app.add_middleware(SecurityHeaders)`.
- The engine is created with `hide_parameters=True`. Keep that argument on the line you edit.
- The build-time CSRF guard: `assert_guarded(app.routes)` is the **last statement** before `return app`, after the fleet and proxy routers and after the `SpaFiles` mount (Task 5). Every router you include in Tasks 3 and 4 goes above it.
- The comment on the `/auth` router (no `Person` guard before a session exists).

Make exactly these changes:

1. Imports: add `asyncio`, `timedelta`, `run_periodically`, `utcnow`, `LeaderClient`, `run_exclusive`, `PRUNE_LOCK`, `PollerConfig`, `poll_due_leaders`, `prune` (the imports of the previous version of this step), next to C2a's.
2. `create_app` gains `background: bool = True` and `leader_transport: httpx.AsyncBaseTransport | None = None`, and the docstring names `leader_transport`.
3. After the engine and sessionmaker lines, build the shared pieces and the two loop steps, and replace C2a's `lifespan` with the one below (C2a's only disposed the engine):

```python
    keys = ConsoleKeys(settings.key_bytes())
    leader_client = LeaderClient(transport=leader_transport)
    poller_config = PollerConfig.from_settings(settings)
    session_idle = timedelta(seconds=settings.session_idle_seconds)

    async def poll_step() -> None:
        await poll_due_leaders(
            engine, sessionmaker, leader_client, keys, now=utcnow(), config=poller_config
        )

    async def prune_step() -> None:
        async def work() -> None:
            await prune(
                sessionmaker,
                now=utcnow(),
                history=poller_config.history,
                session_idle=session_idle,
            )

        await run_exclusive(engine, PRUNE_LOCK, work)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        stop = asyncio.Event()
        tasks = []
        if background:
            tasks = [
                asyncio.create_task(
                    run_periodically(stop, settings.poll_tick_seconds, poll_step, "poller")
                ),
                asyncio.create_task(
                    run_periodically(stop, settings.prune_interval_seconds, prune_step, "prune")
                ),
            ]
        try:
            yield
        finally:
            stop.set()
            try:
                if tasks:
                    _done, pending = await asyncio.wait(tasks, timeout=SHUTDOWN_GRACE_SECONDS)
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
            finally:
                await leader_client.aclose()
                await engine.dispose()
```

   with `SHUTDOWN_GRACE_SECONDS = 10` at module level.
4. Reuse `keys` for `app.state.keys` (C2a built `ConsoleKeys(...)` inline: replace that line with `app.state.keys = keys`), and add `app.state.leader_client = leader_client` and `app.state.poller_config = poller_config` next to the other `app.state` lines.
5. Leave `api_errors.install(app)`, `app.add_middleware(BodyLimit)`, the three `include_router` calls and the final `assert_guarded(app.routes)` exactly as they are.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python -m uv run pytest packages/console/tests -v`
Expected: PASS (C2a's suites now run against the fixture with `background=False`).

Run: `python -m uv run ruff check packages/console`
Expected: no findings.

- [ ] **Step 8: Commit**

```bash
git add packages/console/src/swarmscribe_console/background.py packages/console/src/swarmscribe_console/poller.py packages/console/src/swarmscribe_console/app.py packages/console/tests/conftest.py packages/console/tests/test_poller.py
git commit -m "Console: poller every 15 s per leader, exclusive, stops on a revoked credential

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The fleet view and history

**Files:**
- Create: `packages/console/src/swarmscribe_console/api/fleet.py`
- Modify: `packages/console/src/swarmscribe_console/app.py` (include the router)
- Modify: `packages/console/tests/conftest.py` (`Factory.snapshot`)
- Test: `packages/console/tests/test_fleet.py`

**Interfaces:**
- Consumes: `grants.grants_held`, `role_for` (C2a Task 3); `leaders.find_leader`, `list_leaders` (C2a Task 6); `api.deps.Person`, `Session` (C2a Task 4); `app.state.poller_config.unreachable_after` (Task 2).
- Produces:
  - `api.fleet.health_of(leader, unreachable_after: int) -> str` (`disabled|credential_revoked|unreachable|pending|reachable`).
  - `async api.fleet.leader_for(session, person, name) -> tuple[Leader, str]` (raises `NotFound(code="leader_not_found")` for an unknown leader **and** for one the person holds no role on).
  - `api.fleet.summary_of(status: dict) -> dict` (the overview row's figures, from one status payload).
  - `GET /api/fleet` → `[{name, labels, role, health, enabled, last_polled_at, last_success_at, last_error, consecutive_failures, summary: {queued, completed_last_hour, completed_last_day, failed_attempts_last_day, oldest_queued_age_s, followers_active_by_pool, scan_errors} | null, snapshot: {taken_at, status} | null}]`.
  - `GET /api/leaders/{name}/history?hours=1..24` → `[{at, reachable, queued, leased, completed_last_hour, completed_last_day, failed_attempts_last_day, oldest_queued_age_s, followers_active}]`.
  - `Factory.snapshot(leader, *, taken_at, reachable=True, outcome="ok", status=None) -> Snapshot` (status defaults to a copy of `STATUS` when reachable).

- [ ] **Step 1: Add the snapshot factory**

In `packages/console/tests/conftest.py`, add `STATUS` to the `console_testkit` import list, `Snapshot` to the models import, `import copy  # noqa: E402`, and add this method to `class Factory`:

```python
    async def snapshot(
        self, leader, *, taken_at, reachable=True, outcome="ok", status=None
    ) -> Snapshot:
        if status is None and reachable:
            status = copy.deepcopy(STATUS)
        return await self._save(
            Snapshot(
                leader_id=leader.id,
                taken_at=taken_at,
                reachable=reachable,
                outcome=outcome,
                status=status,
            )
        )
```

- [ ] **Step 2: Write the failing tests**

Create `packages/console/tests/test_fleet.py`:

```python
import copy
from datetime import datetime, timedelta

import pytest
from console_testkit import STATUS
from sqlalchemy import update
from swarmscribe_console.db.models import Leader
from swarmscribe_leader.clock import utcnow

PERSON = {"email:person@example.org"}


@pytest.fixture
async def fleet(factory):
    return {
        "eu-1": await factory.leader("eu-1", labels={"env": "prod"}),
        "us-1": await factory.leader("us-1", labels={"env": "test"}),
        "ap-1": await factory.leader("ap-1"),
    }


async def _state(sessionmaker, name, **values):
    async with sessionmaker() as session:
        await session.execute(update(Leader).where(Leader.name == name).values(**values))
        await session.commit()


async def test_a_person_sees_only_leaders_they_hold_a_role_on(client, factory, fleet):
    await factory.grant("operator", "label:env=prod", "email", "person@example.org")
    await factory.grant("viewer", "leader:ap-1", "email", "person@example.org")
    await factory.person(client, principals=PERSON)
    body = (await client.get("/api/fleet")).json()
    assert [(row["name"], row["role"]) for row in body] == [
        ("ap-1", "viewer"),
        ("eu-1", "operator"),
    ]
    assert body[1]["labels"] == {"env": "prod"}


async def test_the_highest_matching_grant_is_the_role_shown(client, factory, fleet):
    await factory.grant("viewer", "all", "email", "person@example.org")
    await factory.grant("admin", "leader:eu-1", "email", "person@example.org")
    await factory.person(client, principals=PERSON)
    roles = {row["name"]: row["role"] for row in (await client.get("/api/fleet")).json()}
    assert roles == {"ap-1": "viewer", "eu-1": "admin", "us-1": "viewer"}


async def test_a_console_admin_without_grants_sees_no_leaders(client, factory, fleet):
    await factory.console_admin("email", "person@example.org")
    await factory.person(client, principals=PERSON)
    assert (await client.get("/api/fleet")).json() == []


async def test_the_fleet_needs_a_session(client):
    assert (await client.get("/api/fleet")).status_code == 401


async def test_health_follows_the_pollers_state(client, factory, fleet, sessionmaker):
    await factory.leader("xx-1", enabled=False)
    await factory.leader("rv-1")
    await factory.grant("viewer", "all", "email", "person@example.org")
    now = utcnow()
    await _state(sessionmaker, "eu-1", last_success_at=now, last_polled_at=now)
    await _state(
        sessionmaker, "us-1", consecutive_failures=3, last_error="timeout", last_polled_at=now
    )
    await _state(
        sessionmaker, "ap-1", consecutive_failures=2, last_error="timeout", last_polled_at=now
    )
    await _state(sessionmaker, "rv-1", credential_revoked_at=now, last_error="credential_revoked")
    await factory.person(client, principals=PERSON)
    rows = {row["name"]: row for row in (await client.get("/api/fleet")).json()}
    assert {name: row["health"] for name, row in rows.items()} == {
        "ap-1": "pending",
        "eu-1": "reachable",
        "rv-1": "credential_revoked",
        "us-1": "unreachable",
        "xx-1": "disabled",
    }
    assert (rows["us-1"]["last_error"], rows["us-1"]["consecutive_failures"]) == ("timeout", 3)


async def test_the_latest_successful_snapshot_is_shown(client, factory, fleet):
    await factory.grant("viewer", "all", "email", "person@example.org")
    now = utcnow()
    older = copy.deepcopy(STATUS) | {"completed_last_hour": 4}
    newest_ok = copy.deepcopy(STATUS) | {"completed_last_hour": 9}
    await factory.snapshot(fleet["eu-1"], taken_at=now - timedelta(seconds=90), status=older)
    await factory.snapshot(fleet["eu-1"], taken_at=now - timedelta(seconds=60), status=newest_ok)
    await factory.snapshot(
        fleet["eu-1"], taken_at=now - timedelta(seconds=10), reachable=False, outcome="timeout"
    )
    await factory.person(client, principals=PERSON)
    rows = {row["name"]: row for row in (await client.get("/api/fleet")).json()}
    snapshot = rows["eu-1"]["snapshot"]
    assert datetime.fromisoformat(snapshot["taken_at"]) == now - timedelta(seconds=60)
    assert snapshot["status"]["completed_last_hour"] == 9
    assert rows["eu-1"]["summary"]["completed_last_hour"] == 9
    assert rows["us-1"]["snapshot"] is None
    assert rows["us-1"]["summary"] is None


async def test_the_overview_row_comes_from_the_status_alone(client, factory, fleet):
    await factory.grant("viewer", "leader:eu-1", "email", "person@example.org")
    status = copy.deepcopy(STATUS)
    status["follower_pools"] = [
        {"pool": "default", "active": 2, "draining": 1, "revoked": 0, "gone": 0},
        {"pool": "gpu", "active": 0, "draining": 0, "revoked": 0, "gone": 3},
    ]
    status["locations"] = [
        {"name": "talks", "last_scan_error": None},
        {"name": "archive", "last_scan_error": "input folder 'x' is not available"},
    ]
    await factory.snapshot(fleet["eu-1"], taken_at=utcnow(), status=status)
    await factory.person(client, principals=PERSON)
    (row,) = (await client.get("/api/fleet")).json()
    assert row["summary"] == {
        "queued": 3,
        "completed_last_hour": 7,
        "completed_last_day": 30,
        "failed_attempts_last_day": 1,
        "oldest_queued_age_s": 420,
        "followers_active_by_pool": {"default": 2, "gpu": 0},
        "scan_errors": [
            {"location": "archive", "error": "input folder 'x' is not available"}
        ],
    }


async def test_nothing_queued_shows_no_queue_age(client, factory, fleet):
    await factory.grant("viewer", "leader:eu-1", "email", "person@example.org")
    status = copy.deepcopy(STATUS)
    status["jobs"]["queued"] = 0
    status["oldest_queued_age_s"] = None
    await factory.snapshot(fleet["eu-1"], taken_at=utcnow(), status=status)
    await factory.person(client, principals=PERSON)
    (row,) = (await client.get("/api/fleet")).json()
    assert (row["summary"]["queued"], row["summary"]["oldest_queued_age_s"]) == (0, None)


async def test_history_is_bucketed_by_five_minutes(client, factory, fleet):
    await factory.grant("viewer", "leader:eu-1", "email", "person@example.org")
    now = utcnow()
    base = now.replace(second=0, microsecond=0) - timedelta(minutes=now.minute % 5 + 30)

    def status(queued):
        body = copy.deepcopy(STATUS)
        body["jobs"]["queued"] = queued
        return body

    eu = fleet["eu-1"]
    await factory.snapshot(eu, taken_at=now - timedelta(hours=30), status=status(99))
    await factory.snapshot(eu, taken_at=base + timedelta(minutes=1), status=status(1))
    await factory.snapshot(eu, taken_at=base + timedelta(minutes=4), status=status(2))
    await factory.snapshot(
        eu, taken_at=base + timedelta(minutes=6), reachable=False, outcome="timeout"
    )
    await factory.snapshot(eu, taken_at=base + timedelta(minutes=12), status=status(5))
    await factory.person(client, principals=PERSON)
    answer = await client.get("/api/leaders/eu-1/history")
    assert answer.status_code == 200
    points = [
        (datetime.fromisoformat(p["at"]) - base, p["reachable"], p["queued"])
        for p in answer.json()
    ]
    assert points == [
        (timedelta(0), True, 2),
        (timedelta(minutes=5), False, None),
        (timedelta(minutes=10), True, 5),
    ]
    first = answer.json()[0]
    assert (first["completed_last_hour"], first["followers_active"]) == (7, 2)
    assert (first["completed_last_day"], first["oldest_queued_age_s"]) == (30, 420)
    assert answer.json()[1]["oldest_queued_age_s"] is None  # the failed poll's bucket
    short = await client.get("/api/leaders/eu-1/history", params={"hours": 1})
    assert len(short.json()) == 3


@pytest.mark.parametrize("hours", ["0", "25", "x"])
async def test_history_hours_are_bounded(client, factory, fleet, hours):
    await factory.grant("viewer", "all", "email", "person@example.org")
    await factory.person(client, principals=PERSON)
    answer = await client.get("/api/leaders/eu-1/history", params={"hours": hours})
    assert answer.status_code == 422


async def test_a_leader_without_a_grant_is_answered_like_an_unknown_one(client, factory, fleet):
    await factory.grant("viewer", "leader:eu-1", "email", "person@example.org")
    await factory.person(client, principals=PERSON)
    hidden = await client.get("/api/leaders/us-1/history")
    unknown = await client.get("/api/leaders/zz-9/history")
    assert hidden.status_code == unknown.status_code == 404
    assert hidden.json() == unknown.json() == {
        "code": "leader_not_found",
        "message": "no leader with that name",
    }


@pytest.mark.parametrize("name", ["..", "a%2Fb", "eu-1%2F..%2Fus-1"])
async def test_odd_leader_names_in_the_history_path_are_not_found(client, factory, fleet, name):
    await factory.grant("viewer", "all", "email", "person@example.org")
    await factory.person(client, principals=PERSON)
    assert (await client.get(f"/api/leaders/{name}/history")).status_code == 404
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m uv run pytest packages/console/tests/test_fleet.py -v`
Expected: FAIL — `404` from `/api/fleet`.

- [ ] **Step 4: Write the fleet routes**

Create `packages/console/src/swarmscribe_console/api/fleet.py`:

```python
"""The fleet view (fleet console spec 5.4 and 6): every leader the person holds a role on,
with its health and latest successful snapshot, and one leader's history for the 24-hour
chart. A leader the person holds no role on is answered exactly like an unknown one."""

from datetime import datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel
from sqlalchemy import BigInteger, Boolean, DateTime, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from swarmscribe_leader.clock import utcnow

from .. import grants, leaders
from ..db.models import Leader
from ..errors import NotFound
from ..sessions import SignedIn
from .deps import Person, Session

router = APIRouter(prefix="/api")

HISTORY_BUCKET = timedelta(minutes=5)
MAX_HISTORY_HOURS = 24

_LATEST = text(
    """
    SELECT s.leader_id, s.taken_at, s.status
    FROM unnest(CAST(:ids AS bigint[])) AS l(id)
    CROSS JOIN LATERAL (
        SELECT leader_id, taken_at, status FROM snapshots
        WHERE leader_id = l.id AND reachable
        ORDER BY taken_at DESC
        LIMIT 1
    ) AS s
    """
).columns(leader_id=BigInteger, taken_at=DateTime(timezone=True), status=JSONB)

_HISTORY = text(
    """
    SELECT DISTINCT ON (bucket)
        date_bin(CAST(:bucket AS interval), taken_at, TIMESTAMPTZ '2000-01-01 00:00:00+00')
            AS bucket,
        reachable,
        (status->'jobs'->>'queued')::bigint AS queued,
        (status->'jobs'->>'leased')::bigint AS leased,
        (status->>'completed_last_hour')::bigint AS completed_last_hour,
        (status->>'completed_last_day')::bigint AS completed_last_day,
        (status->>'failed_attempts_last_day')::bigint AS failed_attempts_last_day,
        (status->>'oldest_queued_age_s')::bigint AS oldest_queued_age_s,
        (status->'followers'->>'active')::bigint AS followers_active
    FROM snapshots
    WHERE leader_id = :leader_id AND taken_at >= :since
    ORDER BY bucket, taken_at DESC
    """
).columns(
    bucket=DateTime(timezone=True),
    reachable=Boolean,
    queued=BigInteger,
    leased=BigInteger,
    completed_last_hour=BigInteger,
    completed_last_day=BigInteger,
    failed_attempts_last_day=BigInteger,
    oldest_queued_age_s=BigInteger,
    followers_active=BigInteger,
)


class SnapshotOut(BaseModel):
    taken_at: datetime
    status: dict[str, Any]


class ScanError(BaseModel):
    location: str
    error: str


class FleetSummary(BaseModel):
    """Spec 6's overview row, from one status payload (C1 + C1b). `oldest_queued_age_s` is
    as of the snapshot's taken_at."""

    queued: int
    completed_last_hour: int
    completed_last_day: int
    failed_attempts_last_day: int
    oldest_queued_age_s: int | None
    followers_active_by_pool: dict[str, int]
    scan_errors: list[ScanError]


class FleetLeader(BaseModel):
    name: str
    labels: dict[str, str]
    role: str
    health: str
    enabled: bool
    last_polled_at: datetime | None
    last_success_at: datetime | None
    last_error: str | None
    consecutive_failures: int
    summary: FleetSummary | None
    snapshot: SnapshotOut | None


class HistoryPoint(BaseModel):
    at: datetime
    reachable: bool
    queued: int | None
    leased: int | None
    completed_last_hour: int | None
    completed_last_day: int | None
    failed_attempts_last_day: int | None
    oldest_queued_age_s: int | None
    followers_active: int | None


def summary_of(status: dict[str, Any]) -> dict[str, Any]:
    """The overview figures from a stored status (validated by the poller's StatusPayload).
    Lookups stay defensive: a stored row is data, not a promise."""
    pools = status.get("follower_pools") or []
    locations = status.get("locations") or []
    return {
        "queued": int((status.get("jobs") or {}).get("queued", 0)),
        "completed_last_hour": int(status.get("completed_last_hour", 0)),
        "completed_last_day": int(status.get("completed_last_day", 0)),
        "failed_attempts_last_day": int(status.get("failed_attempts_last_day", 0)),
        "oldest_queued_age_s": status.get("oldest_queued_age_s"),
        "followers_active_by_pool": {
            str(pool.get("pool")): int(pool.get("active", 0))
            for pool in pools
            if isinstance(pool, dict) and pool.get("pool") is not None
        },
        "scan_errors": [
            {"location": str(loc.get("name")), "error": str(loc["last_scan_error"])}
            for loc in locations
            if isinstance(loc, dict) and loc.get("last_scan_error")
        ],
    }


def health_of(leader: Leader, unreachable_after: int) -> str:
    if not leader.enabled:
        return "disabled"
    if leader.credential_revoked_at is not None:
        return "credential_revoked"
    if leader.consecutive_failures >= unreachable_after:
        return "unreachable"
    if leader.last_success_at is None:
        return "pending"
    return "reachable"


async def leader_for(
    session: AsyncSession, person: SignedIn, name: str
) -> tuple[Leader, str]:
    """The named leader and the person's role on it."""
    try:
        leader = await leaders.find_leader(session, name)
    except NotFound:
        leader = None
    role = None
    if leader is not None:
        held = await grants.grants_held(session, person.principals)
        role = grants.role_for(held, leader.name, leader.labels)
    if leader is None or role is None:
        raise NotFound("no leader with that name", code="leader_not_found")
    return leader, role


@router.get("/fleet", response_model=list[FleetLeader])
async def fleet(request: Request, person: Person, session: Session) -> list[FleetLeader]:
    held = await grants.grants_held(session, person.principals)
    visible: list[tuple[Leader, str]] = []
    if held:
        for leader in await leaders.list_leaders(session):
            role = grants.role_for(held, leader.name, leader.labels)
            if role is not None:
                visible.append((leader, role))
    latest: dict[int, Any] = {}
    if visible:
        rows = await session.execute(_LATEST, {"ids": [leader.id for leader, _ in visible]})
        latest = {row.leader_id: row for row in rows}
    after = request.app.state.poller_config.unreachable_after
    answer = []
    for leader, role in visible:
        snapshot = latest.get(leader.id)
        answer.append(
            FleetLeader(
                name=leader.name,
                labels=leader.labels,
                role=role,
                health=health_of(leader, after),
                enabled=leader.enabled,
                last_polled_at=leader.last_polled_at,
                last_success_at=leader.last_success_at,
                last_error=leader.last_error,
                consecutive_failures=leader.consecutive_failures,
                summary=(
                    FleetSummary.model_validate(summary_of(snapshot.status))
                    if snapshot is not None
                    else None
                ),
                snapshot=(
                    SnapshotOut(taken_at=snapshot.taken_at, status=snapshot.status)
                    if snapshot is not None
                    else None
                ),
            )
        )
    return answer


@router.get("/leaders/{name}/history", response_model=list[HistoryPoint])
async def history(
    name: str,
    person: Person,
    session: Session,
    hours: Annotated[int, Query(ge=1, le=MAX_HISTORY_HOURS)] = MAX_HISTORY_HOURS,
) -> list[HistoryPoint]:
    leader, _role = await leader_for(session, person, name)
    rows = await session.execute(
        _HISTORY,
        {
            "leader_id": leader.id,
            "since": utcnow() - timedelta(hours=hours),
            "bucket": HISTORY_BUCKET,
        },
    )
    return [
        HistoryPoint(
            at=row.bucket,
            reachable=row.reachable,
            queued=row.queued,
            leased=row.leased,
            completed_last_hour=row.completed_last_hour,
            completed_last_day=row.completed_last_day,
            failed_attempts_last_day=row.failed_attempts_last_day,
            oldest_queued_age_s=row.oldest_queued_age_s,
            followers_active=row.followers_active,
        )
        for row in rows
    ]
```

In `packages/console/src/swarmscribe_console/app.py`, add `from .api import fleet as fleet_api` to the `.api` imports and, after `app.include_router(admin_api.router)`, add:

```python
    app.include_router(fleet_api.router)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m uv run pytest packages/console/tests -v`
Expected: PASS.

Run: `python -m uv run ruff check packages/console`
Expected: no findings.

- [ ] **Step 6: Commit**

```bash
git add packages/console/src/swarmscribe_console/api/fleet.py packages/console/src/swarmscribe_console/app.py packages/console/tests/conftest.py packages/console/tests/test_fleet.py
git commit -m "Console: fleet view with health and latest snapshot, 24-hour history

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The action proxy — allow-list, actor and role, errors, audit

**Files:**
- Create: `packages/console/src/swarmscribe_console/proxy.py`, `packages/console/src/swarmscribe_console/api/proxy.py`
- Modify: `packages/console/src/swarmscribe_console/app.py` (include the router, after the fleet router)
- Test: `packages/console/tests/test_proxy.py`

**Interfaces:**
- Consumes: `api.fleet.leader_for` (Task 3); `leader_client.*` (Task 1); `poller.mark_revoked` (Task 2); `errors.*` (C2a Task 1, Task 1); `audit.record_apart` (C2a Task 2); `api.deps.Person`, `Session`, `keys_of`, `settings_of` (C2a Task 4); `leaders.sealing_context(name, base_url)` (C2a Task 6; `ConsoleKeys.open_credential(context, sealed)` takes that context, not the leader's name); `swarmscribe_leader.auth.roles.at_least`.
- Produces:
  - `proxy.ProxyRoute(method, template, role, action, query=(), body=False)` with `pattern` and `leader_path(params) -> str`; `proxy.ROUTES`; `proxy.match_route(method, rest) -> tuple[ProxyRoute, dict[str, str]] | None`; `proxy.target_of(params) -> str | None`.
  - Route `GET|POST /api/leaders/{name}/{rest:path}`.
  - Console audit actions: each `ProxyRoute.action` of a `POST` route.

- [ ] **Step 1: Write the failing tests**

Create `packages/console/tests/test_proxy.py`:

```python
import inspect
import json
import logging
import re

import pytest
from console_testkit import CREDENTIAL, ENTRA_ISSUER, all_rows_text
from sqlalchemy import select
from swarmscribe_console.crypto import ConsoleKeys
from swarmscribe_console.db.models import AuditEntry, Leader
from swarmscribe_console.proxy import ROUTES
from swarmscribe_console.sessions import SESSION_COOKIE
from swarmscribe_leader.api.admin import router as leader_admin_router
from swarmscribe_leader.auth.consoles import parse_delegation

HOST = "eu-1.leaders.example"
JOB = "11111111-2222-4333-8444-555555555555"
TOKEN_ID = "99999999-8888-4777-8666-555555555555"
TOKEN_PLAINTEXT = "J" * 20 + "-_" + "t" * 21
ACTOR = f"{ENTRA_ISSUER} entra-person-1 person@example.org"


@pytest.fixture
async def eu(factory):
    return await factory.leader("eu-1", labels={"env": "prod"})


@pytest.fixture
async def signed_in_as(client, factory):
    async def sign(role, scope="label:env=prod", **person):
        await factory.grant(role, scope, "email", "person@example.org")
        csrf = await factory.person(client, principals={"email:person@example.org"}, **person)
        client.headers["X-CSRF-Token"] = csrf
        return client

    return sign


async def _audit(sessionmaker) -> list[AuditEntry]:
    async with sessionmaker() as session:
        return list((await session.scalars(select(AuditEntry).order_by(AuditEntry.id))).all())


# --- forwarding -------------------------------------------------------------------------


async def test_a_read_is_forwarded_with_the_person_and_their_role(signed_in_as, eu, fake_leader):
    fake_leader.replies[("GET", "/v1/admin/jobs")] = (200, [{"id": JOB, "state": "queued"}], {})
    client = await signed_in_as("operator")
    answer = await client.get(
        "/api/leaders/eu-1/jobs", params={"state": "queued", "limit": "5", "evil": "1"}
    )
    assert answer.status_code == 200
    assert answer.json() == [{"id": JOB, "state": "queued"}]
    assert answer.headers["cache-control"] == "no-store"
    (request,) = fake_leader.requests
    assert str(request.url) == f"https://{HOST}/v1/admin/jobs?state=queued&limit=5"
    assert request.headers["authorization"] == f"Console {CREDENTIAL}"
    assert request.headers["x-swarmscribe-actor"] == ACTOR
    assert request.headers["x-swarmscribe-actor-role"] == "operator"
    parse_delegation(
        request.headers.get_list("x-swarmscribe-actor"),
        request.headers.get_list("x-swarmscribe-actor-role"),
    )


async def test_the_role_sent_is_the_persons_console_role_on_that_leader(
    signed_in_as, eu, fake_leader
):
    client = await signed_in_as("admin", scope="all")
    assert (await client.get("/api/leaders/eu-1/status")).status_code == 200
    assert fake_leader.requests[0].headers["x-swarmscribe-actor-role"] == "admin"


async def test_reads_are_not_audited_in_the_console(signed_in_as, eu, sessionmaker):
    client = await signed_in_as("viewer")
    await client.get("/api/leaders/eu-1/status")
    assert await _audit(sessionmaker) == []


@pytest.mark.parametrize(
    "query",
    ["state=queued&state=failed", "state=" + "x" * 201, "location=a%0Ab"],
    ids=["twice", "too-long", "control-character"],
)
async def test_odd_query_parameters_are_refused_before_the_leader(
    signed_in_as, eu, fake_leader, query
):
    client = await signed_in_as("viewer")
    answer = await client.get(f"/api/leaders/eu-1/jobs?{query}")
    assert answer.status_code == 422
    assert fake_leader.requests == []


async def test_an_action_is_forwarded_and_audited(signed_in_as, eu, fake_leader, sessionmaker):
    fake_leader.replies[("POST", f"/v1/admin/jobs/{JOB}/retry")] = (200, {"id": JOB}, {})
    client = await signed_in_as("operator")
    answer = await client.post(f"/api/leaders/eu-1/jobs/{JOB}/retry")
    assert (answer.status_code, answer.json()) == (200, {"id": JOB})
    assert fake_leader.requests[0].method == "POST"
    (entry,) = await _audit(sessionmaker)
    assert (entry.action, entry.leader, entry.target, entry.outcome) == (
        "jobs.retry",
        "eu-1",
        f"job_id={JOB}",
        "ok",
    )
    assert entry.actor == f"person@example.org ({ENTRA_ISSUER} entra-person-1)"
    assert entry.detail == {"role": "operator"}


async def test_a_body_is_forwarded_as_the_same_json(signed_in_as, eu, fake_leader):
    fake_leader.replies[("POST", f"/v1/admin/jobs/{JOB}/priority")] = (200, {"id": JOB}, {})
    client = await signed_in_as("operator")
    answer = await client.post(f"/api/leaders/eu-1/jobs/{JOB}/priority", json={"priority": 5})
    assert answer.status_code == 200
    assert json.loads(fake_leader.bodies[0]) == {"priority": 5}


@pytest.mark.parametrize(
    ("content", "status"),
    [(b"[1, 2]", 422), (b'"x"', 422), (b"{not json", 422), (b"", 422), (b"{" + b" " * 70_000 + b"}", 413)],  # noqa: E501
    ids=["array", "string", "invalid", "missing", "too-large"],
)
async def test_bodies_must_be_small_json_objects(signed_in_as, eu, fake_leader, content, status):
    client = await signed_in_as("operator")
    answer = await client.post(
        f"/api/leaders/eu-1/jobs/{JOB}/priority",
        content=content,
        headers={"Content-Type": "application/json"},
    )
    assert answer.status_code == status
    assert fake_leader.requests == []


# --- authorised twice -------------------------------------------------------------------


async def test_the_console_refuses_below_the_routes_role_without_calling_the_leader(
    signed_in_as, eu, fake_leader, sessionmaker
):
    client = await signed_in_as("viewer")
    answer = await client.post(f"/api/leaders/eu-1/jobs/{JOB}/retry")
    assert answer.status_code == 403
    assert answer.json()["code"] == "forbidden"
    assert "operator" in answer.json()["message"]
    assert fake_leader.requests == []
    (entry,) = await _audit(sessionmaker)
    assert (entry.action, entry.outcome) == ("jobs.retry", "forbidden")
    assert (await client.get("/api/leaders/eu-1/tokens")).status_code == 403


async def test_the_leaders_own_refusal_passes_through(signed_in_as, eu, fake_leader):
    fake_leader.replies[("POST", "/v1/admin/followers/" + JOB + "/revoke")] = (
        403,
        {"code": "forbidden", "message": "this needs the admin role; console fleet is limited to operator"},  # noqa: E501
        {},
    )
    client = await signed_in_as("admin")
    answer = await client.post(f"/api/leaders/eu-1/followers/{JOB}/revoke")
    assert answer.status_code == 403
    assert answer.json()["message"].endswith("limited to operator")


# --- leader errors and outages ----------------------------------------------------------


async def test_a_leader_error_passes_through_with_its_code(
    signed_in_as, eu, fake_leader, sessionmaker
):
    fake_leader.replies[("POST", f"/v1/admin/jobs/{JOB}/retry")] = (
        409,
        {"code": "not_retryable", "message": "the job is completed"},
        {},
    )
    client = await signed_in_as("operator")
    answer = await client.post(f"/api/leaders/eu-1/jobs/{JOB}/retry")
    assert (answer.status_code, answer.json()) == (
        409,
        {"code": "not_retryable", "message": "the job is completed"},
    )
    assert (await _audit(sessionmaker))[-1].outcome == "not_retryable"


async def test_a_leaders_retry_after_passes_through(signed_in_as, eu, fake_leader):
    fake_leader.replies[("GET", "/v1/admin/status")] = (
        503,
        {"code": "unavailable", "message": "service temporarily unavailable"},
        {"Retry-After": "10"},
    )
    client = await signed_in_as("viewer")
    answer = await client.get("/api/leaders/eu-1/status")
    assert (answer.status_code, answer.headers["retry-after"]) == (503, "10")


@pytest.mark.parametrize(
    "reply",
    [
        (500, b"<html>oops</html>", {"Content-Type": "text/html"}),
        (500, {"error": "no code"}, {}),
        (418, {"code": "Bad Code!", "message": "x"}, {}),
        (302, b"", {"Location": "https://elsewhere.example/"}),
    ],
    ids=["html", "no-code", "bad-code", "redirect"],
)
async def test_an_unusable_leader_answer_is_a_bad_gateway(signed_in_as, eu, fake_leader, reply):
    fake_leader.replies[("GET", "/v1/admin/status")] = reply
    client = await signed_in_as("viewer")
    answer = await client.get("/api/leaders/eu-1/status")
    assert (answer.status_code, answer.json()["code"]) == (502, "bad_gateway")


async def test_an_unreachable_leader_is_503_and_others_still_work(
    signed_in_as, eu, factory, fake_leader, sessionmaker
):
    await factory.leader("us-1", labels={"env": "prod"})
    fake_leader.modes[HOST] = "down"
    client = await signed_in_as("operator")
    answer = await client.post(f"/api/leaders/eu-1/jobs/{JOB}/cancel")
    assert answer.status_code == 503
    assert answer.json()["code"] == "leader_unreachable"
    assert answer.headers["retry-after"] == "15"
    assert (await _audit(sessionmaker))[-1].outcome == "leader_unreachable"
    assert (await client.get("/api/leaders/us-1/status")).status_code == 200


async def test_a_slow_leader_is_unreachable_after_the_proxy_timeout(
    app, make_settings, signed_in_as, eu, fake_leader
):
    app.state.settings = make_settings(proxy_timeout_seconds=0.2)
    fake_leader.modes[HOST] = "slow"
    fake_leader.delay = 2.0
    client = await signed_in_as("viewer")
    answer = await client.get("/api/leaders/eu-1/status")
    assert (answer.status_code, answer.json()["code"]) == (503, "leader_unreachable")


async def test_a_revoked_credential_marks_the_leader_and_stops_calls(
    signed_in_as, eu, fake_leader, sessionmaker
):
    fake_leader.modes[HOST] = "revoked"
    client = await signed_in_as("viewer")
    first = await client.get("/api/leaders/eu-1/status")
    assert (first.status_code, first.json()["code"]) == (503, "leader_credential_revoked")
    async with sessionmaker() as session:
        leader = (await session.scalars(select(Leader))).one()
    assert leader.credential_revoked_at is not None
    again = await client.get("/api/leaders/eu-1/status")
    assert again.json()["code"] == "leader_credential_revoked"
    assert len(fake_leader.requests) == 1
    actions = [entry.action for entry in await _audit(sessionmaker)]
    assert actions == ["leader.credential_revoked"]


async def test_an_unknown_credential_is_never_a_401_to_the_browser(signed_in_as, eu, fake_leader):
    fake_leader.modes[HOST] = "unknown"
    client = await signed_in_as("viewer")
    answer = await client.get("/api/leaders/eu-1/status")
    assert (answer.status_code, answer.json()["code"]) == (502, "leader_credential_rejected")
    assert (await client.get("/api/session")).status_code == 200


async def test_a_revoked_message_without_the_code_does_not_mark_the_leader(
    signed_in_as, eu, fake_leader, sessionmaker
):
    fake_leader.replies[("GET", "/v1/admin/status")] = (
        401,
        {"code": "unauthorized", "message": "this console credential has been revoked"},
        {},
    )
    client = await signed_in_as("viewer")
    answer = await client.get("/api/leaders/eu-1/status")
    assert (answer.status_code, answer.json()["code"]) == (502, "leader_credential_rejected")
    async with sessionmaker() as session:
        leader = (await session.scalars(select(Leader))).one()
    assert leader.credential_revoked_at is None


async def test_a_credential_the_key_cannot_open_is_refused_without_a_call(
    signed_in_as, factory, fake_leader
):
    factory.keys = ConsoleKeys(bytes(32))
    await factory.leader("eu-1", labels={"env": "prod"})
    client = await signed_in_as("viewer")
    answer = await client.get("/api/leaders/eu-1/status")
    assert answer.json()["code"] == "leader_credential_unreadable"
    assert fake_leader.requests == []


async def test_a_disabled_leader_is_refused(signed_in_as, factory, fake_leader):
    await factory.leader("eu-1", labels={"env": "prod"}, enabled=False)
    client = await signed_in_as("viewer")
    answer = await client.get("/api/leaders/eu-1/status")
    assert (answer.status_code, answer.json()["code"]) == (409, "leader_disabled")
    assert fake_leader.requests == []


# --- join tokens ------------------------------------------------------------------------


async def test_join_token_plaintext_passes_once_and_is_kept_nowhere(
    signed_in_as, eu, fake_leader, sessionmaker, engine, caplog
):
    caplog.set_level(logging.DEBUG)
    created = {
        "id": TOKEN_ID,
        "token": TOKEN_PLAINTEXT,
        "pool": "default",
        "expires_at": "2026-10-10T00:00:00Z",
        "max_uses": 1,
    }
    fake_leader.replies[("POST", "/v1/admin/tokens")] = (201, created, {})
    client = await signed_in_as("admin")
    answer = await client.post("/api/leaders/eu-1/tokens", json={"pool": "default"})
    assert answer.status_code == 201
    assert answer.json()["token"] == TOKEN_PLAINTEXT
    assert answer.headers["cache-control"] == "no-store"
    (entry,) = await _audit(sessionmaker)
    assert (entry.action, entry.outcome) == ("tokens.create", "ok")
    assert entry.detail == {"role": "admin", "token_id": TOKEN_ID, "pool": "default"}
    assert TOKEN_PLAINTEXT not in await all_rows_text(engine)
    assert TOKEN_PLAINTEXT not in caplog.text
    assert CREDENTIAL not in caplog.text
    assert client.cookies.get(SESSION_COOKIE) not in caplog.text


# --- hostile input ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/leaders/..%2Feu-1/status",
        "/api/leaders/eu-1%2F..%2Fus-1/status",
        "/api/leaders/%2E%2E/status",
        "/api/leaders/eu-1/%2E%2E/%2E%2E/admin/leaders",
        "/api/leaders/eu-1/jobs/..%2F..%2Fconsoles",
        "/api/leaders/eu-1/jobs/not-a-uuid/retry",
        f"/api/leaders/eu-1/jobs/{JOB}%2F..%2F..%2Fconsoles/retry",
        "/api/leaders/eu-1/locations/..%2Fx/ingest",
        "/api/leaders/eu-1/consoles",
        "/api/leaders/eu-1/whoami",
        "/api/leaders/eu-1/login-config",
        "/api/leaders/eu-1/v1/admin/status",
        "/api/leaders/eu-1/status/",
        "/api/leaders/eu-1/STATUS",
        "/api/leaders/eu-1/",
    ],
)
async def test_path_tricks_never_reach_a_leader(signed_in_as, eu, fake_leader, path):
    client = await signed_in_as("admin", scope="all")
    for method in ("GET", "POST"):
        answer = await client.request(method, path)
        assert answer.status_code in (404, 405), (method, path, answer.status_code)
    assert fake_leader.requests == []


async def test_an_unknown_and_an_ungranted_leader_look_the_same(
    signed_in_as, factory, eu, fake_leader
):
    await factory.leader("us-1", labels={"env": "test"})
    client = await signed_in_as("admin")
    hidden = await client.get("/api/leaders/us-1/status")
    unknown = await client.get("/api/leaders/zz-9/status")
    assert hidden.status_code == unknown.status_code == 404
    assert hidden.json() == unknown.json()
    assert fake_leader.requests == []


async def test_an_identity_the_leader_cannot_take_is_refused(signed_in_as, eu, fake_leader):
    client = await signed_in_as("viewer", subject="has a space")
    answer = await client.get("/api/leaders/eu-1/status")
    assert (answer.status_code, answer.json()["code"]) == (403, "actor_not_representable")
    assert fake_leader.requests == []


async def test_header_injection_through_the_email_goes_nowhere(signed_in_as, eu, fake_leader):
    client = await signed_in_as("viewer", email="a@example.org\r\nX-Evil: 1")
    assert (await client.get("/api/leaders/eu-1/status")).status_code == 200
    (request,) = fake_leader.requests
    assert request.headers["x-swarmscribe-actor"] == f"{ENTRA_ISSUER} entra-person-1 -"
    assert "x-evil" not in request.headers


async def test_an_action_without_the_csrf_token_never_reaches_the_leader(
    signed_in_as, eu, fake_leader
):
    client = await signed_in_as("operator")
    del client.headers["X-CSRF-Token"]
    answer = await client.post(f"/api/leaders/eu-1/jobs/{JOB}/retry")
    assert (answer.status_code, answer.json()["code"]) == (403, "csrf_failed")
    assert fake_leader.requests == []


# --- the allow-list against the leader --------------------------------------------------


def _leader_routes() -> dict[tuple[str, str], tuple[str | None, bool]]:
    """Every leader /v1/admin route: (method, path shape) -> (required role, consoles ok)."""
    found = {}
    for route in leader_admin_router.routes:
        role, consoles_allowed = None, True
        for dependency in route.dependant.dependencies:
            call = dependency.call
            if getattr(call, "__qualname__", "") == "require.<locals>.dependency":
                captured = inspect.getclosurevars(call).nonlocals
                role, consoles_allowed = captured["role"], captured["consoles_allowed"]
        for method in route.methods:
            found[(method, re.sub(r"\{\w+\}", "{}", route.path))] = (role, consoles_allowed)
    return found


def test_the_allow_list_is_exactly_the_leaders_delegable_admin_routes_with_their_roles():
    leader = _leader_routes()
    delegable = {
        key: role
        for key, (role, consoles_allowed) in leader.items()
        if role is not None
        and consoles_allowed
        and key not in {("GET", "/v1/admin/whoami")}
    }
    offered = {
        (route.method, re.sub(r"\{\w+\}", "{}", "/v1/admin/" + route.template)): route.role
        for route in ROUTES
    }
    assert offered == delegable
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m uv run pytest packages/console/tests/test_proxy.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'swarmscribe_console.proxy'`.

- [ ] **Step 3: Write the allow-list**

Create `packages/console/src/swarmscribe_console/proxy.py`:

```python
"""The leader actions the console proxies (fleet console spec 5.4) — an allow-list.

Each entry names the method, the leader route under /v1/admin, the console role it needs
(the leader's own role for that route, so the console refuses first: spec 7, "authorised
twice"), the audit action, the query parameters passed on, and whether it takes a body.
Nothing outside this list reaches a leader. Path parameters must be UUIDs or leader-style
names, so `..`, encoded slashes and other path tricks cannot form a leader URL; test_proxy
checks the list against the leader's own router."""

import re
from dataclasses import dataclass, field
from urllib.parse import quote

UUID_PATTERN = (
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
NAME_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}"
PARAMETERS = {
    "job_id": UUID_PATTERN,
    "follower_id": UUID_PATTERN,
    "token_id": UUID_PATTERN,
    "location": NAME_PATTERN,
}
_PARAM = re.compile(r"\{(\w+)\}")


@dataclass(frozen=True)
class ProxyRoute:
    method: str
    template: str  # relative to /v1/admin/, e.g. "jobs/{job_id}/retry"
    role: str
    action: str
    query: tuple[str, ...] = ()
    body: bool = False
    pattern: re.Pattern[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        regex, position = "", 0
        for found in _PARAM.finditer(self.template):
            name = found.group(1)
            regex += re.escape(self.template[position : found.start()])
            regex += f"(?P<{name}>{PARAMETERS[name]})"
            position = found.end()
        regex += re.escape(self.template[position:])
        object.__setattr__(self, "pattern", re.compile(regex))

    def leader_path(self, params: dict[str, str]) -> str:
        quoted = {name: quote(value, safe="") for name, value in params.items()}
        return "/v1/admin/" + self.template.format(**quoted)


ROUTES: tuple[ProxyRoute, ...] = (
    ProxyRoute("GET", "status", "viewer", "status.view"),
    ProxyRoute("GET", "locations", "viewer", "locations.view"),
    ProxyRoute("GET", "jobs", "viewer", "jobs.view", query=("state", "location", "limit")),
    ProxyRoute("GET", "followers", "viewer", "followers.view", query=("state",)),
    ProxyRoute("GET", "tokens", "admin", "tokens.view"),
    ProxyRoute("GET", "consent/report", "viewer", "consent.view", query=("location", "limit")),
    ProxyRoute("POST", "locations", "admin", "locations.add", body=True),
    ProxyRoute("POST", "locations/{location}/enable", "admin", "locations.enable"),
    ProxyRoute("POST", "locations/{location}/disable", "admin", "locations.disable"),
    ProxyRoute("POST", "locations/{location}/ingest", "operator", "locations.ingest"),
    ProxyRoute("POST", "jobs/{job_id}/retry", "operator", "jobs.retry"),
    ProxyRoute("POST", "jobs/{job_id}/cancel", "operator", "jobs.cancel"),
    ProxyRoute("POST", "jobs/{job_id}/priority", "operator", "jobs.priority", body=True),
    ProxyRoute("POST", "followers/{follower_id}/drain", "operator", "followers.drain"),
    ProxyRoute("POST", "followers/{follower_id}/revoke", "admin", "followers.revoke"),
    ProxyRoute("POST", "tokens", "admin", "tokens.create", body=True),
    ProxyRoute("POST", "tokens/{token_id}/revoke", "admin", "tokens.revoke"),
)


def match_route(method: str, rest: str) -> tuple[ProxyRoute, dict[str, str]] | None:
    for route in ROUTES:
        if route.method == method:
            found = route.pattern.fullmatch(rest)
            if found is not None:
                return route, found.groupdict()
    return None


def target_of(params: dict[str, str]) -> str | None:
    return ", ".join(f"{name}={value}" for name, value in sorted(params.items())) or None
```

- [ ] **Step 4: Write the proxy route**

Create `packages/console/src/swarmscribe_console/api/proxy.py`:

```python
"""GET and POST /api/leaders/{name}/... — the allow-listed leader actions, proxied live with
the signed-in person as actor and their console role for that leader (fleet console spec
5.4). The console refuses first (unknown or ungranted leader, role below the route's,
disabled, revoked, unsendable identity, odd query or body); then the leader applies its cap
and role checks. A leader 401 never reaches the browser as a 401. Every POST is audited."""

import json
import re
from typing import Any, NoReturn

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from swarmscribe_leader.auth.roles import at_least
from swarmscribe_leader.clock import utcnow

from .. import audit
from ..crypto import CredentialUnreadable
from ..db.models import Leader
from ..errors import (
    BadGateway,
    Conflict,
    ConsoleError,
    CredentialRejected,
    CredentialRevoked,
    CredentialUnreadableError,
    Forbidden,
    Invalid,
    LeaderUnavailable,
    NotFound,
    PassedThrough,
    PayloadTooLarge,
)
from ..leader_client import (
    LeaderBadAnswer,
    LeaderReply,
    LeaderTarget,
    LeaderUnreachable,
    is_revoked,
    person_actor,
)
from ..leaders import sealing_context
from ..poller import mark_revoked
from ..proxy import ProxyRoute, match_route, target_of
from ..sessions import SignedIn
from .deps import Person, Session, keys_of, settings_of
from .fleet import leader_for

router = APIRouter(prefix="/api/leaders")

MAX_BODY_BYTES = 64 * 1024
MAX_QUERY_CHARS = 200
MAX_RETRY_AFTER = 3600
_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")


def _forwarded_query(request: Request, route: ProxyRoute) -> dict[str, str]:
    query: dict[str, str] = {}
    for name in route.query:
        values = request.query_params.getlist(name)
        if len(values) > 1:
            raise Invalid(f"send {name} once")
        if values:
            value = values[0]
            if len(value) > MAX_QUERY_CHARS or not value.isprintable():
                raise Invalid(f"{name} must be at most {MAX_QUERY_CHARS} printable characters")
            query[name] = value
    return query


async def _forwarded_body(request: Request, route: ProxyRoute) -> dict[str, Any] | None:
    if not route.body:
        return None
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise PayloadTooLarge("the request body is larger than 64 KiB")
    try:
        body = json.loads(raw) if raw else None
    except ValueError:
        raise Invalid("the request body is not JSON") from None
    if not isinstance(body, dict):
        raise Invalid("the request body must be a JSON object")
    return body


def _passed_through(reply: LeaderReply) -> ConsoleError:
    body = reply.body
    if 400 <= reply.status <= 599 and isinstance(body, dict):
        code, message = body.get("code"), body.get("message")
        if isinstance(code, str) and _CODE.fullmatch(code) and isinstance(message, str):
            retry = reply.retry_after
            retry_after = min(int(retry), MAX_RETRY_AFTER) if retry and retry.isdigit() else None
            return PassedThrough(reply.status, code, message[:500], retry_after)
    return BadGateway(f"the leader answered HTTP {reply.status} without a usable error")


def _detail(route: ProxyRoute, role: str, body: Any) -> dict[str, Any]:
    detail: dict[str, Any] = {"role": role}
    if route.action == "tokens.create" and isinstance(body, dict):
        # Never "token": the plaintext passes to the browser once and is recorded nowhere.
        for key, name in (("id", "token_id"), ("pool", "pool")):
            value = body.get(key)
            if isinstance(value, str) and len(value) <= 100:
                detail[name] = value
    return detail


async def _audit(
    request: Request,
    person: SignedIn,
    route: ProxyRoute,
    leader: str,
    target: str | None,
    outcome: str,
    detail: dict[str, Any],
) -> None:
    if route.method != "POST":
        return
    request.state.audited = True  # the error handler must not add a second entry
    await audit.record_apart(
        request.app.state.sessionmaker,
        actor=person.actor,
        action=route.action,
        leader=leader,
        target=target,
        outcome=outcome,
        detail=detail,
    )


@router.api_route("/{name}/{rest:path}", methods=["GET", "POST"])
async def proxied(
    name: str, rest: str, request: Request, person: Person, session: Session
) -> Response:
    found = match_route(request.method, rest)
    if found is None:
        raise NotFound("the console does not offer that leader action")
    route, params = found
    leader, role = await leader_for(session, person, name)
    target = target_of(params)

    async def refuse(error: ConsoleError) -> NoReturn:
        await _audit(request, person, route, leader.name, target, error.code, {"role": role})
        raise error

    if not at_least(role, route.role):
        await refuse(
            Forbidden(f"this needs the {route.role} role on {leader.name}; you have {role}")
        )
    if not leader.enabled:
        await refuse(Conflict("this leader is disabled in the console", code="leader_disabled"))
    if leader.credential_revoked_at is not None:
        await refuse(
            CredentialRevoked(
                f"leader {leader.name} revoked this console's credential; "
                "a console administrator must replace it"
            )
        )
    try:
        actor = person_actor(person.issuer, person.subject, person.email)
        query = _forwarded_query(request, route)
        body = await _forwarded_body(request, route)
    except ConsoleError as exc:
        await refuse(exc)
    sealed = leader.credential
    try:
        # Bound to the name and URL, from the same row as `sealed` (C2a `sealing_context`).
        context = sealing_context(leader.name, leader.base_url)
        credential = keys_of(request).open_credential(context, sealed)
    except CredentialUnreadable:
        await refuse(
            CredentialUnreadableError(
                f"the stored credential for {leader.name} cannot be read; replace it"
            )
        )
    try:
        reply = await request.app.state.leader_client.call(
            LeaderTarget(leader.name, leader.base_url, credential),
            route.method,
            route.leader_path(params),
            actor=actor,
            role=role,
            timeout=settings_of(request).proxy_timeout_seconds,
            params=query or None,
            json_body=body,
        )
    except LeaderUnreachable:
        await refuse(LeaderUnavailable(f"leader {leader.name} cannot be reached; try again"))
    except LeaderBadAnswer:
        await refuse(BadGateway(f"leader {leader.name} answered too much"))

    if is_revoked(reply):
        async with request.app.state.sessionmaker() as marking:
            fresh = await marking.get(
                Leader, leader.id, with_for_update=True, populate_existing=True
            )
            if fresh is not None:
                await mark_revoked(marking, fresh, sealed, now=utcnow(), actor=person.actor)
            await marking.commit()
        await refuse(
            CredentialRevoked(
                f"leader {leader.name} revoked this console's credential; "
                "a console administrator must replace it"
            )
        )
    if reply.status == 401:
        await refuse(CredentialRejected(f"leader {leader.name} does not accept the credential"))
    if 200 <= reply.status < 300:
        await _audit(
            request, person, route, leader.name, target, "ok", _detail(route, role, reply.body)
        )
        if reply.body is None:
            return Response(status_code=reply.status)
        return JSONResponse(reply.body, status_code=reply.status)
    await refuse(_passed_through(reply))
```

In `packages/console/src/swarmscribe_console/app.py`, add `from .api import proxy as proxy_api` to the `.api` imports and, after `app.include_router(fleet_api.router)` (the fleet router's `/api/leaders/{name}/history` must be matched first), add:

```python
    app.include_router(proxy_api.router)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m uv run pytest packages/console/tests -v`
Expected: PASS — including C2a's structural CSRF test, which now also walks the proxy's `POST` route.

Run: `python -m uv run ruff check packages/console`
Expected: no findings.

- [ ] **Step 6: Commit**

```bash
git add packages/console/src/swarmscribe_console/proxy.py packages/console/src/swarmscribe_console/api/proxy.py packages/console/src/swarmscribe_console/app.py packages/console/tests/test_proxy.py
git commit -m "Console: allow-listed leader proxy with actor and role, audited actions

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Static files under the CSP, the log sweep, README

**Files:**
- Create: `packages/console/src/swarmscribe_console/static.py`
- Modify: `packages/console/src/swarmscribe_console/config.py` (`static_dir`), `packages/console/src/swarmscribe_console/app.py` (mount)
- Modify: `README.md`
- Test: `packages/console/tests/test_static_and_logs.py`

**Interfaces:**
- Consumes: everything above; `main.main`, `main.LOGGING` (C2a).
- Produces:
  - `Settings.static_dir: Path | None` (`SWARMSCRIBE_CONSOLE_STATIC_DIR`; must be a folder holding `index.html`).
  - `static.SpaFiles(StaticFiles)`; mounted at `/` when `static_dir` is set.

- [ ] **Step 1: Write the failing tests**

Create `packages/console/tests/test_static_and_logs.py`:

```python
import logging

import httpx
import pytest
from console_testkit import (
    ENTRA_SECRET,
    GROUPS,
    PUBLIC_URL,
    all_rows_text,
    console_env,
)
from pydantic import ValidationError
from swarmscribe_console.app import create_app
from swarmscribe_console.main import main
from swarmscribe_console.poller import poll_due_leaders
from swarmscribe_console.sessions import SESSION_COOKIE
from swarmscribe_leader.clock import utcnow

INDEX = "<!doctype html><title>SwarmScribe console</title>"


@pytest.fixture
def web(tmp_path):
    folder = tmp_path / "web"
    (folder / "assets").mkdir(parents=True)
    (folder / "index.html").write_text(INDEX, encoding="utf-8")
    (folder / "assets" / "app.js").write_text("console.log('console')", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("not for the web", encoding="utf-8")
    return folder


@pytest.fixture
async def web_client(engine, make_settings, idp, fake_leader, web):
    application = create_app(
        make_settings(static_dir=str(web)),
        background=False,
        fetch=idp.fetch,
        idp_transport=idp.transport,
        leader_transport=fake_leader.transport,
    )
    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url=PUBLIC_URL
        ) as made:
            yield made


# --- static files -----------------------------------------------------------------------


async def test_the_web_app_is_served_under_the_csp(web_client):
    answer = await web_client.get("/")
    assert answer.status_code == 200
    assert answer.text == INDEX
    assert "script-src 'self'" in answer.headers["content-security-policy"]
    script = await web_client.get("/assets/app.js")
    assert script.status_code == 200
    assert "script-src 'self'" in script.headers["content-security-policy"]


@pytest.mark.parametrize("path", ["/leaders/eu-1", "/fleet", "/leaders/eu-1/jobs"])
async def test_the_web_apps_own_routes_get_index_html(web_client, path):
    answer = await web_client.get(path)
    assert (answer.status_code, answer.text) == (200, INDEX)


@pytest.mark.parametrize(
    "path",
    [
        "/assets/missing.js",
        "/api/nothing-here",
        "/auth/nothing-here",
        "/..%2fsecret.txt",
        "/assets/..%2f..%2fsecret.txt",
    ],
)
async def test_missing_files_api_paths_and_escapes_are_not_index_html(web_client, path):
    answer = await web_client.get(path)
    assert answer.status_code == 404
    assert INDEX not in answer.text
    assert "not for the web" not in answer.text


async def test_the_api_still_answers_beside_the_web_app(web_client):
    assert (await web_client.get("/api/session")).status_code == 401
    assert (await web_client.get("/auth/providers")).status_code == 200


def test_the_static_folder_must_hold_index_html(make_settings, tmp_path):
    with pytest.raises(ValidationError, match="index.html"):
        make_settings(static_dir=str(tmp_path))
    with pytest.raises(ValidationError, match="index.html"):
        make_settings(static_dir=str(tmp_path / "missing"))


async def test_without_a_static_folder_the_root_is_a_404(client):
    assert (await client.get("/")).status_code == 404


# --- no secret in the logs or the database ----------------------------------------------


async def test_no_secret_reaches_the_logs_or_the_database(
    app, client, idp, factory, fake_leader, engine, caplog
):
    caplog.set_level(logging.DEBUG)
    await factory.grant("admin", "all", "entra_group", GROUPS["admin"])
    await factory.console_admin("entra_group", GROUPS["admin"])

    started = await client.get("/auth/login", params={"provider": "entra"})
    callback = idp.authorize(started.headers["location"], groups=[GROUPS["admin"]])
    code = dict(httpx.URL(callback).params)["code"]
    assert (await client.get(callback)).status_code == 200
    session_id = client.cookies.get(SESSION_COOKIE)
    csrf = (await client.get("/api/session")).json()["csrf_token"]
    client.headers["X-CSRF-Token"] = csrf

    credential = "S" * 20 + "_-" + "c" * 21
    registered = await client.post(
        "/api/admin/leaders",
        json={
            "name": "eu-1",
            "base_url": "https://eu-1.leaders.example",
            "credential": credential,
        },
    )
    assert registered.status_code == 201
    state = app.state
    outcomes = await poll_due_leaders(
        state.engine,
        state.sessionmaker,
        state.leader_client,
        state.keys,
        now=utcnow(),
        config=state.poller_config,
    )
    assert outcomes == {"eu-1": "ok"}

    plaintext = "T" * 20 + "-_" + "k" * 21
    fake_leader.replies[("POST", "/v1/admin/tokens")] = (
        201,
        {
            "id": "99999999-8888-4777-8666-555555555555",
            "token": plaintext,
            "pool": "default",
            "expires_at": "2026-10-10T00:00:00Z",
            "max_uses": 1,
        },
        {},
    )
    assert (await client.post("/api/leaders/eu-1/tokens", json={})).status_code == 201
    assert (await client.get("/api/leaders/eu-1/status")).status_code == 200
    refused = await client.post(
        "/api/leaders/eu-1/tokens", json={}, headers={"X-CSRF-Token": "wrong"}
    )
    assert refused.status_code == 403
    assert (await client.post("/api/session/logout")).status_code == 204

    assert fake_leader.requests[0].headers["authorization"] == f"Console {credential}"
    secrets = [
        session_id,
        csrf,
        credential,
        plaintext,
        code,
        idp.exchanges[0]["code_verifier"],
        ENTRA_SECRET,
        *idp.issued,
    ]
    logged = caplog.text
    stored = await all_rows_text(engine)
    for secret in secrets:
        assert secret not in logged
        assert secret not in stored


def test_serve_runs_without_an_access_log_and_with_quiet_http_clients(
    migrated_database_url, monkeypatch
):
    import logging.config

    import uvicorn

    console_env(monkeypatch, migrated_database_url)
    seen: dict = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: seen.update(kwargs))
    monkeypatch.setattr(logging.config, "dictConfig", lambda config: seen.update(logging=config))
    assert main(["serve", "--port", "8443"]) == 0
    assert seen["access_log"] is False
    assert seen["port"] == 8443
    loggers = seen["logging"]["loggers"]
    assert loggers["httpx"]["level"] == loggers["httpcore"]["level"] == "WARNING"
```

Extend the sweep with three cases C2a's final review found and left open (the secrets list above already covers the credentials, cookies, codes and tokens):

- **Field-name echo.** A request that sends an unknown field (`{"SECRETKEY-xyz": 1}`) to a proxy or admin route is refused `422` with fixed text, and neither the key nor any value appears in the response, the logs or the audit rows. C2a's `invalid_summary` renders fixed text by error type and drops any field path that is not one of the console's own lowercase field names; the new Task 4 request models must stay `extra="forbid"` with no `dict[str, <typed>]` field whose errors would carry a client key.
- **Error logging.** An unhandled exception is logged once by `ContainErrors`: type, route and traceback frames (`traceback.format_tb`), never the exception's text. Assert it for a failure raised inside the proxy and inside a poll step: a log line with the frames and none of the plaintext token, credential or leader reply body.
- **Statement parameters.** The console's engine is created with `hide_parameters=True` (C2a does this in `create_app`, `_schema_problem` and the `admins` command). Add a test that enables `sqlalchemy.engine` at INFO plus a failing statement and checks the sign-in nonce and PKCE verifier (the `login_attempts` insert's parameters) are in no log record. Keep `hide_parameters=True` on any engine this plan creates.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m uv run pytest packages/console/tests/test_static_and_logs.py -v`
Expected: FAIL — `static_dir` is not a setting (the web-app tests), while the log sweep and `serve` tests may already pass (they pin behaviour built in earlier tasks).

- [ ] **Step 3: Add the setting and the static files**

In `packages/console/src/swarmscribe_console/config.py`, add after `proxy_timeout_seconds`:

```python
    static_dir: Path | None = None  # the built web app (C3): a folder holding index.html
```

and add this validator to `Settings`:

```python
    @field_validator("static_dir")
    @classmethod
    def _static_dir_holds_the_app(cls, value: Path | None) -> Path | None:
        if value is not None and not (value / "index.html").is_file():
            raise ValueError("static_dir must be a folder holding the web app's index.html")
        return value
```

Create `packages/console/src/swarmscribe_console/static.py`:

```python
"""The built web app (C3), served by the console under its security headers (fleet console
spec 3, 7). An extensionless path that is not a file gets index.html, so the web app's own
routes survive a reload; /api/ and /auth/ never do, and StaticFiles keeps every path inside
the folder."""

from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

_NEVER_THE_APP = ("/api/", "/auth/")


class SpaFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            last = path.rsplit("/", 1)[-1]
            if (
                exc.status_code != 404
                or scope["path"].startswith(_NEVER_THE_APP)
                or "." in last
                or ".." in path.split("/")
            ):
                raise
            return await super().get_response("index.html", scope)
```

In `packages/console/src/swarmscribe_console/app.py`, add `from .static import SpaFiles` to the imports and, after the last `app.include_router(...)` and **before** the existing `assert_guarded(app.routes)` (which stays the last statement), add:

```python
    if settings.static_dir is not None:
        # Last of the routes, so every API and sign-in route is matched first.
        app.mount("/", SpaFiles(directory=settings.static_dir, html=True), name="web")
```

Never mount anything under `/` that contains `/api` routes. `assert_guarded` cannot prove what a mounted sub-app guards, so it refuses a root `Mount("/")` holding an `/api` route and the app will not build. API routes go in routers included on the app itself.

- [ ] **Step 4: Document leaders, rotation, the poller and the proxy**

In `README.md`, add this as a `###` subsection of "Run the fleet console (development)", after C2a's "Console administrators and grants" subsection and **before** its "Deployment note: egress". Merge it with what C2a wrote; do not repeat C2a's egress rules or registry rules (`credential_required`, `use_rotate`):

````markdown
### Leaders in the console

On each leader, a leader administrator creates a credential for the console:

```
swarmscribe-admin console create --name fleet --max-role operator
```

Prefer `operator`: the leader trusts the console's word for who the person is,
so the cap is the only bound on what a leaked credential can do. Use `admin`
only if people must create join tokens or locations from the console.

A console administrator then registers the leader (`POST /api/admin/leaders`
with its name, `https://` URL, labels and the credential). Which URLs are
refused, and the egress policy to apply at the host, are in "Deployment note:
egress" above and are not repeated here. The credential is sealed with
`SWARMSCRIBE_CONSOLE_KEY`, bound to the leader's name and URL, and never shown
again. Changing a leader's `base_url` needs the credential in the same request
(the sealed one is bound to the old URL); the change resets the leader's poll
health (failure count, last error, last poll time) and clears a revoked mark,
exactly as a rotation does.

**Rotation.** Console names are never reused on a leader. Create a new one
(`console create --name fleet-2 …`), replace the credential in the console in
place (`PUT /api/admin/leaders/<name>/credential`), then revoke the old one
(`console revoke fleet`).

**Polling.** Every 15 seconds the console reads each leader's status as
`system:poller` (viewer); the leader does not audit these reads. A leader that
fails three polls in a row is shown unreachable (within a minute). A leader
that answers `401 credential_revoked` is shown as revoked and not polled again
until its credential is replaced. Snapshots are kept for 24 hours; the overview
shows each leader's queue, completions in the last hour and day, failures,
followers by pool, the oldest queued job's age and scan errors, all from the
latest successful one.

**Actions.** The web app calls `/api/leaders/<name>/…`; the console checks the
person's role there, then forwards the call with the person as actor, and the
leader applies its own cap and role checks. Every action is audited in the
console and in the leader. A join token's plaintext is shown once and kept
nowhere.

`SWARMSCRIBE_CONSOLE_STATIC_DIR` points at the built web app (C3).
````

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m uv run pytest packages/console/tests -v`
Expected: PASS (all of C2a and C2b).

Run: `python -m uv run pytest packages -q`
Expected: PASS — the leader, engine and protocol suites are untouched.

Run: `python -m uv run ruff check .`
Expected: no findings.

- [ ] **Step 6: Commit**

```bash
git add packages/console/src/swarmscribe_console/static.py packages/console/src/swarmscribe_console/config.py packages/console/src/swarmscribe_console/app.py packages/console/tests/test_static_and_logs.py README.md
git commit -m "Console: web app served under the CSP, no secret in logs, README

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Self-review

**Spec coverage (5.3, 5.4, and 1/7/9 where they apply).** One loop per replica, exclusive per leader by advisory lock: Task 2 (`run_exclusive` keyed by leader id; `test_two_replicas_poll_a_leader_once`, `test_a_leader_locked_by_another_replica_is_skipped`). Every 15 s, `GET /v1/admin/status` with followers by pool (C1 amendment 2), as `system:poller`/viewer, 5 s timeout: Task 2 (`test_a_healthy_leader_is_polled_as_the_poller…`, `test_a_leader_is_polled_at_most_once_per_interval`, `test_a_slow_leader_times_out_as_a_failure`). Latest snapshot and 24 h history, pruned hourly: Tasks 2 and 3. Three failures → unreachable: Task 2 (count) and Task 3 (`health`). Revoked → stop polling until replaced (C1 follow-up), shown as revoked, detected by C1b's code only (owner ruling): Tasks 1 (`test_revocation_is_recognised_by_the_code_only`), 2 (the `revoked-message-without-the-code` case), 3, 4 (`test_a_revoked_message_without_the_code_does_not_mark_the_leader`). `GET /api/fleet` with latest snapshot, and spec 6's overview figures from status alone using C1b's `completed_last_day` and `oldest_queued_age_s` (owner ruling): Task 3 (`test_the_overview_row_comes_from_the_status_alone`, `test_nothing_queued_shows_no_queue_age`, the two fields in history); the poller requires both fields (Task 2's `no-completed-last-day`/`no-queue-age` cases, `test_nothing_queued_is_a_valid_status`). Owner rulings are recorded in their own section. Proxied reads (jobs, followers, locations, tokens, consent report) and actions with the person as actor and their role: Task 4, with the allow-list checked against the leader's router and roles. Join-token plaintext once, never stored or logged: Task 4 and Task 5. Leader errors passed through with their code; `leader_unreachable` 503: Task 4. Authorised twice: Task 4 (`test_the_console_refuses_below_the_routes_role…`, `test_the_leaders_own_refusal_passes_through`). Console audit of every action: Task 4. Unreachable within a minute, nothing else stops: Tasks 2 and 4 (`test_an_unreachable_leader_is_503_and_others_still_work`). Logs never contain cookies, CSRF tokens, credentials, join tokens (link URLs: the leader admin API returns none): Task 5's end-to-end sweep. CSP on static files: Task 5. Section 9 lines owned here: poller healthy/slow/failing three times, proxy forwarding actor and role, token plaintext passed once and absent from logs. Not here: C3 (web app), C4 (image, Helm, Compose; the egress policy noted in C2a's decisions).

**Placeholder scan.** Every code step carries complete code; every run step its command and expected result. Insertions into existing files (config, conftest, app) name the exact block and its place.

**Type consistency.** `LeaderClient.call(target, method, path, *, actor, role, timeout, params=None, json_body=None) -> LeaderReply` is called with those keywords by `poll_leader` and `api/proxy.py`. `LeaderUnreachable.reason` is read by `poll_leader`. `mark_revoked(session, leader, sealed, *, now, actor)` is called by `_record` and the proxy. `PollerConfig.unreachable_after` is read by `api/fleet.py` through `app.state.poller_config`. `is_revoked` compares with `REVOKED_CODE`; `REVOKED_BODY` in the test kit carries that code. `StatusPayload`'s `completed_last_day`/`oldest_queued_age_s`, `STATUS`'s values (30, 420), `summary_of`'s keys, `FleetSummary`'s fields and `HistoryPoint`'s two new fields use the same names as C1b's `Status`. `leader_for(session, person, name) -> (Leader, role)` is defined in Task 3 and used in Task 4. `Factory.leader(...)` (C2a) and `Factory.snapshot(leader, *, taken_at, reachable, outcome, status)` (Task 3) match their call sites. `create_app(settings, *, background, fetch, idp_transport, graph, google_groups, leader_transport)` matches the conftest fixture and the tests that build their own app. Audit actions: `leader.credential_revoked`, and the `ProxyRoute.action` strings in the table — the same in code and tests.

**Review Focus.** 1 → `test_path_tricks_never_reach_a_leader` (15 paths × 2 methods); 2 → `test_an_email_the_leader_would_refuse_is_sent_as_a_dash`, `test_an_identity_the_leader_cannot_take_is_refused` (Task 1) and `test_header_injection_through_the_email_goes_nowhere`, `test_an_identity_the_leader_cannot_take_is_refused` (Task 4); 3 → `test_a_revoked_credential_stops_polling_until_it_is_replaced`, `test_a_credential_rotated_during_a_poll_is_not_marked_by_its_answer`, `test_health_follows_the_pollers_state`, `test_a_revoked_credential_marks_the_leader_and_stops_calls`, `test_revocation_is_recognised_by_the_code_only`, `test_a_revoked_message_without_the_code_does_not_mark_the_leader`; 4 → the slow/down tests in Tasks 1, 2 and 4 and `test_every_due_leader_is_polled_in_one_round`; 5 → `test_join_token_plaintext_passes_once_and_is_kept_nowhere`, `test_no_secret_reaches_the_logs_or_the_database`; 6 → `test_odd_answers_are_failures_and_store_no_status` (now 12 cases), `test_nothing_queued_is_a_valid_status`, `test_an_unusable_leader_answer_is_a_bad_gateway`, `test_an_oversized_answer_is_refused`, `test_a_redirect_is_never_followed`; 7 → `test_a_leader_without_a_grant_is_answered_like_an_unknown_one`, `test_an_unknown_and_an_ungranted_leader_look_the_same`; 8 → `test_the_allow_list_is_exactly_the_leaders_delegable_admin_routes_with_their_roles`; 9 → the two replica tests; 10 → `test_an_unknown_credential_is_never_a_401_to_the_browser` and the revoked proxy test.
