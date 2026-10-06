# Follower F4a — The Native Install: GPU Libraries, Settings File, Trust Check and the systemd Unit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the follower installable directly on a Linux or Windows machine (no container): the GPU library found by the follower itself, one settings file and one token file, a memory guard and a credential trust check that are true outside a container, and a systemd unit proven under a real systemd against a real leader.

**Architecture:** Four portable changes to the agent, each in a module of its own (`cudalibs.py` loads cuBLAS from the wheel into the process; `envfile.py` reads a settings file for `--env-file`; `memory.py` reads the process's own cgroup and, on Windows, its working set; `winacl.py` gives the credential's trust check a meaning on NTFS). Then the Linux service: a unit in `deploy/systemd/` that starts `swarmscribe-follower --env-file ... run` as a dedicated user, and a driver that installs the follower in a container running systemd as PID 1, exactly as the README will tell an operator to, and checks what the unit does when it is stopped, killed, drained, revoked, short of memory and unable to work. Nothing changes for the images or the chart: they keep their own settings and the same agent code.

**Tech Stack:** Python 3.11+ (standard library and `ctypes` only for the new modules), uv 0.12, systemd 252 (Debian 12) in Docker 29 for the Linux proof, pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-10-04-follower-design.md` (build step 5; ruling R4; D15, D18, D21, D22; sections 4, 4.1, 5.3, 5.6, 5.7, 6.5, 8.3, 10), with `docs/superpowers/plans/2026-10-04-follower-f1-followups.md` (section "F4 (native install)").

**This plan is the first of two.** F4b (`2026-10-05-follower-f4b-windows-service-and-guide.md`) adds the Windows service, the native Windows proof on the development machine's GPU, the Windows CI job, the README's guide to outside machines and the spec's amendments. F4b needs this plan executed first on the same branch; the names it relies on are in each task's **Interfaces** block.

## Global Constraints

- "The same agent code runs in the CPU image, the CUDA image and installed directly on Linux and Windows" (spec 1). Nothing here may break F2 or F3: the images keep `LD_LIBRARY_PATH` and their own settings, the chart is untouched. **Task 1 changes how a model is loaded on a GPU, Task 3 where the memory limit is read, and Tasks 1 to 4 together the modules every image runs: Task 1 ends with the `cuda` image on the GPU, Task 3 with a container's limit, and Task 4 with the follower Compose scenario.** The `kind` scenario need not be run again: the chart is untouched.
- "Docker stays the documented default; a native install (`uv tool install`, a systemd unit, a Windows service) is supported" (D21, ruling R4). "A graphical or packaged (MSI, deb) installer" stays out of scope (spec 1).
- "The join token and the credential are never logged, never passed as arguments, never baked into an image" (spec 7). In a native install the token is also never in a unit file, never in the settings file and never in a process's environment: it is a file of its own, named by `SWARMSCRIBE_JOIN_TOKEN_FILE` (ruling 6).
- "An optional HTTP listener serving `/healthz` and `/metrics`; loopback in the image, pod IP in the chart, off in a native install" (D18). "No port is opened unless the health listener is configured" (spec 7).
- Exit codes (spec 4.1; `errors.py`): `0` stopped cleanly or drained; `2` invalid configuration; `3` this machine cannot do the work; `4` not authorised; `5` protocol version refused; `1` a bug. "Exit 4 and 5 must not be restarted blindly: the systemd unit sets `RestartPreventExitStatus=4 5`" (spec 6.5).
- Shutdown (spec 5.6): "systemd unit | `TimeoutStopSec=930` | `900`"; "The follower's grace is always the platform's timeout minus 30 seconds or less".
- Third-party packages of the follower stay "`httpx` and `pydantic-settings`; `prometheus-client`" (spec 4): **no new dependency.** Windows calls are made with `ctypes`.
- **No test seam in production paths.** Every new function that touches the system takes what it reads as an argument with the real thing as its default (`cudalibs.load(search=...)`, `memory.cgroup_limit_mb(root=..., proc=...)`), as the existing code does.
- **Tests run on both platforms.** A test of portable code runs on Linux and Windows. A test that needs Windows itself is skipped elsewhere with `reason=`; one that needs POSIX modes is skipped on Windows the same way. CI runs on Linux only until F4b adds a Windows job, so on Linux the Windows-only tests of this plan are skipped: they were run on the development machine, and each task says so.
- **The whole suite is the gate of every task that changes Python:** `uv run ruff check .` and `uv run pytest` over the whole repository, run **once**, before the task's commit (about 12 minutes; run focused tests while working). **Never let a pytest run be killed** by a time limit or by hand: a killed run leaves a stray `postgres` holding `.pgdata` under the worktree, and the next runs fail. If that happens, stop only the `postgres` whose data directory is under `C:\Users\walla\SwarmScribe-f4`. Python `>=3.11`; `ruff` line length 100, rules `E, F, I, UP, B`.
- **The development machine** is Windows 11 with Git Bash and PowerShell 5.1. `uv` is not on the PATH: run it as `python -m uv` (the commands below are written `uv ...`; for the drivers set `UV="python -m uv"`). Docker Desktop: in Git Bash first `export PATH="$PATH:/c/Users/walla/AppData/Local/Programs/DockerDesktop/resources/bin"`. In Git Bash a `docker` argument that starts with `/` is rewritten into a Windows path: put `MSYS_NO_PATHCONV=1` in front of such a command (the drivers are Python and are not affected).
- **Work only in the worktree `C:\Users\walla\SwarmScribe-f4`**, on the branch `follower-f4`. Never touch `C:\Users\walla\SwarmScribe` or `C:\Users\walla\SwarmScribe-ui` (another agent works there). Do not push.
- **Another agent uses Docker on this machine.** Never stop, remove or retag a container or an image you did not create, never run any `docker ... prune`, never restart Docker. This plan's containers, network and machine image are all named `follower-systemd-*` (`E2E_PREFIX`); the images it builds are tagged `swarmscribe-leader:f4-e2e` and `swarmscribe-follower:f4-*`, never the shared `:e2e`, `:cpu` or `:cuda`. Never use ports 8900 or 8901 (nothing here publishes a port).
- **Never attempt to elevate.** The shell is not an administrator's and cannot become one. Nothing in this plan needs it. Do not use `runas`, scheduled tasks or any other way around a UAC prompt.
- **Capture, then match.** In a shell check under `set -euo pipefail`, never pipe a live `docker`, `systemctl` or `journalctl` command into `grep -q`: `grep -q` leaves at its first match, the producer dies of `SIGPIPE`, and `pipefail` fails a check that passed. Write the output to a variable or a file first, then match it. (The driver is Python and captures everything.)
- **Line endings.** The repository is checked out with CRLF on the development machine (`core.autocrlf=true`). Files that a Linux machine reads as they are (the systemd unit, the settings example) are pinned to LF in `.gitattributes` (Task 5); a unit with CRLF does not load.
- Commit after each task with the message the task gives, ending with exactly the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Measured before this plan was written

All of this plan's code ran on 2026-10-05 in a scratch folder outside the repository, built from `main` at `33885bb` (Windows 11 10.0.26200, Python 3.12.10 from the Microsoft Store and 3.12.15 from uv, uv 0.12.22, RTX 4090 with driver 617.14, Docker Desktop 29.8.1 with WSL 2).

**How CTranslate2 finds cuBLAS on Windows** (carry-over 2). `uv tool install` of the follower with its `cuda` extra, in a clean environment, `tiny.en` as `float16` on `cuda`, load and warm-up:

| How the wheel's folder (`site-packages\nvidia\cublas\bin`) was offered | uv's Python 3.12.15 | Store Python 3.12.10 |
|---|---|---|
| nothing | `Library cublas64_12.dll is not found or cannot be loaded` | the same |
| prepended to `PATH` | loaded and ran | **not found** |
| `os.add_dll_directory` | **not found** | loaded and ran |
| `cublas64_12.dll` loaded first by its full path (`ctypes`) | loaded and ran | loaded and ran |
| the DLLs copied beside `ctranslate2.dll` (the 2026-10 benchmark's way) | works for the same reason: CTranslate2's `__init__` loads every DLL in its own folder by full path | |

Why: `ctranslate2.dll` imports `LoadLibraryA` and names `cublas64_12.dll` bare. That is the standard search: it reads `PATH` and ignores `os.add_dll_directory` (which only serves `LoadLibraryEx` with the `LOAD_LIBRARY_SEARCH_*` flags). The Store's Python is a packaged app, whose search does not read `PATH` (`LoadLibraryExW("cublas64_12.dll", 0)` with the folder on `PATH`: error 126 there, loaded on uv's Python). The earlier finding "PATH did not work, copying did" was made on the Store Python. **A library that is already in the process is found by its bare name on both**, and that is the mechanism this plan uses (ruling 1).

| What | Result |
|---|---|
| The same on Linux: Debian 12 container with `--gpus all`, `uv tool install "swarmscribe-follower[cuda]"`, `LD_LIBRARY_PATH` empty | `doctor`: `device: cuda (NVIDIA GeForce RTX 4090, 24564 MiB)`, `model: tiny.en (float16) loaded and ran`. The engine alone, in the same environment, without the follower's loading: `Library libcublas.so.12 is not found or cannot be loaded` |
| `swarmscribe-follower cuda-paths` there | `/opt/swarmscribe-follower/tools/swarmscribe-follower/lib/python3.12/site-packages/nvidia/cublas/lib:.../nvidia/cuda_nvrtc/lib` |
| `uv tool install swarmscribe-follower` as the spec writes it | cannot work: the package is on no index, and it needs `swarmscribe-engine` and `swarmscribe-protocol` from the same workspace (`[tool.uv.sources]` is not part of a wheel's metadata) |
| Three wheels (`uv build --package ... --wheel`), then `uv tool install --find-links <folder> swarmscribe-follower` | works on Windows and in a Debian 12 container: 14 s without the `cuda` extra (541 MB with uv's Python), 57 s with it (1.6 GB). The wheels are 8, 15 and 62 KB |
| The same with `--constraints` of a file exported from `uv.lock` (`uv export --frozen --no-dev --no-emit-workspace --package swarmscribe-follower --extra cuda --no-hashes`) | works; 100 lines; CTranslate2 4.8.2, faster-whisper 1.2.1, av 18.1.0, nvidia-cublas-cu12 12.9.2.10, as the images |
| `uv tool install "swarmscribe-follower @ git+https://github.com/iamfatness/SwarmScribe@33885bb#subdirectory=packages/follower"` | works too (the repository is public): uv takes the engine and the protocol package from the same commit. Not pinned to `uv.lock` unless `--constraints` is given, and it needs `git` on the machine |
| A container with systemd as PID 1 (`debian:12-slim` with `systemd`, `--privileged --cgroupns=private --tmpfs /run --tmpfs /run/lock`) | `systemctl is-system-running`: `running`; systemd 252 (252.39-1~deb12u2); cgroup v2 |
| The unit of Task 5, installed by the driver of Task 6 | `active (running)`; the process is `swarmscribe-follower`'s; `/var/lib/swarmscribe-follower` and `state` are `700`, `credential.json` `600`; no token in the process's environment or in `systemctl show`; nothing listens |
| `e2e/follower-systemd/run_e2e.py run` | passed three times: 4 min 2 s, 3 min 51 s and 4 min 5 s. `up` takes 12 to 15 s once the image exists (its first build: 15 s and a 30 MB pull) |
| Registered after `systemctl enable --now` | 24 s, of which `tiny.en` from Hugging Face about 5 s |
| `systemctl stop` mid-job, grace 900 s (the recording has about 40 s left on two cores) | the recording was finished first: the stop took 40 to 44 s, `completed`, 1 attempt; unit `inactive`, `Result=success` |
| `systemctl stop` mid-job, grace 1 s | 1.6 to 2.1 s; the recording `released` and `queued`, then `completed` with 1 attempt by the same follower after `systemctl start` (no second `registered` line) |
| `kill -9` of the follower mid-job | `Result=signal`, restarted after `RestartSec`; the recording `expired`, then `completed` |
| A drop-in with `MemoryMax=2500M`, one hour of silence | failed three times: `out_of_resources: OutOfMemory: a recording of 60 minutes needs about 3932 MiB here and this follower may use 2500 MiB (the cgroup's limit ...)`. `/proc/<pid>/cgroup`: `0::/system.slice/swarmscribe-follower.service`; before this plan the guard read `/sys/fs/cgroup/memory.max`, which a host's root cgroup does not have |
| Drained | `stopping: drained`, exit 0, `inactive (dead)`, not restarted |
| The pool token revoked with its followers | `status=4/NOPERMISSION`, `failed`, `NRestarts=0`; the journal's last follower line: `stopping: this follower has been revoked` |
| `SWARMSCRIBE_FOLLOWER_DEVICE=cuda` without a GPU (exit 3) | restarted five times, then `Start request repeated too quickly`, `failed` |
| `doctor` run by hand as the service's user (`runuser -u swarmscribe-follower -- swarmscribe-follower --env-file ... doctor --no-model`) | `leader: answers`, `joined: yes`, `result: ready`; its `memory:` line says `this machine`, because a command run by hand is not in the unit's cgroup |

**Windows, without administrator rights:**

| What | Result |
|---|---|
| The access control list of `%LOCALAPPDATA%` on this machine | the user, SYSTEM, Administrators, **and** a second local account and an app-container capability SID: a folder made there by Explorer or `cmd` inherits all of them |
| `%TEMP%` on this machine | `Everyone` and five more |
| A folder made by Python with mode `0700` (`os.mkdir(path, 0o700)`, Python 3.12.10) | owner rights, SYSTEM, Administrators only: Python 3.12.4 and later give such a folder a private list. An older Python inherits the parent's |
| `winacl.make_private` on a folder made by `cmd` under `%TEMP%` | changed it to the user, SYSTEM, Administrators; a file inside inherits that; a second call changes nothing |
| The `icacls` command the refusal prints | ran, and the folder was trusted afterwards |
| One trust check (`access_problem`) | 0.12 ms |
| `test_writers_in_the_same_folder_never_leave_a_torn_file` (three writers, one reader in a tight loop) | 1.1 s as before, once the file's list is read through the already open file and the check runs after it is closed. With a second open of the file by name it took 3 to 6 s and a writer sometimes gave up (`Access is denied`): Windows refuses a replace while another handle is open |
| `memory.rss_mb()` (the working set) | 40 MiB at start, 257 MiB after allocating 200 MiB |
| The follower's own tests on Windows, with every change of F4a and F4b | `737 passed, 14 skipped` in 67 s (`uv run pytest packages/follower`) |
| The whole suite with every change of F4a and F4b | `3193 passed, 21 skipped, 5 deselected` in 9 min 6 s on Windows. Three tests were added after that run (one each in F4a's Tasks 4 and 5 and F4b's Task 1); the follower package was run again with them: `737 passed, 14 skipped` |
| The same tests on Linux (a Debian 12 container, an unprivileged user, Python 3.12; without the contract tests, which need a Postgres) | `uv run ruff check .`: `All checks passed!`; `716 passed, 20 skipped, 15 deselected`. `test_winacl.py`: `8 skipped`; `test_memory.py`: `32 passed`; `test_credentials.py`: `18 passed` (the POSIX cases) |

**The images with this plan's code** (nothing may break F2 and F3), built from the scratch folder under tags of the planner's own:

| What | Result |
|---|---|
| `docker/check-follower-image.sh` on the `cpu` image, the `cpu` image with `tiny.en` baked in, and the `cuda` image | `ok` three times (789 MB, 938 MB, 2548 MB) |
| The `cuda` image's `doctor` on the GPU (`--gpus all`, `tiny.en`): the image's `LD_LIBRARY_PATH` and the follower's own loading together | `device: cuda (NVIDIA GeForce RTX 4090, 24564 MiB)`, `model: tiny.en (float16) loaded and ran`, `result: ready` |
| The `cpu` image's `doctor` under `docker run --memory 3g` | `memory: 3072 MiB may be used (the cgroup's limit: a container's, or a systemd unit's MemoryMax=)` |
| The follower Compose scenario (`e2e/follower-compose/run_e2e.py run`) with every change of F4a and F4b | `passed (tiny.en on cpu): two followers registered in 4 s and shared six recordings; a killed follower's job was redone in 32 s under an 8 s lease; a stop mid-job took 2.1 s and counted no attempt; drain exited 0 and revoke exited 4, twice each`, in 1 min 57 s |

**Not run again by the planner:** the Compose scenario on the GPU (`run --gpu`) and the `kind` scenario. The chart is untouched; the modules a pod runs that this plan changes are the ones the Compose scenario exercises.

## Rulings

Decisions this plan makes. Those marked **(owner)** are the owner's to overturn; each has the plan's recommendation built in, so execution does not wait.

1. **The follower loads cuBLAS itself, from the wheel, into its own process; nothing is copied and no path variable is needed. There is no `setup-cuda` command.** **(owner)** The spec (D15, 8.3) has Linux set `LD_LIBRARY_PATH` from `cuda-paths` and Windows copy the DLLs beside `ctranslate2.dll` with `setup-cuda`, to be "run again after an upgrade that replaces CTranslate2". The measurement above shows one mechanism that works on Linux, on a python.org or uv Python and on the Store's Python, with nothing to set up and nothing to redo after an upgrade: load the library by its full path before the first model. Recommended as written. Overturning it costs a command of about thirty lines that copies two DLLs (700 MB) into `site-packages\ctranslate2`, and a line in every upgrade procedure.
2. **`cuda-paths` stays, for looking**: it prints the wheels' library folders joined with the platform's path separator (what `LD_LIBRARY_PATH` or `PATH` would take), and exits 3 with a line naming `swarmscribe-follower[cuda]` when the extra is not installed. The follower itself does not use it.
3. **The images keep their `LD_LIBRARY_PATH`.** In the `cuda` image both mechanisms name the same file; removing the variable would change an image that F2 proved for no gain.
4. **An outside machine installs three wheels built from the repository, pinned by a constraints file exported from `uv.lock`.** **(owner)** The spec's `uv tool install swarmscribe-follower` cannot work (measured above). The command is `uv tool install --python 3.12 --find-links <folder> --constraints <folder>/follower-constraints.txt "swarmscribe-follower[cuda]"`; the folder holds the three wheels and `deploy/follower-constraints.txt` (committed, and checked against `uv.lock` by a test). Installing straight from the Git repository also works and the README will say so; it is not the documented way because it needs `git` on every machine and is pinned only if the constraints file is fetched as well. Publishing the packages to an index later makes the spec's line true and costs nothing here.
5. **Settings come from a file the follower reads itself: `swarmscribe-follower --env-file PATH <command>`.** The spec has the unit use `EnvironmentFile=`. With `--env-file` the unit, the Windows service (F4b) and a person running `doctor` or `leave` by hand all read the same file the same way; with `EnvironmentFile=` a hand-run command would see none of the unit's settings. A value in the file wins over the environment. The file is `NAME=value` lines, nothing expanded.
6. **The token is a file of its own**, `join-token` beside the settings file, named in it by `SWARMSCRIBE_JOIN_TOKEN_FILE`, readable by root (Windows: administrators) and the service's account only. It is in no unit file, no command line and no process's environment (which also closes F1's minor M8 for native installs: `nvidia-smi` inherits nothing). It is read once, at the first registration, and may be emptied afterwards. The settings file is `root:swarmscribe-follower 0640`, not "readable by root only" as the spec has it: the follower reads it itself, and it holds no secret.
7. **The health listener stays off in a native install** (D18): neither the unit nor the settings example sets `SWARMSCRIBE_FOLLOWER_HEALTH_ADDR`, and a test holds them to it. systemd does not probe HTTP, and "outside machines must not open a port". An operator who wants `/metrics` for a local Prometheus sets `127.0.0.1:9108` in the settings file. Carry-over 1 (the supervisor's tick) needs nothing under systemd: the unit runs `run`, whose `run_supervised` ticks.
8. **The memory guard reads the process's own cgroup**, from `/proc/self/cgroup`, and takes the smallest `memory.max` from there up to the root. A unit's `MemoryMax=` (and a slice's) then is the limit, with no setting to keep in step. In a container nothing changes: the process's cgroup is the root there. **On Windows the guard now counts what the process holds** (its working set, `K32GetProcessMemoryInfo`), as on Linux; its default limit there stays the machine's total memory, and `SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB` stays the way to give it less. A Windows job object's limit is not read.
9. **On Windows the state folder and the credential file may be reachable only by the follower's account, SYSTEM and Administrators.** **(owner)** Today nothing is checked there. The POSIX rule ("another user's, or writable by others, is refused; a looser folder of one's own is tightened before a credential exists") is applied to the access control list: the owner must be this account, SYSTEM or Administrators, and any allow entry for anyone else refuses the folder, with the `icacls` command that fixes it in the message. Before a credential exists a folder of the account's own is made private, as `chmod 700` does. **This can refuse a follower that joined before F4 on Windows**: a state folder that Explorer or an older Python made under `%LOCALAPPDATA%` inherits that folder's list, which on the development machine names two more principals. The message prints the command; `leave` and `join` again also works. Recommended as written: a credential that another account can read is what the check exists for. Overturning it means returning to "Windows checks nothing", stated in the README. Deny entries and the two entry kinds Windows does not put on files are not read; a custom scratch folder is made private the same way but never refused.
10. **What systemd does with each exit** (carry-over 7): `Restart=on-failure` with `RestartPreventExitStatus=4 5`, `RestartSec=30`, and `StartLimitBurst=5` in `StartLimitIntervalSec=600`.

    | The follower ends with | systemd |
    |---|---|
    | `0` stopped, or drained | leaves it stopped (`inactive`) |
    | `4` revoked or token refused, `5` protocol | leaves it `failed`; never restarted |
    | `2` configuration, `3` this machine cannot do the work, `1` a bug | restarted after 30 s; the fifth start within ten minutes is the last, and the unit is left `failed` with the reason as the journal's last line |
    | a signal (`kill -9`, the kernel's out-of-memory kill) | restarted after 30 s (the same limit) |

    Without the limit, a machine whose GPU has gone would load its model every 30 seconds for ever. The image's `--restart on-failure:5` and the chart's CrashLoopBackOff do the same in their way.
11. **Stopping under systemd** (carry-over 6): `systemctl stop` sends `SIGTERM` to the follower (`KillMode=mixed`), which finishes the recording in hand if its estimated time left fits `SWARMSCRIBE_FOLLOWER_SHUTDOWN_GRACE_SECONDS` (900 in the settings example) and hands it back without a counted attempt otherwise; at `TimeoutStopSec=930` systemd kills what is left, and the recording then costs one counted attempt when its lease runs out. A reboot is the same stop. A test holds the two numbers 30 seconds apart.
12. **The service's user is a static system user, `swarmscribe-follower`**, made with `useradd --system`, not `DynamicUser=`: an operator must be able to run `doctor` and `leave` as that user, and the state folder's owner must be the same at every start.
13. **The systemd proof is a container that runs systemd as PID 1, run by hand and recorded in an outcomes document, not a CI job.** **(owner)** Recommended as written, as F3b's ruling 2 did for the `kind` test. It needs a privileged container, takes four minutes, and proves the unit's wiring, which changes rarely; GitHub's Linux runners could run it (a job of about ten lines around the driver) if the owner wants it there. It is not a real host: no reboot, no real `network-online.target`, no NVIDIA driver under systemd (the GPU was run in a plain container, above).
14. **`run` and `doctor` use the leader the stored credential names when `SWARMSCRIBE_LEADER_URL` is not set** (F1 follow-up M4), as `leave` already does: after `join --leader URL` nothing else needs setting.
15. **The limit's source is named `the cgroup's limit: a container's, or a systemd unit's MemoryMax=`** in `doctor` and in a refusal, where it said `the container's limit`.

## Review Focus

Conditions the spec implies and that are most likely to bite a person installing a follower on a machine of their own, each pinned by a test in the task that owns the code:

1. **A GPU machine where the `cuda` extra was forgotten, or the driver is missing**: start-up exits 3 before registering, and the line says what to install or check, not only the name of a DLL — Task 1, `test_a_warm_up_that_misses_cublas_says_how_to_install_it`, `test_the_hint_says_what_to_do_about_a_missing_cublas_and_nothing_about_other_errors`.
2. **A settings file with a typo, saved by Notepad with a byte-order mark, or missing**: exit 2 naming the file and the line, never echoing a value; a BOM is ignored — Task 2, `test_a_broken_settings_file_is_exit_2_and_its_content_is_not_shown`, `test_the_file_wins_over_the_environment_and_a_byte_order_mark_is_ignored`.
3. **A unit with `MemoryMax=`, a unit without one, a slice with a smaller one, a cgroup v1 host, no cgroup at all**: the limit is the smallest that binds, or the machine's memory, never a crash — Task 3, `test_a_systemd_units_memory_max_is_seen`, `test_the_smallest_limit_from_the_unit_up_to_the_root_binds`, `test_cgroup_v1_is_still_read_where_there_is_no_v2`; Task 7, the scenario's step 7.
4. **A Windows state folder that other accounts can reach** (made at the root of a drive, or inherited): refused before a join token is spent, with the command that fixes it; a folder of one's own is made private instead — Task 4, `test_a_folder_others_can_reach_is_refused_with_the_command_that_fixes_it`, `test_before_registering_a_loose_folder_of_ones_own_is_made_private`.
5. **A service stopped in the middle of a recording, or killed**: finished if it fits the grace period, else handed back with no attempt counted; killed, it comes back as the same follower — Task 7, steps 3 to 6.
6. **A revoked follower, and a machine that has lost its GPU, under a supervisor that restarts on failure**: the first is never restarted, the second five times and no more — Task 5, `test_the_unit_restarts_on_failure_but_never_a_revoked_follower_and_not_for_ever`; Task 7, steps 9 and 10.
7. **A unit file checked out on Windows** (CRLF) **and copied to a Linux machine**: pinned to LF — Task 5, `test_what_a_linux_machine_reads_has_no_carriage_returns`.

## File Structure

| File | Responsibility |
|---|---|
| `packages/follower/src/swarmscribe_follower/cudalibs.py` (new, Task 1) | find the `nvidia-*` wheels' libraries; load cuBLAS into the process; say what to do when it is missing |
| `.../models.py` (modify, Task 1) | call it before a model is loaded on a GPU; add its hint to the refusal |
| `.../envfile.py` (new, Task 2) | read a `NAME=value` settings file into the environment |
| `.../entry.py` (modify, Task 2) | find the command behind `--env-file PATH` |
| `.../main.py` (modify, Tasks 1 and 2) | `cuda-paths`; `--env-file`; the stored leader for `run` and `doctor` |
| `.../memory.py` (modify, Task 3) | the process's own cgroup; the working set on Windows |
| `.../winacl.py` (new, Task 4) | Windows: read an access control list, judge it, make a folder private |
| `.../fsutil.py`, `.../credentials.py` (modify, Task 4) | use it where POSIX modes are used |
| `packages/follower/tests/test_cudalibs.py`, `test_envfile.py`, `test_winacl.py`, `test_native_files.py` (new); `test_memory.py`, `test_main.py` (modify) | their tests |
| `deploy/systemd/swarmscribe-follower.service`, `deploy/systemd/follower.env.example` (new, Task 5) | the unit and its settings example |
| `deploy/follower-constraints.txt` (new, Task 5) | the lock file's versions, for `uv tool install --constraints` |
| `.gitattributes` (modify, Task 5) | LF for what a Linux machine reads |
| `e2e/follower-systemd/Dockerfile`, `run_e2e.py` (new, Task 6) | a machine with systemd; the driver: `up`, `run`, `down` |
| `.gitignore`, `.dockerignore` (modify, Task 6) | `e2e/follower-systemd/work` |
| `docs/superpowers/plans/2026-10-05-follower-f4-outcomes.md` (new, Task 7) | the systemd run, recorded |

The README, the spec's amendments and the follow-ups are F4b's last task: they describe both plans' work at once.

---

### Task 1: The GPU library is found and loaded by the follower itself; `cuda-paths`

**Files:**
- Create: `packages/follower/src/swarmscribe_follower/cudalibs.py`
- Modify: `packages/follower/src/swarmscribe_follower/models.py`
- Modify: `packages/follower/src/swarmscribe_follower/main.py`
- Test: `packages/follower/tests/test_cudalibs.py` (new)

**Interfaces:**
- Consumes: `ModelHost(device, *, factory, allowed, on_loaded)` and its `get(model, compute_type)` (`models.py`); `main(argv, *, build, out, err, stdin, signals)` and `parser()` (`main.py`); `EXIT_UNFIT = 3` (`errors.py`).
- Produces: `cudalibs.NAMES` (the two cuBLAS file names of this platform, in load order), `cudalibs.EXTRA == "swarmscribe-follower[cuda]"`, `cudalibs.roots() -> list[Path]`, `cudalibs.folders(search=None) -> list[Path]`, `cudalibs.files(search=None) -> list[Path]`, `cudalibs.load(search=None) -> list[Path]`, `cudalibs.hint(message, search=None) -> str`. `ModelHost(..., libraries: Callable[[], object] = cudalibs.load, hint: Callable[[str], str] = cudalibs.hint)`. The command `swarmscribe-follower cuda-paths` (exit 0 and one line, or exit 3). F4b's Windows proof runs `cuda-paths` and relies on `load` being called before a model is loaded on `cuda`.

- [ ] **Step 1: Write the failing tests**

Create `packages/follower/tests/test_cudalibs.py`:

```python
import io
import logging
import os

import pytest
from swarmscribe_follower import cudalibs
from swarmscribe_follower import main as cli
from swarmscribe_follower.models import ModelHost, ModelUnavailable

INSIDE = "bin" if os.name == "nt" else "lib"
MISSING = "Library cublas64_12.dll is not found or cannot be loaded"


def wheels(tmp_path, *, files: bool = True):
    """A site-packages/nvidia as the nvidia-cublas-cu12 wheel unpacks it; the "libraries" are
    empty files, which no loader accepts."""
    root = tmp_path / "site-packages" / "nvidia"
    for package in ("cublas", "cuda_nvrtc"):
        (root / package / INSIDE).mkdir(parents=True)
    (root / "cublas" / "include").mkdir()
    if files:
        for name in cudalibs.NAMES:
            (root / "cublas" / INSIDE / name).write_bytes(b"")
    return [root]


def test_the_wheels_library_folders_are_found_and_the_files_come_in_load_order(tmp_path):
    search = wheels(tmp_path)
    root = search[0]
    assert cudalibs.folders(search) == [root / "cublas" / INSIDE, root / "cuda_nvrtc" / INSIDE]
    assert cudalibs.files(search) == [root / "cublas" / INSIDE / name for name in cudalibs.NAMES]
    assert cudalibs.NAMES[0].lower().startswith(("cublaslt", "libcublaslt"))  # needed by the other


def test_without_the_cuda_extra_nothing_is_found_and_nothing_is_loaded(tmp_path):
    assert cudalibs.folders([]) == [] and cudalibs.files([]) == [] and cudalibs.load([]) == []
    assert cudalibs.files(wheels(tmp_path, files=False)) == []


def test_a_library_that_will_not_load_is_a_warning_never_an_error(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="swarmscribe_follower.cudalibs"):
        assert cudalibs.load(wheels(tmp_path)) == []
    assert [record.getMessage() for record in caplog.records] == [
        f"the GPU library {name} could not be loaded (OSError)" for name in cudalibs.NAMES
    ]


def test_the_real_library_loads_where_the_cuda_extra_is_installed():
    if not cudalibs.files():
        pytest.skip("the cuda extra (nvidia-cublas-cu12) is not installed in this environment")
    assert cudalibs.load() == cudalibs.files()
    assert cudalibs.load() == cudalibs.files()  # once: the second call loads nothing again


def test_the_hint_says_what_to_do_about_a_missing_cublas_and_nothing_about_other_errors(tmp_path):
    assert cudalibs.hint("CUDA failed with error out of memory", []) == ""
    assert "install the follower as swarmscribe-follower[cuda]" in cudalibs.hint(MISSING, [])
    assert "check the NVIDIA driver" in cudalibs.hint(MISSING, wheels(tmp_path))


class Model:
    def __init__(self, log, error=None):
        self.log, self.error = log, error

    def warm_up(self):
        self.log.append("warm_up")
        if self.error is not None:
            raise self.error

    def close(self):
        self.log.append("close")


def test_on_a_gpu_the_libraries_are_loaded_before_the_model_and_never_on_a_cpu():
    log = []

    def factory(settings):
        log.append("load")
        return Model(log)

    def libraries():
        log.append("libraries")

    ModelHost("cuda", factory=factory, libraries=libraries).get("large-v3", "float16")
    assert log == ["libraries", "load", "warm_up"]
    log.clear()
    ModelHost("cpu", factory=factory, libraries=libraries).get("tiny.en", "int8")
    assert log == ["load", "warm_up"]


def test_a_warm_up_that_misses_cublas_says_how_to_install_it():
    host = ModelHost(
        "cuda", factory=lambda settings: Model([], RuntimeError(MISSING)),
        libraries=lambda: None, hint=lambda message: cudalibs.hint(message, []),
    )
    with pytest.raises(ModelUnavailable) as refused:
        host.get("large-v3", "float16")
    assert MISSING in str(refused.value)
    assert "install the follower as swarmscribe-follower[cuda]" in str(refused.value)


def test_cuda_paths_prints_the_folders_or_says_the_extra_is_missing(tmp_path, monkeypatch):
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(cudalibs, "roots", lambda: [])
    assert cli.main(["cuda-paths"], out=out, err=err) == 3
    assert out.getvalue() == "" and "install swarmscribe-follower[cuda]" in err.getvalue()
    search = wheels(tmp_path)
    monkeypatch.setattr(cudalibs, "roots", lambda: search)
    out, err = io.StringIO(), io.StringIO()
    assert cli.main(["cuda-paths"], out=out, err=err) == 0
    assert out.getvalue().strip().split(os.pathsep) == [
        str(search[0] / "cublas" / INSIDE), str(search[0] / "cuda_nvrtc" / INSIDE)
    ]
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/follower/tests/test_cudalibs.py -q`
Expected: an error while collecting, `ImportError: cannot import name 'cudalibs' from 'swarmscribe_follower'`.

- [ ] **Step 3: The module**

Create `packages/follower/src/swarmscribe_follower/cudalibs.py`:

```python
"""The GPU library CTranslate2 needs, found and loaded by the follower itself (follower spec D15).

CTranslate2 opens cuBLAS by its bare name at the first inference (`cublas64_12.dll`,
`libcublas.so.12`). The library comes from the `nvidia-cublas-cu12` wheel (the `cuda` extra),
which unpacks it into `site-packages/nvidia/cublas/bin` (Windows) or `.../lib` (Linux): a
folder no loader searches by itself.

What was measured on Windows (plan F4a, 2026-10-05, CTranslate2 4.8.2, an RTX 4090):

    how the folder was offered         python.org / uv Python     Microsoft Store Python
    nothing                            not found                  not found
    PATH                               loads                      not found
    os.add_dll_directory               not found                  loads
    the library loaded by full path    loads                      loads

CTranslate2 calls `LoadLibraryA("cublas64_12.dll")`: the standard search, which reads PATH and
ignores `os.add_dll_directory`; a Store Python is a packaged app, whose search ignores PATH.
The one thing both honour is a library that is already in the process: a load by bare name
finds a loaded module of that name before it searches anywhere. Linux is the same (`dlopen`
of a name finds a loaded library with that SONAME), so the follower needs no
`LD_LIBRARY_PATH` either. That is what `load` does, on both: it loads the wheel's files by
their full paths before the first model is loaded."""

import ctypes
import importlib.util
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# In load order: cuBLAS itself needs the "Lt" library beside it.
NAMES = (
    ("cublasLt64_12.dll", "cublas64_12.dll")
    if os.name == "nt"
    else ("libcublasLt.so.12", "libcublas.so.12")
)
EXTRA = "swarmscribe-follower[cuda]"
_loaded: dict[Path, ctypes.CDLL] = {}  # kept: a library that is let go could be unloaded


def roots() -> list[Path]:
    """Where the `nvidia-*` wheels are unpacked: every `nvidia` folder on the import path."""
    try:
        spec = importlib.util.find_spec("nvidia")
    except (ImportError, ValueError):
        return []
    if spec is None or not spec.submodule_search_locations:
        return []
    return [Path(root) for root in spec.submodule_search_locations]


def folders(search: list[Path] | None = None) -> list[Path]:
    """The folders that hold the wheels' shared libraries (`nvidia/*/bin` on Windows,
    `nvidia/*/lib` elsewhere), sorted; empty when the `cuda` extra is not installed."""
    inside = "bin" if os.name == "nt" else "lib"
    found = []
    for root in roots() if search is None else search:
        found.extend(path for path in root.glob(f"*/{inside}") if path.is_dir())
    return sorted(found)


def files(search: list[Path] | None = None) -> list[Path]:
    """The cuBLAS files to load, in load order; empty when the wheel is not installed."""
    found = []
    for name in NAMES:
        for folder in folders(search):
            if (folder / name).is_file():
                found.append(folder / name)
                break
    return found


def load(search: list[Path] | None = None) -> list[Path]:
    """Load cuBLAS from the wheel into this process, once; returns the files that are loaded.
    Nothing is raised: without the wheel, or with a file that will not load, the model's
    warm-up says what is missing, and `hint` adds what to do about it."""
    done = []
    for path in files(search):
        if path not in _loaded:
            try:
                # RTLD_GLOBAL is ignored on Windows; a full path makes Windows look for the
                # library's own neighbours in its folder.
                _loaded[path] = ctypes.CDLL(str(path), mode=ctypes.RTLD_GLOBAL)
            except OSError as error:
                logger.warning(
                    "the GPU library %s could not be loaded (%s)", path.name, type(error).__name__
                )
                continue
        done.append(path)
    return done


def hint(message: str, search: list[Path] | None = None) -> str:
    """What to do when a model failed with `message`; empty when it is not about cuBLAS."""
    if "cublas" not in message.lower():
        return ""
    if not files(search):
        return (
            f" The GPU library cuBLAS is not installed: install the follower as {EXTRA}"
            " (`swarmscribe-follower cuda-paths` shows what is found)."
        )
    return (
        " cuBLAS is installed but did not load: check the NVIDIA driver with `nvidia-smi`"
        " (`swarmscribe-follower cuda-paths` shows the folders)."
    )
```

- [ ] **Step 4: `ModelHost` loads the libraries before a model, on a GPU only, and adds the hint**

In `packages/follower/src/swarmscribe_follower/models.py` make exactly these four changes (shown as a diff; edit by hand):

```diff
--- a/packages/follower/src/swarmscribe_follower/models.py
+++ b/packages/follower/src/swarmscribe_follower/models.py
@@ -13,6 +13,8 @@ from typing import Any
 from swarmscribe_engine import Device, Transcriber, TranscribeSettings
 from swarmscribe_protocol import MODEL_NAME_MAX_LENGTH, MODEL_NAME_PATTERN
 
+from . import cudalibs
+
 logger = logging.getLogger(__name__)
 
 # A model is named, never located: the name comes over the wire and the loader would accept
@@ -44,8 +46,12 @@ class ModelHost:
         factory: EngineFactory = Transcriber,
         allowed: frozenset[str] = frozenset(),
         on_loaded: Callable[[float], None] | None = None,
+        libraries: Callable[[], object] = cudalibs.load,
+        hint: Callable[[str], str] = cudalibs.hint,
     ) -> None:
         self.device = device
+        # On a GPU, called before a model is loaded: puts cuBLAS into the process (cudalibs.py).
+        self._libraries, self._hint = libraries, hint
         self._factory = factory
         self._allowed = allowed
         self._on_loaded = on_loaded  # told how many seconds a load and its warm-up took
@@ -70,6 +76,8 @@ class ModelHost:
         settings = TranscribeSettings(model=model, compute_type=compute_type, device=self.device)
         transcriber = None
         started = time.monotonic()
+        if self.device == "cuda":
+            self._libraries()
         try:
             transcriber = self._factory(settings)
             transcriber.warm_up()
@@ -82,6 +90,7 @@ class ModelHost:
             # operator's first clue, and it holds nothing of a recording.
             raise ModelUnavailable(
                 f"{model} ({compute_type}, {self.device}): {type(exc).__name__}: {str(exc)[:500]}"
+                f"{self._hint(str(exc)) if self.device == 'cuda' else ''}"
             ) from exc
         self._transcriber, self._loaded = transcriber, (model, compute_type)
         if self._on_loaded is not None:
```

- [ ] **Step 5: The `cuda-paths` command**

In `packages/follower/src/swarmscribe_follower/main.py` make exactly these changes:

```diff
--- a/packages/follower/src/swarmscribe_follower/main.py
+++ b/packages/follower/src/swarmscribe_follower/main.py
@@ -1,4 +1,5 @@
-"""swarmscribe-follower: run, join, leave, doctor (follower spec 4, 5.2, 5.3).
+"""swarmscribe-follower: run, join, leave, doctor, cuda-paths (follower spec 4, 5.2, 5.3,
+8.3).
 
 Every way this ends is a one-line `error: ...` and an exit code from errors.py; no traceback
 reaches the user. The join token is never an argument (spec 5.3: it would show in process
@@ -18,7 +19,7 @@ from typing import Any, TextIO
 from pydantic import SecretStr, ValidationError
 from swarmscribe_engine import DeviceUnavailableError
 
-from . import FOLLOWER_VERSION, logs
+from . import FOLLOWER_VERSION, cudalibs, logs
 from .agent import Agent
 from .config import Settings
 from .credentials import CredentialFileError, CredentialStore
@@ -109,6 +110,21 @@ def doctor_settings(out: TextIO) -> Settings | int:
         return EXIT_CONFIGURATION
 
 
+def command_cuda_paths(out: TextIO, err: TextIO) -> int:
+    """Print the folders of the GPU libraries the `cuda` extra installed, joined as PATH and
+    LD_LIBRARY_PATH want them. The follower needs neither variable (cudalibs.py); this is for
+    looking, and for another program that uses the same libraries."""
+    found = cudalibs.folders()
+    if not found or not cudalibs.files():
+        print(
+            f"error: the GPU library cuBLAS is not installed here; install {cudalibs.EXTRA}",
+            file=err,
+        )
+        return EXIT_UNFIT
+    print(os.pathsep.join(str(folder) for folder in found), file=out)
+    return EXIT_OK
+
+
 def configure_environment(settings: Settings) -> None:
     """Tell the model loader where its cache is and whether it may download. Must run
     before the first model is loaded: Hugging Face's library reads these when it is
@@ -397,6 +413,9 @@ def parser() -> argparse.ArgumentParser:
     doctor.add_argument(
         "--no-leader", action="store_true", help="do not ask the leader's /healthz"
     )
+    commands.add_parser(
+        "cuda-paths", help="print the folders of the GPU libraries the cuda extra installed"
+    )
     return top
 
 
@@ -413,6 +432,8 @@ def main(
     imported, for `run` only (entry.py says why)."""
     out, err, stdin = out or sys.stdout, err or sys.stderr, stdin or sys.stdin
     args = parser().parse_args(argv)
+    if args.command == "cuda-paths":
+        return command_cuda_paths(out, err)
     if args.command == "doctor":
         settings = doctor_settings(out)
         if isinstance(settings, int):
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest packages/follower/tests/test_cudalibs.py packages/follower/tests/test_models.py packages/follower/tests/test_main.py -q`
Expected: `73 passed, 1 skipped` (the one skipped test is `test_the_real_library_loads_where_the_cuda_extra_is_installed`: the development environment and CI do not install the `cuda` extra; F4b's Windows proof and Step 7 run the real library).

- [ ] **Step 7: The `cuda` image still finds its library** (this task changed how a model is loaded on a GPU)

On the development machine (Git Bash, the Docker `PATH` line of Global Constraints; about three minutes, most of it the image's layers from the cache):

```bash
docker build -t swarmscribe-follower:f4-cuda --target cuda -f docker/follower.Dockerfile .
bash docker/check-follower-image.sh swarmscribe-follower:f4-cuda cuda
MSYS_NO_PATHCONV=1 docker run --rm --gpus all --tmpfs /models:uid=10001,gid=10001 --tmpfs /scratch:uid=10001,gid=10001 \
  -e SWARMSCRIBE_LEADER_URL=https://leader.invalid -e SWARMSCRIBE_FOLLOWER_STARTUP_MODEL=tiny.en \
  swarmscribe-follower:f4-cuda doctor --no-leader
docker rmi swarmscribe-follower:f4-cuda
```

Expected (the planner saw exactly this, under a tag of its own): the check script's last line is `ok: swarmscribe-follower:f4-cuda is a cuda follower (2548 MB)`; `doctor` prints `device: cuda (NVIDIA GeForce RTX 4090, 24564 MiB)` and `model: tiny.en (float16) loaded and ran`, and ends `result: ready`. In the image the library is named twice, by `LD_LIBRARY_PATH` and by the follower's own loading; both name the same file. If `doctor` fails here, stop and report: ruling 3 would then be wrong.

- [ ] **Step 8: The gate, and commit**

Run: `uv run ruff check .` — expected `All checks passed!`. Run: `uv run pytest` once, to the end — expected no failure (on the development machine the count grows by this task's eight tests; F4a and F4b together end at about `3196 passed, 21 skipped, 5 deselected`).

```bash
git add packages/follower/src/swarmscribe_follower/cudalibs.py packages/follower/src/swarmscribe_follower/models.py packages/follower/src/swarmscribe_follower/main.py packages/follower/tests/test_cudalibs.py
git commit -m "feat(follower): load cuBLAS from its wheel into the process; cuda-paths

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: A settings file (`--env-file`), and the stored leader for `run` and `doctor`

**Files:**
- Create: `packages/follower/src/swarmscribe_follower/envfile.py`
- Modify: `packages/follower/src/swarmscribe_follower/entry.py`
- Modify: `packages/follower/src/swarmscribe_follower/main.py`
- Test: `packages/follower/tests/test_envfile.py` (new), `packages/follower/tests/test_main.py` (modify)

**Interfaces:**
- Consumes: `main()`, `parser()`, `load_settings(err, **given)`, `leave_settings(out, err)`, `doctor_settings(out)` (`main.py`); `entry.run()`; the test kit's `BASE`, `make_agent` and `test_main.py`'s `run_cli`.
- Produces: `envfile.EnvFileError`, `envfile.parse(text, where) -> dict[str, str]`, `envfile.load(path, environ=None) -> list[str]` (the names it set; standard library only). The option `swarmscribe-follower --env-file PATH <command>` (before the command). `entry.command(argv) -> str | None`. `main.stored_leader(err) -> dict | int`. The systemd unit (Task 5) starts `swarmscribe-follower --env-file /etc/swarmscribe-follower/follower.env run`; F4b's Windows service calls `envfile.load` itself and then `main(["run"], ...)`.

- [ ] **Step 1: Write the failing tests**

Create `packages/follower/tests/test_envfile.py`:

```python
import io
import os

import pytest
from swarmscribe_follower import entry, envfile
from swarmscribe_follower import main as cli

SECRET = "value-that-must-not-be-echoed"


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    """No SwarmScribe setting comes in from outside, and none that a settings file put into
    the process's environment (that is what `--env-file` does) stays behind."""
    before = set(os.environ)
    for name in list(os.environ):
        if name.startswith("SWARMSCRIBE_") or name in ("HF_HUB_CACHE", "HF_HUB_OFFLINE"):
            monkeypatch.delenv(name)
    yield
    for name in set(os.environ) - before:
        del os.environ[name]


def test_names_and_values_are_read_and_comments_blanks_quotes_and_export_are_allowed():
    text = (
        "# a comment\n"
        "\n"
        "SWARMSCRIBE_LEADER_URL=https://leader.example.org\n"
        "  SWARMSCRIBE_FOLLOWER_POOL = office \n"
        'QUOTED="two words"\n'
        "SINGLE='x=y'\n"
        "export EXPORTED=1\n"
        "EMPTY=\n"
        r"WINDOWS=C:\ProgramData\swarmscribe-follower\state" "\n"
    )
    assert envfile.parse(text, "follower.env") == {
        "SWARMSCRIBE_LEADER_URL": "https://leader.example.org",
        "SWARMSCRIBE_FOLLOWER_POOL": "office",
        "QUOTED": "two words",
        "SINGLE": "x=y",
        "EXPORTED": "1",
        "EMPTY": "",
        "WINDOWS": r"C:\ProgramData\swarmscribe-follower\state",
    }


@pytest.mark.parametrize("line", [f"no equals sign {SECRET}", f"9NAME={SECRET}", f"A B={SECRET}"])
def test_a_bad_line_is_named_by_its_number_and_never_by_its_content(line):
    with pytest.raises(envfile.EnvFileError) as refused:
        envfile.parse(f"GOOD=1\n{line}\n", "follower.env")
    assert str(refused.value) == "follower.env, line 2: expected NAME=value"


def test_the_file_wins_over_the_environment_and_a_byte_order_mark_is_ignored(tmp_path):
    path = tmp_path / "follower.env"
    path.write_bytes(b"\xef\xbb\xbfSWARMSCRIBE_FOLLOWER_POOL=from-file\nOTHER=2\n")
    environ = {"SWARMSCRIBE_FOLLOWER_POOL": "from-environment", "KEPT": "yes"}
    assert envfile.load(path, environ) == ["OTHER", "SWARMSCRIBE_FOLLOWER_POOL"]
    assert environ == {"SWARMSCRIBE_FOLLOWER_POOL": "from-file", "OTHER": "2", "KEPT": "yes"}


def test_a_file_that_is_missing_too_large_or_not_text_is_refused_in_words(tmp_path):
    with pytest.raises(envfile.EnvFileError, match="cannot be read"):
        envfile.load(tmp_path / "absent.env", {})
    large = tmp_path / "large.env"
    large.write_bytes(b"A=1\n" * 20_000)
    with pytest.raises(envfile.EnvFileError, match="larger than"):
        envfile.load(large, {})
    binary = tmp_path / "binary.env"
    binary.write_bytes(b"A=\xff\xfe\n")
    with pytest.raises(envfile.EnvFileError, match="not UTF-8"):
        envfile.load(binary, {})


def test_the_command_is_found_behind_the_env_file_option():
    assert entry.command(["run"]) == "run"
    assert entry.command(["--env-file", "run", "doctor"]) == "doctor"  # "run" is the PATH here
    assert entry.command(["--env-file", "/etc/swarmscribe-follower/follower.env", "run"]) == "run"
    assert entry.command(["--env-file=/etc/x.env", "run"]) == "run"
    assert entry.command(["--version"]) is None
    assert entry.command([]) is None


def test_a_command_reads_its_settings_from_the_file(tmp_path):
    path = tmp_path / "follower.env"
    path.write_text(
        "SWARMSCRIBE_LEADER_URL=https://leader.example.org\n"
        f"SWARMSCRIBE_FOLLOWER_STATE_DIR={tmp_path / 'state'}\n"
        "SWARMSCRIBE_FOLLOWER_POOL=office\n"
        "SWARMSCRIBE_FOLLOWER_DEVICE=cpu\n",
        encoding="utf-8",
    )
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(
        ["--env-file", str(path), "doctor", "--no-model", "--no-leader"], out=out, err=err
    )
    assert code == 0, out.getvalue() + err.getvalue()
    assert "settings: ok (leader https://leader.example.org, pool office)" in out.getvalue()
    assert f"state folder: ok ({tmp_path / 'state'}" in out.getvalue()


def test_a_broken_settings_file_is_exit_2_and_its_content_is_not_shown(tmp_path):
    path = tmp_path / "follower.env"
    path.write_text(f"SWARMSCRIBE_LEADER_URL https://{SECRET}\n", encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()
    assert cli.main(["--env-file", str(path), "run"], out=out, err=err) == 2
    assert err.getvalue().strip() == f"error: {path}, line 1: expected NAME=value"
    assert SECRET not in out.getvalue() + err.getvalue()
    out, err = io.StringIO(), io.StringIO()
    assert cli.main(["--env-file", str(tmp_path / "absent"), "leave"], out=out, err=err) == 2
    assert "cannot be read" in err.getvalue()
```

Append two tests at the end of `packages/follower/tests/test_main.py` (shown as a diff; every line of it is an addition):

```diff
--- a/packages/follower/tests/test_main.py
+++ b/packages/follower/tests/test_main.py
@@ -500,3 +500,37 @@ def test_the_no_leader_and_no_model_flags_reach_doctor(monkeypatch):
     assert seen == {"load_model": True, "ask_leader": False}
     assert cli.main(["doctor", "--no-model", "--no-leader"]) == 0
     assert seen == {"load_model": False, "ask_leader": False}
+
+
+def test_after_join_with_a_leader_run_and_doctor_need_no_leader_url(
+    tmp_path, leader, engine, monkeypatch
+):
+    """F1 follow-up M4: `join --leader URL` on an outside machine, and nothing else to set."""
+    monkeypatch.delenv("SWARMSCRIBE_LEADER_URL")
+    code, _out, err, _ = run_cli(tmp_path, leader, engine, "join", "--leader", BASE)
+    assert code == 0, err
+    leader.state = "draining"  # so that `run` ends by itself, after its first claim
+    asked = []
+
+    def build(settings):
+        asked.append(settings.leader_url)
+        return make_agent(tmp_path, leader, engine)
+
+    out, err = io.StringIO(), io.StringIO()
+    assert cli.main(["run"], build=build, out=out, err=err) == 0, err.getvalue()
+    assert asked == [BASE]
+    out, err = io.StringIO(), io.StringIO()
+    assert cli.main(["doctor", "--no-model", "--no-leader"], out=out, err=err) == 0
+    assert f"settings: ok (leader {BASE}, pool default)" in out.getvalue()
+
+
+def test_without_a_leader_url_and_without_a_credential_run_still_says_what_is_missing(
+    monkeypatch,
+):
+    monkeypatch.delenv("SWARMSCRIBE_LEADER_URL")
+    out, err = io.StringIO(), io.StringIO()
+    assert cli.main(["run"], out=out, err=err) == 2
+    assert "SWARMSCRIBE_LEADER_URL" in err.getvalue() or "leader_url" in err.getvalue()
+    out, err = io.StringIO(), io.StringIO()
+    assert cli.main(["doctor", "--no-model", "--no-leader"], out=out, err=err) == 2
+    assert "settings: FAILED" in out.getvalue()
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/follower/tests/test_envfile.py -q`
Expected: an error while collecting, `ImportError: cannot import name 'envfile' from 'swarmscribe_follower'`.

- [ ] **Step 3: The module**

Create `packages/follower/src/swarmscribe_follower/envfile.py`:

```python
"""A settings file for a follower that a service manager starts (follower spec 8.3).

`swarmscribe-follower --env-file PATH <command>` reads `NAME=value` lines into the process's
environment before the settings are read, so that the systemd unit, the Windows service and a
person running `doctor` or `leave` by hand all use the same file. A value in the file wins over
the environment the process was started with.

The file holds settings, not the join token: the token is a file of its own, named by
`SWARMSCRIBE_JOIN_TOKEN_FILE`. A mistake in the file is reported by its line number, never by
its content. Standard library only: the Windows service reads the file before anything slow
is imported."""

import os
import re
from collections.abc import MutableMapping
from pathlib import Path

NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
MAX_BYTES = 64 * 1024


class EnvFileError(Exception):
    """The settings file cannot be used; the message never holds a value from it."""


def parse(text: str, where: str) -> dict[str, str]:
    """`NAME=value` per line. Blank lines and lines starting with `#` are skipped; spaces
    around the name and the value are dropped; one pair of quotes around a value is removed.
    Nothing is expanded: `$HOME` and `%ProgramData%` stay as written."""
    values: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, equals, value = line.partition("=")
        name = name.strip().removeprefix("export ").strip()
        if not equals or not NAME.fullmatch(name):
            raise EnvFileError(f"{where}, line {number}: expected NAME=value")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if "\x00" in value:
            raise EnvFileError(f"{where}, line {number}: a value cannot hold a NUL character")
        values[name] = value
    return values


def load(path: str | Path, environ: MutableMapping[str, str] | None = None) -> list[str]:
    """Read `path` into `environ` (the process's environment by default); returns the names
    it set, sorted."""
    environ = os.environ if environ is None else environ
    try:
        with open(path, "rb") as source:
            data = source.read(MAX_BYTES + 1)
    except OSError as error:
        raise EnvFileError(
            f"the settings file {path} cannot be read: {error.strerror or type(error).__name__}"
        ) from None
    if len(data) > MAX_BYTES:
        raise EnvFileError(f"the settings file {path} is larger than {MAX_BYTES} bytes")
    try:
        text = data.decode("utf-8-sig")  # Notepad writes a byte-order mark
    except UnicodeDecodeError:
        raise EnvFileError(f"the settings file {path} is not UTF-8 text") from None
    values = parse(text, str(path))
    environ.update(values)
    return sorted(values)
```

- [ ] **Step 4: `entry.run` finds `run` behind the option**

`entry.run` installs the stop-signal handlers before anything is imported, for `run` only, and recognised it by `sys.argv[1:2] == ["run"]`. In `packages/follower/src/swarmscribe_follower/entry.py`:

```diff
--- a/packages/follower/src/swarmscribe_follower/entry.py
+++ b/packages/follower/src/swarmscribe_follower/entry.py
@@ -22,9 +22,23 @@ This module imports only the standard library at import time. Keep it so."""
 import sys
 
 
+def command(argv: list[str]) -> str | None:
+    """The command on the command line: its first word that is not an option, nor the PATH
+    of `--env-file PATH`."""
+    skip = False
+    for argument in argv:
+        if skip:
+            skip = False
+        elif argument == "--env-file":
+            skip = True
+        elif not argument.startswith("-"):
+            return argument
+    return None
+
+
 def run() -> None:
     signals = previous = None
-    if sys.argv[1:2] == ["run"]:
+    if command(sys.argv[1:]) == "run":
         from .signals import StopSignals
 
         signals = StopSignals()
```

- [ ] **Step 5: The option, and the stored leader**

In `packages/follower/src/swarmscribe_follower/main.py` (as Task 1 left it):

```diff
--- a/packages/follower/src/swarmscribe_follower/main.py
+++ b/packages/follower/src/swarmscribe_follower/main.py
@@ -19,7 +19,7 @@ from typing import Any, TextIO
 from pydantic import SecretStr, ValidationError
 from swarmscribe_engine import DeviceUnavailableError
 
-from . import FOLLOWER_VERSION, cudalibs, logs
+from . import FOLLOWER_VERSION, cudalibs, envfile, logs
 from .agent import Agent
 from .config import Settings
 from .credentials import CredentialFileError, CredentialStore
@@ -62,13 +62,14 @@ def load_settings(err: TextIO, **given: Any) -> Settings | None:
         return None
 
 
-def leave_settings(out: TextIO, err: TextIO) -> Settings | int:
-    """The settings for `leave`: the environment's, or, when no leader is set, the leader the
-    credential was issued by. An exit code when there is nothing to do or nothing can be
-    done: only an ABSENT credential file means "has not joined"; an invalid setting or a
-    credential file that cannot be read is an error (exit 2), never a success."""
+def stored_leader(err: TextIO) -> dict[str, Any] | int:
+    """What to add to the settings when SWARMSCRIBE_LEADER_URL is not set: the leader the
+    stored credential was issued by, so that `run`, `doctor` and `leave` work after
+    `join --leader URL` without the URL being set anywhere. Empty when a leader is set or
+    nothing is stored; an exit code when the other settings or the credential file are wrong
+    (exit 2, never taken for "has not joined")."""
     if os.environ.get("SWARMSCRIBE_LEADER_URL", "").strip():
-        return load_settings(err) or EXIT_CONFIGURATION
+        return {}
     # A placeholder leader that is never contacted: it lets the other settings (the state
     # folder above all) be validated and reported as usual.
     base = load_settings(err, leader_url="https://placeholder.invalid")
@@ -80,19 +81,32 @@ def leave_settings(out: TextIO, err: TextIO) -> Settings | int:
         print(f"error: {error}", file=err)
         return EXIT_CONFIGURATION
     if stored is None:
-        print("this follower has not joined a leader", file=out)
-        return EXIT_OK
+        return {}
     given: dict[str, Any] = {"leader_url": stored.leader_url}
     if stored.leader_url.lower().startswith("http://"):
-        given["allow_http"] = True  # it was issued under that switch; leaving needs no more
+        given["allow_http"] = True  # it was issued under that switch
+    return given
+
+
+def leave_settings(out: TextIO, err: TextIO) -> Settings | int:
+    """The settings for `leave`: the environment's, or, when no leader is set, the leader the
+    credential was issued by. An exit code when there is nothing to do or nothing can be
+    done: only an ABSENT credential file means "has not joined"; an invalid setting or a
+    credential file that cannot be read is an error (exit 2), never a success."""
+    given = stored_leader(err)
+    if isinstance(given, int):
+        return given
+    if not given and not os.environ.get("SWARMSCRIBE_LEADER_URL", "").strip():
+        print("this follower has not joined a leader", file=out)
+        return EXIT_OK
     return load_settings(err, **given) or EXIT_CONFIGURATION
 
 
-def doctor_settings(out: TextIO) -> Settings | int:
+def doctor_settings(out: TextIO, **given: Any) -> Settings | int:
     """`doctor` says what is wrong with the settings as one of its checks, in plain words,
     instead of refusing to start."""
     try:
-        return Settings()
+        return Settings(**given)
     except (ValidationError, RuntimeError) as error:
         print(f"swarmscribe-follower {FOLLOWER_VERSION}", file=out)
         if isinstance(error, ValidationError):
@@ -394,6 +408,12 @@ def parser() -> argparse.ArgumentParser:
         " 5 protocol version refused; 1 unexpected error (doctor: leader unreachable).",
     )
     top.add_argument("--version", action="version", version=f"%(prog)s {FOLLOWER_VERSION}")
+    top.add_argument(
+        "--env-file",
+        metavar="PATH",
+        help="read settings (NAME=value lines) from this file first; they win over the"
+        " environment. What the systemd unit and the Windows service use",
+    )
     commands = top.add_subparsers(dest="command", required=True, metavar="command")
     commands.add_parser("run", help="join if needed, then work until stopped")
     join = commands.add_parser("join", help="register with the leader and store the credential")
@@ -434,19 +454,34 @@ def main(
     args = parser().parse_args(argv)
     if args.command == "cuda-paths":
         return command_cuda_paths(out, err)
-    if args.command == "doctor":
-        settings = doctor_settings(out)
-        if isinstance(settings, int):
-            return settings
-    elif args.command == "leave":
+    if args.env_file:
+        try:
+            envfile.load(args.env_file)
+        except envfile.EnvFileError as error:
+            print(f"error: {error}", file=err)
+            return EXIT_CONFIGURATION
+    if args.command == "leave":
         settings = leave_settings(out, err)
         if isinstance(settings, int):
             return settings
-    else:
-        given = {"leader_url": args.leader} if args.command == "join" and args.leader else {}
-        settings = load_settings(err, **given)
+    elif args.command == "join" and args.leader:
+        settings = load_settings(err, leader_url=args.leader)
         if settings is None:
             return EXIT_CONFIGURATION
+    else:
+        # Without SWARMSCRIBE_LEADER_URL, `run` and `doctor` use the leader this follower
+        # joined (`join --leader URL` needs nothing set afterwards).
+        given = stored_leader(err) if args.command in ("run", "doctor") else {}
+        if isinstance(given, int):
+            return given
+        if args.command == "doctor":
+            settings = doctor_settings(out, **given)
+            if isinstance(settings, int):
+                return settings
+        else:
+            settings = load_settings(err, **given)
+            if settings is None:
+                return EXIT_CONFIGURATION
     logs.configure_logging(settings.log_format, stream=err)
     try:
         if args.command == "run":
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest packages/follower/tests/test_envfile.py packages/follower/tests/test_main.py packages/follower/tests/test_startup.py -q`
Expected: `71 passed`.

- [ ] **Step 7: The gate, and commit**

Run: `uv run ruff check .` — expected `All checks passed!`. Run: `uv run pytest` once, to the end — expected no failure.

```bash
git add packages/follower/src/swarmscribe_follower/envfile.py packages/follower/src/swarmscribe_follower/entry.py packages/follower/src/swarmscribe_follower/main.py packages/follower/tests/test_envfile.py packages/follower/tests/test_main.py
git commit -m "feat(follower): --env-file reads a settings file; run and doctor use the stored leader

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The memory guard outside a container — the process's own cgroup, and the working set on Windows

**Files:**
- Modify: `packages/follower/src/swarmscribe_follower/memory.py`
- Test: `packages/follower/tests/test_memory.py` (modify)

**Interfaces:**
- Consumes: `memory.find_limit(configured, *, cgroup=cgroup_limit_mb, physical=physical_mb)`, `memory.rss_mb()`, `memory.MemoryGuard` (unchanged signatures).
- Produces: `memory.own_cgroup(proc=PROC_CGROUP) -> str | None`; `memory.cgroup_limit_mb(root=CGROUP_ROOT, proc=PROC_CGROUP, v1=CGROUP_V1) -> int | None` (the old `paths` argument is gone; nothing else called it with one); `memory.CGROUP_SOURCE`; `memory.rss_mb()` answers on Windows. Task 7's scenario expects a refusal that contains `may use 2500 MiB` and `cgroup`.

- [ ] **Step 1: Replace the cgroup tests and add the working-set test**

In `packages/follower/tests/test_memory.py`:

```diff
--- a/packages/follower/tests/test_memory.py
+++ b/packages/follower/tests/test_memory.py
@@ -1,4 +1,5 @@
 import io
+import sys
 import wave
 from pathlib import Path
 
@@ -92,23 +93,80 @@ def test_nothing_known_means_no_limit_and_a_nonsense_figure_is_ignored():
     assert find_limit(None, cgroup=lambda: 3, physical=lambda: 16000).megabytes == 16000
 
 
+def cgroups(tmp_path, own: str | None, limits: dict[str, str]):
+    """A cgroup v2 tree under tmp_path: `own` is what /proc/self/cgroup names (None: no v2),
+    `limits` maps a cgroup ("" is the root) to the content of its memory.max."""
+    root, proc = tmp_path / "cgroup", tmp_path / "proc-self-cgroup"
+    for cgroup, content in limits.items():
+        (root / cgroup).mkdir(parents=True, exist_ok=True)
+        (root / cgroup / "memory.max").write_text(content, encoding="ascii")
+    if own is not None:
+        proc.write_text(f"0::{own}\n", encoding="ascii")
+    return {"root": root, "proc": proc, "v1": tmp_path / "v1-absent"}
+
+
 @pytest.mark.parametrize(
     ("content", "megabytes"),
-    [
-        ("max\n", None),  # cgroup v2: unlimited
-        ("4294967296\n", 4096),
-        ("9223372036854771712\n", None),  # cgroup v1: unlimited
-        ("junk\n", None),
-    ],
+    [("max\n", None), ("4294967296\n", 4096), ("junk\n", None), ("", None)],
 )
-def test_the_container_limit_is_read_from_the_cgroup_file(tmp_path, content, megabytes):
-    file = tmp_path / "memory.max"
-    file.write_text(content, encoding="ascii")
-    assert memory.cgroup_limit_mb((tmp_path / "absent", file)) == megabytes
+def test_a_containers_limit_is_its_root_cgroup_file(tmp_path, content, megabytes):
+    # In a container the process sees its own cgroup as the root.
+    assert memory.cgroup_limit_mb(**cgroups(tmp_path, "/", {"": content})) == megabytes
+
+
+def test_a_systemd_units_memory_max_is_seen(tmp_path):
+    # On a host the root cgroup has no memory.max at all; the unit's own cgroup has.
+    unit = "system.slice/swarmscribe-follower.service"
+    where = cgroups(tmp_path, "/" + unit, {"system.slice": "max\n", unit: "6442450944\n"})
+    assert memory.cgroup_limit_mb(**where) == 6144
+
+
+def test_the_smallest_limit_from_the_unit_up_to_the_root_binds(tmp_path):
+    unit = "machine.slice/small.slice/follower.service"
+    limits = {
+        "machine.slice": "8589934592\n",
+        "machine.slice/small.slice": "2147483648\n",
+        unit: "4294967296\n",
+    }
+    assert memory.cgroup_limit_mb(**cgroups(tmp_path, "/" + unit, limits)) == 2048
 
 
-def test_no_cgroup_file_is_no_container_limit(tmp_path):
-    assert memory.cgroup_limit_mb((tmp_path / "absent", tmp_path / "also-absent")) is None
+def test_a_unit_without_a_limit_has_none(tmp_path):
+    unit = "system.slice/swarmscribe-follower.service"
+    where = cgroups(tmp_path, "/" + unit, {"system.slice": "max\n", unit: "max\n"})
+    assert memory.cgroup_limit_mb(**where) is None
+
+
+def test_a_cgroup_outside_the_visible_tree_reads_the_root_only(tmp_path):
+    where = cgroups(tmp_path, "/../../elsewhere", {"": "1073741824\n"})
+    assert memory.cgroup_limit_mb(**where) == 1024
+
+
+@pytest.mark.parametrize(
+    ("content", "megabytes"),
+    [("4294967296\n", 4096), ("9223372036854771712\n", None)],  # the second: v1's "no limit"
+)
+def test_cgroup_v1_is_still_read_where_there_is_no_v2(tmp_path, content, megabytes):
+    where = cgroups(tmp_path, None, {})
+    where["v1"].write_text(content, encoding="ascii")
+    assert memory.cgroup_limit_mb(**where) == megabytes
+    where["proc"].write_text("12:memory:/user.slice\n", encoding="ascii")  # v1 lines only
+    assert memory.cgroup_limit_mb(**where) == megabytes
+
+
+def test_no_cgroup_at_all_is_no_limit(tmp_path):
+    assert memory.cgroup_limit_mb(**cgroups(tmp_path, None, {})) is None
+
+
+def test_this_process_says_what_it_holds_on_linux_and_on_windows():
+    if sys.platform == "darwin":
+        pytest.skip("macOS has no /proc and no working-set call here: the job is counted alone")
+    before = memory.rss_mb()
+    assert before is not None and before >= 10
+    held = bytearray(64 * 1024 * 1024)
+    held[::4096] = b"\x01" * len(held[::4096])  # touch every page, so that it is really held
+    assert memory.rss_mb() >= before + 32
+    del held
 
 
 def test_this_machine_says_how_much_memory_it_has():
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/follower/tests/test_memory.py -q`
Expected: the new cgroup tests fail with `TypeError: cgroup_limit_mb() got an unexpected keyword argument 'root'`; on Windows `test_this_process_says_what_it_holds_on_linux_and_on_windows` fails with `assert None is not None`.

- [ ] **Step 3: The implementation**

In `packages/follower/src/swarmscribe_follower/memory.py`:

```diff
--- a/packages/follower/src/swarmscribe_follower/memory.py
+++ b/packages/follower/src/swarmscribe_follower/memory.py
@@ -22,8 +22,10 @@ JOB_BASE_MB = 100.0  # what a job of any length adds (measured: 50 to 80)
 MONO_MB_PER_HOUR = 3600.0  # measured 3460: mono, or `auto` on anything but a stereo file
 SPLIT_MB_PER_HOUR = 3900.0  # the same pass, with the other channel (about 230 MiB/h) held
 MIN_LIMIT_MB = 64
+CGROUP_SOURCE = "the cgroup's limit: a container's, or a systemd unit's MemoryMax="
 _UNLIMITED = 1 << 60  # cgroup v1 says "no limit" with a number near 2**63
-CGROUP_V2 = Path("/sys/fs/cgroup/memory.max")
+CGROUP_ROOT = Path("/sys/fs/cgroup")  # cgroup v2, as every current distribution mounts it
+PROC_CGROUP = Path("/proc/self/cgroup")
 CGROUP_V1 = Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")
 
 
@@ -31,19 +33,54 @@ def _megabytes(count: int) -> int:
     return count // (1024 * 1024)
 
 
-def cgroup_limit_mb(paths: tuple[Path, ...] = (CGROUP_V2, CGROUP_V1)) -> int | None:
-    """The container's memory limit, or None when there is none (or no cgroup: Windows)."""
-    for path in paths:
-        try:
-            text = path.read_text(encoding="ascii").strip()
-        except (OSError, ValueError):
-            continue
-        if text.isdigit() and int(text) < _UNLIMITED:
-            return _megabytes(int(text))
+def own_cgroup(proc: Path = PROC_CGROUP) -> str | None:
+    """This process's cgroup (v2) as the kernel names it: `/` in a container (it sees its own
+    cgroup as the root), `/system.slice/swarmscribe-follower.service` under systemd. None
+    without cgroup v2 (Windows, macOS, a v1-only host)."""
+    try:
+        lines = proc.read_text(encoding="ascii").splitlines()
+    except (OSError, ValueError):
         return None
+    for line in lines:
+        hierarchy, _, rest = line.partition(":")
+        controllers, _, path = rest.partition(":")
+        if hierarchy == "0" and not controllers and path.startswith("/"):
+            return path
     return None
 
 
+def _limit_bytes(path: Path) -> int | None:
+    try:
+        text = path.read_text(encoding="ascii").strip()
+    except (OSError, ValueError):
+        return None
+    return int(text) if text.isdigit() and int(text) < _UNLIMITED else None
+
+
+def cgroup_limit_mb(
+    root: Path = CGROUP_ROOT, proc: Path = PROC_CGROUP, v1: Path = CGROUP_V1
+) -> int | None:
+    """The memory limit that binds this process, or None when there is none (or no cgroup:
+    Windows). On cgroup v2 it is the smallest `memory.max` of the process's own cgroup and of
+    every cgroup above it: a container's limit, a systemd unit's `MemoryMax=`, its slice's.
+    Reading the root's file alone (as the follower did before F4) sees a container's limit and
+    misses a unit's."""
+    own = own_cgroup(proc)
+    if own is not None:
+        parts = [part for part in own.split("/") if part]
+        if ".." in parts:  # a cgroup outside this namespace's root: only the root can be read
+            parts = []
+        found = []
+        for depth in range(len(parts), -1, -1):
+            value = _limit_bytes(root.joinpath(*parts[:depth]) / "memory.max")
+            if value is not None:
+                found.append(value)
+        if found:
+            return _megabytes(min(found))
+    value = _limit_bytes(v1)
+    return _megabytes(value) if value is not None else None
+
+
 def physical_mb() -> int | None:
     """The machine's memory, or None when the platform will not say."""
     if os.name == "nt":
@@ -66,9 +103,42 @@ def physical_mb() -> int | None:
         return None
 
 
+def _working_set_mb() -> int | None:
+    """Windows: the physical memory this process holds (its working set)."""
+    import ctypes
+    from ctypes import wintypes
+
+    class Counters(ctypes.Structure):
+        _fields_ = [("cb", wintypes.DWORD), ("page_faults", wintypes.DWORD)] + [
+            (name, ctypes.c_size_t)
+            for name in (
+                "peak_working_set", "working_set", "peak_paged", "paged",
+                "peak_non_paged", "non_paged", "pagefile", "peak_pagefile",
+            )
+        ]
+
+    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
+    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
+    kernel32.K32GetProcessMemoryInfo.argtypes = [
+        wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD,
+    ]
+    counters = Counters()
+    counters.cb = ctypes.sizeof(Counters)
+    if not kernel32.K32GetProcessMemoryInfo(
+        kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
+    ):
+        return None
+    return _megabytes(counters.working_set)
+
+
 def rss_mb() -> int | None:
     """What this process holds now (the loaded model is most of it), or None when the
-    platform will not say (then the guard counts the job alone)."""
+    platform will not say (macOS: then the guard counts the job alone)."""
+    if os.name == "nt":
+        try:
+            return _working_set_mb()
+        except (OSError, AttributeError):
+            return None
     try:
         with open("/proc/self/statm", encoding="ascii") as handle:
             return _megabytes(int(handle.read().split()[1]) * os.sysconf("SC_PAGE_SIZE"))
@@ -94,7 +164,7 @@ def find_limit(
         return Limit(configured, "SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB")
     found = [
         Limit(value, source)
-        for value, source in ((cgroup(), "the container's limit"), (physical(), "this machine"))
+        for value, source in ((cgroup(), CGROUP_SOURCE), (physical(), "this machine"))
         if value is not None and value >= MIN_LIMIT_MB
     ]
     return min(found, key=lambda limit: limit.megabytes) if found else None
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest packages/follower/tests/test_memory.py -q`
Expected: `32 passed`.

- [ ] **Step 5: A container's limit is still seen** (this task changed where the limit is read)

The follower Compose scenario limits its followers with `SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB`, which does not read the cgroup at all, so ask `doctor` in a container that has a memory limit. On the development machine (Git Bash, the Docker `PATH` line of Global Constraints):

```bash
docker build -t swarmscribe-follower:f4-cpu --target cpu -f docker/follower.Dockerfile .
out="$(MSYS_NO_PATHCONV=1 docker run --rm --memory 3g -e SWARMSCRIBE_LEADER_URL=https://leader.invalid swarmscribe-follower:f4-cpu doctor --no-model --no-leader)"
echo "$out"
docker rmi swarmscribe-follower:f4-cpu
```

Expected, among the lines (the planner saw exactly this): `memory: 3072 MiB may be used (the cgroup's limit: a container's, or a systemd unit's MemoryMax=)`.

- [ ] **Step 6: The gate, and commit**

Run: `uv run ruff check .` — expected `All checks passed!`. Run: `uv run pytest` once, to the end — expected no failure.

```bash
git add packages/follower/src/swarmscribe_follower/memory.py packages/follower/tests/test_memory.py
git commit -m "feat(follower): the memory guard reads its own cgroup (a unit's MemoryMax=) and, on Windows, its working set

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The credential's trust check on Windows

**Files:**
- Create: `packages/follower/src/swarmscribe_follower/winacl.py`
- Modify: `packages/follower/src/swarmscribe_follower/fsutil.py`
- Modify: `packages/follower/src/swarmscribe_follower/credentials.py`
- Test: `packages/follower/tests/test_winacl.py` (new)

**Interfaces:**
- Consumes: `fsutil.private_folder(path)`; `CredentialStore._folder_problem(folder, *, mode_matters=True)`, `check_folder(*, tighten=True)`, `_check_trust(folder, file)`, `load()`, `save()` (`credentials.py`).
- Produces (called on Windows only; each raises `OSError` when Windows refuses): `winacl.current_user() -> str` (a SID), `winacl.read_acl(path, descriptor_of=None) -> tuple[str, list[str] | None]`, `winacl.access_problem(path, *, what="folder", owner_only=False, acl=None) -> str | None`, `winacl.make_private(path) -> bool`, `winacl.SYSTEM`, `winacl.ADMINISTRATORS`, `winacl.OWNER_PLACEHOLDERS`. F4b's `service install` makes the service's folders with `icacls` and relies on this rule accepting a folder owned by Administrators whose list names Administrators, SYSTEM and the service's own account.

What Windows checks today: nothing. `_folder_problem` and `_check_trust` return at once when `os.name == "nt"`, and `private_folder` leaves the list a folder inherits. This task makes ruling 9 true. The tests are Windows-only and are skipped on Linux (so in CI until F4b's Windows job): they were run on the development machine, which is where you run them.

- [ ] **Step 1: Write the failing tests**

Create `packages/follower/tests/test_winacl.py`:

```python
"""The credential's trust check on Windows: access control lists, read and set for real. No
administrator rights are needed: every folder here is this account's own."""

import os
import subprocess

import pytest

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="Windows access control lists; POSIX modes are in test_credentials"
)

EVERYONE = "S-1-1-0"


def stored():
    from swarmscribe_follower.credentials import Stored

    return Stored(
        leader_url="https://leader.example.org", follower_id="f-1", credential="secret-1",
        device="cpu", heartbeat_interval=30, lease_seconds=120,
    )


def let_everyone_in(path) -> None:
    flags = "(OI)(CI)R" if path.is_dir() else "R"
    subprocess.run(
        ["icacls", str(path), "/grant", f"*{EVERYONE}:{flags}"], check=True, capture_output=True
    )


def test_this_account_has_a_sid_and_a_folder_it_made_private_is_trusted(tmp_path):
    from swarmscribe_follower import winacl
    from swarmscribe_follower.fsutil import private_folder

    assert winacl.current_user().startswith("S-1-5-")
    private_folder(tmp_path / "state")
    owner, allowed = winacl.read_acl(tmp_path / "state")
    assert owner == winacl.current_user()
    # Python makes a folder of mode 0700 with an access list of the owner, SYSTEM and
    # Administrators (3.12.4 and later); an older one inherits, and private_folder tightens.
    trusted = {winacl.current_user(), winacl.SYSTEM, winacl.ADMINISTRATORS}
    assert set(allowed) <= trusted | winacl.OWNER_PLACEHOLDERS
    assert winacl.access_problem(tmp_path / "state") is None


def test_a_folder_others_can_reach_is_refused_with_the_command_that_fixes_it(tmp_path):
    from swarmscribe_follower import winacl

    folder = tmp_path / "state"
    folder.mkdir()
    let_everyone_in(folder)
    problem = winacl.access_problem(folder)
    assert "can be reached by other accounts (Everyone (S-1-1-0))" in problem
    assert winacl.access_problem(folder, owner_only=True) is None
    command = problem.split("make it private with: ")[1]
    assert command.startswith(f'icacls "{folder}" /inheritance:r /grant:r')
    assert command.endswith(f'/remove "*{EVERYONE}"')
    subprocess.run(command, check=True, capture_output=True)  # the printed command works
    assert winacl.access_problem(folder) is None


def test_making_a_folder_private_shuts_everyone_else_out_of_it_and_of_what_is_in_it(tmp_path):
    from swarmscribe_follower import winacl

    folder = tmp_path / "state"
    folder.mkdir()
    (folder / "inside.txt").write_text("x", encoding="utf-8")
    let_everyone_in(folder)
    assert winacl.access_problem(folder / "inside.txt", what="file") is not None  # inherited
    assert winacl.make_private(folder) is True
    assert winacl.make_private(folder) is False  # nothing left to change
    assert winacl.access_problem(folder) is None
    assert winacl.access_problem(folder / "inside.txt", what="file") is None


def test_a_missing_path_is_an_oserror_not_a_crash(tmp_path):
    from swarmscribe_follower import winacl

    with pytest.raises(OSError):
        winacl.read_acl(tmp_path / "absent")


def test_before_registering_a_loose_folder_of_ones_own_is_made_private(tmp_path):
    from swarmscribe_follower import winacl
    from swarmscribe_follower.credentials import CredentialStore

    folder = tmp_path / "state"
    folder.mkdir()
    let_everyone_in(folder)
    store = CredentialStore(folder / "credential.json")
    store.check_folder(tighten=False)  # doctor changes nothing and passes what run will fix
    assert winacl.access_problem(folder) is not None
    store.check_folder()  # run and join: tightened, as chmod 700 is on POSIX
    assert winacl.access_problem(folder) is None
    store.save(stored())
    assert store.load() == stored()


def test_a_credential_in_a_folder_that_was_opened_to_others_is_refused(tmp_path):
    from swarmscribe_follower.credentials import CredentialFileError, CredentialStore

    store = CredentialStore(tmp_path / "state" / "credential.json")
    store.save(stored())
    let_everyone_in(tmp_path / "state")
    for check in (store.load, store.check_folder):
        with pytest.raises(CredentialFileError) as refused:
            check()
        assert "can be reached by other accounts" in str(refused.value)
        assert "secret-1" not in str(refused.value)


def test_a_folder_windows_will_not_describe_is_refused_not_a_crash(tmp_path, monkeypatch):
    from swarmscribe_follower import winacl
    from swarmscribe_follower.credentials import CredentialFileError, CredentialStore

    store = CredentialStore(tmp_path / "state" / "credential.json")
    store.save(stored())

    def denied(path, **kwargs):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(winacl, "access_problem", denied)
    with pytest.raises(CredentialFileError, match="cannot be checked .Access is denied."):
        store.check_folder()


def test_a_credential_file_others_can_read_is_refused(tmp_path):
    from swarmscribe_follower.credentials import CredentialFileError, CredentialStore

    store = CredentialStore(tmp_path / "state" / "credential.json")
    store.save(stored())
    let_everyone_in(store.path)
    with pytest.raises(CredentialFileError) as refused:
        store.load()
    assert f"the file {store.path} can be reached by other accounts" in str(refused.value)
```

- [ ] **Step 2: Run them to see them fail**

Run (on Windows): `uv run pytest packages/follower/tests/test_winacl.py -q`
Expected: `8 failed`: the tests that use `winacl` with `ImportError: cannot import name 'winacl' from 'swarmscribe_follower'`, the two that use only the credential store with `DID NOT RAISE` (nothing is checked yet). (On Linux: `8 skipped`.)

- [ ] **Step 3: The module**

Create `packages/follower/src/swarmscribe_follower/winacl.py`:

```python
"""Windows: who may reach the state folder and the credential file (follower spec 5.3).

POSIX mode bits mean nothing on NTFS, so on Windows the credential's trust check reads the
access control list instead. The rule is the POSIX one in Windows terms: the folder and the
file must belong to this account (or to SYSTEM or the Administrators group, which own what an
installer creates), and nobody but this account, SYSTEM and Administrators may be allowed in,
for anything. A folder under the user's profile (the default state folder) and one made by
`swarmscribe-follower service install` pass; a folder made at the root of a drive does not
(there `Authenticated Users` may write).

Only allow entries are read. A deny entry can only take access away, and the two entry kinds
Windows never puts on a file (object and callback entries) are ignored.

Every function here raises OSError when Windows refuses a call, and is called on Windows only."""

import ctypes
import functools
from pathlib import Path

SYSTEM = "S-1-5-18"
ADMINISTRATORS = "S-1-5-32-544"
# CREATOR OWNER and OWNER RIGHTS stand for whoever owns the object: the owner is checked.
OWNER_PLACEHOLDERS = frozenset({"S-1-3-0", "S-1-3-4"})
KNOWN = {
    "S-1-1-0": "Everyone",
    "S-1-5-11": "Authenticated Users",
    "S-1-5-32-545": "Users",
    "S-1-5-4": "Interactive",
    SYSTEM: "SYSTEM",
    ADMINISTRATORS: "Administrators",
}
_SE_FILE_OBJECT = 1
_DACL = 0x4
_OWNER_AND_DACL = 0x1 | _DACL
_PROTECTED_DACL = 0x80000000
_ACCESS_ALLOWED_ACE = 0
_TOKEN_QUERY = 0x8
_TOKEN_USER = 1


@functools.cache
def _api():
    from ctypes import wintypes

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    pointer = ctypes.POINTER(ctypes.c_void_p)
    advapi.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD, pointer, pointer, pointer, pointer, pointer,
    ]
    advapi.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi.GetSecurityInfo.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.DWORD, pointer, pointer, pointer, pointer, pointer,
    ]
    advapi.GetSecurityInfo.restype = wintypes.DWORD
    advapi.GetAclInformation.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.c_int,
    ]
    advapi.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, pointer]
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi.OpenProcessToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    return advapi, kernel


def _sid_text(advapi, kernel, sid: int) -> str:
    from ctypes import wintypes

    text = wintypes.LPWSTR()
    if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(text)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return text.value or ""
    finally:
        kernel.LocalFree(ctypes.cast(text, ctypes.c_void_p))


@functools.cache
def current_user() -> str:
    """The SID of the account this process runs as, as text (`S-1-5-21-...`)."""
    from ctypes import wintypes

    advapi, kernel = _api()
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        size = wintypes.DWORD()
        advapi.GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(
            token, _TOKEN_USER, buffer, size.value, ctypes.byref(size)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        # TOKEN_USER starts with a pointer to the SID.
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        return _sid_text(advapi, kernel, sid)
    finally:
        kernel.CloseHandle(token)


def read_acl(path: Path, descriptor_of: int | None = None) -> tuple[str, list[str] | None]:
    """(the owner's SID, the SIDs with an allow entry) for a file or folder. The list is None
    when the object has no access control list at all: then everyone may do anything.

    `descriptor_of`: an open file descriptor of `path`. The file is then read through it and
    not opened a second time: the answer is about the file that is being read, and another
    writer's replace of the credential is not held up by a second open."""
    import msvcrt
    from ctypes import wintypes

    advapi, kernel = _api()
    owner, dacl, descriptor = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    out = (ctypes.byref(owner), None, ctypes.byref(dacl), None, ctypes.byref(descriptor))
    if descriptor_of is None:
        error = advapi.GetNamedSecurityInfoW(str(path), _SE_FILE_OBJECT, _OWNER_AND_DACL, *out)
    else:
        handle = msvcrt.get_osfhandle(descriptor_of)
        error = advapi.GetSecurityInfo(handle, _SE_FILE_OBJECT, _OWNER_AND_DACL, *out)
    if error:
        raise ctypes.WinError(error)
    try:
        owner_sid = _sid_text(advapi, kernel, owner.value)
        if not dacl.value:
            return owner_sid, None
        counts = (wintypes.DWORD * 3)()  # ACL_SIZE_INFORMATION: the entry count comes first
        if not advapi.GetAclInformation(dacl, counts, ctypes.sizeof(counts), 2):
            raise ctypes.WinError(ctypes.get_last_error())
        allowed = []
        for index in range(counts[0]):
            entry = ctypes.c_void_p()
            if not advapi.GetAce(dacl, index, ctypes.byref(entry)):
                raise ctypes.WinError(ctypes.get_last_error())
            # ACE_HEADER is a type byte, a flags byte and a 16-bit size; an allow entry
            # follows it with a 32-bit mask and then the SID.
            kind = ctypes.cast(entry, ctypes.POINTER(ctypes.c_ubyte))[0]
            mask = ctypes.cast(entry.value + 4, ctypes.POINTER(wintypes.DWORD))[0]
            if kind == _ACCESS_ALLOWED_ACE and mask:
                allowed.append(_sid_text(advapi, kernel, entry.value + 8))
        return owner_sid, allowed
    finally:
        kernel.LocalFree(descriptor)


def _name(sid: str) -> str:
    return f"{KNOWN[sid]} ({sid})" if sid in KNOWN else sid


def access_problem(
    path: Path,
    *,
    what: str = "folder",
    owner_only: bool = False,
    acl: tuple[str, list[str] | None] | None = None,
) -> str | None:
    """None when `path` can be trusted with the credential; else why not, naming who else is
    allowed in and the command that makes it private. `owner_only`: look at the owner alone
    (`doctor` on a folder that `run` would make private before it uses it). `acl`: what
    `read_acl` already said of `path` (the credential file, read while it was open)."""
    me = current_user()
    owner, allowed = acl if acl is not None else read_acl(path)
    trusted = {me, SYSTEM, ADMINISTRATORS}
    fix = (
        f'icacls "{path}" /inheritance:r /grant:r "*{me}:(OI)(CI)F" "*{SYSTEM}:(OI)(CI)F"'
        f' "*{ADMINISTRATORS}:(OI)(CI)F"'
        if what == "folder"
        else f'icacls "{path}" /inheritance:r /grant:r "*{me}:F" "*{SYSTEM}:F"'
        f' "*{ADMINISTRATORS}:F"'
    )
    if owner not in trusted:
        return (
            f"the {what} {path} is owned by another account ({_name(owner)}) and will not be"
            " trusted with the credential; use a folder of this account's own (the default is"
            r" under %LOCALAPPDATA%)"
        )
    if owner_only:
        return None
    if allowed is None:
        return (
            f"the {what} {path} has no access control list (everyone may do anything) and will"
            f" not be trusted with the credential; run: {fix}"
        )
    others = sorted({sid for sid in allowed if sid not in trusted | OWNER_PLACEHOLDERS})
    if others:
        removes = " ".join(f'/remove "*{sid}"' for sid in others)
        return (
            f"the {what} {path} can be reached by other accounts"
            f" ({', '.join(_name(sid) for sid in others)}) and will not be trusted with the"
            f" credential; make it private with: {fix} {removes}"
        )
    return None


def make_private(path: Path) -> bool:
    """What `chmod 700` is on POSIX: when the folder `path` is this account's own and others
    are allowed in, replace its access control list with one that names this account, SYSTEM
    and Administrators only, inherited by everything inside it, and inheriting nothing from
    above. True when it was changed. A folder that is someone else's is left alone (the
    callers that trust a folder refuse it by themselves)."""
    from ctypes import wintypes

    me = current_user()
    owner, allowed = read_acl(path)
    if owner != me:
        return False
    if allowed is not None and set(allowed) <= {me, SYSTEM, ADMINISTRATORS} | OWNER_PLACEHOLDERS:
        return False
    advapi, kernel = _api()
    convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
    ]
    advapi.GetSecurityDescriptorDacl.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.BOOL),
    ]
    advapi.SetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p,
    ]
    advapi.SetNamedSecurityInfoW.restype = wintypes.DWORD
    # D:P = a protected list (nothing inherited); FA = full access; OICI = files and folders
    # inside inherit it; SY and BA are SYSTEM and Administrators.
    text = f"D:P(A;OICI;FA;;;{me})(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"
    descriptor = ctypes.c_void_p()
    if not convert(text, 1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        present, defaulted, dacl = wintypes.BOOL(), wintypes.BOOL(), ctypes.c_void_p()
        if not advapi.GetSecurityDescriptorDacl(
            descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        error = advapi.SetNamedSecurityInfoW(
            str(path), _SE_FILE_OBJECT, _DACL | _PROTECTED_DACL, None, None, dacl, None
        )
        if error:
            raise ctypes.WinError(error)
    finally:
        kernel.LocalFree(descriptor)
    return True
```

- [ ] **Step 4: `private_folder` makes a folder private on Windows too**

In `packages/follower/src/swarmscribe_follower/fsutil.py`:

```diff
--- a/packages/follower/src/swarmscribe_follower/fsutil.py
+++ b/packages/follower/src/swarmscribe_follower/fsutil.py
@@ -11,7 +11,8 @@ def private_folder(path: Path) -> None:
     A new folder is created 0700 (its parents, if missing, get the default mode). An existing
     folder that belongs to this user and is looser is tightened. One that belongs to someone
     else is left alone: the callers that trust a folder refuse it by themselves. On Windows
-    the folder keeps the ACL it inherits."""
+    the same is done with the folder's access control list (winacl.make_private): this
+    account, SYSTEM and Administrators, and nobody else."""
     path = Path(path)
     try:
         info = os.lstat(path)
@@ -24,7 +25,12 @@ def private_folder(path: Path) -> None:
         else:
             return
         info = os.lstat(path)
-    if os.name == "nt" or not stat.S_ISDIR(info.st_mode):
+    if not stat.S_ISDIR(info.st_mode):
+        return
+    if os.name == "nt":
+        from . import winacl
+
+        winacl.make_private(path)
         return
     if info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) & 0o077:
         os.chmod(path, 0o700)
```

- [ ] **Step 5: The credential store asks `winacl` where it asks the mode bits**

In `packages/follower/src/swarmscribe_follower/credentials.py`. The file's list is read through the already open file, and the checks run after it is closed: on Windows a second open by name would hold up another writer's replace (measured: the concurrent-writers test went from 1 s to several, and a writer sometimes gave up).

```diff
--- a/packages/follower/src/swarmscribe_follower/credentials.py
+++ b/packages/follower/src/swarmscribe_follower/credentials.py
@@ -2,12 +2,9 @@
 
 One JSON file in the state folder, readable by its owner only. On POSIX a file or folder
 that another user owns, or that others can write to, is refused: it could have been planted.
-On Windows the folder relies on the account's profile permissions (POSIX modes do not apply
-there), as the admin CLI's sign-in cache does. Nothing here prints the credential, and its
-repr is hidden.
-
-On Windows nothing here restricts the file beyond the folder's inherited ACL (by default
-the user's profile: the user, SYSTEM and administrators); the 0600 mode is ignored there."""
+On Windows the same rule is applied to the access control list (winacl.py): the folder and
+the file may be reachable only by this account, SYSTEM and Administrators. Nothing here
+prints the credential, and its repr is hidden."""
 
 import json
 import os
@@ -55,10 +52,20 @@ class CredentialStore:
         """The rule for the folder, in one place: it must be this user's, and nobody else may
         be able to write to it. None when it can be trusted; else why not, naming the folder,
         its owner and mode, and what to do."""
+        where = self.path.parent
         if os.name == "nt":
-            return None
+            from . import winacl
+
+            try:
+                return winacl.access_problem(where, owner_only=not mode_matters)
+            except OSError as exc:
+                # Windows will not even say who owns it: not a folder to keep a secret in.
+                return (
+                    f"the folder {where} cannot be checked"
+                    f" ({exc.strerror or type(exc).__name__}) and will not be trusted with the"
+                    " credential; use a folder of this account's own"
+                )
         me = os.getuid()
-        where = self.path.parent
         found = f"owner uid {folder.st_uid}, mode {stat.S_IMODE(folder.st_mode):04o}"
         if folder.st_uid != me:
             return (
@@ -101,12 +108,18 @@ class CredentialStore:
         if problem is not None:
             raise CredentialFileError(problem)
 
-    def _check_trust(self, folder: os.stat_result, file: os.stat_result) -> None:
-        if os.name == "nt":
-            return
+    def _check_trust(self, folder: os.stat_result, file: os.stat_result, acl=None) -> None:
+        """`file` is the open credential file's fstat; on Windows `acl` is its access control
+        list, read through the same open file."""
         problem = self._folder_problem(folder)
+        if problem is None and os.name == "nt":
+            from . import winacl
+
+            problem = winacl.access_problem(self.path, what="file", acl=acl)
         if problem is not None:
             raise CredentialFileError(problem)
+        if os.name == "nt":
+            return
         if file.st_uid != os.getuid():
             raise CredentialFileError(
                 f"{self.path} is owned by another user and will not be trusted; delete it"
@@ -147,9 +160,17 @@ class CredentialStore:
             # O_NOFOLLOW closes the window between the lstat and the open; the checks then run
             # on the opened descriptor, so they describe the file that is actually read.
             handle = self._open_for_reading()
+            acl = None
             with os.fdopen(handle, "rb") as source:
-                self._check_trust(self.path.parent.stat(), os.fstat(source.fileno()))
+                # Kept short: on Windows another writer cannot replace the file while this
+                # one has it open. What was read is used only if the checks below pass.
+                file = os.fstat(source.fileno())
+                if os.name == "nt":
+                    from . import winacl
+
+                    acl = winacl.read_acl(self.path, source.fileno())
                 raw = source.read()
+            self._check_trust(self.path.parent.stat(), file, acl)
             data = json.loads(raw.decode("utf-8"))
         except OSError as exc:
             raise CredentialFileError(
```

Also replace the module docstring's last two paragraphs' claims about Windows. The docstring's first paragraph says "On Windows the folder relies on the account's profile permissions (POSIX modes do not apply there), as the admin CLI's sign-in cache does", and the second says "On Windows nothing here restricts the file beyond the folder's inherited ACL ...". Replace both sentences with: "On Windows the same rule is applied to the access control list (winacl.py): the folder and the file may be reachable only by this account, SYSTEM and Administrators." (The diff above contains this edit; it is spelled out here because a docstring is easy to leave stale.)

- [ ] **Step 6: Run the tests**

Run (on Windows): `uv run pytest packages/follower/tests/test_winacl.py packages/follower/tests/test_credentials.py packages/follower/tests/test_scratch.py -q`
Expected: on Windows `44 passed, 4 skipped` (the four are POSIX-mode tests); on Linux `37 passed, 11 skipped` (the eight tests of `test_winacl.py` among the skipped). Run `uv run pytest packages/follower/tests/test_credentials.py -q -k writers` three times: each `1 passed` in about a second, with no warning (a `PytestUnhandledThreadExceptionWarning` naming `Access is denied` means the credential file is being opened a second time: Step 5 was not applied as written).

- [ ] **Step 7: The images still run the same agent — the follower Compose scenario**

Tasks 1 to 4 changed modules that the images run (`models.py`, `main.py`, `entry.py`, `memory.py`, `fsutil.py`, `credentials.py`). The follower Compose scenario is the proof that they still work there. On the development machine (Git Bash, the Docker `PATH` line of Global Constraints). **First** make sure no other agent is running that Compose project, which has one fixed name and publishes port 15432:

```bash
names="$(docker ps -a --format '{{.Names}}')"; echo "$names" | grep swarmscribe-follower-e2e || echo "free"
```

Expected: `free`. If containers are listed and you did not start them, wait and ask the controller; do not stop them. Then (both images get tags of this plan's own, so that nobody's `swarmscribe-leader:e2e` or `swarmscribe-follower:e2e` is replaced; the leader's image is kept for Task 7):

```bash
export UV="python -m uv" FOLLOWER_IMAGE=swarmscribe-follower:f4-e2e LEADER_IMAGE=swarmscribe-leader:f4-e2e
docker build -t swarmscribe-leader:f4-e2e -f e2e/compose/Dockerfile .
docker build --build-arg MODELS=tiny.en -t swarmscribe-follower:f4-e2e --target cpu -f docker/follower.Dockerfile .
bash docker/check-follower-image.sh swarmscribe-follower:f4-e2e cpu tiny.en
python -m uv run python e2e/follower-compose/run_e2e.py prepare
docker compose -f e2e/follower-compose/docker-compose.yml up -d
python -m uv run python e2e/follower-compose/run_e2e.py run
docker compose -f e2e/follower-compose/docker-compose.yml --profile followers down -v
docker rmi swarmscribe-follower:f4-e2e
```

Expected (the planner saw this with every change of F4a and F4b, in 1 min 57 s; the figures differ a little): the check script ends `ok: swarmscribe-follower:f4-e2e is a cpu follower with tiny.en baked in (938 MB)`, and the scenario ends with one line that starts `passed (tiny.en on cpu): two followers registered in` and goes on `... a stop mid-job took 2.1 s and counted no attempt; drain exited 0 and revoke exited 4, twice each`. A line that starts `FAILED:` names the step: fix the cause in the follower (with a test), not in the scenario.

- [ ] **Step 8: The gate, and commit**

Run: `uv run ruff check .` — expected `All checks passed!`. Run: `uv run pytest` once, to the end — expected no failure. On Windows this is also the proof that no existing test relied on an unchecked folder.

```bash
git add packages/follower/src/swarmscribe_follower/winacl.py packages/follower/src/swarmscribe_follower/fsutil.py packages/follower/src/swarmscribe_follower/credentials.py packages/follower/tests/test_winacl.py
git commit -m "feat(follower): on Windows the state folder and the credential are checked by their access control list

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: The systemd unit, its settings example and the constraints file

**Files:**
- Create: `deploy/systemd/swarmscribe-follower.service`
- Create: `deploy/systemd/follower.env.example`
- Create: `deploy/follower-constraints.txt` (generated)
- Modify: `.gitattributes`
- Test: `packages/follower/tests/test_native_files.py` (new)

**Interfaces:**
- Consumes: the command `swarmscribe-follower --env-file PATH run` (Task 2); the exit codes; `uv.lock`.
- Produces: the unit `swarmscribe-follower.service` (user and group `swarmscribe-follower`; `ExecStart=/usr/local/bin/swarmscribe-follower --env-file /etc/swarmscribe-follower/follower.env run`; `StateDirectory=swarmscribe-follower`); the settings example with `SWARMSCRIBE_JOIN_TOKEN_FILE=/etc/swarmscribe-follower/join-token`, `SWARMSCRIBE_FOLLOWER_STATE_DIR=/var/lib/swarmscribe-follower/state`, `SWARMSCRIBE_FOLLOWER_MODEL_DIR=/var/lib/swarmscribe-follower/models`, `SWARMSCRIBE_FOLLOWER_SHUTDOWN_GRACE_SECONDS=900`; `deploy/follower-constraints.txt`. Task 6's driver installs exactly these; F4b's Windows proof uses the constraints file.

- [ ] **Step 1: Write the failing tests**

Create `packages/follower/tests/test_native_files.py`:

```python
"""What the native install ships: the systemd unit, its settings example and the constraints
file. Static checks; e2e/follower-systemd runs the unit under a real systemd."""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
UNIT = (ROOT / "deploy" / "systemd" / "swarmscribe-follower.service").read_text(encoding="utf-8")
EXAMPLE = (ROOT / "deploy" / "systemd" / "follower.env.example").read_text(encoding="utf-8")
CONSTRAINTS = ROOT / "deploy" / "follower-constraints.txt"


def settings(text: str) -> dict[str, list[str]]:
    """`Name=value` lines of a unit or a settings file; a name may come more than once."""
    found: dict[str, list[str]] = {}
    for line in text.splitlines():
        if line and not line.startswith(("#", "[")) and "=" in line:
            name, _, value = line.partition("=")
            found.setdefault(name, []).append(value)
    return found


def test_what_a_linux_machine_reads_has_no_carriage_returns():
    """A unit with CRLF line ends does not load. `.gitattributes` pins these files to LF,
    whatever `core.autocrlf` says on the machine that checks them out."""
    folder = ROOT / "deploy" / "systemd"
    for path in (*sorted(folder.iterdir()), CONSTRAINTS):
        assert b"\r" not in path.read_bytes(), f"{path.name} has CRLF line ends"
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "deploy/systemd/* text eol=lf" in attributes


def test_the_unit_restarts_on_failure_but_never_a_revoked_follower_and_not_for_ever():
    unit = settings(UNIT)
    assert unit["Restart"] == ["on-failure"]  # exit 0 (stopped, drained) is final
    assert unit["RestartPreventExitStatus"] == ["4 5"]
    assert (unit["StartLimitBurst"], unit["StartLimitIntervalSec"]) == (["5"], ["600"])
    # Five starts must fit the interval, or the limit never bites.
    assert 5 * int(unit["RestartSec"][0]) < int(unit["StartLimitIntervalSec"][0])


def test_the_stop_timeout_is_the_grace_period_and_thirty_seconds():
    grace = int(settings(EXAMPLE)["SWARMSCRIBE_FOLLOWER_SHUTDOWN_GRACE_SECONDS"][0])
    assert int(settings(UNIT)["TimeoutStopSec"][0]) == grace + 30 == 930


def test_the_unit_runs_as_its_own_user_from_the_settings_file_and_holds_no_secret():
    unit = settings(UNIT)
    assert unit["User"] == ["swarmscribe-follower"]
    assert unit["ExecStart"] == [
        "/usr/local/bin/swarmscribe-follower --env-file /etc/swarmscribe-follower/follower.env run"
    ]
    assert (unit["StateDirectory"], unit["StateDirectoryMode"]) == (
        ["swarmscribe-follower"], ["0700"]
    )
    assert unit["NoNewPrivileges"] == ["yes"] and unit["ProtectSystem"] == ["strict"]
    # No setting of the follower's in the unit at all: they live in one place, the file.
    assert "Environment" not in unit and "EnvironmentFile" not in unit
    assert not [name for name in unit if name.startswith("SWARMSCRIBE_")]
    # No health listener: a native install opens no port (follower spec D18).
    assert "HEALTH_ADDR" not in UNIT and "HEALTH_ADDR" not in EXAMPLE


def test_the_settings_example_names_a_token_file_and_holds_no_token():
    example = settings(EXAMPLE)
    assert example["SWARMSCRIBE_JOIN_TOKEN_FILE"] == ["/etc/swarmscribe-follower/join-token"]
    assert "SWARMSCRIBE_JOIN_TOKEN" not in example
    state = example["SWARMSCRIBE_FOLLOWER_STATE_DIR"][0]
    assert state.startswith("/var/lib/swarmscribe-follower/")  # inside the unit's StateDirectory


def test_the_constraints_are_the_lock_files_versions():
    """`uv tool install --constraints deploy/follower-constraints.txt` gives an outside
    machine the versions CI tested. Regenerate it when uv.lock changes (the README says how)."""
    with (ROOT / "uv.lock").open("rb") as source:
        locked = {
            package["name"]: package["version"]
            for package in tomllib.load(source)["package"]
            if "version" in package
        }
    pins = dict(
        re.match(r"([a-z0-9_.-]+)==([^\s;]+)", line).groups()
        for line in CONSTRAINTS.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith(("#", " "))
    )
    for needed in ("ctranslate2", "faster-whisper", "av", "httpx", "nvidia-cublas-cu12"):
        assert needed in pins, f"{needed} is not pinned"
    assert not [name for name in pins if name.startswith("swarmscribe-")]  # those are the wheels
    assert {name: locked.get(name) for name in pins} == pins
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/follower/tests/test_native_files.py -q`
Expected: an error while collecting, `FileNotFoundError` for `deploy/systemd/swarmscribe-follower.service`.

- [ ] **Step 3: Line endings first**

Append to `.gitattributes` (it has `*.json text eol=lf` and `*.sh text eol=lf`), **before** the files below are created, so that they are never checked out with CRLF:

```
# What a Linux machine reads as it is: a unit with CRLF does not load.
deploy/systemd/* text eol=lf
deploy/follower-constraints.txt text eol=lf
```

- [ ] **Step 4: The unit**

Create `deploy/systemd/swarmscribe-follower.service`:

```ini
# SwarmScribe follower as a systemd service (follower spec 8.3). The README's section
# "Run a follower on an outside machine" has the install steps; this file goes to
# /etc/systemd/system/swarmscribe-follower.service.
#
# Settings are read from /etc/swarmscribe-follower/follower.env by the follower itself
# (--env-file), so that `doctor` and `leave` run by hand use the same file. The join token is
# never in this unit, in that file or on a command line: it is a file of its own, named there
# by SWARMSCRIBE_JOIN_TOKEN_FILE and readable by the service's user only.
[Unit]
Description=SwarmScribe follower
Documentation=https://github.com/iamfatness/SwarmScribe
Wants=network-online.target
After=network-online.target
# Exit 2 (configuration) and 3 (this machine cannot do the work) are restarted, but not for
# ever: five starts in ten minutes and systemd leaves the unit failed, with the reason as the
# last line of its journal.
StartLimitIntervalSec=600
StartLimitBurst=5

[Service]
Type=exec
User=swarmscribe-follower
Group=swarmscribe-follower
ExecStart=/usr/local/bin/swarmscribe-follower --env-file /etc/swarmscribe-follower/follower.env run

# /var/lib/swarmscribe-follower: the credential, scratch and the model cache.
StateDirectory=swarmscribe-follower
StateDirectoryMode=0700
WorkingDirectory=/var/lib/swarmscribe-follower

# Exit 0 is final: stopped, or drained. Exit 4 (revoked, or the token was refused) and 5 (the
# leader speaks another protocol) are never restarted: a restart cannot cure them.
Restart=on-failure
RestartSec=30
RestartPreventExitStatus=4 5

# A stop is SIGTERM to the follower. It finishes the recording in hand if that fits
# SWARMSCRIBE_FOLLOWER_SHUTDOWN_GRACE_SECONDS (900 in follower.env) and hands it back
# otherwise; 30 seconds more and systemd kills it.
KillMode=mixed
TimeoutStopSec=930

NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
LockPersonality=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
UMask=0077

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 5: The settings example**

Create `deploy/systemd/follower.env.example`:

```
# Settings of the SwarmScribe follower service: copy to /etc/swarmscribe-follower/follower.env
# (root:swarmscribe-follower, mode 0640). NAME=value per line; nothing is expanded. The README
# lists every setting.
#
# The join or pool token is NOT written here. Put it, alone, in the file below
# (root:swarmscribe-follower, mode 0640). It is read once, when the follower first registers;
# after that the file may be emptied.
SWARMSCRIBE_LEADER_URL=https://leader.example.org
SWARMSCRIBE_JOIN_TOKEN_FILE=/etc/swarmscribe-follower/join-token
SWARMSCRIBE_FOLLOWER_STATE_DIR=/var/lib/swarmscribe-follower/state
SWARMSCRIBE_FOLLOWER_MODEL_DIR=/var/lib/swarmscribe-follower/models
# A stop waits this long for the recording in hand before it gives it back. Keep it 30
# seconds under the unit's TimeoutStopSec.
SWARMSCRIBE_FOLLOWER_SHUTDOWN_GRACE_SECONDS=900
```

- [ ] **Step 6: The constraints file**

Run, from the repository root:

```bash
uv export --frozen --no-dev --no-emit-workspace --package swarmscribe-follower --extra cuda --no-hashes -o deploy/follower-constraints.txt
```

Expected: a file of about 100 lines whose first two lines are a comment naming this command, and which holds among others `av==18.1.0`, `ctranslate2==4.8.2`, `faster-whisper==1.2.1`, `nvidia-cublas-cu12==12.9.2.10 ; sys_platform == 'linux' or sys_platform == 'win32'`, and no line that starts with `swarmscribe-`. It is generated from `uv.lock`, never edited by hand; the test of Step 1 fails when the two disagree.

- [ ] **Step 7: Run the tests**

Run: `uv run pytest packages/follower/tests/test_native_files.py -q`
Expected: `6 passed`.

- [ ] **Step 8: The gate, and commit**

Run: `uv run ruff check .` — expected `All checks passed!`. Run: `uv run pytest` once, to the end — expected no failure.

```bash
git add .gitattributes deploy/systemd/swarmscribe-follower.service deploy/systemd/follower.env.example deploy/follower-constraints.txt packages/follower/tests/test_native_files.py
git commit -m "feat(deploy): a systemd unit for the follower, its settings example, and the lock file's versions as constraints

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: The systemd test — a machine with systemd, and the driver

This task writes the test; Task 7 runs it. Nothing here needs Docker.

**Files:**
- Create: `e2e/follower-systemd/Dockerfile`
- Create: `e2e/follower-systemd/run_e2e.py`
- Modify: `.gitignore`, `.dockerignore`

**Interfaces:**
- Consumes: the unit, the settings example and the constraints file (Task 5); `--env-file` (Task 2); the cgroup limit (Task 3); the leader's test image (`e2e/compose/Dockerfile`, tagged `swarmscribe-leader:e2e`) and `e2e/follower-kind/admin.py`, whose commands are `pool-token <name> <pool>`, `profile <device> <model> <compute type>`, `location <name> <mode>`, `recording <location> <key> <repeats>`, `silence <location> <key> <seconds>`, `drain <follower id>`, `revoke-pool <name>` and `state` (each prints one JSON value).
- Produces: `python e2e/follower-systemd/run_e2e.py up | run | down`. Environment: `E2E_PREFIX` (default `follower-systemd`; containers `<prefix>-postgres`, `<prefix>-leader`, `<prefix>-machine`, network `<prefix>-net`, image `<prefix>:e2e`), `LEADER_IMAGE` (`swarmscribe-leader:e2e`), `UV` (`uv`). `run` ends with a line that starts `passed (tiny.en on cpu, systemd `. Task 7 runs these and records what they print.

- [ ] **Step 1: The machine**

Create `e2e/follower-systemd/Dockerfile`:

```dockerfile
# A machine with systemd as PID 1, for the test of the native Linux install
# (e2e/follower-systemd/run_e2e.py). It holds systemd, curl and uv, and nothing of SwarmScribe:
# the test installs the follower in it the way the README tells an operator to.
FROM debian:12-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends systemd systemd-sysv ca-certificates curl procps \
    && rm -rf /var/lib/apt/lists/* \
    && curl -LsSf https://astral.sh/uv/0.12.22/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_UNMANAGED_INSTALL=1 sh \
    && systemctl mask systemd-logind.service getty.target console-getty.service
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
```

- [ ] **Step 2: The driver**

Create `e2e/follower-systemd/run_e2e.py`:

```python
"""The native Linux install of the follower, under a real systemd, against a real leader
(follower spec 8.3; plan F4a).

There is no Linux host to install on, so the "machine" is a container that runs systemd as
PID 1 (e2e/follower-systemd/Dockerfile: Debian 12, systemd, curl and uv, nothing of
SwarmScribe). The driver installs the follower in it exactly as the README tells an operator
to (wheels built from this repository, `uv tool install`, a service user, the two files in
/etc/swarmscribe-follower, the unit from deploy/systemd) and then checks what the unit does
when it is stopped, killed, drained, revoked, short of memory and unable to work.

    uv run python e2e/follower-systemd/run_e2e.py up      # wheels, images, leader, the machine
    uv run python e2e/follower-systemd/run_e2e.py run     # install, then the scenario
    uv run python e2e/follower-systemd/run_e2e.py down    # remove the containers and the network

`run` needs a fresh `up` each time (it drains and revokes). The leader is the leader's test
image with Postgres beside it, administered with the leader's own functions through
`docker exec` (e2e/follower-kind/admin.py), as the kind test does. Nothing here prints a
token, a credential or a link.

Environment: E2E_PREFIX names the containers, the network and the image (default
`follower-systemd`); LEADER_IMAGE is the leader's test image (`swarmscribe-leader:e2e`); UV is
how uv is run (`uv`; on the development machine `python -m uv`)."""

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DIST = HERE / "work" / "dist"
ADMIN = ROOT / "e2e" / "follower-kind" / "admin.py"
UNIT = ROOT / "deploy" / "systemd" / "swarmscribe-follower.service"
ENV_EXAMPLE = ROOT / "deploy" / "systemd" / "follower.env.example"
CONSTRAINTS = ROOT / "deploy" / "follower-constraints.txt"
PREFIX = os.environ.get("E2E_PREFIX", "follower-systemd")
NETWORK, POSTGRES, LEADER, MACHINE = (
    f"{PREFIX}-net", f"{PREFIX}-postgres", f"{PREFIX}-leader", f"{PREFIX}-machine",
)
MACHINE_IMAGE = f"{PREFIX}:e2e"
LEADER_IMAGE = os.environ.get("LEADER_IMAGE", "swarmscribe-leader:e2e")
UV = shlex.split(os.environ.get("UV", "uv"))
SERVICE = "swarmscribe-follower"
ENV_FILE = "/etc/swarmscribe-follower/follower.env"
DROP_IN = f"/etc/systemd/system/{SERVICE}.service.d/test.conf"
LEADER_ENV = {
    "SWARMSCRIBE_DATABASE_URL": f"postgresql://postgres:postgres@{POSTGRES}:5432/swarmscribe",
    # The leader builds its own file links from this, and the follower fetches them from the
    # machine: it must be the leader's name on the network (follower spec 12.6).
    "SWARMSCRIBE_PUBLIC_URL": f"http://{LEADER}:8080",
    "SWARMSCRIBE_LINK_KEY": "follower-systemd-link-key-0123456789abcdef",
    "SWARMSCRIBE_LEASE_SECONDS": "8",
    "SWARMSCRIBE_HEARTBEAT_SECONDS": "2",
    "SWARMSCRIBE_REAPER_INTERVAL_SECONDS": "1",
    "SWARMSCRIBE_SCANNER_INTERVAL_SECONDS": "1",
    "SWARMSCRIBE_CLAIM_RETRY_AFTER": "1",
    "SWARMSCRIBE_FOLLOWER_GONE_AFTER_SECONDS": "20",
}
INSTALL_ENV = {
    "UV_TOOL_DIR": "/opt/swarmscribe-follower/tools",
    "UV_TOOL_BIN_DIR": "/usr/local/bin",
    "UV_PYTHON_INSTALL_DIR": "/opt/swarmscribe-follower/python",
    "UV_COMPILE_BYTECODE": "1",
}
# Postgres needs a moment after its container starts.
MIGRATE = "for i in $(seq 60); do swarmscribe-leader migrate && exit 0; sleep 2; done; exit 1"
LONG_REPEATS = 96  # the 5-second speech fixture 96 times: eight minutes
STEP_SECONDS = 180.0


def expect(condition: bool, message: str) -> None:
    if not condition:
        print(f"FAILED: {message}", file=sys.stderr)
        raise SystemExit(1)


def run(*command: str, stdin: str | None = None, check: bool = True) -> str:
    done = subprocess.run(command, input=stdin, capture_output=True, text=True)
    if check and done.returncode != 0:
        said = (done.stdout + done.stderr).strip()[-1500:]
        expect(False, f"`{' '.join(command[:6])} ...` exited {done.returncode}: {said}")
    return done.stdout


def docker(*arguments: str, **kwargs) -> str:
    return run("docker", *arguments, **kwargs)


def machine(script: str, *, stdin: str | None = None, check: bool = True) -> str:
    """Run a shell script as root in the machine."""
    return docker("exec", "-i", MACHINE, "bash", "-ec", script, stdin=stdin, check=check)


def admin(*arguments: str):
    source = ADMIN.read_text(encoding="utf-8")
    return json.loads(docker("exec", "-i", LEADER, "python", "-", *arguments, stdin=source))


def wait(what: str, probe, seconds: float = STEP_SECONDS, every: float = 1.0):
    deadline = time.monotonic() + seconds
    while True:
        found = probe()
        if found:
            return found
        expect(time.monotonic() < deadline, f"timed out after {seconds:.0f} s waiting for {what}")
        time.sleep(every)


def unit() -> dict[str, str]:
    shown = machine(
        f"systemctl show {SERVICE} -p ActiveState -p SubState -p Result -p ExecMainStatus"
        " -p NRestarts -p MainPID"
    )
    return dict(line.split("=", 1) for line in shown.splitlines() if "=" in line)


def journal() -> str:
    return machine(f"journalctl -u {SERVICE} --no-pager -o cat")


def job(key: str) -> dict:
    return admin("state")["jobs"].get(f"talks/{key}", {"state": "absent", "tried": []})


def followers(state: str) -> list[dict]:
    return [row for row in admin("state")["followers"] if row["state"] == state]


def queue(key: str, repeats: int) -> None:
    admin("recording", "talks", key, str(repeats))


def held(key: str, seconds: float) -> None:
    """Wait until the follower holds `key`, and then `seconds` more: it is in the middle."""
    wait(f"{key} to be leased", lambda: job(key)["state"] == "leased")
    time.sleep(seconds)
    expect(job(key)["state"] == "leased", f"{key} was not held long enough to be interrupted")


def set_env(name: str, value: str) -> None:
    machine(f"sed -i '/^{name}=/d' {ENV_FILE}; echo '{name}={value}' >> {ENV_FILE}")


# --- up and down ------------------------------------------------------------------------------


def up() -> None:
    DIST.mkdir(parents=True, exist_ok=True)
    for old in DIST.glob("*.whl"):
        old.unlink()
    for package in ("protocol", "engine", "follower"):
        run(*UV, "build", "--package", f"swarmscribe-{package}", "--wheel", "-o", str(DIST))
    docker("build", "-t", MACHINE_IMAGE, "-f", str(HERE / "Dockerfile"), str(HERE))
    down()
    docker("network", "create", NETWORK)
    docker(
        "run", "-d", "--name", POSTGRES, "--network", NETWORK,
        "-e", "POSTGRES_PASSWORD=postgres", "-e", "POSTGRES_DB=swarmscribe", "postgres:16",
    )
    leader_env = [part for name, value in LEADER_ENV.items() for part in ("-e", f"{name}={value}")]
    docker(
        "run", "--rm", "--network", NETWORK, *leader_env, LEADER_IMAGE,
        "sh", "-c", MIGRATE,
    )
    docker("run", "-d", "--name", LEADER, "--network", NETWORK, *leader_env, LEADER_IMAGE)
    ready = (
        "import urllib.request as u; "
        "print(u.urlopen('http://localhost:8080/readyz', timeout=2).status)"
    )
    wait(
        "the leader to be ready",
        lambda: "200" in docker("exec", LEADER, "python", "-c", ready, check=False),
        60,
    )
    # systemd as PID 1 needs a private cgroup namespace it may write to, and /run as tmpfs.
    docker(
        "run", "-d", "--name", MACHINE, "--network", NETWORK, "--privileged",
        "--cgroupns=private", "--tmpfs", "/run", "--tmpfs", "/run/lock", MACHINE_IMAGE,
    )
    wait(
        "systemd to be running in the machine",
        lambda: machine("systemctl is-system-running", check=False).strip()
        in ("running", "degraded"),
        60,
    )
    version = machine("systemctl --version").splitlines()[0]
    print(f"up: a leader at http://{LEADER}:8080 and a machine with {version}")


def down() -> None:
    for name in (MACHINE, LEADER, POSTGRES):
        docker("rm", "-f", "-v", name, check=False)
    docker("network", "rm", NETWORK, check=False)


# --- the install, as the README has it ----------------------------------------------------------


def install(token: str) -> None:
    machine("mkdir -p /dist")
    docker("cp", f"{DIST}{os.sep}.", f"{MACHINE}:/dist/")
    docker("cp", str(CONSTRAINTS), f"{MACHINE}:/dist/follower-constraints.txt")
    exports = " ".join(f"{name}={value}" for name, value in INSTALL_ENV.items())
    machine(
        f"{exports} uv tool install --python 3.12 --find-links /dist"
        " --constraints /dist/follower-constraints.txt swarmscribe-follower"
    )
    machine(
        "useradd --system --home-dir /var/lib/swarmscribe-follower --shell /usr/sbin/nologin"
        " swarmscribe-follower\n"
        "install -d -o root -g swarmscribe-follower -m 0750 /etc/swarmscribe-follower"
    )
    docker("cp", str(ENV_EXAMPLE), f"{MACHINE}:/tmp/follower.env")
    docker("cp", str(UNIT), f"{MACHINE}:/tmp/{SERVICE}.service")
    machine(
        f"install -o root -g swarmscribe-follower -m 0640 /tmp/follower.env {ENV_FILE}\n"
        f"install -o root -g root -m 0644 /tmp/{SERVICE}.service /etc/systemd/system/\n"
        "umask 027; cat > /etc/swarmscribe-follower/join-token\n"
        "chgrp swarmscribe-follower /etc/swarmscribe-follower/join-token",
        stdin=token,
    )
    set_env("SWARMSCRIBE_LEADER_URL", f"http://{LEADER}:8080")
    set_env("SWARMSCRIBE_FOLLOWER_ALLOW_HTTP", "1")  # the test leader is plain http
    set_env("SWARMSCRIBE_FOLLOWER_STARTUP_MODEL", "tiny.en")
    # Test-only: two cores, so that eight minutes of audio take long enough to interrupt;
    # and a restart after 2 s instead of the unit's 30.
    machine(
        f"mkdir -p $(dirname {DROP_IN})\n"
        f"printf '[Service]\\nCPUQuota=200%%\\nRestartSec=2\\n' > {DROP_IN}\n"
        f"systemctl daemon-reload\nsystemd-analyze verify /etc/systemd/system/{SERVICE}.service\n"
        f"systemctl enable --now {SERVICE}"
    )


# --- the scenario -----------------------------------------------------------------------------


def scenario() -> None:
    started = time.monotonic()
    token = admin("pool-token", "outside", "default")
    admin("profile", "cpu", "tiny.en", "int8")
    admin("location", "talks", "mono")
    install(token)
    del token

    # 1. It registers (after downloading tiny.en), as its own user, with nothing exposed.
    wait("the follower to register", lambda: followers("active"))
    registered = time.monotonic() - started
    first = followers("active")[0]["id"]
    expect(unit()["ActiveState"] == "active", f"the unit is not active: {unit()}")
    pid = unit()["MainPID"]
    owner = machine(f"ps -o user:32= -p {pid}").strip()
    expect(owner == "swarmscribe-follower", f"the follower runs as {owner}")
    modes = machine(
        "stat -c '%U %a' /var/lib/swarmscribe-follower /var/lib/swarmscribe-follower/state"
        " /var/lib/swarmscribe-follower/state/credential.json"
    ).split("\n")[:3]
    expect(
        modes == ["swarmscribe-follower 700"] * 2 + ["swarmscribe-follower 600"],
        f"the state folder or the credential is not private: {modes}",
    )
    exposed = machine(
        f"tr '\\0' '\\n' < /proc/{pid}/environ | grep -c '^SWARMSCRIBE_JOIN_TOKEN=' || true\n"
        f"systemctl show {SERVICE} -p Environment --value | grep -c TOKEN || true\n"
        "ss -Hltnp 2>/dev/null | grep -c python || true"
    ).split()
    expect(
        exposed == ["0", "0", "0"], f"a token in the environment, or a listening port: {exposed}"
    )

    # 2. A recording is transcribed.
    queue("short.wav", 1)
    wait("short.wav to complete", lambda: job("short.wav")["state"] == "completed")
    expect(bool(job("short.wav")["text"]), "short.wav has an empty transcript")

    # 3. A stop that fits the grace period (900 s): the recording in hand is finished first.
    queue("fits.wav", LONG_REPEATS)
    held("fits.wav", 4)
    began = time.monotonic()
    machine(f"systemctl stop {SERVICE}")
    stop_fits = time.monotonic() - began
    done = job("fits.wav")
    expect(
        (done["state"], done["attempts"]) == ("completed", 1),
        f"a stop with 900 s of grace did not finish the recording: {done}",
    )
    state = unit()
    ended = (state["ActiveState"], state["Result"], state["ExecMainStatus"])
    expect(ended == ("inactive", "success", "0"), f"after a stop the unit is {state}")

    # 4. A stop that does not fit (grace 1 s): the recording is handed back, no attempt counted.
    set_env("SWARMSCRIBE_FOLLOWER_SHUTDOWN_GRACE_SECONDS", "1")
    machine(f"systemctl start {SERVICE}")
    queue("released.wav", LONG_REPEATS)
    held("released.wav", 4)
    began = time.monotonic()
    machine(f"systemctl stop {SERVICE}")
    stop_releases = time.monotonic() - began
    back = job("released.wav")
    expect(
        back["state"] == "queued" and back["tried"][-1][1] == "released",
        f"a stop with 1 s of grace did not release the recording: {back}",
    )
    expect(stop_releases < 15, f"the stop took {stop_releases:.1f} s")
    set_env("SWARMSCRIBE_FOLLOWER_SHUTDOWN_GRACE_SECONDS", "900")

    # 5. Started again it is the same follower: the credential was kept, nothing registers.
    machine(f"systemctl start {SERVICE}")
    wait("released.wav to complete", lambda: job("released.wav")["state"] == "completed")
    again = job("released.wav")
    expect(again["attempts"] == 1, f"the released attempt was counted: {again}")
    expect(
        [row["id"] for row in admin("state")["followers"]] == [first]
        and journal().count('"event": "registered"') == 1,
        "the follower registered again after a restart",
    )

    # 6. Killed outright mid-job: systemd restarts it, and the recording is redone.
    queue("killed.wav", LONG_REPEATS)
    held("killed.wav", 3)
    machine(f"kill -9 {unit()['MainPID']}")
    wait("systemd to restart the follower", lambda: int(unit()["NRestarts"]) >= 1, 30)
    wait("killed.wav to complete", lambda: job("killed.wav")["state"] == "completed")
    redone = job("killed.wav")
    expect(
        [outcome for _, outcome in redone["tried"]] == ["expired", "completed"],
        f"the killed follower's recording was not redone once: {redone}",
    )

    # 7. MemoryMax= on the unit is the memory guard's limit.
    machine(
        f"echo 'MemoryMax=2500M' >> {DROP_IN}\n"
        f"systemctl daemon-reload\nsystemctl restart {SERVICE}"
    )
    admin("silence", "talks", "an-hour.wav", "3600")
    wait("the hour to be refused", lambda: job("an-hour.wav")["state"] == "failed")
    reason = job("an-hour.wav")["failure_reason"] or ""
    expect(
        "out_of_resources" in reason and "may use 2500 MiB" in reason and "cgroup" in reason,
        f"the hour was not refused by the unit's MemoryMax: {reason}",
    )

    # 8. Drained: it exits 0, and Restart=on-failure leaves it stopped.
    restarts = unit()["NRestarts"]
    admin("drain", first)
    wait("the drained follower to exit", lambda: unit()["ActiveState"] == "inactive", 60)
    time.sleep(5)
    state = unit()
    ended = (state["ActiveState"], state["ExecMainStatus"], state["NRestarts"])
    expect(ended == ("inactive", "0", restarts), f"a drained follower was started again: {state}")

    # 9. Revoked: exit 4, and RestartPreventExitStatus keeps it out.
    machine(f"rm /var/lib/swarmscribe-follower/state/credential.json\nsystemctl start {SERVICE}")
    wait("a second follower to register", lambda: followers("active"))
    admin("revoke-pool", "outside")
    wait("the revoked follower to exit", lambda: unit()["ActiveState"] == "failed", 60)
    time.sleep(5)
    state = unit()
    expect(
        (state["ExecMainStatus"], state["SubState"], state["NRestarts"]) == ("4", "failed", "0"),
        f"a revoked follower was restarted, or did not exit 4: {state}",
    )
    expect("this follower has been revoked" in journal(), "the journal does not say why")

    # 10. Exit 3 (no GPU where one is demanded) is restarted, but not for ever.
    set_env("SWARMSCRIBE_FOLLOWER_DEVICE", "cuda")
    machine(f"systemctl reset-failed {SERVICE}\nsystemctl start {SERVICE}", check=False)
    wait(
        "systemd to give up on exit 3",
        lambda: unit()["ActiveState"] == "failed" and "repeated too quickly" in journal(),
        90,
    )
    state = unit()
    expect(
        state["ExecMainStatus"] == "3" and int(state["NRestarts"]) >= 4,
        f"exit 3 was not restarted up to the start limit: {state}",
    )
    expect("cuda was requested" in journal(), "the journal does not say why")

    print(
        f"passed (tiny.en on cpu, systemd {machine('systemctl --version').split()[1]}):"
        f" installed with uv tool install and registered in {registered:.0f} s; a stop with"
        f" 900 s of grace finished the recording ({stop_fits:.0f} s) and one with 1 s released it"
        f" ({stop_releases:.1f} s, no attempt counted); a killed follower was restarted and its"
        " recording redone; MemoryMax= refused an hour; drain exited 0 and stayed stopped;"
        " revoke exited 4 and was not restarted; exit 3 was restarted"
        f" {state['NRestarts']} times, then left failed"
    )


def main() -> int:
    command = sys.argv[1] if len(sys.argv) == 2 else ""
    if command not in ("up", "run", "down"):
        print(__doc__, file=sys.stderr)
        return 2
    {"up": up, "run": scenario, "down": down}[command]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: Keep its work folder out of Git and out of image builds**

Add the line `e2e/follower-systemd/work/` to `.gitignore` after `e2e/follower-kind/work/`, and the line `e2e/follower-systemd/work` to `.dockerignore` after `e2e/follower-kind/work`.

- [ ] **Step 4: Check that it parses and says how it is used**

Run: `uv run ruff check e2e/follower-systemd` — expected `All checks passed!`.
Run: `uv run python e2e/follower-systemd/run_e2e.py`
Expected: the module's docstring on stderr (it starts `The native Linux install of the follower, under a real systemd`), exit code 2.

- [ ] **Step 5: Commit**

```bash
git add e2e/follower-systemd/Dockerfile e2e/follower-systemd/run_e2e.py .gitignore .dockerignore
git commit -m "test(follower): a scenario for the native Linux install under systemd, against a real leader

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Install the follower under systemd, against a real leader, and record it

**Files:**
- Create: `docs/superpowers/plans/2026-10-05-follower-f4-outcomes.md`

**Interfaces:**
- Consumes: Task 6's driver and everything it installs.
- Produces: the outcomes document, with the section headings `## The planner's run, 2026-10-05`, `## The run of Task 7 (plan F4a)` and `## What this does not prove`. F4b adds its Windows sections between the second and the third.

- [ ] **Step 1: The leader's image**

On the development machine (Git Bash, the Docker `PATH` line of Global Constraints). The scenario needs the leader's test image, built from this branch:

```bash
docker build -t swarmscribe-leader:f4-e2e -f e2e/compose/Dockerfile .
export LEADER_IMAGE=swarmscribe-leader:f4-e2e
```

Expected: it ends `naming to docker.io/library/swarmscribe-leader:f4-e2e` (at once, if Task 4's Step 7 built it today). The tag is this plan's own: `swarmscribe-leader:e2e` may be another agent's and is left alone. Keep `LEADER_IMAGE` set for Steps 2 and 3.

- [ ] **Step 2: Up**

```bash
export UV="python -m uv"
time python -m uv run python e2e/follower-systemd/run_e2e.py up
```

Expected last line: `up: a leader at http://follower-systemd-leader:8080 and a machine with systemd 252 (252.39-1~deb12u2)` (the Debian revision may differ). About 15 s once the machine's image exists; its first build pulls `debian:12-slim`.

- [ ] **Step 3: Run the scenario**

```bash
time python -m uv run python e2e/follower-systemd/run_e2e.py run
```

Expected, after about four minutes, one line (the figures will differ a little):

```
passed (tiny.en on cpu, systemd 252): installed with uv tool install and registered in 24 s; a stop with 900 s of grace finished the recording (40 s) and one with 1 s released it (1.6 s, no attempt counted); a killed follower was restarted and its recording redone; MemoryMax= refused an hour; drain exited 0 and stayed stopped; revoke exited 4 and was not restarted; exit 3 was restarted 5 times, then left failed
```

A line that starts `FAILED:` names the step. The scenario cannot be run twice on one `up` (it drains and revokes): `down`, `up`, `run`. If it fails, look before you tear down: `docker exec follower-systemd-machine journalctl -u swarmscribe-follower --no-pager | tail -30` and `docker exec follower-systemd-machine systemctl status swarmscribe-follower --no-pager`. Fix the cause (in the unit, the driver or the follower, with a test where the follower was wrong), and run again from `down`.

- [ ] **Step 4: Look at the machine by hand, once**

Before `down`, the unit is `failed` on exit 3 (the scenario's last step). Capture, then read:

```bash
out="$(MSYS_NO_PATHCONV=1 docker exec follower-systemd-machine bash -c 'systemctl show swarmscribe-follower -p ActiveState -p Result -p ExecMainStatus -p NRestarts; stat -c "%U:%G %a %n" /etc/swarmscribe-follower /etc/swarmscribe-follower/follower.env /etc/swarmscribe-follower/join-token /var/lib/swarmscribe-follower /var/lib/swarmscribe-follower/state /var/lib/swarmscribe-follower/state/credential.json; journalctl -u swarmscribe-follower --no-pager -o cat | tail -4')"
echo "$out"
```

Expected: `ActiveState=failed`, `Result=exit-code`, `ExecMainStatus=3`, `NRestarts=5`; `root:swarmscribe-follower 750 /etc/swarmscribe-follower`, `root:swarmscribe-follower 640` for the two files in it, `swarmscribe-follower:swarmscribe-follower 700` for the two folders and `600` for the credential; the journal ends with `Start request repeated too quickly.`, `Failed with result 'exit-code'.` and `Failed to start swarmscribe-follower.service - SwarmScribe follower.`

- [ ] **Step 5: Down, and check that nothing is left**

```bash
python -m uv run python e2e/follower-systemd/run_e2e.py down
left="$(docker ps -a --format '{{.Names}}')"; echo "$left" | grep follower-systemd || echo "nothing left"
```

Expected: `nothing left`. The machine's image `follower-systemd:e2e` stays for the next run.

- [ ] **Step 6: Record it**

Create `docs/superpowers/plans/2026-10-05-follower-f4-outcomes.md` with the content below, and fill the section "The run of Task 7" from what Steps 2 to 4 printed on this run: the versions from the commands named there, the three `real` times, `run`'s last line whole, and Step 4's output. Do not copy the planner's figures into it.

````markdown
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

Run on: this run (the date).

- Versions, from the commands: `cmd /c ver`: this run; `docker version --format
  '{{.Server.Version}}'`: this run; `python -m uv --version`: this run; the machine's systemd
  (the last line of `up`): this run.
- `up`: its last line, and `real`: this run.
- `run`: its last line, whole, and `real`: this run.
- The machine looked at by hand before `down` (step 4's output, whole): this run.
- `down`, and that no `follower-systemd-*` container was left: this run.

## What this does not prove

- **A real Linux host.** The machine is a privileged container: no reboot was survived, no
  real `network-online.target`, no NVIDIA driver under the unit. The GPU was run in a plain
  container, not under systemd.
- Any distribution but Debian 12, any systemd but 252, cgroup v1.
- A leader on TLS: the test leader is plain http, with the development switch.
- A recording of people talking: the long recordings are one phrase repeated.
````

- [ ] **Step 7: Commit**

```bash
git add docs/superpowers/plans/2026-10-05-follower-f4-outcomes.md
git commit -m "docs(follower): record the native Linux install under systemd

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## For the owner

Not tasks; what is yours to decide, and what this plan leaves.

- **Rulings marked (owner):** 1 (the follower loads cuBLAS itself: no `setup-cuda`, no `LD_LIBRARY_PATH`), 4 (outside machines install wheels built from the repository, pinned by a constraints file; the spec's bare `uv tool install swarmscribe-follower` waits for the packages to be published), 9 (on Windows a state folder other accounts can reach is refused, which can stop a follower that joined before F4 until the printed `icacls` command is run), 13 (the systemd proof is a recorded local run, not a CI job).
- **Nothing here needs an administrator.** The Windows service and its one administrator procedure are F4b.
- **The systemd proof is a container, not a host**: a reboot, `network-online.target` on a real network and an NVIDIA driver under the unit were not run. The first Linux PC that joins should be watched through one reboot (`systemctl is-enabled`, `journalctl -u swarmscribe-follower -b`) and the result added to the outcomes document.
- **Not pushed.**

## Self-Review

**Spec coverage.** D15 (GPU libraries from the `nvidia-*-cu12` wheels "in the image and in a native install alike"): Task 1, with ruling 1's difference in how they are found, and `cuda-paths` (spec 4's module map). Spec 8.3 "Install": ruling 4, Task 5's constraints file and Task 6's driver, which runs the command. Spec 8.3 "Join and check": `doctor` is unchanged; ruling 14 and Task 2 make `join --leader URL` enough. Spec 8.3 "Linux service" (dedicated user, `Restart=on-failure`, `RestartPreventExitStatus=4 5`, `TimeoutStopSec=930`, `StateDirectory`, `ProtectSystem=strict`, `NoNewPrivileges=yes`, a settings file): Task 5, proven in Task 7; rulings 5 and 6 for `--env-file` in place of `EnvironmentFile=` and for the token file. Spec 5.3 (the Windows credential): Task 4. Spec 5.6's table row for systemd: Task 5's test and Task 7's steps 3 and 4. Spec 5.7 and D22 (the limit "or when unset the cgroup limit"): Task 3. Spec 6.5: Task 5, Task 7's steps 9 and 10. D18 (off in a native install): ruling 7 and Task 5's test. The F1 follow-ups for F4: M4 in Task 2; the Windows ACL in Task 4; the service stop is F4b.

**The nine carry-overs.** 1 (the tick): ruling 7 for systemd; the service is F4b. 2 (the DLL mechanism, by experiment; Linux): "Measured", ruling 1, Task 1. 3 (the memory guard): ruling 8, Task 3. 4 (the trust check on Windows): ruling 9, Task 4. 5 (the listener): ruling 7. 6 (stopping): ruling 11 for systemd; the service is F4b. 7 (exit codes and restarts): ruling 10 for systemd; the service is F4b. 8 (the token and the leader's address): rulings 5 and 6, Tasks 2 and 5. 9 (what an outside machine installs from): ruling 4, "Measured", Tasks 5 to 7.

**Where this plan differs from the spec**, each to be written into the amendments by F4b's last task: no `setup-cuda` and no `LD_LIBRARY_PATH` for a native install; the install command; `--env-file` and the token file; the unit's start limit; the cgroup the guard reads; the Windows rule for the state folder.

**Not covered here:** the Windows service, the native Windows GPU proof, the Windows CI job, the README and the spec's amendments (all F4b); a real Linux host (above).

**Placeholders.** One, deliberate: the lines of the outcomes document's "The run of Task 7" that only the person running Task 7 can fill in. `deploy/follower-constraints.txt` is generated by a command the plan gives, with what it must contain. Everything else is given whole.

**Unproven, said where it applies:** a real Linux host (a reboot, a GPU under the unit); the Compose scenario on the GPU and the `kind` scenario were not run again with this plan's code.

**Names.** `cudalibs.NAMES`, `EXTRA`, `roots`, `folders`, `files`, `load`, `hint` (Task 1) are what `models.py`, `main.py` and `test_cudalibs.py` use; `libraries=` and `hint=` are `ModelHost`'s keyword names in the module and the tests. `envfile.parse`, `load`, `EnvFileError`, `entry.command`, `stored_leader` (Task 2) match between `envfile.py`, `entry.py`, `main.py` and the tests. `own_cgroup`, `cgroup_limit_mb(root=, proc=, v1=)`, `CGROUP_SOURCE`, `rss_mb` (Task 3) match between `memory.py` and `test_memory.py`. `current_user`, `read_acl(path, descriptor_of)`, `access_problem(path, what=, owner_only=, acl=)`, `make_private` (Task 4) match between `winacl.py`, `fsutil.py`, `credentials.py` and `test_winacl.py`. The unit's paths (`/usr/local/bin/swarmscribe-follower`, `/etc/swarmscribe-follower/follower.env`, `/etc/swarmscribe-follower/join-token`, `/var/lib/swarmscribe-follower/state`) are the same in the unit, the settings example, the tests of Task 5 and the driver of Task 6. The driver's `admin` commands are the ones `e2e/follower-kind/admin.py` has.
