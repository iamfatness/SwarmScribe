import asyncio

import httpx
import pytest
from console_testkit import PUBLIC_URL
from sqlalchemy import text
from swarmscribe_console.api import health as health_module
from swarmscribe_console.app import create_app
from swarmscribe_console.db.migrate import head_revision


async def test_healthz_answers_without_a_session(client):
    answer = await client.get("/healthz")
    assert answer.status_code == 200
    assert answer.json() == {"status": "ok"}
    assert answer.headers["cache-control"] == "no-store"
    assert "script-src 'self'" in answer.headers["content-security-policy"]


async def test_readyz_is_ready_on_a_migrated_database(client):
    answer = await client.get("/readyz")
    assert (answer.status_code, answer.json()) == (200, {"status": "ready"})
    assert answer.headers["cache-control"] == "no-store"
    assert "script-src 'self'" in answer.headers["content-security-policy"]


async def test_readyz_says_when_the_database_cannot_be_reached(
    make_settings, idp, fake_leader
):
    application = create_app(
        make_settings(database_url="postgresql://console:secret-pw@127.0.0.1:1/none"),
        background=False,
        fetch=idp.fetch,
        idp_transport=idp.transport,
        leader_transport=fake_leader.transport,
    )
    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url=PUBLIC_URL
        ) as made:
            ready = await made.get("/readyz")
            alive = await made.get("/healthz")
    assert (ready.status_code, ready.json()) == (503, {"status": "database unreachable"})
    assert "secret-pw" not in ready.text
    assert "127.0.0.1" not in ready.text
    # Liveness is about the process: a database outage must not get the pod restarted.
    assert alive.status_code == 200
    assert alive.json() == {"status": "ok"}


async def test_readyz_is_not_ready_when_the_schema_is_behind(app, client):
    # What a console newer than the database sees: its head is not the database's revision.
    app.state.head_revision = "9999_not_applied_yet"
    answer = await client.get("/readyz")
    assert answer.status_code == 503
    assert answer.json() == {"status": "database migrations are not current"}


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
    assert answer.status_code == 503
    assert answer.json() == {"status": "database migrations are not current"}


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


async def test_concurrent_readyz_calls_share_one_database_check(client, monkeypatch):
    checks = 0
    real = health_module.current_revision

    async def counting(engine):
        nonlocal checks
        checks += 1
        await asyncio.sleep(0.2)  # long enough that all 50 callers overlap the check
        return await real(engine)

    monkeypatch.setattr(health_module, "current_revision", counting)
    answers = await asyncio.gather(*(client.get("/readyz") for _ in range(50)))
    assert {a.status_code for a in answers} == {200}
    assert checks == 1
    # Within the cache window a further call still does not reach the database.
    assert (await client.get("/readyz")).status_code == 200
    assert checks == 1


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


async def test_the_readiness_answer_expires_after_a_second(client, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(health_module.time, "monotonic", clock)
    checks = 0
    real = health_module.current_revision

    async def counting(engine):
        nonlocal checks
        checks += 1
        return await real(engine)

    monkeypatch.setattr(health_module, "current_revision", counting)
    assert (await client.get("/readyz")).status_code == 200
    clock.now += 0.9
    assert (await client.get("/readyz")).status_code == 200
    assert checks == 1
    clock.now += 0.2  # 1.1 s since the check
    assert (await client.get("/readyz")).status_code == 200
    assert checks == 2


async def test_a_failed_check_is_reused_for_a_second_and_then_retried(client, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(health_module.time, "monotonic", clock)
    real = health_module.current_revision
    checks = 0
    broken = True

    async def flaky(engine):
        nonlocal checks
        checks += 1
        if broken:
            raise ConnectionError("database is down")
        return await real(engine)

    monkeypatch.setattr(health_module, "current_revision", flaky)
    assert (await client.get("/readyz")).status_code == 503
    broken = False  # recovered, but the failure is still cached
    clock.now += 0.5
    assert (await client.get("/readyz")).status_code == 503
    assert checks == 1
    clock.now += 0.6  # past one second: retried
    assert (await client.get("/readyz")).status_code == 200
    assert checks == 2


async def test_healthz_never_touches_the_database(client, monkeypatch):
    async def boom(engine):
        raise AssertionError("healthz must not query the database")

    monkeypatch.setattr(health_module, "current_revision", boom)
    assert (await client.get("/healthz")).status_code == 200


@pytest.mark.parametrize("path", ["/healthz", "/readyz"])
async def test_the_web_app_does_not_shadow_the_probes(
    path, tmp_path, engine, make_settings, idp, fake_leader
):
    (tmp_path / "index.html").write_text("<!doctype html><title>console</title>")
    application = create_app(
        make_settings(static_dir=str(tmp_path)),
        background=False,
        fetch=idp.fetch,
        idp_transport=idp.transport,
        leader_transport=fake_leader.transport,
    )
    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url=PUBLIC_URL
        ) as made:
            answer = await made.get(path)
    assert answer.status_code == 200
    assert answer.headers["content-type"].startswith("application/json")


@pytest.mark.parametrize("path", ["/healthz", "/readyz"])
async def test_a_probe_cannot_be_posted_to(client, path):
    assert (await client.post(path)).status_code == 405
