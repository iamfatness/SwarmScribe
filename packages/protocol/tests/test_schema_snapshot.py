from pathlib import Path

from swarmscribe_protocol.schema import export_schema

SNAPSHOT = Path(__file__).parent / "schema_v1.json"


def test_wire_schema_matches_the_committed_snapshot():
    assert export_schema() == SNAPSHOT.read_text(encoding="utf-8"), (
        "The wire format changed. If this is intended, decide whether PROTOCOL_VERSION "
        "must be bumped, then regenerate with: "
        "uv run python -m swarmscribe_protocol.schema packages/protocol/tests/schema_v1.json"
    )


def test_export_is_deterministic():
    assert export_schema() == export_schema()
