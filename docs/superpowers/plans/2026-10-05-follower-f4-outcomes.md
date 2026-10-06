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

## The run of Task 7 (plan F4a)

Run on 2026-10-06, from the branch `follower-f4` at `735dc75`, on the same Windows 11 machine.
The scenario ran once and passed.

- Versions, from the commands: `cmd /c ver`: `Microsoft Windows [Version 10.0.26200.9457]`;
  `docker version --format '{{.Server.Version}}'`: `29.8.1`; `python -m uv --version`:
  `uv 0.12.22 (70fe1196a 2026-10-01 x86_64-pc-windows-msvc)`; in the machine,
  `systemctl --version | head -1`: `systemd 252 (252.39-1~deb12u2)`, `/etc/os-release`:
  `Debian GNU/Linux 12 (bookworm)`, `uv --version`: `uv 0.12.22 (x86_64-unknown-linux-gnu)`,
  Python `3.12.15` (the one uv fetched into `/opt/swarmscribe-follower/python`).
- The leader image `swarmscribe-leader:f4-e2e` already existed from this branch; the build was
  all cache hits.
- How the driver was started: `.venv/Scripts/python.exe e2e/follower-systemd/run_e2e.py ...`
  with `UV="python -m uv"`, not through `python -m uv run`. `uv run` overwrites the `UV`
  environment variable with the path of `uv.exe`, which the driver's `shlex.split` then breaks
  on its backslashes (`FileNotFoundError` on the first `uv build`). The driver was not changed.
- `up`: `up: a leader at http://follower-systemd-leader:8080 and a machine with systemd 252
  (252.39-1~deb12u2)`; `real 0m16.554s`.
- `run`, `real 4m32.316s`:

  ```
  passed (tiny.en on cpu, systemd 252): installed with uv tool install and registered in 22 s; a stop with 900 s of grace finished the recording (50 s) and one with 1 s released it (3.0 s, no attempt counted); a killed follower was restarted and its recording redone; MemoryMax= refused an hour; drain exited 0 and stayed stopped; revoke exited 4 and was not restarted; exit 3 was restarted 5 times, then left failed
  ```

- The machine looked at by hand before `down` (step 4's output, whole):

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

- `down`: `real 0m1.877s`, no output. Afterwards no container, network or volume named
  `follower-systemd*` remained (`docker ps -a`, `docker network ls`, `docker volume ls`); the
  images `follower-systemd:e2e` and `swarmscribe-leader:f4-e2e` were kept.

## What this does not prove

- **A real Linux host.** The machine is a privileged container: no reboot was survived, no
  real `network-online.target`, no NVIDIA driver under the unit. The GPU was run in a plain
  container, not under systemd.
- Any distribution but Debian 12, any systemd but 252, cgroup v1.
- A leader on TLS: the test leader is plain http, with the development switch.
- A recording of people talking: the long recordings are one phrase repeated.
