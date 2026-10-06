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
