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
        elif pattern[i] == "[" and (end := pattern.find("]", i + 1)) != -1:
            body = pattern[i + 1 : end]
            if body.startswith("!"):
                body = "^" + body[1:]
            out.append("[" + body.replace("\\", "\\\\") + "]")
            i = end + 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out))


def matching_pattern(patterns: tuple[str, ...], key: str) -> str | None:
    for pattern in patterns:
        if glob_to_regex(pattern).fullmatch(key):
            return pattern
    return None
