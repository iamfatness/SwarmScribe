import re
from pathlib import PurePosixPath

AUDIO_EXTENSIONS = frozenset(
    {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".wma",
     ".mp4", ".m4v", ".mov", ".mkv", ".webm"}
)


def is_recording(key: str) -> bool:
    return PurePosixPath(key).suffix.lower() in AUDIO_EXTENSIONS


def parse_consent(text: str | None) -> tuple[str, ...]:
    if not text:
        return ()
    if text.startswith("﻿"):
        text = text[1:]
    return tuple(
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    )


class ConsentFileError(ValueError):
    """consent.txt holds a pattern that cannot be used, or is too large to read."""


CONSENT_FILE = "consent.txt"
MAX_CONSENT_BYTES = 1024 * 1024
CONSENT_TOO_LARGE = "consent.txt is larger than 1 MiB"


def _class_end(pattern: str, start: int) -> int:
    """Index of the `]` closing the class opened at `start`, or -1 (then `[` is literal)."""
    j = start + 1
    if j < len(pattern) and pattern[j] == "!":
        j += 1
    if j < len(pattern) and pattern[j] == "]":
        j += 1
    return pattern.find("]", j)


def _translate_class(body: str) -> str:
    """fnmatch semantics: `!` negates, `^` is literal, and a class never matches `/`."""
    negated = body.startswith("!")
    if negated:
        body = body[1:]
    for char in ("\\", "[", "]", "^", "&", "~", "|"):
        body = body.replace(char, "\\" + char)
    return "(?!/)[" + ("^" if negated else "") + body + "]"


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Glob with `/` as separator: `*` and `?` stay inside one path part, `**` crosses parts."""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        elif pattern[i] == "[" and (end := _class_end(pattern, i)) != -1:
            out.append(_translate_class(pattern[i + 1 : end]))
            i = end + 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out))


def compile_consent(text: str | None) -> tuple[tuple[str, re.Pattern[str]], ...]:
    """Parse consent.txt and compile every pattern, naming the line of one that is unusable."""
    if not text:
        return ()
    if text.startswith("﻿"):
        text = text[1:]
    compiled: list[tuple[str, re.Pattern[str]]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            compiled.append((line, glob_to_regex(line)))
        except (re.error, RecursionError) as exc:
            raise ConsentFileError(f"consent.txt line {number}: {exc}") from exc
    return tuple(compiled)


def first_match(compiled: tuple[tuple[str, re.Pattern[str]], ...], key: str) -> str | None:
    for pattern, regex in compiled:
        if regex.fullmatch(key):
            return pattern
    return None


def matching_pattern(patterns: tuple[str, ...], key: str) -> str | None:
    for pattern in patterns:
        if glob_to_regex(pattern).fullmatch(key):
            return pattern
    return None
