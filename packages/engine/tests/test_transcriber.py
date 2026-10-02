import hashlib
from types import SimpleNamespace

import pytest
from swarmscribe_engine import Transcriber, TranscribeSettings, UndecodableAudioError
from swarmscribe_engine.transcriber import build_prompt, sha256_file

SETTINGS = TranscribeSettings(model="large-v3", compute_type="float16", device="cuda")


class DecodeError(Exception):
    pass


def raw_word(start, end, word, probability):
    return SimpleNamespace(start=start, end=end, word=word, probability=probability)


def raw_segment(start, end, text, words):
    return SimpleNamespace(start=start, end=end, text=text, words=words)


class FakeModel:
    def __init__(self, segments=(), duration=0.0, error=None, error_while_iterating=None):
        self.segments = segments
        self.duration = duration
        self.error = error
        self.error_while_iterating = error_while_iterating
        self.calls = []

    def transcribe(self, audio, **kwargs):
        self.calls.append((audio, kwargs))
        if self.error is not None:
            raise self.error
        return self._iterate(), SimpleNamespace(duration=self.duration)

    def _iterate(self):
        yield from self.segments
        if self.error_while_iterating is not None:
            raise self.error_while_iterating


def make_transcriber(model, settings=SETTINGS):
    return Transcriber(settings, model_factory=lambda _: model, decode_errors=(DecodeError,))


@pytest.fixture
def audio(tmp_path):
    path = tmp_path / "recording.mp3"
    path.write_bytes(b"not really audio, the model is fake")
    return path


def test_model_is_loaded_once_and_reused(audio):
    loads = []
    model = FakeModel()

    def factory(settings):
        loads.append(settings)
        return model

    transcriber = Transcriber(SETTINGS, model_factory=factory, decode_errors=())
    transcriber.transcribe(audio)
    transcriber.transcribe(audio)
    assert loads == [SETTINGS]
    assert len(model.calls) == 2


def test_fixed_settings_are_passed_to_the_model(audio):
    model = FakeModel()
    make_transcriber(model).transcribe(audio, glossary=["Ashford", "José"])
    path_arg, kwargs = model.calls[0]
    assert path_arg == str(audio)
    assert kwargs == {
        "language": "en",
        "condition_on_previous_text": False,
        "temperature": [0.0, 0.2, 0.4],
        "vad_filter": True,
        "word_timestamps": True,
        "initial_prompt": "Glossary: Ashford, José.",
    }


def test_custom_ladder_is_passed_through(audio):
    model = FakeModel()
    settings = TranscribeSettings(
        model="large-v3", compute_type="float16", device="cuda", temperatures=(0.0,)
    )
    make_transcriber(model, settings).transcribe(audio)
    assert model.calls[0][1]["temperature"] == [0.0]


def test_segments_and_words_are_converted(audio):
    model = FakeModel(
        segments=[
            raw_segment(
                0.0,
                1.5,
                " Welcome to Ashford. ",
                [raw_word(0.0, 0.4, " Welcome", 0.98), raw_word(0.4, 1.5, " to Ashford.", 0.7)],
            )
        ],
        duration=1.5,
    )
    transcript = make_transcriber(model).transcribe(audio, glossary=["Ashford"])
    assert transcript.source_name == "recording.mp3"
    assert transcript.source_checksum == hashlib.sha256(audio.read_bytes()).hexdigest()
    assert transcript.duration == 1.5
    assert transcript.settings == SETTINGS
    assert transcript.glossary == ("Ashford",)
    (segment,) = transcript.segments
    assert segment.text == "Welcome to Ashford."
    assert segment.words[0].word == " Welcome"
    assert segment.words[1].probability == 0.7


def test_no_speech_gives_an_empty_transcript(audio):
    transcript = make_transcriber(FakeModel(duration=30.0)).transcribe(audio)
    assert transcript.segments == ()
    assert transcript.duration == 30.0


def test_probability_is_clamped_to_zero_and_one(audio):
    model = FakeModel(
        segments=[
            raw_segment(
                0.0,
                1.0,
                "Okay.",
                [raw_word(0.0, 0.5, " O", 1.0000001), raw_word(0.5, 1.0, "kay.", -0.0000001)],
            )
        ]
    )
    words = make_transcriber(model).transcribe(audio).segments[0].words
    assert [word.probability for word in words] == [1.0, 0.0]


def test_segment_without_words_gets_an_empty_tuple(audio):
    model = FakeModel(segments=[raw_segment(0.0, 1.0, "Okay.", None)])
    assert make_transcriber(model).transcribe(audio).segments[0].words == ()


def test_whitespace_only_segments_are_dropped(audio):
    model = FakeModel(
        segments=[raw_segment(0.0, 1.0, "   ", []), raw_segment(1.0, 2.0, "Okay.", [])]
    )
    transcript = make_transcriber(model).transcribe(audio)
    assert [segment.text for segment in transcript.segments] == ["Okay."]


def test_missing_file_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        make_transcriber(FakeModel()).transcribe(tmp_path / "missing.mp3")


def test_a_directory_is_not_a_recording(tmp_path):
    with pytest.raises(FileNotFoundError):
        make_transcriber(FakeModel()).transcribe(tmp_path)


def test_decode_failure_becomes_undecodable_audio_error(audio):
    transcriber = make_transcriber(FakeModel(error=DecodeError("Invalid data found")))
    with pytest.raises(UndecodableAudioError, match="recording.mp3") as excinfo:
        transcriber.transcribe(audio)
    assert isinstance(excinfo.value.__cause__, DecodeError)


def test_decode_failure_during_iteration_is_also_caught(audio):
    model = FakeModel(
        segments=[raw_segment(0.0, 1.0, "Okay.", [])],
        error_while_iterating=DecodeError("truncated"),
    )
    with pytest.raises(UndecodableAudioError):
        make_transcriber(model).transcribe(audio)


def test_other_model_errors_propagate_unchanged(audio):
    transcriber = make_transcriber(FakeModel(error=RuntimeError("CUDA out of memory")))
    with pytest.raises(RuntimeError, match="CUDA out of memory"):
        transcriber.transcribe(audio)


@pytest.mark.parametrize(
    ("terms", "expected"),
    [
        ([], None),
        (["  ", ""], None),
        (["Ashford"], "Glossary: Ashford."),
        ([" Ashford ", "", "José"], "Glossary: Ashford, José."),
    ],
)
def test_build_prompt(terms, expected):
    assert build_prompt(terms) == expected


def test_blank_glossary_terms_are_not_recorded(audio):
    transcript = make_transcriber(FakeModel()).transcribe(audio, glossary=[" Ashford ", " "])
    assert transcript.glossary == ("Ashford",)


def test_sha256_file_matches_hashlib(tmp_path):
    path = tmp_path / "big.bin"
    data = b"x" * (3 * 1024 * 1024 + 7)
    path.write_bytes(data)
    assert sha256_file(path) == hashlib.sha256(data).hexdigest()
