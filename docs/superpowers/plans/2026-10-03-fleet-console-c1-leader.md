# Fleet Console C1 — Leader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A leader accepts a fleet console's own credential on `/v1/admin/*`, acting for the person (or the console's poller) the console names, with the lower of the asserted role and the credential's cap, auditing every call as `<email> (<issuer> <subject>) via console <name>` (except the poller's successful reads); leader admins manage those credentials with `swarmscribe-admin console create|list|revoke`; and `GET /v1/admin/status` also counts followers by pool and state, so the poller needs one call.

**Architecture:** A new `console_credentials` table (migration `0005`) holds each console's name, role cap and the SHA-256 of its credential, created and found exactly like follower credentials (`auth/secrets.py`). A new module `auth/consoles.py` owns the credential store (create, revoke, authenticate) and the pure parsing of the two delegation headers. `api/admin_auth.py`'s `require()` learns a second `Authorization` scheme, `Console`: it authenticates the credential, parses the actor and role headers (only on that scheme), caps the role and then applies the unchanged role check; `Admin` stops wrapping an `Identity` and carries the caller's fields plus the console's name, so every existing `admin.actor` audit call names the console without being touched; `_viewed` skips the audit row for the poller's successful reads. Three person-only admin routes (`/v1/admin/consoles…`) and three CLI commands sit on top. Separately, the status summary's follower query becomes one `GROUP BY pool, state` aggregate that yields both the existing totals and a new additive `follower_pools` field.

**Tech Stack:** Python 3.11+, uv workspace, FastAPI, Pydantic v2, SQLAlchemy 2.0 async + Alembic on Postgres, httpx, pytest + pytest-asyncio, ruff.

**Spec:** `docs/superpowers/specs/2026-10-03-fleet-console-design.md` (approved; the authority) — section 4 is this plan's scope, section 9's "Leader" line its required tests. Sign-in and roles: `docs/superpowers/specs/2026-10-02-leader-design.md` section 10.

**Precondition:** PR #4 (`channel-split-leader`, migration `0004_channel_split.py`) is merged to `main`, and this work branches from `main`:

```bash
git switch main
git pull
git switch -c fleet-console-leader
```

Before Task 1, confirm `packages/leader/src/swarmscribe_leader/db/migrations/versions/0004_channel_split.py` exists and `packages/leader/tests/test_migrations.py::test_database_is_at_the_head_revision` asserts `"0004"`.

## Global Constraints

- Command, verbatim: `swarmscribe-admin console create --name <console> --max-role viewer|operator|admin`; "The leader stores the credential hashed (like follower credentials) with its cap and shows it once. `console list|revoke` manage them."
- Request, verbatim: `Authorization: Console <credential>`, `X-SwarmScribe-Actor: <issuer> <subject> <email>`, `X-SwarmScribe-Actor-Role: viewer|operator|admin`.
- "authenticates the console credential (revoked → 401)"; "computes the effective role as the lower of the asserted role and the credential's cap"; "applies the same role checks as for a signed-in person"; "audits with actor `<email> (<issuer> <subject>) via console <name>`".
- "Console credentials are accepted only on `/v1/admin/*`, never on follower routes; follower credentials and ID tokens are unchanged."
- "The actor headers are ignored on requests not authenticated as a console."
- The poller (spec 5.3) calls "with actor `system:poller` and role `viewer`".
- Roles are cumulative: admin ⊃ operator ⊃ viewer (leader spec section 10); role checks stay in `require()`.
- Logs never contain credentials (spec section 7); credentials are 32 random bytes, URL-safe base64, shown once, stored as SHA-256 (leader spec section 10).
- Migration `0005`, `down_revision = "0004"`. Alembic head becomes `"0005"`.
- Owner rulings 2026-10-03 (see "Spec amendments"): the poller's successful reads write no audit row, every refusal involving it still does; `GET /v1/admin/status` carries followers by pool and state from one aggregate query, additively.
- Dependency rule: `leader` imports `swarmscribe_protocol` only (no new third-party dependency in this plan).
- On this Windows machine `uv` is not on PATH: every command is written `python -m uv run …`. The leader tests need Postgres (`pgserver` starts one automatically).
- If `ruff check` flags import order or line length in code copied from this plan, run `python -m uv run ruff check --fix --select I packages/leader` or wrap the line without changing behaviour.
- Commits end with a blank line and `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Do not push.

## Review Focus

Hostile and malformed input the spec implies but does not spell out. Each line names the behaviour a reasonable person expects and the task whose tests pin it.

1. **Actor header missing, empty, or with too few or too many parts** (one, two, four words; double, leading or trailing spaces; tabs; sent twice) → `400 invalid_actor`, audited as `console.refused` by `console <name>` with only the error code, never the header value. — Task 2 (parser), Task 4 (HTTP).
2. **Control characters or line breaks in the actor** (`\r\n`, `\n`, `\x00`, `\x1b`, `\x7f`, U+2028) — audit-log injection → refused before anything is built from it; the audit actor is assembled only from validated parts and a console name that matched `^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$`. — Task 2, Task 3 (names), Task 4.
3. **Over-long values** → issuer > 255, subject > 255 or email > 254 characters is `400 invalid_actor`; a console credential that is not exactly 43 URL-safe characters (including 100 000 characters) is `401` before it is hashed or looked up. — Task 1, Task 2, Task 4.
4. **Non-ASCII email** (`pérson@…`, `person@exämple.org`, UTF-8 bytes in the header) → `400 invalid_actor`; the console must send ASCII. — Task 2, Task 4.
5. **Unknown role string** (`superadmin`, `Admin`, `ADMIN`, ` admin`, `admin,viewer`, NUL, non-ASCII) → `400 invalid_actor_role`; only the exact lowercase names count. — Task 2, Task 4.
6. **Role header missing (or sent twice) on a console request** → `400 invalid_actor_role`. — Task 2, Task 4.
7. **Both a Bearer and a Console `Authorization` header** (either order) → `401 unauthorized` "send exactly one Authorization header"; nothing is picked. — Task 4.
8. **A console credential presented to a follower route** (as `Console` or as `Bearer`) → `401`; and a follower credential presented as `Console` on an admin route → `401`. — Task 4.
9. **Timing-safe comparison** → the plaintext is never compared: it is hashed and the SHA-256 is looked up (as for followers); a guess sharing 42 of 43 characters with the real credential gets a byte-identical answer to a random guess. — Task 1, Task 4.
10. **A credential revoked mid-session** (the console keeps polling) → the next request is `401` "this console credential has been revoked"; nothing caches console authentication. — Task 1, Task 4.
11. **A duplicate console name** (same, different case, after a revocation, two creations at once) → `409 exists`, exactly one row. — Task 1, Task 3, Task 5.
12. **Actor headers on a person's Bearer request** (a viewer claiming `admin` for someone else, or garbage values) → ignored entirely: the person keeps their own role and name, no `400`. — Task 4.
13. **A console credential on the console-management routes** (even capped and asserted `admin`) → `403`; a console cannot mint, list or revoke console credentials. — Task 4.
14. **The poller asking for more than viewer** → `400 invalid_actor_role`, not silently lowered. — Task 2, Task 4.
15. **The poller's reads every 15 seconds** → a successful poller GET (status, followers, whoami) leaves the audit-log row count unchanged; every refusal involving the poller (400 malformed headers, 401 revoked credential, 403 role, 404 failed read) still adds a row; a person's read through the same console still adds one. — Task 4.
16. **Follower counts per pool from one call** (two pools, several states, a state with no followers, no followers at all) → `follower_pools` lists each pool with all four states (zeros included), totals in `followers` unchanged, and the status call issues exactly one query against `followers` however many pools exist. — Task 6.

## Decisions this plan makes (the spec is silent)

- **Who manages console credentials:** a leader **admin signed in as a person** (Bearer ID token). Console requests to `/v1/admin/consoles…` are `403` even at effective `admin`, so a leaked console credential cannot mint more credentials or revoke the human admins' way back in.
- **Poller actor:** `X-SwarmScribe-Actor` is either exactly three parts or exactly the single word `system:poller`. The poller must assert `viewer` (anything else is `400 invalid_actor_role`); its effective role is `min(viewer, cap)`; it is audited as `system:poller via console <name>`; `whoami` shows `issuer`, `subject` and `email` as `null`. No other `system:*` actor exists.
- **Actor format:** parts are separated by single ASCII spaces and each is visible ASCII (`0x21`–`0x7E`). Issuer: an `https://` URL (OIDC requires https issuers), ≤ 255 characters. Subject: ≤ 255 characters (OIDC's limit). Email: one `@` with text on both sides, ≤ 254 characters (RFC 5321), lowercased like a signed-in person's; the literal `-` means "no email" and is audited as `unknown`, as `Identity.actor` does for persons today.
- **Role header:** exactly `viewer`, `operator` or `admin`, case-sensitive; checked before the actor.
- **Malformed delegation:** `400` with codes `invalid_actor` / `invalid_actor_role`, fixed messages (never echoing the value), audited as action `console.refused`, actor `console <name>`, subject `"<METHOD> <route template>"`, detail `{"code": …}`.
- **Check order:** credential first (an unknown or revoked credential is `401` whatever the headers say, so an outsider learns nothing about header rules), then role header, then actor header, then the role check.
- **401 challenge for a refused console credential:** `WWW-Authenticate: Console error="invalid_token"` (new `Unauthorized.scheme`); a request with no usable `Authorization` still gets `Bearer`.
- **Several `Authorization` headers:** `401 unauthorized`, for Bearer as well as Console.
- **Credential format:** `new_secret()` output, 43 URL-safe base64 characters; anything else is refused unhashed.
- **Unknown vs revoked messages differ** ("unknown console credential" / "this console credential has been revoked"); only a holder of the real credential can see the second.
- **A revoked console's 401 is audited** as `console.refused` by `console <name>`, detail `{"code": "revoked"}` (a revoked console still calling is worth seeing). An unknown credential's 401 is not audited: it names no one, like an unauthenticated Bearer request today.
- **Poller reads are not audited (owner ruling).** `Admin.is_poller` (console request whose actor is `system:poller`) makes `_viewed` — the one place reads are audited — skip the row. Nothing else changes: the poller's refusals are audited by `require()` (`admin.refused`), `_console_caller` (`console.refused`, including 400s and the revoked 401) and `audit_refused_request` (`admin.read_refused`/`admin.change_refused`); every read and change by a person through a console is audited.
- **Followers by pool (owner ruling).** `Status.follower_pools: list[PoolFollowers]` (default `[]`), sorted by pool, each `{pool, active, draining, revoked, gone}` with zeros filled in; pools with no followers do not appear. The existing `followers` totals are now summed from the same `GROUP BY pool, state` query, which replaces the old `GROUP BY state` query, so the status call still makes one follower query. `swarmscribe-admin status` output is unchanged (`--json` shows the new field).
- **Names:** same pattern as locations (`^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$`), unique ignoring case, **never reused even after revocation** (the audit log names consoles by name). Rotation = create a new name, configure the console, revoke the old one.
- **Revoke is by name**, case-insensitive; revoking twice is `200` and keeps the first `revoked_at`/`revoked_by` (each call is audited).
- **`--max-role` is required** in the CLI and the API (no default: the cap is a security decision).
- **No caching and no row lock** for console authentication: one indexed read per request, so revocation applies from the next request; a request that authenticated before the revocation committed completes.
- **Refusal audit detail** for a console adds `asserted` and `cap`; the `403` message says "console <name> is limited to <cap>" when the cap is what refused it.
- **`whoami`** reports `provider: "console"` and a new `console` field (null for persons); `issuer`/`subject` become nullable for the poller.
- **No `last_used_at`:** recording use would write a row every poll; out of scope.
- Console credentials work on a leader with no identity provider configured (their check does not use OIDC).

## File Structure

```
packages/leader/src/swarmscribe_leader/
  db/models.py                                         MOD  ConsoleCredential (Task 1)
  db/migrations/versions/0005_console_credentials.py   NEW  (Task 1)
  auth/consoles.py                                     NEW  store (Task 1); delegation parsing (Task 2)
  reports.py                                           MOD  console_view, list_consoles (Task 1); follower_pools (Task 6)
  api/admin_models.py                                  MOD  ConsoleIn/Out/Created (Task 3); WhoAmI (Task 4); PoolFollowers (Task 6)
  api/admin.py                                         MOD  /consoles routes (Task 3); whoami, person-only, _viewed (Task 4)
  errors.py                                            MOD  Unauthorized.scheme (Task 4)
  api/errors.py                                        MOD  WWW-Authenticate uses the scheme (Task 4)
  api/admin_auth.py                                    MOD  Console scheme, Admin, require (Task 4)
  admin_cli/main.py                                    MOD  console create/list/revoke (Task 5)
packages/leader/tests/
  conftest.py                                          MOD  Factory.console (Task 4)
  test_migrations.py                                   MOD  head 0005, CHECK, round trip (Task 1)
  test_consoles.py                                     NEW  store (Task 1); parsing (Task 2)
  test_admin_api.py                                    MOD  routes + role table (Task 3); console boundary (Task 4); status by pool (Task 6)
  test_console_auth.py                                 NEW  delegated requests, hostile input (Task 4)
  test_admin_cli.py                                    MOD  (Task 5)
README.md                                              MOD  console section (Task 5)
```

---

### Task 1: Console credential store — table, migration 0005, create/revoke/authenticate

**Files:**
- Modify: `packages/leader/src/swarmscribe_leader/db/models.py` (new class after `Follower`)
- Create: `packages/leader/src/swarmscribe_leader/db/migrations/versions/0005_console_credentials.py`
- Create: `packages/leader/src/swarmscribe_leader/auth/consoles.py`
- Modify: `packages/leader/src/swarmscribe_leader/reports.py` (models import; new functions after `list_tokens`)
- Test: `packages/leader/tests/test_migrations.py`, `packages/leader/tests/test_consoles.py` (new)

**Interfaces:**
- Consumes: `hash_secret`, `new_secret` (`auth/secrets.py`); `audit.record`; `Conflict`, `InvalidToken`, `NotFound` (`errors.py`); `Role` (`auth/roles.py`).
- Produces:
  - `db.models.ConsoleCredential` with `id: uuid.UUID`, `created_at`, `name: str`, `credential_hash: str`, `max_role: str`, `created_by: str`, `revoked_at: datetime | None`, `revoked_by: str | None`; table `console_credentials`; CHECK `ck_console_credentials_max_role`.
  - `auth.consoles.InvalidConsoleCredential(InvalidToken)` with class attribute `scheme = "Console"`; subclass `RevokedConsoleCredential(console: str)` with attribute `console` (the revoked console's name, so the refusal can be audited).
  - `async create_console(session, *, name: str, max_role: Role, actor: str) -> tuple[ConsoleCredential, str]` — raises `Conflict(code="exists")`.
  - `async revoke_console(session, name: str, *, now: datetime, actor: str) -> ConsoleCredential` — raises `NotFound`.
  - `async authenticate_console(session, credential: str) -> ConsoleCredential` — raises `InvalidConsoleCredential` (unknown or malformed) or `RevokedConsoleCredential`.
  - `reports.console_view(console) -> dict[str, Any]` with keys `id, name, max_role, revoked, revoked_at, revoked_by, created_by, created_at`; `async reports.list_consoles(session) -> list[dict[str, Any]]` ordered by `created_at, id`.
  - Audit actions `console.create` (detail `{"name", "max_role"}`) and `console.revoke` (detail `{"name"}`), subject type `console_credential`.

- [ ] **Step 1: Write the failing migration tests**

In `packages/leader/tests/test_migrations.py`, replace `test_database_is_at_the_head_revision` with:

```python
async def test_database_is_at_the_head_revision(engine):
    assert head_revision() == "0005"
    assert await current_revision(engine) == "0005"
```

Change the models import line to:

```python
from swarmscribe_leader.db.models import Base, ConsoleCredential, SettingsProfile, StorageLocation
```

Append:

```python
async def test_a_console_credential_cannot_have_an_unknown_role_cap(sessionmaker):
    async with sessionmaker() as session:
        with pytest.raises(IntegrityError):
            await session.execute(
                text(
                    "insert into console_credentials (id, name, credential_hash, max_role,"
                    " created_by) values (gen_random_uuid(), 'fleet', repeat('a', 64),"
                    " 'superuser', 'test')"
                )
            )
        await session.rollback()


async def test_the_console_role_cap_check_matches_the_model(engine):
    (model,) = [
        c for c in ConsoleCredential.__table__.constraints if isinstance(c, CheckConstraint)
    ]
    assert model.name == "ck_console_credentials_max_role"
    definition = (
        "select pg_get_constraintdef(oid) from pg_constraint"
        " where conname = :name and conrelid = '{table}'::regclass"
    )
    async with engine.connect() as conn:
        migrated = await conn.scalar(
            text(definition.format(table="console_credentials")), {"name": model.name}
        )
        await conn.execute(
            text(
                "create temp table console_probe (max_role varchar(16),"
                f" constraint {model.name} check ({model.sqltext.text}))"
            )
        )
        probed = await conn.scalar(
            text(definition.format(table="console_probe")), {"name": model.name}
        )
        await conn.rollback()
    assert migrated is not None
    assert probed == migrated


MIGRATE_0005_SCRIPT = """
import asyncio, sys
import asyncpg
from alembic import command
from swarmscribe_leader.db.migrate import alembic_config, upgrade
url = sys.argv[1]

async def table():
    conn = await asyncpg.connect(url)
    try:
        return await conn.fetchval("select to_regclass('public.console_credentials')::text")
    finally:
        await conn.close()

upgrade(url)
command.downgrade(alembic_config(url), "0004")
assert asyncio.run(table()) is None, "0005 downgrade left the table"
command.upgrade(alembic_config(url), "head")
"""


def test_0005_downgrades_to_0004_and_upgrades_again(database_url):
    # A separate database and process: the shared test database must stay at head.
    name = "swarmscribe_migrate_roundtrip_0005"
    url = urlunsplit(urlsplit(database_url)._replace(path="/" + name))
    asyncio.run(_recreate(database_url, name))
    try:
        run = subprocess.run(
            [sys.executable, "-c", MIGRATE_0005_SCRIPT, url], capture_output=True, timeout=120
        )
        assert run.returncode == 0, run.stderr.decode()[-2000:]

        async def state() -> tuple[str, str | None]:
            conn = await asyncpg.connect(url)
            try:
                revision = await conn.fetchval("select version_num from alembic_version")
                table = await conn.fetchval(
                    "select to_regclass('public.console_credentials')::text"
                )
                return revision, table
            finally:
                await conn.close()

        assert asyncio.run(state()) == (head_revision(), "console_credentials")
    finally:
        asyncio.run(_recreate(database_url, name, drop_only=True))
```

- [ ] **Step 2: Write the failing store tests**

Create `packages/leader/tests/test_consoles.py`:

```python
import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select
from swarmscribe_leader.auth.consoles import (
    InvalidConsoleCredential,
    RevokedConsoleCredential,
    authenticate_console,
    create_console,
    revoke_console,
)
from swarmscribe_leader.auth.secrets import hash_secret
from swarmscribe_leader.clock import utcnow
from swarmscribe_leader.db.models import AuditEntry, ConsoleCredential
from swarmscribe_leader.errors import Conflict, NotFound
from swarmscribe_leader.reports import list_consoles

ADMIN = "admin@example.org (https://issuer.example.org admin-1)"


async def create(sessionmaker, name="fleet", max_role="operator", actor=ADMIN):
    async with sessionmaker() as session:
        console, credential = await create_console(
            session, name=name, max_role=max_role, actor=actor
        )
        await session.commit()
    return console, credential


async def revoke(sessionmaker, name="fleet", *, now=None, actor=ADMIN):
    async with sessionmaker() as session:
        console = await revoke_console(session, name, now=now or utcnow(), actor=actor)
        await session.commit()
    return console


async def authenticated(sessionmaker, credential):
    async with sessionmaker() as session:
        return await authenticate_console(session, credential)


async def audit_rows(sessionmaker, action):
    async with sessionmaker() as session:
        return list(
            (await session.scalars(select(AuditEntry).where(AuditEntry.action == action))).all()
        )


# --- creation -------------------------------------------------------------------------


async def test_a_console_credential_is_shown_once_and_stored_only_as_its_sha256(sessionmaker):
    console, credential = await create(sessionmaker)
    assert len(credential) == 43
    async with sessionmaker() as session:
        row = await session.get(ConsoleCredential, console.id)
    assert (row.name, row.max_role, row.credential_hash, row.created_by) == (
        "fleet",
        "operator",
        hash_secret(credential),
        ADMIN,
    )
    assert (row.revoked_at, row.revoked_by) == (None, None)
    stored = " ".join(str(getattr(row, c.key)) for c in ConsoleCredential.__table__.columns)
    assert credential not in stored


async def test_creation_is_audited_without_the_credential(sessionmaker):
    console, credential = await create(sessionmaker)
    (entry,) = await audit_rows(sessionmaker, "console.create")
    assert (entry.actor, entry.subject_type, entry.subject_id) == (
        ADMIN,
        "console_credential",
        str(console.id),
    )
    assert entry.detail == {"name": "fleet", "max_role": "operator"}
    assert credential not in f"{entry.actor} {entry.subject_id} {entry.detail}"
    assert hash_secret(credential) not in str(entry.detail)


async def test_every_credential_is_different(sessionmaker):
    _, first = await create(sessionmaker, name="fleet-1")
    _, second = await create(sessionmaker, name="fleet-2")
    assert first != second


@pytest.mark.parametrize("again", ["fleet", "FLEET", "Fleet"])
async def test_a_console_name_is_never_reused_ignoring_case(sessionmaker, again):
    await create(sessionmaker, name="fleet")
    with pytest.raises(Conflict) as raised:
        await create(sessionmaker, name=again, max_role="admin")
    assert raised.value.code == "exists"
    assert "'fleet'" in raised.value.message


async def test_a_revoked_consoles_name_is_not_reused(sessionmaker):
    await create(sessionmaker)
    await revoke(sessionmaker)
    with pytest.raises(Conflict):
        await create(sessionmaker)


async def test_two_creations_of_one_name_at_once_create_one(sessionmaker):
    results = await asyncio.gather(
        create(sessionmaker), create(sessionmaker), return_exceptions=True
    )
    assert sorted(type(r).__name__ for r in results) == ["Conflict", "tuple"]
    async with sessionmaker() as session:
        assert len((await session.scalars(select(ConsoleCredential))).all()) == 1


# --- authentication -------------------------------------------------------------------


async def test_a_console_authenticates_with_its_credential(sessionmaker):
    console, credential = await create(sessionmaker)
    found = await authenticated(sessionmaker, credential)
    assert (found.id, found.name, found.max_role) == (console.id, "fleet", "operator")


def _near_miss(credential: str) -> str:
    return credential[:-1] + ("A" if credential[-1] != "A" else "B")


@pytest.mark.parametrize(
    "make",
    [
        _near_miss,
        lambda c: c[:-1],
        lambda c: c + "A",
        lambda c: "x" * 43,
        lambda c: c + " ",
        lambda c: "",
        lambda c: "é" * 43,
        lambda c: "a" * 100_000,
        hash_secret,
    ],
    ids=[
        "near-miss",
        "short",
        "long",
        "random",
        "trailing-space",
        "empty",
        "non-ascii",
        "oversize",
        "its-own-hash",
    ],
)
async def test_anything_but_the_exact_credential_is_unknown(sessionmaker, make):
    _, credential = await create(sessionmaker)
    with pytest.raises(InvalidConsoleCredential) as raised:
        await authenticated(sessionmaker, make(credential))
    assert raised.value.message == "unknown console credential"
    assert (raised.value.status, raised.value.scheme) == (401, "Console")


async def test_a_revoked_console_is_refused(sessionmaker):
    _, credential = await create(sessionmaker)
    await revoke(sessionmaker, "FLEET")
    with pytest.raises(RevokedConsoleCredential) as raised:
        await authenticated(sessionmaker, credential)
    assert raised.value.message == "this console credential has been revoked"
    assert (raised.value.console, raised.value.status) == ("fleet", 401)
    assert isinstance(raised.value, InvalidConsoleCredential)


# --- revocation and the list ----------------------------------------------------------


async def test_revoking_twice_keeps_the_first_revocation(sessionmaker):
    await create(sessionmaker)
    first = utcnow()
    await revoke(sessionmaker, now=first, actor="first-admin")
    again = await revoke(sessionmaker, now=first + timedelta(hours=1), actor="second-admin")
    assert (again.revoked_at, again.revoked_by) == (first, "first-admin")
    entries = await audit_rows(sessionmaker, "console.revoke")
    assert [e.actor for e in entries] == ["first-admin", "second-admin"]
    assert all(e.detail == {"name": "fleet"} for e in entries)


async def test_revoking_an_unknown_console_is_not_found(sessionmaker):
    with pytest.raises(NotFound):
        await revoke(sessionmaker, "nowhere")
    assert await audit_rows(sessionmaker, "console.revoke") == []


async def test_the_list_shows_every_console_without_its_credential_or_hash(sessionmaker):
    await create(sessionmaker, name="fleet-1", max_role="viewer")
    _, credential = await create(sessionmaker, name="fleet-2", max_role="admin")
    await revoke(sessionmaker, "fleet-1")
    async with sessionmaker() as session:
        rows = await list_consoles(session)
    assert [(r["name"], r["max_role"], r["revoked"]) for r in rows] == [
        ("fleet-1", "viewer", True),
        ("fleet-2", "admin", False),
    ]
    assert sorted(rows[0]) == sorted(
        [
            "id",
            "name",
            "max_role",
            "revoked",
            "revoked_at",
            "revoked_by",
            "created_by",
            "created_at",
        ]
    )
    assert credential not in str(rows)
    assert hash_secret(credential) not in str(rows)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m uv run pytest packages/leader/tests/test_migrations.py packages/leader/tests/test_consoles.py -v`
Expected: FAIL — `ImportError: cannot import name 'ConsoleCredential'` (collection error for both files).

- [ ] **Step 4: Add the model**

In `packages/leader/src/swarmscribe_leader/db/models.py`, add after `class Follower` (all names used are already imported):

```python
class ConsoleCredential(_Row, Base):
    """A fleet console's credential, stored as its SHA-256 and capped at a role."""

    __tablename__ = "console_credentials"
    __table_args__ = (
        CheckConstraint(
            "max_role IN ('viewer','operator','admin')", name="ck_console_credentials_max_role"
        ),
    )

    name: Mapped[str] = mapped_column(String(100), unique=True)
    credential_hash: Mapped[str] = mapped_column(String(64), unique=True)
    max_role: Mapped[str] = mapped_column(String(16))
    created_by: Mapped[str] = mapped_column(Text)
    revoked_at: Mapped[datetime | None]
    revoked_by: Mapped[str | None] = mapped_column(Text)
```

- [ ] **Step 5: Write migration 0005**

`packages/leader/src/swarmscribe_leader/db/migrations/versions/0005_console_credentials.py`:

```python
"""fleet console credentials: name, role cap and the credential's SHA-256

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-03

Hand-written.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "console_credentials",
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("credential_hash", sa.String(length=64), nullable=False),
        sa.Column("max_role", sa.String(length=16), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "max_role IN ('viewer','operator','admin')",
            name="ck_console_credentials_max_role",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("credential_hash"),
    )


def downgrade() -> None:
    op.drop_table("console_credentials")
```

- [ ] **Step 6: Write the store**

Create `packages/leader/src/swarmscribe_leader/auth/consoles.py`:

```python
"""Fleet console credentials. A leader administrator creates one per console, with a role
cap; the console calls /v1/admin with it on behalf of a person (api/admin_auth.py).

A credential is 32 random bytes (URL-safe base64, 43 characters), shown once and stored only
as its SHA-256, like a follower credential. It is found by looking its hash up, so the
plaintext is never compared with anything and a guess that shares a prefix with a real
credential is answered exactly like any other guess. Names are unique ignoring case and are
never reused, even after a revocation, because the audit log names consoles by name. Each
change is audited; the caller commits.
"""

import re
import uuid
from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit
from ..db.models import ConsoleCredential
from ..errors import Conflict, InvalidToken, NotFound
from .roles import Role
from .secrets import hash_secret, new_secret

# Serialises console creation, so that two at once cannot both pass the name check.
_CREATE_LOCK = 7_204_511_002
# What new_secret() makes. Anything else is refused before it is hashed or looked up.
_CREDENTIAL = re.compile(r"[A-Za-z0-9_-]{43}")


class InvalidConsoleCredential(InvalidToken):
    """An unknown, malformed or revoked console credential: 401 with
    `WWW-Authenticate: Console error="invalid_token"`."""

    scheme = "Console"


class RevokedConsoleCredential(InvalidConsoleCredential):
    """A revoked console's credential. Carries the console's name, so that the refusal can
    be audited (an unknown credential names no one)."""

    def __init__(self, console: str):
        super().__init__("this console credential has been revoked")
        self.console = console


async def create_console(
    session: AsyncSession, *, name: str, max_role: Role, actor: str
) -> tuple[ConsoleCredential, str]:
    """A new console credential capped at `max_role`. Returns the row and the plaintext,
    which is kept nowhere."""
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _CREATE_LOCK})
    taken = await session.scalar(
        select(ConsoleCredential.name).where(func.lower(ConsoleCredential.name) == name.lower())
    )
    if taken is not None:
        raise Conflict(
            f"a console named {taken!r} already exists; console names are never reused",
            code="exists",
        )
    plaintext = new_secret()
    console = ConsoleCredential(
        id=uuid.uuid4(),
        name=name,
        credential_hash=hash_secret(plaintext),
        max_role=max_role,
        created_by=actor,
    )
    session.add(console)
    audit.record(
        session,
        actor=actor,
        action="console.create",
        subject_type="console_credential",
        subject_id=console.id,
        detail={"name": name, "max_role": max_role},
    )
    return console, plaintext


async def revoke_console(
    session: AsyncSession, name: str, *, now: datetime, actor: str
) -> ConsoleCredential:
    """Refuse the console's credential from its next request on. Revoking again keeps the
    first revocation's time and administrator; every call is audited."""
    console = await session.scalar(
        select(ConsoleCredential)
        .where(func.lower(ConsoleCredential.name) == name.lower())
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if console is None:
        raise NotFound(f"no console named {name!r}")
    if console.revoked_at is None:
        console.revoked_at = now
        console.revoked_by = actor
    audit.record(
        session,
        actor=actor,
        action="console.revoke",
        subject_type="console_credential",
        subject_id=console.id,
        detail={"name": console.name},
    )
    return console


async def authenticate_console(session: AsyncSession, credential: str) -> ConsoleCredential:
    """The console holding `credential`. Nothing is cached, so a revocation applies to the
    console's next request; nothing is locked, so a console's requests never wait on each
    other."""
    if not _CREDENTIAL.fullmatch(credential):
        raise InvalidConsoleCredential("unknown console credential")
    console = await session.scalar(
        select(ConsoleCredential).where(
            ConsoleCredential.credential_hash == hash_secret(credential)
        )
    )
    if console is None:
        raise InvalidConsoleCredential("unknown console credential")
    if console.revoked_at is not None:
        raise RevokedConsoleCredential(console.name)
    return console
```

- [ ] **Step 7: Add the report views**

In `packages/leader/src/swarmscribe_leader/reports.py`, replace the models import with:

```python
from .db.models import (
    ConsoleCredential,
    Follower,
    Job,
    JobAttempt,
    JobResult,
    JoinToken,
    Recording,
    StorageLocation,
)
```

and add after `list_tokens`:

```python
def console_view(console: ConsoleCredential) -> dict[str, Any]:
    """A console credential as administrators see it: never the credential or its hash."""
    return {
        "id": str(console.id),
        "name": console.name,
        "max_role": console.max_role,
        "revoked": console.revoked_at is not None,
        "revoked_at": console.revoked_at,
        "revoked_by": console.revoked_by,
        "created_by": console.created_by,
        "created_at": console.created_at,
    }


async def list_consoles(session: AsyncSession) -> list[dict[str, Any]]:
    consoles = (
        await session.scalars(
            select(ConsoleCredential).order_by(ConsoleCredential.created_at, ConsoleCredential.id)
        )
    ).all()
    return [console_view(console) for console in consoles]
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `python -m uv run pytest packages/leader/tests/test_migrations.py packages/leader/tests/test_consoles.py -v`
Expected: PASS — including `test_migrations_produce_exactly_the_models` (0005 matches the model) and `test_0004_downgrades_to_0003_and_upgrades_again` (its downgrade now passes through 0005).

Run: `python -m uv run ruff check packages/leader`
Expected: no findings.

- [ ] **Step 9: Commit**

```bash
git add packages/leader/src/swarmscribe_leader/db/models.py packages/leader/src/swarmscribe_leader/db/migrations/versions/0005_console_credentials.py packages/leader/src/swarmscribe_leader/auth/consoles.py packages/leader/src/swarmscribe_leader/reports.py packages/leader/tests/test_migrations.py packages/leader/tests/test_consoles.py
git commit -m "Leader: console credentials stored hashed with a role cap (migration 0005)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Delegation headers — parse the actor and role, cap the role

**Files:**
- Modify: `packages/leader/src/swarmscribe_leader/auth/consoles.py` (import block; append a section)
- Test: `packages/leader/tests/test_consoles.py` (import block; append)

**Interfaces:**
- Consumes: `RANK`, `Role` (`auth/roles.py`); `LeaderError` (`errors.py`).
- Produces (all in `auth.consoles`):
  - `ACTOR_HEADER = "x-swarmscribe-actor"`, `ROLE_HEADER = "x-swarmscribe-actor-role"`, `POLLER_ACTOR = "system:poller"`, `MAX_ISSUER_CHARS = 255`, `MAX_SUBJECT_CHARS = 255`, `MAX_EMAIL_CHARS = 254`.
  - `class InvalidActor(LeaderError)` — status 400, code `"invalid_actor"`; `class InvalidActorRole(LeaderError)` — status 400, code `"invalid_actor_role"`.
  - `@dataclass(frozen=True) class DelegatedActor(issuer: str | None, subject: str | None, email: str | None)` with properties `is_poller: bool` (issuer is None) and `name: str` (`"<email or unknown> (<issuer> <subject>)"` or `"system:poller"`); constant `POLLER = DelegatedActor(None, None, None)`.
  - `parse_delegation(actor_values: list[str], role_values: list[str]) -> tuple[DelegatedActor, Role]` — each list is every value of that header (`request.headers.getlist(...)`).
  - `effective_role(asserted: Role, cap: str) -> Role`.

- [ ] **Step 1: Write the failing tests**

In `packages/leader/tests/test_consoles.py`, replace the `from swarmscribe_leader.auth.consoles import (...)` block with:

```python
from swarmscribe_leader.auth.consoles import (
    MAX_EMAIL_CHARS,
    MAX_ISSUER_CHARS,
    MAX_SUBJECT_CHARS,
    POLLER_ACTOR,
    DelegatedActor,
    InvalidActor,
    InvalidActorRole,
    InvalidConsoleCredential,
    RevokedConsoleCredential,
    authenticate_console,
    create_console,
    effective_role,
    parse_delegation,
    revoke_console,
)
```

and append:

```python
# --- delegation headers ---------------------------------------------------------------

ISSUER = "https://login.microsoftonline.com/0f0e0d0c-0b0a-4908-8706-050403020100/v2.0"
PERSON = f"{ISSUER} entra-person-1 person@example.org"


def delegation(actor=PERSON, role="operator"):
    return parse_delegation([] if actor is None else [actor], [] if role is None else [role])


def test_a_person_is_parsed_from_issuer_subject_and_email():
    actor, role = delegation()
    assert actor == DelegatedActor(
        issuer=ISSUER, subject="entra-person-1", email="person@example.org"
    )
    assert role == "operator"
    assert actor.name == f"person@example.org ({ISSUER} entra-person-1)"
    assert actor.is_poller is False


def test_the_email_is_lowercased_like_a_signed_in_persons():
    actor, _ = delegation(f"{ISSUER} entra-person-1 Person@Example.ORG")
    assert actor.email == "person@example.org"


def test_a_person_without_an_email_is_sent_as_a_dash_and_named_unknown():
    actor, _ = delegation(f"{ISSUER} entra-person-1 -")
    assert actor.email is None
    assert actor.name == f"unknown ({ISSUER} entra-person-1)"


def test_the_poller_is_one_word_and_a_viewer():
    actor, role = delegation(POLLER_ACTOR, "viewer")
    assert (actor.is_poller, actor.name, actor.issuer, role) == (
        True,
        "system:poller",
        None,
        "viewer",
    )


@pytest.mark.parametrize("role", ["operator", "admin"])
def test_the_poller_may_not_assert_more_than_viewer(role):
    with pytest.raises(InvalidActorRole):
        delegation(POLLER_ACTOR, role)


def test_parts_at_their_length_limits_are_accepted():
    issuer = "https://" + "a" * (MAX_ISSUER_CHARS - len("https://"))
    subject = "s" * MAX_SUBJECT_CHARS
    email = "a" * (MAX_EMAIL_CHARS - len("@example.org")) + "@example.org"
    actor, _ = delegation(f"{issuer} {subject} {email}")
    assert (len(actor.issuer), len(actor.subject), len(actor.email)) == (255, 255, 254)


BAD_ACTORS = [
    pytest.param(None, id="missing"),
    pytest.param("", id="empty"),
    pytest.param(ISSUER, id="one-part"),
    pytest.param(f"{ISSUER} entra-person-1", id="two-parts"),
    pytest.param(f"{PERSON} extra", id="four-parts"),
    pytest.param(f"{ISSUER}  entra-person-1 person@example.org", id="double-space"),
    pytest.param(f" {PERSON}", id="leading-space"),
    pytest.param(f"{PERSON} ", id="trailing-space"),
    pytest.param(f"{ISSUER}\tentra-person-1\tperson@example.org", id="tabs"),
    pytest.param(f"{PERSON}\r\nX-Injected: 1", id="crlf"),
    pytest.param(f"{ISSUER} entra\nperson person@example.org", id="newline-in-subject"),
    pytest.param(f"{PERSON}\x1b[2J", id="escape"),
    pytest.param(f"{ISSUER} entra\x00 person@example.org", id="nul"),
    pytest.param(f"{ISSUER} entra\x7f person@example.org", id="delete"),
    pytest.param(f"{PERSON} ", id="line-separator"),
    pytest.param(f"{ISSUER} entra-person-1 pérson@example.org", id="non-ascii-email"),
    pytest.param(f"{ISSUER} entra-person-1 person@exämple.org", id="non-ascii-domain"),
    pytest.param(f"https://{'a' * 248} s person@example.org", id="issuer-256"),
    pytest.param(f"{ISSUER} {'s' * 256} person@example.org", id="subject-256"),
    pytest.param(f"{ISSUER} s {'a' * 243}@example.org", id="email-255"),
    pytest.param("http://issuer.example.org s person@example.org", id="http-issuer"),
    pytest.param("accounts.google.com s person@example.org", id="schemeless-issuer"),
    pytest.param("https:// s person@example.org", id="bare-scheme"),
    pytest.param(f"{ISSUER} s not-an-email", id="no-at"),
    pytest.param(f"{ISSUER} s a@b@example.org", id="two-ats"),
    pytest.param(f"{ISSUER} s @example.org", id="empty-local"),
    pytest.param(f"{ISSUER} s person@", id="empty-domain"),
    pytest.param("system:poller extra words", id="poller-with-parts"),
    pytest.param("system:reaper", id="other-system-actor"),
    pytest.param("System:Poller", id="poller-wrong-case"),
]


@pytest.mark.parametrize("actor", BAD_ACTORS)
def test_a_malformed_actor_is_refused_without_echoing_it(actor):
    with pytest.raises(InvalidActor) as raised:
        delegation(actor)
    assert (raised.value.status, raised.value.code) == (400, "invalid_actor")
    if actor:
        assert actor not in raised.value.message


@pytest.mark.parametrize(
    "role",
    [None, "", "superadmin", "Admin", "ADMIN", " admin", "admin ", "admin,viewer", "admin\x00",
     "operatör"],
)
def test_a_missing_or_unknown_role_is_refused(role):
    with pytest.raises(InvalidActorRole) as raised:
        delegation(role=role)
    assert (raised.value.status, raised.value.code) == (400, "invalid_actor_role")


def test_either_header_sent_twice_is_refused():
    with pytest.raises(InvalidActor):
        parse_delegation([PERSON, PERSON], ["viewer"])
    with pytest.raises(InvalidActorRole):
        parse_delegation([PERSON], ["viewer", "admin"])


@pytest.mark.parametrize(
    ("asserted", "cap", "effective"),
    [
        ("admin", "operator", "operator"),
        ("admin", "viewer", "viewer"),
        ("operator", "viewer", "viewer"),
        ("viewer", "admin", "viewer"),
        ("operator", "admin", "operator"),
        ("operator", "operator", "operator"),
        ("admin", "admin", "admin"),
    ],
)
def test_the_effective_role_is_the_lower_of_asserted_and_cap(asserted, cap, effective):
    assert effective_role(asserted, cap) == effective
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m uv run pytest packages/leader/tests/test_consoles.py -v`
Expected: FAIL — `ImportError: cannot import name 'MAX_EMAIL_CHARS' from 'swarmscribe_leader.auth.consoles'`.

- [ ] **Step 3: Implement the parser**

In `packages/leader/src/swarmscribe_leader/auth/consoles.py`, replace the import block with:

```python
import re
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit
from ..db.models import ConsoleCredential
from ..errors import Conflict, InvalidToken, LeaderError, NotFound
from .roles import RANK, Role
from .secrets import hash_secret, new_secret
```

and append to the end of the file:

```python
# --- who a console acts for -------------------------------------------------------------

ACTOR_HEADER = "x-swarmscribe-actor"
ROLE_HEADER = "x-swarmscribe-actor-role"
POLLER_ACTOR = "system:poller"
MAX_ISSUER_CHARS = 255  # OIDC limits sub to 255 ASCII characters; issuers are kept as short
MAX_SUBJECT_CHARS = 255
MAX_EMAIL_CHARS = 254  # RFC 5321
_VISIBLE_ASCII = re.compile(r"[\x21-\x7e]+")
_EMAIL = re.compile(r"[^@]+@[^@]+")


class InvalidActor(LeaderError):
    status = 400
    code = "invalid_actor"


class InvalidActorRole(LeaderError):
    status = 400
    code = "invalid_actor_role"


@dataclass(frozen=True)
class DelegatedActor:
    """The person a console acts for, or (issuer None) the console's own poller."""

    issuer: str | None
    subject: str | None
    email: str | None

    @property
    def is_poller(self) -> bool:
        return self.issuer is None

    @property
    def name(self) -> str:
        """As the audit log names a signed-in person (Identity.actor), or system:poller."""
        if self.issuer is None:
            return POLLER_ACTOR
        return f"{self.email or 'unknown'} ({self.issuer} {self.subject})"


POLLER = DelegatedActor(issuer=None, subject=None, email=None)


def _single(values: list[str], header: str, error: type[LeaderError]) -> str:
    if not values:
        raise error(f"{header} is required on a console request")
    if len(values) > 1:
        raise error(f"send {header} once")
    return values[0]


def parse_delegation(
    actor_values: list[str], role_values: list[str]
) -> tuple[DelegatedActor, Role]:
    """Who a console says it acts for, and the role it asserts for them, from every value
    of X-SwarmScribe-Actor and X-SwarmScribe-Actor-Role. Only visible ASCII passes, so
    nothing that reaches the audit log can carry a line break or a control character.
    Messages are fixed text: header values are never echoed, logged or audited."""
    role = _single(role_values, "X-SwarmScribe-Actor-Role", InvalidActorRole)
    if role not in RANK:
        raise InvalidActorRole("X-SwarmScribe-Actor-Role must be viewer, operator or admin")
    value = _single(actor_values, "X-SwarmScribe-Actor", InvalidActor)
    if value == POLLER_ACTOR:
        if role != "viewer":
            raise InvalidActorRole("the console's poller acts as viewer only")
        return POLLER, "viewer"
    parts = value.split(" ")
    if len(parts) != 3 or not all(_VISIBLE_ASCII.fullmatch(part) for part in parts):
        raise InvalidActor(
            "X-SwarmScribe-Actor must be `<issuer> <subject> <email>` in printable ASCII, "
            "separated by single spaces (email - when unknown), or system:poller"
        )
    issuer, subject, email = parts
    if (
        len(issuer) > MAX_ISSUER_CHARS
        or len(subject) > MAX_SUBJECT_CHARS
        or len(email) > MAX_EMAIL_CHARS
    ):
        raise InvalidActor(
            f"X-SwarmScribe-Actor is too long: the issuer and subject may have at most "
            f"{MAX_ISSUER_CHARS} characters each and the email {MAX_EMAIL_CHARS}"
        )
    if not issuer.startswith("https://") or issuer == "https://":
        raise InvalidActor("the actor's issuer must be an https:// URL")
    if email != "-" and not _EMAIL.fullmatch(email):
        raise InvalidActor("the actor's email must be one address, or - when unknown")
    person = DelegatedActor(
        issuer=issuer, subject=subject, email=None if email == "-" else email.lower()
    )
    return person, role


def effective_role(asserted: Role, cap: str) -> Role:
    """The lower of the role a console asserts and the role its credential is capped at."""
    return asserted if RANK[asserted] <= RANK[cap] else cap
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m uv run pytest packages/leader/tests/test_consoles.py -v`
Expected: PASS.

Run: `python -m uv run ruff check packages/leader`
Expected: no findings.

- [ ] **Step 5: Commit**

```bash
git add packages/leader/src/swarmscribe_leader/auth/consoles.py packages/leader/tests/test_consoles.py
git commit -m "Leader: parse a console's actor and role headers, cap the role

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Admin API — create, list and revoke console credentials

**Files:**
- Modify: `packages/leader/src/swarmscribe_leader/api/admin_models.py` (append three models)
- Modify: `packages/leader/src/swarmscribe_leader/api/admin.py` (imports; three routes at the end)
- Test: `packages/leader/tests/test_admin_api.py` (imports; `ROUTES`; `world`; new section)

**Interfaces:**
- Consumes: `create_console`, `revoke_console` (Task 1, via `from ..auth import consoles`); `reports.console_view`, `reports.list_consoles` (Task 1); `Administrator` dependency alias already in `api/admin.py`.
- Produces:
  - `admin_models.ConsoleRole = Literal["viewer", "operator", "admin"]`; `ConsoleIn(name: str matching NAME_PATTERN, max_role: ConsoleRole)` with `extra="forbid"` and no default for `max_role`; `ConsoleCreated(id, name, max_role, credential)`; `ConsoleOut(id, name, max_role, revoked: bool, revoked_at: datetime | None, revoked_by: str | None, created_by, created_at: datetime)`.
  - `GET /v1/admin/consoles` → `list[ConsoleOut]`, audit `consoles.view`.
  - `POST /v1/admin/consoles` → `201 ConsoleCreated` (the only response carrying a console credential); `409 exists` on a taken name.
  - `POST /v1/admin/consoles/{name}/revoke` → `200 ConsoleOut`; `404` for an unknown name.
  - All three need role `admin` (Task 4 narrows them to persons).

- [ ] **Step 1: Write the failing tests**

In `packages/leader/tests/test_admin_api.py`, replace

```python
from swarmscribe_leader.auth.followers import create_join_token
from swarmscribe_leader.clock import utcnow
from swarmscribe_leader.db.models import (
    AuditEntry,
    Follower,
    Job,
    JobAttempt,
    JoinToken,
    StorageLocation,
)
```

with

```python
from swarmscribe_leader.auth.consoles import create_console
from swarmscribe_leader.auth.followers import create_join_token
from swarmscribe_leader.auth.secrets import hash_secret
from swarmscribe_leader.clock import utcnow
from swarmscribe_leader.db.models import (
    AuditEntry,
    ConsoleCredential,
    Follower,
    Job,
    JobAttempt,
    JoinToken,
    StorageLocation,
)
```

In `ROUTES`, after the line `("POST", "/v1/admin/followers/{follower}/revoke", None, "admin"),` add:

```python
    ("GET", "/v1/admin/consoles", None, "admin"),
    ("POST", "/v1/admin/consoles", {"name": "added-console", "max_role": "viewer"}, "admin"),
    ("POST", "/v1/admin/consoles/{console}/revoke", None, "admin"),
```

Replace the `world` fixture with:

```python
@pytest.fixture
async def world(factory, sessionmaker, tmp_path_factory):
    location = await factory.location(name="here")
    failed = await factory.job(await factory.recording(location, key="talks/a.mp3"), state="failed")
    open_job = await factory.job(await factory.recording(location, key="talks/b.mp3"))
    follower, _ = await factory.follower()
    token_id, _ = await join_token(sessionmaker)
    async with sessionmaker() as session:
        await create_console(session, name="fleet", max_role="admin", actor="test")
        await session.commit()
    return {
        "name": "here",
        "failed_job": failed.id,
        "open_job": open_job.id,
        "follower": follower.id,
        "token": token_id,
        "console": "fleet",
        # a folder apart from the factory location's, which would overlap
        "root": str(tmp_path_factory.mktemp("added-root")),
    }
```

Append to the end of the file:

```python
# --- console credentials --------------------------------------------------------------


async def create_console_via_api(client, idp, name="fleet", max_role="operator"):
    return await post(
        client, idp, "/v1/admin/consoles", "admin", {"name": name, "max_role": max_role}
    )


async def test_an_admin_creates_a_console_credential_shown_once(
    admin_client, idp, sessionmaker, caplog
):
    with caplog.at_level("DEBUG"):
        response = await create_console_via_api(admin_client, idp)
    assert response.status_code == 201, response.text
    created = response.json()
    assert (created["name"], created["max_role"], len(created["credential"])) == (
        "fleet",
        "operator",
        43,
    )
    assert created["credential"] not in caplog.text
    async with sessionmaker() as session:
        row = await session.get(ConsoleCredential, uuid.UUID(created["id"]))
    assert (row.credential_hash, row.created_by) == (
        hash_secret(created["credential"]),
        actor(idp, "admin"),
    )
    listed = await get(admin_client, idp, "/v1/admin/consoles", role="admin")
    assert [(r["name"], r["max_role"], r["revoked"], r["created_by"]) for r in listed] == [
        ("fleet", "operator", False, actor(idp, "admin"))
    ]
    assert created["credential"] not in str(listed)
    assert row.credential_hash not in str(listed)
    (entry,) = await audit_rows(sessionmaker, "console.create")
    assert entry.actor == actor(idp, "admin")
    assert created["credential"] not in f"{entry.actor} {entry.detail}"
    (viewed,) = await audit_rows(sessionmaker, "consoles.view")
    assert viewed.actor == actor(idp, "admin")


async def test_a_duplicate_console_name_is_409_ignoring_case(admin_client, idp):
    assert (await create_console_via_api(admin_client, idp, "fleet")).status_code == 201
    again = await create_console_via_api(admin_client, idp, "Fleet", "admin")
    assert (again.status_code, again.json()["code"]) == (409, "exists")
    listed = await get(admin_client, idp, "/v1/admin/consoles", role="admin")
    assert [(r["name"], r["max_role"]) for r in listed] == [("fleet", "operator")]


async def test_two_creations_of_one_console_at_once_create_one(admin_client, idp):
    responses = await asyncio.gather(
        create_console_via_api(admin_client, idp), create_console_via_api(admin_client, idp)
    )
    assert sorted(r.status_code for r in responses) == [201, 409]


@pytest.mark.parametrize(
    "body, field",
    [
        ({"name": "fleet", "max_role": "superadmin"}, "max_role"),
        ({"name": "fleet", "max_role": "Admin"}, "max_role"),
        ({"name": "fleet"}, "max_role"),
        ({"name": "has space", "max_role": "viewer"}, "name"),
        ({"name": "fleet\n", "max_role": "viewer"}, "name"),
        ({"name": "fleet\x1b[2J", "max_role": "viewer"}, "name"),
        ({"name": "a" * 101, "max_role": "viewer"}, "name"),
        ({"name": "flëet", "max_role": "viewer"}, "name"),
        ({"name": "", "max_role": "viewer"}, "name"),
        ({"name": "fleet", "max_role": "viewer", "credential": "chosen-by-caller"}, "credential"),
    ],
)
async def test_invalid_console_credentials_are_refused(
    admin_client, idp, sessionmaker, body, field
):
    response = await post(admin_client, idp, "/v1/admin/consoles", "admin", body)
    assert (response.status_code, response.json()["code"]) == (422, "invalid_request")
    assert field in response.json()["message"]
    async with sessionmaker() as session:
        assert (await session.scalars(select(ConsoleCredential))).all() == []


async def test_revoking_a_console_shows_it_revoked_and_twice_is_harmless(
    admin_client, idp, sessionmaker
):
    await create_console_via_api(admin_client, idp)
    response = await post(admin_client, idp, "/v1/admin/consoles/FLEET/revoke", "admin")
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["name"], body["revoked"], body["revoked_by"]) == (
        "fleet",
        True,
        actor(idp, "admin"),
    )
    again = await post(admin_client, idp, "/v1/admin/consoles/fleet/revoke", "admin")
    assert (again.status_code, again.json()["revoked_at"]) == (200, body["revoked_at"])
    assert len(await audit_rows(sessionmaker, "console.revoke")) == 2
    (listed,) = await get(admin_client, idp, "/v1/admin/consoles", role="admin")
    assert listed["revoked"] is True


async def test_revoking_an_unknown_console_is_404(admin_client, idp):
    response = await post(admin_client, idp, "/v1/admin/consoles/nowhere/revoke", "admin")
    assert (response.status_code, response.json()["code"]) == (404, "not_found")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m uv run pytest packages/leader/tests/test_admin_api.py -v`
Expected: FAIL — the new tests and the three new `ROUTES` cases with `404 not_found` on `/v1/admin/consoles`, and `test_every_admin_route_is_in_the_role_table` (the table lists routes that are not served).

- [ ] **Step 3: Add the request and response models**

Append to `packages/leader/src/swarmscribe_leader/api/admin_models.py`:

```python
ConsoleRole = Literal["viewer", "operator", "admin"]


class ConsoleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=NAME_PATTERN)
    max_role: ConsoleRole  # no default: the cap is the administrator's decision


class ConsoleCreated(BaseModel):
    id: str
    name: str
    max_role: str
    credential: str


class ConsoleOut(BaseModel):
    id: str
    name: str
    max_role: str
    revoked: bool
    revoked_at: datetime | None
    revoked_by: str | None
    created_by: str
    created_at: datetime
```

- [ ] **Step 4: Add the routes**

In `packages/leader/src/swarmscribe_leader/api/admin.py`, change `from ..auth import followers` to:

```python
from ..auth import consoles, followers
```

and replace the `from .admin_models import (...)` block with:

```python
from .admin_models import (
    ConsentReport,
    ConsoleCreated,
    ConsoleIn,
    ConsoleOut,
    FollowerOut,
    FollowerRevoked,
    FollowerState,
    JobOut,
    JobState,
    LocationIn,
    LocationOut,
    LoginConfig,
    PriorityIn,
    ScanRequested,
    Status,
    TokenCreated,
    TokenIn,
    TokenOut,
    WhoAmI,
)
```

Append to the end of the file:

```python
@router.get("/consoles", response_model=list[ConsoleOut])
async def list_console_credentials(
    admin: Administrator, session: Session
) -> list[ConsoleOut]:
    rows = await reports.list_consoles(session)
    await _viewed(session, admin, "consoles.view")
    return [ConsoleOut.model_validate(row) for row in rows]


@router.post("/consoles", response_model=ConsoleCreated, status_code=201)
async def create_console_credential(
    body: ConsoleIn, admin: Administrator, session: Session
) -> ConsoleCreated:
    """The only response that carries a console credential. Nothing logs response bodies."""
    console, credential = await consoles.create_console(
        session, name=body.name, max_role=body.max_role, actor=admin.actor
    )
    await session.commit()
    return ConsoleCreated(
        id=str(console.id), name=console.name, max_role=console.max_role, credential=credential
    )


@router.post("/consoles/{name}/revoke", response_model=ConsoleOut)
async def revoke_console_credential(
    name: str, admin: Administrator, session: Session
) -> ConsoleOut:
    console = await consoles.revoke_console(session, name, now=utcnow(), actor=admin.actor)
    view = reports.console_view(console)
    await session.commit()
    return ConsoleOut.model_validate(view)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m uv run pytest packages/leader/tests/test_admin_api.py packages/leader/tests/test_consoles.py -v`
Expected: PASS — including `test_each_admin_route_is_refused_for_the_role_below_it[...consoles...]` (operator refused, admin allowed) and `test_every_admin_route_is_in_the_role_table`.

Run: `python -m uv run ruff check packages/leader`
Expected: no findings.

- [ ] **Step 6: Commit**

```bash
git add packages/leader/src/swarmscribe_leader/api/admin_models.py packages/leader/src/swarmscribe_leader/api/admin.py packages/leader/tests/test_admin_api.py
git commit -m "Admin API: create, list and revoke console credentials

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Console requests on /v1/admin — delegated actor, role cap, audit

**Files:**
- Modify: `packages/leader/src/swarmscribe_leader/errors.py` (`Unauthorized`)
- Modify: `packages/leader/src/swarmscribe_leader/api/errors.py` (`leader_error` handler)
- Modify: `packages/leader/src/swarmscribe_leader/api/admin_auth.py` (whole file)
- Modify: `packages/leader/src/swarmscribe_leader/api/admin_models.py` (`WhoAmI`)
- Modify: `packages/leader/src/swarmscribe_leader/api/admin.py` (`_viewed`; `whoami`; console routes person-only)
- Modify: `packages/leader/tests/conftest.py` (models import; `Factory.console`)
- Test: `packages/leader/tests/test_console_auth.py` (new), `packages/leader/tests/test_admin_api.py` (append)

**Interfaces:**
- Consumes: `authenticate_console`, `InvalidConsoleCredential`, `RevokedConsoleCredential`, `parse_delegation`, `effective_role`, `ACTOR_HEADER`, `ROLE_HEADER`, `POLLER_ACTOR` (Tasks 1–2); the three console routes (Task 3).
- Produces:
  - `errors.Unauthorized.scheme: str = "Bearer"` (the `WWW-Authenticate` scheme).
  - `api.admin_auth.Admin` — frozen dataclass `(provider: str, issuer: str | None, subject: str | None, email: str | None, role: Role | None, console: str | None = None)`, classmethod `Admin.person(identity, role)`, property `is_poller -> bool` (a console request whose actor is `system:poller`), property `actor -> str` (`"<email|unknown> (<issuer> <subject>)"` or `"system:poller"`, plus `" via console <name>"` on a console request). `Admin.identity` no longer exists.
  - `api.admin_auth.require(role: Role, *, consoles_allowed: bool = True)`.
  - `api.admin.PersonAdministrator` dependency alias (admin, persons only), used by the three console routes.
  - `WhoAmI.issuer` and `.subject` become `str | None`; new `WhoAmI.console: str | None = None`; `provider` is `"console"` on console requests.
  - `api.admin._viewed` writes no row when `admin.is_poller` (owner ruling); nothing else about auditing changes for the poller.
  - Audit: `console.refused` (actor `console <name>`, detail `{"code"}`) for malformed delegation headers and (code `"revoked"`) for a revoked console's request; `admin.refused` detail gains `asserted` and `cap` for console callers, plus `console_allowed: False` on the person-only routes.
  - Test helper `Factory.console(*, name=None, max_role="admin", revoked=False) -> tuple[ConsoleCredential, str]`.

- [ ] **Step 1: Add the test factory**

In `packages/leader/tests/conftest.py`, change the models import to:

```python
from swarmscribe_leader.db.models import (
    Base,
    ConsoleCredential,
    Follower,
    Job,
    Recording,
    StorageLocation,
)
```

and add to `class Factory`, after `follower`:

```python
    async def console(
        self, *, name=None, max_role="admin", revoked=False
    ) -> tuple[ConsoleCredential, str]:
        credential = new_secret()
        console = ConsoleCredential(
            id=uuid.uuid4(),
            name=name or f"console-{uuid.uuid4().hex[:8]}",
            credential_hash=hash_secret(credential),
            max_role=max_role,
            created_by="test",
            revoked_at=utcnow() if revoked else None,
            revoked_by="test" if revoked else None,
        )
        await self._save(console)
        return console, credential
```

- [ ] **Step 2: Write the failing tests**

Create `packages/leader/tests/test_console_auth.py`:

```python
import uuid

import pytest
from sqlalchemy import func, select
from swarmscribe_leader.auth.secrets import new_secret
from swarmscribe_leader.db.models import AuditEntry, ConsoleCredential, Follower

ISSUER = "https://login.microsoftonline.com/0f0e0d0c-0b0a-4908-8706-050403020100/v2.0"
PERSON = f"{ISSUER} entra-person-7 person.seven@example.org"
PERSON_ACTOR = f"person.seven@example.org ({ISSUER} entra-person-7)"


def console_headers(credential, *, actor=PERSON, role="admin", scheme="Console"):
    headers = {"Authorization": f"{scheme} {credential}"}
    if actor is not None:
        headers["X-SwarmScribe-Actor"] = actor
    if role is not None:
        headers["X-SwarmScribe-Actor-Role"] = role
    return headers


def raw_headers(credential, *, actor=PERSON.encode(), role=b"viewer"):
    """Headers as bytes, so tests can send exactly what a hostile console would."""
    headers = [(b"authorization", f"Console {credential}".encode())]
    if actor is not None:
        headers.append((b"x-swarmscribe-actor", actor))
    if role is not None:
        headers.append((b"x-swarmscribe-actor-role", role))
    return headers


async def audit_rows(sessionmaker, action):
    async with sessionmaker() as session:
        return list(
            (await session.scalars(select(AuditEntry).where(AuditEntry.action == action))).all()
        )


async def all_audit_text(sessionmaker) -> str:
    async with sessionmaker() as session:
        entries = (await session.scalars(select(AuditEntry))).all()
    return " ".join(f"{e.actor} {e.subject_id} {e.detail}" for e in entries)


async def audit_count(sessionmaker) -> int:
    async with sessionmaker() as session:
        return await session.scalar(select(func.count()).select_from(AuditEntry))


# --- acting for a person ----------------------------------------------------------------


async def test_a_console_acts_for_the_person_it_names(admin_client, factory, sessionmaker):
    _, credential = await factory.console(name="fleet", max_role="admin")
    response = await admin_client.get(
        "/v1/admin/whoami", headers=console_headers(credential, role="operator")
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "provider": "console",
        "issuer": ISSUER,
        "subject": "entra-person-7",
        "email": "person.seven@example.org",
        "role": "operator",
        "console": "fleet",
    }
    (entry,) = await audit_rows(sessionmaker, "whoami.view")
    assert entry.actor == f"{PERSON_ACTOR} via console fleet"


async def test_asserted_admin_with_an_operator_cap_acts_as_operator(
    admin_client, factory, sessionmaker
):
    _, credential = await factory.console(name="fleet", max_role="operator")
    headers = console_headers(credential, role="admin")
    me = await admin_client.get("/v1/admin/whoami", headers=headers)
    assert me.json()["role"] == "operator"
    refused = await admin_client.post("/v1/admin/tokens", headers=headers, json={})
    assert (refused.status_code, refused.json()["code"]) == (403, "forbidden")
    assert refused.json()["message"] == (
        "this needs the admin role; console fleet is limited to operator"
    )
    (entry,) = await audit_rows(sessionmaker, "admin.refused")
    assert entry.actor == f"{PERSON_ACTOR} via console fleet"
    assert entry.detail == {
        "role": "operator",
        "required": "admin",
        "asserted": "admin",
        "cap": "operator",
    }
    job = await factory.job()
    cancelled = await admin_client.post(f"/v1/admin/jobs/{job.id}/cancel", headers=headers)
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["cancelled_by"] == f"{PERSON_ACTOR} via console fleet"
    (cancel,) = await audit_rows(sessionmaker, "job.cancel")
    assert cancel.actor == f"{PERSON_ACTOR} via console fleet"


async def test_an_asserted_role_below_the_cap_is_kept(admin_client, factory):
    _, credential = await factory.console(name="fleet", max_role="admin")
    response = await admin_client.post(
        "/v1/admin/tokens", headers=console_headers(credential, role="viewer"), json={}
    )
    assert response.status_code == 403
    assert response.json()["message"] == "this needs the admin role; you have viewer"


async def test_a_person_without_an_email_is_audited_as_unknown(
    admin_client, factory, sessionmaker
):
    _, credential = await factory.console(name="fleet")
    headers = console_headers(credential, actor=f"{ISSUER} entra-person-7 -", role="viewer")
    assert (await admin_client.get("/v1/admin/status", headers=headers)).status_code == 200
    (entry,) = await audit_rows(sessionmaker, "status.view")
    assert entry.actor == f"unknown ({ISSUER} entra-person-7) via console fleet"


async def test_a_refused_change_through_a_console_names_the_person(
    admin_client, factory, sessionmaker
):
    _, credential = await factory.console(name="fleet")
    response = await admin_client.post(
        f"/v1/admin/jobs/{uuid.uuid4()}/cancel",
        headers=console_headers(credential, role="operator"),
    )
    assert response.status_code == 404
    (entry,) = await audit_rows(sessionmaker, "admin.change_refused")
    assert entry.actor == f"{PERSON_ACTOR} via console fleet"


# --- the poller ---------------------------------------------------------------------------


async def test_the_poller_reads_as_a_viewer(admin_client, factory):
    await factory.location(name="here")
    _, credential = await factory.console(name="fleet", max_role="admin")
    poller = console_headers(credential, actor="system:poller", role="viewer")
    status = await admin_client.get("/v1/admin/status", headers=poller)
    assert status.status_code == 200, status.text
    me = (await admin_client.get("/v1/admin/whoami", headers=poller)).json()
    assert (me["provider"], me["issuer"], me["subject"], me["email"], me["role"]) == (
        "console",
        None,
        None,
        None,
        "viewer",
    )
    ingest = await admin_client.post("/v1/admin/locations/here/ingest", headers=poller)
    assert (ingest.status_code, ingest.json()["code"]) == (403, "forbidden")
    greedy = await admin_client.get(
        "/v1/admin/status",
        headers=console_headers(credential, actor="system:poller", role="operator"),
    )
    assert (greedy.status_code, greedy.json()["code"]) == (400, "invalid_actor_role")


async def test_the_pollers_successful_reads_are_not_audited(admin_client, factory, sessionmaker):
    _, credential = await factory.console(name="fleet", max_role="admin")
    poller = console_headers(credential, actor="system:poller", role="viewer")
    before = await audit_count(sessionmaker)
    for path in ("/v1/admin/status", "/v1/admin/followers", "/v1/admin/whoami"):
        response = await admin_client.get(path, headers=poller)
        assert response.status_code == 200, (path, response.text)
    assert await audit_count(sessionmaker) == before


async def test_a_persons_read_through_the_same_console_is_audited(
    admin_client, factory, sessionmaker
):
    _, credential = await factory.console(name="fleet", max_role="admin")
    before = await audit_count(sessionmaker)
    response = await admin_client.get(
        "/v1/admin/status", headers=console_headers(credential, role="viewer")
    )
    assert response.status_code == 200, response.text
    assert await audit_count(sessionmaker) == before + 1
    (entry,) = await audit_rows(sessionmaker, "status.view")
    assert entry.actor == f"{PERSON_ACTOR} via console fleet"


async def test_every_refusal_of_the_poller_is_audited(admin_client, factory, sessionmaker):
    await factory.location(name="here")
    _, credential = await factory.console(name="fleet", max_role="admin")
    _, revoked = await factory.console(name="old", revoked=True)
    poller = console_headers(credential, actor="system:poller", role="viewer")
    answers = [
        # 403: the poller is a viewer
        await admin_client.post("/v1/admin/locations/here/ingest", headers=poller),
        # 400: the poller asserts more than viewer
        await admin_client.get(
            "/v1/admin/status",
            headers=console_headers(credential, actor="system:poller", role="operator"),
        ),
        # 404: a read that fails after the role check
        await admin_client.get(
            "/v1/admin/consent/report", headers=poller, params={"location": "nowhere"}
        ),
        # 401: a revoked console's poller
        await admin_client.get(
            "/v1/admin/status",
            headers=console_headers(revoked, actor="system:poller", role="viewer"),
        ),
    ]
    assert [a.status_code for a in answers] == [403, 400, 404, 401]
    async with sessionmaker() as session:
        entries = (await session.scalars(select(AuditEntry))).all()
    assert sorted((e.action, e.actor, e.detail.get("code", "-")) for e in entries) == [
        ("admin.read_refused", "system:poller via console fleet", "not_found"),
        ("admin.refused", "system:poller via console fleet", "-"),
        ("console.refused", "console fleet", "invalid_actor_role"),
        ("console.refused", "console old", "revoked"),
    ]


# --- the credential -------------------------------------------------------------------------


@pytest.mark.parametrize("which", ["unknown", "revoked", "malformed", "oversize", "follower"])
@pytest.mark.parametrize("with_headers", [True, False], ids=["with-headers", "without-headers"])
async def test_a_credential_that_is_not_valid_is_401_whatever_the_headers_say(
    admin_client, factory, sessionmaker, which, with_headers
):
    _, revoked = await factory.console(name="old", revoked=True)
    _, follower_credential = await factory.follower()
    credential = {
        "unknown": new_secret(),
        "revoked": revoked,
        "malformed": "not a credential",
        "oversize": "a" * 100_000,
        "follower": follower_credential,
    }[which]
    headers = (
        console_headers(credential)
        if with_headers
        else console_headers(credential, actor=None, role=None)
    )
    response = await admin_client.get("/v1/admin/whoami", headers=headers)
    assert (response.status_code, response.json()["code"]) == (401, "unauthorized")
    assert response.headers["www-authenticate"] == 'Console error="invalid_token"'
    assert await audit_rows(sessionmaker, "whoami.view") == []
    refusals = await audit_rows(sessionmaker, "console.refused")
    if which == "revoked":  # a revoked console is named; an unknown credential names no one
        assert [(e.actor, e.detail) for e in refusals] == [("console old", {"code": "revoked"})]
    else:
        assert refusals == []


async def test_a_near_miss_is_answered_exactly_like_a_random_guess(admin_client, factory):
    _, credential = await factory.console(name="fleet")
    near = credential[:-1] + ("A" if credential[-1] != "A" else "B")
    answers = [
        await admin_client.get("/v1/admin/whoami", headers=console_headers(guess))
        for guess in (near, new_secret())
    ]
    assert [a.status_code for a in answers] == [401, 401]
    assert answers[0].content == answers[1].content
    assert answers[0].headers["www-authenticate"] == answers[1].headers["www-authenticate"]


async def test_a_console_revoked_mid_session_is_refused_from_its_next_request(
    admin_client, idp
):
    created = await admin_client.post(
        "/v1/admin/consoles",
        headers=idp.bearer("admin"),
        json={"name": "fleet", "max_role": "viewer"},
    )
    headers = console_headers(created.json()["credential"], role="viewer")
    assert (await admin_client.get("/v1/admin/status", headers=headers)).status_code == 200
    revoked = await admin_client.post(
        "/v1/admin/consoles/fleet/revoke", headers=idp.bearer("admin")
    )
    assert revoked.status_code == 200
    after = await admin_client.get("/v1/admin/status", headers=headers)
    assert (after.status_code, after.json()["message"]) == (
        401,
        "this console credential has been revoked",
    )


@pytest.mark.parametrize("scheme", ["console", "CONSOLE"])
async def test_the_console_scheme_is_matched_ignoring_case(admin_client, factory, scheme):
    _, credential = await factory.console(name="fleet")
    response = await admin_client.get(
        "/v1/admin/whoami", headers=console_headers(credential, scheme=scheme)
    )
    assert response.status_code == 200, response.text


async def test_a_console_credential_sent_as_a_bearer_token_is_401(admin_client, factory):
    _, credential = await factory.console(name="fleet")
    response = await admin_client.get(
        "/v1/admin/whoami", headers=console_headers(credential, scheme="Bearer")
    )
    assert (response.status_code, response.headers["www-authenticate"]) == (
        401,
        'Bearer error="invalid_token"',
    )


# --- hostile delegation headers --------------------------------------------------------------

BAD_ACTORS = [
    pytest.param(None, id="missing"),
    pytest.param(b"", id="empty"),
    pytest.param(f"{ISSUER} entra-person-7".encode(), id="two-parts"),
    pytest.param(f"{PERSON} extra".encode(), id="four-parts"),
    pytest.param(f"{ISSUER}\tentra-person-7\tperson.seven@example.org".encode(), id="tabs"),
    pytest.param(
        f"{ISSUER} entra-person-7\x1b[2J person.seven@example.org".encode(), id="escape"
    ),
    pytest.param(f"{ISSUER} entra-person-7 pérson@example.org".encode(), id="non-ascii-email"),
    pytest.param(f"{ISSUER} {'s' * 256} person.seven@example.org".encode(), id="over-long"),
    pytest.param(b"system:reaper", id="other-system-actor"),
]


@pytest.mark.parametrize("actor", BAD_ACTORS)
async def test_a_malformed_actor_is_400_and_audited_without_its_value(
    admin_client, factory, sessionmaker, actor
):
    _, credential = await factory.console(name="fleet")
    response = await admin_client.get(
        "/v1/admin/whoami", headers=raw_headers(credential, actor=actor)
    )
    assert (response.status_code, response.json()["code"]) == (400, "invalid_actor")
    (entry,) = await audit_rows(sessionmaker, "console.refused")
    assert (entry.actor, entry.subject_type, entry.subject_id, entry.detail) == (
        "console fleet",
        "endpoint",
        "GET /v1/admin/whoami",
        {"code": "invalid_actor"},
    )
    assert await audit_rows(sessionmaker, "whoami.view") == []
    if actor:
        value = actor.decode("latin-1")
        assert value not in response.text
        assert value not in await all_audit_text(sessionmaker)


@pytest.mark.parametrize(
    "role", [None, b"", b"superadmin", b"Admin", b"admin,viewer", "operatör".encode()]
)
async def test_a_missing_or_unknown_actor_role_is_400(admin_client, factory, sessionmaker, role):
    _, credential = await factory.console(name="fleet")
    response = await admin_client.get(
        "/v1/admin/whoami", headers=raw_headers(credential, role=role)
    )
    assert (response.status_code, response.json()["code"]) == (400, "invalid_actor_role")
    (entry,) = await audit_rows(sessionmaker, "console.refused")
    assert entry.detail == {"code": "invalid_actor_role"}


async def test_delegation_headers_sent_twice_are_400(admin_client, factory):
    _, credential = await factory.console(name="fleet")
    auth = (b"authorization", f"Console {credential}".encode())
    person = (b"x-swarmscribe-actor", PERSON.encode())
    twice_actor = [auth, person, person, (b"x-swarmscribe-actor-role", b"viewer")]
    twice_role = [
        auth,
        person,
        (b"x-swarmscribe-actor-role", b"viewer"),
        (b"x-swarmscribe-actor-role", b"admin"),
    ]
    answers = [
        await admin_client.get("/v1/admin/whoami", headers=headers)
        for headers in (twice_actor, twice_role)
    ]
    assert [(a.status_code, a.json()["code"]) for a in answers] == [
        (400, "invalid_actor"),
        (400, "invalid_actor_role"),
    ]


@pytest.mark.parametrize("console_first", [True, False], ids=["console-first", "bearer-first"])
async def test_a_bearer_and_a_console_header_together_are_refused(
    admin_client, idp, factory, sessionmaker, console_first
):
    _, credential = await factory.console(name="fleet")
    pair = [
        (b"authorization", f"Console {credential}".encode()),
        (b"authorization", idp.bearer("admin")["Authorization"].encode()),
    ]
    headers = (pair if console_first else pair[::-1]) + [
        (b"x-swarmscribe-actor", PERSON.encode()),
        (b"x-swarmscribe-actor-role", b"admin"),
    ]
    response = await admin_client.get("/v1/admin/whoami", headers=headers)
    assert (response.status_code, response.json()["code"], response.json()["message"]) == (
        401,
        "unauthorized",
        "send exactly one Authorization header",
    )
    assert await audit_rows(sessionmaker, "whoami.view") == []


async def test_actor_headers_are_ignored_on_a_persons_request(admin_client, idp, sessionmaker):
    headers = {
        **idp.bearer("viewer"),
        "X-SwarmScribe-Actor": PERSON,
        "X-SwarmScribe-Actor-Role": "admin",
    }
    me = await admin_client.get("/v1/admin/whoami", headers=headers)
    assert me.status_code == 200, me.text
    body = me.json()
    assert (body["provider"], body["email"], body["role"], body["console"]) == (
        "entra",
        "viewer@example.org",
        "viewer",
        None,
    )
    refused = await admin_client.post("/v1/admin/tokens", headers=headers, json={})
    assert refused.status_code == 403
    garbage = [
        (b"authorization", idp.bearer("viewer")["Authorization"].encode()),
        (b"x-swarmscribe-actor", b"\x1b[2J nonsense"),
        (b"x-swarmscribe-actor-role", b"superadmin"),
    ]
    assert (await admin_client.get("/v1/admin/status", headers=garbage)).status_code == 200
    entries = await audit_rows(sessionmaker, "whoami.view")
    viewer = f"viewer@example.org ({idp.ENTRA_ISSUER} entra-viewer)"
    assert [e.actor for e in entries] == [viewer]
    assert "via console" not in await all_audit_text(sessionmaker)
    assert await audit_rows(sessionmaker, "console.refused") == []


async def test_actor_headers_alone_are_401(admin_client):
    response = await admin_client.get(
        "/v1/admin/whoami",
        headers={"X-SwarmScribe-Actor": PERSON, "X-SwarmScribe-Actor-Role": "admin"},
    )
    assert (response.status_code, response.headers["www-authenticate"]) == (401, "Bearer")


# --- where a console credential is not accepted --------------------------------------------


@pytest.mark.parametrize("scheme", ["Console", "Bearer"])
async def test_a_console_credential_is_refused_on_follower_routes(
    admin_client, factory, sessionmaker, scheme
):
    _, credential = await factory.console(name="fleet")
    job = "00000000-0000-4000-8000-000000000001"
    for path in ("/v1/jobs/claim", f"/v1/jobs/{job}/heartbeat", "/v1/followers/deregister"):
        response = await admin_client.post(
            path, headers=console_headers(credential, scheme=scheme), json={}
        )
        assert (response.status_code, response.json()["code"]) == (401, "unauthorized"), path
    async with sessionmaker() as session:
        assert (await session.scalars(select(Follower))).all() == []


@pytest.mark.parametrize(
    "method, path, body",
    [
        ("GET", "/v1/admin/consoles", None),
        ("POST", "/v1/admin/consoles", {"name": "minted", "max_role": "admin"}),
        ("POST", "/v1/admin/consoles/fleet/revoke", None),
    ],
    ids=["list", "create", "revoke"],
)
async def test_a_console_cannot_manage_console_credentials_even_as_admin(
    admin_client, factory, sessionmaker, method, path, body
):
    _, credential = await factory.console(name="fleet", max_role="admin")
    response = await admin_client.request(
        method, path, headers=console_headers(credential, role="admin"), json=body
    )
    assert (response.status_code, response.json()["code"]) == (403, "forbidden")
    assert "a console credential cannot do this" in response.json()["message"]
    async with sessionmaker() as session:
        rows = (await session.scalars(select(ConsoleCredential))).all()
    assert [(r.name, r.revoked_at) for r in rows] == [("fleet", None)]
    (entry,) = await audit_rows(sessionmaker, "admin.refused")
    assert entry.actor == f"{PERSON_ACTOR} via console fleet"
    assert entry.detail["console_allowed"] is False


async def test_console_credentials_never_reach_the_log_or_the_audit_log(
    admin_client, factory, sessionmaker, caplog
):
    _, good = await factory.console(name="fleet")
    _, revoked = await factory.console(name="old", revoked=True)
    unknown = new_secret()
    with caplog.at_level("DEBUG"):
        await admin_client.get("/v1/admin/whoami", headers=console_headers(good))
        await admin_client.get("/v1/admin/whoami", headers=console_headers(good, actor="a b"))
        await admin_client.post(
            "/v1/admin/tokens", headers=console_headers(good, role="viewer"), json={}
        )
        await admin_client.get("/v1/admin/whoami", headers=console_headers(revoked))
        await admin_client.get("/v1/admin/whoami", headers=console_headers(unknown))
    audit_text = await all_audit_text(sessionmaker)
    assert "via console fleet" in audit_text
    for credential in (good, revoked, unknown):
        assert credential not in caplog.text
        assert credential not in audit_text
```

Append to `packages/leader/tests/test_admin_api.py`:

```python
# --- a console's delegated requests meet the same role boundary --------------------------

CONSOLE_MANAGEMENT = {"/v1/admin/consoles", "/v1/admin/consoles/{console}/revoke"}
DELEGABLE = [route for route in ROUTES if route[1] not in CONSOLE_MANAGEMENT]


def delegated(credential: str, role: str) -> dict[str, str]:
    return {
        "Authorization": f"Console {credential}",
        "X-SwarmScribe-Actor": "https://issuer.example.org person-1 person@example.org",
        "X-SwarmScribe-Actor-Role": role,
    }


@pytest.mark.parametrize(
    "method, path, body, role", DELEGABLE, ids=[f"{m} {p}" for m, p, _, _ in DELEGABLE]
)
async def test_a_console_meets_each_routes_role_boundary_like_a_person(
    admin_client, factory, world, method, path, body, role
):
    path = path.format(**world)
    if body is not None:
        body = {k: v.format(**world) if isinstance(v, str) else v for k, v in body.items()}
    _, full = await factory.console(name="full", max_role="admin")
    below = BELOW[role]
    if below is not None:
        _, capped = await factory.console(name="capped", max_role=below)
        # asserting the role below, and asserting admin through a cap below
        for headers in (delegated(full, below), delegated(capped, "admin")):
            refused = await admin_client.request(method, path, headers=headers, json=body)
            assert (refused.status_code, refused.json()["code"]) == (403, "forbidden")
    allowed = await admin_client.request(method, path, headers=delegated(full, role), json=body)
    assert allowed.status_code < 400, allowed.text
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m uv run pytest packages/leader/tests/test_console_auth.py packages/leader/tests/test_admin_api.py -v`
Expected: FAIL — console requests are `401` with `WWW-Authenticate: Bearer` ("sign in with `swarmscribe-admin login`…"), and `whoami` has no `console` field.

- [ ] **Step 4: Let `Unauthorized` name its scheme**

In `packages/leader/src/swarmscribe_leader/errors.py`, replace `class Unauthorized` with:

```python
class Unauthorized(LeaderError):
    """No usable credential was sent. Answered with `WWW-Authenticate: <scheme>`: Bearer,
    or Console when a console credential was refused."""

    status = 401
    code = "unauthorized"
    scheme = "Bearer"
    bearer_error: str | None = None
```

In `packages/leader/src/swarmscribe_leader/api/errors.py`, replace

```python
        if isinstance(exc, Unauthorized):  # RFC 6750: say how to authenticate
            error = exc.bearer_error
            headers["WWW-Authenticate"] = f'Bearer error="{error}"' if error else "Bearer"
```

with

```python
        if isinstance(exc, Unauthorized):  # RFC 6750: say how to authenticate
            error, scheme = exc.bearer_error, exc.scheme
            headers["WWW-Authenticate"] = f'{scheme} error="{error}"' if error else scheme
```

- [ ] **Step 5: Accept the Console scheme in `require`**

Replace `packages/leader/src/swarmscribe_leader/api/admin_auth.py` with:

```python
"""Who is calling /v1/admin, and may they?

A person signs in with an ID token (`Authorization: Bearer`). A fleet console calls with its
own credential (`Authorization: Console`) on behalf of the person named in
X-SwarmScribe-Actor, or of its poller; its role is the lower of the asserted role and the
credential's cap, then the same role check applies. The actor headers are read on console
requests only. Refusals are audited; tokens and credentials are never logged or stored."""

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import Request

from .. import audit
from ..auth import consoles
from ..auth.oidc import (
    Fetch,
    Identity,
    MetadataUnavailable,
    TokenVerifier,
    http_fetch,
    providers_from,
)
from ..auth.roles import (
    GoogleCloudIdentity,
    GoogleGroupsClient,
    GraphClient,
    MicrosoftGraph,
    Role,
    RoleLookupFailed,
    RoleMapping,
    RoleResolver,
    at_least,
)
from ..config import Settings
from ..errors import Forbidden, LeaderError, ServiceUnavailable, Unauthorized

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Admin:
    """The caller of an admin route. `provider` is "entra" or "google" for a signed-in
    person and "console" for a console's delegated request; `issuer` is None only for the
    console's poller; `role` is None only before `require` admits the caller."""

    provider: str
    issuer: str | None
    subject: str | None
    email: str | None
    role: Role | None
    console: str | None = None

    @classmethod
    def person(cls, identity: Identity, role: Role | None) -> "Admin":
        return cls(
            provider=identity.provider,
            issuer=identity.issuer,
            subject=identity.subject,
            email=identity.email,
            role=role,
        )

    @property
    def is_poller(self) -> bool:
        """The console's own status poller (fleet console spec 5.3), not a person."""
        return self.console is not None and self.issuer is None

    @property
    def actor(self) -> str:
        """How the audit log names the caller: `<email> (<issuer> <subject>)` (as
        Identity.actor) or `system:poller`, then ` via console <name>` for a console."""
        if self.issuer is None:
            who = consoles.POLLER_ACTOR
        else:
            who = f"{self.email or 'unknown'} ({self.issuer} {self.subject})"
        return who if self.console is None else f"{who} via console {self.console}"


class AdminAuth:
    def __init__(self, verifier: TokenVerifier, resolver: RoleResolver):
        self.verifier = verifier
        self.resolver = resolver

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        fetch: Fetch | None = None,
        graph: GraphClient | None = None,
        google_groups: GoogleGroupsClient | None = None,
        clock: Callable[[], float] = time.time,
    ) -> "AdminAuth":
        """Real directory clients are built only when configured and not given."""
        if graph is None and settings.entra_client_id and settings.entra_client_secret:
            graph = MicrosoftGraph(
                settings.entra_tenant_id, settings.entra_client_id, settings.entra_client_secret
            )
        service_account = settings.google_service_account_key()
        if google_groups is None and service_account is not None:
            google_groups = GoogleCloudIdentity(service_account)
        verifier = TokenVerifier(providers_from(settings), fetch=fetch or http_fetch, clock=clock)
        resolver = RoleResolver(
            RoleMapping.from_settings(settings),
            graph=graph,
            google_groups=google_groups,
            cache_seconds=settings.role_cache_seconds,
        )
        return cls(verifier, resolver)

    async def authenticate(self, token: str) -> tuple[Identity, Role | None]:
        try:
            identity = await self.verifier.verify(token)
            return identity, await self.resolver.role_for(identity)
        except (MetadataUnavailable, RoleLookupFailed) as exc:
            logger.warning("an administrator's sign-in could not be checked: %s", exc)
            raise ServiceUnavailable("sign-in cannot be checked right now; retry shortly") from exc


_SCHEMES = ("bearer", "console")


def _authorization(request: Request) -> tuple[str, str]:
    """The one Authorization header's scheme (lowercase) and credential. Several headers (a
    Bearer and a Console, say) are refused rather than one being picked."""
    values = request.headers.getlist("authorization")
    if len(values) > 1:
        raise Unauthorized("send exactly one Authorization header")
    scheme, _, credential = (values[0] if values else "").partition(" ")
    scheme = scheme.lower()
    if scheme not in _SCHEMES or not credential.strip():
        raise Unauthorized(
            "sign in with `swarmscribe-admin login` and send the ID token as a Bearer token"
        )
    return scheme, credential.strip()


async def _audit(request: Request, *, actor: str, action: str, detail: dict[str, Any]) -> None:
    """An entry about this request in its own session; only the route template is kept."""
    route = request.scope.get("route")
    async with request.app.state.sessionmaker() as session:
        audit.record(
            session,
            actor=actor,
            action=action,
            subject_type="endpoint",
            subject_id=f"{request.method} {getattr(route, 'path', '?')}",
            detail=detail,
        )
        await session.commit()


async def _console_caller(request: Request, credential: str) -> tuple[Admin, dict[str, Any]]:
    """Authenticate the console first (401 for an unknown or revoked credential, whatever
    the headers say), then read whom it acts for. Nothing is cached: a revocation applies
    to the console's next request."""
    try:
        async with request.app.state.sessionmaker() as session:
            console = await consoles.authenticate_console(session, credential)
            name, cap = console.name, console.max_role
    except consoles.RevokedConsoleCredential as exc:
        # A revoked console that keeps calling is worth seeing; an unknown one names no one.
        await _audit(
            request,
            actor=f"console {exc.console}",
            action="console.refused",
            detail={"code": "revoked"},
        )
        raise
    try:
        delegate, asserted = consoles.parse_delegation(
            request.headers.getlist(consoles.ACTOR_HEADER),
            request.headers.getlist(consoles.ROLE_HEADER),
        )
    except LeaderError as exc:
        # A known console sent malformed headers: record that it did, never what it sent.
        await _audit(
            request, actor=f"console {name}", action="console.refused", detail={"code": exc.code}
        )
        raise
    caller = Admin(
        provider="console",
        issuer=delegate.issuer,
        subject=delegate.subject,
        email=delegate.email,
        role=consoles.effective_role(asserted, cap),
        console=name,
    )
    return caller, {"asserted": asserted, "cap": cap}


def _refusal(caller: Admin, required: Role, console: dict[str, Any]) -> str:
    if caller.role is None:
        return "you have no SwarmScribe role; ask an administrator for one"
    if console and not at_least(console["cap"], required):
        cap = console["cap"]
        return f"this needs the {required} role; console {caller.console} is limited to {cap}"
    return f"this needs the {required} role; you have {caller.role}"


_READS = ("GET", "HEAD", "OPTIONS")


async def audit_refused_request(request: Request, code: str) -> None:
    """Record that an admitted caller's request (past the role check) was refused: a change
    for a business reason (admin.change_refused) or a read that failed (admin.read_refused),
    so every admin call leaves an entry. Own session: the request's transaction is rolled
    back. Only the route template and the error code are recorded, never request values."""
    admin = getattr(request.state, "admin", None)
    if admin is None:
        return
    action = "admin.read_refused" if request.method in _READS else "admin.change_refused"
    try:
        await _audit(request, actor=admin.actor, action=action, detail={"code": code})
    except Exception:
        logger.exception("a refused admin request could not be audited")


def require(
    role: Role, *, consoles_allowed: bool = True
) -> Callable[[Request], Awaitable[Admin]]:
    """A dependency admitting callers whose role is `role` or higher: a signed-in person, or
    (unless consoles_allowed is False) a console acting for one."""

    async def dependency(request: Request) -> Admin:
        scheme, credential = _authorization(request)
        console: dict[str, Any] = {}
        if scheme == "console":
            caller, console = await _console_caller(request, credential)
            if not consoles_allowed:
                detail = {
                    "role": caller.role,
                    "required": role,
                    **console,
                    "console_allowed": False,
                }
                await _audit(request, actor=caller.actor, action="admin.refused", detail=detail)
                raise Forbidden(
                    "a console credential cannot do this; "
                    "sign in as a person with `swarmscribe-admin login`"
                )
        else:
            auth: AdminAuth = request.app.state.admin_auth
            identity, granted = await auth.authenticate(credential)
            caller = Admin.person(identity, granted)
        if not at_least(caller.role, role):
            detail = {"role": caller.role, "required": role, **console}
            await _audit(request, actor=caller.actor, action="admin.refused", detail=detail)
            raise Forbidden(_refusal(caller, role, console))
        request.state.admin = caller
        return caller

    return dependency
```

- [ ] **Step 6: Report the console in `whoami` and keep console management to persons**

In `packages/leader/src/swarmscribe_leader/api/admin_models.py`, replace `class WhoAmI` with:

```python
class WhoAmI(BaseModel):
    provider: str  # "entra", "google", or "console" for a console's delegated request
    issuer: str | None  # None only for a console's poller
    subject: str | None
    email: str | None
    role: str
    console: str | None = None
```

In `packages/leader/src/swarmscribe_leader/api/admin.py`, replace `_viewed` with:

```python
async def _viewed(
    session: AsyncSession, admin: Admin, action: str, detail: dict[str, Any] | None = None
) -> None:
    """Audit a successful read. The console's poller reads every 15 seconds, so its
    successful reads are not audited (owner ruling, 2026-10-03); its refusals still are, by
    require(), the console checks and the error handlers."""
    if admin.is_poller:
        return
    audit.record(session, actor=admin.actor, action=action, detail=detail)
    await session.commit()
```

After `Administrator = …` add:

```python
# Console credentials are managed by people only: a console cannot mint or revoke them.
PersonAdministrator = Annotated[Admin, Depends(require("admin", consoles_allowed=False))]
```

replace the `whoami` route with:

```python
@router.get("/whoami", response_model=WhoAmI)
async def whoami(admin: Viewer, session: Session) -> WhoAmI:
    await _viewed(session, admin, "whoami.view")
    return WhoAmI(
        provider=admin.provider,
        issuer=admin.issuer,
        subject=admin.subject,
        email=admin.email,
        role=admin.role,
        console=admin.console,
    )
```

and in the three console routes from Task 3 (`list_console_credentials`, `create_console_credential`, `revoke_console_credential`) change the parameter annotation `admin: Administrator` to `admin: PersonAdministrator`.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python -m uv run pytest packages/leader -v`
Expected: PASS — the new console tests, and every existing admin test unchanged (`test_a_person_with_no_role_is_refused_and_the_refusal_is_audited` still sees detail `{"role": None, "required": "viewer"}`; `test_odd_authorization_headers_are_401`; `test_a_401_without_a_token_says_to_send_a_bearer_token`; `test_an_admin_token_is_not_accepted_on_follower_routes`; `test_each_admin_route_is_refused_for_the_role_below_it`).

Run: `python -m uv run ruff check packages/leader`
Expected: no findings.

- [ ] **Step 8: Commit**

```bash
git add packages/leader/src/swarmscribe_leader/errors.py packages/leader/src/swarmscribe_leader/api/errors.py packages/leader/src/swarmscribe_leader/api/admin_auth.py packages/leader/src/swarmscribe_leader/api/admin_models.py packages/leader/src/swarmscribe_leader/api/admin.py packages/leader/tests/conftest.py packages/leader/tests/test_console_auth.py packages/leader/tests/test_admin_api.py
git commit -m "Leader: console credentials act for a person on /v1/admin, capped and audited

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: `swarmscribe-admin console create|list|revoke` and the README

**Files:**
- Modify: `packages/leader/src/swarmscribe_leader/admin_cli/main.py` (constants, `build_parser`, a renderer, `dispatch`)
- Modify: `README.md` (`### swarmscribe-admin` section)
- Test: `packages/leader/tests/test_admin_cli.py` (models import; append)

**Interfaces:**
- Consumes: `POST /v1/admin/consoles`, `GET /v1/admin/consoles`, `POST /v1/admin/consoles/{name}/revoke` (Tasks 3–4); `LeaderClient`, `print_fields`, `table`, `_cell`, `_seg` in `admin_cli/main.py`.
- Produces: `admin_cli.main.ROLES = ("viewer", "operator", "admin")`; `print_console_credential(data, out)`; commands `console create --name N --max-role R` (both required), `console list`, `console revoke NAME`.

- [ ] **Step 1: Write the failing tests**

In `packages/leader/tests/test_admin_cli.py`, change the models import to:

```python
from swarmscribe_leader.db.models import (
    ConsoleCredential,
    Follower,
    Job,
    JoinToken,
    StorageLocation,
)
```

and append:

```python
# --- console credentials ----------------------------------------------------------------


async def test_console_create_shows_the_credential_once_and_list_never_does(
    cli, store, idp, sessionmaker
):
    sign_in_as(store, idp, "admin")
    code, out, err = await cli("console", "create", "--name", "fleet", "--max-role", "operator")
    assert code == 0, err
    credential = out.splitlines()[0].rsplit(" ", 1)[1]
    assert out.count(credential) == 1
    assert credential not in err
    async with sessionmaker() as session:
        row = (await session.scalars(select(ConsoleCredential))).one()
    assert (row.name, row.max_role, row.credential_hash) == (
        "fleet",
        "operator",
        hash_secret(credential),
    )
    code, listed, _err = await cli("console", "list")
    assert code == 0
    assert "fleet" in listed and "operator" in listed
    assert credential not in listed
    code, revoked, _err = await cli("console", "revoke", "fleet")
    assert code == 0
    assert "revoked: yes" in revoked
    async with sessionmaker() as session:
        assert (await session.get(ConsoleCredential, row.id)).revoked_at is not None


async def test_console_create_of_a_taken_name_is_a_one_line_error(cli, store, idp):
    sign_in_as(store, idp, "admin")
    assert (await cli("console", "create", "--name", "fleet", "--max-role", "viewer"))[0] == 0
    code, out, err = await cli("console", "create", "--name", "FLEET", "--max-role", "admin")
    assert (code, out) == (1, "")
    assert "already exists" in err and "(409 exists)" in err
    assert err.count("\n") == 1


async def test_console_commands_need_the_admin_role(cli, store, idp):
    token = sign_in_as(store, idp, "operator")
    for argv in (
        ("console", "list"),
        ("console", "create", "--name", "fleet", "--max-role", "viewer"),
        ("console", "revoke", "fleet"),
    ):
        code, out, err = await cli(*argv)
        assert (code, out) == (1, ""), argv
        assert "this needs the admin role" in err
        assert token not in err


async def test_console_revoke_of_an_unknown_name_is_an_error(cli, store, idp):
    sign_in_as(store, idp, "admin")
    code, _out, err = await cli("console", "revoke", "nowhere")
    assert code == 1
    assert "(404 not_found)" in err


@pytest.mark.parametrize(
    "argv",
    [
        ["console", "create", "--name", "fleet"],
        ["console", "create", "--max-role", "viewer"],
        ["console", "create", "--name", "fleet", "--max-role", "root"],
        ["console", "create", "--name", "fleet", "--max-role", "Admin"],
        ["console", "revoke"],
        ["console"],
    ],
)
async def test_console_usage_errors_exit_2(cli, store, idp, argv):
    sign_in_as(store, idp, "admin")
    with pytest.raises(SystemExit) as excinfo:
        await cli(*argv)
    assert excinfo.value.code == 2


async def test_a_console_credential_answer_never_reaches_the_terminal_with_control_characters(
    store, idp
):
    sign_in_as(store, idp, "admin")
    answer = {"id": "c1", "name": f"fleet{CONTROL}", "max_role": CONTROL, "credential": CONTROL}
    code, out, err = await run_cli(
        store,
        lambda request: httpx.Response(201, json=answer),
        "console",
        "create",
        "--name",
        "fleet",
        "--max-role",
        "viewer",
    )
    assert code == 0, err
    assert not has_control_characters(out), repr(out)


@pytest.mark.parametrize(
    "argv, answer",
    [
        (["console", "create", "--name", "fleet", "--max-role", "viewer"], {"id": "c1"}),
        (["console", "list"], {"not": "rows"}),
    ],
)
async def test_a_console_answer_of_the_wrong_shape_is_a_one_line_error(store, idp, argv, answer):
    sign_in_as(store, idp, "admin")
    code, out, err = await run_cli(store, lambda request: httpx.Response(200, json=answer), *argv)
    assert (code, out) == (1, "")
    assert err.startswith("error: ") and err.count("\n") == 1, err
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m uv run pytest packages/leader/tests/test_admin_cli.py -v`
Expected: FAIL — `SystemExit: 2` from argparse ("invalid choice: 'console'") in the new tests that expect exit code 0 or 1.

- [ ] **Step 3: Add the commands**

In `packages/leader/src/swarmscribe_leader/admin_cli/main.py`, after `FOLLOWER_STATES = …` add:

```python
ROLES = ("viewer", "operator", "admin")
```

In `build_parser`, after `tokens.add_parser("revoke").add_argument("token_id")` add:

```python
    consoles = commands.add_parser(
        "console", help="fleet console credentials (administrators, signed in as a person)"
    ).add_subparsers(dest="action", required=True)
    console_create = consoles.add_parser(
        "create", help="a new console credential with a role cap (shown once)"
    )
    console_create.add_argument("--name", required=True, help="e.g. fleet")
    console_create.add_argument(
        "--max-role", required=True, choices=ROLES, help="the most this console may do"
    )
    consoles.add_parser("list")
    consoles.add_parser("revoke", help="refuse the console from its next request").add_argument(
        "name"
    )
```

After `print_token` add:

```python
def print_console_credential(data: dict[str, Any], out: TextIO) -> None:
    print(
        f"console credential (shown once; give it to the console): {_cell(data['credential'])}",
        file=out,
    )
    print_fields({name: value for name, value in data.items() if name != "credential"}, out)
```

In `dispatch`, before `if command == "consent":` add:

```python
    if command == "console":
        if action == "create":
            body = {"name": args.name, "max_role": args.max_role}
            return await post("/v1/admin/consoles", body=body), print_console_credential
        if action == "list":
            columns = ("name", "max_role", "revoked", "revoked_at", "created_by", "created_at")
            return await get("/v1/admin/consoles"), table(columns)
        return await post(f"/v1/admin/consoles/{_seg(args.name)}/revoke"), print_fields
```

- [ ] **Step 4: Document the commands**

In `README.md`, in the `### swarmscribe-admin` section, add these three lines to the end of the first command block (after `uv run swarmscribe-admin consent report`):

```
uv run swarmscribe-admin console create --name fleet --max-role operator
uv run swarmscribe-admin console list
uv run swarmscribe-admin console revoke fleet
```

and add at the end of the section (after the paragraph that begins "`ingest` asks for a scan within a minute"):

```markdown
#### Fleet console credentials

A fleet console calls the admin API with its own credential, on behalf of the
person signed in to the console. A leader administrator, signed in as a
person, creates one credential per console with `console create`, choosing the
most the console may do (`--max-role`). The credential is shown once; give it
to the console. A console's request acts with the lower of the role it asserts
for the person and that cap, and the audit log names the person
"`<email> (<issuer> <subject>) via console <name>`". `console revoke` refuses
the console from its next request. Console names are never reused, even after
a revocation: to rotate a credential, create one under a new name, give it to
the console, then revoke the old one. A console credential cannot create, list
or revoke console credentials, and is refused on follower routes.

The console sends `Authorization: Console <credential>`,
`X-SwarmScribe-Actor: <issuer> <subject> <email>` (email `-` when unknown; or
`system:poller` for its status poller, which acts as viewer only) and
`X-SwarmScribe-Actor-Role: viewer|operator|admin`. The actor's parts are
printable ASCII separated by single spaces; the issuer is an `https://` URL of
at most 255 characters, the subject at most 255 characters and the email at
most 254. Malformed headers are refused with `400 invalid_actor` or
`400 invalid_actor_role`. These headers are ignored on a person's requests.
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m uv run pytest packages/leader -v`
Expected: PASS — the new CLI tests and every existing CLI test (`whoami` now also prints `console: -` for a person, which no existing assertion excludes).

Run: `python -m uv run pytest packages -v`
Expected: PASS across protocol, engine and leader.

Run: `python -m uv run ruff check packages`
Expected: no findings.

- [ ] **Step 6: Commit**

```bash
git add packages/leader/src/swarmscribe_leader/admin_cli/main.py packages/leader/tests/test_admin_cli.py README.md
git commit -m "Admin: swarmscribe-admin console create, list and revoke

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Status — followers counted by pool and state, in one query

Independent of Tasks 1–5 (it touches only the status summary); it can be done at any point.

**Files:**
- Modify: `packages/leader/src/swarmscribe_leader/reports.py` (`status_summary`)
- Modify: `packages/leader/src/swarmscribe_leader/api/admin_models.py` (new `PoolFollowers`; `Status`)
- Test: `packages/leader/tests/test_admin_api.py` (sqlalchemy import; append)

**Interfaces:**
- Consumes: `reports.FOLLOWER_STATES`, `status_summary`, `Status` as they are today.
- Produces: `admin_models.PoolFollowers(pool: str, active: int = 0, draining: int = 0, revoked: int = 0, gone: int = 0)`; `Status.follower_pools: list[PoolFollowers]` (default empty), sorted by pool. Every existing `Status` field, and its values, is unchanged.

- [ ] **Step 1: Write the failing tests**

In `packages/leader/tests/test_admin_api.py`, change `from sqlalchemy import select, update` to:

```python
from sqlalchemy import event, select, update
```

and append:

```python
# --- followers by pool in the status ------------------------------------------------------


async def test_status_counts_followers_by_pool_and_state(admin_client, idp, factory):
    for pool, state in [
        ("default", "active"),
        ("default", "active"),
        ("default", "draining"),
        ("gpu", "active"),
        ("gpu", "gone"),
        ("gpu", "revoked"),
    ]:
        await factory.follower(pool=pool, state=state)
    body = await get(admin_client, idp, "/v1/admin/status")
    assert body["follower_pools"] == [
        {"pool": "default", "active": 2, "draining": 1, "revoked": 0, "gone": 0},
        {"pool": "gpu", "active": 1, "draining": 0, "revoked": 1, "gone": 1},
    ]
    # the totals existing clients read are unchanged
    assert body["followers"] == {"active": 3, "draining": 1, "revoked": 1, "gone": 1}


async def test_status_without_followers_has_no_follower_pools(admin_client, idp):
    body = await get(admin_client, idp, "/v1/admin/status")
    assert body["follower_pools"] == []
    assert body["followers"] == {"active": 0, "draining": 0, "revoked": 0, "gone": 0}


async def test_status_reads_followers_with_one_query_however_many_pools(
    admin_app, admin_client, idp, factory
):
    for n in range(6):
        await factory.follower(pool=f"pool-{n}", state="active" if n % 2 else "draining")
    statements: list[str] = []

    def record(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    engine = admin_app.state.engine.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        body = await get(admin_client, idp, "/v1/admin/status")
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert len(body["follower_pools"]) == 6
    assert len([s for s in statements if "FROM followers" in s]) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m uv run pytest packages/leader/tests/test_admin_api.py -k "follower_pools or followers_by_pool or one_query" -v`
Expected: FAIL — `KeyError: 'follower_pools'`.

- [ ] **Step 3: Count followers by pool and state in the one follower query**

In `packages/leader/src/swarmscribe_leader/reports.py`, in `status_summary`, replace

```python
    followers = dict.fromkeys(FOLLOWER_STATES, 0)
    for state, count in (
        await session.execute(select(Follower.state, func.count()).group_by(Follower.state))
    ).all():
        followers[state] = count
```

with

```python
    # One aggregate over (pool, state) gives both the totals and the per-pool counts.
    followers = dict.fromkeys(FOLLOWER_STATES, 0)
    follower_pools: dict[str, dict[str, Any]] = {}
    for pool, state, count in (
        await session.execute(
            select(Follower.pool, Follower.state, func.count()).group_by(
                Follower.pool, Follower.state
            )
        )
    ).all():
        followers[state] = followers.get(state, 0) + count
        row = follower_pools.setdefault(pool, {"pool": pool, **dict.fromkeys(FOLLOWER_STATES, 0)})
        row[state] = row.get(state, 0) + count
```

and in the dictionary it returns, after `"followers": followers,` add:

```python
        "follower_pools": [follower_pools[name] for name in sorted(follower_pools)],
```

- [ ] **Step 4: Add the field to the response model**

In `packages/leader/src/swarmscribe_leader/api/admin_models.py`, add before `class Status`:

```python
class PoolFollowers(BaseModel):
    pool: str
    active: int = 0
    draining: int = 0
    revoked: int = 0
    gone: int = 0
```

and in `class Status`, after `followers: dict[str, int]`, add:

```python
    # Added for the fleet console's poller; additive, so older clients ignore it.
    follower_pools: list[PoolFollowers] = Field(default_factory=list)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m uv run pytest packages/leader -v`
Expected: PASS — the three new tests, and unchanged `test_status_summarises_queue_followers_and_locations`, `test_status_counts_recent_completions_and_failed_attempts`, the CLI's `test_status` and its status shape tests.

Run: `python -m uv run ruff check packages/leader`
Expected: no findings.

- [ ] **Step 6: Commit**

```bash
git add packages/leader/src/swarmscribe_leader/reports.py packages/leader/src/swarmscribe_leader/api/admin_models.py packages/leader/tests/test_admin_api.py
git commit -m "Leader: status counts followers by pool and state in one query

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Spec amendments

Owner rulings of 2026-10-03, amending `2026-10-03-fleet-console-design.md` (and, for auditing, leader spec section 10's "Every admin endpoint writes an audit entry"). Built in this plan:

1. **Poller reads are not audited.** A successful read whose actor is `system:poller` writes no audit row. Every refusal involving the poller is audited (400 `console.refused`, 401 for a revoked console `console.refused`, 403 `admin.refused`, failed reads `admin.read_refused`), and every read and change by a person through a console is audited. (Amends section 4's audit bullet and section 5.3.) — Task 4.
2. **Followers by pool come from the status call.** `GET /v1/admin/status` gains `follower_pools: [{pool, active, draining, revoked, gone}]`, computed in the one follower aggregate query; the poller's "followers/pools summary" (section 5.3) and the overview's "followers active by pool" (section 6) need no other call. Additive: existing fields are unchanged. — Task 6.

Notes for C2 (console side; not C1 work):

3. **Rotation.** Console names are never reused on a leader, even after revocation, because the leader's audit log names consoles by name. To rotate: a leader admin runs `console create` under a new name (say `fleet-2`), the console's `leaders` row (section 5.1) gets the new credential, then `console revoke fleet`. C2 should let a console admin replace a leader's stored credential in place, without re-registering the leader, and should surface a `401 … has been revoked` from a leader as "credential revoked" rather than "unreachable".
4. **Header format the console must send.** `X-SwarmScribe-Actor` is exactly three single-space-separated parts of visible ASCII: an `https://` issuer (≤ 255 characters), the subject (≤ 255), and the email (≤ 254, one `@`; the leader lowercases it) or `-` when the person has no email; or exactly `system:poller`, which must send `X-SwarmScribe-Actor-Role: viewer`. The role header is exactly `viewer`, `operator` or `admin`. Anything else is `400 invalid_actor` / `400 invalid_actor_role`. C2 must therefore send `-` for a person whose email is missing or not ASCII, and refuse to proxy for a person whose issuer or subject contains a space or non-ASCII character (OIDC allows such a `sub`; Entra and Google do not issue one). A JSON or percent-encoded header would lift this limit if it is ever needed.
5. **Trust.** The leader takes the console's word for who the person is; the credential's cap is the only bound, so a leaked `admin`-capped credential is full admin under any name. C2's guidance should recommend `operator` caps unless a console must create join tokens or locations.

## Self-review

**Spec coverage (section 4 and the section 9 "Leader" line).** `console create --name --max-role`, stored hashed with its cap and shown once: Task 1 (store), Task 3 (API), Task 5 (CLI). `console list|revoke`: Tasks 1, 3, 5. `Authorization: Console` with the two actor headers: Tasks 2 and 4. Revoked → 401: Task 1 (`authenticate_console`), Task 4 (HTTP, mid-session). Effective role = lower of asserted and cap: Task 2 (`effective_role`), Task 4 (`test_asserted_admin_with_an_operator_cap_acts_as_operator`, the spec's own example). Same role checks as a person: Task 4 (`test_a_console_meets_each_routes_role_boundary_like_a_person` walks every route in `ROUTES`). Audit actor `<email> (<issuer> <subject>) via console <name>`: Task 4 (reads, changes, refusals, change-refused). Console credentials only on `/v1/admin/*`, never on follower routes: Task 4. Follower credentials and ID tokens unchanged: every existing test in Task 4's full-suite run. Actor headers ignored otherwise: Task 4. Poller `system:poller`/`viewer` (spec 5.3): Tasks 2 and 4. Migration `0005` with `down_revision = "0004"`: Task 1. Owner ruling 1 (poller reads unaudited, its refusals and persons' calls audited): Task 4 (`_viewed`, `Admin.is_poller`, the revoked-console audit, and the three tests named in the ruling). Owner ruling 2 (followers by pool in status, one aggregate, additive): Task 6, with the existing status tests as the compatibility check and a statement-count test for the single query.

**Placeholder scan.** Every code step carries its complete code, every test step its test code, and every run step its command and expected result; no step defers a decision. Code lines were checked against ruff's 100-character limit.

**Type consistency.** `create_console(session, *, name, max_role, actor)`, `revoke_console(session, name, *, now, actor)` and `authenticate_console(session, credential)` are called with those signatures in Tasks 1, 3 and 4. `parse_delegation(actor_values, role_values) -> (DelegatedActor, Role)` and `effective_role(asserted, cap)` match between Task 2 and Task 4's `_console_caller`. `Admin(provider, issuer, subject, email, role, console)` is built only in Task 4; `admin.actor` keeps its name for every existing call site; the one `admin.identity` user (`whoami`) is rewritten in the same task. `require(role, *, consoles_allowed=True)` keeps its positional use (`require("viewer")`) in `api/admin.py` and `test_admin_auth.py`. `Factory.console` returns `(ConsoleCredential, str)` like `Factory.follower`. `RevokedConsoleCredential(console)` is raised in Task 1 and caught by that name in Task 4's `_console_caller`; it subclasses `InvalidConsoleCredential`, so Task 1's and Task 4's 401 assertions hold for it. `Admin.is_poller` (Task 4) is read only by `_viewed`. `Status.follower_pools` items have exactly the keys `PoolFollowers` declares, matching `reports.FOLLOWER_STATES`. Audit actions: `console.create`, `console.revoke`, `consoles.view`, `console.refused`, `admin.refused` — the same strings in code and tests.

**Review Focus.** Each of the 16 lines has a named test in its owning task: 15 in `test_console_auth.py` (`test_the_pollers_successful_reads_are_not_audited`, `test_every_refusal_of_the_poller_is_audited`, `test_a_persons_read_through_the_same_console_is_audited`); 16 in `test_admin_api.py` (Task 6's three status tests); 1–6 in `test_consoles.py` (Task 2) and `test_console_auth.py` (Task 4); 7, 8, 10, 12, 13 in `test_console_auth.py`; 9 in `test_consoles.py::test_anything_but_the_exact_credential_is_unknown[near-miss]` and `test_console_auth.py::test_a_near_miss_is_answered_exactly_like_a_random_guess`; 11 in `test_consoles.py`, `test_admin_api.py` and `test_admin_cli.py`; 14 in both parser and HTTP tests.
