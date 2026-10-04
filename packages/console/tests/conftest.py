import asyncio
import os
import sys
from pathlib import Path

# console_testkit (shared constants and helpers) sits beside this file.
sys.path.insert(0, str(Path(__file__).parent))

import base64  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import secrets  # noqa: E402
import time  # noqa: E402
from datetime import timedelta  # noqa: E402
from urllib.parse import parse_qsl, urlencode  # noqa: E402

import httpx  # noqa: E402
import jwt as pyjwt  # noqa: E402
import pytest  # noqa: E402
from console_testkit import (  # noqa: E402
    CREDENTIAL,
    ENTRA_CLIENT,
    ENTRA_ISSUER,
    ENTRA_SECRET,
    ENTRA_TENANT,
    GOOGLE_CLIENT,
    GOOGLE_ISSUER,
    GOOGLE_SECRET,
    MASTER_KEY,
    PUBLIC_URL,
    TEST_KEY,
    recreate,
    with_database,
)
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from jwt.algorithms import RSAAlgorithm  # noqa: E402
from sqlalchemy import text  # noqa: E402
from swarmscribe_console.app import create_app  # noqa: E402
from swarmscribe_console.config import Settings  # noqa: E402
from swarmscribe_console.crypto import ConsoleKeys  # noqa: E402
from swarmscribe_console.db.migrate import upgrade  # noqa: E402
from swarmscribe_console.db.models import (  # noqa: E402
    Base,
    ConsoleAdmin,
    Leader,
    RoleGrant,
)
from swarmscribe_console.sessions import SESSION_COOKIE, create_session  # noqa: E402
from swarmscribe_leader.auth.roles import RoleLookupFailed  # noqa: E402
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
async def app(engine, make_settings, idp, graph, google_groups):
    application = create_app(
        make_settings(),
        fetch=idp.fetch,
        idp_transport=idp.transport,
        graph=graph,
        google_groups=google_groups,
    )
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

    async def leader(
        self,
        name="eu-1",
        *,
        base_url=None,
        labels=None,
        credential=CREDENTIAL,
        enabled=True,
    ) -> Leader:
        return await self._save(
            Leader(
                name=name,
                base_url=base_url or f"https://{name}.leaders.example",
                labels=labels or {},
                enabled=enabled,
                credential=self.keys.seal_credential(name, credential),
                credential_updated_by="t",
                added_by="t",
            )
        )


@pytest.fixture
def factory(sessionmaker, keys):
    return Factory(sessionmaker, keys)


@pytest.fixture(scope="session")
def signing_keys():
    return {
        name: rsa.generate_private_key(public_exponent=65537, key_size=2048)
        for name in ("entra", "google", "rogue")
    }


class FakeIdentityProviders:
    """Entra ID and Google as the console sees them: discovery documents and JWKS through an
    injected fetcher; the person's visit to the authorization page (`authorize`); and the
    token endpoint the console posts the code to (`transport`). Every token it hands out is
    kept in `issued`, so tests can check none of them is stored."""

    SECRETS = {"entra": ENTRA_SECRET, "google": GOOGLE_SECRET}
    CLIENTS = {"entra": ENTRA_CLIENT, "google": GOOGLE_CLIENT}

    def __init__(self, keys):
        self.keys = keys
        self.codes: dict[str, dict] = {}
        self.exchanges: list[dict[str, str]] = []
        self.issued: list[str] = []
        self.token_status = 200
        self.token_html = False  # a 200 answer that is a web page

    def _jwks(self, provider: str) -> dict:
        jwk = json.loads(RSAAlgorithm.to_jwk(self.keys[provider].public_key()))
        jwk.update(kid=f"{provider}-key-1", use="sig", alg="RS256")
        return {"keys": [jwk]}

    async def fetch(self, url: str) -> dict:
        entra_jwks = f"https://login.microsoftonline.com/{ENTRA_TENANT}/discovery/v2.0/keys"
        google_jwks = "https://www.googleapis.com/oauth2/v3/certs"
        documents = {
            f"{ENTRA_ISSUER}/.well-known/openid-configuration": {
                "issuer": ENTRA_ISSUER,
                "jwks_uri": entra_jwks,
            },
            entra_jwks: self._jwks("entra"),
            "https://accounts.google.com/.well-known/openid-configuration": {
                "issuer": GOOGLE_ISSUER,
                "jwks_uri": google_jwks,
            },
            google_jwks: self._jwks("google"),
        }
        return documents[url]

    def _defaults(self, provider: str) -> dict:
        if provider == "entra":
            return {
                "iss": ENTRA_ISSUER,
                "aud": ENTRA_CLIENT,
                "tid": ENTRA_TENANT,
                "sub": "entra-person-1",
                "oid": "00000000-0000-4000-8000-0000000000a1",
                "email": "person@example.org",
                "groups": [],
            }
        return {
            "iss": GOOGLE_ISSUER,
            "aud": GOOGLE_CLIENT,
            "sub": "google-person-1",
            "email": "person@example.org",
            "email_verified": True,
        }

    def id_token(self, provider: str, *, signed_with: str | None = None, **claims) -> str:
        now = int(time.time())
        payload = {"iat": now, "nbf": now, "exp": now + 3600, **self._defaults(provider)}
        payload.update(claims)
        payload = {k: v for k, v in payload.items() if v is not None}
        key = self.keys[signed_with or provider]
        return pyjwt.encode(
            payload, key, algorithm="RS256", headers={"kid": f"{provider}-key-1"}
        )

    def authorize(self, location: str, **claims) -> str:
        """The person signs in at the provider. Returns the path and query the provider
        sends the browser back to."""
        url = httpx.URL(location)
        params = dict(url.params)
        provider = "entra" if url.host == "login.microsoftonline.com" else "google"
        code = secrets.token_urlsafe(24)
        self.codes[code] = {
            "provider": provider,
            "claims": {"nonce": params["nonce"], **claims},
            "challenge": params["code_challenge"],
            "redirect_uri": params["redirect_uri"],
        }
        back = httpx.URL(params["redirect_uri"])
        return f"{back.path}?{urlencode({'code': code, 'state': params['state']})}"

    async def _token_endpoint(self, request: httpx.Request) -> httpx.Response:
        form = dict(parse_qsl((await request.aread()).decode()))
        self.exchanges.append(form)
        if self.token_html:
            return httpx.Response(
                200,
                text="<html><body>Service unavailable</body></html>",
                headers={"content-type": "text/html"},
            )
        if self.token_status != 200:
            return httpx.Response(self.token_status, json={"error": "invalid_grant"})
        grant = self.codes.pop(form.get("code", ""), None)
        if grant is None:
            return httpx.Response(400, json={"error": "invalid_grant"})
        provider = grant["provider"]
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        if (
            form.get("grant_type") != "authorization_code"
            or challenge != grant["challenge"]
            or form.get("redirect_uri") != grant["redirect_uri"]
            or form.get("client_id") != self.CLIENTS[provider]
            or form.get("client_secret") != self.SECRETS[provider]
        ):
            return httpx.Response(400, json={"error": "invalid_grant"})
        id_token = self.id_token(provider, **grant["claims"])
        access = f"access-{secrets.token_urlsafe(16)}"
        refresh = f"refresh-{secrets.token_urlsafe(16)}"
        self.issued += [id_token, access, refresh]
        return httpx.Response(
            200,
            json={
                "token_type": "Bearer",
                "expires_in": 3600,
                "id_token": id_token,
                "access_token": access,
                "refresh_token": refresh,
            },
        )

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._token_endpoint)


@pytest.fixture
def idp(signing_keys):
    return FakeIdentityProviders(signing_keys)


class FakeGraph:
    def __init__(self):
        self.groups: dict[str, set[str]] = {}
        self.failing = False

    async def member_object_ids(self, user_object_id: str) -> set[str]:
        if self.failing:
            raise RoleLookupFailed("Microsoft Graph could not be asked: ConnectError")
        return set(self.groups.get(user_object_id, set()))


class FakeGoogleGroups:
    def __init__(self):
        self.groups: dict[str, set[str]] = {}
        self.failing = False

    async def group_emails(self, email: str) -> set[str]:
        if self.failing:
            raise RoleLookupFailed("Google Cloud Identity could not be asked: ConnectError")
        return set(self.groups.get(email, set()))


@pytest.fixture
def graph():
    return FakeGraph()


@pytest.fixture
def google_groups():
    return FakeGoogleGroups()
