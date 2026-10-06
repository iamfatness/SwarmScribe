"""What the rendered swarmscribe-leader chart must hold, whatever else changes.

Run from the repository root (CI job `chart` does), with Helm on the PATH:

    uv run --no-project --with pyyaml python deploy/helm/swarmscribe-leader/ci/check_render.py

It renders the chart with `helm template` and ci/test-values.yaml, checks the manifests, then
renders it with values that must be refused. `--only core,storage` runs some sections only
(core, storage, migrate, ingress, network, values). Exit status 1 lists every problem."""

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
CONTAINER_CONTEXT = {
    "allowPrivilegeEscalation": False,
    "readOnlyRootFilesystem": True,
    "capabilities": {"drop": ["ALL"]},
}
POD_CONTEXT_KEYS = {
    "runAsNonRoot",
    "runAsUser",
    "runAsGroup",
    "fsGroup",
    "fsGroupChangePolicy",
    "supplementalGroups",
    "seccompProfile",
}
SHARED_NAMESPACES = ("hostNetwork", "hostPID", "hostIPC", "shareProcessNamespace")
# Template functions that make up a value, read the cluster, or decode a Secret. The chart
# generates no secret and reads none: none of these has a place in it.
FORBIDDEN_FUNCTIONS = re.compile(
    r"\b(lookup|rand[A-Za-z]*|genCA\w*|genPrivateKey|genSelfSignedCert\w*|genSignedCert\w*"
    r"|derivePassword|htpasswd|uuidv4|b64dec|b32dec|encryptAES|decryptAES|now|getHostByName)\b"
)
DIGEST = "sha256:" + "ab" * 32
NAME_PATTERN = re.compile(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?")


def helm_template(*extra: str, release: str = "leader") -> subprocess.CompletedProcess:
    command = ["helm", "template", release, str(CHART), "--namespace", "swarmscribe"]
    return subprocess.run([*command, "-f", str(VALUES), *extra], capture_output=True, text=True)


def printed_notes(*extra: str) -> str:
    """NOTES.txt as Helm prints it after an install (a client-side dry run: no cluster)."""
    command = ["helm", "install", "--dry-run=client", "leader", str(CHART), "-n", "swarmscribe"]
    done = subprocess.run([*command, "-f", str(VALUES), *extra], capture_output=True, text=True)
    if done.returncode != 0:
        raise SystemExit(f"helm install --dry-run=client failed:\n{done.stderr}")
    return done.stdout.partition("\nNOTES:\n")[2]


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
    if pod.get("runAsGroup") != 10001:
        problems.append(f"{where}: the pod's group is not 10001")
    # Nothing else may be set on the pod: a later `sysctls` or `runAsUser: 0` beside these
    # would pass every check above.
    for key in sorted(set(pod) - POD_CONTEXT_KEYS):
        problems.append(f"{where}: the pod's securityContext sets {key}")
    if any(spec.get(key) for key in SHARED_NAMESPACES):
        problems.append(f"{where}: the pod shares a host or process namespace")
    if len(spec["containers"]) != 1 or spec.get("initContainers"):
        problems.append(f"{where}: more than the one container (none of the others is checked)")
    (found,) = [c for c in spec["containers"] if c["name"] == container]
    context = found.get("securityContext", {})
    if context.get("readOnlyRootFilesystem") is not True:
        problems.append(f"{where}: the root filesystem is writable")
    if context.get("allowPrivilegeEscalation") is not False:
        problems.append(f"{where}: privilege escalation is allowed")
    if context.get("capabilities", {}).get("drop") != ["ALL"]:
        problems.append(f"{where}: capabilities are not dropped")
    # Exactly these three: `privileged`, `capabilities.add` or a `runAsUser: 0` on the
    # container would undo them without touching them.
    if context != CONTAINER_CONTEXT:
        problems.append(f"{where}: the container's securityContext is {context}")
    names = set()
    for entry in found.get("env", []):
        names.add(entry["name"])
        if entry["name"] in SECRET_NAMES and "secretKeyRef" not in entry.get("valueFrom", {}):
            problems.append(f"{where}: {entry['name']} is not read from the Secret")
        elif entry["name"] in SECRET_NAMES and "value" in entry:
            problems.append(f"{where}: {entry['name']} also has a plain value")
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
    printed = printed_notes()
    if "https://leader.example.org" not in printed or "pool-tokens create" not in printed:
        problems.append("NOTES: the leader's address or the next steps are not printed")
    # The owner's ruling: two replicas by default, and the notes say what that needs.
    if "MUST SEE THE SAME FILES" not in printed or "shared by every replica" not in printed:
        problems.append("NOTES: two replicas, and nothing says the volume must be shared")
    if "MUST SEE THE SAME FILES" in printed_notes("--set", "replicaCount=1"):
        problems.append("NOTES: one replica is told to share its volume with the others")
    problems += check_no_secret_is_made()
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
    if leader["ports"][0].get("name") != "http":
        problems.append("Deployment: the container port is not named http")
    for probe, path in (
        ("startupProbe", "/healthz"),
        ("livenessProbe", "/healthz"),
        ("readinessProbe", "/readyz"),
    ):
        asked = leader.get(probe, {}).get("httpGet", {})
        if asked.get("path") != path:
            problems.append(f"Deployment: {probe} does not ask {path} with GET (httpGet)")
        if asked.get("port") != "http" or asked.get("scheme", "HTTP") != "HTTP" or "host" in asked:
            problems.append(f"Deployment: {probe} does not ask the leader's own port over HTTP")
    if "readinessProbe" not in leader or "livenessProbe" not in leader:
        raise SystemExit("Deployment: the leader has no readiness or no liveness probe")
    # The settings reach the leader through the ConfigMap, and only that way.
    if leader.get("envFrom") != [{"configMapRef": {"name": NAME}}]:
        problems.append("Deployment: the leader does not read its settings from the ConfigMap")
    # A changed setting restarts the pods; an unchanged one does not.
    marks = [
        one(rendered, "Deployment")["spec"]["template"]["metadata"]["annotations"].get(
            "checksum/settings"
        )
        for rendered in (docs, render(), render("--set-string", "settings.LEASE_SECONDS=121"))
    ]
    if not marks[0] or marks[0] != marks[1] or marks[0] == marks[2]:
        problems.append("Deployment: the pods' checksum does not follow the settings")
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
    # All twelve role lists, each under the name the leader reads it by.
    every = {"entraGroups": ["g"], "googleGroups": ["g@example.org"]}
    every |= {"emails": ["a@example.org"], "domains": ["example.org"]}
    all_roles = {"roles": dict.fromkeys(("viewer", "operator", "admin"), every)}
    full = one(render_with(all_roles), "ConfigMap")
    role_names = {name for name in full["data"] if name.startswith("SWARMSCRIBE_ROLE_")}
    if len(role_names) != 12:
        problems.append(f"ConfigMap: {len(role_names)} role lists are rendered, not twelve")
    problems += check_setting_names(full["data"])
    # ROLE_CACHE_SECONDS is a setting like any other: no chart value sets it.
    cached = render_with({"settings": {"ROLE_CACHE_SECONDS": 60}})
    if one(cached, "ConfigMap")["data"].get("SWARMSCRIBE_ROLE_CACHE_SECONDS") != "60":
        problems.append("ConfigMap: settings.ROLE_CACHE_SECONDS is not passed on")
    problems += check_setting_names({"SWARMSCRIBE_ROLE_CACHE_SECONDS": "60"})
    refused_file(
        problems,
        "SWARMSCRIBE_ROLE_OPERATOR_DOMAINS under extraEnv",
        {"extraEnv": [{"name": "swarmscribe_role_operator_domains", "value": "example.org"}]},
        says="collides",
    )
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
    # With the Ingress off, only allowHttpPublicUrl stands between http:// and a render.
    refused_file(
        problems,
        "an http publicUrl with the Ingress off and no allowHttpPublicUrl",
        {"publicUrl": "http://leader.example.org", "ingress": {"enabled": False}},
        says="publicUrl must be https://",
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


def check_no_secret_is_made() -> list[str]:
    """The chart never generates, reads, prints or stores a secret."""
    problems: list[str] = []
    for source in sorted((CHART / "templates").iterdir()):
        text = source.read_text(encoding="utf-8")
        for action in re.findall(r"\{\{.*?\}\}", text, flags=re.DOTALL):
            if action.lstrip("{- ").startswith("/*"):
                continue
            # Function names only: not the words of a message.
            called = re.sub(r'"(\\.|[^"\\])*"|`[^`]*`', '""', action)
            for found in FORBIDDEN_FUNCTIONS.findall(called):
                problems.append(f"{source.name}: the template calls {found}")
    # The same values give the same manifests: nothing in them is made up at render time.
    first, second = helm_template(), helm_template()
    if first.stdout != second.stdout:
        problems.append("two renders of the same values differ: something is generated")
    # The Secret is referred to by name and key in secretKeyRef, and nowhere else: not in
    # the ConfigMap, an annotation, an argument or the notes.
    marked = {
        "secrets": {
            "existingSecret": "zz-secret",
            "keys": {
                "databaseUrl": "zz-database-url",
                "linkKey": "zz-link-key",
                "entraClientSecret": "zz-entra",
                "googleClientSecret": "zz-google",
                "googleServiceAccount": "zz-account",
            },
        }
    }
    path = str(values_file(marked))
    if "zz-" in printed_notes("-f", path):
        problems.append("NOTES: the Secret's name or one of its keys is printed")

    def strays(node: object, under: str) -> list[str]:
        if isinstance(node, dict):
            return [
                where
                for key, value in node.items()
                if key != "secretKeyRef"
                for where in strays(value, f"{under}.{key}")
            ]
        if isinstance(node, list):
            return [where for item in node for where in strays(item, under)]
        return [under] if isinstance(node, str) and "zz-" in node else []

    for doc in render("-f", path):
        for where in strays(doc, doc["kind"]):
            problems.append(f"{where}: holds the Secret's name or a key outside a secretKeyRef")
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
    # The three annotations, as written: the chart has one hook, at weight 0, and anything
    # that is to run before or after it is placed against that.
    if hooks != {
        "helm.sh/hook": "pre-install,pre-upgrade",
        "helm.sh/hook-weight": "0",
        "helm.sh/hook-delete-policy": "before-hook-creation,hook-succeeded",
    }:
        problems.append(f"Job: its annotations are {hooks}")
    hooked = [
        f"{doc['kind']} {doc['metadata']['name']}"
        for doc in docs
        if "helm.sh/hook" in doc["metadata"].get("annotations", {})
    ]
    if hooked != [f"Job {NAME}-migrate"]:
        problems.append(f"the chart's hooks are {hooked}, not the migration Job alone")
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
    # This release's pods and no other's: the Service's selector, without the component.
    service = dict(one(docs, "Service")["spec"]["selector"])
    service.pop("app.kubernetes.io/component", None)
    if selector != service or "app.kubernetes.io/instance" not in selector:
        problems.append(f"NetworkPolicy: selects {selector}, not every pod of this release only")
    job_labels = one(docs, "Job", f"{NAME}-migrate")["spec"]["template"]["metadata"]["labels"]
    if any(job_labels.get(k) != v for k, v in selector.items()):
        problems.append("NetworkPolicy: does not select the migration pod")

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


def check_values(docs: list[dict]) -> list[str]:
    """Every value that is a plain pass-through arrives: none is hard-coded at its default."""
    problems: list[str] = []
    account = one(docs, "ServiceAccount")
    if account.get("automountServiceAccountToken") is not False:
        problems.append("ServiceAccount: it mounts its token")
    if pod_spec(docs).get("serviceAccountName") != NAME:
        problems.append("Deployment: it does not run as the chart's ServiceAccount")
    job = one(docs, "Job", f"{NAME}-migrate")["spec"]["template"]["spec"]["containers"][0]
    if job["resources"] != {
        "requests": {"cpu": "50m", "memory": "128Mi"},
        "limits": {"memory": "256Mi"},
    }:
        problems.append("Job: its resources are not migrate.resources")
    if leader_container(docs)["resources"] != {
        "requests": {"cpu": "100m", "memory": "256Mi"},
        "limits": {"memory": "512Mi"},
    }:
        problems.append("Deployment: its resources are not the documented defaults")
    if leader_container(docs)["imagePullPolicy"] != "IfNotPresent":
        problems.append("Deployment: the default pull policy is not IfNotPresent")

    toleration = {"key": "dedicated", "operator": "Equal", "value": "leader"}
    spread = {
        "maxSkew": 2,
        "topologyKey": "topology.kubernetes.io/zone",
        "whenUnsatisfiable": "DoNotSchedule",
    }
    affinity = {"podAntiAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": []}}
    dns = [{"ipBlock": {"cidr": "169.254.20.10/32"}}]
    moved = render_with(
        {
            "replicaCount": 3,
            "image": {"pullPolicy": "Always"},
            "imagePullSecrets": [{"name": "registry"}],
            "service": {"type": "NodePort", "port": 8443},
            "storage": {"fsGroup": 2000},
            "resources": {"requests": {"cpu": "1"}},
            "migrate": {
                "backoffLimit": 1,
                "activeDeadlineSeconds": 600,
                "resources": {"requests": {"cpu": "2"}},
            },
            "preStopSleepSeconds": 10,
            "terminationGracePeriodSeconds": 90,
            "podDisruptionBudget": {"maxUnavailable": 2},
            "podAnnotations": {"example.org/note": "a"},
            "podLabels": {"example.org/team": "b"},
            "nodeSelector": {"disk": "fast"},
            "tolerations": [toleration],
            "affinity": affinity,
            "topologySpreadConstraints": [spread],
            "serviceAccount": {"name": "named"},
            "networkPolicy": {
                "egress": {
                    "dns": {"peers": dns},
                    "postgres": {"port": 6432},
                    "https": {"ports": [443, 8443]},
                }
            },
        }
    )
    deployment = one(moved, "Deployment")
    spec = deployment["spec"]["template"]["spec"]
    leader = spec["containers"][0]
    hook = one(moved, "Job", f"{NAME}-migrate")["spec"]
    hook_pod = hook["template"]["spec"]
    service = one(moved, "Service")["spec"]
    policy = one(moved, "NetworkPolicy")["spec"]
    ports = [port for rule in policy["egress"] for port in rule["ports"]]
    template = deployment["spec"]["template"]["metadata"]
    for what, got, wanted in (
        ("replicaCount", deployment["spec"]["replicas"], 3),
        ("image.pullPolicy (leader)", leader["imagePullPolicy"], "Always"),
        ("image.pullPolicy (migration)", hook_pod["containers"][0]["imagePullPolicy"], "Always"),
        ("imagePullSecrets (leader)", spec.get("imagePullSecrets"), [{"name": "registry"}]),
        ("imagePullSecrets (migration)", hook_pod.get("imagePullSecrets"), [{"name": "registry"}]),
        ("service.type", service["type"], "NodePort"),
        ("service.port", service["ports"][0]["port"], 8443),
        ("storage.fsGroup", spec["securityContext"]["fsGroup"], 2000),
        ("resources", leader["resources"]["requests"]["cpu"], "1"),
        ("migrate.resources", hook_pod["containers"][0]["resources"]["requests"]["cpu"], "2"),
        ("migrate.backoffLimit", hook["backoffLimit"], 1),
        ("migrate.activeDeadlineSeconds", hook["activeDeadlineSeconds"], 600),
        ("preStopSleepSeconds", leader["lifecycle"]["preStop"]["exec"]["command"], ["sleep", "10"]),
        ("terminationGracePeriodSeconds", spec["terminationGracePeriodSeconds"], 90),
        (
            "podDisruptionBudget.maxUnavailable",
            one(moved, "PodDisruptionBudget")["spec"]["maxUnavailable"],
            2,
        ),
        ("podAnnotations", template["annotations"].get("example.org/note"), "a"),
        ("podLabels", template["labels"].get("example.org/team"), "b"),
        ("nodeSelector (leader)", spec.get("nodeSelector"), {"disk": "fast"}),
        ("nodeSelector (migration)", hook_pod.get("nodeSelector"), {"disk": "fast"}),
        ("tolerations (leader)", spec.get("tolerations"), [toleration]),
        ("tolerations (migration)", hook_pod.get("tolerations"), [toleration]),
        ("affinity", spec.get("affinity"), affinity),
        ("topologySpreadConstraints", spec.get("topologySpreadConstraints"), [spread]),
        ("serviceAccount.name", one(moved, "ServiceAccount", "named")["metadata"]["name"], "named"),
        ("serviceAccount.name (leader)", spec.get("serviceAccountName"), "named"),
        ("networkPolicy.egress.dns.peers", policy["egress"][0]["to"], dns),
        ("networkPolicy.egress.postgres.port", policy["egress"][1]["ports"][0]["port"], 6432),
        ("networkPolicy.egress.https.ports", {"protocol": "TCP", "port": 8443} in ports, True),
    ):
        if got != wanted:
            problems.append(f"{what} is not passed on: {got!r}, not {wanted!r}")
    # A fixed Postgres port would cut the leader off from a database on another one.
    if {"protocol": "TCP", "port": 5432} in ports:
        problems.append("NetworkPolicy: 5432 is still open after postgres.port moved")

    own = render("--set", "serviceAccount.create=false")
    if "ServiceAccount" in kinds(own) or pod_spec(own).get("serviceAccountName") != "default":
        problems.append("serviceAccount.create=false does not fall back to `default`")
    return problems


SECTIONS = {
    "core": check_core,
    "storage": check_storage,
    "migrate": check_migrate,
    "ingress": check_ingress,
    "network": check_network,
    "values": check_values,
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
