"""The leader kind test's own parts (e2e/leader-kind), checked without a cluster: that its
values, its manifests and its driver agree with each other and with the two charts."""

import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[3]
KIND = ROOT / "e2e" / "leader-kind"
LEADER_CHART = ROOT / "deploy" / "helm" / "swarmscribe-leader"
FOLLOWER_CHART = ROOT / "deploy" / "helm" / "swarmscribe-follower"
# Measured, in MiB (README, "Sizing a pool"): tiny.en once loaded, and what a follower keeps.
TINY_LOADED, KEPT_AFTER_A_LONG_JOB = 230, 300


def read(name: str):
    return list(yaml.safe_load_all((KIND / name).read_text(encoding="utf-8")))


def mebibytes(quantity: str) -> int:
    number, unit = re.fullmatch(r"(\d+)(Mi|Gi)", quantity).groups()
    return int(number) * (1024 if unit == "Gi" else 1)


@pytest.fixture(scope="module")
def driver():
    spec = importlib.util.spec_from_file_location("leader_kind_driver", KIND / "run_e2e.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["leader_kind_driver"] = module
    spec.loader.exec_module(module)
    return module


def test_the_followers_reach_the_leader_at_the_address_it_builds_its_links_from(driver):
    (leader,) = read("leader-values.yaml")
    (follower,) = read("follower-values.yaml")
    assert leader["publicUrl"] == driver.LEADER_URL == f"http://{driver.LEADER_NAME}"
    assert follower["leader"] == {"url": leader["publicUrl"], "allowHttp": True}
    assert leader["allowHttpPublicUrl"] is True and leader["ingress"] == {"enabled": False}
    # A NetworkPolicy is matched after Service translation: the followers must be allowed
    # the port the leader's PODS listen on, which is the leader chart's default.
    defaults = yaml.safe_load((LEADER_CHART / "values.yaml").read_text(encoding="utf-8"))
    assert defaults["port"] in follower["networkPolicy"]["egress"]["https"]["ports"]
    assert follower["poolToken"]["existingSecret"] == "pool-token"  # store_pool_token's Secret


def test_the_manifest_holds_what_the_values_name(driver):
    documents = {(d["kind"], d["metadata"]["name"]): d for d in read("postgres.yaml")}
    (leader,) = read("leader-values.yaml")
    secret = documents["Secret", leader["secrets"]["existingSecret"]]
    assert set(secret["stringData"]) == {"database-url", "link-key"}  # the chart's default keys
    assert secret["stringData"]["link-key"] == driver.LINK_KEY
    assert len(driver.LINK_KEY) >= 32
    (volume,) = leader["storage"]["volumes"]
    assert ("PersistentVolumeClaim", volume["existingClaim"]) in documents
    assert volume["mountPath"] == "/data"  # admin.py: DATA
    postgres = documents["Deployment", "postgres"]
    labels = postgres["spec"]["template"]["metadata"]["labels"]
    (peer,) = leader["networkPolicy"]["egress"]["postgres"]["peers"]
    assert peer == {"podSelector": {"matchLabels": labels}}
    assert postgres["spec"]["template"]["spec"]["containers"][0]["image"] == driver.POSTGRES_IMAGE
    assert "@postgres:5432/" in secret["stringData"]["database-url"]
    assert ("Service", "postgres") in documents


def test_the_leader_lets_in_the_followers_and_the_prober_and_nobody_else(driver):
    (leader,) = read("leader-values.yaml")
    peers = leader["networkPolicy"]["ingress"]["from"]
    assert peers == [
        {"podSelector": {"matchLabels": {"app.kubernetes.io/name": "swarmscribe-follower"}}},
        {"podSelector": {"matchLabels": {"swarmscribe-e2e/role": "prober"}}},
    ]
    prober = driver.pod_manifest("prober", "prober", ["pass"])
    stranger = driver.pod_manifest("stranger", "stranger", ["pass"])
    assert prober["metadata"]["labels"] == {"swarmscribe-e2e/role": "prober"}
    assert stranger["metadata"]["labels"] == {"swarmscribe-e2e/role": "stranger"}
    assert prober["spec"]["containers"][0]["command"][:3] == ["python", "-u", "-c"]


def test_the_recording_in_hand_fits_the_followers_memory(driver):
    job_mb = pytest.importorskip("swarmscribe_follower.memory").job_mb
    (follower,) = read("follower-values.yaml")
    limit = mebibytes(follower["resources"]["limits"]["memory"])
    longest = driver.LONG_REPEATS * 5.0  # the fixture is five seconds long
    assert TINY_LOADED + KEPT_AFTER_A_LONG_JOB + job_mb(longest, split=True) < limit


def test_the_hold_outlasts_three_failed_readiness_probes(driver):
    """The scenario holds the old pods on the migrated database for HOLD_SECONDS. That only
    proves something if a pod that fails /readyz would have been dropped well within it."""
    deployment = (LEADER_CHART / "templates" / "deployment.yaml").read_text(encoding="utf-8")
    probe = deployment[deployment.index("readinessProbe:") :]
    period = int(re.search(r"periodSeconds: (\d+)", probe).group(1))
    failures = int(re.search(r"failureThreshold: (\d+)", probe).group(1))
    assert driver.HOLD_SECONDS >= 2 * period * failures


def test_the_images_are_this_tests_own_and_the_values_name_them(driver):
    """`swarmscribe-leader:e2e` is the Compose tests' image (e2e/compose/Dockerfile), which
    other tests on the same machine build and expect: this test never uses that tag."""
    (leader,) = read("leader-values.yaml")
    (follower,) = read("follower-values.yaml")
    assert driver.image_parts(driver.LEADER_IMAGE) == (
        leader["image"]["repository"],
        leader["image"]["tag"],
    )
    assert driver.image_parts(driver.FOLLOWER_IMAGE) == (
        follower["image"]["repository"],
        follower["image"]["tag"],
    )
    assert driver.LEADER_IMAGE != "swarmscribe-leader:e2e" != driver.NEXT_IMAGE
    assert driver.NEXT_IMAGE != driver.LEADER_IMAGE
    assert f"ARG BASE={driver.LEADER_IMAGE}" in (KIND / "next.Dockerfile").read_text("utf-8")


def test_the_next_image_adds_the_revision_the_driver_expects(driver):
    source = (KIND / "next_migration.py").read_text(encoding="utf-8")
    assert f'REVISION = "{driver.NEXT_REVISION}"' in source
    dockerfile = (KIND / "next.Dockerfile").read_text(encoding="utf-8")
    assert "USER 10001:10001" in dockerfile.splitlines()[-1]


@pytest.mark.parametrize(
    ("image", "parts"),
    [
        ("swarmscribe-leader:kind", ("swarmscribe-leader", "kind")),
        ("registry.example.org:5000/team/l:1.2", ("registry.example.org:5000/team/l", "1.2")),
    ],
)
def test_an_image_is_split_into_its_repository_and_its_tag(driver, image, parts):
    assert driver.image_parts(image) == parts


@pytest.mark.parametrize("image", ["swarmscribe-leader", "registry.example.org:5000/l"])
def test_an_image_without_a_tag_is_refused(driver, image):
    with pytest.raises(AssertionError):
        driver.image_parts(image)


def helm4() -> str | None:
    """Helm 4, or None: $HELM, else the first on the PATH. CI's `test` job has only the
    runner image's own Helm 3: this test is skipped there, and never renders with a Helm
    nobody chose."""
    path = os.environ.get("HELM") or shutil.which("helm")
    if not path:
        return None
    done = subprocess.run([path, "version", "--short"], capture_output=True, text=True)
    return path if done.stdout.startswith("v4.") else None


# This test is SKIPPED in CI's `test` job (only the runner image's Helm 3 is there). What
# covers leader-values.yaml on a runner is the chart's own check, in the `chart` job, which
# renders that file as a must-render case and compares what it renders.
@pytest.mark.skipif(helm4() is None, reason="Helm 4 is not on the PATH or named in $HELM")
@pytest.mark.parametrize(
    ("chart", "values", "release"),
    [
        (LEADER_CHART, "leader-values.yaml", "leader"),
        (FOLLOWER_CHART, "follower-values.yaml", "pool"),
    ],
)
def test_both_charts_render_with_the_kind_values(driver, chart, values, release):
    done = subprocess.run(
        [helm4(), "template", release, str(chart), "-n", driver.NAMESPACE]
        + ["-f", str(KIND / values)],
        capture_output=True, text=True,
    )
    assert done.returncode == 0, done.stderr
    names = {d["metadata"]["name"] for d in yaml.safe_load_all(done.stdout) if d}
    assert {"leader": driver.LEADER_NAME, "pool": driver.POOL_NAME}[release] in names
