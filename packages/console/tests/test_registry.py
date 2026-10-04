import logging

import pytest
from console_testkit import ADMIN_PRINCIPAL, CREDENTIAL, all_rows_text
from sqlalchemy import select, text, update
from swarmscribe_console.crypto import CredentialUnreadable
from swarmscribe_console.db.models import AuditEntry, Leader
from swarmscribe_console.errors import Invalid
from swarmscribe_console.leaders import sealing_context, validate_base_url

NEW_CREDENTIAL = "N" * 43


@pytest.fixture
async def admin(client, factory):
    await factory.console_admin("email", "admin@example.org")
    csrf = await factory.person(client, principals={ADMIN_PRINCIPAL}, email="admin@example.org")
    client.headers["X-CSRF-Token"] = csrf
    return client


def _body(**overrides):
    body = {
        "name": "eu-1",
        "base_url": "https://eu-1.leaders.example",
        "labels": {"env": "prod", "region": "eu=west"},
        "credential": CREDENTIAL,
    }
    body.update(overrides)
    return body


def _open(keys, name, base_url, sealed) -> str:
    return keys.open_credential(sealing_context(name, base_url), sealed)


async def _leader(sessionmaker, name="eu-1") -> Leader:
    async with sessionmaker() as session:
        return (await session.scalars(select(Leader).where(Leader.name == name))).one()


async def _audit(sessionmaker) -> list[AuditEntry]:
    async with sessionmaker() as session:
        return list((await session.scalars(select(AuditEntry).order_by(AuditEntry.id))).all())


# --- adding -----------------------------------------------------------------------------


async def test_a_console_admin_registers_a_leader(admin, sessionmaker):
    answer = await admin.post("/api/admin/leaders", json=_body())
    assert answer.status_code == 201
    body = answer.json()
    assert body["name"] == "eu-1"
    assert body["base_url"] == "https://eu-1.leaders.example"
    assert body["labels"] == {"env": "prod", "region": "eu=west"}
    assert body["enabled"] is True
    assert body["added_by"].startswith("admin@example.org (https://login.microsoftonline.com/")
    assert "credential" not in body
    assert CREDENTIAL not in answer.text
    listed = await admin.get("/api/admin/leaders")
    assert [leader["name"] for leader in listed.json()] == ["eu-1"]
    assert CREDENTIAL not in listed.text
    (entry,) = await _audit(sessionmaker)
    assert (entry.action, entry.leader, entry.outcome) == ("leader.add", "eu-1", "ok")
    assert CREDENTIAL not in str(entry.detail)


async def test_the_credential_is_encrypted_at_rest(admin, sessionmaker, engine, keys):
    await admin.post("/api/admin/leaders", json=_body())
    async with engine.connect() as conn:
        raw = await conn.scalar(text("select credential from leaders where name = 'eu-1'"))
    assert CREDENTIAL.encode() not in raw
    assert _open(keys, "eu-1", "https://eu-1.leaders.example", raw) == CREDENTIAL


async def test_a_sealed_credential_moved_to_another_leader_does_not_open(
    admin, sessionmaker, keys
):
    await admin.post("/api/admin/leaders", json=_body())
    await admin.post("/api/admin/leaders", json=_body(name="us-1", base_url="https://us-1.x.org"))
    eu = await _leader(sessionmaker, "eu-1")
    async with sessionmaker() as session:
        await session.execute(
            update(Leader).where(Leader.name == "us-1").values(credential=eu.credential)
        )
        await session.commit()
    us = await _leader(sessionmaker, "us-1")
    with pytest.raises(CredentialUnreadable):
        _open(keys, "us-1", us.base_url, us.credential)


@pytest.mark.parametrize("name", ["EU-1", "eu-1"])
async def test_leader_names_are_unique_ignoring_case(admin, name):
    await admin.post("/api/admin/leaders", json=_body())
    answer = await admin.post("/api/admin/leaders", json=_body(name=name))
    assert answer.status_code == 409
    assert answer.json()["code"] == "exists"


@pytest.mark.parametrize(
    "name", ["", "../eu", "a/b", ".hidden", "eu 1", "x" * 101, "eu%2F1", "café"]
)
async def test_odd_leader_names_are_refused(admin, name):
    answer = await admin.post("/api/admin/leaders", json=_body(name=name))
    assert answer.status_code == 422
    assert answer.json()["code"] == "invalid_name"


@pytest.mark.parametrize(
    "credential", ["short", "x" * 44, "x" * 42 + "!", "", "é" * 43]
)
async def test_a_credential_not_in_c1s_format_is_refused_without_echo(admin, credential):
    answer = await admin.post("/api/admin/leaders", json=_body(credential=credential))
    assert answer.status_code == 422
    assert answer.json()["code"] == "invalid_credential"
    if credential:
        assert credential not in answer.text


@pytest.mark.parametrize(
    "labels",
    [
        {"Env": "prod"},
        {"env": "has space"},
        {"env": ""},
        {"env=x": "prod"},
        {"env": "x" * 256},
        {f"k{i}": "v" for i in range(33)},
    ],
)
async def test_odd_labels_are_refused(admin, labels):
    answer = await admin.post("/api/admin/leaders", json=_body(labels=labels))
    assert answer.status_code == 422
    assert answer.json()["code"] == "invalid_labels"


# --- leader URLs ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "kept"),
    [
        ("https://leader.example.org", "https://leader.example.org"),
        ("https://Leader.Example.org/", "https://leader.example.org"),
        ("https://leader.example.org:443", "https://leader.example.org"),
        ("https://10.0.0.5:8443", "https://10.0.0.5:8443"),
        ("https://192.168.1.50", "https://192.168.1.50"),
        ("https://[fd00::5]", "https://[fd00::5]"),
        ("https://172.16.0.9:8443", "https://172.16.0.9:8443"),
        ("https://leader.example.org:65535", "https://leader.example.org:65535"),
        ("https://100.64.0.1", "https://100.64.0.1"),
        ("https://swarmscribe-leader", "https://swarmscribe-leader"),
        ("https://leader.example.org/swarmscribe/", "https://leader.example.org/swarmscribe"),
    ],
)
def test_https_leader_urls_are_normalised(given, kept):
    assert validate_base_url(given) == kept


@pytest.mark.parametrize(
    "bad",
    [
        "http://leader.example.org",
        "ftp://leader.example.org",
        "leader.example.org",
        "https://user:pw@leader.example.org",
        "https://user@leader.example.org",
        "https://leader.example.org?x=1",
        "https://leader.example.org#top",
        "https://127.0.0.1",
        "https://127.1",
        "https://0x7f.1",
        "https://127.8.9.10",
        "https://localhost",
        "https://LOCALHOST.",
        "https://api.localhost",
        "https://169.254.169.254",
        "https://[fe80::1]",
        "https://[::1]",
        "https://[::ffff:127.0.0.1]",
        "https://0.0.0.0",
        "https://224.0.0.1",
        "https://240.0.0.1",
        "https://2130706433",
        "https://0x7f000001",
        "https://metadata.google.internal",
        "https://metadata.google.internal.",
        "https://localhost.",
        "https://metadata",
        "https://[fd00:ec2::254]",
        "https://[FD00:EC2:0:0:0:0:0:254]",
        "https://[::ffff:169.254.169.254]",
        "https://100.100.100.200",
        "https://[::ffff:100.100.100.200]",
        "https://[fe80::]",
        "https://[fe80::1%25eth0]",
        "https://leader.example.org.",
        "https://leader.example.org:65536",
        "https://leader.example.org:-1",
        "https://[fd00:ec2::254%251]",
        "https://[fd00::5%25eth0]",
        "https://[fe80::1%25lo]",
        "https://[::1",
        "https://[fd00::5]x",
        "https://host]",
        "https://[v1.x]",
        "https://ip6-localhost",
        "https://IP6-Loopback.",
        "https://Localhost.Localdomain",
        "https://localhost.localdomain.",
        "https://instance-data",
        "https://INSTANCE-DATA.",
        "https://instance-data.ec2.internal",
        "https://[2002:a9fe:a9fe::1]",
        "https://[2002::]",
        "https://[2001:0:4136:e378::1]",
        "https://[64:ff9b::a9fe:a9fe]",
        "https://[64:ff9b::7f00:1]",
        "https://[fec0::1]",
        "https://0.1.2.3",
        "https://leader.example.org/../admin",
        "https://leader.example.org/a/./b",
        "https://leader.example.org:0",
        "https://leader.example.org:99999",
        "https://leader.example.org:port",
        "https://lea der.example.org",
        "https://leader.exämple.org",
        "https://leader.example.org/\r\nX-Injected: 1",
        "https://",
        "https://" + "a" * 2000 + ".org",
    ],
)
def test_other_leader_urls_are_refused(bad):
    with pytest.raises(Invalid) as raised:
        validate_base_url(bad)
    assert raised.value.code == "invalid_url"


async def test_the_api_refuses_a_metadata_address(admin):
    answer = await admin.post(
        "/api/admin/leaders", json=_body(base_url="https://169.254.169.254")
    )
    assert answer.status_code == 422
    assert answer.json()["code"] == "invalid_url"


# --- editing, rotation, removal ---------------------------------------------------------


async def test_a_leader_is_edited_in_place(admin, sessionmaker):
    await admin.post("/api/admin/leaders", json=_body())
    answer = await admin.patch(
        "/api/admin/leaders/EU-1",
        json={"labels": {"env": "test"}, "enabled": False},
    )
    assert answer.status_code == 200
    body = answer.json()
    assert (body["labels"], body["enabled"], body["base_url"]) == (
        {"env": "test"},
        False,
        "https://eu-1.leaders.example",
    )
    entry = (await _audit(sessionmaker))[-1]
    assert (entry.action, entry.leader) == ("leader.edit", "eu-1")
    assert entry.detail == {"labels": {"env": "test"}, "enabled": False}


async def test_editing_refuses_unknown_fields(admin):
    await admin.post("/api/admin/leaders", json=_body())
    assert (await admin.patch("/api/admin/leaders/eu-1", json={"name": "eu-2"})).status_code == 422


async def test_a_credential_alone_is_not_an_edit(admin):
    await admin.post("/api/admin/leaders", json=_body())
    answer = await admin.patch("/api/admin/leaders/eu-1", json={"credential": NEW_CREDENTIAL})
    assert answer.status_code == 422
    assert answer.json()["code"] == "use_rotate"
    assert NEW_CREDENTIAL not in answer.text


async def test_changing_the_url_needs_the_credential_in_the_same_request(
    admin, sessionmaker, keys
):
    await admin.post("/api/admin/leaders", json=_body())
    before = len(await _audit(sessionmaker))
    answer = await admin.patch("/api/admin/leaders/eu-1", json={"base_url": "https://evil.x.org"})
    assert answer.status_code == 422
    assert answer.json()["code"] == "credential_required"
    leader = await _leader(sessionmaker)
    assert leader.base_url == "https://eu-1.leaders.example"
    assert _open(keys, "eu-1", leader.base_url, leader.credential) == CREDENTIAL
    assert [e.action for e in (await _audit(sessionmaker))[before:]] == ["request.refused"]


async def test_changing_the_url_with_the_credential_reseals_and_audits_both(
    admin, sessionmaker, keys
):
    await admin.post("/api/admin/leaders", json=_body())
    answer = await admin.patch(
        "/api/admin/leaders/eu-1",
        json={"base_url": "https://eu.x.org/", "credential": NEW_CREDENTIAL},
    )
    assert answer.status_code == 200
    assert answer.json()["base_url"] == "https://eu.x.org"
    assert NEW_CREDENTIAL not in answer.text
    leader = await _leader(sessionmaker)
    assert _open(keys, "eu-1", leader.base_url, leader.credential) == NEW_CREDENTIAL
    assert leader.credential_updated_by.startswith("admin@example.org")
    entries = (await _audit(sessionmaker))[1:]
    assert [(e.action, e.leader) for e in entries] == [
        ("leader.edit", "eu-1"),
        ("leader.credential_replace", "eu-1"),
    ]
    assert entries[0].detail == {"base_url": "https://eu.x.org"}
    assert NEW_CREDENTIAL not in str([e.detail for e in entries])


async def test_the_same_url_needs_no_credential(admin):
    await admin.post("/api/admin/leaders", json=_body())
    answer = await admin.patch(
        "/api/admin/leaders/eu-1", json={"base_url": "https://EU-1.leaders.example/"}
    )
    assert answer.status_code == 200


async def test_a_new_url_that_breaks_the_rules_is_refused_before_anything_changes(
    admin, sessionmaker
):
    await admin.post("/api/admin/leaders", json=_body())
    answer = await admin.patch(
        "/api/admin/leaders/eu-1",
        json={"base_url": "https://169.254.169.254", "credential": NEW_CREDENTIAL},
    )
    assert answer.status_code == 422
    assert answer.json()["code"] == "invalid_url"
    assert (await _leader(sessionmaker)).base_url == "https://eu-1.leaders.example"


async def test_rotation_replaces_the_credential_in_place(admin, sessionmaker, keys):
    await admin.post("/api/admin/leaders", json=_body())
    async with sessionmaker() as session:
        await session.execute(
            update(Leader).values(
                credential_revoked_at=text("now()"),
                consecutive_failures=5,
                last_error="credential_revoked",
                last_polled_at=text("now()"),
            )
        )
        await session.commit()
    answer = await admin.put(
        "/api/admin/leaders/eu-1/credential", json={"credential": NEW_CREDENTIAL}
    )
    assert answer.status_code == 200
    assert answer.json()["credential_revoked"] is False
    assert NEW_CREDENTIAL not in answer.text
    leader = await _leader(sessionmaker)
    assert _open(keys, "eu-1", leader.base_url, leader.credential) == NEW_CREDENTIAL
    assert (
        leader.credential_revoked_at,
        leader.consecutive_failures,
        leader.last_error,
        leader.last_polled_at,
    ) == (None, 0, None, None)
    assert leader.credential_updated_by.startswith("admin@example.org")
    entry = (await _audit(sessionmaker))[-1]
    assert (entry.action, entry.leader) == ("leader.credential_replace", "eu-1")
    assert NEW_CREDENTIAL not in str(entry.detail)


async def test_a_leader_is_removed(admin, sessionmaker):
    await admin.post("/api/admin/leaders", json=_body())
    assert (await admin.delete("/api/admin/leaders/eu-1")).status_code == 204
    assert (await admin.get("/api/admin/leaders")).json() == []
    assert (await _audit(sessionmaker))[-1].action == "leader.remove"
    assert (await admin.delete("/api/admin/leaders/eu-1")).status_code == 404


@pytest.mark.parametrize("name", ["us-9", "..", "a%2Fb"])
async def test_an_unknown_leader_is_not_found(admin, name):
    answer = await admin.patch(f"/api/admin/leaders/{name}", json={"enabled": False})
    assert answer.status_code == 404


# --- who may ----------------------------------------------------------------------------


async def test_a_leader_admin_who_is_not_a_console_admin_cannot_manage_the_registry(
    client, factory, sessionmaker
):
    await factory.grant("admin", "all", "email", "person@example.org")
    csrf = await factory.person(client, principals={"email:person@example.org"})
    client.headers["X-CSRF-Token"] = csrf
    calls = [
        client.get("/api/admin/leaders"),
        client.post("/api/admin/leaders", json=_body()),
        client.patch("/api/admin/leaders/eu-1", json={"enabled": False}),
        client.put("/api/admin/leaders/eu-1/credential", json={"credential": NEW_CREDENTIAL}),
        client.delete("/api/admin/leaders/eu-1"),
    ]
    for call in calls:
        answer = await call
        assert answer.status_code == 403
        assert answer.json()["code"] == "forbidden"
    refused = [e for e in await _audit(sessionmaker) if e.action == "request.refused"]
    assert len(refused) == 4  # the four changes; reads are not audited


# --- the credential never leaks --------------------------------------------------------


async def test_validation_messages_never_echo_labels_or_credentials(admin):
    secret_key, secret_value = "zzsecretkey", "zzsecretvalue" + "!" * 300
    for labels in ({secret_key.upper(): "ok"}, {"env": secret_value}, {secret_key: 7}):
        answer = await admin.post("/api/admin/leaders", json=_body(labels=labels))
        assert answer.status_code == 422
        assert secret_key not in answer.text.lower()
        assert "zzsecretvalue" not in answer.text
    answer = await admin.post("/api/admin/leaders", json=_body(credential=["zzcred"]))
    assert answer.status_code == 422
    assert "zzcred" not in answer.text


async def test_the_credential_reaches_no_response_log_audit_row_or_table(
    admin, sessionmaker, engine, caplog
):
    caplog.set_level(logging.DEBUG)
    third, bad = "E" * 43, "badcredential" + "x" * 40
    secrets_used = [CREDENTIAL, NEW_CREDENTIAL, third, bad]
    answers = [
        await admin.post("/api/admin/leaders", json=_body()),
        await admin.get("/api/admin/leaders"),
        await admin.put("/api/admin/leaders/eu-1/credential", json={"credential": NEW_CREDENTIAL}),
        await admin.patch(
            "/api/admin/leaders/eu-1",
            json={"base_url": "https://eu.x.org", "credential": third},
        ),
        await admin.patch("/api/admin/leaders/eu-1", json={"base_url": "https://eu.y.org"}),
        await admin.put("/api/admin/leaders/eu-1/credential", json={"credential": bad}),
        await admin.post("/api/admin/leaders", json=_body(name="eu-2", credential=bad)),
        await admin.get("/api/admin/leaders"),
    ]
    assert [a.status_code for a in answers] == [201, 200, 200, 200, 422, 422, 422, 200]
    everything = await all_rows_text(engine)
    async with engine.connect() as conn:
        sealed = await conn.scalar(text("select credential from leaders where name = 'eu-1'"))
    for secret in secrets_used:
        assert secret not in caplog.text
        assert secret not in everything
        assert secret.encode() not in sealed
        for answer in answers:
            assert secret not in answer.text
    for entry in await _audit(sessionmaker):
        for secret in secrets_used:
            assert secret not in str(entry.detail) + entry.action + str(entry.target)


def test_a_credential_is_not_part_of_a_request_models_repr():
    from swarmscribe_console.api.models import CredentialIn, LeaderIn

    assert CREDENTIAL not in repr(CredentialIn(credential=CREDENTIAL))
    request = LeaderIn(name="a", base_url="https://a.org", credential=CREDENTIAL)
    assert CREDENTIAL not in repr(request)


async def test_non_ascii_names_and_credentials_are_refused_before_sealing(sessionmaker, keys):
    from swarmscribe_console.leaders import add_leader

    async with sessionmaker() as session:
        for name, credential in (("caf\u00e9", CREDENTIAL), ("eu-1", "\u00e9" * 43)):
            with pytest.raises(Invalid) as raised:
                await add_leader(
                    session,
                    keys,
                    name=name,
                    base_url="https://a.org",
                    labels={},
                    credential=credential,
                    enabled=True,
                    actor="t",
                )
            assert raised.value.code in ("invalid_name", "invalid_credential")
            assert credential not in str(raised.value)


async def test_malformed_brackets_are_refused_and_audited_not_a_500(admin, sessionmaker):
    for bad in ("https://[::1", "https://[fd00::5]x", "https://host]", "https://[v1.x]"):
        before = len(await _audit(sessionmaker))
        answer = await admin.post("/api/admin/leaders", json=_body(base_url=bad))
        assert (answer.status_code, answer.json()["code"]) == (422, "invalid_url")
        assert [e.action for e in (await _audit(sessionmaker))[before:]] == ["request.refused"]


async def test_the_url_is_bound_into_the_seal(admin, sessionmaker, keys):
    await admin.post("/api/admin/leaders", json=_body())
    leader = await _leader(sessionmaker)
    async with sessionmaker() as session:
        await session.execute(
            update(Leader).where(Leader.name == "eu-1").values(base_url="https://other.example")
        )
        await session.commit()
    moved = await _leader(sessionmaker)
    with pytest.raises(CredentialUnreadable):
        keys.open_credential(sealing_context(moved.name, moved.base_url), moved.credential)
    assert keys.open_credential(
        sealing_context("EU-1", leader.base_url), leader.credential
    ) == CREDENTIAL


async def test_a_credential_with_an_unchanged_url_is_for_the_rotation_route(admin):
    await admin.post("/api/admin/leaders", json=_body())
    answer = await admin.patch(
        "/api/admin/leaders/eu-1",
        json={"base_url": "https://eu-1.leaders.example/", "credential": NEW_CREDENTIAL},
    )
    assert (answer.status_code, answer.json()["code"]) == (422, "use_rotate")
    assert NEW_CREDENTIAL not in answer.text


async def test_an_edit_that_changes_nothing_writes_no_audit_row(admin, sessionmaker):
    await admin.post("/api/admin/leaders", json=_body())
    await admin.patch("/api/admin/leaders/eu-1", json={})
    assert [e.action for e in await _audit(sessionmaker)] == ["leader.add"]


def test_the_edit_models_credential_is_not_in_its_repr():
    from swarmscribe_console.api.models import LeaderEdit

    assert CREDENTIAL not in repr(LeaderEdit(credential=CREDENTIAL))
