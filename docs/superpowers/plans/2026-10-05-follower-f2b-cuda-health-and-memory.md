# Follower F2b — Metrics, Health Listener, Memory Guard and the CUDA Image Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the follower `/metrics`, a health listener the image probes, and a memory guard with measured figures; add the `swarmscribe-follower:cuda` image and prove it on the development machine's GPU against a real leader; and document both images.

**Architecture:** Three small modules join the follower: `metrics.py` (counters in a registry of the follower's own), `health.py` (a standard-library HTTP listener for `/healthz` and `/metrics`, off unless configured, started before the model is loaded) and `memory.py` (an estimate of what a recording needs, from its length, against the container's or the machine's memory). The image turns the listener on at loopback and gains a `HEALTHCHECK`. A second build target, `cuda`, adds cuBLAS from the `nvidia-cublas-cu12` wheel through a `cuda` extra and asks for a GPU outright. The Compose test of F2a is extended to see all of it, and runs once with the `cuda` image on a real GPU.

**Tech Stack:** Python 3.12, `prometheus-client` 0.26, `http.server` (standard library), PyAV 18 (already there, for a recording's length), CTranslate2 4.8.2 with `nvidia-cublas-cu12` 12.9.2.10, Docker 29 with the NVIDIA container runtime, pytest.

**Spec:** `docs/superpowers/specs/2026-10-04-follower-design.md` (decisions D15, D16, D18, D22; sections 4.1, 5.2, 5.4, 5.7, 8.1, 9, 10 and ruling R5), with `docs/superpowers/plans/2026-10-04-follower-f1-followups.md` (section "F2").

**This plan is the second of two.** It needs F2a (`2026-10-05-follower-f2a-cpu-image-and-compose.md`) merged: `docker/follower.Dockerfile` with its stages `manifests`, `deps-cpu`, `build-cpu`, `models`, `runtime`, `cpu`; `docker/check-follower-image.sh <image> <cpu|cuda> [model]`; `e2e/follower-compose/` with `Target`, `CPU` and `target` in its driver; `doctor --no-leader`.

## Global Constraints

- "An optional HTTP listener serving `/healthz` and `/metrics`; loopback in the image, pod IP in the chart, off in a native install" (D18). "No port is opened unless the health listener is configured, and by default it listens on loopback" (spec 7). `SWARMSCRIBE_FOLLOWER_HEALTH_ADDR`: "image: `127.0.0.1:9108`; native: off" (spec 4.1).
- "`/healthz` answers `200` while the supervisor has ticked in the last 30 seconds and, during a job, the lease keeper has run its loop within three heartbeat intervals. It reports that the threads are alive, not that the leader answered: a leader outage must not make Kubernetes kill a pod that is transcribing" (spec 9).
- "Start the health listener, if configured, so that a slow model download in step 5 is not taken for a failed start" (spec 5.2, step 2; F1 follow-up "Start the health listener before the model load").
- Metrics (spec 9): `swarmscribe_follower_jobs_total{outcome}`, `…_audio_seconds_total`, `…_transcribe_seconds_total`, `…_job_progress`, `…_model_load_seconds`, `…_heartbeat_failures_total`, `…_download_bytes_total`, `…_upload_bytes_total`, `…_state` (idle, working, draining, stopping).
- Third-party packages of the follower: "`httpx` and `pydantic-settings`; `prometheus-client` joins in F2. No `fastapi`, no database driver" (spec 4).
- Memory guard (D22, 5.7): "Before transcribing, the follower compares an estimate of the job's memory with the memory it may use and fails the job `out_of_resources` when it cannot fit"; "The limit is `SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB`, or when unset the cgroup limit (`memory.max`), or the machine's physical memory. `needed > limit` fails the job `out_of_resources`, retryable. A duration that cannot be read skips the guard." The spec's per-hour figures are "estimates to be measured in plan F2": they were, and they are not the spec's (ruling 5).
- "A reason is the exception's class and message; it never contains transcript text, a link or a path outside scratch" (spec 6). No audio, transcript text, link, token or credential in a log, a metric or a failure reason.
- "The CUDA image is proven on the development machine, not in CI" (R5): `docker run --gpus all`, "transcribe a real recording from a real leader", and "records the result in the plan's outcomes". If pass-through does not work: say so in the README, prove the GPU path natively on Windows, and mark the image "not yet run on a GPU".
- "The same agent code runs in the CPU image, the CUDA image and installed directly on Linux and Windows" (spec 1): nothing here may be Linux-only without a fallback.
- **No test seam in production paths**, and **the whole suite is the gate**: every task ends with `uv run ruff check .` and `uv run pytest` over the whole repository.
- Python `>=3.11`; `ruff` line length 100, rules `E, F, I, UP, B`.
- Docker on the development machine: in Git Bash, first `export PATH="$PATH:/c/Users/walla/AppData/Local/Programs/DockerDesktop/resources/bin"`. `uv` there is `python -m uv`; the commands below are written `uv run ...`.
- **Never touch `C:\Users\walla\SwarmScribe-ui`, and never use ports 8900 or 8901.** The listener's tests bind port 0 (the system chooses); the image's port 9108 is on the container's own loopback and is never published.
- Start from an up-to-date `main` that has F2a merged, on a new branch `follower-f2b`.

## Measured before this plan was written

All of this plan's code ran on 2026-10-05 in a scratch folder outside the repository (Docker Desktop 29.8.1 on Windows 11 with WSL 2, RTX 4090 with driver 617.14, 28 logical CPUs, 16.6 GB for the Docker VM): the three modules and their 64 tests in a copy of the follower (553 passed after the last task), both images, the check script on four images, and the extended Compose scenario with the `cpu` image and with the `cuda` image on the GPU.

**GPU pass-through works in Docker Desktop.** `docker run --rm --gpus all nvidia/cuda:12.9.1-base-ubuntu24.04 nvidia-smi` showed the RTX 4090 (NVIDIA-SMI 615.78.02, CUDA 13.4). So ruling R5's first branch applies: the `cuda` image is proven in Docker, and the native Windows fallback is not needed.

| What | Result |
|---|---|
| `nvidia-cublas-cu12` resolved by `uv lock` | 12.9.2.10 (it brings `nvidia-cuda-nvrtc-cu12` 12.9.86) |
| What CTranslate2 4.8.2 needs on a GPU | `libcublas.so.12`, and `libcuda.so.1` from the driver. **Not cuDNN**: no `cudnn` string is in its library, and with cuDNN made unloadable large-v3 float16 loaded, warmed up and transcribed five minutes of audio |
| `swarmscribe-follower:cuda`, no model | 1.69 GB of layers (environment 1.54 GB); `docker images` shows 2.55 GB. With cuDNN it was 2.9 GB of layers |
| the same with `MODELS=large-v3` | model layer 3.09 GB; `docker images` shows 8.48 GB |
| Build of the `cuda` target, nothing cached | 80 s for the libraries; large-v3 adds 37 s |
| `doctor` on the GPU (`--gpus all --read-only --cap-drop ALL --network none`) | `device: cuda (NVIDIA GeForce RTX 4090, 24564 MiB)`, `model: large-v3 (float16) loaded and ran`, in 4.7 s |
| The `cuda` image without a GPU, device `auto` | fell back to the CPU and loaded large-v3 as int8 (3.6 GB, slow), silently. With the image's `SWARMSCRIBE_FOLLOWER_DEVICE=cuda`: `device: FAILED: cuda was requested but no CUDA GPU is available`, exit 3 |
| GPU, large-v3 float16, one hour mono | 377 s (10 times real time) |
| GPU, one hour split (two channels) | 282 s (26 channel-hours per hour) |
| CPU, distil-large-v3 int8, five minutes mono | 57 s (5 times real time) |
| CPU, tiny.en int8, one hour mono | 169 s (21 times real time) |
| Compose scenario, extended, `cpu` image | passed twice, 104 and 127 s |
| Compose scenario, extended, `cuda` image on the GPU (`run --gpu`) | passed twice, 138 and 150 s: two followers on one GPU, the long job redone in 34 to 39 s under the 8 s lease, a stop mid-job in 1.8 to 2.3 s |
| `check-follower-image.sh` on the baked `cuda` image, `CHECK_GPU=1` | passed: healthy with no leader in reach, `/metrics` served |

**Memory** (host memory of one process; `ru_maxrss` and `/proc/self/status` inside the images):

| Model | Once loaded and warmed up | Peak while loading |
|---|---|---|
| tiny.en, int8, CPU | 213 MiB | 265 MiB |
| distil-large-v3, int8, CPU | 1730 MiB | 1901 MiB |
| large-v3, int8, CPU | 3627 MiB | 3736 MiB |
| large-v3, float16, GPU | 741 MiB | 3155 MiB (the model passes through host memory on its way to the GPU) |

| Recording (the speech fixture repeated) | What the job added at its peak |
|---|---|
| 5 min mono | 346 MiB |
| 15 min mono | 976 MiB |
| 30 min mono | 1792 MiB |
| 1 h mono, tiny.en on the CPU | 3493 MiB |
| 1 h mono, large-v3 on the GPU | 3511 MiB |
| 2 h mono | 6928 MiB |
| 1 h split (each channel speaks about two thirds of the time) | 2706 MiB on the CPU, 2622 MiB on the GPU |
| 2 h split | 5127 MiB |

It is a straight line: about 3.5 GiB per hour of speech in one pass, on the CPU and on the GPU alike, because faster-whisper computes the features of the whole recording at once, on the CPU. The peak comes before the first segment; during the segments the process holds 0.5 to 1.4 GiB.

## Rulings

Decisions this plan makes. Those marked **(owner)** are the owner's to overturn; each has the plan's recommendation built in.

1. **The `cuda` extra is cuBLAS only: no cuDNN.** The spec (D15, 8.1) lists both. The locked CTranslate2 4.8.2 does not load cuDNN (measured above), and leaving it out saves 1.4 GB per image. If a later CTranslate2 needs it again, the check script's `ctypes.CDLL` line and the warm-up will say so. **(owner)**
2. **The `cuda` image sets `SWARMSCRIBE_FOLLOWER_DEVICE=cuda`.** With `auto`, a `cuda` image on a node without a GPU (a missing `--gpus all`, a pod on the wrong node) quietly runs large-v3 on the CPU. With `cuda` it exits 3 and says why, before it registers. **(owner)**
3. **`uv.lock` pins the GPU library**: the manifest says `nvidia-cublas-cu12>=12,<13` (the library's name is `libcublas.so.12`), the lock file says 12.9.2.10 with its hashes, and the image is built `--frozen`. Linux and Windows only (a marker): there is no wheel for macOS.
4. **`/healthz` allows the lease keeper three heartbeat intervals plus the time one request may take (30 s).** The keeper stamps its loop before it asks the leader, and a leader that does not answer holds that request for up to the client's timeout. With the spec's three intervals alone and a short interval, a leader outage would turn `/healthz` to 503 and have Kubernetes kill a pod that is transcribing, which the same paragraph forbids. With the default 30 s interval the difference is 90 s against 120 s. **(owner)**
5. **The memory guard uses the measured figures, which are five times the spec's.** Per hour of recording: 3600 MiB (mono, or `auto` on anything but a stereo file) and 3900 MiB (split: the same pass, with the other channel held), plus 100 MiB. The spec's 600 and 1200 would let through recordings that are then killed. The guard assumes a recording that is all speech, as it cannot know better before the engine reads it; a split recording whose channels alternate needs about a third less (measured 2.7 GiB per hour), so the guard is strict for those. **(owner)** The real fix is in the engine (features computed in pieces); see "For the owner" at the end.
6. **"The host memory of the loaded model" is read, not looked up.** The guard runs after the model is loaded and adds the recording's estimate to what the process holds at that moment (`/proc/self/statm`). No table of models can cover the model a profile may name tomorrow. Where the platform does not say (Windows, macOS), the job is counted alone; the README says so.
7. **The limit is the smaller of the container's limit and the machine's memory**, not "the cgroup limit, or the machine's": a container limit above the machine's memory (Docker's default is none) is no limit.
8. **A recording the guard refuses is failed, never released**, and the follower stays: `out_of_resources`, retryable, so the leader tries it `max_attempts` times and then parks it with the reason. On a pool of equal machines that is three quick refusals; on a mixed pool a bigger follower may take it.
9. **The metrics registry is the follower's own object**, not `prometheus_client`'s global one: no process metrics a test cannot predict, and two agents in one process do not share counts. Label values are only the job outcomes and the four states.
10. **The listener is the standard library's `http.server`** on a daemon thread: two fixed paths, GET and HEAD, no body read, no access log. `prometheus-client` is used for its registry and text format only, not for its own server (which has no `/healthz`).
11. **The image's `HEALTHCHECK` is `python -c` against the address in `SWARMSCRIBE_FOLLOWER_HEALTH_ADDR`**; a follower started with that variable set to nothing is not probed (exit 0), so turning the listener off does not turn the container "unhealthy".
12. **CI builds the `cuda` target without a model and checks it** (job `follower-cuda-image`: the library loads by name; without a GPU the image exits 3). It never runs on a GPU. That costs about three minutes and a 600 MB download per run, and catches a `cuda` image that no longer builds. **(owner)**
13. **The GPU run is the same Compose scenario**, `run --gpu`, with `FOLLOWER_IMAGE` naming a `cuda` image that has large-v3 baked in and `docker-compose.gpu.yml` giving the followers the GPU. Two followers share the one GPU there; that is the test's doing, not a recommendation (D4 stands: one process per GPU).
14. **`doctor` gains a `memory:` line** (spec 8.3 lists host memory among what it prints; F1's minor M6).

## Review Focus

Inputs and conditions the spec implies and that are most likely to bite a person running this, each pinned by a test in the task that owns the code:

1. **A leader that does not answer while a follower transcribes**: `/healthz` stays 200 (a slow leader is not a dead keeper), and Docker reports a follower with no leader in reach `healthy` — Task 2, `test_a_lease_keeper_that_stopped_going_round_is_unhealthy`; Task 4, the check script.
2. **A recording longer than the pod's memory allows**: refused before the engine reads it, with a reason that says the length and the limit, three times, then parked; a recording that fits is not held back — Task 3, `test_a_job_too_long_for_this_follower_fails_out_of_resources_before_the_engine_runs`; Task 4, the scenario's step 5.
3. **A container with no memory limit, a limit above the machine's memory, cgroup v1, or no cgroup at all (Windows)**: the limit is still a sensible number or there is no guard, never a crash — Task 3, `test_the_container_limit_is_read_from_the_cgroup_file`, `test_the_smaller_of_the_container_and_the_machine_is_the_limit`, `test_this_machine_says_how_much_memory_it_has` (runs on Windows too).
4. **The health port already taken, or the address mistyped**: exit 2 naming the setting, not a traceback and not a follower without a listener — Task 2, `test_a_port_that_is_taken_is_a_configuration_error_naming_the_setting`, `test_a_bad_listener_address_is_refused`.
5. **A `cuda` image started without a GPU**: exit 3 naming the device, before registering — Task 5, the check script (in CI).
6. **A probe or a scraper that connects and says nothing, or asks for another path**: it holds no thread for long, gets 404, and never a page about a job — Task 2, `test_a_client_that_sends_nothing_does_not_stop_the_next_one`, `test_nothing_else_is_served`.

## File Structure

| File | Responsibility |
|---|---|
| `packages/follower/pyproject.toml`, `uv.lock` (modify) | `prometheus-client`; the `cuda` extra |
| `packages/follower/src/swarmscribe_follower/metrics.py` (new) | what the follower counts; the text format |
| `packages/follower/src/swarmscribe_follower/health.py` (new) | the listener for `/healthz` and `/metrics` |
| `packages/follower/src/swarmscribe_follower/memory.py` (new) | the limit, a recording's length, the guard |
| `.../config.py` (modify) | `health_addr`, `memory_limit_mb` |
| `.../models.py`, `.../lease.py` (modify) | tell a callback about a load, a failed heartbeat |
| `.../leader.py` (modify) | `REQUEST_TIMEOUT_SECONDS` |
| `.../job.py` (modify) | count a job's bytes and seconds; `keeper_stalled`; run the guard |
| `.../agent.py` (modify) | `metrics`, `state`, `progress`, `tick`, `health`; pass the guard on |
| `.../main.py` (modify) | build the metrics and the guard; start the listener in `run`; `doctor`'s memory line |
| `packages/follower/tests/test_metrics.py`, `test_health.py`, `test_memory.py` (new) | their tests |
| `docker/follower.Dockerfile` (modify) | the listener's address, `HEALTHCHECK`; stages `build-cuda` and `cuda` |
| `docker/check-follower-image.sh` (modify) | the listener, the probe, `/metrics` |
| `e2e/follower-compose/docker-compose.yml`, `run_e2e.py` (modify) | health, metrics, the guard; `run --gpu` |
| `e2e/follower-compose/docker-compose.gpu.yml` (new) | the GPU for the followers |
| `packages/follower/tests/test_compose_driver.py` (modify) | the Compose limit against the guard's figures |
| `.github/workflows/ci.yml` (modify) | job `follower-cuda-image` |
| `docs/superpowers/plans/2026-10-05-follower-f2-outcomes.md` (new) | the GPU run, recorded (R5) |
| `README.md` (modify) | "Follower images"; two settings; the Compose test |
| `docs/superpowers/specs/2026-10-04-follower-design.md` (modify) | amendments after F2 |

---

### Task 1: Metrics

**Files:**
- Modify: `packages/follower/pyproject.toml`, `uv.lock`
- Create: `packages/follower/src/swarmscribe_follower/metrics.py`
- Modify: `models.py`, `lease.py`, `job.py`, `agent.py`, `main.py` in `packages/follower/src/swarmscribe_follower/`
- Test: `packages/follower/tests/test_metrics.py`

**Interfaces:**
- Consumes: `ModelHost(device, *, factory, allowed)`, `LeaseKeeper(client, job_id, lease_id, interval, control, *, lease_seconds, clock)`, `JobRunner(client, links, models, scratch, *, device, heartbeat_interval, clock, sleep)`, `Agent(settings, *, client, links, models, scratch, store, probe, ...)` (all exist); the test kit's `make_agent`, `FakeLeader`, `FakeEngine`.
- Produces: `swarmscribe_follower.metrics`: `Metrics()` with `registry`, `watch(*, state, progress)`, `job_ended(outcome)`, `transcribed(audio_seconds, seconds)`, `heartbeat_failed()`, `downloaded(size)`, `uploaded(size)`, `model_loaded(seconds)`, `render() -> bytes`; `CONTENT_TYPE`, `STATES = ("idle", "working", "draining", "stopping")`, `PREFIX`. New optional keyword arguments: `ModelHost(..., on_loaded: Callable[[float], None] | None)`, `LeaseKeeper(..., on_failure: Callable[[], None] | None)`, `JobRunner(..., metrics: Metrics | None)`, `Agent(..., metrics: Metrics | None)`. `Agent.metrics` (always set), `Agent.state() -> str`, `Agent.progress() -> float | None`. Task 2 serves `agent.metrics.render`; Task 4's check script and driver read the samples by name.

- [ ] **Step 1: Add the dependency**

In `packages/follower/pyproject.toml`, replace

```toml
    "pydantic-settings>=2.4,<3",
]
```

with

```toml
    "pydantic-settings>=2.4,<3",
    "prometheus-client>=0.20,<1",
]
```

Run: `uv lock && uv sync`
Expected: `Added prometheus-client v0.26.0` (or a later 0.x) and nothing else added or removed.

- [ ] **Step 2: Write the failing tests**

Create `packages/follower/tests/test_metrics.py`:

```python
import pytest
from follower_testkit import JOIN_TOKEN, SPOKEN, FakeEngine, FakeLeader, make_agent
from prometheus_client.parser import text_string_to_metric_families
from swarmscribe_follower.leader import Transient
from swarmscribe_follower.lease import JobControl, LeaseKeeper
from swarmscribe_follower.metrics import STATES, Metrics
from swarmscribe_follower.models import ModelHost

P = "swarmscribe_follower_"


def samples(metrics: Metrics) -> dict[tuple[str, tuple], float]:
    """Every sample as {(name, sorted labels): value}."""
    found = {}
    for family in text_string_to_metric_families(metrics.render().decode()):
        for sample in family.samples:
            found[(sample.name, tuple(sorted(sample.labels.items())))] = sample.value
    return found


def value(metrics: Metrics, name: str, **labels: str) -> float:
    return samples(metrics)[(P + name, tuple(sorted(labels.items())))]


def test_a_new_follower_counts_nothing_and_is_idle():
    metrics = Metrics()
    for name in ("audio_seconds_total", "transcribe_seconds_total", "heartbeat_failures_total",
                 "download_bytes_total", "upload_bytes_total", "model_load_seconds",
                 "job_progress"):
        assert value(metrics, name) == 0.0, name
    assert [value(metrics, "state", state=state) for state in STATES] == [1.0, 0.0, 0.0, 0.0]


def test_two_followers_do_not_share_counts():
    first, second = Metrics(), Metrics()
    first.job_ended("completed")
    assert value(first, "jobs_total", outcome="completed") == 1.0
    assert (P + "jobs_total", (("outcome", "completed"),)) not in samples(second)


def test_the_state_and_the_progress_are_read_when_asked_for():
    metrics, now = Metrics(), {"state": "working", "progress": 0.25}
    metrics.watch(state=lambda: now["state"], progress=lambda: now["progress"])
    assert value(metrics, "state", state="working") == 1.0
    assert value(metrics, "job_progress") == 0.25
    now.update(state="stopping", progress=None)
    assert value(metrics, "state", state="working") == 0.0
    assert value(metrics, "state", state="stopping") == 1.0
    assert value(metrics, "job_progress") == 0.0


def test_a_completed_job_is_counted_with_its_audio_its_time_and_its_bytes(tmp_path):
    leader, engine = FakeLeader(links_min_interval=0), FakeEngine()
    source = b"a recording of some length"
    job_id = leader.add_job(source)
    engine.on_step = lambda fraction: setattr(leader, "state", "draining")
    agent = make_agent(tmp_path, leader, engine)
    assert agent.run_supervised(poll=0.01) == 0
    metrics = agent.metrics
    assert value(metrics, "jobs_total", outcome="completed") == 1.0
    assert value(metrics, "audio_seconds_total") == 3.25  # the fake transcript's duration
    assert value(metrics, "transcribe_seconds_total") >= 0.0
    assert value(metrics, "download_bytes_total") == len(source)
    uploaded = sum(len(body) for body in leader.jobs[job_id]["uploads"].values())
    assert value(metrics, "upload_bytes_total") == uploaded > 0
    assert value(metrics, "state", state="stopping") == 1.0  # it has exited


def test_a_failed_job_is_counted_by_its_outcome(tmp_path):
    leader, engine = FakeLeader(links_min_interval=0), FakeEngine()
    leader.add_job()
    engine.error = RuntimeError("the engine broke")
    engine.on_step = lambda fraction: setattr(leader, "state", "draining")
    agent = make_agent(tmp_path, leader, engine)
    assert agent.run_supervised(poll=0.01) == 0
    assert value(agent.metrics, "jobs_total", outcome="failed") == 1.0
    assert value(agent.metrics, "audio_seconds_total") == 0.0


def test_a_model_load_is_timed():
    seen = []
    host = ModelHost("cpu", factory=FakeEngine(), on_loaded=seen.append)
    host.get("tiny.en", "int8")
    host.get("tiny.en", "int8")  # already loaded: not a load
    assert len(seen) == 1 and seen[0] >= 0.0
    metrics = Metrics()
    metrics.model_loaded(4.5)
    assert value(metrics, "model_load_seconds") == 4.5


def test_a_model_that_fails_to_load_is_not_timed():
    engine, seen = FakeEngine(), []
    engine.load_error = RuntimeError("no such library")
    host = ModelHost("cpu", factory=engine, on_loaded=seen.append)
    with pytest.raises(Exception, match="no such library"):
        host.get("tiny.en", "int8")
    assert seen == []


def test_every_heartbeat_the_leader_does_not_answer_is_counted():
    class Silent:
        def __init__(self):
            self.calls = 0

        def heartbeat(self, job_id, lease_id, progress):
            self.calls += 1
            if self.calls <= 2:
                raise Transient(None, None, "nobody answered")
            control.stop("cancelled")  # the third is answered: end the test
            return "continue"

    metrics, control, client = Metrics(), JobControl(), Silent()
    keeper = LeaseKeeper(
        client, "job-1", "lease-1", 0.01, control, on_failure=metrics.heartbeat_failed
    )
    keeper.start()
    keeper.join(timeout=30)
    keeper.finish()
    assert not keeper.is_alive()
    assert value(metrics, "heartbeat_failures_total") == 2.0


def test_the_metrics_hold_no_secret_and_no_transcript(tmp_path):
    leader, engine = FakeLeader(links_min_interval=0), FakeEngine()
    leader.add_job()
    engine.on_step = lambda fraction: setattr(leader, "state", "draining")
    agent = make_agent(tmp_path, leader, engine)
    assert agent.run_supervised(poll=0.01) == 0
    text = agent.metrics.render().decode()
    for secret in (JOIN_TOKEN, "credential-SECRET", SPOKEN, "leader.test", "/v1/"):
        assert secret not in text
    labels = {key for (_name, pairs) in samples(agent.metrics) for key, _ in pairs}
    assert labels <= {"outcome", "state"}
```

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest packages/follower/tests/test_metrics.py -v`
Expected: an error at import: `ModuleNotFoundError: No module named 'swarmscribe_follower.metrics'`.

- [ ] **Step 4: Write the module**

Create `packages/follower/src/swarmscribe_follower/metrics.py`:

```python
"""What the follower counts (follower spec 9), in the Prometheus text format.

The registry is this object's own, never prometheus_client's global one: two followers in
one process (the tests) do not share counts, and nothing is counted by importing.

No label and no value ever comes from a recording, a transcript, a link or a token: the only
label values are the job outcomes and the four states."""

from collections.abc import Callable

from prometheus_client import CollectorRegistry, Counter, Gauge, generate_latest

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
STATES = ("idle", "working", "draining", "stopping")
PREFIX = "swarmscribe_follower_"


class Metrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        # prometheus_client appends `_total` to a counter's name.
        self._jobs = self._counter("jobs", "Jobs this follower ended, by outcome", ["outcome"])
        self._audio = self._counter("audio_seconds", "Seconds of audio transcribed")
        self._spent = self._counter("transcribe_seconds", "Seconds spent transcribing")
        self._failed = self._counter("heartbeat_failures", "Heartbeats the leader did not answer")
        self._down = self._counter("download_bytes", "Bytes of recordings downloaded")
        self._up = self._counter("upload_bytes", "Bytes of outputs uploaded")
        self._load = self._gauge("model_load_seconds", "Seconds the last model load took")
        self._progress = self._gauge("job_progress", "Fraction of the current job done (0 to 1)")
        self._state = self._gauge("state", "1 for the state the follower is in", ["state"])
        self.watch(state=lambda: "idle", progress=lambda: None)

    def _counter(self, name: str, text: str, labels: list[str] | None = None) -> Counter:
        return Counter(PREFIX + name, text, labels or [], registry=self.registry)

    def _gauge(self, name: str, text: str, labels: list[str] | None = None) -> Gauge:
        return Gauge(PREFIX + name, text, labels or [], registry=self.registry)

    def watch(self, *, state: Callable[[], str], progress: Callable[[], float | None]) -> None:
        """Read the state and the progress from the agent whenever the metrics are asked for,
        so they can never be stale."""
        for name in STATES:
            self._state.labels(name).set_function(lambda name=name: float(state() == name))
        self._progress.set_function(lambda: float(progress() or 0.0))

    def job_ended(self, outcome: str) -> None:
        self._jobs.labels(outcome).inc()

    def transcribed(self, audio_seconds: float, seconds: float) -> None:
        self._audio.inc(max(0.0, audio_seconds))
        self._spent.inc(max(0.0, seconds))

    def heartbeat_failed(self) -> None:
        self._failed.inc()

    def downloaded(self, size: int) -> None:
        self._down.inc(max(0, size))

    def uploaded(self, size: int) -> None:
        self._up.inc(max(0, size))

    def model_loaded(self, seconds: float) -> None:
        self._load.set(max(0.0, seconds))

    def render(self) -> bytes:
        return generate_latest(self.registry)
```

- [ ] **Step 5: Count things where they happen**

In `packages/follower/src/swarmscribe_follower/models.py`, 4 changes.

**1.** Replace

```python
import logging
import re
from collections.abc import Callable
```

with

```python
import logging
import re
import time
from collections.abc import Callable
```

**2.** Replace

```python
        factory: EngineFactory = Transcriber,
        allowed: frozenset[str] = frozenset(),
    ) -> None:
        self.device = device
        self._factory = factory
        self._allowed = allowed
```

with

```python
        factory: EngineFactory = Transcriber,
        allowed: frozenset[str] = frozenset(),
        on_loaded: Callable[[float], None] | None = None,
    ) -> None:
        self.device = device
        self._factory = factory
        self._allowed = allowed
        self._on_loaded = on_loaded  # told how many seconds a load and its warm-up took
```

**3.** Replace

```python
        transcriber = None
        try:
            transcriber = self._factory(settings)
```

with

```python
        transcriber = None
        started = time.monotonic()
        try:
            transcriber = self._factory(settings)
```

**4.** Replace

```python
        self._transcriber, self._loaded = transcriber, (model, compute_type)
        logger.info("model %s (%s) loaded on %s", model, compute_type, self.device)
```

with

```python
        self._transcriber, self._loaded = transcriber, (model, compute_type)
        if self._on_loaded is not None:
            self._on_loaded(time.monotonic() - started)
        logger.info("model %s (%s) loaded on %s", model, compute_type, self.device)
```

In `packages/follower/src/swarmscribe_follower/lease.py`, 2 changes.

**1.** Replace

```python
        lease_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(name="lease-keeper", daemon=True)
        self._client, self._job_id, self._lease_id = client, job_id, lease_id
        self._interval, self._control, self._clock = interval, control, clock
```

with

```python
        lease_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        on_failure: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(name="lease-keeper", daemon=True)
        self._client, self._job_id, self._lease_id = client, job_id, lease_id
        self._interval, self._control, self._clock = interval, control, clock
        self._on_failure = on_failure  # told of every heartbeat the leader did not answer
```

**2.** Replace

```python
            except Transient:
                self.failures += 1
                if self.failures == 1:
```

with

```python
            except Transient:
                self.failures += 1
                if self._on_failure is not None:
                    self._on_failure()
                if self.failures == 1:
```

In `packages/follower/src/swarmscribe_follower/job.py`, 6 changes.

**1.** Replace

```python
from .models import ModelHost, ModelUnavailable, OutOfMemory, is_out_of_memory
```

with

```python
from .metrics import Metrics
from .models import ModelHost, ModelUnavailable, OutOfMemory, is_out_of_memory
```

**2.** Replace

```python
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client, self._links, self._models, self._scratch = client, links, models, scratch
        self._device, self._interval = device, heartbeat_interval
        self._clock, self._sleep = clock, sleep
```

with

```python
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        metrics: Metrics | None = None,
    ) -> None:
        self._client, self._links, self._models, self._scratch = client, links, models, scratch
        self._device, self._interval = device, heartbeat_interval
        self._clock, self._sleep = clock, sleep
        self._metrics = metrics or Metrics()
```

**3.** Replace

```python
        keeper = LeaseKeeper(self._client, claim.job_id, claim.lease_id, self._interval, control)
```

with

```python
        keeper = LeaseKeeper(
            self._client,
            claim.job_id,
            claim.lease_id,
            self._interval,
            control,
            on_failure=self._metrics.heartbeat_failed,
        )
```

**4.** Replace

```python
            raise _Release("the recording could not be downloaded for ten minutes") from None
```

with

```python
            raise _Release("the recording could not be downloaded for ten minutes") from None
        self._metrics.downloaded(source.stat().st_size)
```

**5.** Replace

```python
        if transcript.source_checksum != downloaded:
```

with

```python
        self._metrics.transcribed(transcript.duration, self._clock() - self._transcribing_since)
        if transcript.source_checksum != downloaded:
```

**6.** Replace

```python
                    pause=control.pause,
                )
            return sent
```

with

```python
                    pause=control.pause,
                )
                self._metrics.uploaded(path.stat().st_size)
            return sent
```

In `packages/follower/src/swarmscribe_follower/agent.py`, 5 changes.

**1.** Replace

```python
from .lease import SHUTDOWN, JobControl
from .models import ModelHost, ModelUnavailable, OutOfMemory
```

with

```python
from .lease import SHUTDOWN, JobControl
from .metrics import Metrics
from .models import ModelHost, ModelUnavailable, OutOfMemory
```

**2.** Replace

```python
        sleep: Callable[[float], None] = time.sleep,
        hold_lock: Callable[..., BinaryIO] = hold_state_lock,
    ) -> None:
        self._settings, self._client, self._links = settings, client, links
```

with

```python
        sleep: Callable[[float], None] = time.sleep,
        hold_lock: Callable[..., BinaryIO] = hold_state_lock,
        metrics: Metrics | None = None,
    ) -> None:
        self.metrics = metrics or Metrics()
        self.metrics.watch(state=self.state, progress=self.progress)
        self._settings, self._client, self._links = settings, client, links
```

**3.** Replace

```python
    # --- before the loop -----------------------------------------------------------------

    def startup_model(self) -> tuple[str, str]:
```

with

```python
    # --- what the listener is told -------------------------------------------------------

    def state(self) -> str:
        """One of metrics.STATES."""
        if self._stopping.is_set():
            return "stopping"
        control = self._control
        if self.drained or (control is not None and control.draining):
            return "draining"
        return "working" if control is not None else "idle"

    def progress(self) -> float | None:
        control = self._control
        return control.progress if control is not None else None

    # --- before the loop -----------------------------------------------------------------

    def startup_model(self) -> tuple[str, str]:
```

**4.** Replace

```python
            heartbeat_interval=interval,
            clock=self._clock,
            sleep=self._sleep,
        )

    def register(self) -> None:
```

with

```python
            heartbeat_interval=interval,
            clock=self._clock,
            sleep=self._sleep,
            metrics=self.metrics,
        )

    def register(self) -> None:
```

**5.** Replace

```python
            result = self._run(answer)
            if result.outcome != SCRATCH_BROKEN:
```

with

```python
            result = self._run(answer)
            self.metrics.job_ended(result.outcome)
            if result.outcome != SCRATCH_BROKEN:
```

In `packages/follower/src/swarmscribe_follower/main.py`, 2 changes.

**1.** Replace

```python
from .leader import LeaderClient, Refused, Transient
from .models import ModelHost, ModelUnavailable, OutOfMemory
```

with

```python
from .leader import LeaderClient, Refused, Transient
from .metrics import Metrics
from .models import ModelHost, ModelUnavailable, OutOfMemory
```

**2.** Replace

```python
    verify = tls(settings)
    return Agent(
        settings,
        client=LeaderClient(settings.leader_url, verify=verify),
        links=Links(verify=verify, allow_http=settings.allow_http),
        models=ModelHost(found.choice.device, allowed=frozenset(settings.allowed_models)),
        scratch=Scratch(settings.scratch, settings.state_dir),
        store=CredentialStore(settings.credential_file),
        probe=found,
    )
```

with

```python
    verify = tls(settings)
    metrics = Metrics()
    return Agent(
        settings,
        client=LeaderClient(settings.leader_url, verify=verify),
        links=Links(verify=verify, allow_http=settings.allow_http),
        models=ModelHost(
            found.choice.device,
            allowed=frozenset(settings.allowed_models),
            on_loaded=metrics.model_loaded,
        ),
        scratch=Scratch(settings.scratch, settings.state_dir),
        store=CredentialStore(settings.credential_file),
        probe=found,
        metrics=metrics,
    )
```

- [ ] **Step 6: Run the tests, then the whole suite**

Run: `uv run pytest packages/follower/tests/test_metrics.py -v`
Expected: 9 passed.

Run: `uv run ruff check . && uv run pytest`
Expected: `All checks passed!` and no failure.

- [ ] **Step 7: Commit**

```bash
git add packages/follower/pyproject.toml uv.lock packages/follower/src/swarmscribe_follower packages/follower/tests/test_metrics.py
git commit -m "feat(follower): count jobs, audio, bytes, heartbeat failures and the state"
```

---

### Task 2: The health listener

**Files:**
- Create: `packages/follower/src/swarmscribe_follower/health.py`
- Modify: `config.py`, `leader.py`, `job.py`, `agent.py`, `main.py` in `packages/follower/src/swarmscribe_follower/`
- Test: `packages/follower/tests/test_health.py`

**Interfaces:**
- Consumes: `Agent.metrics.render` and `metrics.CONTENT_TYPE` (Task 1); `LeaseKeeper.last_loop` (exists: when the keeper's loop last went round, on `time.monotonic`); `FollowerExit`, `EXIT_CONFIGURATION`; `Agent.run_supervised(*, poll)`.
- Produces: the setting `SWARMSCRIBE_FOLLOWER_HEALTH_ADDR` as `Settings.health_addr: str | None` (`host:port`, blank is unset) and `Settings.health_address -> tuple[str, int] | None`. `swarmscribe_follower.health.HealthServer(address, *, healthy, metrics)` with `start()` (raises `FollowerExit(2, ...)` when it cannot listen), `port`, `close()`. `GET` or `HEAD /healthz` answers `200 ok` or `503 <what stopped>`; `/metrics` answers 200 in the Prometheus text format; anything else 404. `Agent.tick()`, `Agent.health() -> tuple[bool, str]`; `JobRunner.keeper_stalled() -> bool`; `job.KEEPER_INTERVALS = 3`; `agent.SUPERVISOR_SILENCE_SECONDS = 30.0`; `leader.REQUEST_TIMEOUT_SECONDS = 30.0`. `command_run` starts the listener before the agent is prepared and closes it when `run_supervised` returns. Task 4 sets the variable in the image and probes it.

- [ ] **Step 1: Write the failing tests**

Create `packages/follower/tests/test_health.py`:

```python
import http.client
import socket
import threading
import time

import pytest
from follower_testkit import FakeEngine, FakeLeader, make_agent, make_runner
from swarmscribe_follower import main as cli
from swarmscribe_follower.agent import SUPERVISOR_SILENCE_SECONDS
from swarmscribe_follower.config import Settings
from swarmscribe_follower.errors import FollowerExit
from swarmscribe_follower.health import HealthServer
from swarmscribe_follower.job import KEEPER_INTERVALS
from swarmscribe_follower.leader import REQUEST_TIMEOUT_SECONDS
from swarmscribe_follower.metrics import CONTENT_TYPE


@pytest.fixture
def served():
    """A listener on a free loopback port, with a health answer the test can change."""
    answer = {"alive": True, "text": "ok"}
    server = HealthServer(
        ("127.0.0.1", 0),
        healthy=lambda: (answer["alive"], answer["text"]),
        metrics=lambda: b"swarmscribe_follower_jobs_total 0.0\n",
    )
    server.start()
    yield server.port, answer
    server.close()


def drain_at_the_first_claim(leader):
    """The follower registers, is told `drain` by its first claim and exits 0. (A drain is
    set on a registered follower: registering makes it active.)"""
    leader.on["claim"] = lambda: setattr(leader, "state", "draining")


def ask(port, method, path):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request(method, path)
        response = connection.getresponse()
        return response.status, response.read(), {k.lower(): v for k, v in response.getheaders()}
    finally:
        connection.close()


def test_healthz_says_ok_while_the_follower_is_alive(served):
    port, _answer = served
    status, body, headers = ask(port, "GET", "/healthz")
    assert (status, body) == (200, b"ok\n")
    assert headers["cache-control"] == "no-store"
    assert "python" not in headers.get("server", "").lower()


def test_healthz_is_503_and_says_what_stopped(served):
    port, answer = served
    answer.update(alive=False, text="the lease keeper has stopped")
    status, body, _headers = ask(port, "GET", "/healthz?probe=1")
    assert (status, body) == (503, b"the lease keeper has stopped\n")


def test_head_answers_like_get_without_a_body(served):
    port, _answer = served
    status, body, headers = ask(port, "HEAD", "/healthz")
    assert (status, body, headers["content-length"]) == (200, b"", "3")


def test_metrics_are_served_in_the_prometheus_text_format(served):
    port, _answer = served
    status, body, headers = ask(port, "GET", "/metrics")
    assert (status, headers["content-type"]) == (200, CONTENT_TYPE)
    assert body == b"swarmscribe_follower_jobs_total 0.0\n"


@pytest.mark.parametrize("path", ["/", "/healthz/", "/metrics/x", "/v1/jobs/claim", "/../etc"])
def test_nothing_else_is_served(served, path):
    port, _answer = served
    assert ask(port, "GET", path)[0] == 404


def test_a_request_that_is_not_a_read_is_refused(served):
    port, _answer = served
    assert ask(port, "POST", "/healthz")[0] == 501
    assert ask(port, "GET", "/healthz")[0] == 200  # and the listener is still there


def test_a_client_that_sends_nothing_does_not_stop_the_next_one(served):
    port, _answer = served
    silent = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        assert ask(port, "GET", "/healthz")[0] == 200
    finally:
        silent.close()


def test_a_port_that_is_taken_is_a_configuration_error_naming_the_setting():
    taken = socket.socket()
    taken.bind(("127.0.0.1", 0))
    taken.listen(1)
    try:
        server = HealthServer(
            ("127.0.0.1", taken.getsockname()[1]), healthy=lambda: (True, "ok"), metrics=bytes
        )
        with pytest.raises(FollowerExit) as stop:
            server.start()
    finally:
        taken.close()
    assert stop.value.code == 2 and "SWARMSCRIBE_FOLLOWER_HEALTH_ADDR" in stop.value.reason
    server.close()  # closing a listener that never started is harmless


def test_closing_leaves_no_thread_and_frees_the_port():
    server = HealthServer(("127.0.0.1", 0), healthy=lambda: (True, "ok"), metrics=bytes)
    server.start()
    port = server.port
    server.close()
    assert not any(thread.name == "health" for thread in threading.enumerate())
    with pytest.raises(OSError):
        ask(port, "GET", "/healthz")


# --- what the agent answers ------------------------------------------------------------


def test_an_agent_is_healthy_until_its_supervising_thread_goes_silent(tmp_path):
    agent = make_agent(tmp_path, FakeLeader(), FakeEngine())
    assert agent.health() == (True, "ok")
    agent._ticked = time.monotonic() - SUPERVISOR_SILENCE_SECONDS - 1
    assert agent.health() == (False, "the supervising thread has stopped")
    agent.tick()
    assert agent.health() == (True, "ok")


def test_run_supervised_ticks_while_it_supervises(tmp_path):
    leader = FakeLeader()
    drain_at_the_first_claim(leader)
    agent = make_agent(tmp_path, leader, FakeEngine())
    agent._ticked = 0.0
    assert agent.run_supervised(poll=0.01) == 0
    assert agent.health() == (True, "ok")


class StalledKeeper:
    def __init__(self, last_loop, alive=True):
        self.last_loop, self._alive = last_loop, alive

    def is_alive(self):
        return self._alive


def test_a_lease_keeper_that_stopped_going_round_is_unhealthy(tmp_path):
    runner, _client = make_runner(tmp_path, FakeLeader(), FakeEngine(), heartbeat_interval=2.0)
    assert runner.keeper_stalled() is False  # no job, no keeper
    allowed = KEEPER_INTERVALS * 2.0 + REQUEST_TIMEOUT_SECONDS
    runner._keeper = StalledKeeper(time.monotonic() - allowed + 5)
    assert runner.keeper_stalled() is False  # a slow leader is not a dead keeper
    runner._keeper = StalledKeeper(time.monotonic() - allowed - 1)
    assert runner.keeper_stalled() is True
    runner._keeper = StalledKeeper(time.monotonic() - allowed - 1, alive=False)
    assert runner.keeper_stalled() is False  # it ended itself and stopped the job


def test_the_agent_reports_a_stalled_keeper(tmp_path):
    leader = FakeLeader()
    agent = make_agent(tmp_path, leader, FakeEngine())
    agent.prepare()
    try:
        agent._runner._keeper = StalledKeeper(time.monotonic() - 3600)
        assert agent.health() == (False, "the lease keeper has stopped")
    finally:
        agent._runner._keeper = None
        agent.close()


# --- the setting and the command line ---------------------------------------------------


@pytest.mark.parametrize(
    ("given", "address"),
    [
        ("127.0.0.1:9108", ("127.0.0.1", 9108)),
        ("0.0.0.0:9108", ("0.0.0.0", 9108)),
        ("[::1]:9108", ("::1", 9108)),
        (" localhost:09108 ", ("localhost", 9108)),
    ],
)
def test_the_listener_address_is_a_host_and_a_port(given, address):
    assert Settings(leader_url="https://l.example.org", health_addr=given).health_address == address


@pytest.mark.parametrize("given", ["9108", ":9108", "localhost", "localhost:0", "h:70000", "h:x"])
def test_a_bad_listener_address_is_refused(given):
    with pytest.raises(ValueError, match="host:port"):
        Settings(leader_url="https://l.example.org", health_addr=given)


def test_no_listener_unless_asked_for_and_blank_means_unset(monkeypatch):
    monkeypatch.delenv("SWARMSCRIBE_FOLLOWER_HEALTH_ADDR", raising=False)
    assert Settings(leader_url="https://l.example.org").health_address is None
    monkeypatch.setenv("SWARMSCRIBE_FOLLOWER_HEALTH_ADDR", "")
    assert Settings(leader_url="https://l.example.org").health_address is None


def test_run_listens_before_the_model_is_loaded_and_stops_listening_at_the_end(
    tmp_path, monkeypatch
):
    leader, engine, events = FakeLeader(), FakeEngine(), []
    drain_at_the_first_claim(leader)

    class Listener:
        def __init__(self, address, *, healthy, metrics):
            events.append(("made", address, healthy()[0], metrics().startswith(b"# HELP")))

        def start(self):
            events.append(("start", len(engine.loads)))

        def close(self):
            events.append(("close", len(engine.loads)))

    monkeypatch.setattr(cli, "HealthServer", Listener)
    for name, value in (
        ("SWARMSCRIBE_LEADER_URL", "https://leader.test"),
        ("SWARMSCRIBE_FOLLOWER_STATE_DIR", str(tmp_path / "state")),
        ("SWARMSCRIBE_FOLLOWER_HEALTH_ADDR", "127.0.0.1:9108"),
    ):
        monkeypatch.setenv(name, value)
    code = cli.command_run(Settings(), lambda settings: make_agent(tmp_path, leader, engine))
    assert code == 0
    assert events == [("made", ("127.0.0.1", 9108), True, True), ("start", 0), ("close", 1)]


def test_run_opens_no_port_without_the_setting(tmp_path, monkeypatch):
    leader = FakeLeader()
    drain_at_the_first_claim(leader)

    def refuse(*args, **kwargs):
        raise AssertionError("a listener was made without SWARMSCRIBE_FOLLOWER_HEALTH_ADDR")

    monkeypatch.setattr(cli, "HealthServer", refuse)
    monkeypatch.delenv("SWARMSCRIBE_FOLLOWER_HEALTH_ADDR", raising=False)
    monkeypatch.setenv("SWARMSCRIBE_LEADER_URL", "https://leader.test")
    monkeypatch.setenv("SWARMSCRIBE_FOLLOWER_STATE_DIR", str(tmp_path / "state"))
    settings = Settings()
    assert cli.command_run(settings, lambda s: make_agent(tmp_path, leader, FakeEngine())) == 0
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/follower/tests/test_health.py -v`
Expected: an error at import: `ImportError: cannot import name 'SUPERVISOR_SILENCE_SECONDS' from 'swarmscribe_follower.agent'`.

- [ ] **Step 3: Write the listener**

Create `packages/follower/src/swarmscribe_follower/health.py`:

```python
"""An optional HTTP listener for /healthz and /metrics (follower spec D18, section 9).

Off unless SWARMSCRIBE_FOLLOWER_HEALTH_ADDR is set: a follower opens no port by default. The
image sets it to loopback, for its own HEALTHCHECK; a chart sets the pod's address, for the
kubelet and Prometheus. It serves two paths, reads no request body, keeps no access log and
says nothing about the leader, a job or a recording.

/healthz says that the follower's threads are alive, not that the leader answers: a leader
outage must never make a supervisor kill a follower that is transcribing."""

import logging
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .errors import EXIT_CONFIGURATION, FollowerExit
from .metrics import CONTENT_TYPE

logger = logging.getLogger(__name__)

Healthy = Callable[[], tuple[bool, str]]
"""(alive, one plain line saying so or what has stopped)."""
PLAIN = "text/plain; charset=utf-8"


class _Handler(BaseHTTPRequestHandler):
    server_version = "swarmscribe-follower"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = 10  # a client that sends nothing is dropped; it cannot hold a thread

    def _answer(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _route(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            alive, text = self.server.healthy()  # type: ignore[attr-defined]
            self._answer(200 if alive else 503, (text + "\n").encode(), PLAIN)
        elif path == "/metrics":
            self._answer(200, self.server.metrics(), CONTENT_TYPE)  # type: ignore[attr-defined]
        else:
            self._answer(404, b"not found\n", PLAIN)

    do_GET = _route  # noqa: N815 (the names http.server looks for)
    do_HEAD = _route  # noqa: N815

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """No access log: a probe every few seconds is not news."""


class HealthServer:
    def __init__(
        self, address: tuple[str, int], *, healthy: Healthy, metrics: Callable[[], bytes]
    ) -> None:
        self._address, self._healthy, self._metrics = address, healthy, metrics
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        """The port it listens on (the one asked for, or the one the system chose for 0)."""
        if self._server is None:
            raise RuntimeError("the health listener has not been started")
        return self._server.server_address[1]

    def start(self) -> None:
        host, port = self._address
        try:
            server = ThreadingHTTPServer((host, port), _Handler)
        except OSError as error:
            raise FollowerExit(
                EXIT_CONFIGURATION,
                f"cannot listen on {host}:{port} for /healthz and /metrics"
                f" ({error.strerror or type(error).__name__}); change or unset"
                " SWARMSCRIBE_FOLLOWER_HEALTH_ADDR",
            ) from None
        server.daemon_threads = True
        server.healthy, server.metrics = self._healthy, self._metrics  # type: ignore[attr-defined]
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, name="health", daemon=True)
        self._thread.start()
        logger.info("listening on %s:%d for /healthz and /metrics", host, self.port)

    def close(self) -> None:
        server, self._server = self._server, None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
```

- [ ] **Step 4: The setting, what the agent answers, and where the listener starts**

In `packages/follower/src/swarmscribe_follower/config.py`, 4 changes.

**1.** Replace

```python
    on_drained: Literal["exit", "park"] = "exit"
    log_format: Literal["json", "text"] = "json"
```

with

```python
    on_drained: Literal["exit", "park"] = "exit"
    log_format: Literal["json", "text"] = "json"
    # `host:port` for /healthz and /metrics. Unset: no listener, and no port is opened.
    health_addr: str | None = None
```

**2.** Replace

```python
        "model_dir",
        "startup_model",
        mode="before",
```

with

```python
        "model_dir",
        "startup_model",
        "health_addr",
        mode="before",
```

**3.** Replace

```python
    @model_validator(mode="after")
    def _startup_model_is_allowed(self) -> "Settings":
```

with

```python
    @field_validator("health_addr")
    @classmethod
    def _a_host_and_a_port(cls, value: str | None) -> str | None:
        if value is None:
            return None
        host, _, port = value.strip().rpartition(":")
        host = host.removeprefix("[").removesuffix("]")  # [::1]:9108
        if not host or not port.isascii() or not port.isdigit() or not 0 < int(port) < 65536:
            raise ValueError("health_addr must be host:port, e.g. 127.0.0.1:9108")
        return f"{host}:{int(port)}"

    @model_validator(mode="after")
    def _startup_model_is_allowed(self) -> "Settings":
```

**4.** Replace

```python
    @property
    def credential_file(self) -> Path:
```

with

```python
    @property
    def health_address(self) -> tuple[str, int] | None:
        """(host, port) to listen on for /healthz and /metrics, or None for no listener."""
        if self.health_addr is None:
            return None
        host, _, port = self.health_addr.rpartition(":")
        return host, int(port)

    @property
    def credential_file(self) -> Path:
```

In `packages/follower/src/swarmscribe_follower/leader.py`, 2 changes.

**1.** Replace

```python
TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})
```

with

```python
TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})
REQUEST_TIMEOUT_SECONDS = 30.0  # how long one request to the leader may take
```

**2.** Replace

```python
        verify: Any = True,
        timeout: float = 30.0,
    ) -> None:
        self.credential = credential
```

with

```python
        verify: Any = True,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self.credential = credential
```

In `packages/follower/src/swarmscribe_follower/job.py`, 6 changes.

**1.** Replace

```python
from .leader import Interrupted, LeaderClient, Refused, Transient, retrying
```

with

```python
from .leader import (
    REQUEST_TIMEOUT_SECONDS,
    Interrupted,
    LeaderClient,
    Refused,
    Transient,
    retrying,
)
```

**2.** Replace

```python
UPLOAD_AGAIN = frozenset({"outputs_missing", "checksum_mismatch", "outputs_changed"})
```

with

```python
UPLOAD_AGAIN = frozenset({"outputs_missing", "checksum_mismatch", "outputs_changed"})
KEEPER_INTERVALS = 3  # /healthz: the lease keeper must have gone round within this many
```

**3.** Replace

```python
        self._metrics = metrics or Metrics()
        self._control: JobControl | None = None
```

with

```python
        self._metrics = metrics or Metrics()
        self._keeper: LeaseKeeper | None = None
        self._control: JobControl | None = None
```

**4.** Replace

```python
    # --- the job -------------------------------------------------------------------------

    def run(self, claim: ClaimResponse, control: JobControl) -> JobResult:
```

with

```python
    def keeper_stalled(self) -> bool:
        """True when a job is held and its lease keeper has not gone round its loop for
        three heartbeat intervals plus the time one request may take (follower spec 9). A
        leader that does not answer never makes this true: the keeper still goes round."""
        keeper = self._keeper
        if keeper is None or not keeper.is_alive():
            return False  # no job, or the keeper ended itself and stopped the job
        allowed = KEEPER_INTERVALS * self._interval + REQUEST_TIMEOUT_SECONDS
        return time.monotonic() - keeper.last_loop > allowed

    # --- the job -------------------------------------------------------------------------

    def run(self, claim: ClaimResponse, control: JobControl) -> JobResult:
```

**5.** Replace

```python
        folder: Path | None = None
        self._control, self._phase = control, "starting"
```

with

```python
        folder: Path | None = None
        self._control, self._phase, self._keeper = control, "starting", keeper
```

**6.** Replace

```python
            keeper.finish()
            self._clean_up(folder, extra)
            self._control, self._phase = None, "idle"
```

with

```python
            keeper.finish()
            self._clean_up(folder, extra)
            self._control, self._phase, self._keeper = None, "idle", None
```

In `packages/follower/src/swarmscribe_follower/agent.py`, 4 changes.

**1.** Replace

```python
POLL_SECONDS = 0.1  # how often the main thread looks at the signal counter
```

with

```python
POLL_SECONDS = 0.1  # how often the main thread looks at the signal counter
SUPERVISOR_SILENCE_SECONDS = 30.0  # /healthz: the main thread must have ticked this recently
```

**2.** Replace

```python
        self.metrics = metrics or Metrics()
        self.metrics.watch(state=self.state, progress=self.progress)
```

with

```python
        self.metrics = metrics or Metrics()
        self.metrics.watch(state=self.state, progress=self.progress)
        self._ticked = time.monotonic()
```

**3.** Replace

```python
    # --- what the listener is told -------------------------------------------------------
```

with

```python
    # --- what the listener is told -------------------------------------------------------

    def tick(self) -> None:
        """The supervising thread is alive. `run_supervised` calls this every time it goes
        round; anything else that supervises an agent (the F4 Windows service) must too."""
        self._ticked = time.monotonic()

    def health(self) -> tuple[bool, str]:
        """Whether the follower's threads are alive, and a line saying so (follower spec 9).
        Never whether the leader answers. True while the model loads: the supervising thread
        ticks through it."""
        if time.monotonic() - self._ticked > SUPERVISOR_SILENCE_SECONDS:
            return False, "the supervising thread has stopped"
        runner = self._runner
        if runner is not None and runner.keeper_stalled():
            return False, "the lease keeper has stopped"
        return True, "ok"
```

**4.** Replace

```python
            while worker.is_alive():
                worker.join(poll)
                arrived = signals.count
```

with

```python
            while worker.is_alive():
                worker.join(poll)
                self.tick()
                arrived = signals.count
```

In `packages/follower/src/swarmscribe_follower/main.py`, 2 changes.

**1.** Replace

```python
from .errors import EXIT_CONFIGURATION, EXIT_OK, EXIT_UNFIT, FollowerExit
from .leader import LeaderClient, Refused, Transient
```

with

```python
from .errors import EXIT_CONFIGURATION, EXIT_OK, EXIT_UNFIT, FollowerExit
from .health import HealthServer
from .leader import LeaderClient, Refused, Transient
```

**2.** Replace

```python
    agent = build(settings)
    return agent.run_supervised()
```

with

```python
    agent = build(settings)
    listener = None
    if settings.health_address is not None:
        # Before the model is loaded (spec 5.2, step 2): a slow load or a first download
        # must not look like a failed start to whoever probes /healthz.
        listener = HealthServer(
            settings.health_address, healthy=agent.health, metrics=agent.metrics.render
        )
        listener.start()
    try:
        return agent.run_supervised()
    finally:
        if listener is not None:
            listener.close()
```

The Windows service wrapper of plan F4 does not go through `run_supervised`: it must call `agent.tick()` from its own loop, or `/healthz` turns 503 after 30 seconds. `tick`'s docstring says so; add the item to F4's list when this merges.

- [ ] **Step 5: Run the tests, then the whole suite**

Run: `uv run pytest packages/follower/tests/test_health.py -v`
Expected: 30 passed.

Run: `uv run ruff check . && uv run pytest`
Expected: `All checks passed!` and no failure.

- [ ] **Step 6: See it answer on a real follower (no Docker)**

In one terminal (any leader URL that does not answer will do; the follower retries its registration for ever):

```bash
SWARMSCRIBE_LEADER_URL=https://leader.invalid SWARMSCRIBE_JOIN_TOKEN=x \
SWARMSCRIBE_FOLLOWER_STARTUP_MODEL=tiny.en SWARMSCRIBE_FOLLOWER_HEALTH_ADDR=127.0.0.1:9108 \
SWARMSCRIBE_FOLLOWER_STATE_DIR=/tmp/follower-health-try uv run swarmscribe-follower run
```

In another: `curl -si http://127.0.0.1:9108/healthz` and `curl -s http://127.0.0.1:9108/metrics | grep state`
Expected: `HTTP/1.1 200 OK` with the body `ok`, also while the model is still loading; and `swarmscribe_follower_state{state="idle"} 1.0` with the three other states at `0.0`. Stop the follower with Ctrl+C (exit 0) and delete the state folder.

- [ ] **Step 7: Commit**

```bash
git add packages/follower/src/swarmscribe_follower packages/follower/tests/test_health.py
git commit -m "feat(follower): an optional listener for /healthz and /metrics, up before the model loads"
```

---

### Task 3: The memory guard

**Files:**
- Create: `packages/follower/src/swarmscribe_follower/memory.py`
- Modify: `config.py`, `job.py`, `agent.py`, `main.py` in `packages/follower/src/swarmscribe_follower/`
- Test: `packages/follower/tests/test_memory.py`

**Interfaces:**
- Consumes: `models.OutOfMemory` (exists; `job.classify` already maps it to `fail` `out_of_resources`, retryable); `JobRunner._work` with its phases; `av.open` (PyAV, already a dependency of the engine); `doctor --no-leader` (F2a).
- Produces: the setting `SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB` as `Settings.memory_limit_mb: int | None` (at least 64; blank is unset). `swarmscribe_follower.memory`: `Limit(megabytes, source)`, `find_limit(configured, *, cgroup, physical) -> Limit | None`, `cgroup_limit_mb(paths) -> int | None`, `physical_mb() -> int | None`, `rss_mb() -> int | None`, `probe_recording(path) -> tuple[float, int] | None` (seconds, channels), `job_mb(seconds, *, split) -> float`, `MemoryGuard(limit, *, held, probe)` with `check(path, channel_mode)` raising `OutOfMemory`; the constants `JOB_BASE_MB = 100`, `MONO_MB_PER_HOUR = 3600`, `SPLIT_MB_PER_HOUR = 3900`. New optional keyword arguments `JobRunner(..., guard: MemoryGuard | None)` and `Agent(..., guard: MemoryGuard | None)`. `doctor` prints `memory: <n> MiB may be used (<where the figure comes from>)`. Task 4's Compose test sets the limit to 2500 and queues an hour.

- [ ] **Step 1: Write the failing tests**

Create `packages/follower/tests/test_memory.py`:

```python
import io
import wave
from pathlib import Path

import pytest
from follower_testkit import CPU, FakeEngine, FakeLeader, make_runner
from swarmscribe_follower import main as cli
from swarmscribe_follower import memory
from swarmscribe_follower.config import Settings
from swarmscribe_follower.device import Probe
from swarmscribe_follower.leader import LeaderClient
from swarmscribe_follower.lease import JobControl
from swarmscribe_follower.memory import Limit, MemoryGuard, find_limit, job_mb, probe_recording
from swarmscribe_follower.models import ModelHost, OutOfMemory

HOUR = 3600.0


def guard(limit_mb, seconds, channels=1, held=500):
    return MemoryGuard(
        Limit(limit_mb, "a test"), held=lambda: held, probe=lambda path: (seconds, channels)
    )


def test_the_estimate_grows_with_the_recording_and_a_split_costs_more():
    assert job_mb(0, split=False) == memory.JOB_BASE_MB == 100
    assert job_mb(HOUR, split=False) == 100 + memory.MONO_MB_PER_HOUR == 3700
    assert job_mb(HOUR, split=True) == 100 + memory.SPLIT_MB_PER_HOUR == 4000
    assert job_mb(HOUR / 2, split=False) == 1900
    assert job_mb(-5, split=False) == 100


def test_a_recording_that_fits_passes_and_one_that_does_not_is_refused_by_its_length():
    guard(4200, HOUR).check(Path("source"), "mono")  # 500 held + 3700: just inside
    with pytest.raises(OutOfMemory) as refused:
        guard(4199, HOUR).check(Path("source"), "mono")
    said = str(refused.value)
    assert "60 minutes" in said and "4200 MiB" in said and "4199 MiB (a test)" in said
    assert "source" not in said  # nothing of the recording but its length


@pytest.mark.parametrize(
    ("mode", "channels", "split"),
    [
        ("mono", 2, False),
        ("stereo_split", 2, True),
        ("auto", 2, True),
        ("auto", 1, False),
        ("auto", 6, False),
    ],
)
def test_a_recording_counts_as_split_only_when_the_engine_will_split_it(mode, channels, split):
    limit = 500 + 3850  # between the mono and the split figure for an hour
    check = guard(limit, HOUR, channels).check
    if split:
        with pytest.raises(OutOfMemory):
            check(Path("source"), mode)
    else:
        check(Path("source"), mode)


def test_a_recording_whose_length_cannot_be_read_is_left_to_the_engine():
    unreadable = MemoryGuard(Limit(64, "a test"), held=lambda: 500, probe=lambda path: None)
    unreadable.check(Path("source"), "mono")


def test_a_platform_that_will_not_say_what_is_held_counts_the_job_alone():
    unknown = MemoryGuard(Limit(3700, "a test"), held=lambda: None, probe=lambda p: (HOUR, 1))
    unknown.check(Path("source"), "mono")


# --- the limit ----------------------------------------------------------------------------


def test_the_setting_wins_over_everything():
    limit = find_limit(2048, cgroup=lambda: 512, physical=lambda: 64000)
    assert limit == Limit(2048, "SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB")


def test_the_smaller_of_the_container_and_the_machine_is_the_limit():
    assert find_limit(None, cgroup=lambda: 4096, physical=lambda: 64000).megabytes == 4096
    assert find_limit(None, cgroup=lambda: None, physical=lambda: 16000) == Limit(
        16000, "this machine"
    )
    # A container limit above the machine's memory is no limit at all.
    assert find_limit(None, cgroup=lambda: 99999, physical=lambda: 8000).megabytes == 8000


def test_nothing_known_means_no_limit_and_a_nonsense_figure_is_ignored():
    assert find_limit(None, cgroup=lambda: None, physical=lambda: None) is None
    assert find_limit(None, cgroup=lambda: 0, physical=lambda: None) is None
    assert find_limit(None, cgroup=lambda: 3, physical=lambda: 16000).megabytes == 16000


@pytest.mark.parametrize(
    ("content", "megabytes"),
    [
        ("max\n", None),  # cgroup v2: unlimited
        ("4294967296\n", 4096),
        ("9223372036854771712\n", None),  # cgroup v1: unlimited
        ("junk\n", None),
    ],
)
def test_the_container_limit_is_read_from_the_cgroup_file(tmp_path, content, megabytes):
    file = tmp_path / "memory.max"
    file.write_text(content, encoding="ascii")
    assert memory.cgroup_limit_mb((tmp_path / "absent", file)) == megabytes


def test_no_cgroup_file_is_no_container_limit(tmp_path):
    assert memory.cgroup_limit_mb((tmp_path / "absent", tmp_path / "also-absent")) is None


def test_this_machine_says_how_much_memory_it_has():
    assert (memory.physical_mb() or 0) >= 256


def test_the_setting_must_be_a_sensible_number_and_blank_means_unset(monkeypatch):
    monkeypatch.setenv("SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB", "")
    assert Settings(leader_url="https://l.example.org").memory_limit_mb is None
    monkeypatch.setenv("SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB", "8192")
    assert Settings(leader_url="https://l.example.org").memory_limit_mb == 8192
    monkeypatch.setenv("SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB", "8")
    with pytest.raises(ValueError, match="memory_limit_mb"):
        Settings(leader_url="https://l.example.org")


# --- a real file ----------------------------------------------------------------------------


def write_wav(path, seconds, channels):
    with wave.open(str(path), "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(8000)
        out.writeframes(b"\0\0" * channels * 8000 * seconds)


def test_the_length_and_the_channels_come_from_the_file_itself(tmp_path):
    pytest.importorskip("av")
    write_wav(tmp_path / "source", 3, 2)  # no extension: the follower's scratch name
    seconds, channels = probe_recording(tmp_path / "source")
    assert (round(seconds, 1), channels) == (3.0, 2)


def test_a_file_that_is_not_audio_or_is_missing_has_no_length(tmp_path):
    (tmp_path / "source").write_bytes(b"not audio at all" * 100)
    assert probe_recording(tmp_path / "source") is None
    assert probe_recording(tmp_path / "absent") is None


# --- in a job ---------------------------------------------------------------------------------


def run_guarded(tmp_path, limit_mb, seconds):
    leader, engine = FakeLeader(links_min_interval=0), FakeEngine()
    job_id = leader.add_job()
    runner, client = make_runner(tmp_path, leader, engine, guard=guard(limit_mb, seconds))
    result = runner.run(client.claim(), JobControl())
    return result, leader, engine, job_id


def test_a_job_too_long_for_this_follower_fails_out_of_resources_before_the_engine_runs(
    tmp_path,
):
    result, leader, engine, job_id = run_guarded(tmp_path, 2000, 3 * HOUR)
    assert (result.outcome, result.detail) == ("failed", "out_of_resources")
    failed = leader.failed[0]
    assert (failed["code"], failed["retryable"]) == ("out_of_resources", True)
    assert "180 minutes" in failed["reason"] and "2000 MiB" in failed["reason"]
    assert engine.transcribed == []  # the engine never read the recording
    assert engine.loads  # the model was loaded first: what it holds is counted
    assert leader.released == []  # failed, not released: the machine is not unfit


def test_a_job_that_fits_is_transcribed(tmp_path):
    result, leader, engine, job_id = run_guarded(tmp_path, 64000, HOUR)
    assert result.outcome == "completed" and len(engine.transcribed) == 1


def test_without_a_guard_every_job_is_tried(tmp_path):
    leader, engine = FakeLeader(links_min_interval=0), FakeEngine()
    leader.add_job()
    runner, client = make_runner(tmp_path, leader, engine)
    assert runner.run(client.claim(), JobControl()).outcome == "completed"


# --- doctor -------------------------------------------------------------------------------------


def doctor_says(monkeypatch, limit):
    monkeypatch.setattr(cli, "probe", lambda preference: Probe(CPU))
    monkeypatch.setattr(cli, "find_limit", lambda configured: limit)
    monkeypatch.setenv("SWARMSCRIBE_LEADER_URL", "https://leader.test")
    engine, out = FakeEngine(), io.StringIO()
    code = cli.command_doctor(
        Settings(),
        out,
        ask_leader=False,
        host=lambda device, **kw: ModelHost(device, factory=engine, **kw),
        client=LeaderClient,
    )
    return code, out.getvalue().splitlines()


def test_doctor_says_how_much_memory_the_follower_may_use(monkeypatch, tmp_path):
    monkeypatch.setenv("SWARMSCRIBE_FOLLOWER_STATE_DIR", str(tmp_path / "state"))
    code, lines = doctor_says(monkeypatch, Limit(15832, "this machine"))
    assert code == 0 and "memory: 15832 MiB may be used (this machine)" in lines
    code, lines = doctor_says(monkeypatch, None)
    assert code == 0 and "memory: unknown (recordings are not checked against it)" in lines
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/follower/tests/test_memory.py -v`
Expected: an error at import: `ImportError: cannot import name 'memory' from 'swarmscribe_follower'`.

- [ ] **Step 3: Write the guard**

Create `packages/follower/src/swarmscribe_follower/memory.py`:

```python
"""The memory guard (follower spec 5.7, D22).

The engine holds a whole recording in memory and computes its features in one piece, so a
long recording on a small machine ends in an out-of-memory kill: no call reaches the leader,
the lease expires, and the job is tried again, and killed again, hours apart. The guard
estimates what a job needs before it starts and fails the job `out_of_resources` when that
cannot fit, which the leader records with its reason.

The figures were measured (plan F2b, 2026-10-05; faster-whisper 1.2.1, on the CPU and on a
GPU alike, because the features are computed on the CPU either way): the peak grows with the
length of the recording, about 3.5 GiB per hour of speech in one pass, and a split recording
holds the other channel meanwhile."""

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .models import OutOfMemory

JOB_BASE_MB = 100.0  # what a job of any length adds (measured: 50 to 80)
MONO_MB_PER_HOUR = 3600.0  # measured 3460: mono, or `auto` on anything but a stereo file
SPLIT_MB_PER_HOUR = 3900.0  # the same pass, with the other channel (about 230 MiB/h) held
MIN_LIMIT_MB = 64
_UNLIMITED = 1 << 60  # cgroup v1 says "no limit" with a number near 2**63
CGROUP_V2 = Path("/sys/fs/cgroup/memory.max")
CGROUP_V1 = Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")


def _megabytes(count: int) -> int:
    return count // (1024 * 1024)


def cgroup_limit_mb(paths: tuple[Path, ...] = (CGROUP_V2, CGROUP_V1)) -> int | None:
    """The container's memory limit, or None when there is none (or no cgroup: Windows)."""
    for path in paths:
        try:
            text = path.read_text(encoding="ascii").strip()
        except (OSError, ValueError):
            continue
        if text.isdigit() and int(text) < _UNLIMITED:
            return _megabytes(int(text))
        return None
    return None


def physical_mb() -> int | None:
    """The machine's memory, or None when the platform will not say."""
    if os.name == "nt":
        import ctypes

        class Status(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong)
                for name in ("total", "free", "page", "page_free", "virt", "virt_free", "ext")
            ]

        status = Status()
        status.length = ctypes.sizeof(Status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return _megabytes(status.total)
    try:
        return _megabytes(os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE"))
    except (ValueError, OSError, AttributeError):
        return None


def rss_mb() -> int | None:
    """What this process holds now (the loaded model is most of it), or None when the
    platform will not say (then the guard counts the job alone)."""
    try:
        with open("/proc/self/statm", encoding="ascii") as handle:
            return _megabytes(int(handle.read().split()[1]) * os.sysconf("SC_PAGE_SIZE"))
    except (OSError, ValueError, IndexError, AttributeError):
        return None


@dataclass(frozen=True)
class Limit:
    megabytes: int
    source: str  # for `doctor` and for a failure reason: where the figure comes from


def find_limit(
    configured: int | None,
    *,
    cgroup: Callable[[], int | None] = cgroup_limit_mb,
    physical: Callable[[], int | None] = physical_mb,
) -> Limit | None:
    """The memory this follower may use: the setting, else the smaller of the container's
    limit and the machine's memory; None when neither is known (then there is no guard)."""
    if configured is not None:
        return Limit(configured, "SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB")
    found = [
        Limit(value, source)
        for value, source in ((cgroup(), "the container's limit"), (physical(), "this machine"))
        if value is not None and value >= MIN_LIMIT_MB
    ]
    return min(found, key=lambda limit: limit.megabytes) if found else None


def probe_recording(path: Path) -> tuple[float, int] | None:
    """(seconds, channels of the first audio stream) from the container's header, or None
    when it cannot be read: the engine then says what is wrong with the file, not the guard."""
    try:
        import av

        with av.open(str(path)) as container:
            stream = container.streams.audio[0]
            channels = stream.codec_context.layout.nb_channels
            if container.duration is not None:
                return container.duration / av.time_base, channels
            if stream.duration is not None and stream.time_base is not None:
                return float(stream.duration * stream.time_base), channels
    except Exception:
        return None
    return None


def job_mb(seconds: float, *, split: bool) -> float:
    """What transcribing a recording of this length adds to the process, at most."""
    per_hour = SPLIT_MB_PER_HOUR if split else MONO_MB_PER_HOUR
    return JOB_BASE_MB + max(0.0, seconds) / 3600.0 * per_hour


class MemoryGuard:
    def __init__(
        self,
        limit: Limit,
        *,
        held: Callable[[], int | None] = rss_mb,
        probe: Callable[[Path], tuple[float, int] | None] = probe_recording,
    ) -> None:
        self.limit, self._held, self._probe = limit, held, probe

    def check(self, path: Path, channel_mode: str) -> None:
        """Raise OutOfMemory when the recording cannot be transcribed inside the limit. The
        reason says nothing of the recording but its length."""
        found = self._probe(path)
        if found is None:
            return
        seconds, channels = found
        split = channel_mode == "stereo_split" or (channel_mode == "auto" and channels == 2)
        needed = (self._held() or 0) + job_mb(seconds, split=split)
        if needed > self.limit.megabytes:
            raise OutOfMemory(
                f"a recording of {seconds / 60:.0f} minutes needs about {needed:.0f} MiB here"
                f" and this follower may use {self.limit.megabytes} MiB ({self.limit.source})"
            )
```

- [ ] **Step 4: The setting, the job step and `doctor`'s line**

In `packages/follower/src/swarmscribe_follower/config.py`, 2 changes.

**1.** Replace

```python
    health_addr: str | None = None
```

with

```python
    health_addr: str | None = None
    # The memory this follower may use, in MiB. Unset: the container's limit, else the
    # machine's memory (spec 5.7).
    memory_limit_mb: int | None = Field(default=None, ge=64)
```

**2.** Replace

```python
        "health_addr",
        mode="before",
```

with

```python
        "health_addr",
        "memory_limit_mb",
        mode="before",
```

In `packages/follower/src/swarmscribe_follower/job.py`, 4 changes.

**1.** Replace

```python
from .metrics import Metrics
```

with

```python
from .memory import MemoryGuard
from .metrics import Metrics
```

**2.** Replace

```python
        metrics: Metrics | None = None,
    ) -> None:
        self._client, self._links, self._models, self._scratch = client, links, models, scratch
```

with

```python
        metrics: Metrics | None = None,
        guard: MemoryGuard | None = None,
    ) -> None:
        self._client, self._links, self._models, self._scratch = client, links, models, scratch
```

**3.** Replace

```python
        self._metrics = metrics or Metrics()
        self._keeper: LeaseKeeper | None = None
```

with

```python
        self._metrics = metrics or Metrics()
        self._guard = guard  # None: this follower has no memory limit to hold a job to
        self._keeper: LeaseKeeper | None = None
```

**4.** Replace

```python
        control.check()

        self._phase = "transcribe"
```

with

```python
        control.check()

        # After the model is in memory (what it holds is counted) and before the engine
        # reads the recording. Not the "model" phase: a recording too long for this machine
        # fails the job `out_of_resources`; it does not make the machine unfit.
        self._phase = "guard"
        if self._guard is not None:
            self._guard.check(source, settings.channel_mode)

        self._phase = "transcribe"
```

In `packages/follower/src/swarmscribe_follower/agent.py`, 3 changes.

**1.** Replace

```python
from .lease import SHUTDOWN, JobControl
from .metrics import Metrics
```

with

```python
from .lease import SHUTDOWN, JobControl
from .memory import MemoryGuard
from .metrics import Metrics
```

**2.** Replace

```python
        metrics: Metrics | None = None,
    ) -> None:
        self.metrics = metrics or Metrics()
```

with

```python
        metrics: Metrics | None = None,
        guard: MemoryGuard | None = None,
    ) -> None:
        self._guard = guard
        self.metrics = metrics or Metrics()
```

**3.** Replace

```python
            metrics=self.metrics,
        )

    def register(self) -> None:
```

with

```python
            metrics=self.metrics,
            guard=self._guard,
        )

    def register(self) -> None:
```

In `packages/follower/src/swarmscribe_follower/main.py`, 4 changes.

**1.** Replace

```python
from .leader import LeaderClient, Refused, Transient
from .metrics import Metrics
```

with

```python
from .leader import LeaderClient, Refused, Transient
from .memory import MemoryGuard, find_limit
from .metrics import Metrics
```

**2.** Replace

```python
    metrics = Metrics()
    return Agent(
```

with

```python
    metrics = Metrics()
    limit = find_limit(settings.memory_limit_mb)
    return Agent(
```

**3.** Replace

```python
        metrics=metrics,
    )
```

with

```python
        metrics=metrics,
        guard=MemoryGuard(limit) if limit is not None else None,
    )
```

**4.** Replace

```python
    print(f"cached models: {', '.join(cached_models(settings.model_dir)) or '(none)'}", file=out)
```

with

```python
    print(f"cached models: {', '.join(cached_models(settings.model_dir)) or '(none)'}", file=out)
    limit = find_limit(settings.memory_limit_mb)
    if limit is None:
        print("memory: unknown (recordings are not checked against it)", file=out)
    else:
        print(f"memory: {limit.megabytes} MiB may be used ({limit.source})", file=out)
```

The phase is named `"guard"` on purpose. `JobRunner._settle` treats an `OutOfMemory` raised in the `"model"` phase as "this machine cannot serve its pool" (release the job, exit 3): right for a model that does not fit, wrong for one recording that is too long.

- [ ] **Step 5: Run the tests, then the whole suite**

Run: `uv run pytest packages/follower/tests/test_memory.py -v`
Expected: 25 passed.

Run: `uv run ruff check . && uv run pytest`
Expected: `All checks passed!` and no failure.

- [ ] **Step 6: Commit**

```bash
git add packages/follower/src/swarmscribe_follower packages/follower/tests/test_memory.py
git commit -m "feat(follower): refuse a recording that cannot fit in memory, before the engine reads it"
```

---

### Task 4: The image listens and is probed; the Compose test sees health, metrics and the guard

**Files:**
- Modify: `docker/follower.Dockerfile`
- Modify: `docker/check-follower-image.sh`
- Modify: `e2e/follower-compose/docker-compose.yml`, `e2e/follower-compose/run_e2e.py`
- Modify: `packages/follower/tests/test_compose_driver.py` (append)

**Interfaces:**
- Consumes: Tasks 1 to 3; F2a's Dockerfile, check script, Compose file and driver (`start`, `compose`, `must`, `until`, `job_of`, `attempts_of`, `transcript`, `check_outputs`, `service_of`, `TALKS`, `FOLLOWERS`).
- Produces: the image sets `SWARMSCRIBE_FOLLOWER_HEALTH_ADDR=127.0.0.1:9108` and has a `HEALTHCHECK` on `/healthz`. The check script fails an image without them and, for a baked image, waits for Docker to report the container `healthy` with no leader in reach and reads `/metrics`. In the driver: `write_silence(path, seconds)`, `health(service) -> str`, `metrics(service) -> dict[str, float]`, `TOO_LONG`; the scenario gains a step and its later steps move up by one. The followers of the Compose file have `SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB: "2500"`.

- [ ] **Step 1: Turn the listener on in the image and probe it**

In `docker/follower.Dockerfile`, two changes.

**1.** Replace

```dockerfile
    SWARMSCRIBE_FOLLOWER_OFFLINE=${BAKED:-0}
```

with

```dockerfile
    SWARMSCRIBE_FOLLOWER_OFFLINE=${BAKED:-0} \
    SWARMSCRIBE_FOLLOWER_HEALTH_ADDR=127.0.0.1:9108
```

**2.** Replace

```dockerfile
# No HEALTHCHECK yet: the follower listens on no port. Plan F2b adds the listener and it.
```

with

```dockerfile
# /healthz says the follower's threads are alive; it answers while the model loads and
# while the leader is away. Loopback only: nothing outside the container can reach it. A
# follower started with the listener turned off (the variable set to nothing) is not probed.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD ["python", "-c", "import os, sys, urllib.request; a = os.environ.get('SWARMSCRIBE_FOLLOWER_HEALTH_ADDR', ''); sys.exit(0 if not a or urllib.request.urlopen('http://' + a + '/healthz', timeout=3).status == 200 else 1)"]
```

- [ ] **Step 2: Have the check script look**

In `docker/check-follower-image.sh`, three changes.

**1.** Replace

```bash

# The credential is refused in a folder that others can write to, or that is not its own.
```

with

```bash

# The listener for /healthz and /metrics is on loopback only, and the image probes it.
[ "$(setting SWARMSCRIBE_FOLLOWER_HEALTH_ADDR)" = "127.0.0.1:9108" ] \
  || fail "the health listener is not on 127.0.0.1:9108"
docker inspect --format '{{json .Config.Healthcheck}}' "$image" | grep -q '/healthz' \
  || fail "the image has no HEALTHCHECK on /healthz"

# The credential is refused in a folder that others can write to, or that is not its own.
```

**2.** Replace

```bash
      -e SWARMSCRIBE_JOIN_TOKEN=not-a-token "$image" >/dev/null
```

with

```bash
      --health-interval 2s --health-start-period 1s \
      -e SWARMSCRIBE_JOIN_TOKEN=not-a-token "$image" >/dev/null
```

**3.** Replace

```bash
    begun="$(date +%s)"
```

with

```bash

    # Healthy with no leader in reach: /healthz is about the follower's own threads.
    health=""
    for _ in $(seq 1 30); do
      health="$(docker inspect --format '{{.State.Health.Status}}' "$name")"
      [ "$health" = "healthy" ] && break
      sleep 1
    done
    [ "$health" = "healthy" ] || fail "Docker reports the follower $health while it waits for a leader"
    metrics="$(docker exec "$name" python -c \
      "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:9108/metrics', timeout=3).read().decode())")" \
      || fail "/metrics does not answer"
    echo "$metrics" | grep -q '^swarmscribe_follower_state{state="idle"} 1.0$' \
      || fail "/metrics does not show an idle follower"
    echo "$metrics" | grep -q '^swarmscribe_follower_model_load_seconds [0-9]' \
      || fail "/metrics does not show the model load"
    begun="$(date +%s)"
```

- [ ] **Step 3: Build and check**

```bash
docker build --build-arg MODELS=tiny.en -t swarmscribe-follower:e2e --target cpu -f docker/follower.Dockerfile .
docker build -t swarmscribe-follower:cpu --target cpu -f docker/follower.Dockerfile .
bash docker/check-follower-image.sh swarmscribe-follower:e2e cpu tiny.en
bash docker/check-follower-image.sh swarmscribe-follower:cpu cpu
```

Expected:

```
ok: swarmscribe-follower:e2e is a cpu follower with tiny.en baked in (936 MB)
ok: swarmscribe-follower:cpu is a cpu follower (787 MB)
```

The first now also waits, with `--network none`, until Docker calls the container `healthy`: a follower that cannot reach its leader is alive, and says so.

- [ ] **Step 4: Write the failing test of the Compose limit**

Append to `packages/follower/tests/test_compose_driver.py`:

```python
def test_the_memory_limit_admits_the_long_recordings_and_refuses_the_hour(loaded):
    from swarmscribe_follower.memory import job_mb

    compose = yaml.safe_load((COMPOSE / "docker-compose.yml").read_text(encoding="utf-8"))
    environment = compose["services"]["follower-1"]["environment"]
    limit = int(environment["SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB"])
    # Refused whatever the model holds; the driver writes exactly an hour.
    assert job_mb(3600, split=False) > limit
    # Admitted with room to spare: large-v3 on a GPU holds about 1.1 GB on the host.
    longest = 96 * loaded.fixture_seconds()
    assert 1100 + job_mb(longest, split=True) < limit
    assert loaded.TOO_LONG not in loaded.SHORT[loaded.TALKS]
```

Run: `uv run pytest packages/follower/tests/test_compose_driver.py -v`
Expected: the new test fails with `KeyError: 'SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB'`; the other 15 pass.

- [ ] **Step 5: Give the Compose followers a memory limit and a faster probe**

In `e2e/follower-compose/docker-compose.yml`:

Replace

```yaml
    # Unset in Compose and Kubernetes arrives as an empty string: it must mean "none".
    SWARMSCRIBE_LEADER_CA_FILE: ""
```

with

```yaml
    # Unset in Compose and Kubernetes arrives as an empty string: it must mean "none".
    SWARMSCRIBE_LEADER_CA_FILE: ""
    # Room for the model and the four- and eight-minute recordings, and not for an hour:
    # run_e2e.py queues one, and the memory guard must refuse it (spec 5.7).
    SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB: "2500"
  # The image's own HEALTHCHECK on /healthz, asked more often than its 30 s.
  healthcheck:
    interval: 2s
    timeout: 5s
    retries: 3
    start_period: 1s
```

The `healthcheck` block has no `test`: Compose keeps the image's own and changes only the timing.

- [ ] **Step 6: Extend the scenario**

In `e2e/follower-compose/run_e2e.py`, ten changes.

**1.** Replace

```python
5. a drained follower exits 0, exits 0 again when started again, and takes nothing more;
```

with

```python
5. an hour-long recording is refused by the memory guard, three times, and parked as
   failed with its reason, without the engine ever reading it; `/metrics` counts all of it;
6. a drained follower exits 0, exits 0 again when started again, and takes nothing more;
```

**2.** Replace

```python
6. a revoked follower exits 4, and 4 again when started again;
7. the recording without consent
```

with

```python
7. a revoked follower exits 4, and 4 again when started again;
8. the recording without consent
```

**3.** Replace

```python
KILLED, STOPPED, AFTER_DRAIN = "long/kill.wav", "long/stop.wav", "day2/after-drain.wav"
```

with

```python
KILLED, STOPPED, AFTER_DRAIN = "long/kill.wav", "long/stop.wav", "day2/after-drain.wav"
TOO_LONG = "an-hour.wav"  # in TALKS: more than the followers' memory limit allows
```

**4.** Replace

```python
def prepare() -> None:
```

with

```python
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


def prepare() -> None:
```

**5.** Replace

```python
def start(service: str) -> None:
    must(compose("start", service), f"starting {service}")
```

with

```python
def start(service: str) -> None:
    must(compose("start", service), f"starting {service}")


def health(service: str) -> str:
    """Docker's own view of the container, from the image's HEALTHCHECK."""
    container_id = compose("ps", "-a", "-q", service).stdout.strip()
    done = subprocess.run(
        ["docker", "inspect", "--format", "{{.State.Health.Status}}", container_id],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return done.stdout.strip()


def metrics(service: str) -> dict[str, float]:
    """The follower's /metrics, asked from inside its container (the listener is on
    loopback): {sample with its labels: value}."""
    fetch = (
        "import urllib.request;"
        "print(urllib.request.urlopen('http://127.0.0.1:9108/metrics', timeout=5).read().decode())"
    )
    done = compose("exec", "-T", service, "python", "-c", fetch)
    text = must(done, f"reading {service}'s metrics")
    found = {}
    for line in text.splitlines():
        if line and not line.startswith("#"):
            name, _, number = line.rpartition(" ")
            found[name] = float(number)
    return found
```

**6.** Replace

```python
    await until(both_said_so, "both followers to log their registration", 30.0)
```

with

```python
    await until(both_said_so, "both followers to log their registration", 30.0)

    async def both_healthy() -> bool:
        return all(health(name) == "healthy" for name in FOLLOWERS)

    await until(both_healthy, "Docker to report both followers healthy", 60.0)
```

**7.** Replace

```python
    check_outputs(CALLS, KILLED, repeats=target.long_repeats)
```

with

```python
    check_outputs(CALLS, KILLED, repeats=target.long_repeats)
    survivor = service_of[attempts[1][0]]
    expect(
        health(survivor) == "healthy",
        f"{survivor} is {health(survivor)} after a long job under a short lease",
    )
```

**8.** Replace

```python
    # 5. Drain: exit 0, exit 0 again when started again, and nothing more is taken.
```

with

```python
    # 5. The memory guard refuses a recording too long for the followers' limit, on every
    # attempt, and the leader parks the job with the reason; /metrics counts it all.
    write_silence(DATA / TALKS / TOO_LONG, 3600)

    async def parked() -> Job | None:
        job = await job_of(sessions, TALKS, TOO_LONG)
        return job if job is not None and job.state == "failed" else None

    job = await until(parked, f"{TOO_LONG} to be refused for good")
    reason = job.failure_reason or ""
    expect(
        reason.startswith("out_of_resources: ") and "60 minutes" in reason,
        f"{TOO_LONG} failed with an unexpected reason: {reason[:200]}",
    )
    attempts = await attempts_of(sessions, job)
    expect(
        [outcome for _, outcome, _ in attempts] == ["failed"] * 3,
        f"{TOO_LONG}: expected three failed attempts, got {[a[1] for a in attempts]}",
    )
    expect(
        not transcript(TALKS, TOO_LONG, "txt").exists(), f"{TOO_LONG} was transcribed anyway"
    )
    counted = [metrics(name) for name in FOLLOWERS]

    def total(sample: str) -> float:
        return sum(follower.get(sample, 0.0) for follower in counted)

    p = "swarmscribe_follower_"
    expect(total(p + 'jobs_total{outcome="failed"}') == 3, "the refusals are not in /metrics")
    expect(total(p + 'jobs_total{outcome="completed"}') >= 1, "no completion is in /metrics")
    expect(total(p + "audio_seconds_total") > 0, "no audio is counted in /metrics")
    expect(total(p + "download_bytes_total") > 0, "no download is counted in /metrics")
    expect(
        all(follower.get(p + 'state{state="idle"}') == 1.0 for follower in counted),
        "an idle follower does not say so in /metrics",
    )

    # 6. Drain: exit 0, exit 0 again when started again, and nothing more is taken.
```

**9.** Replace

```python
    # 6. Revoke: exit 4, and 4 again.
```

with

```python
    # 7. Revoke: exit 4, and 4 again.
```

**10.** Replace

```python
    # 7. Nothing registered twice, nothing without consent was touched, nothing leaked.
```

with

```python
    # 8. Nothing registered twice, nothing without consent was touched, nothing leaked.
```

Why the hour is silence at 8 kHz and 8 bits: the guard reads the length from the file's header and refuses before the engine opens it, so the content does not matter, and the file is 29 MB instead of 230.

- [ ] **Step 7: Run the driver's tests, the whole suite, and the scenario**

Run: `uv run pytest packages/follower/tests/test_compose_driver.py -v`
Expected: 16 passed.

Run: `uv run ruff check . && uv run pytest`
Expected: `All checks passed!` and no failure.

```bash
docker build -t swarmscribe-leader:e2e -f e2e/compose/Dockerfile .
uv run python e2e/follower-compose/run_e2e.py prepare
docker compose -f e2e/follower-compose/docker-compose.yml up -d
uv run python e2e/follower-compose/run_e2e.py run
docker compose -f e2e/follower-compose/docker-compose.yml --profile followers down -v
```

Expected, after about two minutes (measured 104 and 127 s): the same closing line as before, `passed (tiny.en on cpu): two followers registered in ...`. The scenario now also fails if a follower is not `healthy`, if the hour-long recording is not refused three times with a reason that starts `out_of_resources: ` and says `60 minutes`, or if `/metrics` does not count those three refusals.

- [ ] **Step 8: Commit and push; watch `follower-compose-e2e`**

```bash
git add docker/follower.Dockerfile docker/check-follower-image.sh e2e/follower-compose packages/follower/tests/test_compose_driver.py
git commit -m "feat(follower): the image listens on loopback and is probed; Compose checks health, metrics and the memory guard"
git push -u origin follower-f2b
```

Expected on GitHub: `follower-compose-e2e` green. It needs no change: it builds the same two images and runs the same two commands.

---

### Task 5: The `cuda` extra, the `cuda` target and its CI job

**Files:**
- Modify: `packages/follower/pyproject.toml`, `uv.lock`
- Modify: `docker/follower.Dockerfile`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: the Dockerfile's stages `manifests`, `models`, `runtime` (F2a); the check script's `cuda` branch (F2a): it expects `SWARMSCRIBE_FOLLOWER_DEVICE=cuda` in the image, `libcublas.so.12` loadable by name, and `doctor --no-leader` without a GPU to exit 3 saying `cuda was requested`.
- Produces: the extra `swarmscribe-follower[cuda]` (`nvidia-cublas-cu12`, Linux and Windows); build stage `build-cuda` and target `cuda`; `cpu` stays the last stage and so the default. The image `swarmscribe-follower:cuda`. The GitHub Actions job `follower-cuda-image`. Plan F4's native install uses the same extra.

- [ ] **Step 1: Add the extra and lock it**

In `packages/follower/pyproject.toml`, replace

```toml
[project.scripts]
swarmscribe-follower = "swarmscribe_follower.main:run"
```

with

```toml
[project.optional-dependencies]
# The GPU library CTranslate2 loads by name at the first inference (follower spec D15).
cuda = [
    "nvidia-cublas-cu12>=12,<13; sys_platform == 'linux' or sys_platform == 'win32'",
]

[project.scripts]
swarmscribe-follower = "swarmscribe_follower.main:run"
```

Run: `uv lock`
Expected: `Added nvidia-cublas-cu12 v12.9.2.10` and `Added nvidia-cuda-nvrtc-cu12 v12.9.86` (the second is a dependency of the first), nothing removed.

If `uv lock` picked a newer 12.x, pin the one that was run on the GPU and lock again: `uv lock --upgrade-package nvidia-cublas-cu12==12.9.2.10`. A newer one may only replace it together with a new run of Task 6.

Run: `uv sync && uv run pytest packages/follower -q`
Expected: no failure (the extra is not installed by `uv sync`; nothing in the tests needs it).

- [ ] **Step 2: Add the stages**

In `docker/follower.Dockerfile`, four changes.

**1.** Replace

```dockerfile
#   docker build -t swarmscribe-follower:cpu --target cpu -f docker/follower.Dockerfile .
#
```

with

```dockerfile
#   docker build -t swarmscribe-follower:cpu  --target cpu  -f docker/follower.Dockerfile .
#   docker build -t swarmscribe-follower:cuda --target cuda -f docker/follower.Dockerfile .
#
```

**2.** Replace

```dockerfile
#   docker build --build-arg MODELS=distil-large-v3 -t swarmscribe-follower:cpu-distil-large-v3 \
#     --target cpu -f docker/follower.Dockerfile .
```

with

```dockerfile
#   docker build --build-arg MODELS=large-v3 -t swarmscribe-follower:cuda-large-v3 \
#     --target cuda -f docker/follower.Dockerfile .
```

**3.** Replace

```dockerfile
# --- the baked models (an empty folder when MODELS is empty) ------------------------------
```

with

```dockerfile
# --- the same, with cuBLAS from the nvidia wheel (the `cuda` extra) ----------------------
FROM manifests AS build-cuda
RUN uv sync --frozen --no-dev --package swarmscribe-follower --extra cuda --no-install-workspace
COPY packages/protocol packages/protocol
COPY packages/engine packages/engine
COPY packages/follower packages/follower
RUN uv sync --frozen --no-dev --package swarmscribe-follower --extra cuda --no-editable \
 && rm -f /app/.venv/.lock

# --- the baked models (an empty folder when MODELS is empty) ------------------------------
```

**4.** Replace

```dockerfile
# --- swarmscribe-follower:cpu ---------------------------------------------------------------
```

with

```dockerfile
# --- swarmscribe-follower:cuda -------------------------------------------------------------
FROM runtime AS cuda
COPY --from=build-cuda /app/.venv /app/.venv
# CTranslate2 loads cuBLAS by name at the first inference; the driver's own libraries
# (libcuda) and nvidia-smi come from the NVIDIA container runtime (`--gpus all`). This image
# is for a GPU: without one it says so and exits 3, where `auto` would quietly use the CPU.
ENV LD_LIBRARY_PATH=/app/.venv/lib/python3.12/site-packages/nvidia/cublas/lib \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility \
    SWARMSCRIBE_FOLLOWER_DEVICE=cuda

# --- swarmscribe-follower:cpu (last: the default target) ----------------------------------
```

`cpu` stays last: a `docker build` without `--target` must never produce the GPU image by accident.

- [ ] **Step 3: Build the `cuda` image and check it (no GPU needed)**

```bash
docker build -t swarmscribe-follower:cuda --target cuda -f docker/follower.Dockerfile .
bash docker/check-follower-image.sh swarmscribe-follower:cuda cuda
bash docker/check-follower-image.sh swarmscribe-follower:cpu cpu
```

Expected (80 s for the libraries the first time; the wheel is 581 MB):

```
ok: swarmscribe-follower:cuda is a cuda follower (2546 MB)
ok: swarmscribe-follower:cpu is a cpu follower (787 MB)
```

and, to see the refusal the script relies on:

```bash
docker run --rm --read-only --network none -e SWARMSCRIBE_LEADER_URL=https://leader.invalid \
  swarmscribe-follower:cuda doctor --no-leader; echo "exit $?"
```

Expected: `device: FAILED: cuda was requested but no CUDA GPU is available`, `result: NOT READY (exit 3)`, `exit 3`.

- [ ] **Step 4: Add the CI job**

In `.github/workflows/ci.yml`, insert this job after `follower-compose-e2e` and before `chart`:

```yaml
  # The cuda image is built and checked for what it holds. It is never RUN on a GPU here:
  # GitHub's runners have none (follower spec, ruling R5). See the F2 outcomes document.
  follower-cuda-image:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v4
      - name: Build the cuda follower image, without a model
        run: docker build -t swarmscribe-follower:cuda --target cuda -f docker/follower.Dockerfile .
      - name: Check it (cuBLAS loads by name; without a GPU it says so and exits 3)
        run: bash docker/check-follower-image.sh swarmscribe-follower:cuda cuda
```

Run: `uv run python -c "import yaml; print(sorted(yaml.safe_load(open('.github/workflows/ci.yml'))['jobs']))"`
Expected: the list now contains `follower-cuda-image`.

- [ ] **Step 5: Run the whole suite, commit and push**

Run: `uv run ruff check . && uv run pytest`
Expected: `All checks passed!` and no failure.

```bash
git add packages/follower/pyproject.toml uv.lock docker/follower.Dockerfile .github/workflows/ci.yml
git commit -m "feat(follower): the swarmscribe-follower:cuda image, with cuBLAS from the nvidia wheel"
git push
```

Expected on GitHub: `follower-cuda-image` green in about three minutes.

---

### Task 6: Run the `cuda` image on the GPU, against a real leader, and record it

Ruling R5: this is done on the development machine (RTX 4090, Docker Desktop), by hand, once per change of CTranslate2 or cuBLAS. It was done while this plan was written and passed; this task repeats it with the repository's own files and writes the result down.

**Files:**
- Create: `e2e/follower-compose/docker-compose.gpu.yml`
- Modify: `e2e/follower-compose/run_e2e.py`
- Create: `docs/superpowers/plans/2026-10-05-follower-f2-outcomes.md`

**Interfaces:**
- Consumes: the `cuda` target (Task 5); the driver's `Target`, `CPU`, `target` and `compose()` (F2a); `CHECK_GPU=1` in the check script (F2a).
- Produces: `python e2e/follower-compose/run_e2e.py run --gpu` (the same scenario with the profile of device `cuda` set to large-v3 float16, eight-minute long recordings, and the followers given the GPU); the outcomes document.

- [ ] **Step 1: See that Docker reaches the GPU**

Run: `docker run --rm --gpus all nvidia/cuda:12.9.1-base-ubuntu24.04 nvidia-smi`
Expected: a table naming `NVIDIA GeForce RTX 4090`.

**If this fails** (no NVIDIA runtime, an error about `--gpus`), stop here and do the fallback at the end of this task instead of steps 2 to 6. Do not go on to build a 5 GB image that cannot be run.

- [ ] **Step 2: Give the followers the GPU**

Create `e2e/follower-compose/docker-compose.gpu.yml`:

```yaml
# Added by `run_e2e.py run --gpu`: the followers get the machine's NVIDIA GPU. Needs the
# NVIDIA container runtime (Docker Desktop on Windows has it; on Linux, the NVIDIA Container
# Toolkit) and FOLLOWER_IMAGE set to a cuda image with large-v3 baked in. Never run in CI.
services:
  follower-1:
    gpus: all
  follower-2:
    gpus: all
```

In `e2e/follower-compose/run_e2e.py`, three changes.

**1.** Replace

```python
COMPOSE_FILE = HERE / "docker-compose.yml"
```

with

```python
COMPOSE_FILE = HERE / "docker-compose.yml"
GPU_FILE = HERE / "docker-compose.gpu.yml"
```

**2.** Replace

```python
CPU = Target("cpu", "tiny.en", "int8", (COMPOSE_FILE,), 48)
target = CPU
```

with

```python
CPU = Target("cpu", "tiny.en", "int8", (COMPOSE_FILE,), 48)
# `run --gpu`, on a machine with an NVIDIA GPU: FOLLOWER_IMAGE names a cuda image with
# large-v3 baked in, and the followers are given the GPU. Eight minutes of audio.
GPU = Target("cuda", "large-v3", "float16", (COMPOSE_FILE, GPU_FILE), 96)
target = CPU
```

**3.** Replace

```python
    commands.add_parser("run", help="run the scenario against the running Compose project")
    args = parser.parse_args()
```

with

```python
    scenario_command = commands.add_parser(
        "run", help="run the scenario against the running Compose project"
    )
    scenario_command.add_argument(
        "--gpu", action="store_true", help="the followers are a cuda image and get the GPU"
    )
    args = parser.parse_args()
    global target
    target = GPU if getattr(args, "gpu", False) else CPU
```

Run: `uv run ruff check . && uv run pytest packages/follower/tests/test_compose_driver.py -q`
Expected: `All checks passed!` and 16 passed.

- [ ] **Step 3: Build the `cuda` image with large-v3 baked in, and check it on the GPU**

```bash
docker build --build-arg MODELS=large-v3 -t swarmscribe-follower:cuda-large-v3 --target cuda -f docker/follower.Dockerfile .
CHECK_GPU=1 bash docker/check-follower-image.sh swarmscribe-follower:cuda-large-v3 cuda large-v3
```

Expected: the build prints `baked large-v3: Systran/faster-whisper-large-v3@edaa852ec7e1, 5 files, 3090 MB` (the download took 37 s here), and the check ends with

```
ok: swarmscribe-follower:cuda-large-v3 is a cuda follower with large-v3 baked in (8484 MB)
```

With `CHECK_GPU=1` it ran `doctor` on the GPU with no network (`device: cuda (NVIDIA GeForce RTX 4090, 24564 MiB)`, `model: large-v3 (float16) loaded and ran`) and stopped a follower as PID 1.

- [ ] **Step 4: Run the scenario on the GPU**

```bash
docker build -t swarmscribe-leader:e2e -f e2e/compose/Dockerfile .
uv run python e2e/follower-compose/run_e2e.py prepare
docker compose -f e2e/follower-compose/docker-compose.yml up -d
FOLLOWER_IMAGE=swarmscribe-follower:cuda-large-v3 uv run python e2e/follower-compose/run_e2e.py run --gpu
```

Expected, after about two and a half minutes (the numbers measured here):

```
killed follower-2 mid-job
stopped follower-1 mid-job in 2.3 s
passed (large-v3 on cuda): two followers registered in 9 s and shared six recordings; a killed follower's job was redone in 39 s under an 8 s lease; a stop mid-job took 2.3 s and counted no attempt; drain exited 0 and revoke exited 4, twice each
```

Keep the output: step 6 records it. While it runs, `nvidia-smi` on the host shows two `python` processes on the GPU.

- [ ] **Step 5: Tear down**

```bash
docker compose -f e2e/follower-compose/docker-compose.yml -f e2e/follower-compose/docker-compose.gpu.yml --profile followers down -v
```

- [ ] **Step 6: Record the run**

Create `docs/superpowers/plans/2026-10-05-follower-f2-outcomes.md` with the text below, then replace the three lines marked "this run" with what steps 1, 3 and 4 printed for you. The first table is the planner's run and stays as it is.

```markdown
# Follower F2: the `cuda` image on a GPU (ruling R5)

The follower spec has the `cuda` image proven on the development machine, because GitHub's
runners have no GPU. This is the record. CI builds the image and checks what it holds (job
`follower-cuda-image`); it never runs it on a GPU. Repeat this run, and add a section here,
whenever CTranslate2 or `nvidia-cublas-cu12` changes in `uv.lock`.

## The planner's run, 2026-10-05

Machine: Windows 11, Docker Desktop (Engine 29.8.1, WSL 2), NVIDIA GeForce RTX 4090 (24 GB),
driver 617.14. Built in a scratch folder from the `follower-f1` tree with this plan's files.

| What | Result |
|---|---|
| `docker run --rm --gpus all nvidia/cuda:12.9.1-base-ubuntu24.04 nvidia-smi` | worked: the RTX 4090, NVIDIA-SMI 615.78.02, CUDA 13.4 |
| Libraries in the image | ctranslate2 4.8.2, faster-whisper 1.2.1, nvidia-cublas-cu12 12.9.2.10, nvidia-cuda-nvrtc-cu12 12.9.86; no cuDNN |
| `doctor`, `--gpus all --read-only --cap-drop ALL --network none` | `device: cuda (NVIDIA GeForce RTX 4090, 24564 MiB)`; `model: large-v3 (float16) loaded and ran`; 4.7 s |
| Without cuBLAS on the library path | `RuntimeError: Library libcublas.so.12 is not found or cannot be loaded` |
| Without cuDNN on the library path | loaded, warmed up and transcribed five minutes of audio |
| One hour mono, large-v3 float16 | 377 s |
| One hour split | 282 s |
| `CHECK_GPU=1 check-follower-image.sh ... cuda large-v3` | ok (8484 MB as `docker image inspect` counts it; 4.8 GB of layers) |
| `run_e2e.py run --gpu` | passed twice: registered in 6 and 9 s; the killed follower's job redone in 34 and 39 s under the 8 s lease; a stop mid-job in 1.8 and 2.3 s; the hour-long recording refused three times by the memory guard; drain 0, revoke 4 |
| Host memory, large-v3 on the GPU | 741 MiB once loaded, 3155 MiB at the peak of loading; one hour mono adds 3511 MiB |

## The run of Task 6

- Step 1 (`nvidia-smi` in a container), this run:
- Step 3 (the check's last line), this run:
- Step 4 (the scenario's last line), this run:

## What this does not prove

- A Linux host with the NVIDIA Container Toolkit, and Kubernetes with the device plugin:
  only Docker Desktop on WSL 2 was run. The image asks the runtime for
  `NVIDIA_DRIVER_CAPABILITIES=compute,utility` and needs nothing else from it.
- More than one GPU, and any GPU but the RTX 4090.
- A recording of people talking: the long recordings here are one synthetic phrase repeated,
  which large-v3 partly collapses. The benchmark of 2026-10 is the measure of quality and
  speed on real speech.
```

- [ ] **Step 7: Commit**

```bash
git add e2e/follower-compose/docker-compose.gpu.yml e2e/follower-compose/run_e2e.py docs/superpowers/plans/2026-10-05-follower-f2-outcomes.md
git commit -m "test(follower): run the cuda image on a GPU against a real leader, and record it"
```

**The fallback, only if step 1 failed** (ruling R5's second branch). The image cannot be run here, so the same libraries are proven outside it:

1. In a fresh virtual environment on this Windows machine: `uv venv .venv-gpu && uv pip install --python .venv-gpu/Scripts/python.exe -e "packages/follower[cuda]" -e packages/engine -e packages/protocol`.
2. Windows does not search the wheel's folder for DLLs. Copy them next to CTranslate2's own, which is what the 2026-10 benchmark did: `cp .venv-gpu/Lib/site-packages/nvidia/cublas/bin/*.dll .venv-gpu/Lib/site-packages/ctranslate2/`.
3. `SWARMSCRIBE_LEADER_URL=https://leader.invalid SWARMSCRIBE_FOLLOWER_DEVICE=cuda .venv-gpu/Scripts/swarmscribe-follower doctor --no-leader`. Expected: `device: cuda (NVIDIA GeForce RTX 4090, ...)` and `model: large-v3 (float16) loaded and ran` (the first run downloads 3 GB).
4. Still do step 2 of this task (the file and the driver changes are right whatever this machine can do) and write the outcomes document with the heading "GPU pass-through does not work in Docker Desktop on this machine", the error step 1 printed, the output of the native `doctor`, and the sentence "The `cuda` image has not yet been run on a GPU." Task 7's README then carries that sentence in "Known limits" in place of the one about Docker Desktop. Delete `.venv-gpu`.

---

### Task 7: The README and the spec's amendments

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-10-04-follower-design.md`

**Interfaces:**
- Consumes: everything above; the measured figures of this plan and of Task 6.
- Produces: the README section "Follower images" (replacing F2a's "Follower image"), two rows in the follower's settings table, an updated "Follower Compose test"; the spec section "Amendments after F2".

- [ ] **Step 1: The two settings**

In `README.md`, in the settings table of "Run a follower (development)", replace

```
| `SWARMSCRIBE_FOLLOWER_LOG_FORMAT` | `json` | `json` or `text` |
```

with

```
| `SWARMSCRIBE_FOLLOWER_LOG_FORMAT` | `json` | `json` or `text` |
| `SWARMSCRIBE_FOLLOWER_HEALTH_ADDR` | none (the images: `127.0.0.1:9108`) | `host:port` to listen on for `/healthz` and `/metrics`; unset, the follower opens no port |
| `SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB` | the container's limit, else the machine's memory | the memory the follower may use; a recording that cannot fit is failed `out_of_resources` before it is transcribed |
```

- [ ] **Step 2: The images and the Compose test**

In `README.md`, replace everything from the line `### Follower image` up to, and not including, the line `## Run the fleet console (development)` (F2a's two sections) with:

````markdown
### Follower images

`docker/follower.Dockerfile` builds two images from the same code. Each holds the follower,
the engine and the protocol package, installed with `uv` into an environment that is all
the final image holds beside Python: no leader, no console, no build tools.

| | `swarmscribe-follower:cpu` | `swarmscribe-follower:cuda` |
|---|---|---|
| Build | `--target cpu` (the default) | `--target cuda` |
| Adds | nothing | cuBLAS, from the `nvidia-cublas-cu12` wheel (the follower's `cuda` extra) |
| Device | CPU (`auto` finds no GPU) | `SWARMSCRIBE_FOLLOWER_DEVICE=cuda`: without a GPU it exits `3` and says so, before it registers |
| Default model | `distil-large-v3`, `int8` | `large-v3`, `float16` |
| Size without a model | 0.6 GB | 1.7 GB |
| Needs at run time | nothing | the NVIDIA container runtime, for the driver's own libraries |

```
docker build -t swarmscribe-follower:cpu  --target cpu  -f docker/follower.Dockerfile .
docker build -t swarmscribe-follower:cuda --target cuda -f docker/follower.Dockerfile .
```

**Run one.** The follower is the container's only process (`docker stop` reaches it
directly: it exits `0` with the job in hand released, see "Stopping" above) and runs as user
10001. Restart it on failure only: exit `0` (stopped, or drained) and exit `4` (revoked, or
no valid token) are final.

```
docker run -d --restart on-failure --read-only --cap-drop ALL --stop-timeout 930 \
  -e SWARMSCRIBE_LEADER_URL=https://leader.example.org \
  -e SWARMSCRIBE_JOIN_TOKEN=<the token> \
  -e SWARMSCRIBE_FOLLOWER_SHUTDOWN_GRACE_SECONDS=900 \
  -v swarmscribe-follower:/var/lib/swarmscribe-follower \
  -v swarmscribe-models:/models \
  swarmscribe-follower:cpu
```

For a GPU machine: the `cuda` tag and `--gpus all`. The shorter line of the design
(`docker run -e SWARMSCRIBE_LEADER_URL=... -e SWARMSCRIBE_JOIN_TOKEN=... swarmscribe-follower:cpu`)
works too, with Docker's 10-second stop, a credential that lasts as long as the container,
and a model downloaded again for every new container.

It writes in three places and nowhere else, so the root filesystem can be read-only:

| Path | Holds | In the image |
|---|---|---|
| `/var/lib/swarmscribe-follower` | the credential and the lock file (also `HOME`) | a declared volume; name it (`-v swarmscribe-follower:...`) so that the credential outlives `docker rm`, which a single-use join token needs |
| `/scratch` | the recording being transcribed; emptied after every job and at every start | a declared volume |
| `/models` | the model cache | a plain folder: mount a volume to keep downloads (with `--read-only` it is needed), or bake the models in |

A state folder given as a `tmpfs` must be the follower's own: `--tmpfs
/var/lib/swarmscribe-follower:uid=10001,gid=10001,mode=0700`. A plain tmpfs belongs to root
and is writable by all, and the credential is refused there.

**GPU prerequisites.** An NVIDIA GPU with a driver new enough for CUDA 12, and a Docker that
can hand it to a container: on Linux the NVIDIA Container Toolkit, on Windows Docker Desktop
with WSL 2 (nothing to install). Check with `docker run --rm --gpus all
nvidia/cuda:12.9.1-base-ubuntu24.04 nvidia-smi`, then with the image itself:

```
docker run --rm --gpus all -e SWARMSCRIBE_LEADER_URL=https://leader.example.org \
  swarmscribe-follower:cuda doctor
```

`doctor` names the GPU (`device: cuda (NVIDIA GeForce RTX 4090, 24564 MiB)`), loads the
model and runs it once: GPU libraries are loaded at the first inference, so a missing one
shows here and not in the middle of a job. The image carries cuBLAS only; the CTranslate2
it is locked to does not use cuDNN. One follower uses one GPU: on a machine with several,
run one container per GPU (`--gpus device=0`, `--gpus device=1`), each with its own state
volume.

**Baked models.** By default a follower downloads its start-up model from Hugging Face on
first start into `/models` (1.5 GB for `distil-large-v3`, 3.1 GB for `large-v3`). To put
models into the image instead, which is what a Kubernetes pool should run, name them at
build time:

```
docker build --build-arg MODELS=large-v3 -t swarmscribe-follower:cuda-large-v3 \
  --target cuda -f docker/follower.Dockerfile .
```

`MODELS` is a comma-separated list. Each model is downloaded during the build from one
pinned commit and every file is checked against its SHA-256 in `docker/models.lock.json`;
a file that differs fails the build. The first name becomes the start-up model
(`SWARMSCRIBE_FOLLOWER_STARTUP_MODEL`), and the image is offline
(`SWARMSCRIBE_FOLLOWER_OFFLINE=1`): it never asks Hugging Face for anything, and a job for
a model it does not hold is handed back and the follower exits `3`. The leader's profile
for the device must therefore name a model the image holds (`swarmscribe-admin profiles
set`). To allow a model that is not in the lock file yet, add the entry that
`uv run python docker/fetch_models.py --pin <name>` prints. A baked image is its model
larger: 3.1 GB for `large-v3`, 1.5 GB for `distil-large-v3`.

**Health and metrics.** In the images the follower listens on `127.0.0.1:9108`, inside the
container only, for the image's own `HEALTHCHECK`. `/healthz` answers 200 while the
follower's threads are alive: also while the model loads, and also while the leader is
away, so that nothing kills a follower that is transcribing through an outage. `/metrics`
is in the Prometheus format: `swarmscribe_follower_jobs_total{outcome}`,
`_audio_seconds_total` and `_transcribe_seconds_total` (their ratio is the speed),
`_job_progress`, `_model_load_seconds`, `_heartbeat_failures_total`,
`_download_bytes_total`, `_upload_bytes_total` and `_state` (idle, working, draining,
stopping). To scrape it from outside, listen on the container's address
(`-e SWARMSCRIBE_FOLLOWER_HEALTH_ADDR=0.0.0.0:9108`) and publish the port to the network
your Prometheus is on, never to the internet: it has no authentication. Outside the images
the listener is off unless the variable is set.

**Memory.** The engine reads a whole recording into memory and computes its features in
one piece, so what a job needs grows with the recording's length, on a GPU as on a CPU:

| | Host memory |
|---|---|
| `distil-large-v3`, `int8`, on a CPU | 1.7 GB once loaded |
| `large-v3`, `float16`, on a GPU | 0.75 GB once loaded, 3.2 GB while it loads |
| `large-v3`, `int8`, on a CPU | 3.6 GB once loaded |
| each hour of the longest recording, mono | 3.5 GB (the follower counts 3.6) |
| each hour of the longest recording, split into channels | 2.7 GB when the speakers alternate (the follower counts 3.9, for two who both talk throughout) |

So a CPU follower for recordings of up to one hour needs about 5.5 GB, and one for three
hours about 13 GB; a GPU follower needs 3.5 GB to start at all. Before each job the
follower adds its own estimate for the recording to what it already holds and compares
that with what it may use: `SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB`, else the container's
memory limit, else the machine's memory (`doctor` prints the figure). A recording that
cannot fit is failed `out_of_resources` at once, with its length and the limit in the
reason, instead of being killed half-way three times, hours apart. Give the container a
memory limit (`--memory 8g`) and the guard follows it.

`bash docker/check-follower-image.sh <image> <cpu|cuda> [<baked model>]` checks an image
without a leader: the user and the folders, that the leader and build tools are absent,
that it starts read-only without capabilities, the listener and the `HEALTHCHECK`, for
`cuda` that cuBLAS loads and that the image refuses to start without a GPU, and, for a
baked image, that the model loads and runs with no network at all and that `docker stop`
ends a follower that is still starting. `CHECK_GPU=1` also runs a baked `cuda` image on
this machine's GPU.

**Known limits.**

- The `cuda` image is run on a GPU by hand, not in CI: GitHub's runners have none. It was
  run on an RTX 4090 in Docker Desktop (WSL 2), with a real leader
  (`docs/superpowers/plans/2026-10-05-follower-f2-outcomes.md`). A Linux host with the
  NVIDIA Container Toolkit and Kubernetes with the device plugin have not been run.
- Only `amd64` has been built and run.
- A model downloaded at run time, not baked, is whatever its repository's `main` is that
  day; only baked models are pinned and checked.
- A long recording needs a great deal of memory (the table above); nothing splits it.
- Where the platform does not say what a process holds (Windows, macOS), the memory guard
  counts the recording alone and not the loaded model.
- The images are not published: build them, or push them to a registry of your own.

### Follower Compose test

`e2e/follower-compose/` runs the `cpu` image, with `tiny.en` baked in, as two followers
against Postgres and two leader replicas behind nginx. The followers are read-only, without
capabilities, and on a network with no route out. It checks that both register with one
join token, are reported healthy by Docker, and share six recordings, each transcribed once
and saying what was said (on the right channel, where the location splits channels); that a
follower killed in the middle of a recording loses nothing and comes back as the same
follower with an empty scratch folder; that an 8-second lease survives a transcription
several times as long; that a follower stopped with `SIGTERM` mid-job releases the job
without a counted attempt; that an hour-long recording is refused by the memory guard and
parked with its reason, and that `/metrics` counts it; that a drained follower exits `0`
and a revoked one `4`, again when started again; and that a recording without consent is
never touched and no follower log holds a token, a link or transcript text. It runs in
GitHub Actions (job `follower-compose-e2e`); locally, with Docker:

```
docker build -t swarmscribe-leader:e2e -f e2e/compose/Dockerfile .
docker build --build-arg MODELS=tiny.en -t swarmscribe-follower:e2e --target cpu -f docker/follower.Dockerfile .
bash docker/check-follower-image.sh swarmscribe-follower:e2e cpu tiny.en
uv run python e2e/follower-compose/run_e2e.py prepare
docker compose -f e2e/follower-compose/docker-compose.yml up -d
uv run python e2e/follower-compose/run_e2e.py run
docker compose -f e2e/follower-compose/docker-compose.yml --profile followers down -v
```

It takes about two minutes and runs once per stack (it drains and revokes its followers): a
second run stops at once and says to `down -v` first. The leader is plain `http` behind the
proxy, as it is behind an ingress, so the followers set the development switch
`SWARMSCRIBE_FOLLOWER_ALLOW_HTTP=1`. The driver adds the join token, the profile and the
locations, and drains and revokes, with the leader's own functions against its database
(published on `127.0.0.1:15432`): this stack has no identity provider to sign an
administrator in. `LEADER_IMAGE` and `FOLLOWER_IMAGE` replace the two images.

On a machine with an NVIDIA GPU the same scenario runs the `cuda` image, with `large-v3`:

```
docker build --build-arg MODELS=large-v3 -t swarmscribe-follower:cuda-large-v3 --target cuda -f docker/follower.Dockerfile .
CHECK_GPU=1 bash docker/check-follower-image.sh swarmscribe-follower:cuda-large-v3 cuda large-v3
uv run python e2e/follower-compose/run_e2e.py prepare
docker compose -f e2e/follower-compose/docker-compose.yml up -d
FOLLOWER_IMAGE=swarmscribe-follower:cuda-large-v3 uv run python e2e/follower-compose/run_e2e.py run --gpu
docker compose -f e2e/follower-compose/docker-compose.yml -f e2e/follower-compose/docker-compose.gpu.yml --profile followers down -v
```
````

- [ ] **Step 3: Read it as a stranger would**

Run each command block of "Follower images" that does not need a leader, exactly as written: the two `docker build` lines, `check-follower-image.sh` for both targets, and `docker run --rm swarmscribe-follower:cpu --help`. Expected: each works as the text beside it says. Fix the text, not the command, only if the text is what is wrong.

- [ ] **Step 4: Amend the spec**

These are for the owner to approve with this plan's review; they change the spec to what was measured and built. Append to `docs/superpowers/specs/2026-10-04-follower-design.md`:

````markdown

**Amendments after F2** (measured and built in plans F2a and F2b, 2026-10-05).
- **GPU libraries (D15, 8.1).** The `cuda` extra and image carry cuBLAS only
  (`nvidia-cublas-cu12`, and `nvidia-cuda-nvrtc-cu12` which it depends on). CTranslate2 4.8.2
  does not load cuDNN. `LD_LIBRARY_PATH` names the one wheel's `lib` folder. The image check
  loads `libcublas.so.12` by name.
- **The `cuda` image's device (8.1, 5.9).** The `cuda` image sets
  `SWARMSCRIBE_FOLLOWER_DEVICE=cuda`: without a usable GPU it exits 3 at start-up step 4
  where `auto` would use the CPU.
- **Image sizes (8.1).** Without a model: `cpu` 0.6 GB, `cuda` 1.7 GB.
- **Declared volumes (8.1).** The state folder and `/scratch` are both declared; `/models`
  is not (a volume there would copy a baked model on every start).
- **Baked models (5.10).** `MODELS` is a comma-separated list; its first name is the
  start-up model. Models come from Hugging Face at build time, each pinned to a commit and
  to a SHA-256 per file in `docker/models.lock.json`.
- **`doctor` (8.3).** `--no-leader` leaves the leader out; a `memory:` line says what the
  follower may use.
- **Health (9).** During a job the lease keeper must have run its loop within three
  heartbeat intervals *plus the time one request to the leader may take* (30 seconds): the
  keeper stamps its loop before it asks, and a leader that does not answer must not make
  `/healthz` fail.
- **Memory guard (5.7, D22, 8.2).** The measured figures replace the estimates:

  ```
  needed = what the process holds once the model is loaded (read, not looked up)
         + 100 MiB
         + duration in hours × 3600 MiB  (mono, or auto on anything but a stereo file)
         + duration in hours × 3900 MiB  (split)
  ```

  The limit is `SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB`, or when unset the smaller of the
  cgroup limit and the machine's physical memory. The chart's sizing guidance (8.2) becomes:
  the model's host memory (1.7 GB for `distil-large-v3` on a CPU; 3.5 GB to load `large-v3`
  onto a GPU) plus 3.9 GB per hour of the longest recording the pool will see.
- **Compose test (10).** The profile is set with the leader's `set_profile`, not in the
  database by hand. The test also covers the health listener, `/metrics` and the memory
  guard.
````

- [ ] **Step 5: Run the whole suite, commit and push**

Run: `uv run ruff check . && uv run pytest`
Expected: `All checks passed!` and no failure.

```bash
git add README.md docs/superpowers/specs/2026-10-04-follower-design.md
git commit -m "docs(follower): the two images, GPU prerequisites, baked models, sizing; spec amendments after F2"
git push
```

---

## For the owner

Not tasks; decisions and one finding that is larger than this plan.

- **A long recording needs far more memory than the specs assumed**: about 3.5 GiB per hour of speech, on a GPU as on a CPU, because faster-whisper computes the features of the whole recording in one piece. A three-hour recording needs 11 GiB beside the model. The guard of Task 3 turns the out-of-memory kill into a recorded refusal; it does not make the recording transcribable on a small pod. The fix belongs to the engine (feed the model in pieces of, say, ten minutes cut at silences) and is its own piece of work. Until then, size pools by their longest recording (the README's table).
- **Rulings marked (owner)** above: 1 (no cuDNN), 2 (`cuda` image asks for a GPU), 4 (the health allowance), 5 (the figures), 12 (CI builds the `cuda` image).
- **Not pushed to a registry**, as F2a.

## Self-Review

**Spec coverage.** D18 and spec 9 (listener, `/healthz`, the nine metrics): Tasks 1, 2, 4. Spec 5.2 step 2 and the F1 follow-up (listener before the model load): Task 2 (`test_run_listens_before_the_model_is_loaded_and_stops_listening_at_the_end`) and Task 4 (healthy with no leader). Spec 8.1's `HEALTHCHECK`: Task 4. D22 and spec 5.7 (guard, limit from the setting, the cgroup or the machine, unreadable duration skips, figures measured and in the README): Tasks 3, 4, 7. D15 and D16's `cuda` target: Task 5, with ruling 1's difference. R5 (the `cuda` image on the development machine's GPU, from a real leader, recorded; the fallback): Task 6. Spec 8.1's check script for `cuda`: Task 5 in CI. The README's five subjects (build, run, GPU prerequisites, baking, limits): Task 7.

**Where this plan differs from the spec**, each written into Task 7's amendments: no cuDNN; the `cuda` image's device; the health allowance; the guard's figures and where the model's memory comes from; two declared volumes (F2a).

**Not covered:** `SWARMSCRIBE_FOLLOWER_ON_DRAINED=park` and probes from a kubelet (F3); the Windows service's `tick` (F4); a Linux host with the NVIDIA Container Toolkit (only Docker Desktop on WSL 2 was run); `arm64`.

**Placeholders.** One, deliberate: the three "this run" lines of the outcomes document, which only the person running Task 6 can fill in. Everything else is given whole.

**Names.** `Metrics`, `watch`, `job_ended`, `transcribed`, `heartbeat_failed`, `downloaded`, `uploaded`, `model_loaded`, `render` (Task 1) are the ones `job.py`, `agent.py`, `main.py` and `health.py` call. `on_loaded` and `on_failure` are the keyword names in `ModelHost` and `LeaseKeeper` and in their callers. `keeper_stalled`, `tick`, `health`, `health_address`, `HealthServer` (Task 2) match between `job.py`, `agent.py`, `config.py`, `main.py` and the tests. `MemoryGuard`, `Limit`, `find_limit`, `job_mb`, `probe_recording` (Task 3) match between `memory.py`, `job.py`, `main.py`, the tests and Task 4's driver test. `health`, `metrics`, `write_silence`, `TOO_LONG` (Task 4) and `GPU`, `GPU_FILE`, `--gpu` (Task 6) are the driver's. The sample names the check script and the driver read (`swarmscribe_follower_state{state="idle"}`, `swarmscribe_follower_model_load_seconds`, `swarmscribe_follower_jobs_total{outcome="failed"}`) are what `metrics.py` renders.
