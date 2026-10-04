"""Request and response bodies of the console's own /api routes. Credentials arrive as
SecretStr so that they are never part of a repr, and no response model has a credential."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class LeaderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=200)
    base_url: str = Field(max_length=4000)
    # Any values: leaders.validate_labels judges them with fixed messages. A typed dict
    # would report a bad label under its key, and the error text must not echo label keys.
    labels: dict[str, Any] = Field(default_factory=dict)
    credential: SecretStr
    enabled: bool = True


class LeaderEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str | None = Field(default=None, max_length=4000)
    labels: dict[str, Any] | None = None
    enabled: bool | None = None
    credential: SecretStr | None = None  # only with a new base_url (see leaders.edit_leader)


class CredentialIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credential: SecretStr


class LeaderOut(BaseModel):
    name: str
    base_url: str
    labels: dict[str, str]
    enabled: bool
    added_by: str
    created_at: datetime
    credential_updated_at: datetime
    credential_updated_by: str
    credential_revoked: bool
    credential_revoked_at: datetime | None


class GrantIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["viewer", "operator", "admin"]
    scope: str = Field(max_length=400)
    principal_kind: str = Field(max_length=32)
    principal: str = Field(max_length=320)


class GrantOut(BaseModel):
    id: uuid.UUID
    role: str
    scope: str
    principal_kind: str
    principal: str
    created_by: str
    created_at: datetime


class ConsoleAdminIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    principal_kind: str = Field(max_length=32)
    principal: str = Field(max_length=320)


class ConsoleAdminOut(BaseModel):
    id: uuid.UUID
    principal_kind: str
    principal: str
    created_by: str
    created_at: datetime
