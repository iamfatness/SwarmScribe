"""Counting stop signals. Standard library only, on purpose: `entry.run` imports this module
and installs the handlers before anything slow is imported (see entry.py)."""

import signal


class StopSignals:
    """What the signal handlers do: count. Nothing else (no lock, no logging, no `Event`):
    see agent.py's module docstring. `count` is how many signals have arrived."""

    NAMES = ("SIGINT", "SIGTERM", "SIGBREAK")  # whichever the platform has

    def __init__(self) -> None:
        self._arrived: list[None] = []

    @property
    def count(self) -> int:
        return len(self._arrived)

    def handler(self, _signum, _frame) -> None:
        self._arrived.append(None)  # one C call: a nested handler cannot interleave in it

    def install(self) -> dict[int, object]:
        """Install the handler (main thread only); returns what to give `restore`."""
        previous = {}
        for name in self.NAMES:
            number = getattr(signal, name, None)
            if number is not None:
                previous[number] = signal.signal(number, self.handler)
        return previous

    @staticmethod
    def restore(previous: dict[int, object]) -> None:
        for number, handler in previous.items():
            signal.signal(number, handler)
