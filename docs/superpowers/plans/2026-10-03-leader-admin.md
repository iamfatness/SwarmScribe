# Leader Admin (Plan A2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Administrators sign in with Microsoft Entra ID or Google, get a role from their groups (or, for Google, from email and domain lists), and run the leader through an audited `/v1/admin` API and the `swarmscribe-admin` CLI; the Plan A1 follow-ups are fixed; and a Docker Compose test proves two leader replicas survive a follower and a replica being killed.

**Architecture:** Three new layers on the Plan A1 leader. `auth/oidc.py` validates provider ID tokens (RS256, JWKS fetched through an injectable fetcher and cached) and `auth/roles.py` turns an identity into a role (Entra `groups` claim with a Microsoft Graph fallback on overage; Google Groups through Cloud Identity plus email/domain lists), cached five minutes per person. `api/admin.py` is a thin router: each route authenticates through `api/admin_auth.require(role)`, calls one service function (`reports.py` for reads; `ingest/locations.py`, `jobs/admin.py`, `auth/followers.py` for changes), writes an audit entry and commits. `admin_cli/` is a separate client inside the leader package: device-code sign-in, a token cache with owner-only permissions and silent refresh, and one subcommand per admin route.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2.0 (asyncio) on asyncpg, Alembic, pydantic-settings, PyJWT with `cryptography` (RS256, JWKs), httpx (leader-side directory clients and the CLI), pytest-asyncio, pgserver; Docker Compose, nginx and GitHub Actions for the replica test.

**Spec:** `docs/superpowers/specs/2026-10-02-leader-design.md` — sections 10 (administrators, roles, CLI login), 11 (configuration), 12 (`/readyz`, `status`), 13 (role enforcement, JWT tests, the Compose end-to-end), 15 (the rest of Plan A). Plan A1 (`docs/superpowers/plans/2026-10-02-leader-core.md`) is built and merged; this plan also carries out the "Fix in the next leader plan (A2)" items of `docs/superpowers/plans/2026-10-02-leader-core-followups.md`. Plan B (cloud storage, vocabulary, metrics, consent deletion) is out of scope.

## Global Constraints

- Dependency rule: `leader` imports `swarmscribe_protocol` only (never the engine). The CLI lives in `swarmscribe_leader.admin_cli` and imports nothing from the leader's server modules.
- The follower wire format does not change: `PROTOCOL_VERSION` stays `1` and `packages/protocol/tests/schema_v1.json` is not touched.
- Roles are cumulative: admin ⊃ operator ⊃ viewer. viewer: `status`, `jobs list`, `followers list`, `consent report` (and `locations list`, `whoami`). operator: viewer + `ingest`, `jobs retry/cancel/priority`, `followers drain`. admin: operator + `locations add/disable/enable`, `tokens create/list/revoke`, `followers revoke`.
- ID token validation, verbatim from the spec: "signature against that issuer's JWKS (cached, refreshed on an unknown `kid`), `iss`, `aud` equal to the configured client ID, `exp`, `nbf`, 60 s clock skew. Entra tokens must also carry the configured tenant (`tid`); Google tokens must carry `email_verified=true` and, if configured, an allowed hosted domain (`hd`)." Only `RS256` is accepted.
- "A person is identified by `(issuer, sub)`; the audit log also records their email." "Role lookups are cached for 5 minutes per person. A person with no role is refused with `403`."
- "Every admin endpoint writes an audit entry with the issuer, `sub` and email." The actor string is `"<email> (<issuer> <sub>)"`.
- Credentials cache: `~/.config/swarmscribe/credentials.json`, owner-only permissions, silent refresh.
- Configuration names, verbatim: `SWARMSCRIBE_ENTRA_TENANT_ID`, `…_ENTRA_CLIENT_ID`, `…_ENTRA_CLIENT_SECRET`; `SWARMSCRIBE_GOOGLE_CLIENT_ID`, `…_GOOGLE_CLIENT_SECRET`, `…_GOOGLE_HOSTED_DOMAIN`, `…_GOOGLE_SERVICE_ACCOUNT`; `SWARMSCRIBE_ROLE_<VIEWER/OPERATOR/ADMIN>_ENTRA_GROUPS`, `…_GOOGLE_GROUPS`, `…_EMAILS`, `…_DOMAINS` (comma-separated).
- A recording becomes a job only if its location's `consent.txt` matches it. `jobs retry` re-checks consent; nothing in this plan can queue anything else.
- Never log, print (except `tokens create`, once) or store in the audit log: ID tokens, refresh tokens, join tokens, follower credentials, client secrets, service-account keys, or file links. This includes proxy logs in the Compose test.
- Tests make no network calls to Entra, Google or Microsoft Graph: keys are generated locally, JWKS and discovery documents come from an injected fetcher, directory clients are fakes or run on `httpx.MockTransport`.
- Every role boundary has a test: each admin endpoint is refused for the role below it.
- Nothing in code, defaults, examples or test data may be specific to one kind of content or organisation (`example.org`, `talks/one.mp3`).
- Job states, defaults and the follower API behaviour of Plan A1 are unchanged except where a task here says otherwise.
- On this Windows machine `uv` is not on PATH: run `python -m uv …` wherever a step says `uv …`. There is no Docker here: Task 13's Compose run happens only in GitHub Actions; its driver is also tested in-process.
- If `ruff check` flags import order or line length in code copied from this plan, run `uv run ruff check --fix --select I` or wrap the line without changing behaviour.
- Commits end with a blank line and `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Do not push.

## Review Focus

1. **Two operators retry the same failed job at the same moment**: exactly one new job is queued; the other gets `409`. — Task 10.
2. **A follower is revoked while it is uploading**: its lease is released at once and its upload links stop working (`409 stale_lease`) before it can overwrite anything. — Task 10.
3. **Signed file links passing through the Compose proxy**: nginx must not write them to its access or error log. — Task 13.
4. **Tokens in failure paths**: a refused, expired or malformed ID token, and a failing CLI command, leave no token in the leader log, the audit log or the CLI's output. — Task 8, Task 12.
5. **An `ingest` request that arrives while that location is already being scanned**: it is kept and causes another scan, not swallowed by the scan in progress. — Task 4.

## Decisions this plan makes (the spec does not dictate them)

Follow-ups from Plan A1:

- **Walk from `input_prefix`.** `LocalBackend.list(prefix)` starts its walk in the deepest folder of the prefix (`incoming/2024-` → `incoming/`). A missing input folder fails the scan like a missing root (an empty listing would mark everything missing and cancel work). `consent.txt` is still read from the location root.
- **No-speech result.** A recording with no speech is the engine's natural output: an empty `.txt`, an empty `.srt` and a `.segments.json` whose `segments` is `[]`. Submit accepts empty `txt`/`srt` only together and only if `segments.json` (≤ 1 MiB) parses as a `SegmentsDocument` with no segments; anything else is `409 outputs_inconsistent`. An empty `segments.json` is still `outputs_missing`. The result is stored with `job_results.no_speech = true`.
- **One clock for `available_at`.** Claims compare `available_at <= now()` in SQL and `push_back` sets `available_at = now() + delay` in SQL. Lease times still use the replica's clock (replicas need NTP).
- **Hashing outside the row lock.** Submit commits the follower's authentication first, hashes the outputs with no lock held, then locks the job, re-checks the lease, and confirms by `stat` that no output changed since it was hashed (`409 outputs_changed` if one did).
- **Blocking filesystem calls.** `file_path`, the download `open`/`fstat`, and the upload's `mkdir`/`open`/`write`/`close`/`replace`/`unlink` all run in worker threads.
- **Reaper audit rows.** `job.expire` per expired lease and `follower.gone` per follower, actor `system`. The lock-loss metric stays in Plan B.
- **One settings profile per device.** No `profiles edit` in this plan, so migration 0002's constraint needs nothing now.

Sign-in and roles:

- **Email and domain lists apply to Google sign-ins only.** Entra's `email` claim is not verified by Entra and can be set by users in some tenants; Entra roles come only from group IDs.
- **Overage without a client secret** gives no Entra groups (a warning is logged), so the person gets `403`. A Graph or Cloud Identity failure is `503` with `Retry-After`, never `403`, and is not cached.
- **Settings refuse half-configured providers**: Entra client and tenant ID go together; the tenant ID must be a GUID; a Google client ID needs its client secret; role lists need their provider (and Google Groups lists need the service account).
- **`SWARMSCRIBE_GOOGLE_SERVICE_ACCOUNT`** holds either the JSON key itself or the path of a mounted file containing it.
- **The CLI learns everything from the leader**: `GET /v1/admin/login-config` (no sign-in needed) lists each configured provider's client ID, device-code and token endpoints, and scopes. For Google it also returns the client secret, because Google's "TVs and Limited Input devices" clients require it for device sign-in and Google documents it as not confidential; the Entra client secret is never returned.
- **Readiness**: `/readyz` also needs each configured provider's discovery document and keys fetched at least once; a leader with no provider configured is ready (its admin API answers `401`).
- **Refusals are audited too**: a signed-in person refused for their role gets an `admin.refused` audit row. Unauthenticated `401`s are not audited.
- **Reads are audited** (`status.view`, `jobs.view`, …) because the spec says every admin endpoint writes an entry.

Admin behaviour:

- **Admin cancel is final for that version.** A job an administrator cancelled (`jobs.cancelled_by` set) is not recreated by scanning, like a failed job; `jobs retry` brings it back. Jobs cancelled by the system (missing, withdrawn, changed) are still recreated when the cause goes away.
- **`ingest` is a request, not a synchronous scan.** It sets `storage_locations.scan_requested_at`; the scanner picks such locations within one tick and clears the request only if it is unchanged since the scan began (so a request during a scan is kept). `ingest` on a disabled location is `409`.
- **`jobs retry`** works on `failed` or `cancelled` jobs whose recording is still consented, present and unchanged, and which has no open job; it creates a new queued job with the old priority.
- **`jobs cancel`** on an already-cancelled job succeeds unchanged; on `completed`/`failed` it is `409`. **`jobs priority`** is limited to −1000…1000 on open jobs.
- **`followers drain`** works from `active` or `gone`; on `revoked` it is `409`. **`followers revoke`** releases every lease (outcome `released`, attempt not counted).
- **`locations add`** supports the `local` backend only (Azure and GCS arrive in Plan B), requires the root to be a folder on the replica that handles the request, names match `^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$`, and output always goes to the same location. **`locations disable`** stops scanning only; queued jobs stay claimable. **`locations enable`** is added so a disable can be undone.
- **`tokens list`** is added (admin role) so a token whose id was lost can still be revoked; it never shows the token.
- **Token lifetime** for `tokens create` is 1 minute to 90 days (default 7 days), `max_uses` 1 to 10 000 (default 1).
- **CLI**: the leader URL comes from `--leader`, `SWARMSCRIBE_LEADER_URL`, or the leader last signed in to; `SWARMSCRIBE_ADMIN_CREDENTIALS` overrides the credentials path; the ID token is refreshed when it has less than 2 minutes left, and once more if the leader answers `401 token_expired`. On Windows the credentials file relies on the user-profile ACL (POSIX modes do not apply); on POSIX it is written `0600` in a `0700` folder and tightened if found looser.
- **Compose test**: followers reach both replicas through nginx; the driver runs on the CI host, seeds the location and join token directly in Postgres (no identity provider in Compose) and kills `leader-1` with `docker compose kill` after two completions.

## File Structure

```
.dockerignore                                    NEW  keep the venv and caches out of image builds
.gitignore                                       MOD  e2e/compose/work/
.github/workflows/ci.yml                         MOD  new job compose-e2e
pyproject.toml                                   MOD  dev dependency pyyaml
README.md                                        MOD  sign-in setup, admin CLI, Compose test
e2e/compose/
  Dockerfile                                     NEW  leader image (leader package only)
  docker-compose.yml                             NEW  postgres, migrate, leader-1, leader-2, proxy
  nginx.conf                                     NEW  proxy that logs no links
  run_e2e.py                                     NEW  scripted followers, kill, assertions
packages/leader/
  pyproject.toml                                 MOD  pyjwt[crypto], httpx; swarmscribe-admin script
  src/swarmscribe_leader/
    config.py                                    MOD  sign-in and role settings
    errors.py                                    MOD  ServiceUnavailable, retry_after
    app.py                                       MOD  admin_auth, admin router
    reports.py                                   NEW  read-only views for administrators
    db/models.py                                 MOD  cancelled_by, no_speech, scan_requested_at
    db/migrations/versions/0003_admin.py         NEW
    storage/local.py                             MOD  walk from the prefix folder; validate_key
    jobs/store.py                                MOD  DB clock, OutputsCheck, submit off-lock, cancel by
    jobs/claims.py                               MOD  outputs check incl. no-speech; outputs_unchanged
    jobs/reaper.py                               MOD  audit rows
    jobs/admin.py                                NEW  retry, cancel, priority
    ingest/scanner.py                            MOD  admin-cancelled versions; scan requests
    ingest/locations.py                          NEW  add, enable/disable, request a scan
    auth/oidc.py                                 NEW  providers, JWKS cache, token verification
    auth/roles.py                                NEW  role mapping, resolver, Graph and Cloud Identity
    auth/followers.py                            MOD  drain, revoke, revoke token
    api/errors.py                                MOD  Retry-After for LeaderError
    api/files.py                                 MOD  filesystem work in threads
    api/follower.py                              MOD  push_back delay; submit commits auth first
    api/health.py                                MOD  readiness needs sign-in metadata
    api/admin_auth.py                            NEW  AdminAuth, Admin, require(role)
    api/admin_models.py                          NEW  request and response models
    api/admin.py                                 NEW  /v1/admin routes
    admin_cli/__init__.py                        NEW
    admin_cli/credentials.py                     NEW  SignIn, CredentialStore
    admin_cli/device_flow.py                     NEW  device-code sign-in, refresh
    admin_cli/client.py                          NEW  LeaderClient (refreshing, error mapping)
    admin_cli/main.py                            NEW  argparse commands and rendering
  tests/
    conftest.py                                  MOD  fake identity providers, directories, admin app
    test_local_storage.py, test_app.py           MOD  Task 1
    test_job_store.py, test_reaper.py,
    test_migrations.py                           MOD  Task 2, Task 3
    test_follower_api.py                         MOD  Task 3
    test_scanner.py                              MOD  Task 1, Task 4
    test_config.py                               MOD  Task 5
    test_oidc.py                                 NEW  Task 6
    test_roles.py                                NEW  Task 7
    test_admin_auth.py                           NEW  Task 8
    test_admin_api.py                            NEW  Task 9, Task 10
    test_admin_cli_auth.py                       NEW  Task 11
    test_admin_cli.py                            NEW  Task 12
    test_compose_driver.py                       NEW  Task 13
```

---

### Task 1: Local storage walks only the input folder; file routes stay off the event loop

**Files:**
- Modify: `packages/leader/src/swarmscribe_leader/storage/local.py` (`_list_sync`, new `_walk_start`)
- Modify: `packages/leader/src/swarmscribe_leader/api/files.py` (whole file)
- Test: `packages/leader/tests/test_local_storage.py`, `packages/leader/tests/test_scanner.py`, `packages/leader/tests/test_app.py`

**Interfaces:**
- Consumes: `LocalBackend.file_path(key) -> Path`, `LocalBackend._require_root()`, `version_of`, from Plan A1.
- Produces: `LocalBackend.list(prefix)` reads only the folder holding `prefix` (`prefix.rpartition("/")[0]`, or the root); a missing input folder raises `StorageUnavailable("input folder '…' is not available")`. `api/files.py` behaviour is unchanged except that every filesystem call runs in a worker thread.

- [ ] **Step 1: Write the failing tests**

Append to `packages/leader/tests/test_local_storage.py`:

```python
async def test_list_walks_only_the_folder_of_the_prefix(tmp_path, monkeypatch):
    write(tmp_path, "incoming/one.mp3")
    write(tmp_path, "elsewhere/two.mp3")
    real_walk = os.walk
    walked = []

    def walk(top, *args, **kwargs):
        walked.append(Path(top))
        yield from real_walk(top, *args, **kwargs)

    monkeypatch.setattr(os, "walk", walk)
    keys = [i.key for i in await collect(backend(tmp_path).list("incoming/"))]
    assert keys == ["incoming/one.mp3"]
    assert walked == [(tmp_path / "incoming").resolve()]


async def test_a_prefix_inside_a_folder_walks_that_folder(tmp_path):
    write(tmp_path, "incoming/2024-one.mp3")
    write(tmp_path, "incoming/2023-two.mp3")
    keys = [i.key for i in await collect(backend(tmp_path).list("incoming/2024-"))]
    assert keys == ["incoming/2024-one.mp3"]


async def test_a_missing_input_folder_fails_the_listing(tmp_path):
    write(tmp_path, "elsewhere/one.mp3")
    with pytest.raises(StorageUnavailable, match="input folder 'incoming' is not available"):
        await collect(backend(tmp_path).list("incoming/"))
```

In `packages/leader/tests/test_scanner.py`, add `from pathlib import Path` to the imports, then append:

```python
async def test_an_unreadable_folder_outside_the_input_prefix_does_not_stop_the_scan(
    sessionmaker, factory, tmp_path, monkeypatch
):
    # A drive root holds folders the leader may not read (e.g. system folders); only the
    # input folder matters.
    write(tmp_path, "consent.txt", b"**/*.mp3\n")
    write(tmp_path, "incoming/one.mp3")
    real_walk = os.walk

    def walk(top, topdown=True, onerror=None, followlinks=False):
        if Path(top).resolve() == tmp_path.resolve() and onerror is not None:
            onerror(PermissionError(13, "Access is denied", str(tmp_path / "locked")))
        yield from real_walk(top, topdown=topdown, onerror=onerror, followlinks=followlinks)

    monkeypatch.setattr(os, "walk", walk)
    summary = await scan(sessionmaker, await factory.location(input_prefix="incoming/"))
    assert summary.jobs_created == 1
```

In `packages/leader/tests/test_app.py`, add `import itertools` and `from swarmscribe_leader.storage.local import LocalBackend` to the imports, then append:

```python
async def test_a_slow_disk_does_not_block_the_event_loop_during_a_download(
    app, client, factory, tmp_path, monkeypatch
):
    write(tmp_path, "talks/one.mp3", b"the audio")
    location = await factory.location()
    backend = app.state.backend_factory(location)
    info = await backend.stat("talks/one.mp3")
    link = backend.download_link("talks/one.mp3", info.version, timedelta(minutes=5))
    real_require_root = LocalBackend._require_root

    def slow_require_root(self):
        time.sleep(0.5)
        real_require_root(self)

    monkeypatch.setattr(LocalBackend, "_require_root", slow_require_root)
    ticks: list[float] = []
    stop = asyncio.Event()

    async def ticker():
        while not stop.is_set():
            ticks.append(time.monotonic())
            await asyncio.sleep(0)

    task = asyncio.create_task(ticker())
    try:
        response = await client.get(link.url)
    finally:
        stop.set()
        await task
    assert (response.status_code, response.content) == (200, b"the audio")
    assert max(b - a for a, b in itertools.pairwise(ticks)) < 0.25


async def test_a_slow_disk_does_not_block_the_event_loop_during_an_upload(
    app, client, factory, tmp_path, monkeypatch
):
    location = await factory.location()
    link = await upload_link(app, factory, location, "transcripts/one.txt")
    real_require_root = LocalBackend._require_root

    def slow_require_root(self):
        time.sleep(0.5)
        real_require_root(self)

    monkeypatch.setattr(LocalBackend, "_require_root", slow_require_root)
    ticks: list[float] = []
    stop = asyncio.Event()

    async def ticker():
        while not stop.is_set():
            ticks.append(time.monotonic())
            await asyncio.sleep(0)

    task = asyncio.create_task(ticker())
    try:
        response = await client.put(link.url, content=b"text")
    finally:
        stop.set()
        await task
    assert response.status_code == 201
    assert (tmp_path / "transcripts" / "one.txt").read_bytes() == b"text"
    assert max(b - a for a, b in itertools.pairwise(ticks)) < 0.25
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest packages/leader/tests/test_local_storage.py packages/leader/tests/test_scanner.py packages/leader/tests/test_app.py -k "prefix or input_folder or slow_disk" -v`
Expected: FAIL — the walk starts at the root (walked is the root; the scan raises `StorageUnavailable`; no "input folder" error), and the slow-disk tests see a gap of about 0.5 s.

- [ ] **Step 3: Walk from the folder of the prefix**

In `packages/leader/src/swarmscribe_leader/storage/local.py`, replace the whole `_list_sync` method with these two methods:

```python
    def _walk_start(self, prefix: str) -> Path:
        """The folder holding every key that starts with `prefix`. The walk begins there, so
        folders outside the input folder (a drive root's system folders, the outputs) are
        never read."""
        folder = prefix.rpartition("/")[0]
        if not folder:
            return self.root
        start = self.file_path(folder)
        if not start.is_dir():
            raise StorageUnavailable(f"input folder {folder!r} is not available")
        return start

    def _list_sync(self, prefix: str) -> Sequence[ObjectInfo]:
        """Everything under `prefix`, or an error: a directory that cannot be read makes
        the listing fail rather than come back partial (a partial listing would mark the
        unseen recordings missing and cancel their jobs)."""
        self._require_root()
        start = self._walk_start(prefix)

        def unreadable(exc: OSError) -> None:
            raise StorageUnavailable(f"a directory cannot be listed: {exc.strerror}") from exc

        found: list[ObjectInfo] = []
        for dirpath, dirnames, filenames in os.walk(start, onerror=unreadable):
            dirnames[:] = sorted(d for d in dirnames if not self._is_link(Path(dirpath) / d))
            for name in sorted(filenames):
                full = Path(dirpath) / name
                if self._is_link(full):
                    continue
                key = full.relative_to(self.root).as_posix()
                if not key.startswith(prefix):
                    continue
                try:
                    self.path_for(key)
                    st = full.stat()
                except StorageError:
                    continue
                except FileNotFoundError:
                    continue  # removed while we listed: genuinely gone
                except OSError as exc:
                    raise StorageUnavailable(f"a file cannot be read: {exc.strerror}") from exc
                found.append(ObjectInfo(key=key, size=st.st_size, version=version_of(st)))
        return found
```

- [ ] **Step 4: Move every filesystem call in the file routes to a worker thread**

Replace `packages/leader/src/swarmscribe_leader/api/files.py` with:

```python
import contextlib
import os
import time
import uuid
from collections.abc import AsyncIterator, Callable
from functools import partial
from pathlib import Path
from typing import Any

import anyio.to_thread
from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from ..db.models import Job, StorageLocation
from ..errors import Forbidden, NotFound, PayloadTooLarge, PreconditionFailed, StaleLease
from ..storage.base import StorageError, StorageUnavailable
from ..storage.links import InvalidLink, LinkClaims
from ..storage.local import LocalBackend, version_of
from .errors import error_response

router = APIRouter(prefix="/v1/files")

MAX_UPLOAD_BYTES = 512 * 1024 * 1024
CHUNK_BYTES = 256 * 1024


async def _in_thread(function: Callable[..., Any], *args: Any) -> Any:
    """Filesystem calls can block for seconds on a slow or network disk: never on the loop."""
    return await anyio.to_thread.run_sync(function, *args)


def _claims(request: Request, token: str, method: str) -> LinkClaims:
    try:
        claims = request.app.state.signer.verify(token, now=time.time())
    except InvalidLink as exc:
        raise Forbidden("invalid or expired link") from exc
    if claims.method != method:
        raise Forbidden("this link does not allow that method")
    return claims


async def _backend(request: Request, claims: LinkClaims) -> LocalBackend:
    try:
        location_id = uuid.UUID(claims.location_id)
    except ValueError as exc:
        raise NotFound("no such location") from exc
    async with request.app.state.sessionmaker() as session:
        location = await session.get(StorageLocation, location_id)
    if location is None or location.backend != "local":
        raise NotFound("no such location")
    return request.app.state.backend_factory(location)


def _open_current(path: Path, version: str):
    """Open the file once and check its version on that same handle, so nothing can
    change between the check and the bytes we send."""
    try:
        handle = path.open("rb")
    except OSError as exc:
        if not _is_file(path):
            raise NotFound("file not found") from exc
        raise StorageUnavailable("the file cannot be opened") from exc
    try:
        stat_result = os.fstat(handle.fileno())
        if version and version_of(stat_result) != version:
            raise PreconditionFailed("the file changed after this link was issued")
    except OSError as exc:
        handle.close()
        raise StorageUnavailable("the file cannot be read") from exc
    except BaseException:
        handle.close()
        raise
    return handle, stat_result.st_size


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return True  # it may well be there; we cannot tell, so do not answer 404


async def _stream(handle) -> AsyncIterator[bytes]:
    try:
        while chunk := await _in_thread(handle.read, CHUNK_BYTES):
            yield chunk
    finally:
        handle.close()


@router.get("/{token}")
async def download(token: str, request: Request) -> StreamingResponse:
    claims = _claims(request, token, "GET")
    backend = await _backend(request, claims)
    path = await _in_thread(backend.file_path, claims.key)
    handle, size = await _in_thread(_open_current, path, claims.version)
    return StreamingResponse(
        _stream(handle),
        media_type="application/octet-stream",
        headers={"Content-Length": str(size)},
    )


async def _require_current_lease(request: Request, claims: LinkClaims) -> None:
    """The upload belongs to a lease; once that lease has ended (expired and re-leased,
    cancelled, completed), its links must not write anything."""
    try:
        job_id, lease_id = uuid.UUID(claims.job_id), uuid.UUID(claims.lease_id)
    except ValueError as exc:
        raise StaleLease("this upload link's lease is no longer current") from exc
    async with request.app.state.sessionmaker() as session:
        job = await session.get(Job, job_id)
    if job is None or job.state != "leased" or job.lease_id != lease_id:
        raise StaleLease("this upload link's lease is no longer current")


@router.put("/{token}", status_code=201)
async def upload(token: str, request: Request) -> Response:
    claims = _claims(request, token, "PUT")
    if not claims.job_id or not claims.lease_id:
        raise Forbidden("this upload link is not bound to a lease")
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
        raise PayloadTooLarge("upload is larger than 512 MiB")
    await _require_current_lease(request, claims)
    backend = await _backend(request, claims)
    path = await _in_thread(backend.file_path, claims.key)
    if await _in_thread(path.is_dir):
        raise StorageError(f"{claims.key!r} is a directory, not an object")
    temp = path.with_name(f".upload-{uuid.uuid4().hex}")
    size = 0
    try:
        await _in_thread(partial(path.parent.mkdir, parents=True, exist_ok=True))
        out = await _in_thread(temp.open, "wb")
        try:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise PayloadTooLarge("upload is larger than 512 MiB")
                await _in_thread(out.write, chunk)
        finally:
            await _in_thread(out.close)
        # Again, right before the target changes: the lease may have ended meanwhile.
        await _require_current_lease(request, claims)
        await _in_thread(os.replace, temp, path)
    except (NotADirectoryError, FileExistsError) as exc:
        raise StorageError(f"invalid storage key {claims.key!r}") from exc
    except PermissionError:
        return error_response(
            "conflict", "the file is in use; retry shortly", 409, headers={"Retry-After": "5"}
        )
    except OSError as exc:
        raise StorageUnavailable("the upload could not be stored") from exc
    finally:
        with contextlib.suppress(OSError):
            await _in_thread(partial(temp.unlink, missing_ok=True))
    return Response(status_code=201)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest packages/leader/tests/test_local_storage.py packages/leader/tests/test_scanner.py packages/leader/tests/test_app.py -v`
Expected: PASS, including every Plan A1 test in those files (the blocked-replace, upload-under-a-file and read-error tests patch `os.replace`/`os.fstat`, which are looked up at call time inside the threads).

- [ ] **Step 6: Commit**

```bash
git add packages/leader/src/swarmscribe_leader/storage/local.py packages/leader/src/swarmscribe_leader/api/files.py packages/leader/tests/test_local_storage.py packages/leader/tests/test_scanner.py packages/leader/tests/test_app.py
git commit -m "Leader: scans walk only the input folder; file routes never block the event loop

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Schema for administration, one clock for claim back-off, reaper audit rows

**Files:**
- Modify: `packages/leader/src/swarmscribe_leader/db/models.py`
- Create: `packages/leader/src/swarmscribe_leader/db/migrations/versions/0003_admin.py`
- Modify: `packages/leader/src/swarmscribe_leader/jobs/store.py` (`claim`, `push_back`)
- Modify: `packages/leader/src/swarmscribe_leader/api/follower.py` (the `push_back` call)
- Modify: `packages/leader/src/swarmscribe_leader/jobs/reaper.py`
- Test: `packages/leader/tests/test_migrations.py`, `packages/leader/tests/test_job_store.py`, `packages/leader/tests/test_reaper.py`

**Interfaces:**
- Produces (columns): `Job.cancelled_by: str | None` (the administrator's actor string, `None` when the system cancelled), `JobResult.no_speech: bool` (default `False`), `StorageLocation.scan_requested_at: datetime | None`. Alembic head becomes `"0003"`.
- Produces: `store.claim(...)` compares `Job.available_at <= func.now()` (the `now` argument still sets the lease expiry). `store.push_back(session, job_id, *, delay_seconds: int) -> None` sets `available_at = now() + delay` in SQL.
- Produces: the reaper writes `AuditEntry(actor="system", action="job.expire", subject_type="job", detail={"follower": <id>, "attempt": n, "state": "queued"|"failed"})` per expired lease and `AuditEntry(actor="system", action="follower.gone", subject_type="follower")` per follower marked gone.

- [ ] **Step 1: Write the failing tests**

In `packages/leader/tests/test_migrations.py`, change the head assertion:

```python
async def test_database_is_at_the_head_revision(engine):
    assert head_revision() == "0003"
    assert await current_revision(engine) == "0003"
```

In `packages/leader/tests/test_job_store.py`, change the sqlalchemy import to `from sqlalchemy import func, select, update`, replace `test_a_job_not_yet_available_is_not_claimed` with the version below, and add the two tests after it:

```python
async def test_a_job_not_yet_available_is_not_claimed(sessionmaker, factory):
    location = await factory.location()
    later = await factory.job(
        await factory.recording(location, key="later.mp3"),
        priority=9,
        available_at=utcnow() + timedelta(seconds=60),
    )
    ready = await factory.job(await factory.recording(location, key="ready.mp3"))
    follower, _ = await factory.follower()
    assert (await claim(sessionmaker, follower)).id == ready.id
    assert await claim(sessionmaker, follower) is None
    async with sessionmaker() as session:
        await session.execute(
            update(Job)
            .where(Job.id == later.id)
            .values(available_at=func.now() - timedelta(seconds=1))
        )
        await session.commit()
    assert (await claim(sessionmaker, follower)).id == later.id


async def test_availability_is_judged_by_the_database_clock(sessionmaker, factory):
    # A replica whose clock runs an hour fast must not hand out a pushed-back job early.
    await factory.job(available_at=utcnow() + timedelta(minutes=10))
    follower, _ = await factory.follower()
    assert await claim(sessionmaker, follower, now=utcnow() + timedelta(hours=1)) is None


async def test_push_back_counts_from_the_database_clock(sessionmaker, factory):
    job = await factory.job()
    async with sessionmaker() as session:
        await store.push_back(session, job.id, delay_seconds=60)
        await session.commit()
    async with sessionmaker() as session:
        database_now = await session.scalar(select(func.now()))
    stored = await load(sessionmaker, job.id)
    assert timedelta(seconds=50) <= stored.available_at - database_now <= timedelta(seconds=60)
```

In `packages/leader/tests/test_reaper.py`, add `AuditEntry` to the `swarmscribe_leader.db.models` import and append:

```python
async def test_reaper_transitions_are_audited(sessionmaker, factory):
    job = await factory.job()
    holder, _ = await factory.follower()
    start = utcnow()
    await claim(sessionmaker, holder, now=start)
    silent, _ = await factory.follower(last_seen_at=start - timedelta(hours=1))
    await run_reaper(sessionmaker, now=start + timedelta(seconds=121))
    async with sessionmaker() as session:
        entries = (
            await session.scalars(select(AuditEntry).where(AuditEntry.actor == "system"))
        ).all()
    (expired,) = [e for e in entries if e.action == "job.expire"]
    assert (expired.subject_type, expired.subject_id) == ("job", str(job.id))
    assert expired.detail == {"follower": str(holder.id), "attempt": 1, "state": "queued"}
    (gone,) = [e for e in entries if e.action == "follower.gone"]
    assert (gone.subject_type, gone.subject_id) == ("follower", str(silent.id))
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest packages/leader/tests/test_migrations.py packages/leader/tests/test_job_store.py packages/leader/tests/test_reaper.py -v`
Expected: FAIL — head is `0002`; a fast caller clock claims the pushed-back job; `push_back` has no `delay_seconds`; no audit rows from the reaper.

- [ ] **Step 3: Add the columns to the models**

In `packages/leader/src/swarmscribe_leader/db/models.py`, add `false` to the `sqlalchemy` import, then:

In `StorageLocation`, after `last_scan_error`:

```python
    # Set by `ingest`; the scanner clears it once a scan that began after it has finished.
    scan_requested_at: Mapped[datetime | None]
```

In `Job`, after `failure_reason`:

```python
    # The administrator who cancelled the job; None when the system cancelled it. Scanning
    # never recreates a job an administrator cancelled.
    cancelled_by: Mapped[str | None] = mapped_column(Text)
```

In `JobResult`, after `low_confidence_words`:

```python
    # The recording has no speech: empty .txt and .srt, a segments.json without segments.
    no_speech: Mapped[bool] = mapped_column(default=False, server_default=false())
```

- [ ] **Step 4: Write migration 0003**

`packages/leader/src/swarmscribe_leader/db/migrations/versions/0003_admin.py`:

```python
"""administration: who cancelled a job, no-speech results, on-demand scan requests

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-03

Hand-written.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("cancelled_by", sa.Text(), nullable=True))
    op.add_column(
        "job_results",
        sa.Column("no_speech", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "storage_locations",
        sa.Column("scan_requested_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("storage_locations", "scan_requested_at")
    op.drop_column("job_results", "no_speech")
    op.drop_column("jobs", "cancelled_by")
```

- [ ] **Step 5: Use the database clock for availability**

In `packages/leader/src/swarmscribe_leader/jobs/store.py`, change the import to `from sqlalchemy import func, select, update`. In `claim`, replace the condition `Job.available_at <= now,` with:

```python
            # The database's clock, the same one that stamped available_at: replicas' clocks
            # may differ from it and from each other.
            Job.available_at <= func.now(),
```

Replace `push_back` with:

```python
async def push_back(session: AsyncSession, job_id: uuid.UUID, *, delay_seconds: int) -> None:
    """Make a queued job unclaimable for `delay_seconds`, counted on the database's clock.
    Never waits: a job someone else has locked meanwhile (e.g. just leased it) is left to
    them."""
    lockable = (
        select(Job.id)
        .where(Job.id == job_id, Job.state == "queued")
        .with_for_update(skip_locked=True)
    )
    await session.execute(
        update(Job)
        .where(Job.id.in_(lockable))
        .values(available_at=func.now() + timedelta(seconds=delay_seconds))
        .execution_options(synchronize_session=False)
    )
```

In `packages/leader/src/swarmscribe_leader/api/follower.py`, replace the `push_back` call in `claim_job` with:

```python
            await store.push_back(session, job_id, delay_seconds=UNBUILDABLE_BACKOFF_SECONDS)
```

and remove `timedelta` from the `datetime` import if nothing else in the file uses it (`from datetime import timedelta` becomes unused: delete that line).

- [ ] **Step 6: Audit the reaper's transitions**

Replace `expire_leases` and `mark_gone` in `packages/leader/src/swarmscribe_leader/jobs/reaper.py` (add `from .. import audit` to the imports):

```python
async def expire_leases(session: AsyncSession, *, now: datetime) -> tuple[int, int]:
    """Return expired leases to the queue (or fail them). Locks job rows only, and skips
    any another transaction holds (a submit in progress). Returns (requeued, failed)."""
    jobs = (
        await session.scalars(
            select(Job)
            .where(Job.state == "leased", Job.lease_expires_at < now)
            .order_by(Job.id)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
    ).all()
    requeued = failed = 0
    for job in jobs:
        await close_attempt(session, job, "expired", "lease expired", now)
        if job.attempts >= job.max_attempts:
            job.state = "failed"
            job.failure_reason = "lease expired too many times"
            failed += 1
        else:
            job.state = "queued"
            requeued += 1
        audit.record(
            session,
            actor="system",
            action="job.expire",
            subject_type="job",
            subject_id=job.id,
            detail={"follower": str(job.leased_by), "attempt": job.attempts, "state": job.state},
        )
        clear_lease(job)
    return requeued, failed


async def mark_gone(session: AsyncSession, *, now: datetime, gone_after: timedelta) -> int:
    """Mark silent, lease-less `active` followers gone. Locks follower rows only, and skips
    any another transaction holds (a request from that follower is in flight). A draining
    follower is never touched: gone -> active on its next call would end the drain."""
    holding = select(Job.leased_by).where(Job.state == "leased", Job.leased_by.is_not(None))
    silent = (
        select(Follower.id)
        .where(
            Follower.state == "active",
            Follower.last_seen_at < now - gone_after,
            Follower.id.not_in(holding),
        )
        .with_for_update(skip_locked=True)
    )
    result = await session.execute(
        update(Follower)
        .where(Follower.id.in_(silent))
        .values(state="gone")
        .returning(Follower.id)
        .execution_options(synchronize_session=False)
    )
    gone = result.scalars().all()
    for follower_id in gone:
        audit.record(
            session,
            actor="system",
            action="follower.gone",
            subject_type="follower",
            subject_id=follower_id,
        )
    return len(gone)
```

- [ ] **Step 7: Migrate the test database and run the tests**

The session-scoped test database is recreated and migrated on every run, so nothing extra is needed.

Run: `uv run pytest packages/leader -q`
Expected: PASS (`test_migrations_produce_exactly_the_models` confirms 0003 matches the models; `test_a_whole_broken_location_ahead_of_the_queue_does_not_stall_it` still sees `available_at` about 60 s ahead).

- [ ] **Step 8: Commit**

```bash
git add packages/leader/src/swarmscribe_leader/db packages/leader/src/swarmscribe_leader/jobs/store.py packages/leader/src/swarmscribe_leader/jobs/reaper.py packages/leader/src/swarmscribe_leader/api/follower.py packages/leader/tests/test_migrations.py packages/leader/tests/test_job_store.py packages/leader/tests/test_reaper.py
git commit -m "Leader: schema for administration; claim back-off on the database clock; reaper audit rows

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Submit hashes outputs before locking the job and accepts a no-speech result

**Files:**
- Modify: `packages/leader/src/swarmscribe_leader/jobs/store.py` (`OutputsCheck`, `_OUTPUT_PROBLEMS`, `submit`)
- Modify: `packages/leader/src/swarmscribe_leader/jobs/claims.py` (`OutputProblem`, `outputs_verified`, new `outputs_unchanged`, `_has_no_segments`)
- Modify: `packages/leader/src/swarmscribe_leader/api/follower.py` (`submit` route)
- Test: `packages/leader/tests/test_job_store.py`, `packages/leader/tests/test_follower_api.py`

**Interfaces:**
- Consumes: `JobResult.no_speech` (Task 2), `LocalBackend.stat/sha256/read_text`, `swarmscribe_protocol.SegmentsDocument`.
- Produces (in `jobs/store.py`):
  - `@dataclass(frozen=True) class OutputsCheck: problem: str | None = None; no_speech: bool = False; versions: tuple[tuple[str, str], ...] = ()` — `versions` is `(storage key, version)` for each output as it was hashed.
  - `async def submit(session, job_id, request, follower, *, now, outputs_verified: Callable[[Job, OutputChecksums], Awaitable[OutputsCheck]], outputs_unchanged: Callable[[Job, OutputsCheck], Awaitable[bool]] | None = None) -> None`. New conflict codes: `outputs_inconsistent`, `outputs_changed`.
- Produces (in `jobs/claims.py`): `OutputProblem = Literal["outputs_missing", "checksum_mismatch", "outputs_inconsistent"]`; `async def outputs_verified(session, job, checksums, *, backend_factory) -> OutputsCheck`; `async def outputs_unchanged(session, job, check, *, backend_factory) -> bool`; `NO_SPEECH_SEGMENTS_LIMIT = 1024 * 1024`.

- [ ] **Step 1: Write the failing store tests**

In `packages/leader/tests/test_job_store.py`, replace the three verifier helpers at the top with:

```python
async def present(_job, _checksums):
    return store.OutputsCheck()


async def absent(_job, _checksums):
    return store.OutputsCheck(problem="outputs_missing")


async def mismatched(_job, _checksums):
    return store.OutputsCheck(problem="checksum_mismatch")
```

Append to the `# --- submit ---` section:

```python
async def test_outputs_are_hashed_before_the_job_row_is_locked(sessionmaker, factory):
    job = await factory.job()
    follower, _ = await factory.follower()
    claimed = await claim(sessionmaker, follower)
    locked_while_hashing = []

    async def verifier(job_seen, _checksums):
        async with sessionmaker() as other:
            row = await other.scalar(
                select(Job).where(Job.id == job_seen.id).with_for_update(skip_locked=True)
            )
            locked_while_hashing.append(row is None)
            await other.rollback()
        return store.OutputsCheck()

    async with sessionmaker() as session:
        await store.submit(
            session,
            job.id,
            submission(claimed.lease_id),
            follower,
            now=utcnow(),
            outputs_verified=verifier,
        )
        await session.commit()
    assert locked_while_hashing == [False]
    assert (await load(sessionmaker, job.id)).state == "completed"


async def test_outputs_replaced_after_hashing_are_refused(sessionmaker, factory):
    job = await factory.job()
    follower, _ = await factory.follower()
    claimed = await claim(sessionmaker, follower)

    async def replaced(_job, _check):
        return False

    async with sessionmaker() as session:
        with pytest.raises(Conflict) as excinfo:
            await store.submit(
                session,
                job.id,
                submission(claimed.lease_id),
                follower,
                now=utcnow(),
                outputs_verified=present,
                outputs_unchanged=replaced,
            )
    assert excinfo.value.code == "outputs_changed"
    assert (await load(sessionmaker, job.id)).state == "leased"


async def test_a_lease_lost_while_the_outputs_were_hashed_is_refused(sessionmaker, factory):
    job = await factory.job()
    follower, _ = await factory.follower()
    claimed = await claim(sessionmaker, follower)

    async def reaped_meanwhile(job_seen, _checksums):
        async with sessionmaker() as other:
            row = await other.get(Job, job_seen.id, with_for_update=True)
            row.state = "queued"
            store.clear_lease(row)
            await other.commit()
        return store.OutputsCheck()

    async with sessionmaker() as session:
        with pytest.raises(StaleLease):
            await store.submit(
                session,
                job.id,
                submission(claimed.lease_id),
                follower,
                now=utcnow(),
                outputs_verified=reaped_meanwhile,
            )
    assert (await load(sessionmaker, job.id)).state == "queued"


async def test_a_no_speech_result_is_recorded(sessionmaker, factory):
    job = await factory.job()
    follower, _ = await factory.follower()
    claimed = await claim(sessionmaker, follower)

    async def silent(_job, _checksums):
        return store.OutputsCheck(no_speech=True)

    async with sessionmaker() as session:
        await store.submit(
            session,
            job.id,
            submission(claimed.lease_id),
            follower,
            now=utcnow(),
            outputs_verified=silent,
        )
        await session.commit()
    async with sessionmaker() as session:
        result = (await session.scalars(select(JobResult))).one()
    assert result.no_speech is True
```

- [ ] **Step 2: Write the failing API tests**

In `packages/leader/tests/test_follower_api.py`, add `import json` to the imports and `JobResult` to the `swarmscribe_leader.db.models` import, then append:

```python
def segments_document(source_sha: str, segments: list[dict]) -> bytes:
    return json.dumps(
        {
            "schema_version": 1,
            "source_checksum": source_sha,
            "duration": 12.5,
            "device": "cpu",
            "engine_version": "0.1.0",
            "settings": {"model": "distil-large-v3", "compute_type": "int8"},
            "vocabulary_version": 0,
            "vocabulary_terms_used": [],
            "corrections_applied": [],
            "segments": segments,
        }
    ).encode()


ONE_SEGMENT = [{"start": 0.0, "end": 1.0, "text": "one two", "words": []}]


async def submit_outputs(client, headers, claimed, source: bytes, txt, srt, segments):
    checksums = {"source": sha(source)}
    for name, body in (("txt", txt), ("srt", srt), ("segments_json", segments)):
        link = getattr(claimed.upload_urls, name)
        assert (await client.put(link.url, content=body)).status_code == 201
        checksums[name] = sha(body)
    return await client.post(
        f"/v1/jobs/{claimed.job_id}/submit",
        headers=headers,
        json={"lease_id": claimed.lease_id, "checksums": checksums},
    )


async def test_a_recording_without_speech_completes_with_an_empty_transcript(
    client, sessionmaker, factory, tmp_path
):
    await queue_one(sessionmaker, factory, tmp_path, data=b"quiet")
    headers = await register(client, sessionmaker)
    claimed = await claim(client, headers)
    response = await submit_outputs(
        client, headers, claimed, b"quiet", b"", b"", segments_document(sha(b"quiet"), [])
    )
    assert response.status_code == 200, response.text
    async with sessionmaker() as session:
        job = await session.get(Job, uuid.UUID(claimed.job_id))
        result = (await session.scalars(select(JobResult))).one()
    assert (job.state, result.no_speech) == ("completed", True)


@pytest.mark.parametrize(
    "txt, srt, segments, code",
    [
        (b"", b"", ONE_SEGMENT, "outputs_inconsistent"),
        (b"", b"1\n00:00:00,000 --> 00:00:01,000\none two\n", [], "outputs_inconsistent"),
        (b"", b"", None, "outputs_inconsistent"),
        (b"one two\n", b"1\n00:00:00,000 --> 00:00:01,000\none two\n", b"", "outputs_missing"),
    ],
    ids=["empty-text-with-segments", "empty-text-only", "unparseable-segments", "empty-segments"],
)
async def test_an_inconsistent_empty_result_is_refused(
    client, sessionmaker, factory, tmp_path, txt, srt, segments, code
):
    await queue_one(sessionmaker, factory, tmp_path, data=b"quiet")
    headers = await register(client, sessionmaker)
    claimed = await claim(client, headers)
    if segments is None:
        body = b"{not json"
    elif isinstance(segments, bytes):
        body = segments
    else:
        body = segments_document(sha(b"quiet"), segments)
    response = await submit_outputs(client, headers, claimed, b"quiet", txt, srt, body)
    assert (response.status_code, response.json()["code"]) == (409, code)
    async with sessionmaker() as session:
        job = await session.get(Job, uuid.UUID(claimed.job_id))
    assert job.state == "leased"


async def test_submit_does_not_hold_the_followers_row_while_hashing(
    client, app, sessionmaker, factory, tmp_path, monkeypatch
):
    await queue_one(sessionmaker, factory, tmp_path)
    headers = await register(client, sessionmaker)
    claimed = await claim(client, headers)
    checksums = await upload_outputs(client, claimed)
    checksums["source"] = sha(b"audio bytes")
    follower_locked = []
    real_sha256 = LocalBackend.sha256

    async def watching_sha256(self, key):
        async with sessionmaker() as other:
            row = await other.scalar(
                select(Follower).with_for_update(skip_locked=True).limit(1)
            )
            follower_locked.append(row is None)
            await other.rollback()
        return await real_sha256(self, key)

    monkeypatch.setattr(LocalBackend, "sha256", watching_sha256)
    response = await client.post(
        f"/v1/jobs/{claimed.job_id}/submit",
        headers=headers,
        json={"lease_id": claimed.lease_id, "checksums": checksums},
    )
    assert response.status_code == 200, response.text
    assert follower_locked and not any(follower_locked)
```

Also add `from swarmscribe_leader.storage.local import LocalBackend` to that file's imports.

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest packages/leader/tests/test_job_store.py packages/leader/tests/test_follower_api.py -k "submit or speech or empty or hashed or replaced or lease_lost" -v`
Expected: FAIL — `store.OutputsCheck` does not exist; the empty transcript is `409 outputs_missing`; the row is locked while hashing.

- [ ] **Step 4: Change the store**

In `packages/leader/src/swarmscribe_leader/jobs/store.py`, add `from dataclasses import dataclass` to the imports, replace `_OUTPUT_PROBLEMS` with the block below, and replace `submit` (keep `_same_checksums` as it is):

```python
_OUTPUT_PROBLEMS = {
    "outputs_missing": "the outputs are not in storage yet",
    "checksum_mismatch": "the stored outputs do not match the submitted checksums",
    "outputs_inconsistent": (
        "an empty transcript needs an empty .txt and .srt and a segments.json without segments"
    ),
    "outputs_changed": "the outputs changed while they were being checked; submit again",
}


@dataclass(frozen=True)
class OutputsCheck:
    """What checking the stored outputs found: the problem (None when there is none),
    whether they are a no-speech result, and each output's (key, version) as it was hashed."""

    problem: str | None = None
    no_speech: bool = False
    versions: tuple[tuple[str, str], ...] = ()
```

```python
async def submit(
    session: AsyncSession,
    job_id: uuid.UUID,
    request: SubmitRequest,
    follower: Follower,
    *,
    now: datetime,
    outputs_verified: Callable[[Job, OutputChecksums], Awaitable[OutputsCheck]],
    outputs_unchanged: Callable[[Job, OutputsCheck], Awaitable[bool]] | None = None,
) -> None:
    """Complete the job.

    The outputs are hashed before the job row is locked: hashing reads every byte, and the
    reaper and administrators must not wait for it. Under the lock the lease is checked again
    and `outputs_unchanged` confirms that no output was replaced after it was hashed.
    """
    job = await session.get(Job, job_id, populate_existing=True)
    if job is None:
        raise NotFound("no such job")
    check: OutputsCheck | None = None
    if job.state != "completed":
        _require_lease(job, request.lease_id, follower)
        check = await outputs_verified(job, request.checksums)
        if check.problem is not None:
            message = _OUTPUT_PROBLEMS.get(check.problem, "the outputs cannot be verified")
            raise Conflict(message, code=check.problem)
    job = await _locked_job(session, job_id)
    if job.state == "completed":
        result = await session.scalar(select(JobResult).where(JobResult.job_id == job.id))
        if (
            job.lease_id == _parse_lease(request.lease_id)
            and job.leased_by == follower.id
            and result is not None
            and _same_checksums(result, request)
        ):
            return
        raise StaleLease("this job is already completed")
    _require_lease(job, request.lease_id, follower)
    if check is None:
        raise StaleLease("this job is not leased to you with that lease")
    if outputs_unchanged is not None and not await outputs_unchanged(job, check):
        raise Conflict(_OUTPUT_PROBLEMS["outputs_changed"], code="outputs_changed")
    c = request.checksums
    session.add(
        JobResult(
            job_id=job.id,
            source_sha256=c.source,
            txt_sha256=c.txt,
            srt_sha256=c.srt,
            segments_sha256=c.segments_json,
            no_speech=check.no_speech,
        )
    )
    await close_attempt(session, job, "completed", None, now)
    job.state = "completed"
    job.completed_at = now
    job.lease_expires_at = None
    audit.record(
        session,
        actor=f"follower:{follower.id}",
        action="job.submit",
        subject_type="job",
        subject_id=job.id,
        detail={"no_speech": check.no_speech},
    )
```

- [ ] **Step 5: Check the outputs, including the no-speech case**

In `packages/leader/src/swarmscribe_leader/jobs/claims.py`:

Change the imports to:

```python
import hashlib
from collections.abc import Callable
from datetime import timedelta
from typing import Literal

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from swarmscribe_protocol import (
    ClaimResponse,
    JobSettings,
    OutputChecksums,
    SegmentsDocument,
    UploadUrls,
    Vocabulary,
)

from ..config import Settings
from ..db.models import Follower, Job, Recording, SettingsProfile, StorageLocation
from ..errors import LeaderError
from ..storage.base import StorageBackend, StorageError, StorageUnavailable
from .store import OutputsCheck
```

Replace the `OutputProblem` line with:

```python
OutputProblem = Literal["outputs_missing", "checksum_mismatch", "outputs_inconsistent"]
NO_SPEECH_SEGMENTS_LIMIT = 1024 * 1024
```

Replace everything from `EMPTY_SHA256 = …` to the end of the file with:

```python
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


async def _has_no_segments(backend: StorageBackend, key: str) -> bool:
    """Whether segments.json is a valid, small document listing no segments: the only
    thing an empty .txt and .srt may sit beside."""
    info = await backend.stat(key)
    if info is None or info.size > NO_SPEECH_SEGMENTS_LIMIT:
        return False
    try:
        text = await backend.read_text(key)
        document = SegmentsDocument.model_validate_json(text or "")
    except StorageUnavailable:
        raise
    except (StorageError, ValidationError):
        return False
    return not document.segments


async def outputs_verified(
    session: AsyncSession,
    job: Job,
    checksums: OutputChecksums,
    *,
    backend_factory: BackendFactory,
) -> OutputsCheck:
    """Hash the three stored outputs and compare them with the submitted checksums.

    Each output's version is noted before it is hashed, so the caller can confirm later,
    cheaply, that nothing was replaced. A missing output, or an empty segments.json, is
    `outputs_missing`. An empty .txt and .srt are a no-speech result, accepted only together
    and only beside a segments.json with no segments.
    """
    recording, source, target = await _places(session, job)
    backend = backend_factory(target)
    keys = output_keys(source.output_prefix, recording.key)
    digests: dict[str, str] = {}
    versions: list[tuple[str, str]] = []
    for name, key in keys.items():
        before = await backend.stat(key)
        digest = await backend.sha256(key)
        if before is None or digest is None:
            return OutputsCheck(problem="outputs_missing")
        digests[name] = digest
        versions.append((key, before.version))
    if digests["segments_json"] == EMPTY_SHA256:
        return OutputsCheck(problem="outputs_missing")
    if any(digests[name] != getattr(checksums, name) for name in keys):
        return OutputsCheck(problem="checksum_mismatch")
    empty = {name for name in ("txt", "srt") if digests[name] == EMPTY_SHA256}
    if empty and (
        empty != {"txt", "srt"} or not await _has_no_segments(backend, keys["segments_json"])
    ):
        return OutputsCheck(problem="outputs_inconsistent")
    return OutputsCheck(no_speech=bool(empty), versions=tuple(versions))


async def outputs_unchanged(
    session: AsyncSession,
    job: Job,
    check: OutputsCheck,
    *,
    backend_factory: BackendFactory,
) -> bool:
    """Whether every output still has the version it had when it was hashed."""
    _recording, _source, target = await _places(session, job)
    backend = backend_factory(target)
    for key, version in check.versions:
        info = await backend.stat(key)
        if info is None or info.version != version:
            return False
    return True
```

- [ ] **Step 6: Commit the authentication before hashing**

In `packages/leader/src/swarmscribe_leader/api/follower.py`, change the claims import to `from ..jobs.claims import build_claim, device_of, outputs_unchanged, outputs_verified, profile_for` and replace the `submit` route with:

```python
@router.post("/jobs/{job_id}/submit", response_model=SubmitResponse)
async def submit(
    job_id: uuid.UUID,
    body: SubmitRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    follower: Annotated[Follower, Depends(current_follower)],
) -> SubmitResponse:
    # Authentication locked the follower's row. Commit it now, so hashing the outputs never
    # holds that lock (the follower's heartbeats for its other jobs would wait on it).
    await session.commit()
    backend_factory = request.app.state.backend_factory

    async def verified(job: Job, checksums: OutputChecksums) -> store.OutputsCheck:
        return await outputs_verified(session, job, checksums, backend_factory=backend_factory)

    async def unchanged(job: Job, check: store.OutputsCheck) -> bool:
        return await outputs_unchanged(session, job, check, backend_factory=backend_factory)

    await store.submit(
        session,
        job_id,
        body,
        follower,
        now=utcnow(),
        outputs_verified=verified,
        outputs_unchanged=unchanged,
    )
    await session.commit()
    return SubmitResponse(accepted=True)
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest packages/leader -q`
Expected: PASS, including the Plan A1 submit tests (idempotent submit, checksum mismatch, outputs missing) and the in-process end-to-end test.

- [ ] **Step 8: Commit**

```bash
git add packages/leader/src/swarmscribe_leader/jobs packages/leader/src/swarmscribe_leader/api/follower.py packages/leader/tests/test_job_store.py packages/leader/tests/test_follower_api.py
git commit -m "Leader: submit hashes outputs without holding locks and accepts a no-speech result

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Scanner honours administrators' cancellations and on-demand scan requests

**Files:**
- Modify: `packages/leader/src/swarmscribe_leader/jobs/store.py` (`cancel`)
- Modify: `packages/leader/src/swarmscribe_leader/ingest/scanner.py` (`scan_location`'s job creation, `scan_due_locations`)
- Test: `packages/leader/tests/test_scanner.py`

**Interfaces:**
- Consumes: `Job.cancelled_by`, `StorageLocation.scan_requested_at` (Task 2).
- Produces: `store.cancel(session, job, *, now, reason, by: str | None = None) -> None` — `by` is stored in `job.cancelled_by`.
- Produces: scanning creates a job for a consented recording's version unless a job for that version exists that is not cancelled **or** was cancelled by an administrator.
- Produces: `scan_due_locations` also scans every enabled location whose `scan_requested_at` is set, and afterwards (success or failure) clears `scan_requested_at` only if it still holds the value read before the scan.

- [ ] **Step 1: Write the failing tests**

In `packages/leader/tests/test_scanner.py`, add `update` to the `sqlalchemy` import, then append:

```python
async def test_a_version_an_administrator_cancelled_is_not_queued_again(
    sessionmaker, factory, tmp_path
):
    write(tmp_path, "consent.txt", b"**/*.mp3\n")
    write(tmp_path, "talks/one.mp3")
    location = await factory.location()
    await scan(sessionmaker, location)
    async with sessionmaker() as session:
        (job,) = (await session.scalars(select(Job).with_for_update())).all()
        await store.cancel(
            session, job, now=utcnow(), reason="cancelled by an administrator", by="admin-1"
        )
        await session.commit()
    assert (await scan(sessionmaker, location)).jobs_created == 0
    write(tmp_path, "talks/one.mp3", b"a new version of the audio")
    assert (await scan(sessionmaker, location)).jobs_created == 1


async def test_a_requested_scan_runs_before_the_location_is_due(sessionmaker, factory, tmp_path):
    write(tmp_path, "consent.txt", b"**/*.mp3\n")
    write(tmp_path, "talks/one.mp3")
    now = utcnow()
    location = await factory.location(
        last_scan_at=now - timedelta(seconds=10), scan_requested_at=now - timedelta(seconds=1)
    )
    results = await scan_due_locations(sessionmaker, backends, now=now, max_attempts=3)
    assert results[location.name].jobs_created == 1
    async with sessionmaker() as session:
        assert (await session.get(StorageLocation, location.id)).scan_requested_at is None


async def test_a_scan_request_made_during_a_scan_is_kept(sessionmaker, factory, tmp_path):
    write(tmp_path, "consent.txt", b"**/*.mp3\n")
    write(tmp_path, "talks/one.mp3")
    now = utcnow()
    location = await factory.location(scan_requested_at=now - timedelta(seconds=5))

    def requesting_backends(loc):
        inner = backends(loc)

        class RequestsAgainWhileListing:
            async def stat(self, key):
                return await inner.stat(key)

            async def read_text(self, key):
                return await inner.read_text(key)

            async def list(self, prefix=""):
                async with sessionmaker() as other:
                    await other.execute(
                        update(StorageLocation)
                        .where(StorageLocation.id == loc.id)
                        .values(scan_requested_at=utcnow())
                    )
                    await other.commit()
                async for obj in inner.list(prefix):
                    yield obj

        return RequestsAgainWhileListing()

    results = await scan_due_locations(sessionmaker, requesting_backends, now=now, max_attempts=3)
    assert results[location.name].jobs_created == 1
    async with sessionmaker() as session:
        assert (await session.get(StorageLocation, location.id)).scan_requested_at is not None


async def test_a_requested_scan_that_fails_is_not_retried_every_tick(
    sessionmaker, factory, tmp_path
):
    now = utcnow()
    broken = await factory.location(
        backend="azure", config={}, last_scan_at=now, scan_requested_at=now
    )
    results = await scan_due_locations(sessionmaker, backends, now=now, max_attempts=3)
    assert "not available" in results[broken.name]
    async with sessionmaker() as session:
        stored = await session.get(StorageLocation, broken.id)
    assert stored.scan_requested_at is None
    assert "not available" in stored.last_scan_error
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest packages/leader/tests/test_scanner.py -k "administrator or requested or request_made" -v`
Expected: FAIL — `cancel()` has no `by`; requested locations that are not due are skipped.

- [ ] **Step 3: Record who cancelled**

In `packages/leader/src/swarmscribe_leader/jobs/store.py`, replace `cancel` with:

```python
async def cancel(
    session: AsyncSession, job: Job, *, now: datetime, reason: str, by: str | None = None
) -> None:
    """Cancel a queued or leased job. A leased job keeps its lease id so the holder hears
    cancel. `by` names the administrator; None means the system cancelled it."""
    if job.state not in OPEN_STATES:
        return
    if job.state == "leased":
        await close_attempt(session, job, "cancelled", reason, now)
        job.lease_expires_at = None
    job.state = "cancelled"
    job.failure_reason = reason
    job.cancelled_by = by
```

- [ ] **Step 4: Keep administrators' cancellations and serve scan requests**

In `packages/leader/src/swarmscribe_leader/ingest/scanner.py`, change the sqlalchemy import to `from sqlalchemy import or_, select, update`.

In `scan_location`, in the `have` query, replace `Job.state != "cancelled",` with:

```python
                    # A version the system cancelled (missing, withdrawn, changed) is queued
                    # again once the cause is gone; one an administrator cancelled is not.
                    or_(Job.state != "cancelled", Job.cancelled_by.is_not(None)),
```

Replace `scan_due_locations` with:

```python
def _is_due(location: StorageLocation, now: datetime) -> bool:
    return (
        location.scan_requested_at is not None
        or location.last_scan_at is None
        or location.last_scan_at + timedelta(seconds=location.scan_interval_s) <= now
    )


async def scan_due_locations(
    sessionmaker: async_sessionmaker[AsyncSession],
    backend_factory: Callable[[StorageLocation], StorageBackend],
    *,
    now: datetime,
    max_attempts: int,
) -> dict[str, ScanSummary | str]:
    """Scan every enabled location that is due or has a scan requested.

    A request is cleared only if it is unchanged since this scan began: one made while the
    scan ran (after storage was read) is kept and causes another scan on the next tick.
    """
    async with sessionmaker() as session:
        locations = (
            await session.scalars(select(StorageLocation).where(StorageLocation.enabled.is_(True)))
        ).all()
    results: dict[str, ScanSummary | str] = {}
    for loc in [loc for loc in locations if _is_due(loc, now)]:
        requested = loc.scan_requested_at
        try:
            # Read storage first, outside any transaction: a slow listing must not hold a
            # database connection idle in a transaction or the location row lock.
            snapshot = await take_snapshot(backend_factory(loc), loc.input_prefix)
            async with sessionmaker() as session:
                location = await session.get(StorageLocation, loc.id, with_for_update=True)
                if (location.backend, location.config, location.input_prefix) != (
                    loc.backend,
                    loc.config,
                    loc.input_prefix,
                ):
                    snapshot = None  # edited meanwhile: read it again under the lock
                results[loc.name] = await scan_location(
                    session,
                    location,
                    backend_factory(location),
                    now=now,
                    max_attempts=max_attempts,
                    snapshot=snapshot,
                )
                if requested is not None and location.scan_requested_at == requested:
                    location.scan_requested_at = None
                await session.commit()
        except Exception as exc:
            logger.exception("scanning location %s failed", loc.name)
            results[loc.name] = str(exc)
            async with sessionmaker() as session:
                await session.execute(
                    update(StorageLocation)
                    .where(StorageLocation.id == loc.id)
                    .values(last_scan_at=now, last_scan_error=str(exc)[:2000])
                )
                if requested is not None:
                    await session.execute(
                        update(StorageLocation)
                        .where(
                            StorageLocation.id == loc.id,
                            StorageLocation.scan_requested_at == requested,
                        )
                        .values(scan_requested_at=None)
                    )
                await session.commit()
    return results
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest packages/leader/tests/test_scanner.py packages/leader/tests/test_job_store.py -v`
Expected: PASS (including `test_restoring_consent_requeues_only_when_no_live_job_exists`, which relies on system cancellations being requeued).

- [ ] **Step 6: Commit**

```bash
git add packages/leader/src/swarmscribe_leader/jobs/store.py packages/leader/src/swarmscribe_leader/ingest/scanner.py packages/leader/tests/test_scanner.py
git commit -m "Leader: scanning keeps administrators' cancellations and serves scan requests

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Settings for sign-in and roles

**Files:**
- Modify: `packages/leader/src/swarmscribe_leader/config.py`
- Test: `packages/leader/tests/test_config.py`

**Interfaces:**
- Produces on `Settings`: `entra_tenant_id: str | None` (normalised lowercase GUID), `entra_client_id: str | None`, `entra_client_secret: SecretStr | None`, `google_client_id: str | None`, `google_client_secret: SecretStr | None`, `google_hosted_domain: str | None` (lowercase), `google_service_account: SecretStr | None`, `role_cache_seconds: int = 300`, and twelve role lists `role_<viewer|operator|admin>_<entra_groups|google_groups|emails|domains>: tuple[str, ...]` (comma-separated in the environment, stripped, lowercased, a leading `@` dropped).
- Produces: `Settings.google_service_account_key() -> dict[str, str] | None` — the parsed key (with `token_uri` defaulted to `https://oauth2.googleapis.com/token`), from JSON text or from a file path; `ValueError` if unusable.
- Produces: `config.ROLES = ("viewer", "operator", "admin")`.

- [ ] **Step 1: Write the failing tests**

Append to `packages/leader/tests/test_config.py` (add `import json` to its imports):

```python
TENANT = "0F0E0D0C-0B0A-4908-8706-050403020100"
SERVICE_ACCOUNT = json.dumps(
    {
        "type": "service_account",
        "client_email": "group-reader@project-1.iam.gserviceaccount.com",
        "private_key": "-----BEGIN PRIVATE KEY-----\nnot-a-real-key\n-----END PRIVATE KEY-----\n",
    }
)


def test_no_sign_in_provider_is_configured_by_default():
    settings = Settings(**BASE)
    assert (settings.entra_client_id, settings.google_client_id) == (None, None)
    assert settings.role_admin_emails == ()
    assert settings.role_cache_seconds == 300


def test_both_providers_and_role_lists_from_the_environment(monkeypatch):
    for name, value in {
        "SWARMSCRIBE_DATABASE_URL": "postgresql://x/y",
        "SWARMSCRIBE_PUBLIC_URL": "https://l",
        "SWARMSCRIBE_LINK_KEY": "z" * 40,
        "SWARMSCRIBE_ENTRA_TENANT_ID": TENANT,
        "SWARMSCRIBE_ENTRA_CLIENT_ID": "entra-client",
        "SWARMSCRIBE_ENTRA_CLIENT_SECRET": "entra-secret",
        "SWARMSCRIBE_GOOGLE_CLIENT_ID": "google-client",
        "SWARMSCRIBE_GOOGLE_CLIENT_SECRET": "google-secret",
        "SWARMSCRIBE_GOOGLE_HOSTED_DOMAIN": "Example.ORG",
        "SWARMSCRIBE_ROLE_ADMIN_ENTRA_GROUPS": "A1A1A1A1-0000-4000-8000-000000000003, b2",
        "SWARMSCRIBE_ROLE_VIEWER_EMAILS": "One@Example.org,two@example.org ,",
        "SWARMSCRIBE_ROLE_OPERATOR_DOMAINS": "@Example.org",
    }.items():
        monkeypatch.setenv(name, value)
    settings = Settings()
    assert settings.entra_tenant_id == TENANT.lower()
    assert settings.google_hosted_domain == "example.org"
    assert settings.role_admin_entra_groups == ("a1a1a1a1-0000-4000-8000-000000000003", "b2")
    assert settings.role_viewer_emails == ("one@example.org", "two@example.org")
    assert settings.role_operator_domains == ("example.org",)
    assert settings.entra_client_secret.get_secret_value() == "entra-secret"


@pytest.mark.parametrize(
    "values, problem",
    [
        ({"entra_client_id": "c"}, "entra_client_id and entra_tenant_id must be set together"),
        ({"entra_tenant_id": TENANT}, "entra_client_id and entra_tenant_id must be set together"),
        (
            {"entra_client_id": "c", "entra_tenant_id": "contoso.example"},
            "tenant's ID (a GUID)",
        ),
        ({"entra_client_secret": "s"}, "entra_client_secret needs entra_client_id"),
        ({"google_client_id": "g"}, "google_client_secret is required"),
        ({"google_hosted_domain": "example.org"}, "need google_client_id"),
        ({"role_admin_entra_groups": "g1"}, "role_admin_entra_groups needs Entra ID sign-in"),
        (
            {
                "google_client_id": "g",
                "google_client_secret": "s",
                "role_viewer_google_groups": "x",
            },
            "role_viewer_google_groups needs google_service_account",
        ),
        ({"role_operator_emails": "a@example.org"}, "role_operator_emails applies to Google"),
    ],
)
def test_half_configured_sign_in_is_refused(values, problem):
    with pytest.raises(ValidationError, match=re.escape(problem)):
        Settings(**BASE, **values)


def test_the_service_account_key_can_be_json_or_a_file(tmp_path):
    google = {"google_client_id": "g", "google_client_secret": "s"}
    inline = Settings(**BASE, **google, google_service_account=SERVICE_ACCOUNT)
    key = inline.google_service_account_key()
    assert key["client_email"] == "group-reader@project-1.iam.gserviceaccount.com"
    assert key["token_uri"] == "https://oauth2.googleapis.com/token"
    path = tmp_path / "service-account.json"
    path.write_text(SERVICE_ACCOUNT, encoding="utf-8")
    from_file = Settings(**BASE, **google, google_service_account=str(path))
    assert from_file.google_service_account_key() == key


def test_an_unusable_service_account_is_refused_without_echoing_it():
    secret_text = '{"client_email": "x", "private_key": ""} plus-secret-material'
    with pytest.raises(ValidationError) as excinfo:
        Settings(
            **BASE,
            google_client_id="g",
            google_client_secret="s",
            google_service_account=secret_text,
        )
    rendered = str(excinfo.value.errors(include_input=False, include_url=False))
    assert "google_service_account must be a service-account JSON key" in rendered
    assert "plus-secret-material" not in rendered


def test_sign_in_secrets_are_not_shown_in_the_settings_repr():
    settings = Settings(
        **BASE,
        entra_tenant_id=TENANT,
        entra_client_id="c",
        entra_client_secret="entra-hidden",
        google_client_id="g",
        google_client_secret="google-hidden",
        google_service_account=SERVICE_ACCOUNT,
    )
    shown = repr(settings)
    assert "entra-hidden" not in shown
    assert "google-hidden" not in shown
    assert "not-a-real-key" not in shown
```

Add `import re` to the imports of `test_config.py` as well.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest packages/leader/tests/test_config.py -v`
Expected: FAIL — the settings have no sign-in fields.

- [ ] **Step 3: Add the settings**

Replace `packages/leader/src/swarmscribe_leader/config.py` with:

```python
import json
import uuid
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

ROLES = ("viewer", "operator", "admin")
_ROLE_KINDS = ("entra_groups", "google_groups", "emails", "domains")
_ROLE_LISTS = tuple(f"role_{role}_{kind}" for role in ROLES for kind in _ROLE_KINDS)
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
_BAD_SERVICE_ACCOUNT = (
    "google_service_account must be a service-account JSON key or the path of a file holding one"
)

NameList = Annotated[tuple[str, ...], NoDecode]
"""Comma-separated in the environment, e.g. `a@example.org, b@example.org`."""


class Settings(BaseSettings):
    """Leader configuration, read from SWARMSCRIBE_* environment variables."""

    model_config = SettingsConfigDict(env_prefix="SWARMSCRIBE_", extra="ignore")

    # Secrets: never shown in repr, logs or validation errors; use .get_secret_value().
    database_url: SecretStr
    public_url: str
    link_key: SecretStr

    lease_seconds: int = Field(default=120, gt=0)
    heartbeat_seconds: int = Field(default=30, gt=0)
    max_attempts: int = Field(default=3, gt=0)
    claim_retry_after: int = Field(default=10, gt=0)
    reaper_interval_seconds: float = Field(default=15.0, gt=0)
    scanner_interval_seconds: float = Field(default=30.0, gt=0)
    follower_gone_after_seconds: int = Field(default=600, gt=0)
    download_link_ttl_seconds: int = Field(default=1800, gt=0)
    upload_link_ttl_seconds: int = Field(default=7200, gt=0)

    # Administrators' sign-in. Either provider, or both, may be configured.
    entra_tenant_id: str | None = None
    entra_client_id: str | None = None
    entra_client_secret: SecretStr | None = None  # only for Microsoft Graph on group overage
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    google_hosted_domain: str | None = None
    google_service_account: SecretStr | None = None  # JSON key, or the path of a file with it
    role_cache_seconds: int = Field(default=300, gt=0)

    role_viewer_entra_groups: NameList = ()
    role_viewer_google_groups: NameList = ()
    role_viewer_emails: NameList = ()
    role_viewer_domains: NameList = ()
    role_operator_entra_groups: NameList = ()
    role_operator_google_groups: NameList = ()
    role_operator_emails: NameList = ()
    role_operator_domains: NameList = ()
    role_admin_entra_groups: NameList = ()
    role_admin_google_groups: NameList = ()
    role_admin_emails: NameList = ()
    role_admin_domains: NameList = ()

    @field_validator("public_url")
    @classmethod
    def _absolute_http_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError("public_url must be an absolute http(s) URL, e.g. https://leader")
        return value.rstrip("/")

    @field_validator("link_key")
    @classmethod
    def _long_enough(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("link_key must be at least 32 characters")
        return value

    @field_validator(*_ROLE_LISTS, mode="before")
    @classmethod
    def _name_list(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.split(",")
        if isinstance(value, list | tuple):
            names = (str(item).strip().lower().removeprefix("@") for item in value)
            return tuple(name for name in names if name)
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
        return value.lower() if value else value

    @model_validator(mode="after")
    def _heartbeat_inside_lease(self) -> "Settings":
        if self.heartbeat_seconds >= self.lease_seconds:
            raise ValueError("heartbeat_seconds must be shorter than lease_seconds")
        return self

    @model_validator(mode="after")
    def _gone_only_after_a_lease(self) -> "Settings":
        if self.follower_gone_after_seconds <= self.lease_seconds:
            raise ValueError("follower_gone_after_seconds must be longer than lease_seconds")
        return self

    @model_validator(mode="after")
    def _sign_in_is_complete(self) -> "Settings":
        if bool(self.entra_client_id) != bool(self.entra_tenant_id):
            raise ValueError("entra_client_id and entra_tenant_id must be set together")
        if self.entra_client_secret is not None and not self.entra_client_id:
            raise ValueError("entra_client_secret needs entra_client_id")
        if self.google_client_id and self.google_client_secret is None:
            raise ValueError(
                "google_client_secret is required with google_client_id "
                "(the admin CLI's device sign-in sends it)"
            )
        extras = (self.google_client_secret, self.google_hosted_domain, self.google_service_account)
        if not self.google_client_id and any(value is not None for value in extras):
            raise ValueError("the google_* settings need google_client_id")
        for role in ROLES:
            if getattr(self, f"role_{role}_entra_groups") and not self.entra_client_id:
                raise ValueError(f"role_{role}_entra_groups needs Entra ID sign-in")
            if getattr(self, f"role_{role}_google_groups") and self.google_service_account is None:
                raise ValueError(
                    f"role_{role}_google_groups needs google_service_account "
                    "to read Google Groups"
                )
            for kind in ("emails", "domains"):
                if getattr(self, f"role_{role}_{kind}") and not self.google_client_id:
                    raise ValueError(
                        f"role_{role}_{kind} applies to Google sign-in; set google_client_id"
                    )
        if self.google_service_account is not None:
            self.google_service_account_key()
        return self

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

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest packages/leader/tests/test_config.py packages/leader/tests/test_main.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/leader/src/swarmscribe_leader/config.py packages/leader/tests/test_config.py
git commit -m "Leader: settings for Entra ID and Google sign-in and role mapping

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: ID token validation for Entra ID and Google

**Files:**
- Modify: `packages/leader/pyproject.toml` (dependencies)
- Create: `packages/leader/src/swarmscribe_leader/auth/oidc.py`
- Modify: `packages/leader/tests/conftest.py` (fake identity providers, sign-in settings)
- Test: `packages/leader/tests/test_oidc.py`

**Interfaces:**
- Consumes: `Settings` sign-in fields (Task 5), `errors.Unauthorized`.
- Produces (in `auth/oidc.py`):
  - `Fetch = Callable[[str], Awaitable[dict[str, Any]]]`; `async def http_fetch(url: str) -> dict[str, Any]` (httpx, 10 s timeout).
  - `@dataclass(frozen=True) class Provider: name: Literal["entra", "google"]; issuers: tuple[str, ...]; client_id: str; discovery_url: str; tenant_id: str | None = None; hosted_domain: str | None = None`.
  - `@dataclass(frozen=True) class Identity: provider: str; issuer: str; subject: str; email: str | None; claims: dict[str, Any]` with property `actor -> str` = `"<email or 'unknown'> (<issuer> <subject>)"`.
  - `class MetadataUnavailable(Exception)`.
  - `def providers_from(settings: Settings) -> tuple[Provider, ...]`.
  - `def login_providers(settings: Settings) -> list[dict[str, str | None]]` — per provider `name`, `client_id`, `device_authorization_endpoint`, `token_endpoint`, `scope`, `client_secret` (Google only; `None` for Entra).
  - `class TokenVerifier(providers, *, fetch: Fetch = http_fetch, clock: Callable[[], float] = time.time)` with `async verify(token: str) -> Identity` (raises `Unauthorized`, code `token_expired` for expiry, or `MetadataUnavailable`) and `async ready() -> bool`.
  - Constants: `CLOCK_SKEW_SECONDS = 60`, `MAX_TOKEN_CHARS = 16384`, `JWKS_MIN_REFRESH_SECONDS = 60`, `JWKS_MAX_AGE_SECONDS = 86400`, `READY_RETRY_SECONDS = 5`, `ENTRA_AUTHORITY = "https://login.microsoftonline.com"`, `GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")`.
- Produces (fixtures in `conftest.py`): `signing_keys` (session; RSA keys `entra`, `google`, `rogue`, `service`), `idp` (a `FakeIdentityProviders`), `sign_in_settings` (a function `make(**overrides) -> Settings` with both providers, role groups and role emails configured).
  - `FakeIdentityProviders` attributes: `ENTRA_TENANT`, `ENTRA_CLIENT`, `GOOGLE_CLIENT`, `ENTRA_ISSUER`, `GOOGLE_ISSUER`, `ENTRA_GROUPS` and `GOOGLE_GROUPS` (`{"viewer"|"operator"|"admin": id}`), `down: bool`, `fetched: list[str]`; methods `fetch(url)`, `entra(**claims) -> str`, `google(**claims) -> str`, `token(provider, *, signed_with=None, kid=None, lifetime=3600, **claims)`, `rotate(provider, key_name, kid)`, `bearer(role, *, provider="entra") -> dict[str, str]`. A claim passed as `None` is removed from the token.

- [ ] **Step 1: Add the dependencies**

In `packages/leader/pyproject.toml`, add to `dependencies`:

```toml
    "pyjwt[crypto]>=2.10,<3",
    "httpx>=0.27,<1",
```

Run: `uv lock` then `uv sync`
Expected: `pyjwt` and `cryptography` are installed; `uv.lock` changes.

- [ ] **Step 2: Add the fake identity providers to the test fixtures**

In `packages/leader/tests/conftest.py`, add these imports:

```python
import json
import time

import httpx
import jwt as pyjwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
from swarmscribe_leader.config import ROLES, Settings
```

and append:

```python
@pytest.fixture(scope="session")
def signing_keys():
    return {
        name: rsa.generate_private_key(public_exponent=65537, key_size=2048)
        for name in ("entra", "google", "rogue", "service")
    }


class FakeIdentityProviders:
    """Entra ID and Google as the leader sees them: discovery documents and JWKS served
    through an injected fetcher, and ID tokens signed with locally generated keys."""

    ENTRA_TENANT = "0f0e0d0c-0b0a-4908-8706-050403020100"
    ENTRA_CLIENT = "6d3a6b52-1c2e-4a8f-9d61-2f6a4c0b7e11"
    GOOGLE_CLIENT = "google-client-1.apps.googleusercontent.com"
    ENTRA_ISSUER = f"https://login.microsoftonline.com/{ENTRA_TENANT}/v2.0"
    GOOGLE_ISSUER = "https://accounts.google.com"
    ENTRA_GROUPS = {
        "viewer": "a1a1a1a1-0000-4000-8000-000000000001",
        "operator": "a1a1a1a1-0000-4000-8000-000000000002",
        "admin": "a1a1a1a1-0000-4000-8000-000000000003",
    }
    GOOGLE_GROUPS = {
        "viewer": "viewers@example.org",
        "operator": "operators@example.org",
        "admin": "admins@example.org",
    }

    def __init__(self, keys):
        self.keys = keys
        self.signing = {"entra": "entra", "google": "google"}
        self.kids = {"entra": "entra-key-1", "google": "google-key-1"}
        self.published = {
            "entra": [("entra", "entra-key-1")],
            "google": [("google", "google-key-1")],
        }
        self.down = False
        self.fetched: list[str] = []

    def _jwks(self, provider: str) -> dict:
        keys = []
        for key_name, kid in self.published[provider]:
            jwk = json.loads(RSAAlgorithm.to_jwk(self.keys[key_name].public_key()))
            jwk.update(kid=kid, use="sig", alg="RS256")
            keys.append(jwk)
        return {"keys": keys}

    def _documents(self) -> dict[str, dict]:
        entra_jwks = f"https://login.microsoftonline.com/{self.ENTRA_TENANT}/discovery/v2.0/keys"
        google_jwks = "https://www.googleapis.com/oauth2/v3/certs"
        return {
            f"{self.ENTRA_ISSUER}/.well-known/openid-configuration": {
                "issuer": self.ENTRA_ISSUER,
                "jwks_uri": entra_jwks,
            },
            entra_jwks: self._jwks("entra"),
            "https://accounts.google.com/.well-known/openid-configuration": {
                "issuer": self.GOOGLE_ISSUER,
                "jwks_uri": google_jwks,
            },
            google_jwks: self._jwks("google"),
        }

    async def fetch(self, url: str) -> dict:
        self.fetched.append(url)
        if self.down:
            raise httpx.ConnectError("identity provider unreachable")
        return self._documents()[url]

    def rotate(self, provider: str, key_name: str, kid: str) -> None:
        """Publish another signing key and sign new tokens with it."""
        self.published[provider].append((key_name, kid))
        self.signing[provider] = key_name
        self.kids[provider] = kid

    def _defaults(self, provider: str) -> dict:
        if provider == "entra":
            return {
                "iss": self.ENTRA_ISSUER,
                "aud": self.ENTRA_CLIENT,
                "tid": self.ENTRA_TENANT,
                "sub": "entra-person-1",
                "oid": "00000000-0000-4000-8000-0000000000a1",
                "email": "person@example.org",
                "preferred_username": "person@example.org",
                "groups": [],
            }
        return {
            "iss": self.GOOGLE_ISSUER,
            "aud": self.GOOGLE_CLIENT,
            "sub": "google-person-1",
            "email": "person@example.org",
            "email_verified": True,
        }

    def token(self, provider, *, signed_with=None, kid=None, lifetime=3600, **claims) -> str:
        now = int(time.time())
        payload = {"iat": now, "nbf": now, "exp": now + lifetime}
        payload.update(self._defaults(provider))
        payload.update(claims)
        payload = {name: value for name, value in payload.items() if value is not None}
        key = self.keys[signed_with or self.signing[provider]]
        return pyjwt.encode(
            payload, key, algorithm="RS256", headers={"kid": kid or self.kids[provider]}
        )

    def entra(self, **claims) -> str:
        return self.token("entra", **claims)

    def google(self, **claims) -> str:
        return self.token("google", **claims)

    def bearer(self, role: str | None, *, provider: str = "entra") -> dict[str, str]:
        """Headers for a person holding `role` (None: no role at all)."""
        name = role or "nobody"
        email = f"{name}@example.org"
        if provider == "entra":
            groups = [self.ENTRA_GROUPS[role]] if role else []
            token = self.entra(groups=groups, sub=f"entra-{name}", email=email)
        else:
            token = self.google(email=email, sub=f"google-{name}")
        return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def idp(signing_keys):
    return FakeIdentityProviders(signing_keys)


@pytest.fixture
def sign_in_settings(signing_keys):
    """Settings with both providers and every role mapping configured. Database-free by
    default; pass database_url=… for tests that need one."""
    private_key = (
        signing_keys["service"]
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode()
    )
    service_account = json.dumps(
        {
            "type": "service_account",
            "client_email": "group-reader@project-1.iam.gserviceaccount.com",
            "private_key": private_key,
            "private_key_id": "service-key-1",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    )
    fake = FakeIdentityProviders

    def make(**overrides) -> Settings:
        values = {
            "database_url": "postgresql://u:p@127.0.0.1:1/none",
            "public_url": "http://leader",
            "link_key": "k" * 32,
            "entra_tenant_id": fake.ENTRA_TENANT,
            "entra_client_id": fake.ENTRA_CLIENT,
            "entra_client_secret": "entra-app-secret-value",
            "google_client_id": fake.GOOGLE_CLIENT,
            "google_client_secret": "google-device-secret-value",
            "google_service_account": service_account,
        }
        for role in ROLES:
            values[f"role_{role}_entra_groups"] = (fake.ENTRA_GROUPS[role],)
            values[f"role_{role}_google_groups"] = (fake.GOOGLE_GROUPS[role],)
            values[f"role_{role}_emails"] = (f"{role}@example.org",)
        values.update(overrides)
        return Settings(**values)

    return make
```

- [ ] **Step 3: Write the failing tests**

`packages/leader/tests/test_oidc.py`:

```python
import time

import jwt as pyjwt
import pytest
from swarmscribe_leader.auth.oidc import (
    MetadataUnavailable,
    TokenVerifier,
    login_providers,
    providers_from,
)
from swarmscribe_leader.errors import Unauthorized


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def verifier(idp, sign_in_settings, clock):
    return TokenVerifier(providers_from(sign_in_settings()), fetch=idp.fetch, clock=clock)


async def test_an_entra_token_gives_its_identity(verifier, idp):
    identity = await verifier.verify(idp.entra(email="Person@Example.org"))
    assert (identity.provider, identity.issuer, identity.subject, identity.email) == (
        "entra",
        idp.ENTRA_ISSUER,
        "entra-person-1",
        "person@example.org",
    )
    assert identity.actor == f"person@example.org ({idp.ENTRA_ISSUER} entra-person-1)"


@pytest.mark.parametrize("issuer", ["https://accounts.google.com", "accounts.google.com"])
async def test_a_google_token_gives_its_identity_under_either_issuer_spelling(
    verifier, idp, issuer
):
    identity = await verifier.verify(idp.google(iss=issuer))
    assert (identity.provider, identity.issuer, identity.subject) == (
        "google",
        "https://accounts.google.com",
        "google-person-1",
    )


async def test_an_expired_token_is_refused_as_expired(verifier, idp):
    with pytest.raises(Unauthorized) as excinfo:
        await verifier.verify(idp.entra(lifetime=-120))
    assert excinfo.value.code == "token_expired"


async def test_sixty_seconds_of_clock_skew_are_allowed(verifier, idp):
    assert await verifier.verify(idp.entra(lifetime=-30))
    now = int(time.time())
    assert await verifier.verify(idp.google(nbf=now + 30))
    with pytest.raises(Unauthorized):
        await verifier.verify(idp.google(nbf=now + 120))


@pytest.mark.parametrize(
    "make_token",
    [
        lambda idp: idp.entra(aud="another-client"),
        lambda idp: idp.google(aud=idp.ENTRA_CLIENT),
        lambda idp: idp.entra(iss="https://login.microsoftonline.com/another/v2.0"),
        lambda idp: idp.entra(tid="ffffffff-0000-4000-8000-000000000000"),
        lambda idp: idp.entra(signed_with="rogue"),
        lambda idp: idp.google(email_verified=False),
        lambda idp: idp.google(email_verified=None),
        lambda idp: idp.entra(exp=None),
        lambda idp: idp.entra(sub=None),
    ],
    ids=[
        "wrong-audience",
        "other-providers-audience",
        "unknown-issuer",
        "other-tenant",
        "forged-signature",
        "unverified-email",
        "no-email-verified-claim",
        "no-expiry",
        "no-subject",
    ],
)
async def test_tokens_that_fail_a_check_are_refused(verifier, idp, make_token):
    with pytest.raises(Unauthorized):
        await verifier.verify(make_token(idp))


async def test_only_rs256_is_accepted(verifier, idp):
    forged = pyjwt.encode(
        {"iss": idp.ENTRA_ISSUER, "aud": idp.ENTRA_CLIENT, "sub": "x", "exp": 9999999999},
        "a-shared-secret-of-thirty-two-bytes!!",
        algorithm="HS256",
        headers={"kid": "entra-key-1"},
    )
    with pytest.raises(Unauthorized, match="RS256"):
        await verifier.verify(forged)


@pytest.mark.parametrize("token", ["", "not-a-token", "a.b.c", "x" * 20000])
async def test_malformed_or_oversized_tokens_are_refused(verifier, token):
    with pytest.raises(Unauthorized):
        await verifier.verify(token)


async def test_a_configured_hosted_domain_is_required(idp, sign_in_settings, clock):
    settings = sign_in_settings(google_hosted_domain="example.org")
    verifier = TokenVerifier(providers_from(settings), fetch=idp.fetch, clock=clock)
    assert await verifier.verify(idp.google(hd="example.org"))
    with pytest.raises(Unauthorized, match="hosted domain"):
        await verifier.verify(idp.google(hd="elsewhere.example"))
    with pytest.raises(Unauthorized, match="hosted domain"):
        await verifier.verify(idp.google())


async def test_keys_are_cached_and_refreshed_for_a_new_key_id(verifier, idp, clock):
    await verifier.verify(idp.entra())
    fetches = len(idp.fetched)
    await verifier.verify(idp.entra())
    assert len(idp.fetched) == fetches  # cached
    idp.rotate("entra", "rogue", "entra-key-2")
    clock.now += 61
    assert await verifier.verify(idp.entra())
    assert len(idp.fetched) == fetches + 1  # the keys again; discovery stays cached


async def test_unknown_key_ids_refresh_the_keys_at_most_once_a_minute(verifier, idp, clock):
    await verifier.verify(idp.entra())
    fetches = len(idp.fetched)
    for _ in range(5):
        with pytest.raises(Unauthorized, match="unknown key"):
            await verifier.verify(idp.entra(kid="made-up"))
    assert len(idp.fetched) == fetches


async def test_a_provider_that_cannot_be_reached_is_unavailable_not_a_refusal(verifier, idp):
    idp.down = True
    with pytest.raises(MetadataUnavailable):
        await verifier.verify(idp.google())


async def test_cached_keys_keep_working_while_the_provider_is_down(verifier, idp, clock):
    await verifier.verify(idp.entra())
    idp.down = True
    clock.now += 2 * 86400  # the keys are stale, the refresh fails
    assert await verifier.verify(idp.entra())


async def test_ready_after_the_metadata_was_fetched_once(verifier, idp, clock):
    idp.down = True
    assert await verifier.ready() is False
    idp.down = False
    assert await verifier.ready() is False  # retried no more than every 5 seconds
    clock.now += 6
    assert await verifier.ready() is True


async def test_a_leader_without_providers_refuses_every_token_and_is_ready(idp, clock):
    verifier = TokenVerifier((), fetch=idp.fetch, clock=clock)
    with pytest.raises(Unauthorized, match="not configured"):
        await verifier.verify(idp.entra())
    assert await verifier.ready() is True


def test_login_providers_give_the_cli_what_it_needs_and_no_entra_secret(sign_in_settings, idp):
    providers = {p["name"]: p for p in login_providers(sign_in_settings())}
    entra, google = providers["entra"], providers["google"]
    base = f"https://login.microsoftonline.com/{idp.ENTRA_TENANT}/oauth2/v2.0"
    assert entra == {
        "name": "entra",
        "client_id": idp.ENTRA_CLIENT,
        "device_authorization_endpoint": f"{base}/devicecode",
        "token_endpoint": f"{base}/token",
        "scope": "openid profile email offline_access",
        "client_secret": None,
    }
    assert google["client_secret"] == "google-device-secret-value"
    assert google["device_authorization_endpoint"] == "https://oauth2.googleapis.com/device/code"
    assert "entra-app-secret-value" not in str(providers)
```

- [ ] **Step 4: Run them to verify they fail**

Run: `uv run pytest packages/leader/tests/test_oidc.py -v`
Expected: FAIL — `swarmscribe_leader.auth.oidc` does not exist.

- [ ] **Step 5: Implement the verifier**

`packages/leader/src/swarmscribe_leader/auth/oidc.py`:

```python
"""Validate administrators' ID tokens from Microsoft Entra ID and Google.

Tokens are never logged: messages name the failed check only."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm

from ..config import Settings
from ..errors import Unauthorized

logger = logging.getLogger(__name__)

Fetch = Callable[[str], Awaitable[dict[str, Any]]]
ProviderName = Literal["entra", "google"]

CLOCK_SKEW_SECONDS = 60
MAX_TOKEN_CHARS = 16 * 1024
JWKS_MIN_REFRESH_SECONDS = 60
JWKS_MAX_AGE_SECONDS = 24 * 3600
READY_RETRY_SECONDS = 5
ENTRA_AUTHORITY = "https://login.microsoftonline.com"
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
GOOGLE_DISCOVERY = "https://accounts.google.com/.well-known/openid-configuration"


class MetadataUnavailable(Exception):
    """A provider's discovery document or signing keys cannot be fetched (transient)."""


@dataclass(frozen=True)
class Provider:
    name: ProviderName
    issuers: tuple[str, ...]
    client_id: str
    discovery_url: str
    tenant_id: str | None = None
    hosted_domain: str | None = None


@dataclass(frozen=True)
class Identity:
    provider: ProviderName
    issuer: str
    subject: str
    email: str | None
    claims: dict[str, Any]

    @property
    def actor(self) -> str:
        """How the audit log names this person: email, then the (issuer, sub) identity."""
        return f"{self.email or 'unknown'} ({self.issuer} {self.subject})"


def providers_from(settings: Settings) -> tuple[Provider, ...]:
    found: list[Provider] = []
    if settings.entra_client_id and settings.entra_tenant_id:
        issuer = f"{ENTRA_AUTHORITY}/{settings.entra_tenant_id}/v2.0"
        found.append(
            Provider(
                name="entra",
                issuers=(issuer,),
                client_id=settings.entra_client_id,
                discovery_url=f"{issuer}/.well-known/openid-configuration",
                tenant_id=settings.entra_tenant_id,
            )
        )
    if settings.google_client_id:
        found.append(
            Provider(
                name="google",
                issuers=GOOGLE_ISSUERS,
                client_id=settings.google_client_id,
                discovery_url=GOOGLE_DISCOVERY,
                hosted_domain=settings.google_hosted_domain,
            )
        )
    return tuple(found)


def login_providers(settings: Settings) -> list[dict[str, str | None]]:
    """What the admin CLI needs for each provider's device-code sign-in."""
    found: list[dict[str, str | None]] = []
    if settings.entra_client_id:
        base = f"{ENTRA_AUTHORITY}/{settings.entra_tenant_id}/oauth2/v2.0"
        found.append(
            {
                "name": "entra",
                "client_id": settings.entra_client_id,
                "device_authorization_endpoint": f"{base}/devicecode",
                "token_endpoint": f"{base}/token",
                "scope": "openid profile email offline_access",
                "client_secret": None,
            }
        )
    if settings.google_client_id:
        secret = settings.google_client_secret
        found.append(
            {
                "name": "google",
                "client_id": settings.google_client_id,
                "device_authorization_endpoint": "https://oauth2.googleapis.com/device/code",
                "token_endpoint": "https://oauth2.googleapis.com/token",
                "scope": "openid email profile",
                # Google's limited-input-device clients need their secret for device sign-in;
                # Google documents it as not confidential.
                "client_secret": secret.get_secret_value() if secret else None,
            }
        )
    return found


async def http_fetch(url: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()


class _ProviderKeys:
    """One provider's discovery metadata and signing keys, fetched lazily and cached.

    Keys are fetched again when they are a day old, or when a token names a key id we do not
    have (a rotation) — but never more than once a minute, so made-up key ids cannot make
    the leader hammer the provider. While a refresh fails, the keys we have keep working.
    """

    def __init__(self, provider: Provider, fetch: Fetch, clock: Callable[[], float]):
        self.provider = provider
        self.fetch = fetch
        self.clock = clock
        self.jwks_uri: str | None = None
        self.keys: dict[str, Any] = {}
        self.fetched_at: float | None = None
        self.attempted_at: float | None = None
        self.lock = asyncio.Lock()

    async def _refresh(self, now: float) -> None:
        self.attempted_at = now
        try:
            if self.jwks_uri is None:
                metadata = await self.fetch(self.provider.discovery_url)
                if metadata.get("issuer") not in self.provider.issuers:
                    raise ValueError("the discovery document names another issuer")
                self.jwks_uri = str(metadata["jwks_uri"])
            document = await self.fetch(self.jwks_uri)
            keys = {}
            for jwk in document.get("keys", []):
                usable = jwk.get("kty") == "RSA" and jwk.get("use", "sig") == "sig"
                if not usable or not jwk.get("kid"):
                    continue
                keys[str(jwk["kid"])] = RSAAlgorithm.from_jwk(jwk)
        except Exception as exc:  # network, HTTP status, malformed documents
            logger.warning(
                "%s sign-in keys could not be fetched: %s", self.provider.name, type(exc).__name__
            )
            return
        self.keys = keys
        self.fetched_at = now

    async def key(self, kid: str) -> Any | None:
        async with self.lock:
            now = self.clock()
            due = (
                self.fetched_at is None
                or now - self.fetched_at > JWKS_MAX_AGE_SECONDS
                or kid not in self.keys
            )
            allowed = (
                self.attempted_at is None or now - self.attempted_at >= JWKS_MIN_REFRESH_SECONDS
            )
            if due and allowed:
                await self._refresh(now)
            if self.fetched_at is None:
                raise MetadataUnavailable(f"{self.provider.name} sign-in keys are not available")
            return self.keys.get(kid)

    async def ready(self) -> bool:
        async with self.lock:
            if self.fetched_at is None:
                now = self.clock()
                if self.attempted_at is None or now - self.attempted_at >= READY_RETRY_SECONDS:
                    await self._refresh(now)
            return self.fetched_at is not None


class TokenVerifier:
    def __init__(
        self,
        providers: Sequence[Provider],
        *,
        fetch: Fetch = http_fetch,
        clock: Callable[[], float] = time.time,
    ):
        self.providers = tuple(providers)
        self._keys = {p.name: _ProviderKeys(p, fetch, clock) for p in self.providers}

    async def ready(self) -> bool:
        """Whether every configured provider's metadata has been fetched at least once."""
        results = [await keys.ready() for keys in self._keys.values()]
        return all(results)

    def _provider_for(self, issuer: object) -> Provider | None:
        for provider in self.providers:
            if issuer in provider.issuers:
                return provider
        return None

    async def verify(self, token: str) -> Identity:
        if not self.providers:
            raise Unauthorized("sign-in is not configured on this leader")
        if len(token) > MAX_TOKEN_CHARS:
            raise Unauthorized("the sign-in token is too large")
        try:
            header = jwt.get_unverified_header(token)
            unverified = jwt.decode(token, options={"verify_signature": False})
        except jwt.PyJWTError as exc:
            raise Unauthorized("the sign-in token is malformed") from exc
        if header.get("alg") != "RS256":
            raise Unauthorized("the sign-in token must be signed with RS256")
        provider = self._provider_for(unverified.get("iss"))
        if provider is None:
            raise Unauthorized("the sign-in token is from an issuer this leader does not accept")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise Unauthorized("the sign-in token names no signing key")
        key = await self._keys[provider.name].key(kid)
        if key is None:
            raise Unauthorized("the sign-in token is signed with an unknown key")
        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                audience=provider.client_id,
                leeway=CLOCK_SKEW_SECONDS,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise Unauthorized("the sign-in token has expired", code="token_expired") from exc
        except jwt.PyJWTError as exc:
            raise Unauthorized(f"the sign-in token is not valid: {exc}") from exc
        email = self._check_provider_claims(provider, claims)
        return Identity(
            provider=provider.name,
            issuer=provider.issuers[0],
            subject=str(claims["sub"]),
            email=email,
            claims=claims,
        )

    @staticmethod
    def _check_provider_claims(provider: Provider, claims: dict[str, Any]) -> str | None:
        """Provider-specific checks; returns the person's email (lowercase) if known."""
        if provider.name == "entra":
            if str(claims.get("tid", "")).lower() != provider.tenant_id:
                raise Unauthorized("the sign-in token is from another Entra ID tenant")
            email = claims.get("email") or claims.get("preferred_username")
        else:
            if claims.get("email_verified") not in (True, "true"):
                raise Unauthorized("the Google account's email address is not verified")
            if provider.hosted_domain and str(claims.get("hd", "")).lower() != (
                provider.hosted_domain
            ):
                raise Unauthorized("the Google account is not in the allowed hosted domain")
            email = claims.get("email")
        return str(email).lower() if email else None
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest packages/leader/tests/test_oidc.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add packages/leader/pyproject.toml uv.lock packages/leader/src/swarmscribe_leader/auth/oidc.py packages/leader/tests/conftest.py packages/leader/tests/test_oidc.py
git commit -m "Leader: validate Entra ID and Google ID tokens against cached provider keys

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Roles from Entra groups, Google Groups and email/domain lists

**Files:**
- Create: `packages/leader/src/swarmscribe_leader/auth/roles.py`
- Modify: `packages/leader/tests/conftest.py` (fake Graph and Google Groups directories)
- Test: `packages/leader/tests/test_roles.py`

**Interfaces:**
- Consumes: `Identity` (Task 6), `Settings` role lists and `ROLES` (Task 5).
- Produces (in `auth/roles.py`):
  - `Role = Literal["viewer", "operator", "admin"]`; `RANK = {"viewer": 1, "operator": 2, "admin": 3}`; `def at_least(role: str | None, required: str) -> bool`; `def highest(roles: Iterable[str]) -> Role | None`.
  - `class RoleLookupFailed(Exception)` — a directory could not be asked (transient).
  - Protocols `GraphClient.member_object_ids(user_object_id: str) -> set[str]` and `GoogleGroupsClient.group_emails(email: str) -> set[str]` (both async, both return lowercase values).
  - `@dataclass(frozen=True) class RoleMapping: entra_groups, google_groups, emails, domains: dict[str, frozenset[str]]` with `from_settings(settings)` and `wants_google_groups() -> bool`.
  - `class RoleResolver(mapping, *, graph=None, google_groups=None, cache_seconds=300, clock=time.monotonic, max_entries=4096)` with `async role_for(identity) -> Role | None` (cached per `(issuer, subject)` including "no role"; failures raise `RoleLookupFailed` and are not cached).
  - `class MicrosoftGraph(tenant_id, client_id, client_secret: SecretStr, *, transport=None, clock=time.time)` and `class GoogleCloudIdentity(service_account: dict[str, str], *, transport=None, clock=time.time)` implementing the protocols over httpx (`transport` lets tests use `httpx.MockTransport`).
- Produces (fixtures): `graph` (`FakeGraph`: `groups: dict[oid, set[str]]`, `calls`, `failing`) and `google_groups` (`FakeGoogleGroups`: `groups: dict[email, set[str]]`, `calls`, `failing`).

- [ ] **Step 1: Add the fake directories to the fixtures**

In `packages/leader/tests/conftest.py`, add `from swarmscribe_leader.auth.roles import RoleLookupFailed` to the imports and append:

```python
class FakeGraph:
    """Microsoft Graph's getMemberObjects, answered from a dict."""

    def __init__(self):
        self.groups: dict[str, set[str]] = {}
        self.calls: list[str] = []
        self.failing = False

    async def member_object_ids(self, user_object_id: str) -> set[str]:
        self.calls.append(user_object_id)
        if self.failing:
            raise RoleLookupFailed("Microsoft Graph could not be asked: ConnectError")
        return {group.lower() for group in self.groups.get(user_object_id, set())}


class FakeGoogleGroups:
    """Cloud Identity's transitive group search, answered from a dict."""

    def __init__(self):
        self.groups: dict[str, set[str]] = {}
        self.calls: list[str] = []
        self.failing = False

    async def group_emails(self, email: str) -> set[str]:
        self.calls.append(email)
        if self.failing:
            raise RoleLookupFailed("Google Cloud Identity could not be asked: ConnectError")
        return {group.lower() for group in self.groups.get(email, set())}


@pytest.fixture
def graph():
    return FakeGraph()


@pytest.fixture
def google_groups():
    return FakeGoogleGroups()
```

- [ ] **Step 2: Write the failing tests**

`packages/leader/tests/test_roles.py`:

```python
import json
from urllib.parse import parse_qsl

import httpx
import jwt as pyjwt
import pytest
from pydantic import SecretStr
from swarmscribe_leader.auth.oidc import Identity
from swarmscribe_leader.auth.roles import (
    GoogleCloudIdentity,
    MicrosoftGraph,
    RoleLookupFailed,
    RoleMapping,
    RoleResolver,
    at_least,
)

OID = "00000000-0000-4000-8000-0000000000a1"


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def make_resolver(sign_in_settings, graph, google_groups, clock):
    def make(*, settings=None, with_graph=True, with_groups=True) -> RoleResolver:
        return RoleResolver(
            RoleMapping.from_settings(settings or sign_in_settings()),
            graph=graph if with_graph else None,
            google_groups=google_groups if with_groups else None,
            clock=clock,
        )

    return make


def entra_identity(idp, *, groups=(), email="person@example.org", **claims) -> Identity:
    return Identity(
        provider="entra",
        issuer=idp.ENTRA_ISSUER,
        subject="entra-person-1",
        email=email,
        claims={"groups": list(groups), "oid": OID, **claims},
    )


def google_identity(idp, email="person@example.org") -> Identity:
    return Identity(
        provider="google",
        issuer=idp.GOOGLE_ISSUER,
        subject="google-person-1",
        email=email,
        claims={},
    )


def test_roles_are_cumulative():
    assert at_least("admin", "viewer") and at_least("operator", "operator")
    assert not at_least("viewer", "operator")
    assert not at_least(None, "viewer")


@pytest.mark.parametrize("role", ["viewer", "operator", "admin"])
async def test_an_entra_group_gives_its_role(make_resolver, idp, role):
    identity = entra_identity(idp, groups=[idp.ENTRA_GROUPS[role]])
    assert await make_resolver().role_for(identity) == role


async def test_the_highest_role_wins_and_group_ids_ignore_case(make_resolver, idp):
    groups = [idp.ENTRA_GROUPS["viewer"], idp.ENTRA_GROUPS["admin"].upper()]
    assert await make_resolver().role_for(entra_identity(idp, groups=groups)) == "admin"


async def test_no_matching_group_is_no_role(make_resolver, idp):
    identity = entra_identity(idp, groups=["99999999-0000-4000-8000-000000000000"])
    assert await make_resolver().role_for(identity) is None


async def test_email_lists_do_not_apply_to_entra_sign_ins(make_resolver, idp):
    # Entra's email claim is not verified by Entra: only group ids count.
    assert await make_resolver().role_for(entra_identity(idp, email="admin@example.org")) is None


async def test_group_overage_is_resolved_through_microsoft_graph(make_resolver, idp, graph):
    graph.groups[OID] = {idp.ENTRA_GROUPS["operator"]}
    identity = entra_identity(idp, _claim_names={"groups": "src1"})
    assert await make_resolver().role_for(identity) == "operator"
    assert graph.calls == [OID]


async def test_group_overage_without_graph_credentials_is_no_role(make_resolver, idp):
    identity = entra_identity(idp, hasgroups=True)
    assert await make_resolver(with_graph=False).role_for(identity) is None


async def test_google_groups_emails_and_domains_all_count(
    make_resolver, idp, google_groups, sign_in_settings
):
    settings = sign_in_settings(role_operator_domains=("example.org",))
    resolver = make_resolver(settings=settings)
    google_groups.groups["person@example.org"] = {"Viewers@Example.org"}
    assert await resolver.role_for(google_identity(idp)) == "operator"  # the domain wins
    assert await resolver.role_for(google_identity(idp, "admin@example.org")) == "admin"


async def test_google_without_a_group_reader_uses_the_lists_only(make_resolver, idp, google_groups):
    resolver = make_resolver(with_groups=False)
    google_groups.groups["viewer@example.org"] = {"admins@example.org"}
    assert await resolver.role_for(google_identity(idp, "viewer@example.org")) == "viewer"
    assert google_groups.calls == []


async def test_roles_are_cached_for_five_minutes_per_person(
    make_resolver, idp, google_groups, clock
):
    resolver = make_resolver()
    google_groups.groups["person@example.org"] = {"admins@example.org"}
    assert await resolver.role_for(google_identity(idp)) == "admin"
    google_groups.groups["person@example.org"] = set()
    clock.now += 299
    assert await resolver.role_for(google_identity(idp)) == "admin"
    clock.now += 2
    assert await resolver.role_for(google_identity(idp)) is None
    assert len(google_groups.calls) == 2


async def test_a_failed_lookup_raises_and_is_not_cached(make_resolver, idp, google_groups):
    resolver = make_resolver()
    google_groups.failing = True
    with pytest.raises(RoleLookupFailed):
        await resolver.role_for(google_identity(idp, "viewer@example.org"))
    google_groups.failing = False
    assert await resolver.role_for(google_identity(idp, "viewer@example.org")) == "viewer"


async def test_microsoft_graph_uses_the_apps_own_credentials():
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/oauth2/v2.0/token"):
            return httpx.Response(200, json={"access_token": "app-token", "expires_in": 3600})
        return httpx.Response(200, json={"value": ["G-ONE", "g-two"]})

    graph = MicrosoftGraph(
        "tenant-1", "client-1", SecretStr("app-secret"), transport=httpx.MockTransport(answer)
    )
    assert await graph.member_object_ids(OID) == {"g-one", "g-two"}
    assert await graph.member_object_ids(OID) == {"g-one", "g-two"}
    token_requests = [r for r in seen if r.url.path.endswith("/token")]
    assert len(token_requests) == 1  # the app token is reused
    assert token_requests[0].url.host == "login.microsoftonline.com"
    form = dict(parse_qsl(token_requests[0].content.decode()))
    assert (form["grant_type"], form["scope"], form["client_secret"]) == (
        "client_credentials",
        "https://graph.microsoft.com/.default",
        "app-secret",
    )
    lookup = [r for r in seen if r.url.path.endswith("/getMemberObjects")][0]
    assert (lookup.url.host, lookup.url.path) == (
        "graph.microsoft.com",
        f"/v1.0/users/{OID}/getMemberObjects",
    )
    assert lookup.headers["authorization"] == "Bearer app-token"
    assert json.loads(lookup.content) == {"securityEnabledOnly": False}


async def test_a_graph_failure_is_a_lookup_failure_that_names_no_secret():
    graph = MicrosoftGraph(
        "tenant-1",
        "client-1",
        SecretStr("app-secret"),
        transport=httpx.MockTransport(lambda request: httpx.Response(503)),
    )
    with pytest.raises(RoleLookupFailed) as excinfo:
        await graph.member_object_ids(OID)
    assert "app-secret" not in str(excinfo.value)


async def test_google_groups_are_read_with_the_service_account(signing_keys, sign_in_settings):
    account = sign_in_settings().google_service_account_key()
    pages = {
        None: {"memberships": [{"groupKey": {"id": "Viewers@Example.org"}}], "nextPageToken": "p2"},
        "p2": {"memberships": [{"groupKey": {"id": "admins@example.org"}}]},
    }
    searches: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            form = dict(parse_qsl(request.content.decode()))
            claims = pyjwt.decode(
                form["assertion"],
                signing_keys["service"].public_key(),
                algorithms=["RS256"],
                audience="https://oauth2.googleapis.com/token",
            )
            assert claims["iss"] == account["client_email"]
            assert claims["scope"] == GoogleCloudIdentity.SCOPE
            return httpx.Response(200, json={"access_token": "sa-token", "expires_in": 3600})
        searches.append(request)
        assert request.headers["authorization"] == "Bearer sa-token"
        return httpx.Response(200, json=pages[request.url.params.get("pageToken")])

    client = GoogleCloudIdentity(account, transport=httpx.MockTransport(answer))
    assert await client.group_emails("person@example.org") == {
        "viewers@example.org",
        "admins@example.org",
    }
    assert len(searches) == 2
    assert "member_key_id == 'person@example.org'" in searches[0].url.params["query"]


async def test_google_lookup_refuses_an_email_that_could_alter_the_query(sign_in_settings):
    calls = []
    client = GoogleCloudIdentity(
        sign_in_settings().google_service_account_key(),
        transport=httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(500)),
    )
    assert await client.group_emails("x' || true || '@example.org") == set()
    assert calls == []
```

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest packages/leader/tests/test_roles.py -v`
Expected: FAIL — `swarmscribe_leader.auth.roles` does not exist (and `conftest.py` cannot import it, so every leader test errors until Step 4).

- [ ] **Step 4: Implement role resolution**

`packages/leader/src/swarmscribe_leader/auth/roles.py`:

```python
"""Turn a signed-in person into a role. Roles are cumulative: admin > operator > viewer.

Entra ID: the token's group object ids, or Microsoft Graph when the person is in too many
groups for the token ("overage"). Google: the person's Google Groups through Cloud Identity
(when a service account is configured), plus email and domain lists. Lookups are cached
per person; a directory that cannot be asked is a transient failure, never "no role".
"""

import logging
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Literal, Protocol
from urllib.parse import quote

import httpx
import jwt
from pydantic import SecretStr

from ..config import ROLES, Settings
from .oidc import ENTRA_AUTHORITY, Identity

logger = logging.getLogger(__name__)

Role = Literal["viewer", "operator", "admin"]
RANK = {"viewer": 1, "operator": 2, "admin": 3}
DIRECTORY_TIMEOUT_SECONDS = 10.0


class RoleLookupFailed(Exception):
    """A group directory (Microsoft Graph, Cloud Identity) could not be asked."""


class GraphClient(Protocol):
    async def member_object_ids(self, user_object_id: str) -> set[str]: ...


class GoogleGroupsClient(Protocol):
    async def group_emails(self, email: str) -> set[str]: ...


def at_least(role: str | None, required: str) -> bool:
    return role is not None and RANK[role] >= RANK[required]


def highest(roles: Iterable[str]) -> Role | None:
    ranked = sorted(set(roles), key=RANK.__getitem__)
    return ranked[-1] if ranked else None


@dataclass(frozen=True)
class RoleMapping:
    entra_groups: dict[str, frozenset[str]]
    google_groups: dict[str, frozenset[str]]
    emails: dict[str, frozenset[str]]
    domains: dict[str, frozenset[str]]

    @classmethod
    def from_settings(cls, settings: Settings) -> "RoleMapping":
        def per_role(kind: str) -> dict[str, frozenset[str]]:
            return {role: frozenset(getattr(settings, f"role_{role}_{kind}")) for role in ROLES}

        return cls(
            entra_groups=per_role("entra_groups"),
            google_groups=per_role("google_groups"),
            emails=per_role("emails"),
            domains=per_role("domains"),
        )

    def wants_google_groups(self) -> bool:
        return any(self.google_groups.values())


def _has_group_overage(claims: dict[str, Any]) -> bool:
    names = claims.get("_claim_names")
    if isinstance(names, dict) and "groups" in names:
        return True
    return claims.get("hasgroups") in (True, "true")


class RoleResolver:
    def __init__(
        self,
        mapping: RoleMapping,
        *,
        graph: GraphClient | None = None,
        google_groups: GoogleGroupsClient | None = None,
        cache_seconds: float = 300,
        clock: Callable[[], float] = time.monotonic,
        max_entries: int = 4096,
    ):
        self.mapping = mapping
        self.graph = graph
        self.google_groups = google_groups
        self.cache_seconds = cache_seconds
        self.clock = clock
        self.max_entries = max_entries
        self._cache: dict[tuple[str, str], tuple[Role | None, float]] = {}

    async def role_for(self, identity: Identity) -> Role | None:
        key = (identity.issuer, identity.subject)
        now = self.clock()
        cached = self._cache.get(key)
        if cached is not None and now - cached[1] < self.cache_seconds:
            return cached[0]
        role = await self._resolve(identity)  # RoleLookupFailed propagates, uncached
        self._cache.pop(key, None)
        if len(self._cache) >= self.max_entries:
            self._cache.pop(next(iter(self._cache)))  # the oldest entry
        self._cache[key] = (role, now)
        return role

    async def _resolve(self, identity: Identity) -> Role | None:
        if identity.provider == "entra":
            groups = await self._entra_groups(identity)
            return highest(role for role, ids in self.mapping.entra_groups.items() if ids & groups)
        roles: set[str] = set()
        email = identity.email
        if email and self.google_groups is not None and self.mapping.wants_google_groups():
            groups = {group.lower() for group in await self.google_groups.group_emails(email)}
            roles |= {role for role, names in self.mapping.google_groups.items() if names & groups}
        if email:
            domain = email.rpartition("@")[2]
            roles |= {role for role, names in self.mapping.emails.items() if email in names}
            roles |= {role for role, names in self.mapping.domains.items() if domain in names}
        return highest(roles)

    async def _entra_groups(self, identity: Identity) -> set[str]:
        claims = identity.claims
        if not _has_group_overage(claims):
            return {str(group).lower() for group in claims.get("groups") or []}
        oid = claims.get("oid")
        if self.graph is None or not oid:
            logger.warning(
                "an Entra ID sign-in is in too many groups to list in its token and no "
                "client secret is configured to look them up; it gets no role"
            )
            return set()
        return {group.lower() for group in await self.graph.member_object_ids(str(oid))}


class MicrosoftGraph:
    """getMemberObjects with the leader's own app credentials (client-credentials flow).
    The app needs the GroupMember.Read.All application permission."""

    SCOPE = "https://graph.microsoft.com/.default"

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: SecretStr,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.transport = transport
        self.clock = clock
        self._token: tuple[str, float] | None = None

    async def _access_token(self, client: httpx.AsyncClient) -> str:
        if self._token is not None and self._token[1] - 60 > self.clock():
            return self._token[0]
        response = await client.post(
            f"{ENTRA_AUTHORITY}/{self.tenant_id}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret.get_secret_value(),
                "scope": self.SCOPE,
            },
        )
        response.raise_for_status()
        body = response.json()
        expires_at = self.clock() + float(body.get("expires_in", 3600))
        self._token = (str(body["access_token"]), expires_at)
        return self._token[0]

    async def member_object_ids(self, user_object_id: str) -> set[str]:
        try:
            async with httpx.AsyncClient(
                timeout=DIRECTORY_TIMEOUT_SECONDS, transport=self.transport
            ) as client:
                token = await self._access_token(client)
                response = await client.post(
                    "https://graph.microsoft.com/v1.0/users/"
                    f"{quote(user_object_id, safe='')}/getMemberObjects",
                    json={"securityEnabledOnly": False},
                    headers={"Authorization": f"Bearer {token}"},
                )
                response.raise_for_status()
                return {str(value).lower() for value in response.json().get("value", [])}
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise RoleLookupFailed(
                f"Microsoft Graph could not be asked: {type(exc).__name__}"
            ) from exc


class GoogleCloudIdentity:
    """The person's Google Groups (direct and inherited) through the Cloud Identity Groups
    API, signed in as a service account that holds the Groups Reader admin role."""

    SCOPE = "https://www.googleapis.com/auth/cloud-identity.groups.readonly"
    SEARCH_URL = (
        "https://cloudidentity.googleapis.com/v1/groups/-/memberships:searchTransitiveGroups"
    )
    MAX_PAGES = 20
    _SAFE_EMAIL = re.compile(r"[^'\\\s]+@[^'\\\s]+")

    def __init__(
        self,
        service_account: dict[str, str],
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.account = service_account
        self.transport = transport
        self.clock = clock
        self._token: tuple[str, float] | None = None

    async def _access_token(self, client: httpx.AsyncClient) -> str:
        if self._token is not None and self._token[1] - 60 > self.clock():
            return self._token[0]
        now = int(self.clock())
        key_id = self.account.get("private_key_id")
        assertion = jwt.encode(
            {
                "iss": self.account["client_email"],
                "scope": self.SCOPE,
                "aud": self.account["token_uri"],
                "iat": now,
                "exp": now + 3600,
            },
            self.account["private_key"],
            algorithm="RS256",
            headers={"kid": key_id} if key_id else None,
        )
        response = await client.post(
            self.account["token_uri"],
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            },
        )
        response.raise_for_status()
        body = response.json()
        expires_at = self.clock() + float(body.get("expires_in", 3600))
        self._token = (str(body["access_token"]), expires_at)
        return self._token[0]

    async def group_emails(self, email: str) -> set[str]:
        if not self._SAFE_EMAIL.fullmatch(email):
            return set()  # cannot be quoted safely into the search query
        query = (
            f"member_key_id == '{email}' "
            "&& 'cloud.googleapis.com/groups.discussion_forum' in labels"
        )
        found: set[str] = set()
        page_token: str | None = None
        try:
            async with httpx.AsyncClient(
                timeout=DIRECTORY_TIMEOUT_SECONDS, transport=self.transport
            ) as client:
                token = await self._access_token(client)
                for _ in range(self.MAX_PAGES):
                    params = {"query": query}
                    if page_token:
                        params["pageToken"] = page_token
                    response = await client.get(
                        self.SEARCH_URL,
                        params=params,
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    response.raise_for_status()
                    body = response.json()
                    for membership in body.get("memberships", []):
                        group = (membership.get("groupKey") or {}).get("id")
                        if group:
                            found.add(str(group).lower())
                    page_token = body.get("nextPageToken")
                    if not page_token:
                        break
        except (httpx.HTTPError, KeyError, TypeError, ValueError, jwt.PyJWTError) as exc:
            raise RoleLookupFailed(
                f"Google Cloud Identity could not be asked: {type(exc).__name__}"
            ) from exc
        return found
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest packages/leader/tests/test_roles.py -v`, then `uv run pytest packages/leader -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/leader/src/swarmscribe_leader/auth/roles.py packages/leader/tests/conftest.py packages/leader/tests/test_roles.py
git commit -m "Leader: resolve administrators' roles from Entra groups, Google Groups and email lists

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Admin authentication in the application

**Files:**
- Modify: `packages/leader/src/swarmscribe_leader/errors.py` (`retry_after`, `ServiceUnavailable`)
- Modify: `packages/leader/src/swarmscribe_leader/api/errors.py` (`leader_error` handler)
- Create: `packages/leader/src/swarmscribe_leader/api/admin_auth.py`
- Create: `packages/leader/src/swarmscribe_leader/api/admin_models.py`
- Create: `packages/leader/src/swarmscribe_leader/api/admin.py`
- Modify: `packages/leader/src/swarmscribe_leader/app.py`
- Modify: `packages/leader/src/swarmscribe_leader/api/health.py`
- Modify: `packages/leader/tests/conftest.py` (`admin_app`, `admin_client`)
- Test: `packages/leader/tests/test_admin_auth.py`

**Interfaces:**
- Consumes: `TokenVerifier`, `providers_from`, `login_providers`, `http_fetch`, `MetadataUnavailable`, `Identity` (Task 6); `RoleResolver`, `RoleMapping`, `MicrosoftGraph`, `GoogleCloudIdentity`, `RoleLookupFailed`, `at_least`, `Role` (Task 7).
- Produces:
  - `errors.ServiceUnavailable(LeaderError)`: status 503, code `unavailable`, `retry_after = 10`; every `LeaderError` with `retry_after` answers with a `Retry-After` header.
  - `api/admin_auth.py`: `@dataclass(frozen=True) class Admin: identity: Identity; role: Role` with `actor -> str`; `class AdminAuth(verifier, resolver)` with `from_settings(settings, *, fetch=None, graph=None, google_groups=None, clock=time.time)` and `async authenticate(token) -> tuple[Identity, Role | None]`; `def require(role: Role) -> Callable[[Request], Awaitable[Admin]]` (FastAPI dependency: `401` without a valid token, `403` below the role with an `admin.refused` audit row, `503` when a provider or directory cannot be asked).
  - `create_app(settings, *, background=True, admin_auth: AdminAuth | None = None)`; `app.state.admin_auth`.
  - `api/admin.py`: `router` (prefix `/v1/admin`), annotated dependencies `Viewer`, `Operator`, `Administrator`, `Session`, helper `_viewed(session, admin, action, detail=None)`; routes `GET /login-config` (no sign-in) and `GET /whoami` (viewer, audited `whoami.view`).
  - `api/admin_models.py`: `WhoAmI`, `LoginProvider`, `LoginConfig`.
  - `/readyz` answers `503 {"status": "sign-in metadata unavailable"}` until every configured provider's metadata has been fetched once.
  - Fixtures `admin_app` (both providers, fake fetcher and directories, no background loops) and `admin_client`.

- [ ] **Step 1: Add the app fixtures**

In `packages/leader/tests/conftest.py`, add these imports:

```python
from swarmscribe_leader.api.admin_auth import AdminAuth
from swarmscribe_leader.app import create_app
```

and append:

```python
@pytest.fixture
async def admin_app(engine, migrated_database_url, sign_in_settings, idp, graph, google_groups):
    settings = sign_in_settings(database_url=migrated_database_url)
    auth = AdminAuth.from_settings(
        settings, fetch=idp.fetch, graph=graph, google_groups=google_groups
    )
    application = create_app(settings, background=False, admin_auth=auth)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def admin_client(admin_app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=admin_app), base_url="http://leader"
    ) as http:
        yield http
```

- [ ] **Step 2: Write the failing tests**

`packages/leader/tests/test_admin_auth.py`:

```python
import httpx
import pytest
from sqlalchemy import select
from swarmscribe_leader.app import create_app
from swarmscribe_leader.config import Settings
from swarmscribe_leader.db.models import AuditEntry


async def audit_rows(sessionmaker, action):
    async with sessionmaker() as session:
        return list(
            (await session.scalars(select(AuditEntry).where(AuditEntry.action == action))).all()
        )


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_a_call_without_a_token_is_401(admin_client):
    response = await admin_client.get("/v1/admin/whoami")
    assert (response.status_code, response.json()["code"]) == (401, "unauthorized")


@pytest.mark.parametrize("header", ["Bearer", "Basic dXNlcjpwYXNz", "Bearer not-a-token"])
async def test_a_malformed_authorization_is_401(admin_client, header):
    response = await admin_client.get("/v1/admin/whoami", headers={"Authorization": header})
    assert response.status_code == 401


@pytest.mark.parametrize("role", ["viewer", "operator", "admin"])
@pytest.mark.parametrize("provider", ["entra", "google"])
async def test_each_provider_signs_in_with_its_role(admin_client, idp, provider, role):
    response = await admin_client.get(
        "/v1/admin/whoami", headers=idp.bearer(role, provider=provider)
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["provider"], body["role"], body["email"]) == (
        provider,
        role,
        f"{role}@example.org",
    )


async def test_google_groups_give_a_role(admin_client, idp, google_groups):
    google_groups.groups["person@example.org"] = {"Operators@Example.org"}
    response = await admin_client.get("/v1/admin/whoami", headers=bearer(idp.google()))
    assert response.json()["role"] == "operator"
    assert google_groups.calls == ["person@example.org"]


async def test_entra_group_overage_is_resolved_through_graph(admin_client, idp, graph):
    graph.groups["00000000-0000-4000-8000-0000000000a1"] = {idp.ENTRA_GROUPS["admin"]}
    token = idp.entra(
        groups=None,
        _claim_names={"groups": "src1"},
        _claim_sources={"src1": {"endpoint": "https://graph.microsoft.com/v1.0/users/x"}},
    )
    response = await admin_client.get("/v1/admin/whoami", headers=bearer(token))
    assert response.json()["role"] == "admin"


async def test_a_person_with_no_role_is_refused_and_the_refusal_is_audited(
    admin_client, idp, sessionmaker
):
    response = await admin_client.get("/v1/admin/whoami", headers=idp.bearer(None))
    assert (response.status_code, response.json()["code"]) == (403, "forbidden")
    assert "no SwarmScribe role" in response.json()["message"]
    (entry,) = await audit_rows(sessionmaker, "admin.refused")
    assert entry.actor == f"nobody@example.org ({idp.ENTRA_ISSUER} entra-nobody)"
    assert entry.subject_id == "GET /v1/admin/whoami"
    assert entry.detail == {"role": None, "required": "viewer"}


async def test_an_expired_token_is_401_token_expired(admin_client, idp):
    expired = idp.entra(lifetime=-300)
    response = await admin_client.get("/v1/admin/whoami", headers=bearer(expired))
    assert (response.status_code, response.json()["code"]) == (401, "token_expired")


async def test_an_unreachable_identity_provider_is_503_with_retry_after(admin_client, idp):
    idp.down = True
    response = await admin_client.get("/v1/admin/whoami", headers=idp.bearer("viewer"))
    assert (response.status_code, response.json()["code"]) == (503, "unavailable")
    assert response.headers["retry-after"] == "10"


async def test_a_failing_group_directory_is_503_not_403(admin_client, idp, google_groups):
    google_groups.failing = True
    response = await admin_client.get("/v1/admin/whoami", headers=bearer(idp.google()))
    assert (response.status_code, response.json()["code"]) == (503, "unavailable")


async def test_every_admin_call_is_audited_with_issuer_subject_and_email(
    admin_client, idp, sessionmaker
):
    await admin_client.get("/v1/admin/whoami", headers=idp.bearer("viewer"))
    (entry,) = await audit_rows(sessionmaker, "whoami.view")
    assert entry.actor == f"viewer@example.org ({idp.ENTRA_ISSUER} entra-viewer)"


async def test_tokens_never_reach_the_log_or_the_audit_log(
    admin_client, idp, sessionmaker, caplog, google_groups
):
    tokens = [
        idp.entra(groups=[idp.ENTRA_GROUPS["viewer"]], sub="entra-viewer"),
        idp.entra(lifetime=-300),
        idp.entra(signed_with="rogue"),
        idp.google(email="nobody@example.org"),
    ]
    with caplog.at_level("DEBUG"):
        for token in tokens:
            await admin_client.get("/v1/admin/whoami", headers=bearer(token))
        google_groups.failing = True
        failing = idp.google(sub="google-other")
        await admin_client.get("/v1/admin/whoami", headers=bearer(failing))
    async with sessionmaker() as session:
        entries = (await session.scalars(select(AuditEntry))).all()
    audit_text = " ".join(f"{e.actor} {e.subject_id} {e.detail}" for e in entries)
    for token in [*tokens, failing]:
        assert token not in caplog.text
        assert token not in audit_text


async def test_readyz_needs_the_sign_in_metadata(admin_client, idp):
    idp.down = True
    response = await admin_client.get("/readyz")
    assert (response.status_code, response.json()) == (
        503,
        {"status": "sign-in metadata unavailable"},
    )


async def test_readyz_is_ready_once_the_metadata_is_fetched(admin_client):
    response = await admin_client.get("/readyz")
    assert (response.status_code, response.json()) == (200, {"status": "ready"})


async def test_login_config_needs_no_sign_in_and_hides_the_entra_secret(admin_client, idp):
    response = await admin_client.get("/v1/admin/login-config")
    assert response.status_code == 200
    providers = response.json()["providers"]
    assert [p["name"] for p in providers] == ["entra", "google"]
    assert providers[0]["client_id"] == idp.ENTRA_CLIENT
    assert "entra-app-secret-value" not in response.text


async def test_a_leader_without_sign_in_refuses_admin_calls(engine, migrated_database_url, idp):
    settings = Settings(
        database_url=migrated_database_url, public_url="http://leader", link_key="k" * 32
    )
    application = create_app(settings, background=False)
    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://leader"
        ) as http:
            response = await http.get("/v1/admin/whoami", headers=idp.bearer("admin"))
            ready = await http.get("/readyz")
            config = await http.get("/v1/admin/login-config")
    assert (response.status_code, response.json()["message"]) == (
        401,
        "sign-in is not configured on this leader",
    )
    assert ready.status_code == 200
    assert config.json() == {"providers": []}
```

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest packages/leader/tests/test_admin_auth.py -v`
Expected: FAIL — `swarmscribe_leader.api.admin_auth` does not exist (conftest import error).

- [ ] **Step 4: Errors that tell the caller when to retry**

In `packages/leader/src/swarmscribe_leader/errors.py`, add `retry_after: int | None = None` as a class attribute of `LeaderError` (after `code = "bad_request"`), and append:

```python
class ServiceUnavailable(LeaderError):
    """Something the leader depends on (an identity provider, a group directory) cannot be
    reached right now; the caller should retry."""

    status = 503
    code = "unavailable"
    retry_after = 10
```

In `packages/leader/src/swarmscribe_leader/api/errors.py`, replace the `leader_error` handler with:

```python
    @app.exception_handler(LeaderError)
    async def leader_error(_request: Request, exc: LeaderError) -> JSONResponse:
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
        return error_response(exc.code, exc.message, exc.status, headers=headers)
```

- [ ] **Step 5: The admin dependency**

`packages/leader/src/swarmscribe_leader/api/admin_auth.py`:

```python
"""Who is calling /v1/admin, and may they? Refusals for a role are audited; tokens are never
logged or stored."""

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import Request

from .. import audit
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
from ..errors import Forbidden, ServiceUnavailable, Unauthorized

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Admin:
    identity: Identity
    role: Role

    @property
    def actor(self) -> str:
        return self.identity.actor


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


def _bearer(request: Request) -> str:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise Unauthorized(
            "sign in with `swarmscribe-admin login` and send the ID token as a Bearer token"
        )
    return token.strip()


async def _audit_refusal(
    request: Request, identity: Identity, granted: Role | None, required: Role
) -> None:
    route = request.scope.get("route")
    async with request.app.state.sessionmaker() as session:
        audit.record(
            session,
            actor=identity.actor,
            action="admin.refused",
            subject_type="endpoint",
            subject_id=f"{request.method} {getattr(route, 'path', '?')}",
            detail={"role": granted, "required": required},
        )
        await session.commit()


def require(role: Role) -> Callable[[Request], Awaitable[Admin]]:
    """A dependency admitting people whose role is `role` or higher."""

    async def dependency(request: Request) -> Admin:
        auth: AdminAuth = request.app.state.admin_auth
        identity, granted = await auth.authenticate(_bearer(request))
        if not at_least(granted, role):
            await _audit_refusal(request, identity, granted, role)
            if granted is None:
                raise Forbidden("you have no SwarmScribe role; ask an administrator for one")
            raise Forbidden(f"this needs the {role} role; you have {granted}")
        return Admin(identity=identity, role=granted)

    return dependency
```

- [ ] **Step 6: The first admin models and routes**

`packages/leader/src/swarmscribe_leader/api/admin_models.py` (Tasks 9 and 10 replace its import block and append more models):

```python
"""Request and response bodies of the /v1/admin API."""

from typing import Literal

from pydantic import BaseModel

JobState = Literal["queued", "leased", "completed", "failed", "cancelled"]
FollowerState = Literal["active", "draining", "revoked", "gone"]
RequiredDevice = Literal["any", "cuda", "cpu"]
NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$"


class WhoAmI(BaseModel):
    provider: str
    issuer: str
    subject: str
    email: str | None
    role: str


class LoginProvider(BaseModel):
    name: str
    client_id: str
    device_authorization_endpoint: str
    token_endpoint: str
    scope: str
    client_secret: str | None = None


class LoginConfig(BaseModel):
    providers: list[LoginProvider]
```

`packages/leader/src/swarmscribe_leader/api/admin.py`:

```python
"""The /v1/admin API.

Each route authenticates the caller and checks their role (`require`), calls one service
function, writes an audit entry and commits. Changes are audited by the service functions
with the change; reads are audited here.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit
from ..auth.oidc import login_providers
from .admin_auth import Admin, require
from .admin_models import LoginConfig, WhoAmI
from .deps import db_session, settings_of

router = APIRouter(prefix="/v1/admin")

Viewer = Annotated[Admin, Depends(require("viewer"))]
Operator = Annotated[Admin, Depends(require("operator"))]
Administrator = Annotated[Admin, Depends(require("admin"))]
Session = Annotated[AsyncSession, Depends(db_session)]


async def _viewed(
    session: AsyncSession, admin: Admin, action: str, detail: dict[str, Any] | None = None
) -> None:
    audit.record(session, actor=admin.actor, action=action, detail=detail)
    await session.commit()


@router.get("/login-config", response_model=LoginConfig)
async def login_config(request: Request) -> LoginConfig:
    """What the CLI needs to start a device-code sign-in. Needs no sign-in itself."""
    return LoginConfig.model_validate({"providers": login_providers(settings_of(request))})


@router.get("/whoami", response_model=WhoAmI)
async def whoami(admin: Viewer, session: Session) -> WhoAmI:
    await _viewed(session, admin, "whoami.view")
    identity = admin.identity
    return WhoAmI(
        provider=identity.provider,
        issuer=identity.issuer,
        subject=identity.subject,
        email=identity.email,
        role=admin.role,
    )
```

- [ ] **Step 7: Wire it into the application and readiness**

In `packages/leader/src/swarmscribe_leader/app.py`: change `from .api import files, follower, health` to `from .api import admin, files, follower, health`, add `from .api.admin_auth import AdminAuth`, change the signature to

```python
def create_app(
    settings: Settings, *, background: bool = True, admin_auth: AdminAuth | None = None
) -> FastAPI:
```

and after `app.state.backend_factory = backend_factory` add:

```python
    app.state.admin_auth = admin_auth or AdminAuth.from_settings(settings)
```

and after `app.include_router(follower.router)` add:

```python
    app.include_router(admin.router)
```

In `packages/leader/src/swarmscribe_leader/api/health.py`, replace the last line of `readyz` (`return JSONResponse({"status": "ready"})`) with:

```python
    if not await request.app.state.admin_auth.verifier.ready():
        return JSONResponse({"status": "sign-in metadata unavailable"}, status_code=503)
    return JSONResponse({"status": "ready"})
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `uv run pytest packages/leader -q`
Expected: PASS (the Plan A1 app tests build apps without providers: the verifier has nothing to fetch and is ready).

- [ ] **Step 9: Commit**

```bash
git add packages/leader/src/swarmscribe_leader packages/leader/tests/conftest.py packages/leader/tests/test_admin_auth.py
git commit -m "Leader: admin sign-in with roles, audited refusals, readiness needs sign-in metadata

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Admin API — status and read-only views

**Files:**
- Create: `packages/leader/src/swarmscribe_leader/reports.py`
- Modify: `packages/leader/src/swarmscribe_leader/api/admin_models.py` (import block; read models)
- Modify: `packages/leader/src/swarmscribe_leader/api/admin.py` (imports; read routes)
- Test: `packages/leader/tests/test_admin_api.py` (new)

**Interfaces:**
- Consumes: `Viewer`, `Administrator`, `Session`, `_viewed` (Task 8); `output_keys` (`jobs/claims.py`); `OPEN_STATES` (`jobs/store.py`).
- Produces (in `reports.py`, all `async`, all returning plain dicts shaped like the response models):
  - `status_summary(session) -> dict` — `jobs` (every state, zero-filled), `pools` (`[{pool, queued, leased}]`), `followers` (every state), `completed_last_hour`, `failed_attempts_last_day` (attempt outcomes `failed`/`expired` in the last 24 h), `locations` (`[{name, backend, enabled, last_scan_at, last_scan_error, scan_requested, recordings, consented}]`, present recordings only).
  - `list_locations(session) -> list[dict]`; `location_view(location) -> dict` (sync).
  - `list_jobs(session, *, state=None, location=None, limit=50) -> list[dict]` (newest first); `job_view(job, key, location_name, no_speech=None) -> dict` (sync); `describe_job(session, job) -> dict` (flushes and refreshes first).
  - `list_followers(session, *, state=None) -> list[dict]`; `describe_follower(session, follower) -> dict`.
  - `list_tokens(session) -> list[dict]`; `token_view(token) -> dict` (sync; never the token or its hash).
  - `consent_report(session, *, location=None, limit=500) -> dict` — `locations` (`[{name, consented, not_consented, withdrawn, missing}]`), `flagged` (`[{job_id, location, key, completed_at, output_location, outputs}]`), `truncated`; unknown location → `NotFound`.
- Produces (routes, all audited by `_viewed`): `GET /v1/admin/status` (viewer, `status.view`), `GET /locations` (viewer, `locations.view`), `GET /jobs?state=&location=&limit=` (viewer, `jobs.view`, limit 1–500), `GET /followers?state=` (viewer, `followers.view`), `GET /tokens` (admin, `tokens.view`), `GET /consent/report?location=&limit=` (viewer, `consent.view`, limit 1–5000).
- Produces (models): `PoolQueue`, `LocationStatus`, `Status`, `LocationOut`, `JobOut`, `FollowerOut`, `TokenOut`, `ConsentCounts`, `FlaggedOutputs`, `ConsentReport`.

- [ ] **Step 1: Write the failing tests**

`packages/leader/tests/test_admin_api.py`:

```python
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select
from swarmscribe_leader.auth.followers import create_join_token
from swarmscribe_leader.clock import utcnow
from swarmscribe_leader.db.models import AuditEntry, JobAttempt

CAPABILITIES = {
    "device": "cpu",
    "models": ["distil-large-v3"],
    "engine_version": "0.1.0",
    "pool": "default",
}


def write(root, key, data=b"audio"):
    path = root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


async def audit_rows(sessionmaker, action):
    async with sessionmaker() as session:
        return list(
            (await session.scalars(select(AuditEntry).where(AuditEntry.action == action))).all()
        )


async def join_token(sessionmaker, **overrides) -> tuple[uuid.UUID, str]:
    values = {
        "pool": "default",
        "expires_at": utcnow() + timedelta(days=1),
        "max_uses": 1,
        "created_by": "test",
    }
    values.update(overrides)
    async with sessionmaker() as session:
        token, plaintext = await create_join_token(session, **values)
        await session.commit()
    return token.id, plaintext


async def get(client, idp, path, role="viewer", **params):
    response = await client.get(path, headers=idp.bearer(role), params=params)
    assert response.status_code == 200, response.text
    return response.json()


async def test_status_summarises_queue_followers_and_locations(admin_client, idp, factory):
    here = await factory.location(name="here", last_scan_error="input folder 'x' is not available")
    await factory.job(await factory.recording(here, key="talks/a.mp3"))
    await factory.job(await factory.recording(here, key="talks/b.mp3"), state="failed")
    await factory.follower(state="draining")
    body = await get(admin_client, idp, "/v1/admin/status")
    assert body["jobs"] == {"queued": 1, "leased": 0, "completed": 0, "failed": 1, "cancelled": 0}
    assert body["pools"] == [{"pool": "default", "queued": 1, "leased": 0}]
    assert body["followers"] == {"active": 0, "draining": 1, "revoked": 0, "gone": 0}
    (location,) = body["locations"]
    assert (location["name"], location["recordings"], location["consented"]) == ("here", 2, 2)
    assert location["last_scan_error"] == "input folder 'x' is not available"
    assert location["scan_requested"] is False


async def test_status_counts_recent_completions_and_failed_attempts(
    admin_client, idp, factory, sessionmaker
):
    location = await factory.location()
    follower, _ = await factory.follower()
    recent = await factory.job(
        await factory.recording(location, key="talks/a.mp3"),
        state="completed",
        completed_at=utcnow(),
    )
    await factory.job(
        await factory.recording(location, key="talks/b.mp3"),
        state="completed",
        completed_at=utcnow() - timedelta(hours=2),
    )
    async with sessionmaker() as session:
        session.add(
            JobAttempt(
                job_id=recent.id,
                follower_id=follower.id,
                lease_id=uuid.uuid4(),
                started_at=utcnow(),
                ended_at=utcnow(),
                outcome="failed",
                reason="engine_error: out of memory",
            )
        )
        await session.commit()
    body = await get(admin_client, idp, "/v1/admin/status")
    assert (body["completed_last_hour"], body["failed_attempts_last_day"]) == (1, 1)


async def test_locations_are_listed(admin_client, idp, factory, tmp_path):
    await factory.location(name="here", input_prefix="incoming/")
    (row,) = await get(admin_client, idp, "/v1/admin/locations")
    assert (row["name"], row["backend"], row["root"], row["input_prefix"], row["enabled"]) == (
        "here",
        "local",
        str(tmp_path),
        "incoming/",
        True,
    )


async def test_jobs_are_listed_with_where_they_come_from_and_filtered(admin_client, idp, factory):
    here = await factory.location(name="here")
    there = await factory.location(name="there")
    failed = await factory.job(
        await factory.recording(here, key="talks/a.mp3"),
        state="failed",
        failure_reason="undecodable: no audio stream",
    )
    await factory.job(await factory.recording(there, key="talks/b.mp3"))
    rows = await get(admin_client, idp, "/v1/admin/jobs", state="failed")
    assert [(r["id"], r["location"], r["key"], r["failure_reason"]) for r in rows] == [
        (str(failed.id), "here", "talks/a.mp3", "undecodable: no audio stream")
    ]
    rows = await get(admin_client, idp, "/v1/admin/jobs", location="there")
    assert [r["location"] for r in rows] == ["there"]
    assert len(await get(admin_client, idp, "/v1/admin/jobs", limit=1)) == 1


@pytest.mark.parametrize("params", [{"limit": 501}, {"limit": 0}, {"state": "sleeping"}])
async def test_job_listing_parameters_are_checked(admin_client, idp, params):
    response = await admin_client.get(
        "/v1/admin/jobs", headers=idp.bearer("viewer"), params=params
    )
    assert (response.status_code, response.json()["code"]) == (422, "invalid_request")


async def test_followers_are_listed_with_device_and_leases(admin_client, idp, factory):
    busy, _ = await factory.follower(device="cuda")
    await factory.follower(state="gone")
    await factory.job(state="leased", lease_id=uuid.uuid4(), leased_by=busy.id, attempts=1)
    rows = await get(admin_client, idp, "/v1/admin/followers", state="active")
    assert [(r["id"], r["device"], r["leases"]) for r in rows] == [(str(busy.id), "cuda", 1)]
    assert len(await get(admin_client, idp, "/v1/admin/followers")) == 2


async def test_tokens_are_listed_without_the_token(admin_client, idp, sessionmaker):
    token_id, plaintext = await join_token(sessionmaker, pool="gpu")
    response = await admin_client.get("/v1/admin/tokens", headers=idp.bearer("admin"))
    (row,) = response.json()
    assert (row["id"], row["pool"], row["uses"], row["revoked"]) == (str(token_id), "gpu", 0, False)
    assert plaintext not in response.text


async def test_the_consent_report_counts_and_lists_outputs_flagged_for_deletion(
    admin_client, idp, factory
):
    here = await factory.location(name="here")
    withdrawn = await factory.recording(here, key="talks/a.mp3", consent="withdrawn")
    await factory.recording(here, key="talks/b.mp3", consent="not_consented")
    await factory.recording(here, key="talks/c.mp3", missing=True)
    flagged = await factory.job(
        withdrawn, state="completed", completed_at=utcnow(), outputs_flagged_for_deletion=True
    )
    body = await get(admin_client, idp, "/v1/admin/consent/report")
    assert body["locations"] == [
        {"name": "here", "consented": 0, "not_consented": 1, "withdrawn": 1, "missing": 1}
    ]
    (row,) = body["flagged"]
    assert (row["job_id"], row["key"], row["output_location"]) == (
        str(flagged.id),
        "talks/a.mp3",
        "here",
    )
    assert row["outputs"] == [
        "transcripts/talks/a.mp3.txt",
        "transcripts/talks/a.mp3.srt",
        "transcripts/talks/a.mp3.segments.json",
    ]
    assert body["truncated"] is False


async def test_the_consent_report_for_an_unknown_location_is_404(admin_client, idp):
    response = await admin_client.get(
        "/v1/admin/consent/report", headers=idp.bearer("viewer"), params={"location": "nowhere"}
    )
    assert (response.status_code, response.json()["code"]) == (404, "not_found")


async def test_every_read_is_audited(admin_client, idp, sessionmaker):
    for path in (
        "/v1/admin/status",
        "/v1/admin/locations",
        "/v1/admin/jobs",
        "/v1/admin/followers",
        "/v1/admin/consent/report",
    ):
        await get(admin_client, idp, path)
    await get(admin_client, idp, "/v1/admin/tokens", role="admin")
    async with sessionmaker() as session:
        actions = sorted(e.action for e in (await session.scalars(select(AuditEntry))).all())
    assert actions == sorted(
        [
            "status.view",
            "locations.view",
            "jobs.view",
            "followers.view",
            "consent.view",
            "tokens.view",
        ]
    )
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest packages/leader/tests/test_admin_api.py -v`
Expected: FAIL — the routes answer `404`.

- [ ] **Step 3: The read-only views**

`packages/leader/src/swarmscribe_leader/reports.py`:

```python
"""Read-only views for administrators. Each returns plain data shaped like its API model."""

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .db.models import (
    Follower,
    Job,
    JobAttempt,
    JobResult,
    JoinToken,
    Recording,
    StorageLocation,
)
from .errors import NotFound
from .jobs.claims import output_keys
from .jobs.store import OPEN_STATES

JOB_STATES = ("queued", "leased", "completed", "failed", "cancelled")
FOLLOWER_STATES = ("active", "draining", "revoked", "gone")


def location_view(location: StorageLocation) -> dict[str, Any]:
    return {
        "id": str(location.id),
        "name": location.name,
        "backend": location.backend,
        "root": (location.config or {}).get("root"),
        "input_prefix": location.input_prefix,
        "output_prefix": location.output_prefix,
        "pool": location.pool,
        "required_device": location.required_device,
        "scan_interval_s": location.scan_interval_s,
        "enabled": location.enabled,
        "last_scan_at": location.last_scan_at,
        "last_scan_error": location.last_scan_error,
        "scan_requested": location.scan_requested_at is not None,
    }


def job_view(
    job: Job, key: str, location_name: str, no_speech: bool | None = None
) -> dict[str, Any]:
    return {
        "id": str(job.id),
        "state": job.state,
        "location": location_name,
        "key": key,
        "priority": job.priority,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "pool": job.pool,
        "leased_by": str(job.leased_by) if job.leased_by else None,
        "failure_reason": job.failure_reason,
        "cancelled_by": job.cancelled_by,
        "no_speech": no_speech,
        "created_at": job.created_at,
        "completed_at": job.completed_at,
    }


def follower_view(follower: Follower, leases: int) -> dict[str, Any]:
    return {
        "id": str(follower.id),
        "pool": follower.pool,
        "state": follower.state,
        "device": (follower.capabilities or {}).get("device"),
        "last_seen_at": follower.last_seen_at,
        "created_at": follower.created_at,
        "leases": leases,
    }


def token_view(token: JoinToken) -> dict[str, Any]:
    return {
        "id": str(token.id),
        "pool": token.pool,
        "expires_at": token.expires_at,
        "max_uses": token.max_uses,
        "uses": token.uses,
        "revoked": token.revoked,
        "created_by": token.created_by,
        "created_at": token.created_at,
    }


async def status_summary(session: AsyncSession) -> dict[str, Any]:
    jobs = dict.fromkeys(JOB_STATES, 0)
    for state, count in (
        await session.execute(select(Job.state, func.count()).group_by(Job.state))
    ).all():
        jobs[state] = count
    pools: dict[str, dict[str, Any]] = {}
    for pool, state, count in (
        await session.execute(
            select(Job.pool, Job.state, func.count())
            .where(Job.state.in_(OPEN_STATES))
            .group_by(Job.pool, Job.state)
        )
    ).all():
        pools.setdefault(pool, {"pool": pool, "queued": 0, "leased": 0})[state] = count
    followers = dict.fromkeys(FOLLOWER_STATES, 0)
    for state, count in (
        await session.execute(select(Follower.state, func.count()).group_by(Follower.state))
    ).all():
        followers[state] = count
    completed_last_hour = await session.scalar(
        select(func.count())
        .select_from(Job)
        .where(Job.state == "completed", Job.completed_at >= func.now() - timedelta(hours=1))
    )
    failed_attempts = await session.scalar(
        select(func.count())
        .select_from(JobAttempt)
        .where(
            JobAttempt.outcome.in_(("failed", "expired")),
            JobAttempt.ended_at >= func.now() - timedelta(days=1),
        )
    )
    counts: dict[Any, dict[str, int]] = {}
    for location_id, consent, count in (
        await session.execute(
            select(Recording.location_id, Recording.consent, func.count())
            .where(Recording.missing.is_(False))
            .group_by(Recording.location_id, Recording.consent)
        )
    ).all():
        entry = counts.setdefault(location_id, {"recordings": 0, "consented": 0})
        entry["recordings"] += count
        if consent == "consented":
            entry["consented"] += count
    locations = (
        await session.scalars(select(StorageLocation).order_by(StorageLocation.name))
    ).all()
    return {
        "jobs": jobs,
        "pools": [pools[name] for name in sorted(pools)],
        "followers": followers,
        "completed_last_hour": completed_last_hour or 0,
        "failed_attempts_last_day": failed_attempts or 0,
        "locations": [
            {
                "name": location.name,
                "backend": location.backend,
                "enabled": location.enabled,
                "last_scan_at": location.last_scan_at,
                "last_scan_error": location.last_scan_error,
                "scan_requested": location.scan_requested_at is not None,
                **counts.get(location.id, {"recordings": 0, "consented": 0}),
            }
            for location in locations
        ],
    }


async def list_locations(session: AsyncSession) -> list[dict[str, Any]]:
    locations = (
        await session.scalars(select(StorageLocation).order_by(StorageLocation.name))
    ).all()
    return [location_view(location) for location in locations]


async def list_jobs(
    session: AsyncSession,
    *,
    state: str | None = None,
    location: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    query = (
        select(Job, Recording.key, StorageLocation.name, JobResult.no_speech)
        .join(Recording, Recording.id == Job.recording_id)
        .join(StorageLocation, StorageLocation.id == Recording.location_id)
        .outerjoin(JobResult, JobResult.job_id == Job.id)
        .order_by(Job.created_at.desc(), Job.id)
        .limit(limit)
    )
    if state is not None:
        query = query.where(Job.state == state)
    if location is not None:
        query = query.where(StorageLocation.name == location)
    rows = (await session.execute(query)).all()
    return [job_view(job, key, name, no_speech) for job, key, name, no_speech in rows]


async def describe_job(session: AsyncSession, job: Job) -> dict[str, Any]:
    await session.flush()
    await session.refresh(job)
    recording = await session.get(Recording, job.recording_id)
    location = await session.get(StorageLocation, recording.location_id)
    no_speech = await session.scalar(
        select(JobResult.no_speech).where(JobResult.job_id == job.id)
    )
    return job_view(job, recording.key, location.name, no_speech)


def _leases_per_follower():
    return (
        select(Job.leased_by, func.count().label("leases"))
        .where(Job.state == "leased", Job.leased_by.is_not(None))
        .group_by(Job.leased_by)
        .subquery()
    )


async def list_followers(
    session: AsyncSession, *, state: str | None = None
) -> list[dict[str, Any]]:
    leases = _leases_per_follower()
    query = (
        select(Follower, func.coalesce(leases.c.leases, 0))
        .outerjoin(leases, leases.c.leased_by == Follower.id)
        .order_by(Follower.created_at, Follower.id)
    )
    if state is not None:
        query = query.where(Follower.state == state)
    rows = (await session.execute(query)).all()
    return [follower_view(follower, count) for follower, count in rows]


async def describe_follower(session: AsyncSession, follower: Follower) -> dict[str, Any]:
    await session.flush()
    await session.refresh(follower)
    leases = await session.scalar(
        select(func.count())
        .select_from(Job)
        .where(Job.leased_by == follower.id, Job.state == "leased")
    )
    return follower_view(follower, leases or 0)


async def list_tokens(session: AsyncSession) -> list[dict[str, Any]]:
    tokens = (
        await session.scalars(select(JoinToken).order_by(JoinToken.created_at, JoinToken.id))
    ).all()
    return [token_view(token) for token in tokens]


async def consent_report(
    session: AsyncSession, *, location: str | None = None, limit: int = 500
) -> dict[str, Any]:
    """Consent per location, and the completed outputs flagged for deletion because their
    recording's consent was withdrawn (deletion itself is Plan B)."""
    all_locations = (
        await session.scalars(select(StorageLocation).order_by(StorageLocation.name))
    ).all()
    names = {loc.id: loc.name for loc in all_locations}
    chosen = [loc for loc in all_locations if location is None or loc.name == location]
    if location is not None and not chosen:
        raise NotFound(f"no location named {location!r}")
    ids = [loc.id for loc in chosen]
    counts = {
        loc.id: {"name": loc.name, "consented": 0, "not_consented": 0, "withdrawn": 0, "missing": 0}
        for loc in chosen
    }
    for location_id, consent, missing, count in (
        await session.execute(
            select(Recording.location_id, Recording.consent, Recording.missing, func.count())
            .where(Recording.location_id.in_(ids))
            .group_by(Recording.location_id, Recording.consent, Recording.missing)
        )
    ).all():
        counts[location_id]["missing" if missing else consent] += count
    rows = (
        await session.execute(
            select(Job, Recording, StorageLocation)
            .join(Recording, Recording.id == Job.recording_id)
            .join(StorageLocation, StorageLocation.id == Recording.location_id)
            .where(
                Job.state == "completed",
                Job.outputs_flagged_for_deletion.is_(True),
                StorageLocation.id.in_(ids),
            )
            .order_by(Job.completed_at, Job.id)
            .limit(limit + 1)
        )
    ).all()
    flagged = [
        {
            "job_id": str(job.id),
            "location": loc.name,
            "key": recording.key,
            "completed_at": job.completed_at,
            "output_location": names[loc.output_location_id or loc.id],
            "outputs": list(output_keys(loc.output_prefix, recording.key).values()),
        }
        for job, recording, loc in rows[:limit]
    ]
    return {
        "locations": list(counts.values()),
        "flagged": flagged,
        "truncated": len(rows) > limit,
    }
```

- [ ] **Step 4: The read models**

In `packages/leader/src/swarmscribe_leader/api/admin_models.py`, replace the import block with:

```python
from datetime import datetime
from typing import Literal

from pydantic import BaseModel
```

and append:

```python
class PoolQueue(BaseModel):
    pool: str
    queued: int
    leased: int


class LocationStatus(BaseModel):
    name: str
    backend: str
    enabled: bool
    last_scan_at: datetime | None
    last_scan_error: str | None
    scan_requested: bool
    recordings: int
    consented: int


class Status(BaseModel):
    jobs: dict[str, int]
    pools: list[PoolQueue]
    followers: dict[str, int]
    completed_last_hour: int
    failed_attempts_last_day: int
    locations: list[LocationStatus]


class LocationOut(BaseModel):
    id: str
    name: str
    backend: str
    root: str | None
    input_prefix: str
    output_prefix: str
    pool: str
    required_device: str
    scan_interval_s: int
    enabled: bool
    last_scan_at: datetime | None
    last_scan_error: str | None
    scan_requested: bool


class JobOut(BaseModel):
    id: str
    state: str
    location: str
    key: str
    priority: int
    attempts: int
    max_attempts: int
    pool: str
    leased_by: str | None
    failure_reason: str | None
    cancelled_by: str | None
    no_speech: bool | None
    created_at: datetime
    completed_at: datetime | None


class FollowerOut(BaseModel):
    id: str
    pool: str
    state: str
    device: str | None
    last_seen_at: datetime
    created_at: datetime
    leases: int


class TokenOut(BaseModel):
    id: str
    pool: str
    expires_at: datetime
    max_uses: int
    uses: int
    revoked: bool
    created_by: str
    created_at: datetime


class ConsentCounts(BaseModel):
    name: str
    consented: int
    not_consented: int
    withdrawn: int
    missing: int


class FlaggedOutputs(BaseModel):
    job_id: str
    location: str
    key: str
    completed_at: datetime | None
    output_location: str
    outputs: list[str]


class ConsentReport(BaseModel):
    locations: list[ConsentCounts]
    flagged: list[FlaggedOutputs]
    truncated: bool
```

- [ ] **Step 5: The read routes**

In `packages/leader/src/swarmscribe_leader/api/admin.py`, replace the import block with:

```python
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit, reports
from ..auth.oidc import login_providers
from .admin_auth import Admin, require
from .admin_models import (
    ConsentReport,
    FollowerOut,
    FollowerState,
    JobOut,
    JobState,
    LocationOut,
    LoginConfig,
    Status,
    TokenOut,
    WhoAmI,
)
from .deps import db_session, settings_of
```

and append:

```python
@router.get("/status", response_model=Status)
async def status(admin: Viewer, session: Session) -> Status:
    summary = await reports.status_summary(session)
    await _viewed(session, admin, "status.view")
    return Status.model_validate(summary)


@router.get("/locations", response_model=list[LocationOut])
async def list_locations(admin: Viewer, session: Session) -> list[LocationOut]:
    rows = await reports.list_locations(session)
    await _viewed(session, admin, "locations.view")
    return [LocationOut.model_validate(row) for row in rows]


@router.get("/jobs", response_model=list[JobOut])
async def list_jobs(
    admin: Viewer,
    session: Session,
    state: JobState | None = None,
    location: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[JobOut]:
    rows = await reports.list_jobs(session, state=state, location=location, limit=limit)
    await _viewed(session, admin, "jobs.view", {"state": state, "location": location})
    return [JobOut.model_validate(row) for row in rows]


@router.get("/followers", response_model=list[FollowerOut])
async def list_followers(
    admin: Viewer, session: Session, state: FollowerState | None = None
) -> list[FollowerOut]:
    rows = await reports.list_followers(session, state=state)
    await _viewed(session, admin, "followers.view", {"state": state})
    return [FollowerOut.model_validate(row) for row in rows]


@router.get("/tokens", response_model=list[TokenOut])
async def list_tokens(admin: Administrator, session: Session) -> list[TokenOut]:
    rows = await reports.list_tokens(session)
    await _viewed(session, admin, "tokens.view")
    return [TokenOut.model_validate(row) for row in rows]


@router.get("/consent/report", response_model=ConsentReport)
async def consent_report(
    admin: Viewer,
    session: Session,
    location: str | None = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 500,
) -> ConsentReport:
    report = await reports.consent_report(session, location=location, limit=limit)
    await _viewed(session, admin, "consent.view", {"location": location})
    return ConsentReport.model_validate(report)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest packages/leader/tests/test_admin_api.py packages/leader/tests/test_admin_auth.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add packages/leader/src/swarmscribe_leader/reports.py packages/leader/src/swarmscribe_leader/api/admin.py packages/leader/src/swarmscribe_leader/api/admin_models.py packages/leader/tests/test_admin_api.py
git commit -m "Leader: admin status, listings and consent report, each audited

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Admin API — changes, and the role boundary of every route

**Files:**
- Modify: `packages/leader/src/swarmscribe_leader/storage/local.py` (new `validate_key`; `path_for` uses it)
- Create: `packages/leader/src/swarmscribe_leader/ingest/locations.py`
- Create: `packages/leader/src/swarmscribe_leader/jobs/admin.py`
- Modify: `packages/leader/src/swarmscribe_leader/auth/followers.py` (`drain`, `revoke_follower`, `revoke_token`)
- Modify: `packages/leader/src/swarmscribe_leader/api/admin_models.py` (import block; change models)
- Modify: `packages/leader/src/swarmscribe_leader/api/admin.py` (imports; change routes)
- Test: `packages/leader/tests/test_admin_api.py`

**Interfaces:**
- Consumes: `store.cancel(..., by=)`, `store.release_all`, `OPEN_STATES` (Tasks 2–4); `create_join_token` (Plan A1); `reports.location_view/describe_job/describe_follower/token_view` (Task 9); `Operator`, `Administrator` (Task 8).
- Produces:
  - `storage.local.validate_key(key: str) -> None` (raises `StorageError`).
  - `ingest/locations.py`: `add_location(session, *, name, root, input_prefix, output_prefix, pool, required_device, scan_interval_s, actor) -> StorageLocation` (codes `root_unavailable` 400, `exists` 409); `set_enabled(session, name, enabled, *, actor) -> StorageLocation`; `request_scan(session, name, *, now, actor) -> StorageLocation` (code `disabled` 409). Audit actions `location.add`, `location.disable`, `location.enable`, `location.ingest`.
  - `jobs/admin.py`: `retry_job(session, job_id, *, actor, max_attempts) -> Job` (codes `not_retryable`, `not_consented`, `recording_changed`, `already_open`); `cancel_job(session, job_id, *, now, actor) -> Job` (code `not_open`); `set_priority(session, job_id, priority, *, actor) -> Job` (code `not_open`). Audit actions `job.retry` (subject = the new job, `detail.retry_of`), `job.cancel`, `job.priority` (`detail.from`/`to`).
  - `auth/followers.py`: `drain(session, follower_id, *, actor) -> Follower` (code `revoked`); `revoke_follower(session, follower_id, *, now, actor) -> tuple[Follower, int]`; `revoke_token(session, token_id, *, actor) -> JoinToken`. Audit actions `follower.drain`, `follower.revoke` (`detail.released`), `token.revoke`; `token.create` comes from `create_join_token`.
  - Routes: `POST /locations` (admin, 201), `POST /locations/{name}/disable|enable` (admin), `POST /locations/{name}/ingest` (operator, 202), `POST /jobs/{job_id}/retry|cancel` (operator), `POST /jobs/{job_id}/priority` body `{"priority": int}` (operator), `POST /followers/{follower_id}/drain` (operator), `POST /followers/{follower_id}/revoke` (admin), `POST /tokens` body `{"pool", "expires_in_seconds", "max_uses"}` (admin, 201, the token appears once in the response), `POST /tokens/{token_id}/revoke` (admin).
  - Models: `LocationIn`, `PriorityIn`, `TokenIn`, `TokenCreated`, `ScanRequested`, `FollowerRevoked`.

- [ ] **Step 1: Write the failing tests**

In `packages/leader/tests/test_admin_api.py`, change the imports to:

```python
import asyncio
import re
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select
from swarmscribe_leader.auth.followers import create_join_token
from swarmscribe_leader.clock import utcnow
from swarmscribe_leader.db.models import AuditEntry, Job, JobAttempt, JoinToken
from swarmscribe_leader.ingest.scanner import scan_due_locations
from swarmscribe_leader.jobs import store
```

and append:

```python
async def post(client, idp, path, role, body=None):
    return await client.post(path, headers=idp.bearer(role), json=body)


def actor(idp, role):
    return f"{role}@example.org ({idp.ENTRA_ISSUER} entra-{role})"


# --- locations ------------------------------------------------------------------


async def test_add_a_location_then_list_it(admin_client, idp, sessionmaker, tmp_path):
    response = await post(
        admin_client,
        idp,
        "/v1/admin/locations",
        "admin",
        {"name": "archive-1", "root": str(tmp_path), "input_prefix": "incoming/"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert (
        body["name"],
        body["backend"],
        body["root"],
        body["input_prefix"],
        body["output_prefix"],
        body["scan_interval_s"],
        body["enabled"],
    ) == ("archive-1", "local", str(tmp_path), "incoming/", "transcripts/", 900, True)
    listed = await get(admin_client, idp, "/v1/admin/locations")
    assert [row["name"] for row in listed] == ["archive-1"]
    (entry,) = await audit_rows(sessionmaker, "location.add")
    assert entry.actor == actor(idp, "admin")


async def test_a_duplicate_location_name_is_409(admin_client, idp, tmp_path):
    body = {"name": "archive-1", "root": str(tmp_path)}
    assert (await post(admin_client, idp, "/v1/admin/locations", "admin", body)).status_code == 201
    again = await post(admin_client, idp, "/v1/admin/locations", "admin", body)
    assert (again.status_code, again.json()["code"]) == (409, "exists")


@pytest.mark.parametrize(
    "change, field",
    [
        ({"name": "a/b"}, "name"),
        ({"name": "has space"}, "name"),
        ({"root": "relative/folder"}, "root"),
        ({"input_prefix": "../escape/"}, "input_prefix"),
        ({"input_prefix": "/absolute/"}, "input_prefix"),
        ({"output_prefix": "a\\b/"}, "output_prefix"),
        ({"backend": "azure"}, "backend"),
        ({"scan_interval_s": 5}, "scan_interval_s"),
        ({"required_device": "tpu"}, "required_device"),
    ],
)
async def test_invalid_locations_are_refused(admin_client, idp, tmp_path, change, field):
    body = {"name": "archive-1", "root": str(tmp_path), **change}
    response = await post(admin_client, idp, "/v1/admin/locations", "admin", body)
    assert (response.status_code, response.json()["code"]) == (422, "invalid_request")
    assert field in response.json()["message"]


async def test_a_root_this_leader_cannot_see_is_refused(admin_client, idp, tmp_path):
    body = {"name": "archive-1", "root": str(tmp_path / "not-mounted")}
    response = await post(admin_client, idp, "/v1/admin/locations", "admin", body)
    assert (response.status_code, response.json()["code"]) == (400, "root_unavailable")


async def test_disable_stops_scanning_and_enable_resumes_it(
    admin_app, admin_client, idp, factory, sessionmaker
):
    await factory.location(name="here")
    disabled = await post(admin_client, idp, "/v1/admin/locations/here/disable", "admin")
    assert disabled.json()["enabled"] is False
    async def scanned() -> set[str]:
        backends = admin_app.state.backend_factory
        return set(
            await scan_due_locations(sessionmaker, backends, now=utcnow(), max_attempts=3)
        )

    assert "here" not in await scanned()
    enabled = await post(admin_client, idp, "/v1/admin/locations/here/enable", "admin")
    assert enabled.json()["enabled"] is True
    assert "here" in await scanned()
    actions = [e.action for e in await audit_rows(sessionmaker, "location.disable")]
    assert actions == ["location.disable"]


async def test_ingest_requests_a_scan_that_the_scanner_then_runs(
    admin_app, admin_client, idp, factory, sessionmaker, tmp_path
):
    write(tmp_path, "consent.txt", b"**/*.mp3\n")
    write(tmp_path, "talks/one.mp3")
    now = utcnow()
    await factory.location(name="here", last_scan_at=now)  # not due for 15 minutes
    response = await post(admin_client, idp, "/v1/admin/locations/here/ingest", "operator")
    assert (response.status_code, response.json()["name"]) == (202, "here")
    results = await scan_due_locations(
        sessionmaker, admin_app.state.backend_factory, now=now, max_attempts=3
    )
    assert results["here"].jobs_created == 1


async def test_ingest_of_a_disabled_or_unknown_location(admin_client, idp, factory):
    await factory.location(name="off", enabled=False)
    off = await post(admin_client, idp, "/v1/admin/locations/off/ingest", "operator")
    assert (off.status_code, off.json()["code"]) == (409, "disabled")
    unknown = await post(admin_client, idp, "/v1/admin/locations/nowhere/ingest", "operator")
    assert unknown.status_code == 404


# --- jobs -----------------------------------------------------------------------


async def test_retry_queues_a_new_job_for_a_failed_one(admin_client, idp, factory, sessionmaker):
    failed = await factory.job(state="failed", failure_reason="engine_error: x", priority=4)
    response = await post(admin_client, idp, f"/v1/admin/jobs/{failed.id}/retry", "operator")
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["state"], body["attempts"], body["priority"]) == ("queued", 0, 4)
    assert body["id"] != str(failed.id)
    (entry,) = await audit_rows(sessionmaker, "job.retry")
    assert (entry.subject_id, entry.detail) == (body["id"], {"retry_of": str(failed.id)})


@pytest.mark.parametrize(
    "recording_values, code",
    [
        ({"consent": "withdrawn"}, "not_consented"),
        ({"consent": "not_consented"}, "not_consented"),
        ({"missing": True}, "not_consented"),
        ({"source_version": "99-9"}, "recording_changed"),
    ],
)
async def test_retry_never_queues_a_recording_that_may_not_be_processed(
    admin_client, idp, factory, sessionmaker, recording_values, code
):
    recording = await factory.recording(**recording_values)
    failed = await factory.job(recording, state="failed", source_version="10-1")
    response = await post(admin_client, idp, f"/v1/admin/jobs/{failed.id}/retry", "operator")
    assert (response.status_code, response.json()["code"]) == (409, code)
    async with sessionmaker() as session:
        assert len((await session.scalars(select(Job))).all()) == 1


async def test_retry_of_an_unfinished_job_is_409(admin_client, idp, factory):
    queued = await factory.job()
    response = await post(admin_client, idp, f"/v1/admin/jobs/{queued.id}/retry", "operator")
    assert (response.status_code, response.json()["code"]) == (409, "not_retryable")


async def test_two_retries_at_once_queue_the_job_once(admin_client, idp, factory, sessionmaker):
    failed = await factory.job(state="failed")
    headers = idp.bearer("operator")
    responses = await asyncio.gather(
        *(admin_client.post(f"/v1/admin/jobs/{failed.id}/retry", headers=headers) for _ in range(2))
    )
    assert sorted(r.status_code for r in responses) == [200, 409]
    async with sessionmaker() as session:
        queued = (await session.scalars(select(Job).where(Job.state == "queued"))).all()
    assert len(queued) == 1


async def test_cancel_tells_the_holder_and_is_not_undone_by_scanning(
    admin_client, idp, factory, sessionmaker
):
    follower, credential = await factory.follower()
    lease = uuid.uuid4()
    job = await factory.job(state="leased", lease_id=lease, leased_by=follower.id, attempts=1)
    response = await post(admin_client, idp, f"/v1/admin/jobs/{job.id}/cancel", "operator")
    assert (response.status_code, response.json()["state"], response.json()["cancelled_by"]) == (
        200,
        "cancelled",
        actor(idp, "operator"),
    )
    heartbeat = await admin_client.post(
        f"/v1/jobs/{job.id}/heartbeat",
        headers={"Authorization": f"Bearer {credential}"},
        json={"lease_id": str(lease)},
    )
    assert heartbeat.json() == {"directive": "cancel"}
    again = await post(admin_client, idp, f"/v1/admin/jobs/{job.id}/cancel", "operator")
    assert again.status_code == 200


@pytest.mark.parametrize("state", ["completed", "failed"])
async def test_a_finished_job_cannot_be_cancelled_or_reprioritised(
    admin_client, idp, factory, state
):
    job = await factory.job(state=state)
    cancel = await post(admin_client, idp, f"/v1/admin/jobs/{job.id}/cancel", "operator")
    priority = await post(
        admin_client, idp, f"/v1/admin/jobs/{job.id}/priority", "operator", {"priority": 5}
    )
    assert [(r.status_code, r.json()["code"]) for r in (cancel, priority)] == [
        (409, "not_open"),
        (409, "not_open"),
    ]


async def test_priority_changes_the_claim_order(admin_client, idp, factory, sessionmaker):
    location = await factory.location()
    await factory.job(await factory.recording(location, key="talks/a.mp3"))
    later = await factory.job(await factory.recording(location, key="talks/b.mp3"))
    response = await post(
        admin_client, idp, f"/v1/admin/jobs/{later.id}/priority", "operator", {"priority": 10}
    )
    assert response.json()["priority"] == 10
    follower, _ = await factory.follower()
    async with sessionmaker() as session:
        claimed = await store.claim(session, follower, now=utcnow(), lease_seconds=120)
        await session.commit()
    assert claimed.id == later.id
    (entry,) = await audit_rows(sessionmaker, "job.priority")
    assert entry.detail == {"from": 0, "to": 10}


@pytest.mark.parametrize("priority", [1001, -1001, "high"])
async def test_priority_is_bounded(admin_client, idp, factory, priority):
    job = await factory.job()
    response = await post(
        admin_client, idp, f"/v1/admin/jobs/{job.id}/priority", "operator", {"priority": priority}
    )
    assert response.status_code == 422


async def test_an_unknown_job_is_404(admin_client, idp):
    response = await post(admin_client, idp, f"/v1/admin/jobs/{uuid.uuid4()}/cancel", "operator")
    assert response.status_code == 404


# --- followers ------------------------------------------------------------------


async def test_drain_lets_the_follower_finish_and_take_no_more(admin_client, idp, factory):
    follower, credential = await factory.follower()
    await factory.job()
    response = await post(admin_client, idp, f"/v1/admin/followers/{follower.id}/drain", "operator")
    assert response.json()["state"] == "draining"
    claim = await admin_client.post(
        "/v1/jobs/claim", headers={"Authorization": f"Bearer {credential}"}
    )
    assert claim.status_code == 204


async def test_revoke_releases_leases_at_once_and_shuts_the_follower_out(
    admin_app, admin_client, idp, factory, sessionmaker, tmp_path
):
    follower, credential = await factory.follower()
    location = await factory.location()
    lease = uuid.uuid4()
    job = await factory.job(
        await factory.recording(location),
        state="leased",
        lease_id=lease,
        leased_by=follower.id,
        attempts=1,
    )
    upload = admin_app.state.backend_factory(location).upload_link(
        "transcripts/talks/one.mp3.txt",
        timedelta(minutes=5),
        job_id=str(job.id),
        lease_id=str(lease),
    )
    response = await post(admin_client, idp, f"/v1/admin/followers/{follower.id}/revoke", "admin")
    assert response.json() == {"id": str(follower.id), "state": "revoked", "released": 1}
    async with sessionmaker() as session:
        stored = await session.get(Job, job.id)
    assert (stored.state, stored.lease_id, stored.attempts) == ("queued", None, 0)
    late = await admin_client.put(upload.url, content=b"late output")
    assert (late.status_code, late.json()["code"]) == (409, "stale_lease")
    assert not (tmp_path / "transcripts" / "talks" / "one.mp3.txt").exists()
    claim = await admin_client.post(
        "/v1/jobs/claim", headers={"Authorization": f"Bearer {credential}"}
    )
    assert claim.status_code == 403
    (entry,) = await audit_rows(sessionmaker, "follower.revoke")
    assert entry.detail == {"released": 1}


async def test_a_revoked_follower_cannot_be_drained(admin_client, idp, factory):
    follower, _ = await factory.follower(state="revoked")
    response = await post(admin_client, idp, f"/v1/admin/followers/{follower.id}/drain", "operator")
    assert (response.status_code, response.json()["code"]) == (409, "revoked")


# --- join tokens ----------------------------------------------------------------


async def test_a_created_token_registers_a_follower_once_and_is_never_logged(
    admin_client, idp, sessionmaker, caplog
):
    with caplog.at_level("DEBUG"):
        response = await post(
            admin_client,
            idp,
            "/v1/admin/tokens",
            "admin",
            {"pool": "gpu", "expires_in_seconds": 3600, "max_uses": 1},
        )
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["token"] not in caplog.text
    register = {"join_token": created["token"], "protocol_version": 1, "capabilities": CAPABILITIES}
    first = await admin_client.post("/v1/followers/register", json=register)
    second = await admin_client.post("/v1/followers/register", json=register)
    assert (first.status_code, second.status_code) == (200, 401)
    async with sessionmaker() as session:
        row = await session.get(JoinToken, uuid.UUID(created["id"]))
    assert (row.pool, row.created_by) == ("gpu", actor(idp, "admin"))
    (entry,) = await audit_rows(sessionmaker, "token.create")
    assert created["token"] not in f"{entry.actor} {entry.detail}"


async def test_a_revoked_token_registers_nothing(admin_client, idp, sessionmaker):
    token_id, plaintext = await join_token(sessionmaker, max_uses=5)
    response = await post(admin_client, idp, f"/v1/admin/tokens/{token_id}/revoke", "admin")
    assert response.json()["revoked"] is True
    register = await admin_client.post(
        "/v1/followers/register",
        json={"join_token": plaintext, "protocol_version": 1, "capabilities": CAPABILITIES},
    )
    assert register.status_code == 401


@pytest.mark.parametrize(
    "body",
    [
        {"expires_in_seconds": 59},
        {"expires_in_seconds": 90 * 86400 + 1},
        {"max_uses": 0},
        {"pool": "bad pool"},
    ],
)
async def test_invalid_tokens_are_refused(admin_client, idp, body):
    response = await post(admin_client, idp, "/v1/admin/tokens", "admin", body)
    assert response.status_code == 422


# --- every route's role boundary ------------------------------------------------

ROUTES = [
    ("GET", "/v1/admin/whoami", None, "viewer"),
    ("GET", "/v1/admin/status", None, "viewer"),
    ("GET", "/v1/admin/locations", None, "viewer"),
    ("GET", "/v1/admin/jobs", None, "viewer"),
    ("GET", "/v1/admin/followers", None, "viewer"),
    ("GET", "/v1/admin/consent/report", None, "viewer"),
    ("POST", "/v1/admin/locations/{name}/ingest", None, "operator"),
    ("POST", "/v1/admin/jobs/{failed_job}/retry", None, "operator"),
    ("POST", "/v1/admin/jobs/{open_job}/cancel", None, "operator"),
    ("POST", "/v1/admin/jobs/{open_job}/priority", {"priority": 5}, "operator"),
    ("POST", "/v1/admin/followers/{follower}/drain", None, "operator"),
    ("POST", "/v1/admin/locations", {"name": "added", "root": "{root}"}, "admin"),
    ("POST", "/v1/admin/locations/{name}/disable", None, "admin"),
    ("POST", "/v1/admin/locations/{name}/enable", None, "admin"),
    ("GET", "/v1/admin/tokens", None, "admin"),
    ("POST", "/v1/admin/tokens", {"pool": "default"}, "admin"),
    ("POST", "/v1/admin/tokens/{token}/revoke", None, "admin"),
    ("POST", "/v1/admin/followers/{follower}/revoke", None, "admin"),
]
BELOW = {"viewer": None, "operator": "viewer", "admin": "operator"}


@pytest.fixture
async def world(factory, sessionmaker, tmp_path):
    location = await factory.location(name="here")
    failed = await factory.job(
        await factory.recording(location, key="talks/a.mp3"), state="failed"
    )
    open_job = await factory.job(await factory.recording(location, key="talks/b.mp3"))
    follower, _ = await factory.follower()
    token_id, _ = await join_token(sessionmaker)
    return {
        "name": "here",
        "failed_job": failed.id,
        "open_job": open_job.id,
        "follower": follower.id,
        "token": token_id,
        "root": str(tmp_path),
    }


@pytest.mark.parametrize(
    "method, path, body, role", ROUTES, ids=[f"{m} {p}" for m, p, _, _ in ROUTES]
)
async def test_each_admin_route_is_refused_for_the_role_below_it(
    admin_client, idp, world, sessionmaker, method, path, body, role
):
    path = path.format(**world)
    if body is not None:
        body = {k: v.format(**world) if isinstance(v, str) else v for k, v in body.items()}
    refused = await admin_client.request(method, path, headers=idp.bearer(BELOW[role]), json=body)
    assert (refused.status_code, refused.json()["code"]) == (403, "forbidden")
    async with sessionmaker() as session:
        changes = (
            await session.scalars(
                select(AuditEntry).where(
                    AuditEntry.action != "admin.refused", AuditEntry.actor != "test"
                )
            )
        ).all()
    assert changes == []  # the refused call changed and recorded nothing else
    allowed = await admin_client.request(method, path, headers=idp.bearer(role), json=body)
    assert allowed.status_code < 400, allowed.text


async def test_every_admin_route_is_in_the_role_table(admin_app):
    def shape(path: str) -> str:
        return re.sub(r"\{[^}]+\}", "{}", path)

    served = {
        (method, shape(route.path))
        for route in admin_app.routes
        if getattr(route, "path", "").startswith("/v1/admin/")
        for method in route.methods
    }
    table = {(method, shape(path)) for method, path, _, _ in ROUTES}
    assert served - {("GET", "/v1/admin/login-config")} == table
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest packages/leader/tests/test_admin_api.py -v`
Expected: the new tests FAIL (`404`/`405` for the change routes; the role table finds routes missing).

- [ ] **Step 3: A key validator usable on its own**

In `packages/leader/src/swarmscribe_leader/storage/local.py`, add this module-level function above `class LocalBackend`:

```python
def validate_key(key: str) -> None:
    """Refuse a key that is empty, absolute, non-canonical, holds control characters, or
    has a part that could escape its folder or misbehave on Windows."""
    if not key or key.startswith("/") or "\\" in key or ":" in key:
        raise StorageError(f"invalid storage key {key!r}")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in key):
        raise StorageError(f"invalid storage key {key!r}")
    if key != PurePosixPath(key).as_posix():
        raise StorageError(f"invalid storage key {key!r}")
    for segment in key.split("/"):
        if segment in ("", ".", "..") or segment.endswith((".", " ")):
            raise StorageError(f"invalid storage key {key!r}")
```

and replace `path_for` with:

```python
    def path_for(self, key: str) -> Path:
        validate_key(key)
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise StorageError(f"invalid storage key {key!r}")
        return path
```

- [ ] **Step 4: Location changes**

`packages/leader/src/swarmscribe_leader/ingest/locations.py`:

```python
"""Storage locations as administrators change them. Each function audits its change; the
caller commits."""

import asyncio
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit
from ..db.models import StorageLocation
from ..errors import Conflict, LeaderError, NotFound


async def _by_name(session: AsyncSession, name: str) -> StorageLocation:
    location = await session.scalar(
        select(StorageLocation)
        .where(StorageLocation.name == name)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if location is None:
        raise NotFound(f"no location named {name!r}")
    return location


async def add_location(
    session: AsyncSession,
    *,
    name: str,
    root: str,
    input_prefix: str,
    output_prefix: str,
    pool: str,
    required_device: str,
    scan_interval_s: int,
    actor: str,
) -> StorageLocation:
    """A local-folder location (Azure and GCS arrive with Plan B). The folder must be
    visible to this replica, which suggests every replica mounts it at the same path."""
    if not await asyncio.to_thread(Path(root).is_dir):
        raise LeaderError(
            f"{root!r} is not a folder this leader can see; every replica must mount it there",
            code="root_unavailable",
        )
    location = StorageLocation(
        id=uuid.uuid4(),
        name=name,
        backend="local",
        config={"root": root},
        input_prefix=input_prefix,
        output_prefix=output_prefix,
        pool=pool,
        required_device=required_device,
        scan_interval_s=scan_interval_s,
        enabled=True,
        vocabulary_version=0,
    )
    session.add(location)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise Conflict(f"a location named {name!r} already exists", code="exists") from exc
    await session.refresh(location)
    audit.record(
        session,
        actor=actor,
        action="location.add",
        subject_type="location",
        subject_id=location.id,
        detail={"name": name, "backend": "local", "root": root, "pool": pool},
    )
    return location


async def set_enabled(
    session: AsyncSession, name: str, enabled: bool, *, actor: str
) -> StorageLocation:
    """Disabling stops scanning; jobs already queued stay claimable."""
    location = await _by_name(session, name)
    location.enabled = enabled
    audit.record(
        session,
        actor=actor,
        action="location.enable" if enabled else "location.disable",
        subject_type="location",
        subject_id=location.id,
    )
    return location


async def request_scan(
    session: AsyncSession, name: str, *, now: datetime, actor: str
) -> StorageLocation:
    """Ask the scanner to scan this location on its next tick (see scan_due_locations)."""
    location = await _by_name(session, name)
    if not location.enabled:
        raise Conflict(f"location {name!r} is disabled; enable it first", code="disabled")
    location.scan_requested_at = now
    audit.record(
        session,
        actor=actor,
        action="location.ingest",
        subject_type="location",
        subject_id=location.id,
    )
    return location
```

- [ ] **Step 5: Job changes**

`packages/leader/src/swarmscribe_leader/jobs/admin.py`:

```python
"""Administrators' changes to jobs. Each function audits its change; the caller commits."""

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit
from ..db.models import Job, Recording, StorageLocation
from ..errors import Conflict, NotFound
from .store import OPEN_STATES, cancel

ADMIN_CANCEL_REASON = "cancelled by an administrator"


async def _locked(session: AsyncSession, job_id: uuid.UUID) -> Job:
    job = await session.get(Job, job_id, with_for_update=True, populate_existing=True)
    if job is None:
        raise NotFound("no such job")
    return job


async def retry_job(
    session: AsyncSession, job_id: uuid.UUID, *, actor: str, max_attempts: int
) -> Job:
    """Queue a failed or cancelled job's recording again, as a new job. Consent is checked
    again here: retry can never queue a recording its location's consent.txt does not match.
    The old job's row lock serialises two retries of the same job."""
    job = await _locked(session, job_id)
    if job.state not in ("failed", "cancelled"):
        raise Conflict(
            f"the job is {job.state}; only failed or cancelled jobs can be retried",
            code="not_retryable",
        )
    recording = await session.get(
        Recording, job.recording_id, with_for_update=True, populate_existing=True
    )
    if recording.consent != "consented" or recording.missing:
        raise Conflict(
            "the recording is not consented or is no longer present", code="not_consented"
        )
    if recording.source_version != job.source_version:
        raise Conflict(
            "the recording changed after this job; the next scan queues the new version",
            code="recording_changed",
        )
    open_job = await session.scalar(
        select(Job.id)
        .where(Job.recording_id == recording.id, Job.state.in_(OPEN_STATES))
        .limit(1)
    )
    if open_job is not None:
        raise Conflict("the recording already has a queued or leased job", code="already_open")
    location = await session.get(StorageLocation, recording.location_id)
    retry = Job(
        id=uuid.uuid4(),
        recording_id=recording.id,
        source_version=recording.source_version,
        state="queued",
        pool=location.pool,
        required_device=location.required_device,
        priority=job.priority,
        attempts=0,
        max_attempts=max_attempts,
    )
    session.add(retry)
    audit.record(
        session,
        actor=actor,
        action="job.retry",
        subject_type="job",
        subject_id=retry.id,
        detail={"retry_of": str(job.id)},
    )
    return retry


async def cancel_job(
    session: AsyncSession, job_id: uuid.UUID, *, now: datetime, actor: str
) -> Job:
    """Cancel a queued or leased job (the holder hears `cancel` on its next heartbeat).
    Cancelling a cancelled job changes nothing."""
    job = await _locked(session, job_id)
    if job.state not in (*OPEN_STATES, "cancelled"):
        raise Conflict(
            f"the job is {job.state}; only queued or leased jobs can be cancelled",
            code="not_open",
        )
    if job.state != "cancelled":
        await cancel(session, job, now=now, reason=ADMIN_CANCEL_REASON, by=actor)
    audit.record(session, actor=actor, action="job.cancel", subject_type="job", subject_id=job.id)
    return job


async def set_priority(
    session: AsyncSession, job_id: uuid.UUID, priority: int, *, actor: str
) -> Job:
    job = await _locked(session, job_id)
    if job.state not in OPEN_STATES:
        raise Conflict(
            f"the job is {job.state}; only queued or leased jobs can be reprioritised",
            code="not_open",
        )
    previous = job.priority
    job.priority = priority
    audit.record(
        session,
        actor=actor,
        action="job.priority",
        subject_type="job",
        subject_id=job.id,
        detail={"from": previous, "to": priority},
    )
    return job
```

- [ ] **Step 6: Follower and token changes**

In `packages/leader/src/swarmscribe_leader/auth/followers.py`, change the errors import to `from ..errors import Conflict, Forbidden, NotFound, Unauthorized`, add `from ..jobs.store import release_all`, and append:

```python
async def _locked_follower(session: AsyncSession, follower_id: uuid.UUID) -> Follower:
    follower = await session.get(
        Follower, follower_id, with_for_update=True, populate_existing=True
    )
    if follower is None:
        raise NotFound("no such follower")
    return follower


async def drain(session: AsyncSession, follower_id: uuid.UUID, *, actor: str) -> Follower:
    """The follower finishes what it holds and takes nothing new."""
    follower = await _locked_follower(session, follower_id)
    if follower.state == "revoked":
        raise Conflict("a revoked follower cannot be drained", code="revoked")
    follower.state = "draining"
    audit.record(
        session,
        actor=actor,
        action="follower.drain",
        subject_type="follower",
        subject_id=follower.id,
    )
    return follower


async def revoke_follower(
    session: AsyncSession, follower_id: uuid.UUID, *, now: datetime, actor: str
) -> tuple[Follower, int]:
    """Every further call from the follower is refused, and its leases are released at once
    (so its upload links stop working). Returns the follower and the number released."""
    follower = await _locked_follower(session, follower_id)
    released = await release_all(session, follower, now=now)
    follower.state = "revoked"
    audit.record(
        session,
        actor=actor,
        action="follower.revoke",
        subject_type="follower",
        subject_id=follower.id,
        detail={"released": released},
    )
    return follower, released


async def revoke_token(session: AsyncSession, token_id: uuid.UUID, *, actor: str) -> JoinToken:
    token = await session.get(JoinToken, token_id, with_for_update=True, populate_existing=True)
    if token is None:
        raise NotFound("no such join token")
    token.revoked = True
    audit.record(
        session,
        actor=actor,
        action="token.revoke",
        subject_type="join_token",
        subject_id=token.id,
    )
    return token
```

- [ ] **Step 7: The change models**

In `packages/leader/src/swarmscribe_leader/api/admin_models.py`, replace the import block with:

```python
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..storage.base import StorageError
from ..storage.local import validate_key
```

and append:

```python
class LocationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=NAME_PATTERN)
    root: str = Field(min_length=1, max_length=1000)
    input_prefix: str = Field(default="", max_length=1000)
    output_prefix: str = Field(default="transcripts/", max_length=1000)
    pool: str = Field(default="default", pattern=NAME_PATTERN)
    required_device: RequiredDevice = "any"
    scan_interval_s: int = Field(default=900, ge=30, le=7 * 86400)

    @field_validator("root")
    @classmethod
    def _absolute_folder(cls, value: str) -> str:
        if any(ord(ch) < 0x20 for ch in value) or not Path(value).is_absolute():
            raise ValueError("root must be an absolute folder path")
        return value

    @field_validator("input_prefix", "output_prefix")
    @classmethod
    def _relative_prefix(cls, value: str) -> str:
        if value:
            try:
                validate_key(value.rstrip("/"))
            except StorageError:
                raise ValueError("must be a relative folder such as 'incoming/'") from None
        return value


class PriorityIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    priority: int = Field(ge=-1000, le=1000, strict=True)


class TokenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pool: str = Field(default="default", pattern=NAME_PATTERN)
    expires_in_seconds: int = Field(default=7 * 86400, ge=60, le=90 * 86400)
    max_uses: int = Field(default=1, ge=1, le=10_000)


class TokenCreated(BaseModel):
    id: str
    token: str
    pool: str
    expires_at: datetime
    max_uses: int


class ScanRequested(BaseModel):
    name: str
    requested_at: datetime


class FollowerRevoked(BaseModel):
    id: str
    state: str
    released: int
```

- [ ] **Step 8: The change routes**

In `packages/leader/src/swarmscribe_leader/api/admin.py`, replace the import block with:

```python
import uuid
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit, reports
from ..auth import followers
from ..auth.followers import create_join_token
from ..auth.oidc import login_providers
from ..clock import utcnow
from ..ingest import locations
from ..jobs import admin as job_admin
from .admin_auth import Admin, require
from .admin_models import (
    ConsentReport,
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
from .deps import db_session, settings_of
```

and append:

```python
@router.post("/locations", response_model=LocationOut, status_code=201)
async def add_location(body: LocationIn, admin: Administrator, session: Session) -> LocationOut:
    location = await locations.add_location(session, **body.model_dump(), actor=admin.actor)
    view = reports.location_view(location)
    await session.commit()
    return LocationOut.model_validate(view)


async def _set_enabled(session: AsyncSession, name: str, enabled: bool, admin: Admin):
    location = await locations.set_enabled(session, name, enabled, actor=admin.actor)
    view = reports.location_view(location)
    await session.commit()
    return LocationOut.model_validate(view)


@router.post("/locations/{name}/disable", response_model=LocationOut)
async def disable_location(name: str, admin: Administrator, session: Session) -> LocationOut:
    return await _set_enabled(session, name, False, admin)


@router.post("/locations/{name}/enable", response_model=LocationOut)
async def enable_location(name: str, admin: Administrator, session: Session) -> LocationOut:
    return await _set_enabled(session, name, True, admin)


@router.post("/locations/{name}/ingest", response_model=ScanRequested, status_code=202)
async def ingest_location(name: str, admin: Operator, session: Session) -> ScanRequested:
    location = await locations.request_scan(session, name, now=utcnow(), actor=admin.actor)
    answer = ScanRequested(name=location.name, requested_at=location.scan_requested_at)
    await session.commit()
    return answer


@router.post("/jobs/{job_id}/retry", response_model=JobOut)
async def retry_job(
    job_id: uuid.UUID, request: Request, admin: Operator, session: Session
) -> JobOut:
    job = await job_admin.retry_job(
        session, job_id, actor=admin.actor, max_attempts=settings_of(request).max_attempts
    )
    view = await reports.describe_job(session, job)
    await session.commit()
    return JobOut.model_validate(view)


@router.post("/jobs/{job_id}/cancel", response_model=JobOut)
async def cancel_job(job_id: uuid.UUID, admin: Operator, session: Session) -> JobOut:
    job = await job_admin.cancel_job(session, job_id, now=utcnow(), actor=admin.actor)
    view = await reports.describe_job(session, job)
    await session.commit()
    return JobOut.model_validate(view)


@router.post("/jobs/{job_id}/priority", response_model=JobOut)
async def set_priority(
    job_id: uuid.UUID, body: PriorityIn, admin: Operator, session: Session
) -> JobOut:
    job = await job_admin.set_priority(session, job_id, body.priority, actor=admin.actor)
    view = await reports.describe_job(session, job)
    await session.commit()
    return JobOut.model_validate(view)


@router.post("/followers/{follower_id}/drain", response_model=FollowerOut)
async def drain_follower(follower_id: uuid.UUID, admin: Operator, session: Session) -> FollowerOut:
    follower = await followers.drain(session, follower_id, actor=admin.actor)
    view = await reports.describe_follower(session, follower)
    await session.commit()
    return FollowerOut.model_validate(view)


@router.post("/followers/{follower_id}/revoke", response_model=FollowerRevoked)
async def revoke_follower(
    follower_id: uuid.UUID, admin: Administrator, session: Session
) -> FollowerRevoked:
    follower, released = await followers.revoke_follower(
        session, follower_id, now=utcnow(), actor=admin.actor
    )
    await session.commit()
    return FollowerRevoked(id=str(follower.id), state=follower.state, released=released)


@router.post("/tokens", response_model=TokenCreated, status_code=201)
async def create_token(body: TokenIn, admin: Administrator, session: Session) -> TokenCreated:
    """The only response that carries a join token. Nothing logs response bodies."""
    token, plaintext = await create_join_token(
        session,
        pool=body.pool,
        expires_at=utcnow() + timedelta(seconds=body.expires_in_seconds),
        max_uses=body.max_uses,
        created_by=admin.actor,
    )
    await session.commit()
    return TokenCreated(
        id=str(token.id),
        token=plaintext,
        pool=token.pool,
        expires_at=token.expires_at,
        max_uses=token.max_uses,
    )


@router.post("/tokens/{token_id}/revoke", response_model=TokenOut)
async def revoke_token(token_id: uuid.UUID, admin: Administrator, session: Session) -> TokenOut:
    token = await followers.revoke_token(session, token_id, actor=admin.actor)
    view = reports.token_view(token)
    await session.commit()
    return TokenOut.model_validate(view)
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `uv run pytest packages/leader -q`
Expected: PASS, including all 18 role-boundary cases and the route-table check.

- [ ] **Step 10: Commit**

```bash
git add packages/leader/src/swarmscribe_leader packages/leader/tests/test_admin_api.py
git commit -m "Leader: admin changes (locations, ingest, jobs, followers, tokens), each audited and role-checked

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: CLI sign-in — device-code flow, token cache, refresh

**Files:**
- Create: `packages/leader/src/swarmscribe_leader/admin_cli/__init__.py`
- Create: `packages/leader/src/swarmscribe_leader/admin_cli/credentials.py`
- Create: `packages/leader/src/swarmscribe_leader/admin_cli/device_flow.py`
- Test: `packages/leader/tests/test_admin_cli_auth.py`

**Interfaces:**
- Produces (in `admin_cli/credentials.py`):
  - `PATH_ENV = "SWARMSCRIBE_ADMIN_CREDENTIALS"`; `def default_path() -> Path` (`~/.config/swarmscribe/credentials.json` unless overridden).
  - `@dataclass(frozen=True) class SignIn: leader: str; provider: str; client_id: str; token_endpoint: str; scope: str; id_token: str; refresh_token: str | None = None; client_secret: str | None = None` (the three secrets are hidden from `repr`).
  - `class CredentialStore(path: Path | None = None)` with `load(leader) -> SignIn | None`, `save(sign_in) -> None` (also makes it the default leader), `remove(leader) -> bool`, `default_leader() -> str | None`. File format `{"default_leader": url, "leaders": {url: SignIn fields}}`. POSIX: file `0600`, a folder it creates `0700`, a looser file is tightened on read.
- Produces (in `admin_cli/device_flow.py`):
  - `class SignInError(Exception)`; `@dataclass(frozen=True) class ProviderConfig: name, client_id, device_authorization_endpoint, token_endpoint, scope, client_secret: str | None = None`; `@dataclass(frozen=True) class TokenSet: id_token: str; refresh_token: str | None; expires_at: float`.
  - `async def device_sign_in(http: httpx.AsyncClient, provider: ProviderConfig, *, prompt: Callable[[str], object], sleep=asyncio.sleep, clock=time.time) -> TokenSet` (RFC 8628: `authorization_pending`, `slow_down` +5 s, `access_denied`/`authorization_declined`, `expired_token`; Entra's `verification_uri` or Google's `verification_url`; the client secret is sent only when the provider has one).
  - `async def refresh_tokens(http, *, token_endpoint, client_id, refresh_token, scope="", client_secret=None, clock=time.time) -> TokenSet`.
  - `def id_token_expiry(token: str) -> float | None` (reads `exp` without verifying).
- The CLI package imports nothing from the leader's server modules.

- [ ] **Step 1: Write the failing tests**

`packages/leader/tests/test_admin_cli_auth.py`:

```python
import os
import stat
from pathlib import Path
from urllib.parse import parse_qsl

import httpx
import pytest
import swarmscribe_leader.admin_cli
from swarmscribe_leader.admin_cli.credentials import CredentialStore, SignIn
from swarmscribe_leader.admin_cli.device_flow import (
    ProviderConfig,
    SignInError,
    device_sign_in,
    id_token_expiry,
    refresh_tokens,
)

ENTRA = ProviderConfig(
    name="entra",
    client_id="entra-client",
    device_authorization_endpoint="https://login.microsoftonline.com/t/oauth2/v2.0/devicecode",
    token_endpoint="https://login.microsoftonline.com/t/oauth2/v2.0/token",
    scope="openid profile email offline_access",
)
GOOGLE = ProviderConfig(
    name="google",
    client_id="google-client",
    device_authorization_endpoint="https://oauth2.googleapis.com/device/code",
    token_endpoint="https://oauth2.googleapis.com/token",
    scope="openid email profile",
    client_secret="google-device-secret",
)


class ScriptedProvider:
    """A provider's device-code endpoint and a scripted series of token-endpoint answers."""

    def __init__(self, answers, **start):
        self.answers = list(answers)
        self.start = {
            "device_code": "device-code-1",
            "user_code": "WDJB-MJHT",
            "verification_uri": "https://microsoft.com/devicelogin",
            "expires_in": 900,
            "interval": 5,
            **start,
        }
        self.token_forms: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(("/devicecode", "/device/code")):
            return httpx.Response(200, json=self.start)
        self.token_forms.append(dict(parse_qsl(request.content.decode())))
        status, body = self.answers.pop(0)
        return httpx.Response(status, json=body)


async def sign_in(config, provider, **kwargs):
    sleeps: list[float] = []
    prompts: list[str] = []

    async def sleep(seconds):
        sleeps.append(seconds)

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
        tokens = await device_sign_in(http, config, prompt=prompts.append, sleep=sleep, **kwargs)
    return tokens, sleeps, prompts


async def test_entra_sign_in_waits_while_pending_and_slows_down_when_told(idp):
    token = idp.entra()
    provider = ScriptedProvider(
        [
            (400, {"error": "authorization_pending"}),
            (400, {"error": "slow_down"}),
            (200, {"id_token": token, "refresh_token": "refresh-1", "expires_in": 3600}),
        ]
    )
    tokens, sleeps, prompts = await sign_in(ENTRA, provider)
    assert (tokens.id_token, tokens.refresh_token) == (token, "refresh-1")
    assert tokens.expires_at == id_token_expiry(token)
    assert sleeps == [5, 5, 10]
    assert prompts == [
        "To sign in, open https://microsoft.com/devicelogin and enter the code WDJB-MJHT"
    ]
    form = provider.token_forms[0]
    assert form == {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "client_id": "entra-client",
        "device_code": "device-code-1",
    }


async def test_google_sign_in_uses_its_verification_url_and_client_secret(idp):
    provider = ScriptedProvider(
        [(200, {"id_token": idp.google(), "refresh_token": "refresh-1"})],
        verification_uri=None,
        verification_url="https://www.google.com/device",
    )
    _tokens, _sleeps, prompts = await sign_in(GOOGLE, provider)
    assert "https://www.google.com/device" in prompts[0]
    assert provider.token_forms[0]["client_secret"] == "google-device-secret"


@pytest.mark.parametrize(
    "answer, message",
    [
        ((400, {"error": "access_denied"}), "declined"),
        ((400, {"error": "authorization_declined"}), "declined"),
        ((400, {"error": "expired_token"}), "expired"),
        ((400, {"error": "invalid_client"}), "invalid_client"),
    ],
)
async def test_sign_in_failures_say_what_happened_without_the_device_code(answer, message):
    with pytest.raises(SignInError, match=message) as excinfo:
        await sign_in(ENTRA, ScriptedProvider([answer]))
    assert "device-code-1" not in str(excinfo.value)


async def test_a_code_that_runs_out_of_time_is_an_error():
    with pytest.raises(SignInError, match="expired"):
        await sign_in(ENTRA, ScriptedProvider([], expires_in=0))


async def test_a_provider_that_will_not_start_a_sign_in_is_an_error():
    def refuse(request):
        return httpx.Response(400, json={"error": "unauthorized_client"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(refuse)) as http:
        with pytest.raises(SignInError, match="unauthorized_client"):
            await device_sign_in(http, ENTRA, prompt=print)


async def test_refresh_returns_new_tokens_and_sends_what_the_provider_needs(idp):
    provider = ScriptedProvider([(200, {"id_token": idp.google()})])
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
        tokens = await refresh_tokens(
            http,
            token_endpoint=GOOGLE.token_endpoint,
            client_id="google-client",
            refresh_token="refresh-1",
            client_secret="google-device-secret",
        )
    assert tokens.refresh_token is None  # Google keeps the old refresh token valid
    assert provider.token_forms == [
        {
            "grant_type": "refresh_token",
            "client_id": "google-client",
            "refresh_token": "refresh-1",
            "client_secret": "google-device-secret",
        }
    ]


async def test_a_refused_refresh_asks_for_a_new_login():
    provider = ScriptedProvider([(400, {"error": "invalid_grant"})])
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
        with pytest.raises(SignInError, match="run `swarmscribe-admin login`") as excinfo:
            await refresh_tokens(
                http, token_endpoint=ENTRA.token_endpoint, client_id="c", refresh_token="r-9"
            )
    assert "r-9" not in str(excinfo.value)


def test_id_token_expiry(idp):
    assert id_token_expiry(idp.entra(exp=2_000_000_000)) == 2_000_000_000
    assert id_token_expiry("not-a-token") is None


def sign_in_record(leader="https://leader.example.org", **overrides) -> SignIn:
    values = {
        "leader": leader,
        "provider": "entra",
        "client_id": "entra-client",
        "token_endpoint": ENTRA.token_endpoint,
        "scope": ENTRA.scope,
        "id_token": "id-token-secret",
        "refresh_token": "refresh-token-secret",
    }
    values.update(overrides)
    return SignIn(**values)


def test_the_store_keeps_one_sign_in_per_leader(tmp_path):
    store = CredentialStore(tmp_path / "config" / "credentials.json")
    assert store.load("https://leader.example.org") is None
    store.save(sign_in_record())
    store.save(sign_in_record("https://other.example.org", provider="google"))
    assert store.load("https://leader.example.org") == sign_in_record()
    assert store.default_leader() == "https://other.example.org"
    assert store.remove("https://other.example.org") is True
    assert store.default_leader() is None
    assert store.load("https://leader.example.org") == sign_in_record()


def test_a_corrupt_cache_reads_as_signed_out(tmp_path):
    path = tmp_path / "credentials.json"
    path.write_text("{not json", encoding="utf-8")
    assert CredentialStore(path).load("https://leader.example.org") is None


def test_tokens_are_not_in_the_repr():
    shown = repr(sign_in_record(client_secret="client-secret-value"))
    for secret in ("id-token-secret", "refresh-token-secret", "client-secret-value"):
        assert secret not in shown


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_the_cache_is_readable_by_its_owner_only(tmp_path):
    path = tmp_path / "config" / "credentials.json"
    CredentialStore(path).save(sign_in_record())
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_a_cache_left_readable_by_others_is_tightened(tmp_path):
    path = tmp_path / "credentials.json"
    store = CredentialStore(path)
    store.save(sign_in_record())
    os.chmod(path, 0o644)
    assert store.load("https://leader.example.org") is not None
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_the_cli_imports_nothing_from_the_server():
    folder = Path(swarmscribe_leader.admin_cli.__file__).parent
    for path in folder.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "from .." not in text, path.name
        assert "import swarmscribe_leader" not in text, path.name
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest packages/leader/tests/test_admin_cli_auth.py -v`
Expected: FAIL — `swarmscribe_leader.admin_cli` does not exist.

- [ ] **Step 3: The package and the credentials cache**

`packages/leader/src/swarmscribe_leader/admin_cli/__init__.py`:

```python
"""swarmscribe-admin: the administrators' command-line client. It talks to a leader over
HTTPS only and imports nothing from the leader's server modules."""
```

`packages/leader/src/swarmscribe_leader/admin_cli/credentials.py`:

```python
"""The sign-in cache: ~/.config/swarmscribe/credentials.json, readable by its owner only.

It holds ID and refresh tokens, so nothing here prints them, and their repr is hidden.
On Windows the file relies on the user-profile folder's permissions (POSIX modes do not
apply there)."""

import json
import os
import stat
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

PATH_ENV = "SWARMSCRIBE_ADMIN_CREDENTIALS"


def default_path() -> Path:
    override = os.environ.get(PATH_ENV)
    if override:
        return Path(override)
    return Path.home() / ".config" / "swarmscribe" / "credentials.json"


@dataclass(frozen=True)
class SignIn:
    leader: str
    provider: str
    client_id: str
    token_endpoint: str
    scope: str
    id_token: str = field(repr=False)
    refresh_token: str | None = field(default=None, repr=False)
    client_secret: str | None = field(default=None, repr=False)


class CredentialStore:
    def __init__(self, path: Path | None = None):
        self.path = path or default_path()

    def _read(self) -> dict[str, Any]:
        empty: dict[str, Any] = {"leaders": {}}
        try:
            if os.name != "nt" and stat.S_IMODE(self.path.stat().st_mode) & 0o077:
                os.chmod(self.path, 0o600)
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return empty  # absent or unreadable: signed out
        if not isinstance(data, dict) or not isinstance(data.get("leaders"), dict):
            return empty
        return data

    def _write(self, data: dict[str, Any]) -> None:
        folder = self.path.parent
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)  # the mode applies if created
        temp = folder / f".{self.path.name}.{os.getpid()}.tmp"
        descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
        os.replace(temp, self.path)
        if os.name != "nt":
            os.chmod(self.path, 0o600)

    def load(self, leader: str) -> SignIn | None:
        entry = self._read()["leaders"].get(leader)
        if not isinstance(entry, dict):
            return None
        try:
            return SignIn(**entry)
        except TypeError:
            return None

    def save(self, sign_in: SignIn) -> None:
        data = self._read()
        data["leaders"][sign_in.leader] = asdict(sign_in)
        data["default_leader"] = sign_in.leader
        self._write(data)

    def remove(self, leader: str) -> bool:
        data = self._read()
        removed = data["leaders"].pop(leader, None) is not None
        if data.get("default_leader") == leader:
            del data["default_leader"]
        self._write(data)
        return removed

    def default_leader(self) -> str | None:
        value = self._read().get("default_leader")
        return value if isinstance(value, str) else None
```

- [ ] **Step 4: The device-code flow and refresh**

`packages/leader/src/swarmscribe_leader/admin_cli/device_flow.py`:

```python
"""OAuth 2.0 device authorization grant (RFC 8628) against Entra ID or Google, and token
refresh. Nothing here prints a token or the device code; only the user code is shown."""

import asyncio
import base64
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
SLOW_DOWN_SECONDS = 5


class SignInError(Exception):
    """Signing in did not work; the message says what to do next."""


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    client_id: str
    device_authorization_endpoint: str
    token_endpoint: str
    scope: str
    client_secret: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class TokenSet:
    id_token: str = field(repr=False)
    refresh_token: str | None = field(repr=False)
    expires_at: float


def id_token_expiry(token: str) -> float | None:
    """The token's `exp`, read without verifying it: the leader verifies, the CLI only needs
    to know when to refresh."""
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return float(claims["exp"])
    except (IndexError, KeyError, TypeError, ValueError):
        return None


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _token_set(body: dict[str, Any], clock: Callable[[], float]) -> TokenSet:
    id_token = body.get("id_token")
    if not isinstance(id_token, str) or not id_token:
        raise SignInError("the identity provider returned no ID token (is `openid` in scope?)")
    refresh = body.get("refresh_token")
    expires_at = id_token_expiry(id_token) or clock() + float(body.get("expires_in", 3600))
    return TokenSet(
        id_token=id_token,
        refresh_token=refresh if isinstance(refresh, str) and refresh else None,
        expires_at=expires_at,
    )


async def device_sign_in(
    http: httpx.AsyncClient,
    provider: ProviderConfig,
    *,
    prompt: Callable[[str], object],
    sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
    clock: Callable[[], float] = time.time,
) -> TokenSet:
    response = await http.post(
        provider.device_authorization_endpoint,
        data={"client_id": provider.client_id, "scope": provider.scope},
    )
    start = _json(response)
    if response.status_code != 200 or "device_code" not in start:
        reason = start.get("error") or response.status_code
        raise SignInError(f"{provider.name} would not start a sign-in ({reason})")
    uri = start.get("verification_uri") or start.get("verification_url")
    prompt(f"To sign in, open {uri} and enter the code {start.get('user_code')}")
    interval = float(start.get("interval", SLOW_DOWN_SECONDS))
    deadline = clock() + float(start.get("expires_in", 900))
    poll = {
        "grant_type": DEVICE_GRANT,
        "client_id": provider.client_id,
        "device_code": start["device_code"],
    }
    if provider.client_secret:
        poll["client_secret"] = provider.client_secret
    while clock() < deadline:
        await sleep(interval)
        response = await http.post(provider.token_endpoint, data=poll)
        body = _json(response)
        if response.status_code == 200:
            return _token_set(body, clock)
        error = body.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += SLOW_DOWN_SECONDS
            continue
        if error in ("access_denied", "authorization_declined"):
            raise SignInError("the sign-in was declined")
        if error in ("expired_token", "code_expired"):
            break
        raise SignInError(f"sign-in failed ({error or response.status_code})")
    raise SignInError("the sign-in code expired; run `swarmscribe-admin login` again")


async def refresh_tokens(
    http: httpx.AsyncClient,
    *,
    token_endpoint: str,
    client_id: str,
    refresh_token: str,
    scope: str = "",
    client_secret: str | None = None,
    clock: Callable[[], float] = time.time,
) -> TokenSet:
    form = {"grant_type": "refresh_token", "client_id": client_id, "refresh_token": refresh_token}
    if scope:
        form["scope"] = scope
    if client_secret:
        form["client_secret"] = client_secret
    response = await http.post(token_endpoint, data=form)
    if response.status_code != 200:
        raise SignInError("your sign-in has expired; run `swarmscribe-admin login` again")
    return _token_set(_json(response), clock)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest packages/leader/tests/test_admin_cli_auth.py -v`
Expected: PASS (the two POSIX tests are skipped on Windows and run in CI).

- [ ] **Step 6: Commit**

```bash
git add packages/leader/src/swarmscribe_leader/admin_cli packages/leader/tests/test_admin_cli_auth.py
git commit -m "Admin CLI: device-code sign-in, refresh, and an owner-only credentials cache

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: The `swarmscribe-admin` commands

**Files:**
- Create: `packages/leader/src/swarmscribe_leader/admin_cli/client.py`
- Create: `packages/leader/src/swarmscribe_leader/admin_cli/main.py`
- Modify: `packages/leader/pyproject.toml` (`[project.scripts]`)
- Test: `packages/leader/tests/test_admin_cli.py`

**Interfaces:**
- Consumes: `CredentialStore`, `SignIn` (Task 11); `ProviderConfig`, `device_sign_in`, `refresh_tokens`, `id_token_expiry`, `SignInError` (Task 11); the `/v1/admin` routes (Tasks 8–10).
- Produces:
  - `admin_cli/client.py`: `class CliError(Exception)`; `REFRESH_MARGIN_SECONDS = 120`; `class LeaderClient(leader, store, *, http, clock=time.time)` with `async request(method, path, *, body=None, params=None) -> Any` (refreshes the ID token when it has under 2 minutes left, retries once after `401 token_expired`, turns any error answer into `CliError("<message> (<status> <code>)")`).
  - `admin_cli/main.py`: `LEADER_ENV = "SWARMSCRIBE_LEADER_URL"`; `def parse_duration(text) -> int` (`90s`, `30m`, `12h`, `7d`); `def build_parser() -> argparse.ArgumentParser`; `async def amain(argv=None, *, transport=None, store=None, out=None, err=None) -> int` (0 success, 1 failure, 2 usage); `def main(argv=None) -> int`; `def run() -> None` (the console script).
  - Commands: `login [--provider entra|google]`, `logout`, `whoami`, `status`, `locations add NAME --root PATH [--input-prefix] [--output-prefix] [--pool] [--device any|cuda|cpu] [--scan-interval 15m]`, `locations list`, `locations disable NAME`, `locations enable NAME`, `ingest NAME`, `jobs list [--state] [--location] [--limit]`, `jobs retry ID`, `jobs cancel ID`, `jobs priority ID N`, `followers list [--state]`, `followers drain ID`, `followers revoke ID`, `tokens create [--pool] [--expires 7d] [--max-uses 1]`, `tokens list`, `tokens revoke ID`, `consent report [--location] [--limit]`. Global flags `--leader URL`, `--json`.

- [ ] **Step 1: Write the failing tests**

`packages/leader/tests/test_admin_cli.py`:

```python
import argparse
import io
import json
from urllib.parse import parse_qsl

import httpx
import pytest
from sqlalchemy import select
from swarmscribe_leader.admin_cli.credentials import CredentialStore, SignIn
from swarmscribe_leader.admin_cli.main import amain, parse_duration
from swarmscribe_leader.auth.secrets import hash_secret
from swarmscribe_leader.db.models import Follower, Job, JoinToken, StorageLocation

ENTRA_SCOPE = "openid profile email offline_access"


class Routed(httpx.AsyncBaseTransport):
    """Leader requests go to the app in-process; everything else to the fake provider."""

    def __init__(self, app, provider):
        self.leader = httpx.ASGITransport(app=app)
        self.provider = httpx.MockTransport(provider)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "leader":
            return await self.leader.handle_async_request(request)
        return await self.provider.handle_async_request(request)


class FakeEntra:
    """Entra ID's device-code and token endpoints, signing the person in as an admin."""

    def __init__(self, idp):
        self.idp = idp
        self.polls = 0
        self.refreshes = 0

    def admin_token(self) -> str:
        return self.idp.entra(
            groups=[self.idp.ENTRA_GROUPS["admin"]], sub="entra-admin", email="admin@example.org"
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/devicecode"):
            return httpx.Response(
                200,
                json={
                    "device_code": "device-code-1",
                    "user_code": "WDJB-MJHT",
                    "verification_uri": "https://microsoft.com/devicelogin",
                    "expires_in": 900,
                    "interval": 0,
                },
            )
        form = dict(parse_qsl(request.content.decode()))
        if form["grant_type"] == "refresh_token":
            self.refreshes += 1
            return httpx.Response(
                200, json={"id_token": self.admin_token(), "refresh_token": "refresh-token-2"}
            )
        self.polls += 1
        if self.polls == 1:
            return httpx.Response(400, json={"error": "authorization_pending"})
        return httpx.Response(
            200, json={"id_token": self.admin_token(), "refresh_token": "refresh-token-1"}
        )


@pytest.fixture
def store(tmp_path):
    return CredentialStore(tmp_path / "config" / "credentials.json")


@pytest.fixture
def entra(idp):
    return FakeEntra(idp)


@pytest.fixture
def cli(admin_app, store, entra):
    async def run(*argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        code = await amain(
            ["--leader", "http://leader", *argv],
            transport=Routed(admin_app, entra),
            store=store,
            out=out,
            err=err,
        )
        return code, out.getvalue(), err.getvalue()

    return run


def sign_in_as(store, idp, role, *, lifetime=3600) -> str:
    token = idp.entra(
        groups=[idp.ENTRA_GROUPS[role]],
        sub=f"entra-{role}",
        email=f"{role}@example.org",
        lifetime=lifetime,
    )
    store.save(
        SignIn(
            leader="http://leader",
            provider="entra",
            client_id=idp.ENTRA_CLIENT,
            token_endpoint=(
                f"https://login.microsoftonline.com/{idp.ENTRA_TENANT}/oauth2/v2.0/token"
            ),
            scope=ENTRA_SCOPE,
            id_token=token,
            refresh_token="refresh-token-1",
        )
    )
    return token


async def test_login_with_entra_saves_the_sign_in_and_shows_the_role(cli, store):
    code, out, err = await cli("login", "--provider", "entra")
    assert code == 0, err
    assert "open https://microsoft.com/devicelogin and enter the code WDJB-MJHT" in out
    assert "signed in as admin@example.org (admin)" in out
    saved = store.load("http://leader")
    assert (saved.provider, saved.refresh_token) == ("entra", "refresh-token-1")
    assert saved.id_token not in out
    assert store.default_leader() == "http://leader"


async def test_login_asks_which_provider_when_the_leader_accepts_both(cli):
    code, _out, err = await cli("login")
    assert code == 1
    assert "choose one with --provider" in err


async def test_a_command_before_login_says_to_sign_in(cli):
    code, out, err = await cli("status")
    assert (code, out) == (1, "")
    assert "run `swarmscribe-admin login`" in err


async def test_status(cli, store, idp, factory):
    sign_in_as(store, idp, "viewer")
    await factory.job()
    code, out, _err = await cli("status")
    assert code == 0
    assert "queued 1" in out
    assert "locations:" in out


async def test_json_output_is_the_leaders_answer(cli, store, idp):
    sign_in_as(store, idp, "viewer")
    code, out, _err = await cli("--json", "whoami")
    assert code == 0
    assert json.loads(out)["role"] == "viewer"


async def test_locations_add_list_ingest_and_disable(cli, store, idp, sessionmaker, tmp_path):
    sign_in_as(store, idp, "admin")
    code, _out, err = await cli(
        "locations",
        "add",
        "archive-1",
        "--root",
        str(tmp_path),
        "--input-prefix",
        "incoming/",
        "--scan-interval",
        "30m",
    )
    assert code == 0, err
    assert "archive-1" in (await cli("locations", "list"))[1]
    assert (await cli("ingest", "archive-1"))[1].startswith("scan requested for archive-1")
    assert (await cli("locations", "disable", "archive-1"))[0] == 0
    async with sessionmaker() as session:
        location = (await session.scalars(select(StorageLocation))).one()
    assert (location.input_prefix, location.scan_interval_s, location.enabled) == (
        "incoming/",
        1800,
        False,
    )
    assert location.scan_requested_at is not None


async def test_jobs_list_retry_priority_and_cancel(cli, store, idp, factory, sessionmaker):
    sign_in_as(store, idp, "operator")
    failed = await factory.job(state="failed", failure_reason="engine_error: x")
    queued = await factory.job()
    _code, out, _err = await cli("jobs", "list", "--state", "failed")
    assert str(failed.id) in out
    assert str(queued.id) not in out
    code, out, _err = await cli("jobs", "retry", str(failed.id))
    assert code == 0
    assert "state: queued" in out
    assert (await cli("jobs", "priority", str(queued.id), "7"))[0] == 0
    assert (await cli("jobs", "cancel", str(queued.id)))[0] == 0
    async with sessionmaker() as session:
        stored = await session.get(Job, queued.id)
    assert (stored.priority, stored.state) == (7, "cancelled")


async def test_followers_list_drain_and_revoke(cli, store, idp, factory, sessionmaker):
    sign_in_as(store, idp, "admin")
    follower, _ = await factory.follower()
    assert str(follower.id) in (await cli("followers", "list"))[1]
    assert (await cli("followers", "drain", str(follower.id)))[0] == 0
    _code, out, _err = await cli("followers", "revoke", str(follower.id))
    assert "released: 0" in out
    async with sessionmaker() as session:
        assert (await session.get(Follower, follower.id)).state == "revoked"


async def test_tokens_create_shows_the_token_once_and_list_never_does(
    cli, store, idp, sessionmaker
):
    sign_in_as(store, idp, "admin")
    code, out, _err = await cli(
        "tokens", "create", "--pool", "gpu", "--expires", "12h", "--max-uses", "3"
    )
    assert code == 0
    token = out.splitlines()[0].rsplit(" ", 1)[1]
    async with sessionmaker() as session:
        row = (await session.scalars(select(JoinToken))).one()
    assert (row.pool, row.max_uses, row.token_hash) == ("gpu", 3, hash_secret(token))
    _code, listed, _err = await cli("tokens", "list")
    assert str(row.id) in listed
    assert token not in listed
    assert (await cli("tokens", "revoke", str(row.id)))[0] == 0


async def test_consent_report(cli, store, idp, factory):
    sign_in_as(store, idp, "viewer")
    here = await factory.location(name="here")
    await factory.recording(here, key="talks/a.mp3", consent="withdrawn")
    code, out, _err = await cli("consent", "report")
    assert code == 0
    assert "here" in out


async def test_a_refusal_is_reported_without_any_token(cli, store, idp):
    token = sign_in_as(store, idp, "viewer")
    code, out, err = await cli("tokens", "create")
    assert code == 1
    assert "this needs the admin role" in err
    assert "(403 forbidden)" in err
    assert token not in out + err
    assert "refresh-token-1" not in out + err


async def test_an_id_token_about_to_expire_is_refreshed_silently(cli, store, idp, entra):
    old = sign_in_as(store, idp, "admin", lifetime=60)
    code, out, err = await cli("whoami")
    assert code == 0, err
    assert entra.refreshes == 1
    saved = store.load("http://leader")
    assert saved.id_token != old
    assert saved.refresh_token == "refresh-token-2"
    assert "role: admin" in out


async def test_logout_forgets_the_sign_in(cli, store, idp):
    sign_in_as(store, idp, "viewer")
    assert (await cli("logout"))[0] == 0
    assert store.load("http://leader") is None


def test_durations():
    assert [parse_duration(text) for text in ("90s", "30m", "12h", "7d")] == [
        90,
        1800,
        43200,
        604800,
    ]
    with pytest.raises(argparse.ArgumentTypeError):
        parse_duration("7 days")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest packages/leader/tests/test_admin_cli.py -v`
Expected: FAIL — `swarmscribe_leader.admin_cli.main` does not exist.

- [ ] **Step 3: The leader client**

`packages/leader/src/swarmscribe_leader/admin_cli/client.py`:

```python
"""HTTPS client for the leader's /v1/admin API that keeps the caller signed in."""

import time
from dataclasses import replace
from typing import Any

import httpx

from .credentials import CredentialStore, SignIn
from .device_flow import id_token_expiry, refresh_tokens

REFRESH_MARGIN_SECONDS = 120


class CliError(Exception):
    """A command failed; the message is for the person at the terminal."""


def _body(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


class LeaderClient:
    def __init__(
        self,
        leader: str,
        store: CredentialStore,
        *,
        http: httpx.AsyncClient,
        clock=time.time,
    ):
        self.leader = leader
        self.store = store
        self.http = http
        self.clock = clock

    async def _refreshed(self, sign_in: SignIn) -> SignIn:
        if not sign_in.refresh_token:
            raise CliError("your sign-in has expired; run `swarmscribe-admin login`")
        tokens = await refresh_tokens(
            self.http,
            token_endpoint=sign_in.token_endpoint,
            client_id=sign_in.client_id,
            refresh_token=sign_in.refresh_token,
            scope=sign_in.scope if sign_in.provider == "entra" else "",
            client_secret=sign_in.client_secret,
            clock=self.clock,
        )
        updated = replace(
            sign_in,
            id_token=tokens.id_token,
            refresh_token=tokens.refresh_token or sign_in.refresh_token,
        )
        self.store.save(updated)
        return updated

    async def _id_token(self, *, force_refresh: bool) -> str:
        sign_in = self.store.load(self.leader)
        if sign_in is None:
            raise CliError("not signed in to this leader; run `swarmscribe-admin login`")
        expiry = id_token_expiry(sign_in.id_token)
        if force_refresh or expiry is None or expiry - self.clock() < REFRESH_MARGIN_SECONDS:
            sign_in = await self._refreshed(sign_in)
        return sign_in.id_token

    async def _send(
        self, method: str, path: str, body: Any, params: Any, *, force_refresh: bool
    ) -> httpx.Response:
        token = await self._id_token(force_refresh=force_refresh)
        return await self.http.request(
            method,
            f"{self.leader}{path}",
            json=body,
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )

    async def request(
        self, method: str, path: str, *, body: Any = None, params: Any = None
    ) -> Any:
        response = await self._send(method, path, body, params, force_refresh=False)
        if response.status_code == 401 and _body(response).get("code") == "token_expired":
            response = await self._send(method, path, body, params, force_refresh=True)
        if response.status_code >= 400:
            error = _body(response)
            message = error.get("message") or response.reason_phrase
            raise CliError(f"{message} ({response.status_code} {error.get('code', 'error')})")
        return response.json() if response.content else None
```

- [ ] **Step 4: The commands**

`packages/leader/src/swarmscribe_leader/admin_cli/main.py`:

```python
"""swarmscribe-admin: sign in with Entra ID or Google and run a SwarmScribe leader."""

import argparse
import asyncio
import json
import os
import re
import sys
from collections.abc import Callable, Sequence
from typing import Any, TextIO
from urllib.parse import quote

import httpx

from .client import CliError, LeaderClient
from .credentials import CredentialStore, SignIn
from .device_flow import ProviderConfig, SignInError, device_sign_in

LEADER_ENV = "SWARMSCRIBE_LEADER_URL"
JOB_STATES = ("queued", "leased", "completed", "failed", "cancelled")
FOLLOWER_STATES = ("active", "draining", "revoked", "gone")
_DURATION = re.compile(r"(\d+)([smhd])")
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}

Renderer = Callable[[Any, TextIO], None]


def parse_duration(text: str) -> int:
    match = _DURATION.fullmatch(text.strip())
    if match is None:
        raise argparse.ArgumentTypeError("use a number and a unit: 90s, 30m, 12h or 7d")
    return int(match.group(1)) * _UNITS[match.group(2)]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="swarmscribe-admin", description="Administer a SwarmScribe leader."
    )
    parser.add_argument(
        "--leader", help=f"leader URL (default: ${LEADER_ENV}, or the last one signed in to)"
    )
    parser.add_argument("--json", action="store_true", help="print the leader's JSON answer")
    commands = parser.add_subparsers(dest="command", required=True)

    login = commands.add_parser("login", help="sign in with your organisation account")
    login.add_argument("--provider", choices=("entra", "google"))
    commands.add_parser("logout", help="forget the sign-in for this leader")
    commands.add_parser("whoami", help="who you are signed in as, and your role")
    commands.add_parser("status", help="queue, followers, locations and scan errors")

    locations = commands.add_parser("locations", help="storage locations").add_subparsers(
        dest="action", required=True
    )
    add = locations.add_parser("add", help="add a folder every leader replica can see")
    add.add_argument("name")
    add.add_argument("--root", required=True, help="absolute path of the folder")
    add.add_argument("--input-prefix", default="", help="e.g. incoming/ (default: all)")
    add.add_argument("--output-prefix", default="transcripts/")
    add.add_argument("--pool", default="default")
    add.add_argument("--device", choices=("any", "cuda", "cpu"), default="any")
    add.add_argument("--scan-interval", type=parse_duration, default=900, help="e.g. 15m")
    locations.add_parser("list")
    for action in ("disable", "enable"):
        locations.add_parser(action).add_argument("name")

    ingest = commands.add_parser("ingest", help="scan a location now")
    ingest.add_argument("name")

    jobs = commands.add_parser("jobs", help="transcription jobs").add_subparsers(
        dest="action", required=True
    )
    job_list = jobs.add_parser("list")
    job_list.add_argument("--state", choices=JOB_STATES)
    job_list.add_argument("--location")
    job_list.add_argument("--limit", type=int, default=50)
    for action in ("retry", "cancel"):
        jobs.add_parser(action).add_argument("job_id")
    priority = jobs.add_parser("priority")
    priority.add_argument("job_id")
    priority.add_argument("priority", type=int)

    followers = commands.add_parser("followers", help="transcribing machines").add_subparsers(
        dest="action", required=True
    )
    follower_list = followers.add_parser("list")
    follower_list.add_argument("--state", choices=FOLLOWER_STATES)
    for action in ("drain", "revoke"):
        followers.add_parser(action).add_argument("follower_id")

    tokens = commands.add_parser("tokens", help="join tokens for followers").add_subparsers(
        dest="action", required=True
    )
    create = tokens.add_parser("create", help="a new join token (shown once)")
    create.add_argument("--pool", default="default")
    create.add_argument("--expires", type=parse_duration, default=7 * 86400, help="e.g. 7d")
    create.add_argument("--max-uses", type=int, default=1)
    tokens.add_parser("list")
    tokens.add_parser("revoke").add_argument("token_id")

    consent = commands.add_parser("consent", help="consent overview").add_subparsers(
        dest="action", required=True
    )
    report = consent.add_parser("report")
    report.add_argument("--location")
    report.add_argument("--limit", type=int, default=500)
    return parser


# --- output ---------------------------------------------------------------------


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return " ".join(str(value).split())


def print_table(out: TextIO, rows: list[dict[str, Any]], columns: Sequence[str]) -> None:
    if not rows:
        print("(none)", file=out)
        return
    cells = [[_cell(row.get(column)) for column in columns] for row in rows]
    widths = [
        max(len(column), *(len(row[i]) for row in cells)) for i, column in enumerate(columns)
    ]

    def line(values: Sequence[str]) -> str:
        return "  ".join(v.ljust(w) for v, w in zip(values, widths, strict=True)).rstrip()

    print(line(columns), file=out)
    for row in cells:
        print(line(row), file=out)


def print_fields(data: dict[str, Any], out: TextIO) -> None:
    for name, value in data.items():
        print(f"{name}: {_cell(value)}", file=out)


def table(columns: Sequence[str]) -> Renderer:
    return lambda data, out: print_table(out, data, columns)


def print_status(data: dict[str, Any], out: TextIO) -> None:
    jobs = data["jobs"]
    job_counts = ", ".join(f"{state} {jobs.get(state, 0)}" for state in JOB_STATES)
    print(f"jobs: {job_counts}", file=out)
    print(
        f"completed in the last hour: {data['completed_last_hour']}; "
        f"failed attempts in the last day: {data['failed_attempts_last_day']}",
        file=out,
    )
    followers = data["followers"]
    follower_counts = ", ".join(f"{state} {followers.get(state, 0)}" for state in FOLLOWER_STATES)
    print(f"followers: {follower_counts}", file=out)
    print("\nqueues:", file=out)
    print_table(out, data["pools"], ("pool", "queued", "leased"))
    print("\nlocations:", file=out)
    print_table(
        out,
        data["locations"],
        ("name", "enabled", "recordings", "consented", "last_scan_at", "last_scan_error"),
    )


def print_token(data: dict[str, Any], out: TextIO) -> None:
    print(f"join token (shown once; keep it safe): {data['token']}", file=out)
    print_fields({name: value for name, value in data.items() if name != "token"}, out)


def print_consent(data: dict[str, Any], out: TextIO) -> None:
    print_table(
        out, data["locations"], ("name", "consented", "not_consented", "withdrawn", "missing")
    )
    print("\noutputs flagged for deletion (consent withdrawn):", file=out)
    print_table(out, data["flagged"], ("job_id", "location", "key", "output_location", "outputs"))
    if data["truncated"]:
        print("(more not shown; use --limit)", file=out)


def print_scan(data: dict[str, Any], out: TextIO) -> None:
    print(f"scan requested for {data['name']}; it starts within a minute", file=out)


# --- commands -------------------------------------------------------------------


def _seg(value: str) -> str:
    return quote(value, safe="")


async def dispatch(args: argparse.Namespace, client: LeaderClient) -> tuple[Any, Renderer]:
    command, action = args.command, getattr(args, "action", None)

    async def get(path: str, **kwargs: Any) -> Any:
        return await client.request("GET", path, **kwargs)

    async def post(path: str, **kwargs: Any) -> Any:
        return await client.request("POST", path, **kwargs)

    if command == "whoami":
        return await get("/v1/admin/whoami"), print_fields
    if command == "status":
        return await get("/v1/admin/status"), print_status
    if command == "ingest":
        return await post(f"/v1/admin/locations/{_seg(args.name)}/ingest"), print_scan
    if command == "locations":
        if action == "add":
            body = {
                "name": args.name,
                "root": args.root,
                "input_prefix": args.input_prefix,
                "output_prefix": args.output_prefix,
                "pool": args.pool,
                "required_device": args.device,
                "scan_interval_s": args.scan_interval,
            }
            return await post("/v1/admin/locations", body=body), print_fields
        if action == "list":
            columns = (
                "name",
                "enabled",
                "root",
                "input_prefix",
                "pool",
                "last_scan_at",
                "last_scan_error",
            )
            return await get("/v1/admin/locations"), table(columns)
        return await post(f"/v1/admin/locations/{_seg(args.name)}/{action}"), print_fields
    if command == "jobs":
        if action == "list":
            params = {"limit": args.limit}
            if args.state:
                params["state"] = args.state
            if args.location:
                params["location"] = args.location
            columns = ("id", "state", "location", "key", "attempts", "priority", "failure_reason")
            return await get("/v1/admin/jobs", params=params), table(columns)
        path = f"/v1/admin/jobs/{_seg(args.job_id)}/{action}"
        if action == "priority":
            return await post(path, body={"priority": args.priority}), print_fields
        return await post(path), print_fields
    if command == "followers":
        if action == "list":
            params = {"state": args.state} if args.state else None
            columns = ("id", "pool", "state", "device", "leases", "last_seen_at")
            return await get("/v1/admin/followers", params=params), table(columns)
        return await post(f"/v1/admin/followers/{_seg(args.follower_id)}/{action}"), print_fields
    if command == "tokens":
        if action == "create":
            body = {
                "pool": args.pool,
                "expires_in_seconds": args.expires,
                "max_uses": args.max_uses,
            }
            return await post("/v1/admin/tokens", body=body), print_token
        if action == "list":
            columns = ("id", "pool", "uses", "max_uses", "revoked", "expires_at", "created_by")
            return await get("/v1/admin/tokens"), table(columns)
        return await post(f"/v1/admin/tokens/{_seg(args.token_id)}/revoke"), print_fields
    if command == "consent":
        params = {"limit": args.limit}
        if args.location:
            params["location"] = args.location
        return await get("/v1/admin/consent/report", params=params), print_consent
    raise CliError(f"unknown command {command!r}")


async def login(
    args: argparse.Namespace,
    leader: str,
    http: httpx.AsyncClient,
    store: CredentialStore,
    out: TextIO,
) -> int:
    response = await http.get(f"{leader}/v1/admin/login-config")
    if response.status_code != 200:
        raise CliError(f"the leader did not answer the sign-in request ({response.status_code})")
    providers = {p["name"]: p for p in response.json()["providers"]}
    if not providers:
        raise CliError("this leader has no sign-in provider configured")
    name = args.provider
    if name is None:
        if len(providers) > 1:
            raise CliError(
                "this leader accepts "
                + " and ".join(sorted(providers))
                + " sign-in; choose one with --provider"
            )
        (name,) = providers
    if name not in providers:
        raise CliError(f"this leader does not accept {name} sign-in")
    chosen = providers[name]
    config = ProviderConfig(
        name=name,
        client_id=chosen["client_id"],
        device_authorization_endpoint=chosen["device_authorization_endpoint"],
        token_endpoint=chosen["token_endpoint"],
        scope=chosen["scope"],
        client_secret=chosen.get("client_secret"),
    )
    tokens = await device_sign_in(
        http, config, prompt=lambda message: print(message, file=out, flush=True)
    )
    store.save(
        SignIn(
            leader=leader,
            provider=name,
            client_id=config.client_id,
            token_endpoint=config.token_endpoint,
            scope=config.scope,
            id_token=tokens.id_token,
            refresh_token=tokens.refresh_token,
            client_secret=config.client_secret,
        )
    )
    try:
        me = await LeaderClient(leader, store, http=http).request("GET", "/v1/admin/whoami")
    except CliError as exc:
        print(f"signed in, but the leader refused you: {exc}", file=out)
        return 1
    print(f"signed in as {me['email'] or me['subject']} ({me['role']})", file=out)
    return 0


async def amain(
    argv: Sequence[str] | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    store: CredentialStore | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    out = out or sys.stdout
    err = err or sys.stderr
    args = build_parser().parse_args(argv)
    store = store or CredentialStore()
    leader = (args.leader or os.environ.get(LEADER_ENV) or store.default_leader() or "").rstrip(
        "/"
    )
    if not leader:
        print(f"error: no leader URL; pass --leader or set {LEADER_ENV}", file=err)
        return 2
    try:
        async with httpx.AsyncClient(transport=transport, timeout=30.0) as http:
            if args.command == "login":
                return await login(args, leader, http, store, out)
            if args.command == "logout":
                removed = store.remove(leader)
                message = f"signed out of {leader}" if removed else f"not signed in to {leader}"
                print(message, file=out)
                return 0
            data, render = await dispatch(args, LeaderClient(leader, store, http=http))
    except (CliError, SignInError) as exc:
        print(f"error: {exc}", file=err)
        return 1
    except httpx.HTTPError as exc:
        print(f"error: cannot reach {leader} ({type(exc).__name__})", file=err)
        return 1
    if args.json:
        print(json.dumps(data, indent=2), file=out)
    else:
        render(data, out)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(amain(argv))


def run() -> None:
    sys.exit(main())
```

In `packages/leader/pyproject.toml`, add to `[project.scripts]`:

```toml
swarmscribe-admin = "swarmscribe_leader.admin_cli.main:run"
```

Run: `uv sync`
Expected: `swarmscribe-admin --help` (via `uv run swarmscribe-admin --help`) prints the command list.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest packages/leader/tests/test_admin_cli.py packages/leader/tests/test_admin_cli_auth.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/leader/pyproject.toml uv.lock packages/leader/src/swarmscribe_leader/admin_cli packages/leader/tests/test_admin_cli.py
git commit -m "Admin CLI: swarmscribe-admin commands for status, locations, ingest, jobs, followers, tokens, consent

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Compose end-to-end — two leader replicas, a dying follower, a killed replica

**Files:**
- Create: `.dockerignore`
- Create: `e2e/compose/Dockerfile`, `e2e/compose/docker-compose.yml`, `e2e/compose/nginx.conf`, `e2e/compose/run_e2e.py`
- Modify: `.gitignore`, `pyproject.toml` (dev dependency `pyyaml`), `.github/workflows/ci.yml` (job `compose-e2e`)
- Test: `packages/leader/tests/test_compose_driver.py`

**Interfaces:**
- Consumes: the follower API, `create_join_token`, the models and `make_engine`/`make_sessionmaker` from Plan A1.
- Produces (in `e2e/compose/run_e2e.py`): `CONSENTED` (8 keys under `talks/`), `UNCONSENTED = "private/held.mp3"`, `link_key(url) -> str`, `@dataclass Follower`, `@dataclass(frozen=True) Report(completed: int, abandoned_job: str, killed_after: int)`, and `async def run(*, base_url, database_url, data_dir: Path, storage_root: str, kill_replica: Callable[[], object], transport=None, work_seconds=1.0, timeout=240.0) -> Report` (raises `AssertionError` with a plain message on any failed expectation); `main()` for CI (`--base-url http://localhost:8080`, `--database-url postgresql://postgres:postgres@localhost:5432/swarmscribe`, `--data-dir e2e/compose/work/data`, `--storage-root /data`, `--replica leader-1`).
- The scenario: write 8 consented recordings, one unconsented, one non-audio file; seed the location (`scan_interval_s=0`) and a join token directly in Postgres; wait for `/readyz` through the proxy; follower "doomed" claims one job and is never heard from again; follower "steady" works until every consented recording is complete; after its second completion, `kill_replica()` runs (CI: `docker compose kill leader-1`). Then it asserts: each consented recording completed exactly once with a result row and outputs on disk; the unconsented one is `not_consented`, has no job, and no claim ever linked it; the abandoned job's attempts include the doomed follower's `expired` and the steady follower's `completed`.

This machine has no Docker: Steps 1–5 are verified here by the in-process test and the YAML check; the real two-replica run happens in GitHub Actions (Step 6) after the branch is pushed by the controller.

- [ ] **Step 1: Write the failing tests**

In `pyproject.toml` (repository root), add `"pyyaml>=6",` to the `dev` dependency group, then run `uv lock` and `uv sync`.

`packages/leader/tests/test_compose_driver.py`:

```python
import importlib.util
import sys
from pathlib import Path

import httpx
import yaml
from swarmscribe_leader.app import create_app
from swarmscribe_leader.config import Settings

COMPOSE = Path(__file__).resolve().parents[3] / "e2e" / "compose"


def load_driver():
    spec = importlib.util.spec_from_file_location("compose_e2e_driver", COMPOSE / "run_e2e.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


async def test_the_compose_scenario_passes_against_an_in_process_leader(
    engine, migrated_database_url, tmp_path
):
    driver = load_driver()
    settings = Settings(
        database_url=migrated_database_url,
        public_url="http://leader",
        link_key="k" * 32,
        lease_seconds=2,
        heartbeat_seconds=1,
        reaper_interval_seconds=0.2,
        scanner_interval_seconds=0.2,
        claim_retry_after=1,
    )
    app = create_app(settings, background=True)
    kills: list[str] = []
    async with app.router.lifespan_context(app):
        report = await driver.run(
            base_url="http://leader",
            database_url=migrated_database_url,
            data_dir=tmp_path,
            storage_root=str(tmp_path),
            kill_replica=lambda: kills.append("leader-1"),
            transport=httpx.ASGITransport(app=app),
            work_seconds=0.05,
            timeout=90,
        )
    assert kills == ["leader-1"]
    assert report.completed == len(driver.CONSENTED)
    assert report.killed_after == 2


def test_link_key_reads_the_key_a_leader_link_names():
    from swarmscribe_leader.storage.links import LinkClaims, LinkSigner

    token = LinkSigner(b"k" * 32).sign(
        LinkClaims(location_id="l", key="talks/a.mp3", method="GET", version="1-2", expires=9)
    )
    assert load_driver().link_key(f"http://leader/v1/files/{token}") == "talks/a.mp3"


def test_two_identical_replicas_sit_behind_a_proxy_that_logs_no_links():
    compose = yaml.safe_load((COMPOSE / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    assert {"postgres", "migrate", "leader-1", "leader-2", "proxy"} <= set(services)
    one, two = services["leader-1"], services["leader-2"]
    assert one["environment"] == two["environment"]
    assert one["volumes"] == two["volumes"] == ["./work/data:/data"]
    assert one["environment"]["SWARMSCRIBE_PUBLIC_URL"] == "http://localhost:8080"
    nginx = (COMPOSE / "nginx.conf").read_text(encoding="utf-8")
    assert "access_log off;" in nginx
    assert "error_log /dev/stderr crit;" in nginx
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest packages/leader/tests/test_compose_driver.py -v`
Expected: FAIL — `e2e/compose/run_e2e.py` and `docker-compose.yml` do not exist.

- [ ] **Step 3: The driver**

`e2e/compose/run_e2e.py`:

```python
"""Compose end-to-end scenario for the leader (leader spec, section 13).

Postgres, two leader replicas behind nginx, and two scripted followers on a local location
holding consented and unconsented recordings. One follower dies holding a job; after two
completions one leader replica is killed. Every consented recording must complete exactly
once, and the unconsented one must never be linked.

CI runs this after `docker compose up` (.github/workflows/ci.yml); the leader's tests also
run it in-process against one leader (packages/leader/tests/test_compose_driver.py).
Nothing here prints a link, a credential or a join token.
"""

import argparse
import asyncio
import base64
import hashlib
import json
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from swarmscribe_leader.auth.followers import create_join_token
from swarmscribe_leader.clock import utcnow
from swarmscribe_leader.db.models import Job, JobAttempt, JobResult, Recording, StorageLocation
from swarmscribe_leader.db.session import make_engine, make_sessionmaker

HERE = Path(__file__).resolve().parent
COMPOSE_FILE = HERE / "docker-compose.yml"
LOCATION = "e2e"
CONSENTED = tuple(f"talks/{name}.mp3" for name in "abcdefgh")
UNCONSENTED = "private/held.mp3"
CAPABILITIES = {
    "device": "cpu",
    "models": ["distil-large-v3"],
    "engine_version": "0.1.0",
    "pool": "default",
}
TRANSIENT = frozenset({502, 503, 504})


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def link_key(url: str) -> str:
    """The storage key a leader file link names (its payload is signed, not encrypted)."""
    payload = url.rsplit("/", 1)[1].split(".", 1)[0]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["key"]


async def call(client: httpx.AsyncClient, method: str, url: str, **kwargs: Any) -> httpx.Response:
    """One request through the proxy, retried while a replica is down or restarting."""
    for _ in range(120):
        try:
            response = await client.request(method, url, **kwargs)
        except httpx.TransportError:
            await asyncio.sleep(0.5)
            continue
        if response.status_code not in TRANSIENT:
            return response
        await asyncio.sleep(0.5)
    raise AssertionError(f"a {method} request failed for a minute: the leaders are unreachable")


@dataclass
class Follower:
    client: httpx.AsyncClient
    follower_id: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    linked: list[str] = field(default_factory=list)
    completed: list[str] = field(default_factory=list)

    async def register(self, token: str) -> None:
        response = await call(
            self.client,
            "POST",
            "/v1/followers/register",
            json={"join_token": token, "protocol_version": 1, "capabilities": CAPABILITIES},
        )
        expect(response.status_code == 200, f"registering answered {response.status_code}")
        body = response.json()
        self.follower_id = body["follower_id"]
        self.headers = {"Authorization": f"Bearer {body['credential']}"}

    async def claim(self) -> dict[str, Any] | None:
        response = await call(self.client, "POST", "/v1/jobs/claim", headers=self.headers)
        if response.status_code == 204:
            return None
        expect(response.status_code == 200, f"claiming answered {response.status_code}")
        claim = response.json()
        self.linked.append(link_key(claim["download_url"]["url"]))
        return claim

    async def work(self, claim: dict[str, Any], work_seconds: float) -> bool:
        """Download, 'transcribe', upload, submit. False when the lease was lost on the way
        (the reaper queues the job again and a later attempt completes it)."""
        job, lease = claim["job_id"], {"lease_id": claim["lease_id"]}
        download = await call(self.client, "GET", claim["download_url"]["url"])
        if download.status_code != 200:
            return False
        heartbeat = await call(
            self.client, "POST", f"/v1/jobs/{job}/heartbeat", headers=self.headers, json=lease
        )
        if heartbeat.status_code != 200 or heartbeat.json()["directive"] != "continue":
            return False
        await asyncio.sleep(work_seconds)
        checksums = {"source": hashlib.sha256(download.content).hexdigest()}
        for name in ("txt", "srt", "segments_json"):
            body = f"{name} of {len(download.content)} bytes\n".encode()
            upload = await call(
                self.client, "PUT", claim["upload_urls"][name]["url"], content=body
            )
            if upload.status_code != 201:
                return False
            checksums[name] = hashlib.sha256(body).hexdigest()
        submit = await call(
            self.client,
            "POST",
            f"/v1/jobs/{job}/submit",
            headers=self.headers,
            json={**lease, "checksums": checksums},
        )
        if submit.status_code != 200:
            return False
        self.completed.append(job)
        return True


@dataclass(frozen=True)
class Report:
    completed: int
    abandoned_job: str
    killed_after: int


def write_recordings(data_dir: Path) -> None:
    def put(key: str, data: bytes) -> None:
        path = data_dir / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    put("consent.txt", b"talks/*.mp3\n")
    for number, key in enumerate(CONSENTED):
        put(key, f"recording {number}\n".encode() * (50 + number))
    put(UNCONSENTED, b"never to be processed\n")
    put("talks/notes.txt", b"not audio\n")


async def seed(sessionmaker: async_sessionmaker[AsyncSession], storage_root: str) -> str:
    """The location and a join token, written straight to Postgres (Compose runs no
    identity provider, so the admin API is not used here)."""
    async with sessionmaker() as session:
        session.add(
            StorageLocation(
                id=uuid.uuid4(),
                name=LOCATION,
                backend="local",
                config={"root": storage_root},
                input_prefix="",
                output_prefix="transcripts/",
                pool="default",
                required_device="any",
                scan_interval_s=0,
                enabled=True,
                vocabulary_version=0,
            )
        )
        _, token = await create_join_token(
            session,
            pool="default",
            expires_at=utcnow() + timedelta(hours=1),
            max_uses=5,
            created_by="e2e",
        )
        await session.commit()
    return token


async def wait_until_ready(client: httpx.AsyncClient, deadline: float) -> None:
    while time.monotonic() < deadline:
        try:
            if (await client.get("/readyz")).status_code == 200:
                return
        except httpx.TransportError:
            pass
        await asyncio.sleep(1)
    raise AssertionError("the leaders never became ready")


async def claim_and_die(follower: Follower, deadline: float) -> str:
    """The doomed follower takes one job and is never heard from again."""
    while time.monotonic() < deadline:
        claim = await follower.claim()
        if claim is not None:
            return claim["job_id"]
        await asyncio.sleep(0.3)
    raise AssertionError("the scanner never queued anything")


async def completed_recordings(sessionmaker: async_sessionmaker[AsyncSession]) -> int:
    async with sessionmaker() as session:
        return await session.scalar(
            select(func.count(func.distinct(Job.recording_id))).where(Job.state == "completed")
        )


async def check(
    sessionmaker: async_sessionmaker[AsyncSession],
    data_dir: Path,
    *,
    doomed: Follower,
    steady: Follower,
    abandoned: str,
) -> None:
    async with sessionmaker() as session:
        recordings = {r.key: r for r in (await session.scalars(select(Recording))).all()}
        jobs = (await session.scalars(select(Job))).all()
        results = {r.job_id for r in (await session.scalars(select(JobResult))).all()}
        attempts = (
            await session.scalars(
                select(JobAttempt).where(JobAttempt.job_id == uuid.UUID(abandoned))
            )
        ).all()
    expect(set(recordings) == {*CONSENTED, UNCONSENTED}, f"catalogued {sorted(recordings)}")
    for key in CONSENTED:
        done = [j for j in jobs if j.recording_id == recordings[key].id and j.state == "completed"]
        expect(len(done) == 1, f"{key} completed {len(done)} times")
        expect(done[0].id in results, f"{key} has no recorded result")
        outputs = data_dir / "transcripts" / f"{key}.segments.json"
        expect(outputs.exists(), f"{key} has no outputs in storage")
    held = recordings[UNCONSENTED]
    expect(held.consent == "not_consented", f"the unconsented recording is {held.consent}")
    expect(
        all(j.recording_id != held.id for j in jobs),
        "a job exists for a recording without consent",
    )
    linked = set(doomed.linked) | set(steady.linked)
    expect(linked <= set(CONSENTED), "a link was issued for a recording without consent")
    seen = {(str(a.follower_id), a.outcome) for a in attempts}
    expect((doomed.follower_id, "expired") in seen, "the dead follower's lease never expired")
    expect(
        (steady.follower_id, "completed") in seen,
        "the abandoned job was not finished by the other follower",
    )


async def run(
    *,
    base_url: str,
    database_url: str,
    data_dir: Path,
    storage_root: str,
    kill_replica: Callable[[], object],
    transport: httpx.AsyncBaseTransport | None = None,
    work_seconds: float = 1.0,
    timeout: float = 240.0,
) -> Report:
    write_recordings(data_dir)
    engine = make_engine(database_url)
    sessionmaker = make_sessionmaker(engine)
    deadline = time.monotonic() + timeout
    try:
        token = await seed(sessionmaker, storage_root)
        async with httpx.AsyncClient(
            base_url=base_url, transport=transport, timeout=30.0
        ) as client:
            await wait_until_ready(client, deadline)
            doomed, steady = Follower(client), Follower(client)
            await doomed.register(token)
            await steady.register(token)
            abandoned = await claim_and_die(doomed, deadline)
            killed_after: int | None = None
            while await completed_recordings(sessionmaker) < len(CONSENTED):
                expect(time.monotonic() < deadline, "not every consented recording completed")
                if killed_after is None and len(steady.completed) >= 2:
                    killed_after = len(steady.completed)
                    await asyncio.to_thread(kill_replica)
                claim = await steady.claim()
                if claim is None:
                    await asyncio.sleep(0.3)
                    continue
                await steady.work(claim, work_seconds)
        expect(killed_after is not None, "everything completed before a replica was killed")
        await check(sessionmaker, data_dir, doomed=doomed, steady=steady, abandoned=abandoned)
    finally:
        await engine.dispose()
    return Report(completed=len(CONSENTED), abandoned_job=abandoned, killed_after=killed_after)


def main() -> int:
    parser = argparse.ArgumentParser(description="SwarmScribe Compose end-to-end scenario")
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument(
        "--database-url", default="postgresql://postgres:postgres@localhost:5432/swarmscribe"
    )
    parser.add_argument("--data-dir", type=Path, default=HERE / "work" / "data")
    parser.add_argument("--storage-root", default="/data")
    parser.add_argument("--replica", default="leader-1")
    args = parser.parse_args()

    def kill() -> None:
        subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "kill", args.replica], check=True
        )
        print(f"killed {args.replica}", flush=True)

    try:
        report = asyncio.run(
            run(
                base_url=args.base_url,
                database_url=args.database_url,
                data_dir=args.data_dir,
                storage_root=args.storage_root,
                kill_replica=kill,
            )
        )
    except AssertionError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    print(
        f"passed: {report.completed} recordings completed once each; "
        f"{args.replica} was killed after {report.killed_after} completions"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: The image, the Compose file and the proxy**

`.dockerignore` (repository root):

```
.git
.venv
.pgdata
.pytest_cache
.ruff_cache
.superpowers
**/__pycache__
e2e/compose/work
```

`e2e/compose/Dockerfile`:

```dockerfile
# The leader alone (no engine, no model), for the Compose end-to-end test.
# Build from the repository root: docker build -t swarmscribe-leader:e2e -f e2e/compose/Dockerfile .
FROM python:3.12-slim
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy
RUN pip install --no-cache-dir "uv>=0.5,<1"
WORKDIR /app
COPY . .
RUN uv sync --frozen --no-dev --package swarmscribe-leader
ENV PATH="/app/.venv/bin:${PATH}"
EXPOSE 8080
CMD ["swarmscribe-leader", "serve", "--host", "0.0.0.0", "--port", "8080"]
```

`e2e/compose/docker-compose.yml`:

```yaml
# Postgres, a one-shot migration, two leader replicas and an nginx proxy in front of them.
# The image is built beforehand (see .github/workflows/ci.yml); run_e2e.py drives the test.
name: swarmscribe-e2e

x-leader: &leader
  image: swarmscribe-leader:e2e
  pull_policy: never
  environment:
    SWARMSCRIBE_DATABASE_URL: postgresql://postgres:postgres@postgres:5432/swarmscribe
    SWARMSCRIBE_PUBLIC_URL: http://localhost:8080
    SWARMSCRIBE_LINK_KEY: e2e-only-link-key-0123456789abcdef
    SWARMSCRIBE_LEASE_SECONDS: "8"
    SWARMSCRIBE_HEARTBEAT_SECONDS: "2"
    SWARMSCRIBE_REAPER_INTERVAL_SECONDS: "1"
    SWARMSCRIBE_SCANNER_INTERVAL_SECONDS: "1"
    SWARMSCRIBE_CLAIM_RETRY_AFTER: "1"
    SWARMSCRIBE_FOLLOWER_GONE_AFTER_SECONDS: "60"
  volumes:
    - ./work/data:/data

x-ready: &ready
  test:
    - CMD
    - python
    - -c
    - "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/readyz', timeout=2).status == 200 else 1)"
  interval: 2s
  timeout: 3s
  retries: 30

services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: swarmscribe
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres -d swarmscribe"]
      interval: 2s
      timeout: 3s
      retries: 30

  migrate:
    <<: *leader
    command: ["swarmscribe-leader", "migrate"]
    depends_on:
      postgres:
        condition: service_healthy

  leader-1:
    <<: *leader
    healthcheck: *ready
    depends_on:
      migrate:
        condition: service_completed_successfully

  leader-2:
    <<: *leader
    healthcheck: *ready
    depends_on:
      migrate:
        condition: service_completed_successfully

  proxy:
    image: nginx:1.27-alpine
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
    ports:
      - "8080:80"
    depends_on:
      leader-1:
        condition: service_healthy
      leader-2:
        condition: service_healthy
```

`e2e/compose/nginx.conf`:

```nginx
# File links carry signed tokens in their paths, so nothing here may log a request line:
# no access log, and the error log keeps only "crit" and above (upstream failures are
# logged at "error" level together with the request line).
worker_processes 1;
error_log /dev/stderr crit;

events {}

http {
    access_log off;
    client_max_body_size 600m;
    proxy_request_buffering off;
    proxy_buffering off;

    upstream leaders {
        server leader-1:8080 max_fails=1 fail_timeout=5s;
        server leader-2:8080 max_fails=1 fail_timeout=5s;
    }

    server {
        listen 80;
        location / {
            proxy_pass http://leaders;
            proxy_http_version 1.1;
            proxy_connect_timeout 2s;
            # Idempotent requests move to the other replica; a POST that fails answers 502
            # and the follower retries it (claim, heartbeat and submit are safe to repeat).
            proxy_next_upstream error timeout http_502 http_503;
        }
    }
}
```

Add to `.gitignore`:

```
e2e/compose/work/
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest packages/leader/tests/test_compose_driver.py -v`, then `uv run pytest -q` and `uv run ruff check .`
Expected: PASS; ruff clean (it checks `e2e/` too).

- [ ] **Step 6: The CI job**

In `.github/workflows/ci.yml`, add a second job under `jobs:` (after `test:`):

```yaml
  compose-e2e:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync
      - name: Build the leader image
        run: docker build -t swarmscribe-leader:e2e -f e2e/compose/Dockerfile .
      - name: Start Postgres, two leader replicas and the proxy
        run: |
          mkdir -p e2e/compose/work/data
          docker compose -f e2e/compose/docker-compose.yml up -d
      - name: Run the scenario (kills leader-1 mid-run)
        run: uv run python e2e/compose/run_e2e.py
      - name: Show the leaders' logs
        if: failure()
        run: docker compose -f e2e/compose/docker-compose.yml logs --no-color migrate leader-1 leader-2
      - name: Tear down
        if: always()
        run: docker compose -f e2e/compose/docker-compose.yml down -v
```

`up -d` returns once the proxy has started, which waits for both replicas to report `/readyz` healthy (and they wait for the migration). The leaders never log links or credentials, so printing their logs on failure is safe.

- [ ] **Step 7: Commit**

```bash
git add .dockerignore .gitignore pyproject.toml uv.lock e2e .github/workflows/ci.yml packages/leader/tests/test_compose_driver.py
git commit -m "Leader: Compose end-to-end with two replicas, a dying follower and a killed replica (CI only)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

The controller pushes the branch and confirms the `compose-e2e` job passes in GitHub Actions; that run is the proof for spec section 13's "killing a leader replica mid-run changes nothing".

---

### Task 14: README

**Files:**
- Modify: `README.md`

**Interfaces:** none (documentation only).

- [ ] **Step 1: Update the status row**

In the Status table, set the `swarmscribe-leader` row to:

```markdown
| `swarmscribe-leader` — catalogue, consent, jobs, admin API and `swarmscribe-admin` | Built (local storage); cloud storage and vocabulary next |
```

- [ ] **Step 2: Replace the paragraph "Admin commands (adding locations, creating join tokens) arrive with the next plan; …"**

Replace that paragraph (two sentences, in "Run the leader (development)") with:

````markdown
### Administrators: sign-in and roles

Administrators sign in with Microsoft Entra ID, Google, or either (both may be
configured at once). Each person gets a role; roles are cumulative:

| Role | May |
|---|---|
| viewer | `status`, `whoami`, `locations list`, `jobs list`, `followers list`, `consent report` |
| operator | viewer + `ingest`, `jobs retry/cancel/priority`, `followers drain` |
| admin | operator + `locations add/disable/enable`, `tokens create/list/revoke`, `followers revoke` |

Every admin call is written to the audit log with the person's email, issuer and
subject. Roles are cached for five minutes, so a change of group membership takes
up to five minutes to apply.

**Entra ID.** Register an application with "Allow public client flows" enabled
and the `groups` claim added to the ID token (security groups). Set
`SWARMSCRIBE_ENTRA_TENANT_ID` (the tenant's GUID) and
`SWARMSCRIBE_ENTRA_CLIENT_ID`, and map group object IDs to roles with
`SWARMSCRIBE_ROLE_VIEWER_ENTRA_GROUPS`, `…_OPERATOR_…`, `…_ADMIN_ENTRA_GROUPS`
(comma-separated). People in too many groups for the token are looked up in
Microsoft Graph: give the application a client secret
(`SWARMSCRIBE_ENTRA_CLIENT_SECRET`) and the `GroupMember.Read.All` application
permission.

**Google.** Create an OAuth client of type "TVs and Limited Input devices" and
set `SWARMSCRIBE_GOOGLE_CLIENT_ID` and `SWARMSCRIBE_GOOGLE_CLIENT_SECRET` (the
CLI needs this secret for device sign-in; Google treats it as public, and the
leader hands it to the CLI). Optionally restrict sign-in to one Workspace domain
with `SWARMSCRIBE_GOOGLE_HOSTED_DOMAIN`. Roles come from Google Groups when
`SWARMSCRIBE_GOOGLE_SERVICE_ACCOUNT` holds a service-account JSON key (or the path
of a mounted file with it) whose service account has the Groups Reader admin
role — map group emails with `SWARMSCRIBE_ROLE_<ROLE>_GOOGLE_GROUPS` — and, in
addition or instead, from `SWARMSCRIBE_ROLE_<ROLE>_EMAILS` and
`SWARMSCRIBE_ROLE_<ROLE>_DOMAINS`. Email and domain lists apply to Google
sign-ins only.

`/readyz` reports ready once each configured provider's signing keys have been
fetched.

### `swarmscribe-admin`

```
uv run swarmscribe-admin --leader https://leader.example.org login --provider entra
uv run swarmscribe-admin status
uv run swarmscribe-admin locations add archive --root /mnt/archive --input-prefix incoming/
uv run swarmscribe-admin ingest archive
uv run swarmscribe-admin tokens create --pool default --expires 7d --max-uses 5
uv run swarmscribe-admin jobs list --state failed
uv run swarmscribe-admin jobs retry <job-id>
uv run swarmscribe-admin followers revoke <follower-id>
uv run swarmscribe-admin consent report
```

`login` shows a code to enter in the browser. The sign-in is kept in
`~/.config/swarmscribe/credentials.json` (readable by you only; override the
path with `SWARMSCRIBE_ADMIN_CREDENTIALS`) and refreshed silently; the leader
URL is remembered, or set `SWARMSCRIBE_LEADER_URL`. `--json` prints the
leader's answer as JSON. A join token is shown once, by `tokens create`.

A location's root must be visible at the same path to every leader replica.
`ingest` asks for a scan within a minute; `jobs cancel` is final for that
version of the recording until `jobs retry`; `followers revoke` releases the
follower's work at once.

### Multi-replica test

`e2e/compose/` runs Postgres, two leader replicas behind nginx and scripted
followers, kills a follower and a replica mid-run, and checks that every
consented recording completes exactly once. It runs in GitHub Actions (job
`compose-e2e`); locally, with Docker:

```
docker build -t swarmscribe-leader:e2e -f e2e/compose/Dockerfile .
mkdir -p e2e/compose/work/data
docker compose -f e2e/compose/docker-compose.yml up -d
uv run python e2e/compose/run_e2e.py
docker compose -f e2e/compose/docker-compose.yml down -v
```
````

- [ ] **Step 3: Check and commit**

Run: `uv run pytest -q` and `uv run ruff check .`
Expected: PASS.

```bash
git add README.md
git commit -m "README: administrator sign-in, roles, swarmscribe-admin and the multi-replica test

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
