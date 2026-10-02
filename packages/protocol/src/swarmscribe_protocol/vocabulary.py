from pydantic import Field, field_validator

from .base import WireModel


class Correction(WireModel):
    """A fix applied after transcription: text heard as `heard` becomes `replacement`."""

    heard: str = Field(min_length=1)
    replacement: str = Field(min_length=1)

    @field_validator("heard", "replacement")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must contain something other than whitespace")
        return value


class Vocabulary(WireModel):
    """Words to recognise and fixes to apply. Version 0 means not assigned by a leader
    (a local run, or no vocabulary)."""

    version: int = Field(ge=0)
    terms: list[str] = Field(default_factory=list)
    corrections: list[Correction] = Field(default_factory=list)


class AppliedCorrection(WireModel):
    """A correction that fired in one recording, and how many times."""

    heard: str
    replacement: str
    count: int = Field(ge=1)
