import json

import pytest
from swarmscribe_engine import ENGINE_VERSION, AppliedCorrection, Segment, Word, write_outputs
from swarmscribe_engine.writers import format_srt_time, render_srt, render_txt
from swarmscribe_protocol import SegmentsDocument


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0.0, "00:00:00,000"),
        (1.5, "00:00:01,500"),
        (59.9996, "00:01:00,000"),
        (3723.004, "01:02:03,004"),
        (360000.0, "100:00:00,000"),
        (-0.2, "00:00:00,000"),
    ],
)
def test_format_srt_time(seconds, expected):
    assert format_srt_time(seconds) == expected


def test_txt_is_one_segment_per_line(make_transcript):
    assert render_txt(make_transcript()) == "Welcome to Ashford.\nThanks for joining.\n"


def test_srt_blocks_are_numbered_from_one_and_blank_line_separated(make_transcript):
    assert render_srt(make_transcript()) == (
        "1\n00:00:00,000 --> 00:00:01,500\nWelcome to Ashford.\n"
        "\n"
        "2\n00:00:02,000 --> 00:00:03,250\nThanks for joining.\n"
    )


def test_outputs_are_named_after_the_source_file(make_transcript, tmp_path):
    files = write_outputs(make_transcript(), tmp_path)
    assert files.txt == tmp_path / "recording.mp3.txt"
    assert files.srt == tmp_path / "recording.mp3.srt"
    assert files.segments_json == tmp_path / "recording.mp3.segments.json"
    assert all(path.is_file() for path in (files.txt, files.srt, files.segments_json))


def test_output_directory_is_created(make_transcript, tmp_path):
    out_dir = tmp_path / "2024" / "march"
    assert write_outputs(make_transcript(), out_dir).txt.is_file()


def test_no_temp_files_are_left_behind(make_transcript, tmp_path):
    write_outputs(make_transcript(), tmp_path)
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "recording.mp3.segments.json",
        "recording.mp3.srt",
        "recording.mp3.txt",
    ]


def test_segments_json_conforms_to_the_protocol_schema(make_transcript, tmp_path):
    files = write_outputs(make_transcript(), tmp_path)
    document = SegmentsDocument.model_validate_json(files.segments_json.read_text("utf-8"))
    assert document.schema_version == 1
    assert document.source_checksum == "a" * 64
    assert document.duration == 3.25
    assert document.device == "cuda"
    assert document.engine_version == ENGINE_VERSION
    assert document.settings.model == "large-v3"
    assert document.settings.compute_type == "float16"
    assert document.settings.temperatures == (0.0, 0.2, 0.4)
    assert document.vocabulary_version == 1
    assert document.vocabulary_terms_used == ["Ashford"]
    assert document.corrections_applied == []
    assert document.segments[0].words[2].word == " Ashford."
    assert document.segments[0].words[2].probability == 0.71


def test_segments_json_records_every_fixed_setting(make_transcript, tmp_path):
    files = write_outputs(make_transcript(), tmp_path)
    settings = json.loads(files.segments_json.read_text("utf-8"))["settings"]
    assert settings == {
        "model": "large-v3",
        "compute_type": "float16",
        "language": "en",
        "condition_on_previous_text": False,
        "temperatures": [0.0, 0.2, 0.4],
        "vad_filter": True,
        "word_timestamps": True,
    }


def test_no_speech_still_writes_all_three_outputs(make_transcript, tmp_path):
    files = write_outputs(make_transcript(segments=()), tmp_path)
    assert files.txt.read_text("utf-8") == ""
    assert files.srt.read_text("utf-8") == ""
    document = SegmentsDocument.model_validate_json(files.segments_json.read_text("utf-8"))
    assert document.segments == []


def test_non_ascii_text_is_written_as_utf8(make_transcript, tmp_path):
    text = "José said “hello”."
    transcript = make_transcript(
        segments=(Segment(start=0.0, end=1.0, text=text, words=()),),
        vocabulary_terms_used=("José",),
    )
    files = write_outputs(transcript, tmp_path)
    assert files.txt.read_bytes() == (text + "\n").encode("utf-8")
    assert text.encode("utf-8") in files.srt.read_bytes()
    assert text.encode("utf-8") in files.segments_json.read_bytes()


def test_corrections_and_originals_are_recorded(make_transcript, tmp_path):
    corrected = Segment(
        start=0.0,
        end=2.0,
        text="Thanks Jason.",
        words=(
            Word(start=0.0, end=0.5, word=" Thanks", probability=0.9),
            Word(start=0.5, end=2.0, word=" Jason.", probability=0.3, original=" jay son."),
        ),
    )
    transcript = make_transcript(
        segments=(corrected,),
        vocabulary_version=5,
        corrections_applied=(AppliedCorrection(heard="jay son", replacement="Jason", count=1),),
    )
    files = write_outputs(transcript, tmp_path)

    raw = json.loads(files.segments_json.read_text("utf-8"))
    assert raw["vocabulary_version"] == 5
    assert raw["corrections_applied"] == [{"heard": "jay son", "replacement": "Jason", "count": 1}]
    first, second = raw["segments"][0]["words"]
    assert "original" not in first
    assert second["original"] == " jay son."
    assert "glossary" not in raw

    document = SegmentsDocument.model_validate_json(files.segments_json.read_text("utf-8"))
    assert document.segments[0].words[0].original is None
    assert document.segments[0].words[1].original == " jay son."
    assert files.txt.read_text("utf-8") == "Thanks Jason.\n"


def test_segments_json_declares_every_field_it_writes(make_transcript, tmp_path):
    corrected = Segment(
        start=0.0,
        end=2.0,
        text="Thanks Jason.",
        words=(
            Word(start=0.0, end=0.5, word=" Thanks", probability=0.9),
            Word(start=0.5, end=2.0, word=" Jason.", probability=0.3, original=" jay son."),
        ),
    )
    transcript = make_transcript(
        segments=(corrected,),
        corrections_applied=(AppliedCorrection(heard="jay son", replacement="Jason", count=1),),
    )
    raw = write_outputs(transcript, tmp_path).segments_json.read_text("utf-8")
    # The protocol models ignore unknown fields, so a round trip drops anything undeclared.
    # exclude_unset: fields with protocol defaults (channel_mode, channel_labels) that the
    # writer deliberately omits for a mono transcript must not count as dropped.
    document = SegmentsDocument.model_validate_json(raw)
    assert json.loads(raw) == json.loads(
        document.model_dump_json(exclude_none=True, exclude_unset=True)
    )


def test_a_non_finite_number_is_an_error_not_invalid_json(make_transcript, tmp_path):
    with pytest.raises(ValueError):
        write_outputs(make_transcript(duration=float("nan")), tmp_path)
    assert not (tmp_path / "recording.mp3.segments.json").exists()


def test_line_endings_are_lf_on_every_platform(make_transcript, tmp_path):
    files = write_outputs(make_transcript(), tmp_path)
    for path in (files.txt, files.srt, files.segments_json):
        assert b"\r\n" not in path.read_bytes()


def test_rewriting_replaces_existing_outputs(make_transcript, tmp_path):
    write_outputs(make_transcript(), tmp_path)
    files = write_outputs(make_transcript(segments=()), tmp_path)
    assert files.txt.read_text("utf-8") == ""


PINNED_SEGMENTS_JSON = """{
  "schema_version": 1,
  "source_checksum": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "duration": 3.25,
  "device": "cuda",
  "engine_version": "@ENGINE_VERSION@",
  "settings": {
    "model": "large-v3",
    "compute_type": "float16",
    "language": "en",
    "condition_on_previous_text": false,
    "vad_filter": true,
    "word_timestamps": true,
    "temperatures": [
      0.0,
      0.2,
      0.4
    ]
  },
  "vocabulary_version": 5,
  "vocabulary_terms_used": [
    "Ashford"
  ],
  "corrections_applied": [
    {
      "heard": "jay son",
      "replacement": "Jason",
      "count": 1
    }
  ],
  "segments": [
    {
      "start": 0.0,
      "end": 1.5,
      "text": "Welcome to Ashford.",
      "words": [
        {
          "start": 0.0,
          "end": 0.4,
          "word": " Welcome",
          "probability": 0.98
        },
        {
          "start": 0.4,
          "end": 0.6,
          "word": " to",
          "probability": 0.99
        },
        {
          "start": 0.6,
          "end": 1.5,
          "word": " Ashford.",
          "probability": 0.71
        }
      ]
    },
    {
      "start": 2.0,
      "end": 3.25,
      "text": "Thanks Jason.",
      "words": [
        {
          "start": 2.0,
          "end": 2.5,
          "word": " Thanks",
          "probability": 0.9
        },
        {
          "start": 2.5,
          "end": 3.25,
          "word": " Jason.",
          "probability": 0.3,
          "original": " jay son."
        }
      ]
    }
  ]
}
"""


def test_mono_outputs_are_pinned_byte_for_byte(make_transcript, tmp_path):
    # Written before per-channel transcription existed; splitting must not change one byte.
    segments = (
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
            text="Thanks Jason.",
            words=(
                Word(start=2.0, end=2.5, word=" Thanks", probability=0.9),
                Word(start=2.5, end=3.25, word=" Jason.", probability=0.3, original=" jay son."),
            ),
        ),
    )
    transcript = make_transcript(
        segments=segments,
        vocabulary_version=5,
        corrections_applied=(AppliedCorrection(heard="jay son", replacement="Jason", count=1),),
    )
    files = write_outputs(transcript, tmp_path)
    assert files.txt.read_bytes() == b"Welcome to Ashford.\nThanks Jason.\n"
    assert files.srt.read_bytes() == (
        b"1\n00:00:00,000 --> 00:00:01,500\nWelcome to Ashford.\n"
        b"\n"
        b"2\n00:00:02,000 --> 00:00:03,250\nThanks Jason.\n"
    )
    expected = PINNED_SEGMENTS_JSON.replace("@ENGINE_VERSION@", ENGINE_VERSION)
    assert files.segments_json.read_bytes() == expected.encode("utf-8")
