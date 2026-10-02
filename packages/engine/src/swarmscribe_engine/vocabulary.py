"""Bias-term selection and post-transcription corrections. Pure functions, no model."""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import NamedTuple

from .types import AppliedCorrection, Correction, Segment, Word

# faster-whisper truncates hotwords at 223 tokens, and rare names and acronyms cost 2-3 tokens
# per character cluster (600 characters measured 338-397 tokens). The token budget, measured
# with the model's own tokenizer, is the real limit; the character budget is only the
# fallback for a model that exposes no tokenizer, set conservatively for the same reason.
HOTWORDS_TOKEN_BUDGET = 220
HOTWORDS_CHAR_BUDGET = 300

_SEPARATOR = ", "
# prefix of non-word characters, core, suffix of non-word characters
_EDGES = re.compile(r"^(\W*)(.*?)(\W*)$", re.DOTALL)
_TEXT_TOKENS = re.compile(r"\s*\S+")


def select_bias_terms(
    terms: Sequence[str],
    budget: int = HOTWORDS_CHAR_BUDGET,
    measure: Callable[[str], int] = len,
) -> tuple[str, ...]:
    """Leading terms, in order, whose joined text measures within the budget.

    `measure` sizes the joined string (characters by default). Stops at the first misfit.
    """
    selected: list[str] = []
    seen: set[str] = set()
    for term in terms:
        cleaned = term.strip()
        key = cleaned.casefold()
        if not cleaned or key in seen:
            continue
        if measure(_SEPARATOR.join([*selected, cleaned])) > budget:
            break
        seen.add(key)
        selected.append(cleaned)
    return tuple(selected)


def build_hotwords(terms_used: Sequence[str]) -> str | None:
    return _SEPARATOR.join(terms_used) if terms_used else None


class _Edges(NamedTuple):
    prefix: str
    core: str  # case-folded
    suffix: str


@dataclass(frozen=True)
class _Rule:
    index: int
    heard: tuple[_Edges, ...]
    replacement: str


class _Piece(NamedTuple):
    first: int
    last: int
    text: str
    rule: _Rule | None


def _split(token: str) -> _Edges:
    prefix, core, suffix = _EDGES.match(token).groups()
    return _Edges(prefix, core.casefold(), suffix)


def _compile(corrections: Sequence[Correction]) -> list[_Rule]:
    rules = []
    for index, correction in enumerate(corrections):
        heard = tuple(map(_split, correction.heard.split()))
        replacement = correction.replacement.strip()
        if heard and all(edges.core for edges in heard) and replacement:
            rules.append(_Rule(index=index, heard=heard, replacement=replacement))
    # Longest first: most tokens, then most characters (punctuation written in `heard`
    # counts, so the more specific rule wins), then file order.
    rules.sort(key=lambda r: (-len(r.heard), -sum(map(_weight, r.heard)), r.index))
    return rules


def _weight(edges: _Edges) -> int:
    return len(edges.prefix) + len(edges.core) + len(edges.suffix)


def _overlap(tail: str, head: str) -> int:
    """Length of the longest suffix of `tail` that is also a prefix of `head`."""
    for k in range(min(len(tail), len(head)), 0, -1):
        if tail[-k:] == head[:k]:
            return k
    return 0


def _rewrite(tokens: Sequence[_Edges], first: int, rule: _Rule) -> str | None:
    """Replacement text for `rule` matching at `first`, or None when it does not match."""
    last = first + len(rule.heard) - 1
    if last >= len(tokens):
        return None
    remaining: list[tuple[str, str]] = []
    for token, heard in zip(tokens[first : last + 1], rule.heard, strict=True):
        if token.core != heard.core:
            return None
        if not (token.prefix.endswith(heard.prefix) and token.suffix.startswith(heard.suffix)):
            return None
        kept_prefix = token.prefix[: len(token.prefix) - len(heard.prefix)]
        remaining.append((kept_prefix, token.suffix[len(heard.suffix) :]))
    # Between the tokens of a multi-word match only whitespace may sit.
    if any(suffix for _, suffix in remaining[:-1]):
        return None
    if any(prefix.strip() for prefix, _ in remaining[1:]):
        return None
    prefix, suffix = remaining[0][0], remaining[-1][1]
    written_prefix, _, written_suffix = _EDGES.match(rule.replacement).groups()
    prefix = prefix[: len(prefix) - _overlap(prefix, written_prefix)]
    suffix = suffix[_overlap(written_suffix, suffix) :]
    return prefix + rule.replacement + suffix


def _correct(tokens: Sequence[str], rules: Sequence[_Rule]) -> list[_Piece]:
    edges = [_split(token) for token in tokens]
    pieces: list[_Piece] = []
    i = 0
    while i < len(tokens):
        pieces_here = [_Piece(first=i, last=i, text=tokens[i], rule=None)]
        for rule in rules:
            text = _rewrite(edges, i, rule)
            if text is None:
                continue
            # The first matching rule wins, whether or not it changes anything.
            last = i + len(rule.heard) - 1
            if text != "".join(tokens[i : last + 1]):
                pieces_here = [_Piece(first=i, last=last, text=text, rule=rule)]
            else:
                pieces_here = [
                    _Piece(first=k, last=k, text=tokens[k], rule=None) for k in range(i, last + 1)
                ]
            break
        pieces.extend(pieces_here)
        i = pieces_here[-1].last + 1
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
    # Whisper's segment text is the concatenation of its words, so rebuilding it from
    # the corrected words keeps text and words consistent.
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
