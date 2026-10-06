"""Which Helm the three charts' render checks run, and where they write their scratch files.

Shared by deploy/helm/*/ci/check_render.py so that the rule is written once."""

import atexit
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


def find_helm() -> str:
    """The Helm a check runs, printed and checked before anything is rendered.

    $HELM names the binary (CI names the pinned one by its full path); without it, the first
    `helm` on the PATH. It must be Helm 4, and when $HELM_VERSION is set (CI: the pinned
    version) it must be exactly that one. A runner image carries a Helm of its own, so "a
    helm was found" proves nothing about which one rendered the chart."""
    on_path = shutil.which("helm")
    path = os.environ.get("HELM") or on_path
    if not path:
        raise SystemExit("FAILED: no helm: put Helm 4 on the PATH or name it in $HELM")
    try:
        done = subprocess.run([path, "version", "--short"], capture_output=True, text=True)
    except OSError as exc:
        raise SystemExit(f"FAILED: {path} cannot be run as helm: {exc}") from None
    version = done.stdout.strip()
    print(f"helm: {path} is {version or done.stderr.strip()!r} (first on the PATH: {on_path})")
    if done.returncode != 0 or not re.match(r"v4\.\d+\.\d+", version):
        raise SystemExit(f"FAILED: {path} is not Helm 4 (helm version --short: {version!r})")
    wanted = os.environ.get("HELM_VERSION", "")
    if wanted and re.split(r"[+-]", version)[0] != wanted:
        raise SystemExit(f"FAILED: {path} is Helm {version}, not the pinned {wanted}")
    return path


def scratch_folder(chart: str) -> Path:
    """One folder for a whole run (values files, chart copies), removed when the run ends,
    however it ends. Its name starts `swarmscribe-<chart>-check-`."""
    folder = tempfile.TemporaryDirectory(prefix=f"swarmscribe-{chart}-check-")
    atexit.register(folder.cleanup)
    return Path(folder.name)
