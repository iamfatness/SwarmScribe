# Leader Core (Plan A1) — carried-forward items

From the task reviews and the final whole-branch review of `leader-core`
(2026-10-03). None blocks merge. Each is assigned to the plan that should
pick it up.

## Fix in the next leader plan (A2)

- **Scan walk starts at the location root, not `input_prefix`.** Any
  unreadable folder anywhere under the root fails the scan, so a Windows
  drive root (`E:\`, with `System Volume Information`) can never be scanned,
  and every scan also walks `output_prefix`. Walk from the `input_prefix`
  directory instead.
- **An empty transcript can never complete.** Submit treats a zero-length
  output as missing, so a recording with no speech is retried and then
  fails. Decide how a no-speech result is represented (and accept an empty
  `txt`/`srt` with a valid `segments.json`).
- **Clocks.** `jobs.available_at` defaults to the database clock but claims
  compare against the leader host's clock; use the database clock for both.
- **Submit hashes outputs under the job row lock.** Fine for small text
  outputs; move hashing before taking the lock if outputs grow.
- **Remaining blocking filesystem calls** on the event loop: `file_path`'s
  root probe and the download `open`/`fstat`. Move to a worker thread.
- **Reaper and lock-loss visibility.** Expired/failed/gone transitions write
  no audit rows, and a background loop that never acquires its lock is
  silent. Add audit rows (A2) and a metric/alert (Plan B).
- **Migration 0002** makes `settings_profiles.device` unique; any future
  admin "profiles edit" must keep one profile per device.

## Plan B (cloud storage)

- Upload binding to a lease is enforced by the leader for the local backend.
  Azure/GCS links bypass the leader: choose per-lease staging keys promoted
  at submit, or hash-at-submit, before those backends ship.
- Storage I/O for `outputs_present`/hashing runs under the job row lock;
  with cloud round-trips this needs checking before the lock.

## Can wait

- Windows reserved device names (`nul`, `CON`) and case aliasing
  (`OK.MP3` vs `ok.mp3`) accepted as storage keys.
- A consent character class containing `--` emits a Python `FutureWarning`.
- A recording whose key falls outside an edited `input_prefix` keeps its old
  consent and is marked missing on every scan (matters once A2 can edit a
  location).
- Every `PermissionError` on upload answers 409 "retry", including permanent
  read-only folders.
- An unstarted streaming download leaves its file handle to the garbage
  collector.
- The capabilities JSON stores the follower-supplied pool (the claim uses
  the token's pool); overwrite it on register.
- Test gaps: settings defaults for the interval fields, two registrations
  racing a single-use join token, end-to-end timing margin (~1 s) to soak in
  CI.
- Spec wording: `followers.registered_at` is `created_at`.
