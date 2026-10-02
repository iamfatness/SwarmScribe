# Protocol + Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the two foundation packages of SwarmScribe: the shared wire protocol and the single-file transcription engine, usable on its own as a CLI.

**Architecture:** A `uv` workspace with two independent Python packages. `swarmscribe-protocol` holds Pydantic models for every leader–follower message and the `segments.json` schema, pinned by a schema snapshot. `swarmscribe-engine` wraps faster-whisper with the fixed transcription settings, resolves the device, and writes `txt`, `srt` and `segments.json`; it does not import the protocol package at runtime, and a test proves its JSON output conforms to the protocol schema.

**Tech Stack:** Python 3.11+, uv workspace, Pydantic v2, faster-whisper (CTranslate2, PyAV), pytest, ruff, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-02-swarmscribe-architecture-design.md` (sections 4, 5.1, 5.2, 6, 14, 15 step 1)

## Global Constraints

- Python `>=3.11`.
- Dependency rule: `protocol` depends on nothing internal. `engine` depends on nothing internal at runtime. (Engine *tests* may import `swarmscribe_protocol`.)
- The engine has no networking and no knowledge of jobs or leases.
- Fixed transcription behaviour, not configurable: `language="en"`, `condition_on_previous_text=False`, VAD filter on, word timestamps on.
- Temperature ladder defaults to `(0.0, 0.2, 0.4)`; no value may exceed `0.4`.
- Glossary is passed as the initial prompt.
- Auto device resolution: CUDA GPU → `large-v3` / `float16`; CPU → `distil-large-v3` / `int8`.
- No system ffmpeg; audio is decoded by the PyAV bundled with faster-whisper.
- Output names: `<source file name>.txt`, `<source file name>.srt`, `<source file name>.segments.json`.
- All text files are written as UTF-8 with `\n` line endings, on every OS.
- `segments.json` is written last; its presence means the file is done.
- Protocol version is the integer `1`. All API paths in later steps live under `/v1`.
- No audio or transcript text in logs.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Commands are shown as `uv …`. If `uv` is not on PATH after Task 1 Step 1, use `python -m uv …`.

## Review Focus

Inputs the spec implies but does not spell out, most likely first. Each has a test in the task named.

1. **A recording with no speech** (silence, music, a tone): all three outputs are still written, `txt` and `srt` empty, `segments` an empty list. — Task 5 (writers), Task 6 (transcriber), Task 7 (smoke).
2. **A corrupt or non-audio file**: raises `UndecodableAudioError` with the file name, not a raw library traceback, so the follower can report a non-retryable failure. — Task 6, Task 7 (CLI exit code, smoke against the real library).
3. **Non-ASCII text and names** (`José`, curly quotes) on Windows: outputs are UTF-8, never the system code page. — Task 5.
4. **Long recordings**: SRT timestamps past one hour, and millisecond rounding that carries (`59.9996 s` → `00:01:00,000`). — Task 5.
5. **Model output that is slightly off**: a word probability of `1.0000001`, a segment with `words=None`, a whitespace-only segment. Probability is clamped to `[0, 1]`, missing words become an empty list, empty segments are dropped. — Task 6.

## File Structure

```
pyproject.toml                         workspace root, dev tools, pytest + ruff config
.gitignore
.github/workflows/ci.yml               ruff + pytest on push/PR
README.md                              what this is, how to run the engine CLI
packages/protocol/
  pyproject.toml
  src/swarmscribe_protocol/
    __init__.py                        PROTOCOL_VERSION, re-exports
    base.py                            WireModel base class
    segments.py                        JobSettings, Word, Segment, SegmentsDocument
    messages.py                        register / claim / heartbeat / submit / fail / release models
    schema.py                          export_schema(), `python -m swarmscribe_protocol.schema <path>`
  tests/
    test_segments.py
    test_messages.py
    test_schema_snapshot.py
    schema_v1.json                     committed snapshot
packages/engine/
  pyproject.toml
  src/swarmscribe_engine/
    __init__.py                        re-exports
    version.py                         ENGINE_VERSION
    types.py                           TranscribeSettings, Word, Segment, Transcript, OutputFiles, DeviceChoice, errors
    device.py                          resolve_device
    writers.py                         render_txt, render_srt, render_segments_json, write_outputs
    transcriber.py                     Transcriber, transcribe, build_prompt, sha256_file
    cli.py                             swarmscribe-engine command
  tests/
    conftest.py                        make_transcript fixture
    test_device.py
    test_writers.py
    test_transcriber.py
    test_cli.py
    test_smoke.py                      opt-in, real tiny.en model
```

---

### Task 1: Workspace scaffold and protocol version

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.github/workflows/ci.yml`
- Create: `packages/protocol/pyproject.toml`
- Create: `packages/protocol/src/swarmscribe_protocol/__init__.py`
- Create: `packages/protocol/src/swarmscribe_protocol/base.py`
- Test: `packages/protocol/tests/test_version.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `swarmscribe_protocol.PROTOCOL_VERSION: int` (value `1`); `swarmscribe_protocol.base.WireModel` (Pydantic base class, frozen). A working `uv run pytest` and `uv run ruff check .` at the repo root.

- [ ] **Step 1: Install uv**

Run: `python -m pip install --user uv`
Then: `uv --version` (or `python -m uv --version`)
Expected: prints a version number.

- [ ] **Step 2: Write the root `pyproject.toml`**

```toml
[project]
name = "swarmscribe"
version = "0.1.0"
description = "Distributed leader/follower transcription"
requires-python = ">=3.11"
dependencies = []

[tool.uv.workspace]
members = ["packages/*"]

[tool.uv.sources]
swarmscribe-protocol = { workspace = true }

[dependency-groups]
dev = [
    "pytest>=8",
    "ruff>=0.6",
    "swarmscribe-protocol",
]

[tool.pytest.ini_options]
testpaths = ["packages"]
addopts = "--import-mode=importlib -m 'not smoke'"
markers = [
    "smoke: downloads and runs a real Whisper model; run with: uv run pytest -m smoke",
]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]
```

- [ ] **Step 3: Write `.gitignore`**

```gitignore
.venv/
__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/
dist/
*.egg-info/
*.tmp
```

- [ ] **Step 4: Write `packages/protocol/pyproject.toml`**

```toml
[project]
name = "swarmscribe-protocol"
version = "0.1.0"
description = "SwarmScribe leader-follower wire models"
requires-python = ">=3.11"
dependencies = ["pydantic>=2.7,<3"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/swarmscribe_protocol"]
```

- [ ] **Step 5: Write the failing test**

`packages/protocol/tests/test_version.py`:

```python
import pytest
from pydantic import ValidationError

import swarmscribe_protocol
from swarmscribe_protocol.base import WireModel


def test_protocol_version_is_one():
    assert swarmscribe_protocol.PROTOCOL_VERSION == 1


def test_wire_models_are_immutable():
    class Example(WireModel):
        name: str

    example = Example(name="a")
    with pytest.raises(ValidationError):
        example.name = "b"
```

- [ ] **Step 6: Run the test to verify it fails**

Run: `uv sync` then `uv run pytest packages/protocol -v`
Expected: `uv sync` fails or pytest errors with `ModuleNotFoundError: No module named 'swarmscribe_protocol'` (the package has no source yet).

- [ ] **Step 7: Write the implementation**

`packages/protocol/src/swarmscribe_protocol/base.py`:

```python
from pydantic import BaseModel, ConfigDict


class WireModel(BaseModel):
    """Base for everything that crosses the leader-follower wire."""

    model_config = ConfigDict(frozen=True, protected_namespaces=())
```

`packages/protocol/src/swarmscribe_protocol/__init__.py`:

```python
PROTOCOL_VERSION = 1

__all__ = ["PROTOCOL_VERSION"]
```

- [ ] **Step 8: Run the tests and the linter**

Run: `uv sync` then `uv run pytest packages/protocol -v` then `uv run ruff check .`
Expected: 2 passed; ruff reports `All checks passed!`

- [ ] **Step 9: Write the CI workflow**

`.github/workflows/ci.yml`:

```yaml
name: ci

on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync
      - run: uv run ruff check .
      - run: uv run pytest
```

- [ ] **Step 10: Commit**

```bash
git add pyproject.toml uv.lock .gitignore .github packages/protocol
git commit -m "Scaffold uv workspace and protocol package"
```

---

### Task 2: Protocol — job settings and the segments.json schema

**Files:**
- Create: `packages/protocol/src/swarmscribe_protocol/segments.py`
- Modify: `packages/protocol/src/swarmscribe_protocol/__init__.py`
- Test: `packages/protocol/tests/test_segments.py`

**Interfaces:**
- Consumes: `WireModel` from `swarmscribe_protocol.base`.
- Produces (all importable from `swarmscribe_protocol`):
  - `Device = Literal["cuda", "cpu"]`
  - `MAX_TEMPERATURE = 0.4`
  - `JobSettings(model: str, compute_type: str, language: Literal["en"] = "en", condition_on_previous_text: Literal[False] = False, temperatures: tuple[float, ...] = (0.0, 0.2, 0.4), vad_filter: Literal[True] = True, word_timestamps: Literal[True] = True)`
  - `Word(start: float, end: float, word: str, probability: float)` — probability in `[0, 1]`
  - `Segment(start: float, end: float, text: str, words: list[Word])`
  - `SegmentsDocument(schema_version: Literal[1], source_checksum: str, duration: float, device: Device, engine_version: str, settings: JobSettings, glossary: list[str], segments: list[Segment])`

- [ ] **Step 1: Write the failing tests**

`packages/protocol/tests/test_segments.py`:

```python
import pytest
from pydantic import ValidationError

from swarmscribe_protocol import JobSettings, Segment, SegmentsDocument, Word


def _settings(**overrides):
    return JobSettings(**{"model": "large-v3", "compute_type": "float16", **overrides})


def test_job_settings_defaults_are_the_fixed_behaviour():
    settings = _settings()
    assert settings.language == "en"
    assert settings.condition_on_previous_text is False
    assert settings.temperatures == (0.0, 0.2, 0.4)
    assert settings.vad_filter is True
    assert settings.word_timestamps is True


@pytest.mark.parametrize(
    "override",
    [
        {"language": "fr"},
        {"condition_on_previous_text": True},
        {"vad_filter": False},
        {"word_timestamps": False},
    ],
)
def test_job_settings_reject_changes_to_fixed_behaviour(override):
    with pytest.raises(ValidationError):
        _settings(**override)


@pytest.mark.parametrize("temperatures", [(), (0.0, 0.6), (-0.1,), (0.0, 1.0)])
def test_job_settings_reject_unclamped_temperature_ladder(temperatures):
    with pytest.raises(ValidationError):
        _settings(temperatures=temperatures)


def test_word_probability_must_be_between_zero_and_one():
    Word(start=0.0, end=0.5, word=" hello", probability=1.0)
    with pytest.raises(ValidationError):
        Word(start=0.0, end=0.5, word=" hello", probability=1.2)


def test_segments_document_round_trips_through_json():
    document = SegmentsDocument(
        schema_version=1,
        source_checksum="a" * 64,
        duration=12.5,
        device="cuda",
        engine_version="0.1.0",
        settings=_settings(),
        glossary=["Shiloh", "José"],
        segments=[
            Segment(
                start=0.0,
                end=1.5,
                text="Welcome to Shiloh.",
                words=[Word(start=0.0, end=0.4, word=" Welcome", probability=0.98)],
            )
        ],
    )
    assert SegmentsDocument.model_validate_json(document.model_dump_json()) == document


def test_segments_document_accepts_no_speech():
    document = SegmentsDocument(
        schema_version=1,
        source_checksum="a" * 64,
        duration=3.0,
        device="cpu",
        engine_version="0.1.0",
        settings=_settings(model="distil-large-v3", compute_type="int8"),
        glossary=[],
        segments=[],
    )
    assert document.segments == []


def test_segments_document_rejects_unknown_schema_version():
    with pytest.raises(ValidationError):
        SegmentsDocument(
            schema_version=2,
            source_checksum="a" * 64,
            duration=3.0,
            device="cpu",
            engine_version="0.1.0",
            settings=_settings(),
            glossary=[],
            segments=[],
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/protocol/tests/test_segments.py -v`
Expected: FAIL with `ImportError: cannot import name 'JobSettings' from 'swarmscribe_protocol'`

- [ ] **Step 3: Write the implementation**

`packages/protocol/src/swarmscribe_protocol/segments.py`:

```python
from typing import Literal

from pydantic import Field, field_validator

from .base import WireModel

Device = Literal["cuda", "cpu"]

MAX_TEMPERATURE = 0.4


class JobSettings(WireModel):
    """How a follower must transcribe. The Literal fields are fixed behaviour."""

    model: str
    compute_type: str
    language: Literal["en"] = "en"
    condition_on_previous_text: Literal[False] = False
    temperatures: tuple[float, ...] = (0.0, 0.2, 0.4)
    vad_filter: Literal[True] = True
    word_timestamps: Literal[True] = True

    @field_validator("temperatures")
    @classmethod
    def _ladder_is_clamped(cls, value: tuple[float, ...]) -> tuple[float, ...]:
        if not value:
            raise ValueError("temperatures must not be empty")
        if any(t < 0.0 or t > MAX_TEMPERATURE for t in value):
            raise ValueError(f"temperatures must be between 0.0 and {MAX_TEMPERATURE}")
        return value


class Word(WireModel):
    start: float
    end: float
    word: str
    probability: float = Field(ge=0.0, le=1.0)


class Segment(WireModel):
    start: float
    end: float
    text: str
    words: list[Word]


class SegmentsDocument(WireModel):
    """The contents of <name>.segments.json."""

    schema_version: Literal[1]
    source_checksum: str
    duration: float = Field(ge=0.0)
    device: Device
    engine_version: str
    settings: JobSettings
    glossary: list[str]
    segments: list[Segment]
```

Replace `packages/protocol/src/swarmscribe_protocol/__init__.py` with:

```python
from .segments import MAX_TEMPERATURE, Device, JobSettings, Segment, SegmentsDocument, Word

PROTOCOL_VERSION = 1

__all__ = [
    "MAX_TEMPERATURE",
    "PROTOCOL_VERSION",
    "Device",
    "JobSettings",
    "Segment",
    "SegmentsDocument",
    "Word",
]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest packages/protocol -v` then `uv run ruff check .`
Expected: all pass; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add packages/protocol
git commit -m "Add job settings and segments.json schema to protocol"
```

---

### Task 3: Protocol — leader–follower messages and schema snapshot

**Files:**
- Create: `packages/protocol/src/swarmscribe_protocol/messages.py`
- Create: `packages/protocol/src/swarmscribe_protocol/schema.py`
- Create: `packages/protocol/tests/schema_v1.json` (generated in Step 6)
- Modify: `packages/protocol/src/swarmscribe_protocol/__init__.py`
- Test: `packages/protocol/tests/test_messages.py`, `packages/protocol/tests/test_schema_snapshot.py`

**Interfaces:**
- Consumes: `WireModel`, `Device`, `JobSettings`, `SegmentsDocument`.
- Produces (all importable from `swarmscribe_protocol`):
  - `Directive = Literal["continue", "cancel", "drain"]`
  - `Link(url: str, method: Literal["GET", "PUT"], headers: dict[str, str] = {})`
  - `Capabilities(device: Device, gpu_name: str | None = None, gpu_memory_mb: int | None = None, models: list[str], engine_version: str, pool: str)`
  - `RegisterRequest(join_token: str, protocol_version: int, capabilities: Capabilities)`
  - `RegisterResponse(follower_id: str, credential: str, heartbeat_interval: int, lease_seconds: int)` — both in seconds, both `> 0`
  - `UploadUrls(txt: Link, srt: Link, segments_json: Link)`
  - `ClaimResponse(job_id: str, lease_id: str, download_url: Link, upload_urls: UploadUrls, settings: JobSettings, glossary: list[str], source_checksum: str)`
  - `HeartbeatRequest(lease_id: str, progress: float | None = None)` — progress in `[0, 1]`
  - `HeartbeatResponse(directive: Directive)`
  - `OutputChecksums(txt: str, srt: str, segments_json: str)`
  - `SubmitRequest(lease_id: str, checksums: OutputChecksums)`
  - `SubmitResponse(accepted: bool)`
  - `FailRequest(lease_id: str, reason: str, retryable: bool)`
  - `ReleaseRequest(lease_id: str)`
  - `swarmscribe_protocol.schema.export_schema() -> str` — deterministic JSON of every model's JSON Schema.
- A claim with no work is HTTP `204` with a `Retry-After` header; it has no body and no model.

- [ ] **Step 1: Write the failing message tests**

`packages/protocol/tests/test_messages.py`:

```python
import pytest
from pydantic import ValidationError

from swarmscribe_protocol import (
    Capabilities,
    ClaimResponse,
    FailRequest,
    HeartbeatRequest,
    HeartbeatResponse,
    JobSettings,
    Link,
    OutputChecksums,
    RegisterRequest,
    RegisterResponse,
    ReleaseRequest,
    SubmitRequest,
    SubmitResponse,
    UploadUrls,
)


def _link(method="GET"):
    return Link(url="https://storage.example/object?sig=abc", method=method)


def _claim():
    return ClaimResponse(
        job_id="job-1",
        lease_id="lease-1",
        download_url=_link(),
        upload_urls=UploadUrls(txt=_link("PUT"), srt=_link("PUT"), segments_json=_link("PUT")),
        settings=JobSettings(model="large-v3", compute_type="float16"),
        glossary=["Shiloh"],
        source_checksum="a" * 64,
    )


@pytest.mark.parametrize(
    "message",
    [
        RegisterRequest(
            join_token="token",
            protocol_version=1,
            capabilities=Capabilities(
                device="cuda",
                gpu_name="RTX 4090",
                gpu_memory_mb=24564,
                models=["large-v3"],
                engine_version="0.1.0",
                pool="gpu",
            ),
        ),
        RegisterResponse(
            follower_id="f-1", credential="secret", heartbeat_interval=30, lease_seconds=120
        ),
        _claim(),
        HeartbeatRequest(lease_id="lease-1", progress=0.5),
        HeartbeatResponse(directive="drain"),
        SubmitRequest(
            lease_id="lease-1",
            checksums=OutputChecksums(txt="a" * 64, srt="b" * 64, segments_json="c" * 64),
        ),
        SubmitResponse(accepted=True),
        FailRequest(lease_id="lease-1", reason="undecodable audio", retryable=False),
        ReleaseRequest(lease_id="lease-1"),
    ],
    ids=lambda m: type(m).__name__,
)
def test_every_message_round_trips_through_json(message):
    assert type(message).model_validate_json(message.model_dump_json()) == message


def test_cpu_follower_capabilities_need_no_gpu_fields():
    capabilities = Capabilities(
        device="cpu", models=["distil-large-v3"], engine_version="0.1.0", pool="cpu"
    )
    assert capabilities.gpu_name is None
    assert capabilities.gpu_memory_mb is None


def test_link_headers_default_to_empty():
    assert _link().headers == {}


def test_link_rejects_methods_other_than_get_and_put():
    with pytest.raises(ValidationError):
        Link(url="https://storage.example/object", method="DELETE")


def test_heartbeat_directive_must_be_known():
    with pytest.raises(ValidationError):
        HeartbeatResponse(directive="explode")


@pytest.mark.parametrize("progress", [-0.1, 1.1])
def test_heartbeat_progress_must_be_a_fraction(progress):
    with pytest.raises(ValidationError):
        HeartbeatRequest(lease_id="lease-1", progress=progress)


def test_heartbeat_progress_is_optional():
    assert HeartbeatRequest(lease_id="lease-1").progress is None


@pytest.mark.parametrize("field", ["heartbeat_interval", "lease_seconds"])
def test_register_response_timings_must_be_positive(field):
    values = {
        "follower_id": "f-1",
        "credential": "secret",
        "heartbeat_interval": 30,
        "lease_seconds": 120,
        field: 0,
    }
    with pytest.raises(ValidationError):
        RegisterResponse(**values)


def test_claim_carries_the_fixed_settings():
    assert _claim().settings.condition_on_previous_text is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/protocol/tests/test_messages.py -v`
Expected: FAIL with `ImportError: cannot import name 'Capabilities' from 'swarmscribe_protocol'`

- [ ] **Step 3: Write the messages**

`packages/protocol/src/swarmscribe_protocol/messages.py`:

```python
from typing import Literal

from pydantic import Field

from .base import WireModel
from .segments import Device, JobSettings

Directive = Literal["continue", "cancel", "drain"]


class Link(WireModel):
    """A short-lived, single-object URL. Identical for every storage backend."""

    url: str
    method: Literal["GET", "PUT"]
    headers: dict[str, str] = Field(default_factory=dict)


class Capabilities(WireModel):
    device: Device
    gpu_name: str | None = None
    gpu_memory_mb: int | None = None
    models: list[str]
    engine_version: str
    pool: str


class RegisterRequest(WireModel):
    join_token: str
    protocol_version: int
    capabilities: Capabilities


class RegisterResponse(WireModel):
    follower_id: str
    credential: str
    heartbeat_interval: int = Field(gt=0)
    lease_seconds: int = Field(gt=0)


class UploadUrls(WireModel):
    txt: Link
    srt: Link
    segments_json: Link


class ClaimResponse(WireModel):
    job_id: str
    lease_id: str
    download_url: Link
    upload_urls: UploadUrls
    settings: JobSettings
    glossary: list[str]
    source_checksum: str


class HeartbeatRequest(WireModel):
    lease_id: str
    progress: float | None = Field(default=None, ge=0.0, le=1.0)


class HeartbeatResponse(WireModel):
    directive: Directive


class OutputChecksums(WireModel):
    txt: str
    srt: str
    segments_json: str


class SubmitRequest(WireModel):
    lease_id: str
    checksums: OutputChecksums


class SubmitResponse(WireModel):
    accepted: bool


class FailRequest(WireModel):
    lease_id: str
    reason: str
    retryable: bool


class ReleaseRequest(WireModel):
    lease_id: str
```

Replace `packages/protocol/src/swarmscribe_protocol/__init__.py` with:

```python
from .messages import (
    Capabilities,
    ClaimResponse,
    Directive,
    FailRequest,
    HeartbeatRequest,
    HeartbeatResponse,
    Link,
    OutputChecksums,
    RegisterRequest,
    RegisterResponse,
    ReleaseRequest,
    SubmitRequest,
    SubmitResponse,
    UploadUrls,
)
from .segments import MAX_TEMPERATURE, Device, JobSettings, Segment, SegmentsDocument, Word

PROTOCOL_VERSION = 1

__all__ = [
    "MAX_TEMPERATURE",
    "PROTOCOL_VERSION",
    "Capabilities",
    "ClaimResponse",
    "Device",
    "Directive",
    "FailRequest",
    "HeartbeatRequest",
    "HeartbeatResponse",
    "JobSettings",
    "Link",
    "OutputChecksums",
    "RegisterRequest",
    "RegisterResponse",
    "ReleaseRequest",
    "Segment",
    "SegmentsDocument",
    "SubmitRequest",
    "SubmitResponse",
    "UploadUrls",
    "Word",
]
```

- [ ] **Step 4: Run the message tests to verify they pass**

Run: `uv run pytest packages/protocol/tests/test_messages.py -v`
Expected: all pass.

- [ ] **Step 5: Write the failing snapshot test**

`packages/protocol/tests/test_schema_snapshot.py`:

```python
from pathlib import Path

from swarmscribe_protocol.schema import export_schema

SNAPSHOT = Path(__file__).parent / "schema_v1.json"


def test_wire_schema_matches_the_committed_snapshot():
    assert export_schema() == SNAPSHOT.read_text(encoding="utf-8"), (
        "The wire format changed. If this is intended, decide whether PROTOCOL_VERSION "
        "must be bumped, then regenerate with: "
        "uv run python -m swarmscribe_protocol.schema packages/protocol/tests/schema_v1.json"
    )


def test_export_is_deterministic():
    assert export_schema() == export_schema()
```

Run: `uv run pytest packages/protocol/tests/test_schema_snapshot.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'swarmscribe_protocol.schema'`

- [ ] **Step 6: Write the exporter and generate the snapshot**

`packages/protocol/src/swarmscribe_protocol/schema.py`:

```python
"""Export the JSON Schema of every wire model, for the snapshot test."""

import json
import sys
from pathlib import Path

from . import messages, segments
from .base import WireModel

MODELS: tuple[type[WireModel], ...] = (
    segments.JobSettings,
    segments.Word,
    segments.Segment,
    segments.SegmentsDocument,
    messages.Link,
    messages.Capabilities,
    messages.RegisterRequest,
    messages.RegisterResponse,
    messages.UploadUrls,
    messages.ClaimResponse,
    messages.HeartbeatRequest,
    messages.HeartbeatResponse,
    messages.OutputChecksums,
    messages.SubmitRequest,
    messages.SubmitResponse,
    messages.FailRequest,
    messages.ReleaseRequest,
)


def export_schema() -> str:
    schemas = {model.__name__: model.model_json_schema() for model in MODELS}
    return json.dumps(schemas, indent=2, sort_keys=True) + "\n"


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m swarmscribe_protocol.schema <output-path>")
    Path(sys.argv[1]).write_text(export_schema(), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
```

Run: `uv run python -m swarmscribe_protocol.schema packages/protocol/tests/schema_v1.json`
Expected: creates `schema_v1.json`. Open it and confirm it contains a `"ClaimResponse"` key and a `"SegmentsDocument"` key.

Add a `.gitattributes` at the repo root so Git never rewrites the snapshot's line endings:

```gitattributes
*.json text eol=lf
```

- [ ] **Step 7: Run all protocol tests**

Run: `uv run pytest packages/protocol -v` then `uv run ruff check .`
Expected: all pass; ruff clean.

- [ ] **Step 8: Commit**

```bash
git add .gitattributes packages/protocol
git commit -m "Add leader-follower messages and wire schema snapshot"
```

---

### Task 4: Engine — package, types and device resolution

**Files:**
- Modify: `pyproject.toml` (add the engine to sources and the dev group)
- Create: `packages/engine/pyproject.toml`
- Create: `packages/engine/src/swarmscribe_engine/__init__.py`
- Create: `packages/engine/src/swarmscribe_engine/version.py`
- Create: `packages/engine/src/swarmscribe_engine/types.py`
- Create: `packages/engine/src/swarmscribe_engine/device.py`
- Test: `packages/engine/tests/test_device.py`

**Interfaces:**
- Consumes: nothing internal.
- Produces (all importable from `swarmscribe_engine`):
  - `ENGINE_VERSION: str` (`"0.1.0"`)
  - `Device = Literal["cuda", "cpu"]`, `DevicePreference = Literal["auto", "cuda", "cpu"]`
  - `DEFAULT_TEMPERATURES = (0.0, 0.2, 0.4)`, `MAX_TEMPERATURE = 0.4`, `LANGUAGE = "en"`
  - `TranscribeSettings(model: str, compute_type: str, device: Device, temperatures: tuple[float, ...] = DEFAULT_TEMPERATURES)` — frozen, hashable; raises `ValueError` on an empty ladder or a value outside `[0.0, 0.4]`
  - `Word(start: float, end: float, word: str, probability: float)`
  - `Segment(start: float, end: float, text: str, words: tuple[Word, ...])`
  - `Transcript(source_name: str, source_checksum: str, duration: float, settings: TranscribeSettings, glossary: tuple[str, ...], segments: tuple[Segment, ...])`
  - `OutputFiles(txt: Path, srt: Path, segments_json: Path)`
  - `DeviceChoice(device: Device, model: str, compute_type: str)`
  - `EngineError(Exception)`, `UndecodableAudioError(EngineError)`, `DeviceUnavailableError(EngineError)`
  - `resolve_device(preference: DevicePreference = "auto", *, cuda_available: Callable[[], bool] = ...) -> DeviceChoice`

- [ ] **Step 1: Add the engine package to the workspace**

`packages/engine/pyproject.toml`:

```toml
[project]
name = "swarmscribe-engine"
version = "0.1.0"
description = "SwarmScribe single-file transcription engine"
requires-python = ">=3.11"
dependencies = ["faster-whisper>=1.1,<2"]

[project.scripts]
swarmscribe-engine = "swarmscribe_engine.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/swarmscribe_engine"]
```

In the root `pyproject.toml`, change these two tables to:

```toml
[tool.uv.sources]
swarmscribe-protocol = { workspace = true }
swarmscribe-engine = { workspace = true }

[dependency-groups]
dev = [
    "pytest>=8",
    "ruff>=0.6",
    "swarmscribe-protocol",
    "swarmscribe-engine",
]
```

- [ ] **Step 2: Write the failing tests**

`packages/engine/tests/test_device.py`:

```python
import dataclasses

import pytest

from swarmscribe_engine import (
    DeviceChoice,
    DeviceUnavailableError,
    TranscribeSettings,
    resolve_device,
)


def test_auto_picks_large_v3_float16_when_a_gpu_is_present():
    assert resolve_device("auto", cuda_available=lambda: True) == DeviceChoice(
        device="cuda", model="large-v3", compute_type="float16"
    )


def test_auto_picks_distil_int8_without_a_gpu():
    assert resolve_device("auto", cuda_available=lambda: False) == DeviceChoice(
        device="cpu", model="distil-large-v3", compute_type="int8"
    )


def test_cpu_can_be_forced_on_a_gpu_machine():
    assert resolve_device("cpu", cuda_available=lambda: True).device == "cpu"


def test_forcing_cuda_without_a_gpu_is_a_clear_error():
    with pytest.raises(DeviceUnavailableError, match="no CUDA GPU"):
        resolve_device("cuda", cuda_available=lambda: False)


def test_unknown_preference_is_rejected():
    with pytest.raises(ValueError, match="auto, cuda or cpu"):
        resolve_device("tpu", cuda_available=lambda: False)


def test_settings_default_to_the_clamped_ladder():
    settings = TranscribeSettings(model="large-v3", compute_type="float16", device="cuda")
    assert settings.temperatures == (0.0, 0.2, 0.4)


@pytest.mark.parametrize("temperatures", [(), (0.0, 0.6), (-0.1,)])
def test_settings_reject_an_unclamped_ladder(temperatures):
    with pytest.raises(ValueError):
        TranscribeSettings(
            model="large-v3", compute_type="float16", device="cuda", temperatures=temperatures
        )


def test_settings_are_immutable_and_hashable():
    settings = TranscribeSettings(model="large-v3", compute_type="float16", device="cuda")
    with pytest.raises(dataclasses.FrozenInstanceError):
        settings.model = "tiny.en"
    assert hash(settings) == hash(
        TranscribeSettings(model="large-v3", compute_type="float16", device="cuda")
    )
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv sync` then `uv run pytest packages/engine -v`
Expected: `uv sync` fails or pytest errors with `ModuleNotFoundError: No module named 'swarmscribe_engine'`. (`uv sync` downloads faster-whisper and CTranslate2 here; allow a few minutes.)

- [ ] **Step 4: Write the implementation**

`packages/engine/src/swarmscribe_engine/version.py`:

```python
ENGINE_VERSION = "0.1.0"
```

`packages/engine/src/swarmscribe_engine/types.py`:

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Device = Literal["cuda", "cpu"]
DevicePreference = Literal["auto", "cuda", "cpu"]

LANGUAGE = "en"
DEFAULT_TEMPERATURES: tuple[float, ...] = (0.0, 0.2, 0.4)
MAX_TEMPERATURE = 0.4


class EngineError(Exception):
    """Base class for every error the engine raises on purpose."""


class UndecodableAudioError(EngineError):
    """The file is not audio the decoder can read. Retrying will not help."""


class DeviceUnavailableError(EngineError):
    """A device was requested that this machine does not have."""


@dataclass(frozen=True)
class TranscribeSettings:
    model: str
    compute_type: str
    device: Device
    temperatures: tuple[float, ...] = DEFAULT_TEMPERATURES

    def __post_init__(self) -> None:
        if not self.temperatures:
            raise ValueError("temperatures must not be empty")
        if any(t < 0.0 or t > MAX_TEMPERATURE for t in self.temperatures):
            raise ValueError(f"temperatures must be between 0.0 and {MAX_TEMPERATURE}")


@dataclass(frozen=True)
class Word:
    start: float
    end: float
    word: str
    probability: float


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str
    words: tuple[Word, ...]


@dataclass(frozen=True)
class Transcript:
    source_name: str
    source_checksum: str
    duration: float
    settings: TranscribeSettings
    glossary: tuple[str, ...]
    segments: tuple[Segment, ...]


@dataclass(frozen=True)
class OutputFiles:
    txt: Path
    srt: Path
    segments_json: Path


@dataclass(frozen=True)
class DeviceChoice:
    device: Device
    model: str
    compute_type: str
```

`packages/engine/src/swarmscribe_engine/device.py`:

```python
from collections.abc import Callable

from .types import DeviceChoice, DevicePreference, DeviceUnavailableError

_CUDA = DeviceChoice(device="cuda", model="large-v3", compute_type="float16")
_CPU = DeviceChoice(device="cpu", model="distil-large-v3", compute_type="int8")


def _cuda_available() -> bool:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def resolve_device(
    preference: DevicePreference = "auto",
    *,
    cuda_available: Callable[[], bool] = _cuda_available,
) -> DeviceChoice:
    if preference == "cpu":
        return _CPU
    if preference == "cuda":
        if not cuda_available():
            raise DeviceUnavailableError("cuda was requested but no CUDA GPU is available")
        return _CUDA
    if preference == "auto":
        return _CUDA if cuda_available() else _CPU
    raise ValueError(f"device preference must be auto, cuda or cpu, not {preference!r}")
```

`packages/engine/src/swarmscribe_engine/__init__.py`:

```python
from .device import resolve_device
from .types import (
    DEFAULT_TEMPERATURES,
    LANGUAGE,
    MAX_TEMPERATURE,
    Device,
    DeviceChoice,
    DevicePreference,
    DeviceUnavailableError,
    EngineError,
    OutputFiles,
    Segment,
    Transcript,
    TranscribeSettings,
    UndecodableAudioError,
    Word,
)
from .version import ENGINE_VERSION

__all__ = [
    "DEFAULT_TEMPERATURES",
    "ENGINE_VERSION",
    "LANGUAGE",
    "MAX_TEMPERATURE",
    "Device",
    "DeviceChoice",
    "DevicePreference",
    "DeviceUnavailableError",
    "EngineError",
    "OutputFiles",
    "Segment",
    "Transcript",
    "TranscribeSettings",
    "UndecodableAudioError",
    "Word",
    "resolve_device",
]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv sync` then `uv run pytest packages/engine -v` then `uv run ruff check .`
Expected: all pass; ruff clean.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock packages/engine
git commit -m "Add engine package with types and device resolution"
```

---

### Task 5: Engine — writers

**Files:**
- Create: `packages/engine/src/swarmscribe_engine/writers.py`
- Create: `packages/engine/tests/conftest.py`
- Modify: `packages/engine/src/swarmscribe_engine/__init__.py`
- Test: `packages/engine/tests/test_writers.py`

**Interfaces:**
- Consumes: `Transcript`, `Segment`, `Word`, `TranscribeSettings`, `OutputFiles`, `ENGINE_VERSION`, `LANGUAGE` from Task 4. Tests also use `swarmscribe_protocol.SegmentsDocument` from Task 2.
- Produces (importable from `swarmscribe_engine`):
  - `write_outputs(transcript: Transcript, out_dir: Path) -> OutputFiles`
  - In `swarmscribe_engine.writers`: `format_srt_time(seconds: float) -> str`, `render_txt(transcript) -> str`, `render_srt(transcript) -> str`, `render_segments_json(transcript) -> str`
  - Test fixture `make_transcript(segments=..., **overrides) -> Transcript` in `conftest.py`, used by Tasks 5 and 7.

- [ ] **Step 1: Write the shared fixture**

`packages/engine/tests/conftest.py`:

```python
import pytest

from swarmscribe_engine import Segment, Transcript, TranscribeSettings, Word

DEFAULT_SEGMENTS = (
    Segment(
        start=0.0,
        end=1.5,
        text="Welcome to Shiloh.",
        words=(
            Word(start=0.0, end=0.4, word=" Welcome", probability=0.98),
            Word(start=0.4, end=0.6, word=" to", probability=0.99),
            Word(start=0.6, end=1.5, word=" Shiloh.", probability=0.71),
        ),
    ),
    Segment(
        start=2.0,
        end=3.25,
        text="Please be seated.",
        words=(Word(start=2.0, end=3.25, word=" Please be seated.", probability=0.9),),
    ),
)


@pytest.fixture
def make_transcript():
    def _make(segments=DEFAULT_SEGMENTS, **overrides) -> Transcript:
        values = {
            "source_name": "sermon.mp3",
            "source_checksum": "a" * 64,
            "duration": 3.25,
            "settings": TranscribeSettings(
                model="large-v3", compute_type="float16", device="cuda"
            ),
            "glossary": ("Shiloh",),
            "segments": tuple(segments),
        }
        values.update(overrides)
        return Transcript(**values)

    return _make
```

- [ ] **Step 2: Write the failing tests**

`packages/engine/tests/test_writers.py`:

```python
import json

import pytest
from swarmscribe_protocol import SegmentsDocument

from swarmscribe_engine import ENGINE_VERSION, Segment, write_outputs
from swarmscribe_engine.writers import format_srt_time, render_srt, render_txt


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0.0, "00:00:00,000"),
        (1.5, "00:00:01,500"),
        (59.9996, "00:01:00,000"),
        (3723.004, "01:02:03,004"),
        (360000.0, "100:00:00,000"),
        (-0.2, "00:00:00,000"),
    ],
)
def test_format_srt_time(seconds, expected):
    assert format_srt_time(seconds) == expected


def test_txt_is_one_segment_per_line(make_transcript):
    assert render_txt(make_transcript()) == "Welcome to Shiloh.\nPlease be seated.\n"


def test_srt_blocks_are_numbered_from_one_and_blank_line_separated(make_transcript):
    assert render_srt(make_transcript()) == (
        "1\n00:00:00,000 --> 00:00:01,500\nWelcome to Shiloh.\n"
        "\n"
        "2\n00:00:02,000 --> 00:00:03,250\nPlease be seated.\n"
    )


def test_outputs_are_named_after_the_source_file(make_transcript, tmp_path):
    files = write_outputs(make_transcript(), tmp_path)
    assert files.txt == tmp_path / "sermon.mp3.txt"
    assert files.srt == tmp_path / "sermon.mp3.srt"
    assert files.segments_json == tmp_path / "sermon.mp3.segments.json"
    assert all(path.is_file() for path in (files.txt, files.srt, files.segments_json))


def test_output_directory_is_created(make_transcript, tmp_path):
    out_dir = tmp_path / "2024" / "march"
    assert write_outputs(make_transcript(), out_dir).txt.is_file()


def test_no_temp_files_are_left_behind(make_transcript, tmp_path):
    write_outputs(make_transcript(), tmp_path)
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "sermon.mp3.segments.json",
        "sermon.mp3.srt",
        "sermon.mp3.txt",
    ]


def test_segments_json_conforms_to_the_protocol_schema(make_transcript, tmp_path):
    files = write_outputs(make_transcript(), tmp_path)
    document = SegmentsDocument.model_validate_json(files.segments_json.read_text("utf-8"))
    assert document.schema_version == 1
    assert document.source_checksum == "a" * 64
    assert document.duration == 3.25
    assert document.device == "cuda"
    assert document.engine_version == ENGINE_VERSION
    assert document.settings.model == "large-v3"
    assert document.settings.compute_type == "float16"
    assert document.settings.temperatures == (0.0, 0.2, 0.4)
    assert document.glossary == ["Shiloh"]
    assert document.segments[0].words[2].word == " Shiloh."
    assert document.segments[0].words[2].probability == 0.71


def test_segments_json_records_every_fixed_setting(make_transcript, tmp_path):
    files = write_outputs(make_transcript(), tmp_path)
    settings = json.loads(files.segments_json.read_text("utf-8"))["settings"]
    assert settings == {
        "model": "large-v3",
        "compute_type": "float16",
        "language": "en",
        "condition_on_previous_text": False,
        "temperatures": [0.0, 0.2, 0.4],
        "vad_filter": True,
        "word_timestamps": True,
    }


def test_no_speech_still_writes_all_three_outputs(make_transcript, tmp_path):
    files = write_outputs(make_transcript(segments=()), tmp_path)
    assert files.txt.read_text("utf-8") == ""
    assert files.srt.read_text("utf-8") == ""
    document = SegmentsDocument.model_validate_json(files.segments_json.read_text("utf-8"))
    assert document.segments == []


def test_non_ascii_text_is_written_as_utf8(make_transcript, tmp_path):
    text = "José said “grace”."
    transcript = make_transcript(
        segments=(Segment(start=0.0, end=1.0, text=text, words=()),), glossary=("José",)
    )
    files = write_outputs(transcript, tmp_path)
    assert files.txt.read_bytes() == (text + "\n").encode("utf-8")
    assert text.encode("utf-8") in files.srt.read_bytes()
    assert text.encode("utf-8") in files.segments_json.read_bytes()


def test_line_endings_are_lf_on_every_platform(make_transcript, tmp_path):
    files = write_outputs(make_transcript(), tmp_path)
    for path in (files.txt, files.srt, files.segments_json):
        assert b"\r\n" not in path.read_bytes()


def test_rewriting_replaces_existing_outputs(make_transcript, tmp_path):
    write_outputs(make_transcript(), tmp_path)
    files = write_outputs(make_transcript(segments=()), tmp_path)
    assert files.txt.read_text("utf-8") == ""
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest packages/engine/tests/test_writers.py -v`
Expected: FAIL with `ImportError: cannot import name 'write_outputs' from 'swarmscribe_engine'`

- [ ] **Step 4: Write the implementation**

`packages/engine/src/swarmscribe_engine/writers.py`:

```python
import json
import os
from pathlib import Path

from .types import LANGUAGE, OutputFiles, Transcript
from .version import ENGINE_VERSION

SCHEMA_VERSION = 1


def format_srt_time(seconds: float) -> str:
    total_ms = max(0, round(seconds * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def render_txt(transcript: Transcript) -> str:
    return "".join(f"{segment.text}\n" for segment in transcript.segments)


def render_srt(transcript: Transcript) -> str:
    blocks = [
        f"{index}\n"
        f"{format_srt_time(segment.start)} --> {format_srt_time(segment.end)}\n"
        f"{segment.text}\n"
        for index, segment in enumerate(transcript.segments, start=1)
    ]
    return "\n".join(blocks)


def render_segments_json(transcript: Transcript) -> str:
    settings = transcript.settings
    document = {
        "schema_version": SCHEMA_VERSION,
        "source_checksum": transcript.source_checksum,
        "duration": transcript.duration,
        "device": settings.device,
        "engine_version": ENGINE_VERSION,
        "settings": {
            "model": settings.model,
            "compute_type": settings.compute_type,
            "language": LANGUAGE,
            "condition_on_previous_text": False,
            "temperatures": list(settings.temperatures),
            "vad_filter": True,
            "word_timestamps": True,
        },
        "glossary": list(transcript.glossary),
        "segments": [
            {
                "start": segment.start,
                "end": segment.end,
                "text": segment.text,
                "words": [
                    {
                        "start": word.start,
                        "end": word.end,
                        "word": word.word,
                        "probability": word.probability,
                    }
                    for word in segment.words
                ],
            }
            for segment in transcript.segments
        ],
    }
    return json.dumps(document, ensure_ascii=False, indent=2) + "\n"


def _atomic_write(path: Path, text: str) -> None:
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temp, path)


def write_outputs(transcript: Transcript, out_dir: Path) -> OutputFiles:
    """Write txt, srt, then segments.json. segments.json last marks the file done."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = OutputFiles(
        txt=out_dir / f"{transcript.source_name}.txt",
        srt=out_dir / f"{transcript.source_name}.srt",
        segments_json=out_dir / f"{transcript.source_name}.segments.json",
    )
    _atomic_write(files.txt, render_txt(transcript))
    _atomic_write(files.srt, render_srt(transcript))
    _atomic_write(files.segments_json, render_segments_json(transcript))
    return files
```

In `packages/engine/src/swarmscribe_engine/__init__.py`, add this import after the `.version` import and add `"write_outputs"` to `__all__` (keep the list sorted):

```python
from .writers import write_outputs
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest packages/engine -v` then `uv run ruff check .`
Expected: all pass; ruff clean.

- [ ] **Step 6: Commit**

```bash
git add packages/engine
git commit -m "Add txt, srt and segments.json writers"
```

---

### Task 6: Engine — transcriber

**Files:**
- Create: `packages/engine/src/swarmscribe_engine/transcriber.py`
- Modify: `packages/engine/src/swarmscribe_engine/__init__.py`
- Test: `packages/engine/tests/test_transcriber.py`

**Interfaces:**
- Consumes: `TranscribeSettings`, `Transcript`, `Segment`, `Word`, `UndecodableAudioError`, `LANGUAGE` from Task 4.
- Produces (importable from `swarmscribe_engine`):
  - `Transcriber(settings: TranscribeSettings, *, model_factory: Callable[[TranscribeSettings], Any] = ..., decode_errors: tuple[type[BaseException], ...] | None = None)` — loads the model once in `__init__`.
  - `Transcriber.transcribe(path: Path, glossary: Sequence[str] = ()) -> Transcript`
  - `transcribe(path: Path, settings: TranscribeSettings, glossary: Sequence[str] = ()) -> Transcript` — module-level; reuses one loaded model per process for the same settings.
  - In `swarmscribe_engine.transcriber`: `build_prompt(terms: Sequence[str]) -> str | None`, `sha256_file(path: Path) -> str`
- Raises: `FileNotFoundError` if the path is not a file; `UndecodableAudioError` if the decoder rejects it. Any other exception from the model (for example GPU out of memory) propagates unchanged, because it may be retryable.

The model object is anything with faster-whisper's `WhisperModel.transcribe` shape: `transcribe(audio: str, **kwargs) -> (iterable of segments, info)`, where a segment has `.start`, `.end`, `.text`, `.words` (list or `None`; each word has `.start`, `.end`, `.word`, `.probability`) and `info` has `.duration`. Tests inject a fake with that shape.

- [ ] **Step 1: Write the failing tests**

`packages/engine/tests/test_transcriber.py`:

```python
import hashlib
from types import SimpleNamespace

import pytest

from swarmscribe_engine import TranscribeSettings, Transcriber, UndecodableAudioError
from swarmscribe_engine.transcriber import build_prompt, sha256_file

SETTINGS = TranscribeSettings(model="large-v3", compute_type="float16", device="cuda")


class DecodeError(Exception):
    pass


def raw_word(start, end, word, probability):
    return SimpleNamespace(start=start, end=end, word=word, probability=probability)


def raw_segment(start, end, text, words):
    return SimpleNamespace(start=start, end=end, text=text, words=words)


class FakeModel:
    def __init__(self, segments=(), duration=0.0, error=None, error_while_iterating=None):
        self.segments = segments
        self.duration = duration
        self.error = error
        self.error_while_iterating = error_while_iterating
        self.calls = []

    def transcribe(self, audio, **kwargs):
        self.calls.append((audio, kwargs))
        if self.error is not None:
            raise self.error
        return self._iterate(), SimpleNamespace(duration=self.duration)

    def _iterate(self):
        yield from self.segments
        if self.error_while_iterating is not None:
            raise self.error_while_iterating


def make_transcriber(model, settings=SETTINGS):
    return Transcriber(settings, model_factory=lambda _: model, decode_errors=(DecodeError,))


@pytest.fixture
def audio(tmp_path):
    path = tmp_path / "sermon.mp3"
    path.write_bytes(b"not really audio, the model is fake")
    return path


def test_model_is_loaded_once_and_reused(audio):
    loads = []
    model = FakeModel()

    def factory(settings):
        loads.append(settings)
        return model

    transcriber = Transcriber(SETTINGS, model_factory=factory, decode_errors=())
    transcriber.transcribe(audio)
    transcriber.transcribe(audio)
    assert loads == [SETTINGS]
    assert len(model.calls) == 2


def test_fixed_settings_are_passed_to_the_model(audio):
    model = FakeModel()
    make_transcriber(model).transcribe(audio, glossary=["Shiloh", "José"])
    path_arg, kwargs = model.calls[0]
    assert path_arg == str(audio)
    assert kwargs == {
        "language": "en",
        "condition_on_previous_text": False,
        "temperature": [0.0, 0.2, 0.4],
        "vad_filter": True,
        "word_timestamps": True,
        "initial_prompt": "Glossary: Shiloh, José.",
    }


def test_custom_ladder_is_passed_through(audio):
    model = FakeModel()
    settings = TranscribeSettings(
        model="large-v3", compute_type="float16", device="cuda", temperatures=(0.0,)
    )
    make_transcriber(model, settings).transcribe(audio)
    assert model.calls[0][1]["temperature"] == [0.0]


def test_segments_and_words_are_converted(audio):
    model = FakeModel(
        segments=[
            raw_segment(
                0.0,
                1.5,
                " Welcome to Shiloh. ",
                [raw_word(0.0, 0.4, " Welcome", 0.98), raw_word(0.4, 1.5, " to Shiloh.", 0.7)],
            )
        ],
        duration=1.5,
    )
    transcript = make_transcriber(model).transcribe(audio, glossary=["Shiloh"])
    assert transcript.source_name == "sermon.mp3"
    assert transcript.source_checksum == hashlib.sha256(audio.read_bytes()).hexdigest()
    assert transcript.duration == 1.5
    assert transcript.settings == SETTINGS
    assert transcript.glossary == ("Shiloh",)
    (segment,) = transcript.segments
    assert segment.text == "Welcome to Shiloh."
    assert segment.words[0].word == " Welcome"
    assert segment.words[1].probability == 0.7


def test_no_speech_gives_an_empty_transcript(audio):
    transcript = make_transcriber(FakeModel(duration=30.0)).transcribe(audio)
    assert transcript.segments == ()
    assert transcript.duration == 30.0


def test_probability_is_clamped_to_zero_and_one(audio):
    model = FakeModel(
        segments=[
            raw_segment(
                0.0,
                1.0,
                "Amen.",
                [raw_word(0.0, 0.5, " A", 1.0000001), raw_word(0.5, 1.0, "men.", -0.0000001)],
            )
        ]
    )
    words = make_transcriber(model).transcribe(audio).segments[0].words
    assert [word.probability for word in words] == [1.0, 0.0]


def test_segment_without_words_gets_an_empty_tuple(audio):
    model = FakeModel(segments=[raw_segment(0.0, 1.0, "Amen.", None)])
    assert make_transcriber(model).transcribe(audio).segments[0].words == ()


def test_whitespace_only_segments_are_dropped(audio):
    model = FakeModel(
        segments=[raw_segment(0.0, 1.0, "   ", []), raw_segment(1.0, 2.0, "Amen.", [])]
    )
    transcript = make_transcriber(model).transcribe(audio)
    assert [segment.text for segment in transcript.segments] == ["Amen."]


def test_missing_file_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        make_transcriber(FakeModel()).transcribe(tmp_path / "missing.mp3")


def test_a_directory_is_not_a_recording(tmp_path):
    with pytest.raises(FileNotFoundError):
        make_transcriber(FakeModel()).transcribe(tmp_path)


def test_decode_failure_becomes_undecodable_audio_error(audio):
    transcriber = make_transcriber(FakeModel(error=DecodeError("Invalid data found")))
    with pytest.raises(UndecodableAudioError, match="sermon.mp3") as excinfo:
        transcriber.transcribe(audio)
    assert isinstance(excinfo.value.__cause__, DecodeError)


def test_decode_failure_during_iteration_is_also_caught(audio):
    model = FakeModel(
        segments=[raw_segment(0.0, 1.0, "Amen.", [])],
        error_while_iterating=DecodeError("truncated"),
    )
    with pytest.raises(UndecodableAudioError):
        make_transcriber(model).transcribe(audio)


def test_other_model_errors_propagate_unchanged(audio):
    transcriber = make_transcriber(FakeModel(error=RuntimeError("CUDA out of memory")))
    with pytest.raises(RuntimeError, match="CUDA out of memory"):
        transcriber.transcribe(audio)


@pytest.mark.parametrize(
    ("terms", "expected"),
    [
        ([], None),
        (["  ", ""], None),
        (["Shiloh"], "Glossary: Shiloh."),
        ([" Shiloh ", "", "José"], "Glossary: Shiloh, José."),
    ],
)
def test_build_prompt(terms, expected):
    assert build_prompt(terms) == expected


def test_blank_glossary_terms_are_not_recorded(audio):
    transcript = make_transcriber(FakeModel()).transcribe(audio, glossary=[" Shiloh ", " "])
    assert transcript.glossary == ("Shiloh",)


def test_sha256_file_matches_hashlib(tmp_path):
    path = tmp_path / "big.bin"
    data = b"x" * (3 * 1024 * 1024 + 7)
    path.write_bytes(data)
    assert sha256_file(path) == hashlib.sha256(data).hexdigest()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/engine/tests/test_transcriber.py -v`
Expected: FAIL with `ImportError: cannot import name 'Transcriber' from 'swarmscribe_engine'`

- [ ] **Step 3: Write the implementation**

`packages/engine/src/swarmscribe_engine/transcriber.py`:

```python
import hashlib
from collections.abc import Callable, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

from .types import (
    LANGUAGE,
    Segment,
    Transcript,
    TranscribeSettings,
    UndecodableAudioError,
    Word,
)

ModelFactory = Callable[[TranscribeSettings], Any]

_CHUNK = 1024 * 1024


def _default_model_factory(settings: TranscribeSettings) -> Any:
    from faster_whisper import WhisperModel

    return WhisperModel(
        settings.model, device=settings.device, compute_type=settings.compute_type
    )


def _default_decode_errors() -> tuple[type[BaseException], ...]:
    try:
        import av
    except ImportError:
        return ()
    return (av.error.FFmpegError,)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _clean_terms(terms: Sequence[str]) -> tuple[str, ...]:
    return tuple(term.strip() for term in terms if term.strip())


def build_prompt(terms: Sequence[str]) -> str | None:
    cleaned = _clean_terms(terms)
    if not cleaned:
        return None
    return "Glossary: " + ", ".join(cleaned) + "."


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


def _convert(raw: Any) -> Segment:
    words = tuple(
        Word(
            start=float(word.start),
            end=float(word.end),
            word=word.word,
            probability=_clamp(word.probability),
        )
        for word in (raw.words or ())
    )
    return Segment(start=float(raw.start), end=float(raw.end), text=raw.text.strip(), words=words)


class Transcriber:
    """Holds one loaded model and transcribes files with the fixed settings."""

    def __init__(
        self,
        settings: TranscribeSettings,
        *,
        model_factory: ModelFactory = _default_model_factory,
        decode_errors: tuple[type[BaseException], ...] | None = None,
    ) -> None:
        self.settings = settings
        self._decode_errors = _default_decode_errors() if decode_errors is None else decode_errors
        self._model = model_factory(settings)

    def transcribe(self, path: Path, glossary: Sequence[str] = ()) -> Transcript:
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(f"recording not found: {path}")
        terms = _clean_terms(glossary)
        try:
            raw_segments, info = self._model.transcribe(
                str(path),
                language=LANGUAGE,
                condition_on_previous_text=False,
                temperature=list(self.settings.temperatures),
                vad_filter=True,
                word_timestamps=True,
                initial_prompt=build_prompt(terms),
            )
            segments = tuple(
                segment for segment in (_convert(raw) for raw in raw_segments) if segment.text
            )
        except self._decode_errors as exc:
            raise UndecodableAudioError(f"cannot decode {path.name}: {exc}") from exc
        return Transcript(
            source_name=path.name,
            source_checksum=sha256_file(path),
            duration=float(info.duration),
            settings=self.settings,
            glossary=terms,
            segments=segments,
        )


@lru_cache(maxsize=1)
def _shared_transcriber(settings: TranscribeSettings) -> Transcriber:
    return Transcriber(settings)


def transcribe(
    path: Path, settings: TranscribeSettings, glossary: Sequence[str] = ()
) -> Transcript:
    """Transcribe one file, reusing the model already loaded for these settings."""
    return _shared_transcriber(settings).transcribe(path, glossary)
```

In `packages/engine/src/swarmscribe_engine/__init__.py`, add this import after the `.device` import and add `"Transcriber"` and `"transcribe"` to `__all__` (keep the list sorted):

```python
from .transcriber import Transcriber, transcribe
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest packages/engine -v` then `uv run ruff check .`
Expected: all pass; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add packages/engine
git commit -m "Add transcriber with fixed Whisper settings"
```

---

### Task 7: Engine — CLI, smoke test and README

**Files:**
- Create: `packages/engine/src/swarmscribe_engine/cli.py`
- Create: `README.md`
- Test: `packages/engine/tests/test_cli.py`, `packages/engine/tests/test_smoke.py`

**Interfaces:**
- Consumes: `transcribe`, `resolve_device`, `write_outputs`, `TranscribeSettings`, `EngineError`, `UndecodableAudioError`, `DeviceUnavailableError`, and the `make_transcript` fixture.
- Produces:
  - Console command `swarmscribe-engine FILE --out DIR [--glossary FILE] [--device auto|cuda|cpu] [--model NAME] [--compute-type TYPE]`
  - `swarmscribe_engine.cli.run(argv: Sequence[str], *, transcribe_fn=transcribe, resolve_fn=resolve_device) -> int` — exit code `0` on success, `2` on a missing file, undecodable audio or unavailable device.
  - `swarmscribe_engine.cli.read_glossary(path: Path) -> tuple[str, ...]` — one term per line; blank lines and lines starting with `#` ignored; tolerates a UTF-8 byte-order mark.
  - `swarmscribe_engine.cli.main() -> None` — the console entry point.
- The CLI prints the three output paths on success and one `error: …` line on stderr on failure. It never prints transcript text.

- [ ] **Step 1: Write the failing CLI tests**

`packages/engine/tests/test_cli.py`:

```python
from swarmscribe_engine import (
    DeviceChoice,
    DeviceUnavailableError,
    TranscribeSettings,
    UndecodableAudioError,
)
from swarmscribe_engine.cli import read_glossary, run

CPU = DeviceChoice(device="cpu", model="distil-large-v3", compute_type="int8")


def _recording(tmp_path):
    path = tmp_path / "sermon.mp3"
    path.write_bytes(b"fake")
    return path


def test_run_writes_outputs_and_prints_their_paths(tmp_path, make_transcript, capsys):
    recording = _recording(tmp_path)
    out_dir = tmp_path / "out"
    calls = []

    def fake_transcribe(path, settings, glossary):
        calls.append((path, settings, glossary))
        return make_transcript(settings=settings)

    code = run(
        [str(recording), "--out", str(out_dir)],
        transcribe_fn=fake_transcribe,
        resolve_fn=lambda preference: CPU,
    )

    assert code == 0
    assert calls == [
        (
            recording,
            TranscribeSettings(model="distil-large-v3", compute_type="int8", device="cpu"),
            (),
        )
    ]
    assert (out_dir / "sermon.mp3.segments.json").is_file()
    printed = capsys.readouterr().out
    assert "sermon.mp3.txt" in printed
    assert "sermon.mp3.srt" in printed
    assert "sermon.mp3.segments.json" in printed
    assert "Welcome to Shiloh" not in printed


def test_device_preference_is_forwarded(tmp_path, make_transcript):
    seen = []

    def resolve(preference):
        seen.append(preference)
        return CPU

    run(
        [str(_recording(tmp_path)), "--out", str(tmp_path), "--device", "cpu"],
        transcribe_fn=lambda path, settings, glossary: make_transcript(),
        resolve_fn=resolve,
    )
    assert seen == ["cpu"]


def test_model_and_compute_type_can_be_overridden(tmp_path, make_transcript):
    seen = []

    def fake_transcribe(path, settings, glossary):
        seen.append(settings)
        return make_transcript()

    run(
        [
            str(_recording(tmp_path)),
            "--out",
            str(tmp_path),
            "--model",
            "tiny.en",
            "--compute-type",
            "float32",
        ],
        transcribe_fn=fake_transcribe,
        resolve_fn=lambda preference: CPU,
    )
    assert seen == [TranscribeSettings(model="tiny.en", compute_type="float32", device="cpu")]


def test_glossary_file_is_read_and_passed(tmp_path, make_transcript):
    glossary = tmp_path / "glossary.txt"
    glossary.write_text("# people\nJosé\n\n  Shiloh  \n", encoding="utf-8")
    seen = []

    def fake_transcribe(path, settings, terms):
        seen.append(terms)
        return make_transcript()

    run(
        [str(_recording(tmp_path)), "--out", str(tmp_path), "--glossary", str(glossary)],
        transcribe_fn=fake_transcribe,
        resolve_fn=lambda preference: CPU,
    )
    assert seen == [("José", "Shiloh")]


def test_glossary_saved_by_notepad_with_a_bom_is_read_cleanly(tmp_path):
    glossary = tmp_path / "glossary.txt"
    glossary.write_bytes(b"\xef\xbb\xbfShiloh\r\nJos\xc3\xa9\r\n")
    assert read_glossary(glossary) == ("Shiloh", "José")


def test_missing_recording_exits_2_with_a_message(tmp_path, capsys):
    def fake_transcribe(path, settings, glossary):
        raise FileNotFoundError(f"recording not found: {path}")

    code = run(
        [str(tmp_path / "missing.mp3"), "--out", str(tmp_path)],
        transcribe_fn=fake_transcribe,
        resolve_fn=lambda preference: CPU,
    )
    assert code == 2
    assert "error: recording not found" in capsys.readouterr().err


def test_missing_glossary_file_exits_2_with_a_message(tmp_path, make_transcript, capsys):
    code = run(
        [
            str(_recording(tmp_path)),
            "--out",
            str(tmp_path),
            "--glossary",
            str(tmp_path / "nope.txt"),
        ],
        transcribe_fn=lambda path, settings, glossary: make_transcript(),
        resolve_fn=lambda preference: CPU,
    )
    assert code == 2
    assert "error:" in capsys.readouterr().err


def test_undecodable_audio_exits_2_and_writes_nothing(tmp_path, capsys):
    out_dir = tmp_path / "out"

    def fake_transcribe(path, settings, glossary):
        raise UndecodableAudioError("cannot decode sermon.mp3: Invalid data")

    code = run(
        [str(_recording(tmp_path)), "--out", str(out_dir)],
        transcribe_fn=fake_transcribe,
        resolve_fn=lambda preference: CPU,
    )
    assert code == 2
    assert "error: cannot decode sermon.mp3" in capsys.readouterr().err
    assert not out_dir.exists()


def test_unavailable_device_exits_2(tmp_path, make_transcript, capsys):
    def resolve(preference):
        raise DeviceUnavailableError("cuda was requested but no CUDA GPU is available")

    code = run(
        [str(_recording(tmp_path)), "--out", str(tmp_path), "--device", "cuda"],
        transcribe_fn=lambda path, settings, glossary: make_transcript(),
        resolve_fn=resolve,
    )
    assert code == 2
    assert "no CUDA GPU" in capsys.readouterr().err
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/engine/tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'swarmscribe_engine.cli'`

- [ ] **Step 3: Write the CLI**

`packages/engine/src/swarmscribe_engine/cli.py`:

```python
import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from .device import resolve_device
from .transcriber import transcribe
from .types import DeviceChoice, EngineError, Transcript, TranscribeSettings
from .writers import write_outputs

TranscribeFn = Callable[[Path, TranscribeSettings, Sequence[str]], Transcript]
ResolveFn = Callable[[str], DeviceChoice]


def read_glossary(path: Path) -> tuple[str, ...]:
    lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
    return tuple(
        line.strip() for line in lines if line.strip() and not line.strip().startswith("#")
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="swarmscribe-engine",
        description="Transcribe one English recording to txt, srt and segments.json.",
    )
    parser.add_argument("file", type=Path, help="audio or video file")
    parser.add_argument("--out", type=Path, required=True, help="output directory")
    parser.add_argument("--glossary", type=Path, help="text file, one name or term per line")
    parser.add_argument("--device", choices=["auto", "cuda", "cpu"], default="auto")
    parser.add_argument("--model", help="override the model chosen for the device")
    parser.add_argument("--compute-type", help="override the compute type chosen for the device")
    return parser


def run(
    argv: Sequence[str],
    *,
    transcribe_fn: TranscribeFn = transcribe,
    resolve_fn: ResolveFn = resolve_device,
) -> int:
    args = _parser().parse_args(argv)
    try:
        choice = resolve_fn(args.device)
        settings = TranscribeSettings(
            model=args.model or choice.model,
            compute_type=args.compute_type or choice.compute_type,
            device=choice.device,
        )
        glossary = read_glossary(args.glossary) if args.glossary else ()
        transcript = transcribe_fn(args.file, settings, glossary)
        files = write_outputs(transcript, args.out)
    except (EngineError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for path in (files.txt, files.srt, files.segments_json):
        print(path)
    return 0


def main() -> None:
    sys.exit(run(sys.argv[1:]))
```

- [ ] **Step 4: Run the CLI tests to verify they pass**

Run: `uv run pytest packages/engine -v` then `uv run ruff check .`
Expected: all pass; ruff clean.

- [ ] **Step 5: Write the smoke test**

This runs the real `tiny.en` model, so it proves three things the fakes cannot: the keyword arguments are accepted by the installed faster-whisper, a no-speech file produces valid outputs, and a corrupt file raises `UndecodableAudioError` from the real decoder. It uses a generated tone, not speech; a speech-accuracy check waits until the owner supplies a consented clip.

`packages/engine/tests/test_smoke.py`:

```python
import math
import struct
import wave

import pytest
from swarmscribe_protocol import SegmentsDocument

from swarmscribe_engine import (
    TranscribeSettings,
    Transcriber,
    UndecodableAudioError,
    write_outputs,
)

pytestmark = pytest.mark.smoke

SETTINGS = TranscribeSettings(model="tiny.en", compute_type="int8", device="cpu")


@pytest.fixture(scope="module")
def transcriber():
    return Transcriber(SETTINGS)


@pytest.fixture
def tone(tmp_path):
    path = tmp_path / "tone.wav"
    rate = 16000
    frames = b"".join(
        struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / rate)))
        for i in range(rate * 3)
    )
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(frames)
    return path


def test_real_model_accepts_the_fixed_settings_and_outputs_validate(transcriber, tone, tmp_path):
    transcript = transcriber.transcribe(tone, glossary=["Shiloh"])
    assert transcript.duration == pytest.approx(3.0, abs=0.1)

    files = write_outputs(transcript, tmp_path / "out")
    document = SegmentsDocument.model_validate_json(files.segments_json.read_text("utf-8"))
    assert document.settings.model == "tiny.en"
    assert files.txt.is_file()
    assert files.srt.is_file()


def test_real_decoder_rejects_a_corrupt_file(transcriber, tmp_path):
    corrupt = tmp_path / "corrupt.mp3"
    corrupt.write_bytes(bytes(range(256)) * 64)
    with pytest.raises(UndecodableAudioError, match="corrupt.mp3"):
        transcriber.transcribe(corrupt)
```

- [ ] **Step 6: Run the smoke test**

Run: `uv run pytest -m smoke -v`
Expected: 2 passed. The first run downloads `tiny.en` (about 75 MB).

If `test_real_decoder_rejects_a_corrupt_file` fails because the real library raised a different exception type, read the type from the failure output, add that exact type to the tuple returned by `_default_decode_errors()` in `transcriber.py`, and re-run. Do not widen it to `Exception`.

Then confirm the default run still excludes it:
Run: `uv run pytest -v`
Expected: all unit tests pass, the 2 smoke tests are deselected.

- [ ] **Step 7: Run the CLI by hand**

Create a 3-second tone and transcribe it:

```bash
uv run python -c "import math,struct,wave; w=wave.open('tone.wav','wb'); w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b''.join(struct.pack('<h', int(8000*math.sin(2*math.pi*440*i/16000))) for i in range(48000))); w.close()"
uv run swarmscribe-engine tone.wav --out out --device cpu --model tiny.en
```

Expected: three paths printed, ending `tone.wav.txt`, `tone.wav.srt`, `tone.wav.segments.json`; exit code 0. Delete `tone.wav` and `out/` afterwards.

- [ ] **Step 8: Write the README**

`README.md`:

````markdown
# SwarmScribe

Distributed transcription for a large archive of English recordings. A leader
hands recordings to follower machines, which transcribe them and return text,
subtitles and word-level timings. Only recordings explicitly marked OK to
publish are ever processed.

Design: [`docs/superpowers/specs/2026-10-02-swarmscribe-architecture-design.md`](docs/superpowers/specs/2026-10-02-swarmscribe-architecture-design.md)

## Status

| Package | State |
|---|---|
| `swarmscribe-protocol` — leader–follower wire models | Built |
| `swarmscribe-engine` — single-file transcriber | Built |
| `swarmscribe-leader` | Not started |
| `swarmscribe-follower` | Not started |
| Helm chart | Not started |

## Transcribe one file

```
uv sync
uv run swarmscribe-engine sermon.mp3 --out out --glossary glossary.txt
```

Writes `out/sermon.mp3.txt`, `out/sermon.mp3.srt` and
`out/sermon.mp3.segments.json`.

- `--device auto|cuda|cpu` — default `auto`: a CUDA GPU uses `large-v3`
  (`float16`); otherwise `distil-large-v3` (`int8`) on CPU.
- `--model`, `--compute-type` — override the choice.
- `--glossary` — a text file with one name or term per line. `#` starts a
  comment. A short list of ministry, place and people names noticeably
  improves accuracy.

Some settings are fixed on purpose: English only, no conditioning on previous
text (prevents repeated-sentence loops), and a temperature ladder capped at
0.4 (prevents gibberish on noisy audio).

## Develop

```
uv sync
uv run pytest            # unit tests
uv run pytest -m smoke   # downloads tiny.en and runs the real model
uv run ruff check .
```

If you change a wire model, the schema snapshot test fails. Decide whether
`PROTOCOL_VERSION` must change, then regenerate:

```
uv run python -m swarmscribe_protocol.schema packages/protocol/tests/schema_v1.json
```
````

- [ ] **Step 9: Commit**

```bash
git add README.md packages/engine
git commit -m "Add engine CLI, smoke test and README"
```
