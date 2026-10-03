import asyncio
import itertools
import os
import shutil
import time
import uuid
from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, insert, select, update
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


async def test_consent_is_reevaluated_for_a_recording_missing_from_the_listing(
    sessionmaker, factory, tmp_path
):
    write(tmp_path, "consent.txt", b"talks/*.mp3\n")
    write(tmp_path, "talks/one.mp3")
    location = await factory.location()
    await scan(sessionmaker, location)
    follower, _ = await factory.follower()
    async with sessionmaker() as session:
        job = await store.claim(session, follower, now=utcnow(), lease_seconds=120)
        await session.commit()
    async with sessionmaker() as session:
        (await session.get(Job, job.id)).state = "completed"
        await session.commit()
    (tmp_path / "talks" / "one.mp3").unlink()
    await scan(sessionmaker, location)
    write(tmp_path, "consent.txt", b"other/*.mp3\n")
    await scan(sessionmaker, location)
    (recording,) = await rows(sessionmaker, Recording)
    (job,) = await rows(sessionmaker, Job)
    assert (recording.consent, recording.missing) == ("withdrawn", True)
    assert job.outputs_flagged_for_deletion is True


async def test_a_bad_pattern_fails_the_scan_and_queues_nothing(sessionmaker, factory, tmp_path):
    write(tmp_path, "consent.txt", b"talks/*.mp3\n[z-a].mp3\n")
    write(tmp_path, "talks/one.mp3")
    location = await factory.location()
    results = await scan_due_locations(sessionmaker, backends, now=utcnow(), max_attempts=3)
    assert "line 2" in results[location.name]
    assert await rows(sessionmaker, Job) == []
    assert await rows(sessionmaker, Recording) == []
    async with sessionmaker() as session:
        stored = await session.get(StorageLocation, location.id)
    assert "line 2" in stored.last_scan_error


class OverListingBackend:
    """Lists every key under the root, ignoring the prefix it was asked for."""

    def __init__(self, inner):
        self.inner = inner

    async def list(self, prefix=""):
        async for obj in self.inner.list(""):
            yield obj

    async def read_text(self, key):
        return await self.inner.read_text(key)

    async def stat(self, key):
        return await self.inner.stat(key)


async def test_keys_outside_the_input_prefix_are_ignored(sessionmaker, factory, tmp_path):
    write(tmp_path, "consent.txt", b"**\n")
    write(tmp_path, "incoming/one.mp3")
    write(tmp_path, "elsewhere/two.mp3")
    location = await factory.location(input_prefix="incoming/")
    async with sessionmaker() as session:
        loc = await session.get(StorageLocation, location.id, with_for_update=True)
        await scan_location(
            session, loc, OverListingBackend(backends(loc)), now=utcnow(), max_attempts=3
        )
        await session.commit()
    assert [r.key for r in await rows(sessionmaker, Recording)] == ["incoming/one.mp3"]


async def test_restoring_consent_requeues_only_when_no_live_job_exists(
    sessionmaker, factory, tmp_path
):
    write(tmp_path, "consent.txt", b"**/*.mp3\n")
    write(tmp_path, "talks/one.mp3")
    write(tmp_path, "talks/two.mp3")
    location = await factory.location()
    await scan(sessionmaker, location)
    follower, _ = await factory.follower()
    async with sessionmaker() as session:
        done = await store.claim(session, follower, now=utcnow(), lease_seconds=120)
        await session.commit()
    async with sessionmaker() as session:
        (await session.get(Job, done.id)).state = "completed"
        await session.commit()
    write(tmp_path, "consent.txt", b"")
    await scan(sessionmaker, location)
    write(tmp_path, "consent.txt", b"**/*.mp3\n")
    summary = await scan(sessionmaker, location)
    assert {r.consent for r in await rows(sessionmaker, Recording)} == {"consented"}
    assert summary.jobs_created == 1
    jobs = await rows(sessionmaker, Job)
    assert sorted(j.state for j in jobs) == ["cancelled", "completed", "queued"]


async def test_restoring_consent_clears_the_deletion_flag(sessionmaker, factory, tmp_path):
    write(tmp_path, "consent.txt", b"**/*.mp3\n")
    write(tmp_path, "talks/one.mp3")
    location = await factory.location()
    await scan(sessionmaker, location)
    async with sessionmaker() as session:
        (job,) = (await session.scalars(select(Job))).all()
        job.state = "completed"
        await session.commit()
    write(tmp_path, "consent.txt", b"")
    withdrawn = await scan(sessionmaker, location)
    assert withdrawn.flags_cleared == 0
    assert (await session_job(sessionmaker, job.id)).outputs_flagged_for_deletion is True
    write(tmp_path, "consent.txt", b"**/*.mp3\n")
    restored = await scan(sessionmaker, location)
    assert restored.flags_cleared == 1
    assert (await session_job(sessionmaker, job.id)).outputs_flagged_for_deletion is False
    entries = await rows(sessionmaker, AuditEntry)
    assert sorted(e.detail["flags_cleared"] for e in entries) == [0, 0, 1]


async def session_job(sessionmaker, job_id) -> Job:
    async with sessionmaker() as session:
        return await session.get(Job, job_id)


async def test_a_recording_that_reappears_is_queued_again(sessionmaker, factory, tmp_path):
    write(tmp_path, "consent.txt", b"**/*.mp3\n")
    write(tmp_path, "talks/one.mp3", b"same")
    location = await factory.location()
    await scan(sessionmaker, location)
    path = tmp_path / "talks" / "one.mp3"
    stat = path.stat()
    path.unlink()
    await scan(sessionmaker, location)
    write(tmp_path, "talks/one.mp3", b"same")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    await scan(sessionmaker, location)
    (recording,) = await rows(sessionmaker, Recording)
    assert recording.missing is False
    assert sorted(j.state for j in await rows(sessionmaker, Job)) == ["cancelled", "queued"]


async def test_a_location_with_40000_recordings_scans(sessionmaker, factory, tmp_path):
    # Every recording is in the catalogue but gone from storage, so each query of the scan
    # covers all 40 000 rows. Binding them as a Python list would exceed asyncpg's limit of
    # 32 767 parameters per statement.
    write(tmp_path, "consent.txt", b"")
    location = await factory.location()
    now = utcnow()
    rows_to_insert = [
        {
            "id": uuid.uuid4(),
            "location_id": location.id,
            "key": f"talks/{i:05d}.mp3",
            "size": 1,
            "source_version": "1-1",
            "consent": "consented",
            "first_seen_at": now,
            "last_seen_at": now,
            "missing": False,
        }
        for i in range(40_000)
    ]
    async with sessionmaker() as session:
        for start in range(0, len(rows_to_insert), 5_000):
            await session.execute(insert(Recording), rows_to_insert[start : start + 5_000])
        await session.commit()
    summary = await scan(sessionmaker, location)
    assert (summary.missing, summary.listed) == (40_000, 0)
    async with sessionmaker() as session:
        withdrawn = await session.scalar(
            select(func.count()).select_from(Recording).where(Recording.consent == "withdrawn")
        )
    assert withdrawn == 40_000


def walk_with_an_unreadable_directory(real_walk):
    def walk(top, topdown=True, onerror=None, followlinks=False):
        yield from real_walk(top, topdown=topdown, onerror=onerror, followlinks=followlinks)
        if onerror is not None:
            onerror(PermissionError(13, "Access is denied", os.path.join(top, "talks")))

    return walk


async def test_a_partial_listing_fails_the_scan_and_changes_nothing(
    sessionmaker, factory, tmp_path, monkeypatch
):
    write(tmp_path, "consent.txt", b"**/*.mp3\n")
    write(tmp_path, "talks/one.mp3")
    location = await factory.location()
    await scan(sessionmaker, location)
    (tmp_path / "talks" / "one.mp3").unlink()  # unseen: but only because the listing was partial
    monkeypatch.setattr(os, "walk", walk_with_an_unreadable_directory(os.walk))
    results = await scan_due_locations(
        sessionmaker, backends, now=utcnow() + timedelta(hours=1), max_attempts=3
    )
    assert isinstance(results[location.name], str)
    (recording,) = await rows(sessionmaker, Recording)
    (job,) = await rows(sessionmaker, Job)
    assert recording.missing is False
    assert job.state == "queued"
    async with sessionmaker() as session:
        assert (await session.get(StorageLocation, location.id)).last_scan_error


async def test_the_event_loop_keeps_running_during_a_slow_listing(
    sessionmaker, factory, tmp_path, monkeypatch
):
    write(tmp_path, "consent.txt", b"**/*.mp3\n")
    write(tmp_path, "talks/one.mp3")
    location = await factory.location()
    real_walk = os.walk

    def slow_walk(*args, **kwargs):
        time.sleep(0.5)
        yield from real_walk(*args, **kwargs)

    monkeypatch.setattr(os, "walk", slow_walk)
    ticks: list[float] = []
    stop = asyncio.Event()

    async def ticker():
        while not stop.is_set():
            ticks.append(time.monotonic())
            await asyncio.sleep(0)

    task = asyncio.create_task(ticker())
    try:
        await scan(sessionmaker, location)
    finally:
        stop.set()
        await task
    gaps = [b - a for a, b in itertools.pairwise(ticks)]
    assert max(gaps) < 0.25


async def test_a_consent_file_over_1_mib_fails_the_scan(sessionmaker, factory, tmp_path):
    write(tmp_path, "consent.txt", b"**/*.mp3\n" + b"#" * (1024 * 1024))
    write(tmp_path, "talks/one.mp3")
    location = await factory.location()
    results = await scan_due_locations(sessionmaker, backends, now=utcnow(), max_attempts=3)
    assert results[location.name] == "consent.txt is larger than 1 MiB"
    assert await rows(sessionmaker, Recording) == []
    assert await rows(sessionmaker, Job) == []


async def test_a_consent_file_that_is_not_utf8_fails_the_scan(sessionmaker, factory, tmp_path):
    write(tmp_path, "consent.txt", b"\xff\xfe\x00bad\x80\n")
    write(tmp_path, "talks/one.mp3")
    location = await factory.location()
    results = await scan_due_locations(sessionmaker, backends, now=utcnow(), max_attempts=3)
    assert isinstance(results[location.name], str)
    assert await rows(sessionmaker, Job) == []
    async with sessionmaker() as session:
        assert (await session.get(StorageLocation, location.id)).last_scan_error


async def test_an_unreadable_folder_outside_the_input_prefix_does_not_stop_the_scan(
    sessionmaker, factory, tmp_path, monkeypatch
):
    # A drive root holds folders the leader may not read (e.g. system folders); only the
    # input folder matters.
    write(tmp_path, "consent.txt", b"**/*.mp3\n")
    write(tmp_path, "incoming/one.mp3")
    real_walk = os.walk

    def walk(top, topdown=True, onerror=None, followlinks=False):
        if Path(top).resolve() == tmp_path.resolve() and onerror is not None:
            onerror(PermissionError(13, "Access is denied", str(tmp_path / "locked")))
        yield from real_walk(top, topdown=topdown, onerror=onerror, followlinks=followlinks)

    monkeypatch.setattr(os, "walk", walk)
    summary = await scan(sessionmaker, await factory.location(input_prefix="incoming/"))
    assert summary.jobs_created == 1


async def test_a_version_an_administrator_cancelled_is_not_queued_again(
    sessionmaker, factory, tmp_path
):
    write(tmp_path, "consent.txt", b"**/*.mp3\n")
    write(tmp_path, "talks/one.mp3")
    location = await factory.location()
    await scan(sessionmaker, location)
    async with sessionmaker() as session:
        (job,) = (await session.scalars(select(Job).with_for_update())).all()
        await store.cancel(
            session, job, now=utcnow(), reason="cancelled by an administrator", by="admin-1"
        )
        await session.commit()
    assert (await scan(sessionmaker, location)).jobs_created == 0
    write(tmp_path, "talks/one.mp3", b"a new version of the audio")
    assert (await scan(sessionmaker, location)).jobs_created == 1


async def test_a_requested_scan_runs_before_the_location_is_due(sessionmaker, factory, tmp_path):
    write(tmp_path, "consent.txt", b"**/*.mp3\n")
    write(tmp_path, "talks/one.mp3")
    now = utcnow()
    location = await factory.location(
        last_scan_at=now - timedelta(seconds=10), scan_requested_at=now - timedelta(seconds=1)
    )
    results = await scan_due_locations(sessionmaker, backends, now=now, max_attempts=3)
    assert results[location.name].jobs_created == 1
    async with sessionmaker() as session:
        assert (await session.get(StorageLocation, location.id)).scan_requested_at is None


async def test_a_scan_request_made_during_a_scan_is_kept(sessionmaker, factory, tmp_path):
    write(tmp_path, "consent.txt", b"**/*.mp3\n")
    write(tmp_path, "talks/one.mp3")
    now = utcnow()
    location = await factory.location(scan_requested_at=now - timedelta(seconds=5))

    def requesting_backends(loc):
        inner = backends(loc)

        class RequestsAgainWhileListing:
            async def stat(self, key):
                return await inner.stat(key)

            async def read_text(self, key):
                return await inner.read_text(key)

            async def list(self, prefix=""):
                async with sessionmaker() as other:
                    await other.execute(
                        update(StorageLocation)
                        .where(StorageLocation.id == loc.id)
                        .values(scan_requested_at=utcnow())
                    )
                    await other.commit()
                async for obj in inner.list(prefix):
                    yield obj

        return RequestsAgainWhileListing()

    results = await scan_due_locations(sessionmaker, requesting_backends, now=now, max_attempts=3)
    assert results[location.name].jobs_created == 1
    async with sessionmaker() as session:
        assert (await session.get(StorageLocation, location.id)).scan_requested_at is not None


async def test_a_requested_scan_that_fails_is_not_retried_every_tick(
    sessionmaker, factory, tmp_path
):
    now = utcnow()
    broken = await factory.location(
        backend="azure", config={}, last_scan_at=now, scan_requested_at=now
    )
    results = await scan_due_locations(sessionmaker, backends, now=now, max_attempts=3)
    assert "not available" in results[broken.name]
    async with sessionmaker() as session:
        stored = await session.get(StorageLocation, broken.id)
    assert stored.scan_requested_at is None
    assert "not available" in stored.last_scan_error
