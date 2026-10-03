import os
import stat
from pathlib import Path
from urllib.parse import parse_qsl

import httpx
import pytest
import swarmscribe_leader.admin_cli
from swarmscribe_leader.admin_cli.credentials import (
    CredentialsFileError,
    CredentialStore,
    SignIn,
    check_owner,
)
from swarmscribe_leader.admin_cli.device_flow import (
    ProviderConfig,
    SignInError,
    device_sign_in,
    id_token_expiry,
    refresh_tokens,
)

ENTRA = ProviderConfig(
    name="entra",
    client_id="entra-client",
    device_authorization_endpoint="https://login.microsoftonline.com/t/oauth2/v2.0/devicecode",
    token_endpoint="https://login.microsoftonline.com/t/oauth2/v2.0/token",
    scope="openid profile email offline_access",
)
GOOGLE = ProviderConfig(
    name="google",
    client_id="google-client",
    device_authorization_endpoint="https://oauth2.googleapis.com/device/code",
    token_endpoint="https://oauth2.googleapis.com/token",
    scope="openid email profile",
    client_secret="google-device-secret",
)


class ScriptedProvider:
    """A provider's device-code endpoint and a scripted series of token-endpoint answers."""

    def __init__(self, answers, **start):
        self.answers = list(answers)
        self.start = {
            "device_code": "device-code-1",
            "user_code": "WDJB-MJHT",
            "verification_uri": "https://microsoft.com/devicelogin",
            "expires_in": 900,
            "interval": 5,
            **start,
        }
        self.token_forms: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(("/devicecode", "/device/code")):
            return httpx.Response(200, json=self.start)
        self.token_forms.append(dict(parse_qsl(request.content.decode())))
        status, body = self.answers.pop(0)
        return httpx.Response(status, json=body)


async def sign_in(config, provider, **kwargs):
    sleeps: list[float] = []
    prompts: list[str] = []

    async def sleep(seconds):
        sleeps.append(seconds)

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
        tokens = await device_sign_in(http, config, prompt=prompts.append, sleep=sleep, **kwargs)
    return tokens, sleeps, prompts


async def test_entra_sign_in_waits_while_pending_and_slows_down_when_told(idp):
    token = idp.entra()
    provider = ScriptedProvider(
        [
            (400, {"error": "authorization_pending"}),
            (400, {"error": "slow_down"}),
            (200, {"id_token": token, "refresh_token": "refresh-1", "expires_in": 3600}),
        ]
    )
    tokens, sleeps, prompts = await sign_in(ENTRA, provider)
    assert (tokens.id_token, tokens.refresh_token) == (token, "refresh-1")
    assert tokens.expires_at == id_token_expiry(token)
    assert sleeps == [5, 5, 10]
    assert prompts == [
        "To sign in, open https://microsoft.com/devicelogin and enter the code WDJB-MJHT"
    ]
    form = provider.token_forms[0]
    assert form == {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "client_id": "entra-client",
        "device_code": "device-code-1",
    }


async def test_google_sign_in_uses_its_verification_url_and_client_secret(idp):
    provider = ScriptedProvider(
        [(200, {"id_token": idp.google(), "refresh_token": "refresh-1"})],
        verification_uri=None,
        verification_url="https://www.google.com/device",
    )
    _tokens, _sleeps, prompts = await sign_in(GOOGLE, provider)
    assert "https://www.google.com/device" in prompts[0]
    assert provider.token_forms[0]["client_secret"] == "google-device-secret"


@pytest.mark.parametrize(
    "answer, message",
    [
        ((400, {"error": "access_denied"}), "declined"),
        ((400, {"error": "authorization_declined"}), "declined"),
        ((400, {"error": "expired_token"}), "expired"),
        ((400, {"error": "invalid_client"}), "invalid_client"),
    ],
)
async def test_sign_in_failures_say_what_happened_without_the_device_code(answer, message):
    with pytest.raises(SignInError, match=message) as excinfo:
        await sign_in(ENTRA, ScriptedProvider([answer]))
    assert "device-code-1" not in str(excinfo.value)


async def test_a_code_that_runs_out_of_time_is_an_error():
    with pytest.raises(SignInError, match="expired"):
        await sign_in(ENTRA, ScriptedProvider([], expires_in=0))


async def test_a_provider_that_will_not_start_a_sign_in_is_an_error():
    def refuse(request):
        return httpx.Response(400, json={"error": "unauthorized_client"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(refuse)) as http:
        with pytest.raises(SignInError, match="unauthorized_client"):
            await device_sign_in(http, ENTRA, prompt=print)


async def test_refresh_returns_new_tokens_and_sends_what_the_provider_needs(idp):
    provider = ScriptedProvider([(200, {"id_token": idp.google()})])
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
        tokens = await refresh_tokens(
            http,
            token_endpoint=GOOGLE.token_endpoint,
            client_id="google-client",
            refresh_token="refresh-1",
            client_secret="google-device-secret",
        )
    assert tokens.refresh_token is None  # Google keeps the old refresh token valid
    assert provider.token_forms == [
        {
            "grant_type": "refresh_token",
            "client_id": "google-client",
            "refresh_token": "refresh-1",
            "client_secret": "google-device-secret",
        }
    ]


async def test_a_refused_refresh_asks_for_a_new_login():
    provider = ScriptedProvider([(400, {"error": "invalid_grant"})])
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
        with pytest.raises(SignInError, match="run `swarmscribe-admin login`") as excinfo:
            await refresh_tokens(
                http, token_endpoint=ENTRA.token_endpoint, client_id="c", refresh_token="r-9"
            )
    assert "r-9" not in str(excinfo.value)


def test_id_token_expiry(idp):
    assert id_token_expiry(idp.entra(exp=2_000_000_000)) == 2_000_000_000
    assert id_token_expiry("not-a-token") is None


def sign_in_record(leader="https://leader.example.org", **overrides) -> SignIn:
    values = {
        "leader": leader,
        "provider": "entra",
        "client_id": "entra-client",
        "token_endpoint": ENTRA.token_endpoint,
        "scope": ENTRA.scope,
        "id_token": "id-token-secret",
        "refresh_token": "refresh-token-secret",
    }
    values.update(overrides)
    return SignIn(**values)


def test_the_store_keeps_one_sign_in_per_leader(tmp_path):
    store = CredentialStore(tmp_path / "config" / "credentials.json")
    assert store.load("https://leader.example.org") is None
    store.save(sign_in_record())
    store.save(sign_in_record("https://other.example.org", provider="google"))
    assert store.load("https://leader.example.org") == sign_in_record()
    assert store.default_leader() == "https://other.example.org"
    assert store.remove("https://other.example.org") is True
    assert store.default_leader() is None
    assert store.load("https://leader.example.org") == sign_in_record()


@pytest.mark.parametrize("content", ["{not json", "[]", '{"leaders": 3}', ""])
def test_a_corrupt_cache_is_refused_with_a_clear_message(tmp_path, content):
    path = tmp_path / "credentials.json"
    path.write_text(content, encoding="utf-8")
    store = CredentialStore(path)
    with pytest.raises(CredentialsFileError, match="corrupted"):
        store.load("https://leader.example.org")
    with pytest.raises(CredentialsFileError, match="corrupted"):
        store.save(sign_in_record())  # never silently overwritten either
    assert path.read_text(encoding="utf-8") == content


def test_a_cache_that_is_not_a_file_is_refused(tmp_path):
    path = tmp_path / "credentials.json"
    path.mkdir()
    with pytest.raises(CredentialsFileError, match="not a regular file"):
        CredentialStore(path).load("https://leader.example.org")


def test_a_cache_owned_by_someone_else_is_refused(tmp_path):
    with pytest.raises(CredentialsFileError, match="owned by another user"):
        check_owner(1001, 1000, tmp_path / "credentials.json")
    check_owner(1000, 1000, tmp_path / "credentials.json")  # our own is fine


def test_saving_leaves_no_temp_file_behind(tmp_path):
    path = tmp_path / "config" / "credentials.json"
    store = CredentialStore(path)
    store.save(sign_in_record())
    store.save(sign_in_record(id_token="id-token-secret-2"))
    assert [item.name for item in path.parent.iterdir()] == ["credentials.json"]
    assert store.load("https://leader.example.org").id_token == "id-token-secret-2"


def test_a_failed_save_keeps_the_old_file_and_cleans_up(tmp_path, monkeypatch):
    path = tmp_path / "credentials.json"
    store = CredentialStore(path)
    store.save(sign_in_record())
    before = path.read_text(encoding="utf-8")

    def boom(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        store.save(sign_in_record(id_token="id-token-secret-2"))
    assert path.read_text(encoding="utf-8") == before
    assert [item.name for item in tmp_path.iterdir()] == ["credentials.json"]


def test_the_refusal_message_holds_no_file_content(tmp_path):
    path = tmp_path / "credentials.json"
    path.write_text('{"leaders": 3, "refresh_token": "refresh-token-secret"', encoding="utf-8")
    with pytest.raises(CredentialsFileError) as excinfo:
        CredentialStore(path).load("x")
    assert "refresh-token-secret" not in str(excinfo.value)


def test_tokens_are_not_in_the_repr():
    shown = repr(sign_in_record(client_secret="client-secret-value"))
    for secret in ("id-token-secret", "refresh-token-secret", "client-secret-value"):
        assert secret not in shown


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_the_cache_is_readable_by_its_owner_only(tmp_path):
    path = tmp_path / "config" / "credentials.json"
    CredentialStore(path).save(sign_in_record())
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_a_cache_left_readable_by_others_is_tightened(tmp_path):
    path = tmp_path / "credentials.json"
    store = CredentialStore(path)
    store.save(sign_in_record())
    os.chmod(path, 0o644)
    assert store.load("https://leader.example.org") is not None
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_the_cli_imports_nothing_from_the_server():
    folder = Path(swarmscribe_leader.admin_cli.__file__).parent
    for path in folder.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "from .." not in text, path.name
        assert "import swarmscribe_leader" not in text, path.name


async def test_polling_stops_at_expires_in_even_while_still_pending():
    now = [1000.0]
    polls = []

    def provider(request):
        if request.url.path.endswith("/devicecode"):
            return httpx.Response(
                200,
                json={
                    "device_code": "device-code-1",
                    "user_code": "U-1",
                    "verification_uri": "https://x",
                    "expires_in": 30,
                    "interval": 10,
                },
            )
        polls.append(now[0])
        return httpx.Response(400, json={"error": "authorization_pending"})

    async def sleep(seconds):
        now[0] += seconds

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
        with pytest.raises(SignInError, match="expired"):
            await device_sign_in(
                http, ENTRA, prompt=lambda _m: None, sleep=sleep, clock=lambda: now[0]
            )
    assert polls == [1010.0, 1020.0, 1030.0]


async def test_no_token_or_device_code_is_ever_printed(idp, capsys, caplog):
    token = idp.entra()
    provider = ScriptedProvider(
        [
            (400, {"error": "authorization_pending"}),
            (200, {"id_token": token, "refresh_token": "r-1"}),
        ]
    )
    caplog.set_level("DEBUG")
    tokens, _sleeps, prompts = await sign_in(ENTRA, provider)
    out, err = capsys.readouterr()
    shown = out + err + caplog.text + " ".join(prompts) + repr(tokens) + repr(ENTRA) + repr(GOOGLE)
    for secret in (token, "r-1", "device-code-1", "google-device-secret"):
        assert secret not in shown


async def test_a_token_response_without_an_id_token_is_an_error():
    with pytest.raises(SignInError, match="no ID token"):
        await sign_in(ENTRA, ScriptedProvider([(200, {"access_token": "a"})]))
