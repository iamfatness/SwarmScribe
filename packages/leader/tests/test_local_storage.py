import os
from datetime import timedelta
from urllib.parse import urlsplit

import pytest
from swarmscribe_leader.storage.base import StorageError
from swarmscribe_leader.storage.links import LinkSigner
from swarmscribe_leader.storage.local import LocalBackend, version_of
from swarmscribe_leader.storage.registry import backend_for

SIGNER = LinkSigner(b"k" * 32)


def backend(root, clock=lambda: 1_000.0):
    return LocalBackend(
        root, location_id="loc-1", signer=SIGNER, public_url="https://leader/", clock=clock
    )


def write(root, key, data=b"audio"):
    path = root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


async def collect(iterator):
    return [item async for item in iterator]


async def test_list_returns_posix_keys_with_size_and_version(tmp_path):
    a = write(tmp_path, "talks/one.mp3", b"12345")
    write(tmp_path, "talks/deep/two.wav", b"1")
    write(tmp_path, "other.txt")
    items = await collect(backend(tmp_path).list())
    by_key = {i.key: i for i in items}
    assert set(by_key) == {"talks/one.mp3", "talks/deep/two.wav", "other.txt"}
    assert by_key["talks/one.mp3"].size == 5
    assert by_key["talks/one.mp3"].version == version_of(os.stat(a))


async def test_list_filters_by_prefix(tmp_path):
    write(tmp_path, "talks/one.mp3")
    write(tmp_path, "archive/two.mp3")
    keys = [i.key for i in await collect(backend(tmp_path).list("talks/"))]
    assert keys == ["talks/one.mp3"]


async def test_read_text_strips_a_bom_and_returns_none_when_absent(tmp_path):
    write(tmp_path, "consent.txt", "﻿talks/*.mp3\n".encode())
    store = backend(tmp_path)
    assert await store.read_text("consent.txt") == "talks/*.mp3\n"
    assert await store.read_text("missing.txt") is None


async def test_stat(tmp_path):
    write(tmp_path, "a.mp3", b"123")
    store = backend(tmp_path)
    assert (await store.stat("a.mp3")).size == 3
    assert await store.stat("nope.mp3") is None


@pytest.mark.parametrize(
    "key",
    ["../escape.mp3", "talks/../../escape.mp3", "/etc/passwd", "\\windows\\x", "a\\b.mp3", ""],
)
async def test_keys_that_could_escape_the_root_are_rejected(tmp_path, key):
    with pytest.raises(StorageError):
        backend(tmp_path).path_for(key)


async def test_download_link_is_a_signed_get_pinned_to_the_version(tmp_path):
    link = backend(tmp_path).download_link("talks/one.mp3", "5-99", timedelta(minutes=30))
    assert link.method == "GET"
    parts = urlsplit(link.url)
    assert (parts.scheme, parts.netloc) == ("https", "leader")
    assert parts.path.startswith("/v1/files/")
    claims = SIGNER.verify(parts.path.removeprefix("/v1/files/"), now=1_000)
    assert (claims.location_id, claims.key, claims.method, claims.version, claims.expires) == (
        "loc-1",
        "talks/one.mp3",
        "GET",
        "5-99",
        1_000 + 1_800,
    )


async def test_upload_link_is_a_signed_put(tmp_path):
    link = backend(tmp_path).upload_link("transcripts/talks/one.mp3.txt", timedelta(hours=2))
    claims = SIGNER.verify(urlsplit(link.url).path.removeprefix("/v1/files/"), now=1_000)
    assert (link.method, claims.method, claims.version) == ("PUT", "PUT", "")


async def test_links_refuse_bad_keys(tmp_path):
    with pytest.raises(StorageError):
        backend(tmp_path).download_link("../x.mp3", "1-1", timedelta(minutes=1))


async def test_delete(tmp_path):
    path = write(tmp_path, "a.mp3")
    store = backend(tmp_path)
    await store.delete("a.mp3")
    await store.delete("a.mp3")
    assert not path.exists()


async def test_registry_builds_a_local_backend_and_refuses_others(tmp_path, factory):
    location = await factory.location()
    assert isinstance(backend_for(location, signer=SIGNER, public_url="https://l"), LocalBackend)
    azure = await factory.location(backend="azure", config={})
    with pytest.raises(StorageError, match="not available"):
        backend_for(azure, signer=SIGNER, public_url="https://l")
