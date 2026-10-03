"""The sign-in cache: ~/.config/swarmscribe/credentials.json, readable by its owner only.

It holds ID and refresh tokens, so nothing here prints them, and their repr is hidden.
On Windows the file relies on the user-profile folder's permissions (POSIX modes do not
apply there)."""

import json
import os
import stat
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

PATH_ENV = "SWARMSCRIBE_ADMIN_CREDENTIALS"


class CredentialsFileError(Exception):
    """The credentials file cannot be trusted; the message says what to do. It never
    contains file content."""


def check_owner(file_uid: int, my_uid: int, path: Path) -> None:
    """Refuse a credentials file that another user owns (POSIX): it could have been planted."""
    if file_uid != my_uid:
        raise CredentialsFileError(
            f"{path} is owned by another user and will not be trusted; "
            "delete it and run `swarmscribe-admin login` again"
        )


def check_folder(mode: int, uid: int, my_uid: int, path: Path) -> None:
    """Refuse (POSIX) a credentials folder that another user owns or that others can write to:
    they could swap the file for one of their own."""
    if uid != my_uid:
        raise CredentialsFileError(
            f"the folder {path} is owned by another user and will not be trusted"
        )
    if mode & 0o022:
        raise CredentialsFileError(
            f"the folder {path} is writable by others and will not be trusted; "
            f"run `chmod 700 {path}`"
        )


def default_path() -> Path:
    override = os.environ.get(PATH_ENV)
    if override:
        return Path(override)
    return Path.home() / ".config" / "swarmscribe" / "credentials.json"


@dataclass(frozen=True)
class SignIn:
    leader: str
    provider: str
    client_id: str
    token_endpoint: str
    scope: str
    id_token: str = field(repr=False)
    refresh_token: str | None = field(default=None, repr=False)
    client_secret: str | None = field(default=None, repr=False)


class CredentialStore:
    def __init__(self, path: Path | None = None):
        self.path = path or default_path()

    def _check_folder(self) -> None:
        if os.name == "nt":
            return
        try:
            status = self.path.parent.stat()
        except FileNotFoundError:
            return  # we create it 0700
        except OSError as exc:
            raise CredentialsFileError(f"cannot read {self.path.parent}: {exc.strerror}") from None
        check_folder(stat.S_IMODE(status.st_mode), status.st_uid, os.getuid(), self.path.parent)

    def _read(self) -> dict[str, Any]:
        empty: dict[str, Any] = {"leaders": {}}
        self._check_folder()
        try:
            status = self.path.lstat()
        except FileNotFoundError:
            return empty  # never signed in
        except OSError as exc:
            raise CredentialsFileError(f"cannot read {self.path}: {exc.strerror}") from None
        if stat.S_ISLNK(status.st_mode):
            raise CredentialsFileError(
                f"{self.path} is a symbolic link and will not be trusted; remove it"
            )
        if not stat.S_ISREG(status.st_mode):
            raise CredentialsFileError(f"{self.path} is not a regular file; remove it")
        if os.name != "nt":
            check_owner(status.st_uid, os.getuid(), self.path)
            if stat.S_IMODE(status.st_mode) & 0o077:
                os.chmod(self.path, 0o600)
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise CredentialsFileError(f"cannot read {self.path}: {exc.strerror}") from None
        except ValueError:
            data = None
        if not isinstance(data, dict) or not isinstance(data.get("leaders"), dict):
            raise CredentialsFileError(
                f"{self.path} is corrupted and will not be trusted; "
                "delete it and run `swarmscribe-admin login` again"
            )
        return data

    def _write(self, data: dict[str, Any]) -> None:
        folder = self.path.parent
        self._check_folder()
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)  # the mode applies if created
        temp = folder / f".{self.path.name}.{os.getpid()}.tmp"
        temp.unlink(missing_ok=True)  # a stale temp of ours; O_EXCL then never follows a link
        # 0600 from creation, so the secrets are never in a world-readable file
        descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.path)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise

    def load(self, leader: str) -> SignIn | None:
        leaders = self._read()["leaders"]
        if leader not in leaders:
            return None
        entry = leaders[leader]
        return self._sign_in(leader, entry)

    def _sign_in(self, leader: str, entry: Any) -> SignIn:
        corrupt = CredentialsFileError(
            f"the sign-in for {leader} in {self.path} is corrupted and will not be trusted; "
            "run `swarmscribe-admin login` again"
        )
        if not isinstance(entry, dict) or entry.get("leader") != leader:
            raise corrupt
        try:
            sign_in = SignIn(**entry)
        except TypeError:
            raise corrupt from None
        required = (sign_in.provider, sign_in.client_id, sign_in.token_endpoint, sign_in.scope)
        optional = (sign_in.refresh_token, sign_in.client_secret)
        if not all(isinstance(v, str) for v in (sign_in.id_token, *required)):
            raise corrupt
        if not all(v is None or isinstance(v, str) for v in optional):
            raise corrupt
        return sign_in

    def save(self, sign_in: SignIn) -> None:
        data = self._read()
        data["leaders"][sign_in.leader] = asdict(sign_in)
        data["default_leader"] = sign_in.leader
        self._write(data)

    def remove(self, leader: str) -> bool:
        data = self._read()
        removed = data["leaders"].pop(leader, None) is not None
        if data.get("default_leader") == leader:
            del data["default_leader"]
        self._write(data)
        return removed

    def default_leader(self) -> str | None:
        value = self._read().get("default_leader")
        return value if isinstance(value, str) else None
