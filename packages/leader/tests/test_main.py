import pytest
from swarmscribe_leader import main as entry
from swarmscribe_leader.db.migrate import head_revision


@pytest.fixture
def environment(monkeypatch):
    monkeypatch.setenv("SWARMSCRIBE_DATABASE_URL", "postgresql://u:p@127.0.0.1:1/none")
    monkeypatch.setenv("SWARMSCRIBE_PUBLIC_URL", "http://leader")
    monkeypatch.setenv("SWARMSCRIBE_LINK_KEY", "k" * 32)


def at_revision(monkeypatch, revision):
    async def fake(_engine):
        return revision

    monkeypatch.setattr(entry, "current_revision", fake)


def test_serve_refuses_an_unreachable_database(environment, capsys):
    assert entry.main(["serve"]) == 2
    err = capsys.readouterr().err
    assert "cannot connect to the database: " in err
    assert "migrate" not in err


def test_serve_refuses_a_database_behind_this_leader(environment, monkeypatch, capsys):
    at_revision(monkeypatch, "0001")
    assert entry.main(["serve"]) == 2
    err = capsys.readouterr().err
    assert f"database is at revision 0001, expected {head_revision()}" in err
    assert "run `swarmscribe-leader migrate`" in err


def test_serve_refuses_a_database_without_the_schema(environment, monkeypatch, capsys):
    at_revision(monkeypatch, None)
    assert entry.main(["serve"]) == 2
    assert "run `swarmscribe-leader migrate`" in capsys.readouterr().err


def test_serve_refuses_a_database_ahead_of_this_leader(environment, monkeypatch, capsys):
    at_revision(monkeypatch, "9999")
    assert entry.main(["serve"]) == 2
    err = capsys.readouterr().err
    assert "database is ahead of this leader" in err
    assert "9999" in err
    assert "run `swarmscribe-leader migrate`" not in err


def test_serve_runs_uvicorn_without_an_access_log(environment, monkeypatch):
    # File-link URLs carry signed tokens; the access log would write them to disk.
    import uvicorn

    at_revision(monkeypatch, head_revision())
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append(kwargs))
    monkeypatch.setattr(entry.logging.config, "dictConfig", lambda config: None)
    assert entry.main(["serve", "--host", "127.0.0.1", "--port", "9123"]) == 0
    assert len(calls) == 1
    assert calls[0]["access_log"] is False
    assert calls[0]["host"] == "127.0.0.1"
    assert calls[0]["port"] == 9123


def test_serve_configures_logging(environment, monkeypatch):
    import uvicorn

    at_revision(monkeypatch, head_revision())
    configs = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: None)
    monkeypatch.setattr(entry.logging.config, "dictConfig", configs.append)
    assert entry.main(["serve"]) == 0
    (config,) = configs
    assert config["root"]["level"] == "INFO"
    (formatter,) = config["formatters"].values()
    for part in ("%(asctime)s", "%(levelname)s", "%(name)s", "%(message)s"):
        assert part in formatter["format"]


@pytest.mark.parametrize("command", ["serve", "migrate"])
def test_an_invalid_link_key_is_named_but_never_echoed(monkeypatch, capsys, command):
    monkeypatch.setenv("SWARMSCRIBE_DATABASE_URL", "postgresql://user:hunter2-db@db/none")
    monkeypatch.setenv("SWARMSCRIBE_PUBLIC_URL", "http://leader")
    monkeypatch.setenv("SWARMSCRIBE_LINK_KEY", "tooShortSecretValue")
    assert entry.main([command]) == 2
    captured = capsys.readouterr()
    assert "link_key" in captured.err
    for secret in ("tooShortSecretValue", "hunter2-db"):
        assert secret not in captured.err + captured.out


def test_migrate_upgrades_and_reports_the_revision(monkeypatch, capsys, migrated_database_url):
    monkeypatch.setenv("SWARMSCRIBE_DATABASE_URL", migrated_database_url)
    monkeypatch.setenv("SWARMSCRIBE_PUBLIC_URL", "http://leader")
    monkeypatch.setenv("SWARMSCRIBE_LINK_KEY", "k" * 32)
    assert entry.main(["migrate"]) == 0
    assert head_revision() in capsys.readouterr().out
