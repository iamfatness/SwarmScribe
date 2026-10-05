"""Start-up: a stop is seen at once (signals are installed before anything slow), the cheap
checks come before the model load, the start-up model is a setting, an http leader is refused
unless the switch is on, and a link the follower's own settings refuse releases the job."""

import io
import json
import signal
import threading
import time

import httpx
import pytest
from follower_testkit import (
    BASE,
    CPU,
    JOIN_TOKEN,
    FakeEngine,
    FakeLeader,
    make_agent,
    make_settings,
)
from pydantic import ValidationError
from swarmscribe_follower import main as cli
from swarmscribe_follower.agent import Agent
from swarmscribe_follower.config import Settings
from swarmscribe_follower.credentials import CredentialStore
from swarmscribe_follower.device import Probe
from swarmscribe_follower.errors import FollowerExit
from swarmscribe_follower.leader import LeaderClient
from swarmscribe_follower.models import ModelHost
from swarmscribe_follower.scratch import Scratch
from swarmscribe_follower.transfer import Links


@pytest.fixture
def leader():
    return FakeLeader()


@pytest.fixture
def engine():
    return FakeEngine()


def raise_stop():
    signal.raise_signal(signal.SIGINT)


class Helper(threading.Thread):
    """Runs steps on a helper thread while the test's own (the main) thread runs
    `run_supervised`; whatever goes wrong in a step is re-raised by `finish`."""

    def __init__(self, *steps):
        super().__init__(name="helper", daemon=True)
        self.steps, self.error = steps, None

    def run(self):
        try:
            for step in self.steps:
                step()
        except BaseException as error:  # noqa: BLE001 - handed to the test thread
            self.error = error

    def finish(self):
        self.join(20)
        assert not self.is_alive(), "the helper did not finish"
        if self.error is not None:
            raise self.error


def until(condition, what):
    deadline = time.monotonic() + 10
    while not condition():
        assert time.monotonic() < deadline, f"{what} never happened"
        time.sleep(0.005)


def agent_with(tmp_path, transport, engine, factory=None, **overrides):
    settings = make_settings(tmp_path, **overrides)
    return Agent(
        settings,
        client=LeaderClient(BASE, transport=transport),
        links=Links(transport=transport),
        models=ModelHost("cpu", factory=factory or engine),
        scratch=Scratch(settings.scratch, settings.state_dir),
        store=CredentialStore(settings.credential_file),
        probe=Probe(CPU),
        heartbeat_interval=0.01,
        parked_poll_seconds=0.01,
    )


# --- I1: a stop during start-up ---------------------------------------------------------


def test_a_stop_while_registration_retries_against_a_dead_leader_exits_within_a_bound(
    tmp_path, engine
):
    attempts = []

    def dead(request):
        attempts.append(request.url.path)
        raise httpx.ConnectError("the leader is down")

    agent = agent_with(tmp_path, httpx.MockTransport(dead), engine)
    helper = Helper(lambda: until(lambda: attempts, "a registration attempt"), raise_stop)
    helper.start()
    began = time.monotonic()
    with pytest.raises(FollowerExit) as stopped:
        agent.run_supervised(poll=0.02)
    helper.finish()
    assert stopped.value.code == 0
    assert time.monotonic() - began < 5
    assert len(attempts) >= 1
    # Nothing is left holding the state folder, and the model was let go.
    cli.hold_state_lock(tmp_path / "state").close()
    assert engine.closed == 1


def test_a_stop_during_a_slow_model_load_exits_right_after_the_load(tmp_path, leader, engine):
    started, release = threading.Event(), threading.Event()
    stopped_at = {}

    def slow(settings):
        started.set()
        assert release.wait(10)
        return engine(settings)

    agent = agent_with(tmp_path, leader.transport, engine, factory=slow)

    def stop_then_wait_for_it_to_register():
        assert started.wait(10)
        raise_stop()
        until(agent._stopping.is_set, "the stop to be seen")
        stopped_at["seen"] = time.monotonic()
        release.set()

    helper = Helper(stop_then_wait_for_it_to_register)
    helper.start()
    with pytest.raises(FollowerExit) as stopped:
        agent.run_supervised(poll=0.02)
    helper.finish()
    assert stopped.value.code == 0
    assert time.monotonic() - stopped_at["seen"] < 3  # right after the load, not later
    assert leader.registrations == 0 and leader.count("claim") == 0  # nothing registered
    assert engine.closed == 1  # the model that did load was let go


def test_the_handlers_are_installed_before_start_up_begins(tmp_path, leader, engine):
    seen = {}

    def loading(settings):
        seen["handler"] = signal.getsignal(signal.SIGINT)
        return engine(settings)

    agent = agent_with(tmp_path, leader.transport, engine, factory=loading)
    helper = Helper(lambda: until(lambda: leader.count("claim") >= 1, "a claim"), raise_stop)
    helper.start()
    assert agent.run_supervised(poll=0.02) == 0
    helper.finish()
    assert getattr(seen["handler"], "__self__", None).__class__.__name__ == "StopSignals"


# --- I2b: cheap checks first --------------------------------------------------------------


def test_no_credential_and_no_token_exits_4_without_loading_a_model(tmp_path, leader, engine):
    agent = make_agent(tmp_path, leader, engine, join_token=None)
    with pytest.raises(FollowerExit) as refused:
        agent.prepare()
    assert refused.value.code == 4
    assert engine.loads == [] and leader.registrations == 0


def test_an_unreadable_token_file_exits_2_without_loading_a_model(tmp_path, leader, engine):
    agent = make_agent(
        tmp_path, leader, engine, join_token=None, join_token_file=tmp_path / "missing.token"
    )
    with pytest.raises(FollowerExit) as refused:
        agent.prepare()
    assert refused.value.code == 2 and engine.loads == []


def test_a_stored_credential_is_enough_to_go_on_to_the_model(tmp_path, leader, engine):
    first = make_agent(tmp_path, leader, engine)
    first.prepare()
    first.close()
    again = make_agent(tmp_path, leader, FakeEngine(), join_token=None)
    again.prepare()  # no token, but a credential for this leader and device
    again.close()
    assert leader.registrations == 1


# --- I2: the start-up model ---------------------------------------------------------------


def test_the_startup_model_defaults_to_the_device_default(tmp_path, leader, engine):
    agent = make_agent(tmp_path, leader, engine)
    agent.prepare()
    agent.close()
    assert engine.loads[0][0] == "distil-large-v3"


def test_the_startup_model_setting_is_what_is_loaded_first(tmp_path, leader, engine):
    agent = make_agent(tmp_path, leader, engine, startup_model="tiny.en")
    agent.prepare()
    agent.close()
    assert engine.loads[0][:2] == ("tiny.en", "int8")


@pytest.mark.parametrize("name", ["../x", "C:\\models\\x", "a b", "x" * 101, "/abs/path"])
def test_a_startup_model_must_be_a_plain_name(name):
    with pytest.raises(ValidationError):
        Settings(leader_url=BASE, startup_model=name)


def test_a_startup_model_must_be_among_the_allowed_models_when_those_are_set():
    with pytest.raises(ValidationError) as refused:
        Settings(leader_url=BASE, startup_model="tiny.en", allowed_models="large-v3")
    assert "ALLOWED_MODELS" in str(refused.value)
    ok = Settings(leader_url=BASE, startup_model="tiny.en", allowed_models="tiny.en,large-v3")
    assert ok.startup_model == "tiny.en"
    assert Settings(leader_url=BASE, startup_model="owner/name").startup_model == "owner/name"


def test_a_blank_startup_model_is_unset(monkeypatch):
    monkeypatch.setenv("SWARMSCRIBE_FOLLOWER_STARTUP_MODEL", "  ")
    monkeypatch.setenv("SWARMSCRIBE_LEADER_URL", BASE)
    assert Settings().startup_model is None


def test_offline_with_the_startup_model_missing_is_exit_3_naming_the_setting(
    tmp_path, leader, engine
):
    engine.unavailable = {"tiny.en"}
    agent = make_agent(tmp_path, leader, engine, startup_model="tiny.en", offline=True)
    with pytest.raises(FollowerExit) as refused:
        agent.prepare()
    assert refused.value.code == 3
    assert "SWARMSCRIBE_FOLLOWER_STARTUP_MODEL" in refused.value.reason
    assert "SWARMSCRIBE_FOLLOWER_OFFLINE=1" in refused.value.reason
    assert leader.registrations == 0


def test_doctor_loads_the_startup_model(tmp_path, leader, engine, monkeypatch):
    monkeypatch.setattr(cli, "probe", lambda preference: Probe(CPU))
    monkeypatch.setenv("SWARMSCRIBE_LEADER_URL", BASE)
    monkeypatch.setenv("SWARMSCRIBE_FOLLOWER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SWARMSCRIBE_FOLLOWER_STARTUP_MODEL", "tiny.en")
    out = io.StringIO()
    code = cli.command_doctor(
        Settings(),
        out,
        host=lambda device, **kw: ModelHost(device, factory=engine, **kw),
        client=lambda url, **kw: LeaderClient(url, transport=leader.transport),
    )
    assert code == 0 and "model: tiny.en (int8) loaded and ran" in out.getvalue()


# --- I3: an http leader, and a link the settings refuse -------------------------------------


@pytest.fixture
def plain_http_environment(monkeypatch, tmp_path):
    monkeypatch.delenv("SWARMSCRIBE_FOLLOWER_ALLOW_HTTP", raising=False)
    monkeypatch.setenv("SWARMSCRIBE_LEADER_URL", "http://localhost:8080")
    monkeypatch.setenv("SWARMSCRIBE_FOLLOWER_STATE_DIR", str(tmp_path / "state"))


def test_doctor_says_plainly_that_an_http_leader_is_not_ready(plain_http_environment):
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(["doctor"], out=out, err=err)
    text = out.getvalue()
    assert code == 2
    assert "result: NOT READY (exit 2)" in text
    assert "must be https" in text and "SWARMSCRIBE_FOLLOWER_ALLOW_HTTP=1" in text
    assert err.getvalue() == ""


def test_run_and_join_refuse_an_http_leader_without_the_switch(plain_http_environment):
    for command in ("run", "join"):
        err = io.StringIO()
        code = cli.main([command], out=io.StringIO(), err=err)
        assert code == 2 and "ALLOW_HTTP" in err.getvalue()


def test_a_link_refused_for_its_scheme_releases_the_job_and_exits_2(tmp_path, leader, engine):
    job_id = leader.add_job()
    real_issue = leader._issue

    def plain_links(job):
        issued = real_issue(job)
        issued["download_url"]["url"] = issued["download_url"]["url"].replace("https:", "http:")
        for link in issued["upload_urls"].values():
            link["url"] = link["url"].replace("https:", "http:")
        return issued

    leader._issue = plain_links
    agent = make_agent(tmp_path, leader, engine)  # Links without the http switch
    agent.prepare()
    code = agent.serve()
    assert code == 2
    assert leader.failed == [], "a misconfigured follower must never fail a job"
    assert leader.count("release") == 1
    assert leader.jobs[job_id]["state"] == "queued"
    assert "ALLOW_HTTP" in agent.exit_reason


# --- I4: leave never says "not joined" about a credential it could not read ------------------


@pytest.fixture
def state(monkeypatch, tmp_path):
    for name in ("SWARMSCRIBE_LEADER_URL", "SWARMSCRIBE_FOLLOWER_DEVICE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SWARMSCRIBE_FOLLOWER_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "state").mkdir()
    return tmp_path / "state"


def leave():
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(["leave"], out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def test_leave_with_no_credential_file_has_not_joined_and_exits_0(state):
    code, out, _err = leave()
    assert code == 0 and "has not joined" in out


def test_leave_with_a_corrupt_credential_file_exits_2_and_never_says_not_joined(state):
    (state / "credential.json").write_text("{ not json", encoding="utf-8")
    code, out, err = leave()
    assert code == 2
    assert "has not joined" not in out + err
    assert "credential" in err and (state / "credential.json").exists()


def test_leave_with_an_invalid_setting_exits_2_and_never_says_not_joined(
    state, monkeypatch
):
    (state / "credential.json").write_text(
        json.dumps(
            {
                "leader_url": BASE,
                "follower_id": "f1",
                "credential": "c",
                "device": "cpu",
                "heartbeat_interval": 5,
                "lease_seconds": 30,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SWARMSCRIBE_FOLLOWER_DEVICE", "tpu")
    code, out, err = leave()
    assert code == 2 and "has not joined" not in out + err
    assert "device" in err and (state / "credential.json").exists()


def test_join_to_another_leader_while_joined_is_refused(tmp_path, leader, engine, monkeypatch):
    monkeypatch.setenv("SWARMSCRIBE_LEADER_URL", BASE)
    monkeypatch.setenv("SWARMSCRIBE_JOIN_TOKEN", JOIN_TOKEN)
    monkeypatch.setenv("SWARMSCRIBE_FOLLOWER_STATE_DIR", str(tmp_path / "state"))

    def build(settings):
        return make_agent(tmp_path, leader, engine)

    assert cli.main(["join"], build=build, out=io.StringIO(), err=io.StringIO()) == 0
    err = io.StringIO()
    code = cli.main(
        ["join", "--leader", "https://other.test"], build=build, out=io.StringIO(), err=err
    )
    assert code == 2 and "another leader" in err.getvalue()
