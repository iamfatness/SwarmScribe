"""The scratch folder: where a job's recording and outputs live, and only while it runs
(follower spec 5.8).

The follower wipes this folder, so it must be sure the folder is its own. It leaves a marker
file there, and refuses a folder that holds anything but has no marker: a wrong setting must
never delete someone's files. Deletion never follows a link (a symbolic link, or a Windows
junction, is removed itself and what it points at is left alone), and on Windows it works on
extended-length paths so a deep tree is not stranded by the 260-character limit. Wiping is
deletion, not secure erasure."""

import errno
import os
import stat
import uuid
from pathlib import Path

MARKER = ".swarmscribe-scratch"
MARKER_TEXT = "SwarmScribe follower scratch: everything here is deleted.\n"
_REPARSE_POINT = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT: a symbolic link or a junction


class ScratchError(Exception):
    """The scratch folder cannot be used. The message says what to do."""


class ScratchNotOurs(ScratchError):
    """The scratch folder holds files and no marker; the message names the folder."""


class ScratchWipeFailed(ScratchError):
    """Something in the scratch folder could not be deleted (a file another program holds
    open, a folder without write permission). The marker stays, so the next start tries
    again."""


class ScratchDiskFull(ScratchError):
    """The disk holding the scratch folder is full."""


def _typed(exc: OSError, root: Path, doing: str) -> ScratchError:
    if exc.errno in (errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)):
        return ScratchDiskFull(
            f"the disk is full: cannot {doing} in the scratch folder {root}; free space or"
            " point SWARMSCRIBE_FOLLOWER_SCRATCH_DIR at a bigger disk"
        )
    return ScratchError(f"cannot {doing} in the scratch folder {root}: {exc.strerror or 'error'}")


def _extended(path: Path) -> str:
    """The path as a string the OS accepts however long it is (Windows only needs the prefix)."""
    text = os.path.abspath(path)
    if os.name == "nt" and not text.startswith("\\\\?\\"):
        text = "\\\\?\\UNC\\" + text[2:] if text.startswith("\\\\") else "\\\\?\\" + text
    return text


def _is_link(info: os.stat_result) -> bool:
    if stat.S_ISLNK(info.st_mode):
        return True
    return bool(getattr(info, "st_file_attributes", 0) & _REPARSE_POINT)


def _delete(path: str) -> bool:
    """Delete one file, link or folder tree without following links. True when it is gone."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    if stat.S_ISDIR(info.st_mode) and not _is_link(info):
        gone = True
        try:
            with os.scandir(path) as children:
                names = [child.name for child in children]
        except OSError:
            return False
        for name in names:
            gone = _delete(os.path.join(path, name)) and gone
        if not gone:
            return False
        return _try(os.rmdir, path)
    if _try(os.unlink, path):
        return True
    # A directory link is removed as a directory on Windows.
    return _is_link(info) and stat.S_ISDIR(info.st_mode) and _try(os.rmdir, path)


def _try(operation, path: str) -> bool:
    for _attempt in range(2):
        try:
            operation(path)
            return True
        except FileNotFoundError:
            return True
        except PermissionError:
            try:  # a read-only file cannot be deleted on Windows until the bit is cleared
                os.chmod(path, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
            except OSError:
                return False
        except OSError:
            return False
    return False


class Scratch:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _is_marked(self) -> bool:
        """The marker must be a plain file of ours: not a link, with our text."""
        try:
            marker = self.root / MARKER
            if not stat.S_ISREG(os.lstat(marker).st_mode):
                return False
            return marker.read_text(encoding="utf-8", errors="replace") == MARKER_TEXT
        except OSError:
            return False

    def prepare(self) -> None:
        """Create the folder, or take it over if it is empty or carries the marker; then
        wipe whatever a previous run left. Raises ScratchNotOurs for a folder that holds
        someone's files, ScratchWipeFailed if something cannot be deleted (it is tried again
        at the next start) and ScratchDiskFull when the disk is full."""
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            if not self._is_marked() and any(self.root.iterdir()):
                raise ScratchNotOurs(
                    f"the scratch folder {self.root} holds files that are not the follower's;"
                    " point SWARMSCRIBE_FOLLOWER_SCRATCH_DIR at an empty folder"
                )
            if not self._is_marked():
                (self.root / MARKER).write_text(MARKER_TEXT, encoding="utf-8")
        except OSError as exc:
            raise _typed(exc, self.root, "prepare it") from None
        self.wipe()
        left = [entry for entry in self._entries()]
        if left:
            raise ScratchWipeFailed(
                f"{len(left)} item(s) in the scratch folder {self.root} could not be deleted"
                " (a file may be open in another program); close it, the follower will try"
                " again at the next start"
            )

    def _entries(self) -> list[Path]:
        try:
            return [entry for entry in self.root.iterdir() if entry.name != MARKER]
        except OSError:
            return []

    def wipe(self) -> None:
        """Delete everything but the marker. Does nothing unless the folder carries the
        marker, so it can never delete a folder the follower did not make. Never raises: what
        cannot be deleted now is deleted by the next start."""
        if not self._is_marked():
            return
        for entry in self._entries():
            self.remove(entry)

    def job_dir(self, job_id: str) -> Path:
        """A fresh folder for one job. The id comes from the leader and becomes a folder
        name, so it must be a UUID and nothing else."""
        folder = self.root / f"job-{uuid.UUID(job_id)}"
        self.remove(folder)
        try:
            folder.mkdir()
        except OSError as exc:
            raise _typed(exc, self.root, "make the job folder") from None
        return folder

    @staticmethod
    def remove(entry: Path | None) -> None:
        """Delete a file or a folder; a link is removed, never followed. Never raises: a
        file that cannot be deleted now is deleted by the next start."""
        if entry is None:
            return
        try:
            _delete(_extended(entry))
        except (OSError, ValueError):
            pass
