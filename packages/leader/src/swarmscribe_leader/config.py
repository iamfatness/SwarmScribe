from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Leader configuration, read from SWARMSCRIBE_* environment variables."""

    model_config = SettingsConfigDict(env_prefix="SWARMSCRIBE_", extra="ignore")

    database_url: str
    public_url: str
    link_key: str = Field(min_length=32)

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
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @model_validator(mode="after")
    def _heartbeat_inside_lease(self) -> "Settings":
        if self.heartbeat_seconds >= self.lease_seconds:
            raise ValueError("heartbeat_seconds must be shorter than lease_seconds")
        return self
