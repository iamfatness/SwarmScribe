import asyncio
import hashlib
import os
import stat
import time
from collections.abc import AsyncIterator, Callable, Sequence
from datetime import timedelta
from pathlib import Path, PurePosixPath

from swarmscribe_protocol import Link

from .base import ObjectInfo, StorageError, StorageUnavailable
from .links import LinkClaims, LinkSigner

HASH_CHUNK_BYTES = 1024 * 1024


def version_of(stat_result: os.stat_result) -> str:
    return f"{stat_result.st_size}-{stat_result.st_mtime_ns}"


class LocalBackend:
    """A folder on a filesystem every leader replica can see. The leader serves its links."""

    def __init__(
        self,
        root: Path,
        *,
        location_id: str,
        signer: LinkSigner,
        public_url: str,
        clock: Callable[[], float] = time.time,
    ):
        self.root = Path(root).resolve()
        self.location_id = location_id
        self.signer = signer
        self.public_url = public_url.rstrip("/")
        self.clock = clock

    def _require_root(self) -> None:
        if not self.root.is_dir():
            raise StorageUnavailable(f"storage root {str(self.root)!r} is not available")
        try:
            with os.scandir(self.root):
                pass
        except OSError as exc:
            raise StorageUnavailable(
                f"storage root {str(self.root)!r} cannot be listed: {exc.strerror}"
            ) from exc

    def path_for(self, key: str) -> Path:
        if not key or key.startswith("/") or "\\" in key or ":" in key:
            raise StorageError(f"invalid storage key {key!r}")
        if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in key):
            raise StorageError(f"invalid storage key {key!r}")
        if key != PurePosixPath(key).as_posix():
            raise StorageError(f"invalid storage key {key!r}")
        for segment in key.split("/"):
            if segment in ("", ".", "..") or segment.endswith((".", " ")):
                raise StorageError(f"invalid storage key {key!r}")
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise StorageError(f"invalid storage key {key!r}")
        return path

    def file_path(self, key: str) -> Path:
        """The path for `key`, refusing a missing root and any symlink or junction on the way."""
        self._require_root()
        path = self.path_for(key)
        current = self.root
        for segment in key.split("/"):
            current = current / segment
            if self._is_link(current):
                raise StorageError(f"invalid storage key {key!r}")
        return path

    @staticmethod
    def _is_link(path: Path) -> bool:
        return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())

    async def list(self, prefix: str = "") -> AsyncIterator[ObjectInfo]:
        # The walk runs in a worker thread so a slow or large folder never blocks the loop.
        for info in await asyncio.to_thread(self._list_sync, prefix):
            yield info

    def _walk_start(self, prefix: str) -> Path:
        """The folder holding every key that starts with `prefix`. The walk begins there, so
        folders outside the input folder (a drive root's system folders, the outputs) are
        never read."""
        folder = prefix.rpartition("/")[0]
        if not folder:
            return self.root
        start = self.file_path(folder)
        if not start.is_dir():
            raise StorageUnavailable(f"input folder {folder!r} is not available")
        return start

    def _list_sync(self, prefix: str) -> Sequence[ObjectInfo]:
        """Everything under `prefix`, or an error: a directory that cannot be read makes
        the listing fail rather than come back partial (a partial listing would mark the
        unseen recordings missing and cancel their jobs)."""
        self._require_root()
        start = self._walk_start(prefix)

        def unreadable(exc: OSError) -> None:
            raise StorageUnavailable(f"a directory cannot be listed: {exc.strerror}") from exc

        found: list[ObjectInfo] = []
        for dirpath, dirnames, filenames in os.walk(start, onerror=unreadable):
            dirnames[:] = sorted(d for d in dirnames if not self._is_link(Path(dirpath) / d))
            for name in sorted(filenames):
                full = Path(dirpath) / name
                if self._is_link(full):
                    continue
                key = full.relative_to(self.root).as_posix()
                if not key.startswith(prefix):
                    continue
                try:
                    self.path_for(key)
                    st = full.stat()
                except StorageError:
                    continue
                except FileNotFoundError:
                    continue  # removed while we listed: genuinely gone
                except OSError as exc:
                    raise StorageUnavailable(f"a file cannot be read: {exc.strerror}") from exc
                found.append(ObjectInfo(key=key, size=st.st_size, version=version_of(st)))
        return found

    @staticmethod
    def _stat_file(path: Path, key: str) -> os.stat_result | None:
        """The stat of a regular file, None when there is none. Any other failure is an
        outage, never 'absent': an unreadable consent.txt must not read as 'no consent'."""
        try:
            st = path.stat()
        except (FileNotFoundError, NotADirectoryError):
            return None
        except OSError as exc:
            raise StorageUnavailable(f"cannot stat {key!r}: {exc.strerror}") from exc
        return st if stat.S_ISREG(st.st_mode) else None

    async def read_text(self, key: str) -> str | None:
        return await asyncio.to_thread(self._read_text_sync, key)

    def _read_text_sync(self, key: str) -> str | None:
        self._require_root()
        path = self.path_for(key)
        if self._stat_file(path, key) is None:
            return None
        try:
            with path.open("r", encoding="utf-8-sig") as handle:
                return handle.read()
        except UnicodeDecodeError as exc:
            raise StorageError(f"{key!r} is not valid UTF-8 text") from exc
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise StorageUnavailable(f"cannot read {key!r}: {exc.strerror}") from exc

    async def stat(self, key: str) -> ObjectInfo | None:
        return await asyncio.to_thread(self._stat_sync, key)

    def _stat_sync(self, key: str) -> ObjectInfo | None:
        self._require_root()
        path = self.path_for(key)
        st = self._stat_file(path, key)
        if st is None:
            return None
        return ObjectInfo(key=key, size=st.st_size, version=version_of(st))

    def _link(
        self,
        key: str,
        method: str,
        version: str,
        ttl: timedelta,
        *,
        job_id: str = "",
        lease_id: str = "",
    ) -> Link:
        self.path_for(key)
        claims = LinkClaims(
            location_id=self.location_id,
            key=key,
            method=method,
            version=version,
            expires=int(self.clock() + ttl.total_seconds()),
            job_id=job_id,
            lease_id=lease_id,
        )
        return Link(url=f"{self.public_url}/v1/files/{self.signer.sign(claims)}", method=method)

    def download_link(self, key: str, version: str, ttl: timedelta) -> Link:
        return self._link(key, "GET", version, ttl)

    def upload_link(
        self, key: str, ttl: timedelta, *, job_id: str = "", lease_id: str = ""
    ) -> Link:
        """A PUT link. The file route accepts it only while `lease_id` is the job's lease."""
        return self._link(key, "PUT", "", ttl, job_id=job_id, lease_id=lease_id)

    async def sha256(self, key: str) -> str | None:
        """Hex SHA-256 of the object's content, or None when there is no such object."""
        return await asyncio.to_thread(self._sha256_sync, key)

    def _sha256_sync(self, key: str) -> str | None:
        self._require_root()
        path = self.path_for(key)
        if self._stat_file(path, key) is None:
            return None
        digest = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                while chunk := handle.read(HASH_CHUNK_BYTES):
                    digest.update(chunk)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise StorageUnavailable(f"cannot read {key!r}: {exc.strerror}") from exc
        return digest.hexdigest()

    async def delete(self, key: str) -> None:
        path = self.path_for(key)
        try:
            if path.is_dir():
                raise StorageError(f"{key!r} is a directory, not an object")
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise StorageUnavailable(f"cannot delete {key!r}: {exc.strerror}") from exc
