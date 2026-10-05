import subprocess
import sys
from pathlib import Path

import httpx
from leader_testkit import LINK_KEY, add_recordings, empty_tables, new_join_token
from sqlalchemy import select
from swarmscribe_leader.app import create_app
from swarmscribe_leader.config import Settings
from swarmscribe_leader.db.models import Job, Recording, SettingsProfile


async def test_the_kit_queues_consented_recordings_a_follower_can_claim_and_fetch(
    sessionmaker, migrated_database_url, tmp_path
):
    await add_recordings(
        sessionmaker,
        tmp_path / "archive",
        {"talks/one.wav": b"one", "private/two.wav": b"two"},
        consent="talks/*\n",
        channel_mode="auto",
    )
    token = await new_join_token(sessionmaker)
    settings = Settings(
        database_url=migrated_database_url, public_url="http://leader", link_key=LINK_KEY
    )
    app = create_app(settings, background=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://leader"
        ) as client:
            capabilities = {
                "device": "cpu",
                "models": [],
                "engine_version": "0.1.0",
                "pool": "default",
            }
            joined = await client.post(
                "/v1/followers/register",
                json={"join_token": token, "protocol_version": 1, "capabilities": capabilities},
            )
            headers = {"Authorization": f"Bearer {joined.json()['credential']}"}
            claimed = (await client.post("/v1/jobs/claim", headers=headers)).json()
            assert claimed["settings"]["channel_mode"] == "auto"
            assert (await client.get(claimed["download_url"]["url"])).content == b"one"
            assert (await client.post("/v1/jobs/claim", headers=headers)).status_code == 204
    async with sessionmaker() as session:
        assert len((await session.scalars(select(Recording))).all()) == 2
        assert len((await session.scalars(select(Job))).all()) == 1


async def test_emptying_the_tables_keeps_the_seeded_profiles(sessionmaker, tmp_path):
    await add_recordings(sessionmaker, tmp_path / "archive", {"a.wav": b"a"})
    async with sessionmaker() as session:
        await empty_tables(session)
    async with sessionmaker() as session:
        assert (await session.scalars(select(Job))).all() == []
        assert len((await session.scalars(select(SettingsProfile))).all()) == 2


async def test_another_package_can_start_a_real_leader_from_the_kit_alone(tmp_path):
    """What the follower's tests do: put this folder on sys.path, import the kit, build a
    database of their own, and serve the real application in-process. Run in a fresh
    interpreter so nothing of this package's conftest is present."""
    script = tmp_path / "other_package.py"
    script.write_text(
        f"""
import asyncio, sys
from pathlib import Path
sys.path.insert(0, {str(Path(__file__).parent)!r})
import httpx
from leader_testkit import LINK_KEY, add_recordings, migrated_database, new_join_token
from swarmscribe_leader.app import create_app
from swarmscribe_leader.config import Settings
from swarmscribe_leader.db.session import make_engine, make_sessionmaker


async def main():
    url = await migrated_database("swarmscribe_testkit_other")
    engine = make_engine(url)
    sm = make_sessionmaker(engine)
    root = Path({str(tmp_path / "rec")!r})
    await add_recordings(sm, root, {{"a.wav": b"a"}})
    token = await new_join_token(sm)
    await engine.dispose()
    settings = Settings(database_url=url, public_url="http://leader", link_key=LINK_KEY)
    app = create_app(settings, background=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://leader"
        ) as client:
            caps = {{"device": "cpu", "models": [], "engine_version": "0.1.0", "pool": "default"}}
            r = await client.post(
                "/v1/followers/register",
                json={{"join_token": token, "protocol_version": 1, "capabilities": caps}},
            )
            h = {{"Authorization": "Bearer " + r.json()["credential"]}}
            c = await client.post("/v1/jobs/claim", headers=h)
            assert c.status_code == 200, c.status_code
    print("ok")


asyncio.run(main())
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip() == "ok"
