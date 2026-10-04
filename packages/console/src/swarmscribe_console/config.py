"""Fleet console configuration, read from SWARMSCRIBE_CONSOLE_* environment variables.

Secrets (the database URL, the console key, the client secrets, the service-account key) are
SecretStr: never shown in repr, logs or validation errors."""

import base64
import binascii
import json
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

KEY_BYTES = 32
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
_DEFAULT_PORTS = {"http": 80, "https": 443}
_BAD_KEY = (
    "key must be 32 random bytes in URL-safe base64; make one with "
    'python -c "import secrets; print(secrets.token_urlsafe(32))"'
)
_BAD_SERVICE_ACCOUNT = (
    "google_service_account must be a service-account JSON key or the path of a file holding one"
)


def decode_key(value: str) -> bytes:
    """SWARMSCRIBE_CONSOLE_KEY: exactly 32 bytes, URL-safe base64, padding optional."""
    text = value.strip()
    try:
        raw = base64.b64decode(
            text.replace("-", "+").replace("_", "/") + "=" * (-len(text) % 4), validate=True
        )
    except (binascii.Error, ValueError):
        raise ValueError(_BAD_KEY) from None
    if len(raw) != KEY_BYTES:
        raise ValueError(_BAD_KEY)
    return raw


class Settings(BaseSettings):
    """Console configuration. The console is a confidential OIDC web client of each
    configured provider, so each needs its client secret."""

    # hide_input_in_errors: a model-level ValidationError would otherwise echo the values.
    model_config = SettingsConfigDict(
        env_prefix="SWARMSCRIBE_CONSOLE_", extra="ignore", hide_input_in_errors=True
    )

    database_url: SecretStr
    public_url: str
    key: SecretStr

    entra_tenant_id: str | None = None
    entra_client_id: str | None = None
    entra_client_secret: SecretStr | None = None
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    google_hosted_domain: str | None = None
    google_service_account: SecretStr | None = None  # JSON key, or the path of a file with it

    session_lifetime_seconds: int = Field(default=8 * 3600, gt=0)
    session_idle_seconds: int = Field(default=3600, gt=0)
    login_attempt_seconds: int = Field(default=600, gt=0)

    # The poller and the proxy (fleet console spec 5.3, 5.4).
    poll_interval_seconds: float = Field(default=15.0, gt=0)
    poll_timeout_seconds: float = Field(default=5.0, gt=0)
    poll_tick_seconds: float = Field(default=1.0, gt=0)
    poll_concurrency: int = Field(default=8, gt=0)
    unreachable_after_failures: int = Field(default=3, gt=0)
    history_hours: int = Field(default=24, gt=0, le=24 * 7)
    prune_interval_seconds: float = Field(default=3600.0, gt=0)
    proxy_timeout_seconds: float = Field(default=10.0, gt=0)

    static_dir: Path | None = None  # the built web app (C3): a folder holding index.html

    @field_validator("database_url")
    @classmethod
    def _database_url_is_set(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("database_url is empty; set the console's own database URL")
        return value

    @field_validator("static_dir", mode="before")
    @classmethod
    def _blank_static_dir_is_unset(cls, value: Any) -> Any:
        # Compose/Kubernetes pass an unset variable as an empty string.
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("static_dir")
    @classmethod
    def _static_dir_holds_the_app(cls, value: Path | None) -> Path | None:
        if value is not None and not (value / "index.html").is_file():
            raise ValueError("static_dir must be a folder holding the web app's index.html")
        return value

    @field_validator("public_url")
    @classmethod
    def _console_origin(cls, value: str) -> str:
        if not value.isascii():
            # An internationalised host name is never the origin a browser sends in Origin.
            raise ValueError("public_url must be ASCII (use the punycode form of the host)")
        parts = urlsplit(value.strip())
        local = parts.scheme == "http" and parts.hostname in _LOCAL_HOSTS
        if (parts.scheme != "https" and not local) or not parts.hostname:
            raise ValueError(
                "public_url must be the console's https:// origin, "
                "e.g. https://console.example.org"
            )
        if (
            parts.path not in ("", "/")
            or parts.query
            or parts.fragment
            or parts.username is not None
            or parts.password is not None
        ):
            raise ValueError("public_url is an origin only: scheme, host and port, no path")
        try:
            port = parts.port
        except ValueError:
            raise ValueError("public_url has an invalid port") from None
        if port == 0:
            raise ValueError("public_url has an invalid port")
        host = parts.hostname
        if host.endswith("."):
            raise ValueError("public_url's host has no trailing dot (browsers send Origin without)")
        if ":" in host:  # IPv6 literal
            host = f"[{host}]"
        if port is not None and port != _DEFAULT_PORTS[parts.scheme]:
            host = f"{host}:{port}"  # an explicit default port is dropped so Origin compares equal
        return f"{parts.scheme}://{host}"

    @field_validator("key")
    @classmethod
    def _key_is_32_bytes(cls, value: SecretStr) -> SecretStr:
        decode_key(value.get_secret_value())
        return value

    @field_validator(
        "entra_client_secret", "google_client_secret", "google_service_account", mode="before"
    )
    @classmethod
    def _blank_secret_is_unset(cls, value: Any) -> Any:
        # Compose/Kubernetes pass an unset variable as an empty string.
        if isinstance(value, str) and not value.strip():
            return None
        if isinstance(value, SecretStr) and not value.get_secret_value().strip():
            return None
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
        if value is None:
            return None
        domain = value.removeprefix("@").lower()
        if not domain:  # "@" must not collapse to "" (which a truthiness test reads as unset)
            raise ValueError("google_hosted_domain is empty; unset it or name a domain")
        return domain

    @model_validator(mode="after")
    def _sign_in_is_complete(self) -> "Settings":
        entra = (self.entra_tenant_id, self.entra_client_id, self.entra_client_secret)
        if any(v is not None for v in entra) and not all(v is not None for v in entra):
            raise ValueError(
                "entra_tenant_id, entra_client_id and entra_client_secret are set together "
                "(the console is a confidential web client)"
            )
        if bool(self.google_client_id) != (self.google_client_secret is not None):
            raise ValueError("google_client_id and google_client_secret are set together")
        if not self.google_client_id:
            for name in ("google_hosted_domain", "google_service_account"):
                if getattr(self, name) is not None:
                    raise ValueError(f"{name} needs google_client_id")
        if not self.entra_client_id and not self.google_client_id:
            raise ValueError("configure Entra ID or Google sign-in (or both)")
        if self.session_idle_seconds > self.session_lifetime_seconds:
            raise ValueError(
                "session_idle_seconds cannot be longer than session_lifetime_seconds"
            )
        if self.google_service_account is not None:
            self.google_service_account_key()
        return self

    def key_bytes(self) -> bytes:
        return decode_key(self.key.get_secret_value())

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
