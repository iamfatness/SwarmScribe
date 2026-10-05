# Follower F3b — The Chart on `kind`, and the Guide Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove the follower chart on a local `kind` cluster against a real leader (a pod becomes Ready, registers and transcribes; a killed, deleted, drained or revoked pod loses nothing; a recording that is too long is refused under the pod's memory limit), record the result, and write the README's guide to follower pools and the spec's amendments.

**Architecture:** A throwaway leader with Postgres runs inside the cluster (one manifest, the leader's own test image). A Python driver creates the cluster with a kubeconfig of its own, loads the locally built images, installs the chart with a pool token it puts into a Secret, and drives a scenario through `kubectl`: it administers the leader with the leader's own functions through `kubectl exec`, as the Compose tests do through the database. The run is by hand and is recorded in an outcomes document; the README then says what was run and what was not.

**Tech Stack:** kind 0.30.0 (Kubernetes 1.34.0), kubectl 1.33, Helm 4.3.0, Docker 29, Python 3.12 (standard library only in the driver), pytest with PyYAML for the driver's own parts.

**Spec:** `docs/superpowers/specs/2026-10-04-follower-design.md` (build step 4; sections 5.3, 5.5, 5.6, 5.7, 6.5, 6.6, 8.2, 10 and 12.5), with `docs/superpowers/plans/2026-10-04-follower-f1-followups.md` (sections "F3" and "Left open from the F2a final review").

**This plan is the second of two.** It needs F3a (`2026-10-05-follower-f3a-listener-and-chart.md`) executed on the same branch: the chart `deploy/helm/swarmscribe-follower` with its value names (`image.repository`, `image.tag`, `leader.url`, `leader.allowHttp`, `poolToken.existingSecret`, `resources`, `scratch.sizeLimit`, `terminationGracePeriodSeconds`, `replicaCount`, `gpu.enabled`, `models.volume`, `networkPolicy.ingress.from`), and the listener's limits in `health.py` (`REQUEST_SECONDS = 5.0`, `MAX_CONNECTIONS = 8`). F3a's rulings are referred to here by their numbers.

## Global Constraints

- "F3 also installs the chart on a local `kind` cluster with the `cpu` image: kind, Helm and kubeconform run on the development machine as standalone binaries, as they did for the console chart" (spec 10). "*Result:* `helm install` per pool against any reachable leader" (spec 11).
- What the install must show (spec 5.3, 5.5, 5.6, 6.5, 8.2, 12.5): "a container restart reuses the credential, a new pod registers again, with the pool's pool token"; a new pod "takes over the `followers` row of a pod that has gone"; "A drained pod parks: it stays `Running` and takes no work"; on a stop, "finish if it fits, else release"; exit 4 "show[s] as `CrashLoopBackOff` with the reason as the last log line"; a recording that cannot fit under the limit fails `out_of_resources`.
- **A `kind` cluster on the development machine has no GPU.** The `cuda` image can be shown to exit 3 in a pod, and a GPU pool's pod to stay `Pending`; nothing here runs a follower on a GPU in Kubernetes, and the README and the outcomes document must say so plainly.
- **The images are not published.** The `kind` test loads locally built images into the cluster's node.
- "The join token and the credential are never logged, never passed as arguments" (spec 7). The driver never prints a pool token, a credential or a link, and reads only the follower's id out of a credential file.
- **The whole suite is the gate of every task that changes Python:** `uv run ruff check .` and `uv run pytest` over the whole repository (about 12 minutes; run focused tests while working and the whole suite once, before the task's commit). Python `>=3.11`; `ruff` line length 100, rules `E, F, I, UP, B`.
- **The development machine** is Windows 11 with Git Bash. `uv` is not on the PATH: run it as `python -m uv` (the commands below are written `uv run ...`). Docker Desktop, Helm, kubeconform, kind and kubectl: see "The tools on the Windows machine" below, and run its `export PATH=...` line in every shell that uses them. None is installed system-wide.
- **Work only in the worktree `C:\Users\walla\SwarmScribe-f3`**, on the branch `follower-f3`. Never touch `C:\Users\walla\SwarmScribe`, `C:\Users\walla\SwarmScribe-ui` or `C:\Users\walla\SwarmScribe-f2a-fix`. Do not push.
- **Another agent uses Docker on this machine.** Never stop, remove or retag a container or an image you did not create (`f2a-review-readme`, `swarmscribe-*:review*` and the like), never run any `docker ... prune`, never restart Docker, never touch a `kind` cluster that is not this plan's (`swarmscribe-follower-e2e`). Never use ports 8900 or 8901 (nothing here publishes a port). The `kind` test uses a kubeconfig of its own (`e2e/follower-kind/work/kubeconfig`) and leaves `~/.kube/config` alone.
- **Capture, then match.** In a shell check under `set -euo pipefail`, never pipe a live `docker`, `kubectl` or `helm` command into `grep -q`: `grep -q` leaves at its first match, the producer dies of `SIGPIPE`, and `pipefail` fails a check that passed. Write the output to a variable or a file first, then match it. (The driver is Python and captures everything; this plan's shell commands are run by hand and read by eye.)
- Commit after each task with the message the task gives, ending with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

### The tools on the Windows machine

Helm, kubeconform, kind and kubectl are single binaries. They are already in `$TEMP/chart-tools` on the development machine; fetch whichever is missing (Git Bash), and never install one system-wide:

```bash
mkdir -p "$TEMP/chart-tools" && cd "$TEMP/chart-tools"
[ -x windows-amd64/helm.exe ] || { curl -sL -o helm.zip https://get.helm.sh/helm-v4.3.0-windows-amd64.zip && unzip -q -o helm.zip; }
[ -x kubeconform.exe ] || { curl -sL -o kc.zip https://github.com/yannh/kubeconform/releases/download/v0.8.0/kubeconform-windows-amd64.zip && unzip -q -o kc.zip; }
[ -x kind.exe ] || curl -sL -o kind.exe https://kind.sigs.k8s.io/dl/v0.30.0/kind-windows-amd64
[ -x kubectl.exe ] || curl -sL -o kubectl.exe https://dl.k8s.io/release/v1.33.0/bin/windows/amd64/kubectl.exe
cd -
```

Then, in every shell that runs one of them or `docker`:

```bash
export PATH="$TEMP/chart-tools/windows-amd64:$TEMP/chart-tools:$PATH:/c/Users/walla/AppData/Local/Programs/DockerDesktop/resources/bin"
helm version --short   # v4.3.0+gbec5b06
kind version           # kind v0.30.0 go1.24.6 windows/amd64
```

(Checked on 2026-10-05: the four addresses answer 200.)

## Measured before this plan was written

All of this plan's code ran on 2026-10-05 in a scratch folder outside the repository, built from `main` at `96ffd19` with F3a's files (Windows 11, Docker Desktop 29.8.1 with WSL 2 and 16.6 GB for the Docker VM, kind 0.30.0 with Kubernetes 1.34.0 and containerd 2.1.3, kubectl 1.33.0, Helm 4.3.0). The scenario passed four times in its final form; one earlier run failed on a mistake of the driver's own (it expected no restarts of the drained pod, which was the pod it had killed two steps before), corrected.

| What | Result |
|---|---|
| `run_e2e.py up` (cluster, three images, leader) | 1 min 50 s on a new cluster, 1 min 20 s on an existing one; `down` takes 2 s |
| `kind load docker-image` | worked for the two images built here (26 s and 13 s); for the pulled `postgres:16` it stopped with `content digest ... not found`, and `docker save --platform linux/amd64 ... \| ctr -n k8s.io images import -` worked. The driver tries the first and falls back to the second |
| `kind create cluster` with `--kubeconfig` | `kubectl config get-contexts` of the user's own kubeconfig stayed empty |
| `run_e2e.py run` | passed four times, 1 min 43 s to 1 min 50 s |
| Both pods Ready; both registered | 3 s; 3 to 4 s after `helm upgrade --install` |
| A follower killed mid-job (`SIGKILL` to the process) | the container restarted (exit 137) and was the same follower, with no `registered` line; the recording `expired`, then `completed`, 2 attempts |
| A pod deleted mid-job, 60 s of grace | gone in 1.8 to 2.1 s; the recording `released`, then `completed`, 1 attempt |
| Scale to 1, wait for `gone`, scale to 2 | the new pod took over the row; the leader's list did not grow |
| One hour of silence, limit 2500Mi | failed three times: `out_of_resources: OutOfMemory: a recording of 60 minutes needs about 4121 MiB here and this follower may use 2500 MiB (SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB)` |
| A drained follower | `swarmscribe_follower_state{state="draining"} 1.0`; the pod stayed Running, not restarted; deleting it brought a new, active follower and the row stayed `draining` |
| Another pod asking `/metrics` | timed out by default; 200 once `networkPolicy.ingress.from` named it; a third pod still shut out; no pod restarted by the upgrade |
| The pool token revoked with its followers | both pods `CrashLoopBackOff`, exit 4, `stopping: this follower has been revoked`; a new token in the Secret and `kubectl rollout restart` brought two new followers (the rollout took 6 s) |
| `run_e2e.py no-gpu` | passed twice, in 1 min 23 s and 1 min 25 s (80 s of it loading the 2.5 GB `cuda` image): exit 3 with `error: cuda was requested but no CUDA GPU is available`; the GPU pool's pod `Pending`, `0/1 nodes are available: 1 Insufficient nvidia.com/gpu` |
| The whole suite with every change of F3a and F3b | `3128 passed, 19 skipped, 5 deselected` in 11 min 26 s (`main` at `96ffd19` has 3110 passing tests; these plans add 18) |

The memory figures that the README's sizing rests on (a follower keeps up to 242 MiB after a long recording; a model downloaded at start-up costs about 0.5 GiB until the container restarts) are in F3a's "Measured" section and in the outcomes document of Task 2.

## Rulings

Those marked **(owner)** are the owner's to overturn; each has the plan's recommendation built in.

1. **The `kind` install runs the leader inside the cluster** (`e2e/follower-kind/leader.yaml`: Postgres and one leader replica from the leader's test image, local storage in an `emptyDir`, the lease at 8 seconds as in the Compose test), and administers it with the leader's own functions through `kubectl exec -i deploy/leader -- python -` (a pool token, the profile, locations, drain, revoke). A leader outside the cluster would have needed an address that pods can reach and that the leader builds its links from, which differs between Docker Desktop and Linux; inside, it is `http://leader:8080` everywhere. It is a test fixture, not a way to deploy a leader.
2. **The `kind` install is a local proof, recorded in an outcomes document, not a CI job** (C4b's ruling 11 did the same for the console chart). **(owner)** Recommended as written for now. As a job it would cost about five minutes (two image builds that `follower-compose-e2e` already makes, a cluster, the scenario), and GitHub's runners have `kind`; the reason to wait is that most of what it proves beyond the Compose test is the chart's wiring, which changes rarely, and a cluster in CI is one more thing that fails for reasons of its own. Adding it later is a job of about ten lines around `run_e2e.py`.
3. **The cluster has a kubeconfig of its own** (`e2e/follower-kind/work/kubeconfig`, git-ignored and kept out of image builds): `kind create cluster` would otherwise add a context to `~/.kube/config` and make it the current one, on a machine another agent shares.
4. **The scenario runs once per leader**, like the Compose scenario (it drains and revokes); `up` makes a fresh namespace, and with it a fresh leader and database, on the same cluster.
5. **Which pod is which follower is read from the credential file in the pod** (the id only), not from the log: a container that restarted did not register and logs no id.
6. **The README's sizing table is the measured formula** (F3a's ruling 9): `model + 0.4 GiB + 3.6 GiB x hours`. The hour-long recording under the chart's default 6Gi with `distil-large-v3` was not run in a pod (two half-hour recordings under a limit sized by the formula were); the outcomes document says so.

## Review Focus

Conditions the spec implies and that are most likely to bite a person running a pool, each pinned by a step of the scenario in Task 2 (the driver is Task 1's):

1. **A pod stopped in the middle of a recording** (a rollout, a node drain): the recording is handed back without a counted attempt and redone, and the pod is gone long before its grace period — step 5.
2. **A follower killed outright** (out of memory): its container restarts as the same follower, without spending the pool token again or adding a row — step 4.
3. **A recording longer than the pod's memory allows**: refused with its length and the pod's limit in the reason, three times, never killed half-way — step 7.
4. **A drained pod**: it must stay up (a Deployment would restart whatever exits, for ever) and take nothing — step 8.
5. **A pool whose token was revoked with its followers**: every pod exits 4 and stays out; a new token alone does not bring them back, a rollout restart does — step 10.
6. **A `cuda` image where there is no GPU, and a GPU pool on a cluster without GPUs**: exit 3 with a line that says why; `Pending` with `Insufficient nvidia.com/gpu` — `no-gpu`.
7. **A cluster whose network plugin does not enforce NetworkPolicy**: the scenario says so in words instead of passing — step 2.

## File Structure

```
e2e/follower-kind/
  leader.yaml                 a throwaway leader and Postgres
  admin.py                    the driver's hands inside the leader's pod
  values.yaml                 the chart's values for the test
  run_e2e.py                  up, run, no-gpu, down
packages/follower/tests/test_kind_driver.py                 (new, Task 1)
.gitignore, .dockerignore                                   (modify, Task 1) e2e/follower-kind/work
docs/superpowers/plans/2026-10-05-follower-f3-outcomes.md   (new, Task 2)
README.md                                                   (modify, Task 3)
docs/superpowers/specs/2026-10-04-follower-design.md        (modify, Task 3) amendments after F3
docs/superpowers/plans/2026-10-04-follower-f1-followups.md  (modify, Task 3)
```

---

### Task 1: The `kind` test — a throwaway leader, the chart's values and the driver

This task writes the test; Task 2 runs it. Nothing here needs a cluster.

**Files:**
- Create: `e2e/follower-kind/leader.yaml`
- Create: `e2e/follower-kind/admin.py`
- Create: `e2e/follower-kind/values.yaml`
- Create: `e2e/follower-kind/run_e2e.py`
- Create: `packages/follower/tests/test_kind_driver.py`
- Modify: `.gitignore`, `.dockerignore`

**Interfaces:**
- Consumes: the chart (F3a) and its value names (`image.*`, `leader.url`, `leader.allowHttp`, `poolToken.existingSecret`, `resources`, `scratch.sizeLimit`, `terminationGracePeriodSeconds`, `replicaCount`, `gpu.enabled`, `networkPolicy.ingress.from`); the leader's test image (`e2e/compose/Dockerfile`, which copies the whole repository to `/app` and has `swarmscribe-leader` on its PATH); the leader's functions `create_pool_token(session, *, name, pool, actor)`, `revoke_pool_token(session, name, *, now, actor, revoke_followers)`, `drain(session, follower_id, *, actor)`, `set_profile(session, device, *, model, compute_type, temperatures, actor)`; the follower's `/metrics` sample names (`swarmscribe_follower_state{state="idle"}`, `{state="draining"}`).
- Produces: `python e2e/follower-kind/run_e2e.py up | run | no-gpu | down`. Environment: `KIND_CLUSTER` (default `swarmscribe-follower-e2e`), `LEADER_IMAGE` (`swarmscribe-leader:e2e`), `FOLLOWER_IMAGE` (`swarmscribe-follower:e2e`, a `cpu` image with `tiny.en` baked in), `CUDA_IMAGE` (`swarmscribe-follower:cuda`). The cluster's kubeconfig is `e2e/follower-kind/work/kubeconfig`; the namespace is `swarmscribe-e2e`; the release is `pool`. `run` ends with a line that starts `passed (tiny.en on cpu):`; `no-gpu` with one that starts `passed: without a GPU`. Task 2 runs these and records what they print.

- [ ] **Step 1: The throwaway leader**

Create `e2e/follower-kind/leader.yaml`:

```yaml
# A throwaway leader for the kind test of the follower chart (e2e/follower-kind/run_e2e.py):
# Postgres and one leader replica with local storage, in the test's namespace. This is NOT
# how a leader is deployed: no TLS, no identity provider, storage in an emptyDir, and every
# value here is test-only. The leader's chart is roadmap item 6.
apiVersion: apps/v1
kind: Deployment
metadata:
  name: postgres
spec:
  replicas: 1
  selector:
    matchLabels: {app: postgres}
  template:
    metadata:
      labels: {app: postgres}
    spec:
      containers:
        - name: postgres
          image: postgres:16
          imagePullPolicy: IfNotPresent
          env:
            - {name: POSTGRES_PASSWORD, value: postgres}
            - {name: POSTGRES_DB, value: swarmscribe}
          ports:
            - containerPort: 5432
          readinessProbe:
            exec:
              command: ["pg_isready", "-U", "postgres", "-d", "swarmscribe"]
            periodSeconds: 2
---
apiVersion: v1
kind: Service
metadata:
  name: postgres
spec:
  selector: {app: postgres}
  ports:
    - port: 5432
      targetPort: 5432
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: leader
spec:
  replicas: 1
  selector:
    matchLabels: {app: leader}
  template:
    metadata:
      labels: {app: leader}
    spec:
      initContainers:
        - name: migrate
          image: swarmscribe-leader:e2e
          imagePullPolicy: Never
          command: ["sh", "-c", "until swarmscribe-leader migrate; do sleep 2; done"]
          env: &leader-env
            - name: SWARMSCRIBE_DATABASE_URL
              value: postgresql://postgres:postgres@postgres:5432/swarmscribe
            # The leader builds its own file links from this, and the followers fetch them
            # from their pods: it must be the Service's name (follower spec 12.6).
            - name: SWARMSCRIBE_PUBLIC_URL
              value: http://leader:8080
            - {name: SWARMSCRIBE_LINK_KEY, value: follower-kind-link-key-0123456789abcdef}
            # A lease far shorter than a long transcription: only heartbeats keep it, and a
            # killed follower's job comes back within seconds.
            - {name: SWARMSCRIBE_LEASE_SECONDS, value: "8"}
            - {name: SWARMSCRIBE_HEARTBEAT_SECONDS, value: "2"}
            - {name: SWARMSCRIBE_REAPER_INTERVAL_SECONDS, value: "1"}
            - {name: SWARMSCRIBE_SCANNER_INTERVAL_SECONDS, value: "1"}
            - {name: SWARMSCRIBE_CLAIM_RETRY_AFTER, value: "1"}
            - {name: SWARMSCRIBE_FOLLOWER_GONE_AFTER_SECONDS, value: "20"}
      containers:
        - name: leader
          image: swarmscribe-leader:e2e
          imagePullPolicy: Never
          env: *leader-env
          ports:
            - containerPort: 8080
          readinessProbe:
            httpGet: {path: /readyz, port: 8080}
            periodSeconds: 2
          volumeMounts:
            - {name: data, mountPath: /data}
      volumes:
        - name: data
          emptyDir: {}
---
apiVersion: v1
kind: Service
metadata:
  name: leader
spec:
  selector: {app: leader}
  ports:
    - port: 8080
      targetPort: 8080
```

Create `e2e/follower-kind/admin.py` (it runs inside the leader's pod, where the leader's package is installed; on your machine it is only linted):

```python
"""The kind test's hands inside the leader's pod (e2e/follower-kind/run_e2e.py pipes this file
to `kubectl exec -i deploy/leader -- python - <command> ...`).

It administers the leader with the leader's own functions against its database, as the
Compose tests do: this stack has no identity provider to sign an administrator in. It also
writes the recordings and reads the transcripts, which live in the leader's /data. Every
command prints one JSON value. Nothing here prints a credential or a link; `pool-token`
prints the new token, once, for the Secret."""

import asyncio
import json
import os
import sys
import uuid
import wave
from pathlib import Path

from sqlalchemy import select
from swarmscribe_leader.auth.followers import drain
from swarmscribe_leader.auth.pool_tokens import create_pool_token, revoke_pool_token
from swarmscribe_leader.clock import utcnow
from swarmscribe_leader.db.models import Follower, Job, JobAttempt, Recording, StorageLocation
from swarmscribe_leader.db.session import make_engine, make_sessionmaker
from swarmscribe_leader.profiles import set_profile

DATA = Path("/data")
FIXTURE = Path("/app/packages/engine/tests/fixtures/stereo_speech.wav")
ACTOR = "e2e"


def write_recording(path: Path, repeats: int) -> None:
    """The speech fixture, `repeats` times over, under another name first: the scanner must
    never see half a file."""
    with wave.open(str(FIXTURE), "rb") as source:
        params, frames = source.getparams(), source.readframes(source.getnframes())
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    with wave.open(str(partial), "wb") as out:
        out.setparams(params)
        for _ in range(repeats):
            out.writeframes(frames)
    partial.replace(path)


def write_silence(path: Path, seconds: int) -> None:
    """A mono recording of nothing, as small as a WAV file gets (8 kHz, 8 bits)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    with wave.open(str(partial), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(1)
        out.setframerate(8000)
        out.writeframes(bytes([128]) * 8000 * seconds)
    partial.replace(path)


async def state(session) -> dict:
    """Everything the driver asserts on: the followers, and every job with its attempts."""
    followers = [
        {
            "id": str(row.id),
            "state": row.state,
            "pool": row.pool,
            "device": row.capabilities.get("device"),
            "models": row.capabilities.get("models", []),
            "last_seen_at": row.last_seen_at.isoformat(),
        }
        for row in (await session.scalars(select(Follower))).all()
    ]
    jobs = {}
    rows = await session.execute(
        select(Job, Recording.key, StorageLocation.name)
        .join(Recording, Recording.id == Job.recording_id)
        .join(StorageLocation, StorageLocation.id == Recording.location_id)
    )
    for job, key, location in rows.all():
        attempts = (
            await session.scalars(
                select(JobAttempt)
                .where(JobAttempt.job_id == job.id)
                .order_by(JobAttempt.started_at)
            )
        ).all()
        text = DATA / location / "transcripts" / f"{key}.txt"
        jobs[f"{location}/{key}"] = {
            "state": job.state,
            "attempts": job.attempts,
            "leased_by": str(job.leased_by) if job.leased_by else None,
            "failure_reason": job.failure_reason,
            "tried": [[str(a.follower_id), a.outcome] for a in attempts],
            "text": text.read_text(encoding="utf-8") if text.is_file() else None,
        }
    return {"followers": followers, "jobs": jobs}


async def run(command: str, arguments: list[str]) -> object:
    engine = make_engine(os.environ["SWARMSCRIBE_DATABASE_URL"])
    try:
        async with make_sessionmaker(engine)() as session:
            if command == "pool-token":
                name, pool = arguments
                _, token = await create_pool_token(session, name=name, pool=pool, actor=ACTOR)
                result: object = token
            elif command == "profile":
                device, model, compute_type = arguments
                await set_profile(
                    session, device, model=model, compute_type=compute_type,
                    temperatures=None, actor=ACTOR,
                )
                result = True
            elif command == "location":
                # Written straight to Postgres, as the Compose driver does.
                name, mode = arguments
                (DATA / name).mkdir(parents=True, exist_ok=True)
                (DATA / name / "consent.txt").write_text("*.wav\n", encoding="utf-8")
                session.add(
                    StorageLocation(
                        id=uuid.uuid4(), name=name, backend="local",
                        config={"root": f"/data/{name}"}, input_prefix="",
                        output_prefix="transcripts/", pool="default", required_device="any",
                        scan_interval_s=0, enabled=True, vocabulary_version=0,
                        channel_mode=mode, channel_labels=["Agent", "Customer"],
                    )
                )
                result = True
            elif command == "recording":
                location, key, repeats = arguments
                write_recording(DATA / location / key, int(repeats))
                result = True
            elif command == "silence":
                location, key, seconds = arguments
                write_silence(DATA / location / key, int(seconds))
                result = True
            elif command == "drain":
                await drain(session, uuid.UUID(arguments[0]), actor=ACTOR)
                result = True
            elif command == "revoke-pool":
                _, revoked = await revoke_pool_token(
                    session, arguments[0], now=utcnow(), actor=ACTOR, revoke_followers=True
                )
                result = revoked
            elif command == "state":
                result = await state(session)
            else:
                raise SystemExit(f"unknown command {command}")
            await session.commit()
            return result
    finally:
        await engine.dispose()


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run(sys.argv[1], sys.argv[2:]))))
```

- [ ] **Step 2: The chart's values for the test**

Create `e2e/follower-kind/values.yaml`:

```yaml
# The follower chart's values for the kind test (e2e/follower-kind/run_e2e.py). Test-only:
# a plain-http leader inside the cluster, a small image with tiny.en baked in, and a memory
# limit that has room for a four-minute recording and not for an hour.
image:
  repository: swarmscribe-follower
  tag: e2e
  pullPolicy: Never
replicaCount: 2
leader:
  url: http://leader:8080
  allowHttp: true
poolToken:
  existingSecret: pool-token
pool: default
resources:
  requests:
    cpu: 500m
    memory: 1Gi
  limits:
    cpu: "2"
    memory: 2500Mi
scratch:
  sizeLimit: 1Gi
# Long enough to hand a recording back, short enough for a test: the follower is told 30 s.
terminationGracePeriodSeconds: 60
```

- [ ] **Step 3: Write the failing tests of the driver's own parts**

Create `packages/follower/tests/test_kind_driver.py`:

```python
"""The kind test's own parts (e2e/follower-kind), checked without a cluster: that its values,
its leader and its driver agree with each other and with the memory guard's figures."""

import importlib.util
import re
import sys
from pathlib import Path

import pytest
from swarmscribe_follower.memory import job_mb

yaml = pytest.importorskip("yaml")

KIND = Path(__file__).resolve().parents[3] / "e2e" / "follower-kind"
# Measured, in MiB (README, "Sizing a pool"): tiny.en once loaded, and what a follower keeps.
TINY_LOADED, KEPT_AFTER_A_LONG_JOB = 230, 300


def mebibytes(quantity: str) -> int:
    number, unit = re.fullmatch(r"(\d+)(Mi|Gi)", quantity).groups()
    return int(number) * (1024 if unit == "Gi" else 1)


@pytest.fixture(scope="module")
def driver():
    spec = importlib.util.spec_from_file_location("follower_kind_driver", KIND / "run_e2e.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["follower_kind_driver"] = module
    spec.loader.exec_module(module)
    return module


def test_the_kind_values_admit_the_long_recordings_and_refuse_the_hour(driver):
    values = yaml.safe_load((KIND / "values.yaml").read_text(encoding="utf-8"))
    limit = mebibytes(values["resources"]["limits"]["memory"])
    longest = driver.LONG_REPEATS * 5.0  # the fixture is five seconds long
    assert TINY_LOADED + KEPT_AFTER_A_LONG_JOB + job_mb(longest, split=True) < limit
    assert job_mb(3600, split=False) > limit
    assert f"{limit} MiB" in "2500 MiB"  # what the scenario looks for in the refusal
    assert values["terminationGracePeriodSeconds"] == driver.GRACE_SECONDS
    assert values["poolToken"]["existingSecret"] == "pool-token"  # store_pool_token's Secret


def test_the_kind_leader_is_reached_by_the_name_the_values_give(driver):
    documents = list(yaml.safe_load_all((KIND / "leader.yaml").read_text(encoding="utf-8")))
    values = yaml.safe_load((KIND / "values.yaml").read_text(encoding="utf-8"))
    services = {d["metadata"]["name"]: d for d in documents if d["kind"] == "Service"}
    port = services["leader"]["spec"]["ports"][0]
    # A NetworkPolicy is matched after Service translation: the two ports must be one.
    assert values["leader"] == {"url": f"http://leader:{port['port']}", "allowHttp": True}
    assert port["port"] == port["targetPort"]
    (leader,) = [
        d for d in documents if d["kind"] == "Deployment" and d["metadata"]["name"] == "leader"
    ]
    container = leader["spec"]["template"]["spec"]["containers"][0]
    settings = {entry["name"]: entry["value"] for entry in container["env"]}
    assert settings["SWARMSCRIBE_PUBLIC_URL"] == values["leader"]["url"]
    assert container["image"] == "swarmscribe-leader:e2e"  # `up` replaces exactly this name
    assert leader["spec"]["template"]["metadata"]["labels"] == {"app": "leader"}  # step 9's peer


@pytest.mark.parametrize(
    ("image", "parts"),
    [
        ("swarmscribe-follower:e2e", ("swarmscribe-follower", "e2e")),
        ("registry.example.org:5000/team/f:1.2", ("registry.example.org:5000/team/f", "1.2")),
    ],
)
def test_an_image_is_split_into_its_repository_and_its_tag(driver, image, parts):
    assert driver.image_parts(image) == parts


@pytest.mark.parametrize("image", ["swarmscribe-follower", "registry.example.org:5000/follower"])
def test_an_image_without_a_tag_is_refused(driver, image):
    with pytest.raises(AssertionError, match="name:tag"):
        driver.image_parts(image)
```

Run: `uv run pytest packages/follower/tests/test_kind_driver.py -q`
Expected: 6 errors, each `FileNotFoundError: ... e2e\\follower-kind\\run_e2e.py` (the driver does not exist yet).

- [ ] **Step 4: The driver**

Create `e2e/follower-kind/run_e2e.py`:

```python
"""The follower chart on a local `kind` cluster, against a real leader (follower spec 10).

A throwaway leader with Postgres (leader.yaml) and the chart `deploy/helm/swarmscribe-follower`
installed with values.yaml: two followers from the real image, with tiny.en baked in. Needs
`kind`, `kubectl`, `helm` and `docker` on the PATH and the two images built. It is run by
hand, not in CI, and its result is recorded in the F3 outcomes document.

    python e2e/follower-kind/run_e2e.py up       # the cluster, the images, the leader
    python e2e/follower-kind/run_e2e.py run      # the scenario below
    python e2e/follower-kind/run_e2e.py no-gpu   # a cuda image where there is no GPU
    python e2e/follower-kind/run_e2e.py down     # delete the cluster

What `run` proves, in order:

 1. the chart installs; both pods become Ready and register with the pool token from the
    Secret; the state folder is the follower's own inside the root-owned emptyDir; the
    listener is on the pod's address and not on loopback;
 2. the NetworkPolicy lets the kubelet probe and nobody else in: another pod cannot connect;
 3. four recordings are transcribed, each once, and say what was said;
 4. a follower killed mid-job (its container restarts) loses nothing, and comes back as the
    same follower: the credential in the emptyDir outlives the container;
 5. a pod deleted mid-job hands its recording back without a counted attempt and is gone
    long before its grace period; another follower redoes the recording;
 6. a pod that replaces one that has gone takes over its row: the leader's list of followers
    does not grow;
 7. an hour-long recording is refused by the memory guard under the pod's memory limit;
 8. a drained pod parks: it stays Running, is not restarted and takes nothing more; deleting
    it brings a new, active follower;
 9. /metrics opens to a peer named in values, without restarting a pod; and no pod's log
    holds the token, a file link or a word of a transcript;
10. revoking the pool token with its followers makes every pod exit 4; a new token in the
    Secret and a rollout restart bring the pool back.

The cluster has its own kubeconfig (work/kubeconfig): yours is not touched. Nothing here
prints a pool token, a credential or a link."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
CHART = HERE.parents[1] / "deploy" / "helm" / "swarmscribe-follower"
WORK = HERE / "work"
KUBECONFIG = WORK / "kubeconfig"
CLUSTER = os.environ.get("KIND_CLUSTER", "swarmscribe-follower-e2e")
NAMESPACE = "swarmscribe-e2e"
RELEASE = "pool"
DEPLOYMENT = f"{RELEASE}-swarmscribe-follower"
LEADER_IMAGE = os.environ.get("LEADER_IMAGE", "swarmscribe-leader:e2e")
FOLLOWER_IMAGE = os.environ.get("FOLLOWER_IMAGE", "swarmscribe-follower:e2e")
CUDA_IMAGE = os.environ.get("CUDA_IMAGE", "swarmscribe-follower:cuda")
POSTGRES_IMAGE = "postgres:16"  # leader.yaml
SELECTOR = f"app.kubernetes.io/instance={RELEASE}"
STATE_DIR = "/var/lib/swarmscribe-follower"  # the chart's mount; the state folder is /state in it

MODEL, COMPUTE_TYPE = "tiny.en", "int8"
CALLS, TALKS = "calls", "talks"  # two locations: split into Agent/Customer, and mono
SHORT = (f"{CALLS}/call-1.wav", f"{CALLS}/call-2.wav", f"{TALKS}/talk-1.wav", f"{TALKS}/talk-2.wav")
KILLED, DELETED = f"{CALLS}/long-kill.wav", f"{CALLS}/long-delete.wav"
TOO_LONG, AFTER_DRAIN = f"{TALKS}/an-hour.wav", f"{TALKS}/after-drain.wav"
LEFT_WORD, RIGHT_WORD = "weather", "report"  # what the fixture says, left then right
LONG_REPEATS = 48  # the 5 s fixture, 48 times: four minutes, some 30 s to a minute on two cores
MID_JOB_SECONDS = 5.0
GRACE_SECONDS = 60  # values.yaml: terminationGracePeriodSeconds
STEP_SECONDS = 180.0


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


# --- the tools ---------------------------------------------------------------------------


def call(*command: str, stdin: str | None = None, check: bool = True) -> str:
    done = subprocess.run(
        command, input=stdin, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if check and done.returncode != 0:
        said = (done.stderr.strip() or done.stdout.strip())[-600:]
        raise AssertionError(f"`{' '.join(command[:4])} ...` failed: {said}")
    return done.stdout


def kubectl(*arguments: str, stdin: str | None = None, check: bool = True) -> str:
    return call(
        "kubectl", "--kubeconfig", str(KUBECONFIG), "-n", NAMESPACE, *arguments,
        stdin=stdin, check=check,
    )


def helm(*arguments: str) -> str:
    return call("helm", "--kubeconfig", str(KUBECONFIG), "-n", NAMESPACE, *arguments)


def image_parts(image: str) -> tuple[str, str]:
    """(repository, tag) of `name:tag`; a registry's port is not a tag."""
    repository, colon, tag = image.rpartition(":")
    if not colon or "/" in tag:
        raise AssertionError(f"{image} must be name:tag")
    return repository, tag


def install(release: str, image: str, *extra: str) -> None:
    repository, tag = image_parts(image)
    helm(
        "upgrade", "--install", release, str(CHART), "-f", str(HERE / "values.yaml"),
        "--set", f"image.repository={repository}", "--set", f"image.tag={tag}", *extra,
    )


def until(check: Callable[[], Any], what: str, within: float = STEP_SECONDS) -> Any:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        found = check()
        if found:
            return found
        time.sleep(1.0)
    raise AssertionError(f"timed out after {within:.0f} s waiting for {what}")


# --- the leader --------------------------------------------------------------------------


def admin(command: str, *arguments: str) -> Any:
    """Run admin.py inside the leader's pod; returns what it printed (one JSON value)."""
    script = (HERE / "admin.py").read_text(encoding="utf-8")
    said = kubectl(
        "exec", "-i", "deploy/leader", "-c", "leader", "--", "python", "-", command, *arguments,
        stdin=script,
    )
    return json.loads(said.strip().splitlines()[-1])


def followers(state: str | None = None) -> list[dict]:
    rows = admin("state")["followers"]
    return [row for row in rows if state is None or row["state"] == state]


def job(key: str) -> dict | None:
    return admin("state")["jobs"].get(key)


def add(key: str, repeats: int = 1) -> None:
    location, _, name = key.partition("/")
    admin("recording", location, name, str(repeats))


def completed(key: str) -> dict | None:
    found = job(key)
    return found if found is not None and found["state"] == "completed" else None


def held_mid_job(key: str) -> str:
    """Wait until one follower has held the job for MID_JOB_SECONDS; returns its id."""

    def holder() -> str | None:
        found = job(key)
        expect(
            found is None or found["state"] != "completed",
            f"{key} was transcribed before it could be interrupted; raise LONG_REPEATS",
        )
        return found["leased_by"] if found and found["state"] == "leased" else None

    first = until(holder, f"a follower to take {key}")
    time.sleep(MID_JOB_SECONDS)
    expect(holder() == first, f"{key} did not stay with one follower for {MID_JOB_SECONDS:.0f} s")
    return first


def store_pool_token(name: str) -> str:
    """A new pool token, put into the Secret the chart reads. Returns it only so that the
    logs can be searched for it."""
    token = admin("pool-token", name, "default")
    secret = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": "pool-token"},
        "stringData": {"pool-token": token},
    }
    kubectl("apply", "-f", "-", stdin=json.dumps(secret))
    return token


# --- the follower pods -------------------------------------------------------------------


def pods() -> list[dict]:
    """The release's pods that are not being deleted."""
    listed = json.loads(kubectl("get", "pods", "-l", SELECTOR, "-o", "json"))["items"]
    return [pod for pod in listed if "deletionTimestamp" not in pod["metadata"]]


def container(pod: dict) -> dict:
    statuses = pod.get("status", {}).get("containerStatuses") or [{}]
    return statuses[0]


def in_pod(name: str, code: str, *arguments: str, check: bool = True) -> str:
    return kubectl("exec", name, "--", "python", "-c", code, *arguments, check=check).strip()


FOLLOWER_ID = (
    "import json, sys;"
    "print(json.load(open(sys.argv[1] + '/state/credential.json'))['follower_id'])"
)
# Only the id is read out of the credential file; the credential itself never leaves the pod.


def follower_id(name: str) -> str | None:
    return in_pod(name, FOLLOWER_ID, STATE_DIR, check=False) or None


def pod_of(wanted: str) -> str:
    def find() -> str | None:
        return next(
            (p["metadata"]["name"] for p in pods() if follower_id(p["metadata"]["name"]) == wanted),
            None,
        )

    return until(find, f"the pod of follower {wanted}", 60.0)


FETCH = (
    "import sys, urllib.request;"
    "opener = urllib.request.build_opener(urllib.request.ProxyHandler({}));"
    "print(opener.open(sys.argv[1], timeout=4).read().decode())"
)


def metrics(name: str, address: str) -> dict[str, float]:
    found = {}
    for line in in_pod(name, FETCH, f"http://{address}:9108/metrics").splitlines():
        if line and not line.startswith("#"):
            sample, _, number = line.rpartition(" ")
            found[sample] = float(number)
    return found


def reachable_from_the_leader(address: str) -> bool:
    """Whether the leader's pod, which is not the kubelet, can read a follower's /metrics."""
    said = kubectl(
        "exec", "deploy/leader", "-c", "leader", "--", "python", "-c", FETCH,
        f"http://{address}:9108/metrics", check=False,
    )
    return "swarmscribe_follower_state" in said


def two_active_followers() -> list[dict] | None:
    active = followers("active")
    return active if len(active) == 2 else None


# --- up, down ----------------------------------------------------------------------------


def load(image: str) -> None:
    """Put a local image into the cluster's node. `kind load docker-image` first; Docker
    Desktop's image store can make it stop with "content digest ... not found" on an image
    that was pulled, so the fallback pipes one platform of it into the node."""
    done = subprocess.run(
        ["kind", "load", "docker-image", image, "--name", CLUSTER], capture_output=True
    )
    if done.returncode == 0:
        return
    node = f"{CLUSTER}-control-plane"
    save = subprocess.Popen(
        ["docker", "save", "--platform", "linux/amd64", image], stdout=subprocess.PIPE
    )
    imported = subprocess.run(
        ["docker", "exec", "-i", node, "ctr", "-n", "k8s.io", "images", "import", "-"],
        stdin=save.stdout, capture_output=True,
    )
    save.wait()
    expect(
        save.returncode == 0 and imported.returncode == 0,
        f"could not load {image} into the cluster: is it built (or pulled)?",
    )


def up() -> None:
    for tool in ("kind", "kubectl", "helm", "docker"):
        expect(shutil.which(tool) is not None, f"{tool} is not on the PATH")
    WORK.mkdir(exist_ok=True)
    if CLUSTER not in call("kind", "get", "clusters").split():
        call(
            "kind", "create", "cluster", "--name", CLUSTER, "--kubeconfig", str(KUBECONFIG),
            "--wait", "120s",
        )
    else:
        KUBECONFIG.write_text(call("kind", "get", "kubeconfig", "--name", CLUSTER), "utf-8")
    for image in (LEADER_IMAGE, FOLLOWER_IMAGE, POSTGRES_IMAGE):
        load(image)
    # A fresh namespace every time: the scenario drains and revokes, and runs once per leader.
    kubectl("delete", "namespace", NAMESPACE, "--ignore-not-found", "--wait=true")
    kubectl("create", "namespace", NAMESPACE)
    manifest = (HERE / "leader.yaml").read_text(encoding="utf-8")
    kubectl("apply", "-f", "-", stdin=manifest.replace("swarmscribe-leader:e2e", LEADER_IMAGE))
    kubectl("rollout", "status", "deploy/postgres", "--timeout=180s")
    kubectl("rollout", "status", "deploy/leader", "--timeout=180s")
    print(f"the cluster {CLUSTER} is up, with a leader in namespace {NAMESPACE}")


def down() -> None:
    call("kind", "delete", "cluster", "--name", CLUSTER, "--kubeconfig", str(KUBECONFIG))
    shutil.rmtree(WORK, ignore_errors=True)
    print(f"deleted the cluster {CLUSTER}")


# --- the scenario ------------------------------------------------------------------------

FOLDERS = (
    "import os, stat, sys;"
    "print(' '.join(f'{os.stat(p).st_uid}:{stat.S_IMODE(os.stat(p).st_mode):o}'"
    " for p in sys.argv[1:]))"
)


def scenario() -> dict[str, float]:
    report: dict[str, float] = {}
    expect(not followers(), "the leader already has followers: run `up` again for a fresh one")

    # 1. Install; both pods are Ready and registered; the folders and the listener are right.
    token = store_pool_token("kind-pods")
    admin("profile", "cpu", MODEL, COMPUTE_TYPE)
    started = time.monotonic()
    install(RELEASE, FOLLOWER_IMAGE)
    kubectl("rollout", "status", f"deploy/{DEPLOYMENT}", "--timeout=180s")
    report["ready"] = time.monotonic() - started
    rows = until(two_active_followers, "both followers to register")
    report["registered"] = time.monotonic() - started
    for row in rows:
        expect(row["device"] == "cpu", f"a follower registered as {row['device']}")
        expect(MODEL in row["models"], "a follower did not report its model")
        expect(row["pool"] == "default", f"a follower is in pool {row['pool']}")
    for pod in pods():
        name, address = pod["metadata"]["name"], pod["status"]["podIP"]
        folders = in_pod(
            name, FOLDERS, STATE_DIR, f"{STATE_DIR}/state", f"{STATE_DIR}/state/credential.json",
            "/run/secrets/swarmscribe/pool-token",
        ).split()
        expect(folders[0].startswith("0:"), f"the state mount is not root's: {folders[0]}")
        expect(
            folders[1:] == ["10001:2700", "10001:600", "0:440"],
            f"the state folder, the credential and the token file are {folders[1:]}",
        )
        expect(metrics(name, address)['swarmscribe_follower_state{state="idle"}'] == 1.0,
               f"{name} is not idle")
        loopback = in_pod(name, FETCH, "http://127.0.0.1:9108/healthz", check=False)
        expect(loopback == "", f"{name} also listens on loopback")
        expect(container(pod).get("restartCount") == 0, f"{name} restarted while starting")

    # 2. The NetworkPolicy: the kubelet's probes pass (the pods are Ready), nobody else gets in.
    target = pods()[0]["status"]["podIP"]
    expect(
        not reachable_from_the_leader(target),
        "another pod can read a follower's /metrics: does this cluster's network plugin"
        " enforce NetworkPolicy?",
    )

    # 3. Four short recordings, each transcribed once.
    admin("location", CALLS, "stereo_split")
    admin("location", TALKS, "mono")
    for key in SHORT:
        add(key)
    until(lambda: all(completed(key) for key in SHORT), "the four short recordings")
    for key in SHORT:
        done = job(key)
        expect([o for _, o in done["tried"]] == ["completed"], f"{key}: {done['tried']}")
        text = (done["text"] or "").lower()
        expect(LEFT_WORD in text and RIGHT_WORD in text, f"{key}: the transcript is wrong")
        if key.startswith(CALLS):
            expect(text.startswith("agent: "), f"{key} was not split into speakers")

    # 4. A follower killed mid-job: its container restarts and it is the same follower.
    add(KILLED, LONG_REPEATS)
    victim = held_mid_job(KILLED)
    victim_pod = pod_of(victim)
    in_pod(
        victim_pod,
        "import os, signal;"
        "[os.kill(int(p), signal.SIGKILL) for p in os.listdir('/proc')"
        " if p.isdigit() and int(p) not in (1, os.getpid())]",
        check=False,
    )
    done = until(lambda: completed(KILLED), f"{KILLED} to be redone", 300.0)
    expect(
        [o for _, o in done["tried"]] == ["expired", "completed"] and done["attempts"] == 2,
        f"{KILLED}: expected the lease to expire and the recording to be redone: {done['tried']}",
    )

    def restarted() -> dict | None:
        status = next(container(p) for p in pods() if p["metadata"]["name"] == victim_pod)
        return status if status.get("restartCount") == 1 and status.get("ready") else None

    status = until(restarted, f"{victim_pod}'s container to be started again", 60.0)
    expect(status["lastState"]["terminated"]["exitCode"] == 137, "the kill was not a kill")
    expect(follower_id(victim_pod) == victim, "the restarted container is another follower")
    expect(
        '"event": "registered"' not in kubectl("logs", victim_pod),
        "the restarted container registered again instead of using its credential",
    )
    expect(len(followers()) == 2, "a container restart added a follower row")

    # 5. A pod deleted mid-job hands the recording back and is gone long before its grace.
    add(DELETED, LONG_REPEATS)
    holder = held_mid_job(DELETED)
    holder_pod = pod_of(holder)
    asked = time.monotonic()
    kubectl("delete", "pod", holder_pod, "--wait=true", f"--timeout={GRACE_SECONDS + 30}s")
    report["stop"] = time.monotonic() - asked
    expect(
        report["stop"] < GRACE_SECONDS / 2,
        f"a pod deleted mid-job took {report['stop']:.0f} s of its {GRACE_SECONDS} s grace",
    )
    done = until(lambda: completed(DELETED), f"{DELETED} to be redone", 300.0)
    outcomes = [o for _, o in done["tried"]]
    expect(
        outcomes[0] == "released" and outcomes[-1] == "completed" and done["attempts"] == 1,
        f"{DELETED}: expected a release that counts no attempt, then a completion: {outcomes}",
    )
    until(two_active_followers, "the replacement pod to register")

    # 6. A pod that replaces one that has gone takes over its row.
    before = len(followers())
    kubectl("scale", f"deploy/{DEPLOYMENT}", "--replicas=1")
    until(lambda: len(followers("gone")) >= 1 and len(pods()) == 1, "a follower to be gone")
    kubectl("scale", f"deploy/{DEPLOYMENT}", "--replicas=2")
    until(two_active_followers, "the new pod to register")
    expect(len(followers()) == before, f"the leader has {len(followers())} rows, not {before}")
    kubectl("rollout", "status", f"deploy/{DEPLOYMENT}", "--timeout=120s")

    # 7. The memory guard, under the pod's memory limit.
    location, _, name = TOO_LONG.partition("/")
    admin("silence", location, name, "3600")

    def parked() -> dict | None:
        found = job(TOO_LONG)
        return found if found is not None and found["state"] == "failed" else None

    refused = until(parked, f"{TOO_LONG} to be refused for good")
    reason = refused["failure_reason"] or ""
    expect(
        reason.startswith("out_of_resources: ") and "60 minutes" in reason
        and "2500 MiB (SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB)" in reason,
        f"{TOO_LONG} failed with an unexpected reason: {reason[:200]}",
    )
    expect([o for _, o in refused["tried"]] == ["failed"] * 3, f"{TOO_LONG}: {refused['tried']}")

    # 8. A drained pod parks; deleting it brings a new, active follower.
    drained = followers("active")[0]["id"]
    drained_pod = pod_of(drained)
    resting = next(p for p in pods() if p["metadata"]["name"] == drained_pod)
    address, restarts = resting["status"]["podIP"], container(resting)["restartCount"]
    admin("drain", drained)
    until(
        lambda: metrics(drained_pod, address).get('swarmscribe_follower_state{state="draining"}'),
        "the drained follower to say so in /metrics", 60.0,
    )
    add(AFTER_DRAIN)
    done = until(lambda: completed(AFTER_DRAIN), f"{AFTER_DRAIN} to complete")
    expect(done["tried"][0][0] != drained, "a drained follower took a recording")
    status = next(container(p) for p in pods() if p["metadata"]["name"] == drained_pod)
    expect(
        status["restartCount"] == restarts and status["ready"],
        "a drained pod did not stay up: it must park, not exit",
    )
    kubectl("delete", "pod", drained_pod, "--wait=true", "--timeout=60s")
    until(two_active_followers, "a new follower to replace the drained one")
    expect(len(followers("draining")) == 1, "the drained follower's row did not stay draining")

    # 9. /metrics for a peer named in values; the pods are not restarted for it. No secrets.
    kubectl("rollout", "status", f"deploy/{DEPLOYMENT}", "--timeout=120s")
    names = sorted(p["metadata"]["name"] for p in pods())
    peers = json.dumps([{"podSelector": {"matchLabels": {"app": "leader"}}}])
    install(RELEASE, FOLLOWER_IMAGE, "--set-json", f"networkPolicy.ingress.from={peers}")
    target = pods()[0]["status"]["podIP"]
    until(lambda: reachable_from_the_leader(target), "/metrics to open to the named peer", 60.0)
    expect(sorted(p["metadata"]["name"] for p in pods()) == names, "the upgrade replaced pods")
    for name in names:
        said = kubectl("logs", name)
        expect(token not in said, f"{name}'s log holds the pool token")
        expect("/v1/files/" not in said, f"{name}'s log holds a file link")
        expect(
            LEFT_WORD not in said.lower() and "quarterly" not in said.lower(),
            f"{name}'s log holds transcript text",
        )

    # 10. Revoke the token and its followers: exit 4, again and again. A new token brings
    # the pool back.
    admin("revoke-pool", "kind-pods")

    def all_exited_4() -> bool:
        ended = [container(p).get("lastState", {}).get("terminated", {}) for p in pods()]
        return len(ended) == 2 and all(state.get("exitCode") == 4 for state in ended)

    until(all_exited_4, "both revoked pods to exit 4")
    expect(not followers("active"), "a follower is still active after the revocation")
    store_pool_token("kind-pods-2")
    kubectl("rollout", "restart", f"deploy/{DEPLOYMENT}")
    kubectl("rollout", "status", f"deploy/{DEPLOYMENT}", "--timeout=180s")
    until(two_active_followers, "two new followers to register with the new token")
    return report


def no_gpu() -> None:
    """What a pod does where there is no GPU: a `cuda` image exits 3 and says why; a pod
    that asks for a GPU stays Pending."""
    load(CUDA_IMAGE)
    install("wrong-node", CUDA_IMAGE, "--set", "replicaCount=1")
    install("gpu-pool", CUDA_IMAGE, "--set", "replicaCount=1", "--set", "gpu.enabled=true")

    def pod(release: str) -> dict | None:
        listed = kubectl("get", "pods", "-l", f"app.kubernetes.io/instance={release}", "-o", "json")
        items = json.loads(listed)["items"]
        return items[0] if items else None

    def exited_3() -> str | None:
        found = pod("wrong-node")
        ended = container(found or {}).get("lastState", {}).get("terminated", {})
        return found["metadata"]["name"] if ended.get("exitCode") == 3 else None

    name = until(exited_3, "the cuda image without a GPU to exit 3")
    said = kubectl("logs", name, "--previous").strip().splitlines()[-1]
    expect("no CUDA GPU is available" in said, f"its last line does not say why: {said[:200]}")

    def unschedulable() -> str | None:
        found = pod("gpu-pool") or {}
        for condition in found.get("status", {}).get("conditions", []):
            if condition.get("reason") == "Unschedulable":
                return condition.get("message", "")
        return None

    why = until(unschedulable, "the GPU pod to be found unschedulable", 60.0)
    expect("Insufficient nvidia.com/gpu" in why, f"the GPU pod is unschedulable for: {why}")
    helm("uninstall", "wrong-node", "gpu-pool")
    print(
        f"passed: without a GPU the cuda image exits 3 and says `{said}`; a pod of a GPU "
        "pool stays Pending (Insufficient nvidia.com/gpu)"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="SwarmScribe follower chart on kind")
    parser.add_argument("command", choices=("up", "run", "no-gpu", "down"))
    command = parser.parse_args().command
    try:
        if command == "up":
            up()
        elif command == "down":
            down()
        elif command == "no-gpu":
            no_gpu()
        else:
            report = scenario()
            print(
                f"passed ({MODEL} on cpu): two pods were Ready in {report['ready']:.0f} s and "
                f"registered in {report['registered']:.0f} s; a killed follower came back as "
                "itself and its recording was redone; a pod deleted mid-job was gone in "
                f"{report['stop']:.1f} s and counted no attempt; an hour was refused by the "
                "memory guard; a drained pod parked; revoked pods exited 4 and a new token "
                "brought the pool back"
            )
    except AssertionError as error:
        print(f"FAILED: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Three things in it that are easy to "tidy" wrongly:

- **Which pod is which follower** is read from the credential file inside the pod (`FOLLOWER_ID` prints only the id). A container that restarted logs no `registered` line, because it did not register, so the log cannot say.
- **Step 8 remembers the drained pod's restart count before the drain** and compares with that. The drained pod may be the one that step 4 killed, which has one restart already; expecting zero made the scenario fail on its second run while this plan was written.
- **Step 6, not step 5, asserts that a follower's row is taken over.** In step 5 the replacement pod starts the moment the old one is told to stop, and may register a moment before the old one has deregistered; then it gets a row of its own, which is correct. Step 6 scales down, waits for the row to be `gone`, and only then scales up.

- [ ] **Step 5: Keep the test's work folder out of git and out of image builds**

In `.gitignore`, after the line `e2e/follower-compose/work/`, add:

```
e2e/follower-kind/work/
```

In `.dockerignore`, after the line `e2e/follower-compose/work`, add:

```
e2e/follower-kind/work
```

(The kubeconfig in that folder holds the cluster's admin key; the leader's test image copies the whole repository.)

- [ ] **Step 6: Run the tests to see them pass**

Run: `uv run pytest packages/follower/tests/test_kind_driver.py -q`
Expected: `6 passed`.

- [ ] **Step 7: The whole suite and the linter**

Run: `uv run ruff check . && uv run pytest`
Expected: `All checks passed!`, then `3128 passed, 19 skipped, 5 deselected` (six more passing than at the end of F3a, which left 3122; go by "six more than before" if the numbers have moved) and no failure.

- [ ] **Step 8: Commit**

```bash
git add e2e/follower-kind packages/follower/tests/test_kind_driver.py .gitignore .dockerignore
git commit -m "test(chart): a kind scenario for the follower chart, against a real leader in the cluster

A throwaway leader with Postgres, the chart installed with a pool token from a
Secret, and a driver that kills, deletes, drains and revokes pods and checks
the memory guard under the pod's limit. Run by hand, not in CI.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Install the chart on `kind`, against a real leader, and record it

Spec 10: "F3 also installs the chart on a local `kind` cluster with the `cpu` image". This is done on the development machine, by hand. It was done while this plan was written and passed (the planner's run is in the outcomes document below); this task repeats it with the repository's own files and writes the result down.

**Files:**
- Create: `docs/superpowers/plans/2026-10-05-follower-f3-outcomes.md`

**Interfaces:**
- Consumes: `e2e/follower-kind/run_e2e.py up | run | no-gpu | down` (Task 1); the chart and the listener's limits (F3a), the second of which is in the follower image this task builds; `e2e/compose/Dockerfile` and `docker/follower.Dockerfile`.
- Produces: the outcomes document, which Task 3's README names.

In every shell of this task, first (see "The tools on the Windows machine" in the Global Constraints):

```bash
export PATH="$TEMP/chart-tools/windows-amd64:$TEMP/chart-tools:$PATH:/c/Users/walla/AppData/Local/Programs/DockerDesktop/resources/bin"
```

Rules of the machine, again, because this task is the one that could break them: never stop, remove or retag a container or an image you did not create; never run a `docker ... prune`; never restart Docker; never touch a `kind` cluster other than `swarmscribe-follower-e2e`; the cluster has its own kubeconfig in `e2e/follower-kind/work/`, and `~/.kube/config` must be left as it is.

- [ ] **Step 1: See that the tools and Docker are there**

```bash
helm version --short && kind version && kubectl version --client && docker version --format '{{.Server.Version}}'
kind get clusters
df -h /c | tail -1
```

Expected: `v4.3.0+gbec5b06`, `kind v0.30.0 ...`, `Client Version: v1.33.0`, Docker's version (`29.8.1` here). `kind get clusters` must not list `swarmscribe-follower-e2e` (if it does, a previous run was left behind: `uv run python e2e/follower-kind/run_e2e.py down`). At least 30 GB must be free on C: before building (F2a's final review filled the disk and Docker stopped answering); do not build under that.

- [ ] **Step 2: Build the three images from this branch**

```bash
docker build -t swarmscribe-leader:f3-e2e -f e2e/compose/Dockerfile .
docker build --build-arg MODELS=tiny.en -t swarmscribe-follower:f3-e2e --target cpu -f docker/follower.Dockerfile .
docker build -t swarmscribe-follower:f3-cuda --target cuda -f docker/follower.Dockerfile .
docker image inspect postgres:16 > /dev/null || docker pull postgres:16
export LEADER_IMAGE=swarmscribe-leader:f3-e2e FOLLOWER_IMAGE=swarmscribe-follower:f3-e2e CUDA_IMAGE=swarmscribe-follower:f3-cuda
```

Expected: three builds that end `naming to docker.io/library/...` (most layers come from the cache: the follower's build took 30 s here, because only the source layer changed with F3a's listener), and `postgres:16` present (it is pulled only if it is not there: a pull would move a tag that other work on this machine uses).

The tags are this task's own (`f3-...`), so that no image another agent may be using is replaced; the driver takes the three names from the environment. **Run the `export` line in every shell of steps 3 to 6.** The three images are yours to remove (`docker rmi`) once F3 is merged.

The follower image must be built from this branch: it is what carries F3a's listener into the pods.

- [ ] **Step 3: The cluster, the images and the leader**

Run: `uv run python e2e/follower-kind/run_e2e.py up`
Expected, after one to two minutes:

```
the cluster swarmscribe-follower-e2e is up, with a leader in namespace swarmscribe-e2e
```

It creates the cluster (Kubernetes 1.34.0 with kind 0.30.0), loads the three images, and starts Postgres and the leader. `kind load docker-image` stopped here with `content digest ... not found` for the pulled `postgres:16`; the driver then pipes one platform of the image into the node, which worked.

- [ ] **Step 4: The scenario**

Run: `uv run python e2e/follower-kind/run_e2e.py run`
Expected, after about two minutes (the numbers measured here):

```
passed (tiny.en on cpu): two pods were Ready in 3 s and registered in 4 s; a killed follower came back as itself and its recording was redone; a pod deleted mid-job was gone in 1.9 s and counted no attempt; an hour was refused by the memory guard; a drained pod parked; revoked pods exited 4 and a new token brought the pool back
```

The ten steps it proves are listed at the top of `run_e2e.py`. If it fails, it says which expectation failed. Do not weaken an expectation to make it pass: find out why (the pods' logs, `kubectl describe`), and if the cause is the chart or the follower, fix that where it lives (the chart and the listener are F3a's files, on this same branch), with its checks run again, and say so in your report. Two failures that are the cluster's and not the chart's: step 2 fails where the network plugin does not enforce NetworkPolicy (kind 0.30.0's does), and a machine too slow to hold a long recording for five seconds fails `held_mid_job` with its own hint. The scenario runs once per leader: to run it again, run `up` again (it makes a fresh namespace on the same cluster).

- [ ] **Step 5: Look at a pod yourself**

The pool the scenario left behind is two new followers, registered with the second token. With `K="kubectl --kubeconfig e2e/follower-kind/work/kubeconfig -n swarmscribe-e2e"` and `export MSYS_NO_PATHCONV=1` (Git Bash would otherwise rewrite the paths given to the pod):

```bash
$K get pods
P=$($K get pods -l app.kubernetes.io/instance=pool -o jsonpath='{.items[0].metadata.name}')
$K exec $P -- sh -c 'cat /proc/1/comm; stat -c "%u:%g %a %n" /var/lib/swarmscribe-follower /var/lib/swarmscribe-follower/state /var/lib/swarmscribe-follower/state/credential.json /scratch; stat -L -c "%u:%g %a %n" /run/secrets/swarmscribe/pool-token'
$K exec $P -- swarmscribe-follower doctor --no-model
```

Expected: the leader, Postgres and two `pool-swarmscribe-follower-...` pods, all `1/1 Running` (`kubectl exec` may print a harmless `Unknown stream id` line on Windows); then

```
tini
0:10001 3777 /var/lib/swarmscribe-follower
10001:10001 2700 /var/lib/swarmscribe-follower/state
10001:10001 600 /var/lib/swarmscribe-follower/state/credential.json
0:10001 2777 /scratch
0:10001 440 /run/secrets/swarmscribe/pool-token
```

and a `doctor` that ends

```
state folder: ok (/var/lib/swarmscribe-follower/state, 0.0 GiB free)
scratch folder: ok (/scratch, ... GiB free)
device: cpu
cached models: tiny.en
memory: 2500 MiB may be used (SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB)
model: not checked (--no-model)
leader: answers
joined: yes
result: ready
```

("0.0 GiB free" is the 16 MiB memory-backed state volume.) Write the `stat` lines down for the outcomes document.

- [ ] **Step 6: A `cuda` image where there is no GPU**

Run: `uv run python e2e/follower-kind/run_e2e.py no-gpu`
Expected, after about a minute and a half (most of it loading the 2.5 GB image into the node):

```
passed: without a GPU the cuda image exits 3 and says `error: cuda was requested but no CUDA GPU is available`; a pod of a GPU pool stays Pending (Insufficient nvidia.com/gpu)
```

This is all that can be shown of the GPU path on this cluster. It does not run a follower on a GPU.

- [ ] **Step 7: Delete the cluster**

```bash
uv run python e2e/follower-kind/run_e2e.py down
kind get clusters
git status --short
```

Expected: `deleted the cluster swarmscribe-follower-e2e`; the cluster no longer listed (`No kind clusters found.` when there is no other); and `git status` clean apart from what this task has yet to add (the work folder is ignored and gone).

- [ ] **Step 8: Write the outcomes document**

Create `docs/superpowers/plans/2026-10-05-follower-f3-outcomes.md` with the text below, then replace each `this run:` line with what you saw. Record only what was recorded: a number you did not write down is left out, not estimated.

````markdown
# Follower F3: the chart on a `kind` cluster

The follower spec (section 10) has the chart installed on a local `kind` cluster with the
`cpu` image. This is the record. CI lints the chart, validates what it renders and checks
what must hold (job `chart`); it never installs it. Repeat this run, and add a section here,
when the chart's Deployment or NetworkPolicy changes, or the follower's start-up, stop or
listener does.

How: `e2e/follower-kind/run_e2e.py` (`up`, `run`, `no-gpu`, `down`), with a throwaway leader
and Postgres in the cluster (`e2e/follower-kind/leader.yaml`) and the chart installed with
`e2e/follower-kind/values.yaml`: two followers, `tiny.en` baked in, limits of 2 CPUs and
2500Mi, a 60-second grace period, the leader's lease at 8 seconds.

## The planner's run, 2026-10-05

Machine: Windows 11, Docker Desktop (Engine 29.8.1, WSL 2, 16.6 GB for the VM), kind v0.30.0
(node image Kubernetes v1.34.0, containerd 2.1.3), kubectl v1.33.0, Helm v4.3.0. Built in a
scratch folder from `main` at `96ffd19` with this plan's files.

| What | Result |
|---|---|
| `up` | 1 min 50 s on a new cluster; 1 min 20 s on an existing one (`down`: 2 s). `kind load docker-image` failed for the pulled `postgres:16` (`content digest ... not found`); the fallback worked |
| `run` | passed four times in its final form, 1 min 43 s to 1 min 50 s. An earlier run failed on the driver's own mistake (it expected no restarts of the drained pod, which was the one killed before), corrected |
| Both pods Ready; both registered | 3 s; 3 to 4 s after `helm upgrade --install` |
| The state mount, the state folder, the credential, the token file | `0:10001 3777`, `10001:10001 2700`, `10001:10001 600`, `0:10001 440` |
| The listener | answers on the pod's address; loopback refuses; PID 1 is `tini` |
| Another pod asking a follower's `/metrics` | timed out, with the pods Ready (the kubelet's probes pass); after `networkPolicy.ingress.from` named the leader's pod: 200 from it, still closed to the Postgres pod, no pod restarted |
| A follower killed mid-job (`SIGKILL` to the process) | container restarted, exit 137; the same follower afterwards, no `registered` line; the recording `expired`, then `completed`, 2 attempts |
| A pod deleted mid-job | gone in 1.8 to 2.1 s of its 60 s; the recording `released`, then `completed`, 1 attempt |
| Scale to 1, then to 2 | the new pod took over the `gone` row; the leader's list did not grow |
| One hour of silence under the 2500Mi limit | failed three times: `out_of_resources: OutOfMemory: a recording of 60 minutes needs about 4121 MiB here and this follower may use 2500 MiB (SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB)` |
| A drained follower | `swarmscribe_follower_state{state="draining"} 1.0`; the pod stayed Running and was not restarted; the next recording went to the other; deleting the pod brought a new, active follower and the row stayed `draining` |
| The pool token revoked with its followers | both pods `CrashLoopBackOff`, exit 4, `stopping: this follower has been revoked`; a new token in the Secret and `kubectl rollout restart` brought two new followers in 6 s |
| `no-gpu` | passed twice, 1 min 23 s and 1 min 25 s: the `cuda` image exited 3 with `error: cuda was requested but no CUDA GPU is available`; the GPU pool's pod `Pending`: `0/1 nodes are available: 1 Insufficient nvidia.com/gpu` |
| A trickling client, from a pod the policy let in | dropped after 5.0 s; with 20 silent connections open a request was turned away in 0.01 s, and 6 s later answered 200; no restart |
| `models.volume: emptyDir` with an image without a model | downloaded `distil-large-v3` through the NetworkPolicy and registered 30 s after the pod started |

Memory of one follower in a pod (`/proc/<pid>/status`, every 3 s):

| | Held (RSS) | Peak so far |
|---|---|---|
| `tiny.en` loaded | 230 MiB | 282 MiB |
| after one hour mono; a second; one hour split; a third mono | 432; 460; 472; 442 MiB | 3784; 3899; 3899; 3899 MiB |
| `distil-large-v3` read from a cache; after half an hour mono; after a second | 1766; 1779; 1779 MiB | 1937; 3509; 3509 MiB |
| `distil-large-v3` just downloaded by the same process | 2257 MiB | 2429 MiB |

The two half-hour recordings ran under a limit of 3930Mi (1730 + 400 + 1800) and took 360 and
349 s on 4 CPUs; the cgroup's own count peaked at 3540 MiB. A `tiny.en` pod limited to 3990Mi
(230 + 100 + 3660 for one 61-minute recording, the sizing before F3) took the first such
recording and refused the second: `a recording of 61 minutes needs about 4134 MiB here and
this follower may use 3990 MiB`. The "hour" here is the 5-second fixture 720 times: 61 minutes.

## The run of Task 2 (plan F3b)

Run on: this run: the date. Machine and versions (`cmd /c ver`, `docker version --format
'{{.Server.Version}}'`, `kind version`, `helm version --short`, `kubectl version --client`):
this run: what they printed.

- Step 3 (`up`), this run: how long it took, and its last line.
- Step 4 (`run`), this run: how long it took, and its last line, whole.
- Step 5 (a pod by hand), this run: the five `stat` lines and `doctor`'s `memory:` line.
- Step 6 (`no-gpu`), this run: its last line.
- Step 7 (`down`), this run: that `kind get clusters` no longer lists the cluster.

## What this does not prove

- **A GPU pool on Kubernetes.** The cluster has no GPU: the device plugin, a `RuntimeClass`
  and a follower transcribing on a GPU in a pod were not run. The `cuda` image itself was
  run on a GPU in Docker (the F2 outcomes).
- Any cluster but kind on one node; a network plugin other than kind's; an IPv6 or
  dual-stack cluster (only the setting's parsing of a bracketed address is tested).
- A leader on TLS, and one on a private CA (`leader.ca`): the leader here is plain http
  inside the cluster.
- `models.volume: persistentVolumeClaim`, `metrics.scrapeAnnotations` with a Prometheus,
  and a PodDisruptionBudget: rendered and schema-checked only.
- A real node drain (`kubectl drain`): a pod delete takes the same path through the kubelet.
- A recording of people talking: the long recordings are one phrase repeated.
- The chart's default size (6Gi) with `distil-large-v3` and an hour-long recording: the
  figures it rests on were measured (above, with half-hour recordings), the hour itself
  was not run in a pod. `large-v3` on a GPU was not measured in a pod at all.
````

- [ ] **Step 9: Commit**

```bash
git add docs/superpowers/plans/2026-10-05-follower-f3-outcomes.md
git commit -m "test(chart): install the follower chart on kind against a real leader, and record it

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

**If the scenario cannot be made to pass on this machine** (kind will not start, Docker is unwell): do not commit a record of a run that did not happen. Write under "The run of Task 2" exactly what was tried and how it failed, keep the planner's run as the only evidence, and in Task 3 word the README's "What has been run" as the planner's run only. Report it; the owner decides whether F3 is done.

---

### Task 3: The README, the spec's amendments and the follow-ups

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-10-04-follower-design.md`
- Modify: `docs/superpowers/plans/2026-10-04-follower-f1-followups.md`

**Interfaces:**
- Consumes: the chart's value names and `REQUEST_SECONDS = 5.0`, `MAX_CONNECTIONS = 8` (F3a), `e2e/follower-kind/` (Task 1), the outcomes document and what Task 2's run showed.
- Produces: documentation only.

Before you start, read `docs/superpowers/plans/2026-10-05-follower-f3-outcomes.md`, section "The run of Task 2". The README text below states what the planner's run showed. If Task 2's run showed something else (a step that could not be run, a different outcome), change the sentences of "What has been run, and what has not" to match what the outcomes document records, and say so in your report. Numbers that vary from run to run (seconds) are given below as the planner measured them and are worded as such.

- [ ] **Step 1: The status table**

In `README.md`, replace

```
| `swarmscribe-follower` — the agent: join, claim, transcribe, upload | Agent built; images, Helm chart and service install next |
```

with

```
| `swarmscribe-follower` — the agent, its two images and its Helm chart (`deploy/helm/swarmscribe-follower`, one release per pool) | Built; the service install for outside machines is next |
```

and replace

```
| Helm chart for the leader and followers | Not started |
```

with

```
| Helm chart for the leader, and autoscaling | Not started |
```

- [ ] **Step 2: Three places in "Follower images"**

**1.** In the table "What each kind of mount needs", replace the end of the `emptyDir` row,

```
the follower creates it as its own, `0700`. Not yet run on a cluster; the chart (F3) will do this |
```

with

```
the follower creates it as its own, `0700`. The chart does exactly this ("Deploy a follower pool"); on a kind cluster the mount was `0:10001` mode `3777` and the folder `10001:10001` mode `2700` |
```

**2.** In the paragraph **Health and metrics.**, replace

```
your Prometheus is on, never to the internet: it has no authentication. Outside the images
the listener is off unless the variable is set.
```

with

```
your Prometheus is on, never to the internet: it has no authentication. Outside the images
the listener is off unless the variable is set. Wherever it listens, it answers one request
per connection (`Connection: close`), drops a connection after five seconds in all however
slowly its bytes arrive, and holds at most eight connections at once (a ninth is closed
unanswered): a slow or silent client cannot keep a thread, or keep a probe waiting for long.
```

**3.** In the paragraph **Memory.**, replace

```
So a CPU follower with `distil-large-v3` for recordings of up to one hour needs about
5.3 GiB (what the follower counts: 1730 + 100 + 3600 MiB), and one for three hours about
12 GiB; a GPU follower with `large-v3` needs 3.1 GiB to load its model at all. Before each
```

with

```
So a CPU follower with `distil-large-v3` for recordings of up to one hour needs about
5.6 GiB (what the follower counts for the first one, 1730 + 100 + 3600 MiB, and 300 MiB for
what the process keeps after a long recording: see "Sizing a pool"), and one for three hours
about 12.6 GiB; a GPU follower with `large-v3` needs 3.1 GiB to load its model at all. Before each
```

- [ ] **Step 3: The new section**

In `README.md`, immediately before the line `## Develop`, insert this section (it ends with a blank line before `## Develop`):

````markdown
## Deploy a follower pool

A pool is a set of followers that are alike: one image, one model, one size, one kind of
node. One release of the chart `deploy/helm/swarmscribe-follower` is one pool; install it
again under another name for another pool (a CPU pool and a GPU pool, two sizes, two
clusters). It needs:

- **A leader the pods can reach**, by the address in `leader.url`. The leader's own file
  links are built from its `SWARMSCRIBE_PUBLIC_URL`, so that address must be reachable from
  the pods too. The leader has no chart yet; the follower chart needs only its URL.
- **An image with its model baked in**, where the cluster can pull it ("The pool's image").
- **A pool token in a Secret, created beforehand.** The chart never creates a Secret.
- **For a GPU pool:** nodes with an NVIDIA GPU, the NVIDIA device plugin (it advertises
  `nvidia.com/gpu`) and whatever your cluster needs to hand the driver to a container
  (often a `RuntimeClass` named `nvidia`).

### The pool's image

No image is published, so `image.repository` and `image.tag` have no default: both are
required, and the render fails saying so. Bake the model in ("Follower images" above): a pod
then starts without Hugging Face, and every pod of the pool holds the same, checked model.

```
docker build --build-arg MODELS=distil-large-v3 -t swarmscribe-follower:cpu-distil-large-v3 \
  --target cpu -f docker/follower.Dockerfile .
docker build --build-arg MODELS=large-v3 -t swarmscribe-follower:cuda-large-v3 \
  --target cuda -f docker/follower.Dockerfile .
# a local cluster:        kind load docker-image swarmscribe-follower:cpu-distil-large-v3
# a registry of your own: docker tag ... && docker push ...
```

The leader's profile for the device must name the baked model (`swarmscribe-admin profiles
set`): a baked image is offline, and a pod asked for a model it does not hold hands the
recording back and exits `3`. If `kind load docker-image` stops with "content digest ... not
found", load from an archive as "Deploy the fleet console" shows.

### Kubernetes, with the Helm chart

1. Create the pool token on the leader, and the Secret in the pool's namespace. A pool
   token does not expire and registers any number of pods ("Pool tokens versus join
   tokens").

   ```
   swarmscribe-admin pool-tokens create --name cpu-pods --pool default   # shows the token, once
   kubectl create namespace transcribe
   kubectl -n transcribe create secret generic swarmscribe-pool-token \
     --from-file=pool-token=pool-token.txt    # a file that holds the token and nothing else
   ```

   (`--from-file`, so that the token is not in your shell's history; delete the file
   afterwards. A newline at its end does no harm.)

2. Write the values. A CPU pool:

   ```yaml
   image:
     repository: swarmscribe-follower   # or your registry's name for it
     tag: cpu-distil-large-v3
   leader:
     url: https://leader.example.org
   poolToken:
     existingSecret: swarmscribe-pool-token
   pool: default
   replicaCount: 4
   ```

   A GPU pool, as a second release:

   ```yaml
   image:
     repository: swarmscribe-follower
     tag: cuda-large-v3
   leader:
     url: https://leader.example.org
   poolToken:
     existingSecret: swarmscribe-gpu-pool-token
   pool: gpu
   replicaCount: 2
   gpu:
     enabled: true              # one GPU per pod, SWARMSCRIBE_FOLLOWER_DEVICE=cuda, no surge
   runtimeClassName: nvidia     # if your cluster hands out GPUs through a RuntimeClass
   nodeSelector:
     nvidia.com/gpu.present: "true"
   tolerations:
     - {key: nvidia.com/gpu, operator: Exists, effect: NoSchedule}
   ```

3. Install, one release per pool:

   ```
   helm upgrade --install cpu-pool deploy/helm/swarmscribe-follower -n transcribe -f cpu-values.yaml
   kubectl -n transcribe get pods -l app.kubernetes.io/instance=cpu-pool
   ```

   A pod is `Running` and ready within seconds: that says its threads are up, not that it
   has joined. That it has loaded its model and registered is in its log (`registered`) and
   in `swarmscribe-admin followers list`.

What the chart installs:

| Object | What it is |
|---|---|
| Deployment | `replicaCount` followers running `run` under the image's init: non-root (10001, `fsGroup` 10001), read-only root filesystem, no capabilities, no service-account token. Startup and liveness probes on `/healthz`; no readiness probe and no Service, because nothing connects to a follower. Rollouts replace a quarter of the pool at a time, with a surge pod on a CPU pool and none on a GPU pool |
| NetworkPolicy | egress to DNS and to anywhere on port 443 and the port of `leader.url`, except loopback, link-local (cloud metadata) and reserved ranges; ingress to nobody but the peers in `networkPolicy.ingress.from` |
| ServiceAccount | one with no token mounted; a follower never calls the Kubernetes API |
| PodDisruptionBudget | only with `podDisruptionBudget.enabled` and more than one replica |

Values:

| Value | Setting or meaning |
|---|---|
| `image.repository`, `image.tag` | the image. Both required (`image.digest` wins over the tag) |
| `leader.url` | `SWARMSCRIBE_LEADER_URL`. Required, `https`. `leader.allowHttp: true` accepts plain `http` (a test cluster only) |
| `leader.ca.existingConfigMap`, `.key` | CA certificates (PEM) for a leader on a private CA (`SWARMSCRIBE_LEADER_CA_FILE`) |
| `poolToken.existingSecret`, `.key` | the Secret that holds the pool token (key `pool-token`). Required. Mounted as a file, mode 0440, and read only when a pod registers |
| `pool` | the pool name the followers report; the token decides the real pool |
| `replicaCount` | followers; `0` parks the pool |
| `resources` | CPU and memory; the memory limit is what the memory guard goes by ("Sizing a pool") |
| `gpu.enabled`, `gpu.resource`, `runtimeClassName`, `nodeSelector`, `tolerations`, `affinity` | a GPU pool and where it runs |
| `models.volume` | `none` (baked, the default), `emptyDir` (every new pod downloads its model) or `persistentVolumeClaim` with `models.existingClaim` |
| `scratch.sizeLimit` | room for the recording being transcribed (20Gi) |
| `terminationGracePeriodSeconds` | 900; the follower is told 30 s less |
| `settings` | any other `SWARMSCRIBE_FOLLOWER_*` setting, without the prefix: `ALLOWED_MODELS`, `STARTUP_MODEL`, `LOG_FORMAT`, ... |
| `extraEnv` | other environment, such as `HTTPS_PROXY` |
| `healthPort`, `metrics.scrapeAnnotations`, `networkPolicy.*`, `podDisruptionBudget.*` | described in `values.yaml` |

`helm template` fails, saying why, when the image, `leader.url` or the Secret's name is
missing; when `leader.url` is not `http(s)://host[:port][/path]`, or is `http` without
`leader.allowHttp`; when a value has the wrong type or a name the chart does not know
(`replicas` for `replicaCount`: `values.schema.json` allows no unknown value); when
`settings` or `extraEnv` names something the chart sets itself (the state folder, the
listener, the memory limit, the device, the token ...); and when a claim is not named for a
`persistentVolumeClaim` model volume.

### Sizing a pool

The memory limit decides the longest recording a pod takes. Before each job the follower
adds its estimate for the recording to what the process holds at that moment and compares
the sum with the limit, which the chart passes on (`SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB`);
a recording that cannot fit is failed `out_of_resources` with its length and the limit in
the reason, three times, and parked. Size the limit as:

```
what the model holds once loaded  +  0.4 GiB  +  3.6 GiB x hours of the longest recording
```

(3.9 GiB per hour where recordings are split into channels.) The 0.4 GiB is the 100 MiB a
job of any length adds and 300 MiB that the process keeps after long recordings: measured on
2026-10-05 in a pod, a follower held 230 MiB with `tiny.en` loaded and 432 to 472 MiB after each of four hour-long recordings (it does not keep growing; with `distil-large-v3` and half-hour recordings it kept 13 MiB). So a pod sized for exactly one long recording
by the table in "Follower images" refuses the next one: one that was tried did.

| Pool | Recordings up to | Memory limit (and request) |
|---|---|---|
| `distil-large-v3` on a CPU (1.7 GiB loaded) | 1 hour | 6Gi (the default) |
| | 2 hours | 10Gi |
| | 3 hours | 13Gi |
| `large-v3` on a GPU (0.7 GiB loaded, 3.1 GiB while loading) | 1 hour | 5Gi |
| | 2 hours | 9Gi |
| | 3 hours | 12Gi |
| `large-v3` on a CPU (3.5 GiB loaded) | 1 hour | 8Gi |

Keep the memory request equal to the limit: a follower uses nearly all of it for a few
seconds of every long recording, and a node that promised the same memory twice would kill
it there. The model figures are for a baked model, or one read from a cache. A pod that had
to download its model in the same start holds more until its container restarts
(2257 MiB against 1766 MiB with `distil-large-v3`), which is one more reason to bake. On a CPU pool the CPU limit is also the number of threads the engine uses
(`OMP_NUM_THREADS`); speed follows it. A GPU pod needs little CPU and exactly one GPU: one
follower uses one GPU, whole, so a node with four GPUs runs four pods.

### What the pods do

- **A stop** (a rollout, a scale-down, a node drain, `kubectl delete pod`): the follower
  finishes the recording in hand if its estimated time left fits in
  `terminationGracePeriodSeconds` less 30 s, and otherwise hands it back at the end of the
  segment it is on, without a counted attempt; another follower redoes it from the start.
  On the kind cluster a pod deleted in the middle of a recording was gone in two seconds.
  What is lost is the compute already spent, so there is no PodDisruptionBudget by default:
  it would protect nothing else and make every node drain wait.
- **A crash or a kill** (out of memory, a node that dies): the container restarts and is
  the same follower, because its credential lives in a memory-backed `emptyDir` that
  outlives the container. The recording it held is redone when its lease expires, and that
  attempt is counted.
- **A new pod** registers with the pool token and takes over the row of a pod of the same
  token that has gone, so the leader's list stays the size of the pool at its largest.
- **Drain** (`swarmscribe-admin followers drain <id>`): the pod finishes its recording and
  parks. It stays `Running`, is not restarted, and takes nothing more
  (`swarmscribe_follower_state{state="draining"}` is 1). Delete the pod to replace it: its
  replacement is a new, active follower, and the drained row stays `draining`.
- **A pod that keeps restarting** says why in the last line of its log (`kubectl logs
  --previous`), and its exit code is in `kubectl describe pod`: `2` a setting is wrong; `3`
  this pod cannot do the work: a `cuda` image on a node without a GPU (`error: cuda was
  requested but no CUDA GPU is available`), or a model the image does not hold; `4` the
  pool token was refused or the follower was revoked. Each restart loads the model again
  before it finds out, with Kubernetes' growing back-off in between.
- **Shutting a pool out:** `swarmscribe-admin pool-tokens revoke <name> --revoke-followers`.
  Every pod then exits `4`, again at every restart: a revoked follower keeps its credential
  and never registers again by itself. **To bring the pool back,** create a new pool token,
  put it in the Secret, and `kubectl rollout restart deployment/<release>-swarmscribe-follower`:
  new pods have an empty state folder and register with the new token.
- **A changed Secret** needs no restart otherwise. The token is a mounted file, read only at
  a registration; the kubelet refreshes the file, and running followers keep their
  credentials.

### Probes, metrics and the network

The follower listens on the pod's own address (never loopback, never a Service) on
`healthPort` (9108). `/healthz` answers 200 while its threads are alive: also while the
model loads and while the leader is away, so a leader outage never restarts a pod that is
transcribing. `/metrics` is the Prometheus text of "Follower images". It has no
authentication, so by default the NetworkPolicy lets nobody in; the kubelet's probes are not
affected, because they come from the pod's own node, which a NetworkPolicy never cuts off.
To scrape, name your Prometheus:

```yaml
networkPolicy:
  ingress:
    from:
      - namespaceSelector: {matchLabels: {kubernetes.io/metadata.name: monitoring}}
metrics:
  scrapeAnnotations: true    # prometheus.io/scrape, /port and /path on the pods
```

The listener answers one request per connection and drops a connection after five seconds
in all, however slowly its bytes arrive, and holds at most eight at once: a slow or silent
client cannot keep the kubelet's probe waiting for long. To look at a pod by hand, ask from
inside it (`kubectl port-forward` reaches loopback, where nothing listens):

```
kubectl -n transcribe exec <pod> -- python -c "import os, urllib.request; print(urllib.request.urlopen('http://' + os.environ['POD_IP'] + ':9108/metrics').read().decode())"
```

Egress is open to port 443 and the port of `leader.url` anywhere but loopback, link-local
(the cloud metadata addresses) and reserved ranges: a NetworkPolicy cannot name a host, and
the leader's file links may point at a storage service. Narrow
`networkPolicy.egress.https.cidrs` to your leader and storage if you can. Peers are matched
after Service address translation: for a leader inside the cluster the port that counts is
its pod's, so add it to `networkPolicy.egress.https.ports` if it differs from the port in
`leader.url`. A NetworkPolicy needs a network plugin that enforces it.

### What has been run, and what has not

CI lints the chart, validates what it renders against the Kubernetes schemas and checks
what must hold (`deploy/helm/swarmscribe-follower/ci/check_render.py`), for a CPU pool and a
GPU pool. On 2026-10-05 the chart was also installed on a local kind cluster (kind v0.30.0,
Kubernetes 1.34.0) against a real leader with Postgres, with the `cpu` image and `tiny.en`
(`e2e/follower-kind/`; the record is
`docs/superpowers/plans/2026-10-05-follower-f3-outcomes.md`): two pods became ready and
registered with a pool token from the Secret; recordings were transcribed; a killed
follower came back as itself; a pod deleted mid-recording handed it back; a replacement
pod took over a gone row; an hour-long recording was refused under a 2500Mi limit; a
drained pod parked; the NetworkPolicy kept another pod out of `/metrics` and let a named
one in; and revoked pods exited `4` until a new token and a rollout restart. The same run
shows a `cuda` image exiting `3` on a node without a GPU, and a GPU pool's pod staying
`Pending` there.

Not run:

- **A GPU pool on Kubernetes.** A kind cluster has no GPU. The `cuda` image was run on a
  GPU in Docker only (F2); the chart's GPU values are rendered and schema-checked, not run.
- A leader on a private CA (`leader.ca`), an IPv6 or dual-stack cluster, a persistent model
  volume, scrape annotations with a real Prometheus, a PodDisruptionBudget, a real node
  drain (a pod delete takes the same path), and any cluster but kind.
- The kind test is run by hand, not in CI.
- Autoscaling on queue depth belongs to the leader's chart (roadmap item 6).
````

- [ ] **Step 4: Check the README's claims against the files**

Every value name, path and figure in the new section must be one that exists. Run:

```bash
C=deploy/helm/swarmscribe-follower
for name in image.repository image.tag image.digest leader.url leader.allowHttp leader.ca.existingConfigMap poolToken.existingSecret pool replicaCount resources gpu.enabled gpu.resource runtimeClassName nodeSelector tolerations affinity models.volume models.existingClaim scratch.sizeLimit terminationGracePeriodSeconds settings extraEnv healthPort metrics.scrapeAnnotations networkPolicy.ingress.from networkPolicy.egress.https.cidrs networkPolicy.egress.https.ports podDisruptionBudget.enabled; do
  helm show values $C | uv run python -c "import sys, yaml; v = yaml.safe_load(sys.stdin); [v := v[k] for k in '$name'.split('.')]" 2>/dev/null || echo "MISSING: $name"
done
printf 'image: {repository: swarmscribe-follower, tag: cuda-large-v3}\nleader: {url: https://leader.example.org}\npoolToken: {existingSecret: swarmscribe-gpu-pool-token}\npool: gpu\nreplicaCount: 2\ngpu: {enabled: true}\nruntimeClassName: nvidia\nnodeSelector: {nvidia.com/gpu.present: "true"}\ntolerations: [{key: nvidia.com/gpu, operator: Exists, effect: NoSchedule}]\n' > "$TEMP/readme-gpu-values.yaml"
helm template gpu-pool $C -n transcribe -f "$TEMP/readme-gpu-values.yaml" > "$TEMP/readme-gpu.yaml" && kubeconform -strict -summary -kubernetes-version 1.33.0 "$TEMP/readme-gpu.yaml"
```

(with the tools on the PATH, as in the Global Constraints; each line of the loop looks one name up in the chart's own `values.yaml`). Expected: no `MISSING:` line, and `Summary: 3 resources found in 1 file - Valid: 3, Invalid: 0, Errors: 0, Skipped: 0` for the README's GPU values.

- [ ] **Step 5: The spec's amendments**

At the very end of `docs/superpowers/specs/2026-10-04-follower-design.md` (after the last line of "Amendments after F2"), add:

````markdown
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
  `networkPolicy.ingress.from` names peers (the kubelet's probes come from the node and are
  not affected). Egress is DNS, and TCP 443 and the port of `leader.url` to anywhere but
  loopback, link-local and reserved ranges.
- **PodDisruptionBudget (8.2).** None by default; `podDisruptionBudget.enabled` renders one
  for more than one replica.
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
````

- [ ] **Step 6: The follow-ups**

In `docs/superpowers/plans/2026-10-04-follower-f1-followups.md`, replace the whole section `## F3 (chart)` (its heading and its five bullets, up to the line before `## F4 (native install)`) with:

````markdown
## F3 (chart)

All done in F3 (`plans/2026-10-05-follower-f3a-listener-and-chart.md` and `plans/2026-10-05-follower-f3b-kind-install-and-guide.md`):

- The 900 s grace depends on I1. *The chart sets `terminationGracePeriodSeconds: 900` and the follower's grace to 870.*
- A revoked or drained pod reloads the model on every restart (M5). *A drained pod parks and is not restarted. A revoked pod still loads its model at every restart, spaced by Kubernetes' back-off; the README says so. Left as it is.*
- The state folder as a 0700 subfolder of the `emptyDir`. *Done, and measured on a kind cluster.*
- `ON_DRAINED=park`. *Done.*
- The pool token as a file. *Done: a Secret volume, mode 0440, never a subPath.*
````

In the same file, in the section "Left open from the F2a final review", replace the bullet that begins `- F3: the chart's state `emptyDir` must pass the trust check` with

```
- F3: the chart's state `emptyDir` passes the trust check through a 0700 folder the follower creates inside the mount, and the `kind` test restarts a container and sees the same follower come back. *Done in F3 (`e2e/follower-kind`, step 4).*
```

and the bullet that begins ``- `tini` warns on stderr when it is not PID 1`` with

```
- `tini` warns on stderr when it is not PID 1 (`--init`, or a pod with `shareProcessNamespace`). *Decided in F3: the chart never shares the process namespace (its render check refuses it), `tini` is PID 1 in the pod, and nothing is silenced.*
```

Then add at the end of the file:

````markdown
## Left open after F3 (2026-10-05)

- A GPU pool has never run on Kubernetes: the chart's GPU values are rendered and validated only. The first cluster with GPU nodes should run `doctor` in a pod and one recording, and record it in the F3 outcomes.
- The `kind` scenario is run by hand. As a CI job it would take about five minutes (ruling 2 of the F3b plan, the owner's to decide).
- `leader.ca` (a private CA), `models.volume: persistentVolumeClaim`, `metrics.scrapeAnnotations` and the PodDisruptionBudget are rendered and schema-checked, not run in a cluster. The leader's chart (roadmap item 6) will bring a TLS leader to test the first against.
- A pod is Ready before it has registered (no readiness probe, by the spec). A readiness signal would need a second path on the listener.
- The listener's cap bounds threads but cannot keep a place for the kubelet: a peer the NetworkPolicy lets in, reconnecting without pause, could make the liveness probe fail. The default policy lets nobody in.
- No `PodMonitor` (it needs the Prometheus operator's CRD); pod annotations are offered instead.
- A follower that downloads its model at start-up holds about 0.5 GiB more than one that reads it from a cache, until its container restarts (measured with `distil-large-v3`: 2257 against 1766 MiB). Not looked into; baked models avoid it.
````

- [ ] **Step 7: The linter, and a last look**

Run: `uv run ruff check . && git diff --stat`
Expected: `All checks passed!`, and only `README.md`, the spec and the follow-ups changed (no Python changed in this task, so the suite is not run again).

- [ ] **Step 8: Commit**

```bash
git add README.md docs/superpowers/specs/2026-10-04-follower-design.md docs/superpowers/plans/2026-10-04-follower-f1-followups.md
git commit -m "docs(follower): deploy a follower pool: the chart, GPU pools, sizing, what the pods do; spec amendments after F3

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## For the owner

Not tasks; decisions, and what F3 could not prove.

- **Ruling marked (owner)** above: 2 (the `kind` install is not a CI job). F3a's are its rulings 3 (who reaches `/metrics`), 5 (the token only from an existing Secret) and 9 (the default pod size, 6Gi).
- **The GPU path has never run on Kubernetes.** A kind cluster has no GPU. What is shown: the `cuda` image exits 3 in a pod and says why, a GPU pool's pod stays `Pending` where no node has a GPU, and the chart's GPU values render and validate. A pod with a GPU has not been tried; the README says so in "What has been run, and what has not". The first real GPU cluster is the test, and its result belongs in the outcomes document.
- **The scenario is run by hand.** After F3 nothing re-runs it unless someone does; the outcomes document says when to (a change of the chart's Deployment or NetworkPolicy, or of the follower's start-up, stop or listener).
- **Not pushed to a registry**, as F2: the chart has no default image, and the guide says to build and load or push one.

## Self-Review

**Spec coverage.** Spec 10 ("F3 also installs the chart on a local `kind` cluster with the `cpu` image"): Tasks 1 and 2. Spec 11, step 4 ("its README (pools, GPU nodes, baked models, the pool token, sizing)"): Task 3, sections "Deploy a follower pool", "The pool's image", "Kubernetes, with the Helm chart", "Sizing a pool". 8.2's "things an operator must know" (a new pod takes over a gone row; a drained pod parks; shutting a pool out with `--revoke-followers`; autoscaling belongs to roadmap item 6): Task 3's "What the pods do", each proven by the scenario (steps 6, 8, 10). 5.3 (a container restart reuses the credential; a new pod registers with the pool token): steps 1 and 4. 5.6 (a stop that does not fit releases, without a counted attempt): step 5. 6.5 (exit 4 and exit 3 as `CrashLoopBackOff` with the reason as the last log line): step 10 and `no-gpu`. 6.6 (a kill costs one counted attempt and loses nothing): step 4. 5.7 (the guard under the pod's limit): step 7. The spec's amendments for everything F3a and this plan changed: Task 3, step 5. The F1 and F2a follow-ups assigned to F3: Task 3, step 6.

**What the brief asked the install to prove.** A follower pod becomes Ready, registers, and transcribes a real recording submitted to the leader: steps 1 and 3. A drained or terminated pod's job is finished or redone: steps 5 and 8 (and step 4 for a kill). An over-long recording is refused by the guard under the pod's memory limit: step 7. The `cuda` image exits 3 in a pod: `no-gpu`.

**Not covered, and said so in the README and the outcomes document:** a GPU in a pod; a leader on TLS or a private CA in a cluster; IPv6; a persistent model volume; scrape annotations with a real Prometheus; a PodDisruptionBudget and a real node drain; any cluster but kind; an hour-long recording with `distil-large-v3` under the default 6Gi (two half-hour ones under a limit sized by the same formula were run).

**Placeholders.** One kind, deliberate: the `this run:` lines of the outcomes document, which only the person running Task 2 can fill in. Everything else is given whole.

**Names.** The driver's `CLUSTER`, `NAMESPACE` (`swarmscribe-e2e`), `RELEASE` (`pool`), `DEPLOYMENT` (`pool-swarmscribe-follower`), the Secret `pool-token` and the label `app: leader` match `leader.yaml`, `values.yaml` and `test_kind_driver.py`. `admin.py`'s commands (`pool-token`, `profile`, `location`, `recording`, `silence`, `drain`, `revoke-pool`, `state`) are the ones `run_e2e.py` calls, with the arguments it gives them, and the keys of `state`'s answer (`followers[].id/state/pool/device/models`, `jobs[key].state/attempts/leased_by/failure_reason/tried/text`) are the ones the driver reads. The chart values the driver sets (`image.repository`, `image.tag`, `replicaCount`, `gpu.enabled`, `networkPolicy.ingress.from`) and those in `e2e/follower-kind/values.yaml` are F3a's names. The sample names the driver reads (`swarmscribe_follower_state{state="idle"}`, `{state="draining"}`) are what `metrics.py` renders, and the folder modes it expects (`10001:2700`, `10001:600`, `0:440`) are the ones measured. `LONG_REPEATS`, `GRACE_SECONDS` and `image_parts` are what `test_kind_driver.py` uses. The README's value names are checked against `values.yaml` by Task 3's step 4.
