import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select
from swarmscribe_leader.auth.followers import create_join_token
from swarmscribe_leader.clock import utcnow
from swarmscribe_leader.db.models import AuditEntry, JobAttempt

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
        actions = sorted(e.action for e in (await session.scalars(select(AuditEntry))).all())
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


async def test_no_read_exposes_secrets_or_credentials(admin_client, idp, factory, sessionmaker):
    location = await factory.location()
    await factory.job(await factory.recording(location, key="talks/a.mp3"))
    follower, credential = await factory.follower()
    await join_token(sessionmaker)
    seen = []
    for path in (*READ_PATHS, "/v1/admin/tokens"):
        seen.append((await admin_client.get(path, headers=idp.bearer("admin"))).text)
    text = " ".join(seen)
    for forbidden in ("token_hash", "credential_hash", "sdes", "link", "http://", "https://"):
        assert forbidden not in text.lower()
    assert str(credential) not in text
