"""swarmscribe-admin: sign in with Entra ID or Google and run a SwarmScribe leader."""

import argparse
import asyncio
import io
import json
import os
import re
import sys
from collections.abc import Callable, Sequence
from typing import Any, TextIO
from urllib.parse import quote

import httpx

from .client import CliError, LeaderClient, UsageError, normalise_leader, printable
from .credentials import CredentialsFileError, CredentialStore, SignIn
from .device_flow import ProviderConfig, SignInError, device_sign_in

LEADER_ENV = "SWARMSCRIBE_LEADER_URL"
JOB_STATES = ("queued", "leased", "completed", "failed", "cancelled")
FOLLOWER_STATES = ("active", "draining", "revoked", "gone")
_DURATION = re.compile(r"(\d+)([smhd])")
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}

Renderer = Callable[[Any, TextIO], None]


def parse_duration(text: str) -> int:
    match = _DURATION.fullmatch(text.strip())
    if match is None:
        raise argparse.ArgumentTypeError("use a number and a unit: 90s, 30m, 12h or 7d")
    return int(match.group(1)) * _UNITS[match.group(2)]


def parse_labels(text: str) -> tuple[str, ...]:
    names = tuple(name.strip() for name in text.split(","))
    if len(names) != 2:
        raise argparse.ArgumentTypeError(
            'give exactly two names separated by a comma, e.g. "Agent,Customer"'
        )
    return names


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="swarmscribe-admin", description="Administer a SwarmScribe leader."
    )
    parser.add_argument(
        "--leader", help=f"leader URL (default: ${LEADER_ENV}, or the last one signed in to)"
    )
    parser.add_argument("--json", action="store_true", help="print the leader's JSON answer")
    commands = parser.add_subparsers(dest="command", required=True)

    login = commands.add_parser("login", help="sign in with your organisation account")
    login.add_argument("--provider", choices=("entra", "google"))
    commands.add_parser("logout", help="forget the sign-in for this leader")
    commands.add_parser("whoami", help="who you are signed in as, and your role")
    commands.add_parser("status", help="queue, followers, locations and scan errors")

    locations = commands.add_parser("locations", help="storage locations").add_subparsers(
        dest="action", required=True
    )
    add = locations.add_parser("add", help="add a folder every leader replica can see")
    add.add_argument("name")
    add.add_argument("--root", required=True, help="absolute path of the folder")
    add.add_argument("--input-prefix", default="", help="e.g. incoming/ (default: all)")
    add.add_argument("--output-prefix", default="transcripts/")
    add.add_argument("--pool", default="default")
    add.add_argument("--device", choices=("any", "cuda", "cpu"), default="any")
    add.add_argument("--scan-interval", type=parse_duration, default=900, help="e.g. 15m")
    add.add_argument(
        "--channels",
        choices=("mono", "stereo-split", "auto"),
        default="mono",
        help="mono mixes channels (default); stereo-split transcribes left and right separately;"
        " auto splits two-channel files",
    )
    add.add_argument(
        "--labels",
        type=parse_labels,
        help='names of the left and right channels, e.g. "Agent,Customer" (default: Left,Right)',
    )
    locations.add_parser("list")
    for action in ("disable", "enable"):
        locations.add_parser(action).add_argument("name")

    ingest = commands.add_parser("ingest", help="scan a location now")
    ingest.add_argument("name")

    jobs = commands.add_parser("jobs", help="transcription jobs").add_subparsers(
        dest="action", required=True
    )
    job_list = jobs.add_parser("list")
    job_list.add_argument("--state", choices=JOB_STATES)
    job_list.add_argument("--location")
    job_list.add_argument("--limit", type=int, default=50)
    for action in ("retry", "cancel"):
        jobs.add_parser(action).add_argument("job_id")
    priority = jobs.add_parser("priority")
    priority.add_argument("job_id")
    priority.add_argument("priority", type=int)

    followers = commands.add_parser("followers", help="transcribing machines").add_subparsers(
        dest="action", required=True
    )
    follower_list = followers.add_parser("list")
    follower_list.add_argument("--state", choices=FOLLOWER_STATES)
    for action in ("drain", "revoke"):
        followers.add_parser(action).add_argument("follower_id")

    tokens = commands.add_parser("tokens", help="join tokens for followers").add_subparsers(
        dest="action", required=True
    )
    create = tokens.add_parser("create", help="a new join token (shown once)")
    create.add_argument("--pool", default="default")
    create.add_argument("--expires", type=parse_duration, default=7 * 86400, help="e.g. 7d")
    create.add_argument("--max-uses", type=int, default=1)
    tokens.add_parser("list")
    tokens.add_parser("revoke").add_argument("token_id")

    consent = commands.add_parser("consent", help="consent overview").add_subparsers(
        dest="action", required=True
    )
    report = consent.add_parser("report")
    report.add_argument("--location")
    report.add_argument("--limit", type=int, default=500)
    return parser


# --- output ---------------------------------------------------------------------


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return ", ".join(printable(str(item)) for item in value)
    return printable(str(value))


def print_table(out: TextIO, rows: list[dict[str, Any]], columns: Sequence[str]) -> None:
    if not rows:
        print("(none)", file=out)
        return
    cells = [[_cell(row.get(column)) for column in columns] for row in rows]
    widths = [max(len(column), *(len(row[i]) for row in cells)) for i, column in enumerate(columns)]

    def line(values: Sequence[str]) -> str:
        return "  ".join(v.ljust(w) for v, w in zip(values, widths, strict=True)).rstrip()

    print(line(columns), file=out)
    for row in cells:
        print(line(row), file=out)


def print_fields(data: dict[str, Any], out: TextIO) -> None:
    for name, value in data.items():
        print(f"{_cell(name)}: {_cell(value)}", file=out)


def table(columns: Sequence[str]) -> Renderer:
    return lambda data, out: print_table(out, data, columns)


def print_status(data: dict[str, Any], out: TextIO) -> None:
    jobs = data["jobs"]
    job_counts = ", ".join(f"{state} {_cell(jobs.get(state, 0))}" for state in JOB_STATES)
    print(f"jobs: {job_counts}", file=out)
    print(
        f"completed in the last hour: {_cell(data['completed_last_hour'])}; "
        f"failed attempts in the last day: {_cell(data['failed_attempts_last_day'])}",
        file=out,
    )
    followers = data["followers"]
    follower_counts = ", ".join(
        f"{state} {_cell(followers.get(state, 0))}" for state in FOLLOWER_STATES
    )
    print(f"followers: {follower_counts}", file=out)
    print("\nqueues:", file=out)
    print_table(out, data["pools"], ("pool", "queued", "leased"))
    print("\nlocations:", file=out)
    print_table(
        out,
        data["locations"],
        ("name", "enabled", "recordings", "consented", "last_scan_at", "last_scan_error"),
    )


def print_token(data: dict[str, Any], out: TextIO) -> None:
    print(f"join token (shown once; keep it safe): {_cell(data['token'])}", file=out)
    print_fields({name: value for name, value in data.items() if name != "token"}, out)


def print_consent(data: dict[str, Any], out: TextIO) -> None:
    print_table(
        out, data["locations"], ("name", "consented", "not_consented", "withdrawn", "missing")
    )
    print("\noutputs flagged for deletion (consent withdrawn):", file=out)
    print_table(out, data["flagged"], ("job_id", "location", "key", "output_location", "outputs"))
    if data["truncated"]:
        print("(more not shown; use --limit)", file=out)


def print_scan(data: dict[str, Any], out: TextIO) -> None:
    print(f"scan requested for {_cell(data['name'])}; it starts within a minute", file=out)


def rendered(render: Renderer, data: Any) -> str:
    """The whole rendering, or one CliError: an answer of an unexpected shape must not end
    in a traceback or half a table."""
    buffer = io.StringIO()
    try:
        render(data, buffer)
    except (KeyError, TypeError, AttributeError):
        raise CliError("the leader's answer is not in the expected form") from None
    return buffer.getvalue()


# --- commands -------------------------------------------------------------------


def _seg(value: str) -> str:
    return quote(value, safe="")


async def dispatch(args: argparse.Namespace, client: LeaderClient) -> tuple[Any, Renderer]:
    command, action = args.command, getattr(args, "action", None)

    async def get(path: str, **kwargs: Any) -> Any:
        return await client.request("GET", path, **kwargs)

    async def post(path: str, **kwargs: Any) -> Any:
        return await client.request("POST", path, **kwargs)

    if command == "whoami":
        return await get("/v1/admin/whoami"), print_fields
    if command == "status":
        return await get("/v1/admin/status"), print_status
    if command == "ingest":
        return await post(f"/v1/admin/locations/{_seg(args.name)}/ingest"), print_scan
    if command == "locations":
        if action == "add":
            if args.labels is not None and args.channels == "mono":
                raise CliError("--labels needs --channels stereo-split or auto")
            body = {
                "name": args.name,
                "root": args.root,
                "input_prefix": args.input_prefix,
                "output_prefix": args.output_prefix,
                "pool": args.pool,
                "required_device": args.device,
                "scan_interval_s": args.scan_interval,
                "channel_mode": args.channels.replace("-", "_"),
            }
            if args.labels is not None:
                body["channel_labels"] = list(args.labels)
            return await post("/v1/admin/locations", body=body), print_fields
        if action == "list":
            columns = (
                "name",
                "enabled",
                "root",
                "input_prefix",
                "pool",
                "channel_mode",
                "channel_labels",
                "last_scan_at",
                "last_scan_error",
            )
            return await get("/v1/admin/locations"), table(columns)
        return await post(f"/v1/admin/locations/{_seg(args.name)}/{action}"), print_fields
    if command == "jobs":
        if action == "list":
            params = {"limit": args.limit}
            if args.state:
                params["state"] = args.state
            if args.location:
                params["location"] = args.location
            columns = ("id", "state", "location", "key", "attempts", "priority", "failure_reason")
            return await get("/v1/admin/jobs", params=params), table(columns)
        path = f"/v1/admin/jobs/{_seg(args.job_id)}/{action}"
        if action == "priority":
            return await post(path, body={"priority": args.priority}), print_fields
        return await post(path), print_fields
    if command == "followers":
        if action == "list":
            params = {"state": args.state} if args.state else None
            columns = ("id", "pool", "state", "device", "leases", "last_seen_at")
            return await get("/v1/admin/followers", params=params), table(columns)
        return await post(f"/v1/admin/followers/{_seg(args.follower_id)}/{action}"), print_fields
    if command == "tokens":
        if action == "create":
            body = {
                "pool": args.pool,
                "expires_in_seconds": args.expires,
                "max_uses": args.max_uses,
            }
            return await post("/v1/admin/tokens", body=body), print_token
        if action == "list":
            columns = ("id", "pool", "uses", "max_uses", "revoked", "expires_at", "created_by")
            return await get("/v1/admin/tokens"), table(columns)
        return await post(f"/v1/admin/tokens/{_seg(args.token_id)}/revoke"), print_fields
    if command == "consent":
        params = {"limit": args.limit}
        if args.location:
            params["location"] = args.location
        return await get("/v1/admin/consent/report", params=params), print_consent
    raise CliError(f"unknown command {command!r}")


async def login(
    args: argparse.Namespace,
    leader: str,
    http: httpx.AsyncClient,
    store: CredentialStore,
    out: TextIO,
) -> int:
    response = await http.get(f"{leader}/v1/admin/login-config")
    if response.status_code != 200:
        raise CliError(f"the leader did not answer the sign-in request ({response.status_code})")
    try:
        providers = {p["name"]: p for p in response.json()["providers"]}
        for chosen in providers.values():
            for key in ("client_id", "device_authorization_endpoint", "token_endpoint", "scope"):
                if not isinstance(chosen[key], str):
                    raise TypeError(key)
            secret = chosen.get("client_secret")
            if secret is not None and not isinstance(secret, str):
                raise TypeError("client_secret")
    except (ValueError, KeyError, TypeError):
        raise CliError("the leader's sign-in settings are not in the expected form") from None
    if not providers:
        raise CliError("this leader has no sign-in provider configured")
    name = args.provider
    if name is None:
        if len(providers) > 1:
            raise CliError(
                "this leader accepts "
                + " and ".join(sorted(providers))
                + " sign-in; choose one with --provider"
            )
        (name,) = providers
    if name not in providers:
        raise CliError(f"this leader does not accept {name} sign-in")
    chosen = providers[name]
    config = ProviderConfig(
        name=name,
        client_id=chosen["client_id"],
        device_authorization_endpoint=chosen["device_authorization_endpoint"],
        token_endpoint=chosen["token_endpoint"],
        scope=chosen["scope"],
        client_secret=chosen.get("client_secret"),
    )
    tokens = await device_sign_in(
        http, config, prompt=lambda message: print(message, file=out, flush=True)
    )
    store.save(
        SignIn(
            leader=leader,
            provider=name,
            client_id=config.client_id,
            token_endpoint=config.token_endpoint,
            scope=config.scope,
            id_token=tokens.id_token,
            refresh_token=tokens.refresh_token,
            client_secret=config.client_secret,
        )
    )
    try:
        me = await LeaderClient(leader, store, http=http).request("GET", "/v1/admin/whoami")
    except CliError as exc:
        print(f"signed in, but the leader refused you: {exc}", file=out)
        return 1
    try:
        who, role = me["email"] or me["subject"], me["role"]
    except (KeyError, TypeError):
        raise CliError("signed in, but the leader's answer is not in the expected form") from None
    print(f"signed in as {_cell(who)} ({_cell(role)})", file=out)
    return 0


async def amain(
    argv: Sequence[str] | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    store: CredentialStore | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    out = out or sys.stdout
    err = err or sys.stderr
    args = build_parser().parse_args(argv)
    store = store or CredentialStore()
    leader = ""
    try:
        text = args.leader or os.environ.get(LEADER_ENV) or store.default_leader() or ""
        if not text:
            raise UsageError(f"no leader URL; pass --leader or set {LEADER_ENV}")
        leader = normalise_leader(text)
        async with httpx.AsyncClient(transport=transport, timeout=30.0) as http:
            if args.command == "login":
                return await login(args, leader, http, store, out)
            if args.command == "logout":
                removed = store.remove(leader)
                message = f"signed out of {leader}" if removed else f"not signed in to {leader}"
                print(message, file=out)
                return 0
            data, render = await dispatch(args, LeaderClient(leader, store, http=http))
            text = json.dumps(data, indent=2) + "\n" if args.json else rendered(render, data)
    except UsageError as exc:
        print(f"error: {exc}", file=err)
        return 2
    except (CliError, SignInError, CredentialsFileError) as exc:
        print(f"error: {exc}", file=err)
        return 1
    except httpx.HTTPError as exc:
        print(f"error: cannot reach {leader} ({type(exc).__name__})", file=err)
        return 1
    out.write(text)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(amain(argv))


def run() -> None:
    sys.exit(main())
