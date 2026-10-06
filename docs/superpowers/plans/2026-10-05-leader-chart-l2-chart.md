# Leader Chart L2 — The Helm Chart and Its Guide Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a Helm chart that deploys the leader (and only the leader) on Kubernetes, to the standard of the console's and the follower's charts: a migration hook, volumes the operator provides, a NetworkPolicy, an optional Ingress with TLS, values that cannot render a leader nobody can reach or administer, a render check, and a deployment guide.

**Architecture:** One chart, `deploy/helm/swarmscribe-leader`, renders a Deployment, a migration Job run as a pre-install and pre-upgrade hook, a Service, an Ingress with TLS, a ConfigMap, a PodDisruptionBudget, a NetworkPolicy and a ServiceAccount. It never creates a Secret, a volume or a database. A Python script renders the chart and asserts what must hold and what must be refused; CI runs it with `helm lint` and `kubeconform` in the existing job `chart`.

**Tech Stack:** Helm 4.3.0, Kubernetes 1.27 or later, kubeconform 0.8.0, Python with PyYAML for the render check, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-05-leader-chart-design.md` — sections 3 (decisions), 5 (values), 7 (migrations and upgrades), 8 (replicas and storage), 9 (security and the network), 10 (the walk-through), 11 (verification).

**This plan is the second of three.** It needs L1 (`2026-10-05-leader-chart-l1-image-and-readiness.md`) merged into the branch: the image `docker/leader.Dockerfile` (entrypoint tini and `swarmscribe-leader`, user 10001, port 8080, `sleep` on the PATH) and a `/readyz` that is ready on a newer schema and answers within 3 s. L3 (`2026-10-05-leader-chart-l3-kind-and-guide.md`) installs the chart on `kind`. L2 works on its own: after it the chart lints, validates and renders in CI, and can be installed by hand.

## Global Constraints

- **Work only in the worktree `C:\Users\walla\SwarmScribe-leader-chart`, on the branch `leader-chart`.** Other agents work in `C:\Users\walla\SwarmScribe`, `SwarmScribe-ui` and `SwarmScribe-f4`.
- **This plan runs no Docker and no cluster.** Helm and kubeconform render and validate text. If a step seems to need Docker, it is not this plan's (it is L3's).
- **Never kill a process by name.**
- "Images are built and tested in CI but not published to a registry" (owner): `image.repository` and `image.tag` have no working default.
- "No KMS integration; secrets come from Kubernetes Secrets the operator supplies" (owner): the chart renders no Secret and no secret value; it names one Secret.
- "Consent gating is never weakened by a deployment option" (owner): no value of this chart touches consent.
- "Leader `Deployment`, 2+ replicas, `PodDisruptionBudget`, `Service`, `Ingress`. Migration `Job` as a pre-upgrade hook. Postgres is external by default" (master spec, section 12).
- "The local backend requires all leader replicas to see the same path (a shared volume in Kubernetes)" (master spec, section 9). The leader has no other backend today (leader chart spec, 1.7).
- The pods run as the image's user 10001 with a read-only root filesystem, no capabilities, no privilege escalation, the `RuntimeDefault` seccomp profile and no service-account token, as the other two charts' do.
- **The development machine** is Windows 11 with Git Bash. `uv` is run as `python -m uv` (written `uv run ...` below). Helm and kubeconform are standalone binaries in `$TEMP/chart-tools`; see "The tools on the Windows machine".
- SwarmScribe is general-purpose: examples in values and in the guide name no use case.
- Commit after every task, on `leader-chart`. Do not push. End every commit message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

### The tools on the Windows machine

Helm and kubeconform are already in `$TEMP/chart-tools`; fetch whichever is missing (Git Bash), and never install one system-wide:

```bash
mkdir -p "$TEMP/chart-tools" && cd "$TEMP/chart-tools"
[ -x windows-amd64/helm.exe ] || { curl -sL -o helm.zip https://get.helm.sh/helm-v4.3.0-windows-amd64.zip && unzip -q -o helm.zip; }
[ -x kubeconform.exe ] || { curl -sL -o kc.zip https://github.com/yannh/kubeconform/releases/download/v0.8.0/kubeconform-windows-amd64.zip && unzip -q -o kc.zip; }
cd - >/dev/null
export PATH="$TEMP/chart-tools/windows-amd64:$TEMP/chart-tools:$PATH"
helm version --short    # v4.3.0+gbec5b06
kubeconform -v          # v0.8.0
```

Run that `export` in every shell that runs a `helm` or `kubeconform` command below. kubeconform fetches the Kubernetes schemas from the network.

## Prototyped before this plan was written

Every file of the chart below, the schema and `ci/check_render.py` were written and run in a scratch folder outside the repository on 2026-10-05 (Helm v4.3.0, kubeconform v0.8.0, Python 3.12), against a copy of the leader's `config.py` and `api/` from `main` at `bc19dfd`:

| What | Result |
|---|---|
| `helm lint --strict` with `ci/test-values.yaml` | `1 chart(s) linted, 0 chart(s) failed` (one `[INFO]`: no icon) |
| `kubeconform -strict -kubernetes-version 1.33.0` on the render | 8 resources, all valid; 7 with one replica and `Recreate` |
| `check_render.py --only core,storage` with only Task 1's templates | passed |
| the same with `migrate`, with Task 2's template missing | `expected one Job named leader-swarmscribe-leader-migrate, found 0` |
| `--only core,storage,migrate` after Task 2; `--only ingress` before Task 3 | passed; `expected one Ingress named leader-swarmscribe-leader, found 0` |
| `--only core,storage,migrate,ingress` after Task 3; `--only network` before Task 4 | passed; `expected one PodDisruptionBudget named leader-swarmscribe-leader, found 0` |
| the whole check after Task 4 | `the rendered chart holds every required property (core, storage, migrate, ingress, network)`, about 14 s |
| `ruff check` on `check_render.py` with the repository's settings | passed |

So the "Expected" lines below are what was seen, not what was hoped. What the prototype cannot say: whether the rendered objects *work* in a cluster. That is L3.

Two mistakes of the check's own were found and corrected while prototyping, and are worth knowing: an empty `image.repository` is refused by the **schema** (`minLength`) before any template's message can be given, so that case has no `says=`; and Helm refuses a release name that ends in a hyphen by itself, so the trailing-hyphen case is made with `fullnameOverride`.

## Rulings

Those marked **(owner)** are the owner's to overturn.

1. **Chart location: `deploy/helm/swarmscribe-leader`**, beside the other two; one release is one leader deployment. Not an umbrella chart (C4b's ruling 1 holds: each part has its own release cycle).
2. **The chart never creates a Secret.** `secrets.existingSecret` is required. One Secret, with the database URL under one key and the link key under another, and up to three sign-in secrets.
3. **Non-secret settings go in a ConfigMap** read with `envFrom`; a checksum annotation restarts the pods when it changes. Role mappings are non-secret: group ids, emails, domains.
4. **The migration is a Job with `helm.sh/hook: pre-install,pre-upgrade`**, carrying its settings inline, with the namespace's default ServiceAccount and no token, and no volume.
5. **Probes:** startup and liveness on `/healthz`, readiness on `/readyz` with `timeoutSeconds: 5` (the leader's own check gives up at 3 s) every 5 s. Liveness never depends on the database.
6. **`replicaCount` defaults to 2 and is not capped.** The code is safe with several (spec 1.8). The condition, that every replica sees the same files, cannot be checked by a chart; it is stated in `values.yaml`, `NOTES.txt` and the guide.
7. **`storage.volumes` is required; the chart creates no PersistentVolumeClaim.** An entry is `existingClaim` or a raw `volume` source, never both, never an `emptyDir`. Mounted at paths that are not `/` and not under `/app`. **(owner)**: whether the chart should also be able to create a claim (open question 5; recommended no).
8. **`updateStrategy: RollingUpdate | Recreate`.** `RollingUpdate` is `maxUnavailable: 0, maxSurge: 1`. `Recreate` is for a ReadWriteOnce claim on several nodes and is an outage on every upgrade; the values file says so.
9. **A `preStop` sleep** (`preStopSleepSeconds`, 5) run as `sleep` from the image, not the native `sleep` action: the chart supports Kubernetes 1.27, and the native action is on by default only from 1.30.
10. **`publicUrl` is `https://<DNS hostname>[:port]`**; `http://` only with `allowHttpPublicUrl`; with the Ingress on, `https` and no port. An IP address is refused in every case.
11. **Sign-in is required** unless `oidc.allowNone`; with sign-in, `roles.admin` must name someone; each role list needs its provider. The last rule repeats the leader's own (`config.py:189-200`) so that the mistake fails `helm install` and not every pod.
12. **The Ingress publishes `/v1` by default** and refuses `/` and the probes. The render check reads the leader's routers' prefixes from `packages/leader/src/swarmscribe_leader/api/*.py` and fails if one is outside the published paths.
13. **NetworkPolicy on by default.** Ingress peers are required (or `anySource: true`); Postgres peers are required; the HTTPS egress rule is rendered only with sign-in. The ranges cut out of "anywhere" are the other two charts' lists, unchanged.
14. **No ServiceMonitor, no scrape annotations, no HorizontalPodAutoscaler.** The leader has no `/metrics` (spec 1.9). The render check fails if one appears.
15. **CI: the existing job `chart`** gets three steps per task, as it has for the other two charts. No new job in this plan.
16. **`kubeVersion: ">=1.27.0-0"`**, as the console's: `unhealthyPodEvictionPolicy` needs 1.27.
17. **The leader's requests (100m, 256Mi; limit 512Mi) are the console's and are not measured.** The values file says "not measured".

## Review Focus

1. **A first install**: the hook Job runs before the chart's ConfigMap and ServiceAccount exist. It must reference neither, carry the same settings as the leader, and mount nothing — Task 2, `check_migrate`.
2. **Values that would deploy a leader nobody can reach or administer**: no sign-in; sign-in with nobody under `roles.admin`; a NetworkPolicy with no ingress peer; no storage volume. Each must fail the render with a message that says what to set — Tasks 1 and 4.
3. **A secret can never reach the ConfigMap, the values or the pod spec in the clear**: `settings` and `extraEnv` refuse `DATABASE_URL`, `LINK_KEY`, `*_SECRET` and `GOOGLE_SERVICE_ACCOUNT` in any letter case; only the secrets a feature needs are read — Task 1.
4. **Every setting the chart writes is one the leader has**: the leader ignores unknown variables, so a misspelt `SWARMSCRIBE_ROLE_ADMIN_EMAIL` would silently give nobody the role — Task 1, `check_setting_names`.
5. **Storage that cannot work**: an `emptyDir`, a mount over `/app`, two volumes on one path — Task 1, `check_storage`.
6. **The probes are never published, and every route is**: Task 3.
7. **One replica**: no PodDisruptionBudget, or node drains hang — Task 4.
8. **A leader without sign-in has no egress to the internet**, and a narrowed HTTPS range carries no `except` list (the API server would refuse the whole release) — Task 4.

## File Structure

```
deploy/helm/swarmscribe-leader/
  Chart.yaml
  values.yaml                 every value, documented
  values.schema.json          types, enums, patterns
  .helmignore
  ci/
    test-values.yaml          the values CI lints and renders with: everything turned on
    check_render.py           what the rendered chart must hold
  templates/
    _helpers.tpl              names, labels, validation, settings, security contexts, refused ranges
    serviceaccount.yaml
    configmap.yaml            non-secret settings
    deployment.yaml           the leader pods, with the storage volumes
    service.yaml
    NOTES.txt
    migrate-job.yaml          Task 2
    ingress.yaml              Task 3
    pdb.yaml                  Task 4
    networkpolicy.yaml        Task 4
```

Also modified: `.github/workflows/ci.yml` (job `chart`, Tasks 1 to 4), `README.md` (Task 5).

---

### Task 1: The chart's core — settings, storage, Deployment, Service

**Files:**
- Create: `deploy/helm/swarmscribe-leader/Chart.yaml`
- Create: `deploy/helm/swarmscribe-leader/.helmignore`
- Create: `deploy/helm/swarmscribe-leader/values.yaml`
- Create: `deploy/helm/swarmscribe-leader/values.schema.json`
- Create: `deploy/helm/swarmscribe-leader/ci/test-values.yaml`
- Create: `deploy/helm/swarmscribe-leader/ci/check_render.py`
- Create: `deploy/helm/swarmscribe-leader/templates/_helpers.tpl`
- Create: `deploy/helm/swarmscribe-leader/templates/serviceaccount.yaml`
- Create: `deploy/helm/swarmscribe-leader/templates/configmap.yaml`
- Create: `deploy/helm/swarmscribe-leader/templates/deployment.yaml`
- Create: `deploy/helm/swarmscribe-leader/templates/service.yaml`
- Create: `deploy/helm/swarmscribe-leader/templates/NOTES.txt`
- Modify: `.github/workflows/ci.yml` (job `chart`)

**Interfaces:**
- Consumes: the L1 image (arguments `serve --host 0.0.0.0 --port <port>`; user 10001; `/healthz`, `/readyz`; `sleep`); the `SWARMSCRIBE_*` settings (`packages/leader/src/swarmscribe_leader/config.py`); the routers' prefixes (`api/*.py`).
- Produces: named templates used by later tasks — `swarmscribe-leader.fullname`, `.name`, `.labels`, `.selectorLabels`, `.image`, `.host` (the Ingress host, from `publicUrl`), `.signIn` (`"true"` or empty), `.validate`, `.settings` (a YAML map of environment names to strings), `.secretEnv` (container `env` entries), `.podSecurityContext` (with `fsGroup`), `.jobSecurityContext` (without), `.containerSecurityContext`, `.refusedV4`, `.refusedV6`. Pod label `app.kubernetes.io/component: leader` (later: `migrate` on the Job's pod). `ci/check_render.py` with sections `core`, `storage`, `migrate`, `ingress`, `network` and the flag `--only`. With release name `leader`, every object is named `leader-swarmscribe-leader`. L3's `kind` test relies on these names, on the pod's container being named `leader`, and on the label selector `app.kubernetes.io/instance=<release>,app.kubernetes.io/component=leader`.

- [ ] **Step 1: Write the render check (the failing test)**

Create `deploy/helm/swarmscribe-leader/ci/check_render.py`. It holds all five sections now; this task makes `core` and `storage` pass, and Tasks 2 to 4 the others.

```python
"""What the rendered swarmscribe-leader chart must hold, whatever else changes.

Run from the repository root (CI job `chart` does), with Helm on the PATH:

    uv run --no-project --with pyyaml python deploy/helm/swarmscribe-leader/ci/check_render.py

It renders the chart with `helm template` and ci/test-values.yaml, checks the manifests, then
renders it with values that must be refused. `--only core,storage` runs some sections only
(core, storage, migrate, ingress, network). Exit status 1 lists every problem."""

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
LEADER = CHART.parents[2] / "packages" / "leader" / "src" / "swarmscribe_leader"
NAME = "leader-swarmscribe-leader"
# test-values.yaml turns everything on, so all five are expected there.
SECRET_NAMES = {
    "SWARMSCRIBE_DATABASE_URL",
    "SWARMSCRIBE_LINK_KEY",
    "SWARMSCRIBE_ENTRA_CLIENT_SECRET",
    "SWARMSCRIBE_GOOGLE_CLIENT_SECRET",
    "SWARMSCRIBE_GOOGLE_SERVICE_ACCOUNT",
}
# The least a leader needs: the two every leader reads.
ALWAYS_SECRET = {"SWARMSCRIBE_DATABASE_URL", "SWARMSCRIBE_LINK_KEY"}
# Values for a leader with no sign-in (the kind test's shape).
NO_SIGN_IN = {
    "oidc": {
        "allowNone": True,
        "entra": {"enabled": False, "clientSecret": False},
        "google": {"enabled": False, "serviceAccount": False},
    },
    "roles": {
        "viewer": {"domains": []},
        "operator": {"googleGroups": []},
        "admin": {"entraGroups": [], "emails": []},
    },
}
# One address from each range no identity provider lives in. Each must be cut out.
MUST_BE_BLOCKED = (
    "0.0.0.1",
    "127.0.0.1",
    "169.254.169.254",
    "169.254.170.2",
    "100.100.100.200",
    "168.63.129.16",
    "224.0.0.1",
    "240.0.0.1",
    "::1",
    "::ffff:169.254.169.254",
    "64:ff9b::a9fe:a9fe",
    "fe80::1",
    "fec0::1",
    "ff02::1",
    "fd00:ec2::254",
    "2002:a9fe:a9fe::1",
    "2001:0:a9fe:a9fe::1",
)
# Identity providers and proxies live at addresses like these; none may be cut out.
MUST_BE_ALLOWED = ("10.1.2.3", "192.168.1.50", "20.190.128.1", "142.250.1.1", "2606:4700::1111")
DIGEST = "sha256:" + "ab" * 32
NAME_PATTERN = re.compile(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?")


def helm_template(*extra: str, release: str = "leader") -> subprocess.CompletedProcess:
    command = ["helm", "template", release, str(CHART), "--namespace", "swarmscribe"]
    return subprocess.run([*command, "-f", str(VALUES), *extra], capture_output=True, text=True)


def render(*extra: str, release: str = "leader") -> list[dict]:
    done = helm_template(*extra, release=release)
    if done.returncode != 0:
        raise SystemExit(f"helm template failed:\n{done.stderr}")
    return [doc for doc in yaml.safe_load_all(done.stdout) if doc]


def values_file(values: dict) -> Path:
    path = Path(tempfile.mkdtemp()) / "values.yaml"
    path.write_text(yaml.safe_dump(values), encoding="utf-8")
    return path


def render_with(values: dict, *extra: str) -> list[dict]:
    return render("-f", str(values_file(values)), *extra)


def refused(
    problems: list[str], what: str, *values: str, strings: bool = False, says: str = ""
) -> None:
    """The chart must not render with these --set values; `says` must be in the message."""
    flag = "--set-string" if strings else "--set"
    _must_fail(problems, what, helm_template(flag, ",".join(values)), says)


def refused_file(problems: list[str], what: str, values: dict, says: str = "") -> None:
    """`refused` for values --set cannot express (a newline in a key, a list of maps)."""
    _must_fail(problems, what, helm_template("-f", str(values_file(values))), says)


def _must_fail(
    problems: list[str], what: str, done: subprocess.CompletedProcess, says: str
) -> None:
    if done.returncode == 0:
        problems.append(f"the chart renders with {what}")
    elif says and says not in done.stderr:
        problems.append(f"{what} is refused without saying {says!r}: {done.stderr.strip()[-300:]}")


def one(docs: list[dict], kind: str, name: str = NAME) -> dict:
    found = [d for d in docs if d["kind"] == kind and d["metadata"]["name"] == name]
    if len(found) != 1:
        raise SystemExit(f"expected one {kind} named {name}, found {len(found)}")
    return found[0]


def kinds(docs: list[dict]) -> set[str]:
    return {doc["kind"] for doc in docs}


def pod_spec(docs: list[dict]) -> dict:
    return one(docs, "Deployment")["spec"]["template"]["spec"]


def leader_container(docs: list[dict]) -> dict:
    return pod_spec(docs)["containers"][0]


def leader_labels(docs: list[dict]) -> dict:
    return one(docs, "Deployment")["spec"]["template"]["metadata"]["labels"]


def check_pod(
    spec: dict, container: str, problems: list[str], where: str, secrets: set[str]
) -> dict:
    pod = spec.get("securityContext", {})
    if pod.get("runAsNonRoot") is not True or pod.get("runAsUser") != 10001:
        problems.append(f"{where}: the pod does not run as the non-root user 10001")
    if pod.get("seccompProfile", {}).get("type") != "RuntimeDefault":
        problems.append(f"{where}: no RuntimeDefault seccomp profile")
    if spec.get("automountServiceAccountToken") is not False:
        problems.append(f"{where}: the service-account token is mounted")
    if spec.get("hostNetwork") or spec.get("hostPID") or spec.get("shareProcessNamespace"):
        problems.append(f"{where}: the pod shares a host or process namespace")
    (found,) = [c for c in spec["containers"] if c["name"] == container]
    context = found.get("securityContext", {})
    if context.get("readOnlyRootFilesystem") is not True:
        problems.append(f"{where}: the root filesystem is writable")
    if context.get("allowPrivilegeEscalation") is not False:
        problems.append(f"{where}: privilege escalation is allowed")
    if context.get("capabilities", {}).get("drop") != ["ALL"]:
        problems.append(f"{where}: capabilities are not dropped")
    names = set()
    for entry in found.get("env", []):
        names.add(entry["name"])
        if entry["name"] in SECRET_NAMES and "secretKeyRef" not in entry.get("valueFrom", {}):
            problems.append(f"{where}: {entry['name']} is not read from the Secret")
    for needed in sorted(secrets):
        if needed not in names:
            problems.append(f"{where}: {needed} is missing")
    for extra in sorted((names & SECRET_NAMES) - secrets):
        problems.append(f"{where}: {extra} is read although its feature is off")
    if "requests" not in found.get("resources", {}):
        problems.append(f"{where}: no resource requests")
    # The image writes nothing outside the storage volumes (L1 ran it read-only with no
    # tmpfs), so there is no /tmp volume; the check keeps one from coming back out of habit.
    if any(mount["mountPath"] == "/tmp" for mount in found.get("volumeMounts", [])):
        problems.append(f"{where}: a /tmp volume the image does not need")
    return found


def check_core(docs: list[dict]) -> list[str]:
    problems: list[str] = []
    notes = (CHART / "templates" / "NOTES.txt").read_text(encoding="utf-8")
    if re.search(r"\S {4,}\S", notes):
        problems.append("NOTES.txt: a run of spaces inside a line (a broken line continuation?)")
    deployment = one(docs, "Deployment")
    if deployment["spec"]["replicas"] != 2:
        problems.append("Deployment: the default is not 2 replicas")
    strategy = deployment["spec"]["strategy"]
    if strategy != {"type": "RollingUpdate", "rollingUpdate": {"maxUnavailable": 0, "maxSurge": 1}}:
        problems.append("Deployment: a rollout may take a pod away before its replacement is Ready")
    spec = deployment["spec"]["template"]["spec"]
    leader = check_pod(spec, "leader", problems, "Deployment", SECRET_NAMES)
    if leader["args"] != ["serve", "--host", "0.0.0.0", "--port", "8080"]:
        problems.append("Deployment: the container does not run `serve` on the port value")
    if leader["ports"][0]["containerPort"] != 8080:
        problems.append("Deployment: the container port is not the port value")
    if "command" in leader:
        problems.append("Deployment: the image's entrypoint is replaced")
    if leader["image"] != "swarmscribe-leader:local":
        problems.append("Deployment: the image is not image.repository:image.tag")
    for probe, path in (
        ("startupProbe", "/healthz"),
        ("livenessProbe", "/healthz"),
        ("readinessProbe", "/readyz"),
    ):
        if leader.get(probe, {}).get("httpGet", {}).get("path") != path:
            problems.append(f"Deployment: {probe} does not ask {path} with GET (httpGet)")
    if leader["readinessProbe"].get("timeoutSeconds", 1) < 5:
        problems.append("Deployment: the readiness timeout is under 5 s (the check takes up to 3)")
    if "/readyz" in str(leader["startupProbe"]) or "/readyz" in str(leader["livenessProbe"]):
        problems.append("Deployment: startup or liveness asks /readyz")
    stop = leader.get("lifecycle", {}).get("preStop", {}).get("exec", {}).get("command")
    if stop != ["sleep", "5"]:
        problems.append("Deployment: no preStop sleep of preStopSleepSeconds")
    if spec["terminationGracePeriodSeconds"] != 60:
        problems.append("Deployment: the grace period is not terminationGracePeriodSeconds")
    if "lifecycle" in leader_container(render("--set", "preStopSleepSeconds=0")):
        problems.append("Deployment: preStopSleepSeconds=0 still renders a preStop hook")
    recreate = one(render("--set", "updateStrategy=Recreate"), "Deployment")["spec"]["strategy"]
    if recreate != {"type": "Recreate"}:
        problems.append("Deployment: updateStrategy=Recreate is not rendered as Recreate alone")

    settings = one(docs, "ConfigMap")["data"]
    expected = {
        "SWARMSCRIBE_PUBLIC_URL": "https://leader.example.org",
        "SWARMSCRIBE_ENTRA_TENANT_ID": "0f0e0d0c-0b0a-4908-8706-050403020100",
        "SWARMSCRIBE_ENTRA_CLIENT_ID": "6d3a6b52-1c2e-4a8f-9d61-2f6a4c0b7e11",
        "SWARMSCRIBE_GOOGLE_CLIENT_ID": "leader.apps.googleusercontent.com",
        "SWARMSCRIBE_GOOGLE_HOSTED_DOMAIN": "example.org",
        "SWARMSCRIBE_ROLE_VIEWER_DOMAINS": "example.org",
        "SWARMSCRIBE_ROLE_OPERATOR_GOOGLE_GROUPS": "operators@example.org",
        "SWARMSCRIBE_ROLE_ADMIN_ENTRA_GROUPS": (
            "3f2b0c5e-8f6d-4a51-9c0e-6f1d2a7b9c11,9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"
        ),
        "SWARMSCRIBE_ROLE_ADMIN_EMAILS": "owner@example.org",
        "SWARMSCRIBE_LEASE_SECONDS": "120",
    }
    if settings != expected:
        problems.append(f"ConfigMap: the settings are {settings}, not {expected}")
    problems += check_setting_names(settings)

    selector = one(docs, "Service")["spec"]["selector"]
    labels = leader_labels(docs)
    if any(labels.get(key) != value for key, value in selector.items()):
        problems.append("Service: its selector does not match the leader pods")
    if "app.kubernetes.io/component" not in selector:
        problems.append("Service: its selector would also match the migration pod")
    if one(docs, "Service")["spec"]["ports"][0]["targetPort"] != "http":
        problems.append("Service: it does not target the named port")
    if "Secret" in kinds(docs):
        problems.append("the chart renders a Secret; it must only reference one")
    if kinds(docs) & {"ServiceMonitor", "PodMonitor", "HorizontalPodAutoscaler", "StatefulSet"}:
        problems.append("the chart renders a kind the leader has nothing for (it has no /metrics)")
    if kinds(docs) & {"PersistentVolumeClaim", "PersistentVolume"}:
        problems.append("the chart renders storage of its own; storage is the operator's")

    moved = render("--set", "port=9090")
    container = leader_container(moved)
    if container["args"][-1] != "9090" or container["ports"][0]["containerPort"] != 9090:
        problems.append("Deployment: the port value does not move the container port and --port")

    # A leader with neither optional secret: only the two every leader needs are read.
    lean = render_with(
        {
            "oidc": {
                "entra": {"clientSecret": False},
                "google": {"enabled": False, "serviceAccount": False},
            },
            "roles": {
                "viewer": {"domains": []},
                "operator": {"googleGroups": []},
                "admin": {"emails": []},
            },
        }
    )
    check_pod(pod_spec(lean), "leader", problems, "Deployment (Entra only)", ALWAYS_SECRET)
    bare = render_with(
        NO_SIGN_IN,
        "--set",
        "networkPolicy.ingress.anySource=true",
        "--set",
        "networkPolicy.ingress.from=null",
    )
    check_pod(pod_spec(bare), "leader", problems, "Deployment (no sign-in)", ALWAYS_SECRET)
    if set(one(bare, "ConfigMap")["data"]) != {
        "SWARMSCRIBE_PUBLIC_URL",
        "SWARMSCRIBE_LEASE_SECONDS",
    }:
        problems.append("ConfigMap: a leader without sign-in still carries sign-in settings")

    refused(problems, "no image repository", "image.repository=")
    refused(problems, "no image tag", "image.tag=", says="image.tag is required")
    refused(problems, "no publicUrl", "publicUrl=")
    refused(problems, "no Secret", "secrets.existingSecret=")
    refused(
        problems,
        "no sign-in provider",
        "oidc.entra.enabled=false",
        "oidc.google.enabled=false",
    )
    refused_file(
        problems,
        "no sign-in provider and no allowNone",
        {**NO_SIGN_IN, "oidc": {**NO_SIGN_IN["oidc"], "allowNone": False}},
        says="without sign-in refuses every admin call",
    )
    refused_file(
        problems,
        "sign-in on and no administrator",
        {"roles": {"admin": {"entraGroups": [], "emails": []}}},
        says="roles.admin names nobody",
    )
    refused_file(
        problems,
        "Entra groups without Entra sign-in",
        {"oidc": {"entra": {"enabled": False, "clientSecret": False}}},
        says="entraGroups needs oidc.entra.enabled",
    )
    refused_file(
        problems,
        "Google groups without the service account",
        {"oidc": {"google": {"serviceAccount": False}}},
        says="googleGroups needs oidc.google.serviceAccount",
    )
    refused_file(
        problems,
        "emails without Google sign-in",
        {
            "oidc": {"google": {"enabled": False, "serviceAccount": False}},
            "roles": {"operator": {"googleGroups": []}},
        },
        says="apply to Google sign-in",
    )
    refused_file(
        problems,
        "an Entra client secret without Entra sign-in",
        {
            "oidc": {"entra": {"enabled": False, "clientSecret": True}},
            "roles": {"admin": {"entraGroups": []}},
        },
        says="oidc.entra.clientSecret needs oidc.entra.enabled",
    )
    refused(problems, "an Entra tenant that is a domain name", "oidc.entra.tenantId=example.org")
    refused(problems, "Entra sign-in without a client id", "oidc.entra.clientId=")
    refused(problems, "Google sign-in without a client id", "oidc.google.clientId=")
    refused_file(
        problems,
        "a role entry with a comma (it would become two entries)",
        {"roles": {"admin": {"emails": ["a@example.org,b@example.org"]}}},
    )
    refused_file(problems, "an unknown role", {"roles": {"owner": {"emails": ["a@example.org"]}}})
    refused_file(problems, "an unknown role list", {"roles": {"admin": {"users": ["a"]}}})

    # Secrets and chart-owned names through `settings`: the leader reads its environment
    # case-insensitively, so every spelling is refused (and a name must be upper case).
    for what, value in (
        ("the link key under settings", "settings.LINK_KEY=abc"),
        ("a lower-case link key under settings", "settings.link_key=abc"),
        ("a lower-case database_url under settings", "settings.database_url=abc"),
        ("a mixed-case link key under settings", "settings.Link_Key=abc"),
        ("a client secret under settings", "settings.ENTRA_CLIENT_SECRET=abc"),
        ("a mixed-case *_secret under settings", "settings.Google_Client_Secret=abc"),
        ("google_service_account under settings", "settings.google_service_account=x"),
        ("public_url under settings", "settings.public_url=https://a.example.org"),
        ("a role list under settings", "settings.ROLE_ADMIN_EMAILS=a@example.org"),
        ("a lower-case role list under settings", "settings.role_admin_domains=example.org"),
        ("the Entra tenant under settings", "settings.ENTRA_TENANT_ID=x"),
        ("a lower-case setting name", "settings.lease_seconds=4"),
    ):
        refused(problems, what, value)
    refused_file(problems, "a setting name with a space", {"settings": {"A B": "4"}})
    refused_file(problems, "a setting name with a newline", {"settings": {"A\nKEY": "plain"}})
    refused_file(
        problems,
        "a setting name that injects a ConfigMap entry",
        {"settings": {"X: y\n  SWARMSCRIBE_LINK_KEY": "plain"}},
    )
    for name in (
        "SWARMSCRIBE_LINK_KEY",
        "swarmscribe_database_url",
        "Swarmscribe_Entra_Client_Secret",
        "SWARMSCRIBE_PUBLIC_URL",
        "SWARMSCRIBE_ROLE_ADMIN_EMAILS",
        "swarmscribe_google_service_account",
    ):
        refused_file(
            problems, f"{name} under extraEnv", {"extraEnv": [{"name": name, "value": "x"}]}
        )
    refused_file(
        problems,
        "a secret under extraEnv by valueFrom",
        {
            "extraEnv": [
                {"name": "SWARMSCRIBE_LINK_KEY", "valueFrom": {"fieldRef": {"fieldPath": "x"}}}
            ]
        },
    )
    proxied = render_with({"extraEnv": [{"name": "HTTPS_PROXY", "value": "http://proxy:3128"}]})
    if "HTTPS_PROXY" not in {e["name"] for e in leader_container(proxied)["env"]}:
        problems.append("Deployment: extraEnv HTTPS_PROXY is not passed on")

    for what, value in (
        ("a publicUrl with a query", "https://leader.example.org?x=1"),
        ("a publicUrl with a fragment", "https://leader.example.org#f"),
        ("a publicUrl with userinfo", "https://user@leader.example.org"),
        ("a publicUrl with a path", "https://leader.example.org/app"),
        ("a publicUrl with a trailing slash", "https://leader.example.org/"),
        ("a publicUrl with a trailing dot", "https://leader.example.org."),
        ("a publicUrl with upper-case letters", "https://Leader.Example.ORG"),
        ("a publicUrl with a space", "https://leader example.org"),
        ("an IPv4 publicUrl", "https://10.1.2.3"),
        ("an IPv6 publicUrl", "https://[::1]"),
        ("a wildcard publicUrl", "https://*.example.org"),
        ("a publicUrl with a quote", 'https://lea"der.example.org'),
        ("a publicUrl with a leading hyphen", "https://-leader.example.org"),
        ("a publicUrl without a host", "https://"),
        ("a publicUrl with another scheme", "ftp://leader.example.org"),
        ("a publicUrl with a port that is not a number", "https://leader.example.org:https"),
        ("an http publicUrl", "http://leader.example.org"),
        ("a publicUrl with a port and the Ingress on", "https://leader.example.org:8443"),
    ):
        refused_file(problems, what, {"publicUrl": value})
    refused_file(problems, "a publicUrl with a newline", {"publicUrl": "https://a.example.org\nx"})
    refused_file(problems, "a publicUrl that is not a string", {"publicUrl": 5})
    refused_file(
        problems,
        "an http publicUrl with allowHttpPublicUrl and the Ingress on",
        {"publicUrl": "http://leader.example.org", "allowHttpPublicUrl": True},
        says="https:// with the Ingress on",
    )
    # What the kind test uses: plain http to the Service's name, no Ingress.
    inside = render_with(
        {
            "publicUrl": "http://leader-swarmscribe-leader",
            "allowHttpPublicUrl": True,
            "ingress": {"enabled": False},
        }
    )
    if (
        one(inside, "ConfigMap")["data"]["SWARMSCRIBE_PUBLIC_URL"]
        != "http://leader-swarmscribe-leader"
    ):
        problems.append("ConfigMap: an allowed http publicUrl is not passed on")
    ported = render_with(
        {"publicUrl": "https://leader.example.org:8443", "ingress": {"enabled": False}}
    )
    if (
        one(ported, "ConfigMap")["data"]["SWARMSCRIBE_PUBLIC_URL"]
        != "https://leader.example.org:8443"
    ):
        problems.append("ConfigMap: a publicUrl with a port is not passed on with the Ingress off")

    for what, value in (
        ("port 0", "port=0"),
        ("a negative port", "port=-1"),
        ("port 70000", "port=70000"),
        ("a text port", "port=abc"),
        ("a fractional port", "port=8080.5"),
        ("service.port 0", "service.port=0"),
        ("service.port 70000", "service.port=70000"),
        ("no replicas", "replicaCount=0"),
        ("negative replicas", "replicaCount=-1"),
        ("text replicas", "replicaCount=two"),
        ("fractional replicas", "replicaCount=2.5"),
        ("an unknown pullPolicy", "image.pullPolicy=Sometimes"),
        ("an unknown service type", "service.type=ExternalName"),
        ("a grace period of 0", "terminationGracePeriodSeconds=0"),
        ("a grace period no longer than the preStop sleep", "terminationGracePeriodSeconds=5"),
        ("a negative preStop sleep", "preStopSleepSeconds=-1"),
        ("an unknown update strategy", "updateStrategy=OnDelete"),
        ("a malformed digest", "image.digest=notadigest"),
        ("a bad fullnameOverride", "fullnameOverride=Not_Valid"),
        ("a Postgres port of 0", "networkPolicy.egress.postgres.port=0"),
    ):
        refused(problems, what, value)

    pinned = leader_container(render("--set", f"image.digest={DIGEST}"))["image"]
    if pinned != f"swarmscribe-leader@{DIGEST}":
        problems.append("Deployment: image.digest does not win over the tag")

    problems += check_names()
    problems += check_quoting()
    return problems


def check_setting_names(settings: dict) -> list[str]:
    """Every name the chart writes must be a setting the leader really has (config.py): the
    leader ignores unknown variables, so a misspelt one would be silently without effect."""
    source = (LEADER / "config.py").read_text(encoding="utf-8")
    fields = set(re.findall(r"^    ([a-z_]+): ", source, flags=re.MULTILINE))
    fields |= {
        f"role_{role}_{kind}"
        for role in ("viewer", "operator", "admin")
        for kind in ("entra_groups", "google_groups", "emails", "domains")
    }
    problems = []
    for name in sorted({*settings, *SECRET_NAMES}):
        if name.removeprefix("SWARMSCRIBE_").lower() not in fields:
            problems.append(f"{name} is not a setting of the leader (config.py)")
    return problems


def label_values(node: object):
    """Every string under a `labels` or `matchLabels` map, wherever it sits in a manifest."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("labels", "matchLabels") and isinstance(value, dict):
                yield from (v for v in value.values() if isinstance(v, str))
            else:
                yield from label_values(value)
    elif isinstance(node, list):
        for item in node:
            yield from label_values(item)


def check_names() -> list[str]:
    """Long and dotted release names: every object name and label value stays a valid one."""
    problems: list[str] = []
    cases = (
        ("r" * 53, ()),
        ("a.b", ()),
        ("leader", ("--set", "fullnameOverride=" + "f" * 63)),
        ("leader", ("--set", "nameOverride=" + "n" * 63)),
        # Cut to 55 characters this would end in a hyphen, which no name may.
        ("leader", ("--set", "fullnameOverride=" + "f" * 54 + "-" + "f" * 8)),
    )
    for release, extra in cases:
        done = helm_template(*extra, release=release)
        if done.returncode != 0:
            problems.append(f"release {release!r} {extra}: does not render: {done.stderr[-200:]}")
            continue
        for doc in (d for d in yaml.safe_load_all(done.stdout) if d):
            name = doc["metadata"]["name"]
            where = f"release {release[:12]}... {doc['kind']} {name[:20]}..."
            if len(name) > 63 or not NAME_PATTERN.fullmatch(name):
                problems.append(f"{where}: not a valid name of at most 63 characters")
            for value in label_values(doc):
                if len(value) > 63:
                    problems.append(f"{where}: a label value longer than 63 characters")
    return problems


def check_quoting() -> list[str]:
    """Values that YAML would read as something else arrive as the strings they are."""
    problems: list[str] = []
    odd = render_with(
        {
            "settings": {"LEASE_SECONDS": 120, "CLAIM_RETRY_AFTER": "010"},
            "secrets": {"existingSecret": "123", "keys": {"linkKey": "yes"}},
            "storage": {
                "volumes": [{"name": "123", "mountPath": "/data/1e3", "existingClaim": "123"}]
            },
        }
    )
    data = one(odd, "ConfigMap")["data"]
    if data.get("SWARMSCRIBE_LEASE_SECONDS") != "120":
        problems.append("ConfigMap: a number under settings is not a string")
    if data.get("SWARMSCRIBE_CLAIM_RETRY_AFTER") != "010":
        problems.append("ConfigMap: a string with a leading zero lost it")
    env = {e["name"]: e for e in leader_container(odd)["env"]}
    ref = env["SWARMSCRIBE_LINK_KEY"]["valueFrom"]["secretKeyRef"]
    if ref != {"name": "123", "key": "yes"}:
        problems.append(f"Deployment: the Secret's name and key are not strings ({ref})")
    volume = pod_spec(odd)["volumes"][0]
    if volume != {"name": "storage-123", "persistentVolumeClaim": {"claimName": "123"}}:
        problems.append(f"Deployment: a numeric volume or claim name is not a string ({volume})")
    return problems


def check_storage(docs: list[dict]) -> list[str]:
    problems: list[str] = []
    spec = pod_spec(docs)
    pod = spec["securityContext"]
    if pod.get("fsGroup") != 10001 or pod.get("fsGroupChangePolicy") != "OnRootMismatch":
        problems.append("Deployment: the storage volumes are not opened with group 10001")
    if pod.get("supplementalGroups") != [2000]:
        problems.append("Deployment: storage.supplementalGroups is not passed on")
    if spec["volumes"] != [
        {"name": "storage-recordings", "persistentVolumeClaim": {"claimName": "recordings"}},
        {"name": "storage-archive", "nfs": {"server": "nas.internal", "path": "/exports/archive"}},
    ]:
        problems.append(f"Deployment: the volumes are not storage.volumes ({spec['volumes']})")
    if leader_container(docs)["volumeMounts"] != [
        {"name": "storage-recordings", "mountPath": "/data/recordings"},
        {"name": "storage-archive", "mountPath": "/data/archive", "readOnly": True},
    ]:
        problems.append("Deployment: the mounts are not storage.volumes")
    read_only = render_with(
        {
            "storage": {
                "volumes": [
                    {"name": "a", "mountPath": "/data", "existingClaim": "a", "readOnly": True}
                ]
            }
        }
    )
    if pod_spec(read_only)["volumes"][0]["persistentVolumeClaim"].get("readOnly") is not True:
        problems.append("Deployment: a read-only claim is not mounted read-only")

    def volumes(*entries: dict) -> dict:
        return {"storage": {"volumes": list(entries)}}

    claim = {"name": "a", "mountPath": "/data/a", "existingClaim": "a"}
    refused(
        problems, "no storage volume", "storage.volumes=null", says="storage.volumes is required"
    )
    refused_file(
        problems, "an empty list of volumes", volumes(), says="storage.volumes is required"
    )
    refused_file(
        problems,
        "a volume with neither a claim nor a source",
        volumes({"name": "a", "mountPath": "/data/a"}),
        says="exactly one of",
    )
    refused_file(
        problems,
        "a volume with a claim and a source",
        volumes({**claim, "volume": {"nfs": {"server": "s", "path": "/p"}}}),
        says="exactly one of",
    )
    refused_file(
        problems,
        "an emptyDir volume",
        volumes({"name": "a", "mountPath": "/data/a", "volume": {"emptyDir": {}}}),
        says="is an emptyDir",
    )
    refused_file(
        problems,
        "a volume source with two kinds",
        volumes(
            {
                "name": "a",
                "mountPath": "/data/a",
                "volume": {"nfs": {"server": "s", "path": "/p"}, "hostPath": {"path": "/x"}},
            }
        ),
    )
    for what, path in (
        ("a relative mountPath", "data/a"),
        ("a mountPath of /", "/"),
        ("a mountPath of /app", "/app"),
        ("a mountPath under /app", "/app/data"),
        ("a mountPath with ..", "/data/../app"),
        ("a mountPath with a trailing slash", "/data/a/"),
        ("a mountPath with a doubled slash", "/data//a"),
        ("a mountPath with a space", "/data/my files"),
        ("a mountPath with a colon", "/data/a:b"),
    ):
        refused_file(problems, what, volumes({**claim, "mountPath": path}))
    refused_file(
        problems, "a volume name that is not a DNS label", volumes({**claim, "name": "A_b"})
    )
    refused_file(
        problems, "a volume without a name", volumes({"mountPath": "/d", "existingClaim": "a"})
    )
    refused_file(problems, "an unknown key on a volume", volumes({**claim, "subPath": "x"}))
    refused_file(
        problems,
        "two volumes with one name",
        volumes(claim, {**claim, "mountPath": "/data/b"}),
        says="used twice",
    )
    refused_file(
        problems,
        "two volumes on one mountPath",
        volumes(claim, {**claim, "name": "b"}),
        says="used twice",
    )
    refused(problems, "an fsGroup of 0 (root's group)", "storage.fsGroup=0")
    refused_file(problems, "a supplemental group of 0", {"storage": {"supplementalGroups": [0]}})
    return problems


def check_migrate(docs: list[dict]) -> list[str]:
    problems: list[str] = []
    job = one(docs, "Job", f"{NAME}-migrate")
    hooks = job["metadata"]["annotations"]
    if hooks.get("helm.sh/hook") != "pre-install,pre-upgrade":
        problems.append("Job: it is not a pre-install and pre-upgrade hook")
    policy = hooks.get("helm.sh/hook-delete-policy", "")
    if "before-hook-creation" not in policy or "hook-succeeded" not in policy:
        problems.append("Job: an old or succeeded Job is not removed")
    if "hook-failed" in policy:
        problems.append("Job: a failed Job is removed, and its log with it")
    spec = job["spec"]["template"]["spec"]
    migrate = check_pod(spec, "migrate", problems, "Job", SECRET_NAMES)
    if migrate["args"] != ["migrate"]:
        problems.append("Job: the container does not run `migrate`")
    if migrate["image"] != leader_container(docs)["image"]:
        problems.append("Job: it does not run the leader's own image")
    if spec.get("restartPolicy") != "Never":
        problems.append("Job: restartPolicy is not Never")
    # A hook runs before the release's own ConfigMap and ServiceAccount exist.
    if "envFrom" in migrate:
        problems.append("Job: it reads the ConfigMap, which does not exist on a first install")
    if spec.get("serviceAccountName") not in (None, "default"):
        problems.append("Job: it uses the chart's ServiceAccount, which does not exist yet")
    if spec.get("volumes") or migrate.get("volumeMounts"):
        problems.append("Job: it mounts a volume (a migration touches the database only)")
    if "fsGroup" in spec.get("securityContext", {}):
        problems.append("Job: an fsGroup with no volume")
    inline = {e["name"]: e.get("value") for e in migrate["env"] if "value" in e}
    if inline != one(docs, "ConfigMap")["data"]:
        problems.append("Job: its inline settings differ from the ConfigMap's")
    labels = job["spec"]["template"]["metadata"]["labels"]
    if labels.get("app.kubernetes.io/component") != "migrate":
        problems.append("Job: its pod is not labelled component=migrate")
    service = one(docs, "Service")["spec"]["selector"]
    if all(labels.get(key) == value for key, value in service.items()):
        problems.append("Job: the Service would send requests to the migration pod")
    if job["spec"].get("backoffLimit") != 3 or job["spec"].get("activeDeadlineSeconds") != 300:
        problems.append("Job: backoffLimit or activeDeadlineSeconds is not the value")
    if "Job" in kinds(render("--set", "migrate.enabled=false")):
        problems.append("a disabled migration is still rendered")
    return problems


def routes(entry: dict, request_path: str) -> bool:
    """Whether an Ingress path entry matches a request path (Exact, or Prefix by element)."""
    path = entry["path"]
    if entry["pathType"] == "Exact":
        return request_path == path
    if path == "/":
        return True
    prefix = path.rstrip("/")
    return request_path == prefix or request_path.startswith(prefix + "/")


def served_prefixes() -> set[str]:
    """The prefixes of the leader's routers (api/*.py), other than the probes'."""
    found = set()
    for source in sorted((LEADER / "api").glob("*.py")):
        found |= set(re.findall(r'APIRouter\(prefix="([^"]+)"', source.read_text(encoding="utf-8")))
    return found


def check_ingress(docs: list[dict]) -> list[str]:
    problems: list[str] = []
    ingress = one(docs, "Ingress")
    spec = ingress["spec"]
    if spec["tls"] != [{"hosts": ["leader.example.org"], "secretName": "leader-tls"}]:
        problems.append("Ingress: TLS is not the public URL's host with the named Secret")
    rule = spec["rules"][0]
    if rule["host"] != "leader.example.org":
        problems.append("Ingress: the host is not the public URL's")
    if spec.get("ingressClassName") != "traefik":
        problems.append("Ingress: className is not passed on")
    entries = rule["http"]["paths"]
    if [(e["path"], e["pathType"]) for e in entries] != [("/v1", "Prefix")]:
        problems.append("Ingress: the default paths are not the one Prefix /v1")
    for entry in entries:
        if entry["backend"]["service"] != {"name": NAME, "port": {"name": "http"}}:
            problems.append("Ingress: a path does not go to the leader's Service")
    for probe in ("/healthz", "/healthz/", "/readyz", "/readyz/", "/"):
        if any(routes(entry, probe) for entry in entries):
            problems.append(f"Ingress: {probe} is published")
    # Every router the leader has must be reachable: a route outside /v1 would be cut off.
    prefixes = served_prefixes()
    if not prefixes:
        problems.append("no router prefix found in the leader's api package (did it move?)")
    for prefix in sorted(prefixes):
        if not any(routes(entry, prefix + "/x") for entry in entries):
            problems.append(f"Ingress: the leader serves {prefix}, which no path reaches")
    for what, path, kind in (
        ("a Prefix / path", "/", "Prefix"),
        ("an Exact / path", "/", "Exact"),
        ("an ImplementationSpecific / path", "/", "ImplementationSpecific"),
        ("the readiness probe as a path", "/readyz", "Exact"),
        ("the liveness probe as a path", "/healthz", "Prefix"),
        ("an unknown pathType", "/v1", "Loose"),
        ("a path without a leading slash", "v1", "Prefix"),
    ):
        refused_file(problems, what, {"ingress": {"paths": [{"path": path, "pathType": kind}]}})
    refused_file(problems, "no ingress path at all", {"ingress": {"paths": []}})
    refused(problems, "an Ingress without TLS", "ingress.tls.secretName=", says="TLS only")
    split = render_with(
        {
            "ingress": {
                "paths": [
                    {"path": "/v1/followers", "pathType": "Prefix"},
                    {"path": "/v1/jobs", "pathType": "Prefix"},
                    {"path": "/v1/files", "pathType": "Prefix"},
                ],
                "annotations": {"example.org/limit": "10"},
            }
        }
    )
    narrowed = one(split, "Ingress")
    if len(narrowed["spec"]["rules"][0]["http"]["paths"]) != 3:
        problems.append("Ingress: ingress.paths is not what is rendered")
    if narrowed["metadata"].get("annotations") != {"example.org/limit": "10"}:
        problems.append("Ingress: annotations are not passed on")
    odd = render_with({"ingress": {"className": "true", "tls": {"secretName": "12345"}}})
    quoted = one(odd, "Ingress")["spec"]
    if quoted.get("ingressClassName") != "true" or quoted["tls"][0]["secretName"] != "12345":
        problems.append("Ingress: className or the TLS secret name is not a string")
    if "Ingress" in kinds(render("--set", "ingress.enabled=false")):
        problems.append("a disabled Ingress is still rendered")
    return problems


def cut_out(address: str, rule: dict) -> bool:
    """Whether `address` is unreachable through an egress rule's ipBlock peers."""
    target = ipaddress.ip_address(address)
    for peer in rule["to"]:
        block = peer["ipBlock"]
        network = ipaddress.ip_network(block["cidr"])
        if target.version == network.version and target in network:
            excepted = (ipaddress.ip_network(cut) for cut in block.get("except", []))
            if not any(target in cut for cut in excepted):
                return False
    return True


def https_rules(policy: dict) -> list[dict]:
    return [
        r
        for r in policy["egress"]
        if any("ipBlock" in p for p in r.get("to", []))
        and {"protocol": "TCP", "port": 443} in r["ports"]
    ]


def check_network(docs: list[dict]) -> list[str]:
    problems: list[str] = []
    budget = one(docs, "PodDisruptionBudget")
    if budget["spec"].get("maxUnavailable") != 1:
        problems.append("PodDisruptionBudget: not maxUnavailable 1")
    labels = leader_labels(docs)
    if any(labels.get(k) != v for k, v in budget["spec"]["selector"]["matchLabels"].items()):
        problems.append("PodDisruptionBudget: does not select the leader pods")
    if budget["spec"].get("unhealthyPodEvictionPolicy") != "AlwaysAllow":
        problems.append("PodDisruptionBudget: unready pods are not evictable (AlwaysAllow)")
    spread = pod_spec(docs).get("topologySpreadConstraints", [])
    if [(c["topologyKey"], c["whenUnsatisfiable"]) for c in spread] != [
        ("kubernetes.io/hostname", "ScheduleAnyway")
    ]:
        problems.append("Deployment: no soft spread across nodes by default with 2 replicas")
    elif any(labels.get(k) != v for k, v in spread[0]["labelSelector"]["matchLabels"].items()):
        problems.append("Deployment: the default spread does not select the leader pods")
    if "topologySpreadConstraints" in pod_spec(render("--set", "replicaCount=1")):
        problems.append("Deployment: a spread constraint for one replica")
    if "topologySpreadConstraints" in pod_spec(render("--set", "spreadAcrossNodes=false")):
        problems.append("Deployment: spreadAcrossNodes=false still spreads")
    if "PodDisruptionBudget" in kinds(render("--set", "replicaCount=1")):
        problems.append("a PodDisruptionBudget is rendered for one replica (it blocks drains)")

    policy = one(docs, "NetworkPolicy")["spec"]
    if sorted(policy["policyTypes"]) != ["Egress", "Ingress"]:
        problems.append("NetworkPolicy: does not cover both directions")
    selector = policy["podSelector"]["matchLabels"]
    if any(labels.get(k) != v for k, v in selector.items()):
        problems.append("NetworkPolicy: does not select the leader pods")
    if "app.kubernetes.io/component" in selector:
        problems.append("NetworkPolicy: leaves the migration pod out")

    (inbound,) = policy["ingress"]
    if inbound["ports"] != [{"protocol": "TCP", "port": 8080}]:
        problems.append("NetworkPolicy: ingress is not limited to the leader's port")
    if len(inbound.get("from", [])) != 2:
        problems.append("NetworkPolicy: ingress.from is not the two peers of the test values")
    moved = one(render("--set", "port=9090"), "NetworkPolicy")["spec"]["ingress"][0]["ports"]
    if moved != [{"protocol": "TCP", "port": 9090}]:
        problems.append("NetworkPolicy: ingress does not follow the port value")
    refused(
        problems,
        "a NetworkPolicy that lets nobody in",
        "networkPolicy.ingress.from=null",
        says="networkPolicy.ingress.from is required",
    )
    refused(
        problems,
        "ingress.from together with anySource",
        "networkPolicy.ingress.anySource=true",
        says="choose one",
    )
    anyone = render(
        "--set", "networkPolicy.ingress.from=null", "--set", "networkPolicy.ingress.anySource=true"
    )
    (open_rule,) = one(anyone, "NetworkPolicy")["spec"]["ingress"]
    if "from" in open_rule or open_rule["ports"] != [{"protocol": "TCP", "port": 8080}]:
        problems.append("NetworkPolicy: anySource is not 'any source, on the leader's port only'")

    found = https_rules(policy)
    if len(found) != 1:
        raise SystemExit(f"expected one https egress rule, found {len(found)}")
    rule = found[0]
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
    ports = [port for rule in policy["egress"] for port in rule["ports"]]
    if {"protocol": "UDP", "port": 53} not in ports or {"protocol": "TCP", "port": 53} not in ports:
        problems.append("NetworkPolicy: DNS is not allowed")
    if {"protocol": "TCP", "port": 5432} not in ports:
        problems.append("NetworkPolicy: Postgres is not allowed")
    if len(policy["egress"]) != 3:
        problems.append("NetworkPolicy: more egress than DNS, Postgres and the identity providers")

    # A leader without sign-in calls nobody but DNS and Postgres.
    quiet = render_with(NO_SIGN_IN)
    quiet_policy = one(quiet, "NetworkPolicy")["spec"]
    if https_rules(quiet_policy) or len(quiet_policy["egress"]) != 2:
        problems.append("NetworkPolicy: a leader without sign-in may still reach the internet")
    # The API server refuses an `except` outside its `cidr`: a narrowed range gets none.
    narrowed = render("--set", "networkPolicy.egress.https.cidrs={10.0.0.0/8}")
    for peer in https_rules(one(narrowed, "NetworkPolicy")["spec"])[0]["to"]:
        if "except" in peer["ipBlock"]:
            problems.append("NetworkPolicy: a narrowed range carries the catch-all's except list")
    extra = render_with(
        {
            "networkPolicy": {
                "egress": {
                    "extra": [
                        {
                            "to": [{"ipBlock": {"cidr": "10.9.9.9/32"}}],
                            "ports": [{"protocol": "TCP", "port": 3128}],
                        }
                    ]
                }
            }
        }
    )
    extra_ports = [p for r in one(extra, "NetworkPolicy")["spec"]["egress"] for p in r["ports"]]
    if {"protocol": "TCP", "port": 3128} not in extra_ports:
        problems.append("NetworkPolicy: egress.extra is not passed on")
    refused(
        problems,
        "a NetworkPolicy without Postgres",
        "networkPolicy.egress.postgres.peers=null",
        says="postgres.peers is required",
    )
    if "NetworkPolicy" in kinds(render("--set", "networkPolicy.enabled=false")):
        problems.append("a disabled NetworkPolicy is still rendered")
    # With the policy off, neither of its two required values is asked for.
    off = helm_template(
        "--set",
        "networkPolicy.enabled=false",
        "--set",
        "networkPolicy.ingress.from=null",
        "--set",
        "networkPolicy.egress.postgres.peers=null",
    )
    if off.returncode != 0:
        problems.append("a disabled NetworkPolicy still demands its peers")
    return problems


SECTIONS = {
    "core": check_core,
    "storage": check_storage,
    "migrate": check_migrate,
    "ingress": check_ingress,
    "network": check_network,
}


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
```

Create `deploy/helm/swarmscribe-leader/ci/test-values.yaml`:

```yaml
# Values the chart is linted and rendered with, locally and in CI. Nothing here is a real secret.
publicUrl: https://leader.example.org
secrets:
  existingSecret: swarmscribe-leader
image:
  repository: swarmscribe-leader
  tag: local
oidc:
  entra:
    enabled: true
    tenantId: 0f0e0d0c-0b0a-4908-8706-050403020100
    clientId: 6d3a6b52-1c2e-4a8f-9d61-2f6a4c0b7e11
    clientSecret: true
  google:
    enabled: true
    clientId: leader.apps.googleusercontent.com
    hostedDomain: example.org
    serviceAccount: true
roles:
  viewer:
    domains: [example.org]
  operator:
    googleGroups: [operators@example.org]
  admin:
    entraGroups: [3f2b0c5e-8f6d-4a51-9c0e-6f1d2a7b9c11, 9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d]
    emails: [owner@example.org]
settings:
  LEASE_SECONDS: "120"
storage:
  volumes:
    - name: recordings
      mountPath: /data/recordings
      existingClaim: recordings
    - name: archive
      mountPath: /data/archive
      readOnly: true
      volume:
        nfs:
          server: nas.internal
          path: /exports/archive
  supplementalGroups: [2000]
ingress:
  className: traefik
  tls:
    secretName: leader-tls
networkPolicy:
  ingress:
    from:
      - namespaceSelector:
          matchLabels:
            kubernetes.io/metadata.name: traefik
      - podSelector:
          matchLabels:
            app.kubernetes.io/name: swarmscribe-follower
  egress:
    postgres:
      peers:
        - ipBlock:
            cidr: 10.20.30.40/32
    https:
      extraExcept: ["10.96.0.0/12", "fd00:10:96::/112"]
```

- [ ] **Step 2: Run the check to see it fail**

Run: `uv run --no-project --with pyyaml python deploy/helm/swarmscribe-leader/ci/check_render.py --only core,storage`

Expected: exit status 1 with `helm template failed:` and Helm's own message that `Chart.yaml` is missing from the chart's folder.

- [ ] **Step 3: The chart's metadata**

Create `deploy/helm/swarmscribe-leader/Chart.yaml`:

```yaml
apiVersion: v2
name: swarmscribe-leader
description: The SwarmScribe leader - catalogue, consent, jobs, and the follower and admin APIs
type: application
version: 0.1.0
appVersion: "0.1.0"
kubeVersion: ">=1.27.0-0"
```

Create `deploy/helm/swarmscribe-leader/.helmignore`:

```
# Not part of the packaged chart: the values and the check that CI renders it with.
ci/
```

- [ ] **Step 4: The values, documented**

Create `deploy/helm/swarmscribe-leader/values.yaml`:

```yaml
# SwarmScribe leader. What you must set: `image`, `publicUrl`, `secrets.existingSecret`,
# one of `oidc.entra` / `oidc.google` with an administrator under `roles.admin`, at least
# one entry in `storage.volumes`, and with the NetworkPolicy on (the default)
# `networkPolicy.egress.postgres.peers` and `networkPolicy.ingress.from`.

# Names. fullnameOverride replaces <release>-swarmscribe-leader as the base of every object
# name; either way the base is cut to 55 characters, so that -migrate still fits the 63 a
# Service name or a label value may have.
nameOverride: ""
fullnameOverride: ""

# Leader replicas (at least 1). Replicas hold no state of their own: jobs, leases and
# credentials are in Postgres, the reaper and the scanner run in one replica at a time
# (advisory locks), and any replica serves any file link. Two conditions come with more
# than one: every replica must see the same files (storage.volumes, below), and Postgres
# must have room for 15 connections per replica (SQLAlchemy's default pool: 5, and 10 of
# overflow) plus one for the migration job.
replicaCount: 2

# The image is not published: there is no working default. Build it and load it into the
# cluster (README, "Deploy the leader"), then name it here. Both are required.
image:
  repository: ""
  tag: ""
  # "sha256:..." pins the image by digest and wins over the tag.
  digest: ""
  pullPolicy: IfNotPresent
imagePullSecrets: []

# SWARMSCRIBE_PUBLIC_URL: the one address followers, administrators and consoles reach
# this leader at, e.g. https://leader.example.org. The leader builds every file link from
# it, so pods in this cluster and machines outside it must both be able to reach it.
# Exactly https://<lowercase DNS hostname>, with an optional :port when the Ingress is off;
# no path, query, fragment, user, uppercase or IP address. It is also the Ingress host.
publicUrl: ""
# true accepts an http:// publicUrl: a test cluster only. Credentials, pool tokens and
# signed file links then cross the network in the clear. Never with the Ingress on.
allowHttpPublicUrl: false

# The chart never creates a Secret: values files and Helm's release history are not a
# place for the link key or a database password. Create one Secret yourself and name it.
secrets:
  existingSecret: ""
  keys:
    # SWARMSCRIBE_DATABASE_URL: the leader's own Postgres,
    # e.g. postgresql://leader:...@db.internal:5432/swarmscribe
    databaseUrl: database-url
    # SWARMSCRIBE_LINK_KEY: at least 32 random characters
    # (python -c "import secrets; print(secrets.token_urlsafe(48))"). It signs every file
    # link; changing it makes the links already handed out worthless (followers ask again).
    linkKey: link-key
    # Read only when oidc.entra.clientSecret.
    entraClientSecret: entra-client-secret
    # Read only when oidc.google.enabled.
    googleClientSecret: google-client-secret
    # Read only when oidc.google.serviceAccount: the service account's JSON key.
    googleServiceAccount: google-service-account

# Administrators' sign-in. Enable Entra ID, Google, or both. A leader without sign-in
# refuses every admin call (401): nobody could create a pool token or a console credential.
oidc:
  # true renders a leader with no sign-in at all. For tests only.
  allowNone: false
  entra:
    enabled: false
    # The tenant's GUID (not its domain name).
    tenantId: ""
    clientId: ""
    # true reads the application's client secret from the Secret: needed only to look up
    # people in too many groups for the token (Microsoft Graph, GroupMember.Read.All).
    clientSecret: false
  google:
    enabled: false
    clientId: ""
    # Optional: only this Google Workspace domain may sign in.
    hostedDomain: ""
    # true reads the Google Groups service-account key from the Secret.
    serviceAccount: false

# Who gets which role (README, "Administrators: sign-in and roles"). Roles are cumulative.
# entraGroups: group object ids (needs oidc.entra). googleGroups: group emails (needs
# oidc.google.serviceAccount). emails, domains: Google sign-ins only (needs oidc.google).
# With sign-in on, roles.admin must name someone: the first administrator is whoever these
# lists say, and nothing else creates one.
roles:
  viewer:
    entraGroups: []
    googleGroups: []
    emails: []
    domains: []
  operator:
    entraGroups: []
    googleGroups: []
    emails: []
    domains: []
  admin:
    entraGroups: []
    googleGroups: []
    emails: []
    domains: []

# Other non-secret settings, without the SWARMSCRIBE_ prefix, for example
#   LEASE_SECONDS: "120"
#   HEARTBEAT_SECONDS: "30"
#   SCANNER_INTERVAL_SECONDS: "30"
# Names are upper case (^[A-Z][A-Z0-9_]*$). The render refuses a secret (DATABASE_URL,
# LINK_KEY, *_SECRET, GOOGLE_SERVICE_ACCOUNT, in any letter case) and a name the chart sets
# itself (PUBLIC_URL, the oidc ones and every ROLE_*).
settings: {}

# Extra environment for the leader container, as a list of {name, value|valueFrom}, for
# example HTTPS_PROXY for identity-provider calls. A SWARMSCRIBE_ name that the chart owns
# or reads from the Secret is refused, in any letter case.
extraEnv: []

# Where recordings and transcripts live. Today the leader's only storage backend is a
# folder on a filesystem (`backend: local`); cloud object storage is not built. Each entry
# mounts one volume you provide into every leader pod; a location's root
# (`swarmscribe-admin locations add NAME --root /data/recordings`) is a folder inside one.
#   - name: recordings              a DNS label, unique
#     mountPath: /data/recordings   absolute, unique, never / or under /app
#     existingClaim: recordings     a PersistentVolumeClaim you created ...
#     # volume: {nfs: {server: nas.internal, path: /exports/recordings}}   ... or a volume source
#     readOnly: false               true: recordings can be read, transcripts cannot be written
# EVERY REPLICA MUST SEE THE SAME FILES. With pods on more than one node that means a
# ReadWriteMany claim (NFS, CephFS, Azure Files, Filestore, ...). A ReadWriteOnce claim
# works only while every leader pod is on one node, and a rollout whose new pod lands on
# another node hangs on "Multi-Attach error" until that pod is deleted; with such a claim
# on a cluster of several nodes set replicaCount: 1 and updateStrategy: Recreate, and
# accept that every upgrade is an outage of the leader. The chart cannot see a claim's
# access mode. An emptyDir is refused: it is one folder per pod.
storage:
  volumes: []
  # The group the volumes are opened with (fsGroup); files the leader writes belong to
  # user 10001 and this group. On NFS and other volumes that ignore fsGroup, make the
  # export writable by this group or by uid 10001 yourself.
  fsGroup: 10001
  # More groups for the leader's process, to read recordings another system wrote.
  supplementalGroups: []

# The port the leader listens on in the pod (the Service and NetworkPolicy follow it).
port: 8080

service:
  type: ClusterIP
  port: 80

ingress:
  enabled: true
  className: ""
  # The leader's file links carry a signed token in the URL path (/v1/files/<token>).
  # Turn request logging off for this Ingress at your controller, or its access log holds
  # tokens that work until they expire (30 minutes for a download, 2 hours for an upload,
  # and an upload only while its job's lease is current). Uploads are up to 512 MiB:
  # raise your controller's body limit and turn request buffering off. Both are your
  # controller's own annotations (README, "Deploy the leader").
  annotations: {}
  tls:
    # Required when the Ingress is enabled: the leader is published over TLS only.
    secretName: ""
  # The paths sent to the leader. Everything it serves to the outside is under /v1
  # (/v1/followers, /v1/jobs, /v1/files, /v1/admin). Never "/" and never the probes:
  # /readyz tells an anonymous caller whether the database is up.
  paths:
    - {path: /v1, pathType: Prefix}

# The schema migration: a Job run as a pre-install and pre-upgrade hook, before any leader
# pod of the new version starts. The old pods keep serving while and after it runs.
migrate:
  enabled: true
  backoffLimit: 3
  activeDeadlineSeconds: 300
  resources:
    requests:
      cpu: 50m
      memory: 128Mi
    limits:
      memory: 256Mi

resources:
  requests:
    cpu: 100m
    memory: 256Mi
  limits:
    memory: 512Mi

# RollingUpdate replaces the pods one at a time with no pod missing (maxUnavailable 0,
# maxSurge 1). Recreate stops every pod before starting the new ones: an outage on every
# upgrade, and the only choice for a ReadWriteOnce volume on a cluster of several nodes.
updateStrategy: RollingUpdate

# A stopping pod waits this long before the leader is told to stop, so that Services and
# ingress controllers stop sending it new requests first. Then the leader finishes the
# requests in hand. terminationGracePeriodSeconds must be longer than this.
preStopSleepSeconds: 5
terminationGracePeriodSeconds: 60

podDisruptionBudget:
  # Rendered only when replicaCount is above 1. Unready pods stay evictable
  # (unhealthyPodEvictionPolicy: AlwaysAllow), so a database outage cannot stall a drain.
  enabled: true
  maxUnavailable: 1

networkPolicy:
  # What a NetworkPolicy cannot do:
  #  - It needs a network plugin that enforces it (Calico, Cilium, kindnet, ...). Without
  #    one this object is accepted and does nothing.
  #  - It cannot match names: it allows by address and port only.
  #  - On a first install the migration hook runs before this policy exists.
  # It selects every pod of the release (leader and migration Job).
  enabled: true
  ingress:
    # Required with the policy on: who may reach the leader's port (NetworkPolicyPeer
    # objects). Your ingress controller, the follower pools in this cluster that use the
    # Service directly, a console in this cluster, for example
    #   - namespaceSelector: {matchLabels: {kubernetes.io/metadata.name: ingress-nginx}}
    #   - podSelector: {matchLabels: {app.kubernetes.io/name: swarmscribe-follower}}
    # Peers are matched on the address a packet arrives with: a follower that comes through
    # the Ingress arrives as the ingress controller. The kubelet's probes come from the
    # pod's own node, which most network plugins never cut off; where they are cut off,
    # add the nodes' range here.
    from: []
    # true allows any source instead (the leader authenticates every caller itself).
    anySource: false
  egress:
    dns:
      # Where the pods resolve names. Add an ipBlock for NodeLocal DNSCache
      # (169.254.20.10/32), which the link-local block below would otherwise cut off.
      peers:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
          podSelector:
            matchLabels:
              k8s-app: kube-dns
    postgres:
      port: 5432
      # Required: where the leader's Postgres is (NetworkPolicyPeer objects), e.g.
      #   - ipBlock: {cidr: 10.20.30.40/32}
      # Peers are matched AFTER Service address translation. For a Postgres inside the
      # cluster use a podSelector (with a namespaceSelector for another namespace) and the
      # pod's own port: never the Service's ClusterIP. The migration hook runs under the
      # PREVIOUS release's policy.
      peers: []
    https:
      # The identity providers (Entra ID and Microsoft Graph, Google): rendered only when
      # sign-in is on. NetworkPolicy cannot name a DNS host, so the default is "anywhere on
      # these ports except the ranges no identity provider lives in".
      ports: [443]
      cidrs: ["0.0.0.0/0", "::/0"]
      # More ranges to cut out of 0.0.0.0/0 and ::/0, for example the cluster's own pod
      # and service ranges.
      extraExcept: []
    # Extra raw egress rules (NetworkPolicyEgressRule objects), e.g. an HTTPS proxy.
    extra: []

serviceAccount:
  create: true
  name: ""

podAnnotations: {}
podLabels: {}
nodeSelector: {}
tolerations: []
affinity: {}
# With two or more replicas and no topologySpreadConstraints of your own, the pods are
# spread across nodes softly (ScheduleAnyway, so one node still schedules them all). Your
# own topologySpreadConstraints replace this; false turns it off.
spreadAcrossNodes: true
topologySpreadConstraints: []
```

- [ ] **Step 5: The schema**

Create `deploy/helm/swarmscribe-leader/values.schema.json`. Helm's schema library uses Go regular expressions: no look-ahead. What a pattern cannot say (a mount under `/app`, exactly one of two keys) is said in `_helpers.tpl`.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "properties": {
    "nameOverride": {"type": "string", "pattern": "^([a-z0-9]([-a-z0-9]*[a-z0-9])?)?$", "maxLength": 63},
    "fullnameOverride": {"type": "string", "pattern": "^([a-z0-9]([-a-z0-9]*[a-z0-9])?)?$"},
    "replicaCount": {"type": "integer", "minimum": 1},
    "image": {
      "type": "object",
      "properties": {
        "repository": {"type": "string", "minLength": 1},
        "tag": {"type": "string"},
        "digest": {"type": "string", "pattern": "^(sha256:[a-f0-9]{64})?$"},
        "pullPolicy": {"enum": ["Always", "IfNotPresent", "Never"]}
      }
    },
    "imagePullSecrets": {"type": "array"},
    "publicUrl": {"type": "string", "minLength": 1},
    "allowHttpPublicUrl": {"type": "boolean"},
    "secrets": {
      "type": "object",
      "properties": {
        "existingSecret": {"type": "string", "minLength": 1},
        "keys": {
          "type": "object",
          "additionalProperties": {"type": "string", "minLength": 1}
        }
      }
    },
    "oidc": {
      "type": "object",
      "properties": {
        "allowNone": {"type": "boolean"},
        "entra": {
          "type": "object",
          "properties": {
            "enabled": {"type": "boolean"},
            "tenantId": {
              "type": "string",
              "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})?$"
            },
            "clientId": {"type": "string"},
            "clientSecret": {"type": "boolean"}
          }
        },
        "google": {
          "type": "object",
          "properties": {
            "enabled": {"type": "boolean"},
            "clientId": {"type": "string"},
            "hostedDomain": {"type": "string"},
            "serviceAccount": {"type": "boolean"}
          }
        }
      }
    },
    "roles": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "viewer": {"$ref": "#/definitions/roleLists"},
        "operator": {"$ref": "#/definitions/roleLists"},
        "admin": {"$ref": "#/definitions/roleLists"}
      }
    },
    "settings": {"type": "object"},
    "extraEnv": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["name"],
        "properties": {"name": {"type": "string", "minLength": 1}}
      }
    },
    "storage": {
      "type": "object",
      "properties": {
        "volumes": {
          "type": "array",
          "items": {
            "type": "object",
            "required": ["name", "mountPath"],
            "additionalProperties": false,
            "properties": {
              "name": {"type": "string", "pattern": "^[a-z0-9]([-a-z0-9]*[a-z0-9])?$", "maxLength": 50},
              "mountPath": {"type": "string", "pattern": "^/[^:,\\s]+$"},
              "existingClaim": {"type": "string", "pattern": "^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$"},
              "volume": {"type": "object", "minProperties": 1, "maxProperties": 1},
              "readOnly": {"type": "boolean"}
            }
          }
        },
        "fsGroup": {"type": "integer", "minimum": 1},
        "supplementalGroups": {"type": "array", "items": {"type": "integer", "minimum": 1}}
      }
    },
    "port": {"type": "integer", "minimum": 1, "maximum": 65535},
    "service": {
      "type": "object",
      "properties": {
        "type": {"enum": ["ClusterIP", "NodePort", "LoadBalancer"]},
        "port": {"type": "integer", "minimum": 1, "maximum": 65535}
      }
    },
    "ingress": {
      "type": "object",
      "properties": {
        "enabled": {"type": "boolean"},
        "className": {"type": "string"},
        "annotations": {"type": "object"},
        "tls": {"type": "object", "properties": {"secretName": {"type": "string"}}},
        "paths": {
          "type": "array",
          "minItems": 1,
          "items": {
            "type": "object",
            "required": ["path", "pathType"],
            "properties": {
              "path": {"type": "string", "pattern": "^/"},
              "pathType": {"enum": ["Exact", "Prefix", "ImplementationSpecific"]}
            }
          }
        }
      }
    },
    "migrate": {
      "type": "object",
      "properties": {
        "enabled": {"type": "boolean"},
        "backoffLimit": {"type": "integer", "minimum": 0},
        "activeDeadlineSeconds": {"type": "integer", "minimum": 1},
        "resources": {"type": "object"}
      }
    },
    "resources": {"type": "object"},
    "updateStrategy": {"enum": ["RollingUpdate", "Recreate"]},
    "preStopSleepSeconds": {"type": "integer", "minimum": 0, "maximum": 300},
    "terminationGracePeriodSeconds": {"type": "integer", "minimum": 1},
    "podDisruptionBudget": {
      "type": "object",
      "properties": {
        "enabled": {"type": "boolean"},
        "maxUnavailable": {"type": ["integer", "string"], "minimum": 1}
      }
    },
    "networkPolicy": {
      "type": "object",
      "properties": {
        "enabled": {"type": "boolean"},
        "ingress": {
          "type": "object",
          "properties": {
            "from": {"type": ["array", "null"]},
            "anySource": {"type": "boolean"}
          }
        },
        "egress": {
          "type": "object",
          "properties": {
            "dns": {"type": "object", "properties": {"peers": {"type": "array"}}},
            "postgres": {
              "type": "object",
              "properties": {
                "port": {"type": "integer", "minimum": 1, "maximum": 65535},
                "peers": {"type": ["array", "null"]}
              }
            },
            "https": {
              "type": "object",
              "properties": {
                "ports": {
                  "type": "array",
                  "items": {"type": "integer", "minimum": 1, "maximum": 65535}
                },
                "cidrs": {"type": "array", "items": {"type": "string"}},
                "extraExcept": {"type": "array", "items": {"type": "string"}}
              }
            },
            "extra": {"type": "array"}
          }
        }
      }
    },
    "serviceAccount": {
      "type": "object",
      "properties": {"create": {"type": "boolean"}, "name": {"type": "string"}}
    },
    "podAnnotations": {"type": "object"},
    "podLabels": {"type": "object"},
    "nodeSelector": {"type": "object"},
    "tolerations": {"type": "array"},
    "affinity": {"type": "object"},
    "spreadAcrossNodes": {"type": "boolean"},
    "topologySpreadConstraints": {"type": "array"}
  },
  "definitions": {
    "roleLists": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "entraGroups": {"$ref": "#/definitions/names"},
        "googleGroups": {"$ref": "#/definitions/names"},
        "emails": {"$ref": "#/definitions/names"},
        "domains": {"$ref": "#/definitions/names"}
      }
    },
    "names": {
      "type": "array",
      "items": {"type": "string", "pattern": "^[^,\\s]+$"}
    }
  }
}
```

- [ ] **Step 6: The helpers**

Create `deploy/helm/swarmscribe-leader/templates/_helpers.tpl`:

```yaml
{{/* The app name label: the chart name, or nameOverride. */}}
{{- define "swarmscribe-leader.name" -}}
{{- regexReplaceAll "-+$" (default .Chart.Name .Values.nameOverride | trunc 63) "" }}
{{- end }}

{{/* The base of every object name. At most 55 characters, so that the suffix the chart
appends (-migrate: 8) keeps every derived name within 63, the limit for a Service name and
for a label value (a Job's name becomes its pods' job-name label). A release name has up to
53 characters and may hold dots, which a Service name may not. */}}
{{- define "swarmscribe-leader.fullname" -}}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if .Values.fullnameOverride }}
{{- regexReplaceAll "-+$" (.Values.fullnameOverride | trunc 55) "" }}
{{- else if contains $name .Release.Name }}
{{- regexReplaceAll "-+$" (.Release.Name | replace "." "-" | trunc 55) "" }}
{{- else }}
{{- regexReplaceAll "-+$" (printf "%s-%s" (.Release.Name | replace "." "-") $name | trunc 55) "" }}
{{- end }}
{{- end }}

{{- define "swarmscribe-leader.selectorLabels" -}}
app.kubernetes.io/name: {{ include "swarmscribe-leader.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "swarmscribe-leader.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{ include "swarmscribe-leader.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "swarmscribe-leader.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "swarmscribe-leader.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{- define "swarmscribe-leader.image" -}}
{{- $repository := required "image.repository is required: the leader image is not published; build it and load it into the cluster (see the README)" .Values.image.repository }}
{{- if .Values.image.digest }}
{{- printf "%s@%s" $repository .Values.image.digest }}
{{- else }}
{{- printf "%s:%s" $repository (required "image.tag is required: the leader image is not published; build it and name its tag (see the README)" .Values.image.tag) }}
{{- end }}
{{- end }}

{{/* Whether any sign-in provider is on ("true" or ""). */}}
{{- define "swarmscribe-leader.signIn" -}}
{{- if or .Values.oidc.entra.enabled .Values.oidc.google.enabled }}true{{ end }}
{{- end }}

{{/* The host of publicUrl, which is also the Ingress host. publicUrl is exactly
https://<lowercase DNS hostname>[:port] (http:// only with allowHttpPublicUrl): no path,
query, fragment, userinfo, uppercase letters, spaces or IP literal. With the Ingress on it
is https and has no port. */}}
{{- define "swarmscribe-leader.host" -}}
{{- $url := required "publicUrl is required: the address followers and administrators reach the leader at, e.g. https://leader.example.org" .Values.publicUrl }}
{{- $https := hasPrefix "https://" $url }}
{{- if and (hasPrefix "http://" $url) (not .Values.allowHttpPublicUrl) }}
{{- fail (printf "publicUrl must be https:// (got %q): credentials and signed file links would cross the network in the clear. allowHttpPublicUrl: true accepts http:// on a test cluster" $url) }}
{{- end }}
{{- $rest := $url | trimPrefix "https://" | trimPrefix "http://" }}
{{- $host := regexReplaceAll ":[0-9]{1,5}$" $rest "" }}
{{- if or (not (or $https (hasPrefix "http://" $url))) (not (regexMatch `^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$` $host)) (gt (len $host) 253) (regexMatch `(^|\.)[0-9]+$` $host) }}
{{- fail (printf "publicUrl must be exactly https://<lowercase DNS hostname>, with an optional :port: no path, query, fragment, user, uppercase letters or IP address (got %q)" $url) }}
{{- end }}
{{- if .Values.ingress.enabled }}
{{- if not $https }}
{{- fail (printf "publicUrl must be https:// with the Ingress on (got %q): the leader is published over TLS only" $url) }}
{{- end }}
{{- if ne $host $rest }}
{{- fail (printf "publicUrl must not carry a port with the Ingress on (got %q): an Ingress serves 443" $url) }}
{{- end }}
{{- end }}
{{- $host }}
{{- end }}

{{/* Fails the render on values that would deploy a leader that cannot start, cannot be
administered, or is cut off. */}}
{{- define "swarmscribe-leader.validate" -}}
{{- $_ := include "swarmscribe-leader.host" . }}
{{- $_ := required "secrets.existingSecret is required: the Secret holding the database URL and the link key" .Values.secrets.existingSecret }}
{{- $entra := .Values.oidc.entra.enabled }}
{{- $google := .Values.oidc.google.enabled }}
{{- if and (not (or $entra $google)) (not .Values.oidc.allowNone) }}
{{- fail "enable oidc.entra or oidc.google (or both): a leader without sign-in refuses every admin call, so nobody could create a pool token or a console credential. oidc.allowNone: true renders one anyway, for tests" }}
{{- end }}
{{- if and .Values.oidc.entra.clientSecret (not $entra) }}
{{- fail "oidc.entra.clientSecret needs oidc.entra.enabled" }}
{{- end }}
{{- if and .Values.oidc.google.serviceAccount (not $google) }}
{{- fail "oidc.google.serviceAccount needs oidc.google.enabled" }}
{{- end }}
{{- /* The same rules as the leader's own settings (config.py, _sign_in_is_complete), so a
bad mapping fails the render and not every pod at start-up. */}}
{{- range $role := list "viewer" "operator" "admin" }}
{{- $lists := index $.Values.roles $role | default dict }}
{{- if and $lists.entraGroups (not $entra) }}
{{- fail (printf "roles.%s.entraGroups needs oidc.entra.enabled" $role) }}
{{- end }}
{{- if and $lists.googleGroups (not $.Values.oidc.google.serviceAccount) }}
{{- fail (printf "roles.%s.googleGroups needs oidc.google.serviceAccount: the leader reads Google Groups with a service account" $role) }}
{{- end }}
{{- if and (or $lists.emails $lists.domains) (not $google) }}
{{- fail (printf "roles.%s.emails and .domains apply to Google sign-in: enable oidc.google" $role) }}
{{- end }}
{{- end }}
{{- $admin := .Values.roles.admin | default dict }}
{{- if and (or $entra $google) (not (or $admin.entraGroups $admin.googleGroups $admin.emails $admin.domains)) }}
{{- fail "roles.admin names nobody: with sign-in on, the first administrator is whoever roles.admin lists (a group, an email or a domain), and nothing else creates one" }}
{{- end }}
{{- /* Names the chart sets itself or reads from the Secret, without the prefix. The leader
reads its environment case-insensitively, so every comparison is on the upper-cased name. */}}
{{- $owned := list "PUBLIC_URL" "ENTRA_TENANT_ID" "ENTRA_CLIENT_ID" "GOOGLE_CLIENT_ID" "GOOGLE_HOSTED_DOMAIN" }}
{{- $secret := list "DATABASE_URL" "LINK_KEY" "GOOGLE_SERVICE_ACCOUNT" }}
{{- range $name, $_ := .Values.settings }}
{{- $upper := upper $name }}
{{- if or (has $upper $secret) (hasSuffix "_SECRET" $upper) }}
{{- fail (printf "settings.%s is a secret: secrets come from secrets.existingSecret, never from values" $name) }}
{{- end }}
{{- if or (has $upper $owned) (hasPrefix "ROLE_" $upper) }}
{{- fail (printf "settings.%s is set by the chart: use publicUrl, oidc or roles" $name) }}
{{- end }}
{{- if not (regexMatch "^[A-Z][A-Z0-9_]*$" $name) }}
{{- fail (printf "settings key %q must match ^[A-Z][A-Z0-9_]*$ (an upper-case environment name without the SWARMSCRIBE_ prefix)" $name) }}
{{- end }}
{{- end }}
{{- range .Values.extraEnv }}
{{- $upper := upper (toString .name) }}
{{- if hasPrefix "SWARMSCRIBE_" $upper }}
{{- $base := trimPrefix "SWARMSCRIBE_" $upper }}
{{- if or (has $base $secret) (hasSuffix "_SECRET" $base) (has $base $owned) (hasPrefix "ROLE_" $base) }}
{{- fail (printf "extraEnv %s collides with a variable the chart owns or reads from the Secret: use secrets.existingSecret, publicUrl, oidc or roles" .name) }}
{{- end }}
{{- end }}
{{- end }}
{{- if not .Values.storage.volumes }}
{{- fail "storage.volumes is required: the leader's only storage backend today is a folder on a filesystem, so without a volume no location can be added. Name a PersistentVolumeClaim you created (see values.yaml)" }}
{{- end }}
{{- $names := list }}
{{- $paths := list }}
{{- range .Values.storage.volumes }}
{{- if eq (empty .existingClaim) (empty .volume) }}
{{- fail (printf "storage.volumes %q needs exactly one of existingClaim and volume" .name) }}
{{- end }}
{{- if and .volume (hasKey .volume "emptyDir") }}
{{- fail (printf "storage.volumes %q is an emptyDir: every pod would get a folder of its own, and it is lost with the pod. Recordings need a volume every replica sees" .name) }}
{{- end }}
{{- $path := clean .mountPath }}
{{- if or (ne $path .mountPath) (eq $path "/") (eq $path "/app") (hasPrefix "/app/" $path) }}
{{- fail (printf "storage.volumes %q: mountPath %q must be a clean absolute path, not / and not under /app (the leader's own files)" .name .mountPath) }}
{{- end }}
{{- if or (has .name $names) (has $path $paths) }}
{{- fail (printf "storage.volumes %q: a name or mountPath is used twice" .name) }}
{{- end }}
{{- $names = append $names .name }}
{{- $paths = append $paths $path }}
{{- end }}
{{- range .Values.ingress.paths }}
{{- if or (eq .path "/") (hasPrefix "/healthz" .path) (hasPrefix "/readyz" .path) }}
{{- fail (printf "ingress.paths must not hold %q: \"/\" and the probes are never published (/readyz tells an anonymous caller whether the database is up). Everything the leader serves to the outside is under /v1" .path) }}
{{- end }}
{{- end }}
{{- if and .Values.ingress.enabled (not .Values.ingress.tls.secretName) }}
{{- fail "ingress.tls.secretName is required: the leader is published over TLS only" }}
{{- end }}
{{- if le (int .Values.terminationGracePeriodSeconds) (int .Values.preStopSleepSeconds) }}
{{- fail "terminationGracePeriodSeconds must be longer than preStopSleepSeconds: the leader would be killed before it is asked to stop" }}
{{- end }}
{{- if .Values.networkPolicy.enabled }}
{{- if not .Values.networkPolicy.egress.postgres.peers }}
{{- fail "networkPolicy.egress.postgres.peers is required with the NetworkPolicy on: say where the leader's Postgres is" }}
{{- end }}
{{- if and (not .Values.networkPolicy.ingress.from) (not .Values.networkPolicy.ingress.anySource) }}
{{- fail "networkPolicy.ingress.from is required with the NetworkPolicy on: say who may reach the leader (your ingress controller, the follower pools, a console), or set networkPolicy.ingress.anySource: true" }}
{{- end }}
{{- if and .Values.networkPolicy.ingress.from .Values.networkPolicy.ingress.anySource }}
{{- fail "networkPolicy.ingress.from and networkPolicy.ingress.anySource are both set: choose one" }}
{{- end }}
{{- end }}
{{- end }}

{{/* Non-secret settings as a YAML map of environment names to strings. */}}
{{- define "swarmscribe-leader.settings" -}}
SWARMSCRIBE_PUBLIC_URL: {{ .Values.publicUrl | quote }}
{{- if .Values.oidc.entra.enabled }}
SWARMSCRIBE_ENTRA_TENANT_ID: {{ required "oidc.entra.tenantId is required with oidc.entra.enabled" .Values.oidc.entra.tenantId | quote }}
SWARMSCRIBE_ENTRA_CLIENT_ID: {{ required "oidc.entra.clientId is required with oidc.entra.enabled" .Values.oidc.entra.clientId | quote }}
{{- end }}
{{- if .Values.oidc.google.enabled }}
SWARMSCRIBE_GOOGLE_CLIENT_ID: {{ required "oidc.google.clientId is required with oidc.google.enabled" .Values.oidc.google.clientId | quote }}
{{- with .Values.oidc.google.hostedDomain }}
SWARMSCRIBE_GOOGLE_HOSTED_DOMAIN: {{ . | quote }}
{{- end }}
{{- end }}
{{- $kinds := dict "entraGroups" "ENTRA_GROUPS" "googleGroups" "GOOGLE_GROUPS" "emails" "EMAILS" "domains" "DOMAINS" }}
{{- range $role := list "viewer" "operator" "admin" }}
{{- $lists := index $.Values.roles $role | default dict }}
{{- range $key, $suffix := $kinds }}
{{- with index $lists $key }}
SWARMSCRIBE_ROLE_{{ upper $role }}_{{ $suffix }}: {{ join "," . | quote }}
{{- end }}
{{- end }}
{{- end }}
{{- range $name, $value := .Values.settings }}
SWARMSCRIBE_{{ $name }}: {{ $value | toString | quote }}
{{- end }}
{{- end }}

{{/* Secret settings, as container env entries reading the existing Secret. */}}
{{- define "swarmscribe-leader.secretEnv" -}}
{{- $secret := .Values.secrets.existingSecret -}}
- name: SWARMSCRIBE_DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ $secret | quote }}
      key: {{ .Values.secrets.keys.databaseUrl | quote }}
- name: SWARMSCRIBE_LINK_KEY
  valueFrom:
    secretKeyRef:
      name: {{ $secret | quote }}
      key: {{ .Values.secrets.keys.linkKey | quote }}
{{- if and .Values.oidc.entra.enabled .Values.oidc.entra.clientSecret }}
- name: SWARMSCRIBE_ENTRA_CLIENT_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ $secret | quote }}
      key: {{ .Values.secrets.keys.entraClientSecret | quote }}
{{- end }}
{{- if .Values.oidc.google.enabled }}
- name: SWARMSCRIBE_GOOGLE_CLIENT_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ $secret | quote }}
      key: {{ .Values.secrets.keys.googleClientSecret | quote }}
{{- if .Values.oidc.google.serviceAccount }}
- name: SWARMSCRIBE_GOOGLE_SERVICE_ACCOUNT
  valueFrom:
    secretKeyRef:
      name: {{ $secret | quote }}
      key: {{ .Values.secrets.keys.googleServiceAccount | quote }}
{{- end }}
{{- end }}
{{- end }}

{{/* The leader pods. fsGroup: the storage volumes are opened with the leader's group. */}}
{{- define "swarmscribe-leader.podSecurityContext" -}}
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
fsGroup: {{ .Values.storage.fsGroup }}
fsGroupChangePolicy: OnRootMismatch
{{- with .Values.storage.supplementalGroups }}
supplementalGroups:
  {{- toYaml . | nindent 2 }}
{{- end }}
seccompProfile:
  type: RuntimeDefault
{{- end }}

{{/* The migration Job's pod: it mounts no volume, so it has no fsGroup. */}}
{{- define "swarmscribe-leader.jobSecurityContext" -}}
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
seccompProfile:
  type: RuntimeDefault
{{- end }}

{{- define "swarmscribe-leader.containerSecurityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: ["ALL"]
{{- end }}

{{/* Destinations no identity provider lives at, cut out of 0.0.0.0/0: this network,
Alibaba Cloud metadata, loopback, the Azure platform address, link-local (cloud metadata),
multicast and reserved. The same list as the console's and the follower's charts. */}}
{{- define "swarmscribe-leader.refusedV4" -}}
- 0.0.0.0/8
- 100.100.100.200/32
- 127.0.0.0/8
- 168.63.129.16/32
- 169.254.0.0/16
- 224.0.0.0/4
- 240.0.0.0/4
{{- end }}

{{/* The same for ::/0: loopback, IPv4-mapped and NAT64 (all inside ::/8), Teredo, 6to4,
the AWS IPv6 metadata address, link-local, site-local and multicast. */}}
{{- define "swarmscribe-leader.refusedV6" -}}
- ::/8
- 2001::/32
- 2002::/16
- fd00:ec2::254/128
- fe80::/10
- fec0::/10
- ff00::/8
{{- end }}
```

- [ ] **Step 7: The ServiceAccount, the ConfigMap, the Service**

Create `deploy/helm/swarmscribe-leader/templates/serviceaccount.yaml`:

```yaml
{{- if .Values.serviceAccount.create }}
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {{ include "swarmscribe-leader.serviceAccountName" . }}
  labels:
    {{- include "swarmscribe-leader.labels" . | nindent 4 }}
# The leader never calls the Kubernetes API.
automountServiceAccountToken: false
{{- end }}
```

Create `deploy/helm/swarmscribe-leader/templates/configmap.yaml`:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ include "swarmscribe-leader.fullname" . }}
  labels:
    {{- include "swarmscribe-leader.labels" . | nindent 4 }}
data:
  {{- include "swarmscribe-leader.settings" . | nindent 2 }}
```

Create `deploy/helm/swarmscribe-leader/templates/service.yaml`:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: {{ include "swarmscribe-leader.fullname" . }}
  labels:
    {{- include "swarmscribe-leader.labels" . | nindent 4 }}
spec:
  type: {{ .Values.service.type }}
  selector:
    {{- include "swarmscribe-leader.selectorLabels" . | nindent 4 }}
    app.kubernetes.io/component: leader
  ports:
    - name: http
      port: {{ .Values.service.port }}
      targetPort: http
      protocol: TCP
```

- [ ] **Step 8: The Deployment**

Create `deploy/helm/swarmscribe-leader/templates/deployment.yaml`:

```yaml
{{- include "swarmscribe-leader.validate" . }}
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "swarmscribe-leader.fullname" . }}
  labels:
    {{- include "swarmscribe-leader.labels" . | nindent 4 }}
    app.kubernetes.io/component: leader
spec:
  replicas: {{ .Values.replicaCount }}
  strategy:
    {{- if eq .Values.updateStrategy "Recreate" }}
    type: Recreate
    {{- else }}
    # No pod is missing during a rollout: a new one is Ready before an old one is stopped.
    type: RollingUpdate
    rollingUpdate:
      maxUnavailable: 0
      maxSurge: 1
    {{- end }}
  selector:
    matchLabels:
      {{- include "swarmscribe-leader.selectorLabels" . | nindent 6 }}
      app.kubernetes.io/component: leader
  template:
    metadata:
      labels:
        {{- include "swarmscribe-leader.labels" . | nindent 8 }}
        app.kubernetes.io/component: leader
        {{- with .Values.podLabels }}
        {{- toYaml . | nindent 8 }}
        {{- end }}
      annotations:
        # A changed setting restarts the pods.
        checksum/settings: {{ include "swarmscribe-leader.settings" . | sha256sum }}
        {{- with .Values.podAnnotations }}
        {{- toYaml . | nindent 8 }}
        {{- end }}
    spec:
      serviceAccountName: {{ include "swarmscribe-leader.serviceAccountName" . }}
      automountServiceAccountToken: false
      terminationGracePeriodSeconds: {{ .Values.terminationGracePeriodSeconds }}
      {{- with .Values.imagePullSecrets }}
      imagePullSecrets:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      securityContext:
        {{- include "swarmscribe-leader.podSecurityContext" . | nindent 8 }}
      containers:
        - name: leader
          image: {{ include "swarmscribe-leader.image" . | quote }}
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          args: ["serve", "--host", "0.0.0.0", "--port", {{ .Values.port | toString | quote }}]
          ports:
            - name: http
              containerPort: {{ .Values.port }}
              protocol: TCP
          envFrom:
            - configMapRef:
                name: {{ include "swarmscribe-leader.fullname" . }}
          env:
            {{- include "swarmscribe-leader.secretEnv" . | nindent 12 }}
            {{- with .Values.extraEnv }}
            {{- toYaml . | nindent 12 }}
            {{- end }}
          securityContext:
            {{- include "swarmscribe-leader.containerSecurityContext" . | nindent 12 }}
          # /healthz: the process answers (startup and liveness; never /readyz, so a database
          # outage restarts nothing). /readyz: the database answers and its schema is this
          # leader's or newer; it gives up on the database after 3 s, so the timeout is 5 s.
          # Neither asks an identity provider.
          startupProbe:
            httpGet:
              path: /healthz
              port: http
            periodSeconds: 2
            failureThreshold: 30
          livenessProbe:
            httpGet:
              path: /healthz
              port: http
            periodSeconds: 10
            timeoutSeconds: 3
            failureThreshold: 3
          readinessProbe:
            httpGet:
              path: /readyz
              port: http
            periodSeconds: 5
            timeoutSeconds: 5
            failureThreshold: 3
          {{- if gt (int .Values.preStopSleepSeconds) 0 }}
          # The pod is taken out of the Service while it still answers: only then is the
          # leader told to stop, and it finishes the requests it has.
          lifecycle:
            preStop:
              exec:
                command: ["sleep", {{ .Values.preStopSleepSeconds | toString | quote }}]
          {{- end }}
          resources:
            {{- toYaml .Values.resources | nindent 12 }}
          volumeMounts:
            {{- range .Values.storage.volumes }}
            - name: {{ printf "storage-%s" .name | quote }}
              mountPath: {{ .mountPath | quote }}
              {{- if .readOnly }}
              readOnly: true
              {{- end }}
            {{- end }}
      volumes:
        {{- range .Values.storage.volumes }}
        - name: {{ printf "storage-%s" .name | quote }}
          {{- if .existingClaim }}
          persistentVolumeClaim:
            claimName: {{ .existingClaim | quote }}
            {{- if .readOnly }}
            readOnly: true
            {{- end }}
          {{- else }}
          {{- toYaml .volume | nindent 10 }}
          {{- end }}
        {{- end }}
      {{- with .Values.nodeSelector }}
      nodeSelector:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.tolerations }}
      tolerations:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.affinity }}
      affinity:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- if .Values.topologySpreadConstraints }}
      topologySpreadConstraints:
        {{- toYaml .Values.topologySpreadConstraints | nindent 8 }}
      {{- else if and .Values.spreadAcrossNodes (gt (int .Values.replicaCount) 1) }}
      # Soft: prefer different nodes, never leave a replica unscheduled for it.
      topologySpreadConstraints:
        - maxSkew: 1
          topologyKey: kubernetes.io/hostname
          whenUnsatisfiable: ScheduleAnyway
          labelSelector:
            matchLabels:
              {{- include "swarmscribe-leader.selectorLabels" . | nindent 14 }}
              app.kubernetes.io/component: leader
      {{- end }}
```

- [ ] **Step 9: The notes**

Create `deploy/helm/swarmscribe-leader/templates/NOTES.txt`. It prints what to do next and never a secret; nothing in it is created by the chart.

```
The SwarmScribe leader is at {{ .Values.publicUrl }}
{{- if include "swarmscribe-leader.signIn" . }}

1. Sign in from your own machine, as someone roles.admin names (nothing is created for
   you, and no credential is printed here):

   swarmscribe-admin --leader {{ .Values.publicUrl }} login

2. Add a location inside a mounted volume ({{ range $i, $v := .Values.storage.volumes }}{{ if $i }}, {{ end }}{{ $v.mountPath }}{{ end }}), with a
   consent.txt at its root:

   swarmscribe-admin locations add NAME --root <a folder under a mount path>

3. For each follower pool, create a pool token and put it in a Secret for the follower
   chart (it is shown once):

   swarmscribe-admin pool-tokens create --name cpu-pods --pool default

4. For a fleet console: swarmscribe-admin console create --name fleet --max-role operator
{{- else }}

This leader has NO sign-in (oidc.allowNone): its admin API refuses everyone. Test use only.
{{- end }}
{{- if .Values.ingress.enabled }}

File links carry a signed token in the URL path. Turn request logging off for this Ingress
at your ingress controller, and raise its body limit to 512 MiB (README, "Deploy the leader").
{{- end }}
{{- if .Values.networkPolicy.enabled }}

A NetworkPolicy limits who reaches the leader and what it reaches. It needs a network plugin
that enforces NetworkPolicy; check that yours does.
{{- end }}
```

- [ ] **Step 10: Lint, validate, check**

```bash
helm lint deploy/helm/swarmscribe-leader -f deploy/helm/swarmscribe-leader/ci/test-values.yaml --strict
helm template leader deploy/helm/swarmscribe-leader --namespace swarmscribe \
  -f deploy/helm/swarmscribe-leader/ci/test-values.yaml > "$TEMP/leader-rendered.yaml"
kubeconform -strict -summary -kubernetes-version 1.33.0 "$TEMP/leader-rendered.yaml"
uv run --no-project --with pyyaml python deploy/helm/swarmscribe-leader/ci/check_render.py --only core,storage
uv run ruff check deploy/helm/swarmscribe-leader/ci/check_render.py
```

Expected, in order: `1 chart(s) linted, 0 chart(s) failed`; `Summary: 4 resources found in 1 file - Valid: 4, Invalid: 0, Errors: 0, Skipped: 0` (ServiceAccount, ConfigMap, Service, Deployment); `the rendered chart holds every required property (core, storage)`; `All checks passed!`.

Then see that the later sections fail for the right reason:

Run: `uv run --no-project --with pyyaml python deploy/helm/swarmscribe-leader/ci/check_render.py --only migrate`
Expected: `expected one Job named leader-swarmscribe-leader-migrate, found 0`.

And read the rendered notes once (a broken line continuation shows only here):

Run: `helm install --dry-run=client leader deploy/helm/swarmscribe-leader -n swarmscribe -f deploy/helm/swarmscribe-leader/ci/test-values.yaml | sed -n '/^NOTES:/,$p'`
Expected: the four numbered steps, the location line naming `/data/recordings, /data/archive`, and no secret.

- [ ] **Step 11: CI**

In `.github/workflows/ci.yml`, at the end of the job `chart` (after the step "Check what the follower chart renders"), add:

```yaml
      - name: Lint the leader chart
        run: >-
          helm lint deploy/helm/swarmscribe-leader
          -f deploy/helm/swarmscribe-leader/ci/test-values.yaml --strict
      - name: Validate the leader chart's manifests against the Kubernetes schemas
        run: |
          helm template leader deploy/helm/swarmscribe-leader --namespace swarmscribe \
            -f deploy/helm/swarmscribe-leader/ci/test-values.yaml > "$RUNNER_TEMP/leader.yaml"
          kubeconform -strict -summary -kubernetes-version 1.33.0 "$RUNNER_TEMP/leader.yaml"
      - name: Check what the leader chart renders
        run: >-
          uv run --no-project --with pyyaml
          python deploy/helm/swarmscribe-leader/ci/check_render.py --only core,storage
```

- [ ] **Step 12: Commit**

```bash
git add deploy/helm/swarmscribe-leader .github/workflows/ci.yml
git commit -m "Leader chart: settings, storage volumes, Deployment and Service

The chart names one Secret and creates none, mounts volumes the operator
provides, and refuses values that would deploy a leader nobody can reach
or administer. check_render.py holds what must be rendered and refused.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The migration Job

**Files:**
- Create: `deploy/helm/swarmscribe-leader/templates/migrate-job.yaml`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `swarmscribe-leader.settings`, `.secretEnv`, `.image`, `.jobSecurityContext`, `.containerSecurityContext`, `.labels`, `.fullname`; values `migrate.*`.
- Produces: Job `leader-swarmscribe-leader-migrate`, a pre-install and pre-upgrade hook running `migrate`; its pod labelled `app.kubernetes.io/component: migrate`. L3's `kind` test checks that it ran before the pods and was removed.

- [ ] **Step 1: Run the check to see it fail**

Run: `uv run --no-project --with pyyaml python deploy/helm/swarmscribe-leader/ci/check_render.py --only migrate`
Expected: exit status 1 with `expected one Job named leader-swarmscribe-leader-migrate, found 0`.

- [ ] **Step 2: Write the Job**

Create `deploy/helm/swarmscribe-leader/templates/migrate-job.yaml`:

```yaml
{{- if .Values.migrate.enabled }}
# Runs `swarmscribe-leader migrate` before any pod of this release starts (install) or is
# replaced (upgrade). A hook runs before the release's ConfigMap, ServiceAccount and
# NetworkPolicy exist, so this Job carries its settings inline (no envFrom), uses the
# namespace's default service account with no token, and reads the operator's own Secret
# (secrets.existingSecret), which must exist before `helm install`. It mounts no storage
# volume: a migration touches the database only.
#
# Delete policy: before-hook-creation removes the previous Job when the next one is made, so
# an old Job never blocks an upgrade; hook-succeeded removes a Job that worked. A FAILED Job
# is kept (hook-failed is deliberately not listed), so `kubectl logs job/...` shows why, and
# the failed hook fails the install or upgrade.
#
# During a pre-upgrade migration the old pods keep running against the new schema, and stay
# Ready: the leader's /readyz is ready when the schema is its own or newer. Migrations must
# therefore stay compatible with the previous release (add, then remove in a later release).
apiVersion: batch/v1
kind: Job
metadata:
  name: {{ include "swarmscribe-leader.fullname" . }}-migrate
  labels:
    {{- include "swarmscribe-leader.labels" . | nindent 4 }}
    app.kubernetes.io/component: migrate
  annotations:
    helm.sh/hook: pre-install,pre-upgrade
    helm.sh/hook-weight: "0"
    helm.sh/hook-delete-policy: before-hook-creation,hook-succeeded
spec:
  backoffLimit: {{ .Values.migrate.backoffLimit }}
  activeDeadlineSeconds: {{ .Values.migrate.activeDeadlineSeconds }}
  template:
    metadata:
      labels:
        {{- include "swarmscribe-leader.labels" . | nindent 8 }}
        app.kubernetes.io/component: migrate
    spec:
      restartPolicy: Never
      automountServiceAccountToken: false
      {{- with .Values.imagePullSecrets }}
      imagePullSecrets:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      securityContext:
        {{- include "swarmscribe-leader.jobSecurityContext" . | nindent 8 }}
      containers:
        - name: migrate
          image: {{ include "swarmscribe-leader.image" . | quote }}
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          args: ["migrate"]
          env:
            {{- range $name, $value := (include "swarmscribe-leader.settings" . | fromYaml) }}
            - name: {{ $name }}
              value: {{ $value | quote }}
            {{- end }}
            {{- include "swarmscribe-leader.secretEnv" . | nindent 12 }}
          securityContext:
            {{- include "swarmscribe-leader.containerSecurityContext" . | nindent 12 }}
          resources:
            {{- toYaml .Values.migrate.resources | nindent 12 }}
      {{- with .Values.nodeSelector }}
      nodeSelector:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.tolerations }}
      tolerations:
        {{- toYaml . | nindent 8 }}
      {{- end }}
{{- end }}
```

- [ ] **Step 3: Check**

```bash
helm lint deploy/helm/swarmscribe-leader -f deploy/helm/swarmscribe-leader/ci/test-values.yaml --strict
uv run --no-project --with pyyaml python deploy/helm/swarmscribe-leader/ci/check_render.py --only core,storage,migrate
```

Expected: `0 chart(s) failed`; `the rendered chart holds every required property (core, storage, migrate)`.

- [ ] **Step 4: CI**

In `.github/workflows/ci.yml`, in the step "Check what the leader chart renders", change `--only core,storage` to `--only core,storage,migrate`.

- [ ] **Step 5: Commit**

```bash
git add deploy/helm/swarmscribe-leader/templates/migrate-job.yaml .github/workflows/ci.yml
git commit -m "Leader chart: the migration as a pre-install and pre-upgrade hook

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The Ingress, with TLS

**Files:**
- Create: `deploy/helm/swarmscribe-leader/templates/ingress.yaml`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `swarmscribe-leader.host`, `.fullname`, `.labels`; values `ingress.enabled`, `.className`, `.annotations`, `.tls.secretName`, `.paths`. The Service's port name `http`.
- Produces: Ingress `leader-swarmscribe-leader` for the host of `publicUrl`, with TLS, and the paths of `ingress.paths` (default: Prefix `/v1`).

- [ ] **Step 1: Run the check to see it fail**

Run: `uv run --no-project --with pyyaml python deploy/helm/swarmscribe-leader/ci/check_render.py --only ingress`
Expected: exit status 1 with `expected one Ingress named leader-swarmscribe-leader, found 0`.

- [ ] **Step 2: Write the Ingress**

Create `deploy/helm/swarmscribe-leader/templates/ingress.yaml`:

```yaml
{{- if .Values.ingress.enabled }}
{{- $host := include "swarmscribe-leader.host" . }}
{{- $service := include "swarmscribe-leader.fullname" . }}
# Explicit paths under /v1, never "/" and never the probes (the validation refuses them).
# File links carry a signed token in their path: see values.yaml on request logging.
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: {{ $service }}
  labels:
    {{- include "swarmscribe-leader.labels" . | nindent 4 }}
  {{- with .Values.ingress.annotations }}
  annotations:
    {{- toYaml . | nindent 4 }}
  {{- end }}
spec:
  {{- with .Values.ingress.className }}
  ingressClassName: {{ . | quote }}
  {{- end }}
  tls:
    - hosts: [{{ $host | quote }}]
      secretName: {{ .Values.ingress.tls.secretName | quote }}
  rules:
    - host: {{ $host | quote }}
      http:
        paths:
          {{- range .Values.ingress.paths }}
          - path: {{ .path | quote }}
            pathType: {{ .pathType | quote }}
            backend:
              service:
                name: {{ $service }}
                port:
                  name: http
          {{- end }}
{{- end }}
```

- [ ] **Step 3: Check**

```bash
helm lint deploy/helm/swarmscribe-leader -f deploy/helm/swarmscribe-leader/ci/test-values.yaml --strict
uv run --no-project --with pyyaml python deploy/helm/swarmscribe-leader/ci/check_render.py --only core,storage,migrate,ingress
```

Expected: `0 chart(s) failed`; `the rendered chart holds every required property (core, storage, migrate, ingress)`.

The check reads the leader's routers. To see that it would catch a route outside `/v1`, change one prefix for a moment and change it back:

```bash
sed -i 's|APIRouter(prefix="/v1/admin")|APIRouter(prefix="/admin")|' packages/leader/src/swarmscribe_leader/api/admin.py
uv run --no-project --with pyyaml python deploy/helm/swarmscribe-leader/ci/check_render.py --only ingress
git checkout packages/leader/src/swarmscribe_leader/api/admin.py
```

Expected from the middle command: `FAILED: Ingress: the leader serves /admin, which no path reaches`. After the `git checkout`, `git status --short packages` prints nothing.

- [ ] **Step 4: CI**

In `.github/workflows/ci.yml`, in the step "Check what the leader chart renders", change `--only core,storage,migrate` to `--only core,storage,migrate,ingress`.

- [ ] **Step 5: Commit**

```bash
git add deploy/helm/swarmscribe-leader/templates/ingress.yaml .github/workflows/ci.yml
git commit -m "Leader chart: an Ingress with TLS that publishes /v1 and never a probe

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The PodDisruptionBudget and the NetworkPolicy

**Files:**
- Create: `deploy/helm/swarmscribe-leader/templates/pdb.yaml`
- Create: `deploy/helm/swarmscribe-leader/templates/networkpolicy.yaml`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `swarmscribe-leader.selectorLabels`, `.labels`, `.fullname`, `.signIn`, `.refusedV4`, `.refusedV6`; values `podDisruptionBudget.*`, `networkPolicy.*`, `port`, `replicaCount`.
- Produces: PodDisruptionBudget `leader-swarmscribe-leader` (only for more than one replica); NetworkPolicy `leader-swarmscribe-leader` selecting every pod of the release. L3's `kind` test sees it block.

- [ ] **Step 1: Run the check to see it fail**

Run: `uv run --no-project --with pyyaml python deploy/helm/swarmscribe-leader/ci/check_render.py --only network`
Expected: exit status 1 with `expected one PodDisruptionBudget named leader-swarmscribe-leader, found 0`.

- [ ] **Step 2: Write the PodDisruptionBudget**

Create `deploy/helm/swarmscribe-leader/templates/pdb.yaml`:

```yaml
{{- if and .Values.podDisruptionBudget.enabled (gt (int .Values.replicaCount) 1) }}
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: {{ include "swarmscribe-leader.fullname" . }}
  labels:
    {{- include "swarmscribe-leader.labels" . | nindent 4 }}
spec:
  maxUnavailable: {{ .Values.podDisruptionBudget.maxUnavailable }}
  # During a database outage every pod is unready; without this a node drain would hang on
  # them for as long as the outage lasts (Kubernetes 1.27 and later).
  unhealthyPodEvictionPolicy: AlwaysAllow
  selector:
    matchLabels:
      {{- include "swarmscribe-leader.selectorLabels" . | nindent 6 }}
      app.kubernetes.io/component: leader
{{- end }}
```

- [ ] **Step 3: Write the NetworkPolicy**

Create `deploy/helm/swarmscribe-leader/templates/networkpolicy.yaml`:

```yaml
{{- if .Values.networkPolicy.enabled }}
{{- $egress := .Values.networkPolicy.egress }}
{{- $v4 := include "swarmscribe-leader.refusedV4" . | fromYamlArray }}
{{- $v6 := include "swarmscribe-leader.refusedV6" . | fromYamlArray }}
{{- range $egress.https.extraExcept }}
{{- if contains ":" . }}
{{- $v6 = append $v6 . }}
{{- else }}
{{- $v4 = append $v4 . }}
{{- end }}
{{- end }}
# Selects every pod of this release: the leader pods and the migration Job's pod (so the
# migration, on an upgrade, gets the DNS and Postgres egress it needs; on a first install the
# hook runs before this policy exists). What a NetworkPolicy cannot do is in values.yaml.
# The storage volumes need no rule: the node mounts them, not the pod.
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: {{ include "swarmscribe-leader.fullname" . }}
  labels:
    {{- include "swarmscribe-leader.labels" . | nindent 4 }}
spec:
  podSelector:
    matchLabels:
      {{- include "swarmscribe-leader.selectorLabels" . | nindent 6 }}
  policyTypes: ["Ingress", "Egress"]
  ingress:
    # Followers, administrators and consoles, on the leader's port only.
    - ports:
        - protocol: TCP
          port: {{ .Values.port }}
      {{- if not .Values.networkPolicy.ingress.anySource }}
      from:
        {{- toYaml .Values.networkPolicy.ingress.from | nindent 8 }}
      {{- end }}
  egress:
    # Name resolution.
    - to:
        {{- toYaml $egress.dns.peers | nindent 8 }}
      ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53
    # The leader's own Postgres.
    - to:
        {{- toYaml $egress.postgres.peers | nindent 8 }}
      ports:
        - protocol: TCP
          port: {{ $egress.postgres.port }}
    {{- if include "swarmscribe-leader.signIn" . }}
    # The identity providers: the refused ranges are cut out of "anywhere".
    - to:
        {{- range $egress.https.cidrs }}
        - ipBlock:
            cidr: {{ . | quote }}
            {{- if eq . "0.0.0.0/0" }}
            except:
              {{- toYaml $v4 | nindent 14 }}
            {{- else if eq . "::/0" }}
            except:
              {{- toYaml $v6 | nindent 14 }}
            {{- end }}
        {{- end }}
      ports:
        {{- range $egress.https.ports }}
        - protocol: TCP
          port: {{ . }}
        {{- end }}
    {{- end }}
    {{- with $egress.extra }}
    {{- toYaml . | nindent 4 }}
    {{- end }}
{{- end }}
```

- [ ] **Step 4: Lint, validate, run the whole check**

```bash
helm lint deploy/helm/swarmscribe-leader -f deploy/helm/swarmscribe-leader/ci/test-values.yaml --strict
helm template leader deploy/helm/swarmscribe-leader --namespace swarmscribe \
  -f deploy/helm/swarmscribe-leader/ci/test-values.yaml > "$TEMP/leader-rendered.yaml"
kubeconform -strict -summary -kubernetes-version 1.33.0 "$TEMP/leader-rendered.yaml"
uv run --no-project --with pyyaml python deploy/helm/swarmscribe-leader/ci/check_render.py
```

Expected: `0 chart(s) failed`; `Summary: 8 resources found in 1 file - Valid: 8, Invalid: 0, Errors: 0, Skipped: 0`; `the rendered chart holds every required property (core, storage, migrate, ingress, network)` (about 15 seconds: it renders the chart some 150 times).

One more render, of the shape L3 installs (no sign-in, no Ingress, plain http, peers by label):

```bash
cat > "$TEMP/leader-kind-shape.yaml" <<'EOF'
image: {repository: swarmscribe-leader, tag: kind, pullPolicy: Never}
publicUrl: http://leader-swarmscribe-leader
allowHttpPublicUrl: true
secrets: {existingSecret: leader}
oidc: {allowNone: true}
storage:
  volumes:
    - {name: data, mountPath: /data, existingClaim: leader-data}
ingress: {enabled: false}
networkPolicy:
  ingress:
    from:
      - podSelector: {matchLabels: {app.kubernetes.io/name: swarmscribe-follower}}
  egress:
    postgres:
      peers:
        - podSelector: {matchLabels: {app: postgres}}
EOF
helm template leader deploy/helm/swarmscribe-leader -n swarmscribe-e2e -f "$TEMP/leader-kind-shape.yaml" \
  | kubeconform -strict -summary -kubernetes-version 1.33.0 -
```

Expected: `Summary: 7 resources found parsing stdin - Valid: 7, Invalid: 0, Errors: 0, Skipped: 0` (no Ingress). In the rendered NetworkPolicy the egress has two rules only, DNS and Postgres: `helm template ... | grep -c "ipBlock"` prints `0`.

- [ ] **Step 5: CI**

In `.github/workflows/ci.yml`, in the step "Check what the leader chart renders", remove ` --only core,storage,migrate,ingress` so that the whole check runs:

```yaml
      - name: Check what the leader chart renders
        run: >-
          uv run --no-project --with pyyaml
          python deploy/helm/swarmscribe-leader/ci/check_render.py
```

Run: `uv run --no-project --with pyyaml python -c "import yaml; steps = yaml.safe_load(open('.github/workflows/ci.yml'))['jobs']['chart']['steps']; print([s.get('name') for s in steps][-3:])"`
Expected: `['Lint the leader chart', "Validate the leader chart's manifests against the Kubernetes schemas", 'Check what the leader chart renders']`.

- [ ] **Step 6: Commit**

```bash
git add deploy/helm/swarmscribe-leader/templates/pdb.yaml deploy/helm/swarmscribe-leader/templates/networkpolicy.yaml .github/workflows/ci.yml
git commit -m "Leader chart: a PodDisruptionBudget and a NetworkPolicy

Ingress only from the peers the operator names; egress to DNS, to
Postgres, and to HTTPS only when sign-in is on.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: The deployment guide

**Files:**
- Modify: `README.md` (the status table at line 19; a new section "## Deploy the leader" before "## Deploy the fleet console"; one sentence in "## Deploy a follower pool")

**Interfaces:**
- Consumes: Tasks 1 to 4; L1's README section "Leader image".
- Produces: README section "Deploy the leader" with the subsections "The image", "Kubernetes, with the Helm chart", "Storage", "The first administrator, pool tokens and consoles", "Probes and upgrades", "The network", "What has been run, and what has not". L3's Task 4 rewrites the last subsection with what the `kind` run showed, and adds "The whole system on one cluster".

- [ ] **Step 1: The status table**

In `README.md`, replace the row

```markdown
| Helm chart for the leader, and autoscaling | Not started |
```

with

```markdown
| `swarmscribe-leader` image and Helm chart (`deploy/helm/swarmscribe-leader`) | Built; rendered and validated in CI; see "Deploy the leader" for what has been run |
| Autoscaling of follower pools | Not started (it needs a queue-depth metric the leader does not expose yet) |
```

and in the row for `swarmscribe-leader` keep the text as it is ("Built (local storage); cloud storage and vocabulary next").

- [ ] **Step 2: The guide**

In `README.md`, immediately before the line `## Deploy the fleet console`, add:

````markdown
## Deploy the leader

One release of the chart `deploy/helm/swarmscribe-leader` is one leader deployment. It needs:

- **Its own Postgres** (the tests and CI run on 16; no older version has been tried). Never
  the console's database. Allow 15 connections per leader replica and one for the migration.
- **A volume every leader replica can see**, holding the recordings and receiving the
  transcripts. The leader's only storage backend today is a folder on a filesystem; cloud
  object storage (Azure Blob Storage, Google Cloud Storage) is planned and not built, and
  S3 is not planned. The chart mounts a volume you provide and creates none ("Storage").
- **The image, built and loaded** where the cluster can pull it ("The image").
- **A Secret, created beforehand,** holding the database URL and the link key. The chart
  never creates one, and the migration hook reads it before anything else exists.
- **An identity provider**: an Entra ID app registration or a Google OAuth client, as in
  "Administrators: sign-in and roles" above. A leader without sign-in refuses every admin
  call, and the chart refuses to render one.
- **One address for everybody.** `publicUrl` is where followers, administrators and consoles
  reach the leader, and the leader builds every file link from it. Followers inside the
  cluster and machines outside it must both be able to reach that one address, over TLS.

### The image

`docker/leader.Dockerfile` ("Leader image" above) builds `swarmscribe-leader`. No image is
published, so the chart has no working default for `image.repository` and `image.tag`: both
are required, and the render fails saying so.

```
docker build -t swarmscribe-leader:0.1.0 -f docker/leader.Dockerfile .
# a local cluster:        kind load docker-image swarmscribe-leader:0.1.0
# a registry of your own: docker tag swarmscribe-leader:0.1.0 registry.example.org/swarmscribe-leader:0.1.0
#                         docker push registry.example.org/swarmscribe-leader:0.1.0
```

If `kind load docker-image` stops with "content digest ... not found", load it from an
archive as "Deploy the fleet console", "The image" shows. Use `image.pullPolicy: Never` (or
`IfNotPresent`) for a loaded image.

### Kubernetes, with the Helm chart

1. Create the namespace and the Secret. The values below are placeholders.

   ```
   kubectl create namespace swarmscribe
   kubectl -n swarmscribe create secret generic swarmscribe-leader \
     --from-literal=database-url='postgresql://leader:...@db.internal:5432/swarmscribe' \
     --from-literal=link-key="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
   ```

   With Google sign-in add `--from-literal=google-client-secret=...`; for Google Groups,
   `--from-file=google-service-account=key.json` and `oidc.google.serviceAccount: true`; for
   Entra ID people in too many groups for the token, `--from-literal=entra-client-secret=...`
   and `oidc.entra.clientSecret: true`. The link key signs every file link: keep it, and
   know that changing it makes the links already handed out worthless (followers ask for
   new ones).

2. Write your values. This is a whole file for Entra ID sign-in, an NFS-backed claim and
   Traefik:

   ```yaml
   image:
     repository: registry.example.org/swarmscribe-leader
     tag: 0.1.0
   publicUrl: https://leader.example.org
   secrets:
     existingSecret: swarmscribe-leader
   oidc:
     entra:
       enabled: true
       tenantId: 0f0e0d0c-0b0a-4908-8706-050403020100
       clientId: 6d3a6b52-1c2e-4a8f-9d61-2f6a4c0b7e11
   roles:
     admin:
       entraGroups: [3f2b0c5e-8f6d-4a51-9c0e-6f1d2a7b9c11]
   storage:
     volumes:
       - name: recordings
         mountPath: /data/recordings
         existingClaim: recordings
   ingress:
     className: traefik
     tls:
       secretName: leader-tls
   networkPolicy:
     ingress:
       from:
         - namespaceSelector:
             matchLabels:
               kubernetes.io/metadata.name: traefik
     egress:
       postgres:
         peers:
           - ipBlock:
               cidr: 10.20.30.40/32
   ```

3. Install. Helm runs the migration first, then starts the pods.

   ```
   helm -n swarmscribe install leader deploy/helm/swarmscribe-leader -f values.yaml
   kubectl -n swarmscribe rollout status deploy/leader-swarmscribe-leader
   ```

   If the migration fails, the install fails and the Job is kept:
   `kubectl -n swarmscribe logs job/leader-swarmscribe-leader-migrate` says why.

4. Sign in and set the leader up, from your own machine ("The first administrator, pool
   tokens and consoles").

What the chart installs:

| Object | What it is |
|---|---|
| Deployment | `replicaCount` leader pods (default 2) running `serve`: non-root (10001), read-only root filesystem, no capabilities, no service-account token; your volumes mounted; startup and liveness probes on `/healthz`, readiness on `/readyz`; a `preStop` pause before a pod is stopped; requests 100m CPU and 256Mi (not measured under load) |
| Job (hook) | `swarmscribe-leader migrate`, before install and before every upgrade |
| Service | port 80 to the pods' 8080 |
| Ingress | the host of `publicUrl`, with TLS from `ingress.tls.secretName` (required), publishing `/v1` only |
| ConfigMap | the settings that are not secret, the role mappings among them |
| PodDisruptionBudget | `maxUnavailable: 1`, when there is more than one replica |
| NetworkPolicy | who may reach the pods and what they may reach: "The network" |
| ServiceAccount | one with no token mounted; the leader never calls the Kubernetes API |

It installs no Secret, no volume, no database, and nothing for metrics (the leader has no
`/metrics` yet).

Values (`deploy/helm/swarmscribe-leader/values.yaml` documents every one):

| Value | Setting or meaning |
|---|---|
| `publicUrl` | `SWARMSCRIBE_PUBLIC_URL`, and the Ingress host. Required. `https://<host>`; a `:port` only with the Ingress off; `http://` only with `allowHttpPublicUrl: true`, on a test cluster |
| `image.repository`, `image.tag` | the image. Both required (`image.digest` wins over the tag) |
| `secrets.existingSecret` | the Secret's name. Required. `secrets.keys.*` name its keys: `database-url`, `link-key`, `entra-client-secret`, `google-client-secret`, `google-service-account` |
| `oidc.entra.*`, `oidc.google.*` | sign-in. One is required; `oidc.allowNone: true` renders a leader without sign-in, for tests |
| `roles.<role>.<list>` | `SWARMSCRIBE_ROLE_<ROLE>_<LIST>`: `entraGroups`, `googleGroups`, `emails`, `domains` for `viewer`, `operator`, `admin`. `roles.admin` must name someone |
| `settings` | any other non-secret setting without the `SWARMSCRIBE_` prefix, e.g. `LEASE_SECONDS: "120"`. A secret, or a name the chart sets itself, is refused |
| `storage.volumes` | the volumes to mount. Required: "Storage" |
| `replicaCount` | leader pods, default 2 |
| `updateStrategy` | `RollingUpdate` (default) or `Recreate`: "Storage" |
| `ingress.*` | the Ingress; `ingress.enabled: false` if you publish the Service another way |
| `networkPolicy.*` | "The network" |
| `migrate.*`, `resources`, `preStopSleepSeconds`, `terminationGracePeriodSeconds`, `podDisruptionBudget.*` | as named |

The render fails, with a message, on values that would deploy a leader that cannot start,
cannot be reached or cannot be administered: no image; a `publicUrl` that is not exactly a
host, or is `http://`; no Secret; no sign-in; sign-in with nobody under `roles.admin`; a
role list whose provider is off; a secret under `settings` or `extraEnv`; no storage volume,
or an `emptyDir`, or a mount at `/` or under `/app`; an Ingress path of `/` or a probe; an
Ingress without TLS; a NetworkPolicy without Postgres peers or without ingress peers.

### Storage

A location's root is a folder inside the leader's own filesystem
(`swarmscribe-admin locations add NAME --root /data/recordings/archive`), so the recordings
must be on a volume mounted into the leader. Followers never see that volume: they download
each recording and upload each transcript through the leader, by signed links.

```yaml
storage:
  volumes:
    - name: recordings
      mountPath: /data/recordings
      existingClaim: recordings          # a PersistentVolumeClaim you created
    - name: archive
      mountPath: /data/archive
      readOnly: true                     # transcripts cannot be written here: give such a
      volume:                            # location an output folder on another volume
        nfs: {server: nas.internal, path: /exports/archive}
```

**Every leader replica must see the same files.** The chart cannot check it:

| Your volume | Use | Upgrades |
|---|---|---|
| ReadWriteMany (NFS, CephFS, Azure Files, Filestore) | two or more replicas, `RollingUpdate` | no pod is missing |
| ReadWriteOnce on a one-node cluster | two or more replicas, `RollingUpdate` | no pod is missing; the node is a single point of failure |
| ReadWriteOnce on several nodes | `replicaCount: 1` and `updateStrategy: Recreate` | the leader is away for the length of every upgrade; followers wait and retry |

With a ReadWriteOnce claim on several nodes and the default strategy, the rollout's new pod
may land on another node and wait for ever on "Multi-Attach error"; delete that pod, or use
the last row. Replicas that saw *different* files would each scan their own and report the
other's recordings missing: never give two replicas two volumes.

The volumes are opened with group 10001 (`storage.fsGroup`), and the leader writes as user
10001. A volume that ignores `fsGroup` (NFS) must be writable by uid or gid 10001 on the
server; `storage.supplementalGroups` adds groups so the leader can read recordings another
system wrote. The leader writes each upload to a temporary file beside its target and then
moves it into place, so it needs to create files in the output folders and nowhere else.

### The first administrator, pool tokens and consoles

Nothing creates an administrator. The first one is whoever `roles.admin` names (a group, an
email or a domain), signing in with their own account from their own machine:

```
uv run swarmscribe-admin --leader https://leader.example.org login --provider entra
uv run swarmscribe-admin whoami
uv run swarmscribe-admin locations add archive --root /data/recordings/archive
```

To change who administers the leader, change `roles` and run `helm upgrade`.

A follower pool needs a pool token, and a fleet console needs a console credential. Both are
created by a signed-in administrator, shown once on their terminal, and stored by the leader
only as a hash. The chart has no part in either, and neither is ever in Helm's values, its
notes or a ConfigMap:

```
uv run swarmscribe-admin pool-tokens create --name cpu-pods --pool default
kubectl -n transcribe create secret generic pool-token --from-literal=pool-token='<the token>'

uv run swarmscribe-admin console create --name fleet --max-role operator
```

The first goes into the Secret the follower chart reads ("Deploy a follower pool"); the
second is entered once in the console ("Leaders in the console").

### Probes and upgrades

Startup and liveness ask `/healthz`, which never touches the database, so a database outage
restarts nothing. Readiness asks `/readyz` every 5 seconds with a 5 second timeout (the
leader's own check gives up at 3): during a database outage the pods go unready and come
back by themselves. A pod that *starts* while the database is unreachable exits and is
restarted until it answers.

`helm upgrade` runs the migration, then replaces the pods one at a time
(`maxUnavailable: 0`). The migration is a pre-upgrade hook, so it runs while the old pods
are serving: they keep serving on the migrated schema until they are replaced, and stay
Ready (`/readyz` is ready when the schema is the leader's own or newer). A pod that is being
stopped first waits `preStopSleepSeconds` while still answering, so that the Service stops
sending it requests, and then finishes the requests it has.

What follows from that:

- **Every migration must stay compatible with the previous release** (add a column now, drop
  the old one in a later release). A migration that breaks the previous version breaks the
  serving pods for the length of the upgrade.
- **There is no rolling back the image after a migration.** A leader never starts on a
  database that is ahead of it, and there is no downgrade command: roll forward.
- An old pod that restarts in the middle of an upgrade does not come back, for the same
  reason; the others carry on.
- A changed setting restarts the pods (the Deployment carries a checksum of the ConfigMap).
  A changed Secret does not: `kubectl rollout restart` after changing one.

### The network

The pods speak plain HTTP on 8080; TLS ends at your Ingress. Three things about the Ingress
are yours to do, with your controller's own annotations (`ingress.annotations`):

- **Turn request logging off for this Ingress.** A file link carries its signed token in the
  URL path (`/v1/files/<token>`). The leader never logs a request line; an ingress
  controller's access log would hold tokens that work until they expire (30 minutes for a
  download; 2 hours for an upload, and an upload only while its job's lease is current).
- **Allow bodies of 512 MiB** and turn request buffering off: transcripts are small, but
  the limit on an upload is the leader's, not your controller's default.
- Publish `/v1` and nothing else (the default). `/healthz` and `/readyz` are never
  published, and the chart refuses a path that would: `/readyz` tells an anonymous caller
  whether the database is up.

The NetworkPolicy (on by default) selects every pod of the release:

| Direction | What is allowed |
|---|---|
| in | the leader's port, from `networkPolicy.ingress.from` only. Required: your ingress controller, and any pool or console in this cluster that uses the Service directly. `anySource: true` allows anyone (the leader authenticates every caller itself) |
| out | DNS; Postgres, at `networkPolicy.egress.postgres.peers` (required); and, with sign-in, TCP 443 to anywhere except loopback, link-local and cloud-metadata addresses, multicast and reserved ranges (the identity providers) |

A follower that reaches the leader through the Ingress arrives as the ingress controller, so
that one peer covers every follower and console that uses `publicUrl`. Peers are matched
after Service address translation: name pods by selector, or by their own addresses, never a
Service's ClusterIP. A NetworkPolicy needs a network plugin that enforces it; without one it
is accepted and does nothing. The storage volumes need no rule: the node mounts them.

### What has been run, and what has not

CI lints the chart, validates what it renders against the Kubernetes 1.33 schemas, and runs
`deploy/helm/swarmscribe-leader/ci/check_render.py`, which renders it some 150 times and
checks what must hold and what must be refused. **The chart has not been installed on a
cluster yet**: that is the next piece of work (`e2e/leader-kind`), and this section will say
what it showed. Until then every statement above about what a running cluster does comes
from the leader's own tests and from the other two charts' installs, not from this one.
````

- [ ] **Step 3: The follower guide's one sentence**

In `README.md`, section "## Deploy a follower pool", replace

```markdown
  the pods too. The leader has no chart yet; the follower chart needs only its URL.
```

with

```markdown
  the pods too ("Deploy the leader"). The follower chart needs only the leader's URL.
```

- [ ] **Step 4: Check the guide against the chart**

Every value the guide names must exist, and the example values file must render:

```bash
python - <<'EOF' > "$TEMP/guide-values.yaml"
import re
text = open("README.md", encoding="utf-8").read()
section = text[text.index("## Deploy the leader"):text.index("## Deploy the fleet console")]
block = re.search(r"   ```yaml\n(.*?)\n   ```", section, re.S).group(1)
print("\n".join(line[3:] for line in block.splitlines()))
EOF
helm template leader deploy/helm/swarmscribe-leader -n swarmscribe -f "$TEMP/guide-values.yaml" \
  | kubeconform -strict -summary -kubernetes-version 1.33.0 -
```

Expected: `Summary: 8 resources found parsing stdin - Valid: 8, Invalid: 0, Errors: 0, Skipped: 0`. If the render fails, the guide's example is wrong: fix the guide.

Run: `grep -n "S3\|ServiceMonitor\|first admin credential" README.md | sed -n '1,20p'`
Expected: the only mention of S3 in "Deploy the leader" is "S3 is not planned"; no `ServiceMonitor`; no "first admin credential".

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "README: deploy the leader with its Helm chart

Says what the chart installs and refuses, what storage it needs, where
the first administrator comes from, and that it has not been installed
on a cluster yet.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage.** Section 5 (values): every row is in `values.yaml` and `values.schema.json` (Task 1), and the refusals at its end are each a `refused(...)` in `check_render.py`. Section 7 (the hook): Task 2. Section 8 (replicas and storage): Task 1's Deployment, validation and `check_storage`; the three-row table is in the guide (Task 5). Section 9 (security, NetworkPolicy, Ingress): Tasks 1, 3 and 4. Decisions C1, C2, C4, C5, C6, C7 and rulings R5 to R11: rulings 2 to 14 here. Section 10 (the walk-through): its leader part is the guide's steps 1 to 4; the three charts together are L3's.

**What is not done here.** Any install (L3). The `kind` values and the CI job that installs (L3). Follower autoscaling, metrics, cloud storage (spec, non-goals).

**Type and name consistency.** The check's `NAME = "leader-swarmscribe-leader"` is the release `leader` plus the chart's name, as `swarmscribe-leader.fullname` renders it. Container names: `leader` and `migrate`. Component labels: `leader` and `migrate`. Volume names are `storage-<name>`. The check's sections are `core`, `storage`, `migrate`, `ingress`, `network`, and each task's CI edit names exactly the sections that pass after it. Secret keys in `values.yaml`, the guide and `test-values.yaml` are the same five.

**Honesty.** The chart's files were rendered and checked before this plan was written; nothing was installed. Task 5's last subsection says so in the README, in those words, until L3 replaces it.
