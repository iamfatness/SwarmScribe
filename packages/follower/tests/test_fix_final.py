"""The F2a final review's fixes: a stop that arrives before start-up has finished ends the
follower cleanly with nothing started (I1), and a state folder the credential would be
refused in is refused BEFORE anything is registered, so no join token is spent (I2)."""

import io
import os
import signal
import stat
import subprocess
import sys

import pytest
from follower_testkit import BASE, CPU, JOIN_TOKEN, FakeEngine, FakeLeader, make_agent
from swarmscribe_follower import credentials, entry, fsutil
from swarmscribe_follower import main as cli
from swarmscribe_follower.config import Settings
from swarmscribe_follower.credentials import CredentialFileError, CredentialStore, Stored
from swarmscribe_follower.device import Probe
from swarmscribe_follower.errors import FollowerExit
from swarmscribe_follower.signals import StopSignals

posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX owners and file modes")
STORED = Stored(
    leader_url=BASE,
    follower_id="follower-1",
    credential="credential-SECRET",
    device="cpu",
    heartbeat_interval=10,
    lease_seconds=60,
)


@pytest.fixture
def leader():
    return FakeLeader()


@pytest.fixture
def engine():
    return FakeEngine()


@pytest.fixture(autouse=True)
def environment(monkeypatch, tmp_path):
    for name in list(os.environ):
        if name.startswith("SWARMSCRIBE_") or name in ("HF_HUB_CACHE", "HF_HUB_OFFLINE"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("SWARMSCRIBE_LEADER_URL", BASE)
    monkeypatch.setenv("SWARMSCRIBE_JOIN_TOKEN", JOIN_TOKEN)
    monkeypatch.setenv("SWARMSCRIBE_FOLLOWER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SWARMSCRIBE_FOLLOWER_MODEL_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("SWARMSCRIBE_FOLLOWER_DEVICE", "cpu")
    signals = {
        name: signal.getsignal(getattr(signal, name))
        for name in StopSignals.NAMES
        if hasattr(signal, name)
    }
    yield
    for name, previous in signals.items():
        signal.signal(getattr(signal, name), previous)


def run_cli(tmp_path, leader, engine, *argv, signals=None, while_building=None):
    out, err = io.StringIO(), io.StringIO()
    built = []

    def build(settings):
        if while_building is not None:
            while_building()
        built.append(make_agent(tmp_path, leader, engine))
        return built[-1]

    code = cli.main(list(argv), build=build, out=out, err=err, signals=signals)
    return code, out.getvalue(), err.getvalue(), built


def nothing_was_started(tmp_path, leader, engine) -> bool:
    """No request to the leader, no model, no lock file, no scratch folder."""
    return (
        leader.kinds == []
        and leader.registrations == 0
        and engine.loads == []
        and not (tmp_path / "state" / "follower.lock").exists()
        and not (tmp_path / "state" / "scratch").exists()
    )


# --- I1: a stop before start-up has finished -----------------------------------------------


def test_a_stop_that_arrived_before_the_agent_is_built_exits_0_with_nothing_started(
    tmp_path, leader, engine
):
    signals = StopSignals()
    signals.handler(signal.SIGINT, None)  # it arrived while the follower was being imported
    code, _out, err, built = run_cli(tmp_path, leader, engine, "run", signals=signals)
    assert code == 0 and "error" not in err
    assert built == []  # the device was not probed and no agent was made
    assert nothing_was_started(tmp_path, leader, engine)
    assert not (tmp_path / "state").exists()


def test_a_stop_that_arrives_while_the_agent_is_built_exits_0_with_nothing_started(
    tmp_path, leader, engine
):
    signals = StopSignals()
    code, _out, err, built = run_cli(
        tmp_path,
        leader,
        engine,
        "run",
        signals=signals,
        while_building=lambda: signals.handler(signal.SIGINT, None),
    )
    assert code == 0 and "error" not in err
    assert len(built) == 1
    assert nothing_was_started(tmp_path, leader, engine)


def test_a_real_signal_caught_by_the_early_handlers_stops_the_run(tmp_path, leader, engine):
    signals = StopSignals()
    previous = signals.install()
    try:
        signal.raise_signal(signal.SIGINT)  # caught and counted, not a KeyboardInterrupt
        code, _out, _err, built = run_cli(tmp_path, leader, engine, "run", signals=signals)
    finally:
        signals.restore(previous)
    assert code == 0 and built == [] and nothing_was_started(tmp_path, leader, engine)


def test_run_supervised_leaves_handlers_it_was_given_installed(tmp_path, leader, engine):
    signals = StopSignals()
    previous = signals.install()
    try:
        agent = make_agent(tmp_path, leader, engine)
        signals.handler(signal.SIGINT, None)
        with pytest.raises(FollowerExit) as stopped:  # main turns it into exit 0
            agent.run_supervised(poll=0.02, signals=signals)
        assert stopped.value.code == 0
        # They are the caller's (entry.run's) to put back: a signal between run_supervised
        # returning and the process ending must still be caught, not kill the process.
        assert signal.getsignal(signal.SIGINT) == signals.handler
    finally:
        signals.restore(previous)
    assert nothing_was_started(tmp_path, leader, engine)


def test_the_entry_point_installs_the_handlers_before_it_loads_the_follower(monkeypatch):
    seen = {}

    def fake_main(*, signals=None):
        seen["signals"] = signals
        seen["handler"] = signal.getsignal(signal.SIGINT)
        return 0

    before = signal.getsignal(signal.SIGINT)
    monkeypatch.setattr(cli, "main", fake_main)
    monkeypatch.setattr(sys, "argv", ["swarmscribe-follower", "run"])
    with pytest.raises(SystemExit) as stop:
        entry.run()
    assert stop.value.code == 0
    assert isinstance(seen["signals"], StopSignals)
    assert seen["handler"] == seen["signals"].handler
    assert signal.getsignal(signal.SIGINT) == before  # put back when the command has ended


@pytest.mark.parametrize("command", ["join", "leave", "doctor", "--version"])
def test_the_entry_point_leaves_the_other_commands_to_ctrl_c(monkeypatch, command):
    seen = {}

    def fake_main(*, signals=None):
        seen["signals"] = signals
        seen["handler"] = signal.getsignal(signal.SIGINT)
        return 0

    before = signal.getsignal(signal.SIGINT)
    monkeypatch.setattr(cli, "main", fake_main)
    monkeypatch.setattr(sys, "argv", ["swarmscribe-follower", command])
    with pytest.raises(SystemExit):
        entry.run()
    assert seen == {"signals": None, "handler": before}


def test_the_entry_module_imports_nothing_slow():
    """The handlers can only be first if importing the entry point costs nothing: no
    pydantic, no engine, no model library, not even the follower's own main module."""
    code = (
        "import sys\n"
        "import swarmscribe_follower.entry\n"
        "import swarmscribe_follower.signals\n"
        "slow = sorted(name for name in sys.modules if name.split('.')[0] in"
        " ('pydantic', 'httpx', 'swarmscribe_engine', 'swarmscribe_protocol',"
        " 'faster_whisper', 'ctranslate2', 'av', 'numpy')"
        " or name in ('swarmscribe_follower.main', 'swarmscribe_follower.agent'))\n"
        "sys.exit(', '.join(slow) or 0)\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


# --- I2: the state folder is checked before anything is registered ----------------------------


REFUSED = "the folder /state is owned by another user and will not be trusted"


@pytest.fixture
def untrusted(monkeypatch):
    """A state folder `check_folder` refuses, on any platform (the rule itself is POSIX's
    and is tested below, where POSIX is)."""

    def refuse(self, *, tighten=True):
        raise CredentialFileError(REFUSED)

    monkeypatch.setattr(CredentialStore, "check_folder", refuse)


def test_an_untrusted_state_folder_ends_run_with_2_and_the_token_is_not_spent(
    tmp_path, leader, engine, untrusted
):
    code, _out, err, built = run_cli(tmp_path, leader, engine, "run")
    assert code == 2
    assert REFUSED in err and "no join token was used" in err and "Traceback" not in err
    assert JOIN_TOKEN not in err
    assert leader.kinds == [] and leader.registrations == 0 and leader.join_uses == 0
    assert engine.loads == []  # refused before the model load, too
    assert not (tmp_path / "state" / "credential.json").exists()
    built[0].close()


def test_an_untrusted_state_folder_is_refused_by_prepare_before_the_scratch_folder(
    tmp_path, leader, engine, untrusted
):
    agent = make_agent(tmp_path, leader, engine)
    with pytest.raises(FollowerExit) as refused:
        agent.prepare()
    assert refused.value.code == 2 and REFUSED in refused.value.reason
    assert leader.kinds == [] and not (tmp_path / "state" / "scratch").exists()
    # The lock was let go again: the operator can fix the folder and start it.
    cli.hold_state_lock(tmp_path / "state").close()


def test_an_untrusted_state_folder_ends_join_with_2_and_the_token_is_not_spent(
    tmp_path, leader, engine, untrusted
):
    code, _out, err, built = run_cli(tmp_path, leader, engine, "join")
    assert code == 2 and REFUSED in err
    assert built == [] and leader.kinds == [] and leader.join_uses == 0


def test_doctor_says_that_the_state_folder_would_not_be_trusted(
    tmp_path, leader, engine, untrusted, monkeypatch
):
    monkeypatch.setattr(cli, "probe", lambda preference: Probe(CPU))
    out = io.StringIO()
    code = cli.command_doctor(Settings(), out, load_model=False, ask_leader=False)
    text = out.getvalue()
    assert code == 2
    assert f"state folder: FAILED: {REFUSED}" in text
    assert "result: NOT READY (exit 2)" in text
    assert "scratch folder: ok" in text  # only the state folder holds the credential


def test_a_trusted_state_folder_registers_as_before(tmp_path, leader, engine):
    agent = make_agent(tmp_path, leader, engine)
    agent.prepare()
    try:
        assert leader.registrations == 1
        assert (tmp_path / "state" / "credential.json").exists()
    finally:
        agent.close()


def test_the_folder_check_passes_a_folder_that_is_not_there_yet_and_makes_nothing(tmp_path):
    store = CredentialStore(tmp_path / "state" / "credential.json")
    store.check_folder(tighten=False)  # doctor's: `run` will create it 0700
    assert not (tmp_path / "state").exists()


def test_the_folder_check_passes_the_followers_own_folder(tmp_path):
    store = CredentialStore(tmp_path / "state" / "credential.json")
    store.check_folder()
    store.save(STORED)
    store.check_folder()
    store.check_folder(tighten=False)
    assert store.load() == STORED


def someone_else(monkeypatch):
    """Make this process look like another user than the one who owns its files (a follower
    as uid 10001 given a root-owned folder), without needing root to chown anything."""
    me = os.getuid()
    monkeypatch.setattr(credentials.os, "getuid", lambda: me + 1)
    monkeypatch.setattr(fsutil.os, "getuid", lambda: me + 1)
    return me


@posix_only
def test_a_folder_owned_by_another_user_is_refused_naming_it_its_owner_its_mode_and_the_cure(
    tmp_path, monkeypatch
):
    folder = tmp_path / "state"
    folder.mkdir()
    folder.chmod(0o777)  # a bind mount from Windows, a Kubernetes emptyDir
    owner = someone_else(monkeypatch)
    store = CredentialStore(folder / "credential.json")
    for tighten in (True, False):
        with pytest.raises(CredentialFileError) as refused:
            store.check_folder(tighten=tighten)
        said = str(refused.value)
        assert str(folder) in said and "owned by another user" in said
        assert f"owner uid {owner}, mode 0777" in said
        assert f"runs as uid {owner + 1}" in said
        assert f"chown {owner + 1} {folder} && chmod 700 {folder}" in said
    assert stat.S_IMODE(folder.stat().st_mode) == 0o777  # another user's folder is left alone


@posix_only
def test_a_follower_given_another_users_folder_never_registers(
    tmp_path, leader, engine, monkeypatch
):
    (tmp_path / "state").mkdir()
    (tmp_path / "state").chmod(0o777)
    someone_else(monkeypatch)
    code, _out, err, built = run_cli(tmp_path, leader, engine, "run")
    assert code == 2 and "owned by another user" in err and "no join token was used" in err
    assert leader.kinds == [] and leader.join_uses == 0
    assert not (tmp_path / "state" / "credential.json").exists()
    built[0].close()


@posix_only
def test_the_folder_rule_before_registering_is_the_rule_load_applies_afterwards(
    tmp_path, monkeypatch
):
    """What the check refuses up front is exactly what `load` would refuse at the next start."""
    folder = tmp_path / "state"
    store = CredentialStore(folder / "credential.json")
    store.save(STORED)
    someone_else(monkeypatch)
    with pytest.raises(CredentialFileError) as later:
        store.load()
    with pytest.raises(CredentialFileError) as up_front:
        store.check_folder()
    assert str(later.value) == str(up_front.value)


@posix_only
def test_the_followers_own_loose_folder_is_tightened_before_registering_as_save_does(tmp_path):
    folder = tmp_path / "state"
    folder.mkdir()
    folder.chmod(0o777)
    store = CredentialStore(folder / "credential.json")
    store.check_folder(tighten=False)  # doctor changes nothing, and `run` would tighten it
    assert stat.S_IMODE(folder.stat().st_mode) == 0o777
    store.check_folder()
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700


@posix_only
def test_a_folder_that_cannot_be_tightened_is_refused_before_registering(tmp_path, monkeypatch):
    folder = tmp_path / "state"
    folder.mkdir()
    folder.chmod(0o777)
    # A mount that ignores chmod (Docker Desktop's bind mounts do).
    monkeypatch.setattr(credentials, "private_folder", lambda path: None)
    with pytest.raises(CredentialFileError, match="writable by others.*mode 0777.*chmod 700"):
        CredentialStore(folder / "credential.json").check_folder()


@posix_only
def test_a_folder_loosened_while_it_held_the_credential_is_refused_not_tightened(tmp_path):
    """The rule is not loosened: once a credential is there, a folder others could write to
    is refused (the file could have been planted), by the check as by `load`."""
    folder = tmp_path / "state"
    store = CredentialStore(folder / "credential.json")
    store.save(STORED)
    folder.chmod(0o777)
    for check in (store.check_folder, lambda: store.check_folder(tighten=False), store.load):
        with pytest.raises(CredentialFileError, match="chmod 700"):
            check()
    assert stat.S_IMODE(folder.stat().st_mode) == 0o777
