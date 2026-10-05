"""An optional HTTP listener for /healthz and /metrics (follower spec D18, section 9).

Off unless SWARMSCRIBE_FOLLOWER_HEALTH_ADDR is set: a follower opens no port by default. The
image sets it to loopback, for its own HEALTHCHECK; a chart sets the pod's address, for the
kubelet and Prometheus. It serves two paths, reads no request body, keeps no access log and
says nothing about the leader, a job or a recording.

/healthz says that the follower's threads are alive, not that the leader answers: a leader
outage must never make a supervisor kill a follower that is transcribing."""

import logging
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .errors import EXIT_CONFIGURATION, FollowerExit
from .metrics import CONTENT_TYPE

logger = logging.getLogger(__name__)

Healthy = Callable[[], tuple[bool, str]]
"""(alive, one plain line saying so or what has stopped)."""
PLAIN = "text/plain; charset=utf-8"


class _Handler(BaseHTTPRequestHandler):
    server_version = "swarmscribe-follower"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = 10  # a client that sends nothing is dropped; it cannot hold a thread

    def _answer(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _route(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            alive, text = self.server.healthy()  # type: ignore[attr-defined]
            self._answer(200 if alive else 503, (text + "\n").encode(), PLAIN)
        elif path == "/metrics":
            self._answer(200, self.server.metrics(), CONTENT_TYPE)  # type: ignore[attr-defined]
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
        self._server: ThreadingHTTPServer | None = None
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
            server = ThreadingHTTPServer((host, port), _Handler)
        except OSError as error:
            raise FollowerExit(
                EXIT_CONFIGURATION,
                f"cannot listen on {host}:{port} for /healthz and /metrics"
                f" ({error.strerror or type(error).__name__}); change or unset"
                " SWARMSCRIBE_FOLLOWER_HEALTH_ADDR",
            ) from None
        server.daemon_threads = True
        server.healthy, server.metrics = self._healthy, self._metrics  # type: ignore[attr-defined]
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
