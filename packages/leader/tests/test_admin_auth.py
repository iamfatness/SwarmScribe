import httpx
import pytest
from fastapi import Depends
from sqlalchemy import select
from swarmscribe_leader.api.admin_auth import require
from swarmscribe_leader.app import create_app
from swarmscribe_leader.config import Settings
from swarmscribe_leader.db.models import AuditEntry


async def audit_rows(sessionmaker, action):
    async with sessionmaker() as session:
        return list(
            (await session.scalars(select(AuditEntry).where(AuditEntry.action == action))).all()
        )


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_a_call_without_a_token_is_401(admin_client):
    response = await admin_client.get("/v1/admin/whoami")
    assert (response.status_code, response.json()["code"]) == (401, "unauthorized")


@pytest.mark.parametrize("header", ["Bearer", "Basic dXNlcjpwYXNz", "Bearer not-a-token"])
async def test_a_malformed_authorization_is_401(admin_client, header):
    response = await admin_client.get("/v1/admin/whoami", headers={"Authorization": header})
    assert response.status_code == 401


async def test_a_401_without_a_token_says_to_send_a_bearer_token(admin_client):
    response = await admin_client.get("/v1/admin/whoami")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "make_token",
    [
        lambda idp: "not-a-token",
        lambda idp: idp.entra(signed_with="rogue"),
        lambda idp: idp.entra(aud="another-client"),
    ],
    ids=["malformed", "forged", "wrong-audience"],
)
async def test_a_401_for_an_invalid_token_says_invalid_token(admin_client, idp, make_token):
    response = await admin_client.get("/v1/admin/whoami", headers=bearer(make_token(idp)))
    assert (response.status_code, response.json()["code"]) == (401, "unauthorized")
    assert response.headers["www-authenticate"] == 'Bearer error="invalid_token"'


async def test_a_401_for_an_expired_token_says_invalid_token(admin_client, idp):
    response = await admin_client.get("/v1/admin/whoami", headers=bearer(idp.entra(lifetime=-300)))
    assert (response.status_code, response.json()["code"]) == (401, "token_expired")
    assert response.headers["www-authenticate"] == 'Bearer error="invalid_token"'


@pytest.mark.parametrize(
    "header",
    [
        lambda token: f"Bearer\t{token}",  # a tab is not the separator
        lambda token: f" Bearer {token}",  # nor is a leading space allowed
        lambda token: "Bearer " + token + "x" * 20_000,  # oversize
    ],
    ids=["tab-separator", "leading-space", "oversize"],
)
async def test_odd_authorization_headers_are_401(admin_client, idp, header):
    token = idp.entra(groups=[idp.ENTRA_GROUPS["admin"]])
    response = await admin_client.get(
        "/v1/admin/whoami", headers=[(b"authorization", header(token).encode())]
    )
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer")


@pytest.mark.parametrize("name", ["access_token", "token", "id_token"])
async def test_a_token_only_in_the_query_string_is_401(admin_client, idp, name):
    token = idp.entra(groups=[idp.ENTRA_GROUPS["admin"]])
    response = await admin_client.get("/v1/admin/whoami", params={name: token})
    assert (response.status_code, response.headers["www-authenticate"]) == (401, "Bearer")


@pytest.mark.parametrize("role", ["viewer", "operator", "admin"])
@pytest.mark.parametrize("provider", ["entra", "google"])
async def test_each_provider_signs_in_with_its_role(admin_client, idp, provider, role):
    response = await admin_client.get(
        "/v1/admin/whoami", headers=idp.bearer(role, provider=provider)
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["provider"], body["role"], body["email"]) == (
        provider,
        role,
        f"{role}@example.org",
    )


async def test_google_groups_give_a_role(admin_client, idp, google_groups):
    google_groups.groups["person@example.org"] = {"Operators@Example.org"}
    response = await admin_client.get("/v1/admin/whoami", headers=bearer(idp.google()))
    assert response.json()["role"] == "operator"
    assert google_groups.calls == ["person@example.org"]


async def test_entra_group_overage_is_resolved_through_graph(admin_client, idp, graph):
    graph.groups["00000000-0000-4000-8000-0000000000a1"] = {idp.ENTRA_GROUPS["admin"]}
    token = idp.entra(
        groups=None,
        _claim_names={"groups": "src1"},
        _claim_sources={"src1": {"endpoint": "https://graph.microsoft.com/v1.0/users/x"}},
    )
    response = await admin_client.get("/v1/admin/whoami", headers=bearer(token))
    assert response.json()["role"] == "admin"


async def test_a_person_with_no_role_is_refused_and_the_refusal_is_audited(
    admin_client, idp, sessionmaker
):
    response = await admin_client.get("/v1/admin/whoami", headers=idp.bearer(None))
    assert (response.status_code, response.json()["code"]) == (403, "forbidden")
    assert "no SwarmScribe role" in response.json()["message"]
    (entry,) = await audit_rows(sessionmaker, "admin.refused")
    assert entry.actor == f"nobody@example.org ({idp.ENTRA_ISSUER} entra-nobody)"
    assert entry.subject_id == "GET /v1/admin/whoami"
    assert entry.detail == {"role": None, "required": "viewer"}


async def test_an_expired_token_is_401_token_expired(admin_client, idp):
    expired = idp.entra(lifetime=-300)
    response = await admin_client.get("/v1/admin/whoami", headers=bearer(expired))
    assert (response.status_code, response.json()["code"]) == (401, "token_expired")


async def test_an_unreachable_identity_provider_is_503_with_retry_after(admin_client, idp):
    idp.down = True
    response = await admin_client.get("/v1/admin/whoami", headers=idp.bearer("viewer"))
    assert (response.status_code, response.json()["code"]) == (503, "unavailable")
    assert response.headers["retry-after"] == "10"


async def test_a_failing_group_directory_is_503_not_403(admin_client, idp, google_groups):
    google_groups.failing = True
    response = await admin_client.get("/v1/admin/whoami", headers=bearer(idp.google()))
    assert (response.status_code, response.json()["code"]) == (503, "unavailable")


async def test_every_admin_call_is_audited_with_issuer_subject_and_email(
    admin_client, idp, sessionmaker
):
    await admin_client.get("/v1/admin/whoami", headers=idp.bearer("viewer"))
    (entry,) = await audit_rows(sessionmaker, "whoami.view")
    assert entry.actor == f"viewer@example.org ({idp.ENTRA_ISSUER} entra-viewer)"


async def test_tokens_never_reach_the_log_or_the_audit_log(
    admin_client, idp, sessionmaker, caplog, google_groups
):
    tokens = [
        idp.entra(groups=[idp.ENTRA_GROUPS["viewer"]], sub="entra-viewer"),
        idp.entra(lifetime=-300),
        idp.entra(signed_with="rogue"),
        idp.google(email="nobody@example.org"),
    ]
    with caplog.at_level("DEBUG"):
        for token in tokens:
            await admin_client.get("/v1/admin/whoami", headers=bearer(token))
        google_groups.failing = True
        failing = idp.google(sub="google-other")
        await admin_client.get("/v1/admin/whoami", headers=bearer(failing))
    async with sessionmaker() as session:
        entries = (await session.scalars(select(AuditEntry))).all()
    audit_text = " ".join(f"{e.actor} {e.subject_id} {e.detail}" for e in entries)
    for token in [*tokens, failing]:
        assert token not in caplog.text
        assert token not in audit_text


async def test_readyz_is_ready_once_the_metadata_is_fetched(admin_client):
    response = await admin_client.get("/readyz")
    assert (response.status_code, response.json()) == (200, {"status": "ready"})


async def test_login_config_needs_no_sign_in_and_hides_the_entra_secret(admin_client, idp):
    response = await admin_client.get("/v1/admin/login-config")
    assert response.status_code == 200
    providers = response.json()["providers"]
    assert [p["name"] for p in providers] == ["entra", "google"]
    assert providers[0]["client_id"] == idp.ENTRA_CLIENT
    assert providers[0]["client_secret"] is None
    assert "entra-app-secret-value" not in response.text


async def test_a_leader_without_sign_in_refuses_admin_calls(engine, migrated_database_url, idp):
    settings = Settings(
        database_url=migrated_database_url, public_url="http://leader", link_key="k" * 32
    )
    application = create_app(settings, background=False)
    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://leader"
        ) as http:
            response = await http.get("/v1/admin/whoami", headers=idp.bearer("admin"))
            ready = await http.get("/readyz")
            config = await http.get("/v1/admin/login-config")
    assert (response.status_code, response.json()["message"]) == (
        401,
        "sign-in is not configured on this leader",
    )
    assert ready.status_code == 200
    assert config.json() == {"providers": []}


async def test_login_config_carries_the_public_client_details_and_no_server_secret(
    admin_client, idp, sign_in_settings
):
    response = await admin_client.get("/v1/admin/login-config")
    entra, google = response.json()["providers"]
    assert entra["client_id"] == idp.ENTRA_CLIENT
    assert entra["client_secret"] is None
    assert idp.ENTRA_TENANT in entra["token_endpoint"]
    # Owner-approved: a Google limited-input-device client's secret is not confidential.
    assert google["client_id"] == idp.GOOGLE_CLIENT
    assert google["client_secret"] == "google-device-secret-value"
    # Never the Entra app secret or anything from the Google service-account key.
    settings = sign_in_settings()
    service_account = settings.google_service_account_key()
    assert "entra-app-secret-value" not in response.text
    assert "PRIVATE KEY" not in response.text
    assert "service_account" not in response.text
    assert "group-reader@project-1.iam.gserviceaccount.com" not in response.text
    assert service_account is not None
    assert service_account["private_key_id"] not in response.text


@pytest.fixture
def role_routes(admin_app):
    async def handler() -> dict:
        return {"ok": True}

    for role in ("viewer", "operator", "admin"):
        admin_app.add_api_route(
            f"/v1/admin/_test/{role}",
            handler,
            methods=["GET"],
            dependencies=[Depends(require(role))],
        )
    return admin_app


@pytest.mark.parametrize(
    ("held", "reaches"),
    [
        ("viewer", {"viewer"}),
        ("operator", {"viewer", "operator"}),
        ("admin", {"viewer", "operator", "admin"}),
    ],
)
async def test_roles_are_cumulative(role_routes, admin_client, idp, held, reaches):
    for required in ("viewer", "operator", "admin"):
        response = await admin_client.get(f"/v1/admin/_test/{required}", headers=idp.bearer(held))
        assert response.status_code == (200 if required in reaches else 403), (held, required)


async def test_a_refusal_for_a_lower_role_is_audited_with_both_roles(
    role_routes, admin_client, idp, sessionmaker
):
    response = await admin_client.get("/v1/admin/_test/admin", headers=idp.bearer("operator"))
    assert response.status_code == 403
    assert "needs the admin role" in response.json()["message"]
    (entry,) = await audit_rows(sessionmaker, "admin.refused")
    assert entry.detail == {"role": "operator", "required": "admin"}
    assert entry.subject_id == "GET /v1/admin/_test/admin"


async def test_a_refusal_never_contains_the_token(admin_client, idp, sessionmaker):
    token = idp.entra(groups=[], sub="entra-nobody", email="nobody@example.org")
    await admin_client.get("/v1/admin/whoami", headers=bearer(token))
    async with sessionmaker() as session:
        entries = (await session.scalars(select(AuditEntry))).all()
    assert entries
    assert all(token not in f"{e.actor} {e.subject_id} {e.detail}" for e in entries)


async def test_a_failed_lookup_is_not_audited_as_a_refusal(
    admin_client, idp, google_groups, sessionmaker
):
    google_groups.failing = True
    await admin_client.get("/v1/admin/whoami", headers=bearer(idp.google()))
    assert await audit_rows(sessionmaker, "admin.refused") == []


async def test_a_follower_credential_is_not_accepted_on_admin_routes(admin_client, factory):
    _, credential = await factory.follower()
    response = await admin_client.get("/v1/admin/whoami", headers=bearer(credential))
    assert (response.status_code, response.json()["code"]) == (401, "unauthorized")


async def test_an_admin_token_is_not_accepted_on_follower_routes(admin_client, idp):
    headers = idp.bearer("admin")
    job = "00000000-0000-4000-8000-000000000001"
    for path in ("/v1/jobs/claim", f"/v1/jobs/{job}/heartbeat", "/v1/followers/deregister"):
        response = await admin_client.post(path, headers=headers, json={})
        assert (response.status_code, response.json()["code"]) == (401, "unauthorized"), path
        assert response.headers["www-authenticate"] == 'Bearer error="invalid_token"', path
