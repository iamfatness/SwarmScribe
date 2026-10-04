import logging.config
from datetime import timedelta

import httpx
import pytest
from console_testkit import ENTRA_ISSUER, all_rows_text, cookie_attributes
from fastapi import APIRouter, Depends, FastAPI
from sqlalchemy import delete, select, update
from swarmscribe_console.api.deps import SAFE_METHODS, checked
from swarmscribe_console.api.guard import (
    UnguardedRoute,
    api_routes,
    assert_guarded,
    unguarded,
)
from swarmscribe_console.app import create_app
from swarmscribe_console.db.models import AuditEntry, ConsoleSession
from swarmscribe_console.sessions import SESSION_COOKIE, find_session
from swarmscribe_leader.auth.secrets import hash_secret
from swarmscribe_leader.clock import utcnow


async def _session_row(sessionmaker) -> ConsoleSession:
    async with sessionmaker() as session:
        return (await session.scalars(select(ConsoleSession))).one()


async def _age(sessionmaker, **values):
    async with sessionmaker() as session:
        await session.execute(update(ConsoleSession).values(**values))
        await session.commit()


async def test_a_signed_in_browser_sees_its_session_and_csrf_token(client, factory, keys):
    csrf = await factory.person(client, principals={"email:person@example.org"})
    answer = await client.get("/api/session")
    assert answer.status_code == 200
    body = answer.json()
    assert (body["provider"], body["issuer"], body["subject"], body["email"]) == (
        "entra",
        ENTRA_ISSUER,
        "entra-person-1",
        "person@example.org",
    )
    assert body["csrf_token"] == csrf
    assert body["console_admin"] is False
    assert answer.headers["cache-control"] == "no-store"


async def test_a_console_admin_is_told_so(client, factory):
    await factory.console_admin("email", "person@example.org")
    await factory.person(client, principals={"email:person@example.org"})
    assert (await client.get("/api/session")).json()["console_admin"] is True


async def test_the_session_is_stored_only_by_its_hash(client, factory, sessionmaker, engine):
    await factory.person(client)
    session_id = client.cookies.get(SESSION_COOKIE)
    row = await _session_row(sessionmaker)
    assert row.id_hash == hash_secret(session_id)
    assert session_id not in await all_rows_text(engine)


async def test_no_cookie_is_unauthenticated_without_clearing_anything(client):
    answer = await client.get("/api/session")
    assert answer.status_code == 401
    assert answer.json()["code"] == "unauthenticated"
    assert cookie_attributes(answer, SESSION_COOKIE) is None


@pytest.mark.parametrize(
    "cookie",
    ["A" * 43, "x" * 10_000, "../../etc/passwd", "A" * 42 + "!", ""],
    ids=["forged", "oversize", "path", "bad-character", "empty"],
)
async def test_a_forged_or_malformed_cookie_is_refused_and_cleared(client, cookie):
    client.cookies.set(SESSION_COOKIE, cookie, domain="console.test", path="/")
    answer = await client.get("/api/session")
    assert answer.status_code == 401
    if cookie:
        cleared = cookie_attributes(answer, SESSION_COOKIE)
        assert cleared is not None and cleared["max-age"] == "0"


async def test_a_session_ends_eight_hours_after_sign_in(client, factory, sessionmaker):
    await factory.person(client)
    await _age(
        sessionmaker,
        created_at=utcnow() - timedelta(hours=8, seconds=1),
        expires_at=utcnow() - timedelta(seconds=1),
        last_seen_at=utcnow() - timedelta(seconds=5),
    )
    answer = await client.get("/api/session")
    assert answer.status_code == 401
    assert cookie_attributes(answer, SESSION_COOKIE)["max-age"] == "0"
    async with sessionmaker() as session:
        assert (await session.scalars(select(ConsoleSession))).all() == []


async def test_a_session_idle_for_an_hour_ends(client, factory, sessionmaker):
    await factory.person(client)
    await _age(sessionmaker, last_seen_at=utcnow() - timedelta(minutes=61))
    assert (await client.get("/api/session")).status_code == 401
    async with sessionmaker() as session:
        assert (await session.scalars(select(ConsoleSession))).all() == []


async def test_activity_keeps_a_session_alive_and_is_written_at_most_once_a_minute(
    client, factory, sessionmaker
):
    await factory.person(client)
    await _age(sessionmaker, last_seen_at=utcnow() - timedelta(minutes=59))
    assert (await client.get("/api/session")).status_code == 200
    touched = (await _session_row(sessionmaker)).last_seen_at
    assert utcnow() - touched < timedelta(seconds=30)
    assert (await client.get("/api/session")).status_code == 200
    assert (await _session_row(sessionmaker)).last_seen_at == touched


async def test_the_idle_deadline_never_passes_the_lifetime(client, factory, sessionmaker):
    await factory.person(client)
    await _age(sessionmaker, expires_at=utcnow() + timedelta(minutes=10))
    body = (await client.get("/api/session")).json()
    assert body["idle_expires_at"] == body["expires_at"]


# --- CSRF -------------------------------------------------------------------------------


async def test_logout_with_the_csrf_token_ends_the_session(client, factory, sessionmaker):
    csrf = await factory.person(client)
    answer = await client.post("/api/session/logout", headers={"X-CSRF-Token": csrf})
    assert answer.status_code == 204
    cleared = cookie_attributes(answer, SESSION_COOKIE)
    assert cleared["max-age"] == "0"
    assert (await client.get("/api/session")).status_code == 401
    async with sessionmaker() as session:
        actions = (await session.scalars(select(AuditEntry.action))).all()
    assert actions == ["sign_out"]


async def test_the_csrf_token_may_come_with_the_consoles_own_origin(client, factory):
    csrf = await factory.person(client)
    answer = await client.post(
        "/api/session/logout",
        headers={"X-CSRF-Token": csrf, "Origin": "https://console.test"},
    )
    assert answer.status_code == 204


async def test_csrf_failures_are_refused_audited_and_change_nothing(
    client, new_client, factory, sessionmaker
):
    csrf = await factory.person(client)
    other = new_client()
    other_csrf = await factory.person(other, subject="entra-person-2")
    attempts = [
        {},
        {"X-CSRF-Token": "x" * 43},
        {"X-CSRF-Token": other_csrf},
        {"X-CSRF-Token": csrf, "Origin": "https://evil.example"},
        {"X-CSRF-Token": csrf, "Origin": "null"},
    ]
    for headers in attempts:
        answer = await client.post("/api/session/logout", headers=headers)
        assert answer.status_code == 403, headers
        assert answer.json()["code"] == "csrf_failed"
    doubled = await client.post(
        "/api/session/logout", headers=[("X-CSRF-Token", csrf), ("X-CSRF-Token", csrf)]
    )
    assert doubled.status_code == 403
    assert (await client.get("/api/session")).status_code == 200
    async with sessionmaker() as session:
        entries = (await session.scalars(select(AuditEntry))).all()
    assert len(entries) == len(attempts) + 1
    assert {(e.action, e.outcome, e.target) for e in entries} == {
        ("request.refused", "csrf_failed", "POST /api/session/logout")
    }
    assert all(csrf not in str(e.detail) and other_csrf not in str(e.detail) for e in entries)


async def test_every_state_changing_api_route_needs_the_csrf_token(app, client, factory):
    """Structural: walks every route by full path, including those later tasks add."""
    await factory.person(client)
    checked = 0
    for full_path, route, _ in api_routes(app.routes):
        if not full_path.startswith("/api"):
            continue
        for method in sorted(route.methods - SAFE_METHODS):
            path = full_path
            for name in route.param_convertors:
                path = path.replace(f"{{{name}:path}}", "x/x").replace(f"{{{name}}}", "x")
            answer = await client.request(method, path)
            assert answer.status_code == 403, (method, path, answer.text)
            assert answer.json()["code"] == "csrf_failed", (method, path)
            checked += 1
    assert checked >= 1


def _throwaway(prefix, *, guarded):
    router = APIRouter()
    deps = [Depends(checked)] if guarded else []

    @router.post("/leaders", dependencies=deps)
    async def create():
        return {}

    throwaway = FastAPI()
    throwaway.include_router(router, prefix=prefix)
    return throwaway


def test_the_walker_finds_and_flags_a_prefixed_route_without_the_dependency():
    found = _throwaway("/api/x", guarded=False)
    assert [p for p, _, _ in api_routes(found.routes)] == ["/api/x/leaders"]
    assert unguarded(found.routes) == ["POST /api/x/leaders"]
    with pytest.raises(UnguardedRoute, match="POST /api/x/leaders"):
        assert_guarded(found.routes)
    assert unguarded(_throwaway("/api/x", guarded=True).routes) == []


def test_the_walker_fails_loudly_on_an_api_route_it_cannot_inspect():
    odd = FastAPI()
    odd.mount("/api/files", FastAPI())
    with pytest.raises(UnguardedRoute, match="cannot inspect"):
        list(api_routes(odd.routes))


def test_the_app_refuses_to_build_with_an_unguarded_route(make_settings, monkeypatch):
    from swarmscribe_console.api import session as session_api

    router = APIRouter(prefix="/api/forgetful")

    @router.delete("/thing")
    async def forgetful():
        return {}

    monkeypatch.setattr(session_api, "router", router)
    with pytest.raises(UnguardedRoute, match="DELETE /api/forgetful/thing"):
        create_app(make_settings())


async def test_csrf_applies_to_every_method_but_get_head_options(app, client, factory):
    await factory.person(client)
    for method in ("POST", "PUT", "PATCH", "DELETE", "PURGE", "PROPFIND"):
        answer = await client.request(method, "/api/session/logout")
        assert answer.status_code in (403, 405), method
    assert (await client.get("/api/session")).status_code == 200
    assert SAFE_METHODS == {"GET", "HEAD", "OPTIONS"}


async def test_a_cross_site_fetch_is_refused_even_with_a_valid_token(client, factory):
    csrf = await factory.person(client)
    for site in ("cross-site", "same-site"):
        answer = await client.post(
            "/api/session/logout", headers={"X-CSRF-Token": csrf, "Sec-Fetch-Site": site}
        )
        assert answer.status_code == 403, site
    assert (await client.get("/api/session")).status_code == 200
    ok = await client.post(
        "/api/session/logout", headers={"X-CSRF-Token": csrf, "Sec-Fetch-Site": "same-origin"}
    )
    assert ok.status_code == 204


async def test_logout_racing_the_last_seen_update_is_signed_out_not_a_500(
    factory, sessionmaker, client
):
    await factory.person(client)
    cookie = client.cookies.get(SESSION_COOKIE)
    await _age(sessionmaker, last_seen_at=utcnow() - timedelta(minutes=5))

    class Racing:
        """Deletes the row (a concurrent logout) right after find_session reads it."""

        def __init__(self, inner):
            self.inner = inner

        async def get(self, *args):
            row = await self.inner.get(*args)
            async with sessionmaker() as other:
                await other.execute(delete(ConsoleSession))
                await other.commit()
            return row

        def __getattr__(self, name):
            return getattr(self.inner, name)

    async with sessionmaker() as session:
        found = await find_session(Racing(session), cookie, now=utcnow(), idle=timedelta(hours=1))
        await session.commit()
    assert found is None


# --- headers ----------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/api/session", "/nothing-here", "/auth/nothing"])
async def test_every_answer_carries_the_security_headers(client, path):
    answer = await client.get(path)
    csp = answer.headers["content-security-policy"]
    assert "script-src 'self'" in csp
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert answer.headers["x-content-type-options"] == "nosniff"
    assert answer.headers["referrer-policy"] == "no-referrer"
    assert answer.headers["x-frame-options"] == "DENY"
    assert answer.headers["strict-transport-security"].startswith("max-age=")


# --- hostile cases ----------------------------------------------------------------------


async def test_a_cookie_replayed_after_logout_is_refused(client, factory, new_client):
    csrf = await factory.person(client)
    stolen = client.cookies.get(SESSION_COOKIE)
    assert (
        await client.post("/api/session/logout", headers={"X-CSRF-Token": csrf})
    ).status_code == 204
    replay = new_client()
    replay.cookies.set(SESSION_COOKIE, stolen, domain="console.test", path="/")
    assert (await replay.get("/api/session")).status_code == 401
    answer = await replay.post("/api/session/logout", headers={"X-CSRF-Token": csrf})
    assert answer.status_code == 401


async def test_an_expired_session_cannot_be_used_to_change_anything(
    client, factory, sessionmaker
):
    csrf = await factory.person(client)
    await _age(sessionmaker, expires_at=utcnow() - timedelta(seconds=1))
    answer = await client.post("/api/session/logout", headers={"X-CSRF-Token": csrf})
    assert answer.status_code == 401


@pytest.mark.parametrize("token", ["", " ", "A" * 42, "A" * 44, "-" * 43])
async def test_a_wrong_length_or_empty_csrf_token_is_refused(client, factory, token):
    await factory.person(client)
    answer = await client.post("/api/session/logout", headers={"X-CSRF-Token": token})
    assert answer.status_code == 403
    assert answer.json()["code"] == "csrf_failed"
    assert (await client.get("/api/session")).status_code == 200


@pytest.mark.parametrize(
    "origin",
    [
        "https://console.test:8443",
        "http://console.test",
        "https://console.test.evil.example",
        "https://CONSOLE.test",
        "https://console.test/",
    ],
    ids=["port", "scheme", "suffix", "case", "slash"],
)
async def test_an_origin_differing_in_any_part_is_refused(client, factory, origin):
    csrf = await factory.person(client)
    answer = await client.post(
        "/api/session/logout", headers={"X-CSRF-Token": csrf, "Origin": origin}
    )
    assert answer.status_code == 403
    assert (await client.get("/api/session")).status_code == 200


async def test_two_session_cookies_are_refused_not_guessed_between(client, factory, new_client):
    await factory.person(client)
    other = new_client()
    await factory.person(other, subject="entra-person-2")
    good, other_id = client.cookies.get(SESSION_COOKIE), other.cookies.get(SESSION_COOKIE)
    bare = new_client()
    for header in (
        f"{SESSION_COOKIE}={good}; {SESSION_COOKIE}={other_id}",
        f"{SESSION_COOKIE}={other_id}; {SESSION_COOKIE}={good}",
    ):
        answer = await bare.get("/api/session", headers={"Cookie": header})
        assert answer.status_code == 401
    answer = await bare.get(
        "/api/session",
        headers=[("Cookie", f"{SESSION_COOKIE}={good}")] * 2,
    )
    assert answer.status_code == 401


async def test_there_is_no_state_changing_get(app, client, factory):
    csrf = await factory.person(client)
    for path, route, _ in api_routes(app.routes):
        assert not (route.methods & {"GET", "HEAD"}) or "logout" not in path
    answer = await client.get("/api/session/logout")
    assert answer.status_code == 405
    assert answer.headers["content-security-policy"]
    assert (await client.get("/api/session")).status_code == 200
    assert csrf


async def test_a_server_error_still_carries_the_security_headers(app, make_settings):
    @app.get("/api/boom")
    async def boom():
        raise RuntimeError("secret-detail")

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="https://console.test") as c:
        answer = await c.get("/api/boom")
    assert answer.status_code == 500
    assert answer.json() == {"code": "internal", "message": "internal error"}
    assert "frame-ancestors 'none'" in answer.headers["content-security-policy"]
    assert answer.headers["cache-control"] == "no-store"
    assert "secret-detail" not in answer.text


# --- serve ------------------------------------------------------------------------------


def test_serve_starts_uvicorn_without_an_access_log(monkeypatch, migrated_database_url):
    import uvicorn
    from console_testkit import console_env
    from swarmscribe_console.main import main

    console_env(monkeypatch, migrated_database_url)
    started = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: started.update(kwargs))
    monkeypatch.setattr(logging.config, "dictConfig", lambda config: None)
    assert main(["serve", "--host", "127.0.0.1", "--port", "9"]) == 0
    assert started["access_log"] is False
    assert started["log_config"] is None
    assert (started["host"], started["port"]) == ("127.0.0.1", 9)


def test_serve_refuses_a_database_it_cannot_reach(monkeypatch, capsys):
    import uvicorn
    from console_testkit import console_env
    from swarmscribe_console.main import main

    console_env(monkeypatch, "postgresql://nobody:hunter2@127.0.0.1:9/none")
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: pytest.fail("must not serve"))
    assert main(["serve"]) == 2
    err = capsys.readouterr().err
    assert "cannot connect to the database" in err
    assert "hunter2" not in err


@pytest.mark.parametrize("case", ["413", "422"])
async def test_refusals_from_outside_the_routes_carry_the_security_headers(app, client, case):
    @app.get("/api/needs-a-number")
    async def needs(n: int):
        return {}

    if case == "413":
        answer = await client.post("/api/session/logout", content=b"x" * (1024 * 1024 + 1))
        assert answer.status_code == 413
    else:
        answer = await client.get("/api/needs-a-number?n=x")
        assert answer.status_code == 422
    assert "frame-ancestors 'none'" in answer.headers["content-security-policy"]
    assert answer.headers["cache-control"] == "no-store"


async def test_an_unhandled_error_is_logged_once_with_frames_but_never_its_text(app, caplog):
    secret = "".join(["secret", "-detail"])  # built here, so no source line spells it

    @app.get("/api/boom2")
    async def boom():
        raise RuntimeError(secret)

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=True)
    with caplog.at_level(logging.INFO):
        async with httpx.AsyncClient(transport=transport, base_url="https://console.test") as c:
            assert (await c.get("/api/boom2")).status_code == 500
    lines = [r.getMessage() for r in caplog.records if "unhandled" in r.getMessage()]
    assert len(lines) == 1
    first, _, frames = lines[0].partition("\n")
    assert first == "unhandled error: RuntimeError at /api/boom2"
    assert "in boom" in frames and "raise RuntimeError(secret)" in frames
    assert secret not in caplog.text


async def test_contain_errors_wraps_http_only_so_a_lifespan_failure_reaches_the_server():
    from swarmscribe_console.app import ContainErrors

    async def failing(scope, receive, send):
        raise RuntimeError("startup failed")

    wrapped = ContainErrors(failing)
    with pytest.raises(RuntimeError):
        await wrapped({"type": "lifespan"}, None, None)
    with pytest.raises(RuntimeError):
        await wrapped({"type": "websocket"}, None, None)
    await wrapped({"type": "http"}, None, None)  # contained (and logged), not raised


async def test_the_database_engine_hides_statement_parameters(app):
    assert app.state.engine.sync_engine.hide_parameters is True


def test_a_root_mount_holding_api_routes_is_refused():
    from starlette.applications import Starlette
    from starlette.routing import Mount, Route
    from starlette.staticfiles import StaticFiles

    async def noop(request):
        return None

    sub = Starlette(routes=[Route("/api/things", noop, methods=["POST"])])
    root = FastAPI()
    root.mount("/", sub)
    with pytest.raises(UnguardedRoute, match=r"Mount at '/' contains the /api route"):
        assert_guarded(root.routes)
    nested = Starlette(routes=[Mount("/", routes=[Route("/api/x", noop)])])
    outer = FastAPI()
    outer.mount("/", nested)
    with pytest.raises(UnguardedRoute, match="contains the /api route"):
        assert_guarded(outer.routes)
    # Static files at the root hold no routes: fine. So is a mount that is not at the root.
    static = FastAPI()
    static.mount("/", StaticFiles(directory=".", check_dir=False))
    assert_guarded(static.routes)
    elsewhere = FastAPI()
    elsewhere.mount("/ui", sub)
    assert_guarded(elsewhere.routes)
