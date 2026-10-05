"""Where the `swarmscribe-follower` command starts.

For `run`, the stop-signal handlers are installed here, before anything else is imported.
Importing the follower (pydantic, the engine, the model libraries) and building the agent
takes from a third of a second to several seconds, and until a handler exists a stop is
lost or fatal: as PID 1 in a container the kernel DROPS a SIGTERM that has no handler, so a
`docker stop` right after `docker run` was ignored and the container was killed (exit 137)
at the end of the stop window, by then perhaps in the middle of a job; anywhere else the
default action ends the process uncleanly. With the handlers installed first, a stop that
arrives before start-up has finished is counted, and `run` ends with exit 0 before it takes
the state folder's lock, loads a model or tells the leader anything.

What is left is the time the Python interpreter itself needs to reach this function (tens of
milliseconds). Nothing in Python can close that; the image closes it with an init (`tini`)
as PID 1, see docker/follower.Dockerfile.

The other commands (`join`, `leave`, `doctor`) keep Python's own handling: Ctrl+C raises
KeyboardInterrupt and they exit 130.

This module imports only the standard library at import time. Keep it so."""

import sys


def run() -> None:
    signals = previous = None
    if sys.argv[1:2] == ["run"]:
        from .signals import StopSignals

        signals = StopSignals()
        previous = signals.install()
    try:
        from .main import main

        code = main(signals=signals)
    finally:
        if signals is not None:
            signals.restore(previous)
    sys.exit(code)
