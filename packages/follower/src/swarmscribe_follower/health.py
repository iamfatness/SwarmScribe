"""An optional HTTP listener for /healthz and /metrics (follower spec D18, section 9).

Off unless SWARMSCRIBE_FOLLOWER_HEALTH_ADDR is set: a follower opens no port by default. The
image sets it to loopback, for its own HEALTHCHECK; a chart sets the pod's address, for the
kubelet and Prometheus. It serves two paths, reads no request body, keeps no access log and
says nothing about the leader, a job or a recording.

/healthz says that the follower's threads are alive, not that the leader answers: a leader
outage must never make a supervisor kill a follower that is transcribing.

In a pod the listener is on the pod's address, where anything the network lets through can
reach it. So a connection is one request and at most REQUEST_SECONDS in all, however slowly
its bytes arrive, and at most MAX_CONNECTIONS are held at once: one more is closed unanswered.
A client can keep a place for those seconds and no longer, and never a thread for good; a
probe that is turned away is asked again by the kubelet, which gives up only after several."""

import io
import ipaddress
import logging
import os
import socket
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .errors import EXIT_CONFIGURATION, FollowerExit
from .metrics import CONTENT_TYPE

logger = logging.getLogger(__name__)

Healthy = Callable[[], tuple[bool, str]]
"""(alive, one plain line saying so or what has stopped)."""
PLAIN = "text/plain; charset=utf-8"
REQUEST_SECONDS = 5.0  # a connection, from accepted to answered; a probe takes milliseconds
MAX_CONNECTIONS = 8  # held at once: a kubelet and a few scrapers never come near it


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets a second socket share a port that is being listened on, so a
    # taken port would go unnoticed; there it is left off. Elsewhere it only skips TIME_WAIT.
    allow_reuse_address = os.name != "nt"

    def __init__(
        self,
        address: tuple[str, int],
        healthy: Healthy,
        metrics: Callable[[], bytes],
    ) -> None:
        try:
            ipv6 = isinstance(ipaddress.ip_address(address[0]), ipaddress.IPv6Address)
        except ValueError:
            ipv6 = False  # a name: IPv4, as http.server has always done
        if ipv6:
            self.address_family = socket.AF_INET6
        self.healthy, self.metrics = healthy, metrics
        self._slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        super().__init__(address, _Handler)

    def process_request(self, request, client_address) -> None:
        """One thread per connection, as ThreadingHTTPServer does, but never more than
        MAX_CONNECTIONS of them: a connection beyond that is closed without an answer."""
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()  # no thread was started: nothing else will give it back
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


class _UntilDeadline(io.RawIOBase):
    """The connection, readable until a moment that never moves: each read may wait only for
    what is left of the time, so bytes that trickle in do not buy more of it."""

    def __init__(self, connection: socket.socket, deadline: float) -> None:
        self._connection, self._deadline = connection, deadline

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        left = self._deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError("the request took too long")
        self._connection.settimeout(left)
        return self._connection.recv_into(buffer)


class _Handler(BaseHTTPRequestHandler):
    server_version = "swarmscribe-follower"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    server: "_Server"
    timeout = REQUEST_SECONDS  # until setup() has put the deadline in its place

    def setup(self) -> None:
        super().setup()
        self._deadline = time.monotonic() + REQUEST_SECONDS
        self.rfile.close()
        self.rfile = io.BufferedReader(_UntilDeadline(self.connection, self._deadline))

    def _answer(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")  # one request per connection
        self.end_headers()
        if self.command != "HEAD":
            # A client that does not read its answer is not waited for either.
            self.connection.settimeout(max(0.001, self._deadline - time.monotonic()))
            self.wfile.write(body)

    def _route(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            alive, text = self.server.healthy()
            self._answer(200 if alive else 503, (text + "\n").encode(), PLAIN)
        elif path == "/metrics":
            self._answer(200, self.server.metrics(), CONTENT_TYPE)
        else:
            self._answer(404, b"not found\n", PLAIN)

    do_GET = _route  # noqa: N815 (the names http.server looks for)
    do_HEAD = _route  # noqa: N815

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """No access log: a probe every few seconds is not news."""


class HealthServer:
    def __init__(
        self, address: tuple[str, int], *, healthy: Healthy, metrics: Callable[[], bytes]
    ) -> None:
        self._address, self._healthy, self._metrics = address, healthy, metrics
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        """The port it listens on (the one asked for, or the one the system chose for 0)."""
        if self._server is None:
            raise RuntimeError("the health listener has not been started")
        return self._server.server_address[1]

    def start(self) -> None:
        host, port = self._address
        try:
            server = _Server((host, port), self._healthy, self._metrics)
        except OSError as error:
            raise FollowerExit(
                EXIT_CONFIGURATION,
                f"cannot listen on {host}:{port} for /healthz and /metrics"
                f" ({error.strerror or type(error).__name__}); change or unset"
                " SWARMSCRIBE_FOLLOWER_HEALTH_ADDR",
            ) from None
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, name="health", daemon=True)
        self._thread.start()
        logger.info("listening on %s:%d for /healthz and /metrics", host, self.port)

    def close(self) -> None:
        server, self._server = self._server, None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
