# Follower F0 — Leader, Protocol and Engine Changes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the leader, the protocol and the engine what the follower agent needs before it can be built: pool tokens, fresh links for a lease holder, a drain signal an idle follower can hear, a command to set settings profiles, follower rows that do not grow with every pod, and an engine that reports progress and can be stopped.

**Architecture:** Every change is additive. The engine's `Transcriber` gains an optional `progress` callback (which is also how a transcription is stopped), `warm_up()` and `close()`. The protocol gains two models and one header name; `PROTOCOL_VERSION` stays 1. The leader gains one migration (`0006`), one follower route (`POST /v1/jobs/{id}/links`), one response header on an existing answer, and five admin routes that are for people only, so the fleet console's allow-list does not change.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2 (async, asyncpg), Alembic, Pydantic v2, pytest with a real Postgres (`pgserver`, or `SWARMSCRIBE_TEST_DATABASE_URL`), `uv`, `ruff`.

**Spec:** `docs/superpowers/specs/2026-10-04-follower-design.md`, section 12 (and 5.5, 13, 14, 15), with `docs/superpowers/specs/2026-10-02-leader-design.md` (sections 4, 6, 9, 10) and `docs/superpowers/specs/2026-10-02-swarmscribe-architecture-design.md` (the master spec).

**This plan is the first of five.** F1 (`2026-10-04-follower-f1-agent-core.md`) builds the agent on top of it and needs every task here. F2 to F4 (images and Compose, the chart, the outside-machine install) are written later.

## Global Constraints

- SwarmScribe is general-purpose: nothing in code, tests, fixtures or documentation may be specific to one kind of content or organisation (master spec 1).
- `PROTOCOL_VERSION` stays `1`: "the protocol has not shipped" (follower spec 12). The schema snapshot `packages/protocol/tests/schema_v1.json` is regenerated, not hand-edited.
- The engine imports nothing internal (master spec 4). The leader never imports the engine.
- The leader's migration head is `0005`; this plan adds exactly one migration, `0006`. `test_migrations_produce_exactly_the_models` must stay green: models and migration change together.
- Secrets are shown once and stored only as their SHA-256 (`auth/secrets.py`: `new_secret()`, `hash_secret()`). A pool token, like a join token, never appears in a log, an audit entry or a listing.
- Logs and audit entries never contain links: "never transcript text, never credentials or links" (leader spec 12).
- Errors use one body, `{"code", "message"}` (leader spec 6). New codes in this plan: `too_many_requests` (429), `exists` (409, reused), `invalid_name` (422, reused).
- **The fleet console's allow-list does not change.** `packages/console/tests/test_proxy.py::test_the_allow_list_is_exactly_the_leaders_delegable_admin_routes_with_their_roles` compares the console's `ROUTES` with every leader admin route that a console may be delegated, and `packages/console-web/src/api/roles.test.ts` compares the web app's role map with `ROUTES`. The five admin routes added here are declared with `consoles_allowed=False`, which that test treats as deliberately excluded. `packages/console/src/swarmscribe_console/proxy.py` and `packages/console-web/src/api/roles.ts` must not be touched; Tasks 5 and 8 run the console's test to prove it.
- Python `>=3.11`; `ruff` line length 100, rules `E, F, I, UP, B`. `uv run ruff check .` must pass after every task.
- Tests run against a real Postgres. On the Windows development machine `uv` is run as `python -m uv` (for example `python -m uv run pytest ...`); the commands below are written `uv run ...`.
- Other work happens in this repository at the same time. Start from an up-to-date `main`, on a new branch `follower-f0`. Never discard a hunk you did not write.

## Rulings

Decisions this plan makes. Those marked **(owner)** are the owner's to overturn; the plan says what changes if the answer is different. Rulings R1 and R2 of the follower spec (a non-expiring pool token; fresh links on request) are the owner's own, made 2026-10-04.

1. **A pool token is presented as a join token.** The follower sends it in `RegisterRequest.join_token`; the leader looks the secret up among join tokens, then among pool tokens. The follower has one code path and the protocol's register models do not change.
2. **Pool tokens have their own table**, `pool_tokens`, not a nullable expiry on `join_tokens`: `TokenOut.expires_at` and `max_uses` stay non-null, so the console and the web app, which list join tokens, see nothing change.
3. **Pool tokens are named, and names are never reused** (like console credentials): the audit log names them.
4. **Pool tokens and settings profiles are for people, never a console (owner).** Their five routes use `require(..., consoles_allowed=False)`. If overturned: declare them with the plain `Administrator`/`Viewer` dependencies, add five `ProxyRoute` entries to `packages/console/src/swarmscribe_console/proxy.py` (with `"name"` and `"device"` in `PARAMETERS`), five entries to `packages/console-web/src/api/roles.ts`, and pages for them.
5. **Revoking a pool token leaves its followers at work unless asked (owner).** `revoke_followers: true` (CLI `--revoke-followers`) revokes every follower the token registered, each with its own audit entry and its leases released.
6. **Follower rows are reused, not deleted (owner).** A registration with a pool token takes over the row of a `gone` follower of the same token that holds no lease. Join-token followers are never reused: an outside machine keeps its identity however long it is off. The alternative (delete old `gone` rows) needs `job_attempts.follower_id` and `jobs.leased_by` to let go of them, and loses machines switched off for longer than the retention.
7. **The drain signal is a response header on the claim's `204`**: `X-SwarmScribe-Directive: drain`. An idle heartbeat would be a new route and a second poll; a `204` has no body, and changing the status would break existing callers (`e2e/compose/run_e2e.py` among them).
8. **Fresh links are bounded to one issue per lease per `SWARMSCRIBE_LINKS_REFRESH_MIN_SECONDS` (60); the claim is the first (owner).** Sooner is `429` with `Retry-After`. `0` turns the bound off (tests use it).
9. **Fresh links do not extend the lease.** That stays the heartbeat's job, so that a follower cannot keep a lease alive without reporting.
10. **The public URL needs no code.** The leader's own links are built from `SWARMSCRIBE_PUBLIC_URL`; it must be an address every follower can reach. The README says so (Task 9); F1's contract test and F2's Compose test set it accordingly.
11. **The stored capabilities carry the token's pool**, not the pool the follower reported (a carried-forward item of the leader core plan). The claim already used the token's pool; now the listing agrees with it.
12. **A leader test kit is a plain module**, `packages/leader/tests/leader_testkit.py`, because pytest fixtures do not cross packages (`--import-mode=importlib`). The leader's `conftest.py` is not rewritten to use it; it only puts the folder on `sys.path`.

## Review Focus

Inputs and conditions the spec implies and that are most likely to bite a person running this, each pinned by a test in the task that owns the code:

1. **Two pods registering at the same moment with one `gone` row to reuse**: they must never get the same row or the same credential — Task 4, `test_two_registrations_at_once_never_share_a_row`.
2. **A machine that was thought gone and is still alive**: after its row was reused its old credential must stop working (`401`), never act as the new follower — Task 4, `test_a_gone_followers_row_is_reused_and_its_old_credential_dies`.
3. **A follower asking for fresh links in a loop** (a bug, or an attack with a stolen credential): bounded per lease, answered `429` with `Retry-After`, and an audit entry only for links actually issued — Task 6, `test_fresh_links_are_bounded_per_lease`.
4. **Fresh links for a lease that has ended** (cancelled, expired and re-leased, revoked follower): refused, and links issued before the lease ended stop working with it — Task 6, `test_the_old_holders_fresh_links_die_with_its_lease`, `test_a_job_that_is_no_longer_leased_gets_no_links`.
5. **A profile whose model is a path** (`/models/x`, `..\x`, `a/b/c`): refused with `422` and nothing changed; followers would otherwise be told to load from their own disks — Task 8, `test_invalid_profiles_are_refused_and_change_nothing`.
6. **A progress callback that raises something the decoder also raises**: it must reach the caller unchanged, never as `UndecodableAudioError`, or a stopped job would be failed for good as undecodable — Task 1, `test_a_decode_error_raised_by_the_callback_is_not_called_undecodable_audio`.
7. **A drained follower that deregisters and comes back**: it is still told `drain` — Task 7, `test_a_drain_survives_deregistering_and_coming_back`.

## File Structure

| File | Responsibility |
|---|---|
| `packages/engine/src/swarmscribe_engine/transcriber.py` (modify) | `progress` callback, `warm_up()`, `close()` |
| `packages/engine/tests/test_transcriber.py` (modify) | their tests |
| `packages/protocol/src/swarmscribe_protocol/messages.py`, `__init__.py`, `schema.py` (modify) | `LinksRequest`, `JobLinks`, `DIRECTIVE_HEADER` |
| `packages/protocol/tests/test_messages.py` (modify), `schema_v1.json` (regenerate) | their tests; the snapshot |
| `packages/leader/src/swarmscribe_leader/db/models.py` (modify) | `PoolToken`; `Follower.pool_token_id`; `Job.links_issued_at` |
| `packages/leader/src/swarmscribe_leader/db/migrations/versions/0006_follower_support.py` (new) | the migration |
| `packages/leader/src/swarmscribe_leader/auth/followers.py` (modify) | register with a pool token; reuse a `gone` row; record the token's pool |
| `packages/leader/src/swarmscribe_leader/auth/pool_tokens.py` (new) | create and revoke pool tokens |
| `packages/leader/src/swarmscribe_leader/reports.py`, `api/admin_models.py`, `api/admin.py` (modify) | pool-token and profile views, bodies, routes |
| `packages/leader/src/swarmscribe_leader/profiles.py` (new) | list and set settings profiles |
| `packages/leader/src/swarmscribe_leader/admin_cli/main.py` (modify) | `pool-tokens`, `profiles` |
| `packages/leader/src/swarmscribe_leader/errors.py`, `config.py` (modify) | `TooManyRequests`; `links_refresh_min_seconds` |
| `packages/leader/src/swarmscribe_leader/jobs/store.py`, `jobs/claims.py` (modify) | `refresh_links`; `build_links` |
| `packages/leader/src/swarmscribe_leader/api/follower.py` (modify) | `POST /v1/jobs/{id}/links`; the drain header |
| `packages/leader/tests/test_pool_tokens.py`, `test_testkit.py` (new); `test_migrations.py`, `test_admin_api.py`, `test_admin_cli.py`, `test_follower_api.py`, `conftest.py` (modify) | tests |
| `packages/leader/tests/leader_testkit.py` (new) | helpers other packages' tests build a real leader with |
| `README.md` (modify) | pool tokens, profiles, what a follower is told, the public URL |

---

### Task 1: Engine — progress, warm-up and close

The engine call blocks and, today, can be neither watched nor stopped. The follower needs a fraction for the heartbeat and the shutdown estimate, a way to stop a job (cancel, revocation, shutdown), a real inference at start-up (GPU libraries load at the first inference, not when the model loads), and a way to free a model before loading another.

**Files:**
- Modify: `packages/engine/src/swarmscribe_engine/transcriber.py`
- Test: `packages/engine/tests/test_transcriber.py`

**Interfaces:**
- Consumes: the existing `Transcriber`, `FIXED_SETTINGS`, `SAMPLE_RATE`; in the tests, the file's own `FakeModel`, `make_transcriber`, `split_transcriber`, `raw_segment`, `raw_word`, `DecodeError` and the `audio` fixture.
- Produces:
  - `Transcriber.transcribe(path, vocabulary=EMPTY_VOCABULARY, *, settings=None, progress: Callable[[float], None] | None = None) -> Transcript`. `progress` is called after every segment the model yields (also ones dropped as empty) with the fraction done, 0.0 to 1.0, and once more with `1.0` at the end. In a split recording the left channel reports `0.0–0.5` and the right `0.5–1.0`. Whatever `progress` raises stops the transcription at once and reaches the caller unchanged.
  - `Transcriber.warm_up() -> None`: one second of silence through the model with `vad_filter=False`; raises whatever the model raises.
  - `Transcriber.close() -> None`: drops the model; afterwards `transcribe` and `warm_up` raise `RuntimeError("this transcriber is closed")`. Harmless twice.
  - F1's `ModelHost` and `JobRunner` use all three.

- [ ] **Step 1: Write the failing tests**

Append to `packages/engine/tests/test_transcriber.py`:

```python
# --- progress, warm-up and close (follower spec, section 12) ---------------------------


def three_segments():
    return [
        raw_segment(0.0, 2.0, " one", [raw_word(0.0, 2.0, " one", 0.9)]),
        raw_segment(2.0, 5.0, "  ", []),  # dropped from the transcript, still reported
        raw_segment(5.0, 10.0, " three", [raw_word(5.0, 10.0, " three", 0.9)]),
    ]


def test_progress_is_reported_after_every_segment_and_at_the_end(audio):
    seen = []
    model = FakeModel(segments=three_segments(), duration=10.0)
    make_transcriber(model).transcribe(audio, progress=seen.append)
    assert seen == [0.2, 0.5, 1.0, 1.0]


def test_a_split_recording_reports_each_channel_as_half(audio):
    seen = []
    model = FakeModel(segments=three_segments(), duration=10.0)
    split_transcriber(model).transcribe(audio, progress=seen.append)
    assert seen == [0.1, 0.25, 0.5, 0.6, 0.75, 1.0, 1.0]


def test_progress_never_leaves_zero_to_one(audio):
    seen = []
    late = [raw_segment(0.0, 12.0, " longer than the file says", [])]
    for duration in (10.0, 0.0):
        model = FakeModel(segments=late, duration=duration)
        make_transcriber(model).transcribe(audio, progress=seen.append)
    assert seen == [1.0, 1.0, 0.0, 1.0]


class Stop(Exception):
    pass


def test_what_the_progress_callback_raises_stops_the_transcription_unchanged(audio):
    consumed = []

    def segments():
        for segment in three_segments():
            consumed.append(segment)
            yield segment

    model = FakeModel(duration=10.0)
    model.segments = segments()

    def stop_at_once(_fraction):
        raise Stop("asked to stop")

    with pytest.raises(Stop, match="asked to stop"):
        make_transcriber(model).transcribe(audio, progress=stop_at_once)
    assert len(consumed) == 1  # nothing after the first segment was computed


def test_a_decode_error_raised_by_the_callback_is_not_called_undecodable_audio(audio):
    def stop(_fraction):
        raise DecodeError("raised by the caller, not the decoder")

    model = FakeModel(segments=three_segments(), duration=10.0)
    with pytest.raises(DecodeError):
        make_transcriber(model).transcribe(audio, progress=stop)


def test_without_a_callback_nothing_changes(audio):
    model = FakeModel(segments=three_segments(), duration=10.0)
    transcript = make_transcriber(model).transcribe(audio)
    assert [segment.text for segment in transcript.segments] == ["one", "three"]


def test_warm_up_runs_the_model_with_the_vad_filter_off():
    model = FakeModel(segments=[raw_segment(0.0, 1.0, " noise", [])])
    make_transcriber(model).warm_up()
    ((audio_arg, kwargs),) = model.calls
    assert (len(audio_arg), str(audio_arg.dtype)) == (16000, "float32")
    assert not audio_arg.any()
    assert kwargs == {
        "language": "en",
        "condition_on_previous_text": False,
        "vad_filter": False,
        "word_timestamps": False,
        "temperature": 0.0,
    }


def test_warm_up_consumes_the_segments_so_the_model_really_runs():
    model = FakeModel(error_while_iterating=RuntimeError("Library cublas64_12.dll is not found"))
    with pytest.raises(RuntimeError, match="cublas64_12"):
        make_transcriber(model).warm_up()


def test_a_closed_transcriber_refuses_work(audio):
    transcriber = make_transcriber(FakeModel())
    transcriber.close()
    transcriber.close()  # harmless twice
    with pytest.raises(RuntimeError, match="closed"):
        transcriber.transcribe(audio)
    with pytest.raises(RuntimeError, match="closed"):
        transcriber.warm_up()
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest packages/engine/tests/test_transcriber.py -q -k "progress or callback or warm_up or closed"`
Expected: FAIL. `transcribe()` rejects the keyword `progress` (`TypeError: ... unexpected keyword argument 'progress'`), and `Transcriber` has no `warm_up` or `close`.

- [ ] **Step 3: Add the callback type and its carrier**

In `packages/engine/src/swarmscribe_engine/transcriber.py`:

Replace

```python
import hashlib
from collections.abc import Callable, Sequence
```

with

```python
import gc
import hashlib
from collections.abc import Callable, Sequence
```

Replace

```python
ModelFactory = Callable[[TranscribeSettings], Any]
ChannelCounter = Callable[[Path], int]
StereoDecoder = Callable[[Path], tuple[Any, Any]]
```

with

```python
ModelFactory = Callable[[TranscribeSettings], Any]
ChannelCounter = Callable[[Path], int]
StereoDecoder = Callable[[Path], tuple[Any, Any]]
Progress = Callable[[float], None]
"""Called after every segment with the fraction of the recording done, 0.0 to 1.0. Whatever
it raises stops the transcription and reaches the caller unchanged."""


class _FromProgress(BaseException):
    """Carries what a progress callback raised past the decoder's error handling, so that it
    is never mistaken for undecodable audio."""

    def __init__(self, error: BaseException) -> None:
        super().__init__()
        self.error = error


def _report(progress: Progress, fraction: float) -> None:
    try:
        progress(min(1.0, max(0.0, fraction)))
    except BaseException as exc:
        raise _FromProgress(exc) from None
```

`_FromProgress` derives from `BaseException` on purpose: the decoder's error classes are `Exception`s, so nothing the callback raises can be caught by `except self._decode_errors`.

- [ ] **Step 4: Report progress, and add `warm_up` and `close`**

In the same file, in `class Transcriber`:

Replace

```python
    def _pass(
        self, audio: Any, settings: TranscribeSettings, hotwords: str | None
    ) -> tuple[list[Segment], float]:
        raw_segments, info = self._model.transcribe(
            audio,
            **FIXED_SETTINGS,
            temperature=list(settings.temperatures),
            hotwords=hotwords,
        )
        segments = [segment for segment in (_convert(raw) for raw in raw_segments) if segment.text]
        return segments, float(info.duration)

    def transcribe(
        self,
        path: Path,
        vocabulary: Vocabulary = EMPTY_VOCABULARY,
        *,
        settings: TranscribeSettings | None = None,
    ) -> Transcript:
        """Transcribe one file. `settings` may set this call's channel handling and temperature
        ladder; its model, compute type and device must be the loaded ones."""
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(f"recording not found: {path}")
        settings = self._settings_for(settings)
        terms_used = self._select_terms(vocabulary.terms)
        hotwords = build_hotwords(terms_used)
        labels = None
        try:
            if self._splits(path, settings):
                left, right = self._decode_stereo(path)
                left_segments, duration = self._pass(left, settings, hotwords)
                right_segments, _ = self._pass(right, settings, hotwords)
                segments = _merge(left_segments, right_segments)
                labels = settings.channel_labels
            else:
                segments, duration = self._pass(str(path), settings, hotwords)
        except self._decode_errors as exc:
            raise UndecodableAudioError(f"cannot decode {path.name}: {exc}") from exc
```

with

```python
    def _pass(
        self,
        audio: Any,
        settings: TranscribeSettings,
        hotwords: str | None,
        progress: Progress | None = None,
        *,
        start: float = 0.0,
        share: float = 1.0,
    ) -> tuple[list[Segment], float]:
        """One run of the model. Progress is reported as `start` plus this run's `share` of
        the whole (a split recording is two runs of half each)."""
        raw_segments, info = self._model.transcribe(
            audio,
            **FIXED_SETTINGS,
            temperature=list(settings.temperatures),
            hotwords=hotwords,
        )
        duration = float(info.duration)
        segments: list[Segment] = []
        for raw in raw_segments:
            segment = _convert(raw)
            if segment.text:
                segments.append(segment)
            if progress is not None:
                done = segment.end / duration if duration > 0 else 0.0
                _report(progress, start + share * min(1.0, max(0.0, done)))
        return segments, duration

    def _model_or_closed(self) -> Any:
        if self._model is None:
            raise RuntimeError("this transcriber is closed")
        return self._model

    def warm_up(self) -> None:
        """Run one second of silence through the model, with the VAD filter off so that the
        model really runs. GPU libraries are loaded at the first inference, not when the
        model is loaded: this is where a missing one shows."""
        import numpy

        silence = numpy.zeros(SAMPLE_RATE, dtype=numpy.float32)
        segments, _info = self._model_or_closed().transcribe(
            silence,
            language=FIXED_SETTINGS["language"],
            condition_on_previous_text=False,
            vad_filter=False,
            word_timestamps=False,
            temperature=0.0,
        )
        for _segment in segments:
            pass

    def close(self) -> None:
        """Drop the model so that its memory is returned before another one is loaded.
        The transcriber cannot be used afterwards."""
        self._model = None
        gc.collect()

    def transcribe(
        self,
        path: Path,
        vocabulary: Vocabulary = EMPTY_VOCABULARY,
        *,
        settings: TranscribeSettings | None = None,
        progress: Progress | None = None,
    ) -> Transcript:
        """Transcribe one file. `settings` may set this call's channel handling and temperature
        ladder; its model, compute type and device must be the loaded ones. `progress` is
        called after every segment with the fraction done; raising from it stops the
        transcription, and the exception reaches the caller as it was raised."""
        self._model_or_closed()
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(f"recording not found: {path}")
        settings = self._settings_for(settings)
        terms_used = self._select_terms(vocabulary.terms)
        hotwords = build_hotwords(terms_used)
        labels = None
        try:
            if self._splits(path, settings):
                left, right = self._decode_stereo(path)
                left_segments, duration = self._pass(
                    left, settings, hotwords, progress, share=0.5
                )
                right_segments, _ = self._pass(
                    right, settings, hotwords, progress, start=0.5, share=0.5
                )
                segments = _merge(left_segments, right_segments)
                labels = settings.channel_labels
            else:
                segments, duration = self._pass(str(path), settings, hotwords, progress)
            if progress is not None:
                _report(progress, 1.0)
        except _FromProgress as stopped:
            raise stopped.error from None
        except self._decode_errors as exc:
            raise UndecodableAudioError(f"cannot decode {path.name}: {exc}") from exc
```

`_select_terms` uses `self._model` directly; `transcribe` calls `_model_or_closed()` first, so a closed transcriber is refused before that line is reached.

- [ ] **Step 5: Run the tests to see them pass**

Run: `uv run pytest packages/engine -q`
Expected: PASS, every test in the engine package (the new ones and the existing ones: a call without `progress` behaves exactly as before).

Run: `uv run ruff check packages/engine`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add packages/engine/src/swarmscribe_engine/transcriber.py packages/engine/tests/test_transcriber.py
git commit -m "Engine: a progress callback that can stop a transcription, warm_up and close"
```

---

### Task 2: Protocol — fresh links and the drain header

**Files:**
- Modify: `packages/protocol/src/swarmscribe_protocol/messages.py`, `packages/protocol/src/swarmscribe_protocol/__init__.py`, `packages/protocol/src/swarmscribe_protocol/schema.py`
- Regenerate: `packages/protocol/tests/schema_v1.json`
- Test: `packages/protocol/tests/test_messages.py`

**Interfaces:**
- Consumes: `WireModel`, `Link`, `UploadUrls`, `Directive` (all exist).
- Produces, all exported from `swarmscribe_protocol`:
  - `DIRECTIVE_HEADER = "X-SwarmScribe-Directive"`
  - `LinksRequest(lease_id: str)`
  - `JobLinks(download_url: Link, upload_urls: UploadUrls)`
  - Tasks 6 and 7 and F1's `LeaderClient` use them.

- [ ] **Step 1: Write the failing tests**

Append to `packages/protocol/tests/test_messages.py`:

```python
# --- fresh links and the drain header (follower spec, section 12) -----------------------


def test_job_links_carry_one_download_and_three_upload_links():
    from swarmscribe_protocol import JobLinks, LinksRequest

    put = {"url": "https://storage.example.org/out", "method": "PUT"}
    links = JobLinks.model_validate(
        {
            "download_url": {"url": "https://storage.example.org/in", "method": "GET"},
            "upload_urls": {"txt": put, "srt": put, "segments_json": put},
        }
    )
    assert links.download_url.method == "GET"
    assert links.upload_urls.segments_json.method == "PUT"
    assert LinksRequest(lease_id="lease-1").model_dump() == {"lease_id": "lease-1"}


def test_the_drain_header_name_and_value_are_fixed():
    from typing import get_args

    from swarmscribe_protocol import DIRECTIVE_HEADER, Directive

    assert DIRECTIVE_HEADER == "X-SwarmScribe-Directive"
    assert "drain" in get_args(Directive)
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest packages/protocol/tests/test_messages.py -q -k "job_links or drain_header"`
Expected: FAIL with `ImportError: cannot import name 'JobLinks'` and `'DIRECTIVE_HEADER'`.

- [ ] **Step 3: Add the models and the header name**

In `packages/protocol/src/swarmscribe_protocol/messages.py`:

Replace

```python
Directive = Literal["continue", "cancel", "drain"]
```

with

```python
Directive = Literal["continue", "cancel", "drain"]
DIRECTIVE_HEADER = "X-SwarmScribe-Directive"
"""Response header on a claim answered 204. Its value is a Directive; the leader sends only
`drain`, to a draining follower, which will be given nothing more. Absent otherwise."""
```

Replace

```python
class ReleaseRequest(WireModel):
    lease_id: str
```

with

```python
class ReleaseRequest(WireModel):
    lease_id: str


class LinksRequest(WireModel):
    lease_id: str


class JobLinks(WireModel):
    """Fresh links for a job, given to the follower that holds its lease."""

    download_url: Link
    upload_urls: UploadUrls
```

- [ ] **Step 4: Export them and add them to the schema**

In `packages/protocol/src/swarmscribe_protocol/__init__.py`:

Replace

```python
from .messages import (
    Capabilities,
    ClaimResponse,
    Directive,
    ErrorBody,
    FailRequest,
    FailureCode,
    HeartbeatRequest,
    HeartbeatResponse,
    Link,
    OutputChecksums,
```

with

```python
from .messages import (
    DIRECTIVE_HEADER,
    Capabilities,
    ClaimResponse,
    Directive,
    ErrorBody,
    FailRequest,
    FailureCode,
    HeartbeatRequest,
    HeartbeatResponse,
    JobLinks,
    Link,
    LinksRequest,
    OutputChecksums,
```

Replace

```python
    "DEFAULT_CHANNEL_LABELS",
    "MAX_CHANNEL_LABEL_LENGTH",
```

with

```python
    "DEFAULT_CHANNEL_LABELS",
    "DIRECTIVE_HEADER",
    "MAX_CHANNEL_LABEL_LENGTH",
```

Replace

```python
    "JobSettings",
    "Link",
    "OutputChecksums",
```

with

```python
    "JobLinks",
    "JobSettings",
    "Link",
    "LinksRequest",
    "OutputChecksums",
```

In `packages/protocol/src/swarmscribe_protocol/schema.py`:

Replace

```python
    messages.ReleaseRequest,
    messages.ErrorBody,
```

with

```python
    messages.ReleaseRequest,
    messages.LinksRequest,
    messages.JobLinks,
    messages.ErrorBody,
```

- [ ] **Step 5: Regenerate the schema snapshot**

The snapshot test now fails on purpose ("The wire format changed"). The change is additive and the protocol has not shipped, so `PROTOCOL_VERSION` stays 1. Regenerate:

Run: `uv run python -m swarmscribe_protocol.schema packages/protocol/tests/schema_v1.json`

Run: `git diff --stat packages/protocol/tests/schema_v1.json`
Expected: one file changed, insertions only (the `JobLinks` and `LinksRequest` schemas); no deletion.

- [ ] **Step 6: Run the tests to see them pass**

Run: `uv run pytest packages/protocol -q`
Expected: PASS, including `test_wire_schema_matches_the_committed_snapshot` and `test_version.py` (`PROTOCOL_VERSION` is still 1).

Run: `uv run ruff check packages/protocol`
Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add packages/protocol
git commit -m "Protocol: JobLinks, LinksRequest and the directive header name"
```

---

### Task 3: Leader schema — migration 0006

**Files:**
- Modify: `packages/leader/src/swarmscribe_leader/db/models.py`
- Create: `packages/leader/src/swarmscribe_leader/db/migrations/versions/0006_follower_support.py`
- Test: `packages/leader/tests/test_migrations.py`

**Interfaces:**
- Consumes: the leader's migration head `0005`.
- Produces:
  - `PoolToken` (table `pool_tokens`): `name` (unique), `token_hash` (unique), `pool`, `created_by`, `registrations: int`, `last_used_at`, `revoked_at`, `revoked_by`, plus `id`, `created_at`.
  - `Follower.pool_token_id: uuid.UUID | None` (FK `pool_tokens.id`), index `ix_followers_pool_token (pool_token_id, state)`.
  - `Job.links_issued_at: datetime | None`.
  - Migration revision `"0006"`, `down_revision "0005"`. Tasks 4, 5 and 6 use these columns.

- [ ] **Step 1: Write the failing tests**

In `packages/leader/tests/test_migrations.py`:

Replace

```python
    assert head_revision() == "0005"
    assert await current_revision(engine) == "0005"
```

with

```python
    assert head_revision() == "0006"
    assert await current_revision(engine) == "0006"
```

and append:

```python
async def test_a_follower_may_name_the_pool_token_it_registered_with(engine):
    async with engine.connect() as conn:
        columns = dict(
            (
                await conn.execute(
                    text(
                        "select table_name || '.' || column_name, is_nullable "
                        "from information_schema.columns where (table_name, column_name) in "
                        "(('followers', 'pool_token_id'), ('jobs', 'links_issued_at'), "
                        "('pool_tokens', 'token_hash'))"
                    )
                )
            ).all()
        )
        index = await conn.scalar(
            text("select indexdef from pg_indexes where indexname = 'ix_followers_pool_token'")
        )
    assert columns == {
        "followers.pool_token_id": "YES",
        "jobs.links_issued_at": "YES",
        "pool_tokens.token_hash": "NO",
    }
    assert "(pool_token_id, state)" in index
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest packages/leader/tests/test_migrations.py -q -k "head_revision or pool_token"`
Expected: FAIL. The head is still `0005`, and the new columns do not exist.

- [ ] **Step 3: Add the models**

In `packages/leader/src/swarmscribe_leader/db/models.py`:

Replace

```python
class Follower(_Row, Base):
    __tablename__ = "followers"

    pool: Mapped[str] = mapped_column(String(100))
    capabilities: Mapped[dict[str, Any]] = mapped_column(default=dict)
    credential_hash: Mapped[str] = mapped_column(String(64), unique=True)
    state: Mapped[str] = mapped_column(String(16), default="active")
    last_seen_at: Mapped[datetime]
    join_token_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("join_tokens.id"))
```

with

```python
class PoolToken(_Row, Base):
    """A token that registers followers of one pool for as long as it is not revoked: no
    expiry, no limit on uses. Stored as its SHA-256. For pools whose machines come and go
    by themselves (Kubernetes); a join token is for a machine a person sets up."""

    __tablename__ = "pool_tokens"

    name: Mapped[str] = mapped_column(String(100), unique=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    pool: Mapped[str] = mapped_column(String(100))
    created_by: Mapped[str] = mapped_column(Text)
    registrations: Mapped[int] = mapped_column(default=0)
    last_used_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]
    revoked_by: Mapped[str | None] = mapped_column(Text)


class Follower(_Row, Base):
    __tablename__ = "followers"
    # Serves registration with a pool token: that token's `gone` followers, to reuse one.
    __table_args__ = (Index("ix_followers_pool_token", "pool_token_id", "state"),)

    pool: Mapped[str] = mapped_column(String(100))
    capabilities: Mapped[dict[str, Any]] = mapped_column(default=dict)
    credential_hash: Mapped[str] = mapped_column(String(64), unique=True)
    state: Mapped[str] = mapped_column(String(16), default="active")
    last_seen_at: Mapped[datetime]
    join_token_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("join_tokens.id"))
    # Set when the follower registered with a pool token.
    pool_token_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("pool_tokens.id"))
```

Replace

```python
    # Not before: a job whose claim could not be built is pushed back so others are reached.
    available_at: Mapped[datetime] = mapped_column(server_default=func.now())
```

with

```python
    # Not before: a job whose claim could not be built is pushed back so others are reached.
    available_at: Mapped[datetime] = mapped_column(server_default=func.now())
    # When this lease's links were last issued (at the claim, then at each refresh).
    links_issued_at: Mapped[datetime | None]
```

- [ ] **Step 4: Write the migration**

Create `packages/leader/src/swarmscribe_leader/db/migrations/versions/0006_follower_support.py`:

```python
"""follower support: pool tokens, the follower's pool token, when a lease's links were issued

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-04

Hand-written.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pool_tokens",
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("pool", sa.String(length=100), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("registrations", sa.Integer(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("token_hash"),
    )
    op.add_column("followers", sa.Column("pool_token_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "followers_pool_token_id_fkey", "followers", "pool_tokens", ["pool_token_id"], ["id"]
    )
    op.create_index("ix_followers_pool_token", "followers", ["pool_token_id", "state"])
    op.add_column(
        "jobs", sa.Column("links_issued_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("jobs", "links_issued_at")
    op.drop_index("ix_followers_pool_token", table_name="followers")
    op.drop_constraint("followers_pool_token_id_fkey", "followers", type_="foreignkey")
    op.drop_column("followers", "pool_token_id")
    op.drop_table("pool_tokens")
```

- [ ] **Step 5: Run the tests to see them pass**

Run: `uv run pytest packages/leader/tests/test_migrations.py -q`
Expected: PASS. `test_migrations_produce_exactly_the_models` is the one that matters: the migration and the models describe the same schema (it returns `[]` differences).

The test database is created once per session from the migrations; if an earlier run left one at `0005`, it is dropped and recreated automatically by the `database_url` fixture.

Run: `uv run pytest packages/leader -q`
Expected: PASS (no existing test depends on the head being `0005` except the one changed in step 1).

- [ ] **Step 6: Commit**

```bash
git add packages/leader/src/swarmscribe_leader/db packages/leader/tests/test_migrations.py
git commit -m "Leader schema 0006: pool tokens, a follower's pool token, when links were issued"
```

---

### Task 4: Pool tokens register followers, and rows are reused

**Files:**
- Modify: `packages/leader/src/swarmscribe_leader/auth/followers.py`
- Create: `packages/leader/src/swarmscribe_leader/auth/pool_tokens.py`
- Test: `packages/leader/tests/test_pool_tokens.py` (new)

**Interfaces:**
- Consumes: `PoolToken`, `Follower.pool_token_id` (Task 3); `revoke_follower(session, follower_id, *, now, actor) -> tuple[Follower, int]`, `hash_secret`, `new_secret`, `audit.record`, `Conflict`, `NotFound`, `InvalidToken`, `LeaderError` (all exist).
- Produces:
  - `auth.followers.register(session, request, *, now) -> tuple[Follower, str]` — unchanged signature. A `join_token` that is no join token is looked up as a pool token; unknown or revoked raises `InvalidToken("the join token is not valid")`, exactly as before.
  - `auth.pool_tokens.create_pool_token(session, *, name: str, pool: str, actor: str) -> tuple[PoolToken, str]` (the row and the plaintext). Raises `InvalidPoolTokenName` (422, code `invalid_name`) and `Conflict` (409, code `exists`).
  - `auth.pool_tokens.revoke_pool_token(session, name: str, *, now, actor: str, revoke_followers: bool = False) -> tuple[PoolToken, int]` (the row and how many followers were revoked). Raises `NotFound`.
  - `auth.pool_tokens.InvalidPoolTokenName`.
  - Audit actions `pool_token.create` `{name, pool}`, `pool_token.revoke` `{name, followers_revoked}`; `follower.register` gains `pool_token` and `reused` in its detail when a pool token registered it.
  - Task 5's routes and F1's contract test use both functions.

- [ ] **Step 1: Write the failing tests**

Create `packages/leader/tests/test_pool_tokens.py`:

```python
import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select
from swarmscribe_leader.auth.followers import authenticate, create_join_token, register
from swarmscribe_leader.auth.pool_tokens import (
    InvalidPoolTokenName,
    create_pool_token,
    revoke_pool_token,
)
from swarmscribe_leader.auth.secrets import hash_secret
from swarmscribe_leader.clock import utcnow
from swarmscribe_leader.db.models import AuditEntry, Follower, Job, PoolToken
from swarmscribe_leader.errors import Conflict, Forbidden, NotFound, Unauthorized
from swarmscribe_leader.jobs import store
from swarmscribe_leader.jobs.reaper import reap
from swarmscribe_protocol import Capabilities, RegisterRequest


def request(token: str, *, device: str = "cpu") -> RegisterRequest:
    return RegisterRequest(
        join_token=token,
        protocol_version=1,
        capabilities=Capabilities(
            device=device, models=["distil-large-v3"], engine_version="0.1.0", pool="ignored"
        ),
    )


async def pool_token(sessionmaker, name="gpu-pods", pool="gpu") -> str:
    async with sessionmaker() as session:
        _, plaintext = await create_pool_token(session, name=name, pool=pool, actor="test")
        await session.commit()
    return plaintext


async def join(sessionmaker, plaintext, **kwargs) -> tuple[Follower, str]:
    async with sessionmaker() as session:
        follower, credential = await register(session, request(plaintext, **kwargs), now=utcnow())
        await session.commit()
    return follower, credential


async def followers(sessionmaker) -> list[Follower]:
    async with sessionmaker() as session:
        return list((await session.scalars(select(Follower).order_by(Follower.created_at))).all())


async def test_a_pool_token_is_stored_only_as_a_hash_and_audited_by_name(sessionmaker):
    plaintext = await pool_token(sessionmaker)
    async with sessionmaker() as session:
        stored = (await session.scalars(select(PoolToken))).one()
        (entry,) = (await session.scalars(select(AuditEntry))).all()
    assert stored.token_hash == hash_secret(plaintext)
    assert (stored.name, stored.pool, stored.registrations) == ("gpu-pods", "gpu", 0)
    assert (entry.action, entry.detail) == (
        "pool_token.create",
        {"name": "gpu-pods", "pool": "gpu"},
    )
    assert plaintext not in f"{entry.actor} {entry.detail} {stored.name}"


async def test_a_pool_token_registers_any_number_of_followers_into_its_pool(sessionmaker):
    plaintext = await pool_token(sessionmaker)
    credentials = set()
    for _ in range(12):
        follower, credential = await join(sessionmaker, plaintext)
        credentials.add(credential)
        assert (follower.pool, follower.state) == ("gpu", "active")
    rows = await followers(sessionmaker)
    assert len(rows) == 12 and len(credentials) == 12
    assert all(row.capabilities["pool"] == "gpu" for row in rows)  # not what it claimed
    async with sessionmaker() as session:
        stored = (await session.scalars(select(PoolToken))).one()
    assert stored.registrations == 12 and stored.last_used_at is not None


async def test_a_join_token_registration_also_records_the_tokens_pool(sessionmaker):
    async with sessionmaker() as session:
        _, plaintext = await create_join_token(
            session,
            pool="cpu",
            expires_at=utcnow() + timedelta(days=1),
            max_uses=1,
            created_by="test",
        )
        await session.commit()
    follower, _ = await join(sessionmaker, plaintext)
    assert (follower.pool, follower.capabilities["pool"], follower.pool_token_id) == (
        "cpu",
        "cpu",
        None,
    )


async def test_a_gone_followers_row_is_reused_and_its_old_credential_dies(sessionmaker):
    plaintext = await pool_token(sessionmaker)
    first, old_credential = await join(sessionmaker, plaintext)
    await join(sessionmaker, plaintext)  # still active: never reused
    async with sessionmaker() as session:
        (await session.get(Follower, first.id)).state = "gone"
        await session.commit()
    third, new_credential = await join(sessionmaker, plaintext, device="cuda")
    rows = await followers(sessionmaker)
    assert len(rows) == 2
    assert third.id == first.id
    reused = next(row for row in rows if row.id == first.id)
    assert (reused.state, reused.capabilities["device"]) == ("active", "cuda")
    async with sessionmaker() as session:
        with pytest.raises(Unauthorized):
            await authenticate(session, old_credential, now=utcnow())
    async with sessionmaker() as session:
        assert (await authenticate(session, new_credential, now=utcnow())).id == first.id
        entries = (
            await session.scalars(
                select(AuditEntry)
                .where(AuditEntry.action == "follower.register")
                .order_by(AuditEntry.created_at)
            )
        ).all()
    assert [entry.detail["reused"] for entry in entries] == [False, False, True]
    assert {entry.detail["pool_token"] for entry in entries} == {"gpu-pods"}


@pytest.mark.parametrize("state", ["draining", "revoked", "active"])
async def test_only_a_gone_follower_is_reused(sessionmaker, state):
    plaintext = await pool_token(sessionmaker)
    first, _ = await join(sessionmaker, plaintext)
    async with sessionmaker() as session:
        (await session.get(Follower, first.id)).state = state
        await session.commit()
    second, _ = await join(sessionmaker, plaintext)
    assert second.id != first.id
    async with sessionmaker() as session:
        assert (await session.get(Follower, first.id)).state == state


async def test_a_gone_follower_of_another_token_or_of_a_join_token_is_never_reused(
    sessionmaker, factory
):
    mine = await pool_token(sessionmaker)
    other = await pool_token(sessionmaker, name="other", pool="gpu")
    theirs, _ = await join(sessionmaker, other)
    plain, _ = await factory.follower(pool="gpu", state="gone")
    async with sessionmaker() as session:
        (await session.get(Follower, theirs.id)).state = "gone"
        await session.commit()
    joined, _ = await join(sessionmaker, mine)
    assert joined.id not in {theirs.id, plain.id}


async def test_a_gone_follower_that_still_holds_a_lease_is_not_reused(sessionmaker, factory):
    plaintext = await pool_token(sessionmaker, pool="default")
    first, _ = await join(sessionmaker, plaintext)
    await factory.job()
    async with sessionmaker() as session:
        holder = await session.get(Follower, first.id)
        assert await store.claim(session, holder, now=utcnow(), lease_seconds=120) is not None
        holder.state = "gone"  # cannot happen through the API; the row must still be safe
        await session.commit()
    second, _ = await join(sessionmaker, plaintext)
    assert second.id != first.id
    async with sessionmaker() as session:
        assert (await session.scalars(select(Job))).one().leased_by == first.id


async def test_a_pod_that_stops_and_one_that_starts_keep_the_table_the_same_size(sessionmaker):
    plaintext = await pool_token(sessionmaker)
    for _ in range(20):  # twenty pod restarts
        follower, credential = await join(sessionmaker, plaintext)
        async with sessionmaker() as session:
            (await session.get(Follower, follower.id)).state = "gone"  # what deregister does
            await session.commit()
    assert len(await followers(sessionmaker)) == 1


async def test_two_registrations_at_once_never_share_a_row(sessionmaker):
    plaintext = await pool_token(sessionmaker)
    first, _ = await join(sessionmaker, plaintext)
    async with sessionmaker() as session:
        (await session.get(Follower, first.id)).state = "gone"
        await session.commit()
    joined = await asyncio.gather(*(join(sessionmaker, plaintext) for _ in range(6)))
    assert len({follower.id for follower, _ in joined}) == 6
    assert len({credential for _, credential in joined}) == 6
    assert len(await followers(sessionmaker)) == 6


async def test_the_reaper_and_a_reused_row_agree(sessionmaker):
    plaintext = await pool_token(sessionmaker)
    first, _ = await join(sessionmaker, plaintext)
    await reap(sessionmaker, now=utcnow() + timedelta(hours=1), gone_after=timedelta(minutes=10))
    second, _ = await join(sessionmaker, plaintext)
    assert second.id == first.id
    assert [row.state for row in await followers(sessionmaker)] == ["active"]


async def test_a_revoked_pool_token_registers_nothing_and_leaves_its_followers(sessionmaker):
    plaintext = await pool_token(sessionmaker)
    follower, credential = await join(sessionmaker, plaintext)
    async with sessionmaker() as session:
        token, revoked = await revoke_pool_token(
            session, "gpu-pods", now=utcnow(), actor="admin@example.org"
        )
        await session.commit()
    assert (token.revoked_by, revoked) == ("admin@example.org", 0)
    async with sessionmaker() as session:
        with pytest.raises(Unauthorized):
            await register(session, request(plaintext), now=utcnow())
    async with sessionmaker() as session:
        assert (await authenticate(session, credential, now=utcnow())).id == follower.id


async def test_revoking_with_its_followers_revokes_them_and_releases_their_work(
    sessionmaker, factory
):
    plaintext = await pool_token(sessionmaker, pool="default")
    first, credential = await join(sessionmaker, plaintext)
    await join(sessionmaker, plaintext)
    bystander, _ = await factory.follower()
    await factory.job()
    async with sessionmaker() as session:
        holder = await session.get(Follower, first.id)
        await store.claim(session, holder, now=utcnow(), lease_seconds=120)
        await session.commit()
    async with sessionmaker() as session:
        _, revoked = await revoke_pool_token(
            session, "gpu-pods", now=utcnow(), actor="admin@example.org", revoke_followers=True
        )
        await session.commit()
    assert revoked == 2
    async with sessionmaker() as session:
        assert (await session.scalars(select(Job))).one().state == "queued"
        assert (await session.get(Follower, bystander.id)).state == "active"
        with pytest.raises(Forbidden):
            await authenticate(session, credential, now=utcnow())
        (entry,) = (
            await session.scalars(
                select(AuditEntry).where(AuditEntry.action == "pool_token.revoke")
            )
        ).all()
    assert entry.detail == {"name": "gpu-pods", "followers_revoked": 2}


async def test_revoking_twice_keeps_the_first_revocation(sessionmaker):
    await pool_token(sessionmaker)
    start = utcnow()
    async with sessionmaker() as session:
        await revoke_pool_token(session, "gpu-pods", now=start, actor="first")
        await session.commit()
    async with sessionmaker() as session:
        token, _ = await revoke_pool_token(
            session, "gpu-pods", now=start + timedelta(hours=1), actor="second"
        )
        await session.commit()
    assert (token.revoked_at, token.revoked_by) == (start, "first")


async def test_names_are_checked_unique_and_never_reused(sessionmaker):
    await pool_token(sessionmaker)
    async with sessionmaker() as session:
        await revoke_pool_token(session, "gpu-pods", now=utcnow(), actor="test")
        await session.commit()
    async with sessionmaker() as session:
        with pytest.raises(Conflict) as refused:
            await create_pool_token(session, name="gpu-pods", pool="gpu", actor="test")
    assert refused.value.code == "exists"
    for bad in ("", "has space", "-leading", "x" * 101, "line\nbreak"):
        async with sessionmaker() as session:
            with pytest.raises(InvalidPoolTokenName):
                await create_pool_token(session, name=bad, pool="gpu", actor="test")
    async with sessionmaker() as session:
        with pytest.raises(NotFound):
            await revoke_pool_token(session, "no-such", now=utcnow(), actor="test")
        with pytest.raises(NotFound):
            await revoke_pool_token(session, "has space", now=utcnow(), actor="test")


async def test_an_unknown_token_is_refused_like_any_bad_join_token(sessionmaker):
    async with sessionmaker() as session:
        with pytest.raises(Unauthorized) as refused:
            await register(session, request("not-a-token"), now=utcnow())
    assert refused.value.message == "the join token is not valid"
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest packages/leader/tests/test_pool_tokens.py -q`
Expected: FAIL at import: `ModuleNotFoundError: No module named 'swarmscribe_leader.auth.pool_tokens'`.

- [ ] **Step 3: Register with a pool token, reusing a gone follower's row**

In `packages/leader/src/swarmscribe_leader/auth/followers.py`:

Replace

```python
from .. import audit
from ..db.models import Follower, JoinToken
from ..errors import Conflict, Forbidden, InvalidToken, NotFound
```

with

```python
from .. import audit
from ..db.models import Follower, Job, JoinToken, PoolToken
from ..errors import Conflict, Forbidden, InvalidToken, NotFound
```

Replace

```python
    token = await session.scalar(
        select(JoinToken)
        .where(JoinToken.token_hash == hash_secret(request.join_token))
        .with_for_update()
    )
    if token is None or token.revoked or token.expires_at <= now or token.uses >= token.max_uses:
        raise InvalidToken("the join token is not valid")
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
```

with

```python
    token_hash = hash_secret(request.join_token)
    token = await session.scalar(
        select(JoinToken).where(JoinToken.token_hash == token_hash).with_for_update()
    )
    if token is None:
        return await _register_with_pool_token(session, request, token_hash, now=now)
    if token.revoked or token.expires_at <= now or token.uses >= token.max_uses:
        raise InvalidToken("the join token is not valid")
    token.uses += 1
    credential = new_secret()
    follower = Follower(
        id=uuid.uuid4(),
        pool=token.pool,
        capabilities=_capabilities(request, token.pool),
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


def _capabilities(request: RegisterRequest, pool: str) -> dict[str, Any]:
    """What the follower reported, with the pool the token put it in (the one that counts)."""
    return {**request.capabilities.model_dump(), "pool": pool}


async def _gone_follower_of(session: AsyncSession, pool_token: PoolToken) -> Follower | None:
    """A follower this pool token registered that is gone and holds nothing: its row is
    given to the next registration, so machines that come and go do not grow the table."""
    holding = select(Job.leased_by).where(Job.state == "leased", Job.leased_by.is_not(None))
    return await session.scalar(
        select(Follower)
        .where(
            Follower.pool_token_id == pool_token.id,
            Follower.state == "gone",
            Follower.id.not_in(holding),
        )
        .order_by(Follower.last_seen_at, Follower.id)
        .limit(1)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    )


async def _register_with_pool_token(
    session: AsyncSession, request: RegisterRequest, token_hash: str, *, now: datetime
) -> tuple[Follower, str]:
    pool_token = await session.scalar(
        select(PoolToken).where(PoolToken.token_hash == token_hash).with_for_update()
    )
    if pool_token is None or pool_token.revoked_at is not None:
        raise InvalidToken("the join token is not valid")
    pool_token.registrations += 1
    pool_token.last_used_at = now
    credential = new_secret()
    follower = await _gone_follower_of(session, pool_token)
    reused = follower is not None
    if follower is None:
        follower = Follower(id=uuid.uuid4(), pool=pool_token.pool, pool_token_id=pool_token.id)
        session.add(follower)
    # A reused row's old credential stops working here: its hash is replaced.
    follower.capabilities = _capabilities(request, pool_token.pool)
    follower.credential_hash = hash_secret(credential)
    follower.state = "active"
    follower.last_seen_at = now
    audit.record(
        session,
        actor=f"follower:{follower.id}",
        action="follower.register",
        subject_type="follower",
        subject_id=follower.id,
        detail={
            "pool": pool_token.pool,
            "device": request.capabilities.device,
            "pool_token": pool_token.name,
            "reused": reused,
        },
    )
    return follower, credential
```

Replace

```python
import uuid
from datetime import datetime

from sqlalchemy import select, update
```

with

```python
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
```

Why this is safe when two pods register at once: each registration locks the pool token's row first (`with_for_update`), so registrations with one token run one after another; the `gone` row is locked with `skip_locked`, so a row another transaction holds is never chosen. A follower whose credential hash was just replaced is refused on its next call: `authenticate` looks the follower up by hash.

- [ ] **Step 4: Create and revoke pool tokens**

Create `packages/leader/src/swarmscribe_leader/auth/pool_tokens.py`:

```python
"""Pool tokens: what a pool of followers that come and go by themselves registers with.

A join token expires (90 days at most) and has a limited number of uses, which suits a
machine a person sets up. A Kubernetes pool registers a follower every time a pod starts,
for as long as the pool exists, so its token must not run out on a calendar: a pool token
has no expiry and no limit on uses. It is therefore the more dangerous secret, and is
handled like a console credential: named, shown once, stored only as its SHA-256, created
and revoked only by an administrator signed in as a person, never through a console.
Names are never reused, because the audit log names pool tokens by name.
"""

import re
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit
from ..db.models import Follower, PoolToken
from ..errors import Conflict, LeaderError, NotFound
from .followers import revoke_follower
from .secrets import hash_secret, new_secret

# The API's NAME_PATTERN: nothing that could forge or break an audit line.
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")


class InvalidPoolTokenName(LeaderError):
    status = 422
    code = "invalid_name"


def _exists(name: str) -> Conflict:
    return Conflict(
        f"a pool token named {name!r} already exists; pool token names are never reused",
        code="exists",
    )


async def create_pool_token(
    session: AsyncSession, *, name: str, pool: str, actor: str
) -> tuple[PoolToken, str]:
    """A new pool token for `pool`. Returns the row and the plaintext, kept nowhere."""
    if not _NAME.fullmatch(name):
        raise InvalidPoolTokenName(
            "a pool token name starts with a letter or digit and holds only letters, digits,"
            " '.', '_' and '-' (at most 100 characters)"
        )
    if await session.scalar(select(PoolToken.id).where(PoolToken.name == name)) is not None:
        raise _exists(name)
    plaintext = new_secret()
    token = PoolToken(
        id=uuid.uuid4(),
        name=name,
        token_hash=hash_secret(plaintext),
        pool=pool,
        created_by=actor,
        registrations=0,
    )
    session.add(token)
    try:
        # The unique constraint has the last word if two creations passed the check at once.
        await session.flush()
    except IntegrityError as exc:
        raise _exists(name) from exc
    audit.record(
        session,
        actor=actor,
        action="pool_token.create",
        subject_type="pool_token",
        subject_id=token.id,
        detail={"name": name, "pool": pool},
    )
    return token, plaintext


async def revoke_pool_token(
    session: AsyncSession,
    name: str,
    *,
    now: datetime,
    actor: str,
    revoke_followers: bool = False,
) -> tuple[PoolToken, int]:
    """Refuse every further registration with the token. With `revoke_followers`, every
    follower it registered is revoked too (what a leaked token needs). Revoking again keeps
    the first revocation's time and administrator. Returns the token and how many followers
    were revoked."""
    if not _NAME.fullmatch(name):
        raise NotFound("no pool token with that name")
    token = await session.scalar(
        select(PoolToken)
        .where(PoolToken.name == name)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if token is None:
        raise NotFound(f"no pool token named {name!r}")
    if token.revoked_at is None:
        token.revoked_at = now
        token.revoked_by = actor
    revoked = 0
    if revoke_followers:
        follower_ids = (
            await session.scalars(
                select(Follower.id)
                .where(Follower.pool_token_id == token.id, Follower.state != "revoked")
                .order_by(Follower.id)
            )
        ).all()
        for follower_id in follower_ids:
            await revoke_follower(session, follower_id, now=now, actor=actor)
            revoked += 1
    audit.record(
        session,
        actor=actor,
        action="pool_token.revoke",
        subject_type="pool_token",
        subject_id=token.id,
        detail={"name": token.name, "followers_revoked": revoked},
    )
    return token, revoked
```

- [ ] **Step 5: Run the tests to see them pass**

Run: `uv run pytest packages/leader/tests/test_pool_tokens.py packages/leader/tests/test_follower_auth.py -q`
Expected: PASS. `test_follower_auth.py` proves join tokens register exactly as before.

Run: `uv run pytest packages/leader -q && uv run ruff check packages/leader`
Expected: PASS; `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add packages/leader/src/swarmscribe_leader/auth packages/leader/tests/test_pool_tokens.py
git commit -m "Leader: pool tokens register followers without expiry, reusing a gone follower's row"
```

---

### Task 5: Pool tokens in the admin API and CLI

**Files:**
- Modify: `packages/leader/src/swarmscribe_leader/reports.py`, `packages/leader/src/swarmscribe_leader/api/admin_models.py`, `packages/leader/src/swarmscribe_leader/api/admin.py`, `packages/leader/src/swarmscribe_leader/admin_cli/main.py`
- Test: `packages/leader/tests/test_admin_api.py`, `packages/leader/tests/test_admin_cli.py`

**Interfaces:**
- Consumes: `create_pool_token`, `revoke_pool_token`, `InvalidPoolTokenName` (Task 4); `PersonAdministrator` (exists in `api/admin.py`: `require("admin", consoles_allowed=False)`); in the tests, `post`, `get`, `actor`, `audit_rows`, `BELOW`, `delegated`, `CAPABILITIES` (exist in `test_admin_api.py`), and `cli`, `store`, `sign_in_as` (exist in `test_admin_cli.py`).
- Produces:
  - `GET /v1/admin/pool-tokens` → `200 list[PoolTokenOut]` (`id, name, pool, registrations, last_used_at, revoked, revoked_at, revoked_by, created_by, created_at`). Audited `pool_tokens.view`.
  - `POST /v1/admin/pool-tokens` body `{name, pool = "default"}` → `201 {id, name, pool, token}`.
  - `POST /v1/admin/pool-tokens/{name}/revoke`, optional body `{revoke_followers: bool}` → `200` `PoolTokenOut` plus `followers_revoked: int`.
  - All three: role `admin`, a person only (a console gets `403 forbidden`).
  - CLI: `swarmscribe-admin pool-tokens create --name N [--pool P]`, `pool-tokens list`, `pool-tokens revoke NAME [--revoke-followers]`.
  - Test helper `for_people_with_the_role(admin_client, idp, factory, method, path, body, role)` in `test_admin_api.py`; Task 8 reuses it.

- [ ] **Step 1: Write the failing API tests**

In `packages/leader/tests/test_admin_api.py`:

Replace

```python
from swarmscribe_leader.auth.consoles import create_console
from swarmscribe_leader.auth.followers import create_join_token
```

with

```python
from swarmscribe_leader.auth import pool_tokens
from swarmscribe_leader.auth.consoles import create_console
from swarmscribe_leader.auth.followers import create_join_token
```

and append:

```python
# --- pool tokens (follower spec 12.1) -----------------------------------------------------


async def test_a_pool_token_is_shown_once_registers_followers_and_is_never_logged(
    admin_client, idp, sessionmaker, caplog
):
    with caplog.at_level("DEBUG"):
        body = {"name": "gpu-pods", "pool": "gpu"}
        response = await post(admin_client, idp, "/v1/admin/pool-tokens", "admin", body)
    assert response.status_code == 201, response.text
    created = response.json()
    assert set(created) == {"id", "name", "pool", "token"}
    assert created["token"] not in caplog.text
    register = {"join_token": created["token"], "protocol_version": 1, "capabilities": CAPABILITIES}
    answers = [await admin_client.post("/v1/followers/register", json=register) for _ in range(3)]
    assert [answer.status_code for answer in answers] == [200, 200, 200]
    (listed,) = await get(admin_client, idp, "/v1/admin/pool-tokens", role="admin")
    assert created["token"] not in str(listed)
    assert (listed["name"], listed["pool"], listed["registrations"], listed["revoked"]) == (
        "gpu-pods",
        "gpu",
        3,
        False,
    )
    assert listed["created_by"] == actor(idp, "admin")
    (entry,) = await audit_rows(sessionmaker, "pool_token.create")
    assert created["token"] not in f"{entry.actor} {entry.detail}"
    assert len(await audit_rows(sessionmaker, "pool_tokens.view")) == 1


async def test_revoking_a_pool_token_stops_registrations_and_can_take_its_followers(
    admin_client, idp, sessionmaker
):
    created = (
        await post(admin_client, idp, "/v1/admin/pool-tokens", "admin", {"name": "cpu-pods"})
    ).json()
    register = {"join_token": created["token"], "protocol_version": 1, "capabilities": CAPABILITIES}
    credential = (await admin_client.post("/v1/followers/register", json=register)).json()[
        "credential"
    ]
    plain = await post(admin_client, idp, "/v1/admin/pool-tokens/cpu-pods/revoke", "admin")
    assert (plain.status_code, plain.json()["revoked"], plain.json()["followers_revoked"]) == (
        200,
        True,
        0,
    )
    assert (await admin_client.post("/v1/followers/register", json=register)).status_code == 401
    headers = {"Authorization": f"Bearer {credential}"}
    assert (await admin_client.post("/v1/jobs/claim", headers=headers)).status_code == 204
    again = await post(
        admin_client,
        idp,
        "/v1/admin/pool-tokens/cpu-pods/revoke",
        "admin",
        {"revoke_followers": True},
    )
    assert again.json()["followers_revoked"] == 1
    assert (await admin_client.post("/v1/jobs/claim", headers=headers)).status_code == 403


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"name": "bad name"}, 422, "invalid_request"),
        ({"name": "ok", "pool": "bad pool"}, 422, "invalid_request"),
        ({"name": "ok", "expires_in_seconds": 60}, 422, "invalid_request"),
        ({}, 422, "invalid_request"),
    ],
)
async def test_invalid_pool_tokens_are_refused(admin_client, idp, body, status, code):
    response = await post(admin_client, idp, "/v1/admin/pool-tokens", "admin", body)
    assert (response.status_code, response.json()["code"]) == (status, code)


async def test_pool_token_names_conflict_and_unknown_ones_are_404(admin_client, idp):
    body = {"name": "gpu-pods"}
    first = await post(admin_client, idp, "/v1/admin/pool-tokens", "admin", body)
    assert first.status_code == 201
    again = await post(admin_client, idp, "/v1/admin/pool-tokens", "admin", body)
    assert (again.status_code, again.json()["code"]) == (409, "exists")
    for name in ("no-such", "bad%20name"):
        gone = await post(admin_client, idp, f"/v1/admin/pool-tokens/{name}/revoke", "admin")
        assert gone.status_code == 404, name
    path = "/v1/admin/pool-tokens/gpu-pods/revoke"
    wrong = await post(admin_client, idp, path, "admin", {"revoke_followers": 1})
    assert wrong.status_code == 422


async def for_people_with_the_role(admin_client, idp, factory, method, path, body, role):
    """The route refuses the role below, refuses a console whatever its cap and whoever it
    names, and admits a person holding the role."""
    if BELOW[role] is not None:
        refused = await admin_client.request(
            method, path, headers=idp.bearer(BELOW[role]), json=body
        )
        assert (refused.status_code, refused.json()["code"]) == (403, "forbidden")
    _, credential = await factory.console(name="full", max_role="admin")
    through_console = await admin_client.request(
        method, path, headers=delegated(credential, "admin"), json=body
    )
    assert (through_console.status_code, through_console.json()["code"]) == (403, "forbidden")
    allowed = await admin_client.request(method, path, headers=idp.bearer(role), json=body)
    assert allowed.status_code < 400, allowed.text


POOL_TOKEN_ROUTES = [
    ("GET", "/v1/admin/pool-tokens", None, "admin"),
    ("POST", "/v1/admin/pool-tokens", {"name": "added-pool-token"}, "admin"),
    ("POST", "/v1/admin/pool-tokens/gpu-pods/revoke", None, "admin"),
]


@pytest.mark.parametrize(
    "method, path, body, role",
    POOL_TOKEN_ROUTES,
    ids=[f"{m} {p}" for m, p, _, _ in POOL_TOKEN_ROUTES],
)
async def test_pool_token_routes_are_for_administrators_signed_in_as_people(
    admin_client, idp, factory, sessionmaker, method, path, body, role
):
    async with sessionmaker() as session:
        await pool_tokens.create_pool_token(session, name="gpu-pods", pool="gpu", actor="test")
        await session.commit()
    await for_people_with_the_role(admin_client, idp, factory, method, path, body, role)
```

- [ ] **Step 2: Write the failing CLI tests**

Append to `packages/leader/tests/test_admin_cli.py`:

```python
# --- pool tokens (follower spec 12.1) ----------------------------------------------------


async def test_pool_tokens_create_shows_the_token_once_and_list_never_does(
    cli, store, idp, sessionmaker
):
    from swarmscribe_leader.db.models import PoolToken

    sign_in_as(store, idp, "admin")
    code, out, err = await cli("pool-tokens", "create", "--name", "gpu-pods", "--pool", "gpu")
    assert code == 0, err
    async with sessionmaker() as session:
        row = (await session.scalars(select(PoolToken))).one()
    shown = out.splitlines()[0].rsplit(" ", 1)[1]
    assert out.startswith("pool token (store this now; it will not be shown again): ")
    assert hash_secret(shown) == row.token_hash
    code, listed, _err = await cli("pool-tokens", "list")
    assert code == 0 and "gpu-pods" in listed and shown not in listed
    code, out, err = await cli("pool-tokens", "revoke", "gpu-pods", "--revoke-followers")
    assert code == 0, err
    assert "followers_revoked: 0" in out and "revoked: yes" in out


async def test_pool_tokens_are_refused_below_admin(cli, store, idp):
    sign_in_as(store, idp, "operator")
    code, out, err = await cli("pool-tokens", "create", "--name", "gpu-pods")
    assert (code, out) == (1, "")
    assert err.startswith("error: ")
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `uv run pytest packages/leader/tests/test_admin_api.py packages/leader/tests/test_admin_cli.py -q -k pool_token`
Expected: FAIL. The API answers `404` for `/v1/admin/pool-tokens`; the CLI tests stop in argparse (`SystemExit: 2`, "invalid choice: 'pool-tokens'").

- [ ] **Step 4: Add the view and the bodies**

In `packages/leader/src/swarmscribe_leader/reports.py`:

Replace

```python
    JoinToken,
    Recording,
    StorageLocation,
)
```

with

```python
    JoinToken,
    PoolToken,
    Recording,
    StorageLocation,
)
```

and append:

```python
def pool_token_view(token: PoolToken) -> dict[str, Any]:
    """A pool token as administrators see it: never the token or its hash."""
    return {
        "id": str(token.id),
        "name": token.name,
        "pool": token.pool,
        "registrations": token.registrations,
        "last_used_at": token.last_used_at,
        "revoked": token.revoked_at is not None,
        "revoked_at": token.revoked_at,
        "revoked_by": token.revoked_by,
        "created_by": token.created_by,
        "created_at": token.created_at,
    }


async def list_pool_tokens(session: AsyncSession) -> list[dict[str, Any]]:
    tokens = (
        await session.scalars(select(PoolToken).order_by(PoolToken.created_at, PoolToken.id))
    ).all()
    return [pool_token_view(token) for token in tokens]
```

Append to `packages/leader/src/swarmscribe_leader/api/admin_models.py`:

```python
class PoolTokenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=NAME_PATTERN)
    pool: str = Field(default="default", pattern=NAME_PATTERN)


class PoolTokenCreated(BaseModel):
    id: str
    name: str
    pool: str
    token: str


class PoolTokenOut(BaseModel):
    id: str
    name: str
    pool: str
    registrations: int
    last_used_at: datetime | None
    revoked: bool
    revoked_at: datetime | None
    revoked_by: str | None
    created_by: str
    created_at: datetime


class PoolTokenRevokeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Also revoke every follower the token registered (for a token that has leaked).
    revoke_followers: bool = Field(default=False, strict=True)


class PoolTokenRevoked(PoolTokenOut):
    followers_revoked: int
```

- [ ] **Step 5: Add the routes**

In `packages/leader/src/swarmscribe_leader/api/admin.py`:

Replace

```python
from ..auth import consoles, followers
from ..auth.followers import create_join_token
```

with

```python
from ..auth import consoles, followers, pool_tokens
from ..auth.followers import create_join_token
```

Replace

```python
    LoginConfig,
    PriorityIn,
    ScanRequested,
```

with

```python
    LoginConfig,
    PoolTokenCreated,
    PoolTokenIn,
    PoolTokenOut,
    PoolTokenRevoked,
    PoolTokenRevokeIn,
    PriorityIn,
    ScanRequested,
```

and append:

```python
@router.get("/pool-tokens", response_model=list[PoolTokenOut])
async def list_pool_tokens(admin: PersonAdministrator, session: Session) -> list[PoolTokenOut]:
    rows = await reports.list_pool_tokens(session)
    await _viewed(session, admin, "pool_tokens.view")
    return [PoolTokenOut.model_validate(row) for row in rows]


@router.post("/pool-tokens", response_model=PoolTokenCreated, status_code=201)
async def create_pool_token(
    body: PoolTokenIn, admin: PersonAdministrator, session: Session
) -> PoolTokenCreated:
    """The only response that carries a pool token. Nothing logs response bodies."""
    try:
        token, plaintext = await pool_tokens.create_pool_token(
            session, name=body.name, pool=body.pool, actor=admin.actor
        )
    except pool_tokens.InvalidPoolTokenName as exc:
        # The request model refuses bad names first; this keeps one code if it ever does not.
        raise pool_tokens.InvalidPoolTokenName(exc.message, code="invalid_request") from exc
    await session.commit()
    return PoolTokenCreated(id=str(token.id), name=token.name, pool=token.pool, token=plaintext)


@router.post("/pool-tokens/{name}/revoke", response_model=PoolTokenRevoked)
async def revoke_pool_token(
    name: str,
    admin: PersonAdministrator,
    session: Session,
    body: PoolTokenRevokeIn | None = None,
) -> PoolTokenRevoked:
    """A malformed name is answered 404 like any unknown one (the store decides)."""
    token, revoked = await pool_tokens.revoke_pool_token(
        session,
        name,
        now=utcnow(),
        actor=admin.actor,
        revoke_followers=body.revoke_followers if body is not None else False,
    )
    view = reports.pool_token_view(token)
    await session.commit()
    return PoolTokenRevoked.model_validate({**view, "followers_revoked": revoked})
```

- [ ] **Step 6: Add the CLI commands**

In `packages/leader/src/swarmscribe_leader/admin_cli/main.py`:

Replace

```python
    consent = commands.add_parser("consent", help="consent overview").add_subparsers(
        dest="action", required=True
    )
```

with

```python
    pool_tokens = commands.add_parser(
        "pool-tokens",
        help="non-expiring tokens for pools whose followers come and go (Kubernetes);"
        " administrators, signed in as a person",
    ).add_subparsers(dest="action", required=True)
    pool_create = pool_tokens.add_parser("create", help="a new pool token (shown once)")
    pool_create.add_argument("--name", required=True, help="e.g. gpu-pods")
    pool_create.add_argument("--pool", default="default")
    pool_tokens.add_parser("list")
    pool_revoke = pool_tokens.add_parser("revoke", help="refuse further registrations")
    pool_revoke.add_argument("name")
    pool_revoke.add_argument(
        "--revoke-followers",
        action="store_true",
        help="also revoke every follower the token registered (for a token that has leaked)",
    )

    consent = commands.add_parser("consent", help="consent overview").add_subparsers(
        dest="action", required=True
    )
```

Replace

```python
def print_console_credential(data: dict[str, Any], out: TextIO) -> None:
```

with

```python
def print_pool_token(data: dict[str, Any], out: TextIO) -> None:
    print(
        f"pool token (store this now; it will not be shown again): {_cell(data['token'])}",
        file=out,
    )
    print_fields({name: value for name, value in data.items() if name != "token"}, out)


def print_console_credential(data: dict[str, Any], out: TextIO) -> None:
```

Replace

```python
    if command == "consent":
        params = {"limit": args.limit}
```

with

```python
    if command == "pool-tokens":
        if action == "create":
            body = {"name": args.name, "pool": args.pool}
            return await post("/v1/admin/pool-tokens", body=body), print_pool_token
        if action == "list":
            columns = ("name", "pool", "registrations", "last_used_at", "revoked", "created_by")
            return await get("/v1/admin/pool-tokens"), table(columns)
        body = {"revoke_followers": True} if args.revoke_followers else None
        path = f"/v1/admin/pool-tokens/{_seg(args.name)}/revoke"
        return await post(path, body=body), print_fields
    if command == "consent":
        params = {"limit": args.limit}
```

- [ ] **Step 7: Run the tests to see them pass**

Run: `uv run pytest packages/leader/tests/test_admin_api.py packages/leader/tests/test_admin_cli.py -q`
Expected: PASS, the new tests and every existing one (among them `test_no_admin_route_is_hidden_or_mounted_as_a_sub_application`).

- [ ] **Step 8: Prove the console's allow-list is untouched**

Run: `uv run pytest packages/console/tests/test_proxy.py -q -k allow_list`
Expected: PASS. The three new routes are for people only (`consoles_allowed=False`), which the check leaves out, as it leaves out `/v1/admin/consoles`.

Run: `git status --short packages/console packages/console-web`
Expected: no output.

Run: `uv run ruff check packages/leader`
Expected: `All checks passed!`

- [ ] **Step 9: Commit**

```bash
git add packages/leader/src/swarmscribe_leader packages/leader/tests/test_admin_api.py packages/leader/tests/test_admin_cli.py
git commit -m "Leader admin: pool tokens, for administrators signed in as people"
```

---

### Task 6: Fresh links for the lease holder

**Files:**
- Modify: `packages/leader/src/swarmscribe_leader/errors.py`, `packages/leader/src/swarmscribe_leader/config.py`, `packages/leader/src/swarmscribe_leader/jobs/store.py`, `packages/leader/src/swarmscribe_leader/jobs/claims.py`, `packages/leader/src/swarmscribe_leader/api/follower.py`
- Test: `packages/leader/tests/test_follower_api.py`

**Interfaces:**
- Consumes: `LinksRequest`, `JobLinks` (Task 2); `Job.links_issued_at` (Task 3); `_locked_job`, `_require_lease`, `audit.record` (exist in `jobs/store.py`); in the tests, `register`, `claim`, `queue_one`, `Broken`, the `client` and `app` fixtures (exist in `test_follower_api.py`).
- Produces:
  - `POST /v1/jobs/{job_id}/links` body `{lease_id}` → `200 JobLinks`. `409 stale_lease` unless the caller holds the job's current lease; `403` for a revoked follower; `404` for an unknown job; `429 too_many_requests` with `Retry-After` when links were issued for this lease less than `links_refresh_min_seconds` ago (the claim counts); `503 unavailable` when storage is down.
  - `errors.TooManyRequests(message, *, retry_after: int)` (status 429, code `too_many_requests`).
  - `Settings.links_refresh_min_seconds: int = 60` (`SWARMSCRIBE_LINKS_REFRESH_MIN_SECONDS`; `0` turns the bound off).
  - `jobs.store.refresh_links(session, job_id, lease_id, follower, *, now, min_interval_seconds) -> Job`; `jobs.claims.build_links(session, job, *, settings, backend_factory) -> JobLinks`.
  - Audit action `job.links`, actor `follower:<id>`, subject the job, detail `{"attempt": n}`.
  - F1's `LeaderClient.links()` calls the route.

- [ ] **Step 1: Write the failing tests**

Append to `packages/leader/tests/test_follower_api.py`:

```python
# --- fresh links (follower spec 12.2) ----------------------------------------------------


@pytest.fixture
async def quick_links_app(engine, migrated_database_url):
    """A leader that allows a refresh at once, for tests that are not about the bound."""
    settings = Settings(
        database_url=migrated_database_url,
        public_url="http://leader",
        link_key="k" * 32,
        links_refresh_min_seconds=0,
    )
    application = create_app(settings, background=False)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def quick(quick_links_app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=quick_links_app), base_url="http://leader"
    ) as http:
        yield http


async def fresh(client, headers, claimed, lease_id=None):
    return await client.post(
        f"/v1/jobs/{claimed.job_id}/links",
        headers=headers,
        json={"lease_id": lease_id or claimed.lease_id},
    )


async def test_the_lease_holder_gets_fresh_links_that_work(quick, sessionmaker, factory, tmp_path):
    await queue_one(sessionmaker, factory, tmp_path, data=b"the recording")
    headers = await register(quick, sessionmaker)
    claimed = await claim(quick, headers)
    response = await fresh(quick, headers, claimed)
    assert response.status_code == 200, response.text
    links = response.json()
    assert set(links) == {"download_url", "upload_urls"}
    assert set(links["upload_urls"]) == {"txt", "srt", "segments_json"}
    assert (await quick.get(links["download_url"]["url"])).content == b"the recording"
    put = await quick.put(links["upload_urls"]["txt"]["url"], content=b"text\n")
    assert put.status_code == 201
    assert (tmp_path / "transcripts" / "talks" / "one.mp3.txt").read_bytes() == b"text\n"
    (entry,) = await audit_of(sessionmaker, "job.links")
    assert entry.detail == {"attempt": 1}
    assert entry.subject_id == claimed.job_id
    assert "/v1/files/" not in f"{entry.actor} {entry.detail}"


async def audit_of(sessionmaker, action):
    from swarmscribe_leader.db.models import AuditEntry

    async with sessionmaker() as session:
        return list(
            (await session.scalars(select(AuditEntry).where(AuditEntry.action == action))).all()
        )


async def test_fresh_links_do_not_extend_the_lease(quick, sessionmaker, factory, tmp_path):
    await queue_one(sessionmaker, factory, tmp_path)
    headers = await register(quick, sessionmaker)
    claimed = await claim(quick, headers)
    async with sessionmaker() as session:
        before = (await session.scalars(select(Job))).one().lease_expires_at
    assert (await fresh(quick, headers, claimed)).status_code == 200
    async with sessionmaker() as session:
        assert (await session.scalars(select(Job))).one().lease_expires_at == before


async def test_fresh_links_are_bounded_per_lease(client, sessionmaker, factory, tmp_path):
    await queue_one(sessionmaker, factory, tmp_path)
    headers = await register(client, sessionmaker)
    claimed = await claim(client, headers)
    early = await fresh(client, headers, claimed)  # the claim itself issued links just now
    assert (early.status_code, early.json()["code"]) == (429, "too_many_requests")
    assert 1 <= int(early.headers["retry-after"]) <= 60
    assert await audit_of(sessionmaker, "job.links") == []
    async with sessionmaker() as session:
        job = (await session.scalars(select(Job))).one()
        job.links_issued_at = utcnow() - timedelta(seconds=61)
        await session.commit()
    assert (await fresh(client, headers, claimed)).status_code == 200
    again = await fresh(client, headers, claimed)
    assert again.status_code == 429


async def test_fresh_links_need_the_current_lease(quick, sessionmaker, factory, tmp_path):
    await queue_one(sessionmaker, factory, tmp_path)
    owner = await register(quick, sessionmaker)
    claimed = await claim(quick, owner)
    intruder = await register(quick, sessionmaker)
    for headers, lease in (
        (intruder, claimed.lease_id),
        (owner, "00000000-0000-0000-0000-000000000000"),
        (owner, "not-a-lease"),
    ):
        response = await fresh(quick, headers, claimed, lease)
        assert (response.status_code, response.json()["code"]) == (409, "stale_lease")
    missing = await quick.post(
        f"/v1/jobs/{uuid.uuid4()}/links", headers=owner, json={"lease_id": claimed.lease_id}
    )
    assert missing.status_code == 404
    assert (await quick.post(f"/v1/jobs/{claimed.job_id}/links", json={})).status_code == 401
    assert await audit_of(sessionmaker, "job.links") == []


async def test_a_job_that_is_no_longer_leased_gets_no_links(quick, sessionmaker, factory, tmp_path):
    await queue_one(sessionmaker, factory, tmp_path)
    headers = await register(quick, sessionmaker)
    claimed = await claim(quick, headers)
    async with sessionmaker() as session:
        job = await session.get(Job, uuid.UUID(claimed.job_id), with_for_update=True)
        await store.cancel(session, job, now=utcnow(), reason="consent withdrawn")
        await session.commit()
    response = await fresh(quick, headers, claimed)
    assert (response.status_code, response.json()["code"]) == (409, "stale_lease")


async def test_the_old_holders_fresh_links_die_with_its_lease(
    quick, sessionmaker, factory, tmp_path
):
    await queue_one(sessionmaker, factory, tmp_path)
    slow = await register(quick, sessionmaker)
    first = await claim(quick, slow)
    links = (await fresh(quick, slow, first)).json()
    await reap(sessionmaker, now=utcnow() + timedelta(seconds=121), gone_after=timedelta(hours=1))
    fast = await register(quick, sessionmaker)
    await claim(quick, fast)
    late = await quick.put(links["upload_urls"]["txt"]["url"], content=b"old holder\n")
    assert (late.status_code, late.json()["code"]) == (409, "stale_lease")
    assert (await fresh(quick, slow, first)).status_code == 409


async def test_a_revoked_follower_gets_no_links(quick, sessionmaker, factory, tmp_path):
    await queue_one(sessionmaker, factory, tmp_path)
    headers = await register(quick, sessionmaker)
    claimed = await claim(quick, headers)
    async with sessionmaker() as session:
        (await session.scalars(select(Follower))).one().state = "revoked"
        await session.commit()
    assert (await fresh(quick, headers, claimed)).status_code == 403


async def test_links_when_storage_is_unavailable_are_503_and_not_counted(
    quick, quick_links_app, sessionmaker, factory, tmp_path
):
    location = await queue_one(sessionmaker, factory, tmp_path)
    headers = await register(quick, sessionmaker)
    claimed = await claim(quick, headers)
    real = quick_links_app.state.backend_factory
    quick_links_app.state.backend_factory = Broken(real, location.id)
    response = await fresh(quick, headers, claimed)
    assert (response.status_code, response.json()["code"]) == (503, "unavailable")
    assert await audit_of(sessionmaker, "job.links") == []
    quick_links_app.state.backend_factory = real
    assert (await fresh(quick, headers, claimed)).status_code == 200
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest packages/leader/tests/test_follower_api.py -q -k "links"`
Expected: FAIL. The route does not exist yet: `POST /v1/jobs/{id}/links` answers `404`. (`Settings(links_refresh_min_seconds=0)` raises nothing: unknown settings are ignored until step 3 adds this one.)

- [ ] **Step 3: The error and the setting**

In `packages/leader/src/swarmscribe_leader/errors.py`:

Replace

```python
class ServiceUnavailable(LeaderError):
```

with

```python
class TooManyRequests(LeaderError):
    """The caller asks too often; the answer carries `Retry-After`."""

    status = 429
    code = "too_many_requests"

    def __init__(self, message: str, *, retry_after: int):
        super().__init__(message)
        self.retry_after = retry_after


class ServiceUnavailable(LeaderError):
```

The error handler already sends `Retry-After` for any `LeaderError` whose `retry_after` is set (`api/errors.py`).

In `packages/leader/src/swarmscribe_leader/config.py`:

Replace

```python
    upload_link_ttl_seconds: int = Field(default=7200, gt=0)
```

with

```python
    upload_link_ttl_seconds: int = Field(default=7200, gt=0)
    # A lease holder may ask for fresh links this often, and no more.
    links_refresh_min_seconds: int = Field(default=60, ge=0)
```

- [ ] **Step 4: The store function**

In `packages/leader/src/swarmscribe_leader/jobs/store.py`:

Replace

```python
import uuid
from collections.abc import Awaitable, Callable, Collection
```

with

```python
import math
import uuid
from collections.abc import Awaitable, Callable, Collection
```

Replace

```python
from ..errors import Conflict, Forbidden, NotFound, StaleLease
```

with

```python
from ..errors import Conflict, Forbidden, NotFound, StaleLease, TooManyRequests
```

Replace

```python
    job.lease_expires_at = now + timedelta(seconds=lease_seconds)
    job.attempts += 1
    session.add(
```

with

```python
    job.lease_expires_at = now + timedelta(seconds=lease_seconds)
    job.links_issued_at = now
    job.attempts += 1
    session.add(
```

Replace

```python
def _same_checksums(result: JobResult, request: SubmitRequest) -> bool:
```

with

```python
async def refresh_links(
    session: AsyncSession,
    job_id: uuid.UUID,
    lease_id: str,
    follower: Follower,
    *,
    now: datetime,
    min_interval_seconds: int,
) -> Job:
    """Note that the lease holder is given fresh links, at most once per
    `min_interval_seconds` for a lease (the claim counts as the first). The lease is not
    extended: that is the heartbeat's. The caller builds the links and commits."""
    job = await _locked_job(session, job_id)
    _require_lease(job, lease_id, follower)
    if job.links_issued_at is not None:
        wait = min_interval_seconds - (now - job.links_issued_at).total_seconds()
        if wait > 0:
            raise TooManyRequests(
                "fresh links were issued for this lease a moment ago",
                retry_after=math.ceil(wait),
            )
    job.links_issued_at = now
    audit.record(
        session,
        actor=f"follower:{follower.id}",
        action="job.links",
        subject_type="job",
        subject_id=job.id,
        detail={"attempt": job.attempts},
    )
    return job


def _same_checksums(result: JobResult, request: SubmitRequest) -> bool:
```

- [ ] **Step 5: Build links in one place**

In `packages/leader/src/swarmscribe_leader/jobs/claims.py`:

Replace

```python
from swarmscribe_protocol import (
    ClaimResponse,
    JobSettings,
    OutputChecksums,
    SegmentsDocument,
    UploadUrls,
    Vocabulary,
)
```

with

```python
from swarmscribe_protocol import (
    ClaimResponse,
    JobLinks,
    JobSettings,
    OutputChecksums,
    SegmentsDocument,
    UploadUrls,
    Vocabulary,
)
```

Replace

```python
async def build_claim(
```

with

```python
async def build_links(
    session: AsyncSession, job: Job, *, settings: Settings, backend_factory: BackendFactory
) -> JobLinks:
    """The job's download link and three upload links, bound to its current lease."""
    recording, source, target = await _places(session, job)
    download = backend_factory(source).download_link(
        recording.key, job.source_version, timedelta(seconds=settings.download_link_ttl_seconds)
    )
    uploader = backend_factory(target)
    upload_ttl = timedelta(seconds=settings.upload_link_ttl_seconds)
    uploads = UploadUrls(
        **{
            name: uploader.upload_link(
                key, upload_ttl, job_id=str(job.id), lease_id=str(job.lease_id)
            )
            for name, key in output_keys(source.output_prefix, recording.key).items()
        }
    )
    return JobLinks(download_url=download, upload_urls=uploads)


async def build_claim(
```

Replace

```python
    recording, source, target = await _places(session, job)
    download = backend_factory(source).download_link(
        recording.key, job.source_version, timedelta(seconds=settings.download_link_ttl_seconds)
    )
    uploader = backend_factory(target)
    upload_ttl = timedelta(seconds=settings.upload_link_ttl_seconds)
    uploads = UploadUrls(
        **{
            name: uploader.upload_link(
                key, upload_ttl, job_id=str(job.id), lease_id=str(job.lease_id)
            )
            for name, key in output_keys(source.output_prefix, recording.key).items()
        }
    )
    device = device_of(follower)
```

with

```python
    _recording, source, _target = await _places(session, job)
    links = await build_links(session, job, settings=settings, backend_factory=backend_factory)
    download, uploads = links.download_url, links.upload_urls
    device = device_of(follower)
```

- [ ] **Step 6: The route**

In `packages/leader/src/swarmscribe_leader/api/follower.py`:

Replace

```python
from swarmscribe_protocol import (
    ClaimResponse,
    FailRequest,
    HeartbeatRequest,
    HeartbeatResponse,
    OutputChecksums,
```

with

```python
from swarmscribe_protocol import (
    ClaimResponse,
    FailRequest,
    HeartbeatRequest,
    HeartbeatResponse,
    JobLinks,
    LinksRequest,
    OutputChecksums,
```

Replace

```python
from ..jobs.claims import build_claim, device_of, outputs_unchanged, outputs_verified, profile_for
```

with

```python
from ..jobs.claims import (
    build_claim,
    build_links,
    device_of,
    outputs_unchanged,
    outputs_verified,
    profile_for,
)
```

Replace

```python
@router.post("/jobs/{job_id}/submit", response_model=SubmitResponse)
```

with

```python
@router.post("/jobs/{job_id}/links", response_model=JobLinks)
async def fresh_links(
    job_id: uuid.UUID,
    body: LinksRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    follower: Annotated[Follower, Depends(current_follower)],
) -> JobLinks:
    """New links for the job, for the follower holding its lease: a job that outlasts its
    links (two hours for uploads) asks here. The links carry the same lease, so they stop
    working when it ends, like the ones the claim gave."""
    settings = settings_of(request)
    job = await store.refresh_links(
        session,
        job_id,
        body.lease_id,
        follower,
        now=utcnow(),
        min_interval_seconds=settings.links_refresh_min_seconds,
    )
    links = await build_links(
        session, job, settings=settings, backend_factory=request.app.state.backend_factory
    )
    await session.commit()
    return links


@router.post("/jobs/{job_id}/submit", response_model=SubmitResponse)
```

The route records the issue (`refresh_links`) and builds the links in one transaction: if building fails (storage unavailable), nothing is committed, so neither the audit entry nor `links_issued_at` is written and the follower's retry is not bounded out.

- [ ] **Step 7: Run the tests to see them pass**

Run: `uv run pytest packages/leader/tests/test_follower_api.py packages/leader/tests/test_job_store.py packages/leader/tests/test_end_to_end.py -q`
Expected: PASS. The existing claim tests prove `build_claim` still issues the same links.

Run: `uv run pytest packages/leader -q && uv run ruff check packages/leader`
Expected: PASS; `All checks passed!`

- [ ] **Step 8: Commit**

```bash
git add packages/leader/src/swarmscribe_leader packages/leader/tests/test_follower_api.py
git commit -m "Leader: fresh links for the follower holding a job's lease, bounded per lease"
```

---

### Task 7: The drain signal on an empty claim

**Files:**
- Modify: `packages/leader/src/swarmscribe_leader/api/follower.py`
- Test: `packages/leader/tests/test_follower_api.py`

**Interfaces:**
- Consumes: `DIRECTIVE_HEADER` (Task 2).
- Produces: `POST /v1/jobs/claim` answered `204` to a follower whose state is `draining` carries the header `X-SwarmScribe-Directive: drain` beside `Retry-After`. Every other `204` carries no such header. F1's `LeaderClient.claim()` reads it into `NoWork.draining`.

- [ ] **Step 1: Write the failing tests**

Append to `packages/leader/tests/test_follower_api.py`:

```python
# --- the drain signal (follower spec 12.3) -----------------------------------------------


async def test_an_idle_draining_follower_is_told_it_is_draining(
    client, sessionmaker, factory, tmp_path
):
    await queue_one(sessionmaker, factory, tmp_path)
    headers = await register(client, sessionmaker)
    idle = await client.post("/v1/jobs/claim", headers=await register(client, sessionmaker))
    assert idle.status_code == 200  # an active follower gets the job, and no directive
    empty = await client.post("/v1/jobs/claim", headers=headers)
    assert (empty.status_code, empty.headers.get("x-swarmscribe-directive")) == (204, None)
    async with sessionmaker() as session:
        for follower in (await session.scalars(select(Follower))).all():
            follower.state = "draining"
        await session.commit()
    told = await client.post("/v1/jobs/claim", headers=headers)
    assert told.status_code == 204
    assert told.headers["x-swarmscribe-directive"] == "drain"
    assert told.headers["retry-after"] == "10"
    assert told.content == b""


async def test_a_drain_survives_deregistering_and_coming_back(client, sessionmaker):
    headers = await register(client, sessionmaker)
    async with sessionmaker() as session:
        (await session.scalars(select(Follower))).one().state = "draining"
        await session.commit()
    assert (await client.post("/v1/followers/deregister", headers=headers)).status_code == 204
    back = await client.post("/v1/jobs/claim", headers=headers)
    assert (back.status_code, back.headers["x-swarmscribe-directive"]) == (204, "drain")
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest packages/leader/tests/test_follower_api.py -q -k "told_it_is_draining or drain_survives"`
Expected: FAIL with `KeyError: 'x-swarmscribe-directive'`.

- [ ] **Step 3: Send the header**

In `packages/leader/src/swarmscribe_leader/api/follower.py`:

Replace

```python
from swarmscribe_protocol import (
    ClaimResponse,
```

with

```python
from swarmscribe_protocol import (
    DIRECTIVE_HEADER,
    ClaimResponse,
```

Replace

```python
def _no_work(request: Request) -> Response:
    return Response(
        status_code=204, headers={"Retry-After": str(settings_of(request).claim_retry_after)}
    )
```

with

```python
def _no_work(request: Request, *, draining: bool = False) -> Response:
    """Nothing to hand out. A draining follower is told so, here, because an idle follower
    makes no other call: without it, "you are draining" and "the queue is empty" look the
    same and an idle follower could never wind down."""
    headers = {"Retry-After": str(settings_of(request).claim_retry_after)}
    if draining:
        headers[DIRECTIVE_HEADER] = "drain"
    return Response(status_code=204, headers=headers)
```

Replace

```python
    if follower.state == "draining":
        await session.commit()
        return _no_work(request)
```

with

```python
    if follower.state == "draining":
        await session.commit()
        return _no_work(request, draining=True)
```

The two other `_no_work(request)` calls (no profile for the device; nothing claimable) are unchanged: only a draining follower is told to drain.

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest packages/leader/tests/test_follower_api.py packages/leader/tests/test_compose_driver.py -q`
Expected: PASS. `test_compose_driver.py` runs the scripted followers of `e2e/compose/run_e2e.py` in-process: an existing caller that ignores the header is unaffected.

Run: `uv run ruff check packages/leader`
Expected: `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add packages/leader/src/swarmscribe_leader/api/follower.py packages/leader/tests/test_follower_api.py
git commit -m "Leader: tell a draining follower so on every empty claim"
```

---

### Task 8: Settings profiles in the admin API and CLI

The leader spec lists `profiles edit`; no command exists, so a profile can be changed only in the database. F2's Compose test needs `tiny.en`, and an operator needs it to choose models.

**Files:**
- Create: `packages/leader/src/swarmscribe_leader/profiles.py`
- Modify: `packages/leader/src/swarmscribe_leader/api/admin_models.py`, `packages/leader/src/swarmscribe_leader/api/admin.py`, `packages/leader/src/swarmscribe_leader/admin_cli/main.py`
- Test: `packages/leader/tests/test_admin_api.py`, `packages/leader/tests/test_admin_cli.py`

**Interfaces:**
- Consumes: `SettingsProfile` (exists; one row per device, seeded by migration `0001`: `cuda` → `large-v3`/`float16`, `cpu` → `distil-large-v3`/`int8`, ladder `[0.0, 0.2, 0.4]`); `Device`, `MAX_TEMPERATURE` from `swarmscribe_protocol`; `for_people_with_the_role` (Task 5, in `test_admin_api.py`).
- Produces:
  - `GET /v1/admin/profiles` → `200 list[{device, name, model, compute_type, temperatures}]`. Role `viewer`, a person only. Audited `profiles.view`.
  - `POST /v1/admin/profiles/{device}` (`device` is `cuda` or `cpu`) body `{model, compute_type, temperatures?}` → `200` the profile. Role `admin`, a person only. `422 invalid_request` for a model that is not a plain name or `owner/name`, an unknown compute type, an empty or out-of-range ladder, an unknown field or an unknown device.
  - `profiles.list_profiles(session) -> list[dict]`, `profiles.set_profile(session, device, *, model, compute_type, temperatures, actor) -> SettingsProfile`, `profiles.profile_view(profile) -> dict`.
  - Audit action `profile.set` with `{device, model, compute_type, temperatures, before: {model, compute_type}}`.
  - CLI: `swarmscribe-admin profiles list`, `profiles set {cuda,cpu} --model M --compute-type C [--temperatures 0,0.2,0.4]`.

- [ ] **Step 1: Write the failing API tests**

Append to `packages/leader/tests/test_admin_api.py`:

```python
# --- settings profiles (follower spec 12.4) -----------------------------------------------


@pytest.fixture
async def restore_profiles(sessionmaker):
    """settings_profiles is seeded by a migration and kept between tests: put it back."""
    from swarmscribe_leader.db.models import SettingsProfile

    async with sessionmaker() as session:
        before = {
            row.device: (row.model, row.compute_type, list(row.temperatures))
            for row in (await session.scalars(select(SettingsProfile))).all()
        }
    yield
    async with sessionmaker() as session:
        for row in (await session.scalars(select(SettingsProfile))).all():
            if row.device in before:
                row.model, row.compute_type, row.temperatures = before[row.device]
            else:
                await session.delete(row)
        await session.commit()


async def test_profiles_are_listed_per_device(admin_client, idp):
    rows = await get(admin_client, idp, "/v1/admin/profiles")
    assert {row["device"]: (row["model"], row["compute_type"]) for row in rows} == {
        "cpu": ("distil-large-v3", "int8"),
        "cuda": ("large-v3", "float16"),
    }
    assert all(row["temperatures"] == [0.0, 0.2, 0.4] for row in rows)


async def test_a_changed_profile_is_what_the_next_claim_carries(
    admin_client, idp, sessionmaker, factory, tmp_path, restore_profiles
):
    response = await post(
        admin_client,
        idp,
        "/v1/admin/profiles/cpu",
        "admin",
        {"model": "tiny.en", "compute_type": "int8", "temperatures": [0.0, 0.2]},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "device": "cpu",
        "name": "cpu",
        "model": "tiny.en",
        "compute_type": "int8",
        "temperatures": [0.0, 0.2],
    }
    (entry,) = await audit_rows(sessionmaker, "profile.set")
    assert entry.actor == actor(idp, "admin")
    assert entry.detail == {
        "device": "cpu",
        "model": "tiny.en",
        "compute_type": "int8",
        "temperatures": [0.0, 0.2],
        "before": {"model": "distil-large-v3", "compute_type": "int8"},
    }
    location = await factory.location(name="here")
    await factory.job(await factory.recording(location, key="talks/a.mp3"))
    write(tmp_path, "talks/a.mp3")
    _, credential = await factory.follower()
    claimed = await admin_client.post(
        "/v1/jobs/claim", headers={"Authorization": f"Bearer {credential}"}
    )
    assert claimed.status_code == 200, claimed.text
    settings = claimed.json()["settings"]
    assert (settings["model"], settings["temperatures"]) == ("tiny.en", [0.0, 0.2])


async def test_a_profile_keeps_its_ladder_when_none_is_given(
    admin_client, idp, restore_profiles
):
    body = {"model": "owner/custom-model", "compute_type": "float32"}
    response = await post(admin_client, idp, "/v1/admin/profiles/cuda", "admin", body)
    assert response.json()["temperatures"] == [0.0, 0.2, 0.4]
    assert response.json()["model"] == "owner/custom-model"


@pytest.mark.parametrize(
    "body",
    [
        {"model": "/models/large-v3", "compute_type": "int8"},
        {"model": "..\\models", "compute_type": "int8"},
        {"model": "C:/models/x", "compute_type": "int8"},
        {"model": "a/b/c", "compute_type": "int8"},
        {"model": "", "compute_type": "int8"},
        {"model": "tiny.en", "compute_type": "float128"},
        {"model": "tiny.en", "compute_type": "int8", "temperatures": []},
        {"model": "tiny.en", "compute_type": "int8", "temperatures": [0.5]},
        {"model": "tiny.en", "compute_type": "int8", "language": "fr"},
        {"model": "tiny.en"},
    ],
)
async def test_invalid_profiles_are_refused_and_change_nothing(
    admin_client, idp, sessionmaker, body
):
    response = await post(admin_client, idp, "/v1/admin/profiles/cpu", "admin", body)
    assert (response.status_code, response.json()["code"]) == (422, "invalid_request")
    assert await audit_rows(sessionmaker, "profile.set") == []
    rows = await get(admin_client, idp, "/v1/admin/profiles")
    assert next(row for row in rows if row["device"] == "cpu")["model"] == "distil-large-v3"


async def test_a_profile_for_an_unknown_device_is_422(admin_client, idp):
    body = {"model": "tiny.en", "compute_type": "int8"}
    response = await post(admin_client, idp, "/v1/admin/profiles/tpu", "admin", body)
    assert response.status_code == 422


PROFILE_ROUTES = [
    ("GET", "/v1/admin/profiles", None, "viewer"),
    ("POST", "/v1/admin/profiles/cpu", {"model": "tiny.en", "compute_type": "int8"}, "admin"),
]


@pytest.mark.parametrize(
    "method, path, body, role", PROFILE_ROUTES, ids=[f"{m} {p}" for m, p, _, _ in PROFILE_ROUTES]
)
async def test_profile_routes_are_for_people_with_the_role(
    admin_client, idp, factory, restore_profiles, method, path, body, role
):
    await for_people_with_the_role(admin_client, idp, factory, method, path, body, role)
```

`settings_profiles` is one of the two tables the test fixtures do not empty between tests (it is seeded by a migration), which is why every test that changes a profile uses `restore_profiles`.

- [ ] **Step 2: Write the failing CLI test**

Append to `packages/leader/tests/test_admin_cli.py`:

```python
# --- settings profiles (follower spec 12.4) ----------------------------------------------


async def test_profiles_list_and_set(cli, store, idp, sessionmaker):
    from swarmscribe_leader.db.models import SettingsProfile

    sign_in_as(store, idp, "admin")
    code, out, err = await cli("profiles", "list")
    assert code == 0, err
    assert "distil-large-v3" in out and "large-v3" in out
    try:
        change = ("profiles", "set", "cpu", "--compute-type", "int8")
        code, out, err = await cli(*change, "--model", "tiny.en", "--temperatures", "0,0.2")
        assert code == 0, err
        assert "model: tiny.en" in out and "temperatures: 0.0, 0.2" in out
        code, _out, err = await cli(*change, "--model", "../x")
        assert code == 1
    finally:
        async with sessionmaker() as session:
            row = await session.scalar(
                select(SettingsProfile).where(SettingsProfile.device == "cpu")
            )
            row.model, row.compute_type = "distil-large-v3", "int8"
            row.temperatures = [0.0, 0.2, 0.4]
            await session.commit()
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `uv run pytest packages/leader/tests/test_admin_api.py packages/leader/tests/test_admin_cli.py -q -k profile`
Expected: FAIL. `/v1/admin/profiles` answers `404`; the CLI test stops in argparse (`SystemExit: 2`, "invalid choice: 'profiles'").

- [ ] **Step 4: The service**

Create `packages/leader/src/swarmscribe_leader/profiles.py`:

```python
"""Settings profiles: the model, compute type and temperature ladder each device class
transcribes with. One per device; a claim carries the caller's device's profile, read at
the claim, so a change applies to every job claimed after it."""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import audit
from .db.models import SettingsProfile


def profile_view(profile: SettingsProfile) -> dict[str, Any]:
    return {
        "device": profile.device,
        "name": profile.name,
        "model": profile.model,
        "compute_type": profile.compute_type,
        "temperatures": [float(value) for value in profile.temperatures],
    }


async def list_profiles(session: AsyncSession) -> list[dict[str, Any]]:
    profiles = (
        await session.scalars(select(SettingsProfile).order_by(SettingsProfile.device))
    ).all()
    return [profile_view(profile) for profile in profiles]


async def set_profile(
    session: AsyncSession,
    device: str,
    *,
    model: str,
    compute_type: str,
    temperatures: list[float] | None,
    actor: str,
) -> SettingsProfile:
    """Change the device's profile (create it if the row is missing). `temperatures` None
    keeps the ladder it has."""
    profile = await session.scalar(
        select(SettingsProfile)
        .where(SettingsProfile.device == device)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if profile is None:
        profile = SettingsProfile(
            id=uuid.uuid4(), name=device, device=device, temperatures=[0.0, 0.2, 0.4]
        )
        session.add(profile)
    before = {"model": profile.model, "compute_type": profile.compute_type}
    profile.model = model
    profile.compute_type = compute_type
    if temperatures is not None:
        profile.temperatures = list(temperatures)
    audit.record(
        session,
        actor=actor,
        action="profile.set",
        subject_type="settings_profile",
        subject_id=profile.id,
        detail={
            "device": device,
            "model": model,
            "compute_type": compute_type,
            "temperatures": list(profile.temperatures),
            "before": before,
        },
    )
    return profile
```

- [ ] **Step 5: The bodies**

In `packages/leader/src/swarmscribe_leader/api/admin_models.py`:

Replace

```python
from swarmscribe_protocol import DEFAULT_CHANNEL_LABELS, ChannelLabels, ChannelMode
```

with

```python
from swarmscribe_protocol import (
    DEFAULT_CHANNEL_LABELS,
    MAX_TEMPERATURE,
    ChannelLabels,
    ChannelMode,
    Device,
)
```

and append:

```python
# A model is named, never located: followers load it by this name, and a path here would
# make them read their own disks. `owner/name` is a Hugging Face repository.
MODEL_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}(/[A-Za-z0-9][A-Za-z0-9._-]{0,63})?$"
ComputeType = Literal[
    "int8", "int8_float16", "int8_float32", "int8_bfloat16", "int16", "float16", "bfloat16",
    "float32",
]


class ProfileOut(BaseModel):
    device: Device
    name: str
    model: str
    compute_type: str
    temperatures: list[float]


class ProfileIn(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    model: str = Field(pattern=MODEL_PATTERN, max_length=100)
    compute_type: ComputeType
    temperatures: list[float] | None = Field(default=None, min_length=1, max_length=10)

    @field_validator("temperatures")
    @classmethod
    def _ladder_is_clamped(cls, value: list[float] | None) -> list[float] | None:
        if value is not None and any(not (0.0 <= t <= MAX_TEMPERATURE) for t in value):
            raise ValueError(f"temperatures must be between 0.0 and {MAX_TEMPERATURE}")
        return value
```

`protected_namespaces=()` lets a Pydantic model have a field called `model`.

- [ ] **Step 6: The routes**

In `packages/leader/src/swarmscribe_leader/api/admin.py`:

Replace

```python
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit, reports
```

with

```python
from sqlalchemy.ext.asyncio import AsyncSession
from swarmscribe_protocol import Device

from .. import audit, profiles, reports
```

Replace

```python
    PriorityIn,
    ScanRequested,
```

with

```python
    PriorityIn,
    ProfileIn,
    ProfileOut,
    ScanRequested,
```

Replace

```python
PersonAdministrator = Annotated[Admin, Depends(require("admin", consoles_allowed=False))]
```

with

```python
PersonAdministrator = Annotated[Admin, Depends(require("admin", consoles_allowed=False))]
# Read by people only, because the fleet console has no page for it yet (follower spec 12.4).
PersonViewer = Annotated[Admin, Depends(require("viewer", consoles_allowed=False))]
```

and append:

```python
@router.get("/profiles", response_model=list[ProfileOut])
async def list_profiles(admin: PersonViewer, session: Session) -> list[ProfileOut]:
    rows = await profiles.list_profiles(session)
    await _viewed(session, admin, "profiles.view")
    return [ProfileOut.model_validate(row) for row in rows]


@router.post("/profiles/{device}", response_model=ProfileOut)
async def set_profile(
    device: Device, body: ProfileIn, admin: PersonAdministrator, session: Session
) -> ProfileOut:
    """Applies to every job claimed from now on; a job already leased keeps what it has."""
    profile = await profiles.set_profile(
        session,
        device,
        model=body.model,
        compute_type=body.compute_type,
        temperatures=body.temperatures,
        actor=admin.actor,
    )
    view = profiles.profile_view(profile)
    await session.commit()
    return ProfileOut.model_validate(view)
```

- [ ] **Step 7: The CLI commands**

In `packages/leader/src/swarmscribe_leader/admin_cli/main.py`:

Replace

```python
def build_parser() -> argparse.ArgumentParser:
```

with

```python
def parse_temperatures(text: str) -> list[float]:
    try:
        return [float(part) for part in text.split(",")]
    except ValueError:
        raise argparse.ArgumentTypeError(
            "give numbers separated by commas, e.g. 0,0.2,0.4"
        ) from None


def build_parser() -> argparse.ArgumentParser:
```

Replace

```python
    consent = commands.add_parser("consent", help="consent overview").add_subparsers(
        dest="action", required=True
    )
```

with

```python
    profiles = commands.add_parser(
        "profiles", help="the model each device class transcribes with"
    ).add_subparsers(dest="action", required=True)
    profiles.add_parser("list")
    profile_set = profiles.add_parser("set", help="change a device class's model")
    profile_set.add_argument("device", choices=("cuda", "cpu"))
    profile_set.add_argument("--model", required=True, help="e.g. large-v3")
    profile_set.add_argument("--compute-type", required=True, help="e.g. float16 or int8")
    profile_set.add_argument(
        "--temperatures", type=parse_temperatures, help="e.g. 0,0.2,0.4 (default: unchanged)"
    )

    consent = commands.add_parser("consent", help="consent overview").add_subparsers(
        dest="action", required=True
    )
```

Replace

```python
    if command == "consent":
        params = {"limit": args.limit}
```

with

```python
    if command == "profiles":
        if action == "list":
            columns = ("device", "model", "compute_type", "temperatures")
            return await get("/v1/admin/profiles"), table(columns)
        body = {"model": args.model, "compute_type": args.compute_type}
        if args.temperatures is not None:
            body["temperatures"] = args.temperatures
        return await post(f"/v1/admin/profiles/{_seg(args.device)}", body=body), print_fields
    if command == "consent":
        params = {"limit": args.limit}
```

- [ ] **Step 8: Run the tests to see them pass**

Run: `uv run pytest packages/leader/tests/test_admin_api.py packages/leader/tests/test_admin_cli.py packages/leader/tests/test_follower_api.py -q`
Expected: PASS. `test_follower_api.py` still sees the seeded profiles (`distil-large-v3`, `large-v3`): every test here that changes one puts it back.

- [ ] **Step 9: Prove the console's allow-list is untouched**

Run: `uv run pytest packages/console/tests/test_proxy.py -q -k allow_list`
Expected: PASS.

Run: `git status --short packages/console packages/console-web`
Expected: no output.

Run: `uv run ruff check packages/leader`
Expected: `All checks passed!`

- [ ] **Step 10: Commit**

```bash
git add packages/leader/src/swarmscribe_leader packages/leader/tests/test_admin_api.py packages/leader/tests/test_admin_cli.py
git commit -m "Leader admin: list and set settings profiles"
```

---

### Task 9: The leader's test kit, and the README

F1 tests the follower against the real leader application. Fixtures do not cross packages, so the leader offers a plain module of helpers.

**Files:**
- Create: `packages/leader/tests/leader_testkit.py`, `packages/leader/tests/test_testkit.py`
- Modify: `packages/leader/tests/conftest.py`, `README.md`

**Interfaces:**
- Consumes: `upgrade` (`db/migrate.py`), `add_location` (`ingest/locations.py`), `scan_location` (`ingest/scanner.py`), `backend_for` (`storage/registry.py`), `LinkSigner`, `create_join_token`, `Base` (all exist).
- Produces, in `leader_testkit` (importable once its folder is on `sys.path`):
  - `LINK_KEY: str` (32 characters, for `Settings(link_key=...)`)
  - `admin_url() -> str`, `with_database(url, name) -> str`, `async recreate(url, name) -> None`
  - `async migrated_database(name: str) -> str` — drop, create and migrate the database `name`; its URL
  - `async empty_tables(session) -> None` — empty every table but `alembic_version` and `settings_profiles`
  - `async add_recordings(sessionmaker, root: Path, files: dict[str, bytes], *, name="testkit", consent="**/*\n", public_url="http://leader", **location) -> StorageLocation` — write the files and a `consent.txt`, add the folder as a location, scan it once
  - `async new_join_token(sessionmaker, *, pool="default", max_uses=5) -> str`
  - F1's `packages/follower/tests/test_real_leader.py` uses all of them.

- [ ] **Step 1: Write the failing tests**

Create `packages/leader/tests/test_testkit.py`:

```python
import httpx
from leader_testkit import LINK_KEY, add_recordings, empty_tables, new_join_token
from sqlalchemy import select
from swarmscribe_leader.app import create_app
from swarmscribe_leader.config import Settings
from swarmscribe_leader.db.models import Job, Recording, SettingsProfile


async def test_the_kit_queues_consented_recordings_a_follower_can_claim_and_fetch(
    sessionmaker, migrated_database_url, tmp_path
):
    await add_recordings(
        sessionmaker,
        tmp_path / "archive",
        {"talks/one.wav": b"one", "private/two.wav": b"two"},
        consent="talks/*\n",
        channel_mode="auto",
    )
    token = await new_join_token(sessionmaker)
    settings = Settings(
        database_url=migrated_database_url, public_url="http://leader", link_key=LINK_KEY
    )
    app = create_app(settings, background=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://leader"
        ) as client:
            capabilities = {
                "device": "cpu",
                "models": [],
                "engine_version": "0.1.0",
                "pool": "default",
            }
            joined = await client.post(
                "/v1/followers/register",
                json={"join_token": token, "protocol_version": 1, "capabilities": capabilities},
            )
            headers = {"Authorization": f"Bearer {joined.json()['credential']}"}
            claimed = (await client.post("/v1/jobs/claim", headers=headers)).json()
            assert claimed["settings"]["channel_mode"] == "auto"
            assert (await client.get(claimed["download_url"]["url"])).content == b"one"
            assert (await client.post("/v1/jobs/claim", headers=headers)).status_code == 204
    async with sessionmaker() as session:
        assert len((await session.scalars(select(Recording))).all()) == 2
        assert len((await session.scalars(select(Job))).all()) == 1


async def test_emptying_the_tables_keeps_the_seeded_profiles(sessionmaker, tmp_path):
    await add_recordings(sessionmaker, tmp_path / "archive", {"a.wav": b"a"})
    async with sessionmaker() as session:
        await empty_tables(session)
    async with sessionmaker() as session:
        assert (await session.scalars(select(Job))).all() == []
        assert len((await session.scalars(select(SettingsProfile))).all()) == 2
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest packages/leader/tests/test_testkit.py -q`
Expected: FAIL at import: `ModuleNotFoundError: No module named 'leader_testkit'`.

- [ ] **Step 3: Write the kit**

Create `packages/leader/tests/leader_testkit.py`:

```python
"""Helpers for tests, in this package and in others, that need a real leader: a migrated
database, recordings queued as jobs, a token to join with. conftest.py puts this folder on
sys.path; another package's conftest does the same to use it (the follower's does).

Nothing here is a fixture: fixtures do not cross packages. Everything is a plain function
or coroutine, and nothing prints a token, a credential or a link."""

import os
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from swarmscribe_leader.auth.followers import create_join_token
from swarmscribe_leader.clock import utcnow
from swarmscribe_leader.db.migrate import upgrade
from swarmscribe_leader.db.models import Base, StorageLocation
from swarmscribe_leader.ingest.locations import add_location
from swarmscribe_leader.ingest.scanner import scan_location
from swarmscribe_leader.storage.links import LinkSigner
from swarmscribe_leader.storage.registry import backend_for

REPO_ROOT = Path(__file__).resolve().parents[3]
LINK_KEY = "k" * 32
KEEP_TABLES = {"alembic_version", "settings_profiles"}


def admin_url() -> str:
    """A Postgres to create test databases in: $SWARMSCRIBE_TEST_DATABASE_URL, or a local
    server started from the repository's .pgdata folder."""
    url = os.environ.get("SWARMSCRIBE_TEST_DATABASE_URL")
    if url:
        return url
    import pgserver

    server = pgserver.get_server(str(REPO_ROOT / ".pgdata"), cleanup_mode="stop")
    return server.get_uri()


def with_database(url: str, name: str) -> str:
    return urlunsplit(urlsplit(url)._replace(path="/" + name))


async def recreate(url: str, name: str) -> None:
    conn = await asyncpg.connect(url)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()


async def migrated_database(name: str) -> str:
    """Drop and recreate the database `name`, migrate it to the head, return its URL."""
    import asyncio

    url = admin_url()
    await recreate(url, name)
    database = with_database(url, name)
    await asyncio.to_thread(upgrade, database)
    return database


async def empty_tables(session: AsyncSession) -> None:
    """Empty every table but the migration's own rows (the seeded settings profiles)."""
    tables = [t.name for t in reversed(Base.metadata.sorted_tables) if t.name not in KEEP_TABLES]
    await session.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    await session.commit()


async def add_recordings(
    sessionmaker: async_sessionmaker[AsyncSession],
    root: Path,
    files: dict[str, bytes],
    *,
    name: str = "testkit",
    consent: str = "**/*\n",
    public_url: str = "http://leader",
    **location: object,
) -> StorageLocation:
    """Write `files` (key -> content) and a consent.txt under `root`, add the folder as a
    location and scan it once, so that every consented file is a queued job. `location`
    passes pool, required_device, channel_mode or channel_labels to the location."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "consent.txt").write_text(consent, encoding="utf-8")
    for key, data in files.items():
        path = root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    values = {
        "name": name,
        "root": str(root),
        "input_prefix": "",
        "output_prefix": "transcripts/",
        "pool": "default",
        "required_device": "any",
        "scan_interval_s": 900,
        "channel_mode": "mono",
        "channel_labels": ("Left", "Right"),
    }
    values.update(location)
    async with sessionmaker() as session:
        added = await add_location(session, **values, actor="testkit")
        backend = backend_for(
            added, signer=LinkSigner(LINK_KEY.encode()), public_url=public_url
        )
        await scan_location(session, added, backend, now=utcnow(), max_attempts=3)
        await session.commit()
    return added


async def new_join_token(
    sessionmaker: async_sessionmaker[AsyncSession], *, pool: str = "default", max_uses: int = 5
) -> str:
    async with sessionmaker() as session:
        _, plaintext = await create_join_token(
            session,
            pool=pool,
            expires_at=utcnow() + timedelta(days=1),
            max_uses=max_uses,
            created_by="testkit",
        )
        await session.commit()
    return plaintext
```

- [ ] **Step 4: Put the kit's folder on the path**

In `packages/leader/tests/conftest.py`:

Replace

```python
import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
```

with

```python
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

# leader_testkit (helpers shared with other packages' tests) sits beside this file.
sys.path.insert(0, str(Path(__file__).parent))
```

Nothing else in `conftest.py` changes: its fixtures keep their own copies of the database helpers.

- [ ] **Step 5: Run the tests to see them pass**

Run: `uv run pytest packages/leader/tests/test_testkit.py -q`
Expected: PASS (2 tests).

- [ ] **Step 6: Document it**

In `README.md`:

Replace

````markdown
| `SWARMSCRIBE_PUBLIC_URL` | the leader's external base URL, used in file links |
````

with

````markdown
| `SWARMSCRIBE_PUBLIC_URL` | the leader's external base URL, used in file links: an address every follower can reach (the same one for pods and for outside machines) |
````

Replace

````markdown
| admin | operator + `locations add/disable/enable`, `tokens create/list/revoke`, `followers revoke`, `console create/list/revoke` (a signed-in person only, never a console) |
````

with

````markdown
| admin | operator + `locations add/disable/enable`, `tokens create/list/revoke`, `followers revoke`; and, signed in as a person only (never through a console), `console create/list/revoke`, `pool-tokens create/list/revoke` and `profiles set` |
````

Replace

````markdown
uv run swarmscribe-admin console list
uv run swarmscribe-admin console revoke fleet
```
````

with

````markdown
uv run swarmscribe-admin console list
uv run swarmscribe-admin console revoke fleet
uv run swarmscribe-admin pool-tokens create --name gpu-pods --pool gpu
uv run swarmscribe-admin pool-tokens revoke gpu-pods
uv run swarmscribe-admin profiles list
uv run swarmscribe-admin profiles set cpu --model distil-large-v3 --compute-type int8
```
````

After

````markdown
`ingest` asks for a scan within a minute; `jobs cancel` is final for that
version of the recording until `jobs retry`; `followers revoke` releases the
follower's work at once.
````

add

````markdown
### Pool tokens

A join token expires and has a limited number of uses, which suits a machine a
person sets up. A pool whose followers come and go by themselves (Kubernetes
pods) registers with a **pool token** instead: it has no expiry and no limit on
uses, and stays valid until it is revoked.

```
uv run swarmscribe-admin pool-tokens create --name gpu-pods --pool gpu
uv run swarmscribe-admin pool-tokens list
uv run swarmscribe-admin pool-tokens revoke gpu-pods
uv run swarmscribe-admin pool-tokens revoke gpu-pods --revoke-followers
```

The token is shown once, by `create`; a follower is given it exactly like a
join token. Names are never reused. `revoke` stops further registrations and
leaves the followers it registered at work; `--revoke-followers` revokes them
too, which is what a leaked token needs. Pool tokens are created, listed and
revoked only by an administrator signed in as a person: a fleet console is
refused. A follower that registers with a pool token takes over the row of a
follower of the same token that has gone, so pods that start and stop do not
grow the list of followers.

### Settings profiles

Each device class (`cuda`, `cpu`) transcribes with one profile: a model, a
compute type and a temperature ladder.

```
uv run swarmscribe-admin profiles list
uv run swarmscribe-admin profiles set cuda --model large-v3 --compute-type float16
uv run swarmscribe-admin profiles set cpu --model distil-large-v3 --compute-type int8 --temperatures 0,0.2,0.4
```

A change applies to every job claimed after it; a job already leased keeps what
it was given. A model is a name (`large-v3`, or `owner/name` for a Hugging Face
repository), never a path. `profiles list` and `profiles set` are for people
signed in to the leader; a fleet console is refused.

### What a follower is told

- A draining follower hears it on every claim: the `204` carries the header
  `X-SwarmScribe-Directive: drain`, so a follower with nothing to do can wind
  down too. Deregistering does not end a drain.
- `POST /v1/jobs/{id}/links` gives the follower that holds a job's lease fresh
  download and upload links, for a job that outlasts the two hours its upload
  links last. At most once per `SWARMSCRIBE_LINKS_REFRESH_MIN_SECONDS` (60) for
  a lease; sooner is answered `429` with `Retry-After`. The lease is not
  extended: that is the heartbeat's.
````

- [ ] **Step 7: Run everything**

Run: `uv run ruff check . && uv run pytest -q`
Expected: `All checks passed!`; every package's tests PASS (protocol, engine, leader, console).

- [ ] **Step 8: Commit**

```bash
git add packages/leader/tests/leader_testkit.py packages/leader/tests/test_testkit.py packages/leader/tests/conftest.py README.md
git commit -m "Leader test kit for other packages' tests; README: pool tokens, profiles, drain, fresh links"
```

---

## Self-review

- **Spec coverage.** Follower spec 12.1 → Tasks 3, 4, 5. 12.2 → Tasks 2, 3, 6. 12.3 → Tasks 2, 7. 12.4 → Task 8. 12.5 → Task 4. 12.6 → Task 9 (README; ruling 10). 12.7 → Global Constraints, Tasks 5 and 8 (the console's check, run and unchanged). 12.8 → Task 1. 12.9 → Task 9. Section 14 item 3 (the stored pool) → Task 4.
- **Not in this plan, on purpose.** The follower itself (F1). A command that ends a drain. Pool tokens and profiles in the fleet console (ruling 4). Deleting old follower rows (ruling 6).
- **Names used across tasks.** `PoolToken`, `Follower.pool_token_id`, `Job.links_issued_at` (Task 3 → 4, 5, 6). `create_pool_token`, `revoke_pool_token` (Task 4 → 5). `JobLinks`, `LinksRequest`, `DIRECTIVE_HEADER` (Task 2 → 6, 7). `for_people_with_the_role` (Task 5 → 8). `leader_testkit` (Task 9 → F1).
