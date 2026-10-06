import io
import logging
import os

import pytest
from swarmscribe_follower import cudalibs
from swarmscribe_follower import main as cli
from swarmscribe_follower.models import ModelHost, ModelUnavailable

INSIDE = "bin" if os.name == "nt" else "lib"
MISSING = "Library cublas64_12.dll is not found or cannot be loaded"


def wheels(tmp_path, *, files: bool = True):
    """A site-packages/nvidia as the nvidia-cublas-cu12 wheel unpacks it; the "libraries" are
    empty files, which no loader accepts."""
    root = tmp_path / "site-packages" / "nvidia"
    for package in ("cublas", "cuda_nvrtc"):
        (root / package / INSIDE).mkdir(parents=True)
    (root / "cublas" / "include").mkdir()
    if files:
        for name in cudalibs.NAMES:
            (root / "cublas" / INSIDE / name).write_bytes(b"")
    return [root]


def test_the_wheels_library_folders_are_found_and_the_files_come_in_load_order(tmp_path):
    search = wheels(tmp_path)
    root = search[0]
    assert cudalibs.folders(search) == [root / "cublas" / INSIDE, root / "cuda_nvrtc" / INSIDE]
    assert cudalibs.files(search) == [root / "cublas" / INSIDE / name for name in cudalibs.NAMES]
    assert cudalibs.NAMES[0].lower().startswith(("cublaslt", "libcublaslt"))  # needed by the other


def test_without_the_cuda_extra_nothing_is_found_and_nothing_is_loaded(tmp_path):
    assert cudalibs.folders([]) == [] and cudalibs.files([]) == [] and cudalibs.load([]) == []
    assert cudalibs.files(wheels(tmp_path, files=False)) == []


def test_a_library_that_will_not_load_is_a_warning_never_an_error(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="swarmscribe_follower.cudalibs"):
        assert cudalibs.load(wheels(tmp_path)) == []
    assert [record.getMessage() for record in caplog.records] == [
        f"the GPU library {name} could not be loaded (OSError)" for name in cudalibs.NAMES
    ]


def test_the_real_library_loads_where_the_cuda_extra_is_installed():
    if not cudalibs.files():
        pytest.skip("the cuda extra (nvidia-cublas-cu12) is not installed in this environment")
    assert cudalibs.load() == cudalibs.files()
    assert cudalibs.load() == cudalibs.files()  # once: the second call loads nothing again


def test_the_hint_says_what_to_do_about_a_missing_cublas_and_nothing_about_other_errors(tmp_path):
    assert cudalibs.hint("CUDA failed with error out of memory", []) == ""
    assert "install the follower as swarmscribe-follower[cuda]" in cudalibs.hint(MISSING, [])
    assert "check the NVIDIA driver" in cudalibs.hint(MISSING, wheels(tmp_path))


class Model:
    def __init__(self, log, error=None):
        self.log, self.error = log, error

    def warm_up(self):
        self.log.append("warm_up")
        if self.error is not None:
            raise self.error

    def close(self):
        self.log.append("close")


def test_on_a_gpu_the_libraries_are_loaded_before_the_model_and_never_on_a_cpu():
    log = []

    def factory(settings):
        log.append("load")
        return Model(log)

    def libraries():
        log.append("libraries")

    ModelHost("cuda", factory=factory, libraries=libraries).get("large-v3", "float16")
    assert log == ["libraries", "load", "warm_up"]
    log.clear()
    ModelHost("cpu", factory=factory, libraries=libraries).get("tiny.en", "int8")
    assert log == ["load", "warm_up"]


def test_a_warm_up_that_misses_cublas_says_how_to_install_it():
    host = ModelHost(
        "cuda", factory=lambda settings: Model([], RuntimeError(MISSING)),
        libraries=lambda: None, hint=lambda message: cudalibs.hint(message, []),
    )
    with pytest.raises(ModelUnavailable) as refused:
        host.get("large-v3", "float16")
    assert MISSING in str(refused.value)
    assert "install the follower as swarmscribe-follower[cuda]" in str(refused.value)


def test_cuda_paths_prints_the_folders_or_says_the_extra_is_missing(tmp_path, monkeypatch):
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(cudalibs, "roots", lambda: [])
    assert cli.main(["cuda-paths"], out=out, err=err) == 3
    assert out.getvalue() == "" and "install swarmscribe-follower[cuda]" in err.getvalue()
    search = wheels(tmp_path)
    monkeypatch.setattr(cudalibs, "roots", lambda: search)
    out, err = io.StringIO(), io.StringIO()
    assert cli.main(["cuda-paths"], out=out, err=err) == 0
    assert out.getvalue().strip().split(os.pathsep) == [
        str(search[0] / "cublas" / INSIDE), str(search[0] / "cuda_nvrtc" / INSIDE)
    ]
