"""The one model this follower holds in memory (follower spec 5.7).

A model is loaded, then exercised with a real inference (GPU libraries load at the first
inference, not at the load), and kept until a claim names another. Before another is loaded
the old one is closed, so two are never in memory together."""

import logging
import re
from collections.abc import Callable
from typing import Any

from swarmscribe_engine import Device, Transcriber, TranscribeSettings
from swarmscribe_protocol import MODEL_NAME_MAX_LENGTH, MODEL_NAME_PATTERN

logger = logging.getLogger(__name__)

# A model is named, never located: the name comes over the wire and the loader would accept
# a path. `owner/name` is a Hugging Face repository. The rule is the protocol's, shared with
# the leader that accepts the name (Adjustment 4): never a copy.
MODEL_NAME = re.compile(MODEL_NAME_PATTERN)

EngineFactory = Callable[[TranscribeSettings], Any]


class ModelUnavailable(Exception):
    """This machine cannot load the model: not allowed, not in the cache while offline, a
    compute type the device lacks, a GPU library missing. The machine's fault, not a job's."""


class OutOfMemory(Exception):
    """The model, or a job, does not fit in this machine's memory."""


def is_out_of_memory(error: BaseException) -> bool:
    return isinstance(error, MemoryError) or "out of memory" in str(error).lower()


class ModelHost:
    def __init__(
        self,
        device: Device,
        *,
        factory: EngineFactory = Transcriber,
        allowed: frozenset[str] = frozenset(),
    ) -> None:
        self.device = device
        self._factory = factory
        self._allowed = allowed
        self._loaded: tuple[str, str] | None = None
        self._transcriber: Any = None

    @property
    def loaded(self) -> tuple[str, str] | None:
        """(model, compute type) in memory, or None."""
        return self._loaded

    def get(self, model: str, compute_type: str) -> Any:
        """The transcriber for (model, compute type), loading it if it is not the one in
        memory."""
        if len(model) > MODEL_NAME_MAX_LENGTH or not MODEL_NAME.fullmatch(model):
            raise ModelUnavailable("the model name is not a plain name or owner/name")
        if self._allowed and model not in self._allowed:
            raise ModelUnavailable(f"the model {model} is not in this follower's allowed models")
        if self._loaded == (model, compute_type):
            return self._transcriber
        self.close()
        settings = TranscribeSettings(model=model, compute_type=compute_type, device=self.device)
        transcriber = None
        try:
            transcriber = self._factory(settings)
            transcriber.warm_up()
        except Exception as exc:
            if transcriber is not None:
                self._discard(transcriber)
            if is_out_of_memory(exc):
                raise OutOfMemory(f"loading {model}: {type(exc).__name__}") from exc
            # The text of a loader error names the missing file or library: that is the
            # operator's first clue, and it holds nothing of a recording.
            raise ModelUnavailable(
                f"{model} ({compute_type}, {self.device}): {type(exc).__name__}: {str(exc)[:500]}"
            ) from exc
        self._transcriber, self._loaded = transcriber, (model, compute_type)
        logger.info("model %s (%s) loaded on %s", model, compute_type, self.device)
        return transcriber

    @staticmethod
    def _discard(transcriber: Any) -> None:
        """Free a model that loaded but did not warm up. Nothing else holds it."""
        try:
            transcriber.close()
        except Exception:
            logger.warning("closing a model that failed its warm-up failed", exc_info=False)

    def close(self) -> None:
        transcriber, self._transcriber, self._loaded = self._transcriber, None, None
        if transcriber is not None:
            transcriber.close()
