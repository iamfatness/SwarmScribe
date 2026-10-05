"""What the rendered swarmscribe-follower chart must hold, whatever else changes.

Run from the repository root (CI job `chart` does), with Helm on the PATH:

    uv run --no-project --with pyyaml python deploy/helm/swarmscribe-follower/ci/check_render.py

It renders the chart with `helm template` and ci/test-values.yaml (a CPU pool), and again with
ci/gpu-values.yaml on top (a GPU pool), checks the manifests, then renders it with values that
must be refused. `--only core,gpu` runs some sections only (core, gpu, network). Exit status 1
lists every problem."""

import argparse
import ipaddress
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

CHART = Path(__file__).resolve().parents[1]
VALUES = CHART / "ci" / "test-values.yaml"
GPU_VALUES = CHART / "ci" / "gpu-values.yaml"
NAME = "pool-swarmscribe-follower"
DIGEST = "sha256:" + "ab" * 32
NAME_PATTERN = re.compile(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?")
STATE_MOUNT = "/var/lib/swarmscribe-follower"
# What the chart sets itself: an operator's `settings` or `extraEnv` must not set them again.
OWNED = (
    "STATE_DIR", "SCRATCH_DIR", "MODEL_DIR", "HEALTH_ADDR", "ON_DRAINED",
    "SHUTDOWN_GRACE_SECONDS", "MEMORY_LIMIT_MB", "ALLOW_HTTP", "POOL", "DEVICE",
    # The follower also reads these with the prefix (populate_by_name): a token must not reach
    # values, Helm's history or the pod spec by that road.
    "JOIN_TOKEN", "JOIN_TOKEN_FILE", "LEADER_URL", "LEADER_CA_FILE",
)
FIXED = (
    "SWARMSCRIBE_LEADER_URL", "SWARMSCRIBE_JOIN_TOKEN", "SWARMSCRIBE_JOIN_TOKEN_FILE",
    "SWARMSCRIBE_LEADER_CA_FILE", "POD_IP", "OMP_NUM_THREADS",
)
# A follower fetches the links its leader hands it. None of these may be reachable.
MUST_BE_BLOCKED = (
    "0.0.0.1", "127.0.0.1", "169.254.169.254", "169.254.170.2", "100.100.100.200",
    "168.63.129.16", "224.0.0.1", "240.0.0.1", "::1", "::ffff:169.254.169.254",
    "64:ff9b::a9fe:a9fe", "fe80::1", "fec0::1", "ff02::1", "fd00:ec2::254",
)
# Leaders, storage services and model hosts live at addresses like these.
MUST_BE_ALLOWED = ("10.1.2.3", "192.168.1.50", "172.16.0.9", "100.64.0.1", "52.216.0.1",
                   "fd12::1", "2606:4700::1111")


def helm_template(*extra: str, release: str = "pool") -> subprocess.CompletedProcess:
    command = ["helm", "template", release, str(CHART), "--namespace", "transcribe"]
    return subprocess.run([*command, "-f", str(VALUES), *extra], capture_output=True, text=True)


def render(*extra: str, release: str = "pool") -> list[dict]:
    done = helm_template(*extra, release=release)
    if done.returncode != 0:
        raise SystemExit(f"helm template failed:\n{done.stderr}")
    return [doc for doc in yaml.safe_load_all(done.stdout) if doc]


def refused(problems: list[str], what: str, *values: str, strings: bool = False) -> None:
    flag = "--set-string" if strings else "--set"
    if helm_template(flag, ",".join(values)).returncode == 0:
        problems.append(f"the chart renders with {what}")


def values_file(values: dict) -> Path:
    path = Path(tempfile.mkdtemp()) / "values.yaml"
    path.write_text(yaml.safe_dump(values), encoding="utf-8")
    return path


def refused_file(problems: list[str], what: str, values: dict) -> None:
    """`refused` for values --set cannot express (a newline in a key, a list of maps)."""
    if helm_template("-f", str(values_file(values))).returncode == 0:
        problems.append(f"the chart renders with {what}")


def one(docs: list[dict], kind: str, name: str = NAME) -> dict:
    found = [d for d in docs if d["kind"] == kind and d["metadata"]["name"] == name]
    if len(found) != 1:
        raise SystemExit(f"expected one {kind} named {name}, found {len(found)}")
    return found[0]


def kinds(docs: list[dict]) -> set[str]:
    return {doc["kind"] for doc in docs}


def pod_spec(docs: list[dict]) -> dict:
    return one(docs, "Deployment")["spec"]["template"]["spec"]


def follower(docs: list[dict]) -> dict:
    (found,) = [c for c in pod_spec(docs)["containers"] if c["name"] == "follower"]
    return found


def env(container: dict) -> dict[str, dict]:
    return {entry["name"]: entry for entry in container.get("env", [])}


def volume(docs: list[dict], name: str) -> dict | None:
    return next((v for v in pod_spec(docs).get("volumes", []) if v["name"] == name), None)


def mount(container: dict, name: str) -> dict | None:
    return next((m for m in container.get("volumeMounts", []) if m["name"] == name), None)


def check_security(docs: list[dict], problems: list[str]) -> None:
    spec, container = pod_spec(docs), follower(docs)
    pod = spec.get("securityContext", {})
    if pod.get("runAsNonRoot") is not True or pod.get("runAsUser") != 10001:
        problems.append("the pod does not run as the non-root user 10001")
    if pod.get("fsGroup") != 10001:
        problems.append("no fsGroup 10001: the token file would have to be world-readable")
    if pod.get("seccompProfile", {}).get("type") != "RuntimeDefault":
        problems.append("no RuntimeDefault seccomp profile")
    if spec.get("automountServiceAccountToken") is not False:
        problems.append("the service-account token is mounted")
    context = container.get("securityContext", {})
    if context.get("readOnlyRootFilesystem") is not True:
        problems.append("the root filesystem is writable")
    if context.get("allowPrivilegeEscalation") is not False:
        problems.append("privilege escalation is allowed")
    if context.get("capabilities", {}).get("drop") != ["ALL"]:
        problems.append("capabilities are not dropped")
    if spec.get("shareProcessNamespace"):
        problems.append("the process namespace is shared: the image's init must be PID 1")


def check_core(docs: list[dict]) -> list[str]:
    problems: list[str] = []
    notes = (CHART / "templates" / "NOTES.txt").read_text()
    if re.search(r"\S {4,}\S", notes):
        problems.append("NOTES.txt: a run of spaces inside a line (a broken line continuation?)")
    for kind in ("Secret", "Service", "Ingress", "ConfigMap"):
        if kind in kinds(docs):
            problems.append(f"the chart renders a {kind}; a follower pool has none")
    deployment = one(docs, "Deployment")
    if deployment["spec"]["replicas"] != 2:
        problems.append("Deployment: replicaCount is not passed on")
    if one(render("--set", "replicaCount=0"), "Deployment")["spec"]["replicas"] != 0:
        problems.append("Deployment: a pool cannot be parked at 0 replicas")
    check_security(docs, problems)
    spec, container = pod_spec(docs), follower(docs)
    if container["image"] != "swarmscribe-follower:cpu-distil-large-v3":
        problems.append("Deployment: the image is not image.repository:image.tag")
    if container.get("args") != ["run"] or "command" in container:
        problems.append("Deployment: the container does not run the image's init with `run`")

    # The settings the chart owns (follower spec 8.2).
    given = env(container)
    for name, value in (
        ("SWARMSCRIBE_LEADER_URL", "https://leader.example.org"),
        ("SWARMSCRIBE_JOIN_TOKEN_FILE", "/run/secrets/swarmscribe/pool-token"),
        ("SWARMSCRIBE_LEADER_CA_FILE", "/etc/swarmscribe/leader-ca/ca.pem"),
        ("SWARMSCRIBE_FOLLOWER_POOL", "cpu-pods"),
        ("SWARMSCRIBE_FOLLOWER_STATE_DIR", f"{STATE_MOUNT}/state"),
        ("SWARMSCRIBE_FOLLOWER_ON_DRAINED", "park"),
        ("SWARMSCRIBE_FOLLOWER_SHUTDOWN_GRACE_SECONDS", "870"),
        ("SWARMSCRIBE_FOLLOWER_HEALTH_ADDR", "[$(POD_IP)]:9108"),
        ("SWARMSCRIBE_FOLLOWER_ALLOWED_MODELS", "distil-large-v3"),
    ):
        if given.get(name, {}).get("value") != value:
            problems.append(f"Deployment: {name} is not {value!r}")
    if "SWARMSCRIBE_JOIN_TOKEN" in given:
        problems.append("Deployment: the token is in the environment, not a mounted file")
    if "SWARMSCRIBE_FOLLOWER_ALLOW_HTTP" in given:
        problems.append("Deployment: plain http is allowed without leader.allowHttp")
    if "SWARMSCRIBE_FOLLOWER_DEVICE" in given:
        problems.append("Deployment: a CPU pool sets the device (the image decides it)")
    names = [entry["name"] for entry in container["env"]]
    if "POD_IP" not in names or names.index("POD_IP") > names.index(
        "SWARMSCRIBE_FOLLOWER_HEALTH_ADDR"
    ):
        problems.append("Deployment: POD_IP is not defined before the address that uses it")
    if given.get("POD_IP", {}).get("valueFrom", {}).get("fieldRef", {}).get("fieldPath") != (
        "status.podIP"
    ):
        problems.append("Deployment: POD_IP is not the pod's address")
    memory = given.get("SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB", {}).get("valueFrom", {})
    if memory.get("resourceFieldRef", {}).get("resource") != "limits.memory" or str(
        memory.get("resourceFieldRef", {}).get("divisor")
    ) != "1Mi":
        problems.append("Deployment: the memory guard is not given the memory limit in MiB")
    threads = given.get("OMP_NUM_THREADS", {}).get("valueFrom", {}).get("resourceFieldRef", {})
    if threads.get("resource") != "limits.cpu":
        problems.append("Deployment: OMP_NUM_THREADS is not the CPU limit on a CPU pool")
    resources = container.get("resources", {})
    if resources.get("requests", {}).get("memory") != resources.get("limits", {}).get("memory"):
        problems.append("Deployment: the default memory request differs from the limit")
    if "nvidia.com/gpu" in resources.get("limits", {}):
        problems.append("Deployment: a CPU pool asks for a GPU")

    # Stopping: the follower's grace is 30 s inside the pod's (spec 5.6).
    if spec.get("terminationGracePeriodSeconds") != 900:
        problems.append("Deployment: the default grace period is not 900 s")
    short = render("--set", "terminationGracePeriodSeconds=120")
    if env(follower(short))["SWARMSCRIBE_FOLLOWER_SHUTDOWN_GRACE_SECONDS"]["value"] != "90":
        problems.append("Deployment: the follower's grace does not follow the pod's, less 30 s")

    # Probes: /healthz on the named port; no readiness probe (nothing routes to a follower).
    port = container.get("ports", [{}])[0]
    if (port.get("name"), port.get("containerPort")) != ("health", 9108):
        problems.append("Deployment: the health port is not declared")
    for probe in ("startupProbe", "livenessProbe"):
        get = container.get(probe, {}).get("httpGet", {})
        if (get.get("path"), get.get("port")) != ("/healthz", "health") or "host" in get:
            problems.append(f"Deployment: {probe} does not GET /healthz on the pod's address")
    if "readinessProbe" in container:
        problems.append("Deployment: a readiness probe (nothing routes to a follower)")
    moved = render("--set", "healthPort=9200")
    if (
        env(follower(moved))["SWARMSCRIBE_FOLLOWER_HEALTH_ADDR"]["value"] != "[$(POD_IP)]:9200"
        or follower(moved)["ports"][0]["containerPort"] != 9200
    ):
        problems.append("Deployment: healthPort does not move the listener and the port")

    # Volumes: the state folder in memory, scratch on disk, the token as a whole Secret volume.
    state, scratch, token = (volume(docs, n) for n in ("state", "scratch", "pool-token"))
    if not state or state.get("emptyDir", {}).get("medium") != "Memory":
        problems.append("Deployment: the state folder is not a memory-backed emptyDir")
    if (mount(container, "state") or {}).get("mountPath") != STATE_MOUNT:
        problems.append("Deployment: the state emptyDir is not mounted where the image expects")
    if not scratch or "medium" in scratch.get("emptyDir", {"medium": 1}):
        problems.append("Deployment: scratch is not an emptyDir on disk")
    elif scratch["emptyDir"].get("sizeLimit") != "20Gi":
        problems.append("Deployment: scratch has no 20Gi size limit by default")
    secret = (token or {}).get("secret", {})
    if secret.get("secretName") != "swarmscribe-pool-token" or secret.get("items") != [
        {"key": "pool-token", "path": "pool-token"}
    ]:
        problems.append("Deployment: the pool token is not read from poolToken.existingSecret")
    if secret.get("defaultMode") != 0o440:
        problems.append("Deployment: the token file is not 0440")
    token_mount = mount(container, "pool-token") or {}
    if token_mount.get("readOnly") is not True or "subPath" in token_mount:
        problems.append("Deployment: the token is mounted writable or by subPath (never updated)")
    if volume(docs, "models") is not None or mount(container, "models") is not None:
        problems.append("Deployment: a model volume by default (the model is baked in)")
    ca = volume(docs, "leader-ca")
    if not ca or ca.get("configMap", {}).get("name") != "leader-ca":
        problems.append("Deployment: the leader CA is not mounted from leader.ca")
    if any(m["mountPath"] == "/tmp" for m in container.get("volumeMounts", [])):
        problems.append("Deployment: a /tmp volume the image does not need")
    cache = render("--set", "models.volume=emptyDir")
    if (volume(cache, "models") or {}).get("emptyDir", {}).get("sizeLimit") != "10Gi":
        problems.append("Deployment: models.volume=emptyDir mounts no cache")
    claim = render("--set", "models.volume=persistentVolumeClaim,models.existingClaim=whisper")
    if (volume(claim, "models") or {}).get("persistentVolumeClaim") != {"claimName": "whisper"}:
        problems.append("Deployment: models.existingClaim is not mounted")
    if (mount(follower(claim), "models") or {}).get("mountPath") != "/models":
        problems.append("Deployment: the model volume is not mounted at /models")

    plain = render("--set", "leader.url=http://leader.internal:8080,leader.allowHttp=true")
    if env(follower(plain)).get("SWARMSCRIBE_FOLLOWER_ALLOW_HTTP", {}).get("value") != "1":
        problems.append("Deployment: leader.allowHttp does not set ALLOW_HTTP")
    annotated = one(render("--set", "metrics.scrapeAnnotations=true"), "Deployment")
    if annotated["spec"]["template"]["metadata"].get("annotations", {}).get(
        "prometheus.io/port"
    ) != "9108":
        problems.append("Deployment: metrics.scrapeAnnotations adds no annotations")
    if "annotations" in deployment["spec"]["template"]["metadata"]:
        problems.append("Deployment: scrape annotations by default")
    pinned = follower(render("--set", f"image.digest={DIGEST}"))["image"]
    if pinned != f"swarmscribe-follower@{DIGEST}":
        problems.append("Deployment: image.digest does not win over the tag")
    proxied = render(
        "-f", str(values_file({"extraEnv": [{"name": "HTTPS_PROXY", "value": "http://p:3128"}]}))
    )
    if "HTTPS_PROXY" not in env(follower(proxied)):
        problems.append("Deployment: extraEnv HTTPS_PROXY is not passed on")

    # A pod that cannot load its model or register exits within a minute: a new pod must not
    # count as available before then, or a bad rollout walks through the whole pool.
    if deployment["spec"].get("minReadySeconds") != 60:
        problems.append("Deployment: minReadySeconds is not 60 by default")
    for ready in (0, 5):
        got = one(render("--set", f"minReadySeconds={ready}"), "Deployment")["spec"]
        if got.get("minReadySeconds") != ready:
            problems.append(f"Deployment: minReadySeconds={ready} is not passed on")
    refused(problems, "a negative minReadySeconds", "minReadySeconds=-1")
    refused(problems, "a text minReadySeconds", "minReadySeconds=soon")
    # The pod template carries the selector labels and podLabels only: a chart version bump
    # (helm.sh/chart, app.kubernetes.io/version) must not restart every follower.
    template = deployment["spec"]["template"]["metadata"]["labels"]
    if "helm.sh/chart" in template or "app.kubernetes.io/version" in template:
        problems.append("Deployment: the pod template carries the chart or app version label")
    if any(template.get(k) != v for k, v in deployment["spec"]["selector"]["matchLabels"].items()):
        problems.append("Deployment: the pod template lacks a selector label")
    if "helm.sh/chart" not in deployment["metadata"]["labels"]:
        problems.append("Deployment: its own metadata lost the full labels")
    labelled = one(render("--set", "podLabels.team=asr"), "Deployment")
    if labelled["spec"]["template"]["metadata"]["labels"].get("team") != "asr":
        problems.append("Deployment: podLabels are not on the pod template")

    # Values that would deploy a pool that cannot start, or that leak a token.
    refused(problems, "no image repository", "image.repository=")
    refused(problems, "no image tag", "image.tag=")
    refused(problems, "no leader URL", "leader.url=")
    refused(problems, "no pool token Secret", "poolToken.existingSecret=")
    refused(problems, "an http leader without allowHttp", "leader.url=http://leader.internal")
    for what, url in (
        ("a leader URL with a user", "https://user@leader.example.org"),
        ("a leader URL with a password", "https://user:secret@leader.example.org"),
        ("a leader URL with a query", "https://leader.example.org?x=1"),
        ("a leader URL with a fragment", "https://leader.example.org#f"),
        ("a leader URL with a space", "https://leader example.org"),
        ("a leader URL with a quote", 'https://leader".example.org'),
        ("a leader URL without a host", "https://"),
        ("a leader URL of another scheme", "ftp://leader.example.org"),
        ("a leader URL with a newline", "https://leader.example.org\nX"),
        ("a leader URL with port 99999", "https://leader.example.org:99999"),
        ("a leader URL with port 65536", "https://leader.example.org:65536"),
        ("a leader URL with port 0", "https://leader.example.org:0"),
    ):
        refused_file(problems, what, {"leader": {"url": url}})
    for name in OWNED:
        refused(problems, f"{name} under settings", f"settings.{name}=x")
        refused_file(
            problems,
            f"SWARMSCRIBE_FOLLOWER_{name} under extraEnv",
            {"extraEnv": [{"name": f"swarmscribe_follower_{name.lower()}", "value": "x"}]},
        )
    for name in FIXED:
        refused_file(
            problems, f"{name} under extraEnv", {"extraEnv": [{"name": name, "value": "x"}]}
        )
    refused(problems, "a lower-case setting name", "settings.log_format=text")
    refused_file(problems, "a setting name with a space", {"settings": {"A B": "4"}})
    refused_file(
        problems,
        "a setting name that injects an environment entry",
        {"settings": {"X\n            - name: SWARMSCRIBE_JOIN_TOKEN": "plain"}},
    )
    refused(problems, "a PVC model volume without a claim", "models.volume=persistentVolumeClaim")
    for what, value in (
        ("negative replicas", "replicaCount=-1"),
        ("text replicas", "replicaCount=two"),
        ("a grace period under 30 s", "terminationGracePeriodSeconds=20"),
        ("a privileged health port", "healthPort=80"),
        ("health port 70000", "healthPort=70000"),
        ("an unknown pullPolicy", "image.pullPolicy=Sometimes"),
        ("a malformed digest", "image.digest=notadigest"),
        ("a bad fullnameOverride", "fullnameOverride=Not_Valid"),
        ("an unknown model volume", "models.volume=hostPath"),
        ("an unknown value (a typing mistake)", "replicas=3"),
        ("an unknown value under leader", "leader.uri=https://leader.example.org"),
        ("a token in values", "poolToken.value=abc"),
        ("an empty pool name", "pool="),
    ):
        refused(problems, what, value)

    problems += check_names()
    problems += check_quoting()
    return problems


def label_values(node: object):
    """Every string under a `labels` or `matchLabels` map, wherever it sits in a manifest."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("labels", "matchLabels") and isinstance(value, dict):
                yield from (str(v) for v in value.values())
            else:
                yield from label_values(value)
    elif isinstance(node, list):
        for item in node:
            yield from label_values(item)


def check_names() -> list[str]:
    """Every object name and label value stays within 63 characters, for the longest release
    name Helm allows (53), a long fullnameOverride, and a long nameOverride."""
    problems: list[str] = []
    release = ("r" * 52) + "1"
    cases = (
        ("a 53-character release name", {"release": release}, ()),
        ("a release name with dots", {"release": "a" * 20 + "." + "b" * 20 + ".cc"}, ()),
        ("a 70-character fullnameOverride", {}, ("--set", "fullnameOverride=" + "x" * 70)),
        ("a long nameOverride", {"release": release}, ("--set", "nameOverride=" + "n" * 60)),
    )
    for what, kwargs, extra in cases:
        docs = render(*extra, **kwargs)
        for doc in docs:
            name = doc["metadata"]["name"]
            if len(name) > 63 or not NAME_PATTERN.fullmatch(name):
                problems.append(f"{what}: {doc['kind']} name {name!r} is too long or not valid")
        for value in label_values(docs):
            if len(value) > 63:
                problems.append(f"{what}: a label value is {len(value)} characters: {value!r}")
    # Two pools in one namespace must not share an object or a selector.
    first, second = render(release="cpu"), render(release="gpu")
    if {d["metadata"]["name"] for d in first} & {d["metadata"]["name"] for d in second}:
        problems.append("two releases in one namespace share an object name")
    selectors = [
        one(docs, "Deployment", f"{release}-swarmscribe-follower")["spec"]["selector"]
        for docs, release in ((first, "cpu"), (second, "gpu"))
    ]
    if selectors[0] == selectors[1]:
        problems.append("two releases in one namespace select each other's pods")
    return problems


def check_quoting() -> list[str]:
    """Names that look like numbers or booleans stay strings."""
    problems: list[str] = []
    docs = render(
        "--set-string",
        "poolToken.existingSecret=12345,leader.ca.existingConfigMap=789,pool=true,"
        "settings.ALLOWED_MODELS=123",
    )
    if volume(docs, "pool-token")["secret"]["secretName"] != "12345":
        problems.append("Deployment: a Secret named 12345 does not stay a string")
    if volume(docs, "leader-ca")["configMap"]["name"] != "789":
        problems.append("Deployment: a CA ConfigMap named 789 does not stay a string")
    given = env(follower(docs))
    if given["SWARMSCRIBE_FOLLOWER_POOL"]["value"] != "true":
        problems.append("Deployment: a pool named true does not stay a string")
    if given["SWARMSCRIBE_FOLLOWER_ALLOWED_MODELS"]["value"] != "123":
        problems.append("Deployment: a setting of 123 does not stay a string")
    return problems


def check_gpu(_docs: list[dict]) -> list[str]:
    """A GPU pool (follower spec 8.2, decision D4): ci/gpu-values.yaml on top."""
    problems: list[str] = []
    docs = render("-f", str(GPU_VALUES))
    check_security(docs, problems)
    spec, container = pod_spec(docs), follower(docs)
    limits = container["resources"].get("limits", {})
    if limits.get("nvidia.com/gpu") != 1:
        problems.append("GPU pool: the pod does not ask for exactly one GPU")
    if limits.get("memory") != "8Gi" or container["resources"]["requests"].get("cpu") != "2":
        problems.append("GPU pool: the operator's own requests and limits are lost")
    two = render("-f", str(GPU_VALUES), "--set", r"resources.limits.nvidia\.com/gpu=2")
    if follower(two)["resources"]["limits"]["nvidia.com/gpu"] != 1:
        problems.append("GPU pool: a pod can be given two GPUs (one follower uses one)")
    other = render("-f", str(GPU_VALUES), "--set", "gpu.resource=amd.com/gpu")
    if follower(other)["resources"]["limits"].get("amd.com/gpu") != 1:
        problems.append("GPU pool: gpu.resource does not name the resource")
    given = env(container)
    if given.get("SWARMSCRIBE_FOLLOWER_DEVICE", {}).get("value") != "cuda":
        problems.append("GPU pool: the device is not set to cuda")
    if "OMP_NUM_THREADS" in given:
        problems.append("GPU pool: OMP_NUM_THREADS is set from a CPU limit that may not exist")
    if spec.get("runtimeClassName") != "nvidia":
        problems.append("GPU pool: runtimeClassName is not passed on")
    if spec.get("nodeSelector") != {"nvidia.com/gpu.present": "true"}:
        problems.append("GPU pool: the nodeSelector is not passed on")
    if [t.get("key") for t in spec.get("tolerations", [])] != ["nvidia.com/gpu"]:
        problems.append("GPU pool: the tolerations are not passed on")
    strategy = one(docs, "Deployment")["spec"]["strategy"]["rollingUpdate"]
    if strategy != {"maxUnavailable": "25%", "maxSurge": 0}:
        problems.append("GPU pool: a rollout may surge (the new pod would wait for a GPU)")
    cpu = one(_docs, "Deployment")["spec"]["strategy"]["rollingUpdate"]
    if cpu != {"maxUnavailable": "25%", "maxSurge": "25%"}:
        problems.append("CPU pool: the rollout does not surge")
    if "runtimeClassName" in pod_spec(_docs):
        problems.append("CPU pool: a runtimeClassName by default")
    budget = one(docs, "PodDisruptionBudget")["spec"]
    labels = one(docs, "Deployment")["spec"]["template"]["metadata"]["labels"]
    if any(labels.get(k) != v for k, v in budget["selector"]["matchLabels"].items()):
        problems.append("PodDisruptionBudget: does not select the follower pods")
    if budget.get("unhealthyPodEvictionPolicy") != "AlwaysAllow":
        problems.append("PodDisruptionBudget: a crash-looping pod is not evictable")
    if "PodDisruptionBudget" in kinds(_docs):
        problems.append("a PodDisruptionBudget by default (it only slows node drains)")
    single = render("-f", str(GPU_VALUES), "--set", "replicaCount=1")
    if "PodDisruptionBudget" in kinds(single):
        problems.append("a PodDisruptionBudget is rendered for one replica (it blocks drains)")
    for zero in ("0", "0%"):
        refused_file(
            problems,
            f"podDisruptionBudget.maxUnavailable {zero!r} (it blocks every eviction)",
            {"podDisruptionBudget": {"enabled": True, "maxUnavailable": zero}},
        )
    for allowed in ("25%", "1"):
        for as_string in (True, False):
            if as_string or allowed == "1":
                flag = "--set-string" if as_string else "--set"
                got = one(
                    render(
                        "-f", str(GPU_VALUES), flag, f"podDisruptionBudget.maxUnavailable={allowed}"
                    ),
                    "PodDisruptionBudget",
                )["spec"]["maxUnavailable"]
                if str(got) != allowed:
                    problems.append(f"podDisruptionBudget.maxUnavailable {allowed} is not kept")
    return problems


def cut_out(address: str, rule: dict) -> bool:
    """Whether the https egress rule leaves `address` unreachable."""
    ip = ipaddress.ip_address(address)
    for peer in rule["to"]:
        block = peer["ipBlock"]
        network = ipaddress.ip_network(block["cidr"])
        if ip.version != network.version or ip not in network:
            continue
        if not any(ip in ipaddress.ip_network(cut) for cut in block.get("except", [])):
            return False
    return True


def https_rule(policy: dict) -> dict:
    found = [rule for rule in policy["egress"] if all("ipBlock" in p for p in rule["to"])]
    if len(found) != 1:
        raise SystemExit(f"expected one ipBlock egress rule, found {len(found)}")
    return found[0]


def tcp_ports(rule: dict) -> list[int]:
    return [port["port"] for port in rule["ports"] if port["protocol"] == "TCP"]


def check_network(docs: list[dict]) -> list[str]:
    problems: list[str] = []
    policy = one(docs, "NetworkPolicy")["spec"]
    labels = one(docs, "Deployment")["spec"]["template"]["metadata"]["labels"]
    if sorted(policy["policyTypes"]) != ["Egress", "Ingress"]:
        problems.append("NetworkPolicy: does not cover both directions")
    if any(labels.get(k) != v for k, v in policy["podSelector"]["matchLabels"].items()):
        problems.append("NetworkPolicy: does not select the follower pods")

    # Ingress: /metrics has no authentication. Only the named peers, only the health port.
    monitoring = {"matchLabels": {"kubernetes.io/metadata.name": "monitoring"}}
    if policy["ingress"] != [
        {"ports": [{"protocol": "TCP", "port": 9108}], "from": [{"namespaceSelector": monitoring}]}
    ]:
        problems.append("NetworkPolicy: ingress is not the health port from the named peers only")
    closed = one(render("--set", "networkPolicy.ingress.from=null"), "NetworkPolicy")["spec"]
    if closed.get("ingress") != [] or "Ingress" not in closed["policyTypes"]:
        problems.append("NetworkPolicy: without peers, ingress is not closed to everyone")
    moved = one(render("--set", "healthPort=9200"), "NetworkPolicy")["spec"]["ingress"]
    if moved[0]["ports"] != [{"protocol": "TCP", "port": 9200}]:
        problems.append("NetworkPolicy: ingress does not follow healthPort")

    # Egress: DNS, and the leader's port and 443 to anywhere but the refused ranges.
    rule = https_rule(policy)
    for address in MUST_BE_BLOCKED:
        if not cut_out(address, rule):
            problems.append(f"NetworkPolicy: {address} is reachable")
    for address in MUST_BE_ALLOWED:
        if cut_out(address, rule):
            problems.append(f"NetworkPolicy: {address} is cut out")
    if not cut_out("10.96.0.1", rule) or not cut_out("fd00:10:96::1", rule):
        problems.append("NetworkPolicy: extraExcept is not applied")
    for peer in rule["to"]:
        network = ipaddress.ip_network(peer["ipBlock"]["cidr"])
        for cut in peer["ipBlock"].get("except", []):
            inner = ipaddress.ip_network(cut)
            if inner.version != network.version or not inner.subnet_of(network):
                problems.append(f"NetworkPolicy: except {cut} is outside {network}")
    if tcp_ports(rule) != [443]:
        problems.append(f"NetworkPolicy: egress ports for an https leader are {tcp_ports(rule)}")
    ports = [port for each in policy["egress"] for port in each["ports"]]
    if {"protocol": "UDP", "port": 53} not in ports:
        problems.append("NetworkPolicy: DNS is not allowed")
    # The leader's own port is always open: a leader on 8443, or plain http inside a cluster.
    for url, extra, wanted in (
        ("https://leader.example.org:8443", "", [8443, 443]),
        ("https://leader.example.org:8443/swarm", "", [8443, 443]),
        ("https://[fd12::5]:8443", "", [8443, 443]),
        ("http://leader.internal", ",leader.allowHttp=true", [80, 443]),
        ("http://leader.internal:8080", ",leader.allowHttp=true", [8080, 443]),
        ("https://leader.example.org:443", "", [443]),
    ):
        got = tcp_ports(
            https_rule(
                one(render("--set", f"leader.url={url}{extra}"), "NetworkPolicy")["spec"]
            )
        )
        if got != wanted:
            problems.append(f"NetworkPolicy: for {url} the egress ports are {got}, not {wanted}")
    only = render(
        "--set", "leader.url=http://leader:8080,leader.allowHttp=true",
        "--set", "networkPolicy.egress.https.ports=null",
    )
    if tcp_ports(https_rule(one(only, "NetworkPolicy")["spec"])) != [8080]:
        problems.append("NetworkPolicy: with no https ports the leader's port is not the only one")

    # The API server refuses an `except` outside its `cidr`: a narrowed range gets none.
    narrowed = render("--set", "networkPolicy.egress.https.cidrs={10.0.0.0/8}")
    for peer in https_rule(one(narrowed, "NetworkPolicy")["spec"])["to"]:
        if "except" in peer["ipBlock"]:
            problems.append("NetworkPolicy: a narrowed range carries the catch-all's except list")
    if "NetworkPolicy" in kinds(render("--set", "networkPolicy.enabled=false")):
        problems.append("a disabled NetworkPolicy is still rendered")
    return problems


SECTIONS = {"core": check_core, "gpu": check_gpu, "network": check_network}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", default=",".join(SECTIONS), help="sections, comma-separated")
    chosen = parser.parse_args().only.split(",")
    docs = render()
    problems = [problem for section in chosen for problem in SECTIONS[section](docs)]
    for problem in problems:
        print(f"FAILED: {problem}", file=sys.stderr)
    if not problems:
        print(f"the rendered chart holds every required property ({', '.join(chosen)})")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
