"""The bound on pending sign-ins (SWARMSCRIBE_CONSOLE_LOGIN_ATTEMPTS_MAX).

/auth/login needs no session and stores a row every time, so the table is capped: beyond
the cap the oldest pending sign-ins are dropped and the newest survive."""

import asyncio
import logging
from datetime import timedelta

import httpx
import pytest
from console_testkit import GROUPS, PUBLIC_URL, all_rows_text
from pydantic import ValidationError
from sqlalchemy import func, select, text
from swarmscribe_console import oidc
from swarmscribe_console.app import create_app
from swarmscribe_console.db.models import LoginAttempt
from swarmscribe_console.oidc import trim_pending_sign_ins
from swarmscribe_leader.clock import utcnow

CAP = 3


@pytest.fixture
async def capped(engine, make_settings, idp, graph, google_groups, fake_leader):
    application = create_app(
        make_settings(login_attempts_max=CAP),
        background=False,
        fetch=idp.fetch,
        idp_transport=idp.transport,
        graph=graph,
        google_groups=google_groups,
        leader_transport=fake_leader.transport,
    )
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def browsers(capped):
    made = [
        httpx.AsyncClient(transport=httpx.ASGITransport(app=capped), base_url=PUBLIC_URL)
        for _ in range(CAP + 2)
    ]
    yield made
    for browser in made:
        await browser.aclose()


@pytest.fixture(autouse=True)
def warn_again(monkeypatch):
    # The warning is limited to one a minute by a module-level time; start each test fresh.
    monkeypatch.setattr(oidc, "_last_trim_warning", float("-inf"))


async def pending(sessionmaker) -> int:
    async with sessionmaker() as session:
        return await session.scalar(select(func.count()).select_from(LoginAttempt))


async def start(browser: httpx.AsyncClient) -> str:
    answer = await browser.get("/auth/login", params={"provider": "entra"})
    assert answer.status_code == 302
    return answer.headers["location"]


async def test_pending_sign_ins_are_capped_and_the_newest_survive(
    browsers, idp, factory, sessionmaker
):
    await factory.grant("operator", "all", "entra_group", GROUPS["operator"])
    at_provider = [await start(browser) for browser in browsers]
    assert await pending(sessionmaker) == CAP

    dropped = await browsers[0].get(
        idp.authorize(at_provider[0], groups=[GROUPS["operator"]])
    )
    assert dropped.status_code == 400
    assert "sign in again" in dropped.text

    newest = await browsers[-1].get(
        idp.authorize(at_provider[-1], groups=[GROUPS["operator"]])
    )
    assert newest.status_code == 200
    assert (await browsers[-1].get("/api/session")).status_code == 200


async def test_under_the_cap_nothing_is_dropped(browsers, sessionmaker, caplog):
    with caplog.at_level(logging.WARNING, logger="swarmscribe_console.oidc"):
        for browser in browsers[:CAP]:
            await start(browser)
    assert await pending(sessionmaker) == CAP
    assert caplog.records == []


async def test_a_flood_is_logged_once_and_without_secrets(browsers, engine, caplog):
    with caplog.at_level(logging.WARNING, logger="swarmscribe_console.oidc"):
        at_provider = [await start(browser) for browser in browsers]
    (warning,) = caplog.records
    assert "sign-ins are pending" in warning.getMessage()
    assert "/auth/login" in warning.getMessage()
    for location in at_provider:
        state = dict(httpx.URL(location).params)["state"]
        assert state not in caplog.text
        assert state not in await all_rows_text(engine)  # only its hash is ever stored


async def test_trimming_keeps_the_newest(sessionmaker):
    now = utcnow()
    async with sessionmaker() as session:
        for age in range(4):
            session.add(
                LoginAttempt(
                    state_hash=f"{age}" * 64,
                    browser_hash="b" * 64,
                    provider="entra",
                    nonce="n",
                    code_verifier="v",
                    return_to="/",
                    expires_at=now + timedelta(minutes=10 - age),  # 0 is the newest
                )
            )
        await session.flush()
        assert await trim_pending_sign_ins(session, keep=2) == 2
        assert await trim_pending_sign_ins(session, keep=2) == 0
        await session.commit()
    async with sessionmaker() as session:
        left = (await session.scalars(select(LoginAttempt.state_hash))).all()
    assert sorted(left) == ["0" * 64, "1" * 64]


@pytest.mark.parametrize("bad", [0, -1])
def test_the_cap_is_at_least_one(make_settings, bad):
    with pytest.raises(ValidationError):
        make_settings(login_attempts_max=bad)


def test_the_default_cap(make_settings):
    assert make_settings().login_attempts_max == 10_000


async def add_rows(session, count: int):
    now = utcnow()
    for age in range(count):
        session.add(
            LoginAttempt(
                state_hash=f"{age:064d}",
                browser_hash="b" * 64,
                provider="entra",
                nonce="n",
                code_verifier="v",
                return_to="/",
                expires_at=now + timedelta(minutes=100 - age),  # 0 is the newest
            )
        )
    await session.flush()


async def test_under_the_cap_deletes_nothing_and_locks_nothing(sessionmaker):
    async with sessionmaker() as trimming, sessionmaker() as other:
        await add_rows(trimming, 5)
        await trimming.commit()
        assert await trim_pending_sign_ins(trimming, keep=5) == 0
        assert await trim_pending_sign_ins(trimming, keep=50) == 0
        # Still inside the trimming transaction: a second session can lock every row at once.
        locked = await other.execute(
            text("SELECT state_hash FROM login_attempts FOR UPDATE NOWAIT")
        )
        assert len(locked.all()) == 5
        await other.rollback()
        await trimming.rollback()


async def test_over_the_cap_trims_to_exactly_the_cap(sessionmaker):
    async with sessionmaker() as session:
        await add_rows(session, 12)
        assert await trim_pending_sign_ins(session, keep=5) == 7
        await session.commit()
    async with sessionmaker() as session:
        left = (await session.scalars(select(LoginAttempt.state_hash))).all()
    assert sorted(left) == [f"{age:064d}" for age in range(5)]


async def test_concurrent_trims_each_make_progress(sessionmaker):
    async with sessionmaker() as setup:
        await add_rows(setup, 12)
        await setup.commit()
    async with sessionmaker() as first, sessionmaker() as second:
        dropped_first = await trim_pending_sign_ins(first, keep=10)  # holds 2 locks, open
        dropped_second = await asyncio.wait_for(
            trim_pending_sign_ins(second, keep=4), timeout=10  # must not wait for them
        )
        assert dropped_first == 2
        assert dropped_second == 6  # its victims, minus the two the first one holds
        await first.commit()
        await second.commit()
    async with sessionmaker() as session:
        assert await pending(sessionmaker) == 4
        left = (await session.scalars(select(LoginAttempt.state_hash))).all()
    assert sorted(left) == [f"{age:064d}" for age in range(4)]


async def test_the_newest_row_is_never_dropped(sessionmaker):
    async with sessionmaker() as session:
        await add_rows(session, 3)
        assert await trim_pending_sign_ins(session, keep=1) == 2
        left = (await session.scalars(select(LoginAttempt.state_hash))).all()
    assert left == [f"{0:064d}"]
