"""What the rendered swarmscribe-leader chart must be, whatever else changes.

Run from the repository root (CI job `chart` does), with Helm 4 on the PATH or named in $HELM:

    uv run --no-project --with pyyaml python deploy/helm/swarmscribe-leader/ci/check_render.py

It renders the chart with `helm template` and compares everything rendered with the whole
objects it must be, so that anything added, removed or changed fails. It does that for
ci/test-values.yaml and for some thirty other renders, one for each branch the templates
have: another release name, sign-in on and off, each object switched off, each optional
field, the guide's example values alone and the kind test's. It compares values.yaml with
the defaults it must hold, renders the notes with a marker in every free-form value to see
that none is printed, and renders the chart with values that must be refused. It reads
the leader's own code for what the chart has to agree with:
the settings (config.py), the probes' routes (api/health.py) and the routers (api/*.py).
`--only core,storage` runs some sections only (core, storage, migrate, ingress, network,
values). Every problem is listed, one `FAILED:` line each, and the exit status is 1; a part
that cannot go on says so in one line and the other parts still run (`--stop-at-first` stops
after the first part that finds anything)."""

import argparse
import ast
import copy
import ipaddress
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import textwrap
from collections.abc import Callable, Iterator
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import helm_tool  # noqa: E402  (deploy/helm/helm_tool.py)

CHART = Path(__file__).resolve().parents[1]
REPO = CHART.parents[2]
VALUES = CHART / "ci" / "test-values.yaml"
LEADER = REPO / "packages" / "leader" / "src" / "swarmscribe_leader"
README = REPO / "README.md"
# The kind test's values (plan L3): read below as KIND_VALUES.
KIND_VALUES_FILE = REPO / "e2e" / "leader-kind" / "leader-values.yaml"
NAME = "leader-swarmscribe-leader"
HOST = "leader.example.org"
SECRET = "swarmscribe-leader"

# --- what test-values.yaml must render to -------------------------------------------------

CHART_FILE = yaml.safe_load((CHART / "Chart.yaml").read_text(encoding="utf-8"))
SELECTOR = {
    "app.kubernetes.io/name": "swarmscribe-leader",
    "app.kubernetes.io/instance": "leader",
}
LABELS = {
    "helm.sh/chart": f"swarmscribe-leader-{CHART_FILE.get('version')}",
    **SELECTOR,
    "app.kubernetes.io/version": str(CHART_FILE.get("appVersion")),
    "app.kubernetes.io/managed-by": "Helm",
}
LEADER_SELECTOR = {**SELECTOR, "app.kubernetes.io/component": "leader"}
LEADER_LABELS = {**LABELS, "app.kubernetes.io/component": "leader"}
MIGRATE_LABELS = {**LABELS, "app.kubernetes.io/component": "migrate"}
# The Secret's default keys (values.yaml), in the order the pods read them.
SECRET_KEYS = {
    "SWARMSCRIBE_DATABASE_URL": "database-url",
    "SWARMSCRIBE_LINK_KEY": "link-key",
    "SWARMSCRIBE_ENTRA_CLIENT_SECRET": "entra-client-secret",
    "SWARMSCRIBE_GOOGLE_CLIENT_SECRET": "google-client-secret",
    "SWARMSCRIBE_GOOGLE_SERVICE_ACCOUNT": "google-service-account",
}
# test-values.yaml turns everything on, so all five are expected there.
SECRET_NAMES = set(SECRET_KEYS)
# The least a leader needs: the two every leader reads.
ALWAYS_SECRET = {"SWARMSCRIBE_DATABASE_URL", "SWARMSCRIBE_LINK_KEY"}
# The settings the chart sets from values of its own, and the value that sets each.
OWNED = {
    "public_url": "publicUrl",
    "entra_tenant_id": "oidc.entra.tenantId",
    "entra_client_id": "oidc.entra.clientId",
    "google_client_id": "oidc.google.clientId",
    "google_hosted_domain": "oidc.google.hostedDomain",
}
SETTINGS = {
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
CONTAINER_CONTEXT = {
    "allowPrivilegeEscalation": False,
    "readOnlyRootFilesystem": True,
    "capabilities": {"drop": ["ALL"]},
}
LEADER_RESOURCES = {
    "requests": {"cpu": "100m", "memory": "256Mi"},
    "limits": {"memory": "512Mi"},
}
MIGRATE_RESOURCES = {
    "requests": {"cpu": "50m", "memory": "128Mi"},
    "limits": {"memory": "256Mi"},
}
TEST_VOLUMES = [
    {"name": "storage-recordings", "persistentVolumeClaim": {"claimName": "recordings"}},
    {"name": "storage-archive", "nfs": {"server": "nas.internal", "path": "/exports/archive"}},
]
TEST_MOUNTS = [
    {"name": "storage-recordings", "mountPath": "/data/recordings"},
    {"name": "storage-archive", "mountPath": "/data/archive", "readOnly": True},
]
HOOKS = {
    "helm.sh/hook": "pre-install,pre-upgrade",
    "helm.sh/hook-weight": "0",
    "helm.sh/hook-delete-policy": "before-hook-creation,hook-succeeded",
}
KUBE_DNS = [
    {
        "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}},
        "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
    }
]
TEST_SOURCES = [
    {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "traefik"}}},
    {
        "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "transcribe"}},
        "podSelector": {"matchLabels": {"app.kubernetes.io/name": "swarmscribe-follower"}},
    },
]
TEST_POSTGRES = [{"ipBlock": {"cidr": "10.20.30.40/32"}}]
# What is cut out of "anywhere" for the identity providers, as the chart writes it.
REFUSED_V4 = [
    "0.0.0.0/8",
    "100.100.100.200/32",
    "127.0.0.0/8",
    "168.63.129.16/32",
    "169.254.0.0/16",
    "224.0.0.0/4",
    "240.0.0.0/4",
]
REFUSED_V6 = [
    "::/8",
    "2001::/32",
    "2002::/16",
    "fd00:ec2::254/128",
    "fe80::/10",
    "fec0::/10",
    "ff00::/8",
]
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
# The notes for test-values.yaml, whole: what an operator reads after every install.
NOTES = """\
The SwarmScribe leader is at https://leader.example.org

1. Sign in from your own machine, as someone roles.admin names (nothing is created for
   you, and no credential is printed here):

   swarmscribe-admin --leader https://leader.example.org login

2. Add a location inside a mounted volume (/data/recordings, /data/archive), with a
   consent.txt at its root:

   swarmscribe-admin locations add NAME --root <a folder under a mount path>

3. For each follower pool, create a pool token and put it in a Secret for the follower
   chart (it is shown once):

   swarmscribe-admin pool-tokens create --name cpu-pods --pool default

4. For a fleet console: swarmscribe-admin console create --name fleet --max-role operator

ALL 2 LEADER PODS MUST SEE THE SAME FILES: each storage volume must be one volume
shared by every replica (ReadWriteMany when the pods are on more than one node). The chart
cannot check this, and the leader does not check it yet.

File links carry a signed token in the URL path. Turn request logging off for this Ingress
at your ingress controller, and raise its body limit to 512 MiB (README, "Deploy the leader").

A NetworkPolicy limits who reaches the leader and what it reaches. It needs a network plugin
that enforces NetworkPolicy; check that yours does.

The pods are Ready whether or not they can reach the identity provider. If `login` is
answered 503 "sign-in cannot be checked right now" after about 3 seconds, the leader cannot
fetch the provider's keys: look in its log for "sign-in keys could not be fetched", and at
the HTTPS egress (networkPolicy.egress.https, or the proxy your cluster needs)."""
# The files of the packaged chart: these and no other.
PACKAGED = {
    ".helmignore",
    "Chart.yaml",
    "values.schema.json",
    "values.yaml",
    "templates/NOTES.txt",
    "templates/_helpers.tpl",
    "templates/configmap.yaml",
    "templates/deployment.yaml",
    "templates/ingress.yaml",
    "templates/migrate-job.yaml",
    "templates/networkpolicy.yaml",
    "templates/pdb.yaml",
    "templates/service.yaml",
    "templates/serviceaccount.yaml",
}

# values.yaml, whole: every default the chart ships. A default that test-values.yaml
# overrides, or that only a refusal reads (oidc.allowNone, the empty extraExcept), is seen
# by no render of the test values; it is seen here.
DEFAULTS = {
    "nameOverride": "",
    "fullnameOverride": "",
    "replicaCount": 2,
    "image": {"repository": "", "tag": "", "digest": "", "pullPolicy": "IfNotPresent"},
    "imagePullSecrets": [],
    "publicUrl": "",
    "allowHttpPublicUrl": False,
    "secrets": {
        "existingSecret": "",
        "keys": {
            "databaseUrl": "database-url",
            "linkKey": "link-key",
            "entraClientSecret": "entra-client-secret",
            "googleClientSecret": "google-client-secret",
            "googleServiceAccount": "google-service-account",
        },
    },
    "oidc": {
        "allowNone": False,
        "entra": {"enabled": False, "tenantId": "", "clientId": "", "clientSecret": False},
        "google": {"enabled": False, "clientId": "", "hostedDomain": "", "serviceAccount": False},
    },
    "roles": {
        role: {"entraGroups": [], "googleGroups": [], "emails": [], "domains": []}
        for role in ("viewer", "operator", "admin")
    },
    "settings": {},
    "extraEnv": [],
    "storage": {"volumes": [], "fsGroup": 10001, "supplementalGroups": []},
    "port": 8080,
    "service": {"type": "ClusterIP", "port": 80},
    "ingress": {
        "enabled": True,
        "className": "",
        "annotations": {},
        "tls": {"secretName": ""},
        "paths": [{"path": "/v1", "pathType": "Prefix"}],
    },
    "migrate": {
        "enabled": True,
        "backoffLimit": 3,
        "activeDeadlineSeconds": 300,
        "resources": MIGRATE_RESOURCES,
    },
    "resources": LEADER_RESOURCES,
    "updateStrategy": "RollingUpdate",
    "preStopSleepSeconds": 5,
    "terminationGracePeriodSeconds": 60,
    "podDisruptionBudget": {"enabled": True, "maxUnavailable": 1},
    "networkPolicy": {
        "enabled": True,
        "ingress": {"from": [], "anySource": False},
        "egress": {
            "dns": {"peers": KUBE_DNS},
            "postgres": {"port": 5432, "peers": []},
            "https": {"ports": [443], "cidrs": ["0.0.0.0/0", "::/0"], "extraExcept": []},
            "extra": [],
        },
    },
    "serviceAccount": {"create": True, "name": ""},
    "podAnnotations": {},
    "podLabels": {},
    "nodeSelector": {},
    "tolerations": [],
    "affinity": {},
    "spreadAcrossNodes": True,
    "topologySpreadConstraints": [],
}
# The three values where null means something; every other key must have a value.
NULLABLE = {
    "storage.fsGroup",
    "networkPolicy.ingress.from",
    "networkPolicy.egress.postgres.peers",
}
# The maps of values.yaml whose keys the chart owns (the others are Kubernetes' own, or
# resources, where `limits: null` is how a default limit is taken away).
OWNED_MAPS = {
    "image",
    "secrets",
    "secrets.keys",
    "oidc",
    "oidc.entra",
    "oidc.google",
    "roles",
    "roles.viewer",
    "roles.operator",
    "roles.admin",
    "storage",
    "service",
    "ingress",
    "ingress.tls",
    "migrate",
    "podDisruptionBudget",
    "networkPolicy",
    "networkPolicy.ingress",
    "networkPolicy.egress",
    "networkPolicy.egress.dns",
    "networkPolicy.egress.postgres",
    "networkPolicy.egress.https",
    "serviceAccount",
}
# The marker put into every free-form value to see that the notes print none of them.
MARK = "zzmark"

# --- other values the chart is rendered with ----------------------------------------------

# Values for a leader with no sign-in, laid over test-values.yaml.
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
# The kind test's values (e2e/leader-kind/leader-values.yaml), a whole values file: no
# sign-in, no Ingress, plain http to the Service, peers by label. It must keep rendering as
# the chart tightens, and what it renders is compared whole (core_whole) and counted
# (core_must_render: 7 objects, two egress rules, no ipBlock).
KIND_VALUES = yaml.safe_load(KIND_VALUES_FILE.read_text(encoding="utf-8"))

# --- the schema: which objects may take keys it does not name ------------------------------

# Objects that are Kubernetes' own and passed on as written. Every other object of the
# schema refuses a key it does not name (the follower chart's standard), root included.
OPEN_OBJECTS = {
    "global",
    "extraEnv[].valueFrom",
    "affinity",
    "tolerations[]",
    "topologySpreadConstraints[]",
    "networkPolicy.egress.extra[].ports[]",
    "#resources.requests",
    "#resources.limits",
    "#peer.namespaceSelector",
    "#peer.podSelector",
}
# Maps of names to strings (annotations, labels, a driver's attributes): any name.
STRING_MAPS = {
    "ingress.annotations",
    "podAnnotations",
    "podLabels",
    "nodeSelector",
    "storage.volumes[].volume.csi.volumeAttributes",
}
# Lists that would open the NetworkPolicy, or publish nothing, if they could be empty.
NEVER_EMPTY = {
    "ingress.paths",
    "networkPolicy.egress.dns.peers",
    "networkPolicy.egress.https.ports",
    "networkPolicy.egress.https.cidrs",
    "networkPolicy.egress.extra[].to",
    "networkPolicy.egress.extra[].ports",
}

# Template functions that make up a value, read the cluster, or decode a Secret. The chart
# generates no secret and reads none: none of these has a place in it.
FORBIDDEN_FUNCTIONS = re.compile(
    r"\b(lookup|rand[A-Za-z]*|genCA\w*|genPrivateKey|genSelfSignedCert\w*|genSignedCert\w*"
    r"|derivePassword|htpasswd|uuidv4|b64dec|b32dec|encryptAES|decryptAES|now|getHostByName)\b"
)
DIGEST = "sha256:" + "ab" * 32
NAME_PATTERN = re.compile(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?")

# Set by main(): the Helm binary every render runs, and this run's one scratch folder.
HELM = "helm"
SCRATCH = Path()
_numbers = itertools.count(1)
_runs = {"rendered": 0, "refused": 0}


class CheckError(Exception):
    """A part of the check cannot go on. It becomes one FAILED line; the other parts run."""


# --- running Helm -------------------------------------------------------------------------


def helm_template(
    *extra: str, release: str = "leader", chart: Path = CHART, base: bool = True
) -> subprocess.CompletedProcess:
    """`helm template` with ci/test-values.yaml first (base=False: with `extra` alone)."""
    command = [HELM, "template", release, str(chart), "--namespace", "swarmscribe"]
    if base:
        command += ["-f", str(VALUES)]
    done = subprocess.run(
        [*command, *extra], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    _runs["rendered" if done.returncode == 0 else "refused"] += 1
    return done


def said(done: subprocess.CompletedProcess) -> str:
    """Helm's error on one line, without its advice."""
    text = done.stderr.replace("Use --debug flag to render out invalid YAML", "")
    return " ".join(text.split())[-400:]


def render(*extra: str, release: str = "leader", base: bool = True) -> list[dict]:
    done = helm_template(*extra, release=release, base=base)
    if done.returncode != 0:
        raise CheckError(f"helm template {' '.join(extra)} failed: {said(done)}")
    return [doc for doc in yaml.safe_load_all(done.stdout) if doc]


def values_file(values: dict) -> Path:
    path = SCRATCH / f"values-{next(_numbers)}.yaml"
    path.write_text(yaml.safe_dump(values), encoding="utf-8")
    return path


def render_with(values: dict, *extra: str, base: bool = True) -> list[dict]:
    return render("-f", str(values_file(values)), *extra, base=base)


def refused(
    problems: list[str], what: str, *values: str, strings: bool = False, says: str = ""
) -> None:
    """The chart must not render with these --set values; `says` must be in the message."""
    flag = "--set-string" if strings else "--set"
    _must_fail(problems, what, helm_template(flag, ",".join(values)), says)


def refused_file(
    problems: list[str], what: str, values: dict, says: str = "", unchecked: bool = False
) -> None:
    """`refused` for values --set cannot express (a newline in a key, a list of maps).

    unchecked=True renders with the schema switched off (`--skip-schema-validation`): the
    templates' own refusal, the second line behind the schema, must then be what stops it."""
    extra = ["--skip-schema-validation"] if unchecked else []
    done = helm_template("-f", str(values_file(values)), *extra)
    _must_fail(problems, what + (" (schema off)" if unchecked else ""), done, says)


def refused_twice(
    problems: list[str], what: str, values: dict, schema: str, template: str
) -> None:
    """Refused by the schema (saying `schema`) and, with the schema off, by the templates
    (saying `template`)."""
    refused_file(problems, what, values, says=schema)
    refused_file(problems, what, values, says=template, unchecked=True)


def _must_fail(
    problems: list[str], what: str, done: subprocess.CompletedProcess, says: str
) -> None:
    if done.returncode == 0:
        problems.append(f"the chart renders with {what}")
    elif says and says not in done.stderr:
        problems.append(f"{what} is refused without saying {says!r}: {said(done)}")


# --- reading manifests --------------------------------------------------------------------


def dig(node: object, *path: object) -> object:
    """node[a][b]..., or None where a key or an index is missing. Never raises."""
    for key in path:
        if isinstance(node, dict):
            node = node.get(key)
        elif isinstance(node, list) and isinstance(key, int) and -len(node) <= key < len(node):
            node = node[key]
        else:
            return None
    return node


def one(docs: list[dict], kind: str, name: str = NAME) -> dict:
    found = [d for d in docs if d.get("kind") == kind and dig(d, "metadata", "name") == name]
    if len(found) != 1:
        raise CheckError(f"expected one {kind} named {name}, found {len(found)}")
    return found[0]


def kinds(docs: list[dict]) -> set[str]:
    return {str(doc.get("kind")) for doc in docs}


def pod_spec(docs: list[dict]) -> dict:
    return dig(one(docs, "Deployment"), "spec", "template", "spec") or {}


def leader_container(docs: list[dict]) -> dict:
    return dig(pod_spec(docs), "containers", 0) or {}


def job_pod(docs: list[dict]) -> dict:
    return dig(one(docs, "Job", f"{NAME}-migrate"), "spec", "template", "spec") or {}


def policy_spec(docs: list[dict]) -> dict:
    return one(docs, "NetworkPolicy").get("spec") or {}


def differences(got: object, wanted: object, at: str = "") -> Iterator[str]:
    """Where `got` is not exactly `wanted`: every key or entry missing, extra or different."""
    if isinstance(got, dict) and isinstance(wanted, dict):
        for key in sorted({*got, *wanted}, key=str):
            where = f"{at}.{key}" if at else str(key)
            if key not in got:
                yield f"{where} is missing (it must be {wanted[key]!r})"
            elif key not in wanted:
                yield f"{where} is set, to {got[key]!r}, and must not be"
            else:
                yield from differences(got[key], wanted[key], where)
    elif isinstance(got, list) and isinstance(wanted, list):
        if len(got) != len(wanted):
            yield f"{at} has {len(got)} entries, not {len(wanted)}: {got!r}, not {wanted!r}"
        else:
            for index, (mine, theirs) in enumerate(zip(got, wanted, strict=True)):
                yield from differences(mine, theirs, f"{at}[{index}]")
    elif type(got) is not type(wanted) or got != wanted:
        yield f"{at} is {got!r}, not {wanted!r}"


def same(problems: list[str], what: str, got: object, wanted: object) -> None:
    """`got` must be exactly `wanted`: nothing missing, nothing more, nothing else."""
    for difference in differences(got, wanted):
        problems.append(f"{what}: {difference}")


# --- what the leader's own code says ------------------------------------------------------


def probe_routes() -> dict[str, str]:
    """The probes' paths as api/health.py serves them: {"live": ..., "ready": ...}. The
    route whose handler asks the readiness check is the readiness probe's."""
    source = (LEADER / "api" / "health.py").read_text(encoding="utf-8")
    handlers = re.findall(
        r'@router\.get\("([^"]+)"\)\nasync def \w+\([^)]*\)[^\n]*\n((?:    [^\n]*\n|\n)+)', source
    )
    ready = [path for path, body in handlers if "readiness" in body]
    live = [path for path, body in handlers if "readiness" not in body]
    if len(ready) != 1 or len(live) != 1:
        raise CheckError(
            f"api/health.py serves {[path for path, _ in handlers]}: expected one route that "
            "asks the readiness check and one that does not (did the probes move?)"
        )
    return {"live": live[0], "ready": ready[0]}


def served_prefixes() -> set[str]:
    """The prefixes of the leader's routers (api/*.py), other than the probes'."""
    found = set()
    for source in sorted((LEADER / "api").glob("*.py")):
        found |= set(re.findall(r'APIRouter\(prefix="([^"]+)"', source.read_text(encoding="utf-8")))
    return found


def leader_fields() -> dict[str, tuple[str, str]]:
    """Every setting of the leader: name -> (its type as written, its default as written)."""
    source = (LEADER / "config.py").read_text(encoding="utf-8")
    start = source.find("class Settings(")
    if start < 0:
        raise CheckError("config.py has no `class Settings(` (did the settings move?)")
    body = source[start : source.find("\n    @", start)]
    fields = {}
    for name, kind, default in re.findall(
        r"^    ([a-z_]+): ([^=\n#]+?)(?: = ([^#\n]*?))?(?:\s*#.*)?$", body, flags=re.MULTILINE
    ):
        fields[name] = (kind.strip(), default.strip())
    return fields


def plain_settings(fields: dict[str, tuple[str, str]]) -> dict[str, tuple[str, int]]:
    """The settings an operator may put under `settings`: not a secret, not a role list, not
    set by another value. name -> (the schema definition it must use, its default)."""
    described = {
        ("int", "gt"): "positiveInteger",
        ("int", "ge"): "nonNegativeInteger",
        ("float", "gt"): "positiveNumber",
    }
    found = {}
    for name, (kind, default) in fields.items():
        if "SecretStr" in kind or kind == "NameList" or name in OWNED:
            continue
        bound = re.fullmatch(r"Field\(default=([0-9.]+), (gt|ge)=0\)", default)
        if bound is None or (kind, bound.group(2)) not in described:
            raise CheckError(
                f"config.py: `{name}: {kind} = {default}` is a setting this check cannot "
                "place: give it a value of the chart, or a line in values.schema.json and here"
            )
        found[name] = (described[kind, bound.group(2)], int(float(bound.group(1))))
    return found


def leader_is_domain() -> Callable[[str], bool]:
    """config.py's own `_is_domain`, taken out of its source and run (it needs nothing)."""
    tree = ast.parse((LEADER / "config.py").read_text(encoding="utf-8"))
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_is_domain"]
    if len(nodes) != 1:
        raise CheckError("config.py has no `_is_domain` (did the role lists' rules move?)")
    scope: dict = {}
    exec(compile(ast.Module(nodes, []), "config.py", "exec"), scope)
    return scope["_is_domain"]


# --- the objects, whole -------------------------------------------------------------------


def secret_env(names: set[str], secret: str = SECRET) -> list[dict]:
    return [
        {"name": name, "valueFrom": {"secretKeyRef": {"name": secret, "key": key}}}
        for name, key in SECRET_KEYS.items()
        if name in names
    ]


def expected_leader_pod(
    *,
    secrets: set[str] = SECRET_NAMES,
    port: int = 8080,
    volumes: list[dict] = TEST_VOLUMES,
    mounts: list[dict] = TEST_MOUNTS,
    groups: tuple[int, ...] = (2000,),
    fs_group: int | None = 10001,
    spread: bool = True,
    pull: str = "IfNotPresent",
) -> dict:
    """The leader's pod, whole. It meets the restricted Pod Security Standard: non-root
    10001, seccomp, no escalation, every capability dropped, a read-only root, no token, no
    host namespace, no host port, one container."""
    probes = probe_routes()
    context: dict = {"runAsNonRoot": True, "runAsUser": 10001, "runAsGroup": 10001}
    if fs_group is not None:
        context |= {"fsGroup": fs_group, "fsGroupChangePolicy": "OnRootMismatch"}
    if groups:
        context["supplementalGroups"] = list(groups)
    context["seccompProfile"] = {"type": "RuntimeDefault"}
    pod = {
        "serviceAccountName": NAME,
        "automountServiceAccountToken": False,
        "enableServiceLinks": False,
        "terminationGracePeriodSeconds": 60,
        "securityContext": context,
        "containers": [
            {
                "name": "leader",
                "image": "swarmscribe-leader:local",
                "imagePullPolicy": pull,
                "args": ["serve", "--host", "0.0.0.0", "--port", str(port)],
                "ports": [{"name": "http", "containerPort": port, "protocol": "TCP"}],
                "envFrom": [{"configMapRef": {"name": NAME}}],
                "env": secret_env(secrets),
                "securityContext": CONTAINER_CONTEXT,
                # Startup and liveness never ask the database, so an outage restarts nothing;
                # readiness gives up on the database after 3 s, so its timeout is 5.
                "startupProbe": {
                    "httpGet": {"path": probes["live"], "port": "http"},
                    "periodSeconds": 2,
                    "failureThreshold": 30,
                },
                "livenessProbe": {
                    "httpGet": {"path": probes["live"], "port": "http"},
                    "periodSeconds": 10,
                    "timeoutSeconds": 3,
                    "failureThreshold": 3,
                },
                "readinessProbe": {
                    "httpGet": {"path": probes["ready"], "port": "http"},
                    "periodSeconds": 5,
                    "timeoutSeconds": 5,
                    "failureThreshold": 3,
                },
                "lifecycle": {"preStop": {"exec": {"command": ["sleep", "5"]}}},
                "resources": LEADER_RESOURCES,
                "volumeMounts": mounts,
            }
        ],
        "volumes": volumes,
    }
    if spread:
        pod["topologySpreadConstraints"] = [
            {
                "maxSkew": 1,
                "topologyKey": "kubernetes.io/hostname",
                "whenUnsatisfiable": "ScheduleAnyway",
                "labelSelector": {"matchLabels": LEADER_SELECTOR},
            }
        ]
    return pod


def expected_deployment() -> dict:
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": NAME, "labels": LEADER_LABELS},
        "spec": {
            "replicas": 2,
            # A new pod is Ready before an old one is stopped: no pod is missing in a rollout.
            "strategy": {
                "type": "RollingUpdate",
                "rollingUpdate": {"maxUnavailable": 0, "maxSurge": 1},
            },
            "selector": {"matchLabels": LEADER_SELECTOR},
            "template": {
                "metadata": {"labels": LEADER_LABELS, "annotations": {}},
                "spec": expected_leader_pod(),
            },
        },
    }


def expected_job_pod(
    settings: dict[str, str] = SETTINGS, secrets: set[str] = SECRET_NAMES
) -> dict:
    """The migration's pod, whole: the settings inline (a hook runs before the ConfigMap
    exists), the Secret's keys, no volume, no service account of the chart's, no fsGroup."""
    inline = [{"name": name, "value": settings[name]} for name in sorted(settings)]
    return {
        "restartPolicy": "Never",
        "automountServiceAccountToken": False,
        "enableServiceLinks": False,
        "securityContext": {
            "runAsNonRoot": True,
            "runAsUser": 10001,
            "runAsGroup": 10001,
            "seccompProfile": {"type": "RuntimeDefault"},
        },
        "containers": [
            {
                "name": "migrate",
                "image": "swarmscribe-leader:local",
                "imagePullPolicy": "IfNotPresent",
                "args": ["migrate"],
                "env": [*inline, *secret_env(secrets)],
                "securityContext": CONTAINER_CONTEXT,
                "resources": MIGRATE_RESOURCES,
            }
        ],
    }


def expected_job() -> dict:
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": f"{NAME}-migrate", "labels": MIGRATE_LABELS, "annotations": HOOKS},
        # No ttlSecondsAfterFinished: a failed Job is kept, and its log with it.
        "spec": {
            "backoffLimit": 3,
            "activeDeadlineSeconds": 300,
            "template": {"metadata": {"labels": MIGRATE_LABELS}, "spec": expected_job_pod()},
        },
    }


def expected_ingress() -> dict:
    backend = {"service": {"name": NAME, "port": {"name": "http"}}}
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "Ingress",
        "metadata": {"name": NAME, "labels": LABELS},
        # One rule, for the public URL's host; no default backend; TLS for that host.
        "spec": {
            "ingressClassName": "traefik",
            "tls": [{"hosts": [HOST], "secretName": "leader-tls"}],
            "rules": [
                {
                    "host": HOST,
                    "http": {"paths": [{"path": "/v1", "pathType": "Prefix", "backend": backend}]},
                }
            ],
        },
    }


def expected_policy(
    *,
    sign_in: bool = True,
    sources: list[dict] | None = TEST_SOURCES,
    port: int = 8080,
    dns: list[dict] = KUBE_DNS,
    postgres_port: int = 5432,
    https_ports: tuple[int, ...] = (443,),
    more_refused: tuple[str, str] | None = ("10.96.0.0/12", "fd00:10:96::/112"),
    extra: tuple[dict, ...] = (),
) -> dict:
    """The NetworkPolicy's spec, whole. sources=None: any source (anySource)."""
    inbound: dict = {"ports": [{"protocol": "TCP", "port": port}]}
    if sources is not None:
        inbound["from"] = sources
    egress = [
        {"to": dns, "ports": [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}]},
        {"to": TEST_POSTGRES, "ports": [{"protocol": "TCP", "port": postgres_port}]},
    ]
    if sign_in:
        v4 = [*REFUSED_V4, *([more_refused[0]] if more_refused else [])]
        v6 = [*REFUSED_V6, *([more_refused[1]] if more_refused else [])]
        egress.append(
            {
                "to": [
                    {"ipBlock": {"cidr": "0.0.0.0/0", "except": v4}},
                    {"ipBlock": {"cidr": "::/0", "except": v6}},
                ],
                "ports": [{"protocol": "TCP", "port": p} for p in https_ports],
            }
        )
    return {
        # Every pod of this release, the migration's too, and no other release's.
        "podSelector": {"matchLabels": SELECTOR},
        "policyTypes": ["Ingress", "Egress"],
        "ingress": [inbound],
        "egress": [*egress, *extra],
    }


def expected_objects() -> dict[str, dict]:
    """Everything test-values.yaml renders, whole, by kind (one of each). Nothing else may
    be rendered. A fresh copy each time: a variant changes its own."""
    meta = {"name": NAME, "labels": LABELS}
    objects = [
        {"apiVersion": "v1", "kind": "ConfigMap", "metadata": meta, "data": SETTINGS},
        expected_deployment(),
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": meta,
            # ClusterIP, to Ready leader pods only (not the migration's pod).
            "spec": {
                "type": "ClusterIP",
                "selector": LEADER_SELECTOR,
                "ports": [{"name": "http", "port": 80, "targetPort": "http", "protocol": "TCP"}],
            },
        },
        {
            "apiVersion": "v1",
            "kind": "ServiceAccount",
            "metadata": meta,
            "automountServiceAccountToken": False,
        },
        expected_job(),
        expected_ingress(),
        {
            "apiVersion": "policy/v1",
            "kind": "PodDisruptionBudget",
            "metadata": meta,
            "spec": {
                "maxUnavailable": 1,
                "unhealthyPodEvictionPolicy": "AlwaysAllow",
                "selector": {"matchLabels": LEADER_SELECTOR},
            },
        },
        {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": meta,
            "spec": expected_policy(),
        },
    ]
    return {doc["kind"]: copy.deepcopy(doc) for doc in objects}


def for_release(node: object, release: str) -> object:
    """Expected objects under another release name: every object name and the instance label."""
    if isinstance(node, dict):
        return {
            key: release if key == "app.kubernetes.io/instance" else for_release(value, release)
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [for_release(item, release) for item in node]
    if isinstance(node, str) and (node == NAME or node.startswith(f"{NAME}-")):
        return f"{release}-swarmscribe-leader" + node.removeprefix(NAME)
    return node


def compare_all(problems: list[str], what: str, docs: list[dict], wanted: dict[str, dict]) -> None:
    """Everything rendered is exactly `wanted`: the same objects, and each of them whole."""
    label = f"{what}: " if what else ""
    rendered = sorted(str(doc.get("kind")) for doc in docs)
    if rendered != sorted(wanted):
        problems.append(f"{label}the chart renders {rendered}, not exactly {sorted(wanted)}")
    for doc in docs:
        kind = str(doc.get("kind"))
        if kind not in wanted:
            continue
        got = copy.deepcopy(doc)
        if kind == "Deployment":
            # The checksum is of the settings: checked for what it does, in core_variants.
            marks = dig(got, "spec", "template", "metadata", "annotations")
            mark = marks.pop("checksum/settings", "") if isinstance(marks, dict) else ""
            if not re.fullmatch(r"[0-9a-f]{64}", str(mark)):
                problems.append(f"{label}Deployment: the pods carry no checksum of the settings")
        same(problems, f"{label}{kind}", got, wanted[kind])


def compare_objects(docs: list[dict], which: set[str]) -> list[str]:
    """The objects of these kinds that test-values.yaml renders are exactly the expected."""
    problems: list[str] = []
    wanted = {kind: doc for kind, doc in expected_objects().items() if kind in which}
    compare_all(problems, "", [doc for doc in docs if doc.get("kind") in which], wanted)
    return problems


def pod_of(objects: dict[str, dict]) -> dict:
    return objects["Deployment"]["spec"]["template"]["spec"]


def job_pod_of(objects: dict[str, dict]) -> dict:
    return objects["Job"]["spec"]["template"]["spec"]


def variant(
    problems: list[str],
    what: str,
    docs: list[dict],
    change: Callable[[dict[str, dict]], object] | None = None,
    *,
    release: str = "leader",
    without: tuple[str, ...] = (),
) -> None:
    """A render with other values is the test values' objects with exactly this change:
    `change` edits the expected objects, `without` names the kinds that are gone."""
    wanted = expected_objects()
    for kind in without:
        del wanted[kind]
    if change is not None:
        change(wanted)
    compare_all(problems, what, docs, for_release(wanted, release))  # type: ignore[arg-type]


# --- core ---------------------------------------------------------------------------------


def core_objects(docs: list[dict]) -> list[str]:
    problems: list[str] = []
    wanted = expected_objects()
    rendered = sorted(str(doc.get("kind")) for doc in docs)
    if rendered != sorted(wanted):
        problems.append(f"the chart renders {rendered}, not exactly {sorted(wanted)}")
    if "Secret" in kinds(docs):
        problems.append("the chart renders a Secret; it must only reference one")
    if kinds(docs) & {"ServiceMonitor", "PodMonitor", "HorizontalPodAutoscaler", "StatefulSet"}:
        problems.append("the chart renders a kind the leader has nothing for (it has no /metrics)")
    if kinds(docs) & {"PersistentVolumeClaim", "PersistentVolume"}:
        problems.append("the chart renders storage of its own; storage is the operator's")
    problems += compare_objects(docs, {"ConfigMap", "Deployment", "Service", "ServiceAccount"})
    return problems


def core_whole(_docs: list[dict]) -> list[str]:
    """R1: whole objects under other values too. Each render below takes a branch of the
    templates that test-values.yaml does not, and everything it renders is compared with
    the test values' objects changed in exactly the way that value should change them."""
    problems: list[str] = []

    def set_in(kind: str, *keys: object, to: object) -> Callable[[dict[str, dict]], None]:
        def change(objects: dict[str, dict]) -> None:
            node = objects[kind]
            for key in keys[:-1]:
                node = node[key]  # type: ignore[index]
            node[keys[-1]] = to  # type: ignore[index]

        return change

    def gone(kind: str, *keys: object) -> Callable[[dict[str, dict]], None]:
        def change(objects: dict[str, dict]) -> None:
            node = objects[kind]
            for key in keys[:-1]:
                node = node[key]  # type: ignore[index]
            del node[keys[-1]]  # type: ignore[index]

        return change

    def both_images(image: str) -> Callable[[dict[str, dict]], None]:
        def change(objects: dict[str, dict]) -> None:
            pod_of(objects)["containers"][0]["image"] = image
            job_pod_of(objects)["containers"][0]["image"] = image

        return change

    def one_replica(objects: dict[str, dict]) -> None:
        objects["Deployment"]["spec"]["replicas"] = 1
        del pod_of(objects)["topologySpreadConstraints"]

    def no_account(objects: dict[str, dict]) -> None:
        pod_of(objects)["serviceAccountName"] = "default"

    def three(objects: dict[str, dict]) -> None:
        objects["Deployment"]["spec"]["replicas"] = 3

    def labelled(objects: dict[str, dict]) -> None:
        template = objects["Deployment"]["spec"]["template"]["metadata"]
        template["labels"] = {**template["labels"], "app.kubernetes.io/part-of": "swarmscribe"}

    container = ("spec", "template", "spec", "containers", 0)
    # Another release in the same namespace: every name and every selector follows it.
    variant(problems, "release other", render(release="other"), release="other")
    cases: tuple[tuple[str, tuple[str, ...], object, tuple[str, ...]], ...] = (
        # (what, --set values, the change, the kinds that are gone)
        ("ingress.className empty (the default)", ("ingress.className=",),
         gone("Ingress", "spec", "ingressClassName"), ()),
        ("updateStrategy=Recreate", ("updateStrategy=Recreate",),
         set_in("Deployment", "spec", "strategy", to={"type": "Recreate"}), ()),
        ("image.digest", (f"image.digest={DIGEST}",),
         both_images(f"swarmscribe-leader@{DIGEST}"), ()),
        ("serviceAccount.create=false", ("serviceAccount.create=false",),
         no_account, ("ServiceAccount",)),
        ("migrate.backoffLimit=0", ("migrate.backoffLimit=0",),
         set_in("Job", "spec", "backoffLimit", to=0), ()),
        ("migrate.activeDeadlineSeconds=900", ("migrate.activeDeadlineSeconds=900",),
         set_in("Job", "spec", "activeDeadlineSeconds", to=900), ()),
        ("service.type=LoadBalancer", ("service.type=LoadBalancer",),
         set_in("Service", "spec", "type", to="LoadBalancer"), ()),
        ("service.type=NodePort", ("service.type=NodePort",),
         set_in("Service", "spec", "type", to="NodePort"), ()),
        ("replicaCount=3", ("replicaCount=3",), three, ()),
        ("replicaCount=1", ("replicaCount=1",), one_replica, ("PodDisruptionBudget",)),
        ("ingress.enabled=false", ("ingress.enabled=false",), None, ("Ingress",)),
        ("networkPolicy.enabled=false", ("networkPolicy.enabled=false",), None,
         ("NetworkPolicy",)),
        ("migrate.enabled=false", ("migrate.enabled=false",), None, ("Job",)),
        ("podDisruptionBudget.enabled=false", ("podDisruptionBudget.enabled=false",), None,
         ("PodDisruptionBudget",)),
        ("preStopSleepSeconds=0", ("preStopSleepSeconds=0",),
         gone("Deployment", *container, "lifecycle"), ()),
        ("spreadAcrossNodes=false", ("spreadAcrossNodes=false",),
         gone("Deployment", "spec", "template", "spec", "topologySpreadConstraints"), ()),
    )
    for what, values, change, without in cases:
        rendered = render(*(arg for value in values for arg in ("--set", value)))
        variant(problems, what, rendered, change, without=without)  # type: ignore[arg-type]

    variant(
        problems,
        "a label of the operator's own",
        render_with({"podLabels": {"app.kubernetes.io/part-of": "swarmscribe"}}),
        labelled,
    )

    # The guide's whole example file, alone: the render that rides on values.yaml's defaults
    # (Entra only, one claim, no class of groups, the default egress with nothing more cut out).
    def readme(objects: dict[str, dict]) -> None:
        settings = {
            "SWARMSCRIBE_PUBLIC_URL": "https://leader.example.org",
            "SWARMSCRIBE_ENTRA_TENANT_ID": "0f0e0d0c-0b0a-4908-8706-050403020100",
            "SWARMSCRIBE_ENTRA_CLIENT_ID": "6d3a6b52-1c2e-4a8f-9d61-2f6a4c0b7e11",
            "SWARMSCRIBE_ROLE_ADMIN_ENTRA_GROUPS": "3f2b0c5e-8f6d-4a51-9c0e-6f1d2a7b9c11",
        }
        objects["ConfigMap"]["data"] = settings
        objects["Deployment"]["spec"]["template"]["spec"] = expected_leader_pod(
            secrets=ALWAYS_SECRET, volumes=TEST_VOLUMES[:1], mounts=TEST_MOUNTS[:1], groups=()
        )
        objects["Job"]["spec"]["template"]["spec"] = expected_job_pod(settings, ALWAYS_SECRET)
        both_images("registry.example.org/swarmscribe-leader:0.1.0")(objects)
        objects["NetworkPolicy"]["spec"] = expected_policy(
            sources=TEST_SOURCES[:1], more_refused=None
        )

    whole = readme_examples()[0]
    variant(
        problems,
        "the README's example values, alone",
        render_with(whole, base=False),
        readme,
    )

    # The kind test's values, alone: no sign-in, no Ingress, peers by label, another Secret.
    def kind(objects: dict[str, dict]) -> None:
        settings = {
            "SWARMSCRIBE_PUBLIC_URL": "http://leader-swarmscribe-leader",
            **{f"SWARMSCRIBE_{name}": value for name, value in KIND_VALUES["settings"].items()},
        }
        refs = secret_env(ALWAYS_SECRET, "leader")
        objects["ConfigMap"]["data"] = settings
        pod = expected_leader_pod(
            secrets=ALWAYS_SECRET,
            volumes=[
                {"name": "storage-data", "persistentVolumeClaim": {"claimName": "leader-data"}}
            ],
            mounts=[{"name": "storage-data", "mountPath": "/data"}],
            groups=(),
            pull="Never",
        )
        pod["containers"][0]["env"] = refs
        objects["Deployment"]["spec"]["template"]["spec"] = pod
        hook = expected_job_pod(settings, ALWAYS_SECRET)
        hook["containers"][0]["env"] = [e for e in hook["containers"][0]["env"] if "value" in e]
        hook["containers"][0]["env"] += refs
        hook["containers"][0]["imagePullPolicy"] = "Never"
        objects["Job"]["spec"]["template"]["spec"] = hook
        both_images("swarmscribe-leader:kind")(objects)
        peers = KIND_VALUES["networkPolicy"]
        policy = expected_policy(sign_in=False, sources=peers["ingress"]["from"])
        policy["egress"][1]["to"] = peers["egress"]["postgres"]["peers"]
        objects["NetworkPolicy"]["spec"] = policy

    variant(
        problems,
        "the kind test's values, alone",
        render_with(KIND_VALUES, base=False),
        kind,
        without=("Ingress",),
    )
    return problems


def core_defaults(_docs: list[dict]) -> list[str]:
    """values.yaml holds exactly the defaults it must (R1), and every key of it must have a
    value: the schema requires each, and the templates refuse a null (M1)."""
    problems: list[str] = []
    shipped = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))
    same(problems, "values.yaml: the default", shipped, DEFAULTS)
    # Every path that must not be null: each key, at every depth of a map the chart owns.
    paths: list[str] = []
    levels: dict[str, list[str]] = {}

    def walk(node: dict, prefix: str) -> None:
        levels[prefix.rstrip(".")] = [k for k in node if f"{prefix}{k}" not in NULLABLE]
        for key, value in node.items():
            if f"{prefix}{key}" in NULLABLE:
                continue
            paths.append(f"{prefix}{key}")
            if isinstance(value, dict) and f"{prefix}{key}" in OWNED_MAPS:
                walk(value, f"{prefix}{key}.")

    walk(DEFAULTS, "")
    schema = json.loads((CHART / "values.schema.json").read_text(encoding="utf-8"))
    for level, keys in levels.items():
        node = schema
        for key in filter(None, level.split(".")):
            node = dig(node, "properties", key) or {}
            if "$ref" in node:
                node = dig(schema, "definitions", str(node["$ref"]).rpartition("/")[2]) or {}
        same(
            problems,
            f"values.schema.json: `required` of {level or 'the root'}",
            node.get("required"),
            keys,
        )
    helpers = (CHART / "templates" / "_helpers.tpl").read_text(encoding="utf-8")
    listed = re.search(r'"swarmscribe-leader\.requiredValues" -\}\}\n([A-Za-z. ]+)\n', helpers)
    same(
        problems,
        "_helpers.tpl: swarmscribe-leader.requiredValues",
        listed.group(1).split() if listed else None,
        paths,
    )
    # A null, or a key left blank, takes the default away with it. Refused everywhere by
    # the schema; by the templates too for a plain value (a whole map set to null fails in
    # them as well, but in Go's own words).
    for name in paths:
        values: object = None
        for key in reversed(name.split(".")):
            values = {key: values}
        refused_file(problems, f"{name}: null", values, says="missing property")
        if name not in OWNED_MAPS:
            refused_file(
                problems,
                f"{name}: null",
                values,  # type: ignore[arg-type]
                says=name,
                unchecked=True,
            )
    return problems


def notes_chart() -> Path:
    """A copy of the chart with one more template, which renders the chart's own NOTES.txt.

    `helm template` never prints the notes, and `helm install --dry-run` asks a cluster for
    its version on some Helm releases; CI has no cluster. The copy is made from the chart as
    it is when the check runs, so the text under test is templates/NOTES.txt itself, rendered
    by Helm with the same values and helpers (`tpl` of the file, in the chart's context)."""
    target = SCRATCH / "notes-chart"
    if not target.exists():
        shutil.copytree(CHART, target, ignore=shutil.ignore_patterns("ci", "__pycache__"))
        shutil.copyfile(CHART / "templates" / "NOTES.txt", target / "notes.src")
        (target / "templates" / "zz-notes.yaml").write_text(
            "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: zz-notes\n"
            'data:\n  notes: {{ tpl (.Files.Get "notes.src") . | quote }}\n',
            encoding="utf-8",
        )
    return target


def printed_notes(*extra: str) -> str:
    """NOTES.txt as Helm renders it for these values (no install, no cluster)."""
    done = helm_template(*extra, "--show-only", "templates/zz-notes.yaml", chart=notes_chart())
    if done.returncode != 0:
        raise CheckError(f"helm template of the notes failed: {said(done)}")
    # A Windows checkout with core.autocrlf gives NOTES.txt CRLF line ends, and Helm prints
    # them as they are: the text is compared without them.
    notes = str(dig(yaml.safe_load(done.stdout), "data", "notes") or "")
    return "\n".join(notes.splitlines())


def core_notes(_docs: list[dict]) -> list[str]:
    problems: list[str] = []
    source = (CHART / "templates" / "NOTES.txt").read_text(encoding="utf-8")
    if re.search(r"\S {4,}\S", source):
        problems.append("NOTES.txt: a run of spaces inside a line (a broken line continuation?)")
    printed = printed_notes()
    if printed.strip() != NOTES.strip():
        wanted, got = NOTES.strip().splitlines(), printed.strip().splitlines()
        lost = [line for line in wanted if line not in got]
        new = [line for line in got if line not in wanted]
        problems.append(f"NOTES: not the expected text; lost {lost[:3]}, new {new[:3]}")
    # The owner's ruling: two replicas by default, and the notes say what that needs.
    if "MUST SEE THE SAME FILES" not in printed or "shared by every replica" not in printed:
        problems.append("NOTES: two replicas, and nothing says the volume must be shared")
    if "MUST SEE THE SAME FILES" in printed_notes("--set", "replicaCount=1"):
        problems.append("NOTES: one replica is told to share its volume with the others")
    if "request logging off" not in printed:
        problems.append("NOTES: nothing says to turn request logging off for the Ingress")
    if "request logging" in printed_notes("--set", "ingress.enabled=false"):
        problems.append("NOTES: an Ingress is spoken of when there is none")
    if "sign-in keys could not be fetched" not in printed:
        problems.append("NOTES: nothing says how a blocked identity provider shows")
    bare = printed_notes("-f", str(values_file(NO_SIGN_IN)))
    if "NO sign-in" not in bare or "login" in bare:
        problems.append("NOTES: a leader without sign-in is not told so, or is told to sign in")
    if "NetworkPolicy" in printed_notes("--set", "networkPolicy.enabled=false"):
        problems.append("NOTES: a NetworkPolicy is spoken of when there is none")
    # The notes print the address and the mount paths, and nothing else an operator wrote:
    # a proxy URL can hold a password, and an annotation anything at all.
    marked = {
        "extraEnv": [
            {"name": f"{MARK.upper()}_PROXY", "value": f"http://user:{MARK}@proxy.internal:3128"},
            {"name": "HTTPS_PROXY", "valueFrom": {"secretKeyRef": {"name": MARK, "key": MARK}}},
        ],
        "settings": {"LEASE_SECONDS": 98765, "FOLLOWER_GONE_AFTER_SECONDS": "987650"},
        "podAnnotations": {f"example.org/{MARK}": MARK},
        "podLabels": {f"example.org/{MARK}": MARK},
        "nodeSelector": {MARK: MARK},
        "tolerations": [{"key": MARK, "operator": "Exists"}],
        "imagePullSecrets": [{"name": MARK}],
        "serviceAccount": {"name": MARK},
        "ingress": {
            "className": MARK,
            "annotations": {f"example.org/{MARK}": MARK},
            "tls": {"secretName": MARK},
        },
        "secrets": {
            "existingSecret": MARK,
            "keys": dict.fromkeys(DEFAULTS["secrets"]["keys"], MARK),
        },
        "oidc": {"entra": {"clientId": MARK}, "google": {"clientId": MARK, "hostedDomain": MARK}},
        "roles": {"admin": {"entraGroups": [MARK], "emails": [f"{MARK}@example.org"]}},
        "networkPolicy": {"egress": {"https": {"extraExcept": ["10.98.76.0/24"]}}},
    }
    told = printed_notes("-f", str(values_file(marked))).lower()
    for marker in (MARK, "98765", "10.98.76"):
        if marker in told:
            line = next(line for line in told.splitlines() if marker in line)
            problems.append(f"NOTES: a value the operator wrote is printed ({line.strip()[:120]})")
    return problems


def core_no_secret(_docs: list[dict]) -> list[str]:
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
    keys = {
        "databaseUrl": "zz-database-url",
        "linkKey": "zz-link-key",
        "entraClientSecret": "zz-entra",
        "googleClientSecret": "zz-google",
        "googleServiceAccount": "zz-account",
    }
    path = str(values_file({"secrets": {"existingSecret": "zz-secret", "keys": keys}}))
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

    marked = render("-f", path)
    for doc in marked:
        for where in strays(doc, str(doc.get("kind"))):
            problems.append(f"{where}: holds the Secret's name or a key outside a secretKeyRef")
    # And each of the five keys is the one read for its variable.
    refs = [
        {"name": name, "valueFrom": {"secretKeyRef": {"name": "zz-secret", "key": key}}}
        for name, key in zip(SECRET_KEYS, keys.values(), strict=True)
    ]
    same(problems, "Deployment (named keys): env", leader_container(marked).get("env"), refs)
    return problems


def core_settings(_docs: list[dict]) -> list[str]:
    """The chart and the leader agree on every setting's name (config.py is the authority)."""
    problems: list[str] = []
    fields = leader_fields()
    secrets = {name for name, (kind, _) in fields.items() if "SecretStr" in kind}
    role_lists = {name for name, (kind, _) in fields.items() if kind == "NameList"}
    plain = plain_settings(fields)

    def env(names: set[str]) -> set[str]:
        return {f"SWARMSCRIBE_{name.upper()}" for name in names}

    if env(secrets) != SECRET_NAMES:
        problems.append(
            f"config.py's secrets are {sorted(env(secrets))}; the chart reads "
            f"{sorted(SECRET_NAMES)} from the Secret"
        )
    for name, value in OWNED.items():
        if name not in fields or "SecretStr" in fields[name][0]:
            problems.append(f"{value} sets {name}, which is not a plain setting in config.py")
    # Every name the chart can write is a setting the leader has: it ignores a variable it
    # does not know, so a misspelt one would be silently without effect.
    every = {"entraGroups": ["g"], "googleGroups": ["g@example.org"]}
    every |= {"emails": ["a@example.org"], "domains": ["example.org"]}
    all_roles = render_with({"roles": dict.fromkeys(("viewer", "operator", "admin"), every)})
    full = dig(one(all_roles, "ConfigMap"), "data") or {}
    written = {name for name in full if name.startswith("SWARMSCRIBE_ROLE_")}
    same(problems, "ConfigMap: the role lists", sorted(written), sorted(env(role_lists)))
    if len(role_lists) != 12:
        problems.append(f"config.py has {len(role_lists)} role lists, not twelve")
    for name in sorted({*SETTINGS, *full, *SECRET_NAMES}):
        if name.removeprefix("SWARMSCRIBE_").lower() not in fields:
            problems.append(f"{name} is not a setting of the leader (config.py)")

    # `settings` takes exactly the leader's plain settings: the schema, the templates' own
    # list and config.py say the same eleven names, each with its type and its bound.
    schema = json.loads((CHART / "values.schema.json").read_text(encoding="utf-8"))
    named = dig(schema, "properties", "settings", "properties") or {}
    wanted = {name.upper(): {"$ref": f"#/definitions/{plain[name][0]}"} for name in plain}
    same(problems, "values.schema.json: settings.properties", named, wanted)
    if dig(schema, "properties", "settings", "additionalProperties") is not False:
        problems.append("values.schema.json: settings takes names it does not list")
    helpers = (CHART / "templates" / "_helpers.tpl").read_text(encoding="utf-8")
    listed = re.search(r'define "swarmscribe-leader\.settingNames" -\}\}\n([A-Z_ ]+)\n', helpers)
    same(
        problems,
        "_helpers.tpl: swarmscribe-leader.settingNames",
        sorted(listed.group(1).split()) if listed else None,
        sorted(wanted),
    )
    documented = (CHART / "values.yaml").read_text(encoding="utf-8")
    for name in sorted(wanted):
        default = plain[name.lower()][1]
        if not re.search(rf"^#   {name} \[{default}\]", documented, flags=re.MULTILINE):
            problems.append(f"values.yaml does not list settings.{name} with its default {default}")

    # Each of them is passed on, under the name the leader reads, as the string it parses.
    each = {name.upper(): str(700 + index) for index, name in enumerate(sorted(plain))}
    each |= {"LEASE_SECONDS": "100", "HEARTBEAT_SECONDS": "10"}
    data = dig(one(render_with({"settings": each}), "ConfigMap"), "data") or {}
    for name, value in each.items():
        if data.get(f"SWARMSCRIBE_{name}") != value:
            problems.append(f"settings.{name} is not passed on as SWARMSCRIBE_{name}")
    # I6: a number arrives written out in full, never as 1e+06; and a fraction as one.
    numbers = {
        "FOLLOWER_GONE_AFTER_SECONDS": (1000000, "1000000"),
        "UPLOAD_LINK_TTL_SECONDS": (2000000000, "2000000000"),
        "REAPER_INTERVAL_SECONDS": (0.5, "0.5"),
        "SCANNER_INTERVAL_SECONDS": (2, "2"),
        "LINKS_REFRESH_MIN_SECONDS": (0, "0"),
        "CLAIM_RETRY_AFTER": ("010", "010"),
    }
    numbered = render_with({"settings": {name: given for name, (given, _) in numbers.items()}})
    data = dig(one(numbered, "ConfigMap"), "data") or {}
    hook_env = dig(job_pod(numbered), "containers", 0, "env") or []
    inline = {e.get("name"): e.get("value") for e in hook_env}
    for name, (given, value) in numbers.items():
        for where, got in (("ConfigMap", data), ("Job", inline)):
            if got.get(f"SWARMSCRIBE_{name}") != value:
                problems.append(
                    f"{where}: settings.{name}: {given!r} arrives as "
                    f"{got.get(f'SWARMSCRIBE_{name}')!r}, not {value!r}"
                )
    with_set = render("--set", "settings.FOLLOWER_GONE_AFTER_SECONDS=1000000")
    if dig(one(with_set, "ConfigMap"), "data", "SWARMSCRIBE_FOLLOWER_GONE_AFTER_SECONDS") != (
        "1000000"
    ):
        problems.append("ConfigMap: a large number given with --set is not written out in full")
    for what, value in (
        ("a null setting", None),
        ("a map as a setting", {"a": 1}),
        ("a list as a setting", [1]),
        ("a boolean setting", True),
        ("a setting of 0", 0),
        ("a negative setting", -5),
        ("a fraction for a whole number of seconds", 1.5),
        ("a setting that is not a number", "soon"),
        ("an empty setting", ""),
        ("a setting in exponent form", "1e3"),
    ):
        refused_file(problems, what, {"settings": {"LEASE_SECONDS": value}}, says="LEASE_SECONDS")
    for what, value in (("a null setting", None), ("a map as a setting", {"a": 1})):
        refused_file(
            problems,
            what,
            {"settings": {"LEASE_SECONDS": value}},
            says="must be a number or a string",
            unchecked=True,
        )
    huge = {"settings": {"FOLLOWER_GONE_AFTER_SECONDS": 10**21}}
    refused_twice(problems, "a setting of 10^21", huge, schema="maximum", template="is too large")
    refused_file(
        problems,
        "a setting of twenty digits",
        {"settings": {"FOLLOWER_GONE_AFTER_SECONDS": "1" + "0" * 19}},
        says="FOLLOWER_GONE_AFTER_SECONDS",
    )
    refused_file(problems, "an interval of 0", {"settings": {"REAPER_INTERVAL_SECONDS": 0}})
    refused_file(problems, "an interval of 0.0", {"settings": {"REAPER_INTERVAL_SECONDS": "0.0"}})

    # Names that are not the eleven: a typo, a secret, a name another value sets. The schema
    # refuses each, and so do the templates with the schema off.
    for what, name in (
        ("a misspelt setting", "LEASE_SECOND"),
        ("the link key under settings", "LINK_KEY"),
        ("a lower-case link key under settings", "link_key"),
        ("a lower-case database_url under settings", "database_url"),
        ("a mixed-case link key under settings", "Link_Key"),
        ("a client secret under settings", "ENTRA_CLIENT_SECRET"),
        ("a mixed-case *_secret under settings", "Google_Client_Secret"),
        ("google_service_account under settings", "google_service_account"),
        ("public_url under settings", "public_url"),
        ("a role list under settings", "ROLE_ADMIN_EMAILS"),
        ("a lower-case role list under settings", "role_admin_domains"),
        ("the Entra tenant under settings", "ENTRA_TENANT_ID"),
        ("the Google hosted domain under settings", "GOOGLE_HOSTED_DOMAIN"),
        ("a lower-case setting name", "lease_seconds"),
        ("a setting nobody has (API_TOKEN)", "API_TOKEN"),
        ("a database password under settings", "DB_PASSWORD"),
        ("a signing key under settings", "SIGNING_KEY"),
        ("a setting name with a space", "A B"),
        ("a setting name with a newline", "A\nKEY"),
        ("a setting name that injects a ConfigMap entry", "X: y\n  SWARMSCRIBE_LINK_KEY"),
    ):
        refused_twice(
            problems,
            what,
            {"settings": {name: "4"}},
            schema="additional properties",
            template="is not a setting the chart passes on",
        )
    # ROLE_CACHE_SECONDS is a setting like any other: no chart value sets it.
    cached = render_with({"settings": {"ROLE_CACHE_SECONDS": 60}})
    if dig(one(cached, "ConfigMap"), "data", "SWARMSCRIBE_ROLE_CACHE_SECONDS") != "60":
        problems.append("ConfigMap: settings.ROLE_CACHE_SECONDS is not passed on")

    # The leader's two rules across settings, at its own defaults (read from config.py).
    lease, heartbeat = plain["lease_seconds"][1], plain["heartbeat_seconds"][1]
    gone = plain["follower_gone_after_seconds"][1]
    for what, values, says in (
        (
            "a lease no longer than the default heartbeat",
            {"LEASE_SECONDS": heartbeat},
            "must be shorter than LEASE_SECONDS",
        ),
        (
            "a lease as long as the default follower_gone_after_seconds",
            {"LEASE_SECONDS": gone},
            "must be longer than LEASE_SECONDS",
        ),
        (
            "a heartbeat as long as the default lease",
            {"HEARTBEAT_SECONDS": str(lease)},
            "must be shorter than LEASE_SECONDS",
        ),
        (
            "follower_gone_after_seconds no longer than the default lease",
            {"FOLLOWER_GONE_AFTER_SECONDS": lease},
            "must be longer than LEASE_SECONDS",
        ),
    ):
        refused_file(problems, what, {"settings": values}, says=says)
    for values in ({"LEASE_SECONDS": heartbeat + 1}, {"LEASE_SECONDS": gone - 1}):
        render_with({"settings": values})

    # extraEnv: what is not a setting of the leader. No SWARMSCRIBE_ name at all.
    for name in (
        "SWARMSCRIBE_LINK_KEY",
        "swarmscribe_database_url",
        "Swarmscribe_Entra_Client_Secret",
        "SWARMSCRIBE_PUBLIC_URL",
        "SWARMSCRIBE_ROLE_ADMIN_EMAILS",
        "swarmscribe_role_operator_domains",
        "swarmscribe_google_service_account",
        "SWARMSCRIBE_GOOGLE_HOSTED_DOMAIN",
        "SWARMSCRIBE_LEASE_SECONDS",
        "SWARMSCRIBE_ANYTHING",
    ):
        refused_file(
            problems,
            f"{name} under extraEnv",
            {"extraEnv": [{"name": name, "value": "x"}]},
            says="is a setting of the leader",
        )
    refused_file(
        problems,
        "a secret under extraEnv by valueFrom",
        {
            "extraEnv": [
                {"name": "SWARMSCRIBE_LINK_KEY", "valueFrom": {"fieldRef": {"fieldPath": "x"}}}
            ]
        },
        says="is a setting of the leader",
    )
    proxy = {"name": "HTTPS_PROXY", "value": "http://proxy:3128"}
    refused_file(
        problems, "a name twice under extraEnv", {"extraEnv": [proxy, proxy]}, says="twice"
    )
    refused_file(
        problems,
        "an extraEnv entry with a value and a valueFrom",
        {"extraEnv": [{**proxy, "valueFrom": {"fieldRef": {"fieldPath": "x"}}}]},
        says="both value and valueFrom",
    )
    refused_file(problems, "an extraEnv entry without a name", {"extraEnv": [{"value": "x"}]})
    refused_file(
        problems, "an extraEnv name with a space", {"extraEnv": [{"name": "A B", "value": "x"}]}
    )
    no_proxy = {"name": "NO_PROXY", "value": ".svc"}
    proxied = render_with({"extraEnv": [proxy, no_proxy]})
    same(
        problems,
        "Deployment (extraEnv): env",
        leader_container(proxied).get("env"),
        [*secret_env(SECRET_NAMES), proxy, no_proxy],
    )
    # The Job calls nobody but Postgres: extraEnv is not its business.
    same(problems, "Job (extraEnv)", job_pod(proxied), expected_job_pod())
    return problems


def core_roles(_docs: list[dict]) -> list[str]:
    """A role's emails and domains are refused when the leader would refuse them at start
    (config.py: `_entries_are_well_formed`), and never when it would accept them."""
    problems: list[str] = []
    is_domain = leader_is_domain()
    schema = json.loads((CHART / "values.schema.json").read_text(encoding="utf-8"))
    lists = dig(schema, "definitions", "roleLists", "properties") or {}
    for key, kind in (("emails", "email"), ("domains", "domain")):
        same(
            problems,
            f"values.schema.json: roleLists.{key}",
            lists.get(key),
            {"type": "array", "items": {"$ref": f"#/definitions/{kind}"}},
        )

    def leader_accepts(kind: str, entry: str) -> bool:
        # As config.py: strip, lower-case ASCII, drop one leading "@", then the rule.
        name = entry.strip()
        name = (name.lower() if name.isascii() else name).removeprefix("@")
        if not name or not name.isascii():
            return False
        if kind == "domain":
            return is_domain(name)
        local, at, domain = name.partition("@")
        return bool(local) and at == "@" and is_domain(domain)

    good = {
        "email": ("owner@example.org", "First.Last+tag@Example.ORG", "a_b-c@sub.example.org"),
        "domain": ("example.org", "Sub.Example.ORG", "a-b.example.co.uk"),
    }
    bad = {
        "email": (
            "owner",
            "owner@",
            "@example.org",
            "owner@localhost",
            "owner@*.example.org",
            "a@b@example.org",
            "owner@exa mple.org",
            "owner@example.org,x@example.org",
            "Kate@example.org",
            "owner@exämple.org",
            "owner@example.org\n",
            "",
        ),
        "domain": (
            "localhost",
            "*.example.org",
            "owner@example.org",
            "example .org",
            "example.org,example.com",
            "exämple.org",
            "example.org\n",
            "",
        ),
    }
    for kind in ("email", "domain"):
        pattern = str(dig(schema, "definitions", kind, "pattern"))
        if not (pattern.startswith("^") and pattern.endswith("$")):
            problems.append(f"values.schema.json: the {kind} pattern is not anchored: {pattern}")
        for entry in (*good[kind], *bad[kind]):
            # \Z: Python's $ also matches before a final newline, which Go's does not.
            accepted = re.match(pattern.removesuffix("$") + r"\Z", entry) is not None
            if accepted and not leader_accepts(kind, entry):
                problems.append(f"the schema takes the {kind} {entry!r}, which the leader refuses")
            if entry in good[kind] and not (accepted and leader_accepts(kind, entry)):
                problems.append(f"the well-formed {kind} {entry!r} is refused (schema: {accepted})")
            if entry in bad[kind] and accepted:
                problems.append(f"the schema takes the malformed {kind} {entry!r}")
    # And Helm reads the patterns as this check does.
    for what, entries in (
        ("an email without @", {"emails": ["owner"]}),
        ("an email at a host without a dot", {"emails": ["owner@localhost"]}),
        ("an email with letters that are not ASCII", {"emails": ["Kate@example.org"]}),
        ("an email with two @", {"emails": ["a@b@example.org"]}),
        ("a domain without a dot", {"domains": ["localhost"]}),
        ("a wildcard domain", {"domains": ["*.example.org"]}),
        ("an email as a domain", {"domains": ["owner@example.org"]}),
        ("a domain with letters that are not ASCII", {"domains": ["exämple.org"]}),
        (
            "a role entry with a comma (it would become two entries)",
            {"emails": ["a@example.org,b@example.org"]},
        ),
        ("an Entra group with a space", {"entraGroups": ["a b"]}),
        ("a Google group with a comma", {"googleGroups": ["a@example.org,b@example.org"]}),
    ):
        refused_file(problems, what, {"roles": {"admin": entries}}, says="/roles/admin/")
    accepted = render_with(
        {"roles": {"viewer": {"emails": list(good["email"]), "domains": list(good["domain"])}}}
    )
    data = dig(one(accepted, "ConfigMap"), "data") or {}
    if data.get("SWARMSCRIBE_ROLE_VIEWER_EMAILS") != ",".join(good["email"]):
        problems.append("ConfigMap: well-formed emails are not passed on, comma-separated")
    if data.get("SWARMSCRIBE_ROLE_VIEWER_DOMAINS") != ",".join(good["domain"]):
        problems.append("ConfigMap: well-formed domains are not passed on, comma-separated")
    return problems


def core_variants(docs: list[dict]) -> list[str]:
    """Other values change what they should, and nothing else."""
    problems: list[str] = []
    # A changed setting restarts the pods; an unchanged one does not.
    marks = [
        dig(one(rendered, "Deployment"), "spec", "template", "metadata", "annotations")
        for rendered in (docs, render(), render("--set-string", "settings.LEASE_SECONDS=121"))
    ]
    sums = [mark.get("checksum/settings") if isinstance(mark, dict) else None for mark in marks]
    if not sums[0] or sums[0] != sums[1] or sums[0] == sums[2]:
        problems.append("Deployment: the pods' checksum does not follow the settings")
    if "lifecycle" in leader_container(render("--set", "preStopSleepSeconds=0")):
        problems.append("Deployment: preStopSleepSeconds=0 still renders a preStop hook")
    recreate = one(render("--set", "updateStrategy=Recreate"), "Deployment")
    same(
        problems,
        "Deployment (updateStrategy=Recreate): strategy",
        dig(recreate, "spec", "strategy"),
        {"type": "Recreate"},
    )

    moved = render("--set", "port=9090")
    same(problems, "Deployment (port=9090)", pod_spec(moved), expected_leader_pod(port=9090))
    same(problems, "NetworkPolicy (port=9090)", policy_spec(moved), expected_policy(port=9090))
    same(
        problems,
        "Service (port=9090): ports",
        dig(one(moved, "Service"), "spec", "ports"),
        [{"name": "http", "port": 80, "targetPort": "http", "protocol": "TCP"}],
    )

    # A leader with neither optional secret: only the two every leader needs are read.
    entra_only = {
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
    lean = render_with(entra_only)
    lean_pod = expected_leader_pod(secrets=ALWAYS_SECRET)
    same(problems, "Deployment (Entra only)", pod_spec(lean), lean_pod)
    lean_settings = {
        name: SETTINGS[name]
        for name in (
            "SWARMSCRIBE_PUBLIC_URL",
            "SWARMSCRIBE_ENTRA_TENANT_ID",
            "SWARMSCRIBE_ENTRA_CLIENT_ID",
            "SWARMSCRIBE_ROLE_ADMIN_ENTRA_GROUPS",
            "SWARMSCRIBE_LEASE_SECONDS",
        )
    }
    same(problems, "ConfigMap (Entra only)", dig(one(lean, "ConfigMap"), "data"), lean_settings)
    same(
        problems,
        "Job (Entra only)",
        job_pod(lean),
        expected_job_pod(lean_settings, ALWAYS_SECRET),
    )
    bare = render_with(
        NO_SIGN_IN,
        "--set",
        "networkPolicy.ingress.anySource=true",
        "--set",
        "networkPolicy.ingress.from=null",
    )
    same(problems, "Deployment (no sign-in)", pod_spec(bare), lean_pod)
    plain = {
        "SWARMSCRIBE_PUBLIC_URL": "https://leader.example.org",
        "SWARMSCRIBE_LEASE_SECONDS": "120",
    }
    same(problems, "ConfigMap (no sign-in)", dig(one(bare, "ConfigMap"), "data"), plain)
    same(problems, "Job (no sign-in)", job_pod(bare), expected_job_pod(plain, ALWAYS_SECRET))

    pinned = leader_container(render("--set", f"image.digest={DIGEST}")).get("image")
    if pinned != f"swarmscribe-leader@{DIGEST}":
        problems.append("Deployment: image.digest does not win over the tag")
    return problems


def core_sign_in(_docs: list[dict]) -> list[str]:
    problems: list[str] = []
    refused(problems, "no image repository", "image.repository=")
    refused(problems, "no image tag", "image.tag=", says="image.tag is required")
    refused(problems, "no publicUrl", "publicUrl=")
    refused(problems, "no Secret", "secrets.existingSecret=")
    # oidc.allowNone is not named here: it is the default (false) that must refuse this.
    providers_off = {key: value for key, value in NO_SIGN_IN["oidc"].items() if key != "allowNone"}
    refused_file(
        problems,
        "no sign-in provider (oidc.allowNone left at its default)",
        {**NO_SIGN_IN, "oidc": providers_off},
        says="without sign-in refuses every admin call",
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
    refused_file(problems, "an unknown role", {"roles": {"owner": {"emails": ["a@example.org"]}}})
    refused_file(problems, "an unknown role list", {"roles": {"admin": {"users": ["a"]}}})
    return problems


def core_public_url(_docs: list[dict]) -> list[str]:
    problems: list[str] = []
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
    for port in ("99999", "0", "65536"):
        refused_file(
            problems,
            f"a publicUrl with the port {port}",
            {"publicUrl": f"https://leader.example.org:{port}", "ingress": {"enabled": False}},
            says="is not a port",
        )
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
    if dig(one(inside, "ConfigMap"), "data", "SWARMSCRIBE_PUBLIC_URL") != (
        "http://leader-swarmscribe-leader"
    ):
        problems.append("ConfigMap: an allowed http publicUrl is not passed on")
    for port in ("8443", "65535", "1"):
        url = f"https://leader.example.org:{port}"
        ported = render_with({"publicUrl": url, "ingress": {"enabled": False}})
        if dig(one(ported, "ConfigMap"), "data", "SWARMSCRIBE_PUBLIC_URL") != url:
            problems.append(f"ConfigMap: a publicUrl with the port {port} is not passed on")
    return problems


def core_numbers(_docs: list[dict]) -> list[str]:
    problems: list[str] = []
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
        ("a grace period too short for a leader to stop in", "terminationGracePeriodSeconds=29"),
        ("a negative preStop sleep", "preStopSleepSeconds=-1"),
        ("a preStop sleep of an hour", "preStopSleepSeconds=3600"),
        ("an unknown update strategy", "updateStrategy=OnDelete"),
        ("a malformed digest", "image.digest=notadigest"),
        ("a bad fullnameOverride", "fullnameOverride=Not_Valid"),
        ("a nameOverride of 64 characters", "nameOverride=" + "n" * 64),
        ("a Postgres port of 0", "networkPolicy.egress.postgres.port=0"),
        ("a migration retried a negative number of times", "migrate.backoffLimit=-1"),
        ("a migration deadline of 0", "migrate.activeDeadlineSeconds=0"),
        ("a budget that lets no pod go", "podDisruptionBudget.maxUnavailable=0"),
    ):
        refused(problems, what, value)
    for what, values in (
        ('a budget of "0"', {"podDisruptionBudget": {"maxUnavailable": "0"}}),
        ('a budget of "0%"', {"podDisruptionBudget": {"maxUnavailable": "0%"}}),
        ('a budget of "half"', {"podDisruptionBudget": {"maxUnavailable": "half"}}),
        ("an empty key name in the Secret", {"secrets": {"keys": {"linkKey": ""}}}),
        ("a key name with a slash in the Secret", {"secrets": {"keys": {"databaseUrl": "a/b"}}}),
        ("a pull secret without a name", {"imagePullSecrets": [{}]}),
        ("a supplemental group of 0", {"storage": {"supplementalGroups": [0]}}),
    ):
        refused_file(problems, what, values)
    # The leader needs about 17 s to stop after the preStop sleep: 25 are kept for it.
    refused_file(
        problems,
        "a grace period of 40 s after a preStop sleep of 20",
        {"terminationGracePeriodSeconds": 40, "preStopSleepSeconds": 20},
        says="must be at least preStopSleepSeconds (20) plus 25",
    )
    for grace, sleep in ((30, 5), (25, 0), (45, 20)):
        render_with({"terminationGracePeriodSeconds": grace, "preStopSleepSeconds": sleep})
    half = render_with({"podDisruptionBudget": {"maxUnavailable": "50%"}})
    if dig(one(half, "PodDisruptionBudget"), "spec", "maxUnavailable") != "50%":
        problems.append("PodDisruptionBudget: a percentage is not passed on")
    return problems


def schema_objects(node: object, at: str = "") -> Iterator[tuple[str, str, dict]]:
    """Every object and list the schema describes: (its path, "object" or "array", itself).
    A definition's path begins with # and its name."""
    if not isinstance(node, dict):
        return
    types = node.get("type")
    for kind in ("object", "array"):
        if kind in (types if isinstance(types, list) else [types]):
            yield at, kind, node
    for key, child in (node.get("properties") or {}).items():
        yield from schema_objects(child, f"{at}.{key}" if at else key)
    if "items" in node:
        yield from schema_objects(node["items"], at + "[]")
    for child in node.get("anyOf") or []:
        yield from schema_objects(child, at)
    for key, child in (node.get("definitions") or {}).items():
        yield from schema_objects(child, f"#{key}")


def volume_of(source: dict) -> dict:
    return {"storage": {"volumes": [{"name": "a", "mountPath": "/data/a", "volume": source}]}}


# One unknown key for every object of the schema that refuses them: (the object's path as
# schema_objects names it, values holding a key that object does not have, the key).
UNKNOWN_KEYS: tuple[tuple[str, dict, str], ...] = (
    ("", {"replicas": 5}, "replicas"),
    ("", {"ingres": {"enabled": False}}, "ingres"),
    ("image", {"image": {"tagg": "x"}}, "tagg"),
    ("imagePullSecrets[]", {"imagePullSecrets": [{"name": "a", "namespace": "b"}]}, "namespace"),
    ("secrets", {"secrets": {"name": "x"}}, "name"),
    ("secrets.keys", {"secrets": {"keys": {"databaseURL": "database-url"}}}, "databaseURL"),
    ("oidc", {"oidc": {"provider": "entra"}}, "provider"),
    ("oidc.entra", {"oidc": {"entra": {"tenant": "x"}}}, "tenant"),
    ("oidc.google", {"oidc": {"google": {"domain": "example.org"}}}, "domain"),
    ("roles", {"roles": {"owner": {"emails": ["a@example.org"]}}}, "owner"),
    ("#roleLists", {"roles": {"admin": {"users": ["a"]}}}, "users"),
    ("settings", {"settings": {"LEASE_SECOND": "4"}}, "LEASE_SECOND"),
    ("extraEnv[]", {"extraEnv": [{"name": "A", "valuefrom": {}}]}, "valuefrom"),
    ("storage", {"storage": {"fsgroup": 2000}}, "fsgroup"),
    (
        "storage.volumes[]",
        {"storage": {"volumes": [{"name": "a", "mountPath": "/d", "subPath": "x"}]}},
        "subPath",
    ),
    ("storage.volumes[].volume", volume_of({"secret": {}}), "secret"),
    (
        "storage.volumes[].volume.persistentVolumeClaim",
        volume_of({"persistentVolumeClaim": {"claimName": "a", "claim": "b"}}),
        "claim",
    ),
    (
        "storage.volumes[].volume.nfs",
        volume_of({"nfs": {"server": "s", "path": "/p", "host": "h"}}),
        "host",
    ),
    (
        "storage.volumes[].volume.csi",
        volume_of({"csi": {"driver": "d", "volumeHandle": "h"}}),
        "volumeHandle",
    ),
    (
        "storage.volumes[].volume.csi.nodePublishSecretRef",
        volume_of({"csi": {"driver": "d", "nodePublishSecretRef": {"name": "n", "key": "k"}}}),
        "key",
    ),
    ("service", {"service": {"nodePort": 30080}}, "nodePort"),
    ("ingress", {"ingress": {"host": "x.example.org"}}, "host"),
    ("ingress.tls", {"ingress": {"tls": {"secret": "x"}}}, "secret"),
    (
        "ingress.paths[]",
        {"ingress": {"paths": [{"path": "/v1", "pathType": "Prefix", "backend": "x"}]}},
        "backend",
    ),
    ("migrate", {"migrate": {"enable": False}}, "enable"),
    ("#resources", {"resources": {"request": {"cpu": "1"}}}, "request"),
    ("#resources", {"migrate": {"resources": {"limit": {"cpu": "1"}}}}, "limit"),
    ("podDisruptionBudget", {"podDisruptionBudget": {"minAvailable": 1}}, "minAvailable"),
    ("networkPolicy", {"networkPolicy": {"enable": False}}, "enable"),
    ("networkPolicy.ingress", {"networkPolicy": {"ingress": {"to": []}}}, "to"),
    ("networkPolicy.egress", {"networkPolicy": {"egress": {"postgress": {}}}}, "postgress"),
    ("networkPolicy.egress.dns", {"networkPolicy": {"egress": {"dns": {"peer": []}}}}, "peer"),
    (
        "networkPolicy.egress.postgres",
        {"networkPolicy": {"egress": {"postgres": {"peer": []}}}},
        "peer",
    ),
    (
        "networkPolicy.egress.https",
        {"networkPolicy": {"egress": {"https": {"cidr": "10.0.0.0/8"}}}},
        "cidr",
    ),
    (
        "networkPolicy.egress.extra[]",
        {
            "networkPolicy": {
                "egress": {
                    "extra": [
                        {
                            "to": [{"ipBlock": {"cidr": "10.9.9.9/32"}}],
                            "ports": [{"port": 1}],
                            "from": [],
                        }
                    ]
                }
            }
        },
        "from",
    ),
    (
        "#peer",
        {"networkPolicy": {"ingress": {"from": [{"ipblock": {"cidr": "10.0.0.0/8"}}]}}},
        "ipblock",
    ),
    (
        "#peer.ipBlock",
        {"networkPolicy": {"ingress": {"from": [{"ipBlock": {"cidr": "10.0.0.0/8", "to": 1}}]}}},
        "to",
    ),
    ("serviceAccount", {"serviceAccount": {"annotations": {}}}, "annotations"),
)


def core_schema(_docs: list[dict]) -> list[str]:
    """I2: a key the chart does not have is refused at every level, the root included."""
    problems: list[str] = []
    schema = json.loads((CHART / "values.schema.json").read_text(encoding="utf-8"))
    closed, open_, maps, lists = set(), set(), set(), {}
    for at, kind, node in schema_objects(schema):
        if kind == "array":
            lists[at] = node
        elif node.get("additionalProperties") is False:
            closed.add(at)
        elif isinstance(node.get("additionalProperties"), dict):
            maps.add(at)
        else:
            open_.add(at)
    for at in sorted(open_ - OPEN_OBJECTS):
        problems.append(
            f"values.schema.json: {at or 'the root'} takes keys it does not name "
            '(no "additionalProperties": false)'
        )
    for at in sorted(OPEN_OBJECTS - open_):
        problems.append(f"values.schema.json: {at} is no longer an open object (update this check)")
    same(problems, "values.schema.json: the maps of strings", sorted(maps), sorted(STRING_MAPS))
    for at in sorted(NEVER_EMPTY):
        if not isinstance(lists.get(at), dict) or lists[at].get("minItems", 0) < 1:
            problems.append(f"values.schema.json: {at} may be an empty list (no minItems)")
    for at in sorted(closed - {level for level, _, _ in UNKNOWN_KEYS}):
        problems.append(f"this check has no unknown key for the schema's {at or 'root'}")
    for level, values, key in UNKNOWN_KEYS:
        if level not in closed:
            problems.append(f"values.schema.json: {level or 'the root'} is not a closed object")
        refused_file(
            problems,
            f"an unknown key ({key}) under {level or 'the root'}",
            values,
            says=f"additional properties '{key}' not allowed",
        )
    # Chart.yaml: the PodDisruptionBudget's unhealthyPodEvictionPolicy needs Kubernetes 1.27.
    same(
        problems,
        "Chart.yaml",
        {k: v for k, v in CHART_FILE.items() if k not in ("description", "version", "appVersion")},
        {
            "apiVersion": "v2",
            "name": "swarmscribe-leader",
            "type": "application",
            "kubeVersion": ">=1.27.0-0",
        },
    )
    _must_fail(problems, "Kubernetes 1.26", helm_template("--kube-version", "1.26.0"), "1.26")
    return problems


def core_package(_docs: list[dict]) -> list[str]:
    """The packaged chart holds its fourteen files: not ci/, and no template more."""
    problems: list[str] = []
    target = SCRATCH / "package"
    done = subprocess.run(
        [HELM, "package", str(CHART), "--destination", str(target)], capture_output=True, text=True
    )
    if done.returncode != 0:
        raise CheckError(f"helm package failed: {said(done)}")
    (archive,) = target.glob("*.tgz")
    with tarfile.open(archive) as packed:
        files = {name.partition("/")[2] for name in packed.getnames()}
    same(problems, "the packaged chart's files", sorted(files), sorted(PACKAGED))
    return problems


def readme_examples() -> list[dict]:
    """The values in the guide ("Deploy the leader"): the whole file first, then each
    fragment an operator is told to lay over it."""
    text = README.read_text(encoding="utf-8")
    start = text.find("\n## Deploy the leader\n")
    if start < 0:
        raise CheckError('README.md has no "## Deploy the leader"')
    section = text[start : text.find("\n## ", start + 5)]
    blocks = re.findall(r"^ *```yaml\n(.*?)^ *```$", section, flags=re.DOTALL | re.MULTILINE)
    examples = [yaml.safe_load(textwrap.dedent(body)) for body in blocks]
    if len(examples) < 3 or "publicUrl" not in (examples[0] or {}):
        raise CheckError(
            f"README.md, Deploy the leader: {len(examples)} yaml examples, and the first is "
            "not a whole values file (did the guide's examples move?)"
        )
    return examples


def validated(what: str, manifests: str) -> list[str]:
    """kubeconform's verdict on rendered manifests, when $KUBECONFORM names it."""
    tool = os.environ.get("KUBECONFORM")
    if not tool:
        return []
    path = SCRATCH / f"rendered-{next(_numbers)}.yaml"
    path.write_text(manifests, encoding="utf-8")
    done = subprocess.run(
        [tool, "-strict", "-summary", "-kubernetes-version", "1.33.0", str(path)],
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        said = (done.stdout + done.stderr).strip()[-300:]
        return [f"{what}: kubeconform refuses the manifests: {said}"]
    return []


def core_must_render(_docs: list[dict]) -> list[str]:
    """What people will really install keeps rendering: the guide's example values, and the
    kind test's. With $KUBECONFORM set (CI), each is validated against Kubernetes' schemas."""
    problems: list[str] = []
    whole, *fragments = readme_examples()
    cases = [("the README's example values", [whole], 8)]
    for index, fragment in enumerate(fragments, start=1):
        keys = ", ".join(sorted(fragment))
        cases.append((f"the README's example {index} ({keys}) over them", [whole, fragment], 8))
    kind = KIND_VALUES
    cases.append(("the kind test's values", [kind], 7))
    for what, layers, count in cases:
        files = [arg for layer in layers for arg in ("-f", str(values_file(layer)))]
        done = helm_template(*files, base=False)
        if done.returncode != 0:
            problems.append(f"{what} do not render: {said(done)}")
            continue
        rendered = [doc for doc in yaml.safe_load_all(done.stdout) if doc]
        if len(rendered) != count:
            problems.append(f"{what} render {sorted(kinds(rendered))}, not {count} objects")
        policies = [doc for doc in rendered if doc.get("kind") == "NetworkPolicy"]
        if sorted(dig(policies, 0, "spec", "policyTypes") or []) != ["Egress", "Ingress"]:
            problems.append(f"{what}: no NetworkPolicy that covers both directions")
        problems += validated(what, done.stdout)
    quiet = render_with(kind, base=False)
    if "ipBlock" in str(policy_spec(quiet)) or len(policy_spec(quiet).get("egress") or []) != 2:
        problems.append("the kind test's values: a leader without sign-in may reach the internet")
    same(
        problems,
        "the kind test's values: ConfigMap",
        dig(one(quiet, "ConfigMap"), "data"),
        {
            "SWARMSCRIBE_PUBLIC_URL": "http://leader-swarmscribe-leader",
            **{f"SWARMSCRIBE_{k}": str(v) for k, v in (kind.get("settings") or {}).items()},
        },
    )
    return problems


def label_values(node: object) -> Iterator[str]:
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


def core_names(_docs: list[dict]) -> list[str]:
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
            problems.append(f"release {release!r} {extra}: does not render: {said(done)}")
            continue
        for doc in (d for d in yaml.safe_load_all(done.stdout) if d):
            name = str(dig(doc, "metadata", "name"))
            where = f"release {release[:12]}... {doc.get('kind')} {name[:20]}..."
            if len(name) > 63 or not NAME_PATTERN.fullmatch(name):
                problems.append(f"{where}: not a valid name of at most 63 characters")
            for value in label_values(doc):
                if len(value) > 63:
                    problems.append(f"{where}: a label value longer than 63 characters")
    # Another release in the same namespace shares no selector with this one.
    other = one(render(release="other"), "Service", "other-swarmscribe-leader")
    if dig(other, "spec", "selector", "app.kubernetes.io/instance") != "other":
        problems.append("Service: a second release would select the first one's pods")
    return problems


def core_quoting(_docs: list[dict]) -> list[str]:
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
    data = dig(one(odd, "ConfigMap"), "data") or {}
    if data.get("SWARMSCRIBE_LEASE_SECONDS") != "120":
        problems.append("ConfigMap: a number under settings is not a string")
    if data.get("SWARMSCRIBE_CLAIM_RETRY_AFTER") != "010":
        problems.append("ConfigMap: a string with a leading zero lost it")
    env = {e.get("name"): e for e in leader_container(odd).get("env") or []}
    ref = dig(env, "SWARMSCRIBE_LINK_KEY", "valueFrom", "secretKeyRef")
    if ref != {"name": "123", "key": "yes"}:
        problems.append(f"Deployment: the Secret's name and key are not strings ({ref})")
    volume = dig(pod_spec(odd), "volumes", 0)
    if volume != {"name": "storage-123", "persistentVolumeClaim": {"claimName": "123"}}:
        problems.append(f"Deployment: a numeric volume or claim name is not a string ({volume})")
    # The chart's own labels and its checksum are not the operator's to replace.
    for label in sorted(LEADER_LABELS):
        refused_file(
            problems,
            f"podLabels that replace {label}",
            {"podLabels": {label: "x"}},
            says="the chart writes that label itself",
        )
    refused_file(
        problems,
        "podAnnotations that replace the settings' checksum",
        {"podAnnotations": {"checksum/settings": "x"}},
        says="podAnnotations must not set",
    )
    refused_file(problems, "a label that is not a string", {"podLabels": {"example.org/a": 1}})
    return problems


# --- storage ------------------------------------------------------------------------------


def volumes(*entries: dict) -> dict:
    return {"storage": {"volumes": list(entries)}}


CLAIM = {"name": "a", "mountPath": "/data/a", "existingClaim": "a"}


def storage_mounts(_docs: list[dict]) -> list[str]:
    """(The volumes of test-values.yaml are in the whole Deployment, checked by `core`.)"""
    problems: list[str] = []
    read_only = render_with(volumes({**CLAIM, "readOnly": True}))
    claim = {"claimName": "a", "readOnly": True}
    same(
        problems,
        "Deployment (a read-only claim)",
        pod_spec(read_only),
        expected_leader_pod(
            volumes=[{"name": "storage-a", "persistentVolumeClaim": claim}],
            mounts=[{"name": "storage-a", "mountPath": "/data/a", "readOnly": True}],
        ),
    )
    # The three kinds of volume source that can hold files every replica sees.
    sources: tuple[dict, ...] = (
        {"nfs": {"server": "nas.internal", "path": "/exports/a", "readOnly": True}},
        {"persistentVolumeClaim": {"claimName": "shared"}},
        {
            "csi": {
                "driver": "file.csi.example.org",
                "volumeAttributes": {"share": "recordings"},
                "nodePublishSecretRef": {"name": "share-credentials"},
            }
        },
    )
    for source in sources:
        (kind,) = source
        rendered = render_with(volume_of(source))
        same(
            problems,
            f"Deployment (a {kind} volume)",
            pod_spec(rendered),
            expected_leader_pod(
                volumes=[{"name": "storage-a", **source}],
                mounts=[{"name": "storage-a", "mountPath": "/data/a"}],
            ),
        )
    # m2: fsGroup can be turned off (a large volume another system also writes to), and the
    # supplemental groups can be none. The Job has no volume, so never an fsGroup.
    for what, fs_group in (("null", None), ("2000", 2000)):
        rendered = render_with({"storage": {"fsGroup": fs_group, "supplementalGroups": []}})
        same(
            problems,
            f"Deployment (storage.fsGroup: {what})",
            pod_spec(rendered),
            expected_leader_pod(fs_group=fs_group, groups=()),
        )
        same(problems, f"Job (storage.fsGroup: {what})", job_pod(rendered), expected_job_pod())
    return problems


def storage_refusals(_docs: list[dict]) -> list[str]:
    problems: list[str] = []
    refused(problems, "no storage volume", "storage.volumes=null", says="missing property")
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
        volumes({**CLAIM, "volume": {"nfs": {"server": "s", "path": "/p"}}}),
        says="exactly one of",
    )
    # I4: only a kind of volume that holds shared files. Each other kind is refused by the
    # schema and, with the schema off, by the templates, which say what is wrong with it.
    token = {"sources": [{"serviceAccountToken": {"path": "token"}}]}
    for kind, source, why in (
        ("emptyDir", {}, "is an emptyDir"),
        ("emptyDir", {"medium": "Memory"}, "is an emptyDir"),
        ("ephemeral", {"volumeClaimTemplate": {"spec": {}}}, "is an ephemeral volume"),
        ("hostPath", {"path": "/"}, "is a hostPath"),
        ("hostPath", {"path": "/mnt/recordings", "type": "Directory"}, "is a hostPath"),
        ("projected", token, "cannot hold the recordings"),
        ("secret", {"secretName": "swarmscribe-leader"}, "cannot hold the recordings"),
        ("configMap", {"name": "x"}, "cannot hold the recordings"),
        ("downwardAPI", {"items": []}, "cannot hold the recordings"),
        ("cephfs", {"monitors": ["a"]}, "cannot hold the recordings"),
        ("azureFile", {"secretName": "a", "shareName": "b"}, "cannot hold the recordings"),
        ("image", {"reference": "x"}, "cannot hold the recordings"),
    ):
        refused_twice(
            problems,
            f"a volume of kind {kind}",
            volume_of({kind: source}),
            schema=f"additional properties '{kind}' not allowed",
            template=why,
        )
    two = {"nfs": {"server": "s", "path": "/p"}, "csi": {"driver": "d"}}
    refused_file(problems, "a volume source with two kinds", volume_of(two), says="/volume")
    for what, source in (
        ("an nfs volume without a server", {"nfs": {"path": "/p"}}),
        ("an nfs volume with a relative path", {"nfs": {"server": "s", "path": "p"}}),
        ("a csi volume without a driver", {"csi": {"fsType": "ext4"}}),
        ("a claim volume without a claim", {"persistentVolumeClaim": {}}),
    ):
        refused_file(problems, what, volume_of(source), says="/volume")
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
        # m10: the image's own folders. /tmp would work, and break "no /tmp".
        ("a mountPath of /tmp", "/tmp"),
        ("a mountPath under /tmp", "/tmp/recordings"),
        ("a mountPath of /etc", "/etc"),
        ("a mountPath of /usr", "/usr"),
        ("a mountPath under /usr", "/usr/bin"),
        ("a mountPath of /proc", "/proc"),
        ("a mountPath of /dev", "/dev"),
        ("a mountPath of /sys", "/sys"),
        ("a mountPath of /run", "/run"),
        ("a mountPath under /lib", "/lib/x"),
        # M8: nothing of the leader's is there, but the image's own files are.
        ("a mountPath of /root", "/root"),
        ("a mountPath under /var/run", "/var/run/recordings"),
        ("a mountPath of /var", "/var"),
        ("a mountPath of /home", "/home"),
        ("a mountPath of /opt", "/opt"),
    ):
        refused_file(problems, what, volumes({**CLAIM, "mountPath": path}), says="mountPath")
    for path in (
        "/data",
        "/apps",
        "/tmpfiles",
        "/usrdata/a",
        "/var/lib/recordings",
        "/opt/recordings",
        "/home/recordings",
        "/mnt/a",
        "/srv",
    ):
        render_with(volumes({**CLAIM, "mountPath": path}))
    refused_file(
        problems, "a volume name that is not a DNS label", volumes({**CLAIM, "name": "A_b"})
    )
    refused_file(
        problems, "a volume without a name", volumes({"mountPath": "/d", "existingClaim": "a"})
    )
    refused_file(
        problems,
        "two volumes with one name",
        volumes(CLAIM, {**CLAIM, "mountPath": "/data/b"}),
        says="used twice",
    )
    refused_file(
        problems,
        "two volumes on one mountPath",
        volumes(CLAIM, {**CLAIM, "name": "b"}),
        says="used twice",
    )
    refused(problems, "an fsGroup of 0 (root's group)", "storage.fsGroup=0")
    refused_file(problems, "a text fsGroup", {"storage": {"fsGroup": "none"}})
    return problems


# --- migrate ------------------------------------------------------------------------------


def migrate_job(docs: list[dict]) -> list[str]:
    problems = compare_objects(docs, {"Job"})
    job = one(docs, "Job", f"{NAME}-migrate")
    policy = str(dig(job, "metadata", "annotations", "helm.sh/hook-delete-policy"))
    if "hook-failed" in policy:
        problems.append("Job: a failed Job is removed, and its log with it")
    hooked = [
        f"{doc.get('kind')} {dig(doc, 'metadata', 'name')}"
        for doc in docs
        if "helm.sh/hook" in (dig(doc, "metadata", "annotations") or {})
    ]
    if hooked != [f"Job {NAME}-migrate"]:
        problems.append(f"the chart's hooks are {hooked}, not the migration Job alone")
    # What the whole comparison above means, said plainly for what matters most.
    spec = job_pod(docs)
    migrate = dig(spec, "containers", 0) or {}
    if "envFrom" in migrate:
        problems.append("Job: it reads the ConfigMap, which does not exist on a first install")
    if spec.get("serviceAccountName") not in (None, "default"):
        problems.append("Job: it uses the chart's ServiceAccount, which does not exist yet")
    if spec.get("volumes") or migrate.get("volumeMounts"):
        problems.append("Job: it mounts a volume (a migration touches the database only)")
    if migrate.get("image") != leader_container(docs).get("image"):
        problems.append("Job: it does not run the leader's own image")
    inline = {e.get("name"): e.get("value") for e in migrate.get("env") or [] if "value" in e}
    if inline != dig(one(docs, "ConfigMap"), "data"):
        problems.append("Job: its inline settings differ from the ConfigMap's")
    labels = dig(job, "spec", "template", "metadata", "labels") or {}
    service = dig(one(docs, "Service"), "spec", "selector") or {}
    if all(labels.get(key) == value for key, value in service.items()):
        problems.append("Job: the Service would send requests to the migration pod")
    if "Job" in kinds(render("--set", "migrate.enabled=false")):
        problems.append("a disabled migration is still rendered")
    return problems


# --- ingress ------------------------------------------------------------------------------


def routes(entry: dict, request_path: str) -> bool:
    """Whether an Ingress path entry matches a request path (Exact, or Prefix by element)."""
    path = str(entry.get("path"))
    if entry.get("pathType") == "Exact":
        return request_path == path
    if path == "/":
        return True
    prefix = path.rstrip("/")
    return request_path == prefix or request_path.startswith(prefix + "/")


def ingress_object(docs: list[dict]) -> list[str]:
    problems = compare_objects(docs, {"Ingress"})
    spec = one(docs, "Ingress").get("spec") or {}
    if "defaultBackend" in spec:
        problems.append("Ingress: a default backend publishes every path, the probes included")
    entries = [
        entry
        for rule in spec.get("rules") or []
        for entry in dig(rule, "http", "paths") or []
        if isinstance(entry, dict)
    ]
    probes = probe_routes()
    for probe in (*probes.values(), *(f"{path}/" for path in probes.values()), "/"):
        if any(routes(entry, probe) for entry in entries):
            problems.append(f"Ingress: {probe} is published")
    # Every router the leader has must be reachable: a route outside /v1 would be cut off.
    prefixes = served_prefixes()
    if not prefixes:
        problems.append("no router prefix found in the leader's api package (did it move?)")
    for prefix in sorted(prefixes):
        if not any(routes(entry, prefix + "/x") for entry in entries):
            problems.append(f"Ingress: the leader serves {prefix}, which no path reaches")
    for path in probes.values():
        if path == "/v1" or path.startswith("/v1/"):
            problems.append(f"the leader's probe {path} is under /v1, which the Ingress publishes")
    return problems


def ingress_variants(_docs: list[dict]) -> list[str]:
    problems: list[str] = []
    backend = {"service": {"name": NAME, "port": {"name": "http"}}}
    paths = [
        {"path": "/v1/followers", "pathType": "Prefix"},
        {"path": "/v1/jobs", "pathType": "Prefix"},
        {"path": "/v1/files", "pathType": "Exact"},
    ]
    split = render_with({"ingress": {"paths": paths, "annotations": {"example.org/limit": "10"}}})
    wanted = expected_ingress()
    wanted["metadata"] = {**wanted["metadata"], "annotations": {"example.org/limit": "10"}}
    wanted["spec"]["rules"][0]["http"]["paths"] = [{**path, "backend": backend} for path in paths]
    same(problems, "Ingress (three paths, an annotation)", one(split, "Ingress"), wanted)
    odd = render_with({"ingress": {"className": "true", "tls": {"secretName": "12345"}}})
    quoted = one(odd, "Ingress").get("spec") or {}
    if quoted.get("ingressClassName") != "true" or dig(quoted, "tls", 0, "secretName") != "12345":
        problems.append("Ingress: className or the TLS secret name is not a string")
    unnamed = render_with({"ingress": {"className": ""}})
    if "ingressClassName" in (one(unnamed, "Ingress").get("spec") or {}):
        problems.append("Ingress: an empty className is still rendered")
    if "Ingress" in kinds(render("--set", "ingress.enabled=false")):
        problems.append("a disabled Ingress is still rendered")
    return problems


def ingress_refusals(_docs: list[dict]) -> list[str]:
    """I5: every path is /v1 or under it, as Exact or Prefix; nothing can publish a probe."""
    problems: list[str] = []
    probes = probe_routes()
    for what, path, kind in (
        ("a Prefix / path", "/", "Prefix"),
        ("an Exact / path", "/", "Exact"),
        ("an ImplementationSpecific / path", "/", "ImplementationSpecific"),
        ("the readiness probe as a path", probes["ready"], "Exact"),
        ("the liveness probe as a path", probes["live"], "Prefix"),
        ("a path without a leading slash", "v1", "Prefix"),
        # Patterns: everything, on some controllers.
        ("/* as a pattern", "/*", "ImplementationSpecific"),
        ("/.* as a pattern", "/.*", "ImplementationSpecific"),
        ("/(.*) as a pattern", "/(.*)", "ImplementationSpecific"),
        ("a pattern under /v1", "/v1/.*", "ImplementationSpecific"),
        ("/v1 as ImplementationSpecific", "/v1", "ImplementationSpecific"),
        ("//", "//", "Prefix"),
        ("the readiness probe in another letter case", "/Readyz", "Prefix"),
        ("a path that climbs out of /v1", "/v1/../readyz", "Prefix"),
        ("a path with a doubled slash", "/v1//files", "Prefix"),
        ("a path that only begins like /v1", "/v10", "Prefix"),
        ("a path beside /v1", "/v1x/files", "Prefix"),
        ("another prefix", "/metrics", "Prefix"),
    ):
        refused_twice(
            problems,
            what,
            {"ingress": {"paths": [{"path": path, "pathType": kind}]}},
            schema="/ingress/paths/0/",
            template="ingress.paths must not hold",
        )
    for what, entry in (
        ("an unknown pathType", {"path": "/v1", "pathType": "Loose"}),
        ("a pattern as a Prefix", {"path": "/v1/(.*)", "pathType": "Prefix"}),
        ("a path with a trailing slash", {"path": "/v1/", "pathType": "Prefix"}),
        ("a path without a pathType", {"path": "/v1"}),
    ):
        refused_file(problems, what, {"ingress": {"paths": [entry]}}, says="/ingress/paths/0")
    refused_file(problems, "no ingress path at all", {"ingress": {"paths": []}}, says="minItems")
    refused(problems, "an Ingress without TLS", "ingress.tls.secretName=", says="TLS only")
    refused_file(
        problems, "an annotation that is not a string", {"ingress": {"annotations": {"a": 1}}}
    )
    return problems


# --- network ------------------------------------------------------------------------------


def cut_out(address: str, rule: dict) -> bool:
    """Whether `address` is unreachable through an egress rule's ipBlock peers."""
    target = ipaddress.ip_address(address)
    for peer in rule.get("to") or []:
        block = peer.get("ipBlock") or {}
        if "cidr" not in block:
            return False
        network = ipaddress.ip_network(block["cidr"])
        if target.version == network.version and target in network:
            excepted = (ipaddress.ip_network(cut) for cut in block.get("except", []))
            if not any(target in cut for cut in excepted):
                return False
    return True


def https_rules(policy: dict) -> list[dict]:
    return [
        rule
        for rule in policy.get("egress") or []
        if any("ipBlock" in peer for peer in rule.get("to") or [])
        and {"protocol": "TCP", "port": 443} in (rule.get("ports") or [])
    ]


def network_objects(docs: list[dict]) -> list[str]:
    problems = compare_objects(docs, {"PodDisruptionBudget", "NetworkPolicy"})
    # One selector for the leader pods wherever one is written: the Service's.
    service = dig(one(docs, "Service"), "spec", "selector")
    same(problems, "Service: its selector", service, LEADER_SELECTOR)
    budget = dig(one(docs, "PodDisruptionBudget"), "spec", "selector", "matchLabels")
    same(problems, "PodDisruptionBudget: its selector, against the Service's", budget, service)
    deployment = dig(one(docs, "Deployment"), "spec", "selector", "matchLabels")
    same(problems, "Deployment: its selector, against the Service's", deployment, service)
    spread = dig(pod_spec(docs), "topologySpreadConstraints", 0, "labelSelector", "matchLabels")
    same(problems, "Deployment: the spread's selector, against the Service's", spread, service)
    if "topologySpreadConstraints" in pod_spec(render("--set", "replicaCount=1")):
        problems.append("Deployment: a spread constraint for one replica")
    if "topologySpreadConstraints" in pod_spec(render("--set", "spreadAcrossNodes=false")):
        problems.append("Deployment: spreadAcrossNodes=false still spreads")
    if "PodDisruptionBudget" in kinds(render("--set", "replicaCount=1")):
        problems.append("a PodDisruptionBudget is rendered for one replica (it blocks drains)")
    if "PodDisruptionBudget" in kinds(render("--set", "podDisruptionBudget.enabled=false")):
        problems.append("a disabled PodDisruptionBudget is still rendered")

    # The policy selects this release's pods, the migration's too, and no other's.
    policy = policy_spec(docs)
    selector = dig(policy, "podSelector", "matchLabels") or {}
    job = one(docs, "Job", f"{NAME}-migrate")
    for kind, labels in (
        ("leader", dig(one(docs, "Deployment"), "spec", "template", "metadata", "labels")),
        ("migration", dig(job, "spec", "template", "metadata", "labels")),
    ):
        if not selector or any((labels or {}).get(k) != v for k, v in selector.items()):
            problems.append(f"NetworkPolicy: does not select the {kind} pod")
    if "app.kubernetes.io/instance" not in selector:
        problems.append(f"NetworkPolicy: selects {selector}, not this release's pods only")
    # No rule anywhere allows every address or every port.
    for index, rule in enumerate(policy.get("egress") or []):
        if not rule.get("to") or not rule.get("ports"):
            problems.append(f"NetworkPolicy: egress rule {index} has no peers or no ports")

    # What the whole comparison means for the identity providers' rule, address by address.
    found = https_rules(policy)
    if len(found) != 1:
        problems.append(f"NetworkPolicy: expected one https egress rule, found {len(found)}")
        return problems
    rule = found[0]
    for address in MUST_BE_BLOCKED:
        if not cut_out(address, rule):
            problems.append(f"NetworkPolicy: {address} is reachable")
    for address in MUST_BE_ALLOWED:
        if cut_out(address, rule):
            problems.append(f"NetworkPolicy: {address} is cut out")
    if not cut_out("10.96.0.1", rule) or not cut_out("fd00:10:96::1", rule):
        problems.append("NetworkPolicy: extraExcept is not applied")
    for peer in rule.get("to") or []:
        network = ipaddress.ip_network(str(dig(peer, "ipBlock", "cidr")))
        for cut in dig(peer, "ipBlock", "except") or []:
            inner = ipaddress.ip_network(cut)
            if inner.version != network.version or not inner.subnet_of(network):
                problems.append(f"NetworkPolicy: except {cut} is outside {network}")
    return problems


def network_variants(_docs: list[dict]) -> list[str]:
    problems: list[str] = []
    anyone = render(
        "--set", "networkPolicy.ingress.from=null", "--set", "networkPolicy.ingress.anySource=true"
    )
    same(
        problems,
        "NetworkPolicy (anySource: any source, on the leader's port only)",
        policy_spec(anyone),
        expected_policy(sources=None),
    )
    # A leader without sign-in calls nobody but DNS and Postgres.
    quiet = render_with(NO_SIGN_IN)
    same(
        problems,
        "NetworkPolicy (no sign-in: no HTTPS egress at all)",
        policy_spec(quiet),
        expected_policy(sign_in=False),
    )
    # The API server refuses an `except` outside its `cidr`: a narrowed range gets none.
    narrowed = policy_spec(render("--set", "networkPolicy.egress.https.cidrs={10.0.0.0/8}"))
    same(
        problems,
        "NetworkPolicy (https narrowed to one range): the https rule",
        dig(narrowed, "egress", 2),
        {"to": [{"ipBlock": {"cidr": "10.0.0.0/8"}}], "ports": [{"protocol": "TCP", "port": 443}]},
    )
    proxy = {
        "to": [{"ipBlock": {"cidr": "10.9.9.9/32"}}],
        "ports": [{"protocol": "TCP", "port": 3128}],
    }
    extra = render_with({"networkPolicy": {"egress": {"extra": [proxy]}}})
    wanted = expected_policy(extra=(proxy,))
    same(problems, "NetworkPolicy (egress.extra)", policy_spec(extra), wanted)
    plain = render_with({"networkPolicy": {"egress": {"https": {"extraExcept": []}}}})
    same(
        problems,
        "NetworkPolicy (no extraExcept)",
        policy_spec(plain),
        expected_policy(more_refused=None),
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


def network_refusals(_docs: list[dict]) -> list[str]:
    """I3: an empty list never opens the policy. In a NetworkPolicy rule, no peers means
    every address and no ports means every port."""
    problems: list[str] = []
    refused(
        problems,
        "a NetworkPolicy that lets nobody in",
        "networkPolicy.ingress.from=null",
        says="networkPolicy.ingress.from is required",
    )
    refused_file(
        problems,
        "an empty list of ingress peers",
        {"networkPolicy": {"ingress": {"from": []}}},
        says="networkPolicy.ingress.from is required",
    )
    refused(
        problems,
        "ingress.from together with anySource",
        "networkPolicy.ingress.anySource=true",
        says="choose one",
    )
    refused(
        problems,
        "a NetworkPolicy without Postgres",
        "networkPolicy.egress.postgres.peers=null",
        says="postgres.peers is required",
    )
    refused_file(
        problems,
        "an empty list of Postgres peers",
        {"networkPolicy": {"egress": {"postgres": {"peers": []}}}},
        says="postgres.peers is required",
    )
    to = [{"ipBlock": {"cidr": "10.9.9.9/32"}}]
    ports = [{"protocol": "TCP", "port": 3128}]
    both = "needs both `to` and `ports`"
    for what, egress, level, why in (
        (
            "no https cidrs (443 to every address)",
            {"https": {"cidrs": []}},
            "/networkPolicy/egress/https/cidrs",
            "https.cidrs is empty",
        ),
        (
            "no https ports (every port)",
            {"https": {"ports": []}},
            "/networkPolicy/egress/https/ports",
            "https.ports is empty",
        ),
        (
            "no DNS peers (53 to every address)",
            {"dns": {"peers": []}},
            "/networkPolicy/egress/dns/peers",
            "dns.peers is empty",
        ),
        ("an extra rule with no peers", {"extra": [{"to": [], "ports": ports}]}, "/extra/0", both),
        ("an extra rule with no `to`", {"extra": [{"ports": ports}]}, "/extra/0", both),
        ("an extra rule with no ports", {"extra": [{"to": to, "ports": []}]}, "/extra/0", both),
        ("an extra rule with no `ports`", {"extra": [{"to": to}]}, "/extra/0", both),
        ("an extra rule that allows everything", {"extra": [{}]}, "/extra/0", both),
    ):
        refused_twice(problems, what, {"networkPolicy": {"egress": egress}}, level, why)
    # The same three with sign-in off, when the https rule is not rendered at all.
    for egress in ({"https": {"cidrs": []}}, {"https": {"ports": []}}, {"dns": {"peers": []}}):
        refused_file(
            problems,
            f"{egress} without sign-in",
            {**NO_SIGN_IN, "networkPolicy": {"egress": egress}},
            says="is empty",
            unchecked=True,
        )
    for what, values in (
        ("a peer that is empty", {"ingress": {"from": [{}]}}),
        ("a DNS peer that is empty", {"egress": {"dns": {"peers": [{}]}}}),
        ("a Postgres peer that is empty", {"egress": {"postgres": {"peers": [{}]}}}),
        ("a peer that is a string", {"ingress": {"from": ["10.0.0.0/8"]}}),
        ("an https range that is not a CIDR", {"egress": {"https": {"cidrs": ["anywhere"]}}}),
        ("an https port of 0", {"egress": {"https": {"ports": [0]}}}),
        ("an https port that is a name", {"egress": {"https": {"ports": ["https"]}}}),
        ("an extraExcept that is not a CIDR", {"egress": {"https": {"extraExcept": ["x"]}}}),
    ):
        refused_file(problems, what, {"networkPolicy": values}, says="/networkPolicy/")
    return problems


# --- values -------------------------------------------------------------------------------


def values_passed_on(_docs: list[dict]) -> list[str]:
    """Every value that is a plain pass-through arrives: none is hard-coded at its default."""
    problems: list[str] = []
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
    # The leader's pod, whole, with exactly what those values change.
    pod = expected_leader_pod(fs_group=2000, pull="Always", spread=False)
    pod |= {
        "serviceAccountName": "named",
        "terminationGracePeriodSeconds": 90,
        "imagePullSecrets": [{"name": "registry"}],
        "nodeSelector": {"disk": "fast"},
        "tolerations": [toleration],
        "affinity": affinity,
        "topologySpreadConstraints": [spread],
    }
    pod["containers"][0]["lifecycle"] = {"preStop": {"exec": {"command": ["sleep", "10"]}}}
    pod["containers"][0]["resources"] = {
        "requests": {"cpu": "1", "memory": "256Mi"},
        "limits": {"memory": "512Mi"},
    }
    same(problems, "Deployment (every pass-through value moved)", pod_spec(moved), pod)
    hook_pod = expected_job_pod()
    hook_pod |= {
        "imagePullSecrets": [{"name": "registry"}],
        "nodeSelector": {"disk": "fast"},
        "tolerations": [toleration],
    }
    hook_pod["containers"][0]["imagePullPolicy"] = "Always"
    hook_pod["containers"][0]["resources"] = {
        "requests": {"cpu": "2", "memory": "128Mi"},
        "limits": {"memory": "256Mi"},
    }
    hook = one(moved, "Job", f"{NAME}-migrate").get("spec") or {}
    same(problems, "Job (every pass-through value moved)", job_pod(moved), hook_pod)
    same(
        problems,
        "NetworkPolicy (every pass-through value moved)",
        policy_spec(moved),
        expected_policy(dns=dns, postgres_port=6432, https_ports=(443, 8443)),
    )
    # And everything that render holds, whole: nothing else moved with those values.
    def everything(objects: dict[str, dict]) -> None:
        objects["Deployment"]["spec"]["replicas"] = 3
        objects["Deployment"]["spec"]["template"] = {
            "metadata": {
                "labels": {**LEADER_LABELS, "example.org/team": "b"},
                "annotations": {"example.org/note": "a"},
            },
            "spec": pod,
        }
        objects["Job"]["spec"] = {
            "backoffLimit": 1,
            "activeDeadlineSeconds": 600,
            "template": {"metadata": {"labels": MIGRATE_LABELS}, "spec": hook_pod},
        }
        objects["Service"]["spec"]["type"] = "NodePort"
        objects["Service"]["spec"]["ports"][0]["port"] = 8443
        objects["ServiceAccount"]["metadata"]["name"] = "named"
        objects["PodDisruptionBudget"]["spec"]["maxUnavailable"] = 2
        objects["NetworkPolicy"]["spec"] = expected_policy(
            dns=dns, postgres_port=6432, https_ports=(443, 8443)
        )

    variant(problems, "every pass-through value moved", moved, everything)
    deployment = one(moved, "Deployment")
    template = dig(deployment, "spec", "template", "metadata") or {}
    account = dig(one(moved, "ServiceAccount", "named"), "metadata", "name")
    for what, got, wanted in (
        ("replicaCount", dig(deployment, "spec", "replicas"), 3),
        ("service.type", dig(one(moved, "Service"), "spec", "type"), "NodePort"),
        ("service.port", dig(one(moved, "Service"), "spec", "ports", 0, "port"), 8443),
        ("migrate.backoffLimit", hook.get("backoffLimit"), 1),
        ("migrate.activeDeadlineSeconds", hook.get("activeDeadlineSeconds"), 600),
        (
            "podDisruptionBudget.maxUnavailable",
            dig(one(moved, "PodDisruptionBudget"), "spec", "maxUnavailable"),
            2,
        ),
        ("podAnnotations", dig(template, "annotations", "example.org/note"), "a"),
        ("podLabels", dig(template, "labels"), {**LEADER_LABELS, "example.org/team": "b"}),
        (
            "podLabels (not on the migration's pod)",
            dig(hook, "template", "metadata", "labels"),
            MIGRATE_LABELS,
        ),
        ("serviceAccount.name", account, "named"),
    ):
        if got != wanted:
            problems.append(f"{what} is not passed on: {got!r}, not {wanted!r}")
    own = render("--set", "serviceAccount.create=false")
    if "ServiceAccount" in kinds(own) or pod_spec(own).get("serviceAccountName") != "default":
        problems.append("serviceAccount.create=false does not fall back to `default`")
    return problems


SECTIONS: dict[str, list[Callable[[list[dict]], list[str]]]] = {
    "core": [
        core_objects,
        core_whole,
        core_defaults,
        core_notes,
        core_no_secret,
        core_settings,
        core_roles,
        core_variants,
        core_sign_in,
        core_public_url,
        core_numbers,
        core_schema,
        core_package,
        core_must_render,
        core_names,
        core_quoting,
    ],
    "storage": [storage_mounts, storage_refusals],
    "migrate": [migrate_job],
    "ingress": [ingress_object, ingress_variants, ingress_refusals],
    "network": [network_objects, network_variants, network_refusals],
    "values": [values_passed_on],
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", default=",".join(SECTIONS), help="sections, comma-separated")
    parser.add_argument(
        "--stop-at-first",
        action="store_true",
        help="stop after the first part that finds a problem (quicker when one is expected)",
    )
    arguments = parser.parse_args()
    chosen = arguments.only.split(",")
    global HELM, SCRATCH
    HELM = helm_tool.find_helm()
    SCRATCH = helm_tool.scratch_folder("leader")
    tool = os.environ.get("KUBECONFORM") or "not named in $KUBECONFORM: examples not validated"
    print(f"kubeconform: {tool}")
    problems: list[str] = []
    base = helm_template()
    if base.returncode != 0:
        problems.append(f"ci/test-values.yaml does not render: {said(base)}")
    docs = [doc for doc in yaml.safe_load_all(base.stdout) if doc] if base.returncode == 0 else []
    for part in (part for section in chosen for part in SECTIONS[section]):
        if problems and arguments.stop_at_first:
            break
        # One part that cannot go on is one FAILED line: the parts after it still run.
        try:
            problems += part(docs)
        except CheckError as stopped:
            problems.append(f"{part.__name__}: {stopped}")
        except Exception as crashed:
            problems.append(
                f"{part.__name__} crashed ({type(crashed).__name__}: {crashed}): "
                "the rest of that part did not run"
            )
    for problem in problems:
        print(f"FAILED: {problem}", file=sys.stderr)
    if problems:
        # A deliberate change shows as one line per render that takes it: say where it goes.
        print(
            "If a change to the chart is deliberate, change what this check expects in the "
            f"same commit: the constants at the top of {Path(__file__).name} feed every "
            "render (DEFAULTS for values.yaml, NOTES for the notes, expected_leader_pod, "
            "expected_job_pod and expected_policy for the objects), and the README's "
            '"Deploy the leader" repeats the defaults in its tables and prose.',
            file=sys.stderr,
        )
    print(f"{sum(_runs.values())} Helm runs: {_runs['rendered']} rendered, {_runs['refused']} not")
    if not problems:
        print(f"the rendered chart is exactly what it must be ({', '.join(chosen)})")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
