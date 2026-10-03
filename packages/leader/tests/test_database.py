from sqlalchemy import text
from swarmscribe_leader.clock import utcnow
from swarmscribe_leader.db.session import make_engine


async def test_the_test_database_is_reachable(database_url):
    engine = make_engine(database_url)
    try:
        async with engine.connect() as conn:
            assert await conn.scalar(text("select 1")) == 1
            assert await conn.scalar(text("select current_database()")) == "swarmscribe_test"
    finally:
        await engine.dispose()


def test_utcnow_is_timezone_aware():
    assert utcnow().utcoffset().total_seconds() == 0
