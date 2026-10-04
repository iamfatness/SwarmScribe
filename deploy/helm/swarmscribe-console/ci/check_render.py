"""What the rendered swarmscribe-console chart must hold, whatever else changes.

Run from the repository root (CI job `chart` does), with Helm on the PATH:

    uv run python deploy/helm/swarmscribe-console/ci/check_render.py

It renders the chart with `helm template` and ci/test-values.yaml, checks the manifests, then
renders it with values that must be refused. `--only core,migrate` runs some sections only
(core, migrate, ingress, network). Exit status 1 lists every problem."""

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
WEB = CHART.parents[2] / "packages" / "console-web"
# What the console serves besides the web app's pages (packages/console/src/.../api/*.py:
# APIRouter prefixes /api and /auth; the web build's assets folder is read from vite.config.ts).
BACKEND_PREFIXES = ("/api", "/auth")
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
    return subprocess.run([*command, "-f", str(VALUES), *extra], capture_output=True, text=True)


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
    # Found on a real install: a long exec line in NOTES.txt printed a run of spaces where
    # the line continuation was meant to be.
    notes = (CHART / "templates" / "NOTES.txt").read_text()
    if re.search(r"\S {4,}\S", notes):
        problems.append("NOTES.txt: a run of spaces inside a line (a broken line continuation?)")
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

    if settings.get("SWARMSCRIBE_CONSOLE_PORT") != "8080":
        problems.append("ConfigMap: SWARMSCRIBE_CONSOLE_PORT is not the port value")
    if console["args"][-2:] != ["--port", "8080"] or console["ports"][0]["containerPort"] != 8080:
        problems.append("Deployment: the container port and --port are not the port value")
    moved = one(render("--set", "port=9090"), "Deployment")["spec"]["template"]["spec"][
        "containers"
    ][0]
    if moved["args"][-1] != "9090" or moved["ports"][0]["containerPort"] != 9090:
        problems.append("Deployment: the port value does not move the container port and --port")

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


def web_routes() -> list[str]:
    """Concrete example paths for every route the web app answers, read from the router's
    route table (packages/console-web/src/App.tsx: `pathname === "..."` and `matchPath("...")`),
    plus the backend prefixes and the web build's assets folder."""
    source = (WEB / "src" / "App.tsx").read_text(encoding="utf-8")
    literals = re.findall(r'(?:pathname === |matchPath\()"(/[^"]*)"', source)
    literals += re.findall(r'<Route[^>]*path="(/[^"]*)"', source)
    if len(literals) < 5:
        raise SystemExit("could not read the web app's routes from App.tsx: update check_render.py")
    assets = re.search(r'assetsDir:\s*"([^"]+)"', (WEB / "vite.config.ts").read_text("utf-8"))
    if assets is None:
        raise SystemExit("could not read assetsDir from vite.config.ts: update check_render.py")
    paths = [re.sub(r":\w+", "x", literal) for literal in literals]
    return sorted({*paths, *BACKEND_PREFIXES, f"/{assets.group(1)}/index-0123456789abcdef.js"})


def check_paths(entries: list[dict]) -> list[str]:
    """Every route is matched by an ingress path; the probe endpoints never are."""
    problems = []
    for route in web_routes():
        if not any(routes(entry, route) for entry in entries):
            problems.append(f"Ingress: no ingress.paths entry routes {route} (a web app route)")
    for probe in ("/healthz", "/healthz/", "/readyz", "/readyz/"):
        for entry in entries:
            if routes(entry, probe):
                problems.append(f"Ingress: {entry['path']} ({entry['pathType']}) routes {probe}")
    return problems


def check_ingress(docs: list[dict]) -> list[str]:
    problems: list[str] = []
    ingress = one(docs, "Ingress")
    if ingress["spec"]["tls"] != [{"hosts": ["console.example.org"], "secretName": "console-tls"}]:
        problems.append("Ingress: TLS is not for the public URL's host")
    if ingress["spec"]["rules"][0]["host"] != "console.example.org":
        problems.append("Ingress: the host is not the public URL's")
    for rule in ingress["spec"]["rules"]:
        problems += check_paths(rule["http"]["paths"])
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
    # The drift check must be able to fail: drop /admin from the list and it has to notice.
    shortened = {"ingress": {"paths": [{"path": "/", "pathType": "Exact"}]}}
    with tempfile.TemporaryDirectory() as folder:
        values = Path(folder) / "paths.yaml"
        values.write_text(yaml.safe_dump(shortened), encoding="utf-8")
        thin = one(render("-f", str(values)), "Ingress")
    if not check_paths(thin["spec"]["rules"][0]["http"]["paths"]):
        problems.append("the route drift check passes when ingress.paths lacks the web routes")
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
    moved = one(render("--set", "port=9090"), "NetworkPolicy")["spec"]["ingress"][0]["ports"]
    if moved != [{"protocol": "TCP", "port": 9090}]:
        problems.append("NetworkPolicy: ingress does not follow the port value")

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
