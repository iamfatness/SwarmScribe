"""Keeping a job's lease, and stopping the job (follower spec 5.5).

The engine call blocks, so the lease is renewed from a thread of its own that never waits
for the worker. The two meet in a JobControl: the keeper (or a shutdown) asks the job to
stop with a reason, and the worker notices at its next check: between download chunks,
after every transcribed segment, before each upload."""

import logging
import threading
import time
import traceback
from collections.abc import Callable

from .leader import LeaderClient, Refused, Transient

logger = logging.getLogger(__name__)

# Why a job stops. The first reason given is the one that counts.
CANCELLED = "cancelled"  # the leader cancelled the job: call nothing, wipe
LEASE_LOST = "lease_lost"  # the lease is someone else's now: call nothing, wipe
REVOKED = "revoked"  # this follower was revoked: call nothing, wipe, exit
UNAUTHORISED = "unauthorised"  # the leader does not know the credential
KEEPER_FAILED = "keeper_failed"  # the keeper itself broke: the lease may still be ours, so
# the job is released, never kept without a keeper
SHUTDOWN = "shutdown"  # the follower is stopping: release the job
LEASE_GONE = frozenset({CANCELLED, LEASE_LOST, REVOKED, UNAUTHORISED})


class JobStopped(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class JobControl:
    """Shared by the worker, the lease keeper and the agent for one job."""

    def __init__(self) -> None:
        self._stopped = threading.Event()
        self._lock = threading.Lock()
        self.reason: str | None = None
        self.progress: float | None = None
        self.draining = False

    def stop(self, reason: str) -> None:
        with self._lock:
            if self.reason is None:
                self.reason = reason
        self._stopped.set()

    @property
    def stopped(self) -> bool:
        return self._stopped.is_set()

    def check(self) -> None:
        if self._stopped.is_set():
            raise JobStopped(self.reason or SHUTDOWN)

    def pause(self, seconds: float) -> bool:
        """Wait, or less if the job is stopped meanwhile. True means: stopped."""
        return self._stopped.wait(seconds)


class LeaseKeeper(threading.Thread):
    """Heartbeats every `interval` seconds until `finish()`. A failed heartbeat is retried
    after 2, 4, 8 ... seconds, never more than `interval` apart, for as long as it takes:
    the job carries on while the leader is away (master spec 13), until `lease_seconds` of
    local monotonic time have passed since the last renewal: then the lease has certainly
    expired and the job is stopped (LEASE_LOST). Without `lease_seconds` it never expires
    locally. An unexpected error in the loop stops the job (KEEPER_FAILED)."""

    def __init__(
        self,
        client: LeaderClient,
        job_id: str,
        lease_id: str,
        interval: float,
        control: JobControl,
        *,
        lease_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(name="lease-keeper", daemon=True)
        self._client, self._job_id, self._lease_id = client, job_id, lease_id
        self._interval, self._control, self._clock = interval, control, clock
        self._lease_seconds = lease_seconds
        self._done = threading.Event()
        self.last_loop = clock()  # liveness: when the loop last went round
        self.last_renewed = clock()  # the claim renewed it
        self.failures = 0

    def finish(self) -> None:
        self._done.set()
        if self.is_alive() and threading.current_thread() is not self:
            self.join(timeout=5)

    def run(self) -> None:
        try:
            self._run()
        except Exception as exc:
            # Never a silent death: without a keeper the lease runs out while the worker
            # carries on. Frames only: an exception's text can hold a path or a URL.
            trace = reversed(traceback.extract_tb(exc.__traceback__))
            frames = " <- ".join(f"{frame.name}:{frame.lineno}" for frame in trace)
            logger.error(
                "the lease keeper failed (%s at %s); stopping the job",
                type(exc).__name__,
                frames,
                extra={"job_id": self._job_id, "lease_id": self._lease_id},
            )
            self._control.stop(KEEPER_FAILED)

    def _lease_has_certainly_expired(self) -> bool:
        """The leader's expiry is at most `lease_seconds` after the last renewal reached it,
        and that is no later than when its answer reached us. Past that, the job may already
        be someone else's: transcribing on would only waste the work."""
        if self._lease_seconds is None:
            return False
        return self._clock() - self.last_renewed > self._lease_seconds

    def _run(self) -> None:
        extra = {"job_id": self._job_id, "lease_id": self._lease_id}
        delay = self._interval
        while not self._done.wait(delay) and not self._control.stopped:
            self.last_loop = self._clock()
            try:
                directive = self._client.heartbeat(
                    self._job_id, self._lease_id, self._control.progress
                )
            except Transient:
                self.failures += 1
                if self.failures == 1:
                    logger.warning("heartbeat failed; retrying", extra=extra)
                if self._lease_has_certainly_expired():
                    logger.error("the lease has expired without a heartbeat", extra=extra)
                    self._control.stop(LEASE_LOST)
                    return
                delay = min(self._interval, 2.0 ** min(self.failures, 10))
                continue
            except Refused as refused:
                reason = {403: REVOKED, 401: UNAUTHORISED}.get(refused.status, LEASE_LOST)
                logger.warning("heartbeat refused (%s): %s", refused.code, reason, extra=extra)
                self._control.stop(reason)
                return
            if self.failures:
                logger.info("heartbeat recovered after %d failures", self.failures, extra=extra)
            self.failures, delay = 0, self._interval
            self.last_renewed = self._clock()
            if directive == "cancel":
                logger.info("the leader cancelled the job", extra=extra)
                self._control.stop(CANCELLED)
                return
            if directive == "drain" and not self._control.draining:
                logger.info("the leader is draining this follower", extra=extra)
                self._control.draining = True
