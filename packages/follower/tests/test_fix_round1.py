"""Fix round 1: environment names, proxies and plain http, the log filter, and small limits."""

import hashlib
import http.server
import io
import json
import logging
import socket
import threading

import httpx
import pytest
from pydantic import ValidationError
from swarmscribe_follower import transfer
from swarmscribe_follower.config import Settings
from swarmscribe_follower.credentials import CredentialFileError, CredentialStore, Stored
from swarmscribe_follower.leader import (
    MIN_WAIT_SECONDS,
    LeaderClient,
    Refused,
    Transient,
    retrying,
)
from swarmscribe_follower.logs import RedactingFilter, configure_logging, redact
from swarmscribe_follower.transfer import LinkRefusedByPolicy, Links
from swarmscribe_protocol import Link

BARE = ("LEADER_URL", "JOIN_TOKEN", "JOIN_TOKEN_FILE", "LEADER_CA_FILE")
PREFIXED = tuple(f"SWARMSCRIBE_{name}" for name in BARE)


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in (*BARE, *PREFIXED):
        monkeypatch.delenv(name, raising=False)


# --- 1. environment names ---


def test_unprefixed_variables_are_ignored(monkeypatch):
    monkeypatch.setenv("LEADER_URL", "https://stray.example")
    monkeypatch.setenv("JOIN_TOKEN", "stray-token")
    monkeypatch.setenv("JOIN_TOKEN_FILE", "/stray")
    monkeypatch.setenv("LEADER_CA_FILE", "/stray-ca")
    with pytest.raises(ValidationError):  # no leader URL: the stray one is not used
        Settings()
    monkeypatch.setenv("SWARMSCRIBE_LEADER_URL", "https://leader.example")
    settings = Settings()
    assert settings.leader_url == "https://leader.example"
    assert settings.join_token is None
    assert settings.join_token_file is None
    assert settings.leader_ca_file is None


def test_the_prefixed_variables_work(monkeypatch, tmp_path):
    tokens = tmp_path / "token"
    tokens.write_text("file-token")
    monkeypatch.setenv("SWARMSCRIBE_LEADER_URL", "https://leader.example")
    monkeypatch.setenv("SWARMSCRIBE_JOIN_TOKEN", "env-token")
    monkeypatch.setenv("SWARMSCRIBE_LEADER_CA_FILE", "/a/ca")
    settings = Settings()
    assert settings.token() == "env-token"
    assert str(settings.leader_ca_file).endswith("ca")
    monkeypatch.setenv("SWARMSCRIBE_JOIN_TOKEN_FILE", str(tokens))
    assert Settings().token() == "file-token"


def test_settings_refuse_a_scratch_that_reaches_the_state_or_models(tmp_path):
    base = {"leader_url": "https://leader.example", "state_dir": tmp_path / "state"}
    for scratch in (tmp_path / "state", tmp_path):
        with pytest.raises(ValidationError, match="scratch"):
            Settings(**base, scratch_dir=scratch)
    with pytest.raises(ValidationError, match="models"):
        Settings(**base, scratch_dir=tmp_path / "s", model_dir=tmp_path / "s" / "m")
    Settings(**base)  # the default scratch sits inside the state folder: fine


# --- 2 and 6. no proxy for plain http; links are https unless the switch is on ---


class Recorder(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _answer(self):
        self.server.seen.append((self.command, self.path, dict(self.headers)))
        # Read the whole request body before answering. A server that answers a PUT or POST
        # and closes with the client's body still unread makes the operating system reset the
        # connection, and the client then sees a ReadError instead of the answer: a race in
        # this stub (about 3 in 100 requests on Windows), never in the code under test.
        remaining = int(self.headers.get("Content-Length") or 0)
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 65536))
            if not chunk:
                break
            remaining -= len(chunk)
        body = b"abc"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    do_GET = do_POST = do_PUT = _answer


def serve():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Recorder)
    server.seen = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


@pytest.fixture
def stub_proxy(monkeypatch):
    """A recording stub named by HTTP_PROXY/http_proxy: anything sent through a proxy lands here."""
    proxy, proxy_thread = serve()
    target, target_thread = serve()
    url = f"http://127.0.0.1:{proxy.server_address[1]}"
    for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, url)
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    yield proxy, target
    for server, thread in ((proxy, proxy_thread), (target, target_thread)):
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()


def test_the_stub_really_is_what_a_trusting_client_would_reach(stub_proxy):
    proxy, target = stub_proxy
    port = target.server_address[1]
    with httpx.Client(trust_env=True) as control:
        control.get(f"http://127.0.0.1:{port}/x")
    assert len(proxy.seen) == 1 and target.seen == []


def test_a_plain_http_leader_is_never_reached_through_a_proxy(stub_proxy):
    proxy, target = stub_proxy
    client = LeaderClient(
        f"http://127.0.0.1:{target.server_address[1]}", credential="the-credential"
    )
    try:
        client.healthy()
        with pytest.raises(Refused):  # the stub answers 200 with no body: not a claim
            client.claim()
    finally:
        client.close()
    assert proxy.seen == []
    assert any(path == "/v1/jobs/claim" for _, path, _ in target.seen)
    auth = [h.get("Authorization") for _, path, h in target.seen if path == "/v1/jobs/claim"]
    assert auth == ["Bearer the-credential"]


def test_a_plain_http_register_sends_the_join_token_only_to_the_leader(stub_proxy):
    from swarmscribe_protocol import Capabilities

    proxy, target = stub_proxy
    client = LeaderClient(f"http://127.0.0.1:{target.server_address[1]}")
    capabilities = Capabilities(device="cpu", models=[], engine_version="1", pool="p")
    with pytest.raises(Refused):
        client.register("secret-join-token", capabilities)
    client.close()
    assert proxy.seen == []
    assert [path for _, path, _ in target.seen] == ["/v1/followers/register"]


def test_a_plain_http_link_is_never_reached_through_a_proxy(stub_proxy, tmp_path):
    proxy, target = stub_proxy
    port = target.server_address[1]
    links = Links(allow_http=True)
    get = Link(url=f"http://127.0.0.1:{port}/in?sig=SECRET", method="GET")
    put = Link(url=f"http://127.0.0.1:{port}/out?sig=SECRET", method="PUT")
    digest = links.download(get, tmp_path / "in", lambda: None)
    assert digest == hashlib.sha256(b"abc").hexdigest()
    (tmp_path / "out").write_bytes(b"x")
    links.upload(put, tmp_path / "out")
    links.close()
    assert proxy.seen == []
    assert len(target.seen) == 2


def test_a_link_must_be_https_unless_the_switch_is_on(tmp_path):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=b"abc")

    plain = Link(url="http://storage.test/in?sig=SECRET", method="GET")
    secure = Link(url="https://storage.test/in?sig=SECRET", method="GET")
    closed = Links(transport=httpx.MockTransport(handler))
    with pytest.raises(LinkRefusedByPolicy) as refused:
        closed.download(plain, tmp_path / "a", lambda: None)
    assert "SECRET" not in str(refused.value) and seen == []
    closed.download(secure, tmp_path / "a", lambda: None)
    opened = Links(transport=httpx.MockTransport(handler), allow_http=True)
    opened.download(plain, tmp_path / "b", lambda: None)
    assert len(seen) == 2
    with pytest.raises(LinkRefusedByPolicy):  # the switch does not open other schemes
        ftp = Link(url="ftp://storage.test/x", method="GET")
        opened.download(ftp, tmp_path / "c", lambda: None)


# --- 7. minors ---


def test_a_wait_below_the_floor_uses_the_floor():
    client = LeaderClient(
        "https://leader.test",
        credential="c",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(503, headers={"Retry-After": "0"})
        ),
    )
    with pytest.raises(Transient) as error:
        client.claim()
    assert error.value.retry_after == MIN_WAIT_SECONDS
    waits = []
    outcomes = [Transient(503, 0.0, "x"), Transient(503, None, "x"), "done"]

    def call():
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    assert retrying(call, pause=lambda s: waits.append(s) or False, rng=lambda: 0.0) == "done"
    assert waits == [MIN_WAIT_SECONDS, MIN_WAIT_SECONDS]


GOOD = {
    "leader_url": "https://leader.test",
    "follower_id": "f1",
    "credential": "abc123",
    "device": "cpu",
    "heartbeat_interval": 30,
    "lease_seconds": 120,
}


@pytest.mark.parametrize(
    "change",
    [
        {"heartbeat_interval": 0},
        {"heartbeat_interval": -1},
        {"lease_seconds": 0},
        {"lease_seconds": -5},
        {"credential": ""},
        {"credential": "a\r\nb"},
        {"credential": "café"},
        {"credential": "has space"},
    ],
)
def test_a_credential_file_with_impossible_values_is_refused_as_corrupt(tmp_path, change):
    path = tmp_path / "credential.json"
    path.write_text(json.dumps({**GOOD, **change}))
    path.chmod(0o600)
    with pytest.raises(CredentialFileError, match="delete it and join again"):
        CredentialStore(tmp_path / "credential.json").load()


def test_a_deeply_nested_credential_file_is_refused_not_a_crash(tmp_path):
    (tmp_path / "credential.json").write_text("[" * 200000)
    (tmp_path / "credential.json").chmod(0o600)
    with pytest.raises(CredentialFileError):
        CredentialStore(tmp_path / "credential.json").load()


def test_a_good_credential_file_still_loads(tmp_path):
    store = CredentialStore(tmp_path / "state" / "credential.json")
    store.save(Stored(**GOOD))
    assert store.load() == Stored(**GOOD)


def test_the_stop_check_runs_at_least_every_64_kib(tmp_path):
    assert transfer.CHUNK_BYTES <= 64 * 1024
    links = Links(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 1_000_000))
    )
    checks = []
    links.download(
        Link(url="https://storage.test/in", method="GET"), tmp_path / "in", lambda: checks.append(1)
    )
    assert len(checks) >= 1_000_000 // (64 * 1024)


# --- the log filter ---


@pytest.mark.parametrize(
    ("text", "gone"),
    [
        ("fetch https://storage.test/v1/files/TOKEN123 failed", "TOKEN123"),
        ("fetch https://storage.test/in?sig=SECRET&x=1 failed", "SECRET"),
        ("fetch https://storage.test?sig=SECRET failed", "SECRET"),
        ("sent Authorization: Bearer abc.DEF-123_xyz== to leader", "abc.DEF-123_xyz"),
        ("BEARER topsecret", "topsecret"),
    ],
)
def test_redact_removes_link_paths_queries_and_bearer_values(text, gone):
    assert gone not in redact(text)


def test_redact_keeps_the_host_and_ordinary_text():
    assert redact("leader https://leader.example is up") == "leader https://leader.example is up"


def test_the_configured_log_handler_redacts_and_drops_http_library_chatter():
    stream = io.StringIO()
    configure_logging("json", stream=stream)
    try:
        logging.getLogger("swarmscribe_follower.test").warning(
            "download failed: %s", httpx.ConnectError("no route to https://storage.test/o?sig=SECRET")
        )
        logging.getLogger("httpx").setLevel(logging.INFO)  # something lowers it again
        logging.getLogger("httpx").info('HTTP Request: GET https://storage.test/o?sig=SECRET "200"')
        logging.getLogger("swarmscribe_follower.test").error(
            "header %s", "Authorization: Bearer abcdef"
        )
    finally:
        logging.getLogger().handlers.clear()
        logging.getLogger("httpx").setLevel(logging.WARNING)
    output = stream.getvalue()
    assert "SECRET" not in output and "abcdef" not in output
    assert "download failed" in output
    assert output.count("\n") == 2  # the httpx line was dropped


def test_the_filter_is_a_logging_filter_on_the_handler():
    assert isinstance(RedactingFilter(), logging.Filter)
    configure_logging("text", stream=io.StringIO())
    try:
        assert any(isinstance(f, RedactingFilter) for f in logging.getLogger().handlers[0].filters)
    finally:
        logging.getLogger().handlers.clear()


def test_a_log_line_with_a_non_json_extra_and_a_newline_is_still_one_line():
    stream = io.StringIO()
    configure_logging("json", stream=stream)
    try:
        import uuid

        logging.getLogger("x").warning("a\nb", extra={"job_id": uuid.uuid4()})
    finally:
        logging.getLogger().handlers.clear()
    assert stream.getvalue().count("\n") == 1
    json.loads(stream.getvalue())


def _unused_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
