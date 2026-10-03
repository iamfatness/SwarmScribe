import hashlib
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select
from swarmscribe_leader.app import create_app
from swarmscribe_leader.auth.followers import create_join_token
from swarmscribe_leader.clock import utcnow
from swarmscribe_leader.config import Settings
from swarmscribe_leader.db.models import Follower, Job, StorageLocation
from swarmscribe_leader.ingest.scanner import scan_location
from swarmscribe_leader.storage.links import LinkSigner
from swarmscribe_leader.storage.registry import backend_for
from swarmscribe_protocol import ClaimResponse

CAPABILITIES = {
    "device": "cpu",
    "models": ["distil-large-v3"],
    "engine_version": "0.1.0",
    "pool": "default",
}


@pytest.fixture
async def app(engine, migrated_database_url):
    settings = Settings(
        database_url=migrated_database_url, public_url="http://leader", link_key="k" * 32
    )
    application = create_app(settings, background=False)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://leader"
    ) as http:
        yield http


def write(root, key, data=b"audio bytes"):
    path = root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def join_token(sessionmaker, *, max_uses=5) -> str:
    async with sessionmaker() as session:
        _, plaintext = await create_join_token(
            session,
            pool="default",
            expires_at=utcnow() + timedelta(days=1),
            max_uses=max_uses,
            created_by="test",
        )
        await session.commit()
    return plaintext


async def register(client, sessionmaker, **capabilities) -> dict:
    token = await join_token(sessionmaker)
    response = await client.post(
        "/v1/followers/register",
        json={
            "join_token": token,
            "protocol_version": 1,
            "capabilities": {**CAPABILITIES, **capabilities},
        },
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['credential']}"}


async def queue_one(sessionmaker, factory, tmp_path, key="talks/one.mp3", data=b"audio bytes"):
    write(tmp_path, "consent.txt", b"**/*.mp3\n")
    write(tmp_path, key, data)
    location = await factory.location()
    async with sessionmaker() as session:
        loc = await session.get(StorageLocation, location.id)
        backend = backend_for(loc, signer=LinkSigner(b"k" * 32), public_url="http://leader")
        await scan_location(session, loc, backend, now=utcnow(), max_attempts=3)
        await session.commit()
    return location


async def claim(client, headers) -> ClaimResponse:
    response = await client.post("/v1/jobs/claim", headers=headers)
    assert response.status_code == 200, response.text
    return ClaimResponse.model_validate(response.json())


async def upload_outputs(client, claimed: ClaimResponse) -> dict[str, str]:
    checksums = {}
    for name in ("txt", "srt", "segments_json"):
        body = f"{name} output\n".encode()
        link = getattr(claimed.upload_urls, name)
        assert (await client.put(link.url, content=body)).status_code == 201
        checksums[name] = sha(body)
    return checksums


async def test_register_returns_a_credential_and_timings(client, sessionmaker):
    token = await join_token(sessionmaker)
    response = await client.post(
        "/v1/followers/register",
        json={"join_token": token, "protocol_version": 1, "capabilities": CAPABILITIES},
    )
    body = response.json()
    assert response.status_code == 200
    assert (body["heartbeat_interval"], body["lease_seconds"]) == (30, 120)
    assert len(body["credential"]) >= 43


async def test_register_errors_use_the_error_body(client, sessionmaker):
    response = await client.post(
        "/v1/followers/register",
        json={"join_token": "nope", "protocol_version": 1, "capabilities": CAPABILITIES},
    )
    assert (response.status_code, response.json()["code"]) == (401, "unauthorized")
    token = await join_token(sessionmaker)
    response = await client.post(
        "/v1/followers/register",
        json={"join_token": token, "protocol_version": 99, "capabilities": CAPABILITIES},
    )
    assert (response.status_code, response.json()["code"]) == (409, "protocol_version")


async def test_calls_without_a_valid_credential_are_refused(client, sessionmaker, factory):
    assert (await client.post("/v1/jobs/claim")).status_code == 401
    bad = {"Authorization": "Bearer nonsense"}
    assert (await client.post("/v1/jobs/claim", headers=bad)).status_code == 401
    _, credential = await factory.follower(state="revoked")
    revoked = {"Authorization": f"Bearer {credential}"}
    response = await client.post("/v1/jobs/claim", headers=revoked)
    assert (response.status_code, response.json()["code"]) == (403, "forbidden")


async def test_claim_with_nothing_queued_is_204_with_retry_after(client, sessionmaker):
    headers = await register(client, sessionmaker)
    response = await client.post("/v1/jobs/claim", headers=headers)
    assert response.status_code == 204
    assert response.headers["retry-after"] == "10"


async def test_the_whole_happy_path(client, sessionmaker, factory, tmp_path):
    await queue_one(sessionmaker, factory, tmp_path, data=b"the recording")
    headers = await register(client, sessionmaker)
    claimed = await claim(client, headers)
    assert claimed.settings.model == "distil-large-v3"
    assert claimed.settings.compute_type == "int8"
    assert claimed.vocabulary.version == 0
    download = await client.get(claimed.download_url.url)
    assert download.content == b"the recording"
    beat = await client.post(
        f"/v1/jobs/{claimed.job_id}/heartbeat",
        headers=headers,
        json={"lease_id": claimed.lease_id, "progress": 0.5},
    )
    assert beat.json() == {"directive": "continue"}
    checksums = await upload_outputs(client, claimed)
    assert (tmp_path / "transcripts" / "talks" / "one.mp3.segments.json").exists()
    submit = await client.post(
        f"/v1/jobs/{claimed.job_id}/submit",
        headers=headers,
        json={
            "lease_id": claimed.lease_id,
            "checksums": {"source": sha(b"the recording"), **checksums},
        },
    )
    assert submit.json() == {"accepted": True}
    async with sessionmaker() as session:
        (job,) = (await session.scalars(select(Job))).all()
    assert job.state == "completed"


async def test_submit_before_uploading_is_409_outputs_missing(
    client, sessionmaker, factory, tmp_path
):
    await queue_one(sessionmaker, factory, tmp_path)
    headers = await register(client, sessionmaker)
    claimed = await claim(client, headers)
    h = "a" * 64
    response = await client.post(
        f"/v1/jobs/{claimed.job_id}/submit",
        headers=headers,
        json={
            "lease_id": claimed.lease_id,
            "checksums": {"source": h, "txt": h, "srt": h, "segments_json": h},
        },
    )
    assert (response.status_code, response.json()["code"]) == (409, "outputs_missing")


async def test_a_stale_lease_is_409(client, sessionmaker, factory, tmp_path):
    await queue_one(sessionmaker, factory, tmp_path)
    headers = await register(client, sessionmaker)
    claimed = await claim(client, headers)
    response = await client.post(
        f"/v1/jobs/{claimed.job_id}/heartbeat",
        headers=headers,
        json={"lease_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert (response.status_code, response.json()["code"]) == (409, "stale_lease")


async def test_fail_with_undecodable_fails_the_job(client, sessionmaker, factory, tmp_path):
    await queue_one(sessionmaker, factory, tmp_path)
    headers = await register(client, sessionmaker)
    claimed = await claim(client, headers)
    response = await client.post(
        f"/v1/jobs/{claimed.job_id}/fail",
        headers=headers,
        json={
            "lease_id": claimed.lease_id,
            "code": "undecodable",
            "reason": "not audio",
            "retryable": False,
        },
    )
    assert response.status_code == 204
    async with sessionmaker() as session:
        assert (await session.scalars(select(Job))).one().state == "failed"


async def test_release_requeues(client, sessionmaker, factory, tmp_path):
    await queue_one(sessionmaker, factory, tmp_path)
    headers = await register(client, sessionmaker)
    claimed = await claim(client, headers)
    response = await client.post(
        f"/v1/jobs/{claimed.job_id}/release", headers=headers, json={"lease_id": claimed.lease_id}
    )
    assert response.status_code == 204
    async with sessionmaker() as session:
        assert (await session.scalars(select(Job))).one().state == "queued"


async def test_a_draining_follower_gets_no_work(client, sessionmaker, factory, tmp_path):
    await queue_one(sessionmaker, factory, tmp_path)
    headers = await register(client, sessionmaker)
    async with sessionmaker() as session:
        follower = (await session.scalars(select(Follower))).one()
        follower.state = "draining"
        await session.commit()
    assert (await client.post("/v1/jobs/claim", headers=headers)).status_code == 204


async def test_deregister_releases_the_lease(client, sessionmaker, factory, tmp_path):
    await queue_one(sessionmaker, factory, tmp_path)
    headers = await register(client, sessionmaker)
    await claim(client, headers)
    assert (await client.post("/v1/followers/deregister", headers=headers)).status_code == 204
    async with sessionmaker() as session:
        assert (await session.scalars(select(Job))).one().state == "queued"
        assert (await session.scalars(select(Follower))).one().state == "gone"


async def test_a_cuda_follower_gets_the_cuda_profile(client, sessionmaker, factory, tmp_path):
    await queue_one(sessionmaker, factory, tmp_path)
    headers = await register(client, sessionmaker, device="cuda")
    claimed = await claim(client, headers)
    assert (claimed.settings.model, claimed.settings.compute_type) == ("large-v3", "float16")


async def test_an_invalid_job_id_is_422(client, sessionmaker):
    headers = await register(client, sessionmaker)
    response = await client.post(
        "/v1/jobs/not-a-uuid/heartbeat", headers=headers, json={"lease_id": "x"}
    )
    assert (response.status_code, response.json()["code"]) == (422, "invalid_request")
