import argparse
import re
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from .device import resolve_device
from .transcriber import transcribe
from .types import (
    Correction,
    DeviceChoice,
    EngineError,
    TranscribeSettings,
    Transcript,
    Vocabulary,
)
from .writers import write_outputs

TranscribeFn = Callable[[Path, TranscribeSettings, Vocabulary], Transcript]
ResolveFn = Callable[[str], DeviceChoice]


def _content_lines(path: Path) -> list[tuple[int, str]]:
    """Numbered, stripped lines of a UTF-8 text file, without blanks and # comments."""
    lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
    return [
        (number, line.strip())
        for number, line in enumerate(lines, start=1)
        if line.strip() and not line.strip().startswith("#")
    ]


def read_terms(path: Path) -> tuple[str, ...]:
    return tuple(line for _, line in _content_lines(path))


def read_corrections(path: Path) -> tuple[Correction, ...]:
    corrections: list[Correction] = []
    for number, line in _content_lines(path):
        heard, arrow, replacement = line.partition("=>")
        heard, replacement = heard.strip(), replacement.strip()
        if not arrow or not heard or not replacement:
            raise ValueError(
                f"{Path(path).name} line {number}: expected 'heard as => should be'"
            )
        if not all(re.search(r"\w", part) for part in heard.split()):
            raise ValueError(
                f"{Path(path).name} line {number}: 'heard as' must contain a word in every part"
            )
        corrections.append(Correction(heard=heard, replacement=replacement))
    return tuple(corrections)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="swarmscribe-engine",
        description="Transcribe one English recording to txt, srt and segments.json.",
    )
    parser.add_argument("file", type=Path, help="audio or video file")
    parser.add_argument("--out", type=Path, required=True, help="output directory")
    parser.add_argument(
        "--vocabulary", type=Path, help="text file, one word, name or phrase per line"
    )
    parser.add_argument(
        "--corrections", type=Path, help="text file, one 'heard as => should be' per line"
    )
    parser.add_argument("--device", choices=["auto", "cuda", "cpu"], default="auto")
    parser.add_argument("--model", help="override the model chosen for the device")
    parser.add_argument("--compute-type", help="override the compute type chosen for the device")
    return parser


def run(
    argv: Sequence[str],
    *,
    transcribe_fn: TranscribeFn = transcribe,
    resolve_fn: ResolveFn = resolve_device,
) -> int:
    args = _parser().parse_args(argv)
    try:
        choice = resolve_fn(args.device)
        settings = TranscribeSettings(
            model=args.model or choice.model,
            compute_type=args.compute_type or choice.compute_type,
            device=choice.device,
        )
        vocabulary = Vocabulary(
            terms=read_terms(args.vocabulary) if args.vocabulary else (),
            corrections=read_corrections(args.corrections) if args.corrections else (),
        )
        transcript = transcribe_fn(args.file, settings, vocabulary)
        files = write_outputs(transcript, args.out)
    except (EngineError, OSError, UnicodeDecodeError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for path in (files.txt, files.srt, files.segments_json):
        print(path)
    return 0


def main() -> None:
    sys.exit(run(sys.argv[1:]))
