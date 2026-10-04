"""Logging an exception without its text (which can carry SQL parameters, credentials or
request values): the type, where it happened and the traceback's frames (file, line,
function, source line). One place, so the web app's ContainErrors and the background loops
log failures the same way."""

import logging
import time
import traceback
from collections.abc import Awaitable, Callable

# A failure that repeats (a database outage fails the poller every tick) is logged once per
# window per cause; the next line it does write says how many it held back.
REPEAT_LOG_SECONDS = 30.0
_clock: Callable[[], float] = time.monotonic
_repeats: dict[str, tuple[float, int]] = {}  # key -> (when last logged, held back since)


def _due(key: str, every: float) -> int | None:
    """None while `key` was logged less than `every` seconds ago (and the held-back count
    grows); otherwise the number held back since the last line, and the window restarts."""
    now = _clock()
    last, held = _repeats.get(key, (None, 0))
    if last is not None and now - last < every:
        _repeats[key] = (last, held + 1)
        return None
    _repeats[key] = (now, 0)
    return held


def log_limited(
    logger: logging.Logger,
    label: str,
    exc: BaseException | None,
    *,
    key: str,
    every: float = REPEAT_LOG_SECONDS,
    where: str | None = None,
    frames: bool = True,
) -> None:
    """log_contained at most once per `every` seconds for `key` (the cause). `frames=False`
    logs the type alone, on one line; `exc=None` logs `label` alone."""
    held = _due(key, every)
    if held is None:
        return
    more = f" ({held} similar suppressed)" if held else ""
    if exc is None:
        logger.error("%s%s", label, more)
    elif frames:
        place = f" at {where}" if where else ""
        stack = "".join(traceback.format_tb(exc.__traceback__)).rstrip()
        logger.error("%s: %s%s%s\n%s", label, type(exc).__name__, place, more, stack)
    else:
        logger.error("%s: %s%s", label, type(exc).__name__, more)


def log_contained(
    logger: logging.Logger, label: str, exc: BaseException, *, where: str | None = None
) -> None:
    frames = "".join(traceback.format_tb(exc.__traceback__)).rstrip()
    place = f" at {where}" if where else ""
    logger.error("%s: %s%s\n%s", label, type(exc).__name__, place, frames)


def contained(
    logger: logging.Logger, label: str, step: Callable[[], Awaitable[None]]
) -> Callable[[], Awaitable[None]]:
    """`step` that logs its failure with log_contained and never raises (cancellation still
    propagates), so the loop running it never logs the exception's text."""

    async def run() -> None:
        try:
            await step()
        except Exception as exc:
            # A step that fails every tick (the database is down) logs once per 30 s per cause.
            log_limited(
                logger, f"background task {label} failed", exc,
                key=f"{label}:{type(exc).__name__}",
            )

    return run
