import io
import os

import pytest
from swarmscribe_follower import entry, envfile
from swarmscribe_follower import main as cli

SECRET = "value-that-must-not-be-echoed"


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    """No SwarmScribe setting comes in from outside, and none that a settings file put into
    the process's environment (that is what `--env-file` does) stays behind."""
    before = set(os.environ)
    for name in list(os.environ):
        if name.startswith("SWARMSCRIBE_") or name in ("HF_HUB_CACHE", "HF_HUB_OFFLINE"):
            monkeypatch.delenv(name)
    yield
    for name in set(os.environ) - before:
        del os.environ[name]


def test_names_and_values_are_read_and_comments_blanks_quotes_and_export_are_allowed():
    text = (
        "# a comment\n"
        "\n"
        "SWARMSCRIBE_LEADER_URL=https://leader.example.org\n"
        "  SWARMSCRIBE_FOLLOWER_POOL = office \n"
        'QUOTED="two words"\n'
        "SINGLE='x=y'\n"
        "export EXPORTED=1\n"
        "EMPTY=\n"
        r"WINDOWS=C:\ProgramData\swarmscribe-follower\state" "\n"
    )
    assert envfile.parse(text, "follower.env") == {
        "SWARMSCRIBE_LEADER_URL": "https://leader.example.org",
        "SWARMSCRIBE_FOLLOWER_POOL": "office",
        "QUOTED": "two words",
        "SINGLE": "x=y",
        "EXPORTED": "1",
        "EMPTY": "",
        "WINDOWS": r"C:\ProgramData\swarmscribe-follower\state",
    }


@pytest.mark.parametrize("line", [f"no equals sign {SECRET}", f"9NAME={SECRET}", f"A B={SECRET}"])
def test_a_bad_line_is_named_by_its_number_and_never_by_its_content(line):
    with pytest.raises(envfile.EnvFileError) as refused:
        envfile.parse(f"GOOD=1\n{line}\n", "follower.env")
    assert str(refused.value) == "follower.env, line 2: expected NAME=value"


def test_the_file_wins_over_the_environment_and_a_byte_order_mark_is_ignored(tmp_path):
    path = tmp_path / "follower.env"
    path.write_bytes(b"\xef\xbb\xbfSWARMSCRIBE_FOLLOWER_POOL=from-file\nOTHER=2\n")
    environ = {"SWARMSCRIBE_FOLLOWER_POOL": "from-environment", "KEPT": "yes"}
    assert envfile.load(path, environ) == ["OTHER", "SWARMSCRIBE_FOLLOWER_POOL"]
    assert environ == {"SWARMSCRIBE_FOLLOWER_POOL": "from-file", "OTHER": "2", "KEPT": "yes"}


def test_a_file_that_is_missing_too_large_or_not_text_is_refused_in_words(tmp_path):
    with pytest.raises(envfile.EnvFileError, match="cannot be read"):
        envfile.load(tmp_path / "absent.env", {})
    large = tmp_path / "large.env"
    large.write_bytes(b"A=1\n" * 20_000)
    with pytest.raises(envfile.EnvFileError, match="larger than"):
        envfile.load(large, {})
    binary = tmp_path / "binary.env"
    binary.write_bytes(b"A=\xff\xfe\n")
    with pytest.raises(envfile.EnvFileError, match="not UTF-8"):
        envfile.load(binary, {})


def test_the_command_is_found_behind_the_env_file_option():
    assert entry.command(["run"]) == "run"
    assert entry.command(["--env-file", "run", "doctor"]) == "doctor"  # "run" is the PATH here
    assert entry.command(["--env-file", "/etc/swarmscribe-follower/follower.env", "run"]) == "run"
    assert entry.command(["--env-file=/etc/x.env", "run"]) == "run"
    assert entry.command(["--version"]) is None
    assert entry.command([]) is None


def test_a_command_reads_its_settings_from_the_file(tmp_path):
    path = tmp_path / "follower.env"
    path.write_text(
        "SWARMSCRIBE_LEADER_URL=https://leader.example.org\n"
        f"SWARMSCRIBE_FOLLOWER_STATE_DIR={tmp_path / 'state'}\n"
        "SWARMSCRIBE_FOLLOWER_POOL=office\n"
        "SWARMSCRIBE_FOLLOWER_DEVICE=cpu\n",
        encoding="utf-8",
    )
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(
        ["--env-file", str(path), "doctor", "--no-model", "--no-leader"], out=out, err=err
    )
    assert code == 0, out.getvalue() + err.getvalue()
    assert "settings: ok (leader https://leader.example.org, pool office)" in out.getvalue()
    assert f"state folder: ok ({tmp_path / 'state'}" in out.getvalue()


def test_a_broken_settings_file_is_exit_2_and_its_content_is_not_shown(tmp_path):
    path = tmp_path / "follower.env"
    path.write_text(f"SWARMSCRIBE_LEADER_URL https://{SECRET}\n", encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()
    assert cli.main(["--env-file", str(path), "run"], out=out, err=err) == 2
    assert err.getvalue().strip() == f"error: {path}, line 1: expected NAME=value"
    assert SECRET not in out.getvalue() + err.getvalue()
    out, err = io.StringIO(), io.StringIO()
    assert cli.main(["--env-file", str(tmp_path / "absent"), "leave"], out=out, err=err) == 2
    assert "cannot be read" in err.getvalue()
