import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal

Device = Literal["cuda", "cpu"]
DevicePreference = Literal["auto", "cuda", "cpu"]

LANGUAGE = "en"
DEFAULT_TEMPERATURES: tuple[float, ...] = (0.0, 0.2, 0.4)
MAX_TEMPERATURE = 0.4
ChannelMode = Literal["mono", "stereo_split", "auto"]
CHANNEL_MODES: tuple[str, ...] = ("mono", "stereo_split", "auto")
DEFAULT_CHANNEL_LABELS: tuple[str, str] = ("Left", "Right")
MAX_CHANNEL_LABEL_LENGTH = 40

# Passed to the model on every call and recorded in segments.json. Not configurable.
FIXED_SETTINGS = MappingProxyType(
    {
        "language": LANGUAGE,
        "condition_on_previous_text": False,
        "vad_filter": True,
        "word_timestamps": True,
    }
)


class EngineError(Exception):
    """Base class for every error the engine raises on purpose."""


class UndecodableAudioError(EngineError):
    """The file is not audio the decoder can read. Retrying will not help."""


class DeviceUnavailableError(EngineError):
    """A device was requested that this machine does not have."""


def _unprintable(ch: str) -> bool:
    category = unicodedata.category(ch)
    return category.startswith("C") or category in ("Zl", "Zp")


def check_channel_labels(labels: Sequence[str]) -> tuple[str, str]:
    """The two channel names, checked by the same rules as the protocol's JobSettings."""
    if isinstance(labels, str):
        raise ValueError("channel_labels must be exactly two names")
    labels = tuple(labels)
    if len(labels) != 2 or not all(isinstance(label, str) for label in labels):
        raise ValueError("channel_labels must be exactly two names")
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


@dataclass(frozen=True)
class TranscribeSettings:
    model: str
    compute_type: str
    device: Device
    temperatures: tuple[float, ...] = DEFAULT_TEMPERATURES
    channel_mode: ChannelMode = "mono"
    channel_labels: tuple[str, ...] = DEFAULT_CHANNEL_LABELS

    def __post_init__(self) -> None:
        object.__setattr__(self, "temperatures", tuple(self.temperatures))
        if not self.temperatures:
            raise ValueError("temperatures must not be empty")
        if any(not (0.0 <= t <= MAX_TEMPERATURE) for t in self.temperatures):
            raise ValueError(f"temperatures must be between 0.0 and {MAX_TEMPERATURE}")
        if self.channel_mode not in CHANNEL_MODES:
            raise ValueError(f"channel_mode must be one of: {', '.join(CHANNEL_MODES)}")
        object.__setattr__(self, "channel_labels", check_channel_labels(self.channel_labels))


@dataclass(frozen=True)
class Word:
    start: float
    end: float
    word: str
    probability: float
    original: str | None = None


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str
    words: tuple[Word, ...]
    channel: int | None = None  # 0 left, 1 right; None when the recording was not split


@dataclass(frozen=True)
class Correction:
    heard: str
    replacement: str


@dataclass(frozen=True)
class Vocabulary:
    """Words to recognise and fixes to apply. Version 0 means not assigned by a leader
    (a local run, or no vocabulary)."""

    version: int = 0
    terms: tuple[str, ...] = ()
    corrections: tuple[Correction, ...] = ()


EMPTY_VOCABULARY = Vocabulary()


@dataclass(frozen=True)
class AppliedCorrection:
    heard: str
    replacement: str
    count: int


@dataclass(frozen=True)
class Transcript:
    source_name: str
    source_checksum: str
    duration: float
    settings: TranscribeSettings
    vocabulary_version: int
    vocabulary_terms_used: tuple[str, ...]
    corrections_applied: tuple[AppliedCorrection, ...]
    segments: tuple[Segment, ...]
    channel_labels: tuple[str, ...] | None = None  # set only when the recording was split


@dataclass(frozen=True)
class OutputFiles:
    txt: Path
    srt: Path
    segments_json: Path


@dataclass(frozen=True)
class DeviceChoice:
    device: Device
    model: str
    compute_type: str
