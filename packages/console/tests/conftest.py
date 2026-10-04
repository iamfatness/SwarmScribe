import asyncio
import os
import sys
from pathlib import Path

# console_testkit (shared constants and helpers) sits beside this file.
sys.path.insert(0, str(Path(__file__).parent))

from datetime import timedelta  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
from console_testkit import (  # noqa: E402
    ENTRA_CLIENT,
    ENTRA_ISSUER,
    ENTRA_SECRET,
    ENTRA_TENANT,
    GOOGLE_CLIENT,
    GOOGLE_SECRET,
    MASTER_KEY,
    PUBLIC_URL,
    TEST_KEY,
    recreate,
    with_database,
)
from sqlalchemy import text  # noqa: E402
from swarmscribe_console.app import create_app  # noqa: E402
from swarmscribe_console.config import Settings  # noqa: E402
from swarmscribe_console.crypto import ConsoleKeys  # noqa: E402
from swarmscribe_console.db.migrate import upgrade  # noqa: E402
from swarmscribe_console.db.models import (  # noqa: E402
    Base,
    ConsoleAdmin,
    RoleGrant,
)
from swarmscribe_console.sessions import SESSION_COOKIE, create_session  # noqa: E402
from swarmscribe_leader.clock import utcnow  # noqa: E402
from swarmscribe_leader.db.session import make_engine, make_sessionmaker  # noqa: E402

TEST_DATABASE = "swarmscribe_console_test"
REPO_ROOT = Path(__file__).resolve().parents[3]
KEEP_TABLES = {"alembic_version"}


def _admin_url() -> str:
    url = os.environ.get("SWARMSCRIBE_TEST_DATABASE_URL")
    if url:
        return url
    import pgserver

    server = pgserver.get_server(str(REPO_ROOT / ".pgdata"), cleanup_mode="stop")
    return server.get_uri()


@pytest.fixture(scope="session")
def admin_database_url() -> str:
    return _admin_url()


@pytest.fixture(scope="session")
def database_url(admin_database_url) -> str:
    asyncio.run(recreate(admin_database_url, TEST_DATABASE))
    return with_database(admin_database_url, TEST_DATABASE)


@pytest.fixture(scope="session")
def migrated_database_url(database_url) -> str:
    upgrade(database_url)
    return database_url


@pytest.fixture
async def engine(migrated_database_url):
    engine = make_engine(migrated_database_url)
    tables = [t.name for t in reversed(Base.metadata.sorted_tables) if t.name not in KEEP_TABLES]
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    yield engine
    await engine.dispose()


@pytest.fixture
def sessionmaker(engine):
    return make_sessionmaker(engine)


@pytest.fixture
def keys():
    return ConsoleKeys(MASTER_KEY)


@pytest.fixture
def make_settings(migrated_database_url):
    def make(**overrides) -> Settings:
        values = {
            "database_url": migrated_database_url,
            "public_url": PUBLIC_URL,
            "key": TEST_KEY,
            "entra_tenant_id": ENTRA_TENANT,
            "entra_client_id": ENTRA_CLIENT,
            "entra_client_secret": ENTRA_SECRET,
            "google_client_id": GOOGLE_CLIENT,
            "google_client_secret": GOOGLE_SECRET,
        }
        values.update(overrides)
        return Settings(**values)

    return make


@pytest.fixture
async def app(engine, make_settings):
    application = create_app(make_settings())
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def new_client(app):
    clients: list[httpx.AsyncClient] = []

    def make() -> httpx.AsyncClient:
        made = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=PUBLIC_URL)
        clients.append(made)
        return made

    yield make
    for made in clients:
        await made.aclose()


@pytest.fixture
async def client(new_client):
    return new_client()


class Factory:
    """Committed rows and signed-in browsers for tests."""

    def __init__(self, sessionmaker, keys):
        self.sessionmaker = sessionmaker
        self.keys = keys

    async def _save(self, row):
        async with self.sessionmaker() as session:
            session.add(row)
            await session.commit()
        return row

    async def person(
        self,
        client,
        *,
        principals=(),
        provider="entra",
        issuer=ENTRA_ISSUER,
        subject="entra-person-1",
        email="person@example.org",
        now=None,
    ) -> str:
        """Sign `client` in directly (a session row and its cookie). Returns the CSRF token."""
        async with self.sessionmaker() as session:
            session_id = await create_session(
                session,
                provider=provider,
                issuer=issuer,
                subject=subject,
                email=email,
                principals=frozenset(principals),
                now=now or utcnow(),
                lifetime=timedelta(hours=8),
            )
            await session.commit()
        client.cookies.set(SESSION_COOKIE, session_id, domain="console.test", path="/")
        return self.keys.csrf_token(session_id)

    async def grant(self, role, scope, kind, principal) -> RoleGrant:
        return await self._save(
            RoleGrant(
                role=role, scope=scope, principal_kind=kind, principal=principal, created_by="t"
            )
        )

    async def console_admin(self, kind, principal) -> ConsoleAdmin:
        return await self._save(
            ConsoleAdmin(principal_kind=kind, principal=principal, created_by="t")
        )


@pytest.fixture
def factory(sessionmaker, keys):
    return Factory(sessionmaker, keys)
