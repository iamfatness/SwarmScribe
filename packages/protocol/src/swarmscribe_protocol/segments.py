from typing import Literal

from pydantic import Field, field_validator

from .base import WireModel

Device = Literal["cuda", "cpu"]

MAX_TEMPERATURE = 0.4


class JobSettings(WireModel):
    """How a follower must transcribe. The Literal fields are fixed behaviour."""

    model: str
    compute_type: str
    language: Literal["en"] = "en"
    condition_on_previous_text: Literal[False] = False
    temperatures: tuple[float, ...] = (0.0, 0.2, 0.4)
    vad_filter: Literal[True] = True
    word_timestamps: Literal[True] = True

    @field_validator("temperatures")
    @classmethod
    def _ladder_is_clamped(cls, value: tuple[float, ...]) -> tuple[float, ...]:
        if not value:
            raise ValueError("temperatures must not be empty")
        if any(t < 0.0 or t > MAX_TEMPERATURE for t in value):
            raise ValueError(f"temperatures must be between 0.0 and {MAX_TEMPERATURE}")
        return value


class Word(WireModel):
    start: float
    end: float
    word: str
    probability: float = Field(ge=0.0, le=1.0)


class Segment(WireModel):
    start: float
    end: float
    text: str
    words: list[Word]


class SegmentsDocument(WireModel):
    """The contents of <name>.segments.json."""

    schema_version: Literal[1]
    source_checksum: str
    duration: float = Field(ge=0.0)
    device: Device
    engine_version: str
    settings: JobSettings
    glossary: list[str]
    segments: list[Segment]
