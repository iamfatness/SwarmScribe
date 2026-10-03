"""Compose end-to-end scenario for the leader (leader spec, section 13).

Postgres, two leader replicas behind nginx, and two scripted followers on a local location
holding consented and unconsented recordings. One follower dies holding a job; after two
completions, and once both replicas have answered requests (the proxy names the upstream
in an X-Upstream header), one leader replica is killed. Every consented recording must
complete exactly once (one job, one completed attempt), and the unconsented one must never
be linked.

CI runs this after `docker compose up` (.github/workflows/ci.yml); the leader's tests also
run it in-process against one leader (packages/leader/tests/test_compose_driver.py).
Nothing here prints a link, a credential or a join token.
"""

import argparse
import asyncio
import base64
import hashlib
import json
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from swarmscribe_leader.auth.followers import create_join_token
from swarmscribe_leader.clock import utcnow
from swarmscribe_leader.db.models import Job, JobAttempt, JobResult, Recording, StorageLocation
from swarmscribe_leader.db.session import make_engine, make_sessionmaker

HERE = Path(__file__).resolve().parent
COMPOSE_FILE = HERE / "docker-compose.yml"
LOCATION = "e2e"
CONSENTED = tuple(f"talks/{name}.mp3" for name in "abcdefgh")
UNCONSENTED = "private/held.mp3"
CAPABILITIES = {
    "device": "cpu",
    "models": ["distil-large-v3"],
    "engine_version": "0.1.0",
    "pool": "default",
}
TRANSIENT = frozenset({502, 503, 504})


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def link_key(url: str) -> str:
    """The storage key a leader file link names (its payload is signed, not encrypted)."""
    payload = url.rsplit("/", 1)[1].split(".", 1)[0]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["key"]


async def call(client: httpx.AsyncClient, method: str, url: str, **kwargs: Any) -> httpx.Response:
    """One request through the proxy, retried while a replica is down or restarting."""
    for _ in range(120):
        try:
            response = await client.request(method, url, **kwargs)
        except httpx.TransportError:
            await asyncio.sleep(0.5)
            continue
        if response.status_code not in TRANSIENT:
            return response
        await asyncio.sleep(0.5)
    raise AssertionError(f"a {method} request failed for a minute: the leaders are unreachable")


@dataclass
class Follower:
    client: httpx.AsyncClient
    follower_id: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    linked: list[str] = field(default_factory=list)
    completed: list[str] = field(default_factory=list)

    async def register(self, token: str) -> None:
        response = await call(
            self.client,
            "POST",
            "/v1/followers/register",
            json={"join_token": token, "protocol_version": 1, "capabilities": CAPABILITIES},
        )
        expect(response.status_code == 200, f"registering answered {response.status_code}")
        body = response.json()
        self.follower_id = body["follower_id"]
        self.headers = {"Authorization": f"Bearer {body['credential']}"}

    async def claim(self) -> dict[str, Any] | None:
        response = await call(self.client, "POST", "/v1/jobs/claim", headers=self.headers)
        if response.status_code == 204:
            return None
        expect(response.status_code == 200, f"claiming answered {response.status_code}")
        claim = response.json()
        self.linked.append(link_key(claim["download_url"]["url"]))
        return claim

    async def work(self, claim: dict[str, Any], work_seconds: float) -> bool:
        """Download, 'transcribe', upload, submit. False when the lease was lost on the way
        (the reaper queues the job again and a later attempt completes it)."""
        job, lease = claim["job_id"], {"lease_id": claim["lease_id"]}
        download = await call(self.client, "GET", claim["download_url"]["url"])
        if download.status_code != 200:
            return False
        heartbeat = await call(
            self.client, "POST", f"/v1/jobs/{job}/heartbeat", headers=self.headers, json=lease
        )
        if heartbeat.status_code != 200 or heartbeat.json()["directive"] != "continue":
            return False
        await asyncio.sleep(work_seconds)
        checksums = {"source": hashlib.sha256(download.content).hexdigest()}
        for name in ("txt", "srt", "segments_json"):
            body = f"{name} of {len(download.content)} bytes\n".encode()
            upload = await call(
                self.client, "PUT", claim["upload_urls"][name]["url"], content=body
            )
            if upload.status_code != 201:
                return False
            checksums[name] = hashlib.sha256(body).hexdigest()
        submit = await call(
            self.client,
            "POST",
            f"/v1/jobs/{job}/submit",
            headers=self.headers,
            json={**lease, "checksums": checksums},
        )
        if submit.status_code != 200:
            return False
        self.completed.append(job)
        return True


@dataclass(frozen=True)
class Report:
    completed: int
    abandoned_job: str
    killed_after: int
    upstreams_before_kill: frozenset[str]


class Upstreams:
    """The leader replicas that answered, from the proxy's X-Upstream header. A request the
    proxy moved to another replica names several and counts for none."""

    def __init__(self) -> None:
        self.served: set[str] = set()

    async def note(self, response: httpx.Response) -> None:
        upstream = response.headers.get("x-upstream", "").strip()
        if upstream and "," not in upstream and " : " not in upstream:
            self.served.add(upstream)


def write_recordings(data_dir: Path) -> None:
    def put(key: str, data: bytes) -> None:
        path = data_dir / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    put("consent.txt", b"talks/*.mp3\n")
    for number, key in enumerate(CONSENTED):
        put(key, f"recording {number}\n".encode() * (50 + number))
    put(UNCONSENTED, b"never to be processed\n")
    put("talks/notes.txt", b"not audio\n")


async def seed(sessionmaker: async_sessionmaker[AsyncSession], storage_root: str) -> str:
    """The location and a join token, written straight to Postgres (Compose runs no
    identity provider, so the admin API is not used here)."""
    async with sessionmaker() as session:
        session.add(
            StorageLocation(
                id=uuid.uuid4(),
                name=LOCATION,
                backend="local",
                config={"root": storage_root},
                input_prefix="",
                output_prefix="transcripts/",
                pool="default",
                required_device="any",
                scan_interval_s=0,
                enabled=True,
                vocabulary_version=0,
            )
        )
        _, token = await create_join_token(
            session,
            pool="default",
            expires_at=utcnow() + timedelta(hours=1),
            max_uses=5,
            created_by="e2e",
        )
        await session.commit()
    return token


async def wait_until_ready(client: httpx.AsyncClient, deadline: float) -> None:
    while time.monotonic() < deadline:
        try:
            if (await client.get("/readyz")).status_code == 200:
                return
        except httpx.TransportError:
            pass
        await asyncio.sleep(1)
    raise AssertionError("the leaders never became ready")


async def claim_and_die(follower: Follower, deadline: float) -> str:
    """The doomed follower takes one job and is never heard from again."""
    while time.monotonic() < deadline:
        claim = await follower.claim()
        if claim is not None:
            return claim["job_id"]
        await asyncio.sleep(0.3)
    raise AssertionError("the scanner never queued anything")


async def completed_recordings(sessionmaker: async_sessionmaker[AsyncSession]) -> int:
    async with sessionmaker() as session:
        return await session.scalar(
            select(func.count(func.distinct(Job.recording_id))).where(Job.state == "completed")
        )


async def jobs_completed_more_than_once(session: AsyncSession) -> list[uuid.UUID]:
    rows = await session.scalars(
        select(JobAttempt.job_id)
        .where(JobAttempt.outcome == "completed")
        .group_by(JobAttempt.job_id)
        .having(func.count() > 1)
        .order_by(JobAttempt.job_id)
    )
    return list(rows.all())


async def check(
    sessionmaker: async_sessionmaker[AsyncSession],
    data_dir: Path,
    *,
    doomed: Follower,
    steady: Follower,
    abandoned: str,
) -> None:
    async with sessionmaker() as session:
        recordings = {r.key: r for r in (await session.scalars(select(Recording))).all()}
        jobs = (await session.scalars(select(Job))).all()
        results = {r.job_id for r in (await session.scalars(select(JobResult))).all()}
        attempts = (
            await session.scalars(
                select(JobAttempt).where(JobAttempt.job_id == uuid.UUID(abandoned))
            )
        ).all()
        repeated = await jobs_completed_more_than_once(session)
    expect(set(recordings) == {*CONSENTED, UNCONSENTED}, f"catalogued {sorted(recordings)}")
    for key in CONSENTED:
        done = [j for j in jobs if j.recording_id == recordings[key].id and j.state == "completed"]
        expect(len(done) == 1, f"{key} completed {len(done)} times")
        expect(done[0].id in results, f"{key} has no recorded result")
        outputs = data_dir / "transcripts" / f"{key}.segments.json"
        expect(outputs.exists(), f"{key} has no outputs in storage")
    expect(not repeated, f"{len(repeated)} job(s) have more than one completed attempt")
    held = recordings[UNCONSENTED]
    expect(held.consent == "not_consented", f"the unconsented recording is {held.consent}")
    expect(
        all(j.recording_id != held.id for j in jobs),
        "a job exists for a recording without consent",
    )
    linked = set(doomed.linked) | set(steady.linked)
    expect(linked <= set(CONSENTED), "a link was issued for a recording without consent")
    seen = {(str(a.follower_id), a.outcome) for a in attempts}
    expect((doomed.follower_id, "expired") in seen, "the dead follower's lease never expired")
    expect(
        (steady.follower_id, "completed") in seen,
        "the abandoned job was not finished by the other follower",
    )


async def run(
    *,
    base_url: str,
    database_url: str,
    data_dir: Path,
    storage_root: str,
    kill_replica: Callable[[], object],
    transport: httpx.AsyncBaseTransport | None = None,
    replicas: int = 0,
    work_seconds: float = 1.0,
    timeout: float = 240.0,
) -> Report:
    """`replicas`: how many distinct upstreams must have answered before one is killed
    (0 when there is no proxy to name them)."""
    write_recordings(data_dir)
    engine = make_engine(database_url)
    sessionmaker = make_sessionmaker(engine)
    deadline = time.monotonic() + timeout
    upstreams = Upstreams()
    served_before_kill: frozenset[str] = frozenset()
    try:
        token = await seed(sessionmaker, storage_root)
        async with httpx.AsyncClient(
            base_url=base_url,
            transport=transport,
            timeout=30.0,
            event_hooks={"response": [upstreams.note]},
        ) as client:
            await wait_until_ready(client, deadline)
            doomed, steady = Follower(client), Follower(client)
            await doomed.register(token)
            await steady.register(token)
            abandoned = await claim_and_die(doomed, deadline)
            killed_after: int | None = None
            while await completed_recordings(sessionmaker) < len(CONSENTED):
                expect(time.monotonic() < deadline, "not every consented recording completed")
                if killed_after is None and len(steady.completed) >= 2:
                    served_before_kill = frozenset(upstreams.served)
                    expect(
                        len(served_before_kill) >= replicas,
                        f"only {len(served_before_kill)} of {replicas} leader replicas "
                        "answered before the kill",
                    )
                    killed_after = len(steady.completed)
                    await asyncio.to_thread(kill_replica)
                claim = await steady.claim()
                if claim is None:
                    await asyncio.sleep(0.3)
                    continue
                await steady.work(claim, work_seconds)
        expect(killed_after is not None, "everything completed before a replica was killed")
        await check(sessionmaker, data_dir, doomed=doomed, steady=steady, abandoned=abandoned)
    finally:
        await engine.dispose()
    return Report(
        completed=len(CONSENTED),
        abandoned_job=abandoned,
        killed_after=killed_after,
        upstreams_before_kill=served_before_kill,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="SwarmScribe Compose end-to-end scenario")
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument(
        "--database-url", default="postgresql://postgres:postgres@localhost:5432/swarmscribe"
    )
    parser.add_argument("--data-dir", type=Path, default=HERE / "work" / "data")
    parser.add_argument("--storage-root", default="/data")
    parser.add_argument("--replica", default="leader-1", help="the replica to kill")
    parser.add_argument(
        "--replicas", type=int, default=2, help="replicas that must answer before the kill"
    )
    args = parser.parse_args()

    def kill() -> None:
        subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "kill", args.replica], check=True
        )
        print(f"killed {args.replica}", flush=True)

    try:
        report = asyncio.run(
            run(
                base_url=args.base_url,
                database_url=args.database_url,
                data_dir=args.data_dir,
                storage_root=args.storage_root,
                kill_replica=kill,
                replicas=args.replicas,
            )
        )
    except AssertionError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    print(
        f"passed: {report.completed} recordings completed once each; "
        f"{len(report.upstreams_before_kill)} replicas answered, then {args.replica} was "
        f"killed after {report.killed_after} completions"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
