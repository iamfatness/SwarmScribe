"""/healthz and /readyz as a Kubernetes rollout needs them (leader chart spec, section 6)."""

import asyncio
import logging
import time

import httpx
import pytest
from sqlalchemy import text
from swarmscribe_leader.api import health
from swarmscribe_leader.app import create_app
from swarmscribe_leader.config import Settings
from swarmscribe_leader.db.migrate import head_revision

LINK_KEY = "k" * 32


@pytest.fixture
async def app(engine, migrated_database_url):
    settings = Settings(
        database_url=migrated_database_url, public_url="http://leader", link_key=LINK_KEY
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


async def test_readyz_stays_ready_when_the_database_is_ahead(client, engine):
    """A rolling upgrade: the migration has run and this older replica is still serving. It
    must stay in service until it is replaced, or every old replica drops out at once."""
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE alembic_version SET version_num = 'newer_than_this'"))
    try:
        answer = await client.get("/readyz")
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE alembic_version SET version_num = :head"),
                {"head": head_revision()},
            )
    assert (answer.status_code, answer.json()) == (200, {"status": "ready"})


async def test_readyz_is_not_ready_when_the_schema_is_behind(app, client):
    # What a leader newer than the database sees: its head is not the database's revision,
    # and the database's revision is one it knows.
    app.state.head_revision = "9999_not_applied_yet"
    answer = await client.get("/readyz")
    assert (answer.status_code, answer.json()) == (
        503,
        {"status": "database migrations are not current"},
    )


async def test_readyz_is_not_ready_before_the_first_migration(client, engine):
    async with engine.begin() as conn:
        await conn.execute(text("ALTER TABLE alembic_version RENAME TO alembic_version_away"))
    try:
        answer = await client.get("/readyz")
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                text("ALTER TABLE alembic_version_away RENAME TO alembic_version")
            )
    assert (answer.status_code, answer.json()) == (
        503,
        {"status": "database migrations are not current"},
    )


async def test_readyz_says_when_the_database_cannot_be_reached(caplog, monkeypatch):
    monkeypatch.setattr(health, "READY_CACHE_SECONDS", 0.0)  # every call checks again
    settings = Settings(
        database_url="postgresql://user:hunter2-db@127.0.0.1:1/none",
        public_url="http://leader",
        link_key=LINK_KEY,
    )
    application = create_app(settings, background=False)
    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://leader"
        ) as made:
            with caplog.at_level(logging.WARNING):
                ready = await made.get("/readyz")
                again = await made.get("/readyz")
            alive = await made.get("/healthz")
    assert (ready.status_code, ready.json()) == (503, {"status": "database unreachable"})
    assert again.status_code == 503
    assert (alive.status_code, alive.json()) == (200, {"status": "ok"})
    # Neither the answer nor the log names the database or its password.
    assert "hunter2-db" not in ready.text + caplog.text
    assert "127.0.0.1" not in ready.text + caplog.text
    lines = [r for r in caplog.records if "readiness check" in r.getMessage()]
    assert len(lines) == 1, "a failing check is logged once per 30 s, not on every probe"


async def test_readyz_does_not_ask_the_identity_provider(admin_client, idp):
    """An identity provider's outage must not take a replica away from the followers. The
    admin API still answers 503 for as long as a sign-in cannot be checked."""
    idp.down = True
    ready = await admin_client.get("/readyz")
    assert (ready.status_code, ready.json()) == (200, {"status": "ready"})
    admin = await admin_client.get("/v1/admin/whoami", headers=idp.bearer("viewer"))
    assert (admin.status_code, admin.json()["code"]) == (503, "unavailable")


async def test_concurrent_readyz_calls_share_one_database_check(client, monkeypatch):
    calls = 0
    real = health._database_revision

    async def counted(engine):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return await real(engine)

    monkeypatch.setattr(health, "_database_revision", counted)
    answers = await asyncio.gather(*(client.get("/readyz") for _ in range(50)))
    assert {answer.status_code for answer in answers} == {200}
    assert calls == 1, "50 anonymous callers ran more than one database check"


async def test_readyz_asks_again_after_the_answer_has_aged(client, monkeypatch):
    calls = 0
    real = health._database_revision

    async def counted(engine):
        nonlocal calls
        calls += 1
        return await real(engine)

    monkeypatch.setattr(health, "_database_revision", counted)
    monkeypatch.setattr(health, "READY_CACHE_SECONDS", 0.0)
    assert (await client.get("/readyz")).status_code == 200
    assert (await client.get("/readyz")).status_code == 200
    assert calls == 2


async def test_readyz_answers_503_on_time_when_the_database_does_not_answer(client, monkeypatch):
    released = asyncio.Event()

    async def frozen(_engine):
        await released.wait()
        return head_revision()

    monkeypatch.setattr(health, "_database_revision", frozen)
    monkeypatch.setattr(health, "READY_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(health, "READY_CACHE_SECONDS", 0.0)
    started = time.monotonic()
    answer = await client.get("/readyz")
    assert (answer.status_code, answer.json()) == (503, {"status": "database unreachable"})
    assert time.monotonic() - started < 2.0
    released.set()


@pytest.mark.parametrize("path", ["/healthz", "/readyz"])
async def test_probe_answers_are_never_cached(client, path):
    assert (await client.get(path)).headers["cache-control"] == "no-store"


def test_serve_still_refuses_to_start_on_a_database_that_is_ahead(monkeypatch, capsys):
    """Staying Ready is for a replica that is already serving. A new one never starts on a
    schema it does not know: an image rolled back after a migration must not come up."""
    from swarmscribe_leader import main as entry

    async def ahead(_engine):
        return "newer_than_this"

    monkeypatch.setenv("SWARMSCRIBE_DATABASE_URL", "postgresql://u:p@127.0.0.1:1/none")
    monkeypatch.setenv("SWARMSCRIBE_PUBLIC_URL", "http://leader")
    monkeypatch.setenv("SWARMSCRIBE_LINK_KEY", LINK_KEY)
    monkeypatch.setattr(entry, "current_revision", ahead)
    assert entry.main(["serve"]) == 2
    assert "database is ahead of this leader" in capsys.readouterr().err
