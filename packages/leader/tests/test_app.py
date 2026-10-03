import asyncio
import itertools
import os
import time
import uuid
from datetime import timedelta

import httpx
import pytest
from sqlalchemy.exc import DBAPIError
from swarmscribe_leader.app import create_app
from swarmscribe_leader.config import Settings
from swarmscribe_leader.db.models import Job
from swarmscribe_leader.storage.links import LinkClaims
from swarmscribe_leader.storage.local import LocalBackend

LINK_KEY = "k" * 32


@pytest.fixture
async def app(engine, migrated_database_url):
    settings = Settings(
        database_url=migrated_database_url, public_url="http://leader", link_key=LINK_KEY
    )
    application = create_app(settings, background=False)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://leader"
    ) as http:
        yield http


async def leased_job(factory, location):
    follower, _ = await factory.follower()
    recording = await factory.recording(location, key=f"talks/{uuid.uuid4().hex}.mp3")
    return await factory.job(
        recording, state="leased", lease_id=uuid.uuid4(), leased_by=follower.id, attempts=1
    )


async def upload_link(app, factory, location, key, ttl=timedelta(minutes=5)):
    """An upload link bound, like every real one, to a job and its current lease."""
    job = await leased_job(factory, location)
    return app.state.backend_factory(location).upload_link(
        key, ttl, job_id=str(job.id), lease_id=str(job.lease_id)
    )


def no_temp_files(root):
    return not any(".upload" in name for _, _, files in os.walk(root) for name in files)


def write(root, key, data=b"audio bytes"):
    path = root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


async def test_healthz(client):
    response = await client.get("/healthz")
    assert (response.status_code, response.json()) == (200, {"status": "ok"})


async def test_readyz_when_migrated(client):
    response = await client.get("/readyz")
    assert (response.status_code, response.json()) == (200, {"status": "ready"})


async def test_download_through_a_signed_link(app, client, factory, tmp_path):
    write(tmp_path, "talks/one.mp3", b"the audio")
    location = await factory.location()
    backend = app.state.backend_factory(location)
    info = await backend.stat("talks/one.mp3")
    link = backend.download_link("talks/one.mp3", info.version, timedelta(minutes=5))
    response = await client.get(link.url)
    assert (response.status_code, response.content) == (200, b"the audio")


async def test_a_changed_file_answers_412_source_changed(app, client, factory, tmp_path):
    write(tmp_path, "talks/one.mp3", b"before")
    location = await factory.location()
    backend = app.state.backend_factory(location)
    info = await backend.stat("talks/one.mp3")
    link = backend.download_link("talks/one.mp3", info.version, timedelta(minutes=5))
    write(tmp_path, "talks/one.mp3", b"after, and longer")
    response = await client.get(link.url)
    assert response.status_code == 412
    assert response.json()["code"] == "source_changed"


async def test_a_missing_file_answers_404(app, client, factory):
    location = await factory.location()
    backend = app.state.backend_factory(location)
    link = backend.download_link("gone.mp3", "1-1", timedelta(minutes=5))
    response = await client.get(link.url)
    assert response.status_code == 404


async def test_upload_through_a_signed_link_creates_directories(app, client, factory, tmp_path):
    location = await factory.location()
    link = await upload_link(app, factory, location, "transcripts/talks/one.mp3.txt")
    response = await client.put(link.url, content=b"hello\n")
    assert response.status_code == 201
    assert (tmp_path / "transcripts" / "talks" / "one.mp3.txt").read_bytes() == b"hello\n"
    assert no_temp_files(tmp_path)


async def test_an_upload_link_not_bound_to_a_lease_is_refused(app, client, factory, tmp_path):
    location = await factory.location()
    link = app.state.backend_factory(location).upload_link("out.txt", timedelta(minutes=5))
    response = await client.put(link.url, content=b"x")
    assert (response.status_code, response.json()["code"]) == (403, "forbidden")
    assert not (tmp_path / "out.txt").exists()


@pytest.mark.parametrize("problem", ["lease-ended", "other-lease", "no-such-job"])
async def test_an_upload_for_a_lease_that_is_not_current_is_409(
    app, client, factory, tmp_path, problem
):
    write(tmp_path, "out.txt", b"current")
    location = await factory.location()
    job = await leased_job(factory, location)
    job_id, lease_id = str(job.id), str(job.lease_id)
    if problem == "lease-ended":
        async with app.state.sessionmaker() as session:
            stored = await session.get(Job, job.id)
            stored.state, stored.lease_id, stored.leased_by = "queued", None, None
            await session.commit()
    elif problem == "other-lease":
        lease_id = str(uuid.uuid4())
    else:
        job_id = str(uuid.uuid4())
    link = app.state.backend_factory(location).upload_link(
        "out.txt", timedelta(minutes=5), job_id=job_id, lease_id=lease_id
    )
    response = await client.put(link.url, content=b"late")
    assert (response.status_code, response.json()["code"]) == (409, "stale_lease")
    assert (tmp_path / "out.txt").read_bytes() == b"current"
    assert no_temp_files(tmp_path)


async def test_a_lease_that_ends_during_the_upload_does_not_replace_the_file(
    app, client, factory, tmp_path
):
    write(tmp_path, "out.txt", b"current")
    location = await factory.location()
    job = await leased_job(factory, location)
    link = app.state.backend_factory(location).upload_link(
        "out.txt", timedelta(minutes=5), job_id=str(job.id), lease_id=str(job.lease_id)
    )

    async def body():
        yield b"late "
        async with app.state.sessionmaker() as session:  # the reaper takes the lease meanwhile
            stored = await session.get(Job, job.id)
            stored.state, stored.lease_id, stored.leased_by = "queued", None, None
            await session.commit()
        yield b"bytes"

    response = await client.put(link.url, content=body())
    assert (response.status_code, response.json()["code"]) == (409, "stale_lease")
    assert (tmp_path / "out.txt").read_bytes() == b"current"
    assert no_temp_files(tmp_path)


async def test_a_get_link_cannot_upload_and_a_put_link_cannot_download(
    app, client, factory, tmp_path
):
    write(tmp_path, "talks/one.mp3")
    location = await factory.location()
    backend = app.state.backend_factory(location)
    get_link = backend.download_link("talks/one.mp3", "", timedelta(minutes=5))
    put_link = await upload_link(app, factory, location, "talks/one.mp3")
    assert (await client.put(get_link.url, content=b"x")).status_code == 403
    assert (await client.get(put_link.url)).status_code == 403


async def test_forged_and_expired_links_are_refused(app, client, factory, tmp_path):
    write(tmp_path, "talks/one.mp3")
    location = await factory.location()
    expired = app.state.signer.sign(
        LinkClaims(str(location.id), "talks/one.mp3", "GET", "", expires=1)
    )
    for token in ("not-a-token", expired):
        response = await client.get(f"/v1/files/{token}")
        assert response.status_code == 403
        assert response.json()["code"] == "forbidden"


async def test_a_link_to_a_missing_root_answers_503_unavailable(app, client, factory, tmp_path):
    location = await factory.location(config={"root": str(tmp_path / "removed")})
    backend = app.state.backend_factory(location)
    get_link = backend.download_link("talks/one.mp3", "", timedelta(minutes=5))
    put_link = await upload_link(app, factory, location, "talks/one.mp3")
    for response in (await client.get(get_link.url), await client.put(put_link.url, content=b"x")):
        assert response.status_code == 503
        assert response.json() == {
            "code": "unavailable",
            "message": "storage location is not available",
        }
        assert response.headers["retry-after"] == "30"
    assert not (tmp_path / "removed").exists()


async def test_uploading_over_a_directory_answers_400(app, client, factory, tmp_path):
    (tmp_path / "talks").mkdir()
    location = await factory.location()
    link = await upload_link(app, factory, location, "talks")
    response = await client.put(link.url, content=b"x")
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_key"
    assert no_temp_files(tmp_path)


async def test_an_oversize_upload_answers_413_and_leaves_nothing(
    app, client, factory, tmp_path, monkeypatch
):
    monkeypatch.setattr("swarmscribe_leader.api.files.MAX_UPLOAD_BYTES", 4)
    location = await factory.location()
    link = await upload_link(app, factory, location, "big.txt")
    response = await client.put(link.url, content=b"12345678")
    assert response.status_code == 413
    assert list(tmp_path.iterdir()) == []


async def test_readyz_is_503_when_migrations_are_behind(app, client):
    app.state.head_revision = "not-the-head"
    response = await client.get("/readyz")
    assert response.status_code == 503


def _background_app(migrated_database_url):
    settings = Settings(
        database_url=migrated_database_url,
        public_url="http://leader",
        link_key=LINK_KEY,
        reaper_interval_seconds=0.05,
        scanner_interval_seconds=0.05,
    )
    return create_app(settings, background=True)


async def test_background_loops_run_each_step_and_dispose_the_engine(
    engine, migrated_database_url, monkeypatch
):
    ran = []

    async def fake_run_exclusive(_engine, name, work):
        ran.append(name)

    monkeypatch.setattr("swarmscribe_leader.app.run_exclusive", fake_run_exclusive)
    application = _background_app(migrated_database_url)
    pool_before = application.state.engine.sync_engine.pool
    async with application.router.lifespan_context(application):
        await asyncio.sleep(0.3)
    assert {"reaper", "scanner"} <= set(ran)
    # AsyncEngine.dispose() recreates the pool, so a new pool means it was disposed.
    assert application.state.engine.sync_engine.pool is not pool_before


async def test_shutdown_cannot_hang_on_a_stuck_background_step(
    engine, migrated_database_url, monkeypatch
):
    async def blocks_forever(_engine, name, work):
        await asyncio.Event().wait()

    monkeypatch.setattr("swarmscribe_leader.app.run_exclusive", blocks_forever)
    application = _background_app(migrated_database_url)
    started = time.monotonic()
    async with application.router.lifespan_context(application):
        await asyncio.sleep(0.2)
    assert time.monotonic() - started < 15


async def test_the_shutdown_grace_is_ten_seconds():
    from swarmscribe_leader import app as app_module

    assert app_module.SHUTDOWN_GRACE_SECONDS == 10


async def test_upload_under_a_file_answers_400_and_leaves_no_temp(app, client, factory, tmp_path):
    write(tmp_path, "talks/one.mp3")
    location = await factory.location()
    link = await upload_link(app, factory, location, "talks/one.mp3/x.txt")
    response = await client.put(link.url, content=b"x")
    assert (response.status_code, response.json()["code"]) == (400, "invalid_key")
    assert no_temp_files(tmp_path)


async def test_a_blocked_replace_answers_409_with_retry_after(
    app, client, factory, tmp_path, monkeypatch
):
    write(tmp_path, "out.txt", b"old")
    location = await factory.location()
    link = await upload_link(app, factory, location, "out.txt")

    def refuse(*_args, **_kwargs):
        raise PermissionError("in use")

    monkeypatch.setattr("swarmscribe_leader.api.files.os.replace", refuse)
    response = await client.put(link.url, content=b"new")
    assert (response.status_code, response.json()["code"]) == (409, "conflict")
    assert response.headers["retry-after"] == "5"
    assert (tmp_path / "out.txt").read_bytes() == b"old"
    assert no_temp_files(tmp_path)


async def test_other_upload_os_errors_answer_503_unavailable(
    app, client, factory, tmp_path, monkeypatch
):
    location = await factory.location()
    link = await upload_link(app, factory, location, "out.txt")

    def broken(*_args, **_kwargs):
        raise OSError(5, "disk on fire", str(tmp_path))

    monkeypatch.setattr("swarmscribe_leader.api.files.os.replace", broken)
    response = await client.put(link.url, content=b"new")
    assert (response.status_code, response.json()["code"]) == (503, "unavailable")
    assert response.headers["retry-after"] == "30"
    assert str(tmp_path) not in response.text
    assert no_temp_files(tmp_path)


async def test_a_download_read_error_answers_503_unavailable(
    app, client, factory, tmp_path, monkeypatch
):
    write(tmp_path, "talks/one.mp3", b"audio")
    location = await factory.location()
    link = app.state.backend_factory(location).download_link(
        "talks/one.mp3", "", timedelta(minutes=5)
    )

    def unreadable(*_args, **_kwargs):
        raise OSError(5, "I/O error", str(tmp_path))

    monkeypatch.setattr("swarmscribe_leader.api.files.os.fstat", unreadable)
    response = await client.get(link.url)
    assert (response.status_code, response.json()["code"]) == (503, "unavailable")
    assert response.headers["retry-after"] == "30"


@pytest.mark.parametrize(
    "error",
    [
        OSError(5, "raw failure"),
        DBAPIError("select secret", {}, RuntimeError("db down")),
    ],
    ids=["oserror", "dbapierror"],
)
async def test_outages_reaching_the_app_answer_503_without_the_path(app, caplog, error):
    async def outage():
        raise error

    app.add_api_route("/outage-token-xyz", outage)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://leader") as http:
        with caplog.at_level("WARNING"):
            response = await http.get("/outage-token-xyz")
    assert response.status_code == 503
    assert response.json() == {"code": "unavailable", "message": "service temporarily unavailable"}
    assert response.headers["retry-after"] == "10"
    assert caplog.records
    assert "outage-token-xyz" not in caplog.text


async def test_a_240_character_file_name_uploads(app, client, factory, tmp_path):
    name = "n" * 236 + ".txt"
    try:
        (tmp_path / "probe").mkdir()
        (tmp_path / "probe" / name).write_bytes(b"")
    except OSError:
        pytest.skip("this platform cannot hold a 240 character name under the temp directory")
    key = "a/" + name
    location = await factory.location()
    link = await upload_link(app, factory, location, key)
    response = await client.put(link.url, content=b"long")
    assert response.status_code == 201
    assert (tmp_path / "a" / name).read_bytes() == b"long"
    assert no_temp_files(tmp_path)


async def test_the_temp_name_does_not_grow_with_the_target_name(
    app, client, factory, tmp_path, monkeypatch
):
    seen = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append(os.path.basename(src))
        real_replace(src, dst)

    monkeypatch.setattr("swarmscribe_leader.api.files.os.replace", spy)
    location = await factory.location()
    link = await upload_link(app, factory, location, "n" * 100 + ".txt")
    assert (await client.put(link.url, content=b"x")).status_code == 201
    assert len(seen[0]) == len(".upload-") + 32


async def test_unknown_routes_and_wrong_methods_answer_error_body_json(
    app, client, factory, tmp_path
):
    response = await client.get("/v1/nope")
    assert (response.status_code, response.json()["code"]) == (404, "not_found")
    assert set(response.json()) == {"code", "message"}
    write(tmp_path, "talks/one.mp3")
    location = await factory.location()
    backend = app.state.backend_factory(location)
    link = backend.download_link("talks/one.mp3", "", timedelta(minutes=5))
    response = await client.post(link.url)
    assert (response.status_code, response.json()["code"]) == (405, "method_not_allowed")
    assert "allow" in response.headers
    assert set(response.json()) == {"code", "message"}


async def test_unhandled_errors_answer_500_internal_without_the_path(app, caplog):
    async def boom():
        raise RuntimeError("secret detail")

    app.add_api_route("/boom-token-xyz", boom)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://leader") as http:
        response = await http.get("/boom-token-xyz")
    assert response.status_code == 500
    assert response.json() == {"code": "internal", "message": "internal error"}
    assert "boom-token-xyz" not in caplog.text


async def test_request_validation_errors_answer_422_error_body(app, client):
    async def needs_int(n: int):
        return n

    app.add_api_route("/needs-int", needs_int)
    response = await client.get("/needs-int?n=abc")
    assert (response.status_code, response.json()["code"]) == (422, "invalid_request")
    assert set(response.json()) == {"code", "message"}


REGISTER = "/v1/followers/register"


async def test_an_api_body_over_1_mib_is_refused_with_413(client):
    big = b"{" + b" " * (1024 * 1024) + b"}"
    response = await client.post(
        REGISTER, content=big, headers={"content-type": "application/json"}
    )
    assert (response.status_code, response.json()["code"]) == (413, "too_large")


async def test_an_api_body_over_1_mib_without_a_length_is_refused_with_413(client):
    async def chunks():
        for _ in range(17):
            yield b" " * (64 * 1024)

    response = await client.post(
        REGISTER, content=chunks(), headers={"content-type": "application/json"}
    )
    assert "content-length" not in response.request.headers
    assert (response.status_code, response.json()["code"]) == (413, "too_large")


async def test_an_api_body_just_under_1_mib_is_read(client):
    body = b"{" + b" " * (1024 * 1024 - 2) + b"}"
    response = await client.post(
        REGISTER, content=body, headers={"content-type": "application/json"}
    )
    assert response.status_code == 422  # read and validated (no join token in it)


async def test_file_uploads_are_not_held_to_the_api_body_limit(app, client, factory, tmp_path):
    location = await factory.location()
    link = await upload_link(app, factory, location, "big.txt")
    data = b"x" * (2 * 1024 * 1024)
    assert (await client.put(link.url, content=data)).status_code == 201
    assert (tmp_path / "big.txt").read_bytes() == data


async def test_docs_and_openapi_are_not_served(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        response = await client.get(path)
        assert (response.status_code, response.json()["code"]) == (404, "not_found")


async def test_storage_errors_never_reveal_server_paths(app, client, factory, tmp_path):
    location = await factory.location(config={"root": str(tmp_path / "removed")})
    link = app.state.backend_factory(location).download_link("a.mp3", "", timedelta(minutes=5))
    response = await client.get(link.url)
    assert response.status_code == 503
    text = response.text
    assert str(tmp_path) not in text
    assert ":\\" not in text
    assert "/Users" not in text
    assert response.json()["message"] == "storage location is not available"
    good_location = await factory.location()
    bad = app.state.signer.sign(
        LinkClaims(str(good_location.id), "a/../b", "GET", "", 4102444800)
    )
    response = await client.get(f"/v1/files/{bad}")
    assert response.json()["message"] == "invalid storage key"


async def test_a_download_of_a_file_deleted_after_the_link_answers_404(
    app, client, factory, tmp_path
):
    path = write(tmp_path, "talks/one.mp3", b"here")
    location = await factory.location()
    backend = app.state.backend_factory(location)
    info = await backend.stat("talks/one.mp3")
    link = backend.download_link("talks/one.mp3", info.version, timedelta(minutes=5))
    path.unlink()
    response = await client.get(link.url)
    assert (response.status_code, response.json()["code"]) == (404, "not_found")


def _symlink(link, target):
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not permitted here")


async def test_symlinked_keys_are_refused_for_download_and_upload(app, client, factory, tmp_path):
    write(tmp_path, "real.txt", b"secret")
    _symlink(tmp_path / "alias.txt", tmp_path / "real.txt")
    location = await factory.location()
    backend = app.state.backend_factory(location)
    get_link = backend.download_link("alias.txt", "", timedelta(minutes=5))
    put_link = await upload_link(app, factory, location, "alias.txt")
    for response in (await client.get(get_link.url), await client.put(put_link.url, content=b"x")):
        assert (response.status_code, response.json()["code"]) == (400, "invalid_key")
    assert (tmp_path / "real.txt").read_bytes() == b"secret"


async def test_a_declared_oversize_body_is_refused_before_reading(
    app, client, factory, tmp_path, monkeypatch
):
    monkeypatch.setattr("swarmscribe_leader.api.files.MAX_UPLOAD_BYTES", 10)
    location = await factory.location()
    link = await upload_link(app, factory, location, "big.txt")
    response = await client.put(link.url, content=b"x", headers={"content-length": "11"})
    assert (response.status_code, response.json()["code"]) == (413, "too_large")
    assert list(tmp_path.iterdir()) == []


async def test_a_slow_disk_does_not_block_the_event_loop_during_a_download(
    app, client, factory, tmp_path, monkeypatch
):
    write(tmp_path, "talks/one.mp3", b"the audio")
    location = await factory.location()
    backend = app.state.backend_factory(location)
    info = await backend.stat("talks/one.mp3")
    link = backend.download_link("talks/one.mp3", info.version, timedelta(minutes=5))
    real_require_root = LocalBackend._require_root

    def slow_require_root(self):
        time.sleep(0.5)
        real_require_root(self)

    monkeypatch.setattr(LocalBackend, "_require_root", slow_require_root)
    ticks: list[float] = []
    stop = asyncio.Event()

    async def ticker():
        while not stop.is_set():
            ticks.append(time.monotonic())
            await asyncio.sleep(0)

    task = asyncio.create_task(ticker())
    try:
        response = await client.get(link.url)
    finally:
        stop.set()
        await task
    assert (response.status_code, response.content) == (200, b"the audio")
    assert max(b - a for a, b in itertools.pairwise(ticks)) < 0.25


async def test_a_slow_disk_does_not_block_the_event_loop_during_an_upload(
    app, client, factory, tmp_path, monkeypatch
):
    location = await factory.location()
    link = await upload_link(app, factory, location, "transcripts/one.txt")
    real_require_root = LocalBackend._require_root

    def slow_require_root(self):
        time.sleep(0.5)
        real_require_root(self)

    monkeypatch.setattr(LocalBackend, "_require_root", slow_require_root)
    ticks: list[float] = []
    stop = asyncio.Event()

    async def ticker():
        while not stop.is_set():
            ticks.append(time.monotonic())
            await asyncio.sleep(0)

    task = asyncio.create_task(ticker())
    try:
        response = await client.put(link.url, content=b"text")
    finally:
        stop.set()
        await task
    assert response.status_code == 201
    assert (tmp_path / "transcripts" / "one.txt").read_bytes() == b"text"
    assert max(b - a for a, b in itertools.pairwise(ticks)) < 0.25
