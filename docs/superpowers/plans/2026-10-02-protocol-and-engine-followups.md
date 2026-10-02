# Protocol + Engine — carried-forward items

From the final review of `protocol-engine` (2026-10-02). None blocks merge.
Each is assigned to the plan that should pick it up.

## For the vocabulary plan (spec section 16)

- The fixed transcription settings are written as literals in two places
  (`transcriber.py` call and `writers.py` JSON). Define them once in
  `types.py` so `segments.json` cannot misreport what ran.
- `json.dumps` would emit `NaN`/`Infinity` for a non-finite start, end or
  duration; pass `allow_nan=False` in `render_segments_json`.

## For the leader spec and plan

- `retry_after` on an empty claim and the deregister call have no protocol
  model. Decide header versus body.
- The checksum algorithm is implicit: the engine emits SHA-256 hex; protocol
  checksum fields are free-form `str`. Pin the format.
- No model yet for the claim request body or auth headers.

## For the follower plan

- `TranscribeSettings(temperatures=[...])` validates but is unhashable, which
  breaks the cached module-level `transcribe()`. Coerce to a tuple in
  `__post_init__`, and add a test that the model is reused.
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
