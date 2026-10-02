from swarmscribe_engine import (
    DeviceChoice,
    DeviceUnavailableError,
    TranscribeSettings,
    UndecodableAudioError,
)
from swarmscribe_engine.cli import read_glossary, run

CPU = DeviceChoice(device="cpu", model="distil-large-v3", compute_type="int8")


def _recording(tmp_path):
    path = tmp_path / "recording.mp3"
    path.write_bytes(b"fake")
    return path


def test_run_writes_outputs_and_prints_their_paths(tmp_path, make_transcript, capsys):
    recording = _recording(tmp_path)
    out_dir = tmp_path / "out"
    calls = []

    def fake_transcribe(path, settings, glossary):
        calls.append((path, settings, glossary))
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
            (),
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
        transcribe_fn=lambda path, settings, glossary: make_transcript(),
        resolve_fn=resolve,
    )
    assert seen == ["cpu"]


def test_model_and_compute_type_can_be_overridden(tmp_path, make_transcript):
    seen = []

    def fake_transcribe(path, settings, glossary):
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


def test_glossary_file_is_read_and_passed(tmp_path, make_transcript):
    glossary = tmp_path / "glossary.txt"
    glossary.write_text("# people\nJosé\n\n  Ashford  \n", encoding="utf-8")
    seen = []

    def fake_transcribe(path, settings, terms):
        seen.append(terms)
        return make_transcript()

    run(
        [str(_recording(tmp_path)), "--out", str(tmp_path), "--glossary", str(glossary)],
        transcribe_fn=fake_transcribe,
        resolve_fn=lambda preference: CPU,
    )
    assert seen == [("José", "Ashford")]


def test_glossary_saved_by_notepad_with_a_bom_is_read_cleanly(tmp_path):
    glossary = tmp_path / "glossary.txt"
    glossary.write_bytes(b"\xef\xbb\xbfAshford\r\nJos\xc3\xa9\r\n")
    assert read_glossary(glossary) == ("Ashford", "José")


def test_missing_recording_exits_2_with_a_message(tmp_path, capsys):
    def fake_transcribe(path, settings, glossary):
        raise FileNotFoundError(f"recording not found: {path}")

    code = run(
        [str(tmp_path / "missing.mp3"), "--out", str(tmp_path)],
        transcribe_fn=fake_transcribe,
        resolve_fn=lambda preference: CPU,
    )
    assert code == 2
    assert "error: recording not found" in capsys.readouterr().err


def test_missing_glossary_file_exits_2_with_a_message(tmp_path, make_transcript, capsys):
    code = run(
        [
            str(_recording(tmp_path)),
            "--out",
            str(tmp_path),
            "--glossary",
            str(tmp_path / "nope.txt"),
        ],
        transcribe_fn=lambda path, settings, glossary: make_transcript(),
        resolve_fn=lambda preference: CPU,
    )
    assert code == 2
    err = capsys.readouterr().err
    assert "error:" in err
    assert "nope.txt" in err


def test_undecodable_audio_exits_2_and_writes_nothing(tmp_path, capsys):
    out_dir = tmp_path / "out"

    def fake_transcribe(path, settings, glossary):
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
        transcribe_fn=lambda path, settings, glossary: make_transcript(),
        resolve_fn=resolve,
    )
    assert code == 2
    assert "no CUDA GPU" in capsys.readouterr().err


def test_glossary_that_is_not_utf8_exits_2_without_a_traceback(tmp_path, make_transcript, capsys):
    glossary = tmp_path / "glossary.txt"
    glossary.write_bytes(b"\xff\xfe\x00bad")
    code = run(
        [str(_recording(tmp_path)), "--out", str(tmp_path), "--glossary", str(glossary)],
        transcribe_fn=lambda path, settings, glossary: make_transcript(),
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
        transcribe_fn=lambda path, settings, glossary: make_transcript(),
        resolve_fn=lambda preference: CPU,
    )
    assert code == 2
    assert "error:" in capsys.readouterr().err


def test_invalid_model_size_exits_2_with_the_message(tmp_path, capsys):
    def fake_transcribe(path, settings, glossary):
        raise ValueError("Invalid model size 'nope'")

    code = run(
        [str(_recording(tmp_path)), "--out", str(tmp_path)],
        transcribe_fn=fake_transcribe,
        resolve_fn=lambda preference: CPU,
    )
    assert code == 2
    assert "Invalid model size 'nope'" in capsys.readouterr().err


def test_runtime_failure_exits_2_with_the_message(tmp_path, capsys):
    def fake_transcribe(path, settings, glossary):
        raise RuntimeError("CUDA out of memory")

    code = run(
        [str(_recording(tmp_path)), "--out", str(tmp_path)],
        transcribe_fn=fake_transcribe,
        resolve_fn=lambda preference: CPU,
    )
    assert code == 2
    assert "CUDA out of memory" in capsys.readouterr().err
