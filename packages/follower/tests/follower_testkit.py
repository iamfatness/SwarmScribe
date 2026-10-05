"""A leader and an engine that are fakes, for the follower's unit tests. conftest.py puts
this folder on sys.path; test files import from here.

FakeLeader speaks the protocol through an httpx transport and behaves like the real leader
(packages/leader api/follower.py, api/files.py, jobs/store.py, jobs/claims.py) wherever the
follower can tell: statuses, codes and headers; leases that links are bound to; link expiry;
fresh links once per lease per interval, then 429; the drain directive; submit verifying the
three outputs' checksums, accepting a repeat of a completed submit, and answering
`stale_lease` otherwise. The source checksum is NOT verified, as in the real leader (it only
records it).
Registration is as strict as the real leader's: the protocol version is checked first; a
join token is single use (`join_max_uses`, default 1) and a registration with it makes a new
follower; a pool token is never used up, and it reuses the row of a gone follower that
holds nothing, which replaces that row's credential (the old one answers 401). Revoking
(`state = "revoked"`) releases the follower's leases at once, so its links stop working
before it hears 403. A bearer credential must be sent as `Bearer <credential>`.
`push` scripts the next answers of one kind of request; `on` hooks run before a
request is handled. Requests are recorded by kind, never by URL."""

import hashlib
import json
import math
import threading
import time
import uuid
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

import httpx
from pydantic import ValidationError
from swarmscribe_engine import (
    DeviceChoice,
    Segment,
    Transcript,
    Word,
)
from swarmscribe_engine.transcriber import sha256_file
from swarmscribe_follower.config import Settings
from swarmscribe_follower.credentials import CredentialStore
from swarmscribe_follower.device import Probe
from swarmscribe_follower.job import JobRunner
from swarmscribe_follower.leader import LeaderClient
from swarmscribe_follower.models import ModelHost
from swarmscribe_follower.scratch import Scratch
from swarmscribe_follower.transfer import Links
from swarmscribe_protocol import (
    DIRECTIVE_HEADER,
    FailRequest,
    HeartbeatRequest,
    LinksRequest,
    RegisterRequest,
    ReleaseRequest,
    SegmentsDocument,
    SubmitRequest,
)

BASE = "https://leader.test"
JOIN_TOKEN = "join-token-SECRET"  # single use, as a real join token is by default
POOL_TOKEN = "pool-token-SECRET"  # never used up; the row of a gone follower is reused
SPOKEN = "Welcome to Ashford."
CPU = DeviceChoice(device="cpu", model="distil-large-v3", compute_type="int8")
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
OUTPUT_NAMES = ("txt", "srt", "segments_json")
NON_RETRYABLE = frozenset({"source_changed", "undecodable"})  # the real leader's rule
SEGMENTS = (
    Segment(
        start=0.0,
        end=1.5,
        text=SPOKEN,
        words=(
            Word(start=0.0, end=0.4, word=" Welcome", probability=0.98),
            Word(start=0.4, end=0.6, word=" to", probability=0.99),
            Word(start=0.6, end=1.5, word=" Ashford.", probability=0.71),
        ),
    ),
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def error(status: int, code: str, **headers: str) -> httpx.Response:
    return httpx.Response(status, json={"code": code, "message": code}, headers=headers)


class LostAnswer:
    """Scripted in `push`: the request is handled for real, then its answer never arrives
    (the connection drops). What a retry of submit has to survive."""


LOST = LostAnswer()


class FakeLeader:
    def __init__(
        self,
        *,
        links_min_interval: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """`links_min_interval` is the real setting `links_refresh_min_seconds` (60 by
        default; the real configuration allows 0, which most flows here use)."""
        self.lock = threading.RLock()
        self.kinds: list[str] = []  # every request, by kind, in order
        self.bearer_on_links = False
        self._state = "active"  # of the one follower: active, draining, revoked or gone
        self.join_max_uses = 1
        self.join_uses = 0
        self.pool_token_revoked = False
        self.credentials: set[str] = set()
        self.registrations = 0
        self.deregistrations = 0
        self.capabilities: list[dict] = []
        self.queue: list[str] = []
        self.jobs: dict[str, dict] = {}
        self.heartbeats: list[float | None] = []
        self.failed: list[dict] = []
        self.released: list[str] = []
        self.submitted: list[dict] = []
        self.script: dict[str, list] = defaultdict(list)
        self.on: dict[str, Callable[[], None]] = {}
        self.retry_after = "0"  # of a claim that finds no work (the real default is 10)
        self.links_min_interval = links_min_interval
        self.clock = clock
        self.max_attempts = 3
        self.issued: dict[str, dict] = {}  # link token -> what it is bound to
        self.transport = httpx.MockTransport(self._handle)

    @property
    def state(self) -> str:
        return self._state

    @state.setter
    def state(self, value: str) -> None:
        with self.lock:
            self._state = value
            if value == "revoked":  # the real revoke_follower: release_all, nobody is told
                for job_id, job in self.jobs.items():
                    if job["state"] == "leased":
                        self._release(job_id, record=False)

    # --- arranging -----------------------------------------------------------------------

    def add_job(self, source: bytes = b"a recording", **settings) -> str:
        job_id = str(uuid.uuid4())
        with self.lock:
            self.jobs[job_id] = {
                "state": "queued",
                "lease": None,
                "source": source,
                "settings": {"model": "distil-large-v3", "compute_type": "int8", **settings},
                "vocabulary": {"version": 0},
                "uploads": {},
                "links": 0,  # how many times links were issued
                "oldest_link": 0,  # links issued before this one are expired
                "links_issued_at": None,
                "attempts": 0,
                "result": None,
            }
            self.queue.append(job_id)
        return job_id

    def push(self, kind: str, *answers) -> None:
        """Answer the next requests of `kind` with these (a Response, an Exception, or LOST)."""
        self.script[kind].extend(answers)

    def expire_links(self, job_id: str) -> None:
        job = self.jobs[job_id]
        job["oldest_link"] = job["links"] + 1

    def cancel(self, job_id: str) -> None:
        """As the real leader: the state changes, the lease id stays, so the holder's next
        heartbeat is answered `cancel` and everything else `stale_lease`."""
        self.jobs[job_id]["state"] = "cancelled"

    def take_over(self, job_id: str) -> None:
        """The lease expired and another follower holds the job now."""
        self.jobs[job_id]["lease"] = str(uuid.uuid4())

    def count(self, kind: str) -> int:
        return sum(1 for seen in self.kinds if seen == kind)

    # --- answering -----------------------------------------------------------------------

    def _issue(self, job_id: str) -> dict:
        job = self.jobs[job_id]
        job["links"] += 1
        job["links_issued_at"] = self.clock()

        def link(name: str, method: str) -> dict:
            token = f"LINK-SECRET-{uuid.uuid4().hex}"
            self.issued[token] = {
                "job": job_id,
                "name": name,
                "lease": job["lease"],
                "n": job["links"],
                "method": method,
            }
            return {"url": f"{BASE}/v1/files/{token}", "method": method, "headers": {}}

        return {
            "download_url": link("source", "GET"),
            "upload_urls": {name: link(name, "PUT") for name in OUTPUT_NAMES},
        }

    def _kind(self, request: httpx.Request, parts: list[str]) -> str:
        if parts[:2] == ["v1", "files"]:
            if request.method == "GET":
                return "download"
            bound = self.issued.get(parts[2] if len(parts) > 2 else "")
            return f"upload:{bound['name'] if bound else 'unknown'}"
        if parts[:2] == ["v1", "followers"]:
            return parts[2]
        return parts[-1]

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/healthz":
            return httpx.Response(200, json={"status": "ok"})
        with self.lock:
            parts = request.url.path.strip("/").split("/")
            kind = self._kind(request, parts)
            if parts[:2] == ["v1", "files"] and "authorization" in request.headers:
                self.bearer_on_links = True
            self.kinds.append(kind)
            hook = self.on.get(kind)
        if hook is not None:
            hook()
        with self.lock:
            answer = self.script[kind].pop(0) if self.script[kind] else None
            if isinstance(answer, Exception):
                raise answer
            if answer is not None and answer is not LOST:
                return answer
            response = self._route(request, parts, kind)
            if answer is LOST:
                raise httpx.ReadError("the connection dropped before the answer")
            return response

    def _route(self, request: httpx.Request, parts: list[str], kind: str) -> httpx.Response:
        if parts[:2] == ["v1", "files"] and len(parts) == 3:
            return self._file(request, parts[2])
        try:
            body = json.loads(request.content) if request.content else {}
        except ValueError:
            return error(422, "invalid_request")
        if kind == "register":
            return self._register(body)
        scheme, _, credential = request.headers.get("authorization", "").partition(" ")
        if scheme != "Bearer" or not credential:  # no usable credential: just the scheme
            return error(401, "unauthorized", **{"WWW-Authenticate": "Bearer"})
        if credential not in self.credentials:
            challenge = {"WWW-Authenticate": 'Bearer error="invalid_token"'}
            return error(401, "unauthorized", **challenge)
        if self.state == "revoked":
            return error(403, "forbidden")
        if self.state == "gone":  # the real authenticate: a follower that calls is back
            self.state = "active"
        if kind == "deregister":
            return self._deregister()
        if kind == "claim":
            return self._claim()
        if len(parts) == 4 and parts[:2] == ["v1", "jobs"]:
            try:
                uuid.UUID(parts[2])
            except ValueError:
                return error(422, "invalid_request")
            return self._job(kind, parts[2], body)
        return error(404, "not_found")

    def _register(self, body: dict) -> httpx.Response:
        invalid = {"WWW-Authenticate": 'Bearer error="invalid_token"'}
        try:
            RegisterRequest.model_validate(body)
        except ValidationError:
            return error(422, "invalid_request")
        if body["protocol_version"] != 1:  # the real register checks the version first
            return error(409, "protocol_version")
        token = body["join_token"]
        reuse = False
        if token == JOIN_TOKEN:
            if self.join_uses >= self.join_max_uses:
                return error(401, "unauthorized", **invalid)
            self.join_uses += 1
        elif token == POOL_TOKEN and not self.pool_token_revoked:
            holding = any(job["state"] == "leased" for job in self.jobs.values())
            reuse = self.registrations > 0 and self._state == "gone" and not holding
        else:
            return error(401, "unauthorized", **invalid)
        self.registrations += 1
        self.capabilities.append(body["capabilities"])
        credential = f"credential-SECRET-{self.registrations}"
        if reuse:  # a reused row's old credential stops working: its hash is replaced
            self.credentials.clear()
        self.credentials.add(credential)
        self._state = "active"
        answer = {
            "follower_id": str(uuid.uuid4()),
            "credential": credential,
            "heartbeat_interval": 30,
            "lease_seconds": 120,
        }
        return httpx.Response(200, json=answer)

    def _deregister(self) -> httpx.Response:
        self.deregistrations += 1
        for job_id, job in self.jobs.items():  # the real release_all
            if job["state"] == "leased":
                self._release(job_id)
        if self._state == "active":  # the real leader: a draining follower stays draining
            self._state = "gone"
        return httpx.Response(204)

    def _claim(self) -> httpx.Response:
        headers = {"Retry-After": self.retry_after}
        if self.state == "draining":
            return httpx.Response(204, headers={**headers, DIRECTIVE_HEADER: "drain"})
        while self.queue:
            job_id = self.queue.pop(0)
            job = self.jobs[job_id]
            if job["state"] != "queued":
                continue
            job.update(state="leased", lease=str(uuid.uuid4()))
            job["attempts"] += 1
            claim = {
                "job_id": job_id,
                "lease_id": job["lease"],
                **self._issue(job_id),  # the claim counts as the lease's first links
                "settings": job["settings"],
                "vocabulary": job["vocabulary"],
                "source_version": "11-1-1",
            }
            return httpx.Response(200, json=claim)
        return httpx.Response(204, headers=headers)

    @staticmethod
    def _mine(job: dict, lease_id: str | None) -> bool:
        return lease_id is not None and lease_id == job["lease"]

    def _release(self, job_id: str, *, record: bool = True) -> None:
        """Give a job back. `record`: the follower asked for it (`released` lists those)."""
        job = self.jobs[job_id]
        job.update(state="queued", lease=None)
        job["attempts"] = max(0, job["attempts"] - 1)
        self.queue.append(job_id)
        if record:
            self.released.append(job_id)

    def _job(self, kind: str, job_id: str, body: dict) -> httpx.Response:
        models = {
            "heartbeat": HeartbeatRequest,
            "links": LinksRequest,
            "submit": SubmitRequest,
            "fail": FailRequest,
            "release": ReleaseRequest,
        }
        if kind not in models:
            return error(404, "not_found")
        try:
            models[kind].model_validate(body)
        except ValidationError:
            return error(422, "invalid_request")
        job = self.jobs.get(job_id)  # the real route validates the body before it looks
        if job is None:
            return error(404, "not_found")
        mine = self._mine(job, body["lease_id"])
        stale = error(409, "stale_lease")
        if kind == "heartbeat":
            if job["state"] == "cancelled" and mine:
                return httpx.Response(200, json={"directive": "cancel"})
            if job["state"] != "leased" or not mine:
                return stale
            self.heartbeats.append(body.get("progress"))
            directive = "drain" if self.state == "draining" else "continue"
            return httpx.Response(200, json={"directive": directive})
        if kind == "submit":
            return self._submit(job_id, job, body, mine)
        if job["state"] != "leased" or not mine:
            return stale
        if kind == "links":
            wait = None
            if job["links_issued_at"] is not None:
                wait = self.links_min_interval - (self.clock() - job["links_issued_at"])
            if wait is not None and wait > 0:
                return error(429, "too_many_requests", **{"Retry-After": str(math.ceil(wait))})
            return httpx.Response(200, json=self._issue(job_id))
        if kind == "fail":
            terminal = (
                body["code"] in NON_RETRYABLE
                or not body["retryable"]
                or job["attempts"] >= self.max_attempts
            )
            job.update(state="failed" if terminal else "queued", lease=None)
            if not terminal:
                self.queue.append(job_id)
            self.failed.append({"job_id": job_id, **body})
            return httpx.Response(204)
        self._release(job_id)
        return httpx.Response(204)

    def _submit(self, job_id: str, job: dict, body: dict, mine: bool) -> httpx.Response:
        sums = body["checksums"]
        if job["state"] == "completed":
            # The real leader repeats its answer to the same submit, so a lost answer is
            # safe to retry; anything else is a stale lease.
            if mine and job["result"] == sums:
                return httpx.Response(200, json={"accepted": True})
            return error(409, "stale_lease")
        if job["state"] != "leased" or not mine:
            return error(409, "stale_lease")
        uploads = job["uploads"]
        if set(uploads) != set(OUTPUT_NAMES) or sha(uploads["segments_json"]) == EMPTY_SHA256:
            return error(409, "outputs_missing")
        if any(sha(uploads[name]) != sums[name] for name in OUTPUT_NAMES):
            return error(409, "checksum_mismatch")
        empty = {name for name in ("txt", "srt") if sha(uploads[name]) == EMPTY_SHA256}
        if empty:
            try:
                no_segments = not SegmentsDocument.model_validate_json(
                    uploads["segments_json"]
                ).segments
            except ValidationError:
                no_segments = False
            if empty != {"txt", "srt"} or not no_segments:
                return error(409, "outputs_inconsistent")
        # `source` is recorded, never verified: the real leader has nothing to compare it to.
        job.update(state="completed", result=sums)
        self.submitted.append({"job_id": job_id, **sums})
        return httpx.Response(200, json={"accepted": True})

    def _file(self, request: httpx.Request, token: str) -> httpx.Response:
        bound = self.issued.get(token)
        job = self.jobs.get(bound["job"]) if bound else None
        if bound is None or job is None or bound["n"] < job["oldest_link"]:
            return error(403, "forbidden")
        if bound["method"] != request.method:
            return error(403, "forbidden")
        if job["state"] != "leased" or bound["lease"] != job["lease"]:
            return error(409, "stale_lease")
        if request.method == "GET":
            return httpx.Response(200, content=job["source"])
        job["uploads"][bound["name"]] = request.content
        return httpx.Response(201)


class FakeEngine:
    """What ModelHost is given as its factory. `steps` are the fractions the fake engine
    reports; `on_step` runs before each report (to cancel, revoke or stop at that moment)."""

    def __init__(self) -> None:
        self.loads: list[tuple[str, str, str]] = []
        self.closed = 0
        self.steps: tuple[float, ...] = (0.25, 0.5, 0.75, 1.0)
        self.on_step = None
        self.error: Exception | None = None
        self.load_error: Exception | None = None
        self.unavailable: set[str] = set()
        self.segments: tuple[Segment, ...] = SEGMENTS
        self.transcribed: list[dict] = []

    def __call__(self, settings):
        if self.load_error is not None:
            raise self.load_error
        if settings.model in self.unavailable:
            raise RuntimeError(f"{settings.model} is not in the local cache and offline mode is on")
        self.loads.append((settings.model, settings.compute_type, settings.device))
        return _FakeTranscriber(self)


class _FakeTranscriber:
    def __init__(self, engine: FakeEngine) -> None:
        self.engine = engine

    def warm_up(self) -> None:
        pass

    def close(self) -> None:
        self.engine.closed += 1

    def transcribe(self, path, vocabulary, *, settings, progress):
        path = Path(path)
        self.engine.transcribed.append(
            {"audio": path.read_bytes(), "vocabulary": vocabulary, "settings": settings}
        )
        for fraction in self.engine.steps:
            if self.engine.on_step is not None:
                self.engine.on_step(fraction)
            progress(fraction)
        if self.engine.error is not None:
            raise self.engine.error
        return Transcript(
            source_name=path.name,
            source_checksum=sha256_file(path),
            duration=3.25,
            settings=settings,
            vocabulary_version=vocabulary.version,
            vocabulary_terms_used=(),
            corrections_applied=(),
            segments=self.engine.segments,
        )


def make_settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "leader_url": BASE,
        "join_token": JOIN_TOKEN,
        "state_dir": tmp_path / "state",
        "model_dir": tmp_path / "models",
        "device": "cpu",
    }
    values.update(overrides)
    return Settings(**values)


def make_runner(tmp_path: Path, leader: FakeLeader, engine: FakeEngine, **overrides):
    """A JobRunner whose follower is already registered, and the client to claim with."""
    leader.credentials.add("credential-SECRET-0")
    client = LeaderClient(BASE, credential="credential-SECRET-0", transport=leader.transport)
    scratch = Scratch(tmp_path / "scratch", tmp_path / "state", delays=(), sleep=lambda s: None)
    scratch.prepare()
    values = {"device": "cpu", "heartbeat_interval": 0.01, "sleep": lambda seconds: None}
    values.update(overrides)
    runner = JobRunner(
        client, Links(transport=leader.transport), ModelHost("cpu", factory=engine), scratch,
        **values,
    )
    return runner, client


def make_agent(tmp_path: Path, leader: FakeLeader, engine: FakeEngine, **overrides):
    """An Agent wired to the fake leader and engine, with waits that do not wait. The import
    is here because the kit is used by the job's tests before the agent exists."""
    from swarmscribe_follower.agent import Agent

    settings = make_settings(tmp_path, **overrides)
    return Agent(
        settings,
        client=LeaderClient(BASE, transport=leader.transport),
        links=Links(transport=leader.transport),
        models=ModelHost("cpu", factory=engine, allowed=frozenset(settings.allowed_models)),
        scratch=Scratch(settings.scratch, settings.state_dir),
        store=CredentialStore(settings.credential_file),
        probe=Probe(CPU),
        heartbeat_interval=0.01,
        parked_poll_seconds=0.01,
        sleep=lambda seconds: None,
    )
