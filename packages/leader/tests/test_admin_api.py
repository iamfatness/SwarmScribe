import asyncio
import random
import re
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select, update
from swarmscribe_leader.auth.followers import create_join_token
from swarmscribe_leader.clock import utcnow
from swarmscribe_leader.db.models import (
    AuditEntry,
    Follower,
    Job,
    JobAttempt,
    JoinToken,
    StorageLocation,
)
from swarmscribe_leader.ingest.scanner import scan_due_locations
from swarmscribe_leader.jobs import store

CAPABILITIES = {
    "device": "cpu",
    "models": ["distil-large-v3"],
    "engine_version": "0.1.0",
    "pool": "default",
}


def write(root, key, data=b"audio"):
    path = root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


async def audit_rows(sessionmaker, action):
    async with sessionmaker() as session:
        return list(
            (await session.scalars(select(AuditEntry).where(AuditEntry.action == action))).all()
        )


async def join_token(sessionmaker, **overrides) -> tuple[uuid.UUID, str]:
    values = {
        "pool": "default",
        "expires_at": utcnow() + timedelta(days=1),
        "max_uses": 1,
        "created_by": "test",
    }
    values.update(overrides)
    async with sessionmaker() as session:
        token, plaintext = await create_join_token(session, **values)
        await session.commit()
    return token.id, plaintext


async def get(client, idp, path, role="viewer", **params):
    response = await client.get(path, headers=idp.bearer(role), params=params)
    assert response.status_code == 200, response.text
    return response.json()


async def test_status_summarises_queue_followers_and_locations(admin_client, idp, factory):
    here = await factory.location(name="here", last_scan_error="input folder 'x' is not available")
    await factory.job(await factory.recording(here, key="talks/a.mp3"))
    await factory.job(await factory.recording(here, key="talks/b.mp3"), state="failed")
    await factory.follower(state="draining")
    body = await get(admin_client, idp, "/v1/admin/status")
    assert body["jobs"] == {"queued": 1, "leased": 0, "completed": 0, "failed": 1, "cancelled": 0}
    assert body["pools"] == [{"pool": "default", "queued": 1, "leased": 0}]
    assert body["followers"] == {"active": 0, "draining": 1, "revoked": 0, "gone": 0}
    (location,) = body["locations"]
    assert (location["name"], location["recordings"], location["consented"]) == ("here", 2, 2)
    assert location["last_scan_error"] == "input folder 'x' is not available"
    assert location["scan_requested"] is False


async def test_status_counts_recent_completions_and_failed_attempts(
    admin_client, idp, factory, sessionmaker
):
    location = await factory.location()
    follower, _ = await factory.follower()
    recent = await factory.job(
        await factory.recording(location, key="talks/a.mp3"),
        state="completed",
        completed_at=utcnow(),
    )
    await factory.job(
        await factory.recording(location, key="talks/b.mp3"),
        state="completed",
        completed_at=utcnow() - timedelta(hours=2),
    )
    async with sessionmaker() as session:
        session.add(
            JobAttempt(
                job_id=recent.id,
                follower_id=follower.id,
                lease_id=uuid.uuid4(),
                started_at=utcnow(),
                ended_at=utcnow(),
                outcome="failed",
                reason="engine_error: out of memory",
            )
        )
        await session.commit()
    body = await get(admin_client, idp, "/v1/admin/status")
    assert (body["completed_last_hour"], body["failed_attempts_last_day"]) == (1, 1)


async def test_locations_are_listed(admin_client, idp, factory, tmp_path):
    await factory.location(name="here", input_prefix="incoming/")
    (row,) = await get(admin_client, idp, "/v1/admin/locations")
    assert (row["name"], row["backend"], row["root"], row["input_prefix"], row["enabled"]) == (
        "here",
        "local",
        str(tmp_path),
        "incoming/",
        True,
    )


async def test_jobs_are_listed_with_where_they_come_from_and_filtered(admin_client, idp, factory):
    here = await factory.location(name="here")
    there = await factory.location(name="there")
    failed = await factory.job(
        await factory.recording(here, key="talks/a.mp3"),
        state="failed",
        failure_reason="undecodable: no audio stream",
    )
    await factory.job(await factory.recording(there, key="talks/b.mp3"))
    rows = await get(admin_client, idp, "/v1/admin/jobs", state="failed")
    assert [(r["id"], r["location"], r["key"], r["failure_reason"]) for r in rows] == [
        (str(failed.id), "here", "talks/a.mp3", "undecodable: no audio stream")
    ]
    rows = await get(admin_client, idp, "/v1/admin/jobs", location="there")
    assert [r["location"] for r in rows] == ["there"]
    assert len(await get(admin_client, idp, "/v1/admin/jobs", limit=1)) == 1


@pytest.mark.parametrize("params", [{"limit": 501}, {"limit": 0}, {"state": "sleeping"}])
async def test_job_listing_parameters_are_checked(admin_client, idp, params):
    response = await admin_client.get(
        "/v1/admin/jobs", headers=idp.bearer("viewer"), params=params
    )
    assert (response.status_code, response.json()["code"]) == (422, "invalid_request")


async def test_followers_are_listed_with_device_and_leases(admin_client, idp, factory):
    busy, _ = await factory.follower(device="cuda")
    await factory.follower(state="gone")
    await factory.job(state="leased", lease_id=uuid.uuid4(), leased_by=busy.id, attempts=1)
    rows = await get(admin_client, idp, "/v1/admin/followers", state="active")
    assert [(r["id"], r["device"], r["leases"]) for r in rows] == [(str(busy.id), "cuda", 1)]
    assert len(await get(admin_client, idp, "/v1/admin/followers")) == 2


async def test_tokens_are_listed_without_the_token(admin_client, idp, sessionmaker):
    token_id, plaintext = await join_token(sessionmaker, pool="gpu")
    response = await admin_client.get("/v1/admin/tokens", headers=idp.bearer("admin"))
    (row,) = response.json()
    assert (row["id"], row["pool"], row["uses"], row["revoked"]) == (str(token_id), "gpu", 0, False)
    assert plaintext not in response.text


async def test_the_consent_report_counts_and_lists_outputs_flagged_for_deletion(
    admin_client, idp, factory
):
    here = await factory.location(name="here")
    withdrawn = await factory.recording(here, key="talks/a.mp3", consent="withdrawn")
    await factory.recording(here, key="talks/b.mp3", consent="not_consented")
    await factory.recording(here, key="talks/c.mp3", missing=True)
    flagged = await factory.job(
        withdrawn, state="completed", completed_at=utcnow(), outputs_flagged_for_deletion=True
    )
    body = await get(admin_client, idp, "/v1/admin/consent/report")
    assert body["locations"] == [
        {"name": "here", "consented": 0, "not_consented": 1, "withdrawn": 1, "missing": 1}
    ]
    (row,) = body["flagged"]
    assert (row["job_id"], row["key"], row["output_location"]) == (
        str(flagged.id),
        "talks/a.mp3",
        "here",
    )
    assert row["outputs"] == [
        "transcripts/talks/a.mp3.txt",
        "transcripts/talks/a.mp3.srt",
        "transcripts/talks/a.mp3.segments.json",
    ]
    assert body["truncated"] is False


async def test_the_consent_report_for_an_unknown_location_is_404(admin_client, idp):
    response = await admin_client.get(
        "/v1/admin/consent/report", headers=idp.bearer("viewer"), params={"location": "nowhere"}
    )
    assert (response.status_code, response.json()["code"]) == (404, "not_found")


async def test_every_read_is_audited(admin_client, idp, sessionmaker):
    for path in (
        "/v1/admin/status",
        "/v1/admin/locations",
        "/v1/admin/jobs",
        "/v1/admin/followers",
        "/v1/admin/consent/report",
    ):
        await get(admin_client, idp, path)
    await get(admin_client, idp, "/v1/admin/tokens", role="admin")
    async with sessionmaker() as session:
        entries = (await session.scalars(select(AuditEntry))).all()
    # Each entry names the person: email, issuer and subject.
    assert {(e.action, e.actor) for e in entries if e.action == "tokens.view"} == {
        ("tokens.view", f"admin@example.org ({idp.ENTRA_ISSUER} entra-admin)")
    }
    assert {e.actor for e in entries if e.action != "tokens.view"} == {
        f"viewer@example.org ({idp.ENTRA_ISSUER} entra-viewer)"
    }
    actions = sorted(e.action for e in entries)
    assert actions == sorted(
        [
            "status.view",
            "locations.view",
            "jobs.view",
            "followers.view",
            "consent.view",
            "tokens.view",
        ]
    )


READ_PATHS = (
    "/v1/admin/status",
    "/v1/admin/locations",
    "/v1/admin/jobs",
    "/v1/admin/followers",
    "/v1/admin/consent/report",
)


@pytest.mark.parametrize("path", [*READ_PATHS, "/v1/admin/tokens"])
async def test_reads_need_a_sign_in(admin_client, path):
    assert (await admin_client.get(path)).status_code == 401


@pytest.mark.parametrize("path", [*READ_PATHS, "/v1/admin/tokens"])
async def test_reads_refuse_a_caller_without_a_role(admin_client, idp, path):
    response = await admin_client.get(path, headers=idp.bearer(None))
    assert response.status_code == 403


async def test_tokens_need_the_administrator_role(admin_client, idp):
    response = await admin_client.get("/v1/admin/tokens", headers=idp.bearer("viewer"))
    assert response.status_code == 403


async def test_the_jobs_page_is_bounded(admin_client, idp, factory):
    location = await factory.location()
    for n in range(3):
        await factory.job(await factory.recording(location, key=f"talks/{n}.mp3"))
    assert len(await get(admin_client, idp, "/v1/admin/jobs", limit=2)) == 2


async def test_the_consent_report_is_bounded_and_says_so(admin_client, idp, factory):
    location = await factory.location()
    for n in range(2):
        recording = await factory.recording(location, key=f"talks/{n}.mp3", consent="withdrawn")
        await factory.job(
            recording, state="completed", completed_at=utcnow(), outputs_flagged_for_deletion=True
        )
    body = await get(admin_client, idp, "/v1/admin/consent/report", limit=1)
    assert (len(body["flagged"]), body["truncated"]) == (1, True)


async def test_no_read_exposes_secrets_or_credentials(
    admin_client, idp, factory, sessionmaker, tmp_path
):
    location = await factory.location(
        config={"root": str(tmp_path), "secret_ref": "storage-credential-ref-1"}
    )
    await factory.job(await factory.recording(location, key="talks/a.mp3"))
    follower, credential = await factory.follower()
    await join_token(sessionmaker)
    seen = []
    for path in (*READ_PATHS, "/v1/admin/tokens"):
        seen.append((await admin_client.get(path, headers=idp.bearer("admin"))).text)
    text = " ".join(seen)
    assert location.name in text  # the location (and its config) was read
    for forbidden in ("token_hash", "credential_hash", "sdes", "link", "http://", "https://"):
        assert forbidden not in text.lower()
    assert "secret_ref" not in text
    assert "storage-credential-ref-1" not in text
    assert str(credential) not in text


async def post(client, idp, path, role, body=None):
    return await client.post(path, headers=idp.bearer(role), json=body)


def actor(idp, role):
    return f"{role}@example.org ({idp.ENTRA_ISSUER} entra-{role})"


# --- locations ------------------------------------------------------------------


async def test_add_a_location_then_list_it(admin_client, idp, sessionmaker, tmp_path):
    response = await post(
        admin_client,
        idp,
        "/v1/admin/locations",
        "admin",
        {"name": "archive-1", "root": str(tmp_path), "input_prefix": "incoming/"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert (
        body["name"],
        body["backend"],
        body["root"],
        body["input_prefix"],
        body["output_prefix"],
        body["scan_interval_s"],
        body["enabled"],
    ) == ("archive-1", "local", str(tmp_path), "incoming/", "transcripts/", 900, True)
    listed = await get(admin_client, idp, "/v1/admin/locations")
    assert [row["name"] for row in listed] == ["archive-1"]
    (entry,) = await audit_rows(sessionmaker, "location.add")
    assert entry.actor == actor(idp, "admin")


async def test_a_duplicate_location_name_is_409(admin_client, idp, tmp_path):
    body = {"name": "archive-1", "root": str(tmp_path)}
    assert (await post(admin_client, idp, "/v1/admin/locations", "admin", body)).status_code == 201
    again = await post(admin_client, idp, "/v1/admin/locations", "admin", body)
    assert (again.status_code, again.json()["code"]) == (409, "exists")


@pytest.mark.parametrize(
    "change, field",
    [
        ({"name": "a/b"}, "name"),
        ({"name": "has space"}, "name"),
        ({"root": "relative/folder"}, "root"),
        ({"input_prefix": "../escape/"}, "input_prefix"),
        ({"input_prefix": "/absolute/"}, "input_prefix"),
        ({"output_prefix": "a\\b/"}, "output_prefix"),
        ({"backend": "azure"}, "backend"),
        ({"scan_interval_s": 5}, "scan_interval_s"),
        ({"required_device": "tpu"}, "required_device"),
    ],
)
async def test_invalid_locations_are_refused(admin_client, idp, tmp_path, change, field):
    body = {"name": "archive-1", "root": str(tmp_path), **change}
    response = await post(admin_client, idp, "/v1/admin/locations", "admin", body)
    assert (response.status_code, response.json()["code"]) == (422, "invalid_request")
    assert field in response.json()["message"]


async def test_a_root_this_leader_cannot_see_is_refused(admin_client, idp, tmp_path):
    body = {"name": "archive-1", "root": str(tmp_path / "not-mounted")}
    response = await post(admin_client, idp, "/v1/admin/locations", "admin", body)
    assert (response.status_code, response.json()["code"]) == (400, "root_unavailable")


async def test_disable_stops_scanning_and_enable_resumes_it(
    admin_app, admin_client, idp, factory, sessionmaker
):
    await factory.location(name="here")
    disabled = await post(admin_client, idp, "/v1/admin/locations/here/disable", "admin")
    assert disabled.json()["enabled"] is False

    async def scanned() -> set[str]:
        backends = admin_app.state.backend_factory
        return set(await scan_due_locations(sessionmaker, backends, now=utcnow(), max_attempts=3))

    assert "here" not in await scanned()
    enabled = await post(admin_client, idp, "/v1/admin/locations/here/enable", "admin")
    assert enabled.json()["enabled"] is True
    assert "here" in await scanned()
    actions = [e.action for e in await audit_rows(sessionmaker, "location.disable")]
    assert actions == ["location.disable"]


async def test_ingest_requests_a_scan_that_the_scanner_then_runs(
    admin_app, admin_client, idp, factory, sessionmaker, tmp_path
):
    write(tmp_path, "consent.txt", b"**/*.mp3\n")
    write(tmp_path, "talks/one.mp3")
    now = utcnow()
    await factory.location(name="here", last_scan_at=now)  # not due for 15 minutes
    response = await post(admin_client, idp, "/v1/admin/locations/here/ingest", "operator")
    assert (response.status_code, response.json()["name"]) == (202, "here")
    results = await scan_due_locations(
        sessionmaker, admin_app.state.backend_factory, now=now, max_attempts=3
    )
    assert results["here"].jobs_created == 1


async def test_ingest_of_a_disabled_or_unknown_location(admin_client, idp, factory):
    await factory.location(name="off", enabled=False)
    off = await post(admin_client, idp, "/v1/admin/locations/off/ingest", "operator")
    assert (off.status_code, off.json()["code"]) == (409, "disabled")
    unknown = await post(admin_client, idp, "/v1/admin/locations/nowhere/ingest", "operator")
    assert unknown.status_code == 404


# --- jobs -----------------------------------------------------------------------


async def test_retry_queues_a_new_job_for_a_failed_one(admin_client, idp, factory, sessionmaker):
    failed = await factory.job(state="failed", failure_reason="engine_error: x", priority=4)
    response = await post(admin_client, idp, f"/v1/admin/jobs/{failed.id}/retry", "operator")
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["state"], body["attempts"], body["priority"]) == ("queued", 0, 4)
    assert body["id"] != str(failed.id)
    (entry,) = await audit_rows(sessionmaker, "job.retry")
    assert (entry.subject_id, entry.detail) == (body["id"], {"retry_of": str(failed.id)})


async def test_retrying_a_cancelled_job_is_claimable_and_not_cancelled(
    admin_client, idp, factory, sessionmaker
):
    job = await factory.job()
    cancelled = await post(admin_client, idp, f"/v1/admin/jobs/{job.id}/cancel", "operator")
    assert cancelled.json()["cancelled_by"] == actor(idp, "operator")
    response = await post(admin_client, idp, f"/v1/admin/jobs/{job.id}/retry", "operator")
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["state"], body["cancelled_by"], body["failure_reason"]) == ("queued", None, None)
    follower, _ = await factory.follower()
    async with sessionmaker() as session:
        claimed = await store.claim(session, follower, now=utcnow(), lease_seconds=120)
        await session.commit()
    assert str(claimed.id) == body["id"]
    # the retry is now open, so a second retry of the old job is refused
    again = await post(admin_client, idp, f"/v1/admin/jobs/{job.id}/retry", "operator")
    assert (again.status_code, again.json()["code"]) == (409, "already_open")


@pytest.mark.parametrize(
    "recording_values, code",
    [
        ({"consent": "withdrawn"}, "not_consented"),
        ({"consent": "not_consented"}, "not_consented"),
        ({"missing": True}, "not_consented"),
        ({"source_version": "99-9"}, "recording_changed"),
    ],
)
async def test_retry_never_queues_a_recording_that_may_not_be_processed(
    admin_client, idp, factory, sessionmaker, recording_values, code
):
    recording = await factory.recording(**recording_values)
    failed = await factory.job(recording, state="failed", source_version="10-1")
    response = await post(admin_client, idp, f"/v1/admin/jobs/{failed.id}/retry", "operator")
    assert (response.status_code, response.json()["code"]) == (409, code)
    async with sessionmaker() as session:
        assert len((await session.scalars(select(Job))).all()) == 1


async def test_retry_of_an_unfinished_job_is_409(admin_client, idp, factory):
    queued = await factory.job()
    response = await post(admin_client, idp, f"/v1/admin/jobs/{queued.id}/retry", "operator")
    assert (response.status_code, response.json()["code"]) == (409, "not_retryable")


async def test_two_retries_at_once_queue_the_job_once(admin_client, idp, factory, sessionmaker):
    failed = await factory.job(state="failed")
    headers = idp.bearer("operator")
    responses = await asyncio.gather(
        *(admin_client.post(f"/v1/admin/jobs/{failed.id}/retry", headers=headers) for _ in range(2))
    )
    assert sorted(r.status_code for r in responses) == [200, 409]
    async with sessionmaker() as session:
        queued = (await session.scalars(select(Job).where(Job.state == "queued"))).all()
    assert len(queued) == 1


async def test_cancel_tells_the_holder_and_is_not_undone_by_scanning(
    admin_client, idp, factory, sessionmaker
):
    follower, credential = await factory.follower()
    lease = uuid.uuid4()
    job = await factory.job(state="leased", lease_id=lease, leased_by=follower.id, attempts=1)
    response = await post(admin_client, idp, f"/v1/admin/jobs/{job.id}/cancel", "operator")
    assert (response.status_code, response.json()["state"], response.json()["cancelled_by"]) == (
        200,
        "cancelled",
        actor(idp, "operator"),
    )
    heartbeat = await admin_client.post(
        f"/v1/jobs/{job.id}/heartbeat",
        headers={"Authorization": f"Bearer {credential}"},
        json={"lease_id": str(lease)},
    )
    assert heartbeat.json() == {"directive": "cancel"}
    again = await post(admin_client, idp, f"/v1/admin/jobs/{job.id}/cancel", "operator")
    assert again.status_code == 200


@pytest.mark.parametrize("state", ["completed", "failed"])
async def test_a_finished_job_cannot_be_cancelled_or_reprioritised(
    admin_client, idp, factory, state
):
    job = await factory.job(state=state)
    cancel = await post(admin_client, idp, f"/v1/admin/jobs/{job.id}/cancel", "operator")
    priority = await post(
        admin_client, idp, f"/v1/admin/jobs/{job.id}/priority", "operator", {"priority": 5}
    )
    assert [(r.status_code, r.json()["code"]) for r in (cancel, priority)] == [
        (409, "not_open"),
        (409, "not_open"),
    ]


async def test_priority_changes_the_claim_order(admin_client, idp, factory, sessionmaker):
    location = await factory.location()
    await factory.job(await factory.recording(location, key="talks/a.mp3"))
    later = await factory.job(await factory.recording(location, key="talks/b.mp3"))
    response = await post(
        admin_client, idp, f"/v1/admin/jobs/{later.id}/priority", "operator", {"priority": 10}
    )
    assert response.json()["priority"] == 10
    follower, _ = await factory.follower()
    async with sessionmaker() as session:
        claimed = await store.claim(session, follower, now=utcnow(), lease_seconds=120)
        await session.commit()
    assert claimed.id == later.id
    (entry,) = await audit_rows(sessionmaker, "job.priority")
    assert entry.detail == {"from": 0, "to": 10}


@pytest.mark.parametrize("priority", [1001, -1001, "high"])
async def test_priority_is_bounded(admin_client, idp, factory, priority):
    job = await factory.job()
    response = await post(
        admin_client, idp, f"/v1/admin/jobs/{job.id}/priority", "operator", {"priority": priority}
    )
    assert response.status_code == 422


async def test_an_unknown_job_is_404(admin_client, idp):
    response = await post(admin_client, idp, f"/v1/admin/jobs/{uuid.uuid4()}/cancel", "operator")
    assert response.status_code == 404


# --- followers ------------------------------------------------------------------


async def test_drain_lets_the_follower_finish_and_take_no_more(
    admin_client, idp, factory, sessionmaker
):
    follower, credential = await factory.follower()
    await factory.job()
    response = await post(admin_client, idp, f"/v1/admin/followers/{follower.id}/drain", "operator")
    assert response.json()["state"] == "draining"
    claim = await admin_client.post(
        "/v1/jobs/claim", headers={"Authorization": f"Bearer {credential}"}
    )
    assert claim.status_code == 204
    (entry,) = await audit_rows(sessionmaker, "follower.drain")
    assert entry.actor == actor(idp, "operator")


async def test_revoke_releases_leases_at_once_and_shuts_the_follower_out(
    admin_app, admin_client, idp, factory, sessionmaker, tmp_path
):
    follower, credential = await factory.follower()
    location = await factory.location()
    lease = uuid.uuid4()
    job = await factory.job(
        await factory.recording(location),
        state="leased",
        lease_id=lease,
        leased_by=follower.id,
        attempts=1,
    )
    upload = admin_app.state.backend_factory(location).upload_link(
        "transcripts/talks/one.mp3.txt",
        timedelta(minutes=5),
        job_id=str(job.id),
        lease_id=str(lease),
    )
    response = await post(admin_client, idp, f"/v1/admin/followers/{follower.id}/revoke", "admin")
    assert response.json() == {"id": str(follower.id), "state": "revoked", "released": 1}
    async with sessionmaker() as session:
        stored = await session.get(Job, job.id)
    assert (stored.state, stored.lease_id, stored.attempts) == ("queued", None, 0)
    late = await admin_client.put(upload.url, content=b"late output")
    assert (late.status_code, late.json()["code"]) == (409, "stale_lease")
    assert not (tmp_path / "transcripts" / "talks" / "one.mp3.txt").exists()
    claim = await admin_client.post(
        "/v1/jobs/claim", headers={"Authorization": f"Bearer {credential}"}
    )
    assert claim.status_code == 403
    (entry,) = await audit_rows(sessionmaker, "follower.revoke")
    assert entry.detail == {"released": 1}
    assert entry.actor == actor(idp, "admin")


async def test_a_revoked_follower_cannot_be_drained(admin_client, idp, factory, sessionmaker):
    follower, _ = await factory.follower(state="revoked")
    response = await post(admin_client, idp, f"/v1/admin/followers/{follower.id}/drain", "operator")
    assert (response.status_code, response.json()["code"]) == (409, "revoked")
    assert await audit_rows(sessionmaker, "follower.drain") == []


async def test_a_drain_racing_a_revoke_never_undoes_the_revoke(
    admin_client, idp, factory, sessionmaker
):
    follower, _ = await factory.follower()
    await factory.job(state="leased", lease_id=uuid.uuid4(), leased_by=follower.id, attempts=1)
    drain, revoke = await asyncio.gather(
        post(admin_client, idp, f"/v1/admin/followers/{follower.id}/drain", "operator"),
        post(admin_client, idp, f"/v1/admin/followers/{follower.id}/revoke", "admin"),
    )
    assert revoke.status_code == 200
    assert drain.status_code in (200, 409)
    async with sessionmaker() as session:
        assert (await session.get(Follower, follower.id)).state == "revoked"


async def test_an_unknown_follower_or_token_is_404(admin_client, idp):
    for path in (
        f"/v1/admin/followers/{uuid.uuid4()}/drain",
        f"/v1/admin/followers/{uuid.uuid4()}/revoke",
        f"/v1/admin/tokens/{uuid.uuid4()}/revoke",
    ):
        response = await post(admin_client, idp, path, "admin")
        assert response.status_code == 404, path


# --- join tokens ----------------------------------------------------------------


async def test_a_created_token_registers_a_follower_once_and_is_never_logged(
    admin_client, idp, sessionmaker, caplog
):
    with caplog.at_level("DEBUG"):
        response = await post(
            admin_client,
            idp,
            "/v1/admin/tokens",
            "admin",
            {"pool": "gpu", "expires_in_seconds": 3600, "max_uses": 1},
        )
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["token"] not in caplog.text
    register = {"join_token": created["token"], "protocol_version": 1, "capabilities": CAPABILITIES}
    first = await admin_client.post("/v1/followers/register", json=register)
    second = await admin_client.post("/v1/followers/register", json=register)
    assert (first.status_code, second.status_code) == (200, 401)
    async with sessionmaker() as session:
        row = await session.get(JoinToken, uuid.UUID(created["id"]))
    assert (row.pool, row.created_by) == ("gpu", actor(idp, "admin"))
    (entry,) = await audit_rows(sessionmaker, "token.create")
    assert created["token"] not in f"{entry.actor} {entry.detail}"
    assert entry.actor == actor(idp, "admin")
    listed = await get(admin_client, idp, "/v1/admin/tokens", role="admin")
    assert created["token"] not in str(listed)


async def test_a_revoked_token_registers_nothing(admin_client, idp, sessionmaker):
    token_id, plaintext = await join_token(sessionmaker, max_uses=5)
    response = await post(admin_client, idp, f"/v1/admin/tokens/{token_id}/revoke", "admin")
    assert response.json()["revoked"] is True
    register = await admin_client.post(
        "/v1/followers/register",
        json={"join_token": plaintext, "protocol_version": 1, "capabilities": CAPABILITIES},
    )
    assert register.status_code == 401
    (entry,) = await audit_rows(sessionmaker, "token.revoke")
    assert entry.actor == actor(idp, "admin")


@pytest.mark.parametrize(
    "body",
    [
        {"expires_in_seconds": 59},
        {"expires_in_seconds": 90 * 86400 + 1},
        {"max_uses": 0},
        {"pool": "bad pool"},
    ],
)
async def test_invalid_tokens_are_refused(admin_client, idp, body):
    response = await post(admin_client, idp, "/v1/admin/tokens", "admin", body)
    assert response.status_code == 422


# --- every route's role boundary ------------------------------------------------

ROUTES = [
    ("GET", "/v1/admin/whoami", None, "viewer"),
    ("GET", "/v1/admin/status", None, "viewer"),
    ("GET", "/v1/admin/locations", None, "viewer"),
    ("GET", "/v1/admin/jobs", None, "viewer"),
    ("GET", "/v1/admin/followers", None, "viewer"),
    ("GET", "/v1/admin/consent/report", None, "viewer"),
    ("POST", "/v1/admin/locations/{name}/ingest", None, "operator"),
    ("POST", "/v1/admin/jobs/{failed_job}/retry", None, "operator"),
    ("POST", "/v1/admin/jobs/{open_job}/cancel", None, "operator"),
    ("POST", "/v1/admin/jobs/{open_job}/priority", {"priority": 5}, "operator"),
    ("POST", "/v1/admin/followers/{follower}/drain", None, "operator"),
    ("POST", "/v1/admin/locations", {"name": "added", "root": "{root}"}, "admin"),
    ("POST", "/v1/admin/locations/{name}/disable", None, "admin"),
    ("POST", "/v1/admin/locations/{name}/enable", None, "admin"),
    ("GET", "/v1/admin/tokens", None, "admin"),
    ("POST", "/v1/admin/tokens", {"pool": "default"}, "admin"),
    ("POST", "/v1/admin/tokens/{token}/revoke", None, "admin"),
    ("POST", "/v1/admin/followers/{follower}/revoke", None, "admin"),
]
BELOW = {"viewer": None, "operator": "viewer", "admin": "operator"}


@pytest.fixture
async def world(factory, sessionmaker, tmp_path_factory):
    location = await factory.location(name="here")
    failed = await factory.job(await factory.recording(location, key="talks/a.mp3"), state="failed")
    open_job = await factory.job(await factory.recording(location, key="talks/b.mp3"))
    follower, _ = await factory.follower()
    token_id, _ = await join_token(sessionmaker)
    return {
        "name": "here",
        "failed_job": failed.id,
        "open_job": open_job.id,
        "follower": follower.id,
        "token": token_id,
        # a folder apart from the factory location's, which would overlap
        "root": str(tmp_path_factory.mktemp("added-root")),
    }


@pytest.mark.parametrize(
    "method, path, body, role", ROUTES, ids=[f"{m} {p}" for m, p, _, _ in ROUTES]
)
async def test_each_admin_route_is_refused_for_the_role_below_it(
    admin_client, idp, world, sessionmaker, method, path, body, role
):
    path = path.format(**world)
    if body is not None:
        body = {k: v.format(**world) if isinstance(v, str) else v for k, v in body.items()}
    refused = await admin_client.request(method, path, headers=idp.bearer(BELOW[role]), json=body)
    assert (refused.status_code, refused.json()["code"]) == (403, "forbidden")
    async with sessionmaker() as session:
        changes = (
            await session.scalars(
                select(AuditEntry).where(
                    AuditEntry.action != "admin.refused", AuditEntry.actor != "test"
                )
            )
        ).all()
    assert changes == []  # the refused call changed and recorded nothing else
    allowed = await admin_client.request(method, path, headers=idp.bearer(role), json=body)
    assert allowed.status_code < 400, allowed.text


async def test_every_admin_route_is_in_the_role_table(admin_app):
    def shape(path: str) -> str:
        return re.sub(r"\{[^}]+\}", "{}", path)

    # The OpenAPI document lists every served route, however the router nests them.
    served = {
        (method.upper(), shape(path))
        for path, operations in admin_app.openapi()["paths"].items()
        if path.startswith("/v1/admin/")
        for method in operations
    }
    table = {(method, shape(path)) for method, path, _, _ in ROUTES}
    assert served - {("GET", "/v1/admin/login-config")} == table


# --- fix round 1 ----------------------------------------------------------------


async def add_at(client, idp, name, root, **extra):
    return await post(
        client, idp, "/v1/admin/locations", "admin", {"name": name, "root": str(root), **extra}
    )


async def test_a_second_location_on_the_same_root_cannot_reanchor_consent(
    admin_client, idp, tmp_path
):
    write(tmp_path, "consent.txt", b"public/*.mp3\n")
    assert (await add_at(admin_client, idp, "a", tmp_path)).status_code == 201
    again = await add_at(admin_client, idp, "b", tmp_path, input_prefix="private/")
    assert (again.status_code, again.json()["code"]) == (409, "overlaps")
    assert "'a'" in again.json()["message"]


async def test_nested_and_containing_roots_overlap_but_siblings_do_not(
    admin_client, idp, tmp_path
):
    for folder in ("outer", "outer/inner", "sibling-1", "sibling-2"):
        (tmp_path / folder).mkdir(parents=True, exist_ok=True)
    assert (await add_at(admin_client, idp, "outer", tmp_path / "outer")).status_code == 201
    inside = await add_at(admin_client, idp, "inner", tmp_path / "outer" / "inner")
    assert (inside.status_code, inside.json()["code"]) == (409, "overlaps")
    assert (await add_at(admin_client, idp, "s1", tmp_path / "sibling-1")).status_code == 201
    containing = await add_at(admin_client, idp, "parent", tmp_path)
    assert (containing.status_code, containing.json()["code"]) == (409, "overlaps")
    assert (await add_at(admin_client, idp, "s2", tmp_path / "sibling-2")).status_code == 201


async def test_a_disabled_location_still_holds_its_folder(admin_client, idp, tmp_path):
    await add_at(admin_client, idp, "a", tmp_path)
    await post(admin_client, idp, "/v1/admin/locations/a/disable", "admin")
    again = await add_at(admin_client, idp, "b", tmp_path)
    assert (again.status_code, again.json()["code"]) == (409, "overlaps")


async def test_two_overlapping_additions_at_once_add_one(admin_client, idp, tmp_path):
    responses = await asyncio.gather(
        add_at(admin_client, idp, "a", tmp_path), add_at(admin_client, idp, "b", tmp_path)
    )
    assert sorted(r.status_code for r in responses) == [201, 409]


async def test_the_stored_root_is_the_resolved_path(admin_client, idp, tmp_path):
    (tmp_path / "real").mkdir()
    awkward = f"{tmp_path}/real/../real"
    response = await add_at(admin_client, idp, "a", awkward)
    assert response.status_code == 201, response.text
    assert response.json()["root"] == str((tmp_path / "real").resolve())


async def test_a_root_through_a_symlink_is_refused(admin_client, idp, tmp_path):
    (tmp_path / "real").mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(tmp_path / "real", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not permitted here")
    response = await add_at(admin_client, idp, "a", link)
    assert (response.status_code, response.json()["code"]) == (422, "invalid_root")
    through = await add_at(admin_client, idp, "b", link / "deeper")
    assert through.status_code in (422, 400)


async def test_a_root_that_is_a_file_is_refused(admin_client, idp, tmp_path):
    write(tmp_path, "file.txt")
    response = await add_at(admin_client, idp, "a", tmp_path / "file.txt")
    assert (response.status_code, response.json()["code"]) == (422, "invalid_root")


@pytest.mark.parametrize(
    "field, value, status",
    [
        ("input_prefix", "incoming//", 422),
        ("input_prefix", "incoming", 422),
        ("output_prefix", "transcripts", 422),
        ("output_prefix", "out//", 422),
        ("input_prefix", "a/b/", 201),
        ("input_prefix", "", 201),
    ],
)
async def test_prefixes_end_in_exactly_one_slash(admin_client, idp, tmp_path, field, value, status):
    response = await add_at(admin_client, idp, "a", tmp_path, **{field: value})
    assert response.status_code == status, response.text


async def test_a_retry_and_a_scan_of_the_same_recording_queue_it_once(
    admin_app, admin_client, idp, factory, sessionmaker, tmp_path
):
    """Reproduces the race fixed by locking location -> recording -> job in retry. Every scan
    uses the first scan's `now`, so the recording row is not rewritten (the scanner then takes
    no recording row lock) and only the location lock orders it against a retry."""
    write(tmp_path, "consent.txt", b"**/*.mp3\n")
    write(tmp_path, "talks/one.mp3")
    await factory.location(name="here")
    backends = admin_app.state.backend_factory
    now = utcnow()
    await scan_due_locations(sessionmaker, backends, now=now, max_attempts=3)
    async with sessionmaker() as session:
        first_job = (await session.scalars(select(Job))).one().id
    headers = idp.bearer("operator")

    async def jittered(coroutine):
        await asyncio.sleep(random.uniform(0, 0.03))
        return await coroutine

    for _ in range(60):
        async with sessionmaker() as session:
            # Back to "the system cancelled it": failed jobs would count as already queued once.
            await session.execute(
                update(Job).values(
                    state="cancelled", cancelled_by=None, lease_id=None, leased_by=None
                )
            )
            await session.execute(
                update(StorageLocation).values(scan_requested_at=now, last_scan_at=now)
            )
            await session.commit()
        retry, _scan = await asyncio.gather(
            jittered(admin_client.post(f"/v1/admin/jobs/{first_job}/retry", headers=headers)),
            jittered(scan_due_locations(sessionmaker, backends, now=now, max_attempts=3)),
        )
        assert retry.status_code in (200, 409), retry.text
        async with sessionmaker() as session:
            open_jobs = (await session.scalars(select(Job).where(Job.state == "queued"))).all()
        assert len(open_jobs) <= 1, "retry and scan both queued the recording"


async def test_cancelling_a_system_cancelled_job_makes_the_cancel_the_administrators(
    admin_client, idp, factory, sessionmaker
):
    job = await factory.job(state="cancelled", failure_reason="recording missing")
    response = await post(admin_client, idp, f"/v1/admin/jobs/{job.id}/cancel", "operator")
    assert response.status_code == 200
    assert response.json()["cancelled_by"] == actor(idp, "operator")
    async with sessionmaker() as session:
        assert (await session.get(Job, job.id)).cancelled_by == actor(idp, "operator")


async def test_retry_refuses_a_version_that_was_already_transcribed(
    admin_client, idp, factory
):
    recording = await factory.recording()
    await factory.job(recording, state="completed")
    failed = await factory.job(recording, state="failed")
    response = await post(admin_client, idp, f"/v1/admin/jobs/{failed.id}/retry", "operator")
    assert (response.status_code, response.json()["code"]) == (409, "already_completed")


async def test_location_names_are_unique_ignoring_case(admin_client, idp, tmp_path):
    (tmp_path / "x").mkdir()
    (tmp_path / "y").mkdir()
    assert (await add_at(admin_client, idp, "Archive", tmp_path / "x")).status_code == 201
    again = await add_at(admin_client, idp, "archive", tmp_path / "y")
    assert (again.status_code, again.json()["code"]) == (409, "exists")


async def test_refused_changes_are_audited_without_their_values(
    admin_client, idp, factory, sessionmaker
):
    queued = await factory.job()
    conflict = await post(admin_client, idp, f"/v1/admin/jobs/{queued.id}/retry", "operator")
    missing = await post(admin_client, idp, f"/v1/admin/jobs/{uuid.uuid4()}/cancel", "operator")
    assert (conflict.status_code, missing.status_code) == (409, 404)
    entries = await audit_rows(sessionmaker, "admin.change_refused")
    assert sorted((e.subject_id, e.detail["code"]) for e in entries) == [
        ("POST /v1/admin/jobs/{job_id}/cancel", "not_found"),
        ("POST /v1/admin/jobs/{job_id}/retry", "not_retryable"),
    ]
    assert {e.actor for e in entries} == {actor(idp, "operator")}
    assert str(queued.id) not in str([e.detail for e in entries])


@pytest.mark.parametrize(
    ("path", "params", "status", "code"),
    [
        ("/v1/admin/consent/report", {"location": "nowhere"}, 404, "not_found"),
        ("/v1/admin/jobs", {"state": "sleeping"}, 422, "invalid_request"),
        ("/v1/admin/followers", {"state": "asleep"}, 422, "invalid_request"),
    ],
)
async def test_a_failed_read_is_audited_without_its_values(
    admin_client, idp, sessionmaker, path, params, status, code
):
    response = await admin_client.get(path, headers=idp.bearer("viewer"), params=params)
    assert (response.status_code, response.json()["code"]) == (status, code)
    (entry,) = await audit_rows(sessionmaker, "admin.read_refused")
    assert entry.actor == actor(idp, "viewer")
    assert (entry.subject_type, entry.subject_id) == ("endpoint", f"GET {path}")
    assert entry.detail == {"code": code}
    recorded = f"{entry.subject_id} {entry.detail}"
    assert all(str(value) not in recorded for value in params.values())


async def test_a_read_refused_by_role_is_not_a_failed_read(admin_client, idp, sessionmaker):
    await admin_client.get("/v1/admin/tokens", headers=idp.bearer("viewer"))
    await admin_client.get("/v1/admin/jobs", params={"state": "sleeping"})  # no sign-in
    assert await audit_rows(sessionmaker, "admin.read_refused") == []


async def test_a_refused_read_or_role_is_not_a_refused_change(
    admin_client, idp, sessionmaker
):
    await admin_client.get("/v1/admin/consent/report", headers=idp.bearer("viewer"),
                           params={"location": "nowhere"})
    await post(admin_client, idp, "/v1/admin/tokens", "viewer", {})
    assert await audit_rows(sessionmaker, "admin.change_refused") == []


def admin_surface_problems(app) -> tuple[list[str], int]:
    """Routes under /v1/admin that are hidden from the schema or not plain API routes, and
    sub-applications mounted at, above or below /v1/admin; and how many admin routes exist."""
    from fastapi.routing import APIRoute
    from starlette.routing import Mount

    def walk(routes):
        for route in routes:
            nested = getattr(route, "original_router", None)
            if nested is not None:
                yield from walk(nested.routes)
            else:
                yield route

    problems, seen = [], 0
    for route in walk(app.routes):
        path = getattr(route, "path", "")
        if isinstance(route, Mount):
            p = path.rstrip("/") + "/"
            if p.startswith("/v1/admin/") or "/v1/admin/".startswith(p):
                problems.append(f"sub-application mounted at {path!r}")
        elif path.startswith("/v1/admin/"):
            seen += 1
            if not isinstance(route, APIRoute):
                problems.append(f"{path} is not an API route")
            elif not route.include_in_schema:
                problems.append(f"{path} is hidden from the schema")
    return problems, seen


async def test_no_admin_route_is_hidden_or_mounted_as_a_sub_application(admin_app):
    problems, seen = admin_surface_problems(admin_app)
    assert problems == []
    assert seen >= len(ROUTES)


@pytest.mark.parametrize("mount_at", ["/v1/admin/legacy", "/v1/admin", "/v1", "/"])
def test_the_walk_catches_a_sub_application_mounted_on_or_around_admin(mount_at):
    from fastapi import FastAPI

    app = FastAPI()
    app.mount(mount_at, FastAPI())
    problems, _ = admin_surface_problems(app)
    assert len(problems) == 1


def test_the_walk_catches_a_hidden_admin_route():
    from fastapi import FastAPI

    app = FastAPI()
    app.add_api_route("/v1/admin/secret", lambda: None, include_in_schema=False)
    problems, _ = admin_surface_problems(app)
    assert problems == ["/v1/admin/secret is hidden from the schema"]


def test_an_unrelated_mount_is_fine():
    from fastapi import FastAPI

    app = FastAPI()
    app.mount("/static", FastAPI())
    assert admin_surface_problems(app)[0] == []
