import json
import re
from pathlib import Path

import pytest
from swarmscribe_protocol.vocabulary_files import (
    VocabularyFileError,
    parse_corrections,
    parse_terms,
)

CASES = json.loads(
    (Path(__file__).parent / "vocabulary_file_cases.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_parser_matches_the_shared_cases(case):
    if case["kind"] == "terms":
        result = parse_terms(case["text"])
        assert result == case["expect"]
        return
    if "error" in case:
        with pytest.raises(VocabularyFileError, match=re.escape(case["error"])):
            parse_corrections(case["text"], source="corrections.txt")
        return
    result = parse_corrections(case["text"], source="corrections.txt")
    assert [[c.heard, c.replacement] for c in result] == case["expect"]


def test_the_error_names_the_given_source():
    with pytest.raises(VocabularyFileError, match=r"^archive/corrections\.txt line 1"):
        parse_corrections("nonsense\n", source="archive/corrections.txt")


def test_vocabulary_file_error_is_a_value_error():
    assert issubclass(VocabularyFileError, ValueError)
