import json

import httpx
import pytest
from swarmscribe_follower.leader import (
    Interrupted,
    LeaderClient,
    NoWork,
    Refused,
    Transient,
    retrying,
)
from swarmscribe_protocol import Capabilities, OutputChecksums

JOB = "2f0d1c1e-0000-4000-8000-000000000001"
LEASE = "2f0d1c1e-0000-4000-8000-000000000002"
CAPABILITIES = Capabilities(
    device="cpu", models=["tiny.en"], engine_version="0.1.0", pool="default"
)
LINK = {"url": "https://leader.test/v1/files/token", "method": "PUT"}
CLAIM = {
    "job_id": JOB,
    "lease_id": LEASE,
    "download_url": {"url": "https://leader.test/v1/files/in", "method": "GET"},
    "upload_urls": {"txt": LINK, "srt": LINK, "segments_json": LINK},
    "settings": {"model": "tiny.en", "compute_type": "int8"},
    "vocabulary": {"version": 0},
    "source_version": "10-1-1",
}


def client_for(handler, credential="the-credential"):
    seen = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = LeaderClient(
        "https://leader.test/", credential=credential, transport=httpx.MockTransport(recording)
    )
    return client, seen


def test_register_sends_the_token_and_no_bearer():
    answer = {
        "follower_id": "f1",
        "credential": "new-credential",
        "heartbeat_interval": 30,
        "lease_seconds": 120,
    }
    client, seen = client_for(lambda request: httpx.Response(200, json=answer), credential=None)
    registered = client.register("join-token", CAPABILITIES)
    assert (registered.credential, registered.heartbeat_interval) == ("new-credential", 30)
    (request,) = seen
    assert request.url == "https://leader.test/v1/followers/register"
    assert "authorization" not in request.headers
    body = json.loads(request.content)
    assert (body["join_token"], body["protocol_version"]) == ("join-token", 1)
    assert request.headers["user-agent"].startswith("swarmscribe-follower/")


def test_every_other_call_carries_the_bearer_and_its_lease():
    client, seen = client_for(lambda request: httpx.Response(204))
    h = "a" * 64
    client.submit(JOB, LEASE, OutputChecksums(source=h, txt=h, srt=h, segments_json=h))
    client.fail(JOB, LEASE, "other", "x" * 5000, True)
    client.release(JOB, LEASE)
    client.deregister()
    assert [request.url.path for request in seen] == [
        f"/v1/jobs/{JOB}/submit",
        f"/v1/jobs/{JOB}/fail",
        f"/v1/jobs/{JOB}/release",
        "/v1/followers/deregister",
    ]
    assert all(request.headers["authorization"] == "Bearer the-credential" for request in seen)
    assert all(json.loads(r.content)["lease_id"] == LEASE for r in seen[:3])
    assert len(json.loads(seen[1].content)["reason"]) == 2000  # the protocol's limit
    assert seen[3].content == b""


def test_a_call_without_a_credential_never_leaves_the_machine():
    client, seen = client_for(lambda request: httpx.Response(204), credential=None)
    with pytest.raises(Refused) as refused:
        client.claim()
    assert (refused.value.status, seen) == (401, [])


def test_a_claim_with_work_is_parsed():
    client, _ = client_for(lambda request: httpx.Response(200, json=CLAIM))
    claimed = client.claim()
    assert (claimed.job_id, claimed.settings.model) == (JOB, "tiny.en")


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"Retry-After": "7"}, NoWork(retry_after=7.0, draining=False)),
        ({}, NoWork(retry_after=10.0, draining=False)),
        ({"Retry-After": "soon"}, NoWork(retry_after=10.0, draining=False)),
        (
            {"Retry-After": "10", "X-SwarmScribe-Directive": "drain"},
            NoWork(retry_after=10.0, draining=True),
        ),
        ({"X-SwarmScribe-Directive": "something-new"}, NoWork(retry_after=10.0, draining=False)),
    ],
)
def test_no_work_says_how_long_to_wait_and_whether_the_follower_is_draining(headers, expected):
    client, _ = client_for(lambda request: httpx.Response(204, headers=headers))
    assert client.claim() == expected


def test_heartbeat_returns_the_directive_and_sends_progress():
    client, seen = client_for(lambda request: httpx.Response(200, json={"directive": "cancel"}))
    assert client.heartbeat(JOB, LEASE, 0.25) == "cancel"
    assert json.loads(seen[0].content) == {"lease_id": LEASE, "progress": 0.25}


def test_links_are_parsed():
    links = {"download_url": CLAIM["download_url"], "upload_urls": CLAIM["upload_urls"]}
    client, _ = client_for(lambda request: httpx.Response(200, json=links))
    assert client.links(JOB, LEASE).upload_urls.txt.method == "PUT"


@pytest.mark.parametrize(
    ("status", "headers", "retry_after"),
    [
        (503, {"Retry-After": "10"}, 10.0),
        (502, {}, None),
        (504, {}, None),
        (500, {}, None),
        (429, {"Retry-After": "42"}, 42.0),
    ],
)
def test_come_back_later_is_transient(status, headers, retry_after):
    body = {"code": "unavailable", "message": "service temporarily unavailable"}
    client, _ = client_for(lambda request: httpx.Response(status, json=body, headers=headers))
    with pytest.raises(Transient) as error:
        client.claim()
    assert (error.value.status, error.value.retry_after) == (status, retry_after)


def test_nobody_answering_is_transient_and_never_names_the_url():
    def unreachable(request):
        raise httpx.ConnectError(f"cannot connect to {request.url}")

    client, _ = client_for(unreachable)
    with pytest.raises(Transient) as error:
        client.heartbeat(JOB, LEASE, None)
    assert error.value.status is None
    assert "leader.test" not in str(error.value)


@pytest.mark.parametrize(
    ("status", "body", "code"),
    [
        (409, {"code": "stale_lease", "message": "not leased to you"}, "stale_lease"),
        (403, {"code": "forbidden", "message": "this follower has been revoked"}, "forbidden"),
        (401, {"code": "unauthorized", "message": "unknown follower credential"}, "unauthorized"),
        (422, {"code": "invalid_request", "message": "lease_id: required"}, "invalid_request"),
        (404, None, "http_404"),
        (302, None, "http_302"),
    ],
)
def test_a_refusal_carries_the_leaders_code(status, body, code):
    def answer(request):
        if body is None:
            return httpx.Response(status, text="<html>proxy</html>", headers={"Location": "/x"})
        return httpx.Response(status, json=body)

    client, seen = client_for(answer)
    with pytest.raises(Refused) as refused:
        client.release(JOB, LEASE)
    assert (refused.value.status, refused.value.code) == (status, code)
    assert len(seen) == 1  # a redirect is not followed


def test_an_answer_that_is_not_the_protocol_is_a_refusal_not_a_crash():
    client, _ = client_for(lambda request: httpx.Response(200, json={"job_id": 5}))
    with pytest.raises(Refused) as refused:
        client.claim()
    assert refused.value.code == "invalid_answer"


def test_a_job_id_is_one_path_segment_whatever_it_holds():
    client, seen = client_for(lambda request: httpx.Response(204))
    client.release("../../v1/followers/deregister", LEASE)
    assert seen[0].url.raw_path == b"/v1/jobs/..%2F..%2Fv1%2Ffollowers%2Fderegister/release"


def test_healthy_asks_healthz_and_never_raises():
    client, seen = client_for(lambda request: httpx.Response(200, json={"status": "ok"}))
    assert client.healthy() is True
    assert (seen[0].method, seen[0].url.path) == ("GET", "/healthz")

    def unreachable(request):
        raise httpx.ConnectError("no")

    assert client_for(unreachable)[0].healthy() is False


# --- retrying ----------------------------------------------------------------------------


class Flaky:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_retrying_waits_with_a_doubling_capped_delay_and_full_jitter():
    waits = []
    call = Flaky(*[Transient(503, None, "x")] * 8, "done")
    result = retrying(call, pause=lambda s: waits.append(s) or False, rng=lambda: 1.0)
    assert result == "done"
    assert waits == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0]
    halved = []
    retrying(
        Flaky(Transient(None, None, "x"), "done"),
        pause=lambda s: halved.append(s) or False,
        rng=lambda: 0.5,
    )
    assert halved == [0.5]


def test_retrying_waits_what_the_leader_asked_for():
    waits = []
    call = Flaky(Transient(503, 10.0, "x"), Transient(503, 30.0, "x"), "done")
    assert retrying(call, pause=lambda s: waits.append(s) or False) == "done"
    assert waits == [10.0, 30.0]


def test_retrying_stops_when_asked_to():
    call = Flaky(Transient(503, None, "x"), "never reached")
    with pytest.raises(Interrupted):
        retrying(call, pause=lambda seconds: True)
    assert call.calls == 1


def test_retrying_can_give_up_and_never_retries_a_refusal():
    call = Flaky(*[Transient(500, None, "x")] * 5)
    with pytest.raises(Transient):
        retrying(
            call,
            pause=lambda seconds: False,
            give_up=lambda error, failures: error.status == 500 and failures >= 3,
        )
    assert call.calls == 3
    refused = Flaky(Refused(409, "stale_lease", "no"), "never reached")
    with pytest.raises(Refused):
        retrying(refused, pause=lambda seconds: False)
    assert refused.calls == 1


# --- credential scope, bad answers, timeouts ---------------------------------------------


def test_the_credential_never_reaches_a_redirect_target_or_another_host():
    def redirect(request):
        return httpx.Response(307, headers={"Location": "https://elsewhere.test/v1/jobs/claim"})

    client, seen = client_for(redirect)
    with pytest.raises(Refused):
        client.claim()
    assert [r.url.host for r in seen] == ["leader.test"]


def test_the_credential_is_only_sent_to_the_leaders_api_paths():
    client, seen = client_for(lambda request: httpx.Response(204))
    client.release(JOB, LEASE)
    client.deregister()
    client.claim()
    assert all(r.url.host == "leader.test" for r in seen)
    assert all(r.url.path.startswith(("/v1/jobs/", "/v1/followers/")) for r in seen)
    seen.clear()
    client.healthy()
    assert "authorization" not in seen[0].headers


def test_the_credential_is_not_in_a_repr_an_error_or_a_log(caplog):
    caplog.set_level("DEBUG")
    client, _ = client_for(lambda request: httpx.Response(503, text="x"))
    assert "the-credential" not in repr(client)
    with pytest.raises(Transient) as transient:
        client.claim()
    assert "the-credential" not in repr(transient.value) + str(transient.value)
    client2, _ = client_for(lambda request: httpx.Response(409, json={"code": "stale_lease"}))
    with pytest.raises(Refused) as refused:
        client2.release(JOB, LEASE)
    assert "the-credential" not in repr(refused.value) + str(refused.value)
    assert "the-credential" not in caplog.text


@pytest.mark.parametrize("response", [
    httpx.Response(200, text="<html>captive portal</html>"),
    httpx.Response(200, content=b""),
    httpx.Response(200, json=["not", "an", "object"]),
    httpx.Response(200, content=b"\xff\xfe"),
])
def test_an_answer_that_is_not_json_is_a_typed_refusal(response):
    client, _ = client_for(lambda request: response)
    for call in (client.claim, lambda: client.heartbeat(JOB, LEASE, 0.5),
                 lambda: client.links(JOB, LEASE)):
        with pytest.raises(Refused) as refused:
            call()
        assert refused.value.code == "invalid_answer"
    with pytest.raises(Refused):
        client.register("t", CAPABILITIES)


def test_an_unknown_directive_is_refused_not_trusted():
    client, _ = client_for(lambda request: httpx.Response(200, json={"directive": "explode"}))
    with pytest.raises(Refused):
        client.heartbeat(JOB, LEASE, None)


def test_every_call_has_a_timeout_and_redirects_are_off():
    client, _ = client_for(lambda request: httpx.Response(204))
    assert client._http.timeout == httpx.Timeout(30.0)
    assert client._http.follow_redirects is False


def test_a_timeout_is_transient():
    def slow(request):
        raise httpx.ReadTimeout("took too long")

    client, _ = client_for(slow)
    with pytest.raises(Transient) as error:
        client.claim()
    assert error.value.status is None


def test_retry_after_is_capped_and_a_date_is_ignored():
    body = {"code": "unavailable", "message": "x"}
    cases = (("999999", 3600.0), ("Wed, 21 Oct 2026 07:28:00 GMT", None), ("-5", None))
    for header, expected in cases:
        client, _ = client_for(
            lambda request, h=header: httpx.Response(503, json=body, headers={"Retry-After": h})
        )
        with pytest.raises(Transient) as error:
            client.claim()
        assert error.value.retry_after == expected


def test_the_retry_loop_wakes_at_once_when_asked_to_stop():
    import threading
    import time

    stop = threading.Event()
    call = Flaky(*[Transient(503, 3600.0, "x")] * 3)
    started = time.monotonic()
    timer = threading.Timer(0.05, stop.set)
    timer.start()
    with pytest.raises(Interrupted):
        retrying(call, pause=stop.wait)
    timer.join(2)
    assert time.monotonic() - started < 5
    assert call.calls == 1
