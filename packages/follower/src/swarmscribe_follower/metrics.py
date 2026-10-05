"""What the follower counts (follower spec 9), in the Prometheus text format.

The registry is this object's own, never prometheus_client's global one: two followers in
one process (the tests) do not share counts, and nothing is counted by importing.

No label and no value ever comes from a recording, a transcript, a link or a token: the only
label values are the job outcomes and the four states."""

from collections.abc import Callable

from prometheus_client import CollectorRegistry, Counter, Gauge, generate_latest

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
STATES = ("idle", "working", "draining", "stopping")
PREFIX = "swarmscribe_follower_"


class Metrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        # prometheus_client appends `_total` to a counter's name.
        self._jobs = self._counter("jobs", "Jobs this follower ended, by outcome", ["outcome"])
        self._audio = self._counter("audio_seconds", "Seconds of audio transcribed")
        self._spent = self._counter("transcribe_seconds", "Seconds spent transcribing")
        self._failed = self._counter("heartbeat_failures", "Heartbeats the leader did not answer")
        self._down = self._counter("download_bytes", "Bytes of recordings downloaded")
        self._up = self._counter("upload_bytes", "Bytes of outputs uploaded")
        self._load = self._gauge("model_load_seconds", "Seconds the last model load took")
        self._progress = self._gauge("job_progress", "Fraction of the current job done (0 to 1)")
        self._state = self._gauge("state", "1 for the state the follower is in", ["state"])
        self.watch(state=lambda: "idle", progress=lambda: None)

    def _counter(self, name: str, text: str, labels: list[str] | None = None) -> Counter:
        return Counter(PREFIX + name, text, labels or [], registry=self.registry)

    def _gauge(self, name: str, text: str, labels: list[str] | None = None) -> Gauge:
        return Gauge(PREFIX + name, text, labels or [], registry=self.registry)

    def watch(self, *, state: Callable[[], str], progress: Callable[[], float | None]) -> None:
        """Read the state and the progress from the agent whenever the metrics are asked for,
        so they can never be stale."""
        for name in STATES:
            self._state.labels(name).set_function(lambda name=name: float(state() == name))
        self._progress.set_function(lambda: float(progress() or 0.0))

    def job_ended(self, outcome: str) -> None:
        self._jobs.labels(outcome).inc()

    def transcribed(self, audio_seconds: float, seconds: float) -> None:
        self._audio.inc(max(0.0, audio_seconds))
        self._spent.inc(max(0.0, seconds))

    def heartbeat_failed(self) -> None:
        self._failed.inc()

    def downloaded(self, size: int) -> None:
        self._down.inc(max(0, size))

    def uploaded(self, size: int) -> None:
        self._up.inc(max(0, size))

    def model_loaded(self, seconds: float) -> None:
        self._load.set(max(0.0, seconds))

    def render(self) -> bytes:
        return generate_latest(self.registry)
