import errno
import os
import stat
import uuid
from pathlib import Path

import pytest
from swarmscribe_follower.scratch import (
    MARKER,
    Scratch,
    ScratchDiskFull,
    ScratchError,
    ScratchNotOurs,
    ScratchWipeFailed,
)

JOB = "2f0d1c1e-0000-4000-8000-000000000001"


def test_a_new_folder_is_created_and_marked(tmp_path):
    scratch = Scratch(tmp_path / "deep" / "scratch")
    scratch.prepare()
    assert (tmp_path / "deep" / "scratch" / MARKER).is_file()


def test_a_folder_with_someone_elses_files_is_refused_and_left_alone(tmp_path):
    (tmp_path / "thesis.docx").write_bytes(b"years of work")
    (tmp_path / "photos").mkdir()
    with pytest.raises(ScratchNotOurs, match="SWARMSCRIBE_FOLLOWER_SCRATCH_DIR"):
        Scratch(tmp_path).prepare()
    assert (tmp_path / "thesis.docx").read_bytes() == b"years of work"
    assert (tmp_path / "photos").is_dir()
    assert not (tmp_path / MARKER).exists()


def test_wiping_a_folder_that_was_never_prepared_deletes_nothing(tmp_path):
    (tmp_path / "thesis.docx").write_bytes(b"years of work")
    Scratch(tmp_path).wipe()
    assert (tmp_path / "thesis.docx").exists()


def test_what_a_crashed_run_left_is_wiped_at_the_next_start(tmp_path):
    scratch = Scratch(tmp_path)
    scratch.prepare()
    folder = scratch.job_dir(JOB)
    (folder / "source").write_bytes(b"a recording")
    (folder / "source.txt.tmp").write_text("half a transcript")
    (tmp_path / "stray").write_text("x")
    Scratch(tmp_path).prepare()  # the next start
    assert [entry.name for entry in tmp_path.iterdir()] == [MARKER]


def test_a_job_folder_is_fresh_and_named_by_the_job(tmp_path):
    scratch = Scratch(tmp_path)
    scratch.prepare()
    folder = scratch.job_dir(JOB)
    (folder / "source").write_bytes(b"first attempt")
    again = scratch.job_dir(JOB)
    assert again == tmp_path / f"job-{JOB}"
    assert list(again.iterdir()) == []


@pytest.mark.parametrize("job_id", ["../../etc", "job", "", "a/b", "..", "C:\\x"])
def test_a_job_id_that_is_not_a_uuid_never_becomes_a_path(tmp_path, job_id):
    scratch = Scratch(tmp_path / "scratch")
    scratch.prepare()
    with pytest.raises(ValueError):
        scratch.job_dir(job_id)
    assert [entry.name for entry in (tmp_path / "scratch").iterdir()] == [MARKER]


def test_remove_deletes_a_link_and_not_what_it_points_at(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    scratch = Scratch(tmp_path / "scratch")
    scratch.prepare()
    link = tmp_path / "scratch" / "link"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this account cannot create symbolic links")
    scratch.wipe()
    assert not link.exists()
    assert (outside / "keep.txt").read_text() == "keep"


def test_removing_what_is_already_gone_is_not_an_error(tmp_path):
    scratch = Scratch(tmp_path / "scratch")
    scratch.prepare()
    scratch.remove(tmp_path / "scratch" / "missing")
    scratch.remove(tmp_path / "scratch" / f"job-{uuid.uuid4()}")


def _link_or_skip(target, link):
    try:
        os.symlink(target, link, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError):
        pytest.skip("this account cannot create symbolic links")


def test_links_nested_inside_a_job_folder_are_removed_and_never_followed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    scratch = Scratch(tmp_path / "scratch")
    scratch.prepare()
    folder = scratch.job_dir(JOB)
    (folder / "inner").mkdir()
    _link_or_skip(outside, folder / "inner" / "dir-link")
    _link_or_skip(outside / "keep.txt", folder / "file-link")
    scratch.wipe()
    assert [entry.name for entry in (tmp_path / "scratch").iterdir()] == [MARKER]
    assert (outside / "keep.txt").read_text() == "keep"


@pytest.mark.skipif(os.name != "nt", reason="directory junctions are a Windows thing")
def test_a_junction_inside_scratch_is_removed_and_never_followed(tmp_path):
    import subprocess

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    scratch = Scratch(tmp_path / "scratch")
    scratch.prepare()
    junction = tmp_path / "scratch" / "junction"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside)], check=True,
                   capture_output=True)
    scratch.wipe()
    assert not junction.exists()
    assert (outside / "keep.txt").read_text() == "keep"


def test_a_marker_that_is_a_link_does_not_make_a_folder_ours(tmp_path):
    donor = tmp_path / "donor"
    donor.mkdir()
    marker_file = donor / "marker"
    marker_file.write_text("SwarmScribe follower scratch: everything here is deleted.\n")
    user = tmp_path / "user"
    user.mkdir()
    (user / "thesis.docx").write_bytes(b"years of work")
    _link_or_skip(marker_file, user / MARKER)
    with pytest.raises(ScratchNotOurs):
        Scratch(user).prepare()
    Scratch(user).wipe()
    assert (user / "thesis.docx").read_bytes() == b"years of work"


def test_a_scratch_path_that_is_a_file_is_a_typed_error(tmp_path):
    (tmp_path / "scratch").write_text("not a folder")
    with pytest.raises(ScratchError):
        Scratch(tmp_path / "scratch").prepare()


def test_a_read_only_file_is_still_wiped(tmp_path):
    scratch = Scratch(tmp_path)
    scratch.prepare()
    victim = scratch.job_dir(JOB) / "source"
    victim.write_bytes(b"x")
    victim.chmod(stat.S_IREAD)
    scratch.wipe()
    assert [entry.name for entry in tmp_path.iterdir()] == [MARKER]


@pytest.mark.skipif(os.name != "nt", reason="long paths are a Windows limit")
def test_a_path_beyond_the_windows_limit_is_wiped(tmp_path):
    scratch = Scratch(tmp_path / "scratch")
    scratch.prepare()
    deep = "\\\\?\\" + str(scratch.job_dir(JOB).resolve())
    for _ in range(8):
        deep += "\\" + "d" * 50
        os.mkdir(deep)
    with open(deep + "\\" + "f" * 50, "wb") as handle:
        handle.write(b"x")
    assert len(deep) > 400
    scratch.wipe()
    assert [entry.name for entry in (tmp_path / "scratch").iterdir()] == [MARKER]


@pytest.mark.skipif(os.name != "nt", reason="only Windows refuses to delete an open file")
def test_a_file_open_elsewhere_is_a_clear_error_and_is_wiped_at_the_next_start(tmp_path):
    scratch = Scratch(tmp_path, delays=(0.0,) * 4)
    scratch.prepare()
    held = scratch.job_dir(JOB) / "source"
    held.write_bytes(b"recording")
    with open(held, "rb"):
        scratch.wipe()  # a wipe at exit never raises
        assert held.exists()
        with pytest.raises(ScratchWipeFailed, match="next start"):
            scratch.remove(held.parent)  # after a job: loud, and the next start wipes it
        with pytest.raises(ScratchWipeFailed, match="next start"):
            Scratch(tmp_path, delays=(0.0,) * 4).prepare()
        assert (tmp_path / MARKER).is_file()  # still ours: the next start may try again
    Scratch(tmp_path).prepare()
    assert [entry.name for entry in tmp_path.iterdir()] == [MARKER]


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="needs POSIX permissions and a non-root user")
def test_an_entry_that_cannot_be_deleted_is_a_clear_error_at_start(tmp_path):
    scratch = Scratch(tmp_path)
    scratch.prepare()
    folder = scratch.job_dir(JOB)
    (folder / "source").write_bytes(b"x")
    folder.chmod(stat.S_IREAD | stat.S_IEXEC)
    try:
        with pytest.raises(ScratchWipeFailed, match="next start"):
            Scratch(tmp_path).prepare()
    finally:
        folder.chmod(stat.S_IRWXU)
    Scratch(tmp_path).prepare()
    assert [entry.name for entry in tmp_path.iterdir()] == [MARKER]


def test_a_full_disk_is_a_typed_error_not_a_bare_oserror(tmp_path, monkeypatch):
    def full(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(Path, "write_text", full)
    with pytest.raises(ScratchDiskFull, match="disk is full"):
        Scratch(tmp_path / "scratch").prepare()
    monkeypatch.undo()
    scratch = Scratch(tmp_path / "scratch")
    scratch.prepare()
    monkeypatch.setattr(os, "mkdir", full)
    with pytest.raises(ScratchDiskFull):
        scratch.job_dir(JOB)
