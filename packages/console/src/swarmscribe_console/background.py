"""Advisory-lock exclusivity for the console's background work. The same pattern as the
leader's background.run_exclusive, keyed by number so that each leader has its own lock."""

from collections.abc import Awaitable, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def run_exclusive(
    engine: AsyncEngine, key: int, work: Callable[[], Awaitable[None]]
) -> bool:
    """Run `work` only if no other replica holds the session advisory lock `key`.

    The lock outlives the transaction that took it, so that transaction is committed at once
    and the lock connection sits idle while `work` runs. If anything interrupts the acquire
    or the unlock (including cancellation), the connection is invalidated rather than
    returned to the pool, so the server session and its lock end instead of leaking."""
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
