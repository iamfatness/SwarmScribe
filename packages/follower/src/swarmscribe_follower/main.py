"""swarmscribe-follower: run, join, leave, doctor (follower spec 4, 5.2, 5.3).

Every way this ends is a one-line `error: ...` and an exit code from errors.py; no traceback
reaches the user. The join token is never an argument (spec 5.3: it would show in process
lists and shell history): it comes from the environment, a file, or `join --token-stdin`."""

import argparse
import getpass
import logging
import os
import shutil
import ssl
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, TextIO

from pydantic import SecretStr, ValidationError
from swarmscribe_engine import DeviceUnavailableError

from . import FOLLOWER_VERSION, logs
from .agent import Agent
from .config import Settings
from .credentials import CredentialFileError, CredentialStore
from .device import cached_models, probe
from .errors import EXIT_CONFIGURATION, EXIT_OK, EXIT_UNFIT, FollowerExit
from .health import HealthServer
from .leader import LeaderClient, Refused, Transient
from .metrics import Metrics
from .models import ModelHost, ModelUnavailable, OutOfMemory
from .scratch import Scratch
from .signals import StopSignals
from .statelock import hold_state_lock
from .transfer import Links

logger = logging.getLogger(__name__)
Build = Callable[[Settings], Agent]

EXIT_UNEXPECTED = 1  # a bug; doctor also uses it for "the leader does not answer"
EXIT_INTERRUPTED = 130  # Ctrl+C outside `run` (which handles its own signals)


def load_settings(err: TextIO, **given: Any) -> Settings | None:
    try:
        return Settings(**given)
    except RuntimeError:  # no home directory to put the state folder in
        print(
            "error: cannot find a home directory for the state folder;"
            " set SWARMSCRIBE_FOLLOWER_STATE_DIR",
            file=err,
        )
        return None
    except ValidationError as error:
        # Field names and messages only: the default rendering echoes the values, and one
        # of them is the join token.
        print("error: invalid configuration (SWARMSCRIBE_* environment):", file=err)
        for problem in error.errors(include_input=False, include_url=False, include_context=False):
            field = ".".join(str(part) for part in problem["loc"]) or "settings"
            print(f"  {field}: {problem['msg']}", file=err)
        return None


def leave_settings(out: TextIO, err: TextIO) -> Settings | int:
    """The settings for `leave`: the environment's, or, when no leader is set, the leader the
    credential was issued by. An exit code when there is nothing to do or nothing can be
    done: only an ABSENT credential file means "has not joined"; an invalid setting or a
    credential file that cannot be read is an error (exit 2), never a success."""
    if os.environ.get("SWARMSCRIBE_LEADER_URL", "").strip():
        return load_settings(err) or EXIT_CONFIGURATION
    # A placeholder leader that is never contacted: it lets the other settings (the state
    # folder above all) be validated and reported as usual.
    base = load_settings(err, leader_url="https://placeholder.invalid")
    if base is None:
        return EXIT_CONFIGURATION
    try:
        stored = CredentialStore(base.credential_file).load()
    except CredentialFileError as error:
        print(f"error: {error}", file=err)
        return EXIT_CONFIGURATION
    if stored is None:
        print("this follower has not joined a leader", file=out)
        return EXIT_OK
    given: dict[str, Any] = {"leader_url": stored.leader_url}
    if stored.leader_url.lower().startswith("http://"):
        given["allow_http"] = True  # it was issued under that switch; leaving needs no more
    return load_settings(err, **given) or EXIT_CONFIGURATION


def doctor_settings(out: TextIO) -> Settings | int:
    """`doctor` says what is wrong with the settings as one of its checks, in plain words,
    instead of refusing to start."""
    try:
        return Settings()
    except (ValidationError, RuntimeError) as error:
        print(f"swarmscribe-follower {FOLLOWER_VERSION}", file=out)
        if isinstance(error, ValidationError):
            for problem in error.errors(
                include_input=False, include_url=False, include_context=False
            ):
                field = ".".join(str(part) for part in problem["loc"]) or "settings"
                print(f"settings: FAILED: {field}: {problem['msg']}", file=out)
        else:
            print(
                "settings: FAILED: no home directory; set SWARMSCRIBE_FOLLOWER_STATE_DIR",
                file=out,
            )
        print(f"result: NOT READY (exit {EXIT_CONFIGURATION})", file=out)
        return EXIT_CONFIGURATION


def configure_environment(settings: Settings) -> None:
    """Tell the model loader where its cache is and whether it may download. Must run
    before the first model is loaded: Hugging Face's library reads these when it is
    imported."""
    if settings.model_dir is not None:
        os.environ["HF_HUB_CACHE"] = str(settings.model_dir)
    if settings.offline:
        os.environ["HF_HUB_OFFLINE"] = "1"


def tls(settings: Settings) -> Any:
    """What verifies the leader's certificate: the public roots, plus the configured CA."""
    if settings.leader_ca_file is None:
        return True
    try:
        context = ssl.create_default_context()
        context.load_verify_locations(cafile=str(settings.leader_ca_file))
    except (OSError, ssl.SSLError):
        raise FollowerExit(
            EXIT_CONFIGURATION, "SWARMSCRIBE_LEADER_CA_FILE cannot be read as PEM certificates"
        ) from None
    return context


def build(settings: Settings) -> Agent:
    configure_environment(settings)
    try:
        found = probe(settings.device)
    except DeviceUnavailableError as error:
        raise FollowerExit(EXIT_UNFIT, str(error)) from None
    verify = tls(settings)
    metrics = Metrics()
    return Agent(
        settings,
        client=LeaderClient(settings.leader_url, verify=verify),
        links=Links(verify=verify, allow_http=settings.allow_http),
        models=ModelHost(
            found.choice.device,
            allowed=frozenset(settings.allowed_models),
            on_loaded=metrics.model_loaded,
        ),
        scratch=Scratch(settings.scratch, settings.state_dir),
        store=CredentialStore(settings.credential_file),
        probe=found,
        metrics=metrics,
    )


def command_run(settings: Settings, build: Build, signals: StopSignals | None = None) -> int:
    # The agent takes the state folder's lock itself and releases it when it ends.
    # `signals` are the handlers entry.run installed before anything was imported (without
    # them, run_supervised installs its own): a stop that arrived during the imports ends
    # here, before the device is probed or anything is built; one that arrives while the
    # agent is built, the model loads or the registration retries is seen by run_supervised.
    if signals is not None and signals.count:
        logger.info("stopping: stopped before it started")
        return EXIT_OK
    agent = build(settings)
    listener = None
    if settings.health_address is not None:
        # Before the model is loaded (spec 5.2, step 2): a slow load or a first download
        # must not look like a failed start to whoever probes /healthz.
        listener = HealthServer(
            settings.health_address, healthy=agent.health, metrics=agent.metrics.render
        )
        listener.start()
    try:
        return agent.run_supervised(signals=signals)
    finally:
        if listener is not None:
            listener.close()


def command_join(
    settings: Settings, build: Build, token_stdin: bool, out: TextIO, stdin: TextIO
) -> int:
    if token_stdin:
        # On a terminal the token is not echoed.
        token = (getpass.getpass('join token: ') if stdin.isatty() else stdin.readline()).strip()
        # The file would win over the variable in Settings.token(): the explicit source wins.
        settings = settings.model_copy(
            update={
                "join_token": SecretStr(token) if token else None,
                "join_token_file": None,
            }
        )
        del token
    lock = hold_state_lock(settings.state_dir)
    try:
        store = CredentialStore(settings.credential_file)
        try:
            # Before the token is spent: a folder the credential would be refused in at
            # the next start is refused now (agent.py, _check_state_folder).
            store.check_folder()
            stored = store.load()
        except CredentialFileError as error:
            raise FollowerExit(EXIT_CONFIGURATION, str(error)) from None
        if stored is not None and stored.leader_url != settings.leader_url:
            raise FollowerExit(
                EXIT_CONFIGURATION,
                f"this follower has already joined another leader ({stored.leader_url});"
                " run `swarmscribe-follower leave` first to join this one",
            )
        if stored is not None:
            # Registering again would orphan that follower and spend another token.
            print(
                f"this follower has already joined as follower {stored.follower_id};"
                " run `swarmscribe-follower leave` first to join again",
                file=out,
            )
            return EXIT_OK
        agent = build(settings)
        agent.register()
        print(f"joined as follower {agent.follower_id}", file=out)
        return EXIT_OK
    finally:
        lock.close()


def command_leave(settings: Settings, out: TextIO) -> int:
    lock = hold_state_lock(settings.state_dir)
    try:
        store = CredentialStore(settings.credential_file)
        try:
            stored = store.load()
        except CredentialFileError as error:
            raise FollowerExit(EXIT_CONFIGURATION, str(error)) from None
        if stored is None:
            print("this follower has not joined a leader", file=out)
            return EXIT_OK
        client = LeaderClient(stored.leader_url, credential=stored.credential, verify=tls(settings))
        try:
            client.deregister()
        except (Transient, Refused):
            print("the leader could not be told; it will notice the silence", file=out)
        finally:
            client.close()
        try:
            store.delete()
        except CredentialFileError as error:
            raise FollowerExit(EXIT_CONFIGURATION, str(error)) from None
        print("left; the credential is deleted", file=out)
        return EXIT_OK
    finally:
        lock.close()


def _nearest_existing(path: Path) -> Path:
    here = Path(os.path.abspath(path))
    while not here.exists() and here.parent != here:
        here = here.parent
    return here


def _folder_problem(path: Path) -> str | None:
    """None when `path` exists (or could be created) and can be written to; else why not.
    Creates nothing: another follower may be using it."""
    here = _nearest_existing(path)
    if not here.is_dir():
        return f"{here} is not a folder"
    if not os.access(here, os.W_OK | os.X_OK):
        return f"{here} cannot be written to by this user"
    return None


def _untrusted(settings: Settings) -> str | None:
    """Why `run` would refuse to keep a credential in the state folder (another user's, or
    writable by others), or None. Changes nothing: `run` tightens a folder of its own."""
    try:
        CredentialStore(settings.credential_file).check_folder(tighten=False)
    except CredentialFileError as error:
        return str(error)
    return None


def _free_space(path: Path) -> str:
    try:
        return f"{shutil.disk_usage(_nearest_existing(path)).free / 2**30:.1f} GiB free"
    except OSError:
        return "free space unknown"


def command_doctor(
    settings: Settings,
    out: TextIO,
    *,
    host: Callable[..., ModelHost] = ModelHost,
    client: Callable[..., LeaderClient] = LeaderClient,
    load_model: bool = True,
    ask_leader: bool = True,
) -> int:
    """What start-up checks, said out loud, one line per check: settings, folders, device,
    the model with a real inference, the leader. Registers nothing and claims nothing, and
    prints no secret (no token, credential or link). `ask_leader=False` leaves the leader
    out: a machine or an image can be checked where there is no network."""
    configure_environment(settings)
    print(f"swarmscribe-follower {FOLLOWER_VERSION}", file=out)
    print(f"settings: ok (leader {settings.leader_url}, pool {settings.pool})", file=out)
    code = EXIT_OK
    folders = (("state folder", settings.state_dir), ("scratch folder", settings.scratch))
    for name, folder in folders:
        problem = _folder_problem(folder)
        if problem is None and folder == settings.state_dir:
            problem = _untrusted(settings)
        if problem is None:
            print(f"{name}: ok ({folder}, {_free_space(folder)})", file=out)
        else:
            print(f"{name}: FAILED: {problem}", file=out)
            code = EXIT_CONFIGURATION
    try:
        found = probe(settings.device)
    except DeviceUnavailableError as error:
        print(f"device: FAILED: {error}", file=out)
        print(f"result: NOT READY (exit {EXIT_UNFIT})", file=out)
        return EXIT_UNFIT
    choice = found.choice
    gpu = f" ({found.gpu_name}, {found.gpu_memory_mb} MiB)" if found.gpu_name else ""
    print(f"device: {choice.device}{gpu}", file=out)
    print(f"cached models: {', '.join(cached_models(settings.model_dir)) or '(none)'}", file=out)
    if load_model:
        models = host(choice.device, allowed=frozenset(settings.allowed_models))
        try:
            wanted = settings.startup_model or choice.model
            models.get(wanted, choice.compute_type)
            print(f"model: {wanted} ({choice.compute_type}) loaded and ran", file=out)
        except (ModelUnavailable, OutOfMemory, DeviceUnavailableError) as error:
            print(f"model: FAILED: {error}", file=out)
            code = EXIT_UNFIT
        finally:
            models.close()
    else:
        print("model: not checked (--no-model)", file=out)
    if ask_leader:
        leader = client(settings.leader_url, verify=tls(settings))
        try:
            if leader.healthy():
                print("leader: answers", file=out)
            else:
                print("leader: FAILED: no answer from its /healthz", file=out)
                code = code or EXIT_UNEXPECTED
        finally:
            leader.close()
    else:
        print("leader: not checked (--no-leader)", file=out)
    joined = CredentialStore(settings.credential_file).path.is_file()
    print(f"joined: {'yes' if joined else 'no'}", file=out)
    print("result: ready" if code == EXIT_OK else f"result: NOT READY (exit {code})", file=out)
    return code


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(
        prog="swarmscribe-follower",
        description="Take recordings from a SwarmScribe leader and transcribe them. Settings"
        " come from SWARMSCRIBE_* environment variables (SWARMSCRIBE_LEADER_URL is required).",
        epilog="The join token is never a command-line argument, so it stays out of process"
        " lists and shell history: set SWARMSCRIBE_JOIN_TOKEN_FILE (preferred) or"
        " SWARMSCRIBE_JOIN_TOKEN, or use `join --token-stdin`. Exit codes: 0 stopped or drained;"
        " 2 invalid configuration; 3 this machine cannot do the work; 4 not authorised;"
        " 5 protocol version refused; 1 unexpected error (doctor: leader unreachable).",
    )
    top.add_argument("--version", action="version", version=f"%(prog)s {FOLLOWER_VERSION}")
    commands = top.add_subparsers(dest="command", required=True, metavar="command")
    commands.add_parser("run", help="join if needed, then work until stopped")
    join = commands.add_parser("join", help="register with the leader and store the credential")
    join.add_argument(
        "--leader",
        metavar="URL",
        help="the leader's address (instead of SWARMSCRIBE_LEADER_URL; this one wins)",
    )
    join.add_argument(
        "--token-stdin", action="store_true", help="read the join token from standard input"
    )
    commands.add_parser("leave", help="deregister and delete the stored credential")
    doctor = commands.add_parser("doctor", help="check settings, folders, device, model, leader")
    doctor.add_argument(
        "--no-model", action="store_true", help="do not load the model or run the warm-up"
    )
    doctor.add_argument(
        "--no-leader", action="store_true", help="do not ask the leader's /healthz"
    )
    return top


def main(
    argv: Sequence[str] | None = None,
    *,
    build: Build = build,
    out: TextIO | None = None,
    err: TextIO | None = None,
    stdin: TextIO | None = None,
    signals: StopSignals | None = None,
) -> int:
    """`signals`: the stop-signal handlers `entry.run` installed before this module was
    imported, for `run` only (entry.py says why)."""
    out, err, stdin = out or sys.stdout, err or sys.stderr, stdin or sys.stdin
    args = parser().parse_args(argv)
    if args.command == "doctor":
        settings = doctor_settings(out)
        if isinstance(settings, int):
            return settings
    elif args.command == "leave":
        settings = leave_settings(out, err)
        if isinstance(settings, int):
            return settings
    else:
        given = {"leader_url": args.leader} if args.command == "join" and args.leader else {}
        settings = load_settings(err, **given)
        if settings is None:
            return EXIT_CONFIGURATION
    logs.configure_logging(settings.log_format, stream=err)
    try:
        if args.command == "run":
            return command_run(settings, build, signals)
        if args.command == "join":
            return command_join(settings, build, args.token_stdin, out, stdin)
        if args.command == "leave":
            return command_leave(settings, out)
        return command_doctor(
            settings, out, load_model=not args.no_model, ask_leader=not args.no_leader
        )
    except FollowerExit as stop:
        if stop.code != EXIT_OK:
            print(f"error: {stop.reason}", file=err)
        return stop.code
    except KeyboardInterrupt:
        print("interrupted", file=err)
        return EXIT_INTERRUPTED
    except Exception as error:
        # A bug. The class only: an exception's text can hold a path or a URL.
        logger.error("unexpected %s", type(error).__name__)
        print(f"error: unexpected {type(error).__name__} (this is a bug in the follower)", file=err)
        return EXIT_UNEXPECTED


# `run`, the command's entry point, lives in entry.py: it installs the stop-signal handlers
# before this module and its imports are loaded.
from .entry import run  # noqa: E402, F401
