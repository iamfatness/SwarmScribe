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

Two runs, on the same Windows 11 machine (Docker 29.8.1, uv 0.12.22), the first run's commit
dated 2026-10-06 09:00 -0400 and the second's 2026-10-06 09:23 -0400, on the branch
`follower-f4`, against the leader image tag `swarmscribe-leader:f4-e2e` (built by
`docker build -t swarmscribe-leader:f4-e2e -f e2e/compose/Dockerfile .` in the worktree). In the machine:
systemd 252 (252.39-1~deb12u2), Debian GNU/Linux 12 (bookworm), uv 0.12.22
(x86_64-unknown-linux-gnu); `cmd /c ver`: `Microsoft Windows [Version 10.0.26200.9457]`.

### First run, at `735dc75`: passed once, with a harness that was then fixed

- Launched with the venv's python (`.venv/Scripts/python.exe e2e/follower-systemd/run_e2e.py`,
  `UV="python -m uv"`), because the driver did not work under `uv run` on Windows: `uv run`
  overwrote `UV` with the path of `uv.exe`, which the driver split on its backslashes.
- `up` `real 0m16.554s`; `run` `real 4m32.316s`; `down` `real 0m1.877s`. The last line of `run`:
  `passed (tiny.en on cpu, systemd 252): installed with uv tool install and registered in 22 s; a stop with 900 s of grace finished the recording (50 s) and one with 1 s released it (3.0 s, no attempt counted); a killed follower was restarted and its recording redone; MemoryMax= refused an hour; drain exited 0 and stayed stopped; revoke exited 4 and was not restarted; exit 3 was restarted 5 times, then left failed`
- **Its "nothing is listening" result is not evidence.** The Dockerfile at `735dc75` does not
  install iproute2, which provides `ss` (`git show 735dc75:e2e/follower-systemd/Dockerfile |
  grep -c iproute2` printed `0`), so that check of the harness could not have worked as a check. The harness was fixed on review (`369e26a`), and
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
  pass: it ran with `ss` present and found no listening socket of the follower. It is a check of TCP listeners (`ss -Hltnp`)
  matched on the unit's main process id; UDP is not covered.
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

Run on 2026-10-06, on the branch `follower-f4` at `dd56935` plus this task's files, in a shell
without administrator rights (no service was registered, started, stopped or removed), against
the leader image `swarmscribe-leader:f4-e2e`.

- Versions, from the commands: `cmd /c ver`: `Microsoft Windows [Version 10.0.26200.9457]`;
  `nvidia-smi --query-gpu=name,driver_version --format=csv,noheader`: `NVIDIA GeForce RTX 4090,
  617.14`; `python -m uv --version`: `uv 0.12.22 (70fe1196a 2026-10-01 x86_64-pc-windows-msvc)`;
  the follower's Python, uv's own, in `work/python`: 3.12.15 (the driver itself ran on the
  Microsoft Store's Python 3.12.10, and uses only the standard library);
  `docker version --format '{{.Server.Version}}'`: `29.8.1`. The GPU had 1704 MiB of 24564 MiB
  in use by other programs when the runs began.
- `up`: its last line was `up: a leader at http://127.0.0.1:18080; swarmscribe-follower 0.1.0 in
  C:\Users\walla\SwarmScribe-f4\e2e\follower-windows\work`; `real 0m16.198s` (the first, which
  built the wheels and installed the follower) and `real 0m12.025s` (the next, before the CPU
  run). `ls /c/Users/walla/.local/bin` afterwards showed `claude.exe` only: no `python3.12.exe`.
  `work/` was 1.3 GB.
- `run` (GPU), whole last line, `real 1m13.155s`:

  ```
  passed (large-v3 on cuda; cuda (NVIDIA GeForce RTX 4090, 24564 MiB)): installed with uv tool install on uv's Python; the service's code registered in 5 s; the stop control mid-job released the recording and ended the service in 0.4 s with SERVICE_STOPPED; it came back as the same follower; revoked it said SERVICE_STOPPED with error 4; unable to work it exited 3 without SERVICE_STOPPED
  ```

- `run --cpu`, whole last line, `real 0m45.079s`:

  ```
  passed (tiny.en on cpu; cpu): installed with uv tool install on uv's Python; the service's code registered in 2 s; the stop control mid-job released the recording and ended the service in 0.7 s with SERVICE_STOPPED; it came back as the same follower; revoked it said SERVICE_STOPPED with error 4; unable to work it exited 3 without SERVICE_STOPPED
  ```

- Before these two runs, one `run` failed at its own expectation (`FAILED: Windows would have
  been told: [...]`), and the harness was made true to the service's code as it is: after the
  stop control the service says `STOP_PENDING accepts=0x100` (pre-shutdown stays accepted
  while a stop is pending), where the plan's text said `accepts=0x0`. Also, `uv run` sets the
  environment variable `UV` itself, to the path of its own `uv.exe`; the driver now reads that
  as Windows text (the first `up` failed on it).
- `down`, then `docker ps -a --format '{{.Names}}'` filtered for `follower-windows`: `nothing
  left`; no network of that name was left either. `work/` was kept (1.3 GB, ignored by Git).

## The owner's run (needs an administrator)

Everything above was run without the service control manager. This procedure is the one
part of F4 that needs an administrator: it registers the service on the development machine,
runs it against the test leader on the GPU, stops it mid-job and removes it again. About
fifteen minutes, plus the 3 GB download of `large-v3` at step 4. **If a step does not show
what it should, stop, paste what it printed under "Result" below, and go to step 7 (undo).**

All of it in one PowerShell 5.1 window opened with "Run as administrator", in the order
given. Every "Should show" below is derived from the code and from `service install --print`;
none of it has been run under the control manager: it is **expected, not proven**, and where
this document says "unproven" it is the first time the thing is tried.

**0. Nothing from before is in the way:**

```powershell
cd C:\Users\walla\SwarmScribe-f4
sc.exe query SwarmScribeFollower
Test-Path "C:\ProgramData\swarmscribe-follower"
Test-Path "C:\Program Files\swarmscribe-follower"
```

Should show: `[SC] EnumQueryServicesStatus:OpenService FAILED 1060:` (no such service), then
`False` twice. If either folder exists, look at what is in it before going on:
`service install` refuses a data folder that an account other than an administrator made.

**1. The test leader and the wheels** (the agent's runs left the wheels in
`e2e\follower-windows\work\dist` and removed the leader; this makes both). The driver uses only
Python's standard library, so it is run with plain `python`; `$env:UV` is how it runs uv.
About 15 seconds when the wheels are cached.

```powershell
$env:Path += ";C:\Users\walla\AppData\Local\Programs\DockerDesktop\resources\bin"
$env:UV = "python -m uv"
$env:LEADER_IMAGE = "swarmscribe-leader:f4-e2e"
python e2e/follower-windows/run_e2e.py up
```

Should end: `up: a leader at http://127.0.0.1:18080; swarmscribe-follower 0.1.0 in ...`. (The
driver's `token`, `recording` and `state` commands, used below, were run from a non-elevated
PowerShell against such a leader; an elevated window has not been tried.)

**2. Install the follower for the machine, and register the service.** The Python the service
runs on is the one the next lines make: uv's own, under `C:\Program Files`. The development
machine's default Python (the Microsoft Store's) belongs to one user and `service install`
refuses it on purpose, as it refuses anything under a user's profile.

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
```

The `uv` lines take a few minutes the first time (the 700 MB cuBLAS wheel). `--print` changes
nothing; it must show **no line that starts `warning:`** (that warning means the Python is
refused: stop). It should show these lines (the Python's folder name follows the version uv
installs; 3.12.15 when this was written):

```
folders: C:\ProgramData\swarmscribe-follower with state, models, logs inside
settings file: C:\ProgramData\swarmscribe-follower\follower.env (written if it is not there)
icacls.exe C:\ProgramData\swarmscribe-follower /inheritance:r /grant:r *S-1-5-32-544:(OI)(CI)F *S-1-5-18:(OI)(CI)F
sc.exe create SwarmScribeFollower binPath= "\"C:\Program Files\swarmscribe-follower\python\cpython-3.12.15-windows-x86_64-none\python.exe\" \"C:\Program Files\swarmscribe-follower\tools\swarmscribe-follower\Lib\site-packages\swarmscribe_follower\service_boot.py\"" start= delayed-auto obj= "NT SERVICE\SwarmScribeFollower" DisplayName= "SwarmScribe Follower"
sc.exe description SwarmScribeFollower "Takes recordings from a SwarmScribe leader and transcribes them."
sc.exe failure SwarmScribeFollower reset= 86400 actions= restart/60000/restart/60000//60000
sc.exe failureflag SwarmScribeFollower 0
icacls.exe C:\ProgramData\swarmscribe-follower /grant:r "NT SERVICE\SwarmScribeFollower:(OI)(CI)RX"
icacls.exe C:\ProgramData\swarmscribe-follower\state /grant:r "NT SERVICE\SwarmScribeFollower:(OI)(CI)M"
icacls.exe C:\ProgramData\swarmscribe-follower\models /grant:r "NT SERVICE\SwarmScribeFollower:(OI)(CI)M"
icacls.exe C:\ProgramData\swarmscribe-follower\logs /grant:r "NT SERVICE\SwarmScribeFollower:(OI)(CI)M"
```

(The `\"` are how Python prints the quotes; the program runs each as an argument list.)
`service install` does them in this order: look at who owns anything already in the data
folder; create the folder and at once lock it to Administrators and SYSTEM (inheritance
removed); create `state`, `models`, `logs` and `follower.env`; `sc.exe create`, `description`,
`failure`, `failureflag`; then grant the service's account read and execute on the folder and
modify on the three folders; look at the owners again. Then, taking a few seconds:

```powershell
& $follower service install
sc.exe qc SwarmScribeFollower
sc.exe qfailure SwarmScribeFollower
```

Should show: `installed the service SwarmScribeFollower (not started)` and three numbered
lines (`1. set SWARMSCRIBE_LEADER_URL in ...\follower.env`, `2. put the join token, alone, in
...\join-token`, `3. start it: sc.exe start SwarmScribeFollower    its log:
...\logs\follower.log`); in `qc`, `START_TYPE : 2 AUTO_START (DELAYED)`, a `BINARY_PATH_NAME`
of two quoted paths (`...\python.exe` and `...\swarmscribe_follower\service_boot.py`, both under
`C:\Program Files\swarmscribe-follower`) and `SERVICE_START_NAME : NT SERVICE\SwarmScribeFollower`;
in `qfailure`, `RESET_PERIOD (in seconds) : 86400` and two `RESTART -- Delay = 60000
milliseconds` lines. **Unproven: this is the first time the `sc.exe failure` and the `icacls`
lines are run.** If `service install` stops with `error: ... failed: ...`, paste it; when it
adds that the service was registered, step 7 removes it (its `service uninstall` comes first).

**3. Its settings and its token** (the test leader is plain http, which needs the development
switch; a one-second grace period, so that step 5 shows a recording handed back; the model and
the device named, as the scenario names them):

```powershell
$data = "C:\ProgramData\swarmscribe-follower"
$lines = (Get-Content "$data\follower.env") -replace '^SWARMSCRIBE_LEADER_URL=.*', 'SWARMSCRIBE_LEADER_URL=http://127.0.0.1:18080' -replace '^SWARMSCRIBE_FOLLOWER_SHUTDOWN_GRACE_SECONDS=.*', 'SWARMSCRIBE_FOLLOWER_SHUTDOWN_GRACE_SECONDS=1'
$lines + "SWARMSCRIBE_FOLLOWER_ALLOW_HTTP=1", "SWARMSCRIBE_FOLLOWER_DEVICE=cuda", "SWARMSCRIBE_FOLLOWER_STARTUP_MODEL=large-v3" | Set-Content -Encoding ascii "$data\follower.env"
python e2e/follower-windows/run_e2e.py token "$data\join-token"
icacls $data
icacls "$data\state"
```

Should show: `wrote a pool token to C:\ProgramData\swarmscribe-follower\join-token (its name at the
leader: owner-...)` (the token itself is printed nowhere); `icacls $data` three entries,
`BUILTIN\Administrators:(OI)(CI)(F)`, `NT AUTHORITY\SYSTEM:(OI)(CI)(F)` and
`NT SERVICE\SwarmScribeFollower:(OI)(CI)(RX)`, **and no entry for Users or Authenticated
Users** (the folder is locked), then `Successfully processed 1 files with 0 failures` (or
Windows' wording of it); `icacls "$data\state"` the same three, the service's with `(M)`. The
`join-token` file takes the folder's list, so the service can read it.

**4. Start it.** The control manager is told `RUNNING` before the model is loaded, so a state
of `RUNNING` alone proves little: the log and `state` are the evidence. The first start
downloads `large-v3` (3 GB) into `models`, as the service's account; the loop waits up to 15
minutes for the follower to say it registered.

```powershell
sc.exe start SwarmScribeFollower
for ($i = 0; $i -lt 90; $i++) {
    Start-Sleep 10
    if (Select-String -Path "$data\logs\follower.log" -Pattern '"event": "registered"' -Quiet) { break }
}
sc.exe query SwarmScribeFollower
Get-Content "$data\logs\follower.log" -Tail 6
python e2e/follower-windows/run_e2e.py state
```

Should show: `STATE : 4 RUNNING`; in the log a line with `"message": "model large-v3 (float16)
loaded on cuda"` and one with `"event": "registered"`; from `state`, one follower, `active`,
`cuda` (`follower <8 hex> active cuda`). This step proves the control manager's start, the
service's account and its folders, the token file, the state folder's check, and the GPU from
a service. **Unproven, and the likeliest to surprise: the GPU from a service session.** If the
state is `1 STOPPED`: `sc.exe query` shows `WIN32_EXIT_CODE : 1066` and a `SERVICE_EXIT_CODE`
(2: settings, 3: this machine cannot do the work, 4: refused by the leader), and the log says
why; a start-up failure of the code itself is `error: unexpected <ExceptionClassName>` in the
log; if there is no log at all, the service never reached its own code: run `& $follower
service foreground` in the window and paste what it prints (that is the same code, in a
console, with your account's rights).

**5. Stop it in the middle of a recording, and start it again.** The recording is eight minutes
of speech; the first loop waits until it is being transcribed, and the stop comes three seconds
later.

```powershell
python e2e/follower-windows/run_e2e.py recording long.wav 96
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep 2
    if ((python e2e/follower-windows/run_e2e.py state) -match 'long.wav leased') { break }
}
Start-Sleep 3
sc.exe stop SwarmScribeFollower
Start-Sleep 8
sc.exe query SwarmScribeFollower
python e2e/follower-windows/run_e2e.py state
sc.exe start SwarmScribeFollower
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep 5
    if ((python e2e/follower-windows/run_e2e.py state) -match 'long.wav completed') { break }
}
python e2e/follower-windows/run_e2e.py state
```

Should show: from `sc.exe stop`, `STATE : 3 STOP_PENDING` with `WAIT_HINT : 0x7918` (31000
ms); eight seconds later `STATE : 1 STOPPED` with `WIN32_EXIT_CODE : 0`; from the first
`state`, `talks/long.wav queued attempts=1 tried=['released']`; from the last, the same single `follower ... active
cuda` and `talks/long.wav completed attempts=1` (eight minutes of audio take `large-v3` under
a minute on this GPU; the loop waits at most five minutes). If the recording was already
`completed` before the stop, or never `leased`, the timing missed: queue another (`recording
again.wav 96`) and repeat this step with that name.

**6. (Optional) Windows restarts a follower that dies** (about 75 seconds):

```powershell
Stop-Process -Id (Get-CimInstance Win32_Service -Filter "Name='SwarmScribeFollower'").ProcessId -Force
Start-Sleep 75
sc.exe query SwarmScribeFollower
Get-WinEvent -FilterHashtable @{LogName='System'; Id=7031} -MaxEvents 1 | Format-List Message
```

Should show: `STATE : 4 RUNNING` again, and an entry that names the SwarmScribe Follower
service, "terminated unexpectedly" and "Restart the service" (after 60000 milliseconds).

**7. Undo everything.** The test leader, and with it the follower's registration there, go
with `down`, so the follower need not be told to leave. `service uninstall` stops the service
first; the name may stay busy for up to the grace period (one second here), hence the pause.

```powershell
& $follower service uninstall
Start-Sleep 5
Remove-Item -Recurse -Force "C:\ProgramData\swarmscribe-follower"
Remove-Item -Recurse -Force "C:\Program Files\swarmscribe-follower"
python e2e/follower-windows/run_e2e.py down
sc.exe query SwarmScribeFollower
Test-Path "C:\ProgramData\swarmscribe-follower"
Test-Path "C:\Program Files\swarmscribe-follower"
```

Should end: `removed the service SwarmScribeFollower. ...` from `service uninstall`; then
`[SC] EnumQueryServicesStatus:OpenService FAILED 1060:` (the service does not exist) and
`False` twice. If `service uninstall` fails, `sc.exe delete SwarmScribeFollower` does the same
(`sc.exe stop SwarmScribeFollower` first if it is running). Nothing else was changed: uv's
three variables lived in this window only. To remove the agent's work folder too (1.3 GB:
uv's Python, the wheels and the follower installed without administrator rights), from any
window: `Remove-Item -Recurse -Force e2e\follower-windows\work`. The `swarmscribe-leader:f4-e2e`
image is kept.

**Result** (the owner's, or an agent's from the owner's paste; date, and what each step
showed):

- Not run yet.

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
