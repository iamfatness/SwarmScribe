import asyncio
import dataclasses
import logging
from datetime import timedelta

import pytest
from console_testkit import CREDENTIAL, STATUS
from sqlalchemy import select, text
from swarmscribe_console.app import create_app
from swarmscribe_console.crypto import ConsoleKeys
from swarmscribe_console.db.models import AuditEntry, ConsoleSession, Leader, Snapshot
from swarmscribe_console.leaders import edit_leader, replace_credential, sealing_context
from swarmscribe_console.poller import POLL_LOCK_BASE, PollerConfig, poll_due_leaders, prune
from swarmscribe_leader.auth.consoles import parse_delegation
from swarmscribe_leader.clock import utcnow

HOST = "eu-1.leaders.example"
ROTATED = "R" * 43
CONFIG = PollerConfig(
    interval=timedelta(seconds=15),
    timeout=0.2,
    unreachable_after=3,
    history=timedelta(hours=24),
    concurrency=8,
)


@pytest.fixture
def poll(engine, sessionmaker, keys, leader_client):
    async def run(now, **overrides):
        config = dataclasses.replace(CONFIG, **overrides)
        return await poll_due_leaders(
            engine, sessionmaker, leader_client, keys, now=now, config=config
        )

    return run


async def _leader(sessionmaker, name="eu-1") -> Leader:
    async with sessionmaker() as session:
        return (await session.scalars(select(Leader).where(Leader.name == name))).one()


async def _snapshots(sessionmaker) -> list[Snapshot]:
    async with sessionmaker() as session:
        return list((await session.scalars(select(Snapshot).order_by(Snapshot.id))).all())


async def test_a_healthy_leader_is_polled_as_the_poller_and_its_status_kept(
    poll, factory, fake_leader, sessionmaker
):
    await factory.leader("eu-1")
    now = utcnow()
    assert await poll(now) == {"eu-1": "ok"}
    (request,) = fake_leader.requests
    assert str(request.url) == f"https://{HOST}/v1/admin/status"
    assert request.headers["authorization"] == f"Console {CREDENTIAL}"
    assert request.headers["x-swarmscribe-actor"] == "system:poller"
    assert request.headers["x-swarmscribe-actor-role"] == "viewer"
    who, role = parse_delegation(
        request.headers.get_list("x-swarmscribe-actor"),
        request.headers.get_list("x-swarmscribe-actor-role"),
    )
    assert who.is_poller and role == "viewer"
    (snapshot,) = await _snapshots(sessionmaker)
    assert (snapshot.reachable, snapshot.outcome, snapshot.taken_at) == (True, "ok", now)
    assert snapshot.status == STATUS
    assert snapshot.status["follower_pools"][0]["pool"] == "default"
    leader = await _leader(sessionmaker)
    assert (leader.last_polled_at, leader.last_success_at) == (now, now)
    assert (leader.consecutive_failures, leader.last_error) == (0, None)


async def test_a_leader_is_polled_at_most_once_per_interval(poll, factory, fake_leader):
    await factory.leader("eu-1")
    now = utcnow()
    assert await poll(now) == {"eu-1": "ok"}
    assert await poll(now + timedelta(seconds=5)) == {}
    assert await poll(now + timedelta(seconds=14)) == {"eu-1": "ok"}
    assert len(fake_leader.requests) == 2


async def test_a_slow_leader_times_out_as_a_failure(poll, factory, fake_leader, sessionmaker):
    await factory.leader("eu-1")
    fake_leader.modes[HOST] = "slow"
    fake_leader.delay = 2.0
    loop = asyncio.get_running_loop()
    started = loop.time()
    assert await poll(utcnow()) == {"eu-1": "timeout"}
    assert loop.time() - started < 1.5
    (snapshot,) = await _snapshots(sessionmaker)
    assert (snapshot.reachable, snapshot.outcome, snapshot.status) == (False, "timeout", None)
    assert (await _leader(sessionmaker)).consecutive_failures == 1


async def test_three_failures_in_a_row_count_and_one_success_clears_them(
    poll, factory, fake_leader, sessionmaker
):
    await factory.leader("eu-1")
    fake_leader.modes[HOST] = "down"
    start = utcnow()
    for step in range(3):
        outcome = await poll(start + timedelta(seconds=15 * step))
        assert outcome == {"eu-1": "connect_error"}
    leader = await _leader(sessionmaker)
    assert (leader.consecutive_failures, leader.last_error) == (3, "connect_error")
    assert leader.last_success_at is None
    del fake_leader.modes[HOST]
    assert await poll(start + timedelta(seconds=45)) == {"eu-1": "ok"}
    assert (await _leader(sessionmaker)).consecutive_failures == 0


def _without(field: str) -> dict:
    return {key: value for key, value in STATUS.items() if key != field}


async def test_nothing_queued_is_a_valid_status(poll, factory, fake_leader, sessionmaker):
    await factory.leader("eu-1")
    fake_leader.status["oldest_queued_age_s"] = None
    assert await poll(utcnow()) == {"eu-1": "ok"}
    (snapshot,) = await _snapshots(sessionmaker)
    assert snapshot.status["oldest_queued_age_s"] is None
    assert snapshot.status["completed_last_day"] == 30


@pytest.mark.parametrize(
    ("reply", "outcome"),
    [
        ((200, {"hello": 1}, {}), "bad_response"),
        ((200, {**STATUS, "completed_last_hour": -1}, {}), "bad_response"),
        ((200, {**STATUS, "completed_last_hour": 10**15}, {}), "bad_response"),
        ((200, _without("completed_last_day"), {}), "bad_response"),
        ((200, _without("oldest_queued_age_s"), {}), "bad_response"),
        ((200, {**STATUS, "oldest_queued_age_s": -5}, {}), "bad_response"),
        ((200, {**STATUS, "oldest_queued_age_s": "420"}, {}), "bad_response"),
        ((200, b"not json", {"Content-Type": "text/plain"}), "bad_response"),
        ((500, {"code": "internal", "message": "internal error"}, {}), "http_500"),
        ((403, {"code": "forbidden", "message": "no"}, {}), "forbidden"),
        ((302, b"", {"Location": "https://elsewhere.example/"}), "http_302"),
        (
            (401, {"code": "unauthorized", "message": "this console credential has been revoked"}, {}),  # noqa: E501
            "credential_rejected",
        ),
    ],
    ids=[
        "wrong-shape",
        "negative",
        "absurd",
        "no-completed-last-day",
        "no-queue-age",
        "negative-queue-age",
        "queue-age-as-text",
        "not-json",
        "500",
        "403",
        "redirect",
        "revoked-message-without-the-code",
    ],
)
async def test_odd_answers_are_failures_and_store_no_status(
    poll, factory, fake_leader, sessionmaker, reply, outcome
):
    await factory.leader("eu-1")
    fake_leader.replies[("GET", "/v1/admin/status")] = reply
    assert await poll(utcnow()) == {"eu-1": outcome}
    (snapshot,) = await _snapshots(sessionmaker)
    assert (snapshot.reachable, snapshot.status) == (False, None)
    assert (await _leader(sessionmaker)).consecutive_failures == 1


async def test_an_unknown_credential_is_a_failure_and_polling_goes_on(
    poll, factory, fake_leader, sessionmaker
):
    await factory.leader("eu-1")
    fake_leader.modes[HOST] = "unknown"
    now = utcnow()
    assert await poll(now) == {"eu-1": "credential_rejected"}
    assert await poll(now + timedelta(seconds=15)) == {"eu-1": "credential_rejected"}
    leader = await _leader(sessionmaker)
    assert (leader.consecutive_failures, leader.credential_revoked_at) == (2, None)


async def test_a_revoked_credential_stops_polling_until_it_is_replaced(
    poll, factory, fake_leader, sessionmaker, keys
):
    await factory.leader("eu-1")
    fake_leader.modes[HOST] = "revoked"
    now = utcnow()
    assert await poll(now) == {"eu-1": "credential_revoked"}
    leader = await _leader(sessionmaker)
    assert leader.credential_revoked_at == now
    assert leader.consecutive_failures == 0
    for later in (15, 30, 300):
        assert await poll(now + timedelta(seconds=later)) == {}
    assert len(fake_leader.requests) == 1
    async with sessionmaker() as session:
        (entry,) = (await session.scalars(select(AuditEntry))).all()
    assert (entry.actor, entry.action, entry.leader) == (
        "system:poller",
        "leader.credential_revoked",
        "eu-1",
    )

    async with sessionmaker() as session:
        await replace_credential(session, keys, "eu-1", ROTATED, now=utcnow(), actor="t")
        await session.commit()
    del fake_leader.modes[HOST]
    assert await poll(now + timedelta(seconds=301)) == {"eu-1": "ok"}
    assert fake_leader.requests[-1].headers["authorization"] == f"Console {ROTATED}"


async def test_a_credential_rotated_during_a_poll_is_not_marked_by_its_answer(
    poll, factory, fake_leader, sessionmaker, keys
):
    await factory.leader("eu-1")

    async def rotate(_request):
        async with sessionmaker() as session:
            await replace_credential(session, keys, "eu-1", ROTATED, now=utcnow(), actor="t")
            await session.commit()

    fake_leader.on_request = rotate
    fake_leader.modes[HOST] = "revoked"
    assert await poll(utcnow()) == {"eu-1": "credential_revoked"}
    leader = await _leader(sessionmaker)
    assert leader.credential_revoked_at is None
    assert leader.last_polled_at is None  # the rotation's reset stands
    # Sealed bound to the name and URL: open with the row's own sealing context.
    context = sealing_context(leader.name, leader.base_url)
    assert keys.open_credential(context, leader.credential) == ROTATED
    assert await _snapshots(sessionmaker) == []  # the late answer is dropped whole


async def test_disabled_leaders_are_not_polled(poll, factory, fake_leader):
    await factory.leader("eu-1", enabled=False)
    assert await poll(utcnow()) == {}
    assert fake_leader.requests == []


async def test_a_credential_the_key_cannot_open_is_a_failure_with_no_call(
    poll, factory, fake_leader, sessionmaker
):
    factory.keys = ConsoleKeys(bytes(32))  # sealed with another key
    await factory.leader("eu-1")
    assert await poll(utcnow()) == {"eu-1": "credential_unreadable"}
    assert fake_leader.requests == []
    assert (await _leader(sessionmaker)).last_error == "credential_unreadable"


async def test_every_due_leader_is_polled_in_one_round(poll, factory, fake_leader):
    for name in ("eu-1", "us-1", "ap-1"):
        await factory.leader(name)
    fake_leader.modes["us-1.leaders.example"] = "down"
    assert await poll(utcnow(), concurrency=2) == {
        "ap-1": "ok",
        "eu-1": "ok",
        "us-1": "connect_error",
    }


async def test_two_replicas_poll_a_leader_once(poll, factory, fake_leader):
    await factory.leader("eu-1")
    now = utcnow()
    first, second = await asyncio.gather(poll(now), poll(now))
    # The loser either found the lock taken ("locked") or, once it got the lock, found the
    # leader just polled (skipped, so absent from its answer).
    outcomes = [first.get("eu-1"), second.get("eu-1")]
    assert outcomes.count("ok") == 1
    assert set(outcomes) <= {"ok", "locked", None}
    assert len(fake_leader.requests) == 1


async def test_a_leader_locked_by_another_replica_is_skipped(poll, factory, fake_leader, engine):
    leader = await factory.leader("eu-1")
    async with engine.connect() as other_replica:
        key = POLL_LOCK_BASE + leader.id
        await other_replica.execute(text("select pg_advisory_lock(:key)"), {"key": key})
        assert await poll(utcnow()) == {"eu-1": "locked"}
        await other_replica.execute(text("select pg_advisory_unlock(:key)"), {"key": key})
        await other_replica.commit()
    assert fake_leader.requests == []


async def test_history_older_than_a_day_and_ended_sessions_are_pruned(
    sessionmaker, factory, client
):
    leader = await factory.leader("eu-1")
    now = utcnow()
    async with sessionmaker() as session:
        for hours in (25, 23):
            session.add(
                Snapshot(
                    leader_id=leader.id,
                    taken_at=now - timedelta(hours=hours),
                    reachable=True,
                    outcome="ok",
                    status=STATUS,
                )
            )
        await session.commit()
    await factory.person(client, now=now - timedelta(hours=9))
    removed = await prune(
        sessionmaker, now=now, history=timedelta(hours=24), session_idle=timedelta(hours=1)
    )
    assert removed == 2
    assert [s.taken_at for s in await _snapshots(sessionmaker)] == [now - timedelta(hours=23)]
    async with sessionmaker() as session:
        assert (await session.scalars(select(ConsoleSession))).all() == []


async def test_the_app_polls_in_the_background_and_stops_cleanly(
    engine, make_settings, idp, fake_leader, factory
):
    await factory.leader("eu-1")
    application = create_app(
        make_settings(poll_tick_seconds=0.05),
        background=True,
        fetch=idp.fetch,
        idp_transport=idp.transport,
        leader_transport=fake_leader.transport,
    )
    async with application.router.lifespan_context(application):
        for _ in range(100):
            if fake_leader.requests:
                break
            await asyncio.sleep(0.05)
    assert fake_leader.requests
    assert fake_leader.requests[0].url.path == "/v1/admin/status"


STATUS_FIELDS = list(STATUS)
WRONG_TYPES = {
    "jobs": [[], "x", None, {"queued": -1}, {"queued": "3"}, {"queued": True}],
    "pools": [{}, "x", None],
    "followers": [[], None, {"active": -1}, {"active": 1.5}],
    "follower_pools": [{}, "x", None],
    "completed_last_hour": [-1, "7", None, 7.5, True, 10**13],
    "completed_last_day": [-1, "7", None, 7.5, True, 10**13],
    "oldest_queued_age_s": [-1, "7", 7.5, True, 10**13],
    "failed_attempts_last_day": [-1, "7", None, 7.5, True, 10**13],
    "locations": [{}, "x", None],
}


@pytest.mark.parametrize("field", STATUS_FIELDS)
async def test_each_status_field_is_required(poll, factory, fake_leader, sessionmaker, field):
    await factory.leader("eu-1")
    fake_leader.replies[("GET", "/v1/admin/status")] = (200, _without(field), {})
    assert await poll(utcnow()) == {"eu-1": "bad_response"}
    (snapshot,) = await _snapshots(sessionmaker)
    assert (snapshot.reachable, snapshot.status) == (False, None)


@pytest.mark.parametrize(
    ("field", "value"),
    [(field, value) for field, values in WRONG_TYPES.items() for value in values],
)
async def test_each_status_field_must_have_its_type(
    poll, factory, fake_leader, sessionmaker, field, value
):
    await factory.leader("eu-1")
    fake_leader.replies[("GET", "/v1/admin/status")] = (200, {**STATUS, field: value}, {})
    assert await poll(utcnow()) == {"eu-1": "bad_response"}
    (snapshot,) = await _snapshots(sessionmaker)
    assert snapshot.status is None


async def test_an_empty_success_body_is_a_bad_response(poll, factory, fake_leader, sessionmaker):
    await factory.leader("eu-1")
    fake_leader.replies[("GET", "/v1/admin/status")] = (
        200,
        b"",
        {"Content-Type": "application/json"},
    )
    assert await poll(utcnow()) == {"eu-1": "bad_response"}
    assert (await _leader(sessionmaker)).consecutive_failures == 1


async def test_a_status_answer_over_one_mebibyte_is_not_stored(
    poll, factory, fake_leader, sessionmaker
):
    await factory.leader("eu-1")
    big = {**STATUS, "locations": [{"note": "x" * (1024 * 1024)}]}
    fake_leader.replies[("GET", "/v1/admin/status")] = (200, big, {})
    assert await poll(utcnow()) == {"eu-1": "bad_response"}
    (snapshot,) = await _snapshots(sessionmaker)
    assert snapshot.status is None


async def test_a_revoked_answer_is_audited_once_and_is_not_a_failure(
    poll, factory, fake_leader, sessionmaker
):
    await factory.leader("eu-1")
    fake_leader.modes[HOST] = "revoked"
    now = utcnow()
    assert await poll(now) == {"eu-1": "credential_revoked"}
    assert await poll(now + timedelta(minutes=5)) == {}
    leader = await _leader(sessionmaker)
    assert (leader.consecutive_failures, leader.last_error) == (0, "credential_revoked")
    async with sessionmaker() as session:
        assert len((await session.scalars(select(AuditEntry))).all()) == 1


async def test_a_url_change_during_a_poll_drops_the_late_answer(
    poll, factory, fake_leader, sessionmaker, keys
):
    await factory.leader("eu-1")

    async def move(_request):
        async with sessionmaker() as session:
            await edit_leader(
                session,
                "eu-1",
                base_url="https://eu-2.leaders.example",
                credential=ROTATED,
                keys=keys,
                now=utcnow(),
                actor="t",
            )
            await session.commit()

    fake_leader.on_request = move
    fake_leader.modes[HOST] = "revoked"
    assert await poll(utcnow()) == {"eu-1": "credential_revoked"}
    leader = await _leader(sessionmaker)
    assert leader.base_url == "https://eu-2.leaders.example"
    assert (leader.credential_revoked_at, leader.last_polled_at) == (None, None)
    assert leader.consecutive_failures == 0
    assert await _snapshots(sessionmaker) == []


async def test_a_disabled_leader_is_left_out_of_a_round_with_enabled_ones(
    poll, factory, fake_leader
):
    await factory.leader("eu-1")
    await factory.leader("eu-2", enabled=False)
    assert await poll(utcnow()) == {"eu-1": "ok"}
    assert [r.url.host for r in fake_leader.requests] == [HOST]


async def test_pruning_keeps_exactly_the_last_24_hours(sessionmaker, factory):
    leader = await factory.leader("eu-1")
    now = utcnow()
    offsets = [
        timedelta(hours=24, seconds=1),
        timedelta(hours=24),
        timedelta(hours=24) - timedelta(seconds=1),
    ]
    async with sessionmaker() as session:
        for offset in offsets:
            session.add(
                Snapshot(
                    leader_id=leader.id,
                    taken_at=now - offset,
                    reachable=True,
                    outcome="ok",
                    status=STATUS,
                )
            )
        await session.commit()
    removed = await prune(
        sessionmaker, now=now, history=timedelta(hours=24), session_idle=timedelta(hours=1)
    )
    assert removed == 1
    kept = sorted(s.taken_at for s in await _snapshots(sessionmaker))
    assert kept == sorted([now - offsets[1], now - offsets[2]])


SECRET = "".join(["leader-", "secret-value"])  # built here, so no source line spells it


async def test_a_poll_that_raises_logs_frames_only(
    engine, make_settings, idp, fake_leader, factory, caplog
):
    await factory.leader("eu-1")

    async def explode(_request):
        raise RuntimeError(SECRET)

    fake_leader.on_request = explode
    application = create_app(
        make_settings(poll_tick_seconds=0.05),
        background=True,
        fetch=idp.fetch,
        idp_transport=idp.transport,
        leader_transport=fake_leader.transport,
    )
    with caplog.at_level(logging.DEBUG):
        async with application.router.lifespan_context(application):
            for _ in range(100):
                if "polling leader eu-1 failed" in caplog.text:
                    break
                await asyncio.sleep(0.05)
    assert "polling leader eu-1 failed: RuntimeError" in caplog.text
    assert SECRET not in caplog.text


async def test_a_failing_background_step_is_contained_and_logs_no_text(
    engine, make_settings, idp, fake_leader, caplog, monkeypatch
):
    async def boom(*_args, **_kwargs):
        raise RuntimeError(SECRET)

    monkeypatch.setattr("swarmscribe_console.app.poll_due_leaders", boom)
    monkeypatch.setattr("swarmscribe_console.app.prune", boom)
    application = create_app(
        make_settings(poll_tick_seconds=0.05, prune_interval_seconds=0.05),
        background=True,
        fetch=idp.fetch,
        idp_transport=idp.transport,
        leader_transport=fake_leader.transport,
    )
    with caplog.at_level(logging.DEBUG):
        async with application.router.lifespan_context(application):
            for _ in range(100):
                if caplog.text.count("failed: RuntimeError") >= 4:  # both loops, twice
                    break
                await asyncio.sleep(0.05)
    assert "background task poller failed: RuntimeError" in caplog.text
    assert "background task prune failed: RuntimeError" in caplog.text
    assert SECRET not in caplog.text


async def test_the_engine_pool_holds_the_pollers_concurrency_plus_headroom(app):
    pool = app.state.engine.pool
    concurrency = app.state.poller_config.concurrency
    assert pool.size() >= 2 * concurrency + 2
    assert pool._max_overflow >= 10


async def test_an_unexpected_exception_is_recorded_as_a_failure_and_cadence_advances(
    poll, factory, fake_leader, sessionmaker, caplog
):
    await factory.leader("eu-1")

    async def explode(_request):
        raise RuntimeError(SECRET)

    fake_leader.on_request = explode
    now = utcnow()
    with caplog.at_level(logging.DEBUG):
        assert await poll(now) == {"eu-1": "error"}
    assert "polling leader eu-1 failed: RuntimeError" in caplog.text
    assert SECRET not in caplog.text
    leader = await _leader(sessionmaker)
    assert (leader.consecutive_failures, leader.last_error, leader.last_polled_at) == (
        1,
        "error",
        now,
    )
    (snapshot,) = await _snapshots(sessionmaker)
    assert (snapshot.reachable, snapshot.outcome, snapshot.status) == (False, "error", None)
    assert await poll(now + timedelta(seconds=5)) == {}  # waits for its normal turn
    assert len(fake_leader.requests) == 1
    assert await poll(now + timedelta(seconds=15)) == {"eu-1": "error"}
    assert (await _leader(sessionmaker)).consecutive_failures == 2


async def test_a_deeply_nested_answer_is_a_failure_not_a_crash(
    poll, factory, fake_leader, sessionmaker
):
    await factory.leader("eu-1")
    fake_leader.replies[("GET", "/v1/admin/status")] = (
        200,
        b"[" * 200_000,
        {"Content-Type": "application/json"},
    )
    now = utcnow()
    assert (await poll(now))["eu-1"] in {"bad_response", "error"}
    leader = await _leader(sessionmaker)
    assert (leader.consecutive_failures, leader.last_polled_at) == (1, now)
    (snapshot,) = await _snapshots(sessionmaker)
    assert snapshot.status is None


async def test_two_replicas_racing_on_a_revoked_leader_audit_it_once(
    poll, factory, fake_leader, sessionmaker
):
    await factory.leader("eu-1")
    fake_leader.modes[HOST] = "revoked"
    now = utcnow()
    await asyncio.gather(poll(now), poll(now))
    async with sessionmaker() as session:
        entries = (await session.scalars(select(AuditEntry))).all()
    assert [e.action for e in entries] == ["leader.credential_revoked"]
    assert (await _leader(sessionmaker)).credential_revoked_at == now


async def test_a_slow_batch_is_stamped_with_the_time_it_started(
    poll, factory, fake_leader, sessionmaker
):
    for name in ("eu-1", "us-1"):
        await factory.leader(name)
        fake_leader.modes[f"{name}.leaders.example"] = "slow"
    fake_leader.delay = 0.3
    now = utcnow()
    await poll(now, concurrency=1, timeout=1.0)
    stamps = sorted(s.taken_at for s in await _snapshots(sessionmaker))
    assert stamps[0] == now
    assert stamps[1] - now >= timedelta(seconds=0.25)
