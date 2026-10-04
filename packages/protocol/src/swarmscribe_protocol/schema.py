"""Export the JSON Schema of every wire model, for the snapshot test."""

import json
import sys
from pathlib import Path

from . import messages, segments, vocabulary
from .base import WireModel

MODELS: tuple[type[WireModel], ...] = (
    segments.JobSettings,
    segments.Word,
    segments.Segment,
    segments.SegmentsDocument,
    vocabulary.Correction,
    vocabulary.Vocabulary,
    vocabulary.AppliedCorrection,
    messages.Link,
    messages.Capabilities,
    messages.RegisterRequest,
    messages.RegisterResponse,
    messages.UploadUrls,
    messages.ClaimResponse,
    messages.HeartbeatRequest,
    messages.HeartbeatResponse,
    messages.OutputChecksums,
    messages.SubmitRequest,
    messages.SubmitResponse,
    messages.FailRequest,
    messages.ReleaseRequest,
    messages.LinksRequest,
    messages.JobLinks,
    messages.ErrorBody,
)


def export_schema() -> str:
    schemas = {model.__name__: model.model_json_schema() for model in MODELS}
    return json.dumps(schemas, indent=2, sort_keys=True) + "\n"


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m swarmscribe_protocol.schema <output-path>")
    Path(sys.argv[1]).write_text(export_schema(), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
