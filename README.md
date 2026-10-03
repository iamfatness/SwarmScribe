# SwarmScribe

Distributed transcription for a large archive of English audio and video
recordings. A leader hands recordings to follower machines, which transcribe
them and return text, subtitles and word-level timings. Only recordings
explicitly marked OK to publish are ever processed.

Design: [`docs/superpowers/specs/2026-10-02-swarmscribe-architecture-design.md`](docs/superpowers/specs/2026-10-02-swarmscribe-architecture-design.md)

## Status

| Package | State |
|---|---|
| `swarmscribe-protocol` — leader–follower wire models | Built |
| `swarmscribe-engine` — single-file transcriber | Built |
| `swarmscribe-leader` | Core built (local storage); sign-in and admin tools next |
| `swarmscribe-follower` | Not started |
| Helm chart | Not started |

## Transcribe one file

```
uv sync
uv run swarmscribe-engine recording.mp3 --out out --vocabulary vocabulary.txt --corrections corrections.txt
```

Writes `out/recording.mp3.txt`, `out/recording.mp3.srt` and
`out/recording.mp3.segments.json`.

- `--device auto|cuda|cpu` — default `auto`: a CUDA GPU uses `large-v3`
  (`float16`); otherwise `distil-large-v3` (`int8`) on CPU.
- `--model`, `--compute-type` — override the choice.
- `--vocabulary` — a text file with one word, name or phrase per line. These
  bias recognition throughout the recording. Order is
  priority: Whisper can take only about 220 tokens of terms (typically 60-150
  terms, depending on how unusual they are), so put the ones that matter most
  at the top. `segments.json` lists exactly which were used.
- `--corrections` — a text file with one fix per line, written
  `heard as => should be`, for example `jay son => Jason`. Applied after
  transcription as whole-word, case-insensitive replacements.

In both files, blank lines are ignored and a line starting with `#` is a
comment (a `#` later in a line is part of the text, so `C#` works).

Rules for corrections:

- Whole words only, case-insensitive. The longest rule wins.
- Punctuation written on the "heard as" side must be present in the
  transcript.
- A multi-word rule matches only when nothing but spaces sits between the
  words.
- Possessive and hyphenated forms need their own line
  (`jay son's => Jason's`).
- Matches do not span a segment boundary.
- Two lines with the same "heard as" text and different replacements are an
  error.

`segments.json` records which terms were used and every correction that
fired, and keeps the original text of each corrected word.

Some settings are fixed on purpose: English only, no conditioning on previous
text (prevents repeated-sentence loops), and a temperature ladder capped at
0.4 (prevents gibberish on noisy audio).

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

## Develop

```
uv sync
uv run pytest            # unit tests
uv run pytest -m smoke   # downloads tiny.en and runs the real model
uv run ruff check .
```

`av` is pinned below 19 in `packages/engine/pyproject.toml`: faster-whisper
1.2.1 passes `metadata_errors=` to `av.open`, which av 19 removed. Lift the
bound when faster-whisper supports av 19.

If you change a wire model, the schema snapshot test fails. Decide whether
`PROTOCOL_VERSION` must change, then regenerate:

```
uv run python -m swarmscribe_protocol.schema packages/protocol/tests/schema_v1.json
```
