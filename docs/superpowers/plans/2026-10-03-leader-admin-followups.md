# Leader Admin (Plan A2) — carried-forward items

From the task reviews and final review of `leader-admin` (2026-10-03). None
blocks merge.

## Plan B or the next leader plan

- **JWKS key-size floor.** PyJWT accepts very small RSA keys from a
  provider's key set. Skip keys under 2048 bits in the per-key loop. Keys only
  come from the configured issuer over HTTPS, so this is defence in depth.
- **Readiness and the follower plane.** *Done 2026-10-05 (leader chart L1):*
  `/readyz` no longer waits for identity-provider metadata. Still open: a
  way to see from outside that sign-in works, without gating traffic (leader
  chart spec, follow-up F8).
- **Token audience.** Leaders accept any ID token whose audience is their
  client ID; the README says to use one app registration per deployment.
  Access tokens with an API scope would bind tokens to one leader.
- **Location roots.** Any folder the leader can see may be a root (even `/`);
  consider an optional allow-list of permitted parents.
- **Location names** are unique case-insensitively in code under an advisory
  lock; add a `lower(name)` unique index in the next migration.
- **Retry/scanner race backstop.** Retry and the scanner serialise by lock
  order; a partial unique index on open jobs per recording would make that
  independent of future scanner changes.
- **Version format upgrade.** Local versions became `size-mtime-inode`; an
  A1 database (none exist) would re-transcribe everything on first scan.

## From the review of the leader chart's first plan (L1), 2026-10-06

Fixed on `leader-chart-l1-fixes` and not listed here: the image check that hung (I1), the
unbounded stop on a silent database (I2), the migration rule and its guard (I3), the serial
503 on sign-in (M1), the image check's blind spots (M2), the traceback for a malformed or
unreachable database (M4), two wrong comments (M5, M6), "within 3 seconds" (M10). Still
open, with the review's numbers (`.superpowers/sdd/2026-10-05-leader-chart/l1-review.md`):

- **M3. A cancelled `/readyz` caller orphans its database check** (`api/health.py`,
  `ReadinessProbe._run`): five callers cancelled mid-check started five checks, none
  recorded as stuck. Not reachable under uvicorn today (it does not cancel a handler when the
  client goes away). Record the task before awaiting it.
- **M7. Shipped and unused**: `watchfiles`, `python-dotenv`, `websockets` (from
  `uvicorn[standard]`), the base image's pip and setuid binaries. No `revision` label: nothing
  says which commit an image was built from. The image's `HEALTHCHECK` hard-codes port 8080.
- **M8. Leftovers of the old readiness rule**: `tests/test_admin_auth.py`
  `test_readyz_is_ready_once_the_metadata_is_fetched` no longer tests anything about
  metadata; `TokenVerifier.ready()` is called only by tests; `auth/oidc.py` still names its
  retry gap after readiness (`READY_RETRY_SECONDS`).
- **M9. CI's flake surface** for `leader-image`: three Docker Hub pulls (the Dockerfile
  frontend, the base image, `postgres:16`), Debian and PyPI at build time. (The silent
  fall-through when Postgres never becomes ready, and the missing log dump on failure, are
  fixed.)
- **M11. `db/migrate.py` `current_revision`** turns any query error into "no schema", so
  `/readyz` can say "migrations are not current" for a permissions error.
- **A request that waits for a silent database waits for ever.** No query has a time limit:
  with the database frozen, the first 15 requests hold the pool's connections until the
  database comes back, and later ones fail after the pool's 30 s. A stop now cuts them after
  10 s (`main.REQUEST_DRAIN_SECONDS`), which is why the worst-case stop is 17 s and not 7.
  A statement or command timeout on the engine would bound both (with leader chart
  follow-up F10, the pool setting).
- **A stop on a silent database logs one `Exception closing connection` traceback per pooled
  connection** (SQLAlchemy's own, when the polite close is abandoned after 1 s). Accurate,
  but noisy; terminating the connections outright would be quiet.
- **The migration guard is a tripwire** (`tests/test_migration_compatibility.py`): it reads
  `op.` calls and SQL text for removals. It does not see a new constraint that the previous
  release's writes break (the `lower(name)` unique index proposed above is one), a changed
  meaning of a value, or SQL assembled at run time. Leader chart follow-up F4 (the previous
  release's tests against the new schema) is the real check. The console's migrations
  (`packages/console/.../migrations`) have the same rule in the README and no guard.
- **Not run**: sign-in against a provider whose connection is silently dropped, in a
  cluster (measured with Docker only); a stop during a database fail-over that does not
  close connections (measured with a paused container only); a transfer longer than 10 s cut
  by a stop, and the follower's retry of it.

## Can wait

- CLI: open credentials with `O_NOFOLLOW`; map 429 to "try again"; clean
  provider error text in device sign-in messages; cap `interval` /
  `expires_in`; leader URLs with odd hosts (`a..b`, Unicode) fail later with a
  one-line error rather than at parse time.
- Upload temp file can leak if the handler is cancelled mid-cleanup.
- `outputs_verified` commits the caller's session (convention break, safe).
- Domain entries accept empty labels (`a..b`); hosted domain has no shape
  check (neither can match a real email).
- Status windows use the database clock; some follower-API tests still use
  the app clock for `available_at`.
- A refused join token answers with `WWW-Authenticate: Bearer` (harmless).
- A location disabled while its storage read fails still records the scan
  error.
- Tests: symlinked input folder; Python 3.11 junction fallback only exercised
  by a stand-in; POSIX permission tests run only in CI.
