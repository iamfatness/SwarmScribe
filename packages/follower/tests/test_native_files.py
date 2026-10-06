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
    # the follower reads the token again when it must register again (README: keep the file)
    assert "keep that file" in EXAMPLE and "emptied" not in EXAMPLE and "read once" not in EXAMPLE
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
