import json
import re
from pathlib import Path

import pytest
from swarmscribe_engine.cli import read_corrections, read_terms

CASES = json.loads(
    (Path(__file__).parents[2] / "protocol" / "tests" / "vocabulary_file_cases.json").read_text(
        encoding="utf-8"
    )
)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_engine_cli_readers_match_the_shared_cases(case, tmp_path):
    name = "vocabulary.txt" if case["kind"] == "terms" else "corrections.txt"
    path = tmp_path / name
    path.write_bytes(case["text"].encode("utf-8"))
    if case["kind"] == "terms":
        assert list(read_terms(path)) == case["expect"]
        return
    if "error" in case:
        with pytest.raises(ValueError, match=re.escape(case["error"])):
            read_corrections(path)
        return
    assert [[c.heard, c.replacement] for c in read_corrections(path)] == case["expect"]
