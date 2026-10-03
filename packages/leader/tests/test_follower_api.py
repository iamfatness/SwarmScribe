import hashlib
import json
import uuid
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError
from swarmscribe_leader.api import files as file_routes
from swarmscribe_leader.app import create_app
from swarmscribe_leader.auth.followers import create_join_token
from swarmscribe_leader.clock import utcnow
from swarmscribe_leader.config import Settings
from swarmscribe_leader.db.models import (
    Follower,
    Job,
    JobAttempt,
    JobResult,
    Recording,
    SettingsProfile,
    StorageLocation,
)
from swarmscribe_leader.ingest.scanner import scan_location
from swarmscribe_leader.jobs import store
from swarmscribe_leader.jobs.reaper import reap
from swarmscribe_leader.storage.base import StorageUnavailable
from swarmscribe_leader.storage.links import LinkSigner
from swarmscribe_leader.storage.local import LocalBackend
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


async def test_an_old_upload_link_cannot_overwrite_the_new_holders_output(
    client, sessionmaker, factory, tmp_path
):
    await queue_one(sessionmaker, factory, tmp_path)
    slow = await register(client, sessionmaker)
    first = await claim(client, slow)
    # The slow follower's lease expires and another follower takes the job over.
    await reap(sessionmaker, now=utcnow() + timedelta(seconds=121), gone_after=timedelta(hours=1))
    fast = await register(client, sessionmaker)
    second = await claim(client, fast)
    assert (second.job_id, second.lease_id != first.lease_id) == (first.job_id, True)
    uploaded = await client.put(second.upload_urls.txt.url, content=b"new holder\n")
    assert uploaded.status_code == 201
    response = await client.put(first.upload_urls.txt.url, content=b"old holder\n")
    assert (response.status_code, response.json()["code"]) == (409, "stale_lease")
    assert (tmp_path / "transcripts" / "talks" / "one.mp3.txt").read_bytes() == b"new holder\n"


async def test_a_cancelled_jobs_upload_link_is_refused(client, sessionmaker, factory, tmp_path):
    await queue_one(sessionmaker, factory, tmp_path)
    headers = await register(client, sessionmaker)
    claimed = await claim(client, headers)
    async with sessionmaker() as session:
        job = await session.get(Job, uuid.UUID(claimed.job_id), with_for_update=True)
        await store.cancel(session, job, now=utcnow(), reason="consent withdrawn")
        await session.commit()
    response = await client.put(claimed.upload_urls.txt.url, content=b"too late\n")
    assert (response.status_code, response.json()["code"]) == (409, "stale_lease")
    assert not (tmp_path / "transcripts" / "talks" / "one.mp3.txt").exists()


async def test_submit_with_a_checksum_that_does_not_match_storage_is_409(
    client, sessionmaker, factory, tmp_path
):
    await queue_one(sessionmaker, factory, tmp_path, data=b"the recording")
    headers = await register(client, sessionmaker)
    claimed = await claim(client, headers)
    checksums = await upload_outputs(client, claimed)
    response = await client.post(
        f"/v1/jobs/{claimed.job_id}/submit",
        headers=headers,
        json={
            "lease_id": claimed.lease_id,
            "checksums": {"source": sha(b"the recording"), **checksums, "srt": "b" * 64},
        },
    )
    assert (response.status_code, response.json()["code"]) == (409, "checksum_mismatch")
    async with sessionmaker() as session:
        assert (await session.scalars(select(Job))).one().state == "leased"


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


async def follower_state(sessionmaker) -> str:
    async with sessionmaker() as session:
        return (await session.scalars(select(Follower))).one().state


async def test_deregister_leaves_a_draining_follower_draining(
    client, sessionmaker, factory, tmp_path
):
    await queue_one(sessionmaker, factory, tmp_path)
    headers = await register(client, sessionmaker)
    async with sessionmaker() as session:
        (await session.scalars(select(Follower))).one().state = "draining"
        await session.commit()
    assert (await client.post("/v1/jobs/claim", headers=headers)).status_code == 204
    assert (await client.post("/v1/followers/deregister", headers=headers)).status_code == 204
    assert (await client.post("/v1/jobs/claim", headers=headers)).status_code == 204
    assert await follower_state(sessionmaker) == "draining"


async def test_a_silent_draining_follower_cannot_escape_the_drain_via_the_reaper(
    client, sessionmaker, factory, tmp_path
):
    await queue_one(sessionmaker, factory, tmp_path)
    headers = await register(client, sessionmaker)
    async with sessionmaker() as session:
        follower = (await session.scalars(select(Follower))).one()
        follower.state = "draining"
        follower.last_seen_at = utcnow() - timedelta(hours=1)
        await session.commit()
    await reap(sessionmaker, now=utcnow(), gone_after=timedelta(minutes=10))
    assert await follower_state(sessionmaker) == "draining"
    assert (await client.post("/v1/jobs/claim", headers=headers)).status_code == 204
    assert await follower_state(sessionmaker) == "draining"


async def test_a_gone_follower_is_reactivated_by_its_next_call(
    client, sessionmaker, factory, tmp_path
):
    await queue_one(sessionmaker, factory, tmp_path)
    headers = await register(client, sessionmaker)
    assert (await client.post("/v1/followers/deregister", headers=headers)).status_code == 204
    assert await follower_state(sessionmaker) == "gone"
    assert (await client.post("/v1/jobs/claim", headers=headers)).status_code == 200
    assert await follower_state(sessionmaker) == "active"


async def test_deregister_twice_is_safe(client, sessionmaker):
    headers = await register(client, sessionmaker)
    for _ in range(2):
        assert (await client.post("/v1/followers/deregister", headers=headers)).status_code == 204
    assert await follower_state(sessionmaker) == "gone"


class Broken:
    def __init__(self, real, broken_location_id):
        self.real, self.broken_location_id = real, broken_location_id

    def __call__(self, location):
        if location.id == self.broken_location_id:
            raise StorageUnavailable("secret-key-material")
        return self.real(location)


async def two_locations(sessionmaker, factory, tmp_path):
    """A broken location whose job is older, and a healthy one."""
    write(tmp_path, "consent.txt", b"**/*.mp3\n")
    broken = await factory.location()
    recording = await factory.recording(broken, key="talks/broken.mp3")
    await factory.job(recording)
    healthy_root = tmp_path / "healthy"
    healthy = await factory.location(config={"root": str(healthy_root)})
    write(healthy_root, "talks/ok.mp3", b"ok")
    await factory.job(await factory.recording(healthy, key="talks/ok.mp3"))
    return broken, healthy


async def job_rows(sessionmaker):
    async with sessionmaker() as session:
        jobs = (await session.scalars(select(Job).order_by(Job.created_at))).all()
        attempts = (await session.scalars(select(JobAttempt))).all()
    return jobs, attempts


async def test_an_unbuildable_job_does_not_stall_the_pool(
    client, app, sessionmaker, factory, tmp_path, caplog
):
    broken, healthy = await two_locations(sessionmaker, factory, tmp_path)
    app.state.backend_factory = Broken(app.state.backend_factory, broken.id)
    headers = await register(client, sessionmaker)
    with caplog.at_level("WARNING"):
        claimed = await claim(client, headers)
    jobs, attempts = await job_rows(sessionmaker)
    by_state = {job.state: job for job in jobs}
    assert str(by_state["leased"].id) == claimed.job_id
    assert (by_state["queued"].attempts, len(attempts)) == (0, 1)
    assert "secret-key-material" not in caplog.text


async def test_only_an_unbuildable_job_gives_204(client, app, sessionmaker, factory, tmp_path):
    broken, _ = await two_locations(sessionmaker, factory, tmp_path)
    async with sessionmaker() as session:
        await session.execute(
            Job.__table__.delete().where(
                Job.recording_id.in_(select(Recording.id).where(Recording.location_id != broken.id))
            )
        )
        await session.commit()
    app.state.backend_factory = Broken(app.state.backend_factory, broken.id)
    headers = await register(client, sessionmaker)
    response = await client.post("/v1/jobs/claim", headers=headers)
    assert (response.status_code, response.headers["retry-after"]) == (204, "10")
    jobs, attempts = await job_rows(sessionmaker)
    assert ([job.state for job in jobs], [job.attempts for job in jobs], attempts) == (
        ["queued"],
        [0],
        [],
    )


async def test_a_whole_broken_location_ahead_of_the_queue_does_not_stall_it(
    client, app, sessionmaker, factory, tmp_path
):
    broken = await factory.location()
    for i in range(6):
        await factory.job(await factory.recording(broken, key=f"talks/broken-{i}.mp3"))
    healthy_root = tmp_path / "healthy"
    write(healthy_root, "talks/ok.mp3", b"ok")
    healthy = await factory.location(config={"root": str(healthy_root)})
    good = await factory.job(await factory.recording(healthy, key="talks/ok.mp3"))
    app.state.backend_factory = Broken(app.state.backend_factory, broken.id)
    headers = await register(client, sessionmaker)
    started = utcnow()
    response = await client.post("/v1/jobs/claim", headers=headers)
    if response.status_code == 204:  # the cap of 5 skips per claim was hit first
        response = await client.post("/v1/jobs/claim", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["job_id"] == str(good.id)
    jobs, attempts = await job_rows(sessionmaker)
    pushed_back = [job for job in jobs if job.id != good.id]
    assert len(pushed_back) == 6
    assert all(job.state == "queued" and job.attempts == 0 for job in pushed_back)
    assert all(job.available_at >= started + timedelta(seconds=59) for job in pushed_back)
    assert len(attempts) == 1


async def test_a_pushed_back_job_is_not_offered_again_until_it_is_available(
    client, app, sessionmaker, factory, tmp_path
):
    broken, _ = await two_locations(sessionmaker, factory, tmp_path)
    app.state.backend_factory = Broken(app.state.backend_factory, broken.id)
    headers = await register(client, sessionmaker)
    await claim(client, headers)
    app.state.backend_factory = app.state.backend_factory.real  # the location recovers
    response = await client.post("/v1/jobs/claim", headers=headers)
    assert response.status_code == 204
    jobs, _ = await job_rows(sessionmaker)
    assert sorted(job.state for job in jobs) == ["leased", "queued"]


class Buggy(Broken):
    def __call__(self, location):
        if location.id == self.broken_location_id:
            raise RuntimeError("unexpected")
        return self.real(location)


async def test_an_unexpected_claim_build_error_is_logged_with_its_traceback(
    client, app, sessionmaker, factory, tmp_path, caplog
):
    broken, _ = await two_locations(sessionmaker, factory, tmp_path)
    app.state.backend_factory = Buggy(app.state.backend_factory, broken.id)
    headers = await register(client, sessionmaker)
    with caplog.at_level("WARNING"):
        await claim(client, headers)
    (record,) = [r for r in caplog.records if "could not be built" in r.getMessage()]
    assert record.exc_info is not None
    caplog.clear()
    app.state.backend_factory = Broken(app.state.backend_factory.real, broken.id)
    async with sessionmaker() as session:
        await session.execute(update(Job).values(available_at=utcnow() - timedelta(seconds=1)))
        await session.commit()
    with caplog.at_level("WARNING"):
        await client.post("/v1/jobs/claim", headers=headers)
    (record,) = [r for r in caplog.records if "could not be built" in r.getMessage()]
    assert record.exc_info is None


async def test_no_settings_profile_for_the_device_gives_204(
    client, sessionmaker, factory, tmp_path, caplog
):
    await queue_one(sessionmaker, factory, tmp_path)
    (before,), _ = await job_rows(sessionmaker)
    headers = await register(client, sessionmaker, device="cuda")

    async def set_cuda_profiles_device(old: str, new: str) -> None:
        async with sessionmaker() as session:
            await session.execute(
                update(SettingsProfile).where(SettingsProfile.device == old).values(device=new)
            )
            await session.commit()

    await set_cuda_profiles_device("cuda", "retired")
    try:
        with caplog.at_level("WARNING"):
            response = await client.post("/v1/jobs/claim", headers=headers)
        assert response.status_code == 204
        jobs, attempts = await job_rows(sessionmaker)
        assert ([job.state for job in jobs], attempts) == (["queued"], [])
        assert jobs[0].available_at == before.available_at  # not even pushed back
    finally:
        await set_cuda_profiles_device("retired", "cuda")
    assert "cuda" in caplog.text


async def test_claim_with_the_database_unreachable_is_503_unavailable():
    settings = Settings(
        database_url="postgresql://u:p@127.0.0.1:1/none",
        public_url="http://leader",
        link_key="k" * 32,
    )
    application = create_app(settings, background=False)
    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://leader"
        ) as http:
            response = await http.post("/v1/jobs/claim", headers={"Authorization": "Bearer x"})
    assert response.status_code == 503
    assert response.json()["code"] == "unavailable"
    assert response.headers["retry-after"] == "10"


async def test_a_database_error_during_claim_is_503_unavailable(app, client, monkeypatch):
    class Failing:
        def __call__(self):
            raise DBAPIError("select 1", {}, ConnectionError("server closed the connection"))

    monkeypatch.setattr(app.state, "sessionmaker", Failing())
    response = await client.post("/v1/jobs/claim", headers={"Authorization": "Bearer x"})
    assert (response.status_code, response.json()["code"]) == (503, "unavailable")
    assert response.headers["retry-after"] == "10"


async def test_another_followers_credential_cannot_touch_a_lease(
    client, sessionmaker, factory, tmp_path
):
    await queue_one(sessionmaker, factory, tmp_path)
    owner = await register(client, sessionmaker)
    claimed = await claim(client, owner)
    intruder = await register(client, sessionmaker)
    h = "a" * 64
    lease = {"lease_id": claimed.lease_id}
    attempts = {
        "heartbeat": lease,
        "release": lease,
        "fail": {**lease, "code": "other", "reason": "x", "retryable": True},
        "submit": {
            **lease,
            "checksums": {"source": h, "txt": h, "srt": h, "segments_json": h},
        },
    }
    for action, body in attempts.items():
        response = await client.post(
            f"/v1/jobs/{claimed.job_id}/{action}", headers=intruder, json=body
        )
        assert (response.status_code, response.json()["code"]) == (409, "stale_lease"), action
    assert (await client.post("/v1/followers/deregister", headers=intruder)).status_code == 204
    jobs, _ = await job_rows(sessionmaker)
    assert (jobs[0].state, str(jobs[0].lease_id)) == ("leased", claimed.lease_id)


def segments_document(source_sha: str, segments: list[dict]) -> bytes:
    return json.dumps(
        {
            "schema_version": 1,
            "source_checksum": source_sha,
            "duration": 12.5,
            "device": "cpu",
            "engine_version": "0.1.0",
            "settings": {"model": "distil-large-v3", "compute_type": "int8"},
            "vocabulary_version": 0,
            "vocabulary_terms_used": [],
            "corrections_applied": [],
            "segments": segments,
        }
    ).encode()


ONE_SEGMENT = [{"start": 0.0, "end": 1.0, "text": "one two", "words": []}]


async def submit_outputs(client, headers, claimed, source: bytes, txt, srt, segments):
    checksums = {"source": sha(source)}
    for name, body in (("txt", txt), ("srt", srt), ("segments_json", segments)):
        link = getattr(claimed.upload_urls, name)
        assert (await client.put(link.url, content=body)).status_code == 201
        checksums[name] = sha(body)
    return await client.post(
        f"/v1/jobs/{claimed.job_id}/submit",
        headers=headers,
        json={"lease_id": claimed.lease_id, "checksums": checksums},
    )


async def test_a_recording_without_speech_completes_with_an_empty_transcript(
    client, sessionmaker, factory, tmp_path
):
    await queue_one(sessionmaker, factory, tmp_path, data=b"quiet")
    headers = await register(client, sessionmaker)
    claimed = await claim(client, headers)
    response = await submit_outputs(
        client, headers, claimed, b"quiet", b"", b"", segments_document(sha(b"quiet"), [])
    )
    assert response.status_code == 200, response.text
    async with sessionmaker() as session:
        job = await session.get(Job, uuid.UUID(claimed.job_id))
        result = (await session.scalars(select(JobResult))).one()
    assert (job.state, result.no_speech) == ("completed", True)


@pytest.mark.parametrize(
    "txt, srt, segments, code",
    [
        (b"", b"", ONE_SEGMENT, "outputs_inconsistent"),
        (b"", b"1\n00:00:00,000 --> 00:00:01,000\none two\n", [], "outputs_inconsistent"),
        (b"", b"", None, "outputs_inconsistent"),
        (b"one two\n", b"1\n00:00:00,000 --> 00:00:01,000\none two\n", b"", "outputs_missing"),
    ],
    ids=["empty-text-with-segments", "empty-text-only", "unparseable-segments", "empty-segments"],
)
async def test_an_inconsistent_empty_result_is_refused(
    client, sessionmaker, factory, tmp_path, txt, srt, segments, code
):
    await queue_one(sessionmaker, factory, tmp_path, data=b"quiet")
    headers = await register(client, sessionmaker)
    claimed = await claim(client, headers)
    if segments is None:
        body = b"{not json"
    elif isinstance(segments, bytes):
        body = segments
    else:
        body = segments_document(sha(b"quiet"), segments)
    response = await submit_outputs(client, headers, claimed, b"quiet", txt, srt, body)
    assert (response.status_code, response.json()["code"]) == (409, code)
    async with sessionmaker() as session:
        job = await session.get(Job, uuid.UUID(claimed.job_id))
    assert job.state == "leased"


async def test_submit_does_not_hold_the_followers_row_while_hashing(
    client, app, sessionmaker, factory, tmp_path, monkeypatch
):
    await queue_one(sessionmaker, factory, tmp_path)
    headers = await register(client, sessionmaker)
    claimed = await claim(client, headers)
    checksums = await upload_outputs(client, claimed)
    checksums["source"] = sha(b"audio bytes")
    follower_locked = []
    real_sha256 = LocalBackend.sha256

    async def watching_sha256(self, key):
        async with sessionmaker() as other:
            row = await other.scalar(
                select(Follower).with_for_update(skip_locked=True).limit(1)
            )
            follower_locked.append(row is None)
            await other.rollback()
        return await real_sha256(self, key)

    monkeypatch.setattr(LocalBackend, "sha256", watching_sha256)
    response = await client.post(
        f"/v1/jobs/{claimed.job_id}/submit",
        headers=headers,
        json={"lease_id": claimed.lease_id, "checksums": checksums},
    )
    assert response.status_code == 200, response.text
    assert follower_locked and not any(follower_locked)


async def test_an_upload_racing_a_completed_submit_is_refused_and_leaves_the_file(
    client, app, sessionmaker, factory, tmp_path, monkeypatch
):
    await queue_one(sessionmaker, factory, tmp_path)
    headers = await register(client, sessionmaker)
    claimed = await claim(client, headers)
    checksums = await upload_outputs(client, claimed)
    checksums["source"] = sha(b"audio bytes")
    real = file_routes._replace_if_lease_current

    async def submit_first(*args, **kwargs):
        response = await client.post(
            f"/v1/jobs/{claimed.job_id}/submit",
            headers=headers,
            json={"lease_id": claimed.lease_id, "checksums": checksums},
        )
        assert response.status_code == 200, response.text
        return await real(*args, **kwargs)

    monkeypatch.setattr(file_routes, "_replace_if_lease_current", submit_first)
    response = await client.put(claimed.upload_urls.txt.url, content=b"replacement text\n")
    assert (response.status_code, response.json()["code"]) == (409, "stale_lease")
    stored = next(tmp_path.rglob("one.mp3.txt")).read_bytes()
    assert sha(stored) == checksums["txt"]


async def test_an_output_replaced_while_it_is_hashed_makes_submit_refuse(
    client, app, sessionmaker, factory, tmp_path, monkeypatch
):
    await queue_one(sessionmaker, factory, tmp_path)
    headers = await register(client, sessionmaker)
    claimed = await claim(client, headers)
    checksums = await upload_outputs(client, claimed)
    checksums["source"] = sha(b"audio bytes")
    real_sha256 = LocalBackend.sha256
    replaced = []

    async def replacing_sha256(self, key):
        digest = await real_sha256(self, key)
        if key.endswith(".txt") and not replaced:
            replaced.append(True)
            response = await client.put(claimed.upload_urls.txt.url, content=b"other text\n")
            assert response.status_code == 201
        return digest

    monkeypatch.setattr(LocalBackend, "sha256", replacing_sha256)
    response = await client.post(
        f"/v1/jobs/{claimed.job_id}/submit",
        headers=headers,
        json={"lease_id": claimed.lease_id, "checksums": checksums},
    )
    assert (response.status_code, response.json()["code"]) == (409, "outputs_changed")
    async with sessionmaker() as session:
        job = await session.get(Job, uuid.UUID(claimed.job_id))
    assert job.state == "leased"


async def test_submit_holds_no_transaction_open_while_hashing(
    client, app, sessionmaker, factory, tmp_path, monkeypatch
):
    await queue_one(sessionmaker, factory, tmp_path)
    headers = await register(client, sessionmaker)
    claimed = await claim(client, headers)
    checksums = await upload_outputs(client, claimed)
    checksums["source"] = sha(b"audio bytes")
    real_sha256 = LocalBackend.sha256
    idle_in_transaction = []

    async def watching_sha256(self, key):
        async with sessionmaker() as other:
            count = await other.scalar(
                text(
                    "select count(*) from pg_stat_activity "
                    "where state like 'idle in transaction%' "
                    "and datname = current_database() and pid <> pg_backend_pid()"
                )
            )
            idle_in_transaction.append(count)
        return await real_sha256(self, key)

    monkeypatch.setattr(LocalBackend, "sha256", watching_sha256)
    response = await client.post(
        f"/v1/jobs/{claimed.job_id}/submit",
        headers=headers,
        json={"lease_id": claimed.lease_id, "checksums": checksums},
    )
    assert response.status_code == 200, response.text
    assert idle_in_transaction and idle_in_transaction == [0] * len(idle_in_transaction)


async def test_an_empty_srt_beside_a_text_is_inconsistent(client, sessionmaker, factory, tmp_path):
    await queue_one(sessionmaker, factory, tmp_path, data=b"quiet")
    headers = await register(client, sessionmaker)
    claimed = await claim(client, headers)
    response = await submit_outputs(
        client, headers, claimed, b"quiet", b"one two\n", b"", segments_document(sha(b"quiet"), [])
    )
    assert (response.status_code, response.json()["code"]) == (409, "outputs_inconsistent")


async def test_a_transcript_with_a_zero_segment_document_is_a_normal_result(
    client, sessionmaker, factory, tmp_path
):
    # Decision: only an EMPTY txt and srt make a no-speech result. A non-empty transcript
    # beside a segments.json with no segments is accepted as a normal result.
    await queue_one(sessionmaker, factory, tmp_path, data=b"quiet")
    headers = await register(client, sessionmaker)
    claimed = await claim(client, headers)
    srt = b"1\n00:00:00,000 --> 00:00:01,000\none two\n"
    response = await submit_outputs(
        client, headers, claimed, b"quiet", b"one two\n", srt, segments_document(sha(b"quiet"), [])
    )
    assert response.status_code == 200, response.text
    async with sessionmaker() as session:
        result = (await session.scalars(select(JobResult))).one()
    assert result.no_speech is False


async def test_a_claim_carries_its_locations_channel_settings(
    client, sessionmaker, factory, tmp_path
):
    location = await queue_one(sessionmaker, factory, tmp_path)
    async with sessionmaker() as session:
        row = await session.get(StorageLocation, location.id)
        row.channel_mode = "stereo_split"
        row.channel_labels = ["Agent", "Customer"]
        await session.commit()
    headers = await register(client, sessionmaker)
    claimed = await claim(client, headers)
    assert (claimed.settings.channel_mode, claimed.settings.channel_labels) == (
        "stereo_split",
        ("Agent", "Customer"),
    )


async def test_a_claim_from_a_location_left_at_the_defaults_is_mono(
    client, sessionmaker, factory, tmp_path
):
    await queue_one(sessionmaker, factory, tmp_path)
    headers = await register(client, sessionmaker)
    claimed = await claim(client, headers)
    assert (claimed.settings.channel_mode, claimed.settings.channel_labels) == (
        "mono",
        ("Left", "Right"),
    )


async def test_the_channel_settings_come_from_the_recordings_location_not_the_output(
    client, sessionmaker, factory, tmp_path
):
    location = await queue_one(sessionmaker, factory, tmp_path)
    output = await factory.location(channel_mode="auto", channel_labels=["Host", "Guest"])
    async with sessionmaker() as session:
        row = await session.get(StorageLocation, location.id)
        row.output_location_id = output.id
        row.channel_mode = "stereo_split"
        await session.commit()
    headers = await register(client, sessionmaker)
    claimed = await claim(client, headers)
    assert (claimed.settings.channel_mode, claimed.settings.channel_labels) == (
        "stereo_split",
        ("Left", "Right"),
    )


async def test_a_location_with_labels_the_protocol_refuses_is_skipped_not_a_500(
    client, sessionmaker, factory, tmp_path
):
    # Equal labels pass the database CHECK but fail JobSettings validation.
    broken, healthy = await two_locations(sessionmaker, factory, tmp_path)
    async with sessionmaker() as session:
        row = await session.get(StorageLocation, broken.id)
        row.channel_mode = "stereo_split"
        row.channel_labels = ["Same", "Same"]
        await session.commit()
    headers = await register(client, sessionmaker)
    started = utcnow()
    claimed = await claim(client, headers)
    jobs, attempts = await job_rows(sessionmaker)
    by_state = {job.state: job for job in jobs}
    # The broken job was tried and pushed back, not merely sorted behind the healthy one.
    assert by_state["queued"].available_at >= started + timedelta(seconds=59)
    assert str(by_state["leased"].id) == claimed.job_id
    assert (claimed.settings.channel_mode, by_state["queued"].attempts, len(attempts)) == (
        "mono",
        0,
        1,
    )
