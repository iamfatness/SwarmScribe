# SwarmScribe — Follower Spec

Date: 2026-10-04
Status: Owner rulings of 2026-10-04 applied (section 13); draft for owner review
Parents: `2026-10-02-swarmscribe-architecture-design.md` (the master spec),
`2026-10-02-leader-design.md` (the leader spec).
Roadmap: item 4 (follower agent and container images); it prepares item 6
(Helm chart and autoscaling).

This document details build step 3 of the master spec. The master spec is
the authority. Where this document has to differ from it, the difference is
written out in section 15 as an amendment to the master spec, which takes
effect when this document is approved.
Where the specs and the leader's code differ, the code is what the follower
talks to; those differences are listed in section 14.

## 1. Purpose

The follower is the only part of SwarmScribe that transcribes. It dials out
to a leader, takes one recording at a time, transcribes it with the engine,
uploads three outputs and reports the result. Until it exists, nothing
transcribes in a cluster.

### Success criteria

- A machine joins with one command and one join token, on Kubernetes or as a
  single machine, with or without a GPU (master spec 1).
- A follower that dies, loses its network, is drained, revoked or stopped at
  any moment strands no job: the job completes elsewhere or is parked as
  failed with a reason.
- A long transcription never loses its lease while the follower is healthy
  and the leader is reachable.
- A follower that cannot do the work (no usable device, missing GPU
  libraries, missing model) finds out before it claims a job, not after.
- No audio, transcript text, link, join token or credential is ever written
  to a log, and nothing of a job is left on disk after it ends.
- The same agent code runs in the CPU image, the CUDA image and installed
  directly on Linux and Windows.
- A Kubernetes pool keeps registering followers for as long as it exists,
  without anyone renewing a secret, and without the leader's `followers`
  table growing with every pod that starts.
- A drained follower stops for good: it exits when it has nothing left to
  do, and no restart brings it back as an active follower.

### Out of scope

Autoscaling on queue depth (KEDA) and the leader's chart: roadmap item 6.
Leader-side metrics: Plan B. Speaker diarization, non-English audio, live
transcription (master spec non-goals). More than one job at a time in one
process. A graphical or packaged (MSI, deb) installer. Resuming a
half-finished transcription after a restart: a released or expired job is
redone from the start.

## 2. What the master spec already fixes

Restated only; not reopened here.

| Topic | Decision | Where |
|---|---|---|
| Direction | Followers always dial out and pull work; no inbound connections | 2, 3 |
| Shape | One process, one job at a time; scale by running more followers | 5.4 |
| Loop | Resolve device, load model, register, then claim → download → transcribe → upload → submit, heartbeating on a background thread | 5.4 |
| Policy | The follower holds no transcription policy; it applies the claim's settings and vocabulary | 6, 16 |
| Storage | No storage credentials; one download link and three upload links per job; a `Link` is a URL, a method and headers, the same for every backend | 3, 9 |
| Leases | Every job call carries `lease_id`; a stale lease is refused | 6 |
| Directives | `cancel`: abandon and wipe. `drain`: finish, claim nothing further | 6 |
| Shutdown | On `SIGTERM` finish if it fits the grace period, otherwise release; then deregister | 5.4 |
| Scratch | Wiped after every job and at startup | 5.4, 10 |
| Devices | CUDA → `large-v3`/`float16`; CPU → `distil-large-v3`/`int8`; auto-detected, overridable | 5.2 |
| Model | Loaded once per process, reused across jobs | 5.2 |
| Images | `swarmscribe-follower:cpu` and `:cuda`, same code; models download on first start into a cache volume, or are baked in with a build arg | 12 |
| Kubernetes | A follower `Deployment` per pool with node selectors, tolerations and GPU requests; a termination grace period long enough to finish or release | 12 |
| Outside machines | The same image with `docker run`; a native installer is a non-goal (amended by ruling R4: section 15) | 1, 12 |
| Operations | JSON logs with `job_id`, `follower_id`, `lease_id`; `/metrics`; liveness tied to the heartbeat thread | 11 |
| Failures | Table in master spec 13 | 13 |
| Package | `packages/follower` → `swarmscribe-follower`, depending on `protocol` and `engine` only | 4 |

## 3. Decisions

Each row is a decision this document makes, with its reason. Rows marked
**(R*n*)** rest on an owner ruling recorded in section 13.

| # | Topic | Decision | Why |
|---|---|---|---|
| D1 | Threads | Three threads: a supervisor (main thread: signals, state), a worker (the job), a lease keeper (heartbeats). The health listener, when on, is a fourth. Synchronous `httpx`, no asyncio | The engine call blocks; a signal handler only runs on the main thread, so the main thread must never be inside the engine |
| D2 | Lease renewal | The lease keeper heartbeats every `heartbeat_interval` from the register response for as long as a job is held, independent of what the worker is doing | Download, model load, transcription and upload can each outlast a lease |
| D3 | Interrupting the engine | The engine gains an optional `progress` callback, called after every segment; raising from it stops the transcription | `cancel`, revocation and shutdown must be able to stop a job; the engine has no way to today |
| D4 | Concurrency | One job per process (master spec). One process per GPU on a multi-GPU machine, each with `CUDA_VISIBLE_DEVICES`, its own state and scratch folder | CTranslate2 already uses the whole device; a second job would only split it |
| D5 | Model lifecycle | One model in memory. The device's default model is loaded and exercised before registering; a claim that names another model or compute type unloads it and loads that one, and it stays loaded | Fail before claiming, never after; profiles are per device, so switches are rare |
| D6 | Warm-up | After every model load the follower runs a one-second inference that bypasses the VAD filter | GPU libraries load lazily at the first inference, not at model load; a missing cuBLAS must not be found on a real job |
| D7 | Credential | Stored in a file in the state folder, reused on every start. On Kubernetes the state folder is a memory-backed `emptyDir`: a container restart reuses the credential, a new pod registers again, with the pool's pool token **(R1)** | One code path; a crash loop must not register again on every restart |
| D8 | Revocation | A `403` on any leader call: abandon the job, wipe scratch, keep the credential file, exit with code 4. Never re-register on its own | If the follower deleted the file and re-registered, revocation would undo itself |
| D9 | Drain | The leader tells a draining follower so on every claim (a header on the `204`, section 12.3). The follower finishes its job, then exits 0 and keeps its credential, so a restart finds the same draining registration and exits again without registering. Under a supervisor that restarts whatever exits (Kubernetes) it parks instead: it stays up and claims nothing | An idle follower could not tell "draining" from "no work"; and a drain must survive a restart |
| D10 | Shutdown | Finish the job only when the estimated time left fits the configured grace; otherwise stop the engine, `release`, wipe, deregister. A second signal releases at once | No grace period covers the longest job; `release` does not count an attempt |
| D11 | Unfit follower | A claim this machine cannot serve (model not available offline, compute type unsupported): `release` the job, deregister, exit with code 3 | Failing would spend the job's attempts on one machine's fault; staying would claim the same job again |
| D12 | Scratch | One folder per job under a scratch root the follower owns (marker file); wiped at start, after every job and on exit. The follower refuses a non-empty scratch root without its marker | A wrong path must never delete someone's files |
| D13 | Models | Fetched from Hugging Face into a cache folder by default; that stays the default for `docker run`. On Kubernetes models are baked into the image. Offline mode never touches the network **(R3)** | Master spec 12, and a pod must start without Hugging Face |
| D14 | Model names | A claim's model must be a plain name or `owner/name`; never a filesystem path. An optional allowlist restricts further | The model string comes over the wire and is otherwise passed to a loader that accepts paths |
| D15 | GPU libraries | cuBLAS and cuDNN come from the `nvidia-*-cu12` wheels through a `cuda` extra, in the image and in a native install alike. Linux finds them through `LD_LIBRARY_PATH`; Windows copies the DLLs next to `ctranslate2.dll` with `swarmscribe-follower setup-cuda` | One source of the libraries on every platform; the Windows method is the one the 2026-10 benchmark proved |
| D16 | Images | One Dockerfile, targets `cpu` and `cuda`, `python:3.12-slim-bookworm` pinned by digest, user 10001, read-only root, three writable mounts (state, scratch, models) | The console image's conventions (C4a) |
| D17 | Kubernetes | A standalone chart `deploy/helm/swarmscribe-follower`, one release per pool, in plan F3. Autoscaling stays in roadmap item 6 | The leader has no chart yet; a follower chart only needs a leader URL |
| D18 | Health and metrics | An optional HTTP listener serving `/healthz` and `/metrics`; loopback in the image, pod IP in the chart, off in a native install | Kubelet probes and Prometheus need it; outside machines must not open a port |
| D19 | Logs | JSON lines on stderr by default, `text` on request. The `httpx` and `httpcore` loggers are raised to `WARNING` | Master spec 11; `httpx` logs every request URL at `INFO`, and link URLs are secrets |
| D20 | Long jobs | When a link is refused as expired, the follower asks the leader for fresh ones for its lease (`POST /v1/jobs/{id}/links`, section 12.2) and carries on **(R2)** | Links last 2 hours from the claim; a slow machine or a long recording outlasts them |
| D21 | Outside machines | Docker stays the documented default; a native install (`uv tool install`, a systemd unit, a Windows service) is supported **(R4)** | Office and volunteer PCs are mostly Windows without Docker |
| D22 | Memory guard | Before transcribing, the follower compares an estimate of the job's memory with the memory it may use and fails the job `out_of_resources` when it cannot fit. Built in F2, with measured figures | An out-of-memory kill looks like a lease expiry three times, hours apart |
| D23 | Follower rows | A follower that registers with a pool token takes over the row of a `gone` follower of the same token (section 12.5) | Pods come and go; the `followers` table must stay the size of the pool |
| D24 | New admin routes | Pool tokens and settings profiles are managed by a person signed in to the leader, never through a fleet console; they are left out of the console's allow-list on purpose (section 12.7) | A pool token is the most powerful follower secret; and the console has no page for either yet |
| D25 | Job ids | A claim whose job id is not a UUID is ignored entirely | The id becomes a scratch folder name and a URL path segment |

## 4. Components

One package, `packages/follower` → `swarmscribe-follower`, depending on
`swarmscribe-protocol` and `swarmscribe-engine`. Third-party: `httpx` and
`pydantic-settings`; `prometheus-client` joins in F2. No `fastapi`, no
database driver.

```
swarmscribe_follower/
  main.py         CLI: run, join, leave, doctor (F1); setup-cuda, cuda-paths, service (F4)
  config.py       settings from the environment (section 4.1)
  errors.py       exit codes
  agent.py        supervisor: states, signals, exit codes
  leader.py       LeaderClient: one method per route, retries, error mapping
  lease.py        LeaseKeeper: the heartbeat thread
  job.py          JobRunner: one job from claim to submit
  transfer.py     download and upload through a Link
  models.py       ModelHost: load, warm up, switch
  device.py       device, GPU name and memory, cached models
  scratch.py      the scratch root and per-job folders
  credentials.py  the credential file
  health.py       /healthz and /metrics (F2)
  logs.py         JSON logging, redaction
  windows.py      DLL placement, service control handler (F4)
```

Each module has one job and is tested alone. `agent`, `lease` and `job` take
their clock, sleeper, `LeaderClient` and `ModelHost` as arguments, so tests
run without time passing and without a model.

**Engine additions** (the engine still imports nothing internal):

- `Transcriber.transcribe(..., progress: Callable[[float], None] | None = None)`.
  Called after each segment with the fraction done, 0.0 to 1.0. In a split
  recording the left channel covers 0.0–0.5 and the right 0.5–1.0. An
  exception raised by the callback leaves `transcribe` unchanged; it is
  never turned into `UndecodableAudioError`.
- `Transcriber.warm_up()`: one second of silence through the model with the
  VAD filter off, result discarded.
- `Transcriber.close()`: drops the model so its memory is returned before
  another is loaded.

### 4.1 Configuration

Environment variables. The first two keep the names the master spec's
`docker run` line uses.

| Variable | Default | Meaning |
|---|---|---|
| `SWARMSCRIBE_LEADER_URL` | required | The leader's base URL. Must be `https` unless `SWARMSCRIBE_FOLLOWER_ALLOW_HTTP=1` (development and Compose tests); loopback is no exception, so one rule covers the leader and its links |
| `SWARMSCRIBE_JOIN_TOKEN` | — | Secret: a join token or a pool token (section 12.1). Read only when there is no stored credential |
| `SWARMSCRIBE_JOIN_TOKEN_FILE` | — | A file holding the token (a mounted Secret); wins over the variable |
| `SWARMSCRIBE_LEADER_CA_FILE` | — | PEM certificates trusted in addition to the public roots, for calls to the leader and its links |
| `SWARMSCRIBE_FOLLOWER_POOL` | `default` | The pool name reported in capabilities. The token decides the real pool |
| `SWARMSCRIBE_FOLLOWER_DEVICE` | `auto` | `auto`, `cuda` or `cpu` |
| `SWARMSCRIBE_FOLLOWER_STATE_DIR` | image: `/var/lib/swarmscribe-follower`; native: the user's data folder | Credential file and lock file |
| `SWARMSCRIBE_FOLLOWER_SCRATCH_DIR` | image: `/scratch`; native: `<state>/scratch` | Working files |
| `SWARMSCRIBE_FOLLOWER_MODEL_DIR` | image: `/models`; native: the Hugging Face default | Model cache |
| `SWARMSCRIBE_FOLLOWER_OFFLINE` | `0` | `1`: never download a model |
| `SWARMSCRIBE_FOLLOWER_ALLOWED_MODELS` | — | Comma-separated; when set, only these models are loaded |
| `SWARMSCRIBE_FOLLOWER_SHUTDOWN_GRACE_SECONDS` | `8` | How long a stop may wait for the current job (section 5.6) |
| `SWARMSCRIBE_FOLLOWER_ON_DRAINED` | `exit` | `exit`: stop with code 0 once drained and idle. `park`: stay up and claim nothing (the chart sets this; section 5.5) |
| `SWARMSCRIBE_FOLLOWER_ALLOW_HTTP` | `0` | `1`: accept a plain-http leader URL and plain-http file links (development and Compose tests) |
| `SWARMSCRIBE_FOLLOWER_STARTUP_MODEL` | the device default | The model loaded and exercised at start-up, before registering (5.2 step 5). A model name or `owner/name`; must be in `ALLOWED_MODELS` when that is set. With `OFFLINE=1` and the model absent from the cache the follower exits 3 and names this setting |
| `SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB` | detected | Memory the follower may use (section 5.7; F2) |
| `SWARMSCRIBE_FOLLOWER_HEALTH_ADDR` | image: `127.0.0.1:9108`; native: off | Listener for `/healthz` and `/metrics` (F2) |
| `SWARMSCRIBE_FOLLOWER_LOG_FORMAT` | `json` | `json` or `text` |

Invalid configuration prints the field names and messages (never values) and
exits with code 2, like the leader and the console.

Exit codes: `0` stopped cleanly; `2` invalid configuration, including a
state folder locked by another follower and a scratch folder that is not the
follower's; `3` this machine cannot do the work
(device, GPU libraries, model); `4` not authorised (no or invalid join
token, credential revoked); `5` protocol version refused.

## 5. Behaviour

### 5.1 The leader routes, exactly

All under `/v1`, JSON bodies from `swarmscribe_protocol`, `Authorization:
Bearer <credential>` on every call but register. This is what the leader's
code and tests do today, plus the two additions of plan F0 (the fresh-links
route and the drain header).

| Step | Request | Success | What the follower does with it |
|---|---|---|---|
| Register | `POST /followers/register` `{join_token, protocol_version, capabilities}` | `200` `{follower_id, credential, heartbeat_interval, lease_seconds}` | Stores the credential (5.3); uses both timings as given |
| Claim | `POST /jobs/claim`, no body | `200` `ClaimResponse`, or `204` with `Retry-After` (10 by default) and, for a draining follower, `X-SwarmScribe-Directive: drain` | `204`: sleep `Retry-After` plus up to 20 % jitter, claim again. `drain`: section 5.5 |
| Download | The claim's `download_url`: its method, URL and headers, no bearer | `200`, a byte stream | Streams to scratch, hashing as it writes |
| Heartbeat | `POST /jobs/{id}/heartbeat` `{lease_id, progress}` | `200` `{directive}` | Renews the lease; acts on the directive (5.5) |
| Fresh links | `POST /jobs/{id}/links` `{lease_id}` | `200` `{download_url, upload_urls}`; `429` with `Retry-After` when asked more than once a minute | Asked when a link is refused as expired (5.4) |
| Upload | Each of `upload_urls.txt`, `.srt`, `.segments_json`: its method, URL and headers, the file as the body, no bearer | any `2xx` (`201` from the leader's own links) | Uploads `txt`, `srt`, then `segments.json` |
| Submit | `POST /jobs/{id}/submit` `{lease_id, checksums: {source, txt, srt, segments_json}}` | `200` `{accepted: true}` | Job done; repeating it is safe |
| Fail | `POST /jobs/{id}/fail` `{lease_id, code, reason, retryable}` | `204` | Section 6 |
| Release | `POST /jobs/{id}/release` `{lease_id}` | `204` | The job returns to the queue; the attempt is not counted |
| Deregister | `POST /followers/deregister`, no body | `204` | Releases anything still held; the credential stays valid |

There is no "complete" route (it is `submit`) and no separate "renew" route
(the heartbeat renews). There is no follower-level heartbeat: an idle
follower is kept alive in the leader's eyes by its claim polls, each of which
updates `last_seen_at`. A follower silent for 10 minutes is marked `gone`
and becomes `active` again on its next call.

Errors are always `{"code", "message"}`. Section 6 maps every status.

The bearer credential is sent only to `<leader URL>/v1/followers/*` and
`/v1/jobs/*`. It is never sent to a link, including a link that points at
the leader. Redirects are never followed. `HTTPS_PROXY` is honoured.

### 5.2 Startup

1. Read configuration; take an exclusive lock on `<state>/follower.lock`
   (a second follower on the same state folder exits 2).
2. Start the health listener, if configured, so that a slow model download
   in step 5 is not taken for a failed start.
3. Prepare the scratch root (5.8) and wipe it.
4. Probe the device (5.9). Requested `cuda` without a usable GPU: exit 3.
5. Load the device's default model (`resolve_device`) and warm it up (D6).
   A failure here exits 3 with the reason and what to run
   (`swarmscribe-follower doctor`). Nothing has been registered or claimed.
6. Obtain a credential (5.3).
7. Enter the claim loop.

Loading before registering follows the master spec's order (5.4: "Resolve
device, load model. Register"). It costs seconds to tens of seconds from a
warm cache and a download of 1.5–3 GB from a cold one; the leader does not
know the follower exists until it can work.

### 5.3 Credential

The file `<state>/credential.json` holds `{leader_url, follower_id,
credential, device, heartbeat_interval, lease_seconds}`. POSIX: mode `0600` in a `0700` folder, and a
file or folder owned by another user or writable by others is refused, as
the admin CLI does. Windows: the folder is created under the service
account's profile and inherits its permissions; the Windows service runs as
a dedicated account.

The folder's part of that rule is applied **before registering** as well
(`run`, `join`, and reported by `doctor`): a state folder the credential
would be refused in at the next start is refused at once with exit `2`,
naming the folder, its owner and its mode, and no join token is spent
(F2a final review, I2: a root-owned, world-writable folder, which is what a
bind mount from Windows and a default `emptyDir` are, let the follower
register and then refuse its own credential at every restart). The chart
(8.2) must therefore make the state `emptyDir` the follower's own.

- **File present and for this leader and device:** use it. No join
  token is needed, and none is read.
- **File absent, or for another leader or device:** register with the
  join token or pool token. Capabilities are fixed at registration, so a changed device
  needs a new registration and therefore a token. No token: exit 4.
- **`401` on a stored credential** (the leader does not know it, for
  example after its database was replaced): register again if a join token
  is configured, once; otherwise exit 4.
- **`403`:** revoked (D8).
- **Register answers `401`:** the token is unknown, expired, revoked or used
  up. Exit 4; do not retry. **`409 protocol_version`:** exit 5.

`swarmscribe-follower join` reads the token from the environment, or from
standard input with `--token-stdin` (never an argument, so it stays out of
shell history and process lists), registers and writes the file. `run` does the same when it
finds no credential, which keeps the master spec's one-line `docker run`
working. `leave` deregisters and deletes the file.

| Where | State folder | Effect |
|---|---|---|
| Kubernetes pod | `emptyDir` with `medium: Memory` | The credential lives as long as the pod. Container restarts reuse it; a new pod registers with the pool's pool token from a Secret, and takes over the row of a pod that has gone (12.5) |
| Docker on an outside machine | A volume declared by the image (`VOLUME`) | Survives `docker restart`; a named volume (`-v swarmscribe-follower:/var/lib/swarmscribe-follower`) also survives `docker rm`, which a single-use token needs |
| Native install | The service account's data folder | Survives everything short of deleting the file |

### 5.4 One job

`JobRunner`, on the worker thread, after a `200` claim. The lease keeper
starts before step 1 and stops after step 8.

1. **Check the claim.** The job id is a UUID (D25: otherwise the claim is
   ignored and its lease left to expire). Model name allowed (D14); settings
   convertible to `TranscribeSettings`. A model this follower may not or
   cannot load: D11.
2. **Download** to `<scratch>/job-<job_id>/source` (no extension: the
   follower never learns the storage key). SHA-256 is computed while
   writing. A `Content-Length` larger than the free space fails the job
   (section 6). A body shorter than its `Content-Length` is a transport
   error and is retried from the start; the leader's links do not support
   ranges. A download refused with `403` is treated like an expired upload
   link (step 7).
3. **Model.** If the claim's model or compute type differs from the loaded
   one: close it, load the claimed one, warm it up.
4. **Memory guard** (5.7; built in F2).
5. **Transcribe** with `Transcriber.transcribe(source, vocabulary,
   settings=..., progress=...)`. The follower calls the class directly, not
   the engine's cached `transcribe()` function and not its CLI, so it sees
   the exception types. The callback records progress for the next
   heartbeat and raises `JobStopped` when the supervisor has asked the job
   to stop.
6. **Write** with `write_outputs(transcript, job_folder)`.
7. **Upload** `source.txt`, `source.srt`, `source.segments.json` to their
   links, in that order, each with its SHA-256 computed from the file on
   disk. An upload refused with `403` means the link has expired: the
   follower asks the leader for fresh links (D20, section 12.2), waiting if
   it is told `429`, and uploads all three again. If the fresh links are
   refused too, the job is released.
8. **Submit** with the three output checksums and `source` =
   `transcript.source_checksum`.
9. **Wipe** the job folder, whatever happened in steps 1–8.

A recording without speech is a normal result: the engine writes an empty
`.txt` and `.srt` and a `segments.json` with no segments, and the leader
accepts exactly that combination.

### 5.5 Lease renewal and directives

The lease keeper is a thread of its own. It sends a heartbeat every
`heartbeat_interval` seconds (30 by default, against a 120-second lease)
from the moment the claim returns until the job ends, with the latest
progress. It never waits for the worker.

CTranslate2 releases the interpreter lock while it computes, and the Python
parts of a job (hashing, corrections, writing) yield it every few
milliseconds, so the keeper is not starved. The Compose test proves this
with a real model, a 2-second heartbeat and an 8-second lease.

| Heartbeat result | Action |
|---|---|
| `continue` | Nothing |
| `drain` | Note it; the job continues. What follows the job is below |
| `cancel` | Stop the job: the engine at its next segment, a transfer at once. Wipe. Call nothing: the job is already cancelled, and `fail` or `release` would answer `409`. Claim again |
| `409 stale_lease` | The lease is gone (expired and re-leased, or released by a revocation). Same as `cancel` |
| `404` | The job no longer exists. Same as `cancel` |
| `403` | Revoked (D8) |
| `401` | Section 5.3 |
| Network error, `5xx` | The job continues. Retry after 2, 4, 8 … seconds, never more than `heartbeat_interval` apart (a longer `Retry-After` is not waited for: the lease matters more). No give-up: master spec 13 has followers keep transcribing while every leader is down. The leader does not check a lease's expiry on a heartbeat, so a late heartbeat revives a lease the reaper has not yet taken |

**Drain.** A draining follower is told so in two places: in the heartbeat
answers of the job it holds, and on every claim, whose `204` carries
`X-SwarmScribe-Directive: drain` (section 12.3). The second is what an idle
follower hears. The job in hand is finished normally. What happens then is
`SWARMSCRIBE_FOLLOWER_ON_DRAINED`:

- **`exit`** (the default). The follower deregisters and exits with code 0.
  Its credential file stays. The leader keeps the registration `draining`:
  deregistering does not end a drain, and the reaper never marks a draining
  follower gone. A restart therefore finds the stored credential, uses it,
  is told `drain` on its first claim and exits 0 again. It cannot come back
  as an active follower by itself, because it registers only when it has no
  stored credential for this leader and device. The systemd unit and the
  Docker guide restart on failure only, so exit 0 is final. To put the
  machine back to work an operator runs `swarmscribe-follower leave` and
  joins again.
- **`park`**. For a supervisor that restarts whatever exits. A Kubernetes
  `Deployment` only allows `restartPolicy: Always`: a container that exits 0
  is started again, for ever, with a growing back-off and a restart count
  that looks like a fault. The chart therefore sets `park`: the follower
  stays up and healthy, claims nothing, logs the drain once and asks again
  every 60 seconds (the leader sees it alive; a revocation is noticed). If
  the answer stops saying `drain`, it resumes. A parked pod is removed by
  deleting it or scaling down. The pod that replaces it has an empty state
  folder and registers as a new, active follower, which is what replacing a
  drained pod means.

The leader has no command that ends a drain today; `park` handles one
anyway, so that adding it later needs nothing from the follower.

The keeper knows when it last renewed. When that is longer ago than
`lease_seconds` the lease may be lost; the worker carries on, and the first
answer from the leader settles it.

Progress is sent because the protocol has the field; the leader ignores it
today. The follower uses it itself for the shutdown estimate and a metric.

### 5.6 Shutdown

Signals handled on the main thread: `SIGTERM` and `SIGINT`; on Windows also
`SIGBREAK` and the service stop control.

1. Stop claiming.
2. Idle: deregister, exit 0.
3. Busy: estimate the time left.
   - Uploading or submitting: finish.
   - Transcribing with progress `p ≥ 0.05`: `elapsed × (1 − p) / p`, plus 30
     seconds for upload and submit.
   - Otherwise: unknown, treated as too long.
4. If the estimate fits `SHUTDOWN_GRACE_SECONDS`: finish the job, deregister,
   exit 0. If it does not, or when the grace runs out: stop the engine at
   its next segment, `release`, wipe, deregister, exit 0.
5. A second signal: release at once.

If `release` cannot be delivered, the follower exits anyway: the lease
expires within `lease_seconds` and the reaper re-queues the job, at the cost
of one counted attempt.

**The grace period against the longest job.** No fixed grace period covers
the longest job. On the 2026-10 benchmark recording a GPU ran about 20 times
real time and a CPU about 6 times, so a three-hour recording is roughly 9
minutes on a GPU and 30 on a CPU, and slower machines exist. A node drain or
a rollout cannot wait that long, and a spot node gives two minutes or less.
So the rule is finish-if-it-fits, else release. What is lost is the compute
already spent on one job; the attempt is not counted, and the job is redone
from the start elsewhere.

| Where | Stop timeout | `SHUTDOWN_GRACE_SECONDS` |
|---|---|---|
| Chart default | `terminationGracePeriodSeconds: 900` | `870` |
| systemd unit | `TimeoutStopSec=930` | `900` |
| `docker run` with defaults | 10 s before `SIGKILL` | `8` (the default) |

The follower's grace is always the platform's timeout minus 30 seconds or
less, so the release and deregister calls are inside it.

### 5.7 Model lifecycle and memory

`ModelHost` holds at most one `Transcriber`, keyed by (model, compute type,
device). The key from the claim is compared with the loaded key before each
job. Channel mode, labels and the temperature ladder are per call and never
cause a reload.

Switching closes the old model first, so two models are never in memory
together. A load is a blocking call on the worker thread; the lease keeper
keeps the lease meanwhile. A model that fails to load for a reason that
belongs to this machine (not in the cache while offline, compute type
unsupported, GPU libraries missing) is D11. Out of memory while loading is
`out_of_resources` (section 6).

**Memory guard** (built in F2, with the figures measured there)**.** The engine decodes the whole recording into memory: about
230 MB per hour as mono, and for a split recording about 460 MB held and
1.2 GB at peak per hour (channel-split follow-ups). Before transcribing, the
follower reads the recording's duration from its container header and
estimates:

```
needed = host memory of the loaded model
       + duration in hours × 600 MB   (mono, or auto on a mono file)
       + duration in hours × 1200 MB  (split)
```

The mono per-hour figure and the models' own host memory are estimates to be
measured in plan F2 and written into the README's sizing table. The limit is
`SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB`, or when unset the cgroup limit
(`memory.max`), or the machine's physical memory. `needed > limit` fails the
job `out_of_resources`, retryable. A duration that cannot be read skips the
guard.

### 5.8 Scratch

- The scratch root is a folder the follower owns. It writes a marker file
  `.swarmscribe-scratch` there. At start it wipes the root's contents only
  if the marker is present or the folder is empty; otherwise it exits 2.
- One folder per job, `job-<job_id>`, holding `source` and the three
  outputs (and any `.tmp` a failed write left behind).
- Wiped: at start, after every job in a `finally`, and on exit. Wiping is
  deletion, not secure erasure; on outside machines disk encryption is the
  operator's.
- **Size.** The largest recording plus a few megabytes of outputs. Decoded
  audio is in memory, not on disk. The chart's default is an `emptyDir` with
  `sizeLimit: 20Gi`, on disk, never `medium: Memory`.
- **After a crash.** On Kubernetes the `emptyDir` survives a container
  restart and is wiped by the start-up step; it goes with the pod. In Docker
  and native installs the folder persists and is wiped at the next start.
  The crashed job's lease expires and the reaper re-queues it.

### 5.9 Device detection and what is reported

- **Device.** `resolve_device(preference)` from the engine: CUDA when
  CTranslate2 reports a device, else CPU.
- **Compute types.** Not probed separately: a compute type the device lacks
  makes the model load fail, which is exit 3 at start and D11 on a claim.
- **GPU name and memory.** `nvidia-smi --query-gpu=name,memory.total`, with a
  5-second timeout. If it is missing or fails, both are reported as absent;
  the protocol allows that.
- **Models.** The models found in the cache folder, plus the one loaded.
- **Host memory and CPU count.** Not reported: the protocol's
  `Capabilities` has no field for them and none is added. F2's memory guard
  reads the host's memory for its own use.

Reported at register, once: `{device, gpu_name, gpu_memory_mb, models,
engine_version, pool}`. The leader matches work on the token's pool and on
`device`; the rest is for people.

### 5.10 Models

- **Default: fetch.** faster-whisper downloads the model from Hugging Face
  on first use into `MODEL_DIR` (the follower sets `HF_HUB_CACHE`). A cache
  volume keeps it across restarts.
- **Baked.** The Dockerfile's `MODELS` build argument (for example
  `MODELS="large-v3"`) downloads the models at build time into `/models` in
  the image and sets `SWARMSCRIBE_FOLLOWER_OFFLINE=1`. This is the chart's
  default (ruling R3): one image tag per model, such as `cuda-large-v3`.
- **Shared cache.** A read-only volume holding the cache, mounted at
  `/models`, with offline mode on.
- **Offline (air-gapped).** With `OFFLINE=1` the follower sets
  `HF_HUB_OFFLINE=1`, loads only from the cache, and never opens a
  connection to anything but the leader and its links. A claim for a model
  that is not in the cache is D11: the pool's profile and its followers'
  models disagree, and that is an operator's mistake to fix, not a job
  failure.

Approximate sizes: `tiny.en` 75 MB, `distil-large-v3` 1.5 GB, `large-v3`
3.1 GB.

## 6. Failure handling

`fail` takes a code, a reason of at most 2000 characters and `retryable`.
The leader parks the job as `failed` when the code is `source_changed` or
`undecodable`, when `retryable` is false, or when attempts are used up;
otherwise it re-queues. `release` re-queues without counting the attempt.
A reason is the exception's class and message; it never contains transcript
text, a link or a path outside scratch.

### 6.1 Terminal for the job

| What happened | Call |
|---|---|
| `UndecodableAudioError` (not audio, no audio stream, `stereo_split` on a file that is not stereo) | `fail` `undecodable`, not retryable |
| Download answers `412`, or an error body with code `source_changed` | `fail` `source_changed`, not retryable |
| Download answers `404` | `fail` `source_changed`, not retryable (the file is gone; the next scan settles the recording) |
| Submit answers `409 outputs_inconsistent` | `fail` `engine_error`, not retryable (the engine wrote outputs that contradict each other; a retry would write them again) |
| Upload answers `413` | `fail` `other`, not retryable (an output larger than the storage accepts) |

### 6.2 Retryable for the job, on another attempt

| What happened | Call |
|---|---|
| Any other exception from the engine | `fail` `engine_error`, retryable |
| `MemoryError`, a CUDA out-of-memory error, the memory guard, no space on scratch (`ENOSPC` or a `Content-Length` larger than the free space) | `fail` `out_of_resources`, retryable |
| Submit answers `409 outputs_missing`, `checksum_mismatch` or `outputs_changed` three times, each after uploading all three outputs again | `fail` `other`, retryable |
| A `422` or `400` from a job route | `fail` `other`, retryable, and an error log: the follower and leader disagree about the protocol. If the `fail` call itself is refused, stop and let the lease expire |

### 6.3 Not the job's fault: nothing is counted

| What happened | Action |
|---|---|
| Lease lost (`409 stale_lease` on any job call or upload) | Stop, wipe, call nothing, claim again. The leader has already moved the job on |
| `cancel` directive (an admin, or consent withdrawn mid-job) | Stop, wipe, call nothing, claim again. Uploads are refused from the moment of cancellation |
| Download or upload link refused with `403` again, after fresh links were issued | `release` |
| A claim whose job id is not a UUID (D25) | Nothing at all: no folder, no request; the lease expires |
| Download fails with a network error or `5xx` for 10 minutes | `release` |
| Shutdown that does not fit the grace | `release` |
| This machine cannot serve the claim (D11) | `release`, deregister, exit 3 |

### 6.4 Retried in place

| What happened | Action |
|---|---|
| Network error, `502`, `503`, `504` on a leader call | Retry with exponential backoff from 1 s to 60 s with full jitter, honouring `Retry-After` (the leader sends 10 for an outage, 30 for unavailable storage). Claim, heartbeat and submit retry without limit; submit is idempotent |
| `500` on a leader call | As above. `submit` answered `500` five times is treated as a `422` in 6.2, so that a leader bug cannot hold a job for ever |
| `429` on the fresh-links route | Wait its `Retry-After`, ask again |
| Upload answers `409 conflict` with `Retry-After` (the file is in use) | Wait and retry |
| Upload fails with a network error or `5xx` | Retry from the start of that file, with the same backoff, until the lease is lost or a stop is asked |
| Submit answers `409 outputs_missing`, `checksum_mismatch`, `outputs_changed` | Upload all three again, then submit; three rounds (6.2) |

### 6.5 Terminal for the follower

| What happened | Action | Exit |
|---|---|---|
| `403` on a leader call (revoked) | Stop the job, wipe, call nothing (the leader released the lease when it revoked), keep the credential file | 4 |
| `401` on a stored credential, no join token | — | 4 |
| Register `401` (join token invalid, expired, used up) | — | 4 |
| Register `409 protocol_version` | — | 5 |
| Device or GPU libraries unusable at start | — | 3 |

Exit 4 and 5 must not be restarted blindly: the systemd unit sets
`RestartPreventExitStatus=4 5`. On Kubernetes they show as
`CrashLoopBackOff` with the reason as the last log line; a revoked pod stays
revoked across container restarts because its credential file is still
there (D7, D8).

### 6.6 Crashes

A follower killed outright (`SIGKILL`, power loss, a native crash in a GPU
library, an out-of-memory kill) makes no call. Its lease expires after at
most `lease_seconds`, the reaper re-queues the job and counts the attempt,
and after `max_attempts` the job is parked as `lease expired too many
times`. The next start wipes scratch.

## 7. Security

- **Transport.** TLS to the leader, verified; a private CA through
  `SWARMSCRIBE_LEADER_CA_FILE`. Plain HTTP only to loopback or with the
  explicit Compose-test switch.
- **Secrets.** The join token and the credential are never logged, never
  passed as arguments, never baked into an image. The credential file is
  owner-only (5.3).
- **Links are secrets.** A link's URL carries its token. No URL is logged;
  log lines name the step (`download`, `upload txt`) and the status. The
  HTTP library's own request logging is turned off (D19).
- **The credential stays with the leader.** It is attached only to leader
  API paths, never to a link, and redirects are not followed, so a link to a
  storage service can never receive it.
- **What the follower trusts.** The leader decides the work. The follower
  still refuses a model name that is a path (D14), a link whose method is
  not the one expected for its step, and settings the engine's own checks
  reject.
- **Data.** Recordings and transcripts exist only in the job's scratch
  folder and in memory, for the length of the job. No audio or transcript
  text in logs or in a failure reason.
- **No inbound.** No port is opened unless the health listener is
  configured, and by default it listens on loopback.
- **Container.** User 10001, read-only root, all capabilities dropped,
  `no-new-privileges`, no service-account token mounted.
- **Outside machines** receive only consented recordings; consent is the
  leader's (master spec 8). An operator who does not trust a machine with
  the audio must not give it a join token.

## 8. Deployment

### 8.1 Images

`docker/follower.Dockerfile`, multi-stage, built from the repository root,
following `docker/console.Dockerfile`: base images pinned by tag and digest,
`uv sync --frozen --no-dev --package swarmscribe-follower --no-editable`,
only `/app/.venv` copied into the final stage.

| | `swarmscribe-follower:cpu` | `swarmscribe-follower:cuda` |
|---|---|---|
| Build target | `cpu` | `cuda` |
| Base | `python:3.12-slim-bookworm` | the same |
| Extra packages | — | the `cuda` extra: `nvidia-cublas-cu12`, `nvidia-cudnn-cu12` |
| Library path | — | `LD_LIBRARY_PATH` set to the two wheels' `lib` folders |
| Needs at run time | — | the NVIDIA container runtime (`--gpus all`, or the device plugin) for the driver libraries |
| Size without a model | about 0.7 GB | about 2.5 GB |

Sizes are estimates; `check-follower-image.sh` prints the real one. The
`nvidia/cuda` base images were not chosen: the wheels give the same
libraries on a smaller base, and the same extra serves a native install
(D15).

Both images: user `10001:10001`; `VOLUME` for the state folder; writable
paths only `/var/lib/swarmscribe-follower`, `/scratch` and `/models`;
`HOME` pointed at the state folder; `ENTRYPOINT ["/usr/bin/tini", "--",
"swarmscribe-follower"]`, `CMD ["run"]`; `HEALTHCHECK` on
`http://127.0.0.1:9108/healthz`. The image has no build tools, no `uv`, no
leader package, and no `fastapi`.

The init (`tini`, Debian's package) is there because the kernel drops a
signal that PID 1 has no handler for: a `SIGTERM` in the moment before the
follower had installed its handlers was lost, and the container was killed
at the end of the stop window (F2a final review, I1). The follower installs
its handlers before it imports anything; the init covers the time the
interpreter itself needs to start, and reaps the health check's children.

`docker/check-follower-image.sh`, run in CI, checks without a GPU: the
user; that the engine and faster-whisper import; that the leader package
and its database libraries are absent; that `run` without configuration
exits 2 without a traceback; that the image runs with `--read-only
--cap-drop ALL` and tmpfs on the three paths; and, for `cuda`, that
cuBLAS and the cuDNN version the locked CTranslate2 needs load by name.

**The CUDA image is proven on the development machine, not in CI** (ruling
R5). GitHub's runners have no GPU. Docker Desktop (engine 29.8.1, Linux
containers) is installed on the development machine, which has an RTX 4090,
so plan F2 runs the `cuda` image there with `docker run --gpus all`, has it
transcribe a real recording from a real leader, and records the result in
the plan's outcomes. If GPU pass-through does not work in Docker Desktop, F2
says so in the README, proves the GPU path through the native Windows
install instead (the same wheels, not the same image), and the image stays
marked "not yet run on a GPU" until a Linux GPU host runs it.

### 8.2 Kubernetes

A standalone chart, `deploy/helm/swarmscribe-follower`, built in plan F3
with the console chart's conventions: `values.schema.json`, a render check
in CI, `kubeconform`, no Secret created by the chart, image named by the
operator (not published), `NetworkPolicy` on by default. One release per
pool.

- A `Deployment`. `strategy: RollingUpdate` with `maxUnavailable: 25%`,
  `maxSurge: 0` on GPU pools (a surge pod would need a GPU that is not
  there).
- **GPU pool:** `resources.limits."nvidia.com/gpu": 1`, a `nodeSelector` and
  `tolerations` from values, the `cuda` image, `runtimeClassName` from
  values. **CPU pool:** CPU and memory requests and limits, the `cpu` image,
  `OMP_NUM_THREADS` set to the CPU limit.
- Memory sizing guidance in the values file: the model's host memory plus
  1.2 GB per hour of the longest split recording the pool will see (5.7).
  The chart passes the memory limit as `SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB`.
- Volumes: state (`emptyDir`, `medium: Memory`, small), scratch
  (`emptyDir`, `sizeLimit` from values), and for models nothing by default,
  because the model is baked into the image (R3); an `emptyDir` or an
  existing PVC can be chosen instead.
- The pool token (section 12.1) from `secrets.existingSecret`, mounted as a
  file (`SWARMSCRIBE_JOIN_TOKEN_FILE`). It does not expire; there is nothing
  to rotate on a calendar.
- `SWARMSCRIBE_FOLLOWER_ON_DRAINED=park` (section 5.5).
- `terminationGracePeriodSeconds: 900`; `SHUTDOWN_GRACE_SECONDS` derived
  from it.
- Probes on the health listener: a `startupProbe` allowing 30 minutes (a
  cold model download), a `livenessProbe` on `/healthz`. No readiness
  probe and no `Service`: nothing connects to a follower except the kubelet
  and Prometheus.
- `NetworkPolicy`: egress to the leader, DNS, and (unless offline) HTTPS for
  the model download; ingress to the health port from the monitoring
  namespace only.
- `automountServiceAccountToken: false`, read-only root, capabilities
  dropped.

Things an operator must know, written into the chart's README:

- A new pod registers again with the pool token and takes over the
  `followers` row of a pod that has gone (12.5), so the table stays the size
  of the pool at its largest.
- A drained pod parks: it stays `Running` and takes no work (5.5). To
  remove it, delete it or scale down; its replacement is a new, active
  follower.
- To shut a pool out, revoke its pool token with `--revoke-followers`.
- Autoscaling on queue depth, and the choice between a `Deployment` and
  KEDA `ScaledJob`s (which never stop a busy pod on scale-down), belong to
  roadmap item 6. They need the leader's metrics, which are Plan B.

### 8.3 Outside machines

**Docker** (master spec 12), with the state volume named so the credential
survives:

```
docker run -d --restart on-failure --stop-timeout 930 \
  -e SWARMSCRIBE_LEADER_URL=https://leader.example.org \
  -e SWARMSCRIBE_JOIN_TOKEN=... \
  -e SWARMSCRIBE_FOLLOWER_SHUTDOWN_GRACE_SECONDS=900 \
  -v swarmscribe-follower:/var/lib/swarmscribe-follower \
  -v swarmscribe-models:/models \
  swarmscribe-follower:cpu
```

`--gpus all` and the `cuda` tag for a GPU machine. The master spec's shorter
line still works, with a 10-second stop and a credential that lasts as long
as the container.

**Native** (D21, ruling R4):

- **Install.** `uv tool install swarmscribe-follower` (or `pip install`
  into a virtual environment), with `swarmscribe-follower[cuda]` on a GPU
  machine. Python 3.11 or later. Until the packages are published, from a
  wheel built from the repository.
- **Join and check.** `swarmscribe-follower join --leader URL`, then
  `swarmscribe-follower doctor`, which prints the device, compute types,
  cached models, host memory and whether the leader answers, and runs the
  warm-up. `doctor` is the same code as start-up steps 4 and 5.
- **Linux service.** A systemd unit shipped in
  `deploy/systemd/swarmscribe-follower.service`: a dedicated user,
  `Restart=on-failure`, `RestartPreventExitStatus=4 5`, `TimeoutStopSec=930`,
  `StateDirectory=swarmscribe-follower`, `ProtectSystem=strict`,
  `NoNewPrivileges=yes`, and an `EnvironmentFile` readable by root only. For
  the `cuda` extra the unit sets `LD_LIBRARY_PATH` from
  `swarmscribe-follower cuda-paths`.
- **Windows service.** `swarmscribe-follower service install` registers a
  Windows service running as a dedicated local account, with the same
  restart rules and the stop control wired to the shutdown path. On a GPU
  machine `swarmscribe-follower setup-cuda` copies the cuBLAS and cuDNN DLLs
  from the `nvidia-*-cu12` wheels next to `ctranslate2.dll`, and `doctor`
  proves the result with a real inference. `setup-cuda` must be run again
  after an upgrade that replaces CTranslate2; start-up says so when the
  warm-up fails for a missing DLL.

## 9. Observability

- **Logs.** One JSON object per line on stderr: `time`, `level`, `event`,
  `message`, and `job_id`, `lease_id`, `follower_id` wherever they apply.
  Events: started, device, model loaded, registered, claimed, each step's
  duration and byte count, heartbeat failures (first and recovery, not every
  retry), directive received, job outcome with its code, shutdown decision
  with the estimate. Never: audio, transcript text, URLs, tokens,
  credentials, vocabulary terms.
- **Metrics**, on `/metrics` when the listener is on:
  `swarmscribe_follower_jobs_total{outcome}`,
  `…_audio_seconds_total`, `…_transcribe_seconds_total` (the two give the
  master spec's audio-seconds per wall-second), `…_job_progress`,
  `…_model_load_seconds`, `…_heartbeat_failures_total`,
  `…_download_bytes_total`, `…_upload_bytes_total`,
  `…_state` (idle, working, draining, stopping).
- **Health.** `/healthz` answers `200` while the supervisor has ticked in
  the last 30 seconds and, during a job, the lease keeper has run its loop
  within three heartbeat intervals. It reports that the threads are alive,
  not that the leader answered: a leader outage must not make Kubernetes
  kill a pod that is transcribing.
- **Outside machines** cannot be scraped. What an operator sees of them is
  what the leader records: last seen, leases, attempts and failure reasons,
  and with Plan B the leader's own metrics.

## 10. Testing

- **Unit, with a fake leader.** An in-process fake speaking the protocol
  through `httpx.MockTransport`, scripted per test; a fake `Transcriber`
  that calls the progress callback on demand; a fake clock and sleeper.
  Covers: the whole happy path and the order of calls; `204` back-off;
  heartbeat timing; every row of section 6; `cancel`, `drain`, `stale_lease`
  and `403` arriving during download, transcription and upload; the three
  shutdown outcomes and the second signal; model switch and D11; scratch wiping after success, failure and a simulated crash, and
  the refusal of an unmarked folder; the credential file's every branch in
  5.3; and that no log line of any test contains a URL, a token or the
  fake transcript's text.
- **Contract, against the real leader.** The agent with a stub engine
  against the leader's real application, served in the test process on a
  loopback port (the follower's client is synchronous, so it is given real
  HTTP), with a real Postgres from the leader's test kit
  (`packages/leader/tests/leader_testkit.py`, section 12.9). No model is
  needed. This keeps the fake leader honest: join and complete every
  consented recording, drain heard while idle and kept across restarts,
  park, revocation mid-job, release on shutdown, cancellation, pool-token
  pods reusing one row, and every route's status and headers once. The
  follower package's tests import the leader for this; the package itself
  never does.
- **Engine.** The progress callback's fractions for mono and split, an
  exception from the callback passing through unchanged, `warm_up` and
  `close` with a fake model; and in the opt-in smoke test, with `tiny.en`.
- **Compose, real leader and real model** (`e2e/follower-compose`, in CI):
  Postgres, two leader replicas behind nginx, and two `cpu` follower
  containers with `tiny.en` baked into a test-only image. The driver adds a
  location with consented and unconsented files (the engine's speech
  fixture among them, mono and split), sets the CPU profile to `tiny.en`
  directly in the database (no admin command edits profiles yet), and
  asserts: every consented recording completes once with outputs that
  validate against the protocol schema; the unconsented one is never
  linked; a follower killed mid-job loses nothing; a follower stopped with
  `SIGTERM` releases its job without a counted attempt; a drained follower
  takes nothing more; a revoked follower exits 4 and does not come back;
  heartbeats keep an 8-second lease alive through a real transcription. The
  leader's `SWARMSCRIBE_PUBLIC_URL` must be the proxy's name on the Compose
  network, since followers fetch the leader's own links from inside
  containers.
- **Image.** `check-follower-image.sh` for both targets.
- **Chart.** Lint, schema validation and a render check in CI, as for the
  console chart. F3 also installs the chart on a local `kind` cluster with
  the `cpu` image: kind, Helm and kubeconform run on the development machine
  as standalone binaries, as they did for the console chart.
- **CUDA image.** Run on the development machine's GPU in F2 (section 8.1).
- **Windows.** A CI job on `windows-latest` running the follower's unit
  tests (signal handling, credential file, paths). GPU on Windows is checked
  by `doctor` on a real machine.

## 11. Build plans

Each ends in software that works on its own.

1. **F0 — Leader, protocol and engine changes**
   (`plans/2026-10-04-follower-f0-leader-and-engine.md`). Everything in
   section 12: the engine's `progress`, `warm_up` and `close`; the protocol's
   fresh-links models and drain header; leader migration `0006`; pool tokens
   and follower-row reuse; the fresh-links route; the drain signal; the
   settings-profile commands; the leader's test kit. *Result:* a leader that
   every existing test and scripted follower still works against, and that
   an agent can be built against.
2. **F1 — Agent core** (`plans/2026-10-04-follower-f1-agent-core.md`). The
   package; configuration; `LeaderClient` with the whole error mapping;
   credential file; lease keeper; job runner; scratch; model host; drain,
   revocation and shutdown; logs; `run`, `join`, `leave`, `doctor`. Unit
   tests with a fake leader, and the contract tests against the real leader
   with a stub engine. *Result:* `swarmscribe-follower run` on a development
   machine, against a leader on the same machine, transcribes a local folder
   end to end on the CPU.
3. **F2 — Images and Compose.** `docker/follower.Dockerfile` with both
   targets and the `MODELS` argument; the health listener and metrics; the
   memory guard with measured figures; `check-follower-image.sh`; the
   Compose test with a real model; CI jobs; the CUDA image run on the
   development machine's GPU (section 8.1). *Result:* the master spec's
   one-line `docker run` joins a real leader, and CI proves the kill, stop,
   drain and revoke cases with a real model.
4. **F3 — Kubernetes chart.** `deploy/helm/swarmscribe-follower`, its render
   checks, an install on local `kind`, and its README (pools, GPU nodes,
   baked models, the pool token, sizing). *Result:* `helm install` per pool
   against any reachable leader.
5. **F4 — Outside machines.** The systemd unit, the Windows service,
   `setup-cuda` and `cuda-paths`, the Windows CI job, the install guide
   (ruling R4). *Result:* a Windows or Linux PC joins with one command and
   survives a reboot.

F0, F1 and F2 are the master spec's build step 3. F3 is the follower's share
of step 4. The master spec's end-to-end test (14) is F2's Compose test.

## 12. Leader, protocol and engine changes (plan F0)

`PROTOCOL_VERSION` stays 1: the protocol has not shipped. The schema
snapshot is regenerated. The leader's schema moves to migration `0006`.

### 12.1 Pool tokens (ruling R1)

A pool token registers followers of one pool for as long as it is not
revoked. It has no expiry and no limit on uses. A follower presents it
exactly as it presents a join token, in `RegisterRequest.join_token`, so the
follower has one code path and the protocol does not change; the leader
looks the secret up among join tokens first, then among pool tokens.

| | Join token | Pool token |
|---|---|---|
| For | A machine a person sets up | A pool whose machines come and go by themselves |
| Expiry | 60 seconds to 90 days (7 days by default) | None |
| Uses | 1 to 10,000 (1 by default) | Unlimited; counted (`registrations`, `last_used_at`) |
| Known by | Its id | A name: unique, and never reused, because the audit log names it |
| Managed by | An administrator, also through a fleet console | An administrator signed in as a person, never through a console |
| Follower rows | A new row per registration | Takes over a `gone` row of the same token (12.5) |
| Revoking | Stops further registrations | The same; `--revoke-followers` also revokes every follower it registered |
| Stored | `join_tokens`, SHA-256 only | `pool_tokens`, SHA-256 only |

- **Create.** `POST /v1/admin/pool-tokens` `{name, pool}` → `201`
  `{id, name, pool, token}`. The token is shown once. `409 exists` for a
  name that was ever used; `422` for a bad name or pool.
  `swarmscribe-admin pool-tokens create --name gpu-pods --pool gpu`.
- **List.** `GET /v1/admin/pool-tokens` → name, pool, registrations, last
  use, whether and by whom revoked, creator. Never the token.
  `swarmscribe-admin pool-tokens list`.
- **Revoke.** `POST /v1/admin/pool-tokens/{name}/revoke`, optional body
  `{revoke_followers: true}` → the token's view plus `followers_revoked`.
  Revoking twice keeps the first revocation. `404` for an unknown name.
  `swarmscribe-admin pool-tokens revoke gpu-pods [--revoke-followers]`.
- **Role.** `admin`, and a person: a console credential is refused with
  `403` whatever its cap (like console credentials themselves).
- **Audit.** `pool_token.create` `{name, pool}`; `pool_token.revoke`
  `{name, followers_revoked}`; `pool_tokens.view` for a listing; each
  follower revoked with the token gets its own `follower.revoke` entry. A
  registration's `follower.register` entry gains `pool_token` (the name) and
  `reused`. The token itself is in no log, audit entry or listing.

A join token keeps registering exactly as before. The stored capabilities
now always carry the token's pool, not the pool the follower claimed.

### 12.2 Fresh links (ruling R2)

`POST /v1/jobs/{job_id}/links`, body `{lease_id}`.

- **Who.** The follower that holds the job's current lease, checked exactly
  as a heartbeat is: any other caller, a wrong or stale `lease_id`, or a
  job that is no longer leased (cancelled, completed, re-queued) gets `409
  stale_lease`; a revoked follower `403`; an unknown job `404`.
- **What.** `200` `{download_url, upload_urls}`: the job's download link and
  three upload links, issued now with their full lifetimes and bound to the
  same lease, so they stop working when the lease ends, as the claim's do.
  Links issued earlier keep working until their own expiry.
- **Not a heartbeat.** The lease is not extended.
- **Bound.** At most one issue per lease per
  `SWARMSCRIBE_LINKS_REFRESH_MIN_SECONDS` (60 by default); the claim counts
  as the first. Sooner is `429 too_many_requests` with `Retry-After`. The
  time of the last issue is kept on the job (`links_issued_at`).
- **Audit.** `job.links`, actor `follower:<id>`, subject the job, detail
  `{attempt}`. A refused request records nothing. No link is ever recorded.
- **Storage down.** `503` as for a claim, and nothing is recorded, so the
  retry is not bounded out.

### 12.3 The drain signal

A claim answered `204` to a draining follower carries
`X-SwarmScribe-Directive: drain` beside `Retry-After`. The header name is a
constant of the protocol package. It was chosen over an idle heartbeat (a
new route, and a second thing for an idle follower to poll) and over a body
(a `204` has none; changing the status would break every existing caller).
The follower's side is section 5.5.

### 12.4 Settings profiles

The leader spec lists `profiles edit`; no command existed, so a profile
could only be changed in the database.

- `GET /v1/admin/profiles` (viewer) → for each device: model, compute type,
  temperature ladder. `swarmscribe-admin profiles list`.
- `POST /v1/admin/profiles/{device}` `{model, compute_type, temperatures?}`
  (admin) → the profile. `swarmscribe-admin profiles set cpu --model tiny.en
  --compute-type int8 [--temperatures 0,0.2,0.4]`.
- A model is a plain name or `owner/name`, never a path (the same rule the
  follower applies, D14). A compute type is one CTranslate2 knows. A ladder
  is 1 to 10 values between 0.0 and 0.4; left out, the ladder is kept.
- Applies to every job claimed afterwards; a job already leased keeps what
  it was given.
- Audit: `profile.set` with the new values and the model and compute type
  before; `profiles.view`.
- People only, like pool tokens (12.7).

### 12.5 Follower rows

Rows in `followers` were never deleted, and every pod start added one. A
registration with a pool token now first looks for a follower that the same
pool token registered, is `gone`, and holds no lease, and takes its row
over: new credential hash, new capabilities, `active`. The old credential
stops working at that moment (`401`); if its machine is in fact alive, it
registers again and gets another row. Rows that are `active`, `draining` or
`revoked` are never taken over, nor are rows a join token registered: an
outside machine keeps its identity however long it is switched off. The
table therefore stays the size of the pool at its largest. Attempts and
audit entries of the previous holder stay attached to the row's id; the
`follower.register` entry with `reused: true` marks where one machine ends
and the next begins.

### 12.6 The public URL

No code change. The leader builds its own links from
`SWARMSCRIBE_PUBLIC_URL`, so that URL must be one every follower can reach:
the ingress address for outside machines and for pods alike. F2's Compose
test sets it to the proxy's name on the Compose network; the contract test
in F1 sets it to the loopback address the leader is served on. The README
says so where the setting is described.

### 12.7 The fleet console

The console's proxy allow-list (`packages/console/src/swarmscribe_console/
proxy.py`) and the web app's role map (`packages/console-web/src/api/
roles.ts`) are checked against the leader's admin router: every route a
console may be delegated must be in both. The five new routes are declared
for people only (`consoles_allowed=False`), which the check treats as
deliberately excluded, as it does console credentials. Neither file changes.
Offering pool tokens and profiles in the console is a later console change,
with pages for them.

### 12.8 Engine

- `Transcriber.transcribe(..., progress=None)`: called after every segment
  with the fraction done; a split recording reports the left channel as the
  first half and the right as the second. Whatever the callback raises stops
  the transcription and reaches the caller unchanged, never as
  `UndecodableAudioError`.
- `Transcriber.warm_up()`: one second of silence through the model with the
  VAD filter off.
- `Transcriber.close()`: drops the model; the transcriber cannot be used
  afterwards.

### 12.9 The leader's test kit

`packages/leader/tests/leader_testkit.py`: plain functions (no fixtures,
which do not cross packages) to create and migrate a test database, empty
it, queue recordings through a real scan, and make a join token. The
follower's contract test uses it; so can the Compose driver.

## 13. Owner rulings (2026-10-04)

The five questions this document first asked are decided.

| # | Question | Ruling | Where it lands |
|---|---|---|---|
| R1 | How long a Kubernetes pool's token lives | A non-expiring, revocable pool token | 12.1, 12.5, 8.2; plan F0 |
| R2 | A job that outlasts its upload links | Fresh links on request, for the lease holder | 12.2, 5.4; plans F0 and F1 |
| R3 | Where a Kubernetes pool's models come from | Baked into the image on Kubernetes; `docker run` keeps the download default | 5.10, 8.2; plans F2 and F3 |
| R4 | A native install on outside machines | Yes: `uv tool install`, a systemd unit, a Windows service | 8.3; plan F4 |
| R5 | Who proves the CUDA image | The development machine, in F2, with `docker run --gpus all` (Docker Desktop is now installed there, RTX 4090). If GPU pass-through fails in Docker Desktop: the native Windows path, and the image is marked not yet run on a GPU | 8.1; plan F2 |

Rulings made while designing the leader's side, each the owner's to overturn:

- **Pool tokens and profiles are for people, not consoles** (D24, 12.7).
  Overturning it means adding five routes to the console's allow-list and
  the web app's role map, and pages for them.
- **A pool token is revoked by name and can take its followers with it**
  (12.1). Without `--revoke-followers`, followers already registered keep
  working, as with a join token.
- **Follower rows are reused, not deleted** (12.5). The alternative, deleting
  old `gone` rows, needs the attempts table to let go of them and loses
  outside machines that were switched off for longer than the retention.
- **A drained follower exits; on Kubernetes it parks** (5.5).
- **Fresh links are bounded to one issue a minute per lease** (12.2).

## 14. Where the specs and the leader's code differ

These are about the follower contract. The follower is designed to the code.

1. **`drain` reaches only a busy follower.** The master spec (6) delivers
   `drain` in a heartbeat response. Heartbeats exist only per job, so an
   idle draining follower sees `204` to every claim, exactly as when there
   is no work, and cannot know. Hence D9.
2. **Deregister does not end a drain, and does not end a credential.**
   Deregistering a draining follower leaves it `draining`; an `active` one
   becomes `gone` and returns to `active` on its next call. The master spec
   calls deregister a "clean exit" and has a drained follower deregister.
3. **The pool comes from the join token.** The master spec (6) has the
   follower send its pool and the leader match on it. The leader matches on
   the token's pool and ignores `capabilities.pool`, which is still a
   required field.
4. **Submit checks more than existence.** The master spec (7) says the
   leader verifies the outputs exist; the leader spec (6) says exist and are
   non-empty. The code hashes all three and compares them with the submitted
   checksums (`409 checksum_mismatch`), and it *accepts* an empty `.txt` and
   `.srt` as a no-speech result beside a `segments.json` with no segments
   (`outputs_inconsistent` otherwise). Codes `outputs_missing` and
   `outputs_changed` also exist.
5. **A stale holder cannot overwrite.** The master spec (13) says a late
   follower's uploads "are overwritten by the current holder". On the local
   backend upload links are bound to the lease and are refused with `409
   stale_lease` once it ends.
6. **More statuses than the leader spec lists.** Leader spec 6 lists `401`,
   `403`, `404`, `409`, `422`. The code also answers `412 source_changed`
   and `413 too_large` on links, `503 unavailable` with `Retry-After` (10 or
   30), `500 internal`, and `409 conflict` with `Retry-After: 5` when an
   output file is in use. An invalid join token is `401 unauthorized`; a
   protocol mismatch is `409 protocol_version`.
7. **Links cannot be renewed.** Download links last 30 minutes and upload
   links 2 hours from the claim. Neither spec says what a longer job does
   (ruling R2). The leader's own upload links also refuse more than
   512 MiB.
8. **The claim's vocabulary is always empty.** Master spec 16.2 has the
   claim carry the current vocabulary. The leader sends version 0 with no
   terms until Plan B. The follower applies whatever arrives.
9. **Progress is accepted and ignored.** The heartbeat carries `progress`;
   the leader does not store it. The engine cannot produce it today (D3).
10. **The engine's interface.** The master spec (5.2, 16.3) gives
    `transcribe(path, settings, vocabulary)`. The follower needs the
    `Transcriber` class, which has the arguments in another order
    (`transcribe(path, vocabulary, *, settings)`), and neither can be
    interrupted.
11. **A late heartbeat revives a lease.** The leader spec has heartbeats
    require the current lease; the code does not check the expiry time, only
    that the reaper has not yet taken the job. This is in the follower's
    favour and section 5.5 relies on it only as a bonus.
12. **Join tokens are capped** at 90 days and 10,000 uses, one use and seven
    days by default. The master spec says only "expiry and a maximum number
    of uses" (ruling R1).
13. **Layout and logs.** The master spec (4) names `deploy/helm/swarmscribe/`,
    `deploy/compose/` and `docker/leader.Dockerfile`. The repository has
    `deploy/helm/swarmscribe-console`, `e2e/compose` and no leader
    Dockerfile outside the test one. The master spec (11) asks for JSON
    logs; the leader logs plain text. The follower follows the master spec
    on logs (D19) and the repository on layout.

Plan F0 closes items 1 (the drain header, 12.3), 7 (fresh links, 12.2) and
12 (pool tokens, 12.1), corrects the stored pool of item 3, and gives item
10's engine a way to be interrupted (12.8). The others stand, and the
follower is built to them.

## 15. Amendments to the master spec

These amend `2026-10-02-swarmscribe-architecture-design.md`. That file is
not edited by this document; the amendments are applied to it when the owner
approves this spec. Section numbers are the master spec's.

**1. Purpose.**
- Success criterion "A new follower joins with one command and one join
  token" becomes "... one join token, or one pool token for a pool".
- Non-goals: "a native (non-container) follower installer" becomes "a
  packaged follower installer (MSI, deb)". A native install with `uv tool
  install` and a service unit is supported (ruling R4).

**4. Repository layout.** Add `deploy/helm/swarmscribe-follower/` (one
release per pool), `deploy/systemd/`, and `e2e/follower-compose/`. The
leader's chart and `docker/leader.Dockerfile` remain roadmap item 6.

**5.2 Engine.** The interface gains:

```python
class Transcriber:
    def transcribe(self, path, vocabulary=EMPTY, *, settings=None, progress=None) -> Transcript
    def warm_up(self) -> None
    def close(self) -> None
```

`progress` is called after every segment with the fraction done; raising
from it stops the transcription.

**5.4 Follower.** Step 1 becomes "Resolve device, load model, run one
inference". Step 4 becomes "On `SIGTERM`: finish the current job if its
estimated time left fits the grace period, otherwise release it; then
deregister." Add: "5. When drained and idle: exit with code 0 and keep the
credential; under a supervisor that restarts whatever exits, stay up and
claim nothing."

**6. Leader–follower protocol.**
- The claim row's response becomes: "`job_id`, ...; or `204` with a
  `Retry-After` header and, for a draining follower, `X-SwarmScribe-Directive:
  drain`".
- New row: "`POST /v1/jobs/{id}/links` | Fresh links for the lease holder,
  when a job outlasts its links | `download_url`, `upload_urls`; `429` with
  `Retry-After` when asked more than once a minute".
- `submit` answers `200 {accepted: true}`; `fail`, `release` and
  `deregister` answer `204`.
- "Wind down" becomes: "`drain` in a heartbeat response, and in a header on
  every `204` claim: finish the current job, claim nothing further. The
  follower then exits and keeps its credential, so that a restart finds
  itself still draining; the registration stays `draining` in the leader."
- "Capabilities sent on register": the pool is the token's; the pool a
  follower reports is informational.
- Register accepts a join token or a pool token in `join_token`.

**7. Data model.** Add table `pool_tokens` (name, hash, pool, registrations,
last used, revoked at and by, created by). `followers` gains
`pool_token_id`; `jobs` gains `links_issued_at`.

**10. Security.** After "Join tokens": "**Pool tokens** — created by an
administrator signed in as a person, scoped to one pool, without expiry or
a limit on uses, for pools whose machines register by themselves.
Revocable, optionally together with every follower they registered. Stored
hashed, named, never reused." "Follower credentials": add "A revoked
follower exits and does not register again by itself."

**11. Operations.** Admin CLI: add `pool-tokens create|list|revoke` and
`profiles list|set`.

**12. Deployment.**
- Images: "Models download on first start into a cache volume with `docker
  run`; on Kubernetes they are baked into the image (the chart's default),
  which also serves air-gapped clusters."
- Helm chart: the follower `Deployment` per pool is its own chart,
  `swarmscribe-follower`, registering with a pool token from a Secret and
  parking when drained.
- Outside machines: add the native install (`uv tool install
  swarmscribe-follower`, a systemd unit, a Windows service).

**13. Failure handling.**
- "Follower finishes after its lease was reassigned": "`submit` rejected for
  stale `lease_id`; its uploads are refused, so they cannot overwrite the
  current holder's".
- "Follower revoked mid-job": "Every call is answered `403`; the follower
  wipes scratch and exits with a code that tells its supervisor not to
  restart it; it does not register again by itself".
- New row: "A job outlasts its links | The follower asks for fresh links for
  its lease and carries on".
- New row: "Follower drained while idle | The next claim's `204` says
  `drain`; the follower exits (or parks on Kubernetes) and stays drained
  across restarts".
- New row: "A pool's pods start and stop | Each registers with the pool
  token and takes over the row of a pod that has gone".

**15. Build order.** Step 3 becomes "Follower + images: leader and engine
changes (F0), agent core (F1), images and Compose (F2)". Step 4 gains the
follower chart (F3) and the outside-machine install (F4).

**Amendments after the F1 final review.**
- **Start-up model (5.2 step 5, D5, 5.10).** Step 5 loads the model named by
  `SWARMSCRIBE_FOLLOWER_STARTUP_MODEL`, which defaults to the device's default
  model. F2's images set it from the baked model (the image's `MODELS`
  argument), so that an offline follower that holds one model starts with it;
  `doctor` uses the same setting.
- **Start-up order (5.2).** The cheap checks come before the model load: the
  settings, the state folder's lock, the scratch folder, and that there is a
  stored credential for this leader or a token to register with (exit 4
  otherwise). The signal handlers are installed before all of it (5.6), so a
  stop during start-up ends the process; a model load itself cannot be
  interrupted, and the stop is seen the moment it returns, before anything is
  registered.
- **Plain http (4.1).** `https` is required unless `ALLOW_HTTP=1`, loopback
  included. A link whose scheme the follower's own settings refuse is a
  follower misconfiguration: the job is released (not failed) and the follower
  exits 2.
- **Registration refusals (4.1).** Exit 5 is for a protocol refusal only; any
  other refusal of the registration (a 404 from a wrong URL, a 403 from a proxy)
  exits 2.

**Amendments after F2** (measured and built in plans F2a and F2b, 2026-10-05).
- **GPU libraries (D15, 8.1).** The `cuda` extra and image carry cuBLAS only
  (`nvidia-cublas-cu12`, and `nvidia-cuda-nvrtc-cu12` which it depends on). CTranslate2 4.8.2
  does not load cuDNN. `LD_LIBRARY_PATH` names the one wheel's `lib` folder. The image check
  loads `libcublas.so.12` by name.
- **The `cuda` image's device (8.1, 5.9).** The `cuda` image sets
  `SWARMSCRIBE_FOLLOWER_DEVICE=cuda`: without a usable GPU it exits 3 at start-up step 4
  where `auto` would use the CPU.
- **Image sizes (8.1).** Without a model: `cpu` 789 MB, `cuda` 2548 MB; with `tiny.en` baked, `cpu` 938 MB; with `large-v3` baked, `cuda` 8486 MB (as `docker image inspect` counts them).
- **Declared volumes (8.1).** The state folder and `/scratch` are both declared; `/models`
  is not (a volume there would copy a baked model on every start).
- **The init (8.1).** `tini` is PID 1 and the follower its child (already in 8.1, from
  F2a's fix wave); the `HEALTHCHECK`'s children are reaped by it.
- **Baked models (5.10).** `MODELS` is names separated by commas, with no spaces; its first
  name is the start-up model. Models come from Hugging Face at build time, each pinned to a commit and
  to a SHA-256 per file in `docker/models.lock.json`.
- **`doctor` (8.3).** `--no-leader` leaves the leader out; a `memory:` line says what the
  follower may use.
- **Health (9).** During a job the lease keeper must have run its loop within three
  heartbeat intervals *plus about 30 seconds, the time one request to the leader may take*
  (the HTTP client's timeout is per phase, not a hard total): the
  keeper stamps its loop before it asks, and a leader that does not answer must not make
  `/healthz` fail.
- **Memory guard (5.7, D22, 8.2).** The measured figures replace the estimates:

  ```
  needed = what the process holds once the model is loaded (read from /proc on Linux;
           where the platform will not say, Windows and macOS, nothing is added)
         + 100 MiB
         + duration in hours × 3600 MiB  (mono, or auto on anything but a stereo file)
           or
         + duration in hours × 3900 MiB  (split)
  ```

  The limit is `SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB`, or when unset the smaller of the
  cgroup limit and the machine's physical memory (on Windows and macOS, the machine's total
  memory). The chart's sizing guidance (8.2) becomes:
  the model's host memory (1.7 GiB for `distil-large-v3` on a CPU; 3.1 GiB at the peak of
  loading `large-v3` onto a GPU) plus 3.8 GiB per hour of the longest recording the pool will
  see (3900 MiB; mono counts 3.5 GiB, 3600 MiB).
- **Health listener (4.1, 9).** It binds IPv6 literals too (`[::1]:9108`). `/healthz` is 200
  from the moment the follower starts supervising, so also while the model loads.
- **Compose test (10).** The profile is set with the leader's `set_profile`, not in the
  database by hand. The test also covers the health listener, `/metrics` and the memory
  guard.

**Amendments after F3** (built in plan F3, 2026-10-05; the chart is `deploy/helm/swarmscribe-follower`).
- **The pool token's value (8.2).** `poolToken.existingSecret` and `poolToken.key`, not
  `secrets.existingSecret`: the chart reads one secret. The token file is mode 0440 for the
  pod's `fsGroup` (10001). A changed Secret does not restart the pods and need not: the file
  is read only at a registration.
- **The state folder (5.3, 8.2).** `/var/lib/swarmscribe-follower/state`, a folder the
  follower creates (0700) inside the memory-backed `emptyDir`, which is itself root's
  (`0:10001`, mode `3777` with the `fsGroup`) and is refused as the state folder.
- **Probes (8.2).** The startup probe allows two minutes, not thirty: `/healthz` is 200
  while the model loads (amendment after F2), so a slow download cannot fail the start.
- **Rollouts (8.2).** `maxUnavailable: 25%` for every pool; `maxSurge` is `25%` on a CPU
  pool and 0 on a GPU pool.
- **NetworkPolicy (8.2).** Ingress to the health port is for nobody unless
  `networkPolicy.ingress.from` names peers (on most CNIs the kubelet's probes come from the
  node and are not affected; where they are, the nodes' range goes into `ingress.from` or the
  policy is turned off). A named peer shares the listener's eight connections with the
  kubelet's probe, so only trusted peers should be named. Egress is DNS, and TCP 443 and the port of `leader.url` to anywhere but
  loopback, link-local and reserved ranges; another port for the storage that serves the
  leader's file links (MinIO on 9000) is added to `networkPolicy.egress.https.ports`.
- **PodDisruptionBudget (8.2).** None by default; `podDisruptionBudget.enabled` renders one
  for more than one replica. `maxUnavailable` is an integer of at least 1 or a percentage
  from 1%: zero is refused.
- **The listener (D18, 9).** In a pod it is bound to the pod's address
  (`[$(POD_IP)]:9108`), not to loopback. Everywhere it answers one request per connection,
  gives a connection five seconds in all and holds at most eight; what `/healthz` and
  `/metrics` answer is unchanged.
- **Sizing (5.7, 8.2).** A follower keeps up to 240 MiB after a long recording (measured),
  and the guard counts what the process holds, so the guidance becomes: the model's host
  memory plus 0.4 GiB plus 3.6 GiB per hour of the longest recording (3.9 GiB split). The
  chart's default is 6Gi for requests and limits (one hour with `distil-large-v3` on a CPU).
- **Revocation on Kubernetes (6.5, 8.2).** After `pool-tokens revoke --revoke-followers`
  the pods exit 4 at every restart; a new token in the Secret brings the pool back only
  with `kubectl rollout restart`, because a revoked pod keeps its credential.
- **Chart test (10).** `e2e/follower-kind/` installs the chart on `kind` beside a leader
  and Postgres in the cluster. It is run by hand and recorded in
  `plans/2026-10-05-follower-f3-outcomes.md`; a GPU pool has not been run on Kubernetes.
