"""Logging: one JSON object per line on stderr (follower spec 9), or plain text on request.

A log line never holds audio, transcript text, a link, a token or a credential. Link URLs
carry their own secret, and httpx logs every request URL at INFO, so its loggers are kept at
WARNING (the package sets that when it is imported; `configure` sets it again)."""

import json
import logging
import sys
from datetime import UTC, datetime

FIELDS = ("event", "job_id", "lease_id", "follower_id")
QUIETED = ("httpx", "httpcore")


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
        return json.dumps(line, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = "".join(
            f" {field}={getattr(record, field)}"
            for field in FIELDS
            if getattr(record, field, None) is not None
        )
        stamp = datetime.fromtimestamp(record.created, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        return f"{stamp} {record.levelname} {record.name}: {record.getMessage()}{fields}"


def configure(log_format: str = "json", *, stream=None) -> None:
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter() if log_format == "json" else TextFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    for name in QUIETED:  # again, in case something lowered them since the import
        logging.getLogger(name).setLevel(logging.WARNING)
