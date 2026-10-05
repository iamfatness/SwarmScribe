"""The kind test's own parts (e2e/follower-kind), checked without a cluster: that its values,
its leader and its driver agree with each other and with the memory guard's figures."""

import importlib.util
import re
import sys
from pathlib import Path

import pytest
from swarmscribe_follower.memory import job_mb

yaml = pytest.importorskip("yaml")

KIND = Path(__file__).resolve().parents[3] / "e2e" / "follower-kind"
# Measured, in MiB (README, "Sizing a pool"): tiny.en once loaded, and what a follower keeps.
TINY_LOADED, KEPT_AFTER_A_LONG_JOB = 230, 300


def mebibytes(quantity: str) -> int:
    number, unit = re.fullmatch(r"(\d+)(Mi|Gi)", quantity).groups()
    return int(number) * (1024 if unit == "Gi" else 1)


@pytest.fixture(scope="module")
def driver():
    spec = importlib.util.spec_from_file_location("follower_kind_driver", KIND / "run_e2e.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["follower_kind_driver"] = module
    spec.loader.exec_module(module)
    return module


def test_the_kind_values_admit_the_long_recordings_and_refuse_the_hour(driver):
    values = yaml.safe_load((KIND / "values.yaml").read_text(encoding="utf-8"))
    limit = mebibytes(values["resources"]["limits"]["memory"])
    longest = driver.LONG_REPEATS * 5.0  # the fixture is five seconds long
    assert TINY_LOADED + KEPT_AFTER_A_LONG_JOB + job_mb(longest, split=True) < limit
    assert job_mb(3600, split=False) > limit
    assert f"{limit} MiB" in "2500 MiB"  # what the scenario looks for in the refusal
    assert values["terminationGracePeriodSeconds"] == driver.GRACE_SECONDS
    assert values["poolToken"]["existingSecret"] == "pool-token"  # store_pool_token's Secret


def test_the_kind_leader_is_reached_by_the_name_the_values_give(driver):
    documents = list(yaml.safe_load_all((KIND / "leader.yaml").read_text(encoding="utf-8")))
    values = yaml.safe_load((KIND / "values.yaml").read_text(encoding="utf-8"))
    services = {d["metadata"]["name"]: d for d in documents if d["kind"] == "Service"}
    port = services["leader"]["spec"]["ports"][0]
    # A NetworkPolicy is matched after Service translation: the two ports must be one.
    assert values["leader"] == {"url": f"http://leader:{port['port']}", "allowHttp": True}
    assert port["port"] == port["targetPort"]
    (leader,) = [
        d for d in documents if d["kind"] == "Deployment" and d["metadata"]["name"] == "leader"
    ]
    container = leader["spec"]["template"]["spec"]["containers"][0]
    settings = {entry["name"]: entry["value"] for entry in container["env"]}
    assert settings["SWARMSCRIBE_PUBLIC_URL"] == values["leader"]["url"]
    assert container["image"] == "swarmscribe-leader:e2e"  # `up` replaces exactly this name
    assert leader["spec"]["template"]["metadata"]["labels"] == {"app": "leader"}  # step 9's peer


@pytest.mark.parametrize(
    ("image", "parts"),
    [
        ("swarmscribe-follower:e2e", ("swarmscribe-follower", "e2e")),
        ("registry.example.org:5000/team/f:1.2", ("registry.example.org:5000/team/f", "1.2")),
    ],
)
def test_an_image_is_split_into_its_repository_and_its_tag(driver, image, parts):
    assert driver.image_parts(image) == parts


@pytest.mark.parametrize("image", ["swarmscribe-follower", "registry.example.org:5000/follower"])
def test_an_image_without_a_tag_is_refused(driver, image):
    with pytest.raises(AssertionError, match="name:tag"):
        driver.image_parts(image)
