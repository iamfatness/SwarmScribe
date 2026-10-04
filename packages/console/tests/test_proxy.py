import inspect
import json
import logging
import re

import pytest
from console_testkit import CREDENTIAL, ENTRA_ISSUER, all_rows_text
from sqlalchemy import select
from swarmscribe_console.crypto import ConsoleKeys
from swarmscribe_console.db.models import AuditEntry, Leader
from swarmscribe_console.proxy import ROUTES
from swarmscribe_console.sessions import SESSION_COOKIE
from swarmscribe_leader.api.admin import router as leader_admin_router
from swarmscribe_leader.auth.consoles import parse_delegation

HOST = "eu-1.leaders.example"
JOB = "11111111-2222-4333-8444-555555555555"
TOKEN_ID = "99999999-8888-4777-8666-555555555555"
TOKEN_PLAINTEXT = "J" * 20 + "-_" + "t" * 21
ACTOR = f"{ENTRA_ISSUER} entra-person-1 person@example.org"


@pytest.fixture
async def eu(factory):
    return await factory.leader("eu-1", labels={"env": "prod"})


@pytest.fixture
async def signed_in_as(client, factory):
    async def sign(role, scope="label:env=prod", **person):
        await factory.grant(role, scope, "email", "person@example.org")
        csrf = await factory.person(client, principals={"email:person@example.org"}, **person)
        client.headers["X-CSRF-Token"] = csrf
        return client

    return sign


async def _audit(sessionmaker) -> list[AuditEntry]:
    async with sessionmaker() as session:
        return list((await session.scalars(select(AuditEntry).order_by(AuditEntry.id))).all())


# --- forwarding -------------------------------------------------------------------------


async def test_a_read_is_forwarded_with_the_person_and_their_role(signed_in_as, eu, fake_leader):
    fake_leader.replies[("GET", "/v1/admin/jobs")] = (200, [{"id": JOB, "state": "queued"}], {})
    client = await signed_in_as("operator")
    answer = await client.get(
        "/api/leaders/eu-1/jobs", params={"state": "queued", "limit": "5", "evil": "1"}
    )
    assert answer.status_code == 200
    assert answer.json() == [{"id": JOB, "state": "queued"}]
    assert answer.headers["cache-control"] == "no-store"
    (request,) = fake_leader.requests
    assert str(request.url) == f"https://{HOST}/v1/admin/jobs?state=queued&limit=5"
    assert request.headers["authorization"] == f"Console {CREDENTIAL}"
    assert request.headers["x-swarmscribe-actor"] == ACTOR
    assert request.headers["x-swarmscribe-actor-role"] == "operator"
    parse_delegation(
        request.headers.get_list("x-swarmscribe-actor"),
        request.headers.get_list("x-swarmscribe-actor-role"),
    )


async def test_the_role_sent_is_the_persons_console_role_on_that_leader(
    signed_in_as, eu, fake_leader
):
    client = await signed_in_as("admin", scope="all")
    assert (await client.get("/api/leaders/eu-1/status")).status_code == 200
    assert fake_leader.requests[0].headers["x-swarmscribe-actor-role"] == "admin"


async def test_reads_are_not_audited_in_the_console(signed_in_as, eu, sessionmaker):
    client = await signed_in_as("viewer")
    await client.get("/api/leaders/eu-1/status")
    assert await _audit(sessionmaker) == []


@pytest.mark.parametrize(
    "query",
    ["state=queued&state=failed", "state=" + "x" * 201, "location=a%0Ab"],
    ids=["twice", "too-long", "control-character"],
)
async def test_odd_query_parameters_are_refused_before_the_leader(
    signed_in_as, eu, fake_leader, query
):
    client = await signed_in_as("viewer")
    answer = await client.get(f"/api/leaders/eu-1/jobs?{query}")
    assert answer.status_code == 422
    assert fake_leader.requests == []


async def test_an_action_is_forwarded_and_audited(signed_in_as, eu, fake_leader, sessionmaker):
    fake_leader.replies[("POST", f"/v1/admin/jobs/{JOB}/retry")] = (200, {"id": JOB}, {})
    client = await signed_in_as("operator")
    answer = await client.post(f"/api/leaders/eu-1/jobs/{JOB}/retry")
    assert (answer.status_code, answer.json()) == (200, {"id": JOB})
    assert fake_leader.requests[0].method == "POST"
    (entry,) = await _audit(sessionmaker)
    assert (entry.action, entry.leader, entry.target, entry.outcome) == (
        "jobs.retry",
        "eu-1",
        f"job_id={JOB}",
        "ok",
    )
    assert entry.actor == f"person@example.org ({ENTRA_ISSUER} entra-person-1)"
    assert entry.detail == {"role": "operator"}


async def test_a_body_is_forwarded_as_the_same_json(signed_in_as, eu, fake_leader):
    fake_leader.replies[("POST", f"/v1/admin/jobs/{JOB}/priority")] = (200, {"id": JOB}, {})
    client = await signed_in_as("operator")
    answer = await client.post(f"/api/leaders/eu-1/jobs/{JOB}/priority", json={"priority": 5})
    assert answer.status_code == 200
    assert json.loads(fake_leader.bodies[0]) == {"priority": 5}


@pytest.mark.parametrize(
    ("content", "status"),
    [(b"[1, 2]", 422), (b'"x"', 422), (b"{not json", 422), (b"", 422), (b"{" + b" " * 70_000 + b"}", 413)],  # noqa: E501
    ids=["array", "string", "invalid", "missing", "too-large"],
)
async def test_bodies_must_be_small_json_objects(signed_in_as, eu, fake_leader, content, status):
    client = await signed_in_as("operator")
    answer = await client.post(
        f"/api/leaders/eu-1/jobs/{JOB}/priority",
        content=content,
        headers={"Content-Type": "application/json"},
    )
    assert answer.status_code == status
    assert fake_leader.requests == []


# --- authorised twice -------------------------------------------------------------------


async def test_the_console_refuses_below_the_routes_role_without_calling_the_leader(
    signed_in_as, eu, fake_leader, sessionmaker
):
    client = await signed_in_as("viewer")
    answer = await client.post(f"/api/leaders/eu-1/jobs/{JOB}/retry")
    assert answer.status_code == 403
    assert answer.json()["code"] == "forbidden"
    assert "operator" in answer.json()["message"]
    assert fake_leader.requests == []
    (entry,) = await _audit(sessionmaker)
    assert (entry.action, entry.outcome) == ("jobs.retry", "forbidden")
    assert (await client.get("/api/leaders/eu-1/tokens")).status_code == 403


async def test_the_leaders_own_refusal_passes_through(signed_in_as, eu, fake_leader):
    fake_leader.replies[("POST", "/v1/admin/followers/" + JOB + "/revoke")] = (
        403,
        {"code": "forbidden", "message": "this needs the admin role; console fleet is limited to operator"},  # noqa: E501
        {},
    )
    client = await signed_in_as("admin")
    answer = await client.post(f"/api/leaders/eu-1/followers/{JOB}/revoke")
    assert answer.status_code == 403
    assert answer.json()["message"].endswith("limited to operator")


# --- leader errors and outages ----------------------------------------------------------


async def test_a_leader_error_passes_through_with_its_code(
    signed_in_as, eu, fake_leader, sessionmaker
):
    fake_leader.replies[("POST", f"/v1/admin/jobs/{JOB}/retry")] = (
        409,
        {"code": "not_retryable", "message": "the job is completed"},
        {},
    )
    client = await signed_in_as("operator")
    answer = await client.post(f"/api/leaders/eu-1/jobs/{JOB}/retry")
    assert (answer.status_code, answer.json()) == (
        409,
        {"code": "not_retryable", "message": "the job is completed"},
    )
    assert (await _audit(sessionmaker))[-1].outcome == "not_retryable"


async def test_a_leaders_retry_after_passes_through(signed_in_as, eu, fake_leader):
    fake_leader.replies[("GET", "/v1/admin/status")] = (
        503,
        {"code": "unavailable", "message": "service temporarily unavailable"},
        {"Retry-After": "10"},
    )
    client = await signed_in_as("viewer")
    answer = await client.get("/api/leaders/eu-1/status")
    assert (answer.status_code, answer.headers["retry-after"]) == (503, "10")


@pytest.mark.parametrize(
    "reply",
    [
        (500, b"<html>oops</html>", {"Content-Type": "text/html"}),
        (500, {"error": "no code"}, {}),
        (418, {"code": "Bad Code!", "message": "x"}, {}),
        (302, b"", {"Location": "https://elsewhere.example/"}),
    ],
    ids=["html", "no-code", "bad-code", "redirect"],
)
async def test_an_unusable_leader_answer_is_a_bad_gateway(signed_in_as, eu, fake_leader, reply):
    fake_leader.replies[("GET", "/v1/admin/status")] = reply
    client = await signed_in_as("viewer")
    answer = await client.get("/api/leaders/eu-1/status")
    assert (answer.status_code, answer.json()["code"]) == (502, "bad_gateway")


async def test_an_unreachable_leader_is_503_and_others_still_work(
    signed_in_as, eu, factory, fake_leader, sessionmaker
):
    await factory.leader("us-1", labels={"env": "prod"})
    fake_leader.modes[HOST] = "down"
    client = await signed_in_as("operator")
    answer = await client.post(f"/api/leaders/eu-1/jobs/{JOB}/cancel")
    assert answer.status_code == 503
    assert answer.json()["code"] == "leader_unreachable"
    assert answer.headers["retry-after"] == "15"
    assert (await _audit(sessionmaker))[-1].outcome == "leader_unreachable"
    assert (await client.get("/api/leaders/us-1/status")).status_code == 200


async def test_a_slow_leader_is_unreachable_after_the_proxy_timeout(
    app, make_settings, signed_in_as, eu, fake_leader
):
    app.state.settings = make_settings(proxy_timeout_seconds=0.2)
    fake_leader.modes[HOST] = "slow"
    fake_leader.delay = 2.0
    client = await signed_in_as("viewer")
    answer = await client.get("/api/leaders/eu-1/status")
    assert (answer.status_code, answer.json()["code"]) == (503, "leader_unreachable")


async def test_a_revoked_credential_marks_the_leader_and_stops_calls(
    signed_in_as, eu, fake_leader, sessionmaker
):
    fake_leader.modes[HOST] = "revoked"
    client = await signed_in_as("viewer")
    first = await client.get("/api/leaders/eu-1/status")
    assert (first.status_code, first.json()["code"]) == (503, "leader_credential_revoked")
    async with sessionmaker() as session:
        leader = (await session.scalars(select(Leader))).one()
    assert leader.credential_revoked_at is not None
    again = await client.get("/api/leaders/eu-1/status")
    assert again.json()["code"] == "leader_credential_revoked"
    assert len(fake_leader.requests) == 1
    actions = [entry.action for entry in await _audit(sessionmaker)]
    assert actions == ["leader.credential_revoked"]


async def test_an_unknown_credential_is_never_a_401_to_the_browser(signed_in_as, eu, fake_leader):
    fake_leader.modes[HOST] = "unknown"
    client = await signed_in_as("viewer")
    answer = await client.get("/api/leaders/eu-1/status")
    assert (answer.status_code, answer.json()["code"]) == (502, "leader_credential_rejected")
    assert (await client.get("/api/session")).status_code == 200


async def test_a_revoked_message_without_the_code_does_not_mark_the_leader(
    signed_in_as, eu, fake_leader, sessionmaker
):
    fake_leader.replies[("GET", "/v1/admin/status")] = (
        401,
        {"code": "unauthorized", "message": "this console credential has been revoked"},
        {},
    )
    client = await signed_in_as("viewer")
    answer = await client.get("/api/leaders/eu-1/status")
    assert (answer.status_code, answer.json()["code"]) == (502, "leader_credential_rejected")
    async with sessionmaker() as session:
        leader = (await session.scalars(select(Leader))).one()
    assert leader.credential_revoked_at is None


async def test_a_credential_the_key_cannot_open_is_refused_without_a_call(
    signed_in_as, factory, fake_leader
):
    factory.keys = ConsoleKeys(bytes(32))
    await factory.leader("eu-1", labels={"env": "prod"})
    client = await signed_in_as("viewer")
    answer = await client.get("/api/leaders/eu-1/status")
    assert answer.json()["code"] == "leader_credential_unreadable"
    assert fake_leader.requests == []


async def test_a_disabled_leader_is_refused(signed_in_as, factory, fake_leader):
    await factory.leader("eu-1", labels={"env": "prod"}, enabled=False)
    client = await signed_in_as("viewer")
    answer = await client.get("/api/leaders/eu-1/status")
    assert (answer.status_code, answer.json()["code"]) == (409, "leader_disabled")
    assert fake_leader.requests == []


# --- join tokens ------------------------------------------------------------------------


async def test_join_token_plaintext_passes_once_and_is_kept_nowhere(
    signed_in_as, eu, fake_leader, sessionmaker, engine, caplog
):
    caplog.set_level(logging.DEBUG)
    created = {
        "id": TOKEN_ID,
        "token": TOKEN_PLAINTEXT,
        "pool": "default",
        "expires_at": "2026-10-10T00:00:00Z",
        "max_uses": 1,
    }
    fake_leader.replies[("POST", "/v1/admin/tokens")] = (201, created, {})
    client = await signed_in_as("admin")
    answer = await client.post("/api/leaders/eu-1/tokens", json={"pool": "default"})
    assert answer.status_code == 201
    assert answer.json()["token"] == TOKEN_PLAINTEXT
    assert answer.headers["cache-control"] == "no-store"
    (entry,) = await _audit(sessionmaker)
    assert (entry.action, entry.outcome) == ("tokens.create", "ok")
    assert entry.detail == {"role": "admin", "token_id": TOKEN_ID, "pool": "default"}
    assert TOKEN_PLAINTEXT not in await all_rows_text(engine)
    assert TOKEN_PLAINTEXT not in caplog.text
    assert CREDENTIAL not in caplog.text
    assert client.cookies.get(SESSION_COOKIE) not in caplog.text


# --- hostile input ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/leaders/..%2Feu-1/status",
        "/api/leaders/eu-1%2F..%2Fus-1/status",
        "/api/leaders/%2E%2E/status",
        "/api/leaders/eu-1/%2E%2E/%2E%2E/admin/leaders",
        "/api/leaders/eu-1/jobs/..%2F..%2Fconsoles",
        "/api/leaders/eu-1/jobs/not-a-uuid/retry",
        f"/api/leaders/eu-1/jobs/{JOB}%2F..%2F..%2Fconsoles/retry",
        "/api/leaders/eu-1/locations/..%2Fx/ingest",
        "/api/leaders/eu-1/consoles",
        "/api/leaders/eu-1/whoami",
        "/api/leaders/eu-1/login-config",
        "/api/leaders/eu-1/v1/admin/status",
        "/api/leaders/eu-1/status/",
        "/api/leaders/eu-1/STATUS",
        "/api/leaders/eu-1/",
    ],
)
async def test_path_tricks_never_reach_a_leader(signed_in_as, eu, fake_leader, path):
    client = await signed_in_as("admin", scope="all")
    for method in ("GET", "POST"):
        answer = await client.request(method, path)
        assert answer.status_code in (404, 405), (method, path, answer.status_code)
    assert fake_leader.requests == []


async def test_an_unknown_and_an_ungranted_leader_look_the_same(
    signed_in_as, factory, eu, fake_leader
):
    await factory.leader("us-1", labels={"env": "test"})
    client = await signed_in_as("admin")
    hidden = await client.get("/api/leaders/us-1/status")
    unknown = await client.get("/api/leaders/zz-9/status")
    assert hidden.status_code == unknown.status_code == 404
    assert hidden.json() == unknown.json()
    assert fake_leader.requests == []


async def test_an_identity_the_leader_cannot_take_is_refused(signed_in_as, eu, fake_leader):
    client = await signed_in_as("viewer", subject="has a space")
    answer = await client.get("/api/leaders/eu-1/status")
    assert (answer.status_code, answer.json()["code"]) == (403, "actor_not_representable")
    assert fake_leader.requests == []


async def test_header_injection_through_the_email_goes_nowhere(signed_in_as, eu, fake_leader):
    client = await signed_in_as("viewer", email="a@example.org\r\nX-Evil: 1")
    assert (await client.get("/api/leaders/eu-1/status")).status_code == 200
    (request,) = fake_leader.requests
    assert request.headers["x-swarmscribe-actor"] == f"{ENTRA_ISSUER} entra-person-1 -"
    assert "x-evil" not in request.headers


async def test_an_action_without_the_csrf_token_never_reaches_the_leader(
    signed_in_as, eu, fake_leader
):
    client = await signed_in_as("operator")
    del client.headers["X-CSRF-Token"]
    answer = await client.post(f"/api/leaders/eu-1/jobs/{JOB}/retry")
    assert (answer.status_code, answer.json()["code"]) == (403, "csrf_failed")
    assert fake_leader.requests == []


# --- the allow-list against the leader --------------------------------------------------


def _leader_routes() -> dict[tuple[str, str], tuple[str | None, bool]]:
    """Every leader /v1/admin route: (method, path shape) -> (required role, consoles ok)."""
    found = {}
    for route in leader_admin_router.routes:
        role, consoles_allowed = None, True
        for dependency in route.dependant.dependencies:
            call = dependency.call
            if getattr(call, "__qualname__", "") == "require.<locals>.dependency":
                captured = inspect.getclosurevars(call).nonlocals
                role, consoles_allowed = captured["role"], captured["consoles_allowed"]
        for method in route.methods:
            found[(method, re.sub(r"\{\w+\}", "{}", route.path))] = (role, consoles_allowed)
    return found


def test_the_allow_list_is_exactly_the_leaders_delegable_admin_routes_with_their_roles():
    leader = _leader_routes()
    delegable = {
        key: role
        for key, (role, consoles_allowed) in leader.items()
        if role is not None
        and consoles_allowed
        and key not in {("GET", "/v1/admin/whoami")}
    }
    offered = {
        (route.method, re.sub(r"\{\w+\}", "{}", "/v1/admin/" + route.template)): route.role
        for route in ROUTES
    }
    assert offered == delegable


# --- ruling R2: no echo through the proxy, and the small fixes ---------------------------


@pytest.mark.parametrize(
    ("path", "body"),
    [
        (f"/api/leaders/eu-1/jobs/{JOB}/priority", {"priority": "high-SECRETISH"}),
        (f"/api/leaders/eu-1/jobs/{JOB}/priority", {"priority": 5, "extra-SECRETISH": 1}),
        (f"/api/leaders/eu-1/jobs/{JOB}/priority", {"priority": 5000}),
        ("/api/leaders/eu-1/tokens", {"pool": "bad pool SECRETISH"}),
        ("/api/leaders/eu-1/tokens", {"max_uses": 0}),
        ("/api/leaders/eu-1/locations", {"name": "x"}),
    ],
)
async def test_a_body_the_leader_would_refuse_is_refused_by_the_console_without_echo(
    signed_in_as, eu, fake_leader, sessionmaker, path, body
):
    client = await signed_in_as("admin", scope="all")
    answer = await client.post(path, json=body)
    assert answer.status_code == 422
    assert answer.json()["code"] == "invalid_request"
    assert "SECRETISH" not in answer.text
    assert fake_leader.requests == []
    assert (await _audit(sessionmaker))[-1].outcome == "invalid_request"


async def test_a_leader_422_keeps_status_and_code_but_not_its_message(
    signed_in_as, eu, fake_leader
):
    fake_leader.replies[("POST", f"/v1/admin/jobs/{JOB}/priority")] = (
        422,
        {"code": "invalid_request", "message": "priority 'ECHOED-INPUT' is not valid"},
        {},
    )
    client = await signed_in_as("operator")
    answer = await client.post(f"/api/leaders/eu-1/jobs/{JOB}/priority", json={"priority": 5})
    assert (answer.status_code, answer.json()["code"]) == (422, "invalid_request")
    assert "ECHOED-INPUT" not in answer.text


async def test_a_non_json_success_is_a_bad_gateway(signed_in_as, eu, fake_leader, sessionmaker):
    fake_leader.replies[("POST", f"/v1/admin/jobs/{JOB}/retry")] = (
        200,
        b"<html>ok</html>",
        {"Content-Type": "text/html"},
    )
    client = await signed_in_as("operator")
    answer = await client.post(f"/api/leaders/eu-1/jobs/{JOB}/retry")
    assert (answer.status_code, answer.json()["code"]) == (502, "bad_gateway")
    assert (await _audit(sessionmaker))[-1].outcome == "bad_gateway"


async def test_an_oversized_leader_answer_is_a_bad_gateway(
    signed_in_as, eu, fake_leader, monkeypatch
):
    monkeypatch.setattr("swarmscribe_console.leader_client.MAX_BODY_BYTES", 100)
    fake_leader.replies[("GET", "/v1/admin/status")] = (200, {"pad": "x" * 500}, {})
    client = await signed_in_as("viewer")
    answer = await client.get("/api/leaders/eu-1/status")
    assert (answer.status_code, answer.json()["code"]) == (502, "bad_gateway")


async def test_an_empty_success_is_passed_only_as_204(signed_in_as, eu, fake_leader):
    fake_leader.replies[("POST", f"/v1/admin/jobs/{JOB}/cancel")] = (204, b"", {})
    fake_leader.replies[("POST", f"/v1/admin/jobs/{JOB}/retry")] = (200, b"", {})
    client = await signed_in_as("operator")
    assert (await client.post(f"/api/leaders/eu-1/jobs/{JOB}/cancel")).status_code == 204
    again = await client.post(f"/api/leaders/eu-1/jobs/{JOB}/retry")
    assert (again.status_code, again.json()["code"]) == (502, "bad_gateway")


async def test_a_path_the_client_refuses_is_never_a_500(
    signed_in_as, eu, fake_leader, monkeypatch
):
    # Defence in depth: even if a route formed an unsafe path, the guard's ValueError is a 502.
    def unsafe(self, params):
        return "/v1/admin/../consoles"

    monkeypatch.setattr("swarmscribe_console.proxy.ProxyRoute.leader_path", unsafe)
    client = await signed_in_as("viewer")
    answer = await client.get("/api/leaders/eu-1/status")
    assert (answer.status_code, answer.json()["code"]) == (502, "bad_gateway")
    assert fake_leader.requests == []


async def test_deeply_nested_json_is_refused_not_a_500(signed_in_as, eu, fake_leader):
    client = await signed_in_as("operator")
    answer = await client.post(
        f"/api/leaders/eu-1/jobs/{JOB}/priority",
        content=b"[" * 30_000 + b"]" * 30_000,
        headers={"Content-Type": "application/json"},
    )
    assert answer.status_code == 422
    assert fake_leader.requests == []


async def test_the_apps_pool_is_idle_during_the_leader_call(
    app, signed_in_as, eu, fake_leader
):
    seen = []

    async def watch(_request):
        seen.append(app.state.engine.pool.checkedout())

    fake_leader.on_request = watch
    client = await signed_in_as("viewer")
    assert (await client.get("/api/leaders/eu-1/status")).status_code == 200
    assert seen == [0]


@pytest.mark.parametrize(
    "path",
    [
        "/api/leaders/eu-1/jobs%00/retry",
        "/api/leaders/eu%00/status",
        "/api/leaders/" + "a" * 5000 + "/status",
        "/api/leaders/eu-1/" + "a" * 5000,
        f"/api/leaders/eu-1/jobs/{JOB}%5C..%5Cconsoles/retry",
        "/api/leaders/eu-1/jobs//retry",
        "/api/leaders/eu-1//status",
        "/api/leaders/eu-1/status%00",
        "/api/leaders/eu-1/status%3Fx=1",
        "/api/leaders/eu-1/status%23",
        "/api/leaders/eu-1/locations/a%40evil.example/ingest",
        "/api/leaders/%252e%252e/status",
    ],
)
async def test_more_path_tricks_never_reach_a_leader(signed_in_as, eu, fake_leader, path):
    client = await signed_in_as("admin", scope="all")
    for method in ("GET", "POST"):
        answer = await client.request(method, path)
        assert answer.status_code in (404, 405), (method, path, answer.status_code)
    assert fake_leader.requests == []


async def test_a_join_token_is_not_in_any_audit_detail_or_other_answer(
    signed_in_as, eu, fake_leader, sessionmaker, engine, caplog
):
    caplog.set_level(logging.DEBUG)
    fake_leader.replies[("POST", "/v1/admin/tokens")] = (
        201,
        {"id": TOKEN_ID, "token": TOKEN_PLAINTEXT, "pool": "default", "max_uses": 1},
        {},
    )
    client = await signed_in_as("admin")
    await client.post("/api/leaders/eu-1/tokens", json={"pool": "default"})
    fake_leader.replies[("GET", "/v1/admin/tokens")] = (200, [{"id": TOKEN_ID}], {})
    listed = await client.get("/api/leaders/eu-1/tokens")
    assert TOKEN_PLAINTEXT not in listed.text
    rows = await all_rows_text(engine)
    assert TOKEN_PLAINTEXT not in rows
    assert TOKEN_PLAINTEXT not in repr(await _audit(sessionmaker))
    assert TOKEN_PLAINTEXT not in caplog.text


# --- review fix round 1 ------------------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [b'{"x": NaN}', b'{"x": Infinity}', b"[" * 200_000 + b"]" * 200_000],
    ids=["nan", "infinity", "deeply-nested"],
)
async def test_an_unrenderable_leader_success_is_a_502_with_a_bad_gateway_audit(
    signed_in_as, eu, fake_leader, sessionmaker, content
):
    fake_leader.replies[("POST", f"/v1/admin/jobs/{JOB}/retry")] = (
        200,
        content,
        {"Content-Type": "application/json"},
    )
    client = await signed_in_as("operator")
    answer = await client.post(f"/api/leaders/eu-1/jobs/{JOB}/retry")
    assert (answer.status_code, answer.json()["code"]) == (502, "bad_gateway")
    entries = await _audit(sessionmaker)
    assert [(e.action, e.outcome) for e in entries] == [("jobs.retry", "bad_gateway")]


async def test_a_body_that_cannot_be_rendered_is_audited_as_bad_gateway_not_ok(
    signed_in_as, eu, fake_leader, sessionmaker, monkeypatch
):
    import swarmscribe_console.api.proxy as api_proxy

    def refusing(content, **kwargs):
        raise ValueError("cannot render")

    monkeypatch.setattr(api_proxy, "JSONResponse", refusing)
    fake_leader.replies[("POST", f"/v1/admin/jobs/{JOB}/retry")] = (200, {"id": JOB}, {})
    client = await signed_in_as("operator")
    answer = await client.post(f"/api/leaders/eu-1/jobs/{JOB}/retry")
    assert (answer.status_code, answer.json()["code"]) == (502, "bad_gateway")
    assert [e.outcome for e in await _audit(sessionmaker)] == ["bad_gateway"]


async def test_an_empty_success_is_audited_as_bad_gateway(
    signed_in_as, eu, fake_leader, sessionmaker
):
    fake_leader.replies[("POST", f"/v1/admin/jobs/{JOB}/retry")] = (200, b"", {})
    client = await signed_in_as("operator")
    answer = await client.post(f"/api/leaders/eu-1/jobs/{JOB}/retry")
    assert answer.status_code == 502
    assert "unknown" in answer.json()["message"]
    assert [e.outcome for e in await _audit(sessionmaker)] == ["bad_gateway"]


@pytest.mark.parametrize("root", ["/srv/audio", "D:\\recordings"], ids=["posix", "windows"])
async def test_a_location_root_absolute_on_either_os_is_forwarded_intact(
    signed_in_as, eu, fake_leader, root
):
    fake_leader.replies[("POST", "/v1/admin/locations")] = (201, {"name": "loc1"}, {})
    client = await signed_in_as("admin")
    sent = {"name": "loc1", "root": root}
    answer = await client.post("/api/leaders/eu-1/locations", json=sent)
    assert answer.status_code == 201
    assert json.loads(fake_leader.bodies[0]) == sent  # no channel_labels, no defaults added


async def test_a_full_location_body_is_forwarded_as_sent(signed_in_as, eu, fake_leader):
    fake_leader.replies[("POST", "/v1/admin/locations")] = (201, {"name": "loc1"}, {})
    client = await signed_in_as("admin")
    sent = {
        "name": "loc1",
        "root": "/srv/audio",
        "input_prefix": "in/",
        "output_prefix": "out/",
        "pool": "gpu",
        "required_device": "any",
        "scan_interval_s": 60,
        "channel_mode": "stereo_split",
        "channel_labels": ["host", "guest"],
    }
    answer = await client.post("/api/leaders/eu-1/locations", json=sent)
    assert answer.status_code == 201, answer.text
    assert json.loads(fake_leader.bodies[0]) == sent


@pytest.mark.parametrize("root", ["audio/x", "", "relative\\x", "/srv/\nx"])
async def test_a_relative_location_root_is_refused_with_fixed_text(
    signed_in_as, eu, fake_leader, root
):
    client = await signed_in_as("admin")
    answer = await client.post("/api/leaders/eu-1/locations", json={"name": "l", "root": root})
    assert answer.status_code == 422
    assert "audio/x" not in answer.text
    assert fake_leader.requests == []


async def test_a_mono_location_with_channel_labels_set_is_refused(signed_in_as, eu, fake_leader):
    client = await signed_in_as("admin")
    answer = await client.post(
        "/api/leaders/eu-1/locations",
        json={"name": "l", "root": "/srv/a", "channel_labels": ["a", "b"]},
    )
    assert answer.status_code == 422
    assert fake_leader.requests == []


async def test_a_token_body_with_every_field_is_forwarded_as_sent(signed_in_as, eu, fake_leader):
    fake_leader.replies[("POST", "/v1/admin/tokens")] = (
        201,
        {"id": TOKEN_ID, "token": TOKEN_PLAINTEXT, "pool": "gpu"},
        {},
    )
    client = await signed_in_as("admin")
    sent = {"pool": "gpu", "expires_in_seconds": 3600, "max_uses": 5}
    assert (await client.post("/api/leaders/eu-1/tokens", json=sent)).status_code == 201
    assert json.loads(fake_leader.bodies[0]) == sent
