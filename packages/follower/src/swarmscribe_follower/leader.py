"""The leader's follower API, one method per route (follower spec 5.1).

Each method makes one request and either returns or raises:

- `Transient`: nobody answered, or the leader said to come back (429, 500, 502, 503, 504).
  The caller retries, usually with `retrying`.
- `Refused`: the leader answered and said no (any other status). The caller decides what
  that means for the job or for the follower.

The bearer credential is attached here and nowhere else, so it reaches only the leader's
/v1/followers and /v1/jobs routes; links are fetched by `transfer.Links`, which has none.
Redirects are never followed."""

import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar
from urllib.parse import quote

import httpx
from pydantic import ValidationError
from swarmscribe_protocol import (
    DIRECTIVE_HEADER,
    PROTOCOL_VERSION,
    Capabilities,
    ClaimResponse,
    Directive,
    FailRequest,
    FailureCode,
    HeartbeatRequest,
    HeartbeatResponse,
    JobLinks,
    LinksRequest,
    OutputChecksums,
    RegisterRequest,
    RegisterResponse,
    ReleaseRequest,
    SubmitRequest,
)

from . import FOLLOWER_VERSION

TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})
DEFAULT_NO_WORK_SECONDS = 10.0
Result = TypeVar("Result")


class Transient(Exception):
    """Try again later. `status` is None when nothing answered."""

    def __init__(self, status: int | None, retry_after: float | None, what: str) -> None:
        super().__init__(what)
        self.status = status
        self.retry_after = retry_after


class Refused(Exception):
    """The leader (or a storage service) answered with a refusal."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message


class Interrupted(Exception):
    """A wait between retries was cut short because the caller was asked to stop."""


@dataclass(frozen=True)
class NoWork:
    retry_after: float
    draining: bool


MAX_RETRY_AFTER_SECONDS = 3600.0
MIN_WAIT_SECONDS = 0.2  # a floor: `Retry-After: 0` must not become a busy loop


def retry_after_of(response: httpx.Response) -> float | None:
    """Seconds from a `Retry-After` given in seconds (the only form the leader sends);
    None for a date, junk or nothing. Capped, so a hostile or broken answer cannot park
    the follower for days."""
    value = response.headers.get("retry-after", "").strip()
    if not (value.isascii() and value.isdigit()):
        return None
    return max(MIN_WAIT_SECONDS, min(float(int(value)), MAX_RETRY_AFTER_SECONDS))


def refusal_of(response: httpx.Response) -> Refused:
    """The `{code, message}` of an error answer; something sensible when it has none (a
    proxy's HTML page, a storage service's XML)."""
    code, message = f"http_{response.status_code}", ""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        if isinstance(body.get("code"), str):
            code = body["code"][:100]
        if isinstance(body.get("message"), str):
            message = body["message"][:500]
    return Refused(response.status_code, code, message)


def checked(response: httpx.Response) -> httpx.Response:
    """The response if it is a success; otherwise the exception it means."""
    if response.status_code in TRANSIENT_STATUSES:
        raise Transient(
            response.status_code, retry_after_of(response), f"status {response.status_code}"
        )
    if not 200 <= response.status_code < 300:
        raise refusal_of(response)
    return response


def retrying(
    call: Callable[[], Result],
    *,
    pause: Callable[[float], bool],
    give_up: Callable[[Transient, int], bool] | None = None,
    first: float = 1.0,
    cap: float = 60.0,
    rng: Callable[[], float] = random.random,
) -> Result:
    """Call until it stops raising Transient. Waits the leader's Retry-After when it sent
    one, otherwise a random part of a delay that doubles from `first` to `cap` (full
    jitter, so a fleet does not come back in step). `pause(seconds)` waits and returns True
    if the caller must stop, which raises Interrupted. `give_up(error, failures)` returning
    True re-raises the Transient."""
    delay, failures = first, 0
    while True:
        try:
            return call()
        except Transient as exc:
            failures += 1
            if give_up is not None and give_up(exc, failures):
                raise
            wait = exc.retry_after if exc.retry_after is not None else delay * rng()
            wait = max(MIN_WAIT_SECONDS, wait)
            delay = min(cap, delay * 2)
            if pause(wait):
                raise Interrupted() from None


class LeaderClient:
    def __init__(
        self,
        base_url: str,
        *,
        credential: str | None = None,
        transport: httpx.BaseTransport | None = None,
        verify: Any = True,
        timeout: float = 30.0,
    ) -> None:
        self.credential = credential
        # `trust_env` stays on, so HTTPS_PROXY and the CA environment are honoured (follower
        # spec 5.1). The credential is only ever a header built in `_post`, on a path of
        # this client's own base URL; a proxy sees the same request the leader would.
        # Plain http (the development switch) never goes through a proxy named in the
        # environment: a proxy would see the credential and the join token in clear.
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            trust_env=not base_url.strip().lower().startswith("http://"),
            transport=transport,
            verify=verify,
            timeout=timeout,
            follow_redirects=False,
            headers={"User-Agent": f"swarmscribe-follower/{FOLLOWER_VERSION}"},
        )

    def __repr__(self) -> str:
        return f"LeaderClient(base_url={str(self._http.base_url)!r}, credential=<hidden>)"

    def close(self) -> None:
        self._http.close()

    def _post(self, path: str, body: Any = None, *, authenticated: bool = True) -> httpx.Response:
        headers = {}
        if authenticated:
            if not self.credential:
                raise Refused(401, "unauthorized", "this follower has no credential")
            headers["Authorization"] = f"Bearer {self.credential}"
        json = body.model_dump(mode="json") if body is not None else None
        try:
            response = self._http.post(path, json=json, headers=headers)
        except httpx.InvalidURL:
            raise Refused(0, "invalid_url", "the leader URL cannot be used") from None
        except httpx.HTTPError as exc:
            # The class only: the text of a transport error can hold the URL.
            raise Transient(None, None, type(exc).__name__) from None
        return checked(response)

    @staticmethod
    def _parsed(model: type[Result], response: httpx.Response) -> Result:
        try:
            return model.model_validate_json(response.content)
        except (ValidationError, ValueError):
            raise Refused(
                response.status_code, "invalid_answer", f"not a {model.__name__}"
            ) from None

    def healthy(self) -> bool:
        """Whether the leader answers at all (its /healthz). Changes nothing."""
        try:
            return self._http.get("/healthz").status_code == 200
        except httpx.HTTPError:
            return False

    def register(self, join_token: str, capabilities: Capabilities) -> RegisterResponse:
        body = RegisterRequest(
            join_token=join_token, protocol_version=PROTOCOL_VERSION, capabilities=capabilities
        )
        answer = self._post("/v1/followers/register", body, authenticated=False)
        return self._parsed(RegisterResponse, answer)

    def claim(self) -> ClaimResponse | NoWork:
        response = self._post("/v1/jobs/claim")
        if response.status_code == 204:
            wait = retry_after_of(response)
            return NoWork(
                retry_after=DEFAULT_NO_WORK_SECONDS if wait is None else wait,
                draining=response.headers.get(DIRECTIVE_HEADER, "") == "drain",
            )
        return self._parsed(ClaimResponse, response)

    @staticmethod
    def _job(job_id: str, action: str) -> str:
        # The id comes from the leader and becomes a path segment: it is quoted whole.
        return f"/v1/jobs/{quote(job_id, safe='')}/{action}"

    def heartbeat(self, job_id: str, lease_id: str, progress: float | None) -> Directive:
        body = HeartbeatRequest(lease_id=lease_id, progress=progress)
        answer = self._post(self._job(job_id, "heartbeat"), body)
        return self._parsed(HeartbeatResponse, answer).directive

    def links(self, job_id: str, lease_id: str) -> JobLinks:
        answer = self._post(self._job(job_id, "links"), LinksRequest(lease_id=lease_id))
        return self._parsed(JobLinks, answer)

    def submit(self, job_id: str, lease_id: str, checksums: OutputChecksums) -> None:
        body = SubmitRequest(lease_id=lease_id, checksums=checksums)
        self._post(self._job(job_id, "submit"), body)

    def fail(
        self, job_id: str, lease_id: str, code: FailureCode, reason: str, retryable: bool
    ) -> None:
        body = FailRequest(lease_id=lease_id, code=code, reason=reason[:2000], retryable=retryable)
        self._post(self._job(job_id, "fail"), body)

    def release(self, job_id: str, lease_id: str) -> None:
        self._post(self._job(job_id, "release"), ReleaseRequest(lease_id=lease_id))

    def deregister(self) -> None:
        self._post("/v1/followers/deregister")
