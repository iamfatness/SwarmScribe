import hashlib
import logging

import httpx
import pytest
from swarmscribe_follower import transfer
from swarmscribe_follower.leader import Refused, Transient
from swarmscribe_follower.transfer import (
    LeaseLost,
    LinkExpired,
    LinkRefusedByPolicy,
    Links,
    OutOfSpace,
    OutputTooLarge,
    SourceChanged,
)
from swarmscribe_protocol import Link

GET = Link(url="https://storage.test/in?sig=SECRET", method="GET", headers={"If-Match": "etag-1"})
PUT = Link(
    url="https://storage.test/out?sig=SECRET", method="PUT", headers={"x-ms-blob-type": "BlockBlob"}
)
AUDIO = bytes(range(256)) * 5000  # 1.28 MB: more than one chunk


def links_for(handler):
    seen = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return Links(transport=httpx.MockTransport(recording)), seen


def error(status, code, **headers):
    return httpx.Response(status, json={"code": code, "message": "no"}, headers=headers)


def test_a_download_is_streamed_to_disk_hashed_and_sent_the_links_headers(tmp_path):
    links, seen = links_for(lambda request: httpx.Response(200, content=AUDIO))
    checks = []
    digest = links.download(GET, tmp_path / "source", lambda: checks.append(1))
    assert digest == hashlib.sha256(AUDIO).hexdigest()
    assert (tmp_path / "source").read_bytes() == AUDIO
    assert len(checks) >= 2
    (request,) = seen
    assert request.headers["if-match"] == "etag-1"
    assert "authorization" not in request.headers


def test_a_download_can_be_stopped_between_chunks(tmp_path):
    class Stop(Exception):
        pass

    def stop():
        raise Stop()

    links, _ = links_for(lambda request: httpx.Response(200, content=AUDIO))
    with pytest.raises(Stop):
        links.download(GET, tmp_path / "source", stop)


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (error(412, "source_changed"), SourceChanged),
        (httpx.Response(412, text="<Error><Code>ConditionNotMet</Code></Error>"), SourceChanged),
        (error(404, "not_found"), SourceChanged),
        (error(403, "forbidden"), LinkExpired),
        (error(409, "stale_lease"), LeaseLost),
        (error(503, "unavailable", **{"Retry-After": "30"}), Transient),
        (error(400, "invalid_key"), Refused),
    ],
)
def test_a_refused_download_says_what_it_means(tmp_path, response, expected):
    links, _ = links_for(lambda request: response)
    with pytest.raises(expected) as raised:
        links.download(GET, tmp_path / "source", lambda: None)
    assert "SECRET" not in str(raised.value)
    if expected is Transient:
        assert raised.value.retry_after == 30.0


def test_a_download_that_ends_early_or_breaks_is_transient(tmp_path):
    short = httpx.Response(200, content=AUDIO[:1000], headers={"Content-Length": "999999"})
    links, _ = links_for(lambda request: short)
    with pytest.raises(Transient):
        links.download(GET, tmp_path / "source", lambda: None)

    def broken(request):
        raise httpx.ReadError(f"connection lost reading {request.url}")

    links, _ = links_for(broken)
    with pytest.raises(Transient) as raised:
        links.download(GET, tmp_path / "source", lambda: None)
    assert "SECRET" not in str(raised.value)


def test_a_recording_that_does_not_fit_on_scratch_is_not_started(tmp_path, monkeypatch):
    class Usage:
        free = 10 * 1024 * 1024

    monkeypatch.setattr(transfer.shutil, "disk_usage", lambda path: Usage)
    big = httpx.Response(200, content=b"x", headers={"Content-Length": str(11 * 1024 * 1024)})
    links, _ = links_for(lambda request: big)
    with pytest.raises(OutOfSpace):
        links.download(GET, tmp_path / "source", lambda: None)
    assert not (tmp_path / "source").exists()


@pytest.mark.parametrize("status", [200, 201, 204])
def test_an_upload_sends_the_file_with_its_length_and_the_links_headers(tmp_path, status):
    path = tmp_path / "source.txt"
    path.write_bytes(b"Welcome.\n")
    links, seen = links_for(lambda request: httpx.Response(status))
    links.upload(PUT, path)
    (request,) = seen
    assert (request.method, request.content) == ("PUT", b"Welcome.\n")
    assert request.headers["content-length"] == "9"
    assert request.headers["x-ms-blob-type"] == "BlockBlob"
    assert "authorization" not in request.headers


def test_an_empty_output_is_uploaded_as_an_empty_body(tmp_path):
    path = tmp_path / "source.txt"
    path.write_bytes(b"")
    links, seen = links_for(lambda request: httpx.Response(201))
    links.upload(PUT, path)
    assert (seen[0].content, seen[0].headers["content-length"]) == (b"", "0")


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (error(409, "stale_lease"), LeaseLost),
        (error(409, "conflict", **{"Retry-After": "5"}), Transient),
        (error(403, "forbidden"), LinkExpired),
        (httpx.Response(403, text="<Error><Code>AuthenticationFailed</Code></Error>"), LinkExpired),
        (error(413, "too_large"), OutputTooLarge),
        (error(503, "unavailable"), Transient),
        (error(400, "invalid_key"), Refused),
    ],
)
def test_a_refused_upload_says_what_it_means(tmp_path, response, expected):
    path = tmp_path / "source.txt"
    path.write_bytes(b"x")
    links, _ = links_for(lambda request: response)
    with pytest.raises(expected) as raised:
        links.upload(PUT, path)
    assert "SECRET" not in str(raised.value)


def test_a_link_for_the_wrong_method_is_never_used(tmp_path):
    path = tmp_path / "source.txt"
    path.write_bytes(b"x")
    links, seen = links_for(lambda request: httpx.Response(200))
    with pytest.raises(Refused):
        links.download(PUT, tmp_path / "source", lambda: None)
    with pytest.raises(Refused):
        links.upload(GET, path)
    assert seen == []


def test_a_redirect_is_not_followed(tmp_path):
    links, seen = links_for(
        lambda request: httpx.Response(302, headers={"Location": "https://elsewhere.test/"})
    )
    with pytest.raises(Refused):
        links.download(GET, tmp_path / "source", lambda: None)
    assert len(seen) == 1


# --- partial files, disk full, redirects, logs -------------------------------------------


def test_a_failed_or_stopped_download_leaves_no_partial_file(tmp_path):
    class Stop(Exception):
        pass

    calls = []

    def stop_second():
        calls.append(1)
        if len(calls) == 2:
            raise Stop()

    links, _ = links_for(lambda request: httpx.Response(200, content=AUDIO))
    with pytest.raises(Stop):
        links.download(GET, tmp_path / "source", stop_second)
    assert not (tmp_path / "source").exists()
    short = httpx.Response(200, content=AUDIO[:1000], headers={"Content-Length": "999999"})
    links, _ = links_for(lambda request: short)
    with pytest.raises(Transient):
        links.download(GET, tmp_path / "source", lambda: None)
    assert not (tmp_path / "source").exists()


def test_a_download_after_a_broken_one_starts_again_from_the_first_byte(tmp_path):
    answers = [
        httpx.Response(200, content=AUDIO[:1000], headers={"Content-Length": "999999"}),
        httpx.Response(200, content=AUDIO),
    ]
    links, seen = links_for(lambda request: answers.pop(0))
    with pytest.raises(Transient):
        links.download(GET, tmp_path / "source", lambda: None)
    digest = links.download(GET, tmp_path / "source", lambda: None)
    assert digest == hashlib.sha256(AUDIO).hexdigest()
    assert (tmp_path / "source").read_bytes() == AUDIO
    assert all("range" not in request.headers for request in seen)


def test_the_disk_filling_during_a_download_is_a_typed_error(tmp_path, monkeypatch):
    import errno
    from pathlib import Path

    real_open = Path.open

    class Full:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.handle.close()

        def write(self, data):
            raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(Path, "open", lambda self, *a, **k: Full(real_open(self, *a, **k)))
    links, _ = links_for(lambda request: httpx.Response(200, content=AUDIO))
    with pytest.raises(OutOfSpace):
        links.download(GET, tmp_path / "source", lambda: None)
    monkeypatch.undo()
    assert not (tmp_path / "source").exists()


def test_a_download_over_the_size_cap_is_refused_before_it_starts(tmp_path, monkeypatch):
    monkeypatch.setattr(transfer, "MAX_DOWNLOAD_BYTES", 1000)
    links, _ = links_for(lambda request: httpx.Response(200, content=AUDIO))
    with pytest.raises(OutOfSpace):
        links.download(GET, tmp_path / "source", lambda: None)
    assert not (tmp_path / "source").exists()


def test_a_chunked_download_is_capped_while_it_streams(tmp_path, monkeypatch):
    monkeypatch.setattr(transfer, "MAX_DOWNLOAD_BYTES", 2 * transfer.CHUNK_BYTES // 2)

    def chunked(request):
        return httpx.Response(200, content=iter([AUDIO[:700_000], AUDIO[:700_000]]))

    links, _ = links_for(chunked)
    with pytest.raises(OutOfSpace):
        links.download(GET, tmp_path / "source", lambda: None)
    assert not (tmp_path / "source").exists()


def test_a_download_throttled_with_429_is_transient_with_its_retry_after(tmp_path):
    links, _ = links_for(lambda request: error(429, "too_many", **{"Retry-After": "42"}))
    with pytest.raises(Transient) as raised:
        links.download(GET, tmp_path / "source", lambda: None)
    assert raised.value.retry_after == 42.0


def test_a_link_that_is_not_http_is_refused_without_a_request(tmp_path):
    path = tmp_path / "out.txt"
    path.write_bytes(b"x")
    bad_get = Link(url="file:///etc/passwd", method="GET")
    bad_put = Link(url="ftp://storage.test/x", method="PUT")
    links, seen = links_for(lambda request: httpx.Response(200))
    with pytest.raises(LinkRefusedByPolicy) as refused:
        links.download(bad_get, tmp_path / "source", lambda: None)
    assert "passwd" not in str(refused.value)
    with pytest.raises(LinkRefusedByPolicy):
        links.upload(bad_put, path)
    assert seen == []


def test_an_upload_redirect_is_not_followed_and_a_failed_upload_is_repeated_whole(tmp_path):
    path = tmp_path / "source.txt"
    path.write_bytes(b"Welcome.\n")
    answers = [httpx.Response(503), httpx.Response(201)]
    links, seen = links_for(lambda request: answers.pop(0))
    with pytest.raises(Transient):
        links.upload(PUT, path)
    links.upload(PUT, path)
    assert [request.content for request in seen] == [b"Welcome.\n", b"Welcome.\n"]
    links, seen = links_for(
        lambda request: httpx.Response(307, headers={"Location": "https://elsewhere.test/"})
    )
    with pytest.raises(Refused):
        links.upload(PUT, path)
    assert len(seen) == 1


def test_a_big_output_is_refused_without_reading_it_or_sending_it(tmp_path, monkeypatch):
    monkeypatch.setattr(transfer, "MAX_UPLOAD_BYTES", 4)
    path = tmp_path / "source.txt"
    path.write_bytes(b"too long")
    links, seen = links_for(lambda request: httpx.Response(201))
    with pytest.raises(OutputTooLarge):
        links.upload(PUT, path)
    assert seen == []


def test_nothing_about_a_link_reaches_the_log(tmp_path, caplog):
    for name in ("httpx", "httpcore"):  # what the command line does at start (restored after)
        logging.getLogger(name).setLevel(logging.WARNING)
    caplog.set_level("DEBUG")
    path = tmp_path / "source.txt"
    path.write_bytes(b"x")
    links, _ = links_for(lambda request: httpx.Response(201))
    links.upload(PUT, path)
    links, _ = links_for(lambda request: httpx.Response(200, content=b"abc"))
    links.download(GET, tmp_path / "source", lambda: None)
    assert "SECRET" not in caplog.text and "storage.test" not in caplog.text
    assert "SECRET" not in repr(PUT) and "SECRET" not in str(PUT)


def test_a_timeout_is_set_and_a_stalled_link_is_transient(tmp_path):
    links, _ = links_for(lambda request: httpx.Response(200))
    assert links._http.timeout == httpx.Timeout(120.0)

    def stalled(request):
        raise httpx.ReadTimeout(f"timed out on {request.url}")

    links, _ = links_for(stalled)
    with pytest.raises(Transient) as raised:
        links.download(GET, tmp_path / "source", lambda: None)
    assert "SECRET" not in str(raised.value)
