"""One job, from the claim to what the leader is told (follower spec 5.4 and 6).

`JobRunner.run` never raises: whatever happens becomes a JobResult, the leader is told what
it needs to be told (submit, fail, release, or nothing when the lease is gone), and the
job's scratch folder is deleted.

Nothing here logs a link, a credential, a path or transcript text: ids and exception classes
only. A failure reason is the exception's class and message, cut to the protocol's limit."""

import errno
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from swarmscribe_engine import (
    Correction,
    Device,
    DeviceUnavailableError,
    TranscribeSettings,
    UndecodableAudioError,
    Vocabulary,
    write_outputs,
)
from swarmscribe_protocol import ClaimResponse, FailureCode, JobLinks, OutputChecksums

from .leader import (
    REQUEST_TIMEOUT_SECONDS,
    Interrupted,
    LeaderClient,
    Refused,
    Transient,
    retrying,
)
from .lease import (
    CANCELLED,
    LEASE_GONE,
    LEASE_LOST,
    REFUSED_REQUEST,
    REVOKED,
    SHUTDOWN,
    UNAUTHORISED,
    JobControl,
    JobStopped,
    LeaseKeeper,
)
from .metrics import Metrics
from .models import ModelHost, ModelUnavailable, OutOfMemory, is_out_of_memory
from .scratch import Scratch, ScratchDiskFull, ScratchError, ScratchWipeFailed
from .transfer import (
    LeaseLost,
    LinkExpired,
    LinkRefusedByPolicy,
    Links,
    OutOfSpace,
    OutputTooLarge,
    SourceChanged,
)

logger = logging.getLogger(__name__)

OUTPUTS = ("txt", "srt", "segments_json")
DOWNLOAD_GIVE_UP_SECONDS = 600.0
UPLOAD_ALLOWANCE_SECONDS = 30.0  # what uploading and submitting add to a shutdown estimate
SUBMIT_ROUNDS = 3
SUBMIT_500_LIMIT = 5
TELL_ATTEMPTS = 3
TELL_WAIT_CAP_SECONDS = 10.0
UPLOAD_AGAIN = frozenset({"outputs_missing", "checksum_mismatch", "outputs_changed"})
KEEPER_INTERVALS = 3  # /healthz: the lease keeper must have gone round within this many

# JobResult.outcome
COMPLETED = "completed"
FAILED = "failed"  # the leader was told `fail`
RELEASED = "released"  # handed back; the attempt is not counted
ABANDONED = "abandoned"  # cancelled, or the lease was lost: nothing to tell
REVOKED_FOLLOWER = "revoked"  # this follower was revoked: it must exit
UNKNOWN_CREDENTIAL = "unauthorised"  # the leader no longer knows this follower
UNFIT = "unfit"  # this machine cannot serve the claim: released, and it must exit
MISCONFIGURED = "misconfigured"  # its own settings refuse the job's links: released, exit 2
SCRATCH_BROKEN = "scratch"  # the scratch folder is not usable or not ours: released, exit 2
INVALID_JOB_ID = "invalid job id"  # JobResult.detail of an ABANDONED claim that is ignored


@dataclass(frozen=True)
class JobResult:
    outcome: str
    detail: str = ""


class _Fail(Exception):
    def __init__(self, code: FailureCode, reason: str, retryable: bool) -> None:
        super().__init__(reason)
        self.code, self.reason, self.retryable = code, reason, retryable


class _Release(Exception):
    """Hand the job back: neither the job nor this machine is at fault."""


def _reason(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"[:2000]


def classify(error: BaseException) -> tuple[FailureCode, bool, str]:
    """The `fail` a job's error means: code, retryable, reason (follower spec 6.1, 6.2)."""
    if isinstance(error, _Fail):
        return error.code, error.retryable, error.reason
    if isinstance(error, UndecodableAudioError):
        return "undecodable", False, _reason(error)
    if isinstance(error, SourceChanged):
        return "source_changed", False, _reason(error)
    if isinstance(error, OutputTooLarge):
        return "other", False, _reason(error)
    if isinstance(error, OutOfMemory | OutOfSpace | ScratchDiskFull) or is_out_of_memory(error):
        return "out_of_resources", True, _reason(error)
    if isinstance(error, OSError) and error.errno in (errno.ENOSPC, errno.EDQUOT):
        return "out_of_resources", True, "OSError: no space left on the scratch disk"
    if isinstance(error, Refused):
        # A 4xx on a job route: the follower and the leader disagree about the protocol.
        return "other", True, f"the leader refused a request: {error.status} {error.code}"
    return "engine_error", True, _reason(error)


def _gone_because_of(refused: Refused) -> str | None:
    """What a refusal by the leader of a job call says about the lease, if anything: 403 the
    follower is revoked, 401 the leader does not know it, 404 or `stale_lease` the job is no
    longer ours."""
    if refused.status == 403:
        return REVOKED
    if refused.status == 401:
        return UNAUTHORISED
    if refused.status == 404 or (refused.status == 409 and refused.code == "stale_lease"):
        return LEASE_LOST
    return None


class JobRunner:
    def __init__(
        self,
        client: LeaderClient,
        links: Links,
        models: ModelHost,
        scratch: Scratch,
        *,
        device: Device,
        heartbeat_interval: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        metrics: Metrics | None = None,
    ) -> None:
        self._client, self._links, self._models, self._scratch = client, links, models, scratch
        self._device, self._interval = device, heartbeat_interval
        self._clock, self._sleep = clock, sleep
        self._metrics = metrics or Metrics()
        self._keeper: LeaseKeeper | None = None
        self._control: JobControl | None = None
        self._phase = "idle"
        self._transcribing_since = 0.0

    # --- for the agent's shutdown decision ------------------------------------------------

    @property
    def finishing(self) -> bool:
        """True while the finished transcript is being uploaded or submitted: a first stop
        lets that finish (spec 5.6, step 3), however short the grace period."""
        return self._control is not None and self._phase in ("upload", "submit")

    def remaining(self) -> float | None:
        """About how many seconds the current job still needs; None when that is unknown
        (follower spec 5.6). 0 when no job is running."""
        control = self._control
        if control is None:
            return 0.0
        if self._phase in ("upload", "submit"):
            return UPLOAD_ALLOWANCE_SECONDS
        progress = control.progress
        if self._phase == "transcribe" and progress is not None and progress >= 0.05:
            elapsed = self._clock() - self._transcribing_since
            return elapsed * (1.0 - progress) / progress + UPLOAD_ALLOWANCE_SECONDS
        return None

    def keeper_stalled(self) -> bool:
        """True when a job is held and its lease keeper has not gone round its loop for
        three heartbeat intervals plus the time one request may take (follower spec 9). A
        leader that does not answer never makes this true: the keeper still goes round."""
        keeper = self._keeper
        if keeper is None or not keeper.is_alive():
            return False  # no job, or the keeper ended itself and stopped the job
        allowed = KEEPER_INTERVALS * self._interval + REQUEST_TIMEOUT_SECONDS
        return time.monotonic() - keeper.last_loop > allowed

    # --- the job -------------------------------------------------------------------------

    def run(self, claim: ClaimResponse, control: JobControl) -> JobResult:
        if not self._is_canonical_uuid(claim.job_id):
            # It names a scratch folder and a URL path: nothing is done with one that is
            # not a UUID, and the lease is left to expire.
            logger.error("the claim's job id is not a UUID; the claim is ignored")
            return JobResult(ABANDONED, INVALID_JOB_ID)
        extra = {"job_id": claim.job_id, "lease_id": claim.lease_id}
        logger.info("job claimed", extra={**extra, "event": "job.claimed"})
        keeper = LeaseKeeper(
            self._client,
            claim.job_id,
            claim.lease_id,
            self._interval,
            control,
            on_failure=self._metrics.heartbeat_failed,
        )
        folder: Path | None = None
        self._control, self._phase, self._keeper = control, "starting", keeper
        try:
            try:
                keeper.start()
                folder = self._scratch.job_dir(claim.job_id)
                self._work(claim, control, folder)
                result = JobResult(COMPLETED)
            except Exception as error:
                # No heartbeat may follow what the leader is told next.
                keeper.finish()
                result = self._settled(claim, control, error)
        finally:
            keeper.finish()
            self._clean_up(folder, extra)
            self._control, self._phase, self._keeper = None, "idle", None
        logger.info(
            "job %s%s",
            result.outcome,
            f" ({result.detail})" if result.detail else "",
            extra={**extra, "event": f"job.{result.outcome}"},
        )
        return result

    @staticmethod
    def _is_canonical_uuid(text: str) -> bool:
        try:
            return str(uuid.UUID(text)) == text
        except ValueError:
            return False

    def _clean_up(self, folder: Path | None, extra: dict[str, str]) -> None:
        """Delete the job's folder. A failure is logged (ids and the exception's class: its
        text names a path) and never changes what the job's outcome was."""
        if folder is None:
            return
        try:
            self._scratch.remove(folder)
        except ScratchWipeFailed:
            logger.warning(
                "the job's scratch folder could not be deleted; it is wiped at the next start",
                extra=extra,
            )
        except Exception as error:
            logger.error("the job's scratch folder was not cleaned: %s", type(error).__name__,
                         extra=extra)

    def _settings(self, claim: ClaimResponse) -> TranscribeSettings:
        wanted = claim.settings
        return TranscribeSettings(
            model=wanted.model,
            compute_type=wanted.compute_type,
            device=self._device,
            temperatures=tuple(wanted.temperatures),
            channel_mode=wanted.channel_mode,
            channel_labels=tuple(wanted.channel_labels),
        )

    @staticmethod
    def _vocabulary(claim: ClaimResponse) -> Vocabulary:
        given = claim.vocabulary
        return Vocabulary(
            version=given.version,
            terms=tuple(given.terms),
            corrections=tuple(Correction(c.heard, c.replacement) for c in given.corrections),
        )

    @staticmethod
    def _storage(call: Callable[[], Any]) -> Any:
        """A transfer through a link. When the storage answers with something that is not a
        known refusal (the leader's own file route says 400 `invalid_key`), the attempt fails:
        it must never be taken for the leader saying the lease is gone."""
        try:
            return call()
        except Refused as refused:
            raise _Fail(
                "other", f"the storage refused a transfer: {refused.status} {refused.code}", True
            ) from None

    def _with_fresh_links(
        self, claim: ClaimResponse, control: JobControl, links: list[JobLinks], step: Callable
    ) -> Any:
        """Run `step(links)`; if a link is refused as expired, ask the leader once for fresh
        ones (waiting out a 429: one set per lease per minute) and run it again. A second
        refusal is left to the caller (the job is released)."""
        try:
            return step(links[0])
        except LinkExpired:
            logger.info("a link has expired; asking for fresh ones", extra={"job_id": claim.job_id})
            links[0] = retrying(
                lambda: self._client.links(claim.job_id, claim.lease_id), pause=control.pause
            )
            return step(links[0])

    def _work(self, claim: ClaimResponse, control: JobControl, folder: Path) -> None:
        control.check()
        settings = self._settings(claim)
        vocabulary = self._vocabulary(claim)
        links = [JobLinks(download_url=claim.download_url, upload_urls=claim.upload_urls)]
        source = folder / "source"  # no extension: the follower never learns the storage key

        self._phase = "download"
        deadline = self._clock() + DOWNLOAD_GIVE_UP_SECONDS

        def download(current: JobLinks) -> str:
            return retrying(
                lambda: self._storage(
                    lambda: self._links.download(current.download_url, source, control.check)
                ),
                pause=control.pause,
                give_up=lambda _error, _failures: self._clock() >= deadline,
            )

        try:
            downloaded = self._with_fresh_links(claim, control, links, download)
        except Transient:
            raise _Release("the recording could not be downloaded for ten minutes") from None
        self._metrics.downloaded(source.stat().st_size)

        self._phase = "model"
        transcriber = self._models.get(settings.model, settings.compute_type)
        control.check()

        self._phase = "transcribe"
        self._transcribing_since = self._clock()

        def on_progress(fraction: float) -> None:
            control.progress = fraction
            control.check()  # raising here is what stops the engine

        transcript = transcriber.transcribe(
            source, vocabulary, settings=settings, progress=on_progress
        )
        self._metrics.transcribed(transcript.duration, self._clock() - self._transcribing_since)
        if transcript.source_checksum != downloaded:
            raise _Fail("engine_error", "the recording changed on disk during the job", True)
        files = write_outputs(transcript, folder)  # all three, or none: never a partial set
        control.check()

        def upload(current: JobLinks) -> dict[str, str]:
            sent = {}
            for name in OUTPUTS:
                control.check()
                link, path = getattr(current.upload_urls, name), getattr(files, name)
                # The digest is of exactly the bytes that were sent.
                sent[name] = retrying(
                    lambda link=link, path=path: self._storage(
                        lambda: self._links.upload(link, path)
                    ),
                    pause=control.pause,
                )
                self._metrics.uploaded(path.stat().st_size)
            return sent

        for _round in range(SUBMIT_ROUNDS):
            self._phase = "upload"
            sent = self._with_fresh_links(claim, control, links, upload)
            checksums = OutputChecksums(source=downloaded, **sent)
            self._phase = "submit"
            answered_500 = 0

            def give_up(error: Transient) -> bool:
                # Only answers of 500 count (spec 6.4): a connection that was refused, a
                # timeout or a 502/503/504 (a leader that is restarting) never add to it.
                nonlocal answered_500
                if error.status == 500:
                    answered_500 += 1
                return answered_500 >= SUBMIT_500_LIMIT

            try:
                retrying(
                    lambda checksums=checksums: self._client.submit(
                        claim.job_id, claim.lease_id, checksums
                    ),
                    pause=lambda seconds: self._pause_for_submit(control, seconds),
                    give_up=lambda error, _failures: give_up(error),
                )
                return
            except Transient:
                raise _Fail("other", "the leader answered 500 to submit five times", True) from None
            except Refused as refused:
                if refused.status == 409 and refused.code == "outputs_inconsistent":
                    raise _Fail(
                        "engine_error", "the leader found the outputs inconsistent", False
                    ) from None
                if refused.status != 409 or refused.code not in UPLOAD_AGAIN:
                    raise
                logger.warning(
                    "submit refused (%s); uploading again", refused.code,
                    extra={"job_id": claim.job_id},
                )
        raise _Fail("other", "the leader did not accept the outputs after three uploads", True)

    def _pause_for_submit(self, control: JobControl, seconds: float) -> bool:
        """The wait between two tries of submit. A heartbeat that finds the lease gone does
        not interrupt it: once the leader has completed the job, a heartbeat is answered
        `stale_lease` too, and the answer to the submit that completed it may have been lost.
        Only submit's own answer says whether the job was accepted (it repeats its `200` to
        the same checksums, and says `stale_lease` to anything else). Any other stop does,
        and so does a shutdown whatever reason was recorded first (`hard_stopped`): nothing
        masks it, so the worker always returns within the wait."""
        if control.hard_stopped:
            return True
        started = self._clock()
        if control.reason == LEASE_LOST:
            return control.pause_hard(seconds)
        if control.pause(seconds) and control.reason == LEASE_LOST:
            # The stop came during this very wait: serve out what is left of it.
            return control.pause_hard(max(0.0, seconds - (self._clock() - started)))
        return control.stopped

    # --- what the leader is told ----------------------------------------------------------

    def _tell(self, call: Callable[[], None], what: str, claim: ClaimResponse) -> str | None:
        """Make one of the calls that end a job, tried a few times with a wait between. If it
        cannot be delivered the lease simply expires, at the cost of one counted attempt.
        Returns why the lease is gone when the leader's answer says so, otherwise None.
        Waits with `sleep`, not the job's control: the control is stopped by now."""
        extra = {"job_id": claim.job_id, "lease_id": claim.lease_id}
        for attempt in range(TELL_ATTEMPTS):
            try:
                call()
                return None
            except Transient as error:
                if attempt + 1 == TELL_ATTEMPTS:
                    break
                wait = error.retry_after if error.retry_after is not None else 2.0**attempt
                self._sleep(min(wait, TELL_WAIT_CAP_SECONDS))
            except Refused as refused:
                logger.warning("%s refused (%s)", what, refused.code, extra=extra)
                return _gone_because_of(refused)
        logger.warning("%s could not be delivered; the lease will expire", what, extra=extra)
        return None

    @staticmethod
    def _lease_reason(control: JobControl, error: BaseException) -> str | None:
        """Why the lease is gone, if it is: from the lease keeper, or from this error."""
        if control.reason in LEASE_GONE:
            return control.reason
        if isinstance(error, JobStopped) and error.reason in LEASE_GONE:
            return error.reason
        if isinstance(error, LeaseLost):
            return LEASE_LOST
        if isinstance(error, Refused):
            return _gone_because_of(error)
        return None

    @staticmethod
    def _abandoned(gone: str) -> JobResult:
        outcome = {
            CANCELLED: ABANDONED,
            LEASE_LOST: ABANDONED,
            REVOKED: REVOKED_FOLLOWER,
            UNAUTHORISED: UNKNOWN_CREDENTIAL,
        }[gone]
        return JobResult(outcome, gone)

    def _settled(self, claim: ClaimResponse, control: JobControl, error: Exception) -> JobResult:
        try:
            return self._settle(claim, control, error)
        except Exception as unexpected:  # the contract: `run` never raises
            logger.error(
                "settling the job failed (%s); its lease will expire",
                type(unexpected).__name__,
                extra={"job_id": claim.job_id, "lease_id": claim.lease_id},
            )
            return JobResult(ABANDONED, "internal error")

    def _settle(self, claim: ClaimResponse, control: JobControl, error: Exception) -> JobResult:
        job, lease = claim.job_id, claim.lease_id
        gone = self._lease_reason(control, error)
        if gone is not None:
            # The leader has already moved the job on (or no longer knows us): `fail` and
            # `release` would be refused, so nothing is called.
            return self._abandoned(gone)

        def release(detail: str, outcome: str = RELEASED) -> JobResult:
            gone_now = self._tell(lambda: self._client.release(job, lease), "release", claim)
            return self._abandoned(gone_now) if gone_now is not None else JobResult(outcome, detail)

        if control.reason == REFUSED_REQUEST:
            # Our heartbeat was refused 400/422: the sides disagree about the protocol, which
            # is the job's `fail` `other` (spec 6.2), not a lost lease.
            error = _Fail("other", control.detail or "the leader refused a heartbeat", True)
        if isinstance(error, JobStopped | Interrupted):
            reason = error.reason if isinstance(error, JobStopped) else control.reason
            return release(reason or SHUTDOWN)  # a shutdown, or a keeper that broke
        if isinstance(error, ModelUnavailable) or (
            self._phase == "model" and isinstance(error, OutOfMemory | DeviceUnavailableError)
        ):
            return release(str(error), UNFIT)
        if isinstance(error, LinkRefusedByPolicy):
            logger.error("a link was refused by this follower's settings; the job is released",
                         extra={"job_id": job})
            return release(str(error), MISCONFIGURED)
        if isinstance(error, ScratchError) and not isinstance(error, ScratchDiskFull):
            logger.error("the scratch folder is unusable: %s", type(error).__name__,
                         extra={"job_id": job})
            return release(type(error).__name__, SCRATCH_BROKEN)
        if isinstance(error, _Release | LinkExpired | Transient):
            return release(str(error) or type(error).__name__)
        code, retryable, reason = classify(error)
        if isinstance(error, Refused) or (isinstance(error, _Fail) and code == "other"):
            logger.error("the leader and the follower disagree: %s", reason, extra={"job_id": job})
        elif code == "engine_error" and not isinstance(error, _Fail):
            logger.error("job failed: %s", type(error).__name__, extra={"job_id": job})
        gone_now = self._tell(
            lambda: self._client.fail(job, lease, code, reason, retryable), "fail", claim
        )
        return self._abandoned(gone_now) if gone_now is not None else JobResult(FAILED, code)
