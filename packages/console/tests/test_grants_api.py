import asyncio

import pytest
from console_testkit import ADMIN_PRINCIPAL, GROUPS, console_env, recreate, with_database
from sqlalchemy import select
from swarmscribe_console import grants
from swarmscribe_console.db.models import AuditEntry
from swarmscribe_console.errors import Conflict
from swarmscribe_console.main import main


@pytest.fixture
async def admin(client, factory):
    await factory.console_admin("email", "admin@example.org")
    csrf = await factory.person(client, principals={ADMIN_PRINCIPAL}, email="admin@example.org")
    client.headers["X-CSRF-Token"] = csrf
    return client


async def _actions(sessionmaker) -> list[str]:
    async with sessionmaker() as session:
        rows = await session.scalars(select(AuditEntry.action).order_by(AuditEntry.id))
        return list(rows.all())


async def test_a_grant_is_added_canonically_listed_and_removed(admin, sessionmaker):
    answer = await admin.post(
        "/api/admin/grants",
        json={
            "role": "operator",
            "scope": "label:region=eu=west",
            "principal_kind": "entra_group",
            "principal": GROUPS["operator"].upper(),
        },
    )
    assert answer.status_code == 201
    body = answer.json()
    assert (body["role"], body["scope"], body["principal_kind"], body["principal"]) == (
        "operator",
        "label:region=eu=west",
        "entra_group",
        GROUPS["operator"],
    )
    listed = (await admin.get("/api/admin/grants")).json()
    assert [g["id"] for g in listed] == [body["id"]]
    assert (await admin.delete(f"/api/admin/grants/{body['id']}")).status_code == 204
    assert (await admin.get("/api/admin/grants")).json() == []
    assert await _actions(sessionmaker) == ["grant.add", "grant.remove"]


async def test_a_leader_scope_is_stored_lowercase(admin):
    answer = await admin.post(
        "/api/admin/grants",
        json={
            "role": "viewer",
            "scope": "leader:EU-1",
            "principal_kind": "email",
            "principal": "Person@Example.org",
        },
    )
    assert (answer.json()["scope"], answer.json()["principal"]) == (
        "leader:eu-1",
        "person@example.org",
    )


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("role", "superadmin", "invalid_request"),
        ("scope", "leader:../x", "invalid_scope"),
        ("scope", "label:env", "invalid_scope"),
        ("principal_kind", "user", "invalid_principal"),
        ("principal", "not-a-guid", "invalid_principal"),
    ],
)
async def test_odd_grants_are_refused(admin, field, value, code):
    body = {
        "role": "viewer",
        "scope": "all",
        "principal_kind": "entra_group",
        "principal": GROUPS["viewer"],
    }
    body[field] = value
    answer = await admin.post("/api/admin/grants", json=body)
    assert answer.status_code == 422
    assert answer.json()["code"] == code


async def test_one_principal_has_one_role_per_scope(admin):
    body = {"role": "viewer", "scope": "all", "principal_kind": "email", "principal": "a@b.org"}
    assert (await admin.post("/api/admin/grants", json=body)).status_code == 201
    body["role"] = "admin"
    body["principal"] = "A@B.org"
    answer = await admin.post("/api/admin/grants", json=body)
    assert answer.status_code == 409
    assert answer.json()["code"] == "exists"


async def test_removing_an_unknown_grant_is_not_found(admin):
    unknown = "00000000-0000-4000-8000-000000000000"
    assert (await admin.delete(f"/api/admin/grants/{unknown}")).status_code == 404
    assert (await admin.delete("/api/admin/grants/not-a-uuid")).status_code == 422


async def test_console_admins_are_added_and_removed_but_never_the_last(admin, sessionmaker):
    listed = (await admin.get("/api/admin/console-admins")).json()
    (me,) = listed
    assert (me["principal_kind"], me["principal"]) == ("email", "admin@example.org")
    answer = await admin.delete(f"/api/admin/console-admins/{me['id']}")
    assert answer.status_code == 409
    assert answer.json()["code"] == "last_admin"
    added = await admin.post(
        "/api/admin/console-admins",
        json={"principal_kind": "entra_group", "principal": GROUPS["console"]},
    )
    assert added.status_code == 201
    assert (await admin.delete(f"/api/admin/console-admins/{me['id']}")).status_code == 204
    # I am no longer a console administrator.
    assert (await admin.get("/api/admin/console-admins")).status_code == 403
    assert await _actions(sessionmaker) == [
        "request.refused",
        "console_admin.add",
        "console_admin.remove",
    ]


async def test_a_leader_admin_cannot_grant_roles(client, factory):
    await factory.grant("admin", "all", "email", "person@example.org")
    csrf = await factory.person(client, principals={"email:person@example.org"})
    client.headers["X-CSRF-Token"] = csrf
    answer = await client.post(
        "/api/admin/grants",
        json={"role": "admin", "scope": "all", "principal_kind": "email", "principal": "x@y.org"},
    )
    assert answer.status_code == 403
    assert (await client.get("/api/admin/grants")).status_code == 403
    assert (await client.get("/api/admin/console-admins")).status_code == 403


def test_the_first_console_admin_is_added_from_the_command_line(
    admin_database_url, monkeypatch, capsys
):
    # Its own database: main() runs its own event loop, so this test is synchronous and
    # cannot use the async `engine` fixture.
    name = "swarmscribe_console_cli"
    url = with_database(admin_database_url, name)
    asyncio.run(recreate(admin_database_url, name))
    console_env(monkeypatch, url)
    try:
        assert main(["admins", "list"]) == 2  # not migrated yet: refused, says so
        assert "swarmscribe-console migrate" in capsys.readouterr().err
        assert main(["migrate"]) == 0
        capsys.readouterr()
        assert main(["admins", "add", "email", "First@Example.org"]) == 0
        assert "email:first@example.org" in capsys.readouterr().out
        assert main(["admins", "add", "email", "first@example.org"]) == 1
        assert "already" in capsys.readouterr().err
        assert main(["admins", "add", "email", "not an email"]) == 1
        capsys.readouterr()
        assert main(["admins", "list"]) == 0
        assert "email:first@example.org\tswarmscribe-console cli" in capsys.readouterr().out
    finally:
        asyncio.run(recreate(admin_database_url, name, drop_only=True))


# --- hostile cases beyond the brief -------------------------------------------------------

ROUTES = [
    ("GET", "/api/admin/grants", None),
    ("POST", "/api/admin/grants", {
        "role": "viewer", "scope": "all", "principal_kind": "email", "principal": "x@y.org",
    }),
    ("DELETE", "/api/admin/grants/00000000-0000-4000-8000-000000000000", None),
    ("GET", "/api/admin/console-admins", None),
    ("POST", "/api/admin/console-admins", {"principal_kind": "email", "principal": "x@y.org"}),
    ("DELETE", "/api/admin/console-admins/00000000-0000-4000-8000-000000000000", None),
]


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
async def test_a_person_with_no_console_admin_standing_is_refused_everywhere(
    client, factory, sessionmaker, method, path, body
):
    await factory.grant("admin", "all", "email", "person@example.org")  # a leader admin only
    csrf = await factory.person(client, principals={"email:person@example.org"})
    answer = await client.request(method, path, json=body, headers={"X-CSRF-Token": csrf})
    assert answer.status_code == 403
    assert answer.json()["code"] == "forbidden"
    async with sessionmaker() as session:
        assert (await grants.list_grants(session))[0].principal == "person@example.org"
        assert len(await grants.list_grants(session)) == 1
        assert await grants.list_console_admins(session) == []


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
async def test_signed_out_callers_are_refused_everywhere(client, method, path, body):
    assert (await client.request(method, path, json=body)).status_code == 401


@pytest.mark.parametrize(
    ("method", "path", "body"), [r for r in ROUTES if r[0] in ("POST", "DELETE")]
)
async def test_changes_without_the_csrf_token_change_nothing(
    admin, sessionmaker, method, path, body
):
    del admin.headers["X-CSRF-Token"]
    answer = await admin.request(method, path, json=body)
    assert answer.status_code == 403
    assert answer.json()["code"] == "csrf_failed"
    async with sessionmaker() as session:
        assert await grants.list_grants(session) == []
        assert len(await grants.list_console_admins(session)) == 1


async def test_two_concurrent_removals_of_the_last_two_admins_leave_one(
    sessionmaker, factory
):
    first = await factory.console_admin("email", "one@example.org")
    second = await factory.console_admin("email", "two@example.org")

    async def remove(admin_id):
        async with sessionmaker() as session:
            try:
                await grants.remove_console_admin(session, admin_id, actor="t")
                await session.commit()
                return "removed"
            except Conflict as exc:
                await session.rollback()
                return exc.code

    results = await asyncio.gather(remove(first.id), remove(second.id))
    assert sorted(results) == ["last_admin", "removed"]
    async with sessionmaker() as session:
        assert len(await grants.list_console_admins(session)) == 1


async def test_an_unknown_console_admin_is_not_found(admin):
    unknown = "00000000-0000-4000-8000-000000000000"
    assert (await admin.delete(f"/api/admin/console-admins/{unknown}")).status_code == 404


async def test_a_duplicate_console_admin_is_refused_in_any_case(admin):
    body = {"principal_kind": "email", "principal": "ADMIN@Example.org"}
    answer = await admin.post("/api/admin/console-admins", json=body)
    assert answer.status_code == 409
    assert answer.json()["code"] == "exists"


async def test_console_admin_principals_are_stored_canonically(admin):
    answer = await admin.post(
        "/api/admin/console-admins",
        json={"principal_kind": "entra_group", "principal": "{" + GROUPS["console"].upper() + "}"},
    )
    assert answer.status_code == 201
    assert answer.json()["principal"] == GROUPS["console"]
    answer = await admin.post(
        "/api/admin/console-admins", json={"principal_kind": "domain", "principal": "@Example.ORG"}
    )
    assert answer.json()["principal"] == "example.org"


async def test_a_mixed_case_grant_matches_what_sign_in_produces(admin, sessionmaker):
    # Grants are stored lowercase, so a person whose sign-in yields lowercase principals
    # gets the role even though the administrator typed capitals.
    body = {
        "role": "operator",
        "scope": "LEADER:x",
        "principal_kind": "email",
        "principal": "Mixed.Case@Example.ORG",
    }
    assert (await admin.post("/api/admin/grants", json=body)).status_code == 422  # scope kind
    body["scope"] = "leader:Eu-1"
    assert (await admin.post("/api/admin/grants", json=body)).status_code == 201
    async with sessionmaker() as session:
        held = await grants.grants_held(session, ["email:mixed.case@example.org"])
    assert [(role, str(scope)) for role, scope in held] == [("operator", "leader:eu-1")]


async def test_a_grant_for_a_leader_that_is_not_registered_is_allowed(admin):
    # Decision: allowed. Grants and leaders are administered independently (a grant may
    # be prepared before the leader is registered, and a label scope names no leader at all),
    # and a scope only ever matches registered leaders at request time.
    answer = await admin.post(
        "/api/admin/grants",
        json={
            "role": "viewer",
            "scope": "leader:not-yet",
            "principal_kind": "email",
            "principal": "a@b.org",
        },
    )
    assert answer.status_code == 201


async def test_validation_errors_never_echo_what_was_sent(admin):
    secret = "SuperSecretValue-Q9"
    for body in (
        {"role": "viewer", "scope": f"label:{secret}", "principal_kind": "email",
         "principal": "a@b.org"},
        {"role": "viewer", "scope": "all", "principal_kind": "email", "principal": secret},
        {"role": secret, "scope": "all", "principal_kind": "email", "principal": "a@b.org"},
        {"role": "viewer", "scope": "all", "principal_kind": secret, "principal": "a@b.org"},
    ):
        answer = await admin.post("/api/admin/grants", json=body)
        assert answer.status_code == 422
        assert secret not in answer.text


async def test_unknown_fields_are_refused(admin):
    answer = await admin.post(
        "/api/admin/console-admins",
        json={"principal_kind": "email", "principal": "a@b.org", "created_by": "me"},
    )
    assert answer.status_code == 422


def test_the_command_line_refuses_odd_input(admin_database_url, monkeypatch, capsys):
    name = "swarmscribe_console_cli2"
    url = with_database(admin_database_url, name)
    asyncio.run(recreate(admin_database_url, name))
    console_env(monkeypatch, url)
    try:
        assert main(["migrate"]) == 0
        capsys.readouterr()
        assert main(["admins", "add", "entra_group", "not-a-guid"]) == 1
        err = capsys.readouterr().err
        assert "GUID" in err and "not-a-guid" not in err
        assert main(["admins", "add", "email", "x" * 400 + "@example.org"]) == 1
        capsys.readouterr()
        with pytest.raises(SystemExit):
            main(["admins", "add", "user", "a@b.org"])  # unknown kind: argparse refuses
        capsys.readouterr()
        assert main(["admins", "list"]) == 0
        assert capsys.readouterr().out == ""
    finally:
        asyncio.run(recreate(admin_database_url, name, drop_only=True))
