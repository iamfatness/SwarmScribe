"""The kind test's hands inside a leader pod (e2e/leader-kind/run_e2e.py pipes this file to
`kubectl exec -i deploy/leader-swarmscribe-leader -- python - <command> ...`).

The cluster has no identity provider, so nobody can sign in to this leader's admin API. This
administers it with the leader's own functions against its database, as the Compose tests
do, and writes the recordings and reads the transcripts, which live on the leader's volume.
It is the test's stand-in for `swarmscribe-admin`, not a way to run a leader. Every command
prints one JSON value. Nothing here prints a credential or a link; `pool-token` prints the
new token, once, for the Secret."""

import asyncio
import json
import os
import sys
import uuid
import wave
from pathlib import Path

from sqlalchemy import select, text
from swarmscribe_leader.auth.pool_tokens import create_pool_token
from swarmscribe_leader.db.models import Follower, Job, JobAttempt, Recording, StorageLocation
from swarmscribe_leader.db.session import make_engine, make_sessionmaker
from swarmscribe_leader.profiles import set_profile

DATA = Path("/data")  # leader-values.yaml: storage.volumes[0].mountPath
FIXTURE = DATA / ".e2e" / "fixture.wav"  # put there by the driver; outside every location
ACTOR = "e2e"


def write_recording(path: Path, repeats: int) -> None:
    """The speech fixture, `repeats` times over, under another name first: the scanner must
    never see half a file."""
    with wave.open(str(FIXTURE), "rb") as source:
        params, frames = source.getparams(), source.readframes(source.getnframes())
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    with wave.open(str(partial), "wb") as out:
        out.setparams(params)
        for _ in range(repeats):
            out.writeframes(frames)
    partial.replace(path)


async def state(session) -> dict:
    """Everything the driver asserts on: the followers, and every job with its attempts."""
    followers = [
        {"id": str(row.id), "state": row.state, "pool": row.pool}
        for row in (await session.scalars(select(Follower))).all()
    ]
    jobs = {}
    rows = await session.execute(
        select(Job, Recording.key, StorageLocation.name)
        .join(Recording, Recording.id == Job.recording_id)
        .join(StorageLocation, StorageLocation.id == Recording.location_id)
    )
    for job, key, location in rows.all():
        attempts = (
            await session.scalars(
                select(JobAttempt)
                .where(JobAttempt.job_id == job.id)
                .order_by(JobAttempt.started_at)
            )
        ).all()
        transcript = DATA / location / "transcripts" / f"{key}.txt"
        jobs[f"{location}/{key}"] = {
            "state": job.state,
            "attempts": job.attempts,
            "failure_reason": job.failure_reason,
            "tried": [[str(a.follower_id), a.outcome] for a in attempts],
            "text": transcript.read_text(encoding="utf-8") if transcript.is_file() else None,
        }
    return {"followers": followers, "jobs": jobs}


async def run(command: str, arguments: list[str]) -> object:
    engine = make_engine(os.environ["SWARMSCRIBE_DATABASE_URL"])
    try:
        async with make_sessionmaker(engine)() as session:
            if command == "pool-token":
                name, pool = arguments
                _, token = await create_pool_token(session, name=name, pool=pool, actor=ACTOR)
                result: object = token
            elif command == "profile":
                device, model, compute_type = arguments
                await set_profile(
                    session, device, model=model, compute_type=compute_type,
                    temperatures=None, actor=ACTOR,
                )
                result = True
            elif command == "location":
                # Written straight to Postgres, as the Compose driver does.
                name, mode, consent = arguments
                (DATA / name).mkdir(parents=True, exist_ok=True)
                (DATA / name / "consent.txt").write_text(consent + "\n", encoding="utf-8")
                session.add(
                    StorageLocation(
                        id=uuid.uuid4(), name=name, backend="local",
                        config={"root": f"/data/{name}"}, input_prefix="",
                        output_prefix="transcripts/", pool="default", required_device="any",
                        scan_interval_s=0, enabled=True, vocabulary_version=0,
                        channel_mode=mode, channel_labels=["Agent", "Customer"],
                    )
                )
                result = True
            elif command == "recording":
                location, key, repeats = arguments
                write_recording(DATA / location / key, int(repeats))
                result = True
            elif command == "revision":
                result = await session.scalar(text("select version_num from alembic_version"))
            elif command == "state":
                result = await state(session)
            else:
                raise SystemExit(f"unknown command {command}")
            await session.commit()
            return result
    finally:
        await engine.dispose()


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run(sys.argv[1], sys.argv[2:]))))
