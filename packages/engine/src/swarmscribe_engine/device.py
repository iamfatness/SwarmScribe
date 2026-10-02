from collections.abc import Callable

from .types import DeviceChoice, DevicePreference, DeviceUnavailableError

_CUDA = DeviceChoice(device="cuda", model="large-v3", compute_type="float16")
_CPU = DeviceChoice(device="cpu", model="distil-large-v3", compute_type="int8")


def _cuda_available() -> bool:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def resolve_device(
    preference: DevicePreference = "auto",
    *,
    cuda_available: Callable[[], bool] = _cuda_available,
) -> DeviceChoice:
    if preference == "cpu":
        return _CPU
    if preference == "cuda":
        if not cuda_available():
            raise DeviceUnavailableError("cuda was requested but no CUDA GPU is available")
        return _CUDA
    if preference == "auto":
        return _CUDA if cuda_available() else _CPU
    raise ValueError(f"device preference must be auto, cuda or cpu, not {preference!r}")
