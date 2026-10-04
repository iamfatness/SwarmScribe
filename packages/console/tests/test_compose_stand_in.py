"""The console Compose test's own parts (e2e/console-compose), checked without Docker.

The stand-in for Entra ID is exercised in process by the code that talks to it inside the
Compose network: the console's code exchange and token verification, and the admin CLI's
device sign-in from a leader's login-config. Also checked: the certificate the test makes
for itself, the driver's hand-kept cookie jar, and that docker-compose.yml, fake_idp.py and
run_e2e.py agree on the values they share."""

import asyncio
import importlib.util
import secrets
import ssl
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import httpx
import pytest
import yaml
from swarmscribe_console.config import Settings
from swarmscribe_console.oidc import (
    CodeExchangeFailed,
    authorization_url,
    exchange_code,
    web_providers,
)
from swarmscribe_leader.admin_cli.device_flow import ProviderConfig, device_sign_in
from swarmscribe_leader.auth.oidc import TokenVerifier, login_providers, providers_from
from swarmscribe_leader.auth.roles import RoleMapping
from swarmscribe_leader.config import Settings as LeaderSettings

COMPOSE = Path(__file__).resolve().parents[3] / "e2e" / "console-compose"


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, COMPOSE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fake_idp = load("fake_idp", "fake_idp.py")  # run_e2e.py imports it by this name
driver = load("console_compose_driver", "run_e2e.py")

TENANT = "0f0e0d0c-0b0a-4908-8706-050403020100"
CONSOLE_CLIENT = "11111111-1111-4111-8111-111111111111"
LEADER_CLIENT = "22222222-2222-4222-8222-222222222222"
SECRET = "console-compose-client-secret"
REDIRECT = "http://localhost:18080/auth/callback"
STATE, NONCE = "s" * 43, "n" * 43


@pytest.fixture
def transport():
    app = fake_idp.create_app(
        tenant=TENANT,
        console_client_id=CONSOLE_CLIENT,
        console_client_secret=SECRET,
        console_redirect_uri=REDIRECT,
        leader_client_id=LEADER_CLIENT,
    )
    return httpx.ASGITransport(app=app)


@pytest.fixture
def fetch(transport):
    async def fetch(url: str) -> dict:
        async with httpx.AsyncClient(transport=transport) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()

    return fetch


@pytest.fixture
def provider():
    settings = Settings(
        database_url="postgresql://u:p@127.0.0.1:1/none",
        public_url="http://localhost:18080",
        key="AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8",
        entra_tenant_id=TENANT,
        entra_client_id=CONSOLE_CLIENT,
        entra_client_secret=SECRET,
    )
    return web_providers(settings)["entra"]


async def visit(transport, provider, verifier: str, persona: str | None) -> httpx.Response:
    """The browser at the authorization page the console sent it to."""
    url = authorization_url(
        provider, redirect_uri=REDIRECT, state=STATE, nonce=NONCE, verifier=verifier
    )
    if persona is not None:
        url += f"&login_hint={persona}"
    async with httpx.AsyncClient(transport=transport) as browser:
        return await browser.get(url)


def code_of(answer: httpx.Response) -> str:
    assert answer.status_code == 302, answer.text
    back = urlsplit(answer.headers["location"])
    assert f"{back.scheme}://{back.netloc}{back.path}" == REDIRECT
    params = dict(parse_qsl(back.query))
    assert params["state"] == STATE
    return params["code"]


# --- the console's sign-in --------------------------------------------------------------


async def test_the_console_signs_a_person_in_through_the_stand_in(transport, fetch, provider):
    verifier = secrets.token_urlsafe(64)
    code = code_of(await visit(transport, provider, verifier, "console-admin"))
    id_token = await exchange_code(
        provider, code=code, verifier=verifier, redirect_uri=REDIRECT, transport=transport
    )
    identity = await TokenVerifier([provider.verification], fetch=fetch).verify(id_token)
    assert identity.provider == "entra"
    assert identity.email == "console-admin@example.org"
    assert identity.claims["nonce"] == NONCE
    assert identity.claims["groups"] == [fake_idp.CONSOLE_ADMINS, fake_idp.FLEET_ADMINS]


async def test_a_code_works_once_and_only_with_its_verifier(transport, provider):
    verifier = secrets.token_urlsafe(64)
    code = code_of(await visit(transport, provider, verifier, "console-admin"))
    with pytest.raises(CodeExchangeFailed):
        await exchange_code(
            provider,
            code=code,
            verifier=secrets.token_urlsafe(64),
            redirect_uri=REDIRECT,
            transport=transport,
        )
    with pytest.raises(CodeExchangeFailed):  # the failed attempt used the code up
        await exchange_code(
            provider, code=code, verifier=verifier, redirect_uri=REDIRECT, transport=transport
        )


@pytest.mark.parametrize("persona", [None, "nobody-by-this-name"])
async def test_the_stand_in_signs_in_named_personas_only(transport, provider, persona):
    answer = await visit(transport, provider, secrets.token_urlsafe(64), persona)
    assert answer.status_code == 400


# --- the admin CLI's sign-in to a leader ------------------------------------------------


async def test_the_admin_cli_signs_in_to_a_leader_through_the_stand_in(transport, fetch):
    leader = LeaderSettings(
        database_url="postgresql://u:p@127.0.0.1:1/none",
        public_url="https://leader-a:8443",
        link_key="k" * 32,
        entra_tenant_id=TENANT,
        entra_client_id=LEADER_CLIENT,
        role_admin_entra_groups=fake_idp.LEADER_ADMINS,
    )
    (config,) = login_providers(leader)  # what GET /v1/admin/login-config hands the CLI
    shown: list[str] = []
    async with httpx.AsyncClient(transport=transport) as http:
        tokens = await device_sign_in(
            http,
            ProviderConfig(
                name=config["name"],
                client_id=config["client_id"],
                device_authorization_endpoint=config["device_authorization_endpoint"],
                token_endpoint=config["token_endpoint"],
                scope=config["scope"],
                client_secret=config["client_secret"],
            ),
            prompt=shown.append,
            sleep=lambda _seconds: asyncio.sleep(0),
        )
    assert len(shown) == 1
    person = await TokenVerifier(providers_from(leader), fetch=fetch).verify(tokens.id_token)
    assert person.email == "leader-admin@example.org"
    admins = RoleMapping.from_settings(leader).entra_groups["admin"]
    assert set(person.claims["groups"]) & admins


# --- the test's certificate -------------------------------------------------------------


async def handshake(port: int, ca_file: Path, name: str) -> None:
    context = ssl.create_default_context(cafile=str(ca_file))
    _reader, writer = await asyncio.open_connection(
        "127.0.0.1", port, ssl=context, server_hostname=name
    )
    writer.close()
    await writer.wait_closed()


async def test_the_test_certificate_covers_every_name_and_no_other(tmp_path):
    driver.write_certs(tmp_path)
    serving = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    serving.load_cert_chain(tmp_path / "server.pem", tmp_path / "server.key")

    async def hang_up(_reader, writer) -> None:
        writer.close()

    server = await asyncio.start_server(hang_up, "127.0.0.1", 0, ssl=serving)
    port = server.sockets[0].getsockname()[1]
    try:
        for name in ("login.microsoftonline.com", "leader-a", "leader-b", "localhost"):
            await handshake(port, tmp_path / "ca.pem", name)
        with pytest.raises(ssl.SSLCertVerificationError):
            await handshake(port, tmp_path / "ca.pem", "graph.microsoft.com")
    finally:
        server.close()
        await server.wait_closed()


# --- the driver's browser ---------------------------------------------------------------


def test_the_browser_keeps_secure_cookies_and_sends_the_csrf_token(tmp_path):
    driver.write_certs(tmp_path)
    seen: list[httpx.Request] = []

    def console(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/set":
            return httpx.Response(
                200,
                headers=[
                    ("set-cookie", "__Host-a=one; Path=/; Secure; HttpOnly; SameSite=Strict"),
                    ("set-cookie", "__Host-b=two; Path=/; Secure; HttpOnly; SameSite=Lax"),
                ],
            )
        if request.url.path == "/drop":
            return httpx.Response(
                200, headers={"set-cookie": '__Host-b=""; Max-Age=0; Path=/; Secure'}
            )
        return httpx.Response(204)

    browser = driver.Browser(tmp_path / "ca.pem")
    browser.console = httpx.Client(
        transport=httpx.MockTransport(console), base_url=driver.CONSOLE
    )
    try:
        browser.get("/set")
        browser.get("/next")
        assert seen[-1].headers["cookie"] == "__Host-a=one; __Host-b=two"
        assert "origin" not in seen[-1].headers
        browser.get("/drop")
        browser.csrf_token = "csrf-value"
        browser.post("/change", json={})
        assert seen[-1].headers["cookie"] == "__Host-a=one"
        assert seen[-1].headers["origin"] == driver.CONSOLE
        assert seen[-1].headers["x-csrf-token"] == "csrf-value"
    finally:
        browser.close()


# --- one set of values ------------------------------------------------------------------


def test_the_compose_file_the_stand_in_and_the_driver_agree():
    services = yaml.safe_load((COMPOSE / "docker-compose.yml").read_text(encoding="utf-8"))[
        "services"
    ]
    console = services["console"]["environment"]
    idp = services["fake-idp"]["environment"]
    assert console["SWARMSCRIBE_CONSOLE_PUBLIC_URL"] == driver.CONSOLE
    assert idp["FAKE_IDP_CONSOLE_REDIRECT_URI"] == driver.CONSOLE + "/auth/callback"
    assert idp["FAKE_IDP_TENANT"] == console["SWARMSCRIBE_CONSOLE_ENTRA_TENANT_ID"]
    assert idp["FAKE_IDP_CONSOLE_CLIENT_ID"] == console["SWARMSCRIBE_CONSOLE_ENTRA_CLIENT_ID"]
    assert (
        idp["FAKE_IDP_CONSOLE_CLIENT_SECRET"]
        == console["SWARMSCRIBE_CONSOLE_ENTRA_CLIENT_SECRET"]
    )
    assert services["console"]["ports"] == [f"{urlsplit(driver.CONSOLE).port}:8080"]
    assert services["fake-idp"]["ports"] == [f"{urlsplit(driver.IDP).port}:443"]
    assert services["fake-idp"]["networks"]["default"]["aliases"] == [driver.ENTRA_HOST]
    assert services["console-bootstrap"]["command"][-1] == fake_idp.CONSOLE_ADMINS
    for name in driver.LEADERS:
        leader = services[name]["environment"]
        assert leader["SWARMSCRIBE_ROLE_ADMIN_ENTRA_GROUPS"] == fake_idp.LEADER_ADMINS
        assert leader["SWARMSCRIBE_ENTRA_CLIENT_ID"] == idp["FAKE_IDP_LEADER_CLIENT_ID"]
        assert leader["SWARMSCRIBE_ENTRA_TENANT_ID"] == idp["FAKE_IDP_TENANT"]
        assert leader["SWARMSCRIBE_PUBLIC_URL"] == f"https://{name}:8443"
        assert name in driver.SERVER_NAMES
    # The console runs as the chart runs it: read-only, and through the image's own user.
    assert services["console"]["read_only"] is True
    assert "user" not in services["console"]
    assert "healthcheck" not in services["console"]  # the image's HEALTHCHECK is under test
    # Leader calls trust the test CA through the console's own setting, not SSL_CERT_FILE.
    assert console["SWARMSCRIBE_CONSOLE_LEADER_CA_FILE"] == "/certs/ca.pem"
    assert console["SSL_CERT_FILE"] == "/certs/ca.pem"
    assert fake_idp.DEVICE_PERSONA in fake_idp.PERSONAS
    assert fake_idp.FLEET_ADMINS in fake_idp.PERSONAS["console-admin"]
