"""Scratch.remove is bounded, the folders are private and kept apart from the credential, and a
marker needs the id the state folder holds."""

import os
import stat
from pathlib import Path

import pytest
from swarmscribe_follower import scratch as module
from swarmscribe_follower.scratch import (
    MARKER,
    Scratch,
    ScratchError,
    ScratchNotOurs,
    ScratchOutside,
    ScratchWipeFailed,
    check_folders,
)

JOB = "2f0d1c1e-0000-4000-8000-000000000001"
NO_WAIT = (0.0,) * 4
posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX modes")


def _link_or_skip(target, link):
    try:
        os.symlink(target, link, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError):
        pytest.skip("this account cannot create symbolic links")


def _prepared(tmp_path):
    scratch = Scratch(tmp_path / "state" / "scratch", tmp_path / "state")
    scratch.prepare()
    return scratch


# --- remove is bounded ---


def test_remove_deletes_nothing_outside_the_root(tmp_path, monkeypatch):
    scratch = _prepared(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    (other / "keep.txt").write_text("keep")
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / "mine.txt").write_text("mine")
    monkeypatch.chdir(tmp_path / "work")
    (scratch.root / "inside").mkdir()
    for bad in (
        Path(""),
        Path("."),
        Path("relative"),
        scratch.root,
        scratch.root.parent,
        other,
        other / "keep.txt",
        scratch.root / "inside" / ".." / ".." / "other",
        scratch.root / ".." / "other",
        tmp_path / "state" / "scratch-id",
    ):
        with pytest.raises(ScratchOutside):
            scratch.remove(bad)
    with pytest.raises(ScratchOutside):
        scratch.remove(None)
    assert (other / "keep.txt").read_text() == "keep"
    assert (tmp_path / "work" / "mine.txt").read_text() == "mine"
    assert (scratch.root / MARKER).is_file()
    assert (tmp_path / "state" / "scratch-id").is_file()
    assert (scratch.root / "inside").is_dir()


def test_remove_inside_the_root_works(tmp_path):
    scratch = _prepared(tmp_path)
    folder = scratch.job_dir(JOB)
    (folder / "source").write_bytes(b"x")
    scratch.remove(folder)
    assert not folder.exists()


def test_remove_through_a_link_to_the_outside_deletes_nothing(tmp_path):
    scratch = _prepared(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    (other / "keep.txt").write_text("keep")
    _link_or_skip(other, scratch.root / "link")
    with pytest.raises(ScratchOutside):
        scratch.remove(scratch.root / "link" / "keep.txt")
    assert (other / "keep.txt").read_text() == "keep"


def test_remove_refuses_a_root_that_is_not_ours(tmp_path):
    (tmp_path / "files").mkdir()
    (tmp_path / "files" / "thesis.docx").write_bytes(b"years of work")
    scratch = Scratch(tmp_path / "files")
    with pytest.raises(ScratchNotOurs):
        scratch.remove(tmp_path / "files" / "thesis.docx")
    assert (tmp_path / "files" / "thesis.docx").exists()


def test_a_relative_root_is_pinned_when_the_scratch_is_made(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    scratch = Scratch(Path("rel-scratch"))
    scratch.prepare()
    (tmp_path / "elsewhere").mkdir()
    monkeypatch.chdir(tmp_path / "elsewhere")
    assert scratch.root == tmp_path / "rel-scratch"
    scratch.wipe()
    assert (tmp_path / "rel-scratch" / MARKER).is_file()


# --- layout ---


def test_scratch_may_not_be_or_hold_the_state_folder(tmp_path):
    state = tmp_path / "parent" / "state"
    for scratch in (state, tmp_path / "parent", tmp_path):
        with pytest.raises(ScratchError, match="credential"):
            Scratch(scratch, state)
        with pytest.raises(ScratchError):
            check_folders(state, scratch)
    check_folders(state, state / "scratch")  # the default layout is fine
    check_folders(state, tmp_path / "elsewhere")


def test_scratch_may_not_hold_the_model_folder(tmp_path):
    with pytest.raises(ScratchError, match="models"):
        check_folders(tmp_path / "state", tmp_path / "scratch", tmp_path / "scratch" / "models")


def test_a_wipe_never_reaches_the_credential(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "credential.json").write_text("{}")
    with pytest.raises(ScratchError):
        Scratch(state, state).prepare()
    assert (state / "credential.json").exists()


# --- the marker's id ---


def test_a_marker_copied_into_a_folder_with_files_authorises_nothing(tmp_path):
    scratch = _prepared(tmp_path)
    user = tmp_path / "user"
    user.mkdir()
    (user / "thesis.docx").write_bytes(b"years of work")
    (user / MARKER).write_text((scratch.root / MARKER).read_text())
    other = Scratch(user, tmp_path / "state2")  # no record there
    with pytest.raises(ScratchNotOurs):
        other.prepare()
    other.wipe()
    assert (user / "thesis.docx").read_bytes() == b"years of work"


def test_a_marker_from_another_install_authorises_nothing(tmp_path):
    first = Scratch(tmp_path / "a" / "scratch", tmp_path / "a")
    first.prepare()
    (first.root / "precious.txt").write_text("keep")
    second_state = tmp_path / "b"
    second_state.mkdir()
    (second_state / "scratch-id").write_text("0" * 32 + "\n")
    second = Scratch(first.root, second_state)
    with pytest.raises(ScratchNotOurs):
        second.prepare()
    assert (first.root / "precious.txt").exists()


def test_first_start_with_a_marker_and_only_our_job_folders_is_accepted(tmp_path):
    scratch = _prepared(tmp_path)
    folder = scratch.job_dir(JOB)
    (folder / "source").write_bytes(b"x")
    (tmp_path / "state" / "scratch-id").unlink()  # the record is lost
    Scratch(scratch.root, tmp_path / "state").prepare()
    assert (tmp_path / "state" / "scratch-id").is_file()
    assert [e.name for e in scratch.root.iterdir()] == [MARKER]


def test_no_record_and_a_foreign_file_means_not_ours(tmp_path):
    scratch = _prepared(tmp_path)
    (scratch.root / "thesis.docx").write_bytes(b"years of work")
    (tmp_path / "state" / "scratch-id").unlink()
    with pytest.raises(ScratchNotOurs):
        Scratch(scratch.root, tmp_path / "state").prepare()
    assert (scratch.root / "thesis.docx").exists()


# --- private folders ---


@posix_only
def test_state_scratch_and_job_folders_are_created_private(tmp_path):
    old = os.umask(0)
    try:
        scratch = Scratch(tmp_path / "state" / "scratch", tmp_path / "state")
        scratch.prepare()
        job = scratch.job_dir(JOB)
    finally:
        os.umask(old)
    for folder in (tmp_path / "state", scratch.root, job):
        assert stat.S_IMODE(folder.stat().st_mode) == 0o700, folder


@posix_only
def test_loose_folders_we_own_are_tightened(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state").chmod(0o755)
    (tmp_path / "state" / "scratch").mkdir()
    (tmp_path / "state" / "scratch").chmod(0o777)
    Scratch(tmp_path / "state" / "scratch", tmp_path / "state").prepare()
    assert stat.S_IMODE((tmp_path / "state").stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "state" / "scratch").stat().st_mode) == 0o700


# --- a failed delete is retried, then loud ---


def test_remove_retries_a_few_times_before_it_gives_up(tmp_path, monkeypatch):
    waits = []
    scratch = Scratch(tmp_path / "scratch", sleep=waits.append)
    scratch.prepare()
    folder = scratch.job_dir(JOB)
    monkeypatch.setattr(module, "_delete", lambda path: False)
    with pytest.raises(ScratchWipeFailed, match="next start"):
        scratch.remove(folder)
    assert len(waits) == 4 and 1.5 < sum(waits) < 2.5  # five tries over about two seconds
    attempts = iter([False, False, True])
    monkeypatch.setattr(module, "_delete", lambda path: next(attempts))
    scratch.remove(folder)  # a short hold is waited out


@pytest.mark.skipif(os.name != "nt", reason="only Windows refuses to delete an open file")
def test_a_held_file_is_waited_for_then_reported(tmp_path):
    scratch = Scratch(tmp_path, delays=NO_WAIT)
    scratch.prepare()
    held = scratch.job_dir(JOB) / "source"
    held.write_bytes(b"recording")
    with open(held, "rb"), pytest.raises(ScratchWipeFailed):
        scratch.remove(held.parent)
    scratch.remove(held.parent)
