import pytest
from swarmscribe_engine import DeviceChoice, DeviceUnavailableError
from swarmscribe_follower import device
from swarmscribe_follower.device import Probe, cached_models, capabilities, probe
from swarmscribe_follower.models import ModelHost, ModelUnavailable, OutOfMemory

CUDA = DeviceChoice(device="cuda", model="large-v3", compute_type="float16")
CPU = DeviceChoice(device="cpu", model="distil-large-v3", compute_type="int8")


class Loaded:
    def __init__(self, log, settings, warm_up_error=None):
        self.log, self.settings, self.warm_up_error = log, settings, warm_up_error
        log.append(("load", settings.model, settings.compute_type, settings.device))

    def warm_up(self):
        self.log.append(("warm_up", self.settings.model))
        if self.warm_up_error is not None:
            raise self.warm_up_error

    def close(self):
        self.log.append(("close", self.settings.model))


def host(log, **kwargs):
    return ModelHost("cpu", factory=lambda settings: Loaded(log, settings), **kwargs)


def test_a_model_is_loaded_warmed_up_once_and_reused():
    log = []
    models = host(log)
    first = models.get("distil-large-v3", "int8")
    assert models.get("distil-large-v3", "int8") is first
    assert log == [("load", "distil-large-v3", "int8", "cpu"), ("warm_up", "distil-large-v3")]
    assert models.loaded == ("distil-large-v3", "int8")


def test_another_model_or_compute_type_closes_the_old_one_first():
    log = []
    models = host(log)
    models.get("distil-large-v3", "int8")
    models.get("tiny.en", "int8")
    models.get("tiny.en", "float32")
    assert [entry[0] for entry in log] == [
        "load", "warm_up", "close", "load", "warm_up", "close", "load", "warm_up",
    ]
    assert models.loaded == ("tiny.en", "float32")
    models.close()
    models.close()
    assert (models.loaded, log[-1]) == (None, ("close", "tiny.en"))


@pytest.mark.parametrize(
    "name",
    ["/models/large-v3", "../large-v3", "C:\\models\\x", "a/b/c", "", ".hidden", "x" * 65, "a b"],
)
def test_a_model_name_that_could_be_a_path_is_never_loaded(name):
    log = []
    with pytest.raises(ModelUnavailable):
        host(log).get(name, "int8")
    assert log == []


def test_plain_names_and_repositories_are_accepted():
    log = []
    models = host(log)
    for name in ("large-v3", "distil-large-v3", "tiny.en", "owner/custom_model-2"):
        models.get(name, "int8")
    assert [entry[1] for entry in log if entry[0] == "load"] == [
        "large-v3", "distil-large-v3", "tiny.en", "owner/custom_model-2",
    ]


def test_an_allow_list_restricts_what_is_loaded():
    log = []
    models = host(log, allowed=frozenset({"tiny.en"}))
    models.get("tiny.en", "int8")
    with pytest.raises(ModelUnavailable, match="allowed models"):
        models.get("large-v3", "int8")
    assert models.loaded == ("tiny.en", "int8")  # the refused name unloaded nothing


def test_a_model_that_cannot_be_loaded_is_this_machines_fault_and_says_why():
    def missing(settings):
        raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")

    with pytest.raises(ModelUnavailable, match="cublas64_12") as error:
        ModelHost("cuda", factory=missing).get("large-v3", "float16")
    assert "large-v3 (float16, cuda)" in str(error.value)


def test_a_library_that_fails_only_at_the_first_inference_is_caught_by_the_warm_up():
    log = []
    broken = RuntimeError("Library libcudnn_ops.so.9 is not found")
    models = ModelHost("cuda", factory=lambda s: Loaded(log, s, warm_up_error=broken))
    with pytest.raises(ModelUnavailable, match="libcudnn_ops"):
        models.get("large-v3", "float16")
    assert models.loaded is None


@pytest.mark.parametrize(
    "error", [MemoryError(), RuntimeError("CUDA failed with error out of memory")]
)
def test_running_out_of_memory_while_loading_is_not_called_unavailable(error):
    def too_big(settings):
        raise error

    with pytest.raises(OutOfMemory):
        ModelHost("cuda", factory=too_big).get("large-v3", "float16")


# --- device ------------------------------------------------------------------------------


def test_a_gpu_is_reported_with_its_name_and_memory():
    found = probe("auto", resolve=lambda preference: CUDA, smi=lambda: ("RTX 4090", 24564))
    assert found == Probe(CUDA, "RTX 4090", 24564)
    reported = capabilities(found, "gpu", ["large-v3", "large-v3", "tiny.en"])
    assert reported.model_dump() == {
        "device": "cuda",
        "gpu_name": "RTX 4090",
        "gpu_memory_mb": 24564,
        "models": ["large-v3", "tiny.en"],
        "engine_version": reported.engine_version,
        "pool": "gpu",
    }


def test_a_cpu_never_asks_nvidia_smi():
    def never():
        raise AssertionError("nvidia-smi was asked on a CPU machine")

    assert probe("cpu", resolve=lambda preference: CPU, smi=never) == Probe(CPU)


def test_asking_for_a_gpu_that_is_not_there_is_an_error():
    def no_gpu(preference):
        raise DeviceUnavailableError("cuda was requested but no CUDA GPU is available")

    with pytest.raises(DeviceUnavailableError):
        probe("cuda", resolve=no_gpu)


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [
        ("NVIDIA GeForce RTX 4090, 24564\n", ("NVIDIA GeForce RTX 4090", 24564)),
        ("Tesla T4, 15360\nTesla T4, 15360\n", ("Tesla T4", 15360)),
        ("", (None, None)),
        ("garbage\n", (None, None)),
        ("A100, lots\n", (None, None)),
    ],
)
def test_nvidia_smi_output_is_read_or_ignored(monkeypatch, stdout, expected):
    class Done:
        pass

    def run(command, **kwargs):
        assert kwargs["timeout"] == 5
        done = Done()
        done.stdout = stdout
        return done

    monkeypatch.setattr(device.subprocess, "run", run)
    assert device.nvidia_smi() == expected


def test_a_missing_or_hanging_nvidia_smi_is_no_gpu_information(monkeypatch):
    def missing(command, **kwargs):
        raise FileNotFoundError("nvidia-smi")

    monkeypatch.setattr(device.subprocess, "run", missing)
    assert device.nvidia_smi() == (None, None)


def test_cached_models_are_named_as_a_profile_names_them(tmp_path):
    for folder in (
        "models--Systran--faster-whisper-large-v3",
        "models--Systran--faster-whisper-tiny.en",
        "models--owner--custom-model",
        "datasets--something",
        ".locks",
    ):
        (tmp_path / folder).mkdir()
    (tmp_path / "models--a-file").write_text("not a folder")
    assert cached_models(tmp_path) == ["large-v3", "tiny.en", "owner/custom-model"]
    assert cached_models(tmp_path / "missing") == []


# --- a failed load or switch leaves a known state ----------------------------------------


def test_a_failed_switch_leaves_no_model_and_the_next_get_loads_afresh():
    log = []
    bad = {"tiny.en"}

    def factory(settings):
        if settings.model in bad:
            raise RuntimeError("Library cublas64_12.dll is not found")
        return Loaded(log, settings)

    models = ModelHost("cpu", factory=factory)
    models.get("distil-large-v3", "int8")
    with pytest.raises(ModelUnavailable):
        models.get("tiny.en", "int8")
    assert models.loaded is None
    assert ("close", "distil-large-v3") in log  # the old one went first
    bad.clear()
    models.get("tiny.en", "int8")
    assert models.loaded == ("tiny.en", "int8")


def test_a_model_that_fails_its_warm_up_is_closed_not_leaked():
    log = []
    broken = RuntimeError("Library libcudnn_ops.so.9 is not found")
    models = ModelHost("cuda", factory=lambda s: Loaded(log, s, warm_up_error=broken))
    with pytest.raises(ModelUnavailable):
        models.get("large-v3", "float16")
    assert log[-1] == ("close", "large-v3")


def test_a_warm_up_that_runs_out_of_memory_is_closed_and_typed():
    log = []
    models = ModelHost(
        "cuda", factory=lambda s: Loaded(log, s, warm_up_error=MemoryError())
    )
    with pytest.raises(OutOfMemory):
        models.get("large-v3", "float16")
    assert (models.loaded, log[-1]) == (None, ("close", "large-v3"))


def test_the_name_rule_is_the_protocols_own():
    from swarmscribe_follower import models as module
    from swarmscribe_protocol import MODEL_NAME_PATTERN

    assert module.MODEL_NAME.pattern == MODEL_NAME_PATTERN
