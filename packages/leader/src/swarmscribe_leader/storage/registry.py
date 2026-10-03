from pathlib import Path

from ..db.models import StorageLocation
from .base import StorageBackend, StorageError
from .links import LinkSigner
from .local import LocalBackend


def backend_for(
    location: StorageLocation, *, signer: LinkSigner, public_url: str
) -> StorageBackend:
    if location.backend == "local":
        root = (location.config or {}).get("root")
        if not root:
            raise StorageError("local storage location has no 'root' configured")
        return LocalBackend(
            Path(root),
            location_id=str(location.id),
            signer=signer,
            public_url=public_url,
        )
    raise StorageError(f"storage backend {location.backend!r} is not available yet")
