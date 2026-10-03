# Leader Core (Plan A1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A leader that scans a local folder for consented recordings, hands them to followers over the `/v1` API with leases, serves and receives files through signed links, and recovers from dead followers — proven by an in-process end-to-end test with fake followers.

**Architecture:** New package `swarmscribe-leader` (FastAPI, SQLAlchemy 2 async on asyncpg, Alembic). Domain logic lives in small modules that take an `AsyncSession` and plain arguments (job store, reaper, scanner, follower auth, storage); the FastAPI layer only authenticates, calls one function and commits. Background loops (reaper, scanner) run in every replica and are made exclusive with Postgres advisory locks. Tests run against real Postgres: an embedded `pgserver` locally, a service container in CI.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2.0 (asyncio), asyncpg, Alembic, pydantic-settings, httpx, pytest-asyncio, pgserver.

**Spec:** `docs/superpowers/specs/2026-10-02-leader-design.md` — sections 3, 4, 5, 6, 7, 8.1–8.3, 9 (local backend), 10 (follower part), 11, 12 (health), 13, 15 (Plan A). The rest of Plan A — OIDC sign-in and roles, the admin API and `swarmscribe-admin` CLI, and the multi-replica Docker Compose test — is **Plan A2**, written after this one lands.

## Global Constraints

- Dependency rule: `protocol` imports nothing internal; `engine` imports nothing internal at runtime; `leader` imports `swarmscribe_protocol` only (never the engine).
- `PROTOCOL_VERSION` stays `1`; the schema snapshot is regenerated with the exporter, never hand-edited.
- Every checksum on the wire is lowercase hex SHA-256 (`^[0-9a-f]{64}$`).
- Job states: `queued`, `leased`, `completed`, `failed`, `cancelled`. Recording consent: `consented`, `not_consented`, `withdrawn`. Follower states: `active`, `draining`, `revoked`, `gone`.
- Defaults: lease 120 s, heartbeat 30 s, max attempts 3, claim `Retry-After` 10 s, reaper every 15 s, scanner every 30 s, location scan interval 900 s, follower gone after 600 s unseen, download link 30 min, upload link 2 h.
- Failure codes `source_changed` and `undecodable` are never retried, whatever `retryable` says.
- A recording becomes a job only if its location's `consent.txt` matches it. Nothing in this plan can queue anything else.
- Secrets (join tokens, follower credentials) are 32 random bytes, URL-safe base64, stored only as SHA-256 hex.
- Errors returned by the API have the body `{"code": "...", "message": "..."}`.
- Logs never contain transcript text, credentials or links.
- Nothing in code, defaults, examples or test data may be specific to one kind of content or organisation (use neutral names such as `talks/one.mp3`).
- On this Windows machine `uv` is not on PATH: run `python -m uv …` wherever a step says `uv …`.
- If `ruff check` flags import order or line length in code copied from this plan, reorder imports (`ruff check --fix --select I`) or wrap the line without changing behaviour.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Two followers (or two leader replicas) claiming at the same instant**: each job is leased to exactly one. — Task 5 (50 concurrent claimers over 20 jobs).
2. **The reaper running while a follower is mid-submit on the same job**: the locked job is skipped, never half-updated. — Task 6.
3. **A recording replaced at the same path after ingest**: the pinned download answers `412 source_changed`, and the next scan cancels the old job and queues the new version. — Task 8, Task 9.
4. **Hostile or stale links and keys**: tampered, expired or wrong-method links are refused; storage keys with `..`, absolute paths or backslashes are rejected. — Task 7, Task 9.
5. **Consent withdrawn while a job is leased**: the follower's next heartbeat says `cancel`, its submit is refused, and completed outputs are flagged. — Task 5, Task 8.

## File Structure

```
pyproject.toml                       MOD  workspace: leader, dev deps, pytest-asyncio config, ruff exclude
.gitignore                           MOD  .pgdata/
.github/workflows/ci.yml             MOD  Postgres service container
README.md                            MOD  "Run the leader" section (Task 11)
packages/protocol/src/swarmscribe_protocol/
  base.py                            MOD  Sha256 type
  messages.py                        MOD  source_version, checksums, FailureCode, ErrorBody
  segments.py                        MOD  source_checksum is Sha256
  vocabulary_files.py                NEW  parse_terms, parse_corrections
  schema.py, __init__.py             MOD
packages/protocol/tests/
  vocabulary_file_cases.json         NEW  shared parser cases
  test_vocabulary_files.py           NEW
  test_messages.py                   MOD
packages/engine/tests/test_vocabulary_file_cases.py   NEW
packages/leader/
  pyproject.toml
  src/swarmscribe_leader/
    __init__.py  clock.py  config.py  errors.py  audit.py  background.py  app.py  main.py
    db/  __init__.py  session.py  models.py  migrate.py
         migrations/  env.py  script.py.mako  versions/0001_initial_schema.py
    auth/  __init__.py  secrets.py  followers.py
    jobs/  __init__.py  store.py  reaper.py  claims.py
    storage/  __init__.py  base.py  links.py  local.py  registry.py
    ingest/  __init__.py  consent.py  scanner.py
    api/  __init__.py  deps.py  errors.py  health.py  files.py  follower.py
  tests/
    conftest.py  test_config.py  test_database.py  test_migrations.py
    test_follower_auth.py  test_job_store.py  test_reaper.py  test_links.py
    test_local_storage.py  test_consent.py  test_scanner.py  test_app.py
    test_follower_api.py  test_end_to_end.py
```

---

### Task 1: Protocol changes for the leader

**Files:**
- Modify: `packages/protocol/src/swarmscribe_protocol/base.py`, `segments.py`, `messages.py`, `schema.py`, `__init__.py`
- Create: `packages/protocol/src/swarmscribe_protocol/vocabulary_files.py`
- Create: `packages/protocol/tests/vocabulary_file_cases.json`, `packages/protocol/tests/test_vocabulary_files.py`
- Create: `packages/engine/tests/test_vocabulary_file_cases.py`
- Modify: `packages/protocol/tests/test_messages.py`
- Regenerate: `packages/protocol/tests/schema_v1.json`

**Interfaces:**
- Produces (from `swarmscribe_protocol`): `Sha256` (annotated `str`), `FailureCode = Literal["source_changed", "undecodable", "engine_error", "out_of_resources", "other"]`, `ErrorBody(code: str, message: str)`.
- `ClaimResponse.source_version: str` (min length 1) replaces `source_checksum`.
- `OutputChecksums(source: Sha256, txt: Sha256, srt: Sha256, segments_json: Sha256)`.
- `FailRequest(lease_id: str, code: FailureCode, reason: str, retryable: bool)`.
- `SegmentsDocument.source_checksum: Sha256`.
- `swarmscribe_protocol.vocabulary_files`: `VocabularyFileError(ValueError)`, `parse_terms(text: str) -> list[str]`, `parse_corrections(text: str, source: str = "corrections.txt") -> list[Correction]`. Same rules and messages as the engine CLI's `read_terms` / `read_corrections`.

- [ ] **Step 1: Write the shared parser cases**

`packages/protocol/tests/vocabulary_file_cases.json`:

```json
[
  {"name": "terms: comments and blank lines", "kind": "terms", "text": "# people\nJosé\n\n  Ashford  \n", "expect": ["José", "Ashford"]},
  {"name": "terms: a hash inside a line is text", "kind": "terms", "text": "C#\nF# notes\n", "expect": ["C#", "F# notes"]},
  {"name": "terms: byte-order mark and CRLF", "kind": "terms", "text": "﻿Ashford\r\nJason\r\n", "expect": ["Ashford", "Jason"]},
  {"name": "corrections: basic", "kind": "corrections", "text": "jay son => Jason\nashferd=>Ashford\n", "expect": [["jay son", "Jason"], ["ashferd", "Ashford"]]},
  {"name": "corrections: arrow inside the replacement", "kind": "corrections", "text": "a to b => a => b\n", "expect": [["a to b", "a => b"]]},
  {"name": "corrections: byte-order mark and CRLF", "kind": "corrections", "text": "﻿jose => José\r\njay son => Jason\r\n", "expect": [["jose", "José"], ["jay son", "Jason"]]},
  {"name": "corrections: exact duplicate dropped", "kind": "corrections", "text": "Jay  Son => Jason\nashferd => Ashford\njay son => Jason\n", "expect": [["Jay  Son", "Jason"], ["ashferd", "Ashford"]]},
  {"name": "corrections: punctuation in heard is allowed", "kind": "corrections", "text": "c++ => C++\nc# => C#\ndr. => Doctor\n", "expect": [["c++", "C++"], ["c#", "C#"], ["dr.", "Doctor"]]},
  {"name": "corrections: missing arrow", "kind": "corrections", "text": "# header\njay son Jason\n", "error": "corrections.txt line 2: expected 'heard as => should be'"},
  {"name": "corrections: empty heard", "kind": "corrections", "text": "=> Jason\n", "error": "corrections.txt line 1: expected 'heard as => should be'"},
  {"name": "corrections: empty replacement", "kind": "corrections", "text": "jay son =>\n", "error": "corrections.txt line 1: expected 'heard as => should be'"},
  {"name": "corrections: a part with no word", "kind": "corrections", "text": "jay -- son => Jason\n", "error": "corrections.txt line 1: 'heard as' must contain a word in every part"},
  {"name": "corrections: punctuation only", "kind": "corrections", "text": "& => and\n", "error": "corrections.txt line 1: 'heard as' must contain a word in every part"},
  {"name": "corrections: conflicting duplicate", "kind": "corrections", "text": "Jay  Son => Jason\nashferd => Ashford\njay son => Jayson\n", "error": "corrections.txt line 3: 'jay son' is already defined on line 1"}
]
```

- [ ] **Step 2: Write the failing tests**

`packages/protocol/tests/test_vocabulary_files.py`:

```python
import json
import re
from pathlib import Path

import pytest

from swarmscribe_protocol.vocabulary_files import (
    VocabularyFileError,
    parse_corrections,
    parse_terms,
)

CASES = json.loads(
    (Path(__file__).parent / "vocabulary_file_cases.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_parser_matches_the_shared_cases(case):
    if case["kind"] == "terms":
        result = parse_terms(case["text"])
        assert result == case["expect"]
        return
    if "error" in case:
        with pytest.raises(VocabularyFileError, match=re.escape(case["error"])):
            parse_corrections(case["text"], source="corrections.txt")
        return
    result = parse_corrections(case["text"], source="corrections.txt")
    assert [[c.heard, c.replacement] for c in result] == case["expect"]


def test_the_error_names_the_given_source():
    with pytest.raises(VocabularyFileError, match=r"^archive/corrections\.txt line 1"):
        parse_corrections("nonsense\n", source="archive/corrections.txt")


def test_vocabulary_file_error_is_a_value_error():
    assert issubclass(VocabularyFileError, ValueError)
```

`packages/engine/tests/test_vocabulary_file_cases.py`:

```python
import json
import re
from pathlib import Path

import pytest

from swarmscribe_engine.cli import read_corrections, read_terms

CASES = json.loads(
    (Path(__file__).parents[2] / "protocol" / "tests" / "vocabulary_file_cases.json").read_text(
        encoding="utf-8"
    )
)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_engine_cli_readers_match_the_shared_cases(case, tmp_path):
    name = "vocabulary.txt" if case["kind"] == "terms" else "corrections.txt"
    path = tmp_path / name
    path.write_bytes(case["text"].encode("utf-8"))
    if case["kind"] == "terms":
        assert list(read_terms(path)) == case["expect"]
        return
    if "error" in case:
        with pytest.raises(ValueError, match=re.escape(case["error"])):
            read_corrections(path)
        return
    assert [[c.heard, c.replacement] for c in read_corrections(path)] == case["expect"]
```

In `packages/protocol/tests/test_messages.py`:
- in `_claim()`, replace `source_checksum="a" * 64` with `source_version="etag-1"`;
- in the `SubmitRequest(...)` case, give `OutputChecksums` an extra `source="d" * 64` argument;
- in the `FailRequest(...)` case, add `code="undecodable"`;
- add `ErrorBody` and `FailureCode`-dependent tests below, and add `ErrorBody` to the import from `swarmscribe_protocol`.

```python
def test_claim_carries_a_source_version_and_no_checksum():
    assert _claim().source_version == "etag-1"
    assert "source_checksum" not in ClaimResponse.model_fields


@pytest.mark.parametrize("bad", ["A" * 64, "a" * 63, "a" * 65, "g" * 64, ""])
def test_checksums_must_be_lowercase_hex_sha256(bad):
    with pytest.raises(ValidationError):
        OutputChecksums(source=bad, txt="a" * 64, srt="a" * 64, segments_json="a" * 64)


def test_fail_request_code_must_be_known():
    with pytest.raises(ValidationError):
        FailRequest(lease_id="lease-1", code="exploded", reason="x", retryable=True)


def test_error_body_round_trips():
    body = ErrorBody(code="stale_lease", message="this job is not leased to you")
    assert ErrorBody.model_validate_json(body.model_dump_json()) == body
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest packages/protocol packages/engine/tests/test_vocabulary_file_cases.py -q`
Expected: collection errors — `ModuleNotFoundError: No module named 'swarmscribe_protocol.vocabulary_files'` and `ImportError: cannot import name 'ErrorBody'`. The engine shared-case test passes already (it tests existing code) — record that.

- [ ] **Step 4: Implement the protocol changes**

Append to `packages/protocol/src/swarmscribe_protocol/base.py`:

```python
from typing import Annotated

from pydantic import Field

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
"""Lowercase hex SHA-256, the only checksum format on the wire."""
```

(Move the new imports to the top of the file with the existing ones.)

In `segments.py`: import `Sha256` from `.base` and change `source_checksum: str` to `source_checksum: Sha256`.

In `messages.py`: import `Sha256` from `.base`; then

```python
FailureCode = Literal["source_changed", "undecodable", "engine_error", "out_of_resources", "other"]
```

directly after the `Directive` line, and change these models:

```python
class ClaimResponse(WireModel):
    job_id: str
    lease_id: str
    download_url: Link
    upload_urls: UploadUrls
    settings: JobSettings
    vocabulary: Vocabulary
    source_version: str = Field(min_length=1)


class OutputChecksums(WireModel):
    source: Sha256
    txt: Sha256
    srt: Sha256
    segments_json: Sha256


class FailRequest(WireModel):
    lease_id: str
    code: FailureCode
    reason: str
    retryable: bool


class ErrorBody(WireModel):
    """Body of every error response from the leader."""

    code: str
    message: str
```

`packages/protocol/src/swarmscribe_protocol/vocabulary_files.py`:

```python
"""Parse an operator's vocabulary.txt and corrections.txt.

The engine CLI keeps its own copy of these rules, because the engine imports nothing
internal; tests/vocabulary_file_cases.json is run against both to keep them identical.
"""

import re

from .vocabulary import Correction


class VocabularyFileError(ValueError):
    """A line in a vocabulary or corrections file breaks the rules."""


def _content_lines(text: str) -> list[tuple[int, str]]:
    if text.startswith("﻿"):
        text = text[1:]
    return [
        (number, line.strip())
        for number, line in enumerate(text.splitlines(), start=1)
        if line.strip() and not line.strip().startswith("#")
    ]


def parse_terms(text: str) -> list[str]:
    return [line for _, line in _content_lines(text)]


def parse_corrections(text: str, source: str = "corrections.txt") -> list[Correction]:
    corrections: list[Correction] = []
    defined: dict[str, tuple[int, str]] = {}  # normalised heard -> (line number, replacement)
    for number, line in _content_lines(text):
        heard, arrow, replacement = line.partition("=>")
        heard, replacement = heard.strip(), replacement.strip()
        if not arrow or not heard or not replacement:
            raise VocabularyFileError(f"{source} line {number}: expected 'heard as => should be'")
        if not all(re.search(r"\w", part) for part in heard.split()):
            raise VocabularyFileError(
                f"{source} line {number}: 'heard as' must contain a word in every part"
            )
        key = " ".join(heard.split()).casefold()
        if key in defined:
            first_line, first_replacement = defined[key]
            if replacement != first_replacement:
                raise VocabularyFileError(
                    f"{source} line {number}: '{' '.join(heard.split())}' "
                    f"is already defined on line {first_line}"
                )
            continue
        defined[key] = (number, replacement)
        corrections.append(Correction(heard=heard, replacement=replacement))
    return corrections
```

In `schema.py`: add `messages.ErrorBody,` to `MODELS` directly after `messages.ReleaseRequest,`.

In `__init__.py`: import `ErrorBody` and `FailureCode` from `.messages` and `Sha256` from `.base`; add all three to `__all__`, keeping it sorted.

- [ ] **Step 5: Regenerate the snapshot**

Run: `uv run python -m swarmscribe_protocol.schema packages/protocol/tests/schema_v1.json`
Confirm the file contains `"ErrorBody"`, `ClaimResponse` lists `source_version` and not `source_checksum`, and `OutputChecksums` has a `pattern` on each field.

- [ ] **Step 6: Run everything**

Run: `uv run pytest -q`, `uv run pytest -m smoke -q`, `uv run ruff check .`
Expected: all pass (the engine writes `hexdigest()` checksums, which satisfy the pattern).

- [ ] **Step 7: Commit**

```bash
git add packages/protocol packages/engine/tests/test_vocabulary_file_cases.py
git commit -m "Protocol: source versions, SHA-256 checksums, failure codes, shared vocabulary parser"
```

---

### Task 2: Leader package, configuration and the test database

**Files:**
- Modify: `pyproject.toml`, `.gitignore`, `.github/workflows/ci.yml`
- Create: `packages/leader/pyproject.toml`
- Create: `packages/leader/src/swarmscribe_leader/__init__.py`, `clock.py`, `config.py`, `db/__init__.py`, `db/session.py`
- Create: `packages/leader/tests/conftest.py`, `test_config.py`, `test_database.py`

**Interfaces:**
- Produces:
  - `swarmscribe_leader.LEADER_VERSION = "0.1.0"`
  - `swarmscribe_leader.clock.utcnow() -> datetime` (timezone-aware UTC)
  - `swarmscribe_leader.config.Settings` (pydantic-settings, prefix `SWARMSCRIBE_`) with fields: `database_url: str`, `public_url: str` (trailing `/` stripped), `link_key: str` (min 32 chars), `lease_seconds: int = 120`, `heartbeat_seconds: int = 30`, `max_attempts: int = 3`, `claim_retry_after: int = 10`, `reaper_interval_seconds: float = 15.0`, `scanner_interval_seconds: float = 30.0`, `follower_gone_after_seconds: int = 600`, `download_link_ttl_seconds: int = 1800`, `upload_link_ttl_seconds: int = 7200`. Rejects `heartbeat_seconds >= lease_seconds`.
  - `swarmscribe_leader.db.session`: `to_async_url(url: str) -> str`, `make_engine(url: str, **kwargs) -> AsyncEngine`, `make_sessionmaker(engine) -> async_sessionmaker[AsyncSession]` (`expire_on_commit=False`).
  - Test fixture `database_url` (session scope): a freshly created, empty database `swarmscribe_test` on `SWARMSCRIBE_TEST_DATABASE_URL` if set, otherwise on an embedded `pgserver` kept in `<repo>/.pgdata`.

- [ ] **Step 1: Workspace wiring**

`packages/leader/pyproject.toml`:

```toml
[project]
name = "swarmscribe-leader"
version = "0.1.0"
description = "SwarmScribe leader: catalogue, consent, jobs and the follower API"
requires-python = ">=3.11"
dependencies = [
    "swarmscribe-protocol",
    "fastapi>=0.115,<1",
    "uvicorn[standard]>=0.30,<1",
    "sqlalchemy[asyncio]>=2.0.30,<3",
    "asyncpg>=0.29,<1",
    "alembic>=1.13,<2",
    "pydantic-settings>=2.4,<3",
]

[project.scripts]
swarmscribe-leader = "swarmscribe_leader.main:run"

[tool.uv.sources]
swarmscribe-protocol = { workspace = true }

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/swarmscribe_leader"]
```

(The `swarmscribe_leader.main` module arrives in Task 11; the entry point is not used before then.)

In the root `pyproject.toml`:
- add `swarmscribe-leader = { workspace = true }` to `[tool.uv.sources]`;
- add `"swarmscribe-leader"`, `"pytest-asyncio>=0.24"`, `"httpx>=0.27"`, `"pgserver>=0.1"` to the `dev` dependency group;
- add to `[tool.pytest.ini_options]`: `asyncio_mode = "auto"` and `asyncio_default_fixture_loop_scope = "function"`;
- add to `[tool.ruff]`: `extend-exclude = ["packages/leader/src/swarmscribe_leader/db/migrations/versions"]`.

Append `.pgdata/` to `.gitignore`.

Replace `.github/workflows/ci.yml` with:

```yaml
name: ci

on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_PASSWORD: postgres
        ports:
          - 5432:5432
        options: >-
          --health-cmd pg_isready
          --health-interval 5s
          --health-timeout 5s
          --health-retries 10
    env:
      SWARMSCRIBE_TEST_DATABASE_URL: postgresql://postgres:postgres@localhost:5432/postgres
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync
      - run: uv run ruff check .
      - run: uv run pytest
```

- [ ] **Step 2: Write the failing tests**

`packages/leader/tests/conftest.py`:

```python
import asyncio
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest

TEST_DATABASE = "swarmscribe_test"
REPO_ROOT = Path(__file__).resolve().parents[3]


def _admin_url() -> str:
    url = os.environ.get("SWARMSCRIBE_TEST_DATABASE_URL")
    if url:
        return url
    import pgserver

    server = pgserver.get_server(str(REPO_ROOT / ".pgdata"), cleanup_mode="stop")
    return server.get_uri()


def _with_database(url: str, name: str) -> str:
    return urlunsplit(urlsplit(url)._replace(path="/" + name))


async def _recreate(admin_url: str, name: str) -> None:
    conn = await asyncpg.connect(admin_url)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def database_url() -> str:
    admin = _admin_url()
    asyncio.run(_recreate(admin, TEST_DATABASE))
    return _with_database(admin, TEST_DATABASE)
```

`packages/leader/tests/test_config.py`:

```python
import os

import pytest
from pydantic import ValidationError

from swarmscribe_leader.config import Settings
from swarmscribe_leader.db.session import to_async_url

BASE = {
    "database_url": "postgresql://u:p@db:5432/swarm",
    "public_url": "https://leader.example/",
    "link_key": "k" * 32,
}


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in list(os.environ):
        if name.startswith("SWARMSCRIBE_") and name != "SWARMSCRIBE_TEST_DATABASE_URL":
            monkeypatch.delenv(name)


def test_defaults():
    settings = Settings(**BASE)
    assert settings.lease_seconds == 120
    assert settings.heartbeat_seconds == 30
    assert settings.max_attempts == 3
    assert settings.claim_retry_after == 10
    assert settings.download_link_ttl_seconds == 1800
    assert settings.upload_link_ttl_seconds == 7200


def test_public_url_loses_its_trailing_slash():
    assert Settings(**BASE).public_url == "https://leader.example"


def test_reads_the_environment(monkeypatch):
    monkeypatch.setenv("SWARMSCRIBE_DATABASE_URL", "postgresql://x/y")
    monkeypatch.setenv("SWARMSCRIBE_PUBLIC_URL", "https://l")
    monkeypatch.setenv("SWARMSCRIBE_LINK_KEY", "z" * 40)
    monkeypatch.setenv("SWARMSCRIBE_LEASE_SECONDS", "300")
    settings = Settings()
    assert settings.database_url == "postgresql://x/y"
    assert settings.lease_seconds == 300


def test_database_url_is_required():
    with pytest.raises(ValidationError):
        Settings(public_url="https://l", link_key="k" * 32)


def test_link_key_must_be_at_least_32_characters():
    with pytest.raises(ValidationError):
        Settings(**{**BASE, "link_key": "short"})


def test_heartbeat_must_be_shorter_than_the_lease():
    with pytest.raises(ValidationError, match="heartbeat"):
        Settings(**BASE, lease_seconds=30, heartbeat_seconds=30)


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("postgresql://u:p@h/d", "postgresql+asyncpg://u:p@h/d"),
        ("postgres://u:p@h/d", "postgresql+asyncpg://u:p@h/d"),
        ("postgresql+asyncpg://u:p@h/d", "postgresql+asyncpg://u:p@h/d"),
    ],
)
def test_to_async_url(given, expected):
    assert to_async_url(given) == expected
```

`packages/leader/tests/test_database.py`:

```python
from sqlalchemy import text

from swarmscribe_leader.clock import utcnow
from swarmscribe_leader.db.session import make_engine


async def test_the_test_database_is_reachable(database_url):
    engine = make_engine(database_url)
    try:
        async with engine.connect() as conn:
            assert await conn.scalar(text("select 1")) == 1
            assert await conn.scalar(text("select current_database()")) == "swarmscribe_test"
    finally:
        await engine.dispose()


def test_utcnow_is_timezone_aware():
    assert utcnow().utcoffset().total_seconds() == 0
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv sync` then `uv run pytest packages/leader -q`
Expected: `ModuleNotFoundError: No module named 'swarmscribe_leader'`. (`uv sync` installs FastAPI, SQLAlchemy, asyncpg, pgserver and the rest; allow a few minutes.)

- [ ] **Step 4: Implement**

`packages/leader/src/swarmscribe_leader/__init__.py`:

```python
LEADER_VERSION = "0.1.0"
```

`clock.py`:

```python
from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)
```

`config.py`:

```python
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Leader configuration, read from SWARMSCRIBE_* environment variables."""

    model_config = SettingsConfigDict(env_prefix="SWARMSCRIBE_", extra="ignore")

    database_url: str
    public_url: str
    link_key: str = Field(min_length=32)

    lease_seconds: int = Field(default=120, gt=0)
    heartbeat_seconds: int = Field(default=30, gt=0)
    max_attempts: int = Field(default=3, gt=0)
    claim_retry_after: int = Field(default=10, gt=0)
    reaper_interval_seconds: float = Field(default=15.0, gt=0)
    scanner_interval_seconds: float = Field(default=30.0, gt=0)
    follower_gone_after_seconds: int = Field(default=600, gt=0)
    download_link_ttl_seconds: int = Field(default=1800, gt=0)
    upload_link_ttl_seconds: int = Field(default=7200, gt=0)

    @field_validator("public_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @model_validator(mode="after")
    def _heartbeat_inside_lease(self) -> "Settings":
        if self.heartbeat_seconds >= self.lease_seconds:
            raise ValueError("heartbeat_seconds must be shorter than lease_seconds")
        return self
```

`db/__init__.py`: empty.

`db/session.py`:

```python
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


def to_async_url(url: str) -> str:
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix) :]
    return url


def make_engine(url: str, **kwargs) -> AsyncEngine:
    return create_async_engine(to_async_url(url), pool_pre_ping=True, **kwargs)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
```

- [ ] **Step 5: Run the tests**

Run: `uv sync --reinstall-package swarmscribe-leader` (uv installs an empty package if it was synced before the source existed), then `uv run pytest packages/leader -q` (the first run initialises `.pgdata`; allow a minute), then `uv run pytest -q` and `uv run ruff check .`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock .gitignore .github packages/leader
git commit -m "Leader: package, configuration and a real Postgres for tests"
```

---

### Task 3: Schema, migrations and seeded settings profiles

**Files:**
- Create: `packages/leader/src/swarmscribe_leader/db/models.py`, `db/migrate.py`
- Create: `packages/leader/src/swarmscribe_leader/db/migrations/env.py`, `script.py.mako`, `versions/0001_initial_schema.py` (generated)
- Modify: `packages/leader/tests/conftest.py`
- Test: `packages/leader/tests/test_migrations.py`

**Interfaces:**
- Produces: ORM models in `swarmscribe_leader.db.models` — `Base`, `StorageLocation`, `Recording`, `Follower`, `JoinToken`, `SettingsProfile`, `Job`, `JobAttempt`, `JobResult`, `VocabularyVersion` (table `vocabularies`), `AuditEntry` (table `audit_log`). Columns exactly as in the code below (spec section 4, plus `storage_locations.pool` and `storage_locations.required_device`, which give each job its pool and device requirement).
- `swarmscribe_leader.db.migrate`: `alembic_config(database_url=None)`, `upgrade(database_url)`, `autogenerate(database_url, message, rev_id)`, `head_revision() -> str`, `async current_revision(engine) -> str | None`.
- Test fixtures: `migrated_database_url` (session), `engine` (function; truncates every table except `alembic_version` and `settings_profiles` before the test), `sessionmaker` (function).
- Two settings profiles are seeded by the migration: `cuda` (`large-v3`, `float16`) and `cpu` (`distil-large-v3`, `int8`), both with temperatures `[0.0, 0.2, 0.4]`.

- [ ] **Step 1: Write the models**

`packages/leader/src/swarmscribe_leader/db/models.py`:

```python
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map = {
        datetime: DateTime(timezone=True),
        dict[str, Any]: JSONB,
        list[Any]: JSONB,
    }


class _Row:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class StorageLocation(_Row, Base):
    __tablename__ = "storage_locations"

    name: Mapped[str] = mapped_column(String(200), unique=True)
    backend: Mapped[str] = mapped_column(String(16))
    config: Mapped[dict[str, Any]] = mapped_column(default=dict)
    secret_ref: Mapped[str | None] = mapped_column(String(200))
    input_prefix: Mapped[str] = mapped_column(Text, default="")
    output_location_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("storage_locations.id")
    )
    output_prefix: Mapped[str] = mapped_column(Text, default="transcripts/")
    pool: Mapped[str] = mapped_column(String(100), default="default")
    required_device: Mapped[str] = mapped_column(String(8), default="any")
    scan_interval_s: Mapped[int] = mapped_column(default=900)
    enabled: Mapped[bool] = mapped_column(default=True)
    vocabulary_version: Mapped[int] = mapped_column(default=0)
    vocabulary_hash: Mapped[str | None] = mapped_column(String(64))
    last_scan_at: Mapped[datetime | None]
    last_scan_error: Mapped[str | None] = mapped_column(Text)


class Recording(_Row, Base):
    __tablename__ = "recordings"
    __table_args__ = (UniqueConstraint("location_id", "key"),)

    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("storage_locations.id"))
    key: Mapped[str] = mapped_column(Text)
    size: Mapped[int] = mapped_column(BigInteger)
    source_version: Mapped[str] = mapped_column(String(200))
    consent: Mapped[str] = mapped_column(String(16))
    consent_pattern: Mapped[str | None] = mapped_column(Text)
    first_seen_at: Mapped[datetime]
    last_seen_at: Mapped[datetime]
    missing: Mapped[bool] = mapped_column(default=False)


class JoinToken(_Row, Base):
    __tablename__ = "join_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    pool: Mapped[str] = mapped_column(String(100))
    expires_at: Mapped[datetime]
    max_uses: Mapped[int]
    uses: Mapped[int] = mapped_column(default=0)
    revoked: Mapped[bool] = mapped_column(default=False)
    created_by: Mapped[str] = mapped_column(Text)


class Follower(_Row, Base):
    __tablename__ = "followers"

    pool: Mapped[str] = mapped_column(String(100))
    capabilities: Mapped[dict[str, Any]] = mapped_column(default=dict)
    credential_hash: Mapped[str] = mapped_column(String(64), unique=True)
    state: Mapped[str] = mapped_column(String(16), default="active")
    last_seen_at: Mapped[datetime]
    join_token_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("join_tokens.id"))


class SettingsProfile(_Row, Base):
    __tablename__ = "settings_profiles"

    name: Mapped[str] = mapped_column(String(100), unique=True)
    device: Mapped[str] = mapped_column(String(8))
    model: Mapped[str] = mapped_column(String(100))
    compute_type: Mapped[str] = mapped_column(String(32))
    temperatures: Mapped[list[Any]]


class Job(_Row, Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_claim", "state", "pool", "priority", "created_at"),)

    recording_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recordings.id"))
    source_version: Mapped[str] = mapped_column(String(200))
    state: Mapped[str] = mapped_column(String(16), default="queued")
    pool: Mapped[str] = mapped_column(String(100))
    required_device: Mapped[str] = mapped_column(String(8), default="any")
    priority: Mapped[int] = mapped_column(default=0)
    attempts: Mapped[int] = mapped_column(default=0)
    max_attempts: Mapped[int]
    lease_id: Mapped[uuid.UUID | None]
    leased_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("followers.id"))
    lease_expires_at: Mapped[datetime | None]
    vocabulary_version: Mapped[int | None]
    settings_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("settings_profiles.id")
    )
    failure_reason: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None]
    outputs_flagged_for_deletion: Mapped[bool] = mapped_column(default=False)


class JobAttempt(_Row, Base):
    __tablename__ = "job_attempts"
    __table_args__ = (Index("ix_job_attempts_lease", "job_id", "lease_id"),)

    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"))
    follower_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("followers.id"))
    lease_id: Mapped[uuid.UUID]
    started_at: Mapped[datetime]
    ended_at: Mapped[datetime | None]
    outcome: Mapped[str | None] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(Text)


class JobResult(_Row, Base):
    __tablename__ = "job_results"

    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"), unique=True)
    source_sha256: Mapped[str] = mapped_column(String(64))
    txt_sha256: Mapped[str] = mapped_column(String(64))
    srt_sha256: Mapped[str] = mapped_column(String(64))
    segments_sha256: Mapped[str] = mapped_column(String(64))
    duration_s: Mapped[float | None]
    engine_version: Mapped[str | None] = mapped_column(String(50))
    vocabulary_terms_used: Mapped[list[Any]] = mapped_column(default=list)
    corrections_applied: Mapped[list[Any]] = mapped_column(default=list)
    low_confidence_words: Mapped[list[Any]] = mapped_column(default=list)


class VocabularyVersion(_Row, Base):
    __tablename__ = "vocabularies"
    __table_args__ = (UniqueConstraint("scope", "version"),)

    scope: Mapped[str] = mapped_column(String(64))
    version: Mapped[int]
    terms: Mapped[list[Any]] = mapped_column(default=list)
    corrections: Mapped[list[Any]] = mapped_column(default=list)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(Text)


class AuditEntry(_Row, Base):
    __tablename__ = "audit_log"

    actor: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(String(100))
    subject_type: Mapped[str | None] = mapped_column(String(50))
    subject_id: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict[str, Any]] = mapped_column(default=dict)
```

- [ ] **Step 2: Write the migration machinery**

`packages/leader/src/swarmscribe_leader/db/migrate.py`:

```python
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from .session import to_async_url

MIGRATIONS = Path(__file__).parent / "migrations"


def alembic_config(database_url: str | None = None) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    if database_url is not None:
        config.set_main_option("sqlalchemy.url", to_async_url(database_url).replace("%", "%%"))
    return config


def upgrade(database_url: str) -> None:
    command.upgrade(alembic_config(database_url), "head")


def autogenerate(database_url: str, message: str, rev_id: str) -> None:
    """Developer tool: write a new revision from the difference between models and database."""
    command.revision(alembic_config(database_url), message=message, autogenerate=True, rev_id=rev_id)


def head_revision() -> str:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


async def current_revision(engine: AsyncEngine) -> str | None:
    async with engine.connect() as conn:
        try:
            return await conn.scalar(text("select version_num from alembic_version"))
        except DBAPIError:
            return None
```

`packages/leader/src/swarmscribe_leader/db/migrations/env.py`:

```python
import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from swarmscribe_leader.db.models import Base

config = context.config
target_metadata = Base.metadata


def _run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def _main() -> None:
    engine = create_async_engine(config.get_main_option("sqlalchemy.url"))
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run_migrations)
            await connection.commit()
    finally:
        await engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("offline migrations are not supported")
asyncio.run(_main())
```

`packages/leader/src/swarmscribe_leader/db/migrations/script.py.mako`:

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

Create the empty directory `packages/leader/src/swarmscribe_leader/db/migrations/versions/` (add an empty `.gitkeep` only if git would otherwise drop it; the generated revision will live there).

- [ ] **Step 3: Generate the initial revision**

Run this from the repo root (it creates an empty scratch database, then asks Alembic to write the revision from the models):

```bash
uv run python -c "import asyncio, sys; sys.path.insert(0, 'packages/leader/tests'); import conftest as c; admin = c._admin_url(); asyncio.run(c._recreate(admin, 'swarmscribe_autogen')); from swarmscribe_leader.db.migrate import autogenerate; autogenerate(c._with_database(admin, 'swarmscribe_autogen'), 'initial schema', '0001')"
```

Expected: a new file `packages/leader/src/swarmscribe_leader/db/migrations/versions/0001_initial_schema.py` with `create_table` calls for all ten tables and the two indexes. Read it and confirm every table from Step 1 is there.

Then add the seed to the end of its `upgrade()` function (after the last `create_index`/`create_table`), and add `import uuid` and `from sqlalchemy.dialects import postgresql` to its imports if not already present:

```python
    profiles = sa.table(
        "settings_profiles",
        sa.column("id", sa.Uuid),
        sa.column("name", sa.String),
        sa.column("device", sa.String),
        sa.column("model", sa.String),
        sa.column("compute_type", sa.String),
        sa.column("temperatures", postgresql.JSONB),
    )
    op.bulk_insert(
        profiles,
        [
            {
                "id": uuid.UUID("5e7f2a3c-0d1b-4c5e-9f00-000000000001"),
                "name": "cuda",
                "device": "cuda",
                "model": "large-v3",
                "compute_type": "float16",
                "temperatures": [0.0, 0.2, 0.4],
            },
            {
                "id": uuid.UUID("5e7f2a3c-0d1b-4c5e-9f00-000000000002"),
                "name": "cpu",
                "device": "cpu",
                "model": "distil-large-v3",
                "compute_type": "int8",
                "temperatures": [0.0, 0.2, 0.4],
            },
        ],
    )
```

- [ ] **Step 4: Write the failing tests and fixtures**

Append to `packages/leader/tests/conftest.py`:

```python
from sqlalchemy import text

from swarmscribe_leader.db.migrate import upgrade
from swarmscribe_leader.db.models import Base
from swarmscribe_leader.db.session import make_engine, make_sessionmaker

KEEP_TABLES = {"alembic_version", "settings_profiles"}


@pytest.fixture(scope="session")
def migrated_database_url(database_url) -> str:
    upgrade(database_url)
    return database_url


@pytest.fixture
async def engine(migrated_database_url):
    engine = make_engine(migrated_database_url)
    tables = [t.name for t in reversed(Base.metadata.sorted_tables) if t.name not in KEEP_TABLES]
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    yield engine
    await engine.dispose()


@pytest.fixture
def sessionmaker(engine):
    return make_sessionmaker(engine)
```

(Move the new imports to the top of the file.)

`packages/leader/tests/test_migrations.py`:

```python
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import select

from swarmscribe_leader.db.migrate import current_revision, head_revision
from swarmscribe_leader.db.models import Base, SettingsProfile


async def test_migrations_produce_exactly_the_models(engine):
    async with engine.connect() as conn:
        diff = await conn.run_sync(
            lambda sync_conn: compare_metadata(MigrationContext.configure(sync_conn), Base.metadata)
        )
    assert diff == []


async def test_database_is_at_the_head_revision(engine):
    assert head_revision() == "0001"
    assert await current_revision(engine) == "0001"


async def test_default_settings_profiles_are_seeded(sessionmaker):
    async with sessionmaker() as session:
        profiles = (await session.scalars(select(SettingsProfile))).all()
    found = {p.name: (p.device, p.model, p.compute_type, p.temperatures) for p in profiles}
    assert found == {
        "cuda": ("cuda", "large-v3", "float16", [0.0, 0.2, 0.4]),
        "cpu": ("cpu", "distil-large-v3", "int8", [0.0, 0.2, 0.4]),
    }
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest packages/leader -q`, then `uv run pytest -q` and `uv run ruff check .`
Expected: all pass. If `test_migrations_produce_exactly_the_models` reports a difference, the generated revision and the models disagree: fix the cause (never edit the test), and report what it was.

- [ ] **Step 6: Commit**

```bash
git add packages/leader
git commit -m "Leader: schema, Alembic migrations and seeded settings profiles"
```

---

### Task 4: Errors, audit log and follower authentication

**Files:**
- Create: `packages/leader/src/swarmscribe_leader/errors.py`, `audit.py`
- Create: `packages/leader/src/swarmscribe_leader/auth/__init__.py`, `auth/secrets.py`, `auth/followers.py`
- Modify: `packages/leader/tests/conftest.py` (add the `Factory` fixture)
- Test: `packages/leader/tests/test_follower_auth.py`

**Interfaces:**
- `swarmscribe_leader.errors`: `LeaderError(message, *, code=None)` with class attributes `status` and `code`; subclasses `Unauthorized` (401, `unauthorized`), `Forbidden` (403, `forbidden`), `NotFound` (404, `not_found`), `Conflict` (409, `conflict`), `StaleLease(Conflict)` (`stale_lease`), `PreconditionFailed` (412, `source_changed`), `PayloadTooLarge` (413, `too_large`).
- `swarmscribe_leader.audit.record(session, *, actor, action, subject_type=None, subject_id=None, detail=None) -> None`
- `swarmscribe_leader.auth.secrets`: `new_secret() -> str`, `hash_secret(secret: str) -> str`
- `swarmscribe_leader.auth.followers`:
  - `async create_join_token(session, *, pool, expires_at, max_uses, created_by) -> tuple[JoinToken, str]` (plaintext returned once)
  - `async register(session, request: RegisterRequest, *, now) -> tuple[Follower, str]` (credential returned once)
  - `async authenticate(session, credential: str, *, now) -> Follower`
- Test fixture `factory` (`Factory`): `location(**overrides)`, `recording(location=None, *, key=..., **overrides)`, `job(recording=None, **overrides)`, `follower(*, pool="default", device="cpu", state="active", last_seen_at=None) -> tuple[Follower, str]`.

- [ ] **Step 1: Add the test factory**

Append to `packages/leader/tests/conftest.py` (imports to the top):

```python
import uuid

from swarmscribe_leader.auth.secrets import hash_secret, new_secret
from swarmscribe_leader.clock import utcnow
from swarmscribe_leader.db.models import Follower, Job, Recording, StorageLocation


class Factory:
    """Creates committed rows for tests. Each method uses its own session."""

    def __init__(self, sessionmaker, root: Path):
        self.sessionmaker = sessionmaker
        self.root = root

    async def _save(self, row):
        async with self.sessionmaker() as session:
            session.add(row)
            await session.commit()
        return row

    async def location(self, **overrides) -> StorageLocation:
        values = {
            "id": uuid.uuid4(),
            "name": f"location-{uuid.uuid4().hex[:8]}",
            "backend": "local",
            "config": {"root": str(self.root)},
            "input_prefix": "",
            "output_prefix": "transcripts/",
            "pool": "default",
            "required_device": "any",
            "scan_interval_s": 900,
            "enabled": True,
            "vocabulary_version": 0,
        }
        values.update(overrides)
        return await self._save(StorageLocation(**values))

    async def recording(self, location=None, *, key="talks/one.mp3", **overrides) -> Recording:
        location = location or await self.location()
        now = utcnow()
        values = {
            "id": uuid.uuid4(),
            "location_id": location.id,
            "key": key,
            "size": 10,
            "source_version": "10-1",
            "consent": "consented",
            "first_seen_at": now,
            "last_seen_at": now,
            "missing": False,
        }
        values.update(overrides)
        return await self._save(Recording(**values))

    async def job(self, recording=None, **overrides) -> Job:
        recording = recording or await self.recording()
        values = {
            "id": uuid.uuid4(),
            "recording_id": recording.id,
            "source_version": recording.source_version,
            "state": "queued",
            "pool": "default",
            "required_device": "any",
            "priority": 0,
            "attempts": 0,
            "max_attempts": 3,
        }
        values.update(overrides)
        return await self._save(Job(**values))

    async def follower(
        self, *, pool="default", device="cpu", state="active", last_seen_at=None
    ) -> tuple[Follower, str]:
        credential = new_secret()
        follower = Follower(
            id=uuid.uuid4(),
            pool=pool,
            capabilities={"device": device, "models": [], "engine_version": "0.1.0", "pool": pool},
            credential_hash=hash_secret(credential),
            state=state,
            last_seen_at=last_seen_at or utcnow(),
        )
        await self._save(follower)
        return follower, credential


@pytest.fixture
def factory(sessionmaker, tmp_path):
    return Factory(sessionmaker, tmp_path)
```

- [ ] **Step 2: Write the failing tests**

`packages/leader/tests/test_follower_auth.py`:

```python
from datetime import timedelta

import pytest
from sqlalchemy import select

from swarmscribe_leader.auth.followers import authenticate, create_join_token, register
from swarmscribe_leader.auth.secrets import hash_secret, new_secret
from swarmscribe_leader.clock import utcnow
from swarmscribe_leader.db.models import AuditEntry, Follower, JoinToken
from swarmscribe_leader.errors import Conflict, Forbidden, Unauthorized
from swarmscribe_protocol import Capabilities, RegisterRequest


def request(token: str, *, protocol_version: int = 1) -> RegisterRequest:
    return RegisterRequest(
        join_token=token,
        protocol_version=protocol_version,
        capabilities=Capabilities(
            device="cpu", models=["distil-large-v3"], engine_version="0.1.0", pool="ignored"
        ),
    )


async def make_token(sessionmaker, **overrides) -> str:
    values = {
        "pool": "gpu",
        "expires_at": utcnow() + timedelta(days=1),
        "max_uses": 5,
        "created_by": "test",
    }
    values.update(overrides)
    async with sessionmaker() as session:
        _, plaintext = await create_join_token(session, **values)
        await session.commit()
    return plaintext


def test_secrets_are_long_random_and_hashed_with_sha256():
    first, second = new_secret(), new_secret()
    assert first != second
    assert len(first) >= 43
    assert len(hash_secret(first)) == 64
    assert hash_secret(first) == hash_secret(first)


async def test_join_token_is_stored_only_as_a_hash(sessionmaker):
    plaintext = await make_token(sessionmaker)
    async with sessionmaker() as session:
        stored = (await session.scalars(select(JoinToken))).one()
    assert stored.token_hash == hash_secret(plaintext)
    assert plaintext not in stored.token_hash


async def test_register_creates_a_follower_in_the_token_pool(sessionmaker):
    plaintext = await make_token(sessionmaker, pool="gpu")
    async with sessionmaker() as session:
        follower, credential = await register(session, request(plaintext), now=utcnow())
        await session.commit()
    async with sessionmaker() as session:
        stored = await session.get(Follower, follower.id)
        token = (await session.scalars(select(JoinToken))).one()
        audit = (await session.scalars(select(AuditEntry))).all()
    assert stored.pool == "gpu"
    assert stored.state == "active"
    assert stored.capabilities["device"] == "cpu"
    assert stored.credential_hash == hash_secret(credential)
    assert token.uses == 1
    assert [a.action for a in audit] == ["follower.register"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"revoked": True},
        {"expires_at": utcnow() - timedelta(seconds=1)},
        {"max_uses": 0},
    ],
    ids=["revoked", "expired", "used-up"],
)
async def test_register_rejects_an_unusable_token(sessionmaker, overrides):
    revoked = overrides.pop("revoked", False)
    plaintext = await make_token(sessionmaker, **overrides)
    if revoked:
        async with sessionmaker() as session:
            token = (await session.scalars(select(JoinToken))).one()
            token.revoked = True
            await session.commit()
    async with sessionmaker() as session:
        with pytest.raises(Unauthorized):
            await register(session, request(plaintext), now=utcnow())


async def test_register_rejects_an_unknown_token(sessionmaker):
    async with sessionmaker() as session:
        with pytest.raises(Unauthorized):
            await register(session, request("not-a-token"), now=utcnow())


async def test_a_single_use_token_works_once(sessionmaker):
    plaintext = await make_token(sessionmaker, max_uses=1)
    async with sessionmaker() as session:
        await register(session, request(plaintext), now=utcnow())
        await session.commit()
    async with sessionmaker() as session:
        with pytest.raises(Unauthorized):
            await register(session, request(plaintext), now=utcnow())


async def test_a_protocol_mismatch_is_rejected_without_using_the_token(sessionmaker):
    plaintext = await make_token(sessionmaker)
    async with sessionmaker() as session:
        with pytest.raises(Conflict, match="protocol"):
            await register(session, request(plaintext, protocol_version=2), now=utcnow())
        await session.commit()
    async with sessionmaker() as session:
        assert (await session.scalars(select(JoinToken))).one().uses == 0


async def test_authenticate_finds_the_follower_and_marks_it_seen(sessionmaker, factory):
    follower, credential = await factory.follower(last_seen_at=utcnow() - timedelta(hours=1))
    now = utcnow()
    async with sessionmaker() as session:
        found = await authenticate(session, credential, now=now)
        await session.commit()
    assert found.id == follower.id
    async with sessionmaker() as session:
        assert (await session.get(Follower, follower.id)).last_seen_at == now


async def test_authenticate_rejects_an_unknown_credential(sessionmaker):
    async with sessionmaker() as session:
        with pytest.raises(Unauthorized):
            await authenticate(session, "nope", now=utcnow())


async def test_authenticate_refuses_a_revoked_follower(sessionmaker, factory):
    _, credential = await factory.follower(state="revoked")
    async with sessionmaker() as session:
        with pytest.raises(Forbidden):
            await authenticate(session, credential, now=utcnow())


async def test_a_gone_follower_that_returns_is_active_again(sessionmaker, factory):
    _, credential = await factory.follower(state="gone")
    async with sessionmaker() as session:
        found = await authenticate(session, credential, now=utcnow())
    assert found.state == "active"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest packages/leader/tests/test_follower_auth.py -q`
Expected: `ModuleNotFoundError: No module named 'swarmscribe_leader.auth'` (the conftest import fails first; record it).

- [ ] **Step 4: Implement**

`errors.py`:

```python
class LeaderError(Exception):
    """An expected failure with an HTTP status and a stable error code."""

    status = 400
    code = "bad_request"

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code


class Unauthorized(LeaderError):
    status = 401
    code = "unauthorized"


class Forbidden(LeaderError):
    status = 403
    code = "forbidden"


class NotFound(LeaderError):
    status = 404
    code = "not_found"


class Conflict(LeaderError):
    status = 409
    code = "conflict"


class StaleLease(Conflict):
    code = "stale_lease"


class PreconditionFailed(LeaderError):
    status = 412
    code = "source_changed"


class PayloadTooLarge(LeaderError):
    status = 413
    code = "too_large"
```

`audit.py`:

```python
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .db.models import AuditEntry


def record(
    session: AsyncSession,
    *,
    actor: str,
    action: str,
    subject_type: str | None = None,
    subject_id: object | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Add an audit entry to the session; it commits with the change it describes."""
    session.add(
        AuditEntry(
            actor=actor,
            action=action,
            subject_type=subject_type,
            subject_id=None if subject_id is None else str(subject_id),
            detail=detail or {},
        )
    )
```

`auth/__init__.py`: empty.

`auth/secrets.py`:

```python
import hashlib
import secrets


def new_secret() -> str:
    """32 random bytes, URL-safe base64. Shown once; only its hash is stored."""
    return secrets.token_urlsafe(32)


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()
```

`auth/followers.py`:

```python
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from swarmscribe_protocol import PROTOCOL_VERSION, RegisterRequest

from .. import audit
from ..db.models import Follower, JoinToken
from ..errors import Conflict, Forbidden, Unauthorized
from .secrets import hash_secret, new_secret


async def create_join_token(
    session: AsyncSession, *, pool: str, expires_at: datetime, max_uses: int, created_by: str
) -> tuple[JoinToken, str]:
    plaintext = new_secret()
    token = JoinToken(
        id=uuid.uuid4(),
        token_hash=hash_secret(plaintext),
        pool=pool,
        expires_at=expires_at,
        max_uses=max_uses,
        uses=0,
        revoked=False,
        created_by=created_by,
    )
    session.add(token)
    audit.record(
        session,
        actor=created_by,
        action="token.create",
        subject_type="join_token",
        subject_id=token.id,
        detail={"pool": pool, "max_uses": max_uses},
    )
    return token, plaintext


async def register(
    session: AsyncSession, request: RegisterRequest, *, now: datetime
) -> tuple[Follower, str]:
    if request.protocol_version != PROTOCOL_VERSION:
        raise Conflict(
            f"protocol version {request.protocol_version} is not supported; "
            f"this leader speaks {PROTOCOL_VERSION}",
            code="protocol_version",
        )
    token = await session.scalar(
        select(JoinToken)
        .where(JoinToken.token_hash == hash_secret(request.join_token))
        .with_for_update()
    )
    if token is None or token.revoked or token.expires_at <= now or token.uses >= token.max_uses:
        raise Unauthorized("the join token is not valid")
    token.uses += 1
    credential = new_secret()
    follower = Follower(
        id=uuid.uuid4(),
        pool=token.pool,
        capabilities=request.capabilities.model_dump(),
        credential_hash=hash_secret(credential),
        state="active",
        last_seen_at=now,
        join_token_id=token.id,
    )
    session.add(follower)
    audit.record(
        session,
        actor=f"follower:{follower.id}",
        action="follower.register",
        subject_type="follower",
        subject_id=follower.id,
        detail={"pool": token.pool, "device": request.capabilities.device},
    )
    return follower, credential


async def authenticate(session: AsyncSession, credential: str, *, now: datetime) -> Follower:
    follower = await session.scalar(
        select(Follower).where(Follower.credential_hash == hash_secret(credential))
    )
    if follower is None:
        raise Unauthorized("unknown follower credential")
    if follower.state == "revoked":
        raise Forbidden("this follower has been revoked")
    if follower.state == "gone":
        follower.state = "active"
    follower.last_seen_at = now
    return follower
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest packages/leader -q`, `uv run pytest -q`, `uv run ruff check .`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add packages/leader
git commit -m "Leader: errors, audit log and follower join tokens"
```

---

### Task 5: The job store

**Files:**
- Create: `packages/leader/src/swarmscribe_leader/jobs/__init__.py`, `jobs/store.py`
- Test: `packages/leader/tests/test_job_store.py`

**Interfaces:**
- `swarmscribe_leader.jobs.store`:
  - `OPEN_STATES = ("queued", "leased")`, `NON_RETRYABLE = frozenset({"source_changed", "undecodable"})`
  - `async claim(session, follower, *, now, lease_seconds) -> Job | None`
  - `async heartbeat(session, job_id, lease_id: str, follower, *, now, lease_seconds) -> Directive`
  - `async submit(session, job_id, request: SubmitRequest, follower, *, now, outputs_present: Callable[[Job], Awaitable[bool]]) -> None`
  - `async fail(session, job_id, request: FailRequest, follower, *, now) -> None`
  - `async release(session, job_id, lease_id: str, follower, *, now) -> None`
  - `async cancel(session, job, *, now, reason) -> None` — the caller has loaded (and locked) `job`
  - `async release_all(session, follower, *, now) -> int`
  - `async close_attempt(session, job, outcome, reason, now) -> None`, `clear_lease(job) -> None` (shared with the reaper)
- Every function leaves committing to the caller.
- Lease rules: a call carrying a lease is accepted only if the job is `leased`, its `lease_id` equals the given one, and `leased_by` is the calling follower; otherwise `StaleLease`. A completed job keeps its `lease_id` (for idempotent submit); a cancelled job keeps its `lease_id` (so the holder's heartbeat gets `cancel`).

- [ ] **Step 1: Write the failing tests**

`packages/leader/tests/test_job_store.py`:

```python
import asyncio
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from swarmscribe_leader.clock import utcnow
from swarmscribe_leader.db.models import Job, JobAttempt, JobResult
from swarmscribe_leader.errors import Conflict, NotFound, StaleLease
from swarmscribe_leader.jobs import store
from swarmscribe_protocol import FailRequest, OutputChecksums, SubmitRequest

H = "a" * 64


async def present(_job):
    return True


async def absent(_job):
    return False


def submission(lease_id, checksum=H) -> SubmitRequest:
    return SubmitRequest(
        lease_id=str(lease_id),
        checksums=OutputChecksums(source=checksum, txt=checksum, srt=checksum, segments_json=checksum),
    )


async def claim(sessionmaker, follower, *, now=None):
    async with sessionmaker() as session:
        job = await store.claim(session, follower, now=now or utcnow(), lease_seconds=120)
        await session.commit()
    return job


async def load(sessionmaker, job_id) -> Job:
    async with sessionmaker() as session:
        return await session.get(Job, job_id)


async def attempts(sessionmaker, job_id) -> list[JobAttempt]:
    async with sessionmaker() as session:
        return list(
            (
                await session.scalars(
                    select(JobAttempt).where(JobAttempt.job_id == job_id).order_by(JobAttempt.started_at)
                )
            ).all()
        )


# --- claim ---------------------------------------------------------------------


async def test_claim_returns_none_when_nothing_is_queued(sessionmaker, factory):
    follower, _ = await factory.follower()
    assert await claim(sessionmaker, follower) is None


async def test_claim_leases_the_job_and_opens_an_attempt(sessionmaker, factory):
    job = await factory.job()
    follower, _ = await factory.follower()
    now = utcnow()
    claimed = await claim(sessionmaker, follower, now=now)
    stored = await load(sessionmaker, job.id)
    assert claimed.id == job.id
    assert stored.state == "leased"
    assert stored.leased_by == follower.id
    assert stored.lease_id is not None
    assert stored.attempts == 1
    assert stored.lease_expires_at == now + timedelta(seconds=120)
    (attempt,) = await attempts(sessionmaker, job.id)
    assert (attempt.follower_id, attempt.lease_id, attempt.ended_at) == (
        follower.id,
        stored.lease_id,
        None,
    )


async def test_claim_matches_pool_and_device(sessionmaker, factory):
    location = await factory.location()
    cuda_only = await factory.job(await factory.recording(location, key="a.mp3"), required_device="cuda")
    other_pool = await factory.job(await factory.recording(location, key="b.mp3"), pool="elsewhere")
    cpu_follower, _ = await factory.follower(device="cpu")
    assert await claim(sessionmaker, cpu_follower) is None
    cuda_follower, _ = await factory.follower(device="cuda")
    assert (await claim(sessionmaker, cuda_follower)).id == cuda_only.id
    elsewhere, _ = await factory.follower(pool="elsewhere")
    assert (await claim(sessionmaker, elsewhere)).id == other_pool.id


async def test_claim_takes_priority_first_then_oldest(sessionmaker, factory):
    location = await factory.location()
    first = await factory.job(await factory.recording(location, key="1.mp3"))
    second = await factory.job(await factory.recording(location, key="2.mp3"))
    urgent = await factory.job(await factory.recording(location, key="3.mp3"), priority=5)
    follower, _ = await factory.follower()
    order = [(await claim(sessionmaker, follower)).id for _ in range(3)]
    assert order == [urgent.id, first.id, second.id]


async def test_concurrent_claims_never_share_a_job(sessionmaker, factory):
    location = await factory.location()
    for i in range(20):
        await factory.job(await factory.recording(location, key=f"talks/{i:02d}.mp3"))
    followers = [(await factory.follower())[0] for _ in range(50)]

    async def one(follower):
        job = await claim(sessionmaker, follower)
        return None if job is None else job.id

    claimed = [job_id for job_id in await asyncio.gather(*(one(f) for f in followers)) if job_id]
    assert len(claimed) == 20
    assert len(set(claimed)) == 20


# --- heartbeat -----------------------------------------------------------------


async def test_heartbeat_extends_the_lease(sessionmaker, factory):
    job = await factory.job()
    follower, _ = await factory.follower()
    claimed = await claim(sessionmaker, follower)
    later = utcnow() + timedelta(seconds=60)
    async with sessionmaker() as session:
        directive = await store.heartbeat(
            session, job.id, str(claimed.lease_id), follower, now=later, lease_seconds=120
        )
        await session.commit()
    assert directive == "continue"
    assert (await load(sessionmaker, job.id)).lease_expires_at == later + timedelta(seconds=120)


@pytest.mark.parametrize("who", ["wrong-lease", "other-follower", "not-a-uuid"])
async def test_heartbeat_with_a_stale_lease_is_refused(sessionmaker, factory, who):
    job = await factory.job()
    follower, _ = await factory.follower()
    other, _ = await factory.follower()
    claimed = await claim(sessionmaker, follower)
    lease = {"wrong-lease": str(uuid.uuid4()), "other-follower": str(claimed.lease_id), "not-a-uuid": "x"}[who]
    caller = other if who == "other-follower" else follower
    async with sessionmaker() as session:
        with pytest.raises(StaleLease):
            await store.heartbeat(session, job.id, lease, caller, now=utcnow(), lease_seconds=120)


async def test_heartbeat_tells_a_draining_follower_to_drain(sessionmaker, factory):
    await factory.job()
    follower, _ = await factory.follower()
    claimed = await claim(sessionmaker, follower)
    follower.state = "draining"
    async with sessionmaker() as session:
        directive = await store.heartbeat(
            session, claimed.id, str(claimed.lease_id), follower, now=utcnow(), lease_seconds=120
        )
    assert directive == "drain"


async def test_heartbeat_on_a_cancelled_job_says_cancel_and_submit_is_refused(sessionmaker, factory):
    job = await factory.job()
    follower, _ = await factory.follower()
    claimed = await claim(sessionmaker, follower)
    async with sessionmaker() as session:
        locked = await session.get(Job, job.id, with_for_update=True)
        await store.cancel(session, locked, now=utcnow(), reason="consent withdrawn")
        await session.commit()
    async with sessionmaker() as session:
        directive = await store.heartbeat(
            session, job.id, str(claimed.lease_id), follower, now=utcnow(), lease_seconds=120
        )
    assert directive == "cancel"
    async with sessionmaker() as session:
        with pytest.raises(StaleLease):
            await store.submit(
                session, job.id, submission(claimed.lease_id), follower, now=utcnow(), outputs_present=present
            )
    (attempt,) = await attempts(sessionmaker, job.id)
    assert attempt.outcome == "cancelled"


async def test_unknown_job_is_not_found(sessionmaker, factory):
    follower, _ = await factory.follower()
    async with sessionmaker() as session:
        with pytest.raises(NotFound):
            await store.heartbeat(session, uuid.uuid4(), str(uuid.uuid4()), follower, now=utcnow(), lease_seconds=120)


# --- submit --------------------------------------------------------------------


async def test_submit_completes_the_job_and_records_checksums(sessionmaker, factory):
    job = await factory.job()
    follower, _ = await factory.follower()
    claimed = await claim(sessionmaker, follower)
    async with sessionmaker() as session:
        await store.submit(
            session, job.id, submission(claimed.lease_id), follower, now=utcnow(), outputs_present=present
        )
        await session.commit()
    stored = await load(sessionmaker, job.id)
    assert stored.state == "completed"
    assert stored.completed_at is not None
    async with sessionmaker() as session:
        result = (await session.scalars(select(JobResult))).one()
    assert (result.source_sha256, result.txt_sha256) == (H, H)
    (attempt,) = await attempts(sessionmaker, job.id)
    assert attempt.outcome == "completed"


async def test_submit_is_idempotent(sessionmaker, factory):
    job = await factory.job()
    follower, _ = await factory.follower()
    claimed = await claim(sessionmaker, follower)
    for _ in range(2):
        async with sessionmaker() as session:
            await store.submit(
                session, job.id, submission(claimed.lease_id), follower, now=utcnow(), outputs_present=present
            )
            await session.commit()
    async with sessionmaker() as session:
        assert len((await session.scalars(select(JobResult))).all()) == 1


async def test_resubmitting_different_checksums_is_refused(sessionmaker, factory):
    job = await factory.job()
    follower, _ = await factory.follower()
    claimed = await claim(sessionmaker, follower)
    async with sessionmaker() as session:
        await store.submit(session, job.id, submission(claimed.lease_id), follower, now=utcnow(), outputs_present=present)
        await session.commit()
    async with sessionmaker() as session:
        with pytest.raises(StaleLease):
            await store.submit(
                session, job.id, submission(claimed.lease_id, "b" * 64), follower, now=utcnow(), outputs_present=present
            )


async def test_submit_without_outputs_in_storage_is_refused_and_keeps_the_lease(sessionmaker, factory):
    job = await factory.job()
    follower, _ = await factory.follower()
    claimed = await claim(sessionmaker, follower)
    async with sessionmaker() as session:
        with pytest.raises(Conflict) as excinfo:
            await store.submit(session, job.id, submission(claimed.lease_id), follower, now=utcnow(), outputs_present=absent)
    assert excinfo.value.code == "outputs_missing"
    assert (await load(sessionmaker, job.id)).state == "leased"


# --- fail, release, release_all -------------------------------------------------


async def fail_with(sessionmaker, job_id, lease_id, follower, *, code="engine_error", retryable=True):
    async with sessionmaker() as session:
        await store.fail(
            session,
            job_id,
            FailRequest(lease_id=str(lease_id), code=code, reason="it broke", retryable=retryable),
            follower,
            now=utcnow(),
        )
        await session.commit()


async def test_a_retryable_failure_requeues(sessionmaker, factory):
    job = await factory.job()
    follower, _ = await factory.follower()
    claimed = await claim(sessionmaker, follower)
    await fail_with(sessionmaker, job.id, claimed.lease_id, follower)
    stored = await load(sessionmaker, job.id)
    assert (stored.state, stored.lease_id, stored.leased_by, stored.attempts) == ("queued", None, None, 1)
    (attempt,) = await attempts(sessionmaker, job.id)
    assert (attempt.outcome, attempt.reason) == ("failed", "engine_error: it broke")


@pytest.mark.parametrize("code", ["source_changed", "undecodable"])
async def test_non_retryable_codes_fail_the_job_even_if_marked_retryable(sessionmaker, factory, code):
    job = await factory.job()
    follower, _ = await factory.follower()
    claimed = await claim(sessionmaker, follower)
    await fail_with(sessionmaker, job.id, claimed.lease_id, follower, code=code, retryable=True)
    stored = await load(sessionmaker, job.id)
    assert stored.state == "failed"
    assert stored.failure_reason == f"{code}: it broke"


async def test_the_last_attempt_fails_the_job(sessionmaker, factory):
    job = await factory.job(max_attempts=1)
    follower, _ = await factory.follower()
    claimed = await claim(sessionmaker, follower)
    await fail_with(sessionmaker, job.id, claimed.lease_id, follower)
    assert (await load(sessionmaker, job.id)).state == "failed"


async def test_release_requeues_without_counting_the_attempt(sessionmaker, factory):
    job = await factory.job()
    follower, _ = await factory.follower()
    claimed = await claim(sessionmaker, follower)
    async with sessionmaker() as session:
        await store.release(session, job.id, str(claimed.lease_id), follower, now=utcnow())
        await session.commit()
    stored = await load(sessionmaker, job.id)
    assert (stored.state, stored.attempts, stored.lease_id) == ("queued", 0, None)
    (attempt,) = await attempts(sessionmaker, job.id)
    assert attempt.outcome == "released"


async def test_release_all_frees_every_lease_a_follower_holds(sessionmaker, factory):
    location = await factory.location()
    for key in ("a.mp3", "b.mp3"):
        await factory.job(await factory.recording(location, key=key))
    follower, _ = await factory.follower()
    await claim(sessionmaker, follower)
    await claim(sessionmaker, follower)
    async with sessionmaker() as session:
        released = await store.release_all(session, follower, now=utcnow())
        await session.commit()
    assert released == 2
    async with sessionmaker() as session:
        states = {j.state for j in (await session.scalars(select(Job))).all()}
    assert states == {"queued"}


async def test_cancel_a_queued_job(sessionmaker, factory):
    job = await factory.job()
    async with sessionmaker() as session:
        locked = await session.get(Job, job.id, with_for_update=True)
        await store.cancel(session, locked, now=utcnow(), reason="recording missing")
        await session.commit()
    stored = await load(sessionmaker, job.id)
    assert (stored.state, stored.failure_reason) == ("cancelled", "recording missing")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/leader/tests/test_job_store.py -q`
Expected: `ModuleNotFoundError: No module named 'swarmscribe_leader.jobs'`

- [ ] **Step 3: Implement**

`jobs/__init__.py`: empty.

`jobs/store.py`:

```python
"""Job state machine. Every function leaves committing to the caller."""

import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from swarmscribe_protocol import Directive, FailRequest, SubmitRequest

from .. import audit
from ..db.models import Follower, Job, JobAttempt, JobResult
from ..errors import Conflict, NotFound, StaleLease

OPEN_STATES = ("queued", "leased")
NON_RETRYABLE = frozenset({"source_changed", "undecodable"})


def _parse_lease(lease_id: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(lease_id)
    except ValueError:
        return None


async def _locked_job(session: AsyncSession, job_id: uuid.UUID) -> Job:
    job = await session.get(Job, job_id, with_for_update=True, populate_existing=True)
    if job is None:
        raise NotFound("no such job")
    return job


def _require_lease(job: Job, lease_id: str, follower: Follower) -> None:
    if (
        job.state != "leased"
        or job.lease_id is None
        or job.lease_id != _parse_lease(lease_id)
        or job.leased_by != follower.id
    ):
        raise StaleLease("this job is not leased to you with that lease")


def clear_lease(job: Job) -> None:
    job.lease_id = None
    job.leased_by = None
    job.lease_expires_at = None


async def close_attempt(
    session: AsyncSession, job: Job, outcome: str, reason: str | None, now: datetime
) -> None:
    await session.execute(
        update(JobAttempt)
        .where(
            JobAttempt.job_id == job.id,
            JobAttempt.lease_id == job.lease_id,
            JobAttempt.ended_at.is_(None),
        )
        .values(ended_at=now, outcome=outcome, reason=reason)
    )


async def claim(
    session: AsyncSession, follower: Follower, *, now: datetime, lease_seconds: int
) -> Job | None:
    device = follower.capabilities.get("device", "cpu")
    job = await session.scalar(
        select(Job)
        .where(
            Job.state == "queued",
            Job.pool == follower.pool,
            Job.required_device.in_(("any", device)),
        )
        .order_by(Job.priority.desc(), Job.created_at, Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        return None
    job.state = "leased"
    job.lease_id = uuid.uuid4()
    job.leased_by = follower.id
    job.lease_expires_at = now + timedelta(seconds=lease_seconds)
    job.attempts += 1
    session.add(
        JobAttempt(job_id=job.id, follower_id=follower.id, lease_id=job.lease_id, started_at=now)
    )
    audit.record(
        session,
        actor=f"follower:{follower.id}",
        action="job.claim",
        subject_type="job",
        subject_id=job.id,
        detail={"attempt": job.attempts},
    )
    return job


async def heartbeat(
    session: AsyncSession,
    job_id: uuid.UUID,
    lease_id: str,
    follower: Follower,
    *,
    now: datetime,
    lease_seconds: int,
) -> Directive:
    job = await _locked_job(session, job_id)
    if (
        job.state == "cancelled"
        and job.lease_id is not None
        and job.lease_id == _parse_lease(lease_id)
        and job.leased_by == follower.id
    ):
        return "cancel"
    _require_lease(job, lease_id, follower)
    job.lease_expires_at = now + timedelta(seconds=lease_seconds)
    return "drain" if follower.state == "draining" else "continue"


def _same_checksums(result: JobResult, request: SubmitRequest) -> bool:
    c = request.checksums
    return (result.source_sha256, result.txt_sha256, result.srt_sha256, result.segments_sha256) == (
        c.source,
        c.txt,
        c.srt,
        c.segments_json,
    )


async def submit(
    session: AsyncSession,
    job_id: uuid.UUID,
    request: SubmitRequest,
    follower: Follower,
    *,
    now: datetime,
    outputs_present: Callable[[Job], Awaitable[bool]],
) -> None:
    job = await _locked_job(session, job_id)
    if job.state == "completed":
        result = await session.scalar(select(JobResult).where(JobResult.job_id == job.id))
        if (
            job.lease_id == _parse_lease(request.lease_id)
            and job.leased_by == follower.id
            and result is not None
            and _same_checksums(result, request)
        ):
            return
        raise StaleLease("this job is already completed")
    _require_lease(job, request.lease_id, follower)
    if not await outputs_present(job):
        raise Conflict("the outputs are not in storage yet", code="outputs_missing")
    c = request.checksums
    session.add(
        JobResult(
            job_id=job.id,
            source_sha256=c.source,
            txt_sha256=c.txt,
            srt_sha256=c.srt,
            segments_sha256=c.segments_json,
        )
    )
    await close_attempt(session, job, "completed", None, now)
    job.state = "completed"
    job.completed_at = now
    job.lease_expires_at = None
    audit.record(
        session,
        actor=f"follower:{follower.id}",
        action="job.submit",
        subject_type="job",
        subject_id=job.id,
    )


async def fail(
    session: AsyncSession,
    job_id: uuid.UUID,
    request: FailRequest,
    follower: Follower,
    *,
    now: datetime,
) -> None:
    job = await _locked_job(session, job_id)
    _require_lease(job, request.lease_id, follower)
    reason = f"{request.code}: {request.reason}"
    await close_attempt(session, job, "failed", reason, now)
    if request.code in NON_RETRYABLE or not request.retryable or job.attempts >= job.max_attempts:
        job.state = "failed"
        job.failure_reason = reason
    else:
        job.state = "queued"
    clear_lease(job)
    audit.record(
        session,
        actor=f"follower:{follower.id}",
        action="job.fail",
        subject_type="job",
        subject_id=job.id,
        detail={"code": request.code, "retryable": request.retryable, "state": job.state},
    )


async def _release(session: AsyncSession, job: Job, now: datetime) -> None:
    await close_attempt(session, job, "released", None, now)
    job.attempts = max(0, job.attempts - 1)
    job.state = "queued"
    clear_lease(job)


async def release(
    session: AsyncSession, job_id: uuid.UUID, lease_id: str, follower: Follower, *, now: datetime
) -> None:
    job = await _locked_job(session, job_id)
    _require_lease(job, lease_id, follower)
    await _release(session, job, now)


async def release_all(session: AsyncSession, follower: Follower, *, now: datetime) -> int:
    jobs = (
        await session.scalars(
            select(Job)
            .where(Job.leased_by == follower.id, Job.state == "leased")
            .with_for_update()
        )
    ).all()
    for job in jobs:
        await _release(session, job, now)
    return len(jobs)


async def cancel(session: AsyncSession, job: Job, *, now: datetime, reason: str) -> None:
    """Cancel a queued or leased job. A leased job keeps its lease id so the holder hears `cancel`."""
    if job.state == "leased":
        await close_attempt(session, job, "cancelled", reason, now)
        job.lease_expires_at = None
    job.state = "cancelled"
    job.failure_reason = reason
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest packages/leader -q`, `uv run pytest -q`, `uv run ruff check .`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/leader
git commit -m "Leader: job store with leases, idempotent submit and safe concurrent claims"
```

---

### Task 6: Reaper and exclusive background loops

**Files:**
- Create: `packages/leader/src/swarmscribe_leader/jobs/reaper.py`, `background.py`
- Test: `packages/leader/tests/test_reaper.py`

**Interfaces:**
- `swarmscribe_leader.jobs.reaper.reap(session, *, now, gone_after: timedelta) -> ReapResult` where `ReapResult(requeued: int, failed: int, gone: int)` (frozen dataclass). Leaves committing to the caller.
- `swarmscribe_leader.background`:
  - `LOCK_KEYS = {"reaper": 0x53570001, "scanner": 0x53570002}`
  - `async run_exclusive(engine, name, work: Callable[[], Awaitable[None]]) -> bool` — runs `work` only if this process obtains the Postgres advisory lock for `name`; returns whether it ran.
  - `async run_periodically(stop: asyncio.Event, interval: float, step: Callable[[], Awaitable[None]], name: str) -> None` — calls `step` until `stop` is set, logging and surviving exceptions.

- [ ] **Step 1: Write the failing tests**

`packages/leader/tests/test_reaper.py`:

```python
import asyncio
from datetime import timedelta

from sqlalchemy import select

from swarmscribe_leader.background import run_exclusive, run_periodically
from swarmscribe_leader.clock import utcnow
from swarmscribe_leader.db.models import Follower, Job, JobAttempt
from swarmscribe_leader.jobs import store
from swarmscribe_leader.jobs.reaper import ReapResult, reap

GONE_AFTER = timedelta(minutes=10)


async def claim(sessionmaker, follower, *, now):
    async with sessionmaker() as session:
        job = await store.claim(session, follower, now=now, lease_seconds=120)
        await session.commit()
    return job


async def run_reaper(sessionmaker, *, now) -> ReapResult:
    async with sessionmaker() as session:
        result = await reap(session, now=now, gone_after=GONE_AFTER)
        await session.commit()
    return result


async def test_an_expired_lease_is_requeued(sessionmaker, factory):
    job = await factory.job()
    follower, _ = await factory.follower()
    start = utcnow()
    await claim(sessionmaker, follower, now=start)
    result = await run_reaper(sessionmaker, now=start + timedelta(seconds=121))
    assert result == ReapResult(requeued=1, failed=0, gone=0)
    async with sessionmaker() as session:
        stored = await session.get(Job, job.id)
        attempt = (await session.scalars(select(JobAttempt))).one()
    assert (stored.state, stored.lease_id, stored.leased_by) == ("queued", None, None)
    assert attempt.outcome == "expired"


async def test_a_lease_that_has_not_expired_is_left_alone(sessionmaker, factory):
    job = await factory.job()
    follower, _ = await factory.follower()
    start = utcnow()
    await claim(sessionmaker, follower, now=start)
    await run_reaper(sessionmaker, now=start + timedelta(seconds=60))
    async with sessionmaker() as session:
        assert (await session.get(Job, job.id)).state == "leased"


async def test_expiring_the_last_attempt_fails_the_job(sessionmaker, factory):
    job = await factory.job(max_attempts=1)
    follower, _ = await factory.follower()
    start = utcnow()
    await claim(sessionmaker, follower, now=start)
    result = await run_reaper(sessionmaker, now=start + timedelta(seconds=121))
    assert result.failed == 1
    async with sessionmaker() as session:
        stored = await session.get(Job, job.id)
    assert (stored.state, stored.failure_reason) == ("failed", "lease expired too many times")


async def test_a_job_locked_by_another_transaction_is_skipped(sessionmaker, factory):
    job = await factory.job()
    follower, _ = await factory.follower()
    start = utcnow()
    await claim(sessionmaker, follower, now=start)
    async with sessionmaker() as holder:
        await holder.get(Job, job.id, with_for_update=True)  # e.g. a submit in progress
        result = await run_reaper(sessionmaker, now=start + timedelta(seconds=121))
        assert result.requeued == 0
        await holder.rollback()
    async with sessionmaker() as session:
        assert (await session.get(Job, job.id)).state == "leased"


async def test_silent_followers_without_a_lease_are_marked_gone(sessionmaker, factory):
    now = utcnow()
    quiet, _ = await factory.follower(last_seen_at=now - timedelta(minutes=11))
    busy, _ = await factory.follower(last_seen_at=now - timedelta(minutes=11))
    recent, _ = await factory.follower(last_seen_at=now - timedelta(minutes=1))
    await factory.job()
    await claim(sessionmaker, busy, now=now)
    result = await run_reaper(sessionmaker, now=now)
    assert result.gone == 1
    async with sessionmaker() as session:
        states = {f.id: f.state for f in (await session.scalars(select(Follower))).all()}
    assert states == {quiet.id: "gone", busy.id: "active", recent.id: "active"}


async def test_only_one_process_runs_exclusive_work_at_a_time(engine):
    started = asyncio.Event()
    release = asyncio.Event()
    runs = []

    async def slow():
        runs.append("slow")
        started.set()
        await release.wait()

    async def quick():
        runs.append("quick")

    first = asyncio.create_task(run_exclusive(engine, "reaper", slow))
    await started.wait()
    assert await run_exclusive(engine, "reaper", quick) is False
    release.set()
    assert await first is True
    assert await run_exclusive(engine, "reaper", quick) is True
    assert runs == ["slow", "quick"]


async def test_run_periodically_survives_errors_and_stops_when_told():
    stop = asyncio.Event()
    calls = []

    async def step():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        if len(calls) == 3:
            stop.set()

    await asyncio.wait_for(run_periodically(stop, 0.01, step, "test"), timeout=5)
    assert len(calls) == 3
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/leader/tests/test_reaper.py -q`
Expected: `ModuleNotFoundError: No module named 'swarmscribe_leader.background'`

- [ ] **Step 3: Implement**

`jobs/reaper.py`:

```python
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Follower, Job
from .store import clear_lease, close_attempt


@dataclass(frozen=True)
class ReapResult:
    requeued: int
    failed: int
    gone: int


async def reap(session: AsyncSession, *, now: datetime, gone_after: timedelta) -> ReapResult:
    """Return expired leases to the queue and mark silent followers gone.

    Jobs locked by another transaction (a submit in progress) are skipped this round.
    """
    jobs = (
        await session.scalars(
            select(Job)
            .where(Job.state == "leased", Job.lease_expires_at < now)
            .with_for_update(skip_locked=True)
        )
    ).all()
    requeued = failed = 0
    for job in jobs:
        await close_attempt(session, job, "expired", "lease expired", now)
        if job.attempts >= job.max_attempts:
            job.state = "failed"
            job.failure_reason = "lease expired too many times"
            failed += 1
        else:
            job.state = "queued"
            requeued += 1
        clear_lease(job)
    await session.flush()
    holding = select(Job.leased_by).where(Job.state == "leased", Job.leased_by.is_not(None))
    result = await session.execute(
        update(Follower)
        .where(
            Follower.state.in_(("active", "draining")),
            Follower.last_seen_at < now - gone_after,
            Follower.id.not_in(holding),
        )
        .values(state="gone")
    )
    return ReapResult(requeued=requeued, failed=failed, gone=result.rowcount)
```

`background.py`:

```python
import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

LOCK_KEYS = {"reaper": 0x53570001, "scanner": 0x53570002}


async def run_exclusive(
    engine: AsyncEngine, name: str, work: Callable[[], Awaitable[None]]
) -> bool:
    """Run `work` only if no other replica holds the advisory lock for `name`."""
    key = LOCK_KEYS[name]
    async with engine.connect() as conn:
        got = await conn.scalar(text("select pg_try_advisory_lock(:key)"), {"key": key})
        if not got:
            await conn.rollback()
            return False
        try:
            await work()
            return True
        finally:
            await conn.execute(text("select pg_advisory_unlock(:key)"), {"key": key})
            await conn.commit()


async def run_periodically(
    stop: asyncio.Event,
    interval: float,
    step: Callable[[], Awaitable[None]],
    name: str,
) -> None:
    while not stop.is_set():
        try:
            await step()
        except Exception:
            logger.exception("background task %s failed", name)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest packages/leader -q`, `uv run pytest -q`, `uv run ruff check .`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/leader
git commit -m "Leader: lease reaper and advisory-locked background loops"
```

---

### Task 7: Local storage and signed links

**Files:**
- Create: `packages/leader/src/swarmscribe_leader/storage/__init__.py`, `storage/base.py`, `storage/links.py`, `storage/local.py`, `storage/registry.py`
- Test: `packages/leader/tests/test_links.py`, `packages/leader/tests/test_local_storage.py`

**Interfaces:**
- `storage/base.py`: `ObjectInfo(key: str, size: int, version: str)` (frozen dataclass); `StorageError(Exception)`; `StorageBackend` `Protocol` with `list(prefix: str = "") -> AsyncIterator[ObjectInfo]`, `async read_text(key) -> str | None`, `async stat(key) -> ObjectInfo | None`, `download_link(key, version, ttl: timedelta) -> Link`, `upload_link(key, ttl: timedelta) -> Link`, `async delete(key) -> None`.
- `storage/links.py`: `LinkClaims(location_id: str, key: str, method: str, version: str, expires: int)`; `InvalidLink(Exception)`; `LinkSigner(key: bytes)` with `sign(claims) -> str` and `verify(token: str, *, now: float) -> LinkClaims`.
- `storage/local.py`: `LocalBackend(root: Path, *, location_id: str, signer: LinkSigner, public_url: str, clock: Callable[[], float] = time.time)` implementing the protocol, plus `path_for(key) -> Path`; `version_of(stat_result) -> str` = `f"{st_size}-{st_mtime_ns}"`. Links are `{public_url}/v1/files/{token}`.
- `storage/registry.py`: `backend_for(location: StorageLocation, *, signer, public_url) -> StorageBackend` (local only in this plan; anything else raises `StorageError`).

- [ ] **Step 1: Write the failing tests**

`packages/leader/tests/test_links.py`:

```python
import pytest

from swarmscribe_leader.storage.links import InvalidLink, LinkClaims, LinkSigner

KEY = b"k" * 32
CLAIMS = LinkClaims(location_id="loc", key="talks/one.mp3", method="GET", version="10-1", expires=2_000)


def test_a_signed_link_verifies_to_the_same_claims():
    signer = LinkSigner(KEY)
    assert signer.verify(signer.sign(CLAIMS), now=1_000) == CLAIMS


def test_the_key_must_be_at_least_32_bytes():
    with pytest.raises(ValueError):
        LinkSigner(b"short")


def test_an_expired_link_is_refused():
    signer = LinkSigner(KEY)
    with pytest.raises(InvalidLink, match="expired"):
        signer.verify(signer.sign(CLAIMS), now=2_000)


def test_a_link_signed_with_another_key_is_refused():
    token = LinkSigner(b"x" * 32).sign(CLAIMS)
    with pytest.raises(InvalidLink):
        LinkSigner(KEY).verify(token, now=1_000)


def test_a_tampered_payload_is_refused():
    signer = LinkSigner(KEY)
    token = signer.sign(CLAIMS)
    other = signer.sign(LinkClaims("loc", "secret.mp3", "GET", "", 2_000))
    forged = other.split(".")[0] + "." + token.split(".")[1]
    with pytest.raises(InvalidLink):
        signer.verify(forged, now=1_000)


@pytest.mark.parametrize("token", ["", "abc", "abc.", ".abc", "a.b.c", "é.é", "!!!.###"])
def test_malformed_tokens_are_refused(token):
    with pytest.raises(InvalidLink):
        LinkSigner(KEY).verify(token, now=1_000)
```

`packages/leader/tests/test_local_storage.py`:

```python
import os
from datetime import timedelta
from urllib.parse import urlsplit

import pytest

from swarmscribe_leader.storage.base import StorageError
from swarmscribe_leader.storage.links import LinkSigner
from swarmscribe_leader.storage.local import LocalBackend, version_of
from swarmscribe_leader.storage.registry import backend_for

SIGNER = LinkSigner(b"k" * 32)


def backend(root, clock=lambda: 1_000.0):
    return LocalBackend(root, location_id="loc-1", signer=SIGNER, public_url="https://leader/", clock=clock)


def write(root, key, data=b"audio"):
    path = root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


async def collect(iterator):
    return [item async for item in iterator]


async def test_list_returns_posix_keys_with_size_and_version(tmp_path):
    a = write(tmp_path, "talks/one.mp3", b"12345")
    write(tmp_path, "talks/deep/two.wav", b"1")
    write(tmp_path, "other.txt")
    items = await collect(backend(tmp_path).list())
    by_key = {i.key: i for i in items}
    assert set(by_key) == {"talks/one.mp3", "talks/deep/two.wav", "other.txt"}
    assert by_key["talks/one.mp3"].size == 5
    assert by_key["talks/one.mp3"].version == version_of(os.stat(a))


async def test_list_filters_by_prefix(tmp_path):
    write(tmp_path, "talks/one.mp3")
    write(tmp_path, "archive/two.mp3")
    keys = [i.key for i in await collect(backend(tmp_path).list("talks/"))]
    assert keys == ["talks/one.mp3"]


async def test_read_text_strips_a_bom_and_returns_none_when_absent(tmp_path):
    write(tmp_path, "consent.txt", "﻿talks/*.mp3\n".encode())
    store = backend(tmp_path)
    assert await store.read_text("consent.txt") == "talks/*.mp3\n"
    assert await store.read_text("missing.txt") is None


async def test_stat(tmp_path):
    write(tmp_path, "a.mp3", b"123")
    store = backend(tmp_path)
    assert (await store.stat("a.mp3")).size == 3
    assert await store.stat("nope.mp3") is None


@pytest.mark.parametrize(
    "key", ["../escape.mp3", "talks/../../escape.mp3", "/etc/passwd", "\\windows\\x", "a\\b.mp3", ""]
)
async def test_keys_that_could_escape_the_root_are_rejected(tmp_path, key):
    with pytest.raises(StorageError):
        backend(tmp_path).path_for(key)


async def test_download_link_is_a_signed_get_pinned_to_the_version(tmp_path):
    link = backend(tmp_path).download_link("talks/one.mp3", "5-99", timedelta(minutes=30))
    assert link.method == "GET"
    parts = urlsplit(link.url)
    assert (parts.scheme, parts.netloc) == ("https", "leader")
    assert parts.path.startswith("/v1/files/")
    claims = SIGNER.verify(parts.path.removeprefix("/v1/files/"), now=1_000)
    assert (claims.location_id, claims.key, claims.method, claims.version, claims.expires) == (
        "loc-1",
        "talks/one.mp3",
        "GET",
        "5-99",
        1_000 + 1_800,
    )


async def test_upload_link_is_a_signed_put(tmp_path):
    link = backend(tmp_path).upload_link("transcripts/talks/one.mp3.txt", timedelta(hours=2))
    claims = SIGNER.verify(urlsplit(link.url).path.removeprefix("/v1/files/"), now=1_000)
    assert (link.method, claims.method, claims.version) == ("PUT", "PUT", "")


async def test_links_refuse_bad_keys(tmp_path):
    with pytest.raises(StorageError):
        backend(tmp_path).download_link("../x.mp3", "1-1", timedelta(minutes=1))


async def test_delete(tmp_path):
    path = write(tmp_path, "a.mp3")
    store = backend(tmp_path)
    await store.delete("a.mp3")
    await store.delete("a.mp3")
    assert not path.exists()


async def test_registry_builds_a_local_backend_and_refuses_others(tmp_path, factory):
    location = await factory.location()
    assert isinstance(backend_for(location, signer=SIGNER, public_url="https://l"), LocalBackend)
    azure = await factory.location(backend="azure", config={})
    with pytest.raises(StorageError, match="not available"):
        backend_for(azure, signer=SIGNER, public_url="https://l")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/leader/tests/test_links.py packages/leader/tests/test_local_storage.py -q`
Expected: `ModuleNotFoundError: No module named 'swarmscribe_leader.storage'`

- [ ] **Step 3: Implement**

`storage/__init__.py`: empty.

`storage/base.py`:

```python
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from swarmscribe_protocol import Link


@dataclass(frozen=True)
class ObjectInfo:
    key: str
    size: int
    version: str


class StorageError(Exception):
    """A storage request that cannot be carried out (bad key, unsupported backend)."""


class StorageBackend(Protocol):
    def list(self, prefix: str = "") -> AsyncIterator[ObjectInfo]: ...

    async def read_text(self, key: str) -> str | None: ...

    async def stat(self, key: str) -> ObjectInfo | None: ...

    def download_link(self, key: str, version: str, ttl: timedelta) -> Link: ...

    def upload_link(self, key: str, ttl: timedelta) -> Link: ...

    async def delete(self, key: str) -> None: ...
```

`storage/links.py`:

```python
"""HMAC-signed, expiring links for files the leader serves itself."""

import base64
import binascii
import hashlib
import hmac
import json
from dataclasses import asdict, dataclass


class InvalidLink(Exception):
    """The link is malformed, forged or expired."""


@dataclass(frozen=True)
class LinkClaims:
    location_id: str
    key: str
    method: str
    version: str
    expires: int


def _encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class LinkSigner:
    def __init__(self, key: bytes):
        if len(key) < 32:
            raise ValueError("the link key must be at least 32 bytes")
        self._key = key

    def _mac(self, payload: str) -> bytes:
        return hmac.new(self._key, payload.encode("ascii"), hashlib.sha256).digest()

    def sign(self, claims: LinkClaims) -> str:
        body = json.dumps(asdict(claims), separators=(",", ":"), sort_keys=True).encode("utf-8")
        payload = _encode(body)
        return payload + "." + _encode(self._mac(payload))

    def verify(self, token: str, *, now: float) -> LinkClaims:
        payload, dot, signature = token.partition(".")
        if not dot or not payload or not signature or not token.isascii():
            raise InvalidLink("malformed link")
        try:
            given = _decode(signature)
        except (ValueError, binascii.Error) as exc:
            raise InvalidLink("malformed link") from exc
        if not hmac.compare_digest(given, self._mac(payload)):
            raise InvalidLink("bad link signature")
        try:
            claims = LinkClaims(**json.loads(_decode(payload)))
        except (ValueError, TypeError, binascii.Error) as exc:
            raise InvalidLink("malformed link") from exc
        if claims.expires <= now:
            raise InvalidLink("link expired")
        return claims
```

`storage/local.py`:

```python
import os
import time
from collections.abc import AsyncIterator, Callable
from datetime import timedelta
from pathlib import Path, PurePosixPath

from swarmscribe_protocol import Link

from .base import ObjectInfo, StorageError
from .links import LinkClaims, LinkSigner


def version_of(stat_result: os.stat_result) -> str:
    return f"{stat_result.st_size}-{stat_result.st_mtime_ns}"


class LocalBackend:
    """A folder on a filesystem every leader replica can see. The leader serves its links."""

    def __init__(
        self,
        root: Path,
        *,
        location_id: str,
        signer: LinkSigner,
        public_url: str,
        clock: Callable[[], float] = time.time,
    ):
        self.root = Path(root).resolve()
        self.location_id = location_id
        self.signer = signer
        self.public_url = public_url.rstrip("/")
        self.clock = clock

    def path_for(self, key: str) -> Path:
        if (
            not key
            or key.startswith("/")
            or "\\" in key
            or ".." in PurePosixPath(key).parts
        ):
            raise StorageError(f"invalid storage key {key!r}")
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise StorageError(f"invalid storage key {key!r}")
        return path

    async def list(self, prefix: str = "") -> AsyncIterator[ObjectInfo]:
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames.sort()
            for name in sorted(filenames):
                full = Path(dirpath) / name
                key = full.relative_to(self.root).as_posix()
                if key.startswith(prefix):
                    st = full.stat()
                    yield ObjectInfo(key=key, size=st.st_size, version=version_of(st))

    async def read_text(self, key: str) -> str | None:
        path = self.path_for(key)
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8-sig")

    async def stat(self, key: str) -> ObjectInfo | None:
        path = self.path_for(key)
        if not path.is_file():
            return None
        st = path.stat()
        return ObjectInfo(key=key, size=st.st_size, version=version_of(st))

    def _link(self, key: str, method: str, version: str, ttl: timedelta) -> Link:
        self.path_for(key)
        claims = LinkClaims(
            location_id=self.location_id,
            key=key,
            method=method,
            version=version,
            expires=int(self.clock() + ttl.total_seconds()),
        )
        return Link(url=f"{self.public_url}/v1/files/{self.signer.sign(claims)}", method=method)

    def download_link(self, key: str, version: str, ttl: timedelta) -> Link:
        return self._link(key, "GET", version, ttl)

    def upload_link(self, key: str, ttl: timedelta) -> Link:
        return self._link(key, "PUT", "", ttl)

    async def delete(self, key: str) -> None:
        self.path_for(key).unlink(missing_ok=True)
```

`storage/registry.py`:

```python
from pathlib import Path

from ..db.models import StorageLocation
from .base import StorageBackend, StorageError
from .links import LinkSigner
from .local import LocalBackend


def backend_for(
    location: StorageLocation, *, signer: LinkSigner, public_url: str
) -> StorageBackend:
    if location.backend == "local":
        return LocalBackend(
            Path(location.config["root"]),
            location_id=str(location.id),
            signer=signer,
            public_url=public_url,
        )
    raise StorageError(f"storage backend {location.backend!r} is not available yet")
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest packages/leader -q`, `uv run pytest -q`, `uv run ruff check .`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/leader
git commit -m "Leader: local storage backend and HMAC-signed links"
```

---

### Task 8: Consent and the scanner

**Files:**
- Create: `packages/leader/src/swarmscribe_leader/ingest/__init__.py`, `ingest/consent.py`, `ingest/scanner.py`
- Test: `packages/leader/tests/test_consent.py`, `packages/leader/tests/test_scanner.py`

**Interfaces:**
- `ingest/consent.py`: `AUDIO_EXTENSIONS` (frozenset of `.mp3 .m4a .aac .wav .flac .ogg .opus .wma .mp4 .m4v .mov .mkv .webm`); `is_recording(key) -> bool` (extension, case-insensitive); `parse_consent(text: str | None) -> tuple[str, ...]` (one glob per line, blanks and `#` comments skipped, BOM stripped); `glob_to_regex(pattern) -> re.Pattern[str]` (`*` and `?` never cross `/`, `**` any depth, `**/` zero or more directories, `[...]`/`[!...]` classes, case-sensitive); `matching_pattern(patterns, key) -> str | None` (first matching pattern).
- `ingest/scanner.py`:
  - `ScanSummary` (dataclass of ints: `listed`, `consented`, `not_consented`, `withdrawn`, `missing`, `jobs_created`, `jobs_cancelled`)
  - `async scan_location(session, location, backend, *, now, max_attempts) -> ScanSummary` (leaves committing to the caller)
  - `async scan_due_locations(sessionmaker, backend_factory: Callable[[StorageLocation], StorageBackend], *, now, max_attempts) -> dict[str, ScanSummary | str]` (each location in its own transaction; a failure records `last_scan_error` and does not stop the others)
- Consent patterns apply to the key relative to the location's `input_prefix`. `consent.txt` is read from the location root.

- [ ] **Step 1: Write the failing tests**

`packages/leader/tests/test_consent.py`:

```python
import pytest

from swarmscribe_leader.ingest.consent import (
    glob_to_regex,
    is_recording,
    matching_pattern,
    parse_consent,
)


@pytest.mark.parametrize(
    ("pattern", "key", "matches"),
    [
        ("talks/*.mp3", "talks/one.mp3", True),
        ("talks/*.mp3", "talks/deep/one.mp3", False),
        ("talks/*.mp3", "Talks/one.mp3", False),
        ("talks/?.mp3", "talks/a.mp3", True),
        ("talks/?.mp3", "talks/ab.mp3", False),
        ("**/*.mp3", "one.mp3", True),
        ("**/*.mp3", "a/b/c/one.mp3", True),
        ("talks/**", "talks/a/b/one.mp3", True),
        ("talks/**/final.wav", "talks/final.wav", True),
        ("talks/**/final.wav", "talks/x/y/final.wav", True),
        ("talks/[ab].mp3", "talks/b.mp3", True),
        ("talks/[!ab].mp3", "talks/b.mp3", False),
        ("talks/[!ab].mp3", "talks/c.mp3", True),
        ("talks/one.mp3", "talks/one.mp3", True),
        ("talks/one.mp3", "talks/one.mp3.bak", False),
        ("a+b (1).mp3", "a+b (1).mp3", True),
    ],
)
def test_glob_semantics(pattern, key, matches):
    assert bool(glob_to_regex(pattern).fullmatch(key)) is matches


def test_parse_consent_skips_comments_blanks_and_a_bom():
    assert parse_consent("﻿# allowed\ntalks/*.mp3\n\n  2024/**  \n") == (
        "talks/*.mp3",
        "2024/**",
    )


def test_no_consent_file_means_no_patterns():
    assert parse_consent(None) == ()


def test_matching_pattern_returns_the_first_match():
    patterns = ("archive/**", "talks/*.mp3", "**/*.mp3")
    assert matching_pattern(patterns, "talks/one.mp3") == "talks/*.mp3"
    assert matching_pattern(patterns, "private.wav") is None


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("a.mp3", True),
        ("a.MP3", True),
        ("x/b.webm", True),
        ("consent.txt", False),
        ("a.mp3.txt", False),
        ("noextension", False),
    ],
)
def test_is_recording(key, expected):
    assert is_recording(key) is expected
```

`packages/leader/tests/test_scanner.py`:

```python
from datetime import timedelta

from sqlalchemy import select

from swarmscribe_leader.clock import utcnow
from swarmscribe_leader.db.models import AuditEntry, Job, Recording, StorageLocation
from swarmscribe_leader.ingest.scanner import scan_due_locations, scan_location
from swarmscribe_leader.jobs import store
from swarmscribe_leader.storage.links import LinkSigner
from swarmscribe_leader.storage.registry import backend_for

SIGNER = LinkSigner(b"k" * 32)


def write(root, key, data=b"audio"):
    path = root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def backends(location):
    return backend_for(location, signer=SIGNER, public_url="https://leader")


async def scan(sessionmaker, location, *, now=None):
    async with sessionmaker() as session:
        loc = await session.get(StorageLocation, location.id, with_for_update=True)
        summary = await scan_location(session, loc, backends(loc), now=now or utcnow(), max_attempts=3)
        await session.commit()
    return summary


async def rows(sessionmaker, model):
    async with sessionmaker() as session:
        return list((await session.scalars(select(model))).all())


async def test_only_consented_recordings_become_jobs(sessionmaker, factory, tmp_path):
    write(tmp_path, "consent.txt", b"talks/*.mp3\n")
    write(tmp_path, "talks/one.mp3")
    write(tmp_path, "talks/two.mp3")
    write(tmp_path, "private/three.mp3")
    write(tmp_path, "talks/notes.txt")
    location = await factory.location(pool="gpu", required_device="cuda")
    summary = await scan(sessionmaker, location)
    recordings = {r.key: r.consent for r in await rows(sessionmaker, Recording)}
    assert recordings == {
        "talks/one.mp3": "consented",
        "talks/two.mp3": "consented",
        "private/three.mp3": "not_consented",
    }
    jobs = await rows(sessionmaker, Job)
    assert len(jobs) == 2
    assert {(j.state, j.pool, j.required_device, j.max_attempts) for j in jobs} == {
        ("queued", "gpu", "cuda", 3)
    }
    assert (summary.listed, summary.consented, summary.jobs_created) == (3, 2, 2)


async def test_without_a_consent_file_nothing_is_queued(sessionmaker, factory, tmp_path):
    write(tmp_path, "talks/one.mp3")
    await scan(sessionmaker, await factory.location())
    assert await rows(sessionmaker, Job) == []
    assert [r.consent for r in await rows(sessionmaker, Recording)] == ["not_consented"]


async def test_rescanning_an_unchanged_folder_creates_nothing(sessionmaker, factory, tmp_path):
    write(tmp_path, "consent.txt", b"**/*.mp3\n")
    write(tmp_path, "talks/one.mp3")
    location = await factory.location()
    await scan(sessionmaker, location)
    summary = await scan(sessionmaker, location)
    assert summary.jobs_created == 0
    assert len(await rows(sessionmaker, Job)) == 1


async def test_a_changed_file_cancels_the_old_job_and_queues_the_new_version(sessionmaker, factory, tmp_path):
    write(tmp_path, "consent.txt", b"**/*.mp3\n")
    write(tmp_path, "talks/one.mp3", b"first")
    location = await factory.location()
    await scan(sessionmaker, location)
    write(tmp_path, "talks/one.mp3", b"second, longer")
    await scan(sessionmaker, location)
    jobs = sorted(await rows(sessionmaker, Job), key=lambda j: j.created_at)
    assert [j.state for j in jobs] == ["cancelled", "queued"]
    assert jobs[0].failure_reason == "recording changed"
    assert jobs[0].source_version != jobs[1].source_version


async def test_withdrawn_consent_cancels_open_work_and_flags_finished_outputs(sessionmaker, factory, tmp_path):
    write(tmp_path, "consent.txt", b"talks/*.mp3\n")
    write(tmp_path, "talks/one.mp3")
    write(tmp_path, "talks/two.mp3")
    location = await factory.location()
    await scan(sessionmaker, location)
    follower, _ = await factory.follower()
    async with sessionmaker() as session:
        first = await store.claim(session, follower, now=utcnow(), lease_seconds=120)
        await session.commit()
    async with sessionmaker() as session:
        done = await session.get(Job, first.id)
        done.state = "completed"
        await session.commit()
    write(tmp_path, "consent.txt", b"# nothing allowed any more\n")
    summary = await scan(sessionmaker, location)
    assert summary.withdrawn == 2
    jobs = {j.id: j for j in await rows(sessionmaker, Job)}
    assert jobs[first.id].outputs_flagged_for_deletion is True
    others = [j for j in jobs.values() if j.id != first.id]
    assert [(j.state, j.failure_reason) for j in others] == [("cancelled", "consent withdrawn")]
    assert {r.consent for r in await rows(sessionmaker, Recording)} == {"withdrawn"}


async def test_a_leased_job_whose_consent_is_withdrawn_is_told_to_cancel(sessionmaker, factory, tmp_path):
    write(tmp_path, "consent.txt", b"talks/*.mp3\n")
    write(tmp_path, "talks/one.mp3")
    location = await factory.location()
    await scan(sessionmaker, location)
    follower, _ = await factory.follower()
    async with sessionmaker() as session:
        job = await store.claim(session, follower, now=utcnow(), lease_seconds=120)
        await session.commit()
    write(tmp_path, "consent.txt", b"")
    await scan(sessionmaker, location)
    async with sessionmaker() as session:
        directive = await store.heartbeat(
            session, job.id, str(job.lease_id), follower, now=utcnow(), lease_seconds=120
        )
    assert directive == "cancel"


async def test_a_missing_file_cancels_its_open_job(sessionmaker, factory, tmp_path):
    write(tmp_path, "consent.txt", b"**/*.mp3\n")
    write(tmp_path, "talks/one.mp3")
    location = await factory.location()
    await scan(sessionmaker, location)
    (tmp_path / "talks" / "one.mp3").unlink()
    summary = await scan(sessionmaker, location)
    assert summary.missing == 1
    (recording,) = await rows(sessionmaker, Recording)
    (job,) = await rows(sessionmaker, Job)
    assert recording.missing is True
    assert (job.state, job.failure_reason) == ("cancelled", "recording missing")


async def test_a_failed_job_is_not_recreated_by_scanning(sessionmaker, factory, tmp_path):
    write(tmp_path, "consent.txt", b"**/*.mp3\n")
    write(tmp_path, "talks/one.mp3")
    location = await factory.location()
    await scan(sessionmaker, location)
    async with sessionmaker() as session:
        (job,) = (await session.scalars(select(Job))).all()
        job.state = "failed"
        await session.commit()
    summary = await scan(sessionmaker, location)
    assert summary.jobs_created == 0


async def test_patterns_are_relative_to_the_input_prefix(sessionmaker, factory, tmp_path):
    write(tmp_path, "consent.txt", b"*.mp3\n")
    write(tmp_path, "incoming/one.mp3")
    write(tmp_path, "elsewhere/two.mp3")
    await scan(sessionmaker, await factory.location(input_prefix="incoming/"))
    assert [r.key for r in await rows(sessionmaker, Recording)] == ["incoming/one.mp3"]
    assert len(await rows(sessionmaker, Job)) == 1


async def test_a_scan_is_audited_and_timestamped(sessionmaker, factory, tmp_path):
    write(tmp_path, "consent.txt", b"**/*.mp3\n")
    write(tmp_path, "talks/one.mp3")
    location = await factory.location()
    now = utcnow()
    await scan(sessionmaker, location, now=now)
    (entry,) = await rows(sessionmaker, AuditEntry)
    assert (entry.actor, entry.action, entry.detail["jobs_created"]) == ("system", "location.scan", 1)
    assert "talks/one.mp3" not in str(entry.detail)
    async with sessionmaker() as session:
        stored = await session.get(StorageLocation, location.id)
    assert (stored.last_scan_at, stored.last_scan_error) == (now, None)


async def test_scan_due_locations_skips_locations_not_yet_due(sessionmaker, factory, tmp_path):
    write(tmp_path, "consent.txt", b"**/*.mp3\n")
    write(tmp_path, "talks/one.mp3")
    now = utcnow()
    due = await factory.location(name="due")
    await factory.location(name="recent", last_scan_at=now - timedelta(seconds=10))
    await factory.location(name="disabled", enabled=False)
    results = await scan_due_locations(sessionmaker, backends, now=now, max_attempts=3)
    assert set(results) == {"due"}
    assert results["due"].jobs_created == 1
    async with sessionmaker() as session:
        assert (await session.get(StorageLocation, due.id)).last_scan_at == now


async def test_a_failing_location_records_its_error_and_others_still_scan(sessionmaker, factory, tmp_path):
    write(tmp_path, "consent.txt", b"**/*.mp3\n")
    write(tmp_path, "talks/one.mp3")
    broken = await factory.location(name="broken", backend="azure", config={})
    await factory.location(name="fine")
    now = utcnow()
    results = await scan_due_locations(sessionmaker, backends, now=now, max_attempts=3)
    assert "not available" in results["broken"]
    assert results["fine"].jobs_created == 1
    async with sessionmaker() as session:
        stored = await session.get(StorageLocation, broken.id)
    assert "not available" in stored.last_scan_error
    assert stored.last_scan_at == now
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/leader/tests/test_consent.py packages/leader/tests/test_scanner.py -q`
Expected: `ModuleNotFoundError: No module named 'swarmscribe_leader.ingest'`

- [ ] **Step 3: Implement**

`ingest/__init__.py`: empty.

`ingest/consent.py`:

```python
import re
from pathlib import PurePosixPath

AUDIO_EXTENSIONS = frozenset(
    {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".wma",
     ".mp4", ".m4v", ".mov", ".mkv", ".webm"}
)


def is_recording(key: str) -> bool:
    return PurePosixPath(key).suffix.lower() in AUDIO_EXTENSIONS


def parse_consent(text: str | None) -> tuple[str, ...]:
    if not text:
        return ()
    if text.startswith("﻿"):
        text = text[1:]
    return tuple(
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    )


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Glob with `/` as separator: `*` and `?` stay inside one path part, `**` crosses parts."""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        elif pattern[i] == "[" and (end := pattern.find("]", i + 1)) != -1:
            body = pattern[i + 1 : end]
            if body.startswith("!"):
                body = "^" + body[1:]
            out.append("[" + body.replace("\\", "\\\\") + "]")
            i = end + 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out))


def matching_pattern(patterns: tuple[str, ...], key: str) -> str | None:
    for pattern in patterns:
        if glob_to_regex(pattern).fullmatch(key):
            return pattern
    return None
```

`ingest/scanner.py`:

```python
import logging
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .. import audit
from ..db.models import Job, Recording, StorageLocation
from ..jobs.store import OPEN_STATES, cancel
from ..storage.base import StorageBackend
from .consent import is_recording, matching_pattern, parse_consent

logger = logging.getLogger(__name__)


@dataclass
class ScanSummary:
    listed: int = 0
    consented: int = 0
    not_consented: int = 0
    withdrawn: int = 0
    missing: int = 0
    jobs_created: int = 0
    jobs_cancelled: int = 0


def _consent_state(previous: str | None, pattern: str | None) -> str:
    if pattern is not None:
        return "consented"
    if previous in ("consented", "withdrawn"):
        return "withdrawn"
    return "not_consented"


async def scan_location(
    session: AsyncSession,
    location: StorageLocation,
    backend: StorageBackend,
    *,
    now: datetime,
    max_attempts: int,
) -> ScanSummary:
    summary = ScanSummary()
    patterns = parse_consent(await backend.read_text("consent.txt"))
    existing = {
        r.key: r
        for r in (
            await session.scalars(select(Recording).where(Recording.location_id == location.id))
        ).all()
    }
    seen: set[str] = set()
    async for obj in backend.list(location.input_prefix):
        if not is_recording(obj.key):
            continue
        seen.add(obj.key)
        summary.listed += 1
        pattern = matching_pattern(patterns, obj.key[len(location.input_prefix) :])
        recording = existing.get(obj.key)
        if recording is None:
            recording = Recording(
                id=uuid.uuid4(),
                location_id=location.id,
                key=obj.key,
                size=obj.size,
                source_version=obj.version,
                consent=_consent_state(None, pattern),
                consent_pattern=pattern,
                first_seen_at=now,
                last_seen_at=now,
                missing=False,
            )
            session.add(recording)
            existing[obj.key] = recording
        else:
            recording.size = obj.size
            recording.source_version = obj.version
            recording.consent = _consent_state(recording.consent, pattern)
            recording.consent_pattern = pattern
            recording.last_seen_at = now
            recording.missing = False
    for key, recording in existing.items():
        if key not in seen:
            recording.missing = True
            summary.missing += 1
        elif recording.consent == "consented":
            summary.consented += 1
        elif recording.consent == "withdrawn":
            summary.withdrawn += 1
        else:
            summary.not_consented += 1
    await session.flush()

    by_id = {r.id: r for r in existing.values()}
    open_jobs = (
        await session.scalars(
            select(Job)
            .where(Job.recording_id.in_(list(by_id)), Job.state.in_(OPEN_STATES))
            .with_for_update()
        )
    ).all()
    for job in open_jobs:
        recording = by_id[job.recording_id]
        if recording.missing:
            reason = "recording missing"
        elif recording.consent != "consented":
            reason = "consent withdrawn"
        elif job.source_version != recording.source_version:
            reason = "recording changed"
        else:
            continue
        await cancel(session, job, now=now, reason=reason)
        summary.jobs_cancelled += 1

    withdrawn = [r.id for r in by_id.values() if r.consent == "withdrawn"]
    if withdrawn:
        await session.execute(
            update(Job)
            .where(Job.recording_id.in_(withdrawn), Job.state == "completed")
            .values(outputs_flagged_for_deletion=True)
        )

    wanted = [r for r in by_id.values() if r.consent == "consented" and not r.missing]
    have = {
        (row[0], row[1])
        for row in (
            await session.execute(
                select(Job.recording_id, Job.source_version).where(
                    Job.recording_id.in_([r.id for r in wanted]), Job.state != "cancelled"
                )
            )
        ).all()
    }
    for recording in wanted:
        if (recording.id, recording.source_version) in have:
            continue
        session.add(
            Job(
                id=uuid.uuid4(),
                recording_id=recording.id,
                source_version=recording.source_version,
                state="queued",
                pool=location.pool,
                required_device=location.required_device,
                priority=0,
                attempts=0,
                max_attempts=max_attempts,
            )
        )
        summary.jobs_created += 1

    location.last_scan_at = now
    location.last_scan_error = None
    audit.record(
        session,
        actor="system",
        action="location.scan",
        subject_type="location",
        subject_id=location.id,
        detail=asdict(summary),
    )
    return summary


async def scan_due_locations(
    sessionmaker: async_sessionmaker[AsyncSession],
    backend_factory: Callable[[StorageLocation], StorageBackend],
    *,
    now: datetime,
    max_attempts: int,
) -> dict[str, ScanSummary | str]:
    async with sessionmaker() as session:
        locations = (
            await session.scalars(select(StorageLocation).where(StorageLocation.enabled.is_(True)))
        ).all()
    due = [
        loc
        for loc in locations
        if loc.last_scan_at is None
        or loc.last_scan_at + timedelta(seconds=loc.scan_interval_s) <= now
    ]
    results: dict[str, ScanSummary | str] = {}
    for loc in due:
        try:
            async with sessionmaker() as session:
                location = await session.get(StorageLocation, loc.id, with_for_update=True)
                results[loc.name] = await scan_location(
                    session, location, backend_factory(location), now=now, max_attempts=max_attempts
                )
                await session.commit()
        except Exception as exc:
            logger.exception("scanning location %s failed", loc.name)
            results[loc.name] = str(exc)
            async with sessionmaker() as session:
                await session.execute(
                    update(StorageLocation)
                    .where(StorageLocation.id == loc.id)
                    .values(last_scan_at=now, last_scan_error=str(exc)[:2000])
                )
                await session.commit()
    return results
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest packages/leader -q`, `uv run pytest -q`, `uv run ruff check .`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/leader
git commit -m "Leader: consent globbing and the location scanner"
```

---

### Task 9: The application, health checks and file links

**Files:**
- Create: `packages/leader/src/swarmscribe_leader/app.py`
- Create: `packages/leader/src/swarmscribe_leader/api/__init__.py`, `api/deps.py`, `api/errors.py`, `api/health.py`, `api/files.py`
- Test: `packages/leader/tests/test_app.py`

**Interfaces:**
- `swarmscribe_leader.app.create_app(settings: Settings, *, background: bool = True) -> FastAPI`. `app.state` carries `settings`, `engine`, `sessionmaker`, `signer` (`LinkSigner`), `head_revision` (str), `backend_factory` (`Callable[[StorageLocation], StorageBackend]`). With `background=True` the lifespan runs the reaper every `reaper_interval_seconds` and the scanner every `scanner_interval_seconds`, each through `run_exclusive`. The lifespan disposes the engine on exit.
- `api/deps.py`: `async db_session(request) -> AsyncIterator[AsyncSession]`; `settings_of(request) -> Settings`.
- `api/errors.py`: `install(app)` mapping `LeaderError` → its status with `ErrorBody` JSON, `StorageError` → 400 `invalid_key`.
- `GET /healthz` → `200 {"status": "ok"}`. `GET /readyz` → `200 {"status": "ready"}` when the database answers and is at the head revision, else `503 {"status": "<reason>"}`.
- `GET /v1/files/{token}`: verifies the link (method `GET`), the location (must exist and be `local`), and the version (if the link carries one) → file bytes; `412 source_changed` if the file changed; `404` if absent; `403` for an invalid or expired link or wrong method.
- `PUT /v1/files/{token}`: verifies the link (method `PUT`), streams the body to a temporary file beside the target and renames it into place (directories created) → `201`; bodies over 512 MiB → `413`.
- Plan A1 adds the follower routes in Task 10; this task's app has health and files only.

- [ ] **Step 1: Write the failing tests**

`packages/leader/tests/test_app.py`:

```python
import os
from datetime import timedelta

import httpx
import pytest

from swarmscribe_leader.app import create_app
from swarmscribe_leader.config import Settings
from swarmscribe_leader.storage.links import LinkClaims

LINK_KEY = "k" * 32


@pytest.fixture
async def app(engine, migrated_database_url):
    settings = Settings(
        database_url=migrated_database_url, public_url="http://leader", link_key=LINK_KEY
    )
    application = create_app(settings, background=False)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://leader"
    ) as http:
        yield http


def write(root, key, data=b"audio bytes"):
    path = root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


async def test_healthz(client):
    response = await client.get("/healthz")
    assert (response.status_code, response.json()) == (200, {"status": "ok"})


async def test_readyz_when_migrated(client):
    response = await client.get("/readyz")
    assert (response.status_code, response.json()) == (200, {"status": "ready"})


async def test_download_through_a_signed_link(app, client, factory, tmp_path):
    write(tmp_path, "talks/one.mp3", b"the audio")
    location = await factory.location()
    backend = app.state.backend_factory(location)
    info = await backend.stat("talks/one.mp3")
    link = backend.download_link("talks/one.mp3", info.version, timedelta(minutes=5))
    response = await client.get(link.url)
    assert (response.status_code, response.content) == (200, b"the audio")


async def test_a_changed_file_answers_412_source_changed(app, client, factory, tmp_path):
    write(tmp_path, "talks/one.mp3", b"before")
    location = await factory.location()
    backend = app.state.backend_factory(location)
    info = await backend.stat("talks/one.mp3")
    link = backend.download_link("talks/one.mp3", info.version, timedelta(minutes=5))
    write(tmp_path, "talks/one.mp3", b"after, and longer")
    response = await client.get(link.url)
    assert response.status_code == 412
    assert response.json()["code"] == "source_changed"


async def test_a_missing_file_answers_404(app, client, factory):
    location = await factory.location()
    link = app.state.backend_factory(location).download_link("gone.mp3", "1-1", timedelta(minutes=5))
    response = await client.get(link.url)
    assert response.status_code == 404


async def test_upload_through_a_signed_link_creates_directories(app, client, factory, tmp_path):
    location = await factory.location()
    link = app.state.backend_factory(location).upload_link(
        "transcripts/talks/one.mp3.txt", timedelta(minutes=5)
    )
    response = await client.put(link.url, content=b"hello\n")
    assert response.status_code == 201
    assert (tmp_path / "transcripts" / "talks" / "one.mp3.txt").read_bytes() == b"hello\n"
    assert not any(name.endswith(".upload") for _, _, files in os.walk(tmp_path) for name in files)


async def test_a_get_link_cannot_upload_and_a_put_link_cannot_download(app, client, factory, tmp_path):
    write(tmp_path, "talks/one.mp3")
    location = await factory.location()
    backend = app.state.backend_factory(location)
    get_link = backend.download_link("talks/one.mp3", "", timedelta(minutes=5))
    put_link = backend.upload_link("talks/one.mp3", timedelta(minutes=5))
    assert (await client.put(get_link.url, content=b"x")).status_code == 403
    assert (await client.get(put_link.url)).status_code == 403


async def test_forged_and_expired_links_are_refused(app, client, factory, tmp_path):
    write(tmp_path, "talks/one.mp3")
    location = await factory.location()
    expired = app.state.signer.sign(
        LinkClaims(str(location.id), "talks/one.mp3", "GET", "", expires=1)
    )
    for token in ("not-a-token", expired):
        response = await client.get(f"/v1/files/{token}")
        assert response.status_code == 403
        assert response.json()["code"] == "forbidden"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/leader/tests/test_app.py -q`
Expected: `ModuleNotFoundError: No module named 'swarmscribe_leader.app'`

- [ ] **Step 3: Implement**

`api/__init__.py`: empty.

`api/deps.py`:

```python
from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings


async def db_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session


def settings_of(request: Request) -> Settings:
    return request.app.state.settings
```

`api/errors.py`:

```python
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from swarmscribe_protocol import ErrorBody

from ..errors import LeaderError
from ..storage.base import StorageError


def _body(code: str, message: str) -> dict:
    return ErrorBody(code=code, message=message).model_dump()


def install(app: FastAPI) -> None:
    @app.exception_handler(LeaderError)
    async def leader_error(_request: Request, exc: LeaderError) -> JSONResponse:
        return JSONResponse(_body(exc.code, exc.message), status_code=exc.status)

    @app.exception_handler(StorageError)
    async def storage_error(_request: Request, exc: StorageError) -> JSONResponse:
        return JSONResponse(_body("invalid_key", str(exc)), status_code=400)
```

`api/health.py`:

```python
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from ..db.migrate import current_revision

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    engine = request.app.state.engine
    try:
        async with engine.connect() as conn:
            await conn.scalar(text("select 1"))
        revision = await current_revision(engine)
    except Exception:
        return JSONResponse({"status": "database unreachable"}, status_code=503)
    if revision != request.app.state.head_revision:
        return JSONResponse({"status": "database migrations are not current"}, status_code=503)
    return JSONResponse({"status": "ready"})
```

`api/files.py`:

```python
import os
import time
import uuid

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse

from ..db.models import StorageLocation
from ..errors import Forbidden, NotFound, PayloadTooLarge, PreconditionFailed
from ..storage.links import InvalidLink, LinkClaims
from ..storage.local import LocalBackend

router = APIRouter(prefix="/v1/files")

MAX_UPLOAD_BYTES = 512 * 1024 * 1024


def _claims(request: Request, token: str, method: str) -> LinkClaims:
    try:
        claims = request.app.state.signer.verify(token, now=time.time())
    except InvalidLink as exc:
        raise Forbidden("invalid or expired link") from exc
    if claims.method != method:
        raise Forbidden("this link does not allow that method")
    return claims


async def _backend(request: Request, claims: LinkClaims) -> LocalBackend:
    try:
        location_id = uuid.UUID(claims.location_id)
    except ValueError as exc:
        raise NotFound("no such location") from exc
    async with request.app.state.sessionmaker() as session:
        location = await session.get(StorageLocation, location_id)
    if location is None or location.backend != "local":
        raise NotFound("no such location")
    return request.app.state.backend_factory(location)


@router.get("/{token}")
async def download(token: str, request: Request) -> FileResponse:
    claims = _claims(request, token, "GET")
    backend = await _backend(request, claims)
    info = await backend.stat(claims.key)
    if info is None:
        raise NotFound("file not found")
    if claims.version and info.version != claims.version:
        raise PreconditionFailed("the file changed after this link was issued")
    return FileResponse(backend.path_for(claims.key), media_type="application/octet-stream")


@router.put("/{token}", status_code=201)
async def upload(token: str, request: Request) -> Response:
    claims = _claims(request, token, "PUT")
    backend = await _backend(request, claims)
    path = backend.path_for(claims.key)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".upload")
    size = 0
    try:
        with temp.open("wb") as out:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise PayloadTooLarge("upload is larger than 512 MiB")
                out.write(chunk)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return Response(status_code=201)
```

`app.py`:

```python
import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from functools import partial

from fastapi import FastAPI

from .api import errors as api_errors
from .api import files, health
from .background import run_exclusive, run_periodically
from .clock import utcnow
from .config import Settings
from .db.migrate import head_revision
from .db.session import make_engine, make_sessionmaker
from .ingest.scanner import scan_due_locations
from .jobs.reaper import reap
from .storage.links import LinkSigner
from .storage.registry import backend_for


def create_app(settings: Settings, *, background: bool = True) -> FastAPI:
    engine = make_engine(settings.database_url)
    sessionmaker = make_sessionmaker(engine)
    signer = LinkSigner(settings.link_key.encode("utf-8"))
    backend_factory = partial(backend_for, signer=signer, public_url=settings.public_url)

    async def reaper_step() -> None:
        async def work() -> None:
            async with sessionmaker() as session:
                await reap(
                    session,
                    now=utcnow(),
                    gone_after=timedelta(seconds=settings.follower_gone_after_seconds),
                )
                await session.commit()

        await run_exclusive(engine, "reaper", work)

    async def scanner_step() -> None:
        async def work() -> None:
            await scan_due_locations(
                sessionmaker, backend_factory, now=utcnow(), max_attempts=settings.max_attempts
            )

        await run_exclusive(engine, "scanner", work)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        stop = asyncio.Event()
        tasks = []
        if background:
            tasks = [
                asyncio.create_task(
                    run_periodically(stop, settings.reaper_interval_seconds, reaper_step, "reaper")
                ),
                asyncio.create_task(
                    run_periodically(stop, settings.scanner_interval_seconds, scanner_step, "scanner")
                ),
            ]
        try:
            yield
        finally:
            stop.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            await engine.dispose()

    app = FastAPI(title="SwarmScribe leader", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = sessionmaker
    app.state.signer = signer
    app.state.head_revision = head_revision()
    app.state.backend_factory = backend_factory
    api_errors.install(app)
    app.include_router(health.router)
    app.include_router(files.router)
    return app
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest packages/leader -q`, `uv run pytest -q`, `uv run ruff check .`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/leader
git commit -m "Leader: application, health checks and signed file links"
```

---

### Task 10: The follower API

**Files:**
- Create: `packages/leader/src/swarmscribe_leader/jobs/claims.py`, `api/follower.py`
- Modify: `packages/leader/src/swarmscribe_leader/app.py` (include the follower router)
- Test: `packages/leader/tests/test_follower_api.py`

**Interfaces:**
- `jobs/claims.py`:
  - `output_keys(prefix: str, key: str) -> dict[str, str]` → `{"txt": prefix+key+".txt", "srt": ...".srt", "segments_json": ...".segments.json"}`
  - `async build_claim(session, job, follower, *, settings, backend_factory) -> ClaimResponse` — sets `job.settings_profile_id` and `job.vocabulary_version = 0` (the merged vocabulary arrives in Plan B; the claim carries `Vocabulary(version=0)`).
  - `async outputs_present(session, job, *, backend_factory) -> bool` — all three outputs exist with non-zero size.
- `api/follower.py` (`APIRouter(prefix="/v1")`), every route except register authenticated by `Authorization: Bearer <credential>`:
  - `POST /followers/register` (`RegisterRequest` → `RegisterResponse`)
  - `POST /jobs/claim` → `ClaimResponse`, or `204` with `Retry-After: <claim_retry_after>` (also for a draining follower)
  - `POST /jobs/{job_id}/heartbeat` (`HeartbeatRequest` → `HeartbeatResponse`)
  - `POST /jobs/{job_id}/submit` (`SubmitRequest` → `SubmitResponse(accepted=True)`)
  - `POST /jobs/{job_id}/fail` (`FailRequest` → `204`)
  - `POST /jobs/{job_id}/release` (`ReleaseRequest` → `204`)
  - `POST /followers/deregister` → `204` (releases any lease, state `gone`)

- [ ] **Step 1: Write the failing tests**

`packages/leader/tests/test_follower_api.py`:

```python
import hashlib
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from swarmscribe_leader.app import create_app
from swarmscribe_leader.auth.followers import create_join_token
from swarmscribe_leader.clock import utcnow
from swarmscribe_leader.config import Settings
from swarmscribe_leader.db.models import Follower, Job, StorageLocation
from swarmscribe_leader.ingest.scanner import scan_location
from swarmscribe_leader.storage.links import LinkSigner
from swarmscribe_leader.storage.registry import backend_for
from swarmscribe_protocol import ClaimResponse

CAPABILITIES = {"device": "cpu", "models": ["distil-large-v3"], "engine_version": "0.1.0", "pool": "default"}


@pytest.fixture
async def app(engine, migrated_database_url):
    settings = Settings(
        database_url=migrated_database_url, public_url="http://leader", link_key="k" * 32
    )
    application = create_app(settings, background=False)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://leader"
    ) as http:
        yield http


def write(root, key, data=b"audio bytes"):
    path = root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def join_token(sessionmaker, *, max_uses=5) -> str:
    async with sessionmaker() as session:
        _, plaintext = await create_join_token(
            session, pool="default", expires_at=utcnow() + timedelta(days=1), max_uses=max_uses, created_by="test"
        )
        await session.commit()
    return plaintext


async def register(client, sessionmaker, **capabilities) -> dict:
    token = await join_token(sessionmaker)
    response = await client.post(
        "/v1/followers/register",
        json={"join_token": token, "protocol_version": 1, "capabilities": {**CAPABILITIES, **capabilities}},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['credential']}"}


async def queue_one(sessionmaker, factory, tmp_path, key="talks/one.mp3", data=b"audio bytes"):
    write(tmp_path, "consent.txt", b"**/*.mp3\n")
    write(tmp_path, key, data)
    location = await factory.location()
    async with sessionmaker() as session:
        loc = await session.get(StorageLocation, location.id)
        backend = backend_for(loc, signer=LinkSigner(b"k" * 32), public_url="http://leader")
        await scan_location(session, loc, backend, now=utcnow(), max_attempts=3)
        await session.commit()
    return location


async def claim(client, headers) -> ClaimResponse:
    response = await client.post("/v1/jobs/claim", headers=headers)
    assert response.status_code == 200, response.text
    return ClaimResponse.model_validate(response.json())


async def upload_outputs(client, claimed: ClaimResponse) -> dict[str, str]:
    checksums = {}
    for name in ("txt", "srt", "segments_json"):
        body = f"{name} output\n".encode()
        link = getattr(claimed.upload_urls, name)
        assert (await client.put(link.url, content=body)).status_code == 201
        checksums[name] = sha(body)
    return checksums


async def test_register_returns_a_credential_and_timings(client, sessionmaker):
    token = await join_token(sessionmaker)
    response = await client.post(
        "/v1/followers/register",
        json={"join_token": token, "protocol_version": 1, "capabilities": CAPABILITIES},
    )
    body = response.json()
    assert response.status_code == 200
    assert (body["heartbeat_interval"], body["lease_seconds"]) == (30, 120)
    assert len(body["credential"]) >= 43


async def test_register_errors_use_the_error_body(client, sessionmaker):
    response = await client.post(
        "/v1/followers/register",
        json={"join_token": "nope", "protocol_version": 1, "capabilities": CAPABILITIES},
    )
    assert (response.status_code, response.json()["code"]) == (401, "unauthorized")
    token = await join_token(sessionmaker)
    response = await client.post(
        "/v1/followers/register",
        json={"join_token": token, "protocol_version": 99, "capabilities": CAPABILITIES},
    )
    assert (response.status_code, response.json()["code"]) == (409, "protocol_version")


async def test_calls_without_a_valid_credential_are_refused(client, sessionmaker, factory):
    assert (await client.post("/v1/jobs/claim")).status_code == 401
    bad = {"Authorization": "Bearer nonsense"}
    assert (await client.post("/v1/jobs/claim", headers=bad)).status_code == 401
    _, credential = await factory.follower(state="revoked")
    revoked = {"Authorization": f"Bearer {credential}"}
    response = await client.post("/v1/jobs/claim", headers=revoked)
    assert (response.status_code, response.json()["code"]) == (403, "forbidden")


async def test_claim_with_nothing_queued_is_204_with_retry_after(client, sessionmaker):
    headers = await register(client, sessionmaker)
    response = await client.post("/v1/jobs/claim", headers=headers)
    assert response.status_code == 204
    assert response.headers["retry-after"] == "10"


async def test_the_whole_happy_path(client, sessionmaker, factory, tmp_path):
    await queue_one(sessionmaker, factory, tmp_path, data=b"the recording")
    headers = await register(client, sessionmaker)
    claimed = await claim(client, headers)
    assert claimed.settings.model == "distil-large-v3"
    assert claimed.settings.compute_type == "int8"
    assert claimed.vocabulary.version == 0
    download = await client.get(claimed.download_url.url)
    assert download.content == b"the recording"
    beat = await client.post(
        f"/v1/jobs/{claimed.job_id}/heartbeat", headers=headers, json={"lease_id": claimed.lease_id, "progress": 0.5}
    )
    assert beat.json() == {"directive": "continue"}
    checksums = await upload_outputs(client, claimed)
    assert (tmp_path / "transcripts" / "talks" / "one.mp3.segments.json").exists()
    submit = await client.post(
        f"/v1/jobs/{claimed.job_id}/submit",
        headers=headers,
        json={"lease_id": claimed.lease_id, "checksums": {"source": sha(b"the recording"), **checksums}},
    )
    assert submit.json() == {"accepted": True}
    async with sessionmaker() as session:
        (job,) = (await session.scalars(select(Job))).all()
    assert job.state == "completed"


async def test_submit_before_uploading_is_409_outputs_missing(client, sessionmaker, factory, tmp_path):
    await queue_one(sessionmaker, factory, tmp_path)
    headers = await register(client, sessionmaker)
    claimed = await claim(client, headers)
    h = "a" * 64
    response = await client.post(
        f"/v1/jobs/{claimed.job_id}/submit",
        headers=headers,
        json={"lease_id": claimed.lease_id, "checksums": {"source": h, "txt": h, "srt": h, "segments_json": h}},
    )
    assert (response.status_code, response.json()["code"]) == (409, "outputs_missing")


async def test_a_stale_lease_is_409(client, sessionmaker, factory, tmp_path):
    await queue_one(sessionmaker, factory, tmp_path)
    headers = await register(client, sessionmaker)
    claimed = await claim(client, headers)
    response = await client.post(
        f"/v1/jobs/{claimed.job_id}/heartbeat",
        headers=headers,
        json={"lease_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert (response.status_code, response.json()["code"]) == (409, "stale_lease")


async def test_fail_with_undecodable_fails_the_job(client, sessionmaker, factory, tmp_path):
    await queue_one(sessionmaker, factory, tmp_path)
    headers = await register(client, sessionmaker)
    claimed = await claim(client, headers)
    response = await client.post(
        f"/v1/jobs/{claimed.job_id}/fail",
        headers=headers,
        json={"lease_id": claimed.lease_id, "code": "undecodable", "reason": "not audio", "retryable": False},
    )
    assert response.status_code == 204
    async with sessionmaker() as session:
        assert (await session.scalars(select(Job))).one().state == "failed"


async def test_release_requeues(client, sessionmaker, factory, tmp_path):
    await queue_one(sessionmaker, factory, tmp_path)
    headers = await register(client, sessionmaker)
    claimed = await claim(client, headers)
    response = await client.post(
        f"/v1/jobs/{claimed.job_id}/release", headers=headers, json={"lease_id": claimed.lease_id}
    )
    assert response.status_code == 204
    async with sessionmaker() as session:
        assert (await session.scalars(select(Job))).one().state == "queued"


async def test_a_draining_follower_gets_no_work(client, sessionmaker, factory, tmp_path):
    await queue_one(sessionmaker, factory, tmp_path)
    headers = await register(client, sessionmaker)
    async with sessionmaker() as session:
        follower = (await session.scalars(select(Follower))).one()
        follower.state = "draining"
        await session.commit()
    assert (await client.post("/v1/jobs/claim", headers=headers)).status_code == 204


async def test_deregister_releases_the_lease(client, sessionmaker, factory, tmp_path):
    await queue_one(sessionmaker, factory, tmp_path)
    headers = await register(client, sessionmaker)
    await claim(client, headers)
    assert (await client.post("/v1/followers/deregister", headers=headers)).status_code == 204
    async with sessionmaker() as session:
        assert (await session.scalars(select(Job))).one().state == "queued"
        assert (await session.scalars(select(Follower))).one().state == "gone"


async def test_a_cuda_follower_gets_the_cuda_profile(client, sessionmaker, factory, tmp_path):
    await queue_one(sessionmaker, factory, tmp_path)
    headers = await register(client, sessionmaker, device="cuda")
    claimed = await claim(client, headers)
    assert (claimed.settings.model, claimed.settings.compute_type) == ("large-v3", "float16")


async def test_an_invalid_job_id_is_422(client, sessionmaker):
    headers = await register(client, sessionmaker)
    response = await client.post("/v1/jobs/not-a-uuid/heartbeat", headers=headers, json={"lease_id": "x"})
    assert response.status_code == 422
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/leader/tests/test_follower_api.py -q`
Expected: failures with `404 Not Found` responses (the routes do not exist yet) — record the summary line.

- [ ] **Step 3: Implement**

`jobs/claims.py`:

```python
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from swarmscribe_protocol import ClaimResponse, JobSettings, UploadUrls, Vocabulary

from ..config import Settings
from ..db.models import Follower, Job, Recording, SettingsProfile, StorageLocation
from ..storage.base import StorageBackend

BackendFactory = Callable[[StorageLocation], StorageBackend]


def output_keys(prefix: str, key: str) -> dict[str, str]:
    base = prefix + key
    return {"txt": base + ".txt", "srt": base + ".srt", "segments_json": base + ".segments.json"}


async def _places(
    session: AsyncSession, job: Job
) -> tuple[Recording, StorageLocation, StorageLocation]:
    recording = await session.get(Recording, job.recording_id)
    source = await session.get(StorageLocation, recording.location_id)
    target = source
    if source.output_location_id is not None:
        target = await session.get(StorageLocation, source.output_location_id)
    return recording, source, target


async def build_claim(
    session: AsyncSession,
    job: Job,
    follower: Follower,
    *,
    settings: Settings,
    backend_factory: BackendFactory,
) -> ClaimResponse:
    recording, source, target = await _places(session, job)
    download = backend_factory(source).download_link(
        recording.key, job.source_version, timedelta(seconds=settings.download_link_ttl_seconds)
    )
    uploader = backend_factory(target)
    upload_ttl = timedelta(seconds=settings.upload_link_ttl_seconds)
    uploads = UploadUrls(
        **{
            name: uploader.upload_link(key, upload_ttl)
            for name, key in output_keys(source.output_prefix, recording.key).items()
        }
    )
    device = follower.capabilities.get("device", "cpu")
    profile = await session.scalar(select(SettingsProfile).where(SettingsProfile.device == device))
    job.settings_profile_id = profile.id
    job.vocabulary_version = 0
    return ClaimResponse(
        job_id=str(job.id),
        lease_id=str(job.lease_id),
        download_url=download,
        upload_urls=uploads,
        settings=JobSettings(
            model=profile.model,
            compute_type=profile.compute_type,
            temperatures=tuple(profile.temperatures),
        ),
        vocabulary=Vocabulary(version=0),
        source_version=job.source_version,
    )


async def outputs_present(
    session: AsyncSession, job: Job, *, backend_factory: BackendFactory
) -> bool:
    recording, source, target = await _places(session, job)
    backend = backend_factory(target)
    for key in output_keys(source.output_prefix, recording.key).values():
        info = await backend.stat(key)
        if info is None or info.size == 0:
            return False
    return True
```

`api/follower.py`:

```python
import uuid

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from swarmscribe_protocol import (
    ClaimResponse,
    FailRequest,
    HeartbeatRequest,
    HeartbeatResponse,
    RegisterRequest,
    RegisterResponse,
    ReleaseRequest,
    SubmitRequest,
    SubmitResponse,
)

from .. import audit
from ..auth.followers import authenticate, register
from ..clock import utcnow
from ..db.models import Follower, Job
from ..errors import Unauthorized
from ..jobs import store
from ..jobs.claims import build_claim, outputs_present
from .deps import db_session, settings_of

router = APIRouter(prefix="/v1")


async def current_follower(
    request: Request, session: AsyncSession = Depends(db_session)
) -> Follower:
    scheme, _, credential = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not credential.strip():
        raise Unauthorized("missing follower credential")
    return await authenticate(session, credential.strip(), now=utcnow())


def _no_work(request: Request) -> Response:
    return Response(
        status_code=204, headers={"Retry-After": str(settings_of(request).claim_retry_after)}
    )


@router.post("/followers/register", response_model=RegisterResponse)
async def register_follower(
    body: RegisterRequest, request: Request, session: AsyncSession = Depends(db_session)
) -> RegisterResponse:
    settings = settings_of(request)
    follower, credential = await register(session, body, now=utcnow())
    await session.commit()
    return RegisterResponse(
        follower_id=str(follower.id),
        credential=credential,
        heartbeat_interval=settings.heartbeat_seconds,
        lease_seconds=settings.lease_seconds,
    )


@router.post("/jobs/claim", response_model=ClaimResponse)
async def claim_job(
    request: Request,
    session: AsyncSession = Depends(db_session),
    follower: Follower = Depends(current_follower),
):
    settings = settings_of(request)
    if follower.state == "draining":
        await session.commit()
        return _no_work(request)
    job = await store.claim(session, follower, now=utcnow(), lease_seconds=settings.lease_seconds)
    if job is None:
        await session.commit()
        return _no_work(request)
    claim = await build_claim(
        session, job, follower, settings=settings, backend_factory=request.app.state.backend_factory
    )
    await session.commit()
    return claim


@router.post("/jobs/{job_id}/heartbeat", response_model=HeartbeatResponse)
async def heartbeat(
    job_id: uuid.UUID,
    body: HeartbeatRequest,
    request: Request,
    session: AsyncSession = Depends(db_session),
    follower: Follower = Depends(current_follower),
) -> HeartbeatResponse:
    directive = await store.heartbeat(
        session, job_id, body.lease_id, follower, now=utcnow(), lease_seconds=settings_of(request).lease_seconds
    )
    await session.commit()
    return HeartbeatResponse(directive=directive)


@router.post("/jobs/{job_id}/submit", response_model=SubmitResponse)
async def submit(
    job_id: uuid.UUID,
    body: SubmitRequest,
    request: Request,
    session: AsyncSession = Depends(db_session),
    follower: Follower = Depends(current_follower),
) -> SubmitResponse:
    async def present(job: Job) -> bool:
        return await outputs_present(session, job, backend_factory=request.app.state.backend_factory)

    await store.submit(session, job_id, body, follower, now=utcnow(), outputs_present=present)
    await session.commit()
    return SubmitResponse(accepted=True)


@router.post("/jobs/{job_id}/fail", status_code=204)
async def fail(
    job_id: uuid.UUID,
    body: FailRequest,
    session: AsyncSession = Depends(db_session),
    follower: Follower = Depends(current_follower),
) -> Response:
    await store.fail(session, job_id, body, follower, now=utcnow())
    await session.commit()
    return Response(status_code=204)


@router.post("/jobs/{job_id}/release", status_code=204)
async def release(
    job_id: uuid.UUID,
    body: ReleaseRequest,
    session: AsyncSession = Depends(db_session),
    follower: Follower = Depends(current_follower),
) -> Response:
    await store.release(session, job_id, body.lease_id, follower, now=utcnow())
    await session.commit()
    return Response(status_code=204)


@router.post("/followers/deregister", status_code=204)
async def deregister(
    session: AsyncSession = Depends(db_session),
    follower: Follower = Depends(current_follower),
) -> Response:
    released = await store.release_all(session, follower, now=utcnow())
    follower.state = "gone"
    audit.record(
        session,
        actor=f"follower:{follower.id}",
        action="follower.deregister",
        subject_type="follower",
        subject_id=follower.id,
        detail={"released": released},
    )
    await session.commit()
    return Response(status_code=204)
```

In `app.py`: add `follower` to the `from .api import files, health` import and `app.include_router(follower.router)` after the files router.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest packages/leader -q`, `uv run pytest -q`, `uv run ruff check .`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/leader
git commit -m "Leader: follower API — register, claim, heartbeat, submit, fail, release"
```

---

### Task 11: End-to-end proof, server entry point and README

**Files:**
- Create: `packages/leader/src/swarmscribe_leader/main.py`
- Modify: `README.md`
- Test: `packages/leader/tests/test_end_to_end.py`, `packages/leader/tests/test_main.py`

**Interfaces:**
- `swarmscribe_leader.main.main(argv: Sequence[str] | None = None) -> int` with subcommands `migrate` (upgrades the database, prints the revision) and `serve [--host 0.0.0.0] [--port 8080]` (refuses with exit code 2 and a one-line message if the database is not at the head revision; otherwise runs uvicorn). `run()` calls `sys.exit(main())` and is the `swarmscribe-leader` console script.
- The end-to-end test runs the real application with background loops (short intervals) and two scripted followers over `httpx.ASGITransport`; no engine, no model.

- [ ] **Step 1: Write the failing tests**

`packages/leader/tests/test_end_to_end.py`:

```python
import asyncio
import hashlib
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from swarmscribe_leader.app import create_app
from swarmscribe_leader.auth.followers import create_join_token
from swarmscribe_leader.clock import utcnow
from swarmscribe_leader.config import Settings
from swarmscribe_leader.db.models import Job, JobAttempt, Recording
from swarmscribe_protocol import ClaimResponse

CAPABILITIES = {"device": "cpu", "models": ["distil-large-v3"], "engine_version": "0.1.0", "pool": "default"}


def write(root, key, data):
    path = root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


async def follower_session(client, token) -> dict:
    response = await client.post(
        "/v1/followers/register",
        json={"join_token": token, "protocol_version": 1, "capabilities": CAPABILITIES},
    )
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['credential']}"}


async def work_until_idle(client, headers, *, idle_rounds=30):
    """A well-behaved fake follower: claim, download, 'transcribe', upload, submit."""
    idle = 0
    while idle < idle_rounds:
        response = await client.post("/v1/jobs/claim", headers=headers)
        if response.status_code == 204:
            idle += 1
            await asyncio.sleep(0.1)
            continue
        idle = 0
        claim = ClaimResponse.model_validate(response.json())
        source = (await client.get(claim.download_url.url)).content
        checksums = {"source": hashlib.sha256(source).hexdigest()}
        for name in ("txt", "srt", "segments_json"):
            body = f"{name} of {len(source)} bytes\n".encode()
            (await client.put(getattr(claim.upload_urls, name).url, content=body)).raise_for_status()
            checksums[name] = hashlib.sha256(body).hexdigest()
        result = await client.post(
            f"/v1/jobs/{claim.job_id}/submit",
            headers=headers,
            json={"lease_id": claim.lease_id, "checksums": checksums},
        )
        result.raise_for_status()


async def test_consented_recordings_complete_even_when_a_follower_dies(engine, migrated_database_url, factory, sessionmaker, tmp_path):
    write(tmp_path, "consent.txt", b"talks/*.mp3\n")
    for name, size in (("a", 10), ("b", 20), ("c", 30)):
        write(tmp_path, f"talks/{name}.mp3", b"x" * size)
    write(tmp_path, "private/secret.mp3", b"never")
    write(tmp_path, "talks/notes.txt", b"not audio")
    await factory.location(scan_interval_s=0)

    settings = Settings(
        database_url=migrated_database_url,
        public_url="http://leader",
        link_key="k" * 32,
        lease_seconds=2,
        heartbeat_seconds=1,
        reaper_interval_seconds=0.2,
        scanner_interval_seconds=0.2,
        claim_retry_after=1,
    )
    app = create_app(settings, background=True)
    async with sessionmaker() as session:
        _, token = await create_join_token(
            session, pool="default", expires_at=utcnow() + timedelta(hours=1), max_uses=10, created_by="test"
        )
        await session.commit()

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://leader") as client:
            # The doomed follower claims one job and then vanishes without a word.
            doomed = await follower_session(client, token)
            for _ in range(50):
                response = await client.post("/v1/jobs/claim", headers=doomed)
                if response.status_code == 200:
                    abandoned = response.json()["job_id"]
                    break
                await asyncio.sleep(0.1)
            else:
                pytest.fail("the scanner never queued anything")

            healthy = await follower_session(client, token)
            await asyncio.wait_for(work_until_idle(client, healthy), timeout=40)

    async with sessionmaker() as session:
        jobs = (await session.scalars(select(Job))).all()
        recordings = {r.id: r for r in (await session.scalars(select(Recording))).all()}
        attempts = (await session.scalars(select(JobAttempt))).all()
    completed = [j for j in jobs if j.state == "completed"]
    assert sorted(recordings[j.recording_id].key for j in completed) == [
        "talks/a.mp3",
        "talks/b.mp3",
        "talks/c.mp3",
    ]
    assert all(j.state == "completed" for j in jobs)
    secret = [r for r in recordings.values() if r.key == "private/secret.mp3"]
    assert [r.consent for r in secret] == ["not_consented"]
    assert {str(a.job_id) for a in attempts if a.outcome == "expired"} == {abandoned}
    for name in ("a", "b", "c"):
        assert (tmp_path / "transcripts" / "talks" / f"{name}.mp3.segments.json").exists()
```

`packages/leader/tests/test_main.py`:

```python
from swarmscribe_leader import main as entry


def test_serve_refuses_an_unmigrated_database(monkeypatch, capsys):
    monkeypatch.setenv("SWARMSCRIBE_DATABASE_URL", "postgresql://u:p@127.0.0.1:1/none")
    monkeypatch.setenv("SWARMSCRIBE_PUBLIC_URL", "http://leader")
    monkeypatch.setenv("SWARMSCRIBE_LINK_KEY", "k" * 32)

    async def not_current(_settings):
        return False

    monkeypatch.setattr(entry, "_database_is_current", not_current)
    assert entry.main(["serve"]) == 2
    assert "migrate" in capsys.readouterr().err


def test_migrate_upgrades_and_reports_the_revision(monkeypatch, capsys, migrated_database_url):
    monkeypatch.setenv("SWARMSCRIBE_DATABASE_URL", migrated_database_url)
    monkeypatch.setenv("SWARMSCRIBE_PUBLIC_URL", "http://leader")
    monkeypatch.setenv("SWARMSCRIBE_LINK_KEY", "k" * 32)
    assert entry.main(["migrate"]) == 0
    assert "0001" in capsys.readouterr().out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/leader/tests/test_end_to_end.py packages/leader/tests/test_main.py -q`
Expected: `test_main.py` fails with `ImportError: cannot import name 'main'`; record whether `test_end_to_end.py` already passes (it exercises Tasks 1–10 only; if it fails, the failure is a real defect to report, not something to work around).

- [ ] **Step 3: Implement `main.py`**

```python
import argparse
import asyncio
import sys
from collections.abc import Sequence

from .config import Settings
from .db.migrate import current_revision, head_revision, upgrade
from .db.session import make_engine


async def _database_is_current(settings: Settings) -> bool:
    engine = make_engine(settings.database_url)
    try:
        return await current_revision(engine) == head_revision()
    except Exception:
        return False
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="swarmscribe-leader")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="bring the database schema up to date")
    serve = commands.add_parser("serve", help="run the leader")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
    settings = Settings()

    if args.command == "migrate":
        upgrade(settings.database_url)
        print(f"database is at revision {head_revision()}")
        return 0

    if not asyncio.run(_database_is_current(settings)):
        print(
            "error: the database is not at the current schema; run `swarmscribe-leader migrate`",
            file=sys.stderr,
        )
        return 2

    import uvicorn

    from .app import create_app

    uvicorn.run(create_app(settings), host=args.host, port=args.port, proxy_headers=True)
    return 0


def run() -> None:
    sys.exit(main())
```

- [ ] **Step 4: Update the README**

In `README.md`, set the `swarmscribe-leader` row of the Status table to `Core built (local storage); sign-in and admin tools next`, and add this section after "Transcribe one file":

````markdown
## Run the leader (development)

The leader needs Postgres. Set:

| Variable | Meaning |
|---|---|
| `SWARMSCRIBE_DATABASE_URL` | e.g. `postgresql://user:pass@host:5432/swarmscribe` |
| `SWARMSCRIBE_PUBLIC_URL` | the leader's external base URL, used in file links |
| `SWARMSCRIBE_LINK_KEY` | at least 32 random characters, used to sign file links |

```
uv run swarmscribe-leader migrate
uv run swarmscribe-leader serve --port 8080
```

A location is a folder with a `consent.txt` at its root listing which
recordings may be processed, one glob per line (`talks/*.mp3`, `2024/**`).
Nothing else is ever queued. Transcripts are written beside the recordings
under `transcripts/`.

Admin commands (adding locations, creating join tokens) arrive with the next
plan; until then, locations and tokens are created directly in the database.

Tests use a real Postgres: set `SWARMSCRIBE_TEST_DATABASE_URL`, or leave it
unset and an embedded one starts automatically in `.pgdata/`.
````

- [ ] **Step 5: Run everything**

Run: `uv run pytest -q`, `uv run pytest -m smoke -q`, `uv run ruff check .`
Expected: all pass. Record the end-to-end test's duration from `pytest --durations=5`.

- [ ] **Step 6: Commit**

```bash
git add packages/leader README.md
git commit -m "Leader: end-to-end proof with a dying follower, migrate/serve entry point, README"
```
