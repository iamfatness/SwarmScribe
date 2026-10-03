import pytest
from swarmscribe_leader.ingest.consent import (
    ConsentFileError,
    compile_consent,
    glob_to_regex,
    is_recording,
    matching_pattern,
    parse_consent,
)


@pytest.mark.parametrize(
    ("pattern", "key", "matches"),
    [
        ("talks/*.mp3", "talks/one.mp3", True),
        ("talks/*.mp3", "talks/deep/one.mp3", False),
        ("talks/*.mp3", "Talks/one.mp3", False),
        ("talks/?.mp3", "talks/a.mp3", True),
        ("talks/?.mp3", "talks/ab.mp3", False),
        ("**/*.mp3", "one.mp3", True),
        ("**/*.mp3", "a/b/c/one.mp3", True),
        ("talks/**", "talks/a/b/one.mp3", True),
        ("talks/**/final.wav", "talks/final.wav", True),
        ("talks/**/final.wav", "talks/x/y/final.wav", True),
        ("talks/[ab].mp3", "talks/b.mp3", True),
        ("talks/[!ab].mp3", "talks/b.mp3", False),
        ("talks/[!ab].mp3", "talks/c.mp3", True),
        ("talks/one.mp3", "talks/one.mp3", True),
        ("talks/one.mp3", "talks/one.mp3.bak", False),
        ("a+b (1).mp3", "a+b (1).mp3", True),
        ("talks/[^ab].mp3", "talks/c.mp3", False),
        ("talks/[^ab].mp3", "talks/^.mp3", True),
        ("talks[!x]one.mp3", "talks/one.mp3", False),
        ("a[b.mp3", "a[b.mp3", True),
    ],
)
def test_glob_semantics(pattern, key, matches):
    assert bool(glob_to_regex(pattern).fullmatch(key)) is matches


def test_parse_consent_skips_comments_blanks_and_a_bom():
    assert parse_consent("﻿# allowed\ntalks/*.mp3\n\n  2024/**  \n") == (
        "talks/*.mp3",
        "2024/**",
    )


def test_compile_consent_reports_the_line_of_a_bad_pattern():
    with pytest.raises(ConsentFileError, match="line 2"):
        compile_consent("talks/*.mp3\n[z-a].mp3\n")


def test_compile_consent_pairs_patterns_with_regexes():
    ((pattern, regex),) = compile_consent("# c\n\ntalks/*.mp3\n")
    assert pattern == "talks/*.mp3"
    assert regex.fullmatch("talks/a.mp3")


def test_no_consent_file_means_no_patterns():
    assert parse_consent(None) == ()


def test_matching_pattern_returns_the_first_match():
    patterns = ("archive/**", "talks/*.mp3", "**/*.mp3")
    assert matching_pattern(patterns, "talks/one.mp3") == "talks/*.mp3"
    assert matching_pattern(patterns, "private.wav") is None


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("a.mp3", True),
        ("a.MP3", True),
        ("x/b.webm", True),
        ("consent.txt", False),
        ("a.mp3.txt", False),
        ("noextension", False),
    ],
)
def test_is_recording(key, expected):
    assert is_recording(key) is expected
