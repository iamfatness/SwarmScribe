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
    """The data folder is locked the moment it exists, before anything is put in it, and the
    service account is granted its access only once the service (and so the account) exists."""
    plan = windows.install_plan([PYTHON, BOOT], ROOT)
    lock = ["icacls.exe", str(ROOT), "/inheritance:r", "/grant:r",
            "*S-1-5-32-544:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F"]
    account = r"NT SERVICE\SwarmScribeFollower"
    kinds = steps_of(plan)
    assert kinds[:3] == [("check", ROOT), ("mkdir", ROOT), ("run", lock)]
    assert kinds[3:7] == [
        ("mkdir", ROOT / "state"), ("mkdir", ROOT / "models"), ("mkdir", ROOT / "logs"),
        ("settings", ROOT / "follower.env"),
    ]
    create = [
        value for kind, value in kinds if kind == "run" and value[:2] == ["sc.exe", "create"]
    ]
    assert create == [[
        "sc.exe", "create", "SwarmScribeFollower", "binPath=", f'"{PYTHON}" "{BOOT}"',
        "start=", "delayed-auto", "obj=", account, "DisplayName=", "SwarmScribe Follower",
    ]]
    assert kinds[7] == ("run", create[0])
    assert kinds[9] == ("run", [
        "sc.exe", "failure", "SwarmScribeFollower", "reset=", "86400", "actions=",
        "restart/60000/restart/60000//60000",
    ])
    assert kinds[10] == ("run", ["sc.exe", "failureflag", "SwarmScribeFollower", "0"])
    assert kinds[11:] == [
        ("run", ["icacls.exe", str(ROOT), "/grant:r", f"{account}:(OI)(CI)RX"]),
        *(
            ("run", ["icacls.exe", str(ROOT / name), "/grant:r", f"{account}:(OI)(CI)M"])
            for name in ("state", "models", "logs")
        ),
        ("check", ROOT),
    ]
    text = " ".join(part for kind, value in kinds if kind == "run" for part in value).lower()
    assert "token" not in text and "password" not in text


def installing(tmp_path, monkeypatch, run):
    root = tmp_path / "swarmscribe-follower"
    monkeypatch.setattr(windows, "data_root", lambda: root)
    monkeypatch.setattr(windows, "is_administrator", lambda: True)
    monkeypatch.setattr(windows, "image_problem", lambda command: None)
    monkeypatch.setattr(windows, "root_problem", lambda path: None)
    monkeypatch.setattr(windows.subprocess, "run", run)
    return root


def test_install_runs_its_steps_in_that_order_and_nothing_is_made_before_the_lock(
    tmp_path, monkeypatch
):
    seen = []

    def run(argv, **kwargs):
        seen.append((argv[:2], sorted(p.name for p in root.rglob("*"))))
        return subprocess.CompletedProcess(argv, 0, "", "")

    root = installing(tmp_path, monkeypatch, run)
    out, err = io.StringIO(), io.StringIO()
    assert windows.install(out, err, print_only=False) == 0, err.getvalue()
    assert seen[0] == (["icacls.exe", str(root)], [])  # the lock: the root and nothing in it
    assert [argv[0] for argv, _ in seen[1:5]] == ["sc.exe"] * 4
    assert seen[1][1] == ["follower.env", "logs", "models", "state"]
    assert [argv[0] for argv, _ in seen[5:]] == ["icacls.exe"] * 4


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


def test_a_failure_before_the_service_exists_does_not_say_to_uninstall(tmp_path, monkeypatch):
    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, "", "boom")

    installing(tmp_path, monkeypatch, run)
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
    for name in ("follower.env", "join-token"):
        (root / name).write_text("x", encoding="utf-8")


@windows_only
def test_a_data_folder_or_anything_in_it_owned_by_someone_else_is_refused(tmp_path, monkeypatch):
    """Anyone may create a folder under %ProgramData% before the install does. Only what
    Administrators, SYSTEM or the installing administrator own is used: the owner may change
    its permissions back at any time, and could read the token put into a file it made. The
    owner is crafted here (it cannot be given away without privilege), so this holds whether
    the account running the tests is elevated (files then belong to Administrators) or not."""
    from swarmscribe_follower import winacl

    assert windows.root_problem(tmp_path / "absent") is None
    root = tmp_path / "swarmscribe-follower"
    make_tree(root)
    assert windows.root_problem(root) is None  # all owned by whoever installs
    real = winacl.read_acl
    for name in ("", "state", "models", "logs", "follower.env", "join-token"):
        foreign = root / name if name else root

        def read(path, descriptor_of=None, foreign=foreign):
            owner, allowed = real(path, descriptor_of)
            return ("S-1-5-32-545", allowed) if Path(path) == foreign else (owner, allowed)

        monkeypatch.setattr(winacl, "read_acl", read)
        assert f"{foreign} exists and was not made by an administrator" in (
            windows.root_problem(root) or ""
        )
        monkeypatch.setattr(winacl, "read_acl", real)


def test_a_service_sid_is_the_sha1_of_the_upper_cased_name():
    from swarmscribe_follower import winacl

    # the documented SID of NT SERVICE\TrustedInstaller
    assert winacl.service_sid("TrustedInstaller") == (
        "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
    )
    assert winacl.service_sid("trustedinstaller") == winacl.service_sid("TrustedInstaller")
    assert winacl.service_sid(windows.SERVICE_NAME).startswith("S-1-5-80-")


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
    # The real interpreter knows nothing of this checkout's environment (an installed
    # follower has its dependencies beside it; a development checkout has them in the venv).
    environment["PYTHONPATH"] = os.pathsep.join(entry for entry in sys.path if entry)
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
