"""docker/fetch_models.py and docker/models.lock.json, checked without Docker or a network:
the script that bakes models into the follower image at build time."""

import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

DOCKER = Path(__file__).resolve().parents[3] / "docker"
LOCK = DOCKER / "models.lock.json"


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("fetch_models", DOCKER / "fetch_models.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["fetch_models"] = module
    spec.loader.exec_module(module)
    return module


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


FILES = {"config.json": b"{}", "model.bin": b"weights", "tokenizer.json": b"[]"}
ENTRY = {
    "repo": "Example/faster-whisper-test",
    "revision": "a" * 40,
    "files": {name: digest(data) for name, data in FILES.items()},
}


@pytest.fixture
def hub(tool, monkeypatch):
    """A stand-in for the Hugging Face download: writes `content` as the snapshot and records
    what was asked."""
    import huggingface_hub

    asked = []
    content = dict(FILES)

    def snapshot_download(repo, *, revision, cache_dir, allow_patterns):
        asked.append({"repo": repo, "revision": revision, "patterns": allow_patterns})
        snapshot = tool.cache_folder(Path(cache_dir), repo) / "snapshots" / revision
        snapshot.mkdir(parents=True)
        for name, data in content.items():
            (snapshot / name).write_bytes(data)
        return str(snapshot)

    monkeypatch.setattr(huggingface_hub, "snapshot_download", snapshot_download)
    return asked, content


def test_the_lock_file_pins_every_model_to_a_commit_and_a_checksum_per_file():
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    assert {"tiny.en", "distil-large-v3", "large-v3"} <= set(lock)
    for name, entry in lock.items():
        assert set(entry) == {"repo", "revision", "files"}, name
        assert re.fullmatch(r"[\w.-]+/[\w.-]+", entry["repo"]), name
        assert re.fullmatch(r"[0-9a-f]{40}", entry["revision"]), name  # a commit, not a branch
        assert {"config.json", "model.bin", "tokenizer.json"} <= set(entry["files"]), name
        for file, checksum in entry["files"].items():
            assert re.fullmatch(r"[0-9a-f]{64}", checksum), (name, file)


def test_the_names_the_follower_defaults_to_are_in_the_lock_file():
    from swarmscribe_engine import resolve_device

    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    assert resolve_device("cpu").model in lock
    assert resolve_device("cuda", cuda_available=lambda: True).model in lock


@pytest.mark.parametrize(
    ("given", "names"),
    [
        ("", []),
        ("tiny.en", ["tiny.en"]),
        ("large-v3,tiny.en", ["large-v3", "tiny.en"]),
        ("large-v3,tiny.en,large-v3", ["large-v3", "tiny.en"]),
    ],
)
def test_model_names_are_split_on_commas_in_order(tool, given, names):
    assert tool.names(given) == names


# The Dockerfile takes the text before the first comma as the start-up model
# (`${MODELS%%,*}`). Each of these was read differently by the two: the build passed and the
# image started on a model named "tiny.en tiny.en", or on a default it did not hold.
LOOSE = ["a b", "tiny.en tiny.en", " ", ",tiny.en", "tiny.en,", "tiny.en,,large-v3",
         " tiny.en", "tiny.en, large-v3", "tiny.en\t", "tiny.en\n"]


@pytest.mark.parametrize("given", LOOSE)
def test_spaces_and_empty_items_are_refused(tool, given):
    with pytest.raises(ValueError, match="commas"):
        tool.names(given)


@pytest.mark.parametrize("given", LOOSE)
def test_a_loose_list_fails_the_build_before_anything_is_fetched(
    tool, hub, tmp_path, capsys, given
):
    asked, _content = hub
    code = tool.main(["--lock", str(LOCK), "--into", str(tmp_path / "models"), "--", given])
    assert code == 2 and asked == []
    assert "MODELS" in capsys.readouterr().err


def start_up_model(models: str) -> str:
    """What the Dockerfile's `${MODELS%%,*}` gives: the text before the first comma."""
    return models.split(",", 1)[0]


@pytest.mark.parametrize("given", ["", "tiny.en", "large-v3,tiny.en", "large-v3,tiny.en,large-v3"])
def test_the_dockerfile_and_the_fetcher_agree_on_the_first_model(tool, given):
    assert start_up_model(given) == (tool.names(given) or [""])[0]
    line = "    SWARMSCRIBE_FOLLOWER_STARTUP_MODEL=${MODELS%%,*} \\\n"
    assert line in (DOCKER / "follower.Dockerfile").read_text(encoding="utf-8")


@pytest.mark.parametrize("option", ["--pin=tiny.en", "--lock=/etc/passwd", "--into=/", "-h"])
def test_after_two_dashes_an_option_is_only_a_name_that_is_not_in_the_lock(
    tool, hub, tmp_path, capsys, monkeypatch, option
):
    """The Dockerfile passes MODELS after `--`: a build argument can never be an option."""
    asked, _content = hub
    monkeypatch.setattr(tool, "pin", lambda name, repo: pytest.fail("an unpinned fetch"))
    code = tool.main(["--lock", str(LOCK), "--into", str(tmp_path / "models"), "--", option])
    assert code == 2 and asked == []
    assert "not in models.lock.json" in capsys.readouterr().err
    text = (DOCKER / "follower.Dockerfile").read_text(encoding="utf-8")
    assert '--lock /fetch/models.lock.json --into /models -- "${MODELS}"' in text


def test_a_model_is_downloaded_by_commit_checked_and_made_loadable_offline(tool, hub, tmp_path):
    asked, _content = hub
    tool.fetch("test", ENTRY, tmp_path)
    assert asked == [
        {"repo": ENTRY["repo"], "revision": "a" * 40, "patterns": sorted(FILES)}
    ]
    folder = tmp_path / "models--Example--faster-whisper-test"
    # The loader asks for `main`; a download by commit does not record which commit that is.
    assert (folder / "refs" / "main").read_text(encoding="utf-8") == "a" * 40
    assert (folder / "snapshots" / ("a" * 40) / "model.bin").read_bytes() == b"weights"


@pytest.mark.parametrize(
    ("change", "said"),
    [
        ({"model.bin": b"other weights"}, "model.bin has SHA-256"),
        ({"model.bin": None}, "model.bin is missing"),
        ({"extra.bin": b"more"}, "extra.bin is not in the lock file"),
    ],
)
def test_a_file_that_differs_is_missing_or_is_extra_fails_the_build(
    tool, hub, tmp_path, change, said
):
    _asked, content = hub
    for name, data in change.items():
        if data is None:
            del content[name]
        else:
            content[name] = data
    with pytest.raises(SystemExit) as stop:
        tool.fetch("test", ENTRY, tmp_path)
    assert said in str(stop.value) and "Example/faster-whisper-test@" in str(stop.value)
    assert not (tmp_path / "models--Example--faster-whisper-test" / "refs").exists()


def test_a_model_that_is_not_in_the_lock_file_is_refused_before_anything_is_fetched(
    tool, hub, tmp_path, capsys
):
    asked, _content = hub
    code = tool.main(["--lock", str(LOCK), "--into", str(tmp_path / "models"), "tiny.en,nope"])
    assert code == 2 and asked == []
    said = capsys.readouterr().err
    assert "nope" in said and "--pin" in said and "tiny.en" in said


def test_no_model_leaves_an_empty_folder_for_the_image_to_copy(tool, hub, tmp_path, capsys):
    asked, _content = hub
    into = tmp_path / "models"
    assert tool.main(["--lock", str(LOCK), "--into", str(into), ""]) == 0
    assert into.is_dir() and list(into.iterdir()) == [] and asked == []
    assert "no model baked" in capsys.readouterr().out


def test_the_models_asked_for_are_all_fetched_from_the_lock(tool, hub, tmp_path):
    asked, _content = hub
    lock = tmp_path / "lock.json"
    lock.write_text(json.dumps({"one": ENTRY, "two": {**ENTRY, "repo": "Example/two"}}))
    assert tool.main(["--lock", str(lock), "--into", str(tmp_path / "models"), "two,one"]) == 0
    assert [call["repo"] for call in asked] == ["Example/two", "Example/faster-whisper-test"]


@pytest.mark.parametrize("revision", ["main", "v1.0", "a" * 39, "A" * 40])
def test_a_branch_or_tag_is_never_a_revision(tool, hub, tmp_path, revision):
    asked, _content = hub
    with pytest.raises(SystemExit) as stop:
        tool.fetch("test", {**ENTRY, "revision": revision}, tmp_path)
    assert "not a full commit hash" in str(stop.value) and asked == []


def test_a_failed_check_leaves_no_download_that_looks_complete(tool, hub, tmp_path):
    _asked, content = hub
    content["model.bin"] = b"truncated"
    with pytest.raises(SystemExit):
        tool.fetch("test", ENTRY, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_pin_says_what_it_would_change_for_review(tool):
    assert tool.changes("m", None, ENTRY)[0].startswith("m: new entry (Example/")
    assert tool.changes("m", ENTRY, ENTRY) == ["m: unchanged"]
    newer = {
        "repo": ENTRY["repo"],
        "revision": "b" * 40,
        "files": {**ENTRY["files"], "model.bin": "0" * 64, "extra.txt": "1" * 64},
    }
    del newer["files"]["tokenizer.json"]
    said = tool.changes("m", ENTRY, newer)
    assert f"m: revision {'a' * 40} -> {'b' * 40}" in said
    assert any(line.startswith("m: model.bin SHA-256") for line in said)
    assert any(line.startswith("m: extra.txt added") for line in said)
    assert "m: tokenizer.json removed" in said


def test_pin_prints_the_entry_and_the_changes(tool, monkeypatch, capsys):
    monkeypatch.setattr(tool, "pin", lambda name, repo: {name: ENTRY})
    assert tool.main(["--pin", "tiny.en", "--lock", str(LOCK)]) == 0
    seen = capsys.readouterr()
    assert json.loads(seen.out)["tiny.en"] == ENTRY
    assert "tiny.en: repo Systran/faster-whisper-tiny.en -> Example/" in seen.err
