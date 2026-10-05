"""The memory guard (follower spec 5.7, D22).

The engine holds a whole recording in memory and computes its features in one piece, so a
long recording on a small machine ends in an out-of-memory kill: no call reaches the leader,
the lease expires, and the job is tried again, and killed again, hours apart. The guard
estimates what a job needs before it starts and fails the job `out_of_resources` when that
cannot fit, which the leader records with its reason.

The figures were measured (plan F2b, 2026-10-05; faster-whisper 1.2.1, on the CPU and on a
GPU alike, because the features are computed on the CPU either way): the peak grows with the
length of the recording, about 3.5 GiB per hour of speech in one pass, and a split recording
holds the other channel meanwhile."""

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .models import OutOfMemory

JOB_BASE_MB = 100.0  # what a job of any length adds (measured: 50 to 80)
MONO_MB_PER_HOUR = 3600.0  # measured 3460: mono, or `auto` on anything but a stereo file
SPLIT_MB_PER_HOUR = 3900.0  # the same pass, with the other channel (about 230 MiB/h) held
MIN_LIMIT_MB = 64
_UNLIMITED = 1 << 60  # cgroup v1 says "no limit" with a number near 2**63
CGROUP_V2 = Path("/sys/fs/cgroup/memory.max")
CGROUP_V1 = Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")


def _megabytes(count: int) -> int:
    return count // (1024 * 1024)


def cgroup_limit_mb(paths: tuple[Path, ...] = (CGROUP_V2, CGROUP_V1)) -> int | None:
    """The container's memory limit, or None when there is none (or no cgroup: Windows)."""
    for path in paths:
        try:
            text = path.read_text(encoding="ascii").strip()
        except (OSError, ValueError):
            continue
        if text.isdigit() and int(text) < _UNLIMITED:
            return _megabytes(int(text))
        return None
    return None


def physical_mb() -> int | None:
    """The machine's memory, or None when the platform will not say."""
    if os.name == "nt":
        import ctypes

        class Status(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong)
                for name in ("total", "free", "page", "page_free", "virt", "virt_free", "ext")
            ]

        status = Status()
        status.length = ctypes.sizeof(Status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return _megabytes(status.total)
    try:
        return _megabytes(os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE"))
    except (ValueError, OSError, AttributeError):
        return None


def rss_mb() -> int | None:
    """What this process holds now (the loaded model is most of it), or None when the
    platform will not say (then the guard counts the job alone)."""
    try:
        with open("/proc/self/statm", encoding="ascii") as handle:
            return _megabytes(int(handle.read().split()[1]) * os.sysconf("SC_PAGE_SIZE"))
    except (OSError, ValueError, IndexError, AttributeError):
        return None


@dataclass(frozen=True)
class Limit:
    megabytes: int
    source: str  # for `doctor` and for a failure reason: where the figure comes from


def find_limit(
    configured: int | None,
    *,
    cgroup: Callable[[], int | None] = cgroup_limit_mb,
    physical: Callable[[], int | None] = physical_mb,
) -> Limit | None:
    """The memory this follower may use: the setting, else the smaller of the container's
    limit and the machine's memory; None when neither is known (then there is no guard)."""
    if configured is not None:
        return Limit(configured, "SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB")
    found = [
        Limit(value, source)
        for value, source in ((cgroup(), "the container's limit"), (physical(), "this machine"))
        if value is not None and value >= MIN_LIMIT_MB
    ]
    return min(found, key=lambda limit: limit.megabytes) if found else None


def probe_recording(path: Path) -> tuple[float, int] | None:
    """(seconds, channels of the first audio stream) from the container's header, or None
    when it cannot be read: the engine then says what is wrong with the file, not the guard."""
    try:
        import av

        with av.open(str(path)) as container:
            stream = container.streams.audio[0]
            channels = stream.codec_context.layout.nb_channels
            if container.duration is not None:
                return container.duration / av.time_base, channels
            if stream.duration is not None and stream.time_base is not None:
                return float(stream.duration * stream.time_base), channels
    except Exception:
        return None
    return None


def job_mb(seconds: float, *, split: bool) -> float:
    """What transcribing a recording of this length adds to the process, at most."""
    per_hour = SPLIT_MB_PER_HOUR if split else MONO_MB_PER_HOUR
    return JOB_BASE_MB + max(0.0, seconds) / 3600.0 * per_hour


class MemoryGuard:
    def __init__(
        self,
        limit: Limit,
        *,
        held: Callable[[], int | None] = rss_mb,
        probe: Callable[[Path], tuple[float, int] | None] = probe_recording,
    ) -> None:
        self.limit, self._held, self._probe = limit, held, probe

    def check(self, path: Path, channel_mode: str) -> None:
        """Raise OutOfMemory when the recording cannot be transcribed inside the limit. The
        reason says nothing of the recording but its length."""
        found = self._probe(path)
        if found is None:
            return
        seconds, channels = found
        split = channel_mode == "stereo_split" or (channel_mode == "auto" and channels == 2)
        needed = (self._held() or 0) + job_mb(seconds, split=split)
        if needed > self.limit.megabytes:
            raise OutOfMemory(
                f"a recording of {seconds / 60:.0f} minutes needs about {needed:.0f} MiB here"
                f" and this follower may use {self.limit.megabytes} MiB ({self.limit.source})"
            )
