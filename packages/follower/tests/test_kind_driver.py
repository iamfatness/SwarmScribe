"""The kind test's own parts (e2e/follower-kind), checked without a cluster: that its values,
its leader and its driver agree with each other and with the memory guard's figures."""

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest
from swarmscribe_follower.memory import job_mb

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[3]
KIND = ROOT / "e2e" / "follower-kind"
CHART = ROOT / "deploy" / "helm" / "swarmscribe-follower"
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
    # What the scenario looks for in the refusal is the limit the values give, exactly.
    assert limit == driver.MEMORY_LIMIT_MIB
    assert f"{limit} MiB (SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB)" in driver.REFUSAL_LIMIT
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


def test_the_chart_turns_the_memory_limit_into_the_figure_the_driver_expects(driver):
    # The follower is told the container's limit in MiB (divisor 1Mi): 2500Mi reads as 2500.
    template = (CHART / "templates" / "deployment.yaml").read_text(encoding="utf-8")
    assert "SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB" in template
    assert "resource: limits.memory" in template and "divisor: 1Mi" in template
    values = yaml.safe_load((KIND / "values.yaml").read_text(encoding="utf-8"))
    quantity = values["resources"]["limits"]["memory"]
    assert quantity.endswith("Mi") and int(quantity[:-2]) == driver.MEMORY_LIMIT_MIB


def unknown_keys(values: dict, schema: dict, path: str = "") -> list[str]:
    """Keys of `values` that the schema's `properties` do not name, at every depth."""
    found = []
    properties = schema.get("properties")
    for key, value in values.items():
        if properties is None:
            continue  # a free-form map (settings, nodeSelector, ...)
        if key not in properties:
            found.append(f"{path}{key}")
        elif isinstance(value, dict):
            found += unknown_keys(value, properties[key], f"{path}{key}.")
    return found


def test_the_kind_values_use_only_keys_the_charts_schema_names():
    # jsonschema is not a dependency of this repository, so this is a key check, not a full
    # validation: every key, at every depth, exists in the schema's `properties` at its path.
    schema = json.loads((CHART / "values.schema.json").read_text(encoding="utf-8"))
    values = yaml.safe_load((KIND / "values.yaml").read_text(encoding="utf-8"))
    assert unknown_keys(values, schema) == []
    assert unknown_keys({"leader": {"urll": "x"}, "bogus": 1}, schema) == ["leader.urll", "bogus"]
    defaults = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))
    for key, value in values.items():  # an override keeps the default's kind of value
        assert isinstance(value, type(defaults[key])) or defaults[key] in ("", None), key


def test_the_drain_step_offers_enough_recordings_to_catch_a_drained_follower_that_claims(driver):
    # Two followers: the other is busy with one recording at a time, so a drained follower
    # that still claims is idle and polling and takes about half of them; a miss of all N
    # is below 0.5% at N = 8.
    assert len(driver.AFTER_DRAIN) >= 8
    assert len(set(driver.AFTER_DRAIN)) == len(driver.AFTER_DRAIN)


def test_a_failed_command_is_reported_whole(driver):
    with pytest.raises(AssertionError) as caught:
        driver.call(
            sys.executable, "-c", "import sys; print('boom', file=sys.stderr); sys.exit(3)",
            "tail-marker",
        )
    message = str(caught.value)
    assert "tail-marker" in message and "boom" in message
