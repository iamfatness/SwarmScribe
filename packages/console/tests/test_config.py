import base64

import pytest
from pydantic import ValidationError
from swarmscribe_console.config import Settings, decode_key

KEY = base64.urlsafe_b64encode(bytes(range(32))).rstrip(b"=").decode()
TENANT = "0f0e0d0c-0b0a-4908-8706-050403020100"


def settings(**overrides) -> Settings:
    values = {
        "database_url": "postgresql://u:p@127.0.0.1:1/none",
        "public_url": "https://console.example.org",
        "key": KEY,
        "entra_tenant_id": TENANT,
        "entra_client_id": "entra-client",
        "entra_client_secret": "entra-secret-value",
    }
    values.update(overrides)
    return Settings(**values)


def test_the_key_is_32_bytes_of_url_safe_base64():
    assert decode_key(KEY) == bytes(range(32))
    assert decode_key(KEY + "=") == bytes(range(32))
    assert decode_key(f"  {KEY}\n") == bytes(range(32))


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "short",
        base64.urlsafe_b64encode(bytes(31)).decode(),
        base64.urlsafe_b64encode(bytes(33)).decode(),
        "!" * 43,
        KEY[:-1] + "*",
    ],
    ids=["empty", "short", "31-bytes", "33-bytes", "not-base64", "one-bad-character"],
)
def test_any_other_key_is_refused(bad):
    with pytest.raises(ValueError, match="32 random bytes"):
        decode_key(bad)


def test_defaults_are_the_spec_values():
    s = settings()
    assert (s.session_lifetime_seconds, s.session_idle_seconds) == (8 * 3600, 3600)
    assert s.login_attempt_seconds == 600
    assert s.key_bytes() == bytes(range(32))


@pytest.mark.parametrize(
    ("given", "kept"),
    [
        ("https://console.example.org", "https://console.example.org"),
        ("https://Console.Example.org/", "https://console.example.org"),
        ("https://console.example.org:8443", "https://console.example.org:8443"),
        ("https://console.example.org:443", "https://console.example.org"),
        ("https://Console.Example.org:443/", "https://console.example.org"),
        ("http://localhost:80", "http://localhost"),
        ("http://localhost:8000", "http://localhost:8000"),
        ("http://127.0.0.1:8000", "http://127.0.0.1:8000"),
        ("http://[::1]:8000", "http://[::1]:8000"),
    ],
)
def test_the_public_url_is_an_https_origin(given, kept):
    assert settings(public_url=given).public_url == kept


@pytest.mark.parametrize(
    "bad",
    [
        "http://console.example.org",
        "console.example.org",
        "https://console.example.org/console",
        "https://console.example.org/?x=1",
        "https://user:pw@console.example.org",
        "ftp://console.example.org",
        "https://console.example.org:notaport",
        "https://console.example.org:0",
        "https://console.example.org.",
        "https://console.example.org.:8443",
        "https://café.example.org",
        "https://console.example.Korg",
    ],
)
def test_other_public_urls_are_refused(bad):
    with pytest.raises(ValidationError):
        settings(public_url=bad)


def test_a_sign_in_provider_is_required():
    with pytest.raises(ValidationError, match="Entra ID or Google"):
        settings(entra_tenant_id=None, entra_client_id=None, entra_client_secret=None)


@pytest.mark.parametrize("missing", ["entra_tenant_id", "entra_client_id", "entra_client_secret"])
def test_entra_needs_tenant_client_and_secret_together(missing):
    with pytest.raises(ValidationError, match="set together"):
        settings(**{missing: None})


def test_google_needs_its_client_secret():
    with pytest.raises(ValidationError, match="set together"):
        settings(google_client_id="google-client")
    s = settings(google_client_id="google-client", google_client_secret="google-secret")
    assert s.google_client_id == "google-client"


def test_google_options_need_google_sign_in():
    with pytest.raises(ValidationError, match="needs google_client_id"):
        settings(google_hosted_domain="example.org")


def test_the_tenant_is_a_guid():
    with pytest.raises(ValidationError, match="GUID"):
        settings(entra_tenant_id="example.onmicrosoft.com")


def test_idle_timeout_cannot_exceed_the_lifetime():
    with pytest.raises(ValidationError, match="session_idle_seconds"):
        settings(session_idle_seconds=9 * 3600)


def test_secrets_are_not_echoed():
    with pytest.raises(ValidationError) as raised:
        settings(key="not-a-key-but-a-secret-value")
    assert "not-a-key-but-a-secret-value" not in str(raised.value)
    s = settings()
    assert "entra-secret-value" not in repr(s)
    assert KEY not in repr(s)


@pytest.mark.parametrize("empty", ["", "   ", "\n"])
def test_an_empty_database_url_is_refused_at_startup(empty):
    with pytest.raises(ValidationError, match="database_url is empty"):
        settings(database_url=empty)
