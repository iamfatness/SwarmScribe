import io
import json
import logging
import os
import signal
import subprocess
import sys

import httpx
import pytest
from follower_testkit import BASE, CPU, JOIN_TOKEN, FakeEngine, FakeLeader, make_agent
from swarmscribe_follower import main as cli
from swarmscribe_follower.config import Settings
from swarmscribe_follower.device import Probe
from swarmscribe_follower.errors import FollowerExit
from swarmscribe_follower.leader import LeaderClient
from swarmscribe_follower.models import ModelHost


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
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    signals = {
        name: signal.getsignal(getattr(signal, name))
        for name in ("SIGINT", "SIGTERM", "SIGBREAK")
        if hasattr(signal, name)
    }
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
    for name, previous in signals.items():
        signal.signal(getattr(signal, name), previous)


def run_cli(tmp_path, leader, engine, *argv, **overrides):
    out, err = io.StringIO(), io.StringIO()
    built = []

    def build(settings):
        built.append(make_agent(tmp_path, leader, engine, **overrides))
        return built[-1]

    code = cli.main(list(argv), build=build, out=out, err=err)
    return code, out.getvalue(), err.getvalue(), built


@pytest.mark.parametrize(
    ("name", "value", "said"),
    [
        ("SWARMSCRIBE_FOLLOWER_DEVICE", "tpu", "device"),
        ("SWARMSCRIBE_LEADER_URL", "http://leader.example.org", "https"),
        ("SWARMSCRIBE_FOLLOWER_SHUTDOWN_GRACE_SECONDS", "-1", "shutdown_grace_seconds"),
    ],
)
def test_invalid_configuration_exits_2_naming_fields_and_never_values(
    monkeypatch, name, value, said
):
    monkeypatch.setenv(name, value)
    out, err = io.StringIO(), io.StringIO()
    assert cli.main(["run"], out=out, err=err) == 2
    text = err.getvalue()
    assert "invalid configuration" in text and said in text
    assert JOIN_TOKEN not in text and "Traceback" not in text


def test_run_joins_works_and_exits_0_when_drained(tmp_path, leader, engine):
    job_id = leader.add_job()
    engine.on_step = lambda fraction: setattr(leader, "state", "draining")
    code, _out, err, _ = run_cli(tmp_path, leader, engine, "run")
    assert code == 0, err
    assert leader.jobs[job_id]["state"] == "completed"
    lines = [json.loads(line) for line in err.splitlines()]
    assert {"registered", "job.claimed", "job.completed", "drained"} <= {
        line.get("event") for line in lines
    }
    assert JOIN_TOKEN not in err and "credential-SECRET" not in err


def test_run_says_why_it_cannot_start_and_exits_with_the_code(tmp_path, leader, engine):
    engine.load_error = RuntimeError("Library cublas64_12.dll is not found")
    code, _out, err, _ = run_cli(tmp_path, leader, engine, "run")
    assert code == 3
    assert err.strip().splitlines()[-1].startswith("error: this machine cannot transcribe")
    assert leader.registrations == 0


def test_a_revoked_follower_makes_run_exit_4(tmp_path, leader, engine):
    leader.on["claim"] = lambda: setattr(leader, "state", "revoked")
    code, _out, _err, _ = run_cli(tmp_path, leader, engine, "run")
    assert code == 4


def test_two_followers_cannot_share_a_state_folder(tmp_path):
    held = cli.hold_state_lock(tmp_path / "state")
    try:
        with pytest.raises(FollowerExit) as stop:
            cli.hold_state_lock(tmp_path / "state")
        assert stop.value.code == 2 and "already using" in stop.value.reason
    finally:
        held.close()
    cli.hold_state_lock(tmp_path / "state").close()  # free again once the first has ended


def test_join_stores_a_credential_and_leave_deletes_it(tmp_path, leader, engine, monkeypatch):
    code, out, err, _ = run_cli(tmp_path, leader, engine, "join")
    assert code == 0, err
    assert out.startswith("joined as follower ")
    assert (tmp_path / "state" / "credential.json").is_file()
    assert engine.loads == []  # joining loads no model
    monkeypatch.setattr(
        cli, "LeaderClient", lambda url, **kw: LeaderClient(
            url, credential=kw.get("credential"), transport=leader.transport
        )
    )
    code, out, _err, _ = run_cli(tmp_path, leader, engine, "leave")
    assert (code, leader.deregistrations) == (0, 1)
    assert "credential is deleted" in out
    assert not (tmp_path / "state" / "credential.json").exists()
    code, out, _err, _ = run_cli(tmp_path, leader, engine, "leave")
    assert code == 0 and "has not joined" in out


def test_join_with_a_bad_token_exits_4_and_stores_nothing(tmp_path, leader, engine):
    code, _out, err, _ = run_cli(tmp_path, leader, engine, "join", join_token="wrong")
    assert code == 4
    assert "not valid" in err and "wrong" not in err
    assert not (tmp_path / "state" / "credential.json").exists()


def test_the_model_loader_is_told_its_cache_and_offline_mode(tmp_path, monkeypatch):
    settings = Settings(leader_url=BASE, model_dir=tmp_path / "models", offline=True)
    cli.configure_environment(settings)
    assert os.environ["HF_HUB_CACHE"] == str(tmp_path / "models")
    assert os.environ["HF_HUB_OFFLINE"] == "1"
    monkeypatch.delenv("HF_HUB_CACHE")
    monkeypatch.delenv("HF_HUB_OFFLINE")
    monkeypatch.delenv("SWARMSCRIBE_FOLLOWER_MODEL_DIR")
    cli.configure_environment(Settings(leader_url=BASE))
    assert "HF_HUB_OFFLINE" not in os.environ and "HF_HUB_CACHE" not in os.environ


def test_a_ca_file_that_is_not_certificates_exits_2(tmp_path):
    bad = tmp_path / "ca.pem"
    bad.write_text("not a certificate")
    for path in (bad, tmp_path / "missing.pem"):
        with pytest.raises(FollowerExit) as stop:
            cli.tls(Settings(leader_url=BASE, leader_ca_file=path))
        assert stop.value.code == 2
    assert cli.tls(Settings(leader_url=BASE)) is True


def doctor(tmp_path, leader, engine, monkeypatch):
    monkeypatch.setattr(cli, "probe", lambda preference: Probe(CPU))
    out = io.StringIO()
    code = cli.command_doctor(
        Settings(),
        out,
        host=lambda device, **kw: ModelHost(device, factory=engine, **kw),
        client=lambda url, **kw: LeaderClient(url, transport=leader.transport),
    )
    return code, out.getvalue()


def test_doctor_reports_a_working_machine_and_registers_nothing(
    tmp_path, leader, engine, monkeypatch
):
    code, text = doctor(tmp_path, leader, engine, monkeypatch)
    assert code == 0
    assert "device: cpu" in text
    assert "model: distil-large-v3 (int8) loaded and ran" in text
    assert "leader: answers" in text and "joined: no" in text
    assert leader.registrations == 0 and leader.count("claim") == 0
    assert engine.closed == 1


def test_doctor_names_what_is_broken(tmp_path, leader, engine, monkeypatch):
    engine.load_error = RuntimeError("Library cublas64_12.dll is not found")
    code, text = doctor(tmp_path, leader, engine, monkeypatch)
    assert code == 3
    assert "model: FAILED" in text and "cublas64_12" in text


def test_two_followers_cannot_share_a_state_folder_for_join_and_leave(tmp_path, leader, engine):
    held = cli.hold_state_lock(tmp_path / "state")
    try:
        for command in ("join", "leave"):
            code, _out, err, _ = run_cli(tmp_path, leader, engine, command)
            assert code == 2 and "already using" in err and "Traceback" not in err
    finally:
        held.close()


def test_run_hands_the_agent_to_run_supervised_and_takes_no_lock_of_its_own(
    tmp_path, leader, engine, monkeypatch
):
    seen = {}

    def fake_supervised(self, **kwargs):
        # prepare() holds the state lock for the agent; main took none of its own, or
        # prepare() itself would have been refused.
        seen["reached"] = True
        with pytest.raises(FollowerExit):
            cli.hold_state_lock(tmp_path / "state")
        return 0

    from swarmscribe_follower.agent import Agent

    monkeypatch.setattr(Agent, "run_supervised", fake_supervised)
    code, _out, err, built = run_cli(tmp_path, leader, engine, "run")
    assert seen == {"reached": True}
    assert code == 0, err
    built[0].close()


def test_join_reads_the_token_from_stdin_and_never_from_an_argument(
    tmp_path, leader, engine, monkeypatch
):
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.delenv("SWARMSCRIBE_JOIN_TOKEN")
    seen = []

    def build(settings):
        seen.append(settings.token())
        return make_agent(tmp_path, leader, engine, join_token=settings.token())

    stdin = io.StringIO(JOIN_TOKEN + "\n")
    assert cli.main(["join", "--token-stdin"], build=build, out=out, err=err, stdin=stdin) == 0
    assert seen == [JOIN_TOKEN] and JOIN_TOKEN not in out.getvalue() + err.getvalue()
    for argv in (["join", "--token", "x"], ["join", "x"], ["run", "--join-token", "x"]):
        with pytest.raises(SystemExit) as stop:
            cli.main(argv, out=io.StringIO(), err=io.StringIO())
        assert stop.value.code == 2  # argparse: unrecognised arguments


def test_an_empty_stdin_is_no_token_and_exits_4(tmp_path, leader, engine, monkeypatch):
    out, err = io.StringIO(), io.StringIO()
    # stdin is the explicit source: the variable must not be used in its place.
    monkeypatch.setenv("SWARMSCRIBE_JOIN_TOKEN", JOIN_TOKEN)

    def build(settings):
        return make_agent(tmp_path, leader, engine, join_token=settings.token())

    code = cli.main(["join", "--token-stdin"], build=build, out=out, err=err, stdin=io.StringIO(""))
    assert code == 4 and "no join token" in err.getvalue()


def test_joining_twice_does_not_spend_a_second_token(tmp_path, leader, engine):
    assert run_cli(tmp_path, leader, engine, "join")[0] == 0
    code, out, _err, _ = run_cli(tmp_path, leader, engine, "join")
    assert code == 0 and "already joined" in out and leader.registrations == 1


def test_leave_when_the_leader_is_gone_still_deletes_the_credential(
    tmp_path, leader, engine, monkeypatch
):
    assert run_cli(tmp_path, leader, engine, "join")[0] == 0

    def down(request):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(
        cli,
        "LeaderClient",
        lambda url, **kw: LeaderClient(
            url, credential=kw.get("credential"), transport=httpx.MockTransport(down)
        ),
    )
    code, out, _err, _ = run_cli(tmp_path, leader, engine, "leave")
    assert code == 0 and "could not be told" in out
    assert not (tmp_path / "state" / "credential.json").exists()


def test_an_unexpected_error_is_one_line_with_no_traceback_and_no_text():
    def build(settings):
        raise RuntimeError(f"boom with {JOIN_TOKEN} and https://x.test/secret-link")

    out, err = io.StringIO(), io.StringIO()
    assert cli.main(["run"], build=build, out=out, err=err) == 1
    text = err.getvalue()
    assert "unexpected RuntimeError" in text and "Traceback" not in text
    assert JOIN_TOKEN not in text and "secret-link" not in text


def test_a_bad_command_and_a_missing_command_are_usage_errors_not_tracebacks(capsys):
    for argv in (["bogus"], []):
        with pytest.raises(SystemExit) as stop:
            cli.main(argv)
        assert stop.value.code == 2
    assert "Traceback" not in capsys.readouterr().err


def test_help_and_version_work_and_say_where_the_token_goes(capsys):
    with pytest.raises(SystemExit) as stop:
        cli.main(["--version"])
    assert stop.value.code == 0
    assert capsys.readouterr().out.startswith("swarmscribe-follower ")
    with pytest.raises(SystemExit) as stop:
        cli.main(["--help"])
    help_text = capsys.readouterr().out
    assert stop.value.code == 0
    for word in ("run", "join", "leave", "doctor", "never a command-line argument"):
        assert word in help_text
    with pytest.raises(SystemExit):
        cli.main(["join", "--help"])
    assert "--token-stdin" in capsys.readouterr().out


def test_the_module_runs_without_a_traceback():
    env = {k: v for k, v in os.environ.items() if not k.startswith("SWARMSCRIBE_")}
    version = subprocess.run(
        [sys.executable, "-m", "swarmscribe_follower", "--version"],
        capture_output=True, text=True, env=env, timeout=60,
    )
    assert version.returncode == 0 and version.stdout.startswith("swarmscribe-follower ")
    unconfigured = subprocess.run(
        [sys.executable, "-m", "swarmscribe_follower", "run"],
        capture_output=True, text=True, env=env, timeout=60,
    )
    assert unconfigured.returncode == 2 and "Traceback" not in unconfigured.stderr
    assert "SWARMSCRIBE_LEADER_URL: Field required" in unconfigured.stderr


def test_the_console_script_is_declared_and_points_at_run():
    from importlib.metadata import entry_points

    (script,) = entry_points(group="console_scripts", name="swarmscribe-follower")
    assert script.value == "swarmscribe_follower.main:run"
    assert script.load() is cli.run


def test_doctor_prints_a_line_per_check_and_a_plain_verdict(
    tmp_path, leader, engine, monkeypatch
):
    code, text = doctor(tmp_path, leader, engine, monkeypatch)
    lines = text.splitlines()
    assert code == 0 and lines[-1] == "result: ready"
    for start in ("settings: ok", "state folder: ok", "scratch folder: ok", "device: cpu"):
        assert any(line.startswith(start) for line in lines), start
    assert JOIN_TOKEN not in text
    assert not (tmp_path / "state").exists()  # it created nothing


def test_doctor_without_the_model_loads_nothing(leader, engine, monkeypatch):
    monkeypatch.setattr(cli, "probe", lambda preference: Probe(CPU))
    out = io.StringIO()
    code = cli.command_doctor(
        Settings(),
        out,
        load_model=False,
        host=lambda device, **kw: ModelHost(device, factory=engine, **kw),
        client=lambda url, **kw: LeaderClient(url, transport=leader.transport),
    )
    assert code == 0 and "not checked" in out.getvalue() and engine.loads == []


def test_doctor_says_when_the_leader_does_not_answer(engine, monkeypatch):
    def down(request):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(cli, "probe", lambda preference: Probe(CPU))
    out = io.StringIO()
    code = cli.command_doctor(
        Settings(),
        out,
        host=lambda device, **kw: ModelHost(device, factory=engine, **kw),
        client=lambda url, **kw: LeaderClient(url, transport=httpx.MockTransport(down)),
    )
    lines = out.getvalue().splitlines()
    assert code == 1 and "leader: FAILED: no answer from its /healthz" in lines
    assert lines[-1].startswith("result: NOT READY")


def test_doctor_names_a_state_folder_that_cannot_be_used(tmp_path, leader, engine, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("not a folder")
    monkeypatch.setenv("SWARMSCRIBE_FOLLOWER_STATE_DIR", str(blocker / "state"))
    code, text = doctor(tmp_path, leader, engine, monkeypatch)
    assert code == 2 and "state folder: FAILED" in text


def test_doctor_reports_joined_after_a_join_and_never_prints_the_credential(
    tmp_path, leader, engine, monkeypatch
):
    assert run_cli(tmp_path, leader, engine, "join")[0] == 0
    code, text = doctor(tmp_path, leader, engine, monkeypatch)
    assert code == 0 and "joined: yes" in text
    assert "credential-SECRET" not in text and JOIN_TOKEN not in text


def test_join_takes_the_leader_from_its_own_flag_when_the_setting_is_absent(
    tmp_path, leader, engine, monkeypatch
):
    monkeypatch.delenv("SWARMSCRIBE_LEADER_URL")
    seen = []

    def build(settings):
        seen.append(settings.leader_url)
        return make_agent(tmp_path, leader, engine)

    out, err = io.StringIO(), io.StringIO()
    code = cli.main(["join", "--leader", "http://127.0.0.1:9/"], build=build, out=out, err=err)
    assert code == 0, err.getvalue()
    assert seen == ["http://127.0.0.1:9"]
    assert out.getvalue().startswith("joined as follower ")


def test_the_leader_flag_wins_over_the_setting(tmp_path, leader, engine):
    seen = []

    def build(settings):
        seen.append(settings.leader_url)
        return make_agent(tmp_path, leader, engine)

    argv = ["join", "--leader", "http://127.0.0.1:9"]
    code = cli.main(argv, build=build, out=io.StringIO(), err=io.StringIO())
    assert code == 0 and seen == ["http://127.0.0.1:9"]


def test_a_bad_leader_flag_is_a_configuration_error_naming_the_field(
    tmp_path, leader, engine, monkeypatch
):
    monkeypatch.delenv("SWARMSCRIBE_LEADER_URL")
    code, _out, err, _ = run_cli(tmp_path, leader, engine, "join", "--leader", "not a url")
    assert code == 2 and "leader_url" in err and "not a url" not in err


def test_leave_uses_the_stored_leader_when_the_setting_is_absent(
    tmp_path, leader, engine, monkeypatch
):
    assert run_cli(tmp_path, leader, engine, "join")[0] == 0
    monkeypatch.delenv("SWARMSCRIBE_LEADER_URL")
    urls = []

    def client(url, **kw):
        urls.append(url)
        return LeaderClient(url, credential=kw.get("credential"), transport=leader.transport)

    monkeypatch.setattr(cli, "LeaderClient", client)
    code, out, err, _ = run_cli(tmp_path, leader, engine, "leave")
    assert code == 0, err
    assert (leader.deregistrations, urls) == (1, [BASE])
    assert not (tmp_path / "state" / "credential.json").exists()


def test_leave_without_a_setting_or_a_credential_is_not_an_error(
    tmp_path, leader, engine, monkeypatch
):
    monkeypatch.delenv("SWARMSCRIBE_LEADER_URL")
    code, out, _err, _ = run_cli(tmp_path, leader, engine, "leave")
    assert code == 0 and "has not joined" in out
