import pytest
from swarmscribe_engine import Segment, TranscribeSettings, Transcript, Word

DEFAULT_SEGMENTS = (
    Segment(
        start=0.0,
        end=1.5,
        text="Welcome to Ashford.",
        words=(
            Word(start=0.0, end=0.4, word=" Welcome", probability=0.98),
            Word(start=0.4, end=0.6, word=" to", probability=0.99),
            Word(start=0.6, end=1.5, word=" Ashford.", probability=0.71),
        ),
    ),
    Segment(
        start=2.0,
        end=3.25,
        text="Thanks for joining.",
        words=(Word(start=2.0, end=3.25, word=" Thanks for joining.", probability=0.9),),
    ),
)


@pytest.fixture
def make_transcript():
    def _make(segments=DEFAULT_SEGMENTS, **overrides) -> Transcript:
        values = {
            "source_name": "recording.mp3",
            "source_checksum": "a" * 64,
            "duration": 3.25,
            "settings": TranscribeSettings(
                model="large-v3", compute_type="float16", device="cuda"
            ),
            "vocabulary_version": 1,
            "vocabulary_terms_used": ("Ashford",),
            "corrections_applied": (),
            "segments": tuple(segments),
        }
        values.update(overrides)
        return Transcript(**values)

    return _make
