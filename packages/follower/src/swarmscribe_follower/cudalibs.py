"""The GPU library CTranslate2 needs, found and loaded by the follower itself (follower spec D15).

CTranslate2 opens cuBLAS by its bare name at the first inference (`cublas64_12.dll`,
`libcublas.so.12`). The library comes from the `nvidia-cublas-cu12` wheel (the `cuda` extra),
which unpacks it into `site-packages/nvidia/cublas/bin` (Windows) or `.../lib` (Linux): a
folder no loader searches by itself.

What was measured on Windows (plan F4a, 2026-10-05, CTranslate2 4.8.2, an RTX 4090):

    how the folder was offered         uv's Python                Microsoft Store Python
    nothing                            not found                  not found
    PATH                               loads                      not found
    os.add_dll_directory               not found                  loads
    the library loaded by full path    loads                      loads

CTranslate2 calls `LoadLibraryA("cublas64_12.dll")`: the standard search, which reads PATH and
ignores `os.add_dll_directory`; a Store Python is a packaged app, whose search ignores PATH.
The one thing both honour is a library that is already in the process: a load by bare name
finds a loaded module of that name before it searches anywhere. Linux is the same (`dlopen`
of a name finds a loaded library with that SONAME), so the follower needs no
`LD_LIBRARY_PATH` either. That is what `load` does, on both: it loads the wheel's files by
their full paths before the first model is loaded."""

import ctypes
import importlib.util
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# In load order: cuBLAS itself needs the "Lt" library beside it.
NAMES = (
    ("cublasLt64_12.dll", "cublas64_12.dll")
    if os.name == "nt"
    else ("libcublasLt.so.12", "libcublas.so.12")
)
EXTRA = "swarmscribe-follower[cuda]"
_loaded: dict[Path, ctypes.CDLL] = {}  # kept: a library that is let go could be unloaded


def roots() -> list[Path]:
    """Where the `nvidia-*` wheels are unpacked: every `nvidia` folder on the import path."""
    try:
        spec = importlib.util.find_spec("nvidia")
    except (ImportError, ValueError):
        return []
    if spec is None or not spec.submodule_search_locations:
        return []
    return [Path(root) for root in spec.submodule_search_locations]


def folders(search: list[Path] | None = None) -> list[Path]:
    """The folders that hold the wheels' shared libraries (`nvidia/*/bin` on Windows,
    `nvidia/*/lib` elsewhere), sorted; empty when the `cuda` extra is not installed."""
    inside = "bin" if os.name == "nt" else "lib"
    found = []
    for root in roots() if search is None else search:
        found.extend(path for path in root.glob(f"*/{inside}") if path.is_dir())
    return sorted(found)


def files(search: list[Path] | None = None) -> list[Path]:
    """The cuBLAS files to load, in load order; empty when the wheel is not installed."""
    found = []
    for name in NAMES:
        for folder in folders(search):
            if (folder / name).is_file():
                found.append(folder / name)
                break
    return found


def load(search: list[Path] | None = None) -> list[Path]:
    """Load cuBLAS from the wheel into this process, once; returns the files that are loaded.
    Nothing is raised: without the wheel, or with a file that will not load, the model's
    warm-up says what is missing, and `hint` adds what to do about it."""
    done = []
    for path in files(search):
        if path not in _loaded:
            try:
                # RTLD_GLOBAL is ignored on Windows; a full path makes Windows look for the
                # library's own neighbours in its folder.
                _loaded[path] = ctypes.CDLL(str(path), mode=ctypes.RTLD_GLOBAL)
            except OSError as error:
                logger.warning(
                    "the GPU library %s could not be loaded (%s)", path.name, type(error).__name__
                )
                continue
        done.append(path)
    return done


def hint(message: str, search: list[Path] | None = None) -> str:
    """What to do when a model failed with `message`; empty when it is not about cuBLAS."""
    if "cublas" not in message.lower():
        return ""
    if not files(search):
        return (
            f" The GPU library cuBLAS is not installed: install the follower as {EXTRA}"
            " (`swarmscribe-follower cuda-paths` shows what is found)."
        )
    return (
        " cuBLAS is installed but did not load: check the NVIDIA driver with `nvidia-smi`"
        " (`swarmscribe-follower cuda-paths` shows the folders)."
    )
