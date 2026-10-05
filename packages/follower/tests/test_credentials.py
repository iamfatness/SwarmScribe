import json
import os
import threading

import pytest
from swarmscribe_follower.credentials import CredentialFileError, CredentialStore, Stored

STORED = Stored(
    leader_url="https://leader.example.org",
    follower_id="2f0d1c1e-0000-4000-8000-000000000001",
    credential="c" * 43,
    device="cpu",
    heartbeat_interval=30,
    lease_seconds=120,
)
posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")


def test_nothing_stored_is_none(tmp_path):
    assert CredentialStore(tmp_path / "state" / "credential.json").load() is None


def test_a_saved_credential_comes_back_and_its_repr_hides_it(tmp_path):
    store = CredentialStore(tmp_path / "state" / "credential.json")
    store.save(STORED)
    assert store.load() == STORED
    assert STORED.credential not in repr(store.load())
    assert not (tmp_path / "state" / "credential.json.new").exists()


def test_saving_again_replaces_the_file(tmp_path):
    store = CredentialStore(tmp_path / "credential.json")
    store.save(STORED)
    (tmp_path / "credential.json.new").write_text("left by a crash", encoding="utf-8")
    newer = Stored(**{**STORED.__dict__, "credential": "d" * 43})
    store.save(newer)
    assert store.load() == newer


def test_delete_says_whether_there_was_one(tmp_path):
    store = CredentialStore(tmp_path / "credential.json")
    assert store.delete() is False
    store.save(STORED)
    assert store.delete() is True
    assert store.load() is None


@pytest.mark.parametrize(
    "content",
    [
        "",
        "not json",
        "[]",
        json.dumps({"credential": "x"}),
        json.dumps({**STORED.__dict__, "heartbeat_interval": "30"}),
        json.dumps({**STORED.__dict__, "credential": None}),
    ],
)
def test_a_file_that_is_not_a_credential_is_refused_without_showing_it(tmp_path, content):
    path = tmp_path / "credential.json"
    path.write_text(content, encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o600)
    with pytest.raises(CredentialFileError) as refused:
        CredentialStore(path).load()
    assert "delete it and join again" in str(refused.value)
    assert "c" * 43 not in str(refused.value)


@posix_only
def test_the_file_is_owner_only_in_an_owner_only_folder(tmp_path):
    store = CredentialStore(tmp_path / "state" / "credential.json")
    store.save(STORED)
    assert (tmp_path / "state").stat().st_mode & 0o777 == 0o700
    assert store.path.stat().st_mode & 0o777 == 0o600


@posix_only
def test_a_file_others_can_read_or_a_folder_others_can_write_is_refused(tmp_path):
    store = CredentialStore(tmp_path / "state" / "credential.json")
    store.save(STORED)
    store.path.chmod(0o644)
    with pytest.raises(CredentialFileError, match="chmod 600"):
        store.load()
    store.path.chmod(0o600)
    (tmp_path / "state").chmod(0o777)
    with pytest.raises(CredentialFileError, match="chmod 700"):
        store.load()


def test_a_refused_file_is_left_exactly_as_it_was_and_the_error_hides_its_content(tmp_path):
    path = tmp_path / "credential.json"
    body = '{"credential": "' + "z" * 43 + '", "truncated'
    path.write_text(body, encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o600)
    with pytest.raises(CredentialFileError) as refused:
        CredentialStore(path).load()
    assert "z" * 43 not in str(refused.value)
    assert path.read_text(encoding="utf-8") == body


def test_a_failed_save_keeps_the_previous_credential_and_leaves_no_temp_file(
    tmp_path, monkeypatch
):
    store = CredentialStore(tmp_path / "credential.json")
    store.save(STORED)

    def refuse(*_args, **_kwargs):
        raise PermissionError(13, "in use")

    monkeypatch.setattr("swarmscribe_follower.credentials.os.replace", refuse)
    monkeypatch.setattr("swarmscribe_follower.credentials.time.sleep", lambda _s: None)
    newer = Stored(**{**STORED.__dict__, "credential": "d" * 43})
    with pytest.raises(CredentialFileError) as failed:
        store.save(newer)
    assert "d" * 43 not in str(failed.value)
    monkeypatch.undo()
    assert store.load() == STORED
    assert [entry.name for entry in tmp_path.iterdir()] == ["credential.json"]


def test_a_symbolic_link_in_place_of_the_file_is_never_followed(tmp_path):
    target = tmp_path / "elsewhere.json"
    target.write_text(json.dumps(STORED.__dict__), encoding="utf-8")
    if os.name != "nt":
        target.chmod(0o600)
    link = tmp_path / "credential.json"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("this account cannot create symbolic links")
    with pytest.raises(CredentialFileError, match="delete it and join again"):
        CredentialStore(link).load()
    # saving replaces the link itself and never writes through it
    CredentialStore(link).save(Stored(**{**STORED.__dict__, "credential": "d" * 43}))
    assert json.loads(target.read_text(encoding="utf-8"))["credential"] == "c" * 43
    assert not link.is_symlink()


def test_a_directory_in_place_of_the_file_is_refused(tmp_path):
    (tmp_path / "credential.json").mkdir()
    with pytest.raises(CredentialFileError):
        CredentialStore(tmp_path / "credential.json").load()


def test_writers_in_the_same_folder_never_leave_a_torn_file(tmp_path):
    path = tmp_path / "credential.json"
    CredentialStore(path).save(STORED)
    stop = threading.Event()
    problems: list[str] = []

    def write(letter: str) -> None:
        store = CredentialStore(path)
        for _ in range(40):
            store.save(Stored(**{**STORED.__dict__, "credential": letter * 43}))

    def read() -> None:
        store = CredentialStore(path)
        while not stop.is_set():
            try:
                loaded = store.load()
            except CredentialFileError as exc:
                problems.append(str(exc))
                return
            if loaded is None or len(set(loaded.credential)) != 1:
                problems.append("torn or missing")
                return

    writers = [threading.Thread(target=write, args=(letter,)) for letter in "abc"]
    reader = threading.Thread(target=read)
    reader.start()
    for thread in writers:
        thread.start()
    for thread in writers:
        thread.join(timeout=60)
    stop.set()
    reader.join(timeout=10)
    assert not any(t.is_alive() for t in (*writers, reader))
    assert problems == []
    assert sorted(entry.name for entry in tmp_path.iterdir()) == ["credential.json"]


@posix_only
def test_a_file_created_under_a_loose_umask_is_still_owner_only(tmp_path):
    old = os.umask(0)
    try:
        store = CredentialStore(tmp_path / "state" / "credential.json")
        store.save(STORED)
    finally:
        os.umask(old)
    assert store.path.stat().st_mode & 0o777 == 0o600
