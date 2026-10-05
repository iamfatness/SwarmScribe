"""Two leaders as the console sees them over HTTPS, in memory, for the end-to-end harness.

Each leader keeps locations, jobs, followers and join tokens, and answers every route of the
console's allow-list in the shapes of swarmscribe_leader.api.admin_models, so actions change
what the next read shows. A leader applies its console credential's cap the way C1 does
(effective role = the lower of the asserted role and the cap) and then the route's role, so
an action the console allows can still meet the leader's own 403 (us-1 is capped at
operator), in the sentence the real leader uses. `modes[name] = "down"` makes a leader refuse
connections; `modes[name] = "revoked"` makes it answer that this console's credential was
revoked (C1b's 401), for any host name, so a leader added in a test can be in that state too.
A host that is not one of the two leaders refuses connections, as a leader that was never
there does."""

import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from console_testkit import CREDENTIAL
from swarmscribe_console.proxy import match_route
from swarmscribe_leader.auth.roles import at_least

CAPS = {"eu-1": "admin", "us-1": "operator"}
ROLES = ("viewer", "operator", "admin")
JOB_STATES = ("queued", "leased", "completed", "failed", "cancelled")
FOLLOWER_STATES = ("active", "draining", "revoked", "gone")
SCAN_ERROR = "the root folder is not readable"
_ADMIN_PREFIX = "/v1/admin/"


def _iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def _id(leader: str, kind: str, n: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"e2e/{leader}/{kind}/{n}"))


def _error(status: int, code: str, message: str) -> httpx.Response:
    return httpx.Response(status, json={"code": code, "message": message})


class Leader:
    def __init__(self, name: str) -> None:
        now = datetime.now(UTC)
        self.name = name
        self.locations: dict[str, dict[str, Any]] = {}
        archive_error = SCAN_ERROR if name == "eu-1" else None
        for n, (loc, error) in enumerate([("intake", None), ("archive", archive_error)]):
            self.locations[loc] = {
                "id": _id(name, "location", n),
                "name": loc,
                "backend": "local",
                "root": f"/srv/{loc}",
                "input_prefix": "",
                "output_prefix": "transcripts/",
                "pool": "default",
                "required_device": "any",
                "scan_interval_s": 900,
                "enabled": True,
                "last_scan_at": _iso(now - timedelta(minutes=2)),
                "last_scan_error": error,
                "scan_requested": False,
                "channel_mode": "mono",
                "channel_labels": ["Left", "Right"],
            }
        self.followers: dict[str, dict[str, Any]] = {}
        follower_specs = [
            ("default", "active", "cuda"),
            ("gpu", "active", "cuda"),
            ("default", "draining", "cpu"),
        ]
        for n, (pool, state, device) in enumerate(follower_specs):
            fid = _id(name, "follower", n)
            self.followers[fid] = {
                "id": fid,
                "pool": pool,
                "state": state,
                "device": device,
                "last_seen_at": _iso(now - timedelta(seconds=20)),
                "created_at": _iso(now - timedelta(days=3)),
                "leases": 0,
            }
        first_follower = next(iter(self.followers))
        self.jobs: dict[str, dict[str, Any]] = {}
        job_specs = [
            ("queued", "default", 900, None),
            ("queued", "gpu", 300, None),
            ("leased", "default", 600, None),
            ("completed", "default", 7200, None),
            ("failed", "default", 5400, "the engine stopped: out of memory"),
            ("cancelled", "default", 3600, None),
        ]
        for n, (state, pool, age, reason) in enumerate(job_specs):
            jid = _id(name, "job", n)
            completed_at = None
            if state == "completed":
                completed_at = _iso(now - timedelta(seconds=age - 60))
            self.jobs[jid] = {
                "id": jid,
                "state": state,
                "location": "intake",
                "key": f"incoming/meeting-{n + 1}.wav",
                "priority": 0,
                "attempts": 3 if state == "failed" else (1 if state != "queued" else 0),
                "max_attempts": 3,
                "pool": pool,
                "leased_by": first_follower if state == "leased" else None,
                "failure_reason": reason,
                "cancelled_by": "someone@example.org" if state == "cancelled" else None,
                "no_speech": False if state == "completed" else None,
                "created_at": _iso(now - timedelta(seconds=age)),
                "completed_at": completed_at,
            }
        self.followers[first_follower]["leases"] = 1
        self.tokens: dict[str, dict[str, Any]] = {}
        tid = _id(name, "token", 0)
        self.tokens[tid] = {
            "id": tid,
            "pool": "default",
            "expires_at": _iso(now + timedelta(days=6)),
            "max_uses": 5,
            "uses": 1,
            "revoked": False,
            "created_by": "admin@example.org",
            "created_at": _iso(now - timedelta(days=1)),
        }

    # ---- reads ----

    def status(self) -> dict[str, Any]:
        now = datetime.now(UTC)
        jobs = {state: 0 for state in JOB_STATES}
        pools: dict[str, dict[str, Any]] = {}
        oldest: int | None = None
        for job in self.jobs.values():
            jobs[job["state"]] += 1
            pool = pools.setdefault(job["pool"], {"pool": job["pool"], "queued": 0, "leased": 0})
            if job["state"] in ("queued", "leased"):
                pool[job["state"]] += 1
            if job["state"] == "queued":
                age = int((now - datetime.fromisoformat(job["created_at"])).total_seconds())
                oldest = age if oldest is None else max(oldest, age)
        followers = {state: 0 for state in FOLLOWER_STATES}
        by_pool: dict[str, dict[str, Any]] = {}
        for follower in self.followers.values():
            followers[follower["state"]] += 1
            entry = by_pool.setdefault(
                follower["pool"],
                {"pool": follower["pool"], "active": 0, "draining": 0, "revoked": 0, "gone": 0},
            )
            entry[follower["state"]] += 1
        return {
            "jobs": jobs,
            "pools": sorted(pools.values(), key=lambda p: p["pool"]),
            "followers": followers,
            "follower_pools": sorted(by_pool.values(), key=lambda p: p["pool"]),
            "completed_last_hour": 7 if self.name == "eu-1" else 3,
            "completed_last_day": 30 if self.name == "eu-1" else 12,
            "oldest_queued_age_s": oldest,
            "failed_attempts_last_day": jobs["failed"],
            "locations": [
                {
                    "name": loc["name"],
                    "backend": loc["backend"],
                    "enabled": loc["enabled"],
                    "last_scan_at": loc["last_scan_at"],
                    "last_scan_error": loc["last_scan_error"],
                    "scan_requested": loc["scan_requested"],
                    "recordings": 12 if loc["name"] == "intake" else 0,
                    "consented": 10 if loc["name"] == "intake" else 0,
                }
                for loc in self.locations.values()
            ],
        }

    def list_jobs(self, query: dict[str, str]) -> list[dict[str, Any]]:
        found = [
            job
            for job in self.jobs.values()
            if job["state"] == query.get("state", job["state"])
            and job["location"] == query.get("location", job["location"])
        ]
        found.sort(key=lambda job: job["created_at"], reverse=True)
        return found[: int(query.get("limit", "50"))]

    def consent_report(self) -> dict[str, Any]:
        completed = [job for job in self.jobs.values() if job["state"] == "completed"]
        counts = [("intake", 10, 1, 1), ("archive", 0, 0, 0)]
        return {
            "locations": [
                {
                    "name": name,
                    "consented": consented,
                    "not_consented": refused,
                    "withdrawn": withdrawn,
                    "missing": 0,
                }
                for name, consented, refused, withdrawn in counts
            ],
            "flagged": [
                {
                    "job_id": job["id"],
                    "location": job["location"],
                    "key": job["key"],
                    "completed_at": job["completed_at"],
                    "output_location": "intake",
                    "outputs": [f"transcripts/{job['key'].rsplit('/', 1)[-1]}.json"],
                }
                for job in completed[:1]
            ],
            "truncated": False,
        }

    # ---- actions ----

    def job_action(self, action: str, job_id: str, actor: str, body: Any) -> httpx.Response:
        job = self.jobs.get(job_id)
        if job is None:
            return _error(404, "not_found", "no such job")
        if action == "jobs.retry":
            if job["state"] not in ("failed", "cancelled"):
                return _error(
                    409, "not_retryable", "only failed or cancelled jobs can be retried"
                )
            job.update(state="queued", attempts=0, failure_reason=None, cancelled_by=None)
            job["created_at"] = _iso(datetime.now(UTC))
        elif job["state"] not in ("queued", "leased"):
            return _error(409, "not_open", "the job is not queued or leased")
        elif action == "jobs.cancel":
            job.update(state="cancelled", cancelled_by=actor, leased_by=None)
        else:
            job["priority"] = body["priority"]
        return httpx.Response(200, json=job)

    def follower_action(self, action: str, follower_id: str) -> httpx.Response:
        follower = self.followers.get(follower_id)
        if follower is None:
            return _error(404, "not_found", "no such follower")
        if action == "followers.drain":
            if follower["state"] == "active":
                follower["state"] = "draining"
            return httpx.Response(200, json=follower)
        released = 0
        for job in self.jobs.values():
            if job["state"] == "leased" and job["leased_by"] == follower_id:
                job.update(state="queued", leased_by=None)
                released += 1
        follower.update(state="revoked", leases=0)
        answer = {"id": follower_id, "state": "revoked", "released": released}
        return httpx.Response(200, json=answer)

    def location_action(self, action: str, name: str, body: Any) -> httpx.Response:
        if action == "locations.add":
            if body["name"] in self.locations:
                return _error(409, "exists", f"a location named {body['name']!r} already exists")
            location = {
                "id": str(uuid.uuid4()),
                "name": body["name"],
                "backend": "local",
                "root": body["root"],
                "input_prefix": body.get("input_prefix", ""),
                "output_prefix": body.get("output_prefix", "transcripts/"),
                "pool": body.get("pool", "default"),
                "required_device": body.get("required_device", "any"),
                "scan_interval_s": body.get("scan_interval_s", 900),
                "enabled": True,
                "last_scan_at": None,
                "last_scan_error": None,
                "scan_requested": False,
                "channel_mode": body.get("channel_mode", "mono"),
                "channel_labels": body.get("channel_labels", ["Left", "Right"]),
            }
            self.locations[location["name"]] = location
            return httpx.Response(201, json=location)
        location = self.locations.get(name)
        if location is None:
            return _error(404, "not_found", f"no location named {name!r}")
        if action == "locations.ingest":
            if not location["enabled"]:
                return _error(409, "disabled", f"location {name!r} is disabled; enable it first")
            location["scan_requested"] = True
            answer = {"name": name, "requested_at": _iso(datetime.now(UTC))}
            return httpx.Response(202, json=answer)
        location["enabled"] = action == "locations.enable"
        return httpx.Response(200, json=location)

    def token_action(
        self, action: str, token_id: str | None, actor: str, body: Any
    ) -> httpx.Response:
        if action == "tokens.create":
            now = datetime.now(UTC)
            tid = str(uuid.uuid4())
            lifetime = timedelta(seconds=body.get("expires_in_seconds", 7 * 86400))
            token = {
                "id": tid,
                "pool": body.get("pool", "default"),
                "expires_at": _iso(now + lifetime),
                "max_uses": body.get("max_uses", 1),
                "uses": 0,
                "revoked": False,
                "created_by": actor,
                "created_at": _iso(now),
            }
            self.tokens[tid] = token
            plaintext = "sst_" + secrets.token_urlsafe(32)
            created = {key: token[key] for key in ("id", "pool", "expires_at", "max_uses")}
            return httpx.Response(201, json={**created, "token": plaintext})
        token = self.tokens.get(token_id or "")
        if token is None:
            return _error(404, "not_found", "no such join token")
        token["revoked"] = True
        return httpx.Response(200, json=token)


class FakeLeaders:
    def __init__(self) -> None:
        self.modes: dict[str, str] = {}
        self.leaders: dict[str, Leader] = {}
        self.reset()

    def reset(self) -> None:
        self.modes = {}
        self.leaders = {name: Leader(name) for name in CAPS}

    async def handler(self, request: httpx.Request) -> httpx.Response:
        name = (request.url.host or "").split(".", 1)[0]
        if self.modes.get(name) == "revoked":
            return _error(401, "credential_revoked", "this console credential was revoked")
        leader = self.leaders.get(name)
        if leader is None or self.modes.get(name) == "down":
            raise httpx.ConnectError("connection refused", request=request)
        if request.headers.get("authorization") != f"Console {CREDENTIAL}":
            return _error(401, "unauthorized", "unknown console credential")
        asserted = request.headers.get("x-swarmscribe-actor-role", "viewer")
        cap = CAPS[name]
        role = asserted if ROLES.index(asserted) <= ROLES.index(cap) else cap
        actor_header = request.headers.get("x-swarmscribe-actor", "")
        actor = actor_header.rsplit(" ", 1)[-1]
        path = request.url.path
        if not path.startswith(_ADMIN_PREFIX):
            return _error(404, "not_found", "Not Found")
        found = match_route(request.method, path[len(_ADMIN_PREFIX) :])
        if found is None:
            return _error(404, "not_found", "Not Found")
        route, params = found
        if not at_least(role, route.role):
            # The real leader's two sentences (swarmscribe_leader.api.admin_auth._refusal).
            reason = (
                f"console e2e is limited to {cap}"
                if not at_least(cap, route.role)
                else f"you have {role}"
            )
            return _error(403, "forbidden", f"this needs the {route.role} role; {reason}")
        raw = await request.aread()
        body = json.loads(raw) if raw else {}
        query = dict(request.url.params)
        action = route.action
        if action == "status.view":
            return httpx.Response(200, json=leader.status())
        if action == "locations.view":
            return httpx.Response(200, json=list(leader.locations.values()))
        if action == "jobs.view":
            return httpx.Response(200, json=leader.list_jobs(query))
        if action == "followers.view":
            state = query.get("state")
            followers = [f for f in leader.followers.values() if state in (None, f["state"])]
            return httpx.Response(200, json=followers)
        if action == "tokens.view":
            return httpx.Response(200, json=list(leader.tokens.values()))
        if action == "consent.view":
            return httpx.Response(200, json=leader.consent_report())
        if action.startswith("jobs."):
            return leader.job_action(action, params["job_id"], actor, body)
        if action.startswith("followers."):
            return leader.follower_action(action, params["follower_id"])
        if action.startswith("locations."):
            return leader.location_action(action, params.get("location", ""), body)
        return leader.token_action(action, params.get("token_id"), actor, body)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)
