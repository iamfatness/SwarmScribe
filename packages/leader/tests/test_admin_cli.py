import argparse
import io
import json
from urllib.parse import parse_qsl

import httpx
import pytest
from sqlalchemy import select
from swarmscribe_leader.admin_cli.client import REFRESH_MARGIN_SECONDS, CliError, LeaderClient
from swarmscribe_leader.admin_cli.credentials import CredentialStore, SignIn
from swarmscribe_leader.admin_cli.main import amain, parse_duration
from swarmscribe_leader.auth.secrets import hash_secret
from swarmscribe_leader.db.models import (
    ConsoleCredential,
    Follower,
    Job,
    JoinToken,
    StorageLocation,
)

LEADER = "http://localhost"
ENTRA_SCOPE = "openid profile email offline_access"


class Routed(httpx.AsyncBaseTransport):
    """Leader requests go to the app in-process; everything else to the fake provider."""

    def __init__(self, app, provider):
        self.leader = httpx.ASGITransport(app=app)
        self.provider = httpx.MockTransport(provider)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "localhost":
            return await self.leader.handle_async_request(request)
        return await self.provider.handle_async_request(request)


class FakeEntra:
    """Entra ID's device-code and token endpoints, signing the person in as an admin."""

    def __init__(self, idp):
        self.idp = idp
        self.polls = 0
        self.refreshes = 0

    def admin_token(self) -> str:
        return self.idp.entra(
            groups=[self.idp.ENTRA_GROUPS["admin"]], sub="entra-admin", email="admin@example.org"
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/devicecode"):
            return httpx.Response(
                200,
                json={
                    "device_code": "device-code-1",
                    "user_code": "WDJB-MJHT",
                    "verification_uri": "https://microsoft.com/devicelogin",
                    "expires_in": 900,
                    "interval": 0,
                },
            )
        form = dict(parse_qsl(request.content.decode()))
        if form["grant_type"] == "refresh_token":
            self.refreshes += 1
            return httpx.Response(
                200, json={"id_token": self.admin_token(), "refresh_token": "refresh-token-2"}
            )
        self.polls += 1
        if self.polls == 1:
            return httpx.Response(400, json={"error": "authorization_pending"})
        return httpx.Response(
            200, json={"id_token": self.admin_token(), "refresh_token": "refresh-token-1"}
        )


@pytest.fixture
def store(tmp_path):
    return CredentialStore(tmp_path / "config" / "credentials.json")


@pytest.fixture
def entra(idp):
    return FakeEntra(idp)


@pytest.fixture
def cli(admin_app, store, entra):
    async def run(*argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        code = await amain(
            ["--leader", LEADER, *argv],
            transport=Routed(admin_app, entra),
            store=store,
            out=out,
            err=err,
        )
        return code, out.getvalue(), err.getvalue()

    return run


def sign_in_as(store, idp, role, *, lifetime=3600) -> str:
    token = idp.entra(
        groups=[idp.ENTRA_GROUPS[role]],
        sub=f"entra-{role}",
        email=f"{role}@example.org",
        lifetime=lifetime,
    )
    store.save(
        SignIn(
            leader=LEADER,
            provider="entra",
            client_id=idp.ENTRA_CLIENT,
            token_endpoint=(
                f"https://login.microsoftonline.com/{idp.ENTRA_TENANT}/oauth2/v2.0/token"
            ),
            scope=ENTRA_SCOPE,
            id_token=token,
            refresh_token="refresh-token-1",
        )
    )
    return token


async def test_login_with_entra_saves_the_sign_in_and_shows_the_role(cli, store):
    code, out, err = await cli("login", "--provider", "entra")
    assert code == 0, err
    assert "open https://microsoft.com/devicelogin and enter the code WDJB-MJHT" in out
    assert "signed in as admin@example.org (admin)" in out
    saved = store.load(LEADER)
    assert (saved.provider, saved.refresh_token) == ("entra", "refresh-token-1")
    assert saved.id_token not in out
    assert store.default_leader() == LEADER


async def test_login_asks_which_provider_when_the_leader_accepts_both(cli):
    code, _out, err = await cli("login")
    assert code == 1
    assert "choose one with --provider" in err


async def test_a_command_before_login_says_to_sign_in(cli):
    code, out, err = await cli("status")
    assert (code, out) == (1, "")
    assert "run `swarmscribe-admin login`" in err


async def test_status(cli, store, idp, factory):
    sign_in_as(store, idp, "viewer")
    await factory.job()
    code, out, _err = await cli("status")
    assert code == 0
    assert "queued 1" in out
    assert "locations:" in out


async def test_json_output_is_the_leaders_answer(cli, store, idp):
    sign_in_as(store, idp, "viewer")
    code, out, _err = await cli("--json", "whoami")
    assert code == 0
    assert json.loads(out)["role"] == "viewer"


async def test_locations_add_list_ingest_and_disable(cli, store, idp, sessionmaker, tmp_path):
    sign_in_as(store, idp, "admin")
    code, _out, err = await cli(
        "locations",
        "add",
        "archive-1",
        "--root",
        str(tmp_path),
        "--input-prefix",
        "incoming/",
        "--scan-interval",
        "30m",
    )
    assert code == 0, err
    assert "archive-1" in (await cli("locations", "list"))[1]
    assert (await cli("ingest", "archive-1"))[1].startswith("scan requested for archive-1")
    assert (await cli("locations", "disable", "archive-1"))[0] == 0
    async with sessionmaker() as session:
        location = (await session.scalars(select(StorageLocation))).one()
    assert (location.input_prefix, location.scan_interval_s, location.enabled) == (
        "incoming/",
        1800,
        False,
    )
    assert location.scan_requested_at is not None


async def test_jobs_list_retry_priority_and_cancel(cli, store, idp, factory, sessionmaker):
    sign_in_as(store, idp, "operator")
    failed = await factory.job(state="failed", failure_reason="engine_error: x")
    queued = await factory.job()
    _code, out, _err = await cli("jobs", "list", "--state", "failed")
    assert str(failed.id) in out
    assert str(queued.id) not in out
    code, out, _err = await cli("jobs", "retry", str(failed.id))
    assert code == 0
    assert "state: queued" in out
    assert (await cli("jobs", "priority", str(queued.id), "7"))[0] == 0
    assert (await cli("jobs", "cancel", str(queued.id)))[0] == 0
    async with sessionmaker() as session:
        stored = await session.get(Job, queued.id)
    assert (stored.priority, stored.state) == (7, "cancelled")


async def test_followers_list_drain_and_revoke(cli, store, idp, factory, sessionmaker):
    sign_in_as(store, idp, "admin")
    follower, _ = await factory.follower()
    assert str(follower.id) in (await cli("followers", "list"))[1]
    assert (await cli("followers", "drain", str(follower.id)))[0] == 0
    _code, out, _err = await cli("followers", "revoke", str(follower.id))
    assert "released: 0" in out
    async with sessionmaker() as session:
        assert (await session.get(Follower, follower.id)).state == "revoked"


async def test_tokens_create_shows_the_token_once_and_list_never_does(
    cli, store, idp, sessionmaker
):
    sign_in_as(store, idp, "admin")
    code, out, _err = await cli(
        "tokens", "create", "--pool", "gpu", "--expires", "12h", "--max-uses", "3"
    )
    assert code == 0
    token = out.splitlines()[0].rsplit(" ", 1)[1]
    async with sessionmaker() as session:
        row = (await session.scalars(select(JoinToken))).one()
    assert (row.pool, row.max_uses, row.token_hash) == ("gpu", 3, hash_secret(token))
    _code, listed, _err = await cli("tokens", "list")
    assert str(row.id) in listed
    assert token not in listed
    assert (await cli("tokens", "revoke", str(row.id)))[0] == 0


async def test_consent_report(cli, store, idp, factory):
    sign_in_as(store, idp, "viewer")
    here = await factory.location(name="here")
    await factory.recording(here, key="talks/a.mp3", consent="withdrawn")
    code, out, _err = await cli("consent", "report")
    assert code == 0
    assert "here" in out


async def test_a_refusal_is_reported_without_any_token(cli, store, idp):
    token = sign_in_as(store, idp, "viewer")
    code, out, err = await cli("tokens", "create")
    assert code == 1
    assert "this needs the admin role" in err
    assert "(403 forbidden)" in err
    assert token not in out + err
    assert "refresh-token-1" not in out + err


async def test_an_id_token_about_to_expire_is_refreshed_silently(cli, store, idp, entra):
    old = sign_in_as(store, idp, "admin", lifetime=60)
    code, out, err = await cli("whoami")
    assert code == 0, err
    assert entra.refreshes == 1
    saved = store.load(LEADER)
    assert saved.id_token != old
    assert saved.refresh_token == "refresh-token-2"
    assert "role: admin" in out


async def test_logout_forgets_the_sign_in(cli, store, idp):
    sign_in_as(store, idp, "viewer")
    assert (await cli("logout"))[0] == 0
    assert store.load(LEADER) is None


def test_durations():
    assert [parse_duration(text) for text in ("90s", "30m", "12h", "7d")] == [
        90,
        1800,
        43200,
        604800,
    ]
    with pytest.raises(argparse.ArgumentTypeError):
        parse_duration("7 days")


# ---- controller requirements -------------------------------------------------------------


class Recorder(httpx.AsyncBaseTransport):
    """Records every request that reaches the network and answers with a scripted reply."""

    def __init__(self, handler=None):
        self.requests: list[httpx.Request] = []
        self.handler = handler or (lambda request: httpx.Response(200, json={}))

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)


@pytest.mark.parametrize(
    "leader",
    [
        "http://leader.example.org",
        "http://192.168.1.5:8080",
        "ftp://localhost",
        "leader.example.org",
        "https://user:pw@leader.example.org",
        "https://leader.example.org/some/path",
        "https://leader.example.org?x=1",
        "http://localhost.evil.test",
        "http://127.0.0.1.evil.test",
        "https://xn--a.com",  # not valid IDNA
    ],
)
@pytest.mark.parametrize("command", [["login"], ["status"], ["logout"]])
async def test_an_unsafe_leader_url_is_refused_before_any_request(store, leader, command):
    transport = Recorder()
    out, err = io.StringIO(), io.StringIO()
    code = await amain(
        ["--leader", leader, *command], transport=transport, store=store, out=out, err=err
    )
    assert code == 2
    assert transport.requests == []
    assert "leader" in err.getvalue()
    assert err.getvalue().count("\n") == 1  # one line, no traceback


@pytest.mark.parametrize(
    "given, normalised",
    [
        ("http://localhost/", "http://localhost"),
        ("HTTP://LOCALHOST:8080", "http://localhost:8080"),
        ("http://127.0.0.1:9000/", "http://127.0.0.1:9000"),
        ("http://[::1]:9000", "http://[::1]:9000"),
        ("https://Leader.Example.org:443/", "https://leader.example.org"),
    ],
)
async def test_the_leader_url_is_normalised_once(store, idp, given, normalised):
    store.save(
        SignIn(
            leader=normalised,
            provider="entra",
            client_id="c",
            token_endpoint="https://login.microsoftonline.com/t/oauth2/v2.0/token",
            scope=ENTRA_SCOPE,
            id_token=idp.entra(groups=[idp.ENTRA_GROUPS["viewer"]]),
        )
    )
    code = await amain(
        ["--leader", given, "logout"],
        transport=Recorder(),
        store=store,
        out=io.StringIO(),
        err=io.StringIO(),
    )
    assert code == 0
    assert store.load(normalised) is None


async def test_a_sign_in_is_only_used_for_the_leader_it_was_saved_for(store, idp):
    token = sign_in_as(store, idp, "admin")
    transport = Recorder()
    for other in ("http://127.0.0.1", "http://localhost:8001"):
        out, err = io.StringIO(), io.StringIO()
        code = await amain(
            ["--leader", other, "whoami"], transport=transport, store=store, out=out, err=err
        )
        assert code == 1
        assert "run `swarmscribe-admin login`" in err.getvalue()
    assert transport.requests == []
    assert token not in repr(transport.requests)


async def test_a_corrupt_credentials_file_is_an_error_not_a_traceback(cli, store):
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("not json", encoding="utf-8")
    code, out, err = await cli("status")
    assert (code, out) == (1, "")
    assert "corrupted" in err
    assert "Traceback" not in err


async def test_an_unreachable_leader_is_an_error_not_a_traceback(store, idp):
    sign_in_as(store, idp, "admin")

    def refuse(request):
        raise httpx.ConnectError("boom", request=request)

    out, err = io.StringIO(), io.StringIO()
    code = await amain(
        ["--leader", LEADER, "status"],
        transport=Recorder(refuse),
        store=store,
        out=out,
        err=err,
    )
    assert code == 1
    assert "cannot reach http://localhost" in err.getvalue()


@pytest.mark.parametrize(
    "status, code",
    [(401, "unauthorized"), (403, "forbidden"), (409, "conflict"), (503, "unavailable")],
)
async def test_expected_refusals_are_one_line_errors(store, idp, status, code):
    sign_in_as(store, idp, "admin")
    transport = Recorder(
        lambda request: httpx.Response(status, json={"message": "no thanks", "code": code})
    )
    out, err = io.StringIO(), io.StringIO()
    exit_code = await amain(
        ["--leader", LEADER, "status"], transport=transport, store=store, out=out, err=err
    )
    assert exit_code == 1
    assert err.getvalue() == f"error: no thanks ({status} {code})\n"
    assert out.getvalue() == ""


async def test_a_token_that_still_has_time_is_not_refreshed(cli, store, idp, entra):
    sign_in_as(store, idp, "admin", lifetime=REFRESH_MARGIN_SECONDS + 300)
    code, _out, err = await cli("whoami")
    assert code == 0, err
    assert entra.refreshes == 0


async def test_a_token_with_under_two_minutes_left_is_refreshed(cli, store, idp, entra):
    sign_in_as(store, idp, "admin", lifetime=REFRESH_MARGIN_SECONDS - 20)
    assert (await cli("whoami"))[0] == 0
    assert entra.refreshes == 1


class ExpiringLeader:
    """Answers 401 token_expired `expired` times, then 200; counts refreshes."""

    def __init__(self, idp, expired):
        self.idp = idp
        self.expired = expired
        self.leader_calls = 0
        self.refreshes = 0
        self.tokens_seen: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host != "localhost":
            self.refreshes += 1
            return httpx.Response(
                200,
                json={
                    "id_token": self.idp.entra(
                        groups=[self.idp.ENTRA_GROUPS["admin"]], lifetime=3000 + self.refreshes
                    ),
                    "refresh_token": "refresh-token-2",
                },
            )
        self.leader_calls += 1
        self.tokens_seen.append(request.headers["authorization"])
        if self.leader_calls <= self.expired:
            return httpx.Response(401, json={"message": "expired", "code": "token_expired"})
        return httpx.Response(200, json={"ok": True})


async def run_client(store, handler):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        return await LeaderClient(LEADER, store, http=http).request("GET", "/v1/admin/whoami")


async def test_one_token_expired_answer_refreshes_and_retries_once(store, idp):
    sign_in_as(store, idp, "admin")
    leader = ExpiringLeader(idp, expired=1)
    assert await run_client(store, leader) == {"ok": True}
    assert (leader.leader_calls, leader.refreshes) == (2, 1)
    assert leader.tokens_seen[0] != leader.tokens_seen[1]


async def test_a_second_token_expired_answer_is_an_error_never_a_loop(store, idp):
    sign_in_as(store, idp, "admin")
    leader = ExpiringLeader(idp, expired=99)
    with pytest.raises(CliError, match="401 token_expired"):
        await run_client(store, leader)
    assert (leader.leader_calls, leader.refreshes) == (2, 1)


async def test_a_redirect_is_an_error_and_is_not_followed(store, idp):
    sign_in_as(store, idp, "admin")
    seen = []

    def handler(request):
        seen.append(request.url.host)
        return httpx.Response(302, headers={"location": "https://evil.test/x"})

    with pytest.raises(CliError):
        await run_client(store, handler)
    assert seen == ["localhost"]


async def test_the_token_is_printed_once_to_stdout_and_nowhere_else(cli, store, idp, capsys):
    sign_in_as(store, idp, "admin")
    code, out, err = await cli("tokens", "create")
    assert code == 0
    token = out.splitlines()[0].rsplit(" ", 1)[1]
    assert out.count(token) == 1
    assert token not in err
    captured = capsys.readouterr()
    assert token not in captured.out + captured.err


async def test_login_never_prints_tokens_or_secrets(cli, store):
    code, out, err = await cli("login", "--provider", "entra")
    assert code == 0, err
    saved = store.load(LEADER)
    for secret in (saved.id_token, saved.refresh_token):
        assert secret not in out + err


# ---- final review: leader answers are untrusted text and shape ---------------------------

CONTROL = "\x1b[2J\x1b]0;owned\x07\r\x08\x00"


async def run_cli(store, handler, *argv) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = await amain(
        ["--leader", LEADER, *argv], transport=Recorder(handler), store=store, out=out, err=err
    )
    return code, out.getvalue(), err.getvalue()


def has_control_characters(text: str) -> bool:
    return any(not (c.isprintable() or c == "\n") for c in text)


@pytest.mark.parametrize(
    "argv, answer",
    [
        (
            ["jobs", "list"],
            [{"id": "j1", "state": "failed", "key": f"a{CONTROL}b", "failure_reason": CONTROL}],
        ),
        (["whoami"], {"email": f"x{CONTROL}@example.org", "role": "viewer"}),
        (["ingest", "here"], {"name": f"here{CONTROL}", "requested_at": None}),
        (
            ["tokens", "create"],
            {"id": "t1", "token": f"tok{CONTROL}", "pool": CONTROL, "max_uses": 1},
        ),
        (
            ["status"],
            {
                "jobs": {"queued": CONTROL},
                "completed_last_hour": CONTROL,
                "failed_attempts_last_day": 0,
                "followers": {},
                "pools": [],
                "locations": [{"name": "x", "last_scan_error": CONTROL}],
            },
        ),
        (
            ["consent", "report"],
            {
                "locations": [],
                "flagged": [{"job_id": "j", "key": CONTROL, "outputs": [CONTROL]}],
                "truncated": False,
            },
        ),
    ],
    ids=["table", "fields", "scan", "token", "status", "consent-list-cell"],
)
async def test_leader_values_never_reach_the_terminal_with_control_characters(
    store, idp, argv, answer
):
    sign_in_as(store, idp, "admin")
    code, out, err = await run_cli(store, lambda request: httpx.Response(200, json=answer), *argv)
    assert code == 0, err
    assert out
    assert not has_control_characters(out), repr(out)


@pytest.mark.parametrize(
    "argv, answer",
    [
        (["status"], {}),
        (["status"], {"jobs": [], "completed_last_hour": 1}),
        (["whoami"], ["not", "fields"]),
        (["jobs", "list"], {"not": "rows"}),
        (["jobs", "list"], [1, 2]),
        (["consent", "report"], {"locations": []}),
        (["tokens", "create"], {"id": "t1"}),
        (["ingest", "here"], None),
    ],
)
async def test_an_answer_of_the_wrong_shape_is_a_one_line_error(store, idp, argv, answer):
    sign_in_as(store, idp, "admin")
    code, out, err = await run_cli(store, lambda request: httpx.Response(200, json=answer), *argv)
    assert (code, out) == (1, "")
    assert err.startswith("error: ") and err.count("\n") == 1, err


def login_leader(idp, *, client_secret=None, whoami=None):
    """A leader whose login-config offers Entra, plus Entra itself, answering everything."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/admin/login-config":
            base = f"https://login.microsoftonline.com/{idp.ENTRA_TENANT}/oauth2/v2.0"
            return httpx.Response(
                200,
                json={
                    "providers": [
                        {
                            "name": "entra",
                            "client_id": idp.ENTRA_CLIENT,
                            "device_authorization_endpoint": f"{base}/devicecode",
                            "token_endpoint": f"{base}/token",
                            "scope": ENTRA_SCOPE,
                            "client_secret": client_secret,
                        }
                    ]
                },
            )
        if path.endswith("/devicecode"):
            return httpx.Response(
                200,
                json={
                    "device_code": "d",
                    "user_code": "CODE-1",
                    "verification_uri": "https://microsoft.com/devicelogin",
                    "expires_in": 900,
                    "interval": 0,
                },
            )
        if path.endswith("/token"):
            token = idp.entra(groups=[idp.ENTRA_GROUPS["admin"]])
            return httpx.Response(200, json={"id_token": token, "refresh_token": "r"})
        return httpx.Response(200, json=whoami)

    return handler


@pytest.mark.parametrize("secret", [5, ["s"], {"s": 1}, True])
async def test_login_refuses_a_client_secret_that_is_not_text(store, idp, secret):
    transport = Recorder(login_leader(idp, client_secret=secret))
    out, err = io.StringIO(), io.StringIO()
    code = await amain(
        ["--leader", LEADER, "login", "--provider", "entra"],
        transport=transport,
        store=store,
        out=out,
        err=err,
    )
    assert code == 1
    assert "not in the expected form" in err.getvalue()
    assert [r.url.path for r in transport.requests] == ["/v1/admin/login-config"]


@pytest.mark.parametrize("whoami", [{}, {"email": "a@example.org"}, ["x"], None])
async def test_login_reports_an_odd_whoami_answer_as_one_line(store, idp, whoami):
    code, out, err = await run_cli(
        store, login_leader(idp, whoami=whoami), "login", "--provider", "entra"
    )
    assert code == 1
    assert err.startswith("error: ") and err.count("\n") == 1, err
    assert "Traceback" not in out + err


async def test_login_shows_the_signed_in_person_without_control_characters(store, idp):
    whoami = {"email": f"x{CONTROL}@example.org", "subject": "s", "role": f"admin{CONTROL}"}
    code, out, err = await run_cli(
        store, login_leader(idp, whoami=whoami), "login", "--provider", "entra"
    )
    assert code == 0, err
    assert "signed in as" in out
    assert not has_control_characters(out), repr(out)


async def test_locations_add_with_channels_and_labels(cli, store, idp, sessionmaker, tmp_path):
    sign_in_as(store, idp, "admin")
    code, _out, err = await cli(
        "locations",
        "add",
        "calls-1",
        "--root",
        str(tmp_path),
        "--channels",
        "stereo-split",
        "--labels",
        "Agent, Customer",
    )
    assert code == 0, err
    async with sessionmaker() as session:
        location = (await session.scalars(select(StorageLocation))).one()
    assert (location.channel_mode, location.channel_labels) == (
        "stereo_split",
        ["Agent", "Customer"],
    )
    listed = (await cli("locations", "list"))[1]
    assert "stereo_split" in listed
    assert "Agent, Customer" in listed


async def test_locations_add_is_mono_by_default(cli, store, idp, sessionmaker, tmp_path):
    sign_in_as(store, idp, "admin")
    code, _out, err = await cli("locations", "add", "archive-1", "--root", str(tmp_path))
    assert code == 0, err
    async with sessionmaker() as session:
        location = (await session.scalars(select(StorageLocation))).one()
    assert (location.channel_mode, location.channel_labels) == ("mono", ["Left", "Right"])


async def test_locations_add_labels_without_a_split_mode_is_refused(
    cli, store, idp, sessionmaker, tmp_path
):
    sign_in_as(store, idp, "admin")
    code, out, err = await cli(
        "locations", "add", "calls-1", "--root", str(tmp_path), "--labels", "Agent,Customer"
    )
    assert (code, out) == (1, "")
    assert "--labels needs --channels stereo-split or auto" in err
    async with sessionmaker() as session:
        assert (await session.scalars(select(StorageLocation))).all() == []


@pytest.mark.parametrize("labels", ["Agent", "Agent,Customer,Supervisor"])
async def test_locations_add_labels_must_be_two_names(cli, store, idp, tmp_path, labels):
    sign_in_as(store, idp, "admin")
    with pytest.raises(SystemExit) as excinfo:
        await cli(
            "locations",
            "add",
            "calls-1",
            "--root",
            str(tmp_path),
            "--channels",
            "auto",
            "--labels",
            labels,
        )
    assert excinfo.value.code == 2


# --- console credentials ----------------------------------------------------------------


async def test_console_create_shows_the_credential_once_and_list_never_does(
    cli, store, idp, sessionmaker
):
    sign_in_as(store, idp, "admin")
    code, out, err = await cli("console", "create", "--name", "fleet", "--max-role", "operator")
    assert code == 0, err
    credential = out.splitlines()[0].rsplit(" ", 1)[1]
    assert out.count(credential) == 1
    assert credential not in err
    async with sessionmaker() as session:
        row = (await session.scalars(select(ConsoleCredential))).one()
    assert (row.name, row.max_role, row.credential_hash) == (
        "fleet",
        "operator",
        hash_secret(credential),
    )
    code, listed, _err = await cli("console", "list")
    assert code == 0
    assert "fleet" in listed and "operator" in listed
    assert credential not in listed
    code, revoked, _err = await cli("console", "revoke", "fleet")
    assert code == 0
    assert "revoked: yes" in revoked
    async with sessionmaker() as session:
        assert (await session.get(ConsoleCredential, row.id)).revoked_at is not None


async def test_console_create_of_a_taken_name_is_a_one_line_error(cli, store, idp):
    sign_in_as(store, idp, "admin")
    assert (await cli("console", "create", "--name", "fleet", "--max-role", "viewer"))[0] == 0
    code, out, err = await cli("console", "create", "--name", "FLEET", "--max-role", "admin")
    assert (code, out) == (1, "")
    assert "already exists" in err and "(409 exists)" in err
    assert err.count("\n") == 1


async def test_console_commands_need_the_admin_role(cli, store, idp):
    token = sign_in_as(store, idp, "operator")
    for argv in (
        ("console", "list"),
        ("console", "create", "--name", "fleet", "--max-role", "viewer"),
        ("console", "revoke", "fleet"),
    ):
        code, out, err = await cli(*argv)
        assert (code, out) == (1, ""), argv
        assert "this needs the admin role" in err
        assert token not in err


async def test_console_revoke_of_an_unknown_name_is_an_error(cli, store, idp):
    sign_in_as(store, idp, "admin")
    code, _out, err = await cli("console", "revoke", "nowhere")
    assert code == 1
    assert "(404 not_found)" in err


@pytest.mark.parametrize(
    "argv",
    [
        ["console", "create", "--name", "fleet"],
        ["console", "create", "--max-role", "viewer"],
        ["console", "create", "--name", "fleet", "--max-role", "root"],
        ["console", "create", "--name", "fleet", "--max-role", "Admin"],
        ["console", "revoke"],
        ["console"],
    ],
)
async def test_console_usage_errors_exit_2(cli, store, idp, argv):
    sign_in_as(store, idp, "admin")
    with pytest.raises(SystemExit) as excinfo:
        await cli(*argv)
    assert excinfo.value.code == 2


async def test_a_console_credential_answer_never_reaches_the_terminal_with_control_characters(
    store, idp
):
    sign_in_as(store, idp, "admin")
    answer = {"id": "c1", "name": f"fleet{CONTROL}", "max_role": CONTROL, "credential": CONTROL}
    code, out, err = await run_cli(
        store,
        lambda request: httpx.Response(201, json=answer),
        "console",
        "create",
        "--name",
        "fleet",
        "--max-role",
        "viewer",
    )
    assert code == 0, err
    assert not has_control_characters(out), repr(out)


@pytest.mark.parametrize(
    "argv, answer",
    [
        (["console", "create", "--name", "fleet", "--max-role", "viewer"], {"id": "c1"}),
        (["console", "list"], {"not": "rows"}),
    ],
)
async def test_a_console_answer_of_the_wrong_shape_is_a_one_line_error(store, idp, argv, answer):
    sign_in_as(store, idp, "admin")
    code, out, err = await run_cli(store, lambda request: httpx.Response(200, json=answer), *argv)
    assert (code, out) == (1, "")
    assert err.startswith("error: ") and err.count("\n") == 1, err
