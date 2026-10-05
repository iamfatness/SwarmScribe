"""The fleet console as the end-to-end tests meet it: the real console app (create_app) on
http://localhost:8900 serving the built web app, with an in-memory Entra ID and two
in-memory leaders in place of the network, its own Postgres database, and the real poller
(every 2 s, so "unreachable" arrives in seconds).

A separate control server on http://127.0.0.1:8901 lets the tests reset the world, sign a
persona in, take a leader down and end every session. It is a different port and a
different app: nothing here is part of the console package or the built web app.

Run from the repository root (Playwright's webServer does this):

    python -m uv run python packages/console-web/e2e/harness/serve.py

The database server is SWARMSCRIBE_TEST_DATABASE_URL (as for pytest) or, when unset, the
repository's pgserver in .pgdata."""

import asyncio
import logging
import math
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
WEB_ROOT = REPO_ROOT / "packages" / "console-web"
# console_testkit: the console tests' shared constants (keys, credential, Entra ids, groups).
sys.path.insert(0, str(REPO_ROOT / "packages" / "console" / "tests"))

import uvicorn  # noqa: E402
from console_testkit import (  # noqa: E402
    CREDENTIAL,
    ENTRA_CLIENT,
    ENTRA_SECRET,
    ENTRA_TENANT,
    GROUPS,
    TEST_KEY,
    recreate,
    with_database,
)
from fake_entra import FakeEntra, UnknownPersona  # noqa: E402
from fake_leaders import CAPS, FakeLeaders  # noqa: E402
from sqlalchemy import select, text  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402
from starlette.routing import Route  # noqa: E402
from swarmscribe_console import grants, leaders  # noqa: E402
from swarmscribe_console.app import create_app  # noqa: E402
from swarmscribe_console.config import Settings  # noqa: E402
from swarmscribe_console.db.migrate import upgrade  # noqa: E402
from swarmscribe_console.db.models import Base, Leader, Snapshot  # noqa: E402

E2E_DATABASE = "swarmscribe_console_e2e"
CONSOLE_PORT = 8900
CONTROL_PORT = 8901
PUBLIC_URL = f"http://localhost:{CONSOLE_PORT}"
LEADERS = {
    "eu-1": {"region": "eu", "env": "prod"},
    "us-1": {"region": "us", "env": "prod"},
}
KEEP_TABLES = {"alembic_version"}
logger = logging.getLogger("e2e-harness")


def admin_database_url() -> str:
    url = os.environ.get("SWARMSCRIBE_TEST_DATABASE_URL")
    if url:
        return url
    import pgserver

    return pgserver.get_server(str(REPO_ROOT / ".pgdata"), cleanup_mode="stop").get_uri()


class NoDirectory:
    """Group lookups are never needed: every persona's groups are in its token."""

    async def member_object_ids(self, user_object_id: str) -> set[str]:
        return set()

    async def group_emails(self, email: str) -> set[str]:
        return set()


class Harness:
    def __init__(self, database_url: str) -> None:
        self.entra = FakeEntra()
        self.fakes = FakeLeaders()
        settings = Settings(
            database_url=database_url,
            public_url=PUBLIC_URL,
            key=TEST_KEY,
            entra_tenant_id=ENTRA_TENANT,
            entra_client_id=ENTRA_CLIENT,
            entra_client_secret=ENTRA_SECRET,
            static_dir=WEB_ROOT / "dist",
            poll_interval_seconds=2.0,
            poll_timeout_seconds=1.0,
            poll_tick_seconds=0.5,
        )
        self.console = create_app(
            settings,
            fetch=self.entra.fetch,
            idp_transport=self.entra.transport,
            graph=NoDirectory(),
            google_groups=NoDirectory(),
            leader_transport=self.fakes.transport,
        )

    @property
    def sessionmaker(self):
        return self.console.state.sessionmaker

    async def reset(self) -> None:
        """Empty every console table, re-register the two leaders with grants and 24 hours
        of history, restore the fake leaders, and wait for both leaders' first poll."""
        self.fakes.reset()
        tables = [
            table.name
            for table in reversed(Base.metadata.sorted_tables)
            if table.name not in KEEP_TABLES
        ]
        async with self.console.state.engine.begin() as conn:
            await conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
        keys = self.console.state.keys
        async with self.sessionmaker() as session:
            for name, labels in LEADERS.items():
                await leaders.add_leader(
                    session,
                    keys,
                    name=name,
                    base_url=f"https://{name}.leaders.example",
                    labels=labels,
                    credential=CREDENTIAL,
                    enabled=True,
                    actor="e2e",
                )
            for role in ("viewer", "operator", "admin"):
                await grants.add_grant(
                    session,
                    role=role,
                    scope="all",
                    principal_kind="entra_group",
                    principal=GROUPS[role],
                    actor="e2e",
                )
            await grants.add_console_admin(
                session, principal_kind="entra_group", principal=GROUPS["console"], actor="e2e"
            )
            await session.commit()
        await self._seed_history()
        await self._wait_for_first_polls()

    async def _seed_history(self) -> None:
        now = datetime.now(UTC)
        async with self.sessionmaker() as session:
            for row in (await session.scalars(select(Leader))).all():
                status = self.fakes.leaders[row.name].status()
                offset = 0.0 if row.name == "eu-1" else 1.3
                for i in range(288, 0, -1):
                    taken = now - timedelta(minutes=5 * i)
                    down = row.name == "us-1" and 100 <= i < 104
                    # A smooth day with a busy spell, so the chart is drawn as designed.
                    swell = math.sin((288 - i) / 288 * math.tau * 1.4 + offset)
                    busy = 6 + 5 * swell + math.sin(i / 5)
                    point = dict(status, completed_last_hour=max(1, round(busy)))
                    session.add(
                        Snapshot(
                            leader_id=row.id,
                            taken_at=taken,
                            reachable=not down,
                            outcome="connect_error" if down else "ok",
                            status=None if down else point,
                        )
                    )
            await session.commit()

    async def _wait_for_first_polls(self) -> None:
        for _ in range(100):
            async with self.sessionmaker() as session:
                rows = (await session.scalars(select(Leader))).all()
            if rows and all(row.last_success_at is not None for row in rows):
                return
            await asyncio.sleep(0.1)
        raise RuntimeError("the leaders were not polled within 10 seconds")

    async def end_sessions(self) -> None:
        async with self.console.state.engine.begin() as conn:
            await conn.execute(text("DELETE FROM sessions"))


def control_app(harness: Harness) -> Starlette:
    async def reset(_request: Request) -> JSONResponse:
        await harness.reset()
        return JSONResponse({"ok": True})

    async def authorize(request: Request) -> JSONResponse:
        body = await request.json()
        try:
            callback = harness.entra.authorize(str(body["location"]), str(body["persona"]))
        except (KeyError, UnknownPersona):
            return JSONResponse({"error": "unknown persona or location"}, status_code=400)
        return JSONResponse({"callback": callback})

    async def leader_mode(request: Request) -> JSONResponse:
        name = request.path_params["name"]
        mode = (await request.json()).get("mode")
        if name not in CAPS or mode not in ("ok", "down"):
            return JSONResponse({"error": "unknown leader or mode"}, status_code=400)
        harness.fakes.modes[name] = mode
        return JSONResponse({"ok": True})

    async def expire_sessions(_request: Request) -> JSONResponse:
        await harness.end_sessions()
        return JSONResponse({"ok": True})

    return Starlette(
        routes=[
            Route("/control/reset", reset, methods=["POST"]),
            Route("/control/authorize", authorize, methods=["POST"]),
            Route("/control/leaders/{name}/mode", leader_mode, methods=["POST"]),
            Route("/control/sessions/expire", expire_sessions, methods=["POST"]),
        ]
    )


async def main() -> None:
    if not (WEB_ROOT / "dist" / "index.html").is_file():
        raise SystemExit("build the web app first: npm run build (in packages/console-web)")
    admin_url = admin_database_url()
    await recreate(admin_url, E2E_DATABASE)
    database_url = with_database(admin_url, E2E_DATABASE)
    await asyncio.to_thread(upgrade, database_url)
    harness = Harness(database_url)
    apps = ((harness.console, CONSOLE_PORT), (control_app(harness), CONTROL_PORT))
    servers = [
        uvicorn.Server(
            uvicorn.Config(
                app, host="127.0.0.1", port=port, access_log=False, log_level="warning"
            )
        )
        for app, port in apps
    ]
    serving = [asyncio.create_task(server.serve()) for server in servers]
    while not all(server.started for server in servers):
        await asyncio.sleep(0.05)
    await harness.reset()
    print(f"e2e harness ready: console {PUBLIC_URL}, control http://127.0.0.1:{CONTROL_PORT}")
    sys.stdout.flush()
    await asyncio.gather(*serving)


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())
