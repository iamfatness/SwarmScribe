# Per-Channel Transcription Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A stereo recording with one speaker per channel can be transcribed in split mode, giving one transcript in time order with every line labelled by its channel, while mono behaviour and mono output stay exactly as they are.

**Architecture:** The protocol gains `JobSettings.channel_mode`/`channel_labels`, `Segment.channel` and `SegmentsDocument.channel_labels`. The engine's `Transcriber` probes the channel count with PyAV (only for `stereo_split`/`auto`), decodes the two channels with faster-whisper's `decode_audio(split_stereo=True)`, runs the one loaded model over each channel with the same hotwords and fixed settings, merges the segments by `(start, channel)` and applies corrections once over the merged list. Writers prefix `<label>: ` in txt and srt only for split transcripts and write the channel fields only when present, so mono bytes do not change. The CLI gets `--channels` and `--labels`; the leader stores a channel mode and labels per storage location, puts them in every claim, and Plan A2's `locations add` sets them.

**Tech Stack:** Python 3.11+, uv workspace, Pydantic v2, faster-whisper 1.x with PyAV 18, pytest (+ pytest-asyncio for the leader), SQLAlchemy 2.0 async + Alembic on Postgres, FastAPI, httpx, ruff.

**Spec:** `docs/superpowers/specs/2026-10-03-channel-split-design.md` (approved; the authority). Engine conventions: `docs/superpowers/specs/2026-10-02-swarmscribe-architecture-design.md` sections 5.2 and 16.

**Dependency on Plan A2 (`docs/superpowers/plans/2026-10-03-leader-admin.md`):** Tasks 1–5 (protocol, engine, CLI, smoke) do not depend on A2 and can be done now. **Tasks 6 and 7 must be executed only after Plan A2 is merged**: Task 6 adds migration `0004` with `down_revision = "0003"` (A2's `0003_admin.py`), and Task 7 extends A2's `ingest/locations.py::add_location`, `api/admin_models.py::LocationIn`/`LocationOut`, `reports.py::location_view` and `admin_cli/main.py` (`build_parser`, `dispatch`). Before starting Task 6, confirm `packages/leader/src/swarmscribe_leader/db/migrations/versions/0003_admin.py` and `packages/leader/src/swarmscribe_leader/admin_cli/main.py` exist on the branch.

## Global Constraints

- Channel modes, verbatim: `mono` (default; channels are mixed, as today), `stereo_split` (the file must be stereo; left and right are transcribed separately; a mono file is an `undecodable`-class failure with a clear message), `auto` (split if the file has two channels, otherwise mono).
- `channel_labels` default `["Left", "Right"]`; "Labels are 1–40 characters, exactly two of them, and only used when splitting."
- `PROTOCOL_VERSION` stays `1` (not shipped); `schema_version` stays `1`; the schema snapshot is regenerated, never hand-edited.
- Protocol fields, verbatim: `JobSettings.channel_mode: Literal["mono", "stereo_split", "auto"] = "mono"`; `JobSettings.channel_labels: tuple[str, str] = ("Left", "Right")`; `Segment.channel: int | None = None` (`0` left, `1` right, `None` for mono); `SegmentsDocument.channel_labels: list[str] | None`.
- "One model load serves both channels." Each channel uses the same loaded model, the fixed settings, the same hotwords and the same temperature ladder.
- Segments are merged into one list ordered by `(start, channel)`; each carries its `channel`. `corrections_applied` sums counts across channels. `duration` is the file's duration.
- Decode failures and non-stereo input in `stereo_split` mode raise `UndecodableAudioError`.
- txt: one line per segment, prefixed `<label>: ` in split mode. srt: one cue per segment, cue text prefixed `<label>: ` in split mode. "Mono outputs are byte-for-byte unchanged from today."
- CLI, verbatim: `swarmscribe-engine FILE --out DIR [--channels mono|stereo-split|auto] [--labels "Agent,Customer"]`. "`--labels` without a split mode is an error."
- Out of scope: diarization of a single channel; more than two channels; live transcription.
- Dependency rule: `protocol` imports nothing internal; `engine` imports nothing internal at runtime (engine *tests* may import `swarmscribe_protocol`); `leader` imports `swarmscribe_protocol` only.
- Fixed transcription behaviour is unchanged: `language="en"`, `condition_on_previous_text=False`, VAD on, word timestamps on, ladder capped at `0.4`; hotwords only, no `initial_prompt`.
- Nothing in code, defaults, examples or test data may be specific to one kind of content or organisation (use `recording.mp3`, `call.wav`, `Agent`/`Customer`, `Ashford`, `Jason`).
- No audio or transcript text in logs or CLI output.
- All text outputs UTF-8 with `\n` line endings; `segments.json` is written last.
- On this Windows machine `uv` is not on PATH: run `python -m uv …` wherever a step says `uv …`. The leader tests need Postgres (`pgserver` starts one automatically).
- If `ruff check` flags import order or line length in code copied from this plan, run `uv run ruff check --fix --select I` or wrap the line without changing behaviour.
- Commits end with a blank line and `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Do not push.

## Review Focus

Inputs the spec implies but does not spell out, most likely first. Each has a test in the task named.

1. **Anything made before this change** — a storage location row created before migration `0004`, a claim JSON without channel fields, a mono transcript: they behave as mono with `Left`/`Right`, and mono outputs are byte-for-byte what they were. — Task 1 (old `JobSettings` JSON), Task 2 (pinned mono bytes), Task 6 (existing rows).
2. **One follower alternating mono and split jobs** (or jobs with different temperature ladders) on the same model: the model is loaded once, not once per job. — Task 3.
3. **A file with more than two channels or no audio stream** (a 5.1 video soundtrack, a video with no sound): `stereo_split` refuses it naming what it found; `auto` mixes it to mono as today. — Task 3.
4. **Labels as people type them** (`--labels "Agent, Customer"`, a pasted label with a line break, the same name twice): spaces around the comma are trimmed; a line break, control character, blank or duplicate label is refused, because it would break the one-line-per-segment txt and srt. — Task 1, Task 2, Task 4, Task 7.
5. **A split recording in which nobody speaks** (both channels silent): empty txt and srt and a `segments.json` with `segments: []` that still carries `channel_labels` — the same no-speech shape Plan A2's submit accepts. — Task 2, Task 3.

## Decisions this plan makes (the spec does not dictate them)

- **Migration number.** The spec says `0003`; Plan A2 already owns `0003`, so this plan's migration is `0004` (`down_revision = "0003"`).
- **Where the split is recorded in `segments.json`.** The `settings` object never gains `channel_mode`/`channel_labels`; the document-level `channel_labels` (present only when the transcript was split) and each segment's `channel` are the record. This is what keeps mono, and `auto` on a mono file, byte-identical. `channel` is written after `words`; `channel_labels` sits just before `segments`.
- **Channel probing.** The channel count is read with PyAV only in `stereo_split` and `auto`; `mono` never probes. `stereo_split` needs the probe too, because `decode_audio(split_stereo=True)` silently duplicates a mono file into two identical channels.
- **Exactly two.** `stereo_split` refuses 0, 1 or 3+ channels; `auto` splits only on exactly 2 and mixes anything else to mono.
- **Corrections once over the merged list.** Matches never span a segment, so this equals applying them per channel, and counts come out summed in file order.
- **Ties.** Equal start times put channel 0 before channel 1; segments of one channel keep their order (stable sort).
- **Duration** is taken from the left channel's pass (both channels are the same length).
- **No reload when only per-job settings change.** The shared model is cached on `(model, compute_type, device)` only; `Transcriber.transcribe` takes an optional keyword `settings=` for the channel handling and ladder of that call, refusing settings that need a different model. This also stops a different ladder reloading the model.
- **Label rules** (protocol and engine alike): 1–40 characters, no leading or trailing whitespace, no control/format characters or line/paragraph separators, and the two labels must differ ignoring case.
- **`Segment.channel`** is limited to `0..1` in the protocol; `SegmentsDocument.channel_labels` stays the spec's plain `list[str] | None`.
- **Shared validation.** The protocol exports `ChannelMode`, `ChannelLabels` and `DEFAULT_CHANNEL_LABELS`; the leader's `LocationIn` reuses them.
- **CLIs.** `--labels` is split on one comma and each name stripped; not exactly two names is an argparse usage error (exit 2). In the engine CLI, `--labels` with `--channels mono` (the default) is a usage error (exit 2); a label that breaks the rules is `error: …`, exit 2. In `swarmscribe-admin`, `--labels` with mono is `error: …`, exit 1 (its convention for refused commands), and the API refuses `channel_labels` sent with `channel_mode: mono` (`422`).
- **Database.** Columns get server defaults (`'mono'`, `["Left", "Right"]`) so existing rows and A2's Compose seeding SQL keep working; validity is enforced at the API, not by a check constraint.
- **Claims** use the channel settings of the recording's own location, not of its output location.
- **Admin views.** `LocationOut` gains `channel_mode` and `channel_labels`; `locations list` shows a `channel_mode` column.
- **Memory.** A split file is decoded whole into two float32 arrays (about 230 MB per channel-hour), the same order as today's mono decode; no streaming.
- **Real-decoder tests in the normal suite.** PyAV and `decode_audio` run on generated WAVs with a fake model that "hears" non-silent audio, so channel assignment is tested without downloading a model; only the `tiny.en` test is marked `smoke`.

## File Structure

```
packages/protocol/src/swarmscribe_protocol/
  segments.py            MOD  ChannelMode, ChannelLabels, DEFAULT_CHANNEL_LABELS; channel fields
  __init__.py            MOD  re-exports
packages/protocol/tests/
  test_segments.py       MOD  channel fields and label validation
  schema_v1.json         REGENERATED
packages/engine/src/swarmscribe_engine/
  types.py               MOD  channel settings + validation, Segment.channel, Transcript.channel_labels
  writers.py             MOD  label prefixes; channel fields in segments.json only when split
  transcriber.py         MOD  probe, split decode, two passes, merge; model cache keyed on the model
  cli.py                 MOD  --channels, --labels
  __init__.py            MOD  re-exports
packages/engine/tests/
  conftest.py            MOD  make_wav fixture (Task 3)
  test_settings.py       NEW  channel settings validation (Task 2)
  test_writers.py        MOD  pinned mono bytes; split outputs (Task 2)
  test_transcriber.py    MOD  fake-model and real-decoder split tests (Task 3)
  test_cli.py            MOD  (Task 4)
  test_smoke.py          MOD  real tiny.en on a stereo file (Task 5)
README.md                MOD  engine flags (Task 4); admin flags (Task 7)
packages/leader/src/swarmscribe_leader/                       (after Plan A2 is merged)
  db/models.py           MOD  StorageLocation.channel_mode, channel_labels (Task 6)
  db/migrations/versions/0004_channel_split.py  NEW  (Task 6)
  jobs/claims.py         MOD  build_claim carries the location's channel settings (Task 6)
  ingest/locations.py    MOD  add_location channel_mode, channel_labels (Task 7)
  api/admin_models.py    MOD  LocationIn/LocationOut channel fields (Task 7)
  reports.py             MOD  location_view channel fields (Task 7)
  admin_cli/main.py      MOD  locations add --channels --labels; list column (Task 7)
packages/leader/tests/
  test_migrations.py     MOD  head 0004; defaults for existing rows (Task 6)
  test_follower_api.py   MOD  claim carries channel settings (Task 6)
  test_admin_api.py      MOD  (Task 7)
  test_admin_cli.py      MOD  (Task 7)
```

---

### Task 1: Protocol — channel mode, labels and per-segment channel

**Files:**
- Modify: `packages/protocol/src/swarmscribe_protocol/segments.py` (whole file)
- Modify: `packages/protocol/src/swarmscribe_protocol/__init__.py`
- Test: `packages/protocol/tests/test_segments.py`
- Regenerate: `packages/protocol/tests/schema_v1.json`

**Interfaces:**
- Consumes: nothing new.
- Produces (all importable from `swarmscribe_protocol`): `ChannelMode = Literal["mono", "stereo_split", "auto"]`; `ChannelLabels = Annotated[tuple[str, str], AfterValidator(...)]`; `DEFAULT_CHANNEL_LABELS: tuple[str, str] = ("Left", "Right")`; `MAX_CHANNEL_LABEL_LENGTH = 40`; `JobSettings.channel_mode: ChannelMode = "mono"`; `JobSettings.channel_labels: ChannelLabels = ("Left", "Right")`; `Segment.channel: int | None = None` (0 or 1); `SegmentsDocument.channel_labels: list[str] | None = None`.

- [ ] **Step 1: Write the failing tests**

In `packages/protocol/tests/test_segments.py`, change the import line to:

```python
from swarmscribe_protocol import (
    DEFAULT_CHANNEL_LABELS,
    AppliedCorrection,
    JobSettings,
    Segment,
    SegmentsDocument,
    Word,
)
```

and append:

```python
def test_job_settings_default_to_mono_with_left_and_right_labels():
    settings = _settings()
    assert settings.channel_mode == "mono"
    assert settings.channel_labels == ("Left", "Right") == DEFAULT_CHANNEL_LABELS


@pytest.mark.parametrize("mode", ["mono", "stereo_split", "auto"])
def test_job_settings_accept_each_channel_mode(mode):
    assert _settings(channel_mode=mode).channel_mode == mode


@pytest.mark.parametrize("mode", ["stereo", "stereo-split", "split", ""])
def test_job_settings_reject_unknown_channel_modes(mode):
    with pytest.raises(ValidationError):
        _settings(channel_mode=mode)


@pytest.mark.parametrize(
    "labels",
    [
        ("Left",),
        ("Left", "Middle", "Right"),
        ("", "Right"),
        ("x" * 41, "Right"),
        (" Left", "Right"),
        ("Left ", "Right"),
        ("   ", "Right"),
        ("Left\nSide", "Right"),
        ("Left\tSide", "Right"),
        ("Left Side", "Right"),
        ("Same", "same"),
    ],
)
def test_job_settings_reject_bad_channel_labels(labels):
    with pytest.raises(ValidationError):
        _settings(channel_mode="stereo_split", channel_labels=labels)


def test_channel_labels_of_up_to_forty_characters_are_accepted():
    labels = ("x" * 40, "José Ashford")
    assert _settings(channel_mode="stereo_split", channel_labels=labels).channel_labels == labels


def test_job_settings_without_channel_fields_are_mono():
    settings = JobSettings.model_validate_json('{"model": "large-v3", "compute_type": "float16"}')
    assert (settings.channel_mode, settings.channel_labels) == ("mono", ("Left", "Right"))


def test_segment_channel_is_optional_and_left_or_right():
    assert Segment(start=0.0, end=1.0, text="Hello.", words=[]).channel is None
    assert Segment(start=0.0, end=1.0, text="Hello.", words=[], channel=1).channel == 1
    for bad in (-1, 2):
        with pytest.raises(ValidationError):
            Segment(start=0.0, end=1.0, text="Hello.", words=[], channel=bad)


def test_a_split_segments_document_round_trips_through_json():
    document = SegmentsDocument(
        schema_version=1,
        source_checksum="a" * 64,
        duration=4.0,
        device="cpu",
        engine_version="0.1.0",
        settings=_settings(channel_mode="stereo_split", channel_labels=("Agent", "Customer")),
        vocabulary_version=0,
        vocabulary_terms_used=[],
        corrections_applied=[],
        channel_labels=["Agent", "Customer"],
        segments=[
            Segment(start=0.0, end=1.0, text="Good morning.", words=[], channel=0),
            Segment(start=1.5, end=2.0, text="Hello.", words=[], channel=1),
        ],
    )
    assert SegmentsDocument.model_validate_json(document.model_dump_json()) == document


def test_a_mono_segments_document_has_no_channel_labels():
    document = SegmentsDocument(
        schema_version=1,
        source_checksum="a" * 64,
        duration=3.0,
        device="cpu",
        engine_version="0.1.0",
        settings=_settings(),
        vocabulary_version=0,
        vocabulary_terms_used=[],
        corrections_applied=[],
        segments=[],
    )
    assert document.channel_labels is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/protocol/tests/test_segments.py -v`
Expected: FAIL — `ImportError: cannot import name 'DEFAULT_CHANNEL_LABELS'`.

- [ ] **Step 3: Implement the protocol fields**

Replace `packages/protocol/src/swarmscribe_protocol/segments.py` with:

```python
import unicodedata
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, field_validator

from .base import Sha256, WireModel
from .vocabulary import AppliedCorrection

Device = Literal["cuda", "cpu"]
ChannelMode = Literal["mono", "stereo_split", "auto"]

MAX_TEMPERATURE = 0.4
MAX_CHANNEL_LABEL_LENGTH = 40
DEFAULT_CHANNEL_LABELS: tuple[str, str] = ("Left", "Right")


def _unprintable(ch: str) -> bool:
    category = unicodedata.category(ch)
    return category.startswith("C") or category in ("Zl", "Zp")


def _check_channel_labels(labels: tuple[str, str]) -> tuple[str, str]:
    # A label prefixes transcript lines, so a line break in one would break txt and srt.
    for label in labels:
        if not 1 <= len(label) <= MAX_CHANNEL_LABEL_LENGTH:
            raise ValueError(f"channel labels must be 1 to {MAX_CHANNEL_LABEL_LENGTH} characters")
        if label != label.strip():
            raise ValueError("channel labels must not start or end with a space")
        if any(_unprintable(ch) for ch in label):
            raise ValueError("channel labels must not contain control characters or line breaks")
    if labels[0].casefold() == labels[1].casefold():
        raise ValueError("the two channel labels must differ")
    return labels


ChannelLabels = Annotated[tuple[str, str], AfterValidator(_check_channel_labels)]
"""Names of the left and right channel; they prefix transcript lines when splitting."""


class JobSettings(WireModel):
    """How a follower must transcribe. The Literal fields are fixed behaviour."""

    model: str
    compute_type: str
    language: Literal["en"] = "en"
    condition_on_previous_text: Literal[False] = False
    temperatures: tuple[float, ...] = (0.0, 0.2, 0.4)
    vad_filter: Literal[True] = True
    word_timestamps: Literal[True] = True
    channel_mode: ChannelMode = "mono"
    channel_labels: ChannelLabels = DEFAULT_CHANNEL_LABELS

    @field_validator("temperatures")
    @classmethod
    def _ladder_is_clamped(cls, value: tuple[float, ...]) -> tuple[float, ...]:
        if not value:
            raise ValueError("temperatures must not be empty")
        if any(not (0.0 <= t <= MAX_TEMPERATURE) for t in value):
            raise ValueError(f"temperatures must be between 0.0 and {MAX_TEMPERATURE}")
        return value


class Word(WireModel):
    start: float
    end: float
    word: str
    probability: float = Field(ge=0.0, le=1.0)
    original: str | None = None


class Segment(WireModel):
    start: float
    end: float
    text: str
    words: list[Word]
    channel: int | None = Field(default=None, ge=0, le=1)
    """0 left, 1 right; None when the recording was not split."""


class SegmentsDocument(WireModel):
    """The contents of <name>.segments.json."""

    schema_version: Literal[1]
    source_checksum: Sha256
    duration: float = Field(ge=0.0)
    device: Device
    engine_version: str
    settings: JobSettings
    vocabulary_version: int = Field(ge=0)
    vocabulary_terms_used: list[str]
    corrections_applied: list[AppliedCorrection]
    channel_labels: list[str] | None = None
    """The labels used when the transcript was split; None otherwise."""
    segments: list[Segment]
```

In `packages/protocol/src/swarmscribe_protocol/__init__.py`, replace the `.segments` import line with:

```python
from .segments import (
    DEFAULT_CHANNEL_LABELS,
    MAX_CHANNEL_LABEL_LENGTH,
    MAX_TEMPERATURE,
    ChannelLabels,
    ChannelMode,
    Device,
    JobSettings,
    Segment,
    SegmentsDocument,
    Word,
)
```

and replace `__all__` with:

```python
__all__ = [
    "DEFAULT_CHANNEL_LABELS",
    "MAX_CHANNEL_LABEL_LENGTH",
    "MAX_TEMPERATURE",
    "PROTOCOL_VERSION",
    "AppliedCorrection",
    "Capabilities",
    "ChannelLabels",
    "ChannelMode",
    "ClaimResponse",
    "Correction",
    "Device",
    "Directive",
    "ErrorBody",
    "FailRequest",
    "FailureCode",
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
    "Sha256",
    "SubmitRequest",
    "SubmitResponse",
    "UploadUrls",
    "Vocabulary",
    "Word",
]
```

- [ ] **Step 4: Run the tests; the snapshot must now fail**

Run: `uv run pytest packages/protocol -v`
Expected: every `test_segments.py` test PASSES; `test_wire_schema_matches_the_committed_snapshot` FAILS (the wire format changed, as intended; `PROTOCOL_VERSION` stays `1` because the protocol has not shipped).

- [ ] **Step 5: Regenerate the snapshot**

Run: `uv run python -m swarmscribe_protocol.schema packages/protocol/tests/schema_v1.json`
Then: `git diff --stat packages/protocol/tests/schema_v1.json` — Expected: only `schema_v1.json` changed, with `channel_mode`, `channel_labels` and `channel` added.

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest packages/protocol packages/engine packages/leader -v`
Expected: PASS (nothing else constructs these models with positional arguments; the new fields all have defaults).

Run: `uv run ruff check packages/protocol`
Expected: no findings.

- [ ] **Step 7: Commit**

```bash
git add packages/protocol/src/swarmscribe_protocol/segments.py packages/protocol/src/swarmscribe_protocol/__init__.py packages/protocol/tests/test_segments.py packages/protocol/tests/schema_v1.json
git commit -m "Protocol: channel mode and labels in JobSettings, per-segment channel

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Engine types and writers — channel settings, labelled txt/srt, mono bytes pinned

**Files:**
- Modify: `packages/engine/src/swarmscribe_engine/types.py`
- Modify: `packages/engine/src/swarmscribe_engine/writers.py` (whole file)
- Modify: `packages/engine/src/swarmscribe_engine/__init__.py`
- Create: `packages/engine/tests/test_settings.py`
- Test: `packages/engine/tests/test_writers.py`

**Interfaces:**
- Consumes: `SegmentsDocument.channel_labels`, `Segment.channel` (Task 1) — tests only.
- Produces (from `swarmscribe_engine.types`, also re-exported by `swarmscribe_engine` where noted):
  - `ChannelMode = Literal["mono", "stereo_split", "auto"]` (exported); `CHANNEL_MODES: tuple[str, ...]`; `DEFAULT_CHANNEL_LABELS: tuple[str, str] = ("Left", "Right")` (exported); `MAX_CHANNEL_LABEL_LENGTH = 40`; `def check_channel_labels(labels: Sequence[str]) -> tuple[str, str]` (raises `ValueError`).
  - `TranscribeSettings(model, compute_type, device, temperatures=DEFAULT_TEMPERATURES, channel_mode: ChannelMode = "mono", channel_labels: tuple[str, ...] = DEFAULT_CHANNEL_LABELS)` — still frozen and hashable; labels normalised to a tuple.
  - `Segment(..., channel: int | None = None)`; `Transcript(..., channel_labels: tuple[str, ...] | None = None)` (last fields, defaulted).
  - `writers.render_txt` / `render_srt` prefix `"<label>: "` when `transcript.channel_labels` and `segment.channel` are both set; `render_segments_json` writes `"channel"` (after `"words"`) and `"channel_labels"` (before `"segments"`) only when set.

- [ ] **Step 1: Pin today's mono output byte for byte (this test must PASS before any change)**

Append to `packages/engine/tests/test_writers.py`:

```python
PINNED_SEGMENTS_JSON = """{
  "schema_version": 1,
  "source_checksum": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "duration": 3.25,
  "device": "cuda",
  "engine_version": "@ENGINE_VERSION@",
  "settings": {
    "model": "large-v3",
    "compute_type": "float16",
    "language": "en",
    "condition_on_previous_text": false,
    "vad_filter": true,
    "word_timestamps": true,
    "temperatures": [
      0.0,
      0.2,
      0.4
    ]
  },
  "vocabulary_version": 5,
  "vocabulary_terms_used": [
    "Ashford"
  ],
  "corrections_applied": [
    {
      "heard": "jay son",
      "replacement": "Jason",
      "count": 1
    }
  ],
  "segments": [
    {
      "start": 0.0,
      "end": 1.5,
      "text": "Welcome to Ashford.",
      "words": [
        {
          "start": 0.0,
          "end": 0.4,
          "word": " Welcome",
          "probability": 0.98
        },
        {
          "start": 0.4,
          "end": 0.6,
          "word": " to",
          "probability": 0.99
        },
        {
          "start": 0.6,
          "end": 1.5,
          "word": " Ashford.",
          "probability": 0.71
        }
      ]
    },
    {
      "start": 2.0,
      "end": 3.25,
      "text": "Thanks Jason.",
      "words": [
        {
          "start": 2.0,
          "end": 2.5,
          "word": " Thanks",
          "probability": 0.9
        },
        {
          "start": 2.5,
          "end": 3.25,
          "word": " Jason.",
          "probability": 0.3,
          "original": " jay son."
        }
      ]
    }
  ]
}
"""


def test_mono_outputs_are_pinned_byte_for_byte(make_transcript, tmp_path):
    # Written before per-channel transcription existed; splitting must not change one byte.
    segments = (
        Segment(
            start=0.0,
            end=1.5,
            text="Welcome to Ashford.",
            words=(
                Word(start=0.0, end=0.4, word=" Welcome", probability=0.98),
                Word(start=0.4, end=0.6, word=" to", probability=0.99),
                Word(start=0.6, end=1.5, word=" Ashford.", probability=0.71),
            ),
        ),
        Segment(
            start=2.0,
            end=3.25,
            text="Thanks Jason.",
            words=(
                Word(start=2.0, end=2.5, word=" Thanks", probability=0.9),
                Word(start=2.5, end=3.25, word=" Jason.", probability=0.3, original=" jay son."),
            ),
        ),
    )
    transcript = make_transcript(
        segments=segments,
        vocabulary_version=5,
        corrections_applied=(AppliedCorrection(heard="jay son", replacement="Jason", count=1),),
    )
    files = write_outputs(transcript, tmp_path)
    assert files.txt.read_bytes() == b"Welcome to Ashford.\nThanks Jason.\n"
    assert files.srt.read_bytes() == (
        b"1\n00:00:00,000 --> 00:00:01,500\nWelcome to Ashford.\n"
        b"\n"
        b"2\n00:00:02,000 --> 00:00:03,250\nThanks Jason.\n"
    )
    expected = PINNED_SEGMENTS_JSON.replace("@ENGINE_VERSION@", ENGINE_VERSION)
    assert files.segments_json.read_bytes() == expected.encode("utf-8")
```

(`AppliedCorrection`, `ENGINE_VERSION`, `Segment`, `Word` and `write_outputs` are already imported by this file.)

- [ ] **Step 2: Run it against today's code**

Run: `uv run pytest packages/engine/tests/test_writers.py::test_mono_outputs_are_pinned_byte_for_byte -v`
Expected: PASS. If it fails, the expected bytes were copied wrong — fix the test, never the writer. Commit it on its own so the pin predates the change:

```bash
git add packages/engine/tests/test_writers.py
git commit -m "Engine: pin mono txt, srt and segments.json byte for byte

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 3: Write the failing tests for channel settings and split outputs**

Create `packages/engine/tests/test_settings.py`:

```python
import pytest
from swarmscribe_engine import DEFAULT_CHANNEL_LABELS, TranscribeSettings


def settings(**overrides):
    return TranscribeSettings(model="large-v3", compute_type="float16", device="cuda", **overrides)


def test_channel_settings_default_to_mono_left_and_right():
    assert settings().channel_mode == "mono"
    assert settings().channel_labels == ("Left", "Right") == DEFAULT_CHANNEL_LABELS


@pytest.mark.parametrize("mode", ["mono", "stereo_split", "auto"])
def test_each_channel_mode_is_accepted(mode):
    assert settings(channel_mode=mode).channel_mode == mode


@pytest.mark.parametrize("mode", ["stereo", "stereo-split", ""])
def test_an_unknown_channel_mode_is_refused(mode):
    with pytest.raises(ValueError, match="channel_mode"):
        settings(channel_mode=mode)


@pytest.mark.parametrize(
    ("labels", "problem"),
    [
        (("Left",), "exactly two"),
        (("Left", "Middle", "Right"), "exactly two"),
        ("LR", "exactly two"),
        (("", "Right"), "1 to 40"),
        (("x" * 41, "Right"), "1 to 40"),
        ((" Left", "Right"), "space"),
        (("Left\nSide", "Right"), "control characters or line breaks"),
        (("Same", "same"), "differ"),
    ],
)
def test_bad_channel_labels_are_refused(labels, problem):
    with pytest.raises(ValueError, match=problem):
        settings(channel_mode="stereo_split", channel_labels=labels)


def test_labels_given_as_a_list_become_a_hashable_tuple():
    split = settings(channel_mode="auto", channel_labels=["Agent", "Customer"])
    assert split.channel_labels == ("Agent", "Customer")
    assert hash(split) == hash(settings(channel_mode="auto", channel_labels=("Agent", "Customer")))


def test_forty_character_labels_are_accepted():
    labels = ("x" * 40, "José")
    assert settings(channel_mode="stereo_split", channel_labels=labels).channel_labels == labels
```

Append to `packages/engine/tests/test_writers.py`:

```python
SPLIT_SETTINGS = TranscribeSettings(
    model="large-v3",
    compute_type="float16",
    device="cuda",
    channel_mode="stereo_split",
    channel_labels=("Agent", "Customer"),
)
SPLIT_SEGMENTS = (
    Segment(start=0.0, end=1.0, text="Good morning.", words=(), channel=0),
    Segment(
        start=0.5,
        end=1.5,
        text="Hello.",
        words=(Word(start=0.5, end=1.5, word=" Hello.", probability=0.9),),
        channel=1,
    ),
)


def split_transcript(make_transcript, **overrides):
    values = {
        "segments": SPLIT_SEGMENTS,
        "settings": SPLIT_SETTINGS,
        "channel_labels": ("Agent", "Customer"),
    }
    values.update(overrides)
    return make_transcript(**values)


def test_split_txt_prefixes_each_line_with_its_label(make_transcript):
    assert render_txt(split_transcript(make_transcript)) == "Agent: Good morning.\nCustomer: Hello.\n"


def test_split_srt_prefixes_each_cue_with_its_label(make_transcript):
    assert render_srt(split_transcript(make_transcript)) == (
        "1\n00:00:00,000 --> 00:00:01,000\nAgent: Good morning.\n"
        "\n"
        "2\n00:00:00,500 --> 00:00:01,500\nCustomer: Hello.\n"
    )


def test_split_segments_json_records_channels_and_labels(make_transcript, tmp_path):
    files = write_outputs(split_transcript(make_transcript), tmp_path)
    raw = json.loads(files.segments_json.read_text("utf-8"))
    assert raw["channel_labels"] == ["Agent", "Customer"]
    assert list(raw)[-2:] == ["channel_labels", "segments"]
    assert [segment["channel"] for segment in raw["segments"]] == [0, 1]
    assert list(raw["segments"][1]) == ["start", "end", "text", "words", "channel"]
    assert "channel_mode" not in raw["settings"]
    assert "channel_labels" not in raw["settings"]
    document = SegmentsDocument.model_validate_json(files.segments_json.read_text("utf-8"))
    assert document.channel_labels == ["Agent", "Customer"]
    assert [segment.channel for segment in document.segments] == [0, 1]


def test_split_segments_json_declares_every_field_it_writes(make_transcript, tmp_path):
    raw = write_outputs(split_transcript(make_transcript), tmp_path).segments_json.read_text(
        "utf-8"
    )
    document = SegmentsDocument.model_validate_json(raw)
    dumped = document.model_dump_json(
        exclude_none=True, exclude={"settings": {"channel_mode", "channel_labels"}}
    )
    assert json.loads(raw) == json.loads(dumped)


def test_a_split_recording_with_no_speech_keeps_its_labels(make_transcript, tmp_path):
    files = write_outputs(split_transcript(make_transcript, segments=()), tmp_path)
    assert files.txt.read_text("utf-8") == ""
    assert files.srt.read_text("utf-8") == ""
    document = SegmentsDocument.model_validate_json(files.segments_json.read_text("utf-8"))
    assert (document.segments, document.channel_labels) == ([], ["Agent", "Customer"])


def test_mono_segments_json_writes_no_channel_fields(make_transcript, tmp_path):
    raw = json.loads(write_outputs(make_transcript(), tmp_path).segments_json.read_text("utf-8"))
    assert "channel_labels" not in raw
    assert all("channel" not in segment for segment in raw["segments"])
```

Add `TranscribeSettings` to the `from swarmscribe_engine import …` line at the top of `test_writers.py`.

In the existing `test_segments_json_declares_every_field_it_writes`, replace its last line with:

```python
    dumped = document.model_dump_json(
        exclude_none=True, exclude={"settings": {"channel_mode", "channel_labels"}}
    )
    assert json.loads(raw) == json.loads(dumped)
```

(The protocol's `JobSettings` now has defaulted channel fields that `segments.json` deliberately does not write — see "Where the split is recorded" in Decisions.)

- [ ] **Step 4: Run the tests to verify they fail**

Run: `uv run pytest packages/engine/tests/test_settings.py packages/engine/tests/test_writers.py -v`
Expected: FAIL — `ImportError: cannot import name 'DEFAULT_CHANNEL_LABELS'` and `TypeError: ... unexpected keyword argument 'channel'`.

- [ ] **Step 5: Implement the types**

In `packages/engine/src/swarmscribe_engine/types.py`:

Replace the import block with:

```python
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal
```

After `MAX_TEMPERATURE = 0.4` add:

```python
ChannelMode = Literal["mono", "stereo_split", "auto"]
CHANNEL_MODES: tuple[str, ...] = ("mono", "stereo_split", "auto")
DEFAULT_CHANNEL_LABELS: tuple[str, str] = ("Left", "Right")
MAX_CHANNEL_LABEL_LENGTH = 40
```

After the `DeviceUnavailableError` class add:

```python
def _unprintable(ch: str) -> bool:
    category = unicodedata.category(ch)
    return category.startswith("C") or category in ("Zl", "Zp")


def check_channel_labels(labels: Sequence[str]) -> tuple[str, str]:
    """The two channel names, checked by the same rules as the protocol's JobSettings."""
    if isinstance(labels, str):
        raise ValueError("channel_labels must be exactly two names")
    labels = tuple(labels)
    if len(labels) != 2 or not all(isinstance(label, str) for label in labels):
        raise ValueError("channel_labels must be exactly two names")
    # A label prefixes transcript lines, so a line break in one would break txt and srt.
    for label in labels:
        if not 1 <= len(label) <= MAX_CHANNEL_LABEL_LENGTH:
            raise ValueError(f"channel labels must be 1 to {MAX_CHANNEL_LABEL_LENGTH} characters")
        if label != label.strip():
            raise ValueError("channel labels must not start or end with a space")
        if any(_unprintable(ch) for ch in label):
            raise ValueError("channel labels must not contain control characters or line breaks")
    if labels[0].casefold() == labels[1].casefold():
        raise ValueError("the two channel labels must differ")
    return labels
```

Replace the `TranscribeSettings` class with:

```python
@dataclass(frozen=True)
class TranscribeSettings:
    model: str
    compute_type: str
    device: Device
    temperatures: tuple[float, ...] = DEFAULT_TEMPERATURES
    channel_mode: ChannelMode = "mono"
    channel_labels: tuple[str, ...] = DEFAULT_CHANNEL_LABELS

    def __post_init__(self) -> None:
        object.__setattr__(self, "temperatures", tuple(self.temperatures))
        if not self.temperatures:
            raise ValueError("temperatures must not be empty")
        if any(not (0.0 <= t <= MAX_TEMPERATURE) for t in self.temperatures):
            raise ValueError(f"temperatures must be between 0.0 and {MAX_TEMPERATURE}")
        if self.channel_mode not in CHANNEL_MODES:
            raise ValueError(f"channel_mode must be one of: {', '.join(CHANNEL_MODES)}")
        object.__setattr__(self, "channel_labels", check_channel_labels(self.channel_labels))
```

Replace the `Segment` class with:

```python
@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str
    words: tuple[Word, ...]
    channel: int | None = None  # 0 left, 1 right; None when the recording was not split
```

Replace the `Transcript` class with:

```python
@dataclass(frozen=True)
class Transcript:
    source_name: str
    source_checksum: str
    duration: float
    settings: TranscribeSettings
    vocabulary_version: int
    vocabulary_terms_used: tuple[str, ...]
    corrections_applied: tuple[AppliedCorrection, ...]
    segments: tuple[Segment, ...]
    channel_labels: tuple[str, ...] | None = None  # set only when the recording was split
```

In `packages/engine/src/swarmscribe_engine/__init__.py`, add `DEFAULT_CHANNEL_LABELS` (before `DEFAULT_TEMPERATURES`) and `ChannelMode` (after `AppliedCorrection`) to the `from .types import (...)` list, and add `"DEFAULT_CHANNEL_LABELS"` (before `"DEFAULT_TEMPERATURES"`) and `"ChannelMode"` (after `"AppliedCorrection"`) to `__all__`.

- [ ] **Step 6: Implement the writers**

Replace `packages/engine/src/swarmscribe_engine/writers.py` with:

```python
import json
import os
from pathlib import Path

from .types import FIXED_SETTINGS, OutputFiles, Segment, Transcript, Word
from .version import ENGINE_VERSION

SCHEMA_VERSION = 1


def format_srt_time(seconds: float) -> str:
    total_ms = max(0, round(seconds * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _label(transcript: Transcript, segment: Segment) -> str:
    """'<label>: ' for a segment of a split transcript; nothing for mono."""
    if transcript.channel_labels is None or segment.channel is None:
        return ""
    return f"{transcript.channel_labels[segment.channel]}: "


def render_txt(transcript: Transcript) -> str:
    return "".join(
        f"{_label(transcript, segment)}{segment.text}\n" for segment in transcript.segments
    )


def render_srt(transcript: Transcript) -> str:
    blocks = [
        f"{index}\n"
        f"{format_srt_time(segment.start)} --> {format_srt_time(segment.end)}\n"
        f"{_label(transcript, segment)}{segment.text}\n"
        for index, segment in enumerate(transcript.segments, start=1)
    ]
    return "\n".join(blocks)


def _word_json(word: Word) -> dict:
    data = {
        "start": word.start,
        "end": word.end,
        "word": word.word,
        "probability": word.probability,
    }
    if word.original is not None:
        data["original"] = word.original
    return data


def _segment_json(segment: Segment) -> dict:
    data = {
        "start": segment.start,
        "end": segment.end,
        "text": segment.text,
        "words": [_word_json(word) for word in segment.words],
    }
    if segment.channel is not None:
        data["channel"] = segment.channel
    return data


def render_segments_json(transcript: Transcript) -> str:
    settings = transcript.settings
    # Channel settings are deliberately not written here: mono output stays exactly as it was,
    # and channel_labels below records whether the transcript was split.
    document = {
        "schema_version": SCHEMA_VERSION,
        "source_checksum": transcript.source_checksum,
        "duration": transcript.duration,
        "device": settings.device,
        "engine_version": ENGINE_VERSION,
        "settings": {
            "model": settings.model,
            "compute_type": settings.compute_type,
            **FIXED_SETTINGS,
            "temperatures": list(settings.temperatures),
        },
        "vocabulary_version": transcript.vocabulary_version,
        "vocabulary_terms_used": list(transcript.vocabulary_terms_used),
        "corrections_applied": [
            {"heard": applied.heard, "replacement": applied.replacement, "count": applied.count}
            for applied in transcript.corrections_applied
        ],
    }
    if transcript.channel_labels is not None:
        document["channel_labels"] = list(transcript.channel_labels)
    document["segments"] = [_segment_json(segment) for segment in transcript.segments]
    return json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


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
    txt, srt, segments_json = (
        render_txt(transcript),
        render_srt(transcript),
        render_segments_json(transcript),
    )
    _atomic_write(files.txt, txt)
    _atomic_write(files.srt, srt)
    _atomic_write(files.segments_json, segments_json)
    return files
```

- [ ] **Step 7: Run the engine suite**

Run: `uv run pytest packages/engine -v`
Expected: PASS — including `test_mono_outputs_are_pinned_byte_for_byte` (unchanged bytes) and every existing writer, transcriber and CLI test.

Run: `uv run ruff check packages/engine`
Expected: no findings.

- [ ] **Step 8: Commit**

```bash
git add packages/engine/src/swarmscribe_engine/types.py packages/engine/src/swarmscribe_engine/writers.py packages/engine/src/swarmscribe_engine/__init__.py packages/engine/tests/test_settings.py packages/engine/tests/test_writers.py
git commit -m "Engine: channel settings, labelled txt and srt, channel fields in segments.json

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Engine transcriber — split decoding, two passes, one model

**Files:**
- Modify: `packages/engine/src/swarmscribe_engine/transcriber.py` (whole file)
- Modify: `packages/engine/tests/conftest.py` (`make_wav` fixture)
- Test: `packages/engine/tests/test_transcriber.py`

**Interfaces:**
- Consumes: `TranscribeSettings.channel_mode/channel_labels`, `Segment.channel`, `Transcript.channel_labels` (Task 2); `writers.render_txt`, `write_outputs` (Task 2).
- Produces:
  - `transcriber.SAMPLE_RATE = 16000`; `ChannelCounter = Callable[[Path], int]`; `StereoDecoder = Callable[[Path], tuple[Any, Any]]`.
  - `Transcriber(settings, *, model_factory=_default_model_factory, decode_errors=None, channel_count: ChannelCounter = _default_channel_count, decode_stereo: StereoDecoder = _default_decode_stereo)`.
  - `Transcriber.transcribe(path: Path, vocabulary: Vocabulary = EMPTY_VOCABULARY, *, settings: TranscribeSettings | None = None) -> Transcript` — `settings` may differ from the constructor's only in `temperatures`, `channel_mode`, `channel_labels`; otherwise `ValueError("... different model ...")`.
  - Module `transcribe(path, settings, vocabulary=EMPTY_VOCABULARY) -> Transcript` reuses one `Transcriber` per `(model, compute_type, device)`.
  - Test fixture `make_wav(name: str, channels: list[float | None], seconds: float = 1.0, rate: int = 16000) -> Path` (16-bit PCM; one tone frequency in Hz or `None` for silence per channel).

- [ ] **Step 1: Add the WAV fixture**

In `packages/engine/tests/conftest.py`, add at the top:

```python
import math
import struct
import wave
```

and append:

```python
@pytest.fixture
def make_wav(tmp_path):
    """Writes a 16-bit WAV with one entry per channel: a tone frequency in Hz, or None for
    silence."""

    def _make(name, channels, seconds=1.0, rate=16000):
        frames = bytearray()
        for i in range(int(rate * seconds)):
            for frequency in channels:
                if frequency is None:
                    value = 0
                else:
                    value = int(8000 * math.sin(2 * math.pi * frequency * i / rate))
                frames += struct.pack("<h", value)
        path = tmp_path / name
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(len(channels))
            handle.setsampwidth(2)
            handle.setframerate(rate)
            handle.writeframes(bytes(frames))
        return path

    return _make
```

- [ ] **Step 2: Write the failing tests**

In `packages/engine/tests/test_transcriber.py`, replace the import block with:

```python
import hashlib
from dataclasses import replace
from types import SimpleNamespace

import pytest
import swarmscribe_engine.transcriber as engine_transcriber
from swarmscribe_engine import (
    AppliedCorrection,
    Correction,
    Transcriber,
    TranscribeSettings,
    UndecodableAudioError,
    Vocabulary,
    write_outputs,
)
from swarmscribe_engine.transcriber import sha256_file
from swarmscribe_engine.writers import render_txt
```

and append:

```python
# --- per-channel transcription -------------------------------------------------------

LEFT, RIGHT = object(), object()  # stand-ins for the two decoded channel arrays

FIXED_KWARGS = {
    "language": "en",
    "condition_on_previous_text": False,
    "temperature": [0.0, 0.2, 0.4],
    "vad_filter": True,
    "word_timestamps": True,
}


class ChannelModel(FakeModel):
    """Answers each audio input (a channel stand-in or a path string) with its own segments."""

    def __init__(self, by_audio, duration=0.0, **kwargs):
        super().__init__(duration=duration, **kwargs)
        self.by_audio = by_audio

    def transcribe(self, audio, **kwargs):
        self.calls.append((audio, kwargs))
        if self.error is not None:
            raise self.error
        return iter(self.by_audio.get(audio, ())), SimpleNamespace(duration=self.duration)


def seg(start, end, text):
    return raw_segment(start, end, text, [raw_word(start, end, text, 0.9)])


def split_transcriber(
    model,
    *,
    channels=2,
    mode="stereo_split",
    labels=("Left", "Right"),
    decode=None,
    probes=None,
):
    """A Transcriber whose channel probe answers `channels` (or raises it, if it is an
    exception) and whose stereo decoder returns (LEFT, RIGHT) unless `decode` is given."""
    settings = replace(SETTINGS, channel_mode=mode, channel_labels=labels)

    def count(path):
        if probes is not None:
            probes.append(path)
        if isinstance(channels, BaseException):
            raise channels
        return channels

    return Transcriber(
        settings,
        model_factory=lambda _: model,
        decode_errors=(DecodeError,),
        channel_count=count,
        decode_stereo=decode or (lambda path: (LEFT, RIGHT)),
    )


def test_split_transcribes_each_channel_and_merges_them_by_start_time(audio):
    model = ChannelModel(
        {
            LEFT: [seg(0.0, 1.0, " Good morning."), seg(4.0, 5.0, " Fine, thanks.")],
            RIGHT: [seg(2.0, 3.0, " Hello.")],
        },
        duration=5.0,
    )
    transcript = split_transcriber(model).transcribe(audio)
    assert [(s.start, s.channel, s.text) for s in transcript.segments] == [
        (0.0, 0, "Good morning."),
        (2.0, 1, "Hello."),
        (4.0, 0, "Fine, thanks."),
    ]
    assert transcript.channel_labels == ("Left", "Right")
    assert transcript.settings.channel_mode == "stereo_split"
    assert transcript.duration == 5.0
    assert [call[0] for call in model.calls] == [LEFT, RIGHT]


def test_a_tie_on_start_time_puts_the_left_channel_first(audio):
    model = ChannelModel(
        {
            LEFT: [seg(1.0, 2.0, " Yes.")],
            RIGHT: [seg(0.5, 1.0, " So."), seg(1.0, 1.5, " No.")],
        }
    )
    transcript = split_transcriber(model).transcribe(audio)
    assert [(s.start, s.channel, s.text) for s in transcript.segments] == [
        (0.5, 1, "So."),
        (1.0, 0, "Yes."),
        (1.0, 1, "No."),
    ]


def test_both_passes_get_the_same_hotwords_and_fixed_settings(audio):
    model = ChannelModel({})
    split_transcriber(model).transcribe(audio, Vocabulary(version=2, terms=("Ashford", "José")))
    expected = {**FIXED_KWARGS, "hotwords": "Ashford, José"}
    assert model.calls == [(LEFT, expected), (RIGHT, expected)]


def test_split_terms_are_budgeted_once_with_the_models_tokenizer(audio):
    model = ChannelModel({}, hf_tokenizer=WordTokenizer())
    terms = tuple(f"alpha{i:03d} beta{i:03d}" for i in range(300))
    transcript = split_transcriber(model).transcribe(audio, Vocabulary(version=1, terms=terms))
    assert transcript.vocabulary_terms_used == terms[:110]
    hotwords = [call[1]["hotwords"] for call in model.calls]
    assert hotwords == [", ".join(terms[:110])] * 2


def test_corrections_apply_to_both_channels_and_their_counts_add_up(audio):
    def thanks(start):
        return raw_segment(
            start,
            start + 1.0,
            " Thanks jay son.",
            [
                raw_word(start, start + 0.4, " Thanks", 0.9),
                raw_word(start + 0.4, start + 0.7, " jay", 0.8),
                raw_word(start + 0.7, start + 1.0, " son.", 0.6),
            ],
        )

    model = ChannelModel({LEFT: [thanks(0.0)], RIGHT: [thanks(2.0)]})
    vocabulary = Vocabulary(version=5, corrections=(Correction("jay son", "Jason"),))
    transcript = split_transcriber(model).transcribe(audio, vocabulary)
    assert [(s.channel, s.text) for s in transcript.segments] == [
        (0, "Thanks Jason."),
        (1, "Thanks Jason."),
    ]
    assert transcript.segments[1].words[1].original == " jay son."
    assert transcript.corrections_applied == (
        AppliedCorrection(heard="jay son", replacement="Jason", count=2),
    )


def test_one_model_load_serves_both_channels(audio):
    loads = []
    model = ChannelModel({})

    def factory(settings):
        loads.append(settings)
        return model

    transcriber = Transcriber(
        replace(SETTINGS, channel_mode="stereo_split"),
        model_factory=factory,
        decode_errors=(),
        channel_count=lambda path: 2,
        decode_stereo=lambda path: (LEFT, RIGHT),
    )
    transcriber.transcribe(audio)
    transcriber.transcribe(audio)
    assert len(loads) == 1
    assert [call[0] for call in model.calls] == [LEFT, RIGHT, LEFT, RIGHT]


def test_custom_labels_are_carried_into_the_transcript(audio):
    transcriber = split_transcriber(ChannelModel({}), labels=("Agent", "Customer"))
    assert transcriber.transcribe(audio).channel_labels == ("Agent", "Customer")


@pytest.mark.parametrize(
    ("channels", "found"),
    [(1, "1 audio channel;"), (6, "6 audio channels;"), (0, "no audio stream;")],
)
def test_stereo_split_needs_exactly_two_channels(audio, channels, found):
    model = ChannelModel({})
    with pytest.raises(UndecodableAudioError, match=found) as excinfo:
        split_transcriber(model, channels=channels).transcribe(audio)
    assert "recording.mp3" in str(excinfo.value)
    assert "stereo_split needs a two-channel (stereo) recording" in str(excinfo.value)
    assert model.calls == []


def test_auto_splits_a_two_channel_file(audio):
    model = ChannelModel({LEFT: [seg(0.0, 1.0, " Hello.")]})
    transcript = split_transcriber(model, mode="auto").transcribe(audio)
    assert transcript.channel_labels == ("Left", "Right")
    assert [(s.channel, s.text) for s in transcript.segments] == [(0, "Hello.")]


@pytest.mark.parametrize("channels", [0, 1, 6])
def test_auto_mixes_anything_but_two_channels_as_mono(audio, channels):
    model = ChannelModel({str(audio): [seg(0.0, 1.0, " Hello.")]})
    decoded = []
    transcript = split_transcriber(
        model, channels=channels, mode="auto", decode=lambda path: decoded.append(path)
    ).transcribe(audio)
    assert decoded == []
    assert [call[0] for call in model.calls] == [str(audio)]
    assert transcript.channel_labels is None
    assert transcript.segments[0].channel is None


def test_mono_never_looks_at_the_channels(audio):
    probes = []
    model = ChannelModel({})
    split_transcriber(model, mode="mono", probes=probes).transcribe(audio)
    assert probes == []
    assert [call[0] for call in model.calls] == [str(audio)]


def test_a_failing_channel_probe_is_undecodable(audio):
    transcriber = split_transcriber(ChannelModel({}), channels=DecodeError("Invalid data"))
    with pytest.raises(UndecodableAudioError, match="recording.mp3") as excinfo:
        transcriber.transcribe(audio)
    assert isinstance(excinfo.value.__cause__, DecodeError)


def test_a_failing_stereo_decode_is_undecodable(audio):
    def broken(path):
        raise DecodeError("truncated")

    with pytest.raises(UndecodableAudioError, match="recording.mp3"):
        split_transcriber(ChannelModel({}), decode=broken).transcribe(audio)


def test_per_call_settings_choose_the_channel_handling(audio):
    model = ChannelModel({})
    transcriber = Transcriber(
        SETTINGS,
        model_factory=lambda _: model,
        decode_errors=(),
        channel_count=lambda path: 2,
        decode_stereo=lambda path: (LEFT, RIGHT),
    )
    transcriber.transcribe(audio)
    split = replace(SETTINGS, channel_mode="auto")
    assert transcriber.transcribe(audio, settings=split).settings == split
    assert [call[0] for call in model.calls] == [str(audio), LEFT, RIGHT]


def test_per_call_settings_must_use_the_loaded_model(audio):
    transcriber = make_transcriber(FakeModel())
    other = TranscribeSettings(model="tiny.en", compute_type="int8", device="cpu")
    with pytest.raises(ValueError, match="different model"):
        transcriber.transcribe(audio, settings=other)


def test_jobs_with_different_channel_settings_share_one_loaded_model(monkeypatch, audio):
    loads = []
    model = ChannelModel({})

    def factory(settings):
        loads.append(settings)
        return model

    monkeypatch.setattr(engine_transcriber, "_default_model_factory", factory)
    monkeypatch.setattr(engine_transcriber, "_default_channel_count", lambda path: 2)
    monkeypatch.setattr(engine_transcriber, "_default_decode_stereo", lambda path: (LEFT, RIGHT))
    engine_transcriber._shared_transcriber.cache_clear()
    try:
        engine_transcriber.transcribe(audio, SETTINGS)
        split = replace(SETTINGS, channel_mode="stereo_split", channel_labels=("Agent", "Customer"))
        transcript = engine_transcriber.transcribe(audio, split)
        engine_transcriber.transcribe(audio, replace(SETTINGS, temperatures=(0.0,)))
    finally:
        engine_transcriber._shared_transcriber.cache_clear()
    assert loads == [SETTINGS]
    assert transcript.settings == split
    assert transcript.channel_labels == ("Agent", "Customer")
    assert [call[0] for call in model.calls] == [str(audio), LEFT, RIGHT, str(audio)]
    assert model.calls[3][1]["temperature"] == [0.0]


# --- the real decoder on generated WAVs (no model download) --------------------------


class HearsSound:
    """A model that 'hears' one segment in any audio that is not silent. It receives the real
    decoder's channel arrays, so it shows which channel the sound ended up in."""

    def __init__(self):
        self.calls = []

    def transcribe(self, audio, **kwargs):
        self.calls.append(audio)
        loud = float(abs(audio).max()) > 0.05
        segments = [seg(0.0, 1.0, " Sound.")] if loud else []
        return iter(segments), SimpleNamespace(duration=len(audio) / 16000)


def real_split(*, mode="stereo_split", labels=("Left", "Right"), model=None):
    """A Transcriber with the real channel probe and stereo decoder and a fake model."""
    model = model or HearsSound()
    settings = replace(SETTINGS, channel_mode=mode, channel_labels=labels)
    return Transcriber(settings, model_factory=lambda _: model), model


@pytest.mark.parametrize(
    ("tones", "channel", "line"),
    [([440, None], 0, "Agent: Sound.\n"), ([None, 440], 1, "Customer: Sound.\n")],
)
def test_a_real_stereo_file_is_split_into_its_left_and_right_channels(
    make_wav, tones, channel, line
):
    path = make_wav("call.wav", tones)
    transcriber, model = real_split(labels=("Agent", "Customer"))
    transcript = transcriber.transcribe(path)
    assert [(s.channel, s.text) for s in transcript.segments] == [(channel, "Sound.")]
    assert render_txt(transcript) == line
    assert transcript.duration == pytest.approx(1.0, abs=0.01)
    assert len(model.calls) == 2


def test_a_real_stereo_file_with_two_silent_channels_has_no_speech(make_wav):
    transcriber, _ = real_split()
    transcript = transcriber.transcribe(make_wav("call.wav", [None, None]))
    assert transcript.segments == ()
    assert transcript.channel_labels == ("Left", "Right")


def test_a_real_mono_file_is_refused_in_stereo_split(make_wav):
    path = make_wav("recording.wav", [440])
    transcriber, model = real_split()
    with pytest.raises(UndecodableAudioError, match=r"recording\.wav has 1 audio channel;"):
        transcriber.transcribe(path)
    assert model.calls == []


def test_a_real_six_channel_file_is_refused_in_stereo_split_and_mixed_in_auto(make_wav):
    path = make_wav("surround.wav", [440] * 6)
    transcriber, _ = real_split()
    with pytest.raises(UndecodableAudioError, match="6 audio channels"):
        transcriber.transcribe(path)
    model = FakeModel()
    auto, _ = real_split(mode="auto", model=model)
    assert auto.transcribe(path).channel_labels is None
    assert [call[0] for call in model.calls] == [str(path)]


def test_a_real_corrupt_file_is_undecodable_in_split_mode(tmp_path):
    corrupt = tmp_path / "corrupt.wav"
    corrupt.write_bytes(bytes(range(256)) * 64)
    transcriber, _ = real_split()
    with pytest.raises(UndecodableAudioError, match="corrupt.wav"):
        transcriber.transcribe(corrupt)


def test_auto_on_a_real_mono_file_writes_exactly_what_mono_writes(make_wav, tmp_path):
    path = make_wav("recording.wav", [440])

    def outputs(mode):
        model = FakeModel(segments=[seg(0.0, 1.0, " Good morning.")], duration=1.0)
        transcriber, _ = real_split(mode=mode, model=model)
        files = write_outputs(transcriber.transcribe(path), tmp_path / mode)
        assert [call[0] for call in model.calls] == [str(path)]
        return [p.read_bytes() for p in (files.txt, files.srt, files.segments_json)]

    assert outputs("auto") == outputs("mono")
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest packages/engine/tests/test_transcriber.py -v`
Expected: the new tests FAIL — `TypeError: Transcriber.__init__() got an unexpected keyword argument 'channel_count'`; the existing tests still PASS.

- [ ] **Step 4: Implement the transcriber**

Replace `packages/engine/src/swarmscribe_engine/transcriber.py` with:

```python
import hashlib
from collections.abc import Callable, Sequence
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from typing import Any

from .types import (
    EMPTY_VOCABULARY,
    FIXED_SETTINGS,
    Segment,
    TranscribeSettings,
    Transcript,
    UndecodableAudioError,
    Vocabulary,
    Word,
)
from .vocabulary import (
    HOTWORDS_TOKEN_BUDGET,
    apply_corrections,
    build_hotwords,
    select_bias_terms,
)

ModelFactory = Callable[[TranscribeSettings], Any]
ChannelCounter = Callable[[Path], int]
StereoDecoder = Callable[[Path], tuple[Any, Any]]

SAMPLE_RATE = 16000  # what Whisper models take, and what faster-whisper resamples to
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


def _default_channel_count(path: Path) -> int:
    """Channels of the first audio stream (the one the decoder reads); 0 when there is none."""
    import av

    with av.open(str(path)) as container:
        streams = container.streams.audio
        return streams[0].codec_context.layout.nb_channels if streams else 0


def _default_decode_stereo(path: Path) -> tuple[Any, Any]:
    """The left and right channels as two separate 16 kHz float arrays."""
    from faster_whisper import decode_audio

    return decode_audio(str(path), sampling_rate=SAMPLE_RATE, split_stereo=True)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


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


def _not_stereo(name: str, channels: int) -> UndecodableAudioError:
    if channels == 0:
        found = "no audio stream"
    else:
        found = f"{channels} audio channel" + ("" if channels == 1 else "s")
    return UndecodableAudioError(
        f"{name} has {found}; stereo_split needs a two-channel (stereo) recording"
    )


def _merge(left: Sequence[Segment], right: Sequence[Segment]) -> tuple[Segment, ...]:
    """Both channels in one list by start time; on a tie the left channel comes first."""
    tagged = [replace(s, channel=0) for s in left] + [replace(s, channel=1) for s in right]
    return tuple(sorted(tagged, key=lambda segment: (segment.start, segment.channel)))


def _model_settings(settings: TranscribeSettings) -> TranscribeSettings:
    """Only what loading a model depends on."""
    return TranscribeSettings(
        model=settings.model, compute_type=settings.compute_type, device=settings.device
    )


class Transcriber:
    """Holds one loaded model and transcribes files with the fixed settings."""

    def __init__(
        self,
        settings: TranscribeSettings,
        *,
        model_factory: ModelFactory = _default_model_factory,
        decode_errors: tuple[type[BaseException], ...] | None = None,
        channel_count: ChannelCounter = _default_channel_count,
        decode_stereo: StereoDecoder = _default_decode_stereo,
    ) -> None:
        self.settings = settings
        self._decode_errors = _default_decode_errors() if decode_errors is None else decode_errors
        self._channel_count = channel_count
        self._decode_stereo = decode_stereo
        self._model = model_factory(settings)

    def _select_terms(self, terms: Sequence[str]) -> tuple[str, ...]:
        tokenizer = getattr(self._model, "hf_tokenizer", None)
        if tokenizer is None:
            return select_bias_terms(terms)

        def tokens(text: str) -> int:
            # faster-whisper encodes hotwords as tokenizer.encode(" " + hotwords.strip()).
            return len(tokenizer.encode(" " + text, add_special_tokens=False).ids)

        return select_bias_terms(terms, budget=HOTWORDS_TOKEN_BUDGET, measure=tokens)

    def _settings_for(self, settings: TranscribeSettings | None) -> TranscribeSettings:
        if settings is None:
            return self.settings
        if _model_settings(settings) != _model_settings(self.settings):
            raise ValueError("these settings need a different model from the one loaded")
        return settings

    def _splits(self, path: Path, settings: TranscribeSettings) -> bool:
        if settings.channel_mode == "mono":
            return False
        channels = self._channel_count(path)
        if settings.channel_mode == "auto":
            return channels == 2
        if channels != 2:
            raise _not_stereo(path.name, channels)
        return True

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
        # Corrections never span a segment, so one pass over the merged list equals one per
        # channel, and each correction's count is summed across the channels.
        segments, applied = apply_corrections(segments, vocabulary.corrections)
        return Transcript(
            source_name=path.name,
            source_checksum=sha256_file(path),
            duration=duration,
            settings=settings,
            vocabulary_version=vocabulary.version,
            vocabulary_terms_used=terms_used,
            corrections_applied=applied,
            segments=segments,
            channel_labels=labels,
        )


@lru_cache(maxsize=1)
def _shared_transcriber(model_settings: TranscribeSettings) -> Transcriber:
    # Looked up at call time, not bound as defaults, so tests can substitute them.
    return Transcriber(
        model_settings,
        model_factory=_default_model_factory,
        channel_count=_default_channel_count,
        decode_stereo=_default_decode_stereo,
    )


def transcribe(
    path: Path, settings: TranscribeSettings, vocabulary: Vocabulary = EMPTY_VOCABULARY
) -> Transcript:
    """Transcribe one file, reusing the model already loaded for this model, compute type and
    device whatever the channel handling or temperature ladder."""
    return _shared_transcriber(_model_settings(settings)).transcribe(
        path, vocabulary, settings=settings
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest packages/engine -v`
Expected: PASS — the new split tests, the real-decoder tests (PyAV and `decode_audio` on generated WAVs; no model download), and every earlier engine test including the pinned mono bytes.

Run: `uv run ruff check packages/engine`
Expected: no findings.

- [ ] **Step 6: Commit**

```bash
git add packages/engine/src/swarmscribe_engine/transcriber.py packages/engine/tests/conftest.py packages/engine/tests/test_transcriber.py
git commit -m "Engine: transcribe left and right channels separately with one loaded model

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Engine CLI — `--channels` and `--labels`

**Files:**
- Modify: `packages/engine/src/swarmscribe_engine/cli.py` (imports, `_parser`, `run`; new `CHANNEL_MODES`, `parse_labels`)
- Modify: `README.md` ("Transcribe one file")
- Test: `packages/engine/tests/test_cli.py`

**Interfaces:**
- Consumes: `TranscribeSettings(channel_mode=, channel_labels=)`, `DEFAULT_CHANNEL_LABELS` (Task 2).
- Produces: `cli.CHANNEL_MODES = {"mono": "mono", "stereo-split": "stereo_split", "auto": "auto"}`; `cli.parse_labels(text: str) -> tuple[str, ...]` (raises `argparse.ArgumentTypeError` unless exactly two comma-separated names; strips each).

- [ ] **Step 1: Write the failing tests**

Append to `packages/engine/tests/test_cli.py`:

```python
def _settings_seen(tmp_path, make_transcript, *flags):
    seen = []

    def fake_transcribe(path, settings, vocabulary):
        seen.append(settings)
        return make_transcript()

    code = run(
        [str(_recording(tmp_path)), "--out", str(tmp_path), *flags],
        transcribe_fn=fake_transcribe,
        resolve_fn=lambda preference: CPU,
    )
    return code, seen


def test_channels_default_to_mono(tmp_path, make_transcript):
    code, seen = _settings_seen(tmp_path, make_transcript)
    assert code == 0
    assert (seen[0].channel_mode, seen[0].channel_labels) == ("mono", ("Left", "Right"))


def test_stereo_split_with_labels_reaches_the_engine(tmp_path, make_transcript):
    code, seen = _settings_seen(
        tmp_path, make_transcript, "--channels", "stereo-split", "--labels", "Agent, Customer"
    )
    assert code == 0
    assert seen == [
        TranscribeSettings(
            model="distil-large-v3",
            compute_type="int8",
            device="cpu",
            channel_mode="stereo_split",
            channel_labels=("Agent", "Customer"),
        )
    ]


def test_auto_keeps_the_default_labels(tmp_path, make_transcript):
    code, seen = _settings_seen(tmp_path, make_transcript, "--channels", "auto")
    assert code == 0
    assert (seen[0].channel_mode, seen[0].channel_labels) == ("auto", ("Left", "Right"))


def test_labels_without_a_split_mode_are_a_usage_error(tmp_path, capsys):
    with pytest.raises(SystemExit) as excinfo:
        run(
            [str(_recording(tmp_path)), "--out", str(tmp_path), "--labels", "Agent,Customer"],
            transcribe_fn=None,
            resolve_fn=lambda preference: CPU,
        )
    assert excinfo.value.code == 2
    assert "--labels needs --channels stereo-split or auto" in capsys.readouterr().err


@pytest.mark.parametrize("labels", ["Agent", "Agent,Customer,Supervisor", ""])
def test_labels_must_be_two_comma_separated_names(tmp_path, capsys, labels):
    with pytest.raises(SystemExit) as excinfo:
        run(
            [
                str(_recording(tmp_path)),
                "--out",
                str(tmp_path),
                "--channels",
                "auto",
                "--labels",
                labels,
            ],
            transcribe_fn=None,
            resolve_fn=lambda preference: CPU,
        )
    assert excinfo.value.code == 2
    assert "exactly two names" in capsys.readouterr().err


def test_an_unusable_label_exits_2_with_a_message(tmp_path, make_transcript, capsys):
    code = run(
        [
            str(_recording(tmp_path)),
            "--out",
            str(tmp_path),
            "--channels",
            "auto",
            "--labels",
            "Agent," + "x" * 41,
        ],
        transcribe_fn=lambda path, settings, vocabulary: make_transcript(),
        resolve_fn=lambda preference: CPU,
    )
    assert code == 2
    err = capsys.readouterr().err
    assert err.startswith("error: channel labels must be 1 to 40 characters")
    assert "Traceback" not in err


def test_an_unknown_channel_mode_is_a_usage_error(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        run(
            [str(_recording(tmp_path)), "--out", str(tmp_path), "--channels", "stereo"],
            transcribe_fn=None,
            resolve_fn=lambda preference: CPU,
        )
    assert excinfo.value.code == 2


def test_a_mono_file_in_stereo_split_exits_2_and_writes_nothing(tmp_path, capsys):
    out_dir = tmp_path / "out"

    def fake_transcribe(path, settings, vocabulary):
        raise UndecodableAudioError(
            "recording.mp3 has 1 audio channel; stereo_split needs a two-channel (stereo) "
            "recording"
        )

    code = run(
        [str(_recording(tmp_path)), "--out", str(out_dir), "--channels", "stereo-split"],
        transcribe_fn=fake_transcribe,
        resolve_fn=lambda preference: CPU,
    )
    assert code == 2
    assert "error: recording.mp3 has 1 audio channel" in capsys.readouterr().err
    assert not out_dir.exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/engine/tests/test_cli.py -v`
Expected: the new tests FAIL — `error: unrecognized arguments: --channels` (SystemExit where a return code was expected).

- [ ] **Step 3: Implement the flags**

In `packages/engine/src/swarmscribe_engine/cli.py`, replace the `from .types import (...)` block with:

```python
from .types import (
    DEFAULT_CHANNEL_LABELS,
    Correction,
    DeviceChoice,
    EngineError,
    TranscribeSettings,
    Transcript,
    Vocabulary,
)
```

After `ResolveFn = …` add:

```python
CHANNEL_MODES = {"mono": "mono", "stereo-split": "stereo_split", "auto": "auto"}


def parse_labels(text: str) -> tuple[str, ...]:
    names = tuple(name.strip() for name in text.split(","))
    if len(names) != 2:
        raise argparse.ArgumentTypeError(
            'give exactly two names separated by a comma, e.g. "Agent,Customer"'
        )
    return names
```

In `_parser()`, before `return parser`, add:

```python
    parser.add_argument(
        "--channels",
        choices=list(CHANNEL_MODES),
        default="mono",
        help="mono mixes the channels (default); stereo-split transcribes left and right "
        "separately; auto splits a two-channel file and mixes anything else",
    )
    parser.add_argument(
        "--labels",
        type=parse_labels,
        help='names for the left and right channels, e.g. "Agent,Customer" (default: Left,Right)',
    )
```

Replace the start of `run` up to and including the `settings = TranscribeSettings(...)` statement with:

```python
def run(
    argv: Sequence[str],
    *,
    transcribe_fn: TranscribeFn = transcribe,
    resolve_fn: ResolveFn = resolve_device,
) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.labels is not None and args.channels == "mono":
        parser.error("--labels needs --channels stereo-split or auto")
    try:
        choice = resolve_fn(args.device)
        settings = TranscribeSettings(
            model=args.model or choice.model,
            compute_type=args.compute_type or choice.compute_type,
            device=choice.device,
            channel_mode=CHANNEL_MODES[args.channels],
            channel_labels=args.labels or DEFAULT_CHANNEL_LABELS,
        )
```

(The rest of `run` — vocabulary, `transcribe_fn`, `write_outputs`, the `except` clause and the printed paths — is unchanged.)

- [ ] **Step 4: Document the flags**

In `README.md`, in "Transcribe one file", after the `--corrections` bullet add:

```markdown
- `--channels mono|stereo-split|auto` — default `mono`: the channels are mixed.
  `stereo-split` transcribes the left and right channels separately (for
  recordings with one speaker per channel, such as calls) and merges them in
  time order; a file that is not two-channel is refused. `auto` splits a
  two-channel file and mixes anything else.
- `--labels "Agent,Customer"` — names for the left and right channels
  (default `Left,Right`), 1–40 characters each. In a split transcript every
  txt line and srt cue starts with `<label>: `, and `segments.json` records
  each segment's `channel` (0 left, 1 right) and the labels. Only allowed
  with `stereo-split` or `auto`.
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest packages/engine -v`
Expected: PASS.

Run: `uv run ruff check packages/engine`
Expected: no findings.

- [ ] **Step 6: Commit**

```bash
git add packages/engine/src/swarmscribe_engine/cli.py packages/engine/tests/test_cli.py README.md
git commit -m "Engine CLI: --channels and --labels

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Real-model smoke test — a stereo file in split mode

**Files:**
- Test: `packages/engine/tests/test_smoke.py`

**Interfaces:**
- Consumes: `Transcriber.transcribe(..., settings=)` (Task 3); `make_wav` fixture (Task 3); `write_outputs` (Task 2); `SegmentsDocument` (Task 1).
- Produces: nothing new.

- [ ] **Step 1: Write the smoke test**

In `packages/engine/tests/test_smoke.py`, add `from dataclasses import replace` to the imports, and append:

```python
def test_real_model_transcribes_a_stereo_file_in_split_mode(transcriber, make_wav, tmp_path):
    stereo = make_wav("call.wav", [440, None], seconds=3.0)
    settings = replace(SETTINGS, channel_mode="stereo_split", channel_labels=("Agent", "Customer"))
    vocabulary = Vocabulary(
        version=3, terms=("Ashford",), corrections=(Correction("ash ford", "Ashford"),)
    )
    model = transcriber._model
    transcript = transcriber.transcribe(stereo, vocabulary, settings=settings)
    assert transcriber._model is model  # the module-scoped model served both channels
    assert transcript.duration == pytest.approx(3.0, abs=0.1)
    assert transcript.channel_labels == ("Agent", "Customer")

    files = write_outputs(transcript, tmp_path / "out")
    document = SegmentsDocument.model_validate_json(files.segments_json.read_text("utf-8"))
    assert document.channel_labels == ["Agent", "Customer"]
    assert document.vocabulary_terms_used == ["Ashford"]
    assert all(segment.channel in (0, 1) for segment in document.segments)
    lines = files.txt.read_text("utf-8").splitlines()
    assert len(lines) == len(document.segments)
    assert all(line.startswith(("Agent: ", "Customer: ")) for line in lines)
    assert files.srt.is_file()


def test_real_decoder_refuses_a_mono_file_in_split_mode(transcriber, tone):
    settings = replace(SETTINGS, channel_mode="stereo_split")
    with pytest.raises(UndecodableAudioError, match=r"tone\.wav has 1 audio channel"):
        transcriber.transcribe(tone, settings=settings)
```

- [ ] **Step 2: Run the smoke tests**

Run: `uv run pytest packages/engine/tests/test_smoke.py -m smoke -v`
Expected: PASS (downloads `tiny.en` on first run). A pure tone may give zero segments; the test holds either way.

Run: `uv run pytest packages/engine -v`
Expected: PASS, with the smoke tests deselected.

- [ ] **Step 3: Commit**

```bash
git add packages/engine/tests/test_smoke.py
git commit -m "Engine smoke: real tiny.en transcribes a stereo file in split mode

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Leader — channel settings per location, carried in every claim (after Plan A2)

**Precondition:** Plan A2 is merged: `0003_admin.py` exists and `test_migrations.py` asserts head `"0003"`.

**Files:**
- Modify: `packages/leader/src/swarmscribe_leader/db/models.py` (`StorageLocation`, imports)
- Create: `packages/leader/src/swarmscribe_leader/db/migrations/versions/0004_channel_split.py`
- Modify: `packages/leader/src/swarmscribe_leader/jobs/claims.py` (`build_claim`)
- Test: `packages/leader/tests/test_migrations.py`, `packages/leader/tests/test_follower_api.py`

**Interfaces:**
- Consumes: `DEFAULT_CHANNEL_LABELS`, `JobSettings.channel_mode/channel_labels` (Task 1); migration `0003` (Plan A2).
- Produces: `StorageLocation.channel_mode: str` (default `"mono"`, server default `'mono'`); `StorageLocation.channel_labels: list[str]` (default and server default `["Left", "Right"]`); Alembic head `"0004"`; `build_claim` sets `JobSettings.channel_mode`/`channel_labels` from the recording's own location.

- [ ] **Step 1: Write the failing tests**

In `packages/leader/tests/test_migrations.py`, replace `test_database_is_at_the_head_revision` with:

```python
async def test_database_is_at_the_head_revision(engine):
    assert head_revision() == "0004"
    assert await current_revision(engine) == "0004"
```

and append:

```python
async def test_locations_created_without_channel_settings_are_mono(sessionmaker):
    # As rows created before migration 0004, or seeded by SQL that predates it, are.
    async with sessionmaker() as session:
        await session.execute(
            text(
                "insert into storage_locations (id, name, backend, config, input_prefix,"
                " output_prefix, pool, required_device, scan_interval_s, enabled,"
                " vocabulary_version) values (gen_random_uuid(), 'older', 'local',"
                " '{}'::jsonb, '', 'transcripts/', 'default', 'any', 900, true, 0)"
            )
        )
        await session.commit()
        location = await session.scalar(
            select(StorageLocation).where(StorageLocation.name == "older")
        )
    assert (location.channel_mode, location.channel_labels) == ("mono", ["Left", "Right"])
```

and change the models import line in that file to:

```python
from swarmscribe_leader.db.models import Base, SettingsProfile, StorageLocation
```

Append to `packages/leader/tests/test_follower_api.py`:

```python
async def test_a_claim_carries_its_locations_channel_settings(
    client, sessionmaker, factory, tmp_path
):
    location = await queue_one(sessionmaker, factory, tmp_path)
    async with sessionmaker() as session:
        row = await session.get(StorageLocation, location.id)
        row.channel_mode = "stereo_split"
        row.channel_labels = ["Agent", "Customer"]
        await session.commit()
    headers = await register(client, sessionmaker)
    claimed = await claim(client, headers)
    assert (claimed.settings.channel_mode, claimed.settings.channel_labels) == (
        "stereo_split",
        ("Agent", "Customer"),
    )


async def test_a_claim_from_a_location_left_at_the_defaults_is_mono(
    client, sessionmaker, factory, tmp_path
):
    await queue_one(sessionmaker, factory, tmp_path)
    headers = await register(client, sessionmaker)
    claimed = await claim(client, headers)
    assert (claimed.settings.channel_mode, claimed.settings.channel_labels) == (
        "mono",
        ("Left", "Right"),
    )


async def test_the_channel_settings_come_from_the_recordings_location_not_the_output(
    client, sessionmaker, factory, tmp_path
):
    location = await queue_one(sessionmaker, factory, tmp_path)
    output = await factory.location(channel_mode="auto", channel_labels=["Host", "Guest"])
    async with sessionmaker() as session:
        row = await session.get(StorageLocation, location.id)
        row.output_location_id = output.id
        row.channel_mode = "stereo_split"
        await session.commit()
    headers = await register(client, sessionmaker)
    claimed = await claim(client, headers)
    assert (claimed.settings.channel_mode, claimed.settings.channel_labels) == (
        "stereo_split",
        ("Left", "Right"),
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/leader/tests/test_migrations.py packages/leader/tests/test_follower_api.py -v`
Expected: FAIL — head is `"0003"`; `column "channel_mode" does not exist` / `AttributeError: 'StorageLocation' object has no attribute 'channel_mode'`.

- [ ] **Step 3: Add the columns to the model**

In `packages/leader/src/swarmscribe_leader/db/models.py`, add `text` to the `from sqlalchemy import (...)` list, and add after that import block:

```python
from swarmscribe_protocol import DEFAULT_CHANNEL_LABELS
```

In `class StorageLocation`, after `last_scan_error` (and after any column Plan A2 added, such as `scan_requested_at`), add:

```python
    # How stereo recordings here are transcribed: protocol JobSettings.channel_mode/_labels.
    channel_mode: Mapped[str] = mapped_column(String(16), default="mono", server_default="mono")
    channel_labels: Mapped[list[Any]] = mapped_column(
        default=lambda: list(DEFAULT_CHANNEL_LABELS),
        server_default=text("""'["Left", "Right"]'::jsonb"""),
    )
```

- [ ] **Step 4: Write migration 0004**

`packages/leader/src/swarmscribe_leader/db/migrations/versions/0004_channel_split.py`:

```python
"""per-channel transcription: channel mode and labels per storage location

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-03

Hand-written. Existing locations become mono with labels Left and Right.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "storage_locations",
        sa.Column("channel_mode", sa.String(length=16), server_default="mono", nullable=False),
    )
    op.add_column(
        "storage_locations",
        sa.Column(
            "channel_labels",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("""'["Left", "Right"]'::jsonb"""),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("storage_locations", "channel_labels")
    op.drop_column("storage_locations", "channel_mode")
```

- [ ] **Step 5: Carry the settings in the claim**

In `packages/leader/src/swarmscribe_leader/jobs/claims.py`, in `build_claim`, replace the `settings=JobSettings(...)` argument with:

```python
        settings=JobSettings(
            model=profile.model,
            compute_type=profile.compute_type,
            temperatures=tuple(profile.temperatures),
            # The recording's own location decides how it is transcribed.
            channel_mode=source.channel_mode,
            channel_labels=tuple(source.channel_labels),
        ),
```

(`source` is the recording's location returned by `_places`; leave the rest of `build_claim`, including Plan A2's changes, as it is.)

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest packages/leader -v`
Expected: PASS — including `test_migrations_produce_exactly_the_models` (0004 matches the model) and the concurrent-migrate test.

Run: `uv run ruff check packages/leader`
Expected: no findings.

- [ ] **Step 7: Commit**

```bash
git add packages/leader/src/swarmscribe_leader/db/models.py packages/leader/src/swarmscribe_leader/db/migrations/versions/0004_channel_split.py packages/leader/src/swarmscribe_leader/jobs/claims.py packages/leader/tests/test_migrations.py packages/leader/tests/test_follower_api.py
git commit -m "Leader: channel mode and labels per location, carried in every claim

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Leader admin — `locations add --channels --labels` (after Plan A2)

**Precondition:** Task 6 is done and Plan A2's admin API and CLI are merged.

**Files:**
- Modify: `packages/leader/src/swarmscribe_leader/ingest/locations.py` (`add_location`, imports)
- Modify: `packages/leader/src/swarmscribe_leader/api/admin_models.py` (imports, `LocationIn`, `LocationOut`)
- Modify: `packages/leader/src/swarmscribe_leader/reports.py` (`location_view`)
- Modify: `packages/leader/src/swarmscribe_leader/admin_cli/main.py` (`parse_labels`, `build_parser`, `dispatch`)
- Modify: `README.md` (admin CLI section)
- Test: `packages/leader/tests/test_admin_api.py`, `packages/leader/tests/test_admin_cli.py`

**Interfaces:**
- Consumes: `ChannelMode`, `ChannelLabels`, `DEFAULT_CHANNEL_LABELS` (Task 1); `StorageLocation.channel_mode/channel_labels` (Task 6); A2's `add_location`, `LocationIn`, `LocationOut`, `location_view`, `build_parser`, `dispatch`, `CliError`, test helpers `post`, `get`, `sign_in_as` and fixtures `admin_client`, `idp`, `cli`, `store`.
- Produces:
  - `add_location(session, *, name, root, input_prefix, output_prefix, pool, required_device, scan_interval_s, actor, channel_mode: str = "mono", channel_labels: Sequence[str] = DEFAULT_CHANNEL_LABELS) -> StorageLocation`.
  - `LocationIn.channel_mode: ChannelMode = "mono"`, `LocationIn.channel_labels: ChannelLabels = DEFAULT_CHANNEL_LABELS`; labels sent with `channel_mode: "mono"` → `422 invalid_request`. `LocationOut.channel_mode: str`, `LocationOut.channel_labels: list[str]`.
  - `swarmscribe-admin locations add NAME --root PATH [...] [--channels mono|stereo-split|auto] [--labels "A,B"]`; `admin_cli.main.parse_labels(text) -> tuple[str, ...]`; `locations list` shows `channel_mode`.

- [ ] **Step 1: Write the failing tests**

Append to `packages/leader/tests/test_admin_api.py`:

```python
# --- channel settings -------------------------------------------------------------


async def test_a_location_can_split_stereo_recordings(admin_client, idp, tmp_path):
    response = await post(
        admin_client,
        idp,
        "/v1/admin/locations",
        "admin",
        {
            "name": "calls-1",
            "root": str(tmp_path),
            "channel_mode": "stereo_split",
            "channel_labels": ["Agent", "Customer"],
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["channel_mode"], body["channel_labels"]) == (
        "stereo_split",
        ["Agent", "Customer"],
    )
    (listed,) = await get(admin_client, idp, "/v1/admin/locations")
    assert (listed["channel_mode"], listed["channel_labels"]) == (
        "stereo_split",
        ["Agent", "Customer"],
    )


async def test_a_location_is_mono_unless_told_otherwise(admin_client, idp, tmp_path):
    body = {"name": "archive-1", "root": str(tmp_path)}
    response = await post(admin_client, idp, "/v1/admin/locations", "admin", body)
    assert (response.json()["channel_mode"], response.json()["channel_labels"]) == (
        "mono",
        ["Left", "Right"],
    )


async def test_auto_keeps_the_default_labels(admin_client, idp, tmp_path):
    body = {"name": "mixed-1", "root": str(tmp_path), "channel_mode": "auto"}
    response = await post(admin_client, idp, "/v1/admin/locations", "admin", body)
    assert (response.json()["channel_mode"], response.json()["channel_labels"]) == (
        "auto",
        ["Left", "Right"],
    )


@pytest.mark.parametrize(
    "change",
    [
        {"channel_mode": "stereo"},
        {"channel_mode": "auto", "channel_labels": ["Agent"]},
        {"channel_mode": "auto", "channel_labels": ["Agent", "Customer", "Supervisor"]},
        {"channel_mode": "auto", "channel_labels": ["", "Customer"]},
        {"channel_mode": "auto", "channel_labels": ["A" * 41, "Customer"]},
        {"channel_mode": "auto", "channel_labels": ["Agent\nOne", "Customer"]},
        {"channel_mode": "auto", "channel_labels": ["Agent", "agent"]},
        {"channel_labels": ["Agent", "Customer"]},
        {"channel_mode": "mono", "channel_labels": ["Agent", "Customer"]},
    ],
)
async def test_invalid_channel_settings_are_refused(admin_client, idp, tmp_path, change):
    body = {"name": "calls-1", "root": str(tmp_path), **change}
    response = await post(admin_client, idp, "/v1/admin/locations", "admin", body)
    assert (response.status_code, response.json()["code"]) == (422, "invalid_request")
    assert "channel" in response.json()["message"]
    assert await get(admin_client, idp, "/v1/admin/locations") == []
```

Append to `packages/leader/tests/test_admin_cli.py`:

```python
async def test_locations_add_with_channels_and_labels(cli, store, idp, sessionmaker, tmp_path):
    sign_in_as(store, idp, "admin")
    code, _out, err = await cli(
        "locations",
        "add",
        "calls-1",
        "--root",
        str(tmp_path),
        "--channels",
        "stereo-split",
        "--labels",
        "Agent, Customer",
    )
    assert code == 0, err
    async with sessionmaker() as session:
        location = (await session.scalars(select(StorageLocation))).one()
    assert (location.channel_mode, location.channel_labels) == (
        "stereo_split",
        ["Agent", "Customer"],
    )
    assert "stereo_split" in (await cli("locations", "list"))[1]


async def test_locations_add_is_mono_by_default(cli, store, idp, sessionmaker, tmp_path):
    sign_in_as(store, idp, "admin")
    code, _out, err = await cli("locations", "add", "archive-1", "--root", str(tmp_path))
    assert code == 0, err
    async with sessionmaker() as session:
        location = (await session.scalars(select(StorageLocation))).one()
    assert (location.channel_mode, location.channel_labels) == ("mono", ["Left", "Right"])


async def test_locations_add_labels_without_a_split_mode_is_refused(
    cli, store, idp, sessionmaker, tmp_path
):
    sign_in_as(store, idp, "admin")
    code, out, err = await cli(
        "locations", "add", "calls-1", "--root", str(tmp_path), "--labels", "Agent,Customer"
    )
    assert (code, out) == (1, "")
    assert "--labels needs --channels stereo-split or auto" in err
    async with sessionmaker() as session:
        assert (await session.scalars(select(StorageLocation))).all() == []


@pytest.mark.parametrize("labels", ["Agent", "Agent,Customer,Supervisor"])
async def test_locations_add_labels_must_be_two_names(cli, store, idp, tmp_path, labels):
    sign_in_as(store, idp, "admin")
    with pytest.raises(SystemExit) as excinfo:
        await cli(
            "locations",
            "add",
            "calls-1",
            "--root",
            str(tmp_path),
            "--channels",
            "auto",
            "--labels",
            labels,
        )
    assert excinfo.value.code == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/leader/tests/test_admin_api.py packages/leader/tests/test_admin_cli.py -v`
Expected: the new tests FAIL — `extra_forbidden` 422 for `channel_mode` on `LocationIn`, `KeyError: 'channel_mode'` on responses, and `unrecognized arguments: --channels` in the CLI.

- [ ] **Step 3: Store the settings when a location is added**

In `packages/leader/src/swarmscribe_leader/ingest/locations.py`, add to the imports:

```python
from collections.abc import Sequence

from swarmscribe_protocol import DEFAULT_CHANNEL_LABELS
```

Change the signature of `add_location` to:

```python
async def add_location(
    session: AsyncSession,
    *,
    name: str,
    root: str,
    input_prefix: str,
    output_prefix: str,
    pool: str,
    required_device: str,
    scan_interval_s: int,
    actor: str,
    channel_mode: str = "mono",
    channel_labels: Sequence[str] = DEFAULT_CHANNEL_LABELS,
) -> StorageLocation:
```

and in its `StorageLocation(...)` constructor, after `vocabulary_version=0,` add:

```python
        channel_mode=channel_mode,
        channel_labels=list(channel_labels),
```

- [ ] **Step 4: Accept and show the settings in the API**

In `packages/leader/src/swarmscribe_leader/api/admin_models.py`, change the pydantic import to:

```python
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
```

and add:

```python
from swarmscribe_protocol import DEFAULT_CHANNEL_LABELS, ChannelLabels, ChannelMode
```

In `class LocationIn`, after `scan_interval_s`, add the fields:

```python
    channel_mode: ChannelMode = "mono"
    channel_labels: ChannelLabels = DEFAULT_CHANNEL_LABELS
```

and, after its existing validators, add:

```python
    @model_validator(mode="after")
    def _labels_need_a_split_mode(self) -> "LocationIn":
        if "channel_labels" in self.model_fields_set and self.channel_mode == "mono":
            raise ValueError("channel_labels needs channel_mode stereo_split or auto")
        return self
```

In `class LocationOut`, after `scan_requested: bool`, add:

```python
    channel_mode: str
    channel_labels: list[str]
```

In `packages/leader/src/swarmscribe_leader/reports.py`, in `location_view`, add to the returned dict after `"scan_requested": ...`:

```python
        "channel_mode": location.channel_mode,
        "channel_labels": list(location.channel_labels),
```

(`api/admin.py` needs no change: its `add_location` route passes `**body.model_dump()`, which now includes both fields.)

- [ ] **Step 5: Add the CLI flags**

In `packages/leader/src/swarmscribe_leader/admin_cli/main.py`, after `parse_duration` add:

```python
def parse_labels(text: str) -> tuple[str, ...]:
    names = tuple(name.strip() for name in text.split(","))
    if len(names) != 2:
        raise argparse.ArgumentTypeError(
            'give exactly two names separated by a comma, e.g. "Agent,Customer"'
        )
    return names
```

In `build_parser`, after `add.add_argument("--scan-interval", …)` add:

```python
    add.add_argument(
        "--channels",
        choices=("mono", "stereo-split", "auto"),
        default="mono",
        help="mono mixes channels (default); stereo-split transcribes left and right separately;"
        " auto splits two-channel files",
    )
    add.add_argument(
        "--labels",
        type=parse_labels,
        help='names of the left and right channels, e.g. "Agent,Customer" (default: Left,Right)',
    )
```

In `dispatch`, replace the `if action == "add":` branch of the `locations` command with:

```python
        if action == "add":
            if args.labels is not None and args.channels == "mono":
                raise CliError("--labels needs --channels stereo-split or auto")
            body = {
                "name": args.name,
                "root": args.root,
                "input_prefix": args.input_prefix,
                "output_prefix": args.output_prefix,
                "pool": args.pool,
                "required_device": args.device,
                "scan_interval_s": args.scan_interval,
                "channel_mode": args.channels.replace("-", "_"),
            }
            if args.labels is not None:
                body["channel_labels"] = list(args.labels)
            return await post("/v1/admin/locations", body=body), print_fields
```

and in the `if action == "list":` branch replace `columns` with:

```python
            columns = (
                "name",
                "enabled",
                "root",
                "input_prefix",
                "pool",
                "channel_mode",
                "last_scan_at",
                "last_scan_error",
            )
```

- [ ] **Step 6: Document the flags**

In `README.md`, in the admin CLI section Plan A2 added, directly after the `swarmscribe-admin locations add …` example, add:

```markdown
For recordings with one speaker per channel (such as call recordings), add
`--channels stereo-split --labels "Agent,Customer"`: each recording is
transcribed per channel and every transcript line starts with its channel's
label. `--channels auto` splits two-channel files and mixes the rest. The
default, `mono`, mixes the channels as before.
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest packages/leader -v`
Expected: PASS — the new tests and every Plan A2 admin test (`test_add_a_location_then_list_it`, `test_invalid_locations_are_refused`, `test_locations_add_list_ingest_and_disable`) unchanged.

Run: `uv run pytest packages -v`
Expected: PASS across protocol, engine and leader.

Run: `uv run ruff check packages`
Expected: no findings.

- [ ] **Step 8: Commit**

```bash
git add packages/leader/src/swarmscribe_leader/ingest/locations.py packages/leader/src/swarmscribe_leader/api/admin_models.py packages/leader/src/swarmscribe_leader/reports.py packages/leader/src/swarmscribe_leader/admin_cli/main.py packages/leader/tests/test_admin_api.py packages/leader/tests/test_admin_cli.py README.md
git commit -m "Admin: locations add --channels and --labels

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Self-review

**Spec coverage.** §1 success criteria: labelled, time-ordered split transcript (Tasks 2–3), mono unchanged and default (Task 2 pin, Task 3 auto-on-mono bytes), vocabulary and corrections on both channels (Task 3), one model load (Task 3, Task 5). §2 modes and label rules (Tasks 1–3). §3 protocol fields, `PROTOCOL_VERSION` 1, snapshot regenerated (Task 1). §4 engine: settings (Task 2), `decode_audio(split_stereo=True)` and PyAV channel count (Task 3), same model/settings/hotwords/ladder (Task 3), corrections summed (Task 3), merge by `(start, channel)` (Task 3), duration (Task 3), `UndecodableAudioError` (Task 3); outputs (Task 2); CLI (Task 4). §5 leader: columns and migration (Task 6, numbered 0004), claim (Task 6), `locations add` flags (Task 7). §6 testing: generated stereo WAVs with a tone on one side (Task 3), fake-model merge/ties/labels/corrections/hotwords/one load (Tasks 2–3), `stereo_split` on mono and `auto` on mono (Task 3), protocol (Task 1), leader claim (Task 6), real `tiny.en` smoke (Task 5). §7 build order: protocol → engine → CLI → leader, leader after A2 (header).

**Type consistency.** `channel_mode` values are `"mono" | "stereo_split" | "auto"` everywhere internally; only the two CLIs spell `stereo-split` and map it. `channel_labels` is a tuple in `JobSettings`/`TranscribeSettings`/`Transcript`, a list in `SegmentsDocument`, the database row and `LocationOut`. `Transcriber(..., channel_count=, decode_stereo=)` and `transcribe(..., settings=)` match between Task 3's code, its tests and Task 5.
