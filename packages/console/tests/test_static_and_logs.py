import logging
import os

import httpx
import pytest
from console_testkit import (
    CREDENTIAL,
    ENTRA_SECRET,
    GROUPS,
    PUBLIC_URL,
    all_rows_text,
    console_env,
)
from pydantic import ValidationError
from sqlalchemy import select
from swarmscribe_console.app import create_app
from swarmscribe_console.db.models import AuditEntry, LoginAttempt
from swarmscribe_console.main import main
from swarmscribe_console.poller import poll_due_leaders
from swarmscribe_console.sessions import SESSION_COOKIE
from swarmscribe_leader.clock import utcnow

INDEX = "<!doctype html><title>SwarmScribe console</title>"


@pytest.fixture
def web(tmp_path):
    folder = tmp_path / "web"
    (folder / "assets").mkdir(parents=True)
    (folder / "index.html").write_text(INDEX, encoding="utf-8")
    (folder / "assets" / "app.js").write_text("console.log('console')", encoding="utf-8")
    (folder / "assets" / "app-Dk3f9aB2.js").write_text("console.log('hashed')", encoding="utf-8")
    (folder / "assets" / "app.css").write_text("body{margin:0}", encoding="utf-8")
    (folder / "assets" / "logo.svg").write_text(
        "<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8"
    )
    (tmp_path / "secret.txt").write_text("not for the web", encoding="utf-8")
    return folder


@pytest.fixture
async def web_client(engine, make_settings, idp, fake_leader, web):
    application = create_app(
        make_settings(static_dir=str(web)),
        background=False,
        fetch=idp.fetch,
        idp_transport=idp.transport,
        leader_transport=fake_leader.transport,
    )
    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url=PUBLIC_URL
        ) as made:
            yield made


# --- static files -----------------------------------------------------------------------


async def test_the_web_app_is_served_under_the_csp(web_client):
    answer = await web_client.get("/")
    assert answer.status_code == 200
    assert answer.text == INDEX
    assert "script-src 'self'" in answer.headers["content-security-policy"]
    script = await web_client.get("/assets/app.js")
    assert script.status_code == 200
    assert "script-src 'self'" in script.headers["content-security-policy"]


@pytest.mark.parametrize(
    "path, kind",
    [
        ("/", "text/html"),
        ("/fleet", "text/html"),
        ("/assets/app.js", "javascript"),
        ("/assets/app.css", "text/css"),
        ("/assets/logo.svg", "image/svg+xml"),
        ("/assets/missing.js", None),
        ("/api/nothing-here", None),
        ("/auth/nothing-here", None),
    ],
)
async def test_every_static_answer_carries_the_security_headers(web_client, path, kind):
    answer = await web_client.get(path)
    headers = answer.headers
    assert "script-src 'self'" in headers["content-security-policy"]
    assert "default-src 'self'" in headers["content-security-policy"]
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer"
    assert "max-age" in headers["strict-transport-security"]
    if kind is not None:
        assert kind in headers["content-type"]


@pytest.mark.parametrize(
    "path, expected",
    [
        ("/", "no-cache"),
        ("/index.html", "no-cache"),
        ("/fleet", "no-cache"),  # the fallback is index.html
        ("/assets/app.js", "no-cache"),  # not hashed: re-check every time
        ("/assets/app.css", "no-cache"),
        ("/assets/app-Dk3f9aB2.js", "public, max-age=31536000, immutable"),
    ],
)
async def test_only_hashed_assets_are_cached_for_long(web_client, path, expected):
    answer = await web_client.get(path)
    assert answer.status_code == 200
    assert answer.headers["cache-control"] == expected


@pytest.mark.parametrize("path", ["/leaders/eu-1", "/fleet", "/leaders/eu-1/jobs"])
async def test_the_web_apps_own_routes_get_index_html(web_client, path):
    answer = await web_client.get(path)
    assert (answer.status_code, answer.text) == (200, INDEX)


@pytest.mark.parametrize(
    "path",
    [
        "/assets/missing.js",
        "/api/nothing-here",
        "/api/unknown/deeper",
        "/auth/nothing-here",
        "/auth/callback/x",
        "/..%2fsecret.txt",
        "/assets/..%2f..%2fsecret.txt",
    ],
)
async def test_missing_files_api_paths_and_escapes_are_not_index_html(web_client, path):
    answer = await web_client.get(path)
    assert answer.status_code == 404
    assert INDEX not in answer.text
    assert "not for the web" not in answer.text


async def test_an_unknown_api_path_gets_the_apis_json_404(web_client):
    answer = await web_client.get("/api/unknown")
    assert answer.status_code == 404
    assert INDEX not in answer.text
    assert answer.headers["content-type"].startswith("application/json")
    assert answer.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("path", ["/api", "/auth"])
async def test_the_bare_api_and_auth_paths_are_not_index_html(web_client, path):
    answer = await web_client.get(path)
    assert INDEX not in answer.text


HOSTILE = [
    "/..",
    "/../secret.txt",
    "/%2e%2e/secret.txt",
    "/%2e%2e%2fsecret.txt",
    "/assets/%2e%2e/%2e%2e/secret.txt",
    "/assets/..%5c..%5csecret.txt",
    "/..%5csecret.txt",
    "/%5c..%5csecret.txt",
    "/%2fetc/passwd",
    "//etc/passwd",
    "/C:/Windows/win.ini",
    "/C%3a%5cWindows%5cwin.ini",
    "/%00",
    "/index.html%00.txt",
    "/assets/app.js%00",
    "/" + "a" * 5000,
    "/assets/" + "a" * 5000 + ".js",
    "/" + "a/" * 2000,
]


@pytest.mark.parametrize("path", HOSTILE)
async def test_hostile_paths_never_leave_the_static_folder(web_client, path):
    answer = await web_client.get(path)
    assert "not for the web" not in answer.text
    assert "[fonts]" not in answer.text  # win.ini
    assert "root:" not in answer.text
    assert answer.status_code in (200, 400, 404)
    if answer.status_code == 200:
        assert answer.text == INDEX  # only ever the app itself
    assert "script-src 'self'" in answer.headers["content-security-policy"]


async def test_a_symlink_out_of_the_folder_is_not_served(web_client, web, tmp_path):
    link = web / "assets" / "escape.txt"
    try:
        os.symlink(tmp_path / "secret.txt", link)
    except (OSError, NotImplementedError):
        pytest.skip("this machine may not create symlinks")
    answer = await web_client.get("/assets/escape.txt")
    assert "not for the web" not in answer.text
    assert answer.status_code == 404


async def test_the_api_still_answers_beside_the_web_app(web_client):
    assert (await web_client.get("/api/session")).status_code == 401
    assert (await web_client.get("/auth/providers")).status_code == 200


def test_the_static_folder_must_hold_index_html(make_settings, tmp_path):
    with pytest.raises(ValidationError, match="index.html"):
        make_settings(static_dir=str(tmp_path))
    with pytest.raises(ValidationError, match="index.html"):
        make_settings(static_dir=str(tmp_path / "missing"))


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_static_folder_means_unset(make_settings, blank):
    assert make_settings(static_dir=blank).static_dir is None


async def test_without_a_static_folder_the_root_is_a_404(client):
    assert (await client.get("/")).status_code == 404


# --- no secret in the logs or the database ----------------------------------------------


async def test_no_secret_reaches_the_logs_or_the_database(
    app, client, idp, factory, fake_leader, engine, caplog
):
    caplog.set_level(logging.DEBUG)
    await factory.grant("admin", "all", "entra_group", GROUPS["admin"])
    await factory.console_admin("entra_group", GROUPS["admin"])

    started = await client.get("/auth/login", params={"provider": "entra"})
    callback = idp.authorize(started.headers["location"], groups=[GROUPS["admin"]])
    code = dict(httpx.URL(callback).params)["code"]
    assert (await client.get(callback)).status_code == 200
    session_id = client.cookies.get(SESSION_COOKIE)
    csrf = (await client.get("/api/session")).json()["csrf_token"]
    client.headers["X-CSRF-Token"] = csrf

    credential = "S" * 20 + "_-" + "c" * 21
    registered = await client.post(
        "/api/admin/leaders",
        json={
            "name": "eu-1",
            "base_url": "https://eu-1.leaders.example",
            "credential": credential,
        },
    )
    assert registered.status_code == 201
    state = app.state
    outcomes = await poll_due_leaders(
        state.engine,
        state.sessionmaker,
        state.leader_client,
        state.keys,
        now=utcnow(),
        config=state.poller_config,
    )
    assert outcomes == {"eu-1": "ok"}

    plaintext = "T" * 20 + "-_" + "k" * 21
    fake_leader.replies[("POST", "/v1/admin/tokens")] = (
        201,
        {
            "id": "99999999-8888-4777-8666-555555555555",
            "token": plaintext,
            "pool": "default",
            "expires_at": "2026-10-10T00:00:00Z",
            "max_uses": 1,
        },
        {},
    )
    assert (await client.post("/api/leaders/eu-1/tokens", json={})).status_code == 201
    assert (await client.get("/api/leaders/eu-1/status")).status_code == 200
    refused = await client.post(
        "/api/leaders/eu-1/tokens", json={}, headers={"X-CSRF-Token": "wrong"}
    )
    assert refused.status_code == 403
    rotated = "R" * 20 + "-_" + "r" * 21
    answer = await client.put("/api/admin/leaders/eu-1/credential", json={"credential": rotated})
    assert answer.status_code in (200, 204)
    assert (await client.post("/api/session/logout")).status_code == 204

    assert fake_leader.requests[0].headers["authorization"] == f"Console {credential}"
    secrets = [
        session_id,
        csrf,
        credential,
        rotated,
        plaintext,
        code,
        idp.exchanges[0]["code_verifier"],
        ENTRA_SECRET,
        *idp.issued,
    ]
    # The test browser's own httpx client logs its requests to the console (the callback's
    # URL carries the code); that is the browser, not the console, so it is left out. The
    # console's own clients (leaders, identity provider) are in.
    logged = "\n".join(
        record.getMessage()
        for record in caplog.records
        if not (record.name == "httpx" and PUBLIC_URL in record.getMessage())
    )
    assert "eu-1.leaders.example" in logged  # the console's own calls were captured
    stored = await all_rows_text(engine)
    for secret in secrets:
        assert secret not in logged
        assert secret not in stored
    # The state and nonce the person was sent away with are not logged either.
    params = dict(httpx.URL(started.headers["location"]).params)
    assert params["state"] not in logged
    assert params["state"] not in stored
    assert params["nonce"] not in logged


async def _signed_in(client, factory, role="admin"):
    await factory.grant(role, "all", "email", "person@example.org")
    await factory.console_admin("email", "person@example.org")
    client.headers["X-CSRF-Token"] = await factory.person(
        client, principals={"email:person@example.org"}
    )


@pytest.mark.parametrize(
    "path",
    [
        "/api/leaders/eu-1/tokens",
        "/api/leaders/eu-1/locations",
        "/api/admin/leaders",
        "/api/admin/grants",
    ],
)
async def test_an_unknown_field_name_is_never_echoed(
    client, factory, fake_leader, sessionmaker, caplog, path
):
    caplog.set_level(logging.DEBUG)
    await factory.leader("eu-1")
    await _signed_in(client, factory)
    key = "SECRETKEY-xyz"
    value = "SECRETVALUE-abc"
    answer = await client.post(path, json={key: value})
    assert answer.status_code == 422
    assert key not in answer.text
    assert value not in answer.text
    assert key not in caplog.text
    assert value not in caplog.text
    async with sessionmaker() as session:
        rows = (await session.scalars(select(AuditEntry))).all()
    audited = repr([(r.action, r.leader, r.target, r.outcome, r.detail, r.actor) for r in rows])
    assert key not in audited
    assert value not in audited
    assert fake_leader.requests == []


async def test_an_unhandled_error_in_the_proxy_logs_frames_and_never_the_text(
    client, factory, fake_leader, caplog
):
    caplog.set_level(logging.DEBUG)
    await factory.leader("eu-1")
    await _signed_in(client, factory)
    plaintext = "T" * 20 + "-_" + "k" * 21
    reply_body = "leader-reply-body-" + "B" * 20

    async def explode(_request):
        raise RuntimeError(f"{plaintext} {CREDENTIAL} {reply_body}")

    fake_leader.on_request = explode
    answer = await client.post("/api/leaders/eu-1/tokens", json={})
    assert answer.status_code == 502
    assert answer.json()["code"] == "bad_gateway"
    assert caplog.text.count("proxied leader call failed: RuntimeError") == 1
    assert "File " in caplog.text  # the traceback's frames
    for secret in (plaintext, CREDENTIAL, reply_body):
        assert secret not in caplog.text
        assert secret not in answer.text


async def test_an_unhandled_error_in_a_poll_step_logs_frames_and_never_the_text(
    app, factory, fake_leader, caplog
):
    caplog.set_level(logging.DEBUG)
    await factory.leader("eu-1")
    plaintext = "T" * 20 + "-_" + "k" * 21

    async def explode(_request):
        raise RuntimeError(f"{plaintext} {CREDENTIAL}")

    fake_leader.on_request = explode
    state = app.state
    outcomes = await poll_due_leaders(
        state.engine,
        state.sessionmaker,
        state.leader_client,
        state.keys,
        now=utcnow(),
        config=state.poller_config,
    )
    assert outcomes == {"eu-1": "error"}
    assert "polling leader eu-1 failed: RuntimeError" in caplog.text
    assert "File " in caplog.text
    for secret in (plaintext, CREDENTIAL):
        assert secret not in caplog.text


async def test_statement_parameters_are_never_logged(app, client, caplog):
    caplog.set_level(logging.INFO, logger="sqlalchemy.engine")
    started = await client.get("/auth/login", params={"provider": "entra"})
    assert started.status_code == 302
    params = dict(httpx.URL(started.headers["location"]).params)
    assert any("login_attempts" in r.getMessage() for r in caplog.records)  # logging is on
    async with app.state.sessionmaker() as session:
        attempt = (await session.scalars(select(LoginAttempt))).one()
        nonce, verifier = attempt.nonce, attempt.code_verifier
        session.expunge(attempt)
    assert nonce == params["nonce"]
    async with app.state.sessionmaker() as session:
        session.add(  # the same primary key again: the statement fails
            LoginAttempt(
                state_hash=attempt.state_hash,
                browser_hash=attempt.browser_hash,
                provider=attempt.provider,
                nonce=nonce,
                code_verifier=verifier,
                return_to=attempt.return_to,
                expires_at=attempt.expires_at,
            )
        )
        with pytest.raises(Exception) as failed:
            await session.commit()
    for secret in (nonce, verifier):
        assert secret not in caplog.text
        assert secret not in str(failed.value)
    assert app.state.engine.dialect.name == "postgresql"
    assert app.state.engine.sync_engine.hide_parameters is True


def test_serve_runs_without_an_access_log_and_with_quiet_http_clients(
    migrated_database_url, monkeypatch
):
    import logging.config

    import uvicorn

    console_env(monkeypatch, migrated_database_url)
    seen: dict = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: seen.update(kwargs))
    monkeypatch.setattr(logging.config, "dictConfig", lambda config: seen.update(logging=config))
    assert main(["serve", "--port", "8443"]) == 0
    assert seen["access_log"] is False
    assert seen["port"] == 8443
    loggers = seen["logging"]["loggers"]
    assert loggers["httpx"]["level"] == loggers["httpcore"]["level"] == "WARNING"
