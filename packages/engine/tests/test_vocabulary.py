import pytest
from swarmscribe_engine import AppliedCorrection, Correction, Segment, Vocabulary, Word
from swarmscribe_engine.vocabulary import (
    HOTWORDS_CHAR_BUDGET,
    HOTWORDS_TOKEN_BUDGET,
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


def test_the_character_budget_is_a_fallback_well_inside_the_token_limit():
    assert HOTWORDS_CHAR_BUDGET == 300
    assert HOTWORDS_TOKEN_BUDGET == 220


def test_a_custom_measure_sets_the_budget():
    def words(text):
        return len(text.replace(",", " ").split())

    terms = ["one two", "three", "four five", "six"]
    # "one two, three" is 3 words; adding "four five" would make 5
    assert select_bias_terms(terms, budget=4, measure=words) == ("one two", "three")


def test_a_custom_measure_stops_at_the_first_misfit_and_never_skips_ahead():
    def words(text):
        return len(text.replace(",", " ").split())

    assert select_bias_terms(["one", "two three four", "five"], budget=2, measure=words) == (
        "one",
    )


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


# --- punctuation at match edges (fix round 1) ------------------------------


def words_of(segment):
    return [word.word for word in segment.words]


@pytest.mark.parametrize(
    ("tokens", "heard", "replacement", "expected_text", "expected_words"),
    [
        # F1: punctuation in `heard` is required and consumed
        ((" c++", " is"), "c++", "C++", "C++ is", [" C++", " is"]),
        ((" see", " c", " code"), "c++", "C++", "see c code", None),
        ((" $100",), "$100", "one hundred dollars",
         "one hundred dollars", [" one hundred dollars"]),
        ((" dr.", " smith"), "dr.", "Doctor", "Doctor smith", [" Doctor", " smith"]),
        ((" dr", " smith"), "dr.", "Doctor", "dr smith", None),
        ((" c++,",), "c++", "C++", "C++,", [" C++,"]),
        # F2: no duplicated punctuation from the replacement
        ((" dr.", " smith"), "dr", "Dr.", "Dr. smith", [" Dr.", " smith"]),
        ((" dr", " smith"), "dr", "Dr.", "Dr. smith", [" Dr.", " smith"]),
        ((" mister.",), "mister", "Mr.", "Mr.", [" Mr."]),
        ((" mister,",), "mister", "Mr.", "Mr.,", [" Mr.,"]),
        ((' "ashferd!"',), "ashferd", "Ashford", '"Ashford!"', [' "Ashford!"']),
        # F3: only whitespace between the tokens of a multi-word match
        ((" saw", " jay.", " Son,", " come"), "jay son", "Jason", "saw jay. Son, come", None),
        ((" jay", "-son"), "jay son", "Jason", "jay-son", None),
        ((" jay,", " son"), "jay son", "Jason", "jay, son", None),
        ((" jay", " son,", " hello"), "jay son", "Jason", "Jason, hello", [" Jason,", " hello"]),
        ((" (jay", " son)"), "jay son", "Jason", "(Jason)", [" (Jason)"]),
    ],
)
def test_punctuation_at_match_edges(tokens, heard, replacement, expected_text, expected_words):
    original = seg(*tokens)
    (segment,), _ = apply_corrections([original], [fix(heard, replacement)])
    assert segment.text == expected_text
    if expected_words is None:
        assert segment is original
    else:
        assert words_of(segment) == expected_words


def test_text_path_consumes_punctuation_written_in_heard():
    plain = Segment(start=0.0, end=2.0, text="we use c++ daily", words=())
    (segment,), applied = apply_corrections([plain], [fix("c++", "C++")])
    assert segment.text == "we use C++ daily"
    assert applied[0].count == 1


def test_text_path_multi_word_match_refuses_punctuation_between_tokens():
    plain = Segment(start=0.0, end=2.0, text="saw jay. Son, come", words=())
    (segment,), applied = apply_corrections([plain], [fix("jay son", "Jason")])
    assert segment is plain
    assert applied == ()


def test_rule_with_punctuation_in_heard_beats_the_bare_rule():
    (segment,), applied = apply_corrections(
        [seg(" c++", " code")], [fix("c", "SEE"), fix("c++", "C++")]
    )
    assert segment.text == "C++ code"
    assert applied == (AppliedCorrection(heard="c++", replacement="C++", count=1),)


def test_heard_full_stop_rule_beats_the_bare_rule():
    (segment,), _ = apply_corrections(
        [seg(" dr.", " smith")], [fix("dr", "Dr"), fix("dr.", "Doctor")]
    )
    assert segment.text == "Doctor smith"


def test_bare_rule_still_applies_where_the_punctuated_rule_does_not_match():
    (segment,), _ = apply_corrections(
        [seg(" c", " code")], [fix("c", "SEE"), fix("c++", "C++")]
    )
    assert segment.text == "SEE code"


# --- a longest match that changes nothing still wins ------------------------


def test_a_longest_match_that_changes_nothing_blocks_shorter_rules():
    original = seg(" St.", " John", " spoke.")
    (segment,), applied = apply_corrections(
        [original], [fix("st. john", "St. John"), fix("john", "Jon")]
    )
    assert segment is original
    assert segment.text == "St. John spoke."
    assert applied == ()


def test_a_longest_match_that_changes_nothing_blocks_shorter_rules_case_insensitively():
    (segment,), applied = apply_corrections(
        [seg(" st.", " john", " spoke.")], [fix("st. john", "St. John"), fix("john", "Jon")]
    )
    assert segment.text == "St. John spoke."
    assert applied == (AppliedCorrection(heard="st. john", replacement="St. John", count=1),)


def test_the_first_rule_wins_even_when_a_later_rule_would_change_the_text():
    (segment,), applied = apply_corrections(
        [seg(" jason", " and", " Jason")], [fix("jason", "Jason"), fix("jason", "Jayson")]
    )
    assert segment.text == "Jason and Jason"
    assert applied == (AppliedCorrection(heard="jason", replacement="Jason", count=1),)
