# Fleet Console C1b — Leader Additions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two small additive leader changes the fleet console (C2b) depends on: a revoked console credential's `401` carries the code `credential_revoked` (an unknown credential stays `unauthorized`), and `GET /v1/admin/status` adds `completed_last_day` and `oldest_queued_age_s`, both computed by the database's clock inside queries the status call already makes.

**Architecture:** Task 1 sets a class-level `code` on C1's `RevokedConsoleCredential`; the existing error handler already answers with `exc.code`, so nothing else in the request path changes (message, `WWW-Authenticate` and audit stay as they are). Task 2 reshapes two of `reports.status_summary`'s existing job queries: the jobs-by-state aggregate also returns, per state, the age of the oldest job by `now() − min(created_at)` (only the `queued` row is used), and the completed-in-the-last-hour count becomes one query with two `FILTER`ed counts (last hour, last day). Both fields are added to the `Status` model with defaults. No migration is needed (`0006` is not used).

**Tech Stack:** Python 3.11+, uv workspace, FastAPI, Pydantic v2, SQLAlchemy 2.0 async on Postgres, pytest + pytest-asyncio, ruff.

**Spec:** `docs/superpowers/specs/2026-10-03-fleet-console-design.md` section 6 (the overview shows "jobs completed last hour/day" and "oldest queued job age") and the C1 follow-up that the console must recognise a revoked credential (`docs/superpowers/plans/2026-10-03-fleet-console-c1-followups.md`). Owner rulings 2026-10-03: the revoked code (C2 report, "Decisions for you", item 3) and the two status fields (item 4). The leader-side contract being extended is C1: `docs/superpowers/plans/2026-10-03-fleet-console-c1-leader.md`.

**Precondition:** PR #5 (`fleet-console-leader`, C1) is merged to `main`, and this work branches from `main`:

```bash
git switch main
git pull
git switch -c fleet-console-leader-additions
```

Before Task 1, confirm `packages/leader/src/swarmscribe_leader/auth/consoles.py` defines `RevokedConsoleCredential` and `packages/leader/src/swarmscribe_leader/api/admin_models.py`'s `Status` has `follower_pools`.

## Global Constraints

- Owner ruling 2026-10-03: "a revoked console credential's 401 uses code `credential_revoked` (the message is unchanged). An unknown credential stays `unauthorized`."
- Owner ruling 2026-10-03: "`GET /v1/admin/status` adds `completed_last_day` and `oldest_queued_age_s` (null when nothing is queued). The change is additive, uses the database clock and adds no extra queries if avoidable."
- Unchanged from C1: the revoked message `this console credential has been revoked`, `WWW-Authenticate: Console error="invalid_token"`, the `console.refused` audit row with detail `{"code": "revoked"}`, check order (credential first), and that an unknown credential is answered identically to any other wrong guess.
- Spec section 4: "authenticates the console credential (revoked → 401)".
- Additive only: every existing `Status` field and value is unchanged; `swarmscribe-admin status` output is unchanged (`--json` shows the new fields).
- Dependency rule: `leader` imports `swarmscribe_protocol` only; no new third-party dependency.
- On this Windows machine `uv` is not on PATH: every command is written `python -m uv run …`. The leader tests need Postgres (`pgserver` starts one automatically).
- If `ruff check` flags import order or line length in code copied from this plan, run `python -m uv run ruff check --fix --select I packages/leader` or wrap the line without changing behaviour.
- Commits end with a blank line and `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Do not push.

## Review Focus

Hostile and malformed input the rulings imply but do not spell out. Each line names the behaviour a reasonable person expects and the task whose tests pin it.

1. **Probing with wrong credentials** (unknown, malformed, oversize, a follower's credential, a near miss of a real one; with or without actor headers) → still `401 unauthorized`, byte-identical to each other; only the holder of a real revoked credential ever sees `credential_revoked`. — Task 1.
2. **A revoked credential with malformed delegation headers** → `401 credential_revoked` (the credential is checked first), never `400`. — Task 1.
3. **Clock skew between the leader host and the database** → `oldest_queued_age_s` uses the database's `now()`, so a wrong Python clock does not change it. — Task 2.
4. **A queued job whose `created_at` is in the future** (a database clock that stepped back, a hand-edited row) → `oldest_queued_age_s` is `0`, never negative. — Task 2.
5. **Only leased, completed, failed or cancelled jobs** (nothing queued) → `oldest_queued_age_s` is `null`, not `0`; and a very old queued job (years) is a plain integer, not a float or an overflow. — Task 2.
6. **Jobs completed exactly at the window edges, and jobs in other states with a `completed_at`** → only `state = completed` counts; last hour ⊂ last day. — Task 2.
7. **Many pools and states** → the status call issues the same number of statements against `jobs` as before (three). — Task 2.

## Decisions this plan makes (the rulings are silent)

- **The audit detail for a revoked console stays `{"code": "revoked"}`** (existing rows and C1's tests use it); only the HTTP answer's code changes.
- **`oldest_queued_age_s` is measured from the job's `created_at`**: how long the oldest job now waiting has existed. A job put back in the queue by `jobs retry` keeps its original `created_at`, so its age includes earlier attempts — what an operator asking "how long has this been waiting to be done" means. (`available_at`, the backoff "not before", is not used: it moves on every backoff.)
- **Whole seconds, floored, at least 0**, computed in SQL as `greatest(floor(extract(epoch from now() − min(created_at)))::bigint, 0)`; `now()` is the transaction's start, the same instant for every figure in one status answer.
- **`completed_last_day` counts jobs with `state = 'completed'` and `completed_at >= now() − 1 day`**, in the same statement as `completed_last_hour` (`count(*) FILTER (WHERE completed_at >= now() − 1 hour)`), restricted to the last day.
- **Model defaults:** `completed_last_day: int = 0`, `oldest_queued_age_s: int | None = None`, so the model still validates a summary built without them (none exists after this plan).
- **No migration.** The existing table scans serve both; `ix_jobs_claim` (`state, pool, priority, created_at`) already covers the queued rows.
- **The admin CLI's human `status` output is unchanged** (scope); `--json` shows the fields.

## File Structure

```
packages/leader/src/swarmscribe_leader/
  auth/consoles.py        MOD  RevokedConsoleCredential.code (Task 1)
  reports.py              MOD  status_summary: two reshaped queries (Task 2)
  api/admin_models.py     MOD  Status: completed_last_day, oldest_queued_age_s (Task 2)
packages/leader/tests/
  test_consoles.py        MOD  (Task 1)
  test_console_auth.py    MOD  (Task 1)
  test_admin_api.py       MOD  (Task 2)
README.md                 MOD  console section (Task 1); status fields (Task 2)
```

---

### Task 1: A revoked console credential answers `credential_revoked`

**Files:**
- Modify: `packages/leader/src/swarmscribe_leader/auth/consoles.py` (`RevokedConsoleCredential`)
- Modify: `README.md` ("Fleet console credentials")
- Test: `packages/leader/tests/test_consoles.py`, `packages/leader/tests/test_console_auth.py`

**Interfaces:**
- Consumes: C1's `InvalidConsoleCredential`, `RevokedConsoleCredential(console)`, `authenticate_console`, and the `LeaderError` handler in `api/errors.py` (it answers `{code: exc.code, message: exc.message}`).
- Produces: `RevokedConsoleCredential.code == "credential_revoked"`; `InvalidConsoleCredential.code` stays `"unauthorized"`. The fleet console (C2b) treats a `401` with code `credential_revoked` as "stop polling; credential revoked".

- [ ] **Step 1: Write the failing tests**

In `packages/leader/tests/test_consoles.py`, in `test_anything_but_the_exact_credential_is_unknown`, after `assert raised.value.message == "unknown console credential"` add:

```python
    assert raised.value.code == "unauthorized"
```

and in `test_a_revoked_console_is_refused`, after the message assertion add:

```python
    assert raised.value.code == "credential_revoked"
```

In `packages/leader/tests/test_console_auth.py`, in `test_a_credential_that_is_not_valid_is_401_whatever_the_headers_say`, replace

```python
    assert (response.status_code, response.json()["code"]) == (401, "unauthorized")
```

with

```python
    # Only the holder of a real, revoked credential learns it was revoked.
    expected = "credential_revoked" if which == "revoked" else "unauthorized"
    assert (response.status_code, response.json()["code"]) == (401, expected)
```

replace the final assertion of `test_a_console_revoked_mid_session_is_refused_from_its_next_request` with

```python
    assert (after.status_code, after.json()) == (
        401,
        {"code": "credential_revoked", "message": "this console credential has been revoked"},
    )
    assert after.headers["www-authenticate"] == 'Console error="invalid_token"'
```

and append:

```python
async def test_a_revoked_credential_is_reported_before_its_headers_are_read(
    admin_client, factory, sessionmaker
):
    _, revoked = await factory.console(name="old", revoked=True)
    for actor, role in [("one two", "viewer"), ("system:poller", "admin"), (None, None)]:
        response = await admin_client.get(
            "/v1/admin/status", headers=console_headers(revoked, actor=actor, role=role)
        )
        assert (response.status_code, response.json()["code"]) == (401, "credential_revoked")
    refusals = await audit_rows(sessionmaker, "console.refused")
    assert [e.detail for e in refusals] == [{"code": "revoked"}] * 3


async def test_wrong_credentials_are_still_answered_alike(admin_client, factory):
    _, credential = await factory.console(name="fleet")
    _, follower_credential = await factory.follower()
    near = credential[:-1] + ("A" if credential[-1] != "A" else "B")
    answers = [
        await admin_client.get("/v1/admin/whoami", headers=console_headers(guess))
        for guess in (near, new_secret(), follower_credential)
    ]
    assert {a.status_code for a in answers} == {401}
    assert {a.content for a in answers} == {answers[0].content}
    assert answers[0].json()["code"] == "unauthorized"


async def test_a_persons_bearer_refusal_keeps_its_codes(admin_client, idp):
    expired = idp.entra(lifetime=-3600)
    response = await admin_client.get(
        "/v1/admin/whoami", headers={"Authorization": f"Bearer {expired}"}
    )
    assert (response.status_code, response.json()["code"]) == (401, "token_expired")
    missing = await admin_client.get("/v1/admin/whoami")
    assert (missing.status_code, missing.json()["code"]) == (401, "unauthorized")
```

If `new_secret` is not yet imported in `test_console_auth.py`, add `from swarmscribe_leader.auth.secrets import new_secret` to its imports (C1 already uses it in `test_a_near_miss_is_answered_exactly_like_a_random_guess`).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m uv run pytest packages/leader/tests/test_consoles.py packages/leader/tests/test_console_auth.py -v`
Expected: FAIL — `test_a_revoked_console_is_refused`, the `revoked` cases of `test_a_credential_that_is_not_valid_is_401_whatever_the_headers_say`, `test_a_console_revoked_mid_session…` and `test_a_revoked_credential_is_reported_before_its_headers_are_read` see `unauthorized`. The other new tests pass already (they pin behaviour that must not change).

- [ ] **Step 3: Give the revoked credential its own code**

In `packages/leader/src/swarmscribe_leader/auth/consoles.py`, replace the class

```python
class RevokedConsoleCredential(InvalidConsoleCredential):
    """A revoked console's credential. Carries the console's name, so that the refusal can
    be audited (an unknown credential names no one)."""

    def __init__(self, console: str):
        super().__init__("this console credential has been revoked")
        self.console = console
```

with

```python
class RevokedConsoleCredential(InvalidConsoleCredential):
    """A revoked console's credential. Carries the console's name, so that the refusal can
    be audited (an unknown credential names no one). Its code, credential_revoked, tells
    the console to stop calling until its administrator replaces the credential; only the
    holder of the real credential can see it, so it reveals nothing to a guesser."""

    code = "credential_revoked"

    def __init__(self, console: str):
        super().__init__("this console credential has been revoked")
        self.console = console
```

- [ ] **Step 4: Document the code**

In `README.md`, section "Fleet console credentials", replace

```markdown
`console revoke` refuses the console (`401`) from its next request. Console
```

with

```markdown
`console revoke` refuses the console (`401`, code `credential_revoked`) from
its next request; an unknown credential is `401 unauthorized`, so a console can
tell the two apart and stop calling until it is given a new credential. Console
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m uv run pytest packages/leader -v`
Expected: PASS — including `test_every_refusal_of_the_poller_is_audited` (its audit detail is still `revoked`) and `test_a_near_miss_is_answered_exactly_like_a_random_guess`.

Run: `python -m uv run ruff check packages/leader`
Expected: no findings.

- [ ] **Step 6: Commit**

```bash
git add packages/leader/src/swarmscribe_leader/auth/consoles.py packages/leader/tests/test_consoles.py packages/leader/tests/test_console_auth.py README.md
git commit -m "Leader: a revoked console credential answers 401 credential_revoked

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Status adds `completed_last_day` and `oldest_queued_age_s`

Independent of Task 1.

**Files:**
- Modify: `packages/leader/src/swarmscribe_leader/reports.py` (`status_summary`, imports)
- Modify: `packages/leader/src/swarmscribe_leader/api/admin_models.py` (`Status`)
- Modify: `README.md` (status line in the `swarmscribe-admin` section)
- Test: `packages/leader/tests/test_admin_api.py` (append)

**Interfaces:**
- Consumes: `reports.status_summary`, `Status`, the test helpers `get(client, idp, path)` and `factory.job(recording=None, **overrides)` / `factory.recording(location=None, *, key=…)` / `factory.location()`.
- Produces: `Status.completed_last_day: int` (default 0), `Status.oldest_queued_age_s: int | None` (default None); `status_summary` returns both keys. The fleet console (C2b) requires both in every status answer.

- [ ] **Step 1: Write the failing tests**

In `packages/leader/tests/test_admin_api.py`, change `from sqlalchemy import event, select, update` to:

```python
from sqlalchemy import event, extract, func, literal, select, update
```

add `from datetime import UTC, datetime, timedelta` in place of `from datetime import timedelta`, and append:

```python
# --- completed in the last day; age of the oldest queued job ----------------------------


async def _job(factory, **values):
    return await factory.job(await factory.recording(await factory.location()), **values)


async def test_status_with_no_jobs_has_no_queue_age(admin_client, idp):
    body = await get(admin_client, idp, "/v1/admin/status")
    assert (body["completed_last_hour"], body["completed_last_day"]) == (0, 0)
    assert body["oldest_queued_age_s"] is None


async def test_status_counts_completions_in_the_last_hour_and_day(admin_client, idp, factory):
    now = utcnow()
    for ago in (timedelta(minutes=10), timedelta(hours=5), timedelta(days=2)):
        await _job(factory, state="completed", completed_at=now - ago)
    await _job(factory, state="failed", completed_at=now - timedelta(minutes=5))
    await _job(factory, state="cancelled", completed_at=now - timedelta(minutes=5))
    body = await get(admin_client, idp, "/v1/admin/status")
    assert (body["completed_last_hour"], body["completed_last_day"]) == (1, 2)


async def test_the_queue_age_is_the_oldest_queued_jobs(admin_client, idp, factory):
    now = utcnow()
    await _job(factory, state="queued", created_at=now - timedelta(hours=3))
    await _job(factory, state="queued", created_at=now - timedelta(hours=1), pool="gpu")
    await _job(factory, state="leased", created_at=now - timedelta(hours=10))
    await _job(
        factory, state="completed", created_at=now - timedelta(hours=20), completed_at=now
    )
    body = await get(admin_client, idp, "/v1/admin/status")
    age = body["oldest_queued_age_s"]
    assert isinstance(age, int)
    assert 3 * 3600 - 5 <= age <= 3 * 3600 + 60


async def test_nothing_queued_is_no_age_not_zero(admin_client, idp, factory):
    await _job(factory, state="leased", created_at=utcnow() - timedelta(hours=2))
    body = await get(admin_client, idp, "/v1/admin/status")
    assert body["oldest_queued_age_s"] is None


async def test_a_queued_job_from_the_future_has_age_zero(admin_client, idp, factory):
    await _job(factory, state="queued", created_at=utcnow() + timedelta(hours=1))
    body = await get(admin_client, idp, "/v1/admin/status")
    assert body["oldest_queued_age_s"] == 0


async def test_the_queue_age_uses_the_databases_clock(
    admin_client, idp, factory, sessionmaker, monkeypatch
):
    old = datetime(2000, 1, 1, tzinfo=UTC)
    await _job(factory, state="queued", created_at=old)
    # A leader host whose clock is wrong must not change the figure.
    wrong = datetime(1990, 1, 1, tzinfo=UTC)
    monkeypatch.setattr("swarmscribe_leader.clock.utcnow", lambda: wrong)
    body = await get(admin_client, idp, "/v1/admin/status")
    async with sessionmaker() as session:
        expected = await session.scalar(select(extract("epoch", func.now() - literal(old))))
    assert isinstance(body["oldest_queued_age_s"], int)
    assert abs(body["oldest_queued_age_s"] - int(expected)) <= 5


async def test_status_makes_no_extra_job_queries(admin_app, admin_client, idp, factory):
    now = utcnow()
    for n in range(4):
        created = now - timedelta(hours=n)
        await _job(factory, state="queued", pool=f"pool-{n}", created_at=created)
    await _job(factory, state="completed", completed_at=now)
    statements: list[str] = []

    def record(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    engine = admin_app.state.engine.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        await get(admin_client, idp, "/v1/admin/status")
    finally:
        event.remove(engine, "before_cursor_execute", record)
    # By state (with the queue age), open jobs by pool, completions (hour and day): as before.
    assert len([s for s in statements if "FROM jobs" in s]) == 3
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m uv run pytest packages/leader/tests/test_admin_api.py -k "queue_age or no_jobs or last_hour_and_day or nothing_queued or from_the_future or extra_job_queries" -v`
Expected: FAIL — `KeyError: 'completed_last_day'` / `'oldest_queued_age_s'`. (`test_status_makes_no_extra_job_queries` passes already; it pins the query count.)

- [ ] **Step 3: Reshape the two job queries**

In `packages/leader/src/swarmscribe_leader/reports.py`, change the SQLAlchemy import to:

```python
from sqlalchemy import BigInteger, cast, extract, func, select
```

In `status_summary`, replace

```python
    jobs = dict.fromkeys(JOB_STATES, 0)
    for state, count in (
        await session.execute(select(Job.state, func.count()).group_by(Job.state))
    ).all():
        jobs[state] = count
```

with

```python
    # One aggregate gives the counts per state and, per state, the age of its oldest job by
    # the database's clock (now() is the transaction's start; the leader host's clock is
    # never used). Only the queued row's age is reported; at least 0 if a row is "future".
    jobs = dict.fromkeys(JOB_STATES, 0)
    oldest_queued_age_s: int | None = None
    oldest_age = func.greatest(
        cast(func.floor(extract("epoch", func.now() - func.min(Job.created_at))), BigInteger),
        0,
    )
    for state, count, age in (
        await session.execute(
            select(Job.state, func.count(), oldest_age).group_by(Job.state)
        )
    ).all():
        jobs[state] = count
        if state == "queued":
            oldest_queued_age_s = int(age)
```

and replace

```python
    completed_last_hour = await session.scalar(
        select(func.count())
        .select_from(Job)
        .where(Job.state == "completed", Job.completed_at >= func.now() - timedelta(hours=1))
    )
```

with

```python
    # Last hour and last day in one statement: the day's completions, the hour's filtered.
    completed_last_hour, completed_last_day = (
        await session.execute(
            select(
                func.count().filter(Job.completed_at >= func.now() - timedelta(hours=1)),
                func.count(),
            )
            .select_from(Job)
            .where(Job.state == "completed", Job.completed_at >= func.now() - timedelta(days=1))
        )
    ).one()
```

In the dictionary it returns, after `"completed_last_hour": completed_last_hour or 0,` add:

```python
        "completed_last_day": completed_last_day or 0,
        "oldest_queued_age_s": oldest_queued_age_s,
```

- [ ] **Step 4: Add the fields to the response model**

In `packages/leader/src/swarmscribe_leader/api/admin_models.py`, in `class Status`, after `completed_last_hour: int`, add:

```python
    # Added for the fleet console's overview; additive, so older clients ignore them.
    completed_last_day: int = 0
    oldest_queued_age_s: int | None = None  # None when nothing is queued
```

- [ ] **Step 5: Document the fields**

In `README.md`, in the `swarmscribe-admin` section, after the `status` command's line, add:

```markdown
`status --json` also gives `completed_last_day` and `oldest_queued_age_s` (how
long the oldest queued job has existed, by the database's clock; `null` when
nothing is queued).
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m uv run pytest packages/leader -v`
Expected: PASS — the new tests, and unchanged `test_status_summarises_queue_followers_and_locations`, `test_status_counts_recent_completions_and_failed_attempts`, the follower-pool tests, and the CLI's status tests.

Run: `python -m uv run ruff check packages/leader`
Expected: no findings.

- [ ] **Step 7: Commit**

```bash
git add packages/leader/src/swarmscribe_leader/reports.py packages/leader/src/swarmscribe_leader/api/admin_models.py packages/leader/tests/test_admin_api.py README.md
git commit -m "Leader: status adds completions in the last day and the oldest queued job's age

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Self-review

**Ruling coverage.** Revoked → `credential_revoked`, message unchanged, unknown stays `unauthorized`, C1 tests and README updated: Task 1 (Steps 1, 3, 4). `completed_last_day` and `oldest_queued_age_s` (null when nothing queued), additive, database clock, no extra queries: Task 2 (`test_status_with_no_jobs_has_no_queue_age`, `test_status_counts_completions_in_the_last_hour_and_day`, `test_the_queue_age_is_the_oldest_queued_jobs`, `test_nothing_queued_is_no_age_not_zero`, `test_the_queue_age_uses_the_databases_clock`, `test_status_makes_no_extra_job_queries`). No migration needed, so `0006` is unused.

**Placeholder scan.** Every code step gives its exact replacement text and location; every run step its command and expected result.

**Type consistency.** `RevokedConsoleCredential.code` is a class attribute read by the existing handler through `exc.code`; `InvalidConsoleCredential` keeps `unauthorized` from `InvalidToken`. `status_summary` returns `completed_last_day: int` and `oldest_queued_age_s: int | None`, matching `Status`. The test helpers (`console_headers`, `audit_rows`, `factory.console`, `factory.follower`, `get`, `factory.job`) are C1's and the leader suite's existing ones, used with their existing signatures.

**Review Focus.** 1 → `test_wrong_credentials_are_still_answered_alike`, the parametrized `test_a_credential_that_is_not_valid…`; 2 → `test_a_revoked_credential_is_reported_before_its_headers_are_read`; 3 → `test_the_queue_age_uses_the_databases_clock`; 4 → `test_a_queued_job_from_the_future_has_age_zero`; 5 → `test_nothing_queued_is_no_age_not_zero` and the year-2000 job in the clock test (an `int`); 6 → `test_status_counts_completions_in_the_last_hour_and_day` (failed/cancelled with `completed_at` excluded); 7 → `test_status_makes_no_extra_job_queries`.
