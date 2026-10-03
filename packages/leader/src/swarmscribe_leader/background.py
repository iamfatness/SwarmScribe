import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

LOCK_KEYS = {"reaper": 0x53570001, "scanner": 0x53570002}


async def run_exclusive(
    engine: AsyncEngine, name: str, work: Callable[[], Awaitable[None]]
) -> bool:
    """Run `work` only if no other replica holds the advisory lock for `name`."""
    key = LOCK_KEYS[name]
    async with engine.connect() as conn:
        got = await conn.scalar(text("select pg_try_advisory_lock(:key)"), {"key": key})
        if not got:
            await conn.rollback()
            return False
        try:
            await work()
            return True
        finally:
            await conn.execute(text("select pg_advisory_unlock(:key)"), {"key": key})
            await conn.commit()


async def run_periodically(
    stop: asyncio.Event,
    interval: float,
    step: Callable[[], Awaitable[None]],
    name: str,
) -> None:
    while not stop.is_set():
        try:
            await step()
        except Exception:
            logger.exception("background task %s failed", name)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)
