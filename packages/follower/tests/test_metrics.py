import pytest
from follower_testkit import JOIN_TOKEN, SPOKEN, FakeEngine, FakeLeader, make_agent
from prometheus_client.parser import text_string_to_metric_families
from swarmscribe_follower.leader import Transient
from swarmscribe_follower.lease import JobControl, LeaseKeeper
from swarmscribe_follower.metrics import STATES, Metrics
from swarmscribe_follower.models import ModelHost

P = "swarmscribe_follower_"


def samples(metrics: Metrics) -> dict[tuple[str, tuple], float]:
    """Every sample as {(name, sorted labels): value}."""
    found = {}
    for family in text_string_to_metric_families(metrics.render().decode()):
        for sample in family.samples:
            found[(sample.name, tuple(sorted(sample.labels.items())))] = sample.value
    return found


def value(metrics: Metrics, name: str, **labels: str) -> float:
    return samples(metrics)[(P + name, tuple(sorted(labels.items())))]


def test_a_new_follower_counts_nothing_and_is_idle():
    metrics = Metrics()
    for name in ("audio_seconds_total", "transcribe_seconds_total", "heartbeat_failures_total",
                 "download_bytes_total", "upload_bytes_total", "model_load_seconds",
                 "job_progress"):
        assert value(metrics, name) == 0.0, name
    assert [value(metrics, "state", state=state) for state in STATES] == [1.0, 0.0, 0.0, 0.0]


def test_two_followers_do_not_share_counts():
    first, second = Metrics(), Metrics()
    first.job_ended("completed")
    assert value(first, "jobs_total", outcome="completed") == 1.0
    assert (P + "jobs_total", (("outcome", "completed"),)) not in samples(second)


def test_the_state_and_the_progress_are_read_when_asked_for():
    metrics, now = Metrics(), {"state": "working", "progress": 0.25}
    metrics.watch(state=lambda: now["state"], progress=lambda: now["progress"])
    assert value(metrics, "state", state="working") == 1.0
    assert value(metrics, "job_progress") == 0.25
    now.update(state="stopping", progress=None)
    assert value(metrics, "state", state="working") == 0.0
    assert value(metrics, "state", state="stopping") == 1.0
    assert value(metrics, "job_progress") == 0.0


def test_a_completed_job_is_counted_with_its_audio_its_time_and_its_bytes(tmp_path):
    leader, engine = FakeLeader(links_min_interval=0), FakeEngine()
    source = b"a recording of some length"
    job_id = leader.add_job(source)
    engine.on_step = lambda fraction: setattr(leader, "state", "draining")
    agent = make_agent(tmp_path, leader, engine)
    assert agent.run_supervised(poll=0.01) == 0
    metrics = agent.metrics
    assert value(metrics, "jobs_total", outcome="completed") == 1.0
    assert value(metrics, "audio_seconds_total") == 3.25  # the fake transcript's duration
    assert value(metrics, "transcribe_seconds_total") >= 0.0
    assert value(metrics, "download_bytes_total") == len(source)
    uploaded = sum(len(body) for body in leader.jobs[job_id]["uploads"].values())
    assert value(metrics, "upload_bytes_total") == uploaded > 0
    assert value(metrics, "state", state="stopping") == 1.0  # it has exited


def test_a_failed_job_is_counted_by_its_outcome(tmp_path):
    leader, engine = FakeLeader(links_min_interval=0), FakeEngine()
    leader.add_job()
    engine.error = RuntimeError("the engine broke")
    engine.on_step = lambda fraction: setattr(leader, "state", "draining")
    agent = make_agent(tmp_path, leader, engine)
    assert agent.run_supervised(poll=0.01) == 0
    assert value(agent.metrics, "jobs_total", outcome="failed") == 1.0
    assert value(agent.metrics, "audio_seconds_total") == 0.0


def test_a_model_load_is_timed():
    seen = []
    host = ModelHost("cpu", factory=FakeEngine(), on_loaded=seen.append)
    host.get("tiny.en", "int8")
    host.get("tiny.en", "int8")  # already loaded: not a load
    assert len(seen) == 1 and seen[0] >= 0.0
    metrics = Metrics()
    metrics.model_loaded(4.5)
    assert value(metrics, "model_load_seconds") == 4.5


def test_a_model_that_fails_to_load_is_not_timed():
    engine, seen = FakeEngine(), []
    engine.load_error = RuntimeError("no such library")
    host = ModelHost("cpu", factory=engine, on_loaded=seen.append)
    with pytest.raises(Exception, match="no such library"):
        host.get("tiny.en", "int8")
    assert seen == []


def test_every_heartbeat_the_leader_does_not_answer_is_counted():
    class Silent:
        def __init__(self):
            self.calls = 0

        def heartbeat(self, job_id, lease_id, progress):
            self.calls += 1
            if self.calls <= 2:
                raise Transient(None, None, "nobody answered")
            control.stop("cancelled")  # the third is answered: end the test
            return "continue"

    metrics, control, client = Metrics(), JobControl(), Silent()
    keeper = LeaseKeeper(
        client, "job-1", "lease-1", 0.01, control, on_failure=metrics.heartbeat_failed
    )
    keeper.start()
    keeper.join(timeout=30)
    keeper.finish()
    assert not keeper.is_alive()
    assert value(metrics, "heartbeat_failures_total") == 2.0


def test_the_metrics_hold_no_secret_and_no_transcript(tmp_path):
    leader, engine = FakeLeader(links_min_interval=0), FakeEngine()
    leader.add_job()
    engine.on_step = lambda fraction: setattr(leader, "state", "draining")
    agent = make_agent(tmp_path, leader, engine)
    assert agent.run_supervised(poll=0.01) == 0
    text = agent.metrics.render().decode()
    for secret in (JOIN_TOKEN, "credential-SECRET", SPOKEN, "leader.test", "/v1/"):
        assert secret not in text
    labels = {key for (_name, pairs) in samples(agent.metrics) for key, _ in pairs}
    assert labels <= {"outcome", "state"}
