"""Bias-term selection and post-transcription corrections. Pure functions, no model."""

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import NamedTuple

from .types import AppliedCorrection, Correction, Segment, Word

# faster-whisper truncates hotwords to about 220 tokens; 600 characters stays inside that.
HOTWORDS_CHAR_BUDGET = 600

_SEPARATOR = ", "
# prefix of non-word characters, core, suffix of non-word characters
_EDGES = re.compile(r"^(\W*)(.*?)(\W*)$", re.DOTALL)
_TEXT_TOKENS = re.compile(r"\s*\S+")


def select_bias_terms(
    terms: Sequence[str], budget: int = HOTWORDS_CHAR_BUDGET
) -> tuple[str, ...]:
    """Leading terms, in order, that fit the budget once joined. Stops at the first misfit."""
    selected: list[str] = []
    seen: set[str] = set()
    used = 0
    for term in terms:
        cleaned = term.strip()
        key = cleaned.casefold()
        if not cleaned or key in seen:
            continue
        cost = len(cleaned) + (len(_SEPARATOR) if selected else 0)
        if used + cost > budget:
            break
        seen.add(key)
        selected.append(cleaned)
        used += cost
    return tuple(selected)


def build_hotwords(terms_used: Sequence[str]) -> str | None:
    return _SEPARATOR.join(terms_used) if terms_used else None


@dataclass(frozen=True)
class _Rule:
    index: int
    cores: tuple[str, ...]
    replacement: str


class _Piece(NamedTuple):
    first: int
    last: int
    text: str
    rule: _Rule | None


def _split(token: str) -> tuple[str, str, str]:
    prefix, core, suffix = _EDGES.match(token).groups()
    return prefix, core, suffix


def _core(token: str) -> str:
    return _split(token)[1].casefold()


def _compile(corrections: Sequence[Correction]) -> list[_Rule]:
    rules = []
    for index, correction in enumerate(corrections):
        cores = tuple(core for core in map(_core, correction.heard.split()) if core)
        replacement = correction.replacement.strip()
        if cores and replacement:
            rules.append(_Rule(index=index, cores=cores, replacement=replacement))
    # Longest first: most tokens, then most characters, then file order.
    rules.sort(key=lambda r: (-len(r.cores), -sum(map(len, r.cores)), r.index))
    return rules


def _correct(tokens: Sequence[str], rules: Sequence[_Rule]) -> list[_Piece]:
    cores = [_core(token) for token in tokens]
    pieces: list[_Piece] = []
    i = 0
    while i < len(tokens):
        piece = _Piece(first=i, last=i, text=tokens[i], rule=None)
        for rule in rules:
            last = i + len(rule.cores) - 1
            if tuple(cores[i : last + 1]) != rule.cores:
                continue
            text = _split(tokens[i])[0] + rule.replacement + _split(tokens[last])[2]
            if text != "".join(tokens[i : last + 1]):
                piece = _Piece(first=i, last=last, text=text, rule=rule)
                break
        pieces.append(piece)
        i = piece.last + 1
    return pieces


def _merge(group: Sequence[Word], piece: _Piece) -> Word:
    if piece.rule is None:
        return group[0]
    return Word(
        start=group[0].start,
        end=group[-1].end,
        word=piece.text,
        probability=min(word.probability for word in group),
        original="".join(word.word for word in group),
    )


def _correct_segment(segment: Segment, rules: Sequence[_Rule], counts: dict[int, int]) -> Segment:
    if segment.words:
        tokens = [word.word for word in segment.words]
    else:
        tokens = _TEXT_TOKENS.findall(segment.text)
    pieces = _correct(tokens, rules)
    fired = [piece for piece in pieces if piece.rule is not None]
    if not fired:
        return segment
    for piece in fired:
        counts[piece.rule.index] = counts.get(piece.rule.index, 0) + 1
    if not segment.words:
        return replace(segment, text="".join(piece.text for piece in pieces).strip())
    words = tuple(_merge(segment.words[piece.first : piece.last + 1], piece) for piece in pieces)
    return replace(segment, text="".join(word.word for word in words).strip(), words=words)


def apply_corrections(
    segments: Sequence[Segment], corrections: Sequence[Correction]
) -> tuple[tuple[Segment, ...], tuple[AppliedCorrection, ...]]:
    """Apply each correction as a whole-word, case-insensitive match, longest first."""
    rules = _compile(corrections)
    if not rules:
        return tuple(segments), ()
    counts: dict[int, int] = {}
    corrected = tuple(_correct_segment(segment, rules, counts) for segment in segments)
    applied = tuple(
        AppliedCorrection(
            heard=correction.heard.strip(),
            replacement=correction.replacement.strip(),
            count=counts[index],
        )
        for index, correction in enumerate(corrections)
        if counts.get(index)
    )
    return corrected, applied
