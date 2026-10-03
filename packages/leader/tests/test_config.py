import os

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
    assert settings.database_url == "postgresql://x/y"
    assert settings.lease_seconds == 300


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
