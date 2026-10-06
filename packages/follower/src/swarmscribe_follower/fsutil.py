"""Small filesystem helpers shared by the credential file and the scratch folder."""

import os
import stat
from pathlib import Path


def private_folder(path: Path) -> None:
    """Make `path` a folder only this user can use (mode 0700 on POSIX).

    A new folder is created 0700 (its parents, if missing, get the default mode). An existing
    folder that belongs to this user and is looser is tightened. One that belongs to someone
    else is left alone: the callers that trust a folder refuse it by themselves. On Windows
    the same is done with the folder's access control list (winacl.make_private): this
    account, SYSTEM and Administrators, and nobody else."""
    path = Path(path)
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.mkdir(path, 0o700)
        except FileExistsError:
            pass
        else:
            return
        info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode):
        return
    if os.name == "nt":
        from . import winacl

        winacl.make_private(path)
        return
    if info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) & 0o077:
        os.chmod(path, 0o700)
