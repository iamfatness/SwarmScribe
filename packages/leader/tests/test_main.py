from swarmscribe_leader import main as entry


def test_serve_refuses_an_unmigrated_database(monkeypatch, capsys):
    monkeypatch.setenv("SWARMSCRIBE_DATABASE_URL", "postgresql://u:p@127.0.0.1:1/none")
    monkeypatch.setenv("SWARMSCRIBE_PUBLIC_URL", "http://leader")
    monkeypatch.setenv("SWARMSCRIBE_LINK_KEY", "k" * 32)

    async def not_current(_settings):
        return False

    monkeypatch.setattr(entry, "_database_is_current", not_current)
    assert entry.main(["serve"]) == 2
    assert "migrate" in capsys.readouterr().err


def test_serve_runs_uvicorn_without_an_access_log(monkeypatch):
    # File-link URLs carry signed tokens; the access log would write them to disk.
    import uvicorn

    monkeypatch.setenv("SWARMSCRIBE_DATABASE_URL", "postgresql://u:p@127.0.0.1:1/none")
    monkeypatch.setenv("SWARMSCRIBE_PUBLIC_URL", "http://leader")
    monkeypatch.setenv("SWARMSCRIBE_LINK_KEY", "k" * 32)

    async def current(_settings):
        return True

    calls = []
    monkeypatch.setattr(entry, "_database_is_current", current)
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append(kwargs))
    assert entry.main(["serve", "--host", "127.0.0.1", "--port", "9123"]) == 0
    assert len(calls) == 1
    assert calls[0]["access_log"] is False
    assert calls[0]["host"] == "127.0.0.1"
    assert calls[0]["port"] == 9123


def test_migrate_upgrades_and_reports_the_revision(monkeypatch, capsys, migrated_database_url):
    monkeypatch.setenv("SWARMSCRIBE_DATABASE_URL", migrated_database_url)
    monkeypatch.setenv("SWARMSCRIBE_PUBLIC_URL", "http://leader")
    monkeypatch.setenv("SWARMSCRIBE_LINK_KEY", "k" * 32)
    assert entry.main(["migrate"]) == 0
    assert "0001" in capsys.readouterr().out
