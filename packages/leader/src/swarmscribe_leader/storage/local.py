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

    def path_for(self, key: str) -> Path:
        if (
            not key
            or key.startswith("/")
            or "\\" in key
            or ".." in PurePosixPath(key).parts
        ):
            raise StorageError(f"invalid storage key {key!r}")
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise StorageError(f"invalid storage key {key!r}")
        return path

    async def list(self, prefix: str = "") -> AsyncIterator[ObjectInfo]:
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames.sort()
            for name in sorted(filenames):
                full = Path(dirpath) / name
                key = full.relative_to(self.root).as_posix()
                if key.startswith(prefix):
                    st = full.stat()
                    yield ObjectInfo(key=key, size=st.st_size, version=version_of(st))

    async def read_text(self, key: str) -> str | None:
        path = self.path_for(key)
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8-sig")

    async def stat(self, key: str) -> ObjectInfo | None:
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
        self.path_for(key).unlink(missing_ok=True)
