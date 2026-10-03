import asyncio
import os
from datetime import timedelta

import httpx
import pytest
from swarmscribe_leader.app import create_app
from swarmscribe_leader.config import Settings
from swarmscribe_leader.storage.links import LinkClaims

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
    link = app.state.backend_factory(location).upload_link(
        "transcripts/talks/one.mp3.txt", timedelta(minutes=5)
    )
    response = await client.put(link.url, content=b"hello\n")
    assert response.status_code == 201
    assert (tmp_path / "transcripts" / "talks" / "one.mp3.txt").read_bytes() == b"hello\n"
    assert not any(name.endswith(".upload") for _, _, files in os.walk(tmp_path) for name in files)


async def test_a_get_link_cannot_upload_and_a_put_link_cannot_download(
    app, client, factory, tmp_path
):
    write(tmp_path, "talks/one.mp3")
    location = await factory.location()
    backend = app.state.backend_factory(location)
    get_link = backend.download_link("talks/one.mp3", "", timedelta(minutes=5))
    put_link = backend.upload_link("talks/one.mp3", timedelta(minutes=5))
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


async def test_a_link_to_a_missing_root_answers_400_not_500(app, client, factory, tmp_path):
    location = await factory.location(config={"root": str(tmp_path / "removed")})
    backend = app.state.backend_factory(location)
    get_link = backend.download_link("talks/one.mp3", "", timedelta(minutes=5))
    put_link = backend.upload_link("talks/one.mp3", timedelta(minutes=5))
    for response in (await client.get(get_link.url), await client.put(put_link.url, content=b"x")):
        assert response.status_code == 400
        assert response.json()["code"] == "invalid_key"
    assert not (tmp_path / "removed").exists()


async def test_uploading_over_a_directory_answers_400(app, client, factory, tmp_path):
    (tmp_path / "talks").mkdir()
    location = await factory.location()
    link = app.state.backend_factory(location).upload_link("talks", timedelta(minutes=5))
    response = await client.put(link.url, content=b"x")
    assert response.status_code == 400


async def test_an_oversize_upload_answers_413_and_leaves_nothing(
    app, client, factory, tmp_path, monkeypatch
):
    monkeypatch.setattr("swarmscribe_leader.api.files.MAX_UPLOAD_BYTES", 4)
    location = await factory.location()
    link = app.state.backend_factory(location).upload_link("big.txt", timedelta(minutes=5))
    response = await client.put(link.url, content=b"12345678")
    assert response.status_code == 413
    assert list(tmp_path.iterdir()) == []


async def test_readyz_is_503_when_migrations_are_behind(app, client):
    app.state.head_revision = "not-the-head"
    response = await client.get("/readyz")
    assert response.status_code == 503


async def test_background_loops_start_and_stop_with_the_lifespan(engine, migrated_database_url):
    settings = Settings(
        database_url=migrated_database_url,
        public_url="http://leader",
        link_key=LINK_KEY,
        reaper_interval_seconds=0.05,
        scanner_interval_seconds=0.05,
    )
    application = create_app(settings, background=True)
    async with application.router.lifespan_context(application):
        await asyncio.sleep(0.2)




