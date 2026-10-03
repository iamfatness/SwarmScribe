import pytest
from pydantic import ValidationError
from swarmscribe_protocol import (
    DEFAULT_CHANNEL_LABELS,
    AppliedCorrection,
    JobSettings,
    Segment,
    SegmentsDocument,
    Word,
)


def _settings(**overrides):
    return JobSettings(**{"model": "large-v3", "compute_type": "float16", **overrides})


def test_job_settings_defaults_are_the_fixed_behaviour():
    settings = _settings()
    assert settings.language == "en"
    assert settings.condition_on_previous_text is False
    assert settings.temperatures == (0.0, 0.2, 0.4)
    assert settings.vad_filter is True
    assert settings.word_timestamps is True


@pytest.mark.parametrize(
    "override",
    [
        {"language": "fr"},
        {"condition_on_previous_text": True},
        {"vad_filter": False},
        {"word_timestamps": False},
    ],
)
def test_job_settings_reject_changes_to_fixed_behaviour(override):
    with pytest.raises(ValidationError):
        _settings(**override)


@pytest.mark.parametrize("temperatures", [(), (0.0, 0.6), (-0.1,), (0.0, 1.0), (float("nan"),)])
def test_job_settings_reject_unclamped_temperature_ladder(temperatures):
    with pytest.raises(ValidationError):
        _settings(temperatures=temperatures)


def test_word_probability_must_be_between_zero_and_one():
    Word(start=0.0, end=0.5, word=" hello", probability=1.0)
    with pytest.raises(ValidationError):
        Word(start=0.0, end=0.5, word=" hello", probability=1.2)


def test_segments_document_round_trips_through_json():
    document = SegmentsDocument(
        schema_version=1,
        source_checksum="a" * 64,
        duration=12.5,
        device="cuda",
        engine_version="0.1.0",
        settings=_settings(),
        vocabulary_version=3,
        vocabulary_terms_used=["Ashford", "José"],
        corrections_applied=[AppliedCorrection(heard="jay son", replacement="Jason", count=2)],
        segments=[
            Segment(
                start=0.0,
                end=1.5,
                text="Welcome to Ashford.",
                words=[
                    Word(
                        start=0.0, end=0.4, word=" Welcome", probability=0.98, original=" welcom"
                    )
                ],
            )
        ],
    )
    assert SegmentsDocument.model_validate_json(document.model_dump_json()) == document


def test_segments_document_accepts_no_speech():
    document = SegmentsDocument(
        schema_version=1,
        source_checksum="a" * 64,
        duration=3.0,
        device="cpu",
        engine_version="0.1.0",
        settings=_settings(model="distil-large-v3", compute_type="int8"),
        vocabulary_version=0,
        vocabulary_terms_used=[],
        corrections_applied=[],
        segments=[],
    )
    assert document.segments == []


def test_segments_document_rejects_unknown_schema_version():
    with pytest.raises(ValidationError):
        SegmentsDocument(
            schema_version=2,
            source_checksum="a" * 64,
            duration=3.0,
            device="cpu",
            engine_version="0.1.0",
            settings=_settings(),
            vocabulary_version=0,
            vocabulary_terms_used=[],
            corrections_applied=[],
            segments=[],
        )


def test_word_original_is_optional():
    assert Word(start=0.0, end=0.5, word=" hello", probability=1.0).original is None


def test_segments_document_has_no_glossary_field():
    assert "glossary" not in SegmentsDocument.model_fields


def test_vocabulary_version_cannot_be_negative():
    with pytest.raises(ValidationError):
        SegmentsDocument(
            schema_version=1,
            source_checksum="a" * 64,
            duration=3.0,
            device="cpu",
            engine_version="0.1.0",
            settings=_settings(),
            vocabulary_version=-1,
            vocabulary_terms_used=[],
            corrections_applied=[],
            segments=[],
        )


def test_job_settings_default_to_mono_with_left_and_right_labels():
    settings = _settings()
    assert settings.channel_mode == "mono"
    assert settings.channel_labels == ("Left", "Right") == DEFAULT_CHANNEL_LABELS


@pytest.mark.parametrize("mode", ["mono", "stereo_split", "auto"])
def test_job_settings_accept_each_channel_mode(mode):
    assert _settings(channel_mode=mode).channel_mode == mode


@pytest.mark.parametrize("mode", ["stereo", "stereo-split", "split", ""])
def test_job_settings_reject_unknown_channel_modes(mode):
    with pytest.raises(ValidationError):
        _settings(channel_mode=mode)


@pytest.mark.parametrize(
    "labels",
    [
        ("Left",),
        ("Left", "Middle", "Right"),
        ("", "Right"),
        ("x" * 41, "Right"),
        (" Left", "Right"),
        ("Left ", "Right"),
        ("   ", "Right"),
        ("Left\nSide", "Right"),
        ("Left\tSide", "Right"),
        ("Left\u2028Side", "Right"),
        ("Left\u2029Side", "Right"),
        ("Left\u0085Side", "Right"),
        ("Same", "same"),
    ],
)
def test_job_settings_reject_bad_channel_labels(labels):
    with pytest.raises(ValidationError):
        _settings(channel_mode="stereo_split", channel_labels=labels)


def test_channel_labels_of_up_to_forty_characters_are_accepted():
    labels = ("x" * 40, "José Ashford")
    assert _settings(channel_mode="stereo_split", channel_labels=labels).channel_labels == labels


def test_job_settings_without_channel_fields_are_mono():
    settings = JobSettings.model_validate_json('{"model": "large-v3", "compute_type": "float16"}')
    assert (settings.channel_mode, settings.channel_labels) == ("mono", ("Left", "Right"))


def test_segment_channel_is_optional_and_left_or_right():
    assert Segment(start=0.0, end=1.0, text="Hello.", words=[]).channel is None
    assert Segment(start=0.0, end=1.0, text="Hello.", words=[], channel=1).channel == 1
    for bad in (-1, 2):
        with pytest.raises(ValidationError):
            Segment(start=0.0, end=1.0, text="Hello.", words=[], channel=bad)


def test_a_split_segments_document_round_trips_through_json():
    document = SegmentsDocument(
        schema_version=1,
        source_checksum="a" * 64,
        duration=4.0,
        device="cpu",
        engine_version="0.1.0",
        settings=_settings(channel_mode="stereo_split", channel_labels=("Agent", "Customer")),
        vocabulary_version=0,
        vocabulary_terms_used=[],
        corrections_applied=[],
        channel_labels=["Agent", "Customer"],
        segments=[
            Segment(start=0.0, end=1.0, text="Good morning.", words=[], channel=0),
            Segment(start=1.5, end=2.0, text="Hello.", words=[], channel=1),
        ],
    )
    assert SegmentsDocument.model_validate_json(document.model_dump_json()) == document


def test_a_mono_segments_document_has_no_channel_labels():
    document = SegmentsDocument(
        schema_version=1,
        source_checksum="a" * 64,
        duration=3.0,
        device="cpu",
        engine_version="0.1.0",
        settings=_settings(),
        vocabulary_version=0,
        vocabulary_terms_used=[],
        corrections_applied=[],
        segments=[],
    )
    assert document.channel_labels is None
