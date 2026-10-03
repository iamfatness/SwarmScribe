from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
