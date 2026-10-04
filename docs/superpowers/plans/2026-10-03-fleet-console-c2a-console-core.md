# Fleet Console C2a — Console Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A new workspace package `swarmscribe-console` with its own Postgres and Alembic history, where a person signs in through Entra ID or Google (OIDC authorization code + PKCE) into a server-side session (HttpOnly, Secure, SameSite=Strict cookie; 8-hour lifetime, 1-hour idle timeout; CSRF token on every state-changing request), and console administrators manage the leader registry (credentials sealed with AES-GCM), role grants and console administrators, every change audited in the console's own `audit_log`.

**Architecture:** FastAPI app in `packages/console/src/swarmscribe_console`. Settings come from `SWARMSCRIBE_CONSOLE_*`; `crypto.ConsoleKeys` derives two keys from `SWARMSCRIBE_CONSOLE_KEY` with HKDF, one for AES-GCM sealing of leader credentials (bound to the leader's name as associated data) and one for the per-session CSRF HMAC. Sign-in reuses the leader's `TokenVerifier` (signature, `iss`, `aud`, `exp`, tenant, `email_verified`, `hd`) and its Microsoft Graph and Cloud Identity clients by importing `swarmscribe_leader` as a library; the console adds the browser half (authorization URL, a single-use state bound to the browser by a SameSite=Lax login cookie, nonce, PKCE S256, code exchange) and computes the person's *principals* (Entra group ids; Google groups, email and domain under the leader's Workspace rules) once, into the session. Grants are read from the database on every request and matched against the session's principals: the highest role whose scope (`leader:<name>`, `label:<k>=<v>`, `all`) matches the leader wins. The poller, fleet view and action proxy are C2b.

**Tech Stack:** Python 3.11+, uv workspace, FastAPI, Pydantic v2 + pydantic-settings, SQLAlchemy 2.0 async + Alembic on Postgres, httpx, PyJWT, `cryptography` (AES-GCM, HKDF), pytest + pytest-asyncio, ruff.

**Spec:** `docs/superpowers/specs/2026-10-03-fleet-console-design.md` (approved; the authority) — section 5.1 and 5.2 are this plan's scope, with sections 7, 8 and 9 where they apply. Sign-in rules: `docs/superpowers/specs/2026-10-02-leader-design.md` section 10. The leader-side contract is C1: `docs/superpowers/plans/2026-10-03-fleet-console-c1-leader.md` ("Decisions" and "Spec amendments") and `docs/superpowers/plans/2026-10-03-fleet-console-c1-followups.md`. C2b (`docs/superpowers/plans/2026-10-03-fleet-console-c2b-poller-proxy.md`) builds on this plan.

**Precondition:** PR #5 (`fleet-console-leader`, C1) is merged to `main`, and this work branches from `main`:

```bash
git switch main
git pull
git switch -c fleet-console-c2a
```

Before Task 1, confirm `packages/leader/src/swarmscribe_leader/auth/consoles.py` exists on `main` (C1's module; C2b imports it, and C2a's code must not drift from C1's actor format).

## Why two plans

C2 is about twelve right-sized tasks. Split at the seam where each half is working software on its own:

- **C2a (this plan):** data, crypto, sign-in, sessions, CSRF, grants, the registry and grant administration, audit. Shippable result: a console people can sign in to and that console administrators can fill with leaders (credentials sealed), grants and administrators — testable end to end with a fake identity provider, with no leader contacted.
- **C2b:** the leader client, poller (with the revoked-credential stop from the C1 follow-ups), fleet view and history, the allow-listed action proxy with console audit, static files under the CSP, and the log-hygiene sweep. Shippable result: the full backend C3 builds on.

C2b depends only on C2a's interfaces listed in each task's **Produces** block.

## Global Constraints

- Spec 5.1 tables, verbatim: `leaders` "name (unique), base URL (https), labels (map), enabled, credential (encrypted), added_by"; `role_grants` "role (viewer/operator/admin), scope (`leader:<name>` or `label:<key>=<value>` or `all`), principal (Entra group id, Google group, email, domain)"; `console_admins` "principals allowed to manage leaders and grants"; `snapshots` "per leader: time, reachable, status payload"; `audit_log` "actor, action, leader, target, outcome, time".
- "Leader credentials are encrypted at rest (AES-GCM) with a key from `SWARMSCRIBE_CONSOLE_KEY` … never returned by the API."
- "Browser sign-in with OIDC authorization code + PKCE against Entra ID or Google (same providers, validation rules and ASCII/Workspace rules as the leader spec section 10)."
- "Session: server-side session id in an `HttpOnly`, `Secure`, `SameSite=Strict` cookie, 8-hour lifetime, idle timeout 1 hour; CSRF token required on every state-changing request."
- "A person's role for a leader is the highest grant whose scope matches the leader (by name, by one of its labels, or `all`). Console admins manage the registry and grants only; they hold no leader role unless granted one."
- Spec 7: "leader URLs must be https"; "it never holds people's ID or refresh tokens after sign-in completes"; "Logs never contain cookies, CSRF tokens, leader credentials, join tokens or link URLs"; "Content Security Policy restricts scripts to the console's own origin."
- Roles are cumulative: admin ⊃ operator ⊃ viewer (leader spec section 10).
- Leader console credentials are C1's format: exactly 43 URL-safe base64 characters (`[A-Za-z0-9_-]{43}`).
- The console's audit actor for a person is the leader's format, `<email or unknown> (<issuer> <subject>)` (leader `Identity.actor`).
- Dependency rule: `swarmscribe-console` may import `swarmscribe_protocol` and `swarmscribe_leader` (library use only: no leader settings, no leader database); `swarmscribe_protocol` stays a pure models package; nothing imports `swarmscribe_console`. C2a changes no leader file.
- Migration `0001` is the console's first revision; its Alembic history is separate from the leader's.
- On this Windows machine `uv` is not on PATH: every command is written `python -m uv run …`. The console tests need Postgres (`pgserver` starts one automatically, shared with the leader tests, in its own database `swarmscribe_console_test`).
- If `ruff check` flags import order or line length in code copied from this plan, run `python -m uv run ruff check --fix --select I packages/console` or wrap the line without changing behaviour.
- Test helpers shared between console test files live in `packages/console/tests/console_testkit.py`, which `conftest.py` puts on `sys.path`. Do **not** add `tests/__init__.py` or import from `conftest`: under the repo's `--import-mode=importlib` a `tests` package clashes with the other packages' test directories (verified).
- Commits end with a blank line and `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Do not push.

## Review Focus

Hostile and malformed input the spec implies but does not spell out. Each line names the behaviour a reasonable person expects and the task whose tests pin it.

1. **Forged, malformed or stale session cookies** (random 43 characters, 10 000 characters, non-ASCII, a session past 8 hours, a session idle 61 minutes) → `401 unauthenticated`, the cookie cleared, the stale row deleted, no database error. — Task 4.
2. **Session fixation** (a cookie the browser already holds when it signs in, whether forged or another person's real session) → sign-in always issues a fresh id. A cross-site callback does not carry the Strict session cookie, so the old session is ended at `/auth/login` (same-site) and, if its hash was stored with the pending sign-in, deleted at the callback (controller ruling R5). — Task 5.
3. **CSRF bypass attempts** (no token, a wrong token, another session's token, the token sent twice, a correct token from a foreign `Origin`) on every `POST`/`PUT`/`PATCH`/`DELETE` under `/api` → `403 csrf_failed`, audited, nothing changed; a structural test walks every state-changing route, including routes later plans add. — Task 4.
4. **Open redirects** via the return URL (`https://evil.example`, `//evil.example`, `/\evil.example`, `javascript:…`, tabs, over-long) or via the callback (`return_to` added to the callback query, a forged or replayed `state`) → the person lands on `/`; the return URL is taken only from the server-side sign-in record. — Task 5.
5. **SSRF and odd leader URLs** (`http://`, user:password@, query/fragment, loopback, link-local and cloud metadata `169.254.169.254`, `[::1]`, `[::ffff:127.0.0.1]`, `0.0.0.0`, multicast/reserved, decimal or hex IPs like `2130706433`/`0x7f000001`, `localhost`/`*.localhost`/trailing dot, `metadata.google.internal`, `..` in the path, bad ports, spaces, non-ASCII hosts) → `422 invalid_url`; private RFC 1918 and ULA addresses are allowed (ruling below). — Task 6.
6. **A label value containing `=`** (`label:region=eu=west`) → the scope splits at the first `=` (keys cannot hold one) and matches a leader labelled `region: eu=west` only. — Task 3, Task 7.
7. **Grant principals in odd spellings** (upper-case email or GUID, `@domain`, Unicode look-alikes such as KELVIN SIGN, a non-GUID Entra group, two `@`) → stored in one canonical lowercase ASCII form or refused with `422 invalid_principal`, so a grant can never fail to match because of case. — Task 3, Task 7.
8. **Tokens kept after sign-in** → no ID, access or refresh token, authorization code or PKCE verifier remains in any table after the callback, and the pending sign-in row is gone. — Task 5.
9. **Credential shown back or stored readable** → no API response carries a leader credential; the stored bytes do not contain it; a sealed credential copied onto another leader's row does not decrypt. — Task 1, Task 6.
10. **Removing the last console administrator** (including oneself) → `409 last_admin`, so the registry can never become unmanageable from the console. — Task 7.

## Owner rulings 2026-10-03

- **Shared code: import the leader as a library now**; a separate identity package waits until a third consumer appears — owner ruling 2026-10-03.
- **Private leader addresses are allowed** (RFC 1918, ULA, CGNAT); loopback, link-local/metadata, multicast, reserved and localhost names stay refused, and C4 adds an egress network policy blocking link-local — owner ruling 2026-10-03.
- **Group membership is read at sign-in and kept in the session; a change applies at the next sign-in, up to 8 hours later** — owner ruling 2026-10-03.
- **The console does not audit proxied reads** (the leader does; built in C2b) — owner ruling 2026-10-03 (accepted with the recommendations).
- The leader-side additions (revoked code, two status fields) are C1b and affect C2b only — owner ruling 2026-10-03.

## Decisions this plan makes (the spec is silent)

- **Shared code lives in the leader package, imported as a library** (owner ruling above). The console depends on `swarmscribe-leader` and imports `auth.oidc` (`TokenVerifier`, `Provider`, `Identity`, issuer constants), `auth.roles` (`MicrosoftGraph`, `GoogleCloudIdentity`, `RoleLookupFailed`, `RANK`, `highest`, `at_least`, `CONSUMER_GOOGLE_DOMAINS`), `auth.secrets`, `clock.utcnow`, `db.session`, `api.body_limit.BodyLimit` and (C2b) `auth.consoles` constants and `background.run_periodically`. No code is copied and no leader file changes; `swarmscribe_protocol` stays pure models.
- **Crypto dependency: `cryptography`** (already in the lock through `pyjwt[crypto]`, version 50.0.2), declared directly as `cryptography>=42`. AES-GCM with a 12-byte random nonce; stored form `0x01 ‖ nonce ‖ ciphertext+tag`; associated data `swarmscribe-console/leader:<lowercase name>`, so a sealed credential is bound to its leader. The format byte leaves room for key rotation (not built).
- **One setting, two keys.** `SWARMSCRIBE_CONSOLE_KEY` is exactly 32 random bytes in URL-safe base64; HKDF-SHA256 derives the credential key (`info=swarmscribe-console/leader-credentials/v1`) and the CSRF key (`…/csrf/v1`).
- **CSRF token = HMAC-SHA256(CSRF key, session id)**, URL-safe base64, sent by the SPA in `X-CSRF-Token`, read from `GET /api/session`. Not stored. Exactly one header value is accepted. If an `Origin` header is present it must equal `public_url` (defence in depth; a missing `Origin` is allowed).
- **Cookies.** Session: `__Host-swarmscribe-session`, HttpOnly, Secure, SameSite=Strict, Path=/, Max-Age 28 800. Pending sign-in: `__Host-swarmscribe-login`, HttpOnly, Secure, **SameSite=Lax** (a Strict cookie is not sent on the identity provider's cross-site redirect back), Max-Age 600, deleted by the callback.
- **The callback answers 200 with a tiny HTML page that meta-refreshes to the return URL**, not a 302: the browser's next navigation is then same-site, so the Strict session cookie is sent on it. No inline script (CSP).
- **Sessions are stored by SHA-256 of their id**; the cookie holds the only copy of the id. `last_seen_at` is written at most once a minute per session (idle accuracy ±60 s, no write per request).
- **Principals are computed at sign-in and kept in the session** (Entra: group object ids from the token or Graph on overage; Google: groups through Cloud Identity when a service account is configured, plus `email:` and `domain:` under the leader's Workspace rules). A change of group membership applies at the next sign-in (at most 8 hours; owner ruling above). Grants themselves are read on every request, so revoking a grant applies at once. Entra emails are not principals (the leader does not map them either).
- **Only people with access may sign in:** a person whose principals match no grant (any scope) and no console administrator entry gets a 403 page, no session, and an audit row `sign_in.refused`.
- **The return URL** is captured at `/auth/login`, kept server-side with the state, and must match `/[A-Za-z0-9\-._~/?=&%#:+,@]*`, not start with `//`, and be at most 512 characters; anything else becomes `/`.
- **Authorization and token endpoints are fixed**, as the leader's CLI config does: Entra `https://login.microsoftonline.com/<tenant>/oauth2/v2.0/{authorize,token}`, Google `https://accounts.google.com/o/oauth2/v2/auth` and `https://oauth2.googleapis.com/token`. Scope `openid email profile` (no `offline_access`, so no refresh token is requested). The console is a confidential web client: each configured provider needs its client secret.
- **`public_url`** is the console's https origin (scheme, host, port; no path). `http://localhost`/`127.0.0.1`/`[::1]` is allowed for development only.
- **Principal kinds** are `entra_group` (canonical lowercase GUID), `google_group` and `email` (lowercase ASCII address, one `@`), `domain` (lowercase ASCII DNS name, `@` prefix dropped). A grant is unique per `(scope, principal_kind, principal)`; changing a role is remove + add.
- **Scopes** are stored canonically: `all`, `leader:<lowercase name>`, `label:<key>=<value>`. Label keys are `[a-z0-9][a-z0-9._-]{0,62}` and cannot contain `=`; values are 1–255 visible ASCII characters (no spaces) and may contain `=`; value matching is case-sensitive. A grant may name a leader that is not registered yet.
- **Leader names** use the leader's pattern `^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$`, unique ignoring case, and cannot be renamed (the sealed credential is bound to the name; the audit log names leaders by name).
- **Leader URLs and SSRF:** https only; no user info, query or fragment; normalised to `https://<host>[:port][/path]` with no trailing slash. Refused: loopback, link-local (including `169.254.169.254`), unspecified, multicast and reserved IP literals (IPv4-mapped IPv6 checked as IPv4); `localhost`, `*.localhost`, `metadata`, `metadata.google.internal`; hostnames whose last label does not start with a letter (blocks `2130706433`, `0x7f000001` and other numeric spellings); `.`/`..` path segments. **Private addresses (10/8, 172.16/12, 192.168/16, fc00::/7, 100.64/10) are allowed** (owner ruling above), because leaders are usually internal and only console administrators register them. DNS rebinding to a blocked address is not prevented in code; C4 adds an egress network policy blocking link-local.
- **Labels:** at most 32 per leader, stored sorted.
- **Console admin API** (all CSRF-protected, console administrators only): `GET/POST /api/admin/leaders`, `PATCH/DELETE /api/admin/leaders/{name}`, `PUT /api/admin/leaders/{name}/credential` (rotation in place: clears the revoked mark and the poller's failure count — C1 spec amendment 3), `GET/POST /api/admin/grants`, `DELETE /api/admin/grants/{id}`, `GET/POST /api/admin/console-admins`, `DELETE /api/admin/console-admins/{id}`. Removing the last console administrator is `409 last_admin`.
- **Bootstrap:** `swarmscribe-console admins add <kind> <principal>` and `admins list` write to the database directly (actor `swarmscribe-console cli`), for the first administrator.
- **Audit (console side):** every registry, grant and administrator change, sign-in, sign-in refusal and sign-out; every refused state-changing request by a signed-in person (`request.refused`, outcome = error code, target = `"<METHOD> <route template>"`, never request values). Reads are not audited in the console (the leader audits proxied reads; C2b; owner ruling above).
- **Error bodies** are the leader's shape, `swarmscribe_protocol.ErrorBody` `{code, message}`. Validation errors never echo input.
- **Security headers on every response:** CSP `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'`, plus `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`, `Strict-Transport-Security: max-age=31536000; includeSubDomains`, `Cross-Origin-Opener-Policy: same-origin`; `Cache-Control: no-store` on `/api/` and `/auth/`. C3 must build without inline scripts or styles.
- **No access log** (`uvicorn` `access_log=False`): the callback URL carries an authorization code. `httpx` and `httpcore` loggers are set to WARNING.

## File Structure

```
pyproject.toml                                               MOD  workspace member, dev dep, ruff exclude (Task 1)
uv.lock                                                      MOD  (Task 1)
packages/console/pyproject.toml                              NEW  (Task 1)
packages/console/src/swarmscribe_console/
  __init__.py                                                NEW  (Task 1)
  config.py                                                  NEW  Settings, decode_key (Task 1)
  crypto.py                                                  NEW  ConsoleKeys: seal/open, CSRF (Task 1)
  errors.py                                                  NEW  ConsoleError family (Task 1)
  db/__init__.py                                             NEW  (Task 2)
  db/models.py                                               NEW  all tables (Task 2)
  db/migrate.py                                              NEW  (Task 2)
  db/migrations/env.py, script.py.mako                       NEW  (Task 2)
  db/migrations/versions/0001_initial_schema.py              NEW  (Task 2)
  audit.py                                                   NEW  record, record_apart (Task 2)
  main.py                                                    NEW  migrate (Task 2); serve (Task 4); admins (Task 7)
  grants.py                                                  NEW  scopes, principals, matching (Task 3); grant/admin store (Task 7)
  sessions.py                                                NEW  sessions, cookies (Task 4)
  app.py                                                     NEW  (Task 4); sign-in wiring (Task 5); admin router (Task 6)
  api/__init__.py                                            NEW  (Task 4)
  api/deps.py                                                NEW  signed_in, CSRF check (Task 4)
  api/errors.py                                              NEW  handlers, refusal audit (Task 4)
  api/security.py                                            NEW  security headers (Task 4)
  api/session.py                                             NEW  GET /api/session, logout (Task 4)
  oidc.py                                                    NEW  web providers, PKCE, state, exchange (Task 5)
  principals.py                                              NEW  (Task 5)
  api/auth.py                                                NEW  /auth/providers, login, callback (Task 5)
  leaders.py                                                 NEW  registry service, URL rules (Task 6)
  api/models.py                                              NEW  bodies (Task 6, Task 7)
  api/admin.py                                               NEW  leaders (Task 6); grants, admins (Task 7)
packages/console/tests/
  console_testkit.py                                         NEW  shared constants and helpers (Tasks 2, 4, 5, 6)
  conftest.py                                                NEW  db (Task 2); app, factory (Task 4); fake IdP (Task 5)
  test_config.py, test_crypto.py                             NEW  (Task 1)
  test_migrations.py                                         NEW  (Task 2)
  test_grants.py                                             NEW  (Task 3)
  test_sessions.py                                           NEW  (Task 4)
  test_sign_in.py                                            NEW  (Task 5)
  test_registry.py                                           NEW  (Task 6)
  test_grants_api.py                                         NEW  (Task 7)
README.md                                                    MOD  "Run the fleet console" (Task 7)
```

---

### Task 1: Package, settings and crypto

**Files:**
- Modify: `pyproject.toml` (root)
- Create: `packages/console/pyproject.toml`, `packages/console/src/swarmscribe_console/__init__.py`, `config.py`, `crypto.py`, `errors.py`
- Test: `packages/console/tests/test_config.py`, `packages/console/tests/test_crypto.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `config.Settings` (pydantic-settings, prefix `SWARMSCRIBE_CONSOLE_`) with fields `database_url: SecretStr`, `public_url: str` (normalised origin, no trailing slash), `key: SecretStr`, `entra_tenant_id`, `entra_client_id: str | None`, `entra_client_secret: SecretStr | None`, `google_client_id: str | None`, `google_client_secret: SecretStr | None`, `google_hosted_domain: str | None`, `google_service_account: SecretStr | None`, `session_lifetime_seconds: int = 28800`, `session_idle_seconds: int = 3600`, `login_attempt_seconds: int = 600`; methods `key_bytes() -> bytes`, `google_service_account_key() -> dict[str, str] | None`.
  - `config.decode_key(value: str) -> bytes` (raises `ValueError`).
  - `crypto.ConsoleKeys(master: bytes)` with `seal_credential(leader_name: str, credential: str) -> bytes`, `open_credential(leader_name: str, sealed: bytes) -> str` (raises `crypto.CredentialUnreadable`), `csrf_token(session_id: str) -> str`, `csrf_matches(session_id: str, presented: str | None) -> bool`.
  - `errors.ConsoleError(message, *, code=None)` with `status`, `code`, `retry_after`, `message`; subclasses `Unauthenticated(message, *, clear_cookie=False)` (401 `unauthenticated`), `Forbidden` (403 `forbidden`), `CsrfRejected` (403 `csrf_failed`), `NotFound` (404 `not_found`), `Conflict` (409 `conflict`), `Invalid` (422 `invalid_request`), `Unavailable` (503 `unavailable`, `retry_after=10`).

- [ ] **Step 1: Register the package in the workspace**

In the root `pyproject.toml`, add to `[tool.uv.sources]`:

```toml
swarmscribe-console = { workspace = true }
```

add `"swarmscribe-console",` to the `dev` list in `[dependency-groups]` (after `"swarmscribe-leader",`), and replace the ruff exclude with:

```toml
extend-exclude = [
    "packages/leader/src/swarmscribe_leader/db/migrations/versions",
    "packages/console/src/swarmscribe_console/db/migrations/versions",
]
```

Create `packages/console/pyproject.toml`:

```toml
[project]
name = "swarmscribe-console"
version = "0.1.0"
description = "SwarmScribe fleet console: sign-in, leader registry, poller and action proxy"
requires-python = ">=3.11"
dependencies = [
    "swarmscribe-protocol",
    "swarmscribe-leader",
    "fastapi>=0.115,<1",
    "uvicorn[standard]>=0.30,<1",
    "sqlalchemy[asyncio]>=2.0.30,<3",
    "asyncpg>=0.29,<1",
    "alembic>=1.13,<2",
    "pydantic-settings>=2.4,<3",
    "pyjwt[crypto]>=2.10,<3",
    "httpx>=0.27,<1",
    # AES-GCM and HKDF for leader credentials and CSRF tokens (already locked via pyjwt[crypto]).
    "cryptography>=42",
]

[project.scripts]
swarmscribe-console = "swarmscribe_console.main:run"

[tool.uv.sources]
swarmscribe-protocol = { workspace = true }
swarmscribe-leader = { workspace = true }

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/swarmscribe_console"]
```

Create `packages/console/src/swarmscribe_console/__init__.py`:

```python
"""SwarmScribe fleet console: one web console for many leader deployments."""
```

Run: `python -m uv sync`
Expected: resolves and installs `swarmscribe-console` in editable mode; `uv.lock` gains the package.

- [ ] **Step 2: Write the failing settings tests**

Create `packages/console/tests/test_config.py`:

```python
import base64

import pytest
from pydantic import ValidationError
from swarmscribe_console.config import Settings, decode_key

KEY = base64.urlsafe_b64encode(bytes(range(32))).rstrip(b"=").decode()
TENANT = "0f0e0d0c-0b0a-4908-8706-050403020100"


def settings(**overrides) -> Settings:
    values = {
        "database_url": "postgresql://u:p@127.0.0.1:1/none",
        "public_url": "https://console.example.org",
        "key": KEY,
        "entra_tenant_id": TENANT,
        "entra_client_id": "entra-client",
        "entra_client_secret": "entra-secret-value",
    }
    values.update(overrides)
    return Settings(**values)


def test_the_key_is_32_bytes_of_url_safe_base64():
    assert decode_key(KEY) == bytes(range(32))
    assert decode_key(KEY + "=") == bytes(range(32))
    assert decode_key(f"  {KEY}\n") == bytes(range(32))


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "short",
        base64.urlsafe_b64encode(bytes(31)).decode(),
        base64.urlsafe_b64encode(bytes(33)).decode(),
        "!" * 43,
        KEY[:-1] + "*",
    ],
    ids=["empty", "short", "31-bytes", "33-bytes", "not-base64", "one-bad-character"],
)
def test_any_other_key_is_refused(bad):
    with pytest.raises(ValueError, match="32 random bytes"):
        decode_key(bad)


def test_defaults_are_the_spec_values():
    s = settings()
    assert (s.session_lifetime_seconds, s.session_idle_seconds) == (8 * 3600, 3600)
    assert s.login_attempt_seconds == 600
    assert s.key_bytes() == bytes(range(32))


@pytest.mark.parametrize(
    ("given", "kept"),
    [
        ("https://console.example.org", "https://console.example.org"),
        ("https://Console.Example.org/", "https://console.example.org"),
        ("https://console.example.org:8443", "https://console.example.org:8443"),
        ("http://localhost:8000", "http://localhost:8000"),
        ("http://127.0.0.1:8000", "http://127.0.0.1:8000"),
    ],
)
def test_the_public_url_is_an_https_origin(given, kept):
    assert settings(public_url=given).public_url == kept


@pytest.mark.parametrize(
    "bad",
    [
        "http://console.example.org",
        "console.example.org",
        "https://console.example.org/console",
        "https://console.example.org/?x=1",
        "https://user:pw@console.example.org",
        "ftp://console.example.org",
    ],
)
def test_other_public_urls_are_refused(bad):
    with pytest.raises(ValidationError):
        settings(public_url=bad)


def test_a_sign_in_provider_is_required():
    with pytest.raises(ValidationError, match="Entra ID or Google"):
        settings(entra_tenant_id=None, entra_client_id=None, entra_client_secret=None)


@pytest.mark.parametrize("missing", ["entra_tenant_id", "entra_client_id", "entra_client_secret"])
def test_entra_needs_tenant_client_and_secret_together(missing):
    with pytest.raises(ValidationError, match="set together"):
        settings(**{missing: None})


def test_google_needs_its_client_secret():
    with pytest.raises(ValidationError, match="set together"):
        settings(google_client_id="google-client")
    s = settings(google_client_id="google-client", google_client_secret="google-secret")
    assert s.google_client_id == "google-client"


def test_google_options_need_google_sign_in():
    with pytest.raises(ValidationError, match="needs google_client_id"):
        settings(google_hosted_domain="example.org")


def test_the_tenant_is_a_guid():
    with pytest.raises(ValidationError, match="GUID"):
        settings(entra_tenant_id="example.onmicrosoft.com")


def test_idle_timeout_cannot_exceed_the_lifetime():
    with pytest.raises(ValidationError, match="session_idle_seconds"):
        settings(session_idle_seconds=9 * 3600)


def test_secrets_are_not_echoed():
    with pytest.raises(ValidationError) as raised:
        settings(key="not-a-key-but-a-secret-value")
    assert "not-a-key-but-a-secret-value" not in str(raised.value)
    s = settings()
    assert "entra-secret-value" not in repr(s)
    assert KEY not in repr(s)
```

- [ ] **Step 3: Write the failing crypto tests**

Create `packages/console/tests/test_crypto.py`:

```python
import os

import pytest
from swarmscribe_console.crypto import ConsoleKeys, CredentialUnreadable

MASTER = bytes(range(32))
CREDENTIAL = "q" * 21 + "Z_-" + "x" * 19  # 43 URL-safe characters, like C1's credentials


@pytest.fixture
def keys():
    return ConsoleKeys(MASTER)


def test_a_sealed_credential_opens_to_the_same_value(keys):
    sealed = keys.seal_credential("eu-1", CREDENTIAL)
    assert keys.open_credential("eu-1", sealed) == CREDENTIAL


def test_the_sealed_form_does_not_contain_the_credential(keys):
    sealed = keys.seal_credential("eu-1", CREDENTIAL)
    assert CREDENTIAL.encode() not in sealed
    assert sealed[:1] == b"\x01"
    assert len(sealed) == 1 + 12 + len(CREDENTIAL) + 16


def test_every_seal_uses_a_fresh_nonce(keys):
    assert keys.seal_credential("eu-1", CREDENTIAL) != keys.seal_credential("eu-1", CREDENTIAL)


def test_the_leader_name_is_bound_to_the_ciphertext_ignoring_case(keys):
    sealed = keys.seal_credential("EU-1", CREDENTIAL)
    assert keys.open_credential("eu-1", sealed) == CREDENTIAL
    with pytest.raises(CredentialUnreadable):
        keys.open_credential("us-1", sealed)


@pytest.mark.parametrize(
    "spoil",
    [
        lambda s: s[:-1] + bytes([s[-1] ^ 1]),
        lambda s: s[:20] + bytes([s[20] ^ 1]) + s[21:],
        lambda s: s[:5],
        lambda s: b"\x02" + s[1:],
        lambda s: b"",
    ],
    ids=["tag-flipped", "ciphertext-flipped", "truncated", "unknown-format", "empty"],
)
def test_a_tampered_credential_does_not_open(keys, spoil):
    sealed = keys.seal_credential("eu-1", CREDENTIAL)
    with pytest.raises(CredentialUnreadable):
        keys.open_credential("eu-1", spoil(sealed))


def test_another_key_cannot_open_it(keys):
    sealed = keys.seal_credential("eu-1", CREDENTIAL)
    with pytest.raises(CredentialUnreadable):
        ConsoleKeys(os.urandom(32)).open_credential("eu-1", sealed)


def test_the_master_key_must_be_32_bytes():
    with pytest.raises(ValueError):
        ConsoleKeys(bytes(31))


def test_the_csrf_token_is_fixed_per_session_and_differs_between_sessions(keys):
    first = keys.csrf_token("session-one")
    assert first == keys.csrf_token("session-one")
    assert first != keys.csrf_token("session-two")
    assert first != ConsoleKeys(os.urandom(32)).csrf_token("session-one")
    assert len(first) == 43


@pytest.mark.parametrize("presented", [None, "", "x" * 43, "é" * 43])
def test_only_the_sessions_own_token_matches(keys, presented):
    assert keys.csrf_matches("session-one", keys.csrf_token("session-one"))
    assert not keys.csrf_matches("session-one", presented)
    assert not keys.csrf_matches("session-one", keys.csrf_token("session-two"))
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `python -m uv run pytest packages/console/tests/test_config.py packages/console/tests/test_crypto.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'swarmscribe_console.config'`.

- [ ] **Step 5: Write the settings**

Create `packages/console/src/swarmscribe_console/config.py`:

```python
"""Fleet console configuration, read from SWARMSCRIBE_CONSOLE_* environment variables.

Secrets (the database URL, the console key, the client secrets, the service-account key) are
SecretStr: never shown in repr, logs or validation errors."""

import base64
import binascii
import json
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

KEY_BYTES = 32
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
_BAD_KEY = (
    "key must be 32 random bytes in URL-safe base64; make one with "
    'python -c "import secrets; print(secrets.token_urlsafe(32))"'
)
_BAD_SERVICE_ACCOUNT = (
    "google_service_account must be a service-account JSON key or the path of a file holding one"
)


def decode_key(value: str) -> bytes:
    """SWARMSCRIBE_CONSOLE_KEY: exactly 32 bytes, URL-safe base64, padding optional."""
    text = value.strip()
    try:
        raw = base64.b64decode(
            text.replace("-", "+").replace("_", "/") + "=" * (-len(text) % 4), validate=True
        )
    except (binascii.Error, ValueError):
        raise ValueError(_BAD_KEY) from None
    if len(raw) != KEY_BYTES:
        raise ValueError(_BAD_KEY)
    return raw


class Settings(BaseSettings):
    """Console configuration. The console is a confidential OIDC web client of each
    configured provider, so each needs its client secret."""

    # hide_input_in_errors: a model-level ValidationError would otherwise echo the values.
    model_config = SettingsConfigDict(
        env_prefix="SWARMSCRIBE_CONSOLE_", extra="ignore", hide_input_in_errors=True
    )

    database_url: SecretStr
    public_url: str
    key: SecretStr

    entra_tenant_id: str | None = None
    entra_client_id: str | None = None
    entra_client_secret: SecretStr | None = None
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    google_hosted_domain: str | None = None
    google_service_account: SecretStr | None = None  # JSON key, or the path of a file with it

    session_lifetime_seconds: int = Field(default=8 * 3600, gt=0)
    session_idle_seconds: int = Field(default=3600, gt=0)
    login_attempt_seconds: int = Field(default=600, gt=0)

    @field_validator("public_url")
    @classmethod
    def _console_origin(cls, value: str) -> str:
        parts = urlsplit(value.strip())
        local = parts.scheme == "http" and parts.hostname in _LOCAL_HOSTS
        if (parts.scheme != "https" and not local) or not parts.hostname:
            raise ValueError(
                "public_url must be the console's https:// origin, "
                "e.g. https://console.example.org"
            )
        if (
            parts.path not in ("", "/")
            or parts.query
            or parts.fragment
            or parts.username is not None
            or parts.password is not None
        ):
            raise ValueError("public_url is an origin only: scheme, host and port, no path")
        return f"{parts.scheme}://{parts.netloc.lower()}"

    @field_validator("key")
    @classmethod
    def _key_is_32_bytes(cls, value: SecretStr) -> SecretStr:
        decode_key(value.get_secret_value())
        return value

    @field_validator(
        "entra_client_secret", "google_client_secret", "google_service_account", mode="before"
    )
    @classmethod
    def _blank_secret_is_unset(cls, value: Any) -> Any:
        # Compose/Kubernetes pass an unset variable as an empty string.
        if isinstance(value, str) and not value.strip():
            return None
        if isinstance(value, SecretStr) and not value.get_secret_value().strip():
            return None
        return value

    @field_validator("entra_tenant_id")
    @classmethod
    def _tenant_is_an_id(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        try:
            return str(uuid.UUID(value.strip()))
        except ValueError as exc:
            raise ValueError(
                "entra_tenant_id must be the tenant's ID (a GUID), not its domain name"
            ) from exc

    @field_validator("entra_client_id", "google_client_id", "google_hosted_domain")
    @classmethod
    def _blank_is_unset(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return value.strip()

    @field_validator("google_hosted_domain")
    @classmethod
    def _domain_lowercase(cls, value: str | None) -> str | None:
        return value.removeprefix("@").lower() if value else value

    @model_validator(mode="after")
    def _sign_in_is_complete(self) -> "Settings":
        entra = (self.entra_tenant_id, self.entra_client_id, self.entra_client_secret)
        if any(v is not None for v in entra) and not all(v is not None for v in entra):
            raise ValueError(
                "entra_tenant_id, entra_client_id and entra_client_secret are set together "
                "(the console is a confidential web client)"
            )
        if bool(self.google_client_id) != (self.google_client_secret is not None):
            raise ValueError("google_client_id and google_client_secret are set together")
        if not self.google_client_id:
            for name in ("google_hosted_domain", "google_service_account"):
                if getattr(self, name) is not None:
                    raise ValueError(f"{name} needs google_client_id")
        if not self.entra_client_id and not self.google_client_id:
            raise ValueError("configure Entra ID or Google sign-in (or both)")
        if self.session_idle_seconds > self.session_lifetime_seconds:
            raise ValueError(
                "session_idle_seconds cannot be longer than session_lifetime_seconds"
            )
        if self.google_service_account is not None:
            self.google_service_account_key()
        return self

    def key_bytes(self) -> bytes:
        return decode_key(self.key.get_secret_value())

    def google_service_account_key(self) -> dict[str, str] | None:
        """The service account's JSON key: the setting holds the JSON itself, or the path of
        a mounted file containing it. Error messages never include the key."""
        if self.google_service_account is None:
            return None
        raw = self.google_service_account.get_secret_value().strip()
        try:
            if not raw.startswith("{"):
                raw = Path(raw).read_text(encoding="utf-8")
            key = json.loads(raw)
        except (OSError, ValueError):
            raise ValueError(_BAD_SERVICE_ACCOUNT) from None
        if not isinstance(key, dict) or not all(
            isinstance(key.get(field), str) and key.get(field)
            for field in ("client_email", "private_key")
        ):
            raise ValueError(_BAD_SERVICE_ACCOUNT)
        key.setdefault("token_uri", GOOGLE_TOKEN_URI)
        return key
```

- [ ] **Step 6: Write the crypto and errors modules**

Create `packages/console/src/swarmscribe_console/crypto.py`:

```python
"""What the console keeps secret with its own key (fleet console spec 5.1 and 5.2).

SWARMSCRIBE_CONSOLE_KEY is 32 random bytes. HKDF-SHA256 derives two independent keys from it:
one seals leader credentials with AES-GCM, one makes each session's CSRF token (an HMAC of
the session id, so it is never stored). A sealed credential is `0x01 ‖ nonce ‖ ciphertext`;
its associated data is the leader's name, so a credential copied onto another leader's row
does not open."""

import base64
import hashlib
import hmac
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

FORMAT_V1 = b"\x01"
NONCE_BYTES = 12
TAG_BYTES = 16


class CredentialUnreadable(Exception):
    """A stored credential this key cannot open: another key, another leader's row, or a
    tampered value. The message never contains the credential."""


def _derive(master: bytes, purpose: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"swarmscribe-console/" + purpose,
    ).derive(master)


def _associated_data(leader_name: str) -> bytes:
    return b"swarmscribe-console/leader:" + leader_name.lower().encode("ascii")


class ConsoleKeys:
    def __init__(self, master: bytes):
        if len(master) != 32:
            raise ValueError("the console key must be exactly 32 bytes")
        self._aead = AESGCM(_derive(master, b"leader-credentials/v1"))
        self._csrf = _derive(master, b"csrf/v1")

    def seal_credential(self, leader_name: str, credential: str) -> bytes:
        nonce = os.urandom(NONCE_BYTES)
        sealed = self._aead.encrypt(
            nonce, credential.encode("ascii"), _associated_data(leader_name)
        )
        return FORMAT_V1 + nonce + sealed

    def open_credential(self, leader_name: str, sealed: bytes) -> str:
        if len(sealed) <= 1 + NONCE_BYTES + TAG_BYTES or sealed[:1] != FORMAT_V1:
            raise CredentialUnreadable("the stored leader credential is not in a known format")
        nonce, body = sealed[1 : 1 + NONCE_BYTES], sealed[1 + NONCE_BYTES :]
        try:
            plain = self._aead.decrypt(nonce, body, _associated_data(leader_name))
        except InvalidTag:
            raise CredentialUnreadable(
                "the stored leader credential cannot be opened with this console key"
            ) from None
        return plain.decode("ascii")

    def csrf_token(self, session_id: str) -> str:
        mac = hmac.new(self._csrf, session_id.encode("ascii"), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(mac).rstrip(b"=").decode("ascii")

    def csrf_matches(self, session_id: str, presented: str | None) -> bool:
        if not presented:
            return False
        expected = self.csrf_token(session_id).encode("ascii")
        return hmac.compare_digest(expected, presented.encode("utf-8", "replace"))
```

Create `packages/console/src/swarmscribe_console/errors.py`:

```python
"""Expected failures with an HTTP status and a stable code. Messages are fixed text or name
only what the caller sent in a checked form; they never carry secrets."""


class ConsoleError(Exception):
    status = 400
    code = "bad_request"
    retry_after: int | None = None

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code


class Unauthenticated(ConsoleError):
    """No valid session. `clear_cookie` asks the handler to delete the browser's cookie."""

    status = 401
    code = "unauthenticated"

    def __init__(self, message: str, *, clear_cookie: bool = False):
        super().__init__(message)
        self.clear_cookie = clear_cookie


class Forbidden(ConsoleError):
    status = 403
    code = "forbidden"


class CsrfRejected(Forbidden):
    code = "csrf_failed"


class NotFound(ConsoleError):
    status = 404
    code = "not_found"


class Conflict(ConsoleError):
    status = 409
    code = "conflict"


class Invalid(ConsoleError):
    status = 422
    code = "invalid_request"


class Unavailable(ConsoleError):
    """Something the console depends on (its database, an identity provider) is down."""

    status = 503
    code = "unavailable"
    retry_after = 10
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python -m uv run pytest packages/console/tests/test_config.py packages/console/tests/test_crypto.py -v`
Expected: PASS.

Run: `python -m uv run ruff check packages/console`
Expected: no findings.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock packages/console/pyproject.toml packages/console/src/swarmscribe_console/__init__.py packages/console/src/swarmscribe_console/config.py packages/console/src/swarmscribe_console/crypto.py packages/console/src/swarmscribe_console/errors.py packages/console/tests/test_config.py packages/console/tests/test_crypto.py
git commit -m "Console: package, settings and AES-GCM credential sealing

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Console database — models, migration 0001, audit, `migrate`

**Files:**
- Create: `packages/console/src/swarmscribe_console/db/__init__.py`, `db/models.py`, `db/migrate.py`, `db/migrations/env.py`, `db/migrations/script.py.mako`, `db/migrations/versions/0001_initial_schema.py`, `audit.py`, `main.py`
- Test: `packages/console/tests/console_testkit.py`, `packages/console/tests/conftest.py`, `packages/console/tests/test_migrations.py`

**Interfaces:**
- Consumes: `Settings` (Task 1); `swarmscribe_leader.db.session.to_async_url`, `make_engine`, `make_sessionmaker`.
- Produces:
  - `db.models.Base` and the tables: `Leader` (`leaders`: `id: int` bigserial, `name`, `base_url`, `labels: dict`, `enabled: bool`, `credential: bytes`, `credential_updated_at`, `credential_updated_by`, `credential_revoked_at | None`, `added_by`, `created_at`, `last_polled_at | None`, `last_success_at | None`, `consecutive_failures: int`, `last_error: str | None`); `RoleGrant` (`role_grants`: `id: UUID`, `role`, `scope`, `principal_kind`, `principal`, `created_by`, `created_at`); `ConsoleAdmin` (`console_admins`: `id: UUID`, `principal_kind`, `principal`, `created_by`, `created_at`); `Snapshot` (`snapshots`: `id: int`, `leader_id`, `taken_at`, `reachable: bool`, `outcome: str`, `status: dict | None`); `AuditEntry` (`audit_log`: `id: int`, `at`, `actor`, `action`, `leader | None`, `target | None`, `outcome`, `detail: dict`); `ConsoleSession` (`sessions`: `id_hash` PK, `provider`, `issuer`, `subject`, `email | None`, `principals: list`, `created_at`, `last_seen_at`, `expires_at`); `LoginAttempt` (`login_attempts`: `state_hash` PK, `browser_hash`, `provider`, `nonce`, `code_verifier`, `return_to`, `expires_at`).
  - `db.migrate.alembic_config(url=None)`, `upgrade(url)`, `head_revision() -> str`, `is_known_revision(rev) -> bool`, `async current_revision(engine) -> str | None`.
  - `audit.record(session, *, actor, action, leader=None, target=None, outcome="ok", detail=None) -> None`; `async audit.record_apart(sessionmaker, **same) -> None` (own session; never raises).
  - `main.main(argv) -> int` with `migrate`; `main.LOGGING`; `main._load_settings()`.
  - Test fixtures `admin_database_url`, `database_url`, `migrated_database_url`, `engine`, `sessionmaker`, `keys`; `console_testkit` constants `TEST_KEY`, `MASTER_KEY` and helpers `with_database(url, name)`, `recreate(admin_url, name, *, drop_only=False)`, `console_env(monkeypatch, url)`.

- [ ] **Step 1: Write the test kit and fixtures**

Create `packages/console/tests/console_testkit.py`:

```python
"""Constants and helpers shared by the console's test files. conftest.py puts this folder on
sys.path; test files import from here, never from conftest."""

import base64
from urllib.parse import urlsplit, urlunsplit

import asyncpg

MASTER_KEY = bytes(range(32))
TEST_KEY = base64.urlsafe_b64encode(MASTER_KEY).rstrip(b"=").decode("ascii")


def with_database(url: str, name: str) -> str:
    return urlunsplit(urlsplit(url)._replace(path="/" + name))


async def recreate(admin_url: str, name: str, *, drop_only: bool = False) -> None:
    conn = await asyncpg.connect(admin_url)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        if not drop_only:
            await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()


def console_env(monkeypatch, database_url: str) -> None:
    """A complete SWARMSCRIBE_CONSOLE_* environment for command-line tests."""
    monkeypatch.setenv("SWARMSCRIBE_CONSOLE_DATABASE_URL", database_url)
    monkeypatch.setenv("SWARMSCRIBE_CONSOLE_PUBLIC_URL", "https://console.test")
    monkeypatch.setenv("SWARMSCRIBE_CONSOLE_KEY", TEST_KEY)
    monkeypatch.setenv("SWARMSCRIBE_CONSOLE_GOOGLE_CLIENT_ID", "google-client")
    monkeypatch.setenv("SWARMSCRIBE_CONSOLE_GOOGLE_CLIENT_SECRET", "google-secret")
```

Create `packages/console/tests/conftest.py`:

```python
import asyncio
import os
import sys
from pathlib import Path

# console_testkit (shared constants and helpers) sits beside this file.
sys.path.insert(0, str(Path(__file__).parent))

import pytest  # noqa: E402
from console_testkit import MASTER_KEY, recreate, with_database  # noqa: E402
from sqlalchemy import text  # noqa: E402
from swarmscribe_console.crypto import ConsoleKeys  # noqa: E402
from swarmscribe_console.db.migrate import upgrade  # noqa: E402
from swarmscribe_console.db.models import Base  # noqa: E402
from swarmscribe_leader.db.session import make_engine, make_sessionmaker  # noqa: E402

TEST_DATABASE = "swarmscribe_console_test"
REPO_ROOT = Path(__file__).resolve().parents[3]
KEEP_TABLES = {"alembic_version"}


def _admin_url() -> str:
    url = os.environ.get("SWARMSCRIBE_TEST_DATABASE_URL")
    if url:
        return url
    import pgserver

    server = pgserver.get_server(str(REPO_ROOT / ".pgdata"), cleanup_mode="stop")
    return server.get_uri()


@pytest.fixture(scope="session")
def admin_database_url() -> str:
    return _admin_url()


@pytest.fixture(scope="session")
def database_url(admin_database_url) -> str:
    asyncio.run(recreate(admin_database_url, TEST_DATABASE))
    return with_database(admin_database_url, TEST_DATABASE)


@pytest.fixture(scope="session")
def migrated_database_url(database_url) -> str:
    upgrade(database_url)
    return database_url


@pytest.fixture
async def engine(migrated_database_url):
    engine = make_engine(migrated_database_url)
    tables = [t.name for t in reversed(Base.metadata.sorted_tables) if t.name not in KEEP_TABLES]
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    yield engine
    await engine.dispose()


@pytest.fixture
def sessionmaker(engine):
    return make_sessionmaker(engine)


@pytest.fixture
def keys():
    return ConsoleKeys(MASTER_KEY)
```

- [ ] **Step 2: Write the failing migration and audit tests**

Create `packages/console/tests/test_migrations.py`:

```python
import asyncio

import asyncpg
import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from console_testkit import console_env, recreate, with_database
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from swarmscribe_console import audit
from swarmscribe_console.db.migrate import alembic_config, current_revision, head_revision
from swarmscribe_console.db.models import AuditEntry, Base, Snapshot
from swarmscribe_console.main import main

LEADER_SQL = (
    "insert into leaders (name, base_url, credential, credential_updated_by, added_by)"
    " values (:name, :url, '\\x01'::bytea, 'test', 'test') returning id"
)


async def test_migrations_produce_exactly_the_models(engine):
    async with engine.connect() as conn:
        diff = await conn.run_sync(
            lambda sync_conn: compare_metadata(MigrationContext.configure(sync_conn), Base.metadata)
        )
    assert diff == []


async def test_database_is_at_the_head_revision(engine):
    assert head_revision() == "0001"
    assert await current_revision(engine) == "0001"


async def test_leader_names_are_unique_ignoring_case(sessionmaker):
    async with sessionmaker() as session:
        await session.execute(text(LEADER_SQL), {"name": "EU-1", "url": "https://a.example"})
        await session.commit()
        with pytest.raises(IntegrityError):
            await session.execute(text(LEADER_SQL), {"name": "eu-1", "url": "https://b.example"})
        await session.rollback()


@pytest.mark.parametrize(
    ("name", "url"),
    [("eu-1", "http://leader.example"), ("../eu", "https://leader.example"), ("", "https://x")],
    ids=["http-url", "path-name", "empty-name"],
)
async def test_the_database_refuses_bad_leader_rows(sessionmaker, name, url):
    async with sessionmaker() as session:
        with pytest.raises(IntegrityError):
            await session.execute(text(LEADER_SQL), {"name": name, "url": url})
        await session.rollback()


@pytest.mark.parametrize(
    "sql",
    [
        "insert into role_grants (id, role, scope, principal_kind, principal, created_by)"
        " values (gen_random_uuid(), 'root', 'all', 'email', 'a@b.org', 't')",
        "insert into role_grants (id, role, scope, principal_kind, principal, created_by)"
        " values (gen_random_uuid(), 'viewer', 'everything', 'email', 'a@b.org', 't')",
        "insert into role_grants (id, role, scope, principal_kind, principal, created_by)"
        " values (gen_random_uuid(), 'viewer', 'all', 'user', 'a@b.org', 't')",
        "insert into console_admins (id, principal_kind, principal, created_by)"
        " values (gen_random_uuid(), 'group', 'x', 't')",
    ],
    ids=["unknown-role", "unknown-scope", "unknown-principal-kind", "unknown-admin-kind"],
)
async def test_the_database_refuses_bad_grants(sessionmaker, sql):
    async with sessionmaker() as session:
        with pytest.raises(IntegrityError):
            await session.execute(text(sql))
        await session.rollback()


async def test_removing_a_leader_removes_its_snapshots(sessionmaker):
    async with sessionmaker() as session:
        leader_id = await session.scalar(
            text(LEADER_SQL), {"name": "eu-1", "url": "https://leader.example"}
        )
        await session.execute(
            text(
                "insert into snapshots (leader_id, taken_at, reachable, outcome)"
                " values (:id, now(), true, 'ok')"
            ),
            {"id": leader_id},
        )
        await session.execute(text("delete from leaders where id = :id"), {"id": leader_id})
        await session.commit()
        assert await session.scalar(select(func.count()).select_from(Snapshot)) == 0


async def test_an_audit_entry_commits_with_its_change(sessionmaker):
    async with sessionmaker() as session:
        audit.record(session, actor="a (b c)", action="leader.add", leader="eu-1", target="x")
        await session.commit()
        (entry,) = (await session.scalars(select(AuditEntry))).all()
    assert (entry.actor, entry.action, entry.leader, entry.target, entry.outcome) == (
        "a (b c)",
        "leader.add",
        "eu-1",
        "x",
        "ok",
    )
    assert entry.detail == {}
    assert entry.at is not None


async def test_an_audit_entry_apart_never_raises(sessionmaker):
    def broken():
        raise RuntimeError("database is down")

    await audit.record_apart(broken, actor="a", action="x")
    await audit.record_apart(sessionmaker, actor="a", action="request.refused", outcome="csrf")
    async with sessionmaker() as session:
        (entry,) = (await session.scalars(select(AuditEntry))).all()
    assert (entry.action, entry.outcome) == ("request.refused", "csrf")


def test_migrate_brings_an_empty_database_to_head_and_back(admin_database_url, monkeypatch):
    name = "swarmscribe_console_migrate"
    url = with_database(admin_database_url, name)
    asyncio.run(recreate(admin_database_url, name))
    console_env(monkeypatch, url)

    async def tables() -> set[str]:
        conn = await asyncpg.connect(url)
        try:
            rows = await conn.fetch(
                "select tablename from pg_tables where schemaname = 'public'"
            )
            return {row["tablename"] for row in rows}
        finally:
            await conn.close()

    try:
        assert main(["migrate"]) == 0
        assert main(["migrate"]) == 0  # idempotent
        assert {
            "leaders",
            "role_grants",
            "console_admins",
            "snapshots",
            "audit_log",
            "sessions",
            "login_attempts",
        } <= asyncio.run(tables())
        command.downgrade(alembic_config(url), "base")
        assert asyncio.run(tables()) == {"alembic_version"}
    finally:
        asyncio.run(recreate(admin_database_url, name, drop_only=True))


def test_migrate_refuses_bad_settings_without_echoing_them(monkeypatch, capsys):
    monkeypatch.setenv("SWARMSCRIBE_CONSOLE_DATABASE_URL", "postgresql://u:secret-pw@h/db")
    monkeypatch.setenv("SWARMSCRIBE_CONSOLE_PUBLIC_URL", "https://console.test")
    monkeypatch.setenv("SWARMSCRIBE_CONSOLE_KEY", "a-wrong-key-value")
    assert main(["migrate"]) == 2
    err = capsys.readouterr().err
    assert "key" in err
    assert "a-wrong-key-value" not in err
    assert "secret-pw" not in err
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m uv run pytest packages/console/tests/test_migrations.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'swarmscribe_console.db'`.

- [ ] **Step 4: Write the models**

Create `packages/console/src/swarmscribe_console/db/__init__.py`: (empty file)

Create `packages/console/src/swarmscribe_console/db/models.py`:

```python
"""The console's own database (fleet console spec 5.1). Leaders never share it.

Case-insensitive uniqueness of leader names is a functional index created by migration 0001
(`uq_leaders_name_lower`); it is not declared here because autogenerate cannot compare
expression indexes."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

ROLE_CHECK = "role IN ('viewer','operator','admin')"
KIND_CHECK = "principal_kind IN ('entra_group','google_group','email','domain')"
SCOPE_CHECK = "scope = 'all' OR scope LIKE 'leader:_%' OR scope LIKE 'label:_%=_%'"
NAME_CHECK = "name ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$'"


class Base(DeclarativeBase):
    type_annotation_map = {
        datetime: DateTime(timezone=True),
        dict[str, Any]: JSONB,
        list[Any]: JSONB,
    }


class Leader(Base):
    """A registered leader. `credential` is its console credential sealed by
    crypto.ConsoleKeys; the last five columns are the poller's state (C2b)."""

    __tablename__ = "leaders"
    __table_args__ = (
        CheckConstraint(NAME_CHECK, name="ck_leaders_name"),
        CheckConstraint("base_url LIKE 'https://%'", name="ck_leaders_base_url_https"),
        CheckConstraint("jsonb_typeof(labels) = 'object'", name="ck_leaders_labels_object"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    base_url: Mapped[str] = mapped_column(Text)
    labels: Mapped[dict[str, Any]] = mapped_column(
        default=dict, server_default=text("'{}'::jsonb")
    )
    enabled: Mapped[bool] = mapped_column(default=True, server_default=true())
    credential: Mapped[bytes] = mapped_column(LargeBinary)
    credential_updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
    credential_updated_by: Mapped[str] = mapped_column(Text)
    credential_revoked_at: Mapped[datetime | None]
    added_by: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_polled_at: Mapped[datetime | None]
    last_success_at: Mapped[datetime | None]
    consecutive_failures: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(String(64))


class RoleGrant(Base):
    __tablename__ = "role_grants"
    __table_args__ = (
        CheckConstraint(ROLE_CHECK, name="ck_role_grants_role"),
        CheckConstraint(KIND_CHECK, name="ck_role_grants_principal_kind"),
        CheckConstraint(SCOPE_CHECK, name="ck_role_grants_scope"),
        UniqueConstraint(
            "scope", "principal_kind", "principal", name="uq_role_grants_scope_principal"
        ),
        Index("ix_role_grants_principal", "principal_kind", "principal"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    role: Mapped[str] = mapped_column(String(16))
    scope: Mapped[str] = mapped_column(String(400))
    principal_kind: Mapped[str] = mapped_column(String(16))
    principal: Mapped[str] = mapped_column(String(320))
    created_by: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ConsoleAdmin(Base):
    __tablename__ = "console_admins"
    __table_args__ = (
        CheckConstraint(KIND_CHECK, name="ck_console_admins_principal_kind"),
        UniqueConstraint("principal_kind", "principal", name="uq_console_admins_principal"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    principal_kind: Mapped[str] = mapped_column(String(16))
    principal: Mapped[str] = mapped_column(String(320))
    created_by: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Snapshot(Base):
    """One poll of one leader. `status` is the leader's /v1/admin/status answer when the
    poll succeeded, else None; `outcome` is "ok" or the failure's code."""

    __tablename__ = "snapshots"
    __table_args__ = (
        Index("ix_snapshots_leader_taken", "leader_id", "taken_at"),
        Index("ix_snapshots_taken", "taken_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    leader_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("leaders.id", ondelete="CASCADE")
    )
    taken_at: Mapped[datetime]
    reachable: Mapped[bool]
    outcome: Mapped[str] = mapped_column(String(64))
    status: Mapped[dict[str, Any] | None]


class AuditEntry(Base):
    """The console's own audit log. `leader` is a name, not a key, so entries outlive the
    leader's registration."""

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_at", "at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    at: Mapped[datetime] = mapped_column(server_default=func.now())
    actor: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(String(64))
    leader: Mapped[str | None] = mapped_column(String(100))
    target: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(String(64))
    detail: Mapped[dict[str, Any]] = mapped_column(
        default=dict, server_default=text("'{}'::jsonb")
    )


class ConsoleSession(Base):
    """A signed-in browser. The cookie holds the session id; only its SHA-256 is here."""

    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_expires_at", "expires_at"),)

    id_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(16))
    issuer: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    principals: Mapped[list[Any]] = mapped_column(default=list)
    created_at: Mapped[datetime]
    last_seen_at: Mapped[datetime]
    expires_at: Mapped[datetime]


class LoginAttempt(Base):
    """A sign-in that went to the identity provider and has not come back yet. Single use;
    the state and the browser binding are kept only as SHA-256."""

    __tablename__ = "login_attempts"
    __table_args__ = (Index("ix_login_attempts_expires_at", "expires_at"),)

    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    browser_hash: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(16))
    nonce: Mapped[str] = mapped_column(Text)
    code_verifier: Mapped[str] = mapped_column(Text)
    return_to: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime]
```

- [ ] **Step 5: Write migration 0001 and the Alembic plumbing**

Create `packages/console/src/swarmscribe_console/db/migrate.py`:

```python
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.script.revision import RevisionError
from alembic.util import CommandError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine
from swarmscribe_leader.db.session import to_async_url

MIGRATIONS = Path(__file__).parent / "migrations"


def alembic_config(database_url: str | None = None) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    if database_url is not None:
        config.set_main_option("sqlalchemy.url", to_async_url(database_url).replace("%", "%%"))
    return config


def upgrade(database_url: str) -> None:
    command.upgrade(alembic_config(database_url), "head")


def head_revision() -> str:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def is_known_revision(revision: str) -> bool:
    """Whether `revision` is in this console's history (False: a newer console migrated)."""
    try:
        return ScriptDirectory.from_config(alembic_config()).get_revision(revision) is not None
    except (CommandError, RevisionError):
        return False


async def current_revision(engine: AsyncEngine) -> str | None:
    async with engine.connect() as conn:
        try:
            return await conn.scalar(text("select version_num from alembic_version"))
        except DBAPIError:
            return None
```

Create `packages/console/src/swarmscribe_console/db/migrations/env.py`:

```python
import asyncio

from alembic import context
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from swarmscribe_console.db.models import Base

config = context.config
target_metadata = Base.metadata

# Concurrent `migrate` runs (several replicas starting) queue on this lock. Not the leader's
# key: a console and a leader never share a database, but keep them distinct anyway.
MIGRATION_LOCK_KEY = 0x53430000


def _run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        connection.execute(text("select pg_advisory_xact_lock(:key)"), {"key": MIGRATION_LOCK_KEY})
        context.run_migrations()


async def _main() -> None:
    engine = create_async_engine(config.get_main_option("sqlalchemy.url"))
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run_migrations)
            await connection.commit()
    finally:
        await engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("offline migrations are not supported")
asyncio.run(_main())
```

Create `packages/console/src/swarmscribe_console/db/migrations/script.py.mako` by copying the leader's template verbatim:

```bash
cp packages/leader/src/swarmscribe_leader/db/migrations/script.py.mako packages/console/src/swarmscribe_console/db/migrations/script.py.mako
```

Create `packages/console/src/swarmscribe_console/db/migrations/versions/0001_initial_schema.py`:

```python
"""fleet console: leaders, grants, console admins, snapshots, audit, sessions, sign-ins

Revision ID: 0001
Revises:
Create Date: 2026-10-03

Hand-written.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KIND_CHECK = "principal_kind IN ('entra_group','google_group','email','domain')"


def _now() -> sa.TextClause:
    return sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "leaders",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column(
            "labels",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("credential", sa.LargeBinary(), nullable=False),
        sa.Column(
            "credential_updated_at",
            sa.DateTime(timezone=True),
            server_default=_now(),
            nullable=False,
        ),
        sa.Column("credential_updated_by", sa.Text(), nullable=False),
        sa.Column("credential_revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("added_by", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False
        ),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "consecutive_failures", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("last_error", sa.String(length=64), nullable=True),
        sa.CheckConstraint("name ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$'", name="ck_leaders_name"),
        sa.CheckConstraint("base_url LIKE 'https://%'", name="ck_leaders_base_url_https"),
        sa.CheckConstraint("jsonb_typeof(labels) = 'object'", name="ck_leaders_labels_object"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("uq_leaders_name_lower", "leaders", [sa.text("lower(name)")], unique=True)

    op.create_table(
        "role_grants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("scope", sa.String(length=400), nullable=False),
        sa.Column("principal_kind", sa.String(length=16), nullable=False),
        sa.Column("principal", sa.String(length=320), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False
        ),
        sa.CheckConstraint("role IN ('viewer','operator','admin')", name="ck_role_grants_role"),
        sa.CheckConstraint(KIND_CHECK, name="ck_role_grants_principal_kind"),
        sa.CheckConstraint(
            "scope = 'all' OR scope LIKE 'leader:_%' OR scope LIKE 'label:_%=_%'",
            name="ck_role_grants_scope",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "scope", "principal_kind", "principal", name="uq_role_grants_scope_principal"
        ),
    )
    op.create_index(
        "ix_role_grants_principal", "role_grants", ["principal_kind", "principal"]
    )

    op.create_table(
        "console_admins",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("principal_kind", sa.String(length=16), nullable=False),
        sa.Column("principal", sa.String(length=320), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False
        ),
        sa.CheckConstraint(KIND_CHECK, name="ck_console_admins_principal_kind"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("principal_kind", "principal", name="uq_console_admins_principal"),
    )

    op.create_table(
        "snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("leader_id", sa.BigInteger(), nullable=False),
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reachable", sa.Boolean(), nullable=False),
        sa.Column("outcome", sa.String(length=64), nullable=False),
        sa.Column("status", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["leader_id"], ["leaders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_snapshots_leader_taken", "snapshots", ["leader_id", "taken_at"])
    op.create_index("ix_snapshots_taken", "snapshots", ["taken_at"])

    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("leader", sa.String(length=100), nullable=True),
        sa.Column("target", sa.Text(), nullable=True),
        sa.Column("outcome", sa.String(length=64), nullable=False),
        sa.Column(
            "detail",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_log_at", "audit_log", ["at"])

    op.create_table(
        "sessions",
        sa.Column("id_hash", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("issuer", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("principals", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id_hash"),
    )
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])

    op.create_table(
        "login_attempts",
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("browser_hash", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("nonce", sa.Text(), nullable=False),
        sa.Column("code_verifier", sa.Text(), nullable=False),
        sa.Column("return_to", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("state_hash"),
    )
    op.create_index("ix_login_attempts_expires_at", "login_attempts", ["expires_at"])


def downgrade() -> None:
    op.drop_table("login_attempts")
    op.drop_table("sessions")
    op.drop_table("audit_log")
    op.drop_table("snapshots")
    op.drop_table("console_admins")
    op.drop_table("role_grants")
    op.drop_table("leaders")
```

- [ ] **Step 6: Write the audit helper and the `migrate` command**

Create `packages/console/src/swarmscribe_console/audit.py`:

```python
"""The console's audit log (fleet console spec 5.1): actor, action, leader, target, outcome,
time. Entries never hold credentials, cookies, tokens or request bodies."""

import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .db.models import AuditEntry

logger = logging.getLogger(__name__)


def record(
    session: AsyncSession,
    *,
    actor: str,
    action: str,
    leader: str | None = None,
    target: str | None = None,
    outcome: str = "ok",
    detail: dict[str, Any] | None = None,
) -> None:
    """Add an entry to the session; it commits with the change it describes."""
    session.add(
        AuditEntry(
            actor=actor,
            action=action,
            leader=leader,
            target=target,
            outcome=outcome,
            detail=detail or {},
        )
    )


async def record_apart(sessionmaker: Callable[[], Any], **fields: Any) -> None:
    """An entry in its own session (for refusals, whose request rolled back). Never raises:
    a refusal must still be answered when the audit write fails."""
    try:
        async with sessionmaker() as session:
            record(session, **fields)
            await session.commit()
    except Exception as exc:
        logger.error("an audit entry could not be written: %s", type(exc).__name__)
```

Create `packages/console/src/swarmscribe_console/main.py`:

```python
import argparse
import sys
from collections.abc import Sequence

from pydantic import ValidationError

from .config import Settings
from .db.migrate import head_revision, upgrade

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"},
    },
    "handlers": {
        "stderr": {
            "class": "logging.StreamHandler",
            "formatter": "default",
            "stream": "ext://sys.stderr",
        },
    },
    # httpx logs every request URL at INFO; keep client libraries quiet.
    "loggers": {
        "httpx": {"level": "WARNING"},
        "httpcore": {"level": "WARNING"},
    },
    "root": {"level": "INFO", "handlers": ["stderr"]},
}


def _load_settings() -> Settings | None:
    try:
        return Settings()
    except ValidationError as exc:
        # Field names and messages only: the default rendering echoes secrets.
        print("error: invalid configuration (SWARMSCRIBE_CONSOLE_* environment):", file=sys.stderr)
        for error in exc.errors(include_input=False, include_url=False, include_context=False):
            field = ".".join(str(part) for part in error["loc"]) or "settings"
            print(f"  {field}: {error['msg']}", file=sys.stderr)
        return None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="swarmscribe-console")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="bring the console database schema up to date")
    args = parser.parse_args(argv)
    settings = _load_settings()
    if settings is None:
        return 2

    if args.command == "migrate":
        upgrade(settings.database_url.get_secret_value())
        print(f"database is at revision {head_revision()}")
        return 0
    return 2


def run() -> None:
    sys.exit(main())
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python -m uv run pytest packages/console/tests/test_migrations.py -v`
Expected: PASS. If `test_migrations_produce_exactly_the_models` reports a difference, fix the migration (the models are the reference), never the test.

Run: `python -m uv run ruff check packages/console`
Expected: no findings.

- [ ] **Step 8: Commit**

```bash
git add packages/console/src/swarmscribe_console/db packages/console/src/swarmscribe_console/audit.py packages/console/src/swarmscribe_console/main.py packages/console/tests/console_testkit.py packages/console/tests/conftest.py packages/console/tests/test_migrations.py
git commit -m "Console: its own database (migration 0001), audit log and migrate command

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Grants — scopes, principals and the highest matching role

**Files:**
- Create: `packages/console/src/swarmscribe_console/grants.py`
- Test: `packages/console/tests/test_grants.py`

**Interfaces:**
- Consumes: `RoleGrant`, `ConsoleAdmin` (Task 2); `Invalid` (Task 1); `swarmscribe_leader.auth.roles.RANK`, `Role`, `highest`.
- Produces:
  - `grants.LEADER_NAME`, `LABEL_KEY`, `LABEL_VALUE` (compiled patterns, used with `fullmatch`); `PRINCIPAL_KINDS = ("entra_group", "google_group", "email", "domain")`.
  - `grants.Scope(kind, leader=None, key=None, value=None)` frozen dataclass; `str(scope)` is the canonical text; `scope.matches(leader_name: str, labels: Mapping[str, Any]) -> bool`.
  - `grants.parse_scope(text: str) -> Scope` (raises `Invalid(code="invalid_scope")`).
  - `grants.normalize_principal(kind: str, value: str) -> str` (raises `Invalid(code="invalid_principal")`); `grants.principal_key(kind, value) -> str` (`"<kind>:<value>"`).
  - `grants.role_for(held: Iterable[tuple[str, Scope]], leader_name: str, labels: Mapping[str, Any]) -> Role | None`.
  - `async grants.grants_held(session, principals: Iterable[str]) -> list[tuple[str, Scope]]`.
  - `async grants.is_console_admin(session, principals: Iterable[str]) -> bool`.
  - `async grants.has_any_access(session, principals: Iterable[str]) -> bool`.

- [ ] **Step 1: Write the failing tests**

Create `packages/console/tests/test_grants.py`:

```python
import pytest
from swarmscribe_console.db.models import ConsoleAdmin, RoleGrant
from swarmscribe_console.errors import Invalid
from swarmscribe_console.grants import (
    Scope,
    grants_held,
    has_any_access,
    is_console_admin,
    normalize_principal,
    parse_scope,
    principal_key,
    role_for,
)

GROUP = "a1a1a1a1-0000-4000-8000-000000000002"


# --- scopes -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "scope", "canonical"),
    [
        ("all", Scope("all"), "all"),
        ("leader:EU-1", Scope("leader", leader="eu-1"), "leader:eu-1"),
        ("label:env=prod", Scope("label", key="env", value="prod"), "label:env=prod"),
        (
            "label:region=eu=west",
            Scope("label", key="region", value="eu=west"),
            "label:region=eu=west",
        ),
        ("label:team.name=A-b_c", Scope("label", key="team.name", value="A-b_c"), None),
    ],
)
def test_a_scope_is_parsed_and_written_back_canonically(text, scope, canonical):
    assert parse_scope(text) == scope
    assert str(parse_scope(text)) == (canonical or text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "ALL",
        "everything",
        "leader:",
        "leader:../eu",
        "leader:eu 1",
        "leader:a/b",
        "label:",
        "label:env",
        "label:env=",
        "label:=prod",
        "label:Env=prod",
        "label:env=has space",
        "label:env=café",
        "label:env=" + "x" * 256,
        "label:env=a\nb",
        "group:x",
    ],
)
def test_other_scopes_are_refused(text):
    with pytest.raises(Invalid) as raised:
        parse_scope(text)
    assert raised.value.code == "invalid_scope"


def test_a_label_value_with_an_equals_sign_matches_only_that_value():
    scope = parse_scope("label:region=eu=west")
    assert scope.matches("x", {"region": "eu=west"})
    assert not scope.matches("x", {"region": "eu"})
    assert not scope.matches("x", {"region=eu": "west"})


def test_scopes_match_by_name_ignoring_case_by_exact_label_or_all():
    assert parse_scope("leader:eu-1").matches("EU-1", {})
    assert not parse_scope("leader:eu-1").matches("eu-10", {})
    assert parse_scope("label:env=prod").matches("eu-1", {"env": "prod", "region": "eu"})
    assert not parse_scope("label:env=prod").matches("eu-1", {"env": "Prod"})
    assert not parse_scope("label:env=prod").matches("eu-1", {})
    assert parse_scope("all").matches("anything", {})


# --- the highest matching grant ---------------------------------------------------------


def test_the_highest_matching_grant_wins():
    held = [
        ("viewer", parse_scope("all")),
        ("operator", parse_scope("label:env=prod")),
        ("admin", parse_scope("leader:eu-1")),
    ]
    assert role_for(held, "eu-1", {"env": "prod"}) == "admin"
    assert role_for(held, "us-1", {"env": "prod"}) == "operator"
    assert role_for(held, "us-2", {"env": "test"}) == "viewer"


def test_no_matching_grant_is_no_role():
    held = [("admin", parse_scope("leader:eu-1"))]
    assert role_for(held, "us-1", {}) is None
    assert role_for([], "eu-1", {}) is None


# --- principals -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "value", "stored"),
    [
        ("entra_group", GROUP.upper(), GROUP),
        ("entra_group", "{" + GROUP + "}", GROUP),
        ("google_group", "Operators@Example.org", "operators@example.org"),
        ("email", " Person@Example.org ", "person@example.org"),
        ("domain", "@Example.org", "example.org"),
        ("domain", "eu.example.org", "eu.example.org"),
    ],
)
def test_principals_are_stored_in_one_canonical_form(kind, value, stored):
    assert normalize_principal(kind, value) == stored


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("entra_group", "operators"),
        ("email", "person"),
        ("email", "a@b@example.org"),
        ("email", "person@localhost"),
        ("email", "per son@example.org"),
        ("email", "person@exaKple.org"),
        ("google_group", "Kelvin@example.org"),
        ("domain", "example"),
        ("domain", "exa_mple.org"),
        ("domain", "*.example.org"),
        ("user", "person@example.org"),
        ("email", ""),
    ],
)
def test_odd_principals_are_refused(kind, value):
    with pytest.raises(Invalid) as raised:
        normalize_principal(kind, value)
    assert raised.value.code == "invalid_principal"


def test_a_principal_key_names_its_kind():
    assert principal_key("email", "a@example.org") == "email:a@example.org"


# --- from the database ------------------------------------------------------------------


async def _add(sessionmaker, *rows):
    async with sessionmaker() as session:
        session.add_all(rows)
        await session.commit()


def _grant(role, scope, kind, principal):
    return RoleGrant(
        role=role, scope=scope, principal_kind=kind, principal=principal, created_by="t"
    )


async def test_only_grants_held_by_the_persons_principals_count(sessionmaker):
    await _add(
        sessionmaker,
        _grant("operator", "label:env=prod", "entra_group", GROUP),
        _grant("admin", "all", "email", "someone-else@example.org"),
        _grant("viewer", "leader:eu-1", "domain", "example.org"),
    )
    async with sessionmaker() as session:
        held = await grants_held(
            session, {principal_key("entra_group", GROUP), "domain:example.org"}
        )
    assert sorted((role, str(scope)) for role, scope in held) == [
        ("operator", "label:env=prod"),
        ("viewer", "leader:eu-1"),
    ]
    assert role_for(held, "eu-1", {"env": "prod"}) == "operator"


async def test_no_principals_hold_nothing(sessionmaker):
    await _add(sessionmaker, _grant("admin", "all", "email", "a@example.org"))
    async with sessionmaker() as session:
        assert await grants_held(session, set()) == []
        assert await has_any_access(session, set()) is False


async def test_a_console_admin_holds_no_leader_role_but_has_access(sessionmaker):
    await _add(
        sessionmaker,
        ConsoleAdmin(principal_kind="email", principal="admin@example.org", created_by="t"),
    )
    principals = {"email:admin@example.org"}
    async with sessionmaker() as session:
        assert await is_console_admin(session, principals) is True
        assert await grants_held(session, principals) == []
        assert await has_any_access(session, principals) is True
        assert await is_console_admin(session, {"email:other@example.org"}) is False


async def test_a_corrupted_stored_scope_is_skipped_not_fatal(sessionmaker):
    await _add(
        sessionmaker,
        _grant("admin", "label:Bad Key=x", "email", "a@example.org"),
        _grant("viewer", "all", "email", "a@example.org"),
    )
    async with sessionmaker() as session:
        held = await grants_held(session, {"email:a@example.org"})
    assert [(role, str(scope)) for role, scope in held] == [("viewer", "all")]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m uv run pytest packages/console/tests/test_grants.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'swarmscribe_console.grants'`.

- [ ] **Step 3: Write the grants module**

Create `packages/console/src/swarmscribe_console/grants.py`:

```python
"""Console roles (fleet console spec 5.2).

A person's role for a leader is the highest grant whose scope matches the leader — by name,
by one of its labels, or `all` — among the grants held by any of the person's principals
(Entra group ids, Google groups, emails, domains; see principals.py). Console administrators
manage the registry and the grants; that gives them no leader role.

Scopes are stored canonically: `all`, `leader:<lowercase name>`, `label:<key>=<value>`. Label
keys cannot contain `=`, so a scope splits at its first `=` and a value may contain more."""

import logging
import re
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import exists, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from swarmscribe_leader.auth.roles import Role, highest

from .db.models import ConsoleAdmin, RoleGrant
from .errors import Invalid

logger = logging.getLogger(__name__)

PRINCIPAL_KINDS = ("entra_group", "google_group", "email", "domain")
LEADER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
LABEL_KEY = re.compile(r"[a-z0-9][a-z0-9._-]{0,62}")
LABEL_VALUE = re.compile(r"[!-~]{1,255}")
_VISIBLE = re.compile(r"[!-~]+")
_DNS_LABEL = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")
_SCOPE_HELP = (
    "a scope is all, leader:<name>, or label:<key>=<value> (key: lowercase letters, digits "
    "and . _ -; value: 1 to 255 printable characters without spaces)"
)


@dataclass(frozen=True)
class Scope:
    kind: Literal["all", "leader", "label"]
    leader: str | None = None
    key: str | None = None
    value: str | None = None

    def __str__(self) -> str:
        if self.kind == "all":
            return "all"
        if self.kind == "leader":
            return f"leader:{self.leader}"
        return f"label:{self.key}={self.value}"

    def matches(self, leader_name: str, labels: Mapping[str, Any]) -> bool:
        if self.kind == "all":
            return True
        if self.kind == "leader":
            return leader_name.lower() == self.leader
        return labels.get(self.key) == self.value


def parse_scope(text: str) -> Scope:
    if text == "all":
        return Scope("all")
    kind, sep, rest = text.partition(":")
    if sep and kind == "leader" and LEADER_NAME.fullmatch(rest):
        return Scope("leader", leader=rest.lower())
    if sep and kind == "label":
        key, eq, value = rest.partition("=")
        if eq and LABEL_KEY.fullmatch(key) and LABEL_VALUE.fullmatch(value):
            return Scope("label", key=key, value=value)
    raise Invalid(_SCOPE_HELP, code="invalid_scope")


def _is_domain(name: str) -> bool:
    labels = name.split(".")
    return (
        len(name) <= 253
        and len(labels) >= 2
        and all(_DNS_LABEL.fullmatch(label) for label in labels)
        and labels[-1][0].isalpha()
    )


def _bad_principal(message: str) -> Invalid:
    return Invalid(message, code="invalid_principal")


def normalize_principal(kind: str, value: str) -> str:
    """The canonical stored form of a grant's or console admin's principal. Only ASCII
    passes, so a Unicode look-alike can never be stored next to the real name."""
    text = value.strip()
    if kind == "entra_group":
        try:
            return str(uuid.UUID(text))
        except ValueError:
            raise _bad_principal("an Entra ID group is its object ID (a GUID)") from None
    if kind not in PRINCIPAL_KINDS:
        raise _bad_principal("principal_kind is entra_group, google_group, email or domain")
    if not text.isascii():
        raise _bad_principal("principals are ASCII")
    text = text.lower()
    if kind == "domain":
        text = text.removeprefix("@")
        if not _is_domain(text):
            raise _bad_principal("a domain is a DNS name such as example.org")
        return text
    local, at, domain = text.partition("@")
    if not (local and at and _VISIBLE.fullmatch(local) and _is_domain(domain)):
        raise _bad_principal("an email or Google group is one address such as a@example.org")
    return text


def principal_key(kind: str, value: str) -> str:
    return f"{kind}:{value}"


def _pairs(principals: Iterable[str]) -> list[tuple[str, str]]:
    found = set()
    for item in principals:
        kind, sep, value = item.partition(":")
        if sep and kind in PRINCIPAL_KINDS and value:
            found.add((kind, value))
    return sorted(found)


def role_for(
    held: Iterable[tuple[str, Scope]], leader_name: str, labels: Mapping[str, Any]
) -> Role | None:
    return highest(role for role, scope in held if scope.matches(leader_name, labels))


async def grants_held(
    session: AsyncSession, principals: Iterable[str]
) -> list[tuple[str, Scope]]:
    """Every (role, scope) granted to any of these principals. A stored scope that no longer
    parses (edited by hand) is skipped with a warning rather than failing the request."""
    pairs = _pairs(principals)
    if not pairs:
        return []
    rows = (
        await session.execute(
            select(RoleGrant.role, RoleGrant.scope).where(
                tuple_(RoleGrant.principal_kind, RoleGrant.principal).in_(pairs)
            )
        )
    ).all()
    held: list[tuple[str, Scope]] = []
    for role, scope in rows:
        try:
            held.append((role, parse_scope(scope)))
        except Invalid:
            logger.warning("a stored role grant has an unreadable scope and was skipped")
    return held


async def is_console_admin(session: AsyncSession, principals: Iterable[str]) -> bool:
    pairs = _pairs(principals)
    if not pairs:
        return False
    return bool(
        await session.scalar(
            select(
                exists().where(
                    tuple_(ConsoleAdmin.principal_kind, ConsoleAdmin.principal).in_(pairs)
                )
            )
        )
    )


async def has_any_access(session: AsyncSession, principals: Iterable[str]) -> bool:
    """Whether a person may hold a session at all: some grant, or console administration."""
    principals = list(principals)
    return bool(await grants_held(session, principals)) or await is_console_admin(
        session, principals
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m uv run pytest packages/console/tests/test_grants.py -v`
Expected: PASS.

Run: `python -m uv run ruff check packages/console`
Expected: no findings.

- [ ] **Step 5: Commit**

```bash
git add packages/console/src/swarmscribe_console/grants.py packages/console/tests/test_grants.py
git commit -m "Console: grant scopes and principals, highest matching grant wins

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Sessions, CSRF, security headers and the app

**Files:**
- Create: `packages/console/src/swarmscribe_console/sessions.py`, `app.py`, `api/__init__.py`, `api/deps.py`, `api/errors.py`, `api/security.py`, `api/session.py`
- Modify: `packages/console/src/swarmscribe_console/main.py` (add `serve`)
- Modify: `packages/console/tests/conftest.py` (append fixtures)
- Test: `packages/console/tests/test_sessions.py`

**Interfaces:**
- Consumes: `Settings`, `ConsoleKeys`, errors (Task 1); `ConsoleSession`, `audit` (Task 2); `grants.is_console_admin` (Task 3); `swarmscribe_leader.auth.secrets.hash_secret`, `new_secret`; `swarmscribe_leader.clock.utcnow`; `swarmscribe_leader.api.body_limit.BodyLimit`; `swarmscribe_protocol.ErrorBody`.
- Produces:
  - `sessions.SESSION_COOKIE = "__Host-swarmscribe-session"`, `sessions.LOGIN_COOKIE = "__Host-swarmscribe-login"`, `sessions.TOUCH_EVERY = timedelta(seconds=60)`, `sessions.is_token(value: str | None) -> bool`.
  - `sessions.SignedIn` frozen dataclass: `session_id` (hidden from repr), `provider`, `issuer`, `subject`, `email: str | None`, `principals: frozenset[str]`, `created_at`, `expires_at`, `idle_expires_at`; property `actor -> str`.
  - `async sessions.create_session(session, *, provider, issuer, subject, email, principals, now, lifetime: timedelta) -> str` (returns the new id; caller commits).
  - `async sessions.find_session(session, cookie: str | None, *, now, idle: timedelta) -> SignedIn | None` (deletes a stale row; caller commits).
  - `async sessions.end_session(session, cookie: str | None) -> None`; `async sessions.prune_expired(session, *, now, idle) -> int`.
  - `sessions.set_session_cookie(response, value, max_age)`, `clear_session_cookie(response)`, `set_login_cookie(response, value, max_age)`, `clear_login_cookie(response)`.
  - `api.deps.STATE_CHANGING`, `db_session`, `settings_of`, `keys_of`, `signed_in`, `checked`; annotated aliases `Person = Annotated[SignedIn, Depends(checked)]`, `Session = Annotated[AsyncSession, Depends(db_session)]`. `signed_in` sets `request.state.person`.
  - `api.errors.install(app)`, `api.errors.error_response(code, message, status, headers=None)`. Refused state-changing requests by a signed-in person are audited as `request.refused` unless the route set `request.state.audited = True`.
  - `api.security.SecurityHeaders` (ASGI middleware), `CONTENT_SECURITY_POLICY`.
  - `GET /api/session` → `{provider, issuer, subject, email, console_admin, csrf_token, expires_at, idle_expires_at}`; `POST /api/session/logout` → 204.
  - `app.create_app(settings) -> FastAPI` with `app.state.settings`, `engine`, `sessionmaker`, `keys`.
  - `main` gains `serve [--host] [--port]`.
  - `console_testkit` constants `PUBLIC_URL`, `ENTRA_TENANT`, `ENTRA_CLIENT`, `ENTRA_SECRET`, `ENTRA_ISSUER`, `GOOGLE_CLIENT`, `GOOGLE_SECRET`, `GOOGLE_ISSUER`, `GROUPS`; helpers `cookie_attributes(response, name) -> dict[str, str] | None`, `async all_rows_text(engine) -> str`.
  - Fixtures `make_settings(**overrides)`, `app`, `new_client()`, `client`, `factory` (`Factory.person(client, *, principals=(), provider="entra", issuer=ENTRA_ISSUER, subject="entra-person-1", email="person@example.org", now=None) -> str` CSRF token; `Factory.grant(role, scope, kind, principal)`; `Factory.console_admin(kind, principal)`).

- [ ] **Step 1: Add the app fixtures**

Append to `packages/console/tests/console_testkit.py` (add `import httpx`, `from sqlalchemy import text` and `from swarmscribe_console.db.models import Base` to its imports):

```python
PUBLIC_URL = "https://console.test"
ENTRA_TENANT = "0f0e0d0c-0b0a-4908-8706-050403020100"
ENTRA_CLIENT = "6d3a6b52-1c2e-4a8f-9d61-2f6a4c0b7e11"
ENTRA_SECRET = "entra-web-client-secret-value"
ENTRA_ISSUER = f"https://login.microsoftonline.com/{ENTRA_TENANT}/v2.0"
GOOGLE_CLIENT = "google-client-1.apps.googleusercontent.com"
GOOGLE_SECRET = "google-web-client-secret-value"
GOOGLE_ISSUER = "https://accounts.google.com"
GROUPS = {
    "viewer": "a1a1a1a1-0000-4000-8000-000000000001",
    "operator": "a1a1a1a1-0000-4000-8000-000000000002",
    "admin": "a1a1a1a1-0000-4000-8000-000000000003",
    "console": "a1a1a1a1-0000-4000-8000-0000000000c0",
}


def cookie_attributes(response: httpx.Response, name: str) -> dict[str, str] | None:
    """The attributes of the Set-Cookie header for `name` (lowercase keys), or None."""
    for header in response.headers.get_list("set-cookie"):
        first, *rest = [part.strip() for part in header.split(";")]
        key, _, value = first.partition("=")
        if key == name:
            attributes = {"value": value}
            for part in rest:
                attr, _, attr_value = part.partition("=")
                attributes[attr.lower()] = attr_value
            return attributes
    return None


async def all_rows_text(engine) -> str:
    """Every row of every console table as JSON text, for 'is this secret stored?' checks.
    (bytea columns appear hex-encoded; check sealed credentials on the raw bytes instead.)"""
    parts = []
    async with engine.connect() as conn:
        for table in Base.metadata.sorted_tables:
            rows = await conn.execute(text(f"select row_to_json(t)::text from {table.name} t"))
            parts.extend(row[0] for row in rows)
    return "\n".join(parts)
```

In `packages/console/tests/conftest.py`, replace the `console_testkit` import line with:

```python
from console_testkit import (  # noqa: E402
    ENTRA_CLIENT,
    ENTRA_ISSUER,
    ENTRA_SECRET,
    ENTRA_TENANT,
    GOOGLE_CLIENT,
    GOOGLE_SECRET,
    MASTER_KEY,
    PUBLIC_URL,
    TEST_KEY,
    recreate,
    with_database,
)
```

add these imports below the others (each with `# noqa: E402`):

```python
from datetime import timedelta  # noqa: E402

import httpx  # noqa: E402
from swarmscribe_console.app import create_app  # noqa: E402
from swarmscribe_console.config import Settings  # noqa: E402
from swarmscribe_console.db.models import ConsoleAdmin, RoleGrant  # noqa: E402
from swarmscribe_console.sessions import SESSION_COOKIE, create_session  # noqa: E402
from swarmscribe_leader.clock import utcnow  # noqa: E402
```

and append:

```python
@pytest.fixture
def make_settings(migrated_database_url):
    def make(**overrides) -> Settings:
        values = {
            "database_url": migrated_database_url,
            "public_url": PUBLIC_URL,
            "key": TEST_KEY,
            "entra_tenant_id": ENTRA_TENANT,
            "entra_client_id": ENTRA_CLIENT,
            "entra_client_secret": ENTRA_SECRET,
            "google_client_id": GOOGLE_CLIENT,
            "google_client_secret": GOOGLE_SECRET,
        }
        values.update(overrides)
        return Settings(**values)

    return make


@pytest.fixture
async def app(engine, make_settings):
    application = create_app(make_settings())
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def new_client(app):
    clients: list[httpx.AsyncClient] = []

    def make() -> httpx.AsyncClient:
        made = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=PUBLIC_URL)
        clients.append(made)
        return made

    yield make
    for made in clients:
        await made.aclose()


@pytest.fixture
async def client(new_client):
    return new_client()


class Factory:
    """Committed rows and signed-in browsers for tests."""

    def __init__(self, sessionmaker, keys):
        self.sessionmaker = sessionmaker
        self.keys = keys

    async def _save(self, row):
        async with self.sessionmaker() as session:
            session.add(row)
            await session.commit()
        return row

    async def person(
        self,
        client,
        *,
        principals=(),
        provider="entra",
        issuer=ENTRA_ISSUER,
        subject="entra-person-1",
        email="person@example.org",
        now=None,
    ) -> str:
        """Sign `client` in directly (a session row and its cookie). Returns the CSRF token."""
        async with self.sessionmaker() as session:
            session_id = await create_session(
                session,
                provider=provider,
                issuer=issuer,
                subject=subject,
                email=email,
                principals=frozenset(principals),
                now=now or utcnow(),
                lifetime=timedelta(hours=8),
            )
            await session.commit()
        client.cookies.set(SESSION_COOKIE, session_id, domain="console.test", path="/")
        return self.keys.csrf_token(session_id)

    async def grant(self, role, scope, kind, principal) -> RoleGrant:
        return await self._save(
            RoleGrant(
                role=role, scope=scope, principal_kind=kind, principal=principal, created_by="t"
            )
        )

    async def console_admin(self, kind, principal) -> ConsoleAdmin:
        return await self._save(
            ConsoleAdmin(principal_kind=kind, principal=principal, created_by="t")
        )


@pytest.fixture
def factory(sessionmaker, keys):
    return Factory(sessionmaker, keys)
```

- [ ] **Step 2: Write the failing tests**

Create `packages/console/tests/test_sessions.py`:

```python
from datetime import timedelta

import pytest
from fastapi.routing import APIRoute
from sqlalchemy import select, update
from swarmscribe_console.api.deps import STATE_CHANGING
from swarmscribe_console.db.models import AuditEntry, ConsoleSession
from swarmscribe_console.sessions import SESSION_COOKIE
from console_testkit import ENTRA_ISSUER, all_rows_text, cookie_attributes
from swarmscribe_leader.auth.secrets import hash_secret
from swarmscribe_leader.clock import utcnow


async def _session_row(sessionmaker) -> ConsoleSession:
    async with sessionmaker() as session:
        return (await session.scalars(select(ConsoleSession))).one()


async def _age(sessionmaker, **values):
    async with sessionmaker() as session:
        await session.execute(update(ConsoleSession).values(**values))
        await session.commit()


async def test_a_signed_in_browser_sees_its_session_and_csrf_token(client, factory, keys):
    csrf = await factory.person(client, principals={"email:person@example.org"})
    answer = await client.get("/api/session")
    assert answer.status_code == 200
    body = answer.json()
    assert (body["provider"], body["issuer"], body["subject"], body["email"]) == (
        "entra",
        ENTRA_ISSUER,
        "entra-person-1",
        "person@example.org",
    )
    assert body["csrf_token"] == csrf
    assert body["console_admin"] is False
    assert answer.headers["cache-control"] == "no-store"


async def test_a_console_admin_is_told_so(client, factory):
    await factory.console_admin("email", "person@example.org")
    await factory.person(client, principals={"email:person@example.org"})
    assert (await client.get("/api/session")).json()["console_admin"] is True


async def test_the_session_is_stored_only_by_its_hash(client, factory, sessionmaker, engine):
    await factory.person(client)
    session_id = client.cookies.get(SESSION_COOKIE)
    row = await _session_row(sessionmaker)
    assert row.id_hash == hash_secret(session_id)
    assert session_id not in await all_rows_text(engine)


async def test_no_cookie_is_unauthenticated_without_clearing_anything(client):
    answer = await client.get("/api/session")
    assert answer.status_code == 401
    assert answer.json()["code"] == "unauthenticated"
    assert cookie_attributes(answer, SESSION_COOKIE) is None


@pytest.mark.parametrize(
    "cookie",
    ["A" * 43, "x" * 10_000, "../../etc/passwd", "A" * 42 + "!", ""],
    ids=["forged", "oversize", "path", "bad-character", "empty"],
)
async def test_a_forged_or_malformed_cookie_is_refused_and_cleared(client, cookie):
    client.cookies.set(SESSION_COOKIE, cookie, domain="console.test", path="/")
    answer = await client.get("/api/session")
    assert answer.status_code == 401
    if cookie:
        cleared = cookie_attributes(answer, SESSION_COOKIE)
        assert cleared is not None and cleared["max-age"] == "0"


async def test_a_session_ends_eight_hours_after_sign_in(client, factory, sessionmaker):
    await factory.person(client)
    await _age(
        sessionmaker,
        created_at=utcnow() - timedelta(hours=8, seconds=1),
        expires_at=utcnow() - timedelta(seconds=1),
        last_seen_at=utcnow() - timedelta(seconds=5),
    )
    answer = await client.get("/api/session")
    assert answer.status_code == 401
    assert cookie_attributes(answer, SESSION_COOKIE)["max-age"] == "0"
    async with sessionmaker() as session:
        assert (await session.scalars(select(ConsoleSession))).all() == []


async def test_a_session_idle_for_an_hour_ends(client, factory, sessionmaker):
    await factory.person(client)
    await _age(sessionmaker, last_seen_at=utcnow() - timedelta(minutes=61))
    assert (await client.get("/api/session")).status_code == 401
    async with sessionmaker() as session:
        assert (await session.scalars(select(ConsoleSession))).all() == []


async def test_activity_keeps_a_session_alive_and_is_written_at_most_once_a_minute(
    client, factory, sessionmaker
):
    await factory.person(client)
    await _age(sessionmaker, last_seen_at=utcnow() - timedelta(minutes=59))
    assert (await client.get("/api/session")).status_code == 200
    touched = (await _session_row(sessionmaker)).last_seen_at
    assert utcnow() - touched < timedelta(seconds=30)
    assert (await client.get("/api/session")).status_code == 200
    assert (await _session_row(sessionmaker)).last_seen_at == touched


async def test_the_idle_deadline_never_passes_the_lifetime(client, factory, sessionmaker):
    await factory.person(client)
    await _age(sessionmaker, expires_at=utcnow() + timedelta(minutes=10))
    body = (await client.get("/api/session")).json()
    assert body["idle_expires_at"] == body["expires_at"]


# --- CSRF -------------------------------------------------------------------------------


async def test_logout_with_the_csrf_token_ends_the_session(client, factory, sessionmaker):
    csrf = await factory.person(client)
    answer = await client.post("/api/session/logout", headers={"X-CSRF-Token": csrf})
    assert answer.status_code == 204
    cleared = cookie_attributes(answer, SESSION_COOKIE)
    assert cleared["max-age"] == "0"
    assert (await client.get("/api/session")).status_code == 401
    async with sessionmaker() as session:
        actions = (await session.scalars(select(AuditEntry.action))).all()
    assert actions == ["sign_out"]


async def test_the_csrf_token_may_come_with_the_consoles_own_origin(client, factory):
    csrf = await factory.person(client)
    answer = await client.post(
        "/api/session/logout",
        headers={"X-CSRF-Token": csrf, "Origin": "https://console.test"},
    )
    assert answer.status_code == 204


async def test_csrf_failures_are_refused_audited_and_change_nothing(
    client, new_client, factory, sessionmaker
):
    csrf = await factory.person(client)
    other = new_client()
    other_csrf = await factory.person(other, subject="entra-person-2")
    attempts = [
        {},
        {"X-CSRF-Token": "x" * 43},
        {"X-CSRF-Token": other_csrf},
        {"X-CSRF-Token": csrf, "Origin": "https://evil.example"},
        {"X-CSRF-Token": csrf, "Origin": "null"},
    ]
    for headers in attempts:
        answer = await client.post("/api/session/logout", headers=headers)
        assert answer.status_code == 403, headers
        assert answer.json()["code"] == "csrf_failed"
    doubled = await client.post(
        "/api/session/logout", headers=[("X-CSRF-Token", csrf), ("X-CSRF-Token", csrf)]
    )
    assert doubled.status_code == 403
    assert (await client.get("/api/session")).status_code == 200
    async with sessionmaker() as session:
        entries = (await session.scalars(select(AuditEntry))).all()
    assert len(entries) == len(attempts) + 1
    assert {(e.action, e.outcome, e.target) for e in entries} == {
        ("request.refused", "csrf_failed", "POST /api/session/logout")
    }
    assert all(csrf not in str(e.detail) and other_csrf not in str(e.detail) for e in entries)


async def test_every_state_changing_api_route_needs_the_csrf_token(app, client, factory):
    """Structural: walks every route, including those later tasks add."""
    await factory.person(client)
    checked = 0
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api"):
            continue
        for method in sorted(route.methods & STATE_CHANGING):
            path = route.path
            for name in route.param_convertors:
                path = path.replace(f"{{{name}:path}}", "x/x").replace(f"{{{name}}}", "x")
            answer = await client.request(method, path)
            assert answer.status_code == 403, (method, route.path, answer.text)
            assert answer.json()["code"] == "csrf_failed", (method, route.path)
            checked += 1
    assert checked >= 1


# --- headers ----------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/api/session", "/nothing-here", "/auth/nothing"])
async def test_every_answer_carries_the_security_headers(client, path):
    answer = await client.get(path)
    csp = answer.headers["content-security-policy"]
    assert "script-src 'self'" in csp
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert answer.headers["x-content-type-options"] == "nosniff"
    assert answer.headers["referrer-policy"] == "no-referrer"
    assert answer.headers["x-frame-options"] == "DENY"
    assert answer.headers["strict-transport-security"].startswith("max-age=")
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m uv run pytest packages/console/tests/test_sessions.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'swarmscribe_console.app'` (raised by conftest).

- [ ] **Step 4: Write the sessions module**

Create `packages/console/src/swarmscribe_console/sessions.py`:

```python
"""Server-side sessions (fleet console spec 5.2).

The browser holds only a random session id in an HttpOnly, Secure, SameSite=Strict cookie; the
database holds its SHA-256, so a copy of the table cannot be replayed as a cookie. A session
ends 8 hours after sign-in or after 1 hour without a request, whichever comes first; activity
is written at most once a minute. Nothing here keeps a person's ID, access or refresh token."""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import delete, or_
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response
from swarmscribe_leader.auth.secrets import hash_secret, new_secret

from .db.models import ConsoleSession, LoginAttempt

SESSION_COOKIE = "__Host-swarmscribe-session"
LOGIN_COOKIE = "__Host-swarmscribe-login"
TOUCH_EVERY = timedelta(seconds=60)
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")


def is_token(value: str | None) -> bool:
    """Whether `value` has new_secret()'s shape; anything else is refused before hashing."""
    return bool(value) and _TOKEN.fullmatch(value) is not None


@dataclass(frozen=True)
class SignedIn:
    session_id: str = field(repr=False)
    provider: str
    issuer: str
    subject: str
    email: str | None
    principals: frozenset[str]
    created_at: datetime
    expires_at: datetime
    idle_expires_at: datetime

    @property
    def actor(self) -> str:
        """How the audit log names this person: the leader's Identity.actor format."""
        return f"{self.email or 'unknown'} ({self.issuer} {self.subject})"


async def create_session(
    session: AsyncSession,
    *,
    provider: str,
    issuer: str,
    subject: str,
    email: str | None,
    principals: frozenset[str],
    now: datetime,
    lifetime: timedelta,
) -> str:
    session_id = new_secret()
    session.add(
        ConsoleSession(
            id_hash=hash_secret(session_id),
            provider=provider,
            issuer=issuer,
            subject=subject,
            email=email,
            principals=sorted(principals),
            created_at=now,
            last_seen_at=now,
            expires_at=now + lifetime,
        )
    )
    return session_id


async def find_session(
    session: AsyncSession, cookie: str | None, *, now: datetime, idle: timedelta
) -> SignedIn | None:
    if not is_token(cookie):
        return None
    row = await session.get(ConsoleSession, hash_secret(cookie))
    if row is None:
        return None
    if now >= row.expires_at or now >= row.last_seen_at + idle:
        await session.delete(row)
        return None
    if now - row.last_seen_at >= TOUCH_EVERY:
        row.last_seen_at = now
    return SignedIn(
        session_id=cookie,
        provider=row.provider,
        issuer=row.issuer,
        subject=row.subject,
        email=row.email,
        principals=frozenset(row.principals),
        created_at=row.created_at,
        expires_at=row.expires_at,
        idle_expires_at=min(row.last_seen_at + idle, row.expires_at),
    )


async def end_session(session: AsyncSession, cookie: str | None) -> None:
    if is_token(cookie):
        await session.execute(
            delete(ConsoleSession).where(ConsoleSession.id_hash == hash_secret(cookie))
        )


async def prune_expired(session: AsyncSession, *, now: datetime, idle: timedelta) -> int:
    """Delete ended sessions and abandoned sign-ins. Returns how many rows went."""
    ended = await session.execute(
        delete(ConsoleSession).where(
            or_(ConsoleSession.expires_at <= now, ConsoleSession.last_seen_at <= now - idle)
        )
    )
    abandoned = await session.execute(delete(LoginAttempt).where(LoginAttempt.expires_at <= now))
    return ended.rowcount + abandoned.rowcount


def set_session_cookie(response: Response, value: str, max_age: int) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        value,
        max_age=max_age,
        path="/",
        secure=True,
        httponly=True,
        samesite="strict",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="strict")


def set_login_cookie(response: Response, value: str, max_age: int) -> None:
    # Lax, not Strict: the identity provider's redirect back is a cross-site navigation, on
    # which a Strict cookie would not be sent.
    response.set_cookie(
        LOGIN_COOKIE, value, max_age=max_age, path="/", secure=True, httponly=True, samesite="lax"
    )


def clear_login_cookie(response: Response) -> None:
    response.delete_cookie(LOGIN_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
```

- [ ] **Step 5: Write the dependencies, error handlers and security headers**

Create `packages/console/src/swarmscribe_console/api/__init__.py`: (empty file)

Create `packages/console/src/swarmscribe_console/api/deps.py`:

```python
"""Request dependencies: the database session, the signed-in person and the CSRF check.

Every /api route takes `Person`, so the CSRF rule cannot be forgotten on a new route: a
POST, PUT, PATCH or DELETE needs exactly one X-CSRF-Token equal to the session's token, and
an Origin header, if sent, must be the console's own."""

from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from swarmscribe_leader.clock import utcnow

from ..config import Settings
from ..crypto import ConsoleKeys
from ..errors import CsrfRejected, Unauthenticated
from ..sessions import SESSION_COOKIE, SignedIn, find_session

STATE_CHANGING = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CSRF_HEADER = "x-csrf-token"


async def db_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session


def settings_of(request: Request) -> Settings:
    return request.app.state.settings


def keys_of(request: Request) -> ConsoleKeys:
    return request.app.state.keys


async def signed_in(request: Request) -> SignedIn:
    cookie = request.cookies.get(SESSION_COOKIE)
    idle = timedelta(seconds=settings_of(request).session_idle_seconds)
    async with request.app.state.sessionmaker() as session:
        person = await find_session(session, cookie, now=utcnow(), idle=idle)
        await session.commit()
    if person is None:
        raise Unauthenticated("sign in to the console first", clear_cookie=cookie is not None)
    request.state.person = person
    return person


async def checked(request: Request, person: Annotated[SignedIn, Depends(signed_in)]) -> SignedIn:
    if request.method in STATE_CHANGING:
        origins = request.headers.getlist("origin")
        if origins and origins != [settings_of(request).public_url]:
            raise CsrfRejected("this request did not come from the console")
        tokens = request.headers.getlist(CSRF_HEADER)
        if len(tokens) != 1 or not keys_of(request).csrf_matches(person.session_id, tokens[0]):
            raise CsrfRejected("missing or wrong CSRF token; reload the console")
    return person


Person = Annotated[SignedIn, Depends(checked)]
Session = Annotated[AsyncSession, Depends(db_session)]
```

Create `packages/console/src/swarmscribe_console/api/errors.py`:

```python
"""Error answers: always `{code, message}` (the leader's ErrorBody), never request values.

A state-changing request refused after the person was identified is audited as
`request.refused` with the route template and the error code, unless the route audited the
refusal itself (request.state.audited)."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError
from starlette.exceptions import HTTPException as StarletteHTTPException
from swarmscribe_protocol import ErrorBody

from .. import audit
from ..errors import ConsoleError, Unauthenticated
from ..sessions import clear_session_cookie
from .deps import STATE_CHANGING

logger = logging.getLogger(__name__)

_HTTP_CODES = {404: "not_found", 405: "method_not_allowed"}
OUTAGE_RETRY_AFTER = 10


def error_response(
    code: str, message: str, status: int, headers: dict[str, str] | None = None
) -> JSONResponse:
    body = ErrorBody(code=code, message=message).model_dump()
    return JSONResponse(body, status_code=status, headers=headers)


async def audit_refused(request: Request, code: str) -> None:
    person = getattr(request.state, "person", None)
    if (
        person is None
        or request.method not in STATE_CHANGING
        or getattr(request.state, "audited", False)
    ):
        return
    route = request.scope.get("route")
    await audit.record_apart(
        request.app.state.sessionmaker,
        actor=person.actor,
        action="request.refused",
        target=f"{request.method} {getattr(route, 'path', '?')}",
        outcome=code,
    )


def install(app: FastAPI) -> None:
    # Nothing here logs the request URL: the sign-in callback's carries an authorization code.
    @app.exception_handler(ConsoleError)
    async def console_error(request: Request, exc: ConsoleError) -> JSONResponse:
        await audit_refused(request, exc.code)
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
        response = error_response(exc.code, exc.message, exc.status, headers)
        if isinstance(exc, Unauthenticated) and exc.clear_cookie:
            clear_session_cookie(response)
        return response

    @app.exception_handler(DBAPIError)
    async def outage(_request: Request, exc: DBAPIError) -> JSONResponse:
        # Class names only: a DBAPIError's text carries the SQL and its parameters.
        logger.error(
            "database unavailable: %s (%s)", type(exc).__name__, type(exc.orig).__name__
        )
        return error_response(
            "unavailable",
            "service temporarily unavailable",
            503,
            {"Retry-After": str(OUTAGE_RETRY_AFTER)},
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, f"http_{exc.status_code}")
        return error_response(code, str(exc.detail), exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        await audit_refused(request, "invalid_request")
        summary = "; ".join(
            f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        return error_response("invalid_request", summary[:500], 422)

    @app.exception_handler(Exception)
    async def unhandled(_request: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled error: %s", type(exc).__name__, exc_info=exc)
        return error_response("internal", "internal error", 500)
```

Create `packages/console/src/swarmscribe_console/api/security.py`:

```python
"""Headers on every answer, API and static files alike (fleet console spec 7): scripts only
from the console's own origin, no framing, no referrer (the sign-in callback's URL carries an
authorization code), HSTS, and no caching of API or sign-in answers."""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)
_ALWAYS = {
    "content-security-policy": CONTENT_SECURITY_POLICY,
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
    "strict-transport-security": "max-age=31536000; includeSubDomains",
    "cross-origin-opener-policy": "same-origin",
}
_NO_STORE_PREFIXES = ("/api/", "/auth/")


class SecurityHeaders:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        no_store = scope["path"].startswith(_NO_STORE_PREFIXES)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in _ALWAYS.items():
                    headers[name] = value
                if no_store:
                    headers["cache-control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_with_headers)
```

- [ ] **Step 6: Write the session routes and the app**

Create `packages/console/src/swarmscribe_console/api/session.py`:

```python
"""The signed-in person's session: who they are, their CSRF token, and sign-out."""

from datetime import datetime

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from .. import audit, grants
from ..sessions import clear_session_cookie, end_session
from .deps import Person, Session, keys_of

router = APIRouter(prefix="/api/session")


class SessionOut(BaseModel):
    provider: str
    issuer: str
    subject: str
    email: str | None
    console_admin: bool
    csrf_token: str
    expires_at: datetime
    idle_expires_at: datetime


@router.get("", response_model=SessionOut)
async def current_session(request: Request, person: Person, session: Session) -> SessionOut:
    return SessionOut(
        provider=person.provider,
        issuer=person.issuer,
        subject=person.subject,
        email=person.email,
        console_admin=await grants.is_console_admin(session, person.principals),
        csrf_token=keys_of(request).csrf_token(person.session_id),
        expires_at=person.expires_at,
        idle_expires_at=person.idle_expires_at,
    )


@router.post("/logout", status_code=204)
async def logout(person: Person, session: Session) -> Response:
    await end_session(session, person.session_id)
    audit.record(session, actor=person.actor, action="sign_out")
    await session.commit()
    response = Response(status_code=204)
    clear_session_cookie(response)
    return response
```

Create `packages/console/src/swarmscribe_console/app.py`:

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI
from swarmscribe_leader.api.body_limit import BodyLimit
from swarmscribe_leader.db.session import make_engine, make_sessionmaker

from .api import errors as api_errors
from .api import session as session_api
from .api.security import SecurityHeaders
from .config import Settings
from .crypto import ConsoleKeys


def create_app(settings: Settings) -> FastAPI:
    engine = make_engine(settings.database_url.get_secret_value())
    sessionmaker = make_sessionmaker(engine)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(
        title="SwarmScribe console",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = sessionmaker
    app.state.keys = ConsoleKeys(settings.key_bytes())
    api_errors.install(app)
    app.add_middleware(BodyLimit)
    app.add_middleware(SecurityHeaders)  # outermost: also covers BodyLimit's 413
    app.include_router(session_api.router)
    return app
```

- [ ] **Step 7: Add `serve` to the command line**

In `packages/console/src/swarmscribe_console/main.py`, replace the imports block with:

```python
import argparse
import asyncio
import logging.config
import sys
from collections.abc import Sequence

from pydantic import ValidationError
from swarmscribe_leader.db.session import make_engine

from .config import Settings
from .db.migrate import current_revision, head_revision, is_known_revision, upgrade
```

add this function after `_load_settings`:

```python
async def _schema_problem(settings: Settings) -> str | None:
    """Why the console must not serve this database, or None when the schema is current."""
    engine = make_engine(settings.database_url.get_secret_value())
    try:
        revision = await current_revision(engine)
    except Exception as exc:  # refused, unreachable, bad credentials, timeout
        return f"cannot connect to the database: {type(exc).__name__}"
    finally:
        await engine.dispose()
    expected = head_revision()
    if revision == expected:
        return None
    if revision is not None and not is_known_revision(revision):
        return (
            f"database is ahead of this console: it is at revision {revision}, "
            f"this console expects {expected}; run a newer console"
        )
    return (
        f"database is at revision {revision or 'none'}, expected {expected}; "
        "run `swarmscribe-console migrate`"
    )
```

and replace `main` with:

```python
def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="swarmscribe-console")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="bring the console database schema up to date")
    serve = commands.add_parser("serve", help="run the console")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
    settings = _load_settings()
    if settings is None:
        return 2

    if args.command == "migrate":
        upgrade(settings.database_url.get_secret_value())
        print(f"database is at revision {head_revision()}")
        return 0

    problem = asyncio.run(_schema_problem(settings))
    if problem is not None:
        print(f"error: {problem}", file=sys.stderr)
        return 2

    logging.config.dictConfig(LOGGING)
    import uvicorn

    from .app import create_app

    # access_log=False: the sign-in callback's URL carries an authorization code.
    # log_config=None: keep the logging configured above.
    uvicorn.run(
        create_app(settings),
        host=args.host,
        port=args.port,
        proxy_headers=True,
        access_log=False,
        log_config=None,
    )
    return 0
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `python -m uv run pytest packages/console/tests -v`
Expected: PASS (Tasks 1–4).

Run: `python -m uv run ruff check packages/console`
Expected: no findings.

- [ ] **Step 9: Commit**

```bash
git add packages/console/src/swarmscribe_console/sessions.py packages/console/src/swarmscribe_console/app.py packages/console/src/swarmscribe_console/api packages/console/src/swarmscribe_console/main.py packages/console/tests/console_testkit.py packages/console/tests/conftest.py packages/console/tests/test_sessions.py
git commit -m "Console: server-side sessions, CSRF on every change, security headers

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Browser sign-in — authorization code + PKCE, principals, fresh session

**Files:**
- Create: `packages/console/src/swarmscribe_console/oidc.py`, `principals.py`, `api/auth.py`
- Modify: `packages/console/src/swarmscribe_console/app.py`
- Modify: `packages/console/tests/conftest.py` (fake identity provider; `app` fixture)
- Test: `packages/console/tests/test_sign_in.py`

**Interfaces:**
- Consumes: `LoginAttempt` (Task 2); `grants.has_any_access` (Task 3); `sessions.*`, `api.deps.*`, `audit` (Tasks 2, 4); from the leader: `auth.oidc.TokenVerifier`, `Provider`, `Identity`, `ProviderName`, `ENTRA_AUTHORITY`, `GOOGLE_ISSUERS`, `GOOGLE_DISCOVERY`, `http_fetch`, `MetadataUnavailable`, `Fetch`; `auth.roles.MicrosoftGraph`, `GoogleCloudIdentity`, `GraphClient`, `GoogleGroupsClient`, `RoleLookupFailed`, `CONSUMER_GOOGLE_DOMAINS`; `errors.Unauthorized`.
- Produces:
  - `oidc.WebProvider(name, client_id, client_secret, authorization_endpoint, token_endpoint, verification: Provider)`; `oidc.web_providers(settings) -> dict[str, WebProvider]`; `oidc.SCOPE`, `oidc.CALLBACK_PATH = "/auth/callback"`; `oidc.code_challenge(verifier) -> str`.
  - `async oidc.begin_sign_in(session, provider, *, redirect_uri, return_to, now, ttl) -> tuple[str, str]` (authorization URL, browser secret).
  - `async oidc.finish_sign_in(session, *, state, browser_secret, now) -> oidc.SignInAttempt` (`provider`, `nonce`, `code_verifier`, `return_to`); raises `oidc.SignInFailed`. The attempt is deleted whatever happens; the caller commits.
  - `async oidc.exchange_code(provider, *, code, verifier, redirect_uri, transport=None) -> str` (the ID token); raises `oidc.CodeExchangeFailed`.
  - `async principals.principals_for(identity, *, graph, google_groups) -> frozenset[str]` (raises the leader's `RoleLookupFailed`).
  - `api.auth.safe_return_to(value: str | None) -> str`; routes `GET /auth/providers`, `GET /auth/login?provider=&return_to=`, `GET /auth/callback`.
  - `app.create_app(settings, *, fetch=None, idp_transport=None, graph=None, google_groups=None)`; `app.state.web_providers`, `verifier`, `idp_transport`, `graph`, `google_groups`.
  - Test fixtures `idp` (`FakeIdentityProviders`: `fetch`, `authorize(location, **claims) -> str`, `transport`, `issued`, `exchanges`, `token_status`, `id_token(provider, **claims)`), `graph` (`FakeGraph`), `google_groups` (`FakeGoogleGroups`); `console_testkit.sign_in(client, idp, *, provider="entra", return_to=None, **claims) -> httpx.Response`.

- [ ] **Step 1: Add the fake identity provider and replace the `app` fixture**

Append to `packages/console/tests/console_testkit.py`:

```python
async def sign_in(client, idp, *, provider="entra", return_to=None, **claims) -> httpx.Response:
    """The whole browser sign-in: the console's /auth/login, the provider, the callback."""
    params = {"provider": provider}
    if return_to is not None:
        params["return_to"] = return_to
    started = await client.get("/auth/login", params=params)
    assert started.status_code == 302, started.text
    return await client.get(idp.authorize(started.headers["location"], **claims))
```

In `packages/console/tests/conftest.py`, add `GOOGLE_ISSUER` to the `console_testkit` import list, add these imports below the others (each with `# noqa: E402`):

```python
import base64  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import secrets  # noqa: E402
import time  # noqa: E402
from urllib.parse import parse_qsl, urlencode  # noqa: E402

import jwt as pyjwt  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from jwt.algorithms import RSAAlgorithm  # noqa: E402
from swarmscribe_leader.auth.roles import RoleLookupFailed  # noqa: E402
```

and append:

```python
@pytest.fixture(scope="session")
def signing_keys():
    return {
        name: rsa.generate_private_key(public_exponent=65537, key_size=2048)
        for name in ("entra", "google", "rogue")
    }


class FakeIdentityProviders:
    """Entra ID and Google as the console sees them: discovery documents and JWKS through an
    injected fetcher; the person's visit to the authorization page (`authorize`); and the
    token endpoint the console posts the code to (`transport`). Every token it hands out is
    kept in `issued`, so tests can check none of them is stored."""

    SECRETS = {"entra": ENTRA_SECRET, "google": GOOGLE_SECRET}
    CLIENTS = {"entra": ENTRA_CLIENT, "google": GOOGLE_CLIENT}

    def __init__(self, keys):
        self.keys = keys
        self.codes: dict[str, dict] = {}
        self.exchanges: list[dict[str, str]] = []
        self.issued: list[str] = []
        self.token_status = 200

    def _jwks(self, provider: str) -> dict:
        jwk = json.loads(RSAAlgorithm.to_jwk(self.keys[provider].public_key()))
        jwk.update(kid=f"{provider}-key-1", use="sig", alg="RS256")
        return {"keys": [jwk]}

    async def fetch(self, url: str) -> dict:
        entra_jwks = f"https://login.microsoftonline.com/{ENTRA_TENANT}/discovery/v2.0/keys"
        google_jwks = "https://www.googleapis.com/oauth2/v3/certs"
        documents = {
            f"{ENTRA_ISSUER}/.well-known/openid-configuration": {
                "issuer": ENTRA_ISSUER,
                "jwks_uri": entra_jwks,
            },
            entra_jwks: self._jwks("entra"),
            "https://accounts.google.com/.well-known/openid-configuration": {
                "issuer": GOOGLE_ISSUER,
                "jwks_uri": google_jwks,
            },
            google_jwks: self._jwks("google"),
        }
        return documents[url]

    def _defaults(self, provider: str) -> dict:
        if provider == "entra":
            return {
                "iss": ENTRA_ISSUER,
                "aud": ENTRA_CLIENT,
                "tid": ENTRA_TENANT,
                "sub": "entra-person-1",
                "oid": "00000000-0000-4000-8000-0000000000a1",
                "email": "person@example.org",
                "groups": [],
            }
        return {
            "iss": GOOGLE_ISSUER,
            "aud": GOOGLE_CLIENT,
            "sub": "google-person-1",
            "email": "person@example.org",
            "email_verified": True,
        }

    def id_token(self, provider: str, *, signed_with: str | None = None, **claims) -> str:
        now = int(time.time())
        payload = {"iat": now, "nbf": now, "exp": now + 3600, **self._defaults(provider)}
        payload.update(claims)
        payload = {k: v for k, v in payload.items() if v is not None}
        key = self.keys[signed_with or provider]
        return pyjwt.encode(
            payload, key, algorithm="RS256", headers={"kid": f"{provider}-key-1"}
        )

    def authorize(self, location: str, **claims) -> str:
        """The person signs in at the provider. Returns the path and query the provider
        sends the browser back to."""
        url = httpx.URL(location)
        params = dict(url.params)
        provider = "entra" if url.host == "login.microsoftonline.com" else "google"
        code = secrets.token_urlsafe(24)
        self.codes[code] = {
            "provider": provider,
            "claims": {"nonce": params["nonce"], **claims},
            "challenge": params["code_challenge"],
            "redirect_uri": params["redirect_uri"],
        }
        back = httpx.URL(params["redirect_uri"])
        return f"{back.path}?{urlencode({'code': code, 'state': params['state']})}"

    async def _token_endpoint(self, request: httpx.Request) -> httpx.Response:
        form = dict(parse_qsl((await request.aread()).decode()))
        self.exchanges.append(form)
        if self.token_status != 200:
            return httpx.Response(self.token_status, json={"error": "invalid_grant"})
        grant = self.codes.pop(form.get("code", ""), None)
        if grant is None:
            return httpx.Response(400, json={"error": "invalid_grant"})
        provider = grant["provider"]
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        if (
            form.get("grant_type") != "authorization_code"
            or challenge != grant["challenge"]
            or form.get("redirect_uri") != grant["redirect_uri"]
            or form.get("client_id") != self.CLIENTS[provider]
            or form.get("client_secret") != self.SECRETS[provider]
        ):
            return httpx.Response(400, json={"error": "invalid_grant"})
        id_token = self.id_token(provider, **grant["claims"])
        access = f"access-{secrets.token_urlsafe(16)}"
        refresh = f"refresh-{secrets.token_urlsafe(16)}"
        self.issued += [id_token, access, refresh]
        return httpx.Response(
            200,
            json={
                "token_type": "Bearer",
                "expires_in": 3600,
                "id_token": id_token,
                "access_token": access,
                "refresh_token": refresh,
            },
        )

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._token_endpoint)


@pytest.fixture
def idp(signing_keys):
    return FakeIdentityProviders(signing_keys)


class FakeGraph:
    def __init__(self):
        self.groups: dict[str, set[str]] = {}
        self.failing = False

    async def member_object_ids(self, user_object_id: str) -> set[str]:
        if self.failing:
            raise RoleLookupFailed("Microsoft Graph could not be asked: ConnectError")
        return set(self.groups.get(user_object_id, set()))


class FakeGoogleGroups:
    def __init__(self):
        self.groups: dict[str, set[str]] = {}
        self.failing = False

    async def group_emails(self, email: str) -> set[str]:
        if self.failing:
            raise RoleLookupFailed("Google Cloud Identity could not be asked: ConnectError")
        return set(self.groups.get(email, set()))


@pytest.fixture
def graph():
    return FakeGraph()


@pytest.fixture
def google_groups():
    return FakeGoogleGroups()
```

Replace the `app` fixture with:

```python
@pytest.fixture
async def app(engine, make_settings, idp, graph, google_groups):
    application = create_app(
        make_settings(),
        fetch=idp.fetch,
        idp_transport=idp.transport,
        graph=graph,
        google_groups=google_groups,
    )
    async with application.router.lifespan_context(application):
        yield application
```

- [ ] **Step 2: Write the failing tests**

Create `packages/console/tests/test_sign_in.py`:

```python
import base64
import hashlib
import re

import httpx
import pytest
from console_testkit import (
    ENTRA_CLIENT,
    ENTRA_ISSUER,
    GOOGLE_ISSUER,
    GROUPS,
    all_rows_text,
    cookie_attributes,
    sign_in,
)
from sqlalchemy import select, update
from swarmscribe_console.db.models import AuditEntry, ConsoleSession, LoginAttempt
from swarmscribe_console.sessions import LOGIN_COOKIE, SESSION_COOKIE
from swarmscribe_leader.clock import utcnow


def _refresh_target(answer: httpx.Response) -> str:
    found = re.search(r'http-equiv="refresh" content="0;url=([^"]*)"', answer.text)
    assert found, answer.text
    return found.group(1).replace("&amp;", "&")


@pytest.fixture
async def operators(factory):
    await factory.grant("operator", "all", "entra_group", GROUPS["operator"])


async def _sessions(sessionmaker) -> list[ConsoleSession]:
    async with sessionmaker() as session:
        return list((await session.scalars(select(ConsoleSession))).all())


# --- the happy paths --------------------------------------------------------------------


async def test_an_entra_person_with_a_grant_signs_in(client, idp, operators, sessionmaker):
    answer = await sign_in(client, idp, groups=[GROUPS["operator"]])
    assert answer.status_code == 200
    assert _refresh_target(answer) == "/"
    (row,) = await _sessions(sessionmaker)
    assert (row.provider, row.issuer, row.subject, row.email) == (
        "entra",
        ENTRA_ISSUER,
        "entra-person-1",
        "person@example.org",
    )
    assert row.principals == [f"entra_group:{GROUPS['operator']}"]
    me = await client.get("/api/session")
    assert me.status_code == 200
    assert me.json()["email"] == "person@example.org"


async def test_the_session_cookie_has_the_spec_flags(client, idp, operators):
    answer = await sign_in(client, idp, groups=[GROUPS["operator"]])
    cookie = cookie_attributes(answer, SESSION_COOKIE)
    assert cookie is not None
    assert SESSION_COOKIE.startswith("__Host-")
    assert "httponly" in cookie
    assert "secure" in cookie
    assert cookie["samesite"].lower() == "strict"
    assert cookie["path"] == "/"
    assert cookie["max-age"] == str(8 * 3600)
    assert "domain" not in cookie
    login = cookie_attributes(answer, LOGIN_COOKIE)
    assert login is not None and login["max-age"] == "0"


async def test_the_login_cookie_is_lax_short_lived_and_binds_the_browser(client):
    started = await client.get("/auth/login", params={"provider": "entra"})
    cookie = cookie_attributes(started, LOGIN_COOKIE)
    assert cookie["samesite"].lower() == "lax"
    assert ("httponly" in cookie, "secure" in cookie) == (True, True)
    assert cookie["max-age"] == "600"


async def test_a_google_workspace_person_signs_in_by_domain(client, idp, factory, sessionmaker):
    await factory.grant("viewer", "all", "domain", "example.org")
    answer = await sign_in(client, idp, provider="google", hd="example.org")
    assert answer.status_code == 200
    (row,) = await _sessions(sessionmaker)
    assert row.issuer == GOOGLE_ISSUER
    assert set(row.principals) == {"email:person@example.org", "domain:example.org"}


async def test_a_google_account_outside_the_workspace_gets_no_domain(
    client, idp, factory, sessionmaker
):
    await factory.grant("viewer", "all", "domain", "example.org")
    answer = await sign_in(client, idp, provider="google")  # no hd claim
    assert answer.status_code == 403
    assert await _sessions(sessionmaker) == []


async def test_google_groups_become_principals(client, idp, factory, google_groups, sessionmaker):
    google_groups.groups["person@example.org"] = {"Operators@Example.org"}
    await factory.grant("operator", "all", "google_group", "operators@example.org")
    assert (await sign_in(client, idp, provider="google")).status_code == 200
    (row,) = await _sessions(sessionmaker)
    assert "google_group:operators@example.org" in row.principals


async def test_entra_group_overage_is_resolved_through_graph(
    client, idp, operators, graph, sessionmaker
):
    graph.groups["00000000-0000-4000-8000-0000000000a1"] = {GROUPS["operator"].upper()}
    answer = await sign_in(client, idp, groups=None, hasgroups=True)
    assert answer.status_code == 200
    (row,) = await _sessions(sessionmaker)
    assert row.principals == [f"entra_group:{GROUPS['operator']}"]


async def test_a_directory_outage_is_a_retryable_refusal(client, idp, operators, graph):
    graph.failing = True
    answer = await sign_in(client, idp, groups=None, hasgroups=True)
    assert answer.status_code == 503


async def test_the_request_uses_pkce_s256_and_asks_for_no_refresh_token(client, idp, operators):
    started = await client.get("/auth/login", params={"provider": "entra"})
    location = httpx.URL(started.headers["location"])
    params = dict(location.params)
    assert location.host == "login.microsoftonline.com"
    assert location.path.endswith("/oauth2/v2.0/authorize")
    assert params["response_type"] == "code"
    assert params["client_id"] == ENTRA_CLIENT
    assert params["redirect_uri"] == "https://console.test/auth/callback"
    assert params["scope"] == "openid email profile"
    assert params["code_challenge_method"] == "S256"
    assert len(params["state"]) == 43 and len(params["nonce"]) == 43
    back = await client.get(idp.authorize(str(location), groups=[GROUPS["operator"]]))
    assert back.status_code == 200
    (exchange,) = idp.exchanges
    verifier = exchange["code_verifier"]
    assert 43 <= len(verifier) <= 128
    digest = hashlib.sha256(verifier.encode()).digest()
    assert base64.urlsafe_b64encode(digest).rstrip(b"=").decode() == params["code_challenge"]


async def test_no_token_code_or_verifier_is_kept_after_sign_in(
    client, idp, operators, engine, sessionmaker
):
    started = await client.get("/auth/login", params={"provider": "entra"})
    callback = idp.authorize(started.headers["location"], groups=[GROUPS["operator"]])
    code = dict(httpx.URL(callback).params)["code"]
    assert (await client.get(callback)).status_code == 200
    stored = await all_rows_text(engine)
    assert len(idp.issued) == 3  # ID, access and refresh token
    for secret in [*idp.issued, code, idp.exchanges[0]["code_verifier"]]:
        assert secret not in stored
    async with sessionmaker() as session:
        assert (await session.scalars(select(LoginAttempt))).all() == []


async def test_signing_in_is_audited(client, idp, operators, sessionmaker):
    await sign_in(client, idp, groups=[GROUPS["operator"]])
    async with sessionmaker() as session:
        (entry,) = (await session.scalars(select(AuditEntry))).all()
    assert entry.action == "sign_in"
    assert entry.actor == f"person@example.org ({ENTRA_ISSUER} entra-person-1)"


# --- refusals ---------------------------------------------------------------------------


async def test_a_person_with_no_grant_gets_no_session(client, idp, sessionmaker):
    answer = await sign_in(client, idp, groups=[GROUPS["viewer"]])
    assert answer.status_code == 403
    assert cookie_attributes(answer, SESSION_COOKIE) is None
    assert await _sessions(sessionmaker) == []
    async with sessionmaker() as session:
        (entry,) = (await session.scalars(select(AuditEntry))).all()
    assert (entry.action, entry.outcome) == ("sign_in.refused", "no_access")


async def test_a_console_admin_without_a_leader_role_may_sign_in(client, idp, factory):
    await factory.console_admin("entra_group", GROUPS["console"])
    assert (await sign_in(client, idp, groups=[GROUPS["console"]])).status_code == 200


async def test_a_callback_is_single_use(client, idp, operators, sessionmaker):
    started = await client.get("/auth/login", params={"provider": "entra"})
    callback = idp.authorize(started.headers["location"], groups=[GROUPS["operator"]])
    login_cookie = client.cookies.get(LOGIN_COOKIE)
    assert (await client.get(callback)).status_code == 200
    client.cookies.set(LOGIN_COOKIE, login_cookie, domain="console.test", path="/")
    replay = await client.get(callback)
    assert replay.status_code == 400
    assert len(await _sessions(sessionmaker)) == 1


async def test_a_callback_in_another_browser_is_refused_and_burns_the_attempt(
    client, new_client, idp, operators, sessionmaker
):
    started = await client.get("/auth/login", params={"provider": "entra"})
    callback = idp.authorize(started.headers["location"], groups=[GROUPS["operator"]])
    elsewhere = new_client()
    assert (await elsewhere.get(callback)).status_code == 400
    assert (await client.get(callback)).status_code == 400
    assert await _sessions(sessionmaker) == []


@pytest.mark.parametrize(
    "query",
    [
        "code=abc&state=" + "A" * 43,
        "code=abc&state=not-a-state",
        "code=abc",
        "state=",
        "",
    ],
)
async def test_a_forged_or_missing_state_is_refused(client, idp, operators, query, sessionmaker):
    await client.get("/auth/login", params={"provider": "entra"})
    assert (await client.get(f"/auth/callback?{query}")).status_code == 400
    assert await _sessions(sessionmaker) == []


async def test_two_state_values_are_refused(client, idp, operators):
    started = await client.get("/auth/login", params={"provider": "entra"})
    callback = idp.authorize(started.headers["location"], groups=[GROUPS["operator"]])
    assert (await client.get(callback + "&state=" + "B" * 43)).status_code == 400


async def test_an_expired_attempt_is_refused(client, idp, operators, sessionmaker):
    started = await client.get("/auth/login", params={"provider": "entra"})
    async with sessionmaker() as session:
        await session.execute(update(LoginAttempt).values(expires_at=utcnow()))
        await session.commit()
    callback = idp.authorize(started.headers["location"], groups=[GROUPS["operator"]])
    assert (await client.get(callback)).status_code == 400


async def test_a_token_for_another_nonce_is_refused(client, idp, operators, sessionmaker):
    answer = await sign_in(client, idp, groups=[GROUPS["operator"]], nonce="attacker-nonce")
    assert answer.status_code == 401
    assert await _sessions(sessionmaker) == []


@pytest.mark.parametrize(
    "claims",
    [{"aud": "another-client"}, {"tid": "ffffffff-0000-4000-8000-000000000000"}],
    ids=["wrong-audience", "other-tenant"],
)
async def test_a_token_failing_the_leaders_checks_is_refused(client, idp, operators, claims):
    answer = await sign_in(client, idp, groups=[GROUPS["operator"]], **claims)
    assert answer.status_code == 401


async def test_a_refused_code_exchange_makes_no_session(client, idp, operators, sessionmaker):
    idp.token_status = 400
    assert (await sign_in(client, idp, groups=[GROUPS["operator"]])).status_code == 502
    assert await _sessions(sessionmaker) == []


async def test_a_provider_error_is_shown_and_burns_the_attempt(client, idp, sessionmaker):
    started = await client.get("/auth/login", params={"provider": "entra"})
    state = dict(httpx.URL(started.headers["location"]).params)["state"]
    answer = await client.get(f"/auth/callback?error=access_denied&state={state}")
    assert answer.status_code == 400
    assert "<script" not in answer.text
    async with sessionmaker() as session:
        assert (await session.scalars(select(LoginAttempt))).all() == []


async def test_an_unknown_provider_is_not_found(client):
    assert (await client.get("/auth/login", params={"provider": "okta"})).status_code == 404


async def test_the_configured_providers_are_listed(client):
    assert (await client.get("/auth/providers")).json() == {"providers": ["entra", "google"]}


# --- redirects and fixation -------------------------------------------------------------


@pytest.mark.parametrize(
    "return_to", ["/leaders/eu-1?tab=jobs", "/", "/fleet#filter=env%3Dprod"]
)
async def test_a_local_return_url_is_kept(client, idp, operators, return_to):
    answer = await sign_in(client, idp, return_to=return_to, groups=[GROUPS["operator"]])
    assert _refresh_target(answer) == return_to


@pytest.mark.parametrize(
    "return_to",
    [
        "https://evil.example/",
        "//evil.example",
        "/\\evil.example",
        "javascript:alert(1)",
        "/\t/evil.example",
        "/x'><script>alert(1)</script>",
        "evil.example",
        "",
        "/" + "x" * 600,
    ],
)
async def test_any_other_return_url_lands_on_the_root(client, idp, operators, return_to):
    answer = await sign_in(client, idp, return_to=return_to, groups=[GROUPS["operator"]])
    assert answer.status_code == 200
    assert _refresh_target(answer) == "/"
    assert "evil.example" not in answer.text


async def test_a_return_url_added_to_the_callback_is_ignored(client, idp, operators):
    started = await client.get("/auth/login", params={"provider": "entra"})
    callback = idp.authorize(started.headers["location"], groups=[GROUPS["operator"]])
    answer = await client.get(callback + "&return_to=https://evil.example/")
    assert _refresh_target(answer) == "/"


async def test_signing_in_never_adopts_the_cookie_the_browser_brought(
    client, new_client, idp, operators, factory, sessionmaker
):
    victim_planted = "P" * 43
    client.cookies.set(SESSION_COOKIE, victim_planted, domain="console.test", path="/")
    await sign_in(client, idp, groups=[GROUPS["operator"]])
    assert client.cookies.get(SESSION_COOKIE) != victim_planted

    other = new_client()
    await factory.person(other, subject="someone-else")
    existing = other.cookies.get(SESSION_COOKIE)
    await sign_in(other, idp, groups=[GROUPS["operator"]])
    fresh = other.cookies.get(SESSION_COOKIE)
    assert fresh != existing
    stale = new_client()
    stale.cookies.set(SESSION_COOKIE, existing, domain="console.test", path="/")
    assert (await stale.get("/api/session")).status_code == 401
    assert len(await _sessions(sessionmaker)) == 2
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m uv run pytest packages/console/tests/test_sign_in.py -v`
Expected: FAIL with `TypeError: create_app() got an unexpected keyword argument 'fetch'` (raised by the `app` fixture).

- [ ] **Step 4: Write the OIDC flow module**

Create `packages/console/src/swarmscribe_console/oidc.py`:

```python
"""Browser sign-in (fleet console spec 5.2): OIDC authorization code with PKCE against Entra
ID and Google, as a confidential web client.

ID tokens are validated by the leader's TokenVerifier (leader spec section 10's rules). This
module adds the browser half: the authorization URL with an S256 code challenge, a nonce, and
a single-use state bound to the browser that started the sign-in (by a cookie whose SHA-256
is stored with the state); and the code exchange. The ID token lives only for the duration
of the callback; the access and refresh tokens the provider returns are dropped unread."""

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx
from pydantic import SecretStr
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from swarmscribe_leader.auth.oidc import (
    ENTRA_AUTHORITY,
    GOOGLE_DISCOVERY,
    GOOGLE_ISSUERS,
    Provider,
    ProviderName,
)
from swarmscribe_leader.auth.secrets import hash_secret, new_secret

from .config import Settings
from .db.models import LoginAttempt
from .sessions import is_token

SCOPE = "openid email profile"  # no offline_access: no refresh token is asked for
CALLBACK_PATH = "/auth/callback"
GOOGLE_AUTHORIZE = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
EXCHANGE_TIMEOUT_SECONDS = 10.0
MAX_CODE_CHARS = 4096


@dataclass(frozen=True)
class WebProvider:
    name: ProviderName
    client_id: str
    client_secret: SecretStr = field(repr=False)
    authorization_endpoint: str
    token_endpoint: str
    verification: Provider


def web_providers(settings: Settings) -> dict[str, WebProvider]:
    found: dict[str, WebProvider] = {}
    if settings.entra_client_id and settings.entra_tenant_id and settings.entra_client_secret:
        tenant = settings.entra_tenant_id
        issuer = f"{ENTRA_AUTHORITY}/{tenant}/v2.0"
        base = f"{ENTRA_AUTHORITY}/{tenant}/oauth2/v2.0"
        found["entra"] = WebProvider(
            name="entra",
            client_id=settings.entra_client_id,
            client_secret=settings.entra_client_secret,
            authorization_endpoint=f"{base}/authorize",
            token_endpoint=f"{base}/token",
            verification=Provider(
                name="entra",
                issuers=(issuer,),
                client_id=settings.entra_client_id,
                discovery_url=f"{issuer}/.well-known/openid-configuration",
                tenant_id=tenant,
            ),
        )
    if settings.google_client_id and settings.google_client_secret:
        found["google"] = WebProvider(
            name="google",
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
            authorization_endpoint=GOOGLE_AUTHORIZE,
            token_endpoint=GOOGLE_TOKEN,
            verification=Provider(
                name="google",
                issuers=GOOGLE_ISSUERS,
                client_id=settings.google_client_id,
                discovery_url=GOOGLE_DISCOVERY,
                hosted_domain=settings.google_hosted_domain,
            ),
        )
    return found


def code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def authorization_url(
    provider: WebProvider, *, redirect_uri: str, state: str, nonce: str, verifier: str
) -> str:
    params = {
        "client_id": provider.client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": SCOPE,
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge(verifier),
        "code_challenge_method": "S256",
    }
    if provider.verification.hosted_domain:
        params["hd"] = provider.verification.hosted_domain  # a hint; the token is checked
    return f"{provider.authorization_endpoint}?{urlencode(params)}"


async def begin_sign_in(
    session: AsyncSession,
    provider: WebProvider,
    *,
    redirect_uri: str,
    return_to: str,
    now: datetime,
    ttl: timedelta,
) -> tuple[str, str]:
    """Record a pending sign-in. Returns the provider URL to send the browser to and the
    secret for the browser's login cookie. The caller commits."""
    state, browser, nonce = new_secret(), new_secret(), new_secret()
    verifier = secrets.token_urlsafe(64)  # 86 characters, inside PKCE's 43 to 128
    session.add(
        LoginAttempt(
            state_hash=hash_secret(state),
            browser_hash=hash_secret(browser),
            provider=provider.name,
            nonce=nonce,
            code_verifier=verifier,
            return_to=return_to,
            expires_at=now + ttl,
        )
    )
    url = authorization_url(
        provider, redirect_uri=redirect_uri, state=state, nonce=nonce, verifier=verifier
    )
    return url, browser


@dataclass(frozen=True)
class SignInAttempt:
    provider: str
    nonce: str = field(repr=False)
    code_verifier: str = field(repr=False)
    return_to: str


class SignInFailed(Exception):
    """A callback that belongs to no sign-in this browser started, or to an expired one.
    The message is shown to the person."""


class CodeExchangeFailed(Exception):
    """The provider would not exchange the code. The message names no secret."""


async def finish_sign_in(
    session: AsyncSession, *, state: str | None, browser_secret: str | None, now: datetime
) -> SignInAttempt:
    """Take (delete) the pending sign-in `state` names. It is gone afterwards whatever the
    outcome, so a state can never be used twice; the caller commits even on failure."""
    if not is_token(state):
        raise SignInFailed("this sign-in link is not valid; sign in again")
    row = (
        await session.execute(
            delete(LoginAttempt)
            .where(LoginAttempt.state_hash == hash_secret(state))
            .returning(
                LoginAttempt.browser_hash,
                LoginAttempt.provider,
                LoginAttempt.nonce,
                LoginAttempt.code_verifier,
                LoginAttempt.return_to,
                LoginAttempt.expires_at,
            )
        )
    ).one_or_none()
    if row is None:
        raise SignInFailed("this sign-in is unknown or was already used; sign in again")
    if now >= row.expires_at:
        raise SignInFailed("this sign-in took too long; sign in again")
    if not is_token(browser_secret) or not hmac.compare_digest(
        row.browser_hash, hash_secret(browser_secret)
    ):
        raise SignInFailed("this sign-in was started in another browser; sign in again")
    return SignInAttempt(
        provider=row.provider,
        nonce=row.nonce,
        code_verifier=row.code_verifier,
        return_to=row.return_to,
    )


async def exchange_code(
    provider: WebProvider,
    *,
    code: str,
    verifier: str,
    redirect_uri: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    """The ID token for `code`. The access and refresh tokens in the answer are not read."""
    if not code or len(code) > MAX_CODE_CHARS:
        raise CodeExchangeFailed(f"{provider.name} sent no usable authorization code")
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": provider.client_id,
        "client_secret": provider.client_secret.get_secret_value(),
        "code_verifier": verifier,
    }
    try:
        async with httpx.AsyncClient(
            timeout=EXCHANGE_TIMEOUT_SECONDS, transport=transport, follow_redirects=False
        ) as client:
            response = await client.post(
                provider.token_endpoint, data=form, headers={"Accept": "application/json"}
            )
            body = response.json() if response.status_code == 200 else None
    except (httpx.HTTPError, ValueError) as exc:
        raise CodeExchangeFailed(
            f"{provider.name}'s token endpoint could not be used: {type(exc).__name__}"
        ) from None
    if response.status_code != 200:
        raise CodeExchangeFailed(
            f"{provider.name} refused the authorization code (HTTP {response.status_code})"
        )
    token = body.get("id_token") if isinstance(body, dict) else None
    if not isinstance(token, str) or not token:
        raise CodeExchangeFailed(f"{provider.name} returned no ID token")
    return token
```

- [ ] **Step 5: Write the principals module**

Create `packages/console/src/swarmscribe_console/principals.py`:

```python
"""A signed-in person's principals: what grants and console admins name (fleet console spec
5.1), computed once at sign-in with the leader's rules (leader spec section 10) and kept in
the session. A change of group membership therefore applies at the next sign-in.

- Entra ID: the token's group object ids, or Microsoft Graph on overage.
- Google: Google Groups through Cloud Identity (when a service account is configured), plus
  the email and its domain under Workspace rules — a domain only when the token's hd equals
  it; an email when hd equals its domain, or for gmail.com/googlemail.com addresses.
Entra ID emails are not principals; the leader does not map them either."""

import logging
import uuid
from typing import Any

from swarmscribe_leader.auth.oidc import Identity
from swarmscribe_leader.auth.roles import (
    CONSUMER_GOOGLE_DOMAINS,
    GoogleGroupsClient,
    GraphClient,
)

logger = logging.getLogger(__name__)


def _hosted_domain(claims: dict[str, Any]) -> str | None:
    hd = claims.get("hd")
    if isinstance(hd, str) and hd and hd.isascii():
        return hd.lower()
    return None


def _has_group_overage(claims: dict[str, Any]) -> bool:
    names = claims.get("_claim_names")
    if isinstance(names, dict) and "groups" in names:
        return True
    return claims.get("hasgroups") in (True, "true")


def _entra_group(value: object) -> str | None:
    try:
        return f"entra_group:{uuid.UUID(str(value))}"
    except ValueError:
        return None


async def principals_for(
    identity: Identity,
    *,
    graph: GraphClient | None,
    google_groups: GoogleGroupsClient | None,
) -> frozenset[str]:
    """Raises the leader's RoleLookupFailed when a directory cannot be asked."""
    found: set[str] = set()
    claims = identity.claims
    if identity.provider == "entra":
        if _has_group_overage(claims):
            oid = claims.get("oid")
            if graph is None or not oid:
                logger.warning("an Entra ID sign-in has too many groups and no oid; no groups")
                groups: set[str] = set()
            else:
                groups = await graph.member_object_ids(str(oid))
        else:
            groups = {str(group) for group in claims.get("groups") or []}
        found |= {p for p in (_entra_group(group) for group in groups) if p}
        return frozenset(found)

    email = identity.email
    if email and google_groups is not None:
        found |= {
            f"google_group:{group.lower()}" for group in await google_groups.group_emails(email)
        }
    if email and email.isascii():
        address = email.lower()
        local, at, domain = address.partition("@")
        if local and at and domain and "@" not in domain:
            hosted = _hosted_domain(claims)
            if hosted == domain or domain in CONSUMER_GOOGLE_DOMAINS:
                found.add(f"email:{address}")
            if hosted == domain:
                found.add(f"domain:{domain}")
    return frozenset(found)
```

- [ ] **Step 6: Write the sign-in routes**

Create `packages/console/src/swarmscribe_console/api/auth.py`:

```python
"""Sign-in routes: /auth/providers, /auth/login, /auth/callback.

The callback answers with a small HTML page that refreshes to the return URL, not with a
redirect: the browser's next navigation is then same-site, so the SameSite=Strict session
cookie is sent with it. Pages carry no script (CSP) and echo nothing the provider sent."""

import hmac
import html
import logging
import re
from datetime import timedelta

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from swarmscribe_leader.auth.oidc import MetadataUnavailable
from swarmscribe_leader.auth.roles import RoleLookupFailed
from swarmscribe_leader.clock import utcnow
from swarmscribe_leader.errors import Unauthorized

from .. import audit, grants
from ..errors import NotFound
from ..oidc import (
    CALLBACK_PATH,
    CodeExchangeFailed,
    SignInFailed,
    begin_sign_in,
    exchange_code,
    finish_sign_in,
)
from ..principals import principals_for
from ..sessions import (
    LOGIN_COOKIE,
    SESSION_COOKIE,
    clear_login_cookie,
    create_session,
    end_session,
    prune_expired,
    set_login_cookie,
    set_session_cookie,
)
from .deps import settings_of

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth")

MAX_RETURN_TO = 512
_RETURN_TO = re.compile(r"/[A-Za-z0-9\-._~/?=&%#:+,@]*")


def safe_return_to(value: str | None) -> str:
    """A path on this console, or "/". Never a scheme, a host or a protocol-relative URL."""
    if (
        not value
        or len(value) > MAX_RETURN_TO
        or not _RETURN_TO.fullmatch(value)
        or value.startswith("//")
    ):
        return "/"
    return value


_FAILED = "Sign-in failed"
_UNAVAILABLE = "Sign-in unavailable"
_TRY_AGAIN = "Your sign-in could not be verified. Sign in again."


def _page(
    status: int, title: str, message: str, *, refresh_to: str | None = None
) -> HTMLResponse:
    meta = ""
    if refresh_to is not None:
        target = html.escape(refresh_to, quote=True)
        meta = f'<meta http-equiv="refresh" content="0;url={target}">'
    link = html.escape(refresh_to or "/", quote=True)
    body = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f"{meta}<title>{html.escape(title)}</title></head>"
        f"<body><main><h1>{html.escape(title)}</h1><p>{html.escape(message)}</p>"
        f'<p><a href="{link}">Continue</a></p></main></body></html>'
    )
    return HTMLResponse(body, status_code=status)


class _Refused(Exception):
    def __init__(self, status: int, title: str, message: str):
        super().__init__(message)
        self.status, self.title, self.message = status, title, message


@router.get("/providers")
async def providers(request: Request) -> dict[str, list[str]]:
    return {"providers": sorted(request.app.state.web_providers)}


@router.get("/login")
async def login(request: Request, provider: str, return_to: str | None = None) -> Response:
    web = request.app.state.web_providers.get(provider)
    if web is None:
        raise NotFound("no such sign-in provider", code="unknown_provider")
    settings = settings_of(request)
    async with request.app.state.sessionmaker() as session:
        url, browser = await begin_sign_in(
            session,
            web,
            redirect_uri=settings.public_url + CALLBACK_PATH,
            return_to=safe_return_to(return_to),
            now=utcnow(),
            ttl=timedelta(seconds=settings.login_attempt_seconds),
        )
        await session.commit()
    response = RedirectResponse(url, status_code=302)
    set_login_cookie(response, browser, settings.login_attempt_seconds)
    return response


@router.get("/callback")  # CALLBACK_PATH
async def callback(request: Request) -> Response:
    try:
        response = await _complete(request)
    except _Refused as refusal:
        response = _page(refusal.status, refusal.title, refusal.message)
    clear_login_cookie(response)
    return response


async def _complete(request: Request) -> Response:
    settings = settings_of(request)
    state = request.app.state
    params = request.query_params
    now = utcnow()
    states = params.getlist("state")
    async with state.sessionmaker() as session:
        try:
            attempt = await finish_sign_in(
                session,
                state=states[0] if len(states) == 1 else None,
                browser_secret=request.cookies.get(LOGIN_COOKIE),
                now=now,
            )
        except SignInFailed as exc:
            raise _Refused(400, _FAILED, str(exc)) from None
        finally:
            await session.commit()  # the state is single-use even when the callback fails

    web = state.web_providers.get(attempt.provider)
    if web is None:  # the provider was unconfigured between login and callback
        raise _Refused(400, _FAILED, "That sign-in provider is no longer offered.")
    if "error" in params:
        raise _Refused(400, "Sign-in was not completed", "You were not signed in. Sign in again.")
    codes = params.getlist("code")
    if len(codes) != 1:
        raise _Refused(400, _FAILED, "The sign-in answer was incomplete. Sign in again.")
    redirect_uri = settings.public_url + CALLBACK_PATH
    try:
        id_token = await exchange_code(
            web,
            code=codes[0],
            verifier=attempt.code_verifier,
            redirect_uri=redirect_uri,
            transport=state.idp_transport,
        )
    except CodeExchangeFailed as exc:
        logger.warning("a sign-in's code exchange failed: %s", exc)
        raise _Refused(502, _FAILED, "The identity provider did not answer. Try again.") from None
    try:
        identity = await state.verifier.verify(id_token)
    except Unauthorized as exc:
        logger.warning("a sign-in's ID token was refused: %s", exc.message)
        raise _Refused(401, _FAILED, _TRY_AGAIN) from None
    except MetadataUnavailable:
        raise _Refused(503, _UNAVAILABLE, "Sign-in cannot be checked now. Try again.") from None
    nonce = identity.claims.get("nonce")
    if (
        identity.provider != attempt.provider
        or not isinstance(nonce, str)
        or not hmac.compare_digest(nonce.encode(), attempt.nonce.encode())
    ):
        logger.warning("a sign-in's ID token did not belong to its request")
        raise _Refused(401, _FAILED, _TRY_AGAIN)
    try:
        principals = await principals_for(
            identity, graph=state.graph, google_groups=state.google_groups
        )
    except RoleLookupFailed as exc:
        logger.warning("a sign-in's groups could not be read: %s", exc)
        raise _Refused(503, _UNAVAILABLE, "Your groups cannot be read now. Try again.") from None

    idle = timedelta(seconds=settings.session_idle_seconds)
    async with state.sessionmaker() as session:
        if not await grants.has_any_access(session, principals):
            audit.record(
                session, actor=identity.actor, action="sign_in.refused", outcome="no_access"
            )
            await session.commit()
            raise _Refused(
                403,
                "No access",
                "You signed in, but you hold no role in this console. "
                "Ask a console administrator for one.",
            )
        # Never adopt a session id the browser brought with it (session fixation).
        await end_session(session, request.cookies.get(SESSION_COOKIE))
        await prune_expired(session, now=now, idle=idle)
        session_id = await create_session(
            session,
            provider=identity.provider,
            issuer=identity.issuer,
            subject=identity.subject,
            email=identity.email,
            principals=principals,
            now=now,
            lifetime=timedelta(seconds=settings.session_lifetime_seconds),
        )
        audit.record(
            session,
            actor=identity.actor,
            action="sign_in",
            detail={"provider": identity.provider},
        )
        await session.commit()
    response = _page(200, "Signed in", "Continuing to the console.", refresh_to=attempt.return_to)
    set_session_cookie(response, session_id, settings.session_lifetime_seconds)
    return response
```

- [ ] **Step 7: Wire sign-in into the app**

Replace `packages/console/src/swarmscribe_console/app.py` with:

```python
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from swarmscribe_leader.api.body_limit import BodyLimit
from swarmscribe_leader.auth.oidc import Fetch, TokenVerifier, http_fetch
from swarmscribe_leader.auth.roles import (
    GoogleCloudIdentity,
    GoogleGroupsClient,
    GraphClient,
    MicrosoftGraph,
)
from swarmscribe_leader.db.session import make_engine, make_sessionmaker

from .api import auth as auth_api
from .api import errors as api_errors
from .api import session as session_api
from .api.security import SecurityHeaders
from .config import Settings
from .crypto import ConsoleKeys
from .oidc import web_providers


def _graph(settings: Settings) -> GraphClient | None:
    if settings.entra_client_id and settings.entra_tenant_id and settings.entra_client_secret:
        return MicrosoftGraph(
            settings.entra_tenant_id, settings.entra_client_id, settings.entra_client_secret
        )
    return None


def _google_groups(settings: Settings) -> GoogleGroupsClient | None:
    key = settings.google_service_account_key()
    return GoogleCloudIdentity(key) if key is not None else None


def create_app(
    settings: Settings,
    *,
    fetch: Fetch | None = None,
    idp_transport: httpx.AsyncBaseTransport | None = None,
    graph: GraphClient | None = None,
    google_groups: GoogleGroupsClient | None = None,
) -> FastAPI:
    """`fetch`, `idp_transport`, `graph` and `google_groups` replace the identity providers
    and directories in tests; real ones are built from the settings otherwise."""
    engine = make_engine(settings.database_url.get_secret_value())
    sessionmaker = make_sessionmaker(engine)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(
        title="SwarmScribe console",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    providers = web_providers(settings)
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = sessionmaker
    app.state.keys = ConsoleKeys(settings.key_bytes())
    app.state.web_providers = providers
    app.state.verifier = TokenVerifier(
        [provider.verification for provider in providers.values()], fetch=fetch or http_fetch
    )
    app.state.idp_transport = idp_transport
    app.state.graph = graph if graph is not None else _graph(settings)
    app.state.google_groups = (
        google_groups if google_groups is not None else _google_groups(settings)
    )
    api_errors.install(app)
    app.add_middleware(BodyLimit)
    app.add_middleware(SecurityHeaders)  # outermost: also covers BodyLimit's 413
    app.include_router(auth_api.router)
    app.include_router(session_api.router)
    return app
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `python -m uv run pytest packages/console/tests -v`
Expected: PASS (Tasks 1–5, including Task 4's tests with the new `app` fixture).

Run: `python -m uv run ruff check packages/console`
Expected: no findings.

- [ ] **Step 9: Commit**

```bash
git add packages/console/src/swarmscribe_console/oidc.py packages/console/src/swarmscribe_console/principals.py packages/console/src/swarmscribe_console/api/auth.py packages/console/src/swarmscribe_console/app.py packages/console/tests/console_testkit.py packages/console/tests/conftest.py packages/console/tests/test_sign_in.py
git commit -m "Console: browser sign-in with PKCE, principals at sign-in, fresh sessions only

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Leader registry — add, edit, rotate, remove; credentials sealed

**Files:**
- Create: `packages/console/src/swarmscribe_console/leaders.py`, `api/models.py`, `api/admin.py`
- Modify: `packages/console/src/swarmscribe_console/app.py` (include the admin router)
- Modify: `packages/console/tests/conftest.py` (`Factory.leader`, `ADMIN_PRINCIPAL`)
- Test: `packages/console/tests/test_registry.py`

**Interfaces:**
- Consumes: `Leader` (Task 2); `ConsoleKeys` (Task 1); `grants.LEADER_NAME`, `LABEL_KEY`, `LABEL_VALUE`, `is_console_admin` (Task 3); `api.deps.Person`, `Session`, `keys_of` (Task 4); `audit`.
- Produces:
  - `leaders.validate_name(name) -> str`, `validate_base_url(value) -> str` (normalised), `validate_labels(labels) -> dict[str, str]`, `validate_credential(value) -> str` — all raise `Invalid` (codes `invalid_name`, `invalid_url`, `invalid_labels`, `invalid_credential`).
  - `async leaders.find_leader(session, name, *, lock=False) -> Leader` (raises `NotFound(code="leader_not_found")`).
  - `async leaders.list_leaders(session) -> list[Leader]` (by name).
  - `async leaders.add_leader(session, keys, *, name, base_url, labels, credential, enabled, actor) -> Leader`.
  - `async leaders.edit_leader(session, name, *, base_url=None, labels=None, enabled=None, actor) -> Leader`.
  - `async leaders.replace_credential(session, keys, name, credential, *, now, actor) -> Leader` (clears `credential_revoked_at`, `consecutive_failures`, `last_error`, `last_polled_at`).
  - `async leaders.remove_leader(session, name, *, actor) -> None`.
  - `leaders.leader_view(leader) -> dict[str, Any]` (no credential).
  - Audit actions `leader.add`, `leader.edit`, `leader.credential_replace`, `leader.remove` (`leader` = name).
  - `api.models.LeaderIn`, `LeaderEdit`, `CredentialIn`, `LeaderOut`.
  - `api.admin.router` (prefix `/api/admin`), dependency alias `api.admin.ConsoleAdministrator`.
  - `console_testkit.ADMIN_PRINCIPAL = "email:admin@example.org"`, `console_testkit.CREDENTIAL` (a 43-character credential); `Factory.leader(name="eu-1", *, base_url=None, labels=None, credential=CREDENTIAL, enabled=True) -> Leader`.

- [ ] **Step 1: Add the registry test helpers**

Append to `packages/console/tests/console_testkit.py`:

```python
ADMIN_PRINCIPAL = "email:admin@example.org"
CREDENTIAL = "c" * 20 + "_-" + "D" * 21  # 43 URL-safe characters, as C1 makes them
```

In `packages/console/tests/conftest.py`, add `CREDENTIAL` to the `console_testkit` import list, add `Leader` to the `swarmscribe_console.db.models` import, and add this method to `class Factory` (after `console_admin`):

```python
    async def leader(
        self,
        name="eu-1",
        *,
        base_url=None,
        labels=None,
        credential=CREDENTIAL,
        enabled=True,
    ) -> Leader:
        return await self._save(
            Leader(
                name=name,
                base_url=base_url or f"https://{name}.leaders.example",
                labels=labels or {},
                enabled=enabled,
                credential=self.keys.seal_credential(name, credential),
                credential_updated_by="t",
                added_by="t",
            )
        )
```

- [ ] **Step 2: Write the failing tests**

Create `packages/console/tests/test_registry.py`:

```python
import pytest
from console_testkit import ADMIN_PRINCIPAL, CREDENTIAL
from sqlalchemy import select, text, update
from swarmscribe_console.crypto import CredentialUnreadable
from swarmscribe_console.db.models import AuditEntry, Leader
from swarmscribe_console.errors import Invalid
from swarmscribe_console.leaders import validate_base_url

NEW_CREDENTIAL = "N" * 43


@pytest.fixture
async def admin(client, factory):
    await factory.console_admin("email", "admin@example.org")
    csrf = await factory.person(client, principals={ADMIN_PRINCIPAL}, email="admin@example.org")
    client.headers["X-CSRF-Token"] = csrf
    return client


def _body(**overrides):
    body = {
        "name": "eu-1",
        "base_url": "https://eu-1.leaders.example",
        "labels": {"env": "prod", "region": "eu=west"},
        "credential": CREDENTIAL,
    }
    body.update(overrides)
    return body


async def _leader(sessionmaker, name="eu-1") -> Leader:
    async with sessionmaker() as session:
        return (await session.scalars(select(Leader).where(Leader.name == name))).one()


async def _audit(sessionmaker) -> list[AuditEntry]:
    async with sessionmaker() as session:
        return list((await session.scalars(select(AuditEntry).order_by(AuditEntry.id))).all())


# --- adding -----------------------------------------------------------------------------


async def test_a_console_admin_registers_a_leader(admin, sessionmaker):
    answer = await admin.post("/api/admin/leaders", json=_body())
    assert answer.status_code == 201
    body = answer.json()
    assert body["name"] == "eu-1"
    assert body["base_url"] == "https://eu-1.leaders.example"
    assert body["labels"] == {"env": "prod", "region": "eu=west"}
    assert body["enabled"] is True
    assert body["added_by"].startswith("admin@example.org (https://login.microsoftonline.com/")
    assert "credential" not in body
    assert CREDENTIAL not in answer.text
    listed = await admin.get("/api/admin/leaders")
    assert [leader["name"] for leader in listed.json()] == ["eu-1"]
    assert CREDENTIAL not in listed.text
    (entry,) = await _audit(sessionmaker)
    assert (entry.action, entry.leader, entry.outcome) == ("leader.add", "eu-1", "ok")
    assert CREDENTIAL not in str(entry.detail)


async def test_the_credential_is_encrypted_at_rest(admin, sessionmaker, engine, keys):
    await admin.post("/api/admin/leaders", json=_body())
    async with engine.connect() as conn:
        raw = await conn.scalar(text("select credential from leaders where name = 'eu-1'"))
    assert CREDENTIAL.encode() not in raw
    assert keys.open_credential("eu-1", raw) == CREDENTIAL


async def test_a_sealed_credential_moved_to_another_leader_does_not_open(
    admin, sessionmaker, keys
):
    await admin.post("/api/admin/leaders", json=_body())
    await admin.post("/api/admin/leaders", json=_body(name="us-1", base_url="https://us-1.x.org"))
    eu = await _leader(sessionmaker, "eu-1")
    async with sessionmaker() as session:
        await session.execute(
            update(Leader).where(Leader.name == "us-1").values(credential=eu.credential)
        )
        await session.commit()
    us = await _leader(sessionmaker, "us-1")
    with pytest.raises(CredentialUnreadable):
        keys.open_credential("us-1", us.credential)


@pytest.mark.parametrize("name", ["EU-1", "eu-1"])
async def test_leader_names_are_unique_ignoring_case(admin, name):
    await admin.post("/api/admin/leaders", json=_body())
    answer = await admin.post("/api/admin/leaders", json=_body(name=name))
    assert answer.status_code == 409
    assert answer.json()["code"] == "exists"


@pytest.mark.parametrize(
    "name", ["", "../eu", "a/b", ".hidden", "eu 1", "x" * 101, "eu%2F1", "café"]
)
async def test_odd_leader_names_are_refused(admin, name):
    answer = await admin.post("/api/admin/leaders", json=_body(name=name))
    assert answer.status_code == 422
    assert answer.json()["code"] == "invalid_name"


@pytest.mark.parametrize(
    "credential", ["short", "x" * 44, "x" * 42 + "!", "", "é" * 43]
)
async def test_a_credential_not_in_c1s_format_is_refused_without_echo(admin, credential):
    answer = await admin.post("/api/admin/leaders", json=_body(credential=credential))
    assert answer.status_code == 422
    assert answer.json()["code"] == "invalid_credential"
    if credential:
        assert credential not in answer.text


@pytest.mark.parametrize(
    "labels",
    [
        {"Env": "prod"},
        {"env": "has space"},
        {"env": ""},
        {"env=x": "prod"},
        {"env": "x" * 256},
        {f"k{i}": "v" for i in range(33)},
    ],
)
async def test_odd_labels_are_refused(admin, labels):
    answer = await admin.post("/api/admin/leaders", json=_body(labels=labels))
    assert answer.status_code == 422
    assert answer.json()["code"] == "invalid_labels"


# --- leader URLs ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "kept"),
    [
        ("https://leader.example.org", "https://leader.example.org"),
        ("https://Leader.Example.org/", "https://leader.example.org"),
        ("https://leader.example.org:443", "https://leader.example.org"),
        ("https://10.0.0.5:8443", "https://10.0.0.5:8443"),
        ("https://192.168.1.50", "https://192.168.1.50"),
        ("https://[fd00::5]", "https://[fd00::5]"),
        ("https://swarmscribe-leader", "https://swarmscribe-leader"),
        ("https://leader.example.org/swarmscribe/", "https://leader.example.org/swarmscribe"),
    ],
)
def test_https_leader_urls_are_normalised(given, kept):
    assert validate_base_url(given) == kept


@pytest.mark.parametrize(
    "bad",
    [
        "http://leader.example.org",
        "ftp://leader.example.org",
        "leader.example.org",
        "https://user:pw@leader.example.org",
        "https://user@leader.example.org",
        "https://leader.example.org?x=1",
        "https://leader.example.org#top",
        "https://127.0.0.1",
        "https://127.8.9.10",
        "https://localhost",
        "https://LOCALHOST.",
        "https://api.localhost",
        "https://169.254.169.254",
        "https://[fe80::1]",
        "https://[::1]",
        "https://[::ffff:127.0.0.1]",
        "https://0.0.0.0",
        "https://224.0.0.1",
        "https://240.0.0.1",
        "https://2130706433",
        "https://0x7f000001",
        "https://metadata.google.internal",
        "https://metadata",
        "https://leader.example.org/../admin",
        "https://leader.example.org/a/./b",
        "https://leader.example.org:0",
        "https://leader.example.org:99999",
        "https://leader.example.org:port",
        "https://lea der.example.org",
        "https://leader.exämple.org",
        "https://leader.example.org/\r\nX-Injected: 1",
        "https://",
        "https://" + "a" * 2000 + ".org",
    ],
)
def test_other_leader_urls_are_refused(bad):
    with pytest.raises(Invalid) as raised:
        validate_base_url(bad)
    assert raised.value.code == "invalid_url"


async def test_the_api_refuses_a_metadata_address(admin):
    answer = await admin.post(
        "/api/admin/leaders", json=_body(base_url="https://169.254.169.254")
    )
    assert answer.status_code == 422
    assert answer.json()["code"] == "invalid_url"


# --- editing, rotation, removal ---------------------------------------------------------


async def test_a_leader_is_edited_in_place(admin, sessionmaker):
    await admin.post("/api/admin/leaders", json=_body())
    answer = await admin.patch(
        "/api/admin/leaders/EU-1",
        json={"labels": {"env": "test"}, "enabled": False, "base_url": "https://eu.x.org/"},
    )
    assert answer.status_code == 200
    body = answer.json()
    assert (body["labels"], body["enabled"], body["base_url"]) == (
        {"env": "test"},
        False,
        "https://eu.x.org",
    )
    entry = (await _audit(sessionmaker))[-1]
    assert (entry.action, entry.leader) == ("leader.edit", "eu-1")
    assert entry.detail == {
        "labels": {"env": "test"},
        "enabled": False,
        "base_url": "https://eu.x.org",
    }


async def test_editing_refuses_unknown_fields_and_the_credential(admin):
    await admin.post("/api/admin/leaders", json=_body())
    for body in ({"name": "eu-2"}, {"credential": NEW_CREDENTIAL}):
        assert (await admin.patch("/api/admin/leaders/eu-1", json=body)).status_code == 422


async def test_rotation_replaces_the_credential_in_place(admin, sessionmaker, keys):
    await admin.post("/api/admin/leaders", json=_body())
    async with sessionmaker() as session:
        await session.execute(
            update(Leader).values(
                credential_revoked_at=text("now()"),
                consecutive_failures=5,
                last_error="credential_revoked",
                last_polled_at=text("now()"),
            )
        )
        await session.commit()
    answer = await admin.put(
        "/api/admin/leaders/eu-1/credential", json={"credential": NEW_CREDENTIAL}
    )
    assert answer.status_code == 200
    assert answer.json()["credential_revoked"] is False
    assert NEW_CREDENTIAL not in answer.text
    leader = await _leader(sessionmaker)
    assert keys.open_credential("eu-1", leader.credential) == NEW_CREDENTIAL
    assert (
        leader.credential_revoked_at,
        leader.consecutive_failures,
        leader.last_error,
        leader.last_polled_at,
    ) == (None, 0, None, None)
    assert leader.credential_updated_by.startswith("admin@example.org")
    entry = (await _audit(sessionmaker))[-1]
    assert (entry.action, entry.leader) == ("leader.credential_replace", "eu-1")
    assert NEW_CREDENTIAL not in str(entry.detail)


async def test_a_leader_is_removed(admin, sessionmaker):
    await admin.post("/api/admin/leaders", json=_body())
    assert (await admin.delete("/api/admin/leaders/eu-1")).status_code == 204
    assert (await admin.get("/api/admin/leaders")).json() == []
    assert (await _audit(sessionmaker))[-1].action == "leader.remove"
    assert (await admin.delete("/api/admin/leaders/eu-1")).status_code == 404


@pytest.mark.parametrize("name", ["us-9", "..", "a%2Fb"])
async def test_an_unknown_leader_is_not_found(admin, name):
    answer = await admin.patch(f"/api/admin/leaders/{name}", json={"enabled": False})
    assert answer.status_code == 404


# --- who may ----------------------------------------------------------------------------


async def test_a_leader_admin_who_is_not_a_console_admin_cannot_manage_the_registry(
    client, factory, sessionmaker
):
    await factory.grant("admin", "all", "email", "person@example.org")
    csrf = await factory.person(client, principals={"email:person@example.org"})
    client.headers["X-CSRF-Token"] = csrf
    calls = [
        client.get("/api/admin/leaders"),
        client.post("/api/admin/leaders", json=_body()),
        client.patch("/api/admin/leaders/eu-1", json={"enabled": False}),
        client.put("/api/admin/leaders/eu-1/credential", json={"credential": NEW_CREDENTIAL}),
        client.delete("/api/admin/leaders/eu-1"),
    ]
    for call in calls:
        answer = await call
        assert answer.status_code == 403
        assert answer.json()["code"] == "forbidden"
    refused = [e for e in await _audit(sessionmaker) if e.action == "request.refused"]
    assert len(refused) == 4  # the four changes; reads are not audited
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m uv run pytest packages/console/tests/test_registry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'swarmscribe_console.leaders'`.

- [ ] **Step 4: Write the registry service**

Create `packages/console/src/swarmscribe_console/leaders.py`:

```python
"""The leader registry (fleet console spec 5.1).

Console administrators add a leader with its https URL and the console credential a leader
administrator created (`swarmscribe-admin console create`), label it, enable or disable it,
replace the credential in place (rotation, C1 spec amendment 3) and remove it. Credentials
are sealed with AES-GCM before they reach the database and never leave it through the API.

Leader URLs (spec 7: https only) are also an SSRF surface: the console sends its credential
to them. Loopback, link-local (cloud metadata), unspecified, multicast and reserved
addresses, localhost names and numeric host spellings are refused; private addresses are
allowed because leaders are usually internal and only console administrators register them."""

import ipaddress
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from . import audit
from .crypto import ConsoleKeys
from .db.models import Leader
from .errors import Conflict, Invalid, NotFound
from .grants import LABEL_KEY, LABEL_VALUE, LEADER_NAME

MAX_LABELS = 32
MAX_URL_CHARS = 2000
# Serialises registrations, so that two at once cannot both pass the name check.
_REGISTRY_LOCK = 0x53430010
_CREDENTIAL = re.compile(r"[A-Za-z0-9_-]{43}")
_BLOCKED_HOSTS = frozenset({"localhost", "metadata", "metadata.google.internal"})
_HOST_LABEL = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")
_PATH = re.compile(r"(/[A-Za-z0-9._~-]+)*")


def _bad_url(message: str) -> Invalid:
    return Invalid(message, code="invalid_url")


def validate_name(name: str) -> str:
    if not isinstance(name, str) or not LEADER_NAME.fullmatch(name):
        raise Invalid(
            "a leader name starts with a letter or digit and holds only letters, digits,"
            " '.', '_' and '-' (at most 100 characters)",
            code="invalid_name",
        )
    return name


def _check_host(host: str) -> None:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        candidate = getattr(address, "ipv4_mapped", None) or address
        if (
            candidate.is_loopback
            or candidate.is_link_local
            or candidate.is_unspecified
            or candidate.is_multicast
            or candidate.is_reserved
        ):
            raise _bad_url(
                "a leader URL cannot use a loopback, link-local (cloud metadata), "
                "multicast or reserved address"
            )
        return
    labels = host.split(".")
    if (
        host in _BLOCKED_HOSTS
        or host.endswith(".localhost")
        or len(host) > 253
        or not all(_HOST_LABEL.fullmatch(label) for label in labels)
        or not labels[-1][0].isalpha()
    ):
        raise _bad_url("a leader URL needs a DNS host name or an IP address that may be used")


def validate_base_url(value: str) -> str:
    """The leader's normalised base URL: https://host[:port][/path], no trailing slash."""
    raw = value.strip() if isinstance(value, str) else ""
    if (
        not raw
        or len(raw) > MAX_URL_CHARS
        or not raw.isascii()
        or any(c.isspace() or ord(c) < 0x20 or ord(c) == 0x7F for c in raw)
    ):
        raise _bad_url("a leader URL is an https:// URL of printable ASCII without spaces")
    parts = urlsplit(raw)
    if parts.scheme.lower() != "https":
        raise _bad_url("a leader URL must use https://")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise _bad_url("a leader URL cannot carry a user name or password")
    if parts.query or parts.fragment or raw.endswith(("?", "#")):
        raise _bad_url("a leader URL has no query or fragment")
    try:
        port = parts.port
    except ValueError:
        raise _bad_url("the port must be a number from 1 to 65535") from None
    if port == 0:
        raise _bad_url("the port must be a number from 1 to 65535")
    host = parts.hostname
    if not host:
        raise _bad_url("a leader URL needs a host name")
    _check_host(host)
    path = parts.path.rstrip("/")
    if not _PATH.fullmatch(path) or any(seg in (".", "..") for seg in path.split("/")):
        raise _bad_url("a leader URL's path holds only letters, digits and . _ ~ - segments")
    netloc = f"[{host}]" if ":" in host else host
    if port is not None and port != 443:
        netloc += f":{port}"
    return f"https://{netloc}{path}"


def validate_labels(labels: Mapping[str, Any]) -> dict[str, str]:
    if len(labels) > MAX_LABELS:
        raise Invalid(f"a leader has at most {MAX_LABELS} labels", code="invalid_labels")
    for key, value in labels.items():
        if not isinstance(key, str) or not LABEL_KEY.fullmatch(key):
            raise Invalid(
                "label keys are lowercase letters, digits and . _ - (at most 63),"
                " starting with a letter or digit",
                code="invalid_labels",
            )
        if not isinstance(value, str) or not LABEL_VALUE.fullmatch(value):
            raise Invalid(
                "label values are 1 to 255 printable ASCII characters without spaces",
                code="invalid_labels",
            )
    return dict(sorted(labels.items()))


def validate_credential(value: str) -> str:
    if not isinstance(value, str) or not _CREDENTIAL.fullmatch(value):
        raise Invalid(
            "a console credential is the 43-character value `swarmscribe-admin console "
            "create` printed",
            code="invalid_credential",
        )
    return value


async def find_leader(session: AsyncSession, name: str, *, lock: bool = False) -> Leader:
    if not isinstance(name, str) or not LEADER_NAME.fullmatch(name):
        raise NotFound("no leader with that name", code="leader_not_found")
    query = select(Leader).where(func.lower(Leader.name) == name.lower())
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    leader = await session.scalar(query)
    if leader is None:
        raise NotFound("no leader with that name", code="leader_not_found")
    return leader


async def list_leaders(session: AsyncSession) -> list[Leader]:
    return list((await session.scalars(select(Leader).order_by(Leader.name))).all())


async def add_leader(
    session: AsyncSession,
    keys: ConsoleKeys,
    *,
    name: str,
    base_url: str,
    labels: Mapping[str, Any],
    credential: str,
    enabled: bool,
    actor: str,
) -> Leader:
    name = validate_name(name)
    url = validate_base_url(base_url)
    clean_labels = validate_labels(labels)
    credential = validate_credential(credential)
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _REGISTRY_LOCK})
    taken = await session.scalar(
        select(Leader.name).where(func.lower(Leader.name) == name.lower())
    )
    if taken is not None:
        raise Conflict(f"a leader named {taken!r} is already registered", code="exists")
    leader = Leader(
        name=name,
        base_url=url,
        labels=clean_labels,
        enabled=enabled,
        credential=keys.seal_credential(name, credential),
        credential_updated_by=actor,
        added_by=actor,
    )
    session.add(leader)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise Conflict(f"a leader named {name!r} is already registered", code="exists") from exc
    audit.record(
        session,
        actor=actor,
        action="leader.add",
        leader=name,
        detail={"base_url": url, "labels": clean_labels, "enabled": enabled},
    )
    return leader


async def edit_leader(
    session: AsyncSession,
    name: str,
    *,
    base_url: str | None = None,
    labels: Mapping[str, Any] | None = None,
    enabled: bool | None = None,
    actor: str,
) -> Leader:
    leader = await find_leader(session, name, lock=True)
    changed: dict[str, Any] = {}
    if base_url is not None:
        url = validate_base_url(base_url)
        if url != leader.base_url:
            # Another address: its health starts over.
            leader.consecutive_failures, leader.last_error, leader.last_polled_at = 0, None, None
        leader.base_url = changed["base_url"] = url
    if labels is not None:
        leader.labels = changed["labels"] = validate_labels(labels)
    if enabled is not None:
        leader.enabled = changed["enabled"] = enabled
    audit.record(session, actor=actor, action="leader.edit", leader=leader.name, detail=changed)
    return leader


async def replace_credential(
    session: AsyncSession,
    keys: ConsoleKeys,
    name: str,
    credential: str,
    *,
    now: datetime,
    actor: str,
) -> Leader:
    """Rotation in place: the leader keeps its name, labels, grants and history. A revoked
    mark and the poller's failure count are cleared, so polling resumes at once."""
    credential = validate_credential(credential)
    leader = await find_leader(session, name, lock=True)
    leader.credential = keys.seal_credential(leader.name, credential)
    leader.credential_updated_at = now
    leader.credential_updated_by = actor
    leader.credential_revoked_at = None
    leader.consecutive_failures = 0
    leader.last_error = None
    leader.last_polled_at = None
    audit.record(session, actor=actor, action="leader.credential_replace", leader=leader.name)
    return leader


async def remove_leader(session: AsyncSession, name: str, *, actor: str) -> None:
    leader = await find_leader(session, name, lock=True)
    await session.delete(leader)
    audit.record(session, actor=actor, action="leader.remove", leader=leader.name)


def leader_view(leader: Leader) -> dict[str, Any]:
    return {
        "name": leader.name,
        "base_url": leader.base_url,
        "labels": leader.labels,
        "enabled": leader.enabled,
        "added_by": leader.added_by,
        "created_at": leader.created_at,
        "credential_updated_at": leader.credential_updated_at,
        "credential_updated_by": leader.credential_updated_by,
        "credential_revoked": leader.credential_revoked_at is not None,
        "credential_revoked_at": leader.credential_revoked_at,
    }
```

- [ ] **Step 5: Write the request bodies and the admin routes**

Create `packages/console/src/swarmscribe_console/api/models.py`:

```python
"""Request and response bodies of the console's own /api routes. Credentials arrive as
SecretStr so that they are never part of a repr, and no response model has a credential."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class LeaderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=200)
    base_url: str = Field(max_length=4000)
    labels: dict[str, str] = Field(default_factory=dict)
    credential: SecretStr
    enabled: bool = True


class LeaderEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str | None = Field(default=None, max_length=4000)
    labels: dict[str, str] | None = None
    enabled: bool | None = None


class CredentialIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credential: SecretStr


class LeaderOut(BaseModel):
    name: str
    base_url: str
    labels: dict[str, str]
    enabled: bool
    added_by: str
    created_at: datetime
    credential_updated_at: datetime
    credential_updated_by: str
    credential_revoked: bool
    credential_revoked_at: datetime | None
```

Create `packages/console/src/swarmscribe_console/api/admin.py`:

```python
"""Console administration (fleet console spec 5.2): the leader registry, role grants and
console administrators. Console administrators only; holding a leader role is not enough,
and being a console administrator gives no leader role. Every change is audited."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from swarmscribe_leader.clock import utcnow

from .. import grants, leaders
from ..errors import Forbidden
from ..sessions import SignedIn
from .deps import Person, Session, keys_of
from .models import CredentialIn, LeaderEdit, LeaderIn, LeaderOut

router = APIRouter(prefix="/api/admin")


async def console_admin(person: Person, session: Session) -> SignedIn:
    if not await grants.is_console_admin(session, person.principals):
        raise Forbidden("this needs a console administrator")
    return person


ConsoleAdministrator = Annotated[SignedIn, Depends(console_admin)]


@router.get("/leaders", response_model=list[LeaderOut])
async def list_registered(admin: ConsoleAdministrator, session: Session) -> list[LeaderOut]:
    rows = await leaders.list_leaders(session)
    return [LeaderOut.model_validate(leaders.leader_view(row)) for row in rows]


@router.post("/leaders", response_model=LeaderOut, status_code=201)
async def register(
    body: LeaderIn, request: Request, admin: ConsoleAdministrator, session: Session
) -> LeaderOut:
    leader = await leaders.add_leader(
        session,
        keys_of(request),
        name=body.name,
        base_url=body.base_url,
        labels=body.labels,
        credential=body.credential.get_secret_value(),
        enabled=body.enabled,
        actor=admin.actor,
    )
    view = leaders.leader_view(leader)
    await session.commit()
    return LeaderOut.model_validate(view)


@router.patch("/leaders/{name}", response_model=LeaderOut)
async def edit(
    name: str, body: LeaderEdit, admin: ConsoleAdministrator, session: Session
) -> LeaderOut:
    leader = await leaders.edit_leader(
        session,
        name,
        base_url=body.base_url,
        labels=body.labels,
        enabled=body.enabled,
        actor=admin.actor,
    )
    view = leaders.leader_view(leader)
    await session.commit()
    return LeaderOut.model_validate(view)


@router.put("/leaders/{name}/credential", response_model=LeaderOut)
async def rotate(
    name: str,
    body: CredentialIn,
    request: Request,
    admin: ConsoleAdministrator,
    session: Session,
) -> LeaderOut:
    leader = await leaders.replace_credential(
        session,
        keys_of(request),
        name,
        body.credential.get_secret_value(),
        now=utcnow(),
        actor=admin.actor,
    )
    view = leaders.leader_view(leader)
    await session.commit()
    return LeaderOut.model_validate(view)


@router.delete("/leaders/{name}", status_code=204)
async def remove(name: str, admin: ConsoleAdministrator, session: Session) -> Response:
    await leaders.remove_leader(session, name, actor=admin.actor)
    await session.commit()
    return Response(status_code=204)
```

In `packages/console/src/swarmscribe_console/app.py`, add `from .api import admin as admin_api` to the `.api` imports and, after `app.include_router(session_api.router)`, add:

```python
    app.include_router(admin_api.router)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m uv run pytest packages/console/tests -v`
Expected: PASS (Tasks 1–6; the structural CSRF test now also walks the four registry changes).

Run: `python -m uv run ruff check packages/console`
Expected: no findings.

- [ ] **Step 7: Commit**

```bash
git add packages/console/src/swarmscribe_console/leaders.py packages/console/src/swarmscribe_console/api/models.py packages/console/src/swarmscribe_console/api/admin.py packages/console/src/swarmscribe_console/app.py packages/console/tests/console_testkit.py packages/console/tests/conftest.py packages/console/tests/test_registry.py
git commit -m "Console: leader registry with sealed credentials, rotation in place, URL rules

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Grants and console administrators — API, bootstrap command, README

**Files:**
- Modify: `packages/console/src/swarmscribe_console/grants.py` (append the store functions)
- Modify: `packages/console/src/swarmscribe_console/api/models.py`, `api/admin.py`, `main.py`
- Modify: `README.md`
- Test: `packages/console/tests/test_grants_api.py`

**Interfaces:**
- Consumes: `RoleGrant`, `ConsoleAdmin` (Task 2); `parse_scope`, `normalize_principal` (Task 3); `ConsoleAdministrator` (Task 6).
- Produces:
  - `async grants.add_grant(session, *, role, scope, principal_kind, principal, actor) -> RoleGrant` (raises `Invalid`, `Conflict(code="exists")`); `async grants.remove_grant(session, grant_id, *, actor) -> None` (`NotFound`); `async grants.list_grants(session) -> list[RoleGrant]`.
  - `async grants.add_console_admin(session, *, principal_kind, principal, actor) -> ConsoleAdmin`; `async grants.remove_console_admin(session, admin_id, *, actor) -> None` (raises `Conflict(code="last_admin")`); `async grants.list_console_admins(session) -> list[ConsoleAdmin]`.
  - Audit actions `grant.add`, `grant.remove`, `console_admin.add`, `console_admin.remove`.
  - Routes `GET/POST /api/admin/grants`, `DELETE /api/admin/grants/{grant_id}`, `GET/POST /api/admin/console-admins`, `DELETE /api/admin/console-admins/{admin_id}`.
  - `api.models.GrantIn`, `GrantOut`, `ConsoleAdminIn`, `ConsoleAdminOut`.
  - CLI `swarmscribe-console admins add <kind> <principal>` and `admins list`; `main.CLI_ACTOR = "swarmscribe-console cli"`.

- [ ] **Step 1: Write the failing tests**

Create `packages/console/tests/test_grants_api.py`:

```python
import asyncio

import pytest
from console_testkit import ADMIN_PRINCIPAL, GROUPS, console_env, recreate, with_database
from sqlalchemy import select
from swarmscribe_console.db.models import AuditEntry
from swarmscribe_console.main import main


@pytest.fixture
async def admin(client, factory):
    await factory.console_admin("email", "admin@example.org")
    csrf = await factory.person(client, principals={ADMIN_PRINCIPAL}, email="admin@example.org")
    client.headers["X-CSRF-Token"] = csrf
    return client


async def _actions(sessionmaker) -> list[str]:
    async with sessionmaker() as session:
        return list((await session.scalars(select(AuditEntry.action).order_by(AuditEntry.id))).all())


async def test_a_grant_is_added_canonically_listed_and_removed(admin, sessionmaker):
    answer = await admin.post(
        "/api/admin/grants",
        json={
            "role": "operator",
            "scope": "label:region=eu=west",
            "principal_kind": "entra_group",
            "principal": GROUPS["operator"].upper(),
        },
    )
    assert answer.status_code == 201
    body = answer.json()
    assert (body["role"], body["scope"], body["principal_kind"], body["principal"]) == (
        "operator",
        "label:region=eu=west",
        "entra_group",
        GROUPS["operator"],
    )
    listed = (await admin.get("/api/admin/grants")).json()
    assert [g["id"] for g in listed] == [body["id"]]
    assert (await admin.delete(f"/api/admin/grants/{body['id']}")).status_code == 204
    assert (await admin.get("/api/admin/grants")).json() == []
    assert await _actions(sessionmaker) == ["grant.add", "grant.remove"]


async def test_a_leader_scope_is_stored_lowercase(admin):
    answer = await admin.post(
        "/api/admin/grants",
        json={
            "role": "viewer",
            "scope": "leader:EU-1",
            "principal_kind": "email",
            "principal": "Person@Example.org",
        },
    )
    assert (answer.json()["scope"], answer.json()["principal"]) == (
        "leader:eu-1",
        "person@example.org",
    )


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("role", "superadmin", "invalid_request"),
        ("scope", "leader:../x", "invalid_scope"),
        ("scope", "label:env", "invalid_scope"),
        ("principal_kind", "user", "invalid_principal"),
        ("principal", "not-a-guid", "invalid_principal"),
    ],
)
async def test_odd_grants_are_refused(admin, field, value, code):
    body = {
        "role": "viewer",
        "scope": "all",
        "principal_kind": "entra_group",
        "principal": GROUPS["viewer"],
    }
    body[field] = value
    answer = await admin.post("/api/admin/grants", json=body)
    assert answer.status_code == 422
    assert answer.json()["code"] == code


async def test_one_principal_has_one_role_per_scope(admin):
    body = {"role": "viewer", "scope": "all", "principal_kind": "email", "principal": "a@b.org"}
    assert (await admin.post("/api/admin/grants", json=body)).status_code == 201
    body["role"] = "admin"
    body["principal"] = "A@B.org"
    answer = await admin.post("/api/admin/grants", json=body)
    assert answer.status_code == 409
    assert answer.json()["code"] == "exists"


async def test_removing_an_unknown_grant_is_not_found(admin):
    unknown = "00000000-0000-4000-8000-000000000000"
    assert (await admin.delete(f"/api/admin/grants/{unknown}")).status_code == 404
    assert (await admin.delete("/api/admin/grants/not-a-uuid")).status_code == 422


async def test_console_admins_are_added_and_removed_but_never_the_last(admin, sessionmaker):
    listed = (await admin.get("/api/admin/console-admins")).json()
    (me,) = listed
    assert (me["principal_kind"], me["principal"]) == ("email", "admin@example.org")
    answer = await admin.delete(f"/api/admin/console-admins/{me['id']}")
    assert answer.status_code == 409
    assert answer.json()["code"] == "last_admin"
    added = await admin.post(
        "/api/admin/console-admins",
        json={"principal_kind": "entra_group", "principal": GROUPS["console"]},
    )
    assert added.status_code == 201
    assert (await admin.delete(f"/api/admin/console-admins/{me['id']}")).status_code == 204
    # I am no longer a console administrator.
    assert (await admin.get("/api/admin/console-admins")).status_code == 403
    assert await _actions(sessionmaker) == [
        "request.refused",
        "console_admin.add",
        "console_admin.remove",
    ]


async def test_a_leader_admin_cannot_grant_roles(client, factory):
    await factory.grant("admin", "all", "email", "person@example.org")
    csrf = await factory.person(client, principals={"email:person@example.org"})
    client.headers["X-CSRF-Token"] = csrf
    answer = await client.post(
        "/api/admin/grants",
        json={"role": "admin", "scope": "all", "principal_kind": "email", "principal": "x@y.org"},
    )
    assert answer.status_code == 403
    assert (await client.get("/api/admin/grants")).status_code == 403
    assert (await client.get("/api/admin/console-admins")).status_code == 403


def test_the_first_console_admin_is_added_from_the_command_line(
    admin_database_url, monkeypatch, capsys
):
    # Its own database: main() runs its own event loop, so this test is synchronous and
    # cannot use the async `engine` fixture.
    name = "swarmscribe_console_cli"
    url = with_database(admin_database_url, name)
    asyncio.run(recreate(admin_database_url, name))
    console_env(monkeypatch, url)
    try:
        assert main(["admins", "list"]) == 2  # not migrated yet: refused, says so
        assert "swarmscribe-console migrate" in capsys.readouterr().err
        assert main(["migrate"]) == 0
        capsys.readouterr()
        assert main(["admins", "add", "email", "First@Example.org"]) == 0
        assert "email:first@example.org" in capsys.readouterr().out
        assert main(["admins", "add", "email", "first@example.org"]) == 1
        assert "already" in capsys.readouterr().err
        assert main(["admins", "add", "email", "not an email"]) == 1
        capsys.readouterr()
        assert main(["admins", "list"]) == 0
        assert "email:first@example.org\tswarmscribe-console cli" in capsys.readouterr().out
    finally:
        asyncio.run(recreate(admin_database_url, name, drop_only=True))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m uv run pytest packages/console/tests/test_grants_api.py -v`
Expected: FAIL — `404` from `/api/admin/grants`, and `main` refusing the `admins` command.

- [ ] **Step 3: Add the grant and administrator store**

Append to `packages/console/src/swarmscribe_console/grants.py` (add `from sqlalchemy import func, text`, `from sqlalchemy.exc import IntegrityError`, and `from . import audit`, `from .errors import Conflict, NotFound` to its imports):

```python
_ADMINS_LOCK = 0x53430011  # serialises console-admin removals: never remove the last one
ROLES = ("viewer", "operator", "admin")


async def list_grants(session: AsyncSession) -> list[RoleGrant]:
    return list(
        (
            await session.scalars(
                select(RoleGrant).order_by(
                    RoleGrant.scope, RoleGrant.principal_kind, RoleGrant.principal
                )
            )
        ).all()
    )


async def add_grant(
    session: AsyncSession,
    *,
    role: str,
    scope: str,
    principal_kind: str,
    principal: str,
    actor: str,
) -> RoleGrant:
    if role not in ROLES:
        raise Invalid("role is viewer, operator or admin")
    canonical = str(parse_scope(scope))
    value = normalize_principal(principal_kind, principal)
    grant = RoleGrant(
        role=role,
        scope=canonical,
        principal_kind=principal_kind,
        principal=value,
        created_by=actor,
    )
    session.add(grant)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise Conflict(
            "that principal already has a grant on that scope; remove it first", code="exists"
        ) from exc
    audit.record(
        session,
        actor=actor,
        action="grant.add",
        target=str(grant.id),
        detail={
            "role": role,
            "scope": canonical,
            "principal": principal_key(principal_kind, value),
        },
    )
    return grant


async def remove_grant(session: AsyncSession, grant_id: uuid.UUID, *, actor: str) -> None:
    grant = await session.get(RoleGrant, grant_id, with_for_update=True)
    if grant is None:
        raise NotFound("no such grant")
    await session.delete(grant)
    audit.record(
        session,
        actor=actor,
        action="grant.remove",
        target=str(grant_id),
        detail={
            "role": grant.role,
            "scope": grant.scope,
            "principal": principal_key(grant.principal_kind, grant.principal),
        },
    )


async def list_console_admins(session: AsyncSession) -> list[ConsoleAdmin]:
    return list(
        (
            await session.scalars(
                select(ConsoleAdmin).order_by(ConsoleAdmin.principal_kind, ConsoleAdmin.principal)
            )
        ).all()
    )


async def add_console_admin(
    session: AsyncSession, *, principal_kind: str, principal: str, actor: str
) -> ConsoleAdmin:
    value = normalize_principal(principal_kind, principal)
    admin = ConsoleAdmin(principal_kind=principal_kind, principal=value, created_by=actor)
    session.add(admin)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise Conflict(
            "that principal is already a console administrator", code="exists"
        ) from exc
    audit.record(
        session,
        actor=actor,
        action="console_admin.add",
        target=str(admin.id),
        detail={"principal": principal_key(principal_kind, value)},
    )
    return admin


async def remove_console_admin(session: AsyncSession, admin_id: uuid.UUID, *, actor: str) -> None:
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _ADMINS_LOCK})
    admin = await session.get(ConsoleAdmin, admin_id)
    if admin is None:
        raise NotFound("no such console administrator")
    remaining = await session.scalar(select(func.count()).select_from(ConsoleAdmin))
    if remaining <= 1:
        raise Conflict(
            "this is the last console administrator; add another first", code="last_admin"
        )
    await session.delete(admin)
    audit.record(
        session,
        actor=actor,
        action="console_admin.remove",
        target=str(admin_id),
        detail={"principal": principal_key(admin.principal_kind, admin.principal)},
    )
```

- [ ] **Step 4: Add the bodies and routes**

Append to `packages/console/src/swarmscribe_console/api/models.py` (add `import uuid` and `from typing import Literal` at the top):

```python
class GrantIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["viewer", "operator", "admin"]
    scope: str = Field(max_length=400)
    principal_kind: str = Field(max_length=32)
    principal: str = Field(max_length=320)


class GrantOut(BaseModel):
    id: uuid.UUID
    role: str
    scope: str
    principal_kind: str
    principal: str
    created_by: str
    created_at: datetime


class ConsoleAdminIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    principal_kind: str = Field(max_length=32)
    principal: str = Field(max_length=320)


class ConsoleAdminOut(BaseModel):
    id: uuid.UUID
    principal_kind: str
    principal: str
    created_by: str
    created_at: datetime
```

Append to `packages/console/src/swarmscribe_console/api/admin.py` (add `import uuid` and import `ConsoleAdminIn`, `ConsoleAdminOut`, `GrantIn`, `GrantOut` from `.models`):

```python
def _grant_out(grant) -> GrantOut:
    return GrantOut.model_validate(grant, from_attributes=True)


def _admin_out(admin) -> ConsoleAdminOut:
    return ConsoleAdminOut.model_validate(admin, from_attributes=True)


@router.get("/grants", response_model=list[GrantOut])
async def list_grants(admin: ConsoleAdministrator, session: Session) -> list[GrantOut]:
    return [_grant_out(grant) for grant in await grants.list_grants(session)]


@router.post("/grants", response_model=GrantOut, status_code=201)
async def add_grant(body: GrantIn, admin: ConsoleAdministrator, session: Session) -> GrantOut:
    grant = await grants.add_grant(
        session,
        role=body.role,
        scope=body.scope,
        principal_kind=body.principal_kind,
        principal=body.principal,
        actor=admin.actor,
    )
    out = _grant_out(grant)
    await session.commit()
    return out


@router.delete("/grants/{grant_id}", status_code=204)
async def remove_grant(
    grant_id: uuid.UUID, admin: ConsoleAdministrator, session: Session
) -> Response:
    await grants.remove_grant(session, grant_id, actor=admin.actor)
    await session.commit()
    return Response(status_code=204)


@router.get("/console-admins", response_model=list[ConsoleAdminOut])
async def list_admins(admin: ConsoleAdministrator, session: Session) -> list[ConsoleAdminOut]:
    return [_admin_out(row) for row in await grants.list_console_admins(session)]


@router.post("/console-admins", response_model=ConsoleAdminOut, status_code=201)
async def add_admin(
    body: ConsoleAdminIn, admin: ConsoleAdministrator, session: Session
) -> ConsoleAdminOut:
    row = await grants.add_console_admin(
        session, principal_kind=body.principal_kind, principal=body.principal, actor=admin.actor
    )
    out = _admin_out(row)
    await session.commit()
    return out


@router.delete("/console-admins/{admin_id}", status_code=204)
async def remove_admin(
    admin_id: uuid.UUID, admin: ConsoleAdministrator, session: Session
) -> Response:
    await grants.remove_console_admin(session, admin_id, actor=admin.actor)
    await session.commit()
    return Response(status_code=204)
```

- [ ] **Step 5: Add the bootstrap command**

In `packages/console/src/swarmscribe_console/main.py`, add these imports:

```python
from swarmscribe_leader.db.session import make_sessionmaker

from . import grants
from .errors import ConsoleError
```

add after `_schema_problem`:

```python
CLI_ACTOR = "swarmscribe-console cli"


async def _admins(settings: Settings, args: argparse.Namespace) -> int:
    engine = make_engine(settings.database_url.get_secret_value())
    try:
        async with make_sessionmaker(engine)() as session:
            if args.admins_command == "add":
                try:
                    admin = await grants.add_console_admin(
                        session,
                        principal_kind=args.kind,
                        principal=args.principal,
                        actor=CLI_ACTOR,
                    )
                except ConsoleError as exc:
                    print(f"error: {exc.message}", file=sys.stderr)
                    return 1
                await session.commit()
                print(f"console administrator added: {admin.principal_kind}:{admin.principal}")
                return 0
            for admin in await grants.list_console_admins(session):
                print(f"{admin.principal_kind}:{admin.principal}\t{admin.created_by}")
            return 0
    finally:
        await engine.dispose()
```

in `main`, after the `serve` parser lines, add:

```python
    admins = commands.add_parser("admins", help="manage console administrators")
    admin_commands = admins.add_subparsers(dest="admins_command", required=True)
    add = admin_commands.add_parser("add", help="add a console administrator")
    add.add_argument("kind", choices=grants.PRINCIPAL_KINDS)
    add.add_argument("principal")
    admin_commands.add_parser("list", help="list console administrators")
```

and after the `migrate` branch (before the schema check), add:

```python
    if args.command == "admins":
        problem = asyncio.run(_schema_problem(settings))
        if problem is not None:
            print(f"error: {problem}", file=sys.stderr)
            return 2
        return asyncio.run(_admins(settings, args))
```

- [ ] **Step 6: Document running the console**

In `README.md`, insert before `## Develop`:

````markdown
## Run the fleet console (development)

The fleet console is one web service for many leaders. It has its own Postgres
(never a leader's). Set:

| Variable | Meaning |
|---|---|
| `SWARMSCRIBE_CONSOLE_DATABASE_URL` | the console's own database |
| `SWARMSCRIBE_CONSOLE_PUBLIC_URL` | the console's https origin, e.g. `https://console.example.org` (sign-in redirects to `<origin>/auth/callback`) |
| `SWARMSCRIBE_CONSOLE_KEY` | 32 random bytes, URL-safe base64: `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Seals leader credentials; losing it means re-entering every credential |
| `SWARMSCRIBE_CONSOLE_ENTRA_TENANT_ID`, `_ENTRA_CLIENT_ID`, `_ENTRA_CLIENT_SECRET` | Entra ID web app registration (all three) |
| `SWARMSCRIBE_CONSOLE_GOOGLE_CLIENT_ID`, `_GOOGLE_CLIENT_SECRET` | Google OAuth web client (both); optional `_GOOGLE_HOSTED_DOMAIN`, `_GOOGLE_SERVICE_ACCOUNT` (Google Groups) |

```
uv run swarmscribe-console migrate
uv run swarmscribe-console admins add email you@example.org
uv run swarmscribe-console serve --port 8443
```

Sign-in uses the same rules as the leader (section "Administrators: sign-in
and roles"). A person may sign in only if a grant or a console-administrator
entry names one of their principals: an Entra group id (`entra_group`), a
Google group (`google_group`), an email or a domain. Group membership is read
at sign-in; it applies to an open session at the next sign-in (sessions last
at most 8 hours, 1 hour idle).

Console administrators manage leaders and grants (under `/api/admin`); that
gives them no role on any leader. A person's role on a leader is the highest
grant whose scope matches it: `leader:<name>`, `label:<key>=<value>` or `all`.
````

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python -m uv run pytest packages/console/tests -v`
Expected: PASS (all of C2a).

Run: `python -m uv run pytest packages -q`
Expected: PASS — the leader, engine and protocol suites are untouched.

Run: `python -m uv run ruff check .`
Expected: no findings.

- [ ] **Step 8: Commit**

```bash
git add packages/console/src/swarmscribe_console/grants.py packages/console/src/swarmscribe_console/api/models.py packages/console/src/swarmscribe_console/api/admin.py packages/console/src/swarmscribe_console/main.py packages/console/tests/test_grants_api.py README.md
git commit -m "Console: grants and console administrators, bootstrap command, README

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Self-review

**Spec coverage (5.1, 5.2, and 7/8/9 where they apply).** Tables `leaders`, `role_grants`, `console_admins`, `snapshots`, `audit_log` with the spec's columns: Task 2 (plus `sessions` and `login_attempts`, which 5.2 implies). Credentials encrypted with AES-GCM from `SWARMSCRIBE_CONSOLE_KEY`, never returned: Tasks 1 and 6 (`test_the_credential_is_encrypted_at_rest`, the no-credential assertions on every registry answer). OIDC authorization code + PKCE against Entra ID and Google with the leader's validation and ASCII/Workspace rules: Task 5 (the leader's `TokenVerifier`; `principals.py` mirrors the leader's Google rules; `test_the_request_uses_pkce_s256…`). Session cookie flags, 8-hour lifetime, 1-hour idle: Tasks 4 and 5 (`test_the_session_cookie_has_the_spec_flags`, `test_a_session_ends_eight_hours_after_sign_in`, `test_a_session_idle_for_an_hour_ends`). CSRF on every state-changing request: Task 4 (`test_every_state_changing_api_route_needs_the_csrf_token` walks all routes, C2b's included). Highest matching grant by name, label or `all`: Task 3. Console admins manage the registry and grants only, no implied role: Tasks 3, 6, 7. ID and refresh tokens not kept: Task 5 (`test_no_token_code_or_verifier_is_kept_after_sign_in`). Leader URLs https: Task 2 (CHECK) and Task 6. CSP: Task 4. Rotation in place (C1 amendment 3): Task 6. Logs: no access log, `httpx` quiet (Tasks 2/4); the end-to-end log sweep is C2b Task 5. Section 9 console-backend tests owned by C2a: grant matching, cookie flags, CSRF, idle timeout, credentials encrypted at rest. Poller, proxy, token pass-through, fleet and history are C2b.

**Owner rulings 2026-10-03.** Library import: the File Structure and every task import from `swarmscribe_leader`, and no leader file is modified. Private addresses allowed: `test_https_leader_urls_are_normalised` keeps `10.0.0.5`, `192.168.1.50` and `fd00::5` while `test_other_leader_urls_are_refused` refuses loopback, link-local and metadata. 8-hour group delay: principals are stored in `sessions.principals` at sign-in (Task 5's tests read them from the row) and grants are read per request (Task 3). Unaudited proxied reads and the C1b additions are C2b's; nothing in C2a depends on C1b.

**Placeholder scan.** Every code step carries complete code; every run step names its command and expected result. Two steps modify a file by inserting named blocks (main.py in Tasks 4 and 7, app.py in Task 6, grants/models/admin in Task 7); each gives the exact code and where it goes.

**Type consistency.** `create_session(session, *, provider, issuer, subject, email, principals, now, lifetime) -> str` is called with those keywords in Task 4's `Factory.person` and Task 5's callback. `find_session(..., now=, idle=)` matches `signed_in`. `begin_sign_in` returns `(url, browser)` and `finish_sign_in` returns `SignInAttempt` with `provider, nonce, code_verifier, return_to`, as the callback reads them. `grants_held` returns `(role, Scope)` pairs consumed by `role_for` (Task 3, and C2b). `leaders.find_leader(session, name, *, lock=False)` raises `NotFound(code="leader_not_found")`, which C2b's fleet and proxy rely on. `ConsoleKeys.open_credential(leader_name, sealed)` is the call C2b's poller and proxy make. `api.deps.Person`/`Session`/`keys_of`/`STATE_CHANGING` names match across Tasks 4–7. Audit actions: `sign_in`, `sign_in.refused`, `sign_out`, `request.refused`, `leader.add|edit|credential_replace|remove`, `grant.add|remove`, `console_admin.add|remove` — the same strings in code and tests.

**Review Focus.** Lines 1–4 are pinned in Tasks 4 and 5 (`test_a_forged_or_malformed_cookie_is_refused_and_cleared`, the idle and lifetime tests, `test_signing_in_never_adopts_the_cookie_the_browser_brought`, `test_csrf_failures_are_refused_audited_and_change_nothing`, the structural CSRF walk, the return-URL and callback tests); 5 in Task 6 (`test_other_leader_urls_are_refused`, 33 cases); 6 in Tasks 3 and 7 (`test_a_label_value_with_an_equals_sign_matches_only_that_value`, `test_a_grant_is_added_canonically…`); 7 in Tasks 3 and 7 (`test_principals_are_stored_in_one_canonical_form`, `test_odd_principals_are_refused`, `test_one_principal_has_one_role_per_scope`); 8 in Task 5; 9 in Tasks 1 and 6; 10 in Task 7 (`test_console_admins_are_added_and_removed_but_never_the_last`).
