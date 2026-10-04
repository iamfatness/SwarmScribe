import copy
from datetime import datetime, timedelta

import pytest
from console_testkit import STATUS
from sqlalchemy import event, update
from swarmscribe_console.api.fleet import health_of, summary_of
from swarmscribe_console.db.models import Leader
from swarmscribe_leader.clock import utcnow

PERSON = {"email:person@example.org"}


@pytest.fixture
async def fleet(factory):
    return {
        "eu-1": await factory.leader("eu-1", labels={"env": "prod"}),
        "us-1": await factory.leader("us-1", labels={"env": "test"}),
        "ap-1": await factory.leader("ap-1"),
    }


async def _state(sessionmaker, name, **values):
    async with sessionmaker() as session:
        await session.execute(update(Leader).where(Leader.name == name).values(**values))
        await session.commit()


async def test_a_person_sees_only_leaders_they_hold_a_role_on(client, factory, fleet):
    await factory.grant("operator", "label:env=prod", "email", "person@example.org")
    await factory.grant("viewer", "leader:ap-1", "email", "person@example.org")
    await factory.person(client, principals=PERSON)
    body = (await client.get("/api/fleet")).json()
    assert [(row["name"], row["role"]) for row in body] == [
        ("ap-1", "viewer"),
        ("eu-1", "operator"),
    ]
    assert body[1]["labels"] == {"env": "prod"}


async def test_the_highest_matching_grant_is_the_role_shown(client, factory, fleet):
    await factory.grant("viewer", "all", "email", "person@example.org")
    await factory.grant("admin", "leader:eu-1", "email", "person@example.org")
    await factory.person(client, principals=PERSON)
    roles = {row["name"]: row["role"] for row in (await client.get("/api/fleet")).json()}
    assert roles == {"ap-1": "viewer", "eu-1": "admin", "us-1": "viewer"}


async def test_a_console_admin_without_grants_sees_no_leaders(client, factory, fleet):
    await factory.console_admin("email", "person@example.org")
    await factory.person(client, principals=PERSON)
    assert (await client.get("/api/fleet")).json() == []


async def test_the_fleet_needs_a_session(client):
    assert (await client.get("/api/fleet")).status_code == 401


async def test_health_follows_the_pollers_state(client, factory, fleet, sessionmaker):
    await factory.leader("xx-1", enabled=False)
    await factory.leader("rv-1")
    await factory.grant("viewer", "all", "email", "person@example.org")
    now = utcnow()
    await _state(sessionmaker, "eu-1", last_success_at=now, last_polled_at=now)
    await _state(
        sessionmaker, "us-1", consecutive_failures=3, last_error="timeout", last_polled_at=now
    )
    await _state(
        sessionmaker, "ap-1", consecutive_failures=2, last_error="timeout", last_polled_at=now
    )
    await _state(sessionmaker, "rv-1", credential_revoked_at=now, last_error="credential_revoked")
    await factory.person(client, principals=PERSON)
    rows = {row["name"]: row for row in (await client.get("/api/fleet")).json()}
    assert {name: row["health"] for name, row in rows.items()} == {
        "ap-1": "pending",
        "eu-1": "reachable",
        "rv-1": "credential_revoked",
        "us-1": "unreachable",
        "xx-1": "disabled",
    }
    assert (rows["us-1"]["last_error"], rows["us-1"]["consecutive_failures"]) == ("timeout", 3)


async def test_the_latest_successful_snapshot_is_shown(client, factory, fleet):
    await factory.grant("viewer", "all", "email", "person@example.org")
    now = utcnow()
    older = copy.deepcopy(STATUS) | {"completed_last_hour": 4}
    newest_ok = copy.deepcopy(STATUS) | {"completed_last_hour": 9}
    await factory.snapshot(fleet["eu-1"], taken_at=now - timedelta(seconds=90), status=older)
    await factory.snapshot(fleet["eu-1"], taken_at=now - timedelta(seconds=60), status=newest_ok)
    await factory.snapshot(
        fleet["eu-1"], taken_at=now - timedelta(seconds=10), reachable=False, outcome="timeout"
    )
    await factory.person(client, principals=PERSON)
    rows = {row["name"]: row for row in (await client.get("/api/fleet")).json()}
    snapshot = rows["eu-1"]["snapshot"]
    assert datetime.fromisoformat(snapshot["taken_at"]) == now - timedelta(seconds=60)
    assert snapshot["status"]["completed_last_hour"] == 9
    assert rows["eu-1"]["summary"]["completed_last_hour"] == 9
    assert rows["us-1"]["snapshot"] is None
    assert rows["us-1"]["summary"] is None


async def test_the_overview_row_comes_from_the_status_alone(client, factory, fleet):
    await factory.grant("viewer", "leader:eu-1", "email", "person@example.org")
    status = copy.deepcopy(STATUS)
    status["follower_pools"] = [
        {"pool": "default", "active": 2, "draining": 1, "revoked": 0, "gone": 0},
        {"pool": "gpu", "active": 0, "draining": 0, "revoked": 0, "gone": 3},
    ]
    status["locations"] = [
        {"name": "talks", "last_scan_error": None},
        {"name": "archive", "last_scan_error": "input folder 'x' is not available"},
    ]
    await factory.snapshot(fleet["eu-1"], taken_at=utcnow(), status=status)
    await factory.person(client, principals=PERSON)
    (row,) = (await client.get("/api/fleet")).json()
    assert row["summary"] == {
        "queued": 3,
        "completed_last_hour": 7,
        "completed_last_day": 30,
        "failed_attempts_last_day": 1,
        "oldest_queued_age_s": 420,
        "followers_active_by_pool": {"default": 2, "gpu": 0},
        "scan_errors": [
            {"location": "archive", "error": "input folder 'x' is not available"}
        ],
    }


async def test_nothing_queued_shows_no_queue_age(client, factory, fleet):
    await factory.grant("viewer", "leader:eu-1", "email", "person@example.org")
    status = copy.deepcopy(STATUS)
    status["jobs"]["queued"] = 0
    status["oldest_queued_age_s"] = None
    await factory.snapshot(fleet["eu-1"], taken_at=utcnow(), status=status)
    await factory.person(client, principals=PERSON)
    (row,) = (await client.get("/api/fleet")).json()
    assert (row["summary"]["queued"], row["summary"]["oldest_queued_age_s"]) == (0, None)


async def test_history_is_bucketed_by_five_minutes(client, factory, fleet):
    await factory.grant("viewer", "leader:eu-1", "email", "person@example.org")
    now = utcnow()
    base = now.replace(second=0, microsecond=0) - timedelta(minutes=now.minute % 5 + 30)

    def status(queued):
        body = copy.deepcopy(STATUS)
        body["jobs"]["queued"] = queued
        return body

    eu = fleet["eu-1"]
    await factory.snapshot(eu, taken_at=now - timedelta(hours=30), status=status(99))
    await factory.snapshot(eu, taken_at=base + timedelta(minutes=1), status=status(1))
    await factory.snapshot(eu, taken_at=base + timedelta(minutes=4), status=status(2))
    await factory.snapshot(
        eu, taken_at=base + timedelta(minutes=6), reachable=False, outcome="timeout"
    )
    await factory.snapshot(eu, taken_at=base + timedelta(minutes=12), status=status(5))
    await factory.person(client, principals=PERSON)
    answer = await client.get("/api/leaders/eu-1/history")
    assert answer.status_code == 200
    points = [
        (datetime.fromisoformat(p["at"]) - base, p["reachable"], p["queued"])
        for p in answer.json()
    ]
    assert points == [
        (timedelta(0), True, 2),
        (timedelta(minutes=5), False, None),
        (timedelta(minutes=10), True, 5),
    ]
    first = answer.json()[0]
    assert (first["completed_last_hour"], first["followers_active"]) == (7, 2)
    assert (first["completed_last_day"], first["oldest_queued_age_s"]) == (30, 420)
    assert answer.json()[1]["oldest_queued_age_s"] is None  # the failed poll's bucket
    short = await client.get("/api/leaders/eu-1/history", params={"hours": 1})
    assert len(short.json()) == 3


@pytest.mark.parametrize("hours", ["0", "25", "x"])
async def test_history_hours_are_bounded(client, factory, fleet, hours):
    await factory.grant("viewer", "all", "email", "person@example.org")
    await factory.person(client, principals=PERSON)
    answer = await client.get("/api/leaders/eu-1/history", params={"hours": hours})
    assert answer.status_code == 422


async def test_a_leader_without_a_grant_is_answered_like_an_unknown_one(client, factory, fleet):
    await factory.grant("viewer", "leader:eu-1", "email", "person@example.org")
    await factory.person(client, principals=PERSON)
    hidden = await client.get("/api/leaders/us-1/history")
    unknown = await client.get("/api/leaders/zz-9/history")
    assert hidden.status_code == unknown.status_code == 404
    assert hidden.json() == unknown.json() == {
        "code": "leader_not_found",
        "message": "no leader with that name",
    }


@pytest.mark.parametrize("name", ["..", "a%2Fb", "eu-1%2F..%2Fus-1"])
async def test_odd_leader_names_in_the_history_path_are_not_found(client, factory, fleet, name):
    await factory.grant("viewer", "all", "email", "person@example.org")
    await factory.person(client, principals=PERSON)
    assert (await client.get(f"/api/leaders/{name}/history")).status_code == 404


async def test_a_leader_never_polled_is_pending_with_no_summary(client, factory, fleet):
    await factory.grant("viewer", "leader:eu-1", "email", "person@example.org")
    await factory.person(client, principals=PERSON)
    (row,) = (await client.get("/api/fleet")).json()
    assert row["health"] == "pending"
    assert (row["summary"], row["snapshot"], row["last_polled_at"]) == (None, None, None)


async def test_a_rotated_leader_is_pending_even_with_an_old_success(
    client, factory, fleet, sessionmaker
):
    await factory.grant("viewer", "leader:eu-1", "email", "person@example.org")
    # The state after rotation or a URL change: the poller's state is cleared, but an earlier
    # success may still be on the row (R5).
    await _state(sessionmaker, "eu-1", last_success_at=utcnow(), last_polled_at=None)
    await factory.person(client, principals=PERSON)
    (row,) = (await client.get("/api/fleet")).json()
    assert row["health"] == "pending"


def _leader(**values) -> Leader:
    base = {"enabled": True, "credential_revoked_at": None, "consecutive_failures": 0}
    base.update(last_polled_at=None, last_success_at=None)
    return Leader(**(base | values))


def test_health_of_rules_in_order():
    now = utcnow()
    assert health_of(_leader(last_success_at=now), 3) == "pending"
    assert health_of(_leader(last_polled_at=now), 3) == "pending"
    assert health_of(_leader(last_polled_at=now, last_success_at=now), 3) == "reachable"
    failing = _leader(last_polled_at=now, consecutive_failures=3)
    assert health_of(failing, 3) == "unreachable"
    assert health_of(_leader(last_polled_at=now, consecutive_failures=2), 3) == "pending"
    revoked = _leader(last_polled_at=now, consecutive_failures=3, credential_revoked_at=now)
    assert health_of(revoked, 3) == "credential_revoked"
    assert health_of(_leader(enabled=False, credential_revoked_at=now), 3) == "disabled"


@pytest.mark.parametrize(
    "status",
    [{}, {"jobs": {"queued": 2}}, {"jobs": None, "follower_pools": None, "locations": None}],
)
def test_a_status_stored_before_c1b_does_not_crash_the_summary(status):
    summary = summary_of(status)
    assert summary["oldest_queued_age_s"] is None
    assert summary["followers_active_by_pool"] == {}
    assert summary["scan_errors"] == []


async def test_a_pre_c1b_snapshot_still_lists(client, factory, fleet):
    await factory.grant("viewer", "leader:eu-1", "email", "person@example.org")
    old = {"jobs": {"queued": 2, "leased": 0}, "followers": {"active": 1}}
    await factory.snapshot(fleet["eu-1"], taken_at=utcnow(), status=old)
    await factory.person(client, principals=PERSON)
    answer = await client.get("/api/fleet")
    assert answer.status_code == 200
    assert answer.json()[0]["summary"]["queued"] == 2
    history = await client.get("/api/leaders/eu-1/history")
    assert history.status_code == 200
    assert history.json()[0]["oldest_queued_age_s"] is None


async def test_history_for_a_leader_never_polled_is_empty(client, factory, fleet):
    await factory.grant("viewer", "all", "email", "person@example.org")
    await factory.person(client, principals=PERSON)
    answer = await client.get("/api/leaders/eu-1/history")
    assert (answer.status_code, answer.json()) == (200, [])


async def test_the_fleet_query_count_does_not_grow_with_the_leaders(
    client, factory, fleet, app, sessionmaker
):
    await factory.grant("viewer", "all", "email", "person@example.org")
    await factory.person(client, principals=PERSON)
    statements: list[str] = []

    def count(_conn, _cursor, statement, *_rest):
        statements.append(statement)

    engine = app.state.engine.sync_engine
    event.listen(engine, "before_cursor_execute", count)
    try:
        await client.get("/api/fleet")
        small = len(statements)
        for number in range(50):
            leader = await factory.leader(f"bulk-{number}")
            await factory.snapshot(leader, taken_at=utcnow())
        statements.clear()
        answer = await client.get("/api/fleet")
        large = len(statements)
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert len(answer.json()) == 53
    assert large == small


async def test_the_first_history_bucket_holds_only_snapshots_inside_the_window(
    client, factory, fleet
):
    await factory.grant("viewer", "leader:eu-1", "email", "person@example.org")
    now = utcnow()
    since = now - timedelta(hours=1)

    def status(queued):
        body = copy.deepcopy(STATUS)
        body["jobs"]["queued"] = queued
        return body

    eu = fleet["eu-1"]
    # Just before the window and just inside it: often the same 5-minute bucket, which then
    # starts before the window but is built from the inside snapshot only.
    await factory.snapshot(eu, taken_at=since - timedelta(seconds=30), status=status(99))
    await factory.snapshot(eu, taken_at=since + timedelta(seconds=30), status=status(3))
    await factory.person(client, principals=PERSON)
    answer = await client.get("/api/leaders/eu-1/history", params={"hours": 1})
    points = answer.json()
    assert [p["queued"] for p in points] == [3]
    first = datetime.fromisoformat(points[0]["at"])
    assert since - timedelta(minutes=5) < first <= since + timedelta(seconds=30)


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_unsafe_methods_on_the_fleet_are_405(client, factory, fleet, method):
    await factory.grant("viewer", "all", "email", "person@example.org")
    csrf = await factory.person(client, principals=PERSON)
    answer = await client.request(method, "/api/fleet", headers={"X-CSRF-Token": csrf})
    assert answer.status_code == 405


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_history_is_read_only_and_never_reaches_a_leader(
    client, factory, fleet, fake_leader, method
):
    await factory.grant("admin", "all", "email", "person@example.org")
    csrf = await factory.person(client, principals=PERSON)
    answer = await client.request(
        method, "/api/leaders/eu-1/history", headers={"X-CSRF-Token": csrf}
    )
    assert answer.status_code in (404, 405)
    assert fake_leader.requests == []


@pytest.mark.parametrize(
    "status",
    [
        {"jobs": [1, 2], "completed_last_hour": None, "follower_pools": [1, None, "x"]},
        {"jobs": "queued", "oldest_queued_age_s": "soon", "locations": {"a": 1}},
        {"jobs": {"queued": None}, "failed_attempts_last_day": "3", "locations": [None, 4]},
        {"jobs": {"queued": True}, "follower_pools": [{"pool": "p", "active": None}]},
        None,
        [],
    ],
)
async def test_one_oddly_shaped_row_never_breaks_the_summary(status):
    summary = summary_of(status)
    assert summary["queued"] == 0
    assert summary["completed_last_hour"] == summary["completed_last_day"] == 0
    assert summary["oldest_queued_age_s"] is None
    assert summary["scan_errors"] == []
