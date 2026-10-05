"""What the follower chart must agree on with the follower and its image, checked without
Helm (the rest is deploy/helm/swarmscribe-follower/ci/check_render.py, which needs Helm)."""

import re
from pathlib import Path

import pytest
from swarmscribe_follower.config import Settings
from swarmscribe_follower.memory import job_mb

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[3]
CHART = ROOT / "deploy" / "helm" / "swarmscribe-follower"
DEPLOYMENT = (CHART / "templates" / "deployment.yaml").read_text(encoding="utf-8")
HELPERS = (CHART / "templates" / "_helpers.tpl").read_text(encoding="utf-8")
DOCKERFILE = (ROOT / "docker" / "follower.Dockerfile").read_text(encoding="utf-8")
UNPREFIXED = {
    "SWARMSCRIBE_LEADER_URL",
    "SWARMSCRIBE_JOIN_TOKEN_FILE",
    "SWARMSCRIBE_LEADER_CA_FILE",
}
OWN_SETTING = re.compile(r"- name: SWARMSCRIBE_FOLLOWER_([A-Z_]+)$", re.MULTILINE)
# Measured, in MiB (README, "Follower images" and "Sizing a pool").
DISTIL_LOADED, KEPT_AFTER_A_LONG_JOB = 1770, 300


def field_of(variable: str) -> str:
    if variable in UNPREFIXED:
        return variable.removeprefix("SWARMSCRIBE_").lower()
    return variable.removeprefix("SWARMSCRIBE_FOLLOWER_").lower()


def mebibytes(quantity: str) -> int:
    number, unit = re.fullmatch(r"(\d+)(Mi|Gi)", quantity).groups()
    return int(number) * (1024 if unit == "Gi" else 1)


def test_every_setting_the_chart_sets_is_a_setting_of_the_follower():
    # A renamed setting would otherwise be passed on and silently ignored (extra="ignore").
    set_by_chart = set(re.findall(r"- name: (SWARMSCRIBE_[A-Z_]+)$", DEPLOYMENT, re.MULTILINE))
    assert len(set_by_chart) >= 10
    for variable in sorted(set_by_chart):
        assert field_of(variable) in Settings.model_fields, variable


def test_every_setting_the_chart_keeps_for_itself_is_a_setting_of_the_follower():
    (owned,) = re.findall(r"\$owned := list ((?:\"[A-Z_]+\" ?)+)", HELPERS)
    names = re.findall(r'"([A-Z_]+)"', owned)
    assert len(names) == 10
    for name in names:
        assert name.lower() in Settings.model_fields, name
    for name in OWN_SETTING.findall(DEPLOYMENT):
        assert name in names, f"the chart sets {name} and lets `settings` set it again"


def test_the_state_folder_is_a_folder_inside_the_images_state_mount():
    (state,) = re.findall(r"SWARMSCRIBE_FOLLOWER_STATE_DIR=(\S+) \\", DOCKERFILE)
    assert f"mountPath: {state}\n" in DEPLOYMENT
    assert f"value: {state}/state\n" in DEPLOYMENT
    assert "mountPath: /scratch\n" in DEPLOYMENT and "mountPath: /models\n" in DEPLOYMENT


def test_the_chart_listens_on_the_images_port_by_default():
    (port,) = re.findall(r"SWARMSCRIBE_FOLLOWER_HEALTH_ADDR=127\.0\.0\.1:(\d+)", DOCKERFILE)
    values = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))
    assert values["healthPort"] == int(port)


def test_a_pod_address_in_brackets_is_an_address_the_follower_listens_on():
    for host in ("10.244.0.7", "fd00:10:244::7"):
        settings = Settings(leader_url="https://l.example.org", health_addr=f"[{host}]:9108")
        assert settings.health_address == (host, 9108)


def test_the_default_memory_serves_an_hour_with_the_cpu_default_model():
    values = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))
    limit = mebibytes(values["resources"]["limits"]["memory"])
    assert values["resources"]["requests"]["memory"] == values["resources"]["limits"]["memory"]
    # Also the second hour-long recording: the process keeps some of the first one's memory.
    assert DISTIL_LOADED + KEPT_AFTER_A_LONG_JOB + job_mb(3600, split=True) <= limit
    assert DISTIL_LOADED + job_mb(2 * 3600, split=False) > limit  # and it says no to two
