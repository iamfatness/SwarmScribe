from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
"""Lowercase hex SHA-256, the only checksum format on the wire."""


class WireModel(BaseModel):
    """Base for everything that crosses the leader-follower wire."""

    model_config = ConfigDict(frozen=True, protected_namespaces=())
