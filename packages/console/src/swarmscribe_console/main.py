import argparse
import sys
from collections.abc import Sequence

from pydantic import ValidationError

from .config import Settings
from .db.migrate import head_revision, upgrade

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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="swarmscribe-console")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="bring the console database schema up to date")
    args = parser.parse_args(argv)
    settings = _load_settings()
    if settings is None:
        return 2

    if args.command == "migrate":
        upgrade(settings.database_url.get_secret_value())
        print(f"database is at revision {head_revision()}")
        return 0
    return 2


def run() -> None:
    sys.exit(main())
