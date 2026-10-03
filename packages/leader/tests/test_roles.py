import json
from urllib.parse import parse_qsl

import httpx
import jwt as pyjwt
import pytest
from pydantic import SecretStr
from swarmscribe_leader.auth.oidc import Identity
from swarmscribe_leader.auth.roles import (
    GoogleCloudIdentity,
    MicrosoftGraph,
    RoleLookupFailed,
    RoleMapping,
    RoleResolver,
    at_least,
)

OID = "00000000-0000-4000-8000-0000000000a1"


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def make_resolver(sign_in_settings, graph, google_groups, clock):
    def make(*, settings=None, with_graph=True, with_groups=True) -> RoleResolver:
        return RoleResolver(
            RoleMapping.from_settings(settings or sign_in_settings()),
            graph=graph if with_graph else None,
            google_groups=google_groups if with_groups else None,
            clock=clock,
        )

    return make


def entra_identity(idp, *, groups=(), email="person@example.org", **claims) -> Identity:
    return Identity(
        provider="entra",
        issuer=idp.ENTRA_ISSUER,
        subject="entra-person-1",
        email=email,
        claims={"groups": list(groups), "oid": OID, **claims},
    )


WORKSPACE = object()  # hd: the email's own domain, as Google sets it for Workspace accounts


def google_identity(idp, email="person@example.org", *, hd=WORKSPACE) -> Identity:
    # One subject per email: roles are cached per (issuer, subject).
    if hd is WORKSPACE:
        hd = email.rpartition("@")[2]
    return Identity(
        provider="google",
        issuer=idp.GOOGLE_ISSUER,
        subject=f"google-{email}-{hd}",
        email=email,
        claims={} if hd is None else {"hd": hd},
    )


def test_roles_are_cumulative():
    assert at_least("admin", "viewer") and at_least("operator", "operator")
    assert not at_least("viewer", "operator")
    assert not at_least(None, "viewer")


@pytest.mark.parametrize("role", ["viewer", "operator", "admin"])
async def test_an_entra_group_gives_its_role(make_resolver, idp, role):
    identity = entra_identity(idp, groups=[idp.ENTRA_GROUPS[role]])
    assert await make_resolver().role_for(identity) == role


async def test_the_highest_role_wins_and_group_ids_ignore_case(make_resolver, idp):
    groups = [idp.ENTRA_GROUPS["viewer"], idp.ENTRA_GROUPS["admin"].upper()]
    assert await make_resolver().role_for(entra_identity(idp, groups=groups)) == "admin"


async def test_no_matching_group_is_no_role(make_resolver, idp):
    identity = entra_identity(idp, groups=["99999999-0000-4000-8000-000000000000"])
    assert await make_resolver().role_for(identity) is None


async def test_email_lists_do_not_apply_to_entra_sign_ins(make_resolver, idp):
    # Entra's email claim is not verified by Entra: only group ids count.
    assert await make_resolver().role_for(entra_identity(idp, email="admin@example.org")) is None


async def test_group_overage_is_resolved_through_microsoft_graph(make_resolver, idp, graph):
    graph.groups[OID] = {idp.ENTRA_GROUPS["operator"]}
    identity = entra_identity(idp, _claim_names={"groups": "src1"})
    assert await make_resolver().role_for(identity) == "operator"
    assert graph.calls == [OID]


async def test_group_overage_without_graph_credentials_is_no_role(make_resolver, idp):
    identity = entra_identity(idp, hasgroups=True)
    assert await make_resolver(with_graph=False).role_for(identity) is None


async def test_google_groups_emails_and_domains_all_count(
    make_resolver, idp, google_groups, sign_in_settings
):
    settings = sign_in_settings(role_operator_domains=("example.org",))
    resolver = make_resolver(settings=settings)
    google_groups.groups["person@example.org"] = {"Viewers@Example.org"}
    assert await resolver.role_for(google_identity(idp)) == "operator"  # the domain wins
    assert await resolver.role_for(google_identity(idp, "admin@example.org")) == "admin"


async def test_google_without_a_group_reader_uses_the_lists_only(make_resolver, idp, google_groups):
    resolver = make_resolver(with_groups=False)
    google_groups.groups["viewer@example.org"] = {"admins@example.org"}
    assert await resolver.role_for(google_identity(idp, "viewer@example.org")) == "viewer"
    assert google_groups.calls == []


async def test_roles_are_cached_for_five_minutes_per_person(
    make_resolver, idp, google_groups, clock
):
    resolver = make_resolver()
    google_groups.groups["person@example.org"] = {"admins@example.org"}
    assert await resolver.role_for(google_identity(idp)) == "admin"
    google_groups.groups["person@example.org"] = set()
    clock.now += 299
    assert await resolver.role_for(google_identity(idp)) == "admin"
    clock.now += 2
    assert await resolver.role_for(google_identity(idp)) is None
    assert len(google_groups.calls) == 2


async def test_a_failed_lookup_raises_and_is_not_cached(make_resolver, idp, google_groups):
    resolver = make_resolver()
    google_groups.failing = True
    with pytest.raises(RoleLookupFailed):
        await resolver.role_for(google_identity(idp, "viewer@example.org"))
    google_groups.failing = False
    assert await resolver.role_for(google_identity(idp, "viewer@example.org")) == "viewer"


async def test_microsoft_graph_uses_the_apps_own_credentials():
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/oauth2/v2.0/token"):
            return httpx.Response(200, json={"access_token": "app-token", "expires_in": 3600})
        return httpx.Response(200, json={"value": ["G-ONE", "g-two"]})

    graph = MicrosoftGraph(
        "tenant-1", "client-1", SecretStr("app-secret"), transport=httpx.MockTransport(answer)
    )
    assert await graph.member_object_ids(OID) == {"g-one", "g-two"}
    assert await graph.member_object_ids(OID) == {"g-one", "g-two"}
    token_requests = [r for r in seen if r.url.path.endswith("/token")]
    assert len(token_requests) == 1  # the app token is reused
    assert token_requests[0].url.host == "login.microsoftonline.com"
    form = dict(parse_qsl(token_requests[0].content.decode()))
    assert (form["grant_type"], form["scope"], form["client_secret"]) == (
        "client_credentials",
        "https://graph.microsoft.com/.default",
        "app-secret",
    )
    lookup = [r for r in seen if r.url.path.endswith("/getMemberObjects")][0]
    assert (lookup.url.host, lookup.url.path) == (
        "graph.microsoft.com",
        f"/v1.0/users/{OID}/getMemberObjects",
    )
    assert lookup.headers["authorization"] == "Bearer app-token"
    assert json.loads(lookup.content) == {"securityEnabledOnly": False}


async def test_a_graph_failure_is_a_lookup_failure_that_names_no_secret():
    graph = MicrosoftGraph(
        "tenant-1",
        "client-1",
        SecretStr("app-secret"),
        transport=httpx.MockTransport(lambda request: httpx.Response(503)),
    )
    with pytest.raises(RoleLookupFailed) as excinfo:
        await graph.member_object_ids(OID)
    assert "app-secret" not in str(excinfo.value)


async def test_google_groups_are_read_with_the_service_account(signing_keys, sign_in_settings):
    account = sign_in_settings().google_service_account_key()
    pages = {
        None: {"memberships": [{"groupKey": {"id": "Viewers@Example.org"}}], "nextPageToken": "p2"},
        "p2": {"memberships": [{"groupKey": {"id": "admins@example.org"}}]},
    }
    searches: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            form = dict(parse_qsl(request.content.decode()))
            claims = pyjwt.decode(
                form["assertion"],
                signing_keys["service"].public_key(),
                algorithms=["RS256"],
                audience="https://oauth2.googleapis.com/token",
            )
            assert claims["iss"] == account["client_email"]
            assert claims["scope"] == GoogleCloudIdentity.SCOPE
            return httpx.Response(200, json={"access_token": "sa-token", "expires_in": 3600})
        searches.append(request)
        assert request.headers["authorization"] == "Bearer sa-token"
        return httpx.Response(200, json=pages[request.url.params.get("pageToken")])

    client = GoogleCloudIdentity(account, transport=httpx.MockTransport(answer))
    assert await client.group_emails("person@example.org") == {
        "viewers@example.org",
        "admins@example.org",
    }
    assert len(searches) == 2
    assert "member_key_id == 'person@example.org'" in searches[0].url.params["query"]


async def test_google_lookup_refuses_an_email_that_could_alter_the_query(sign_in_settings):
    calls = []
    client = GoogleCloudIdentity(
        sign_in_settings().google_service_account_key(),
        transport=httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(500)),
    )
    assert await client.group_emails("x' || true || '@example.org") == set()
    assert calls == []


@pytest.mark.parametrize(
    "email",
    [
        "a@sub.example.org",
        "a@example.org.evil.com",
        "a@evilexample.org",
        "a@b@example.org",
        "example.org",
        "a@",
    ],
)
async def test_a_domain_entry_matches_only_that_exact_domain(
    make_resolver, idp, sign_in_settings, email
):
    settings = sign_in_settings(
        role_viewer_emails=(),
        role_operator_emails=(),
        role_admin_emails=(),
        role_viewer_google_groups=(),
        role_operator_google_groups=(),
        role_admin_google_groups=(),
        role_operator_domains=("example.org",),
    )
    resolver = make_resolver(settings=settings, with_groups=False)
    assert await resolver.role_for(google_identity(idp, email)) is None


async def test_email_and_domain_matching_ignores_case(make_resolver, idp, sign_in_settings):
    settings = sign_in_settings(
        role_viewer_emails=("Viewer@Example.ORG",), role_operator_domains=("Other.Org",)
    )
    resolver = make_resolver(settings=settings, with_groups=False)
    assert await resolver.role_for(google_identity(idp, "VIEWER@example.org")) == "viewer"
    assert await resolver.role_for(google_identity(idp, "x@OTHER.org")) == "operator"


async def test_entra_domain_lists_never_apply(make_resolver, idp, sign_in_settings):
    settings = sign_in_settings(role_admin_domains=("example.org",))
    resolver = make_resolver(settings=settings)
    assert await resolver.role_for(entra_identity(idp, email="a@example.org")) is None


async def test_a_graph_failure_on_overage_raises_and_is_not_cached(make_resolver, idp, graph):
    resolver = make_resolver()
    identity = entra_identity(idp, hasgroups=True)
    graph.failing = True
    with pytest.raises(RoleLookupFailed):
        await resolver.role_for(identity)
    graph.failing = False
    graph.groups[OID] = {idp.ENTRA_GROUPS["viewer"]}
    assert await resolver.role_for(identity) == "viewer"


@pytest.fixture
def strasse_resolver(make_resolver, sign_in_settings):
    settings = sign_in_settings(
        role_viewer_emails=(),
        role_operator_emails=(),
        role_admin_emails=(),
        role_operator_domains=("strasse.example",),
    )
    return make_resolver(settings=settings, with_groups=False)


@pytest.mark.parametrize("email", ["x@straße.example", "x@ﬆrasse.example"])
async def test_unicode_lookalike_domains_get_no_role(strasse_resolver, idp, email):
    assert await strasse_resolver.role_for(google_identity(idp, email)) is None


async def test_ascii_upper_case_still_matches(strasse_resolver, idp):
    assert await strasse_resolver.role_for(google_identity(idp, "X@Strasse.Example")) == "operator"


async def test_a_non_ascii_email_skips_lists_but_not_google_groups(
    make_resolver, idp, google_groups
):
    email = "x@straße.example"
    google_groups.groups[email] = {"admins@example.org"}
    assert await make_resolver().role_for(google_identity(idp, email)) == "admin"


@pytest.fixture
def list_resolver(make_resolver, sign_in_settings):
    """Email and domain lists only (no Google Groups lookup)."""
    settings = sign_in_settings(
        role_viewer_emails=("viewer@example.org", "personal@gmail.com", "old@googlemail.com"),
        role_operator_emails=(),
        role_admin_emails=(),
        role_operator_domains=("example.org",),
    )
    return make_resolver(settings=settings, with_groups=False)


@pytest.mark.parametrize("email", ["viewer@example.org", "someone@example.org"])
async def test_a_work_address_without_hd_gets_no_role(list_resolver, idp, email):
    # A personal Google account registered with a work address: no Workspace membership.
    assert await list_resolver.role_for(google_identity(idp, email, hd=None)) is None


async def test_a_matching_hd_gives_the_list_roles(list_resolver, idp):
    resolver = list_resolver
    assert await resolver.role_for(google_identity(idp, "someone@example.org")) == "operator"
    assert await resolver.role_for(google_identity(idp, "viewer@example.org")) == "operator"
    viewer = google_identity(idp, "viewer@example.org", hd="EXAMPLE.ORG")
    assert await resolver.role_for(viewer) == "operator"


@pytest.mark.parametrize("email", ["personal@gmail.com", "old@googlemail.com"])
async def test_a_consumer_address_on_the_email_list_gets_its_role(list_resolver, idp, email):
    assert await list_resolver.role_for(google_identity(idp, email, hd=None)) == "viewer"


@pytest.mark.parametrize("email", ["viewer@example.org", "someone@example.org"])
async def test_an_hd_of_another_domain_gets_no_role(list_resolver, idp, email):
    identity = google_identity(idp, email, hd="elsewhere.example")
    assert await list_resolver.role_for(identity) is None


async def test_an_hd_that_is_not_ascii_or_not_text_gets_no_role(list_resolver, idp):
    for hd in ("exampłe.org", ["example.org"], 1):
        identity = google_identity(idp, "someone@example.org", hd=hd)
        assert await list_resolver.role_for(identity) is None, hd


def test_max_entries_must_be_positive(sign_in_settings):
    with pytest.raises(ValueError, match="max_entries"):
        RoleResolver(RoleMapping.from_settings(sign_in_settings()), max_entries=0)


async def test_hitting_the_page_limit_warns_without_identifiers(sign_in_settings, caplog):
    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx.Response(
            200, json={"memberships": [{"groupKey": {"id": "g@x.org"}}], "nextPageToken": "more"}
        )

    client = GoogleCloudIdentity(
        sign_in_settings().google_service_account_key(), transport=httpx.MockTransport(answer)
    )
    with caplog.at_level("WARNING"):
        await client.group_emails("person@example.org")
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert warnings and all("person@example.org" not in r.getMessage() for r in warnings)


async def test_an_unsafe_email_is_skipped_with_a_debug_log(sign_in_settings, caplog):
    client = GoogleCloudIdentity(
        sign_in_settings().google_service_account_key(),
        transport=httpx.MockTransport(lambda request: httpx.Response(500)),
    )
    with caplog.at_level("DEBUG"):
        assert await client.group_emails("x' || true || '@example.org") == set()
    assert any("skipped" in r.getMessage() for r in caplog.records)
    assert all("true" not in r.getMessage() for r in caplog.records)


async def test_claim_sources_are_never_followed(make_resolver, idp, graph):
    identity = entra_identity(
        idp,
        _claim_names={"groups": "src1"},
        _claim_sources={"src1": {"endpoint": "https://evil.example/groups"}},
    )
    graph.groups[OID] = {idp.ENTRA_GROUPS["viewer"]}
    assert await make_resolver().role_for(identity) == "viewer"
    assert graph.calls == [OID]


async def test_the_graph_only_ever_talks_to_its_fixed_hosts():
    hosts = []

    def answer(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx.Response(200, json={"value": []})

    graph = MicrosoftGraph("tenant-1", "c", SecretStr("s"), transport=httpx.MockTransport(answer))
    await graph.member_object_ids(OID)
    assert set(hosts) == {"login.microsoftonline.com", "graph.microsoft.com"}


async def test_the_cache_never_exceeds_max_entries(sign_in_settings, google_groups, idp, clock):
    resolver = RoleResolver(
        RoleMapping.from_settings(sign_in_settings()),
        google_groups=google_groups,
        clock=clock,
        max_entries=3,
    )
    for n in range(10):
        await resolver.role_for(google_identity(idp, f"p{n}@example.org"))
    assert len(resolver._cache) == 3


async def test_two_subjects_never_share_a_cached_result(make_resolver, idp):
    resolver = make_resolver()
    admin = google_identity(idp, "admin@example.org")
    other = Identity(
        provider="google",
        issuer=admin.issuer,
        subject="someone-else",
        email="nobody@elsewhere.org",
        claims={},
    )
    assert await resolver.role_for(admin) == "admin"
    assert await resolver.role_for(other) is None
