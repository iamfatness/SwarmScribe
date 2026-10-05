# Follower F1 — Agent Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `swarmscribe-follower`, the agent that joins a leader, claims one recording at a time, transcribes it, uploads the three outputs and reports the result, and that strands no job when it is stopped, drained, revoked, cancelled or cut off.

**Architecture:** A new package, `packages/follower`, depending on `swarmscribe-protocol` and `swarmscribe-engine` only. Plain threads and synchronous `httpx`, no asyncio: a worker thread runs the job, a lease-keeper thread heartbeats, and the main thread only handles signals, because the engine call blocks and a signal handler runs on the main thread. Every collaborator (leader client, links client, model host, clock, sleep) is passed in, so the unit tests run against a fake leader and a fake engine; one test file runs the agent against the REAL leader application over loopback HTTP with a stub engine, to pin the contract without a model.

**Tech Stack:** Python 3.11+, `httpx` (sync), `pydantic-settings`, the engine's `Transcriber` and `write_outputs`; tests with pytest, `httpx.MockTransport`, `uvicorn` and the leader's test kit (real Postgres).

**Spec:** `docs/superpowers/specs/2026-10-04-follower-design.md` (sections 3 to 7, 9, 10), with `docs/superpowers/specs/2026-10-02-swarmscribe-architecture-design.md` (the master spec: sections 5.4, 6, 10, 11, 13).

**This plan is the second of five.** It needs every task of F0 (`2026-10-04-follower-f0-leader-and-engine.md`) merged: the engine's `progress`, `warm_up` and `close`; the protocol's `JobLinks`, `LinksRequest` and `DIRECTIVE_HEADER`; the leader's fresh-links route, drain header, pool tokens and `leader_testkit`. F2 (images, Compose with a real model, the health listener and metrics, the memory guard), F3 (the chart) and F4 (the outside-machine install) are written later.

## Global Constraints

- SwarmScribe is general-purpose: nothing in code, tests, fixtures or documentation may be specific to one kind of content or organisation (master spec 1).
- `follower` depends on `protocol` and `engine` only (master spec 4). The package never imports `swarmscribe_leader`; only `packages/follower/tests/test_real_leader.py` does.
- "A single-process agent", "One job at a time per follower process" (master spec 5.4).
- "Followers never accept inbound connections" (master spec 3): F1 opens no port.
- "No audio or transcript text in logs" (master spec 10), and no link, join token or credential either (follower spec 7). A link's URL carries its secret: no URL is ever logged or put in an exception.
- The bearer credential is sent only to the leader's `/v1/followers/*` and `/v1/jobs/*`; never to a link. Redirects are never followed.
- The follower holds no transcription policy: it applies the claim's settings and vocabulary (master spec 6).
- Exit codes (follower spec 4.1): `0` stopped cleanly or drained; `2` invalid configuration, a state folder locked by another follower, a scratch folder that is not the follower's; `3` this machine cannot do the work; `4` not authorised (no or invalid token, revoked); `5` protocol version refused.
- Environment names: `SWARMSCRIBE_LEADER_URL`, `SWARMSCRIBE_JOIN_TOKEN`, `SWARMSCRIBE_JOIN_TOKEN_FILE` and `SWARMSCRIBE_LEADER_CA_FILE` keep the master spec's names; everything else is `SWARMSCRIBE_FOLLOWER_*`.
- **No test seam in production paths.** Collaborators are constructor arguments with production defaults; nothing reads an environment variable or flag that exists for tests.
- Tests use real threads with short intervals and wait on events; they never sleep for a fixed time to "let something happen". A test that waits has a deadline and fails with a message.
- Python `>=3.11`; `ruff` line length 100, rules `E, F, I, UP, B`. `uv run ruff check .` must pass after every task.
- On the Windows development machine `uv` is run as `python -m uv`; the commands below are written `uv run ...`. The follower must work on Windows and Linux: no `fcntl` or `msvcrt` import at module level, no POSIX-only signal.
- Start from an up-to-date `main` that has F0 merged, on a new branch `follower-f1`. Never discard a hunk you did not write.

## Rulings

Decisions this plan makes. Those marked **(owner)** are the owner's to overturn.

1. **Retries live in the callers, not in the client.** `LeaderClient` makes one request and raises `Transient` (try again) or `Refused` (the leader said no); `retrying()` is the one retry loop. Each caller says how long it is willing to retry, which is different for a claim (for ever), a download (ten minutes), a submit (for ever, except `500` five times) and a `fail` (three times).
2. **`500` is transient, except on submit.** A leader bug that answers `500` to submit for ever would otherwise keep a job leased for ever while its heartbeats succeed; after five the attempt is failed, retryably (follower spec 6.4).
3. **A drained follower exits 0 by default and parks on request (owner).** `SWARMSCRIBE_FOLLOWER_ON_DRAINED=exit|park` (follower spec 5.5). The credential file is kept either way, so a restart is still drained.
4. **A revoked follower keeps its credential file** and exits 4. A restart finds the file, is refused again and exits again; it never uses a join token it may still hold (follower spec D8).
5. **An unknown credential (`401`) registers again once per process**, if a token is configured. The leader reuses the rows of `gone` pool-token followers (F0), so a machine that was wrongly thought gone meets this. The same `401` in the middle of a job stops the job and wipes scratch first (Adjustment 1).
6. **A claim whose job id is not a UUID is ignored entirely**: the id becomes a folder name and a URL path segment (follower spec D25).
7. **The `httpx` and `httpcore` loggers are raised to `WARNING` when the package is imported**, not only by the command line: `httpx` logs every request URL at `INFO`, and no way of using the follower may log a link.
8. **The contract test serves the real leader with `uvicorn` on a loopback port in the test process.** The follower's client is synchronous; `httpx`'s in-process ASGI transport is asynchronous only. Real HTTP also exercises streaming, `Content-Length` and headers as they really are.
9. **The memory guard (follower spec D22), the health listener and metrics are F2**, where memory is measured and the image needs a health check. F1 has no HTTP listener.
10. **`heartbeat_interval` and `parked_poll_seconds` are constructor arguments of `Agent`**, like its clock and sleep: the protocol's interval is whole seconds, and tests need a keeper that beats in milliseconds.

## Review Focus

Inputs and conditions the spec implies and that are most likely to bite a person running this, each pinned by a test in the task that owns the code:

1. **A scratch setting that points at a folder with someone's files** (`SWARMSCRIBE_FOLLOWER_SCRATCH_DIR=/home/me`): the follower wipes its scratch folder, so it must refuse one it did not mark, and delete nothing — Task 3, `test_a_folder_with_someone_elses_files_is_refused_and_left_alone`; Task 9, `test_a_scratch_folder_that_is_not_the_followers_exits_2`.
2. **A leader outage in the middle of a long job**: heartbeats, uploads and submit must wait it out, not fail the job — Task 7, `test_a_leader_outage_does_not_stop_the_job_and_heartbeats_resume`; Task 8, `test_a_leader_outage_during_upload_and_submit_is_waited_out`.
3. **A job that outlasts its links**: uploads refused as expired get fresh links and carry on; if those are refused too the job is released, not failed — Task 8, `test_upload_links_that_expired_during_a_long_job_are_replaced`, `test_when_fresh_links_are_refused_too_the_job_is_released`.
4. **A restart loop under a service manager** after a drain or a revocation: no restart may register again — Task 9, `test_a_drained_idle_follower_exits_cleanly_and_a_restart_does_not_re_register`, `test_a_revoked_follower_exits_4_keeps_its_credential_and_never_comes_back`; Task 11 against the real leader.
5. **A GPU library that is missing only at the first inference** (cuBLAS on Windows): found by the warm-up before registering, never on a job — Task 6, `test_a_library_that_fails_only_at_the_first_inference_is_caught_by_the_warm_up`; Task 9, `test_a_machine_that_cannot_transcribe_exits_3_without_registering`.
6. **Secrets in logs**: a link URL in an HTTP client's own log line, a credential in a failure reason, transcript text in an error — Task 1, `test_importing_the_follower_already_silences_the_http_clients`; Task 8, `test_no_log_line_or_failure_reason_holds_a_link_a_credential_or_the_transcript`.
7. **An optional setting passed as an empty string** (Compose and Kubernetes do this for unset variables): `SWARMSCRIBE_JOIN_TOKEN=""` means none — Task 1, `test_an_optional_setting_passed_as_an_empty_string_is_unset`.
8. **A stop while a job is running**: finished only if it fits the grace period, released otherwise, released at once on a second stop, and the follower exits even when the release cannot be delivered — Task 9, the five shutdown tests.

## Adjustments from the F0 final review

F0's final review (`.superpowers/sdd/2026-10-04-follower-f0-leader-and-engine/final-review.md`) and its fix wave settle these. Each is applied in the task named; where the task text below is older than this section, this section wins.

1. **A `401` during a job** (the row was reused, so the credential is gone) is not a lost lease. The job runner stops the job, wipes scratch, calls nothing, and the agent registers again once (Ruling 5). It must not be settled as `LEASE_LOST`: Task 7's `test_a_refused_heartbeat_stops_the_job_with_the_reason` row `401` keeps `UNAUTHORISED`, and Tasks 8 and 9 handle that reason as stop, wipe, then re-register, never as release or fail.
2. **Fresh-links errors (Task 8, `_with_fresh_links`).**
   - `409 stale_lease` is handled as a cancel: stop, wipe, call nothing, claim again (spec 6.3). The leader answers it for a cancelled job where a heartbeat would say `cancel`; treat both the same.
   - `429` is expected within 60 s of the claim (`Retry-After` up to 60). It is `Transient`: wait the `Retry-After` and ask again (spec 6.4), without counting against the job. A link refused in the first minute of a job costs up to a minute.
   - `503` (`Retry-After: 30` for unavailable storage) is retried in place (spec 6.4). Since the leader's fix wave the route answers `503` for every storage error, but keep `400 invalid_key` mapped as spec 6.2 says: `fail` `other`, retryable, with an error log.
3. **Download links die with the lease.** F0's fix wave bound download links to the lease, as upload links already were (follower spec 12.2): once the lease ends, a download link answers `409 stale_lease`. `Links.download` therefore maps `409 stale_lease` to `LeaseLost`, as `upload` does (Task 5, and its table of refused downloads). The follower still stops on the directive or on the `409` from a job call, and never waits for a download to fail to learn the lease is gone; a link that is merely expired (`403`) is not a lost lease.
4. **`MODEL_NAME` (Task 6)** must be no looser than the leader's rule: the pattern and at most 100 characters in all. Neither the leader nor the protocol exports an importable rule today, and the follower never imports the leader. So Task 6 begins by adding `MODEL_NAME_PATTERN` and `MODEL_NAME_MAX_LENGTH` to `swarmscribe_protocol` (constants only, no schema change; the snapshot is unchanged), and the leader's `admin_models.MODEL_PATTERN` and `max_length=100` import them. The follower imports the same constants from the protocol and defines no copy.
5. **The test kit (Task 11).** The contract test names its own database with the kit's required prefix, `swarmscribe_kit_follower` (the kit now refuses any name that does not start `swarmscribe_kit_`, and refuses `swarmscribe_test` and `swarmscribe_console_test`). The kit has no pool-token helper: the test calls `auth.pool_tokens.create_pool_token` directly. `empty_tables` keeps `settings_profiles`, so a test that changes a profile restores it in a `finally`.
6. **The engine (Tasks 6 and 8).**
   - A stop takes effect at the next segment only: the progress callback runs once per segment, so a stop during a long segment waits for it. Do not promise a prompt stop, and keep the shutdown grace arithmetic in terms of a segment.
   - `Transcriber.close()` while `transcribe` runs raises `RuntimeError`. `close()` is called only between jobs, on the worker thread that runs jobs, never from a signal handler or the agent's stop path while a job is running.
   - The follower defines its own stop exception (`JobStopped`, in `lease.py`) and raises it from the progress callback; it does not use or subclass an engine exception for this, and the engine's own errors are told apart from it by type.
   - `ModelHost` owns its own `Transcriber`: it builds it through its factory, warms it up and closes it. Nothing else holds a reference to it, and `JobRunner` receives the transcriber from `ModelHost.get()` for the length of one job only.

## File Structure

| File | Responsibility |
|---|---|
| `packages/follower/pyproject.toml` (new); `pyproject.toml`, `uv.lock` (modify) | the package and the workspace |
| `packages/follower/src/swarmscribe_follower/__init__.py` | version; silences the HTTP clients' request logging |
| `.../errors.py` | exit codes, `FollowerExit` |
| `.../config.py` | `Settings` from the environment |
| `.../logs.py` | JSON and text log lines |
| `.../credentials.py` | the credential file |
| `.../scratch.py` | the scratch folder and per-job folders |
| `.../leader.py` | `LeaderClient` (one method per route), `Transient`, `Refused`, `retrying` |
| `.../transfer.py` | `Links`: download and upload through a `Link` |
| `.../device.py` | device probe, GPU name and memory, cached models, capabilities |
| `.../models.py` | `ModelHost`: load, warm up, switch, close |
| `.../lease.py` | `JobControl`, `LeaseKeeper` |
| `.../job.py` | `JobRunner`: one job, and what the leader is told |
| `.../agent.py` | `Agent`: prepare, register, claim loop, drain, stop |
| `.../main.py` | the command line: `run`, `join`, `leave`, `doctor`; signals; the state lock |
| `packages/follower/tests/conftest.py`, `follower_testkit.py` | `sys.path`; `FakeLeader`, `FakeEngine`, `make_runner`, `make_agent` |
| `packages/follower/tests/test_*.py` | one test file per module; `test_real_leader.py` is the contract |
| `README.md` (modify) | "Run a follower (development)"; the status table |

---

### Task 1: The package, its settings, exit codes and logs

**Files:**
- Create: `packages/follower/pyproject.toml`, `packages/follower/src/swarmscribe_follower/__init__.py`, `errors.py`, `config.py`, `logs.py` (all under `packages/follower/src/swarmscribe_follower/`)
- Modify: `pyproject.toml` (the workspace root), `uv.lock`
- Test: `packages/follower/tests/test_config.py`

**Interfaces:**
- Consumes: nothing internal.
- Produces:
  - `swarmscribe_follower.FOLLOWER_VERSION: str`
  - `errors`: `EXIT_OK = 0`, `EXIT_CONFIGURATION = 2`, `EXIT_UNFIT = 3`, `EXIT_UNAUTHORISED = 4`, `EXIT_PROTOCOL = 5`; `FollowerExit(code: int, reason: str)` with `.code`, `.reason`.
  - `config.Settings` (a `BaseSettings`) with fields `leader_url: str`, `join_token: SecretStr | None`, `join_token_file: Path | None`, `leader_ca_file: Path | None`, `allow_http: bool`, `pool: str`, `device: "auto" | "cuda" | "cpu"`, `state_dir: Path`, `scratch_dir: Path | None`, `model_dir: Path | None`, `offline: bool`, `allowed_models: tuple[str, ...]`, `shutdown_grace_seconds: float`, `on_drained: "exit" | "park"`, `log_format: "json" | "text"`; properties `scratch: Path`, `credential_file: Path`; method `token() -> str | None` (raises `ValueError` when the token file cannot be read). Constructible by field name: `Settings(leader_url=..., join_token=...)`.
  - `logs.configure(log_format="json", *, stream=None)`, `logs.JsonFormatter`, `logs.TextFormatter`. Log calls pass `extra={"job_id": ..., "lease_id": ..., "follower_id": ..., "event": ...}`.

- [ ] **Step 1: Create the package and add it to the workspace**

Create `packages/follower/pyproject.toml`:

```toml
[project]
name = "swarmscribe-follower"
version = "0.1.0"
description = "SwarmScribe follower: takes recordings from a leader and transcribes them"
requires-python = ">=3.11"
dependencies = [
    "swarmscribe-protocol",
    "swarmscribe-engine",
    "httpx>=0.27,<1",
    "pydantic-settings>=2.4,<3",
]

[project.scripts]
swarmscribe-follower = "swarmscribe_follower.main:run"

[tool.uv.sources]
swarmscribe-protocol = { workspace = true }
swarmscribe-engine = { workspace = true }

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/swarmscribe_follower"]
```

In the root `pyproject.toml`:

Replace

```toml
swarmscribe-leader = { workspace = true }
swarmscribe-console = { workspace = true }
```

with

```toml
swarmscribe-leader = { workspace = true }
swarmscribe-console = { workspace = true }
swarmscribe-follower = { workspace = true }
```

Replace

```toml
    "swarmscribe-leader",
    "swarmscribe-console",
    "pytest-asyncio>=0.24",
```

with

```toml
    "swarmscribe-leader",
    "swarmscribe-console",
    "swarmscribe-follower",
    "pytest-asyncio>=0.24",
```

Create `packages/follower/src/swarmscribe_follower/__init__.py`:

```python
import logging

FOLLOWER_VERSION = "0.1.0"

# httpx logs every request's URL at INFO, and a link's URL carries its secret. These loggers
# are raised to WARNING as soon as the package is imported, not only when the command line
# configures logging, so that no way of using the follower can log a link (follower spec 7).
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)
```

Run: `uv lock && uv sync`
Expected: `uv.lock` gains `swarmscribe-follower`; no other package's version changes (`httpx` and `pydantic-settings` are already locked for the leader).

- [ ] **Step 2: Write the failing tests**

Create `packages/follower/tests/test_config.py`:

```python
import io
import json
import logging

import pytest
from pydantic import ValidationError
from swarmscribe_follower.config import Settings
from swarmscribe_follower.logs import JsonFormatter, TextFormatter, configure

ENV = (
    "SWARMSCRIBE_LEADER_URL",
    "SWARMSCRIBE_JOIN_TOKEN",
    "SWARMSCRIBE_JOIN_TOKEN_FILE",
    "SWARMSCRIBE_LEADER_CA_FILE",
)


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)


def test_the_docker_run_line_of_the_master_spec_is_enough(monkeypatch):
    monkeypatch.setenv("SWARMSCRIBE_LEADER_URL", "https://leader.example.org/")
    monkeypatch.setenv("SWARMSCRIBE_JOIN_TOKEN", "  the-token  ")
    settings = Settings()
    assert settings.leader_url == "https://leader.example.org"
    assert settings.token() == "the-token"
    assert (settings.device, settings.pool, settings.on_drained) == ("auto", "default", "exit")
    assert settings.shutdown_grace_seconds == 8
    assert settings.scratch == settings.state_dir / "scratch"
    assert settings.credential_file == settings.state_dir / "credential.json"


def test_follower_settings_use_their_own_prefix(monkeypatch, tmp_path):
    monkeypatch.setenv("SWARMSCRIBE_LEADER_URL", "https://leader.example.org")
    monkeypatch.setenv("SWARMSCRIBE_FOLLOWER_DEVICE", "cpu")
    monkeypatch.setenv("SWARMSCRIBE_FOLLOWER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SWARMSCRIBE_FOLLOWER_SCRATCH_DIR", str(tmp_path / "scratch"))
    monkeypatch.setenv("SWARMSCRIBE_FOLLOWER_ALLOWED_MODELS", " large-v3 , owner/custom ,")
    monkeypatch.setenv("SWARMSCRIBE_FOLLOWER_ON_DRAINED", "park")
    settings = Settings()
    assert settings.device == "cpu"
    assert settings.scratch == tmp_path / "scratch"
    assert settings.allowed_models == ("large-v3", "owner/custom")
    assert settings.on_drained == "park"


def test_a_token_file_wins_over_the_variable_and_is_read_late(monkeypatch, tmp_path):
    secret = tmp_path / "token"
    monkeypatch.setenv("SWARMSCRIBE_LEADER_URL", "https://leader.example.org")
    monkeypatch.setenv("SWARMSCRIBE_JOIN_TOKEN", "from-the-variable")
    monkeypatch.setenv("SWARMSCRIBE_JOIN_TOKEN_FILE", str(secret))
    settings = Settings()  # the file need not exist until a registration reads it
    with pytest.raises(ValueError, match="cannot be read"):
        settings.token()
    secret.write_text("from-the-file\n", encoding="utf-8")
    assert settings.token() == "from-the-file"


@pytest.mark.parametrize("name", ENV[1:])
def test_an_optional_setting_passed_as_an_empty_string_is_unset(monkeypatch, name):
    monkeypatch.setenv("SWARMSCRIBE_LEADER_URL", "https://leader.example.org")
    monkeypatch.setenv(name, "")
    settings = Settings()
    assert settings.token() is None
    assert settings.leader_ca_file is None


@pytest.mark.parametrize(
    "url",
    [
        "http://leader.example.org",
        "leader.example.org",
        "ftp://leader.example.org",
        "https://user:pw@leader.example.org",
        "https://leader.example.org/?x=1",
        "",
    ],
)
def test_a_leader_url_that_is_not_safe_is_refused(url):
    with pytest.raises(ValidationError):
        Settings(leader_url=url)


@pytest.mark.parametrize(
    "url", ["http://localhost:8080", "http://127.0.0.1:8080", "http://[::1]:8080"]
)
def test_plain_http_is_accepted_for_loopback(url):
    assert Settings(leader_url=url).leader_url == url


def test_plain_http_elsewhere_needs_the_explicit_switch():
    assert Settings(leader_url="http://proxy", allow_http=True).leader_url == "http://proxy"


def test_a_validation_error_never_shows_the_join_token():
    with pytest.raises(ValidationError) as refused:
        Settings(leader_url="https://leader.example.org", join_token="s3cret-token", device="tpu")
    assert "s3cret-token" not in str(refused.value)
    settings = Settings(leader_url="https://x.example", join_token="s3cret-token")
    assert "s3cret-token" not in repr(settings)


def _record(**extra) -> logging.LogRecord:
    name = "swarmscribe_follower.job"
    record = logging.LogRecord(name, logging.INFO, "f", 1, "job %s", ("done",), None)
    for name, value in extra.items():
        setattr(record, name, value)
    return record


def test_json_lines_carry_the_job_and_lease():
    line = json.loads(JsonFormatter().format(_record(job_id="j1", lease_id="l1", event="job.done")))
    assert (line["message"], line["level"], line["job_id"], line["lease_id"], line["event"]) == (
        "job done",
        "info",
        "j1",
        "l1",
        "job.done",
    )
    assert "follower_id" not in line
    assert line["time"].endswith("+00:00")


def test_an_exception_is_logged_by_class_not_by_text():
    try:
        raise OSError("cannot open https://leader/v1/files/SECRET-LINK")
    except OSError:
        import sys

        record = _record()
        record.exc_info = sys.exc_info()
    line = JsonFormatter().format(record)
    assert json.loads(line)["error"] == "OSError"
    assert "SECRET-LINK" not in line


def test_text_format_is_one_readable_line():
    line = TextFormatter().format(_record(job_id="j1"))
    assert line.endswith("INFO swarmscribe_follower.job: job done job_id=j1")


def test_importing_the_follower_already_silences_the_http_clients():
    import swarmscribe_follower  # noqa: F401

    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING


def test_configuring_logs_silences_the_http_clients_request_lines():
    stream = io.StringIO()
    root = logging.getLogger()
    before, level = root.handlers[:], root.level
    try:
        logging.getLogger("httpx").setLevel(logging.DEBUG)  # something lowered it
        configure("json", stream=stream)
        logging.getLogger("httpx").info("HTTP Request: GET https://leader/v1/files/SECRET-LINK")
        logging.getLogger("httpcore").info("connect https://leader/v1/files/SECRET-LINK")
        logging.getLogger("swarmscribe_follower").info("started")
    finally:
        root.handlers[:] = before
        root.setLevel(level)
    assert "SECRET-LINK" not in stream.getvalue()
    assert json.loads(stream.getvalue())["message"] == "started"
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `uv run pytest packages/follower/tests/test_config.py -q`
Expected: FAIL at import: `ModuleNotFoundError: No module named 'swarmscribe_follower.config'`.

- [ ] **Step 4: Exit codes**

Create `packages/follower/src/swarmscribe_follower/errors.py`:

```python
"""How the follower process ends. Service managers act on these codes (follower spec 4.1)."""

EXIT_OK = 0
EXIT_CONFIGURATION = 2  # invalid settings, a locked state folder, a scratch folder not ours
EXIT_UNFIT = 3  # this machine cannot do the work: device, GPU libraries, model
EXIT_UNAUTHORISED = 4  # no or invalid join token, credential revoked: do not restart blindly
EXIT_PROTOCOL = 5  # the leader speaks another protocol version: do not restart blindly


class FollowerExit(Exception):
    """The follower must stop, with this exit code and this one-line reason. The reason is
    printed and logged, so it never holds a token, a credential or a link."""

    def __init__(self, code: int, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason
```

- [ ] **Step 5: Settings**

Create `packages/follower/src/swarmscribe_follower/config.py`:

```python
import ipaddress
import os
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

NameList = Annotated[tuple[str, ...], NoDecode]
"""Comma-separated in the environment, e.g. `large-v3, distil-large-v3`."""


def default_state_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "swarmscribe-follower"
    return Path.home() / ".local" / "share" / "swarmscribe-follower"


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class Settings(BaseSettings):
    """Follower configuration. SWARMSCRIBE_LEADER_URL, SWARMSCRIBE_JOIN_TOKEN[_FILE] and
    SWARMSCRIBE_LEADER_CA_FILE keep the names the master spec's `docker run` line uses;
    everything else is SWARMSCRIBE_FOLLOWER_*."""

    # hide_input_in_errors: a validation error must never echo the join token.
    model_config = SettingsConfigDict(
        env_prefix="SWARMSCRIBE_FOLLOWER_",
        extra="ignore",
        hide_input_in_errors=True,
        populate_by_name=True,
    )

    leader_url: str = Field(validation_alias=AliasChoices("leader_url", "SWARMSCRIBE_LEADER_URL"))
    join_token: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("join_token", "SWARMSCRIBE_JOIN_TOKEN")
    )
    join_token_file: Path | None = Field(
        default=None,
        validation_alias=AliasChoices("join_token_file", "SWARMSCRIBE_JOIN_TOKEN_FILE"),
    )
    leader_ca_file: Path | None = Field(
        default=None, validation_alias=AliasChoices("leader_ca_file", "SWARMSCRIBE_LEADER_CA_FILE")
    )
    allow_http: bool = False

    pool: str = Field(default="default", max_length=100)
    device: Literal["auto", "cuda", "cpu"] = "auto"
    state_dir: Path = Field(default_factory=default_state_dir)
    scratch_dir: Path | None = None
    model_dir: Path | None = None
    offline: bool = False
    allowed_models: NameList = ()
    shutdown_grace_seconds: float = Field(default=8.0, ge=0)
    on_drained: Literal["exit", "park"] = "exit"
    log_format: Literal["json", "text"] = "json"

    @field_validator(
        "join_token", "join_token_file", "leader_ca_file", "scratch_dir", "model_dir", mode="before"
    )
    @classmethod
    def _blank_is_unset(cls, value: Any) -> Any:
        # Compose and Kubernetes pass an unset variable as an empty string.
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("allowed_models", mode="before")
    @classmethod
    def _name_list(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.split(",")
        if isinstance(value, list | tuple):
            return tuple(name for name in (str(item).strip() for item in value) if name)
        return value

    @field_validator("leader_url")
    @classmethod
    def _absolute_http_url(cls, value: str) -> str:
        parts = urlsplit(value.strip())
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("leader_url must be an absolute URL, e.g. https://leader.example.org")
        if parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("leader_url must not carry credentials, a query or a fragment")
        return value.strip().rstrip("/")

    @model_validator(mode="after")
    def _https_unless_local(self) -> "Settings":
        parts = urlsplit(self.leader_url)
        if parts.scheme == "http" and not self.allow_http and not _is_loopback(parts.hostname):
            raise ValueError(
                "leader_url must be https (plain http is accepted only for a loopback address,"
                " or with SWARMSCRIBE_FOLLOWER_ALLOW_HTTP=1)"
            )
        return self

    @property
    def scratch(self) -> Path:
        return self.scratch_dir or self.state_dir / "scratch"

    @property
    def credential_file(self) -> Path:
        return self.state_dir / "credential.json"

    def token(self) -> str | None:
        """The join token: the file's content if a file is configured, else the variable.
        Read only when a registration needs it."""
        if self.join_token_file is not None:
            try:
                text = self.join_token_file.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise ValueError(
                    f"the join token file cannot be read: {exc.strerror or type(exc).__name__}"
                ) from None
            return text or None
        if self.join_token is not None:
            return self.join_token.get_secret_value().strip() or None
        return None
```

`AliasChoices("leader_url", "SWARMSCRIBE_LEADER_URL")` is what lets the four master-spec names skip the `SWARMSCRIBE_FOLLOWER_` prefix while `Settings(leader_url=...)` still works in code.

- [ ] **Step 6: Logs**

Create `packages/follower/src/swarmscribe_follower/logs.py`:

```python
"""Logging: one JSON object per line on stderr (follower spec 9), or plain text on request.

A log line never holds audio, transcript text, a link, a token or a credential. Link URLs
carry their own secret, and httpx logs every request URL at INFO, so its loggers are kept at
WARNING (the package sets that when it is imported; `configure` sets it again)."""

import json
import logging
import sys
from datetime import UTC, datetime

FIELDS = ("event", "job_id", "lease_id", "follower_id")
QUIETED = ("httpx", "httpcore")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                line[field] = value
        if record.exc_info and record.exc_info[0] is not None:
            # The class only: an exception's text can hold a path or a URL.
            line["error"] = record.exc_info[0].__name__
        return json.dumps(line, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = "".join(
            f" {field}={getattr(record, field)}"
            for field in FIELDS
            if getattr(record, field, None) is not None
        )
        stamp = datetime.fromtimestamp(record.created, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        return f"{stamp} {record.levelname} {record.name}: {record.getMessage()}{fields}"


def configure(log_format: str = "json", *, stream=None) -> None:
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter() if log_format == "json" else TextFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    for name in QUIETED:  # again, in case something lowered them since the import
        logging.getLogger(name).setLevel(logging.WARNING)
```

- [ ] **Step 7: Run the tests to see them pass**

Run: `uv run pytest packages/follower/tests/test_config.py -q`
Expected: PASS.

Run: `uv run ruff check . && uv run pytest packages/leader/tests/test_source_hygiene.py -q`
Expected: `All checks passed!`; PASS (the hygiene test scans every package's Python source).

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock packages/follower
git commit -m "Follower package: settings, exit codes and log lines that cannot leak a link"
```

---

### Task 2: The credential file

**Files:**
- Create: `packages/follower/src/swarmscribe_follower/credentials.py`
- Test: `packages/follower/tests/test_credentials.py`

**Interfaces:**
- Consumes: nothing internal.
- Produces:
  - `Stored(leader_url: str, follower_id: str, credential: str, device: str, heartbeat_interval: int, lease_seconds: int)` — frozen; `credential` hidden from `repr`.
  - `CredentialStore(path: Path)` with `.path`, `load() -> Stored | None`, `save(stored) -> None`, `delete() -> bool`.
  - `CredentialFileError` — its message says what to do and never holds the file's content.

- [ ] **Step 1: Write the failing tests**

Create `packages/follower/tests/test_credentials.py`:

```python
import json
import os

import pytest
from swarmscribe_follower.credentials import CredentialFileError, CredentialStore, Stored

STORED = Stored(
    leader_url="https://leader.example.org",
    follower_id="2f0d1c1e-0000-4000-8000-000000000001",
    credential="c" * 43,
    device="cpu",
    heartbeat_interval=30,
    lease_seconds=120,
)
posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")


def test_nothing_stored_is_none(tmp_path):
    assert CredentialStore(tmp_path / "state" / "credential.json").load() is None


def test_a_saved_credential_comes_back_and_its_repr_hides_it(tmp_path):
    store = CredentialStore(tmp_path / "state" / "credential.json")
    store.save(STORED)
    assert store.load() == STORED
    assert STORED.credential not in repr(store.load())
    assert not (tmp_path / "state" / "credential.json.new").exists()


def test_saving_again_replaces_the_file(tmp_path):
    store = CredentialStore(tmp_path / "credential.json")
    store.save(STORED)
    (tmp_path / "credential.json.new").write_text("left by a crash", encoding="utf-8")
    newer = Stored(**{**STORED.__dict__, "credential": "d" * 43})
    store.save(newer)
    assert store.load() == newer


def test_delete_says_whether_there_was_one(tmp_path):
    store = CredentialStore(tmp_path / "credential.json")
    assert store.delete() is False
    store.save(STORED)
    assert store.delete() is True
    assert store.load() is None


@pytest.mark.parametrize(
    "content",
    [
        "",
        "not json",
        "[]",
        json.dumps({"credential": "x"}),
        json.dumps({**STORED.__dict__, "heartbeat_interval": "30"}),
        json.dumps({**STORED.__dict__, "credential": None}),
    ],
)
def test_a_file_that_is_not_a_credential_is_refused_without_showing_it(tmp_path, content):
    path = tmp_path / "credential.json"
    path.write_text(content, encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o600)
    with pytest.raises(CredentialFileError) as refused:
        CredentialStore(path).load()
    assert "delete it and join again" in str(refused.value)
    assert "c" * 43 not in str(refused.value)


@posix_only
def test_the_file_is_owner_only_in_an_owner_only_folder(tmp_path):
    store = CredentialStore(tmp_path / "state" / "credential.json")
    store.save(STORED)
    assert (tmp_path / "state").stat().st_mode & 0o777 == 0o700
    assert store.path.stat().st_mode & 0o777 == 0o600


@posix_only
def test_a_file_others_can_read_or_a_folder_others_can_write_is_refused(tmp_path):
    store = CredentialStore(tmp_path / "state" / "credential.json")
    store.save(STORED)
    store.path.chmod(0o644)
    with pytest.raises(CredentialFileError, match="chmod 600"):
        store.load()
    store.path.chmod(0o600)
    (tmp_path / "state").chmod(0o777)
    with pytest.raises(CredentialFileError, match="chmod 700"):
        store.load()
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest packages/follower/tests/test_credentials.py -q`
Expected: FAIL at import: `No module named 'swarmscribe_follower.credentials'`.

- [ ] **Step 3: Write the store**

Create `packages/follower/src/swarmscribe_follower/credentials.py`:

```python
"""The follower's credential, kept between starts (follower spec 5.3).

One JSON file in the state folder, readable by its owner only. On POSIX a file or folder
that another user owns, or that others can write to, is refused: it could have been planted.
On Windows the folder relies on the account's profile permissions (POSIX modes do not apply
there), as the admin CLI's sign-in cache does. Nothing here prints the credential, and its
repr is hidden."""

import json
import os
import stat
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

REPLACE_ATTEMPTS = 5  # Windows refuses a replace while a scanner or indexer has the file open


class CredentialFileError(Exception):
    """The credential file cannot be trusted or read; the message says what to do and never
    contains the file's content."""


@dataclass(frozen=True)
class Stored:
    leader_url: str
    follower_id: str
    credential: str = field(repr=False)
    device: str
    heartbeat_interval: int
    lease_seconds: int


_TYPES = {
    "leader_url": str,
    "follower_id": str,
    "credential": str,
    "device": str,
    "heartbeat_interval": int,
    "lease_seconds": int,
}


class CredentialStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _check_trust(self) -> None:
        if os.name == "nt":
            return
        me = os.getuid()
        folder = self.path.parent.stat()
        if folder.st_uid != me:
            raise CredentialFileError(
                f"the folder {self.path.parent} is owned by another user and will not be trusted"
            )
        if stat.S_IMODE(folder.st_mode) & 0o022:
            raise CredentialFileError(
                f"the folder {self.path.parent} is writable by others and will not be trusted;"
                f" run `chmod 700 {self.path.parent}`"
            )
        file = self.path.stat()
        if file.st_uid != me:
            raise CredentialFileError(
                f"{self.path} is owned by another user and will not be trusted; delete it"
                " and join again"
            )
        if stat.S_IMODE(file.st_mode) & 0o077:
            raise CredentialFileError(
                f"{self.path} is readable by others; run `chmod 600 {self.path}`"
            )

    def load(self) -> Stored | None:
        """The stored credential, or None when there is no file."""
        try:
            if not self.path.is_file():
                return None
            self._check_trust()
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise CredentialFileError(
                f"cannot read {self.path}: {exc.strerror or type(exc).__name__}"
            ) from None
        except ValueError:
            raise CredentialFileError(
                f"{self.path} is not a credential file; delete it and join again"
            ) from None
        if not isinstance(data, dict) or any(
            type(data.get(name)) is not kind for name, kind in _TYPES.items()
        ):
            raise CredentialFileError(
                f"{self.path} is not a credential file; delete it and join again"
            )
        return Stored(**{name: data[name] for name in _TYPES})

    def save(self, stored: Stored) -> None:
        """Write the file whole or not at all, never readable by others even for a moment."""
        folder = self.path.parent
        try:
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            temp = folder / (self.path.name + ".new")
            temp.unlink(missing_ok=True)
            handle = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                json.dump(asdict(stored), out)
            for attempt in range(REPLACE_ATTEMPTS):
                try:
                    os.replace(temp, self.path)
                    break
                except PermissionError:
                    if attempt == REPLACE_ATTEMPTS - 1:
                        raise
                    time.sleep(0.1)
        except OSError as exc:
            raise CredentialFileError(
                f"cannot write {self.path}: {exc.strerror or type(exc).__name__}"
            ) from None

    def delete(self) -> bool:
        try:
            self.path.unlink()
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise CredentialFileError(
                f"cannot delete {self.path}: {exc.strerror or type(exc).__name__}"
            ) from None
        return True
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest packages/follower/tests/test_credentials.py -q`
Expected: PASS. On Windows the two POSIX-mode tests are skipped.

- [ ] **Step 5: Commit**

```bash
git add packages/follower
git commit -m "Follower: the credential file, owner-only and written whole"
```

---

### Task 3: Scratch

**Files:**
- Create: `packages/follower/src/swarmscribe_follower/scratch.py`
- Test: `packages/follower/tests/test_scratch.py`

**Interfaces:**
- Consumes: nothing internal.
- Produces:
  - `Scratch(root: Path)` with `.root`, `prepare() -> None` (raises `ScratchNotOurs`), `wipe() -> None`, `job_dir(job_id: str) -> Path` (raises `ValueError` unless `job_id` is a UUID), `remove(entry: Path | None) -> None` (static; never raises).
  - `ScratchNotOurs`, `MARKER = ".swarmscribe-scratch"`.

- [ ] **Step 1: Write the failing tests**

Create `packages/follower/tests/test_scratch.py`:

```python
import os
import uuid

import pytest
from swarmscribe_follower.scratch import MARKER, Scratch, ScratchNotOurs

JOB = "2f0d1c1e-0000-4000-8000-000000000001"


def test_a_new_folder_is_created_and_marked(tmp_path):
    scratch = Scratch(tmp_path / "deep" / "scratch")
    scratch.prepare()
    assert (tmp_path / "deep" / "scratch" / MARKER).is_file()


def test_a_folder_with_someone_elses_files_is_refused_and_left_alone(tmp_path):
    (tmp_path / "thesis.docx").write_bytes(b"years of work")
    (tmp_path / "photos").mkdir()
    with pytest.raises(ScratchNotOurs, match="SWARMSCRIBE_FOLLOWER_SCRATCH_DIR"):
        Scratch(tmp_path).prepare()
    assert (tmp_path / "thesis.docx").read_bytes() == b"years of work"
    assert (tmp_path / "photos").is_dir()
    assert not (tmp_path / MARKER).exists()


def test_wiping_a_folder_that_was_never_prepared_deletes_nothing(tmp_path):
    (tmp_path / "thesis.docx").write_bytes(b"years of work")
    Scratch(tmp_path).wipe()
    assert (tmp_path / "thesis.docx").exists()


def test_what_a_crashed_run_left_is_wiped_at_the_next_start(tmp_path):
    scratch = Scratch(tmp_path)
    scratch.prepare()
    folder = scratch.job_dir(JOB)
    (folder / "source").write_bytes(b"a recording")
    (folder / "source.txt.tmp").write_text("half a transcript")
    (tmp_path / "stray").write_text("x")
    Scratch(tmp_path).prepare()  # the next start
    assert [entry.name for entry in tmp_path.iterdir()] == [MARKER]


def test_a_job_folder_is_fresh_and_named_by_the_job(tmp_path):
    scratch = Scratch(tmp_path)
    scratch.prepare()
    folder = scratch.job_dir(JOB)
    (folder / "source").write_bytes(b"first attempt")
    again = scratch.job_dir(JOB)
    assert again == tmp_path / f"job-{JOB}"
    assert list(again.iterdir()) == []


@pytest.mark.parametrize("job_id", ["../../etc", "job", "", "a/b", "..", "C:\\x"])
def test_a_job_id_that_is_not_a_uuid_never_becomes_a_path(tmp_path, job_id):
    scratch = Scratch(tmp_path / "scratch")
    scratch.prepare()
    with pytest.raises(ValueError):
        scratch.job_dir(job_id)
    assert [entry.name for entry in (tmp_path / "scratch").iterdir()] == [MARKER]


def test_remove_deletes_a_link_and_not_what_it_points_at(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    scratch = Scratch(tmp_path / "scratch")
    scratch.prepare()
    link = tmp_path / "scratch" / "link"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this account cannot create symbolic links")
    scratch.wipe()
    assert not link.exists()
    assert (outside / "keep.txt").read_text() == "keep"


def test_remove_never_raises(tmp_path):
    Scratch.remove(None)
    Scratch.remove(tmp_path / "missing")
    Scratch.remove(tmp_path / f"job-{uuid.uuid4()}")
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest packages/follower/tests/test_scratch.py -q`
Expected: FAIL at import: `No module named 'swarmscribe_follower.scratch'`.

- [ ] **Step 3: Write it**

Create `packages/follower/src/swarmscribe_follower/scratch.py`:

```python
"""The scratch folder: where a job's recording and outputs live, and only while it runs
(follower spec 5.8).

The follower wipes this folder, so it must be sure the folder is its own. It leaves a marker
file there, and refuses a folder that holds anything but has no marker: a wrong setting must
never delete someone's files. Wiping is deletion, not secure erasure."""

import shutil
import uuid
from pathlib import Path

MARKER = ".swarmscribe-scratch"


class ScratchNotOurs(Exception):
    """The scratch folder holds files and no marker; the message names the folder."""


class Scratch:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def prepare(self) -> None:
        """Create the folder, or take it over if it is empty or carries the marker; then
        wipe whatever a previous run left."""
        self.root.mkdir(parents=True, exist_ok=True)
        marker = self.root / MARKER
        if not marker.is_file() and any(self.root.iterdir()):
            raise ScratchNotOurs(
                f"the scratch folder {self.root} holds files that are not the follower's;"
                " point SWARMSCRIBE_FOLLOWER_SCRATCH_DIR at an empty folder"
            )
        marker.write_text("SwarmScribe follower scratch: everything here is deleted.\n")
        self.wipe()

    def wipe(self) -> None:
        """Delete everything but the marker. Does nothing if the folder was never prepared."""
        if not (self.root / MARKER).is_file():
            return
        for entry in self.root.iterdir():
            if entry.name != MARKER:
                self.remove(entry)

    def job_dir(self, job_id: str) -> Path:
        """A fresh folder for one job. The id comes from the leader and becomes a folder
        name, so it must be a UUID and nothing else."""
        folder = self.root / f"job-{uuid.UUID(job_id)}"
        self.remove(folder)
        folder.mkdir()
        return folder

    @staticmethod
    def remove(entry: Path | None) -> None:
        """Delete a file or a folder; a link is removed, never followed. Never raises: a
        file that cannot be deleted now is deleted by the next start."""
        if entry is None:
            return
        try:
            if entry.is_symlink() or not entry.is_dir():
                entry.unlink(missing_ok=True)
            else:
                shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            pass
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest packages/follower/tests/test_scratch.py -q`
Expected: PASS. The symbolic-link test skips itself on an account that cannot create links (Windows without Developer Mode).

- [ ] **Step 5: Commit**

```bash
git add packages/follower
git commit -m "Follower: a scratch folder it wipes only when it is its own"
```

---

### Task 4: The leader client

**Files:**
- Create: `packages/follower/src/swarmscribe_follower/leader.py`
- Test: `packages/follower/tests/test_leader_client.py`

**Interfaces:**
- Consumes: the protocol's models and `DIRECTIVE_HEADER`, `PROTOCOL_VERSION`; `FOLLOWER_VERSION`.
- Produces:
  - `Transient(status: int | None, retry_after: float | None, what: str)` — try again; `status` is `None` when nothing answered. Raised for a transport error and for `429, 500, 502, 503, 504`.
  - `Refused(status: int, code: str, message: str)` — the leader said no.
  - `Interrupted` — a wait between retries was cut short.
  - `NoWork(retry_after: float, draining: bool)`.
  - `retrying(call, *, pause: Callable[[float], bool], give_up: Callable[[Transient, int], bool] | None = None, first=1.0, cap=60.0, rng=random.random)` — calls until no `Transient`; `pause(seconds)` returning `True` raises `Interrupted`; `give_up(error, failures)` returning `True` re-raises the `Transient`.
  - `LeaderClient(base_url, *, credential=None, transport=None, verify=True, timeout=30.0)` with attribute `credential` and methods `register(join_token, capabilities) -> RegisterResponse`, `claim() -> ClaimResponse | NoWork`, `heartbeat(job_id, lease_id, progress) -> Directive`, `links(job_id, lease_id) -> JobLinks`, `submit(job_id, lease_id, checksums) -> None`, `fail(job_id, lease_id, code, reason, retryable) -> None`, `release(job_id, lease_id) -> None`, `deregister() -> None`, `healthy() -> bool`, `close() -> None`.
  - Helpers reused by Task 5: `TRANSIENT_STATUSES`, `refusal_of(response) -> Refused`, `retry_after_of(response) -> float | None`, `checked(response)`.

- [ ] **Step 1: Write the failing tests**

Create `packages/follower/tests/test_leader_client.py`:

```python
import json

import httpx
import pytest
from swarmscribe_follower.leader import (
    Interrupted,
    LeaderClient,
    NoWork,
    Refused,
    Transient,
    retrying,
)
from swarmscribe_protocol import Capabilities, OutputChecksums

JOB = "2f0d1c1e-0000-4000-8000-000000000001"
LEASE = "2f0d1c1e-0000-4000-8000-000000000002"
CAPABILITIES = Capabilities(
    device="cpu", models=["tiny.en"], engine_version="0.1.0", pool="default"
)
LINK = {"url": "https://leader.test/v1/files/token", "method": "PUT"}
CLAIM = {
    "job_id": JOB,
    "lease_id": LEASE,
    "download_url": {"url": "https://leader.test/v1/files/in", "method": "GET"},
    "upload_urls": {"txt": LINK, "srt": LINK, "segments_json": LINK},
    "settings": {"model": "tiny.en", "compute_type": "int8"},
    "vocabulary": {"version": 0},
    "source_version": "10-1-1",
}


def client_for(handler, credential="the-credential"):
    seen = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = LeaderClient(
        "https://leader.test/", credential=credential, transport=httpx.MockTransport(recording)
    )
    return client, seen


def test_register_sends_the_token_and_no_bearer():
    answer = {
        "follower_id": "f1",
        "credential": "new-credential",
        "heartbeat_interval": 30,
        "lease_seconds": 120,
    }
    client, seen = client_for(lambda request: httpx.Response(200, json=answer), credential=None)
    registered = client.register("join-token", CAPABILITIES)
    assert (registered.credential, registered.heartbeat_interval) == ("new-credential", 30)
    (request,) = seen
    assert request.url == "https://leader.test/v1/followers/register"
    assert "authorization" not in request.headers
    body = json.loads(request.content)
    assert (body["join_token"], body["protocol_version"]) == ("join-token", 1)
    assert request.headers["user-agent"].startswith("swarmscribe-follower/")


def test_every_other_call_carries_the_bearer_and_its_lease():
    client, seen = client_for(lambda request: httpx.Response(204))
    h = "a" * 64
    client.submit(JOB, LEASE, OutputChecksums(source=h, txt=h, srt=h, segments_json=h))
    client.fail(JOB, LEASE, "other", "x" * 5000, True)
    client.release(JOB, LEASE)
    client.deregister()
    assert [request.url.path for request in seen] == [
        f"/v1/jobs/{JOB}/submit",
        f"/v1/jobs/{JOB}/fail",
        f"/v1/jobs/{JOB}/release",
        "/v1/followers/deregister",
    ]
    assert all(request.headers["authorization"] == "Bearer the-credential" for request in seen)
    assert all(json.loads(r.content)["lease_id"] == LEASE for r in seen[:3])
    assert len(json.loads(seen[1].content)["reason"]) == 2000  # the protocol's limit
    assert seen[3].content == b""


def test_a_call_without_a_credential_never_leaves_the_machine():
    client, seen = client_for(lambda request: httpx.Response(204), credential=None)
    with pytest.raises(Refused) as refused:
        client.claim()
    assert (refused.value.status, seen) == (401, [])


def test_a_claim_with_work_is_parsed():
    client, _ = client_for(lambda request: httpx.Response(200, json=CLAIM))
    claimed = client.claim()
    assert (claimed.job_id, claimed.settings.model) == (JOB, "tiny.en")


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"Retry-After": "7"}, NoWork(retry_after=7.0, draining=False)),
        ({}, NoWork(retry_after=10.0, draining=False)),
        ({"Retry-After": "soon"}, NoWork(retry_after=10.0, draining=False)),
        (
            {"Retry-After": "10", "X-SwarmScribe-Directive": "drain"},
            NoWork(retry_after=10.0, draining=True),
        ),
        ({"X-SwarmScribe-Directive": "something-new"}, NoWork(retry_after=10.0, draining=False)),
    ],
)
def test_no_work_says_how_long_to_wait_and_whether_the_follower_is_draining(headers, expected):
    client, _ = client_for(lambda request: httpx.Response(204, headers=headers))
    assert client.claim() == expected


def test_heartbeat_returns_the_directive_and_sends_progress():
    client, seen = client_for(lambda request: httpx.Response(200, json={"directive": "cancel"}))
    assert client.heartbeat(JOB, LEASE, 0.25) == "cancel"
    assert json.loads(seen[0].content) == {"lease_id": LEASE, "progress": 0.25}


def test_links_are_parsed():
    links = {"download_url": CLAIM["download_url"], "upload_urls": CLAIM["upload_urls"]}
    client, _ = client_for(lambda request: httpx.Response(200, json=links))
    assert client.links(JOB, LEASE).upload_urls.txt.method == "PUT"


@pytest.mark.parametrize(
    ("status", "headers", "retry_after"),
    [
        (503, {"Retry-After": "10"}, 10.0),
        (502, {}, None),
        (504, {}, None),
        (500, {}, None),
        (429, {"Retry-After": "42"}, 42.0),
    ],
)
def test_come_back_later_is_transient(status, headers, retry_after):
    body = {"code": "unavailable", "message": "service temporarily unavailable"}
    client, _ = client_for(lambda request: httpx.Response(status, json=body, headers=headers))
    with pytest.raises(Transient) as error:
        client.claim()
    assert (error.value.status, error.value.retry_after) == (status, retry_after)


def test_nobody_answering_is_transient_and_never_names_the_url():
    def unreachable(request):
        raise httpx.ConnectError(f"cannot connect to {request.url}")

    client, _ = client_for(unreachable)
    with pytest.raises(Transient) as error:
        client.heartbeat(JOB, LEASE, None)
    assert error.value.status is None
    assert "leader.test" not in str(error.value)


@pytest.mark.parametrize(
    ("status", "body", "code"),
    [
        (409, {"code": "stale_lease", "message": "not leased to you"}, "stale_lease"),
        (403, {"code": "forbidden", "message": "this follower has been revoked"}, "forbidden"),
        (401, {"code": "unauthorized", "message": "unknown follower credential"}, "unauthorized"),
        (422, {"code": "invalid_request", "message": "lease_id: required"}, "invalid_request"),
        (404, None, "http_404"),
        (302, None, "http_302"),
    ],
)
def test_a_refusal_carries_the_leaders_code(status, body, code):
    def answer(request):
        if body is None:
            return httpx.Response(status, text="<html>proxy</html>", headers={"Location": "/x"})
        return httpx.Response(status, json=body)

    client, seen = client_for(answer)
    with pytest.raises(Refused) as refused:
        client.release(JOB, LEASE)
    assert (refused.value.status, refused.value.code) == (status, code)
    assert len(seen) == 1  # a redirect is not followed


def test_an_answer_that_is_not_the_protocol_is_a_refusal_not_a_crash():
    client, _ = client_for(lambda request: httpx.Response(200, json={"job_id": 5}))
    with pytest.raises(Refused) as refused:
        client.claim()
    assert refused.value.code == "invalid_answer"


def test_a_job_id_is_one_path_segment_whatever_it_holds():
    client, seen = client_for(lambda request: httpx.Response(204))
    client.release("../../v1/followers/deregister", LEASE)
    assert seen[0].url.raw_path == b"/v1/jobs/..%2F..%2Fv1%2Ffollowers%2Fderegister/release"


def test_healthy_asks_healthz_and_never_raises():
    client, seen = client_for(lambda request: httpx.Response(200, json={"status": "ok"}))
    assert client.healthy() is True
    assert (seen[0].method, seen[0].url.path) == ("GET", "/healthz")

    def unreachable(request):
        raise httpx.ConnectError("no")

    assert client_for(unreachable)[0].healthy() is False


# --- retrying ----------------------------------------------------------------------------


class Flaky:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_retrying_waits_with_a_doubling_capped_delay_and_full_jitter():
    waits = []
    call = Flaky(*[Transient(503, None, "x")] * 8, "done")
    result = retrying(call, pause=lambda s: waits.append(s) or False, rng=lambda: 1.0)
    assert result == "done"
    assert waits == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0]
    halved = []
    retrying(
        Flaky(Transient(None, None, "x"), "done"),
        pause=lambda s: halved.append(s) or False,
        rng=lambda: 0.5,
    )
    assert halved == [0.5]


def test_retrying_waits_what_the_leader_asked_for():
    waits = []
    call = Flaky(Transient(503, 10.0, "x"), Transient(503, 30.0, "x"), "done")
    assert retrying(call, pause=lambda s: waits.append(s) or False) == "done"
    assert waits == [10.0, 30.0]


def test_retrying_stops_when_asked_to():
    call = Flaky(Transient(503, None, "x"), "never reached")
    with pytest.raises(Interrupted):
        retrying(call, pause=lambda seconds: True)
    assert call.calls == 1


def test_retrying_can_give_up_and_never_retries_a_refusal():
    call = Flaky(*[Transient(500, None, "x")] * 5)
    with pytest.raises(Transient):
        retrying(
            call,
            pause=lambda seconds: False,
            give_up=lambda error, failures: error.status == 500 and failures >= 3,
        )
    assert call.calls == 3
    refused = Flaky(Refused(409, "stale_lease", "no"), "never reached")
    with pytest.raises(Refused):
        retrying(refused, pause=lambda seconds: False)
    assert refused.calls == 1
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest packages/follower/tests/test_leader_client.py -q`
Expected: FAIL at import: `No module named 'swarmscribe_follower.leader'`.

- [ ] **Step 3: Write the client**

Create `packages/follower/src/swarmscribe_follower/leader.py`:

```python
"""The leader's follower API, one method per route (follower spec 5.1).

Each method makes one request and either returns or raises:

- `Transient`: nobody answered, or the leader said to come back (429, 500, 502, 503, 504).
  The caller retries, usually with `retrying`.
- `Refused`: the leader answered and said no (any other status). The caller decides what
  that means for the job or for the follower.

The bearer credential is attached here and nowhere else, so it reaches only the leader's
/v1/followers and /v1/jobs routes; links are fetched by `transfer.Links`, which has none.
Redirects are never followed."""

import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar
from urllib.parse import quote

import httpx
from pydantic import ValidationError
from swarmscribe_protocol import (
    DIRECTIVE_HEADER,
    PROTOCOL_VERSION,
    Capabilities,
    ClaimResponse,
    Directive,
    FailRequest,
    FailureCode,
    HeartbeatRequest,
    HeartbeatResponse,
    JobLinks,
    LinksRequest,
    OutputChecksums,
    RegisterRequest,
    RegisterResponse,
    ReleaseRequest,
    SubmitRequest,
)

from . import FOLLOWER_VERSION

TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})
DEFAULT_NO_WORK_SECONDS = 10.0
Result = TypeVar("Result")


class Transient(Exception):
    """Try again later. `status` is None when nothing answered."""

    def __init__(self, status: int | None, retry_after: float | None, what: str) -> None:
        super().__init__(what)
        self.status = status
        self.retry_after = retry_after


class Refused(Exception):
    """The leader (or a storage service) answered with a refusal."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message


class Interrupted(Exception):
    """A wait between retries was cut short because the caller was asked to stop."""


@dataclass(frozen=True)
class NoWork:
    retry_after: float
    draining: bool


def retry_after_of(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after", "")
    return float(value) if value.isdigit() else None


def refusal_of(response: httpx.Response) -> Refused:
    """The `{code, message}` of an error answer; something sensible when it has none (a
    proxy's HTML page, a storage service's XML)."""
    code, message = f"http_{response.status_code}", ""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        if isinstance(body.get("code"), str):
            code = body["code"]
        if isinstance(body.get("message"), str):
            message = body["message"][:500]
    return Refused(response.status_code, code, message)


def checked(response: httpx.Response) -> httpx.Response:
    """The response if it is a success; otherwise the exception it means."""
    if response.status_code in TRANSIENT_STATUSES:
        raise Transient(
            response.status_code, retry_after_of(response), f"status {response.status_code}"
        )
    if not 200 <= response.status_code < 300:
        raise refusal_of(response)
    return response


def retrying(
    call: Callable[[], Result],
    *,
    pause: Callable[[float], bool],
    give_up: Callable[[Transient, int], bool] | None = None,
    first: float = 1.0,
    cap: float = 60.0,
    rng: Callable[[], float] = random.random,
) -> Result:
    """Call until it stops raising Transient. Waits the leader's Retry-After when it sent
    one, otherwise a random part of a delay that doubles from `first` to `cap` (full
    jitter, so a fleet does not come back in step). `pause(seconds)` waits and returns True
    if the caller must stop, which raises Interrupted. `give_up(error, failures)` returning
    True re-raises the Transient."""
    delay, failures = first, 0
    while True:
        try:
            return call()
        except Transient as exc:
            failures += 1
            if give_up is not None and give_up(exc, failures):
                raise
            wait = exc.retry_after if exc.retry_after is not None else delay * rng()
            delay = min(cap, delay * 2)
            if pause(wait):
                raise Interrupted() from None


class LeaderClient:
    def __init__(
        self,
        base_url: str,
        *,
        credential: str | None = None,
        transport: httpx.BaseTransport | None = None,
        verify: Any = True,
        timeout: float = 30.0,
    ) -> None:
        self.credential = credential
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            transport=transport,
            verify=verify,
            timeout=timeout,
            follow_redirects=False,
            headers={"User-Agent": f"swarmscribe-follower/{FOLLOWER_VERSION}"},
        )

    def close(self) -> None:
        self._http.close()

    def _post(self, path: str, body: Any = None, *, authenticated: bool = True) -> httpx.Response:
        headers = {}
        if authenticated:
            if not self.credential:
                raise Refused(401, "unauthorized", "this follower has no credential")
            headers["Authorization"] = f"Bearer {self.credential}"
        json = body.model_dump(mode="json") if body is not None else None
        try:
            response = self._http.post(path, json=json, headers=headers)
        except httpx.HTTPError as exc:
            # The class only: the text of a transport error can hold the URL.
            raise Transient(None, None, type(exc).__name__) from None
        return checked(response)

    @staticmethod
    def _parsed(model: type[Result], response: httpx.Response) -> Result:
        try:
            return model.model_validate_json(response.content)
        except ValidationError:
            raise Refused(
                response.status_code, "invalid_answer", f"not a {model.__name__}"
            ) from None

    def healthy(self) -> bool:
        """Whether the leader answers at all (its /healthz). Changes nothing."""
        try:
            return self._http.get("/healthz").status_code == 200
        except httpx.HTTPError:
            return False

    def register(self, join_token: str, capabilities: Capabilities) -> RegisterResponse:
        body = RegisterRequest(
            join_token=join_token, protocol_version=PROTOCOL_VERSION, capabilities=capabilities
        )
        answer = self._post("/v1/followers/register", body, authenticated=False)
        return self._parsed(RegisterResponse, answer)

    def claim(self) -> ClaimResponse | NoWork:
        response = self._post("/v1/jobs/claim")
        if response.status_code == 204:
            wait = retry_after_of(response)
            return NoWork(
                retry_after=DEFAULT_NO_WORK_SECONDS if wait is None else wait,
                draining=response.headers.get(DIRECTIVE_HEADER, "") == "drain",
            )
        return self._parsed(ClaimResponse, response)

    @staticmethod
    def _job(job_id: str, action: str) -> str:
        # The id comes from the leader and becomes a path segment: it is quoted whole.
        return f"/v1/jobs/{quote(job_id, safe='')}/{action}"

    def heartbeat(self, job_id: str, lease_id: str, progress: float | None) -> Directive:
        body = HeartbeatRequest(lease_id=lease_id, progress=progress)
        answer = self._post(self._job(job_id, "heartbeat"), body)
        return self._parsed(HeartbeatResponse, answer).directive

    def links(self, job_id: str, lease_id: str) -> JobLinks:
        answer = self._post(self._job(job_id, "links"), LinksRequest(lease_id=lease_id))
        return self._parsed(JobLinks, answer)

    def submit(self, job_id: str, lease_id: str, checksums: OutputChecksums) -> None:
        body = SubmitRequest(lease_id=lease_id, checksums=checksums)
        self._post(self._job(job_id, "submit"), body)

    def fail(
        self, job_id: str, lease_id: str, code: FailureCode, reason: str, retryable: bool
    ) -> None:
        body = FailRequest(lease_id=lease_id, code=code, reason=reason[:2000], retryable=retryable)
        self._post(self._job(job_id, "fail"), body)

    def release(self, job_id: str, lease_id: str) -> None:
        self._post(self._job(job_id, "release"), ReleaseRequest(lease_id=lease_id))

    def deregister(self) -> None:
        self._post("/v1/followers/deregister")
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest packages/follower/tests/test_leader_client.py -q`
Expected: PASS.

Run: `uv run ruff check packages/follower`
Expected: `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add packages/follower
git commit -m "Follower: the leader client, one method per route, and the one retry loop"
```

---

### Task 5: Transfers through links

**Files:**
- Create: `packages/follower/src/swarmscribe_follower/transfer.py`
- Test: `packages/follower/tests/test_transfer.py`

**Interfaces:**
- Consumes: `Link` (protocol); `Transient`, `Refused`, `TRANSIENT_STATUSES`, `refusal_of`, `retry_after_of` (Task 4).
- Produces:
  - `Links(*, transport=None, verify=True, timeout=120.0)` with `download(link: Link, destination: Path, check: Callable[[], None]) -> str` (the SHA-256; `check` is called between chunks and may raise), `upload(link: Link, path: Path) -> None`, `close()`.
  - Exceptions: `SourceChanged` (download `412`, `404`, or code `source_changed`), `LinkExpired` (`403`), `LeaseLost` (`409 stale_lease` on an upload or a download), `OutputTooLarge` (upload `413`), `OutOfSpace` (the recording does not fit on scratch); `Transient` for transport errors, `5xx`, `429`, a short body, and an upload's `409` that is not `stale_lease`; `Refused` otherwise and for a link with the wrong method.

- [ ] **Step 1: Write the failing tests**

Create `packages/follower/tests/test_transfer.py`:

```python
import hashlib

import httpx
import pytest
from swarmscribe_follower import transfer
from swarmscribe_follower.leader import Refused, Transient
from swarmscribe_follower.transfer import (
    LeaseLost,
    LinkExpired,
    Links,
    OutOfSpace,
    OutputTooLarge,
    SourceChanged,
)
from swarmscribe_protocol import Link

GET = Link(url="https://storage.test/in?sig=SECRET", method="GET", headers={"If-Match": "etag-1"})
PUT = Link(
    url="https://storage.test/out?sig=SECRET", method="PUT", headers={"x-ms-blob-type": "BlockBlob"}
)
AUDIO = bytes(range(256)) * 5000  # 1.28 MB: more than one chunk


def links_for(handler):
    seen = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return Links(transport=httpx.MockTransport(recording)), seen


def error(status, code, **headers):
    return httpx.Response(status, json={"code": code, "message": "no"}, headers=headers)


def test_a_download_is_streamed_to_disk_hashed_and_sent_the_links_headers(tmp_path):
    links, seen = links_for(lambda request: httpx.Response(200, content=AUDIO))
    checks = []
    digest = links.download(GET, tmp_path / "source", lambda: checks.append(1))
    assert digest == hashlib.sha256(AUDIO).hexdigest()
    assert (tmp_path / "source").read_bytes() == AUDIO
    assert len(checks) >= 2
    (request,) = seen
    assert request.headers["if-match"] == "etag-1"
    assert "authorization" not in request.headers


def test_a_download_can_be_stopped_between_chunks(tmp_path):
    class Stop(Exception):
        pass

    def stop():
        raise Stop()

    links, _ = links_for(lambda request: httpx.Response(200, content=AUDIO))
    with pytest.raises(Stop):
        links.download(GET, tmp_path / "source", stop)


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (error(412, "source_changed"), SourceChanged),
        (httpx.Response(412, text="<Error><Code>ConditionNotMet</Code></Error>"), SourceChanged),
        (error(404, "not_found"), SourceChanged),
        (error(403, "forbidden"), LinkExpired),
        (error(409, "stale_lease"), LeaseLost),
        (error(503, "unavailable", **{"Retry-After": "30"}), Transient),
        (error(400, "invalid_key"), Refused),
    ],
)
def test_a_refused_download_says_what_it_means(tmp_path, response, expected):
    links, _ = links_for(lambda request: response)
    with pytest.raises(expected) as raised:
        links.download(GET, tmp_path / "source", lambda: None)
    assert "SECRET" not in str(raised.value)
    if expected is Transient:
        assert raised.value.retry_after == 30.0


def test_a_download_that_ends_early_or_breaks_is_transient(tmp_path):
    short = httpx.Response(200, content=AUDIO[:1000], headers={"Content-Length": "999999"})
    links, _ = links_for(lambda request: short)
    with pytest.raises(Transient):
        links.download(GET, tmp_path / "source", lambda: None)

    def broken(request):
        raise httpx.ReadError(f"connection lost reading {request.url}")

    links, _ = links_for(broken)
    with pytest.raises(Transient) as raised:
        links.download(GET, tmp_path / "source", lambda: None)
    assert "SECRET" not in str(raised.value)


def test_a_recording_that_does_not_fit_on_scratch_is_not_started(tmp_path, monkeypatch):
    class Usage:
        free = 10 * 1024 * 1024

    monkeypatch.setattr(transfer.shutil, "disk_usage", lambda path: Usage)
    big = httpx.Response(200, content=b"x", headers={"Content-Length": str(11 * 1024 * 1024)})
    links, _ = links_for(lambda request: big)
    with pytest.raises(OutOfSpace):
        links.download(GET, tmp_path / "source", lambda: None)
    assert not (tmp_path / "source").exists()


@pytest.mark.parametrize("status", [200, 201, 204])
def test_an_upload_sends_the_file_with_its_length_and_the_links_headers(tmp_path, status):
    path = tmp_path / "source.txt"
    path.write_bytes(b"Welcome.\n")
    links, seen = links_for(lambda request: httpx.Response(status))
    links.upload(PUT, path)
    (request,) = seen
    assert (request.method, request.content) == ("PUT", b"Welcome.\n")
    assert request.headers["content-length"] == "9"
    assert request.headers["x-ms-blob-type"] == "BlockBlob"
    assert "authorization" not in request.headers


def test_an_empty_output_is_uploaded_as_an_empty_body(tmp_path):
    path = tmp_path / "source.txt"
    path.write_bytes(b"")
    links, seen = links_for(lambda request: httpx.Response(201))
    links.upload(PUT, path)
    assert (seen[0].content, seen[0].headers["content-length"]) == (b"", "0")


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (error(409, "stale_lease"), LeaseLost),
        (error(409, "conflict", **{"Retry-After": "5"}), Transient),
        (error(403, "forbidden"), LinkExpired),
        (httpx.Response(403, text="<Error><Code>AuthenticationFailed</Code></Error>"), LinkExpired),
        (error(413, "too_large"), OutputTooLarge),
        (error(503, "unavailable"), Transient),
        (error(400, "invalid_key"), Refused),
    ],
)
def test_a_refused_upload_says_what_it_means(tmp_path, response, expected):
    path = tmp_path / "source.txt"
    path.write_bytes(b"x")
    links, _ = links_for(lambda request: response)
    with pytest.raises(expected) as raised:
        links.upload(PUT, path)
    assert "SECRET" not in str(raised.value)


def test_a_link_for_the_wrong_method_is_never_used(tmp_path):
    path = tmp_path / "source.txt"
    path.write_bytes(b"x")
    links, seen = links_for(lambda request: httpx.Response(200))
    with pytest.raises(Refused):
        links.download(PUT, tmp_path / "source", lambda: None)
    with pytest.raises(Refused):
        links.upload(GET, path)
    assert seen == []


def test_a_redirect_is_not_followed(tmp_path):
    links, seen = links_for(
        lambda request: httpx.Response(302, headers={"Location": "https://elsewhere.test/"})
    )
    with pytest.raises(Refused):
        links.download(GET, tmp_path / "source", lambda: None)
    assert len(seen) == 1
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest packages/follower/tests/test_transfer.py -q`
Expected: FAIL at import: `No module named 'swarmscribe_follower.transfer'`.

- [ ] **Step 3: Write it**

Create `packages/follower/src/swarmscribe_follower/transfer.py`:

```python
"""Download a recording and upload outputs through the leader's links (follower spec 5.4).

A link is a URL, a method and headers, the same for every storage backend. The client here
has no credential and is never given one: a link is its own authority. Redirects are not
followed, and no URL is ever logged or put in an exception (a link's URL carries its token).
"""

import hashlib
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from swarmscribe_protocol import Link

from .leader import TRANSIENT_STATUSES, Refused, Transient, refusal_of, retry_after_of

CHUNK_BYTES = 1024 * 1024
SPARE_BYTES = 64 * 1024 * 1024  # room for the outputs beside the recording
IN_USE_RETRY_SECONDS = 5.0


class SourceChanged(Exception):
    """The recording is no longer the file that was ingested (changed, or gone)."""


class LinkExpired(Exception):
    """The link was refused as expired or invalid; a fresh one may work."""


class LeaseLost(Exception):
    """The storage refused the upload because the lease it belongs to has ended."""


class OutputTooLarge(Exception):
    """An output is larger than the storage accepts."""


class OutOfSpace(Exception):
    """The recording does not fit on the scratch disk."""


class Links:
    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        verify: Any = True,
        timeout: float = 120.0,
    ) -> None:
        self._http = httpx.Client(
            transport=transport, verify=verify, timeout=timeout, follow_redirects=False
        )

    def close(self) -> None:
        self._http.close()

    @staticmethod
    def _expect(link: Link, method: str) -> None:
        if link.method != method:
            raise Refused(0, "invalid_link", f"expected a {method} link")

    def download(self, link: Link, destination: Path, check: Callable[[], None]) -> str:
        """Stream the recording to `destination` and return its SHA-256. `check` is called
        between chunks and raises to stop the download."""
        self._expect(link, "GET")
        digest = hashlib.sha256()
        try:
            with self._http.stream("GET", link.url, headers=link.headers) as response:
                if response.status_code != 200:
                    response.read()
                    raise self._download_error(response)
                declared = response.headers.get("content-length", "")
                expected = int(declared) if declared.isdigit() else None
                free = shutil.disk_usage(destination.parent).free
                if expected is not None and expected + SPARE_BYTES > free:
                    raise OutOfSpace(
                        f"the recording is {expected} bytes; scratch has {free} bytes free"
                    )
                received = 0
                with destination.open("wb") as out:
                    for chunk in response.iter_bytes(CHUNK_BYTES):
                        check()
                        out.write(chunk)
                        digest.update(chunk)
                        received += len(chunk)
        except httpx.HTTPError as exc:
            raise Transient(None, None, type(exc).__name__) from None
        if expected is not None and received != expected:
            raise Transient(None, None, "the download ended early")
        return digest.hexdigest()

    @staticmethod
    def _download_error(response: httpx.Response) -> Exception:
        refusal = refusal_of(response)
        if response.status_code in (404, 412) or refusal.code == "source_changed":
            return SourceChanged("the recording changed or was removed after it was ingested")
        if response.status_code == 403:
            return LinkExpired("the download link was refused")
        if response.status_code == 409 and refusal.code == "stale_lease":
            return LeaseLost("the lease this download belongs to has ended")
        if response.status_code in TRANSIENT_STATUSES:
            return Transient(response.status_code, retry_after_of(response), "download")
        return refusal

    def upload(self, link: Link, path: Path) -> None:
        """Send one output file. Outputs are text and small (the leader's own storage takes
        at most 512 MiB), so the file is read whole and sent with its length."""
        self._expect(link, "PUT")
        try:
            response = self._http.put(link.url, headers=link.headers, content=path.read_bytes())
        except httpx.HTTPError as exc:
            raise Transient(None, None, type(exc).__name__) from None
        if 200 <= response.status_code < 300:
            return
        refusal = refusal_of(response)
        if response.status_code == 403:
            raise LinkExpired("the upload link was refused")
        if response.status_code == 409 and refusal.code == "stale_lease":
            raise LeaseLost("the lease this upload belongs to has ended")
        if response.status_code == 409:  # the file is in use: the leader says to retry
            wait = retry_after_of(response)
            raise Transient(409, IN_USE_RETRY_SECONDS if wait is None else wait, "upload")
        if response.status_code == 413:
            size = path.stat().st_size
            raise OutputTooLarge(f"an output of {size} bytes was refused as too large")
        if response.status_code in TRANSIENT_STATUSES:
            raise Transient(response.status_code, retry_after_of(response), "upload")
        raise refusal
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest packages/follower/tests/test_transfer.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/follower
git commit -m "Follower: download and upload through links, without a credential"
```

---

### Task 6: Device and models

**Files:**
- Create: `packages/follower/src/swarmscribe_follower/device.py`, `packages/follower/src/swarmscribe_follower/models.py`
- Test: `packages/follower/tests/test_models.py`

**Interfaces:**
- Consumes: the engine's `resolve_device`, `DeviceChoice`, `DeviceUnavailableError`, `ENGINE_VERSION`, `Transcriber` (with F0's `warm_up()` and `close()`), `TranscribeSettings`; the protocol's `Capabilities`.
- Produces:
  - `device.Probe(choice: DeviceChoice, gpu_name: str | None = None, gpu_memory_mb: int | None = None)`.
  - `device.probe(preference, *, resolve=resolve_device, smi=nvidia_smi) -> Probe` (raises `DeviceUnavailableError`).
  - `device.nvidia_smi() -> tuple[str | None, int | None]`, `device.cached_models(model_dir: Path | None) -> list[str]`, `device.capabilities(found: Probe, pool: str, models: list[str]) -> Capabilities`.
  - `models.ModelHost(device, *, factory=Transcriber, allowed=frozenset())` with `get(model: str, compute_type: str) -> transcriber` (loads, warms up, switches), `close()`, property `loaded: tuple[str, str] | None`.
  - `models.ModelUnavailable`, `models.OutOfMemory`, `models.is_out_of_memory(error) -> bool`, `models.MODEL_NAME`.

- [ ] **Step 1: Write the failing tests**

Create `packages/follower/tests/test_models.py`:

```python
import pytest
from swarmscribe_engine import DeviceChoice, DeviceUnavailableError
from swarmscribe_follower import device
from swarmscribe_follower.device import Probe, cached_models, capabilities, probe
from swarmscribe_follower.models import ModelHost, ModelUnavailable, OutOfMemory

CUDA = DeviceChoice(device="cuda", model="large-v3", compute_type="float16")
CPU = DeviceChoice(device="cpu", model="distil-large-v3", compute_type="int8")


class Loaded:
    def __init__(self, log, settings, warm_up_error=None):
        self.log, self.settings, self.warm_up_error = log, settings, warm_up_error
        log.append(("load", settings.model, settings.compute_type, settings.device))

    def warm_up(self):
        self.log.append(("warm_up", self.settings.model))
        if self.warm_up_error is not None:
            raise self.warm_up_error

    def close(self):
        self.log.append(("close", self.settings.model))


def host(log, **kwargs):
    return ModelHost("cpu", factory=lambda settings: Loaded(log, settings), **kwargs)


def test_a_model_is_loaded_warmed_up_once_and_reused():
    log = []
    models = host(log)
    first = models.get("distil-large-v3", "int8")
    assert models.get("distil-large-v3", "int8") is first
    assert log == [("load", "distil-large-v3", "int8", "cpu"), ("warm_up", "distil-large-v3")]
    assert models.loaded == ("distil-large-v3", "int8")


def test_another_model_or_compute_type_closes_the_old_one_first():
    log = []
    models = host(log)
    models.get("distil-large-v3", "int8")
    models.get("tiny.en", "int8")
    models.get("tiny.en", "float32")
    assert [entry[0] for entry in log] == [
        "load", "warm_up", "close", "load", "warm_up", "close", "load", "warm_up",
    ]
    assert models.loaded == ("tiny.en", "float32")
    models.close()
    models.close()
    assert (models.loaded, log[-1]) == (None, ("close", "tiny.en"))


@pytest.mark.parametrize(
    "name",
    ["/models/large-v3", "../large-v3", "C:\\models\\x", "a/b/c", "", ".hidden", "x" * 65, "a b"],
)
def test_a_model_name_that_could_be_a_path_is_never_loaded(name):
    log = []
    with pytest.raises(ModelUnavailable):
        host(log).get(name, "int8")
    assert log == []


def test_plain_names_and_repositories_are_accepted():
    log = []
    models = host(log)
    for name in ("large-v3", "distil-large-v3", "tiny.en", "owner/custom_model-2"):
        models.get(name, "int8")
    assert [entry[1] for entry in log if entry[0] == "load"] == [
        "large-v3", "distil-large-v3", "tiny.en", "owner/custom_model-2",
    ]


def test_an_allow_list_restricts_what_is_loaded():
    log = []
    models = host(log, allowed=frozenset({"tiny.en"}))
    models.get("tiny.en", "int8")
    with pytest.raises(ModelUnavailable, match="allowed models"):
        models.get("large-v3", "int8")
    assert models.loaded == ("tiny.en", "int8")  # the refused name unloaded nothing


def test_a_model_that_cannot_be_loaded_is_this_machines_fault_and_says_why():
    def missing(settings):
        raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")

    with pytest.raises(ModelUnavailable, match="cublas64_12") as error:
        ModelHost("cuda", factory=missing).get("large-v3", "float16")
    assert "large-v3 (float16, cuda)" in str(error.value)


def test_a_library_that_fails_only_at_the_first_inference_is_caught_by_the_warm_up():
    log = []
    broken = RuntimeError("Library libcudnn_ops.so.9 is not found")
    models = ModelHost("cuda", factory=lambda s: Loaded(log, s, warm_up_error=broken))
    with pytest.raises(ModelUnavailable, match="libcudnn_ops"):
        models.get("large-v3", "float16")
    assert models.loaded is None


@pytest.mark.parametrize(
    "error", [MemoryError(), RuntimeError("CUDA failed with error out of memory")]
)
def test_running_out_of_memory_while_loading_is_not_called_unavailable(error):
    def too_big(settings):
        raise error

    with pytest.raises(OutOfMemory):
        ModelHost("cuda", factory=too_big).get("large-v3", "float16")


# --- device ------------------------------------------------------------------------------


def test_a_gpu_is_reported_with_its_name_and_memory():
    found = probe("auto", resolve=lambda preference: CUDA, smi=lambda: ("RTX 4090", 24564))
    assert found == Probe(CUDA, "RTX 4090", 24564)
    reported = capabilities(found, "gpu", ["large-v3", "large-v3", "tiny.en"])
    assert reported.model_dump() == {
        "device": "cuda",
        "gpu_name": "RTX 4090",
        "gpu_memory_mb": 24564,
        "models": ["large-v3", "tiny.en"],
        "engine_version": reported.engine_version,
        "pool": "gpu",
    }


def test_a_cpu_never_asks_nvidia_smi():
    def never():
        raise AssertionError("nvidia-smi was asked on a CPU machine")

    assert probe("cpu", resolve=lambda preference: CPU, smi=never) == Probe(CPU)


def test_asking_for_a_gpu_that_is_not_there_is_an_error():
    def no_gpu(preference):
        raise DeviceUnavailableError("cuda was requested but no CUDA GPU is available")

    with pytest.raises(DeviceUnavailableError):
        probe("cuda", resolve=no_gpu)


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [
        ("NVIDIA GeForce RTX 4090, 24564\n", ("NVIDIA GeForce RTX 4090", 24564)),
        ("Tesla T4, 15360\nTesla T4, 15360\n", ("Tesla T4", 15360)),
        ("", (None, None)),
        ("garbage\n", (None, None)),
        ("A100, lots\n", (None, None)),
    ],
)
def test_nvidia_smi_output_is_read_or_ignored(monkeypatch, stdout, expected):
    class Done:
        pass

    def run(command, **kwargs):
        assert kwargs["timeout"] == 5
        done = Done()
        done.stdout = stdout
        return done

    monkeypatch.setattr(device.subprocess, "run", run)
    assert device.nvidia_smi() == expected


def test_a_missing_or_hanging_nvidia_smi_is_no_gpu_information(monkeypatch):
    def missing(command, **kwargs):
        raise FileNotFoundError("nvidia-smi")

    monkeypatch.setattr(device.subprocess, "run", missing)
    assert device.nvidia_smi() == (None, None)


def test_cached_models_are_named_as_a_profile_names_them(tmp_path):
    for folder in (
        "models--Systran--faster-whisper-large-v3",
        "models--Systran--faster-whisper-tiny.en",
        "models--owner--custom-model",
        "datasets--something",
        ".locks",
    ):
        (tmp_path / folder).mkdir()
    (tmp_path / "models--a-file").write_text("not a folder")
    assert cached_models(tmp_path) == ["large-v3", "tiny.en", "owner/custom-model"]
    assert cached_models(tmp_path / "missing") == []
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest packages/follower/tests/test_models.py -q`
Expected: FAIL at import: `No module named 'swarmscribe_follower.device'`.

- [ ] **Step 3: The device probe**

Create `packages/follower/src/swarmscribe_follower/device.py`:

```python
"""What this machine is, as the leader is told at registration (follower spec 5.9)."""

import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from swarmscribe_engine import ENGINE_VERSION, DeviceChoice, DevicePreference, resolve_device
from swarmscribe_protocol import Capabilities

SMI = (
    "nvidia-smi",
    "--query-gpu=name,memory.total",
    "--format=csv,noheader,nounits",
)
SHORT_NAMES = "Systran/faster-whisper-"  # the repositories faster-whisper's own names map to
MAX_MODELS = 50
MAX_TEXT = 200


@dataclass(frozen=True)
class Probe:
    choice: DeviceChoice
    gpu_name: str | None = None
    gpu_memory_mb: int | None = None


def nvidia_smi() -> tuple[str | None, int | None]:
    """The first GPU's name and memory in MiB, or (None, None) when nvidia-smi is missing,
    slow or says something unexpected. The protocol allows both to be absent."""
    try:
        done = subprocess.run(SMI, capture_output=True, text=True, timeout=5, check=True)
        name, _, memory = done.stdout.splitlines()[0].rpartition(",")
        return name.strip()[:MAX_TEXT] or None, int(memory.strip())
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None, None


def probe(
    preference: DevicePreference,
    *,
    resolve: Callable[[DevicePreference], DeviceChoice] = resolve_device,
    smi: Callable[[], tuple[str | None, int | None]] = nvidia_smi,
) -> Probe:
    """Raises the engine's DeviceUnavailableError when `cuda` was asked for and is absent."""
    choice = resolve(preference)
    if choice.device != "cuda":
        return Probe(choice)
    name, memory = smi()
    return Probe(choice, name, memory)


def cache_dir(model_dir: Path | None) -> Path:
    if model_dir is not None:
        return model_dir
    configured = os.environ.get("HF_HUB_CACHE")
    return Path(configured) if configured else Path.home() / ".cache" / "huggingface" / "hub"


def cached_models(model_dir: Path | None) -> list[str]:
    """The models in the Hugging Face cache folder, by the name a profile would use."""
    try:
        folders = sorted(entry.name for entry in cache_dir(model_dir).iterdir() if entry.is_dir())
    except OSError:
        return []
    names = []
    for folder in folders:
        if folder.startswith("models--"):
            name = folder.removeprefix("models--").replace("--", "/")
            names.append(name.removeprefix(SHORT_NAMES)[:MAX_TEXT])
    return names[:MAX_MODELS]


def capabilities(found: Probe, pool: str, models: list[str]) -> Capabilities:
    return Capabilities(
        device=found.choice.device,
        gpu_name=found.gpu_name,
        gpu_memory_mb=found.gpu_memory_mb,
        models=sorted(set(models))[:MAX_MODELS],
        engine_version=ENGINE_VERSION,
        pool=pool,
    )
```

- [ ] **Step 4: The model host**

Create `packages/follower/src/swarmscribe_follower/models.py`:

```python
"""The one model this follower holds in memory (follower spec 5.7).

A model is loaded, then exercised with a real inference (GPU libraries load at the first
inference, not at the load), and kept until a claim names another. Before another is loaded
the old one is closed, so two are never in memory together."""

import logging
import re
from collections.abc import Callable
from typing import Any

from swarmscribe_engine import Device, Transcriber, TranscribeSettings
from swarmscribe_protocol import MODEL_NAME_MAX_LENGTH, MODEL_NAME_PATTERN

logger = logging.getLogger(__name__)

# A model is named, never located: the name comes over the wire and the loader would accept
# a path. `owner/name` is a Hugging Face repository. The rule is the protocol's, shared with
# the leader that accepts the name (Adjustment 4): never a copy.
MODEL_NAME = re.compile(MODEL_NAME_PATTERN)

EngineFactory = Callable[[TranscribeSettings], Any]


class ModelUnavailable(Exception):
    """This machine cannot load the model: not allowed, not in the cache while offline, a
    compute type the device lacks, a GPU library missing. The machine's fault, not a job's."""


class OutOfMemory(Exception):
    """The model, or a job, does not fit in this machine's memory."""


def is_out_of_memory(error: BaseException) -> bool:
    return isinstance(error, MemoryError) or "out of memory" in str(error).lower()


class ModelHost:
    def __init__(
        self,
        device: Device,
        *,
        factory: EngineFactory = Transcriber,
        allowed: frozenset[str] = frozenset(),
    ) -> None:
        self.device = device
        self._factory = factory
        self._allowed = allowed
        self._loaded: tuple[str, str] | None = None
        self._transcriber: Any = None

    @property
    def loaded(self) -> tuple[str, str] | None:
        """(model, compute type) in memory, or None."""
        return self._loaded

    def get(self, model: str, compute_type: str) -> Any:
        """The transcriber for (model, compute type), loading it if it is not the one in
        memory."""
        if len(model) > MODEL_NAME_MAX_LENGTH or not MODEL_NAME.fullmatch(model):
            raise ModelUnavailable("the model name is not a plain name or owner/name")
        if self._allowed and model not in self._allowed:
            raise ModelUnavailable(f"the model {model} is not in this follower's allowed models")
        if self._loaded == (model, compute_type):
            return self._transcriber
        self.close()
        settings = TranscribeSettings(model=model, compute_type=compute_type, device=self.device)
        try:
            transcriber = self._factory(settings)
            transcriber.warm_up()
        except Exception as exc:
            if is_out_of_memory(exc):
                raise OutOfMemory(f"loading {model}: {type(exc).__name__}") from exc
            # The text of a loader error names the missing file or library: that is the
            # operator's first clue, and it holds nothing of a recording.
            raise ModelUnavailable(
                f"{model} ({compute_type}, {self.device}): {type(exc).__name__}: {exc}"
            ) from exc
        self._transcriber, self._loaded = transcriber, (model, compute_type)
        logger.info("model %s (%s) loaded on %s", model, compute_type, self.device)
        return transcriber

    def close(self) -> None:
        if self._transcriber is not None:
            self._transcriber.close()
        self._transcriber, self._loaded = None, None
```

- [ ] **Step 5: Run the tests to see them pass**

Run: `uv run pytest packages/follower/tests/test_models.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/follower
git commit -m "Follower: one model in memory, proven by an inference; what the machine reports"
```

---

### Task 7: Stopping a job, and keeping its lease

**Files:**
- Create: `packages/follower/src/swarmscribe_follower/lease.py`
- Test: `packages/follower/tests/test_lease.py`

**Interfaces:**
- Consumes: `LeaderClient.heartbeat`, `Transient`, `Refused` (Task 4).
- Produces:
  - Reasons: `CANCELLED = "cancelled"`, `LEASE_LOST = "lease_lost"`, `REVOKED = "revoked"`, `UNAUTHORISED = "unauthorised"`, `SHUTDOWN = "shutdown"`; `LEASE_GONE = frozenset({CANCELLED, LEASE_LOST, REVOKED, UNAUTHORISED})`.
  - `JobControl()` with attributes `reason: str | None`, `progress: float | None`, `draining: bool`, and methods `stop(reason)` (the first reason wins), `check()` (raises `JobStopped`), `pause(seconds) -> bool` (`True` when stopped).
  - `JobStopped(reason)` with `.reason`.
  - `LeaseKeeper(client, job_id, lease_id, interval: float, control, *, clock=time.monotonic)` — a daemon thread; `start()`, `finish()`; attributes `last_loop`, `last_renewed`, `failures`.

- [ ] **Step 1: Write the failing tests**

Create `packages/follower/tests/test_lease.py`:

```python
import threading
import time

import pytest
from swarmscribe_follower.leader import Refused, Transient
from swarmscribe_follower.lease import (
    CANCELLED,
    LEASE_LOST,
    REVOKED,
    SHUTDOWN,
    UNAUTHORISED,
    JobControl,
    JobStopped,
    LeaseKeeper,
)

JOB, LEASE = "job-1", "lease-1"


class Beats:
    """Stands in for the leader client: answers heartbeats from a script, then `continue`."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls = []
        self.arrived = threading.Semaphore(0)

    def heartbeat(self, job_id, lease_id, progress):
        self.calls.append((job_id, lease_id, progress, threading.current_thread().name))
        self.arrived.release()
        outcome = self.script.pop(0) if self.script else "continue"
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def wait_for(self, count):
        for _ in range(count):
            assert self.arrived.acquire(timeout=5), "the keeper stopped heartbeating"


@pytest.fixture
def keeper_of():
    started = []

    def make(client, control, interval=0.01):
        keeper = LeaseKeeper(client, JOB, LEASE, interval, control)
        started.append(keeper)
        keeper.start()
        return keeper

    yield make
    for keeper in started:
        keeper.finish()
        assert not keeper.is_alive()


def test_the_first_reason_to_stop_is_the_one_that_counts():
    control = JobControl()
    control.check()
    assert control.pause(0) is False
    control.stop(CANCELLED)
    control.stop(SHUTDOWN)
    assert control.reason == CANCELLED
    assert control.pause(60) is True  # returns at once
    with pytest.raises(JobStopped) as stopped:
        control.check()
    assert stopped.value.reason == CANCELLED


def test_heartbeats_go_out_from_their_own_thread_with_the_latest_progress(keeper_of):
    client, control = Beats(), JobControl()
    keeper_of(client, control)
    client.wait_for(2)
    control.progress = 0.5
    client.wait_for(2)
    assert client.calls[0] == (JOB, LEASE, None, "lease-keeper")
    assert client.calls[-1][2] == 0.5


def test_heartbeats_continue_while_the_worker_is_blocked(keeper_of):
    """What the engine call looks like to the keeper: a thread that does not come back."""
    client, control = Beats(), JobControl()
    keeper_of(client, control)
    time.sleep(0.1)  # the "engine call": this thread does nothing for ten intervals
    assert len(client.calls) >= 3


def test_cancel_stops_the_job_and_the_keeper(keeper_of):
    client, control = Beats("continue", "cancel"), JobControl()
    keeper = keeper_of(client, control)
    keeper.join(timeout=5)
    assert (control.reason, len(client.calls), keeper.is_alive()) == (CANCELLED, 2, False)


def test_drain_is_noted_and_the_job_carries_on(keeper_of):
    client, control = Beats("drain"), JobControl()
    keeper_of(client, control)
    client.wait_for(3)
    assert (control.draining, control.reason) == (True, None)


@pytest.mark.parametrize(
    ("status", "code", "reason"),
    [
        (409, "stale_lease", LEASE_LOST),
        (404, "not_found", LEASE_LOST),
        (403, "forbidden", REVOKED),
        (401, "unauthorized", UNAUTHORISED),
        (422, "invalid_request", LEASE_LOST),
    ],
)
def test_a_refused_heartbeat_stops_the_job_with_the_reason(keeper_of, status, code, reason):
    client, control = Beats(Refused(status, code, "no")), JobControl()
    keeper = keeper_of(client, control)
    keeper.join(timeout=5)
    assert (control.reason, len(client.calls)) == (reason, 1)


def test_a_leader_outage_does_not_stop_the_job_and_heartbeats_resume(keeper_of):
    outage = [Transient(None, None, "ConnectError")] * 3
    client, control = Beats(*outage, "continue"), JobControl()
    keeper = keeper_of(client, control)
    client.wait_for(5)
    assert control.reason is None
    assert keeper.failures == 0  # recovered


def test_retries_during_an_outage_are_never_further_apart_than_the_interval():
    client, control = Beats(*[Transient(503, 60.0, "x")] * 50), JobControl()
    keeper = LeaseKeeper(client, JOB, LEASE, 0.01, control)
    keeper.start()
    try:
        client.wait_for(6)  # with the leader's Retry-After of 60 s this would take minutes
    finally:
        keeper.finish()
    assert control.reason is None


def test_finish_ends_the_keeper_promptly_even_with_a_long_interval():
    keeper = LeaseKeeper(Beats(), JOB, LEASE, 3600, JobControl())
    keeper.start()
    began = time.monotonic()
    keeper.finish()
    assert not keeper.is_alive()
    assert time.monotonic() - began < 2
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest packages/follower/tests/test_lease.py -q`
Expected: FAIL at import: `No module named 'swarmscribe_follower.lease'`.

- [ ] **Step 3: Write it**

Create `packages/follower/src/swarmscribe_follower/lease.py`:

```python
"""Keeping a job's lease, and stopping the job (follower spec 5.5).

The engine call blocks, so the lease is renewed from a thread of its own that never waits
for the worker. The two meet in a JobControl: the keeper (or a shutdown) asks the job to
stop with a reason, and the worker notices at its next check: between download chunks,
after every transcribed segment, before each upload."""

import logging
import threading
import time
from collections.abc import Callable

from .leader import LeaderClient, Refused, Transient

logger = logging.getLogger(__name__)

# Why a job stops. The first reason given is the one that counts.
CANCELLED = "cancelled"  # the leader cancelled the job: call nothing, wipe
LEASE_LOST = "lease_lost"  # the lease is someone else's now: call nothing, wipe
REVOKED = "revoked"  # this follower was revoked: call nothing, wipe, exit
UNAUTHORISED = "unauthorised"  # the leader does not know the credential
SHUTDOWN = "shutdown"  # the follower is stopping: release the job
LEASE_GONE = frozenset({CANCELLED, LEASE_LOST, REVOKED, UNAUTHORISED})


class JobStopped(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class JobControl:
    """Shared by the worker, the lease keeper and the agent for one job."""

    def __init__(self) -> None:
        self._stopped = threading.Event()
        self._lock = threading.Lock()
        self.reason: str | None = None
        self.progress: float | None = None
        self.draining = False

    def stop(self, reason: str) -> None:
        with self._lock:
            if self.reason is None:
                self.reason = reason
        self._stopped.set()

    def check(self) -> None:
        if self._stopped.is_set():
            raise JobStopped(self.reason or SHUTDOWN)

    def pause(self, seconds: float) -> bool:
        """Wait, or less if the job is stopped meanwhile. True means: stopped."""
        return self._stopped.wait(seconds)


class LeaseKeeper(threading.Thread):
    """Heartbeats every `interval` seconds until `finish()`. A failed heartbeat is retried
    after 2, 4, 8 ... seconds, never more than `interval` apart, for as long as it takes:
    the job carries on while the leader is away (master spec 13)."""

    def __init__(
        self,
        client: LeaderClient,
        job_id: str,
        lease_id: str,
        interval: float,
        control: JobControl,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(name="lease-keeper", daemon=True)
        self._client, self._job_id, self._lease_id = client, job_id, lease_id
        self._interval, self._control, self._clock = interval, control, clock
        self._done = threading.Event()
        self.last_loop = clock()  # liveness: when the loop last went round
        self.last_renewed = clock()  # the claim renewed it
        self.failures = 0

    def finish(self) -> None:
        self._done.set()
        if self.is_alive() and threading.current_thread() is not self:
            self.join(timeout=5)

    def run(self) -> None:
        extra = {"job_id": self._job_id, "lease_id": self._lease_id}
        delay = self._interval
        while not self._done.wait(delay):
            self.last_loop = self._clock()
            try:
                directive = self._client.heartbeat(
                    self._job_id, self._lease_id, self._control.progress
                )
            except Transient:
                self.failures += 1
                if self.failures == 1:
                    logger.warning("heartbeat failed; retrying", extra=extra)
                delay = min(self._interval, 2.0 ** min(self.failures, 10))
                continue
            except Refused as refused:
                reason = {403: REVOKED, 401: UNAUTHORISED}.get(refused.status, LEASE_LOST)
                logger.warning("heartbeat refused (%s): %s", refused.code, reason, extra=extra)
                self._control.stop(reason)
                return
            if self.failures:
                logger.info("heartbeat recovered after %d failures", self.failures, extra=extra)
            self.failures, delay = 0, self._interval
            self.last_renewed = self._clock()
            if directive == "cancel":
                logger.info("the leader cancelled the job", extra=extra)
                self._control.stop(CANCELLED)
                return
            if directive == "drain" and not self._control.draining:
                logger.info("the leader is draining this follower", extra=extra)
                self._control.draining = True
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest packages/follower/tests/test_lease.py -q`
Expected: PASS, in well under ten seconds (the keeper's interval in these tests is 10 ms).

- [ ] **Step 5: Commit**

```bash
git add packages/follower
git commit -m "Follower: a lease keeper on its own thread, and one place a job is told to stop"
```

---

### Task 8: One job

**Files:**
- Create: `packages/follower/src/swarmscribe_follower/job.py`, `packages/follower/tests/conftest.py`, `packages/follower/tests/follower_testkit.py`
- Test: `packages/follower/tests/test_job.py`

**Interfaces:**
- Consumes: `LeaderClient`, `retrying`, `Transient`, `Refused`, `Interrupted` (Task 4); `Links` and its exceptions (Task 5); `ModelHost`, `ModelUnavailable`, `OutOfMemory`, `is_out_of_memory` (Task 6); `JobControl`, `JobStopped`, `LeaseKeeper` and the reasons (Task 7); `Scratch` (Task 3); the engine's `Transcriber.transcribe(path, vocabulary, *, settings, progress)`, `write_outputs`, `UndecodableAudioError`, `swarmscribe_engine.transcriber.sha256_file`.
- Produces:
  - `job.JobRunner(client, links, models, scratch, *, device, heartbeat_interval: float, clock=time.monotonic, sleep=time.sleep)` with `run(claim: ClaimResponse, control: JobControl) -> JobResult` (never raises) and `remaining() -> float | None`.
  - `job.JobResult(outcome: str, detail: str = "")`; outcomes `COMPLETED = "completed"`, `FAILED = "failed"`, `RELEASED = "released"`, `ABANDONED = "abandoned"`, `REVOKED_FOLLOWER = "revoked"`, `UNKNOWN_CREDENTIAL = "unauthorised"`, `UNFIT = "unfit"`.
  - `job.classify(error) -> tuple[FailureCode, bool, str]`.
  - In `follower_testkit` (tests only): `FakeLeader` (`.transport`, `add_job(source=b"...", **settings) -> job_id`, `push(kind, *answers)`, `on[kind] = hook`, `expire_links(job_id)`, `cancel(job_id)`, `take_over(job_id)`, `count(kind)`, and the records `kinds`, `jobs`, `submitted`, `failed`, `released`, `heartbeats`, `registrations`, `deregistrations`, `capabilities`, `credentials`, `state`, `bearer_on_links`, `retry_after`); `FakeEngine` (`loads`, `closed`, `steps`, `on_step`, `error`, `load_error`, `unavailable`, `segments`, `transcribed`); `make_runner(tmp_path, leader, engine, **overrides) -> (JobRunner, LeaderClient)`; `make_agent(tmp_path, leader, engine, **overrides)` (used from Task 9); `make_settings`, `error(status, code, **headers)`, `sha(data)`, `BASE`, `JOIN_TOKEN`, `SPOKEN`, `CPU`.
  - Request kinds the fake records: `register`, `deregister`, `claim`, `heartbeat`, `links`, `submit`, `fail`, `release`, `download`, `upload:txt`, `upload:srt`, `upload:segments_json`.

- [ ] **Step 1: Write the test kit**

Create `packages/follower/tests/conftest.py`:

```python
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# follower_testkit sits beside this file; leader_testkit beside the leader's tests (it is
# what test_real_leader.py builds a real leader with).
for folder in (HERE, HERE.parents[1] / "leader" / "tests"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))
```

Create `packages/follower/tests/follower_testkit.py`:

```python
"""A leader and an engine that are fakes, for the follower's unit tests. conftest.py puts
this folder on sys.path; test files import from here.

FakeLeader speaks the protocol through an httpx transport and behaves like the real leader
where the follower can tell: leases, link expiry, checksum verification, drain, revocation.
`push` scripts the next answers of one kind of request; `on` hooks run before a request is
handled. Requests are recorded by kind, never by URL."""

import hashlib
import json
import threading
import uuid
from collections import defaultdict
from pathlib import Path

import httpx
from swarmscribe_engine import (
    DeviceChoice,
    Segment,
    Transcript,
    Word,
)
from swarmscribe_engine.transcriber import sha256_file
from swarmscribe_follower.config import Settings
from swarmscribe_follower.credentials import CredentialStore
from swarmscribe_follower.device import Probe
from swarmscribe_follower.job import JobRunner
from swarmscribe_follower.leader import LeaderClient
from swarmscribe_follower.models import ModelHost
from swarmscribe_follower.scratch import Scratch
from swarmscribe_follower.transfer import Links

BASE = "https://leader.test"
JOIN_TOKEN = "join-token-SECRET"
SPOKEN = "Welcome to Ashford."
CPU = DeviceChoice(device="cpu", model="distil-large-v3", compute_type="int8")
SEGMENTS = (
    Segment(
        start=0.0,
        end=1.5,
        text=SPOKEN,
        words=(
            Word(start=0.0, end=0.4, word=" Welcome", probability=0.98),
            Word(start=0.4, end=0.6, word=" to", probability=0.99),
            Word(start=0.6, end=1.5, word=" Ashford.", probability=0.71),
        ),
    ),
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def error(status: int, code: str, **headers: str) -> httpx.Response:
    return httpx.Response(status, json={"code": code, "message": code}, headers=headers)


class FakeLeader:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.kinds: list[str] = []  # every request, by kind, in order
        self.bearer_on_links = False
        self.state = "active"  # of the one follower: active, draining or revoked
        self.credentials: set[str] = set()
        self.registrations = 0
        self.deregistrations = 0
        self.capabilities: list[dict] = []
        self.queue: list[str] = []
        self.jobs: dict[str, dict] = {}
        self.heartbeats: list[float | None] = []
        self.failed: list[dict] = []
        self.released: list[str] = []
        self.submitted: list[dict] = []
        self.script: dict[str, list] = defaultdict(list)
        self.on: dict[str, object] = {}
        self.retry_after = "0"
        self.transport = httpx.MockTransport(self._handle)

    # --- arranging -----------------------------------------------------------------------

    def add_job(self, source: bytes = b"a recording", **settings) -> str:
        job_id = str(uuid.uuid4())
        with self.lock:
            self.jobs[job_id] = {
                "state": "queued",
                "lease": None,
                "source": source,
                "settings": {"model": "distil-large-v3", "compute_type": "int8", **settings},
                "vocabulary": {"version": 0},
                "uploads": {},
                "links": 0,  # how many times links were issued
                "oldest_link": 0,  # links issued before this one are expired
                "attempts": 0,
            }
            self.queue.append(job_id)
        return job_id

    def push(self, kind: str, *answers) -> None:
        """Answer the next requests of `kind` with these (a Response, or an Exception)."""
        self.script[kind].extend(answers)

    def expire_links(self, job_id: str) -> None:
        job = self.jobs[job_id]
        job["oldest_link"] = job["links"] + 1

    def cancel(self, job_id: str) -> None:
        self.jobs[job_id]["state"] = "cancelled"

    def take_over(self, job_id: str) -> None:
        """The lease expired and another follower holds the job now."""
        self.jobs[job_id]["lease"] = str(uuid.uuid4())

    def count(self, kind: str) -> int:
        return sum(1 for seen in self.kinds if seen == kind)

    # --- answering -----------------------------------------------------------------------

    def _links(self, job_id: str) -> dict:
        job = self.jobs[job_id]
        job["links"] += 1

        def link(name: str, method: str) -> dict:
            url = f"{BASE}/v1/files/{job_id}/{name}/{job['lease']}/{job['links']}?sig=LINK-SECRET"
            return {"url": url, "method": method, "headers": {"X-Link": name}}

        return {
            "download_url": link("source", "GET"),
            "upload_urls": {name: link(name, "PUT") for name in ("txt", "srt", "segments_json")},
        }

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/healthz":
            return httpx.Response(200, json={"status": "ok"})
        with self.lock:
            parts = request.url.path.strip("/").split("/")
            if parts[1] == "files":
                kind = "download" if request.method == "GET" else f"upload:{parts[3]}"
                if "authorization" in request.headers:
                    self.bearer_on_links = True
            elif parts[1] == "followers":
                kind = parts[2]
            else:
                kind = parts[-1]
            self.kinds.append(kind)
            hook = self.on.get(kind)
        if hook is not None:
            hook()
        with self.lock:
            if self.script[kind]:
                answer = self.script[kind].pop(0)
                if isinstance(answer, Exception):
                    raise answer
                return answer
            if parts[1] == "files":
                return self._file(request, parts)
            body = json.loads(request.content) if request.content else {}
            if kind == "register":
                return self._register(body)
            credential = request.headers.get("authorization", "").removeprefix("Bearer ")
            if credential not in self.credentials:
                return error(401, "unauthorized")
            if self.state == "revoked":
                return error(403, "forbidden")
            if kind == "deregister":
                self.deregistrations += 1
                return httpx.Response(204)
            if kind == "claim":
                return self._claim()
            return self._job(kind, parts[2], body)

    def _register(self, body: dict) -> httpx.Response:
        if body["join_token"] != JOIN_TOKEN:
            return error(401, "unauthorized")
        if body["protocol_version"] != 1:
            return error(409, "protocol_version")
        self.registrations += 1
        self.capabilities.append(body["capabilities"])
        credential = f"credential-SECRET-{self.registrations}"
        self.credentials.add(credential)
        answer = {
            "follower_id": str(uuid.uuid4()),
            "credential": credential,
            "heartbeat_interval": 30,
            "lease_seconds": 120,
        }
        return httpx.Response(200, json=answer)

    def _claim(self) -> httpx.Response:
        headers = {"Retry-After": self.retry_after}
        if self.state == "draining":
            return httpx.Response(204, headers={**headers, "X-SwarmScribe-Directive": "drain"})
        if not self.queue:
            return httpx.Response(204, headers=headers)
        job_id = self.queue.pop(0)
        job = self.jobs[job_id]
        job.update(state="leased", lease=str(uuid.uuid4()))
        job["attempts"] += 1
        claim = {
            "job_id": job_id,
            "lease_id": job["lease"],
            **self._links(job_id),
            "settings": job["settings"],
            "vocabulary": job["vocabulary"],
            "source_version": "11-1-1",
        }
        return httpx.Response(200, json=claim)

    def _job(self, kind: str, job_id: str, body: dict) -> httpx.Response:
        job = self.jobs.get(job_id)
        if job is None:
            return error(404, "not_found")
        mine = body.get("lease_id") == job["lease"]
        if kind == "heartbeat":
            if job["state"] == "cancelled" and mine:
                return httpx.Response(200, json={"directive": "cancel"})
            if job["state"] != "leased" or not mine:
                return error(409, "stale_lease")
            self.heartbeats.append(body.get("progress"))
            directive = "drain" if self.state == "draining" else "continue"
            return httpx.Response(200, json={"directive": directive})
        if job["state"] != "leased" or not mine:
            return error(409, "stale_lease")
        if kind == "links":
            return httpx.Response(200, json=self._links(job_id))
        if kind == "submit":
            sums = body["checksums"]
            if set(job["uploads"]) != {"txt", "srt", "segments_json"}:
                return error(409, "outputs_missing")
            if any(sha(job["uploads"][name]) != sums[name] for name in job["uploads"]):
                return error(409, "checksum_mismatch")
            if sums["source"] != sha(job["source"]):
                return error(409, "checksum_mismatch")
            job["state"] = "completed"
            self.submitted.append({"job_id": job_id, **sums})
            return httpx.Response(200, json={"accepted": True})
        if kind == "fail":
            job["state"] = "failed"
            self.failed.append({"job_id": job_id, **body})
            return httpx.Response(204)
        if kind == "release":
            job.update(state="queued", lease=None)
            job["attempts"] -= 1
            self.released.append(job_id)
            return httpx.Response(204)
        return error(404, "not_found")

    def _file(self, request: httpx.Request, parts: list[str]) -> httpx.Response:
        _v1, _files, job_id, name, lease, issued = parts
        job = self.jobs[job_id]
        if int(issued) < job["oldest_link"]:
            return error(403, "forbidden")
        if request.method == "GET":
            return httpx.Response(200, content=job["source"])
        if request.headers.get("x-link") != name:
            return error(400, "invalid_request")
        if job["state"] != "leased" or lease != job["lease"]:
            return error(409, "stale_lease")
        job["uploads"][name] = request.content
        return httpx.Response(201)


class FakeEngine:
    """What ModelHost is given as its factory. `steps` are the fractions the fake engine
    reports; `on_step` runs before each report (to cancel, revoke or stop at that moment)."""

    def __init__(self) -> None:
        self.loads: list[tuple[str, str, str]] = []
        self.closed = 0
        self.steps: tuple[float, ...] = (0.25, 0.5, 0.75, 1.0)
        self.on_step = None
        self.error: Exception | None = None
        self.load_error: Exception | None = None
        self.unavailable: set[str] = set()
        self.segments: tuple[Segment, ...] = SEGMENTS
        self.transcribed: list[dict] = []

    def __call__(self, settings):
        if self.load_error is not None:
            raise self.load_error
        if settings.model in self.unavailable:
            raise RuntimeError(f"{settings.model} is not in the local cache and offline mode is on")
        self.loads.append((settings.model, settings.compute_type, settings.device))
        return _FakeTranscriber(self)


class _FakeTranscriber:
    def __init__(self, engine: FakeEngine) -> None:
        self.engine = engine

    def warm_up(self) -> None:
        pass

    def close(self) -> None:
        self.engine.closed += 1

    def transcribe(self, path, vocabulary, *, settings, progress):
        path = Path(path)
        self.engine.transcribed.append(
            {"audio": path.read_bytes(), "vocabulary": vocabulary, "settings": settings}
        )
        for fraction in self.engine.steps:
            if self.engine.on_step is not None:
                self.engine.on_step(fraction)
            progress(fraction)
        if self.engine.error is not None:
            raise self.engine.error
        return Transcript(
            source_name=path.name,
            source_checksum=sha256_file(path),
            duration=3.25,
            settings=settings,
            vocabulary_version=vocabulary.version,
            vocabulary_terms_used=(),
            corrections_applied=(),
            segments=self.engine.segments,
        )


def make_settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "leader_url": BASE,
        "join_token": JOIN_TOKEN,
        "state_dir": tmp_path / "state",
        "model_dir": tmp_path / "models",
        "device": "cpu",
    }
    values.update(overrides)
    return Settings(**values)


def make_runner(tmp_path: Path, leader: FakeLeader, engine: FakeEngine, **overrides):
    """A JobRunner whose follower is already registered, and the client to claim with."""
    leader.credentials.add("credential-SECRET-0")
    client = LeaderClient(BASE, credential="credential-SECRET-0", transport=leader.transport)
    scratch = Scratch(tmp_path / "scratch")
    scratch.prepare()
    values = {"device": "cpu", "heartbeat_interval": 0.01, "sleep": lambda seconds: None}
    values.update(overrides)
    runner = JobRunner(
        client, Links(transport=leader.transport), ModelHost("cpu", factory=engine), scratch,
        **values,
    )
    return runner, client


def make_agent(tmp_path: Path, leader: FakeLeader, engine: FakeEngine, **overrides):
    """An Agent wired to the fake leader and engine, with waits that do not wait. The import
    is here because the kit is used by the job's tests before the agent exists."""
    from swarmscribe_follower.agent import Agent

    settings = make_settings(tmp_path, **overrides)
    return Agent(
        settings,
        client=LeaderClient(BASE, transport=leader.transport),
        links=Links(transport=leader.transport),
        models=ModelHost("cpu", factory=engine, allowed=frozenset(settings.allowed_models)),
        scratch=Scratch(settings.scratch),
        store=CredentialStore(settings.credential_file),
        probe=Probe(CPU),
        heartbeat_interval=0.01,
        parked_poll_seconds=0.01,
        sleep=lambda seconds: None,
    )
```

The fake leader copies the real one where the follower can tell the difference; Task 11 runs the same follower against the real leader, so a difference between the two shows there.

- [ ] **Step 2: Write the failing tests**

Create `packages/follower/tests/test_job.py`:

```python
import json
import logging
import threading
import time

import httpx
import pytest
from follower_testkit import SPOKEN, FakeEngine, FakeLeader, error, make_runner, sha
from swarmscribe_engine import UndecodableAudioError
from swarmscribe_follower.job import classify
from swarmscribe_follower.leader import Refused
from swarmscribe_follower.lease import SHUTDOWN, JobControl
from swarmscribe_follower.transfer import OutOfSpace
from swarmscribe_protocol import SegmentsDocument


@pytest.fixture
def leader():
    return FakeLeader()


@pytest.fixture
def engine():
    return FakeEngine()


def run_one(tmp_path, leader, engine, **overrides):
    """Claim the next job and run it. Returns (result, job id, the control it ran under)."""
    runner, client = make_runner(tmp_path, leader, engine, **overrides)
    claim = client.claim()
    control = JobControl()
    return runner.run(claim, control), claim.job_id, control


def after_heartbeats(leader, more=2):
    """Wait until the lease keeper has reported `more` times from now (so that it has seen
    whatever the test just changed in the leader)."""
    target = leader.count("heartbeat") + more
    deadline = time.monotonic() + 5
    while leader.count("heartbeat") < target:
        assert time.monotonic() < deadline, "the lease keeper stopped heartbeating"
        time.sleep(0.005)


def scratch_is_empty(tmp_path):
    return [entry.name for entry in (tmp_path / "scratch").iterdir()] == [".swarmscribe-scratch"]


# --- the happy path ---------------------------------------------------------------------


def test_a_job_is_downloaded_transcribed_uploaded_and_submitted(tmp_path, leader, engine):
    job_id = leader.add_job(b"the recording", channel_mode="auto", temperatures=[0.0, 0.2])
    leader.jobs[job_id]["vocabulary"] = {
        "version": 3,
        "terms": ["Ashford"],
        "corrections": [{"heard": "ash ford", "replacement": "Ashford"}],
    }
    result, _, _ = run_one(tmp_path, leader, engine)
    assert (result.outcome, leader.jobs[job_id]["state"]) == ("completed", "completed")
    without_heartbeats = [kind for kind in leader.kinds if kind != "heartbeat"]
    assert without_heartbeats == [
        "claim", "download", "upload:txt", "upload:srt", "upload:segments_json", "submit",
    ]
    uploads = leader.jobs[job_id]["uploads"]
    assert uploads["txt"] == (SPOKEN + "\n").encode()
    assert b"00:00:00,000 --> 00:00:01,500" in uploads["srt"]
    document = SegmentsDocument.model_validate_json(uploads["segments_json"])
    assert (document.source_checksum, document.vocabulary_version) == (sha(b"the recording"), 3)
    (submitted,) = leader.submitted
    assert submitted["source"] == sha(b"the recording")
    assert submitted["segments_json"] == sha(uploads["segments_json"])
    (heard,) = engine.transcribed
    assert heard["audio"] == b"the recording"
    assert (heard["settings"].channel_mode, heard["settings"].temperatures) == ("auto", (0.0, 0.2))
    assert (heard["settings"].model, heard["settings"].device) == ("distil-large-v3", "cpu")
    assert heard["vocabulary"].terms == ("Ashford",)
    assert heard["vocabulary"].corrections[0].replacement == "Ashford"
    assert scratch_is_empty(tmp_path)
    assert leader.bearer_on_links is False


def test_a_recording_without_speech_is_a_normal_result(tmp_path, leader, engine):
    job_id = leader.add_job()
    engine.segments = ()
    result, _, _ = run_one(tmp_path, leader, engine)
    assert result.outcome == "completed"
    uploads = leader.jobs[job_id]["uploads"]
    assert (uploads["txt"], uploads["srt"]) == (b"", b"")
    assert SegmentsDocument.model_validate_json(uploads["segments_json"]).segments == []


def test_the_lease_is_renewed_with_progress_while_the_engine_is_busy(tmp_path, leader, engine):
    leader.add_job()
    engine.on_step = lambda fraction: after_heartbeats(leader, 2)
    result, _, _ = run_one(tmp_path, leader, engine)
    assert result.outcome == "completed"
    assert len(leader.heartbeats) >= 8
    assert {0.25, 0.5, 0.75} <= set(leader.heartbeats)


# --- terminal for the job (spec 6.1) ----------------------------------------------------


def test_undecodable_audio_fails_the_job_for_good(tmp_path, leader, engine):
    leader.add_job()
    engine.error = UndecodableAudioError("cannot decode source: Invalid data found")
    result, job_id, _ = run_one(tmp_path, leader, engine)
    assert (result.outcome, result.detail) == ("failed", "undecodable")
    (failed,) = leader.failed
    assert (failed["code"], failed["retryable"]) == ("undecodable", False)
    assert "Invalid data found" in failed["reason"]
    assert leader.jobs[job_id]["uploads"] == {} and scratch_is_empty(tmp_path)


@pytest.mark.parametrize(
    "refusal",
    [
        error(412, "source_changed"),
        error(404, "not_found"),
        httpx.Response(412, text="<Error><Code>ConditionNotMet</Code></Error>"),
    ],
)
def test_a_recording_that_changed_or_went_fails_as_source_changed(
    tmp_path, leader, engine, refusal
):
    leader.add_job()
    leader.push("download", refusal)
    result, _, _ = run_one(tmp_path, leader, engine)
    assert result.detail == "source_changed"
    assert (leader.failed[0]["code"], leader.failed[0]["retryable"]) == ("source_changed", False)
    assert engine.transcribed == []


def test_outputs_the_leader_finds_inconsistent_fail_for_good(tmp_path, leader, engine):
    leader.add_job()
    leader.push("submit", error(409, "outputs_inconsistent"))
    result, _, _ = run_one(tmp_path, leader, engine)
    assert (leader.failed[0]["code"], leader.failed[0]["retryable"]) == ("engine_error", False)
    assert leader.count("submit") == 1 and result.outcome == "failed"


def test_an_output_too_large_for_the_storage_fails_for_good(tmp_path, leader, engine):
    leader.add_job()
    leader.push("upload:segments_json", error(413, "too_large"))
    run_one(tmp_path, leader, engine)
    assert (leader.failed[0]["code"], leader.failed[0]["retryable"]) == ("other", False)


# --- retryable on another attempt (spec 6.2) --------------------------------------------


@pytest.mark.parametrize(
    ("raised", "code"),
    [
        (RuntimeError("the model returned nothing"), "engine_error"),
        (ValueError("unexpected"), "engine_error"),
        (RuntimeError("CUDA failed with error out of memory"), "out_of_resources"),
        (MemoryError(), "out_of_resources"),
        (OSError(28, "No space left on device"), "out_of_resources"),
        (OutOfSpace("the recording is 9 bytes; scratch has 1 bytes free"), "out_of_resources"),
    ],
)
def test_an_engine_or_resource_error_fails_the_attempt_retryably(
    tmp_path, leader, engine, raised, code
):
    leader.add_job()
    engine.error = raised
    result, _, _ = run_one(tmp_path, leader, engine)
    assert (result.outcome, leader.failed[0]["code"], leader.failed[0]["retryable"]) == (
        "failed",
        code,
        True,
    )


def test_a_refused_submit_uploads_everything_again_then_succeeds(tmp_path, leader, engine):
    job_id = leader.add_job()
    leader.push("submit", error(409, "checksum_mismatch"), error(409, "outputs_changed"))
    result, _, _ = run_one(tmp_path, leader, engine)
    assert result.outcome == "completed"
    assert (leader.count("submit"), leader.count("upload:txt")) == (3, 3)
    assert leader.jobs[job_id]["state"] == "completed"


def test_three_refused_submits_fail_the_attempt(tmp_path, leader, engine):
    leader.add_job()
    leader.push("submit", *[error(409, "outputs_missing")] * 3)
    result, _, _ = run_one(tmp_path, leader, engine)
    assert (leader.failed[0]["code"], leader.failed[0]["retryable"]) == ("other", True)
    assert leader.count("submit") == 3


def test_a_request_the_leader_does_not_understand_fails_the_attempt(tmp_path, leader, engine):
    leader.add_job()
    leader.push("submit", error(422, "invalid_request"))
    run_one(tmp_path, leader, engine)
    assert (leader.failed[0]["code"], leader.failed[0]["retryable"]) == ("other", True)
    assert "422 invalid_request" in leader.failed[0]["reason"]


def test_submit_answered_500_five_times_fails_the_attempt(tmp_path, leader, engine):
    leader.add_job()
    leader.push("submit", *[error(500, "internal", **{"Retry-After": "0"})] * 5)
    run_one(tmp_path, leader, engine)
    assert leader.count("submit") == 5
    assert (leader.failed[0]["code"], leader.failed[0]["retryable"]) == ("other", True)


# --- not the job's fault (spec 6.3) -----------------------------------------------------


def test_a_cancelled_job_is_stopped_mid_transcription_and_nothing_is_called(
    tmp_path, leader, engine
):
    job_id = leader.add_job()

    def cancel_at_half(fraction):
        if fraction == 0.5:
            leader.cancel(job_id)
            after_heartbeats(leader)

    engine.on_step = cancel_at_half
    result, _, _ = run_one(tmp_path, leader, engine)
    assert (result.outcome, result.detail) == ("abandoned", "cancelled")
    assert (leader.failed, leader.released, leader.submitted) == ([], [], [])
    assert not any(kind.startswith("upload") for kind in leader.kinds)
    assert scratch_is_empty(tmp_path)


def test_a_lost_lease_is_abandoned_without_a_call(tmp_path, leader, engine):
    job_id = leader.add_job()

    def lose_at_half(fraction):
        if fraction == 0.5:
            leader.take_over(job_id)
            after_heartbeats(leader, 1)

    engine.on_step = lose_at_half
    result, _, _ = run_one(tmp_path, leader, engine)
    assert (result.outcome, result.detail) == ("abandoned", "lease_lost")
    assert (leader.failed, leader.released) == ([], [])


def test_an_upload_refused_for_a_stale_lease_abandons_the_job(tmp_path, leader, engine):
    leader.add_job()
    leader.push("upload:srt", error(409, "stale_lease"))
    result, _, _ = run_one(tmp_path, leader, engine, heartbeat_interval=3600)
    assert (result.outcome, leader.failed, leader.released) == ("abandoned", [], [])
    assert leader.count("submit") == 0


def test_a_revoked_follower_stops_and_calls_nothing(tmp_path, leader, engine):
    leader.add_job()

    def revoke_at_half(fraction):
        if fraction == 0.5:
            leader.state = "revoked"
            after_heartbeats(leader, 1)

    engine.on_step = revoke_at_half
    result, _, _ = run_one(tmp_path, leader, engine)
    assert result.outcome == "revoked"
    assert [k for k in leader.kinds if k not in ("claim", "download", "heartbeat")] == []
    assert scratch_is_empty(tmp_path)


def test_a_shutdown_releases_the_job(tmp_path, leader, engine):
    job_id = leader.add_job()
    runner, client = make_runner(tmp_path, leader, engine)
    control = JobControl()
    engine.on_step = lambda fraction: control.stop(SHUTDOWN) if fraction == 0.5 else None
    result = runner.run(client.claim(), control)
    assert (result.outcome, leader.released, leader.failed) == ("released", [job_id], [])
    assert leader.jobs[job_id]["attempts"] == 0  # the attempt is not counted
    assert scratch_is_empty(tmp_path)


def test_a_job_stopped_before_it_starts_is_released_without_downloading(tmp_path, leader, engine):
    job_id = leader.add_job()
    runner, client = make_runner(tmp_path, leader, engine)
    control = JobControl()
    control.stop(SHUTDOWN)
    result = runner.run(client.claim(), control)
    assert (result.outcome, leader.released) == ("released", [job_id])
    assert leader.count("download") == 0


def test_an_expired_download_link_is_replaced_once(tmp_path, leader, engine):
    job_id = leader.add_job()
    leader.on["claim"] = lambda: None
    runner, client = make_runner(tmp_path, leader, engine)
    claim = client.claim()
    leader.expire_links(job_id)
    result = runner.run(claim, JobControl())
    assert result.outcome == "completed"
    assert (leader.count("links"), leader.count("download")) == (1, 2)


def test_upload_links_that_expired_during_a_long_job_are_replaced(tmp_path, leader, engine):
    job_id = leader.add_job()
    engine.on_step = lambda fraction: leader.expire_links(job_id) if fraction == 1.0 else None
    result, _, _ = run_one(tmp_path, leader, engine)
    assert result.outcome == "completed"
    assert (leader.count("links"), leader.count("upload:txt")) == (1, 2)
    assert leader.bearer_on_links is False


def test_when_fresh_links_are_refused_too_the_job_is_released(tmp_path, leader, engine):
    job_id = leader.add_job()
    leader.push("upload:txt", error(403, "forbidden"), error(403, "forbidden"))
    result, _, _ = run_one(tmp_path, leader, engine)
    assert (result.outcome, leader.released, leader.failed) == ("released", [job_id], [])
    assert leader.count("links") == 1


def test_fresh_links_asked_too_soon_are_waited_for(tmp_path, leader, engine):
    leader.add_job()
    leader.push("upload:txt", error(403, "forbidden"))
    leader.push("links", error(429, "too_many_requests", **{"Retry-After": "0"}))
    result, _, _ = run_one(tmp_path, leader, engine)
    assert (result.outcome, leader.count("links")) == ("completed", 2)


def test_a_download_that_keeps_failing_is_released_after_ten_minutes(tmp_path, leader, engine):
    job_id = leader.add_job()
    now = [0.0]
    leader.on["download"] = lambda: now.__setitem__(0, now[0] + 200)
    leader.push("download", *[error(503, "unavailable", **{"Retry-After": "0"})] * 10)
    result, _, _ = run_one(tmp_path, leader, engine, clock=lambda: now[0])
    assert (result.outcome, leader.released) == ("released", [job_id])
    assert leader.count("download") == 3


def test_a_model_this_machine_cannot_load_releases_the_job_and_says_unfit(
    tmp_path, leader, engine
):
    job_id = leader.add_job(model="large-v3", compute_type="float16")
    engine.unavailable = {"large-v3"}
    result, _, _ = run_one(tmp_path, leader, engine)
    assert result.outcome == "unfit"
    assert "offline" in result.detail
    assert (leader.released, leader.failed) == ([job_id], [])


def test_a_model_name_that_is_a_path_is_never_loaded(tmp_path, leader, engine):
    leader.add_job(model="/etc/passwd")
    result, _, _ = run_one(tmp_path, leader, engine)
    assert (result.outcome, engine.loads) == ("unfit", [])


# --- retried in place (spec 6.4) --------------------------------------------------------


def test_a_leader_outage_during_upload_and_submit_is_waited_out(tmp_path, leader, engine):
    leader.add_job()
    busy = error(409, "conflict", **{"Retry-After": "0"})
    down = error(503, "unavailable", **{"Retry-After": "0"})
    leader.push("upload:txt", down, httpx.ConnectError("connection refused"), busy)
    leader.push("submit", down, down)
    result, _, _ = run_one(tmp_path, leader, engine)
    assert result.outcome == "completed"
    assert (leader.count("upload:txt"), leader.count("submit")) == (4, 3)


def test_a_fail_that_cannot_be_delivered_is_given_up_after_three_tries(tmp_path, leader, engine):
    leader.add_job()
    engine.error = RuntimeError("broken")
    leader.push("fail", *[error(503, "unavailable")] * 5)
    waits = []
    result, _, _ = run_one(tmp_path, leader, engine, sleep=waits.append)
    assert (result.outcome, leader.count("fail"), leader.failed) == ("failed", 3, [])
    assert waits == [1.0, 2.0, 4.0]


# --- what never reaches a log or a failure reason ----------------------------------------


def test_no_log_line_or_failure_reason_holds_a_link_a_credential_or_the_transcript(
    tmp_path, leader, engine, caplog
):
    with caplog.at_level(logging.DEBUG):
        leader.add_job()
        run_one(tmp_path / "ok", leader, engine)
        leader.add_job()
        leader.push("upload:txt", error(403, "forbidden"))
        leader.push("submit", error(409, "checksum_mismatch"))
        run_one(tmp_path / "again", leader, engine)
        leader.add_job()
        leader.push("download", httpx.ConnectError("cannot reach LINK-SECRET"))
        engine.error = RuntimeError("decoder state")
        run_one(tmp_path / "failed", leader, engine)
    text = caplog.text + json.dumps(leader.failed)
    assert leader.failed, "the third job must have failed"
    for secret in ("LINK-SECRET", "credential-SECRET", "/v1/files/", "Ashford", "sig="):
        assert secret not in text, secret
    assert "job completed" in caplog.text


# --- the shutdown estimate ---------------------------------------------------------------


def test_the_time_a_job_still_needs_is_estimated_from_its_progress(tmp_path, leader, engine):
    leader.add_job()
    now = [100.0]
    seen = {}
    runner, client = make_runner(tmp_path, leader, engine, clock=lambda: now[0])
    assert runner.remaining() == 0.0  # idle

    def look(fraction):
        now[0] += 10.0
        seen[fraction] = runner.remaining()

    engine.steps = (0.01, 0.25, 0.5, 1.0)
    engine.on_step = look
    leader.on["upload:txt"] = lambda: seen.__setitem__("upload", runner.remaining())
    runner.run(client.claim(), JobControl())
    # `look` runs before the step is reported, so each estimate uses the step before it.
    assert seen[0.01] is None  # nothing reported yet
    assert seen[0.25] is None  # 1 % done: too little to extrapolate from
    assert seen[0.5] == pytest.approx(30.0 * 0.75 / 0.25 + 30.0)
    assert seen[1.0] == pytest.approx(40.0 * 0.5 / 0.5 + 30.0)
    assert seen["upload"] == 30.0


# --- classify, directly ------------------------------------------------------------------


def test_classify_keeps_reasons_within_the_protocols_limit():
    code, retryable, reason = classify(RuntimeError("x" * 5000))
    assert (code, retryable, len(reason)) == ("engine_error", True, 2000)
    assert classify(Refused(400, "invalid_key", "no"))[:2] == ("other", True)


def test_a_claim_whose_job_id_is_not_a_uuid_is_ignored_entirely(tmp_path, leader, engine):
    leader.add_job()
    runner, client = make_runner(tmp_path, leader, engine)
    claim = client.claim().model_copy(update={"job_id": "../../escape"})
    result = runner.run(claim, JobControl())
    assert (result.outcome, result.detail) == ("abandoned", "invalid job id")
    assert leader.kinds == ["claim"]  # no download, no heartbeat, no fail: no request at all
    assert not (tmp_path / "escape").exists() and scratch_is_empty(tmp_path)


def test_no_lease_keeper_outlives_its_job(tmp_path, leader, engine):
    before = threading.active_count()
    for index in range(5):
        leader.add_job()
        run_one(tmp_path / str(index), leader, engine)
    assert threading.active_count() == before
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `uv run pytest packages/follower/tests/test_job.py -q`
Expected: FAIL at import: `No module named 'swarmscribe_follower.job'` (from `follower_testkit`).

- [ ] **Step 4: Write the job runner**

Create `packages/follower/src/swarmscribe_follower/job.py`:

```python
"""One job, from the claim to what the leader is told (follower spec 5.4 and 6).

`JobRunner.run` never raises: whatever happens becomes a JobResult, the leader is told what
it needs to be told (submit, fail, release, or nothing when the lease is gone), and the
job's scratch folder is deleted."""

import errno
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from swarmscribe_engine import (
    Correction,
    Device,
    TranscribeSettings,
    UndecodableAudioError,
    Vocabulary,
    write_outputs,
)
from swarmscribe_engine.transcriber import sha256_file
from swarmscribe_protocol import ClaimResponse, FailureCode, JobLinks, OutputChecksums

from .leader import Interrupted, LeaderClient, Refused, Transient, retrying
from .lease import (
    CANCELLED,
    LEASE_GONE,
    LEASE_LOST,
    REVOKED,
    SHUTDOWN,
    UNAUTHORISED,
    JobControl,
    JobStopped,
    LeaseKeeper,
)
from .models import ModelHost, ModelUnavailable, OutOfMemory, is_out_of_memory
from .scratch import Scratch
from .transfer import LeaseLost, LinkExpired, Links, OutOfSpace, OutputTooLarge, SourceChanged

logger = logging.getLogger(__name__)

OUTPUTS = ("txt", "srt", "segments_json")
DOWNLOAD_GIVE_UP_SECONDS = 600.0
UPLOAD_ALLOWANCE_SECONDS = 30.0  # what uploading and submitting add to a shutdown estimate
SUBMIT_ROUNDS = 3
SUBMIT_500_LIMIT = 5
TELL_ATTEMPTS = 3
UPLOAD_AGAIN = frozenset({"outputs_missing", "checksum_mismatch", "outputs_changed"})

# JobResult.outcome
COMPLETED = "completed"
FAILED = "failed"  # the leader was told `fail`
RELEASED = "released"  # handed back; the attempt is not counted
ABANDONED = "abandoned"  # cancelled, or the lease was lost: nothing to tell
REVOKED_FOLLOWER = "revoked"  # this follower was revoked: it must exit
UNKNOWN_CREDENTIAL = "unauthorised"  # the leader no longer knows this follower
UNFIT = "unfit"  # this machine cannot serve the claim: released, and it must exit


@dataclass(frozen=True)
class JobResult:
    outcome: str
    detail: str = ""


class _Fail(Exception):
    def __init__(self, code: FailureCode, reason: str, retryable: bool) -> None:
        super().__init__(reason)
        self.code, self.reason, self.retryable = code, reason, retryable


class _Release(Exception):
    """Hand the job back: neither the job nor this machine is at fault."""


def _reason(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"[:2000]


def classify(error: BaseException) -> tuple[FailureCode, bool, str]:
    """The `fail` a job's error means: code, retryable, reason (follower spec 6.1, 6.2)."""
    if isinstance(error, _Fail):
        return error.code, error.retryable, error.reason
    if isinstance(error, UndecodableAudioError):
        return "undecodable", False, _reason(error)
    if isinstance(error, SourceChanged):
        return "source_changed", False, _reason(error)
    if isinstance(error, OutputTooLarge):
        return "other", False, _reason(error)
    if isinstance(error, OutOfMemory | OutOfSpace) or is_out_of_memory(error):
        return "out_of_resources", True, _reason(error)
    if isinstance(error, OSError) and error.errno == errno.ENOSPC:
        return "out_of_resources", True, "OSError: no space left on the scratch disk"
    if isinstance(error, Refused):
        # A 4xx on a job route: the follower and the leader disagree about the protocol.
        return "other", True, f"the leader refused a request: {error.status} {error.code}"
    return "engine_error", True, _reason(error)


class JobRunner:
    def __init__(
        self,
        client: LeaderClient,
        links: Links,
        models: ModelHost,
        scratch: Scratch,
        *,
        device: Device,
        heartbeat_interval: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client, self._links, self._models, self._scratch = client, links, models, scratch
        self._device, self._interval = device, heartbeat_interval
        self._clock, self._sleep = clock, sleep
        self._control: JobControl | None = None
        self._phase = "idle"
        self._transcribing_since = 0.0

    # --- for the agent's shutdown decision ------------------------------------------------

    def remaining(self) -> float | None:
        """About how many seconds the current job still needs; None when that is unknown
        (follower spec 5.6). 0 when no job is running."""
        control = self._control
        if control is None:
            return 0.0
        if self._phase in ("upload", "submit"):
            return UPLOAD_ALLOWANCE_SECONDS
        progress = control.progress
        if self._phase == "transcribe" and progress is not None and progress >= 0.05:
            elapsed = self._clock() - self._transcribing_since
            return elapsed * (1.0 - progress) / progress + UPLOAD_ALLOWANCE_SECONDS
        return None

    # --- the job -------------------------------------------------------------------------

    def run(self, claim: ClaimResponse, control: JobControl) -> JobResult:
        try:
            uuid.UUID(claim.job_id)
        except ValueError:
            # It names a scratch folder and a URL path: nothing is done with one that is
            # not a UUID, and the lease is left to expire.
            logger.error("the claim's job id is not a UUID; the claim is ignored")
            return JobResult(ABANDONED, "invalid job id")
        extra = {"job_id": claim.job_id, "lease_id": claim.lease_id}
        logger.info("job claimed", extra={**extra, "event": "job.claimed"})
        keeper = LeaseKeeper(self._client, claim.job_id, claim.lease_id, self._interval, control)
        folder: Path | None = None
        self._control = control
        keeper.start()
        try:
            try:
                folder = self._scratch.job_dir(claim.job_id)
                self._work(claim, control, folder)
                result = JobResult(COMPLETED)
            except Exception as error:
                result = self._settle(claim, control, error)
        finally:
            keeper.finish()
            self._scratch.remove(folder)
            self._control, self._phase = None, "idle"
        logger.info(
            "job %s%s",
            result.outcome,
            f" ({result.detail})" if result.detail else "",
            extra={**extra, "event": f"job.{result.outcome}"},
        )
        return result

    def _settings(self, claim: ClaimResponse) -> TranscribeSettings:
        wanted = claim.settings
        return TranscribeSettings(
            model=wanted.model,
            compute_type=wanted.compute_type,
            device=self._device,
            temperatures=tuple(wanted.temperatures),
            channel_mode=wanted.channel_mode,
            channel_labels=tuple(wanted.channel_labels),
        )

    @staticmethod
    def _vocabulary(claim: ClaimResponse) -> Vocabulary:
        given = claim.vocabulary
        return Vocabulary(
            version=given.version,
            terms=tuple(given.terms),
            corrections=tuple(Correction(c.heard, c.replacement) for c in given.corrections),
        )

    def _with_fresh_links(
        self, claim: ClaimResponse, control: JobControl, links: list[JobLinks], step: Callable
    ) -> Any:
        """Run `step(links)`; if a link is refused as expired, ask the leader once for fresh
        ones and run it again. A second refusal is left to the caller (the job is released)."""
        try:
            return step(links[0])
        except LinkExpired:
            logger.info("a link has expired; asking for fresh ones", extra={"job_id": claim.job_id})
            links[0] = retrying(
                lambda: self._client.links(claim.job_id, claim.lease_id), pause=control.pause
            )
            return step(links[0])

    def _work(self, claim: ClaimResponse, control: JobControl, folder: Path) -> None:
        control.check()
        settings = self._settings(claim)
        vocabulary = self._vocabulary(claim)
        links = [JobLinks(download_url=claim.download_url, upload_urls=claim.upload_urls)]
        source = folder / "source"  # no extension: the follower never learns the storage key

        self._phase = "download"
        deadline = self._clock() + DOWNLOAD_GIVE_UP_SECONDS

        def download(current: JobLinks) -> str:
            return retrying(
                lambda: self._links.download(current.download_url, source, control.check),
                pause=control.pause,
                give_up=lambda _error, _failures: self._clock() >= deadline,
            )

        try:
            downloaded = self._with_fresh_links(claim, control, links, download)
        except Transient:
            raise _Release("the recording could not be downloaded for ten minutes") from None

        self._phase = "model"
        transcriber = self._models.get(settings.model, settings.compute_type)
        control.check()

        self._phase = "transcribe"
        self._transcribing_since = self._clock()

        def on_progress(fraction: float) -> None:
            control.progress = fraction
            control.check()  # raising here is what stops the engine

        transcript = transcriber.transcribe(
            source, vocabulary, settings=settings, progress=on_progress
        )
        if transcript.source_checksum != downloaded:
            raise _Fail("engine_error", "the recording changed on disk during the job", True)
        files = write_outputs(transcript, folder)
        control.check()
        checksums = OutputChecksums(
            source=transcript.source_checksum,
            txt=sha256_file(files.txt),
            srt=sha256_file(files.srt),
            segments_json=sha256_file(files.segments_json),
        )

        def upload(current: JobLinks) -> None:
            for name in OUTPUTS:
                control.check()
                link, path = getattr(current.upload_urls, name), getattr(files, name)
                retrying(lambda link=link, path=path: self._links.upload(link, path),
                         pause=control.pause)

        for _round in range(SUBMIT_ROUNDS):
            self._phase = "upload"
            self._with_fresh_links(claim, control, links, upload)
            self._phase = "submit"
            try:
                retrying(
                    lambda: self._client.submit(claim.job_id, claim.lease_id, checksums),
                    pause=control.pause,
                    give_up=lambda error, failures: (
                        error.status == 500 and failures >= SUBMIT_500_LIMIT
                    ),
                )
                return
            except Transient:
                raise _Fail("other", "the leader answered 500 to submit five times", True) from None
            except Refused as refused:
                if refused.status == 409 and refused.code == "outputs_inconsistent":
                    raise _Fail(
                        "engine_error", "the leader found the outputs inconsistent", False
                    ) from None
                if refused.status != 409 or refused.code not in UPLOAD_AGAIN:
                    raise
                logger.warning(
                    "submit refused (%s); uploading again", refused.code,
                    extra={"job_id": claim.job_id},
                )
        raise _Fail("other", "the leader did not accept the outputs after three uploads", True)

    # --- what the leader is told ----------------------------------------------------------

    def _tell(self, call: Callable[[], None], what: str, claim: ClaimResponse) -> bool:
        """Make one of the calls that end a job, retried a few times. If it cannot be
        delivered the lease simply expires, at the cost of one counted attempt."""
        extra = {"job_id": claim.job_id, "lease_id": claim.lease_id}
        for attempt in range(TELL_ATTEMPTS):
            try:
                call()
                return True
            except Transient as error:
                wait = error.retry_after if error.retry_after is not None else 2.0**attempt
                self._sleep(min(wait, 10.0))
            except Refused as refused:
                logger.warning("%s refused (%s)", what, refused.code, extra=extra)
                return False
        logger.warning("%s could not be delivered; the lease will expire", what, extra=extra)
        return False

    @staticmethod
    def _lease_reason(control: JobControl, error: BaseException) -> str | None:
        """Why the lease is gone, if it is: from the lease keeper, or from this error."""
        if control.reason in LEASE_GONE:
            return control.reason
        if isinstance(error, JobStopped) and error.reason in LEASE_GONE:
            return error.reason
        if isinstance(error, LeaseLost):
            return LEASE_LOST
        if isinstance(error, Refused):
            if error.status == 403:
                return REVOKED
            if error.status == 401:
                return UNAUTHORISED
            if error.status == 404 or (error.status == 409 and error.code == "stale_lease"):
                return LEASE_LOST
        return None

    def _settle(self, claim: ClaimResponse, control: JobControl, error: BaseException) -> JobResult:
        job, lease = claim.job_id, claim.lease_id
        gone = self._lease_reason(control, error)
        if gone is not None:
            # The leader has already moved the job on: fail and release would be refused.
            outcome = {
                CANCELLED: ABANDONED,
                LEASE_LOST: ABANDONED,
                REVOKED: REVOKED_FOLLOWER,
                UNAUTHORISED: UNKNOWN_CREDENTIAL,
            }[gone]
            return JobResult(outcome, gone)

        def release() -> None:
            self._client.release(job, lease)

        if isinstance(error, JobStopped | Interrupted):
            self._tell(release, "release", claim)
            return JobResult(RELEASED, SHUTDOWN)
        if isinstance(error, ModelUnavailable):
            self._tell(release, "release", claim)
            return JobResult(UNFIT, str(error))
        if isinstance(error, _Release | LinkExpired | Transient):
            self._tell(release, "release", claim)
            return JobResult(RELEASED, str(error) or type(error).__name__)
        code, retryable, reason = classify(error)
        if code == "engine_error" and not isinstance(error, _Fail):
            logger.error("job failed: %s", type(error).__name__, extra={"job_id": job})
        self._tell(lambda: self._client.fail(job, lease, code, reason, retryable), "fail", claim)
        return JobResult(FAILED, code)
```

How to read `_settle`: first, is the lease gone (cancelled, lost, revoked)? Then nothing may be called. Otherwise, was the job stopped for a shutdown, or is this machine unable to serve it, or is a link beyond repair? Then it is released. Only what is left is the job's own failure, and `classify` says which.

- [ ] **Step 5: Run the tests to see them pass**

Run: `uv run pytest packages/follower/tests/test_job.py -q`
Expected: PASS, in a few seconds.

Run: `uv run ruff check packages/follower`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add packages/follower
git commit -m "Follower: one job from claim to submit, and what the leader is told when it goes wrong"
```

---

### Task 9: The agent

**Files:**
- Create: `packages/follower/src/swarmscribe_follower/agent.py`
- Test: `packages/follower/tests/test_agent.py`

**Interfaces:**
- Consumes: everything above; `make_agent`, `FakeLeader`, `FakeEngine` from the test kit.
- Produces:
  - `agent.Agent(settings, *, client, links, models, scratch, store, probe, heartbeat_interval: float | None = None, parked_poll_seconds: float = 60.0, rng=random.random, clock=time.monotonic, sleep=time.sleep)`.
  - `prepare() -> None` — scratch, the device's default model with a warm-up, then the credential; raises `FollowerExit`.
  - `obtain_credential() -> None`, `register() -> None` — raise `FollowerExit`.
  - `serve() -> int` — the claim loop; returns the exit code; deregisters (unless revoked), closes the model, wipes scratch.
  - `stop(*, now: bool = False) -> None` — callable from any thread and from a signal handler.
  - Attributes `follower_id: str | None`, `drained: bool`, `exit_reason: str`.

- [ ] **Step 1: Write the failing tests**

Create `packages/follower/tests/test_agent.py`:

```python
import json
import threading
import time

import httpx
import pytest
from follower_testkit import JOIN_TOKEN, FakeEngine, FakeLeader, error, make_agent
from swarmscribe_follower.errors import FollowerExit


@pytest.fixture
def leader():
    return FakeLeader()


@pytest.fixture
def engine():
    return FakeEngine()


class Served:
    """An agent serving on its own thread, as `swarmscribe-follower run` runs it."""

    def __init__(self, agent):
        self.agent = agent
        self.codes = []
        self.thread = threading.Thread(target=lambda: self.codes.append(agent.serve()))
        self.thread.start()

    def code(self, timeout=10):
        self.thread.join(timeout)
        assert not self.thread.is_alive(), "the agent did not stop"
        return self.codes[0]


def start(tmp_path, leader, engine, **overrides) -> Served:
    agent = make_agent(tmp_path, leader, engine, **overrides)
    agent.prepare()
    return Served(agent)


def start_and_stop(tmp_path, leader, engine) -> None:
    """A first run of the follower that registered, was stopped and has fully ended."""
    served = start(tmp_path, leader, engine)
    served.agent.stop()
    assert served.code() == 0


def until(condition, what="the condition"):
    deadline = time.monotonic() + 10
    while not condition():
        assert time.monotonic() < deadline, f"{what} never happened"
        time.sleep(0.005)


def stored_credential(tmp_path) -> dict:
    return json.loads((tmp_path / "state" / "credential.json").read_text(encoding="utf-8"))


# --- start-up and the credential (spec 5.2, 5.3) -----------------------------------------


def test_the_model_is_loaded_before_the_leader_hears_of_the_follower(tmp_path, leader, engine):
    agent = make_agent(tmp_path, leader, engine)
    leader.on["register"] = lambda: seen.append(list(engine.loads))
    seen = []
    agent.prepare()
    assert seen == [[("distil-large-v3", "int8", "cpu")]]
    (reported,) = leader.capabilities
    assert (reported["device"], reported["pool"], reported["models"]) == (
        "cpu",
        "default",
        ["distil-large-v3"],
    )
    assert stored_credential(tmp_path)["credential"] == "credential-SECRET-1"


def test_a_machine_that_cannot_transcribe_exits_3_without_registering(tmp_path, leader, engine):
    engine.load_error = RuntimeError("Library cublas64_12.dll is not found")
    with pytest.raises(FollowerExit) as stop:
        make_agent(tmp_path, leader, engine).prepare()
    assert stop.value.code == 3
    assert "cublas64_12" in stop.value.reason and "doctor" in stop.value.reason
    assert leader.registrations == 0 and leader.kinds == []


def test_a_scratch_folder_that_is_not_the_followers_exits_2(tmp_path, leader, engine):
    (tmp_path / "state" / "scratch").mkdir(parents=True)
    (tmp_path / "state" / "scratch" / "thesis.docx").write_bytes(b"years of work")
    with pytest.raises(FollowerExit) as stop:
        make_agent(tmp_path, leader, engine).prepare()
    assert stop.value.code == 2
    assert (tmp_path / "state" / "scratch" / "thesis.docx").exists()
    assert leader.kinds == []


def test_a_second_start_reuses_the_credential_and_needs_no_token(tmp_path, leader, engine):
    start_and_stop(tmp_path, leader, engine)
    again = make_agent(tmp_path, leader, engine, join_token=None)
    again.prepare()
    served = Served(again)
    until(lambda: leader.count("claim") >= 2, "the second agent's claim")
    again.stop()
    assert served.code() == 0
    assert leader.registrations == 1


@pytest.mark.parametrize(
    ("overrides", "push", "code", "words"),
    [
        ({"join_token": None}, None, 4, "no join token"),
        ({"join_token": "wrong"}, None, 4, "not valid"),
        ({}, error(409, "protocol_version"), 5, "another protocol"),
        ({}, error(422, "invalid_request"), 5, "refused the registration"),
    ],
)
def test_a_registration_that_cannot_succeed_exits_and_is_not_retried(
    tmp_path, leader, engine, overrides, push, code, words
):
    if push is not None:
        leader.push("register", push)
    with pytest.raises(FollowerExit) as stop:
        make_agent(tmp_path, leader, engine, **overrides).prepare()
    assert stop.value.code == code and words in stop.value.reason
    assert leader.count("register") <= 1
    assert JOIN_TOKEN not in stop.value.reason
    assert not (tmp_path / "state" / "credential.json").exists()


def test_a_registration_waits_out_a_leader_that_is_down(tmp_path, leader, engine):
    leader.push("register", httpx.ConnectError("refused"), error(503, "unavailable"))
    agent = make_agent(tmp_path, leader, engine)
    agent._stopping.wait = lambda seconds: False  # do not really wait between retries
    agent.prepare()
    assert (leader.count("register"), leader.registrations) == (3, 1)


def test_a_credential_for_another_leader_or_device_is_not_used(tmp_path, leader, engine):
    start_and_stop(tmp_path, leader, engine)
    path = tmp_path / "state" / "credential.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**data, "leader_url": "https://other.test"}), encoding="utf-8")
    if hasattr(path, "chmod"):
        path.chmod(0o600)
    make_agent(tmp_path, leader, engine).prepare()
    assert leader.registrations == 2
    assert stored_credential(tmp_path)["leader_url"] == "https://leader.test"


# --- the loop ----------------------------------------------------------------------------


def test_jobs_are_taken_one_after_another_and_a_stop_while_idle_exits_0(tmp_path, leader, engine):
    first, second = leader.add_job(b"one"), leader.add_job(b"two")
    served = start(tmp_path, leader, engine)
    until(lambda: len(leader.submitted) == 2, "both jobs")
    until(lambda: leader.count("claim") >= 4, "idle polling")
    served.agent.stop()
    assert served.code() == 0
    assert [done["job_id"] for done in leader.submitted] == [first, second]
    assert leader.deregistrations == 1
    assert len(engine.loads) == 1  # one model load served both jobs
    assert engine.closed == 1  # and it is closed on the way out
    assert stored_credential(tmp_path)  # the credential outlives a clean stop


def test_an_idle_follower_waits_what_the_leader_says_plus_jitter(tmp_path, leader, engine):
    leader.retry_after = "10"
    agent = make_agent(tmp_path, leader, engine)
    agent.prepare()
    agent._rng = lambda: 0.5
    waits = []

    def wait(seconds):
        waits.append(seconds)
        return len(waits) >= 3  # the third wait is cut short by a stop

    agent._stopping.wait = wait
    assert agent.serve() == 0
    assert waits == [11.0, 11.0, 11.0]


def test_a_leader_outage_while_idle_is_waited_out(tmp_path, leader, engine):
    down = error(503, "unavailable", **{"Retry-After": "0"})
    leader.push("claim", httpx.ConnectError("refused"), down)
    job_id = leader.add_job()
    agent = make_agent(tmp_path, leader, engine)
    agent.prepare()
    agent._rng = lambda: 0.0
    served = Served(agent)
    until(lambda: leader.submitted, "the job after the outage")
    agent.stop()
    assert served.code() == 0 and leader.jobs[job_id]["state"] == "completed"


def test_a_claim_the_leader_refuses_oddly_is_logged_and_asked_again(tmp_path, leader, engine):
    leader.push("claim", error(422, "invalid_request"), httpx.Response(200, json={"job_id": 1}))
    agent = make_agent(tmp_path, leader, engine)
    agent.prepare()
    waits = []

    def wait(seconds):
        waits.append(seconds)
        return len(waits) >= 3

    agent._stopping.wait = wait
    assert agent.serve() == 0
    assert waits[:2] == [60.0, 60.0]


# --- drain (spec 5.5, 12.3) --------------------------------------------------------------


def test_a_drained_idle_follower_exits_cleanly_and_a_restart_does_not_re_register(
    tmp_path, leader, engine
):
    served = start(tmp_path, leader, engine)
    until(lambda: leader.count("claim") >= 2, "idle polling")
    leader.state = "draining"
    assert served.code() == 0  # nobody called stop: the drain ended it
    assert (served.agent.drained, served.agent.exit_reason) == (True, "drained")
    assert leader.deregistrations == 1
    # What a service manager's restart does: the same state folder, the same credential.
    restarted = start(tmp_path, leader, engine)
    assert restarted.code() == 0
    assert leader.registrations == 1
    assert stored_credential(tmp_path)["credential"] == "credential-SECRET-1"


def test_a_drain_that_arrives_mid_job_lets_the_job_finish_first(tmp_path, leader, engine):
    job_id = leader.add_job()
    engine.on_step = lambda fraction: setattr(leader, "state", "draining")
    served = start(tmp_path, leader, engine)
    assert served.code() == 0
    assert leader.jobs[job_id]["state"] == "completed"
    assert (served.agent.drained, served.agent.exit_reason) == (True, "drained")


def test_a_parked_follower_stays_up_takes_nothing_and_resumes_if_the_drain_ends(
    tmp_path, leader, engine
):
    leader.state = "draining"
    job_id = leader.add_job()
    served = start(tmp_path, leader, engine, on_drained="park")
    until(lambda: leader.count("claim") >= 4, "the parked follower's polling")
    assert served.thread.is_alive() and served.agent.drained
    assert leader.jobs[job_id]["state"] == "queued"
    leader.state = "active"
    until(lambda: leader.submitted, "the job after the drain ended")
    assert served.agent.drained is False
    served.agent.stop()
    assert served.code() == 0


# --- revocation and an unknown credential (spec 6.5) -------------------------------------


def test_a_revoked_follower_exits_4_keeps_its_credential_and_never_comes_back(
    tmp_path, leader, engine
):
    served = start(tmp_path, leader, engine)
    until(lambda: leader.count("claim") >= 2, "idle polling")
    leader.state = "revoked"
    assert served.code() == 4
    assert served.agent.exit_reason == "this follower has been revoked"
    assert leader.deregistrations == 0
    assert stored_credential(tmp_path)["credential"] == "credential-SECRET-1"
    for _restart in range(3):  # a service manager that restarts it anyway
        assert start(tmp_path, leader, engine).code() == 4
    assert leader.registrations == 1  # the join token it still holds was never used again


def test_a_follower_revoked_mid_job_exits_4_without_a_call_for_the_job(tmp_path, leader, engine):
    leader.add_job()

    def revoke(fraction):
        if fraction == 0.5:
            leader.state = "revoked"
            until(lambda: served.agent._control.reason is not None, "the keeper to notice")

    engine.on_step = revoke
    served = start(tmp_path, leader, engine)
    assert served.code() == 4
    assert (leader.failed, leader.released, leader.deregistrations) == ([], [], 0)


def test_an_unknown_credential_registers_once_more_then_gives_up(tmp_path, leader, engine):
    served = start(tmp_path, leader, engine)
    until(lambda: leader.count("claim") >= 2, "idle polling")
    leader.credentials.clear()  # the leader's database was replaced
    until(lambda: served.agent._client.credential == "credential-SECRET-2", "registering again")
    until(lambda: leader.count("claim") >= 4, "claims with the new credential")
    assert stored_credential(tmp_path)["credential"] == "credential-SECRET-2"
    leader.credentials.clear()
    assert served.code() == 4
    assert leader.registrations == 2


def test_an_unknown_credential_without_a_token_exits_4(tmp_path, leader, engine):
    start_and_stop(tmp_path, leader, engine)
    leader.credentials.clear()
    again = make_agent(tmp_path, leader, engine, join_token=None)
    again.prepare()
    assert again.serve() == 4
    assert "no join token" in again.exit_reason


# --- a machine that cannot serve its pool (D11) ------------------------------------------


def test_a_claim_for_a_model_this_machine_lacks_releases_it_and_exits_3(tmp_path, leader, engine):
    job_id = leader.add_job(model="large-v3", compute_type="float16")
    engine.unavailable = {"large-v3"}
    served = start(tmp_path, leader, engine)
    assert served.code() == 3
    assert "cannot serve its pool" in served.agent.exit_reason
    assert (leader.released, leader.failed) == ([job_id], [])
    assert leader.jobs[job_id]["attempts"] == 0
    assert leader.deregistrations == 1


# --- shutdown (spec 5.6) -----------------------------------------------------------------


def blocked_at(engine, fraction_to_block):
    """Make the fake engine wait at one step until released; returns (reached, release)."""
    reached, release = threading.Event(), threading.Event()

    def step(fraction):
        if fraction == fraction_to_block:
            reached.set()
            assert release.wait(10)

    engine.on_step = step
    return reached, release


def test_a_stop_that_cannot_wait_for_the_job_releases_it(tmp_path, leader, engine):
    job_id = leader.add_job()
    reached, release = blocked_at(engine, 0.5)
    served = start(tmp_path, leader, engine, shutdown_grace_seconds=0)
    assert reached.wait(10)
    served.agent.stop()
    release.set()
    assert served.code() == 0
    assert (leader.released, leader.submitted) == ([job_id], [])
    assert leader.jobs[job_id]["attempts"] == 0
    assert leader.kinds[-1] == "deregister"


def test_a_stop_with_room_for_the_job_lets_it_finish(tmp_path, leader, engine):
    job_id = leader.add_job()
    reached, release = blocked_at(engine, 0.5)
    served = start(tmp_path, leader, engine, shutdown_grace_seconds=3600)
    assert reached.wait(10)
    served.agent.stop()
    release.set()
    assert served.code() == 0
    assert leader.jobs[job_id]["state"] == "completed" and leader.released == []
    assert leader.count("claim") == 1  # nothing more was claimed


def test_a_stop_before_any_progress_is_known_releases(tmp_path, leader, engine):
    job_id = leader.add_job()
    reached, release = blocked_at(engine, 0.25)  # nothing reported yet: no estimate
    served = start(tmp_path, leader, engine, shutdown_grace_seconds=3600)
    assert reached.wait(10)
    served.agent.stop()
    release.set()
    assert served.code() == 0 and leader.released == [job_id]


def test_a_second_stop_releases_a_job_that_was_being_finished(tmp_path, leader, engine):
    job_id = leader.add_job()
    reached, release = blocked_at(engine, 0.75)
    served = start(tmp_path, leader, engine, shutdown_grace_seconds=3600)
    assert reached.wait(10)
    served.agent.stop()
    served.agent.stop()
    release.set()
    assert served.code() == 0 and leader.released == [job_id]


def test_the_grace_running_out_releases_a_job_that_did_not_finish_in_time(
    tmp_path, leader, engine
):
    job_id = leader.add_job()
    reached, release = blocked_at(engine, 0.75)
    served = start(tmp_path, leader, engine, shutdown_grace_seconds=31)
    assert reached.wait(10)
    served.agent._runner._transcribing_since = served.agent._clock()  # estimate: 30 s left
    served.agent._settings.shutdown_grace_seconds = 0.05  # and the grace then runs out
    served.agent.stop()
    until(lambda: served.agent._control.reason == "shutdown", "the grace timer")
    release.set()
    assert served.code() == 0 and leader.released == [job_id]


def test_a_release_that_cannot_be_delivered_does_not_keep_the_follower_up(
    tmp_path, leader, engine
):
    leader.add_job()
    reached, release = blocked_at(engine, 0.5)
    served = start(tmp_path, leader, engine, shutdown_grace_seconds=0)
    assert reached.wait(10)
    leader.push("release", *[httpx.ConnectError("refused")] * 3)
    leader.push("deregister", httpx.ConnectError("refused"))
    served.agent.stop()
    release.set()
    assert served.code() == 0
    assert leader.count("release") == 3 and leader.released == []


def test_stop_never_blocks_and_can_be_called_from_any_thread_at_any_time(
    tmp_path, leader, engine
):
    agent = make_agent(tmp_path, leader, engine)
    agent.stop()  # before prepare
    agent.prepare()
    agent.stop(now=True)
    assert agent.serve() == 0
    assert leader.count("claim") == 0
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest packages/follower/tests/test_agent.py -q`
Expected: FAIL: `ModuleNotFoundError: No module named 'swarmscribe_follower.agent'` (raised inside `make_agent`).

- [ ] **Step 3: Write the agent**

Create `packages/follower/src/swarmscribe_follower/agent.py`:

```python
"""The follower's life: prepare, register, claim and work until told to stop (follower
spec 5.2, 5.3, 5.6).

`serve()` is the worker loop and runs on a thread of its own. `stop()` may be called from
any thread, including a signal handler on the main thread: it never waits and never talks
to the leader."""

import logging
import random
import threading
import time
from collections.abc import Callable

from swarmscribe_protocol import ClaimResponse

from .config import Settings
from .credentials import CredentialFileError, CredentialStore, Stored
from .device import Probe, cached_models, capabilities
from .errors import (
    EXIT_CONFIGURATION,
    EXIT_OK,
    EXIT_PROTOCOL,
    EXIT_UNAUTHORISED,
    EXIT_UNFIT,
    FollowerExit,
)
from .job import REVOKED_FOLLOWER, UNFIT, UNKNOWN_CREDENTIAL, JobResult, JobRunner
from .leader import Interrupted, LeaderClient, NoWork, Refused, Transient, retrying
from .lease import SHUTDOWN, JobControl
from .models import ModelHost, ModelUnavailable, OutOfMemory
from .scratch import Scratch, ScratchNotOurs
from .transfer import Links

logger = logging.getLogger(__name__)

IDLE_JITTER = 0.2  # an idle follower waits Retry-After plus up to this fraction of it
PARKED_POLL_SECONDS = 60.0  # how often a drained, parked follower asks again
REFUSED_CLAIM_WAIT_SECONDS = 60.0


class Agent:
    def __init__(
        self,
        settings: Settings,
        *,
        client: LeaderClient,
        links: Links,
        models: ModelHost,
        scratch: Scratch,
        store: CredentialStore,
        probe: Probe,
        heartbeat_interval: float | None = None,
        parked_poll_seconds: float = PARKED_POLL_SECONDS,
        rng: Callable[[], float] = random.random,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._settings, self._client, self._links = settings, client, links
        self._models, self._scratch, self._store, self._probe = models, scratch, store, probe
        self._interval_override, self._parked_poll = heartbeat_interval, parked_poll_seconds
        self._rng, self._clock, self._sleep = rng, clock, sleep
        self._stopping = threading.Event()
        self._lock = threading.Lock()
        self._control: JobControl | None = None
        self._timer: threading.Timer | None = None
        self._runner: JobRunner | None = None
        self._registered_again = False
        self._revoked = False
        self.follower_id: str | None = None
        self.drained = False
        self.exit_reason = ""

    # --- before the loop -----------------------------------------------------------------

    def prepare(self) -> None:
        """Everything that can fail before the leader hears of this follower: the scratch
        folder, the device's default model and a real inference with it. Then the
        credential. Raises FollowerExit."""
        try:
            self._scratch.prepare()
        except ScratchNotOurs as error:
            raise FollowerExit(EXIT_CONFIGURATION, str(error)) from None
        choice = self._probe.choice
        try:
            self._models.get(choice.model, choice.compute_type)
        except (ModelUnavailable, OutOfMemory) as error:
            raise FollowerExit(
                EXIT_UNFIT,
                f"this machine cannot transcribe: {error}. Run `swarmscribe-follower doctor`.",
            ) from None
        self.obtain_credential()

    def obtain_credential(self) -> None:
        """Use the stored credential if it is for this leader and device; otherwise register."""
        try:
            stored = self._store.load()
        except CredentialFileError as error:
            raise FollowerExit(EXIT_CONFIGURATION, str(error)) from None
        mine = (
            stored is not None
            and stored.leader_url == self._settings.leader_url
            and stored.device == self._probe.choice.device
        )
        if mine:
            self._use(stored)
        else:
            self.register()

    def _use(self, stored: Stored) -> None:
        self._client.credential = stored.credential
        self.follower_id = stored.follower_id
        interval = self._interval_override or float(stored.heartbeat_interval)
        self._runner = JobRunner(
            self._client,
            self._links,
            self._models,
            self._scratch,
            device=self._probe.choice.device,
            heartbeat_interval=interval,
            clock=self._clock,
            sleep=self._sleep,
        )

    def register(self) -> None:
        """Exchange the join token (or pool token) for a credential and store it."""
        try:
            token = self._settings.token()
        except ValueError as error:
            raise FollowerExit(EXIT_CONFIGURATION, str(error)) from None
        if token is None:
            raise FollowerExit(
                EXIT_UNAUTHORISED,
                "this follower has no credential for this leader and no join token; set"
                " SWARMSCRIBE_JOIN_TOKEN or run `swarmscribe-follower join`",
            )
        models = cached_models(self._settings.model_dir)
        if self._models.loaded is not None:
            models.append(self._models.loaded[0])
        reported = capabilities(self._probe, self._settings.pool, models)
        try:
            answer = retrying(
                lambda: self._client.register(token, reported), pause=self._stopping.wait
            )
        except Interrupted:
            raise FollowerExit(EXIT_OK, "stopped before registering") from None
        except Refused as refused:
            if refused.code == "protocol_version":
                raise FollowerExit(
                    EXIT_PROTOCOL, f"the leader speaks another protocol: {refused.message}"
                ) from None
            if refused.status == 401:
                raise FollowerExit(
                    EXIT_UNAUTHORISED,
                    "the join token is not valid (unknown, expired, revoked or used up);"
                    " ask an administrator for a new one",
                ) from None
            raise FollowerExit(
                EXIT_PROTOCOL,
                f"the leader refused the registration ({refused.status} {refused.code})",
            ) from None
        stored = Stored(
            leader_url=self._settings.leader_url,
            follower_id=answer.follower_id,
            credential=answer.credential,
            device=self._probe.choice.device,
            heartbeat_interval=answer.heartbeat_interval,
            lease_seconds=answer.lease_seconds,
        )
        try:
            self._store.save(stored)
        except CredentialFileError as error:
            raise FollowerExit(EXIT_CONFIGURATION, str(error)) from None
        self._use(stored)
        logger.info(
            "registered", extra={"event": "registered", "follower_id": self.follower_id}
        )

    # --- the loop ------------------------------------------------------------------------

    def serve(self) -> int:
        """Claim and work until stopped, drained (and set to exit), revoked or unfit.
        Returns the process's exit code."""
        code = EXIT_OK
        try:
            self._loop()
        except FollowerExit as stop:
            code, self.exit_reason = stop.code, stop.reason
            log = logger.info if code == EXIT_OK else logger.error
            log("stopping: %s", stop.reason, extra={"follower_id": self.follower_id})
        finally:
            if not self._revoked:
                self._deregister()
            self._models.close()
            self._scratch.wipe()
        return code

    def _loop(self) -> None:
        while not self._stopping.is_set():
            try:
                answer = retrying(self._client.claim, pause=self._stopping.wait)
            except Interrupted:
                return
            except Refused as refused:
                self._claim_refused(refused)
                continue
            if isinstance(answer, NoWork):
                if self._idle(answer):
                    return
                continue
            self.drained = False
            result = self._run(answer)
            if result.outcome == REVOKED_FOLLOWER:
                self._exit_revoked()
            if result.outcome == UNKNOWN_CREDENTIAL:
                self._register_again()
            if result.outcome == UNFIT:
                raise FollowerExit(
                    EXIT_UNFIT, f"this machine cannot serve its pool: {result.detail}"
                )

    def _idle(self, answer: NoWork) -> bool:
        """Wait before the next claim. True means: leave the loop."""
        if answer.draining:
            if not self.drained:
                self.drained = True
                logger.info(
                    "the leader is draining this follower; it will be given nothing more",
                    extra={"event": "drained", "follower_id": self.follower_id},
                )
            if self._settings.on_drained == "exit":
                raise FollowerExit(EXIT_OK, "drained")
            # Parked: stay up (a service manager would only start it again) and keep asking,
            # slowly, so the leader sees it alive and a revocation is noticed.
            wait = max(answer.retry_after, self._parked_poll)
        else:
            self.drained = False
            wait = answer.retry_after * (1.0 + IDLE_JITTER * self._rng())
        return self._stopping.wait(wait)

    def _claim_refused(self, refused: Refused) -> None:
        if refused.status == 403:
            self._exit_revoked()
        if refused.status == 401:
            self._register_again()
            return
        logger.error(
            "the leader refused a claim (%s %s)", refused.status, refused.code,
            extra={"follower_id": self.follower_id},
        )
        self._stopping.wait(REFUSED_CLAIM_WAIT_SECONDS)

    def _exit_revoked(self) -> None:
        # The credential file is kept on purpose: a restart finds it, is refused again and
        # exits again. Deleting it would let a join token undo the revocation.
        self._revoked = True
        raise FollowerExit(EXIT_UNAUTHORISED, "this follower has been revoked")

    def _register_again(self) -> None:
        """The leader does not know the stored credential (its database was replaced, or
        the registration was given to another machine): register once more, if there is a
        token to do it with."""
        if self._registered_again:
            raise FollowerExit(
                EXIT_UNAUTHORISED, "the leader does not know this follower's credential"
            )
        self._registered_again = True
        logger.warning("the leader does not know this follower's credential; registering again")
        self.register()

    def _run(self, claim: ClaimResponse) -> JobResult:
        control = JobControl()
        with self._lock:
            self._control = control
            if self._stopping.is_set():  # a stop arrived between the claim and here
                control.stop(SHUTDOWN)
        try:
            return self._runner.run(claim, control)
        finally:
            with self._lock:
                self._control = None
                if self._timer is not None:
                    self._timer.cancel()
                    self._timer = None

    def _deregister(self) -> None:
        if not self._client.credential:
            return
        try:
            self._client.deregister()
        except (Transient, Refused):
            logger.warning("could not deregister; the leader will notice the silence")

    # --- stopping ------------------------------------------------------------------------

    def stop(self, *, now: bool = False) -> None:
        """Stop claiming. A job in progress is finished only if its estimated time left
        fits the grace period; otherwise, and on a second call, it is released."""
        with self._lock:
            again = self._stopping.is_set()
            self._stopping.set()
            control = self._control
            if control is None:
                return
            if now or again or self._runner is None:
                control.stop(SHUTDOWN)
                return
            grace = self._settings.shutdown_grace_seconds
            remaining = self._runner.remaining()
            if remaining is not None and remaining <= grace:
                logger.info("stopping after the current job (about %d s left)", remaining)
                self._timer = threading.Timer(grace, control.stop, args=(SHUTDOWN,))
                self._timer.daemon = True
                self._timer.start()
            else:
                logger.info("stopping now; the current job is released")
                control.stop(SHUTDOWN)
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest packages/follower/tests/test_agent.py -q`
Expected: PASS.

Run: `uv run pytest packages/follower -q && uv run ruff check packages/follower`
Expected: PASS; `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add packages/follower
git commit -m "Follower agent: register, claim and work; drain, revocation and a stop that strands nothing"
```

---

### Task 10: The command line

**Files:**
- Create: `packages/follower/src/swarmscribe_follower/main.py`
- Test: `packages/follower/tests/test_main.py`

**Interfaces:**
- Consumes: `Agent` (Task 9), `Settings`, `logs.configure` (Task 1), `CredentialStore` (Task 2), `probe`, `cached_models` (Task 6), `ModelHost`, `LeaderClient`, `Links`, `Scratch`.
- Produces:
  - Console script `swarmscribe-follower` (`main:run`) with commands `run`, `join [--token-stdin]`, `leave`, `doctor`.
  - `main.main(argv=None, *, build=build, out=None, err=None) -> int`.
  - `main.build(settings) -> Agent`, `main.load_settings(err) -> Settings | None`, `main.configure_environment(settings)`, `main.tls(settings)`, `main.hold_state_lock(state_dir)` (returns an open file; close it to release), `main.install_signals(agent)`, `main.command_doctor(settings, out, *, host=ModelHost, client=LeaderClient) -> int`.

- [ ] **Step 1: Write the failing tests**

Create `packages/follower/tests/test_main.py`:

```python
import io
import json
import logging
import os
import signal

import pytest
from follower_testkit import BASE, CPU, JOIN_TOKEN, FakeEngine, FakeLeader, make_agent
from swarmscribe_follower import main as cli
from swarmscribe_follower.config import Settings
from swarmscribe_follower.device import Probe
from swarmscribe_follower.errors import FollowerExit
from swarmscribe_follower.leader import LeaderClient
from swarmscribe_follower.models import ModelHost


@pytest.fixture
def leader():
    return FakeLeader()


@pytest.fixture
def engine():
    return FakeEngine()


@pytest.fixture(autouse=True)
def environment(monkeypatch, tmp_path):
    for name in list(os.environ):
        if name.startswith("SWARMSCRIBE_") or name in ("HF_HUB_CACHE", "HF_HUB_OFFLINE"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("SWARMSCRIBE_LEADER_URL", BASE)
    monkeypatch.setenv("SWARMSCRIBE_JOIN_TOKEN", JOIN_TOKEN)
    monkeypatch.setenv("SWARMSCRIBE_FOLLOWER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SWARMSCRIBE_FOLLOWER_MODEL_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("SWARMSCRIBE_FOLLOWER_DEVICE", "cpu")
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    signals = {
        name: signal.getsignal(getattr(signal, name))
        for name in ("SIGINT", "SIGTERM", "SIGBREAK")
        if hasattr(signal, name)
    }
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
    for name, previous in signals.items():
        signal.signal(getattr(signal, name), previous)


def run_cli(tmp_path, leader, engine, *argv, **overrides):
    out, err = io.StringIO(), io.StringIO()
    built = []

    def build(settings):
        built.append(make_agent(tmp_path, leader, engine, **overrides))
        return built[-1]

    code = cli.main(list(argv), build=build, out=out, err=err)
    return code, out.getvalue(), err.getvalue(), built


@pytest.mark.parametrize(
    ("name", "value", "said"),
    [
        ("SWARMSCRIBE_FOLLOWER_DEVICE", "tpu", "device"),
        ("SWARMSCRIBE_LEADER_URL", "http://leader.example.org", "https"),
        ("SWARMSCRIBE_FOLLOWER_SHUTDOWN_GRACE_SECONDS", "-1", "shutdown_grace_seconds"),
    ],
)
def test_invalid_configuration_exits_2_naming_fields_and_never_values(
    monkeypatch, name, value, said
):
    monkeypatch.setenv(name, value)
    out, err = io.StringIO(), io.StringIO()
    assert cli.main(["run"], out=out, err=err) == 2
    text = err.getvalue()
    assert "invalid configuration" in text and said in text
    assert JOIN_TOKEN not in text and "Traceback" not in text


def test_run_joins_works_and_exits_0_when_drained(tmp_path, leader, engine):
    job_id = leader.add_job()
    engine.on_step = lambda fraction: setattr(leader, "state", "draining")
    code, _out, err, _ = run_cli(tmp_path, leader, engine, "run")
    assert code == 0, err
    assert leader.jobs[job_id]["state"] == "completed"
    lines = [json.loads(line) for line in err.splitlines()]
    assert {"registered", "job.claimed", "job.completed", "drained"} <= {
        line.get("event") for line in lines
    }
    assert JOIN_TOKEN not in err and "credential-SECRET" not in err


def test_run_says_why_it_cannot_start_and_exits_with_the_code(tmp_path, leader, engine):
    engine.load_error = RuntimeError("Library cublas64_12.dll is not found")
    code, _out, err, _ = run_cli(tmp_path, leader, engine, "run")
    assert code == 3
    assert err.strip().splitlines()[-1].startswith("error: this machine cannot transcribe")
    assert leader.registrations == 0


def test_a_revoked_follower_makes_run_exit_4(tmp_path, leader, engine):
    leader.on["claim"] = lambda: setattr(leader, "state", "revoked")
    code, _out, _err, _ = run_cli(tmp_path, leader, engine, "run")
    assert code == 4


def test_two_followers_cannot_share_a_state_folder(tmp_path):
    held = cli.hold_state_lock(tmp_path / "state")
    try:
        with pytest.raises(FollowerExit) as stop:
            cli.hold_state_lock(tmp_path / "state")
        assert stop.value.code == 2 and "already using" in stop.value.reason
    finally:
        held.close()
    cli.hold_state_lock(tmp_path / "state").close()  # free again once the first has ended


def test_a_signal_stops_the_agent_and_a_second_one_releases(tmp_path, leader, engine):
    agent = make_agent(tmp_path, leader, engine)
    calls = []
    agent.stop = lambda **kwargs: calls.append(kwargs)
    cli.install_signals(agent)
    for name in ("SIGINT", "SIGTERM"):
        signal.getsignal(getattr(signal, name))(getattr(signal, name), None)
    assert calls == [{}, {}]  # Agent.stop itself treats the second call as "now"


def test_join_stores_a_credential_and_leave_deletes_it(tmp_path, leader, engine, monkeypatch):
    code, out, err, _ = run_cli(tmp_path, leader, engine, "join")
    assert code == 0, err
    assert out.startswith("joined as follower ")
    assert (tmp_path / "state" / "credential.json").is_file()
    assert engine.loads == []  # joining loads no model
    monkeypatch.setattr(
        cli, "LeaderClient", lambda url, **kw: LeaderClient(
            url, credential=kw.get("credential"), transport=leader.transport
        )
    )
    code, out, _err, _ = run_cli(tmp_path, leader, engine, "leave")
    assert (code, leader.deregistrations) == (0, 1)
    assert "credential is deleted" in out
    assert not (tmp_path / "state" / "credential.json").exists()
    code, out, _err, _ = run_cli(tmp_path, leader, engine, "leave")
    assert code == 0 and "has not joined" in out


def test_join_with_a_bad_token_exits_4_and_stores_nothing(tmp_path, leader, engine):
    code, _out, err, _ = run_cli(tmp_path, leader, engine, "join", join_token="wrong")
    assert code == 4
    assert "not valid" in err and "wrong" not in err
    assert not (tmp_path / "state" / "credential.json").exists()


def test_the_model_loader_is_told_its_cache_and_offline_mode(tmp_path, monkeypatch):
    settings = Settings(leader_url=BASE, model_dir=tmp_path / "models", offline=True)
    cli.configure_environment(settings)
    assert os.environ["HF_HUB_CACHE"] == str(tmp_path / "models")
    assert os.environ["HF_HUB_OFFLINE"] == "1"
    monkeypatch.delenv("HF_HUB_CACHE")
    monkeypatch.delenv("HF_HUB_OFFLINE")
    monkeypatch.delenv("SWARMSCRIBE_FOLLOWER_MODEL_DIR")
    cli.configure_environment(Settings(leader_url=BASE))
    assert "HF_HUB_OFFLINE" not in os.environ and "HF_HUB_CACHE" not in os.environ


def test_a_ca_file_that_is_not_certificates_exits_2(tmp_path):
    bad = tmp_path / "ca.pem"
    bad.write_text("not a certificate")
    for path in (bad, tmp_path / "missing.pem"):
        with pytest.raises(FollowerExit) as stop:
            cli.tls(Settings(leader_url=BASE, leader_ca_file=path))
        assert stop.value.code == 2
    assert cli.tls(Settings(leader_url=BASE)) is True


def doctor(tmp_path, leader, engine, monkeypatch):
    monkeypatch.setattr(cli, "probe", lambda preference: Probe(CPU))
    out = io.StringIO()
    code = cli.command_doctor(
        Settings(),
        out,
        host=lambda device, **kw: ModelHost(device, factory=engine, **kw),
        client=lambda url, **kw: LeaderClient(url, transport=leader.transport),
    )
    return code, out.getvalue()


def test_doctor_reports_a_working_machine_and_registers_nothing(
    tmp_path, leader, engine, monkeypatch
):
    code, text = doctor(tmp_path, leader, engine, monkeypatch)
    assert code == 0
    assert "device: cpu" in text
    assert "model: distil-large-v3 (int8) loaded and ran" in text
    assert "leader: answers" in text and "joined: no" in text
    assert leader.registrations == 0 and leader.count("claim") == 0
    assert engine.closed == 1


def test_doctor_names_what_is_broken(tmp_path, leader, engine, monkeypatch):
    engine.load_error = RuntimeError("Library cublas64_12.dll is not found")
    code, text = doctor(tmp_path, leader, engine, monkeypatch)
    assert code == 3
    assert "model: FAILED" in text and "cublas64_12" in text
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest packages/follower/tests/test_main.py -q`
Expected: FAIL at import: `cannot import name 'main' from 'swarmscribe_follower'`.

- [ ] **Step 3: Write the command line**

Create `packages/follower/src/swarmscribe_follower/main.py`:

```python
"""swarmscribe-follower: run, join, leave, doctor (follower spec 4, 5.2, 5.3)."""

import argparse
import logging
import os
import signal
import ssl
import sys
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, TextIO

from pydantic import SecretStr, ValidationError
from swarmscribe_engine import DeviceUnavailableError

from . import FOLLOWER_VERSION, logs
from .agent import Agent
from .config import Settings
from .credentials import CredentialFileError, CredentialStore
from .device import cached_models, probe
from .errors import EXIT_CONFIGURATION, EXIT_OK, EXIT_UNFIT, FollowerExit
from .leader import LeaderClient, Refused, Transient
from .models import ModelHost, ModelUnavailable, OutOfMemory
from .scratch import Scratch
from .transfer import Links

logger = logging.getLogger(__name__)
Build = Callable[[Settings], Agent]


def load_settings(err: TextIO) -> Settings | None:
    try:
        return Settings()
    except ValidationError as error:
        # Field names and messages only: the default rendering echoes the values, and one
        # of them is the join token.
        print("error: invalid configuration (SWARMSCRIBE_* environment):", file=err)
        for problem in error.errors(include_input=False, include_url=False, include_context=False):
            field = ".".join(str(part) for part in problem["loc"]) or "settings"
            print(f"  {field}: {problem['msg']}", file=err)
        return None


def configure_environment(settings: Settings) -> None:
    """Tell the model loader where its cache is and whether it may download. Must run
    before the first model is loaded: Hugging Face's library reads these when it is
    imported."""
    if settings.model_dir is not None:
        os.environ["HF_HUB_CACHE"] = str(settings.model_dir)
    if settings.offline:
        os.environ["HF_HUB_OFFLINE"] = "1"


def tls(settings: Settings) -> Any:
    """What verifies the leader's certificate: the public roots, plus the configured CA."""
    if settings.leader_ca_file is None:
        return True
    try:
        context = ssl.create_default_context()
        context.load_verify_locations(cafile=str(settings.leader_ca_file))
    except (OSError, ssl.SSLError):
        raise FollowerExit(
            EXIT_CONFIGURATION, "SWARMSCRIBE_LEADER_CA_FILE cannot be read as PEM certificates"
        ) from None
    return context


def hold_state_lock(state_dir: Path):
    """An exclusive lock on the state folder for as long as the returned file stays open:
    two followers must never share a credential and a scratch folder."""
    try:
        state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        handle = (state_dir / "follower.lock").open("a+b")
    except OSError as error:
        why = error.strerror or type(error).__name__
        raise FollowerExit(
            EXIT_CONFIGURATION, f"the state folder {state_dir} cannot be used: {why}"
        ) from None
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise FollowerExit(
            EXIT_CONFIGURATION, f"another follower is already using the state folder {state_dir}"
        ) from None
    return handle


def build(settings: Settings) -> Agent:
    configure_environment(settings)
    try:
        found = probe(settings.device)
    except DeviceUnavailableError as error:
        raise FollowerExit(EXIT_UNFIT, str(error)) from None
    verify = tls(settings)
    return Agent(
        settings,
        client=LeaderClient(settings.leader_url, verify=verify),
        links=Links(verify=verify),
        models=ModelHost(found.choice.device, allowed=frozenset(settings.allowed_models)),
        scratch=Scratch(settings.scratch),
        store=CredentialStore(settings.credential_file),
        probe=found,
    )


def install_signals(agent: Agent) -> None:
    """SIGINT and SIGTERM (and SIGBREAK on Windows) ask the agent to stop; a second one
    releases the job at once. The handler only sets flags: it runs between two bytecodes of
    the main thread, which is why the main thread never runs a job."""

    def handler(_signum, _frame) -> None:
        agent.stop()

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        number = getattr(signal, name, None)
        if number is not None:
            signal.signal(number, handler)


def command_run(settings: Settings, build: Build) -> int:
    lock = hold_state_lock(settings.state_dir)
    try:
        agent = build(settings)
        agent.prepare()
        install_signals(agent)
        codes: list[int] = []

        def work() -> None:
            try:
                codes.append(agent.serve())
            except Exception:
                logger.exception("the follower stopped on an unexpected error")
                codes.append(1)

        worker = threading.Thread(target=work, name="worker")
        worker.start()
        while worker.is_alive():  # short joins, so that signals are handled promptly
            worker.join(0.2)
        return codes[0]
    finally:
        lock.close()


def command_join(settings: Settings, build: Build, token_stdin: bool, out: TextIO) -> int:
    if token_stdin:
        token = sys.stdin.readline().strip()
        settings = settings.model_copy(update={"join_token": SecretStr(token) if token else None})
    lock = hold_state_lock(settings.state_dir)
    try:
        agent = build(settings)
        agent.register()
        print(f"joined as follower {agent.follower_id}", file=out)
        return EXIT_OK
    finally:
        lock.close()


def command_leave(settings: Settings, out: TextIO) -> int:
    lock = hold_state_lock(settings.state_dir)
    try:
        store = CredentialStore(settings.credential_file)
        try:
            stored = store.load()
        except CredentialFileError as error:
            raise FollowerExit(EXIT_CONFIGURATION, str(error)) from None
        if stored is None:
            print("this follower has not joined a leader", file=out)
            return EXIT_OK
        client = LeaderClient(stored.leader_url, credential=stored.credential, verify=tls(settings))
        try:
            client.deregister()
        except (Transient, Refused):
            print("the leader could not be told; it will notice the silence", file=out)
        finally:
            client.close()
        store.delete()
        print("left; the credential is deleted", file=out)
        return EXIT_OK
    finally:
        lock.close()


def command_doctor(
    settings: Settings,
    out: TextIO,
    *,
    host: Callable[..., ModelHost] = ModelHost,
    client: Callable[..., LeaderClient] = LeaderClient,
) -> int:
    """What start-up checks, said out loud: the device, the model with a real inference,
    the leader. Registers nothing and claims nothing."""
    configure_environment(settings)
    print(f"swarmscribe-follower {FOLLOWER_VERSION}", file=out)
    try:
        found = probe(settings.device)
    except DeviceUnavailableError as error:
        print(f"device: FAILED: {error}", file=out)
        return EXIT_UNFIT
    choice = found.choice
    gpu = f" ({found.gpu_name}, {found.gpu_memory_mb} MiB)" if found.gpu_name else ""
    print(f"device: {choice.device}{gpu}", file=out)
    print(f"cached models: {', '.join(cached_models(settings.model_dir)) or '(none)'}", file=out)
    code = EXIT_OK
    models = host(choice.device, allowed=frozenset(settings.allowed_models))
    try:
        models.get(choice.model, choice.compute_type)
        print(f"model: {choice.model} ({choice.compute_type}) loaded and ran", file=out)
    except (ModelUnavailable, OutOfMemory) as error:
        print(f"model: FAILED: {error}", file=out)
        code = EXIT_UNFIT
    finally:
        models.close()
    leader = client(settings.leader_url, verify=tls(settings))
    try:
        if leader.healthy():
            print("leader: answers", file=out)
        else:
            print("leader: FAILED: no answer from its /healthz", file=out)
            code = code or 1
    finally:
        leader.close()
    joined = CredentialStore(settings.credential_file).path.is_file()
    print(f"joined: {'yes' if joined else 'no'}", file=out)
    return code


def main(
    argv: Sequence[str] | None = None,
    *,
    build: Build = build,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    out, err = out or sys.stdout, err or sys.stderr
    parser = argparse.ArgumentParser(
        prog="swarmscribe-follower",
        description="Take recordings from a SwarmScribe leader and transcribe them.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run", help="join if needed, then work until stopped")
    join = commands.add_parser("join", help="register with the leader and store the credential")
    join.add_argument(
        "--token-stdin", action="store_true", help="read the join token from standard input"
    )
    commands.add_parser("leave", help="deregister and delete the stored credential")
    commands.add_parser("doctor", help="check the device, the model and the leader")
    args = parser.parse_args(argv)
    settings = load_settings(err)
    if settings is None:
        return EXIT_CONFIGURATION
    logs.configure(settings.log_format, stream=err)
    try:
        if args.command == "run":
            return command_run(settings, build)
        if args.command == "join":
            return command_join(settings, build, args.token_stdin, out)
        if args.command == "leave":
            return command_leave(settings, out)
        return command_doctor(settings, out)
    except FollowerExit as stop:
        if stop.code != EXIT_OK:
            print(f"error: {stop.reason}", file=err)
        return stop.code


def run() -> None:
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest packages/follower/tests/test_main.py -q`
Expected: PASS.

Run: `uv sync && uv run swarmscribe-follower --help`
Expected: the usage text listing `run`, `join`, `leave`, `doctor`.

Run, with `SWARMSCRIBE_LEADER_URL` unset: `uv run swarmscribe-follower run; echo "exit $?"` (in PowerShell: `python -m uv run swarmscribe-follower run; echo "exit $LASTEXITCODE"`)
Expected: `error: invalid configuration (SWARMSCRIBE_* environment):`, the line `  leader_url: Field required`, then `exit 2`, and no traceback.

- [ ] **Step 5: Commit**

```bash
git add packages/follower
git commit -m "swarmscribe-follower: run, join, leave and doctor"
```

---

### Task 11: The contract, against the real leader

One test file runs the follower against the real leader application, with a real Postgres, over real HTTP on a loopback port. Only the engine is a stub, so no model is needed and the tests take seconds. This is what keeps `FakeLeader` honest.

**Files:**
- Test: `packages/follower/tests/test_real_leader.py`

**Interfaces:**
- Consumes: from F0's `leader_testkit`: `LINK_KEY`, `migrated_database(name) -> str`, `empty_tables(session)`, `add_recordings(sessionmaker, root, files, *, consent=..., public_url=..., **location)`, `new_join_token(sessionmaker)` (a join token; for a pool token call `create_pool_token`); from the leader: `create_app(settings, background=False)`, `Settings`, `make_engine`, `make_sessionmaker`, `auth.followers.drain`, `auth.followers.revoke_follower`, `auth.pool_tokens.create_pool_token`, `jobs.admin.cancel_job`; `uvicorn` (a dependency of the leader); `FakeEngine`, `CPU`, `SPOKEN` from `follower_testkit`.
- Produces: nothing other tasks use.

- [ ] **Step 1: Write the tests**

Create `packages/follower/tests/test_real_leader.py`:

```python
"""The follower against the REAL leader application, in this process, over real HTTP on a
loopback port, with a real Postgres. Only the engine is a stub, so no model is needed. This
pins the contract the fake leader in follower_testkit imitates: if the leader changes a
route, a status or a header the follower depends on, these fail."""

import asyncio
import socket
import threading
import time
import uuid

import pytest
import uvicorn
from follower_testkit import CPU, SPOKEN, FakeEngine
from leader_testkit import LINK_KEY, add_recordings, empty_tables, migrated_database
from sqlalchemy import select
from swarmscribe_follower.agent import Agent
from swarmscribe_follower.config import Settings
from swarmscribe_follower.credentials import CredentialStore
from swarmscribe_follower.device import Probe
from swarmscribe_follower.leader import LeaderClient, NoWork, Refused
from swarmscribe_follower.models import ModelHost
from swarmscribe_follower.scratch import Scratch
from swarmscribe_follower.transfer import LeaseLost, Links
from swarmscribe_leader.app import create_app
from swarmscribe_leader.auth import followers as follower_admin
from swarmscribe_leader.auth.pool_tokens import create_pool_token
from swarmscribe_leader.clock import utcnow
from swarmscribe_leader.config import Settings as LeaderSettings
from swarmscribe_leader.db.models import AuditEntry, Follower, Job, JobAttempt, JobResult
from swarmscribe_leader.db.session import make_engine, make_sessionmaker
from swarmscribe_leader.jobs import admin as job_admin
from swarmscribe_protocol import SegmentsDocument

DATABASE = "swarmscribe_kit_follower"  # the kit refuses names without this prefix


@pytest.fixture(scope="session")
def leader_database() -> str:
    return asyncio.run(migrated_database(DATABASE))


class RealLeader:
    def __init__(self, url: str, database: str) -> None:
        self.url, self.database = url, database

    def on_database(self, work):
        """Run `work(sessionmaker)` (a coroutine function) on a connection of its own."""

        async def go():
            engine = make_engine(self.database)
            try:
                return await work(make_sessionmaker(engine))
            finally:
                await engine.dispose()

        return asyncio.run(go())

    def rows(self, model):
        async def read(sessionmaker):
            async with sessionmaker() as session:
                return list((await session.scalars(select(model).order_by(model.created_at))).all())

        return self.on_database(read)

    def queue(self, root, files, **location):
        return self.on_database(
            lambda sm: add_recordings(sm, root, files, public_url=self.url, **location)
        )

    def join_token(self) -> str:
        """A pool token's plaintext, made with the leader's own function (the kit has no
        pool-token helper)."""

        async def make(sessionmaker):
            async with sessionmaker() as session:
                _row, plaintext = await create_pool_token(
                    session, name="follower-tests", pool="default", actor="follower tests"
                )
                await session.commit()
            return plaintext

        return self.on_database(make)


@pytest.fixture
def leader(leader_database):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    url = f"http://127.0.0.1:{listener.getsockname()[1]}"
    settings = LeaderSettings(
        database_url=leader_database,
        public_url=url,  # the leader's own links must be reachable by the follower
        link_key=LINK_KEY,
        lease_seconds=30,
        heartbeat_seconds=1,
        claim_retry_after=1,
        links_refresh_min_seconds=0,
    )
    running = RealLeader(url, leader_database)

    async def clear(sessionmaker):
        async with sessionmaker() as session:
            await empty_tables(session)

    running.on_database(clear)
    application = create_app(settings, background=False)
    server = uvicorn.Server(uvicorn.Config(application, log_level="error"))
    thread = threading.Thread(
        target=lambda: asyncio.run(server.serve(sockets=[listener])), daemon=True
    )
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started:
        assert time.monotonic() < deadline and thread.is_alive(), "the leader did not start"
        time.sleep(0.01)
    yield running
    server.should_exit = True
    thread.join(30)


def follower(tmp_path, leader, engine, token, name="state", **overrides) -> Agent:
    settings = Settings(
        leader_url=leader.url,
        join_token=token,
        state_dir=tmp_path / name,
        model_dir=tmp_path / "models",
        device="cpu",
        **overrides,
    )
    return Agent(
        settings,
        client=LeaderClient(leader.url),
        links=Links(),
        models=ModelHost("cpu", factory=engine),
        scratch=Scratch(settings.scratch),
        store=CredentialStore(settings.credential_file),
        probe=Probe(CPU),
        heartbeat_interval=0.1,
        parked_poll_seconds=0.1,
    )


class Served:
    def __init__(self, agent: Agent) -> None:
        self.agent, self.codes = agent, []
        agent.prepare()
        self.thread = threading.Thread(target=lambda: self.codes.append(agent.serve()))
        self.thread.start()

    def code(self) -> int:
        self.thread.join(30)
        assert not self.thread.is_alive(), "the follower did not stop"
        return self.codes[0]


def until(condition, what):
    deadline = time.monotonic() + 30
    while not condition():
        assert time.monotonic() < deadline, f"{what} never happened"
        time.sleep(0.05)


def states(leader) -> list[str]:
    return sorted(job.state for job in leader.rows(Job))


def test_a_follower_joins_and_completes_every_consented_recording(tmp_path, leader):
    root = tmp_path / "archive"
    leader.queue(
        root,
        {"talks/one.wav": b"recording one", "talks/two.wav": b"recording two",
         "private/held.wav": b"never consented"},
        consent="talks/*\n",
        channel_mode="auto",
        channel_labels=("Agent", "Customer"),
    )
    engine = FakeEngine()
    served = Served(follower(tmp_path, leader, engine, leader.join_token()))
    until(lambda: states(leader) == ["completed", "completed"], "both jobs")
    served.agent.stop()
    assert served.code() == 0

    for key in ("talks/one.wav", "talks/two.wav"):
        outputs = root / "transcripts" / key
        assert outputs.with_name(outputs.name + ".txt").read_text(encoding="utf-8") == SPOKEN + "\n"
        document = SegmentsDocument.model_validate_json(
            outputs.with_name(outputs.name + ".segments.json").read_bytes()
        )
        assert document.settings.channel_mode == "auto"
    assert not (root / "transcripts" / "private").exists()
    assert sorted(heard["audio"] for heard in engine.transcribed) == [
        b"recording one", b"recording two",
    ]
    assert {heard["settings"].channel_labels for heard in engine.transcribed} == {
        ("Agent", "Customer")
    }
    assert [attempt.outcome for attempt in leader.rows(JobAttempt)] == ["completed", "completed"]
    assert len(leader.rows(JobResult)) == 2
    (row,) = leader.rows(Follower)
    assert (row.state, row.capabilities["device"]) == ("gone", "cpu")  # it deregistered
    actions = [entry.action for entry in leader.rows(AuditEntry)]
    assert actions.count("job.submit") == 2 and "follower.deregister" in actions


def test_an_idle_follower_hears_the_drain_exits_and_stays_drained_across_restarts(
    tmp_path, leader
):
    engine = FakeEngine()
    token = leader.join_token()
    served = Served(follower(tmp_path, leader, engine, token))
    until(lambda: leader.rows(Follower), "the registration")
    (row,) = leader.rows(Follower)

    async def drain(sessionmaker):
        async with sessionmaker() as session:
            await follower_admin.drain(session, row.id, actor="test")
            await session.commit()

    leader.on_database(drain)
    assert served.code() == 0  # nobody stopped it: it heard `drain` on a 204
    assert served.agent.exit_reason == "drained"
    leader.queue(tmp_path / "archive", {"talks/one.wav": b"one"})
    for _restart in range(2):  # what a service manager does
        assert Served(follower(tmp_path, leader, engine, token)).code() == 0
    (row,) = leader.rows(Follower)  # still one follower: no restart registered again
    assert row.state == "draining"
    assert states(leader) == ["queued"] and engine.transcribed == []


def test_a_parked_follower_stays_up_under_a_restart_always_policy(tmp_path, leader):
    engine = FakeEngine()
    served = Served(follower(tmp_path, leader, engine, leader.join_token(), on_drained="park"))
    until(lambda: leader.rows(Follower), "the registration")
    (row,) = leader.rows(Follower)

    async def drain(sessionmaker):
        async with sessionmaker() as session:
            await follower_admin.drain(session, row.id, actor="test")
            await session.commit()

    leader.on_database(drain)
    until(lambda: served.agent.drained, "the drain to be heard")
    assert served.thread.is_alive()
    served.agent.stop()
    assert served.code() == 0


def test_a_revoked_follower_stops_its_job_exits_4_and_cannot_come_back(tmp_path, leader):
    leader.queue(tmp_path / "archive", {"talks/one.wav": b"one"})
    engine = FakeEngine()
    reached, release = threading.Event(), threading.Event()

    def hold(fraction):
        if fraction == 0.5:
            reached.set()
            assert release.wait(30)

    engine.on_step = hold
    token = leader.join_token()
    served = Served(follower(tmp_path, leader, engine, token))
    assert reached.wait(30)
    (row,) = leader.rows(Follower)

    async def revoke(sessionmaker):
        async with sessionmaker() as session:
            await follower_admin.revoke_follower(session, row.id, now=utcnow(), actor="test")
            await session.commit()

    leader.on_database(revoke)
    until(lambda: served.agent._control.reason is not None, "the lease keeper to notice")
    release.set()
    assert served.code() == 4
    (job,) = leader.rows(Job)
    assert (job.state, job.attempts) == ("queued", 0)  # the leader released it, uncounted
    assert not (tmp_path / "archive" / "transcripts").exists()
    engine.on_step = None
    assert Served(follower(tmp_path, leader, engine, token)).code() == 4
    assert len(leader.rows(Follower)) == 1  # the token it still holds was not used again


def test_a_stop_mid_job_releases_it_without_a_counted_attempt(tmp_path, leader):
    leader.queue(tmp_path / "archive", {"talks/one.wav": b"one"})
    engine = FakeEngine()
    reached, release = threading.Event(), threading.Event()

    def hold(fraction):
        if fraction == 0.5:
            reached.set()
            assert release.wait(30)

    engine.on_step = hold
    served = Served(
        follower(tmp_path, leader, engine, leader.join_token(), shutdown_grace_seconds=0)
    )
    assert reached.wait(30)
    served.agent.stop()
    release.set()
    assert served.code() == 0
    (job,) = leader.rows(Job)
    (attempt,) = leader.rows(JobAttempt)
    assert (job.state, job.attempts, attempt.outcome) == ("queued", 0, "released")


def test_a_cancelled_job_is_abandoned_and_its_uploads_are_refused(tmp_path, leader):
    leader.queue(tmp_path / "archive", {"talks/one.wav": b"one"})
    engine = FakeEngine()
    reached, release = threading.Event(), threading.Event()

    def hold(fraction):
        if fraction == 0.5:
            reached.set()
            assert release.wait(30)

    engine.on_step = hold
    served = Served(follower(tmp_path, leader, engine, leader.join_token()))
    assert reached.wait(30)
    (job,) = leader.rows(Job)

    async def cancel(sessionmaker):
        async with sessionmaker() as session:
            await job_admin.cancel_job(session, job.id, now=utcnow(), actor="test")
            await session.commit()

    leader.on_database(cancel)
    until(lambda: served.agent._control.reason == "cancelled", "the cancel directive")
    release.set()
    until(lambda: served.agent._control is None, "the job to end")
    served.agent.stop()
    assert served.code() == 0
    assert states(leader) == ["cancelled"]
    assert not (tmp_path / "archive" / "transcripts").exists()


def test_the_real_routes_answer_as_the_client_expects(tmp_path, leader):
    """Each call the follower makes, once, against the real leader: statuses, bodies and
    headers the fake leader copies."""
    leader.queue(tmp_path / "archive", {"talks/one.wav": b"one"})
    agent = follower(tmp_path, leader, FakeEngine(), leader.join_token())
    agent.prepare()
    client, links = agent._client, Links()
    claim = client.claim()
    assert client.heartbeat(claim.job_id, claim.lease_id, 0.5) == "continue"
    assert client.claim() == NoWork(retry_after=1.0, draining=False)
    fresh = client.links(claim.job_id, claim.lease_id)
    source = tmp_path / "source"
    links.download(fresh.download_url, source, lambda: None)
    assert source.read_bytes() == b"one"
    output = tmp_path / "out.txt"
    output.write_bytes(b"text\n")
    links.upload(fresh.upload_urls.txt, output)
    with pytest.raises(Refused) as refused:
        client.heartbeat(claim.job_id, str(uuid.uuid4()), None)
    assert (refused.value.status, refused.value.code) == (409, "stale_lease")
    with pytest.raises(Refused) as missing:
        client.submit(claim.job_id, claim.lease_id, _checksums("a" * 64))
    assert (missing.value.status, missing.value.code) == (409, "outputs_missing")
    client.release(claim.job_id, claim.lease_id)
    with pytest.raises(LeaseLost):
        links.upload(fresh.upload_urls.txt, output)  # the lease ended with the release
    again = client.claim()
    client.fail(again.job_id, again.lease_id, "undecodable", "not audio", False)
    assert states(leader) == ["failed"]
    client.deregister()
    stranger = LeaderClient(leader.url, credential="x" * 43)
    with pytest.raises(Refused) as unknown:
        stranger.claim()
    assert unknown.value.status == 401


def _checksums(value: str):
    from swarmscribe_protocol import OutputChecksums

    return OutputChecksums(source=value, txt=value, srt=value, segments_json=value)


def test_pods_that_come_and_go_with_a_pool_token_reuse_one_follower_row(tmp_path, leader):
    async def pool_token(sessionmaker):
        async with sessionmaker() as session:
            _, plaintext = await create_pool_token(
                session, name="cpu-pods", pool="default", actor="test"
            )
            await session.commit()
        return plaintext

    token = leader.on_database(pool_token)
    engine = FakeEngine()
    for pod in range(3):  # each pod has a new, empty state folder
        served = Served(follower(tmp_path, leader, engine, token, name=f"pod-{pod}"))
        until(lambda: any(row.state == "active" for row in leader.rows(Follower)), "the pod")
        served.agent.stop()
        assert served.code() == 0
    (row,) = leader.rows(Follower)
    assert row.state == "gone"
    registers = [e for e in leader.rows(AuditEntry) if e.action == "follower.register"]
    assert [entry.detail["reused"] for entry in registers] == [False, True, True]
```

The leader's `public_url` is the address it is served on: its own file links are built from it, and the follower must be able to reach them (F0 ruling 10).

- [ ] **Step 2: Run them**

Run: `uv run pytest packages/follower/tests/test_real_leader.py -q`
Expected: PASS (8 tests), in under a minute. They use the database `swarmscribe_kit_follower`, on the same Postgres as the leader's tests (`SWARMSCRIBE_TEST_DATABASE_URL`, or the local `.pgdata` server).

If one fails, the follower and the leader disagree about the contract. Do not change `FakeLeader` to match the follower: find out which side the specs support (follower spec 5.1 and 12; leader spec 6), fix that side, and make `FakeLeader` behave as the real leader does.

- [ ] **Step 3: Run everything**

Run: `uv run ruff check . && uv run pytest -q`
Expected: `All checks passed!`; every package's tests PASS.

- [ ] **Step 4: Commit**

```bash
git add packages/follower/tests/test_real_leader.py
git commit -m "Follower contract test: the agent against the real leader, with a stub engine"
```

---

### Task 12: Try it by hand, and the README

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: the `swarmscribe-follower` command (Task 10); `swarmscribe-leader`, `swarmscribe-admin` (exist).
- Produces: documentation only.

- [ ] **Step 1: Run a real follower against a real leader on this machine**

This is the plan's own acceptance: the follower spec's F1 result is "`swarmscribe-follower run` on a development machine, against a leader on the same machine, transcribes a local folder end to end on the CPU". It uses a real model (the CPU default, `distil-large-v3`, about 1.5 GB, downloaded on first use), so it is a manual step, not a test.

With a leader running as the README's "Run the leader (development)" describes (`SWARMSCRIBE_PUBLIC_URL=http://localhost:8080`), signed in with `swarmscribe-admin login`:

```
uv run swarmscribe-admin locations add try-it --root <an absolute folder>
```

Put a short recording and a `consent.txt` containing `**/*` in that folder, then:

```
uv run swarmscribe-admin ingest try-it
uv run swarmscribe-admin tokens create --pool default
```

and in another terminal, with `SWARMSCRIBE_LEADER_URL=http://localhost:8080`, `SWARMSCRIBE_JOIN_TOKEN=<the token>`, `SWARMSCRIBE_FOLLOWER_DEVICE=cpu` and `SWARMSCRIBE_FOLLOWER_LOG_FORMAT=text`:

```
uv run swarmscribe-follower doctor
uv run swarmscribe-follower run
```

Expected: `doctor` prints `device: cpu`, `model: distil-large-v3 (int8) loaded and ran` (after the download, the first time), `leader: answers` and `joined: no`. `run` logs `registered`, `job claimed`, `job completed`; `<folder>/transcripts/<name>.txt`, `.srt` and `.segments.json` appear; `uv run swarmscribe-admin jobs list` shows the job `completed`. Press Ctrl+C: the follower exits 0 and `uv run swarmscribe-admin followers list` shows it `gone`.

Then try the stop that cannot wait: start `run` again, queue another recording (`ingest` after adding a file), and press Ctrl+C while it is being transcribed. Expected: the follower logs `job released (shutdown)` and exits 0 within a few seconds, and `jobs list` shows the job `queued` with `attempts` 0.

Write what happened (the commands' real output, the time the transcription took) into the pull request's description. If a step does not behave as described, that is a finding to fix before merging, not a note.

- [ ] **Step 2: Document it**

In `README.md`:

Replace

````markdown
| `swarmscribe-follower` | Not started |
````

with

````markdown
| `swarmscribe-follower` — the agent: join, claim, transcribe, upload | Agent built; images, Helm chart and service install next |
````

Replace

````markdown
## Run the fleet console (development)
````

with

````markdown
## Run a follower (development)

A follower takes recordings from a leader, transcribes them and uploads the
three outputs. It needs the leader's URL and, the first time, a join token (or
a pool token):

```
uv run swarmscribe-admin tokens create --pool default
export SWARMSCRIBE_LEADER_URL=http://localhost:8080
export SWARMSCRIBE_JOIN_TOKEN=<the token>
uv run swarmscribe-follower doctor
uv run swarmscribe-follower run
```

`doctor` checks the device, loads the model and runs it once, and asks the
leader's `/healthz`; it registers nothing. `run` does the same checks, joins if
it has no credential, then claims and transcribes until it is stopped. `join`
only registers (`--token-stdin` reads the token from standard input); `leave`
deregisters and deletes the credential.

| Variable | Default | Meaning |
|---|---|---|
| `SWARMSCRIBE_LEADER_URL` | required | the leader; `https`, or `http` for a loopback address |
| `SWARMSCRIBE_JOIN_TOKEN`, `SWARMSCRIBE_JOIN_TOKEN_FILE` | — | a join or pool token, read only when there is no stored credential |
| `SWARMSCRIBE_LEADER_CA_FILE` | — | PEM certificates trusted in addition to the public roots |
| `SWARMSCRIBE_FOLLOWER_DEVICE` | `auto` | `auto`, `cuda` or `cpu` |
| `SWARMSCRIBE_FOLLOWER_POOL` | `default` | the pool name it reports; the token decides the real pool |
| `SWARMSCRIBE_FOLLOWER_STATE_DIR` | the user's data folder | credential and lock file |
| `SWARMSCRIBE_FOLLOWER_SCRATCH_DIR` | `<state>/scratch` | working files; **everything in it is deleted** |
| `SWARMSCRIBE_FOLLOWER_MODEL_DIR` | Hugging Face's default | model cache |
| `SWARMSCRIBE_FOLLOWER_OFFLINE` | `0` | `1`: never download a model |
| `SWARMSCRIBE_FOLLOWER_ALLOWED_MODELS` | — | comma-separated; only these are loaded |
| `SWARMSCRIBE_FOLLOWER_SHUTDOWN_GRACE_SECONDS` | `8` | how long a stop may wait for the current job |
| `SWARMSCRIBE_FOLLOWER_ON_DRAINED` | `exit` | `exit`, or `park` under a supervisor that restarts whatever exits |
| `SWARMSCRIBE_FOLLOWER_LOG_FORMAT` | `json` | `json` or `text` |

The follower refuses a scratch folder that holds files it did not put there.
The leader's `SWARMSCRIBE_PUBLIC_URL` must be an address the follower can
reach: the leader's own file links are built from it.

How it ends:

| Exit code | Meaning | Restart it? |
|---|---|---|
| `0` | stopped, or drained with nothing left to do | no |
| `2` | invalid settings, a state folder another follower holds, a scratch folder that is not its own | after fixing it |
| `3` | this machine cannot do the work: device, GPU libraries, model (`doctor` says which) | after fixing it |
| `4` | no or invalid token, or the follower was revoked | no: it needs a new token |
| `5` | the leader speaks another protocol version | no: upgrade |

A stop (Ctrl+C, `SIGTERM`) finishes the current job only if its estimated time
left fits the grace period; otherwise the job is released, without counting an
attempt, and another follower redoes it. A second stop releases at once. A
drained follower exits `0` and keeps its credential, so starting it again finds
it still drained; `swarmscribe-follower leave`, then joining again, puts the
machine back to work. A revoked follower exits `4` and never registers again by
itself.

Logs are one JSON object per line on stderr, with `job_id` and `lease_id`. They
never hold audio, transcript text, links, tokens or credentials.

## Run the fleet console (development)
````

- [ ] **Step 3: Run everything once more**

Run: `uv run ruff check . && uv run pytest -q`
Expected: `All checks passed!`; PASS.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "README: run a follower"
```

---

## Self-review

- **Spec coverage.** Follower spec 4 and 4.1 → Task 1 (settings, exit codes), Task 10 (the lock, the commands). 5.1 → Tasks 4, 5, 11. 5.2 → Task 9 (`prepare`), Task 10. 5.3 → Tasks 2, 9. 5.4 → Task 8. 5.5 → Tasks 7, 9 (drain: exit and park). 5.6 → Task 9 (`stop`), Task 8 (`remaining`). 5.7 (model lifecycle) → Task 6; its memory guard is F2 (ruling 9). 5.8 → Task 3. 5.9 → Task 6. 5.10 (cache folder, offline) → Task 10 (`configure_environment`), Task 8 (a model missing offline is `unfit`). 6.1 to 6.6 → Task 8 (one test per row), Task 9 (6.5). 7 → Tasks 1, 4, 5, 8 (the leak tests), Task 6 (model names). 9 (logs) → Task 1; metrics and health are F2. 10 (unit tests with a fake leader; contract against the real leader) → Tasks 1 to 10; Task 11.
- **Not in this plan, on purpose.** Images, the Compose test with a real model, the health listener, metrics, the memory guard, the CUDA proof (F2). The chart (F3). The systemd unit, the Windows service, `setup-cuda` (F4).
- **Names used across tasks.** `Transient`, `Refused`, `Interrupted`, `retrying`, `NoWork` (Task 4 → 5, 7, 8, 9). `Links`, `SourceChanged`, `LinkExpired`, `LeaseLost`, `OutputTooLarge`, `OutOfSpace` (Task 5 → 8). `ModelHost.get/close/loaded`, `ModelUnavailable`, `OutOfMemory` (Task 6 → 8, 9, 10). `JobControl.stop/check/pause`, the reasons, `LeaseKeeper` (Task 7 → 8, 9). `JobRunner.run/remaining`, `JobResult`, the outcomes (Task 8 → 9). `Agent.prepare/register/serve/stop` (Task 9 → 10, 11). `Stored`, `CredentialStore` (Task 2 → 9, 10). `Scratch` (Task 3 → 8, 9, 10).
