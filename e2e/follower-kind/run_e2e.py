"""The follower chart on a local `kind` cluster, against a real leader (follower spec 10).

A throwaway leader with Postgres (leader.yaml) and the chart `deploy/helm/swarmscribe-follower`
installed with values.yaml: two followers from the real image, with tiny.en baked in. Needs
`kind`, `kubectl`, `helm` and `docker` on the PATH and the two images built. It is run by
hand, not in CI, and its result is recorded in the F3 outcomes document.

    python e2e/follower-kind/run_e2e.py up       # the cluster, the images, the leader
    python e2e/follower-kind/run_e2e.py run      # the scenario below
    python e2e/follower-kind/run_e2e.py no-gpu   # a cuda image where there is no GPU
    python e2e/follower-kind/run_e2e.py down     # delete the cluster

What `run` proves, in order:

 1. the chart installs; both pods become Ready and register with the pool token from the
    Secret; the state folder is the follower's own inside the root-owned emptyDir; the
    listener is on the pod's address and not on loopback;
 2. the NetworkPolicy lets the kubelet probe and nobody else in: another pod cannot connect;
 3. four recordings are transcribed, each once, and say what was said;
 4. a follower killed mid-job (its container restarts) loses nothing, and comes back as the
    same follower: the credential in the emptyDir outlives the container;
 5. a pod deleted mid-job hands its recording back without a counted attempt and is gone
    long before its grace period; another follower redoes the recording;
 6. a pod that replaces one that has gone takes over its row: the leader's list of followers
    does not grow;
 7. an hour-long recording is refused by the memory guard under the pod's memory limit;
 8. a drained pod parks: it stays Running, is not restarted and takes nothing more; deleting
    it brings a new, active follower;
 9. /metrics opens to a peer named in values, without restarting a pod; and no pod's log
    holds the token, a file link or a word of a transcript;
10. revoking the pool token with its followers makes every pod exit 4; a new token in the
    Secret and a rollout restart bring the pool back.

The cluster has its own kubeconfig (work/kubeconfig): yours is not touched. Nothing here
prints a pool token, a credential or a link."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
CHART = HERE.parents[1] / "deploy" / "helm" / "swarmscribe-follower"
WORK = HERE / "work"
KUBECONFIG = WORK / "kubeconfig"
CLUSTER = os.environ.get("KIND_CLUSTER", "swarmscribe-follower-e2e")
NAMESPACE = "swarmscribe-e2e"
RELEASE = "pool"
DEPLOYMENT = f"{RELEASE}-swarmscribe-follower"
LEADER_IMAGE = os.environ.get("LEADER_IMAGE", "swarmscribe-leader:e2e")
FOLLOWER_IMAGE = os.environ.get("FOLLOWER_IMAGE", "swarmscribe-follower:e2e")
CUDA_IMAGE = os.environ.get("CUDA_IMAGE", "swarmscribe-follower:cuda")
POSTGRES_IMAGE = "postgres:16"  # leader.yaml
SELECTOR = f"app.kubernetes.io/instance={RELEASE}"
STATE_DIR = "/var/lib/swarmscribe-follower"  # the chart's mount; the state folder is /state in it

MODEL, COMPUTE_TYPE = "tiny.en", "int8"
CALLS, TALKS = "calls", "talks"  # two locations: split into Agent/Customer, and mono
SHORT = (f"{CALLS}/call-1.wav", f"{CALLS}/call-2.wav", f"{TALKS}/talk-1.wav", f"{TALKS}/talk-2.wav")
KILLED, DELETED = f"{CALLS}/long-kill.wav", f"{CALLS}/long-delete.wav"
TOO_LONG = f"{TALKS}/an-hour.wav"
# Offered after a drain. The other follower takes one recording at a time while a drained one
# that still claimed would be idle and polling, so it would win about half of them: all eight
# missing it has a chance below 0.5%.
AFTER_DRAIN = tuple(f"{TALKS}/after-drain-{n}.wav" for n in range(8))
LEFT_WORD, RIGHT_WORD = "weather", "report"  # what the fixture says, left then right
LONG_REPEATS = 48  # the 5 s fixture, 48 times: four minutes, some 30 s to a minute on two cores
MID_JOB_SECONDS = 5.0
GRACE_SECONDS = 60  # values.yaml: terminationGracePeriodSeconds
MEMORY_LIMIT_MIB = 2500  # values.yaml: resources.limits.memory, which the chart passes in MiB
REFUSAL_LIMIT = f"{MEMORY_LIMIT_MIB} MiB (SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB)"
STEP_SECONDS = 180.0


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


# --- the tools ---------------------------------------------------------------------------


def call(*command: str, stdin: str | None = None, check: bool = True) -> str:
    done = subprocess.run(
        command, input=stdin, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if check and done.returncode != 0:
        said = (done.stderr.strip() or done.stdout.strip())[-600:]
        # No argument is ever a secret: the pool token goes in on stdin.
        raise AssertionError(f"`{' '.join(command)}` failed: {said}")
    return done.stdout


def kubectl(*arguments: str, stdin: str | None = None, check: bool = True) -> str:
    return call(
        "kubectl", "--kubeconfig", str(KUBECONFIG), "-n", NAMESPACE, *arguments,
        stdin=stdin, check=check,
    )


def helm(*arguments: str) -> str:
    return call("helm", "--kubeconfig", str(KUBECONFIG), "-n", NAMESPACE, *arguments)


def image_parts(image: str) -> tuple[str, str]:
    """(repository, tag) of `name:tag`; a registry's port is not a tag."""
    repository, colon, tag = image.rpartition(":")
    if not colon or "/" in tag:
        raise AssertionError(f"{image} must be name:tag")
    return repository, tag


def install(release: str, image: str, *extra: str) -> None:
    repository, tag = image_parts(image)
    helm(
        "upgrade", "--install", release, str(CHART), "-f", str(HERE / "values.yaml"),
        "--set", f"image.repository={repository}", "--set", f"image.tag={tag}", *extra,
    )


def until(check: Callable[[], Any], what: str, within: float = STEP_SECONDS) -> Any:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        found = check()
        if found:
            return found
        time.sleep(1.0)
    raise AssertionError(f"timed out after {within:.0f} s waiting for {what}")


# --- the leader --------------------------------------------------------------------------


def admin(command: str, *arguments: str) -> Any:
    """Run admin.py inside the leader's pod; returns what it printed (one JSON value)."""
    script = (HERE / "admin.py").read_text(encoding="utf-8")
    said = kubectl(
        "exec", "-i", "deploy/leader", "-c", "leader", "--", "python", "-", command, *arguments,
        stdin=script,
    )
    return json.loads(said.strip().splitlines()[-1])


def followers(state: str | None = None) -> list[dict]:
    rows = admin("state")["followers"]
    return [row for row in rows if state is None or row["state"] == state]


def job(key: str) -> dict | None:
    return admin("state")["jobs"].get(key)


def add(key: str, repeats: int = 1) -> None:
    location, _, name = key.partition("/")
    admin("recording", location, name, str(repeats))


def completed(key: str) -> dict | None:
    found = job(key)
    return found if found is not None and found["state"] == "completed" else None


def held_mid_job(key: str) -> str:
    """Wait until one follower has held the job for MID_JOB_SECONDS; returns its id."""

    def holder() -> str | None:
        found = job(key)
        expect(
            found is None or found["state"] != "completed",
            f"{key} was transcribed before it could be interrupted; raise LONG_REPEATS",
        )
        return found["leased_by"] if found and found["state"] == "leased" else None

    first = until(holder, f"a follower to take {key}")
    time.sleep(MID_JOB_SECONDS)
    expect(holder() == first, f"{key} did not stay with one follower for {MID_JOB_SECONDS:.0f} s")
    return first


def store_pool_token(name: str) -> str:
    """A new pool token, put into the Secret the chart reads. Returns it only so that the
    logs can be searched for it."""
    token = admin("pool-token", name, "default")
    secret = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": "pool-token"},
        "stringData": {"pool-token": token},
    }
    kubectl("apply", "-f", "-", stdin=json.dumps(secret))
    return token


# --- the follower pods -------------------------------------------------------------------


def pods() -> list[dict]:
    """The release's pods that are not being deleted."""
    listed = json.loads(kubectl("get", "pods", "-l", SELECTOR, "-o", "json"))["items"]
    return [pod for pod in listed if "deletionTimestamp" not in pod["metadata"]]


def container(pod: dict) -> dict:
    statuses = pod.get("status", {}).get("containerStatuses") or [{}]
    return statuses[0]


def in_pod(name: str, code: str, *arguments: str, check: bool = True) -> str:
    return kubectl("exec", name, "--", "python", "-c", code, *arguments, check=check).strip()


FOLLOWER_ID = (
    "import json, sys;"
    "print(json.load(open(sys.argv[1] + '/state/credential.json'))['follower_id'])"
)
# Only the id is read out of the credential file; the credential itself never leaves the pod.


def follower_id(name: str) -> str | None:
    return in_pod(name, FOLLOWER_ID, STATE_DIR, check=False) or None


def pod_of(wanted: str) -> str:
    def find() -> str | None:
        return next(
            (p["metadata"]["name"] for p in pods() if follower_id(p["metadata"]["name"]) == wanted),
            None,
        )

    return until(find, f"the pod of follower {wanted}", 60.0)


FETCH = (
    "import sys, urllib.request;"
    "opener = urllib.request.build_opener(urllib.request.ProxyHandler({}));"
    "print(opener.open(sys.argv[1], timeout=4).read().decode())"
)


def metrics(name: str, address: str) -> dict[str, float]:
    found = {}
    for line in in_pod(name, FETCH, f"http://{address}:9108/metrics").splitlines():
        if line and not line.startswith("#"):
            sample, _, number = line.rpartition(" ")
            found[sample] = float(number)
    return found


def reachable_from_the_leader(address: str) -> bool:
    """Whether the leader's pod, which is not the kubelet, can read a follower's /metrics."""
    said = kubectl(
        "exec", "deploy/leader", "-c", "leader", "--", "python", "-c", FETCH,
        f"http://{address}:9108/metrics", check=False,
    )
    return "swarmscribe_follower_state" in said


def two_active_followers() -> list[dict] | None:
    active = followers("active")
    return active if len(active) == 2 else None


# --- up, down ----------------------------------------------------------------------------


def load(image: str) -> None:
    """Put a local image into the cluster's node. `kind load docker-image` first; Docker
    Desktop's image store can make it stop with "content digest ... not found" on an image
    that was pulled, so the fallback pipes one platform of it into the node."""
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
    for tool in ("kind", "kubectl", "helm", "docker"):
        expect(shutil.which(tool) is not None, f"{tool} is not on the PATH")
    WORK.mkdir(exist_ok=True)
    if CLUSTER not in call("kind", "get", "clusters").split():
        call(
            "kind", "create", "cluster", "--name", CLUSTER, "--kubeconfig", str(KUBECONFIG),
            "--wait", "120s",
        )
    else:
        KUBECONFIG.write_text(call("kind", "get", "kubeconfig", "--name", CLUSTER), "utf-8")
    for image in (LEADER_IMAGE, FOLLOWER_IMAGE, POSTGRES_IMAGE):
        load(image)
    # A fresh namespace every time: the scenario drains and revokes, and runs once per leader.
    kubectl("delete", "namespace", NAMESPACE, "--ignore-not-found", "--wait=true")
    kubectl("create", "namespace", NAMESPACE)
    manifest = (HERE / "leader.yaml").read_text(encoding="utf-8")
    kubectl("apply", "-f", "-", stdin=manifest.replace("swarmscribe-leader:e2e", LEADER_IMAGE))
    kubectl("rollout", "status", "deploy/postgres", "--timeout=180s")
    kubectl("rollout", "status", "deploy/leader", "--timeout=180s")
    print(f"the cluster {CLUSTER} is up, with a leader in namespace {NAMESPACE}")


def down() -> None:
    call("kind", "delete", "cluster", "--name", CLUSTER, "--kubeconfig", str(KUBECONFIG))
    shutil.rmtree(WORK, ignore_errors=True)
    print(f"deleted the cluster {CLUSTER}")


# --- the scenario ------------------------------------------------------------------------

FOLDERS = (
    "import os, stat, sys;"
    "print(' '.join(f'{os.stat(p).st_uid}:{stat.S_IMODE(os.stat(p).st_mode):o}'"
    " for p in sys.argv[1:]))"
)


def scenario() -> dict[str, float]:
    report: dict[str, float] = {}
    expect(not followers(), "the leader already has followers: run `up` again for a fresh one")

    # 1. Install; both pods are Ready and registered; the folders and the listener are right.
    token = store_pool_token("kind-pods")
    admin("profile", "cpu", MODEL, COMPUTE_TYPE)
    started = time.monotonic()
    install(RELEASE, FOLLOWER_IMAGE)
    kubectl("rollout", "status", f"deploy/{DEPLOYMENT}", "--timeout=180s")
    report["ready"] = time.monotonic() - started
    rows = until(two_active_followers, "both followers to register")
    report["registered"] = time.monotonic() - started
    for row in rows:
        expect(row["device"] == "cpu", f"a follower registered as {row['device']}")
        expect(MODEL in row["models"], "a follower did not report its model")
        expect(row["pool"] == "default", f"a follower is in pool {row['pool']}")
    for pod in pods():
        name, address = pod["metadata"]["name"], pod["status"]["podIP"]
        folders = in_pod(
            name, FOLDERS, STATE_DIR, f"{STATE_DIR}/state", f"{STATE_DIR}/state/credential.json",
            "/run/secrets/swarmscribe/pool-token",
        ).split()
        expect(folders[0].startswith("0:"), f"the state mount is not root's: {folders[0]}")
        expect(
            folders[1:] == ["10001:2700", "10001:600", "0:440"],
            f"the state folder, the credential and the token file are {folders[1:]}",
        )
        expect(metrics(name, address)['swarmscribe_follower_state{state="idle"}'] == 1.0,
               f"{name} is not idle")
        loopback = in_pod(name, FETCH, "http://127.0.0.1:9108/healthz", check=False)
        expect(loopback == "", f"{name} also listens on loopback")
        expect(container(pod).get("restartCount") == 0, f"{name} restarted while starting")

    # 2. The NetworkPolicy: the kubelet's probes pass (the pods are Ready), nobody else gets in.
    target = pods()[0]["status"]["podIP"]
    expect(
        not reachable_from_the_leader(target),
        "another pod can read a follower's /metrics: does this cluster's network plugin"
        " enforce NetworkPolicy?",
    )

    # 3. Four short recordings, each transcribed once.
    admin("location", CALLS, "stereo_split")
    admin("location", TALKS, "mono")
    for key in SHORT:
        add(key)
    until(lambda: all(completed(key) for key in SHORT), "the four short recordings")
    for key in SHORT:
        done = job(key)
        expect([o for _, o in done["tried"]] == ["completed"], f"{key}: {done['tried']}")
        text = (done["text"] or "").lower()
        expect(LEFT_WORD in text and RIGHT_WORD in text, f"{key}: the transcript is wrong")
        if key.startswith(CALLS):
            expect(text.startswith("agent: "), f"{key} was not split into speakers")

    # 4. A follower killed mid-job: its container restarts and it is the same follower.
    add(KILLED, LONG_REPEATS)
    victim = held_mid_job(KILLED)
    victim_pod = pod_of(victim)
    in_pod(
        victim_pod,
        "import os, signal;"
        "[os.kill(int(p), signal.SIGKILL) for p in os.listdir('/proc')"
        " if p.isdigit() and int(p) not in (1, os.getpid())]",
        check=False,
    )
    done = until(lambda: completed(KILLED), f"{KILLED} to be redone", 300.0)
    expect(
        [o for _, o in done["tried"]] == ["expired", "completed"] and done["attempts"] == 2,
        f"{KILLED}: expected the lease to expire and the recording to be redone: {done['tried']}",
    )

    def restarted() -> dict | None:
        status = next(container(p) for p in pods() if p["metadata"]["name"] == victim_pod)
        return status if status.get("restartCount") == 1 and status.get("ready") else None

    status = until(restarted, f"{victim_pod}'s container to be started again", 60.0)
    expect(status["lastState"]["terminated"]["exitCode"] == 137, "the kill was not a kill")
    expect(follower_id(victim_pod) == victim, "the restarted container is another follower")
    expect(
        '"event": "registered"' not in kubectl("logs", victim_pod),
        "the restarted container registered again instead of using its credential",
    )
    expect(len(followers()) == 2, "a container restart added a follower row")

    # 5. A pod deleted mid-job hands the recording back and is gone long before its grace.
    add(DELETED, LONG_REPEATS)
    holder = held_mid_job(DELETED)
    holder_pod = pod_of(holder)
    asked = time.monotonic()
    kubectl("delete", "pod", holder_pod, "--wait=true", f"--timeout={GRACE_SECONDS + 30}s")
    report["stop"] = time.monotonic() - asked
    expect(
        report["stop"] < GRACE_SECONDS / 2,
        f"a pod deleted mid-job took {report['stop']:.0f} s of its {GRACE_SECONDS} s grace",
    )
    done = until(lambda: completed(DELETED), f"{DELETED} to be redone", 300.0)
    outcomes = [o for _, o in done["tried"]]
    expect(
        outcomes[0] == "released" and outcomes[-1] == "completed" and done["attempts"] == 1,
        f"{DELETED}: expected a release that counts no attempt, then a completion: {outcomes}",
    )
    until(two_active_followers, "the replacement pod to register")

    # 6. A pod that replaces one that has gone takes over its row.
    before = len(followers())
    gone_before = {row["id"] for row in followers("gone")}
    kubectl("scale", f"deploy/{DEPLOYMENT}", "--replicas=1")
    until(
        lambda: len(pods()) == 1 and {r["id"] for r in followers("gone")} - gone_before,
        "the follower of the pod that was scaled away to be gone",
    )
    kubectl("scale", f"deploy/{DEPLOYMENT}", "--replicas=2")
    until(two_active_followers, "the new pod to register")
    expect(len(followers()) == before, f"the leader has {len(followers())} rows, not {before}")
    kubectl("rollout", "status", f"deploy/{DEPLOYMENT}", "--timeout=120s")

    # 7. The memory guard, under the pod's memory limit.
    location, _, name = TOO_LONG.partition("/")
    admin("silence", location, name, "3600")

    def parked() -> dict | None:
        found = job(TOO_LONG)
        return found if found is not None and found["state"] == "failed" else None

    refused = until(parked, f"{TOO_LONG} to be refused for good")
    reason = refused["failure_reason"] or ""
    expect(
        reason.startswith("out_of_resources: ") and "60 minutes" in reason
        and REFUSAL_LIMIT in reason,
        f"{TOO_LONG} failed with an unexpected reason: {reason[:200]}",
    )
    expect([o for _, o in refused["tried"]] == ["failed"] * 3, f"{TOO_LONG}: {refused['tried']}")

    # 8. A drained pod parks; deleting it brings a new, active follower.
    drained = followers("active")[0]["id"]
    drained_pod = pod_of(drained)
    resting = next(p for p in pods() if p["metadata"]["name"] == drained_pod)
    address, restarts = resting["status"]["podIP"], container(resting)["restartCount"]
    admin("drain", drained)
    until(
        lambda: metrics(drained_pod, address).get('swarmscribe_follower_state{state="draining"}'),
        "the drained follower to say so in /metrics", 60.0,
    )
    for key in AFTER_DRAIN:
        add(key)
    until(lambda: all(completed(key) for key in AFTER_DRAIN), "the recordings after the drain")
    for key in AFTER_DRAIN:
        tried = [who for who, _ in job(key)["tried"]]
        expect(drained not in tried, f"a drained follower took {key}")
    status = next(container(p) for p in pods() if p["metadata"]["name"] == drained_pod)
    expect(
        status["restartCount"] == restarts and status["ready"],
        "a drained pod did not stay up: it must park, not exit",
    )
    kubectl("delete", "pod", drained_pod, "--wait=true", "--timeout=60s")
    until(two_active_followers, "a new follower to replace the drained one")
    expect(len(followers("draining")) == 1, "the drained follower's row did not stay draining")

    # 9. /metrics for a peer named in values; the pods are not restarted for it. No secrets.
    kubectl("rollout", "status", f"deploy/{DEPLOYMENT}", "--timeout=120s")
    names = sorted(p["metadata"]["name"] for p in pods())
    peers = json.dumps([{"podSelector": {"matchLabels": {"app": "leader"}}}])
    install(RELEASE, FOLLOWER_IMAGE, "--set-json", f"networkPolicy.ingress.from={peers}")
    target = pods()[0]["status"]["podIP"]
    until(lambda: reachable_from_the_leader(target), "/metrics to open to the named peer", 60.0)
    expect(sorted(p["metadata"]["name"] for p in pods()) == names, "the upgrade replaced pods")
    for name in names:
        said = kubectl("logs", name)
        expect(token not in said, f"{name}'s log holds the pool token")
        expect("/v1/files/" not in said, f"{name}'s log holds a file link")
        expect(
            LEFT_WORD not in said.lower() and "quarterly" not in said.lower(),
            f"{name}'s log holds transcript text",
        )

    # 10. Revoke the token and its followers: exit 4, again and again. A new token brings
    # the pool back.
    admin("revoke-pool", "kind-pods")

    def all_exited_4() -> bool:
        ended = [container(p).get("lastState", {}).get("terminated", {}) for p in pods()]
        return len(ended) == 2 and all(state.get("exitCode") == 4 for state in ended)

    until(all_exited_4, "both revoked pods to exit 4")
    expect(not followers("active"), "a follower is still active after the revocation")
    store_pool_token("kind-pods-2")
    kubectl("rollout", "restart", f"deploy/{DEPLOYMENT}")
    kubectl("rollout", "status", f"deploy/{DEPLOYMENT}", "--timeout=180s")
    until(two_active_followers, "two new followers to register with the new token")
    return report


def no_gpu() -> None:
    """What a pod does where there is no GPU: a `cuda` image exits 3 and says why; a pod
    that asks for a GPU stays Pending."""
    load(CUDA_IMAGE)
    try:
        _no_gpu_checks()
    finally:
        for release in ("wrong-node", "gpu-pool"):
            call("helm", "--kubeconfig", str(KUBECONFIG), "-n", NAMESPACE, "uninstall", release,
                 check=False)


def _no_gpu_checks() -> None:
    install("wrong-node", CUDA_IMAGE, "--set", "replicaCount=1")
    install("gpu-pool", CUDA_IMAGE, "--set", "replicaCount=1", "--set", "gpu.enabled=true")

    def pod(release: str) -> dict | None:
        listed = kubectl("get", "pods", "-l", f"app.kubernetes.io/instance={release}", "-o", "json")
        items = json.loads(listed)["items"]
        return items[0] if items else None

    def exited_3() -> str | None:
        found = pod("wrong-node")
        ended = container(found or {}).get("lastState", {}).get("terminated", {})
        return found["metadata"]["name"] if ended.get("exitCode") == 3 else None

    name = until(exited_3, "the cuda image without a GPU to exit 3")
    said = kubectl("logs", name, "--previous").strip().splitlines()[-1]
    expect("no CUDA GPU is available" in said, f"its last line does not say why: {said[:200]}")

    def unschedulable() -> str | None:
        found = pod("gpu-pool") or {}
        for condition in found.get("status", {}).get("conditions", []):
            if condition.get("reason") == "Unschedulable":
                return condition.get("message", "")
        return None

    why = until(unschedulable, "the GPU pod to be found unschedulable", 60.0)
    expect("Insufficient nvidia.com/gpu" in why, f"the GPU pod is unschedulable for: {why}")
    print(
        f"passed: without a GPU the cuda image exits 3 and says `{said}`; a pod of a GPU "
        "pool stays Pending (Insufficient nvidia.com/gpu)"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="SwarmScribe follower chart on kind")
    parser.add_argument("command", choices=("up", "run", "no-gpu", "down"))
    command = parser.parse_args().command
    try:
        if command == "up":
            up()
        elif command == "down":
            down()
        elif command == "no-gpu":
            no_gpu()
        else:
            report = scenario()
            print(
                f"passed ({MODEL} on cpu): two pods were Ready in {report['ready']:.0f} s and "
                f"registered in {report['registered']:.0f} s; a killed follower came back as "
                "itself and its recording was redone; a pod deleted mid-job was gone in "
                f"{report['stop']:.1f} s and counted no attempt; an hour was refused by the "
                "memory guard; a drained pod parked; revoked pods exited 4 and a new token "
                "brought the pool back"
            )
    except AssertionError as error:
        print(f"FAILED: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
