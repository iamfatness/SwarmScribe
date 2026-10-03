import json
import os
import stat
from dataclasses import asdict
from pathlib import Path
from urllib.parse import parse_qsl

import httpx
import pytest
import swarmscribe_leader.admin_cli
from swarmscribe_leader.admin_cli.credentials import (
    CredentialsFileError,
    CredentialStore,
    SignIn,
    check_folder,
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
            provider="google",
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
                http,
                token_endpoint=ENTRA.token_endpoint,
                client_id="c",
                refresh_token="r-9",
                provider="entra",
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


# ---- fix round 1: endpoints, polling robustness, stricter reads ----------------------------

BAD_ENDPOINTS = [
    "http://login.microsoftonline.com/t/oauth2/v2.0/token",
    "https://evil.test/t/oauth2/v2.0/token",
    "https://login.microsoftonline.com.evil.test/t/oauth2/v2.0/token",
    "https://login.microsoftonline.com@evil.test/",
    "https://login.microsoftonline.com:8443/t/oauth2/v2.0/token",
    "https://sub.login.microsoftonline.com/t/oauth2/v2.0/token",
    "https://oauth2.googleapis.com/token",  # a Google host is not an Entra host
    "ftp://login.microsoftonline.com/x",
    "",
]


class CountingProvider(ScriptedProvider):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.requests = 0

    def __call__(self, request):
        self.requests += 1
        return super().__call__(request)


@pytest.mark.parametrize("endpoint", BAD_ENDPOINTS)
async def test_device_sign_in_refuses_a_bad_endpoint_before_any_request(endpoint):
    for config in (
        ProviderConfig("entra", "c", endpoint, ENTRA.token_endpoint, "s"),
        ProviderConfig("entra", "c", ENTRA.device_authorization_endpoint, endpoint, "s"),
    ):
        provider = CountingProvider([])
        with pytest.raises(SignInError, match="not a trusted"):
            await sign_in(config, provider)
        assert provider.requests == 0


async def test_google_endpoints_must_be_googles():
    config = ProviderConfig(
        "google", "c", "https://login.microsoftonline.com/device", GOOGLE.token_endpoint, "s", "x"
    )
    provider = CountingProvider([])
    with pytest.raises(SignInError, match="not a trusted"):
        await sign_in(config, provider)
    assert provider.requests == 0


async def test_an_unknown_provider_name_is_refused():
    config = ProviderConfig(
        "evil", "c", ENTRA.device_authorization_endpoint, ENTRA.token_endpoint, "s"
    )
    with pytest.raises(SignInError, match="not a trusted"):
        await sign_in(config, CountingProvider([]))


@pytest.mark.parametrize("endpoint", BAD_ENDPOINTS[:-1])
async def test_refresh_refuses_a_bad_endpoint_before_any_request(endpoint):
    provider = CountingProvider([])
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
        with pytest.raises(SignInError, match="not a trusted") as excinfo:
            await refresh_tokens(
                http, token_endpoint=endpoint, client_id="c", refresh_token="r-9", provider="entra"
            )
    assert provider.requests == 0
    assert "r-9" not in str(excinfo.value)


async def test_refresh_requires_a_provider():
    provider = CountingProvider([])
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
        with pytest.raises(TypeError):
            await refresh_tokens(  # type: ignore[call-arg]
                http,
                token_endpoint=ENTRA.token_endpoint,
                client_id="c",
                refresh_token="r",
            )
    assert provider.requests == 0


async def test_refresh_with_an_unknown_provider_is_refused():
    provider = CountingProvider([])
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
        with pytest.raises(SignInError, match="not a trusted"):
            await refresh_tokens(
                http,
                token_endpoint="https://evil.test/token",
                client_id="c",
                refresh_token="r",
                provider="evil",
            )
    assert provider.requests == 0


@pytest.mark.parametrize("padding", [" {}", "{} ", "\t{}", "{}\n", " {} "])
async def test_an_endpoint_with_surrounding_whitespace_is_refused(padding):
    endpoint = padding.format(ENTRA.token_endpoint)
    provider = CountingProvider([])
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
        with pytest.raises(SignInError, match="not a trusted"):
            await refresh_tokens(
                http,
                token_endpoint=endpoint,
                client_id="c",
                refresh_token="r",
                provider="entra",
            )
    assert provider.requests == 0


async def test_an_interval_of_zero_is_raised_to_one_second(idp):
    provider = ScriptedProvider([(200, {"id_token": idp.entra()})], interval=0)
    _tokens, sleeps, _prompts = await sign_in(ENTRA, provider)
    assert sleeps == [1.0]


async def test_a_missing_interval_defaults_to_five_seconds(idp):
    provider = ScriptedProvider([(200, {"id_token": idp.entra()})])
    del provider.start["interval"]
    _tokens, sleeps, _prompts = await sign_in(ENTRA, provider)
    assert sleeps == [5]


@pytest.mark.parametrize(
    "start",
    [
        {"interval": None},
        {"interval": "soon"},
        {"interval": True},
        {"expires_in": None},
        {"expires_in": "later"},
        {"verification_uri": None},  # and no verification_url either
    ],
)
async def test_a_malformed_start_answer_is_an_error_before_any_polling(start):
    provider = CountingProvider([], **start)
    with pytest.raises(SignInError):
        await sign_in(ENTRA, provider)
    assert provider.requests == 1  # the start request only


def test_a_symlinked_cache_is_refused(tmp_path):
    target = tmp_path / "elsewhere.json"
    target.write_text('{"leaders": {}}', encoding="utf-8")
    link = tmp_path / "credentials.json"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted")
    with pytest.raises(CredentialsFileError, match="symbolic link") as excinfo:
        CredentialStore(link).load("x")
    assert "leaders" not in str(excinfo.value)


@pytest.mark.parametrize("mode", [0o777, 0o770, 0o707, 0o752, 0o720])
def test_a_writable_credentials_folder_is_refused(tmp_path, mode):
    with pytest.raises(CredentialsFileError, match="writable by others"):
        check_folder(mode, 1000, 1000, tmp_path)


def test_a_foreign_or_private_credentials_folder(tmp_path):
    with pytest.raises(CredentialsFileError, match="owned by another user"):
        check_folder(0o700, 1001, 1000, tmp_path)
    check_folder(0o700, 1000, 1000, tmp_path)
    check_folder(0o755, 1000, 1000, tmp_path)  # readable is fine; only writable is not


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_an_existing_loose_credentials_folder_is_refused(tmp_path):
    folder = tmp_path / "config"
    folder.mkdir()
    os.chmod(folder, 0o777)
    store = CredentialStore(folder / "credentials.json")
    with pytest.raises(CredentialsFileError, match="writable by others"):
        store.load("x")
    with pytest.raises(CredentialsFileError, match="writable by others"):
        store.save(sign_in_record())


@pytest.mark.skipif(os.name == "nt", reason="POSIX ownership")
def test_the_owner_check_uses_the_files_uid(tmp_path, monkeypatch):
    path = tmp_path / "credentials.json"
    store = CredentialStore(path)
    store.save(sign_in_record())
    monkeypatch.setattr(os, "getuid", lambda: path.stat().st_uid + 1)
    with pytest.raises(CredentialsFileError, match="owned by another user"):
        store.load("https://leader.example.org")


def write_cache(path, entry, key="https://leader.example.org"):
    path.write_text(json.dumps({"leaders": {key: entry}}), encoding="utf-8")


def good_entry(**overrides):
    return {**asdict(sign_in_record()), **overrides}


@pytest.mark.parametrize(
    "overrides",
    [
        {"leader": "https://other.example.org"},
        {"id_token": 5},
        {"refresh_token": ["x"]},
        {"client_id": None},
        {"extra": "field"},
    ],
)
def test_a_malformed_per_leader_entry_is_refused(tmp_path, overrides):
    path = tmp_path / "credentials.json"
    write_cache(path, good_entry(**overrides))
    with pytest.raises(CredentialsFileError, match="corrupted") as excinfo:
        CredentialStore(path).load("https://leader.example.org")
    assert "id-token-secret" not in str(excinfo.value)


def test_a_null_entry_is_refused(tmp_path):
    path = tmp_path / "credentials.json"
    write_cache(path, None)
    with pytest.raises(CredentialsFileError, match="corrupted"):
        CredentialStore(path).load("https://leader.example.org")


def test_an_entry_missing_a_field_is_refused(tmp_path):
    entry = good_entry()
    del entry["token_endpoint"]
    path = tmp_path / "credentials.json"
    write_cache(path, entry)
    with pytest.raises(CredentialsFileError, match="corrupted"):
        CredentialStore(path).load("https://leader.example.org")


def test_an_entry_for_another_leader_is_not_looked_at(tmp_path):
    path = tmp_path / "credentials.json"
    write_cache(path, good_entry())
    assert CredentialStore(path).load("https://nobody.example.org") is None


@pytest.mark.parametrize("status", [400, 401])
async def test_invalid_grant_asks_for_a_new_login(status):
    provider = ScriptedProvider([(status, {"error": "invalid_grant"})])
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
        with pytest.raises(SignInError, match="run `swarmscribe-admin login`"):
            await refresh_tokens(
                http,
                token_endpoint=ENTRA.token_endpoint,
                client_id="c",
                refresh_token="r",
                provider="entra",
            )


@pytest.mark.parametrize(
    "status, body",
    [
        (503, {}),
        (500, {"error": "server_error"}),
        (400, {"error": "temporarily_unavailable"}),
        (502, {"error": "invalid_grant"}),
    ],
)
async def test_a_provider_outage_says_try_again_not_log_in_again(status, body):
    provider = ScriptedProvider([(status, body)])
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
        with pytest.raises(SignInError, match="unavailable") as excinfo:
            await refresh_tokens(
                http,
                token_endpoint=ENTRA.token_endpoint,
                client_id="c",
                refresh_token="r",
                provider="entra",
            )
    assert "login" not in str(excinfo.value)


async def test_another_refusal_names_the_error_but_not_the_token():
    provider = ScriptedProvider([(400, {"error": "invalid_client"})])
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
        with pytest.raises(SignInError, match="invalid_client") as excinfo:
            await refresh_tokens(
                http,
                token_endpoint=ENTRA.token_endpoint,
                client_id="c",
                refresh_token="r-9",
                provider="entra",
            )
    assert "r-9" not in str(excinfo.value)
