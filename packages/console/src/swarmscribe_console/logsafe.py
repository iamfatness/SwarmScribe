"""Logging an exception without its text (which can carry SQL parameters, credentials or
request values): the type, where it happened and the traceback's frames (file, line,
function, source line). One place, so the web app's ContainErrors and the background loops
log failures the same way."""

import logging
import traceback
from collections.abc import Awaitable, Callable


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
            log_contained(logger, f"background task {label} failed", exc)

    return run
