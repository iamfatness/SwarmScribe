"""Request and response bodies of the /v1/admin API."""

from typing import Literal

from pydantic import BaseModel

JobState = Literal["queued", "leased", "completed", "failed", "cancelled"]
FollowerState = Literal["active", "draining", "revoked", "gone"]
RequiredDevice = Literal["any", "cuda", "cpu"]
NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$"


class WhoAmI(BaseModel):
    provider: str
    issuer: str
    subject: str
    email: str | None
    role: str


class LoginProvider(BaseModel):
    name: str
    client_id: str
    device_authorization_endpoint: str
    token_endpoint: str
    scope: str
    client_secret: str | None = None


class LoginConfig(BaseModel):
    providers: list[LoginProvider]
