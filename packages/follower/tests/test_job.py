import json
import logging
import threading
import time

import httpx
import pytest
from follower_testkit import LOST, SPOKEN, FakeEngine, FakeLeader, error, make_runner, sha
from swarmscribe_engine import UndecodableAudioError
from swarmscribe_follower.job import classify
from swarmscribe_follower.leader import Refused
from swarmscribe_follower.lease import SHUTDOWN, JobControl
from swarmscribe_follower.scratch import ScratchDiskFull, ScratchNotOurs, ScratchWipeFailed
from swarmscribe_follower.transfer import OutOfSpace
from swarmscribe_protocol import SegmentsDocument


@pytest.fixture
def leader():
    # Fresh links are asked for at most once per lease per `links_refresh_min_seconds`; the
    # real setting allows 0, and the flows below that are not about the rate use it.
    return FakeLeader(links_min_interval=0)


@pytest.fixture
def engine():
    return FakeEngine()


def run_one(tmp_path, leader, engine, **overrides):
    """Claim the next job and run it. Returns (result, job id, the control it ran under)."""
    runner, client = make_runner(tmp_path, leader, engine, **overrides)
    claim = client.claim()
    control = JobControl()
    return runner.run(claim, control), claim.job_id, control


def deadline_loop(condition, what):
    deadline = time.monotonic() + 5
    while not condition():
        assert time.monotonic() < deadline, what
        time.sleep(0.005)


def after_heartbeats(leader, more=2):
    """Wait until the lease keeper has reported `more` times from now (so that it has seen
    whatever the test just changed in the leader)."""
    target = leader.count("heartbeat") + more
    deadline_loop(lambda: leader.count("heartbeat") >= target, "the lease keeper stopped")


def keeper_has_stopped_the_job():
    """The keeper ends its thread right after it has stopped the job, for any reason that
    makes it stop: wait for that, so the engine's next progress report sees the stop."""
    deadline_loop(
        lambda: not any(t.name == "lease-keeper" and t.is_alive() for t in threading.enumerate()),
        "the lease keeper never stopped the job",
    )


def scratch_is_empty(tmp_path):
    return [entry.name for entry in (tmp_path / "scratch").iterdir()] == [".swarmscribe-scratch"]


def no_call_for_the_job(leader):
    return [k for k in leader.kinds if k not in ("claim", "download", "heartbeat")] == []


# --- the happy path ---------------------------------------------------------------------


def test_a_job_is_downloaded_transcribed_uploaded_and_submitted(tmp_path, leader, engine):
    job_id = leader.add_job(b"the recording", channel_mode="auto", temperatures=[0.0, 0.2])
    leader.jobs[job_id]["vocabulary"] = {
        "version": 3,
        "terms": ["Ashford"],
        "corrections": [{"heard": "ash ford", "replacement": "Ashford"}],
    }
    result, _, _ = run_one(tmp_path, leader, engine)
    assert (result.outcome, leader.jobs[job_id]["state"]) == ("completed", "completed")
    without_heartbeats = [kind for kind in leader.kinds if kind != "heartbeat"]
    assert without_heartbeats == [
        "claim", "download", "upload:txt", "upload:srt", "upload:segments_json", "submit",
    ]
    uploads = leader.jobs[job_id]["uploads"]
    assert uploads["txt"] == (SPOKEN + "\n").encode()
    assert b"00:00:00,000 --> 00:00:01,500" in uploads["srt"]
    document = SegmentsDocument.model_validate_json(uploads["segments_json"])
    assert (document.source_checksum, document.vocabulary_version) == (sha(b"the recording"), 3)
    (submitted,) = leader.submitted
    # The source checksum is the digest of the downloaded bytes; each output's is the digest
    # of the bytes that were uploaded.
    assert submitted["source"] == sha(b"the recording")
    assert (submitted["txt"], submitted["srt"]) == (sha(uploads["txt"]), sha(uploads["srt"]))
    assert submitted["segments_json"] == sha(uploads["segments_json"])
    (heard,) = engine.transcribed
    assert heard["audio"] == b"the recording"
    assert (heard["settings"].channel_mode, heard["settings"].temperatures) == ("auto", (0.0, 0.2))
    assert (heard["settings"].model, heard["settings"].device) == ("distil-large-v3", "cpu")
    assert heard["vocabulary"].version == 3
    assert heard["vocabulary"].terms == ("Ashford",)
    assert heard["vocabulary"].corrections[0].heard == "ash ford"
    assert heard["vocabulary"].corrections[0].replacement == "Ashford"
    assert scratch_is_empty(tmp_path)
    assert leader.bearer_on_links is False


@pytest.mark.parametrize(
    ("mode", "labels"),
    [("mono", None), ("stereo_split", ["Left voice", "Right voice"]), ("auto", None)],
)
def test_every_channel_mode_reaches_the_engine_through_the_claims_settings(
    tmp_path, leader, engine, mode, labels
):
    extra = {} if labels is None else {"channel_labels": labels}
    leader.add_job(channel_mode=mode, **extra)
    result, _, _ = run_one(tmp_path, leader, engine)
    assert result.outcome == "completed"
    (heard,) = engine.transcribed
    assert heard["settings"].channel_mode == mode
    if labels is not None:
        assert heard["settings"].channel_labels == tuple(labels)


def test_a_recording_without_speech_is_a_normal_result(tmp_path, leader, engine):
    job_id = leader.add_job()
    engine.segments = ()
    result, _, _ = run_one(tmp_path, leader, engine)
    assert result.outcome == "completed"
    uploads = leader.jobs[job_id]["uploads"]
    assert (uploads["txt"], uploads["srt"]) == (b"", b"")
    assert SegmentsDocument.model_validate_json(uploads["segments_json"]).segments == []


def test_the_lease_is_renewed_with_progress_while_the_engine_is_busy(tmp_path, leader, engine):
    leader.add_job()
    engine.on_step = lambda fraction: after_heartbeats(leader, 2)
    result, _, _ = run_one(tmp_path, leader, engine)
    assert result.outcome == "completed"
    assert len(leader.heartbeats) >= 8
    assert {0.25, 0.5, 0.75} <= set(leader.heartbeats)


# --- terminal for the job (spec 6.1) ----------------------------------------------------


def test_undecodable_audio_fails_the_job_for_good(tmp_path, leader, engine):
    job_id = leader.add_job()
    engine.error = UndecodableAudioError("cannot decode source: Invalid data found")
    result, _, _ = run_one(tmp_path, leader, engine)
    assert (result.outcome, result.detail) == ("failed", "undecodable")
    (failed,) = leader.failed
    assert (failed["code"], failed["retryable"]) == ("undecodable", False)
    assert "Invalid data found" in failed["reason"]
    assert leader.jobs[job_id]["uploads"] == {} and scratch_is_empty(tmp_path)
    assert leader.jobs[job_id]["state"] == "failed"  # the leader parks it for good
    assert (leader.released, leader.submitted) == ([], [])


@pytest.mark.parametrize(
    "refusal",
    [
        error(412, "source_changed"),
        error(404, "not_found"),
        httpx.Response(412, text="<Error><Code>ConditionNotMet</Code></Error>"),
    ],
)
def test_a_recording_that_changed_or_went_fails_as_source_changed(
    tmp_path, leader, engine, refusal
):
    leader.add_job()
    leader.push("download", refusal)
    result, _, _ = run_one(tmp_path, leader, engine)
    assert result.detail == "source_changed"
    assert (leader.failed[0]["code"], leader.failed[0]["retryable"]) == ("source_changed", False)
    assert engine.transcribed == []


def test_outputs_the_leader_finds_inconsistent_fail_for_good(tmp_path, leader, engine):
    leader.add_job()
    leader.push("submit", error(409, "outputs_inconsistent"))
    result, _, _ = run_one(tmp_path, leader, engine)
    assert (leader.failed[0]["code"], leader.failed[0]["retryable"]) == ("engine_error", False)
    assert leader.count("submit") == 1 and result.outcome == "failed"


def test_an_output_too_large_for_the_storage_fails_for_good(tmp_path, leader, engine):
    leader.add_job()
    leader.push("upload:segments_json", error(413, "too_large"))
    run_one(tmp_path, leader, engine)
    assert (leader.failed[0]["code"], leader.failed[0]["retryable"]) == ("other", False)


# --- retryable on another attempt (spec 6.2) --------------------------------------------


@pytest.mark.parametrize(
    ("raised", "code"),
    [
        (RuntimeError("the model returned nothing"), "engine_error"),
        (ValueError("unexpected"), "engine_error"),
        (RuntimeError("CUDA failed with error out of memory"), "out_of_resources"),
        (MemoryError(), "out_of_resources"),
        (OSError(28, "No space left on device"), "out_of_resources"),
        (OutOfSpace("the recording is 9 bytes; scratch has 1 bytes free"), "out_of_resources"),
    ],
)
def test_an_engine_or_resource_error_fails_the_attempt_retryably(
    tmp_path, leader, engine, raised, code
):
    job_id = leader.add_job()
    engine.error = raised
    result, _, _ = run_one(tmp_path, leader, engine)
    assert (result.outcome, leader.failed[0]["code"], leader.failed[0]["retryable"]) == (
        "failed",
        code,
        True,
    )
    assert leader.jobs[job_id]["uploads"] == {}  # nothing of a failed transcription is sent
    assert leader.jobs[job_id]["state"] == "queued"  # another attempt may be made


def test_a_recording_too_big_for_the_scratch_disk_fails_out_of_resources(
    tmp_path, leader, engine
):
    leader.add_job()
    runner, client = make_runner(tmp_path, leader, engine)

    def no_room(*args, **kwargs):
        raise OutOfSpace("the recording is 9 bytes; scratch has 1 bytes free")

    runner._links.download = no_room
    result = runner.run(client.claim(), JobControl())
    assert (result.outcome, leader.failed[0]["code"]) == ("failed", "out_of_resources")
    assert leader.failed[0]["retryable"] is True and engine.transcribed == []


def test_a_job_folder_that_cannot_be_made_for_lack_of_space_fails_out_of_resources(
    tmp_path, leader, engine
):
    leader.add_job()
    runner, client = make_runner(tmp_path, leader, engine)

    def disk_full(job_id):
        raise ScratchDiskFull("the disk is full")

    runner._scratch.job_dir = disk_full
    result = runner.run(client.claim(), JobControl())
    assert (result.outcome, leader.failed[0]["code"]) == ("failed", "out_of_resources")
    assert leader.failed[0]["retryable"] is True


def test_a_scratch_folder_that_is_not_the_followers_releases_the_job(tmp_path, leader, engine):
    job_id = leader.add_job()
    runner, client = make_runner(tmp_path, leader, engine)

    def not_ours(job_id):
        raise ScratchNotOurs("not ours")

    runner._scratch.job_dir = not_ours
    result = runner.run(client.claim(), JobControl())
    # The agent turns this outcome into exit 2; the job is handed back uncounted.
    assert (result.outcome, leader.released, leader.failed) == ("scratch", [job_id], [])


def test_a_refused_submit_uploads_everything_again_then_succeeds(tmp_path, leader, engine):
    job_id = leader.add_job()
    leader.push("submit", error(409, "checksum_mismatch"), error(409, "outputs_changed"))
    result, _, _ = run_one(tmp_path, leader, engine)
    assert result.outcome == "completed"
    assert (leader.count("submit"), leader.count("upload:txt")) == (3, 3)
    assert leader.jobs[job_id]["state"] == "completed"


def test_outputs_that_really_do_not_match_are_uploaded_again_and_then_accepted(
    tmp_path, leader, engine
):
    """Not a scripted answer: the leader holds a different segments.json than the one the
    checksum is of, as if an upload had been damaged on the way."""
    job_id = leader.add_job()
    damaged = []

    def damage_once():
        if not damaged:
            damaged.append(True)
            leader.jobs[job_id]["uploads"]["segments_json"] = b"{damaged"

    leader.on["submit"] = damage_once
    result, _, _ = run_one(tmp_path, leader, engine)
    assert (result.outcome, leader.count("submit"), leader.count("upload:segments_json")) == (
        "completed",
        2,
        2,
    )


def test_three_refused_submits_fail_the_attempt(tmp_path, leader, engine):
    leader.add_job()
    leader.push("submit", *[error(409, "outputs_missing")] * 3)
    run_one(tmp_path, leader, engine)
    assert (leader.failed[0]["code"], leader.failed[0]["retryable"]) == ("other", True)
    assert leader.count("submit") == 3


def test_a_request_the_leader_does_not_understand_fails_the_attempt(
    tmp_path, leader, engine, caplog
):
    leader.add_job()
    leader.push("submit", error(422, "invalid_request"))
    with caplog.at_level(logging.ERROR):
        run_one(tmp_path, leader, engine)
    assert (leader.failed[0]["code"], leader.failed[0]["retryable"]) == ("other", True)
    assert "422 invalid_request" in leader.failed[0]["reason"]
    assert any(r.levelno == logging.ERROR for r in caplog.records)  # spec 6.2: an error log


def test_a_link_request_the_leader_calls_an_invalid_key_fails_the_attempt(
    tmp_path, leader, engine
):
    leader.add_job()
    leader.push("upload:txt", error(403, "forbidden"))
    leader.push("links", error(400, "invalid_key"))
    run_one(tmp_path, leader, engine)
    assert (leader.failed[0]["code"], leader.failed[0]["retryable"]) == ("other", True)


def test_submit_answered_500_five_times_fails_the_attempt(tmp_path, leader, engine):
    leader.add_job()
    leader.push("submit", *[error(500, "internal", **{"Retry-After": "0"})] * 5)
    run_one(tmp_path, leader, engine)
    assert leader.count("submit") == 5
    assert (leader.failed[0]["code"], leader.failed[0]["retryable"]) == ("other", True)


def test_a_submit_whose_answer_is_lost_is_retried_safely(tmp_path, leader, engine):
    job_id = leader.add_job()
    leader.push("submit", LOST)  # the leader completes the job; the answer never arrives
    result, _, _ = run_one(tmp_path, leader, engine)
    assert result.outcome == "completed"
    assert leader.count("submit") == 2 and len(leader.submitted) == 1
    assert leader.jobs[job_id]["state"] == "completed"
    assert (leader.failed, leader.released) == ([], [])


# --- not the job's fault (spec 6.3) -----------------------------------------------------


def test_a_cancelled_job_is_stopped_mid_transcription_and_nothing_is_called(
    tmp_path, leader, engine
):
    job_id = leader.add_job()

    def cancel_at_half(fraction):
        if fraction == 0.5:
            leader.cancel(job_id)
            keeper_has_stopped_the_job()

    engine.on_step = cancel_at_half
    result, _, _ = run_one(tmp_path, leader, engine)
    assert (result.outcome, result.detail) == ("abandoned", "cancelled")
    assert (leader.failed, leader.released, leader.submitted) == ([], [], [])
    assert not any(kind.startswith("upload") for kind in leader.kinds)
    assert scratch_is_empty(tmp_path)


def test_a_lost_lease_is_abandoned_without_a_call(tmp_path, leader, engine):
    job_id = leader.add_job()

    def lose_at_half(fraction):
        if fraction == 0.5:
            leader.take_over(job_id)
            keeper_has_stopped_the_job()

    engine.on_step = lose_at_half
    result, _, _ = run_one(tmp_path, leader, engine)
    assert (result.outcome, result.detail) == ("abandoned", "lease_lost")
    assert (leader.failed, leader.released) == ([], [])
    assert no_call_for_the_job(leader) and scratch_is_empty(tmp_path)


def test_a_download_link_of_an_ended_lease_abandons_the_job(tmp_path, leader, engine):
    job_id = leader.add_job()
    runner, client = make_runner(tmp_path, leader, engine, heartbeat_interval=3600)
    claim = client.claim()
    leader.take_over(job_id)  # the job is another follower's now; our link is bound to the old
    result = runner.run(claim, JobControl())
    assert (result.outcome, result.detail) == ("abandoned", "lease_lost")
    assert (leader.failed, leader.released, engine.transcribed) == ([], [], [])


def test_an_upload_refused_for_a_stale_lease_abandons_the_job(tmp_path, leader, engine):
    leader.add_job()
    leader.push("upload:srt", error(409, "stale_lease"))
    result, _, _ = run_one(tmp_path, leader, engine, heartbeat_interval=3600)
    assert (result.outcome, leader.failed, leader.released) == ("abandoned", [], [])
    assert leader.count("submit") == 0 and scratch_is_empty(tmp_path)


@pytest.mark.parametrize("answered", [error(409, "stale_lease"), error(404, "not_found")])
@pytest.mark.parametrize("route", ["links", "submit"])
def test_a_stale_lease_from_any_job_call_abandons_the_job(
    tmp_path, leader, engine, route, answered
):
    leader.add_job()
    leader.push("upload:txt", error(403, "forbidden"))  # sends the job to the links route
    leader.push(route, answered)
    result, _, _ = run_one(tmp_path, leader, engine, heartbeat_interval=3600)
    assert (result.outcome, result.detail) == ("abandoned", "lease_lost")
    assert (leader.failed, leader.released) == ([], [])
    assert scratch_is_empty(tmp_path)


def test_a_revoked_follower_stops_and_calls_nothing(tmp_path, leader, engine):
    leader.add_job()

    def revoke_at_half(fraction):
        if fraction == 0.5:
            leader.state = "revoked"
            keeper_has_stopped_the_job()

    engine.on_step = revoke_at_half
    result, _, _ = run_one(tmp_path, leader, engine)
    assert result.outcome == "revoked"
    assert no_call_for_the_job(leader)
    assert scratch_is_empty(tmp_path)


def test_a_401_mid_job_stops_wipes_and_asks_the_agent_to_register_again(
    tmp_path, leader, engine
):
    leader.add_job()

    def forget_credential_at_half(fraction):
        if fraction == 0.5:
            leader.credentials.clear()  # the leader no longer knows this follower
            keeper_has_stopped_the_job()

    engine.on_step = forget_credential_at_half
    result, _, _ = run_one(tmp_path, leader, engine)
    # Not a lost lease, and not a release or a fail: those would be refused with 401 too.
    assert (result.outcome, result.detail) == ("unauthorised", "unauthorised")
    assert no_call_for_the_job(leader) and scratch_is_empty(tmp_path)
    assert (leader.failed, leader.released, leader.submitted) == ([], [], [])


def test_a_401_from_the_links_route_is_the_same(tmp_path, leader, engine):
    leader.add_job()
    leader.push("upload:txt", error(403, "forbidden"))
    leader.push("links", error(401, "unauthorized"))
    result, _, _ = run_one(tmp_path, leader, engine, heartbeat_interval=3600)
    assert result.outcome == "unauthorised"
    assert (leader.failed, leader.released) == ([], [])


def test_a_keeper_that_breaks_releases_the_job_and_never_carries_on_without_it(
    tmp_path, leader, engine
):
    job_id = leader.add_job()
    leader.push("heartbeat", RuntimeError("the keeper's own bug"))
    engine.on_step = lambda fraction: keeper_has_stopped_the_job() if fraction == 0.25 else None
    result, _, _ = run_one(tmp_path, leader, engine)
    assert (result.outcome, result.detail) == ("released", "keeper_failed")
    assert (leader.released, leader.failed) == ([job_id], [])
    assert leader.count("submit") == 0 and scratch_is_empty(tmp_path)


def test_a_shutdown_releases_the_job(tmp_path, leader, engine):
    job_id = leader.add_job()
    runner, client = make_runner(tmp_path, leader, engine)
    control = JobControl()
    engine.on_step = lambda fraction: control.stop(SHUTDOWN) if fraction == 0.5 else None
    result = runner.run(client.claim(), control)
    assert (result.outcome, leader.released, leader.failed) == ("released", [job_id], [])
    assert leader.jobs[job_id]["attempts"] == 0  # the attempt is not counted
    assert scratch_is_empty(tmp_path)


def test_a_job_stopped_before_it_starts_is_released_without_downloading(tmp_path, leader, engine):
    job_id = leader.add_job()
    runner, client = make_runner(tmp_path, leader, engine)
    control = JobControl()
    control.stop(SHUTDOWN)
    result = runner.run(client.claim(), control)
    assert (result.outcome, leader.released) == ("released", [job_id])
    assert leader.count("download") == 0


def test_a_release_the_leader_refuses_as_stale_is_not_an_error(tmp_path, leader, engine):
    leader.add_job()
    leader.push("release", error(409, "stale_lease"))
    runner, client = make_runner(tmp_path, leader, engine)
    control = JobControl()
    control.stop(SHUTDOWN)
    result = runner.run(client.claim(), control)
    assert (result.outcome, result.detail) == ("abandoned", "lease_lost")


def test_a_release_answered_401_asks_the_agent_to_register_again(tmp_path, leader, engine):
    leader.add_job()
    leader.push("release", error(401, "unauthorized"))
    runner, client = make_runner(tmp_path, leader, engine)
    control = JobControl()
    control.stop(SHUTDOWN)
    assert runner.run(client.claim(), control).outcome == "unauthorised"


def test_an_expired_download_link_is_replaced_once(tmp_path, leader, engine):
    job_id = leader.add_job()
    runner, client = make_runner(tmp_path, leader, engine)
    claim = client.claim()
    leader.expire_links(job_id)
    result = runner.run(claim, JobControl())
    assert result.outcome == "completed"
    assert (leader.count("links"), leader.count("download")) == (1, 2)


def test_upload_links_that_expired_during_a_long_job_are_replaced(tmp_path, leader, engine):
    job_id = leader.add_job()
    engine.on_step = lambda fraction: leader.expire_links(job_id) if fraction == 1.0 else None
    result, _, _ = run_one(tmp_path, leader, engine)
    assert result.outcome == "completed"
    assert (leader.count("links"), leader.count("upload:txt")) == (1, 2)
    assert leader.bearer_on_links is False


def test_when_fresh_links_are_refused_too_the_job_is_released(tmp_path, leader, engine):
    job_id = leader.add_job()
    leader.push("upload:txt", error(403, "forbidden"), error(403, "forbidden"))
    result, _, _ = run_one(tmp_path, leader, engine)
    assert (result.outcome, leader.released, leader.failed) == ("released", [job_id], [])
    assert leader.count("links") == 1


def test_fresh_links_asked_too_soon_are_waited_for(tmp_path, leader, engine):
    leader.add_job()
    leader.push("upload:txt", error(403, "forbidden"))
    leader.push("links", error(429, "too_many_requests", **{"Retry-After": "0"}))
    result, _, _ = run_one(tmp_path, leader, engine)
    assert (result.outcome, leader.count("links")) == ("completed", 2)


def test_the_leaders_own_rule_on_fresh_links_is_waited_out(tmp_path):
    """The real rule, not a scripted 429: links asked for within `links_refresh_min_seconds`
    of the lease's last ones are answered 429 with the time left, and the job waits it."""
    leader = FakeLeader(links_min_interval=1)
    engine = FakeEngine()
    job_id = leader.add_job()
    engine.on_step = lambda fraction: leader.expire_links(job_id) if fraction == 1.0 else None
    result, _, _ = run_one(tmp_path, leader, engine)
    assert result.outcome == "completed"
    assert (leader.count("links"), leader.count("upload:txt")) == (2, 2)  # 429, then links
    assert (leader.failed, leader.released) == ([], [])


def test_a_download_that_keeps_failing_is_released_after_ten_minutes(tmp_path, leader, engine):
    job_id = leader.add_job()
    now = [0.0]
    leader.on["download"] = lambda: now.__setitem__(0, now[0] + 200)
    leader.push("download", *[error(503, "unavailable", **{"Retry-After": "0"})] * 10)
    result, _, _ = run_one(tmp_path, leader, engine, clock=lambda: now[0])
    assert (result.outcome, leader.released) == ("released", [job_id])
    assert leader.count("download") == 3


def test_a_model_this_machine_cannot_load_releases_the_job_and_says_unfit(
    tmp_path, leader, engine
):
    job_id = leader.add_job(model="large-v3", compute_type="float16")
    engine.unavailable = {"large-v3"}
    result, _, _ = run_one(tmp_path, leader, engine)
    assert result.outcome == "unfit"
    assert "offline" in result.detail
    assert (leader.released, leader.failed) == ([job_id], [])


def test_a_model_that_does_not_fit_in_memory_is_unfit_too(tmp_path, leader, engine):
    job_id = leader.add_job()
    engine.load_error = MemoryError()
    result, _, _ = run_one(tmp_path, leader, engine)
    assert result.outcome == "unfit"
    assert (leader.released, leader.failed) == ([job_id], [])


def test_a_model_name_that_is_a_path_is_never_loaded(tmp_path, leader, engine):
    leader.add_job(model="/etc/passwd")
    result, _, _ = run_one(tmp_path, leader, engine)
    assert (result.outcome, engine.loads) == ("unfit", [])


# --- retried in place (spec 6.4) --------------------------------------------------------


def test_a_leader_outage_during_upload_and_submit_is_waited_out(tmp_path, leader, engine):
    leader.add_job()
    busy = error(409, "conflict", **{"Retry-After": "0"})
    down = error(503, "unavailable", **{"Retry-After": "0"})
    leader.push("upload:txt", down, httpx.ConnectError("connection refused"), busy)
    leader.push("submit", down, down)
    result, _, _ = run_one(tmp_path, leader, engine)
    assert result.outcome == "completed"
    assert (leader.count("upload:txt"), leader.count("submit")) == (4, 3)


def test_a_fail_that_cannot_be_delivered_is_given_up_after_three_tries(tmp_path, leader, engine):
    leader.add_job()
    engine.error = RuntimeError("broken")
    leader.push("fail", *[error(503, "unavailable")] * 5)
    waits = []
    result, _, _ = run_one(tmp_path, leader, engine, sleep=waits.append)
    assert (result.outcome, leader.count("fail"), leader.failed) == ("failed", 3, [])
    assert waits == [1.0, 2.0]  # between the three tries, never after the last


def cannot_delete_after_the_job_folder_is_made(runner):
    """`Scratch.job_dir` clears a stale folder through `remove` first; only the clean-up
    after the job fails."""
    real_remove = runner._scratch.remove
    calls = []

    def remove(entry):
        calls.append(entry)
        if len(calls) == 1:
            return real_remove(entry)
        raise ScratchWipeFailed(f"{entry} could not be deleted (secret-folder-name)")

    return remove


# --- scratch is always cleaned -----------------------------------------------------------


def test_a_failed_job_leaves_its_scratch_folder_clean(tmp_path, leader, engine):
    leader.add_job()
    engine.error = RuntimeError("broken")
    run_one(tmp_path, leader, engine)
    assert scratch_is_empty(tmp_path)


def test_a_cleanup_that_fails_is_logged_with_ids_and_never_masks_the_outcome(
    tmp_path, leader, engine, caplog
):
    job_id = leader.add_job()
    runner, client = make_runner(tmp_path, leader, engine)

    runner._scratch.remove = cannot_delete_after_the_job_folder_is_made(runner)
    with caplog.at_level(logging.DEBUG):
        result = runner.run(client.claim(), JobControl())
    assert (result.outcome, leader.jobs[job_id]["state"]) == ("completed", "completed")
    warned = [r for r in caplog.records if "scratch folder" in r.getMessage()]
    assert [getattr(r, "job_id", None) for r in warned] == [job_id]  # ids, as fields
    assert "secret-folder-name" not in caplog.text and str(tmp_path) not in caplog.text


def test_a_cleanup_that_fails_after_a_failed_job_still_reports_the_failure(
    tmp_path, leader, engine
):
    leader.add_job()
    engine.error = UndecodableAudioError("not audio")
    runner, client = make_runner(tmp_path, leader, engine)

    runner._scratch.remove = cannot_delete_after_the_job_folder_is_made(runner)
    result = runner.run(client.claim(), JobControl())
    assert (result.outcome, result.detail) == ("failed", "undecodable")


# --- what never reaches a log or a failure reason ----------------------------------------


def test_no_log_line_or_failure_reason_holds_a_link_a_credential_or_the_transcript(
    tmp_path, leader, engine, caplog
):
    with caplog.at_level(logging.DEBUG):
        leader.add_job()
        run_one(tmp_path / "ok", leader, engine)
        leader.add_job()
        leader.push("upload:txt", error(403, "forbidden"))
        leader.push("submit", error(409, "checksum_mismatch"))
        run_one(tmp_path / "again", leader, engine)
        leader.add_job()
        leader.push("download", httpx.ConnectError("cannot reach LINK-SECRET"))
        engine.error = RuntimeError("decoder state")
        run_one(tmp_path / "failed", leader, engine)
    text = caplog.text + json.dumps(leader.failed)
    assert leader.failed, "the third job must have failed"
    for secret in ("LINK-SECRET", "credential-SECRET", "/v1/files/", "Ashford"):
        assert secret not in text, secret
    assert "job completed" in caplog.text


# --- the shutdown estimate ---------------------------------------------------------------


def test_the_time_a_job_still_needs_is_estimated_from_its_progress(tmp_path, leader, engine):
    leader.add_job()
    now = [100.0]
    seen = {}
    runner, client = make_runner(tmp_path, leader, engine, clock=lambda: now[0])
    assert runner.remaining() == 0.0  # idle

    def look(fraction):
        now[0] += 10.0
        seen[fraction] = runner.remaining()

    engine.steps = (0.01, 0.25, 0.5, 1.0)
    engine.on_step = look
    leader.on["upload:txt"] = lambda: seen.__setitem__("upload", runner.remaining())
    runner.run(client.claim(), JobControl())
    # `look` runs before the step is reported, so each estimate uses the step before it.
    assert seen[0.01] is None  # nothing reported yet
    assert seen[0.25] is None  # 1 % done: too little to extrapolate from
    assert seen[0.5] == pytest.approx(30.0 * 0.75 / 0.25 + 30.0)
    assert seen[1.0] == pytest.approx(40.0 * 0.5 / 0.5 + 30.0)
    assert seen["upload"] == 30.0
    assert runner.remaining() == 0.0  # idle again


# --- classify, directly ------------------------------------------------------------------


def test_classify_keeps_reasons_within_the_protocols_limit():
    code, retryable, reason = classify(RuntimeError("x" * 5000))
    assert (code, retryable, len(reason)) == ("engine_error", True, 2000)
    assert classify(Refused(400, "invalid_key", "no"))[:2] == ("other", True)
    assert classify(ScratchDiskFull("full"))[:2] == ("out_of_resources", True)


def test_a_claim_whose_job_id_is_not_a_uuid_is_ignored_entirely(tmp_path, leader, engine):
    leader.add_job()
    runner, client = make_runner(tmp_path, leader, engine)
    claim = client.claim().model_copy(update={"job_id": "../../escape"})
    result = runner.run(claim, JobControl())
    assert (result.outcome, result.detail) == ("abandoned", "invalid job id")
    assert leader.kinds == ["claim"]  # no download, no heartbeat, no fail: no request at all
    assert not (tmp_path / "escape").exists() and scratch_is_empty(tmp_path)


def test_no_lease_keeper_outlives_its_job(tmp_path, leader, engine):
    before = threading.active_count()
    for index in range(5):
        leader.add_job()
        run_one(tmp_path / str(index), leader, engine)
    assert threading.active_count() == before


# --- fix round 1 ---------------------------------------------------------------------------


def test_only_answers_of_500_count_towards_the_five_a_restarting_leader_fails_nothing(
    tmp_path, leader, engine
):
    """A leader that restarts refuses connections, answers 503, then 500 while it starts: a
    finished transcription must not be thrown away for that (spec 6.4, ruling 2)."""
    job_id = leader.add_job()
    retry_now = {"Retry-After": "0"}
    leader.push(
        "submit",
        httpx.ConnectError("refused"),
        error(503, "unavailable", **retry_now),
        error(502, "bad_gateway", **retry_now),
        error(504, "timeout", **retry_now),
        error(500, "internal", **retry_now),
    )
    result, _, _ = run_one(tmp_path, leader, engine)
    assert result.outcome == "completed" and leader.failed == []
    assert leader.count("submit") == 6 and leader.jobs[job_id]["state"] == "completed"


def test_a_500_after_four_other_failures_is_the_first_500_not_the_fifth(
    tmp_path, leader, engine
):
    leader.add_job()
    retry_now = {"Retry-After": "0"}
    unavailable = error(503, "unavailable", **retry_now)
    internal = error(500, "internal", **retry_now)
    answers = [unavailable] * 4 + [internal] * 4
    leader.push("submit", *answers)
    result, _, _ = run_one(tmp_path, leader, engine)
    assert result.outcome == "completed" and leader.failed == []  # 4 x 500 only: not five


def test_a_heartbeat_the_leader_cannot_accept_fails_the_job_other_and_says_why(
    tmp_path, leader, engine, caplog
):
    job_id = leader.add_job()
    leader.push("heartbeat", error(422, "invalid_request"))

    def wait_for_the_keeper(fraction):
        if fraction == 0.5:
            keeper_has_stopped_the_job()

    engine.on_step = wait_for_the_keeper
    result, _, _ = run_one(tmp_path, leader, engine)
    assert (result.outcome, result.detail) == ("failed", "other")
    assert (leader.failed[0]["code"], leader.failed[0]["retryable"]) == ("other", True)
    assert "422 invalid_request" in leader.failed[0]["reason"]
    assert leader.jobs[job_id]["state"] == "queued"  # a retryable failure: another attempt
    assert any(record.levelno >= logging.ERROR for record in caplog.records)


def run_on_a_thread(runner, claim, control):
    outcome = []
    thread = threading.Thread(target=lambda: outcome.append(runner.run(claim, control)))
    thread.start()
    return thread, outcome


def test_a_shutdown_ends_a_submit_retried_after_the_lease_was_lost_and_the_leader_vanished(
    tmp_path, leader, engine
):
    """The worst case of the masked-stop defect: the leader completed the job, the answer was
    lost, the keeper recorded LEASE_LOST, and now nobody answers. The first reason stays
    LEASE_LOST for the report, yet a shutdown must still end the worker within the wait."""
    leader.add_job()
    leader.push("submit", LOST, *[error(503, "unavailable", **{"Retry-After": "3000"})] * 5)
    runner, client = make_runner(tmp_path, leader, engine)
    claim, control = client.claim(), JobControl()
    thread, outcome = run_on_a_thread(runner, claim, control)
    try:
        deadline_loop(
            lambda: control.reason == "lease_lost" and leader.count("submit") >= 2,
            "the lease to be lost during the submit retries",
        )
        assert thread.is_alive()  # waiting out a 3000 s Retry-After, deaf to the lost lease
        began = time.monotonic()
        control.stop(SHUTDOWN)
        thread.join(5)
        assert not thread.is_alive(), "the worker was not stopped by the shutdown"
        assert time.monotonic() - began < 5
    finally:
        control.stop(SHUTDOWN)
        thread.join(5)
    assert outcome[0].outcome == "abandoned" and control.reason == "lease_lost"
    assert (leader.failed, leader.released) == ([], [])
    assert scratch_is_empty(tmp_path)


def test_a_shutdown_that_comes_first_still_ends_a_deaf_submit_wait(tmp_path, leader, engine):
    leader.add_job()
    leader.push("submit", error(503, "unavailable", **{"Retry-After": "3000"}))
    runner, client = make_runner(tmp_path, leader, engine)
    claim, control = client.claim(), JobControl()
    thread, outcome = run_on_a_thread(runner, claim, control)
    try:
        deadline_loop(lambda: leader.count("submit") >= 1, "the first submit")
        control.stop(SHUTDOWN)
        thread.join(5)
        assert not thread.is_alive()
    finally:
        control.stop(SHUTDOWN)
        thread.join(5)
    assert outcome[0].outcome == "released" and leader.released == [claim.job_id]
