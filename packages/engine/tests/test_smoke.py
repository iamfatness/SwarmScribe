import math
import struct
import wave

import pytest
from swarmscribe_engine import (
    Correction,
    Transcriber,
    TranscribeSettings,
    UndecodableAudioError,
    Vocabulary,
    write_outputs,
)
from swarmscribe_protocol import SegmentsDocument

pytestmark = pytest.mark.smoke

SETTINGS = TranscribeSettings(model="tiny.en", compute_type="int8", device="cpu")


@pytest.fixture(scope="module")
def transcriber():
    return Transcriber(SETTINGS)


@pytest.fixture
def tone(tmp_path):
    path = tmp_path / "tone.wav"
    rate = 16000
    frames = b"".join(
        struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / rate)))
        for i in range(rate * 3)
    )
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(frames)
    return path


def test_real_model_accepts_the_fixed_settings_and_outputs_validate(transcriber, tone, tmp_path):
    vocabulary = Vocabulary(
        version=3, terms=("Ashford",), corrections=(Correction("ash ford", "Ashford"),)
    )
    transcript = transcriber.transcribe(tone, vocabulary)
    assert transcript.duration == pytest.approx(3.0, abs=0.1)

    files = write_outputs(transcript, tmp_path / "out")
    document = SegmentsDocument.model_validate_json(files.segments_json.read_text("utf-8"))
    assert document.settings.model == "tiny.en"
    assert document.vocabulary_version == 3
    assert document.vocabulary_terms_used == ["Ashford"]
    assert files.txt.is_file()
    assert files.srt.is_file()


def test_hotwords_stay_inside_whispers_token_limit_for_unusual_terms(transcriber, tone):
    terms = tuple(f"Zyx{i}qué" if i % 2 == 0 else f"QXZ{i}K" for i in range(400))
    transcript = transcriber.transcribe(tone, Vocabulary(version=1, terms=terms))
    used = transcript.vocabulary_terms_used
    assert 0 < len(used) < 400
    assert used == terms[: len(used)]
    tokenizer = transcriber._model.hf_tokenizer
    encoded = tokenizer.encode(" " + ", ".join(used), add_special_tokens=False)
    assert len(encoded.ids) <= 223


def test_real_decoder_rejects_a_corrupt_file(transcriber, tmp_path):
    corrupt = tmp_path / "corrupt.mp3"
    corrupt.write_bytes(bytes(range(256)) * 64)
    with pytest.raises(UndecodableAudioError, match="corrupt.mp3"):
        transcriber.transcribe(corrupt)
