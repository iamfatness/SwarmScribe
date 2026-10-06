# Follower F4b — The Windows Service, the Native Windows Proof and the Guide Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the follower as a Windows service (registered with one command, stopped cleanly by Windows, restarted only when a restart can help), prove the native Windows install on the development machine's GPU without administrator rights, hand the owner the one short procedure that does need them, add a Windows CI job, and write the README's guide to outside machines and the spec's amendments.

**Architecture:** The service is the follower's own code. `windows.py` keeps three things apart: `ServiceHost`, plain Python that decides what Windows is told and what its controls do (tested on every platform with a recording `report`, and run in a console by `service foreground`); the exact `sc.exe` and `icacls.exe` command lines that register the service (printed by `service install --print`); and a thin `ctypes` binding to the service control manager, which only a process that the control manager started can run. A stop control is counted on a `StopSignals` that the agent's `run_supervised` already polls, so the service adds no second supervisor. The command Windows starts is the real interpreter with `service_boot.py`, because the environment's `python.exe` is a launcher. A driver installs the follower natively and runs the service's command in a console against a real leader, on the GPU.

**Tech Stack:** Python 3.11+ with `ctypes` (no new dependency), the Windows service control manager through `advapi32`, `sc.exe` and `icacls.exe`, uv 0.12 with a Python of its own, GitHub Actions `windows-latest`, Docker 29 for the test leader, pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-10-04-follower-design.md` (build step 5; ruling R4; section 4's module map: "`windows.py` DLL placement, service control handler (F4)"; sections 5.3, 5.6, 6.5, 8.3, 9, 10 and 15), with `docs/superpowers/plans/2026-10-04-follower-f1-followups.md` (section "F4 (native install)").

**This plan is the second of two.** It needs F4a (`2026-10-05-follower-f4a-native-install-and-systemd.md`) executed on the same branch: `envfile.load` and `EnvFileError`; the option `--env-file`; `cudalibs` and the command `cuda-paths`; `winacl.read_acl`, `SYSTEM`, `ADMINISTRATORS`; `deploy/follower-constraints.txt`; the outcomes document `docs/superpowers/plans/2026-10-05-follower-f4-outcomes.md` with its three headings; `e2e/follower-kind/admin.py`. F4a's rulings are referred to here as "F4a ruling n".

## Global Constraints

- "**Windows service.** `swarmscribe-follower service install` registers a Windows service running as a dedicated local account, with the same restart rules and the stop control wired to the shutdown path" (spec 8.3). "Signals handled on the main thread: `SIGTERM` and `SIGINT`; on Windows also `SIGBREAK` and the service stop control" (spec 5.6).
- From the F1 follow-ups: "the Windows service's stop handler calls `agent.stop()` from `SERVICE_CONTROL_STOP`, reports stop-pending with a wait hint longer than the grace period, and reports stopped only after `serve()` returns."
- "`/healthz` answers `200` while the supervisor has ticked in the last 30 seconds" (spec 9), and `Agent.tick`'s docstring: "anything else that supervises an agent (the F4 Windows service) must too."
- Exit codes (spec 4.1): `0` stopped or drained; `2` configuration; `3` this machine cannot do the work; `4` not authorised; `5` protocol refused; `1` a bug. "Exit 4 and 5 must not be restarted blindly" (spec 6.5).
- "The join token and the credential are never logged, never passed as arguments" (spec 7): the token is in no service registration, no command line, no registry value and no settings file; it is a file of its own (F4a ruling 6).
- Third-party packages of the follower stay "`httpx` and `pydantic-settings`; `prometheus-client`" (spec 4): **no new dependency** (no pywin32, no service wrapper).
- "**Windows.** A CI job on `windows-latest` running the follower's unit tests (signal handling, credential file, paths). GPU on Windows is checked by `doctor` on a real machine" (spec 10).
- **THE SHELL IS NOT AN ADMINISTRATOR'S AND CANNOT BECOME ONE. Never attempt to elevate**: no `runas`, no `Start-Process -Verb RunAs`, no scheduled task, no other way around a UAC prompt. Registering, starting, stopping and removing a Windows service need an administrator; **no task of this plan does any of them.** Never run `sc.exe create`, `sc.exe delete`, `sc.exe start`, `sc.exe stop`, `New-Service` or `swarmscribe-follower service install` (without `--print`) or `service uninstall` (without `--print`). Everything the tasks run works without those rights; the one procedure that does not is written out for the owner (Task 3) and is run by the owner only.
- **Say what was proven how.** Wherever this plan's documents or the README speak of the Windows service, they keep three things apart: proven by tests on every platform (what the service tells Windows, what its controls do); proven on the development machine without the service control manager (the native install, the GPU, the service's own command run in a console); and waiting for the owner's run as an administrator (everything that involves the control manager itself).
- **Tests run on both platforms.** `ServiceHost` and the command builders are plain Python and are tested on Linux and Windows alike; a test that calls Windows itself is skipped elsewhere with `reason=`.
- **The whole suite is the gate of every task that changes Python:** `uv run ruff check .` and `uv run pytest` over the whole repository, run **once**, before the task's commit (about 12 minutes). **Never let a pytest run be killed** by a time limit or by hand: a killed run leaves a stray `postgres` holding `.pgdata` under the worktree. If that happens, stop only the `postgres` whose data directory is under `C:\Users\walla\SwarmScribe-f4`. Python `>=3.11`; `ruff` line length 100, rules `E, F, I, UP, B`.
- **The development machine** is Windows 11 with Git Bash and PowerShell 5.1. `uv` is not on the PATH: run it as `python -m uv` (written `uv ...` below; for the driver set `UV="python -m uv"`). Docker Desktop: in Git Bash first `export PATH="$PATH:/c/Users/walla/AppData/Local/Programs/DockerDesktop/resources/bin"`.
- **`uv python install` must be given `--no-bin --no-registry`** on this machine (the driver does): without them it writes `python3.12.exe` into `C:\Users\walla\.local\bin` and a key under `HKCU\Software\Python`, outside the worktree.
- **Work only in the worktree `C:\Users\walla\SwarmScribe-f4`**, on the branch `follower-f4`. Never touch `C:\Users\walla\SwarmScribe` or `C:\Users\walla\SwarmScribe-ui`. Do not push.
- **Another agent uses Docker on this machine.** Never stop, remove or retag a container or an image you did not create, never run any `docker ... prune`, never restart Docker. This plan's containers and network are named `follower-windows-*` (`E2E_PREFIX`), and the leader's image it uses is `swarmscribe-leader:f4-e2e` (`LEADER_IMAGE`), never the shared `swarmscribe-leader:e2e`. Never use ports 8900 or 8901; the test leader is published on `127.0.0.1:18080` only.
- **Capture, then match.** In a shell check under `set -euo pipefail`, never pipe a live `docker` command into `grep -q`; write its output to a variable first, then match it.
- The same agent code must still run in the images and on Kubernetes: nothing here changes a module they execute (`windows.py` and `service_boot.py` are imported only by the `service` command and by Windows itself).
- Commit after each task with the message the task gives, ending with exactly the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Measured before this plan was written

All of this plan's code ran on 2026-10-05 in a scratch folder outside the repository, built from `main` at `33885bb` with F4a's files (Windows 11 10.0.26200, uv 0.12.22 with its own Python 3.12.15, RTX 4090 with driver 617.14, Docker Desktop 29.8.1), in a shell without administrator rights.

| What | Result |
|---|---|
| The environment's `Scripts\python.exe` after `uv tool install` | a launcher: the Python that runs is a **child** process, `...\python\cpython-3.12.15-windows-x86_64-none\python.exe` (asked of Windows by process id). `swarmscribe-follower.exe` is a launcher too. A service whose command is either would have the control manager talking to a process that is not the follower |
| The real interpreter with `service_boot.py`'s path | one process; the follower and everything it depends on import (the script puts the environment's `site-packages` on the path) |
| `StartServiceCtrlDispatcherW` called from a console | returns at once (0.1 s) with error 1063, `ERROR_FAILED_SERVICE_CONTROLLER_CONNECT`: the `ctypes` prototypes are accepted, and nothing more can be said without the control manager |
| The Store's Python as the interpreter | `service install` refuses it: it belongs to one user. The same for an install under `C:\Users\...` |
| A user without administrator rights can create `C:\ProgramData\swarmscribe-follower` | yes (the planner's first prototype did, by opening its log there; removed). So `service install` refuses a data folder that an account other than Administrators or SYSTEM owns, and the service's process opens its log only once the control manager has answered |
| `e2e/follower-windows/run_e2e.py up` | 11 to 15 s once uv's Python and the wheels' dependencies are cached (the first time it downloads Python 3.12 and the 700 MB cuBLAS wheel; `work/` is 1.3 GB) |
| `run` (the GPU, `large-v3`, `float16`, from the user's Hugging Face cache) | passed four times, 1 min 14 s to 1 min 20 s |
| `run --cpu` (`tiny.en`) | passed twice, 48 s |
| `cuda-paths`; DLLs beside `ctranslate2.dll`; `PATH` | the wheel's two `bin` folders; none copied; `PATH` does not name them |
| `doctor` | `device: cuda (NVIDIA GeForce RTX 4090, 24564 MiB)`, `model: large-v3 (float16) loaded and ran`, `state folder: ok`, `result: ready` |
| The service's command with `--foreground --env-file` | registered in 5 s as device `cuda`; printed `service status: START_PENDING accepts=0x0 exit=0 wait_hint_ms=30000 checkpoint=1` and `service status: RUNNING accepts=0x101 exit=0 wait_hint_ms=0 checkpoint=0` |
| Ctrl+Break (the stop control) three seconds into an eight-minute recording, grace 1 s | `STOP_PENDING ... wait_hint_ms=31000 checkpoint=1`, then `STOPPED ... exit=0`; the process ended with code 0 in 0.5 s (1.2 s on the CPU); the recording `released` and `queued` |
| Started again | the same follower (one row); the recording `completed` with 1 attempt |
| The pool token revoked with its followers | the process ended with code 4 after `service status: STOPPED accepts=0x0 exit=4 ...` |
| `SWARMSCRIBE_FOLLOWER_OFFLINE=1` with a start-up model that is not in the cache | the process ended with code 3 and printed **no** `STOPPED` line |
| The follower's own tests on Windows, with every change of F4a and F4b | `737 passed, 14 skipped` in 67 s |
| The whole suite with every change of F4a and F4b | `3193 passed, 21 skipped, 5 deselected` in 9 min 6 s on Windows. Three tests were added after that run (one each in F4a's Tasks 4 and 5 and F4b's Task 1); the follower package was run again with them: `737 passed, 14 skipped` |
| `test_windows.py` on Linux (a Debian 12 container, Python 3.12) | `21 passed, 5 skipped`, each skip saying `calls Windows itself` |
| `uv sync` on a machine without a Python of the 3.11 or 3.12 line | uv fetched Python 3.14 and stopped: `pgserver` (the test database) has wheels for 3.11 and 3.12 only. `uv sync --python 3.12` worked. The Windows CI job therefore names the version |
| `gh repo view --json visibility` | `PUBLIC`: GitHub's Windows runners cost nothing |

**Not run, because it needs an administrator** (the owner's procedure in Task 3 runs each of these once): `service install` and `service uninstall` for real, so also the exact syntax `sc.exe` accepts for the recovery actions; the control manager starting the service's command and the `ctypes` binding answering it (`RegisterServiceCtrlHandlerExW`, `SetServiceStatus`); the service's virtual account reading its settings and token and writing its state, models and log; the trust check's verdict on a state folder that Administrators own; a GPU used from a service's session; a stop control arriving from Windows and the wait hint being honoured; a system shutdown; the recovery actions restarting a process that ended without `SERVICE_STOPPED`.

**Not run for another reason:** the Windows CI job (Task 2) has never run on a GitHub runner, because this branch is not pushed.

## Rulings

Decisions this plan makes. Those marked **(owner)** are the owner's to overturn; each has the plan's recommendation built in, so execution does not wait.

1. **The service is the follower's own code, speaking to the service control manager through `ctypes`.** The spec decided this (`windows.py`: "service control handler"; `swarmscribe-follower service install`) and forbids a new dependency, which rules out pywin32; a wrapper such as NSSM would be a second program to install and trust. The binding is three calls and about sixty lines. Because nothing can exercise it here, everything that can be decided without it is decided elsewhere (`ServiceHost`) and tested.
2. **The command Windows starts is the real interpreter with `service_boot.py`**, never `swarmscribe-follower.exe` or the environment's `python.exe` (both are launchers; measured). `service_boot.py` adds the environment's `site-packages` to the import path itself. The service's registration therefore names the interpreter and the environment by path: after an upgrade that replaces either (`uv tool upgrade`, a new Python), run `service uninstall` and `service install` again. The README says so.
3. **The service runs as its own virtual account, `NT SERVICE\SwarmScribeFollower`, and keeps everything under `%ProgramData%\swarmscribe-follower`.** **(owner)** A virtual account is the spec's "dedicated local account" without a password for Windows to store: it exists with the service and can be named in an access control list. Administrators and SYSTEM own the folder; the service may read `follower.env` and `join-token` and may write `state`, `models` and `logs`. Consequence: the follower must be installed for the machine, with uv's own Python, under `C:\Program Files\swarmscribe-follower`; `service install` refuses the Store's Python and an install inside a user's profile, and says what to do. Recommended as written. Overturning it (a named local user) means a password given to `sc.exe config ... password=` by hand, documented but not automated.
4. **What Windows does with each exit.** **(owner)** Windows has no rule per exit code: its recovery actions run when a service's process ends without having reported `SERVICE_STOPPED`. So the service reports it or not, by the exit:

    | The follower ends with | The service tells Windows | Windows then |
    |---|---|---|
    | `0` stopped, or drained | `SERVICE_STOPPED`, no error | nothing |
    | `4` revoked or token refused, `5` protocol | `SERVICE_STOPPED` with service error 4 or 5 (seen in `sc query` and in the System log's entry 7024) | nothing: never restarted |
    | `1`, `2` or `3` after a stop was asked for | `SERVICE_STOPPED` with that service error | nothing: an operator's stop is final |
    | `1` a bug, `2` configuration, `3` this machine cannot do the work | nothing: the process ends with that code | the recovery actions: restart after a minute, twice, then leave it stopped; the count starts again after a day without a failure |
    | the process dies (a crash in a GPU library, an out-of-memory kill) | nothing | the same recovery actions |

    That is systemd's `Restart=on-failure` with `RestartPreventExitStatus=4 5` and a start limit (F4a ruling 10), in the only way Windows offers it. The price: for exits 1, 2 and 3 the System log says "terminated unexpectedly" and the reason is in the follower's own log. Recommended as written. The alternative (report every exit and set the failure flag, so that Windows restarts on any error) cannot keep a revoked follower out without reporting its exit as success. Overturning it is a change of one set (`FINAL_EXITS`) and one `sc.exe` line.
5. **Stopping** (carry-over 6). The stop control is one stop: the follower finishes the recording in hand if it fits `SWARMSCRIBE_FOLLOWER_SHUTDOWN_GRACE_SECONDS` (900 in the settings file `service install` writes) and hands it back without a counted attempt otherwise; the service reports `SERVICE_STOP_PENDING` once, with a wait hint of the grace period plus 30 seconds, and `SERVICE_STOPPED` when the follower has ended. `sc.exe stop` returns at once and the Services console gives up waiting after about two minutes; the service goes on stopping either way. **A system shutdown is two stops at once**: the recording is handed back immediately, whatever the grace. The service accepts the pre-shutdown control for this, which gives a service minutes where the plain shutdown control gives a few seconds. If Windows kills the process first, the recording costs one counted attempt when its lease runs out, as after any crash. **Sleep, hibernation and a shutdown with Fast Startup are not stops**: the process is frozen and wakes with a lease that ran out long ago, which the follower already handles (`409 stale_lease`: it drops the job and claims again). That costs one counted attempt; handling the power event is a follow-up.
6. **The service logs to a file**, `%ProgramData%\swarmscribe-follower\logs\follower.log`: a service has no console, `sys.stderr` is `None` there, and a library that prints (a download's progress bar) would fail. The same JSON lines as everywhere. Past 10 MiB the file is kept as `follower.log.1` at the next start and begun again. No Windows event source is registered; what the control manager itself logs (7024 with the service error, 7031 with the recovery action) is enough to send an operator to the file.
7. **The health listener stays off** (D18): the settings file `service install` writes does not set it. **Carry-over 1** needs no code: the service runs `swarmscribe-follower run`'s own path, `Agent.run_supervised`, on the service's thread with the service's stop counter in place of signal handlers, and `run_supervised` ticks. A test pins that `/healthz`'s check stays true through a stop control.
8. **The service starts `delayed-auto`**: after a boot, once the network and the GPU driver are up.
9. **`service install` and `service uninstall` are `sc.exe` and `icacls.exe` command lines**, shown by `--print`, and they refuse to run without administrator rights before running anything. `uninstall` leaves `%ProgramData%\swarmscribe-follower`: it holds the follower's credential.
10. **`service foreground` runs the service's code in a console**: the same `ServiceHost`, with a `report` that prints each status Windows would be told and Ctrl+C or Ctrl+Break as the stop control (a second one as a shutdown). It is how the service is debugged, and how Task 3 proves it without the control manager.
11. **A Windows CI job, `follower-windows`, runs the follower package's tests on `windows-latest`.** **(owner)** The spec asks for it (section 10) and the repository is public, so the runner is free; it adds about four minutes to a run. Recommended as written. What it proves: the Windows-only tests of F4a and F4b on a second machine and a second Python (access control lists, the working set, `service install --print`, the dispatcher's answer outside the control manager, the service's own command from the real interpreter) and that every other follower test passes on Windows. What it cannot prove: a GPU, and anything under the service control manager. It has not run yet (not pushed); if the leader's test database (`pgserver`) does not start on GitHub's Windows image, limit the job to the tests that need no leader, as "For the owner" says.
12. **The native Windows proof is a recorded local run on the development machine's GPU** (`e2e/follower-windows`), not a CI job: there is no GPU in CI, and its CPU variant adds little to the unit tests.

## Review Focus

Conditions the spec implies and that are most likely to bite a person running a Windows follower, each pinned by a test in Task 1 or a step of Task 3's scenario:

1. **A stop while a recording is being transcribed**: handed back (or finished, if it fits), and Windows told `STOP_PENDING` with a hint past the grace and then `STOPPED` — `test_the_stop_control_mid_job_releases_the_recording_and_the_supervisor_keeps_ticking`; scenario step 4.
2. **A revoked follower under a service that restarts on failure**: reported stopped with its code, so nothing restarts it — `test_revoked_and_protocol_are_reported_stopped_with_their_code_so_nothing_restarts`; scenario step 6.
3. **A machine that cannot do the work, or a bug**: not reported stopped, so the recovery actions run; but never after an operator's stop — `test_an_exit_a_restart_may_cure_is_not_reported_stopped`, `test_after_a_stop_was_asked_any_exit_is_reported_stopped`; scenario step 7.
4. **A system shutdown mid-job**: two stops at once, no grace — `test_a_shutdown_counts_two_stops_so_the_job_is_handed_back_at_once`.
5. **`service install` without administrator rights, on the Store's Python, or from a per-user install**: refused in words before anything is run — `test_without_administrator_rights_install_and_uninstall_refuse_and_say_so`, `test_a_store_python_or_an_install_inside_a_profile_cannot_be_a_service`.
6. **A settings file with a mistake, under a service with no console**: exit 2 and the line number in the log, never a silent service — `test_the_services_own_command_starts_the_follower_from_the_real_interpreter`.
7. **A data folder somebody else made before the install**: refused — `test_a_data_folder_made_by_someone_else_is_refused`.

## File Structure

| File | Responsibility |
|---|---|
| `packages/follower/src/swarmscribe_follower/windows.py` (new, Task 1) | `ServiceHost`; the install and uninstall commands; the service's process; the binding to the control manager |
| `packages/follower/src/swarmscribe_follower/service_boot.py` (new, Task 1) | what Windows starts: puts the environment on the import path, then `windows.service_process` |
| `.../main.py` (modify, Task 1) | the command `service install | uninstall | foreground` |
| `packages/follower/tests/test_windows.py` (new, Task 1) | their tests |
| `.github/workflows/ci.yml` (modify, Task 2) | job `follower-windows` |
| `e2e/follower-windows/run_e2e.py` (new, Task 3) | the native Windows proof: `up`, `run [--cpu]`, `down`; and `token`, `recording`, `state` for the owner's procedure |
| `.gitignore`, `.dockerignore` (modify, Task 3) | `e2e/follower-windows/work` |
| `docs/superpowers/plans/2026-10-05-follower-f4-outcomes.md` (modify, Task 3) | the Windows run, recorded; the owner's procedure and where its result goes |
| `README.md` (modify, Task 4) | "Run a follower on an outside machine"; the settings table; known limits; status |
| `docs/superpowers/specs/2026-10-04-follower-design.md` (modify, Task 4) | amendments after F4 |
| `docs/superpowers/plans/2026-10-04-follower-f1-followups.md` (modify, Task 4) | what F4 closed, what it leaves |

---

### Task 1: The Windows service — its host, its commands, its process

**Files:**
- Create: `packages/follower/src/swarmscribe_follower/windows.py`
- Create: `packages/follower/src/swarmscribe_follower/service_boot.py`
- Modify: `packages/follower/src/swarmscribe_follower/main.py`
- Test: `packages/follower/tests/test_windows.py` (new)

**Interfaces:**
- Consumes: `StopSignals` with `count` and `handler(signum, frame)` (`signals.py`); `Agent.run_supervised(*, poll, signals)` and `Agent.health()` (`agent.py`); `main(argv, *, out, err, signals)` (`main.py`); `envfile.load`, `envfile.EnvFileError` (F4a Task 2); `winacl.read_acl`, `winacl.SYSTEM`, `winacl.ADMINISTRATORS` (F4a Task 4); the test kit's `FakeLeader`, `FakeEngine`, `make_agent`.
- Produces: `windows.ServiceHost(run, report, *, grace_seconds)` with `stops`, `control(code) -> int`, `main() -> int`, `reported_stopped`; the constants `STOPPED`, `START_PENDING`, `STOP_PENDING`, `RUNNING`, `CONTROL_STOP`, `CONTROL_INTERROGATE`, `CONTROL_SHUTDOWN`, `CONTROL_PRESHUTDOWN`, `ACCEPT_STOP`, `ACCEPT_PRESHUTDOWN`, `NO_ERROR`, `ERROR_CALL_NOT_IMPLEMENTED`, `ERROR_FAILED_SERVICE_CONTROLLER_CONNECT`, `SERVICE_NAME == "SwarmScribeFollower"`; `windows.image() -> list[str]`, `image_problem`, `root_problem`, `install_commands(command, root)`, `uninstall_commands()`, `env_template(root)`, `data_root()`, `env_file()`, `log_file()`, `install`, `uninstall`, `run_foreground`, `printing_report`, `serve_under_scm`, `service_process(argv) -> int`. The commands `swarmscribe-follower service install [--print]`, `service uninstall [--print]`, `service foreground` (with the top-level `--env-file PATH` for another settings file). In a console the service prints lines of the form `service status: RUNNING accepts=0x101 exit=0 wait_hint_ms=0 checkpoint=0`; Task 3's driver reads them, and starts `[*windows.image(), "--foreground", "--env-file", PATH]`.

- [ ] **Step 1: Write the failing tests**

Create `packages/follower/tests/test_windows.py`:

```python
"""The Windows service without the service control manager. What the service tells Windows
and what its controls do is plain Python and is tested on every platform; the tests that call
Windows itself skip elsewhere. Nothing here needs administrator rights, and nothing here can
prove what only the control manager can show (see the F4 outcomes document)."""

import io
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest
from follower_testkit import FakeEngine, FakeLeader, make_agent
from swarmscribe_follower import main as cli
from swarmscribe_follower import windows
from swarmscribe_follower.windows import (
    ACCEPT_PRESHUTDOWN,
    ACCEPT_STOP,
    CONTROL_INTERROGATE,
    CONTROL_PRESHUTDOWN,
    CONTROL_SHUTDOWN,
    CONTROL_STOP,
    ERROR_CALL_NOT_IMPLEMENTED,
    NO_ERROR,
    RUNNING,
    START_PENDING,
    STOP_PENDING,
    STOPPED,
    ServiceHost,
)

windows_only = pytest.mark.skipif(os.name != "nt", reason="calls Windows itself")


def host(run, *, grace=8.0):
    reports = []
    made = ServiceHost(run, lambda *status: reports.append(status), grace_seconds=lambda: grace)
    return made, reports


# --- what Windows is told -----------------------------------------------------------------


def test_a_follower_that_ends_cleanly_is_reported_running_and_then_stopped():
    service, reports = host(lambda stops: 0)
    assert service.main() == 0
    # (state, accepted controls, service-specific exit code, wait hint, checkpoint)
    assert reports == [
        (START_PENDING, 0, 0, 30_000, 1),
        (RUNNING, ACCEPT_STOP | ACCEPT_PRESHUTDOWN, 0, 0, 0),
        (STOPPED, 0, 0, 0, 0),
    ]
    assert service.reported_stopped


@pytest.mark.parametrize("code", [4, 5])
def test_revoked_and_protocol_are_reported_stopped_with_their_code_so_nothing_restarts(code):
    service, reports = host(lambda stops: code)
    assert service.main() == code
    assert reports[-1] == (STOPPED, 0, code, 0, 0) and service.reported_stopped


@pytest.mark.parametrize("code", [1, 2, 3])
def test_an_exit_a_restart_may_cure_is_not_reported_stopped(code):
    """The process then ends without SERVICE_STOPPED, which is what Windows' recovery actions
    act on (module docstring of windows.py)."""
    service, reports = host(lambda stops: code)
    assert service.main() == code
    assert [status[0] for status in reports] == [START_PENDING, RUNNING]
    assert not service.reported_stopped


def test_after_a_stop_was_asked_any_exit_is_reported_stopped():
    """An operator who stops the service must not see Windows start it again."""
    service, reports = host(lambda stops: 2)
    service.control(CONTROL_STOP)
    assert service.main() == 2
    assert reports[-1] == (STOPPED, 0, 2, 0, 0) and service.reported_stopped


def test_a_bug_in_the_follower_is_exit_1_and_left_to_the_recovery_actions():
    def run(stops):
        raise RuntimeError("a bug")

    service, reports = host(run)
    assert service.main() == 1
    assert not service.reported_stopped


# --- the controls -------------------------------------------------------------------------


def test_the_stop_control_counts_one_stop_and_asks_for_the_grace_period_and_30_seconds():
    service, reports = host(lambda stops: 0, grace=900.0)
    assert service.control(CONTROL_STOP) == NO_ERROR
    assert service.stops.count == 1
    assert reports == [(STOP_PENDING, 0, 0, 930_000, 1)]


@pytest.mark.parametrize("control", [CONTROL_PRESHUTDOWN, CONTROL_SHUTDOWN])
def test_a_shutdown_counts_two_stops_so_the_job_is_handed_back_at_once(control):
    service, reports = host(lambda stops: 0, grace=900.0)
    assert service.control(control) == NO_ERROR
    assert service.stops.count == 2  # run_supervised: stop(now=True)
    assert reports == [(STOP_PENDING, 0, 0, 30_000, 1)]


def test_interrogate_is_answered_and_any_other_control_is_refused():
    service, reports = host(lambda stops: 0)
    assert service.control(CONTROL_INTERROGATE) == NO_ERROR
    assert service.control(0x7F) == ERROR_CALL_NOT_IMPLEMENTED
    assert service.stops.count == 0 and reports == []


def test_the_stop_control_mid_job_releases_the_recording_and_the_supervisor_keeps_ticking(
    tmp_path,
):
    """The whole path with a real Agent: the control only counts; `run_supervised` (which also
    ticks for /healthz) turns the count into `stop()`; the job is handed back; Windows is told
    SERVICE_STOPPED. This is carry-over 1 of plan F4: under a service nothing else ticks."""
    leader, engine = FakeLeader(), FakeEngine()
    job_id = leader.add_job()
    agent = make_agent(tmp_path, leader, engine, shutdown_grace_seconds=0)
    service, reports = host(
        lambda stops: agent.run_supervised(signals=stops, poll=0.02), grace=0.0
    )
    answers, health = [], []

    def stop_in_the_middle(fraction):
        if not answers:
            answers.append(service.control(CONTROL_STOP))
            deadline = time.monotonic() + 10
            while not agent._stopping.is_set() and time.monotonic() < deadline:
                time.sleep(0.01)
            health.append(agent.health())

    engine.on_step = stop_in_the_middle
    codes = []
    # As under Windows: the service's main function on a thread of its own.
    thread = threading.Thread(target=lambda: codes.append(service.main()))
    thread.start()
    thread.join(30)
    assert codes == [0] and answers == [NO_ERROR]
    assert health == [(True, "ok")]
    assert leader.released == [job_id] and leader.submitted == []
    assert [status[0] for status in reports] == [START_PENDING, RUNNING, STOP_PENDING, STOPPED]
    assert reports[2][3] == 30_000 and service.reported_stopped


def test_in_a_console_ctrl_c_is_the_stop_control_and_a_second_one_is_a_shutdown(monkeypatch):
    seen = threading.Event()

    def run(stops):
        while stops.count < 2:
            time.sleep(0.01)
        return 0

    service, reports = host(run)
    real = windows.StopSignals

    class Console(real):
        def install(self):
            # Instead of real signal handlers: two "Ctrl+C" a moment apart.
            def press():
                seen.wait(5)
                self.handler(None, None)
                time.sleep(0.3)
                self.handler(None, None)

            threading.Thread(target=press, daemon=True).start()
            seen.set()
            return {}

    monkeypatch.setattr(windows, "StopSignals", Console)
    assert windows.run_foreground(service) == 0
    assert [status[0] for status in reports] == [
        START_PENDING, RUNNING, STOP_PENDING, STOP_PENDING, STOPPED,
    ]
    assert [status[4] for status in reports[2:4]] == [1, 2]  # the checkpoint moves on


def test_the_printed_status_is_one_line_per_report():
    out = io.StringIO()
    windows.printing_report(out)(RUNNING, 0x101, 0, 0, 0)
    assert out.getvalue() == (
        "service status: RUNNING accepts=0x101 exit=0 wait_hint_ms=0 checkpoint=0\n"
    )


# --- the commands that register it ----------------------------------------------------------

PYTHON = r"C:\Program Files\swarmscribe-follower\python\cpython-3.12\python.exe"
BOOT = (
    r"C:\Program Files\swarmscribe-follower\tools\swarmscribe-follower\Lib\site-packages"
    r"\swarmscribe_follower\service_boot.py"
)
ROOT = Path(r"C:\ProgramData\swarmscribe-follower")


def test_the_install_commands_are_exactly_these():
    steps = windows.install_commands([PYTHON, BOOT], ROOT)
    assert steps[0] == [
        "sc.exe", "create", "SwarmScribeFollower", "binPath=", f'"{PYTHON}" "{BOOT}"',
        "start=", "delayed-auto", "obj=", r"NT SERVICE\SwarmScribeFollower",
        "DisplayName=", "SwarmScribe Follower",
    ]
    assert steps[2] == [
        "sc.exe", "failure", "SwarmScribeFollower", "reset=", "86400", "actions=",
        "restart/60000/restart/60000//60000",
    ]
    assert steps[3] == ["sc.exe", "failureflag", "SwarmScribeFollower", "0"]
    acl = [step for step in steps if step[0] == "icacls.exe"]
    assert acl[0][1:4] == [str(ROOT), "/inheritance:r", "/grant:r"]
    assert acl[0][-1] == r"NT SERVICE\SwarmScribeFollower:(OI)(CI)RX"
    assert [step[1] for step in acl[1:]] == [
        str(ROOT / name) for name in ("state", "models", "logs")
    ]
    assert all(step[-1] == r"NT SERVICE\SwarmScribeFollower:(OI)(CI)M" for step in acl[1:])
    text = " ".join(part for step in steps for part in step).lower()
    assert "token" not in text and "password" not in text


def test_no_secret_is_in_the_settings_template_and_the_token_is_a_file_of_its_own():
    template = windows.env_template(ROOT)
    assert f"SWARMSCRIBE_JOIN_TOKEN_FILE={ROOT / 'join-token'}" in template
    assert "SWARMSCRIBE_JOIN_TOKEN=" not in template
    assert f"SWARMSCRIBE_FOLLOWER_STATE_DIR={ROOT / 'state'}" in template
    assert "SWARMSCRIBE_FOLLOWER_SHUTDOWN_GRACE_SECONDS=900" in template


def test_a_store_python_or_an_install_inside_a_profile_cannot_be_a_service(monkeypatch):
    monkeypatch.setenv("USERPROFILE", r"C:\Users\someone")
    store = r"C:\Users\someone\AppData\Local\Microsoft\WindowsApps\Python.3.12\python.exe"
    assert "Microsoft Store" in windows.image_problem([store, BOOT])
    inside = r"C:\Users\someone\AppData\Roaming\uv\tools\swarmscribe-follower\boot.py"
    assert "inside a user's profile" in windows.image_problem([PYTHON, inside])
    assert windows.image_problem([PYTHON, BOOT]) is None


def test_the_grace_period_comes_from_the_environment_the_settings_file_filled(monkeypatch):
    monkeypatch.setenv("SWARMSCRIBE_FOLLOWER_SHUTDOWN_GRACE_SECONDS", "900")
    assert windows.grace_from_environment() == 900.0
    monkeypatch.setenv("SWARMSCRIBE_FOLLOWER_SHUTDOWN_GRACE_SECONDS", "soon")
    assert windows.grace_from_environment() == 8.0
    monkeypatch.delenv("SWARMSCRIBE_FOLLOWER_SHUTDOWN_GRACE_SECONDS")
    assert windows.grace_from_environment() == 8.0


def test_the_log_is_appended_to_and_started_again_when_it_has_grown(tmp_path, monkeypatch):
    path = tmp_path / "logs" / "follower.log"
    with windows.open_log(path) as log:
        log.write("first\n")
    monkeypatch.setattr(windows, "LOG_ROTATE_BYTES", 3)
    with windows.open_log(path) as log:
        log.write("second\n")
    assert path.read_text(encoding="utf-8") == "second\n"
    assert path.with_name("follower.log.1").read_text(encoding="utf-8") == "first\n"
    with windows.open_log(tmp_path / "logs" / "follower.log" / "not-a-folder") as log:
        log.write("goes nowhere, raises nothing\n")


def test_on_another_system_the_service_command_points_at_the_systemd_unit(monkeypatch):
    if os.name == "nt":
        pytest.skip("on Windows `service` is the Windows service")
    out, err = io.StringIO(), io.StringIO()
    assert cli.main(["service", "install"], out=out, err=err) == 2
    assert "deploy/systemd/swarmscribe-follower.service" in err.getvalue()


# --- Windows itself (no administrator rights) -------------------------------------------------


@windows_only
def test_install_print_shows_the_commands_and_changes_nothing():
    out, err = io.StringIO(), io.StringIO()
    assert cli.main(["service", "install", "--print"], out=out, err=err) == 0
    lines = out.getvalue().splitlines()
    assert lines[2].startswith("sc.exe create SwarmScribeFollower binPath= ")
    assert "service_boot.py" in lines[2]
    assert sum(line.startswith("icacls.exe ") for line in lines) == 4
    out, err = io.StringIO(), io.StringIO()
    assert cli.main(["service", "uninstall", "--print"], out=out, err=err) == 0
    assert out.getvalue().splitlines() == [
        "sc.exe stop SwarmScribeFollower", "sc.exe delete SwarmScribeFollower",
    ]


@windows_only
def test_without_administrator_rights_install_and_uninstall_refuse_and_say_so(monkeypatch):
    monkeypatch.setattr(windows, "is_administrator", lambda: False)
    monkeypatch.setattr(windows, "image_problem", lambda command: None)
    ran = []
    monkeypatch.setattr(windows.subprocess, "run", lambda *a, **k: ran.append(a))
    for action in ("install", "uninstall"):
        out, err = io.StringIO(), io.StringIO()
        assert cli.main(["service", action], out=out, err=err) == 2
        assert "must be run as an administrator" in err.getvalue()
    assert ran == []


@windows_only
def test_a_data_folder_made_by_someone_else_is_refused(tmp_path):
    """Anyone may create a folder under %ProgramData% before the install does: only one that
    Administrators or SYSTEM own is used. This test's folder is this account's own."""
    assert windows.root_problem(tmp_path / "absent") is None
    root = tmp_path / "swarmscribe-follower"
    root.mkdir()
    assert "was not made by an administrator" in windows.root_problem(root)


@windows_only
def test_outside_the_control_manager_the_dispatcher_says_so_at_once():
    began = time.monotonic()
    error = windows.serve_under_scm(
        lambda report: ServiceHost(lambda stops: 0, report, grace_seconds=lambda: 8.0)
    )
    assert error == windows.ERROR_FAILED_SERVICE_CONTROLLER_CONNECT
    assert time.monotonic() - began < 10


@windows_only
def test_the_services_own_command_starts_the_follower_from_the_real_interpreter(tmp_path):
    """The command `service install` registers (the real interpreter with service_boot.py,
    which finds the follower's environment by itself), run in a console: it gets as far as
    reading its settings file, and reports exit 2 without SERVICE_STOPPED."""
    settings = tmp_path / "follower.env"
    settings.write_text("this line is not a setting\n", encoding="utf-8")
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("SWARMSCRIBE_")
    }
    done = subprocess.run(
        [*windows.image(), "--foreground", "--env-file", str(settings)],
        capture_output=True, text=True, timeout=120, env=environment,
    )
    assert done.returncode == 2, done.stderr
    assert done.stderr.splitlines() == [
        "service status: START_PENDING accepts=0x0 exit=0 wait_hint_ms=30000 checkpoint=1",
        "service status: RUNNING accepts=0x101 exit=0 wait_hint_ms=0 checkpoint=0",
        f"error: {settings}, line 1: expected NAME=value",
    ]
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/follower/tests/test_windows.py -q`
Expected: an error while collecting, `ImportError: cannot import name 'windows' from 'swarmscribe_follower'`.

- [ ] **Step 3: The module**

Create `packages/follower/src/swarmscribe_follower/windows.py`:

```python
"""The follower as a Windows service (follower spec 8.3, ruling R4).

Three parts, kept apart so that all but the last can be run and tested without Windows'
service control manager, and without administrator rights:

- `ServiceHost`: what the service does. It tells the control manager its status through a
  `report` function and acts on the controls it is sent; the follower itself is the `run`
  function it is given. Tests give it a recording `report`; `swarmscribe-follower service
  foreground` gives it one that prints.
- `install_commands` / `uninstall_commands`: the exact `sc.exe` and `icacls.exe` command lines
  that register the service and make its folders. `service install --print` prints them.
- `serve_under_scm`: the binding to the control manager (`StartServiceCtrlDispatcherW`,
  `RegisterServiceCtrlHandlerExW`, `SetServiceStatus`). Only a process the control manager
  started can run it; it is the one part this repository's tests cannot run.

How a stop reaches the follower. A service gets no signal. `SERVICE_CONTROL_STOP` arrives on
the dispatcher's thread, in `ServiceHost.control`, which counts it on a `StopSignals` that is
never installed as a signal handler; `Agent.run_supervised` polls that counter exactly as it
polls real signals, so one stop is `agent.stop()` and a second, or a system shutdown, is
`agent.stop(now=True)`. `run_supervised` is also what ticks for `/healthz`.

What Windows is told when the follower ends (`ServiceHost.main`):

    exit code                          told                               Windows then
    0  stopped, or drained             SERVICE_STOPPED, no error          nothing
    4  revoked or refused, 5 protocol  SERVICE_STOPPED, service error 4/5 nothing
    1, 2, 3 after a stop was asked     SERVICE_STOPPED, service error n   nothing
    1, 2, 3 otherwise                  nothing: the process just ends     the recovery actions

Windows has no rule per exit code. Its recovery actions run when a service's process ends
without having said SERVICE_STOPPED, so that is what the follower does for the exits a
restart may cure, and `install_commands` sets the actions to: restart after a minute, twice,
then leave it stopped; the count starts again after a day without a failure.

This module imports only the standard library when it is imported: the control manager gives
a service 30 seconds to connect, and the follower's own imports (the engine, the model
libraries) are done after that, on the service's thread."""

import os
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from .errors import EXIT_OK, EXIT_PROTOCOL, EXIT_UNAUTHORISED
from .signals import StopSignals

SERVICE_NAME = "SwarmScribeFollower"
DISPLAY_NAME = "SwarmScribe Follower"
DESCRIPTION = "Takes recordings from a SwarmScribe leader and transcribes them."
ACCOUNT = rf"NT SERVICE\{SERVICE_NAME}"  # the service's own virtual account: no password

# SERVICE_STATUS.dwCurrentState
STOPPED, START_PENDING, STOP_PENDING, RUNNING = 1, 2, 3, 4
STATE_NAMES = {
    STOPPED: "STOPPED", START_PENDING: "START_PENDING", STOP_PENDING: "STOP_PENDING",
    RUNNING: "RUNNING",
}
# Controls, and the bits that say which are accepted
CONTROL_STOP, CONTROL_INTERROGATE, CONTROL_SHUTDOWN, CONTROL_PRESHUTDOWN = 1, 4, 5, 15
ACCEPT_STOP, ACCEPT_PRESHUTDOWN = 0x1, 0x100
NO_ERROR, ERROR_CALL_NOT_IMPLEMENTED, ERROR_SERVICE_SPECIFIC_ERROR = 0, 120, 1066
ERROR_FAILED_SERVICE_CONTROLLER_CONNECT = 1063  # "not started by the control manager"

EXIT_UNEXPECTED = 1
EXIT_CONFIGURATION = 2
FINAL_EXITS = frozenset({EXIT_OK, EXIT_UNAUTHORISED, EXIT_PROTOCOL})  # never restarted
START_HINT_MS = 30_000
STOP_MARGIN_SECONDS = 30.0  # past the grace period: the release and deregister calls
SHUTDOWN_HINT_MS = 30_000
RESTART_DELAY_MS = 60_000
FAILURE_RESET_SECONDS = 86_400

Report = Callable[[int, int, int, int, int], None]
"""(state, accepted controls, service-specific exit code, wait hint in ms, checkpoint)."""


class ServiceHost:
    def __init__(
        self,
        run: Callable[[StopSignals], int],
        report: Report,
        *,
        grace_seconds: Callable[[], float],
    ) -> None:
        """`run(stops)` is the follower: it returns its exit code and watches `stops.count`
        (one: stop; two: release now). `grace_seconds()` is the follower's
        `shutdown_grace_seconds`, asked when a stop arrives (the settings are read on the
        service's thread, after the service has started)."""
        self._run, self._report, self._grace_seconds = run, report, grace_seconds
        self.stops = StopSignals()  # counted by `control`; never a signal handler
        self._lock = threading.Lock()
        self._state = STOPPED
        self._checkpoint = 0
        self.reported_stopped = False

    def _tell(self, state: int, *, accepted: int = 0, specific: int = 0, hint_ms: int = 0) -> None:
        with self._lock:
            pending = state in (START_PENDING, STOP_PENDING)
            self._checkpoint = self._checkpoint + 1 if pending else 0
            self._state = state
            self._report(state, accepted, specific, hint_ms, self._checkpoint)

    def control(self, code: int) -> int:
        """The control handler. Runs on the dispatcher's thread and must return at once: it
        counts, reports, and leaves the stopping to the follower's own thread."""
        if code == CONTROL_INTERROGATE:
            return NO_ERROR
        if code == CONTROL_STOP:
            self.stops.handler(None, None)
            hint = int((max(0.0, self._grace_seconds()) + STOP_MARGIN_SECONDS) * 1000)
            self._tell(STOP_PENDING, hint_ms=hint)
            return NO_ERROR
        if code in (CONTROL_PRESHUTDOWN, CONTROL_SHUTDOWN):
            # The machine is going down: no grace period, hand the job back now.
            self.stops.handler(None, None)
            self.stops.handler(None, None)
            self._tell(STOP_PENDING, hint_ms=SHUTDOWN_HINT_MS)
            return NO_ERROR
        return ERROR_CALL_NOT_IMPLEMENTED

    def main(self) -> int:
        """The service's main function: runs the follower and says how it ended. Returns the
        follower's exit code; `reported_stopped` says whether Windows was told SERVICE_STOPPED
        (when it was not, the caller ends the process and Windows' recovery actions run)."""
        self._tell(START_PENDING, hint_ms=START_HINT_MS)
        self._tell(RUNNING, accepted=ACCEPT_STOP | ACCEPT_PRESHUTDOWN)
        try:
            code = self._run(self.stops)
        except Exception:
            code = EXIT_UNEXPECTED
        if code in FINAL_EXITS or self.stops.count:
            self._tell(STOPPED, specific=code)
            self.reported_stopped = True
        return code


# --- the image, the folders and the commands that register the service ------------------------


def data_root() -> Path:
    """Where the service keeps its settings, token, state, models and log."""
    return Path(os.environ.get("ProgramData") or r"C:\ProgramData") / "swarmscribe-follower"


def env_file() -> Path:
    return data_root() / "follower.env"


def log_file() -> Path:
    return data_root() / "logs" / "follower.log"


def env_template(root: Path) -> str:
    return (
        "# Settings of the SwarmScribe follower service (NAME=value; see the README).\n"
        "# The join or pool token is NOT written here: put it, alone, in the file that\n"
        "# SWARMSCRIBE_JOIN_TOKEN_FILE names. It is read once, at the first start.\n"
        "SWARMSCRIBE_LEADER_URL=\n"
        f"SWARMSCRIBE_JOIN_TOKEN_FILE={root / 'join-token'}\n"
        f"SWARMSCRIBE_FOLLOWER_STATE_DIR={root / 'state'}\n"
        f"SWARMSCRIBE_FOLLOWER_MODEL_DIR={root / 'models'}\n"
        "# A stop waits this long for the recording in hand before it gives it back.\n"
        "SWARMSCRIBE_FOLLOWER_SHUTDOWN_GRACE_SECONDS=900\n"
    )


def boot_script() -> Path:
    return Path(__file__).with_name("service_boot.py")


def image(base_python: str | None = None) -> list[str]:
    """The command the control manager starts: the real interpreter (not the environment's
    `python.exe`, which is a launcher that starts the interpreter as a child, so the control
    manager would be talking to the wrong process) with the boot script, which puts the
    follower's environment on the import path itself."""
    return [base_python or getattr(sys, "_base_executable", None) or sys.executable,
            str(boot_script())]


def image_problem(command: list[str]) -> str | None:
    """Why a service account could not start `command`, or None."""
    python = command[0]
    if "\\windowsapps\\" in python.lower():
        return (
            "this follower is installed on the Microsoft Store's Python, which belongs to one"
            " user and cannot run a service; install it on a Python from uv or python.org"
            " (see the README: `uv python install`, then `uv tool install --python ...`)"
        )
    profile = (os.environ.get("USERPROFILE") or "").lower()
    for path in command:
        if profile and path.lower().startswith(profile + "\\"):
            return (
                f"{path} is inside a user's profile, which the service's account cannot read;"
                r" install the follower for the machine (see the README: under C:\Program Files)"
            )
    return None


def root_problem(root: Path) -> str | None:
    """Why the data folder cannot be used as it is, or None. Anyone may create a folder under
    %ProgramData%: one that an account other than Administrators or SYSTEM made could hold a
    settings file of that account's choosing, and its owner could change its permissions back
    at any time."""
    from . import winacl

    for path in (root, root / "follower.env", root / "join-token"):
        if not path.exists():
            continue
        owner, _ = winacl.read_acl(path)
        if owner not in (winacl.ADMINISTRATORS, winacl.SYSTEM):
            return (
                f"{path} exists and was not made by an administrator (its owner is {owner});"
                " look at what is in it, remove it, and run `service install` again"
            )
    return None


def command_line(command: list[str]) -> str:
    """One command as text: for printing, and (every part quoted) for the service's image."""
    return subprocess.list2cmdline(command)


def image_line(command: list[str]) -> str:
    return " ".join(f'"{part}"' for part in command)


def install_commands(command: list[str], root: Path) -> list[list[str]]:
    """What `service install` runs, in order. `sc.exe` wants each `name=` and its value as
    two arguments."""
    private = "*S-1-5-32-544:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F"  # Administrators, SYSTEM
    return [
        [
            "sc.exe", "create", SERVICE_NAME, "binPath=", image_line(command),
            "start=", "delayed-auto", "obj=", ACCOUNT, "DisplayName=", DISPLAY_NAME,
        ],
        ["sc.exe", "description", SERVICE_NAME, DESCRIPTION],
        [
            "sc.exe", "failure", SERVICE_NAME, "reset=", str(FAILURE_RESET_SECONDS), "actions=",
            f"restart/{RESTART_DELAY_MS}/restart/{RESTART_DELAY_MS}//{RESTART_DELAY_MS}",
        ],
        ["sc.exe", "failureflag", SERVICE_NAME, "0"],
        # The settings and the token: administrators write them, the service reads them.
        ["icacls.exe", str(root), "/inheritance:r", "/grant:r", *private, f"{ACCOUNT}:(OI)(CI)RX"],
        # What the service writes: its credential and scratch, its models, its log.
        *(
            ["icacls.exe", str(root / name), "/grant:r", f"{ACCOUNT}:(OI)(CI)M"]
            for name in ("state", "models", "logs")
        ),
    ]


def uninstall_commands() -> list[list[str]]:
    return [["sc.exe", "stop", SERVICE_NAME], ["sc.exe", "delete", SERVICE_NAME]]


def is_administrator() -> bool:
    import ctypes

    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def install(out, err, *, print_only: bool) -> int:
    """`swarmscribe-follower service install [--print]`."""
    command, root = image(), data_root()
    problem = image_problem(command)
    steps = install_commands(command, root)
    if print_only:
        if problem:
            print(f"warning: {problem}", file=err)
        print(f"folders: {root} with state, models and logs inside", file=out)
        print(f"settings file: {root / 'follower.env'} (written if it is not there)", file=out)
        for step in steps:
            print(command_line(step), file=out)
        return EXIT_OK
    if problem:
        print(f"error: {problem}", file=err)
        return EXIT_CONFIGURATION
    if not is_administrator():
        print(
            "error: `service install` must be run as an administrator (open PowerShell with"
            " \"Run as administrator\"); `service install --print` shows what it would do",
            file=err,
        )
        return EXIT_CONFIGURATION
    problem = root_problem(root)
    if problem:
        print(f"error: {problem}", file=err)
        return EXIT_CONFIGURATION
    for name in ("state", "models", "logs"):
        (root / name).mkdir(parents=True, exist_ok=True)
    settings = root / "follower.env"
    if not settings.exists():
        settings.write_text(env_template(root), encoding="utf-8")
    for step in steps:
        done = subprocess.run(step, capture_output=True, text=True)
        if done.returncode != 0:
            said = (done.stdout + done.stderr).strip().splitlines()
            print(f"error: `{command_line(step)}` failed: {said[-1] if said else ''}", file=err)
            return EXIT_UNEXPECTED
    print(f"installed the service {SERVICE_NAME} (not started)", file=out)
    print(f"1. set SWARMSCRIBE_LEADER_URL in {settings}", file=out)
    print(f"2. put the join token, alone, in {root / 'join-token'}", file=out)
    print(f"3. start it: sc.exe start {SERVICE_NAME}    its log: {log_file()}", file=out)
    return EXIT_OK


def uninstall(out, err, *, print_only: bool) -> int:
    """`swarmscribe-follower service uninstall [--print]`. The folders stay: they hold the
    follower's credential."""
    steps = uninstall_commands()
    if print_only:
        for step in steps:
            print(command_line(step), file=out)
        return EXIT_OK
    if not is_administrator():
        print("error: `service uninstall` must be run as an administrator", file=err)
        return EXIT_CONFIGURATION
    subprocess.run(steps[0], capture_output=True, text=True)  # it may not be running
    done = subprocess.run(steps[1], capture_output=True, text=True)
    if done.returncode != 0:
        said = (done.stdout + done.stderr).strip().splitlines()
        print(f"error: `{command_line(steps[1])}` failed: {said[-1] if said else ''}", file=err)
        return EXIT_UNEXPECTED
    print(
        f"removed the service {SERVICE_NAME}; {data_root()} is kept (it holds the follower's"
        " credential: run `swarmscribe-follower leave` first to give that up, or delete it)",
        file=out,
    )
    return EXIT_OK


# --- running it: in a console, and under the control manager ------------------------------------


LOG_ROTATE_BYTES = 10 * 1024 * 1024


def open_log(path: Path):
    """The service's log, opened for appending (a service has no console). A log that has
    grown past 10 MiB is kept as `<name>.1` and started again. Never raises: a log that
    cannot be opened becomes the null device, and the follower still runs."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > LOG_ROTATE_BYTES:
            os.replace(path, path.with_name(path.name + ".1"))
        return open(path, "a", encoding="utf-8", buffering=1)
    except OSError:
        return open(os.devnull, "w", encoding="utf-8")


def grace_from_environment() -> float:
    try:
        return float(os.environ.get("SWARMSCRIBE_FOLLOWER_SHUTDOWN_GRACE_SECONDS") or 8.0)
    except ValueError:
        return 8.0


def follower(
    settings_file: Path, required: bool, open_output: Callable[[], object]
) -> Callable[[StopSignals], int]:
    """The `run` of a ServiceHost: `swarmscribe-follower --env-file <settings_file> run`, with
    its output where `open_output()` says. The output is opened, the settings file read and
    the follower's own (slow) imports done here, on the service's thread, once the control
    manager has been answered. `required`: a missing file is an error (a file named on the
    command line); the service's default file may be absent."""

    def run(stops: StopSignals) -> int:
        from . import envfile

        log = open_output()
        try:
            if required or settings_file.exists():
                envfile.load(settings_file)
        except envfile.EnvFileError as error:
            print(f"error: {error}", file=log, flush=True)
            return EXIT_CONFIGURATION
        from . import main as cli

        return cli.main(["run"], out=log, err=log, signals=stops)

    return run


def run_foreground(host: ServiceHost) -> int:
    """Run a ServiceHost in a console as the control manager would: `host.main` on a thread
    of its own, the controls delivered from this one. Ctrl+C is a stop control; a second one
    is a shutdown."""
    console = StopSignals()
    previous = console.install()
    codes: list[int] = []
    thread = threading.Thread(target=lambda: codes.append(host.main()), name="service-main")
    thread.start()
    seen = 0
    try:
        while thread.is_alive():
            thread.join(0.1)
            if console.count > seen:
                seen = console.count
                host.control(CONTROL_STOP if seen == 1 else CONTROL_PRESHUTDOWN)
    finally:
        thread.join()
        console.restore(previous)
    return codes[0] if codes else EXIT_UNEXPECTED


def printing_report(out) -> Report:
    def report(state: int, accepted: int, specific: int, hint_ms: int, checkpoint: int) -> None:
        print(
            f"service status: {STATE_NAMES[state]} accepts={accepted:#x} exit={specific}"
            f" wait_hint_ms={hint_ms} checkpoint={checkpoint}",
            file=out, flush=True,
        )

    return report


def serve_under_scm(make_host: Callable[[Report], ServiceHost]) -> int:
    """Connect to the service control manager and run the service; returns when the service
    has stopped. Returns ERROR_FAILED_SERVICE_CONTROLLER_CONNECT (1063) at once when this
    process was not started by the control manager.

    When the follower ends with an exit that Windows should answer by restarting it, the
    process is ended here, with that exit code and without SERVICE_STOPPED (module docstring)."""
    import ctypes
    import logging
    from ctypes import wintypes

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    main_type = ctypes.WINFUNCTYPE(None, wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR))
    handler_type = ctypes.WINFUNCTYPE(
        wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.LPVOID
    )

    class Status(ctypes.Structure):  # SERVICE_STATUS
        _fields_ = [
            (name, wintypes.DWORD)
            for name in (
                "service_type", "state", "accepted", "exit_code", "specific", "checkpoint",
                "wait_hint",
            )
        ]

    class Entry(ctypes.Structure):  # SERVICE_TABLE_ENTRYW
        _fields_ = [("name", wintypes.LPWSTR), ("main", main_type)]

    advapi.RegisterServiceCtrlHandlerExW.argtypes = [
        wintypes.LPCWSTR, handler_type, wintypes.LPVOID,
    ]
    advapi.RegisterServiceCtrlHandlerExW.restype = wintypes.HANDLE
    advapi.SetServiceStatus.argtypes = [wintypes.HANDLE, ctypes.POINTER(Status)]
    advapi.StartServiceCtrlDispatcherW.argtypes = [ctypes.POINTER(Entry)]
    handle = wintypes.HANDLE()

    def report(state: int, accepted: int, specific: int, hint_ms: int, checkpoint: int) -> None:
        status = Status(
            0x10,  # SERVICE_WIN32_OWN_PROCESS
            state, accepted,
            ERROR_SERVICE_SPECIFIC_ERROR if specific else NO_ERROR, specific,
            checkpoint, hint_ms,
        )
        advapi.SetServiceStatus(handle, ctypes.byref(status))

    host = make_host(report)

    @handler_type
    def handler(control, _event_type, _event_data, _context):
        return host.control(control)

    @main_type
    def service_main(_argc, _argv):
        handle.value = advapi.RegisterServiceCtrlHandlerExW(SERVICE_NAME, handler, None)
        if not handle.value:
            os._exit(EXIT_UNEXPECTED)
        code = EXIT_UNEXPECTED
        try:
            code = host.main()
        finally:
            if not host.reported_stopped:
                logging.shutdown()
                os._exit(code)

    table = (Entry * 2)(Entry(SERVICE_NAME, service_main), Entry(None, main_type()))
    if not advapi.StartServiceCtrlDispatcherW(table):
        return ctypes.get_last_error()
    return NO_ERROR


def service_process(argv: list[str]) -> int:
    """The service's process: what `service_boot.py` and `swarmscribe-follower service
    foreground` run. `--foreground`: in this console, printing each status Windows would be
    told, with Ctrl+C as the stop control. `--env-file PATH`: another settings file than
    the one in the service's data folder (`env_file()`)."""
    foreground = "--foreground" in argv
    named = argv[argv.index("--env-file") + 1] if "--env-file" in argv[:-1] else None
    settings_file = Path(named) if named else env_file()
    console = sys.stderr

    def to_the_log_file():
        # A service has no console: `sys.stderr` is None, and a library that prints (a
        # download's progress bar) would fail. Everything goes to the log file.
        sys.stdout = sys.stderr = open_log(log_file())
        return sys.stderr

    def make_host(report: Report) -> ServiceHost:
        output = (lambda: console) if foreground else to_the_log_file
        return ServiceHost(
            follower(settings_file, named is not None, output), report,
            grace_seconds=grace_from_environment,
        )

    if foreground:
        return run_foreground(make_host(printing_report(console)))
    error = serve_under_scm(make_host)
    if error == ERROR_FAILED_SERVICE_CONTROLLER_CONNECT:
        if console is not None:
            print(
                "error: this command is what the Windows service control manager starts; to"
                " run the service's code in a console use `swarmscribe-follower service"
                " foreground`",
                file=console,
            )
        return EXIT_CONFIGURATION
    return EXIT_UNEXPECTED if error else EXIT_OK
```

- [ ] **Step 4: What Windows starts**

Create `packages/follower/src/swarmscribe_follower/service_boot.py`:

```python
"""What the Windows service control manager starts (see `windows.image`).

The service's command is the real interpreter with this file's path, not the environment's
`python.exe`: on Windows that one is a launcher which starts the interpreter as a child
process, and the control manager must talk to the process it started. Run this way, Python
knows nothing of the environment the follower is installed in, so this file puts it on the
import path first: the folder this package sits in is the environment's `site-packages`.

Standard library only, and nothing is imported before the path is right."""

import site
import sys
from pathlib import Path


def bootstrap() -> None:
    package = Path(__file__).resolve().parent
    # Python put this file's own folder first on the import path. There the follower's
    # modules (`logs`, `models`, `signals`) would be importable by their bare names.
    sys.path[:] = [entry for entry in sys.path if not entry or Path(entry).resolve() != package]
    site.addsitedir(str(package.parent))


def main() -> int:
    bootstrap()
    from swarmscribe_follower import windows

    return windows.service_process(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: The `service` command**

In `packages/follower/src/swarmscribe_follower/main.py` (as F4a left it):

```diff
--- a/packages/follower/src/swarmscribe_follower/main.py
+++ b/packages/follower/src/swarmscribe_follower/main.py
@@ -1,5 +1,5 @@
-"""swarmscribe-follower: run, join, leave, doctor, cuda-paths (follower spec 4, 5.2, 5.3,
-8.3).
+"""swarmscribe-follower: run, join, leave, doctor, cuda-paths, service (follower spec 4, 5.2,
+5.3, 8.3).
 
 Every way this ends is a one-line `error: ...` and an exit code from errors.py; no traceback
 reaches the user. The join token is never an argument (spec 5.3: it would show in process
@@ -139,6 +139,24 @@ def command_cuda_paths(out: TextIO, err: TextIO) -> int:
     return EXIT_OK
 
 
+def command_service(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
+    if os.name != "nt":
+        print(
+            "error: `service` is the Windows service; on Linux install the systemd unit"
+            " deploy/systemd/swarmscribe-follower.service (see the README)",
+            file=err,
+        )
+        return EXIT_CONFIGURATION
+    from . import windows
+
+    if args.action == "install":
+        return windows.install(out, err, print_only=args.print)
+    if args.action == "uninstall":
+        return windows.uninstall(out, err, print_only=args.print)
+    named = ["--env-file", args.env_file] if args.env_file else []
+    return windows.service_process(["--foreground", *named])
+
+
 def configure_environment(settings: Settings) -> None:
     """Tell the model loader where its cache is and whether it may download. Must run
     before the first model is loaded: Hugging Face's library reads these when it is
@@ -436,6 +454,16 @@ def parser() -> argparse.ArgumentParser:
     commands.add_parser(
         "cuda-paths", help="print the folders of the GPU libraries the cuda extra installed"
     )
+    service = commands.add_parser("service", help="the Windows service")
+    service.add_argument(
+        "action",
+        choices=("install", "uninstall", "foreground"),
+        help="install or uninstall the service (as an administrator), or run the service's"
+        " code in this console (Ctrl+C is the stop control)",
+    )
+    service.add_argument(
+        "--print", action="store_true", help="install, uninstall: only print the commands"
+    )
     return top
 
 
@@ -454,6 +482,8 @@ def main(
     args = parser().parse_args(argv)
     if args.command == "cuda-paths":
         return command_cuda_paths(out, err)
+    if args.command == "service":
+        return command_service(args, out, err)  # it reads its settings file itself
     if args.env_file:
         try:
             envfile.load(args.env_file)
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest packages/follower/tests/test_windows.py -q`
Expected on Windows: `25 passed, 1 skipped` (the one skipped test is the one for other systems). On Linux: `21 passed, 5 skipped`, the skipped being the tests that call Windows itself.

- [ ] **Step 7: Look at it by hand, without administrator rights**

Two commands that change nothing (Git Bash, in the worktree). `service install` without `--print` is **not** among them: Global Constraints forbid it, and its refusal without administrator rights is a test.

```bash
python -m uv run swarmscribe-follower service install --print
python -m uv run python packages/follower/src/swarmscribe_follower/service_boot.py; echo "exit $?"
```

Expected. The first prints a `warning:` line on stderr when the development environment's Python is the Microsoft Store's (it is, on the development machine: `warning: this follower is installed on the Microsoft Store's Python, which belongs to one user and cannot run a service; ...`), then:

```
folders: C:\ProgramData\swarmscribe-follower with state, models and logs inside
settings file: C:\ProgramData\swarmscribe-follower\follower.env (written if it is not there)
sc.exe create SwarmScribeFollower binPath= "\"...\python.exe\" \"...\swarmscribe_follower\service_boot.py\"" start= delayed-auto obj= "NT SERVICE\SwarmScribeFollower" DisplayName= "SwarmScribe Follower"
sc.exe description SwarmScribeFollower "Takes recordings from a SwarmScribe leader and transcribes them."
sc.exe failure SwarmScribeFollower reset= 86400 actions= restart/60000/restart/60000//60000
sc.exe failureflag SwarmScribeFollower 0
icacls.exe C:\ProgramData\swarmscribe-follower /inheritance:r /grant:r *S-1-5-32-544:(OI)(CI)F *S-1-5-18:(OI)(CI)F "NT SERVICE\SwarmScribeFollower:(OI)(CI)RX"
icacls.exe C:\ProgramData\swarmscribe-follower\state /grant:r "NT SERVICE\SwarmScribeFollower:(OI)(CI)M"
icacls.exe C:\ProgramData\swarmscribe-follower\models /grant:r "NT SERVICE\SwarmScribeFollower:(OI)(CI)M"
icacls.exe C:\ProgramData\swarmscribe-follower\logs /grant:r "NT SERVICE\SwarmScribeFollower:(OI)(CI)M"
```

(the two `...` stand for the interpreter's folder and the checkout's `packages\follower\src`). The second prints ``error: this command is what the Windows service control manager starts; to run the service's code in a console use `swarmscribe-follower service foreground` `` and `exit 2`, at once.

**Neither may create `C:\ProgramData\swarmscribe-follower`.** Check: `ls /c/ProgramData | grep -i swarm` prints nothing. (The planner's first version opened the service's log before it had asked the control manager, and so created that folder as an ordinary user; the log is now opened by the service's own thread.)

- [ ] **Step 8: The gate, and commit**

Run: `uv run ruff check .` — expected `All checks passed!`. Run: `uv run pytest` once, to the end — expected no failure (about `3196 passed, 21 skipped, 5 deselected` on the development machine).

```bash
git add packages/follower/src/swarmscribe_follower/windows.py packages/follower/src/swarmscribe_follower/service_boot.py packages/follower/src/swarmscribe_follower/main.py packages/follower/tests/test_windows.py
git commit -m "feat(follower): the Windows service: what it tells Windows, its install commands, its process

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The Windows CI job

**Files:**
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: the follower package's tests, which need no service of their own: without `SWARMSCRIBE_TEST_DATABASE_URL` the contract tests start a local Postgres from the `pgserver` package, as they do on the development machine.
- Produces: the job `follower-windows`.

- [ ] **Step 1: Add the job**

In `.github/workflows/ci.yml`, after the job `follower-cuda-image` and before `chart`, add:

```yaml
  # The follower's tests on Windows: signals, the credential's access control list, the
  # working set, the service's own code and its command run from the real interpreter. No GPU
  # here, and no service control manager: registering, starting and stopping the service is
  # proven by hand (docs/superpowers/plans/2026-10-05-follower-f4-outcomes.md).
  follower-windows:
    runs-on: windows-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      # 3.12 by name: where the runner has no Python of the 3.11 or 3.12 line, uv would fetch
      # the newest, for which the test database (pgserver) has no wheel.
      - run: uv sync --python 3.12
      - run: uv run pytest packages/follower
```

- [ ] **Step 2: Check the file**

Run: `uv run python -c "import yaml; jobs = yaml.safe_load(open('.github/workflows/ci.yml', encoding='utf-8'))['jobs']; print(list(jobs)); print(jobs['follower-windows']['steps'][-1])"`
Expected: `['test', 'compose-e2e', 'web', 'web-e2e', 'console-compose-e2e', 'follower-compose-e2e', 'follower-cuda-image', 'follower-windows', 'chart']` and `{'run': 'uv run pytest packages/follower'}`.

- [ ] **Step 3: Run what the job runs, here**

Run: `uv run pytest packages/follower` (once, to the end; about a minute and a half).
Expected on the development machine: `737 passed, 14 skipped`, no failure. This is the job's command on the same operating system; what it does not show is GitHub's image (ruling 11).

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: run the follower's tests on Windows

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The native Windows proof on the GPU, recorded; and the owner's procedure

**Files:**
- Create: `e2e/follower-windows/run_e2e.py`
- Modify: `.gitignore`, `.dockerignore`
- Modify: `docs/superpowers/plans/2026-10-05-follower-f4-outcomes.md`

**Interfaces:**
- Consumes: `windows.image()`, the printed status lines and `service foreground`'s behaviour (Task 1); `cuda-paths`, `--env-file`, `deploy/follower-constraints.txt` (F4a); `e2e/follower-kind/admin.py` (its commands are listed in the driver's use of them); the leader's test image `swarmscribe-leader:e2e`.
- Produces: `python e2e/follower-windows/run_e2e.py up | run [--cpu] | down`, and for the owner's procedure `token PATH`, `recording NAME REPEATS`, `state`. Environment: `E2E_PREFIX` (default `follower-windows`), `LEADER_IMAGE`, `UV`. `run` ends with a line that starts `passed (large-v3 on cuda;` (or `passed (tiny.en on cpu;`).

- [ ] **Step 1: The driver**

Create `e2e/follower-windows/run_e2e.py`:

```python
"""The native Windows install of the follower, against a real leader, WITHOUT administrator
rights (follower spec 8.3; plan F4b).

What it proves: `uv tool install` of the follower with its `cuda` extra, from wheels built
from this repository, on a Python that uv manages, in a folder of its own; that the GPU
libraries are found with nothing copied and no PATH; and the service's own code, started
exactly as the service control manager will start it (the real interpreter with
`service_boot.py`), in a console (`--foreground`): it registers, transcribes, hands a
recording back when it is stopped mid-job, comes back as the same follower, and says the
right thing to Windows when it is revoked and when the machine cannot do the work.

What it cannot prove: anything that needs the service control manager itself (the
registration, the service's account and its folders' permissions, the stop control arriving
from Windows, the recovery actions). That is the owner's procedure in the outcomes document.

    uv run python e2e/follower-windows/run_e2e.py up          # wheels, leader, the install
    uv run python e2e/follower-windows/run_e2e.py run         # on the GPU, large-v3
    uv run python e2e/follower-windows/run_e2e.py run --cpu   # on the CPU, tiny.en
    uv run python e2e/follower-windows/run_e2e.py down

For the owner's procedure with the real service (the outcomes document), against the same
leader: `token PATH` writes a new pool token into PATH and prints nothing of it,
`recording NAME REPEATS` queues the speech fixture REPEATS times over, and `state` prints the
followers and the recordings.

`run` needs a fresh `up` each time (it revokes). The leader is the leader's test image with
Postgres beside it, published on 127.0.0.1:18080 only and administered through `docker exec`
(e2e/follower-kind/admin.py). Models come from the user's own Hugging Face cache (large-v3 is
3 GB the first time). Nothing here prints a token, a credential or a link.

Environment: E2E_PREFIX names the containers and the network (default `follower-windows`);
LEADER_IMAGE (`swarmscribe-leader:e2e`); UV is how uv is run (`uv`; on the development
machine `python -m uv`)."""

import json
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WORK = HERE / "work"
DIST = WORK / "dist"
ADMIN = ROOT / "e2e" / "follower-kind" / "admin.py"
PREFIX = os.environ.get("E2E_PREFIX", "follower-windows")
NETWORK, POSTGRES, LEADER = f"{PREFIX}-net", f"{PREFIX}-postgres", f"{PREFIX}-leader"
LEADER_IMAGE = os.environ.get("LEADER_IMAGE", "swarmscribe-leader:e2e")
UV = shlex.split(os.environ.get("UV", "uv"))
PORT = 18080
LEADER_URL = f"http://127.0.0.1:{PORT}"
LEADER_ENV = {
    "SWARMSCRIBE_DATABASE_URL": f"postgresql://postgres:postgres@{POSTGRES}:5432/swarmscribe",
    # The leader builds its own file links from this, and the follower fetches them from
    # Windows: it must be the published address (follower spec 12.6).
    "SWARMSCRIBE_PUBLIC_URL": LEADER_URL,
    "SWARMSCRIBE_LINK_KEY": "follower-windows-link-key-0123456789abcdef",
    "SWARMSCRIBE_LEASE_SECONDS": "8",
    "SWARMSCRIBE_HEARTBEAT_SECONDS": "2",
    "SWARMSCRIBE_REAPER_INTERVAL_SECONDS": "1",
    "SWARMSCRIBE_SCANNER_INTERVAL_SECONDS": "1",
    "SWARMSCRIBE_CLAIM_RETRY_AFTER": "1",
    "SWARMSCRIBE_FOLLOWER_GONE_AFTER_SECONDS": "20",
}
# Everything uv makes stays under work/: its Python, the tool's environment, the command.
UV_ENV = {
    "UV_PYTHON_INSTALL_DIR": str(WORK / "python"),
    "UV_TOOL_DIR": str(WORK / "tools"),
    "UV_TOOL_BIN_DIR": str(WORK / "bin"),
}
FOLLOWER = WORK / "bin" / "swarmscribe-follower.exe"
TOOL_PYTHON = WORK / "tools" / "swarmscribe-follower" / "Scripts" / "python.exe"
ENV_FILE = WORK / "follower.env"
TOKEN_FILE = WORK / "join-token"
OUTPUT = WORK / "service-output.txt"
# Postgres needs a moment after its container starts.
MIGRATE = "for i in $(seq 60); do swarmscribe-leader migrate && exit 0; sleep 2; done; exit 1"
LONG_REPEATS = 96  # the 5-second speech fixture 96 times: eight minutes
STEP_SECONDS = 300.0


def expect(condition: bool, message: str) -> None:
    if not condition:
        print(f"FAILED: {message}", file=sys.stderr)
        raise SystemExit(1)


def run(*command: str, stdin: str | None = None, check: bool = True, env=None) -> str:
    done = subprocess.run(
        command, input=stdin, capture_output=True, text=True, env=env, encoding="utf-8",
        errors="replace",
    )
    if check and done.returncode != 0:
        said = (done.stdout + done.stderr).strip()[-1500:]
        expect(False, f"`{' '.join(command[:6])} ...` exited {done.returncode}: {said}")
    return done.stdout


def docker(*arguments: str, **kwargs) -> str:
    return run("docker", *arguments, **kwargs)


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


def job(key: str) -> dict:
    return admin("state")["jobs"].get(f"talks/{key}", {"state": "absent", "tried": []})


def followers(state: str) -> list[dict]:
    return [row for row in admin("state")["followers"] if row["state"] == state]


def uv_env() -> dict[str, str]:
    return {**os.environ, **UV_ENV}


def follower_env() -> dict[str, str]:
    """The environment of a follower started here: nothing of SwarmScribe's inherited."""
    return {
        name: value for name, value in os.environ.items() if not name.startswith("SWARMSCRIBE_")
    }


def write_env(**changes: str) -> None:
    values = {
        "SWARMSCRIBE_LEADER_URL": LEADER_URL,
        "SWARMSCRIBE_FOLLOWER_ALLOW_HTTP": "1",  # the test leader is plain http
        "SWARMSCRIBE_JOIN_TOKEN_FILE": str(TOKEN_FILE),
        "SWARMSCRIBE_FOLLOWER_STATE_DIR": str(WORK / "state"),
        "SWARMSCRIBE_FOLLOWER_SHUTDOWN_GRACE_SECONDS": "1",
        **changes,
    }
    ENV_FILE.write_text("".join(f"{name}={value}\n" for name, value in values.items()), "utf-8")


def service_image() -> list[str]:
    """The command `service install` registers, asked of the installed follower itself."""
    printed = run(
        str(TOOL_PYTHON), "-c",
        "import json; from swarmscribe_follower import windows; print(json.dumps(windows.image()))",
    )
    return json.loads(printed)


class Service:
    """The service's code in a console of its own, started by the service's own command."""

    def __init__(self) -> None:
        self._out = OUTPUT.open("w", encoding="utf-8")
        self.process = subprocess.Popen(
            [*service_image(), "--foreground", "--env-file", str(ENV_FILE)],
            stdout=self._out, stderr=subprocess.STDOUT, env=follower_env(),
            # A group of its own, so that the stop control below reaches it alone.
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )

    def stop_control(self) -> None:
        self.process.send_signal(signal.CTRL_BREAK_EVENT)  # what Ctrl+Break sends

    def ended(self, seconds: float = 60) -> int:
        try:
            code = self.process.wait(seconds)
        except subprocess.TimeoutExpired:
            self.process.kill()
            said = self.output()[-1500:]
            expect(False, f"the service did not end within {seconds:.0f} s:\n{said}")
        self._out.close()
        return code

    def output(self) -> str:
        return OUTPUT.read_text(encoding="utf-8", errors="replace")

    def statuses(self) -> list[str]:
        return [
            line.removeprefix("service status: ")
            for line in self.output().splitlines()
            if line.startswith("service status: ")
        ]


# --- up and down ------------------------------------------------------------------------------


def up() -> None:
    expect(os.name == "nt", "this is the Windows test; on Linux run e2e/follower-systemd")
    down()
    DIST.mkdir(parents=True, exist_ok=True)
    for package in ("protocol", "engine", "follower"):
        run(*UV, "build", "--package", f"swarmscribe-{package}", "--wheel", "-o", str(DIST))
    # A Python of uv's own: the Microsoft Store's cannot run a service, and python.org's may
    # not be there. --no-bin and --no-registry: nothing outside work/ is touched.
    run(*UV, "python", "install", "3.12", "--no-bin", "--no-registry", env=uv_env())
    python = next((WORK / "python").glob("cpython-3.12.*-windows-*/python.exe"))
    run(
        *UV, "tool", "install", "--python", str(python), "--find-links", str(DIST),
        "--constraints", str(ROOT / "deploy" / "follower-constraints.txt"),
        "swarmscribe-follower[cuda]", env=uv_env(),
    )
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
    docker(
        "run", "-d", "--name", LEADER, "--network", NETWORK, "-p", f"127.0.0.1:{PORT}:8080",
        *leader_env, LEADER_IMAGE,
    )
    ready = (
        "import urllib.request as u; "
        "print(u.urlopen('http://localhost:8080/readyz', timeout=2).status)"
    )
    wait(
        "the leader to be ready",
        lambda: "200" in docker("exec", LEADER, "python", "-c", ready, check=False),
        60,
    )
    print(f"up: a leader at {LEADER_URL}; {run(str(FOLLOWER), '--version').strip()} in {WORK}")


def down() -> None:
    for name in (LEADER, POSTGRES):
        docker("rm", "-f", "-v", name, check=False)
    docker("network", "rm", NETWORK, check=False)
    for name in ("state", "follower.env", "join-token", "service-output.txt"):
        path = WORK / name
        if path.is_dir():
            run("cmd", "/c", "rmdir", "/s", "/q", str(path), check=False)
        elif path.exists():
            path.unlink()


# --- the scenario -----------------------------------------------------------------------------


def scenario(cpu: bool) -> None:
    device, model, compute = ("cpu", "tiny.en", "int8") if cpu else ("cuda", "large-v3", "float16")
    base = {"SWARMSCRIBE_FOLLOWER_DEVICE": device, "SWARMSCRIBE_FOLLOWER_STARTUP_MODEL": model}
    TOKEN_FILE.write_text(admin("pool-token", "outside", "default"), encoding="utf-8")
    admin("profile", device, model, compute)
    admin("location", "talks", "mono")
    write_env(**base)

    # 1. The install: the GPU libraries are found where the wheel put them; nothing was copied
    #    beside CTranslate2, and PATH does not name them.
    image = service_image()
    expect("WindowsApps" not in image[0], f"the service's Python is the Store's: {image[0]}")
    if not cpu:
        paths = run(str(FOLLOWER), "cuda-paths", env=follower_env()).strip()
        expect("cublas" in paths and "cublas" not in os.environ["PATH"].lower(), paths)
        beside = run(
            str(TOOL_PYTHON), "-c",
            "import ctranslate2, pathlib;"
            "print(len(list(pathlib.Path(ctranslate2.__file__).parent.glob('cublas*'))))",
        ).strip()
        expect(beside == "0", "a cuBLAS DLL sits beside ctranslate2.dll: the test proves nothing")
    doctor = run(
        str(FOLLOWER), "--env-file", str(ENV_FILE), "doctor", env=follower_env(), check=False
    )
    expect(
        f"model: {model} ({compute}) loaded and ran" in doctor and "result: ready" in doctor
        and "state folder: ok" in doctor,
        f"doctor is not ready:\n{doctor}",
    )
    gpu = next(line for line in doctor.splitlines() if line.startswith("device:"))

    # 2. The service's code, started by the service's own command, registers.
    started = time.monotonic()
    service = Service()
    wait("the follower to register", lambda: followers("active") or service.process.poll())
    expect(service.process.poll() is None, f"the service ended:\n{service.output()[-1500:]}")
    registered = time.monotonic() - started
    first = followers("active")[0]
    expect(first["device"] == device, f"it registered as {first['device']}")
    expect(
        service.statuses()[:2]
        == ["START_PENDING accepts=0x0 exit=0 wait_hint_ms=30000 checkpoint=1",
            "RUNNING accepts=0x101 exit=0 wait_hint_ms=0 checkpoint=0"],
        f"Windows would have been told: {service.statuses()}",
    )

    # 3. A recording is transcribed.
    admin("recording", "talks", "short.wav", "1")
    wait("short.wav to complete", lambda: job("short.wav")["state"] == "completed")

    # 4. The stop control mid-job (grace 1 s): STOP_PENDING with a wait hint past the grace,
    #    the recording handed back, SERVICE_STOPPED without an error, exit 0.
    admin("recording", "talks", "released.wav", str(LONG_REPEATS))
    wait("released.wav to be leased", lambda: job("released.wav")["state"] == "leased")
    time.sleep(3)
    began = time.monotonic()
    service.stop_control()
    code = service.ended()
    stopped = time.monotonic() - began
    back = job("released.wav")
    expect(code == 0, f"a stopped service exited {code}:\n{service.output()[-1500:]}")
    expect(
        back["state"] == "queued" and back["tried"][-1][1] == "released",
        f"the stop control did not release the recording: {back}",
    )
    expect(
        service.statuses()[2:]
        == ["STOP_PENDING accepts=0x0 exit=0 wait_hint_ms=31000 checkpoint=1",
            "STOPPED accepts=0x0 exit=0 wait_hint_ms=0 checkpoint=0"],
        f"Windows would have been told: {service.statuses()}",
    )

    # 5. Started again it is the same follower, and the recording is done with one attempt.
    service = Service()
    wait(
        "released.wav to complete",
        lambda: job("released.wav")["state"] == "completed" or service.process.poll(),
    )
    expect(job("released.wav")["attempts"] == 1, f"the release was counted: {job('released.wav')}")
    expect(
        [row["id"] for row in admin("state")["followers"]] == [first["id"]],
        "the follower registered again after a restart",
    )

    # 6. Revoked: exit 4, told to Windows as SERVICE_STOPPED with service error 4, which its
    #    recovery actions leave alone.
    admin("revoke-pool", "outside")
    code = service.ended()
    expect(code == 4, f"a revoked service exited {code}")
    expect(
        service.statuses()[-1] == "STOPPED accepts=0x0 exit=4 wait_hint_ms=0 checkpoint=0",
        f"Windows would have been told: {service.statuses()}",
    )

    # 7. A machine that cannot do the work: exit 3 and NO SERVICE_STOPPED, so that Windows'
    #    recovery actions restart it (twice, then they leave it).
    without = {
        **base,
        "SWARMSCRIBE_FOLLOWER_OFFLINE": "1",
        "SWARMSCRIBE_FOLLOWER_STARTUP_MODEL": "small.en",
        "SWARMSCRIBE_FOLLOWER_MODEL_DIR": str(WORK / "no-models"),
    }
    write_env(**without)
    service = Service()
    code = service.ended()
    expect(code == 3, f"a follower without its model exited {code}:\n{service.output()[-800:]}")
    expect(
        not any(status.startswith("STOPPED") for status in service.statuses()),
        f"exit 3 was reported as a clean stop: {service.statuses()}",
    )

    print(
        f"passed ({model} on {device}; {gpu.removeprefix('device: ')}): installed with uv tool"
        f" install on uv's Python; the service's code registered in {registered:.0f} s; the"
        f" stop control mid-job released the recording and ended the service in {stopped:.1f} s"
        " with SERVICE_STOPPED; it came back as the same follower; revoked it said"
        " SERVICE_STOPPED with error 4; unable to work it exited 3 without SERVICE_STOPPED"
    )


# --- for the owner's procedure ----------------------------------------------------------------


def owner_token(path: str) -> None:
    """A new pool token into `path`; a profile for each device, as the scenario sets them."""
    name = f"owner-{int(time.time())}"  # a pool token's name is never used twice
    Path(path).write_text(admin("pool-token", name, "default"), encoding="utf-8")
    admin("profile", "cuda", "large-v3", "float16")
    admin("profile", "cpu", "tiny.en", "int8")
    docker(
        "exec", "-i", LEADER, "python", "-", "location", "talks", "mono",
        stdin=ADMIN.read_text(encoding="utf-8"), check=False,  # it may exist already
    )
    print(f"wrote a pool token to {path} (its name at the leader: {name})")


def owner_state() -> None:
    state = admin("state")
    if not state["followers"] and not state["jobs"]:
        print("no follower has registered and no recording is queued")
    for row in state["followers"]:
        print(f"follower {row['id'][:8]} {row['state']} {row['device']}")
    for key, found in sorted(state["jobs"].items()):
        outcomes = [outcome for _, outcome in found["tried"]]
        print(f"{key} {found['state']} attempts={found['attempts']} tried={outcomes}")


def main() -> int:
    arguments = sys.argv[1:]
    if arguments[:1] == ["up"] and len(arguments) == 1:
        up()
    elif arguments[:1] == ["down"] and len(arguments) == 1:
        down()
    elif arguments in (["run"], ["run", "--cpu"]):
        scenario(cpu="--cpu" in arguments)
    elif arguments[:1] == ["token"] and len(arguments) == 2:
        owner_token(arguments[1])
    elif arguments[:1] == ["recording"] and len(arguments) == 3:
        admin("recording", "talks", arguments[1], str(int(arguments[2])))
        print(f"queued talks/{arguments[1]}")
    elif arguments == ["state"]:
        owner_state()
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Keep its work folder out of Git and out of image builds**

Add the line `e2e/follower-windows/work/` to `.gitignore` after `e2e/follower-systemd/work/`, and the line `e2e/follower-windows/work` to `.dockerignore` after `e2e/follower-systemd/work`.

Run: `uv run ruff check e2e/follower-windows` — expected `All checks passed!`.

- [ ] **Step 3: Up**

On the development machine (Git Bash, the Docker `PATH` line of Global Constraints). The leader's test image is the one F4a built under this plan's own tag (build it again if it is gone: `docker build -t swarmscribe-leader:f4-e2e -f e2e/compose/Dockerfile .`); `swarmscribe-leader:e2e` may be another agent's and is left alone.

```bash
export UV="python -m uv" LEADER_IMAGE=swarmscribe-leader:f4-e2e
time python -m uv run python e2e/follower-windows/run_e2e.py up
```

Expected last line: `up: a leader at http://127.0.0.1:18080; swarmscribe-follower 0.1.0 in C:\Users\walla\SwarmScribe-f4\e2e\follower-windows\work`. The first time it downloads uv's Python 3.12 and the 700 MB cuBLAS wheel (a few minutes); afterwards 11 to 15 s. Check afterwards that it left nothing outside the worktree: `ls /c/Users/walla/.local/bin` shows no new `python3.12.exe`.

- [ ] **Step 4: Run the scenario on the GPU**

```bash
time python -m uv run python e2e/follower-windows/run_e2e.py run
```

Expected, after about a minute and a quarter (`large-v3` is read from `C:\Users\walla\.cache\huggingface\hub`, where the 2026-10 benchmark left it; without it the first run downloads 3 GB), one line:

```
passed (large-v3 on cuda; cuda (NVIDIA GeForce RTX 4090, 24564 MiB)): installed with uv tool install on uv's Python; the service's code registered in 5 s; the stop control mid-job released the recording and ended the service in 0.5 s with SERVICE_STOPPED; it came back as the same follower; revoked it said SERVICE_STOPPED with error 4; unable to work it exited 3 without SERVICE_STOPPED
```

A line that starts `FAILED:` names the step; what the service printed last is in `e2e/follower-windows/work/service-output.txt`. The scenario cannot be run twice on one `up` (it revokes): `up` again, then `run`. Then the CPU variant, which is what a machine without a GPU does:

```bash
python -m uv run python e2e/follower-windows/run_e2e.py up
time python -m uv run python e2e/follower-windows/run_e2e.py run --cpu
```

Expected: a line that starts `passed (tiny.en on cpu; cpu): installed with uv tool install on uv's Python;`, after about 50 s.

- [ ] **Step 5: Down**

```bash
python -m uv run python e2e/follower-windows/run_e2e.py down
left="$(docker ps -a --format '{{.Names}}')"; echo "$left" | grep follower-windows || echo "nothing left"
```

Expected: `nothing left`. `e2e/follower-windows/work/` keeps uv's Python, the wheels and the installed follower (1.3 GB, ignored by Git) for the owner's procedure and the next run; delete the folder to start from nothing.

- [ ] **Step 6: Record it, with the owner's procedure**

In `docs/superpowers/plans/2026-10-05-follower-f4-outcomes.md`, insert the text below **before** the heading `## What this does not prove`, fill "The run of Task 3" from what Steps 3 to 5 printed on this run (the versions from the commands named there, the `real` times, both `passed` lines whole), and then replace the document's last section, "What this does not prove", with the one given after it. Do not copy the planner's figures into "The run of Task 3", and do not fill in "The owner's run": only the owner's own run does.

````markdown
## Windows, without administrator rights

How: `e2e/follower-windows/run_e2e.py` (`up`, `run`, `run --cpu`, `down`). It installs the
follower with its `cuda` extra from the three wheels, on a Python that uv fetches, all under
`e2e/follower-windows/work`, and runs **the service's own command** (`windows.image()`: the
real interpreter with `service_boot.py`) in a console with `--foreground`, where each status
the service would report to Windows is printed and Ctrl+Break is the stop control. The leader
is the leader's test image with Postgres in Docker, published on `127.0.0.1:18080`.

Three kinds of proof, kept apart:

1. **Tests, on every platform** (`packages/follower/tests/test_windows.py`): what the service
   tells Windows for every exit, what each control does, the install commands, that the
   supervisor keeps ticking through a stop.
2. **This machine, without the service control manager** (below): the install, the GPU, the
   service's command and its whole life in a console.
3. **The service control manager itself**: only "The owner's run", further down.

## The planner's Windows run, 2026-10-05

Machine: Windows 11 10.0.26200, NVIDIA GeForce RTX 4090 (24 GB), driver 617.14, uv 0.12.22
with its Python 3.12.15, Docker Desktop 29.8.1. Built in a scratch folder from `main` at
`33885bb` with the plan's files, in a shell without administrator rights.

| What | Result |
|---|---|
| `up` | 11 to 15 s once cached; `work/` is 1.3 GB |
| `run` (GPU, `large-v3`, `float16`) | passed four times, 1 min 14 s to 1 min 20 s |
| `run --cpu` (`tiny.en`) | passed twice, 48 s |
| The GPU library | `cuda-paths` names the wheel's `bin` folders; no DLL beside `ctranslate2.dll`; `PATH` does not name them |
| `doctor` | `device: cuda (NVIDIA GeForce RTX 4090, 24564 MiB)`; `model: large-v3 (float16) loaded and ran`; `state folder: ok` |
| The service's command, `--foreground` | registered in 5 s as `cuda`; `START_PENDING ... wait_hint_ms=30000 checkpoint=1`, then `RUNNING accepts=0x101` |
| The stop control mid-job, grace 1 s | `STOP_PENDING ... wait_hint_ms=31000 checkpoint=1`, `STOPPED ... exit=0`; ended with code 0 in 0.5 s; the recording `released` |
| Started again | the same follower; the recording `completed`, 1 attempt |
| Revoked | `STOPPED accepts=0x0 exit=4`; ended with code 4 |
| Unable to work (offline, model absent) | ended with code 3, no `STOPPED` line |

How CTranslate2 finds cuBLAS on Windows, measured with `tiny.en` as `float16`:

| How the wheel's folder was offered | uv's Python 3.12.15 | Microsoft Store Python 3.12.10 |
|---|---|---|
| nothing | not found | not found |
| `PATH` | loads | not found |
| `os.add_dll_directory` | not found | loads |
| the library loaded first by its full path (what the follower does) | loads | loads |

## The run of Task 3 (plan F4b)

Run on: this run (the date).

- Versions, from the commands: `cmd /c ver`: this run; `nvidia-smi
  --query-gpu=name,driver_version --format=csv,noheader`: this run; `python -m uv --version`:
  this run; `docker version --format '{{.Server.Version}}'`: this run.
- `up`: its last line, and `real`: this run.
- `run`: its last line, whole, and `real`: this run.
- `run --cpu`: its last line, whole, and `real`: this run.
- `down`, and that no `follower-windows-*` container was left: this run.

## The owner's run (needs an administrator)

Everything above was run without the service control manager. This procedure is the one
part of F4 that needs an administrator: it registers the service on the development machine,
runs it against the test leader on the GPU, stops it mid-job and removes it again. About
fifteen minutes. **If a step does not show what it should, stop, paste what it printed under
"Result" below, and go to step 7 (undo).**

All of it in one PowerShell window opened with "Run as administrator".

**1. The test leader and the wheels** (Task 3's `up` left both; this makes sure):

```powershell
cd C:\Users\walla\SwarmScribe-f4
$env:Path += ";C:\Users\walla\AppData\Local\Programs\DockerDesktop\resources\bin"
$env:UV = "python -m uv"
$env:LEADER_IMAGE = "swarmscribe-leader:f4-e2e"
python -m uv run python e2e/follower-windows/run_e2e.py up
```

Should end: `up: a leader at http://127.0.0.1:18080; swarmscribe-follower 0.1.0 in ...`.

**2. Install the follower for the machine, and register the service:**

```powershell
$root = "C:\Program Files\swarmscribe-follower"
$env:UV_PYTHON_INSTALL_DIR = "$root\python"
$env:UV_TOOL_DIR = "$root\tools"
$env:UV_TOOL_BIN_DIR = "$root\bin"
python -m uv python install 3.12 --no-bin --no-registry
$python = (Get-ChildItem "$root\python\cpython-3.12.*-windows-*\python.exe" | Select-Object -First 1).FullName
python -m uv tool install --python $python --find-links e2e\follower-windows\work\dist --constraints deploy\follower-constraints.txt "swarmscribe-follower[cuda]"
$follower = "$root\bin\swarmscribe-follower.exe"
& $follower service install --print
& $follower service install
sc.exe qc SwarmScribeFollower
sc.exe qfailure SwarmScribeFollower
```

Should show: `installed the service SwarmScribeFollower (not started)` and three numbered
lines; in `qc`, `START_TYPE : 2 AUTO_START (DELAYED)`, a `BINARY_PATH_NAME` of two quoted
paths (`...\python.exe` and `...\swarmscribe_follower\service_boot.py`, both under
`C:\Program Files\swarmscribe-follower`) and `SERVICE_START_NAME : NT SERVICE\SwarmScribeFollower`;
in `qfailure`, `RESET_PERIOD (in seconds) : 86400` and two `RESTART -- Delay = 60000
milliseconds` lines. **This is the first time the `sc.exe failure` line is run**: if
`service install` stops there, paste the error; the service exists by then and step 7
removes it.

**3. Its settings and its token** (the test leader is plain http; a one-second grace period,
so that step 5 shows a recording handed back):

```powershell
$data = "C:\ProgramData\swarmscribe-follower"
$lines = (Get-Content "$data\follower.env") -replace '^SWARMSCRIBE_LEADER_URL=.*', 'SWARMSCRIBE_LEADER_URL=http://127.0.0.1:18080' -replace '^SWARMSCRIBE_FOLLOWER_SHUTDOWN_GRACE_SECONDS=.*', 'SWARMSCRIBE_FOLLOWER_SHUTDOWN_GRACE_SECONDS=1'
$lines + "SWARMSCRIBE_FOLLOWER_ALLOW_HTTP=1", "SWARMSCRIBE_FOLLOWER_DEVICE=cuda" | Set-Content "$data\follower.env"
python -m uv run python e2e/follower-windows/run_e2e.py token "$data\join-token"
icacls $data
```

Should show: `wrote a pool token to C:\ProgramData\swarmscribe-follower\join-token (...)`,
and from `icacls` three entries: `BUILTIN\Administrators:(OI)(CI)(F)`,
`NT AUTHORITY\SYSTEM:(OI)(CI)(F)`, `NT SERVICE\SwarmScribeFollower:(OI)(CI)(RX)`.

**4. Start it:**

```powershell
sc.exe start SwarmScribeFollower
sc.exe query SwarmScribeFollower
Get-Content "$data\logs\follower.log" -Tail 6
python -m uv run python e2e/follower-windows/run_e2e.py state
```

The first start downloads `large-v3` (3 GB) into `models`, as the service's account: repeat
the last three lines until the log shows `registered` (a few minutes on a fast line).

Should show: `STATE : 4 RUNNING` from the start; in the log a line with `"message": "model
large-v3 (float16) loaded on cuda"` and one with `"event": "registered"`; from `state`, one
follower, `active`, `cuda`. This step proves the control manager's start, the service's
account and its folders, the token file, the state folder's check, and the GPU from a
service. If the state is `1 STOPPED`:
`sc.exe query` shows `SERVICE_EXIT_CODE`, and the log says why; if there is no log at all,
the service never reached its own code: run `& $follower service foreground` and paste what
it prints.

**5. Stop it in the middle of a recording, and start it again:**

```powershell
python -m uv run python e2e/follower-windows/run_e2e.py recording long.wav 96
Start-Sleep 6
sc.exe stop SwarmScribeFollower
Start-Sleep 8
sc.exe query SwarmScribeFollower
python -m uv run python e2e/follower-windows/run_e2e.py state
sc.exe start SwarmScribeFollower
Start-Sleep 90
python -m uv run python e2e/follower-windows/run_e2e.py state
```

Should show: from `sc.exe stop`, `STATE : 3 STOP_PENDING` with `WAIT_HINT : 0x7918` (31000
ms); eight seconds later `STATE : 1 STOPPED` with `WIN32_EXIT_CODE : 0`; from the first
`state`, `long.wav` `queued` with `released` as its last outcome; from the second, the same
single follower `active` and `long.wav` `completed` with `attempts` 1 (eight minutes of audio
take `large-v3` under a minute on this GPU). If the recording was not yet `leased`, or was
already `completed`, when the stop arrived, the timing missed: queue another (`recording
again.wav 96`) and repeat this step.

**6. (Optional) Windows restarts a follower that dies:**

```powershell
Stop-Process -Id (Get-CimInstance Win32_Service -Filter "Name='SwarmScribeFollower'").ProcessId -Force
Start-Sleep 75
sc.exe query SwarmScribeFollower
Get-WinEvent -FilterHashtable @{LogName='System'; Id=7031} -MaxEvents 1 | Format-List Message
```

Should show: `STATE : 4 RUNNING` again, and an entry that names the SwarmScribe Follower
service, "terminated unexpectedly" and "Restart the service".

**7. Undo everything:**

```powershell
& $follower service uninstall
Remove-Item -Recurse -Force "C:\ProgramData\swarmscribe-follower"
Remove-Item -Recurse -Force "C:\Program Files\swarmscribe-follower"
python -m uv run python e2e/follower-windows/run_e2e.py down
sc.exe query SwarmScribeFollower
```

Should end: `[SC] EnumQueryServicesStatus:OpenService FAILED 1060` (the service does not
exist). Nothing else was changed: uv's three variables lived in this window only.

**Result** (the owner's, or an agent's from the owner's paste; date, and what each step
showed):

- Not run yet.
````

Replace the section `## What this does not prove` and everything after it with:

````markdown
## What this does not prove

- **The Windows service under the service control manager**, until "The owner's run" above is
  filled in: the registration (`sc.exe create`, the recovery actions' syntax, the folders'
  permissions), the control manager starting the service's command and the `ctypes` binding
  answering it, the service's account reading its settings and token and writing its state,
  a GPU used from a service, the stop control arriving from Windows and its wait hint, and
  the recovery actions. A system shutdown, and a shutdown with Fast Startup, are not in the
  owner's procedure either.
- **A reboot, on either system.** The Linux machine is a container, and the Windows service
  has not been through a boot: "starts by itself after a reboot" rests on `WantedBy=
  multi-user.target` and on the service's start type, not on a run.
- **A real Linux host.** No real `network-online.target`, no NVIDIA driver under the unit: the
  GPU was run in a plain container, not under systemd.
- **The Windows CI job on GitHub's image**, until the branch is pushed: the same command
  passed on the development machine.
- Any distribution but Debian 12, any systemd but 252, cgroup v1; any Windows but 11; any GPU
  but the RTX 4090; a Python from python.org (uv's own was used; the Store's was measured for
  the GPU library only, and cannot run a service).
- A leader on TLS: the test leaders are plain http, with the development switch.
- A recording of people talking: the long recordings are one phrase repeated.
````

- [ ] **Step 7: Commit**

```bash
git add e2e/follower-windows/run_e2e.py .gitignore .dockerignore docs/superpowers/plans/2026-10-05-follower-f4-outcomes.md
git commit -m "test(follower): the native Windows install on the GPU, against a real leader, and its record; the owner's service procedure

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The README, the spec's amendments and the follow-ups

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-10-04-follower-design.md`
- Modify: `docs/superpowers/plans/2026-10-04-follower-f1-followups.md`

**Interfaces:**
- Consumes: everything F4a and F4b built, and the outcomes document as Task 3 left it. Where the README quotes a figure of a run, take it from the outcomes document's "The run of Task 7" and "The run of Task 3", not from the planner's tables.
- Produces: documentation only.

- [ ] **Step 1: The status table**

In `README.md`, section "Status", replace the row

```
| `swarmscribe-follower` — the agent, its two images and its Helm chart (`deploy/helm/swarmscribe-follower`, one release per pool) | Built; the service install for outside machines is next |
```

with

```
| `swarmscribe-follower` — the agent, its two images, its Helm chart (`deploy/helm/swarmscribe-follower`, one release per pool) and the native install for outside machines (a systemd unit, a Windows service) | Built; the Windows service is waiting for its first run under the service control manager (see "Run a follower on an outside machine") |
```

If the owner's run is already recorded in the outcomes document when this task runs, write instead `Built`.

- [ ] **Step 2: The commands and the settings table**

In the section "Run a follower (development)":

1. After the bullet that starts "- `leave` deregisters with the stored credential and deletes it.", add:

```markdown
- `cuda-paths` prints the folders of the GPU libraries that the `cuda` extra installed
  (`swarmscribe-follower[cuda]`), joined as `PATH` and `LD_LIBRARY_PATH` want them, or exits
  `3` and says the extra is missing. The follower does not need the variable: it loads cuBLAS
  from there itself before it loads a model on a GPU.
- `service install | uninstall | foreground` is the Windows service; see "Run a follower on
  an outside machine".

Every command takes `--env-file PATH` before its name: `NAME=value` lines that are read into
the environment first and win over it (`#` comments, nothing expanded, a mistake is reported
by its line number and never by its content). That is how the systemd unit and the Windows
service are configured, and how `doctor` and `leave` are run by hand with the same settings.
After `join --leader URL`, `run` and `doctor` use the leader the stored credential names when
`SWARMSCRIBE_LEADER_URL` is not set.
```

2. In the settings table, replace the last row

```
| `SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB` | the container's limit or the machine's memory, the smaller | the memory the follower may use, at least 64; a recording that cannot fit is failed `out_of_resources` before it is transcribed |
```

with

```
| `SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB` | the cgroup's limit (a container's, or a systemd unit's `MemoryMax=`) or the machine's memory, the smaller | the memory the follower may use, at least 64; a recording that cannot fit is failed `out_of_resources` before it is transcribed |
```

3. Under "**Known limits.**", replace the first bullet (three lines, from "- The credential file is protected by the profile folder it lives in." to "on POSIX it is `0600` in a `0700` folder.") with:

```markdown
- The credential file is `0600` in a `0700` folder on POSIX. On Windows the folder and the
  file may be reachable only by the follower's account, SYSTEM and Administrators: a state
  folder of the account's own is made so before the follower registers, and one that other
  accounts can reach once it holds a credential is refused (exit `2`) with the `icacls`
  command that fixes it. A follower that joined on Windows before this check existed may be
  refused once: run the printed command, or `leave` and join again. Deny entries are not
  read, and a custom scratch folder is made private but never refused.
```

- [ ] **Step 3: The memory paragraphs under "Follower images"**

In the section "Follower images", replace the two sentences

```
Give the container a memory limit (`--memory 8g`) and the guard follows it. On a
native Linux install run under systemd, a `MemoryMax=` on the unit is not seen (the guard
reads the container's cgroup file, else the machine's memory), so set
`SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB` there.
```

with

```
Give the container a memory limit (`--memory 8g`) and the guard follows it; under systemd a
`MemoryMax=` on the unit (or on its slice) does the same, because the guard reads the
smallest limit from the process's own cgroup upwards.
```

and the paragraph that starts "On Windows and macOS the platform does not say what the process already holds" (four lines, ending "to what the follower may really use.") with

```
On Windows the guard counts what the process holds (its working set), as on Linux; on macOS
the platform does not say, and the recording is counted alone. On both, when no limit is set
the limit is the machine's total physical memory, not what is free: set
`SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB` to what the follower may really use.
```

In the list of that section's limits, replace the bullet that starts "- Where the platform does not say what a process holds (Windows, macOS), the memory guard" (three lines) with:

```markdown
- On macOS the memory guard counts the recording alone and not the loaded model; on Windows
  and macOS its default limit is the machine's total memory.
```

- [ ] **Step 4: The guide**

Insert the section below immediately before the heading `## Develop`:

````markdown
## Run a follower on an outside machine

An outside machine is one that is not in the cluster: an office PC, a workstation with a GPU,
a volunteer's computer. It dials out to the leader over HTTPS and needs nothing opened towards
it. There are two ways to run a follower on one:

- **Docker**, where Docker is there already: "Follower images" above. Nothing more to install.
- **Natively**, which is what most Windows PCs want: the follower installed with `uv`, started
  by systemd or as a Windows service. This section.

Either way the machine receives recordings that are marked OK to publish, and only those; do
not give a join token to a machine you would not trust with them.

### What the machine installs

The packages are not on an index. Build three wheels from this repository, once, and hand
the machine the folder (with the constraints file, which pins every dependency to the
versions `uv.lock` has and CI tested):

```
uv build --package swarmscribe-protocol --wheel -o dist
uv build --package swarmscribe-engine --wheel -o dist
uv build --package swarmscribe-follower --wheel -o dist
cp deploy/follower-constraints.txt dist/
```

On the machine, with [uv](https://docs.astral.sh/uv/) installed and `dist` copied to it:

```
uv tool install --python 3.12 --find-links dist --constraints dist/follower-constraints.txt swarmscribe-follower
```

and on a machine with an NVIDIA GPU, `"swarmscribe-follower[cuda]"` in place of the last word.
That is all a GPU needs besides its driver (`nvidia-smi` must work): no CUDA toolkit, nothing
to copy, no `PATH` or `LD_LIBRARY_PATH`. The `cuda` extra brings cuBLAS as a wheel, and the
follower loads it from there itself; `swarmscribe-follower cuda-paths` shows where it is.
uv downloads a Python of its own if the machine has none that fits.

Where the machine has `git` and can reach the repository, this does the same without the
wheels (take the constraints file from the same commit to get the same versions):

```
uv tool install "swarmscribe-follower[cuda] @ git+https://github.com/iamfatness/SwarmScribe@<commit>#subdirectory=packages/follower"
```

For yourself, without a service, that is enough:

```
swarmscribe-follower join --leader https://leader.example.org --token-stdin
swarmscribe-follower doctor
swarmscribe-follower run
```

`doctor` loads the model and runs it once, on the GPU if there is one; on a GPU machine its
`device:` line must say `cuda`.

### Linux, with systemd

As root. The follower is installed for the machine under `/opt/swarmscribe-follower`, with
its command in `/usr/local/bin`:

```
export UV_TOOL_DIR=/opt/swarmscribe-follower/tools UV_TOOL_BIN_DIR=/usr/local/bin
export UV_PYTHON_INSTALL_DIR=/opt/swarmscribe-follower/python UV_COMPILE_BYTECODE=1
uv tool install --python 3.12 --find-links dist --constraints dist/follower-constraints.txt swarmscribe-follower

useradd --system --home-dir /var/lib/swarmscribe-follower --shell /usr/sbin/nologin swarmscribe-follower
install -d -o root -g swarmscribe-follower -m 0750 /etc/swarmscribe-follower
install -o root -g swarmscribe-follower -m 0640 deploy/systemd/follower.env.example /etc/swarmscribe-follower/follower.env
install -o root -g root -m 0644 deploy/systemd/swarmscribe-follower.service /etc/systemd/system/
```

Set `SWARMSCRIBE_LEADER_URL` in `/etc/swarmscribe-follower/follower.env`, and put the join
token (or a pool token), alone, into `/etc/swarmscribe-follower/join-token`:

```
( umask 027; cat > /etc/swarmscribe-follower/join-token )     # paste the token, then Ctrl+D
chgrp swarmscribe-follower /etc/swarmscribe-follower/join-token
systemctl daemon-reload
systemctl enable --now swarmscribe-follower
journalctl -u swarmscribe-follower -f
```

The token is in no unit file, no command line and no process's environment. It is read once,
when the follower first registers; afterwards the file may be emptied. The credential it was
exchanged for is in `/var/lib/swarmscribe-follower/state` (mode `0700`), with the scratch
folder; the models are in `/var/lib/swarmscribe-follower/models`.

To run a command by hand with the service's settings, run it as the service's user with the
same file:

```
runuser -u swarmscribe-follower -- swarmscribe-follower --env-file /etc/swarmscribe-follower/follower.env doctor
```

(`doctor`'s `memory:` line then describes the machine, not the unit: a command run by hand is
not in the unit's cgroup.)

### Windows, as a service

A service runs as an account of its own, which cannot read a user's profile and cannot use
the Microsoft Store's Python. So the follower is installed for the machine, with a Python
that uv fetches, under `C:\Program Files\swarmscribe-follower`. In PowerShell opened with
"Run as administrator", from the folder that holds `dist`:

```
$root = "C:\Program Files\swarmscribe-follower"
$env:UV_PYTHON_INSTALL_DIR = "$root\python"
$env:UV_TOOL_DIR = "$root\tools"
$env:UV_TOOL_BIN_DIR = "$root\bin"
uv python install 3.12 --no-bin --no-registry
$python = (Get-ChildItem "$root\python\cpython-3.12.*-windows-*\python.exe" | Select-Object -First 1).FullName
uv tool install --python $python --find-links dist --constraints dist\follower-constraints.txt "swarmscribe-follower[cuda]"
& "$root\bin\swarmscribe-follower.exe" service install --print     # what it will do
& "$root\bin\swarmscribe-follower.exe" service install
```

`service install` registers the service `SwarmScribeFollower` (not started), running as its
own account `NT SERVICE\SwarmScribeFollower` (no password to keep), and makes
`C:\ProgramData\swarmscribe-follower`:

| There | What | Who may |
|---|---|---|
| `follower.env` | the settings (`NAME=value`); written with the folders already filled in | administrators write, the service reads |
| `join-token` | the join or pool token, alone; you create it | administrators write, the service reads |
| `state\` | the credential and scratch | the service |
| `models\` | the model cache | the service |
| `logs\follower.log` | the service's log (a service has no console); kept as `follower.log.1` and begun again when it passes 10 MiB at a start | the service |

Set `SWARMSCRIBE_LEADER_URL` in `follower.env`, put the token into `join-token` (Notepad as
administrator will do; the token is in no registry value and no command line), and:

```
sc.exe start SwarmScribeFollower
sc.exe query SwarmScribeFollower
Get-Content C:\ProgramData\swarmscribe-follower\logs\follower.log -Tail 20
```

`swarmscribe-follower service foreground` runs the service's own code in the console instead,
printing each status it would report to Windows; Ctrl+C is the stop control. It is the way to
see why a service does not start (as a user who may read `follower.env`, or with
`--env-file` naming another file).

The service's registration names the interpreter and the follower's environment by their
paths. After an upgrade that replaces either (`uv tool upgrade`, `uv tool install` again, a
new Python), run `service uninstall` and `service install` again. `service uninstall` removes
the service and keeps `C:\ProgramData\swarmscribe-follower`, which holds the credential.

### Stopping, and what restarts it

| | systemd | Windows service |
|---|---|---|
| A stop (`systemctl stop`; `sc.exe stop`, the Services console) | `SIGTERM`. The recording in hand is finished if its estimated time left fits `SWARMSCRIBE_FOLLOWER_SHUTDOWN_GRACE_SECONDS` (900 in both settings files), else handed back with no attempt counted. At `TimeoutStopSec=930` systemd kills what is left | the stop control: the same rule. The service reports "stop pending" with a wait hint of the grace period plus 30 seconds; `sc.exe stop` returns at once and the Services console stops waiting after about two minutes, and the service goes on stopping |
| A reboot or shutdown | the same stop | the recording is handed back at once, whatever the grace period |
| Sleep or hibernation (Windows: also a shutdown with Fast Startup) | not a stop: the process is frozen. When the machine wakes the lease has run out, the leader has given the recording to another follower, and this one starts on the next. One counted attempt | the same |
| Exit `0`: stopped, or drained | left stopped | left stopped |
| Exit `4` (revoked, or the token was refused), `5` (protocol) | left `failed`; never restarted | left stopped, with the code as the service's error (`sc.exe query`, and entry 7024 in the System log); never restarted |
| Exit `2` (settings), `3` (this machine cannot do the work: `doctor` says why), `1` (a bug) | restarted after 30 s; the fifth start in ten minutes is the last, then `failed` | restarted after a minute, twice; then left stopped (the System log says "terminated unexpectedly": the reason is in `follower.log`). The count starts again after a day |
| Killed (out of memory, a crash) | restarted, as above | restarted, as above |

A drained follower stays stopped across restarts and reboots (it keeps its credential and is
told `drain` again): `leave` and join again to put the machine back to work. A revoked one
needs a new token: put it in `join-token`, delete `state/credential.json`, start the service.

### Memory, and the health listener

Under systemd, `MemoryMax=` on the unit is the memory guard's limit (`systemctl edit
swarmscribe-follower`, then `[Service]` and `MemoryMax=8G`): a recording that cannot fit is
refused with a reason instead of being killed half-way. On Windows the guard counts what the
process holds against the machine's total memory; on a PC that is used for other things, set
`SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB` in `follower.env`. "Sizing a pool" has the figures.

A native install opens no port. For `/healthz` and `/metrics` on the machine itself, set
`SWARMSCRIBE_FOLLOWER_HEALTH_ADDR=127.0.0.1:9108` in `follower.env`.

### What has been run, and what has not

The record is `docs/superpowers/plans/2026-10-05-follower-f4-outcomes.md`.

- **Linux.** `e2e/follower-systemd/run_e2e.py` installs the follower as above in a container
  that runs systemd 252 as PID 1 (Debian 12) and checks, against a real leader with Postgres:
  it registers as its own user with a private state folder, no token in its environment and
  no open port; a stop with 900 s of grace finishes the recording and one with 1 s hands it
  back with no attempt counted; started again it is the same follower; killed with `SIGKILL`
  it is restarted and the recording redone; `MemoryMax=` refuses an hour-long recording;
  drained it exits 0 and stays stopped; revoked it exits 4 and is not restarted; unable to
  work it is restarted five times and then left failed. With the `cuda` extra in a plain
  container with the GPU passed in, `doctor` loaded and ran a model on the GPU with no
  `LD_LIBRARY_PATH`. **Not run:** a real host (a reboot, a real network coming up), and a GPU
  under the unit.
- **Windows, without administrator rights.** `e2e/follower-windows/run_e2e.py` installs the
  follower with its `cuda` extra on uv's Python and runs the service's own command in a
  console on an RTX 4090 with `large-v3`: it registers, transcribes, hands a recording back
  when the stop control arrives mid-job, comes back as the same follower, reports revocation
  as a stop with error 4, and ends without reporting a stop when it cannot work. What the
  service tells Windows and what its controls do is also tested on every platform, and the
  follower's tests run on Windows in CI (job `follower-windows`).
- **Windows, under the service control manager: not run yet.** Registering the service, the
  control manager starting it, the service's account reading its settings and writing its
  state, a GPU used from a service, the stop control arriving from Windows, a shutdown and
  the recovery actions all need an administrator. The procedure is in the record, under "The
  owner's run (needs an administrator)"; until that section is filled in, the Windows service
  is built and tested as far as tests reach, and not proven as a service.
````

- [ ] **Step 5: Develop**

In the section "Develop", after the paragraph about `av` being pinned below 19, add:

````markdown
`deploy/follower-constraints.txt` is `uv.lock`'s versions for a native install of the
follower; a test fails when the two disagree. After a change of `uv.lock`, regenerate it:

```
uv export --frozen --no-dev --no-emit-workspace --package swarmscribe-follower --extra cuda --no-hashes -o deploy/follower-constraints.txt
```

The two native end-to-end tests are run by hand and recorded in
`docs/superpowers/plans/2026-10-05-follower-f4-outcomes.md`: `e2e/follower-systemd/run_e2e.py`
(a container with systemd as PID 1) and `e2e/follower-windows/run_e2e.py` (Windows, with the
GPU).
````

- [ ] **Step 6: The spec's amendments**

In `docs/superpowers/specs/2026-10-04-follower-design.md`, append at the end of section 15:

```markdown
**Amendments after F4** (built in plans F4a and F4b, 2026-10-05; the record is `plans/2026-10-05-follower-f4-outcomes.md`).
- **GPU libraries in a native install (D15, 8.3).** There is no `setup-cuda` and no `LD_LIBRARY_PATH`: the
  follower loads cuBLAS from the `nvidia-cublas-cu12` wheel into its own process, by full path, before it loads
  a model on a GPU (`cudalibs.py`). Measured on Windows: CTranslate2 opens `cublas64_12.dll` by its bare name
  with `LoadLibraryA`, which reads `PATH` on a python.org or uv Python but not on the Microsoft Store's, and
  never reads `os.add_dll_directory`; a library already in the process is found on both, and on Linux.
  `cuda-paths` prints the wheels' folders, for looking. The images keep `LD_LIBRARY_PATH`.
- **Install (8.3).** `uv tool install --python 3.12 --find-links <folder> --constraints
  <folder>/follower-constraints.txt "swarmscribe-follower[cuda]"`, from three wheels built from the repository
  and `deploy/follower-constraints.txt` (`uv.lock`'s versions). The bare `uv tool install swarmscribe-follower`
  needs the packages on an index. A Git URL with `#subdirectory=packages/follower` also works.
- **Settings and the token (4.1, 8.3).** `swarmscribe-follower --env-file PATH <command>` reads `NAME=value`
  lines into the environment first; the unit and the Windows service start the follower with it, in place of
  `EnvironmentFile=`. The settings file is readable by the service's account and holds no secret. The token is
  a file of its own beside it (`SWARMSCRIBE_JOIN_TOKEN_FILE`), readable by root or administrators and the
  service's account, and is in no unit, registration, command line or environment. After `join --leader URL`,
  `run` and `doctor` use the stored credential's leader when `SWARMSCRIBE_LEADER_URL` is not set.
- **The systemd unit (8.3, 6.5).** As listed, with `StartLimitBurst=5` in `StartLimitIntervalSec=600` and
  `RestartSec=30`: exits 1, 2 and 3 and a kill are restarted, the fifth start in ten minutes being the last;
  0 is final; 4 and 5 are never restarted. The user is a static system user. Settings come from `--env-file`.
- **The Windows service (8.3, 5.6).** It runs as its own virtual account, `NT SERVICE\SwarmScribeFollower`,
  with its settings, token, state, models and log under `%ProgramData%\swarmscribe-follower`; the follower is
  installed for the machine with uv's own Python. Its command is the real interpreter with `service_boot.py`
  (the environment's `python.exe` is a launcher). The stop control is one stop, reported "stop pending" with a
  wait hint of the grace period plus 30 seconds; a shutdown (the pre-shutdown control) hands the job back at
  once. Exits 0, 4 and 5, and any exit after a stop was asked for, are reported `SERVICE_STOPPED` and never
  restarted; exits 1, 2 and 3 end the process without that report, so that the recovery actions (restart
  after a minute, twice; reset after a day) run. It logs to a file. `service foreground` runs the same code in
  a console. **As of F4 it has not run under the service control manager** (that needs an administrator).
- **The memory guard (5.7).** The limit is `SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB`, else the smaller of the
  machine's memory and the smallest `memory.max` from the process's own cgroup up to the root (a container's
  limit, a unit's `MemoryMax=`, a slice's). On Windows the guard counts the process's working set, as it
  counts the resident set on Linux; macOS still counts the job alone.
- **The credential on Windows (5.3).** The state folder and the credential file may be reachable only by the
  follower's account, SYSTEM and Administrators (their access control lists are read; `winacl.py`). A folder
  of the account's own is made so before a credential exists; one that others can reach is refused with exit
  2 and the `icacls` command that fixes it.
- **The health listener (D18).** Off in a native install, as decided: neither the unit nor the service sets
  it. Under the Windows service the supervisor's tick comes from `run_supervised`, which the service runs.
- **Windows CI (10).** Job `follower-windows` runs the follower package's tests on `windows-latest`.
- **Native tests (10).** `e2e/follower-systemd/` (a container with systemd as PID 1) and
  `e2e/follower-windows/` (the service's command in a console, on a GPU) are run by hand and recorded.
- **F4's result (11).** "Joins with one command" holds once the follower is installed (`join --leader URL
  --token-stdin`, or the service's first start with its token file). "Survives a reboot" is not proven on
  either system.
```

Also in that file: in section 4's module map, replace the line

```
  main.py         CLI: run, join, leave, doctor (F1); setup-cuda, cuda-paths, service (F4)
```

with

```
  main.py         CLI: run, join, leave, doctor (F1); cuda-paths, service, --env-file (F4)
```

and the line

```
  windows.py      DLL placement, service control handler (F4)
```

with

```
  cudalibs.py     the GPU library, loaded from its wheel (F4)
  envfile.py      a settings file for --env-file (F4)
  winacl.py       Windows: the state folder's access control list (F4)
  windows.py      the Windows service: host, install commands, control handler (F4)
  service_boot.py what the Windows service control manager starts (F4)
```

- [ ] **Step 7: The follow-ups**

In `docs/superpowers/plans/2026-10-04-follower-f1-followups.md`, replace the section `## F4 (native install)` (its heading and its six bullets) with:

```markdown
## F4 (native install)

All done in F4 (`plans/2026-10-05-follower-f4a-native-install-and-systemd.md` and `plans/2026-10-05-follower-f4b-windows-service-and-guide.md`), except where a line says otherwise:

- `run` and `doctor` fall back to the stored leader after `join --leader` (M4). *Done (F4a Task 2).*
- `getpass` for the token (M7). *Done in the F1 final fix wave.*
- `join` exits non-zero on a leader mismatch (M1). *Done in the F1 final fix wave.*
- Exit 5 only for a protocol refusal (M9). *Done in the F1 final fix wave.*
- The Windows ACL on the credential file (earlier carry). *Done (F4a Task 4): the folder's and the file's access control lists are read; a folder of the account's own is made private.*
- The service stop calling `agent.stop()` (earlier carry). *Done differently (F4b Task 1): the stop control is counted on a `StopSignals` that `run_supervised` polls, which calls `stop()`; stop-pending carries a wait hint of the grace period plus 30 seconds; stopped is reported when the follower has ended. Tested without Windows; **not yet run under the service control manager**.*
- `Agent.tick()` under a Windows service (F2b). *Done: the service runs `run_supervised`, which ticks.*
- `MemoryMax=` on a systemd unit was not seen by the memory guard (F2b). *Done (F4a Task 3).*
```

and append at the end of the file:

```markdown

## Left open after F4 (2026-10-05)

- **The Windows service has not run under the service control manager.** The owner's procedure is in `plans/2026-10-05-follower-f4-outcomes.md`; until its result is recorded there, the registration, the control manager's handshake, the service's account and folders, the GPU from a service, the stop control and the recovery actions are unproven.
- A reboot was survived on neither system (the Linux proof is a container). The first real machine of each kind should be watched through one.
- Sleep, hibernation and a Windows shutdown with Fast Startup freeze a follower mid-job; the recording costs one counted attempt. The service could accept the power event and hand the recording back before the machine sleeps; systemd could do the same with a sleep hook.
- A system shutdown on Windows was not run even in a console (`test_a_shutdown_counts_two_stops_so_the_job_is_handed_back_at_once` covers the decision, not Windows' timing).
- The Windows CI job has never run on GitHub's image (not pushed). If the local Postgres of the contract tests does not start there, deselect `test_real_leader.py` in that job.
- The packages are on no index: an outside machine installs wheels built from the repository. Publishing them makes the spec's `uv tool install swarmscribe-follower` true.
- After an upgrade that moves the interpreter or the follower's environment, the Windows service must be registered again (`service uninstall`, `service install`); nothing detects a stale registration except the service failing to start.
- The Windows service's log is one file with one kept generation, rotated only at a start; a follower that runs for months without a restart is not rotated.
- On Windows the trust check reads allow entries only, and a job object's memory limit is not read by the memory guard.
- The systemd test and the Windows test are run by hand. The systemd one could be a CI job (a privileged container; about four minutes).
- A native Linux install with a GPU was run in a plain container, not under the unit; `ProtectSystem=strict` and the unit's other restrictions were not run with the NVIDIA device files.
```

In the section "Minors left open from the final review", the bullet "M8: the join token stays in the environment for the life of the process and is inherited by `nvidia-smi`." gains the sentence: " *Not in a native install (F4): there the token is a file, never a variable. It still holds for `docker run -e SWARMSCRIBE_JOIN_TOKEN=...`.*"

- [ ] **Step 8: Check the documents against the code**

Run each and compare with what the README's new section says (capture, then read):

```bash
out="$(python -m uv run swarmscribe-follower --help)"; echo "$out" | grep -E "env-file|cuda-paths|service"
grep -n "RestartPreventExitStatus\|TimeoutStopSec\|StartLimitBurst\|RestartSec" deploy/systemd/swarmscribe-follower.service
grep -n "RESTART_DELAY_MS =\|FAILURE_RESET_SECONDS =\|STOP_MARGIN_SECONDS =\|LOG_ROTATE_BYTES =" packages/follower/src/swarmscribe_follower/windows.py
```

Expected: the three names in the help; `RestartSec=30`, `RestartPreventExitStatus=4 5`, `TimeoutStopSec=930`, `StartLimitBurst=5`; `RESTART_DELAY_MS = 60_000`, `FAILURE_RESET_SECONDS = 86_400`, `STOP_MARGIN_SECONDS = 30.0`, `LOG_ROTATE_BYTES = 10 * 1024 * 1024`. Every figure in the README's section must be one of these or come from the outcomes document.

- [ ] **Step 9: The gate, and commit**

Run: `uv run ruff check .` — expected `All checks passed!`. Run: `uv run pytest` once, to the end — expected no failure (documents are read by a few tests of the repository: the README's commands and the chart's values).

```bash
git add README.md docs/superpowers/specs/2026-10-04-follower-design.md docs/superpowers/plans/2026-10-04-follower-f1-followups.md
git commit -m "docs(follower): run a follower on an outside machine: the install, systemd, the Windows service; spec amendments after F4

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## For the owner

Not tasks; what is yours to do and to decide.

- **One run as an administrator**, about fifteen minutes, written out step by step in the outcomes document's section "The owner's run (needs an administrator)" (Task 3 puts it there): install the follower for the machine, `service install`, start it against the test leader, stop it mid-job, optionally kill it and watch Windows restart it, and remove everything again. It proves what nothing else here can: the registration (and the exact `sc.exe failure` syntax), the control manager's handshake with the `ctypes` binding, the service's account and its folders, the GPU from a service, the stop control. **If a step fails, stop there and paste what it printed**: the fix is in `windows.py`, where each of those is a few lines, and the procedure's last step removes whatever was installed.
- **Rulings marked (owner):** 3 (the service's virtual account, `%ProgramData%`, a machine-wide install), 4 (which exits Windows restarts), 11 (the Windows CI job).
- **The Windows CI job has never run** (nothing is pushed). The likeliest trouble is the local Postgres that the contract tests start (`pgserver`) on GitHub's Windows image. If `follower-windows` fails for that reason on its first run, change its last line to `uv run pytest packages/follower --deselect packages/follower/tests/test_real_leader.py`: those tests run on Linux in the `test` job already.
- **Sleep and Fast Startup** freeze a follower mid-job without a stop; the recording costs one counted attempt when the machine wakes (ruling 5). Office PCs that sleep will show this as `expired` attempts. Handling the power event (hand the recording back before sleeping) is in the follow-ups.
- **Not pushed, and nothing published**: an outside machine installs from wheels you build (the README's first step) until the packages are on an index.

## Self-Review

**Spec coverage.** Spec 8.3 "Windows service" (`service install`, a dedicated account, the same restart rules, the stop control wired to the shutdown path): Task 1, rulings 3 to 5; proven as far as each of the three ways allows, and said so. Spec 8.3's `setup-cuda` and its "run again after an upgrade": replaced by F4a ruling 1; Task 3's scenario shows nothing is copied. Spec 5.6 ("the service stop control"): Task 1's `control`, with tests. Spec 9 and `Agent.tick`: ruling 7 and its test. Spec 6.5 (4 and 5 not restarted): ruling 4, tests, scenario steps 6 and 7. Spec 5.3's Windows paragraph ("the Windows service runs as a dedicated account"): ruling 3; the check itself is F4a's Task 4. Spec 10's Windows CI job: Task 2. Spec 10 "GPU on Windows is checked by `doctor` on a real machine": Task 3. Spec 11's F4 result, "a Windows or Linux PC joins with one command and survives a reboot": the install and `join`/`run` are proven on both; **a reboot is not** (the systemd proof is a container, and the Windows service has not run under the control manager): the outcomes document and the README say so. The install guide (spec 11): Task 4.

**The nine carry-overs, Windows side.** 1: ruling 7, `test_the_stop_control_mid_job_releases_the_recording_and_the_supervisor_keeps_ticking`. 2: F4a; Task 3 step 1 of the scenario. 3: F4a Task 3 (the working set). 4: F4a Task 4; ruling 3 for the service's folders. 5: ruling 7. 6: ruling 5. 7: ruling 4. 8: the settings file and the token file under `%ProgramData%`, written and protected by `service install` (Task 1), never in the registration (`test_the_install_commands_are_exactly_these`). 9: Task 3's `up` runs the install command; the owner's procedure runs it for the machine.

**Where this plan differs from the spec**, each written into Task 4's amendments: no `setup-cuda`; the service's virtual account; what Windows restarts; the settings file; the log file.

**Unproven, said where it applies:** everything under "Not run, because it needs an administrator" above, and the CI job on GitHub's image.

**Placeholders.** Two, deliberate: the lines of the outcomes document's "The run of Task 3" that only the person running it can fill in, and the whole of "The owner's run", which only the owner's run fills. Everything else is given whole.

**Names.** `ServiceHost(run, report, grace_seconds=)`, `stops`, `control`, `main`, `reported_stopped`, the state, control and error constants, `image`, `image_problem`, `root_problem`, `install_commands`, `uninstall_commands`, `env_template`, `data_root`, `env_file`, `log_file`, `open_log`, `grace_from_environment`, `run_foreground`, `printing_report`, `serve_under_scm`, `service_process` (Task 1) are what `main.py`, `service_boot.py`, `test_windows.py` and Task 3's driver use. The printed status line (`service status: <STATE> accepts=<hex> exit=<n> wait_hint_ms=<n> checkpoint=<n>`) is the same in `printing_report`, its test and the driver's `Service.statuses`. The driver's `admin` commands are the ones `e2e/follower-kind/admin.py` has. The paths in the owner's procedure (`C:\Program Files\swarmscribe-follower`, `C:\ProgramData\swarmscribe-follower\follower.env`, `join-token`, `logs\follower.log`) are `windows.data_root()`'s and the README's.
