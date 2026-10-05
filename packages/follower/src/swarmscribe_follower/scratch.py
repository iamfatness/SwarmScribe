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
import re
import secrets
import stat
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from .fsutil import private_folder

MARKER = ".swarmscribe-scratch"
MARKER_TEXT = "SwarmScribe follower scratch: everything here is deleted.\n"
RECORD = "scratch-id"
"""File in the state folder holding the id written into the marker. A marker copied from
elsewhere (or restored from a backup) carries another id, so it authorises nothing."""
_MARKER_ID = re.compile(r"id: ([0-9a-f]{32})\n")
_JOB_FOLDER = re.compile(r"job-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
DELETE_DELAYS = (0.2, 0.4, 0.6, 0.8)
"""Five tries about two seconds apart in all: a scanner or indexer holding a file for a
moment is the usual cause of a failed delete on Windows."""
_REPARSE_POINT = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT: a symbolic link or a junction


class ScratchError(Exception):
    """The scratch folder cannot be used. The message says what to do."""


class ScratchNotOurs(ScratchError):
    """The scratch folder holds files and no marker; the message names the folder."""


class ScratchWipeFailed(ScratchError):
    """Something in the scratch folder could not be deleted (a file another program holds
    open, a folder without write permission). The marker stays, so the next start tries
    again."""


class ScratchOutside(ScratchError):
    """A path given to `remove` is not strictly inside the scratch folder."""


class ScratchDiskFull(ScratchError):
    """The disk holding the scratch folder is full."""


def _same(a: str, b: str) -> bool:
    return os.path.normcase(a) == os.path.normcase(b)


def _within(parent: str, child: str) -> bool:
    """Whether `child` is `parent` or below it, comparing absolute paths as written."""
    try:
        parent, child = os.path.normcase(parent), os.path.normcase(child)
        return os.path.commonpath([parent, child]) == parent
    except ValueError:  # another drive
        return False


def check_folders(state: Path, scratch: Path, model: Path | None = None) -> None:
    """Refuse a layout in which wiping scratch could reach the state folder (the credential)
    or the model folder. The scratch folder may sit inside the state folder (the default),
    never be it, hold it, or hold the models. Links are resolved too."""
    for resolve in (os.path.abspath, os.path.realpath):
        s, c = resolve(state), resolve(scratch)
        if _within(c, s):
            raise ScratchError(
                f"the scratch folder {scratch} is, or holds, the state folder {state}: wiping"
                " it would delete the credential; choose a separate scratch folder"
            )
        if model is not None and _within(c, resolve(model)):
            raise ScratchError(
                f"the scratch folder {scratch} is, or holds, the model folder {model}: wiping"
                " it would delete the models; choose a separate scratch folder"
            )


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
        except PermissionError:
            try:  # a folder without read/write permission: ours to open up (never a link)
                os.chmod(path, 0o700)
                with os.scandir(path) as children:
                    names = [child.name for child in children]
            except OSError:
                return False
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
                if not _is_link(os.lstat(path)):  # chmod would follow a link on POSIX
                    os.chmod(path, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
            except OSError:
                return False
        except OSError:
            return False
    return False


class Scratch:
    """`state_dir`, when given, is the follower's state folder: it is created private, the
    scratch folder may not contain it, and it keeps the record of this folder's marker id.

    Marker rule, kept simple: the marker carries a random id and the state folder records it.
    A folder is ours when its marker's id matches the record. With no record (a first start,
    or a state folder that was reset) a marker alone is accepted only if the folder holds
    nothing but that marker and our own job folders, and the record is then written."""

    def __init__(
        self,
        root: Path,
        state_dir: Path | None = None,
        *,
        delays: tuple[float, ...] = DELETE_DELAYS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        # Absolute from the start: a later change of working directory must not move it.
        self.root = Path(os.path.abspath(root))
        self.state_dir = None if state_dir is None else Path(os.path.abspath(state_dir))
        if self.state_dir is not None:
            check_folders(self.state_dir, self.root)
        self._delays = delays
        self._sleep = sleep

    # --- the marker ---

    def _marker_id(self) -> str | None:
        """The id in the marker, if the marker is a plain file of ours (not a link)."""
        try:
            marker = self.root / MARKER
            if not stat.S_ISREG(os.lstat(marker).st_mode):
                return None
            text = marker.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        if not text.startswith(MARKER_TEXT):
            return None
        found = _MARKER_ID.fullmatch(text[len(MARKER_TEXT) :])
        return found.group(1) if found else None

    def _recorded_id(self) -> str | None:
        if self.state_dir is None:
            return None
        try:
            return (self.state_dir / RECORD).read_text(encoding="ascii").strip() or None
        except (OSError, ValueError):
            return None

    def _only_ours(self) -> bool:
        for name in self._names():
            if name == MARKER:
                continue
            try:
                info = os.lstat(self.root / name)
            except OSError:
                return False
            ours = _JOB_FOLDER.fullmatch(name) and stat.S_ISDIR(info.st_mode)
            if not ours or _is_link(info):
                return False
        return True

    def _is_marked(self) -> bool:
        try:
            if _is_link(os.lstat(self.root)):
                return False
        except OSError:
            return False
        marker = self._marker_id()
        if marker is None:
            return False
        if self.state_dir is None:
            return True
        recorded = self._recorded_id()
        if recorded is not None:
            return recorded == marker
        return self._only_ours()

    def _write_marker(self) -> None:
        marker_id = secrets.token_hex(16)
        (self.root / MARKER).write_text(f"{MARKER_TEXT}id: {marker_id}\n", encoding="utf-8")
        self._record(marker_id)

    def _record(self, marker_id: str) -> None:
        if self.state_dir is not None:
            (self.state_dir / RECORD).write_text(marker_id + "\n", encoding="ascii")

    @staticmethod
    def _is_plain(path: Path) -> bool:
        try:
            return stat.S_ISREG(os.lstat(path).st_mode)
        except OSError:
            return False

    # --- life cycle ---

    def prepare(self) -> None:
        """Create the folder (private), or take it over if it is empty or ours; then wipe
        whatever a previous run left. Raises ScratchNotOurs for a folder that holds
        someone's files, ScratchWipeFailed if something cannot be deleted (it is tried again
        at the next start) and ScratchDiskFull when the disk is full."""
        try:
            if self.state_dir is not None:
                private_folder(self.state_dir)
            private_folder(self.root)
            if _is_link(os.lstat(self.root)):
                raise ScratchNotOurs(f"the scratch folder {self.root} is a link; use a real folder")
            if self._is_marked():
                marker_id = self._marker_id()
                if marker_id and self._recorded_id() is None:
                    self._record(marker_id)
            else:
                plain_marker = self._is_plain(self.root / MARKER)
                leftovers = [n for n in self._names() if n != MARKER or not plain_marker]
                if leftovers:
                    raise ScratchNotOurs(
                        f"the scratch folder {self.root} holds files that are not the"
                        " follower's; point SWARMSCRIBE_FOLLOWER_SCRATCH_DIR at an empty folder"
                    )
                self._write_marker()
        except OSError as exc:
            raise _typed(exc, self.root, "prepare it") from None
        self.wipe()
        left = self._entries()
        if left:
            raise ScratchWipeFailed(
                f"{len(left)} item(s) in the scratch folder {self.root} could not be deleted"
                " (a file may be open in another program); close it, the follower will try"
                " again at the next start"
            )

    def _names(self) -> list[str]:
        try:
            return [entry.name for entry in self.root.iterdir()]
        except OSError:
            return []

    def _entries(self) -> list[Path]:
        return [self.root / name for name in self._names() if name != MARKER]

    def _delete(self, path: Path) -> bool:
        """Delete with a few tries, for a file another program holds for a moment."""
        for delay in (*self._delays, None):
            try:
                if _delete(_extended(path)):
                    return True
            except (OSError, ValueError):
                pass
            if delay is None:
                return False
            self._sleep(delay)
        return False

    def wipe(self) -> None:
        """Delete everything but the marker. Does nothing unless the folder is ours, so it can
        never delete a folder the follower did not make. Never raises: what cannot be deleted
        now is deleted by the next start (`prepare` reports it)."""
        if not self._is_marked():
            return
        for entry in self._entries():
            self._delete(entry)

    def job_dir(self, job_id: str) -> Path:
        """A fresh private folder for one job. The id comes from the leader and becomes a
        folder name, so it must be a UUID and nothing else."""
        folder = self.root / f"job-{uuid.UUID(job_id)}"
        self.remove(folder)
        try:
            os.mkdir(folder, 0o700)
        except OSError as exc:
            raise _typed(exc, self.root, "make the job folder") from None
        return folder

    def remove(self, entry: Path) -> None:
        """Delete a file or a folder strictly inside this scratch folder; a link is removed,
        never followed.

        Raises ScratchOutside for an empty or relative path, a `..` path, the folder itself
        or anything outside it, and ScratchNotOurs if the folder is not verifiably ours:
        nothing is deleted in any of those cases. Raises ScratchWipeFailed when, after the
        retries, something is still there; the caller logs it (ids only), and the next
        `prepare` wipes it."""
        target = self._inside(entry)
        if not self._delete(target):
            raise ScratchWipeFailed(
                f"an item in the scratch folder {self.root} could not be deleted (a file may"
                " be open in another program); it is deleted at the next start"
            )

    def _inside(self, entry: Path | str | None) -> Path:
        text = os.fspath(entry) if entry is not None else ""
        if text in ("", ".") or not os.path.isabs(text) or ".." in Path(text).parts:
            raise ScratchOutside("a path to remove must be absolute and inside the scratch folder")
        target = os.path.abspath(text)
        root = str(self.root)
        if _same(target, root) or not _within(root, target):
            raise ScratchOutside("that path is not strictly inside the scratch folder")
        # The folder holding it must really be inside, however links on the way point.
        if not _within(os.path.realpath(root), os.path.realpath(os.path.dirname(target))):
            raise ScratchOutside("that path is not strictly inside the scratch folder")
        if not self._is_marked():
            raise ScratchNotOurs(f"the scratch folder {self.root} is not verifiably the follower's")
        return Path(target)
