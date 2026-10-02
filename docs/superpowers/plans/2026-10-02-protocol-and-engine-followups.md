# Protocol + Engine — carried-forward items

From the final review of `protocol-engine` (2026-10-02). None blocks merge.
Each is assigned to the plan that should pick it up.

## Done in the vocabulary plan (2026-10-02)

- Fixed transcription settings defined once (`FIXED_SETTINGS`).
- `allow_nan=False` in `render_segments_json`.
- `TranscribeSettings.temperatures` coerced to a tuple.

## Vocabulary and corrections — carried forward

- A corrections line starting with `#` is a comment, so `#1 => number one`
  is silently skipped. Documented; consider an escape or a warning.
- No Unicode normalisation: a `heard` typed in decomposed form (files saved
  on macOS) or with a straight apostrophe will not match Whisper's composed
  characters or curly apostrophe. NFC-normalise both sides.
- A non-UTF-8 vocabulary or corrections file (for example UTF-16) gives a
  bare codec error; name the file and say "save as UTF-8".
- The CLI prints nothing about what was used. A stderr summary such as
  "vocabulary: 41 of 97 terms used; 3 corrections fired 7 times" would help.
- Matcher cost is words x rules (about 9 s for 30,000 words x 1,000 rules).
  Index rules by first word when files grow.
- No CLI test runs the real corrections through to `txt`/`srt` (every CLI
  test fakes the transcriber); the "--glossary is gone" test passes on any
  argparse error.
- Overlap trimming drops enclosing punctuation equal to the replacement's
  own (`(ashferd)` with `ashferd => (sic)` gives `(sic)`).
- The protocol `Correction` accepts a `heard` with no word character; only
  the CLI rejects it. The leader should apply the CLI's checks on sync.
- Corrections have not been seen firing on real speech: the smoke audio is a
  tone. Needs a short clip that may be committed publicly.

## For the leader spec and plan

- `retry_after` on an empty claim and the deregister call have no protocol
  model. Decide header versus body.
- The checksum algorithm is implicit: the engine emits SHA-256 hex; protocol
  checksum fields are free-form `str`. Pin the format.
- No model yet for the claim request body or auth headers.

## For the follower plan

- Add a test that the cached module-level `transcribe()` reuses the model.
- The engine has no progress callback for `HeartbeatRequest.progress`.
- A write that fails before `os.replace` leaves a `<name>.tmp` behind.
- The CLI's broad `except` is right for a CLI; the follower must call
  `Transcriber` directly so it can tell retryable from non-retryable errors.

## Housekeeping

- CI: use `uv sync --locked`, pin a Python version, add a Windows job.
- Lift the `av>=18,<19` pin when faster-whisper supports av 19.
- Small test gaps: ladder boundary `0.4` alone; `cpu` preference skips the
  CUDA probe; the rewrite test checks only the txt.
- The smoke test uses a generated tone. A speech-accuracy check needs a short
  clip that may be committed publicly.
