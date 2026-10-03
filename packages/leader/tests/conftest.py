import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import httpx
import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
from sqlalchemy import text
from swarmscribe_leader.auth.secrets import hash_secret, new_secret
from swarmscribe_leader.clock import utcnow
from swarmscribe_leader.config import ROLES, Settings
from swarmscribe_leader.db.migrate import upgrade
from swarmscribe_leader.db.models import Base, Follower, Job, Recording, StorageLocation
from swarmscribe_leader.db.session import make_engine, make_sessionmaker

TEST_DATABASE = "swarmscribe_test"
REPO_ROOT = Path(__file__).resolve().parents[3]
KEEP_TABLES = {"alembic_version", "settings_profiles"}


def _admin_url() -> str:
    url = os.environ.get("SWARMSCRIBE_TEST_DATABASE_URL")
    if url:
        return url
    import pgserver

    server = pgserver.get_server(str(REPO_ROOT / ".pgdata"), cleanup_mode="stop")
    return server.get_uri()


def _with_database(url: str, name: str) -> str:
    return urlunsplit(urlsplit(url)._replace(path="/" + name))


async def _recreate(admin_url: str, name: str) -> None:
    conn = await asyncpg.connect(admin_url)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def database_url() -> str:
    admin = _admin_url()
    asyncio.run(_recreate(admin, TEST_DATABASE))
    return _with_database(admin, TEST_DATABASE)


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


class Factory:
    """Creates committed rows for tests. Each method uses its own session."""

    def __init__(self, sessionmaker, root: Path):
        self.sessionmaker = sessionmaker
        self.root = root

    async def _save(self, row):
        async with self.sessionmaker() as session:
            session.add(row)
            await session.commit()
        return row

    async def location(self, **overrides) -> StorageLocation:
        values = {
            "id": uuid.uuid4(),
            "name": f"location-{uuid.uuid4().hex[:8]}",
            "backend": "local",
            "config": {"root": str(self.root)},
            "input_prefix": "",
            "output_prefix": "transcripts/",
            "pool": "default",
            "required_device": "any",
            "scan_interval_s": 900,
            "enabled": True,
            "vocabulary_version": 0,
        }
        values.update(overrides)
        return await self._save(StorageLocation(**values))

    async def recording(self, location=None, *, key="talks/one.mp3", **overrides) -> Recording:
        location = location or await self.location()
        now = utcnow()
        values = {
            "id": uuid.uuid4(),
            "location_id": location.id,
            "key": key,
            "size": 10,
            "source_version": "10-1",
            "consent": "consented",
            "first_seen_at": now,
            "last_seen_at": now,
            "missing": False,
        }
        values.update(overrides)
        return await self._save(Recording(**values))

    async def job(self, recording=None, **overrides) -> Job:
        recording = recording or await self.recording()
        values = {
            "id": uuid.uuid4(),
            "recording_id": recording.id,
            "source_version": recording.source_version,
            "state": "queued",
            "pool": "default",
            "required_device": "any",
            "priority": 0,
            "attempts": 0,
            "max_attempts": 3,
        }
        values.update(overrides)
        return await self._save(Job(**values))

    async def follower(
        self, *, pool="default", device="cpu", state="active", last_seen_at=None
    ) -> tuple[Follower, str]:
        credential = new_secret()
        follower = Follower(
            id=uuid.uuid4(),
            pool=pool,
            capabilities={"device": device, "models": [], "engine_version": "0.1.0", "pool": pool},
            credential_hash=hash_secret(credential),
            state=state,
            last_seen_at=last_seen_at or utcnow(),
        )
        await self._save(follower)
        return follower, credential


@pytest.fixture
def factory(sessionmaker, tmp_path):
    return Factory(sessionmaker, tmp_path)


@pytest.fixture(scope="session")
def signing_keys():
    return {
        name: rsa.generate_private_key(public_exponent=65537, key_size=2048)
        for name in ("entra", "google", "rogue", "service")
    }


class FakeIdentityProviders:
    """Entra ID and Google as the leader sees them: discovery documents and JWKS served
    through an injected fetcher, and ID tokens signed with locally generated keys."""

    ENTRA_TENANT = "0f0e0d0c-0b0a-4908-8706-050403020100"
    ENTRA_CLIENT = "6d3a6b52-1c2e-4a8f-9d61-2f6a4c0b7e11"
    GOOGLE_CLIENT = "google-client-1.apps.googleusercontent.com"
    ENTRA_ISSUER = f"https://login.microsoftonline.com/{ENTRA_TENANT}/v2.0"
    GOOGLE_ISSUER = "https://accounts.google.com"
    ENTRA_GROUPS = {
        "viewer": "a1a1a1a1-0000-4000-8000-000000000001",
        "operator": "a1a1a1a1-0000-4000-8000-000000000002",
        "admin": "a1a1a1a1-0000-4000-8000-000000000003",
    }
    GOOGLE_GROUPS = {
        "viewer": "viewers@example.org",
        "operator": "operators@example.org",
        "admin": "admins@example.org",
    }

    def __init__(self, keys):
        self.keys = keys
        self.signing = {"entra": "entra", "google": "google"}
        self.kids = {"entra": "entra-key-1", "google": "google-key-1"}
        self.published = {
            "entra": [("entra", "entra-key-1")],
            "google": [("google", "google-key-1")],
        }
        self.down = False
        self.fetched: list[str] = []

    def _jwks(self, provider: str) -> dict:
        keys = []
        for key_name, kid in self.published[provider]:
            jwk = json.loads(RSAAlgorithm.to_jwk(self.keys[key_name].public_key()))
            jwk.update(kid=kid, use="sig", alg="RS256")
            keys.append(jwk)
        return {"keys": keys}

    def _documents(self) -> dict[str, dict]:
        entra_jwks = f"https://login.microsoftonline.com/{self.ENTRA_TENANT}/discovery/v2.0/keys"
        google_jwks = "https://www.googleapis.com/oauth2/v3/certs"
        return {
            f"{self.ENTRA_ISSUER}/.well-known/openid-configuration": {
                "issuer": self.ENTRA_ISSUER,
                "jwks_uri": entra_jwks,
            },
            entra_jwks: self._jwks("entra"),
            "https://accounts.google.com/.well-known/openid-configuration": {
                "issuer": self.GOOGLE_ISSUER,
                "jwks_uri": google_jwks,
            },
            google_jwks: self._jwks("google"),
        }

    async def fetch(self, url: str) -> dict:
        self.fetched.append(url)
        if self.down:
            raise httpx.ConnectError("identity provider unreachable")
        return self._documents()[url]

    def rotate(self, provider: str, key_name: str, kid: str) -> None:
        """Publish another signing key and sign new tokens with it."""
        self.published[provider].append((key_name, kid))
        self.signing[provider] = key_name
        self.kids[provider] = kid

    def _defaults(self, provider: str) -> dict:
        if provider == "entra":
            return {
                "iss": self.ENTRA_ISSUER,
                "aud": self.ENTRA_CLIENT,
                "tid": self.ENTRA_TENANT,
                "sub": "entra-person-1",
                "oid": "00000000-0000-4000-8000-0000000000a1",
                "email": "person@example.org",
                "preferred_username": "person@example.org",
                "groups": [],
            }
        return {
            "iss": self.GOOGLE_ISSUER,
            "aud": self.GOOGLE_CLIENT,
            "sub": "google-person-1",
            "email": "person@example.org",
            "email_verified": True,
        }

    def token(self, provider, *, signed_with=None, kid=None, lifetime=3600, **claims) -> str:
        now = int(time.time())
        payload = {"iat": now, "nbf": now, "exp": now + lifetime}
        payload.update(self._defaults(provider))
        payload.update(claims)
        payload = {name: value for name, value in payload.items() if value is not None}
        key = self.keys[signed_with or self.signing[provider]]
        return pyjwt.encode(
            payload, key, algorithm="RS256", headers={"kid": kid or self.kids[provider]}
        )

    def entra(self, **claims) -> str:
        return self.token("entra", **claims)

    def google(self, **claims) -> str:
        return self.token("google", **claims)

    def bearer(self, role: str | None, *, provider: str = "entra") -> dict[str, str]:
        """Headers for a person holding `role` (None: no role at all)."""
        name = role or "nobody"
        email = f"{name}@example.org"
        if provider == "entra":
            groups = [self.ENTRA_GROUPS[role]] if role else []
            token = self.entra(groups=groups, sub=f"entra-{name}", email=email)
        else:
            token = self.google(email=email, sub=f"google-{name}")
        return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def idp(signing_keys):
    return FakeIdentityProviders(signing_keys)


@pytest.fixture
def sign_in_settings(signing_keys):
    """Settings with both providers and every role mapping configured. Database-free by
    default; pass database_url=… for tests that need one."""
    private_key = (
        signing_keys["service"]
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode()
    )
    service_account = json.dumps(
        {
            "type": "service_account",
            "client_email": "group-reader@project-1.iam.gserviceaccount.com",
            "private_key": private_key,
            "private_key_id": "service-key-1",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    )
    fake = FakeIdentityProviders

    def make(**overrides) -> Settings:
        values = {
            "database_url": "postgresql://u:p@127.0.0.1:1/none",
            "public_url": "http://leader",
            "link_key": "k" * 32,
            "entra_tenant_id": fake.ENTRA_TENANT,
            "entra_client_id": fake.ENTRA_CLIENT,
            "entra_client_secret": "entra-app-secret-value",
            "google_client_id": fake.GOOGLE_CLIENT,
            "google_client_secret": "google-device-secret-value",
            "google_service_account": service_account,
        }
        for role in ROLES:
            values[f"role_{role}_entra_groups"] = (fake.ENTRA_GROUPS[role],)
            values[f"role_{role}_google_groups"] = (fake.GOOGLE_GROUPS[role],)
            values[f"role_{role}_emails"] = (f"{role}@example.org",)
        values.update(overrides)
        return Settings(**values)

    return make
