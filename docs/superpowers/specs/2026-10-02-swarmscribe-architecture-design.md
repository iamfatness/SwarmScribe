# SwarmScribe — Master Architecture Spec

Date: 2026-10-02
Status: Draft for owner review

## 1. Purpose

SwarmScribe transcribes a large archive of English-language recordings
(sermons, ministry events) into text, subtitles and word-level timing data,
by distributing the work across many machines.

A **leader** owns the catalogue of recordings, decides what may be processed,
and hands instructions to **followers**. Followers are machines that connect
to the leader, transcribe one recording at a time, and return the results.
Followers run as Kubernetes pods and as containers on outside machines
(volunteer or office PCs anywhere on the internet).

### Success criteria

- A recording is transcribed only if it has been explicitly marked OK to
  publish. No code path lets a follower obtain an unconsented recording.
- Every consented recording ends in exactly one of two states: completed with
  all three outputs stored, or parked as failed with a recorded reason.
  No recording is stranded by a follower or leader dying.
- A new follower joins with one command and one join token, on Kubernetes or
  on a single machine with Docker, with or without a GPU.
- Any leader replica can be killed at any time with no lost work.
- An operator can see queue depth, per-follower throughput and failures
  without reading logs.

### Non-goals for the first release

Web dashboard, speaker diarization, translation, non-English audio,
multi-tenant organisations, a native (non-container) follower installer,
live/streaming transcription.

## 2. Decisions already made

| Topic | Decision |
|---|---|
| Model | Leader/follower. Followers always dial out and pull work. |
| Follower location | Kubernetes pods and outside machines, one protocol for both. |
| Storage | Pluggable: Azure Blob Storage, Google Cloud Storage, local filesystem. |
| Leader state | Stateless leader replicas (2+); all state in Postgres. |
| Consent | Opt-in allowlist, enforced in the leader only. |
| Language | English only, pinned. |
| Hardware | GPU and CPU followers, auto-detected, overridable. |
| Stack | Python throughout; FastAPI leader; faster-whisper engine. |

## 3. System overview

```
            Postgres  <──  Leader (N stateless replicas, HTTPS API)  ──>  Storage backend
                                 ▲                                        (Azure / GCS / local)
                 dial out, pull  │
        ┌────────────────────────┴───────────────┐
   Follower pods (K8s, GPU/CPU pools)      Follower on outside machines
```

- Followers never accept inbound connections. Every exchange is a request
  from follower to leader, so outside machines work behind home NAT.
- Followers never hold storage credentials. The leader gives them
  short-lived, single-file links.
- The leader never transcribes. Followers never decide what to work on.

## 4. Repository layout

One repo, four Python packages, managed as a `uv` workspace.

```
packages/
  protocol/    swarmscribe-protocol   shared wire models, API version
  engine/      swarmscribe-engine     transcribe one file; writers
  leader/      swarmscribe-leader     API, job store, leases, consent, storage, admin CLI
  follower/    swarmscribe-follower   agent loop, container entrypoint
deploy/
  helm/swarmscribe/                   Helm chart
  compose/                            docker-compose for local and end-to-end tests
docker/
  leader.Dockerfile
  follower.Dockerfile                 CPU and CUDA variants via build arg
docs/
```

Dependency rule: `protocol` depends on nothing internal. `engine` depends on
nothing internal. `leader` depends on `protocol`. `follower` depends on
`protocol` and `engine`. The leader never imports the engine, so the leader
image carries no model or GPU libraries.

## 5. Components

### 5.1 Protocol (`swarmscribe-protocol`)

Pydantic models for every request and response in section 6, plus the
`segments.json` schema. Carries `PROTOCOL_VERSION`. Both sides import the same
models, so the wire format cannot drift.

### 5.2 Engine (`swarmscribe-engine`)

Turns one local audio/video file into three outputs. No networking, no
knowledge of jobs or leases.

Interface:

```python
transcribe(path: Path, settings: TranscribeSettings) -> Transcript
write_outputs(transcript: Transcript, out_dir: Path) -> OutputFiles  # txt, srt, segments.json
resolve_device(preference: DevicePreference) -> DeviceChoice
```

Fixed behaviour (these are the hard-won settings and are not optional):

- `language="en"`; no language detection.
- `condition_on_previous_text=False` — prevents the repeated-sentence loop.
- Temperature ladder clamped to `(0.0, 0.2, 0.4)` — prevents gibberish on
  noisy audio.
- VAD filter on; word timestamps on.
- Glossary (ministry, place and people names) passed as the initial prompt.

Device resolution, when set to auto:

| Machine | Model | Compute type |
|---|---|---|
| CUDA GPU | `large-v3` | `float16` |
| CPU | `distil-large-v3` | `int8` |

The model loads once per follower process and is reused across jobs. Audio is
decoded with the PyAV bundled in faster-whisper; no system ffmpeg is required.

`segments.json` contains: schema version, source checksum, duration, model,
compute type, every setting used, engine version, and segments each with
start, end, text and words (`start`, `end`, `word`, `probability`).

### 5.3 Leader (`swarmscribe-leader`)

A stateless FastAPI service. Sub-units, each behind its own interface:

- **Job store** — all Postgres access. Owns the job state machine (section 7).
- **Consent** — decides whether a recording may become a job (section 8).
- **Storage backend** — lists recordings and issues download/upload links
  (section 9).
- **Auth** — join tokens, follower credentials, admin credentials
  (section 10).
- **Follower API** — the endpoints in section 6.
- **Admin API + CLI** — ingest, status, retry, revoke.
- **Reaper** — returns expired leases to the queue. Runs in every replica;
  safe to run concurrently because it is a single conditional `UPDATE`.

### 5.4 Follower (`swarmscribe-follower`)

A single-process agent:

1. Resolve device, load model.
2. Register with the leader.
3. Loop: claim → download → transcribe → upload → submit, heartbeating on a
   background thread throughout.
4. On `SIGTERM`: finish the current job if it can within the grace period,
   otherwise release it; then deregister.

One job at a time per follower process. Scale by running more followers.
Working files live in a scratch directory that is wiped after every job and
on startup.

## 6. Leader–follower protocol

HTTPS, JSON, all paths under `/v1`. The follower sends its protocol version
on register; the leader rejects incompatible versions with a clear error.

| Call | Purpose | Key response fields |
|---|---|---|
| `POST /v1/followers/register` | Exchange join token + capabilities for a follower credential | `follower_id`, `credential`, `heartbeat_interval`, `lease_seconds` |
| `POST /v1/jobs/claim` | Ask for work | `job_id`, `lease_id`, `download_url`, `upload_urls`, `settings`, `glossary`, `source_checksum`; or `204` with `retry_after` |
| `POST /v1/jobs/{id}/heartbeat` | Extend lease, report progress | `directive`: `continue` \| `cancel` \| `drain` |
| `POST /v1/jobs/{id}/submit` | Report outputs uploaded, with checksums | `accepted` |
| `POST /v1/jobs/{id}/fail` | Report a failure with reason and whether it is retryable | — |
| `POST /v1/jobs/{id}/release` | Hand a job back unfinished (shutdown) | — |
| `POST /v1/followers/deregister` | Clean exit | — |

How the leader gives instructions:

- **What to do and how** — the claim response carries the model, compute
  type, settings and glossary. Followers apply what they are given; they hold
  no transcription policy of their own.
- **Stop** — `cancel` in a heartbeat response: abandon the job, wipe scratch.
- **Wind down** — `drain` in a heartbeat response: finish the current job,
  claim nothing further, deregister.

Capabilities sent on register: device (`cuda`/`cpu`), GPU name and memory,
models available, engine version, pool name. The leader matches jobs to
followers by pool and device.

Every call that changes a job carries `lease_id`. A call with a stale
`lease_id` is rejected, so a follower that lost its lease cannot overwrite the
work of the follower that took over.

`submit` is idempotent: repeating it with the same `lease_id` and checksums
returns `accepted` again.

## 7. Job lifecycle

```
            ingest (consented only)
                    │
                    ▼
   ┌──────────►  queued  ──claim──►  leased  ──submit──►  completed
   │                                   │
   │   lease expired / release /       │
   └─── retryable fail (attempts < N) ─┤
                                       │
                 attempts = N, or      ▼
                 non-retryable     failed (parked for a human)

   any non-terminal state ──admin/consent withdrawn──►  cancelled
```

- **Claim** selects one `queued` job matching the follower's pool and device
  using `SELECT … FOR UPDATE SKIP LOCKED`, so concurrent leader replicas never
  hand out the same job.
- **Lease** has an expiry. Heartbeats extend it. Defaults: heartbeat every
  30 s, lease 120 s.
- **Attempts** are counted per job. Default maximum is 3. Each attempt records
  the follower, start, end and outcome.
- **Completed** requires all three output checksums. The leader verifies the
  objects exist in storage before accepting.
- **Failed** jobs stay failed until an admin retries them.

### Data model (Postgres)

| Table | Holds |
|---|---|
| `recordings` | storage key, size, checksum, duration, consent state, consent source |
| `jobs` | recording, state, pool, required device, attempt count, current lease, settings profile |
| `job_attempts` | job, follower, lease id, started, ended, outcome, failure reason |
| `followers` | id, pool, capabilities, credential hash, last seen, state (`active`/`draining`/`revoked`) |
| `join_tokens` | hash, pool, expiry, max uses, revoked |
| `settings_profiles` | model, compute type, engine settings, glossary |
| `audit_log` | actor, action, subject, time, detail |

Schema changes go through Alembic migrations, run as a Helm pre-upgrade job.

## 8. Consent

Consent is opt-in and lives only in the leader.

- An allowlist is a `consent.txt` at the root of a storage location: one glob
  pattern per line, `#` comments allowed.
- On ingest, a recording becomes a job only if a pattern matches it. All
  other recordings are recorded as `not_consented` and never queued.
- There is no flag, setting or API call that queues a recording without a
  matching allowlist entry.
- Re-ingest re-evaluates consent. If a recording no longer matches, any
  queued or leased job for it is cancelled and its outputs are flagged for
  deletion; the admin CLI lists them and deletes on confirmation.
- Every consent decision is written to the audit log with the pattern that
  matched.

## 9. Storage

One interface, three implementations.

```python
class StorageBackend(Protocol):
    def list_recordings(self, prefix: str) -> Iterator[RecordingRef]: ...
    def read_text(self, key: str) -> str | None: ...           # consent.txt, glossary
    def download_link(self, key: str, ttl: timedelta) -> Link: ...
    def upload_link(self, key: str, ttl: timedelta) -> Link: ...
    def exists(self, key: str) -> ObjectInfo | None: ...
    def delete(self, key: str) -> None: ...
```

| Backend | Download / upload link |
|---|---|
| Azure Blob Storage | Per-blob SAS URL, read-only or write-only, short expiry |
| Google Cloud Storage | V4 signed URL, GET or PUT, short expiry |
| Local filesystem | URL on the leader itself, carrying an HMAC-signed, expiring, single-key token; the leader streams the file |

A `Link` is a URL, an HTTP method and any required headers. The follower
treats all three backends identically.

Outputs are written to a separate output location (which may use a different
backend from the input), mirroring the input key structure:
`<key>.txt`, `<key>.srt`, `<key>.segments.json`.

The local backend requires all leader replicas to see the same path
(a shared volume in Kubernetes).

## 10. Security

- **Transport** — TLS for all follower and admin traffic, terminated at the
  ingress.
- **Join tokens** — created by an admin, scoped to one pool, with expiry and
  a maximum number of uses. Revocable. Stored hashed.
- **Follower credentials** — issued on register, one per follower, stored
  hashed, individually revocable. A revoked follower's leases are released
  immediately.
- **Admin access** — separate credentials from followers; the admin API is
  not reachable with a follower credential.
- **Least privilege** — a follower can only act on a job it currently holds
  the lease for, and only receives links for that job's one input and three
  outputs.
- **Data handling on followers** — scratch wiped after every job and at
  startup; no audio or transcript text in logs.
- **Audit** — register, claim, submit, fail, revoke, ingest, consent
  decisions and admin actions are all logged with actor and time.
- **Secrets** — database URL, storage credentials and the link-signing key
  come from Kubernetes Secrets; never baked into images.

## 11. Operations

- **Metrics** (Prometheus, `/metrics` on leader and follower): queue depth by
  pool, jobs by state, lease expiries, attempts per job, claim latency,
  per-follower audio-seconds processed per wall-second, failures by reason.
- **Logs** — structured JSON, with `job_id`, `follower_id` and `lease_id` on
  every line where they apply.
- **Health** — `/healthz` (process alive) and `/readyz` (database reachable,
  migrations current) on the leader; liveness on the follower tied to the
  heartbeat thread.
- **Admin CLI** (`swarmscribe-admin`):
  `ingest`, `status`, `jobs list|retry|cancel`,
  `followers list|drain|revoke`, `tokens create|revoke`,
  `consent report`.

## 12. Deployment

### Images

- `swarmscribe-leader` — slim Python image, no model libraries.
- `swarmscribe-follower:cpu` and `swarmscribe-follower:cuda` — same code,
  different base image. Models download on first start into a cache volume,
  or can be baked in with a build arg for air-gapped clusters.

### Helm chart

- Leader `Deployment`, 2+ replicas, `PodDisruptionBudget`, `Service`,
  `Ingress`.
- Migration `Job` as a pre-upgrade hook.
- Follower `Deployment` per pool (at least `gpu` and `cpu`), with node
  selectors, tolerations and GPU resource requests on the GPU pool.
- Followers autoscale on queue depth for their pool (KEDA `ScaledObject`
  reading the leader's metric), including scale to zero when the queue is
  empty.
- `terminationGracePeriodSeconds` on followers long enough to finish or
  release a job.
- Postgres is external by default (managed service); an optional in-chart
  Postgres exists for evaluation only.

### Outside machines

Same follower image:

```
docker run -e SWARMSCRIBE_LEADER_URL=… -e SWARMSCRIBE_JOIN_TOKEN=… swarmscribe-follower:cpu
```

Add `--gpus all` and the `cuda` tag for a GPU machine.

## 13. Failure handling

| Failure | Result |
|---|---|
| Follower crashes or loses network mid-job | Lease expires; job returns to `queued`; attempt recorded |
| Follower finishes after its lease was reassigned | `submit` rejected for stale `lease_id`; its uploads are overwritten by the current holder |
| Leader replica dies | Other replicas continue; followers retry with backoff |
| All leaders down | Followers keep transcribing, retry heartbeat and submit with backoff; if the lease expired meanwhile, the job is redone |
| Postgres down | Leader reports not ready; followers back off; no state lost |
| Corrupt or undecodable recording | Follower reports non-retryable `fail`; job parked as `failed` |
| Upload succeeds but `submit` is lost | Follower retries `submit`; it is idempotent |
| Source file changed since ingest | Checksum mismatch on download; follower fails the job as non-retryable; re-ingest creates a fresh job |
| Follower revoked mid-job | Next heartbeat is rejected; follower wipes scratch and exits |

## 14. Testing

- **Protocol** — model round-trip and schema snapshot tests; a snapshot change
  forces a deliberate version decision.
- **Engine** — unit tests for writers and device resolution with a fake
  model; one opt-in smoke test running `tiny.en` on a few seconds of real
  audio.
- **Leader** — tests run against a real Postgres. Covers the state machine,
  concurrent claims (many simultaneous claimers, each job handed out once),
  lease expiry, stale-lease rejection, consent evaluation and withdrawal, and
  each storage backend (Azurite and a GCS emulator for the cloud backends).
- **Follower** — agent loop against a fake leader and fake engine, including
  `cancel`, `drain` and `SIGTERM`.
- **End to end** — docker-compose with a leader, Postgres and two followers
  using `tiny.en`: ingest a small consented set plus one unconsented file,
  kill a follower mid-job, and assert every consented file completes and the
  unconsented file is never downloaded.
- **Chart** — `helm lint` and template rendering in CI; install on a `kind`
  cluster with the CPU follower.

## 15. Build order

Each step gets its own spec, plan and implementation cycle.

1. **Protocol + engine** — wire models, `segments.json` schema, transcriber,
   writers, device resolution. Usable on its own as a single-file CLI.
2. **Leader** — schema and migrations, job store, consent, storage backends,
   follower and admin APIs, reaper, admin CLI.
3. **Follower + images** — agent loop, Dockerfiles, compose end-to-end test.
4. **Deployment** — Helm chart, autoscaling, metrics dashboards, runbook.

Interfaces in sections 5, 6 and 9 are fixed by this document. Later specs
refine internals; a change to those interfaces means revising this spec first.
