import asyncio

from sqlalchemy import text
from swarmscribe_console.background import run_exclusive as console_exclusive
from swarmscribe_leader.background import LOCK_KEYS
from swarmscribe_leader.background import run_exclusive as leader_exclusive

KEY = LOCK_KEYS["reaper"]  # the same number, so the two helpers contend for one lock


async def _contention(engine, run) -> tuple[bool, bool, int]:
    ran = 0

    async def work() -> None:
        nonlocal ran
        ran += 1

    async with engine.connect() as other:
        await other.execute(text("select pg_advisory_lock(:key)"), {"key": KEY})
        blocked = await run(work)
        await other.execute(text("select pg_advisory_unlock(:key)"), {"key": KEY})
        await other.commit()
    free = await run(work)
    return blocked, free, ran


async def test_the_console_helper_behaves_like_the_leaders_on_lock_contention(engine):
    leader = await _contention(engine, lambda work: leader_exclusive(engine, "reaper", work))
    console = await _contention(engine, lambda work: console_exclusive(engine, KEY, work))
    assert leader == console == (False, True, 1)


async def test_the_lock_is_released_when_the_work_raises(engine):
    async def failing() -> None:
        raise RuntimeError("x")

    try:
        await console_exclusive(engine, KEY, failing)
    except RuntimeError:
        pass
    ran = []

    async def work() -> None:
        ran.append(1)

    assert await asyncio.wait_for(console_exclusive(engine, KEY, work), 5) is True
    assert ran == [1]
