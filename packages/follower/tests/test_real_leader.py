"""The follower against the REAL leader application, in this process, over real HTTP on a
loopback port, with a real Postgres. Only the engine is a stub, so no model is needed. This
pins the contract the fake leader in follower_testkit imitates: if the leader changes a
route, a status or a header the follower depends on, these fail.

If one of these fails, the follower (or the fake leader) is wrong, never this file: fix the
side the specs support and make the fake leader behave as the real one does."""

# ruff: noqa: E402
# The contract tests need the leader's packages and a Postgres. Where either is missing (a
# follower-only container) they are skipped, visibly, not ignored on the command line. Where
# CI says it is running, a missing piece is a failure: a changed dependency must not turn the
# contract tests into one quiet `s`.
import importlib.util
import os

import pytest


def _unavailable(reason: str) -> None:
    if os.environ.get("CI"):
        pytest.fail(f"CI is set, so the contract tests may not be skipped: {reason}", pytrace=False)
    pytest.skip(reason, allow_module_level=True)


for _needed in ("swarmscribe_leader", "uvicorn", "asyncpg", "sqlalchemy"):
    if importlib.util.find_spec(_needed) is None:
        _unavailable(f"the leader package's dependency {_needed!r} is not installed")
if (
    not os.environ.get("SWARMSCRIBE_TEST_DATABASE_URL")
    and importlib.util.find_spec("pgserver") is None
):
    _unavailable("no Postgres: set SWARMSCRIBE_TEST_DATABASE_URL or install pgserver")

import asyncio
import contextlib
import socket
import threading
import time
import uuid

import uvicorn
from follower_testkit import CPU, SPOKEN, FakeEngine
from leader_testkit import (
    LINK_KEY,
    add_recordings,
    empty_tables,
    migrated_database,
    new_join_token,
)
from sqlalchemy import select
from swarmscribe_follower.agent import Agent
from swarmscribe_follower.config import Settings
from swarmscribe_follower.credentials import CredentialStore
from swarmscribe_follower.device import Probe
from swarmscribe_follower.errors import FollowerExit
from swarmscribe_follower.leader import LeaderClient, NoWork, Refused, Transient
from swarmscribe_follower.models import ModelHost
from swarmscribe_follower.scratch import Scratch
from swarmscribe_follower.transfer import LeaseLost, Links
from swarmscribe_leader.app import create_app
from swarmscribe_leader.auth import followers as follower_admin
from swarmscribe_leader.auth.pool_tokens import create_pool_token
from swarmscribe_leader.clock import utcnow
from swarmscribe_leader.config import Settings as LeaderSettings
from swarmscribe_leader.db.models import AuditEntry, Follower, Job, JobAttempt, JobResult
from swarmscribe_leader.db.session import make_engine, make_sessionmaker
from swarmscribe_leader.jobs import admin as job_admin
from swarmscribe_protocol import OutputChecksums, SegmentsDocument

DATABASE = "swarmscribe_kit_follower"  # the kit refuses names without this prefix


@pytest.fixture(scope="session")
def leader_database() -> str:
    return asyncio.run(migrated_database(DATABASE))


class RealLeader:
    def __init__(self, url: str, database: str) -> None:
        self.url, self.database = url, database

    def on_database(self, work):
        """Run `work(sessionmaker)` (a coroutine function) on a connection of its own."""

        async def go():
            engine = make_engine(self.database)
            try:
                return await work(make_sessionmaker(engine))
            finally:
                await engine.dispose()

        return asyncio.run(go())

    def rows(self, model):
        async def read(sessionmaker):
            async with sessionmaker() as session:
                return list((await session.scalars(select(model).order_by(model.created_at))).all())

        return self.on_database(read)

    def queue(self, root, files, **location):
        return self.on_database(
            lambda sm: add_recordings(sm, root, files, public_url=self.url, **location)
        )

    def join_token(self, max_uses: int = 5) -> str:
        """A join token's plaintext (the kit's helper)."""
        return self.on_database(lambda sm: new_join_token(sm, max_uses=max_uses))

    def pool_token(self, name: str = "follower-tests") -> str:
        """A pool token's plaintext, made with the leader's own function (the kit has no
        pool-token helper)."""

        async def make(sessionmaker):
            async with sessionmaker() as session:
                _row, plaintext = await create_pool_token(
                    session, name=name, pool="default", actor="follower tests"
                )
                await session.commit()
            return plaintext

        return self.on_database(make)

    def drain(self, follower_id) -> None:
        async def drain(sessionmaker):
            async with sessionmaker() as session:
                await follower_admin.drain(session, follower_id, actor="test")
                await session.commit()

        self.on_database(drain)

    def revoke(self, follower_id) -> None:
        async def revoke(sessionmaker):
            async with sessionmaker() as session:
                await follower_admin.revoke_follower(
                    session, follower_id, now=utcnow(), actor="test"
                )
                await session.commit()

        self.on_database(revoke)


@contextlib.contextmanager
def serving(database: str, **overrides):
    """The real leader application, served by uvicorn on a loopback port of its own, over a
    database emptied first."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    url = f"http://127.0.0.1:{listener.getsockname()[1]}"
    values = {
        "database_url": database,
        "public_url": url,  # the leader's own links must be reachable by the follower
        "link_key": LINK_KEY,
        "lease_seconds": 30,
        "heartbeat_seconds": 1,
        "claim_retry_after": 1,
        "links_refresh_min_seconds": 0,
    }
    values.update(overrides)
    running = RealLeader(url, database)

    async def clear(sessionmaker):
        async with sessionmaker() as session:
            await empty_tables(session)

    running.on_database(clear)
    application = create_app(LeaderSettings(**values), background=False)
    server = uvicorn.Server(uvicorn.Config(application, log_level="error"))
    thread = threading.Thread(
        target=lambda: asyncio.run(server.serve(sockets=[listener])), daemon=True
    )
    thread.start()
    try:
        deadline = time.monotonic() + 30
        while not server.started:
            assert time.monotonic() < deadline and thread.is_alive(), "the leader did not start"
            time.sleep(0.01)
        yield running
    finally:
        server.should_exit = True
        thread.join(30)
        listener.close()
        assert not thread.is_alive(), "the leader did not stop"


@pytest.fixture
def leader(leader_database):
    with serving(leader_database) as running:
        yield running


@pytest.fixture
def strict_leader(leader_database):
    """The leader with its default rule that fresh links come at most once a minute."""
    with serving(leader_database, links_refresh_min_seconds=60) as running:
        yield running


def follower(tmp_path, leader, engine, token, name="state", **overrides) -> Agent:
    settings = Settings(
        leader_url=leader.url,
        allow_http=True,  # the leader is plain http on loopback
        join_token=token,
        state_dir=tmp_path / name,
        model_dir=tmp_path / "models",
        device="cpu",
        **overrides,
    )
    return Agent(
        settings,
        client=LeaderClient(leader.url),
        links=Links(allow_http=True),  # the leader's links are http on loopback
        models=ModelHost("cpu", factory=engine),
        scratch=Scratch(settings.scratch, settings.state_dir),
        store=CredentialStore(settings.credential_file),
        probe=Probe(CPU),
        heartbeat_interval=0.1,
        parked_poll_seconds=0.1,
    )


class Served:
    def __init__(self, agent: Agent) -> None:
        self.agent, self.codes = agent, []
        agent.prepare()
        self.thread = threading.Thread(target=lambda: self.codes.append(agent.serve()))
        self.thread.start()

    def code(self) -> int:
        self.thread.join(30)
        assert not self.thread.is_alive(), "the follower did not stop"
        return self.codes[0]


@contextlib.contextmanager
def prepared(agent: Agent):
    """An agent that has registered (or reused its credential) and holds nothing else: for
    the tests that make each call themselves."""
    agent.prepare()
    try:
        yield agent
    finally:
        agent.close()


def until(condition, what):
    deadline = time.monotonic() + 30
    while not condition():
        assert time.monotonic() < deadline, f"{what} never happened"
        time.sleep(0.05)


def states(leader) -> list[str]:
    return sorted(job.state for job in leader.rows(Job))


def checksums(value: str) -> OutputChecksums:
    return OutputChecksums(source=value, txt=value, srt=value, segments_json=value)


def hold_at_half(engine):
    """Make the stub engine stop at half way through a job until `release` is set."""
    reached, release = threading.Event(), threading.Event()

    def hold(fraction):
        if fraction == 0.5:
            reached.set()
            assert release.wait(30)

    engine.on_step = hold
    return reached, release


@pytest.mark.parametrize("kind", ["join token", "pool token"])
def test_a_follower_joins_and_completes_every_consented_recording(tmp_path, leader, kind):
    root = tmp_path / "archive"
    leader.queue(
        root,
        {
            "talks/one.wav": b"recording one",
            "talks/two.wav": b"recording two",
            "private/held.wav": b"never consented",
        },
        consent="talks/*\n",
        channel_mode="auto",
        channel_labels=("Agent", "Customer"),
    )
    engine = FakeEngine()
    token = leader.join_token() if kind == "join token" else leader.pool_token()
    served = Served(follower(tmp_path, leader, engine, token))
    until(lambda: states(leader) == ["completed", "completed"], "both jobs")
    served.agent.stop()
    assert served.code() == 0

    for key in ("talks/one.wav", "talks/two.wav"):
        outputs = root / "transcripts" / key
        assert outputs.with_name(outputs.name + ".txt").read_text(encoding="utf-8") == SPOKEN + "\n"
        document = SegmentsDocument.model_validate_json(
            outputs.with_name(outputs.name + ".segments.json").read_bytes()
        )
        assert document.settings.channel_mode == "auto"
    assert not (root / "transcripts" / "private").exists()
    assert sorted(heard["audio"] for heard in engine.transcribed) == [
        b"recording one",
        b"recording two",
    ]
    assert {heard["settings"].channel_labels for heard in engine.transcribed} == {
        ("Agent", "Customer")
    }
    assert [attempt.outcome for attempt in leader.rows(JobAttempt)] == ["completed", "completed"]
    results = leader.rows(JobResult)
    assert len(results) == 2  # submitted, and the leader verified the bytes it holds
    (row,) = leader.rows(Follower)
    assert (row.state, row.capabilities["device"]) == ("gone", "cpu")  # it deregistered
    actions = [entry.action for entry in leader.rows(AuditEntry)]
    assert actions.count("job.submit") == 2 and "follower.deregister" in actions


def test_an_idle_follower_hears_the_drain_exits_and_stays_drained_across_restarts(
    tmp_path, leader
):
    engine = FakeEngine()
    token = leader.join_token()
    served = Served(follower(tmp_path, leader, engine, token))
    until(lambda: leader.rows(Follower), "the registration")
    (row,) = leader.rows(Follower)
    leader.drain(row.id)
    assert served.code() == 0  # nobody stopped it: it heard `drain` on a 204
    assert served.agent.exit_reason == "drained"
    leader.queue(tmp_path / "archive", {"talks/one.wav": b"one"})
    for _restart in range(2):  # what a service manager does
        assert Served(follower(tmp_path, leader, engine, token)).code() == 0
    (row,) = leader.rows(Follower)  # still one follower: no restart registered again
    assert row.state == "draining"
    assert states(leader) == ["queued"] and engine.transcribed == []


def test_a_parked_follower_stays_up_under_a_restart_always_policy(tmp_path, leader):
    engine = FakeEngine()
    served = Served(follower(tmp_path, leader, engine, leader.join_token(), on_drained="park"))
    until(lambda: leader.rows(Follower), "the registration")
    (row,) = leader.rows(Follower)
    leader.drain(row.id)
    until(lambda: served.agent.drained, "the drain to be heard")
    assert served.thread.is_alive()
    served.agent.stop()
    assert served.code() == 0


def test_a_revoked_follower_stops_its_job_exits_4_and_cannot_come_back(tmp_path, leader):
    leader.queue(tmp_path / "archive", {"talks/one.wav": b"one"})
    engine = FakeEngine()
    reached, release = hold_at_half(engine)
    token = leader.join_token()
    served = Served(follower(tmp_path, leader, engine, token))
    assert reached.wait(30)
    (row,) = leader.rows(Follower)
    leader.revoke(row.id)
    until(lambda: served.agent._control.reason is not None, "the lease keeper to notice")
    release.set()
    assert served.code() == 4
    (job,) = leader.rows(Job)
    assert (job.state, job.attempts) == ("queued", 0)  # the leader released it, uncounted
    assert not (tmp_path / "archive" / "transcripts").exists()
    engine.on_step = None
    assert Served(follower(tmp_path, leader, engine, token)).code() == 4
    assert len(leader.rows(Follower)) == 1  # the token it still holds was not used again


def test_a_stop_mid_job_releases_it_without_a_counted_attempt(tmp_path, leader):
    leader.queue(tmp_path / "archive", {"talks/one.wav": b"one"})
    engine = FakeEngine()
    reached, release = hold_at_half(engine)
    served = Served(
        follower(tmp_path, leader, engine, leader.join_token(), shutdown_grace_seconds=0)
    )
    assert reached.wait(30)
    served.agent.stop()
    release.set()
    assert served.code() == 0
    (job,) = leader.rows(Job)
    (attempt,) = leader.rows(JobAttempt)
    assert (job.state, job.attempts, attempt.outcome) == ("queued", 0, "released")


def test_a_cancelled_job_is_abandoned_and_its_uploads_are_refused(tmp_path, leader):
    leader.queue(tmp_path / "archive", {"talks/one.wav": b"one"})
    engine = FakeEngine()
    reached, release = hold_at_half(engine)
    served = Served(follower(tmp_path, leader, engine, leader.join_token()))
    assert reached.wait(30)
    (job,) = leader.rows(Job)

    async def cancel(sessionmaker):
        async with sessionmaker() as session:
            await job_admin.cancel_job(session, job.id, now=utcnow(), actor="test")
            await session.commit()

    leader.on_database(cancel)
    until(lambda: served.agent._control.reason == "cancelled", "the cancel directive")
    release.set()
    until(lambda: served.agent._control is None, "the job to end")
    served.agent.stop()
    assert served.code() == 0
    assert states(leader) == ["cancelled"]
    assert not (tmp_path / "archive" / "transcripts").exists()


def test_the_real_routes_answer_as_the_client_expects(tmp_path, leader):
    """Each call the follower makes, once, against the real leader: statuses, bodies and
    headers the fake leader copies."""
    leader.queue(tmp_path / "archive", {"talks/one.wav": b"one"})
    with prepared(follower(tmp_path, leader, FakeEngine(), leader.join_token())) as agent:
        client, links = agent._client, Links(allow_http=True)
        claim = client.claim()
        assert client.heartbeat(claim.job_id, claim.lease_id, 0.5) == "continue"
        assert client.claim() == NoWork(retry_after=1.0, draining=False)
        fresh = client.links(claim.job_id, claim.lease_id)
        source = tmp_path / "source"
        links.download(fresh.download_url, source, lambda: None)
        assert source.read_bytes() == b"one"
        output = tmp_path / "out.txt"
        output.write_bytes(b"text\n")
        links.upload(fresh.upload_urls.txt, output)
        with pytest.raises(Refused) as refused:
            client.heartbeat(claim.job_id, str(uuid.uuid4()), None)
        assert (refused.value.status, refused.value.code) == (409, "stale_lease")
        with pytest.raises(Refused) as missing:
            client.submit(claim.job_id, claim.lease_id, checksums("a" * 64))
        assert (missing.value.status, missing.value.code) == (409, "outputs_missing")
        client.release(claim.job_id, claim.lease_id)
        with pytest.raises(LeaseLost):
            links.upload(fresh.upload_urls.txt, output)  # the lease ended with the release
        again = client.claim()
        client.fail(again.job_id, again.lease_id, "undecodable", "not audio", False)
        assert states(leader) == ["failed"]
        client.deregister()
        stranger = LeaderClient(leader.url, credential="x" * 43)
        with pytest.raises(Refused) as unknown:
            stranger.claim()
        assert unknown.value.status == 401


def test_pods_that_come_and_go_with_a_pool_token_reuse_one_follower_row(tmp_path, leader):
    token = leader.pool_token("cpu-pods")
    engine = FakeEngine()
    for pod in range(3):  # each pod has a new, empty state folder
        served = Served(follower(tmp_path, leader, engine, token, name=f"pod-{pod}"))
        until(lambda: any(row.state == "active" for row in leader.rows(Follower)), "the pod")
        served.agent.stop()
        assert served.code() == 0
    (row,) = leader.rows(Follower)
    assert row.state == "gone"
    registers = [e for e in leader.rows(AuditEntry) if e.action == "follower.register"]
    assert [entry.detail["reused"] for entry in registers] == [False, True, True]


# --- what the review of tasks 6-9 said the contract test must cover ---------------------


def test_a_single_use_join_token_cannot_register_a_second_follower(tmp_path, leader):
    token = leader.join_token(max_uses=1)
    engine = FakeEngine()
    served = Served(follower(tmp_path, leader, engine, token, name="first"))
    until(lambda: any(row.state == "active" for row in leader.rows(Follower)), "the first")
    served.agent.stop()
    assert served.code() == 0
    second = follower(tmp_path, leader, engine, token, name="second")
    with pytest.raises(FollowerExit) as refused:
        second.prepare()  # a new state folder: it must register, and the token is spent
    assert refused.value.code == 4
    assert "not valid" in refused.value.reason and token not in refused.value.reason
    assert len(leader.rows(Follower)) == 1
    assert not (tmp_path / "second" / "credential.json").exists()


def test_a_pool_tokens_reused_row_kills_the_credential_it_had(tmp_path, leader):
    token = leader.pool_token("reuse")
    engine = FakeEngine()
    first = Served(follower(tmp_path, leader, engine, token, name="pod-0"))
    until(lambda: any(row.state == "active" for row in leader.rows(Follower)), "the first pod")
    first.agent.stop()
    assert first.code() == 0
    old = CredentialStore(tmp_path / "pod-0" / "credential.json").load().credential
    with prepared(follower(tmp_path, leader, engine, token, name="pod-1")) as second:
        assert second.follower_id == str(leader.rows(Follower)[0].id)  # the same row
        with pytest.raises(Refused) as dead:
            LeaderClient(leader.url, credential=old).claim()
        assert dead.value.status == 401  # the old credential is no more
        assert second._client.claim() == NoWork(retry_after=1.0, draining=False)
    assert len(leader.rows(Follower)) == 1


def test_revoking_releases_the_lease_and_kills_every_link_it_gave(tmp_path, leader):
    leader.queue(tmp_path / "archive", {"talks/one.wav": b"one"})
    with prepared(follower(tmp_path, leader, FakeEngine(), leader.join_token())) as agent:
        client, links = agent._client, Links(allow_http=True)
        claim = client.claim()
        fresh = client.links(claim.job_id, claim.lease_id)
        (row,) = leader.rows(Follower)
        leader.revoke(row.id)
        (job,) = leader.rows(Job)
        assert (job.state, job.attempts) == ("queued", 0)  # released at once, uncounted
        output = tmp_path / "out.txt"
        output.write_bytes(b"text\n")
        with pytest.raises(LeaseLost):
            links.upload(fresh.upload_urls.txt, output)
        with pytest.raises(LeaseLost):
            links.download(fresh.download_url, tmp_path / "source", lambda: None)
        with pytest.raises(Refused) as revoked:
            client.heartbeat(claim.job_id, claim.lease_id, None)
        assert revoked.value.status == 403
        with pytest.raises(Refused) as refused_claim:
            client.claim()
        assert refused_claim.value.status == 403


def test_fresh_links_come_once_a_minute_and_a_429_says_when_to_ask_again(tmp_path, strict_leader):
    strict_leader.queue(tmp_path / "archive", {"talks/one.wav": b"one"})
    with prepared(follower(tmp_path, strict_leader, FakeEngine(), strict_leader.join_token())) as a:
        claim = a._client.claim()  # the claim counts as the first issue of links
        with pytest.raises(Transient) as slow_down:
            a._client.links(claim.job_id, claim.lease_id)
        assert slow_down.value.status == 429
        assert 0 < slow_down.value.retry_after <= 60


def test_a_download_link_dies_when_the_job_is_leased_again(tmp_path, leader):
    leader.queue(tmp_path / "archive", {"talks/one.wav": b"one"})
    with prepared(follower(tmp_path, leader, FakeEngine(), leader.join_token())) as agent:
        client, links = agent._client, Links(allow_http=True)
        first = client.claim()
        client.release(first.job_id, first.lease_id)
        second = client.claim()
        assert (second.job_id, second.lease_id != first.lease_id) == (first.job_id, True)
        with pytest.raises(LeaseLost):
            links.download(first.download_url, tmp_path / "stale", lambda: None)
        links.download(second.download_url, tmp_path / "current", lambda: None)
        assert (tmp_path / "current").read_bytes() == b"one"


def test_a_draining_follower_is_told_so_on_an_empty_claim(tmp_path, leader):
    with prepared(follower(tmp_path, leader, FakeEngine(), leader.join_token())) as agent:
        assert agent._client.claim() == NoWork(retry_after=1.0, draining=False)
        leader.drain(uuid.UUID(agent.follower_id))
        assert agent._client.claim() == NoWork(retry_after=1.0, draining=True)
