import json
import logging
import signal
import threading
import time

import httpx
import pytest
from follower_testkit import (
    JOIN_TOKEN,
    LOST,
    POOL_TOKEN,
    FakeEngine,
    FakeLeader,
    error,
)
from follower_testkit import make_agent as _make_agent
from swarmscribe_engine import DeviceUnavailableError
from swarmscribe_follower.agent import FINISH_CAP_SECONDS, Agent, StopSignals
from swarmscribe_follower.errors import FollowerExit
from swarmscribe_follower.scratch import MARKER, ScratchNotOurs, ScratchWipeFailed
from swarmscribe_follower.statelock import hold_state_lock

_built: list[Agent] = []


@pytest.fixture(autouse=True)
def _let_go_of_agents():
    """An agent that was prepared and never served still holds its state folder lock."""
    yield
    for agent in _built:
        agent.close()
    _built.clear()


def make_agent(*args, **kwargs) -> Agent:
    agent = _make_agent(*args, **kwargs)
    _built.append(agent)
    return agent


def assert_left_clean(tmp_path) -> None:
    """What every way of ending leaves: nothing in scratch but its marker, and the state
    folder's lock free for the next follower. (Threads: conftest checks them.)"""
    scratch = tmp_path / "state" / "scratch"
    assert [p.name for p in scratch.iterdir() if p.name != MARKER] == []
    hold_state_lock(tmp_path / "state").close()


@pytest.fixture
def leader():
    return FakeLeader()


@pytest.fixture
def engine():
    return FakeEngine()


class Served:
    """An agent serving on its own thread, as `swarmscribe-follower run` runs it."""

    def __init__(self, agent):
        self.agent = agent
        self.codes = []
        self.thread = threading.Thread(target=lambda: self.codes.append(agent.serve()))
        self.thread.start()

    def code(self, timeout=10):
        self.thread.join(timeout)
        assert not self.thread.is_alive(), "the agent did not stop"
        assert_left_clean(self.agent._settings.state_dir.parent)
        return self.codes[0]


def start(tmp_path, leader, engine, **overrides) -> Served:
    agent = make_agent(tmp_path, leader, engine, **overrides)
    agent.prepare()
    return Served(agent)


def start_and_stop(tmp_path, leader, engine, **overrides) -> None:
    """A first run of the follower that registered, was stopped and has fully ended."""
    served = start(tmp_path, leader, engine, **overrides)
    served.agent.stop()
    assert served.code() == 0


def until(condition, what="the condition"):
    deadline = time.monotonic() + 10
    while not condition():
        assert time.monotonic() < deadline, f"{what} never happened"
        time.sleep(0.005)


def stored_credential(tmp_path) -> dict:
    return json.loads((tmp_path / "state" / "credential.json").read_text(encoding="utf-8"))


# --- start-up and the credential (spec 5.2, 5.3) -----------------------------------------


def test_the_model_is_loaded_before_the_leader_hears_of_the_follower(tmp_path, leader, engine):
    agent = make_agent(tmp_path, leader, engine)
    leader.on["register"] = lambda: seen.append(list(engine.loads))
    seen = []
    agent.prepare()
    assert seen == [[("distil-large-v3", "int8", "cpu")]]
    (reported,) = leader.capabilities
    assert (reported["device"], reported["pool"], reported["models"]) == (
        "cpu",
        "default",
        ["distil-large-v3"],
    )
    assert stored_credential(tmp_path)["credential"] == "credential-SECRET-1"


def test_a_machine_that_cannot_transcribe_exits_3_without_registering(tmp_path, leader, engine):
    engine.load_error = RuntimeError("Library cublas64_12.dll is not found")
    with pytest.raises(FollowerExit) as stop:
        make_agent(tmp_path, leader, engine).prepare()
    assert stop.value.code == 3
    assert "cublas64_12" in stop.value.reason and "doctor" in stop.value.reason
    assert leader.registrations == 0 and leader.kinds == []


def test_a_scratch_folder_that_is_not_the_followers_exits_2(tmp_path, leader, engine):
    (tmp_path / "state" / "scratch").mkdir(parents=True)
    (tmp_path / "state" / "scratch" / "thesis.docx").write_bytes(b"years of work")
    with pytest.raises(FollowerExit) as stop:
        make_agent(tmp_path, leader, engine).prepare()
    assert stop.value.code == 2
    assert (tmp_path / "state" / "scratch" / "thesis.docx").exists()
    assert leader.kinds == []


def test_a_second_start_reuses_the_credential_and_needs_no_token(tmp_path, leader, engine):
    start_and_stop(tmp_path, leader, engine)
    again = make_agent(tmp_path, leader, engine, join_token=None)
    again.prepare()
    served = Served(again)
    until(lambda: leader.count("claim") >= 2, "the second agent's claim")
    again.stop()
    assert served.code() == 0
    assert leader.registrations == 1


@pytest.mark.parametrize(
    ("overrides", "push", "code", "words"),
    [
        ({"join_token": None}, None, 4, "no join token"),
        ({"join_token": "wrong"}, None, 4, "not valid"),
        ({}, error(409, "protocol_version"), 5, "another protocol"),
        ({}, error(422, "invalid_request"), 2, "refused the registration"),
    ],
)
def test_a_registration_that_cannot_succeed_exits_and_is_not_retried(
    tmp_path, leader, engine, overrides, push, code, words
):
    if push is not None:
        leader.push("register", push)
    with pytest.raises(FollowerExit) as stop:
        make_agent(tmp_path, leader, engine, **overrides).prepare()
    assert stop.value.code == code and words in stop.value.reason
    assert leader.count("register") <= 1
    assert JOIN_TOKEN not in stop.value.reason
    assert not (tmp_path / "state" / "credential.json").exists()


def test_a_registration_waits_out_a_leader_that_is_down(tmp_path, leader, engine):
    leader.push("register", httpx.ConnectError("refused"), error(503, "unavailable"))
    agent = make_agent(tmp_path, leader, engine)
    agent._stopping.wait = lambda seconds: False  # do not really wait between retries
    agent.prepare()
    assert (leader.count("register"), leader.registrations) == (3, 1)


def test_a_credential_for_another_leader_or_device_is_not_used(tmp_path, leader, engine):
    start_and_stop(tmp_path, leader, engine, join_token=POOL_TOKEN)
    path = tmp_path / "state" / "credential.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**data, "leader_url": "https://other.test"}), encoding="utf-8")
    if hasattr(path, "chmod"):
        path.chmod(0o600)
    make_agent(tmp_path, leader, engine, join_token=POOL_TOKEN).prepare()
    assert leader.registrations == 2
    assert stored_credential(tmp_path)["leader_url"] == "https://leader.test"


# --- the loop ----------------------------------------------------------------------------


def test_jobs_are_taken_one_after_another_and_a_stop_while_idle_exits_0(tmp_path, leader, engine):
    first, second = leader.add_job(b"one"), leader.add_job(b"two")
    served = start(tmp_path, leader, engine)
    until(lambda: len(leader.submitted) == 2, "both jobs")
    until(lambda: leader.count("claim") >= 4, "idle polling")
    served.agent.stop()
    assert served.code() == 0
    assert [done["job_id"] for done in leader.submitted] == [first, second]
    assert leader.deregistrations == 1
    assert len(engine.loads) == 1  # one model load served both jobs
    assert engine.closed == 1  # and it is closed on the way out
    assert stored_credential(tmp_path)  # the credential outlives a clean stop


def test_an_idle_follower_waits_what_the_leader_says_plus_jitter(tmp_path, leader, engine):
    leader.retry_after = "10"
    agent = make_agent(tmp_path, leader, engine)
    agent.prepare()
    agent._rng = lambda: 0.5
    waits = []

    def wait(seconds):
        waits.append(seconds)
        return len(waits) >= 3  # the third wait is cut short by a stop

    agent._stopping.wait = wait
    assert agent.serve() == 0
    assert waits == [11.0, 11.0, 11.0]


def test_a_leader_outage_while_idle_is_waited_out(tmp_path, leader, engine):
    down = error(503, "unavailable", **{"Retry-After": "0"})
    leader.push("claim", httpx.ConnectError("refused"), down)
    job_id = leader.add_job()
    agent = make_agent(tmp_path, leader, engine)
    agent.prepare()
    agent._rng = lambda: 0.0
    served = Served(agent)
    until(lambda: leader.submitted, "the job after the outage")
    agent.stop()
    assert served.code() == 0 and leader.jobs[job_id]["state"] == "completed"


def test_a_claim_the_leader_refuses_oddly_is_logged_and_asked_again(tmp_path, leader, engine):
    leader.push("claim", error(422, "invalid_request"), httpx.Response(200, json={"job_id": 1}))
    agent = make_agent(tmp_path, leader, engine)
    agent.prepare()
    waits = []

    def wait(seconds):
        waits.append(seconds)
        return len(waits) >= 3

    agent._stopping.wait = wait
    assert agent.serve() == 0
    assert waits[:2] == [60.0, 60.0]


# --- drain (spec 5.5, 12.3) --------------------------------------------------------------


def test_a_drained_idle_follower_exits_cleanly_and_a_restart_does_not_re_register(
    tmp_path, leader, engine
):
    served = start(tmp_path, leader, engine)
    until(lambda: leader.count("claim") >= 2, "idle polling")
    leader.state = "draining"
    assert served.code() == 0  # nobody called stop: the drain ended it
    assert (served.agent.drained, served.agent.exit_reason) == (True, "drained")
    assert leader.deregistrations == 1
    # What a service manager's restart does: the same state folder, the same credential.
    restarted = start(tmp_path, leader, engine)
    assert restarted.code() == 0
    assert leader.registrations == 1
    assert stored_credential(tmp_path)["credential"] == "credential-SECRET-1"


def test_a_drain_that_arrives_mid_job_lets_the_job_finish_first(tmp_path, leader, engine):
    job_id = leader.add_job()
    engine.on_step = lambda fraction: setattr(leader, "state", "draining")
    served = start(tmp_path, leader, engine)
    assert served.code() == 0
    assert leader.jobs[job_id]["state"] == "completed"
    assert (served.agent.drained, served.agent.exit_reason) == (True, "drained")


def test_a_parked_follower_stays_up_takes_nothing_and_resumes_if_the_drain_ends(
    tmp_path, leader, engine
):
    served = start(tmp_path, leader, engine, on_drained="park")
    until(lambda: leader.count("claim") >= 1, "the first claim")
    leader.state = "draining"  # a drain is set on a registered follower, never at registration
    job_id = leader.add_job()
    until(lambda: leader.count("claim") >= 4 and served.agent.drained, "the parked polling")
    assert served.thread.is_alive() and served.agent.drained
    assert leader.jobs[job_id]["state"] == "queued"
    leader.state = "active"
    until(lambda: leader.submitted, "the job after the drain ended")
    assert served.agent.drained is False
    served.agent.stop()
    assert served.code() == 0


# --- revocation and an unknown credential (spec 6.5) -------------------------------------


def test_a_revoked_follower_exits_4_keeps_its_credential_and_never_comes_back(
    tmp_path, leader, engine
):
    served = start(tmp_path, leader, engine)
    until(lambda: leader.count("claim") >= 2, "idle polling")
    leader.state = "revoked"
    assert served.code() == 4
    assert served.agent.exit_reason == "this follower has been revoked"
    assert leader.deregistrations == 0
    assert stored_credential(tmp_path)["credential"] == "credential-SECRET-1"
    for _restart in range(3):  # a service manager that restarts it anyway
        assert start(tmp_path, leader, engine).code() == 4
    assert leader.registrations == 1  # the join token it still holds was never used again


def test_a_follower_revoked_mid_job_exits_4_without_a_call_for_the_job(tmp_path, leader, engine):
    leader.add_job()

    def revoke(fraction):
        if fraction == 0.5:
            leader.state = "revoked"
            until(lambda: served.agent._control.reason is not None, "the keeper to notice")

    engine.on_step = revoke
    served = start(tmp_path, leader, engine)
    assert served.code() == 4
    assert (leader.failed, leader.released, leader.deregistrations) == ([], [], 0)


def test_an_unknown_credential_registers_once_more_then_gives_up(tmp_path, leader, engine):
    served = start(tmp_path, leader, engine, join_token=POOL_TOKEN)
    until(lambda: leader.count("claim") >= 2, "idle polling")
    leader.credentials.clear()  # the leader's database was replaced
    until(lambda: served.agent._client.credential == "credential-SECRET-2", "registering again")
    until(lambda: leader.count("claim") >= 4, "claims with the new credential")
    assert stored_credential(tmp_path)["credential"] == "credential-SECRET-2"
    leader.credentials.clear()
    assert served.code() == 4
    assert leader.registrations == 2


def test_an_unknown_credential_without_a_token_exits_4(tmp_path, leader, engine):
    start_and_stop(tmp_path, leader, engine)
    leader.credentials.clear()
    again = make_agent(tmp_path, leader, engine, join_token=None)
    again.prepare()
    assert again.serve() == 4
    assert "no join token" in again.exit_reason


# --- a machine that cannot serve its pool (D11) ------------------------------------------


def test_a_claim_for_a_model_this_machine_lacks_releases_it_and_exits_3(tmp_path, leader, engine):
    job_id = leader.add_job(model="large-v3", compute_type="float16")
    engine.unavailable = {"large-v3"}
    served = start(tmp_path, leader, engine)
    assert served.code() == 3
    assert "cannot serve its pool" in served.agent.exit_reason
    assert (leader.released, leader.failed) == ([job_id], [])
    assert leader.jobs[job_id]["attempts"] == 0
    assert leader.deregistrations == 1


# --- shutdown (spec 5.6) -----------------------------------------------------------------


def blocked_at(engine, fraction_to_block):
    """Make the fake engine wait at one step until released; returns (reached, release)."""
    reached, release = threading.Event(), threading.Event()

    def step(fraction):
        if fraction == fraction_to_block:
            reached.set()
            assert release.wait(10)

    engine.on_step = step
    return reached, release


def test_a_stop_that_cannot_wait_for_the_job_releases_it(tmp_path, leader, engine):
    job_id = leader.add_job()
    reached, release = blocked_at(engine, 0.5)
    served = start(tmp_path, leader, engine, shutdown_grace_seconds=0)
    assert reached.wait(10)
    served.agent.stop()
    release.set()
    assert served.code() == 0
    assert (leader.released, leader.submitted) == ([job_id], [])
    assert leader.jobs[job_id]["attempts"] == 0
    assert leader.kinds[-1] == "deregister"


def test_a_stop_with_room_for_the_job_lets_it_finish(tmp_path, leader, engine):
    job_id = leader.add_job()
    reached, release = blocked_at(engine, 0.5)
    served = start(tmp_path, leader, engine, shutdown_grace_seconds=3600)
    assert reached.wait(10)
    served.agent.stop()
    release.set()
    assert served.code() == 0
    assert leader.jobs[job_id]["state"] == "completed" and leader.released == []
    assert leader.count("claim") == 1  # nothing more was claimed


def test_a_stop_before_any_progress_is_known_releases(tmp_path, leader, engine):
    job_id = leader.add_job()
    reached, release = blocked_at(engine, 0.25)  # nothing reported yet: no estimate
    served = start(tmp_path, leader, engine, shutdown_grace_seconds=3600)
    assert reached.wait(10)
    served.agent.stop()
    release.set()
    assert served.code() == 0 and leader.released == [job_id]


def test_a_second_stop_releases_a_job_that_was_being_finished(tmp_path, leader, engine):
    job_id = leader.add_job()
    reached, release = blocked_at(engine, 0.75)
    served = start(tmp_path, leader, engine, shutdown_grace_seconds=3600)
    assert reached.wait(10)
    served.agent.stop()
    served.agent.stop()
    release.set()
    assert served.code() == 0 and leader.released == [job_id]


def test_the_grace_running_out_releases_a_job_that_did_not_finish_in_time(
    tmp_path, leader, engine
):
    job_id = leader.add_job()
    reached, release = blocked_at(engine, 0.75)
    served = start(tmp_path, leader, engine, shutdown_grace_seconds=31)
    assert reached.wait(10)
    served.agent._runner._transcribing_since = served.agent._clock()  # estimate: 30 s left
    served.agent._settings.shutdown_grace_seconds = 0.05  # and the grace then runs out
    served.agent.stop()
    until(lambda: served.agent._control.reason == "shutdown", "the grace timer")
    release.set()
    assert served.code() == 0 and leader.released == [job_id]


def test_a_release_that_cannot_be_delivered_does_not_keep_the_follower_up(
    tmp_path, leader, engine
):
    leader.add_job()
    reached, release = blocked_at(engine, 0.5)
    served = start(tmp_path, leader, engine, shutdown_grace_seconds=0)
    assert reached.wait(10)
    leader.push("release", *[httpx.ConnectError("refused")] * 3)
    leader.push("deregister", httpx.ConnectError("refused"))
    served.agent.stop()
    release.set()
    assert served.code() == 0
    assert leader.count("release") == 3 and leader.released == []


def test_stop_never_blocks_and_can_be_called_from_any_thread_at_any_time(
    tmp_path, leader, engine
):
    early = make_agent(tmp_path, leader, engine)
    early.stop()  # before prepare: start-up ends at once, with nothing registered
    with pytest.raises(FollowerExit) as stopped:
        early.prepare()
    assert stopped.value.code == 0 and leader.registrations == 0 and engine.loads == []
    agent = make_agent(tmp_path, leader, engine)
    agent.prepare()
    agent.stop(now=True)
    assert agent.serve() == 0
    assert leader.count("claim") == 0


# =============================================================================================
# Beyond the brief: start-up order, secrecy, bounds, outages, scratch, model switches, exits
# =============================================================================================

CLAIM_SETTINGS = {"model": "distil-large-v3", "compute_type": "int8"}


def odd_claim(leader, job_id: str) -> httpx.Response:
    """A well-formed claim whose job id is whatever is given (the links are a real job's)."""
    real = leader.add_job()
    leader.queue.remove(real)
    leader.jobs[real].update(state="leased", lease="the-lease")
    claim = {
        "job_id": job_id,
        "lease_id": "the-lease",
        **leader._issue(real),
        "settings": CLAIM_SETTINGS,
        "vocabulary": {"version": 0},
        "source_version": "1-1-1",
    }
    return httpx.Response(200, json=claim)


def stop_after(agent, count: int) -> list[float]:
    """Replace the agent's waiting: it records every wait and asks to stop on the count-th."""
    waits: list[float] = []

    def wait(seconds):
        waits.append(seconds)
        return len(waits) >= count

    agent._stopping.wait = wait
    return waits


# --- start-up order (spec 5.2) -------------------------------------------------------------


def test_a_second_follower_on_the_same_state_folder_is_refused_before_anything_else(
    tmp_path, leader, engine
):
    first = make_agent(tmp_path, leader, engine)
    first.prepare()  # holds the lock
    # The second one would also find a scratch folder that is not its own: the lock is
    # taken first, so the refusal is about the lock.
    other = make_agent(tmp_path, leader, engine, scratch_dir=tmp_path / "elsewhere")
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "x").write_text("not the follower's")
    loads, kinds = len(engine.loads), list(leader.kinds)
    with pytest.raises(FollowerExit) as refused:
        other.prepare()
    assert refused.value.code == 2 and "another follower" in refused.value.reason
    assert (len(engine.loads), leader.kinds) == (loads, kinds)  # nothing was loaded or sent
    assert (tmp_path / "elsewhere" / "x").exists()
    first.close()
    make_agent(tmp_path, leader, engine).prepare()  # the lock is free again once it let go


def test_a_failed_start_lets_go_of_the_lock_and_the_model(tmp_path, leader, engine):
    leader.push("register", error(401, "unauthorized"))
    with pytest.raises(FollowerExit):
        make_agent(tmp_path, leader, engine).prepare()
    assert engine.closed == 1  # it had loaded the model before it tried to register
    hold_state_lock(tmp_path / "state").close()


def test_stale_credential_temp_files_are_cleaned_before_the_scratch_is_prepared(
    tmp_path, leader, engine
):
    state = tmp_path / "state"
    state.mkdir()
    stale = [state / "credential.json.123.deadbeef.new", state / "credential.json.9.cafe.new"]
    for path in stale:
        path.write_text("half a credential")
    keep = state / "credential.json.notes"  # not a temp file of ours
    keep.write_text("mine")
    (state / "credential.json.7.dir.new").mkdir()  # a folder of that name is left alone
    make_agent(tmp_path, leader, engine).prepare()
    assert [path.exists() for path in stale] == [False, False]
    assert keep.exists() and (state / "credential.json.7.dir.new").is_dir()
    assert stored_credential(tmp_path)["credential"] == "credential-SECRET-1"


def test_a_stored_credential_is_used_without_reading_or_sending_any_token(
    tmp_path, leader, engine
):
    start_and_stop(tmp_path, leader, engine)
    missing = tmp_path / "no-such-token-file"
    again = make_agent(tmp_path, leader, engine, join_token=None, join_token_file=missing)
    again.prepare()  # would be a configuration error if the file had been read
    served = Served(again)
    until(lambda: leader.count("claim") >= 2, "claims with the stored credential")
    again.stop()
    assert served.code() == 0
    assert leader.count("register") == 1


# --- the token never leaks and is not kept -------------------------------------------------


def test_the_join_token_is_never_logged_and_is_read_from_its_file(
    tmp_path, leader, engine, caplog
):
    token_file = tmp_path / "token"
    token_file.write_text(f"  {JOIN_TOKEN}\n", encoding="utf-8")
    caplog.set_level(logging.DEBUG)
    leader.push("register", httpx.ConnectError("refused"), error(503, "unavailable"))
    agent = make_agent(tmp_path, leader, engine, join_token=None, join_token_file=token_file)
    agent._stopping.wait = lambda seconds: False
    agent.prepare()
    served = Served(agent)
    until(lambda: leader.count("claim") >= 2, "claims")
    agent.stop()
    assert served.code() == 0
    assert leader.registrations == 1
    assert JOIN_TOKEN not in caplog.text and "credential-SECRET" not in caplog.text
    assert JOIN_TOKEN not in repr(vars(agent))  # nothing on the agent holds it
    assert "registered" in caplog.text


@pytest.mark.parametrize("where", ["refused", "protocol", "unreadable"])
def test_no_exit_reason_holds_a_token(tmp_path, leader, engine, where):
    overrides = {}
    if where == "refused":
        overrides["join_token"] = "wrong-" + JOIN_TOKEN
    elif where == "protocol":
        leader.push("register", error(409, "protocol_version"))
    else:
        overrides.update(join_token=None, join_token_file=tmp_path / "gone")
    with pytest.raises(FollowerExit) as stop:
        make_agent(tmp_path, leader, engine, **overrides).prepare()
    assert JOIN_TOKEN not in stop.value.reason and "wrong-" not in stop.value.reason
    assert stop.value.code == (2 if where == "unreadable" else 4 if where == "refused" else 5)


# --- the idle loop: bounds and outages -----------------------------------------------------


@pytest.mark.parametrize(
    ("retry_after", "low", "high"),
    [
        ("0", 0.2, 0.2 * 1.2),  # floor: never a busy loop
        ("10", 10.0, 12.0),
        ("999999", 3600.0, 3600.0 * 1.2),  # a hostile or broken answer cannot park it for days
        ("", 10.0, 12.0),  # no usable Retry-After: the spec's default of 10
    ],
)
def test_the_idle_wait_is_bounded_whatever_the_leader_says(
    tmp_path, leader, engine, retry_after, low, high
):
    leader.retry_after = retry_after
    agent = make_agent(tmp_path, leader, engine)
    agent.prepare()
    waits = stop_after(agent, 40)
    assert agent.serve() == 0
    assert len(waits) == 40 and all(low <= wait <= high for wait in waits)
    assert len({round(wait, 6) for wait in waits}) > 1  # and jittered, not in step
    assert_left_clean(tmp_path)


def test_a_leader_that_stays_down_is_asked_for_ever_with_a_bounded_back_off(
    tmp_path, leader, engine
):
    leader.push("claim", *[httpx.ConnectError("refused")] * 30)
    agent = make_agent(tmp_path, leader, engine)
    agent.prepare()
    waits = stop_after(agent, 30)
    assert agent.serve() == 0  # only the stop ended it: no give-up, no exit code
    assert leader.count("claim") == 30
    assert all(0.2 <= wait <= 60.0 for wait in waits)
    assert_left_clean(tmp_path)


def test_a_stop_ends_even_a_long_idle_wait_at_once(tmp_path, leader, engine):
    leader.retry_after = "3000"
    served = start(tmp_path, leader, engine)
    until(lambda: leader.count("claim") >= 1, "the first claim")
    began = time.monotonic()
    served.agent.stop()
    assert served.code(timeout=3) == 0
    assert time.monotonic() - began < 3
    assert leader.deregistrations == 1


# --- parked: what it does not do -----------------------------------------------------------


def test_a_parked_follower_sends_no_heartbeat_and_deregisters_when_stopped(
    tmp_path, leader, engine
):
    served = start(tmp_path, leader, engine, on_drained="park")
    until(lambda: leader.count("claim") >= 1, "the first claim")
    leader.state = "draining"
    until(lambda: leader.count("claim") >= 4 and served.agent.drained, "parked polling")
    assert leader.count("heartbeat") == 0  # heartbeats belong to a job's lease
    served.agent.stop()
    assert served.code() == 0
    assert leader.deregistrations == 1 and leader.state == "draining"  # a drain is not ended


# --- a credential the leader does not know, in the middle of a job -------------------------


def test_an_unknown_credential_mid_job_stops_the_job_wipes_scratch_and_registers_again(
    tmp_path, leader, engine
):
    job_id = leader.add_job()
    seen = {}

    def forget(fraction):
        if fraction == 0.5:
            seen["scratch"] = list((tmp_path / "state" / "scratch").glob("job-*"))
            leader.credentials.clear()  # the leader's database was replaced
            until(lambda: served.agent._control.reason is not None, "the keeper to notice")

    engine.on_step = forget
    served = start(tmp_path, leader, engine, join_token=POOL_TOKEN)
    until(lambda: leader.registrations == 2, "registering again")
    engine.on_step = None
    until(lambda: leader.count("claim") >= 3, "claims with the new credential")
    served.agent.stop()
    assert served.code() == 0
    assert seen["scratch"]  # the job's folder existed while it ran
    # No call for the job: not fail, release or submit. (The leader's own deregister
    # handling hands back what was still leased, when the follower leaves.)
    assert [leader.count(kind) for kind in ("fail", "release", "submit")] == [0, 0, 0]
    assert leader.jobs[job_id]["state"] == "queued" and leader.submitted == []
    assert stored_credential(tmp_path)["credential"] == "credential-SECRET-2"


def test_an_unknown_credential_mid_job_without_a_token_exits_4_with_scratch_clean(
    tmp_path, leader, engine
):
    start_and_stop(tmp_path, leader, engine)
    leader.add_job()

    def forget(fraction):
        if fraction == 0.5:
            leader.credentials.clear()
            until(lambda: again._control.reason is not None, "the keeper to notice")

    engine.on_step = forget
    again = make_agent(tmp_path, leader, engine, join_token=None)
    again.prepare()
    served = Served(again)
    assert served.code() == 4
    assert "no join token" in again.exit_reason
    assert (leader.failed, leader.released, leader.submitted) == ([], [], [])
    assert leader.deregistrations == 1  # only the first run's: this one is unknown to the leader


# --- claims that must be ignored -----------------------------------------------------------


@pytest.mark.parametrize(
    "bad_id",
    ["../../etc/passwd", "not-a-uuid", "0" * 8, "{12345678-1234-5678-1234-567812345678}", ""],
)
def test_a_claim_whose_job_id_is_not_a_uuid_is_ignored_entirely(tmp_path, leader, engine, bad_id):
    leader.push("claim", odd_claim(leader, bad_id))
    agent = make_agent(tmp_path, leader, engine)
    agent.prepare()
    waits = stop_after(agent, 2)
    kinds_before = len(leader.kinds)
    assert agent.serve() == 0
    asked = leader.kinds[kinds_before:]
    # Nothing but claims (and the final deregister): no download, heartbeat, release or fail.
    assert set(asked) <= {"claim", "deregister"} and engine.transcribed == []
    assert waits[0] == 60.0  # a leader that keeps sending these is not asked in a hot loop
    assert_left_clean(tmp_path)


# --- a claim for another model -------------------------------------------------------------


class OrderedEngine(FakeEngine):
    """Records how many models were closed when each one was loaded."""

    def __init__(self):
        super().__init__()
        self.closed_at_load = []

    def __call__(self, settings):
        self.closed_at_load.append(self.closed)
        return super().__call__(settings)


def test_a_claim_for_another_model_closes_the_old_one_before_loading_the_new(tmp_path, leader):
    engine = OrderedEngine()
    leader.add_job(b"one")
    leader.add_job(b"two", model="large-v3", compute_type="float16")
    served = start(tmp_path, leader, engine)
    until(lambda: len(leader.submitted) == 2, "both jobs")
    served.agent.stop()
    assert served.code() == 0
    assert [load[:2] for load in engine.loads] == [
        ("distil-large-v3", "int8"),
        ("large-v3", "float16"),
    ]
    assert engine.closed_at_load == [0, 1]  # never two models in memory together
    assert engine.closed == 2  # the second is closed on the way out
    assert served.agent._models.loaded is None


def test_a_model_that_fails_to_load_mid_life_leaves_nothing_loaded(tmp_path, leader, engine):
    first = leader.add_job(b"one")
    second = leader.add_job(b"two", model="large-v3", compute_type="float16")
    engine.unavailable = {"large-v3"}
    served = start(tmp_path, leader, engine)
    assert served.code() == 3
    assert leader.jobs[first]["state"] == "completed"
    assert leader.released == [second] and leader.failed == []
    assert served.agent._models.loaded is None and engine.closed == 1


# --- the scratch folder at a job (ruling) --------------------------------------------------


def broken_job_dir(agent, errors):
    """Make the agent's scratch fail at the start of the next jobs with these errors."""
    real = agent._scratch.job_dir
    pending = list(errors)

    def job_dir(job_id):
        if pending:
            raise pending.pop(0)
        return real(job_id)

    agent._scratch.job_dir = job_dir


def test_a_scratch_folder_that_is_not_ours_ends_the_follower_at_once_with_the_job_released(
    tmp_path, leader, engine
):
    job_id = leader.add_job()
    agent = make_agent(tmp_path, leader, engine)
    agent.prepare()
    broken_job_dir(agent, [ScratchNotOurs("not the follower's")])
    waits = stop_after(agent, 99)
    assert agent.serve() == 2
    assert waits == [] and leader.released == [job_id] and leader.failed == []
    assert leader.deregistrations == 1
    assert_left_clean(tmp_path)


def test_a_scratch_wipe_that_fails_is_retried_after_a_back_off_and_the_job_taken_again(
    tmp_path, leader, engine
):
    job_id = leader.add_job()
    agent = make_agent(tmp_path, leader, engine)
    agent.prepare()
    broken_job_dir(agent, [ScratchWipeFailed("a file is open")])
    real_wait = agent._stopping.wait
    waits = []
    agent._stopping.wait = lambda seconds: waits.append(seconds) or real_wait(0.001)
    served = Served(agent)
    until(lambda: leader.submitted, "the job after the scratch recovered")
    agent.stop()
    assert served.code() == 0
    assert waits[0] == 5.0 and leader.jobs[job_id]["state"] == "completed"
    assert leader.released == [job_id]  # the first claim was released, not failed
    assert leader.failed == [] and leader.jobs[job_id]["attempts"] == 1


def test_a_scratch_that_keeps_failing_ends_the_follower_with_exit_2_on_the_third_time(
    tmp_path, leader, engine
):
    job_id = leader.add_job()
    agent = make_agent(tmp_path, leader, engine)
    agent.prepare()
    broken_job_dir(agent, [ScratchWipeFailed("open")] * 3)
    waits = stop_after(agent, 99)
    assert agent.serve() == 2
    assert waits == [5.0, 15.0] and "scratch" in agent.exit_reason
    assert leader.released == [job_id] * 3 and leader.failed == []
    assert_left_clean(tmp_path)


def test_a_scratch_that_cannot_be_cleaned_at_start_is_retried_then_exits_2(
    tmp_path, leader, engine, monkeypatch
):
    agent = make_agent(tmp_path, leader, engine)
    waits = stop_after(agent, 99)

    def prepare():
        raise ScratchWipeFailed("a file is open")

    monkeypatch.setattr(agent._scratch, "prepare", prepare)
    with pytest.raises(FollowerExit) as stop:
        agent.prepare()
    assert stop.value.code == 2 and waits == [5.0, 15.0]
    assert leader.kinds == [] and engine.loads == []
    hold_state_lock(tmp_path / "state").close()


# --- every exit code, and a clean exit from every path -------------------------------------


@pytest.mark.parametrize(
    "load_error",
    [
        RuntimeError("Library cublas64_12.dll is not found"),
        RuntimeError("CUDA failed with error out of memory"),
        MemoryError(),
        DeviceUnavailableError("cuda was requested but no usable GPU was found"),
    ],
    ids=["library", "cuda-oom", "memory", "device"],
)
def test_a_model_or_device_error_at_start_is_exit_3_and_nothing_is_registered(
    tmp_path, leader, engine, load_error
):
    engine.load_error = load_error
    with pytest.raises(FollowerExit) as stop:
        make_agent(tmp_path, leader, engine).prepare()
    assert stop.value.code == 3 and "doctor" in stop.value.reason
    assert leader.kinds == []
    hold_state_lock(tmp_path / "state").close()


def test_a_corrupt_credential_file_is_a_configuration_error(tmp_path, leader, engine):
    state = tmp_path / "state"
    state.mkdir()
    (state / "credential.json").write_text("not json at all")
    with pytest.raises(FollowerExit) as stop:
        make_agent(tmp_path, leader, engine).prepare()
    assert stop.value.code == 2 and leader.kinds == []
    assert (state / "credential.json").read_text() == "not json at all"  # never touched


def test_an_unexpected_error_is_exit_1_and_still_cleans_up(tmp_path, leader, engine, caplog):
    agent = make_agent(tmp_path, leader, engine)
    agent.prepare()

    def broken():
        raise RuntimeError(f"secret detail {JOIN_TOKEN} C:/private/path")

    agent._loop = broken
    assert agent.serve() == 1
    assert "secret detail" not in caplog.text and "private" not in agent.exit_reason
    assert engine.closed == 1 and leader.deregistrations == 1
    assert_left_clean(tmp_path)


def test_a_failure_in_one_clean_up_step_does_not_skip_the_others(tmp_path, leader, engine):
    agent = make_agent(tmp_path, leader, engine)
    agent.prepare()
    agent.stop()

    def refuse():
        raise OSError("the disk is gone")

    agent._scratch.wipe = refuse
    assert agent.serve() == 0
    assert leader.deregistrations == 1 and engine.closed == 1
    hold_state_lock(tmp_path / "state").close()


# --- stopping ------------------------------------------------------------------------------


def test_stop_is_safe_to_call_again_from_inside_itself_as_a_second_signal_would(
    tmp_path, leader, engine
):
    agent = make_agent(tmp_path, leader, engine)
    with agent._lock:  # what the first handler holds when the second one interrupts it
        agent.stop()
        agent.stop(now=True)
    assert agent._stopping.is_set()


def test_a_stop_during_the_grace_wait_leaves_no_timer_behind(tmp_path, leader, engine):
    leader.add_job()
    reached, release = blocked_at(engine, 0.5)
    served = start(tmp_path, leader, engine, shutdown_grace_seconds=3600)
    assert reached.wait(10)
    served.agent.stop()  # starts the grace timer (an hour long)
    assert served.agent._timer is not None
    release.set()
    assert served.code() == 0
    assert served.agent._timer is None  # cancelled and joined: conftest checks the thread


# =============================================================================================
# Fix round 1: signals only count, a shutdown is never masked, upload and submit finish
# =============================================================================================


class Raiser(threading.Thread):
    """Delivers signals from a helper thread when told, while the test's own thread (the main
    thread) runs `run_supervised`. Whatever goes wrong in it is re-raised by `finish`."""

    def __init__(self, steps):
        super().__init__(name="raiser")
        self.steps, self.error = steps, None

    def run(self):
        try:
            for step in self.steps:
                step()
        except BaseException as error:  # noqa: BLE001 - handed to the test thread
            self.error = error

    def finish(self):
        self.join(15)
        assert not self.is_alive(), "the raiser did not finish"
        if self.error is not None:
            raise self.error


def signal_number(name):
    number = getattr(signal, name, None)
    if number is None:
        pytest.skip(f"{name} does not exist on this platform")
    return number


def test_the_signal_handler_only_counts():
    signals = StopSignals()
    assert signals.count == 0
    signals.handler(2, None)
    signals.handler(2, None)
    assert signals.count == 2


def test_run_supervised_puts_the_handlers_back(tmp_path, leader, engine):
    before = {n: signal.getsignal(n) for n in map(signal_number, ("SIGINT",))}
    agent = make_agent(tmp_path, leader, engine)
    agent.prepare()
    agent.stop()
    assert agent.run_supervised(poll=0.02) == 0
    assert {n: signal.getsignal(n) for n in before} == before


@pytest.mark.parametrize("name", StopSignals.NAMES)
def test_a_signal_stops_an_idle_agent_through_the_main_thread(tmp_path, leader, engine, name):
    number = signal_number(name)
    agent = make_agent(tmp_path, leader, engine)
    agent.prepare()
    raiser = Raiser(
        [
            lambda: until(lambda: leader.count("claim") >= 1, "the first claim"),
            lambda: signal.raise_signal(number),
        ]
    )
    raiser.start()
    began = time.monotonic()
    assert agent.run_supervised(poll=0.02) == 0
    raiser.finish()
    assert time.monotonic() - began < 10 and leader.deregistrations == 1
    assert_left_clean(tmp_path)


@pytest.mark.parametrize("name", StopSignals.NAMES)
def test_the_first_signal_finishes_a_job_that_fits_and_the_second_releases_it(
    tmp_path, leader, engine, name
):
    number = signal_number(name)
    job_id = leader.add_job()
    reached, release = blocked_at(engine, 0.5)
    agent = make_agent(tmp_path, leader, engine, shutdown_grace_seconds=3600)
    agent.prepare()

    def second_signal_when_finishing():
        until(lambda: agent._stopping.is_set(), "the first signal to be handled")
        assert agent._control.reason is None  # finishing: not stopped
        signal.raise_signal(number)
        until(lambda: agent._control.reason == "shutdown", "the second signal to be handled")
        release.set()

    raiser = Raiser(
        [
            lambda: assert_true(reached.wait(10)),
            lambda: signal.raise_signal(number),
            second_signal_when_finishing,
        ]
    )
    raiser.start()
    assert agent.run_supervised(poll=0.02) == 0
    raiser.finish()
    assert leader.released == [job_id] and leader.submitted == []
    assert_left_clean(tmp_path)


def assert_true(value):
    assert value


def test_a_second_signal_that_lands_while_stop_is_half_way_still_forces_the_stop(
    tmp_path, leader, engine
):
    """The old design called stop() from the handler, so a signal landing inside it hung the
    process for good. Now stop() runs in normal context and the handler takes no lock."""
    number = signal_number("SIGINT")
    job_id = leader.add_job()
    reached, release = blocked_at(engine, 0.5)
    agent = make_agent(tmp_path, leader, engine, shutdown_grace_seconds=3600)
    agent.prepare()
    real_stop, calls = agent.stop, []

    def stop_with_a_signal_inside(*, now=False):
        calls.append(now)
        if len(calls) == 1:
            signal.raise_signal(number)  # lands here, on this (the main) thread, mid-stop
        real_stop(now=now)

    agent.stop = stop_with_a_signal_inside
    raiser = Raiser(
        [
            lambda: assert_true(reached.wait(10)),
            lambda: signal.raise_signal(number),
            lambda: until(lambda: agent._control.reason == "shutdown", "the forced stop"),
            release.set,
        ]
    )
    raiser.start()
    began = time.monotonic()
    assert agent.run_supervised(poll=0.02) == 0
    raiser.finish()
    assert calls == [False, True]  # the second signal was seen by the loop: stop(now=True)
    assert time.monotonic() - began < 15
    assert leader.released == [job_id]


# --- the upload and submit phases finish on a first stop (spec 5.6, step 3) ---------------


def hold_request(leader, kind):
    reached, release = threading.Event(), threading.Event()

    def hold():
        reached.set()
        assert release.wait(10)

    leader.on[kind] = hold
    return reached, release


@pytest.mark.parametrize("kind", ["upload:txt", "upload:segments_json", "submit"])
def test_a_first_stop_while_uploading_or_submitting_finishes_the_job_whatever_the_grace(
    tmp_path, leader, engine, kind
):
    job_id = leader.add_job()
    reached, release = hold_request(leader, kind)
    served = start(tmp_path, leader, engine, shutdown_grace_seconds=0)
    assert reached.wait(10)
    served.agent.stop()
    assert served.agent._control.reason is None  # not stopped: finishing
    assert served.agent._timer is not None  # bounded by a timer, never for ever
    release.set()
    assert served.code() == 0
    assert leader.jobs[job_id]["state"] == "completed" and leader.released == []
    assert leader.count("claim") == 1 and leader.deregistrations == 1


@pytest.mark.parametrize(
    ("grace", "expected"), [(0, FINISH_CAP_SECONDS), (8, FINISH_CAP_SECONDS), (870, 870.0)]
)
def test_the_wait_for_a_job_that_is_finishing_is_the_cap_or_the_grace_whichever_is_longer(
    tmp_path, leader, engine, monkeypatch, grace, expected
):
    waits = []
    real_timer = threading.Timer
    monkeypatch.setattr(
        threading,
        "Timer",
        lambda wait, function, args: waits.append(wait) or real_timer(wait, function, args),
    )
    leader.add_job()
    reached, release = hold_request(leader, "submit")
    served = start(tmp_path, leader, engine, shutdown_grace_seconds=grace)
    assert reached.wait(10)
    served.agent.stop()
    release.set()
    assert served.code() == 0
    assert waits == [expected]


@pytest.mark.parametrize("kind", ["upload:txt", "submit"])
def test_a_second_signal_during_upload_or_submit_releases_the_job(tmp_path, leader, engine, kind):
    job_id = leader.add_job()
    if kind == "submit":  # the submit that is in flight is answered 503: the retry is cut short
        leader.push("submit", error(503, "unavailable", **{"Retry-After": "3000"}))
    reached, release = hold_request(leader, kind)
    served = start(tmp_path, leader, engine, shutdown_grace_seconds=0)
    assert reached.wait(10)
    served.agent.stop()
    served.agent.stop()
    release.set()
    assert served.code() == 0
    assert leader.released == [job_id] and leader.submitted == []


def test_the_second_signal_ends_a_worker_waiting_deaf_after_the_lease_was_lost(
    tmp_path, leader, engine
):
    """I2 through the agent: the leader completed the job, the answer was lost, the lease is
    'lost', and nobody answers. First stop: finishing (a timer). Second: out within seconds."""
    leader.add_job()
    leader.push("submit", LOST, *[error(503, "unavailable", **{"Retry-After": "3000"})] * 5)
    served = start(tmp_path, leader, engine)
    until(
        lambda: served.agent._control is not None
        and served.agent._control.reason == "lease_lost"
        and leader.count("submit") >= 2,
        "the lease to be lost during the submit retries",
    )
    served.agent.stop()
    assert served.thread.is_alive()
    began = time.monotonic()
    served.agent.stop()
    assert served.code(timeout=5) == 0
    assert time.monotonic() - began < 5
    assert (leader.failed, leader.released) == ([], [])


# --- wipe scratch BEFORE registering again after a 401 (M7) --------------------------------


def stray_in_scratch(tmp_path):
    stray = tmp_path / "state" / "scratch" / "stray-file"
    stray.write_text("left by the job that was running")
    return stray


def test_scratch_is_wiped_before_the_new_registration_after_a_401_mid_job(
    tmp_path, leader, engine
):
    leader.add_job()
    at_register = []
    leader.on["register"] = lambda: at_register.append(
        sorted(p.name for p in (tmp_path / "state" / "scratch").iterdir() if p.name != MARKER)
    )

    def forget(fraction):
        if fraction == 0.5:
            stray_in_scratch(tmp_path)
            leader.credentials.clear()
            until(lambda: served.agent._control.reason is not None, "the keeper to notice")

    engine.on_step = forget
    served = start(tmp_path, leader, engine, join_token=POOL_TOKEN)
    until(lambda: leader.registrations == 2, "registering again")
    served.agent.stop()
    assert served.code() == 0
    assert at_register[-1] == []  # the wipe came first: nothing of the job was left to register


def test_scratch_is_wiped_before_the_new_registration_after_a_401_while_idle(
    tmp_path, leader, engine
):
    served = start(tmp_path, leader, engine, join_token=POOL_TOKEN)
    until(lambda: leader.count("claim") >= 1, "the first claim")
    at_register = []
    leader.on["register"] = lambda: at_register.append(
        sorted(p.name for p in (tmp_path / "state" / "scratch").iterdir() if p.name != MARKER)
    )
    stray_in_scratch(tmp_path)
    leader.credentials.clear()
    until(lambda: leader.registrations == 2, "registering again")
    served.agent.stop()
    assert served.code() == 0
    assert at_register[-1] == []


# --- a lost registration answer spends a single-use token (M11: inherent) -------------------


def test_a_registration_whose_answer_is_lost_cannot_be_retried_with_a_single_use_token(
    tmp_path, leader, engine
):
    leader.push("register", LOST)  # the leader registers us; the answer never arrives
    agent = make_agent(tmp_path, leader, engine)
    agent._stopping.wait = lambda seconds: False
    with pytest.raises(FollowerExit) as stop:
        agent.prepare()
    assert stop.value.code == 4 and leader.registrations == 1  # the retry found it spent
    assert not (tmp_path / "state" / "credential.json").exists()


def test_a_registration_whose_answer_is_lost_is_retried_with_a_pool_token(
    tmp_path, leader, engine
):
    leader.push("register", LOST)
    agent = make_agent(tmp_path, leader, engine, join_token=POOL_TOKEN)
    agent._stopping.wait = lambda seconds: False
    agent.prepare()
    assert leader.registrations == 2  # the lost one's row is not gone: a second row
    assert stored_credential(tmp_path)["credential"] == "credential-SECRET-2"
