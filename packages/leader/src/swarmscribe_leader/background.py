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
    """Run `work` only if no other replica holds the advisory lock for `name`.

    The lock is a session lock: it outlives the transaction that took it, so that transaction
    is committed at once and the lock connection sits idle (not idle in a transaction) while
    `work` runs. If anything interrupts the acquire or the unlock (including cancellation),
    the connection is invalidated rather than returned to the pool, so the server session and
    the lock with it end instead of leaking to the next user of that pooled connection.
    """
    key = LOCK_KEYS[name]
    async with engine.connect() as conn:
        clean = False
        try:
            got = await conn.scalar(text("select pg_try_advisory_lock(:key)"), {"key": key})
            await conn.commit()
            if not got:
                clean = True
                return False
            try:
                await work()
            finally:
                await conn.execute(text("select pg_advisory_unlock(:key)"), {"key": key})
                await conn.commit()
                clean = True
            return True
        finally:
            if not clean:
                await conn.invalidate()


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
