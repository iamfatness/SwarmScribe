"""A settings file for a follower that a service manager starts (follower spec 8.3).

`swarmscribe-follower --env-file PATH <command>` reads `NAME=value` lines into the process's
environment before the settings are read, so that the systemd unit, the Windows service and a
person running `doctor` or `leave` by hand all use the same file. A value in the file wins over
the environment the process was started with.

The file holds settings, not the join token: the token is a file of its own, named by
`SWARMSCRIBE_JOIN_TOKEN_FILE`. A mistake in the file is reported by its line number, never by
its content. Standard library only: the Windows service reads the file before anything slow
is imported."""

import os
import re
from collections.abc import MutableMapping
from pathlib import Path

NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
MAX_BYTES = 64 * 1024


class EnvFileError(Exception):
    """The settings file cannot be used; the message never holds a value from it."""


def parse(text: str, where: str) -> dict[str, str]:
    """`NAME=value` per line. Blank lines and lines starting with `#` are skipped; spaces
    around the name and the value are dropped; one pair of quotes around a value is removed.
    Nothing is expanded: `$HOME` and `%ProgramData%` stay as written."""
    values: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, equals, value = line.partition("=")
        name = name.strip().removeprefix("export ").strip()
        if not equals or not NAME.fullmatch(name):
            raise EnvFileError(f"{where}, line {number}: expected NAME=value")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if "\x00" in value:
            raise EnvFileError(f"{where}, line {number}: a value cannot hold a NUL character")
        values[name] = value
    return values


def load(path: str | Path, environ: MutableMapping[str, str] | None = None) -> list[str]:
    """Read `path` into `environ` (the process's environment by default); returns the names
    it set, sorted."""
    environ = os.environ if environ is None else environ
    try:
        with open(path, "rb") as source:
            data = source.read(MAX_BYTES + 1)
    except OSError as error:
        raise EnvFileError(
            f"the settings file {path} cannot be read: {error.strerror or type(error).__name__}"
        ) from None
    if len(data) > MAX_BYTES:
        raise EnvFileError(f"the settings file {path} is larger than {MAX_BYTES} bytes")
    try:
        text = data.decode("utf-8-sig")  # Notepad writes a byte-order mark
    except UnicodeDecodeError:
        raise EnvFileError(f"the settings file {path} is not UTF-8 text") from None
    values = parse(text, str(path))
    environ.update(values)
    return sorted(values)
