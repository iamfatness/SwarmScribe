import hashlib
from collections.abc import Callable, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

from .types import (
    LANGUAGE,
    Segment,
    TranscribeSettings,
    Transcript,
    UndecodableAudioError,
    Word,
)

ModelFactory = Callable[[TranscribeSettings], Any]

_CHUNK = 1024 * 1024


def _default_model_factory(settings: TranscribeSettings) -> Any:
    from faster_whisper import WhisperModel

    return WhisperModel(
        settings.model, device=settings.device, compute_type=settings.compute_type
    )


def _default_decode_errors() -> tuple[type[BaseException], ...]:
    try:
        import av
    except ImportError:
        return ()
    return (av.error.FFmpegError,)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _clean_terms(terms: Sequence[str]) -> tuple[str, ...]:
    return tuple(term.strip() for term in terms if term.strip())


def build_prompt(terms: Sequence[str]) -> str | None:
    cleaned = _clean_terms(terms)
    if not cleaned:
        return None
    return "Glossary: " + ", ".join(cleaned) + "."


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


def _convert(raw: Any) -> Segment:
    words = tuple(
        Word(
            start=float(word.start),
            end=float(word.end),
            word=word.word,
            probability=_clamp(word.probability),
        )
        for word in (raw.words or ())
    )
    return Segment(start=float(raw.start), end=float(raw.end), text=raw.text.strip(), words=words)


class Transcriber:
    """Holds one loaded model and transcribes files with the fixed settings."""

    def __init__(
        self,
        settings: TranscribeSettings,
        *,
        model_factory: ModelFactory = _default_model_factory,
        decode_errors: tuple[type[BaseException], ...] | None = None,
    ) -> None:
        self.settings = settings
        self._decode_errors = _default_decode_errors() if decode_errors is None else decode_errors
        self._model = model_factory(settings)

    def transcribe(self, path: Path, glossary: Sequence[str] = ()) -> Transcript:
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(f"recording not found: {path}")
        terms = _clean_terms(glossary)
        try:
            raw_segments, info = self._model.transcribe(
                str(path),
                language=LANGUAGE,
                condition_on_previous_text=False,
                temperature=list(self.settings.temperatures),
                vad_filter=True,
                word_timestamps=True,
                initial_prompt=build_prompt(terms),
            )
            segments = tuple(
                segment for segment in (_convert(raw) for raw in raw_segments) if segment.text
            )
        except self._decode_errors as exc:
            raise UndecodableAudioError(f"cannot decode {path.name}: {exc}") from exc
        return Transcript(
            source_name=path.name,
            source_checksum=sha256_file(path),
            duration=float(info.duration),
            settings=self.settings,
            glossary=terms,
            segments=segments,
        )


@lru_cache(maxsize=1)
def _shared_transcriber(settings: TranscribeSettings) -> Transcriber:
    return Transcriber(settings)


def transcribe(
    path: Path, settings: TranscribeSettings, glossary: Sequence[str] = ()
) -> Transcript:
    """Transcribe one file, reusing the model already loaded for these settings."""
    return _shared_transcriber(settings).transcribe(path, glossary)
