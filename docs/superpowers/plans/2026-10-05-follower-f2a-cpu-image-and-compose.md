# Follower F2a — CPU Image, Image Check and Compose Test Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the `swarmscribe-follower:cpu` container image, with models optionally baked in from a pinned and checksummed source, and prove it in GitHub Actions with a Compose test in which two real followers with a real model transcribe for a real leader and are killed, stopped, drained and revoked.

**Architecture:** One multi-stage Dockerfile installs the follower, the engine and the protocol package with `uv` and leaves a non-root, read-only-capable image whose only process is the follower as PID 1. A build argument bakes models from Hugging Face at build time, each pinned to one commit and one SHA-256 per file in `docker/models.lock.json`; a baked image is offline and never asks Hugging Face for anything. The Compose test runs that image on a network with no route out, against two leader replicas behind nginx, and is driven from the host with the leader's own functions, as the leader's own Compose test is.

**Tech Stack:** Python 3.12 in the image (`python:3.12-slim-bookworm`), uv 0.12.22, faster-whisper 1.2.1 on CTranslate2 4.8.2, huggingface-hub 1.33.0, Docker 29 and Docker Compose, nginx 1.27, Postgres 16, pytest, bash.

**Spec:** `docs/superpowers/specs/2026-10-04-follower-design.md` (sections 4.1, 5.2, 5.10, 7, 8.1, 10, 12.6, 13 and the "Amendments after the F1 final review"), with `docs/superpowers/plans/2026-10-04-follower-f1-followups.md` (section "F2").

**This plan is the first of two.** F2b (`2026-10-05-follower-f2b-cuda-health-and-memory.md`) adds `/metrics`, the health listener and the image's `HEALTHCHECK`, the memory guard, the `cuda` target and its run on a GPU, and the full README section. F2a works on its own: after it, `docker run swarmscribe-follower:cpu` joins a real leader, and CI proves the kill, stop, drain and revoke cases with a real model.

## Global Constraints

- "`packages/follower` → `swarmscribe-follower`, depending on `protocol` and `engine` only" (spec 2). "The image has no build tools, no `uv`, no leader package, and no `fastapi`" (spec 8.1).
- "`docker/follower.Dockerfile`, multi-stage, built from the repository root, following `docker/console.Dockerfile`: base images pinned by tag and digest, `uv sync --frozen --no-dev --package swarmscribe-follower --no-editable`, only `/app/.venv` copied into the final stage" (spec 8.1).
- "user `10001:10001`; `VOLUME` for the state folder; writable paths only `/var/lib/swarmscribe-follower`, `/scratch` and `/models`; `HOME` pointed at the state folder; `ENTRYPOINT ["swarmscribe-follower"]`, `CMD ["run"]`" (spec 8.1).
- Models (D13, ruling R3): "Fetched from Hugging Face into a cache folder by default; that stays the default for `docker run`. On Kubernetes models are baked into the image. Offline mode never touches the network".
- Baked models (spec 5.10): "The Dockerfile's `MODELS` build argument (for example `MODELS="large-v3"`) downloads the models at build time into `/models` in the image and sets `SWARMSCRIBE_FOLLOWER_OFFLINE=1`"; and the amendment: "F2's images set it [`SWARMSCRIBE_FOLLOWER_STARTUP_MODEL`] from the baked model (the image's `MODELS` argument)".
- "The join token and the credential are never logged, never passed as arguments, never baked into an image" (spec 7). "No audio, transcript text, link, join token or credential is ever written to a log" (spec 1). The test driver prints none of them either.
- `SWARMSCRIBE_LEADER_URL` "must be `https` unless `SWARMSCRIBE_FOLLOWER_ALLOW_HTTP=1` (development and Compose tests)" (spec 4.1).
- "The leader's `SWARMSCRIBE_PUBLIC_URL` must be the proxy's name on the Compose network, since followers fetch the leader's own links from inside containers" (spec 10, 12.6).
- Exit codes (spec 4.1): `0` stopped cleanly; `2` invalid configuration; `3` this machine cannot do the work; `4` not authorised; `5` protocol version refused.
- **No test seam in production paths.** Nothing in this plan adds a setting, flag or branch that exists for tests. `doctor --no-leader` (Task 1) is an operator's flag, for a machine that has no network yet.
- **The whole suite is the gate, not one package** (F1 follow-up C1). Every task ends with `uv run ruff check .` and `uv run pytest` over the whole repository. In CI the `test` job runs that suite on Ubuntu with Postgres and with `CI` set, where the follower's contract tests (`packages/follower/tests/test_real_leader.py`) fail rather than skip: that job is the Linux gate the follow-ups ask for, and this plan does not weaken it.
- Python `>=3.11`; `ruff` line length 100, rules `E, F, I, UP, B`.
- Docker is on the development machine (Engine 29.8.1, Linux containers). In Git Bash, first: `export PATH="$PATH:/c/Users/walla/AppData/Local/Programs/DockerDesktop/resources/bin"`. On that machine `uv` is run as `python -m uv`; the commands below are written `uv run ...`.
- **Never touch `C:\Users\walla\SwarmScribe-ui`, and never use ports 8900 or 8901**: another team works there. This plan publishes one host port, `127.0.0.1:15432`.
- Start from an up-to-date `main` that has PR #15 (`follower-f1`) merged, on a new branch `follower-f2a`. Other work happens in this repository at the same time: `README.md` and `.github/workflows/ci.yml` are shared files; expect to merge, and never discard a hunk you did not write.

## Measured before this plan was written

Everything in this plan was built and run on 2026-10-05 in a scratch folder outside the repository, from the `follower-f1` tree (Docker Desktop 29.8.1 on Windows 11, 28 logical CPUs, 16.6 GB for the Docker VM). The files below are the files that ran.

| What | Result |
|---|---|
| `swarmscribe-follower:cpu`, no model | 0.60 GB of layers (environment 453 MB, base 145 MB); `docker images` shows 787 MB |
| the same with `MODELS=tiny.en` | model layer 78 MB; `docker images` shows 936 MB |
| the same with `MODELS=distil-large-v3` | model layer 1.52 GB; `docker images` shows 3.7 GB |
| Build, nothing cached | 32 s without a model; tiny.en adds 6 s; distil-large-v3 adds 16 s |
| Build after a source change | 15 to 30 s; the model layer is not rebuilt |
| `doctor --no-leader`, baked tiny.en, `--read-only --network none` | ready in 1.5 s |
| `docker stop` during start-up (PID 1) | exit 0 in 0.4 s |
| `check-follower-image.sh`, baked image | 19 to 23 s |
| The fixture (5 s, split) on 2 cores, tiny.en int8 | 1.8 s including the model load |
| 5 and 10 minutes of audio (split) on 2 cores | 32 s and 59 s: about 10 times real time |
| The Compose scenario of Task 5 | passed 4 times in 97 to 106 s; a stop mid-job took 1.0 to 2.2 s |
| Libraries in the image | ctranslate2 4.8.2, faster-whisper 1.2.1, av 18.1.0, onnxruntime 1.30.0, huggingface-hub 1.33.0, numpy 2.5.3, tokenizers 0.23.2, httpx 0.28.1, pydantic 2.13.5, pydantic-settings 2.15.0 |

Not measured: anything on a GitHub runner. The scenario's waits are sized for a machine three times slower.

## Rulings

Decisions this plan makes. Those marked **(owner)** are the owner's to overturn; each has the plan's recommendation built in.

1. **Two plans.** F2 is twelve tasks; F2a is the CPU image, its check, the Compose test and CI, and F2b the rest. The Dockerfile's stages and the check script's arguments are named in F2a so that F2b only adds to them: the check script takes the build target (`cpu` or `cuda`) from the start and already holds the `cuda` checks, which F2a's CI never calls.
2. **The image check needs `doctor` without a leader, so `doctor` gains `--no-leader`.** Today `doctor` always asks the leader's `/healthz` and exits 1 when it does not answer, so no image check with `--network none` can end in `result: ready`. The flag is an operator's too (a machine set up before it has a network). There is no `--no-register`: `doctor` never registers.
3. **Baked models come from Hugging Face at build time, pinned to a commit and checked file by file.** `docker/models.lock.json` names, for each model, the repository, one commit and the SHA-256 of every file the loader reads; `docker/fetch_models.py` refuses a model that is not in it, and fails the build on a file that differs, is missing or is extra. No token is used: the three repositories are public. `fetch_models.py --pin NAME` prints a lock entry for a new model. **(owner)** The alternative, a mirror of our own, is right for an air-gapped *build*; nothing needs it yet.
4. **`MODELS` is comma-separated and its first name is the start-up model.** `ENV SWARMSCRIBE_FOLLOWER_STARTUP_MODEL=${MODELS%%,*}` and `SWARMSCRIBE_FOLLOWER_OFFLINE=${BAKED:-0}` with `ARG BAKED=${MODELS:+1}`: both follow from the one argument, so an image cannot be baked without being offline. `SWARMSCRIBE_FOLLOWER_ALLOWED_MODELS` is not set by the image: a baked image that is asked for another model is already told `D11` by the loader, with the reason.
5. **The loader's `refs/main` is written by hand.** faster-whisper asks the Hugging Face cache for the revision `main`; a download by commit does not record which commit that is, and without the file an offline load fails with `LocalEntryNotFoundError` (tried). The fetch script writes it after the checksums pass.
6. **Two declared volumes, not one: the state folder and `/scratch`.** The spec declares only the state folder. With `/scratch` declared too, `docker run --read-only` works without a flag, and a recording is never written into the container's own layer. `/models` is deliberately *not* declared: an anonymous volume there would copy a baked 3 GB model on every `docker run`. **(owner)**
7. **No `HEALTHCHECK` in F2a.** The spec's check is on `http://127.0.0.1:9108/healthz`, and the follower listens on no port until F2b. A check that only asked "is PID 1 alive" would say nothing Docker does not already know. F2b adds the listener, the `HEALTHCHECK` and the checks for them.
8. **The follower is PID 1, with no init and no shell.** F1 installs the signal handlers before anything slow; measured here in the image: `docker stop` during start-up exits 0 in 0.4 s. The one child it ever starts (`nvidia-smi`, awaited) leaves no zombie. `pip` of the base image stays in the final image, as in the console image; `uv`, compilers, `make`, `git` and `curl` are checked absent.
9. **The Compose leader is plain http behind nginx, and the followers set `SWARMSCRIBE_FOLLOWER_ALLOW_HTTP=1`.** The spec says so three times (4.1, 10, F1 follow-up I3), and it is how the leader really runs: it speaks http and TLS ends in front of it. The console's Compose test needed TLS only because the console refuses an http leader. What this leaves unproven by a container: `SWARMSCRIBE_LEADER_CA_FILE` and the https default, which stay unit-tested. **(owner)** Recommendation: prove them in F3's `kind` install, where an ingress exists.
10. **Administration is done by the driver with the leader's own functions, from the host**, as `e2e/compose/run_e2e.py` does: a join token (`create_join_token`), the CPU profile (`set_profile`, which did not exist when the spec said "directly in the database"), drain (`drain`) and revoke (`revoke_follower`). `swarmscribe-admin` needs a signed-in person; reusing the console test's stand-in for Entra ID would add an identity provider, a CA and TLS to a test that is about the follower, and the CLI is already proven there. The two locations are inserted as rows, because `add_location` checks the folder on the machine it runs on and `/data` exists only inside the leaders. Postgres is published on `127.0.0.1:15432` for this (not 5432, which the leader's Compose test publishes).
11. **The followers' network has no route out** (`internal: true`). A baked image that fetched anything at run time would fail the test, not pass it slowly.
12. **One join token with exactly two uses.** A follower that registered again after a restart would be refused (exit 4), so "the same follower came back" is proven by the token as well as by the log and the row count.
13. **Each follower gets two cores** (`cpus: 2`), so that a four-minute recording takes long enough on any machine to be killed in the middle and to outlast the 8-second lease (measured: 25 to 30 s here). The driver fails, saying so, if the job was too fast to prove it.
14. **A long recording is checked by its length and its words, not by counting phrases.** It is the fixture 48 times over, and models drop some repeats (large-v3 kept 4 of 48); the transcript's `duration` proves all of it was read.
15. **The scenario runs once per stack**, like the console's: it drains and revokes its followers. A second run stops at once and says to `down -v` first.
16. **CI does not cache the model.** tiny.en is 78 MB from a pinned commit (6 s). A Hugging Face outage fails the job; that is accepted until it happens twice. **(owner)**
17. **The image is built and tested in CI but not pushed**, like the console image. **(owner)** Recommendation: one follow-up that pushes both to `ghcr.io` on a version tag.

## Review Focus

Inputs and conditions the spec implies and that are most likely to bite a person running this, each pinned by a test in the task that owns the code:

1. **A baked image on a node with no route to Hugging Face** (the whole point of ruling R3): the model loads and runs with `--network none` and a read-only root; and in the Compose test the followers' network has no route out — Task 4, `check-follower-image.sh`; Task 5, `docker-compose.yml` and `test_the_followers_run_locked_down_and_cannot_reach_the_internet`.
2. **A model file that changed upstream, or a model that is not in the lock file**: the build fails and names the file; nothing unverified is baked, and `refs/main` is not written — Task 2, `test_a_file_that_differs_is_missing_or_is_extra_fails_the_build`, `test_a_model_that_is_not_in_the_lock_file_is_refused_before_anything_is_fetched`.
3. **`docker stop` while the follower is still starting** (a rollout that gives up on a pod): exit 0 inside the stop window, never 137 — Task 4, the PID 1 check.
4. **A container that is started again after it was killed** (`--restart`, a node reboot): the same follower, its credential reused and never a second registration, and nothing of the dead job left in scratch — Task 5, step 3 of the scenario, and the two-use token.
5. **An optional setting that arrives as an empty string** (Compose and Kubernetes pass unset variables that way; an unbaked image has `SWARMSCRIBE_FOLLOWER_STARTUP_MODEL=""`): it means unset — Task 4 (`doctor` on the unbaked image) and Task 5 (`SWARMSCRIBE_LEADER_CA_FILE: ""` on purpose).
6. **An image expected to be baked that is not, or the reverse**: the check script is told what to expect and fails on a mismatch — Task 4, step 3.

## File Structure

| File | Responsibility |
|---|---|
| `packages/follower/src/swarmscribe_follower/main.py` (modify) | `doctor --no-leader` |
| `packages/follower/tests/test_main.py` (modify) | its tests |
| `docker/models.lock.json` (new) | for each model that may be baked: repository, commit, SHA-256 per file |
| `docker/fetch_models.py` (new) | bake the models named in `MODELS` from the lock file; `--pin` prints a new entry |
| `packages/follower/tests/test_fetch_models.py` (new) | the fetcher and the lock file, without Docker or a network |
| `docker/follower.Dockerfile` (new) | the image, target `cpu` |
| `docker/check-follower-image.sh` (new) | what an image must hold, checked without a leader |
| `.dockerignore`, `.gitignore` (modify) | keep the Compose test's work folder out |
| `e2e/follower-compose/docker-compose.yml` (new) | Postgres, two leaders, nginx, two followers |
| `e2e/follower-compose/run_e2e.py` (new) | the recordings (`prepare`) and the scenario (`run`) |
| `packages/follower/tests/test_compose_driver.py` (new) | the driver's own parts, without Docker |
| `.github/workflows/ci.yml` (modify) | job `follower-compose-e2e` |
| `README.md` (modify) | `doctor` flags; "Follower image"; "Follower Compose test" |

---

### Task 1: `doctor --no-leader`

`doctor` always asks the leader's `/healthz` and exits 1 when nothing answers, so an image check that runs with `--network none` can never end in `result: ready`. This flag leaves the leader out, as `--no-model` leaves the model out.

**Files:**
- Modify: `packages/follower/src/swarmscribe_follower/main.py`
- Modify: `packages/follower/tests/test_main.py` (append)
- Modify: `README.md`

**Interfaces:**
- Consumes: `command_doctor(settings, out, *, host, client, load_model=True) -> int` (exists).
- Produces: `command_doctor(..., ask_leader: bool = True)`; with `ask_leader=False` it prints the line `leader: not checked (--no-leader)`, makes no request, and the leader no longer affects the exit code. `swarmscribe-follower doctor --no-leader` on the command line; it combines with `--no-model`. Task 4's script runs `doctor --no-leader` and `doctor --no-model --no-leader` and matches the lines `result: ready`, `model: <name> (<compute type>) loaded and ran` and `cached models: ...`.

- [ ] **Step 1: Write the failing tests**

Append to `packages/follower/tests/test_main.py`:

```python
def test_doctor_without_the_leader_asks_nothing_and_can_be_ready(engine, monkeypatch):
    def asked(request):
        raise AssertionError("doctor --no-leader asked the leader")

    monkeypatch.setattr(cli, "probe", lambda preference: Probe(CPU))
    out = io.StringIO()
    code = cli.command_doctor(
        Settings(),
        out,
        ask_leader=False,
        host=lambda device, **kw: ModelHost(device, factory=engine, **kw),
        client=lambda url, **kw: LeaderClient(url, transport=httpx.MockTransport(asked)),
    )
    lines = out.getvalue().splitlines()
    assert code == 0 and lines[-1] == "result: ready"
    assert "leader: not checked (--no-leader)" in lines
    assert "model: distil-large-v3 (int8) loaded and ran" in lines


def test_the_no_leader_and_no_model_flags_reach_doctor(monkeypatch):
    seen = {}

    def doctor(settings, out, **options):
        seen.update(options)
        return 0

    monkeypatch.setattr(cli, "command_doctor", doctor)
    assert cli.main(["doctor", "--no-leader"]) == 0
    assert seen == {"load_model": True, "ask_leader": False}
    assert cli.main(["doctor", "--no-model", "--no-leader"]) == 0
    assert seen == {"load_model": False, "ask_leader": False}
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/follower/tests/test_main.py -k "no_leader or without_the_leader" -v`
Expected: 2 failed. The first with `TypeError: command_doctor() got an unexpected keyword argument 'ask_leader'`; the second with `SystemExit: 2` (argparse: `unrecognized arguments: --no-leader`).

- [ ] **Step 3: Implement**

In `packages/follower/src/swarmscribe_follower/main.py`, four changes.

**1.** Replace

```python
    load_model: bool = True,
) -> int:
    """What start-up checks, said out loud, one line per check: settings, folders, device,
    the model with a real inference, the leader. Registers nothing and claims nothing, and
    prints no secret (no token, credential or link)."""
```

with

```python
    load_model: bool = True,
    ask_leader: bool = True,
) -> int:
    """What start-up checks, said out loud, one line per check: settings, folders, device,
    the model with a real inference, the leader. Registers nothing and claims nothing, and
    prints no secret (no token, credential or link). `ask_leader=False` leaves the leader
    out: a machine or an image can be checked where there is no network."""
```

**2.** Replace

```python
    leader = client(settings.leader_url, verify=tls(settings))
    try:
        if leader.healthy():
            print("leader: answers", file=out)
        else:
            print("leader: FAILED: no answer from its /healthz", file=out)
            code = code or EXIT_UNEXPECTED
    finally:
        leader.close()
```

with

```python
    if ask_leader:
        leader = client(settings.leader_url, verify=tls(settings))
        try:
            if leader.healthy():
                print("leader: answers", file=out)
            else:
                print("leader: FAILED: no answer from its /healthz", file=out)
                code = code or EXIT_UNEXPECTED
        finally:
            leader.close()
    else:
        print("leader: not checked (--no-leader)", file=out)
```

**3.** Replace

```python
        "--no-model", action="store_true", help="do not load the model or run the warm-up"
    )
```

with

```python
        "--no-model", action="store_true", help="do not load the model or run the warm-up"
    )
    doctor.add_argument(
        "--no-leader", action="store_true", help="do not ask the leader's /healthz"
    )
```

**4.** Replace

```python
        return command_doctor(settings, out, load_model=not args.no_model)
```

with

```python
        return command_doctor(
            settings, out, load_model=not args.no_model, ask_leader=not args.no_leader
        )
```

- [ ] **Step 4: Run the tests, then the whole suite**

Run: `uv run pytest packages/follower/tests/test_main.py -v`
Expected: all pass (36 tests).

Run: `uv run ruff check . && uv run pytest`
Expected: `All checks passed!` and no failure.

- [ ] **Step 5: Say so in the README**

In `README.md`, section "Run a follower (development)", in the `doctor` bullet, replace

```
  default (`distil-large-v3` on CPU); the model a job uses is the leader's
  profile for the device.
```

with

```
  default (`distil-large-v3` on CPU); the model a job uses is the leader's
  profile for the device. `--no-model` leaves the model out and `--no-leader`
  leaves the leader out, for a machine or an image checked before it has a
  network; what is left out is said (`not checked`), never counted as passed.
```

- [ ] **Step 6: Commit**

```bash
git add packages/follower/src/swarmscribe_follower/main.py packages/follower/tests/test_main.py README.md
git commit -m "feat(follower): doctor --no-leader, for a machine or an image without a network"
```

---

### Task 2: The model lock file and the fetcher

Models are baked from a pinned source: one commit of one repository, and one SHA-256 for every file the loader reads. This task writes the lock file (with the three models the project names: the two device defaults and the test model) and the script the Dockerfile will run.

**Files:**
- Create: `docker/models.lock.json`
- Create: `docker/fetch_models.py`
- Test: `packages/follower/tests/test_fetch_models.py`

**Interfaces:**
- Consumes: `huggingface_hub.snapshot_download(repo_id, *, revision, cache_dir, allow_patterns) -> str` (the installed 1.33.0); `faster_whisper.utils._MODELS` (name to repository, for `--pin` only).
- Produces: `python fetch_models.py --lock FILE --into FOLDER "name,name"`: exit 0 with the models in `FOLDER` as a Hugging Face cache (`models--Owner--name/snapshots/<commit>/...` and `refs/main`), or exit 2 naming a model that is not in the lock file, or `SystemExit` with a message naming the file that failed its check. An empty list makes an empty `FOLDER` and exits 0. `python fetch_models.py --pin NAME [--repo OWNER/NAME]` prints a lock entry as JSON. Functions the tests use: `names(text) -> list[str]`, `cache_folder(into, repo) -> Path`, `verify(snapshot, files) -> list[str]`, `fetch(name, entry, into) -> None`, `main(argv) -> int`. Task 3's Dockerfile runs the first form in a build stage.

- [ ] **Step 1: Write the failing tests**

Create `packages/follower/tests/test_fetch_models.py`:

```python
"""docker/fetch_models.py and docker/models.lock.json, checked without Docker or a network:
the script that bakes models into the follower image at build time."""

import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

DOCKER = Path(__file__).resolve().parents[3] / "docker"
LOCK = DOCKER / "models.lock.json"


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("fetch_models", DOCKER / "fetch_models.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["fetch_models"] = module
    spec.loader.exec_module(module)
    return module


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


FILES = {"config.json": b"{}", "model.bin": b"weights", "tokenizer.json": b"[]"}
ENTRY = {
    "repo": "Example/faster-whisper-test",
    "revision": "a" * 40,
    "files": {name: digest(data) for name, data in FILES.items()},
}


@pytest.fixture
def hub(tool, monkeypatch):
    """A stand-in for the Hugging Face download: writes `content` as the snapshot and records
    what was asked."""
    import huggingface_hub

    asked = []
    content = dict(FILES)

    def snapshot_download(repo, *, revision, cache_dir, allow_patterns):
        asked.append({"repo": repo, "revision": revision, "patterns": allow_patterns})
        snapshot = tool.cache_folder(Path(cache_dir), repo) / "snapshots" / revision
        snapshot.mkdir(parents=True)
        for name, data in content.items():
            (snapshot / name).write_bytes(data)
        return str(snapshot)

    monkeypatch.setattr(huggingface_hub, "snapshot_download", snapshot_download)
    return asked, content


def test_the_lock_file_pins_every_model_to_a_commit_and_a_checksum_per_file():
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    assert {"tiny.en", "distil-large-v3", "large-v3"} <= set(lock)
    for name, entry in lock.items():
        assert set(entry) == {"repo", "revision", "files"}, name
        assert re.fullmatch(r"[\w.-]+/[\w.-]+", entry["repo"]), name
        assert re.fullmatch(r"[0-9a-f]{40}", entry["revision"]), name  # a commit, not a branch
        assert {"config.json", "model.bin", "tokenizer.json"} <= set(entry["files"]), name
        for file, checksum in entry["files"].items():
            assert re.fullmatch(r"[0-9a-f]{64}", checksum), (name, file)


def test_the_names_the_follower_defaults_to_are_in_the_lock_file():
    from swarmscribe_engine import resolve_device

    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    assert resolve_device("cpu").model in lock
    assert resolve_device("cuda", cuda_available=lambda: True).model in lock


@pytest.mark.parametrize(
    ("given", "names"),
    [
        ("", []),
        ("tiny.en", ["tiny.en"]),
        ("large-v3,tiny.en", ["large-v3", "tiny.en"]),
        (" large-v3 , tiny.en ,large-v3", ["large-v3", "tiny.en"]),
        ("a b", ["a", "b"]),
    ],
)
def test_model_names_are_split_on_commas_and_spaces_in_order(tool, given, names):
    assert tool.names(given) == names


def test_a_model_is_downloaded_by_commit_checked_and_made_loadable_offline(tool, hub, tmp_path):
    asked, _content = hub
    tool.fetch("test", ENTRY, tmp_path)
    assert asked == [
        {"repo": ENTRY["repo"], "revision": "a" * 40, "patterns": sorted(FILES)}
    ]
    folder = tmp_path / "models--Example--faster-whisper-test"
    # The loader asks for `main`; a download by commit does not record which commit that is.
    assert (folder / "refs" / "main").read_text(encoding="utf-8") == "a" * 40
    assert (folder / "snapshots" / ("a" * 40) / "model.bin").read_bytes() == b"weights"


@pytest.mark.parametrize(
    ("change", "said"),
    [
        ({"model.bin": b"other weights"}, "model.bin has SHA-256"),
        ({"model.bin": None}, "model.bin is missing"),
        ({"extra.bin": b"more"}, "extra.bin is not in the lock file"),
    ],
)
def test_a_file_that_differs_is_missing_or_is_extra_fails_the_build(
    tool, hub, tmp_path, change, said
):
    _asked, content = hub
    for name, data in change.items():
        if data is None:
            del content[name]
        else:
            content[name] = data
    with pytest.raises(SystemExit) as stop:
        tool.fetch("test", ENTRY, tmp_path)
    assert said in str(stop.value) and "Example/faster-whisper-test@" in str(stop.value)
    assert not (tmp_path / "models--Example--faster-whisper-test" / "refs").exists()


def test_a_model_that_is_not_in_the_lock_file_is_refused_before_anything_is_fetched(
    tool, hub, tmp_path, capsys
):
    asked, _content = hub
    code = tool.main(["--lock", str(LOCK), "--into", str(tmp_path / "models"), "tiny.en,nope"])
    assert code == 2 and asked == []
    said = capsys.readouterr().err
    assert "nope" in said and "--pin" in said and "tiny.en" in said


def test_no_model_leaves_an_empty_folder_for_the_image_to_copy(tool, hub, tmp_path, capsys):
    asked, _content = hub
    into = tmp_path / "models"
    assert tool.main(["--lock", str(LOCK), "--into", str(into), ""]) == 0
    assert into.is_dir() and list(into.iterdir()) == [] and asked == []
    assert "no model baked" in capsys.readouterr().out


def test_the_models_asked_for_are_all_fetched_from_the_lock(tool, hub, tmp_path):
    asked, _content = hub
    lock = tmp_path / "lock.json"
    lock.write_text(json.dumps({"one": ENTRY, "two": {**ENTRY, "repo": "Example/two"}}))
    assert tool.main(["--lock", str(lock), "--into", str(tmp_path / "models"), "two,one"]) == 0
    assert [call["repo"] for call in asked] == ["Example/two", "Example/faster-whisper-test"]
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/follower/tests/test_fetch_models.py -v`
Expected: every test fails or errors with `FileNotFoundError` for `docker/models.lock.json` or `docker/fetch_models.py`.

- [ ] **Step 3: Write the lock file**

Create `docker/models.lock.json`. These are the commits and checksums downloaded and hashed on 2026-10-05; all three models loaded and transcribed from them.

```json
{
  "tiny.en": {
    "repo": "Systran/faster-whisper-tiny.en",
    "revision": "0d3d19a32d3338f10357c0889762bd8d64bbdeba",
    "files": {
      "config.json": "14b1b421a90349bc551b881461426b561a874049cb9e4c4864f2ca384f6a7cc5",
      "model.bin": "1a5afae06a4db91c975c9a9d78be5cc110ee4ea022ad57d55492e4550e936b2a",
      "tokenizer.json": "929c5252409436dce1b38a75d1abbcb5e132d170d8e324e4e04ed915fa2d22df",
      "vocabulary.txt": "ff77588746d3a2595d32ab5b69ffd7b95ce2441ac57533cb66fc3eb575a115cf"
    }
  },
  "distil-large-v3": {
    "repo": "Systran/faster-distil-whisper-large-v3",
    "revision": "c3058b475261292e64a0412df1d2681c06260fab",
    "files": {
      "config.json": "90c55f775cc4e0bb17293d0bf12f96557a486f20dea886fabd8e6075a3588b21",
      "model.bin": "b79368e19b6623813609431a6e5ee309a71506701ebc49fd7820e692dec7c5f5",
      "preprocessor_config.json": "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711",
      "tokenizer.json": "6d8cbd7cd0d8d5815e478dac67b85a26bbe77c1f5e0c6d76d1ce2abc0e5f21ca",
      "vocabulary.json": "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1"
    }
  },
  "large-v3": {
    "repo": "Systran/faster-whisper-large-v3",
    "revision": "edaa852ec7e145841d8ffdb056a99866b5f0a478",
    "files": {
      "config.json": "a9306624f5ec14270a014b647e5c316b6e03a662c369758d1b90697a7b0655b9",
      "model.bin": "69f74147e3334731bc3a76048724833325d2ec74642fb52620eda87352e3d4f1",
      "preprocessor_config.json": "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711",
      "tokenizer.json": "6d8cbd7cd0d8d5815e478dac67b85a26bbe77c1f5e0c6d76d1ce2abc0e5f21ca",
      "vocabulary.json": "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1"
    }
  }
}
```

- [ ] **Step 4: Write the fetcher**

Create `docker/fetch_models.py`:

```python
"""Bake Whisper models into the follower image, from a pinned source (follower spec 5.10).

    python fetch_models.py --lock models.lock.json --into /models tiny.en,large-v3
    python fetch_models.py --pin large-v3-turbo          # prints a lock entry to add

Only docker/follower.Dockerfile runs the first form, at BUILD time. Every model must be in
the lock file, which names its Hugging Face repository, one commit of it, and the SHA-256 of
every file the loader reads. A file that differs, is missing or is extra fails the build:
nothing unverified reaches an image. No token is read or sent: the models are public.

The result is a Hugging Face cache folder, the layout faster-whisper loads from with
HF_HUB_OFFLINE=1. `refs/main` is written by hand: the loader asks for the revision `main`,
and a download by commit does not record which commit that is.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

# What faster-whisper's own download asks for (faster_whisper/utils.py, download_model).
PATTERNS = [
    "config.json",
    "preprocessor_config.json",
    "model.bin",
    "tokenizer.json",
    "vocabulary.*",
]
CHUNK = 1024 * 1024


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def names(text: str) -> list[str]:
    """`tiny.en, large-v3` -> ["tiny.en", "large-v3"]; commas or spaces, order kept."""
    found: list[str] = []
    for name in text.replace(",", " ").split():
        if name not in found:
            found.append(name)
    return found


def cache_folder(into: Path, repo: str) -> Path:
    return into / ("models--" + repo.replace("/", "--"))


def verify(snapshot: Path, files: dict[str, str]) -> list[str]:
    """What is wrong with a downloaded snapshot, as sentences; empty when it is the lock's."""
    problems = []
    present = {p.name for p in snapshot.iterdir()} if snapshot.is_dir() else set()
    for name in sorted(set(files) - present):
        problems.append(f"{name} is missing")
    for name in sorted(present - set(files)):
        problems.append(f"{name} is not in the lock file")
    for name in sorted(set(files) & present):
        found = sha256(snapshot / name)
        if found != files[name]:
            problems.append(f"{name} has SHA-256 {found}, the lock file says {files[name]}")
    return problems


def fetch(name: str, entry: dict, into: Path) -> None:
    from huggingface_hub import snapshot_download

    repo, revision, files = entry["repo"], entry["revision"], entry["files"]
    snapshot = Path(
        snapshot_download(repo, revision=revision, cache_dir=into, allow_patterns=sorted(files))
    )
    problems = verify(snapshot, files)
    if problems:
        raise SystemExit(f"error: {name} ({repo}@{revision}): " + "; ".join(problems))
    refs = cache_folder(into, repo) / "refs"
    refs.mkdir(parents=True, exist_ok=True)
    (refs / "main").write_text(revision, encoding="utf-8")
    size = sum((snapshot / file).stat().st_size for file in files)
    print(f"baked {name}: {repo}@{revision[:12]}, {len(files)} files, {size // 1_000_000} MB")


def pin(name: str, repo: str | None) -> dict:
    """A lock entry for the repository's current `main`, with every file hashed."""
    import tempfile

    from huggingface_hub import snapshot_download

    if repo is None:
        from faster_whisper.utils import _MODELS

        repo = name if "/" in name else _MODELS[name]
    with tempfile.TemporaryDirectory() as folder:
        snapshot = Path(snapshot_download(repo, cache_dir=folder, allow_patterns=PATTERNS))
        files = {p.name: sha256(p) for p in sorted(snapshot.iterdir())}
        return {name: {"repo": repo, "revision": snapshot.name, "files": files}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("models", nargs="?", default="", help="names, comma-separated")
    parser.add_argument("--lock", type=Path, help="the lock file (models.lock.json)")
    parser.add_argument("--into", type=Path, help="the cache folder to fill")
    parser.add_argument("--pin", metavar="NAME", help="print a lock entry for this model")
    parser.add_argument("--repo", help="with --pin: the repository, when the name is not one")
    args = parser.parse_args(argv)
    if args.pin:
        print(json.dumps(pin(args.pin, args.repo), indent=2))
        return 0
    if args.lock is None or args.into is None:
        parser.error("--lock and --into are required")
    args.into.mkdir(parents=True, exist_ok=True)
    wanted = names(args.models)
    lock = json.loads(args.lock.read_text(encoding="utf-8"))
    unknown = [name for name in wanted if name not in lock]
    if unknown:
        print(
            f"error: not in {args.lock.name}: {', '.join(unknown)}."
            f" Known: {', '.join(sorted(lock))}."
            " Add one with `python docker/fetch_models.py --pin NAME`.",
            file=sys.stderr,
        )
        return 2
    for name in wanted:
        fetch(name, lock[name], args.into)
    if not wanted:
        print("no model baked: the follower downloads its model on first start")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run the tests, then the whole suite**

Run: `uv run pytest packages/follower/tests/test_fetch_models.py -v`
Expected: 14 passed.

Run: `uv run ruff check . && uv run pytest`
Expected: `All checks passed!` and no failure.

- [ ] **Step 6: Check the smallest entry against Hugging Face (needs a network)**

Run: `uv run python docker/fetch_models.py --pin tiny.en`
Expected: a JSON entry whose `revision` and four `files` checksums equal those of `tiny.en` in `docker/models.lock.json`. (Hugging Face warns on stderr that the request is unauthenticated; that is expected, no token is used.) If the revision differs, upstream has a new commit: keep the lock file as it is. The pinned commit is what the build downloads.

- [ ] **Step 7: Commit**

```bash
git add docker/models.lock.json docker/fetch_models.py packages/follower/tests/test_fetch_models.py
git commit -m "feat(follower): bake models from a pinned commit with a checksum per file"
```

---

### Task 3: The `swarmscribe-follower:cpu` image

**Files:**
- Create: `docker/follower.Dockerfile`
- Modify: `.dockerignore`, `.gitignore`

**Interfaces:**
- Consumes: `docker/fetch_models.py` and `docker/models.lock.json` (Task 2); the `swarmscribe-follower` console script; the settings `SWARMSCRIBE_FOLLOWER_STATE_DIR`, `_SCRATCH_DIR`, `_MODEL_DIR`, `_STARTUP_MODEL`, `_OFFLINE` (all exist; a blank `STARTUP_MODEL` means unset).
- Produces: build target `cpu` (the last stage, so also the default) and build argument `MODELS` (comma-separated names from the lock file; empty by default). Stages `manifests`, `deps-cpu`, `build-cpu`, `models`, `runtime`, `cpu`; F2b adds `build-cuda` and `cuda` beside them. The image: user `10001:10001`; `HOME` and the state folder `/var/lib/swarmscribe-follower`; scratch `/scratch`; models `/models`; volumes declared for the first two; `ENTRYPOINT ["swarmscribe-follower"]`, `CMD ["run"]`. With `MODELS=a,b`: both models in `/models`, `SWARMSCRIBE_FOLLOWER_STARTUP_MODEL=a`, `SWARMSCRIBE_FOLLOWER_OFFLINE=1`. Without: `/models` empty, the start-up model blank, `SWARMSCRIBE_FOLLOWER_OFFLINE=0`. Tasks 4 to 6 build `swarmscribe-follower:e2e` (`MODELS=tiny.en`) and `swarmscribe-follower:cpu` (no model) from it.

- [ ] **Step 1: Keep the Compose test's work folder out of builds and commits**

In `.dockerignore`, replace

```
e2e/console-compose/work
```

with

```
e2e/console-compose/work
e2e/follower-compose/work
```

In `.gitignore`, replace

```
e2e/console-compose/work/
```

with

```
e2e/console-compose/work/
e2e/follower-compose/work/
```

- [ ] **Step 2: Write the Dockerfile**

Create `docker/follower.Dockerfile`:

```dockerfile
# syntax=docker/dockerfile:1
# swarmscribe-follower: the agent that takes recordings from a leader and transcribes them
# (follower spec, section 8.1). Build from the repository root; `cpu` is the default target:
#
#   docker build -t swarmscribe-follower:cpu --target cpu -f docker/follower.Dockerfile .
#
# Without MODELS the follower downloads its model on first start into /models. With it, the
# models are downloaded now, checked against docker/models.lock.json, and the image never
# asks Hugging Face for anything (SWARMSCRIBE_FOLLOWER_OFFLINE=1); the first name is the
# start-up model:
#
#   docker build --build-arg MODELS=distil-large-v3 -t swarmscribe-follower:cpu-distil-large-v3 \
#     --target cpu -f docker/follower.Dockerfile .
#
# The base image is pinned by tag and digest, as in console.Dockerfile; move both together.

FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3 AS manifests
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
RUN pip install --no-cache-dir "uv==0.12.22"
WORKDIR /app
# Every workspace member's manifest must be present for uv to read the lock; only the
# follower and the two packages it imports (engine, protocol) are installed. The leader, the
# console and their database and web libraries never are: check-follower-image.sh proves it.
COPY pyproject.toml uv.lock ./
COPY packages/protocol/pyproject.toml packages/protocol/pyproject.toml
COPY packages/engine/pyproject.toml packages/engine/pyproject.toml
COPY packages/leader/pyproject.toml packages/leader/pyproject.toml
COPY packages/console/pyproject.toml packages/console/pyproject.toml
COPY packages/follower/pyproject.toml packages/follower/pyproject.toml

# --- the third-party libraries, without GPU libraries -------------------------------------
FROM manifests AS deps-cpu
RUN uv sync --frozen --no-dev --package swarmscribe-follower --no-install-workspace

# --- the Python environment ---------------------------------------------------------------
FROM deps-cpu AS build-cpu
COPY packages/protocol packages/protocol
COPY packages/engine packages/engine
COPY packages/follower packages/follower
# --no-editable: the three packages are installed as wheels, so the final image needs
# /app/.venv and nothing of the source tree. uv's lock file is created world-writable.
RUN uv sync --frozen --no-dev --package swarmscribe-follower --no-editable \
 && rm -f /app/.venv/.lock

# --- the baked models (an empty folder when MODELS is empty) ------------------------------
# From the libraries alone, not from the source: a code change does not download them again.
FROM deps-cpu AS models
ARG MODELS=""
RUN --mount=type=bind,source=docker/fetch_models.py,target=/fetch/fetch_models.py \
    --mount=type=bind,source=docker/models.lock.json,target=/fetch/models.lock.json \
    /app/.venv/bin/python /fetch/fetch_models.py \
      --lock /fetch/models.lock.json --into /models "${MODELS}"

# --- what both images share --------------------------------------------------------------
FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3 AS runtime
LABEL org.opencontainers.image.title="swarmscribe-follower" \
      org.opencontainers.image.description="SwarmScribe follower: transcribes recordings for a leader" \
      org.opencontainers.image.source="https://github.com/iamfatness/SwarmScribe"
# The state and scratch folders are the follower's alone (0700: the credential file is
# refused in a folder others can write to). /models is world-readable: it holds no secret.
RUN groupadd --gid 10001 follower \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /var/lib/swarmscribe-follower \
      --shell /usr/sbin/nologin follower \
 && install -d -o 10001 -g 10001 -m 0700 /var/lib/swarmscribe-follower /scratch \
 && install -d -o 10001 -g 10001 -m 0755 /models
ARG MODELS=""
# "1" when a model is baked, else empty: an ENV cannot hold a condition itself.
ARG BAKED=${MODELS:+1}
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/var/lib/swarmscribe-follower \
    SWARMSCRIBE_FOLLOWER_STATE_DIR=/var/lib/swarmscribe-follower \
    SWARMSCRIBE_FOLLOWER_SCRATCH_DIR=/scratch \
    SWARMSCRIBE_FOLLOWER_MODEL_DIR=/models \
    SWARMSCRIBE_FOLLOWER_STARTUP_MODEL=${MODELS%%,*} \
    SWARMSCRIBE_FOLLOWER_OFFLINE=${BAKED:-0}
# Before the environment: a code change does not move a 3 GB model layer.
COPY --from=models --chown=10001:10001 /models /models
WORKDIR /var/lib/swarmscribe-follower
USER 10001:10001
# Declared, so that `docker run --read-only` works as it is and neither the credential nor a
# recording is ever written into the container's own layer. /models is not declared: a
# volume there would copy a baked model on every start. Mount one to keep downloads.
VOLUME ["/var/lib/swarmscribe-follower", "/scratch"]
# No HEALTHCHECK yet: the follower listens on no port. Plan F2b adds the listener and it.
# The follower is PID 1 and handles SIGTERM itself: no shell and no init in between.
STOPSIGNAL SIGTERM
ENTRYPOINT ["swarmscribe-follower"]
CMD ["run"]

# --- swarmscribe-follower:cpu ---------------------------------------------------------------
FROM runtime AS cpu
COPY --from=build-cpu /app/.venv /app/.venv
```

What is deliberate here, so that a later change does not undo it:

- **`models` is built from `deps-cpu`, not from `build-cpu`.** It needs `huggingface_hub`, which the third-party libraries bring. Built from the stage that holds our source, every code change would download the models again.
- **The model layer comes before the environment in the final image**, so a code change moves 450 MB and not the model.
- **`COPY --from=models` keeps the cache's symbolic links.** `snapshots/<commit>/model.bin` is a link to `blobs/<sha>`; the model is in the image once.
- **`/models` is owned by 10001** so that an image without a baked model can download into it; in a baked image with `--read-only` nothing writes there (checked in Task 4).
- **`ARG BAKED=${MODELS:+1}`** needs the `# syntax=docker/dockerfile:1` line: the `${VAR%%pattern}` and `${VAR:+word}` forms come with the current Dockerfile syntax.

- [ ] **Step 3: Build both images**

```bash
docker build --build-arg MODELS=tiny.en -t swarmscribe-follower:e2e --target cpu -f docker/follower.Dockerfile .
docker build -t swarmscribe-follower:cpu --target cpu -f docker/follower.Dockerfile .
```

Expected: both succeed. The first prints `baked tiny.en: Systran/faster-whisper-tiny.en@0d3d19a32d33, 4 files, 78 MB` in its `models` stage, the second `no model baked: the follower downloads its model on first start`. Measured: 32 s with nothing cached, 6 s more for tiny.en.

- [ ] **Step 4: Try the baked image by hand, with no network and a read-only root**

```bash
docker run --rm --read-only --cap-drop ALL --network none \
  -e SWARMSCRIBE_LEADER_URL=https://leader.invalid swarmscribe-follower:e2e doctor --no-leader
```

Expected (the two JSON log lines of the model load, on stderr, are left out here), exit 0:

```
swarmscribe-follower 0.1.0
settings: ok (leader https://leader.invalid, pool default)
state folder: ok (/var/lib/swarmscribe-follower, ... GiB free)
scratch folder: ok (/scratch, ... GiB free)
device: cpu
cached models: tiny.en
model: tiny.en (int8) loaded and ran
leader: not checked (--no-leader)
joined: no
result: ready
```

And the image without a model says what it would need, when it may not download it:

```bash
docker run --rm --read-only --network none -e SWARMSCRIBE_LEADER_URL=https://leader.invalid \
  -e SWARMSCRIBE_FOLLOWER_OFFLINE=1 swarmscribe-follower:cpu doctor --no-leader
```

Expected: `cached models: (none)`, a line `model: FAILED: distil-large-v3 (int8, cpu): LocalEntryNotFoundError: ...`, and `result: NOT READY (exit 3)`.

- [ ] **Step 5: Run the whole suite and commit**

Run: `uv run ruff check . && uv run pytest`
Expected: `All checks passed!` and no failure.

```bash
git add docker/follower.Dockerfile .dockerignore .gitignore
git commit -m "feat(follower): the swarmscribe-follower:cpu image, with models baked on request"
```

---

### Task 4: `docker/check-follower-image.sh`

What an image must hold and must not hold, checked without a leader, without a GPU and, for everything that runs the follower, without a network. The Compose test (Task 5) proves the rest by using the image.

**Files:**
- Create: `docker/check-follower-image.sh`

**Interfaces:**
- Consumes: an image built from `docker/follower.Dockerfile` (Task 3); `doctor --no-leader` and `doctor --no-model` (Task 1); the log line `model <name> (<compute type>) loaded on <device>` (exists, `models.py`).
- Produces: `bash docker/check-follower-image.sh <image> <cpu|cuda> [baked-model]`: prints `ok: <image> is a <target> follower[ with <model> baked in] (<n> MB)` and exits 0, or prints `FAILED: <what>` on stderr and exits 1. With `CHECK_GPU=1` and target `cuda` it also runs a baked image on the machine's GPU (F2b uses that; never CI). The `cuda` checks are written now and first used by F2b, which builds that target.

- [ ] **Step 1: Write the script**

Create `docker/check-follower-image.sh`:

```bash
#!/usr/bin/env bash
# What a swarmscribe-follower image must hold, checked without a leader and without a GPU:
#
#   bash docker/check-follower-image.sh swarmscribe-follower:e2e cpu tiny.en
#   bash docker/check-follower-image.sh swarmscribe-follower:cuda cuda
#
# The second argument is the build target; the third is the first model baked in with
# MODELS, when there is one. CHECK_GPU=1 also runs a baked `cuda` image on this machine's
# GPU (never in CI). The Compose test (e2e/follower-compose) checks the rest by running it.
set -euo pipefail
# Git Bash on Windows would rewrite /scratch and friends into Windows paths.
export MSYS_NO_PATHCONV=1

image="${1:?usage: check-follower-image.sh <image> <cpu|cuda> [baked-model]}"
target="${2:?usage: check-follower-image.sh <image> <cpu|cuda> [baked-model]}"
baked="${3:-}"
state=/var/lib/swarmscribe-follower
# A leader that is never reached: every check here runs with --network none.
leader=(-e SWARMSCRIBE_LEADER_URL=https://leader.invalid)
locked=(--read-only --cap-drop ALL --security-opt no-new-privileges --network none)

fail() {
  echo "FAILED: $*" >&2
  exit 1
}

case "$target" in cpu | cuda) ;; *) fail "the target is '$target', not cpu or cuda" ;; esac

setting() {  # the value of one variable in the image's environment
  docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$image" \
    | sed -n "s/^$1=//p"
}

# --- who it runs as, and where it may write --------------------------------------------
user="$(docker inspect --format '{{.Config.User}}' "$image")"
[ "$user" = "10001:10001" ] || fail "the image's user is '$user', not 10001:10001"
[ "$(docker run --rm --entrypoint id "$image" -u)" = "10001" ] \
  || fail "the container does not run as uid 10001"

volumes="$(docker inspect --format '{{range $path, $_ := .Config.Volumes}}{{println $path}}{{end}}' "$image" | sort | xargs)"
[ "$volumes" = "/scratch $state" ] \
  || fail "the declared volumes are '$volumes', not /scratch and $state"

[ "$(setting HOME)" = "$state" ] || fail "HOME is not the state folder"
[ "$(setting SWARMSCRIBE_FOLLOWER_STATE_DIR)" = "$state" ] || fail "the state folder is not set"
[ "$(setting SWARMSCRIBE_FOLLOWER_SCRATCH_DIR)" = "/scratch" ] || fail "the scratch folder is not set"
[ "$(setting SWARMSCRIBE_FOLLOWER_MODEL_DIR)" = "/models" ] || fail "the model folder is not set"

# The credential is refused in a folder that others can write to, or that is not its own.
docker run --rm --entrypoint sh "$image" -c \
  'test "$(stat -c "%u:%g %a" /var/lib/swarmscribe-follower)" = "10001:10001 700" \
   && test "$(stat -c "%u:%g %a" /scratch)" = "10001:10001 700" \
   && test -z "$(find /var/lib/swarmscribe-follower /scratch -mindepth 1)"' \
  || fail "the state and scratch folders are not empty, 0700 and owned by 10001"

# --- what is in it, and what is not ------------------------------------------------------
docker run --rm --entrypoint python "$image" -c '
import swarmscribe_engine, swarmscribe_follower, swarmscribe_protocol  # noqa: F401
import av, ctranslate2, faster_whisper  # noqa: F401
' || fail "the follower, the engine or a model library does not import"

# The follower imports the engine and the protocol and nothing else internal (master spec,
# section 4): no leader, no console, none of their web and database libraries.
docker run --rm --entrypoint python "$image" -c '
import importlib.util
import sys

banned = ("swarmscribe_leader", "swarmscribe_console", "fastapi", "starlette", "uvicorn",
          "sqlalchemy", "asyncpg", "alembic", "jwt", "cryptography", "torch", "pytest")
found = [name for name in banned if importlib.util.find_spec(name)]
sys.exit(f"leader, console or test libraries in the image: {found}" if found else 0)
' || fail "the image carries the leader, the console or their libraries"

for tool in uv gcc cc make git curl; do
  if docker run --rm --entrypoint sh "$image" -c "command -v $tool" >/dev/null; then
    fail "a build tool ($tool) is in the final image"
  fi
done

# No secret is baked in: no token, credential or leader in the environment, and no file
# that looks like one (the state folder was checked empty above).
if docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$image" \
  | grep -E '^(SWARMSCRIBE_JOIN_TOKEN|SWARMSCRIBE_JOIN_TOKEN_FILE|SWARMSCRIBE_LEADER_URL|HF_TOKEN|HUGGING_FACE_HUB_TOKEN)='; then
  fail "the image's environment holds a token or a leader"
fi
if docker run --rm --entrypoint sh "$image" -c \
  'find / -xdev \( -name credential.json -o -name "*.env" -o -name token \) -not -path "/proc/*" 2>/dev/null | grep .'; then
  fail "the image holds a file that looks like a secret"
fi

# --- how it starts -------------------------------------------------------------------------
# Read-only root filesystem, no network, no configuration: it names what is missing and
# exits 2, without a traceback.
status=0
output="$(docker run --rm "${locked[@]}" "$image" 2>&1)" || status=$?
[ "$status" = "2" ] || fail "run without configuration exited $status, not 2"
echo "$output" | grep -q 'invalid configuration' \
  || fail "run without configuration did not say so: $output"
if echo "$output" | grep -q 'Traceback'; then
  fail "run without configuration ended in a traceback"
fi

# The three writable paths as tmpfs, as a chart would give them: the state folder needs its
# owner and mode said (a plain tmpfs is root's and world-writable, and is refused).
docker run --rm "${locked[@]}" "${leader[@]}" -e SWARMSCRIBE_FOLLOWER_DEVICE=cpu \
  --tmpfs "$state:uid=10001,gid=10001,mode=0700" \
  --tmpfs /scratch:uid=10001,gid=10001,mode=0700 \
  --tmpfs /models:uid=10001,gid=10001,mode=0755 \
  "$image" doctor --no-model --no-leader | grep -q '^result: ready$' \
  || fail "doctor does not pass on a read-only root with tmpfs on the three writable paths"

# --- the device ------------------------------------------------------------------------------
if [ "$target" = "cuda" ]; then
  [ "$(setting SWARMSCRIBE_FOLLOWER_DEVICE)" = "cuda" ] || fail "the cuda image does not ask for cuda"
  # The library the locked CTranslate2 loads by name at the first inference. It needs no
  # GPU to load, so this is checked everywhere; that it WORKS is checked on a GPU only.
  docker run --rm --entrypoint python "$image" -c 'import ctypes; ctypes.CDLL("libcublas.so.12")' \
    || fail "libcublas.so.12 does not load by name"
  # Without a GPU the cuda image must say so, not fall back to the CPU.
  status=0
  output="$(docker run --rm "${locked[@]}" "${leader[@]}" "$image" doctor --no-leader 2>&1)" || status=$?
  [ "$status" = "3" ] || fail "the cuda image without a GPU exited $status, not 3: $output"
  echo "$output" | grep -q 'cuda was requested' || fail "the cuda image did not name the device"
else
  [ -z "$(setting SWARMSCRIBE_FOLLOWER_DEVICE)" ] || fail "the cpu image sets a device"
  if docker run --rm --entrypoint python "$image" -c 'import ctypes; ctypes.CDLL("libcublas.so.12")' 2>/dev/null; then
    fail "the cpu image carries cuBLAS"
  fi
fi

# --- the model -------------------------------------------------------------------------------
if [ -z "$baked" ]; then
  [ -z "$(setting SWARMSCRIBE_FOLLOWER_STARTUP_MODEL)" ] || fail "a start-up model is set without a baked model"
  [ "$(setting SWARMSCRIBE_FOLLOWER_OFFLINE)" = "0" ] || fail "offline mode is on without a baked model"
  docker run --rm --entrypoint sh "$image" -c 'test -z "$(ls -A /models)"' \
    || fail "/models is not empty in an image without a baked model"
else
  [ "$(setting SWARMSCRIBE_FOLLOWER_STARTUP_MODEL)" = "$baked" ] \
    || fail "the start-up model is '$(setting SWARMSCRIBE_FOLLOWER_STARTUP_MODEL)', not $baked"
  [ "$(setting SWARMSCRIBE_FOLLOWER_OFFLINE)" = "1" ] || fail "a baked image is not offline"
  docker run --rm "${locked[@]}" "${leader[@]}" -e SWARMSCRIBE_FOLLOWER_DEVICE=cpu \
    "$image" doctor --no-model --no-leader | grep -E -q "^cached models: (.*, )?$baked(,|\$)" \
    || fail "the baked model $baked is not in /models"

  gpu=()
  if [ "$target" = "cuda" ]; then
    gpu=(--gpus all)
  fi
  if [ "$target" = "cpu" ] || [ "${CHECK_GPU:-0}" = "1" ]; then
    # The model loads and runs a real inference with no network at all and nothing
    # writable but the two declared volumes: nothing is fetched at run time.
    output="$(docker run --rm "${locked[@]}" "${gpu[@]}" "${leader[@]}" "$image" doctor --no-leader 2>&1)" \
      || fail "doctor failed offline: $output"
    echo "$output" | grep -q "^model: $baked (.*) loaded and ran\$" \
      || fail "the baked model did not load and run offline: $output"
    echo "$output" | grep -q '^result: ready$' || fail "doctor is not ready: $output"
    if [ "$target" = "cuda" ]; then
      echo "$output" | grep -q '^device: cuda (' || fail "doctor did not run on the GPU: $output"
    fi

    # PID 1: `docker stop` during start-up ends it with exit 0, well inside Docker's 10 s.
    # With no network the registration is retried for ever, which is where it is stopped.
    name="follower-check-$$"
    trap 'docker rm -f "$name" >/dev/null 2>&1 || true' EXIT
    docker run -d --name "$name" "${locked[@]}" "${gpu[@]}" "${leader[@]}" \
      -e SWARMSCRIBE_JOIN_TOKEN=not-a-token "$image" >/dev/null
    for _ in $(seq 1 60); do
      docker logs "$name" 2>&1 | grep -q 'loaded on' && break
      sleep 1
    done
    docker logs "$name" 2>&1 | grep -q 'loaded on' || fail "the follower never loaded its model"
    begun="$(date +%s)"
    docker stop --time 10 "$name" >/dev/null
    took="$(($(date +%s) - begun))"
    status="$(docker inspect --format '{{.State.ExitCode}}' "$name")"
    [ "$status" = "0" ] || fail "docker stop during start-up exited $status, not 0 (137 is a kill)"
    [ "$took" -le 5 ] || fail "docker stop during start-up took $took s"
    if docker logs "$name" 2>&1 | grep -q 'not-a-token'; then
      fail "the join token is in the log"
    fi
  fi
fi

size="$(docker image inspect --format '{{.Size}}' "$image")"
echo "ok: $image is a $target follower${baked:+ with $baked baked in} ($((size / 1000000)) MB)"
```

- [ ] **Step 2: Run it on both images**

```bash
bash docker/check-follower-image.sh swarmscribe-follower:e2e cpu tiny.en
bash docker/check-follower-image.sh swarmscribe-follower:cpu cpu
```

Expected (the sizes are what `docker image inspect` reports; measured here):

```
ok: swarmscribe-follower:e2e is a cpu follower with tiny.en baked in (936 MB)
ok: swarmscribe-follower:cpu is a cpu follower (787 MB)
```

The first takes about 20 s: it loads the model twice and stops a follower.

- [ ] **Step 3: See it refuse an image that is not what it was told**

```bash
bash docker/check-follower-image.sh swarmscribe-follower:e2e cpu; echo "exit $?"
bash docker/check-follower-image.sh swarmscribe-follower:cpu cpu tiny.en; echo "exit $?"
bash docker/check-follower-image.sh swarmscribe-follower:e2e cuda tiny.en; echo "exit $?"
```

Expected, each with `exit 1`:

```
FAILED: a start-up model is set without a baked model
FAILED: the start-up model is '', not tiny.en
FAILED: the cuda image does not ask for cuda
```

Then `docker ps -a --filter name=follower-check` shows nothing: the script removes the one container it starts.

- [ ] **Step 4: Commit**

```bash
git add docker/check-follower-image.sh
git commit -m "test(follower): check what the follower image holds, offline and read-only"
```

---

### Task 5: The Compose test

Two followers from the real image, a real model, a real leader. The driver runs on the host, as the leader's own Compose driver does: it needs the leader's package (it is in the development environment) and the leader's Postgres, published on `127.0.0.1:15432`.

**Files:**
- Create: `e2e/follower-compose/docker-compose.yml`
- Create: `e2e/follower-compose/run_e2e.py`
- Test: `packages/follower/tests/test_compose_driver.py`

**Interfaces:**
- Consumes: the images `swarmscribe-leader:e2e` (`e2e/compose/Dockerfile`, unchanged) and `swarmscribe-follower:e2e` (Task 3, `MODELS=tiny.en`); `e2e/compose/nginx.conf` (unchanged: two upstreams named `leader-1` and `leader-2`); `packages/engine/tests/fixtures/stereo_speech.wav`; from the leader: `create_join_token(session, *, pool, expires_at, max_uses, created_by) -> (JoinToken, str)`, `drain(session, follower_id, *, actor)`, `revoke_follower(session, follower_id, *, now, actor)` in `swarmscribe_leader.auth.followers`; `set_profile(session, device, *, model, compute_type, temperatures, actor)` in `swarmscribe_leader.profiles`; `make_engine`, `make_sessionmaker` in `swarmscribe_leader.db.session`; the models `Follower`, `Job`, `JobAttempt`, `Recording`, `StorageLocation`; `swarmscribe_protocol.SegmentsDocument`; the follower's JSON log line with `"event": "registered"` and `"follower_id"`.
- Produces: `python e2e/follower-compose/run_e2e.py prepare` (writes `e2e/follower-compose/work/`: `data/calls`, `data/talks`, `secrets/`) and `python e2e/follower-compose/run_e2e.py run` (exit 0 and a line starting `passed (tiny.en on cpu):`, or exit 1 and `FAILED: <what>`). The Compose project `swarmscribe-follower-e2e`; the images can be replaced with the environment variables `LEADER_IMAGE` and `FOLLOWER_IMAGE`. In the driver, `Target` (device, model, compute type, Compose files, `long_repeats`), `CPU` and the module variable `target`: F2b adds a `GPU` target and `run --gpu`, and more steps to the scenario. Task 6's CI job runs both commands.

- [ ] **Step 1: Write the failing tests of the driver's own parts**

Create `packages/follower/tests/test_compose_driver.py`:

```python
"""The follower Compose test's own parts (e2e/follower-compose), checked without Docker:
what the driver writes before the stack starts, how it reads a follower's log, what it
accepts as a transcript, and that docker-compose.yml and run_e2e.py agree on the values
they share."""

# ruff: noqa: E402
import importlib.util
import json
import sys
import wave
from dataclasses import replace
from pathlib import Path

import pytest

# The driver administers a real leader with the leader's own functions.
pytest.importorskip("swarmscribe_leader")
yaml = pytest.importorskip("yaml")

from swarmscribe_engine import Segment, TranscribeSettings, Transcript, Word, write_outputs

COMPOSE = Path(__file__).resolve().parents[3] / "e2e" / "follower-compose"


@pytest.fixture(scope="module")
def loaded():
    spec = importlib.util.spec_from_file_location("follower_compose_driver", COMPOSE / "run_e2e.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["follower_compose_driver"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def driver(loaded, tmp_path, monkeypatch):
    """The driver, writing under a temporary folder instead of e2e/follower-compose/work."""
    work = tmp_path / "work"
    monkeypatch.setattr(loaded, "WORK", work)
    monkeypatch.setattr(loaded, "DATA", work / "data")
    monkeypatch.setattr(loaded, "SECRETS", work / "secrets")
    return loaded


def frames(path: Path) -> int:
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes()


def test_prepare_writes_two_locations_with_consent_and_an_unconsented_recording(driver):
    driver.prepare()
    calls, talks = driver.DATA / "calls", driver.DATA / "talks"
    assert (calls / "consent.txt").read_bytes() == b"day*/*.wav\nlong/*.wav\n"
    assert (talks / "consent.txt").read_bytes() == b"*.wav\n"
    written = sorted(p.relative_to(driver.DATA).as_posix() for p in driver.DATA.rglob("*.wav"))
    assert written == [
        "calls/day1/call-1.wav",
        "calls/day1/call-2.wav",
        "calls/day1/call-3.wav",
        "calls/day1/call-4.wav",
        "calls/private/held.wav",
        "talks/talk-1.wav",
        "talks/talk-2.wav",
    ]
    assert all(frames(driver.DATA / name) == frames(driver.FIXTURE) for name in written)
    assert driver.SECRETS.is_dir() and list(driver.SECRETS.iterdir()) == []


def test_prepare_starts_from_nothing_every_time(driver):
    driver.prepare()
    stale = driver.DATA / "calls" / "transcripts" / "old.txt"
    stale.parent.mkdir(parents=True)
    stale.write_text("from the last run")
    (driver.SECRETS / "join-token").write_text("an old token")
    driver.prepare()
    assert not stale.exists() and list(driver.SECRETS.iterdir()) == []


def test_the_unconsented_recording_matches_no_line_of_its_consent_file(driver):
    from fnmatch import fnmatchcase

    patterns = driver.CONSENT[driver.CALLS].split()
    assert not any(fnmatchcase(driver.UNCONSENTED, pattern) for pattern in patterns)
    for key in (*driver.SHORT[driver.CALLS], driver.KILLED, driver.STOPPED, driver.AFTER_DRAIN):
        assert any(fnmatchcase(key, pattern) for pattern in patterns), key


def test_a_long_recording_is_the_fixture_repeated_and_never_seen_half_written(driver, tmp_path):
    target = tmp_path / "long" / "four.wav"
    driver.write_recording(target, 4)
    assert frames(target) == 4 * frames(driver.FIXTURE)
    assert [p.name for p in target.parent.iterdir()] == ["four.wav"]  # no .part left behind
    assert driver.fixture_seconds() == pytest.approx(5.04, abs=0.01)


def test_registrations_are_read_from_the_json_lines_of_a_log(driver, monkeypatch):
    log = "\n".join(
        [
            json.dumps({"level": "info", "message": "model tiny.en (int8) loaded on cpu"}),
            "error: a line that is not JSON",
            json.dumps({"event": "registered", "follower_id": "id-1"}),
            json.dumps({"event": "job.claimed", "job_id": "j"}),
            "[1, 2, 3]",
            json.dumps({"event": "registered", "follower_id": "id-2"}),
        ]
    )
    monkeypatch.setattr(driver, "logs", lambda service: log)
    assert driver.registrations("follower-1") == ["id-1", "id-2"]


def split_transcript(model="tiny.en", device="cpu", swap=False) -> Transcript:
    def segment(start, text, channel):
        word = Word(start=start, end=start + 1, word=" " + text, probability=0.9)
        return Segment(start=start, end=start + 2, text=text, words=(word,), channel=channel)

    left, right = "The weather today is clear.", "Please send the quarterly report."
    if swap:
        left, right = right, left
    settings = TranscribeSettings(
        model=model,
        compute_type="int8",
        device=device,
        channel_mode="stereo_split",
        channel_labels=("Agent", "Customer"),
    )
    return Transcript(
        source_name="call-1.wav",
        source_checksum="0" * 64,
        duration=5.04,
        settings=settings,
        vocabulary_version=0,
        vocabulary_terms_used=(),
        corrections_applied=(),
        segments=(segment(0.5, left, 0), segment(3.0, right, 1)),
        channel_labels=("Agent", "Customer"),
    )


def store(driver, location, key, made: Transcript) -> None:
    folder = driver.transcript(location, key, "txt").parent
    write_outputs(replace(made, source_name=Path(key).name), folder)


def test_a_split_transcript_with_each_phrase_on_its_own_channel_is_accepted(driver):
    store(driver, driver.CALLS, "day1/call-1.wav", split_transcript())
    driver.check_outputs(driver.CALLS, "day1/call-1.wav")


@pytest.mark.parametrize(
    ("made", "said"),
    [
        (split_transcript(swap=True), "the left channel is wrong"),
        (split_transcript(model="distil-large-v3"), "transcribed with distil-large-v3"),
        (split_transcript(device="cuda"), "transcribed on cuda"),
        (replace(split_transcript(), duration=2.0), "its transcript covers 2 s"),
    ],
)
def test_a_wrong_transcript_is_refused_with_what_is_wrong(driver, made, said):
    store(driver, driver.CALLS, "day1/call-1.wav", made)
    with pytest.raises(AssertionError, match=said):
        driver.check_outputs(driver.CALLS, "day1/call-1.wav")


def test_a_missing_output_is_named(driver):
    with pytest.raises(AssertionError, match=r"calls/day1/call-1.wav has no .txt"):
        driver.check_outputs(driver.CALLS, "day1/call-1.wav")


def test_a_split_transcript_in_the_mono_location_is_refused(driver):
    store(driver, driver.TALKS, "talk-1.wav", split_transcript())
    with pytest.raises(AssertionError, match="was split in the mono location"):
        driver.check_outputs(driver.TALKS, "talk-1.wav")


def test_the_compose_file_and_the_driver_agree(loaded):
    compose = yaml.safe_load((COMPOSE / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    assert set(loaded.FOLLOWERS) <= set(services)
    leader = services["leader-1"]["environment"]
    assert float(leader["SWARMSCRIBE_LEASE_SECONDS"]) == loaded.LEASE_SECONDS
    # The followers reach the leader, and the leader's own links, at the proxy's name.
    follower = services["follower-1"]
    assert leader["SWARMSCRIBE_PUBLIC_URL"] == follower["environment"]["SWARMSCRIBE_LEADER_URL"]
    assert leader["SWARMSCRIBE_PUBLIC_URL"] == "http://proxy" and "proxy" in services
    assert services["postgres"]["ports"] == ["127.0.0.1:15432:5432"]
    assert "@127.0.0.1:15432/" in loaded.DATABASE_URL
    assert follower["environment"]["SWARMSCRIBE_JOIN_TOKEN_FILE"] == "/run/secrets/join-token"
    assert "./work/secrets:/run/secrets:ro" in follower["volumes"]
    assert "./work/data:/data" in services["leader-1"]["volumes"]


def test_the_followers_run_locked_down_and_cannot_reach_the_internet(loaded):
    compose = yaml.safe_load((COMPOSE / "docker-compose.yml").read_text(encoding="utf-8"))
    assert compose["networks"]["inside"] == {"internal": True}
    for name in loaded.FOLLOWERS:
        follower = compose["services"][name]
        assert follower["networks"] == ["inside"], name
        assert follower["read_only"] is True and follower["cap_drop"] == ["ALL"], name
        assert follower["profiles"] == ["followers"], name  # started by the driver, not by `up`
        assert "ports" not in follower, name
        # No token, credential or link is written into the file.
        assert not {"SWARMSCRIBE_JOIN_TOKEN"} & set(follower["environment"]), name


def test_no_port_another_test_uses_is_published(loaded):
    text = (COMPOSE / "docker-compose.yml").read_text(encoding="utf-8")
    for port in ("8900", "8901", '"5432:5432"', "8080:", "18080", "18443"):
        assert port not in text, port
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/follower/tests/test_compose_driver.py -v`
Expected: every test errors with `FileNotFoundError` for `e2e/follower-compose/run_e2e.py`.

- [ ] **Step 3: Write the Compose file**

Create `e2e/follower-compose/docker-compose.yml`:

```yaml
# The follower image against a real leader (follower spec, section 10): Postgres, two leader
# replicas behind nginx, and two followers from the real image with tiny.en baked in. Both
# images are built beforehand and run_e2e.py drives the test (see .github/workflows/ci.yml,
# job follower-compose-e2e). Every value here is test-only.
#
# The followers sit on a network with no route out (`internal: true`): they reach the proxy
# and nothing else, so a baked image that asked Hugging Face for anything would fail here.
name: swarmscribe-follower-e2e

x-leader: &leader
  image: ${LEADER_IMAGE:-swarmscribe-leader:e2e}
  pull_policy: never
  networks: [inside]
  environment:
    SWARMSCRIBE_DATABASE_URL: postgresql://postgres:postgres@postgres:5432/swarmscribe
    # The leader builds its own file links from this, and the followers fetch them from
    # inside the Compose network: it must be the proxy's name there (follower spec 12.6).
    SWARMSCRIBE_PUBLIC_URL: http://proxy
    SWARMSCRIBE_LINK_KEY: follower-compose-link-key-0123456789abcdef
    # A lease far shorter than a long transcription: only heartbeats keep it (spec 5.5).
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

x-follower: &follower
  image: ${FOLLOWER_IMAGE:-swarmscribe-follower:e2e}
  pull_policy: never
  # Started by run_e2e.py, once the join token exists: `up -d` alone leaves them out.
  profiles: ["followers"]
  networks: [inside]
  # As the chart will run it: a read-only root, no capabilities, the image's own user. The
  # state and scratch folders are the image's declared volumes; they stay with the container,
  # so a killed follower that is started again finds its credential and its old scratch.
  read_only: true
  cap_drop: ["ALL"]
  security_opt: ["no-new-privileges:true"]
  # Two cores each: a long recording then takes long enough, on any machine, to be killed
  # in the middle and to outlast the lease several times.
  cpus: 2
  environment:
    SWARMSCRIBE_LEADER_URL: http://proxy
    # Plain http to the proxy and in the leader's links: the development switch (spec 4.1).
    SWARMSCRIBE_FOLLOWER_ALLOW_HTTP: "1"
    SWARMSCRIBE_JOIN_TOKEN_FILE: /run/secrets/join-token
    # Unset in Compose and Kubernetes arrives as an empty string: it must mean "none".
    SWARMSCRIBE_LEADER_CA_FILE: ""
  volumes:
    - ./work/secrets:/run/secrets:ro

services:
  postgres:
    image: postgres:16
    networks: [inside, outside]
    environment:
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: swarmscribe
    ports:
      # For run_e2e.py, which reads and seeds the leader's database. Loopback only, and not
      # 5432: the leader's own Compose test publishes that one.
      - "127.0.0.1:15432:5432"
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
    networks: [inside]
    volumes:
      # The leader's own Compose test's proxy: two replicas named leader-1 and leader-2.
      - ../compose/nginx.conf:/etc/nginx/nginx.conf:ro
    depends_on:
      leader-1:
        condition: service_healthy
      leader-2:
        condition: service_healthy

  follower-1:
    <<: *follower

  follower-2:
    <<: *follower

networks:
  inside:
    internal: true
  outside: {}
```

- [ ] **Step 4: Write the driver**

Create `e2e/follower-compose/run_e2e.py`:

```python
"""Compose end-to-end scenario for the follower image (follower spec, section 10).

Postgres, two leader replicas behind nginx, and two followers from the real
`swarmscribe-follower` image with tiny.en baked in, read-only, without capabilities and on a
network with no route out. What it proves, in order:

1. both followers load the baked model, register with one join token and compete for six
   short recordings; each is transcribed exactly once, and the outputs say what was said;
2. a follower killed in the middle of a long recording loses nothing: its lease expires,
   the other one transcribes the recording, and heartbeats keep that 8-second lease alive
   through a transcription several times as long;
3. the killed follower, started again, reuses its credential and finds its scratch empty;
4. a follower stopped with SIGTERM in the middle of a job releases it (no attempt is
   counted) and exits 0 within the stop window;
5. a drained follower exits 0, exits 0 again when started again, and takes nothing more;
6. a revoked follower exits 4, and 4 again when started again;
7. the recording without consent is never queued, and no follower log holds the join
   token, a file link or a word of a transcript.

Administration is done with the leader's own functions against its database, as
e2e/compose/run_e2e.py does: this stack runs no identity provider, and `swarmscribe-admin`
needs a signed-in person.

CI runs this (.github/workflows/ci.yml, job follower-compose-e2e):

    python e2e/follower-compose/run_e2e.py prepare   # before `docker compose up`
    python e2e/follower-compose/run_e2e.py run       # after it

Nothing here prints a join token, a credential or a link.
"""

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import time
import uuid
import wave
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from swarmscribe_leader.auth.followers import create_join_token, drain, revoke_follower
from swarmscribe_leader.clock import utcnow
from swarmscribe_leader.db.models import Follower, Job, JobAttempt, Recording, StorageLocation
from swarmscribe_leader.db.session import make_engine, make_sessionmaker
from swarmscribe_leader.profiles import set_profile
from swarmscribe_protocol import SegmentsDocument

HERE = Path(__file__).resolve().parent
COMPOSE_FILE = HERE / "docker-compose.yml"
WORK = HERE / "work"
DATA = WORK / "data"  # mounted at /data in the leaders
SECRETS = WORK / "secrets"  # mounted at /run/secrets in the followers
FIXTURE = HERE.parents[1] / "packages" / "engine" / "tests" / "fixtures" / "stereo_speech.wav"
DATABASE_URL = "postgresql://postgres:postgres@127.0.0.1:15432/swarmscribe"  # docker-compose.yml
FOLLOWERS = ("follower-1", "follower-2")


@dataclass(frozen=True)
class Target:
    """The image under test: its device, the model baked into it, and the Compose files."""

    device: str
    model: str
    compute_type: str
    files: tuple[Path, ...]
    # A long recording is the fixture this many times over. It must take the followers long
    # enough to be interrupted in the middle and to outlast the lease: about 25 s.
    long_repeats: int


# CI, and the default. Four minutes of audio: on two cores tiny.en needs some 30 s for it
# on a fast machine and about a minute on a CI runner.
CPU = Target("cpu", "tiny.en", "int8", (COMPOSE_FILE,), 48)
target = CPU

# The fixture: the left channel says "The weather today is clear and bright", then the right
# says "Please send the quarterly report by Friday" (make_stereo_speech.ps1).
LEFT_WORD, RIGHT_WORD = "weather", "report"
CALLS, TALKS = "calls", "talks"  # two locations: split into Agent/Customer, and mono
LABELS = ("Agent", "Customer")
SHORT = {
    CALLS: tuple(f"day1/call-{n}.wav" for n in (1, 2, 3, 4)),
    TALKS: ("talk-1.wav", "talk-2.wav"),
}
UNCONSENTED = "private/held.wav"  # in CALLS, matched by no line of its consent.txt
KILLED, STOPPED, AFTER_DRAIN = "long/kill.wav", "long/stop.wav", "day2/after-drain.wav"
CONSENT = {CALLS: "day*/*.wav\nlong/*.wav\n", TALKS: "*.wav\n"}

LEASE_SECONDS = 8.0  # SWARMSCRIBE_LEASE_SECONDS in docker-compose.yml
MID_JOB_SECONDS = 5.0  # how long a long job has been held when it is killed or stopped
STOP_WITHIN_SECONDS = 10.0  # what `docker stop` allows before it kills (measured: 1 to 5)
STEP_SECONDS = 180.0  # the longest any one wait may take
SCENARIO_SECONDS = 900.0
RERUN_HINT = (
    "this scenario drains and revokes its followers and cannot be run twice on the same "
    "stack: run `docker compose -f e2e/follower-compose/docker-compose.yml --profile "
    "followers down -v`, then `prepare` and `up -d` again"
)


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


# --- files ----------------------------------------------------------------------------


def fixture_seconds() -> float:
    with wave.open(str(FIXTURE), "rb") as source:
        return source.getnframes() / source.getframerate()


def write_recording(path: Path, repeats: int = 1) -> None:
    """The fixture, `repeats` times over, written under another name first: the leader's
    scanner must never see half a file."""
    with wave.open(str(FIXTURE), "rb") as source:
        params, frames = source.getparams(), source.readframes(source.getnframes())
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    with wave.open(str(partial), "wb") as out:
        out.setparams(params)
        for _ in range(repeats):
            out.writeframes(frames)
    partial.replace(path)


def prepare() -> None:
    """The two locations' folders and the (still empty) secrets folder, before
    `docker compose up`: a bind mount of a missing folder would be created by root."""
    if WORK.exists():
        try:
            shutil.rmtree(WORK)
        except OSError as error:
            raise AssertionError(
                f"cannot clear {WORK} ({type(error).__name__}); on Linux the leaders wrote the "
                "transcripts as root: delete the folder with sudo"
            ) from None
    for location, keys in SHORT.items():
        root = DATA / location
        root.mkdir(parents=True)
        (root / "consent.txt").write_text(CONSENT[location], encoding="utf-8", newline="\n")
        for key in keys:
            write_recording(root / key)
    write_recording(DATA / CALLS / UNCONSENTED)
    SECRETS.mkdir(parents=True)
    # Readable inside the followers whatever user they run as (the image's is 10001).
    SECRETS.chmod(0o755)


def transcript(location: str, key: str, suffix: str) -> Path:
    return DATA / location / "transcripts" / f"{key}.{suffix}"


def check_outputs(location: str, key: str, *, repeats: int = 1) -> None:
    """The three outputs are in storage, validate against the protocol's schema, cover the
    whole recording and carry the words the fixture says (each on its own channel, in the
    split location)."""
    for suffix in ("txt", "srt", "segments.json"):
        expect(transcript(location, key, suffix).is_file(), f"{location}/{key} has no .{suffix}")
    document = SegmentsDocument.model_validate_json(
        transcript(location, key, "segments.json").read_text(encoding="utf-8")
    )
    used = document.settings.model
    expect(used == target.model, f"{key} was transcribed with {used}, not {target.model}")
    expect(document.device == target.device, f"{key} was transcribed on {document.device}")
    # A long recording is one phrase many times over, and a model drops some of the
    # repeats: its length says that all of it was read, the words that it was heard.
    length = fixture_seconds() * repeats
    expect(
        abs(document.duration - length) < 1.0,
        f"{location}/{key} is {length:.0f} s long; its transcript covers {document.duration:.0f} s",
    )
    text = transcript(location, key, "txt").read_text(encoding="utf-8")
    for word in (LEFT_WORD, RIGHT_WORD):
        expect(word in text.lower(), f"{location}/{key}: the transcript never says '{word}'")
    if location == TALKS:
        expect(document.channel_labels is None, f"{key} was split in the mono location")
        return
    expect(tuple(document.channel_labels or ()) == LABELS, f"{key} has no channel labels")
    lines = text.splitlines()
    expect(
        all(line.startswith(("Agent: ", "Customer: ")) for line in lines),
        f"{key}: a line of the split transcript names no speaker",
    )
    agent = " ".join(line for line in lines if line.startswith("Agent: ")).lower()
    customer = " ".join(line for line in lines if line.startswith("Customer: ")).lower()
    expect(LEFT_WORD in agent and RIGHT_WORD not in agent, f"{key}: the left channel is wrong")
    expect(RIGHT_WORD in customer and LEFT_WORD not in customer, f"{key}: the right is wrong")


# --- docker compose -------------------------------------------------------------------


def compose(*arguments: str) -> subprocess.CompletedProcess:
    files = [part for file in target.files for part in ("-f", str(file))]
    return subprocess.run(
        ["docker", "compose", *files, "--profile", "followers", *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def must(done: subprocess.CompletedProcess, what: str) -> str:
    expect(done.returncode == 0, f"{what} failed: {done.stderr.strip()[-500:]}")
    return done.stdout


@dataclass(frozen=True)
class Container:
    status: str  # running, exited, ...
    exit_code: int
    finished_at: str


def container(service: str) -> Container | None:
    container_id = compose("ps", "-a", "-q", service).stdout.strip()
    if not container_id:
        return None
    done = subprocess.run(
        ["docker", "inspect", "--format", "{{json .State}}", container_id],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    state = json.loads(must(done, f"inspecting {service}"))
    return Container(state["Status"], int(state["ExitCode"]), state["FinishedAt"])


def logs(service: str) -> str:
    done = compose("logs", "--no-color", "--no-log-prefix", service)
    return must(done, f"reading {service}'s logs")


def registrations(service: str) -> list[str]:
    """The follower ids this container has registered as, from its JSON log, oldest first."""
    found = []
    for line in logs(service).splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("event") == "registered":
            found.append(str(entry.get("follower_id")))
    return found


def start(service: str) -> None:
    must(compose("start", service), f"starting {service}")


async def wait_for_exit(service: str, *, after: str | None, within: float) -> Container:
    """Wait until the container has exited (again, when `after` is the time it last did)."""
    deadline = time.monotonic() + within
    seen: Container | None = None
    while time.monotonic() < deadline:
        seen = container(service)
        if seen is not None and seen.status == "exited" and seen.finished_at != after:
            return seen
        await asyncio.sleep(0.2)
    raise AssertionError(f"{service} did not exit within {within:.0f} s (it is {seen})")


# --- the leader's database -------------------------------------------------------------

Sessions = async_sessionmaker[AsyncSession]


async def until(
    check: Callable[[], Awaitable[Any]], what: str, within: float = STEP_SECONDS
) -> Any:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        found = await check()
        if found:
            return found
        await asyncio.sleep(0.5)
    raise AssertionError(f"timed out after {within:.0f} s waiting for {what}")


async def join_token_and_profile(sessions: Sessions) -> str:
    """A join token good for exactly two registrations (a follower that registered again on
    a restart would be refused), and the device's profile set to the baked model."""
    async with sessions() as session:
        _, token = await create_join_token(
            session,
            pool="default",
            expires_at=utcnow() + timedelta(hours=1),
            max_uses=len(FOLLOWERS),
            created_by="e2e",
        )
        await set_profile(
            session,
            target.device,
            model=target.model,
            compute_type=target.compute_type,
            temperatures=None,
            actor="e2e",
        )
        await session.commit()
    return token


async def add_locations(sessions: Sessions) -> None:
    """Written straight to Postgres: `add_location` checks the folder on the machine it runs
    on, and /data exists only inside the leaders."""
    async with sessions() as session:
        for name, mode in ((CALLS, "stereo_split"), (TALKS, "mono")):
            session.add(
                StorageLocation(
                    id=uuid.uuid4(),
                    name=name,
                    backend="local",
                    config={"root": f"/data/{name}"},
                    input_prefix="",
                    output_prefix="transcripts/",
                    pool="default",
                    required_device="any",
                    scan_interval_s=0,
                    enabled=True,
                    vocabulary_version=0,
                    channel_mode=mode,
                    channel_labels=list(LABELS),
                )
            )
        await session.commit()


async def followers(sessions: Sessions) -> dict[str, Follower]:
    async with sessions() as session:
        return {str(f.id): f for f in (await session.scalars(select(Follower))).all()}


async def job_of(sessions: Sessions, location: str, key: str) -> Job | None:
    async with sessions() as session:
        return await session.scalar(
            select(Job)
            .join(Recording, Recording.id == Job.recording_id)
            .join(StorageLocation, StorageLocation.id == Recording.location_id)
            .where(StorageLocation.name == location, Recording.key == key)
        )


async def attempts_of(sessions: Sessions, job: Job) -> list[tuple[str, str | None, float]]:
    """(follower id, outcome, seconds it lasted) for each attempt at the job, oldest first."""
    async with sessions() as session:
        rows = (
            await session.scalars(
                select(JobAttempt)
                .where(JobAttempt.job_id == job.id)
                .order_by(JobAttempt.started_at)
            )
        ).all()
    return [
        (
            str(row.follower_id),
            row.outcome,
            (row.ended_at - row.started_at).total_seconds() if row.ended_at else 0.0,
        )
        for row in rows
    ]


async def completed(sessions: Sessions, location: str, key: str) -> Job | None:
    job = await job_of(sessions, location, key)
    return job if job is not None and job.state == "completed" else None


async def held_mid_job(sessions: Sessions, location: str, key: str) -> tuple[Job, str]:
    """Wait until a follower has held the job for MID_JOB_SECONDS; returns the job and the
    holder's follower id."""

    async def leased() -> Job | None:
        job = await job_of(sessions, location, key)
        expect(
            job is None or job.state != "completed",
            f"{key} was transcribed before it could be interrupted; raise the target's"
            " long_repeats",
        )
        return job if job is not None and job.state == "leased" else None

    first = await until(leased, f"a follower to take {key}")
    await asyncio.sleep(MID_JOB_SECONDS)
    job = await leased()
    expect(
        job is not None and job.lease_id == first.lease_id,
        f"{key} did not stay with one follower for {MID_JOB_SECONDS:.0f} s",
    )
    return job, str(job.leased_by)


# --- the scenario ---------------------------------------------------------------------


@dataclass(frozen=True)
class Report:
    registered_after: float
    long_job_seconds: float
    stop_seconds: float


async def scenario(sessions: Sessions) -> Report:
    expect(not await followers(sessions), f"the leader already has followers; {RERUN_HINT}")
    expect(all(container(name) is None for name in FOLLOWERS), f"followers exist; {RERUN_HINT}")

    # 1. Two followers register with one token and compete.
    token = await join_token_and_profile(sessions)
    (SECRETS / "join-token").write_text(token, encoding="utf-8")
    (SECRETS / "join-token").chmod(0o644)
    started = time.monotonic()
    must(compose("up", "-d", "--no-deps", *FOLLOWERS), "starting the followers")

    async def both_registered() -> bool:
        return len(await followers(sessions)) == len(FOLLOWERS)

    await until(both_registered, "both followers to register")
    registered_after = time.monotonic() - started

    async def both_said_so() -> bool:
        return all(registrations(name) for name in FOLLOWERS)

    await until(both_said_so, "both followers to log their registration", 30.0)
    service_of = {registrations(name)[0]: name for name in FOLLOWERS}
    rows = await followers(sessions)
    expect(set(service_of) == set(rows), "the followers' logs and the leader disagree on their ids")
    for row in rows.values():
        told = row.capabilities
        expect(
            told.get("device") == target.device, f"a follower registered as {told.get('device')}"
        )
        expect(target.model in told.get("models", []), "a follower did not report its model")

    await add_locations(sessions)
    short = [(location, key) for location, keys in SHORT.items() for key in keys]

    async def all_short_done() -> bool:
        return all([await completed(sessions, location, key) for location, key in short])

    await until(all_short_done, "the six short recordings to complete")
    winners = set()
    for location, key in short:
        check_outputs(location, key)
        attempts = await attempts_of(sessions, await job_of(sessions, location, key))
        expect(
            [outcome for _, outcome, _ in attempts] == ["completed"],
            f"{location}/{key} took {len(attempts)} attempts: {[a[1] for a in attempts]}",
        )
        winners.add(attempts[0][0])
    expect(winners == set(service_of), "one follower transcribed everything: no competition")

    # 2. A follower killed mid-job loses nothing; heartbeats keep the survivor's lease.
    write_recording(DATA / CALLS / KILLED, target.long_repeats)
    job, victim = await held_mid_job(sessions, CALLS, KILLED)
    must(compose("kill", service_of[victim]), f"killing {service_of[victim]}")
    print(f"killed {service_of[victim]} mid-job", flush=True)
    job = await until(lambda: completed(sessions, CALLS, KILLED), f"{KILLED} to complete", 300.0)
    attempts = await attempts_of(sessions, job)
    expect(
        [(who, outcome) for who, outcome, _ in attempts]
        == [(victim, "expired"), (next(f for f in service_of if f != victim), "completed")],
        f"{KILLED}: expected the victim's lease to expire and the other to finish, got "
        f"{[(service_of.get(who, who), outcome) for who, outcome, _ in attempts]}",
    )
    expect(job.attempts == 2, f"{KILLED} counts {job.attempts} attempts, not 2")
    long_job_seconds = attempts[1][2]
    expect(
        long_job_seconds >= LEASE_SECONDS * 1.5,
        f"{KILLED} took {long_job_seconds:.0f} s, too short to prove that heartbeats keep a "
        f"{LEASE_SECONDS:.0f} s lease; raise the target's long_repeats",
    )
    check_outputs(CALLS, KILLED, repeats=target.long_repeats)

    # 3. Started again, the killed follower is the same follower, with an empty scratch.
    await come_back(sessions, service_of[victim], victim)
    left = must(
        compose("exec", "-T", service_of[victim], "ls", "-A", "/scratch"), "listing scratch"
    ).split()
    expect(left == [".swarmscribe-scratch"], f"the killed follower's scratch holds {left}")

    # 4. SIGTERM mid-job: released, not counted, out within the stop window.
    write_recording(DATA / CALLS / STOPPED, target.long_repeats)
    job, holder = await held_mid_job(sessions, CALLS, STOPPED)
    before = container(service_of[holder])
    asked = time.monotonic()
    must(compose("kill", "-s", "SIGTERM", service_of[holder]), f"stopping {service_of[holder]}")
    ended = await wait_for_exit(service_of[holder], after=before.finished_at, within=30.0)
    stop_seconds = time.monotonic() - asked
    expect(ended.exit_code == 0, f"a follower stopped mid-job exited {ended.exit_code}, not 0")
    expect(
        stop_seconds <= STOP_WITHIN_SECONDS,
        f"a follower stopped mid-job took {stop_seconds:.1f} s (limit {STOP_WITHIN_SECONDS:.0f} s)",
    )
    print(f"stopped {service_of[holder]} mid-job in {stop_seconds:.1f} s", flush=True)
    job = await until(lambda: completed(sessions, CALLS, STOPPED), f"{STOPPED} to complete", 300.0)
    attempts = await attempts_of(sessions, job)
    expect(
        [(who, outcome) for who, outcome, _ in attempts][:1] == [(holder, "released")]
        and attempts[-1][1] == "completed",
        f"{STOPPED}: expected a release, then a completion, got {[a[1] for a in attempts]}",
    )
    expect(job.attempts == 1, f"{STOPPED} counts {job.attempts} attempts; a release counts none")
    check_outputs(CALLS, STOPPED, repeats=target.long_repeats)
    await come_back(sessions, service_of[holder], holder)

    # 5. Drain: exit 0, exit 0 again when started again, and nothing more is taken.
    drained, revoked = sorted(service_of, key=service_of.get)
    before = container(service_of[drained])
    async with sessions() as session:
        await drain(session, uuid.UUID(drained), actor="e2e")
        await session.commit()
    ended = await wait_for_exit(service_of[drained], after=before.finished_at, within=60.0)
    expect(ended.exit_code == 0, f"a drained follower exited {ended.exit_code}, not 0")
    start(service_of[drained])
    again = await wait_for_exit(service_of[drained], after=ended.finished_at, within=60.0)
    expect(again.exit_code == 0, f"a drained follower, restarted, exited {again.exit_code}")
    write_recording(DATA / CALLS / AFTER_DRAIN)
    job = await until(lambda: completed(sessions, CALLS, AFTER_DRAIN), f"{AFTER_DRAIN} to complete")
    attempts = await attempts_of(sessions, job)
    expect(
        [(who, outcome) for who, outcome, _ in attempts] == [(revoked, "completed")],
        "a recording queued after the drain was not taken by the follower that is left",
    )
    check_outputs(CALLS, AFTER_DRAIN)
    expect((await followers(sessions))[drained].state == "draining", "the drain did not last")

    # 6. Revoke: exit 4, and 4 again.
    before = container(service_of[revoked])
    async with sessions() as session:
        await revoke_follower(session, uuid.UUID(revoked), now=utcnow(), actor="e2e")
        await session.commit()
    ended = await wait_for_exit(service_of[revoked], after=before.finished_at, within=60.0)
    expect(ended.exit_code == 4, f"a revoked follower exited {ended.exit_code}, not 4")
    start(service_of[revoked])
    again = await wait_for_exit(service_of[revoked], after=ended.finished_at, within=60.0)
    expect(again.exit_code == 4, f"a revoked follower, restarted, exited {again.exit_code}")

    # 7. Nothing registered twice, nothing without consent was touched, nothing leaked.
    rows = await followers(sessions)
    expect(len(rows) == len(FOLLOWERS), f"the leader has {len(rows)} follower rows, not 2")
    expect(rows[revoked].state == "revoked", "the revocation did not last")
    for name in FOLLOWERS:
        expect(len(registrations(name)) == 1, f"{name} registered more than once")
        said = logs(name)
        expect(token not in said, f"{name}'s log holds the join token")
        expect("/v1/files/" not in said, f"{name}'s log holds a file link")
        expect(
            LEFT_WORD not in said.lower() and "quarterly" not in said.lower(),
            f"{name}'s log holds transcript text",
        )
    expect(await job_of(sessions, CALLS, UNCONSENTED) is None, "a job exists without consent")
    expect(
        not transcript(CALLS, UNCONSENTED, "txt").exists(),
        "the recording without consent was transcribed",
    )
    return Report(registered_after, long_job_seconds, stop_seconds)


async def come_back(sessions: Sessions, service: str, follower_id: str) -> None:
    """Start a stopped follower container again and wait until the leader hears from it:
    the same follower (its credential is in the container's state volume), not a new one."""
    last_seen = (await followers(sessions))[follower_id].last_seen_at
    start(service)

    async def heard() -> bool:
        row = (await followers(sessions))[follower_id]
        return row.last_seen_at > last_seen and row.state == "active"

    await until(heard, f"{service} to come back as the same follower")
    expect(len(registrations(service)) == 1, f"{service} registered again after a restart")
    expect(len(await followers(sessions)) == len(FOLLOWERS), "a restart added a follower row")


async def run() -> Report:
    engine = make_engine(DATABASE_URL)
    try:
        return await asyncio.wait_for(scenario(make_sessionmaker(engine)), SCENARIO_SECONDS)
    except TimeoutError:
        raise AssertionError(f"the scenario did not finish in {SCENARIO_SECONDS:.0f} s") from None
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="SwarmScribe follower Compose scenario")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("prepare", help="write the recordings and folders (before up)")
    commands.add_parser("run", help="run the scenario against the running Compose project")
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            prepare()
            print(f"wrote {WORK}")
            return 0
        report = asyncio.run(run())
    except AssertionError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    print(
        f"passed ({target.model} on {target.device}): two followers registered in "
        f"{report.registered_after:.0f} s and shared six recordings; a killed follower's job "
        f"was redone in {report.long_job_seconds:.0f} s under an {LEASE_SECONDS:.0f} s lease; "
        f"a stop mid-job took {report.stop_seconds:.1f} s and counted no attempt; drain exited "
        "0 and revoke exited 4, twice each"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run the driver's tests, then the whole suite**

Run: `uv run pytest packages/follower/tests/test_compose_driver.py -v`
Expected: 15 passed.

Run: `uv run ruff check . && uv run pytest`
Expected: `All checks passed!` and no failure.

- [ ] **Step 6: Run the scenario**

```bash
docker build -t swarmscribe-leader:e2e -f e2e/compose/Dockerfile .
docker build --build-arg MODELS=tiny.en -t swarmscribe-follower:e2e --target cpu -f docker/follower.Dockerfile .
uv run python e2e/follower-compose/run_e2e.py prepare
docker compose -f e2e/follower-compose/docker-compose.yml up -d
uv run python e2e/follower-compose/run_e2e.py run
```

Expected, after about 100 s (the numbers are the ones measured here; the names of the followers vary):

```
killed follower-2 mid-job
stopped follower-1 mid-job in 1.6 s
passed (tiny.en on cpu): two followers registered in 3 s and shared six recordings; a killed follower's job was redone in 26 s under an 8 s lease; a stop mid-job took 1.6 s and counted no attempt; drain exited 0 and revoke exited 4, twice each
```

If it fails, read every service's log before changing anything: `docker compose -f e2e/follower-compose/docker-compose.yml --profile followers logs --no-color`. Two failures mean the machine, not the follower, and say so themselves: `... was transcribed before it could be interrupted` and `... too short to prove that heartbeats keep a 8 s lease` (raise `long_repeats` of the `CPU` target).

- [ ] **Step 7: See that a second run is refused, then tear down**

```bash
uv run python e2e/follower-compose/run_e2e.py run; echo "exit $?"
docker compose -f e2e/follower-compose/docker-compose.yml --profile followers down -v
```

Expected: `FAILED: the leader already has followers; this scenario drains and revokes its followers and cannot be run twice on the same stack: ...` and `exit 1`; then the project is removed. On Linux the leaders wrote the transcripts as root: `sudo rm -rf e2e/follower-compose/work` before the next `prepare` (it says so if it cannot).

- [ ] **Step 8: Commit**

```bash
git add e2e/follower-compose/docker-compose.yml e2e/follower-compose/run_e2e.py packages/follower/tests/test_compose_driver.py
git commit -m "test(follower): Compose test of the image with a real model and a real leader"
```

---

### Task 6: The CI job and the README

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `README.md`

**Interfaces:**
- Consumes: everything above.
- Produces: the GitHub Actions job `follower-compose-e2e`; the README sections "Follower image" and "Follower Compose test" (F2b replaces the first with the full "Follower images").

- [ ] **Step 1: Add the job**

In `.github/workflows/ci.yml`, insert this job after the `console-compose-e2e` job and before the `chart` job (two spaces of indentation, like its neighbours):

```yaml
  follower-compose-e2e:
    runs-on: ubuntu-latest
    timeout-minutes: 25
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync
      - name: Build the leader image
        run: docker build -t swarmscribe-leader:e2e -f e2e/compose/Dockerfile .
      - name: Build the follower image with tiny.en baked in
        run: >-
          docker build --build-arg MODELS=tiny.en -t swarmscribe-follower:e2e
          --target cpu -f docker/follower.Dockerfile .
      - name: Build the follower image without a model
        run: docker build -t swarmscribe-follower:cpu --target cpu -f docker/follower.Dockerfile .
      - name: Check both follower images
        run: |
          bash docker/check-follower-image.sh swarmscribe-follower:e2e cpu tiny.en
          bash docker/check-follower-image.sh swarmscribe-follower:cpu cpu
      - name: Write the recordings
        run: uv run python e2e/follower-compose/run_e2e.py prepare
      - name: Start Postgres, two leader replicas and the proxy
        run: docker compose -f e2e/follower-compose/docker-compose.yml up -d
      - name: Run the scenario (kills, stops, drains and revokes followers)
        run: uv run python e2e/follower-compose/run_e2e.py run
      - name: Show every service's logs
        if: failure()
        run: >-
          docker compose -f e2e/follower-compose/docker-compose.yml
          --profile followers logs --no-color
      - name: Tear down
        if: always()
        run: >-
          docker compose -f e2e/follower-compose/docker-compose.yml
          --profile followers down -v
```

The followers' logs are safe to print: they hold no token, link or transcript text, and the scenario fails if they do.

- [ ] **Step 2: Check the workflow file still parses**

Run: `uv run python -c "import yaml; jobs = yaml.safe_load(open('.github/workflows/ci.yml'))['jobs']; print(sorted(jobs))"`
Expected: a list that contains `follower-compose-e2e` beside the existing jobs (`chart`, `compose-e2e`, `console-compose-e2e`, `test`, `web`, `web-e2e`).

- [ ] **Step 3: Write the README sections**

In `README.md`, insert the following immediately before the line `## Run the fleet console (development)`:

````markdown
### Follower image

`docker/follower.Dockerfile` builds `swarmscribe-follower:cpu`: the follower, the engine and
the protocol package, installed with `uv` into an environment that is all the final image
holds beside Python. It carries no leader, no console and no build tools. It runs as user
10001 with the follower as its only process: `docker stop` reaches it directly, and it
exits `0` with the job in hand released (see "Stopping" above).

```
docker build -t swarmscribe-follower:cpu --target cpu -f docker/follower.Dockerfile .
docker run -d --restart on-failure --read-only --cap-drop ALL \
  -e SWARMSCRIBE_LEADER_URL=https://leader.example.org \
  -e SWARMSCRIBE_JOIN_TOKEN=<the token> \
  -v swarmscribe-follower:/var/lib/swarmscribe-follower \
  -v swarmscribe-models:/models \
  swarmscribe-follower:cpu
```

It writes in three places and nowhere else, so the root filesystem can be read-only:

| Path | Holds | In the image |
|---|---|---|
| `/var/lib/swarmscribe-follower` | the credential and the lock file (also `HOME`) | a declared volume; name it (`-v swarmscribe-follower:...`) so that the credential outlives `docker rm`, which a single-use join token needs |
| `/scratch` | the recording being transcribed; emptied after every job and at every start | a declared volume |
| `/models` | the model cache | a plain folder: mount a volume to keep downloads (with `--read-only` it is needed), or bake the models in |

A state folder given as a `tmpfs` must be the follower's own: `--tmpfs
/var/lib/swarmscribe-follower:uid=10001,gid=10001,mode=0700`. A plain tmpfs belongs to root
and is writable by all, and the credential is refused there.

**The model.** By default the follower downloads its start-up model from Hugging Face on
first start into `/models` (1.5 GB for `distil-large-v3`). To put models into the image
instead, name them at build time:

```
docker build --build-arg MODELS=distil-large-v3 -t swarmscribe-follower:cpu-distil-large-v3 \
  --target cpu -f docker/follower.Dockerfile .
```

`MODELS` is a comma-separated list. Each model is downloaded during the build from one
pinned commit and every file is checked against its SHA-256 in `docker/models.lock.json`;
a file that differs fails the build. The first name becomes the start-up model
(`SWARMSCRIBE_FOLLOWER_STARTUP_MODEL`), and the image is offline
(`SWARMSCRIBE_FOLLOWER_OFFLINE=1`): it never asks Hugging Face for anything, and a job for
a model it does not hold is handed back and the follower exits `3`. The leader's profile
for the device must therefore name a model the image holds (`swarmscribe-admin profiles
set`). To allow a model that is not in the lock file yet, add the entry that
`uv run python docker/fetch_models.py --pin <name>` prints.

`bash docker/check-follower-image.sh <image> cpu [<baked model>]` checks an image without a
leader: the user and the folders, that the leader and build tools are absent, that it
starts read-only without capabilities, and, for a baked image, that the model loads and
runs with no network at all and that `docker stop` ends a follower that is still starting.

Known limits: the image is built for the machine's own architecture and only `amd64` has
been run; it has no `HEALTHCHECK` yet (the follower listens on no port); a model that is
downloaded at run time, not baked, is whatever its repository's `main` is that day.

### Follower Compose test

`e2e/follower-compose/` runs that image, with `tiny.en` baked in, as two followers against
Postgres and two leader replicas behind nginx. The followers are read-only, without
capabilities, and on a network with no route out. It checks that both register with one
join token and share six recordings, each transcribed once and saying what was said (on the
right channel, where the location splits channels); that a follower killed in the middle of
a recording loses nothing and comes back as the same follower with an empty scratch folder;
that an 8-second lease survives a transcription several times as long; that a follower
stopped with `SIGTERM` mid-job releases the job without a counted attempt; that a drained
follower exits `0` and a revoked one `4`, again when started again; and that a recording
without consent is never touched and no follower log holds a token, a link or transcript
text. It runs in GitHub Actions (job `follower-compose-e2e`); locally, with Docker:

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
````

- [ ] **Step 4: Run the whole suite, commit and push**

Run: `uv run ruff check . && uv run pytest`
Expected: `All checks passed!` and no failure.

```bash
git add .github/workflows/ci.yml README.md
git commit -m "ci(follower): build, check and Compose-test the follower image"
git push -u origin follower-f2a
```

- [ ] **Step 5: Watch the job on GitHub**

Open the pull request and wait for `follower-compose-e2e`. Expected: green, in well under its 25 minutes (the scenario itself: three to five minutes on a runner). Read the scenario step's last line and note its three numbers (registration, the long job, the stop) in the pull request's description: they are the first measurements on a runner. If the long job took less than 20 s or more than 150 s there, say so in the description: `long_repeats` should be adjusted before the test flakes.

If the job fails on `the token file` or a permission error: the runner's user wrote `e2e/follower-compose/work/secrets/join-token` and the follower (uid 10001) reads it through a read-only bind mount; the driver sets the file to 0644 and the folder to 0755 for that. Check those two modes in the failing run's log before anything else.

---

## Self-Review

**Spec coverage.** 8.1's image (user, volumes, writable paths, `HOME`, entrypoint, no build tools, no leader): Tasks 3 and 4. 5.10's baked models and offline mode, and the amendment's start-up model: Tasks 2 to 4. 8.1's check script: Task 4 (the `cuda` part is first *run* in F2b). Section 10's Compose test: Task 5 covers consented and unconsented recordings, mono and split, the profile set to `tiny.en`, exactly-once completion with schema-valid outputs, the kill, the `SIGTERM` release without a counted attempt, drain, revoke with exit 4 that does not come back, and the 8-second lease kept through a real transcription; the public URL is the proxy's name (12.6). F1's follow-ups for F2: the start-up model from the baked model (Task 3), a stop during start-up as PID 1 with the image (Task 4), `ALLOW_HTTP=1` in Compose (Task 5), `HOME` and the state folder in the image (Task 3), the whole suite as the gate and the contract tests on Linux with Postgres (Global Constraints). Left to F2b, on purpose: "start the health listener before the model load", `/healthz`, `/metrics`, the `HEALTHCHECK`, the memory guard, the `cuda` image and its run on a GPU.

**Not covered by a container in either plan:** `SWARMSCRIBE_FOLLOWER_ON_DRAINED=park` and the pool token as a file (F3's chart), `SWARMSCRIBE_LEADER_CA_FILE` over real TLS (ruling 9).

**Placeholders.** None: every file is given whole, every command has its expected output.

**Names.** `ask_leader` and `--no-leader` (Task 1) are what Task 4's script calls. `names`, `cache_folder`, `verify`, `fetch`, `main` (Task 2) are what its tests call. The stage names `manifests`, `deps-cpu`, `build-cpu`, `models`, `runtime`, `cpu` (Task 3) are the ones F2b extends. `prepare`, `run`, `Target`, `CPU`, `target`, `check_outputs`, `write_recording`, `fixture_seconds`, `registrations`, `logs`, `transcript` (Task 5) are what `test_compose_driver.py` uses. The images `swarmscribe-leader:e2e`, `swarmscribe-follower:e2e` and `swarmscribe-follower:cpu` are the same in Tasks 3 to 6.
