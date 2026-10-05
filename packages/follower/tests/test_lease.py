import logging
import threading
import time

import pytest
from swarmscribe_follower.leader import Refused, Transient
from swarmscribe_follower.lease import (
    CANCELLED,
    KEEPER_FAILED,
    LEASE_LOST,
    REFUSED_REQUEST,
    REVOKED,
    SHUTDOWN,
    UNAUTHORISED,
    JobControl,
    JobStopped,
    LeaseKeeper,
)

JOB, LEASE = "job-1", "lease-1"


class Beats:
    """Stands in for the leader client: answers heartbeats from a script, then `continue`."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls = []
        self.arrived = threading.Semaphore(0)

    def heartbeat(self, job_id, lease_id, progress):
        self.calls.append((job_id, lease_id, progress, threading.current_thread().name))
        self.arrived.release()
        outcome = self.script.pop(0) if self.script else "continue"
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def wait_for(self, count):
        for _ in range(count):
            assert self.arrived.acquire(timeout=5), "the keeper stopped heartbeating"


@pytest.fixture
def keeper_of():
    started = []

    def make(client, control, interval=0.01):
        keeper = LeaseKeeper(client, JOB, LEASE, interval, control)
        started.append(keeper)
        keeper.start()
        return keeper

    yield make
    for keeper in started:
        keeper.finish()
        assert not keeper.is_alive()


def test_the_first_reason_to_stop_is_the_one_that_counts():
    control = JobControl()
    control.check()
    assert control.pause(0) is False
    control.stop(CANCELLED)
    control.stop(SHUTDOWN)
    assert control.reason == CANCELLED
    assert control.pause(60) is True  # returns at once
    with pytest.raises(JobStopped) as stopped:
        control.check()
    assert stopped.value.reason == CANCELLED


def test_heartbeats_go_out_from_their_own_thread_with_the_latest_progress(keeper_of):
    client, control = Beats(), JobControl()
    keeper_of(client, control)
    client.wait_for(2)
    control.progress = 0.5
    client.wait_for(2)
    assert client.calls[0] == (JOB, LEASE, None, "lease-keeper")
    assert client.calls[-1][2] == 0.5


def test_heartbeats_continue_while_the_worker_is_blocked(keeper_of):
    """What the engine call looks like to the keeper: a thread that does not come back."""
    client, control = Beats(), JobControl()
    keeper_of(client, control)
    client.wait_for(3)  # this thread is the "engine call": it does nothing meanwhile
    assert len(client.calls) >= 3
    assert {call[3] for call in client.calls} == {"lease-keeper"}


def test_cancel_stops_the_job_and_the_keeper(keeper_of):
    client, control = Beats("continue", "cancel"), JobControl()
    keeper = keeper_of(client, control)
    keeper.join(timeout=5)
    assert (control.reason, len(client.calls), keeper.is_alive()) == (CANCELLED, 2, False)


def test_drain_is_noted_and_the_job_carries_on(keeper_of):
    client, control = Beats("drain"), JobControl()
    keeper_of(client, control)
    client.wait_for(3)
    assert (control.draining, control.reason) == (True, None)


@pytest.mark.parametrize(
    ("status", "code", "reason"),
    [
        (409, "stale_lease", LEASE_LOST),
        (404, "not_found", LEASE_LOST),
        (403, "forbidden", REVOKED),
        (401, "unauthorized", UNAUTHORISED),
        # A request the leader cannot accept is not a lost lease: the two sides disagree
        # about the protocol (spec 6.2), and the job is failed `other`, retryably.
        (422, "invalid_request", REFUSED_REQUEST),
        (400, "bad_request", REFUSED_REQUEST),
        (200, "invalid_answer", REFUSED_REQUEST),
    ],
)
def test_a_refused_heartbeat_stops_the_job_with_the_reason(keeper_of, status, code, reason):
    client, control = Beats(Refused(status, code, "no")), JobControl()
    keeper = keeper_of(client, control)
    keeper.join(timeout=5)
    assert (control.reason, len(client.calls)) == (reason, 1)


def test_a_leader_outage_does_not_stop_the_job_and_heartbeats_resume(keeper_of):
    outage = [Transient(None, None, "ConnectError")] * 3
    client, control = Beats(*outage, "continue"), JobControl()
    keeper = keeper_of(client, control)
    client.wait_for(5)
    assert control.reason is None
    assert keeper.failures == 0  # recovered


def test_retries_during_an_outage_are_never_further_apart_than_the_interval():
    client, control = Beats(*[Transient(503, 60.0, "x")] * 50), JobControl()
    keeper = LeaseKeeper(client, JOB, LEASE, 0.01, control)
    keeper.start()
    try:
        client.wait_for(6)  # with the leader's Retry-After of 60 s this would take minutes
    finally:
        keeper.finish()
    assert control.reason is None


def test_finish_ends_the_keeper_promptly_even_with_a_long_interval():
    keeper = LeaseKeeper(Beats(), JOB, LEASE, 3600, JobControl())
    keeper.start()
    began = time.monotonic()
    keeper.finish()
    assert not keeper.is_alive()
    assert time.monotonic() - began < 2


class Ticking:
    """A monotonic clock that moves `step` seconds every time it is read."""

    def __init__(self, step):
        self.now, self.step = 0.0, step

    def __call__(self):
        self.now += self.step
        return self.now


def keeper_with(client, control, **kwargs):
    keeper = LeaseKeeper(client, JOB, LEASE, 0.01, control, **kwargs)
    keeper.start()
    return keeper


def test_an_outage_that_outlasts_the_lease_stops_the_job():
    client, control = Beats(*[Transient(None, None, "ConnectError")] * 50), JobControl()
    keeper = keeper_with(client, control, lease_seconds=100, clock=Ticking(40))
    keeper.join(timeout=5)
    assert (keeper.is_alive(), control.reason) == (False, LEASE_LOST)
    assert 1 <= len(client.calls) < 10


def test_an_outage_shorter_than_the_lease_does_not_stop_the_job(keeper_of):
    client, control = Beats(*[Transient(None, None, "ConnectError")] * 3), JobControl()
    keeper = LeaseKeeper(client, JOB, LEASE, 0.01, control, lease_seconds=3600)
    keeper.start()
    try:
        client.wait_for(5)
    finally:
        keeper.finish()
    assert (control.reason, keeper.is_alive()) == (None, False)


def test_a_renewal_resets_the_lease_clock():
    # Every second answer fails; each success renews, so 100 s never passes unrenewed
    # even though the clock moves 40 s per reading.
    script = ["continue", Transient(None, None, "x")] * 10
    client, control = Beats(*script), JobControl()
    keeper = keeper_with(client, control, lease_seconds=100, clock=Ticking(40))
    try:
        client.wait_for(10)
    finally:
        keeper.finish()
    assert not keeper.is_alive()
    assert control.reason is None


def test_an_unexpected_error_in_the_keeper_is_logged_by_frames_and_stops_the_job(caplog):
    client, control = Beats(ValueError("secret text of the failure")), JobControl()
    with caplog.at_level(logging.ERROR):
        keeper = keeper_with(client, control)
        keeper.join(timeout=5)
    assert (keeper.is_alive(), control.reason) == (False, KEEPER_FAILED)
    text = " ".join(record.getMessage() for record in caplog.records)
    assert "ValueError" in text and "_run" in text
    assert "secret text" not in text
    assert all(record.exc_info is None for record in caplog.records)


def test_a_stop_from_outside_ends_the_keeper():
    client, control = Beats(), JobControl()
    keeper = keeper_with(client, control)
    client.wait_for(1)
    control.stop(SHUTDOWN)
    keeper.join(timeout=5)
    assert not keeper.is_alive()
    assert control.reason == SHUTDOWN


def test_stop_is_thread_safe_and_the_first_reason_stays():
    control = JobControl()
    start = threading.Barrier(8)
    reasons = [CANCELLED, LEASE_LOST, REVOKED, SHUTDOWN, UNAUTHORISED, KEEPER_FAILED, "a", "b"]

    def stop(reason):
        start.wait(timeout=5)
        control.stop(reason)

    threads = [threading.Thread(target=stop, args=(reason,)) for reason in reasons]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()
    assert control.reason in reasons
    first = control.reason
    control.stop("later")
    assert (control.reason, control.stopped) == (first, True)
    with pytest.raises(JobStopped) as stopped:
        control.check()
    assert stopped.value.reason == first


def test_finish_is_idempotent_and_safe_before_start():
    keeper = LeaseKeeper(Beats(), JOB, LEASE, 3600, JobControl())
    keeper.finish()  # never started
    other = LeaseKeeper(Beats(), JOB, LEASE, 3600, JobControl())
    other.start()
    other.finish()
    other.finish()
    assert not other.is_alive()


def test_a_drain_answer_does_not_end_the_keeper(keeper_of):
    client, control = Beats("drain"), JobControl()
    keeper = keeper_of(client, control)
    client.wait_for(3)
    assert keeper.is_alive()


# --- fix round 1: a shutdown can never be masked; finish() waits once ---------------------


def test_a_shutdown_is_a_hard_stop_whatever_reason_was_recorded_first():
    control = JobControl()
    control.stop(LEASE_LOST)
    assert control.reason == LEASE_LOST and not control.hard_stopped
    assert control.pause_hard(0) is False  # a lost lease is not a hard stop
    control.stop(SHUTDOWN)
    assert control.reason == LEASE_LOST  # the first reason is still the one reported
    assert control.hard_stopped and control.pause_hard(0) is True


def test_a_refused_request_keeps_what_the_leader_said_for_the_report():
    control = JobControl()
    control.stop(REFUSED_REQUEST, "the leader refused a heartbeat: 422 invalid_request")
    control.stop(SHUTDOWN)
    assert control.detail == "the leader refused a heartbeat: 422 invalid_request"


def test_finish_waits_for_a_heartbeat_in_flight_only_once(keeper_of):
    client, control = Beats(), JobControl()
    keeper = keeper_of(client, control)
    client.wait_for(1)
    waits = []
    keeper.join = lambda timeout=None: waits.append(timeout)  # stands in for a stuck heartbeat
    keeper.finish()
    keeper.finish()  # the worker calls it again on its way out: it must not wait again
    assert waits == [5]
    del keeper.join
    keeper.join(timeout=5)
