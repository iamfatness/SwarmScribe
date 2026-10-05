"""Compose end-to-end scenario for the follower image (follower spec, section 10).

Postgres, two leader replicas behind nginx, and two followers from the real
`swarmscribe-follower` image with tiny.en baked in, read-only, without capabilities and on a
network with no route out. What it proves, in order:

1. both followers load the baked model, register with one join token and compete for six
   short recordings; each is transcribed exactly once, and the outputs say what was said;
2. a follower killed in the middle of a long recording loses nothing: its lease expires,
   the other one transcribes the recording, and heartbeats keep that 8-second lease alive
   through a transcription several times as long;
3. the killed follower, started again, reuses its credential and finds its scratch empty;
4. a follower stopped with SIGTERM in the middle of a job releases it (no attempt is
   counted) and exits 0 within the stop window;
5. a drained follower exits 0, exits 0 again when started again, and takes nothing more;
6. a revoked follower exits 4, and 4 again when started again;
7. the recording without consent is never queued, and no follower log holds the join
   token, a file link or a word of a transcript.

Administration is done with the leader's own functions against its database, as
e2e/compose/run_e2e.py does: this stack runs no identity provider, and `swarmscribe-admin`
needs a signed-in person.

CI runs this (.github/workflows/ci.yml, job follower-compose-e2e):

    python e2e/follower-compose/run_e2e.py prepare   # before `docker compose up`
    python e2e/follower-compose/run_e2e.py run       # after it

Nothing here prints a join token, a credential or a link.
"""

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import time
import uuid
import wave
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from swarmscribe_leader.auth.followers import create_join_token, drain, revoke_follower
from swarmscribe_leader.clock import utcnow
from swarmscribe_leader.db.models import Follower, Job, JobAttempt, Recording, StorageLocation
from swarmscribe_leader.db.session import make_engine, make_sessionmaker
from swarmscribe_leader.profiles import set_profile
from swarmscribe_protocol import SegmentsDocument

HERE = Path(__file__).resolve().parent
COMPOSE_FILE = HERE / "docker-compose.yml"
WORK = HERE / "work"
DATA = WORK / "data"  # mounted at /data in the leaders
SECRETS = WORK / "secrets"  # mounted at /run/secrets in the followers
FIXTURE = HERE.parents[1] / "packages" / "engine" / "tests" / "fixtures" / "stereo_speech.wav"
DATABASE_URL = "postgresql://postgres:postgres@127.0.0.1:15432/swarmscribe"  # docker-compose.yml
FOLLOWERS = ("follower-1", "follower-2")


@dataclass(frozen=True)
class Target:
    """The image under test: its device, the model baked into it, and the Compose files."""

    device: str
    model: str
    compute_type: str
    files: tuple[Path, ...]
    # A long recording is the fixture this many times over. It must take the followers long
    # enough to be interrupted in the middle and to outlast the lease: about 25 s.
    long_repeats: int


# CI, and the default. Four minutes of audio: on two cores tiny.en needs some 30 s for it
# on a fast machine and about a minute on a CI runner.
CPU = Target("cpu", "tiny.en", "int8", (COMPOSE_FILE,), 48)
target = CPU

# The fixture: the left channel says "The weather today is clear and bright", then the right
# says "Please send the quarterly report by Friday" (make_stereo_speech.ps1).
LEFT_WORD, RIGHT_WORD = "weather", "report"
CALLS, TALKS = "calls", "talks"  # two locations: split into Agent/Customer, and mono
LABELS = ("Agent", "Customer")
SHORT = {
    CALLS: tuple(f"day1/call-{n}.wav" for n in (1, 2, 3, 4)),
    TALKS: ("talk-1.wav", "talk-2.wav"),
}
UNCONSENTED = "private/held.wav"  # in CALLS, matched by no line of its consent.txt
KILLED, STOPPED, AFTER_DRAIN = "long/kill.wav", "long/stop.wav", "day2/after-drain.wav"
CONSENT = {CALLS: "day*/*.wav\nlong/*.wav\n", TALKS: "*.wav\n"}

LEASE_SECONDS = 8.0  # SWARMSCRIBE_LEASE_SECONDS in docker-compose.yml
MID_JOB_SECONDS = 5.0  # how long a long job has been held when it is killed or stopped
STOP_WITHIN_SECONDS = 10.0  # what `docker stop` allows before it kills (measured: 1 to 5)
STEP_SECONDS = 180.0  # the longest any one wait may take
SCENARIO_SECONDS = 900.0
RERUN_HINT = (
    "this scenario drains and revokes its followers and cannot be run twice on the same "
    "stack: run `docker compose -f e2e/follower-compose/docker-compose.yml --profile "
    "followers down -v`, then `prepare` and `up -d` again"
)


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


# --- files ----------------------------------------------------------------------------


def fixture_seconds() -> float:
    with wave.open(str(FIXTURE), "rb") as source:
        return source.getnframes() / source.getframerate()


def write_recording(path: Path, repeats: int = 1) -> None:
    """The fixture, `repeats` times over, written under another name first: the leader's
    scanner must never see half a file."""
    with wave.open(str(FIXTURE), "rb") as source:
        params, frames = source.getparams(), source.readframes(source.getnframes())
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    with wave.open(str(partial), "wb") as out:
        out.setparams(params)
        for _ in range(repeats):
            out.writeframes(frames)
    partial.replace(path)


def prepare() -> None:
    """The two locations' folders and the (still empty) secrets folder, before
    `docker compose up`: a bind mount of a missing folder would be created by root."""
    if WORK.exists():
        try:
            shutil.rmtree(WORK)
        except OSError as error:
            raise AssertionError(
                f"cannot clear {WORK} ({type(error).__name__}); on Linux the leaders wrote the "
                "transcripts as root: delete the folder with sudo"
            ) from None
    for location, keys in SHORT.items():
        root = DATA / location
        root.mkdir(parents=True)
        (root / "consent.txt").write_text(CONSENT[location], encoding="utf-8", newline="\n")
        for key in keys:
            write_recording(root / key)
    write_recording(DATA / CALLS / UNCONSENTED)
    SECRETS.mkdir(parents=True)
    # Readable inside the followers whatever user they run as (the image's is 10001).
    SECRETS.chmod(0o755)


def transcript(location: str, key: str, suffix: str) -> Path:
    return DATA / location / "transcripts" / f"{key}.{suffix}"


def check_outputs(location: str, key: str, *, repeats: int = 1) -> None:
    """The three outputs are in storage, validate against the protocol's schema, cover the
    whole recording and carry the words the fixture says (each on its own channel, in the
    split location)."""
    for suffix in ("txt", "srt", "segments.json"):
        expect(transcript(location, key, suffix).is_file(), f"{location}/{key} has no .{suffix}")
    document = SegmentsDocument.model_validate_json(
        transcript(location, key, "segments.json").read_text(encoding="utf-8")
    )
    used = document.settings.model
    expect(used == target.model, f"{key} was transcribed with {used}, not {target.model}")
    expect(document.device == target.device, f"{key} was transcribed on {document.device}")
    # A long recording is one phrase many times over, and a model drops some of the
    # repeats: its length says that all of it was read, the words that it was heard.
    length = fixture_seconds() * repeats
    expect(
        abs(document.duration - length) < 1.0,
        f"{location}/{key} is {length:.0f} s long; its transcript covers {document.duration:.0f} s",
    )
    text = transcript(location, key, "txt").read_text(encoding="utf-8")
    for word in (LEFT_WORD, RIGHT_WORD):
        expect(word in text.lower(), f"{location}/{key}: the transcript never says '{word}'")
    if location == TALKS:
        expect(document.channel_labels is None, f"{key} was split in the mono location")
        return
    expect(tuple(document.channel_labels or ()) == LABELS, f"{key} has no channel labels")
    lines = text.splitlines()
    expect(
        all(line.startswith(("Agent: ", "Customer: ")) for line in lines),
        f"{key}: a line of the split transcript names no speaker",
    )
    agent = " ".join(line for line in lines if line.startswith("Agent: ")).lower()
    customer = " ".join(line for line in lines if line.startswith("Customer: ")).lower()
    expect(LEFT_WORD in agent and RIGHT_WORD not in agent, f"{key}: the left channel is wrong")
    expect(RIGHT_WORD in customer and LEFT_WORD not in customer, f"{key}: the right is wrong")


# --- docker compose -------------------------------------------------------------------


def compose(*arguments: str) -> subprocess.CompletedProcess:
    files = [part for file in target.files for part in ("-f", str(file))]
    return subprocess.run(
        ["docker", "compose", *files, "--profile", "followers", *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def must(done: subprocess.CompletedProcess, what: str) -> str:
    expect(done.returncode == 0, f"{what} failed: {done.stderr.strip()[-500:]}")
    return done.stdout


@dataclass(frozen=True)
class Container:
    status: str  # running, exited, ...
    exit_code: int
    finished_at: str


def container(service: str) -> Container | None:
    container_id = compose("ps", "-a", "-q", service).stdout.strip()
    if not container_id:
        return None
    done = subprocess.run(
        ["docker", "inspect", "--format", "{{json .State}}", container_id],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    state = json.loads(must(done, f"inspecting {service}"))
    return Container(state["Status"], int(state["ExitCode"]), state["FinishedAt"])


def logs(service: str) -> str:
    done = compose("logs", "--no-color", "--no-log-prefix", service)
    return must(done, f"reading {service}'s logs")


def registrations(service: str) -> list[str]:
    """The follower ids this container has registered as, from its JSON log, oldest first."""
    found = []
    for line in logs(service).splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("event") == "registered":
            found.append(str(entry.get("follower_id")))
    return found


# What a follower prints when its start-up model cannot be loaded (agent.py), before it exits
# 3. An image built without MODELS, or with another model, does that at once here: the
# followers' network has no route out to download one.
MODEL_FAILURE = "cannot transcribe with its start-up model"
EXIT_MEANS = {
    2: "its configuration or its state folder is refused",
    3: "this machine or image cannot do the work",
    4: "it is not authorised: the join token was refused",
    5: "the leader speaks another protocol version",
    137: "it was killed",
}


def gave_up(service: str) -> str | None:
    """Why this follower will never register: it has exited, or it has said that its model
    cannot be loaded (it may be between restarts). None while it is still on its way. The
    answer names the cause, so that a wait for it need not run to its timeout. The last log
    line is safe to show: a follower's exit reason never holds a token, credential or link."""
    seen = container(service)
    if seen is None:
        return None
    said = [line.strip() for line in logs(service).splitlines() if line.strip()]
    last = said[-1][:400] if said else "(it logged nothing)"
    failed = next((line[:400] for line in reversed(said) if MODEL_FAILURE in line), None)
    bake = (
        f". Is the image built with `--build-arg MODELS={target.model}`? This scenario needs"
        " the model baked in: its followers have no route out to download one. "
    )
    exited = ""
    if seen.status == "exited":
        means = EXIT_MEANS.get(seen.exit_code, "an unexpected error")
        exited = f"{service} exited {seen.exit_code} ({means})"
    if failed is not None:
        subject = f"{exited}: it" if exited else service
        return f"{subject} cannot load its model{bake}It said: {failed}"
    if exited:
        return f"{exited} before it registered: {last}"
    return None


def a_follower_gave_up() -> str | None:
    return next((why for why in map(gave_up, FOLLOWERS) if why), None)


def start(service: str) -> None:
    must(compose("start", service), f"starting {service}")


async def wait_for_exit(service: str, *, after: str | None, within: float) -> Container:
    """Wait until the container has exited (again, when `after` is the time it last did)."""
    deadline = time.monotonic() + within
    seen: Container | None = None
    while time.monotonic() < deadline:
        seen = container(service)
        if seen is not None and seen.status == "exited" and seen.finished_at != after:
            return seen
        await asyncio.sleep(0.2)
    raise AssertionError(f"{service} did not exit within {within:.0f} s (it is {seen})")


# --- the leader's database -------------------------------------------------------------

Sessions = async_sessionmaker[AsyncSession]


async def until(
    check: Callable[[], Awaitable[Any]],
    what: str,
    within: float = STEP_SECONDS,
    *,
    unless: Callable[[], str | None] | None = None,
) -> Any:
    """Wait for `check`. `unless` is asked on every round and ends the wait at once, with
    its own words, when what is waited for can no longer happen."""
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        found = await check()
        if found:
            return found
        why = unless() if unless is not None else None
        if why:
            raise AssertionError(f"{why} (while waiting for {what})")
        await asyncio.sleep(0.5)
    raise AssertionError(f"timed out after {within:.0f} s waiting for {what}")


async def join_token_and_profile(sessions: Sessions) -> str:
    """A join token good for exactly two registrations (a follower that registered again on
    a restart would be refused), and the device's profile set to the baked model."""
    async with sessions() as session:
        _, token = await create_join_token(
            session,
            pool="default",
            expires_at=utcnow() + timedelta(hours=1),
            max_uses=len(FOLLOWERS),
            created_by="e2e",
        )
        await set_profile(
            session,
            target.device,
            model=target.model,
            compute_type=target.compute_type,
            temperatures=None,
            actor="e2e",
        )
        await session.commit()
    return token


async def add_locations(sessions: Sessions) -> None:
    """Written straight to Postgres: `add_location` checks the folder on the machine it runs
    on, and /data exists only inside the leaders."""
    async with sessions() as session:
        for name, mode in ((CALLS, "stereo_split"), (TALKS, "mono")):
            session.add(
                StorageLocation(
                    id=uuid.uuid4(),
                    name=name,
                    backend="local",
                    config={"root": f"/data/{name}"},
                    input_prefix="",
                    output_prefix="transcripts/",
                    pool="default",
                    required_device="any",
                    scan_interval_s=0,
                    enabled=True,
                    vocabulary_version=0,
                    channel_mode=mode,
                    channel_labels=list(LABELS),
                )
            )
        await session.commit()


async def followers(sessions: Sessions) -> dict[str, Follower]:
    async with sessions() as session:
        return {str(f.id): f for f in (await session.scalars(select(Follower))).all()}


async def job_of(sessions: Sessions, location: str, key: str) -> Job | None:
    async with sessions() as session:
        return await session.scalar(
            select(Job)
            .join(Recording, Recording.id == Job.recording_id)
            .join(StorageLocation, StorageLocation.id == Recording.location_id)
            .where(StorageLocation.name == location, Recording.key == key)
        )


async def attempts_of(sessions: Sessions, job: Job) -> list[tuple[str, str | None, float]]:
    """(follower id, outcome, seconds it lasted) for each attempt at the job, oldest first."""
    async with sessions() as session:
        rows = (
            await session.scalars(
                select(JobAttempt)
                .where(JobAttempt.job_id == job.id)
                .order_by(JobAttempt.started_at)
            )
        ).all()
    return [
        (
            str(row.follower_id),
            row.outcome,
            (row.ended_at - row.started_at).total_seconds() if row.ended_at else 0.0,
        )
        for row in rows
    ]


async def completed(sessions: Sessions, location: str, key: str) -> Job | None:
    job = await job_of(sessions, location, key)
    return job if job is not None and job.state == "completed" else None


async def held_mid_job(sessions: Sessions, location: str, key: str) -> tuple[Job, str]:
    """Wait until a follower has held the job for MID_JOB_SECONDS; returns the job and the
    holder's follower id."""

    async def leased() -> Job | None:
        job = await job_of(sessions, location, key)
        expect(
            job is None or job.state != "completed",
            f"{key} was transcribed before it could be interrupted; raise the target's"
            " long_repeats",
        )
        return job if job is not None and job.state == "leased" else None

    first = await until(leased, f"a follower to take {key}")
    await asyncio.sleep(MID_JOB_SECONDS)
    job = await leased()
    expect(
        job is not None and job.lease_id == first.lease_id,
        f"{key} did not stay with one follower for {MID_JOB_SECONDS:.0f} s",
    )
    return job, str(job.leased_by)


# --- the scenario ---------------------------------------------------------------------


@dataclass(frozen=True)
class Report:
    registered_after: float
    long_job_seconds: float
    stop_seconds: float


async def scenario(sessions: Sessions) -> Report:
    expect(not await followers(sessions), f"the leader already has followers; {RERUN_HINT}")
    expect(all(container(name) is None for name in FOLLOWERS), f"followers exist; {RERUN_HINT}")

    # 1. Two followers register with one token and compete.
    token = await join_token_and_profile(sessions)
    (SECRETS / "join-token").write_text(token, encoding="utf-8")
    (SECRETS / "join-token").chmod(0o644)
    started = time.monotonic()
    must(compose("up", "-d", "--no-deps", *FOLLOWERS), "starting the followers")

    async def both_registered() -> bool:
        return len(await followers(sessions)) == len(FOLLOWERS)

    await until(both_registered, "both followers to register", unless=a_follower_gave_up)
    registered_after = time.monotonic() - started

    async def both_said_so() -> bool:
        return all(registrations(name) for name in FOLLOWERS)

    await until(both_said_so, "both followers to log their registration", 30.0)
    service_of = {registrations(name)[0]: name for name in FOLLOWERS}
    rows = await followers(sessions)
    expect(set(service_of) == set(rows), "the followers' logs and the leader disagree on their ids")
    for row in rows.values():
        told = row.capabilities
        expect(
            told.get("device") == target.device, f"a follower registered as {told.get('device')}"
        )
        expect(target.model in told.get("models", []), "a follower did not report its model")

    await add_locations(sessions)
    short = [(location, key) for location, keys in SHORT.items() for key in keys]

    async def all_short_done() -> bool:
        return all([await completed(sessions, location, key) for location, key in short])

    await until(all_short_done, "the six short recordings to complete")
    winners = set()
    for location, key in short:
        check_outputs(location, key)
        attempts = await attempts_of(sessions, await job_of(sessions, location, key))
        expect(
            [outcome for _, outcome, _ in attempts] == ["completed"],
            f"{location}/{key} took {len(attempts)} attempts: {[a[1] for a in attempts]}",
        )
        winners.add(attempts[0][0])
    expect(winners == set(service_of), "one follower transcribed everything: no competition")

    # 2. A follower killed mid-job loses nothing; heartbeats keep the survivor's lease.
    write_recording(DATA / CALLS / KILLED, target.long_repeats)
    job, victim = await held_mid_job(sessions, CALLS, KILLED)
    must(compose("kill", service_of[victim]), f"killing {service_of[victim]}")
    print(f"killed {service_of[victim]} mid-job", flush=True)
    job = await until(lambda: completed(sessions, CALLS, KILLED), f"{KILLED} to complete", 300.0)
    attempts = await attempts_of(sessions, job)
    expect(
        [(who, outcome) for who, outcome, _ in attempts]
        == [(victim, "expired"), (next(f for f in service_of if f != victim), "completed")],
        f"{KILLED}: expected the victim's lease to expire and the other to finish, got "
        f"{[(service_of.get(who, who), outcome) for who, outcome, _ in attempts]}",
    )
    expect(job.attempts == 2, f"{KILLED} counts {job.attempts} attempts, not 2")
    long_job_seconds = attempts[1][2]
    expect(
        long_job_seconds >= LEASE_SECONDS * 1.5,
        f"{KILLED} took {long_job_seconds:.0f} s, too short to prove that heartbeats keep a "
        f"{LEASE_SECONDS:.0f} s lease; raise the target's long_repeats",
    )
    check_outputs(CALLS, KILLED, repeats=target.long_repeats)

    # 3. Started again, the killed follower is the same follower, with an empty scratch.
    await come_back(sessions, service_of[victim], victim)
    left = must(
        compose("exec", "-T", service_of[victim], "ls", "-A", "/scratch"), "listing scratch"
    ).split()
    expect(left == [".swarmscribe-scratch"], f"the killed follower's scratch holds {left}")

    # 4. SIGTERM mid-job: released, not counted, out within the stop window.
    write_recording(DATA / CALLS / STOPPED, target.long_repeats)
    job, holder = await held_mid_job(sessions, CALLS, STOPPED)
    before = container(service_of[holder])
    asked = time.monotonic()
    must(compose("kill", "-s", "SIGTERM", service_of[holder]), f"stopping {service_of[holder]}")
    ended = await wait_for_exit(service_of[holder], after=before.finished_at, within=30.0)
    stop_seconds = time.monotonic() - asked
    expect(ended.exit_code == 0, f"a follower stopped mid-job exited {ended.exit_code}, not 0")
    expect(
        stop_seconds <= STOP_WITHIN_SECONDS,
        f"a follower stopped mid-job took {stop_seconds:.1f} s (limit {STOP_WITHIN_SECONDS:.0f} s)",
    )
    print(f"stopped {service_of[holder]} mid-job in {stop_seconds:.1f} s", flush=True)
    job = await until(lambda: completed(sessions, CALLS, STOPPED), f"{STOPPED} to complete", 300.0)
    attempts = await attempts_of(sessions, job)
    expect(
        [(who, outcome) for who, outcome, _ in attempts][:1] == [(holder, "released")]
        and attempts[-1][1] == "completed",
        f"{STOPPED}: expected a release, then a completion, got {[a[1] for a in attempts]}",
    )
    expect(job.attempts == 1, f"{STOPPED} counts {job.attempts} attempts; a release counts none")
    check_outputs(CALLS, STOPPED, repeats=target.long_repeats)
    await come_back(sessions, service_of[holder], holder)

    # 5. Drain: exit 0, exit 0 again when started again, and nothing more is taken.
    drained, revoked = sorted(service_of, key=service_of.get)
    before = container(service_of[drained])
    async with sessions() as session:
        await drain(session, uuid.UUID(drained), actor="e2e")
        await session.commit()
    ended = await wait_for_exit(service_of[drained], after=before.finished_at, within=60.0)
    expect(ended.exit_code == 0, f"a drained follower exited {ended.exit_code}, not 0")
    start(service_of[drained])
    again = await wait_for_exit(service_of[drained], after=ended.finished_at, within=60.0)
    expect(again.exit_code == 0, f"a drained follower, restarted, exited {again.exit_code}")
    write_recording(DATA / CALLS / AFTER_DRAIN)
    job = await until(lambda: completed(sessions, CALLS, AFTER_DRAIN), f"{AFTER_DRAIN} to complete")
    attempts = await attempts_of(sessions, job)
    expect(
        [(who, outcome) for who, outcome, _ in attempts] == [(revoked, "completed")],
        "a recording queued after the drain was not taken by the follower that is left",
    )
    check_outputs(CALLS, AFTER_DRAIN)
    expect((await followers(sessions))[drained].state == "draining", "the drain did not last")

    # 6. Revoke: exit 4, and 4 again.
    before = container(service_of[revoked])
    async with sessions() as session:
        await revoke_follower(session, uuid.UUID(revoked), now=utcnow(), actor="e2e")
        await session.commit()
    ended = await wait_for_exit(service_of[revoked], after=before.finished_at, within=60.0)
    expect(ended.exit_code == 4, f"a revoked follower exited {ended.exit_code}, not 4")
    start(service_of[revoked])
    again = await wait_for_exit(service_of[revoked], after=ended.finished_at, within=60.0)
    expect(again.exit_code == 4, f"a revoked follower, restarted, exited {again.exit_code}")

    # 7. Nothing registered twice, nothing without consent was touched, nothing leaked.
    rows = await followers(sessions)
    expect(len(rows) == len(FOLLOWERS), f"the leader has {len(rows)} follower rows, not 2")
    expect(rows[revoked].state == "revoked", "the revocation did not last")
    for name in FOLLOWERS:
        expect(len(registrations(name)) == 1, f"{name} registered more than once")
        said = logs(name)
        expect(token not in said, f"{name}'s log holds the join token")
        expect("/v1/files/" not in said, f"{name}'s log holds a file link")
        expect(
            LEFT_WORD not in said.lower() and "quarterly" not in said.lower(),
            f"{name}'s log holds transcript text",
        )
    expect(await job_of(sessions, CALLS, UNCONSENTED) is None, "a job exists without consent")
    expect(
        not transcript(CALLS, UNCONSENTED, "txt").exists(),
        "the recording without consent was transcribed",
    )
    return Report(registered_after, long_job_seconds, stop_seconds)


async def come_back(sessions: Sessions, service: str, follower_id: str) -> None:
    """Start a stopped follower container again and wait until the leader hears from it:
    the same follower (its credential is in the container's state volume), not a new one."""
    last_seen = (await followers(sessions))[follower_id].last_seen_at
    start(service)

    async def heard() -> bool:
        row = (await followers(sessions))[follower_id]
        return row.last_seen_at > last_seen and row.state == "active"

    await until(heard, f"{service} to come back as the same follower")
    expect(len(registrations(service)) == 1, f"{service} registered again after a restart")
    expect(len(await followers(sessions)) == len(FOLLOWERS), "a restart added a follower row")


async def run() -> Report:
    engine = make_engine(DATABASE_URL)
    try:
        return await asyncio.wait_for(scenario(make_sessionmaker(engine)), SCENARIO_SECONDS)
    except TimeoutError:
        raise AssertionError(f"the scenario did not finish in {SCENARIO_SECONDS:.0f} s") from None
    finally:
        await engine.dispose()
        # Spent by now (both uses taken) and its database goes with `down -v`; still, a
        # token is not left lying in the work folder.
        (SECRETS / "join-token").unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="SwarmScribe follower Compose scenario")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("prepare", help="write the recordings and folders (before up)")
    commands.add_parser("run", help="run the scenario against the running Compose project")
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            prepare()
            print(f"wrote {WORK}")
            return 0
        report = asyncio.run(run())
    except AssertionError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    print(
        f"passed ({target.model} on {target.device}): two followers registered in "
        f"{report.registered_after:.0f} s and shared six recordings; a killed follower's job "
        f"was redone in {report.long_job_seconds:.0f} s under an {LEASE_SECONDS:.0f} s lease; "
        f"a stop mid-job took {report.stop_seconds:.1f} s and counted no attempt; drain exited "
        "0 and revoke exited 4, twice each"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
