import pytest
from swarmscribe_engine import DEFAULT_CHANNEL_LABELS, TranscribeSettings


def settings(**overrides):
    return TranscribeSettings(model="large-v3", compute_type="float16", device="cuda", **overrides)


def test_channel_settings_default_to_mono_left_and_right():
    assert settings().channel_mode == "mono"
    assert settings().channel_labels == ("Left", "Right") == DEFAULT_CHANNEL_LABELS


@pytest.mark.parametrize("mode", ["mono", "stereo_split", "auto"])
def test_each_channel_mode_is_accepted(mode):
    assert settings(channel_mode=mode).channel_mode == mode


@pytest.mark.parametrize("mode", ["stereo", "stereo-split", ""])
def test_an_unknown_channel_mode_is_refused(mode):
    with pytest.raises(ValueError, match="channel_mode"):
        settings(channel_mode=mode)


@pytest.mark.parametrize(
    ("labels", "problem"),
    [
        (("Left",), "exactly two"),
        (("Left", "Middle", "Right"), "exactly two"),
        ("LR", "exactly two"),
        (("", "Right"), "1 to 40"),
        (("x" * 41, "Right"), "1 to 40"),
        ((" Left", "Right"), "space"),
        (("Left\nSide", "Right"), "control characters or line breaks"),
        (("Same", "same"), "differ"),
    ],
)
def test_bad_channel_labels_are_refused(labels, problem):
    with pytest.raises(ValueError, match=problem):
        settings(channel_mode="stereo_split", channel_labels=labels)


def test_labels_given_as_a_list_become_a_hashable_tuple():
    split = settings(channel_mode="auto", channel_labels=["Agent", "Customer"])
    assert split.channel_labels == ("Agent", "Customer")
    assert hash(split) == hash(settings(channel_mode="auto", channel_labels=("Agent", "Customer")))


def test_forty_character_labels_are_accepted():
    labels = ("x" * 40, "José")
    assert settings(channel_mode="stereo_split", channel_labels=labels).channel_labels == labels
