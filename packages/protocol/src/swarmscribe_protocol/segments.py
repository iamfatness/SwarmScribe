import unicodedata
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, field_validator

from .base import Sha256, WireModel
from .vocabulary import AppliedCorrection

Device = Literal["cuda", "cpu"]
ChannelMode = Literal["mono", "stereo_split", "auto"]

MAX_TEMPERATURE = 0.4
MAX_CHANNEL_LABEL_LENGTH = 40
DEFAULT_CHANNEL_LABELS: tuple[str, str] = ("Left", "Right")


def _unprintable(ch: str) -> bool:
    category = unicodedata.category(ch)
    return category.startswith("C") or category in ("Zl", "Zp")


def _check_channel_labels(labels: tuple[str, str]) -> tuple[str, str]:
    # A label prefixes transcript lines, so a line break in one would break txt and srt.
    for label in labels:
        if not 1 <= len(label) <= MAX_CHANNEL_LABEL_LENGTH:
            raise ValueError(f"channel labels must be 1 to {MAX_CHANNEL_LABEL_LENGTH} characters")
        if label != label.strip():
            raise ValueError("channel labels must not start or end with a space")
        if any(_unprintable(ch) for ch in label):
            raise ValueError("channel labels must not contain control characters or line breaks")
    if labels[0].casefold() == labels[1].casefold():
        raise ValueError("the two channel labels must differ")
    return labels


ChannelLabels = Annotated[tuple[str, str], AfterValidator(_check_channel_labels)]
"""Names of the left and right channel; they prefix transcript lines when splitting."""


class JobSettings(WireModel):
    """How a follower must transcribe. The Literal fields are fixed behaviour."""

    model: str
    compute_type: str
    language: Literal["en"] = "en"
    condition_on_previous_text: Literal[False] = False
    temperatures: tuple[float, ...] = (0.0, 0.2, 0.4)
    vad_filter: Literal[True] = True
    word_timestamps: Literal[True] = True
    channel_mode: ChannelMode = "mono"
    channel_labels: ChannelLabels = DEFAULT_CHANNEL_LABELS

    @field_validator("temperatures")
    @classmethod
    def _ladder_is_clamped(cls, value: tuple[float, ...]) -> tuple[float, ...]:
        if not value:
            raise ValueError("temperatures must not be empty")
        if any(not (0.0 <= t <= MAX_TEMPERATURE) for t in value):
            raise ValueError(f"temperatures must be between 0.0 and {MAX_TEMPERATURE}")
        return value


class Word(WireModel):
    start: float
    end: float
    word: str
    probability: float = Field(ge=0.0, le=1.0)
    original: str | None = None


class Segment(WireModel):
    start: float
    end: float
    text: str
    words: list[Word]
    channel: int | None = Field(default=None, ge=0, le=1)
    """0 left, 1 right; None when the recording was not split."""


class SegmentsDocument(WireModel):
    """The contents of <name>.segments.json."""

    schema_version: Literal[1]
    source_checksum: Sha256
    duration: float = Field(ge=0.0)
    device: Device
    engine_version: str
    settings: JobSettings
    vocabulary_version: int = Field(ge=0)
    vocabulary_terms_used: list[str]
    corrections_applied: list[AppliedCorrection]
    channel_labels: list[str] | None = None
    """The labels used when the transcript was split; None otherwise."""
    segments: list[Segment]
