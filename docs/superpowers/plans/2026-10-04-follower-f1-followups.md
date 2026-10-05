# Follower F1: follow-ups for the next plans

From the F1 final review ("For the next plans", verbatim), plus the items carried from earlier
tasks. Each item names the review finding it comes from (I = important, M = minor).

## F2 (images, Compose)

- The start-up model setting (I2). *Done in the F1 final fix wave:* `SWARMSCRIBE_FOLLOWER_STARTUP_MODEL`; F2's images set it from the baked model.
- Signals before `prepare` and a stop-during-start-up test as PID 1 (I1). *Done in the F1 final fix wave; F2's Compose test repeats it with the image.*
- `ALLOW_HTTP=1` or the loopback rule for the Compose leader's `http` links (I3). *Done in the F1 final fix wave:* one rule, `ALLOW_HTTP=1`, loopback included; the Compose follower sets it.
- Set `HOME` and the state folder in the image (M2).
- Start the health listener before the model load.
- The whole suite as a gate, not one package (C1).
- The contract tests in a Linux job with Postgres.

## F3 (chart)

- The 900 s grace depends on I1. *I1 is fixed; the chart's grace and `--stop-timeout` can now be relied on.*
- A revoked or drained pod reloads the model on every restart (M5). *The cheap checks now come first, but a drained or revoked follower still loads the model before it learns that; see the follow-up below.*
- The state folder as a 0700 subfolder of the `emptyDir` (earlier carry): the follower creates a subfolder (0700) inside the mount, so the strict trust check passes on container restart.
- `ON_DRAINED=park`.
- The pool token as a file.

## F4 (native install)

- `run` and `doctor` fall back to the stored leader after `join --leader` (M4).
- `getpass` for the token (M7). *Done in the F1 final fix wave.*
- `join` exits non-zero on a leader mismatch (M1). *Done in the F1 final fix wave.*
- Exit 5 only for a protocol refusal (M9). *Done in the F1 final fix wave.*
- The Windows ACL on the credential file (earlier carry): today it relies on the profile folder's inherited permissions.
- The service stop calling `agent.stop()` (earlier carry): the Windows service's stop handler calls `agent.stop()` from `SERVICE_CONTROL_STOP`, reports stop-pending with a wait hint longer than the grace period, and reports stopped only after `serve()` returns.

## Minors left open from the final review

- M6: `doctor` can say `ready` when `run` will not start (it does not check the scratch marker or the state lock, and `joined:` is only a file check); it omits the compute types and host memory spec 8.3 lists.
- M8: the join token stays in the environment for the life of the process and is inherited by `nvidia-smi`.
- M11: contract gaps: fresh links after a 403 and the 401 re-register are tested against the real leader with the raw client only; the tests reach into `agent._control`; the `swarmscribe_kit_follower` database is left behind.
- M12: no unit test proves `--leader` reaches the stored credential (`run_cli` ignores the settings it is given).
- M13: a bug caught by `main` leaves no frames, unlike `serve`.
- M14: README: the exit table omits 1 and 130; "`leave` needs no settings" is not true for a non-default state folder.
- M15 (part): `FOLLOWER_VERSION` duplicates the version in `pyproject.toml`.
- M16: log events spec 9 names are missing: started, device, each step's duration and byte count; a stop while idle logs nothing.
- A drained or revoked follower still loads the start-up model before it learns it has nothing to do (the review's "start-up model load"): a lazy load or a leader route was declined because both weaken "finds out before it claims".
