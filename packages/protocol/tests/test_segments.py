import pytest
from pydantic import ValidationError
from swarmscribe_protocol import JobSettings, Segment, SegmentsDocument, Word


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


@pytest.mark.parametrize("temperatures", [(), (0.0, 0.6), (-0.1,), (0.0, 1.0)])
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
        glossary=["Ashford", "José"],
        segments=[
            Segment(
                start=0.0,
                end=1.5,
                text="Welcome to Ashford.",
                words=[Word(start=0.0, end=0.4, word=" Welcome", probability=0.98)],
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
        glossary=[],
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
            glossary=[],
            segments=[],
        )
