import pytest
from swarmscribe_engine import (
    Correction,
    DeviceChoice,
    DeviceUnavailableError,
    TranscribeSettings,
    UndecodableAudioError,
    Vocabulary,
)
from swarmscribe_engine.cli import read_corrections, read_terms, run

CPU = DeviceChoice(device="cpu", model="distil-large-v3", compute_type="int8")


def _recording(tmp_path):
    path = tmp_path / "recording.mp3"
    path.write_bytes(b"fake")
    return path


def test_run_writes_outputs_and_prints_their_paths(tmp_path, make_transcript, capsys):
    recording = _recording(tmp_path)
    out_dir = tmp_path / "out"
    calls = []

    def fake_transcribe(path, settings, vocabulary):
        calls.append((path, settings, vocabulary))
        return make_transcript(settings=settings)

    code = run(
        [str(recording), "--out", str(out_dir)],
        transcribe_fn=fake_transcribe,
        resolve_fn=lambda preference: CPU,
    )

    assert code == 0
    assert calls == [
        (
            recording,
            TranscribeSettings(model="distil-large-v3", compute_type="int8", device="cpu"),
            Vocabulary(),
        )
    ]
    assert (out_dir / "recording.mp3.segments.json").is_file()
    printed = capsys.readouterr().out
    assert "recording.mp3.txt" in printed
    assert "recording.mp3.srt" in printed
    assert "recording.mp3.segments.json" in printed
    assert "Welcome to Ashford" not in printed


def test_device_preference_is_forwarded(tmp_path, make_transcript):
    seen = []

    def resolve(preference):
        seen.append(preference)
        return CPU

    run(
        [str(_recording(tmp_path)), "--out", str(tmp_path), "--device", "cpu"],
        transcribe_fn=lambda path, settings, vocabulary: make_transcript(),
        resolve_fn=resolve,
    )
    assert seen == ["cpu"]


def test_model_and_compute_type_can_be_overridden(tmp_path, make_transcript):
    seen = []

    def fake_transcribe(path, settings, vocabulary):
        seen.append(settings)
        return make_transcript()

    run(
        [
            str(_recording(tmp_path)),
            "--out",
            str(tmp_path),
            "--model",
            "tiny.en",
            "--compute-type",
            "float32",
        ],
        transcribe_fn=fake_transcribe,
        resolve_fn=lambda preference: CPU,
    )
    assert seen == [TranscribeSettings(model="tiny.en", compute_type="float32", device="cpu")]


def test_vocabulary_file_is_read_and_passed(tmp_path, make_transcript):
    vocabulary = tmp_path / "vocabulary.txt"
    vocabulary.write_text("# people\nJosé\n\n  Ashford  \n", encoding="utf-8")
    seen = []

    def fake_transcribe(path, settings, vocabulary):
        seen.append(vocabulary)
        return make_transcript()

    run(
        [str(_recording(tmp_path)), "--out", str(tmp_path), "--vocabulary", str(vocabulary)],
        transcribe_fn=fake_transcribe,
        resolve_fn=lambda preference: CPU,
    )
    assert seen == [Vocabulary(terms=("José", "Ashford"))]


def test_vocabulary_saved_by_notepad_with_a_bom_is_read_cleanly(tmp_path):
    vocabulary = tmp_path / "vocabulary.txt"
    vocabulary.write_bytes(b"\xef\xbb\xbfAshford\r\nJos\xc3\xa9\r\n")
    assert read_terms(vocabulary) == ("Ashford", "José")


def test_missing_recording_exits_2_with_a_message(tmp_path, capsys):
    def fake_transcribe(path, settings, vocabulary):
        raise FileNotFoundError(f"recording not found: {path}")

    code = run(
        [str(tmp_path / "missing.mp3"), "--out", str(tmp_path)],
        transcribe_fn=fake_transcribe,
        resolve_fn=lambda preference: CPU,
    )
    assert code == 2
    assert "error: recording not found" in capsys.readouterr().err


def test_missing_vocabulary_file_exits_2_with_a_message(tmp_path, make_transcript, capsys):
    code = run(
        [
            str(_recording(tmp_path)),
            "--out",
            str(tmp_path),
            "--vocabulary",
            str(tmp_path / "nope.txt"),
        ],
        transcribe_fn=lambda path, settings, vocabulary: make_transcript(),
        resolve_fn=lambda preference: CPU,
    )
    assert code == 2
    err = capsys.readouterr().err
    assert "error:" in err
    assert "nope.txt" in err


def test_undecodable_audio_exits_2_and_writes_nothing(tmp_path, capsys):
    out_dir = tmp_path / "out"

    def fake_transcribe(path, settings, vocabulary):
        raise UndecodableAudioError("cannot decode recording.mp3: Invalid data")

    code = run(
        [str(_recording(tmp_path)), "--out", str(out_dir)],
        transcribe_fn=fake_transcribe,
        resolve_fn=lambda preference: CPU,
    )
    assert code == 2
    assert "error: cannot decode recording.mp3" in capsys.readouterr().err
    assert not out_dir.exists()


def test_unavailable_device_exits_2(tmp_path, make_transcript, capsys):
    def resolve(preference):
        raise DeviceUnavailableError("cuda was requested but no CUDA GPU is available")

    code = run(
        [str(_recording(tmp_path)), "--out", str(tmp_path), "--device", "cuda"],
        transcribe_fn=lambda path, settings, vocabulary: make_transcript(),
        resolve_fn=resolve,
    )
    assert code == 2
    assert "no CUDA GPU" in capsys.readouterr().err


def test_vocabulary_that_is_not_utf8_exits_2_without_a_traceback(tmp_path, make_transcript, capsys):
    vocabulary = tmp_path / "vocabulary.txt"
    vocabulary.write_bytes(b"\xff\xfe\x00bad")
    code = run(
        [str(_recording(tmp_path)), "--out", str(tmp_path), "--vocabulary", str(vocabulary)],
        transcribe_fn=lambda path, settings, vocabulary: make_transcript(),
        resolve_fn=lambda preference: CPU,
    )
    assert code == 2
    err = capsys.readouterr().err
    assert err.startswith("error:")
    assert "Traceback" not in err


def test_out_pointing_at_a_regular_file_exits_2(tmp_path, make_transcript, capsys):
    not_a_directory = tmp_path / "taken"
    not_a_directory.write_text("occupied", encoding="utf-8")
    code = run(
        [str(_recording(tmp_path)), "--out", str(not_a_directory)],
        transcribe_fn=lambda path, settings, vocabulary: make_transcript(),
        resolve_fn=lambda preference: CPU,
    )
    assert code == 2
    assert "error:" in capsys.readouterr().err


def test_invalid_model_size_exits_2_with_the_message(tmp_path, capsys):
    def fake_transcribe(path, settings, vocabulary):
        raise ValueError("Invalid model size 'nope'")

    code = run(
        [str(_recording(tmp_path)), "--out", str(tmp_path)],
        transcribe_fn=fake_transcribe,
        resolve_fn=lambda preference: CPU,
    )
    assert code == 2
    assert "Invalid model size 'nope'" in capsys.readouterr().err


def test_runtime_failure_exits_2_with_the_message(tmp_path, capsys):
    def fake_transcribe(path, settings, vocabulary):
        raise RuntimeError("CUDA out of memory")

    code = run(
        [str(_recording(tmp_path)), "--out", str(tmp_path)],
        transcribe_fn=fake_transcribe,
        resolve_fn=lambda preference: CPU,
    )
    assert code == 2
    assert "CUDA out of memory" in capsys.readouterr().err


def test_corrections_file_is_read_and_passed(tmp_path, make_transcript):
    corrections = tmp_path / "corrections.txt"
    corrections.write_text(
        "# names\njay son => Jason\n\n  ashferd=>Ashford  \n", encoding="utf-8"
    )
    seen = []

    def fake_transcribe(path, settings, vocabulary):
        seen.append(vocabulary)
        return make_transcript()

    code = run(
        [str(_recording(tmp_path)), "--out", str(tmp_path), "--corrections", str(corrections)],
        transcribe_fn=fake_transcribe,
        resolve_fn=lambda preference: CPU,
    )
    assert code == 0
    assert seen == [
        Vocabulary(
            corrections=(Correction("jay son", "Jason"), Correction("ashferd", "Ashford"))
        )
    ]


def test_vocabulary_and_corrections_can_be_given_together(tmp_path, make_transcript):
    vocabulary = tmp_path / "vocabulary.txt"
    vocabulary.write_text("Ashford\n", encoding="utf-8")
    corrections = tmp_path / "corrections.txt"
    corrections.write_text("ashferd => Ashford\n", encoding="utf-8")
    seen = []

    def fake_transcribe(path, settings, vocab):
        seen.append(vocab)
        return make_transcript()

    run(
        [
            str(_recording(tmp_path)),
            "--out",
            str(tmp_path),
            "--vocabulary",
            str(vocabulary),
            "--corrections",
            str(corrections),
        ],
        transcribe_fn=fake_transcribe,
        resolve_fn=lambda preference: CPU,
    )
    assert seen == [
        Vocabulary(version=0, terms=("Ashford",), corrections=(Correction("ashferd", "Ashford"),))
    ]


def test_corrections_saved_by_notepad_with_a_bom_and_crlf_are_read_cleanly(tmp_path):
    corrections = tmp_path / "corrections.txt"
    corrections.write_bytes(b"\xef\xbb\xbfjose => Jos\xc3\xa9\r\njay son => Jason\r\n")
    assert read_corrections(corrections) == (
        Correction("jose", "José"),
        Correction("jay son", "Jason"),
    )


def test_a_replacement_may_itself_contain_an_arrow(tmp_path):
    corrections = tmp_path / "corrections.txt"
    corrections.write_text("a to b => a => b\n", encoding="utf-8")
    assert read_corrections(corrections) == (Correction("a to b", "a => b"),)


@pytest.mark.parametrize("bad_line", ["jay son Jason", "=> Jason", "jay son =>", "   =>   "])
def test_a_malformed_corrections_line_names_the_file_and_line(tmp_path, bad_line):
    corrections = tmp_path / "corrections.txt"
    corrections.write_text(f"# header\nashferd => Ashford\n{bad_line}\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"corrections\.txt line 3"):
        read_corrections(corrections)


def test_a_malformed_corrections_file_exits_2_without_a_traceback(
    tmp_path, make_transcript, capsys
):
    corrections = tmp_path / "corrections.txt"
    corrections.write_text("jay son Jason\n", encoding="utf-8")
    code = run(
        [str(_recording(tmp_path)), "--out", str(tmp_path), "--corrections", str(corrections)],
        transcribe_fn=lambda path, settings, vocabulary: make_transcript(),
        resolve_fn=lambda preference: CPU,
    )
    err = capsys.readouterr().err
    assert code == 2
    assert err.startswith("error: corrections.txt line 1")
    assert "Traceback" not in err


def test_the_glossary_flag_is_gone(tmp_path):
    with pytest.raises(SystemExit):
        run(
            [str(_recording(tmp_path)), "--out", str(tmp_path), "--glossary", "x.txt"],
            transcribe_fn=None,
            resolve_fn=lambda preference: CPU,
        )


@pytest.mark.parametrize("bad_line", ["-- => —", "& => and", "jay -- son => Jason"])
def test_a_heard_side_with_a_part_that_has_no_word_is_rejected(tmp_path, bad_line):
    corrections = tmp_path / "corrections.txt"
    corrections.write_text(f"# header\nashferd => Ashford\n{bad_line}\n", encoding="utf-8")
    with pytest.raises(
        ValueError,
        match=r"corrections\.txt line 3: 'heard as' must contain a word in every part",
    ):
        read_corrections(corrections)


def test_the_same_heard_with_a_different_replacement_is_rejected(tmp_path):
    corrections = tmp_path / "corrections.txt"
    corrections.write_text("Jay  Son => Jason\n# note\njay son => Jayson\n", encoding="utf-8")
    with pytest.raises(
        ValueError, match=r"corrections\.txt line 3: 'jay son' is already defined on line 1"
    ):
        read_corrections(corrections)


def test_an_exact_duplicate_line_is_dropped_keeping_the_first(tmp_path):
    corrections = tmp_path / "corrections.txt"
    corrections.write_text(
        "Jay  Son => Jason\nashferd => Ashford\njay son => Jason\n", encoding="utf-8"
    )
    assert read_corrections(corrections) == (
        Correction("Jay  Son", "Jason"),
        Correction("ashferd", "Ashford"),
    )


def _settings_seen(tmp_path, make_transcript, *flags):
    seen = []

    def fake_transcribe(path, settings, vocabulary):
        seen.append(settings)
        return make_transcript()

    code = run(
        [str(_recording(tmp_path)), "--out", str(tmp_path), *flags],
        transcribe_fn=fake_transcribe,
        resolve_fn=lambda preference: CPU,
    )
    return code, seen


def test_channels_default_to_mono(tmp_path, make_transcript):
    code, seen = _settings_seen(tmp_path, make_transcript)
    assert code == 0
    assert (seen[0].channel_mode, seen[0].channel_labels) == ("mono", ("Left", "Right"))


def test_stereo_split_with_labels_reaches_the_engine(tmp_path, make_transcript):
    code, seen = _settings_seen(
        tmp_path, make_transcript, "--channels", "stereo-split", "--labels", "Agent, Customer"
    )
    assert code == 0
    assert seen == [
        TranscribeSettings(
            model="distil-large-v3",
            compute_type="int8",
            device="cpu",
            channel_mode="stereo_split",
            channel_labels=("Agent", "Customer"),
        )
    ]


def test_auto_keeps_the_default_labels(tmp_path, make_transcript):
    code, seen = _settings_seen(tmp_path, make_transcript, "--channels", "auto")
    assert code == 0
    assert (seen[0].channel_mode, seen[0].channel_labels) == ("auto", ("Left", "Right"))


def test_labels_without_a_split_mode_are_a_usage_error(tmp_path, capsys):
    with pytest.raises(SystemExit) as excinfo:
        run(
            [str(_recording(tmp_path)), "--out", str(tmp_path), "--labels", "Agent,Customer"],
            transcribe_fn=None,
            resolve_fn=lambda preference: CPU,
        )
    assert excinfo.value.code == 2
    assert "--labels needs --channels stereo-split or auto" in capsys.readouterr().err


@pytest.mark.parametrize("labels", ["Agent", "Agent,Customer,Supervisor", ""])
def test_labels_must_be_two_comma_separated_names(tmp_path, capsys, labels):
    with pytest.raises(SystemExit) as excinfo:
        run(
            [
                str(_recording(tmp_path)),
                "--out",
                str(tmp_path),
                "--channels",
                "auto",
                "--labels",
                labels,
            ],
            transcribe_fn=None,
            resolve_fn=lambda preference: CPU,
        )
    assert excinfo.value.code == 2
    assert "exactly two names" in capsys.readouterr().err


def test_an_unusable_label_exits_2_with_a_message(tmp_path, make_transcript, capsys):
    code = run(
        [
            str(_recording(tmp_path)),
            "--out",
            str(tmp_path),
            "--channels",
            "auto",
            "--labels",
            "Agent," + "x" * 41,
        ],
        transcribe_fn=lambda path, settings, vocabulary: make_transcript(),
        resolve_fn=lambda preference: CPU,
    )
    assert code == 2
    err = capsys.readouterr().err
    assert err.startswith("error: channel labels must be 1 to 40 characters")
    assert "Traceback" not in err


def test_an_unknown_channel_mode_is_a_usage_error(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        run(
            [str(_recording(tmp_path)), "--out", str(tmp_path), "--channels", "stereo"],
            transcribe_fn=None,
            resolve_fn=lambda preference: CPU,
        )
    assert excinfo.value.code == 2


def test_a_mono_file_in_stereo_split_exits_2_and_writes_nothing(tmp_path, capsys):
    out_dir = tmp_path / "out"

    def fake_transcribe(path, settings, vocabulary):
        raise UndecodableAudioError(
            "recording.mp3 has 1 audio channel; stereo-split (stereo_split) needs a "
            "two-channel (stereo) recording"
        )

    code = run(
        [str(_recording(tmp_path)), "--out", str(out_dir), "--channels", "stereo-split"],
        transcribe_fn=fake_transcribe,
        resolve_fn=lambda preference: CPU,
    )
    assert code == 2
    assert "error: recording.mp3 has 1 audio channel" in capsys.readouterr().err
    assert not out_dir.exists()
