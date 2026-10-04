import pytest
from swarmscribe_console.db.models import ConsoleAdmin, RoleGrant
from swarmscribe_console.errors import Invalid
from swarmscribe_console.grants import (
    Scope,
    grants_held,
    has_any_access,
    is_console_admin,
    normalize_principal,
    parse_scope,
    principal_key,
    role_for,
)

GROUP = "a1a1a1a1-0000-4000-8000-000000000002"


# --- scopes -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "scope", "canonical"),
    [
        ("all", Scope("all"), "all"),
        ("leader:EU-1", Scope("leader", leader="eu-1"), "leader:eu-1"),
        ("label:env=prod", Scope("label", key="env", value="prod"), "label:env=prod"),
        (
            "label:region=eu=west",
            Scope("label", key="region", value="eu=west"),
            "label:region=eu=west",
        ),
        ("label:team.name=A-b_c", Scope("label", key="team.name", value="A-b_c"), None),
    ],
)
def test_a_scope_is_parsed_and_written_back_canonically(text, scope, canonical):
    assert parse_scope(text) == scope
    assert str(parse_scope(text)) == (canonical or text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "ALL",
        "everything",
        "leader:",
        "leader:../eu",
        "leader:eu 1",
        "leader:a/b",
        "label:",
        "label:env",
        "label:env=",
        "label:=prod",
        "label:Env=prod",
        "label:env=has space",
        "label:env=café",
        "label:env=" + "x" * 256,
        "label:env=a\nb",
        "group:x",
    ],
)
def test_other_scopes_are_refused(text):
    with pytest.raises(Invalid) as raised:
        parse_scope(text)
    assert raised.value.code == "invalid_scope"


def test_a_label_value_with_an_equals_sign_matches_only_that_value():
    scope = parse_scope("label:region=eu=west")
    assert scope.matches("x", {"region": "eu=west"})
    assert not scope.matches("x", {"region": "eu"})
    assert not scope.matches("x", {"region=eu": "west"})


def test_scopes_match_by_name_ignoring_case_by_exact_label_or_all():
    assert parse_scope("leader:eu-1").matches("EU-1", {})
    assert not parse_scope("leader:eu-1").matches("eu-10", {})
    assert parse_scope("label:env=prod").matches("eu-1", {"env": "prod", "region": "eu"})
    assert not parse_scope("label:env=prod").matches("eu-1", {"env": "Prod"})
    assert not parse_scope("label:env=prod").matches("eu-1", {})
    assert parse_scope("all").matches("anything", {})


# --- the highest matching grant ---------------------------------------------------------


def test_the_highest_matching_grant_wins():
    held = [
        ("viewer", parse_scope("all")),
        ("operator", parse_scope("label:env=prod")),
        ("admin", parse_scope("leader:eu-1")),
    ]
    assert role_for(held, "eu-1", {"env": "prod"}) == "admin"
    assert role_for(held, "us-1", {"env": "prod"}) == "operator"
    assert role_for(held, "us-2", {"env": "test"}) == "viewer"


def test_no_matching_grant_is_no_role():
    held = [("admin", parse_scope("leader:eu-1"))]
    assert role_for(held, "us-1", {}) is None
    assert role_for([], "eu-1", {}) is None


# --- principals -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "value", "stored"),
    [
        ("entra_group", GROUP.upper(), GROUP),
        ("entra_group", "{" + GROUP + "}", GROUP),
        ("google_group", "Operators@Example.org", "operators@example.org"),
        ("email", " Person@Example.org ", "person@example.org"),
        ("domain", "@Example.org", "example.org"),
        ("domain", "eu.example.org", "eu.example.org"),
    ],
)
def test_principals_are_stored_in_one_canonical_form(kind, value, stored):
    assert normalize_principal(kind, value) == stored


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("entra_group", "operators"),
        ("email", "person"),
        ("email", "a@b@example.org"),
        ("email", "person@localhost"),
        ("email", "per son@example.org"),
        ("email", "person@exaKple.org"),
        ("google_group", "Kelvin@example.org"),
        ("domain", "example"),
        ("domain", "exa_mple.org"),
        ("domain", "*.example.org"),
        ("user", "person@example.org"),
        ("email", ""),
    ],
)
def test_odd_principals_are_refused(kind, value):
    with pytest.raises(Invalid) as raised:
        normalize_principal(kind, value)
    assert raised.value.code == "invalid_principal"


def test_a_principal_key_names_its_kind():
    assert principal_key("email", "a@example.org") == "email:a@example.org"


# --- from the database ------------------------------------------------------------------


async def _add(sessionmaker, *rows):
    async with sessionmaker() as session:
        session.add_all(rows)
        await session.commit()


def _grant(role, scope, kind, principal):
    return RoleGrant(
        role=role, scope=scope, principal_kind=kind, principal=principal, created_by="t"
    )


async def test_only_grants_held_by_the_persons_principals_count(sessionmaker):
    await _add(
        sessionmaker,
        _grant("operator", "label:env=prod", "entra_group", GROUP),
        _grant("admin", "all", "email", "someone-else@example.org"),
        _grant("viewer", "leader:eu-1", "domain", "example.org"),
    )
    async with sessionmaker() as session:
        held = await grants_held(
            session, {principal_key("entra_group", GROUP), "domain:example.org"}
        )
    assert sorted((role, str(scope)) for role, scope in held) == [
        ("operator", "label:env=prod"),
        ("viewer", "leader:eu-1"),
    ]
    assert role_for(held, "eu-1", {"env": "prod"}) == "operator"


async def test_no_principals_hold_nothing(sessionmaker):
    await _add(sessionmaker, _grant("admin", "all", "email", "a@example.org"))
    async with sessionmaker() as session:
        assert await grants_held(session, set()) == []
        assert await has_any_access(session, set()) is False


async def test_a_console_admin_holds_no_leader_role_but_has_access(sessionmaker):
    await _add(
        sessionmaker,
        ConsoleAdmin(principal_kind="email", principal="admin@example.org", created_by="t"),
    )
    principals = {"email:admin@example.org"}
    async with sessionmaker() as session:
        assert await is_console_admin(session, principals) is True
        assert await grants_held(session, principals) == []
        assert await has_any_access(session, principals) is True
        assert await is_console_admin(session, {"email:other@example.org"}) is False


async def test_a_corrupted_stored_scope_is_skipped_not_fatal(sessionmaker):
    await _add(
        sessionmaker,
        _grant("admin", "label:Bad Key=x", "email", "a@example.org"),
        _grant("viewer", "all", "email", "a@example.org"),
    )
    async with sessionmaker() as session:
        held = await grants_held(session, {"email:a@example.org"})
    assert [(role, str(scope)) for role, scope in held] == [("viewer", "all")]


# --- hostile input ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "label:a=b​c",  # zero-width space in a value
        "label:aа=x",  # Cyrillic a in a key
        "label:env=ｐrod",  # fullwidth p in a value
        "leader:Kelvin",  # Kelvin sign
        "leader:еu-1",  # Cyrillic e
        "leader:eu-1\n",
        "label:env=prod\n",
        "label:env=prod ",
        " label:env=prod",
        "label: env=prod",
        "label:a=b\x00c",
        "label:=",
        "label:==",
        "label:a b=c",
    ],
)
def test_hostile_scopes_are_refused(text):
    with pytest.raises(Invalid) as raised:
        parse_scope(text)
    assert raised.value.code == "invalid_scope"


def test_a_key_containing_equals_cannot_be_written_and_splits_at_the_first_sign():
    scope = parse_scope("label:e=nv=prod=")
    assert (scope.key, scope.value) == ("e", "nv=prod=")
    assert not scope.matches("x", {"e=nv": "prod="})
    assert not scope.matches("x", {"e=nv=prod": ""})
    assert scope.matches("x", {"e": "nv=prod="})


def test_label_keys_and_values_are_case_sensitive_and_exact():
    scope = parse_scope("label:env=prod")
    assert not scope.matches("x", {"ENV": "prod"})
    assert not scope.matches("x", {"env": "prod "})
    assert not scope.matches("x", {"env": ["prod"]})
    assert not scope.matches("x", {"env": None})


def test_a_leader_scope_never_matches_a_unicode_case_fold_lookalike():
    scope = parse_scope("leader:kelvin")
    assert scope.matches("Kelvin", {})
    assert not scope.matches("Kelvin", {})
    assert not parse_scope("leader:i").matches("İ", {})
    assert not parse_scope("leader:eu-1").matches("eu-1 ", {})


def test_the_longest_valid_scopes_fit_the_stored_column():
    longest_label = f"label:{'k' * 63}={'v' * 255}"
    longest_leader = "leader:" + "a" * 100
    assert str(parse_scope(longest_label)) == longest_label
    assert str(parse_scope(longest_leader)) == longest_leader
    assert max(len(longest_label), len(longest_leader)) <= 400
    for text in (f"label:{'k' * 64}=v", "leader:" + "a" * 101, f"label:k={'v' * 256}"):
        with pytest.raises(Invalid):
            parse_scope(text)


def test_every_scope_longer_than_the_column_is_refused():
    for text in ("leader:" + "a" * 400, "label:k=" + "v" * 400, "all" + " " * 400):
        with pytest.raises(Invalid):
            parse_scope(text)


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("entra_group", GROUP.replace("0", "٠")),  # Arabic-Indic digits
        ("entra_group", GROUP.replace("0", "０")),  # fullwidth digits
        ("entra_group", GROUP.replace("-", "")),
        ("entra_group", "urn:uuid:" + GROUP),
        ("entra_group", " " + GROUP),
        ("email", " a@example.org"),
        ("email", "a@example.org\n"),
        ("email", "a@exam​ple.org"),
        ("email", "a@Kelvin.org"),
        ("email", "aа@example.org"),
        ("email", "a@example.org:x"),
        ("email", "a" * 250 + "@example.org"),
        ("email", "a@" + "b" * 64 + ".org"),
        ("email", "a@example.org."),
        ("email", "a@-example.org"),
        ("email", "a@example..org"),
        ("email", "a@example.123"),
        ("domain", "@@example.org"),
        ("domain", "example.org."),
        ("domain", "exаmple.org"),
        ("domain", ".example.org"),
        ("google_group", "a@b@example.org"),
        ("email", "a" * 400 + "@example.org"),
    ],
)
def test_hostile_principals_are_refused(kind, value):
    with pytest.raises(Invalid) as raised:
        normalize_principal(kind, value)
    assert raised.value.code == "invalid_principal"


def test_every_accepted_principal_fits_its_column():
    longest = "a" * 64 + "@" + ".".join(["b" * 61] * 3) + ".org"
    assert len(normalize_principal("email", longest)) <= 320
    assert len(normalize_principal("domain", ".".join(["b" * 61] * 3) + ".org")) <= 320


# --- never more than one matching grant gives -------------------------------------------


def test_a_role_never_exceeds_what_a_single_matching_grant_gives():
    held = [
        ("admin", parse_scope("leader:other")),
        ("admin", parse_scope("label:env=Prod")),
        ("operator", parse_scope("label:region=eu")),
        ("viewer", parse_scope("leader:eu-1")),
    ]
    assert role_for(held, "eu-1", {"env": "prod", "region": "us"}) == "viewer"
    assert role_for(held, "eu-1", {"region": "eu"}) == "operator"
    assert role_for(held, "us-1", {"env": "prod"}) is None


def test_an_unknown_role_is_ignored_not_fatal_and_never_elevates():
    held = [("superuser", parse_scope("all")), ("viewer", parse_scope("all"))]
    assert role_for(held, "x", {}) == "viewer"
    assert role_for([("superuser", parse_scope("all"))], "x", {}) is None


async def test_a_stored_unknown_role_is_skipped(sessionmaker):
    from sqlalchemy import text

    async with sessionmaker() as session:  # DDL is transactional: nothing is committed
        await session.execute(text("ALTER TABLE role_grants DROP CONSTRAINT ck_role_grants_role"))
        session.add(_grant("superuser", "all", "email", "a@example.org"))
        session.add(_grant("viewer", "leader:x", "email", "a@example.org"))
        await session.flush()
        held = await grants_held(session, {"email:a@example.org"})
        assert [(role, str(scope)) for role, scope in held] == [("viewer", "leader:x")]
        await session.rollback()
