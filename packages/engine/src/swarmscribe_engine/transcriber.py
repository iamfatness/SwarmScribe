import hashlib
from collections.abc import Callable, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

from .types import (
    EMPTY_VOCABULARY,
    FIXED_SETTINGS,
    Segment,
    TranscribeSettings,
    Transcript,
    UndecodableAudioError,
    Vocabulary,
    Word,
)
from .vocabulary import (
    HOTWORDS_TOKEN_BUDGET,
    apply_corrections,
    build_hotwords,
    select_bias_terms,
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

    def _select_terms(self, terms: Sequence[str]) -> tuple[str, ...]:
        tokenizer = getattr(self._model, "hf_tokenizer", None)
        if tokenizer is None:
            return select_bias_terms(terms)

        def tokens(text: str) -> int:
            # faster-whisper encodes hotwords as tokenizer.encode(" " + hotwords.strip()).
            return len(tokenizer.encode(" " + text, add_special_tokens=False).ids)

        return select_bias_terms(terms, budget=HOTWORDS_TOKEN_BUDGET, measure=tokens)

    def transcribe(self, path: Path, vocabulary: Vocabulary = EMPTY_VOCABULARY) -> Transcript:
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(f"recording not found: {path}")
        terms_used = self._select_terms(vocabulary.terms)
        try:
            raw_segments, info = self._model.transcribe(
                str(path),
                **FIXED_SETTINGS,
                temperature=list(self.settings.temperatures),
                hotwords=build_hotwords(terms_used),
            )
            segments = tuple(
                segment for segment in (_convert(raw) for raw in raw_segments) if segment.text
            )
        except self._decode_errors as exc:
            raise UndecodableAudioError(f"cannot decode {path.name}: {exc}") from exc
        segments, applied = apply_corrections(segments, vocabulary.corrections)
        return Transcript(
            source_name=path.name,
            source_checksum=sha256_file(path),
            duration=float(info.duration),
            settings=self.settings,
            vocabulary_version=vocabulary.version,
            vocabulary_terms_used=terms_used,
            corrections_applied=applied,
            segments=segments,
        )


@lru_cache(maxsize=1)
def _shared_transcriber(settings: TranscribeSettings) -> Transcriber:
    return Transcriber(settings)


def transcribe(
    path: Path, settings: TranscribeSettings, vocabulary: Vocabulary = EMPTY_VOCABULARY
) -> Transcript:
    """Transcribe one file, reusing the model already loaded for these settings."""
    return _shared_transcriber(settings).transcribe(path, vocabulary)
