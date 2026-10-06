import argparse
import asyncio
import logging.config
import sys
from collections.abc import Sequence

from pydantic import ValidationError
from sqlalchemy.engine import make_url

from .config import Settings
from .db.migrate import current_revision, head_revision, is_known_revision, upgrade
from .db.session import make_engine, to_async_url

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"},
    },
    "handlers": {
        "stderr": {
            "class": "logging.StreamHandler",
            "formatter": "default",
            "stream": "ext://sys.stderr",
        },
    },
    "root": {"level": "INFO", "handlers": ["stderr"]},
}

# At a stop, how long the requests in hand have to finish before they are cancelled. With
# app.py's limits for what follows (7 s) a leader has ended at most about 17 s after it was
# told to, whatever the database does. Without this uvicorn waits for ever, and a request
# waiting for a database that does not answer holds the stop until the container is killed.
REQUEST_DRAIN_SECONDS = 10


def _without_secrets(message: str, settings: Settings) -> str:
    """`message` with the database URL and its password taken out. A driver's or a
    migration's error is shown as it is; none is known to quote them, and none may."""
    url = settings.database_url.get_secret_value()
    secrets = {url, to_async_url(url)}
    try:
        secrets.add(make_url(to_async_url(url)).password)
    except Exception:  # not a URL: the settings would already have refused it
        pass
    for secret in sorted(filter(None, secrets), key=len, reverse=True):
        message = message.replace(secret, "***")
    return message


async def _schema_problem(settings: Settings, *, migrating: bool = False) -> str | None:
    """Why the leader must not serve this database, or None when the schema is current.
    With `migrating`, only why the database cannot be reached: its schema is about to
    change."""
    engine = make_engine(settings.database_url.get_secret_value())
    try:
        revision = await current_revision(engine)
    except Exception as exc:  # refused, unreachable, bad credentials, timeout
        return _without_secrets(
            f"cannot connect to the database: {type(exc).__name__}: {exc}", settings
        )
    finally:
        await engine.dispose()
    expected = head_revision()
    if revision == expected:
        return None
    if migrating:
        return None
    if revision is not None and not is_known_revision(revision):
        return (
            f"database is ahead of this leader: it is at revision {revision}, "
            f"this leader expects {expected}; run a newer leader"
        )
    return (
        f"database is at revision {revision or 'none'}, expected {expected}; "
        "run `swarmscribe-leader migrate`"
    )


def _load_settings() -> Settings | None:
    try:
        return Settings()
    except ValidationError as exc:
        # Field names and messages only: the default rendering echoes the offending values,
        # which here are secrets (the link key, the database URL with its password).
        print("error: invalid configuration (SWARMSCRIBE_* environment):", file=sys.stderr)
        for error in exc.errors(include_input=False, include_url=False, include_context=False):
            field = ".".join(str(part) for part in error["loc"]) or "settings"
            print(f"  {field}: {error['msg']}", file=sys.stderr)
        return None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="swarmscribe-leader")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="bring the database schema up to date")
    serve = commands.add_parser("serve", help="run the leader")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
    settings = _load_settings()
    if settings is None:
        return 2

    if args.command == "migrate":
        # Exit 2: nothing was tried (settings, or a database that cannot be reached, as for
        # `serve`). Exit 1: a migration was started and failed. One error either way, not a
        # traceback: this is what the log of a failed migration Job shows.
        problem = asyncio.run(_schema_problem(settings, migrating=True))
        if problem is not None:
            print(f"error: {problem}", file=sys.stderr)
            return 2
        try:
            upgrade(settings.database_url.get_secret_value())
        except Exception as exc:  # the driver's or the migration's own error says which
            failure = f"the migration failed: {type(exc).__name__}: {exc}"
            print(f"error: {_without_secrets(failure, settings)}", file=sys.stderr)
            return 1
        print(f"database is at revision {head_revision()}")
        return 0

    problem = asyncio.run(_schema_problem(settings))
    if problem is not None:
        print(f"error: {problem}", file=sys.stderr)
        return 2

    logging.config.dictConfig(LOGGING)
    import uvicorn

    from .app import create_app

    # access_log=False: file-link URLs contain signed tokens and must never be logged.
    # log_config=None: keep the logging configured above (uvicorn's loggers propagate to it).
    uvicorn.run(
        create_app(settings),
        host=args.host,
        port=args.port,
        proxy_headers=True,
        access_log=False,
        log_config=None,
        timeout_graceful_shutdown=REQUEST_DRAIN_SECONDS,
    )
    return 0


def run() -> None:
    sys.exit(main())
