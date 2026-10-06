"""The follower's credential, kept between starts (follower spec 5.3).

One JSON file in the state folder, readable by its owner only. On POSIX a file or folder
that another user owns, or that others can write to, is refused: it could have been planted.
On Windows the same rule is applied to the access control list (winacl.py): the folder and
the file may be reachable only by this account, SYSTEM and Administrators. Nothing here
prints the credential, and its repr is hidden."""

import json
import os
import secrets
import stat
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .fsutil import private_folder

REPLACE_ATTEMPTS = 20  # Windows refuses a replace while a scanner or indexer has the file open


class CredentialFileError(Exception):
    """The credential file cannot be trusted or read; the message says what to do and never
    contains the file's content."""


@dataclass(frozen=True)
class Stored:
    leader_url: str
    follower_id: str
    credential: str = field(repr=False)
    device: str
    heartbeat_interval: int
    lease_seconds: int


_TYPES = {
    "leader_url": str,
    "follower_id": str,
    "credential": str,
    "device": str,
    "heartbeat_interval": int,
    "lease_seconds": int,
}


class CredentialStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _folder_problem(self, folder: os.stat_result, *, mode_matters: bool = True) -> str | None:
        """The rule for the folder, in one place: it must be this user's, and nobody else may
        be able to write to it. None when it can be trusted; else why not, naming the folder,
        its owner and mode, and what to do."""
        where = self.path.parent
        if os.name == "nt":
            from . import winacl

            try:
                return winacl.access_problem(where, owner_only=not mode_matters)
            except OSError as exc:
                # Windows will not even say who owns it: not a folder to keep a secret in.
                return (
                    f"the folder {where} cannot be checked"
                    f" ({exc.strerror or type(exc).__name__}) and will not be trusted with the"
                    " credential; use a folder of this account's own"
                )
        me = os.getuid()
        found = f"owner uid {folder.st_uid}, mode {stat.S_IMODE(folder.st_mode):04o}"
        if folder.st_uid != me:
            return (
                f"the folder {where} is owned by another user and will not be trusted with"
                f" the credential ({found}; the follower runs as uid {me}); give it to the"
                f" follower with `chown {me} {where} && chmod 700 {where}`, or use a folder or"
                " volume that is the follower's own"
            )
        if mode_matters and stat.S_IMODE(folder.st_mode) & 0o022:
            return (
                f"the folder {where} is writable by others and will not be trusted with the"
                f" credential ({found}); run `chmod 700 {where}`"
            )
        return None

    def check_folder(self, *, tighten: bool = True) -> None:
        """Refuse, BEFORE anything is registered, a folder whose credential `load` would
        refuse at the next start: a follower that registered there would spend its join token,
        work, and after a restart never trust its own credential again. The rule is `load`'s
        own (`_folder_problem`).

        While there is no credential yet, a folder that is this user's but looser than 0700
        is tightened first, exactly as `save` would do a moment later. `tighten=False`
        (`doctor`, which changes nothing) passes such a folder instead, and a missing one:
        `run` creates it 0700. Once a credential exists nothing is tightened: a folder that
        others could write to while it held the credential is refused, as it always was."""
        folder = self.path.parent
        fresh = not os.path.lexists(self.path)
        try:
            if tighten and fresh:
                private_folder(folder)
            info = folder.stat()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise CredentialFileError(
                f"cannot use the folder {folder}: {exc.strerror or type(exc).__name__}"
            ) from None
        problem = self._folder_problem(info, mode_matters=tighten or not fresh)
        if problem is not None:
            raise CredentialFileError(problem)

    def _check_trust(self, folder: os.stat_result, file: os.stat_result, acl=None) -> None:
        """`file` is the open credential file's fstat; on Windows `acl` is its access control
        list, read through the same open file."""
        problem = self._folder_problem(folder)
        if problem is None and os.name == "nt":
            from . import winacl

            problem = winacl.access_problem(self.path, what="file", acl=acl)
        if problem is not None:
            raise CredentialFileError(problem)
        if os.name == "nt":
            return
        if file.st_uid != os.getuid():
            raise CredentialFileError(
                f"{self.path} is owned by another user and will not be trusted; delete it"
                " and join again"
            )
        if stat.S_IMODE(file.st_mode) & 0o077:
            raise CredentialFileError(
                f"{self.path} is readable by others; run `chmod 600 {self.path}`"
            )

    def _not_a_credential(self) -> CredentialFileError:
        return CredentialFileError(
            f"{self.path} is not a credential file; delete it and join again"
        )

    def _open_for_reading(self) -> int:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        for attempt in range(REPLACE_ATTEMPTS):
            try:
                return os.open(self.path, flags)
            except PermissionError:
                # Windows refuses an open while another writer's replace is in flight.
                if attempt == REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(0.05)
        raise AssertionError("unreachable")

    def load(self) -> Stored | None:
        """The stored credential, or None when there is no file. A corrupt, foreign or
        over-readable file is refused and never touched."""
        try:
            try:
                before = os.lstat(self.path)
            except FileNotFoundError:
                return None
            if not stat.S_ISREG(before.st_mode):  # a link is never followed; a folder is no file
                raise self._not_a_credential()
            # O_NOFOLLOW closes the window between the lstat and the open; the checks then run
            # on the opened descriptor, so they describe the file that is actually read.
            handle = self._open_for_reading()
            acl = None
            with os.fdopen(handle, "rb") as source:
                # Kept short: on Windows another writer cannot replace the file while this
                # one has it open. What was read is used only if the checks below pass.
                file = os.fstat(source.fileno())
                if os.name == "nt":
                    from . import winacl

                    acl = winacl.read_acl(self.path, source.fileno())
                raw = source.read()
            self._check_trust(self.path.parent.stat(), file, acl)
            data = json.loads(raw.decode("utf-8"))
        except OSError as exc:
            raise CredentialFileError(
                f"cannot read {self.path}: {exc.strerror or type(exc).__name__}"
            ) from None
        except (ValueError, RecursionError):
            raise self._not_a_credential() from None
        if not isinstance(data, dict) or any(
            type(data.get(name)) is not kind for name, kind in _TYPES.items()
        ):
            raise self._not_a_credential()
        secret = data["credential"]
        if (
            data["heartbeat_interval"] <= 0
            or data["lease_seconds"] <= 0
            or not secret
            or not (secret.isascii() and secret.isprintable() and " " not in secret)
        ):
            raise self._not_a_credential()
        return Stored(**{name: data[name] for name in _TYPES})

    def save(self, stored: Stored) -> None:
        """Write the file whole or not at all, never readable by others even for a moment.

        The content goes to a uniquely named temporary file created with O_EXCL and mode 0600
        (so two writers never share one, and a planted link is never followed), is flushed to
        disk, and replaces the credential in one atomic step. A failure leaves the previous
        credential intact."""
        folder = self.path.parent
        temp = folder / f"{self.path.name}.{os.getpid()}.{secrets.token_hex(4)}.new"
        try:
            private_folder(folder)
            handle = os.open(
                temp,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                json.dump(asdict(stored), out)
                out.flush()
                os.fsync(out.fileno())
            for attempt in range(REPLACE_ATTEMPTS):
                try:
                    os.replace(temp, self.path)
                    break
                except PermissionError:
                    if attempt == REPLACE_ATTEMPTS - 1:
                        raise
                    time.sleep(0.05)
        except OSError as exc:
            temp.unlink(missing_ok=True)
            raise CredentialFileError(
                f"cannot write {self.path}: {exc.strerror or type(exc).__name__}"
            ) from None

    def clean_stale_temp(self) -> int:
        """Delete the temporary files an interrupted `save` left beside the credential
        (`<name>.<pid>.<random>.new`). Call it only while holding the state-folder lock, when
        no other follower can be writing one. Never touches the credential itself, and a link
        or folder of that name is left alone. Returns how many were deleted."""
        deleted = 0
        try:
            names = [entry.name for entry in self.path.parent.iterdir()]
        except OSError:
            return 0
        prefix = self.path.name + "."
        for name in names:
            if not (name.startswith(prefix) and name.endswith(".new")):
                continue
            temp = self.path.parent / name
            try:
                if stat.S_ISREG(os.lstat(temp).st_mode):
                    temp.unlink()
                    deleted += 1
            except OSError:
                pass  # in use for the moment: the next start tries again
        return deleted

    def delete(self) -> bool:
        try:
            self.path.unlink()
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise CredentialFileError(
                f"cannot delete {self.path}: {exc.strerror or type(exc).__name__}"
            ) from None
        return True
