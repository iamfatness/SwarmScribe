import shutil
from datetime import timedelta

from sqlalchemy import select
from swarmscribe_leader.clock import utcnow
from swarmscribe_leader.db.models import AuditEntry, Job, Recording, StorageLocation
from swarmscribe_leader.ingest.scanner import scan_due_locations, scan_location
from swarmscribe_leader.jobs import store
from swarmscribe_leader.storage.links import LinkSigner
from swarmscribe_leader.storage.registry import backend_for

SIGNER = LinkSigner(b"k" * 32)


def write(root, key, data=b"audio"):
    path = root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def backends(location):
    return backend_for(location, signer=SIGNER, public_url="https://leader")


async def scan(sessionmaker, location, *, now=None):
    async with sessionmaker() as session:
        loc = await session.get(StorageLocation, location.id, with_for_update=True)
        summary = await scan_location(
            session, loc, backends(loc), now=now or utcnow(), max_attempts=3
        )
        await session.commit()
    return summary


async def rows(sessionmaker, model):
    async with sessionmaker() as session:
        return list((await session.scalars(select(model))).all())


async def test_only_consented_recordings_become_jobs(sessionmaker, factory, tmp_path):
    write(tmp_path, "consent.txt", b"talks/*.mp3\n")
    write(tmp_path, "talks/one.mp3")
    write(tmp_path, "talks/two.mp3")
    write(tmp_path, "private/three.mp3")
    write(tmp_path, "talks/notes.txt")
    location = await factory.location(pool="gpu", required_device="cuda")
    summary = await scan(sessionmaker, location)
    recordings = {r.key: r.consent for r in await rows(sessionmaker, Recording)}
    assert recordings == {
        "talks/one.mp3": "consented",
        "talks/two.mp3": "consented",
        "private/three.mp3": "not_consented",
    }
    jobs = await rows(sessionmaker, Job)
    assert len(jobs) == 2
    assert {(j.state, j.pool, j.required_device, j.max_attempts) for j in jobs} == {
        ("queued", "gpu", "cuda", 3)
    }
    assert (summary.listed, summary.consented, summary.jobs_created) == (3, 2, 2)


async def test_without_a_consent_file_nothing_is_queued(sessionmaker, factory, tmp_path):
    write(tmp_path, "talks/one.mp3")
    await scan(sessionmaker, await factory.location())
    assert await rows(sessionmaker, Job) == []
    assert [r.consent for r in await rows(sessionmaker, Recording)] == ["not_consented"]


async def test_rescanning_an_unchanged_folder_creates_nothing(sessionmaker, factory, tmp_path):
    write(tmp_path, "consent.txt", b"**/*.mp3\n")
    write(tmp_path, "talks/one.mp3")
    location = await factory.location()
    await scan(sessionmaker, location)
    summary = await scan(sessionmaker, location)
    assert summary.jobs_created == 0
    assert len(await rows(sessionmaker, Job)) == 1


async def test_a_changed_file_cancels_the_old_job_and_queues_the_new_version(
    sessionmaker,
    factory,
    tmp_path,
):
    write(tmp_path, "consent.txt", b"**/*.mp3\n")
    write(tmp_path, "talks/one.mp3", b"first")
    location = await factory.location()
    await scan(sessionmaker, location)
    write(tmp_path, "talks/one.mp3", b"second, longer")
    await scan(sessionmaker, location)
    jobs = sorted(await rows(sessionmaker, Job), key=lambda j: j.created_at)
    assert [j.state for j in jobs] == ["cancelled", "queued"]
    assert jobs[0].failure_reason == "recording changed"
    assert jobs[0].source_version != jobs[1].source_version


async def test_withdrawn_consent_cancels_open_work_and_flags_finished_outputs(
    sessionmaker,
    factory,
    tmp_path,
):
    write(tmp_path, "consent.txt", b"talks/*.mp3\n")
    write(tmp_path, "talks/one.mp3")
    write(tmp_path, "talks/two.mp3")
    location = await factory.location()
    await scan(sessionmaker, location)
    follower, _ = await factory.follower()
    async with sessionmaker() as session:
        first = await store.claim(session, follower, now=utcnow(), lease_seconds=120)
        await session.commit()
    async with sessionmaker() as session:
        done = await session.get(Job, first.id)
        done.state = "completed"
        await session.commit()
    write(tmp_path, "consent.txt", b"# nothing allowed any more\n")
    summary = await scan(sessionmaker, location)
    assert summary.withdrawn == 2
    jobs = {j.id: j for j in await rows(sessionmaker, Job)}
    assert jobs[first.id].outputs_flagged_for_deletion is True
    others = [j for j in jobs.values() if j.id != first.id]
    assert [(j.state, j.failure_reason) for j in others] == [("cancelled", "consent withdrawn")]
    assert {r.consent for r in await rows(sessionmaker, Recording)} == {"withdrawn"}


async def test_a_leased_job_whose_consent_is_withdrawn_is_told_to_cancel(
    sessionmaker,
    factory,
    tmp_path,
):
    write(tmp_path, "consent.txt", b"talks/*.mp3\n")
    write(tmp_path, "talks/one.mp3")
    location = await factory.location()
    await scan(sessionmaker, location)
    follower, _ = await factory.follower()
    async with sessionmaker() as session:
        job = await store.claim(session, follower, now=utcnow(), lease_seconds=120)
        await session.commit()
    write(tmp_path, "consent.txt", b"")
    await scan(sessionmaker, location)
    async with sessionmaker() as session:
        directive = await store.heartbeat(
            session, job.id, str(job.lease_id), follower, now=utcnow(), lease_seconds=120
        )
    assert directive == "cancel"


async def test_a_missing_file_cancels_its_open_job(sessionmaker, factory, tmp_path):
    write(tmp_path, "consent.txt", b"**/*.mp3\n")
    write(tmp_path, "talks/one.mp3")
    location = await factory.location()
    await scan(sessionmaker, location)
    (tmp_path / "talks" / "one.mp3").unlink()
    summary = await scan(sessionmaker, location)
    assert summary.missing == 1
    (recording,) = await rows(sessionmaker, Recording)
    (job,) = await rows(sessionmaker, Job)
    assert recording.missing is True
    assert (job.state, job.failure_reason) == ("cancelled", "recording missing")


async def test_a_failed_job_is_not_recreated_by_scanning(sessionmaker, factory, tmp_path):
    write(tmp_path, "consent.txt", b"**/*.mp3\n")
    write(tmp_path, "talks/one.mp3")
    location = await factory.location()
    await scan(sessionmaker, location)
    async with sessionmaker() as session:
        (job,) = (await session.scalars(select(Job))).all()
        job.state = "failed"
        await session.commit()
    summary = await scan(sessionmaker, location)
    assert summary.jobs_created == 0


async def test_patterns_are_relative_to_the_input_prefix(sessionmaker, factory, tmp_path):
    write(tmp_path, "consent.txt", b"*.mp3\n")
    write(tmp_path, "incoming/one.mp3")
    write(tmp_path, "elsewhere/two.mp3")
    await scan(sessionmaker, await factory.location(input_prefix="incoming/"))
    assert [r.key for r in await rows(sessionmaker, Recording)] == ["incoming/one.mp3"]
    assert len(await rows(sessionmaker, Job)) == 1


async def test_a_scan_is_audited_and_timestamped(sessionmaker, factory, tmp_path):
    write(tmp_path, "consent.txt", b"**/*.mp3\n")
    write(tmp_path, "talks/one.mp3")
    location = await factory.location()
    now = utcnow()
    await scan(sessionmaker, location, now=now)
    (entry,) = await rows(sessionmaker, AuditEntry)
    assert (entry.actor, entry.action) == ("system", "location.scan")
    assert entry.detail["jobs_created"] == 1
    assert "talks/one.mp3" not in str(entry.detail)
    async with sessionmaker() as session:
        stored = await session.get(StorageLocation, location.id)
    assert (stored.last_scan_at, stored.last_scan_error) == (now, None)


async def test_scan_due_locations_skips_locations_not_yet_due(sessionmaker, factory, tmp_path):
    write(tmp_path, "consent.txt", b"**/*.mp3\n")
    write(tmp_path, "talks/one.mp3")
    now = utcnow()
    due = await factory.location(name="due")
    await factory.location(name="recent", last_scan_at=now - timedelta(seconds=10))
    await factory.location(name="disabled", enabled=False)
    results = await scan_due_locations(sessionmaker, backends, now=now, max_attempts=3)
    assert set(results) == {"due"}
    assert results["due"].jobs_created == 1
    async with sessionmaker() as session:
        assert (await session.get(StorageLocation, due.id)).last_scan_at == now


async def test_a_failing_location_records_its_error_and_others_still_scan(
    sessionmaker,
    factory,
    tmp_path,
):
    write(tmp_path, "consent.txt", b"**/*.mp3\n")
    write(tmp_path, "talks/one.mp3")
    broken = await factory.location(name="broken", backend="azure", config={})
    await factory.location(name="fine")
    now = utcnow()
    results = await scan_due_locations(sessionmaker, backends, now=now, max_attempts=3)
    assert "not available" in results["broken"]
    assert results["fine"].jobs_created == 1
    async with sessionmaker() as session:
        stored = await session.get(StorageLocation, broken.id)
    assert "not available" in stored.last_scan_error
    assert stored.last_scan_at == now


async def test_a_location_whose_root_vanished_fails_without_marking_anything_missing(
    sessionmaker, factory, tmp_path
):
    root = tmp_path / "gone"
    write(root, "consent.txt", b"**/*.mp3\n")
    write(root, "talks/one.mp3")
    location = await factory.location(config={"root": str(root)})
    await scan(sessionmaker, location)
    shutil.rmtree(root)
    later = utcnow() + timedelta(hours=1)
    results = await scan_due_locations(sessionmaker, backends, now=later, max_attempts=3)
    assert isinstance(results[location.name], str)
    (recording,) = await rows(sessionmaker, Recording)
    (job,) = await rows(sessionmaker, Job)
    assert recording.missing is False
    assert job.state == "queued"
    async with sessionmaker() as session:
        stored = await session.get(StorageLocation, location.id)
    assert stored.last_scan_error
