import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from .device import resolve_device
from .transcriber import transcribe
from .types import DeviceChoice, EngineError, TranscribeSettings, Transcript
from .writers import write_outputs

TranscribeFn = Callable[[Path, TranscribeSettings, Sequence[str]], Transcript]
ResolveFn = Callable[[str], DeviceChoice]


def read_glossary(path: Path) -> tuple[str, ...]:
    lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
    return tuple(
        line.strip() for line in lines if line.strip() and not line.strip().startswith("#")
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="swarmscribe-engine",
        description="Transcribe one English recording to txt, srt and segments.json.",
    )
    parser.add_argument("file", type=Path, help="audio or video file")
    parser.add_argument("--out", type=Path, required=True, help="output directory")
    parser.add_argument("--glossary", type=Path, help="text file, one name or term per line")
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
        glossary = read_glossary(args.glossary) if args.glossary else ()
        transcript = transcribe_fn(args.file, settings, glossary)
        files = write_outputs(transcript, args.out)
    except (EngineError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for path in (files.txt, files.srt, files.segments_json):
        print(path)
    return 0


def main() -> None:
    sys.exit(run(sys.argv[1:]))
