"""The follower's life: prepare, register, claim and work until told to stop (follower
spec 5.2, 5.3, 5.5, 5.6).

The signal handlers are installed FIRST: by `entry.run`, before the follower is even
imported, which hands them to `run_supervised()` (called without them, it installs its own).
`run_supervised()` then runs `prepare()` (the state lock, the state folder's trust check, the
scratch folder, the cheap checks, the start-up model, the registration) and `serve()` on a
thread of its own, while the main thread watches the signals. A stop that arrived before
`run_supervised()` was called is acted on before the worker starts: nothing is locked,
loaded or registered. A stop during start-up therefore
ends the process: the registration's retry loop, the scratch retry waits and the checks between
steps all see it. One step cannot be interrupted: a model load or download (and a request in
flight, bounded by its timeout); the stop is noticed the moment it returns, and nothing is
registered after it. A second signal during start-up cannot hurry that load either. As PID 1 in
a container this is what makes `docker stop` end the process (the kernel gives PID 1 no default
action for SIGTERM, so a process that has installed no handler ignores it).

`serve()` is the worker loop and runs on a thread of its own. `stop()` may be called from any
thread EXCEPT a signal handler: it takes locks (the agent's, `Event.set`, `JobControl`'s), and
a second signal can interrupt the first handler on the same thread while it holds one, which
would hang the process for good. A signal handler therefore only appends to a list
(`StopSignals`: one C call, nothing to deadlock on, and a nested handler cannot lose a count
as `n += 1` could); `run_supervised()` polls it from the main thread, in normal context, and
calls `stop(now=count >= 2)`.

POSIX has SIGTERM and SIGINT; Windows has SIGINT (Ctrl+C) and SIGBREAK (Ctrl+Break, and what
a console close or a wrapper such as winsw can send), and no SIGTERM. A Windows service does
not receive a signal at all: its stop arrives as the service control handler's
`SERVICE_CONTROL_STOP`. The F4 service wrapper calls `agent.stop()` from that handler (a normal
thread, so that is fine: `stop` never blocks), reports `SERVICE_STOP_PENDING` with a wait hint
longer than `shutdown_grace_seconds`, and reports `SERVICE_STOPPED` only when `serve()` has
returned.

Stops that can take longer than the default grace (8 s), none of them for ever: a model load
or download cannot be interrupted; a stalled download is noticed after the transfer client's
read timeout; a heartbeat in flight delays the worker's exit by at most 5 s; telling the leader
`fail` or `release` against a silent leader takes up to three tries of the client's timeout. A
platform that kills the follower first costs one counted attempt (spec 6.6).

The credential: a join or pool token is read (from its file, if that is how it is
configured) only inside `register()`, held in a local variable for the one request and
dropped; it is never logged, put in an exception or kept on the agent."""

import logging
import random
import threading
import time
import traceback
from collections.abc import Callable
from typing import BinaryIO

from swarmscribe_engine import DeviceUnavailableError
from swarmscribe_protocol import Capabilities, ClaimResponse

from .config import Settings
from .credentials import CredentialFileError, CredentialStore, Stored
from .device import Probe, cached_models, capabilities
from .errors import (
    EXIT_CONFIGURATION,
    EXIT_OK,
    EXIT_PROTOCOL,
    EXIT_UNAUTHORISED,
    EXIT_UNFIT,
    FollowerExit,
)
from .job import (
    ABANDONED,
    INVALID_JOB_ID,
    MISCONFIGURED,
    REVOKED_FOLLOWER,
    SCRATCH_BROKEN,
    UNFIT,
    UNKNOWN_CREDENTIAL,
    JobResult,
    JobRunner,
)
from .leader import Interrupted, LeaderClient, NoWork, Refused, Transient, retrying
from .lease import SHUTDOWN, JobControl
from .metrics import Metrics
from .models import ModelHost, ModelUnavailable, OutOfMemory
from .scratch import Scratch, ScratchError, ScratchNotOurs, ScratchOutside
from .signals import StopSignals
from .statelock import hold_state_lock
from .transfer import Links

logger = logging.getLogger(__name__)

NO_CREDENTIAL = (
    "this follower has no credential for this leader and no join token; set"
    " SWARMSCRIBE_JOIN_TOKEN or run `swarmscribe-follower join`"
)
EXIT_UNEXPECTED = 1  # a bug: the follower stopped on an error nobody planned for
IDLE_JITTER = 0.2  # an idle follower waits Retry-After plus up to this fraction of it
PARKED_POLL_SECONDS = 60.0  # how often a drained, parked follower asks again
REFUSED_CLAIM_WAIT_SECONDS = 60.0
# The ruling: three failures to use the scratch folder in a row, with a wait of 5 s after the
# first and 15 s after the second; the third ends the follower (exit 2). A failed job and the
# failed prepares after it are counted together: all are failures of the one folder.
SCRATCH_RETRY_WAITS = (5.0, 15.0)
SCRATCH_FAILURE_LIMIT = len(SCRATCH_RETRY_WAITS) + 1
# A stop during the upload or submit phase lets the finished job finish, whatever the grace
# period (spec 5.6, step 3: "Uploading or submitting: finish"). The spec gives no bound for
# that, so the wait is the larger of the grace period and this: upload and submit have a
# 30 s allowance in the estimate, and four times that covers a slow link.
FINISH_CAP_SECONDS = 120.0
POLL_SECONDS = 0.1  # how often the main thread looks at the signal counter
SUPERVISOR_SILENCE_SECONDS = 30.0  # /healthz: the main thread must have ticked this recently
SCRATCH_FATAL = frozenset({ScratchNotOurs.__name__, ScratchOutside.__name__})


class Agent:
    def __init__(
        self,
        settings: Settings,
        *,
        client: LeaderClient,
        links: Links,
        models: ModelHost,
        scratch: Scratch,
        store: CredentialStore,
        probe: Probe,
        heartbeat_interval: float | None = None,
        parked_poll_seconds: float = PARKED_POLL_SECONDS,
        rng: Callable[[], float] = random.random,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        hold_lock: Callable[..., BinaryIO] = hold_state_lock,
        metrics: Metrics | None = None,
    ) -> None:
        self.metrics = metrics or Metrics()
        self.metrics.watch(state=self.state, progress=self.progress)
        self._ticked = time.monotonic()
        self._settings, self._client, self._links = settings, client, links
        self._models, self._scratch, self._store, self._probe = models, scratch, store, probe
        self._interval_override, self._parked_poll = heartbeat_interval, parked_poll_seconds
        self._rng, self._clock, self._sleep, self._hold_lock = rng, clock, sleep, hold_lock
        self._stopping = threading.Event()
        self._lock = threading.RLock()  # re-entrant: see the module docstring
        self._control: JobControl | None = None
        self._timer: threading.Timer | None = None
        self._runner: JobRunner | None = None
        self._state_lock: BinaryIO | None = None
        self._registered_again = False
        self._skip_deregister = False  # revoked, or the leader does not know us: it would 401
        self._scratch_failures = 0
        self._prepared = False
        self.follower_id: str | None = None
        self.drained = False
        self.exit_reason = ""

    # --- what the listener is told -------------------------------------------------------

    def tick(self) -> None:
        """The supervising thread is alive. `run_supervised` calls this every time it goes
        round; anything else that supervises an agent (the F4 Windows service) must too."""
        self._ticked = time.monotonic()

    def health(self) -> tuple[bool, str]:
        """Whether the follower's threads are alive, and a line saying so (follower spec 9).
        Never whether the leader answers. True while the model loads: the supervising thread
        ticks through it."""
        if time.monotonic() - self._ticked > SUPERVISOR_SILENCE_SECONDS:
            return False, "the supervising thread has stopped"
        runner = self._runner
        if runner is not None and runner.keeper_stalled():
            return False, "the lease keeper has stopped"
        return True, "ok"

    def state(self) -> str:
        """One of metrics.STATES."""
        if self._stopping.is_set():
            return "stopping"
        control = self._control
        if self.drained or (control is not None and control.draining):
            return "draining"
        return "working" if control is not None else "idle"

    def progress(self) -> float | None:
        control = self._control
        return control.progress if control is not None else None

    # --- before the loop -----------------------------------------------------------------

    def startup_model(self) -> tuple[str, str]:
        """The model (and compute type) loaded and exercised at start-up: the setting, else
        the device's default."""
        choice = self._probe.choice
        return self._settings.startup_model or choice.model, choice.compute_type

    def prepare(self) -> None:
        """Everything that can fail before the leader hears of this follower, cheapest first:
        the state folder's lock, that the folder can be trusted with a credential, the scratch
        folder, that there is a credential or a token to register with; then the start-up
        model and a real inference with it; then the credential. Raises FollowerExit (EXIT_OK
        when a stop was asked meanwhile); whatever it had taken (the lock, the model) is let
        go again."""
        try:
            self._stopped_during_start()
            if self._state_lock is None:
                self._state_lock = self._hold_lock(self._settings.state_dir)
            self._check_state_folder()
            self._store.clean_stale_temp()
            if not self._prepare_scratch():
                raise FollowerExit(EXIT_OK, "stopped before it started")
            self._scratch_failures = 0
            self._check_can_register()
            self._load_startup_model()
            self.obtain_credential()
            self._prepared = True
        except BaseException:
            self.close()
            raise

    def _stopped_during_start(self) -> None:
        if self._stopping.is_set():
            raise FollowerExit(EXIT_OK, "stopped before it started")

    def _check_state_folder(self) -> None:
        """Fail now, not at the first restart: `load` refuses a credential in a folder that
        is another user's or that others can write to (a bind mount from Windows, a
        Kubernetes `emptyDir`, a root-made folder). Registering there would spend a
        single-use join token on a follower that can never start a second time."""
        try:
            self._store.check_folder()
        except CredentialFileError as error:
            raise FollowerExit(
                EXIT_CONFIGURATION,
                f"{error}. Nothing was registered and no join token was used.",
            ) from None

    def _check_can_register(self) -> None:
        """Fail now, not after a model load: a follower with neither a credential for this
        leader nor a token to register with can do nothing."""
        self._stopped_during_start()
        try:
            stored = self._store.load()
        except CredentialFileError as error:
            raise FollowerExit(EXIT_CONFIGURATION, str(error)) from None
        if self._is_mine(stored):
            return
        try:
            token = self._settings.token()
        except ValueError as error:
            raise FollowerExit(EXIT_CONFIGURATION, str(error)) from None
        if token is None:
            raise FollowerExit(EXIT_UNAUTHORISED, NO_CREDENTIAL)

    def _load_startup_model(self) -> None:
        model, compute_type = self.startup_model()
        try:
            self._models.get(model, compute_type)
        except (ModelUnavailable, OutOfMemory, DeviceUnavailableError) as error:
            offline = " (SWARMSCRIBE_FOLLOWER_OFFLINE=1: it must be in the model cache)"
            raise FollowerExit(
                EXIT_UNFIT,
                f"this machine cannot transcribe with its start-up model {model}"
                f"{offline if self._settings.offline else ''}: {error}. Set"
                " SWARMSCRIBE_FOLLOWER_STARTUP_MODEL to a model this machine holds, or run"
                " `swarmscribe-follower doctor`.",
            ) from None
        # The load cannot be interrupted; this is the first moment a stop can be seen.
        self._stopped_during_start()

    def close(self) -> None:
        """Let go of what `prepare` took: the model and the state folder's lock. `serve`
        does this itself; this is for an agent that was prepared and never served."""
        self._quietly(self._models.close, "closing the model")
        lock, self._state_lock = self._state_lock, None
        if lock is not None:
            self._quietly(lock.close, "releasing the state folder lock")

    def _prepare_scratch(self) -> bool:
        """Prepare the scratch folder. A folder that is not the follower's ends the follower
        at once (exit 2). One that could not be cleaned (a file another program holds) is
        tried again after 5 s and 15 s; the third failure in a row ends it too. False: a stop
        was asked while waiting."""
        while True:
            try:
                self._scratch.prepare()
                return True
            except (ScratchNotOurs, ScratchOutside) as error:
                raise FollowerExit(EXIT_CONFIGURATION, str(error)) from None
            except ScratchError as error:
                if not self._scratch_failed(error):
                    return False

    def _scratch_failed(self, error: ScratchError | str) -> bool:
        """Count a failure to use the scratch folder: exit 2 on the third in a row, else
        wait (interruptibly). False: a stop was asked while waiting."""
        what = error if isinstance(error, str) else type(error).__name__
        self._scratch_failures += 1
        if self._scratch_failures >= SCRATCH_FAILURE_LIMIT:
            raise FollowerExit(
                EXIT_CONFIGURATION,
                f"the scratch folder cannot be used ({what}); close whatever holds files in it",
            )
        logger.warning(
            "the scratch folder cannot be used (%s); trying again", what,
            extra={"follower_id": self.follower_id},
        )
        return not self._stopping.wait(SCRATCH_RETRY_WAITS[self._scratch_failures - 1])

    def obtain_credential(self) -> None:
        """Use the stored credential if it is for this leader and device; otherwise register."""
        try:
            stored = self._store.load()
        except CredentialFileError as error:
            raise FollowerExit(EXIT_CONFIGURATION, str(error)) from None
        if stored is not None and self._is_mine(stored):
            self._use(stored)
        else:
            self.register()

    def _is_mine(self, stored: Stored | None) -> bool:
        return (
            stored is not None
            and stored.leader_url == self._settings.leader_url
            and stored.device == self._probe.choice.device
        )

    def _use(self, stored: Stored) -> None:
        self._client.credential = stored.credential
        self.follower_id = stored.follower_id
        interval = self._interval_override or float(stored.heartbeat_interval)
        # No `lease_seconds` for the keeper: during a leader outage the worker carries on,
        # and the first answer from the leader settles the lease (spec 5.5).
        self._runner = JobRunner(
            self._client,
            self._links,
            self._models,
            self._scratch,
            device=self._probe.choice.device,
            heartbeat_interval=interval,
            clock=self._clock,
            sleep=self._sleep,
            metrics=self.metrics,
        )

    def register(self) -> None:
        """Exchange the join token (or pool token) for a credential and store it."""
        try:
            token = self._settings.token()
        except ValueError as error:
            raise FollowerExit(EXIT_CONFIGURATION, str(error)) from None
        if token is None:
            raise FollowerExit(EXIT_UNAUTHORISED, NO_CREDENTIAL)
        models = cached_models(self._settings.model_dir)
        if self._models.loaded is not None:
            models.append(self._models.loaded[0])
        reported = capabilities(self._probe, self._settings.pool, models)
        try:
            answer = self._exchange(token, reported)
        except Interrupted:
            raise FollowerExit(EXIT_OK, "stopped before registering") from None
        except Refused as refused:
            if refused.code == "protocol_version":
                raise FollowerExit(
                    EXIT_PROTOCOL, f"the leader speaks another protocol: {refused.message}"
                ) from None
            if refused.status == 401:
                raise FollowerExit(
                    EXIT_UNAUTHORISED,
                    "the join token is not valid (unknown, expired, revoked or used up);"
                    " ask an administrator for a new one",
                ) from None
            # A 404 from a wrong URL, a 403 from a proxy: a configuration error. Exit 5
            # tells a supervisor never to restart, and is for a protocol refusal only.
            raise FollowerExit(
                EXIT_CONFIGURATION,
                f"the leader refused the registration ({refused.status} {refused.code});"
                " check SWARMSCRIBE_LEADER_URL",
            ) from None
        finally:
            del token  # the secret lives for the one request and no longer
        stored = Stored(
            leader_url=self._settings.leader_url,
            follower_id=answer.follower_id,
            credential=answer.credential,
            device=self._probe.choice.device,
            heartbeat_interval=answer.heartbeat_interval,
            lease_seconds=answer.lease_seconds,
        )
        try:
            self._store.save(stored)
        except CredentialFileError as error:
            raise FollowerExit(EXIT_CONFIGURATION, str(error)) from None
        self._use(stored)
        logger.info("registered", extra={"event": "registered", "follower_id": self.follower_id})

    def _exchange(self, token: str, reported: Capabilities):
        return retrying(
            lambda: self._client.register(token, reported), pause=self._stopping.wait
        )

    # --- the loop ------------------------------------------------------------------------

    def serve(self) -> int:
        """Claim and work until stopped, drained (and set to exit), revoked or unfit.
        Returns the process's exit code. However it ends, the model is closed, scratch is
        wiped, the state folder's lock is released and the follower has no thread left."""
        code = EXIT_OK
        try:
            self._loop()
        except FollowerExit as stop:
            code, self.exit_reason = stop.code, stop.reason
            log = logger.info if code == EXIT_OK else logger.error
            log("stopping: %s", stop.reason, extra={"follower_id": self.follower_id})
        except Exception as error:
            # A bug. Frames only: an exception's text can hold a path or a URL.
            frames = " <- ".join(
                f"{frame.name}:{frame.lineno}"
                for frame in reversed(traceback.extract_tb(error.__traceback__))
            )
            code, self.exit_reason = EXIT_UNEXPECTED, f"unexpected {type(error).__name__}"
            logger.error("the follower stopped on %s at %s", type(error).__name__, frames)
        finally:
            self._stopping.set()
            self._cancel_timer()
            if not self._skip_deregister:
                self._quietly(self._deregister, "deregistering")
            self._quietly(self._scratch.wipe, "wiping scratch")
            self.close()
        return code

    @staticmethod
    def _quietly(step: Callable[[], object], what: str) -> None:
        """One step of cleaning up. A failure is logged (its class) and never stops the
        steps after it."""
        try:
            step()
        except Exception as error:
            logger.warning("%s failed (%s)", what, type(error).__name__)

    def _loop(self) -> None:
        while not self._stopping.is_set():
            try:
                answer = retrying(self._client.claim, pause=self._stopping.wait)
            except Interrupted:
                return
            except Refused as refused:
                self._claim_refused(refused)
                continue
            if isinstance(answer, NoWork):
                if self._idle(answer):
                    return
                continue
            self.drained = False
            result = self._run(answer)
            self.metrics.job_ended(result.outcome)
            if result.outcome != SCRATCH_BROKEN:
                self._scratch_failures = 0
            self._after(result)

    def _after(self, result: JobResult) -> None:
        if result.outcome == REVOKED_FOLLOWER:
            self._exit_revoked()
        elif result.outcome == UNKNOWN_CREDENTIAL:
            self._register_again()  # wipes scratch first
        elif result.outcome == MISCONFIGURED:
            raise FollowerExit(EXIT_CONFIGURATION, result.detail)
        elif result.outcome == UNFIT:
            raise FollowerExit(EXIT_UNFIT, f"this machine cannot serve its pool: {result.detail}")
        elif result.outcome == SCRATCH_BROKEN:
            if result.detail in SCRATCH_FATAL:
                raise FollowerExit(
                    EXIT_CONFIGURATION, f"the scratch folder is not usable ({result.detail})"
                )
            if self._scratch_failed(result.detail):
                self._prepare_scratch()
        elif result.outcome == ABANDONED and result.detail == INVALID_JOB_ID:
            # A leader that keeps sending these must not be asked in a hot loop.
            self._stopping.wait(REFUSED_CLAIM_WAIT_SECONDS)

    def _idle(self, answer: NoWork) -> bool:
        """Wait before the next claim. True: a stop was asked meanwhile, leave the loop.
        Leaves through FollowerExit when drained for good."""
        if answer.draining:
            if not self.drained:
                self.drained = True
                logger.info(
                    "the leader is draining this follower; it will be given nothing more",
                    extra={"event": "drained", "follower_id": self.follower_id},
                )
            if self._settings.on_drained == "exit":
                raise FollowerExit(EXIT_OK, "drained")
            # Parked: stay up (a service manager would only start it again) and keep asking,
            # slowly, so the leader sees it alive and a revocation is noticed.
            wait = max(answer.retry_after, self._parked_poll)
        else:
            self.drained = False
            wait = answer.retry_after * (1.0 + IDLE_JITTER * self._rng())
        return self._stopping.wait(wait)

    def _claim_refused(self, refused: Refused) -> None:
        if refused.status == 403:
            self._exit_revoked()
        if refused.status == 401:
            self._register_again()
            return
        logger.error(
            "the leader refused a claim (%s %s)", refused.status, refused.code,
            extra={"follower_id": self.follower_id},
        )
        self._stopping.wait(REFUSED_CLAIM_WAIT_SECONDS)

    def _exit_revoked(self) -> None:
        # The credential file is kept on purpose: a restart finds it, is refused again and
        # exits again. Deleting it would let a join token undo the revocation.
        self._skip_deregister = True
        raise FollowerExit(EXIT_UNAUTHORISED, "this follower has been revoked")

    def _register_again(self) -> None:
        """The leader does not know the stored credential (its database was replaced, or
        the registration was given to another machine): register once more, if there is a
        token to do it with."""
        self._skip_deregister = True  # until it is known again, a deregister would be a 401
        # Whatever the job that was running left (the runner removed its folder already) goes
        # before the new registration, not after.
        self._quietly(self._scratch.wipe, "wiping scratch")
        if self._registered_again:
            raise FollowerExit(
                EXIT_UNAUTHORISED, "the leader does not know this follower's credential"
            )
        self._registered_again = True
        logger.warning("the leader does not know this follower's credential; registering again")
        self.register()
        self._skip_deregister = False

    def _run(self, claim: ClaimResponse) -> JobResult:
        runner = self._runner
        if runner is None:
            raise FollowerExit(EXIT_UNEXPECTED, "serve() was called before prepare()")
        control = JobControl()
        with self._lock:
            self._control = control
            if self._stopping.is_set():  # a stop arrived between the claim and here
                control.stop(SHUTDOWN)
        try:
            return runner.run(claim, control)
        finally:
            with self._lock:
                self._control = None
            self._cancel_timer()

    def _cancel_timer(self) -> None:
        with self._lock:
            timer, self._timer = self._timer, None
        if timer is not None:
            timer.cancel()
            timer.join(timeout=5)

    def _deregister(self) -> None:
        if not self._client.credential:
            return
        try:
            self._client.deregister()
        except (Transient, Refused):
            logger.warning("could not deregister; the leader will notice the silence")

    # --- stopping ------------------------------------------------------------------------

    def run_supervised(
        self, *, poll: float = POLL_SECONDS, signals: StopSignals | None = None
    ) -> int:
        """Start up and serve on a worker thread while this (the main) thread watches for
        signals; returns the exit code. The handlers are installed first, so a stop during
        start-up is seen (module docstring): `signals` are handlers the caller installed
        already (`entry.run`, before the imports), and stay the caller's to put back; without
        them they are installed here and put back at the end. A signal that arrived before
        this call stops the follower before the worker starts. The first signal calls
        `stop()`, the second `stop(now=True)`. The handlers only count; every lock is taken
        here, in normal context. A FollowerExit from start-up is raised here, on this thread.
        Must be called on the main thread. However it ends, the worker has ended."""
        previous: dict[int, object] = {}
        if signals is None:
            signals = StopSignals()
            previous = signals.install()
        codes: list[int] = []
        failed: list[Exception] = []

        def work() -> None:
            try:
                if not self._prepared:
                    self.prepare()
            except Exception as error:  # FollowerExit, or a bug: the main thread re-raises it
                failed.append(error)
                return
            codes.append(self.serve())

        worker = threading.Thread(target=work, name="worker")
        seen = signals.count
        if seen:  # a stop that arrived before start-up began: nothing is to be started
            self.stop(now=seen >= 2)
        worker.start()
        try:
            while worker.is_alive():
                worker.join(poll)
                self.tick()
                arrived = signals.count
                if arrived > seen:
                    seen = arrived
                    self.stop(now=arrived >= 2)
        finally:
            worker.join()
            signals.restore(previous)
        if failed:
            raise failed[0]
        return codes[0] if codes else EXIT_UNEXPECTED

    def stop(self, *, now: bool = False) -> None:
        """Stop claiming. A job in progress is finished if its estimated time left fits the
        grace period, or if it is already being uploaded or submitted (then the wait is bounded
        by FINISH_CAP_SECONDS or the grace, whichever is longer); otherwise, and on a second
        call, it is released. From any thread but a signal handler (module docstring)."""
        with self._lock:
            again = self._stopping.is_set()
            self._stopping.set()
            control, runner = self._control, self._runner
            if control is None:
                return
            if now or again or runner is None:
                control.stop(SHUTDOWN)
                return
            grace = self._settings.shutdown_grace_seconds
            remaining = runner.remaining()
            if runner.finishing:
                wait = max(grace, FINISH_CAP_SECONDS)
            elif remaining is not None and 0 < remaining <= grace:
                wait = grace
            else:
                wait = None  # unknown (or 0: the runner has not taken the job up yet)
            if wait is not None:
                logger.info("stopping after the current job (about %d s left)", remaining or 0)
                timer = threading.Timer(wait, control.stop, args=(SHUTDOWN,))
                timer.daemon = True
                self._timer = timer
                timer.start()
            else:
                logger.info("stopping now; the current job is released")
                control.stop(SHUTDOWN)
