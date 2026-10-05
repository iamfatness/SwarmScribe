"""The follower's credential, kept between starts (follower spec 5.3).

One JSON file in the state folder, readable by its owner only. On POSIX a file or folder
that another user owns, or that others can write to, is refused: it could have been planted.
On Windows the folder relies on the account's profile permissions (POSIX modes do not apply
there), as the admin CLI's sign-in cache does. Nothing here prints the credential, and its
repr is hidden.

On Windows nothing here restricts the file beyond the folder's inherited ACL (by default
the user's profile: the user, SYSTEM and administrators); the 0600 mode is ignored there."""

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

    def _check_trust(self, folder: os.stat_result, file: os.stat_result) -> None:
        if os.name == "nt":
            return
        me = os.getuid()
        if folder.st_uid != me:
            raise CredentialFileError(
                f"the folder {self.path.parent} is owned by another user and will not be trusted"
            )
        if stat.S_IMODE(folder.st_mode) & 0o022:
            raise CredentialFileError(
                f"the folder {self.path.parent} is writable by others and will not be trusted;"
                f" run `chmod 700 {self.path.parent}`"
            )
        if file.st_uid != me:
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
            with os.fdopen(handle, "rb") as source:
                self._check_trust(self.path.parent.stat(), os.fstat(source.fileno()))
                raw = source.read()
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
