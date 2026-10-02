# Vocabulary and Corrections (Protocol + Engine) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an operator supply specific words to recognise and "heard as => should be" fixes, have the engine apply both on every transcription, and record exactly what was used.

**Architecture:** The protocol gains `Vocabulary` (version, terms, corrections), carried in the claim, and `segments.json` records the version, the terms actually used for biasing and every correction that fired. The engine passes leading terms to Whisper as hotwords (applied to every window) and applies corrections after transcription with a pure, token-based matcher that works on the word list. The CLI reads the two operator files. Versioning, reporting and re-queueing belong to the leader plan and are out of scope here.

**Tech Stack:** Python 3.11+, uv workspace, Pydantic v2, faster-whisper, pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-10-02-swarmscribe-architecture-design.md` — section 16 (16.1, 16.3, 16.4, 16.7), plus the carried-forward items in `docs/superpowers/plans/2026-10-02-protocol-and-engine-followups.md` under "For the vocabulary plan".

## Global Constraints

- Nothing in code, defaults, examples, test data or documentation may be specific to one kind of content or organisation. Use neutral sample data (`recording.mp3`, `Ashford`, `Jason`).
- Dependency rule: `protocol` imports nothing internal. `engine` imports nothing internal at runtime; engine *tests* may import `swarmscribe_protocol`.
- `PROTOCOL_VERSION` stays `1` and `schema_version` stays `1`: the protocol has not shipped. The schema snapshot is regenerated, never hand-edited.
- "Glossary" is replaced by "vocabulary" everywhere: no `glossary` field, parameter or flag remains when this plan is done.
- Fixed transcription behaviour is unchanged and not configurable: `language="en"`, `condition_on_previous_text=False`, VAD filter on, word timestamps on; temperature ladder capped at `0.4`.
- Biasing uses hotwords only; `initial_prompt` is not passed to the model.
- Hotwords budget: leading terms in order, joined with `", "`, up to `600` characters; stop at the first term that does not fit (never skip ahead to a shorter one).
- Corrections: whole-word, case-insensitive, longest `heard` first, never inside a longer word, never re-applied to their own output; punctuation and spacing around a match are kept.
- A multi-word correction merges words into one: start of the first, end of the last, lowest probability of the group; `original` holds the text before correction.
- Vocabulary version `0` means no vocabulary.
- All text files are UTF-8 with `\n` line endings on every OS. `segments.json` is written last.
- No audio or transcript text in logs or CLI output.
- On this Windows machine `uv` is not on PATH: run `python -m uv …` wherever a step says `uv …`.
- If `ruff check` flags import order or line length in code copied from this plan, reorder the imports or wrap the line without changing behaviour.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

Inputs the spec implies but does not spell out, most likely first. Each has a test in the task named.

1. **A `heard` text that is part of a longer word** (`art` inside `party`): must not fire. — Task 2.
2. **Punctuation, spacing and case around a match** (`" jay"`, `" son,"` → `" Jason,"`; `JAY SON` matches): surrounding characters survive. — Task 2.
3. **Overlapping and chained rules** (`new => X` with `new york => Y`; `a b => c` with `c => d`): the longest wins and output is never corrected again. — Task 2.
4. **A very large vocabulary** (1,000 terms, duplicates, blank lines): only leading terms within the budget are sent, in order, and those are what is recorded. — Task 2 (selection), Task 3 (recorded).
5. **A malformed corrections file** (line without `=>`, empty side, Notepad BOM, CRLF): a one-line error naming the file and line number, exit code 2, no traceback. — Task 4.

## File Structure

```
packages/protocol/src/swarmscribe_protocol/
  vocabulary.py      NEW  Correction, Vocabulary, AppliedCorrection
  messages.py        MOD  ClaimResponse.glossary -> vocabulary
  segments.py        MOD  Word.original; SegmentsDocument vocabulary fields (Task 3)
  schema.py          MOD  export the three new models
  __init__.py        MOD  re-exports
packages/protocol/tests/
  test_vocabulary.py NEW
  test_messages.py   MOD
  test_segments.py   MOD  (Task 3)
  schema_v1.json     REGENERATED (Tasks 1 and 3)
packages/engine/src/swarmscribe_engine/
  types.py           MOD  Correction, Vocabulary, AppliedCorrection, Word.original, FIXED_SETTINGS, Transcript fields
  vocabulary.py      NEW  select_bias_terms, build_hotwords, apply_corrections
  transcriber.py     MOD  takes Vocabulary; hotwords; applies corrections
  writers.py         MOD  records vocabulary fields; allow_nan=False
  cli.py             MOD  --vocabulary, --corrections
  __init__.py        MOD  re-exports
packages/engine/tests/
  test_vocabulary.py NEW
  conftest.py, test_transcriber.py, test_writers.py, test_cli.py, test_smoke.py, test_device.py  MOD
README.md            MOD
```

---

### Task 1: Protocol — vocabulary models in the claim

**Files:**
- Create: `packages/protocol/src/swarmscribe_protocol/vocabulary.py`
- Modify: `packages/protocol/src/swarmscribe_protocol/messages.py`, `schema.py`, `__init__.py`
- Modify: `packages/protocol/tests/test_messages.py`
- Regenerate: `packages/protocol/tests/schema_v1.json`
- Test: `packages/protocol/tests/test_vocabulary.py`

**Interfaces:**
- Consumes: `WireModel` from `swarmscribe_protocol.base`.
- Produces (importable from `swarmscribe_protocol`):
  - `Correction(heard: str, replacement: str)` — both at least one character.
  - `Vocabulary(version: int, terms: list[str] = [], corrections: list[Correction] = [])` — `version >= 0`.
  - `AppliedCorrection(heard: str, replacement: str, count: int)` — `count >= 1`. (Used by Task 3.)
  - `ClaimResponse.vocabulary: Vocabulary` replaces `ClaimResponse.glossary`.

- [ ] **Step 1: Write the failing tests**

`packages/protocol/tests/test_vocabulary.py`:

```python
import pytest
from pydantic import ValidationError

from swarmscribe_protocol import AppliedCorrection, Correction, Vocabulary


def test_vocabulary_round_trips_through_json():
    vocabulary = Vocabulary(
        version=3,
        terms=["Ashford", "José"],
        corrections=[Correction(heard="jay son", replacement="Jason")],
    )
    assert Vocabulary.model_validate_json(vocabulary.model_dump_json()) == vocabulary


def test_version_zero_with_nothing_in_it_means_no_vocabulary():
    vocabulary = Vocabulary(version=0)
    assert vocabulary.terms == []
    assert vocabulary.corrections == []


def test_version_cannot_be_negative():
    with pytest.raises(ValidationError):
        Vocabulary(version=-1)


@pytest.mark.parametrize("field", ["heard", "replacement"])
def test_correction_sides_cannot_be_empty(field):
    values = {"heard": "jay son", "replacement": "Jason", field: ""}
    with pytest.raises(ValidationError):
        Correction(**values)


def test_applied_correction_counts_at_least_one():
    AppliedCorrection(heard="jay son", replacement="Jason", count=1)
    with pytest.raises(ValidationError):
        AppliedCorrection(heard="jay son", replacement="Jason", count=0)
```

In `packages/protocol/tests/test_messages.py`: add `Correction` and `Vocabulary` to the import from `swarmscribe_protocol`, and in `_claim()` replace the `glossary=[...]` argument with:

```python
        vocabulary=Vocabulary(
            version=2,
            terms=["Ashford"],
            corrections=[Correction(heard="ash ford", replacement="Ashford")],
        ),
```

Add at the end of `test_messages.py`:

```python
def test_claim_carries_the_vocabulary_and_no_glossary():
    claim = _claim()
    assert claim.vocabulary.version == 2
    assert claim.vocabulary.corrections[0].replacement == "Ashford"
    assert "glossary" not in ClaimResponse.model_fields
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/protocol -q`
Expected: collection errors with `ImportError: cannot import name 'AppliedCorrection'` (and `'Correction'`) `from 'swarmscribe_protocol'`.

- [ ] **Step 3: Write the implementation**

`packages/protocol/src/swarmscribe_protocol/vocabulary.py`:

```python
from pydantic import Field

from .base import WireModel


class Correction(WireModel):
    """A fix applied after transcription: text heard as `heard` becomes `replacement`."""

    heard: str = Field(min_length=1)
    replacement: str = Field(min_length=1)


class Vocabulary(WireModel):
    """Words to recognise and fixes to apply. Version 0 means no vocabulary."""

    version: int = Field(ge=0)
    terms: list[str] = Field(default_factory=list)
    corrections: list[Correction] = Field(default_factory=list)


class AppliedCorrection(WireModel):
    """A correction that fired in one recording, and how many times."""

    heard: str
    replacement: str
    count: int = Field(ge=1)
```

In `messages.py`: add `from .vocabulary import Vocabulary` to the imports, and in `ClaimResponse` replace the line `glossary: list[str]` with `vocabulary: Vocabulary`.

In `schema.py`: add `vocabulary` to the `from . import messages, segments` line, and add these three entries to `MODELS` directly after `segments.SegmentsDocument,`:

```python
    vocabulary.Correction,
    vocabulary.Vocabulary,
    vocabulary.AppliedCorrection,
```

In `__init__.py`: add `from .vocabulary import AppliedCorrection, Correction, Vocabulary` and add `"AppliedCorrection"`, `"Correction"`, `"Vocabulary"` to `__all__`, keeping it sorted.

- [ ] **Step 4: Regenerate the snapshot**

The wire format changed on purpose. `PROTOCOL_VERSION` stays `1` (Global Constraints).

Run: `uv run python -m swarmscribe_protocol.schema packages/protocol/tests/schema_v1.json`
Then confirm: the file contains a `"Vocabulary"` key, and the `"ClaimResponse"` entry lists `vocabulary` and no `glossary`.

- [ ] **Step 5: Run the tests and the linter**

Run: `uv run pytest -q` then `uv run ruff check .`
Expected: all pass (the whole repo, engine included); ruff clean.

- [ ] **Step 6: Commit**

```bash
git add packages/protocol
git commit -m "Protocol: carry a versioned vocabulary in the claim"
```

---

### Task 2: Engine — vocabulary types, bias-term selection and the corrections matcher

**Files:**
- Modify: `packages/engine/src/swarmscribe_engine/types.py`
- Create: `packages/engine/src/swarmscribe_engine/vocabulary.py`
- Modify: `packages/engine/src/swarmscribe_engine/__init__.py`
- Modify: `packages/engine/tests/test_device.py`
- Test: `packages/engine/tests/test_vocabulary.py`

**Interfaces:**
- Consumes: existing `Segment`, `Word` in `swarmscribe_engine.types`.
- Produces (importable from `swarmscribe_engine`):
  - `Correction(heard: str, replacement: str)` — frozen dataclass.
  - `Vocabulary(version: int = 0, terms: tuple[str, ...] = (), corrections: tuple[Correction, ...] = ())` — frozen dataclass.
  - `EMPTY_VOCABULARY = Vocabulary()`
  - `AppliedCorrection(heard: str, replacement: str, count: int)` — frozen dataclass.
  - `Word.original: str | None = None` — new last field.
  - `FIXED_SETTINGS` — read-only mapping `{"language": "en", "condition_on_previous_text": False, "vad_filter": True, "word_timestamps": True}`.
  - `TranscribeSettings.temperatures` is coerced to a tuple.
- In `swarmscribe_engine.vocabulary`:
  - `HOTWORDS_CHAR_BUDGET = 600`
  - `select_bias_terms(terms: Sequence[str], budget: int = HOTWORDS_CHAR_BUDGET) -> tuple[str, ...]`
  - `build_hotwords(terms_used: Sequence[str]) -> str | None`
  - `apply_corrections(segments: Sequence[Segment], corrections: Sequence[Correction]) -> tuple[tuple[Segment, ...], tuple[AppliedCorrection, ...]]`
- This task does not change `Transcript`, the transcriber, the writers or the CLI. The existing suite must stay green.

How matching works (needed to read the tests): each word from Whisper is a token such as `" jay"` or `" son,"`. A token splits into a prefix of non-word characters, a core, and a suffix of non-word characters. A rule's `heard` text is split on whitespace into cores. A rule matches at position `i` when the case-folded cores of consecutive tokens equal the rule's cores. The replacement token is `prefix of the first token + replacement + suffix of the last token`. Known limit: a core includes inner apostrophes, so `jay son` does not match `jay son's`; the operator adds a second rule for that.

- [ ] **Step 1: Write the failing tests**

`packages/engine/tests/test_vocabulary.py`:

```python
import pytest

from swarmscribe_engine import AppliedCorrection, Correction, Segment, Vocabulary, Word
from swarmscribe_engine.vocabulary import (
    HOTWORDS_CHAR_BUDGET,
    apply_corrections,
    build_hotwords,
    select_bias_terms,
)


def seg(*tokens, probabilities=None):
    """A segment whose words are `tokens`, one second each."""
    probabilities = probabilities or [0.9] * len(tokens)
    words = tuple(
        Word(start=float(i), end=float(i + 1), word=token, probability=probability)
        for i, (token, probability) in enumerate(zip(tokens, probabilities, strict=True))
    )
    return Segment(
        start=0.0, end=float(len(tokens)), text="".join(tokens).strip(), words=words
    )


def fix(heard, replacement):
    return Correction(heard=heard, replacement=replacement)


# --- vocabulary type -------------------------------------------------------


def test_default_vocabulary_is_empty_version_zero():
    vocabulary = Vocabulary()
    assert (vocabulary.version, vocabulary.terms, vocabulary.corrections) == (0, (), ())


def test_word_original_defaults_to_none():
    assert Word(start=0.0, end=1.0, word=" hi", probability=0.9).original is None


# --- bias term selection ---------------------------------------------------


def test_terms_are_stripped_and_blanks_dropped():
    assert select_bias_terms([" Ashford ", "", "   ", "José"]) == ("Ashford", "José")


def test_duplicate_terms_are_dropped_case_insensitively_keeping_the_first():
    assert select_bias_terms(["Ashford", "ASHFORD", "José", "ashford"]) == ("Ashford", "José")


def test_terms_that_exactly_fill_the_budget_are_kept():
    # "aaaa, bbbb" is exactly 10 characters
    assert select_bias_terms(["aaaa", "bbbb", "cc"], budget=10) == ("aaaa", "bbbb")


def test_selection_stops_at_the_first_term_that_does_not_fit():
    # "c" would fit, but priority order means we stop, never skip ahead
    assert select_bias_terms(["aaaa", "bbbbbbbbbb", "c"], budget=8) == ("aaaa",)


def test_a_very_large_vocabulary_is_cut_to_a_leading_run_within_the_budget():
    terms = [f"term{i:04d}" for i in range(1000)]
    selected = select_bias_terms(terms)
    assert 0 < len(selected) < len(terms)
    assert list(selected) == terms[: len(selected)]
    assert len(build_hotwords(selected)) <= HOTWORDS_CHAR_BUDGET


def test_a_first_term_longer_than_the_budget_selects_nothing():
    assert select_bias_terms(["x" * 20, "ok"], budget=10) == ()


def test_build_hotwords_joins_with_comma_space():
    assert build_hotwords(("Ashford", "José")) == "Ashford, José"


def test_build_hotwords_is_none_when_there_are_no_terms():
    assert build_hotwords(()) is None


# --- corrections -----------------------------------------------------------


def test_no_corrections_returns_segments_unchanged():
    segments = (seg(" Welcome", " to", " Ashford."),)
    corrected, applied = apply_corrections(segments, ())
    assert corrected == segments
    assert applied == ()


def test_single_word_is_corrected_case_insensitively_keeping_punctuation():
    (segment,), applied = apply_corrections(
        [seg(" Welcome", " to", " ASHFERD.")], [fix("ashferd", "Ashford")]
    )
    assert segment.text == "Welcome to Ashford."
    assert segment.words[2].word == " Ashford."
    assert segment.words[2].original == " ASHFERD."
    assert applied == (AppliedCorrection(heard="ashferd", replacement="Ashford", count=1),)


def test_uncorrected_words_are_left_exactly_as_they_were():
    original = seg(" Welcome", " to", " ashferd.")
    (segment,), _ = apply_corrections([original], [fix("ashferd", "Ashford")])
    assert segment.words[0] is original.words[0]
    assert segment.words[1].original is None


def test_multi_word_match_merges_into_one_word():
    (segment,), applied = apply_corrections(
        [seg(" jay", " son,", " hello", probabilities=[0.9, 0.4, 0.8])],
        [fix("jay son", "Jason")],
    )
    assert segment.text == "Jason, hello"
    assert len(segment.words) == 2
    merged = segment.words[0]
    assert merged.word == " Jason,"
    assert merged.original == " jay son,"
    assert (merged.start, merged.end) == (0.0, 2.0)
    assert merged.probability == 0.4
    assert applied[0].count == 1


def test_a_correction_never_fires_inside_a_longer_word():
    original = seg(" the", " party", " started")
    (segment,), applied = apply_corrections([original], [fix("art", "ART")])
    assert segment is original
    assert applied == ()


def test_a_multi_word_rule_does_not_match_across_a_partial_word():
    original = seg(" jay", " sonny")
    (segment,), applied = apply_corrections([original], [fix("jay son", "Jason")])
    assert segment is original
    assert applied == ()


def test_the_longest_rule_wins():
    (segment,), applied = apply_corrections(
        [seg(" new", " york", " new")],
        [fix("new", "NEW"), fix("new york", "NYC")],
    )
    assert segment.text == "NYC NEW"
    assert applied == (
        AppliedCorrection(heard="new", replacement="NEW", count=1),
        AppliedCorrection(heard="new york", replacement="NYC", count=1),
    )


def test_output_is_never_corrected_again():
    (segment,), applied = apply_corrections([seg(" a", " b")], [fix("a b", "c"), fix("c", "d")])
    assert segment.text == "c"
    assert applied == (AppliedCorrection(heard="a b", replacement="c", count=1),)


def test_a_rule_that_changes_nothing_is_not_counted():
    original = seg(" Ashford")
    (segment,), applied = apply_corrections([original], [fix("ashford", "Ashford")])
    assert segment is original
    assert applied == ()


def test_counts_add_up_across_segments_in_rule_order():
    segments = [seg(" jay", " son"), seg(" ashferd", " and", " jay", " son")]
    _, applied = apply_corrections(
        segments, [fix("ashferd", "Ashford"), fix("jay son", "Jason")]
    )
    assert applied == (
        AppliedCorrection(heard="ashferd", replacement="Ashford", count=1),
        AppliedCorrection(heard="jay son", replacement="Jason", count=2),
    )


def test_non_ascii_replacement():
    (segment,), _ = apply_corrections([seg(" Jose", " spoke")], [fix("jose", "José")])
    assert segment.text == "José spoke"


def test_leading_quote_and_trailing_punctuation_survive():
    (segment,), _ = apply_corrections([seg(' "ashferd!"')], [fix("ashferd", "Ashford")])
    assert segment.words[0].word == ' "Ashford!"'


def test_segment_without_words_is_corrected_in_its_text():
    plain = Segment(start=0.0, end=2.0, text="we met jay son, today", words=())
    (segment,), applied = apply_corrections([plain], [fix("jay son", "Jason")])
    assert segment.text == "we met Jason, today"
    assert segment.words == ()
    assert applied[0].count == 1


@pytest.mark.parametrize(
    "rule",
    [fix("", "Jason"), fix("   ", "Jason"), fix("jay", "  "), fix("...", "x")],
)
def test_rules_with_nothing_to_match_or_nothing_to_write_are_ignored(rule):
    original = seg(" jay", " son")
    (segment,), applied = apply_corrections([original], [rule])
    assert segment is original
    assert applied == ()
```

Add to the end of `packages/engine/tests/test_device.py`:

```python
def test_a_list_ladder_is_coerced_to_a_tuple_so_settings_stay_hashable():
    settings = TranscribeSettings(
        model="large-v3", compute_type="float16", device="cuda", temperatures=[0.0, 0.2]
    )
    assert settings.temperatures == (0.0, 0.2)
    hash(settings)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/engine/tests/test_vocabulary.py packages/engine/tests/test_device.py -q`
Expected: `ImportError: cannot import name 'AppliedCorrection' from 'swarmscribe_engine'`, and the new `test_device.py` test fails with `TypeError: unhashable type: 'list'`.

- [ ] **Step 3: Extend the types**

In `packages/engine/src/swarmscribe_engine/types.py`:

Add `from types import MappingProxyType` to the imports.

Directly after the `MAX_TEMPERATURE = 0.4` line add:

```python
# Passed to the model on every call and recorded in segments.json. Not configurable.
FIXED_SETTINGS = MappingProxyType(
    {
        "language": LANGUAGE,
        "condition_on_previous_text": False,
        "vad_filter": True,
        "word_timestamps": True,
    }
)
```

In `TranscribeSettings.__post_init__`, make this the first line of the method body:

```python
        object.__setattr__(self, "temperatures", tuple(self.temperatures))
```

Replace the `Word` dataclass with:

```python
@dataclass(frozen=True)
class Word:
    start: float
    end: float
    word: str
    probability: float
    original: str | None = None
```

Directly before the `Transcript` dataclass add:

```python
@dataclass(frozen=True)
class Correction:
    heard: str
    replacement: str


@dataclass(frozen=True)
class Vocabulary:
    """Words to recognise and fixes to apply. Version 0 means no vocabulary."""

    version: int = 0
    terms: tuple[str, ...] = ()
    corrections: tuple[Correction, ...] = ()


EMPTY_VOCABULARY = Vocabulary()


@dataclass(frozen=True)
class AppliedCorrection:
    heard: str
    replacement: str
    count: int
```

- [ ] **Step 4: Write the vocabulary module**

`packages/engine/src/swarmscribe_engine/vocabulary.py`:

```python
"""Bias-term selection and post-transcription corrections. Pure functions, no model."""

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import NamedTuple

from .types import AppliedCorrection, Correction, Segment, Word

# faster-whisper truncates hotwords to about 220 tokens; 600 characters stays inside that.
HOTWORDS_CHAR_BUDGET = 600

_SEPARATOR = ", "
# prefix of non-word characters, core, suffix of non-word characters
_EDGES = re.compile(r"^(\W*)(.*?)(\W*)$", re.DOTALL)
_TEXT_TOKENS = re.compile(r"\s*\S+")


def select_bias_terms(
    terms: Sequence[str], budget: int = HOTWORDS_CHAR_BUDGET
) -> tuple[str, ...]:
    """Leading terms, in order, that fit the budget once joined. Stops at the first misfit."""
    selected: list[str] = []
    seen: set[str] = set()
    used = 0
    for term in terms:
        cleaned = term.strip()
        key = cleaned.casefold()
        if not cleaned or key in seen:
            continue
        cost = len(cleaned) + (len(_SEPARATOR) if selected else 0)
        if used + cost > budget:
            break
        seen.add(key)
        selected.append(cleaned)
        used += cost
    return tuple(selected)


def build_hotwords(terms_used: Sequence[str]) -> str | None:
    return _SEPARATOR.join(terms_used) if terms_used else None


@dataclass(frozen=True)
class _Rule:
    index: int
    cores: tuple[str, ...]
    replacement: str


class _Piece(NamedTuple):
    first: int
    last: int
    text: str
    rule: _Rule | None


def _split(token: str) -> tuple[str, str, str]:
    prefix, core, suffix = _EDGES.match(token).groups()
    return prefix, core, suffix


def _core(token: str) -> str:
    return _split(token)[1].casefold()


def _compile(corrections: Sequence[Correction]) -> list[_Rule]:
    rules = []
    for index, correction in enumerate(corrections):
        cores = tuple(core for core in map(_core, correction.heard.split()) if core)
        replacement = correction.replacement.strip()
        if cores and replacement:
            rules.append(_Rule(index=index, cores=cores, replacement=replacement))
    # Longest first: most tokens, then most characters, then file order.
    rules.sort(key=lambda r: (-len(r.cores), -sum(map(len, r.cores)), r.index))
    return rules


def _correct(tokens: Sequence[str], rules: Sequence[_Rule]) -> list[_Piece]:
    cores = [_core(token) for token in tokens]
    pieces: list[_Piece] = []
    i = 0
    while i < len(tokens):
        piece = _Piece(first=i, last=i, text=tokens[i], rule=None)
        for rule in rules:
            last = i + len(rule.cores) - 1
            if tuple(cores[i : last + 1]) != rule.cores:
                continue
            text = _split(tokens[i])[0] + rule.replacement + _split(tokens[last])[2]
            if text != "".join(tokens[i : last + 1]):
                piece = _Piece(first=i, last=last, text=text, rule=rule)
                break
        pieces.append(piece)
        i = piece.last + 1
    return pieces


def _merge(group: Sequence[Word], piece: _Piece) -> Word:
    if piece.rule is None:
        return group[0]
    return Word(
        start=group[0].start,
        end=group[-1].end,
        word=piece.text,
        probability=min(word.probability for word in group),
        original="".join(word.word for word in group),
    )


def _correct_segment(segment: Segment, rules: Sequence[_Rule], counts: dict[int, int]) -> Segment:
    if segment.words:
        tokens = [word.word for word in segment.words]
    else:
        tokens = _TEXT_TOKENS.findall(segment.text)
    pieces = _correct(tokens, rules)
    fired = [piece for piece in pieces if piece.rule is not None]
    if not fired:
        return segment
    for piece in fired:
        counts[piece.rule.index] = counts.get(piece.rule.index, 0) + 1
    if not segment.words:
        return replace(segment, text="".join(piece.text for piece in pieces).strip())
    words = tuple(_merge(segment.words[piece.first : piece.last + 1], piece) for piece in pieces)
    return replace(segment, text="".join(word.word for word in words).strip(), words=words)


def apply_corrections(
    segments: Sequence[Segment], corrections: Sequence[Correction]
) -> tuple[tuple[Segment, ...], tuple[AppliedCorrection, ...]]:
    """Apply each correction as a whole-word, case-insensitive match, longest first."""
    rules = _compile(corrections)
    if not rules:
        return tuple(segments), ()
    counts: dict[int, int] = {}
    corrected = tuple(_correct_segment(segment, rules, counts) for segment in segments)
    applied = tuple(
        AppliedCorrection(
            heard=correction.heard.strip(),
            replacement=correction.replacement.strip(),
            count=counts[index],
        )
        for index, correction in enumerate(corrections)
        if counts.get(index)
    )
    return corrected, applied
```

In `packages/engine/src/swarmscribe_engine/__init__.py`: add `EMPTY_VOCABULARY`, `FIXED_SETTINGS`, `AppliedCorrection`, `Correction`, `Vocabulary` to the import from `.types`, and add the same five names to `__all__`, keeping it sorted. Do not re-export the functions in `vocabulary.py`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest -q` then `uv run ruff check .`
Expected: all pass (whole repo); ruff clean.

- [ ] **Step 6: Commit**

```bash
git add packages/engine
git commit -m "Engine: vocabulary types, bias-term selection and corrections matcher"
```

---

### Task 3: Apply and record the vocabulary end to end

This task changes the shape of `Transcript` and of `segments.json`, so the engine and the protocol schema change together and the task ends with the whole repo green.

**Files:**
- Modify: `packages/engine/src/swarmscribe_engine/types.py` (`Transcript`)
- Modify: `packages/engine/src/swarmscribe_engine/transcriber.py`, `writers.py`, `cli.py`
- Modify: `packages/protocol/src/swarmscribe_protocol/segments.py`
- Regenerate: `packages/protocol/tests/schema_v1.json`
- Modify tests: `packages/engine/tests/conftest.py`, `test_transcriber.py`, `test_writers.py`, `test_cli.py`, `test_smoke.py`; `packages/protocol/tests/test_segments.py`

**Interfaces:**
- Consumes: `Vocabulary`, `EMPTY_VOCABULARY`, `AppliedCorrection`, `FIXED_SETTINGS`, `Word.original` (Task 2); `select_bias_terms`, `build_hotwords`, `apply_corrections` from `swarmscribe_engine.vocabulary` (Task 2); protocol `AppliedCorrection` (Task 1).
- Produces:
  - `Transcript(source_name, source_checksum, duration, settings, vocabulary_version: int, vocabulary_terms_used: tuple[str, ...], corrections_applied: tuple[AppliedCorrection, ...], segments)` — the `glossary` field is gone.
  - `Transcriber.transcribe(path: Path, vocabulary: Vocabulary = EMPTY_VOCABULARY) -> Transcript`
  - `transcribe(path: Path, settings: TranscribeSettings, vocabulary: Vocabulary = EMPTY_VOCABULARY) -> Transcript`
  - `build_prompt` is deleted. The model is called with `hotwords=…` and without `initial_prompt`.
  - Protocol `Word.original: str | None = None`; `SegmentsDocument` drops `glossary` and gains `vocabulary_version: int` (`>= 0`), `vocabulary_terms_used: list[str]`, `corrections_applied: list[AppliedCorrection]`.
  - `segments.json`: top-level `vocabulary_version`, `vocabulary_terms_used`, `corrections_applied` (each `heard`, `replacement`, `count`); a word has an `original` key only when it was corrected.
  - The CLI still accepts `--glossary FILE` in this task and passes `Vocabulary(terms=…)`; Task 4 replaces the flag.

- [ ] **Step 1: Update the shared fixture**

In `packages/engine/tests/conftest.py`, inside `make_transcript`'s `values` dict, replace the `"glossary": (...)` entry with:

```python
            "vocabulary_version": 1,
            "vocabulary_terms_used": ("Ashford",),
            "corrections_applied": (),
```

- [ ] **Step 2: Update and add the failing tests**

**`packages/engine/tests/test_transcriber.py`**

Change the imports to:

```python
from swarmscribe_engine import (
    AppliedCorrection,
    Correction,
    TranscribeSettings,
    Transcriber,
    UndecodableAudioError,
    Vocabulary,
)
from swarmscribe_engine.transcriber import sha256_file
```

Delete `test_build_prompt` and `test_blank_glossary_terms_are_not_recorded`.

Replace `test_fixed_settings_are_passed_to_the_model` with:

```python
def test_fixed_settings_and_hotwords_are_passed_to_the_model(audio):
    model = FakeModel()
    make_transcriber(model).transcribe(audio, Vocabulary(version=2, terms=("Ashford", "José")))
    path_arg, kwargs = model.calls[0]
    assert path_arg == str(audio)
    assert kwargs == {
        "language": "en",
        "condition_on_previous_text": False,
        "temperature": [0.0, 0.2, 0.4],
        "vad_filter": True,
        "word_timestamps": True,
        "hotwords": "Ashford, José",
    }
```

In `test_segments_and_words_are_converted`: change the call to `.transcribe(audio, Vocabulary(version=4, terms=("Ashford",)))` and replace the `assert transcript.glossary == (...)` line with:

```python
    assert transcript.vocabulary_version == 4
    assert transcript.vocabulary_terms_used == ("Ashford",)
    assert transcript.corrections_applied == ()
```

Add:

```python
def test_without_a_vocabulary_no_hotwords_are_sent(audio):
    model = FakeModel()
    transcript = make_transcriber(model).transcribe(audio)
    assert model.calls[0][1]["hotwords"] is None
    assert "initial_prompt" not in model.calls[0][1]
    assert transcript.vocabulary_version == 0
    assert transcript.vocabulary_terms_used == ()


def test_only_terms_within_the_budget_are_sent_and_recorded(audio):
    model = FakeModel()
    terms = tuple(f"term{i:04d}" for i in range(1000))
    transcript = make_transcriber(model).transcribe(audio, Vocabulary(version=1, terms=terms))
    used = transcript.vocabulary_terms_used
    assert 0 < len(used) < len(terms)
    assert used == terms[: len(used)]
    assert model.calls[0][1]["hotwords"] == ", ".join(used)


def test_corrections_are_applied_to_the_model_output(audio):
    model = FakeModel(
        segments=[
            raw_segment(
                0.0,
                2.0,
                " Thanks jay son.",
                [
                    raw_word(0.0, 0.5, " Thanks", 0.9),
                    raw_word(0.5, 1.0, " jay", 0.8),
                    raw_word(1.0, 2.0, " son.", 0.3),
                ],
            )
        ],
        duration=2.0,
    )
    vocabulary = Vocabulary(version=5, corrections=(Correction("jay son", "Jason"),))
    transcript = make_transcriber(model).transcribe(audio, vocabulary)
    (segment,) = transcript.segments
    assert segment.text == "Thanks Jason."
    assert segment.words[1].word == " Jason."
    assert segment.words[1].original == " jay son."
    assert segment.words[1].probability == 0.3
    assert transcript.corrections_applied == (
        AppliedCorrection(heard="jay son", replacement="Jason", count=1),
    )
```

**`packages/engine/tests/test_writers.py`**

Add `AppliedCorrection` and `Word` to the import from `swarmscribe_engine`.

In `test_segments_json_conforms_to_the_protocol_schema`, replace the `assert document.glossary == [...]` line with:

```python
    assert document.vocabulary_version == 1
    assert document.vocabulary_terms_used == ["Ashford"]
    assert document.corrections_applied == []
```

In `test_non_ascii_text_is_written_as_utf8`, replace the `glossary=("José",)` argument with `vocabulary_terms_used=("José",)`.

Add:

```python
def test_corrections_and_originals_are_recorded(make_transcript, tmp_path):
    corrected = Segment(
        start=0.0,
        end=2.0,
        text="Thanks Jason.",
        words=(
            Word(start=0.0, end=0.5, word=" Thanks", probability=0.9),
            Word(start=0.5, end=2.0, word=" Jason.", probability=0.3, original=" jay son."),
        ),
    )
    transcript = make_transcript(
        segments=(corrected,),
        vocabulary_version=5,
        corrections_applied=(AppliedCorrection(heard="jay son", replacement="Jason", count=1),),
    )
    files = write_outputs(transcript, tmp_path)

    raw = json.loads(files.segments_json.read_text("utf-8"))
    assert raw["vocabulary_version"] == 5
    assert raw["corrections_applied"] == [{"heard": "jay son", "replacement": "Jason", "count": 1}]
    first, second = raw["segments"][0]["words"]
    assert "original" not in first
    assert second["original"] == " jay son."
    assert "glossary" not in raw

    document = SegmentsDocument.model_validate_json(files.segments_json.read_text("utf-8"))
    assert document.segments[0].words[0].original is None
    assert document.segments[0].words[1].original == " jay son."
    assert files.txt.read_text("utf-8") == "Thanks Jason.\n"


def test_a_non_finite_number_is_an_error_not_invalid_json(make_transcript, tmp_path):
    with pytest.raises(ValueError):
        write_outputs(make_transcript(duration=float("nan")), tmp_path)
    assert not (tmp_path / "recording.mp3.segments.json").exists()
```

**`packages/engine/tests/test_cli.py`**

Add `Vocabulary` to the import from `swarmscribe_engine`. In every fake `transcribe_fn`, the third argument is now a `Vocabulary`:
- where a test asserts the recorded call's third element is `()`, assert `Vocabulary()` instead;
- in `test_glossary_file_is_read_and_passed`, change the final assertion to `assert seen == [Vocabulary(terms=("José", "Ashford"))]`.

**`packages/engine/tests/test_smoke.py`**

Add `Correction` and `Vocabulary` to the import from `swarmscribe_engine`. In `test_real_model_accepts_the_fixed_settings_and_outputs_validate`, replace the `transcriber.transcribe(tone, glossary=[...])` call with:

```python
    vocabulary = Vocabulary(
        version=3, terms=("Ashford",), corrections=(Correction("ash ford", "Ashford"),)
    )
    transcript = transcriber.transcribe(tone, vocabulary)
```

and add after the existing `document.settings.model` assertion:

```python
    assert document.vocabulary_version == 3
    assert document.vocabulary_terms_used == ["Ashford"]
```

**`packages/protocol/tests/test_segments.py`**

Add `AppliedCorrection` to the import from `swarmscribe_protocol`. In every `SegmentsDocument(...)` construction replace the `glossary=[...]` argument with:

```python
        vocabulary_version=0,
        vocabulary_terms_used=[],
        corrections_applied=[],
```

In `test_segments_document_round_trips_through_json` use instead:

```python
        vocabulary_version=3,
        vocabulary_terms_used=["Ashford", "José"],
        corrections_applied=[AppliedCorrection(heard="jay son", replacement="Jason", count=2)],
```

and give its one `Word` an `original=" welcom"` argument.

Add:

```python
def test_word_original_is_optional():
    assert Word(start=0.0, end=0.5, word=" hello", probability=1.0).original is None


def test_segments_document_has_no_glossary_field():
    assert "glossary" not in SegmentsDocument.model_fields


def test_vocabulary_version_cannot_be_negative():
    with pytest.raises(ValidationError):
        SegmentsDocument(
            schema_version=1,
            source_checksum="a" * 64,
            duration=3.0,
            device="cpu",
            engine_version="0.1.0",
            settings=_settings(),
            vocabulary_version=-1,
            vocabulary_terms_used=[],
            corrections_applied=[],
            segments=[],
        )
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest -q`
Expected: many failures, including `TypeError: Transcript.__init__() got an unexpected keyword argument 'vocabulary_version'` and Pydantic errors for the unknown `SegmentsDocument` fields. Record the summary line.

- [ ] **Step 4: Change the protocol schema**

In `packages/protocol/src/swarmscribe_protocol/segments.py`:

Add `from .vocabulary import AppliedCorrection` to the imports.

Replace `Word` with:

```python
class Word(WireModel):
    start: float
    end: float
    word: str
    probability: float = Field(ge=0.0, le=1.0)
    original: str | None = None
```

In `SegmentsDocument`, replace the line `glossary: list[str]` with:

```python
    vocabulary_version: int = Field(ge=0)
    vocabulary_terms_used: list[str]
    corrections_applied: list[AppliedCorrection]
```

Regenerate the snapshot: `uv run python -m swarmscribe_protocol.schema packages/protocol/tests/schema_v1.json`

- [ ] **Step 5: Change `Transcript`**

In `packages/engine/src/swarmscribe_engine/types.py`, replace the `Transcript` dataclass with (it must come after `AppliedCorrection`; move it below if needed):

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
```

- [ ] **Step 6: Change the transcriber**

In `packages/engine/src/swarmscribe_engine/transcriber.py`:

Delete the functions `_clean_terms` and `build_prompt`, and remove `Sequence` from the imports if it is no longer used.

Change the import from `.types` to:

```python
from .types import (
    EMPTY_VOCABULARY,
    FIXED_SETTINGS,
    Segment,
    Transcript,
    TranscribeSettings,
    UndecodableAudioError,
    Vocabulary,
    Word,
)
from .vocabulary import apply_corrections, build_hotwords, select_bias_terms
```

Replace the `Transcriber.transcribe` method with:

```python
    def transcribe(self, path: Path, vocabulary: Vocabulary = EMPTY_VOCABULARY) -> Transcript:
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(f"recording not found: {path}")
        terms_used = select_bias_terms(vocabulary.terms)
        try:
            raw_segments, info = self._model.transcribe(
                str(path),
                **FIXED_SETTINGS,
                temperature=list(self.settings.temperatures),
                hotwords=build_hotwords(terms_used),
            )
            segments = tuple(
                segment for segment in (_convert(raw) for raw in raw_segments) if segment.text
            )
        except self._decode_errors as exc:
            raise UndecodableAudioError(f"cannot decode {path.name}: {exc}") from exc
        segments, applied = apply_corrections(segments, vocabulary.corrections)
        return Transcript(
            source_name=path.name,
            source_checksum=sha256_file(path),
            duration=float(info.duration),
            settings=self.settings,
            vocabulary_version=vocabulary.version,
            vocabulary_terms_used=terms_used,
            corrections_applied=applied,
            segments=segments,
        )
```

Replace the module-level `transcribe` function with:

```python
def transcribe(
    path: Path, settings: TranscribeSettings, vocabulary: Vocabulary = EMPTY_VOCABULARY
) -> Transcript:
    """Transcribe one file, reusing the model already loaded for these settings."""
    return _shared_transcriber(settings).transcribe(path, vocabulary)
```

- [ ] **Step 7: Change the writers**

In `packages/engine/src/swarmscribe_engine/writers.py`:

Change the import from `.types` to `from .types import FIXED_SETTINGS, OutputFiles, Transcript, Word`.

Add this function above `render_segments_json`:

```python
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
```

Replace `render_segments_json` with:

```python
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
            **FIXED_SETTINGS,
            "temperatures": list(settings.temperatures),
        },
        "vocabulary_version": transcript.vocabulary_version,
        "vocabulary_terms_used": list(transcript.vocabulary_terms_used),
        "corrections_applied": [
            {"heard": applied.heard, "replacement": applied.replacement, "count": applied.count}
            for applied in transcript.corrections_applied
        ],
        "segments": [
            {
                "start": segment.start,
                "end": segment.end,
                "text": segment.text,
                "words": [_word_json(word) for word in segment.words],
            }
            for segment in transcript.segments
        ],
    }
    return json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
```

In `write_outputs`, render all three texts before writing any file, so a rendering error leaves nothing behind. Replace the three `_atomic_write` lines with:

```python
    txt, srt, segments_json = (
        render_txt(transcript),
        render_srt(transcript),
        render_segments_json(transcript),
    )
    _atomic_write(files.txt, txt)
    _atomic_write(files.srt, srt)
    _atomic_write(files.segments_json, segments_json)
```

- [ ] **Step 8: Adapt the CLI to the new signature**

In `packages/engine/src/swarmscribe_engine/cli.py`:

Add `Vocabulary` to the import from `.types`, and change the `TranscribeFn` alias to `Callable[[Path, TranscribeSettings, Vocabulary], Transcript]`.

In `run`, replace the two lines that build `glossary` and call `transcribe_fn` with:

```python
        terms = read_glossary(args.glossary) if args.glossary else ()
        transcript = transcribe_fn(args.file, settings, Vocabulary(terms=terms))
```

- [ ] **Step 9: Run everything**

Run, in order:
- `uv run pytest -q` — expected: all pass.
- `uv run pytest -m smoke -q` — expected: 2 passed. This proves the real model accepts `hotwords` together with the fixed settings.
- `uv run ruff check .` — expected: clean.
- `grep -rn "glossary" packages --include=*.py | grep -v "cli.py\|test_cli.py"` — expected: no output (the CLI flag goes in Task 4).

- [ ] **Step 10: Commit**

```bash
git add packages
git commit -m "Apply the vocabulary in the engine and record it in segments.json"
```

---

### Task 4: CLI — `--vocabulary` and `--corrections`, README

**Files:**
- Modify: `packages/engine/src/swarmscribe_engine/cli.py`
- Modify: `packages/engine/tests/test_cli.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `Vocabulary`, `Correction` from `swarmscribe_engine.types`; `transcribe(path, settings, vocabulary)` (Task 3).
- Produces:
  - `swarmscribe-engine FILE --out DIR [--vocabulary FILE] [--corrections FILE] [--device …] [--model …] [--compute-type …]`. The `--glossary` flag is removed.
  - `swarmscribe_engine.cli.read_terms(path: Path) -> tuple[str, ...]` — replaces `read_glossary`; same behaviour (one term per line, blank lines and `#` comments ignored, tolerates a UTF-8 BOM and CRLF).
  - `swarmscribe_engine.cli.read_corrections(path: Path) -> tuple[Correction, ...]` — one `heard as => should be` per line; blank lines and `#` comments ignored; BOM and CRLF tolerated; raises `ValueError("<file name> line <n>: expected 'heard as => should be'")` for a line with no `=>` or with an empty side.
  - The CLI passes `Vocabulary(version=0, terms=…, corrections=…)`: a local run has no leader-assigned version.

- [ ] **Step 1: Update and add the failing tests**

In `packages/engine/tests/test_cli.py`:

Change the import from `swarmscribe_engine.cli` to `from swarmscribe_engine.cli import read_corrections, read_terms, run`, and add `Correction` to the import from `swarmscribe_engine`.

Rename the flag and the reader throughout the file: every `"--glossary"` argument becomes `"--vocabulary"`, every `read_glossary` call becomes `read_terms`, and test names containing `glossary` are renamed to say `vocabulary` (for example `test_vocabulary_file_is_read_and_passed`, `test_vocabulary_saved_by_notepad_with_a_bom_is_read_cleanly`, `test_missing_vocabulary_file_exits_2_with_a_message`). Variable names and file names such as `glossary.txt` become `vocabulary.txt`. Assertions stay as they are.

Add:

```python
def test_corrections_file_is_read_and_passed(tmp_path, make_transcript):
    corrections = tmp_path / "corrections.txt"
    corrections.write_text(
        "# names\njay son => Jason\n\n  ashferd=>Ashford  \n", encoding="utf-8"
    )
    seen = []

    def fake_transcribe(path, settings, vocabulary):
        seen.append(vocabulary)
        return make_transcript()

    code = run(
        [str(_recording(tmp_path)), "--out", str(tmp_path), "--corrections", str(corrections)],
        transcribe_fn=fake_transcribe,
        resolve_fn=lambda preference: CPU,
    )
    assert code == 0
    assert seen == [
        Vocabulary(
            corrections=(Correction("jay son", "Jason"), Correction("ashferd", "Ashford"))
        )
    ]


def test_vocabulary_and_corrections_can_be_given_together(tmp_path, make_transcript):
    vocabulary = tmp_path / "vocabulary.txt"
    vocabulary.write_text("Ashford\n", encoding="utf-8")
    corrections = tmp_path / "corrections.txt"
    corrections.write_text("ashferd => Ashford\n", encoding="utf-8")
    seen = []

    def fake_transcribe(path, settings, vocab):
        seen.append(vocab)
        return make_transcript()

    run(
        [
            str(_recording(tmp_path)),
            "--out",
            str(tmp_path),
            "--vocabulary",
            str(vocabulary),
            "--corrections",
            str(corrections),
        ],
        transcribe_fn=fake_transcribe,
        resolve_fn=lambda preference: CPU,
    )
    assert seen == [
        Vocabulary(version=0, terms=("Ashford",), corrections=(Correction("ashferd", "Ashford"),))
    ]


def test_corrections_saved_by_notepad_with_a_bom_and_crlf_are_read_cleanly(tmp_path):
    corrections = tmp_path / "corrections.txt"
    corrections.write_bytes(b"\xef\xbb\xbfjose => Jos\xc3\xa9\r\njay son => Jason\r\n")
    assert read_corrections(corrections) == (
        Correction("jose", "José"),
        Correction("jay son", "Jason"),
    )


def test_a_replacement_may_itself_contain_an_arrow(tmp_path):
    corrections = tmp_path / "corrections.txt"
    corrections.write_text("a to b => a => b\n", encoding="utf-8")
    assert read_corrections(corrections) == (Correction("a to b", "a => b"),)


@pytest.mark.parametrize("bad_line", ["jay son Jason", "=> Jason", "jay son =>", "   =>   "])
def test_a_malformed_corrections_line_names_the_file_and_line(tmp_path, bad_line):
    corrections = tmp_path / "corrections.txt"
    corrections.write_text(f"# header\nashferd => Ashford\n{bad_line}\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"corrections\.txt line 3"):
        read_corrections(corrections)


def test_a_malformed_corrections_file_exits_2_without_a_traceback(
    tmp_path, make_transcript, capsys
):
    corrections = tmp_path / "corrections.txt"
    corrections.write_text("jay son Jason\n", encoding="utf-8")
    code = run(
        [str(_recording(tmp_path)), "--out", str(tmp_path), "--corrections", str(corrections)],
        transcribe_fn=lambda path, settings, vocabulary: make_transcript(),
        resolve_fn=lambda preference: CPU,
    )
    err = capsys.readouterr().err
    assert code == 2
    assert err.startswith("error: corrections.txt line 1")
    assert "Traceback" not in err


def test_the_glossary_flag_is_gone(tmp_path):
    with pytest.raises(SystemExit):
        run(
            [str(_recording(tmp_path)), "--out", str(tmp_path), "--glossary", "x.txt"],
            transcribe_fn=None,
            resolve_fn=lambda preference: CPU,
        )
```

Make sure `import pytest` is present at the top of the file.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/engine/tests/test_cli.py -q`
Expected: `ImportError: cannot import name 'read_corrections' from 'swarmscribe_engine.cli'`

- [ ] **Step 3: Write the implementation**

In `packages/engine/src/swarmscribe_engine/cli.py`:

Add `Correction` to the import from `.types`.

Replace `read_glossary` with these two functions:

```python
def _content_lines(path: Path) -> list[tuple[int, str]]:
    """Numbered, stripped lines of a UTF-8 text file, without blanks and # comments."""
    lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
    return [
        (number, line.strip())
        for number, line in enumerate(lines, start=1)
        if line.strip() and not line.strip().startswith("#")
    ]


def read_terms(path: Path) -> tuple[str, ...]:
    return tuple(line for _, line in _content_lines(path))


def read_corrections(path: Path) -> tuple[Correction, ...]:
    corrections = []
    for number, line in _content_lines(path):
        heard, arrow, replacement = line.partition("=>")
        heard, replacement = heard.strip(), replacement.strip()
        if not arrow or not heard or not replacement:
            raise ValueError(
                f"{Path(path).name} line {number}: expected 'heard as => should be'"
            )
        corrections.append(Correction(heard=heard, replacement=replacement))
    return tuple(corrections)
```

In `_parser`, replace the `--glossary` argument with:

```python
    parser.add_argument(
        "--vocabulary", type=Path, help="text file, one word, name or phrase per line"
    )
    parser.add_argument(
        "--corrections", type=Path, help="text file, one 'heard as => should be' per line"
    )
```

In `run`, replace the two lines that build `terms` and call `transcribe_fn` with:

```python
        vocabulary = Vocabulary(
            terms=read_terms(args.vocabulary) if args.vocabulary else (),
            corrections=read_corrections(args.corrections) if args.corrections else (),
        )
        transcript = transcribe_fn(args.file, settings, vocabulary)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q` then `uv run ruff check .`
Expected: all pass; ruff clean.

- [ ] **Step 5: Run the CLI by hand against the real model**

```bash
uv run python -c "import math,struct,wave; w=wave.open('tone.wav','wb'); w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b''.join(struct.pack('<h', int(8000*math.sin(2*math.pi*440*i/16000))) for i in range(48000))); w.close()"
printf 'Ashford\nJason\n' > vocabulary.txt
printf 'jay son => Jason\n' > corrections.txt
uv run swarmscribe-engine tone.wav --out out --device cpu --model tiny.en --vocabulary vocabulary.txt --corrections corrections.txt
```

Expected: three paths printed, exit code 0, and `out/tone.wav.segments.json` contains `"vocabulary_version": 0` and `"vocabulary_terms_used": ["Ashford", "Jason"]`. Then delete `tone.wav`, `vocabulary.txt`, `corrections.txt` and `out/`; none may be committed.

- [ ] **Step 6: Update the README**

In `README.md`, in the "Transcribe one file" section:

Replace the example command with:

```
uv sync
uv run swarmscribe-engine recording.mp3 --out out --vocabulary vocabulary.txt --corrections corrections.txt
```

Replace the bullet describing `--glossary` with these two bullets and the paragraph after them:

```markdown
- `--vocabulary` — a text file with one word, name or phrase per line. `#`
  starts a comment. These bias recognition throughout the recording. Order is
  priority: roughly the first 600 characters of terms are used, so put the
  ones that matter most at the top.
- `--corrections` — a text file with one fix per line, written
  `heard as => should be`, for example `jay son => Jason`. Applied after
  transcription as whole-word, case-insensitive replacements.

`segments.json` records which terms were used and every correction that
fired, and keeps the original text of each corrected word.
```

Confirm no `glossary` remains: `grep -rni "glossary" README.md packages` — expected: no output.

- [ ] **Step 7: Commit**

```bash
git add README.md packages/engine
git commit -m "CLI: --vocabulary and --corrections replace --glossary"
```
