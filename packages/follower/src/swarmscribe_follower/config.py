import os
import re
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from swarmscribe_protocol import MODEL_NAME_MAX_LENGTH, MODEL_NAME_PATTERN

from .scratch import ScratchError, check_folders

NameList = Annotated[tuple[str, ...], NoDecode]
"""Comma-separated in the environment, e.g. `large-v3, distil-large-v3`."""


def default_state_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "swarmscribe-follower"
    return Path.home() / ".local" / "share" / "swarmscribe-follower"


class Settings(BaseSettings):
    """Follower configuration. SWARMSCRIBE_LEADER_URL, SWARMSCRIBE_JOIN_TOKEN[_FILE] and
    SWARMSCRIBE_LEADER_CA_FILE keep the names the master spec's `docker run` line uses;
    everything else is SWARMSCRIBE_FOLLOWER_*."""

    # hide_input_in_errors: a validation error must never echo the join token.
    model_config = SettingsConfigDict(
        env_prefix="SWARMSCRIBE_FOLLOWER_",
        extra="ignore",
        hide_input_in_errors=True,
        populate_by_name=True,
    )

    leader_url: str = Field(validation_alias=AliasChoices("SWARMSCRIBE_LEADER_URL"))
    join_token: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("SWARMSCRIBE_JOIN_TOKEN")
    )
    join_token_file: Path | None = Field(
        default=None,
        validation_alias=AliasChoices("SWARMSCRIBE_JOIN_TOKEN_FILE"),
    )
    leader_ca_file: Path | None = Field(
        default=None, validation_alias=AliasChoices("SWARMSCRIBE_LEADER_CA_FILE")
    )
    allow_http: bool = False

    pool: str = Field(default="default", max_length=100)
    device: Literal["auto", "cuda", "cpu"] = "auto"
    state_dir: Path = Field(default_factory=default_state_dir)
    scratch_dir: Path | None = None
    model_dir: Path | None = None
    offline: bool = False
    allowed_models: NameList = ()
    # The model loaded at start-up, before the follower registers (spec 5.2 step 5). Unset: the
    # device's default. An image that bakes one model sets it to that one.
    startup_model: str | None = None
    shutdown_grace_seconds: float = Field(default=8.0, ge=0)
    on_drained: Literal["exit", "park"] = "exit"
    log_format: Literal["json", "text"] = "json"

    @field_validator(
        "join_token",
        "join_token_file",
        "leader_ca_file",
        "scratch_dir",
        "model_dir",
        "startup_model",
        mode="before",
    )
    @classmethod
    def _blank_is_unset(cls, value: Any) -> Any:
        # Compose and Kubernetes pass an unset variable as an empty string.
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("allowed_models", mode="before")
    @classmethod
    def _name_list(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.split(",")
        if isinstance(value, list | tuple):
            return tuple(name for name in (str(item).strip() for item in value) if name)
        return value

    @field_validator("leader_url")
    @classmethod
    def _absolute_http_url(cls, value: str) -> str:
        parts = urlsplit(value.strip())
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("leader_url must be an absolute URL, e.g. https://leader.example.org")
        if parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("leader_url must not carry credentials, a query or a fragment")
        return value.strip().rstrip("/")

    @field_validator("startup_model")
    @classmethod
    def _a_plain_model_name(cls, value: str | None) -> str | None:
        # The protocol's rule for a model name, the same one the leader applies to a profile:
        # a name or owner/name, never a path.
        if value is None:
            return None
        value = value.strip()
        if len(value) > MODEL_NAME_MAX_LENGTH or not re.fullmatch(MODEL_NAME_PATTERN, value):
            raise ValueError("startup_model must be a model name or owner/name, never a path")
        return value

    @model_validator(mode="after")
    def _startup_model_is_allowed(self) -> "Settings":
        if (
            self.startup_model is not None
            and self.allowed_models
            and self.startup_model not in self.allowed_models
        ):
            raise ValueError(
                "startup_model is not in SWARMSCRIBE_FOLLOWER_ALLOWED_MODELS; add it there"
                " or choose one of them"
            )
        return self

    @model_validator(mode="after")
    def _https_unless_allowed(self) -> "Settings":
        # One rule for the leader and for the links it hands out (Links takes the same
        # switch): plain http, loopback included, only on request.
        if urlsplit(self.leader_url).scheme == "http" and not self.allow_http:
            raise ValueError(
                "leader_url must be https; plain http is refused unless"
                " SWARMSCRIBE_FOLLOWER_ALLOW_HTTP=1 is set (development only)"
            )
        return self

    @model_validator(mode="after")
    def _scratch_cannot_reach_state_or_models(self) -> "Settings":
        try:
            check_folders(self.state_dir, self.scratch, self.model_dir)
        except ScratchError as exc:
            raise ValueError(str(exc)) from None
        return self

    @property
    def scratch(self) -> Path:
        return self.scratch_dir or self.state_dir / "scratch"

    @property
    def credential_file(self) -> Path:
        return self.state_dir / "credential.json"

    def token(self) -> str | None:
        """The join token: the file's content if a file is configured, else the variable.
        Read only when a registration needs it."""
        if self.join_token_file is not None:
            try:
                text = self.join_token_file.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise ValueError(
                    f"the join token file cannot be read: {exc.strerror or type(exc).__name__}"
                ) from None
            return text or None
        if self.join_token is not None:
            return self.join_token.get_secret_value().strip() or None
        return None
