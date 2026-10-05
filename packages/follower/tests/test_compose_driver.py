"""The follower Compose test's own parts (e2e/follower-compose), checked without Docker:
what the driver writes before the stack starts, how it reads a follower's log, what it
accepts as a transcript, and that docker-compose.yml and run_e2e.py agree on the values
they share."""

# ruff: noqa: E402
import importlib.util
import json
import sys
import wave
from dataclasses import replace
from pathlib import Path

import pytest

# The driver administers a real leader with the leader's own functions.
pytest.importorskip("swarmscribe_leader")
yaml = pytest.importorskip("yaml")

from swarmscribe_engine import Segment, TranscribeSettings, Transcript, Word, write_outputs

COMPOSE = Path(__file__).resolve().parents[3] / "e2e" / "follower-compose"


@pytest.fixture(scope="module")
def loaded():
    spec = importlib.util.spec_from_file_location("follower_compose_driver", COMPOSE / "run_e2e.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["follower_compose_driver"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def driver(loaded, tmp_path, monkeypatch):
    """The driver, writing under a temporary folder instead of e2e/follower-compose/work."""
    work = tmp_path / "work"
    monkeypatch.setattr(loaded, "WORK", work)
    monkeypatch.setattr(loaded, "DATA", work / "data")
    monkeypatch.setattr(loaded, "SECRETS", work / "secrets")
    return loaded


def frames(path: Path) -> int:
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes()


def test_prepare_writes_two_locations_with_consent_and_an_unconsented_recording(driver):
    driver.prepare()
    calls, talks = driver.DATA / "calls", driver.DATA / "talks"
    assert (calls / "consent.txt").read_bytes() == b"day*/*.wav\nlong/*.wav\n"
    assert (talks / "consent.txt").read_bytes() == b"*.wav\n"
    written = sorted(p.relative_to(driver.DATA).as_posix() for p in driver.DATA.rglob("*.wav"))
    assert written == [
        "calls/day1/call-1.wav",
        "calls/day1/call-2.wav",
        "calls/day1/call-3.wav",
        "calls/day1/call-4.wav",
        "calls/private/held.wav",
        "talks/talk-1.wav",
        "talks/talk-2.wav",
    ]
    assert all(frames(driver.DATA / name) == frames(driver.FIXTURE) for name in written)
    assert driver.SECRETS.is_dir() and list(driver.SECRETS.iterdir()) == []


def test_prepare_starts_from_nothing_every_time(driver):
    driver.prepare()
    stale = driver.DATA / "calls" / "transcripts" / "old.txt"
    stale.parent.mkdir(parents=True)
    stale.write_text("from the last run")
    (driver.SECRETS / "join-token").write_text("an old token")
    driver.prepare()
    assert not stale.exists() and list(driver.SECRETS.iterdir()) == []


def test_the_unconsented_recording_matches_no_line_of_its_consent_file(driver):
    from fnmatch import fnmatchcase

    patterns = driver.CONSENT[driver.CALLS].split()
    assert not any(fnmatchcase(driver.UNCONSENTED, pattern) for pattern in patterns)
    for key in (*driver.SHORT[driver.CALLS], driver.KILLED, driver.STOPPED, driver.AFTER_DRAIN):
        assert any(fnmatchcase(key, pattern) for pattern in patterns), key


def test_a_long_recording_is_the_fixture_repeated_and_never_seen_half_written(driver, tmp_path):
    target = tmp_path / "long" / "four.wav"
    driver.write_recording(target, 4)
    assert frames(target) == 4 * frames(driver.FIXTURE)
    assert [p.name for p in target.parent.iterdir()] == ["four.wav"]  # no .part left behind
    assert driver.fixture_seconds() == pytest.approx(5.04, abs=0.01)


def test_registrations_are_read_from_the_json_lines_of_a_log(driver, monkeypatch):
    log = "\n".join(
        [
            json.dumps({"level": "info", "message": "model tiny.en (int8) loaded on cpu"}),
            "error: a line that is not JSON",
            json.dumps({"event": "registered", "follower_id": "id-1"}),
            json.dumps({"event": "job.claimed", "job_id": "j"}),
            "[1, 2, 3]",
            json.dumps({"event": "registered", "follower_id": "id-2"}),
        ]
    )
    monkeypatch.setattr(driver, "logs", lambda service: log)
    assert driver.registrations("follower-1") == ["id-1", "id-2"]


# --- a follower that will never register is named at once, not after the timeout ---------

NO_MODEL = (
    "error: this machine cannot transcribe with its start-up model tiny.en"
    " (SWARMSCRIBE_FOLLOWER_OFFLINE=1: it must be in the model cache): not cached. Set"
    " SWARMSCRIBE_FOLLOWER_STARTUP_MODEL to a model this machine holds, or run"
    " `swarmscribe-follower doctor`."
)


def stand_in(driver, monkeypatch, states: dict, said: dict) -> None:
    monkeypatch.setattr(driver, "container", lambda service: states.get(service))
    monkeypatch.setattr(driver, "logs", lambda service: said.get(service, ""))


def test_a_follower_that_is_running_and_has_said_nothing_wrong_has_not_given_up(
    driver, monkeypatch
):
    running = driver.Container("running", 0, "0001-01-01T00:00:00Z")
    log = json.dumps({"level": "info", "message": "model tiny.en (int8) loaded on cpu"})
    stand_in(driver, monkeypatch, {"follower-1": running}, {"follower-1": log})
    assert driver.gave_up("follower-1") is None
    assert driver.gave_up("follower-2") is None  # not created yet
    assert driver.a_follower_gave_up() is None


def test_a_follower_that_exited_for_want_of_a_model_is_named_with_the_cause(
    driver, monkeypatch
):
    exited = driver.Container("exited", 3, "2026-10-05T10:00:00Z")
    stand_in(driver, monkeypatch, {"follower-2": exited}, {"follower-2": NO_MODEL + "\n"})
    why = driver.gave_up("follower-2")
    assert why.startswith("follower-2 exited 3")
    assert "cannot transcribe with its start-up model tiny.en" in why
    assert "MODELS=tiny.en" in why  # what to do: build the image with the model baked in
    assert driver.a_follower_gave_up() == why


def test_a_follower_that_keeps_restarting_is_caught_by_what_it_logged(driver, monkeypatch):
    restarting = driver.Container("restarting", 3, "2026-10-05T10:00:00Z")
    stand_in(driver, monkeypatch, {"follower-1": restarting}, {"follower-1": NO_MODEL})
    why = driver.gave_up("follower-1")
    assert "follower-1 cannot load its model" in why and "MODELS=tiny.en" in why


@pytest.mark.parametrize(
    ("code", "hint"),
    [(2, "configuration"), (4, "join token"), (137, "killed"), (1, "unexpected")],
)
def test_any_other_exit_is_named_with_its_code_and_its_last_line(
    driver, monkeypatch, code, hint
):
    exited = driver.Container("exited", code, "2026-10-05T10:00:00Z")
    stand_in(driver, monkeypatch, {"follower-1": exited}, {"follower-1": "one\n\nthe last\n"})
    why = driver.gave_up("follower-1")
    assert f"follower-1 exited {code}" in why and hint in why and why.endswith("the last")


def test_a_wait_ends_at_once_with_the_cause_when_a_follower_has_given_up(driver):
    import asyncio
    import time

    async def never() -> bool:
        return False

    began = time.monotonic()
    with pytest.raises(AssertionError, match="follower-1 exited 3: no model") as failed:
        asyncio.run(
            driver.until(
                never, "both followers to register", 60.0,
                unless=lambda: "follower-1 exited 3: no model",
            )
        )
    assert time.monotonic() - began < 5
    assert "timed out" not in str(failed.value)
    assert "waiting for both followers to register" in str(failed.value)


def test_the_scenario_watches_the_followers_while_it_waits_for_them_to_register(loaded):
    import inspect

    text = inspect.getsource(loaded.scenario)
    assert (
        'await until(both_registered, "both followers to register", unless=a_follower_gave_up)'
        in text
    )


def split_transcript(model="tiny.en", device="cpu", swap=False) -> Transcript:
    def segment(start, text, channel):
        word = Word(start=start, end=start + 1, word=" " + text, probability=0.9)
        return Segment(start=start, end=start + 2, text=text, words=(word,), channel=channel)

    left, right = "The weather today is clear.", "Please send the quarterly report."
    if swap:
        left, right = right, left
    settings = TranscribeSettings(
        model=model,
        compute_type="int8",
        device=device,
        channel_mode="stereo_split",
        channel_labels=("Agent", "Customer"),
    )
    return Transcript(
        source_name="call-1.wav",
        source_checksum="0" * 64,
        duration=5.04,
        settings=settings,
        vocabulary_version=0,
        vocabulary_terms_used=(),
        corrections_applied=(),
        segments=(segment(0.5, left, 0), segment(3.0, right, 1)),
        channel_labels=("Agent", "Customer"),
    )


def store(driver, location, key, made: Transcript) -> None:
    folder = driver.transcript(location, key, "txt").parent
    write_outputs(replace(made, source_name=Path(key).name), folder)


def test_a_split_transcript_with_each_phrase_on_its_own_channel_is_accepted(driver):
    store(driver, driver.CALLS, "day1/call-1.wav", split_transcript())
    driver.check_outputs(driver.CALLS, "day1/call-1.wav")


@pytest.mark.parametrize(
    ("made", "said"),
    [
        (split_transcript(swap=True), "the left channel is wrong"),
        (split_transcript(model="distil-large-v3"), "transcribed with distil-large-v3"),
        (split_transcript(device="cuda"), "transcribed on cuda"),
        (replace(split_transcript(), duration=2.0), "its transcript covers 2 s"),
    ],
)
def test_a_wrong_transcript_is_refused_with_what_is_wrong(driver, made, said):
    store(driver, driver.CALLS, "day1/call-1.wav", made)
    with pytest.raises(AssertionError, match=said):
        driver.check_outputs(driver.CALLS, "day1/call-1.wav")


def test_a_missing_output_is_named(driver):
    with pytest.raises(AssertionError, match=r"calls/day1/call-1.wav has no .txt"):
        driver.check_outputs(driver.CALLS, "day1/call-1.wav")


def test_a_split_transcript_in_the_mono_location_is_refused(driver):
    store(driver, driver.TALKS, "talk-1.wav", split_transcript())
    with pytest.raises(AssertionError, match="was split in the mono location"):
        driver.check_outputs(driver.TALKS, "talk-1.wav")


def test_the_compose_file_and_the_driver_agree(loaded):
    compose = yaml.safe_load((COMPOSE / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    assert set(loaded.FOLLOWERS) <= set(services)
    leader = services["leader-1"]["environment"]
    assert float(leader["SWARMSCRIBE_LEASE_SECONDS"]) == loaded.LEASE_SECONDS
    # The followers reach the leader, and the leader's own links, at the proxy's name.
    follower = services["follower-1"]
    assert leader["SWARMSCRIBE_PUBLIC_URL"] == follower["environment"]["SWARMSCRIBE_LEADER_URL"]
    assert leader["SWARMSCRIBE_PUBLIC_URL"] == "http://proxy" and "proxy" in services
    assert services["postgres"]["ports"] == ["127.0.0.1:15432:5432"]
    assert "@127.0.0.1:15432/" in loaded.DATABASE_URL
    assert follower["environment"]["SWARMSCRIBE_JOIN_TOKEN_FILE"] == "/run/secrets/join-token"
    assert "./work/secrets:/run/secrets:ro" in follower["volumes"]
    assert "./work/data:/data" in services["leader-1"]["volumes"]


def test_the_followers_run_locked_down_and_cannot_reach_the_internet(loaded):
    compose = yaml.safe_load((COMPOSE / "docker-compose.yml").read_text(encoding="utf-8"))
    assert compose["networks"]["inside"] == {"internal": True}
    for name in loaded.FOLLOWERS:
        follower = compose["services"][name]
        assert follower["networks"] == ["inside"], name
        assert follower["read_only"] is True and follower["cap_drop"] == ["ALL"], name
        assert follower["profiles"] == ["followers"], name  # started by the driver, not by `up`
        assert "ports" not in follower, name
        # No token, credential or link is written into the file.
        assert not {"SWARMSCRIBE_JOIN_TOKEN"} & set(follower["environment"]), name


def test_no_port_another_test_uses_is_published(loaded):
    text = (COMPOSE / "docker-compose.yml").read_text(encoding="utf-8")
    for port in ("8900", "8901", '"5432:5432"', "8080:", "18080", "18443"):
        assert port not in text, port


def test_the_memory_limit_admits_the_long_recordings_and_refuses_the_hour(loaded):
    from swarmscribe_follower.memory import job_mb

    compose = yaml.safe_load((COMPOSE / "docker-compose.yml").read_text(encoding="utf-8"))
    environment = compose["services"]["follower-1"]["environment"]
    limit = int(environment["SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB"])
    # Refused whatever the model holds; the driver writes exactly an hour.
    assert job_mb(3600, split=False) > limit
    # Admitted with room to spare: large-v3 on a GPU holds about 1.1 GB on the host.
    longest = 96 * loaded.fixture_seconds()
    assert 1100 + job_mb(longest, split=True) < limit
    assert loaded.TOO_LONG not in loaded.SHORT[loaded.TALKS]
