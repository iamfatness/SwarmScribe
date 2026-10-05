import io
import wave
from pathlib import Path

import pytest
from follower_testkit import CPU, FakeEngine, FakeLeader, make_runner
from swarmscribe_follower import main as cli
from swarmscribe_follower import memory
from swarmscribe_follower.config import Settings
from swarmscribe_follower.device import Probe
from swarmscribe_follower.leader import LeaderClient
from swarmscribe_follower.lease import JobControl
from swarmscribe_follower.memory import Limit, MemoryGuard, find_limit, job_mb, probe_recording
from swarmscribe_follower.models import ModelHost, OutOfMemory

HOUR = 3600.0


def guard(limit_mb, seconds, channels=1, held=500):
    return MemoryGuard(
        Limit(limit_mb, "a test"), held=lambda: held, probe=lambda path: (seconds, channels)
    )


def test_the_estimate_grows_with_the_recording_and_a_split_costs_more():
    assert job_mb(0, split=False) == memory.JOB_BASE_MB == 100
    assert job_mb(HOUR, split=False) == 100 + memory.MONO_MB_PER_HOUR == 3700
    assert job_mb(HOUR, split=True) == 100 + memory.SPLIT_MB_PER_HOUR == 4000
    assert job_mb(HOUR / 2, split=False) == 1900
    assert job_mb(-5, split=False) == 100


def test_a_recording_that_fits_passes_and_one_that_does_not_is_refused_by_its_length():
    guard(4200, HOUR).check(Path("source"), "mono")  # 500 held + 3700: just inside
    with pytest.raises(OutOfMemory) as refused:
        guard(4199, HOUR).check(Path("source"), "mono")
    said = str(refused.value)
    assert "60 minutes" in said and "4200 MiB" in said and "4199 MiB (a test)" in said
    assert "source" not in said  # nothing of the recording but its length


@pytest.mark.parametrize(
    ("mode", "channels", "split"),
    [
        ("mono", 2, False),
        ("stereo_split", 2, True),
        ("auto", 2, True),
        ("auto", 1, False),
        ("auto", 6, False),
    ],
)
def test_a_recording_counts_as_split_only_when_the_engine_will_split_it(mode, channels, split):
    limit = 500 + 3850  # between the mono and the split figure for an hour
    check = guard(limit, HOUR, channels).check
    if split:
        with pytest.raises(OutOfMemory):
            check(Path("source"), mode)
    else:
        check(Path("source"), mode)


def test_a_recording_whose_length_cannot_be_read_is_left_to_the_engine():
    unreadable = MemoryGuard(Limit(64, "a test"), held=lambda: 500, probe=lambda path: None)
    unreadable.check(Path("source"), "mono")


def test_a_platform_that_will_not_say_what_is_held_counts_the_job_alone():
    unknown = MemoryGuard(Limit(3700, "a test"), held=lambda: None, probe=lambda p: (HOUR, 1))
    unknown.check(Path("source"), "mono")


# --- the limit ----------------------------------------------------------------------------


def test_the_setting_wins_over_everything():
    limit = find_limit(2048, cgroup=lambda: 512, physical=lambda: 64000)
    assert limit == Limit(2048, "SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB")


def test_the_smaller_of_the_container_and_the_machine_is_the_limit():
    assert find_limit(None, cgroup=lambda: 4096, physical=lambda: 64000).megabytes == 4096
    assert find_limit(None, cgroup=lambda: None, physical=lambda: 16000) == Limit(
        16000, "this machine"
    )
    # A container limit above the machine's memory is no limit at all.
    assert find_limit(None, cgroup=lambda: 99999, physical=lambda: 8000).megabytes == 8000


def test_nothing_known_means_no_limit_and_a_nonsense_figure_is_ignored():
    assert find_limit(None, cgroup=lambda: None, physical=lambda: None) is None
    assert find_limit(None, cgroup=lambda: 0, physical=lambda: None) is None
    assert find_limit(None, cgroup=lambda: 3, physical=lambda: 16000).megabytes == 16000


@pytest.mark.parametrize(
    ("content", "megabytes"),
    [
        ("max\n", None),  # cgroup v2: unlimited
        ("4294967296\n", 4096),
        ("9223372036854771712\n", None),  # cgroup v1: unlimited
        ("junk\n", None),
    ],
)
def test_the_container_limit_is_read_from_the_cgroup_file(tmp_path, content, megabytes):
    file = tmp_path / "memory.max"
    file.write_text(content, encoding="ascii")
    assert memory.cgroup_limit_mb((tmp_path / "absent", file)) == megabytes


def test_no_cgroup_file_is_no_container_limit(tmp_path):
    assert memory.cgroup_limit_mb((tmp_path / "absent", tmp_path / "also-absent")) is None


def test_this_machine_says_how_much_memory_it_has():
    assert (memory.physical_mb() or 0) >= 256


def test_the_setting_must_be_a_sensible_number_and_blank_means_unset(monkeypatch):
    monkeypatch.setenv("SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB", "")
    assert Settings(leader_url="https://l.example.org").memory_limit_mb is None
    monkeypatch.setenv("SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB", "8192")
    assert Settings(leader_url="https://l.example.org").memory_limit_mb == 8192
    monkeypatch.setenv("SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB", "8")
    with pytest.raises(ValueError, match="memory_limit_mb"):
        Settings(leader_url="https://l.example.org")


# --- a real file ----------------------------------------------------------------------------


def write_wav(path, seconds, channels):
    with wave.open(str(path), "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(8000)
        out.writeframes(b"\0\0" * channels * 8000 * seconds)


def test_the_length_and_the_channels_come_from_the_file_itself(tmp_path):
    pytest.importorskip("av")
    write_wav(tmp_path / "source", 3, 2)  # no extension: the follower's scratch name
    seconds, channels = probe_recording(tmp_path / "source")
    assert (round(seconds, 1), channels) == (3.0, 2)


def test_a_file_that_is_not_audio_or_is_missing_has_no_length(tmp_path):
    (tmp_path / "source").write_bytes(b"not audio at all" * 100)
    assert probe_recording(tmp_path / "source") is None
    assert probe_recording(tmp_path / "absent") is None


# --- in a job ---------------------------------------------------------------------------------


def run_guarded(tmp_path, limit_mb, seconds):
    leader, engine = FakeLeader(links_min_interval=0), FakeEngine()
    job_id = leader.add_job()
    runner, client = make_runner(tmp_path, leader, engine, guard=guard(limit_mb, seconds))
    result = runner.run(client.claim(), JobControl())
    return result, leader, engine, job_id


def test_a_job_too_long_for_this_follower_fails_out_of_resources_before_the_engine_runs(
    tmp_path,
):
    result, leader, engine, job_id = run_guarded(tmp_path, 2000, 3 * HOUR)
    assert (result.outcome, result.detail) == ("failed", "out_of_resources")
    failed = leader.failed[0]
    assert (failed["code"], failed["retryable"]) == ("out_of_resources", True)
    assert "180 minutes" in failed["reason"] and "2000 MiB" in failed["reason"]
    assert engine.transcribed == []  # the engine never read the recording
    assert engine.loads  # the model was loaded first: what it holds is counted
    assert leader.released == []  # failed, not released: the machine is not unfit


def test_a_job_that_fits_is_transcribed(tmp_path):
    result, leader, engine, job_id = run_guarded(tmp_path, 64000, HOUR)
    assert result.outcome == "completed" and len(engine.transcribed) == 1


def test_without_a_guard_every_job_is_tried(tmp_path):
    leader, engine = FakeLeader(links_min_interval=0), FakeEngine()
    leader.add_job()
    runner, client = make_runner(tmp_path, leader, engine)
    assert runner.run(client.claim(), JobControl()).outcome == "completed"


# --- doctor -------------------------------------------------------------------------------------


def doctor_says(monkeypatch, limit):
    monkeypatch.setattr(cli, "probe", lambda preference: Probe(CPU))
    monkeypatch.setattr(cli, "find_limit", lambda configured: limit)
    monkeypatch.setenv("SWARMSCRIBE_LEADER_URL", "https://leader.test")
    engine, out = FakeEngine(), io.StringIO()
    code = cli.command_doctor(
        Settings(),
        out,
        ask_leader=False,
        host=lambda device, **kw: ModelHost(device, factory=engine, **kw),
        client=LeaderClient,
    )
    return code, out.getvalue().splitlines()


def test_doctor_says_how_much_memory_the_follower_may_use(monkeypatch, tmp_path):
    monkeypatch.setenv("SWARMSCRIBE_FOLLOWER_STATE_DIR", str(tmp_path / "state"))
    code, lines = doctor_says(monkeypatch, Limit(15832, "this machine"))
    assert code == 0 and "memory: 15832 MiB may be used (this machine)" in lines
    code, lines = doctor_says(monkeypatch, None)
    assert code == 0 and "memory: unknown (recordings are not checked against it)" in lines
