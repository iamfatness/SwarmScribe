"""The systemd test's own parts (e2e/follower-systemd), checked without Docker: that the values
its driver hard-codes agree with the unit, the settings example and the memory guard it
depends on."""

import importlib.util
import re
import sys
from pathlib import Path

import pytest
from swarmscribe_follower.memory import cgroup_limit_mb, job_mb

ROOT = Path(__file__).resolve().parents[3]
SYSTEMD = ROOT / "e2e" / "follower-systemd"
DEPLOY = ROOT / "deploy" / "systemd"
UNIT = DEPLOY / "swarmscribe-follower.service"
SETTINGS = DEPLOY / "follower.env.example"


@pytest.fixture(scope="module")
def driver():
    spec = importlib.util.spec_from_file_location("follower_systemd_driver", SYSTEMD / "run_e2e.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["follower_systemd_driver"] = module
    spec.loader.exec_module(module)
    return module


def unit_value(name: str) -> str:
    (value,) = re.findall(rf"^{name}=(.*)$", UNIT.read_text(encoding="utf-8"), re.MULTILINE)
    return value.strip()


def setting(name: str) -> str:
    (value,) = re.findall(rf"^{name}=(.*)$", SETTINGS.read_text(encoding="utf-8"), re.MULTILINE)
    return value.strip()


def test_the_grace_the_driver_expects_is_the_settings_and_fits_inside_the_stop_timeout(driver):
    assert int(setting("SWARMSCRIBE_FOLLOWER_SHUTDOWN_GRACE_SECONDS")) == driver.GRACE_SECONDS
    assert int(unit_value("TimeoutStopSec")) > driver.GRACE_SECONDS
    # The step's comments and messages name the same figure.
    assert f"{driver.GRACE_SECONDS} s of grace" in (SYSTEMD / "run_e2e.py").read_text("utf-8")


def test_the_restarts_the_driver_expects_of_exit_3_are_the_units_start_limit(driver):
    # StartLimitBurst starts in the interval: the first, and the rest restarts.
    assert int(unit_value("StartLimitBurst")) - 1 == driver.EXIT_3_RESTARTS
    assert unit_value("Restart") == "on-failure"
    assert int(unit_value("StartLimitIntervalSec")) > 0


def test_the_exit_statuses_the_driver_expects_not_to_be_restarted_are_the_units(driver):
    prevented = {int(code) for code in unit_value("RestartPreventExitStatus").split()}
    assert set(driver.NOT_RESTARTED_STATUSES) <= prevented
    assert 3 not in prevented  # step 10 expects exit 3 to be restarted
    assert 0 not in prevented  # drained (0) is left stopped by Restart=on-failure instead


def test_the_memory_limit_the_driver_sets_is_the_one_the_guard_reads_and_the_driver_expects(
    driver, tmp_path
):
    source = (SYSTEMD / "run_e2e.py").read_text(encoding="utf-8")
    # The drop-in sets MemoryMax= from the constant, and the refusal is matched on it.
    assert "MemoryMax={MEMORY_LIMIT_MIB}M" in source
    assert 'f"may use {MEMORY_LIMIT_MIB} MiB"' in source
    # systemd writes MemoryMax=<n>M into the unit's cgroup as n * 2**20 bytes; the guard
    # reads exactly n MiB back.
    unit_cgroup = tmp_path / "system.slice" / "swarmscribe-follower.service"
    unit_cgroup.mkdir(parents=True)
    (unit_cgroup / "memory.max").write_text(f"{driver.MEMORY_LIMIT_MIB * 2**20}\n")
    (tmp_path / "memory.max").write_text("max\n")
    proc = tmp_path / "proc-cgroup"
    proc.write_text("0::/system.slice/swarmscribe-follower.service\n")
    assert cgroup_limit_mb(root=tmp_path, proc=proc) == driver.MEMORY_LIMIT_MIB


def test_the_memory_limit_admits_the_long_recordings_and_refuses_the_hour(driver):
    # Measured, in MiB (README, "Sizing a pool"): tiny.en once loaded, and what a follower keeps.
    loaded, kept = 230, 300
    longest = driver.LONG_REPEATS * 5.0  # the fixture is five seconds long
    assert loaded + kept + job_mb(longest, split=True) < driver.MEMORY_LIMIT_MIB
    assert job_mb(3600, split=False) > driver.MEMORY_LIMIT_MIB


def test_the_files_the_driver_copies_into_the_machine_exist(driver):
    for path in (driver.UNIT, driver.ENV_EXAMPLE, driver.CONSTRAINTS, driver.ADMIN):
        assert path.is_file(), path
    assert driver.UNIT == UNIT
    assert driver.ENV_EXAMPLE == SETTINGS
    assert driver.UNIT.name == f"{driver.SERVICE}.service"
    assert (SYSTEMD / "Dockerfile").is_file()


def test_the_driver_reads_the_settings_file_and_the_token_file_the_unit_and_example_name(driver):
    assert driver.ENV_FILE in unit_value("ExecStart")
    token = setting("SWARMSCRIBE_JOIN_TOKEN_FILE")
    assert token == "/etc/swarmscribe-follower/join-token"
    assert f"cat > {token}" in (SYSTEMD / "run_e2e.py").read_text(encoding="utf-8")


def test_the_machine_has_the_tool_the_listener_check_runs():
    dockerfile = (SYSTEMD / "Dockerfile").read_text(encoding="utf-8")
    assert "iproute2" in dockerfile  # `ss`
    assert "ss -Hltnp" in (SYSTEMD / "run_e2e.py").read_text(encoding="utf-8")


def test_the_uv_command_defaults_to_the_running_interpreter_and_survives_windows_paths(driver):
    assert driver.uv_command({}, lambda name: object()) == [sys.executable, "-m", "uv"]
    # `uv run` sets UV to uv.exe's path; the driver neither uses that name nor splits it.
    given = {"UV": r"C:\Users\x\uv.exe"}
    assert driver.uv_command(given, lambda name: object()) == [sys.executable, "-m", "uv"]
    # Under `uv run` the interpreter is the project's venv, which has no uv module: the uv that
    # launched the driver is used as one command, backslashes intact.
    launcher = SYSTEMD / "run_e2e.py"
    assert driver.uv_command({"UV": str(launcher)}, lambda name: None) == [str(launcher)]
    assert driver.uv_command({"UV": "missing"}, lambda name: None) == ["uv"]
    assert driver.uv_command({"E2E_UV": "python -m uv"}, lambda name: None) == [
        "python",
        "-m",
        "uv",
    ]


def test_an_installed_uv_module_is_what_the_default_finds(driver):
    assert driver.UV[0] in (sys.executable, "uv") or Path(driver.UV[0]).is_file()
