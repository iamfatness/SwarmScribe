import json
import uuid
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

ROLES = ("viewer", "operator", "admin")
_ROLE_KINDS = ("entra_groups", "google_groups", "emails", "domains")
_ROLE_LISTS = tuple(f"role_{role}_{kind}" for role in ROLES for kind in _ROLE_KINDS)
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
_BAD_SERVICE_ACCOUNT = (
    "google_service_account must be a service-account JSON key or the path of a file holding one"
)

NameList = Annotated[tuple[str, ...], NoDecode]
"""Comma-separated in the environment, e.g. `a@example.org, b@example.org`."""


class Settings(BaseSettings):
    """Leader configuration, read from SWARMSCRIBE_* environment variables."""

    model_config = SettingsConfigDict(env_prefix="SWARMSCRIBE_", extra="ignore")

    # Secrets: never shown in repr, logs or validation errors; use .get_secret_value().
    database_url: SecretStr
    public_url: str
    link_key: SecretStr

    lease_seconds: int = Field(default=120, gt=0)
    heartbeat_seconds: int = Field(default=30, gt=0)
    max_attempts: int = Field(default=3, gt=0)
    claim_retry_after: int = Field(default=10, gt=0)
    reaper_interval_seconds: float = Field(default=15.0, gt=0)
    scanner_interval_seconds: float = Field(default=30.0, gt=0)
    follower_gone_after_seconds: int = Field(default=600, gt=0)
    download_link_ttl_seconds: int = Field(default=1800, gt=0)
    upload_link_ttl_seconds: int = Field(default=7200, gt=0)

    # Administrators' sign-in. Either provider, or both, may be configured.
    entra_tenant_id: str | None = None
    entra_client_id: str | None = None
    entra_client_secret: SecretStr | None = None  # only for Microsoft Graph on group overage
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    google_hosted_domain: str | None = None
    google_service_account: SecretStr | None = None  # JSON key, or the path of a file with it
    role_cache_seconds: int = Field(default=300, gt=0)

    role_viewer_entra_groups: NameList = ()
    role_viewer_google_groups: NameList = ()
    role_viewer_emails: NameList = ()
    role_viewer_domains: NameList = ()
    role_operator_entra_groups: NameList = ()
    role_operator_google_groups: NameList = ()
    role_operator_emails: NameList = ()
    role_operator_domains: NameList = ()
    role_admin_entra_groups: NameList = ()
    role_admin_google_groups: NameList = ()
    role_admin_emails: NameList = ()
    role_admin_domains: NameList = ()

    @field_validator("public_url")
    @classmethod
    def _absolute_http_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError("public_url must be an absolute http(s) URL, e.g. https://leader")
        return value.rstrip("/")

    @field_validator("link_key")
    @classmethod
    def _long_enough(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("link_key must be at least 32 characters")
        return value

    @field_validator(*_ROLE_LISTS, mode="before")
    @classmethod
    def _name_list(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.split(",")
        if isinstance(value, list | tuple):
            names = (str(item).strip().lower().removeprefix("@") for item in value)
            return tuple(name for name in names if name)
        return value

    @field_validator("entra_tenant_id")
    @classmethod
    def _tenant_is_an_id(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        try:
            return str(uuid.UUID(value.strip()))
        except ValueError as exc:
            raise ValueError(
                "entra_tenant_id must be the tenant's ID (a GUID), not its domain name"
            ) from exc

    @field_validator("entra_client_id", "google_client_id", "google_hosted_domain")
    @classmethod
    def _blank_is_unset(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return value.strip()

    @field_validator("google_hosted_domain")
    @classmethod
    def _domain_lowercase(cls, value: str | None) -> str | None:
        return value.lower() if value else value

    @model_validator(mode="after")
    def _heartbeat_inside_lease(self) -> "Settings":
        if self.heartbeat_seconds >= self.lease_seconds:
            raise ValueError("heartbeat_seconds must be shorter than lease_seconds")
        return self

    @model_validator(mode="after")
    def _gone_only_after_a_lease(self) -> "Settings":
        if self.follower_gone_after_seconds <= self.lease_seconds:
            raise ValueError("follower_gone_after_seconds must be longer than lease_seconds")
        return self

    @model_validator(mode="after")
    def _sign_in_is_complete(self) -> "Settings":
        if bool(self.entra_client_id) != bool(self.entra_tenant_id):
            raise ValueError("entra_client_id and entra_tenant_id must be set together")
        if self.entra_client_secret is not None and not self.entra_client_id:
            raise ValueError("entra_client_secret needs entra_client_id")
        if self.google_client_id and self.google_client_secret is None:
            raise ValueError(
                "google_client_secret is required with google_client_id "
                "(the admin CLI's device sign-in sends it)"
            )
        extras = (self.google_client_secret, self.google_hosted_domain, self.google_service_account)
        if not self.google_client_id and any(value is not None for value in extras):
            raise ValueError("the google_* settings need google_client_id")
        for role in ROLES:
            if getattr(self, f"role_{role}_entra_groups") and not self.entra_client_id:
                raise ValueError(f"role_{role}_entra_groups needs Entra ID sign-in")
            if getattr(self, f"role_{role}_google_groups") and self.google_service_account is None:
                raise ValueError(
                    f"role_{role}_google_groups needs google_service_account "
                    "to read Google Groups"
                )
            for kind in ("emails", "domains"):
                if getattr(self, f"role_{role}_{kind}") and not self.google_client_id:
                    raise ValueError(
                        f"role_{role}_{kind} applies to Google sign-in; set google_client_id"
                    )
        if self.google_service_account is not None:
            self.google_service_account_key()
        return self

    def google_service_account_key(self) -> dict[str, str] | None:
        """The service account's JSON key: the setting holds the JSON itself, or the path of
        a mounted file containing it. Error messages never include the key."""
        if self.google_service_account is None:
            return None
        raw = self.google_service_account.get_secret_value().strip()
        try:
            if not raw.startswith("{"):
                raw = Path(raw).read_text(encoding="utf-8")
            key = json.loads(raw)
        except (OSError, ValueError):
            raise ValueError(_BAD_SERVICE_ACCOUNT) from None
        if not isinstance(key, dict) or not all(
            isinstance(key.get(field), str) and key.get(field)
            for field in ("client_email", "private_key")
        ):
            raise ValueError(_BAD_SERVICE_ACCOUNT)
        key.setdefault("token_uri", GOOGLE_TOKEN_URI)
        return key
