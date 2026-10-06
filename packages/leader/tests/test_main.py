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


@pytest.mark.parametrize("command", ["serve", "migrate"])
def test_a_bad_sign_in_setting_is_named_but_no_secret_is_echoed(monkeypatch, capsys, command):
    monkeypatch.setenv("SWARMSCRIBE_DATABASE_URL", "postgresql://user:hunter2-db@db/none")
    monkeypatch.setenv("SWARMSCRIBE_PUBLIC_URL", "http://leader")
    monkeypatch.setenv("SWARMSCRIBE_LINK_KEY", "k" * 32)
    monkeypatch.setenv("SWARMSCRIBE_GOOGLE_CLIENT_ID", "g")
    monkeypatch.setenv("SWARMSCRIBE_GOOGLE_CLIENT_SECRET", "google-secret-value")
    monkeypatch.setenv("SWARMSCRIBE_GOOGLE_SERVICE_ACCOUNT", "{not json} service-account-secret")
    assert entry.main([command]) == 2
    captured = capsys.readouterr()
    assert "google_service_account" in captured.err
    shown = captured.err + captured.out
    for secret in ("google-secret-value", "service-account-secret"):
        assert secret not in shown


@pytest.mark.parametrize("command", ["serve", "migrate"])
def test_an_orphaned_entra_secret_is_named_but_not_echoed(monkeypatch, capsys, command):
    monkeypatch.setenv("SWARMSCRIBE_DATABASE_URL", "postgresql://user:hunter2-db@db/none")
    monkeypatch.setenv("SWARMSCRIBE_PUBLIC_URL", "http://leader")
    monkeypatch.setenv("SWARMSCRIBE_LINK_KEY", "k" * 32)
    monkeypatch.setenv("SWARMSCRIBE_ENTRA_CLIENT_SECRET", "entra-secret-value")
    assert entry.main([command]) == 2
    captured = capsys.readouterr()
    assert "entra_client_secret" in captured.err
    assert "entra-secret-value" not in captured.err + captured.out


def test_migrate_upgrades_and_reports_the_revision(monkeypatch, capsys, migrated_database_url):
    monkeypatch.setenv("SWARMSCRIBE_DATABASE_URL", migrated_database_url)
    monkeypatch.setenv("SWARMSCRIBE_PUBLIC_URL", "http://leader")
    monkeypatch.setenv("SWARMSCRIBE_LINK_KEY", "k" * 32)
    assert entry.main(["migrate"]) == 0
    assert head_revision() in capsys.readouterr().out


def test_serve_gives_requests_in_hand_ten_seconds_at_a_stop(environment, monkeypatch):
    # Without a limit uvicorn waits for them for ever: a request waiting for a database that
    # does not answer would hold the stop until the container is killed.
    import uvicorn

    at_revision(monkeypatch, head_revision())
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append(kwargs))
    monkeypatch.setattr(entry.logging.config, "dictConfig", lambda config: None)
    assert entry.main(["serve"]) == 0
    assert entry.REQUEST_DRAIN_SECONDS == 10
    assert calls[0]["timeout_graceful_shutdown"] == 10


@pytest.mark.parametrize("command", ["serve", "migrate"])
@pytest.mark.parametrize(
    "url",
    [
        "hunter2-db is not a URL",
        "postgresql://user:hunter2-db@db:not-a-port/leader",
        "mysql://user:hunter2-db@db/leader",
    ],
)
def test_a_malformed_database_url_is_named_but_never_echoed(monkeypatch, capsys, command, url):
    monkeypatch.setenv("SWARMSCRIBE_DATABASE_URL", url)
    monkeypatch.setenv("SWARMSCRIBE_PUBLIC_URL", "http://leader")
    monkeypatch.setenv("SWARMSCRIBE_LINK_KEY", "k" * 32)
    assert entry.main([command]) == 2
    captured = capsys.readouterr()
    assert "database_url: " in captured.err
    assert "postgresql://" in captured.err  # says what one looks like
    assert len(captured.err.splitlines()) == 2
    assert "hunter2-db" not in captured.err + captured.out


def test_migrate_says_in_one_line_that_the_database_cannot_be_reached(monkeypatch, capsys):
    monkeypatch.setenv("SWARMSCRIBE_DATABASE_URL", "postgresql://user:hunter2-db@127.0.0.1:1/none")
    monkeypatch.setenv("SWARMSCRIBE_PUBLIC_URL", "http://leader")
    monkeypatch.setenv("SWARMSCRIBE_LINK_KEY", "k" * 32)
    assert entry.main(["migrate"]) == 2  # as `serve` does; it used to raise (a traceback, 1)
    captured = capsys.readouterr()
    assert captured.err.startswith("error: cannot connect to the database: ")
    assert len(captured.err.splitlines()) == 1
    assert "hunter2-db" not in captured.err + captured.out
    assert "database is at revision" not in captured.out


def test_a_migration_that_fails_is_one_error_without_the_password(monkeypatch, capsys):
    url = "postgresql://user:hunter2-db@db/none"
    monkeypatch.setenv("SWARMSCRIBE_DATABASE_URL", url)
    monkeypatch.setenv("SWARMSCRIBE_PUBLIC_URL", "http://leader")
    monkeypatch.setenv("SWARMSCRIBE_LINK_KEY", "k" * 32)
    at_revision(monkeypatch, "0001")

    def fails(database_url):
        raise RuntimeError(f"column jobs.nope does not exist\n[SQL: ...] on {database_url}")

    monkeypatch.setattr(entry, "upgrade", fails)
    assert entry.main(["migrate"]) == 1
    captured = capsys.readouterr()
    assert captured.err.startswith(
        "error: the migration failed: RuntimeError: column jobs.nope does not exist"
    )
    assert "[SQL: ...]" in captured.err  # the statement is what the reader needs
    assert "Traceback" not in captured.err
    assert "hunter2-db" not in captured.err + captured.out
    assert "database is at revision" not in captured.out


def test_serve_never_echoes_the_database_password_in_a_connection_error(
    environment, monkeypatch, capsys
):
    monkeypatch.setenv("SWARMSCRIBE_DATABASE_URL", "postgresql://user:hunter2-db@db/none")

    async def fails(_engine):
        raise OSError("cannot reach postgresql://user:hunter2-db@db/none (hunter2-db)")

    monkeypatch.setattr(entry, "current_revision", fails)
    assert entry.main(["serve"]) == 2
    err = capsys.readouterr().err
    assert "cannot connect to the database: OSError: cannot reach" in err
    assert "hunter2-db" not in err
