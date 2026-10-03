import argparse
import asyncio
import sys
from collections.abc import Sequence

from .config import Settings
from .db.migrate import current_revision, head_revision, upgrade
from .db.session import make_engine


async def _database_is_current(settings: Settings) -> bool:
    engine = make_engine(settings.database_url)
    try:
        return await current_revision(engine) == head_revision()
    except Exception:
        return False
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="swarmscribe-leader")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="bring the database schema up to date")
    serve = commands.add_parser("serve", help="run the leader")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
    settings = Settings()

    if args.command == "migrate":
        upgrade(settings.database_url)
        print(f"database is at revision {head_revision()}")
        return 0

    if not asyncio.run(_database_is_current(settings)):
        print(
            "error: the database is not at the current schema; run `swarmscribe-leader migrate`",
            file=sys.stderr,
        )
        return 2

    import uvicorn

    from .app import create_app

    # access_log=False: file-link URLs contain signed tokens and must never be logged.
    uvicorn.run(
        create_app(settings),
        host=args.host,
        port=args.port,
        proxy_headers=True,
        access_log=False,
    )
    return 0


def run() -> None:
    sys.exit(main())
