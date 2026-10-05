"""Logging: one JSON object per line on stderr (follower spec 9), or plain text on request.

A log line never holds audio, transcript text, a link, a token or a credential. Link URLs
carry their own secret, and httpx logs every request URL at INFO, so its loggers are kept at
WARNING (the package sets that when it is imported; `configure` sets it again)."""

import json
import logging
import re
import sys
from datetime import UTC, datetime

FIELDS = ("event", "job_id", "lease_id", "follower_id")
QUIETED = ("httpx", "httpcore")


_URL = re.compile(r"(https?://[^\s/?#\"'<>]+)([/?#][^\s\"'<>]*)?", re.IGNORECASE)
_BEARER = re.compile(r"\b(bearer)\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)


def redact(text: str) -> str:
    """Defence in depth: the host of a URL stays, its path and query go (a link's secret is in
    one or the other), and a bearer value goes."""
    text = _URL.sub(lambda m: m.group(1) + ("/<redacted>" if m.group(2) else ""), text)
    return _BEARER.sub(r"\1 <redacted>", text)


class RedactingFilter(logging.Filter):
    """On the handler, so every logger passes through it: drops the HTTP libraries' own
    records below WARNING (they print request URLs) and redacts what remains."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name.split(".")[0] in QUIETED and record.levelno < logging.WARNING:
            return False
        record.msg = redact(record.getMessage())
        record.args = None
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                line[field] = value
        if record.exc_info and record.exc_info[0] is not None:
            # The class only: an exception's text can hold a path or a URL.
            line["error"] = record.exc_info[0].__name__
        return json.dumps(line, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = "".join(
            f" {field}={getattr(record, field)}"
            for field in FIELDS
            if getattr(record, field, None) is not None
        )
        stamp = datetime.fromtimestamp(record.created, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        message = record.getMessage().replace("\n", "\\n")
        return f"{stamp} {record.levelname} {record.name}: {message}{fields}"


def configure(log_format: str = "json", *, stream=None) -> None:
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.addFilter(RedactingFilter())
    handler.setFormatter(JsonFormatter() if log_format == "json" else TextFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    for name in QUIETED:  # again, in case something lowered them since the import
        logging.getLogger(name).setLevel(logging.WARNING)
