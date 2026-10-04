# Fleet Console C4b — Helm Chart and Deployment Guide Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a Helm chart that deploys the fleet console (and only the console) on Kubernetes, with a NetworkPolicy that enforces the console's egress rules at connect time, and a deployment guide in the README.

**Architecture:** One chart, `deploy/helm/swarmscribe-console`, renders a Deployment, a migration Job run as a pre-install and pre-upgrade hook, a Service, an Ingress with TLS (plus an optional second Ingress for exactly `/auth/login`, to carry a rate limit), a ConfigMap, a PodDisruptionBudget, a NetworkPolicy and a ServiceAccount. It never creates a Secret; it reads one the operator made. A Python script renders the chart and asserts what must hold, and CI runs it with `helm lint` and `kubeconform`.

**Tech Stack:** Helm 4.3.0, Kubernetes 1.25 or later (`networking.k8s.io/v1`, `policy/v1`, `batch/v1`), kubeconform 0.8.0, Python with PyYAML for the render check, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-03-fleet-console-design.md` (sections 7 and 8), with `docs/superpowers/specs/2026-10-02-swarmscribe-architecture-design.md` (sections 10, 11, 12, 14) for how the project's charts are meant to look.

**This plan is the second of two.** It needs C4a (`2026-10-04-fleet-console-c4a-image-and-compose.md`) merged: the `swarmscribe-console` image (entrypoint `swarmscribe-console`, user 10001, port 8080), `/healthz` and `/readyz`, `SWARMSCRIBE_CONSOLE_LEADER_CA_FILE` and `SWARMSCRIBE_CONSOLE_LOGIN_ATTEMPTS_MAX`.

## Global Constraints

- "Helm values: replicas, Postgres URL, console key, OIDC client settings, ingress with TLS" (fleet console spec 8). Every one of them is settable; the three secret ones (Postgres URL, console key, OIDC client secrets) are settable by naming a Secret, never as plain values.
- "Database URL, storage credentials and the link-signing key come from Kubernetes Secrets; never baked into images" (master spec 10). The chart renders no Secret and no secret value.
- "Schema changes go through Alembic migrations, run as a Helm pre-upgrade job" (master spec 9) and "Migration `Job` as a pre-upgrade hook" (master spec 12). The console's Job is a pre-install *and* pre-upgrade hook, because the console refuses to start on an unmigrated database.
- "`Deployment`, 2+ replicas, `PodDisruptionBudget`, `Service`, `Ingress`" is the master spec's shape for the leader (section 12); the console's chart has the same shape. Default `replicaCount: 2`.
- "Chart — `helm lint` and template rendering in CI" (master spec 14); run locally first.
- "TLS everywhere" (fleet console spec 7): the Ingress cannot be rendered without TLS, and `publicUrl` must be `https://`.
- The egress rules are those of the README's "Deployment note: egress", which is the one place they are written down: loopback, link-local, metadata (`169.254.169.254`, `fd00:ec2::254`, `100.100.100.200`), unspecified, multicast, reserved, `0.0.0.0/8`, `fec0::/10`, 6to4 `2002::/16`, Teredo `2001::/32`, NAT64 `64:ff9b::/96` and IPv4-mapped forms are refused; private addresses are allowed.
- The chart is for the console only. The leader's chart and the follower image are separate roadmap items (roadmap 4 and 6) and are not built here.
- The pods run as the image's user 10001 with a read-only root filesystem, no capabilities, no privilege escalation, the `RuntimeDefault` seccomp profile and no service-account token.
- **Helm and kubeconform run locally.** Neither is installed on the Windows development machine, but both are standalone binaries: fetch them into a scratch folder outside the repository (see "Helm and kubeconform on the Windows machine"; checked 2026-10-04: Helm v4.3.0 and kubeconform v0.8.0 download and run there, and `kubectl` is already on the PATH with Docker Desktop). So every "CI only" or "CI is the authority" statement in this plan is a local run first: lint, render, kubeconform and `check_render.py` are all run before each commit, and CI repeats them. kubeconform needs network access to fetch schemas. Docker is available too, so the C4a image is built and loaded locally (ruling 13).
- On the Windows machine, `uv` is run as `python -m uv`. The commands below are written `uv run ...`.
- Start from an up-to-date `main` that has C4a merged, on a new branch `fleet-console-c4b`.

## Rulings

Those marked **(owner)** are the owner's to overturn.

1. **Chart location: `deploy/helm/swarmscribe-console`**, a chart of its own beside the future `deploy/helm/swarmscribe` (master spec 4). The console is deployed once for many leaders, on its own release cycle, possibly in another cluster; it is not a subchart of the leader's.
2. **The chart never creates a Secret.** `secrets.existingSecret` is required. A values file and Helm's release history are not a place for the console key or a database password. This narrows the spec's "Helm values: … Postgres URL, console key": they are values that name a Secret's keys. **(owner)**
3. **Non-secret settings go in a ConfigMap**, read by the Deployment with `envFrom`. A checksum annotation restarts the pods when it changes.
4. **The migration is a Job with `helm.sh/hook: pre-install,pre-upgrade`.** A hook runs before the release's own ConfigMap and ServiceAccount exist on a first install, so the Job carries the same settings inline and uses the namespace's default ServiceAccount with no token. Not an init container: replicas starting together must not run Alembic concurrently.
5. **Probes:** all three are `httpGet` (GET). Startup and liveness use `/healthz`; the startup probe never uses `/readyz`, which can take up to 3 s while the database is down. Readiness uses `/readyz` with `timeoutSeconds` of 5 or more (the console's own database check gives up at 3 s). Liveness never depends on the database, so a database outage does not restart every pod. The console answers HEAD like GET and 405 to any other method, but the probes use GET and the chart's README says so. **The Ingress must not route `/healthz` or `/readyz`** (ruling 14): `/readyz` tells an anonymous caller whether the database is up.
6. **PodDisruptionBudget `maxUnavailable: 1`, rendered only for more than one replica.** A budget on a single replica blocks node drains.
7. **The Ingress host is derived from `publicUrl`** (one value, so they cannot disagree), TLS is required, and `publicUrl` must be `https://<host>` with no port or path.
8. **Sign-in rate limit: a second Ingress for exactly `/auth/login`** that carries its own annotations (`ingress.signIn`). The chart stays neutral about the controller: ingress-nginx is being retired and its annotations are not a standard, so the annotation names are the operator's. The in-app cap from C4a bounds the table whatever the ingress does. **(owner)**: which controller the project documents first.
9. **NetworkPolicy on by default**, selecting every pod of the release. Egress: DNS; Postgres (peers required); and TCP 443 to `0.0.0.0/0` and `::/0` with the refused ranges as `except`. One rule serves leaders and identity providers, because a NetworkPolicy cannot name a DNS host. The chart fails to render without Postgres peers rather than rendering a policy that silently cuts the database off.
10. **`168.63.129.16` (the Azure platform address) is in the `except` list** although the console's own URL check does not refuse it. It is not link-local, serves VM configuration, and no leader lives there. **(owner)**: whether to add it to `leaders.py`'s refused addresses as well (recommended; a one-line change with a test, not part of this plan).
11. **CI: `helm lint --strict`, `kubeconform -strict` on the rendered manifests, and a render check script** (`ci/check_render.py`), in a job named `chart`. The script, not pytest: pytest's `testpaths` is `packages`, and the check needs Helm, which developers' machines do not have. A `kind` install is not part of C4. **(owner)**: recommended as a follow-up once an image is published.
12. **No in-chart Postgres.** The master spec's "optional in-chart Postgres … for evaluation only" belongs to the main chart; the console's Compose test already covers evaluation.
13. **Image reference: no working default, because the image is not published** (C4a ruling 14). `image.repository` and `image.tag` are required values with no default: the render fails with a message if either is empty (`image.digest` wins over the tag, and still needs the repository). The chart's `appVersion` is not used as a tag. The docs say to build the image and load it into the cluster (`docker build -t swarmscribe-console:<tag> -f docker/console.Dockerfile .`, then `kind load docker-image`, `minikube image load`, or a push to the operator's own registry), with `image.pullPolicy: IfNotPresent` or `Never` for a loaded image. Publishing the image (a registry, tags, a version label) is a follow-up, not part of this plan. `ci/test-values.yaml` sets `swarmscribe-console` and `local` so the check can render.
14. **The Ingress routes explicit paths, never a Prefix `/`.** A Prefix `/` would also send `/healthz` and `/readyz` to the internet. The console's own routes are few, so `ingress.paths` lists them: `/` (Exact), and Prefix `/leaders`, `/admin`, `/sign-in`, `/assets`, `/api`, `/auth` (the web app's routes in `packages/console-web/src/App.tsx`, its asset folder, and the two backend prefixes). `check_render.py` applies the Ingress matching rules (Exact, and Prefix by path element) to `/healthz`, `/healthz/`, `/readyz` and `/readyz/` and fails if any is matched. A new web app route needs a new entry; the README says so.
15. **A database outage floods the log unless it is limited** (observed in C4a: about 1,500 lines a minute from the poller). C4a rate-limits the poller's repeated failure to one line per 30 s per cause and `/readyz`'s failure line to one per 30 s. The operations guide says what the lines look like, so an operator reading a quiet log during an outage knows that is by design.

## Review Focus

1. **A first install**: the hook Job runs before the chart's ConfigMap and ServiceAccount exist. It must not reference either, and must carry the same settings as the console — Task 2, `check_migrate`.
2. **One replica**: a PodDisruptionBudget must not be rendered, or node drains hang — Task 4, `check_network`.
3. **Values that would deploy a console that cannot start or is not safe**: no `publicUrl`, an `http://` one, one with a port, no Secret, no sign-in provider, a secret under `settings`, an Ingress without TLS, a NetworkPolicy without Postgres. Each must fail the render with a message — Tasks 1, 3 and 4 (`refused(...)`).
4. **A narrowed egress range**: when `https.cidrs` is not the catch-all, no `except` list may be attached (the API server refuses an `except` outside its `cidr`, and the whole release would fail to install) — Task 4.
5. **Every refused address is actually cut out, and no leader address is**: one address from each refused range, IPv4 and IPv6, including IPv4-mapped and NAT64 forms of the metadata address, checked against the rendered rule; private, CGNAT and public addresses checked reachable — Task 4.
6. **The rate-limit annotations stay on `/auth/login`**: they must not appear on the Ingress for the whole console, where they would throttle the overview's refresh — Task 3.

## File Structure

```
deploy/helm/swarmscribe-console/
  Chart.yaml
  values.yaml                 every value, documented
  .helmignore
  ci/
    test-values.yaml          the values CI lints and renders with
    check_render.py           what the rendered chart must hold
  templates/
    _helpers.tpl              names, labels, validation, settings, security contexts, refused ranges
    serviceaccount.yaml
    configmap.yaml            non-secret settings
    deployment.yaml
    service.yaml
    migrate-job.yaml          Task 2
    ingress.yaml              Task 3
    pdb.yaml                  Task 4
    networkpolicy.yaml        Task 4
    NOTES.txt
```

Also modified: `.github/workflows/ci.yml` (job `chart`), `README.md` and `packages/console/README.md` (Task 5).

### Helm and kubeconform on the Windows machine

Neither is installed, and neither needs installing: fetch the standalone binaries into a scratch folder outside the repository (Git Bash). These are the local runs that replace "CI only":

```bash
mkdir -p /tmp/chart-tools && cd /tmp/chart-tools
curl -sL -o helm.zip https://get.helm.sh/helm-v4.3.0-windows-amd64.zip && unzip -q -o helm.zip
curl -sL -o kubeconform.zip https://github.com/yannh/kubeconform/releases/download/v0.8.0/kubeconform-windows-amd64.zip && unzip -q -o kubeconform.zip
export PATH="/tmp/chart-tools/windows-amd64:/tmp/chart-tools:$PATH"
helm version   # v4.3.0
```

Run that `export` in every shell that runs a `helm` or `kubeconform` command below.

---

### Task 1: The chart's core — settings, Deployment, Service

**Files:**
- Create: `deploy/helm/swarmscribe-console/Chart.yaml`
- Create: `deploy/helm/swarmscribe-console/values.yaml`
- Create: `deploy/helm/swarmscribe-console/.helmignore`
- Create: `deploy/helm/swarmscribe-console/ci/test-values.yaml`
- Create: `deploy/helm/swarmscribe-console/ci/check_render.py`
- Create: `deploy/helm/swarmscribe-console/templates/_helpers.tpl`
- Create: `deploy/helm/swarmscribe-console/templates/serviceaccount.yaml`
- Create: `deploy/helm/swarmscribe-console/templates/configmap.yaml`
- Create: `deploy/helm/swarmscribe-console/templates/deployment.yaml`
- Create: `deploy/helm/swarmscribe-console/templates/service.yaml`
- Create: `deploy/helm/swarmscribe-console/templates/NOTES.txt`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: the C4a image (arguments `serve --host 0.0.0.0 --port 8080`; port 8080; user 10001; `/healthz`, `/readyz`); the `SWARMSCRIBE_CONSOLE_*` settings.
- Produces: named templates used by later tasks — `swarmscribe-console.fullname`, `.labels`, `.selectorLabels`, `.image`, `.host` (the Ingress host, from `publicUrl`), `.validate`, `.settings` (a YAML map of environment names to strings), `.secretEnv` (container `env` entries), `.podSecurityContext`, `.containerSecurityContext`, `.refusedV4`, `.refusedV6` (YAML lists of CIDRs). Pod label `app.kubernetes.io/component: console` on console pods (later: `migrate` on the Job's pod). `ci/check_render.py` with sections `core`, `migrate`, `ingress`, `network` and the flag `--only`. With release name `console`, every object is named `console-swarmscribe-console`.

- [ ] **Step 1: Write the render check (the failing test)**

Create `deploy/helm/swarmscribe-console/ci/check_render.py`. It holds all four sections now; the later tasks make `migrate`, `ingress` and `network` pass.

```python
"""What the rendered swarmscribe-console chart must hold, whatever else changes.

Run from the repository root (CI job `chart` does), with Helm on the PATH:

    uv run python deploy/helm/swarmscribe-console/ci/check_render.py

It renders the chart with `helm template` and ci/test-values.yaml, checks the manifests, then
renders it with values that must be refused. `--only core,migrate` runs some sections only
(core, migrate, ingress, network). Exit status 1 lists every problem."""

import argparse
import ipaddress
import subprocess
import sys
from pathlib import Path

import yaml

CHART = Path(__file__).resolve().parents[1]
VALUES = CHART / "ci" / "test-values.yaml"
NAME = "console-swarmscribe-console"
SECRET_NAMES = {
    "SWARMSCRIBE_CONSOLE_DATABASE_URL",
    "SWARMSCRIBE_CONSOLE_KEY",
    "SWARMSCRIBE_CONSOLE_ENTRA_CLIENT_SECRET",
    "SWARMSCRIBE_CONSOLE_GOOGLE_CLIENT_SECRET",
    "SWARMSCRIBE_CONSOLE_GOOGLE_SERVICE_ACCOUNT",
}
# Addresses the console refuses as leader URLs, one from each refused range (README,
# "Deployment note: egress"), and the Azure platform address. Each must be cut out.
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
# Leaders and identity providers live at addresses like these; none may be cut out.
MUST_BE_ALLOWED = (
    "10.1.2.3",
    "192.168.1.50",
    "172.16.0.9",
    "100.64.0.1",
    "20.190.128.1",
    "fd12::1",
    "2606:4700::1111",
)


def helm_template(*extra: str) -> subprocess.CompletedProcess:
    command = ["helm", "template", "console", str(CHART), "--namespace", "fleet"]
    return subprocess.run(
        [*command, "-f", str(VALUES), *extra], capture_output=True, text=True
    )


def render(*extra: str) -> list[dict]:
    done = helm_template(*extra)
    if done.returncode != 0:
        raise SystemExit(f"helm template failed:\n{done.stderr}")
    return [doc for doc in yaml.safe_load_all(done.stdout) if doc]


def refused(problems: list[str], what: str, *values: str) -> None:
    if helm_template("--set", ",".join(values)).returncode == 0:
        problems.append(f"the chart renders with {what}")


def one(docs: list[dict], kind: str, name: str = NAME) -> dict:
    found = [d for d in docs if d["kind"] == kind and d["metadata"]["name"] == name]
    if len(found) != 1:
        raise SystemExit(f"expected one {kind} named {name}, found {len(found)}")
    return found[0]


def kinds(docs: list[dict]) -> set[str]:
    return {doc["kind"] for doc in docs}


def check_pod(spec: dict, container: str, problems: list[str], where: str) -> dict:
    pod = spec.get("securityContext", {})
    if pod.get("runAsNonRoot") is not True or pod.get("runAsUser") != 10001:
        problems.append(f"{where}: the pod does not run as the non-root user 10001")
    if pod.get("seccompProfile", {}).get("type") != "RuntimeDefault":
        problems.append(f"{where}: no RuntimeDefault seccomp profile")
    if spec.get("automountServiceAccountToken") is not False:
        problems.append(f"{where}: the service-account token is mounted")
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
    for needed in sorted(SECRET_NAMES):
        if needed not in names:
            problems.append(f"{where}: {needed} is missing")
    if "requests" not in found.get("resources", {}):
        problems.append(f"{where}: no resource requests")
    # The image writes nothing at run time (C4a ran it read-only with no tmpfs), so there is
    # no /tmp volume; the check keeps one from coming back out of habit.
    if any(mount["mountPath"] == "/tmp" for mount in found.get("volumeMounts", [])):
        problems.append(f"{where}: a /tmp volume the image does not need")
    return found


def console_labels(docs: list[dict]) -> dict:
    return one(docs, "Deployment")["spec"]["template"]["metadata"]["labels"]


def check_core(docs: list[dict]) -> list[str]:
    problems: list[str] = []
    deployment = one(docs, "Deployment")
    if deployment["spec"]["replicas"] != 2:
        problems.append("Deployment: the default is not 2 replicas")
    console = check_pod(deployment["spec"]["template"]["spec"], "console", problems, "Deployment")
    if console["args"][:1] != ["serve"]:
        problems.append("Deployment: the container does not run `serve`")
    if console["image"] != "swarmscribe-console:local":
        problems.append("Deployment: the image is not image.repository:image.tag")
    for probe, path in (
        ("startupProbe", "/healthz"),
        ("livenessProbe", "/healthz"),
        ("readinessProbe", "/readyz"),
    ):
        if console.get(probe, {}).get("httpGet", {}).get("path") != path:
            problems.append(f"Deployment: {probe} does not ask {path} with GET (httpGet)")
    if console["readinessProbe"].get("timeoutSeconds", 1) < 5:
        problems.append("Deployment: the readiness timeout is under 5 s (the check takes up to 3)")
    if "/readyz" in str(console["startupProbe"]) or "/readyz" in str(console["livenessProbe"]):
        problems.append("Deployment: startup or liveness asks /readyz")
    ca = [e for e in console["env"] if e["name"] == "SWARMSCRIBE_CONSOLE_LEADER_CA_FILE"]
    if not ca or ca[0].get("value") != "/etc/swarmscribe/leader-ca/ca.pem":
        problems.append("Deployment: the leader CA file is not set from leaderCa")

    settings = one(docs, "ConfigMap")["data"]
    if settings.get("SWARMSCRIBE_CONSOLE_PUBLIC_URL") != "https://console.example.org":
        problems.append("ConfigMap: the public URL is not publicUrl")
    if settings.get("SWARMSCRIBE_CONSOLE_POLL_CONCURRENCY") != "8":
        problems.append("ConfigMap: `settings` are not passed on as strings")
    for name in settings:
        if name in SECRET_NAMES or name.endswith("_SECRET"):
            problems.append(f"ConfigMap: {name} is a secret")

    selector = one(docs, "Service")["spec"]["selector"]
    labels = console_labels(docs)
    if any(labels.get(key) != value for key, value in selector.items()):
        problems.append("Service: its selector does not match the console pods")
    if "app.kubernetes.io/component" not in selector:
        problems.append("Service: its selector would also match the migration pod")
    if "Secret" in kinds(docs):
        problems.append("the chart renders a Secret; it must only reference one")

    refused(problems, "no image repository", "image.repository=")
    refused(problems, "no image tag", "image.tag=")
    refused(problems, "no publicUrl", "publicUrl=")
    refused(problems, "an http publicUrl", "publicUrl=http://console.example.org")
    refused(problems, "a publicUrl with a port", "publicUrl=https://console.example.org:8443")
    refused(problems, "no Secret", "secrets.existingSecret=")
    refused(
        problems, "no sign-in provider", "oidc.entra.enabled=false", "oidc.google.enabled=false"
    )
    refused(problems, "the console key under settings", "settings.KEY=abc")
    refused(problems, "a client secret under settings", "settings.ENTRA_CLIENT_SECRET=abc")
    pinned = one(render("--set", "image.digest=sha256:abc"), "Deployment")
    image = pinned["spec"]["template"]["spec"]["containers"][0]["image"]
    if image != "swarmscribe-console@sha256:abc":
        problems.append("Deployment: image.digest does not win over the tag")
    return problems


def check_migrate(docs: list[dict]) -> list[str]:
    problems: list[str] = []
    job = one(docs, "Job", f"{NAME}-migrate")
    hooks = job["metadata"].get("annotations", {}).get("helm.sh/hook", "")
    if set(hooks.split(",")) != {"pre-install", "pre-upgrade"}:
        problems.append("Job: not a pre-install and pre-upgrade hook")
    spec = job["spec"]["template"]["spec"]
    migrate = check_pod(spec, "migrate", problems, "Job")
    if migrate["args"] != ["migrate"]:
        problems.append("Job: the container does not run `migrate`")
    if spec.get("restartPolicy") != "Never":
        problems.append("Job: its pod restarts in place")
    # A hook runs before the release's own objects exist on a first install.
    if migrate.get("envFrom"):
        problems.append("Job: reads the ConfigMap, which does not exist before the first install")
    if spec.get("serviceAccountName"):
        problems.append("Job: names the ServiceAccount, which does not exist before the install")
    inline = {entry["name"]: entry.get("value") for entry in migrate["env"]}
    for name, value in one(docs, "ConfigMap")["data"].items():
        if inline.get(name) != value:
            problems.append(f"Job: {name} differs from the console's setting")
    console = one(docs, "Deployment")["spec"]["template"]["spec"]["containers"][0]
    if migrate["image"] != console["image"]:
        problems.append("Job: migrates with another image than the console's")
    if "Job" in kinds(render("--set", "migrate.enabled=false")):
        problems.append("a disabled migration Job is still rendered")
    return problems


def routes(entry: dict, request_path: str) -> bool:
    """Whether an Ingress path entry would route `request_path`: Exact is a string match;
    Prefix matches by whole path elements (/leaders matches /leaders/x, not /leaders-x), and
    a Prefix of / matches everything."""
    path = entry["path"]
    if entry["pathType"] == "Exact":
        return request_path == path
    if path == "/":
        return True
    wanted = [part for part in path.split("/") if part]
    got = [part for part in request_path.split("/") if part]
    return got[: len(wanted)] == wanted


def check_ingress(docs: list[dict]) -> list[str]:
    problems: list[str] = []
    ingress = one(docs, "Ingress")
    if ingress["spec"]["tls"] != [{"hosts": ["console.example.org"], "secretName": "console-tls"}]:
        problems.append("Ingress: TLS is not for the public URL's host")
    if ingress["spec"]["rules"][0]["host"] != "console.example.org":
        problems.append("Ingress: the host is not the public URL's")
    for probe in ("/healthz", "/healthz/", "/readyz", "/readyz/"):
        for rule in ingress["spec"]["rules"]:
            for entry in rule["http"]["paths"]:
                if routes(entry, probe):
                    problems.append(f"Ingress: {entry['path']} ({entry['pathType']}) routes {probe}")
    limit = "nginx.ingress.kubernetes.io/limit-rpm"
    if limit in ingress["metadata"].get("annotations", {}):
        problems.append("Ingress: the sign-in annotations are on the whole console")
    sign_in = one(docs, "Ingress", f"{NAME}-sign-in")
    path = sign_in["spec"]["rules"][0]["http"]["paths"][0]
    if (path["path"], path["pathType"]) != ("/auth/login", "Exact"):
        problems.append("sign-in Ingress: not exactly /auth/login")
    if limit not in sign_in["metadata"].get("annotations", {}):
        problems.append("sign-in Ingress: its annotations are missing")
    if sign_in["spec"]["tls"] != ingress["spec"]["tls"]:
        problems.append("sign-in Ingress: its TLS differs from the console's")
    refused(problems, "an Ingress without TLS", "ingress.tls.secretName=")
    if "Ingress" in kinds(render("--set", "ingress.enabled=false")):
        problems.append("a disabled Ingress is still rendered")
    without = render("--set", "ingress.signIn.enabled=false")
    if len([doc for doc in without if doc["kind"] == "Ingress"]) != 1:
        problems.append("the sign-in Ingress is rendered when it is off")
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
    found = [
        rule
        for rule in policy["egress"]
        if all("ipBlock" in peer for peer in rule["to"])
        and {"protocol": "TCP", "port": 443} in rule["ports"]
    ]
    if len(found) != 1:
        raise SystemExit(f"expected one https egress rule, found {len(found)}")
    return found[0]


def check_network(docs: list[dict]) -> list[str]:
    problems: list[str] = []
    budget = one(docs, "PodDisruptionBudget")
    if budget["spec"].get("maxUnavailable") != 1:
        problems.append("PodDisruptionBudget: not maxUnavailable 1")
    labels = console_labels(docs)
    if any(labels.get(k) != v for k, v in budget["spec"]["selector"]["matchLabels"].items()):
        problems.append("PodDisruptionBudget: does not select the console pods")
    if "PodDisruptionBudget" in kinds(render("--set", "replicaCount=1")):
        problems.append("a PodDisruptionBudget is rendered for one replica (it blocks drains)")

    policy = one(docs, "NetworkPolicy")["spec"]
    if sorted(policy["policyTypes"]) != ["Egress", "Ingress"]:
        problems.append("NetworkPolicy: does not cover both directions")
    selector = policy["podSelector"]["matchLabels"]
    if any(labels.get(k) != v for k, v in selector.items()):
        problems.append("NetworkPolicy: does not select the console pods")
    if "app.kubernetes.io/component" in selector:
        problems.append("NetworkPolicy: leaves the migration pod out")
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
    ports = [port for rule in policy["egress"] for port in rule["ports"]]
    if {"protocol": "UDP", "port": 53} not in ports:
        problems.append("NetworkPolicy: DNS is not allowed")
    if {"protocol": "TCP", "port": 5432} not in ports:
        problems.append("NetworkPolicy: Postgres is not allowed")
    if policy["ingress"][0]["ports"] != [{"protocol": "TCP", "port": 8080}]:
        problems.append("NetworkPolicy: ingress is not limited to the console's port")

    # The API server refuses an `except` outside its `cidr`: a narrowed range gets none.
    narrowed = render("--set", "networkPolicy.egress.https.cidrs={10.0.0.0/8}")
    for peer in https_rule(one(narrowed, "NetworkPolicy")["spec"])["to"]:
        if "except" in peer["ipBlock"]:
            problems.append("NetworkPolicy: a narrowed range carries the catch-all's except list")
    refused(
        problems, "a NetworkPolicy without Postgres", "networkPolicy.egress.postgres.peers=null"
    )
    if "NetworkPolicy" in kinds(render("--set", "networkPolicy.enabled=false")):
        problems.append("a disabled NetworkPolicy is still rendered")
    return problems


SECTIONS = {
    "core": check_core,
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

Create `deploy/helm/swarmscribe-console/ci/test-values.yaml`:

```yaml
# Values the chart is linted and rendered with, locally and in CI. Nothing here is a real secret.
publicUrl: https://console.example.org
secrets:
  existingSecret: swarmscribe-console
oidc:
  entra:
    enabled: true
    tenantId: 0f0e0d0c-0b0a-4908-8706-050403020100
    clientId: 6d3a6b52-1c2e-4a8f-9d61-2f6a4c0b7e11
  google:
    enabled: true
    clientId: console.apps.googleusercontent.com
    hostedDomain: example.org
    serviceAccount: true
image:
  repository: swarmscribe-console
  tag: local
settings:
  POLL_CONCURRENCY: "8"
leaderCa:
  existingConfigMap: leader-ca
ingress:
  className: nginx
  tls:
    secretName: console-tls
  signIn:
    enabled: true
    annotations:
      nginx.ingress.kubernetes.io/limit-rpm: "30"
networkPolicy:
  egress:
    postgres:
      peers:
        - ipBlock:
            cidr: 10.20.30.40/32
    https:
      extraExcept: ["10.96.0.0/12", "fd00:10:96::/112"]
```

- [ ] **Step 2: Run it to see it fail**

Run: `uv run --no-project --with pyyaml python deploy/helm/swarmscribe-console/ci/check_render.py --only core`
Expected: exit status 1 with `helm template failed:` and `Error: unable to detect chart at …/Chart.yaml`.

- [ ] **Step 3: Write the chart's metadata and values**

Create `deploy/helm/swarmscribe-console/Chart.yaml`:

```yaml
apiVersion: v2
name: swarmscribe-console
description: The SwarmScribe fleet console - one web console for many leader deployments
type: application
version: 0.1.0
appVersion: "0.1.0"
kubeVersion: ">=1.25.0-0"
```

Create `deploy/helm/swarmscribe-console/.helmignore`:

```text
# Not part of the packaged chart: the values and the check that CI renders it with.
ci/
```

Create `deploy/helm/swarmscribe-console/values.yaml` (complete now; the templates of Tasks 2 to 4 read the parts that Task 1's do not):

```yaml
# SwarmScribe fleet console. The three things you must set are `publicUrl`,
# `secrets.existingSecret` and one of `oidc.entra` / `oidc.google`; with the
# NetworkPolicy on (the default) also `networkPolicy.egress.postgres.peers`.

# Console replicas. Each replica runs its own poller (advisory locks keep one poll
# per leader) and opens up to 2 * POLL_CONCURRENCY + 2 + 10 Postgres connections:
# 28 at the default concurrency of 8. Size the database's max_connections for
# replicaCount times that, plus one for the migration job.
replicaCount: 2

# The image is not published: there is no working default. Build it and load it into
# the cluster (README, "The image"), then name it here. Both are required.
image:
  repository: ""
  tag: ""
  # "sha256:..." pins the image by digest and wins over the tag.
  digest: ""
  pullPolicy: IfNotPresent
imagePullSecrets: []

# The console's https origin, e.g. https://console.example.org: no path, no port.
# It is SWARMSCRIBE_CONSOLE_PUBLIC_URL, the Ingress host, and the origin the
# identity provider redirects back to (<publicUrl>/auth/callback).
publicUrl: ""

# The chart never creates a Secret: values files and Helm's release history are
# not a place for the console key. Create one Secret yourself and name it here.
secrets:
  existingSecret: ""
  keys:
    # SWARMSCRIBE_CONSOLE_DATABASE_URL: the console's own Postgres (14 or later),
    # e.g. postgresql://console:...@db.internal:5432/swarmscribe_console
    databaseUrl: database-url
    # SWARMSCRIBE_CONSOLE_KEY: 32 random bytes, URL-safe base64
    # (python -c "import secrets; print(secrets.token_urlsafe(32))").
    # It seals every leader credential; losing it means re-entering them all.
    consoleKey: console-key
    # Read only when oidc.entra.enabled.
    entraClientSecret: entra-client-secret
    # Read only when oidc.google.enabled.
    googleClientSecret: google-client-secret
    # Read only when oidc.google.serviceAccount: the service account's JSON key.
    googleServiceAccount: google-service-account

# Sign-in. Enable Entra ID, Google, or both.
oidc:
  entra:
    enabled: false
    # The tenant's GUID (not its domain name).
    tenantId: ""
    clientId: ""
  google:
    enabled: false
    clientId: ""
    # Optional: only this Google Workspace domain may sign in.
    hostedDomain: ""
    # true reads the Google Groups service-account key from the Secret.
    serviceAccount: false

# Other non-secret settings, without the SWARMSCRIBE_CONSOLE_ prefix, for example
#   POLL_CONCURRENCY: "8"
#   LOGIN_ATTEMPTS_MAX: "10000"
#   SESSION_IDLE_SECONDS: "3600"
# Secrets are refused here (DATABASE_URL, KEY, *_CLIENT_SECRET, GOOGLE_SERVICE_ACCOUNT).
settings: {}

# Leaders whose TLS certificates come from a private CA: a ConfigMap you created
# that holds the CA certificates as PEM. They are trusted in addition to the
# public roots, and only for calls to leaders (SWARMSCRIBE_CONSOLE_LEADER_CA_FILE).
leaderCa:
  existingConfigMap: ""
  key: ca.pem

# Extra environment for the console container, as a list of {name, value|valueFrom}.
# HTTPS_PROXY here applies to identity-provider calls only: calls to leaders never
# use a proxy.
extraEnv: []

service:
  type: ClusterIP
  port: 80

ingress:
  enabled: true
  className: ""
  annotations: {}
  tls:
    # Required when the Ingress is enabled: the console is served over TLS only.
    # cert-manager can create it from an annotation above.
    secretName: ""
  # The paths sent to the console. Never a Prefix "/": it would also publish /healthz and
  # /readyz (the probes; /readyz tells an anonymous caller whether the database is up).
  # These are the web app's routes (packages/console-web/src/App.tsx), its asset folder and
  # the backend's /api and /auth. A new web app route needs a new entry here.
  paths:
    - {path: /, pathType: Exact}
    - {path: /leaders, pathType: Prefix}
    - {path: /admin, pathType: Prefix}
    - {path: /sign-in, pathType: Prefix}
    - {path: /assets, pathType: Prefix}
    - {path: /api, pathType: Prefix}
    - {path: /auth, pathType: Prefix}
  # A second Ingress for exactly /auth/login, so that a per-client rate limit can
  # be put on starting a sign-in without limiting the rest of the console. The
  # annotations are your ingress controller's own (see the README for examples).
  signIn:
    enabled: false
    annotations: {}

# The schema migration: a Job run as a pre-install and pre-upgrade hook, before
# any console pod of the new version starts.
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

podDisruptionBudget:
  # Rendered only when replicaCount is above 1.
  enabled: true
  maxUnavailable: 1

networkPolicy:
  # Needs a network plugin that enforces NetworkPolicy (Calico, Cilium, ...).
  # Without one this object is accepted and does nothing.
  enabled: true
  ingress:
    # Peers allowed to reach the console's port (NetworkPolicyPeer objects), for
    # example your ingress controller's namespace. Empty: any source.
    from: []
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
      # Required: where the console's Postgres is (NetworkPolicyPeer objects), e.g.
      #   - ipBlock: {cidr: 10.20.30.40/32}
      peers: []
    https:
      # Leaders and identity providers. NetworkPolicy cannot name a DNS host, so
      # the default is "anywhere on these ports except the refused ranges".
      # Narrow `cidrs` to your leaders' networks if identity-provider calls go
      # through a proxy (extraEnv HTTPS_PROXY); list the proxy under `extra`.
      ports: [443]
      cidrs: ["0.0.0.0/0", "::/0"]
      # More ranges to cut out of 0.0.0.0/0 and ::/0, for example the cluster's
      # own pod and service ranges when every leader is outside the cluster.
      extraExcept: []
    # Extra raw egress rules (NetworkPolicyEgressRule objects).
    extra: []

serviceAccount:
  create: true
  name: ""

podAnnotations: {}
podLabels: {}
nodeSelector: {}
tolerations: []
affinity: {}
topologySpreadConstraints: []
terminationGracePeriodSeconds: 30
```

- [ ] **Step 4: Write the helpers**

Create `deploy/helm/swarmscribe-console/templates/_helpers.tpl`:

```yaml
{{- define "swarmscribe-console.name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "swarmscribe-console.fullname" -}}
{{- if contains .Chart.Name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{- define "swarmscribe-console.selectorLabels" -}}
app.kubernetes.io/name: {{ include "swarmscribe-console.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "swarmscribe-console.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{ include "swarmscribe-console.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "swarmscribe-console.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "swarmscribe-console.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{- define "swarmscribe-console.image" -}}
{{- $repository := required "image.repository is required: the console image is not published; build it and load it into the cluster (see the README)" .Values.image.repository }}
{{- if .Values.image.digest }}
{{- printf "%s@%s" $repository .Values.image.digest }}
{{- else }}
{{- printf "%s:%s" $repository (required "image.tag is required: the console image is not published; build it and name its tag (see the README)" .Values.image.tag) }}
{{- end }}
{{- end }}

{{/* The Ingress host: publicUrl without its scheme. publicUrl is an https origin with no
port and no path. */}}
{{- define "swarmscribe-console.host" -}}
{{- $url := required "publicUrl is required: the console's https origin, e.g. https://console.example.org" .Values.publicUrl }}
{{- if not (hasPrefix "https://" $url) }}
{{- fail "publicUrl must start with https://" }}
{{- end }}
{{- $host := trimPrefix "https://" $url }}
{{- if or (contains "/" $host) (contains ":" $host) (eq $host "") }}
{{- fail "publicUrl is an origin on the default port: https://<host>, with no port and no path" }}
{{- end }}
{{- $host }}
{{- end }}

{{/* Fails the render on values that would deploy a console that cannot start. */}}
{{- define "swarmscribe-console.validate" -}}
{{- $_ := include "swarmscribe-console.host" . }}
{{- $_ := required "secrets.existingSecret is required: the Secret holding the database URL and the console key" .Values.secrets.existingSecret }}
{{- if not (or .Values.oidc.entra.enabled .Values.oidc.google.enabled) }}
{{- fail "enable oidc.entra or oidc.google (or both): the console has no other sign-in" }}
{{- end }}
{{- range $name, $_ := .Values.settings }}
{{- if or (has $name (list "DATABASE_URL" "KEY" "GOOGLE_SERVICE_ACCOUNT" "PUBLIC_URL" "LEADER_CA_FILE")) (hasSuffix "_SECRET" $name) }}
{{- fail (printf "settings.%s is not set here: secrets come from secrets.existingSecret, and PUBLIC_URL and LEADER_CA_FILE from publicUrl and leaderCa" $name) }}
{{- end }}
{{- end }}
{{- if and .Values.ingress.enabled (not .Values.ingress.tls.secretName) }}
{{- fail "ingress.tls.secretName is required: the console is served over TLS only" }}
{{- end }}
{{- if and .Values.networkPolicy.enabled (not .Values.networkPolicy.egress.postgres.peers) }}
{{- fail "networkPolicy.egress.postgres.peers is required with the NetworkPolicy on: say where the console's Postgres is" }}
{{- end }}
{{- end }}

{{/* Non-secret settings as a YAML map of environment names to strings. */}}
{{- define "swarmscribe-console.settings" -}}
SWARMSCRIBE_CONSOLE_PUBLIC_URL: {{ .Values.publicUrl | quote }}
{{- if .Values.oidc.entra.enabled }}
SWARMSCRIBE_CONSOLE_ENTRA_TENANT_ID: {{ required "oidc.entra.tenantId is required with oidc.entra.enabled" .Values.oidc.entra.tenantId | quote }}
SWARMSCRIBE_CONSOLE_ENTRA_CLIENT_ID: {{ required "oidc.entra.clientId is required with oidc.entra.enabled" .Values.oidc.entra.clientId | quote }}
{{- end }}
{{- if .Values.oidc.google.enabled }}
SWARMSCRIBE_CONSOLE_GOOGLE_CLIENT_ID: {{ required "oidc.google.clientId is required with oidc.google.enabled" .Values.oidc.google.clientId | quote }}
{{- with .Values.oidc.google.hostedDomain }}
SWARMSCRIBE_CONSOLE_GOOGLE_HOSTED_DOMAIN: {{ . | quote }}
{{- end }}
{{- end }}
{{- range $name, $value := .Values.settings }}
SWARMSCRIBE_CONSOLE_{{ $name }}: {{ $value | toString | quote }}
{{- end }}
{{- end }}

{{/* Secret settings, as container env entries reading the existing Secret. */}}
{{- define "swarmscribe-console.secretEnv" -}}
{{- $secret := .Values.secrets.existingSecret -}}
- name: SWARMSCRIBE_CONSOLE_DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: {{ .Values.secrets.keys.databaseUrl }}
- name: SWARMSCRIBE_CONSOLE_KEY
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: {{ .Values.secrets.keys.consoleKey }}
{{- if .Values.oidc.entra.enabled }}
- name: SWARMSCRIBE_CONSOLE_ENTRA_CLIENT_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: {{ .Values.secrets.keys.entraClientSecret }}
{{- end }}
{{- if .Values.oidc.google.enabled }}
- name: SWARMSCRIBE_CONSOLE_GOOGLE_CLIENT_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: {{ .Values.secrets.keys.googleClientSecret }}
{{- if .Values.oidc.google.serviceAccount }}
- name: SWARMSCRIBE_CONSOLE_GOOGLE_SERVICE_ACCOUNT
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: {{ .Values.secrets.keys.googleServiceAccount }}
{{- end }}
{{- end }}
{{- end }}

{{- define "swarmscribe-console.podSecurityContext" -}}
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
seccompProfile:
  type: RuntimeDefault
{{- end }}

{{- define "swarmscribe-console.containerSecurityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: ["ALL"]
{{- end }}

{{/* Destinations the console refuses as leader addresses (README, "Deployment note:
egress"), cut out of 0.0.0.0/0: this network, Alibaba Cloud metadata, loopback, the Azure
platform address, link-local (cloud metadata), multicast and reserved. */}}
{{- define "swarmscribe-console.refusedV4" -}}
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
{{- define "swarmscribe-console.refusedV6" -}}
- ::/8
- 2001::/32
- 2002::/16
- fd00:ec2::254/128
- fe80::/10
- fec0::/10
- ff00::/8
{{- end }}
```

- [ ] **Step 5: Write the ServiceAccount, ConfigMap, Deployment, Service and notes**

Create `deploy/helm/swarmscribe-console/templates/serviceaccount.yaml`:

```yaml
{{- if .Values.serviceAccount.create }}
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {{ include "swarmscribe-console.serviceAccountName" . }}
  labels:
    {{- include "swarmscribe-console.labels" . | nindent 4 }}
# The console never calls the Kubernetes API.
automountServiceAccountToken: false
{{- end }}
```

Create `deploy/helm/swarmscribe-console/templates/configmap.yaml`:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ include "swarmscribe-console.fullname" . }}
  labels:
    {{- include "swarmscribe-console.labels" . | nindent 4 }}
data:
  {{- include "swarmscribe-console.settings" . | nindent 2 }}
```

Create `deploy/helm/swarmscribe-console/templates/deployment.yaml`:

```yaml
{{- include "swarmscribe-console.validate" . }}
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "swarmscribe-console.fullname" . }}
  labels:
    {{- include "swarmscribe-console.labels" . | nindent 4 }}
    app.kubernetes.io/component: console
spec:
  replicas: {{ .Values.replicaCount }}
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxUnavailable: 0
      maxSurge: 1
  selector:
    matchLabels:
      {{- include "swarmscribe-console.selectorLabels" . | nindent 6 }}
      app.kubernetes.io/component: console
  template:
    metadata:
      labels:
        {{- include "swarmscribe-console.labels" . | nindent 8 }}
        app.kubernetes.io/component: console
        {{- with .Values.podLabels }}
        {{- toYaml . | nindent 8 }}
        {{- end }}
      annotations:
        # A changed setting restarts the pods.
        checksum/settings: {{ include "swarmscribe-console.settings" . | sha256sum }}
        {{- with .Values.podAnnotations }}
        {{- toYaml . | nindent 8 }}
        {{- end }}
    spec:
      serviceAccountName: {{ include "swarmscribe-console.serviceAccountName" . }}
      automountServiceAccountToken: false
      terminationGracePeriodSeconds: {{ .Values.terminationGracePeriodSeconds }}
      {{- with .Values.imagePullSecrets }}
      imagePullSecrets:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      securityContext:
        {{- include "swarmscribe-console.podSecurityContext" . | nindent 8 }}
      containers:
        - name: console
          image: {{ include "swarmscribe-console.image" . | quote }}
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          args: ["serve", "--host", "0.0.0.0", "--port", "8080"]
          ports:
            - name: http
              containerPort: 8080
              protocol: TCP
          envFrom:
            - configMapRef:
                name: {{ include "swarmscribe-console.fullname" . }}
          env:
            {{- include "swarmscribe-console.secretEnv" . | nindent 12 }}
            {{- if .Values.leaderCa.existingConfigMap }}
            - name: SWARMSCRIBE_CONSOLE_LEADER_CA_FILE
              value: /etc/swarmscribe/leader-ca/{{ .Values.leaderCa.key }}
            {{- end }}
            {{- with .Values.extraEnv }}
            {{- toYaml . | nindent 12 }}
            {{- end }}
          securityContext:
            {{- include "swarmscribe-console.containerSecurityContext" . | nindent 12 }}
          # httpGet is GET, which is what the console's probes are for. /healthz: the process
          # answers (startup and liveness; never /readyz). /readyz: the database answers and
          # its schema is this console's or newer; it gives up on the database after 3 s, so the
          # timeout is 5 s or more. Neither asks an identity provider or a leader.
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
            periodSeconds: 10
            timeoutSeconds: 5
            failureThreshold: 3
          resources:
            {{- toYaml .Values.resources | nindent 12 }}
          {{- if .Values.leaderCa.existingConfigMap }}
          volumeMounts:
            - name: leader-ca
              mountPath: /etc/swarmscribe/leader-ca
              readOnly: true
          {{- end }}
      {{- if .Values.leaderCa.existingConfigMap }}
      volumes:
        - name: leader-ca
          configMap:
            name: {{ .Values.leaderCa.existingConfigMap }}
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
      {{- with .Values.topologySpreadConstraints }}
      topologySpreadConstraints:
        {{- toYaml . | nindent 8 }}
      {{- end }}
```

Create `deploy/helm/swarmscribe-console/templates/service.yaml`:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: {{ include "swarmscribe-console.fullname" . }}
  labels:
    {{- include "swarmscribe-console.labels" . | nindent 4 }}
spec:
  type: {{ .Values.service.type }}
  selector:
    {{- include "swarmscribe-console.selectorLabels" . | nindent 4 }}
    app.kubernetes.io/component: console
  ports:
    - name: http
      port: {{ .Values.service.port }}
      targetPort: http
      protocol: TCP
```

Create `deploy/helm/swarmscribe-console/templates/NOTES.txt`:

```text
The SwarmScribe fleet console is at {{ .Values.publicUrl }}

1. Register {{ .Values.publicUrl }}/auth/callback as the redirect URI of the sign-in
   application(s) at your identity provider.

2. Add the first console administrator (once). Entra ID sign-in takes a group's object id;
   Google sign-in takes an email or a Workspace domain (`admins add email you@example.org`);
   an Entra-only console refuses `email`, so never use it there:

   kubectl -n {{ .Release.Namespace }} exec deploy/{{ include "swarmscribe-console.fullname" . }} --      swarmscribe-console admins add entra_group <group-object-id>

3. On each leader, create a credential for this console, then register the leader here:

   swarmscribe-admin console create --name fleet --max-role operator
{{- if .Values.networkPolicy.enabled }}

A NetworkPolicy limits what the console can reach. It needs a network plugin that enforces
NetworkPolicy; check that yours does.
{{- end }}
```

- [ ] **Step 6: Run the check, the linter and the schema validation to see them pass**

```bash
uv run --no-project --with pyyaml python deploy/helm/swarmscribe-console/ci/check_render.py --only core
helm lint deploy/helm/swarmscribe-console -f deploy/helm/swarmscribe-console/ci/test-values.yaml --strict
helm template console deploy/helm/swarmscribe-console --namespace fleet -f deploy/helm/swarmscribe-console/ci/test-values.yaml | kubeconform -strict -summary -kubernetes-version 1.33.0 -
```

Expected: `the rendered chart holds every required property (core)`; `1 chart(s) linted, 0 chart(s) failed` (an `[INFO] Chart.yaml: icon is recommended` line is fine); `Valid: 4, Invalid: 0, Errors: 0, Skipped: 0`.

Also confirm the refusals read well: `helm template console deploy/helm/swarmscribe-console` (no values) must end with `publicUrl is required: the console's https origin, e.g. https://console.example.org`.

Run: `uv run ruff check deploy`
Expected: no errors.

- [ ] **Step 7: Add the CI job**

Append to `.github/workflows/ci.yml`, under `jobs:`:

```yaml
  chart:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - uses: azure/setup-helm@v5
        with:
          version: v4.3.0
      - name: Lint the console chart
        run: >-
          helm lint deploy/helm/swarmscribe-console
          -f deploy/helm/swarmscribe-console/ci/test-values.yaml --strict
      - name: Validate the rendered manifests against the Kubernetes schemas
        run: |
          curl -fsSL https://github.com/yannh/kubeconform/releases/download/v0.8.0/kubeconform-linux-amd64.tar.gz \
            | tar -xz -C "$RUNNER_TEMP" kubeconform
          helm template console deploy/helm/swarmscribe-console --namespace fleet \
            -f deploy/helm/swarmscribe-console/ci/test-values.yaml > "$RUNNER_TEMP/rendered.yaml"
          "$RUNNER_TEMP/kubeconform" -strict -summary -kubernetes-version 1.33.0 "$RUNNER_TEMP/rendered.yaml"
      - name: Check what the chart renders
        run: >-
          uv run --no-project --with pyyaml
          python deploy/helm/swarmscribe-console/ci/check_render.py --only core
```

- [ ] **Step 8: Commit**

```bash
git add deploy/helm/swarmscribe-console .github/workflows/ci.yml
git commit -m "Console Helm chart: settings, Deployment, Service, and a render check in CI"
```

---

### Task 2: The migration Job

**Files:**
- Create: `deploy/helm/swarmscribe-console/templates/migrate-job.yaml`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `swarmscribe-console.settings` (as a map, through `fromYaml`), `.secretEnv`, `.image`, `.podSecurityContext`, `.containerSecurityContext`, `.labels`; values `migrate.*`. The image's `migrate` argument, which reads the full `SWARMSCRIBE_CONSOLE_*` configuration (as `serve` does).
- Produces: a Job `console-swarmscribe-console-migrate` whose pod is labelled `app.kubernetes.io/component: migrate` and carries the release's selector labels (Task 4's NetworkPolicy selects it by those).

- [ ] **Step 1: Run the check to see it fail**

Run: `uv run --no-project --with pyyaml python deploy/helm/swarmscribe-console/ci/check_render.py --only migrate`
Expected: exit status 1 with `expected one Job named console-swarmscribe-console-migrate, found 0`.

- [ ] **Step 2: Write the Job**

Create `deploy/helm/swarmscribe-console/templates/migrate-job.yaml`:

```yaml
{{- if .Values.migrate.enabled }}
# Runs `swarmscribe-console migrate` before any pod of this release starts (install) or is
# replaced (upgrade). A hook runs before the release's ConfigMap and ServiceAccount exist,
# so the Job carries its settings inline and uses the namespace's default service account.
apiVersion: batch/v1
kind: Job
metadata:
  name: {{ include "swarmscribe-console.fullname" . }}-migrate
  labels:
    {{- include "swarmscribe-console.labels" . | nindent 4 }}
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
        {{- include "swarmscribe-console.labels" . | nindent 8 }}
        app.kubernetes.io/component: migrate
    spec:
      restartPolicy: Never
      automountServiceAccountToken: false
      {{- with .Values.imagePullSecrets }}
      imagePullSecrets:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      securityContext:
        {{- include "swarmscribe-console.podSecurityContext" . | nindent 8 }}
      containers:
        - name: migrate
          image: {{ include "swarmscribe-console.image" . | quote }}
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          args: ["migrate"]
          env:
            {{- range $name, $value := (include "swarmscribe-console.settings" . | fromYaml) }}
            - name: {{ $name }}
              value: {{ $value | quote }}
            {{- end }}
            {{- include "swarmscribe-console.secretEnv" . | nindent 12 }}
          securityContext:
            {{- include "swarmscribe-console.containerSecurityContext" . | nindent 12 }}
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

`SWARMSCRIBE_CONSOLE_LEADER_CA_FILE` is deliberately absent: it is set on the Deployment's container only (with its volume), and `migrate` calls no leader.

- [ ] **Step 3: Run the check, lint and validation to see them pass**

```bash
uv run --no-project --with pyyaml python deploy/helm/swarmscribe-console/ci/check_render.py --only core,migrate
helm lint deploy/helm/swarmscribe-console -f deploy/helm/swarmscribe-console/ci/test-values.yaml --strict
helm template console deploy/helm/swarmscribe-console --namespace fleet -f deploy/helm/swarmscribe-console/ci/test-values.yaml | kubeconform -strict -summary -kubernetes-version 1.33.0 -
```

Expected: `… (core, migrate)`; lint clean; `Valid: 5`.

- [ ] **Step 4: Widen the CI step and commit**

In `.github/workflows/ci.yml`, job `chart`, change `--only core` to `--only core,migrate`.

```bash
git add deploy/helm/swarmscribe-console/templates/migrate-job.yaml .github/workflows/ci.yml
git commit -m "Console chart: run the migration as a pre-install and pre-upgrade hook"
```

---

### Task 3: The Ingress, with TLS, and the sign-in Ingress

**Files:**
- Create: `deploy/helm/swarmscribe-console/templates/ingress.yaml`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `swarmscribe-console.host`, `.fullname`, `.labels`; values `ingress.enabled`, `.className`, `.annotations`, `.tls.secretName`, `.signIn.enabled`, `.signIn.annotations`. The Service's port name `http`.
- Produces: Ingress `console-swarmscribe-console` (the explicit `ingress.paths`: `/` Exact and the web app's and API's prefixes; never a Prefix `/`, which would publish `/healthz` and `/readyz`) and, when `ingress.signIn.enabled`, Ingress `console-swarmscribe-console-sign-in` (path `/auth/login`, `Exact`) with `ingress.annotations` plus `ingress.signIn.annotations` (the latter win).

- [ ] **Step 1: Run the check to see it fail**

Run: `uv run --no-project --with pyyaml python deploy/helm/swarmscribe-console/ci/check_render.py --only ingress`
Expected: exit status 1 with `expected one Ingress named console-swarmscribe-console, found 0`.

- [ ] **Step 2: Write the Ingress**

Create `deploy/helm/swarmscribe-console/templates/ingress.yaml`:

```yaml
{{- if .Values.ingress.enabled }}
{{- $host := include "swarmscribe-console.host" . }}
{{- $service := include "swarmscribe-console.fullname" . }}
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: {{ $service }}
  labels:
    {{- include "swarmscribe-console.labels" . | nindent 4 }}
  {{- with .Values.ingress.annotations }}
  annotations:
    {{- toYaml . | nindent 4 }}
  {{- end }}
spec:
  {{- with .Values.ingress.className }}
  ingressClassName: {{ . }}
  {{- end }}
  tls:
    - hosts: [{{ $host | quote }}]
      secretName: {{ .Values.ingress.tls.secretName }}
  rules:
    - host: {{ $host | quote }}
      http:
        paths:
          {{- range .Values.ingress.paths }}
          - path: {{ .path }}
            pathType: {{ .pathType }}
            backend:
              service:
                name: {{ $service }}
                port:
                  name: http
          {{- end }}
{{- if .Values.ingress.signIn.enabled }}
---
# Exactly /auth/login, so the annotations here (a per-client rate limit) apply to starting
# a sign-in and to nothing else. An Exact path wins over the Prefix "/" above.
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: {{ $service }}-sign-in
  labels:
    {{- include "swarmscribe-console.labels" . | nindent 4 }}
  {{- with (merge (dict) .Values.ingress.signIn.annotations .Values.ingress.annotations) }}
  annotations:
    {{- toYaml . | nindent 4 }}
  {{- end }}
spec:
  {{- with .Values.ingress.className }}
  ingressClassName: {{ . }}
  {{- end }}
  tls:
    - hosts: [{{ $host | quote }}]
      secretName: {{ .Values.ingress.tls.secretName }}
  rules:
    - host: {{ $host | quote }}
      http:
        paths:
          - path: /auth/login
            pathType: Exact
            backend:
              service:
                name: {{ $service }}
                port:
                  name: http
{{- end }}
{{- end }}
```

- [ ] **Step 3: Run the check, lint and validation to see them pass**

```bash
uv run --no-project --with pyyaml python deploy/helm/swarmscribe-console/ci/check_render.py --only core,migrate,ingress
helm lint deploy/helm/swarmscribe-console -f deploy/helm/swarmscribe-console/ci/test-values.yaml --strict
helm template console deploy/helm/swarmscribe-console --namespace fleet -f deploy/helm/swarmscribe-console/ci/test-values.yaml | kubeconform -strict -summary -kubernetes-version 1.33.0 -
```

Expected: `… (core, migrate, ingress)`; lint clean; `Valid: 7`.

- [ ] **Step 4: Widen the CI step and commit**

In `.github/workflows/ci.yml`, job `chart`, change `--only core,migrate` to `--only core,migrate,ingress`.

```bash
git add deploy/helm/swarmscribe-console/templates/ingress.yaml .github/workflows/ci.yml
git commit -m "Console chart: Ingress with TLS, and one for /auth/login to carry a rate limit"
```

---

### Task 4: The PodDisruptionBudget and the NetworkPolicy

**Files:**
- Create: `deploy/helm/swarmscribe-console/templates/pdb.yaml`
- Create: `deploy/helm/swarmscribe-console/templates/networkpolicy.yaml`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `swarmscribe-console.refusedV4`, `.refusedV6` (through `fromYamlArray`), `.selectorLabels`, `.labels`, `.fullname`; values `podDisruptionBudget.*`, `replicaCount`, `networkPolicy.*`.
- Produces: a NetworkPolicy whose `podSelector` is the release's selector labels without the component label (so it covers the console pods and the migration pod), and a PodDisruptionBudget selecting the console pods only.

What a NetworkPolicy can and cannot do here (this is the design; the README says the same to operators in Task 5):

- It allows by address and port. It **cannot name a DNS host**, so leaders and identity providers share one rule: TCP 443 to anywhere except the refused ranges. The names the console refuses (`metadata.google.internal`, `instance-data.ec2.internal`, …) are blocked through the addresses they resolve to, all of which are in `169.254.0.0/16` or are one of the two listed metadata addresses.
- `ipBlock.except` must lie inside its `cidr`. The template attaches the refused list only to the catch-all ranges `0.0.0.0/0` and `::/0`; an operator who narrows `https.cidrs` to the leaders' networks has stated the destinations and gets no `except`.
- It is enforced by the cluster's network plugin, or not at all.
- It cannot block loopback inside the pod, and on a first install the hook Job runs before it exists.

- [ ] **Step 1: Run the check to see it fail**

Run: `uv run --no-project --with pyyaml python deploy/helm/swarmscribe-console/ci/check_render.py --only network`
Expected: exit status 1 with `expected one PodDisruptionBudget named console-swarmscribe-console, found 0`.

- [ ] **Step 2: Write the PodDisruptionBudget**

Create `deploy/helm/swarmscribe-console/templates/pdb.yaml`:

```yaml
{{- if and .Values.podDisruptionBudget.enabled (gt (int .Values.replicaCount) 1) }}
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: {{ include "swarmscribe-console.fullname" . }}
  labels:
    {{- include "swarmscribe-console.labels" . | nindent 4 }}
spec:
  maxUnavailable: {{ .Values.podDisruptionBudget.maxUnavailable }}
  selector:
    matchLabels:
      {{- include "swarmscribe-console.selectorLabels" . | nindent 6 }}
      app.kubernetes.io/component: console
{{- end }}
```

- [ ] **Step 3: Write the NetworkPolicy**

Create `deploy/helm/swarmscribe-console/templates/networkpolicy.yaml`:

```yaml
{{- if .Values.networkPolicy.enabled }}
{{- $egress := .Values.networkPolicy.egress }}
{{- $v4 := include "swarmscribe-console.refusedV4" . | fromYamlArray }}
{{- $v6 := include "swarmscribe-console.refusedV6" . | fromYamlArray }}
{{- range $egress.https.extraExcept }}
{{- if contains ":" . }}
{{- $v6 = append $v6 . }}
{{- else }}
{{- $v4 = append $v4 . }}
{{- end }}
{{- end }}
# Selects every pod of this release: the console pods and the migration Job's pod.
# What a NetworkPolicy cannot do is in the README, "Deploy the fleet console", "Egress".
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: {{ include "swarmscribe-console.fullname" . }}
  labels:
    {{- include "swarmscribe-console.labels" . | nindent 4 }}
spec:
  podSelector:
    matchLabels:
      {{- include "swarmscribe-console.selectorLabels" . | nindent 6 }}
  policyTypes: ["Ingress", "Egress"]
  ingress:
    - ports:
        - protocol: TCP
          port: 8080
      {{- with .Values.networkPolicy.ingress.from }}
      from:
        {{- toYaml . | nindent 8 }}
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
    # The console's own Postgres.
    - to:
        {{- toYaml $egress.postgres.peers | nindent 8 }}
      ports:
        - protocol: TCP
          port: {{ $egress.postgres.port }}
    # Leaders and identity providers: the refused ranges are cut out of "anywhere".
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
    {{- with $egress.extra }}
    {{- toYaml . | nindent 4 }}
    {{- end }}
{{- end }}
```

- [ ] **Step 4: Run every check to see it pass**

```bash
uv run --no-project --with pyyaml python deploy/helm/swarmscribe-console/ci/check_render.py
helm lint deploy/helm/swarmscribe-console -f deploy/helm/swarmscribe-console/ci/test-values.yaml --strict
helm template console deploy/helm/swarmscribe-console --namespace fleet -f deploy/helm/swarmscribe-console/ci/test-values.yaml | kubeconform -strict -summary -kubernetes-version 1.33.0 -
```

Expected: `the rendered chart holds every required property (core, migrate, ingress, network)`; lint clean; `Valid: 9, Invalid: 0, Errors: 0, Skipped: 0`.

- [ ] **Step 5: Prove the check can fail**

In `templates/_helpers.tpl`, temporarily change `- 169.254.0.0/16` to `- 169.254.0.0/24` and run the check again.
Expected: `FAILED: NetworkPolicy: 169.254.169.254 is reachable` and `FAILED: NetworkPolicy: 169.254.170.2 is reachable`. Restore the line and run the check once more to see it pass.

- [ ] **Step 6: Run the whole check locally, then commit (CI repeats it)**

In `.github/workflows/ci.yml`, job `chart`, remove ` --only core,migrate,ingress` so the last step runs every section:

```yaml
      - name: Check what the chart renders
        run: >-
          uv run --no-project --with pyyaml
          python deploy/helm/swarmscribe-console/ci/check_render.py
```

```bash
git add deploy/helm/swarmscribe-console/templates/pdb.yaml deploy/helm/swarmscribe-console/templates/networkpolicy.yaml .github/workflows/ci.yml
git commit -m "Console chart: PodDisruptionBudget, and a NetworkPolicy for the egress rules"
```

---

### Task 5: The deployment guide

**Files:**
- Modify: `README.md`
- Modify: `packages/console/README.md`

**Interfaces:**
- Consumes: everything above, and C4a's "Console image" section. Every command and value name in the guide must exist exactly as written in the chart.
- Produces: the README section "Deploy the fleet console".

- [ ] **Step 1: Add the guide**

In `README.md`, insert the following directly before the `## Develop` heading (after C4a's "Console Compose test" subsection):

````markdown
## Deploy the fleet console

One console serves one organisation and any number of leaders. It needs:

- **Its own Postgres, version 14 or later** (the history view uses `date_bin`). Never a
  leader's database.
- **An identity provider**: an Entra ID web app registration, a Google OAuth web client,
  or both, with `<public URL>/auth/callback` as a redirect URI.
- **HTTPS to every leader** it will manage. Leaders need nothing inbound from the console
  beyond their normal admin API, and never connect to it.
- **TLS in front of it.** The public URL is `https://` and the cookies are `Secure`.

### The image

`docker/console.Dockerfile` (section "Console image" above) builds `swarmscribe-console`.
No image is published yet, so the chart has no working default for `image.repository` and
`image.tag`: both are required, and the render fails saying so. Build the image and put it
where the cluster can pull it, or load it into the cluster. Publishing an image is a
follow-up.

```
docker build -t swarmscribe-console:0.1.0 -f docker/console.Dockerfile .
# a local cluster:        kind load docker-image swarmscribe-console:0.1.0
#                         (or: minikube image load swarmscribe-console:0.1.0)
# a registry of your own: docker tag swarmscribe-console:0.1.0 registry.example.org/swarmscribe-console:0.1.0
#                         docker push registry.example.org/swarmscribe-console:0.1.0
```

Use `image.pullPolicy: Never` (or `IfNotPresent`) for a loaded image. The image needs no
writable path, so the chart mounts no `/tmp`.

### Kubernetes, with the Helm chart

The chart is `deploy/helm/swarmscribe-console` (the console only; the leader's chart is a
separate piece of work). CI lints and renders it with Helm 4.3.0.

1. Create the Secret. The chart never creates one: a values file and Helm's release history
   are not a place for the console key.

   ```
   kubectl -n fleet create secret generic swarmscribe-console \
     --from-literal=database-url='postgresql://console:...@db.internal:5432/swarmscribe_console' \
     --from-literal=console-key="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" \
     --from-literal=entra-client-secret='...'
   ```

   Keep a copy of the console key somewhere safe. It seals every leader credential; without
   it each one has to be created again on its leader and entered again. The key comes from
   the environment only: there is no KMS integration and no command that re-seals under a
   new key.

2. Write the values.

   ```yaml
   image:
     repository: swarmscribe-console   # or your registry's name for it
     tag: "0.1.0"
     pullPolicy: IfNotPresent
   publicUrl: https://console.example.org
   secrets:
     existingSecret: swarmscribe-console
   oidc:
     entra:
       enabled: true
       tenantId: 00000000-0000-0000-0000-000000000000
       clientId: 11111111-1111-1111-1111-111111111111
   ingress:
     className: nginx
     tls:
       secretName: console-tls
   networkPolicy:
     egress:
       postgres:
         peers:
           - ipBlock:
               cidr: 10.20.30.40/32
   ```

3. Install. The migration runs first, as a hook; the console pods start after it.

   ```
   helm upgrade --install console deploy/helm/swarmscribe-console -n fleet -f values.yaml
   ```

4. Add the first console administrator, with the command that fits the sign-in provider
   ("Which principals a sign-in yields" above):

   ```
   kubectl -n fleet exec deploy/console-swarmscribe-console -- \
     swarmscribe-console admins add entra_group <group-object-id>
   ```

5. On each leader, a leader administrator creates a credential for the console
   (`swarmscribe-admin console create --name fleet --max-role operator`; "Leaders in the
   console" above says when `admin` is needed). A console administrator then registers the
   leader with it.

What the chart installs:

| Object | What it is |
|---|---|
| Deployment | `replicaCount` console pods (default 2) running `serve`: non-root (10001), read-only root filesystem, no capabilities, no service-account token; startup and liveness probes on `/healthz`, readiness on `/readyz`; requests 100m CPU and 256Mi |
| Job (hook) | `swarmscribe-console migrate`, before install and before every upgrade |
| Service | port 80 to the pods' 8080 |
| Ingress | the host of `publicUrl`, with TLS from `ingress.tls.secretName` (required) |
| Ingress (optional) | exactly `/auth/login`, with its own annotations: `ingress.signIn` |
| ConfigMap | the settings that are not secret |
| PodDisruptionBudget | `maxUnavailable: 1`, when there is more than one replica |
| NetworkPolicy | what the pods may reach and be reached from: "Egress" below |
| ServiceAccount | one with no token mounted; the console never calls the Kubernetes API |

Values:

| Value | Setting or meaning |
|---|---|
| `publicUrl` | `SWARMSCRIBE_CONSOLE_PUBLIC_URL`, and the Ingress host. Required. `https://<host>`, no port, no path |
| `secrets.existingSecret` | the Secret's name. Required. `secrets.keys.*` name its keys: `database-url`, `console-key`, `entra-client-secret`, `google-client-secret`, `google-service-account` |
| `oidc.entra.enabled`, `.tenantId`, `.clientId` | Entra ID sign-in (`SWARMSCRIBE_CONSOLE_ENTRA_*`) |
| `oidc.google.enabled`, `.clientId`, `.hostedDomain`, `.serviceAccount` | Google sign-in (`SWARMSCRIBE_CONSOLE_GOOGLE_*`); `serviceAccount: true` reads the Google Groups key from the Secret |
| `settings` | any other `SWARMSCRIBE_CONSOLE_*` setting that is not a secret, without the prefix: `POLL_CONCURRENCY`, `SESSION_IDLE_SECONDS`, `LOGIN_ATTEMPTS_MAX`, ... |
| `leaderCa.existingConfigMap`, `.key` | CA certificates for leaders on a private CA (`SWARMSCRIBE_CONSOLE_LEADER_CA_FILE`) |
| `replicaCount`, `resources`, `podDisruptionBudget` | scale and availability |
| `ingress.*`, `networkPolicy.*`, `migrate.*` | described in `values.yaml` |

`helm template` fails, saying why, when `publicUrl`, the Secret, a sign-in provider, the
Ingress's TLS secret or the Postgres peer of the NetworkPolicy is missing, and when a
secret is put under `settings`.

### Database connections

Each replica pools `2 * POLL_CONCURRENCY + 2` connections and may open 10 more for web
requests: 18 pooled and up to 28 at the default concurrency of 8. With two replicas that
is up to 56, plus one for the migration job. Size Postgres's `max_connections` for it
(and for Postgres's own reserved connections).

### Probes, the Ingress and a database outage

The probes are `httpGet` (GET). The console also answers HEAD like GET and 405 to any
other method, and redirects `/healthz/` and `/readyz/` to the canonical path. The
Ingress lists explicit paths (`ingress.paths`) and does not route `/healthz` or `/readyz`:
`/readyz` tells an anonymous caller whether the database is up. A route added to the web
app needs a new entry in `ingress.paths`.

During a database outage the console stays alive (liveness does not use the database),
its pods go unready, and the log stays short on purpose: the poller logs one line per 30
seconds per cause and `/readyz` logs one line, at most every 30 seconds, saying whether it
cannot query the database or the migrations are not current. Before C4a's limit this was
about 1,500 lines a minute per replica.

### Egress

The console sends its leader credentials to whatever a leader's URL resolves to. It
refuses a URL that names a loopback, link-local, metadata or reserved address ("Deployment
note: egress" above), but a DNS name is only checked as text, so the network must refuse
the same destinations when the connection is made. The chart's NetworkPolicy allows, from
the console pods:

- DNS, to `networkPolicy.egress.dns.peers` (kube-dns by default);
- Postgres, to `networkPolicy.egress.postgres.peers` on port 5432;
- TCP 443 to anywhere **except** `0.0.0.0/8`, `127.0.0.0/8`, `169.254.0.0/16` (link-local:
  the AWS, Azure and Google metadata services, whatever name was used to reach them),
  `100.100.100.200` (Alibaba Cloud metadata), `168.63.129.16` (the Azure platform
  address), `224.0.0.0/4` and `240.0.0.0/4`; and for IPv6 `::/8` (loopback, IPv4-mapped,
  NAT64), `2001::/32` (Teredo), `2002::/16` (6to4), `fd00:ec2::254` (AWS metadata),
  `fe80::/10`, `fec0::/10` and `ff00::/8`. This one rule serves both the leaders and the
  identity providers.

Private addresses stay reachable, because leaders usually live on them. If every leader is
outside the cluster, add the cluster's pod and service ranges to
`networkPolicy.egress.https.extraExcept`. If leaders listen on another port, add it to
`networkPolicy.egress.https.ports`.

What a NetworkPolicy cannot do, so that nobody relies on it for more:

- It does nothing unless the cluster's network plugin enforces NetworkPolicy.
- It cannot name a DNS host. Identity providers cannot be pinned to their names, and the
  metadata *names* the console refuses are blocked only through the addresses they resolve
  to. To allow only the leaders' networks, narrow `networkPolicy.egress.https.cidrs` to
  them and send identity-provider calls through a proxy (`extraEnv` with `HTTPS_PROXY`,
  and the proxy under `networkPolicy.egress.extra`): calls to leaders never use a proxy.
- It cannot block loopback inside the pod. The console is the only container there, so
  nothing but the console itself listens on it; do not add a sidecar that trusts
  loopback callers.
- NodeLocal DNSCache listens on a link-local address (`169.254.20.10`). Add it as an
  `ipBlock` under `networkPolicy.egress.dns.peers`.
- On a first install the migration Job runs before this policy exists. If the namespace
  denies egress by default, allow the Job's pod
  (`app.kubernetes.io/component: migrate`) to reach Postgres yourself.

Outside Kubernetes, put the same list in the host's firewall or the cloud's security
group: deny the console's outbound traffic to the ranges above, and allow only Postgres,
DNS, the leaders and the identity providers.

### Limiting sign-in attempts

Starting a sign-in (`/auth/login`) needs no session and stores a row until the person comes
back from the identity provider, or for ten minutes. Two things bound it:

- **In the console:** at most `SWARMSCRIBE_CONSOLE_LOGIN_ATTEMPTS_MAX` sign-ins are pending
  (default 10000). Beyond that the oldest are dropped, so a flood evicts itself and a
  person who signs in promptly still finishes. The console logs one warning a minute while
  it is dropping.
- **At the ingress:** a per-client rate limit, which only the ingress can do (the console
  does not see the client's address). `ingress.signIn.enabled` adds an Ingress for exactly
  `/auth/login` that carries its own annotations, so the limit does not slow the rest of
  the console. The annotations are your ingress controller's, for example
  `nginx.ingress.kubernetes.io/limit-rpm: "30"` for ingress-nginx, or a rate-limit
  middleware named in `traefik.ingress.kubernetes.io/router.middlewares` for Traefik.

### Leaders on a private CA

Calls to leaders verify the leader's certificate against the public roots and never read
`SSL_CERT_FILE`. For leaders whose certificates come from your own CA, put the CA
certificates (PEM) in a ConfigMap and name it in `leaderCa.existingConfigMap`; outside
Kubernetes, set `SWARMSCRIBE_CONSOLE_LEADER_CA_FILE` to the file. They are trusted in
addition to the public roots, and for leader calls only.

### Upgrades

`helm upgrade` runs the migration, then replaces the pods one at a time
(`maxUnavailable: 0`). The pods of the old version keep serving on the migrated schema
until they are replaced: `/readyz` stays 200 when the database is ahead. A console never
*starts* on a database that is ahead of it, and there is no downgrade command: after a
migration, a rollback of the image alone leaves pods that refuse to start, so roll forward
again.
````

- [ ] **Step 2: Point the egress note at the chart**

In `README.md`, section "Deployment note: egress", replace the last sentence

```markdown
address later. Add the same blocks to the console host's egress policy: deny
its traffic to loopback, link-local and metadata addresses (`169.254.169.254`,
`fd00:ec2::254`, `100.100.100.200`) and allow only the leaders' networks (the
poller's egress policy itself is C4's).
```

with

```markdown
address later. So the same destinations must be refused when the connection is
made: the Helm chart's NetworkPolicy does that on Kubernetes, and "Deploy the
fleet console", "Egress", says what it covers, what it cannot, and what to do
on other hosts.
```

- [ ] **Step 3: Update the status table**

In `README.md`, section "Status", replace the row `| Helm chart | Not started |` with:

```markdown
| `swarmscribe-console` — fleet console: backend, web app, image and Helm chart (`deploy/helm/swarmscribe-console`) | Built |
| Helm chart for the leader and followers | Not started |
```

(If C3 has already added a row for the console, extend that row instead of adding a second one.)

- [ ] **Step 4: Point the package README at the guide**

In `packages/console/README.md`, replace the paragraph that begins "Configuration, the bootstrap commands" with:

```markdown
Configuration, the bootstrap commands for each sign-in provider, the grant and registry
rules and the leader-URL rules are in the repository's top-level README, section "Run the
fleet console (development)". The image, the Helm chart, the egress policy, the sign-in
limits and the database sizing are in its section "Deploy the fleet console".
```

- [ ] **Step 5: Check the guide against the chart**

Every claim in the guide is checked by rendering, not by reading:

```bash
cat > /tmp/guide-values.yaml <<'EOF'
image:
  repository: registry.example.org/swarmscribe-console
  tag: "0.1.0"
publicUrl: https://console.example.org
secrets:
  existingSecret: swarmscribe-console
oidc:
  entra:
    enabled: true
    tenantId: 00000000-0000-0000-0000-000000000000
    clientId: 11111111-1111-1111-1111-111111111111
ingress:
  className: nginx
  tls:
    secretName: console-tls
networkPolicy:
  egress:
    postgres:
      peers:
        - ipBlock:
            cidr: 10.20.30.40/32
EOF
helm template console deploy/helm/swarmscribe-console -n fleet -f /tmp/guide-values.yaml | kubeconform -strict -summary -kubernetes-version 1.33.0 -
helm template console deploy/helm/swarmscribe-console -n fleet -f /tmp/guide-values.yaml | grep -A3 "^kind: Deployment"
```

Expected: `Valid: 8, Invalid: 0, Errors: 0, Skipped: 0` (the guide's values leave the sign-in Ingress off), and `name: console-swarmscribe-console` under the Deployment, the name the guide's `kubectl exec deploy/console-swarmscribe-console` uses.

- [ ] **Step 6: Commit**

```bash
git add README.md packages/console/README.md
git commit -m "README: deploy the fleet console (image, Helm chart, egress, sign-in limits)"
```

---

## Self-Review

**Spec coverage.**
- Spec 8, "Helm values: replicas" — `replicaCount` (Task 1). "Postgres URL, console key" — `secrets.existingSecret` and `secrets.keys.*` (Task 1, ruling 2). "OIDC client settings" — `oidc.entra.*`, `oidc.google.*`, with the client secrets from the Secret (Task 1). "ingress with TLS" — Task 3; TLS cannot be left out.
- Spec 8, "The console runs anywhere that can reach the leaders over HTTPS; leaders need no inbound access from it": the NetworkPolicy allows egress on 443 and ingress to the console's port only (Task 4); the guide says what leaders need (Task 5).
- Spec 7, "TLS everywhere": Tasks 1 and 3. "Leader credentials (encrypted)": the key is read from a Secret (Task 1).
- Master spec 12's chart shape (Deployment, 2+ replicas, PodDisruptionBudget, Service, Ingress; migration Job as a hook): Tasks 1 to 4. Master spec 14 (`helm lint` and template rendering in CI): Task 1's job, widened by Tasks 2 to 4.
- C2a and C2b deferral, "C4 adds an egress network policy": Task 4, covering loopback, link-local, the metadata addresses `169.254.169.254`, `fd00:ec2::254` and `100.100.100.200`, and the other refused ranges; the names the console refuses are covered through their addresses, and the limits are stated (Task 4, Task 5).
- C2 deferral, rate limit on sign-ins: the sign-in Ingress (Task 3) with the in-app cap from C4a, documented together (Task 5).
- C2 deferral, KMS: out of C4 (C4a ruling 9); the guide says the key comes from the environment only (Task 5).
- Docs asked for: the env vars, the bootstrap admin, console credentials on each leader, the egress rules, the Postgres 14+ requirement and the pool sizes are all in Task 5's guide.

**Placeholder scan.** Every file is given whole. `<group-object-id>` in the guide and in `NOTES.txt` is text the operator replaces, and says so.

**Type consistency.** The named templates defined in Task 1 (`swarmscribe-console.settings`, `.secretEnv`, `.host`, `.refusedV4`, `.refusedV6`, the two security contexts) are used under those names in Tasks 2 to 4. The check's `NAME` (`console-swarmscribe-console`) is the full name the chart produces for release `console`, and the guide uses the same. The component labels `console` and `migrate` are the ones the Service, the PodDisruptionBudget and the check rely on.

**Review Focus.** Each of the six lines is asserted by `check_render.py` in the section its task turns on.

**What was verified while writing, and what was not.** The whole chart as given here was linted with Helm 4.3.0 (`--strict`), rendered, validated with kubeconform 0.8.0 against the Kubernetes 1.33.0 schemas (9 resources valid) and passed `check_render.py`, in a scratch folder on the Windows machine; the Task 1 state (four templates) passes `--only core`; the mutation in Task 4's Step 5 fails the check as described; `helm package` leaves `ci/` out. Not verified: an install on a real cluster. Helm v4.3.0 and kubeconform v0.8.0 were fetched as standalone binaries into a scratch folder on the Windows machine and run there; no step of this plan is "CI only". The edits of 2026-10-04 (no `/tmp` volume, required image values, explicit Ingress paths, GET probes) were written against C4a's final image and have not been re-rendered since. Nothing here has shown that the hook Job runs before the pods, that the probes pass, or that a network plugin enforces the policy as rendered.
