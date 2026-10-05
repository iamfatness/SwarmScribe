import logging
import sys
import threading
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
# follower_testkit sits beside this file; leader_testkit beside the leader's tests (it is
# what test_real_leader.py builds a real leader with).
for folder in (HERE, HERE.parents[1] / "leader" / "tests"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))


@pytest.fixture(autouse=True)
def no_thread_left_running():
    """Every test ends with the threads it started stopped (a lease keeper outliving its
    job, a worker that never joined)."""
    before = set(threading.enumerate())
    yield
    deadline = time.monotonic() + 5
    while True:
        extra = [t for t in threading.enumerate() if t not in before and t.is_alive()]
        if not extra or time.monotonic() >= deadline:
            break
        time.sleep(0.01)
    assert not extra, f"threads still running after the test: {[t.name for t in extra]}"


@pytest.fixture(autouse=True)
def logging_is_left_as_found():
    """A test that configures logging (the command line does) must not change what the rest
    of the process, other packages' tests included, sees."""
    names = ("", "httpx", "httpcore")
    before = [
        (logging.getLogger(n), logging.getLogger(n).level, list(logging.getLogger(n).handlers))
        for n in names
    ]
    yield
    for logger, level, handlers in before:
        logger.setLevel(level)
        logger.handlers[:] = handlers
