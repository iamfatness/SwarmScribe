import argparse
import asyncio
import logging.config
import sys
from collections.abc import Sequence

from pydantic import ValidationError
from swarmscribe_leader.db.session import make_engine

from .config import Settings
from .db.migrate import current_revision, head_revision, is_known_revision, upgrade

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
    # httpx logs every request URL at INFO; keep client libraries quiet.
    "loggers": {
        "httpx": {"level": "WARNING"},
        "httpcore": {"level": "WARNING"},
    },
    "root": {"level": "INFO", "handlers": ["stderr"]},
}


def _load_settings() -> Settings | None:
    try:
        return Settings()
    except ValidationError as exc:
        # Field names and messages only: the default rendering echoes secrets.
        print("error: invalid configuration (SWARMSCRIBE_CONSOLE_* environment):", file=sys.stderr)
        for error in exc.errors(include_input=False, include_url=False, include_context=False):
            field = ".".join(str(part) for part in error["loc"]) or "settings"
            print(f"  {field}: {error['msg']}", file=sys.stderr)
        return None


async def _schema_problem(settings: Settings) -> str | None:
    """Why the console must not serve this database, or None when the schema is current."""
    engine = make_engine(settings.database_url.get_secret_value())
    try:
        revision = await current_revision(engine)
    except Exception as exc:  # refused, unreachable, bad credentials, timeout
        return f"cannot connect to the database: {type(exc).__name__}"
    finally:
        await engine.dispose()
    expected = head_revision()
    if revision == expected:
        return None
    if revision is not None and not is_known_revision(revision):
        return (
            f"database is ahead of this console: it is at revision {revision}, "
            f"this console expects {expected}; run a newer console"
        )
    return (
        f"database is at revision {revision or 'none'}, expected {expected}; "
        "run `swarmscribe-console migrate`"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="swarmscribe-console")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="bring the console database schema up to date")
    serve = commands.add_parser("serve", help="run the console")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
    settings = _load_settings()
    if settings is None:
        return 2

    if args.command == "migrate":
        upgrade(settings.database_url.get_secret_value())
        print(f"database is at revision {head_revision()}")
        return 0

    problem = asyncio.run(_schema_problem(settings))
    if problem is not None:
        print(f"error: {problem}", file=sys.stderr)
        return 2

    logging.config.dictConfig(LOGGING)
    import uvicorn

    from .app import create_app

    # access_log=False: the sign-in callback's URL carries an authorization code.
    # log_config=None: keep the logging configured above.
    uvicorn.run(
        create_app(settings),
        host=args.host,
        port=args.port,
        proxy_headers=True,
        access_log=False,
        log_config=None,
    )
    return 0


def run() -> None:
    sys.exit(main())
