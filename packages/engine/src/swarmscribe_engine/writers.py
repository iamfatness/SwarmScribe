import json
import os
from pathlib import Path

from .types import FIXED_SETTINGS, OutputFiles, Transcript, Word
from .version import ENGINE_VERSION

SCHEMA_VERSION = 1


def format_srt_time(seconds: float) -> str:
    total_ms = max(0, round(seconds * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def render_txt(transcript: Transcript) -> str:
    return "".join(f"{segment.text}\n" for segment in transcript.segments)


def render_srt(transcript: Transcript) -> str:
    blocks = [
        f"{index}\n"
        f"{format_srt_time(segment.start)} --> {format_srt_time(segment.end)}\n"
        f"{segment.text}\n"
        for index, segment in enumerate(transcript.segments, start=1)
    ]
    return "\n".join(blocks)


def _word_json(word: Word) -> dict:
    data = {
        "start": word.start,
        "end": word.end,
        "word": word.word,
        "probability": word.probability,
    }
    if word.original is not None:
        data["original"] = word.original
    return data


def render_segments_json(transcript: Transcript) -> str:
    settings = transcript.settings
    document = {
        "schema_version": SCHEMA_VERSION,
        "source_checksum": transcript.source_checksum,
        "duration": transcript.duration,
        "device": settings.device,
        "engine_version": ENGINE_VERSION,
        "settings": {
            "model": settings.model,
            "compute_type": settings.compute_type,
            **FIXED_SETTINGS,
            "temperatures": list(settings.temperatures),
        },
        "vocabulary_version": transcript.vocabulary_version,
        "vocabulary_terms_used": list(transcript.vocabulary_terms_used),
        "corrections_applied": [
            {"heard": applied.heard, "replacement": applied.replacement, "count": applied.count}
            for applied in transcript.corrections_applied
        ],
        "segments": [
            {
                "start": segment.start,
                "end": segment.end,
                "text": segment.text,
                "words": [_word_json(word) for word in segment.words],
            }
            for segment in transcript.segments
        ],
    }
    return json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def _atomic_write(path: Path, text: str) -> None:
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temp, path)


def write_outputs(transcript: Transcript, out_dir: Path) -> OutputFiles:
    """Write txt, srt, then segments.json. segments.json last marks the file done."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = OutputFiles(
        txt=out_dir / f"{transcript.source_name}.txt",
        srt=out_dir / f"{transcript.source_name}.srt",
        segments_json=out_dir / f"{transcript.source_name}.segments.json",
    )
    txt, srt, segments_json = (
        render_txt(transcript),
        render_srt(transcript),
        render_segments_json(transcript),
    )
    _atomic_write(files.txt, txt)
    _atomic_write(files.srt, srt)
    _atomic_write(files.segments_json, segments_json)
    return files
