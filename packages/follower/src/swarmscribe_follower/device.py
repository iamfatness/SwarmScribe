"""What this machine is, as the leader is told at registration (follower spec 5.9)."""

import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from swarmscribe_engine import ENGINE_VERSION, DeviceChoice, DevicePreference, resolve_device
from swarmscribe_protocol import Capabilities

SMI = (
    "nvidia-smi",
    "--query-gpu=name,memory.total",
    "--format=csv,noheader,nounits",
)
SHORT_NAMES = "Systran/faster-whisper-"  # the repositories faster-whisper's own names map to
MAX_MODELS = 50
MAX_TEXT = 200


@dataclass(frozen=True)
class Probe:
    choice: DeviceChoice
    gpu_name: str | None = None
    gpu_memory_mb: int | None = None


def nvidia_smi() -> tuple[str | None, int | None]:
    """The first GPU's name and memory in MiB, or (None, None) when nvidia-smi is missing,
    slow or says something unexpected. The protocol allows both to be absent."""
    try:
        done = subprocess.run(SMI, capture_output=True, text=True, timeout=5, check=True)
        name, _, memory = done.stdout.splitlines()[0].rpartition(",")
        return name.strip()[:MAX_TEXT] or None, int(memory.strip())
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None, None


def probe(
    preference: DevicePreference,
    *,
    resolve: Callable[[DevicePreference], DeviceChoice] = resolve_device,
    smi: Callable[[], tuple[str | None, int | None]] = nvidia_smi,
) -> Probe:
    """Raises the engine's DeviceUnavailableError when `cuda` was asked for and is absent."""
    choice = resolve(preference)
    if choice.device != "cuda":
        return Probe(choice)
    name, memory = smi()
    return Probe(choice, name, memory)


def cache_dir(model_dir: Path | None) -> Path:
    if model_dir is not None:
        return model_dir
    configured = os.environ.get("HF_HUB_CACHE")
    return Path(configured) if configured else Path.home() / ".cache" / "huggingface" / "hub"


def cached_models(model_dir: Path | None) -> list[str]:
    """The models in the Hugging Face cache folder, by the name a profile would use."""
    try:
        folders = sorted(entry.name for entry in cache_dir(model_dir).iterdir() if entry.is_dir())
    except OSError:
        return []
    names = []
    for folder in folders:
        if folder.startswith("models--"):
            name = folder.removeprefix("models--").replace("--", "/")
            names.append(name.removeprefix(SHORT_NAMES)[:MAX_TEXT])
    return names[:MAX_MODELS]


def capabilities(found: Probe, pool: str, models: list[str]) -> Capabilities:
    return Capabilities(
        device=found.choice.device,
        gpu_name=found.gpu_name,
        gpu_memory_mb=found.gpu_memory_mb,
        models=sorted(set(models))[:MAX_MODELS],
        engine_version=ENGINE_VERSION,
        pool=pool,
    )
