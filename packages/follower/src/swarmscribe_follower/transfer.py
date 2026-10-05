"""Download a recording and upload outputs through the leader's links (follower spec 5.4).

A link is a URL, a method and headers, the same for every storage backend. The client here
has no credential and is never given one: a link is its own authority. Redirects are not
followed, and no URL is ever logged or put in an exception (a link's URL carries its token).
"""

import contextlib
import errno
import hashlib
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from swarmscribe_protocol import Link

from .leader import TRANSIENT_STATUSES, Refused, Transient, refusal_of, retry_after_of

CHUNK_BYTES = 1024 * 1024
SPARE_BYTES = 64 * 1024 * 1024  # room for the outputs beside the recording
IN_USE_RETRY_SECONDS = 5.0
MAX_DOWNLOAD_BYTES = 64 * 1024**3  # a cap on one recording, whatever the server declares
MAX_UPLOAD_BYTES = 512 * 1024 * 1024  # the leader's own limit on one output
DISK_FULL = (errno.ENOSPC, errno.EDQUOT)
SCHEMES = ("http://", "https://")


class SourceChanged(Exception):
    """The recording is no longer the file that was ingested (changed, or gone)."""


class LinkExpired(Exception):
    """The link was refused as expired or invalid; a fresh one may work."""


class LeaseLost(Exception):
    """The storage refused the upload because the lease it belongs to has ended."""


class OutputTooLarge(Exception):
    """An output is larger than the storage accepts."""


class OutOfSpace(Exception):
    """The recording does not fit on the scratch disk."""


class Links:
    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        verify: Any = True,
        timeout: float = 120.0,
    ) -> None:
        self._http = httpx.Client(
            transport=transport, verify=verify, timeout=timeout, follow_redirects=False
        )

    def close(self) -> None:
        self._http.close()

    @staticmethod
    def _expect(link: Link, method: str) -> None:
        if link.method != method:
            raise Refused(0, "invalid_link", f"expected a {method} link")
        if not link.url.lower().startswith(SCHEMES):
            raise Refused(0, "invalid_link", "a link must be an http or https URL")

    def download(self, link: Link, destination: Path, check: Callable[[], None]) -> str:
        """Stream the recording to `destination` and return its SHA-256. `check` is called
        between chunks and raises to stop the download.

        Every attempt starts from the first byte (the file is rewritten whole), and a
        failed or stopped attempt leaves no partial file behind. The claim carries no
        checksum, so the integrity checks are the declared length and the digest returned
        here, which becomes the `source` checksum at submit."""
        self._expect(link, "GET")
        try:
            return self._stream_to(link, destination, check)
        except BaseException:
            with contextlib.suppress(OSError):
                destination.unlink(missing_ok=True)
            raise

    def _stream_to(self, link: Link, destination: Path, check: Callable[[], None]) -> str:
        digest = hashlib.sha256()
        # No transfer encoding, so a declared length is the length of the file itself.
        headers = {"Accept-Encoding": "identity", **link.headers}
        try:
            with self._http.stream("GET", link.url, headers=headers) as response:
                if response.status_code != 200:
                    response.read()
                    raise self._download_error(response)
                declared = response.headers.get("content-length", "")
                expected = int(declared) if declared.isdigit() else None
                if response.headers.get("content-encoding", "identity") != "identity":
                    expected = None
                free = shutil.disk_usage(destination.parent).free
                needed = expected if expected is not None else 0
                if needed > MAX_DOWNLOAD_BYTES:
                    raise OutOfSpace(f"the recording is {needed} bytes, over the limit")
                if needed + SPARE_BYTES > free:
                    raise OutOfSpace(
                        f"the recording is {needed} bytes; scratch has {free} bytes free"
                    )
                received = 0
                with destination.open("wb") as out:
                    for chunk in response.iter_bytes(CHUNK_BYTES):
                        check()
                        received += len(chunk)
                        if received > MAX_DOWNLOAD_BYTES:
                            raise OutOfSpace("the recording is over the size limit")
                        out.write(chunk)
                        digest.update(chunk)
        except httpx.InvalidURL:
            raise Refused(0, "invalid_link", "the link cannot be used") from None
        except httpx.HTTPError as exc:
            raise Transient(None, None, type(exc).__name__) from None
        except OSError as exc:
            if exc.errno in DISK_FULL:
                raise OutOfSpace("scratch ran out of space during the download") from None
            raise
        if expected is not None and received != expected:
            raise Transient(None, None, "the download ended early")
        return digest.hexdigest()

    @staticmethod
    def _download_error(response: httpx.Response) -> Exception:
        refusal = refusal_of(response)
        if response.status_code in (404, 412) or refusal.code == "source_changed":
            return SourceChanged("the recording changed or was removed after it was ingested")
        if response.status_code == 403:
            return LinkExpired("the download link was refused")
        if response.status_code == 409 and refusal.code == "stale_lease":
            return LeaseLost("the lease this download belongs to has ended")
        if response.status_code in TRANSIENT_STATUSES:
            return Transient(response.status_code, retry_after_of(response), "download")
        return Refused(refusal.status, refusal.code, "")  # a storage's text is not repeated

    def upload(self, link: Link, path: Path) -> None:
        """Send one output file. Outputs are text and small (the leader's own storage takes
        at most 512 MiB), so the file is read whole and sent with its length."""
        self._expect(link, "PUT")
        size = path.stat().st_size
        if size > MAX_UPLOAD_BYTES:
            raise OutputTooLarge(f"an output of {size} bytes is over the limit")
        # Read whole before anything is sent: a body is never streamed from a file that is
        # still changing, so no partial file is uploaded. A failed upload is retried by the
        # caller from the start.
        content = path.read_bytes()
        try:
            response = self._http.put(link.url, headers=link.headers, content=content)
        except httpx.InvalidURL:
            raise Refused(0, "invalid_link", "the link cannot be used") from None
        except httpx.HTTPError as exc:
            raise Transient(None, None, type(exc).__name__) from None
        if 200 <= response.status_code < 300:
            return
        refusal = refusal_of(response)
        if response.status_code == 403:
            raise LinkExpired("the upload link was refused")
        if response.status_code == 409 and refusal.code == "stale_lease":
            raise LeaseLost("the lease this upload belongs to has ended")
        if response.status_code == 409:  # the file is in use: the leader says to retry
            wait = retry_after_of(response)
            raise Transient(409, IN_USE_RETRY_SECONDS if wait is None else wait, "upload")
        if response.status_code == 413:
            raise OutputTooLarge(f"an output of {size} bytes was refused as too large")
        if response.status_code in TRANSIENT_STATUSES:
            raise Transient(response.status_code, retry_after_of(response), "upload")
        raise Refused(refusal.status, refusal.code, "")  # a storage's text is not repeated
