import pytest
from swarmscribe_engine import Segment, TranscribeSettings, Transcript, Word

DEFAULT_SEGMENTS = (
    Segment(
        start=0.0,
        end=1.5,
        text="Welcome to Shiloh.",
        words=(
            Word(start=0.0, end=0.4, word=" Welcome", probability=0.98),
            Word(start=0.4, end=0.6, word=" to", probability=0.99),
            Word(start=0.6, end=1.5, word=" Shiloh.", probability=0.71),
        ),
    ),
    Segment(
        start=2.0,
        end=3.25,
        text="Please be seated.",
        words=(Word(start=2.0, end=3.25, word=" Please be seated.", probability=0.9),),
    ),
)


@pytest.fixture
def make_transcript():
    def _make(segments=DEFAULT_SEGMENTS, **overrides) -> Transcript:
        values = {
            "source_name": "sermon.mp3",
            "source_checksum": "a" * 64,
            "duration": 3.25,
            "settings": TranscribeSettings(
                model="large-v3", compute_type="float16", device="cuda"
            ),
            "glossary": ("Shiloh",),
            "segments": tuple(segments),
        }
        values.update(overrides)
        return Transcript(**values)

    return _make
