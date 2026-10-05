from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from swarmscribe_protocol import Link


@dataclass(frozen=True)
class ObjectInfo:
    key: str
    size: int
    version: str


class StorageError(Exception):
    """A storage request that cannot be carried out (bad key, unsupported backend)."""


class StorageUnavailable(StorageError):
    """The storage location itself cannot be reached (missing root, unsupported backend)."""


class StorageBackend(Protocol):
    def list(self, prefix: str = "") -> AsyncIterator[ObjectInfo]: ...

    async def read_text(self, key: str) -> str | None: ...

    async def stat(self, key: str) -> ObjectInfo | None: ...

    def download_link(
        self, key: str, version: str, ttl: timedelta, *, job_id: str = "", lease_id: str = ""
    ) -> Link: ...

    def upload_link(
        self, key: str, ttl: timedelta, *, job_id: str = "", lease_id: str = ""
    ) -> Link: ...

    async def sha256(self, key: str) -> str | None: ...

    async def delete(self, key: str) -> None: ...
