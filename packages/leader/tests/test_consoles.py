import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select, text
from swarmscribe_leader.auth.consoles import (
    InvalidConsoleCredential,
    InvalidConsoleName,
    RevokedConsoleCredential,
    authenticate_console,
    create_console,
    revoke_console,
)
from swarmscribe_leader.auth.secrets import hash_secret
from swarmscribe_leader.clock import utcnow
from swarmscribe_leader.db.models import AuditEntry, ConsoleCredential
from swarmscribe_leader.errors import Conflict, NotFound
from swarmscribe_leader.reports import list_consoles

ADMIN = "admin@example.org (https://issuer.example.org admin-1)"


async def create(sessionmaker, name="fleet", max_role="operator", actor=ADMIN):
    async with sessionmaker() as session:
        console, credential = await create_console(
            session, name=name, max_role=max_role, actor=actor
        )
        await session.commit()
    return console, credential


async def revoke(sessionmaker, name="fleet", *, now=None, actor=ADMIN):
    async with sessionmaker() as session:
        console = await revoke_console(session, name, now=now or utcnow(), actor=actor)
        await session.commit()
    return console


async def authenticated(sessionmaker, credential):
    async with sessionmaker() as session:
        return await authenticate_console(session, credential)


async def audit_rows(sessionmaker, action):
    async with sessionmaker() as session:
        return list(
            (
                await session.scalars(
                    select(AuditEntry)
                    .where(AuditEntry.action == action)
                    .order_by(AuditEntry.created_at, AuditEntry.id)
                )
            ).all()
        )


# --- creation -------------------------------------------------------------------------


async def test_a_console_credential_is_shown_once_and_stored_only_as_its_sha256(sessionmaker):
    console, credential = await create(sessionmaker)
    assert len(credential) == 43
    async with sessionmaker() as session:
        row = await session.get(ConsoleCredential, console.id)
    assert (row.name, row.max_role, row.credential_hash, row.created_by) == (
        "fleet",
        "operator",
        hash_secret(credential),
        ADMIN,
    )
    assert (row.revoked_at, row.revoked_by) == (None, None)
    stored = " ".join(str(getattr(row, c.key)) for c in ConsoleCredential.__table__.columns)
    assert credential not in stored


async def test_creation_is_audited_without_the_credential(sessionmaker):
    console, credential = await create(sessionmaker)
    (entry,) = await audit_rows(sessionmaker, "console.create")
    assert (entry.actor, entry.subject_type, entry.subject_id) == (
        ADMIN,
        "console_credential",
        str(console.id),
    )
    assert entry.detail == {"name": "fleet", "max_role": "operator"}
    assert credential not in f"{entry.actor} {entry.subject_id} {entry.detail}"
    assert hash_secret(credential) not in str(entry.detail)


async def test_every_credential_is_different(sessionmaker):
    _, first = await create(sessionmaker, name="fleet-1")
    _, second = await create(sessionmaker, name="fleet-2")
    assert first != second


@pytest.mark.parametrize("again", ["fleet", "FLEET", "Fleet"])
async def test_a_console_name_is_never_reused_ignoring_case(sessionmaker, again):
    await create(sessionmaker, name="fleet")
    with pytest.raises(Conflict) as raised:
        await create(sessionmaker, name=again, max_role="admin")
    assert raised.value.code == "exists"
    assert "'fleet'" in raised.value.message


async def test_a_revoked_consoles_name_is_not_reused(sessionmaker):
    await create(sessionmaker)
    await revoke(sessionmaker)
    with pytest.raises(Conflict):
        await create(sessionmaker)


async def test_two_creations_of_one_name_at_once_create_one(sessionmaker):
    results = await asyncio.gather(
        create(sessionmaker), create(sessionmaker), return_exceptions=True
    )
    assert sorted(type(r).__name__ for r in results) == ["Conflict", "tuple"]
    async with sessionmaker() as session:
        assert len((await session.scalars(select(ConsoleCredential))).all()) == 1


# --- authentication -------------------------------------------------------------------


async def test_a_console_authenticates_with_its_credential(sessionmaker):
    console, credential = await create(sessionmaker)
    found = await authenticated(sessionmaker, credential)
    assert (found.id, found.name, found.max_role) == (console.id, "fleet", "operator")


def _near_miss(credential: str) -> str:
    return credential[:-1] + ("A" if credential[-1] != "A" else "B")


@pytest.mark.parametrize(
    "make",
    [
        _near_miss,
        lambda c: c[:-1],
        lambda c: c + "A",
        lambda c: "x" * 43,
        lambda c: c + " ",
        lambda c: "",
        lambda c: "\u00e9" * 43,
        lambda c: "a" * 100_000,
        hash_secret,
    ],
    ids=[
        "near-miss",
        "short",
        "long",
        "random",
        "trailing-space",
        "empty",
        "non-ascii",
        "oversize",
        "its-own-hash",
    ],
)
async def test_anything_but_the_exact_credential_is_unknown(sessionmaker, make):
    _, credential = await create(sessionmaker)
    with pytest.raises(InvalidConsoleCredential) as raised:
        await authenticated(sessionmaker, make(credential))
    assert raised.value.message == "unknown console credential"
    assert (raised.value.status, raised.value.scheme) == (401, "Console")


async def test_a_revoked_console_is_refused(sessionmaker):
    _, credential = await create(sessionmaker)
    await revoke(sessionmaker, "FLEET")
    with pytest.raises(RevokedConsoleCredential) as raised:
        await authenticated(sessionmaker, credential)
    assert raised.value.message == "this console credential has been revoked"
    assert (raised.value.console, raised.value.status) == ("fleet", 401)
    assert isinstance(raised.value, InvalidConsoleCredential)


# --- revocation and the list ----------------------------------------------------------


async def test_revoking_twice_keeps_the_first_revocation(sessionmaker):
    await create(sessionmaker)
    first = utcnow()
    await revoke(sessionmaker, now=first, actor="first-admin")
    again = await revoke(sessionmaker, now=first + timedelta(hours=1), actor="second-admin")
    assert (again.revoked_at, again.revoked_by) == (first, "first-admin")
    entries = await audit_rows(sessionmaker, "console.revoke")
    assert [e.actor for e in entries] == ["first-admin", "second-admin"]
    assert all(e.detail == {"name": "fleet"} for e in entries)


async def test_revoking_an_unknown_console_is_not_found(sessionmaker):
    with pytest.raises(NotFound):
        await revoke(sessionmaker, "nowhere")
    assert await audit_rows(sessionmaker, "console.revoke") == []


async def test_the_list_shows_every_console_without_its_credential_or_hash(sessionmaker):
    await create(sessionmaker, name="fleet-1", max_role="viewer")
    _, credential = await create(sessionmaker, name="fleet-2", max_role="admin")
    await revoke(sessionmaker, "fleet-1")
    async with sessionmaker() as session:
        rows = await list_consoles(session)
    assert [(r["name"], r["max_role"], r["revoked"]) for r in rows] == [
        ("fleet-1", "viewer", True),
        ("fleet-2", "admin", False),
    ]
    assert sorted(rows[0]) == sorted(
        [
            "id",
            "name",
            "max_role",
            "revoked",
            "revoked_at",
            "revoked_by",
            "created_by",
            "created_at",
        ]
    )
    assert credential not in str(rows)
    assert hash_secret(credential) not in str(rows)


# --- names ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "",
        " fleet",
        "fleet ",
        "-fleet",
        ".fleet",
        "fle et",
        "fleet\n",
        "fle\net",
        "fleet\u202e",
        "a" * 101,
        "\u00e9",
    ],
)
async def test_a_bad_console_name_is_refused_and_leaves_no_trace(sessionmaker, bad):
    with pytest.raises(InvalidConsoleName) as raised:
        await create(sessionmaker, name=bad)
    assert raised.value.status == 422
    assert await audit_rows(sessionmaker, "console.create") == []
    async with sessionmaker() as session:
        assert (await session.scalars(select(ConsoleCredential))).all() == []


async def test_a_case_variant_racing_creation_creates_one(sessionmaker):
    results = await asyncio.gather(
        create(sessionmaker, name="fleet"),
        create(sessionmaker, name="FLEET"),
        return_exceptions=True,
    )
    assert sorted(type(r).__name__ for r in results) == ["Conflict", "tuple"]


async def test_an_insert_that_gets_past_the_check_becomes_a_conflict(sessionmaker):
    # A row inserted outside create_console (no advisory lock) and not yet committed is
    # invisible to the name check; the unique index makes the second creator wait, then fail.
    async with sessionmaker() as other:
        await other.execute(
            text(
                "insert into console_credentials (id, name, credential_hash, max_role,"
                " created_by) values (gen_random_uuid(), 'Fleet', repeat('b', 64),"
                " 'viewer', 'test')"
            )
        )
        racing = asyncio.create_task(create(sessionmaker, name="fleet"))
        await asyncio.sleep(0.5)
        assert not racing.done()
        await other.commit()
        with pytest.raises(Conflict) as raised:
            await racing
    assert raised.value.code == "exists"
    assert await audit_rows(sessionmaker, "console.create") == []


async def test_revoking_a_malformed_name_is_not_found(sessionmaker):
    with pytest.raises(NotFound):
        await revoke(sessionmaker, "fle et" + chr(10))
