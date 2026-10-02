import pytest
from swarmscribe_engine import AppliedCorrection, Correction, Segment, Vocabulary, Word
from swarmscribe_engine.vocabulary import (
    HOTWORDS_CHAR_BUDGET,
    apply_corrections,
    build_hotwords,
    select_bias_terms,
)


def seg(*tokens, probabilities=None):
    """A segment whose words are `tokens`, one second each."""
    probabilities = probabilities or [0.9] * len(tokens)
    words = tuple(
        Word(start=float(i), end=float(i + 1), word=token, probability=probability)
        for i, (token, probability) in enumerate(zip(tokens, probabilities, strict=True))
    )
    return Segment(
        start=0.0, end=float(len(tokens)), text="".join(tokens).strip(), words=words
    )


def fix(heard, replacement):
    return Correction(heard=heard, replacement=replacement)


# --- vocabulary type -------------------------------------------------------


def test_default_vocabulary_is_empty_version_zero():
    vocabulary = Vocabulary()
    assert (vocabulary.version, vocabulary.terms, vocabulary.corrections) == (0, (), ())


def test_word_original_defaults_to_none():
    assert Word(start=0.0, end=1.0, word=" hi", probability=0.9).original is None


# --- bias term selection ---------------------------------------------------


def test_terms_are_stripped_and_blanks_dropped():
    assert select_bias_terms([" Ashford ", "", "   ", "José"]) == ("Ashford", "José")


def test_duplicate_terms_are_dropped_case_insensitively_keeping_the_first():
    assert select_bias_terms(["Ashford", "ASHFORD", "José", "ashford"]) == ("Ashford", "José")


def test_terms_that_exactly_fill_the_budget_are_kept():
    # "aaaa, bbbb" is exactly 10 characters
    assert select_bias_terms(["aaaa", "bbbb", "cc"], budget=10) == ("aaaa", "bbbb")


def test_selection_stops_at_the_first_term_that_does_not_fit():
    # "c" would fit, but priority order means we stop, never skip ahead
    assert select_bias_terms(["aaaa", "bbbbbbbbbb", "c"], budget=8) == ("aaaa",)


def test_a_very_large_vocabulary_is_cut_to_a_leading_run_within_the_budget():
    terms = [f"term{i:04d}" for i in range(1000)]
    selected = select_bias_terms(terms)
    assert 0 < len(selected) < len(terms)
    assert list(selected) == terms[: len(selected)]
    assert len(build_hotwords(selected)) <= HOTWORDS_CHAR_BUDGET


def test_a_first_term_longer_than_the_budget_selects_nothing():
    assert select_bias_terms(["x" * 20, "ok"], budget=10) == ()


def test_build_hotwords_joins_with_comma_space():
    assert build_hotwords(("Ashford", "José")) == "Ashford, José"


def test_build_hotwords_is_none_when_there_are_no_terms():
    assert build_hotwords(()) is None


# --- corrections -----------------------------------------------------------


def test_no_corrections_returns_segments_unchanged():
    segments = (seg(" Welcome", " to", " Ashford."),)
    corrected, applied = apply_corrections(segments, ())
    assert corrected == segments
    assert applied == ()


def test_single_word_is_corrected_case_insensitively_keeping_punctuation():
    (segment,), applied = apply_corrections(
        [seg(" Welcome", " to", " ASHFERD.")], [fix("ashferd", "Ashford")]
    )
    assert segment.text == "Welcome to Ashford."
    assert segment.words[2].word == " Ashford."
    assert segment.words[2].original == " ASHFERD."
    assert applied == (AppliedCorrection(heard="ashferd", replacement="Ashford", count=1),)


def test_uncorrected_words_are_left_exactly_as_they_were():
    original = seg(" Welcome", " to", " ashferd.")
    (segment,), _ = apply_corrections([original], [fix("ashferd", "Ashford")])
    assert segment.words[0] is original.words[0]
    assert segment.words[1].original is None


def test_multi_word_match_merges_into_one_word():
    (segment,), applied = apply_corrections(
        [seg(" jay", " son,", " hello", probabilities=[0.9, 0.4, 0.8])],
        [fix("jay son", "Jason")],
    )
    assert segment.text == "Jason, hello"
    assert len(segment.words) == 2
    merged = segment.words[0]
    assert merged.word == " Jason,"
    assert merged.original == " jay son,"
    assert (merged.start, merged.end) == (0.0, 2.0)
    assert merged.probability == 0.4
    assert applied[0].count == 1


def test_a_correction_never_fires_inside_a_longer_word():
    original = seg(" the", " party", " started")
    (segment,), applied = apply_corrections([original], [fix("art", "ART")])
    assert segment is original
    assert applied == ()


def test_a_multi_word_rule_does_not_match_across_a_partial_word():
    original = seg(" jay", " sonny")
    (segment,), applied = apply_corrections([original], [fix("jay son", "Jason")])
    assert segment is original
    assert applied == ()


def test_the_longest_rule_wins():
    (segment,), applied = apply_corrections(
        [seg(" new", " york", " new")],
        [fix("new", "NEW"), fix("new york", "NYC")],
    )
    assert segment.text == "NYC NEW"
    assert applied == (
        AppliedCorrection(heard="new", replacement="NEW", count=1),
        AppliedCorrection(heard="new york", replacement="NYC", count=1),
    )


def test_output_is_never_corrected_again():
    (segment,), applied = apply_corrections([seg(" a", " b")], [fix("a b", "c"), fix("c", "d")])
    assert segment.text == "c"
    assert applied == (AppliedCorrection(heard="a b", replacement="c", count=1),)


def test_a_rule_that_changes_nothing_is_not_counted():
    original = seg(" Ashford")
    (segment,), applied = apply_corrections([original], [fix("ashford", "Ashford")])
    assert segment is original
    assert applied == ()


def test_counts_add_up_across_segments_in_rule_order():
    segments = [seg(" jay", " son"), seg(" ashferd", " and", " jay", " son")]
    _, applied = apply_corrections(
        segments, [fix("ashferd", "Ashford"), fix("jay son", "Jason")]
    )
    assert applied == (
        AppliedCorrection(heard="ashferd", replacement="Ashford", count=1),
        AppliedCorrection(heard="jay son", replacement="Jason", count=2),
    )


def test_non_ascii_replacement():
    (segment,), _ = apply_corrections([seg(" Jose", " spoke")], [fix("jose", "José")])
    assert segment.text == "José spoke"


def test_leading_quote_and_trailing_punctuation_survive():
    (segment,), _ = apply_corrections([seg(' "ashferd!"')], [fix("ashferd", "Ashford")])
    assert segment.words[0].word == ' "Ashford!"'


def test_segment_without_words_is_corrected_in_its_text():
    plain = Segment(start=0.0, end=2.0, text="we met jay son, today", words=())
    (segment,), applied = apply_corrections([plain], [fix("jay son", "Jason")])
    assert segment.text == "we met Jason, today"
    assert segment.words == ()
    assert applied[0].count == 1


@pytest.mark.parametrize(
    "rule",
    [fix("", "Jason"), fix("   ", "Jason"), fix("jay", "  "), fix("...", "x")],
)
def test_rules_with_nothing_to_match_or_nothing_to_write_are_ignored(rule):
    original = seg(" jay", " son")
    (segment,), applied = apply_corrections([original], [rule])
    assert segment is original
    assert applied == ()
