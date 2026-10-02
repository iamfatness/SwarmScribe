import dataclasses

import pytest
from swarmscribe_engine import (
    DeviceChoice,
    DeviceUnavailableError,
    TranscribeSettings,
    resolve_device,
)


def test_auto_picks_large_v3_float16_when_a_gpu_is_present():
    assert resolve_device("auto", cuda_available=lambda: True) == DeviceChoice(
        device="cuda", model="large-v3", compute_type="float16"
    )


def test_auto_picks_distil_int8_without_a_gpu():
    assert resolve_device("auto", cuda_available=lambda: False) == DeviceChoice(
        device="cpu", model="distil-large-v3", compute_type="int8"
    )


def test_cpu_can_be_forced_on_a_gpu_machine():
    assert resolve_device("cpu", cuda_available=lambda: True).device == "cpu"


def test_forcing_cuda_without_a_gpu_is_a_clear_error():
    with pytest.raises(DeviceUnavailableError, match="no CUDA GPU"):
        resolve_device("cuda", cuda_available=lambda: False)


def test_unknown_preference_is_rejected():
    with pytest.raises(ValueError, match="auto, cuda or cpu"):
        resolve_device("tpu", cuda_available=lambda: False)


def test_settings_default_to_the_clamped_ladder():
    settings = TranscribeSettings(model="large-v3", compute_type="float16", device="cuda")
    assert settings.temperatures == (0.0, 0.2, 0.4)


@pytest.mark.parametrize("temperatures", [(), (0.0, 0.6), (-0.1,), (float("nan"),)])
def test_settings_reject_an_unclamped_ladder(temperatures):
    with pytest.raises(ValueError):
        TranscribeSettings(
            model="large-v3", compute_type="float16", device="cuda", temperatures=temperatures
        )


def test_settings_are_immutable_and_hashable():
    settings = TranscribeSettings(model="large-v3", compute_type="float16", device="cuda")
    with pytest.raises(dataclasses.FrozenInstanceError):
        settings.model = "tiny.en"
    assert hash(settings) == hash(
        TranscribeSettings(model="large-v3", compute_type="float16", device="cuda")
    )
