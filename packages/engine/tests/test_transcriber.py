import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
import swarmscribe_engine.transcriber as engine_transcriber
from swarmscribe_engine import (
    AppliedCorrection,
    Correction,
    Transcriber,
    TranscribeSettings,
    UndecodableAudioError,
    Vocabulary,
    write_outputs,
)
from swarmscribe_engine.transcriber import sha256_file
from swarmscribe_engine.writers import render_txt

SETTINGS = TranscribeSettings(model="large-v3", compute_type="float16", device="cuda")


class DecodeError(Exception):
    pass


def raw_word(start, end, word, probability):
    return SimpleNamespace(start=start, end=end, word=word, probability=probability)


def raw_segment(start, end, text, words):
    return SimpleNamespace(start=start, end=end, text=text, words=words)


class FakeEncoding:
    def __init__(self, ids):
        self.ids = ids


class WordTokenizer:
    """Stands in for the model's tokenizer: one token per word, as faster-whisper encodes."""

    def __init__(self):
        self.encoded = []

    def encode(self, text, add_special_tokens=True):
        assert add_special_tokens is False
        self.encoded.append(text)
        return FakeEncoding(list(range(len(text.replace(",", " ").split()))))


class FakeModel:
    def __init__(
        self,
        segments=(),
        duration=0.0,
        error=None,
        error_while_iterating=None,
        hf_tokenizer=None,
    ):
        if hf_tokenizer is not None:
            self.hf_tokenizer = hf_tokenizer
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


def test_fixed_settings_and_hotwords_are_passed_to_the_model(audio):
    model = FakeModel()
    make_transcriber(model).transcribe(audio, Vocabulary(version=2, terms=("Ashford", "José")))
    path_arg, kwargs = model.calls[0]
    assert path_arg == str(audio)
    assert kwargs == {
        "language": "en",
        "condition_on_previous_text": False,
        "temperature": [0.0, 0.2, 0.4],
        "vad_filter": True,
        "word_timestamps": True,
        "hotwords": "Ashford, José",
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
    vocabulary = Vocabulary(version=4, terms=("Ashford",))
    transcript = make_transcriber(model).transcribe(audio, vocabulary)
    assert transcript.source_name == "recording.mp3"
    assert transcript.source_checksum == hashlib.sha256(audio.read_bytes()).hexdigest()
    assert transcript.duration == 1.5
    assert transcript.settings == SETTINGS
    assert transcript.vocabulary_version == 4
    assert transcript.vocabulary_terms_used == ("Ashford",)
    assert transcript.corrections_applied == ()
    (segment,) = transcript.segments
    assert segment.text == "Welcome to Ashford."
    assert segment.words[0].word == " Welcome"
    assert segment.words[1].probability == 0.7


def test_without_a_vocabulary_no_hotwords_are_sent(audio):
    model = FakeModel()
    transcript = make_transcriber(model).transcribe(audio)
    assert model.calls[0][1]["hotwords"] is None
    assert "initial_prompt" not in model.calls[0][1]
    assert transcript.vocabulary_version == 0
    assert transcript.vocabulary_terms_used == ()


def test_only_terms_within_the_budget_are_sent_and_recorded(audio):
    model = FakeModel()
    terms = tuple(f"term{i:04d}" for i in range(1000))
    transcript = make_transcriber(model).transcribe(audio, Vocabulary(version=1, terms=terms))
    used = transcript.vocabulary_terms_used
    assert 0 < len(used) < len(terms)
    assert used == terms[: len(used)]
    assert model.calls[0][1]["hotwords"] == ", ".join(used)


def test_corrections_are_applied_to_the_model_output(audio):
    model = FakeModel(
        segments=[
            raw_segment(
                0.0,
                2.0,
                " Thanks jay son.",
                [
                    raw_word(0.0, 0.5, " Thanks", 0.9),
                    raw_word(0.5, 1.0, " jay", 0.8),
                    raw_word(1.0, 2.0, " son.", 0.3),
                ],
            )
        ],
        duration=2.0,
    )
    vocabulary = Vocabulary(version=5, corrections=(Correction("jay son", "Jason"),))
    transcript = make_transcriber(model).transcribe(audio, vocabulary)
    (segment,) = transcript.segments
    assert segment.text == "Thanks Jason."
    assert segment.words[1].word == " Jason."
    assert segment.words[1].original == " jay son."
    assert segment.words[1].probability == 0.3
    assert transcript.corrections_applied == (
        AppliedCorrection(heard="jay son", replacement="Jason", count=1),
    )


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


def test_sha256_file_matches_hashlib(tmp_path):
    path = tmp_path / "big.bin"
    data = b"x" * (3 * 1024 * 1024 + 7)
    path.write_bytes(data)
    assert sha256_file(path) == hashlib.sha256(data).hexdigest()


def test_terms_are_budgeted_in_tokens_when_the_model_has_a_tokenizer(audio):
    tokenizer = WordTokenizer()
    model = FakeModel(hf_tokenizer=tokenizer)
    # 300 distinct two-word terms: far more characters than the fallback allows is not the
    # point; the token budget (220, one token per word here) is.
    terms = tuple(f"alpha{i:03d} beta{i:03d}" for i in range(300))
    transcript = make_transcriber(model).transcribe(audio, Vocabulary(version=1, terms=terms))
    used = transcript.vocabulary_terms_used
    assert len(used) == 110
    assert used == terms[:110]
    assert model.calls[0][1]["hotwords"] == ", ".join(used)
    assert all(text.startswith(" ") for text in tokenizer.encoded)


def test_without_a_tokenizer_terms_fall_back_to_the_character_budget(audio):
    model = FakeModel()
    terms = tuple(f"alpha{i:03d} beta{i:03d}" for i in range(300))
    transcript = make_transcriber(model).transcribe(audio, Vocabulary(version=1, terms=terms))
    used = transcript.vocabulary_terms_used
    assert 0 < len(used) < 110
    assert len(", ".join(used)) <= 300


# --- per-channel transcription -------------------------------------------------------

LEFT, RIGHT = object(), object()  # stand-ins for the two decoded channel arrays

FIXED_KWARGS = {
    "language": "en",
    "condition_on_previous_text": False,
    "temperature": [0.0, 0.2, 0.4],
    "vad_filter": True,
    "word_timestamps": True,
}


class ChannelModel(FakeModel):
    """Answers each audio input (a channel stand-in or a path string) with its own segments."""

    def __init__(self, by_audio, duration=0.0, **kwargs):
        super().__init__(duration=duration, **kwargs)
        self.by_audio = by_audio

    def transcribe(self, audio, **kwargs):
        self.calls.append((audio, kwargs))
        if self.error is not None:
            raise self.error
        return iter(self.by_audio.get(audio, ())), SimpleNamespace(duration=self.duration)


def seg(start, end, text):
    return raw_segment(start, end, text, [raw_word(start, end, text, 0.9)])


def split_transcriber(
    model,
    *,
    channels=2,
    mode="stereo_split",
    labels=("Left", "Right"),
    decode=None,
    probes=None,
):
    """A Transcriber whose channel probe answers `channels` (or raises it, if it is an
    exception) and whose stereo decoder returns (LEFT, RIGHT) unless `decode` is given."""
    settings = replace(SETTINGS, channel_mode=mode, channel_labels=labels)

    def count(path):
        if probes is not None:
            probes.append(path)
        if isinstance(channels, BaseException):
            raise channels
        return channels

    return Transcriber(
        settings,
        model_factory=lambda _: model,
        decode_errors=(DecodeError,),
        channel_count=count,
        decode_stereo=decode or (lambda path: (LEFT, RIGHT)),
    )


def test_split_transcribes_each_channel_and_merges_them_by_start_time(audio):
    model = ChannelModel(
        {
            LEFT: [seg(0.0, 1.0, " Good morning."), seg(4.0, 5.0, " Fine, thanks.")],
            RIGHT: [seg(2.0, 3.0, " Hello.")],
        },
        duration=5.0,
    )
    transcript = split_transcriber(model).transcribe(audio)
    assert [(s.start, s.channel, s.text) for s in transcript.segments] == [
        (0.0, 0, "Good morning."),
        (2.0, 1, "Hello."),
        (4.0, 0, "Fine, thanks."),
    ]
    assert transcript.channel_labels == ("Left", "Right")
    assert transcript.settings.channel_mode == "stereo_split"
    assert transcript.duration == 5.0
    assert [call[0] for call in model.calls] == [LEFT, RIGHT]


def test_a_tie_on_start_time_puts_the_left_channel_first(audio):
    model = ChannelModel(
        {
            LEFT: [seg(1.0, 2.0, " Yes.")],
            RIGHT: [seg(0.5, 1.0, " So."), seg(1.0, 1.5, " No.")],
        }
    )
    transcript = split_transcriber(model).transcribe(audio)
    assert [(s.start, s.channel, s.text) for s in transcript.segments] == [
        (0.5, 1, "So."),
        (1.0, 0, "Yes."),
        (1.0, 1, "No."),
    ]


def test_both_passes_get_the_same_hotwords_and_fixed_settings(audio):
    model = ChannelModel({})
    split_transcriber(model).transcribe(audio, Vocabulary(version=2, terms=("Ashford", "José")))
    expected = {**FIXED_KWARGS, "hotwords": "Ashford, José"}
    assert model.calls == [(LEFT, expected), (RIGHT, expected)]


def test_split_terms_are_budgeted_once_with_the_models_tokenizer(audio):
    model = ChannelModel({}, hf_tokenizer=WordTokenizer())
    terms = tuple(f"alpha{i:03d} beta{i:03d}" for i in range(300))
    transcript = split_transcriber(model).transcribe(audio, Vocabulary(version=1, terms=terms))
    assert transcript.vocabulary_terms_used == terms[:110]
    hotwords = [call[1]["hotwords"] for call in model.calls]
    assert hotwords == [", ".join(terms[:110])] * 2


def test_corrections_apply_to_both_channels_and_their_counts_add_up(audio):
    def thanks(start):
        return raw_segment(
            start,
            start + 1.0,
            " Thanks jay son.",
            [
                raw_word(start, start + 0.4, " Thanks", 0.9),
                raw_word(start + 0.4, start + 0.7, " jay", 0.8),
                raw_word(start + 0.7, start + 1.0, " son.", 0.6),
            ],
        )

    model = ChannelModel({LEFT: [thanks(0.0)], RIGHT: [thanks(2.0)]})
    vocabulary = Vocabulary(version=5, corrections=(Correction("jay son", "Jason"),))
    transcript = split_transcriber(model).transcribe(audio, vocabulary)
    assert [(s.channel, s.text) for s in transcript.segments] == [
        (0, "Thanks Jason."),
        (1, "Thanks Jason."),
    ]
    assert transcript.segments[1].words[1].original == " jay son."
    assert transcript.corrections_applied == (
        AppliedCorrection(heard="jay son", replacement="Jason", count=2),
    )


def test_one_model_load_serves_both_channels(audio):
    loads = []
    model = ChannelModel({})

    def factory(settings):
        loads.append(settings)
        return model

    transcriber = Transcriber(
        replace(SETTINGS, channel_mode="stereo_split"),
        model_factory=factory,
        decode_errors=(),
        channel_count=lambda path: 2,
        decode_stereo=lambda path: (LEFT, RIGHT),
    )
    transcriber.transcribe(audio)
    transcriber.transcribe(audio)
    assert len(loads) == 1
    assert [call[0] for call in model.calls] == [LEFT, RIGHT, LEFT, RIGHT]


def test_custom_labels_are_carried_into_the_transcript(audio):
    transcriber = split_transcriber(ChannelModel({}), labels=("Agent", "Customer"))
    assert transcriber.transcribe(audio).channel_labels == ("Agent", "Customer")


@pytest.mark.parametrize(
    ("channels", "found"),
    [(1, "1 audio channel;"), (6, "6 audio channels;"), (0, "no audio stream;")],
)
def test_stereo_split_needs_exactly_two_channels(audio, channels, found):
    model = ChannelModel({})
    with pytest.raises(UndecodableAudioError, match=found) as excinfo:
        split_transcriber(model, channels=channels).transcribe(audio)
    assert "recording.mp3" in str(excinfo.value)
    assert "stereo-split (stereo_split) needs a two-channel (stereo) recording" in str(
        excinfo.value
    )
    assert model.calls == []


def test_auto_splits_a_two_channel_file(audio):
    model = ChannelModel({LEFT: [seg(0.0, 1.0, " Hello.")]})
    transcript = split_transcriber(model, mode="auto").transcribe(audio)
    assert transcript.channel_labels == ("Left", "Right")
    assert [(s.channel, s.text) for s in transcript.segments] == [(0, "Hello.")]


@pytest.mark.parametrize("channels", [1, 6])
def test_auto_mixes_anything_but_two_channels_as_mono(audio, channels):
    model = ChannelModel({str(audio): [seg(0.0, 1.0, " Hello.")]})
    decoded = []
    transcript = split_transcriber(
        model, channels=channels, mode="auto", decode=lambda path: decoded.append(path)
    ).transcribe(audio)
    assert decoded == []
    assert [call[0] for call in model.calls] == [str(audio)]
    assert transcript.channel_labels is None
    assert transcript.segments[0].channel is None


def test_auto_on_a_file_with_no_audio_stream_is_undecodable(audio):
    model = ChannelModel({})
    with pytest.raises(UndecodableAudioError, match="recording.mp3 has no audio stream"):
        split_transcriber(model, channels=0, mode="auto").transcribe(audio)
    assert model.calls == []


def test_mono_never_looks_at_the_channels(audio):
    probes = []
    model = ChannelModel({})
    split_transcriber(model, mode="mono", probes=probes).transcribe(audio)
    assert probes == []
    assert [call[0] for call in model.calls] == [str(audio)]


def test_a_failing_channel_probe_is_undecodable(audio):
    transcriber = split_transcriber(ChannelModel({}), channels=DecodeError("Invalid data"))
    with pytest.raises(UndecodableAudioError, match="recording.mp3") as excinfo:
        transcriber.transcribe(audio)
    assert isinstance(excinfo.value.__cause__, DecodeError)


def test_a_failing_stereo_decode_is_undecodable(audio):
    def broken(path):
        raise DecodeError("truncated")

    model = ChannelModel({})
    with pytest.raises(UndecodableAudioError, match="recording.mp3"):
        split_transcriber(model, decode=broken).transcribe(audio)
    assert model.calls == []


class FailsOn(ChannelModel):
    """Raises `error` when asked to transcribe `audio_id`; answers the other input normally."""

    def __init__(self, audio_id, error, by_audio):
        super().__init__(by_audio)
        self.audio_id = audio_id
        self.failure = error

    def transcribe(self, audio, **kwargs):
        if audio == self.audio_id:
            self.calls.append((audio, kwargs))
            raise self.failure
        return super().transcribe(audio, **kwargs)


def test_a_non_decode_failure_on_the_right_pass_propagates(audio):
    model = FailsOn(RIGHT, RuntimeError("out of memory"), {LEFT: [seg(0.0, 1.0, " Hello.")]})
    with pytest.raises(RuntimeError, match="out of memory"):
        split_transcriber(model).transcribe(audio)
    assert [call[0] for call in model.calls] == [LEFT, RIGHT]


def test_a_decode_error_raised_while_iterating_segments_is_undecodable(audio):
    def lazy_failure():
        yield seg(0.0, 1.0, " Hello.")
        raise DecodeError("truncated mid-stream")

    model = ChannelModel({LEFT: lazy_failure()})
    with pytest.raises(UndecodableAudioError, match="recording.mp3") as excinfo:
        split_transcriber(model).transcribe(audio)
    assert isinstance(excinfo.value.__cause__, DecodeError)


def test_per_call_settings_choose_the_channel_handling(audio):
    model = ChannelModel({})
    transcriber = Transcriber(
        SETTINGS,
        model_factory=lambda _: model,
        decode_errors=(),
        channel_count=lambda path: 2,
        decode_stereo=lambda path: (LEFT, RIGHT),
    )
    transcriber.transcribe(audio)
    split = replace(SETTINGS, channel_mode="auto")
    assert transcriber.transcribe(audio, settings=split).settings == split
    assert [call[0] for call in model.calls] == [str(audio), LEFT, RIGHT]


def test_per_call_settings_must_use_the_loaded_model(audio):
    transcriber = make_transcriber(FakeModel())
    other = TranscribeSettings(model="tiny.en", compute_type="int8", device="cpu")
    with pytest.raises(ValueError, match="different model"):
        transcriber.transcribe(audio, settings=other)


def test_jobs_with_different_channel_settings_share_one_loaded_model(monkeypatch, audio):
    loads = []
    model = ChannelModel({})

    def factory(settings):
        loads.append(settings)
        return model

    monkeypatch.setattr(engine_transcriber, "_default_model_factory", factory)
    monkeypatch.setattr(engine_transcriber, "_default_channel_count", lambda path: 2)
    monkeypatch.setattr(engine_transcriber, "_default_decode_stereo", lambda path: (LEFT, RIGHT))
    engine_transcriber._shared_transcriber.cache_clear()
    try:
        engine_transcriber.transcribe(audio, SETTINGS)
        split = replace(SETTINGS, channel_mode="stereo_split", channel_labels=("Agent", "Customer"))
        transcript = engine_transcriber.transcribe(audio, split)
        engine_transcriber.transcribe(audio, replace(SETTINGS, temperatures=(0.0,)))
    finally:
        engine_transcriber._shared_transcriber.cache_clear()
    assert loads == [SETTINGS]
    assert transcript.settings == split
    assert transcript.channel_labels == ("Agent", "Customer")
    assert [call[0] for call in model.calls] == [str(audio), LEFT, RIGHT, str(audio)]
    assert model.calls[3][1]["temperature"] == [0.0]


# --- the real decoder on generated WAVs (no model download) --------------------------


class HearsSound:
    """A model that 'hears' one segment in any audio that is not silent. It receives the real
    decoder's channel arrays, so it shows which channel the sound ended up in."""

    def __init__(self):
        self.calls = []

    def transcribe(self, audio, **kwargs):
        self.calls.append(audio)
        loud = float(abs(audio).max()) > 0.05
        segments = [seg(0.0, 1.0, " Sound.")] if loud else []
        return iter(segments), SimpleNamespace(duration=len(audio) / 16000)


def real_split(*, mode="stereo_split", labels=("Left", "Right"), model=None):
    """A Transcriber with the real channel probe and stereo decoder and a fake model."""
    model = model or HearsSound()
    settings = replace(SETTINGS, channel_mode=mode, channel_labels=labels)
    return Transcriber(settings, model_factory=lambda _: model), model


@pytest.mark.parametrize(
    ("tones", "channel", "line"),
    [([440, None], 0, "Agent: Sound.\n"), ([None, 440], 1, "Customer: Sound.\n")],
)
def test_a_real_stereo_file_is_split_into_its_left_and_right_channels(
    make_wav, tones, channel, line
):
    path = make_wav("call.wav", tones)
    transcriber, model = real_split(labels=("Agent", "Customer"))
    transcript = transcriber.transcribe(path)
    assert [(s.channel, s.text) for s in transcript.segments] == [(channel, "Sound.")]
    assert render_txt(transcript) == line
    assert transcript.duration == pytest.approx(1.0, abs=0.01)
    assert len(model.calls) == 2


def test_a_real_stereo_file_with_two_silent_channels_has_no_speech(make_wav):
    transcriber, _ = real_split()
    transcript = transcriber.transcribe(make_wav("call.wav", [None, None]))
    assert transcript.segments == ()
    assert transcript.channel_labels == ("Left", "Right")


def test_a_real_mono_file_is_refused_in_stereo_split(make_wav):
    path = make_wav("recording.wav", [440])
    transcriber, model = real_split()
    with pytest.raises(UndecodableAudioError, match=r"recording\.wav has 1 audio channel;"):
        transcriber.transcribe(path)
    assert model.calls == []


def test_a_real_six_channel_file_is_refused_in_stereo_split_and_mixed_in_auto(make_wav):
    path = make_wav("surround.wav", [440] * 6)
    transcriber, _ = real_split()
    with pytest.raises(UndecodableAudioError, match="6 audio channels"):
        transcriber.transcribe(path)
    model = FakeModel()
    auto, _ = real_split(mode="auto", model=model)
    assert auto.transcribe(path).channel_labels is None
    assert [call[0] for call in model.calls] == [str(path)]


def test_a_real_corrupt_file_is_undecodable_in_split_mode(tmp_path):
    corrupt = tmp_path / "corrupt.wav"
    corrupt.write_bytes(bytes(range(256)) * 64)
    transcriber, _ = real_split()
    with pytest.raises(UndecodableAudioError, match="corrupt.wav"):
        transcriber.transcribe(corrupt)


def test_auto_on_a_real_mono_file_writes_what_mono_writes_apart_from_the_requested_mode(
    make_wav, tmp_path
):
    path = make_wav("recording.wav", [440])

    def outputs(mode):
        model = FakeModel(segments=[seg(0.0, 1.0, " Good morning.")], duration=1.0)
        transcriber, _ = real_split(mode=mode, model=model)
        files = write_outputs(transcriber.transcribe(path), tmp_path / mode)
        assert [call[0] for call in model.calls] == [str(path)]
        return files.txt.read_bytes(), files.srt.read_bytes(), json.loads(
            files.segments_json.read_text("utf-8")
        )

    mono_txt, mono_srt, mono_json = outputs("mono")
    auto_txt, auto_srt, auto_json = outputs("auto")
    assert (auto_txt, auto_srt) == (mono_txt, mono_srt)
    # settings echoes the request; nothing records a split, so nothing else differs.
    assert auto_json["settings"].pop("channel_mode") == "auto"
    assert auto_json["settings"].pop("channel_labels") == ["Left", "Right"]
    assert auto_json == mono_json
