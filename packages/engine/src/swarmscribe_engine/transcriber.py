import gc
import hashlib
from collections.abc import Callable, Sequence
from dataclasses import replace
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
ChannelCounter = Callable[[Path], int]
StereoDecoder = Callable[[Path], tuple[Any, Any]]
Progress = Callable[[float], None]
"""Called after every segment with the fraction of the recording done, 0.0 to 1.0. Whatever
it raises stops the transcription and reaches the caller unchanged."""


class _FromProgress(BaseException):
    """Carries what a progress callback raised past the decoder's error handling, so that it
    is never mistaken for undecodable audio."""

    def __init__(self, error: BaseException) -> None:
        super().__init__()
        self.error = error


def _report(progress: Progress, fraction: float) -> None:
    try:
        progress(min(1.0, max(0.0, fraction)))
    except BaseException as exc:
        raise _FromProgress(exc) from None

SAMPLE_RATE = 16000  # what Whisper models take, and what faster-whisper resamples to
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


def _default_channel_count(path: Path) -> int:
    """Channels of the first audio stream (the one the decoder reads); 0 when there is none."""
    import av

    with av.open(str(path)) as container:
        streams = container.streams.audio
        return streams[0].codec_context.layout.nb_channels if streams else 0


def _default_decode_stereo(path: Path) -> tuple[Any, Any]:
    """The left and right channels as two separate 16 kHz float arrays."""
    from faster_whisper import decode_audio

    return decode_audio(str(path), sampling_rate=SAMPLE_RATE, split_stereo=True)


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


def _not_stereo(name: str, channels: int) -> UndecodableAudioError:
    if channels == 0:
        found = "no audio stream"
    else:
        found = f"{channels} audio channel" + ("" if channels == 1 else "s")
    return UndecodableAudioError(
        f"{name} has {found}; stereo-split (stereo_split) needs a two-channel (stereo) recording"
    )


def _merge(left: Sequence[Segment], right: Sequence[Segment]) -> tuple[Segment, ...]:
    """Both channels in one list by start time; on a tie the left channel comes first."""
    tagged = [replace(s, channel=0) for s in left] + [replace(s, channel=1) for s in right]
    return tuple(sorted(tagged, key=lambda segment: (segment.start, segment.channel)))


def _model_settings(settings: TranscribeSettings) -> TranscribeSettings:
    """Only what loading a model depends on."""
    return TranscribeSettings(
        model=settings.model, compute_type=settings.compute_type, device=settings.device
    )


class Transcriber:
    """Holds one loaded model and transcribes files with the fixed settings."""

    def __init__(
        self,
        settings: TranscribeSettings,
        *,
        model_factory: ModelFactory = _default_model_factory,
        decode_errors: tuple[type[BaseException], ...] | None = None,
        channel_count: ChannelCounter = _default_channel_count,
        decode_stereo: StereoDecoder = _default_decode_stereo,
    ) -> None:
        self.settings = settings
        self._decode_errors = _default_decode_errors() if decode_errors is None else decode_errors
        self._channel_count = channel_count
        self._decode_stereo = decode_stereo
        self._model = model_factory(settings)

    def _select_terms(self, terms: Sequence[str]) -> tuple[str, ...]:
        tokenizer = getattr(self._model, "hf_tokenizer", None)
        if tokenizer is None:
            return select_bias_terms(terms)

        def tokens(text: str) -> int:
            # faster-whisper encodes hotwords as tokenizer.encode(" " + hotwords.strip()).
            return len(tokenizer.encode(" " + text, add_special_tokens=False).ids)

        return select_bias_terms(terms, budget=HOTWORDS_TOKEN_BUDGET, measure=tokens)

    def _settings_for(self, settings: TranscribeSettings | None) -> TranscribeSettings:
        if settings is None:
            return self.settings
        if _model_settings(settings) != _model_settings(self.settings):
            raise ValueError("these settings need a different model from the one loaded")
        return settings

    def _splits(self, path: Path, settings: TranscribeSettings) -> bool:
        if settings.channel_mode == "mono":
            return False
        channels = self._channel_count(path)
        if settings.channel_mode == "auto":
            if channels == 0:
                raise UndecodableAudioError(f"{path.name} has no audio stream")
            return channels == 2
        if channels != 2:
            raise _not_stereo(path.name, channels)
        return True

    def _pass(
        self,
        audio: Any,
        settings: TranscribeSettings,
        hotwords: str | None,
        progress: Progress | None = None,
        *,
        start: float = 0.0,
        share: float = 1.0,
    ) -> tuple[list[Segment], float]:
        """One run of the model. Progress is reported as `start` plus this run's `share` of
        the whole (a split recording is two runs of half each)."""
        raw_segments, info = self._model.transcribe(
            audio,
            **FIXED_SETTINGS,
            temperature=list(settings.temperatures),
            hotwords=hotwords,
        )
        duration = float(info.duration)
        segments: list[Segment] = []
        for raw in raw_segments:
            segment = _convert(raw)
            if segment.text:
                segments.append(segment)
            if progress is not None:
                done = segment.end / duration if duration > 0 else 0.0
                _report(progress, start + share * min(1.0, max(0.0, done)))
        return segments, duration

    def _model_or_closed(self) -> Any:
        if self._model is None:
            raise RuntimeError("this transcriber is closed")
        return self._model

    def warm_up(self) -> None:
        """Run one second of silence through the model, with the VAD filter off so that the
        model really runs. GPU libraries are loaded at the first inference, not when the
        model is loaded: this is where a missing one shows."""
        import numpy

        silence = numpy.zeros(SAMPLE_RATE, dtype=numpy.float32)
        segments, _info = self._model_or_closed().transcribe(
            silence,
            language=FIXED_SETTINGS["language"],
            condition_on_previous_text=False,
            vad_filter=False,
            word_timestamps=False,
            temperature=0.0,
        )
        for _segment in segments:
            pass

    def close(self) -> None:
        """Drop the model so that its memory is returned before another one is loaded.
        The transcriber cannot be used afterwards."""
        self._model = None
        gc.collect()

    def transcribe(
        self,
        path: Path,
        vocabulary: Vocabulary = EMPTY_VOCABULARY,
        *,
        settings: TranscribeSettings | None = None,
        progress: Progress | None = None,
    ) -> Transcript:
        """Transcribe one file. `settings` may set this call's channel handling and temperature
        ladder; its model, compute type and device must be the loaded ones. `progress` is
        called after every segment with the fraction done; raising from it stops the
        transcription, and the exception reaches the caller as it was raised."""
        self._model_or_closed()
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(f"recording not found: {path}")
        settings = self._settings_for(settings)
        terms_used = self._select_terms(vocabulary.terms)
        hotwords = build_hotwords(terms_used)
        labels = None
        try:
            if self._splits(path, settings):
                left, right = self._decode_stereo(path)
                left_segments, duration = self._pass(
                    left, settings, hotwords, progress, share=0.5
                )
                right_segments, _ = self._pass(
                    right, settings, hotwords, progress, start=0.5, share=0.5
                )
                segments = _merge(left_segments, right_segments)
                labels = settings.channel_labels
            else:
                segments, duration = self._pass(str(path), settings, hotwords, progress)
            if progress is not None:
                _report(progress, 1.0)
        except _FromProgress as stopped:
            raise stopped.error from None
        except self._decode_errors as exc:
            raise UndecodableAudioError(f"cannot decode {path.name}: {exc}") from exc
        # Corrections never span a segment, so one pass over the merged list equals one per
        # channel, and each correction's count is summed across the channels.
        segments, applied = apply_corrections(segments, vocabulary.corrections)
        return Transcript(
            source_name=path.name,
            source_checksum=sha256_file(path),
            duration=duration,
            settings=settings,
            vocabulary_version=vocabulary.version,
            vocabulary_terms_used=terms_used,
            corrections_applied=applied,
            segments=segments,
            channel_labels=labels,
        )


@lru_cache(maxsize=1)
def _shared_transcriber(model_settings: TranscribeSettings) -> Transcriber:
    # Looked up at call time, not bound as defaults, so tests can substitute them.
    return Transcriber(
        model_settings,
        model_factory=_default_model_factory,
        channel_count=_default_channel_count,
        decode_stereo=_default_decode_stereo,
    )


def transcribe(
    path: Path, settings: TranscribeSettings, vocabulary: Vocabulary = EMPTY_VOCABULARY
) -> Transcript:
    """Transcribe one file, reusing the model already loaded for this model, compute type and
    device whatever the channel handling or temperature ladder."""
    return _shared_transcriber(_model_settings(settings)).transcribe(
        path, vocabulary, settings=settings
    )
