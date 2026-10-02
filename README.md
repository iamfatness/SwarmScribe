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
| `swarmscribe-leader` | Not started |
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
- `--vocabulary` — a text file with one word, name or phrase per line. `#`
  starts a comment. These bias recognition throughout the recording. Order is
  priority: roughly the first 600 characters of terms are used, so put the
  ones that matter most at the top.
- `--corrections` — a text file with one fix per line, written
  `heard as => should be`, for example `jay son => Jason`. Applied after
  transcription as whole-word, case-insensitive replacements.

`segments.json` records which terms were used and every correction that
fired, and keeps the original text of each corrected word.

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

`av` is pinned below 19 in `packages/engine/pyproject.toml`: faster-whisper
1.2.1 passes `metadata_errors=` to `av.open`, which av 19 removed. Lift the
bound when faster-whisper supports av 19.

If you change a wire model, the schema snapshot test fails. Decide whether
`PROTOCOL_VERSION` must change, then regenerate:

```
uv run python -m swarmscribe_protocol.schema packages/protocol/tests/schema_v1.json
```
