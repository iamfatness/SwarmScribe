import http.client
import io
import socket
import threading
import time

import pytest
from follower_testkit import FakeEngine, FakeLeader, make_agent, make_runner
from swarmscribe_follower import main as cli
from swarmscribe_follower.agent import SUPERVISOR_SILENCE_SECONDS
from swarmscribe_follower.config import Settings
from swarmscribe_follower.errors import FollowerExit
from swarmscribe_follower.health import HealthServer
from swarmscribe_follower.job import KEEPER_INTERVALS
from swarmscribe_follower.leader import REQUEST_TIMEOUT_SECONDS
from swarmscribe_follower.metrics import CONTENT_TYPE


@pytest.fixture
def served():
    """A listener on a free loopback port, with a health answer the test can change."""
    answer = {"alive": True, "text": "ok"}
    server = HealthServer(
        ("127.0.0.1", 0),
        healthy=lambda: (answer["alive"], answer["text"]),
        metrics=lambda: b"swarmscribe_follower_jobs_total 0.0\n",
    )
    server.start()
    yield server.port, answer
    server.close()


def drain_at_the_first_claim(leader):
    """The follower registers, is told `drain` by its first claim and exits 0. (A drain is
    set on a registered follower: registering makes it active.)"""
    leader.on["claim"] = lambda: setattr(leader, "state", "draining")


def ask(port, method, path):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request(method, path)
        response = connection.getresponse()
        return response.status, response.read(), {k.lower(): v for k, v in response.getheaders()}
    finally:
        connection.close()


def test_healthz_says_ok_while_the_follower_is_alive(served):
    port, _answer = served
    status, body, headers = ask(port, "GET", "/healthz")
    assert (status, body) == (200, b"ok\n")
    assert headers["cache-control"] == "no-store"
    assert "python" not in headers.get("server", "").lower()


def test_healthz_is_503_and_says_what_stopped(served):
    port, answer = served
    answer.update(alive=False, text="the lease keeper has stopped")
    status, body, _headers = ask(port, "GET", "/healthz?probe=1")
    assert (status, body) == (503, b"the lease keeper has stopped\n")


def test_head_answers_like_get_without_a_body(served):
    port, _answer = served
    status, body, headers = ask(port, "HEAD", "/healthz")
    assert (status, body, headers["content-length"]) == (200, b"", "3")


def test_metrics_are_served_in_the_prometheus_text_format(served):
    port, _answer = served
    status, body, headers = ask(port, "GET", "/metrics")
    assert (status, headers["content-type"]) == (200, CONTENT_TYPE)
    assert body == b"swarmscribe_follower_jobs_total 0.0\n"


@pytest.mark.parametrize("path", ["/", "/healthz/", "/metrics/x", "/v1/jobs/claim", "/../etc"])
def test_nothing_else_is_served(served, path):
    port, _answer = served
    assert ask(port, "GET", path)[0] == 404


def test_a_request_that_is_not_a_read_is_refused(served):
    port, _answer = served
    assert ask(port, "POST", "/healthz")[0] == 501
    assert ask(port, "GET", "/healthz")[0] == 200  # and the listener is still there


def test_a_client_that_sends_nothing_does_not_stop_the_next_one(served):
    port, _answer = served
    silent = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        assert ask(port, "GET", "/healthz")[0] == 200
    finally:
        silent.close()


def test_a_port_that_is_taken_is_a_configuration_error_naming_the_setting():
    taken = socket.socket()
    taken.bind(("127.0.0.1", 0))
    taken.listen(1)
    try:
        server = HealthServer(
            ("127.0.0.1", taken.getsockname()[1]), healthy=lambda: (True, "ok"), metrics=bytes
        )
        with pytest.raises(FollowerExit) as stop:
            server.start()
    finally:
        taken.close()
    assert stop.value.code == 2 and "SWARMSCRIBE_FOLLOWER_HEALTH_ADDR" in stop.value.reason
    server.close()  # closing a listener that never started is harmless


def test_closing_leaves_no_thread_and_frees_the_port():
    server = HealthServer(("127.0.0.1", 0), healthy=lambda: (True, "ok"), metrics=bytes)
    server.start()
    port = server.port
    server.close()
    assert not any(thread.name == "health" for thread in threading.enumerate())
    with pytest.raises(OSError):
        ask(port, "GET", "/healthz")


# --- what the agent answers ------------------------------------------------------------


def test_an_agent_is_healthy_until_its_supervising_thread_goes_silent(tmp_path):
    agent = make_agent(tmp_path, FakeLeader(), FakeEngine())
    assert agent.health() == (True, "ok")
    agent._ticked = time.monotonic() - SUPERVISOR_SILENCE_SECONDS - 1
    assert agent.health() == (False, "the supervising thread has stopped")
    agent.tick()
    assert agent.health() == (True, "ok")


def test_run_supervised_ticks_while_it_supervises(tmp_path):
    leader = FakeLeader()
    drain_at_the_first_claim(leader)
    agent = make_agent(tmp_path, leader, FakeEngine())
    agent._ticked = 0.0
    assert agent.run_supervised(poll=0.01) == 0
    assert agent._ticked > 0.0  # supervision itself ticked, not just the constructor
    assert agent.health() == (True, "ok")


class StalledKeeper:
    def __init__(self, last_loop, alive=True):
        self.last_loop, self._alive = last_loop, alive

    def is_alive(self):
        return self._alive


def test_a_lease_keeper_that_stopped_going_round_is_unhealthy(tmp_path):
    runner, _client = make_runner(tmp_path, FakeLeader(), FakeEngine(), heartbeat_interval=2.0)
    assert runner.keeper_stalled() is False  # no job, no keeper
    allowed = KEEPER_INTERVALS * 2.0 + REQUEST_TIMEOUT_SECONDS
    runner._keeper = StalledKeeper(time.monotonic() - allowed + 5)
    assert runner.keeper_stalled() is False  # a slow leader is not a dead keeper
    runner._keeper = StalledKeeper(time.monotonic() - allowed - 1)
    assert runner.keeper_stalled() is True
    runner._keeper = StalledKeeper(time.monotonic() - allowed - 1, alive=False)
    assert runner.keeper_stalled() is False  # it ended itself and stopped the job


def test_the_agent_reports_a_stalled_keeper(tmp_path):
    leader = FakeLeader()
    agent = make_agent(tmp_path, leader, FakeEngine())
    agent.prepare()
    try:
        agent._runner._keeper = StalledKeeper(time.monotonic() - 3600)
        assert agent.health() == (False, "the lease keeper has stopped")
    finally:
        agent._runner._keeper = None
        agent.close()


# --- the setting and the command line ---------------------------------------------------


@pytest.mark.parametrize(
    ("given", "address"),
    [
        ("127.0.0.1:9108", ("127.0.0.1", 9108)),
        ("0.0.0.0:9108", ("0.0.0.0", 9108)),
        ("[::1]:9108", ("::1", 9108)),
        (" localhost:09108 ", ("localhost", 9108)),
    ],
)
def test_the_listener_address_is_a_host_and_a_port(given, address):
    assert Settings(leader_url="https://l.example.org", health_addr=given).health_address == address


@pytest.mark.parametrize("given", ["9108", ":9108", "localhost", "localhost:0", "h:70000", "h:x"])
def test_a_bad_listener_address_is_refused(given):
    with pytest.raises(ValueError, match="host:port"):
        Settings(leader_url="https://l.example.org", health_addr=given)


def test_no_listener_unless_asked_for_and_blank_means_unset(monkeypatch):
    monkeypatch.delenv("SWARMSCRIBE_FOLLOWER_HEALTH_ADDR", raising=False)
    assert Settings(leader_url="https://l.example.org").health_address is None
    monkeypatch.setenv("SWARMSCRIBE_FOLLOWER_HEALTH_ADDR", "")
    assert Settings(leader_url="https://l.example.org").health_address is None


def test_run_listens_before_the_model_is_loaded_and_stops_listening_at_the_end(
    tmp_path, monkeypatch
):
    leader, engine, events = FakeLeader(), FakeEngine(), []
    drain_at_the_first_claim(leader)

    class Listener:
        def __init__(self, address, *, healthy, metrics):
            events.append(("made", address, healthy()[0], metrics().startswith(b"# HELP")))

        def start(self):
            events.append(("start", len(engine.loads)))

        def close(self):
            events.append(("close", len(engine.loads)))

    monkeypatch.setattr(cli, "HealthServer", Listener)
    for name, value in (
        ("SWARMSCRIBE_LEADER_URL", "https://leader.test"),
        ("SWARMSCRIBE_FOLLOWER_STATE_DIR", str(tmp_path / "state")),
        ("SWARMSCRIBE_FOLLOWER_HEALTH_ADDR", "127.0.0.1:9108"),
    ):
        monkeypatch.setenv(name, value)
    # `signals` is optional
    code = cli.command_run(Settings(), lambda settings: make_agent(tmp_path, leader, engine))
    assert code == 0
    assert events == [("made", ("127.0.0.1", 9108), True, True), ("start", 0), ("close", 1)]


def test_run_opens_no_port_without_the_setting(tmp_path, monkeypatch):
    leader = FakeLeader()
    drain_at_the_first_claim(leader)

    def refuse(*args, **kwargs):
        raise AssertionError("a listener was made without SWARMSCRIBE_FOLLOWER_HEALTH_ADDR")

    monkeypatch.setattr(cli, "HealthServer", refuse)
    monkeypatch.delenv("SWARMSCRIBE_FOLLOWER_HEALTH_ADDR", raising=False)
    monkeypatch.setenv("SWARMSCRIBE_LEADER_URL", "https://leader.test")
    monkeypatch.setenv("SWARMSCRIBE_FOLLOWER_STATE_DIR", str(tmp_path / "state"))
    settings = Settings()
    assert cli.command_run(settings, lambda s: make_agent(tmp_path, leader, FakeEngine())) == 0


def test_a_listener_on_ipv6_loopback_answers():
    server = HealthServer(("::1", 0), healthy=lambda: (True, "ok"), metrics=bytes)
    try:
        server.start()
    except FollowerExit:
        pytest.skip("this machine has no IPv6 loopback")
    try:
        connection = http.client.HTTPConnection("::1", server.port, timeout=5)
        connection.request("GET", "/healthz")
        response = connection.getresponse()
        assert (response.status, response.read()) == (200, b"ok\n")
        connection.close()
    finally:
        server.close()


def test_a_listener_that_cannot_start_ends_run_with_exit_2_and_names_the_setting(
    tmp_path, monkeypatch
):
    taken = socket.socket()
    taken.bind(("127.0.0.1", 0))
    taken.listen(1)
    for name, value in (
        ("SWARMSCRIBE_LEADER_URL", "https://leader.test"),
        ("SWARMSCRIBE_FOLLOWER_STATE_DIR", str(tmp_path / "state")),
        ("SWARMSCRIBE_FOLLOWER_HEALTH_ADDR", f"127.0.0.1:{taken.getsockname()[1]}"),
    ):
        monkeypatch.setenv(name, value)
    err = io.StringIO()
    try:
        code = cli.main(
            ["run"],
            build=lambda settings: make_agent(tmp_path, FakeLeader(), FakeEngine()),
            out=io.StringIO(),
            err=err,
        )
    finally:
        taken.close()
    assert code == 2
    assert "SWARMSCRIBE_FOLLOWER_HEALTH_ADDR" in err.getvalue()
    assert "Traceback" not in err.getvalue()
