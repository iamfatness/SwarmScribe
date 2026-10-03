"""Parse an operator's vocabulary.txt and corrections.txt.

The engine CLI keeps its own copy of these rules, because the engine imports nothing
internal; tests/vocabulary_file_cases.json is run against both to keep them identical.
"""

import re

from .vocabulary import Correction


class VocabularyFileError(ValueError):
    """A line in a vocabulary or corrections file breaks the rules."""


def _content_lines(text: str) -> list[tuple[int, str]]:
    if text.startswith("\ufeff"):
        text = text[1:]
    return [
        (number, line.strip())
        for number, line in enumerate(text.splitlines(), start=1)
        if line.strip() and not line.strip().startswith("#")
    ]


def parse_terms(text: str) -> list[str]:
    return [line for _, line in _content_lines(text)]


def parse_corrections(text: str, source: str = "corrections.txt") -> list[Correction]:
    corrections: list[Correction] = []
    defined: dict[str, tuple[int, str]] = {}  # normalised heard -> (line number, replacement)
    for number, line in _content_lines(text):
        heard, arrow, replacement = line.partition("=>")
        heard, replacement = heard.strip(), replacement.strip()
        if not arrow or not heard or not replacement:
            raise VocabularyFileError(f"{source} line {number}: expected 'heard as => should be'")
        if not all(re.search(r"\w", part) for part in heard.split()):
            raise VocabularyFileError(
                f"{source} line {number}: 'heard as' must contain a word in every part"
            )
        key = " ".join(heard.split()).casefold()
        if key in defined:
            first_line, first_replacement = defined[key]
            if replacement != first_replacement:
                raise VocabularyFileError(
                    f"{source} line {number}: '{' '.join(heard.split())}' "
                    f"is already defined on line {first_line}"
                )
            continue
        defined[key] = (number, replacement)
        corrections.append(Correction(heard=heard, replacement=replacement))
    return corrections
