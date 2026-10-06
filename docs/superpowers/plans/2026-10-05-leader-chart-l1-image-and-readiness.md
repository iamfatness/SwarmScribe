# Leader Chart L1 — The Leader Image, and Readiness Through a Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the leader something a Deployment can roll: `/readyz` stays ready on a schema that is newer than the pod (so a pre-upgrade migration no longer takes every serving pod out of service) and no longer waits for an identity provider; and build a real leader image, `docker/leader.Dockerfile`, with a script that checks it, in CI.

**Architecture:** `packages/leader/src/swarmscribe_leader/api/health.py` is rewritten after the console's (`packages/console/src/swarmscribe_console/api/health.py`): one bounded database check, ready when the schema is this leader's or newer. `docker/leader.Dockerfile` is modelled on `docker/console.Dockerfile` (two stages, wheels, user 10001, read-only) with the follower image's init (tini). `docker/check-leader-image.sh` runs the image against a throwaway Postgres. A CI job builds and checks it.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy (asyncpg), Alembic, pytest with a real Postgres; Docker (BuildKit); Bash; GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-05-leader-chart-design.md` — sections 1.1, 1.4 and 1.5 (what exists), 3.3 (rulings R1 to R4, R13), 4 (the image), 6 (readiness), 13 (what the planner ran).

**This plan is the first of three.** L2 (`2026-10-05-leader-chart-l2-chart.md`) is the chart; L3 (`2026-10-05-leader-chart-l3-kind-and-guide.md`) installs it on `kind`. L1 works on its own: after it, a leader can be built with `docker build`, run with `docker run`, and survives a migration run under it.

## Global Constraints

- **Work only in the worktree `C:\Users\walla\SwarmScribe-leader-chart`, on the branch `leader-chart`.** Other agents work in `C:\Users\walla\SwarmScribe`, `SwarmScribe-ui` and `SwarmScribe-f4`: never read-modify, build from or `cd` into them.
- **Another agent uses Docker on this machine.** Never stop, remove or retag a container, an image or a network you did not create; never run any `docker ... prune`; never restart Docker; **never kill a process by name**. Every container this plan starts is named `leader-check-...-<pid>` by the script and removed by it.
- **The free-space floor.** A full disk has corrupted Docker's data on this machine. Before **every** step that builds an image or starts a container, run `bash docker/check-free-space.sh` (Task 2, Step 1 creates it) and stop if it does not print `ok:`. Until it exists, run `df -h /c` and stop under 20 GB.
- **The development machine** is Windows 11 with Git Bash. `uv` is not on the PATH: run it as `python -m uv` (the commands below are written `uv run ...`). Scripts that pass container paths set `MSYS_NO_PATHCONV=1` themselves.
- **Tests use a real Postgres** (README, "Multi-replica test", last paragraph): set `SWARMSCRIBE_TEST_DATABASE_URL`, or leave it unset and an embedded one starts in `.pgdata/`.
- **SwarmScribe is general-purpose.** No fixture, comment or example names a use case or a kind of organisation.
- "Images are built and tested in CI but not published to a registry" (owner). Nothing here pushes an image or adds a registry.
- The image is "slim Python image, no model libraries" (master spec, section 12).
- Commit after every task, on `leader-chart`. Do not push. End every commit message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## What the planner did not run

The planner was allowed no Docker and no test suite (spec, section 13). So:

- `health.py` and `test_health.py` below pass `ruff check` and have **never been run**. Task 1 is their first run. The tests are the specification; if one fails, the code is wrong unless the test contradicts spec section 6, in which case say so in the commit message and fix the test.
- `leader.Dockerfile` has never been built and `check-leader-image.sh` has never been run (`bash -n` passes). Task 2 is their first run. Three places in the script rest on behaviour the planner could not observe, and are marked **(unobserved)** in Task 2: fix the script to match what Docker really does, and never by deleting the case.

## Rulings

Those marked **(owner)** are the owner's to overturn (they are in `2026-10-05-leader-chart-open-questions.md`).

1. **`/readyz` is ready when the schema is this leader's or newer** (spec R1). "Newer" means a revision this leader's migration history does not hold (`is_known_revision`, `db/migrate.py:39-45`), exactly as `serve` already tells "ahead" from "behind" (`main.py:42-46`).
2. **`serve` still refuses to start on a database that is ahead.** Nothing in `main.py` changes.
3. **`/readyz` does not ask the identity provider** (spec R2). **(owner)** If the owner keeps the old rule, restore the third check at the end of `readyz` (`if not await request.app.state.admin_auth.verifier.ready(): ...`), keep `test_readyz_needs_the_sign_in_metadata`, delete `test_readyz_does_not_ask_the_identity_provider`, and say in the chart's guide that a leader pod must reach its identity provider to become Ready.
4. **`/readyz` is bounded** as the console's is: one check at a time, 3 s, reused for 1 s, one log line per cause per 30 s. The leader has no `logsafe` module; the rate limit is three lines inside `ReadinessProbe`.
5. **The answers keep their exact bodies**: `{"status": "ready"}`, `{"status": "database unreachable"}`, `{"status": "database migrations are not current"}`. `{"status": "sign-in metadata unavailable"}` is no longer returned.
6. **HEAD is not added** to the probes. Kubernetes and the image's `HEALTHCHECK` use GET, and the leader, unlike the console, serves no web app that a HEAD could fall through to.
7. **The image has tini as PID 1** (spec R4), from Debian's archive as in the follower image.
8. **The image copies `src/` only**, never `tests/`: a test edit does not rebuild the environment layer (the follower follow-up M9).
9. **`e2e/compose/Dockerfile` stays** and the four tests that use it are not touched (spec R13, follow-up F5).
10. **CI job `leader-image`**: build, then check. L3 turns this job into `leader-kind-e2e`, so the image is built once per run for this purpose.
11. **`docker/check-free-space.sh`** takes its floor in GB as an argument (default 20). CI passes 5: GitHub's runners have about 14 GB free and no Docker data worth protecting.

## Review Focus

1. **The defect itself**: with the database's revision ahead of the leader's, a *running* leader answers 200 on `/readyz` (Task 1, `test_readyz_stays_ready_when_the_database_is_ahead`; Task 2, the check script's "AHEAD" case against the real image) and a *starting* one exits 2 (Task 1, `test_serve_still_refuses_...`; Task 2).
2. **Behind is still not ready**: a leader newer than the database must not take traffic (Task 1; Task 2 sets the revision to `0001`).
3. **Nothing leaks**: the unreachable-database answer and the log line hold neither the database's address nor its password (Task 1); the container's log holds neither the link key nor the password (Task 2).
4. **Anonymous load**: fifty concurrent `/readyz` calls make one database check (Task 1).
5. **What the image must not hold**: engine, model libraries, console, follower, pytest, `uv`, the source tree (Task 2).
6. **A stop is never lost**: the container ends on `docker stop` at once while it waits for a frozen database, and by itself while serving; never 137 (Task 2).

## File Structure

```
packages/leader/src/swarmscribe_leader/
  api/health.py                 (rewrite, Task 1) /healthz, /readyz, ReadinessProbe
  app.py                        (modify, Task 1)  app.state.readiness
packages/leader/tests/
  test_health.py                (create, Task 1)
  test_admin_auth.py            (modify, Task 1)  one test removed
docker/
  check-free-space.sh           (create, Task 2)
  check-leader-image.sh         (create, Task 2)
  leader.Dockerfile             (create, Task 2)
.github/workflows/ci.yml        (modify, Task 3)  job leader-image
README.md                       (modify, Tasks 1 and 4)
docs/superpowers/specs/2026-10-02-leader-design.md        (modify, Task 4) section 12
docs/superpowers/plans/2026-10-03-leader-admin-followups.md (modify, Task 4)
```

---

### Task 1: `/readyz` stays ready through a migration

**Files:**
- Create: `packages/leader/tests/test_health.py`
- Modify: `packages/leader/src/swarmscribe_leader/api/health.py` (replace the whole file)
- Modify: `packages/leader/src/swarmscribe_leader/app.py:93` (one line added after it)
- Modify: `packages/leader/tests/test_admin_auth.py:180-186` (one test removed)
- Modify: `README.md:153-155`

**Interfaces:**
- Consumes: `current_revision(engine)` and `is_known_revision(revision)` (`db/migrate.py:39-53`); `app.state.engine`, `app.state.head_revision` (`app.py:90-93`); the test fixtures `engine`, `migrated_database_url`, `admin_client`, `idp` (`tests/conftest.py`).
- Produces: `GET /healthz` → `200 {"status": "ok"}`. `GET /readyz` → `200 {"status": "ready"}`, or `503 {"status": "database unreachable"}`, or `503 {"status": "database migrations are not current"}`. Both carry `Cache-Control: no-store`. `swarmscribe_leader.api.health.ReadinessProbe(engine)` with `async check(head_revision) -> (status, code)`; module constants `READY_TIMEOUT_SECONDS = 3.0`, `READY_CACHE_SECONDS = 1.0`, `FAILURE_LOG_SECONDS = 30.0`; module function `_database_revision(engine)` (tests replace it). `app.state.readiness`. L2's chart sets its readiness probe's timeout to 5 s because of the 3 s here.

- [ ] **Step 1: Write the failing tests**

Create `packages/leader/tests/test_health.py`:

```python
"""/healthz and /readyz as a Kubernetes rollout needs them (leader chart spec, section 6)."""

import asyncio
import logging
import time

import httpx
import pytest
from sqlalchemy import text
from swarmscribe_leader.api import health
from swarmscribe_leader.app import create_app
from swarmscribe_leader.config import Settings
from swarmscribe_leader.db.migrate import head_revision

LINK_KEY = "k" * 32


@pytest.fixture
async def app(engine, migrated_database_url):
    settings = Settings(
        database_url=migrated_database_url, public_url="http://leader", link_key=LINK_KEY
    )
    application = create_app(settings, background=False)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://leader"
    ) as http:
        yield http


async def test_readyz_stays_ready_when_the_database_is_ahead(client, engine):
    """A rolling upgrade: the migration has run and this older replica is still serving. It
    must stay in service until it is replaced, or every old replica drops out at once."""
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE alembic_version SET version_num = 'newer_than_this'"))
    try:
        answer = await client.get("/readyz")
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE alembic_version SET version_num = :head"),
                {"head": head_revision()},
            )
    assert (answer.status_code, answer.json()) == (200, {"status": "ready"})


async def test_readyz_is_not_ready_when_the_schema_is_behind(app, client):
    # What a leader newer than the database sees: its head is not the database's revision,
    # and the database's revision is one it knows.
    app.state.head_revision = "9999_not_applied_yet"
    answer = await client.get("/readyz")
    assert (answer.status_code, answer.json()) == (
        503,
        {"status": "database migrations are not current"},
    )


async def test_readyz_is_not_ready_before_the_first_migration(client, engine):
    async with engine.begin() as conn:
        await conn.execute(text("ALTER TABLE alembic_version RENAME TO alembic_version_away"))
    try:
        answer = await client.get("/readyz")
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                text("ALTER TABLE alembic_version_away RENAME TO alembic_version")
            )
    assert (answer.status_code, answer.json()) == (
        503,
        {"status": "database migrations are not current"},
    )


async def test_readyz_says_when_the_database_cannot_be_reached(caplog, monkeypatch):
    monkeypatch.setattr(health, "READY_CACHE_SECONDS", 0.0)  # every call checks again
    settings = Settings(
        database_url="postgresql://user:hunter2-db@127.0.0.1:1/none",
        public_url="http://leader",
        link_key=LINK_KEY,
    )
    application = create_app(settings, background=False)
    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://leader"
        ) as made:
            with caplog.at_level(logging.WARNING):
                ready = await made.get("/readyz")
                again = await made.get("/readyz")
            alive = await made.get("/healthz")
    assert (ready.status_code, ready.json()) == (503, {"status": "database unreachable"})
    assert again.status_code == 503
    assert (alive.status_code, alive.json()) == (200, {"status": "ok"})
    # Neither the answer nor the log names the database or its password.
    assert "hunter2-db" not in ready.text + caplog.text
    assert "127.0.0.1" not in ready.text + caplog.text
    lines = [r for r in caplog.records if "readiness check" in r.getMessage()]
    assert len(lines) == 1, "a failing check is logged once per 30 s, not on every probe"


async def test_readyz_does_not_ask_the_identity_provider(admin_client, idp):
    """An identity provider's outage must not take a replica away from the followers. The
    admin API still answers 503 for as long as a sign-in cannot be checked."""
    idp.down = True
    ready = await admin_client.get("/readyz")
    assert (ready.status_code, ready.json()) == (200, {"status": "ready"})
    admin = await admin_client.get("/v1/admin/whoami", headers=idp.bearer("viewer"))
    assert (admin.status_code, admin.json()["code"]) == (503, "unavailable")


async def test_concurrent_readyz_calls_share_one_database_check(client, monkeypatch):
    calls = 0
    real = health._database_revision

    async def counted(engine):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return await real(engine)

    monkeypatch.setattr(health, "_database_revision", counted)
    answers = await asyncio.gather(*(client.get("/readyz") for _ in range(50)))
    assert {answer.status_code for answer in answers} == {200}
    assert calls == 1, "50 anonymous callers ran more than one database check"


async def test_readyz_asks_again_after_the_answer_has_aged(client, monkeypatch):
    calls = 0
    real = health._database_revision

    async def counted(engine):
        nonlocal calls
        calls += 1
        return await real(engine)

    monkeypatch.setattr(health, "_database_revision", counted)
    monkeypatch.setattr(health, "READY_CACHE_SECONDS", 0.0)
    assert (await client.get("/readyz")).status_code == 200
    assert (await client.get("/readyz")).status_code == 200
    assert calls == 2


async def test_readyz_answers_503_on_time_when_the_database_does_not_answer(client, monkeypatch):
    released = asyncio.Event()

    async def frozen(_engine):
        await released.wait()
        return head_revision()

    monkeypatch.setattr(health, "_database_revision", frozen)
    monkeypatch.setattr(health, "READY_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(health, "READY_CACHE_SECONDS", 0.0)
    started = time.monotonic()
    answer = await client.get("/readyz")
    assert (answer.status_code, answer.json()) == (503, {"status": "database unreachable"})
    assert time.monotonic() - started < 2.0
    released.set()


@pytest.mark.parametrize("path", ["/healthz", "/readyz"])
async def test_probe_answers_are_never_cached(client, path):
    assert (await client.get(path)).headers["cache-control"] == "no-store"


def test_serve_still_refuses_to_start_on_a_database_that_is_ahead(monkeypatch, capsys):
    """Staying Ready is for a replica that is already serving. A new one never starts on a
    schema it does not know: an image rolled back after a migration must not come up."""
    from swarmscribe_leader import main as entry

    async def ahead(_engine):
        return "newer_than_this"

    monkeypatch.setenv("SWARMSCRIBE_DATABASE_URL", "postgresql://u:p@127.0.0.1:1/none")
    monkeypatch.setenv("SWARMSCRIBE_PUBLIC_URL", "http://leader")
    monkeypatch.setenv("SWARMSCRIBE_LINK_KEY", LINK_KEY)
    monkeypatch.setattr(entry, "current_revision", ahead)
    assert entry.main(["serve"]) == 2
    assert "database is ahead of this leader" in capsys.readouterr().err
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/leader/tests/test_health.py -q`

Expected: `8 failed, 3 passed`. The three that pass describe behaviour the leader already has (`..._is_not_ready_when_the_schema_is_behind`, `..._before_the_first_migration`, `test_serve_still_refuses_...`). Among the failures: `test_readyz_stays_ready_when_the_database_is_ahead` with `assert (503, {'status': 'database migrations are not current'}) == (200, {'status': 'ready'})` — that is the defect; `test_readyz_does_not_ask_the_identity_provider` with `503 ... 'sign-in metadata unavailable'`; four with `AttributeError: ... has no attribute '_database_revision'` or `'READY_CACHE_SECONDS'`; two with `KeyError: 'cache-control'`.

If the count differs, read each failure before going on: a test that fails for another reason (a fixture, an import) must be understood now, not after the code is written.

- [ ] **Step 3: Rewrite the probes**

Replace the whole of `packages/leader/src/swarmscribe_leader/api/health.py` with:

```python
"""Liveness and readiness, for the container's HEALTHCHECK and Kubernetes probes.

/healthz says the process answers; it never touches the database. /readyz says this replica
can serve: the database answers and its schema is this leader's, or a newer one. A newer
schema means a rolling upgrade's migration has already run; this replica keeps serving until
it is replaced (`serve` itself still refuses to start on a database that is ahead). It does
not ask an identity provider: an identity provider's outage must not take a replica away
from the followers. An administrator's call during such an outage is answered 503 by the
admin API itself.

/readyz is anonymous, so its database use is bounded: at most one check runs at a time and
its answer is reused for READY_CACHE_SECONDS. Concurrent callers wait for that one check."""

import asyncio
import logging
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ..db.migrate import current_revision, is_known_revision

router = APIRouter()
logger = logging.getLogger(__name__)

READY_TIMEOUT_SECONDS = 3.0
READY_CACHE_SECONDS = 1.0
FAILURE_LOG_SECONDS = 30.0  # a failing check is logged at most this often, per cause

READY = ("ready", 200)
UNREACHABLE = ("database unreachable", 503)
NOT_CURRENT = ("database migrations are not current", 503)


async def _database_revision(engine: AsyncEngine) -> str | None:
    """The schema's revision; None when the database answers and has no schema yet. Raises
    when the database cannot be reached."""
    async with engine.connect() as conn:
        await conn.scalar(text("select 1"))
    return await current_revision(engine)


def _consume(task: asyncio.Future) -> None:
    if not task.cancelled():
        task.exception()  # marks it retrieved: the answer was already given


class ReadinessProbe:
    """One database check at a time; its result is reused for READY_CACHE_SECONDS."""

    def __init__(self, engine: AsyncEngine):
        self._engine = engine
        self._lock = asyncio.Lock()
        self._result: tuple[str, int] | None = None
        self._at = 0.0
        self._stuck: asyncio.Future | None = None
        self._logged: dict[str, float] = {}

    async def check(self, head_revision: str) -> tuple[str, int]:
        async with self._lock:
            now = time.monotonic()
            if self._result is None or now - self._at >= READY_CACHE_SECONDS:
                self._result = await self._run(head_revision)
                self._at = time.monotonic()
            return self._result

    def _log(self, cause: str, message: str) -> None:
        """One line per cause every FAILURE_LOG_SECONDS: a probe asks every few seconds."""
        now = time.monotonic()
        if now - self._logged.get(cause, float("-inf")) >= FAILURE_LOG_SECONDS:
            self._logged[cause] = now
            logger.warning(message)

    async def _run(self, head_revision: str) -> tuple[str, int]:
        # The query runs as its own task and is never awaited past the timeout: cancelling a
        # query on a frozen database makes the driver wait for that database (it can take
        # minutes), and the probe must answer 503 in READY_TIMEOUT_SECONDS regardless. At most
        # one such stuck task exists; while it does, the answer is "unreachable" at once.
        if self._stuck is not None and not self._stuck.done():
            return UNREACHABLE
        self._stuck = None
        task = asyncio.ensure_future(_database_revision(self._engine))
        try:
            done, _ = await asyncio.wait({task}, timeout=READY_TIMEOUT_SECONDS)
            if not done:
                task.cancel()
                task.add_done_callback(_consume)
                self._stuck = task
                raise TimeoutError
            revision = task.result()
        except Exception as exc:  # refused, unreachable, bad credentials, query failed, no answer
            # The response stays fixed; the log says which (the exception's type only: its
            # text can hold the database's address).
            self._log(
                "cannot-query",
                f"readiness check cannot query the database: {type(exc).__name__}",
            )
            return UNREACHABLE
        if revision == head_revision:
            return READY
        if revision is None or is_known_revision(revision):
            self._log(
                "not-current",
                f"readiness check: migrations are not current (database is at {revision})",
            )
            return NOT_CURRENT
        return READY  # the database is ahead: a newer leader has migrated it


def _answer(status: str, code: int = 200) -> JSONResponse:
    return JSONResponse({"status": status}, status_code=code, headers={"Cache-Control": "no-store"})


@router.get("/healthz")
async def healthz() -> JSONResponse:
    return _answer("ok")


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    status, code = await request.app.state.readiness.check(request.app.state.head_revision)
    return _answer(status, code)
```

- [ ] **Step 4: Give the app its probe**

In `packages/leader/src/swarmscribe_leader/app.py`, after the line `app.state.head_revision = head_revision()` (line 93), add:

```python
    app.state.readiness = health.ReadinessProbe(engine)
```

(`health` is already imported at the top of the file: `from .api import admin, files, follower, health`.)

- [ ] **Step 5: Remove the test of the old rule**

In `packages/leader/tests/test_admin_auth.py`, delete the whole test `test_readyz_needs_the_sign_in_metadata` (lines 180-186, with the two blank lines after it). Keep `test_readyz_is_ready_once_the_metadata_is_fetched` below it, and keep the `ready.status_code == 200` assertion in `test_a_leader_without_sign_in_refuses_admin_calls`: both still hold.

- [ ] **Step 6: Run the tests**

Run: `uv run pytest packages/leader/tests/test_health.py packages/leader/tests/test_app.py packages/leader/tests/test_admin_auth.py packages/leader/tests/test_main.py -q`

Expected: all pass, no warning about a task exception that was never retrieved. In `test_app.py`, `test_readyz_when_migrated` and `test_readyz_is_503_when_migrations_are_behind` pass unchanged.

Then the leader's whole package and the linter:

Run: `uv run pytest packages/leader -q` — Expected: all pass.
Run: `uv run ruff check .` — Expected: `All checks passed!`

- [ ] **Step 7: Say it in the README**

In `README.md`, replace the paragraph at lines 153-155 (it starts "`/readyz` reports ready once each configured provider's signing keys have been") with:

```markdown
`/readyz` answers for the database alone: 200 when the database answers and its schema is
this leader's, or a newer one (a rolling upgrade's migration has run and this replica is
still serving), and 503 otherwise, within 3 seconds. It does not ask an identity provider:
while one cannot be reached, followers are served as usual and an administrator's call is
answered `503` with `Retry-After`. `/healthz` says only that the process answers. A leader
never *starts* on a database that is ahead of it.
```

- [ ] **Step 8: Commit**

```bash
git add packages/leader/src/swarmscribe_leader/api/health.py packages/leader/src/swarmscribe_leader/app.py packages/leader/tests/test_health.py packages/leader/tests/test_admin_auth.py README.md
git commit -m "Leader: /readyz stays ready on a newer schema and no longer waits for sign-in

A pre-upgrade migration made every serving replica unready at once: the
check compared the database's revision with this leader's head for
equality. Ready now means the schema is this leader's or newer; serve
still refuses to start on a newer one. The check is bounded (one at a
time, 3 s, reused for 1 s) and no longer asks the identity provider.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The `swarmscribe-leader` image

**Files:**
- Create: `docker/check-free-space.sh`
- Create: `docker/check-leader-image.sh`
- Create: `docker/leader.Dockerfile`

**Interfaces:**
- Consumes: Task 1's `/readyz`; `swarmscribe-leader migrate | serve` (`main.py`); the base image digest in `docker/console.Dockerfile:23`; `uv.lock`.
- Produces: `docker build -t <tag> -f docker/leader.Dockerfile .` → an image with entrypoint `["/usr/bin/tini", "--", "swarmscribe-leader"]`, default arguments `serve --host 0.0.0.0 --port 8080`, user `10001:10001`, port 8080, `sleep` on the PATH, no writable path needed. `bash docker/check-leader-image.sh <image>` → last line `ok: <image> holds the leader and swarmscribe-admin, without the engine, console or follower (<N> MB)`, or `FAILED: ...` and exit 1. `bash docker/check-free-space.sh [GB]` → `ok: ...` or `STOP: ...` and exit 1. L2's chart relies on the entrypoint, the user, port 8080 and `sleep`; L3 builds on this image.

- [ ] **Step 1: The free-space check**

Create `docker/check-free-space.sh`:

```bash
#!/usr/bin/env bash
# Stop before Docker or kind fills the disk: a full disk has corrupted Docker's data on the
# development machine. Run before every step that builds an image, loads one into a
# cluster or creates a cluster:
#
#   bash docker/check-free-space.sh        # at least 20 GB free
#   bash docker/check-free-space.sh 30     # another floor, in GB
#
# On Windows (Git Bash) the disk checked is C:; elsewhere it is the one holding /.
set -euo pipefail

floor="${1:-20}"
case "$floor" in '' | *[!0-9]*) echo "usage: check-free-space.sh [GB]" >&2; exit 2 ;; esac
if [ -d /c/Windows ]; then disk=/c; name="C:"; else disk=/; name="/"; fi
# POSIX output: one line per filesystem, available space in 1024-byte blocks in column 4.
free_kb="$(df -Pk "$disk" | awk 'NR == 2 { print $4 }')"
case "$free_kb" in '' | *[!0-9]*) echo "FAILED: cannot read the free space of $name" >&2; exit 1 ;; esac
free_gb=$((free_kb / 1024 / 1024))
if [ "$free_gb" -lt "$floor" ]; then
  echo "STOP: $name has $free_gb GB free, under the floor of $floor GB. Free some space first;" >&2
  echo "do not run Docker or kind until this passes." >&2
  exit 1
fi
echo "ok: $name has $free_gb GB free (floor: $floor GB)"
```

Run: `bash docker/check-free-space.sh`
Expected: `ok: C: has <N> GB free (floor: 20 GB)` with N at least 20. **If it prints `STOP:`, stop this task and report it; do not free space by pruning Docker.**

Run: `bash docker/check-free-space.sh 100000; echo "exit $?"`
Expected: two `STOP:` lines and `exit 1`.

- [ ] **Step 2: Write the image check (the failing check)**

Create `docker/check-leader-image.sh`:

```bash
#!/usr/bin/env bash
# What the swarmscribe-leader image must hold, checked with a throwaway Postgres and no
# follower:
#
#   bash docker/check-leader-image.sh swarmscribe-leader:e2e
#
# The kind test (e2e/leader-kind) checks the rest by running it in a cluster: uploads and
# downloads under a read-only root filesystem, a rolling upgrade, a NetworkPolicy.
set -euo pipefail
# Git Bash on Windows would rewrite /usr/bin/tini and friends into Windows paths.
export MSYS_NO_PATHCONV=1

image="${1:?usage: check-leader-image.sh <image>}"
locked=(--read-only --cap-drop ALL --security-opt no-new-privileges)

fail() {
  echo "FAILED: $*" >&2
  exit 1
}

label() {  # the value of one of the image's labels; empty when it is not set
  docker inspect --format "{{with .Config.Labels}}{{index . \"$1\"}}{{end}}" "$image"
}

# --- what it says it is --------------------------------------------------------------------
[ "$(label org.opencontainers.image.title)" = "swarmscribe-leader" ] \
  || fail "the title label is '$(label org.opencontainers.image.title)', not swarmscribe-leader"
[ "$(label org.opencontainers.image.source)" = "https://github.com/iamfatness/SwarmScribe" ] \
  || fail "the source label is '$(label org.opencontainers.image.source)', not the repository"
case "$(label org.opencontainers.image.description)" in
  *SwarmScribe*leader*) ;;
  *) fail "the description label is '$(label org.opencontainers.image.description)'" ;;
esac
installed="$(docker run --rm --entrypoint python "$image" -c \
  'from importlib.metadata import version; print(version("swarmscribe-leader"))')"
[ -n "$installed" ] || fail "the image does not say which version of the leader it holds"
[ "$(label org.opencontainers.image.version)" = "$installed" ] \
  || fail "the version label is '$(label org.opencontainers.image.version)', the leader in the image is $installed"

# --- who it runs as, and what is PID 1 ---------------------------------------------------
user="$(docker inspect --format '{{.Config.User}}' "$image")"
[ "$user" = "10001:10001" ] || fail "the image's user is '$user', not 10001:10001"
[ "$(docker run --rm --entrypoint id "$image" -u)" = "10001" ] \
  || fail "the container does not run as uid 10001"
entrypoint="$(docker inspect --format '{{json .Config.Entrypoint}}' "$image")"
[ "$entrypoint" = '["/usr/bin/tini","--","swarmscribe-leader"]' ] \
  || fail "the entrypoint is $entrypoint, not tini and the leader"
docker run --rm --entrypoint /usr/bin/tini "$image" --version >/dev/null \
  || fail "tini does not run in the image"
docker inspect --format '{{json .Config.Healthcheck}}' "$image" | grep -q '/healthz' \
  || fail "the image has no HEALTHCHECK on /healthz"
if docker inspect --format '{{json .Config.Healthcheck}}' "$image" | grep -q '/readyz'; then
  fail "the HEALTHCHECK asks /readyz: a database outage would mark every leader unhealthy"
fi
# The chart's preStop hook runs `sleep`.
docker run --rm --entrypoint sleep "$image" 0 || fail "there is no sleep in the image (the chart's preStop hook)"

# --- what it holds, and what it must not ---------------------------------------------------
# The leader imports the protocol package and nothing else of SwarmScribe; the engine and
# its model libraries, the console and the follower must never come with it (master spec,
# section 12: "slim Python image, no model libraries").
docker run --rm --entrypoint python "$image" -c '
import importlib.util
import sys

needed = ("swarmscribe_leader", "swarmscribe_protocol", "alembic", "asyncpg", "uvicorn")
missing = [name for name in needed if not importlib.util.find_spec(name)]
banned = ("swarmscribe_engine", "swarmscribe_console", "swarmscribe_follower", "faster_whisper",
          "ctranslate2", "av", "onnxruntime", "torch", "numpy", "tokenizers", "huggingface_hub",
          "pytest")
found = [name for name in banned if importlib.util.find_spec(name)]
sys.exit(f"missing: {missing}; must not be there: {found}" if missing or found else 0)
' || fail "the image does not hold exactly the leader and its libraries"
# The migrations travel inside the wheel: without them `migrate` has nothing to run.
docker run --rm --entrypoint python "$image" -c '
import sys
from swarmscribe_leader.db.migrate import MIGRATIONS, head_revision
versions = sorted(p.name for p in (MIGRATIONS / "versions").glob("*.py"))
sys.exit(0 if versions and head_revision() and (MIGRATIONS / "script.py.mako").is_file() else
         f"migrations in the image: {versions}")
' || fail "the migrations are not in the image"
if docker run --rm --entrypoint sh "$image" -c 'command -v uv || test -e /app/packages || test -e /app/uv.lock' >/dev/null; then
  fail "build tools or the source tree are in the final image"
fi

# --- without configuration, read-only, no network ---------------------------------------
status=0
output="$(docker run --rm "${locked[@]}" --network none "$image" migrate 2>&1)" || status=$?
[ "$status" = "2" ] || fail "migrate without configuration exited $status, not 2"
grep -q 'invalid configuration' <<<"$output" \
  || fail "migrate without configuration did not say so: $output"
if grep -q 'Traceback' <<<"$output"; then
  fail "migrate without configuration ended in a traceback"
fi
docker run --rm "${locked[@]}" --network none "$image" --help >/dev/null \
  || fail "the leader does not run with --read-only --cap-drop ALL"
docker run --rm "${locked[@]}" --network none --entrypoint swarmscribe-admin "$image" --help >/dev/null \
  || fail "swarmscribe-admin does not run in the image"

# --- with a database ----------------------------------------------------------------------
# A throwaway Postgres on a network of its own; the leader reaches it by its container
# name, and nothing is published on the host.
pg="leader-check-pg-$$"
name="leader-check-$$"
net="leader-check-net-$$"
cleanup() {
  docker rm -f "$name" "$name-ahead" "$name-early" "$pg" >/dev/null 2>&1 || true
  docker network rm "$net" >/dev/null 2>&1 || true
}
trap cleanup EXIT
docker network create "$net" >/dev/null
docker run -d --name "$pg" --network "$net" -e POSTGRES_PASSWORD=pw -e POSTGRES_DB=leader \
  postgres:16 >/dev/null
for _ in $(seq 1 60); do
  docker exec "$pg" pg_isready -h 127.0.0.1 -U postgres >/dev/null 2>&1 && break
  sleep 1
done
leader_env=(-e "SWARMSCRIBE_DATABASE_URL=postgresql://postgres:pw@$pg:5432/leader"
  -e SWARMSCRIBE_PUBLIC_URL=http://localhost:8080
  -e SWARMSCRIBE_LINK_KEY=check-only-link-key-0123456789abcdef)
sql() { docker exec "$pg" psql -U postgres -d leader -t -A -c "$1"; }

# A leader never starts on a database it has not migrated.
status=0
output="$(docker run --rm "${locked[@]}" --network "$net" "${leader_env[@]}" "$image" serve 2>&1)" || status=$?
[ "$status" = "2" ] || fail "serve on an unmigrated database exited $status, not 2"
grep -q 'run `swarmscribe-leader migrate`' <<<"$output" \
  || fail "serve on an unmigrated database did not say to migrate: $output"

output="$(docker run --rm "${locked[@]}" --network "$net" "${leader_env[@]}" "$image" migrate 2>&1)" \
  || fail "migrate against a database failed: $output"
head="$(sql 'select version_num from alembic_version')"
grep -q -x "database is at revision $head" <<<"$output" \
  || fail "migrate did not report the revision it reached ($head): $output"
# A second run finds the schema current and does nothing.
docker run --rm "${locked[@]}" --network "$net" "${leader_env[@]}" "$image" migrate >/dev/null \
  || fail "a second migrate failed"

docker run -d --name "$name" "${locked[@]}" --network "$net" "${leader_env[@]}" \
  "$image" serve >/dev/null
probe() {  # path -> the status code, asked from inside the container
  docker exec "$name" python -c "
import sys, urllib.request, urllib.error
try:
    print(urllib.request.urlopen('http://127.0.0.1:8080' + sys.argv[1], timeout=8).status)
except urllib.error.HTTPError as e:
    print(e.code)
" "$1"
}
for _ in $(seq 1 30); do [ "$(probe /healthz 2>/dev/null)" = "200" ] && break; sleep 1; done
[ "$(probe /healthz)" = "200" ] || fail "/healthz is not 200: $(docker logs "$name" 2>&1 | tail -5)"
[ "$(probe /readyz)" = "200" ] || fail "/readyz is not 200 on a migrated database"
[ "$(probe /v1/admin/login-config)" = "200" ] || fail "/v1/admin/login-config is not 200"
# PID 1 is the init, and the leader is what it started.
pid1="$(docker exec "$name" cat /proc/1/cmdline | xargs -0 echo)"
grep -q -x '/usr/bin/tini -- swarmscribe-leader serve' <<<"$pid1" \
  || fail "PID 1 is not tini running the leader: $pid1"

# A rolling upgrade: a newer leader has migrated the database. The one that is serving stays
# Ready (or every old replica would drop out of the Service at once) ...
sql "update alembic_version set version_num = '9999_newer_leader'" >/dev/null
sleep 2  # past the one-second reuse of the last readiness answer
[ "$(probe /readyz)" = "200" ] || fail "/readyz is not 200 when the database is AHEAD of this leader"
# ... and one that is starting refuses to: an image rolled back after a migration stays down.
status=0
output="$(docker run --name "$name-ahead" "${locked[@]}" --network "$net" "${leader_env[@]}" "$image" serve 2>&1)" || status=$?
[ "$status" = "2" ] || fail "serve on a database that is ahead exited $status, not 2"
grep -q 'database is ahead of this leader' <<<"$output" \
  || fail "serve on a database that is ahead did not say so: $output"
# A schema that is behind is not ready: 0001 is a revision every leader knows.
sql "update alembic_version set version_num = '0001'" >/dev/null
sleep 2
[ "$(probe /readyz)" = "503" ] || fail "/readyz is not 503 when the database is BEHIND this leader"
sql "update alembic_version set version_num = '$head'" >/dev/null
sleep 2
[ "$(probe /readyz)" = "200" ] || fail "/readyz did not come back to 200"

# A database that stops answering: 503 inside the probe's timeout, and the process stays up.
docker pause "$pg" >/dev/null
sleep 2
started=$SECONDS
[ "$(probe /readyz)" = "503" ] || { docker unpause "$pg" >/dev/null; fail "/readyz is not 503 while the database is down"; }
[ $((SECONDS - started)) -le 6 ] || { docker unpause "$pg" >/dev/null; fail "/readyz took more than 6 s to say the database is down (its own limit is 3 s)"; }
[ "$(probe /healthz)" = "200" ] || { docker unpause "$pg" >/dev/null; fail "/healthz is not 200 while the database is down"; }

# A stop while a leader is still waiting for that database: tini forwards the signal and the
# container ends at once (143), not when the stop window closes (137).
docker run -d --name "$name-early" "${locked[@]}" --network "$net" "${leader_env[@]}" \
  "$image" serve >/dev/null
sleep 2
started=$SECONDS
docker stop --time 30 "$name-early" >/dev/null
took=$((SECONDS - started))
status="$(docker inspect --format '{{.State.ExitCode}}' "$name-early")"
docker unpause "$pg" >/dev/null
case "$status" in
  143 | 0 | 2) ;;
  *) fail "a stop while waiting for the database exited $status (137: the stop was lost and it was killed)" ;;
esac
[ "$took" -lt 10 ] || fail "a stop while waiting for the database took $took s"

# A stop while serving: the leader ends by itself, well inside the window. 0, or 143 when
# uvicorn, having finished its requests, hands the signal back to the default action; never
# 137, which is Docker's kill at the end of the window.
for _ in $(seq 1 15); do [ "$(probe /readyz 2>/dev/null)" = "200" ] && break; sleep 1; done
started=$SECONDS
docker stop --time 30 "$name" >/dev/null
took=$((SECONDS - started))
status="$(docker inspect --format '{{.State.ExitCode}}' "$name")"
case "$status" in
  0 | 143) ;;
  *) fail "docker stop while serving exited $status, not 0 or 143 (137 is a kill)" ;;
esac
[ "$took" -lt 15 ] || fail "docker stop while serving took $took s"
# No secret in what it logged: neither the link key nor the database password.
if docker logs "$name" 2>&1 | grep -q -e 'check-only-link-key' -e ':pw@'; then
  fail "the leader's log holds the link key or the database password"
fi

size="$(docker image inspect --format '{{.Size}}' "$image")"
echo "ok: $image holds the leader and swarmscribe-admin, without the engine, console or follower ($((size / 1000000)) MB)"
```

Run: `bash -n docker/check-leader-image.sh` — Expected: no output.

- [ ] **Step 3: See it fail on the test image**

The only leader image that exists is the Compose tests'. It must fail the check, on its first line.

```bash
bash docker/check-free-space.sh
docker build -t swarmscribe-leader:l1-old -f e2e/compose/Dockerfile .
bash docker/check-leader-image.sh swarmscribe-leader:l1-old
```

Expected: `FAILED: the title label is '', not swarmscribe-leader`, exit status 1.

- [ ] **Step 4: Write the Dockerfile**

Create `docker/leader.Dockerfile`:

```dockerfile
# syntax=docker/dockerfile:1
# swarmscribe-leader: the leader (catalogue, consent, jobs, the follower and admin APIs) and
# the `swarmscribe-admin` CLI. No engine, no model libraries, no console, no follower. Build
# from the repository root:
#
#   docker build -t swarmscribe-leader -f docker/leader.Dockerfile .
#
# The base image is pinned by tag and digest, as in console.Dockerfile and
# follower.Dockerfile; move all three together. The new digest comes from
#
#   docker buildx imagetools inspect python:3.12-slim-bookworm

# --- the Python environment -----------------------------------------------------------
FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3 AS build
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
RUN pip install --no-cache-dir "uv==0.12.22"
WORKDIR /app
# Every workspace member's manifest must be present for uv to read the lock; only the leader
# and the one package it imports (protocol) are installed: check-leader-image.sh proves it.
COPY pyproject.toml uv.lock ./
COPY packages/protocol/pyproject.toml packages/protocol/pyproject.toml
COPY packages/engine/pyproject.toml packages/engine/pyproject.toml
COPY packages/leader/pyproject.toml packages/leader/pyproject.toml
COPY packages/console/pyproject.toml packages/console/pyproject.toml
COPY packages/follower/pyproject.toml packages/follower/pyproject.toml
RUN uv sync --frozen --no-dev --package swarmscribe-leader --no-install-workspace
# The sources only, never tests/: a test edit does not rebuild this layer.
COPY packages/protocol/src packages/protocol/src
COPY packages/leader/src packages/leader/src
# --no-editable: the two packages are installed into the environment as wheels (the
# migrations with them), so the final image needs /app/.venv and nothing of the source tree.
# uv's lock file is created world-writable and has no use at run time.
RUN uv sync --frozen --no-dev --package swarmscribe-leader --no-editable \
 && rm -f /app/.venv/.lock

# --- the image --------------------------------------------------------------------------
FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3
# The leader's version (packages/leader/pyproject.toml). check-leader-image.sh compares the
# label with the package installed in the image, and fails when they differ.
ARG VERSION=0.1.0
LABEL org.opencontainers.image.title="swarmscribe-leader" \
      org.opencontainers.image.description="SwarmScribe leader: catalogue, consent, jobs, follower and admin APIs" \
      org.opencontainers.image.source="https://github.com/iamfatness/SwarmScribe" \
      org.opencontainers.image.version="${VERSION}"
# An init as PID 1 (see ENTRYPOINT below). Debian's own package: 24 kB, no dependencies.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tini \
 && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid 10001 leader \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent \
      --shell /usr/sbin/nologin leader
COPY --from=build /app/.venv /app/.venv
# Nothing is written at run time outside the storage volumes you mount (uploads are written
# beside their target, inside the location's own folder): the root filesystem can be
# read-only, and there is no /tmp to provide.
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
USER 10001:10001
EXPOSE 8080
# /healthz says the process answers; it never asks the database (a database outage must not
# make an orchestrator restart every leader). The probe's child is reaped by the init.
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).status == 200 else 1)"]
# tini is PID 1 and the leader its only child. The kernel gives PID 1 no default action for
# a signal, so a SIGTERM that reaches a leader running as PID 1 before uvicorn has installed
# its handlers is dropped: that window is as long as `serve` waits for the database before
# it starts (up to a minute on a database that does not answer), and for all of `migrate`.
# With tini the signal is forwarded: in that window the leader dies of it at once (143),
# and once it is serving it finishes the requests it has and then stops by itself.
STOPSIGNAL SIGTERM
# `serve` is the default; `migrate` is run by overriding the arguments, and the admin CLI by
# overriding the entrypoint:
#   docker run ... swarmscribe-leader migrate
#   docker run -it --entrypoint swarmscribe-admin ... swarmscribe-leader --leader https://... login
ENTRYPOINT ["/usr/bin/tini", "--", "swarmscribe-leader"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8080"]
```

Before building, confirm the digest is the one the other two images use, so that all three move together:

Run: `grep -h -o 'python:3.12-slim-bookworm@sha256:[a-f0-9]*' docker/*.Dockerfile | sort -u`
Expected: exactly one line.

- [ ] **Step 5: Build it and run the check**

```bash
bash docker/check-free-space.sh
docker build -t swarmscribe-leader:l1 -f docker/leader.Dockerfile .
bash docker/check-free-space.sh
bash docker/check-leader-image.sh swarmscribe-leader:l1
```

Expected last line: `ok: swarmscribe-leader:l1 holds the leader and swarmscribe-admin, without the engine, console or follower (<N> MB)`. Write N down for Task 4 (the test image was 515 MB; this one should be smaller).

The script pulls `postgres:16` if it is not there. It takes about a minute.

**(unobserved)** The three places the planner could not watch. If the script fails at one of them, find out what Docker really did and make the script say that; the case stays:

1. *"a stop while waiting for the database"*: a leader started while Postgres is paused. The planner expects `serve` to hang in its start-up check (the TCP connection opens, the database says nothing), `docker stop` to end it within a second or two, and the exit status to be 143. If it instead exits 2 before the stop (the connection was refused, not hung), the case proves nothing: say so in a comment, and keep the status list as it is.
2. *"a stop while serving"*: the planner expects 0 or 143. uvicorn 0.30 and later finish their requests and then hand the signal back to the default action, which ends the process with 143 under tini. Either is a clean stop; 137 never is.
3. *`/readyz` within 6 s of a paused database*: the limit inside the leader is 3 s; the rest is `docker exec` starting Python. If it is slower on this machine, measure it three times and raise the 6 to what is needed, not further.

If a case fails because the **image** is wrong (a library that should not be there, the migrations missing from the wheel, a traceback), fix the Dockerfile.

- [ ] **Step 6: Look at it by hand, once**

```bash
docker run --rm --entrypoint sh swarmscribe-leader:l1 -c 'id; ls /app; ls /app/.venv/bin | grep swarmscribe'
docker image inspect --format '{{.Size}}' swarmscribe-leader:l1 swarmscribe-leader:l1-old
```

Expected: `uid=10001(leader) gid=10001(leader) groups=10001(leader)`; `/app` holds nothing visible (`ls` prints nothing; `.venv` is hidden); `swarmscribe-admin` and `swarmscribe-leader`; two sizes, the first the smaller.

- [ ] **Step 7: Remove what this task made**

```bash
docker image rm swarmscribe-leader:l1-old
docker ps -a --filter name=leader-check- --format '{{.Names}}'
docker network ls --filter name=leader-check-net- --format '{{.Name}}'
```

Expected: the image is removed; the two listings print nothing (the script cleans up after itself). Keep `swarmscribe-leader:l1`. Remove nothing else: other images and containers on this machine are another agent's.

- [ ] **Step 8: Commit**

```bash
git add docker/check-free-space.sh docker/check-leader-image.sh docker/leader.Dockerfile
git commit -m "The swarmscribe-leader image, and a script that checks it

docker/leader.Dockerfile: the leader and swarmscribe-admin as wheels,
user 10001, tini as PID 1, nothing written outside mounted volumes.
check-leader-image.sh runs it against a throwaway Postgres: a serving
leader stays ready on a newer schema, a starting one refuses it.
check-free-space.sh stops a Docker step when the disk is nearly full.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

In the commit message's body, add one line for each **(unobserved)** place that turned out differently from what the plan expected, saying what was seen.

---

### Task 3: CI builds and checks the image

**Files:**
- Modify: `.github/workflows/ci.yml` (a new job after `follower-cuda-image`, before `chart`)

**Interfaces:**
- Consumes: Task 2's three files.
- Produces: CI job `leader-image`. L3's Task 3 renames it `leader-kind-e2e` and adds the cluster steps after the check.

- [ ] **Step 1: Add the job**

In `.github/workflows/ci.yml`, between the job `follower-cuda-image` and the job `chart`, add:

```yaml
  # The real leader image (docker/leader.Dockerfile), built and checked against a throwaway
  # Postgres. The Compose tests above still use e2e/compose/Dockerfile.
  leader-image:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v4
      - name: Check the disk has room
        run: bash docker/check-free-space.sh 5
      - name: Build the leader image
        run: docker build -t swarmscribe-leader:kind -f docker/leader.Dockerfile .
      - name: Check it (a serving leader stays ready on a newer schema; a starting one refuses it)
        run: bash docker/check-leader-image.sh swarmscribe-leader:kind
```

- [ ] **Step 2: Check the workflow still parses, and that nothing else changed**

Run: `uv run --no-project --with pyyaml python -c "import yaml; jobs = yaml.safe_load(open('.github/workflows/ci.yml'))['jobs']; print(sorted(jobs)); print(jobs['leader-image']['steps'][-1]['run'])"`

Expected: a list of nine jobs that includes `leader-image` (`chart`, `compose-e2e`, `console-compose-e2e`, `follower-compose-e2e`, `follower-cuda-image`, `leader-image`, `test`, `web`, `web-e2e`), then `bash docker/check-leader-image.sh swarmscribe-leader:kind`.

Run: `git diff --stat .github/workflows/ci.yml` — Expected: one file, insertions only.

The job itself first runs on GitHub when the branch is pushed, which this plan does not do. Say so in the pull request: "`leader-image` has not run on a GitHub runner".

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "CI: build the leader image and check it

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The README's leader image, the spec's amendment and the follow-ups

**Files:**
- Modify: `README.md` (a new section after "Multi-replica test", before "## Run a follower (development)")
- Modify: `docs/superpowers/specs/2026-10-02-leader-design.md` (section 12, line 351-352)
- Modify: `docs/superpowers/plans/2026-10-03-leader-admin-followups.md` (lines 11-13)

**Interfaces:**
- Consumes: Tasks 1 to 3; the image's size from Task 2, Step 5.
- Produces: README section "Leader image", which L2's guide links to.

- [ ] **Step 1: The README section**

In `README.md`, after the section "### Multi-replica test" (it ends with the paragraph "Tests use a real Postgres: ...") and before "## Run a follower (development)", add the following. Replace `<N>` with the size the check printed in Task 2, and if any **(unobserved)** place turned out differently, make the sentences about stopping say what was seen.

````markdown
### Leader image

`docker/leader.Dockerfile` builds `swarmscribe-leader`: the leader and `swarmscribe-admin`,
without the engine, any model library, the console or the follower (<N> MB). It is not
published; build it from the repository root:

```
docker build -t swarmscribe-leader -f docker/leader.Dockerfile .
bash docker/check-leader-image.sh swarmscribe-leader
```

It runs as user 10001 and writes nothing outside the folders you mount into it, so the root
filesystem can be read-only and there is no `/tmp` to provide. The storage folder must be
writable by uid or gid 10001:

```
docker run --rm --read-only --cap-drop ALL --security-opt no-new-privileges \
  -e SWARMSCRIBE_DATABASE_URL -e SWARMSCRIBE_PUBLIC_URL -e SWARMSCRIBE_LINK_KEY \
  swarmscribe-leader migrate
docker run -d --read-only --cap-drop ALL --security-opt no-new-privileges -p 8080:8080 \
  -e SWARMSCRIBE_DATABASE_URL -e SWARMSCRIBE_PUBLIC_URL -e SWARMSCRIBE_LINK_KEY \
  -v /srv/recordings:/data/recordings swarmscribe-leader
```

`serve` is the default command. `swarmscribe-admin` is in the image too
(`--entrypoint swarmscribe-admin`), but it keeps your sign-in in a file under your home
folder: run it on your own machine, not in the leader's container.

An init (tini) is PID 1, so a stop is never lost: a leader that is still waiting for its
database ends at once, and one that is serving finishes the requests it has and then ends by
itself. The image's health check asks `/healthz`, which never touches the database: a
database outage does not make an orchestrator restart every leader.

What `check-leader-image.sh` proves, with a throwaway Postgres: what the image holds and
does not; that `migrate` and `serve` run read-only with every capability dropped; that a
leader refuses to start on a database it has not migrated; that a *serving* leader stays
ready when a newer leader has migrated the database, and a *starting* one refuses to start
on it; that `/readyz` says 503 within its limit when the database stops answering; and that
its log holds neither the link key nor the database password. CI runs it (job
`leader-image`). The Compose tests still use the older test image
(`e2e/compose/Dockerfile`), which is not for deployment.
````

- [ ] **Step 2: Amend the leader spec**

In `docs/superpowers/specs/2026-10-02-leader-design.md`, section 12, replace

```markdown
- `/healthz` (process alive), `/readyz` (database reachable, migrations
  current, OIDC metadata fetched at least once).
```

with

```markdown
- `/healthz` (process alive), `/readyz` (database reachable, and its schema
  this leader's or newer).
  *Amended 2026-10-05 (leader chart spec, section 6):* "migrations current"
  made every serving replica unready the moment a pre-upgrade migration
  finished, and "OIDC metadata fetched at least once" took a restarted
  replica away from the followers during an identity provider's outage.
  `serve` still refuses to start on a database that is ahead.
```

- [ ] **Step 3: Close the follow-up**

In `docs/superpowers/plans/2026-10-03-leader-admin-followups.md`, replace the item at lines 11-13 ("**Readiness and the follower plane.** ...") with:

```markdown
- **Readiness and the follower plane.** *Done 2026-10-05 (leader chart L1):*
  `/readyz` no longer waits for identity-provider metadata. Still open: a
  way to see from outside that sign-in works, without gating traffic (leader
  chart spec, follow-up F8).
```

- [ ] **Step 4: Check the documents against the code**

Run: `grep -n "sign-in metadata unavailable" -r packages README.md`
Expected: no match (the status is gone from the code, the tests and the README; it remains in older plans, which are records).

Run: `grep -n "leader.Dockerfile" README.md .github/workflows/ci.yml docker/check-leader-image.sh`
Expected: at least one match in each of the first two files.

- [ ] **Step 5: Commit**

```bash
git add README.md docs/superpowers/specs/2026-10-02-leader-design.md docs/superpowers/plans/2026-10-03-leader-admin-followups.md
git commit -m "Docs: the leader image, and what /readyz now means

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage.** Section 4 (the image): Task 2, every row of its table is a line of the Dockerfile and a case of the check. Section 6 (readiness): Task 1, each line of the outline is a test. Rulings R1 to R4 and R13: rulings 1 to 9 here. Section 1.4's three points: (a) and (b) in Task 1, the unbounded probe in `ReadinessProbe`. C9 (the free-space floor): Task 2, Step 1, and before every Docker step.

**What is not done here.** The chart (L2). Any cluster (L3). The four existing tests keep the old image (follow-up F5). HEAD on the probes (ruling 6).

**Type and name consistency.** `ReadinessProbe.check(head_revision)` returns `(status, code)`; `readyz` unpacks it; `app.state.readiness` is set in Task 1, Step 4 and read in Step 3's file. `_database_revision` is the name the tests replace. The image tag is `swarmscribe-leader:l1` locally and `swarmscribe-leader:kind` in CI (never `:e2e`, which is the Compose tests' image and another one). The check script's last line begins `ok: ` and the plan's expectations quote it exactly.

**Honesty.** Nothing in this plan has been run by its planner beyond `ruff check` and `bash -n`. Each task's commit message is the place to say what turned out differently.
