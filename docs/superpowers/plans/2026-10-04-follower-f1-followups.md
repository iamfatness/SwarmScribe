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

All done in F3 (`plans/2026-10-05-follower-f3a-listener-and-chart.md` and `plans/2026-10-05-follower-f3b-kind-install-and-guide.md`):

- The 900 s grace depends on I1. *The chart sets `terminationGracePeriodSeconds: 900` and the follower's grace to 870.*
- A revoked or drained pod reloads the model on every restart (M5). *A drained pod parks and is not restarted. A revoked pod still loads its model at every restart, spaced by Kubernetes' back-off; the README says so. Left as it is.*
- The state folder as a 0700 subfolder of the `emptyDir`. *Done, and measured on a kind cluster.*
- `ON_DRAINED=park`. *Done.*
- The pool token as a file. *Done: a Secret volume, mode 0440, never a subPath.*

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

## Left open from the F2a final review (2026-10-05)

Fixed in F2a's fix wave: I1 (early stop: handlers before the imports, and an init in the image), I2 (the state folder's trust check before registering), I3 (labels checked), M1 (`MODELS` read one way), M2 (`--` before `MODELS`), M5 (the engine command in the README), M6 (the Compose scenario names a follower that will never register), M10 (one setting per `ENV` line), M11 (a start-up model of one space can no longer be built), and of M7 `--security-opt no-new-privileges` and the token file in the README.

Still open:

- I4: the whole suite has not been run since F2a's Task 2, and `follower-compose-e2e` has never run on a GitHub runner. Nothing that needs Docker was run in the fix wave either (Docker was down): the new cases of `docker/check-follower-image.sh` (labels, init, immediate stop), the image build with `tini`, and the Compose scenario's fail-fast are written and unrun. Run them before F2b starts.
- M3: `uv` is installed in the build stage by version, not by hash (`pip install uv==0.12.22`), and the Dockerfile frontend floats (`docker/dockerfile:1`). Both act only at build time, but `uv` builds the environment that ships. Pin both by digest. `tini` now comes from Debian's archive unpinned as well (`apt-get install tini` on a base image pinned by digest): pin its version, or copy the binary from an image pinned by digest.
- M4: unverified files ship beside the verified ones in `/models` (`trees/<commit>.json`, `.locks/`, `CACHEDIR.TAG`, `blobs/**.refs|.lock`, two of them mode 0666), and the check script hashes only the snapshot folder while its comment says "no other file is there". Delete them in the fetcher after the checksums pass, or check the whole tree. Not done blind: which of them the offline loader needs was not measured.
- M7 (rest): `--restart on-failure` without a count restarts exit 2, 3 and 4 for ever (a revoked follower asks the leader again every minute). F2b's Task 7 README says `on-failure:5` **(owner)**; F2a's README still shows the spec's line (8.3).
- M8: with a read-only root Python's temp folder falls back to the working directory, which is the state folder. Nothing writes temp files today. `TMPDIR=/scratch` is the review's cure, but the scratch folder is emptied at every start and refuses content that is not its own, so check `Scratch.prepare` against a stray temp file first; a `/tmp` tmpfs may be the better answer.
- M9: CI builds the leader image three times and the follower image twice; `docker/follower.Dockerfile` copies each package's `tests/` into the build stage, so a test edit rebuilds the environment layer.
- The base image's `pip`, `apt-get`, `perl`, `bash` and suid binaries (`su`, `mount`, `passwd`) remain in the final image (the review's contents row; `no-new-privileges` is now in the README's `docker run`).
- `tini` warns on stderr when it is not PID 1 (`--init`, or a pod with `shareProcessNamespace`). *Decided in F3: the chart never shares the process namespace (its render check refuses it), `tini` is PID 1 in the pod, and nothing is silenced.*
- `doctor` passes a state folder that is the follower's own but looser than 0700 (because `run` tightens it); on a mount where `chmod` does nothing and the follower runs as the folder's owner (root on a Docker Desktop bind mount), `run` then refuses what `doctor` passed.
- F3: the chart's state `emptyDir` passes the trust check through a 0700 folder the follower creates inside the mount, and the `kind` test restarts a container and sees the same follower come back. *Done in F3 (`e2e/follower-kind`, step 4).*
- The other commands (`join`, `leave`, `doctor`) do not install the early handlers: under the image's init a `docker stop` ends them by the default action (143), which is right for commands that hold nothing; `join` interrupted between the leader's answer and the credential's write would leave a registered follower without a credential, as before.

## Left open after F3 (2026-10-05)

- A GPU pool has never run on Kubernetes: the chart's GPU values are rendered and validated only. The first cluster with GPU nodes should run `doctor` in a pod and one recording, and record it in the F3 outcomes.
- The `kind` scenario is run by hand. As a CI job it would take about five minutes (the recorded run: `up` 111 s, `run` 116 s, `no-gpu` 79 s; ruling 2 of the F3b plan: it is not a CI job).
- `leader.ca` (a private CA), `models.volume: persistentVolumeClaim`, `metrics.scrapeAnnotations` and the PodDisruptionBudget are rendered and schema-checked, not run in a cluster. The leader's chart (roadmap item 6) will bring a TLS leader to test the first against.
- A pod is Ready before it has registered (no readiness probe, by the spec). A readiness signal would need a second path on the listener.
- The listener's cap bounds threads but cannot keep a place for the kubelet: a peer the NetworkPolicy lets in, reconnecting without pause, could make the liveness probe fail (eight slow connections every five seconds would do it): name only trusted peers. The default policy lets nobody in.
- No `PodMonitor` (it needs the Prometheus operator's CRD); pod annotations are offered instead.
- A follower that downloads its model at start-up holds about 0.5 GiB more than one that reads it from a cache, until its container restarts (measured with `distil-large-v3`: 2257 against 1766 MiB). Not looked into; baked models avoid it.
