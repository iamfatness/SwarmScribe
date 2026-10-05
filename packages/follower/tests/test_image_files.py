"""What the image's files must agree on with the package, checked without Docker (the
rest is docker/check-follower-image.sh, which needs an image)."""

import re
import tomllib
from pathlib import Path

from swarmscribe_follower import FOLLOWER_VERSION

ROOT = Path(__file__).resolve().parents[3]
DOCKERFILE = (ROOT / "docker" / "follower.Dockerfile").read_text(encoding="utf-8")
CHECK = (ROOT / "docker" / "check-follower-image.sh").read_text(encoding="utf-8")
LABELS = ("title", "description", "source", "version")


def test_the_version_label_is_the_package_version():
    project = tomllib.loads(
        (ROOT / "packages" / "follower" / "pyproject.toml").read_text(encoding="utf-8")
    )
    (default,) = re.findall(r"^ARG VERSION=(\S+)$", DOCKERFILE, flags=re.MULTILINE)
    assert default == project["project"]["version"] == FOLLOWER_VERSION
    assert 'org.opencontainers.image.version="${VERSION}"' in DOCKERFILE


def test_every_label_the_image_sets_is_checked_by_the_check_script():
    set_by_image = set(re.findall(r"org\.opencontainers\.image\.(\w+)=", DOCKERFILE))
    assert set_by_image == set(LABELS)
    for name in LABELS:
        assert f"org.opencontainers.image.{name}" in CHECK, f"{name} is not checked"


def test_the_environment_is_one_setting_per_line():
    """Plan F2b adds settings by anchoring on whole lines of this block."""
    block = DOCKERFILE[DOCKERFILE.index("ENV PATH=") :].split("\n# ", 1)[0].splitlines()
    for line in block:
        assert len(re.findall(r"\b[A-Z][A-Z0-9_]*=", line)) == 1, line
    assert "    SWARMSCRIBE_FOLLOWER_OFFLINE=${BAKED:-0} \\" in block


def test_the_follower_runs_under_an_init():
    """PID 1 gets no default action for SIGTERM from the kernel; until Python has installed
    its handlers (entry.py) only an init can end the container on a stop."""
    assert 'ENTRYPOINT ["/usr/bin/tini", "--", "swarmscribe-follower"]' in DOCKERFILE
    assert 'CMD ["run"]' in DOCKERFILE
