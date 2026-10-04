import logging
import time

import pytest
from swarmscribe_console.leader_client import (
    MAX_BODY_BYTES,
    POLLER_ACTOR,
    ActorNotRepresentable,
    LeaderBadAnswer,
    LeaderReply,
    LeaderTarget,
    LeaderUnreachable,
    is_revoked,
    person_actor,
)
from swarmscribe_leader.auth.consoles import parse_delegation

ISSUER = "https://login.microsoftonline.com/0f0e0d0c-0b0a-4908-8706-050403020100/v2.0"
CREDENTIAL = "k" * 21 + "_" + "Q" * 21
HOST = "eu-1.leaders.example"
TARGET = LeaderTarget(name="eu-1", base_url=f"https://{HOST}", credential=CREDENTIAL)


# --- who the console says it acts for ---------------------------------------------------


def test_a_person_is_sent_as_issuer_subject_and_email():
    actor = person_actor(ISSUER, "entra-person-1", "person@example.org")
    assert actor == f"{ISSUER} entra-person-1 person@example.org"
    who, role = parse_delegation([actor], ["operator"])
    assert (who.issuer, who.subject, who.email, role) == (
        ISSUER,
        "entra-person-1",
        "person@example.org",
        "operator",
    )


@pytest.mark.parametrize(
    "email",
    [
        None,
        "",
        "pérson@example.org",
        "per son@example.org",
        "a@b@example.org",
        "no-at-sign",
        "x\r\nX-Evil: 1@example.org",
        "a" * 250 + "@x.org",
    ],
    ids=["none", "empty", "non-ascii", "space", "two-at", "no-at", "crlf", "too-long"],
)
def test_an_email_the_leader_would_refuse_is_sent_as_a_dash(email):
    actor = person_actor(ISSUER, "sub-1", email)
    assert actor == f"{ISSUER} sub-1 -"
    who, _ = parse_delegation([actor], ["viewer"])
    assert who.email is None


@pytest.mark.parametrize(
    ("issuer", "subject"),
    [
        ("http://issuer.example", "s"),
        ("https://", "s"),
        ("https://iss uer.example", "s"),
        (ISSUER, "has space"),
        (ISSUER, "sübject"),
        (ISSUER, ""),
        (ISSUER, "s" * 256),
        (ISSUER, "s\r\nX-Evil: 1"),
        ("https://" + "i" * 250, "s"),
    ],
    ids=[
        "http-issuer",
        "bare-https",
        "issuer-space",
        "subject-space",
        "subject-non-ascii",
        "empty-subject",
        "long-subject",
        "subject-crlf",
        "long-issuer",
    ],
)
def test_an_identity_the_leader_cannot_take_is_refused(issuer, subject):
    with pytest.raises(ActorNotRepresentable) as raised:
        person_actor(issuer, subject, "a@example.org")
    assert (raised.value.status, raised.value.code) == (403, "actor_not_representable")


def test_the_poller_actor_is_c1s():
    who, role = parse_delegation([POLLER_ACTOR], ["viewer"])
    assert who.is_poller and role == "viewer"


# --- calls ------------------------------------------------------------------------------


async def test_a_call_sends_the_console_credential_and_the_delegation(leader_client, fake_leader):
    fake_leader.replies[("GET", "/v1/admin/jobs")] = (200, [{"id": "j-1"}], {})
    actor = person_actor(ISSUER, "entra-person-1", "person@example.org")
    reply = await leader_client.call(
        TARGET,
        "GET",
        "/v1/admin/jobs",
        actor=actor,
        role="operator",
        timeout=2,
        params={"state": "queued"},
    )
    assert (reply.status, reply.body) == (200, [{"id": "j-1"}])
    (request,) = fake_leader.requests
    assert str(request.url) == f"https://{HOST}/v1/admin/jobs?state=queued"
    assert request.headers["authorization"] == f"Console {CREDENTIAL}"
    assert request.headers["x-swarmscribe-actor"] == actor
    assert request.headers["x-swarmscribe-actor-role"] == "operator"
    parse_delegation(
        request.headers.get_list("x-swarmscribe-actor"),
        request.headers.get_list("x-swarmscribe-actor-role"),
    )


async def test_a_json_body_is_sent_as_json(leader_client, fake_leader):
    await leader_client.call(
        TARGET,
        "POST",
        "/v1/admin/jobs/x/priority",
        actor=POLLER_ACTOR,
        role="viewer",
        timeout=2,
        json_body={"priority": 5},
    )
    assert fake_leader.bodies[0] in (b'{"priority": 5}', b'{"priority":5}')
    assert fake_leader.requests[0].headers["content-type"] == "application/json"


async def test_a_leader_under_a_path_prefix_is_called_under_it(leader_client, fake_leader):
    target = LeaderTarget("eu-1", f"https://{HOST}/swarmscribe", CREDENTIAL)
    reply = await leader_client.call(
        target, "GET", "/v1/admin/status", actor=POLLER_ACTOR, role="viewer", timeout=2
    )
    assert reply.status == 200
    assert fake_leader.requests[0].url.path == "/swarmscribe/v1/admin/status"


async def test_a_redirect_is_never_followed(leader_client, fake_leader):
    fake_leader.replies[("GET", "/v1/admin/status")] = (
        302,
        b"",
        {"Location": "https://elsewhere.example/steal"},
    )
    reply = await leader_client.call(
        TARGET, "GET", "/v1/admin/status", actor=POLLER_ACTOR, role="viewer", timeout=2
    )
    assert reply.status == 302
    assert len(fake_leader.requests) == 1


async def test_a_slow_leader_is_unreachable_once_the_timeout_passes(leader_client, fake_leader):
    fake_leader.modes[HOST] = "slow"
    fake_leader.delay = 2.0
    started = time.monotonic()
    with pytest.raises(LeaderUnreachable) as raised:
        await leader_client.call(
            TARGET, "GET", "/v1/admin/status", actor=POLLER_ACTOR, role="viewer", timeout=0.2
        )
    assert raised.value.reason == "timeout"
    assert time.monotonic() - started < 1.5


async def test_a_refused_connection_is_unreachable(leader_client, fake_leader):
    fake_leader.modes[HOST] = "down"
    with pytest.raises(LeaderUnreachable) as raised:
        await leader_client.call(
            TARGET, "GET", "/v1/admin/status", actor=POLLER_ACTOR, role="viewer", timeout=2
        )
    assert raised.value.reason == "connect_error"


async def test_an_oversized_answer_is_refused(leader_client, fake_leader):
    fake_leader.replies[("GET", "/v1/admin/status")] = (
        200,
        b"[" + b"0," * (MAX_BODY_BYTES // 2) + b"0]",
        {"Content-Type": "application/json"},
    )
    with pytest.raises(LeaderBadAnswer):
        await leader_client.call(
            TARGET, "GET", "/v1/admin/status", actor=POLLER_ACTOR, role="viewer", timeout=10
        )


async def test_a_non_json_answer_has_no_body(leader_client, fake_leader):
    fake_leader.replies[("GET", "/v1/admin/status")] = (
        500,
        b"<html>oops</html>",
        {"Content-Type": "text/html"},
    )
    reply = await leader_client.call(
        TARGET, "GET", "/v1/admin/status", actor=POLLER_ACTOR, role="viewer", timeout=2
    )
    assert (reply.status, reply.body) == (500, None)


REVOKED_TEXT = "this console credential has been revoked"


@pytest.mark.parametrize(
    ("reply", "revoked"),
    [
        (LeaderReply(401, {"code": "credential_revoked", "message": REVOKED_TEXT}, None), True),
        (LeaderReply(401, {"code": "credential_revoked", "message": "other"}, None), True),
        (LeaderReply(401, {"code": "unauthorized", "message": REVOKED_TEXT}, None), False),
        (LeaderReply(401, {"code": "unauthorized", "message": "unknown"}, None), False),
        (LeaderReply(403, {"code": "credential_revoked", "message": REVOKED_TEXT}, None), False),
        (LeaderReply(401, {"code": "CREDENTIAL_REVOKED", "message": "x"}, None), False),
        (LeaderReply(401, ["credential_revoked"], None), False),
        (LeaderReply(401, None, None), False),
    ],
    ids=[
        "c1b",
        "code-any-message",
        "message-alone",
        "unknown",
        "not-401",
        "other-case",
        "not-an-object",
        "no-body",
    ],
)
def test_revocation_is_recognised_by_the_code_only(reply, revoked):
    assert is_revoked(reply) is revoked


async def test_the_credential_never_reaches_the_logs(leader_client, fake_leader, caplog):
    caplog.set_level(logging.DEBUG)
    await leader_client.call(
        TARGET, "GET", "/v1/admin/status", actor=POLLER_ACTOR, role="viewer", timeout=2
    )
    fake_leader.modes[HOST] = "down"
    with pytest.raises(LeaderUnreachable):
        await leader_client.call(
            TARGET, "GET", "/v1/admin/status", actor=POLLER_ACTOR, role="viewer", timeout=2
        )
    assert CREDENTIAL not in caplog.text
    assert CREDENTIAL not in repr(TARGET)


# --- hostile cases beyond the plan --------------------------------------------------------


async def test_a_redirect_sends_the_credential_nowhere_else(leader_client, fake_leader):
    fake_leader.replies[("GET", "/v1/admin/status")] = (
        307,
        b"",
        {"Location": "https://elsewhere.example/steal"},
    )
    await leader_client.call(
        TARGET, "POST", "/v1/admin/status", actor=POLLER_ACTOR, role="viewer", timeout=2
    )
    await leader_client.call(
        TARGET, "GET", "/v1/admin/status", actor=POLLER_ACTOR, role="viewer", timeout=2
    )
    assert {r.url.host for r in fake_leader.requests} == {HOST}


def test_the_client_ignores_proxy_and_ca_environment(monkeypatch):
    from swarmscribe_console.leader_client import LeaderClient

    monkeypatch.setenv("HTTPS_PROXY", "http://evil.example:3128")
    monkeypatch.setenv("SSL_CERT_FILE", "/nonexistent/ca.pem")
    client = LeaderClient()
    assert client._client.trust_env is False
    assert client._client.follow_redirects is False


@pytest.mark.parametrize(
    "actor",
    [
        "system:poller\r\nX-Evil: 1",
        f"{ISSUER} sub\x00 -",
        f"{ISSUER} sub\x7f -",
        f"{ISSUER} sub -\n",
        f"{ISSUER}  sub -",
        "https://iss.example sub a@b@c",
        "not an actor",
        "",
        "system:poller ",
    ],
    ids=["crlf", "nul", "del", "trailing-lf", "double-space", "two-at", "garbage", "empty", "pad"],
)
async def test_a_hostile_actor_is_refused_before_any_request(leader_client, fake_leader, actor):
    with pytest.raises(ActorNotRepresentable):
        await leader_client.call(
            TARGET, "GET", "/v1/admin/status", actor=actor, role="viewer", timeout=2
        )
    assert fake_leader.requests == []


@pytest.mark.parametrize("role", ["root", "", "viewer\r\nX: 1", "Viewer"])
async def test_a_bad_role_is_refused_before_any_request(leader_client, fake_leader, role):
    with pytest.raises(ActorNotRepresentable):
        await leader_client.call(
            TARGET, "GET", "/v1/admin/status", actor=POLLER_ACTOR, role=role, timeout=2
        )
    assert fake_leader.requests == []


async def test_the_poller_may_not_ask_for_more_than_viewer(leader_client, fake_leader):
    with pytest.raises(ActorNotRepresentable):
        await leader_client.call(
            TARGET, "GET", "/v1/admin/status", actor=POLLER_ACTOR, role="admin", timeout=2
        )
    assert fake_leader.requests == []


async def test_a_401_is_told_apart_by_its_code_over_the_wire(leader_client, fake_leader):
    results = {}
    for mode in ("revoked", "unknown"):
        fake_leader.modes[HOST] = mode
        reply = await leader_client.call(
            TARGET, "GET", "/v1/admin/status", actor=POLLER_ACTOR, role="viewer", timeout=2
        )
        results[mode] = (reply.status, is_revoked(reply))
    assert results == {"revoked": (401, True), "unknown": (401, False)}


async def test_the_total_timeout_holds_against_a_trickling_leader(leader_client, fake_leader):
    # A body that never finishes within the budget is a timeout, not a hang.
    import asyncio

    async def stall(request):
        await asyncio.sleep(5)

    fake_leader.on_request = stall
    started = time.monotonic()
    with pytest.raises(LeaderUnreachable) as raised:
        await leader_client.call(
            TARGET, "GET", "/v1/admin/status", actor=POLLER_ACTOR, role="viewer", timeout=0.2
        )
    assert raised.value.reason == "timeout"
    assert time.monotonic() - started < 1.5


async def test_an_oversized_answer_is_refused_by_what_arrives_not_what_is_claimed(
    leader_client, fake_leader
):
    fake_leader.replies[("GET", "/v1/admin/status")] = (
        200,
        b"x" * (MAX_BODY_BYTES + 1),
        {"Content-Type": "text/plain"},
    )
    with pytest.raises(LeaderBadAnswer):
        await leader_client.call(
            TARGET, "GET", "/v1/admin/status", actor=POLLER_ACTOR, role="viewer", timeout=10
        )


async def test_a_non_json_success_is_a_bad_answer(leader_client, fake_leader):
    fake_leader.replies[("GET", "/v1/admin/status")] = (
        200,
        b"<html>captive portal</html>",
        {"Content-Type": "text/html"},
    )
    with pytest.raises(LeaderBadAnswer):
        await leader_client.call(
            TARGET, "GET", "/v1/admin/status", actor=POLLER_ACTOR, role="viewer", timeout=2
        )


async def test_malformed_json_claiming_to_be_json_is_a_bad_answer_on_success(
    leader_client, fake_leader
):
    fake_leader.replies[("GET", "/v1/admin/status")] = (
        200,
        b"{not json",
        {"Content-Type": "application/json"},
    )
    with pytest.raises(LeaderBadAnswer):
        await leader_client.call(
            TARGET, "GET", "/v1/admin/status", actor=POLLER_ACTOR, role="viewer", timeout=2
        )


async def test_an_empty_success_has_no_body(leader_client, fake_leader):
    fake_leader.replies[("DELETE", "/v1/admin/x")] = (204, b"", {})
    reply = await leader_client.call(
        TARGET, "DELETE", "/v1/admin/x", actor=POLLER_ACTOR, role="viewer", timeout=2
    )
    assert (reply.status, reply.body) == (204, None)


async def test_the_credential_never_reaches_exceptions_or_reprs(leader_client, fake_leader):
    import traceback

    seen = []
    for mode in ("down", "slow"):
        fake_leader.modes[HOST] = mode
        fake_leader.delay = 2.0
        with pytest.raises(LeaderUnreachable) as raised:
            await leader_client.call(
                TARGET, "GET", "/v1/admin/status", actor=POLLER_ACTOR, role="viewer", timeout=0.1
            )
        seen.append("".join(traceback.format_exception(raised.value)))
        seen.append(repr(raised.value))
    fake_leader.modes[HOST] = "ok"
    reply = await leader_client.call(
        TARGET, "GET", "/v1/admin/status", actor=POLLER_ACTOR, role="viewer", timeout=2
    )
    with pytest.raises(ActorNotRepresentable) as bad:
        await leader_client.call(
            TARGET, "GET", "/x", actor="bad actor", role="viewer", timeout=2
        )
    seen += [repr(reply), repr(leader_client), str(TARGET), repr(TARGET), str(bad.value)]
    assert all(CREDENTIAL not in text for text in seen)
