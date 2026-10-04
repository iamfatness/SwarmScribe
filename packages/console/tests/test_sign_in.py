import base64
import hashlib
import logging
import re
import time
from datetime import timedelta
from urllib.parse import urlencode

import httpx
import pytest
from console_testkit import (
    ENTRA_CLIENT,
    ENTRA_ISSUER,
    ENTRA_SECRET,
    GOOGLE_ISSUER,
    GROUPS,
    all_rows_text,
    cookie_attributes,
    sign_in,
)
from sqlalchemy import select, update
from swarmscribe_console.app import create_app
from swarmscribe_console.db.models import AuditEntry, ConsoleSession, LoginAttempt
from swarmscribe_console.oidc import (
    CodeExchangeFailed,
    code_challenge,
    exchange_code,
    web_providers,
)
from swarmscribe_console.principals import principals_for
from swarmscribe_console.sessions import LOGIN_COOKIE, SESSION_COOKIE
from swarmscribe_leader.auth.oidc import Identity
from swarmscribe_leader.auth.secrets import hash_secret
from swarmscribe_leader.clock import utcnow


def _refresh_target(answer: httpx.Response) -> str:
    found = re.search(r'http-equiv="refresh" content="0;url=([^"]*)"', answer.text)
    assert found, answer.text
    return found.group(1).replace("&amp;", "&")


@pytest.fixture
async def operators(factory):
    await factory.grant("operator", "all", "entra_group", GROUPS["operator"])


async def _sessions(sessionmaker) -> list[ConsoleSession]:
    async with sessionmaker() as session:
        return list((await session.scalars(select(ConsoleSession))).all())


# --- the happy paths --------------------------------------------------------------------


async def test_an_entra_person_with_a_grant_signs_in(client, idp, operators, sessionmaker):
    answer = await sign_in(client, idp, groups=[GROUPS["operator"]])
    assert answer.status_code == 200
    assert _refresh_target(answer) == "/"
    (row,) = await _sessions(sessionmaker)
    assert (row.provider, row.issuer, row.subject, row.email) == (
        "entra",
        ENTRA_ISSUER,
        "entra-person-1",
        "person@example.org",
    )
    assert row.principals == [f"entra_group:{GROUPS['operator']}"]
    me = await client.get("/api/session")
    assert me.status_code == 200
    assert me.json()["email"] == "person@example.org"


async def test_the_session_cookie_has_the_spec_flags(client, idp, operators):
    answer = await sign_in(client, idp, groups=[GROUPS["operator"]])
    cookie = cookie_attributes(answer, SESSION_COOKIE)
    assert cookie is not None
    assert SESSION_COOKIE.startswith("__Host-")
    assert "httponly" in cookie
    assert "secure" in cookie
    assert cookie["samesite"].lower() == "strict"
    assert cookie["path"] == "/"
    assert cookie["max-age"] == str(8 * 3600)
    assert "domain" not in cookie
    login = cookie_attributes(answer, LOGIN_COOKIE)
    assert login is not None and login["max-age"] == "0"


async def test_the_login_cookie_is_lax_short_lived_and_binds_the_browser(client):
    started = await client.get("/auth/login", params={"provider": "entra"})
    cookie = cookie_attributes(started, LOGIN_COOKIE)
    assert cookie["samesite"].lower() == "lax"
    assert ("httponly" in cookie, "secure" in cookie) == (True, True)
    assert cookie["max-age"] == "600"


async def test_a_google_workspace_person_signs_in_by_domain(client, idp, factory, sessionmaker):
    await factory.grant("viewer", "all", "domain", "example.org")
    answer = await sign_in(client, idp, provider="google", hd="example.org")
    assert answer.status_code == 200
    (row,) = await _sessions(sessionmaker)
    assert row.issuer == GOOGLE_ISSUER
    assert set(row.principals) == {"email:person@example.org", "domain:example.org"}


async def test_a_google_account_outside_the_workspace_gets_no_domain(
    client, idp, factory, sessionmaker
):
    await factory.grant("viewer", "all", "domain", "example.org")
    answer = await sign_in(client, idp, provider="google")  # no hd claim
    assert answer.status_code == 403
    assert await _sessions(sessionmaker) == []


async def test_google_groups_become_principals(client, idp, factory, google_groups, sessionmaker):
    google_groups.groups["person@example.org"] = {"Operators@Example.org"}
    await factory.grant("operator", "all", "google_group", "operators@example.org")
    assert (await sign_in(client, idp, provider="google")).status_code == 200
    (row,) = await _sessions(sessionmaker)
    assert "google_group:operators@example.org" in row.principals


async def test_entra_group_overage_is_resolved_through_graph(
    client, idp, operators, graph, sessionmaker
):
    graph.groups["00000000-0000-4000-8000-0000000000a1"] = {GROUPS["operator"].upper()}
    answer = await sign_in(client, idp, groups=None, hasgroups=True)
    assert answer.status_code == 200
    (row,) = await _sessions(sessionmaker)
    assert row.principals == [f"entra_group:{GROUPS['operator']}"]


async def test_a_directory_outage_is_a_retryable_refusal(client, idp, operators, graph):
    graph.failing = True
    answer = await sign_in(client, idp, groups=None, hasgroups=True)
    assert answer.status_code == 503


async def test_the_request_uses_pkce_s256_and_asks_for_no_refresh_token(client, idp, operators):
    started = await client.get("/auth/login", params={"provider": "entra"})
    location = httpx.URL(started.headers["location"])
    params = dict(location.params)
    assert location.host == "login.microsoftonline.com"
    assert location.path.endswith("/oauth2/v2.0/authorize")
    assert params["response_type"] == "code"
    assert params["client_id"] == ENTRA_CLIENT
    assert params["redirect_uri"] == "https://console.test/auth/callback"
    assert params["scope"] == "openid email profile"
    assert params["code_challenge_method"] == "S256"
    assert len(params["state"]) == 43 and len(params["nonce"]) == 43
    back = await client.get(idp.authorize(str(location), groups=[GROUPS["operator"]]))
    assert back.status_code == 200
    (exchange,) = idp.exchanges
    verifier = exchange["code_verifier"]
    assert 43 <= len(verifier) <= 128
    digest = hashlib.sha256(verifier.encode()).digest()
    assert base64.urlsafe_b64encode(digest).rstrip(b"=").decode() == params["code_challenge"]


async def test_no_token_code_or_verifier_is_kept_after_sign_in(
    client, idp, operators, engine, sessionmaker
):
    started = await client.get("/auth/login", params={"provider": "entra"})
    callback = idp.authorize(started.headers["location"], groups=[GROUPS["operator"]])
    code = dict(httpx.URL(callback).params)["code"]
    assert (await client.get(callback)).status_code == 200
    stored = await all_rows_text(engine)
    assert len(idp.issued) == 3  # ID, access and refresh token
    for secret in [*idp.issued, code, idp.exchanges[0]["code_verifier"]]:
        assert secret not in stored
    async with sessionmaker() as session:
        assert (await session.scalars(select(LoginAttempt))).all() == []


async def test_signing_in_is_audited(client, idp, operators, sessionmaker):
    await sign_in(client, idp, groups=[GROUPS["operator"]])
    async with sessionmaker() as session:
        (entry,) = (await session.scalars(select(AuditEntry))).all()
    assert entry.action == "sign_in"
    assert entry.actor == f"person@example.org ({ENTRA_ISSUER} entra-person-1)"


# --- refusals ---------------------------------------------------------------------------


async def test_a_person_with_no_grant_gets_no_session(client, idp, sessionmaker):
    answer = await sign_in(client, idp, groups=[GROUPS["viewer"]])
    assert answer.status_code == 403
    assert cookie_attributes(answer, SESSION_COOKIE) is None
    assert await _sessions(sessionmaker) == []
    async with sessionmaker() as session:
        (entry,) = (await session.scalars(select(AuditEntry))).all()
    assert (entry.action, entry.outcome) == ("sign_in.refused", "no_access")


async def test_a_console_admin_without_a_leader_role_may_sign_in(client, idp, factory):
    await factory.console_admin("entra_group", GROUPS["console"])
    assert (await sign_in(client, idp, groups=[GROUPS["console"]])).status_code == 200


async def test_a_callback_is_single_use(client, idp, operators, sessionmaker):
    started = await client.get("/auth/login", params={"provider": "entra"})
    callback = idp.authorize(started.headers["location"], groups=[GROUPS["operator"]])
    login_cookie = client.cookies.get(LOGIN_COOKIE)
    assert (await client.get(callback)).status_code == 200
    client.cookies.set(LOGIN_COOKIE, login_cookie, domain="console.test", path="/")
    replay = await client.get(callback)
    assert replay.status_code == 400
    assert len(await _sessions(sessionmaker)) == 1


async def test_a_callback_in_another_browser_is_refused_and_burns_the_attempt(
    client, new_client, idp, operators, sessionmaker
):
    started = await client.get("/auth/login", params={"provider": "entra"})
    callback = idp.authorize(started.headers["location"], groups=[GROUPS["operator"]])
    elsewhere = new_client()
    assert (await elsewhere.get(callback)).status_code == 400
    assert (await client.get(callback)).status_code == 400
    assert await _sessions(sessionmaker) == []


@pytest.mark.parametrize(
    "query",
    [
        "code=abc&state=" + "A" * 43,
        "code=abc&state=not-a-state",
        "code=abc",
        "state=",
        "",
    ],
)
async def test_a_forged_or_missing_state_is_refused(client, idp, operators, query, sessionmaker):
    await client.get("/auth/login", params={"provider": "entra"})
    assert (await client.get(f"/auth/callback?{query}")).status_code == 400
    assert await _sessions(sessionmaker) == []


async def test_two_state_values_are_refused(client, idp, operators):
    started = await client.get("/auth/login", params={"provider": "entra"})
    callback = idp.authorize(started.headers["location"], groups=[GROUPS["operator"]])
    assert (await client.get(callback + "&state=" + "B" * 43)).status_code == 400


async def test_an_expired_attempt_is_refused(client, idp, operators, sessionmaker):
    started = await client.get("/auth/login", params={"provider": "entra"})
    async with sessionmaker() as session:
        await session.execute(update(LoginAttempt).values(expires_at=utcnow()))
        await session.commit()
    callback = idp.authorize(started.headers["location"], groups=[GROUPS["operator"]])
    assert (await client.get(callback)).status_code == 400


async def test_a_token_for_another_nonce_is_refused(client, idp, operators, sessionmaker):
    answer = await sign_in(client, idp, groups=[GROUPS["operator"]], nonce="attacker-nonce")
    assert answer.status_code == 401
    assert await _sessions(sessionmaker) == []


@pytest.mark.parametrize(
    "claims",
    [{"aud": "another-client"}, {"tid": "ffffffff-0000-4000-8000-000000000000"}],
    ids=["wrong-audience", "other-tenant"],
)
async def test_a_token_failing_the_leaders_checks_is_refused(client, idp, operators, claims):
    answer = await sign_in(client, idp, groups=[GROUPS["operator"]], **claims)
    assert answer.status_code == 401


async def test_a_refused_code_exchange_makes_no_session(client, idp, operators, sessionmaker):
    idp.token_status = 400
    assert (await sign_in(client, idp, groups=[GROUPS["operator"]])).status_code == 502
    assert await _sessions(sessionmaker) == []


async def test_a_provider_error_is_shown_and_burns_the_attempt(client, idp, sessionmaker):
    started = await client.get("/auth/login", params={"provider": "entra"})
    state = dict(httpx.URL(started.headers["location"]).params)["state"]
    answer = await client.get(f"/auth/callback?error=access_denied&state={state}")
    assert answer.status_code == 400
    assert "<script" not in answer.text
    async with sessionmaker() as session:
        assert (await session.scalars(select(LoginAttempt))).all() == []


async def test_an_unknown_provider_is_not_found(client):
    assert (await client.get("/auth/login", params={"provider": "okta"})).status_code == 404


async def test_the_configured_providers_are_listed(client):
    assert (await client.get("/auth/providers")).json() == {"providers": ["entra", "google"]}


# --- redirects and fixation -------------------------------------------------------------


@pytest.mark.parametrize(
    "return_to", ["/leaders/eu-1?tab=jobs", "/", "/fleet#filter=env%3Dprod"]
)
async def test_a_local_return_url_is_kept(client, idp, operators, return_to):
    answer = await sign_in(client, idp, return_to=return_to, groups=[GROUPS["operator"]])
    assert _refresh_target(answer) == return_to


@pytest.mark.parametrize(
    "return_to",
    [
        "https://evil.example/",
        "//evil.example",
        "/\\evil.example",
        "javascript:alert(1)",
        "/\t/evil.example",
        "/x'><script>alert(1)</script>",
        "evil.example",
        "",
        "/" + "x" * 600,
        "/%2fevil.example",
        "/%5cevil.example",
        "/%252fevil.example",
        "/%2F%2Fevil.example",
        "%2f%2fevil.example",
        "/a%0d%0aSet-Cookie:x=1",
        "/\u202eevil",
    ],
)
async def test_any_other_return_url_lands_on_the_root(client, idp, operators, return_to):
    answer = await sign_in(client, idp, return_to=return_to, groups=[GROUPS["operator"]])
    assert answer.status_code == 200
    assert _refresh_target(answer) == "/"
    assert "evil.example" not in answer.text


async def test_a_return_url_added_to_the_callback_is_ignored(client, idp, operators):
    started = await client.get("/auth/login", params={"provider": "entra"})
    callback = idp.authorize(started.headers["location"], groups=[GROUPS["operator"]])
    answer = await client.get(callback + "&return_to=https://evil.example/")
    assert _refresh_target(answer) == "/"


async def test_signing_in_never_adopts_the_cookie_the_browser_brought(
    client, new_client, idp, operators, factory, sessionmaker
):
    victim_planted = "P" * 43
    client.cookies.set(SESSION_COOKIE, victim_planted, domain="console.test", path="/")
    await sign_in(client, idp, groups=[GROUPS["operator"]])
    assert client.cookies.get(SESSION_COOKIE) != victim_planted

    other = new_client()
    await factory.person(other, subject="someone-else")
    existing = other.cookies.get(SESSION_COOKIE)
    await sign_in(other, idp, groups=[GROUPS["operator"]])
    fresh = other.cookies.get(SESSION_COOKIE)
    assert fresh != existing
    stale = new_client()
    stale.cookies.set(SESSION_COOKIE, existing, domain="console.test", path="/")
    assert (await stale.get("/api/session")).status_code == 401
    assert len(await _sessions(sessionmaker)) == 2


# --- hostile tokens and token endpoints -------------------------------------------------


def _unix(offset: int) -> int:
    return int(time.time()) + offset


@pytest.mark.parametrize(
    "claims",
    [
        {"exp": _unix(-3600), "iat": _unix(-7200)},
        {"iss": "https://login.microsoftonline.com/ffffffff-0000-4000-8000-000000000000/v2.0"},
        {"iss": "https://evil.example/"},
        {"sub": None},
    ],
    ids=["expired", "other-tenant-issuer", "unknown-issuer", "no-subject"],
)
async def test_an_entra_token_that_is_expired_or_from_the_wrong_issuer_is_refused(
    client, idp, operators, sessionmaker, claims
):
    answer = await sign_in(client, idp, groups=[GROUPS["operator"]], **claims)
    assert answer.status_code == 401
    assert cookie_attributes(answer, SESSION_COOKIE) is None
    assert await _sessions(sessionmaker) == []


async def test_a_token_signed_with_another_key_is_refused(client, idp, operators, sessionmaker):
    answer = await sign_in(client, idp, groups=[GROUPS["operator"]], signed_with="rogue")
    assert answer.status_code == 401
    assert await _sessions(sessionmaker) == []


@pytest.mark.parametrize("verified", [False, "false", None, 1])
async def test_a_google_email_that_is_not_verified_is_refused(
    client, idp, factory, sessionmaker, verified
):
    await factory.grant("viewer", "all", "email", "person@example.org")
    answer = await sign_in(client, idp, provider="google", email_verified=verified)
    assert answer.status_code == 401
    assert await _sessions(sessionmaker) == []


async def test_a_google_token_for_another_audience_is_refused(client, idp, factory, sessionmaker):
    await factory.grant("viewer", "all", "domain", "example.org")
    answer = await sign_in(client, idp, provider="google", hd="example.org", aud="another")
    assert answer.status_code == 401
    assert await _sessions(sessionmaker) == []


@pytest.fixture
async def domain_client(engine, make_settings, idp, graph, google_groups):
    """A console that only accepts one Google Workspace domain."""
    application = create_app(
        make_settings(google_hosted_domain="example.org"),
        fetch=idp.fetch,
        idp_transport=idp.transport,
        graph=graph,
        google_groups=google_groups,
    )
    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="https://console.test"
        ) as made:
            yield made


@pytest.mark.parametrize("claims", [{}, {"hd": "evil.example"}, {"hd": ""}])
async def test_a_hosted_domain_rule_needs_the_matching_hd_claim(
    domain_client, idp, factory, sessionmaker, claims
):
    await factory.grant("viewer", "all", "domain", "example.org")
    answer = await sign_in(domain_client, idp, provider="google", **claims)
    assert answer.status_code == 401
    assert await _sessions(sessionmaker) == []


async def test_a_hosted_domain_rule_accepts_the_right_hd_claim(domain_client, idp, factory):
    await factory.grant("viewer", "all", "domain", "example.org")
    answer = await sign_in(domain_client, idp, provider="google", hd="Example.ORG")
    assert answer.status_code == 200


def test_an_empty_hosted_domain_never_means_unrestricted(make_settings):
    with pytest.raises(ValueError):
        make_settings(google_hosted_domain="@")
    settings = make_settings()
    object.__setattr__(settings, "google_hosted_domain", "")  # as a validator bug might leave it
    with pytest.raises(ValueError):
        web_providers(settings)


async def test_a_token_endpoint_that_answers_with_a_web_page_is_a_clean_failure(
    client, idp, operators, sessionmaker
):
    idp.token_html = True
    answer = await sign_in(client, idp, groups=[GROUPS["operator"]])
    assert answer.status_code == 502
    assert "Service unavailable" not in answer.text  # nothing the provider sent is echoed
    assert await _sessions(sessionmaker) == []


@pytest.mark.parametrize("status", [500, 503, 401])
async def test_a_failing_token_endpoint_is_a_clean_failure(
    client, idp, operators, sessionmaker, status
):
    idp.token_status = status
    assert (await sign_in(client, idp, groups=[GROUPS["operator"]])).status_code == 502
    assert await _sessions(sessionmaker) == []


async def test_a_pkce_verifier_that_does_not_match_the_challenge_is_refused(
    client, idp, operators, sessionmaker
):
    started = await client.get("/auth/login", params={"provider": "entra"})
    callback = idp.authorize(started.headers["location"], groups=[GROUPS["operator"]])
    code = dict(httpx.URL(callback).params)["code"]
    idp.codes[code]["challenge"] = "attacker-challenge"  # the provider remembers another one
    assert (await client.get(callback)).status_code == 502
    assert await _sessions(sessionmaker) == []


async def test_an_authorization_code_works_once(app, idp):
    provider = app.state.web_providers["entra"]
    verifier = "v" * 64
    redirect = "https://console.test/auth/callback"
    params = {"nonce": "n", "code_challenge": code_challenge(verifier), "redirect_uri": redirect}
    url = f"https://login.microsoftonline.com/x/oauth2/v2.0/authorize?{urlencode(params)}&state=s"
    code = dict(httpx.URL(idp.authorize(url, groups=[])).params)["code"]
    first = await exchange_code(
        provider, code=code, verifier=verifier, redirect_uri=redirect, transport=idp.transport
    )
    assert first
    with pytest.raises(CodeExchangeFailed):
        await exchange_code(
            provider, code=code, verifier=verifier, redirect_uri=redirect, transport=idp.transport
        )


async def test_a_token_for_the_other_provider_is_refused(client, idp, operators, sessionmaker):
    """Started at Entra ID, answered with a valid Google token."""
    started = await client.get("/auth/login", params={"provider": "entra"})
    callback = idp.authorize(started.headers["location"], groups=[GROUPS["operator"]])
    code = dict(httpx.URL(callback).params)["code"]
    idp.codes[code]["provider"] = "google"
    assert (await client.get(callback)).status_code in (401, 502)
    assert await _sessions(sessionmaker) == []


# --- nothing secret is logged -----------------------------------------------------------


async def test_no_token_code_cookie_or_verifier_reaches_the_logs(client, idp, operators, caplog):
    caplog.set_level(logging.DEBUG)
    # The test client's own httpx logger prints request URLs; main.py's LOGGING quiets it
    # (and runs uvicorn with access_log=False), so mirror that: this checks the console's logs.
    caplog.set_level(logging.WARNING, logger="httpx")
    caplog.set_level(logging.WARNING, logger="httpcore")
    started = await client.get("/auth/login", params={"provider": "entra"})
    callback = idp.authorize(started.headers["location"], groups=[GROUPS["operator"]])
    code = dict(httpx.URL(callback).params)["code"]
    state = dict(httpx.URL(callback).params)["state"]
    login_cookie = client.cookies.get(LOGIN_COOKIE)
    answer = await client.get(callback)
    session_cookie = client.cookies.get(SESSION_COOKIE)
    verifier = idp.exchanges[0]["code_verifier"]
    # and the failing paths, which log the most
    idp.token_status = 400
    await sign_in(client, idp, groups=[GROUPS["operator"]])
    idp.token_status = 200
    await sign_in(client, idp, groups=[GROUPS["operator"]], nonce="bad")
    assert answer.status_code == 200
    for secret in [*idp.issued, code, state, login_cookie, session_cookie, verifier, ENTRA_SECRET]:
        assert secret and secret not in caplog.text
    for secret in [*idp.issued, code, session_cookie, verifier]:
        assert secret not in answer.text


# --- principals -------------------------------------------------------------------------


async def test_principals_are_lowercase_ascii_whatever_the_provider_sends():
    entra = Identity(
        "entra", ENTRA_ISSUER, "s", "A@B.C", {"groups": [GROUPS["operator"].upper(), "not-a-guid"]}
    )
    assert await principals_for(entra, graph=None, google_groups=None) == {
        f"entra_group:{GROUPS['operator']}"
    }

    class Groups:
        async def group_emails(self, email):
            return {"Ops@Example.ORG", "Kelvin@example.org", "café@example.org"}

    google = Identity(
        "google",
        GOOGLE_ISSUER,
        "s",
        "Person@Example.ORG",
        {"hd": "Example.ORG", "email_verified": True},
    )
    found = await principals_for(google, graph=None, google_groups=Groups())
    assert found == {
        "google_group:ops@example.org",
        "email:person@example.org",
        "domain:example.org",
    }
    assert all(p.isascii() and p == p.lower() for p in found)  # Kelvin sign never becomes "k"

    lookalike = Identity("google", GOOGLE_ISSUER, "s", "p@Kexample.org", {"hd": "example.org"})
    assert await principals_for(lookalike, graph=None, google_groups=None) == frozenset()


# --- fixation: the session the browser already had ---------------------------------------


async def test_starting_a_sign_in_ends_the_session_the_browser_brought(
    client, idp, factory, sessionmaker
):
    await factory.person(client, subject="earlier")
    brought = client.cookies.get(SESSION_COOKIE)
    assert len(await _sessions(sessionmaker)) == 1
    started = await client.get("/auth/login", params={"provider": "entra"})
    assert started.status_code == 302
    assert await _sessions(sessionmaker) == []
    cleared = cookie_attributes(started, SESSION_COOKIE)
    assert cleared is not None and cleared["max-age"] == "0"
    assert client.cookies.get(SESSION_COOKIE) != brought
    async with sessionmaker() as session:
        attempt = (await session.scalars(select(LoginAttempt))).one()
    assert attempt.prior_session_hash == hash_secret(brought)  # only the hash is kept
    assert attempt.prior_session_hash != brought


async def test_the_callback_ends_a_session_that_the_login_cookie_could_not_reach(
    client, idp, operators, factory, sessionmaker
):
    """The Strict cookie may not accompany a cross-site return, so the callback does not rely
    on seeing it: the session named when the sign-in started is deleted by hash."""
    await factory.person(client, subject="earlier")
    brought = client.cookies.get(SESSION_COOKIE)
    started = await client.get("/auth/login", params={"provider": "entra"})
    async with sessionmaker() as session:  # the old session lives on, as if login missed it
        session.add(
            ConsoleSession(
                id_hash=hash_secret(brought),
                provider="entra",
                issuer=ENTRA_ISSUER,
                subject="earlier",
                email=None,
                principals=[],
                created_at=utcnow(),
                last_seen_at=utcnow(),
                expires_at=utcnow() + timedelta(hours=1),
            )
        )
        await session.commit()
    assert client.cookies.get(SESSION_COOKIE) is None  # the callback arrives without it
    callback = idp.authorize(started.headers["location"], groups=[GROUPS["operator"]])
    assert (await client.get(callback)).status_code == 200
    (row,) = await _sessions(sessionmaker)
    assert row.subject == "entra-person-1"


async def test_starting_a_sign_in_prunes_expired_pending_sign_ins(client, sessionmaker):
    await client.get("/auth/login", params={"provider": "entra"})
    async with sessionmaker() as session:
        await session.execute(update(LoginAttempt).values(expires_at=utcnow()))
        await session.commit()
    await client.get("/auth/login", params={"provider": "google"})
    async with sessionmaker() as session:
        (left,) = (await session.scalars(select(LoginAttempt))).all()
    assert left.provider == "google"


async def test_the_auth_routes_need_no_session_and_no_csrf_token(client):
    assert (await client.get("/auth/providers")).status_code == 200
    assert (await client.get("/auth/login", params={"provider": "google"})).status_code == 302
    assert (await client.get("/auth/callback")).status_code == 400  # refused by state, not auth
