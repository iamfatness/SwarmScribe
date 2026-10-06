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
