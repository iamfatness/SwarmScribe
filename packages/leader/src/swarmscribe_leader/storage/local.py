import os
import time
from collections.abc import AsyncIterator, Callable
from datetime import timedelta
from pathlib import Path, PurePosixPath

from swarmscribe_protocol import Link

from .base import ObjectInfo, StorageError
from .links import LinkClaims, LinkSigner


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
            raise StorageError(f"storage root {str(self.root)!r} is not available")

    def path_for(self, key: str) -> Path:
        if not key or "\x00" in key or key.startswith("/") or "\\" in key or ":" in key:
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

    @staticmethod
    def _is_link(path: Path) -> bool:
        return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())

    async def list(self, prefix: str = "") -> AsyncIterator[ObjectInfo]:
        self._require_root()
        for dirpath, dirnames, filenames in os.walk(self.root):
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
                    continue
                yield ObjectInfo(key=key, size=st.st_size, version=version_of(st))

    async def read_text(self, key: str) -> str | None:
        self._require_root()
        path = self.path_for(key)
        if not path.is_file():
            return None
        try:
            return path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as exc:
            raise StorageError(f"{key!r} is not valid UTF-8 text") from exc

    async def stat(self, key: str) -> ObjectInfo | None:
        self._require_root()
        path = self.path_for(key)
        if not path.is_file():
            return None
        st = path.stat()
        return ObjectInfo(key=key, size=st.st_size, version=version_of(st))

    def _link(self, key: str, method: str, version: str, ttl: timedelta) -> Link:
        self.path_for(key)
        claims = LinkClaims(
            location_id=self.location_id,
            key=key,
            method=method,
            version=version,
            expires=int(self.clock() + ttl.total_seconds()),
        )
        return Link(url=f"{self.public_url}/v1/files/{self.signer.sign(claims)}", method=method)

    def download_link(self, key: str, version: str, ttl: timedelta) -> Link:
        return self._link(key, "GET", version, ttl)

    def upload_link(self, key: str, ttl: timedelta) -> Link:
        return self._link(key, "PUT", "", ttl)

    async def delete(self, key: str) -> None:
        path = self.path_for(key)
        if path.is_dir():
            raise StorageError(f"{key!r} is a directory, not an object")
        path.unlink(missing_ok=True)
