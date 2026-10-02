import pytest
from pydantic import ValidationError
from swarmscribe_protocol import AppliedCorrection, Correction, Vocabulary


def test_vocabulary_round_trips_through_json():
    vocabulary = Vocabulary(
        version=3,
        terms=["Ashford", "José"],
        corrections=[Correction(heard="jay son", replacement="Jason")],
    )
    assert Vocabulary.model_validate_json(vocabulary.model_dump_json()) == vocabulary


def test_version_zero_with_nothing_in_it_means_no_vocabulary():
    vocabulary = Vocabulary(version=0)
    assert vocabulary.terms == []
    assert vocabulary.corrections == []


def test_version_cannot_be_negative():
    with pytest.raises(ValidationError):
        Vocabulary(version=-1)


@pytest.mark.parametrize("field", ["heard", "replacement"])
def test_correction_sides_cannot_be_empty(field):
    values = {"heard": "jay son", "replacement": "Jason", field: ""}
    with pytest.raises(ValidationError):
        Correction(**values)


def test_applied_correction_counts_at_least_one():
    AppliedCorrection(heard="jay son", replacement="Jason", count=1)
    with pytest.raises(ValidationError):
        AppliedCorrection(heard="jay son", replacement="Jason", count=0)
