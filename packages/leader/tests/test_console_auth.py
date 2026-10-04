import uuid

import pytest
from sqlalchemy import func, select
from swarmscribe_leader.auth.secrets import new_secret
from swarmscribe_leader.db.models import AuditEntry, ConsoleCredential, Follower

ISSUER = "https://login.microsoftonline.com/0f0e0d0c-0b0a-4908-8706-050403020100/v2.0"
PERSON = f"{ISSUER} entra-person-7 person.seven@example.org"
PERSON_ACTOR = f"person.seven@example.org ({ISSUER} entra-person-7)"


def console_headers(credential, *, actor=PERSON, role="admin", scheme="Console"):
    headers = {"Authorization": f"{scheme} {credential}"}
    if actor is not None:
        headers["X-SwarmScribe-Actor"] = actor
    if role is not None:
        headers["X-SwarmScribe-Actor-Role"] = role
    return headers


PERSON_BYTES = PERSON.encode()


def raw_headers(credential, *, actor=PERSON_BYTES, role=b"viewer"):
    """Headers as bytes, so tests can send exactly what a hostile console would."""
    headers = [(b"authorization", f"Console {credential}".encode())]
    if actor is not None:
        headers.append((b"x-swarmscribe-actor", actor))
    if role is not None:
        headers.append((b"x-swarmscribe-actor-role", role))
    return headers


async def audit_rows(sessionmaker, action):
    async with sessionmaker() as session:
        return list(
            (await session.scalars(select(AuditEntry).where(AuditEntry.action == action))).all()
        )


async def all_audit_text(sessionmaker) -> str:
    async with sessionmaker() as session:
        entries = (await session.scalars(select(AuditEntry))).all()
    return " ".join(f"{e.actor} {e.subject_id} {e.detail}" for e in entries)


async def audit_count(sessionmaker) -> int:
    async with sessionmaker() as session:
        return await session.scalar(select(func.count()).select_from(AuditEntry))


# --- acting for a person ----------------------------------------------------------------


async def test_a_console_acts_for_the_person_it_names(admin_client, factory, sessionmaker):
    _, credential = await factory.console(name="fleet", max_role="admin")
    response = await admin_client.get(
        "/v1/admin/whoami", headers=console_headers(credential, role="operator")
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "provider": "console",
        "issuer": ISSUER,
        "subject": "entra-person-7",
        "email": "person.seven@example.org",
        "role": "operator",
        "console": "fleet",
    }
    (entry,) = await audit_rows(sessionmaker, "whoami.view")
    assert entry.actor == f"{PERSON_ACTOR} via console fleet"


async def test_asserted_admin_with_an_operator_cap_acts_as_operator(
    admin_client, factory, sessionmaker
):
    _, credential = await factory.console(name="fleet", max_role="operator")
    headers = console_headers(credential, role="admin")
    me = await admin_client.get("/v1/admin/whoami", headers=headers)
    assert me.json()["role"] == "operator"
    refused = await admin_client.post("/v1/admin/tokens", headers=headers, json={})
    assert (refused.status_code, refused.json()["code"]) == (403, "forbidden")
    assert refused.json()["message"] == (
        "this needs the admin role; console fleet is limited to operator"
    )
    (entry,) = await audit_rows(sessionmaker, "admin.refused")
    assert entry.actor == f"{PERSON_ACTOR} via console fleet"
    assert entry.detail == {
        "role": "operator",
        "required": "admin",
        "asserted": "admin",
        "cap": "operator",
    }
    job = await factory.job()
    cancelled = await admin_client.post(f"/v1/admin/jobs/{job.id}/cancel", headers=headers)
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["cancelled_by"] == f"{PERSON_ACTOR} via console fleet"
    (cancel,) = await audit_rows(sessionmaker, "job.cancel")
    assert cancel.actor == f"{PERSON_ACTOR} via console fleet"


async def test_an_asserted_role_below_the_cap_is_kept(admin_client, factory):
    _, credential = await factory.console(name="fleet", max_role="admin")
    response = await admin_client.post(
        "/v1/admin/tokens", headers=console_headers(credential, role="viewer"), json={}
    )
    assert response.status_code == 403
    assert response.json()["message"] == "this needs the admin role; you have viewer"


async def test_a_person_without_an_email_is_audited_as_unknown(
    admin_client, factory, sessionmaker
):
    _, credential = await factory.console(name="fleet")
    headers = console_headers(credential, actor=f"{ISSUER} entra-person-7 -", role="viewer")
    assert (await admin_client.get("/v1/admin/status", headers=headers)).status_code == 200
    (entry,) = await audit_rows(sessionmaker, "status.view")
    assert entry.actor == f"unknown ({ISSUER} entra-person-7) via console fleet"


async def test_a_refused_change_through_a_console_names_the_person(
    admin_client, factory, sessionmaker
):
    _, credential = await factory.console(name="fleet")
    response = await admin_client.post(
        f"/v1/admin/jobs/{uuid.uuid4()}/cancel",
        headers=console_headers(credential, role="operator"),
    )
    assert response.status_code == 404
    (entry,) = await audit_rows(sessionmaker, "admin.change_refused")
    assert entry.actor == f"{PERSON_ACTOR} via console fleet"


# --- the poller ---------------------------------------------------------------------------


async def test_the_poller_reads_as_a_viewer(admin_client, factory):
    await factory.location(name="here")
    _, credential = await factory.console(name="fleet", max_role="admin")
    poller = console_headers(credential, actor="system:poller", role="viewer")
    status = await admin_client.get("/v1/admin/status", headers=poller)
    assert status.status_code == 200, status.text
    me = (await admin_client.get("/v1/admin/whoami", headers=poller)).json()
    assert (me["provider"], me["issuer"], me["subject"], me["email"], me["role"]) == (
        "console",
        None,
        None,
        None,
        "viewer",
    )
    ingest = await admin_client.post("/v1/admin/locations/here/ingest", headers=poller)
    assert (ingest.status_code, ingest.json()["code"]) == (403, "forbidden")
    greedy = await admin_client.get(
        "/v1/admin/status",
        headers=console_headers(credential, actor="system:poller", role="operator"),
    )
    assert (greedy.status_code, greedy.json()["code"]) == (400, "invalid_actor_role")


async def test_the_pollers_successful_reads_are_not_audited(admin_client, factory, sessionmaker):
    _, credential = await factory.console(name="fleet", max_role="admin")
    poller = console_headers(credential, actor="system:poller", role="viewer")
    before = await audit_count(sessionmaker)
    for path in ("/v1/admin/status", "/v1/admin/followers", "/v1/admin/whoami"):
        response = await admin_client.get(path, headers=poller)
        assert response.status_code == 200, (path, response.text)
    assert await audit_count(sessionmaker) == before


async def test_a_persons_read_through_the_same_console_is_audited(
    admin_client, factory, sessionmaker
):
    _, credential = await factory.console(name="fleet", max_role="admin")
    before = await audit_count(sessionmaker)
    response = await admin_client.get(
        "/v1/admin/status", headers=console_headers(credential, role="viewer")
    )
    assert response.status_code == 200, response.text
    assert await audit_count(sessionmaker) == before + 1
    (entry,) = await audit_rows(sessionmaker, "status.view")
    assert entry.actor == f"{PERSON_ACTOR} via console fleet"


async def test_every_refusal_of_the_poller_is_audited(admin_client, factory, sessionmaker):
    await factory.location(name="here")
    _, credential = await factory.console(name="fleet", max_role="admin")
    _, revoked = await factory.console(name="old", revoked=True)
    poller = console_headers(credential, actor="system:poller", role="viewer")
    answers = [
        # 403: the poller is a viewer
        await admin_client.post("/v1/admin/locations/here/ingest", headers=poller),
        # 400: the poller asserts more than viewer
        await admin_client.get(
            "/v1/admin/status",
            headers=console_headers(credential, actor="system:poller", role="operator"),
        ),
        # 404: a read that fails after the role check
        await admin_client.get(
            "/v1/admin/consent/report", headers=poller, params={"location": "nowhere"}
        ),
        # 401: a revoked console's poller
        await admin_client.get(
            "/v1/admin/status",
            headers=console_headers(revoked, actor="system:poller", role="viewer"),
        ),
    ]
    assert [a.status_code for a in answers] == [403, 400, 404, 401]
    async with sessionmaker() as session:
        entries = (await session.scalars(select(AuditEntry))).all()
    assert sorted((e.action, e.actor, e.detail.get("code", "-")) for e in entries) == [
        ("admin.read_refused", "system:poller via console fleet", "not_found"),
        ("admin.refused", "system:poller via console fleet", "-"),
        ("console.refused", "console fleet", "invalid_actor_role"),
        ("console.refused", "console old", "revoked"),
    ]


# --- the credential -------------------------------------------------------------------------


@pytest.mark.parametrize("which", ["unknown", "revoked", "malformed", "oversize", "follower"])
@pytest.mark.parametrize("with_headers", [True, False], ids=["with-headers", "without-headers"])
async def test_a_credential_that_is_not_valid_is_401_whatever_the_headers_say(
    admin_client, factory, sessionmaker, which, with_headers
):
    _, revoked = await factory.console(name="old", revoked=True)
    _, follower_credential = await factory.follower()
    credential = {
        "unknown": new_secret(),
        "revoked": revoked,
        "malformed": "not a credential",
        "oversize": "a" * 100_000,
        "follower": follower_credential,
    }[which]
    headers = (
        console_headers(credential)
        if with_headers
        else console_headers(credential, actor=None, role=None)
    )
    response = await admin_client.get("/v1/admin/whoami", headers=headers)
    assert (response.status_code, response.json()["code"]) == (401, "unauthorized")
    assert response.headers["www-authenticate"] == 'Console error="invalid_token"'
    assert await audit_rows(sessionmaker, "whoami.view") == []
    refusals = await audit_rows(sessionmaker, "console.refused")
    if which == "revoked":  # a revoked console is named; an unknown credential names no one
        assert [(e.actor, e.detail) for e in refusals] == [("console old", {"code": "revoked"})]
    else:
        assert refusals == []


async def test_a_near_miss_is_answered_exactly_like_a_random_guess(admin_client, factory):
    _, credential = await factory.console(name="fleet")
    near = credential[:-1] + ("A" if credential[-1] != "A" else "B")
    answers = [
        await admin_client.get("/v1/admin/whoami", headers=console_headers(guess))
        for guess in (near, new_secret())
    ]
    assert [a.status_code for a in answers] == [401, 401]
    assert answers[0].content == answers[1].content
    assert answers[0].headers["www-authenticate"] == answers[1].headers["www-authenticate"]


async def test_a_console_revoked_mid_session_is_refused_from_its_next_request(
    admin_client, idp
):
    created = await admin_client.post(
        "/v1/admin/consoles",
        headers=idp.bearer("admin"),
        json={"name": "fleet", "max_role": "viewer"},
    )
    headers = console_headers(created.json()["credential"], role="viewer")
    assert (await admin_client.get("/v1/admin/status", headers=headers)).status_code == 200
    revoked = await admin_client.post(
        "/v1/admin/consoles/fleet/revoke", headers=idp.bearer("admin")
    )
    assert revoked.status_code == 200
    after = await admin_client.get("/v1/admin/status", headers=headers)
    assert (after.status_code, after.json()["message"]) == (
        401,
        "this console credential has been revoked",
    )


@pytest.mark.parametrize("scheme", ["console", "CONSOLE"])
async def test_the_console_scheme_is_matched_ignoring_case(admin_client, factory, scheme):
    _, credential = await factory.console(name="fleet")
    response = await admin_client.get(
        "/v1/admin/whoami", headers=console_headers(credential, scheme=scheme)
    )
    assert response.status_code == 200, response.text


async def test_a_console_credential_sent_as_a_bearer_token_is_401(admin_client, factory):
    _, credential = await factory.console(name="fleet")
    response = await admin_client.get(
        "/v1/admin/whoami", headers=console_headers(credential, scheme="Bearer")
    )
    assert (response.status_code, response.headers["www-authenticate"]) == (
        401,
        'Bearer error="invalid_token"',
    )


# --- hostile delegation headers --------------------------------------------------------------

BAD_ACTORS = [
    pytest.param(None, id="missing"),
    pytest.param(b"", id="empty"),
    pytest.param(f"{ISSUER} entra-person-7".encode(), id="two-parts"),
    pytest.param(f"{PERSON} extra".encode(), id="four-parts"),
    pytest.param(f"{ISSUER}\tentra-person-7\tperson.seven@example.org".encode(), id="tabs"),
    pytest.param(
        f"{ISSUER} entra-person-7\x1b[2J person.seven@example.org".encode(), id="escape"
    ),
    pytest.param(f"{ISSUER} entra-person-7 pérson@example.org".encode(), id="non-ascii-email"),
    pytest.param(f"{ISSUER} {'s' * 256} person.seven@example.org".encode(), id="over-long"),
    pytest.param(b"system:reaper", id="other-system-actor"),
]


@pytest.mark.parametrize("actor", BAD_ACTORS)
async def test_a_malformed_actor_is_400_and_audited_without_its_value(
    admin_client, factory, sessionmaker, actor
):
    _, credential = await factory.console(name="fleet")
    response = await admin_client.get(
        "/v1/admin/whoami", headers=raw_headers(credential, actor=actor)
    )
    assert (response.status_code, response.json()["code"]) == (400, "invalid_actor")
    (entry,) = await audit_rows(sessionmaker, "console.refused")
    assert (entry.actor, entry.subject_type, entry.subject_id, entry.detail) == (
        "console fleet",
        "endpoint",
        "GET /v1/admin/whoami",
        {"code": "invalid_actor"},
    )
    assert await audit_rows(sessionmaker, "whoami.view") == []
    if actor:
        value = actor.decode("latin-1")
        assert value not in response.text
        assert value not in await all_audit_text(sessionmaker)


@pytest.mark.parametrize(
    "role", [None, b"", b"superadmin", b"Admin", b"admin,viewer", "operatör".encode()]
)
async def test_a_missing_or_unknown_actor_role_is_400(admin_client, factory, sessionmaker, role):
    _, credential = await factory.console(name="fleet")
    response = await admin_client.get(
        "/v1/admin/whoami", headers=raw_headers(credential, role=role)
    )
    assert (response.status_code, response.json()["code"]) == (400, "invalid_actor_role")
    (entry,) = await audit_rows(sessionmaker, "console.refused")
    assert entry.detail == {"code": "invalid_actor_role"}


async def test_delegation_headers_sent_twice_are_400(admin_client, factory):
    _, credential = await factory.console(name="fleet")
    auth = (b"authorization", f"Console {credential}".encode())
    person = (b"x-swarmscribe-actor", PERSON.encode())
    twice_actor = [auth, person, person, (b"x-swarmscribe-actor-role", b"viewer")]
    twice_role = [
        auth,
        person,
        (b"x-swarmscribe-actor-role", b"viewer"),
        (b"x-swarmscribe-actor-role", b"admin"),
    ]
    answers = [
        await admin_client.get("/v1/admin/whoami", headers=headers)
        for headers in (twice_actor, twice_role)
    ]
    assert [(a.status_code, a.json()["code"]) for a in answers] == [
        (400, "invalid_actor"),
        (400, "invalid_actor_role"),
    ]


@pytest.mark.parametrize("console_first", [True, False], ids=["console-first", "bearer-first"])
async def test_a_bearer_and_a_console_header_together_are_refused(
    admin_client, idp, factory, sessionmaker, console_first
):
    _, credential = await factory.console(name="fleet")
    pair = [
        (b"authorization", f"Console {credential}".encode()),
        (b"authorization", idp.bearer("admin")["Authorization"].encode()),
    ]
    headers = (pair if console_first else pair[::-1]) + [
        (b"x-swarmscribe-actor", PERSON.encode()),
        (b"x-swarmscribe-actor-role", b"admin"),
    ]
    response = await admin_client.get("/v1/admin/whoami", headers=headers)
    assert (response.status_code, response.json()["code"], response.json()["message"]) == (
        401,
        "unauthorized",
        "send exactly one Authorization header",
    )
    assert await audit_rows(sessionmaker, "whoami.view") == []


async def test_actor_headers_are_ignored_on_a_persons_request(admin_client, idp, sessionmaker):
    headers = {
        **idp.bearer("viewer"),
        "X-SwarmScribe-Actor": PERSON,
        "X-SwarmScribe-Actor-Role": "admin",
    }
    me = await admin_client.get("/v1/admin/whoami", headers=headers)
    assert me.status_code == 200, me.text
    body = me.json()
    assert (body["provider"], body["email"], body["role"], body["console"]) == (
        "entra",
        "viewer@example.org",
        "viewer",
        None,
    )
    refused = await admin_client.post("/v1/admin/tokens", headers=headers, json={})
    assert refused.status_code == 403
    garbage = [
        (b"authorization", idp.bearer("viewer")["Authorization"].encode()),
        (b"x-swarmscribe-actor", b"\x1b[2J nonsense"),
        (b"x-swarmscribe-actor-role", b"superadmin"),
    ]
    assert (await admin_client.get("/v1/admin/status", headers=garbage)).status_code == 200
    entries = await audit_rows(sessionmaker, "whoami.view")
    viewer = f"viewer@example.org ({idp.ENTRA_ISSUER} entra-viewer)"
    assert [e.actor for e in entries] == [viewer]
    assert "via console" not in await all_audit_text(sessionmaker)
    assert await audit_rows(sessionmaker, "console.refused") == []


async def test_actor_headers_alone_are_401(admin_client):
    response = await admin_client.get(
        "/v1/admin/whoami",
        headers={"X-SwarmScribe-Actor": PERSON, "X-SwarmScribe-Actor-Role": "admin"},
    )
    assert (response.status_code, response.headers["www-authenticate"]) == (401, "Bearer")


# --- where a console credential is not accepted --------------------------------------------


@pytest.mark.parametrize("scheme", ["Console", "Bearer"])
async def test_a_console_credential_is_refused_on_follower_routes(
    admin_client, factory, sessionmaker, scheme
):
    _, credential = await factory.console(name="fleet")
    job = "00000000-0000-4000-8000-000000000001"
    for path in ("/v1/jobs/claim", f"/v1/jobs/{job}/heartbeat", "/v1/followers/deregister"):
        response = await admin_client.post(
            path, headers=console_headers(credential, scheme=scheme), json={}
        )
        assert (response.status_code, response.json()["code"]) == (401, "unauthorized"), path
    async with sessionmaker() as session:
        assert (await session.scalars(select(Follower))).all() == []


@pytest.mark.parametrize(
    "method, path, body",
    [
        ("GET", "/v1/admin/consoles", None),
        ("POST", "/v1/admin/consoles", {"name": "minted", "max_role": "admin"}),
        ("POST", "/v1/admin/consoles/fleet/revoke", None),
    ],
    ids=["list", "create", "revoke"],
)
async def test_a_console_cannot_manage_console_credentials_even_as_admin(
    admin_client, factory, sessionmaker, method, path, body
):
    _, credential = await factory.console(name="fleet", max_role="admin")
    response = await admin_client.request(
        method, path, headers=console_headers(credential, role="admin"), json=body
    )
    assert (response.status_code, response.json()["code"]) == (403, "forbidden")
    assert "a console credential cannot do this" in response.json()["message"]
    async with sessionmaker() as session:
        rows = (await session.scalars(select(ConsoleCredential))).all()
    assert [(r.name, r.revoked_at) for r in rows] == [("fleet", None)]
    (entry,) = await audit_rows(sessionmaker, "admin.refused")
    assert entry.actor == f"{PERSON_ACTOR} via console fleet"
    assert entry.detail["console_allowed"] is False


async def test_console_credentials_never_reach_the_log_or_the_audit_log(
    admin_client, factory, sessionmaker, caplog
):
    _, good = await factory.console(name="fleet")
    _, revoked = await factory.console(name="old", revoked=True)
    unknown = new_secret()
    with caplog.at_level("DEBUG"):
        await admin_client.get("/v1/admin/whoami", headers=console_headers(good))
        await admin_client.get("/v1/admin/whoami", headers=console_headers(good, actor="a b"))
        await admin_client.post(
            "/v1/admin/tokens", headers=console_headers(good, role="viewer"), json={}
        )
        await admin_client.get("/v1/admin/whoami", headers=console_headers(revoked))
        await admin_client.get("/v1/admin/whoami", headers=console_headers(unknown))
    audit_text = await all_audit_text(sessionmaker)
    assert "via console fleet" in audit_text
    for credential in (good, revoked, unknown):
        assert credential not in caplog.text
        assert credential not in audit_text


# --- additions: owner rulings and review carry-overs --------------------------------------


VIEW_ACTIONS = {
    "/v1/admin/locations": "locations.view",
    "/v1/admin/jobs": "jobs.view",
    "/v1/admin/consent/report": "consent.view",
}


@pytest.mark.parametrize("path", list(VIEW_ACTIONS))
async def test_the_pollers_reads_of_other_routes_are_audited(
    admin_client, factory, sessionmaker, path
):
    await factory.location(name="here")
    _, credential = await factory.console(name="fleet", max_role="admin")
    poller = console_headers(credential, actor="system:poller", role="viewer")
    before = await audit_count(sessionmaker)
    response = await admin_client.get(path, headers=poller)
    assert response.status_code == 200, response.text
    assert await audit_count(sessionmaker) == before + 1
    entries = await audit_rows(sessionmaker, VIEW_ACTIONS[path])
    assert [e.actor for e in entries] == ["system:poller via console fleet"]


async def test_the_audit_string_of_a_console_request_is_exact(admin_client, factory, sessionmaker):
    await factory.location(name="here")
    _, credential = await factory.console(name="fleet")
    person = await admin_client.get(
        "/v1/admin/locations", headers=console_headers(credential, role="viewer")
    )
    poller = await admin_client.get(
        "/v1/admin/locations",
        headers=console_headers(credential, actor="system:poller", role="viewer"),
    )
    assert (person.status_code, poller.status_code) == (200, 200)
    entries = await audit_rows(sessionmaker, "locations.view")
    assert sorted(e.actor for e in entries) == sorted(
        [PERSON_ACTOR + " via console fleet", "system:poller via console fleet"]
    )


def test_a_delegated_actor_is_named_like_the_same_person_signed_in():
    from swarmscribe_leader.auth.consoles import DelegatedActor
    from swarmscribe_leader.auth.oidc import Identity

    for email in ("person.seven@example.org", None):
        identity = Identity(
            provider="entra", issuer=ISSUER, subject="entra-person-7", email=email, claims={}
        )
        delegate = DelegatedActor(issuer=ISSUER, subject="entra-person-7", email=email)
        assert delegate.name == identity.actor


@pytest.mark.parametrize(
    "asserted, cap, effective",
    [
        ("viewer", "viewer", "viewer"),
        ("viewer", "operator", "viewer"),
        ("operator", "viewer", "viewer"),
        ("admin", "viewer", "viewer"),
        ("operator", "operator", "operator"),
        ("admin", "operator", "operator"),
        ("viewer", "admin", "viewer"),
        ("admin", "admin", "admin"),
    ],
)
async def test_the_effective_role_is_the_lower_of_asserted_and_cap(
    admin_client, factory, asserted, cap, effective
):
    _, credential = await factory.console(name="fleet", max_role=cap)
    response = await admin_client.get(
        "/v1/admin/whoami", headers=console_headers(credential, role=asserted)
    )
    assert response.status_code == 200, response.text
    assert response.json()["role"] == effective
