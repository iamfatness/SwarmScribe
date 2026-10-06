"""The Windows service without the service control manager. What the service tells Windows
and what its controls do is plain Python and is tested on every platform; the tests that call
Windows itself skip elsewhere. Nothing here needs administrator rights, and nothing here can
prove what only the control manager can show (see the F4 outcomes document)."""

import io
import os
import subprocess
import sys
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


def host(run, *, grace=8.0, log=None):
    reports = []
    made = ServiceHost(
        run, lambda *status: reports.append(status), grace_seconds=lambda: grace, log=log
    )
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


def test_an_exit_from_the_follower_is_logged_by_class_and_still_ends_the_process():
    """`SystemExit` (or any other BaseException) from `run` is not turned into an exit code
    here: it goes on, as it did, and the process ends. Only a line is added."""
    lines = []

    def run(stops):
        raise SystemExit(3)

    service, reports = host(run, log=lines.append)
    with pytest.raises(SystemExit) as ended:
        service.main()
    assert ended.value.code == 3
    assert lines == ["error: unexpected SystemExit"]
    assert not service.reported_stopped


def test_a_failure_to_start_leaves_one_line_in_the_log_and_never_its_message():
    """The service has no console: an exception from the follower's own start-up (a broken
    environment, a library that will not load) would otherwise leave nothing at all. Its class
    is logged; its message may hold a path or a secret and is not."""
    lines = []

    def run(stops):
        raise ImportError(r"DLL load failed C:\Users\someone\secret-token-123")

    service, reports = host(run, log=lines.append)
    assert service.main() == 1
    assert lines == ["error: unexpected ImportError"]
    assert not service.reported_stopped
    assert [status[0] for status in reports] == [START_PENDING, RUNNING]


# --- the controls -------------------------------------------------------------------------


def test_the_stop_control_counts_one_stop_and_asks_for_the_grace_period_and_30_seconds():
    service, reports = host(lambda stops: 0, grace=900.0)
    assert service.control(CONTROL_STOP) == NO_ERROR
    assert service.stops.count == 1
    # a system shutdown that follows an operator's stop must still be heard
    assert reports == [(STOP_PENDING, ACCEPT_PRESHUTDOWN, 0, 930_000, 1)]


@pytest.mark.parametrize("control", [CONTROL_PRESHUTDOWN, CONTROL_SHUTDOWN])
def test_a_shutdown_counts_two_stops_so_the_job_is_handed_back_at_once(control):
    service, reports = host(lambda stops: 0, grace=900.0)
    assert service.control(control) == NO_ERROR
    assert service.stops.count == 2  # run_supervised: stop(now=True)
    assert reports == [(STOP_PENDING, 0, 0, 30_000, 1)]


def test_a_shutdown_after_an_operators_stop_hands_the_job_back_at_once():
    service, reports = host(lambda stops: 0, grace=900.0)
    service.control(CONTROL_STOP)
    assert service.stops.count == 1
    assert service.control(CONTROL_PRESHUTDOWN) == NO_ERROR
    assert service.stops.count == 2  # the second stop: run_supervised releases the job now
    assert reports[-1] == (STOP_PENDING, 0, 0, 30_000, 2)


def test_once_stopped_has_been_reported_every_control_is_ignored_and_nothing_is_reported():
    service, reports = host(lambda stops: 0)
    assert service.main() == 0 and service.reported_stopped
    told = list(reports)
    for control in (CONTROL_STOP, CONTROL_SHUTDOWN, CONTROL_PRESHUTDOWN, CONTROL_INTERROGATE):
        assert service.control(control) == NO_ERROR
    assert service.control(0x7F) == ERROR_CALL_NOT_IMPLEMENTED
    assert service.stops.count == 0 and reports == told


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
            # Make the health check able to fail: the supervisor's last tick is put 100 s
            # back (past the 30 s /healthz allows), so a follower that is not ticked under the
            # service reads unhealthy no matter how short this test is.
            agent._ticked = time.monotonic() - 100
            assert agent.health()[0] is False
            answers.append(service.control(CONTROL_STOP))
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not (
                agent._stopping.is_set() and agent.health()[0]
            ):
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


def steps_of(plan):
    return [(step.kind, step.value) for step in plan]


def test_the_install_plan_is_in_this_order():
    """The data folder is made already protected (and locked by command when it was there
    before), before anything is put in it; the service is registered after that; the service
    account is granted its access last, by SID, because the account's name resolves only once
    the service exists."""
    from swarmscribe_follower import winacl

    plan = windows.install_plan([PYTHON, "-I", BOOT], ROOT)
    lock = ["icacls.exe", str(ROOT), "/inheritance:r", "/grant:r",
            "*S-1-5-32-544:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F"]
    account = r"NT SERVICE\SwarmScribeFollower"
    grantee = "*" + winacl.service_sid("SwarmScribeFollower")
    kinds = steps_of(plan)
    assert kinds[:3] == [("check", ROOT), ("root", ROOT), ("lock", lock)]
    assert kinds[3:7] == [
        ("mkdir", ROOT / "state"), ("mkdir", ROOT / "models"), ("mkdir", ROOT / "logs"),
        ("settings", ROOT / "follower.env"),
    ]
    create = [
        value for kind, value in kinds if kind == "run" and value[:2] == ["sc.exe", "create"]
    ]
    assert create == [[
        "sc.exe", "create", "SwarmScribeFollower", "binPath=",
        f'"{PYTHON}" "-I" "{BOOT}"',
        "start=", "delayed-auto", "obj=", account, "DisplayName=", "SwarmScribe Follower",
    ]]
    assert kinds[7] == ("run", create[0])
    assert kinds[9] == ("run", [
        "sc.exe", "failure", "SwarmScribeFollower", "reset=", "86400", "actions=",
        "restart/60000/restart/60000//60000",
    ])
    assert kinds[10] == ("run", ["sc.exe", "failureflag", "SwarmScribeFollower", "0"])
    assert kinds[11:] == [
        ("run", ["icacls.exe", str(ROOT), "/grant:r", f"{grantee}:(OI)(CI)RX"]),
        *(
            ("run", ["icacls.exe", str(ROOT / name), "/grant:r", f"{grantee}:(OI)(CI)M"])
            for name in ("state", "models", "logs")
        ),
        ("check", ROOT),
    ]
    text = " ".join(part for kind, value in kinds if kind == "run" for part in value).lower()
    assert "token" not in text and "password" not in text


def installing(tmp_path, monkeypatch, run, *, existed=False):
    """`install` with the control manager, the elevation test and the owner check replaced.
    `events` records, in order, what was done and what existed at that moment."""
    from swarmscribe_follower import winacl

    root = tmp_path / "swarmscribe-follower"
    if existed:
        root.mkdir()
    events = []

    def create(path):
        events.append(("create", sorted(p.name for p in path.parent.iterdir())))
        if path.exists():
            return False
        path.mkdir()
        return True

    def recording(argv, **kwargs):
        events.append((tuple(argv[:2]), sorted(p.name for p in root.rglob("*"))))
        return run(argv, **kwargs)

    monkeypatch.setattr(windows, "data_root", lambda: root)
    monkeypatch.setattr(windows, "is_administrator", lambda: True)
    monkeypatch.setattr(windows, "image_problem", lambda command: None)
    monkeypatch.setattr(windows, "root_problem", lambda path: None)
    monkeypatch.setattr(winacl, "create_protected_directory", create)
    monkeypatch.setattr(windows.subprocess, "run", recording)
    return root, events


def ok(argv, **kwargs):
    return subprocess.CompletedProcess(argv, 0, "", "")


def test_a_new_data_folder_is_created_protected_and_nothing_is_made_before_that(
    tmp_path, monkeypatch
):
    root, events = installing(tmp_path, monkeypatch, ok)
    out, err = io.StringIO(), io.StringIO()
    assert windows.install(out, err, print_only=False) == 0, err.getvalue()
    assert events[0] == ("create", [])  # the root was made by the protected call, nothing run
    sc = [event for event in events if event[0][0] == "sc.exe"]
    assert len(sc) == 4 and sc[0][1] == ["follower.env", "logs", "models", "state"]
    assert not any(event[0][:2] == ("icacls.exe", str(root)) and "/inheritance:r" in event
                   for event in events)
    assert [event[0][0] for event in events if event[0][0] == "icacls.exe"] == ["icacls.exe"] * 4


def test_a_data_folder_that_was_there_is_locked_before_anything_is_made_in_it(
    tmp_path, monkeypatch
):
    root, events = installing(tmp_path, monkeypatch, ok, existed=True)
    out, err = io.StringIO(), io.StringIO()
    assert windows.install(out, err, print_only=False) == 0, err.getvalue()
    assert events[0][0] == "create" and events[1] == (("icacls.exe", str(root)), [])
    assert events[2][0] == ("sc.exe", "create")


def test_a_data_folder_that_appeared_after_the_first_look_is_looked_at_again_before_the_lock(
    tmp_path, monkeypatch
):
    """Absent at the owner check, there when the protected creation was tried: someone made
    it in between. It was never examined, so it is examined now, before the lock is run and
    before anything is made in it."""
    from swarmscribe_follower import winacl

    root, events = installing(tmp_path, monkeypatch, ok)
    looked = []

    def appeared(path):
        events.append(("create", []))
        path.mkdir()  # not by this install
        return False

    def problem(path):
        looked.append(path.exists())
        return "it exists and was not made by an administrator" if path.exists() else None

    monkeypatch.setattr(winacl, "create_protected_directory", appeared)
    monkeypatch.setattr(windows, "root_problem", problem)
    out, err = io.StringIO(), io.StringIO()
    assert windows.install(out, err, print_only=False) == 2
    assert looked == [False, True]
    assert "was not made by an administrator" in err.getvalue()
    assert events == [("create", [])]  # no command was run: not the lock, not sc.exe
    assert list(root.iterdir()) == []  # and nothing was made in it


def test_a_data_folder_that_was_there_at_the_first_look_is_looked_at_once_before_the_lock(
    tmp_path, monkeypatch
):
    root, events = installing(tmp_path, monkeypatch, ok, existed=True)
    looked = []
    monkeypatch.setattr(windows, "root_problem", lambda path: looked.append(1))
    out, err = io.StringIO(), io.StringIO()
    assert windows.install(out, err, print_only=False) == 0, err.getvalue()
    assert looked == [1, 1]  # the first step and the last, as the plan has them


@pytest.mark.parametrize("name", ["state", "models", "logs"])
def test_a_plain_file_where_a_folder_belongs_is_refused_not_a_traceback(
    tmp_path, monkeypatch, name
):
    root, events = installing(tmp_path, monkeypatch, ok, existed=True)
    (root / name).write_text("not a folder", encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()
    assert windows.install(out, err, print_only=False) == 2
    said = err.getvalue()
    assert f"{root / name} is a file where a folder belongs" in said
    assert f"remove or rename the folder {root}" in said
    assert not any(event[0][0] == "sc.exe" for event in events)  # nothing was registered


def test_a_failure_after_the_service_was_registered_says_to_uninstall_before_trying_again(
    tmp_path, monkeypatch
):
    def run(argv, **kwargs):
        failed = argv[0] == "sc.exe" and argv[1] == "failure"
        return subprocess.CompletedProcess(argv, 1 if failed else 0, "", "boom")

    installing(tmp_path, monkeypatch, run)
    out, err = io.StringIO(), io.StringIO()
    assert windows.install(out, err, print_only=False) == 1
    assert "run `swarmscribe-follower service uninstall` before trying again" in err.getvalue()


def test_a_final_owner_check_that_refuses_says_so_too(tmp_path, monkeypatch):
    installing(tmp_path, monkeypatch, ok)
    answers = iter([None, r"C:\x\join-token exists and was not made by an administrator"])
    monkeypatch.setattr(windows, "root_problem", lambda path: next(answers))
    out, err = io.StringIO(), io.StringIO()
    assert windows.install(out, err, print_only=False) == 2
    assert "run `swarmscribe-follower service uninstall` before trying again" in err.getvalue()


def test_a_failure_before_the_service_exists_does_not_say_to_uninstall(tmp_path, monkeypatch):
    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, "", "boom")

    installing(tmp_path, monkeypatch, run, existed=True)
    out, err = io.StringIO(), io.StringIO()
    assert windows.install(out, err, print_only=False) == 1
    assert "uninstall" not in err.getvalue()


def test_uninstall_says_the_name_may_be_busy_and_how_to_leave_first(tmp_path, monkeypatch):
    monkeypatch.setattr(windows, "data_root", lambda: tmp_path)
    monkeypatch.setattr(windows, "is_administrator", lambda: True)
    monkeypatch.setattr(
        windows.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "", "")
    )
    out, err = io.StringIO(), io.StringIO()
    assert windows.uninstall(out, err, print_only=False) == 0
    text = out.getvalue()
    assert "may take up to the grace period" in text
    assert "swarmscribe-follower --env-file" in text and "leave" in text
    assert "elevated" in text and str(tmp_path / "follower.env") in text


def test_no_secret_is_in_the_settings_template_and_the_token_is_a_file_of_its_own():
    template = windows.env_template(ROOT)
    assert f"SWARMSCRIBE_JOIN_TOKEN_FILE={ROOT / 'join-token'}" in template
    assert "SWARMSCRIBE_JOIN_TOKEN=" not in template
    # the follower reads the token again when it must register again (README: keep the file)
    assert "keep that file" in template and "read once" not in template
    assert f"SWARMSCRIBE_FOLLOWER_STATE_DIR={ROOT / 'state'}" in template
    assert "SWARMSCRIBE_FOLLOWER_SHUTDOWN_GRACE_SECONDS=900" in template


def test_a_store_python_cannot_be_a_service():
    store = r"C:\Users\someone\AppData\Local\Microsoft\WindowsApps\Python.3.12\python.exe"
    assert "Microsoft Store" in windows.image_problem([store, "-I", BOOT])


def test_the_services_command_starts_the_interpreter_isolated():
    """`-I`: no PYTHONPATH, no PYTHONHOME, no user site-packages, and not the script's own
    folder: nothing outside the install decides what the service imports."""
    command = windows.image(PYTHON)
    assert command == [PYTHON, "-I", str(windows.boot_script())]
    assert windows.image_line(command) == f'"{PYTHON}" "-I" "{windows.boot_script()}"'


USERS, AUTHENTICATED, EVERYONE = "S-1-5-32-545", "S-1-5-11", "S-1-1-0"
ADMINISTRATORS, SYSTEM = "S-1-5-32-544", "S-1-5-18"
TRUSTED_INSTALLER = "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
SOMEONE = "S-1-5-21-1-2-3-1001"
FULL, MODIFY, READ_EXECUTE, READ = 0x1F01FF, 0x1301BF, 0x1200A9, 0x120089
PRIVATE = [(ADMINISTRATORS, FULL), (SYSTEM, FULL)]


def code(owner, grants):
    return windows.code_problem(Path(BOOT), owner, grants)


def test_code_the_service_account_can_read_and_only_administrators_can_change_passes():
    """What a file under Program Files looks like, and the other ways the service's account
    gets in."""
    from swarmscribe_follower import winacl

    service = winacl.service_sid(windows.SERVICE_NAME)
    for reader in (USERS, AUTHENTICATED, EVERYONE, service):
        assert code(ADMINISTRATORS, [*PRIVATE, (reader, READ_EXECUTE)]) is None, reader
    installer = [(TRUSTED_INSTALLER, FULL), *PRIVATE, (USERS, READ_EXECUTE)]
    assert code(TRUSTED_INSTALLER, installer) is None
    assert code(SYSTEM, [*PRIVATE, (USERS, 0x80000000 | 0x20000000)]) is None  # generic R and X
    # read here, execute there: the account holds both, and the rights add up
    assert code(ADMINISTRATORS, [*PRIVATE, (USERS, READ), (AUTHENTICATED, 0x1200A0)]) is None
    # an app container's read access is no ordinary user's write access
    packages = [*PRIVATE, (USERS, READ_EXECUTE), ("S-1-15-2-1", READ_EXECUTE)]
    assert code(ADMINISTRATORS, packages) is None


def test_code_the_service_account_cannot_read_is_refused_naming_the_link_mode():
    """A file `uv tool install` hard-linked from its cache keeps the installing user's list:
    that user, SYSTEM and Administrators, and no entry the service's account matches."""
    for grants in (
        [(SOMEONE, FULL), *PRIVATE],
        PRIVATE,
        [*PRIVATE, (USERS, READ)],  # read without execute
        [*PRIVATE, ("S-1-5-80-1-2-3-4-5", READ_EXECUTE)],  # another service's account
    ):
        problem = code(ADMINISTRATORS, grants)
        assert problem is not None, grants
        assert BOOT in problem and "UV_LINK_MODE=copy" in problem
        assert "could not read" in problem


def test_code_an_ordinary_account_can_change_is_refused_by_name():
    from swarmscribe_follower import winacl

    service = winacl.service_sid(windows.SERVICE_NAME)
    readable = [*PRIVATE, (USERS, READ_EXECUTE)]
    for writer, mask in (
        (USERS, MODIFY), (SOMEONE, FULL), (EVERYONE, READ_EXECUTE | 0x2),  # write data
        (USERS, READ_EXECUTE | 0x4), (USERS, READ_EXECUTE | 0x10000),  # append; delete
        (USERS, READ_EXECUTE | 0x40000), (USERS, READ_EXECUTE | 0x80000),  # the list; the owner
        (USERS, 0x40000000), (AUTHENTICATED, 0x10000000),  # generic write; generic all
        (service, MODIFY),  # the service must not be able to rewrite its own code either
        ("?9", 0xFFFFFFFF),  # an entry of a kind that is not read
    ):
        problem = code(ADMINISTRATORS, [*readable, (writer, mask)])
        assert problem is not None, (writer, hex(mask))
        assert BOOT in problem and "can be changed by" in problem, problem
        assert "UV_LINK_MODE=copy" in problem
    assert "Users (S-1-5-32-545)" in code(ADMINISTRATORS, [*PRIVATE, (USERS, MODIFY)])
    # the owner may always rewrite the list, whatever the list says
    problem = code(SOMEONE, readable)
    assert "is owned by" in problem and SOMEONE in problem and "UV_LINK_MODE=copy" in problem
    # no list at all: everyone may do anything
    assert "has no access control list" in code(ADMINISTRATORS, None)


def test_a_file_of_the_image_that_cannot_be_examined_is_a_problem_not_a_crash():
    def missing(path):
        raise FileNotFoundError(2, "The system cannot find the file specified")

    problem = windows.image_problem([PYTHON, "-I", BOOT], read_grants=missing)
    assert PYTHON in problem and "cannot be examined" in problem


def test_the_interpreter_the_boot_script_and_the_package_are_all_examined():
    asked = []

    def read(path):
        asked.append(str(path))
        return ADMINISTRATORS, [*PRIVATE, (USERS, READ_EXECUTE)]

    assert windows.image_problem([PYTHON, "-I", BOOT], read_grants=read) is None
    assert asked == [PYTHON, BOOT, str(Path(BOOT).with_name("__init__.py"))]


def test_install_print_warns_of_a_problem_with_the_image_and_still_prints_the_plan(monkeypatch):
    monkeypatch.setattr(windows, "image", lambda: [PYTHON, "-I", BOOT])
    monkeypatch.setattr(windows, "data_root", lambda: ROOT)
    monkeypatch.setattr(windows, "image_problem", lambda command: "the code cannot be read")
    out, err = io.StringIO(), io.StringIO()
    assert windows.install(out, err, print_only=True) == 0
    assert err.getvalue() == "warning: the code cannot be read\n"
    lines = out.getvalue().splitlines()
    assert any(line.startswith("sc.exe create ") for line in lines)
    lock = next(n for n, line in enumerate(lines) if "/inheritance:r" in line)
    # the line before the lock says when the lock runs
    assert lines[lock - 1] == (
        f"data folder: a new {ROOT} is created already restricted to Administrators and"
        " SYSTEM; the next line runs only for a folder that is already there"
    )
    out, err = io.StringIO(), io.StringIO()
    assert windows.install(out, err, print_only=False) == 2
    assert err.getvalue() == "error: the code cannot be read\n" and out.getvalue() == ""


def test_commands_whose_output_windows_writes_in_its_own_language_are_decoded_leniently(
    tmp_path, monkeypatch
):
    """`sc.exe` and `icacls.exe` answer in the console's code page; text that does not decode
    must not be what fails the install."""
    seen = []

    def run(argv, **kwargs):
        seen.append(kwargs)
        return ok(argv)

    installing(tmp_path, monkeypatch, run)
    out, err = io.StringIO(), io.StringIO()
    assert windows.install(out, err, print_only=False) == 0, err.getvalue()
    assert windows.uninstall(out, err, print_only=False) == 0
    assert len(seen) == 10 and all(kwargs.get("errors") == "replace" for kwargs in seen)


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
    create = [line for line in lines if line.startswith("sc.exe create ")]
    assert len(create) == 1 and "service_boot.py" in create[0]
    assert sum(line.startswith("icacls.exe ") for line in lines) == 5
    assert lines.index(create[0]) > max(
        n for n, line in enumerate(lines) if "/inheritance:r" in line
    )  # the lock comes first
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


def make_tree(root):
    root.mkdir()
    for name in ("state", "models", "logs"):
        (root / name).mkdir()
    (root / "state" / "scratch").mkdir()
    (root / "state" / "scratch" / "nested.txt").write_text("x", encoding="utf-8")
    for name in ("follower.env", "join-token"):
        (root / name).write_text("x", encoding="utf-8")


def owned_by(monkeypatch, owner_of):
    """Everything is owned by Administrators except what `owner_of` names: ownership cannot
    be given away without privilege, so it is crafted (and the test holds whether the account
    running it is elevated, when its files belong to Administrators, or not)."""
    from swarmscribe_follower import winacl

    def read(path, descriptor_of=None):
        return (owner_of(Path(path)) or winacl.ADMINISTRATORS), []

    monkeypatch.setattr(winacl, "read_acl", read)


@windows_only
def test_a_clean_tree_passes_and_an_absent_one_too(tmp_path, monkeypatch):
    owned_by(monkeypatch, lambda path: None)
    assert windows.root_problem(tmp_path / "absent") is None
    make_tree(tmp_path / "root")
    assert windows.root_problem(tmp_path / "root") is None


@windows_only
def test_anything_at_any_depth_owned_by_someone_else_is_refused_by_name(tmp_path, monkeypatch):
    """Anyone may create a folder under %ProgramData% before the install does. Only what
    Administrators or SYSTEM (or the service's own account, after an uninstall) own is used:
    the owner may change its permissions back at any time, and could read a token put into
    a file it made. Not even the installing user's own SID is accepted: an elevated
    administrator's files belong to Administrators."""
    from swarmscribe_follower import winacl

    root = tmp_path / "root"
    make_tree(root)
    deep = root / "state" / "scratch" / "nested.txt"
    for foreign in (root, root / "models", root / "join-token", deep):
        owned_by(monkeypatch, lambda path, foreign=foreign: "S-1-5-32-545" if path == foreign
                 else None)
        problem = windows.root_problem(root) or ""
        assert f"{foreign} exists and was not made by an administrator" in problem
        assert "remove or rename" in problem
    for accepted in (winacl.SYSTEM, winacl.service_sid(windows.SERVICE_NAME)):
        owned_by(monkeypatch, lambda path, accepted=accepted: accepted if path == deep else None)
        assert windows.root_problem(root) is None
    owned_by(monkeypatch, lambda path: winacl.current_user() if path == deep else None)
    if winacl.current_user() != winacl.ADMINISTRATORS:
        assert windows.root_problem(root) is not None


def junction(link, target):
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True
    )


@windows_only
def test_a_junction_anywhere_in_the_tree_is_refused_and_no_grant_follows(tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    root = tmp_path / "root"
    make_tree(root)
    (root / "models").rmdir()
    junction(root / "models", elsewhere)
    owned_by(monkeypatch, lambda path: None)
    problem = windows.root_problem(root)
    assert str(root / "models") in problem and "junction or a symbolic link" in problem
    assert "remove or rename" in problem
    # and install stops there: nothing is run, so no grant is ever issued on the target
    ran = []
    monkeypatch.setattr(windows, "data_root", lambda: root)
    monkeypatch.setattr(windows, "is_administrator", lambda: True)
    monkeypatch.setattr(windows, "image_problem", lambda command: None)
    monkeypatch.setattr(windows.subprocess, "run", lambda argv, **kw: ran.append(argv))
    out, err = io.StringIO(), io.StringIO()
    assert windows.install(out, err, print_only=False) == 2
    assert ran == [] and "junction or a symbolic link" in err.getvalue()


@windows_only
def test_a_grant_is_never_issued_on_a_reparse_point_even_if_the_check_missed_it(
    tmp_path, monkeypatch
):
    from swarmscribe_follower import winacl

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    junction(root / "models", elsewhere)
    ran = []
    monkeypatch.setattr(windows, "data_root", lambda: root)
    monkeypatch.setattr(windows, "is_administrator", lambda: True)
    monkeypatch.setattr(windows, "image_problem", lambda command: None)
    monkeypatch.setattr(windows, "root_problem", lambda path: None)  # the check "missed" it
    monkeypatch.setattr(winacl, "create_protected_directory", lambda path: False)
    monkeypatch.setattr(
        windows.subprocess, "run", lambda argv, **kw: ran.append(argv) or ok(argv)
    )
    out, err = io.StringIO(), io.StringIO()
    assert windows.install(out, err, print_only=False) == 2
    assert "junction or a symbolic link" in err.getvalue()
    assert not any(str(root / "models") in " ".join(argv) for argv in ran)


def test_a_service_sid_is_the_sha1_of_the_upper_cased_name():
    from swarmscribe_follower import winacl

    # the documented SID of NT SERVICE\TrustedInstaller
    assert winacl.service_sid("TrustedInstaller") == (
        "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
    )
    assert winacl.service_sid("trustedinstaller") == winacl.service_sid("TrustedInstaller")
    assert winacl.service_sid(windows.SERVICE_NAME).startswith("S-1-5-80-")


def test_the_service_name_is_defined_once():
    from swarmscribe_follower import winacl

    assert winacl.SERVICE_NAME is windows.SERVICE_NAME


@windows_only
def test_outside_the_control_manager_the_dispatcher_says_so_at_once():
    began = time.monotonic()
    error = windows.serve_under_scm(
        lambda report: ServiceHost(lambda stops: 0, report, grace_seconds=lambda: 8.0)
    )
    assert error == windows.ERROR_FAILED_SERVICE_CONTROLLER_CONNECT
    assert time.monotonic() - began < 10


@windows_only
def test_the_services_command_runs_the_follower_in_the_real_interpreter_given_its_path(
    tmp_path,
):
    """The command `service install` registers (the real interpreter with service_boot.py),
    run in a console: it gets as far as reading its settings file, and reports exit 2 without
    SERVICE_STOPPED. The venv's import path is GIVEN here (PYTHONPATH): what shows the boot
    script finds the environment by itself is the test below."""
    settings = tmp_path / "follower.env"
    settings.write_text("this line is not a setting\n", encoding="utf-8")
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("SWARMSCRIBE_")
    }
    # The real interpreter knows nothing of this checkout's environment (an installed
    # follower has its dependencies beside it; a development checkout has them in the venv).
    environment["PYTHONPATH"] = os.pathsep.join(entry for entry in sys.path if entry)
    # Without `-I`: a development checkout's dependencies are in the venv, not beside the
    # package, so the isolated start cannot find them. The test below and the Windows harness
    # (an installed follower) run the command as it is registered.
    python, boot = windows.image()[0], windows.image()[-1]
    done = subprocess.run(
        [python, boot, "--foreground", "--env-file", str(settings)],
        capture_output=True, text=True, timeout=120, env=environment,
    )
    assert done.returncode == 2, done.stderr
    assert done.stderr.splitlines() == [
        "service status: START_PENDING accepts=0x0 exit=0 wait_hint_ms=30000 checkpoint=1",
        "service status: RUNNING accepts=0x101 exit=0 wait_hint_ms=0 checkpoint=0",
        f"error: {settings}, line 1: expected NAME=value",
    ]


# --- one settings path, and the options that mean nothing -----------------------------------


def test_the_service_hands_its_settings_file_to_the_same_main_that_run_uses(
    tmp_path, monkeypatch
):
    log = io.StringIO()
    given = []
    monkeypatch.setattr(cli, "main", lambda argv, **kw: given.append(argv) or 0)
    present = tmp_path / "follower.env"
    present.write_text("SWARMSCRIBE_LEADER_URL=https://x.example.org\n", encoding="utf-8")
    assert windows.follower(present, False, lambda: log)(windows.StopSignals()) == 0
    named = tmp_path / "named.env"
    assert windows.follower(named, True, lambda: log)(windows.StopSignals()) == 0
    assert given == [["--env-file", str(present), "run"], ["--env-file", str(named), "run"]]
    assert log.getvalue() == ""


def test_an_absent_default_settings_file_is_said_in_the_log(tmp_path, monkeypatch):
    log = io.StringIO()
    given = []
    monkeypatch.setattr(cli, "main", lambda argv, **kw: given.append(argv) or 0)
    absent = tmp_path / "follower.env"
    assert windows.follower(absent, False, lambda: log)(windows.StopSignals()) == 0
    assert given == [["run"]]
    assert log.getvalue() == f"no settings file at {absent}; using the environment alone\n"


def test_options_that_mean_nothing_to_a_service_command_are_refused():
    for argv, word in (
        (["--env-file", "x.env", "service", "install"], "--env-file"),
        (["--env-file", "x.env", "service", "uninstall"], "--env-file"),
        (["service", "foreground", "--print"], "--print"),
    ):
        out, err = io.StringIO(), io.StringIO()
        assert cli.main(argv, out=out, err=err) == 2
        assert word in err.getvalue() and out.getvalue() == ""


@windows_only
def test_the_boot_script_finds_the_environment_by_itself_and_hides_its_own_folder(tmp_path):
    """What `service_boot.bootstrap()` is for, shown with the real interpreter, a clean
    environment and a stand-in install: a site-packages-like folder holding the package (with
    the real service_boot.py beside a stub `windows`, and a stub `logs` module like the real
    package's), one other library, and a `.pth` file naming a folder with a third. The boot
    script is run as the control manager runs it: the libraries are importable only because it
    added the folder (and read its `.pth` file), and the package's own modules only by their
    package name, not bare.

    And nothing outside the install wins over it. A decoy folder holds libraries of the same
    names; it is offered through PYTHONPATH, which puts it ahead of every site-packages. The
    registered command (`-I`) never sees it; and started without `-I`, the boot script still
    puts the install's own folder first."""
    site = tmp_path / "site-packages"
    package = site / "swarmscribe_follower"
    package.mkdir(parents=True)
    (site / "stub_library.py").write_text("MARK = 'found'\n", encoding="utf-8")
    (tmp_path / "linked").mkdir()
    (tmp_path / "linked" / "pth_library.py").write_text("MARK = 'linked'\n", encoding="utf-8")
    (site / "stand-in.pth").write_text(str(tmp_path / "linked") + "\n", encoding="utf-8")
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    for name in ("stub_library", "pth_library"):
        (decoy / f"{name}.py").write_text("MARK = 'DECOY'\n", encoding="utf-8")
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "logs.py").write_text("", encoding="utf-8")
    (package / "windows.py").write_text(
        "def service_process(argv):\n"
        "    import pth_library\n"
        "    import stub_library\n"
        "    try:\n"
        "        import logs\n"
        "        bare = 'importable'\n"
        "    except ImportError:\n"
        "        bare = 'hidden'\n"
        "    print(stub_library.MARK, pth_library.MARK, bare, argv)\n"
        "    return 0\n",
        encoding="utf-8",
    )
    boot = package / "service_boot.py"
    boot.write_text(windows.boot_script().read_text(encoding="utf-8"), encoding="utf-8")
    environment = {
        name: value
        for name, value in os.environ.items()
        if name.upper() not in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP")
        and not name.startswith("SWARMSCRIBE_")
    }
    registered = windows.image()
    python, options = registered[0], registered[1:-1]
    assert options == ["-I"]

    def start(*command, **more):
        return subprocess.run(
            command, capture_output=True, text=True, env={**environment, **more}, timeout=60,
            cwd=tmp_path,
        )

    # without the boot script's bootstrap the library is not on the path
    assert start(python, "-c", "import stub_library").returncode != 0
    # and the decoy is one the bare interpreter does find, ahead of everything
    shadowed = start(
        python, "-c", "import stub_library; print(stub_library.MARK)", PYTHONPATH=str(decoy)
    )
    assert shadowed.stdout.strip() == "DECOY", shadowed.stderr
    for command in (
        [python, *options, str(boot), "--x"],  # as registered
        [python, str(boot), "--x"],  # not isolated: the install's folder is still first
    ):
        for more in ({}, {"PYTHONPATH": str(decoy)}):
            done = start(*command, **more)
            assert done.returncode == 0, done.stderr
            assert done.stdout.strip() == "found linked hidden ['--x']", (command, more)
