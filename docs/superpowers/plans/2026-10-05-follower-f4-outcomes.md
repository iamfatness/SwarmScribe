# Follower F4: the native install on outside machines

The follower spec (ruling R4, section 8.3) has a follower installed directly on a Linux or
Windows machine, with a systemd unit or a Windows service. This is the record of what was
run. CI runs the follower's unit tests; it installs nothing and starts no service. Repeat a
run, and add a section here, when the unit, the Windows service, the install command or the follower's
start-up or stop changes, and when CTranslate2 or `nvidia-cublas-cu12` changes in `uv.lock`.

## Linux, under systemd

How: `e2e/follower-systemd/run_e2e.py` (`up`, `run`, `down`). The "machine" is a container
that runs systemd as PID 1 (`e2e/follower-systemd/Dockerfile`: Debian 12 with systemd, curl
and uv). The driver installs the follower in it as the README tells an operator to: three
wheels built from the repository, `uv tool install --find-links ... --constraints ...` into
`/opt/swarmscribe-follower`, the user `swarmscribe-follower`, `follower.env` and `join-token`
in `/etc/swarmscribe-follower`, and the unit `deploy/systemd/swarmscribe-follower.service`.
The leader is the leader's test image with Postgres, its lease at 8 seconds. Two things in
the test differ from an operator's install, both in a drop-in: `CPUQuota=200%` (so that eight
minutes of audio take long enough to interrupt) and `RestartSec=2` (the unit's is 30).

## The planner's run, 2026-10-05

Machine: Windows 11, Docker Desktop (Engine 29.8.1, WSL 2), uv 0.12.22. Built in a scratch
folder from `main` at `33885bb` with the plan's files. The scenario passed three times.

| What | Result |
|---|---|
| The machine | `debian:12-slim`, systemd 252 (252.39-1~deb12u2), cgroup v2; `systemctl is-system-running`: `running` |
| `up` | 12 to 15 s once the machine's image exists |
| `run` | passed three times: 4 min 2 s, 3 min 51 s, 4 min 5 s |
| `uv tool install` in the machine (uv fetches Python 3.12.15) | 14 s; `/opt/swarmscribe-follower` is 541 MB |
| Registered after `systemctl enable --now` | 24 s (`tiny.en` from Hugging Face takes about 5 s of it) |
| The process, the folders, the files | user `swarmscribe-follower`; `/var/lib/swarmscribe-follower` and `state` `700`, `credential.json` `600`; `/etc/swarmscribe-follower` `root:swarmscribe-follower 750`, its two files `640` |
| A token in the process's environment or in `systemctl show`; a listening port | none; none |
| `systemctl stop` mid-job, grace 900 s | 40 to 44 s: the recording was finished first (`completed`, 1 attempt); `inactive`, `Result=success` |
| `systemctl stop` mid-job, grace 1 s | 1.6 to 3.2 s; `released`, `queued` |
| `systemctl start` after it | the same follower (one row, one `registered` line); the recording `completed` with 1 attempt |
| `kill -9` mid-job | `Result=signal`; restarted; the recording `expired`, then `completed` |
| `MemoryMax=2500M`, one hour of silence | failed three times: `out_of_resources: OutOfMemory: a recording of 60 minutes needs about 3932 MiB here and this follower may use 2500 MiB (the cgroup's limit: a container's, or a systemd unit's MemoryMax=)` |
| Drained | exit 0, `inactive`, not restarted |
| The pool token revoked with its followers | `status=4/NOPERMISSION`, `failed`, `NRestarts=0`; `stopping: this follower has been revoked` |
| `SWARMSCRIBE_FOLLOWER_DEVICE=cuda` without a GPU | exit 3 five times, then `Start request repeated too quickly`, `failed` |

The GPU, separately: in a plain container from the same image with `--gpus all` (an RTX 4090),
`uv tool install "swarmscribe-follower[cuda]"` (57 s, 1.6 GB) and no `LD_LIBRARY_PATH`,
`doctor` printed `device: cuda (NVIDIA GeForce RTX 4090, 24564 MiB)` and `model: tiny.en
(float16) loaded and ran`. The engine alone in that environment, without the follower's
loading of the library, failed with `Library libcublas.so.12 is not found or cannot be loaded`.

## The runs of Task 7 (plan F4a)

Both on 2026-10-06, on the same Windows 11 machine (Docker 29.8.1, uv 0.12.22), against the
leader image `swarmscribe-leader:f4-e2e` built from the branch `follower-f4`. In the machine:
systemd 252 (252.39-1~deb12u2), Debian GNU/Linux 12 (bookworm), uv 0.12.22
(x86_64-unknown-linux-gnu); `cmd /c ver`: `Microsoft Windows [Version 10.0.26200.9457]`.

### First run, at `735dc75`: passed once, with a harness that was then fixed

- Launched with the venv's python (`.venv/Scripts/python.exe e2e/follower-systemd/run_e2e.py`,
  `UV="python -m uv"`), because the driver did not work under `uv run` on Windows: `uv run`
  overwrote `UV` with the path of `uv.exe`, which the driver split on its backslashes.
- `up` `real 0m16.554s`; `run` `real 4m32.316s`; `down` `real 0m1.877s`. The last line of `run`:
  `passed (tiny.en on cpu, systemd 252): installed with uv tool install and registered in 22 s; a stop with 900 s of grace finished the recording (50 s) and one with 1 s released it (3.0 s, no attempt counted); a killed follower was restarted and its recording redone; MemoryMax= refused an hour; drain exited 0 and stayed stopped; revoke exited 4 and was not restarted; exit 3 was restarted 5 times, then left failed`
- **Its "nothing is listening" result is not evidence.** The machine's image had no `ss`, so
  that check of the harness could not fail. The harness was fixed on review (`369e26a`), and
  the second run is the one that counts for it. The same review made step 9 wait for a new
  follower id and reset the unit's start counter, and step 10 read only the journal written
  after it began; the first run's results for those steps were obtained without those fixes.

### Second run, at `369e26a`, with the fixed harness: passed

- Launched with the brief's own command: `python -m uv run python e2e/follower-systemd/run_e2e.py <cmd>`.
- The leader image was rebuilt (cache hits, `real 0m2.543s`); `up` rebuilt the machine's
  image, which now installs iproute2 (`/usr/bin/ss` exists in the machine).
- `up`: `up: a leader at http://follower-systemd-leader:8080 and a machine with systemd 252
  (252.39-1~deb12u2)`; `real 0m13.386s`.
- `run`, `real 3m49.189s`:

  ```
  passed (tiny.en on cpu, systemd 252): installed with uv tool install and registered in 24 s; a stop with 900 s of grace finished the recording (39 s) and one with 1 s released it (1.5 s, no attempt counted); a killed follower was restarted and its recording redone; MemoryMax= refused an hour; drain exited 0 and stayed stopped; revoke exited 4 and was not restarted; exit 3 was restarted 5 times, then left failed
  ```

  The driver's listening check (`ss -Hltnp`, matching the follower's pid) is part of that
  pass: it ran with `ss` present and found no listening socket of the follower.
- The machine looked at by hand before `down`:

  ```
  Result=exit-code
  NRestarts=5
  ExecMainStatus=3
  ActiveState=failed
  root:swarmscribe-follower 750 /etc/swarmscribe-follower
  root:swarmscribe-follower 640 /etc/swarmscribe-follower/follower.env
  root:swarmscribe-follower 640 /etc/swarmscribe-follower/join-token
  swarmscribe-follower:swarmscribe-follower 700 /var/lib/swarmscribe-follower
  swarmscribe-follower:swarmscribe-follower 700 /var/lib/swarmscribe-follower/state
  swarmscribe-follower:swarmscribe-follower 600 /var/lib/swarmscribe-follower/state/credential.json
  Stopped swarmscribe-follower.service - SwarmScribe follower.
  swarmscribe-follower.service: Start request repeated too quickly.
  swarmscribe-follower.service: Failed with result 'exit-code'.
  Failed to start swarmscribe-follower.service - SwarmScribe follower.
  ```

- `down`: `real 0m1.729s`; afterwards no container, network or volume named `follower-systemd*`
  was left. The images `follower-systemd:e2e` and `swarmscribe-leader:f4-e2e` were kept.

## What this does not prove

- **A real Linux host.** The machine is a privileged container: no reboot was survived, no
  real `network-online.target`, no NVIDIA driver under the unit. The GPU was run in a plain
  container, not under systemd.
- Any distribution but Debian 12, any systemd but 252, cgroup v1.
- A leader on TLS: the test leader is plain http, with the development switch.
- A recording of people talking: the long recordings are one phrase repeated.
