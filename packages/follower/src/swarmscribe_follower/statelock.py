"""The exclusive lock on the state folder: two followers must never share a credential and
a scratch folder (follower spec 5.2, step 1)."""

import os
from pathlib import Path
from typing import BinaryIO

from .errors import EXIT_CONFIGURATION, FollowerExit

LOCK_NAME = "follower.lock"


def hold_state_lock(state_dir: Path) -> BinaryIO:
    """Lock `<state>/follower.lock` for as long as the returned file stays open; closing it
    releases the lock (so does the process ending, however it ends). Raises FollowerExit(2)
    when the folder cannot be used or another follower holds the lock."""
    try:
        state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        handle = (state_dir / LOCK_NAME).open("a+b")
    except OSError as error:
        why = error.strerror or type(error).__name__
        raise FollowerExit(
            EXIT_CONFIGURATION, f"the state folder {state_dir} cannot be used: {why}"
        ) from None
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise FollowerExit(
            EXIT_CONFIGURATION, f"another follower is already using the state folder {state_dir}"
        ) from None
    return handle
