import asyncio
import hashlib
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select
from swarmscribe_leader.app import create_app
from swarmscribe_leader.auth.followers import create_join_token
from swarmscribe_leader.clock import utcnow
from swarmscribe_leader.config import Settings
from swarmscribe_leader.db.models import Job, JobAttempt, Recording
from swarmscribe_protocol import ClaimResponse

CAPABILITIES = {
    "device": "cpu",
    "models": ["distil-large-v3"],
    "engine_version": "0.1.0",
    "pool": "default",
}


def write(root, key, data):
    path = root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


async def follower_session(client, token) -> dict:
    response = await client.post(
        "/v1/followers/register",
        json={
            "join_token": token,
            "protocol_version": 1,
            "capabilities": CAPABILITIES,
        },
    )
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['credential']}"}


async def work_until_idle(client, headers, *, idle_rounds=30):
    """A well-behaved fake follower: claim, download, 'transcribe', upload, submit."""
    idle = 0
    while idle < idle_rounds:
        response = await client.post("/v1/jobs/claim", headers=headers)
        if response.status_code == 204:
            idle += 1
            await asyncio.sleep(0.1)
            continue
        idle = 0
        claim = ClaimResponse.model_validate(response.json())
        source = (await client.get(claim.download_url.url)).content
        checksums = {"source": hashlib.sha256(source).hexdigest()}
        for name in ("txt", "srt", "segments_json"):
            body = f"{name} of {len(source)} bytes\n".encode()
            target = getattr(claim.upload_urls, name).url
            (await client.put(target, content=body)).raise_for_status()
            checksums[name] = hashlib.sha256(body).hexdigest()
        result = await client.post(
            f"/v1/jobs/{claim.job_id}/submit",
            headers=headers,
            json={"lease_id": claim.lease_id, "checksums": checksums},
        )
        result.raise_for_status()


async def test_consented_recordings_complete_even_when_a_follower_dies(
    engine, migrated_database_url, factory, sessionmaker, tmp_path
):
    write(tmp_path, "consent.txt", b"talks/*.mp3\n")
    for name, size in (("a", 10), ("b", 20), ("c", 30)):
        write(tmp_path, f"talks/{name}.mp3", b"x" * size)
    write(tmp_path, "private/secret.mp3", b"never")
    write(tmp_path, "talks/notes.txt", b"not audio")
    await factory.location(scan_interval_s=0)

    settings = Settings(
        database_url=migrated_database_url,
        public_url="http://leader",
        link_key="k" * 32,
        lease_seconds=2,
        heartbeat_seconds=1,
        reaper_interval_seconds=0.2,
        scanner_interval_seconds=0.2,
        claim_retry_after=1,
    )
    app = create_app(settings, background=True)
    async with sessionmaker() as session:
        _, token = await create_join_token(
            session,
            pool="default",
            expires_at=utcnow() + timedelta(hours=1),
            max_uses=10,
            created_by="test",
        )
        await session.commit()

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://leader") as client:
            # The doomed follower claims one job and then vanishes without a word.
            doomed = await follower_session(client, token)
            for _ in range(50):
                response = await client.post("/v1/jobs/claim", headers=doomed)
                if response.status_code == 200:
                    abandoned = response.json()["job_id"]
                    break
                await asyncio.sleep(0.1)
            else:
                pytest.fail("the scanner never queued anything")

            healthy = await follower_session(client, token)
            await asyncio.wait_for(work_until_idle(client, healthy), timeout=40)

    async with sessionmaker() as session:
        jobs = (await session.scalars(select(Job))).all()
        recordings = {r.id: r for r in (await session.scalars(select(Recording))).all()}
        attempts = (await session.scalars(select(JobAttempt))).all()
    completed = [j for j in jobs if j.state == "completed"]
    assert sorted(recordings[j.recording_id].key for j in completed) == [
        "talks/a.mp3",
        "talks/b.mp3",
        "talks/c.mp3",
    ]
    assert all(j.state == "completed" for j in jobs)
    secret = [r for r in recordings.values() if r.key == "private/secret.mp3"]
    assert [r.consent for r in secret] == ["not_consented"]
    assert {str(a.job_id) for a in attempts if a.outcome == "expired"} == {abandoned}
    # The abandoned job was taken over exactly once: one expired attempt by the follower that
    # died, one completed attempt by another follower.
    abandoned_attempts = [a for a in attempts if str(a.job_id) == abandoned]
    (expired,) = [a for a in abandoned_attempts if a.outcome == "expired"]
    (completed,) = [a for a in abandoned_attempts if a.outcome == "completed"]
    assert len(abandoned_attempts) == 2
    assert expired.follower_id != completed.follower_id
    for name in ("a", "b", "c"):
        assert (tmp_path / "transcripts" / "talks" / f"{name}.mp3.segments.json").exists()
