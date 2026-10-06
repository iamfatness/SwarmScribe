"""The leader chart on a local `kind` cluster: the whole system that has a chart today
(leader chart spec, section 11).

A Postgres and a volume claim of the test's own (postgres.yaml: the chart never brings
either), the chart `deploy/helm/swarmscribe-leader` with leader-values.yaml (two replicas),
and the chart `deploy/helm/swarmscribe-follower` with follower-values.yaml (two followers,
tiny.en baked in). Needs `kind`, `kubectl`, `helm` and `docker` on the PATH and four images:
swarmscribe-leader:kind (docker/leader.Dockerfile), swarmscribe-leader:kind-next
(next.Dockerfile), swarmscribe-follower:leader-kind (the cpu target with MODELS=tiny.en) and
postgres:16. The tags are this test's own: `swarmscribe-leader:e2e` is the Compose tests'
image, which is another one. CI runs it (job leader-kind-e2e); so can you:

    python e2e/leader-kind/run_e2e.py up     # the cluster, the images, Postgres, the claim
    python e2e/leader-kind/run_e2e.py run    # the scenario below
    python e2e/leader-kind/run_e2e.py down   # delete the cluster

What `run` proves, in order:

 1. the chart installs: the migration hook runs before any leader pod and is removed, both
    pods become Ready with no restart, run as uid 10001 under tini on a read-only root
    filesystem, and nothing Helm keeps or prints holds the link key;
 2. a pool token made inside the cluster reaches the follower chart through a Secret; two
    followers from that chart register with the leader through its Service;
 3. three consented recordings are transcribed, each once, through signed file links served
    by leader pods that can write nowhere but the claim; a recording the consent file does
    not name is never queued;
 4. the NetworkPolicy lets the followers in and nobody else, and lets the leader reach DNS
    and Postgres and nothing else;
 5. an upgrade with a migration, with requests flowing and a recording in hand: the hook
    migrates the database while the OLD pods serve; they stay Ready on the newer schema for
    HOLD_SECONDS with the rollout held back; then the pods are replaced one at a time; not
    one request fails and the recording is finished in one attempt;
 6. after the upgrade a new recording is transcribed, and no leader pod's log holds the
    pool token, the link key or a file link.

There is no identity provider in the cluster, so the leader has no sign-in (oidc.allowNone)
and is administered from inside its pod (admin.py), as the Compose tests do. The cluster has
its own kubeconfig (work/kubeconfig): yours is not touched. Nothing here prints a pool
token, a credential or a link."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
LEADER_CHART = ROOT / "deploy" / "helm" / "swarmscribe-leader"
FOLLOWER_CHART = ROOT / "deploy" / "helm" / "swarmscribe-follower"
FIXTURE = ROOT / "packages" / "engine" / "tests" / "fixtures" / "stereo_speech.wav"
WORK = HERE / "work"
KUBECONFIG = WORK / "kubeconfig"
CLUSTER = os.environ.get("KIND_CLUSTER", "swarmscribe-leader-e2e")
NAMESPACE = "swarmscribe-e2e"
LEADER, POOL = "leader", "pool"  # the two releases
LEADER_NAME = f"{LEADER}-swarmscribe-leader"  # the leader's Deployment and Service
POOL_NAME = f"{POOL}-swarmscribe-follower"
LEADER_URL = f"http://{LEADER_NAME}"  # leader-values.yaml: publicUrl; the Service's port is 80
LEADER_IMAGE = os.environ.get("LEADER_IMAGE", "swarmscribe-leader:kind")
NEXT_IMAGE = os.environ.get("NEXT_IMAGE", "swarmscribe-leader:kind-next")  # next.Dockerfile
FOLLOWER_IMAGE = os.environ.get("FOLLOWER_IMAGE", "swarmscribe-follower:leader-kind")
POSTGRES_IMAGE = "postgres:16"  # postgres.yaml
LEADER_PODS = f"app.kubernetes.io/instance={LEADER},app.kubernetes.io/component=leader"
NEXT_REVISION = "9999"  # next.Dockerfile's migration
LINK_KEY = "leader-kind-link-key-0123456789abcdef"  # postgres.yaml: the Secret `leader`
MODEL, COMPUTE_TYPE = "tiny.en", "int8"
LOCATION = "calls"
CONSENTED = tuple(f"ok/call-{n}.wav" for n in (1, 2, 3))
NOT_CONSENTED = "private/call-4.wav"  # consent.txt names ok/*.wav only
IN_HAND = "ok/long.wav"  # being transcribed while the leader is upgraded
AFTER = "ok/after.wav"
LEFT_WORD, RIGHT_WORD = "weather", "report"  # what the fixture says, left then right
# The 5 s fixture, 144 times: twelve minutes of audio, a minute and a half or more of work on
# two cores, so that it is still in hand when the pods are replaced (the scenario checks).
LONG_REPEATS = 144
HOLD_SECONDS = 30  # old pods on the new schema: twice what three failed probes take (15 s)
FREE_GB_FLOOR = int(os.environ.get("FREE_GB_FLOOR", "20"))  # CI's runners have less disk
STEP_SECONDS = 180.0


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


# --- the tools ---------------------------------------------------------------------------


def free_space() -> None:
    """Stop before Docker or kind fills the disk (docker/check-free-space.sh, in Python)."""
    disk = "C:\\" if os.name == "nt" else "/"
    free_gb = shutil.disk_usage(disk).free // 2**30
    expect(
        free_gb >= FREE_GB_FLOOR,
        f"{disk} has {free_gb} GB free, under the floor of {FREE_GB_FLOOR} GB: free some space"
        " before running Docker or kind",
    )


def call(*command: str, stdin: str | bytes | None = None, check: bool = True) -> str:
    binary = isinstance(stdin, bytes)
    done = subprocess.run(
        command, input=stdin, capture_output=True, text=not binary,
        **({} if binary else {"encoding": "utf-8", "errors": "replace"}),
    )
    out, err = (
        (done.stdout.decode("utf-8", "replace"), done.stderr.decode("utf-8", "replace"))
        if binary
        else (done.stdout, done.stderr)
    )
    if check and done.returncode != 0:
        # No argument is ever a secret: tokens go in on stdin.
        raise AssertionError(f"`{' '.join(command)}` failed: {(err.strip() or out.strip())[-600:]}")
    return out


def kubectl(*arguments: str, stdin: str | bytes | None = None, check: bool = True) -> str:
    return call(
        "kubectl", "--kubeconfig", str(KUBECONFIG), "-n", NAMESPACE, *arguments,
        stdin=stdin, check=check,
    )


_helm: list[str] = []


def helm_binary() -> str:
    """The Helm this test runs: $HELM if set (CI names the pinned binary), else the first
    on the PATH. Anything but Helm 4 is refused: a runner image has a Helm 3 of its own."""
    if not _helm:
        path = os.environ.get("HELM") or shutil.which("helm")
        expect(path is not None, "helm is not on the PATH and $HELM is not set")
        version = call(path, "version", "--short").strip()
        print(f"helm: {path} is {version}", flush=True)
        expect(version.startswith("v4."), f"{path} is Helm {version}; this test needs Helm 4")
        _helm.append(path)
    return _helm[0]


def helm(*arguments: str) -> str:
    return call(helm_binary(), "--kubeconfig", str(KUBECONFIG), "-n", NAMESPACE, *arguments)


def image_parts(image: str) -> tuple[str, str]:
    """(repository, tag) of `name:tag`; a registry's port is not a tag."""
    repository, colon, tag = image.rpartition(":")
    if not colon or "/" in tag:
        raise AssertionError(f"{image} must be name:tag")
    return repository, tag


def install_leader(image: str) -> None:
    """Install or upgrade the leader's release. Helm waits for the migration hook itself;
    the pods are waited for by the caller."""
    repository, tag = image_parts(image)
    try:
        helm(
            "upgrade", "--install", LEADER, str(LEADER_CHART),
            "-f", str(HERE / "leader-values.yaml"),
            "--set", f"image.repository={repository}", "--set", f"image.tag={tag}",
            "--timeout", "300s",
        )
    except Exception:
        # A failed hook: say why before failing. A pod that never started has no log.
        selector = "app.kubernetes.io/component=migrate"
        for arguments in (
            ("describe", "pod", "-l", selector),
            ("logs", f"job/{LEADER_NAME}-migrate", "--tail=80"),
        ):
            print(kubectl(*arguments, check=False), flush=True)
        raise


def until(check: Callable[[], Any], what: str, within: float = STEP_SECONDS) -> Any:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        found = check()
        if found:
            return found
        time.sleep(1.0)
    raise AssertionError(f"timed out after {within:.0f} s waiting for {what}")


# --- the leader --------------------------------------------------------------------------


def leader_pods() -> list[dict]:
    """The leader's pods that are not being deleted."""
    listed = json.loads(kubectl("get", "pods", "-l", LEADER_PODS, "-o", "json"))["items"]
    return [pod for pod in listed if "deletionTimestamp" not in pod["metadata"]]


def is_ready(pod: dict) -> bool:
    conditions = pod.get("status", {}).get("conditions", [])
    return any(c["type"] == "Ready" and c["status"] == "True" for c in conditions)


def image_of(pod: dict) -> str:
    return pod["spec"]["containers"][0]["image"]


def admin(command: str, *arguments: str) -> Any:
    """Run admin.py inside a leader pod; returns what it printed (one JSON value)."""
    script = (HERE / "admin.py").read_text(encoding="utf-8")
    said = kubectl(
        "exec", "-i", f"deploy/{LEADER_NAME}", "-c", "leader", "--",
        "python", "-", command, *arguments, stdin=script,
    )
    return json.loads(said.strip().splitlines()[-1])


def in_pod(name: str, code: str, *arguments: str, check: bool = True) -> str:
    return kubectl("exec", name, "--", "python", "-c", code, *arguments, check=check).strip()


def job(key: str) -> dict | None:
    return admin("state")["jobs"].get(f"{LOCATION}/{key}")


def completed(key: str) -> dict | None:
    found = job(key)
    return found if found is not None and found["state"] == "completed" else None


def add(key: str, repeats: int = 1) -> None:
    admin("recording", LOCATION, key, str(repeats))


def check_transcript(key: str) -> None:
    done = job(key)
    expect([o for _, o in done["tried"]] == ["completed"], f"{key}: {done['tried']}")
    text = (done["text"] or "").lower()
    expect(LEFT_WORD in text and RIGHT_WORD in text, f"{key}: the transcript is wrong")


def store_pool_token(name: str) -> str:
    """A new pool token, put into the Secret the follower chart reads. Returned only so
    that logs and Helm's records can be searched for it."""
    token = admin("pool-token", name, "default")
    secret = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": "pool-token"},
        "stringData": {"pool-token": token},
    }
    kubectl("apply", "-f", "-", stdin=json.dumps(secret))
    return token


# --- other pods: a stranger, and the prober ----------------------------------------------

CONNECT = (
    "import socket, sys\n"
    "try:\n"
    "    socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=4).close()\n"
    "    print('open')\n"
    "except TimeoutError:\n"
    "    print('timeout')\n"
    "except OSError as error:\n"
    "    print('refused' if error.errno == 111 else type(error).__name__)\n"
)
"""What a TCP connection attempt met: `open`, `refused` (it arrived, nothing listens) or
`timeout` (it was dropped on the way, which is what a NetworkPolicy does)."""

PROBER = '''
import json, sys, time, urllib.error, urllib.request

base = sys.argv[1]
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
asked = failed = 0
failures = []


def ask(method, path, expected, headers):
    """One request on a connection of its own, as a new caller makes it."""
    request = urllib.request.Request(
        base + path, method=method, headers={"Connection": "close", **headers},
        data=b"" if method == "POST" else None,
    )
    try:
        with opener.open(request, timeout=5) as answer:
            status = answer.status
    except urllib.error.HTTPError as error:
        status = error.code
    except Exception as error:
        status = type(error).__name__
    return status == expected, status


while True:
    for method, path, expected, headers in (
        # Settings only: any replica that is up answers.
        ("GET", "/v1/admin/login-config", 200, {}),
        # A follower nobody knows: the answer comes from a query on the database.
        ("POST", "/v1/jobs/claim", 401, {"Authorization": "Bearer e2e-not-a-credential"}),
    ):
        ok, status = ask(method, path, expected, headers)
        asked += 1
        if not ok:
            failed += 1
            if len(failures) < 10:
                failures.append(f"{time.strftime('%H:%M:%S')} {method} {path}: {status}")
    print(json.dumps({"asked": asked, "failed": failed, "failures": failures}), flush=True)
    time.sleep(0.1)
'''
"""Runs in a pod of its own for the whole upgrade: ten requests a second or more through the
leader's Service, each on a new connection. Its last line is the count so far."""


def pod_manifest(name: str, role: str, command: list[str]) -> dict:
    """A pod from the leader's image that is no part of either release."""
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": name, "labels": {"swarmscribe-e2e/role": role}},
        "spec": {
            "restartPolicy": "Never",
            "automountServiceAccountToken": False,
            "terminationGracePeriodSeconds": 1,
            "containers": [
                {
                    "name": name,
                    "image": LEADER_IMAGE,
                    "imagePullPolicy": "Never",
                    "command": ["python", "-u", "-c", *command],
                }
            ],
        },
    }


def start_pod(name: str, role: str, command: list[str]) -> str:
    """Start it and return its address."""
    kubectl("delete", "pod", name, "--ignore-not-found", "--wait=true")
    kubectl("apply", "-f", "-", stdin=json.dumps(pod_manifest(name, role, command)))
    kubectl("wait", "--for=condition=Ready", f"pod/{name}", "--timeout=120s")
    return json.loads(kubectl("get", "pod", name, "-o", "json"))["status"]["podIP"]


def prober_count() -> dict:
    lines = kubectl("logs", "prober", "--tail=1").strip().splitlines()
    expect(bool(lines), "the prober has printed nothing")
    return json.loads(lines[-1])


class ReadyWatch(threading.Thread):
    """Samples, twice a second, how many addresses the leader's Service has. The fewest it
    saw is what a caller could reach at the worst moment."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.fewest: int | None = None
        self.samples = 0
        self.errors: list[str] = []
        self._stop_asked = threading.Event()

    def run(self) -> None:
        while not self._stop_asked.is_set():
            try:
                found = json.loads(
                    kubectl(
                        "get", "endpointslices", "-o", "json",
                        "-l", f"kubernetes.io/service-name={LEADER_NAME}",
                    )
                )
                ready = sum(
                    1
                    for piece in found["items"]
                    for endpoint in piece.get("endpoints") or []
                    if endpoint.get("conditions", {}).get("ready")
                )
                self.samples += 1
                self.fewest = ready if self.fewest is None else min(self.fewest, ready)
            except (AssertionError, ValueError) as error:
                self.errors.append(str(error)[-200:])
            self._stop_asked.wait(0.5)

    def finish(self) -> None:
        self._stop_asked.set()
        self.join(timeout=30)


# --- up, down ----------------------------------------------------------------------------


def load(image: str) -> None:
    """Put a local image into the cluster's node. `kind load docker-image` first; Docker
    Desktop's image store can make it stop with "content digest ... not found" on an image
    that was pulled, so the fallback pipes one platform of it into the node."""
    free_space()
    done = subprocess.run(
        ["kind", "load", "docker-image", image, "--name", CLUSTER],
        capture_output=True, text=True, errors="replace",
    )
    if done.returncode == 0:
        return
    node = f"{CLUSTER}-control-plane"
    save = subprocess.Popen(
        ["docker", "save", "--platform", "linux/amd64", image],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        imported = subprocess.run(
            ["docker", "exec", "-i", node, "ctr", "-n", "k8s.io", "images", "import", "-"],
            stdin=save.stdout, capture_output=True,
        )
    finally:
        # If the import ended early, `docker save` may be blocked writing to a pipe nobody
        # reads: close our end, then stop it if it is still there.
        save.stdout.close()
        if save.poll() is None:
            save.kill()
        saved_err = save.stderr.read()
        save.stderr.close()
        save.wait()
    if imported.returncode == 0 and save.returncode == 0:
        return

    def tail(text: bytes | str) -> str:
        if isinstance(text, bytes):
            text = text.decode("utf-8", "replace")
        return text.strip()[-400:] or "(nothing)"

    raise AssertionError(
        f"could not load {image} into the cluster. `kind load docker-image` said: "
        f"{tail(done.stderr or done.stdout)}. Then `docker save | ctr import` said: "
        f"save: {tail(saved_err)}; import: {tail(imported.stderr or imported.stdout)}"
    )


def up() -> None:
    for tool in ("kind", "kubectl", "docker"):
        expect(shutil.which(tool) is not None, f"{tool} is not on the PATH")
    helm_binary()
    expect(FIXTURE.is_file(), f"{FIXTURE} is missing")
    free_space()
    WORK.mkdir(exist_ok=True)
    if CLUSTER not in call("kind", "get", "clusters").split():
        call(
            "kind", "create", "cluster", "--name", CLUSTER, "--kubeconfig", str(KUBECONFIG),
            "--wait", "120s",
        )
    else:
        KUBECONFIG.write_text(call("kind", "get", "kubeconfig", "--name", CLUSTER), "utf-8")
    for image in (LEADER_IMAGE, NEXT_IMAGE, FOLLOWER_IMAGE, POSTGRES_IMAGE):
        load(image)
    # A fresh namespace every time: the scenario upgrades the leader and runs once per database.
    kubectl("delete", "namespace", NAMESPACE, "--ignore-not-found", "--wait=true")
    kubectl("create", "namespace", NAMESPACE)
    kubectl("apply", "-f", str(HERE / "postgres.yaml"))
    kubectl("rollout", "status", "deploy/postgres", "--timeout=180s")
    print(f"the cluster {CLUSTER} is up, with Postgres and a volume claim in {NAMESPACE}")


def down() -> None:
    call("kind", "delete", "cluster", "--name", CLUSTER, "--kubeconfig", str(KUBECONFIG))
    shutil.rmtree(WORK, ignore_errors=True)
    print(f"deleted the cluster {CLUSTER}")


# --- the scenario ------------------------------------------------------------------------

HARDENING = (
    "import os\n"
    "try:\n"
    "    open('/app/written-by-the-test', 'w').close()\n"
    "    wrote = 'writable'\n"
    "except OSError as error:\n"
    "    wrote = 'read-only' if error.errno == 30 else type(error).__name__\n"
    "pid1 = open('/proc/1/cmdline', 'rb').read().split(b'\\0')[0].decode()\n"
    "print(os.getuid(), wrote, pid1)\n"
)

FETCH_STATUS = (
    "import sys, urllib.error, urllib.request\n"
    "opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))\n"
    "try:\n"
    "    print(opener.open(sys.argv[1], timeout=6).status)\n"
    "except urllib.error.HTTPError as error:\n"
    "    print(error.code)\n"
    "except Exception as error:\n"
    "    print(type(error).__name__)\n"
)

WRITE_STDIN = (
    "import pathlib, sys\n"
    "path = pathlib.Path(sys.argv[1])\n"
    "path.parent.mkdir(parents=True, exist_ok=True)\n"
    "path.write_bytes(sys.stdin.buffer.read())\n"
    "print(path.stat().st_size)\n"
)


def scenario() -> dict[str, float]:
    report: dict[str, float] = {}
    expect(
        not json.loads(kubectl("get", "deploy", "-l", f"app.kubernetes.io/instance={LEADER}",
                               "-o", "json"))["items"],
        "a leader is already installed: run `up` again for a fresh namespace",
    )

    # 1. Install. Helm returns once the hook has run; then both pods become Ready.
    started = time.monotonic()
    install_leader(LEADER_IMAGE)
    kubectl("rollout", "status", f"deploy/{LEADER_NAME}", "--timeout=180s")
    report["ready"] = time.monotonic() - started
    pods = leader_pods()
    expect(len(pods) == 2 and all(is_ready(p) for p in pods), "two leader pods are not Ready")
    head = admin("revision")
    expect(bool(head) and head != NEXT_REVISION, f"the database is at revision {head}")
    for pod in pods:
        name = pod["metadata"]["name"]
        status = pod["status"]["containerStatuses"][0]
        expect(status["restartCount"] == 0, f"{name} restarted: it started before the migration")
        expect(
            in_pod(name, HARDENING) == "10001 read-only /usr/bin/tini",
            f"{name} does not run as 10001 under tini on a read-only root: "
            f"{in_pod(name, HARDENING)}",
        )
        expect(in_pod(name, FETCH_STATUS, "http://127.0.0.1:8080/readyz") == "200",
               f"{name} does not say it is ready")
    jobs_left = json.loads(kubectl("get", "jobs", "-o", "json"))["items"]
    expect(not jobs_left, "the migration Job was not removed after it succeeded")
    kept = helm("get", "all", LEADER) + kubectl("get", "configmap", "-o", "yaml")
    expect(LINK_KEY not in kept, "Helm's record of the release or a ConfigMap holds the link key")
    expect("postgres:postgres@" not in kept, "Helm's record or a ConfigMap holds the database URL")
    budget = json.loads(kubectl("get", "pdb", LEADER_NAME, "-o", "json"))
    expect(budget["spec"]["maxUnavailable"] == 1, "no PodDisruptionBudget of 1")

    # 2. A pool token, a profile and a location, made inside the cluster; then the followers.
    token = store_pool_token("kind-pods")
    admin("profile", "cpu", MODEL, COMPUTE_TYPE)
    size = kubectl(
        "exec", "-i", f"deploy/{LEADER_NAME}", "-c", "leader", "--",
        "python", "-c", WRITE_STDIN, "/data/.e2e/fixture.wav", stdin=FIXTURE.read_bytes(),
    ).strip()
    expect(size == str(FIXTURE.stat().st_size), f"the fixture arrived as {size} bytes")
    admin("location", LOCATION, "stereo_split", "ok/*.wav")
    repository, tag = image_parts(FOLLOWER_IMAGE)
    helm(
        "upgrade", "--install", POOL, str(FOLLOWER_CHART), "-f", str(HERE / "follower-values.yaml"),
        "--set", f"image.repository={repository}", "--set", f"image.tag={tag}",
    )
    kubectl("rollout", "status", f"deploy/{POOL_NAME}", "--timeout=180s")

    def two_followers() -> list[dict] | None:
        active = [f for f in admin("state")["followers"] if f["state"] == "active"]
        return active if len(active) == 2 else None

    until(two_followers, "both followers to register through the leader's Service")
    report["registered"] = time.monotonic() - started

    # 3. Three consented recordings and one the consent file does not name.
    add(NOT_CONSENTED)
    for key in CONSENTED:
        add(key)
    until(lambda: all(completed(key) for key in CONSENTED), "the three recordings")
    for key in CONSENTED:
        check_transcript(key)
        expect(job(key)["text"].lower().startswith("agent: "), f"{key} was not split")
    expect(job(NOT_CONSENTED) is None, "a recording without consent was queued")

    # 4. The NetworkPolicy, seen from both sides. A stranger listens on 8000 and is no peer.
    stranger = start_pod(
        "stranger", "stranger", ["import http.server; http.server.test(port=8000, bind='0.0.0.0')"]
    )
    target = leader_pods()[0]
    target_name, target_ip = target["metadata"]["name"], target["status"]["podIP"]
    follower = json.loads(
        kubectl("get", "pods", "-l", f"app.kubernetes.io/instance={POOL}", "-o", "json")
    )["items"][0]["metadata"]["name"]
    expect(
        in_pod(follower, CONNECT, target_ip, "8080") == "open",
        "a follower cannot reach a leader pod",
    )
    said = in_pod("stranger", CONNECT, target_ip, "8080")
    expect(
        said == "timeout",
        f"a pod that is no peer met `{said}` at a leader pod, not a timeout: does this"
        " cluster's network plugin enforce NetworkPolicy?",
    )
    said = in_pod("stranger", CONNECT, LEADER_NAME, "80")
    expect(said == "timeout", f"a pod that is no peer met `{said}` at the leader's Service")
    said = in_pod(target_name, CONNECT, stranger, "8000")
    expect(said == "timeout", f"a leader pod met `{said}` at another pod's port, not a timeout")
    expect(in_pod(target_name, CONNECT, "postgres", "5432") == "open",
           "a leader pod cannot reach Postgres by name (DNS, then 5432)")
    kubectl("delete", "pod", "stranger", "--wait=false")

    # 5. The upgrade. Requests flow and a recording is in hand before anything changes.
    start_pod("prober", "prober", [PROBER, LEADER_URL])
    until(lambda: prober_count()["asked"] >= 20, "the prober's first requests", 60.0)
    expect(prober_count()["failed"] == 0, f"requests fail before the upgrade: {prober_count()}")
    add(IN_HAND, LONG_REPEATS)
    until(lambda: (job(IN_HAND) or {}).get("state") == "leased", f"a follower to take {IN_HAND}")
    old = sorted(p["metadata"]["name"] for p in leader_pods())
    watch = ReadyWatch()
    watch.start()
    # The rollout is held back, so that the old pods are seen on the migrated database for
    # longer than three failed readiness probes would take to drop them.
    kubectl("rollout", "pause", f"deploy/{LEADER_NAME}")
    upgrade_started = time.monotonic()
    install_leader(NEXT_IMAGE)
    report["hook"] = time.monotonic() - upgrade_started
    expect(admin("revision") == NEXT_REVISION, "the upgrade's hook did not migrate the database")
    held = time.monotonic() + HOLD_SECONDS
    while time.monotonic() < held:
        now = leader_pods()
        expect(
            sorted(p["metadata"]["name"] for p in now) == old,
            "a leader pod was replaced while the rollout was held back",
        )
        for pod in now:
            name = pod["metadata"]["name"]
            expect(image_of(pod) == LEADER_IMAGE, f"{name} already runs the new image")
            expect(
                is_ready(pod),
                f"{name} (the old version) is not Ready on the migrated database: the upgrade"
                " would take every serving pod away",
            )
        time.sleep(2.0)
    for name in old:
        expect(
            in_pod(name, FETCH_STATUS, "http://127.0.0.1:8080/readyz") == "200",
            f"{name} (the old version) does not answer 200 on /readyz after {HOLD_SECONDS} s",
        )
    expect(
        watch.fewest is not None and watch.fewest >= 2,
        f"the leader's Service was down to {watch.fewest} ready address(es) while the old pods"
        " served the migrated database",
    )
    expect(
        (job(IN_HAND) or {}).get("state") == "leased",
        f"{IN_HAND} was finished before the pods were replaced: raise LONG_REPEATS",
    )
    kubectl("rollout", "resume", f"deploy/{LEADER_NAME}")
    kubectl("rollout", "status", f"deploy/{LEADER_NAME}", "--timeout=300s")
    report["upgrade"] = time.monotonic() - upgrade_started
    new = leader_pods()
    expect(
        len(new) == 2 and all(image_of(p) == NEXT_IMAGE and is_ready(p) for p in new),
        "the leader's pods are not two Ready pods of the new image",
    )
    expect(not set(old) & {p["metadata"]["name"] for p in new}, "an old pod is still there")
    time.sleep(5.0)  # requests after the last old pod has gone
    watch.finish()
    count = prober_count()
    report["asked"] = count["asked"]
    expect(not watch.errors, f"the Service could not be watched: {watch.errors[:3]}")
    expect(watch.samples >= HOLD_SECONDS, f"the Service was sampled only {watch.samples} times")
    # While the pods are replaced the count may dip to one for an instant (the Deployment
    # and the Service learn of a Ready pod separately); it must never be none.
    report["fewest"] = watch.fewest or 0
    expect(
        watch.fewest is not None and watch.fewest >= 1,
        "the leader's Service had no ready address at some moment of the upgrade",
    )
    expect(
        count["failed"] == 0 and count["asked"] > 10 * HOLD_SECONDS,
        f"{count['failed']} of {count['asked']} requests failed during the upgrade: "
        f"{count['failures']}",
    )
    done = until(lambda: completed(IN_HAND), f"{IN_HAND} to be finished", 420.0)
    expect(
        done["attempts"] == 1 and [o for _, o in done["tried"]] == ["completed"],
        f"{IN_HAND} was not finished in one attempt across the upgrade: {done['tried']}",
    )
    kubectl("delete", "pod", "prober", "--wait=false")

    # 6. Afterwards: the new leaders hand out work, and no log holds a secret or a link.
    add(AFTER)
    until(lambda: completed(AFTER), f"{AFTER} to be transcribed by the upgraded leader")
    check_transcript(AFTER)
    expect(len(two_followers() or []) == 2, "the followers did not stay with the leader")
    for pod in leader_pods():
        log = kubectl("logs", pod["metadata"]["name"], "--tail=-1")
        expect(token not in log, "a leader's log holds the pool token")
        expect(LINK_KEY not in log, "a leader's log holds the link key")
        expect("/v1/files/" not in log, "a leader's log holds a file link")
    expect(token not in helm("get", "all", POOL), "Helm's record of the pool holds its token")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="SwarmScribe leader chart on kind")
    parser.add_argument("command", choices=("up", "run", "down"))
    command = parser.parse_args().command
    try:
        if command == "up":
            up()
        elif command == "down":
            down()
        else:
            report = scenario()
            print(
                f"passed ({MODEL} on cpu): two leader pods were Ready in {report['ready']:.0f} s "
                f"and two followers registered in {report['registered']:.0f} s; three recordings "
                "were transcribed once each and the one without consent never queued; the "
                "NetworkPolicy let followers in and nobody else, and let the leader reach only "
                f"DNS and Postgres; an upgrade with a migration (hook {report['hook']:.0f} s, "
                f"{report['upgrade']:.0f} s in all) kept the old pods Ready on the new schema "
                f"for {HOLD_SECONDS} s and never fewer than {report['fewest']:.0f} ready "
                "address(es) behind the Service, failed none of "
                f"{report['asked']:.0f} requests and finished a recording in one attempt"
            )
    except AssertionError as error:
        print(f"FAILED: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
