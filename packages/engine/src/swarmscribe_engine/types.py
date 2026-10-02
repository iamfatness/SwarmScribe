from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal

Device = Literal["cuda", "cpu"]
DevicePreference = Literal["auto", "cuda", "cpu"]

LANGUAGE = "en"
DEFAULT_TEMPERATURES: tuple[float, ...] = (0.0, 0.2, 0.4)
MAX_TEMPERATURE = 0.4

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


@dataclass(frozen=True)
class TranscribeSettings:
    model: str
    compute_type: str
    device: Device
    temperatures: tuple[float, ...] = DEFAULT_TEMPERATURES

    def __post_init__(self) -> None:
        object.__setattr__(self, "temperatures", tuple(self.temperatures))
        if not self.temperatures:
            raise ValueError("temperatures must not be empty")
        if any(not (0.0 <= t <= MAX_TEMPERATURE) for t in self.temperatures):
            raise ValueError(f"temperatures must be between 0.0 and {MAX_TEMPERATURE}")


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
