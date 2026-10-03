import base64
import hashlib
import os
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from swarmscribe_leader.storage.base import StorageError, StorageUnavailable
from swarmscribe_leader.storage.links import LinkClaims, LinkSigner
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
    write(tmp_path, "consent.txt", "\ufefftalks/*.mp3\n".encode())
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


async def test_an_upload_link_carries_its_job_and_lease(tmp_path):
    link = backend(tmp_path).upload_link(
        "out.txt", timedelta(hours=2), job_id="job-1", lease_id="lease-1"
    )
    claims = SIGNER.verify(urlsplit(link.url).path.removeprefix("/v1/files/"), now=1_000)
    assert (claims.job_id, claims.lease_id) == ("job-1", "lease-1")


async def test_a_download_link_is_unchanged_by_the_lease_fields(tmp_path):
    link = backend(tmp_path).download_link("a.mp3", "1-1", timedelta(minutes=30))
    expected = SIGNER.sign(LinkClaims("loc-1", "a.mp3", "GET", "1-1", 1_000 + 1_800))
    assert link.url == f"https://leader/v1/files/{expected}"
    payload = urlsplit(link.url).path.removeprefix("/v1/files/").split(".")[0]
    assert b"lease" not in base64.urlsafe_b64decode(payload + "==")


async def test_sha256_of_a_stored_object(tmp_path):
    write(tmp_path, "out.txt", b"hello\n")
    store = backend(tmp_path)
    assert await store.sha256("out.txt") == hashlib.sha256(b"hello\n").hexdigest()
    assert await store.sha256("missing.txt") is None


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


def symlink(link, target, is_dir=False):
    try:
        os.symlink(target, link, target_is_directory=is_dir)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted here")


async def test_list_skips_symlinks_and_dangling_links(tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.mp3").write_bytes(b"s")
    write(root, "ok.mp3")
    symlink(root / "link.mp3", outside / "secret.mp3")
    symlink(root / "dangling.mp3", outside / "nope.mp3")
    symlink(root / "dirlink", outside, is_dir=True)
    keys = [i.key for i in await collect(backend(root).list())]
    assert keys == ["ok.mp3"]


async def test_list_survives_a_file_vanishing_before_stat(tmp_path, monkeypatch):
    write(tmp_path, "a.mp3")
    write(tmp_path, "b.mp3")
    real_stat = type(tmp_path).stat

    def flaky(self, *args, **kwargs):
        if self.name == "a.mp3":
            raise FileNotFoundError(2, "gone", str(self))
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(type(tmp_path), "stat", flaky)
    keys = [i.key for i in await collect(backend(tmp_path).list())]
    assert keys == ["b.mp3"]


@pytest.mark.skipif(os.name == "nt", reason="backslash is not legal in Windows filenames")
async def test_list_skips_keys_path_for_would_refuse(tmp_path):
    write(tmp_path, "good.mp3")
    write(tmp_path, "bad\\name.mp3")
    keys = [i.key for i in await collect(backend(tmp_path).list())]
    assert keys == ["good.mp3"]


async def test_list_skips_a_name_containing_a_newline(tmp_path):
    write(tmp_path, "good.mp3")
    try:
        write(tmp_path, "bad\nname.mp3")
    except OSError:
        pytest.skip("this filesystem refuses newlines in names")
    keys = [i.key for i in await collect(backend(tmp_path).list())]
    assert keys == ["good.mp3"]


@pytest.mark.parametrize("ch", ["\n", "\r", "\t", "\x00", "\x1f", "\x7f"])
async def test_control_characters_in_a_key_are_a_storage_error(tmp_path, ch):
    with pytest.raises(StorageError):
        backend(tmp_path).path_for(f"talks/a{ch}b.mp3")


async def test_nul_in_a_key_is_a_storage_error(tmp_path):
    with pytest.raises(StorageError):
        backend(tmp_path).path_for("a\x00b.mp3")


@pytest.mark.parametrize(
    "key",
    [
        "./x",
        "x/",
        "talks//x",
        "x/.",
        "a/./b",
        "a/../b",
        "C:/x",
        "a:b",
        "x.mp3:stream",
        "dir./x",
        "dir /x",
        "x.",
        "x ",
    ],
)
async def test_non_canonical_or_windows_hostile_keys_are_rejected(tmp_path, key):
    with pytest.raises(StorageError):
        backend(tmp_path).path_for(key)


@pytest.mark.parametrize(
    "key", ["talks/one.mp3", "a+b (1).mp3", "2024/x/y.wav", "José.mp3"]
)
async def test_ordinary_keys_stay_valid(tmp_path, key):
    assert backend(tmp_path).path_for(key).is_relative_to(tmp_path.resolve())


async def test_deleting_a_directory_is_a_storage_error(tmp_path):
    (tmp_path / "talks").mkdir()
    with pytest.raises(StorageError):
        await backend(tmp_path).delete("talks")
    assert (tmp_path / "talks").is_dir()


async def test_read_text_of_invalid_utf8_names_the_key(tmp_path):
    write(tmp_path, "bad.txt", b"\xff\xfe\x00bad")
    with pytest.raises(StorageError, match="bad.txt"):
        await backend(tmp_path).read_text("bad.txt")


async def test_a_missing_or_non_directory_root_is_a_storage_error(tmp_path):
    missing = backend(tmp_path / "nope")
    with pytest.raises(StorageError, match="is not available"):
        await collect(missing.list())
    with pytest.raises(StorageError, match="is not available"):
        await missing.read_text("a.txt")
    with pytest.raises(StorageError, match="is not available"):
        await missing.stat("a.txt")
    afile = write(tmp_path, "file.txt")
    with pytest.raises(StorageError, match="is not available"):
        await collect(backend(afile).list())


def fail_for(monkeypatch, name, method):
    """Make Path.<method> raise an I/O error (not 'not found') for files called `name`."""
    real = getattr(Path, method)

    def failing(self, *args, **kwargs):
        if self.name == name:
            raise OSError(5, "I/O error", str(self))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, method, failing)


@pytest.mark.parametrize(
    ("method", "call"),
    [
        ("stat", lambda store: store.stat("a.mp3")),
        ("open", lambda store: store.read_text("a.mp3")),
        ("unlink", lambda store: store.delete("a.mp3")),
    ],
)
async def test_io_errors_become_storage_unavailable_not_absent(tmp_path, monkeypatch, method, call):
    write(tmp_path, "a.mp3", b"data")
    store = backend(tmp_path)
    fail_for(monkeypatch, "a.mp3", method)
    with pytest.raises(StorageUnavailable):
        await call(store)


async def test_an_unreadable_root_is_unavailable(tmp_path, monkeypatch):
    real_scandir = os.scandir
    root = tmp_path.resolve()

    def denied(path="."):
        if Path(path) == root:
            raise PermissionError(13, "Access is denied", str(path))
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", denied)
    with pytest.raises(StorageUnavailable):
        await backend(tmp_path).stat("a.mp3")
    with pytest.raises(StorageUnavailable):
        await collect(backend(tmp_path).list())


async def test_a_directory_that_cannot_be_listed_fails_the_listing(tmp_path, monkeypatch):
    write(tmp_path, "talks/one.mp3")
    real_walk = os.walk

    def walk(top, topdown=True, onerror=None, followlinks=False):
        yield from real_walk(top, topdown=topdown, onerror=onerror, followlinks=followlinks)
        if onerror is not None:
            onerror(PermissionError(13, "Access is denied", str(tmp_path / "talks")))

    monkeypatch.setattr(os, "walk", walk)
    with pytest.raises(StorageUnavailable):
        await collect(backend(tmp_path).list())


async def test_registry_refuses_a_local_location_without_a_root(factory):
    location = await factory.location(config={})
    with pytest.raises(StorageError, match="root"):
        backend_for(location, signer=SIGNER, public_url="https://l")


async def test_list_walks_only_the_folder_of_the_prefix(tmp_path, monkeypatch):
    write(tmp_path, "incoming/one.mp3")
    write(tmp_path, "elsewhere/two.mp3")
    real_walk = os.walk
    walked = []

    def walk(top, *args, **kwargs):
        walked.append(Path(top))
        yield from real_walk(top, *args, **kwargs)

    monkeypatch.setattr(os, "walk", walk)
    keys = [i.key for i in await collect(backend(tmp_path).list("incoming/"))]
    assert keys == ["incoming/one.mp3"]
    assert walked == [(tmp_path / "incoming").resolve()]


async def test_a_prefix_inside_a_folder_walks_that_folder(tmp_path):
    write(tmp_path, "incoming/2024-one.mp3")
    write(tmp_path, "incoming/2023-two.mp3")
    keys = [i.key for i in await collect(backend(tmp_path).list("incoming/2024-"))]
    assert keys == ["incoming/2024-one.mp3"]


async def test_a_missing_input_folder_fails_the_listing(tmp_path):
    write(tmp_path, "elsewhere/one.mp3")
    with pytest.raises(StorageUnavailable, match="input folder 'incoming' is not available"):
        await collect(backend(tmp_path).list("incoming/"))


async def test_a_replacement_with_the_same_size_and_mtime_has_another_version(tmp_path):
    path = write(tmp_path, "talks/one.txt", b"aaaa")
    before = os.stat(path)
    first = (await backend(tmp_path).stat("talks/one.txt")).version
    replacement = tmp_path / "talks" / "replacement.tmp"
    replacement.write_bytes(b"bbbb")
    os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
    os.replace(replacement, path)
    second = (await backend(tmp_path).stat("talks/one.txt")).version
    assert os.stat(path).st_size == before.st_size
    assert os.stat(path).st_mtime_ns == before.st_mtime_ns
    assert second != first

