# SwarmScribe — Leader Spec

Date: 2026-10-02
Status: Draft for owner review
Parent: `2026-10-02-swarmscribe-architecture-design.md` (the master spec).
This document details build step 2. Where it differs from the master spec,
this document wins and the master spec is updated to match (section 14).

## 1. Purpose

The leader owns the catalogue of recordings, decides what may be processed,
hands work to followers, collects results, and gives administrators the tools
to run all of this. It never transcribes.

### Success criteria

- A recording is transcribed only if a location's consent allowlist matches
  it. No API call, setting or admin command can queue anything else.
- Every consented recording ends `completed` with three verified outputs, or
  `failed` with a reason. Killing any leader replica or any follower at any
  moment loses no work and strands no job.
- Two leader replicas never hand the same job to two followers at once.
- A new storage location is usable with one admin command and its
  credentials, on Azure Blob Storage, Google Cloud Storage or a local
  filesystem.
- Every administrative change is attributable to a named person in the audit
  log.
- An operator can see queue depth, throughput and failures, and get a ranked
  list of words to add to a vocabulary, without reading logs.

### Out of scope

Web dashboard; multi-tenancy; storage event notifications (Event Grid,
Pub/Sub); follower implementation (build step 3); Helm chart (build step 4);
non-English audio.

## 2. Decisions

| Topic | Decision |
|---|---|
| Admin authentication | OIDC single sign-on with Entra ID and Google; device-code login; roles from Entra groups, Google Groups, or email/domain lists |
| Follower authentication | Join token exchanged for a per-follower credential |
| Ingest | Scheduled scan per location (default 15 min) plus on-demand |
| Vocabulary scope | Global (stored in the leader) plus per-location (files in the location), merged |
| Source integrity | Pinned to the storage object's own version marker, not a leader-computed hash |
| Background work | Runs inside every replica, one replica at a time per loop via Postgres advisory locks |
| Stack | Python 3.11+, FastAPI, SQLAlchemy 2 (async, asyncpg), Alembic, Pydantic v2 |
| Delivery | Two plans: A (working core, local storage), B (cloud storage, vocabulary, metrics) |

## 3. Components

One package, `packages/leader` → `swarmscribe-leader`, depending on
`swarmscribe-protocol` only.

```
swarmscribe_leader/
  app.py            FastAPI app factory, lifespan starts background loops
  config.py         settings from environment (section 11)
  db/               SQLAlchemy models, session factory, Alembic migrations
  jobs/             job store: state machine, claim, lease, reaper
  ingest/           scanner, consent evaluation
  vocabulary/       file parsing (from protocol), merging, versions, report, requeue
  storage/          StorageBackend protocol + local, azure, gcs implementations
  auth/             follower tokens, OIDC validation, roles
  api/follower.py   /v1 follower endpoints
  api/admin.py      /v1/admin endpoints
  api/links.py      local-backend file serving (signed links)
  audit.py          audit log writer
  metrics.py        Prometheus metrics
  admin_cli/        swarmscribe-admin (device-code login, HTTPS client)
```

Each sub-package has one responsibility and is tested on its own. The API
modules are thin: they authenticate, validate, call one service function and
map its result to HTTP.

## 4. Data model (Postgres)

All timestamps are `timestamptz` in UTC. Every table has `id` (UUID) and
`created_at`.

| Table | Columns (besides id, created_at) |
|---|---|
| `storage_locations` | `name` (unique), `backend` (`local`/`azure`/`gcs`), `config` (JSON, non-secret: container/bucket, account, root path), `secret_ref` (name of the secret holding credentials), `input_prefix`, `output_location_id` (FK, may be self), `output_prefix`, `scan_interval_s` (default 900), `enabled`, `vocabulary_version` (int, default 0), `vocabulary_hash`, `last_scan_at`, `last_scan_error` |
| `recordings` | `location_id`, `key`, `size`, `source_version` (ETag / generation / local size+mtime), `consent` (`consented`/`not_consented`/`withdrawn`), `consent_pattern`, `first_seen_at`, `last_seen_at`, `missing` (bool) — unique (`location_id`, `key`) |
| `jobs` | `recording_id`, `source_version`, `state` (`queued`/`leased`/`completed`/`failed`/`cancelled`), `pool`, `required_device` (`cuda`/`cpu`/`any`), `priority` (int, default 0), `attempts`, `max_attempts` (default 3), `lease_id`, `leased_by`, `lease_expires_at`, `vocabulary_version`, `settings_profile_id`, `failure_reason`, `completed_at`, `outputs_flagged_for_deletion` (bool) |
| `job_attempts` | `job_id`, `follower_id`, `lease_id`, `started_at`, `ended_at`, `outcome` (`completed`/`failed`/`expired`/`released`/`cancelled`), `reason` |
| `job_results` | `job_id`, `source_sha256`, `txt_sha256`, `srt_sha256`, `segments_sha256`, `duration_s`, `engine_version`, `vocabulary_terms_used` (JSON), `corrections_applied` (JSON), `low_confidence_words` (JSON, section 8.5) |
| `followers` | `pool`, `capabilities` (JSON), `credential_hash`, `state` (`active`/`draining`/`revoked`/`gone`), `registered_at`, `last_seen_at`, `join_token_id` |
| `join_tokens` | `token_hash`, `pool`, `expires_at`, `max_uses`, `uses`, `revoked`, `created_by` |
| `settings_profiles` | `name`, `model`, `compute_type`, `temperatures` (JSON) — one default profile per device class |
| `vocabularies` | `scope` (`global` or a `location_id`), `version`, `terms` (JSON), `corrections` (JSON), `content_hash`, `created_by` — the history; never updated in place |
| `audit_log` | `actor` (person's subject + email, or `follower:<id>`, or `system`), `action`, `subject_type`, `subject_id`, `detail` (JSON) |

Normally one job exists per (recording, source_version); `vocabulary
requeue` and `jobs retry` add further ones. A changed file gets a new job;
the old one is cancelled if still open.

## 5. Protocol changes

The protocol has not shipped; `PROTOCOL_VERSION` stays `1` and the schema
snapshot is regenerated.

- `ClaimResponse.source_checksum` is replaced by `source_version: str`, an
  opaque marker. The follower does not interpret it; the download `Link`
  already pins it (section 9).
- A claim with no work returns HTTP `204` with a `Retry-After` header in
  seconds. No body, no model.
- `SubmitRequest.checksums` gains `source: str`, the SHA-256 the follower
  computed of the downloaded recording. All checksums are lowercase hex
  SHA-256, enforced by a pattern on every checksum field.
- New `FailRequest.code`: `source_changed` | `undecodable` | `engine_error` |
  `out_of_resources` | `other`. `retryable` stays; the leader treats
  `source_changed` and `undecodable` as non-retryable whatever the flag says.
- `POST /v1/followers/deregister` takes no body.
- The vocabulary file parser (`parse_terms`, `parse_corrections`, the rules
  from engine CLI and master spec 16.3 item 5) moves into
  `swarmscribe_protocol.vocabulary_files`. The engine CLI keeps its own copy
  (the engine imports nothing internal); a shared set of test cases in
  `packages/protocol/tests/vocabulary_file_cases.json` is run against both.

## 6. Follower API

All under `/v1`, JSON, HTTPS. A follower authenticates with
`Authorization: Bearer <credential>` on every call except register.

| Call | Behaviour |
|---|---|
| `POST /followers/register` | Validates the join token (exists, not revoked, not expired, uses < max), increments uses, creates the follower in the token's pool, returns `RegisterResponse` with a new credential shown once. Rejects an incompatible `protocol_version` with `409`. |
| `POST /jobs/claim` | In one transaction: select one `queued` job whose pool matches and whose `required_device` is `any` or the follower's device, ordered by `priority` desc then `created_at`, using `FOR UPDATE SKIP LOCKED`; set `leased`, new `lease_id`, `lease_expires_at = now + lease_seconds`, increment `attempts`; insert a `job_attempts` row; build links (section 9) and the merged vocabulary for the job's location. Returns `ClaimResponse`, or `204` + `Retry-After: 10`. A `draining` follower always gets `204`. |
| `POST /jobs/{id}/heartbeat` | Requires the current `lease_id` and that the caller holds it, else `409`. Extends the lease. Returns `directive`: `cancel` if the job was cancelled, `drain` if the follower is draining, else `continue`. |
| `POST /jobs/{id}/submit` | Requires the current lease. Verifies the three output objects exist in storage and their sizes are non-zero; records `job_results`; sets `completed`; closes the attempt. Idempotent: the same lease and checksums again returns `accepted`. A different lease returns `409`. |
| `POST /jobs/{id}/fail` | Requires the current lease. Closes the attempt with the reason. Non-retryable code, or `attempts >= max_attempts` → `failed`; otherwise → `queued`. |
| `POST /jobs/{id}/release` | Requires the current lease. Closes the attempt as `released` and re-queues without counting the attempt. |
| `POST /followers/deregister` | Releases any lease the follower holds; state `gone`. |

Errors use one body shape: `{"code": "...", "message": "..."}` with `401`
(bad credential), `403` (revoked), `404`, `409` (stale lease or version
mismatch), `422` (validation).

Every follower call updates `followers.last_seen_at`.

## 7. Job lifecycle and the reaper

States and transitions are those of master spec section 7, with
`release` returning a job to `queued` without counting an attempt.

The **reaper** runs every 15 s under advisory lock `reaper`:

- Leased jobs past `lease_expires_at`: close the attempt as `expired`;
  `failed` with reason `lease expired too many times` if `attempts >=
  max_attempts`, else `queued`.
- Followers not seen for 10 minutes with no lease: state `gone`.

All transitions are single conditional `UPDATE`s that check the expected
current state and `lease_id`, so a reaper running while a submit arrives
cannot corrupt a job: whichever commits first wins and the other sees zero
rows updated.

## 8. Ingest, consent and vocabulary

### 8.1 Scanner

Runs every 30 s under advisory lock `scanner`, and picks each enabled
location whose `last_scan_at + scan_interval_s` has passed. `ingest
<location>` from the admin CLI requests an immediate scan. One scan:

1. Read `consent.txt`, `vocabulary.txt` and `corrections.txt` from the
   location root (absent files are empty).
2. Parse them. A parse error stops the scan for that location, records
   `last_scan_error`, and is shown by `status`; jobs already queued are
   unaffected.
3. Update the location's merged vocabulary (8.4).
4. List objects under `input_prefix` with recognised audio/video extensions
   (`.mp3 .m4a .aac .wav .flac .ogg .opus .wma .mp4 .m4v .mov .mkv .webm`),
   upsert `recordings`, mark unseen ones `missing`.
5. Evaluate consent per recording (8.2).
6. Create a job for every consented recording whose current
   `source_version` has no job other than `cancelled` ones (a `failed` job is
   not retried by scanning; an admin retries it).
7. Cancel open jobs for recordings now missing, withdrawn or changed.
8. Write one audit entry summarising the scan (counts, not file names).

### 8.2 Consent

As master spec section 8. Glob patterns use `fnmatch` semantics on the key
relative to `input_prefix`, case-sensitive, `/` as separator, `**` matching
any depth. A recording that was `consented` and no longer matches becomes
`withdrawn`: open jobs are cancelled, completed outputs are flagged, and
`consent report` lists them for deletion on confirmation.

### 8.3 Settings profiles

Two default profiles are seeded: `cuda` (`large-v3`, `float16`) and `cpu`
(`distil-large-v3`, `int8`), ladder `(0.0, 0.2, 0.4)`. The claim carries the
profile matching the follower's device. Admins can edit profiles; the fixed
transcription behaviour is not part of a profile and cannot be changed.

### 8.4 Vocabulary

- **Global** vocabulary: set with `vocabulary global set --terms FILE
  --corrections FILE` (operator role), parsed with the shared parser, stored
  as a new `vocabularies` row.
- **Location** vocabulary: the location's `vocabulary.txt` and
  `corrections.txt`, read on every scan.
- **Merging** for a location: terms = location terms then global terms,
  de-duplicated case-insensitively keeping the first; corrections = location
  corrections then global corrections, a global correction whose `heard`
  (normalised as in the parser) is already defined by the location is
  dropped.
- **Version**: the merged content is hashed; when the hash differs from
  `storage_locations.vocabulary_hash`, the location's `vocabulary_version`
  increments and the merged content is stored. A change to the global
  vocabulary therefore bumps every location's version at its next scan, or
  immediately with `vocabulary sync`.
- Each job records the vocabulary version it was claimed with.

### 8.5 Report and requeue

At submit the leader reads the job's `segments.json` once (it already
verifies the outputs exist) and stores in `job_results` the vocabulary terms
used, the corrections applied, and the 200 lowest-probability words (word
text normalised to its core, probability, count).

- `vocabulary report [--location L] [--since DATE]` aggregates these:
  - lowest-confidence words ranked by occurrences, each with a suggested
    corrections line skeleton `heard => ` for the operator to complete;
  - vocabulary terms that never appeared in any transcript;
  - how often each correction fired.
- `vocabulary requeue [--location L] [--only-affected]` re-queues completed
  recordings whose job used an older vocabulary version. `--only-affected`
  limits it to recordings whose stored low-confidence words or applied
  corrections include a `heard` text added or changed since that version.
  Requeue creates new jobs; previous results stay until the new job
  completes.

## 9. Storage

```python
class StorageBackend(Protocol):
    async def list(self, prefix: str) -> AsyncIterator[ObjectInfo]   # key, size, version
    async def read_text(self, key: str) -> str | None
    async def stat(self, key: str) -> ObjectInfo | None
    def download_link(self, key: str, version: str, ttl: timedelta) -> Link
    def upload_link(self, key: str, ttl: timedelta) -> Link
    async def delete(self, key: str) -> None
```

| Backend | Version marker | Download link pins the version by | Upload link |
|---|---|---|---|
| Azure Blob | ETag | `If-Match: <etag>` header in `Link.headers` (storage answers `412` if changed) | user-delegation SAS, write-only, `x-ms-blob-type: BlockBlob` header |
| GCS | object generation | `generation=<n>` parameter inside the V4 signed URL | V4 signed `PUT` URL |
| Local | `<size>-<mtime_ns>` | the leader's own link checks size and mtime before streaming | leader link accepting `PUT` |

A follower that receives `412` (or a size mismatch on local) reports
`fail` with code `source_changed`.

- **Azure** credentials: managed identity or a service principal via
  `DefaultAzureCredential`; links are user-delegation SAS so no account key
  is held.
- **GCS** credentials: workload identity or a service-account key; URLs are
  signed through IAM `signBlob` when no private key is present.
- **Local**: links are `https://<leader>/v1/files/<token>`; the token is an
  HMAC-SHA256 over (location, key, method, version, expiry) with the
  `SWARMSCRIBE_LINK_KEY` secret. All replicas must mount the same path.
- Link lifetime: download 30 min, upload 2 h (long recordings on CPU).
- Outputs go to `output_location` under `output_prefix + key` with the
  suffixes `.txt`, `.srt`, `.segments.json`.

## 10. Authentication and roles

### Followers

- Join tokens: 32 random bytes, URL-safe base64, shown once, stored as
  SHA-256. Created by `tokens create --pool P --expires 7d --max-uses N`.
- Credentials: 32 random bytes, shown once to the follower, stored as
  SHA-256, looked up by hash.
- `followers revoke <id>`: state `revoked`, lease released immediately, all
  further calls `403`.

### Administrators

Two identity providers are supported from the first release, and both may be
configured at once: **Microsoft Entra ID** and **Google**. Each is an OIDC
issuer with its own client registration.

- **Token.** `swarmscribe-admin` signs in with the provider's device-code flow
  and sends the provider's **ID token** as `Authorization: Bearer <JWT>`.
  (Google access tokens are opaque, so ID tokens are the one format both
  providers share.) The CLI refreshes the ID token with the refresh token
  before it expires.
- **Validation.** The leader accepts a token only from a configured issuer:
  signature against that issuer's JWKS (cached, refreshed on an unknown
  `kid`), `iss`, `aud` equal to the configured client ID, `exp`, `nbf`, 60 s
  clock skew. Entra tokens must also carry the configured tenant (`tid`);
  Google tokens must carry `email_verified=true` and, if configured, an
  allowed hosted domain (`hd`).
- **Identity.** A person is identified by `(issuer, sub)`; the audit log also
  records their email.
- **Roles.** Cumulative: admin ⊃ operator ⊃ viewer. Resolved per provider:
  - *Entra ID:* the token's `groups` claim (group object IDs), mapped by
    configuration. Users in too many groups for the claim (Entra's
    "overage") are resolved through Microsoft Graph `getMemberObjects` with
    the leader's own app credentials.
  - *Google, groups preferred:* when a Google service account with
    group-read permission is configured, the leader reads the person's
    Google Groups through the Cloud Identity Groups API and maps group
    emails to roles.
  - *Google, fallback:* when no service account is configured, or as an
    addition, roles come from configured email-address and domain lists per
    role.
  - Role lookups are cached for 5 minutes per person. A person with no role
    is refused with `403`.

| Role | May |
|---|---|
| viewer | `status`, `jobs list`, `followers list`, `vocabulary report`, `consent report` |
| operator | viewer + `ingest`, `jobs retry/cancel/priority`, `followers drain`, `vocabulary global set`, `vocabulary sync`, `vocabulary requeue` |
| admin | operator + `locations add/edit/disable`, `tokens create/revoke`, `followers revoke`, `profiles edit`, `consent delete-flagged` |

- `swarmscribe-admin login [--provider entra|google]` uses that provider's
  device-code flow (public client) and caches the tokens in
  `~/.config/swarmscribe/credentials.json` with owner-only permissions,
  refreshing silently.
- Every admin endpoint writes an audit entry with the issuer, `sub` and
  email.

## 11. Configuration

Environment variables (Kubernetes Secrets for the secret ones):

| Variable | Meaning |
|---|---|
| `SWARMSCRIBE_DATABASE_URL` | Postgres URL (secret) |
| `SWARMSCRIBE_PUBLIC_URL` | external base URL, used in local links |
| `SWARMSCRIBE_LINK_KEY` | HMAC key for local links (secret, ≥ 32 bytes) |
| `SWARMSCRIBE_ENTRA_TENANT_ID`, `…_ENTRA_CLIENT_ID`, `…_ENTRA_CLIENT_SECRET` (secret, for Graph overage) | Entra ID sign-in |
| `SWARMSCRIBE_GOOGLE_CLIENT_ID`, `…_GOOGLE_CLIENT_SECRET` (secret), `…_GOOGLE_HOSTED_DOMAIN`, `…_GOOGLE_SERVICE_ACCOUNT` (secret, optional) | Google sign-in and group lookup |
| `SWARMSCRIBE_ROLE_<VIEWER/OPERATOR/ADMIN>_ENTRA_GROUPS`, `…_GOOGLE_GROUPS`, `…_EMAILS`, `…_DOMAINS` | role mapping |
| `SWARMSCRIBE_LEASE_SECONDS` (120), `…_HEARTBEAT_SECONDS` (30), `…_MAX_ATTEMPTS` (3) | job timing |
| storage secrets | referenced by each location's `secret_ref`, read from env or mounted files |

The leader refuses to start if a required variable is missing, the link key
is too short, or migrations are not current.

## 12. Operations

- `/healthz` (process alive), `/readyz` (database reachable, migrations
  current, OIDC metadata fetched at least once).
- `/metrics` (Prometheus): jobs by state and pool, queue depth, claim latency,
  lease expiries, attempts, failures by code, follower count by state and
  pool, scan duration and errors per location, audio-seconds completed.
- Structured JSON logs with `job_id`, `follower_id`, `lease_id`,
  `location`; never transcript text, never credentials or links.
- `swarmscribe-admin status` summarises queue, followers, locations and
  last scan errors.
- Dashboards and alerts belong to build step 4.

## 13. Testing

- **Unit:** job state machine, consent globbing, vocabulary merging and
  versioning, link signing and verification, role mapping, JWT validation
  (with a locally generated key and a fake JWKS).
- **Database:** every job-store function against real Postgres, including
  50 concurrent claimers over 20 jobs asserting each job is leased once, and
  reaper-versus-submit races.
- **API:** FastAPI test client over a real database: every endpoint's
  success and error paths, idempotent submit, stale-lease rejection,
  revoked-follower rejection, role enforcement on every admin endpoint.
- **Storage:** the local backend directly; Azure against Azurite; GCS against
  fake-gcs-server; a shared contract test suite run against all three.
- **Shared parser:** the protocol parser and the engine CLI parser both pass
  `vocabulary_file_cases.json`.
- **End to end (Plan A):** Docker Compose with Postgres, two leader replicas
  and two scripted fake followers on a local location with consented and
  unconsented files; one follower is killed mid-job; asserts every consented
  file completes once, the unconsented file is never linked, and killing a
  leader replica mid-run changes nothing.
- CI runs Postgres as a GitHub Actions service container.

## 14. Changes to the master spec

Applied in the same commit as this document:

- Section 2: admin authentication is OIDC; ingest is scheduled scan plus
  manual; vocabulary is global plus per-location.
- Sections 6, 9, 13: `source_checksum` → `source_version`; version pinning
  replaces leader-side hashing; `Retry-After` header; failure codes.
- Section 16.1–16.2: vocabulary scope and versioning per section 8.4 here.

## 15. Delivery

**Plan A — working core.** Package and configuration; schema and migrations;
job store, claim, leases and reaper; follower API; join tokens and follower
credentials; OIDC validation and roles; local storage backend and signed
links; scanner and consent; settings profiles; protocol changes (section 5)
including the shared parser; admin API and CLI for `login`, `status`,
`locations add`, `ingest`, `jobs`, `followers`, `tokens`; audit log; health
endpoints; Compose end-to-end test.

**Plan B — reach and insight.** Azure and GCS backends with the storage
contract suite; vocabulary global set, merge, versioning, sync, report and
requeue; `job_results` low-confidence capture; `consent report` and
deletion; Prometheus metrics.

At the end of Plan A a leader runs against a local folder with real
followers. Plan B adds cloud storage and the improvement loop.
