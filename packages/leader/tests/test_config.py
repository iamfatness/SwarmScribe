import json
import os
import re

import pytest
from pydantic import ValidationError
from swarmscribe_leader.config import Settings
from swarmscribe_leader.db.session import to_async_url

BASE = {
    "database_url": "postgresql://u:p@db:5432/swarm",
    "public_url": "https://leader.example/",
    "link_key": "k" * 32,
}


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in list(os.environ):
        if name.startswith("SWARMSCRIBE_") and name != "SWARMSCRIBE_TEST_DATABASE_URL":
            monkeypatch.delenv(name)


def test_defaults():
    settings = Settings(**BASE)
    assert settings.lease_seconds == 120
    assert settings.heartbeat_seconds == 30
    assert settings.max_attempts == 3
    assert settings.claim_retry_after == 10
    assert settings.download_link_ttl_seconds == 1800
    assert settings.upload_link_ttl_seconds == 7200


def test_public_url_loses_its_trailing_slash():
    assert Settings(**BASE).public_url == "https://leader.example"


def test_reads_the_environment(monkeypatch):
    monkeypatch.setenv("SWARMSCRIBE_DATABASE_URL", "postgresql://x/y")
    monkeypatch.setenv("SWARMSCRIBE_PUBLIC_URL", "https://l")
    monkeypatch.setenv("SWARMSCRIBE_LINK_KEY", "z" * 40)
    monkeypatch.setenv("SWARMSCRIBE_LEASE_SECONDS", "300")
    settings = Settings()
    assert settings.database_url.get_secret_value() == "postgresql://x/y"
    assert settings.lease_seconds == 300


def test_secrets_are_not_shown_in_the_settings_repr():
    settings = Settings(**{**BASE, "link_key": "s3cr3t" * 8})
    assert "s3cr3t" not in repr(settings)
    assert "u:p@db" not in repr(settings)
    assert settings.link_key.get_secret_value() == "s3cr3t" * 8


def test_followers_are_not_gone_before_their_lease_could_expire():
    with pytest.raises(ValidationError, match="follower_gone_after_seconds"):
        Settings(**BASE, lease_seconds=600, follower_gone_after_seconds=600)
    assert Settings(**BASE, lease_seconds=120, follower_gone_after_seconds=121)


@pytest.mark.parametrize(
    "url", ["leader.example", "/v1", "ftp://leader.example", "https://", "http:/leader"]
)
def test_public_url_must_be_an_absolute_http_url(url):
    with pytest.raises(ValidationError, match="public_url"):
        Settings(**{**BASE, "public_url": url})


def test_database_url_is_required():
    with pytest.raises(ValidationError):
        Settings(public_url="https://l", link_key="k" * 32)


def test_link_key_must_be_at_least_32_characters():
    with pytest.raises(ValidationError):
        Settings(**{**BASE, "link_key": "short"})


def test_heartbeat_must_be_shorter_than_the_lease():
    with pytest.raises(ValidationError, match="heartbeat"):
        Settings(**BASE, lease_seconds=30, heartbeat_seconds=30)


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("postgresql://u:p@h/d", "postgresql+asyncpg://u:p@h/d"),
        ("postgres://u:p@h/d", "postgresql+asyncpg://u:p@h/d"),
        ("postgresql+asyncpg://u:p@h/d", "postgresql+asyncpg://u:p@h/d"),
    ],
)
def test_to_async_url(given, expected):
    assert to_async_url(given) == expected


TENANT = "0F0E0D0C-0B0A-4908-8706-050403020100"
SERVICE_ACCOUNT = json.dumps(
    {
        "type": "service_account",
        "client_email": "group-reader@project-1.iam.gserviceaccount.com",
        "private_key": "-----BEGIN PRIVATE KEY-----\nnot-a-real-key\n-----END PRIVATE KEY-----\n",
    }
)


def test_no_sign_in_provider_is_configured_by_default():
    settings = Settings(**BASE)
    assert (settings.entra_client_id, settings.google_client_id) == (None, None)
    assert settings.role_admin_emails == ()
    assert settings.role_cache_seconds == 300


def test_both_providers_and_role_lists_from_the_environment(monkeypatch):
    for name, value in {
        "SWARMSCRIBE_DATABASE_URL": "postgresql://x/y",
        "SWARMSCRIBE_PUBLIC_URL": "https://l",
        "SWARMSCRIBE_LINK_KEY": "z" * 40,
        "SWARMSCRIBE_ENTRA_TENANT_ID": TENANT,
        "SWARMSCRIBE_ENTRA_CLIENT_ID": "entra-client",
        "SWARMSCRIBE_ENTRA_CLIENT_SECRET": "entra-secret",
        "SWARMSCRIBE_GOOGLE_CLIENT_ID": "google-client",
        "SWARMSCRIBE_GOOGLE_CLIENT_SECRET": "google-secret",
        "SWARMSCRIBE_GOOGLE_HOSTED_DOMAIN": "Example.ORG",
        "SWARMSCRIBE_ROLE_ADMIN_ENTRA_GROUPS": "A1A1A1A1-0000-4000-8000-000000000003, b2",
        "SWARMSCRIBE_ROLE_VIEWER_EMAILS": "One@Example.org,two@example.org ,",
        "SWARMSCRIBE_ROLE_OPERATOR_DOMAINS": "@Example.org",
    }.items():
        monkeypatch.setenv(name, value)
    settings = Settings()
    assert settings.entra_tenant_id == TENANT.lower()
    assert settings.google_hosted_domain == "example.org"
    assert settings.role_admin_entra_groups == ("a1a1a1a1-0000-4000-8000-000000000003", "b2")
    assert settings.role_viewer_emails == ("one@example.org", "two@example.org")
    assert settings.role_operator_domains == ("example.org",)
    assert settings.entra_client_secret.get_secret_value() == "entra-secret"


@pytest.mark.parametrize(
    "values, problem",
    [
        ({"entra_client_id": "c"}, "entra_client_id and entra_tenant_id must be set together"),
        ({"entra_tenant_id": TENANT}, "entra_client_id and entra_tenant_id must be set together"),
        (
            {"entra_client_id": "c", "entra_tenant_id": "contoso.example"},
            "tenant's ID (a GUID)",
        ),
        ({"entra_client_secret": "s"}, "entra_client_secret needs entra_client_id"),
        ({"google_client_id": "g"}, "google_client_secret is required"),
        ({"google_hosted_domain": "example.org"}, "google_hosted_domain needs google_client_id"),
        ({"role_admin_entra_groups": "g1"}, "role_admin_entra_groups needs Entra ID sign-in"),
        (
            {
                "google_client_id": "g",
                "google_client_secret": "s",
                "role_viewer_google_groups": "x",
            },
            "role_viewer_google_groups needs google_service_account",
        ),
        ({"role_operator_emails": "a@example.org"}, "role_operator_emails applies to Google"),
    ],
)
def test_half_configured_sign_in_is_refused(values, problem):
    with pytest.raises(ValidationError, match=re.escape(problem)):
        Settings(**BASE, **values)


def test_the_service_account_key_can_be_json_or_a_file(tmp_path):
    google = {"google_client_id": "g", "google_client_secret": "s"}
    inline = Settings(**BASE, **google, google_service_account=SERVICE_ACCOUNT)
    key = inline.google_service_account_key()
    assert key["client_email"] == "group-reader@project-1.iam.gserviceaccount.com"
    assert key["token_uri"] == "https://oauth2.googleapis.com/token"
    path = tmp_path / "service-account.json"
    path.write_text(SERVICE_ACCOUNT, encoding="utf-8")
    from_file = Settings(**BASE, **google, google_service_account=str(path))
    assert from_file.google_service_account_key() == key


def test_an_unusable_service_account_is_refused_without_echoing_it():
    secret_text = '{"client_email": "x", "private_key": ""} plus-secret-material'
    with pytest.raises(ValidationError) as excinfo:
        Settings(
            **BASE,
            google_client_id="g",
            google_client_secret="s",
            google_service_account=secret_text,
        )
    rendered = str(excinfo.value.errors(include_input=False, include_url=False))
    assert "google_service_account must be a service-account JSON key" in rendered
    assert "plus-secret-material" not in rendered


def test_sign_in_secrets_are_not_shown_in_the_settings_repr():
    settings = Settings(
        **BASE,
        entra_tenant_id=TENANT,
        entra_client_id="c",
        entra_client_secret="entra-hidden",
        google_client_id="g",
        google_client_secret="google-hidden",
        google_service_account=SERVICE_ACCOUNT,
    )
    shown = repr(settings)
    assert "entra-hidden" not in shown
    assert "google-hidden" not in shown
    assert "not-a-real-key" not in shown


GOOGLE = {"google_client_id": "g", "google_client_secret": "s"}


def test_a_blank_google_secret_does_not_satisfy_google_sign_in():
    with pytest.raises(ValidationError, match="google_client_secret is required"):
        Settings(**BASE, google_client_id="g", google_client_secret="")


def test_blank_secrets_count_as_unset():
    assert Settings(**BASE, entra_client_secret="").entra_client_secret is None
    settings = Settings(**BASE, **GOOGLE, google_service_account="  ")
    assert settings.google_service_account is None
    assert settings.google_service_account_key() is None


@pytest.mark.parametrize(
    "setting, entry",
    [
        ("role_admin_domains", "*.example.org"),
        ("role_admin_domains", "a@b.org"),
        ("role_admin_domains", "exa mple.org"),
        ("role_admin_domains", "localhost"),
        ("role_viewer_emails", "no-at-sign.example.org"),
        ("role_viewer_emails", "a@b@example.org"),
        ("role_viewer_emails", "@example.org"),
        ("role_viewer_emails", "a@localhost"),
        ("role_admin_domains", "straße.example"),
        ("role_viewer_emails", "x@straße.example"),
        ("role_viewer_emails", "é@example.org"),
    ],
)
def test_malformed_role_entries_are_refused_naming_setting_and_position(setting, entry):
    values = {setting: f"fine.example.org,{entry}"}
    if setting.endswith("emails"):
        values[setting] = f"ok@example.org,{entry}"
    with pytest.raises(ValidationError) as excinfo:
        Settings(**BASE, **GOOGLE, **values)
    message = str(excinfo.value.errors(include_input=False, include_url=False))
    assert setting in message
    assert "entry 2" in message


def test_hosted_domain_loses_one_leading_at_sign():
    settings = Settings(**BASE, **GOOGLE, google_hosted_domain="@Example.org")
    assert settings.google_hosted_domain == "example.org"


def test_the_orphaned_google_setting_is_named():
    with pytest.raises(ValidationError, match="google_service_account needs google_client_id"):
        Settings(**BASE, google_service_account=SERVICE_ACCOUNT)


def test_empty_role_lists_are_empty(monkeypatch):
    monkeypatch.setenv("SWARMSCRIBE_DATABASE_URL", "postgresql://x/y")
    monkeypatch.setenv("SWARMSCRIBE_PUBLIC_URL", "https://l")
    monkeypatch.setenv("SWARMSCRIBE_LINK_KEY", "z" * 40)
    monkeypatch.setenv("SWARMSCRIBE_ROLE_ADMIN_EMAILS", "")
    monkeypatch.setenv("SWARMSCRIBE_ROLE_VIEWER_DOMAINS", " , ")
    settings = Settings()
    assert settings.role_admin_emails == ()
    assert settings.role_viewer_domains == ()
    assert settings.role_operator_emails == ()


@pytest.mark.parametrize(
    "form",
    [
        TENANT,
        TENANT.replace("-", ""),
        "{" + TENANT + "}",
        "urn:uuid:" + TENANT,
    ],
)
def test_tenant_guid_forms_are_normalised(form):
    settings = Settings(**BASE, entra_client_id="c", entra_tenant_id=form)
    assert settings.entra_tenant_id == TENANT.lower()


def test_a_missing_service_account_file_is_refused_by_setting_name(tmp_path):
    missing = tmp_path / "nope.json"
    with pytest.raises(ValidationError) as excinfo:
        Settings(**BASE, **GOOGLE, google_service_account=str(missing))
    message = str(excinfo.value.errors(include_input=False, include_url=False))
    assert "google_service_account must be a service-account JSON key" in message


def test_a_service_account_file_with_bad_contents_does_not_leak_them(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("file-secret-material", encoding="utf-8")
    with pytest.raises(ValidationError) as excinfo:
        Settings(**BASE, **GOOGLE, google_service_account=str(path))
    message = str(excinfo.value.errors(include_input=False, include_url=False))
    assert "google_service_account" in message
    assert "file-secret-material" not in message
