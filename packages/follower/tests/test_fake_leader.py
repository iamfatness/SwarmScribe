"""The fake leader against the real leader's behaviour (api/follower.py, api/files.py,
jobs/store.py): the follower's tests are only as good as the fake. Task 11 runs the follower
against the real leader, so a difference found there is a defect of the fake."""

import httpx
import pytest
from follower_testkit import BASE, FakeEngine, FakeLeader, make_runner, sha
from swarmscribe_follower.leader import LeaderClient, NoWork, Refused, Transient
from swarmscribe_follower.lease import JobControl
from swarmscribe_follower.transfer import LeaseLost, LinkExpired
from swarmscribe_protocol import DIRECTIVE_HEADER, OutputChecksums

EMPTY_SEGMENTS = b'{"segments": []}'
SOME_SEGMENTS = b'{"segments": [{"x": 1}]}'


@pytest.fixture
def world(tmp_path):
    leader = FakeLeader()
    runner, client = make_runner(tmp_path, leader, FakeEngine())
    return leader, runner._links, client, tmp_path


def sums(txt=b"t", srt=b"s", seg=EMPTY_SEGMENTS, source="a" * 64):
    return OutputChecksums(source=source, txt=sha(txt), srt=sha(srt), segments_json=sha(seg))


def upload_all(links, claim, tmp_path, txt=b"t", srt=b"s", seg=EMPTY_SEGMENTS):
    for name, content in (("txt", txt), ("srt", srt), ("segments_json", seg)):
        path = tmp_path / name
        path.write_bytes(content)
        links.upload(getattr(claim.upload_urls, name), path)


def test_no_work_is_a_204_with_retry_after_and_no_drain_directive(world):
    leader, _, client, _ = world
    leader.retry_after = "10"
    assert client.claim() == NoWork(retry_after=10.0, draining=False)


def test_a_draining_follower_is_told_so_on_an_empty_claim_and_gets_no_job(world):
    leader, _, client, _ = world
    leader.add_job()
    leader.state = "draining"
    leader.retry_after = "10"
    assert client.claim() == NoWork(retry_after=10.0, draining=True)
    assert leader.queue  # nothing was handed out
    response = httpx.Client(transport=leader.transport).post(
        f"{BASE}/v1/jobs/claim", headers={"Authorization": "Bearer credential-SECRET-0"}
    )
    assert (response.status_code, response.headers[DIRECTIVE_HEADER]) == (204, "drain")


def test_a_draining_follower_hears_drain_in_its_heartbeats(world):
    leader, _, client, _ = world
    job_id = leader.add_job()
    claim = client.claim()
    leader.state = "draining"
    assert client.heartbeat(job_id, claim.lease_id, 0.5) == "drain"


def test_fresh_links_are_given_once_per_lease_per_interval_then_429():
    now = [1000.0]
    leader = FakeLeader(links_min_interval=60, clock=lambda: now[0])
    leader.credentials.add("credential-SECRET-0")
    client = LeaderClient(BASE, credential="credential-SECRET-0", transport=leader.transport)
    job_id = leader.add_job()
    claim = client.claim()  # the claim counted as the lease's first links
    now[0] += 30
    with pytest.raises(Transient) as refused:
        client.links(job_id, claim.lease_id)
    assert (refused.value.status, refused.value.retry_after) == (429, 30.0)
    now[0] += 31
    client.links(job_id, claim.lease_id)
    with pytest.raises(Transient) as again:
        client.links(job_id, claim.lease_id)
    assert again.value.retry_after == 60.0


def test_a_download_link_is_bound_to_the_lease(world):
    leader, links, client, tmp_path = world
    job_id = leader.add_job(b"audio")
    claim = client.claim()
    assert links.download(claim.download_url, tmp_path / "a", lambda: None) == sha(b"audio")
    leader.take_over(job_id)  # another follower holds the job: the old lease's links die
    with pytest.raises(LeaseLost):
        links.download(claim.download_url, tmp_path / "b", lambda: None)


def test_links_of_a_cancelled_job_are_refused_as_stale(world):
    leader, links, client, tmp_path = world
    job_id = leader.add_job()
    claim = client.claim()
    leader.cancel(job_id)
    with pytest.raises(LeaseLost):
        links.download(claim.download_url, tmp_path / "a", lambda: None)
    (tmp_path / "out").write_bytes(b"x")
    with pytest.raises(LeaseLost):
        links.upload(claim.upload_urls.txt, tmp_path / "out")


def test_an_expired_link_is_a_403_not_a_lost_lease(world):
    leader, links, client, tmp_path = world
    job_id = leader.add_job()
    claim = client.claim()
    leader.expire_links(job_id)
    with pytest.raises(LinkExpired):
        links.download(claim.download_url, tmp_path / "a", lambda: None)


def test_a_re_lease_makes_the_old_lease_stale_on_every_job_call(world):
    leader, _, client, _ = world
    job_id = leader.add_job()
    old = client.claim()
    client.release(job_id, old.lease_id)
    new = client.claim()
    assert new.lease_id != old.lease_id
    for call in (
        lambda: client.heartbeat(job_id, old.lease_id, None),
        lambda: client.links(job_id, old.lease_id),
        lambda: client.submit(job_id, old.lease_id, sums()),
        lambda: client.fail(job_id, old.lease_id, "other", "x", True),
        lambda: client.release(job_id, old.lease_id),
    ):
        with pytest.raises(Refused) as refused:
            call()
        assert (refused.value.status, refused.value.code) == (409, "stale_lease")
    assert client.heartbeat(job_id, new.lease_id, None) == "continue"


def test_a_cancelled_job_answers_cancel_to_its_heartbeat_and_stale_to_the_rest(world):
    leader, _, client, _ = world
    job_id = leader.add_job()
    claim = client.claim()
    leader.cancel(job_id)
    assert client.heartbeat(job_id, claim.lease_id, None) == "cancel"
    with pytest.raises(Refused) as refused:
        client.links(job_id, claim.lease_id)
    assert refused.value.code == "stale_lease"


def test_submit_verifies_the_stored_outputs_against_the_checksums(world):
    leader, links, client, tmp_path = world
    job_id = leader.add_job()
    claim = client.claim()
    with pytest.raises(Refused) as missing:
        client.submit(job_id, claim.lease_id, sums())
    assert missing.value.code == "outputs_missing"
    upload_all(links, claim, tmp_path)
    with pytest.raises(Refused) as wrong:
        client.submit(job_id, claim.lease_id, sums(txt=b"different"))
    assert (wrong.value.status, wrong.value.code) == (409, "checksum_mismatch")
    client.submit(job_id, claim.lease_id, sums())
    assert leader.jobs[job_id]["state"] == "completed"


def test_the_source_checksum_is_recorded_never_verified(world):
    leader, links, client, tmp_path = world
    job_id = leader.add_job(b"audio")
    claim = client.claim()
    upload_all(links, claim, tmp_path)
    client.submit(job_id, claim.lease_id, sums(source="f" * 64))  # not the audio's digest
    assert leader.submitted[0]["source"] == "f" * 64


def test_an_empty_transcript_needs_empty_txt_and_srt_and_no_segments(world):
    leader, links, client, tmp_path = world
    job_id = leader.add_job()
    claim = client.claim()
    upload_all(links, claim, tmp_path, txt=b"", srt=b"", seg=SOME_SEGMENTS)
    with pytest.raises(Refused) as inconsistent:
        client.submit(job_id, claim.lease_id, sums(txt=b"", srt=b"", seg=SOME_SEGMENTS))
    assert inconsistent.value.code == "outputs_inconsistent"


def test_a_repeated_submit_of_a_completed_job_is_accepted_only_if_identical(world):
    leader, links, client, tmp_path = world
    job_id = leader.add_job()
    claim = client.claim()
    upload_all(links, claim, tmp_path)
    client.submit(job_id, claim.lease_id, sums())
    client.submit(job_id, claim.lease_id, sums())  # the answer was lost: safe to repeat
    assert len(leader.submitted) == 1
    with pytest.raises(Refused) as other:
        client.submit(job_id, claim.lease_id, sums(txt=b"other"))
    assert other.value.code == "stale_lease"


def test_fail_follows_the_real_retry_rules(world):
    leader, _, client, _ = world
    first = leader.add_job()
    claim = client.claim()
    client.fail(first, claim.lease_id, "engine_error", "x", True)
    assert leader.jobs[first]["state"] == "queued"  # retryable, attempts left
    claim = client.claim()
    client.fail(first, claim.lease_id, "undecodable", "x", True)
    assert leader.jobs[first]["state"] == "failed"  # never retried, whatever it says


def test_a_revoked_follower_is_forbidden_and_an_unknown_one_unauthorised(world):
    leader, _, client, _ = world
    leader.state = "revoked"
    with pytest.raises(Refused) as revoked:
        client.claim()
    assert revoked.value.status == 403
    client.credential = "nobody"
    with pytest.raises(Refused) as unknown:
        client.claim()
    assert unknown.value.status == 401


def test_a_job_id_that_is_not_a_uuid_is_a_422_and_an_unknown_job_a_404(world):
    _, _, client, _ = world
    with pytest.raises(Refused) as bad:
        client.heartbeat("not-a-uuid", "lease", None)
    assert bad.value.status == 422
    with pytest.raises(Refused) as gone:
        client.heartbeat("8f0c8a1e-0000-4000-8000-000000000000", "lease", None)
    assert gone.value.status == 404


def test_a_job_runner_never_sends_its_credential_to_a_link(tmp_path):
    leader = FakeLeader(links_min_interval=0)
    runner, client = make_runner(tmp_path, leader, FakeEngine())
    leader.add_job()
    runner.run(client.claim(), JobControl())
    assert leader.bearer_on_links is False
