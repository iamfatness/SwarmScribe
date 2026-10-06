import io
import json
import logging

import pytest
from pydantic import ValidationError
from swarmscribe_follower.config import Settings
from swarmscribe_follower.logs import JsonFormatter, TextFormatter, configure_logging

ENV = (
    "SWARMSCRIBE_LEADER_URL",
    "SWARMSCRIBE_JOIN_TOKEN",
    "SWARMSCRIBE_JOIN_TOKEN_FILE",
    "SWARMSCRIBE_LEADER_CA_FILE",
)


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)


def test_the_docker_run_line_of_the_master_spec_is_enough(monkeypatch):
    monkeypatch.setenv("SWARMSCRIBE_LEADER_URL", "https://leader.example.org/")
    monkeypatch.setenv("SWARMSCRIBE_JOIN_TOKEN", "  the-token  ")
    settings = Settings()
    assert settings.leader_url == "https://leader.example.org"
    assert settings.token() == "the-token"
    assert (settings.device, settings.pool, settings.on_drained) == ("auto", "default", "exit")
    assert settings.shutdown_grace_seconds == 8
    assert settings.scratch == settings.state_dir / "scratch"
    assert settings.credential_file == settings.state_dir / "credential.json"


def test_follower_settings_use_their_own_prefix(monkeypatch, tmp_path):
    monkeypatch.setenv("SWARMSCRIBE_LEADER_URL", "https://leader.example.org")
    monkeypatch.setenv("SWARMSCRIBE_FOLLOWER_DEVICE", "cpu")
    monkeypatch.setenv("SWARMSCRIBE_FOLLOWER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SWARMSCRIBE_FOLLOWER_SCRATCH_DIR", str(tmp_path / "scratch"))
    monkeypatch.setenv("SWARMSCRIBE_FOLLOWER_ALLOWED_MODELS", " large-v3 , owner/custom ,")
    monkeypatch.setenv("SWARMSCRIBE_FOLLOWER_ON_DRAINED", "park")
    settings = Settings()
    assert settings.device == "cpu"
    assert settings.scratch == tmp_path / "scratch"
    assert settings.allowed_models == ("large-v3", "owner/custom")
    assert settings.on_drained == "park"


def test_a_token_file_wins_over_the_variable_and_is_read_late(monkeypatch, tmp_path):
    secret = tmp_path / "token"
    monkeypatch.setenv("SWARMSCRIBE_LEADER_URL", "https://leader.example.org")
    monkeypatch.setenv("SWARMSCRIBE_JOIN_TOKEN", "from-the-variable")
    monkeypatch.setenv("SWARMSCRIBE_JOIN_TOKEN_FILE", str(secret))
    settings = Settings()  # the file need not exist until a registration reads it
    with pytest.raises(ValueError, match="cannot be read"):
        settings.token()
    secret.write_text("from-the-file\n", encoding="utf-8")
    assert settings.token() == "from-the-file"


def with_token_file(monkeypatch, path, content: bytes) -> Settings:
    path.write_bytes(content)
    monkeypatch.setenv("SWARMSCRIBE_LEADER_URL", "https://leader.example.org")
    monkeypatch.setenv("SWARMSCRIBE_JOIN_TOKEN_FILE", str(path))
    return Settings()


@pytest.mark.parametrize(
    "content",
    [
        b"the-token\n",  # plain
        b"\xef\xbb\xbfthe-token\r\n",  # PowerShell 5.1: Set-Content -Encoding UTF8
        b"\xff\xfe" + "the-token\r\n".encode("utf-16-le"),  # PowerShell 5.1: > and Out-File
        b"\xfe\xff" + "the-token\r\n".encode("utf-16-be"),
    ],
    ids=["plain", "utf-8 with a mark", "utf-16 little-endian", "utf-16 big-endian"],
)
def test_a_token_file_is_read_whatever_a_windows_tool_wrote_it_as(monkeypatch, tmp_path, content):
    """A byte-order mark must never become part of the token: the leader would refuse it,
    which is exit 4, final."""
    settings = with_token_file(monkeypatch, tmp_path / "token", content)
    assert settings.token() == "the-token"


@pytest.mark.parametrize(
    "content",
    [
        b"\x80\x81 s3cret-bytes \xfe",  # not text at all
        b"\xff\xfes3cret-odd-",  # says UTF-16 and ends in half a character
        "s3cret-token".encode("utf-16-le"),  # UTF-16 without a mark: NUL after every letter
    ],
    ids=["garbage", "broken utf-16", "utf-16 without a mark"],
)
def test_a_token_file_that_is_not_text_is_a_settings_error_naming_the_file(
    monkeypatch, tmp_path, content
):
    path = tmp_path / "token"
    settings = with_token_file(monkeypatch, path, content)
    with pytest.raises(ValueError) as refused:
        settings.token()
    message = str(refused.value)
    assert str(path) in message and "plain text (UTF-8 or ASCII)" in message
    assert "s3cret" not in message and "codec" not in message


@pytest.mark.parametrize("name", ENV[1:])
def test_an_optional_setting_passed_as_an_empty_string_is_unset(monkeypatch, name):
    monkeypatch.setenv("SWARMSCRIBE_LEADER_URL", "https://leader.example.org")
    monkeypatch.setenv(name, "")
    settings = Settings()
    assert settings.token() is None
    assert settings.leader_ca_file is None


@pytest.mark.parametrize(
    "url",
    [
        "http://leader.example.org",
        "leader.example.org",
        "ftp://leader.example.org",
        "https://user:pw@leader.example.org",
        "https://leader.example.org/?x=1",
        "",
    ],
)
def test_a_leader_url_that_is_not_safe_is_refused(url):
    with pytest.raises(ValidationError):
        Settings(leader_url=url)


@pytest.mark.parametrize(
    "url",
    ["http://localhost:8080", "http://127.0.0.1:8080", "http://[::1]:8080", "http://proxy"],
)
def test_plain_http_is_refused_without_the_switch_loopback_included(url):
    with pytest.raises(ValidationError) as refused:
        Settings(leader_url=url)
    assert "SWARMSCRIBE_FOLLOWER_ALLOW_HTTP=1" in str(refused.value)
    assert Settings(leader_url=url, allow_http=True).leader_url == url


def test_a_validation_error_never_shows_the_join_token():
    with pytest.raises(ValidationError) as refused:
        Settings(leader_url="https://leader.example.org", join_token="s3cret-token", device="tpu")
    assert "s3cret-token" not in str(refused.value)
    settings = Settings(leader_url="https://x.example", join_token="s3cret-token")
    assert "s3cret-token" not in repr(settings)


def _record(**extra) -> logging.LogRecord:
    name = "swarmscribe_follower.job"
    record = logging.LogRecord(name, logging.INFO, "f", 1, "job %s", ("done",), None)
    for name, value in extra.items():
        setattr(record, name, value)
    return record


def test_json_lines_carry_the_job_and_lease():
    line = json.loads(JsonFormatter().format(_record(job_id="j1", lease_id="l1", event="job.done")))
    assert (line["message"], line["level"], line["job_id"], line["lease_id"], line["event"]) == (
        "job done",
        "info",
        "j1",
        "l1",
        "job.done",
    )
    assert "follower_id" not in line
    assert line["time"].endswith("+00:00")


def test_an_exception_is_logged_by_class_not_by_text():
    try:
        raise OSError("cannot open https://leader/v1/files/SECRET-LINK")
    except OSError:
        import sys

        record = _record()
        record.exc_info = sys.exc_info()
    line = JsonFormatter().format(record)
    assert json.loads(line)["error"] == "OSError"
    assert "SECRET-LINK" not in line


def test_text_format_is_one_readable_line():
    line = TextFormatter().format(_record(job_id="j1"))
    assert line.endswith("INFO swarmscribe_follower.job: job done job_id=j1")


def test_importing_every_follower_module_changes_no_logger_in_a_fresh_interpreter():
    import subprocess
    import sys

    code = """
import importlib, logging, pkgutil
names = ("", "httpx", "httpcore")
def state():
    return [(n, logging.getLogger(n).level, list(logging.getLogger(n).handlers),
             logging.getLogger(n).propagate, logging.getLogger(n).disabled) for n in names]
before = state()
import swarmscribe_follower
for module in pkgutil.walk_packages(swarmscribe_follower.__path__, "swarmscribe_follower."):
    if not module.name.endswith("__main__"):  # that one runs the command line
        importlib.import_module(module.name)
assert state() == before, (before, state())
print("unchanged")
"""
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert done.stdout.strip() == "unchanged", done.stderr


def test_configuring_logs_silences_the_http_clients_request_lines():
    stream = io.StringIO()
    root = logging.getLogger()
    before, level = root.handlers[:], root.level
    try:
        logging.getLogger("httpx").setLevel(logging.DEBUG)  # something lowered it
        configure_logging("json", stream=stream)
        logging.getLogger("httpx").info("HTTP Request: GET https://leader/v1/files/SECRET-LINK")
        logging.getLogger("httpcore").info("connect https://leader/v1/files/SECRET-LINK")
        logging.getLogger("swarmscribe_follower").info("started")
    finally:
        root.handlers[:] = before
        root.setLevel(level)
    assert "SECRET-LINK" not in stream.getvalue()
    assert json.loads(stream.getvalue())["message"] == "started"


def test_the_join_token_is_masked_in_every_dump():
    settings = Settings(leader_url="https://x.example", join_token="s3cret-token")
    assert "s3cret-token" not in str(settings)
    assert "s3cret-token" not in settings.model_dump_json()
    assert "s3cret-token" not in repr(settings.model_dump())
