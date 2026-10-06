# Leader Chart L3 — The Chart on `kind` with the Follower Chart, an Upgrade Under Load, and the Guide Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove the leader chart on a `kind` cluster together with the follower chart: it installs with a real Postgres and a real volume claim, followers from the follower chart register and transcribe recordings end to end, the NetworkPolicy is seen to block, and an upgrade that runs a migration replaces the pods with requests flowing and not one failing. Record the result, make it a CI job, and finish the README's guide.

**Architecture:** `e2e/leader-kind/run_e2e.py` drives `kind`, `kubectl` and `helm` (standard library only), modelled on `e2e/follower-kind/run_e2e.py`. The test brings what the chart never does (a Postgres, a PersistentVolumeClaim, the Secret) in one manifest, installs the two charts with test values, and administers the leader from inside its pod, because the cluster has no identity provider. A second image, built from the image under test with one more migration, stands for "the next version". The upgrade is made observable by pausing the rollout: the hook migrates, and the old pods are watched on the new schema for longer than failed probes would take to drop them.

**Tech Stack:** kind 0.30.0 (Kubernetes 1.34.0), kubectl, Helm 4.3.0, Docker, Python 3.12 (standard library in the driver), pytest with PyYAML for the driver's own parts, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-05-leader-chart-design.md` — sections 10 (the three charts on one cluster), 11 (verification), 13 (what has and has not been run), 14 (risks K1, K5).

**This plan is the third of three.** It needs L1 (the image, `/readyz`) and L2 (the chart) on the branch.

## Global Constraints

- **Work only in the worktree `C:\Users\walla\SwarmScribe-leader-chart`, on the branch `leader-chart`.** Other agents work in `C:\Users\walla\SwarmScribe`, `SwarmScribe-ui` and `SwarmScribe-f4`.
- **The free-space floor.** A full disk corrupted Docker's data on this machine. Before **every** step that builds an image, loads one into a cluster, creates a cluster or starts a container: `bash docker/check-free-space.sh`, and stop if it does not print `ok:` (20 GB free on C:). The driver checks again itself before it creates a cluster and before each image it loads. If it says `STOP:`, stop the task and report; never free space with a `docker ... prune`.
- **Another agent uses Docker on this machine.** Never stop, remove or retag a container, an image or a network you did not create; never run any `docker ... prune`; never restart Docker; **never kill a process by name**. Never touch a `kind` cluster that is not this plan's: this plan's is `swarmscribe-leader-e2e`, and `swarmscribe-follower-e2e` is somebody else's. Nothing here publishes a port.
- **This test's image tags are its own**: `swarmscribe-leader:kind`, `swarmscribe-leader:kind-next`, `swarmscribe-follower:leader-kind`. Never build to `swarmscribe-leader:e2e` or `swarmscribe-follower:e2e`: those are the Compose tests' and the follower `kind` test's images, which another agent may be using, and `swarmscribe-leader:e2e` is a *different* image (the test image with the source tree in it).
- **The cluster has a kubeconfig of its own** (`e2e/leader-kind/work/kubeconfig`, git-ignored). `~/.kube/config` is never touched.
- **The development machine** is Windows 11 with Git Bash. `uv` is run as `python -m uv` (written `uv run ...` below). `kind`, `kubectl`, `helm` and `kubeconform` are in `$TEMP/chart-tools`; run this in every shell that uses them:

  ```bash
  export PATH="$TEMP/chart-tools/windows-amd64:$TEMP/chart-tools:$PATH"
  kind version && helm version --short && kubectl version --client
  ```

  Expected: `kind v0.30.0 ...`, `v4.3.0+gbec5b06`, a client version. Fetch a missing one as `plans/2026-10-05-follower-f3b-kind-install-and-guide.md`, "The tools on the Windows machine", shows; never install one system-wide.
- "Images are built and tested in CI but not published" (owner). "Consent gating is never weakened by a deployment option" (owner): the scenario checks that a recording the consent file does not name is never queued. "Plain, exact documentation: say what was really run and what was not" (owner): Task 2 and Task 4 are that.
- SwarmScribe is general-purpose: the recordings are named `call-1.wav` and the location `calls` only because the fixture has two speakers; nothing names a use case.
- Commit after every task, on `leader-chart`. Do not push. End every commit message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## What the planner ran, and what it did not

The planner was allowed no Docker and no `kind` (spec, section 13). On 2026-10-05, in a scratch folder:

| What | Result |
|---|---|
| `helm template` of the leader chart with `leader-values.yaml`, and of the follower chart with `follower-values.yaml`; `kubeconform -strict` on the first and on `postgres.yaml` | 11 resources, all valid |
| `packages/leader/tests/test_leader_kind_driver.py` | 12 passed, 1 skipped (it needs the follower package, which the scratch folder did not have) |
| `ruff check` on the driver, `admin.py`, `next_migration.py` and the test | passed |
| the published SHA-256 of `kind-linux-amd64` v0.30.0, and the same sum computed from the downloaded file | `517ab7fc89ddeed5fa65abf71530d90648d9638ef0c4cde22c2c11f8097b8889`, both |

**`e2e/leader-kind/run_e2e.py` has never been executed against a cluster.** Its tools, its loading of images, its `until`, its `admin` and its transcript checks are copied from `e2e/follower-kind/run_e2e.py`, which has run many times. What is new, and where Task 2 should expect to correct the driver (never the assertion's meaning):

1. **The paused rollout.** `kubectl rollout pause`, then `helm upgrade`, then `kubectl rollout resume`. The planner expects Helm to return once the hook has finished, leaving the Deployment's new template unrolled, and `spec.paused` to survive Helm's apply. If Helm 4 waits on the paused Deployment or resets `paused`, find the flag that stops it (`--wait=hookOnly` is Helm 4's name for "hooks only") and say what was needed in the outcomes.
2. **The fixture sent over `kubectl exec -i` as bytes** on Windows.
3. **The prober** (a pod whose `python -c` program is one argument of some 40 lines) and reading its last log line.
4. **`ReadyWatch`**: the label `kubernetes.io/service-name` on EndpointSlices and `conditions.ready` are standard; the sampling rate on Windows, where each `kubectl` takes a few tenths of a second, is a guess (`samples >= HOLD_SECONDS`).
5. **`CONNECT`** telling `timeout` from `refused`: `errno 111` is Linux's `ECONNREFUSED`, and the code runs in a Linux pod.
6. **Timing**: that the twelve-minute recording is still in hand when the pods are replaced (the scenario fails with "raise LONG_REPEATS" if not), and that 180 s is enough for each wait.

## Rulings

Those marked **(owner)** are the owner's to overturn.

1. **The test brings Postgres, the claim and the Secret** (`postgres.yaml`); the chart brings none, and the test must not make it look as if it did.
2. **No identity provider in the cluster.** The leader is rendered with `oidc.allowNone: true` and administered from inside its pod with its own functions (`admin.py`), as `e2e/follower-kind` does. So `swarmscribe-admin`, a sign-in, and the chart's role mapping reaching a real token are **not** tested, and the documents say so.
3. **Plain http to the Service's name.** `publicUrl: http://leader-swarmscribe-leader` with `allowHttpPublicUrl: true`, no Ingress. So an Ingress, TLS and the follower chart's `leader.ca` are **not** tested.
4. **The volume is kind's `local-path` (ReadWriteOnce) on one node.** Two leader pods share it only because they share the node. A ReadWriteMany volume and pods on two nodes are **not** tested (spec risk K1). **(owner)**: an NFS server in the cluster would test the real shape and costs a privileged pod and some minutes; recommended as a follow-up, not here (open question 5).
5. **"The next version" is the image under test with one more migration that changes nothing but the revision** (`next.Dockerfile`). It proves the hook, readiness on a newer schema, and the rollout. It does not prove that any real migration is compatible with the release before it.
6. **The upgrade is held, not raced.** With two pods that become Ready in seconds, an upgrade can finish before three failed readiness probes (15 s) would show the defect, and the test would pass with or without the fix. Pausing the rollout holds the old pods on the migrated database for `HOLD_SECONDS` (30).
7. **Ready addresses**: at least 2 while the rollout is held (deterministic); never 0 at any sampled moment of the whole upgrade. Not "at least 2 throughout": the Deployment controller and the EndpointSlice controller each learn of a Ready pod on their own, and one sample of 1 while a pod is replaced is not a failure.
8. **The prober opens a new connection for every request.** A request on a kept-alive connection to a pod that is stopping can be cut; followers retry. That is not measured.
9. **A control run, once, by hand** (Task 2): the same scenario with the leader's old `health.py`, to see it fail at the hold. Not in CI.
10. **A CI job, `leader-kind-e2e`** (spec R12). **(owner)** It replaces L1's `leader-image` job (the image is built and checked first, then used). The earlier charts' `kind` runs are by hand; this one is a job because the upgrade it proves is exactly what a later change to the leader's readiness or to the chart's rollout would break without anyone noticing. If it proves flaky on GitHub's runners, the ruling goes back to "by hand, recorded", and the job is deleted rather than retried.
11. **The existing tests are untouched**, and so is their image (`e2e/compose/Dockerfile`).

## Review Focus

1. **The test can fail.** Task 2's control run must show step 5 failing with the old readiness. If it passes with the old code, the test proves nothing about the defect: stop and report.
2. **The hook really ran before the pods, and really migrated**: no restart of a leader container on install; the revision is `9999` after the upgrade's hook and before any pod is replaced.
3. **Old pods on the new schema**: both Ready for the whole hold, both answering 200 on `/readyz` when asked directly, two ready addresses.
4. **Zero failed requests** of several hundred, each on a new connection, one of the two kinds answered by a database query.
5. **The recording in hand** is still leased when the pods are replaced and is finished in one attempt.
6. **NetworkPolicy blocks, both ways**: a timeout (dropped), not a refusal (arrived).
7. **Consent**: the unconsented recording has no job.
8. **No secret anywhere Helm or a log keeps it.**
9. **The driver leaves nothing behind**: `down` deletes the cluster and `work/`; no container, image or cluster of another agent's is touched.

## File Structure

```
e2e/leader-kind/
  run_e2e.py              up | run | down
  admin.py                piped into a leader pod: pool token, profile, location, recordings, state
  postgres.yaml           the test's own Postgres, PersistentVolumeClaim and Secret
  leader-values.yaml      the leader chart's values for the test
  follower-values.yaml    the follower chart's values for the test
  next.Dockerfile         TEST ONLY: the image under test with one more migration
  next_migration.py       run while building next.Dockerfile
  .dockerignore           keeps work/ (the kubeconfig) out of that build
  work/                   git-ignored: the cluster's kubeconfig
packages/leader/tests/test_leader_kind_driver.py              (create, Task 1)
.gitignore, .dockerignore                                     (modify, Task 1) e2e/leader-kind/work
docs/superpowers/plans/2026-10-05-leader-chart-outcomes.md    (create, Task 2)
.github/workflows/ci.yml                                      (modify, Task 3) leader-image -> leader-kind-e2e
README.md                                                     (modify, Task 4)
docs/superpowers/roadmap.md                                   (modify, Task 4)
docs/superpowers/plans/2026-10-05-leader-chart-followups.md   (create, Task 4)
docs/superpowers/plans/2026-10-04-follower-f1-followups.md    (modify, Task 4) one line
```

---

### Task 1: The `kind` test — a Postgres and a claim, the two charts' values, the next version, the driver

**Files:**
- Create: `packages/leader/tests/test_leader_kind_driver.py`
- Create: `e2e/leader-kind/postgres.yaml`
- Create: `e2e/leader-kind/leader-values.yaml`
- Create: `e2e/leader-kind/follower-values.yaml`
- Create: `e2e/leader-kind/next.Dockerfile`
- Create: `e2e/leader-kind/next_migration.py`
- Create: `e2e/leader-kind/.dockerignore`
- Create: `e2e/leader-kind/admin.py`
- Create: `e2e/leader-kind/run_e2e.py`
- Modify: `.gitignore`, `.dockerignore`

**Interfaces:**
- Consumes: L2's chart (release `leader` → Deployment and Service `leader-swarmscribe-leader`, container `leader`, labels `app.kubernetes.io/instance=leader,app.kubernetes.io/component=leader`, the hook Job, the NetworkPolicy's `ingress.from` and `egress.postgres.peers`); the follower chart (release `pool` → Deployment `pool-swarmscribe-follower`, Secret key `pool-token`); L1's image and `/readyz`; the leader's functions `create_pool_token`, `set_profile`, the models `StorageLocation`, `Job`, `JobAttempt`, `Recording`, `Follower`; the fixture `packages/engine/tests/fixtures/stereo_speech.wav` (five seconds; "weather" on the left channel, "report" on the right).
- Produces: `python e2e/leader-kind/run_e2e.py up | run | down`. Environment: `KIND_CLUSTER` (default `swarmscribe-leader-e2e`), `LEADER_IMAGE` (`swarmscribe-leader:kind`), `NEXT_IMAGE` (`swarmscribe-leader:kind-next`), `FOLLOWER_IMAGE` (`swarmscribe-follower:leader-kind`), `FREE_GB_FLOOR` (20). Namespace `swarmscribe-e2e`. `run` ends with a line that starts `passed (tiny.en on cpu):`, or `FAILED: ...` and exit 1. Task 2 runs these and records what they print; Task 3 runs them in CI.

- [ ] **Step 1: Write the test of the test's own parts (the failing test)**

Create `packages/leader/tests/test_leader_kind_driver.py`:

```python
"""The leader kind test's own parts (e2e/leader-kind), checked without a cluster: that its
values, its manifests and its driver agree with each other and with the two charts."""

import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[3]
KIND = ROOT / "e2e" / "leader-kind"
LEADER_CHART = ROOT / "deploy" / "helm" / "swarmscribe-leader"
FOLLOWER_CHART = ROOT / "deploy" / "helm" / "swarmscribe-follower"
# Measured, in MiB (README, "Sizing a pool"): tiny.en once loaded, and what a follower keeps.
TINY_LOADED, KEPT_AFTER_A_LONG_JOB = 230, 300


def read(name: str):
    return list(yaml.safe_load_all((KIND / name).read_text(encoding="utf-8")))


def mebibytes(quantity: str) -> int:
    number, unit = re.fullmatch(r"(\d+)(Mi|Gi)", quantity).groups()
    return int(number) * (1024 if unit == "Gi" else 1)


@pytest.fixture(scope="module")
def driver():
    spec = importlib.util.spec_from_file_location("leader_kind_driver", KIND / "run_e2e.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["leader_kind_driver"] = module
    spec.loader.exec_module(module)
    return module


def test_the_followers_reach_the_leader_at_the_address_it_builds_its_links_from(driver):
    (leader,) = read("leader-values.yaml")
    (follower,) = read("follower-values.yaml")
    assert leader["publicUrl"] == driver.LEADER_URL == f"http://{driver.LEADER_NAME}"
    assert follower["leader"] == {"url": leader["publicUrl"], "allowHttp": True}
    assert leader["allowHttpPublicUrl"] is True and leader["ingress"] == {"enabled": False}
    # A NetworkPolicy is matched after Service translation: the followers must be allowed
    # the port the leader's PODS listen on, which is the leader chart's default.
    defaults = yaml.safe_load((LEADER_CHART / "values.yaml").read_text(encoding="utf-8"))
    assert defaults["port"] in follower["networkPolicy"]["egress"]["https"]["ports"]
    assert follower["poolToken"]["existingSecret"] == "pool-token"  # store_pool_token's Secret


def test_the_manifest_holds_what_the_values_name(driver):
    documents = {(d["kind"], d["metadata"]["name"]): d for d in read("postgres.yaml")}
    (leader,) = read("leader-values.yaml")
    secret = documents["Secret", leader["secrets"]["existingSecret"]]
    assert set(secret["stringData"]) == {"database-url", "link-key"}  # the chart's default keys
    assert secret["stringData"]["link-key"] == driver.LINK_KEY
    assert len(driver.LINK_KEY) >= 32
    (volume,) = leader["storage"]["volumes"]
    assert ("PersistentVolumeClaim", volume["existingClaim"]) in documents
    assert volume["mountPath"] == "/data"  # admin.py: DATA
    postgres = documents["Deployment", "postgres"]
    labels = postgres["spec"]["template"]["metadata"]["labels"]
    (peer,) = leader["networkPolicy"]["egress"]["postgres"]["peers"]
    assert peer == {"podSelector": {"matchLabels": labels}}
    assert postgres["spec"]["template"]["spec"]["containers"][0]["image"] == driver.POSTGRES_IMAGE
    assert "@postgres:5432/" in secret["stringData"]["database-url"]
    assert ("Service", "postgres") in documents


def test_the_leader_lets_in_the_followers_and_the_prober_and_nobody_else(driver):
    (leader,) = read("leader-values.yaml")
    peers = leader["networkPolicy"]["ingress"]["from"]
    assert peers == [
        {"podSelector": {"matchLabels": {"app.kubernetes.io/name": "swarmscribe-follower"}}},
        {"podSelector": {"matchLabels": {"swarmscribe-e2e/role": "prober"}}},
    ]
    prober = driver.pod_manifest("prober", "prober", ["pass"])
    stranger = driver.pod_manifest("stranger", "stranger", ["pass"])
    assert prober["metadata"]["labels"] == {"swarmscribe-e2e/role": "prober"}
    assert stranger["metadata"]["labels"] == {"swarmscribe-e2e/role": "stranger"}
    assert prober["spec"]["containers"][0]["command"][:3] == ["python", "-u", "-c"]


def test_the_recording_in_hand_fits_the_followers_memory(driver):
    job_mb = pytest.importorskip("swarmscribe_follower.memory").job_mb
    (follower,) = read("follower-values.yaml")
    limit = mebibytes(follower["resources"]["limits"]["memory"])
    longest = driver.LONG_REPEATS * 5.0  # the fixture is five seconds long
    assert TINY_LOADED + KEPT_AFTER_A_LONG_JOB + job_mb(longest, split=True) < limit


def test_the_hold_outlasts_three_failed_readiness_probes(driver):
    """The scenario holds the old pods on the migrated database for HOLD_SECONDS. That only
    proves something if a pod that fails /readyz would have been dropped well within it."""
    deployment = (LEADER_CHART / "templates" / "deployment.yaml").read_text(encoding="utf-8")
    probe = deployment[deployment.index("readinessProbe:") :]
    period = int(re.search(r"periodSeconds: (\d+)", probe).group(1))
    failures = int(re.search(r"failureThreshold: (\d+)", probe).group(1))
    assert driver.HOLD_SECONDS >= 2 * period * failures


def test_the_images_are_this_tests_own_and_the_values_name_them(driver):
    """`swarmscribe-leader:e2e` is the Compose tests' image (e2e/compose/Dockerfile), which
    other tests on the same machine build and expect: this test never uses that tag."""
    (leader,) = read("leader-values.yaml")
    (follower,) = read("follower-values.yaml")
    assert driver.image_parts(driver.LEADER_IMAGE) == (
        leader["image"]["repository"],
        leader["image"]["tag"],
    )
    assert driver.image_parts(driver.FOLLOWER_IMAGE) == (
        follower["image"]["repository"],
        follower["image"]["tag"],
    )
    assert driver.LEADER_IMAGE != "swarmscribe-leader:e2e" != driver.NEXT_IMAGE
    assert driver.NEXT_IMAGE != driver.LEADER_IMAGE
    assert f"ARG BASE={driver.LEADER_IMAGE}" in (KIND / "next.Dockerfile").read_text("utf-8")


def test_the_next_image_adds_the_revision_the_driver_expects(driver):
    source = (KIND / "next_migration.py").read_text(encoding="utf-8")
    assert f'REVISION = "{driver.NEXT_REVISION}"' in source
    dockerfile = (KIND / "next.Dockerfile").read_text(encoding="utf-8")
    assert "USER 10001:10001" in dockerfile.splitlines()[-1]


@pytest.mark.parametrize(
    ("image", "parts"),
    [
        ("swarmscribe-leader:kind", ("swarmscribe-leader", "kind")),
        ("registry.example.org:5000/team/l:1.2", ("registry.example.org:5000/team/l", "1.2")),
    ],
)
def test_an_image_is_split_into_its_repository_and_its_tag(driver, image, parts):
    assert driver.image_parts(image) == parts


@pytest.mark.parametrize("image", ["swarmscribe-leader", "registry.example.org:5000/l"])
def test_an_image_without_a_tag_is_refused(driver, image):
    with pytest.raises(AssertionError):
        driver.image_parts(image)


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not on the PATH")
@pytest.mark.parametrize(
    ("chart", "values", "release"),
    [
        (LEADER_CHART, "leader-values.yaml", "leader"),
        (FOLLOWER_CHART, "follower-values.yaml", "pool"),
    ],
)
def test_both_charts_render_with_the_kind_values(driver, chart, values, release):
    done = subprocess.run(
        ["helm", "template", release, str(chart), "-n", driver.NAMESPACE, "-f", str(KIND / values)],
        capture_output=True, text=True,
    )
    assert done.returncode == 0, done.stderr
    names = {d["metadata"]["name"] for d in yaml.safe_load_all(done.stdout) if d}
    assert {"leader": driver.LEADER_NAME, "pool": driver.POOL_NAME}[release] in names
```

- [ ] **Step 2: Run it to see it fail**

Run: `uv run pytest packages/leader/tests/test_leader_kind_driver.py -q`
Expected: every test fails or errors with `FileNotFoundError` naming a file under `e2e/leader-kind` (the folder does not exist yet).

- [ ] **Step 3: What the test brings itself**

Create `e2e/leader-kind/postgres.yaml`:

```yaml
# What the kind test of the leader chart brings itself (e2e/leader-kind/run_e2e.py), because
# the chart never does: a Postgres, the volume claim the recordings live on, and the Secret
# the chart reads. Every value here is test-only. This is NOT how a leader's database or
# storage is run: Postgres keeps its data in the pod, and the claim is on one node's disk.
apiVersion: v1
kind: Secret
metadata:
  name: leader
stringData:
  database-url: postgresql://postgres:postgres@postgres:5432/swarmscribe
  link-key: leader-kind-link-key-0123456789abcdef
---
# kind's default StorageClass (local-path): ReadWriteOnce, a folder on the node. Both leader
# pods can mount it because the cluster has one node; on several nodes it would have to be
# ReadWriteMany (the chart's values.yaml says why).
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: leader-data
spec:
  accessModes: ["ReadWriteOnce"]
  resources:
    requests:
      storage: 1Gi
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: postgres
spec:
  replicas: 1
  selector:
    matchLabels: {app: postgres}
  template:
    metadata:
      labels: {app: postgres}
    spec:
      containers:
        - name: postgres
          image: postgres:16
          imagePullPolicy: IfNotPresent
          env:
            - {name: POSTGRES_PASSWORD, value: postgres}
            - {name: POSTGRES_DB, value: swarmscribe}
          ports:
            - containerPort: 5432
          readinessProbe:
            exec:
              command: ["pg_isready", "-U", "postgres", "-d", "swarmscribe"]
            periodSeconds: 2
---
apiVersion: v1
kind: Service
metadata:
  name: postgres
spec:
  selector: {app: postgres}
  ports:
    - port: 5432
      targetPort: 5432
```

- [ ] **Step 4: The two charts' values**

Create `e2e/leader-kind/leader-values.yaml`:

```yaml
# The leader chart's values for the kind test (e2e/leader-kind/run_e2e.py). Test-only: plain
# http inside the cluster, no sign-in (there is no identity provider here), a lease of eight
# seconds as in the Compose tests. Everything not set here is the chart's default: two
# replicas, the NetworkPolicy, the PodDisruptionBudget, the migration hook.
image:
  repository: swarmscribe-leader
  tag: kind
  pullPolicy: Never
# The Service's own name: the followers reach the leader there, and the leader builds its
# file links from it. The release must be named `leader`.
publicUrl: http://leader-swarmscribe-leader
allowHttpPublicUrl: true
secrets:
  existingSecret: leader
oidc:
  allowNone: true
settings:
  LEASE_SECONDS: "8"
  HEARTBEAT_SECONDS: "2"
  REAPER_INTERVAL_SECONDS: "1"
  SCANNER_INTERVAL_SECONDS: "1"
  CLAIM_RETRY_AFTER: "1"
  FOLLOWER_GONE_AFTER_SECONDS: "20"
storage:
  volumes:
    - name: data
      mountPath: /data
      existingClaim: leader-data
ingress:
  enabled: false
networkPolicy:
  ingress:
    from:
      # The follower chart's pods, and the test's prober. Nobody else: the scenario checks.
      - podSelector:
          matchLabels:
            app.kubernetes.io/name: swarmscribe-follower
      - podSelector:
          matchLabels:
            swarmscribe-e2e/role: prober
  egress:
    postgres:
      peers:
        - podSelector:
            matchLabels:
              app: postgres
```

Create `e2e/leader-kind/follower-values.yaml`:

```yaml
# The follower chart's values for the kind test of the LEADER chart
# (e2e/leader-kind/run_e2e.py). Test-only: a plain-http leader inside the cluster and a small
# image with tiny.en baked in.
image:
  repository: swarmscribe-follower
  tag: leader-kind
  pullPolicy: Never
replicaCount: 2
leader:
  # The leader chart's Service (release `leader`), on its port 80.
  url: http://leader-swarmscribe-leader
  allowHttp: true
poolToken:
  existingSecret: pool-token
pool: default
# Room for the twelve-minute recording that is in hand during the upgrade (the driver's
# LONG_REPEATS; packages/leader/tests/test_leader_kind_driver.py checks the arithmetic).
resources:
  requests:
    cpu: 500m
    memory: 1Gi
  limits:
    cpu: "2"
    memory: 2500Mi
scratch:
  sizeLimit: 1Gi
terminationGracePeriodSeconds: 60
minReadySeconds: 5
networkPolicy:
  egress:
    https:
      # A NetworkPolicy is matched after Service translation: the leader's Service is on 80
      # (allowed from leader.url) and its pods listen on 8080, which is the port that counts.
      ports: [443, 8080]
```

- [ ] **Step 5: The next version's image**

Create `e2e/leader-kind/next.Dockerfile`:

```dockerfile
# syntax=docker/dockerfile:1
# TEST ONLY: the leader image with one more migration, so that the kind test can upgrade a
# running leader to "the next version" (e2e/leader-kind/run_e2e.py, step 5). Build it from
# the image under test:
#
#   docker build -t swarmscribe-leader:kind-next --build-arg BASE=swarmscribe-leader:kind \
#     -f e2e/leader-kind/next.Dockerfile e2e/leader-kind
#
# The migration changes nothing but the revision (9999, after whatever the head is). It
# stands for "a migration the previous version can live with"; whether a real one is, is
# for whoever writes it. Never push or deploy this image.
ARG BASE=swarmscribe-leader:kind
FROM ${BASE}
LABEL org.opencontainers.image.description="TEST ONLY: SwarmScribe leader with an extra no-op migration"
USER root
COPY next_migration.py /tmp/next_migration.py
RUN python /tmp/next_migration.py && rm /tmp/next_migration.py
USER 10001:10001
```

Create `e2e/leader-kind/next_migration.py`:

```python
"""Run while building next.Dockerfile (TEST ONLY): adds one migration after the image's head.

It changes nothing but the revision. Written at build time, not kept in the repository,
because its `down_revision` must be whatever the head is in the image under test."""

from swarmscribe_leader.db.migrate import MIGRATIONS, head_revision

REVISION = "9999"  # e2e/leader-kind/run_e2e.py: NEXT_REVISION

TEMPLATE = '''"""The kind test's stand-in for the next version's migration: the revision only."""

revision = "{revision}"
down_revision = "{head}"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
'''


def main() -> None:
    head = head_revision()
    if head == REVISION:
        raise SystemExit("this image already holds the test's migration")
    target = MIGRATIONS / "versions" / f"{REVISION}_e2e_next_version.py"
    target.write_text(TEMPLATE.format(revision=REVISION, head=head), encoding="utf-8")
    if head_revision() != REVISION:
        raise SystemExit(f"the head is {head_revision()}, not {REVISION}")
    print(f"added migration {REVISION} after {head}")


if __name__ == "__main__":
    main()
```

Create `e2e/leader-kind/.dockerignore` (the build's context is this folder; nothing but the one script belongs in it, least of all the cluster's kubeconfig):

```
*
!next_migration.py
```

- [ ] **Step 6: The hands inside the leader's pod**

Create `e2e/leader-kind/admin.py` (it runs inside a leader pod, where the leader's package is installed; on your machine it is only linted):

```python
"""The kind test's hands inside a leader pod (e2e/leader-kind/run_e2e.py pipes this file to
`kubectl exec -i deploy/leader-swarmscribe-leader -- python - <command> ...`).

The cluster has no identity provider, so nobody can sign in to this leader's admin API. This
administers it with the leader's own functions against its database, as the Compose tests
do, and writes the recordings and reads the transcripts, which live on the leader's volume.
It is the test's stand-in for `swarmscribe-admin`, not a way to run a leader. Every command
prints one JSON value. Nothing here prints a credential or a link; `pool-token` prints the
new token, once, for the Secret."""

import asyncio
import json
import os
import sys
import uuid
import wave
from pathlib import Path

from sqlalchemy import select, text
from swarmscribe_leader.auth.pool_tokens import create_pool_token
from swarmscribe_leader.db.models import Follower, Job, JobAttempt, Recording, StorageLocation
from swarmscribe_leader.db.session import make_engine, make_sessionmaker
from swarmscribe_leader.profiles import set_profile

DATA = Path("/data")  # leader-values.yaml: storage.volumes[0].mountPath
FIXTURE = DATA / ".e2e" / "fixture.wav"  # put there by the driver; outside every location
ACTOR = "e2e"


def write_recording(path: Path, repeats: int) -> None:
    """The speech fixture, `repeats` times over, under another name first: the scanner must
    never see half a file."""
    with wave.open(str(FIXTURE), "rb") as source:
        params, frames = source.getparams(), source.readframes(source.getnframes())
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    with wave.open(str(partial), "wb") as out:
        out.setparams(params)
        for _ in range(repeats):
            out.writeframes(frames)
    partial.replace(path)


async def state(session) -> dict:
    """Everything the driver asserts on: the followers, and every job with its attempts."""
    followers = [
        {"id": str(row.id), "state": row.state, "pool": row.pool}
        for row in (await session.scalars(select(Follower))).all()
    ]
    jobs = {}
    rows = await session.execute(
        select(Job, Recording.key, StorageLocation.name)
        .join(Recording, Recording.id == Job.recording_id)
        .join(StorageLocation, StorageLocation.id == Recording.location_id)
    )
    for job, key, location in rows.all():
        attempts = (
            await session.scalars(
                select(JobAttempt)
                .where(JobAttempt.job_id == job.id)
                .order_by(JobAttempt.started_at)
            )
        ).all()
        transcript = DATA / location / "transcripts" / f"{key}.txt"
        jobs[f"{location}/{key}"] = {
            "state": job.state,
            "attempts": job.attempts,
            "failure_reason": job.failure_reason,
            "tried": [[str(a.follower_id), a.outcome] for a in attempts],
            "text": transcript.read_text(encoding="utf-8") if transcript.is_file() else None,
        }
    return {"followers": followers, "jobs": jobs}


async def run(command: str, arguments: list[str]) -> object:
    engine = make_engine(os.environ["SWARMSCRIBE_DATABASE_URL"])
    try:
        async with make_sessionmaker(engine)() as session:
            if command == "pool-token":
                name, pool = arguments
                _, token = await create_pool_token(session, name=name, pool=pool, actor=ACTOR)
                result: object = token
            elif command == "profile":
                device, model, compute_type = arguments
                await set_profile(
                    session, device, model=model, compute_type=compute_type,
                    temperatures=None, actor=ACTOR,
                )
                result = True
            elif command == "location":
                # Written straight to Postgres, as the Compose driver does.
                name, mode, consent = arguments
                (DATA / name).mkdir(parents=True, exist_ok=True)
                (DATA / name / "consent.txt").write_text(consent + "\n", encoding="utf-8")
                session.add(
                    StorageLocation(
                        id=uuid.uuid4(), name=name, backend="local",
                        config={"root": f"/data/{name}"}, input_prefix="",
                        output_prefix="transcripts/", pool="default", required_device="any",
                        scan_interval_s=0, enabled=True, vocabulary_version=0,
                        channel_mode=mode, channel_labels=["Agent", "Customer"],
                    )
                )
                result = True
            elif command == "recording":
                location, key, repeats = arguments
                write_recording(DATA / location / key, int(repeats))
                result = True
            elif command == "revision":
                result = await session.scalar(text("select version_num from alembic_version"))
            elif command == "state":
                result = await state(session)
            else:
                raise SystemExit(f"unknown command {command}")
            await session.commit()
            return result
    finally:
        await engine.dispose()


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run(sys.argv[1], sys.argv[2:]))))
```

- [ ] **Step 7: The driver**

Create `e2e/leader-kind/run_e2e.py`:

```python
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


def helm(*arguments: str) -> str:
    return call("helm", "--kubeconfig", str(KUBECONFIG), "-n", NAMESPACE, *arguments)


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
    helm(
        "upgrade", "--install", LEADER, str(LEADER_CHART), "-f", str(HERE / "leader-values.yaml"),
        "--set", f"image.repository={repository}", "--set", f"image.tag={tag}",
        "--timeout", "300s",
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
    for tool in ("kind", "kubectl", "helm", "docker"):
        expect(shutil.which(tool) is not None, f"{tool} is not on the PATH")
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
```

- [ ] **Step 8: Keep the kubeconfig out of git and out of image builds**

In `.gitignore`, after the line `e2e/follower-kind/work/`, add:

```
e2e/leader-kind/work/
```

In `.dockerignore`, after the line `e2e/follower-kind/work`, add:

```
e2e/leader-kind/work
```

- [ ] **Step 9: Run the test and the linter**

```bash
export PATH="$TEMP/chart-tools/windows-amd64:$TEMP/chart-tools:$PATH"
uv run pytest packages/leader/tests/test_leader_kind_driver.py -q
uv run ruff check e2e/leader-kind packages/leader/tests/test_leader_kind_driver.py
python e2e/leader-kind/run_e2e.py --help
```

Expected: `13 passed` (with Helm on the PATH and the whole workspace installed; `12 passed, 1 skipped` if Helm is not on the PATH, and then put it there and run again); `All checks passed!`; a usage line naming `{up,run,down}`.

The values against the charts, once more by hand:

```bash
helm template leader deploy/helm/swarmscribe-leader -n swarmscribe-e2e -f e2e/leader-kind/leader-values.yaml > "$TEMP/leader-kind.yaml"
kubeconform -strict -summary -kubernetes-version 1.33.0 "$TEMP/leader-kind.yaml" e2e/leader-kind/postgres.yaml
```

Expected: `Summary: 11 resources found in 2 files - Valid: 11, Invalid: 0, Errors: 0, Skipped: 0`.

- [ ] **Step 10: Commit**

```bash
git add e2e/leader-kind packages/leader/tests/test_leader_kind_driver.py .gitignore .dockerignore
git commit -m "A kind test of the leader chart with the follower chart

The test brings Postgres, a volume claim and the Secret; installs both
charts; transcribes through them; sees the NetworkPolicy block; and
upgrades the leader to an image with one more migration while requests
flow. Not yet run against a cluster: that is the next task.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Run it on `kind`, see it fail without the fix, and record both

**Files:**
- Create: `docs/superpowers/plans/2026-10-05-leader-chart-outcomes.md`
- Modify: `e2e/leader-kind/run_e2e.py`, and any other file of Task 1, **only** where the run shows the driver is wrong (see "What the planner ran, and what it did not")

**Interfaces:**
- Consumes: Task 1; L1's `docker/leader.Dockerfile` and `docker/check-leader-image.sh`; `docker/follower.Dockerfile`.
- Produces: the outcomes document, which Task 4's README quotes. The four images stay on the machine for Task 3's local dry run; the cluster does not.

**The rule of this task: write down what happened.** Every number and every quoted line in the outcomes document comes from a command run in this task. Nothing is copied from this plan. A step that was not run is listed under "What was not run", with the reason.

- [ ] **Step 1: Check the disk, the tools and the neighbours**

```bash
export PATH="$TEMP/chart-tools/windows-amd64:$TEMP/chart-tools:$PATH"
bash docker/check-free-space.sh
kind version; helm version --short; kubectl version --client; docker version --format '{{.Server.Version}}'
kind get clusters
docker ps --format '{{.Names}}'
```

Expected: `ok: C: has <N> GB free`; four versions; then whatever clusters and containers already exist. **Write those two lists down: they are not yours, and they must be the same when this task ends** (Step 9).

- [ ] **Step 2: Build the four images**

```bash
bash docker/check-free-space.sh
docker build -t swarmscribe-leader:kind -f docker/leader.Dockerfile .
bash docker/check-leader-image.sh swarmscribe-leader:kind
bash docker/check-free-space.sh
docker build -t swarmscribe-leader:kind-next --build-arg BASE=swarmscribe-leader:kind \
  -f e2e/leader-kind/next.Dockerfile e2e/leader-kind
bash docker/check-free-space.sh
docker build --build-arg MODELS=tiny.en -t swarmscribe-follower:leader-kind --target cpu \
  -f docker/follower.Dockerfile .
docker pull postgres:16
docker images --format '{{.Repository}}:{{.Tag}} {{.Size}}' | grep -e 'swarmscribe-leader:kind' -e 'leader-kind'
```

Expected: the check's `ok:` line; the second build prints `added migration 9999 after 0006` (or whatever the head is by then); three image lines.

Confirm the next image holds the migration and still runs as 10001:

Run: `MSYS_NO_PATHCONV=1 docker run --rm --entrypoint python swarmscribe-leader:kind-next -c "import os; from swarmscribe_leader.db.migrate import head_revision; print(os.getuid(), head_revision())"`
Expected: `10001 9999`.

- [ ] **Step 3: `up`**

```bash
bash docker/check-free-space.sh
time python e2e/leader-kind/run_e2e.py up
```

Expected: `the cluster swarmscribe-leader-e2e is up, with Postgres and a volume claim in swarmscribe-e2e`. Some two minutes on a new cluster. If `kind load docker-image` fails for an image, the driver falls back to `docker save | ctr import` by itself; if both fail it says what each said.

- [ ] **Step 4: `run`**

```bash
time python e2e/leader-kind/run_e2e.py run
```

Expected: a last line starting `passed (tiny.en on cpu):`. It will probably not pass the first time: this is the driver's first run.

When it fails, the line starts `FAILED:`. Look before changing anything:

```bash
export KUBECONFIG="$PWD/e2e/leader-kind/work/kubeconfig"
kubectl -n swarmscribe-e2e get pods,jobs,endpointslices,networkpolicy,pdb -o wide
kubectl -n swarmscribe-e2e describe deploy/leader-swarmscribe-leader | tail -30
kubectl -n swarmscribe-e2e logs deploy/leader-swarmscribe-leader --tail=40
kubectl -n swarmscribe-e2e logs job/leader-swarmscribe-leader-migrate --tail=40   # only if a hook failed
unset KUBECONFIG
```

Then decide which of three things it is, and act accordingly:

- **The driver is wrong** (a `kubectl` flag, a parse, a timing guess; the six places listed above): fix the driver, keeping what the assertion means. `up` again for a fresh namespace before each `run`.
- **The chart is wrong** (a pod that cannot start read-only, a policy that cuts off something the leader needs, a hook that cannot read its Secret): fix the chart, add the case to `deploy/helm/swarmscribe-leader/ci/check_render.py` if it can be seen in a render, run that check, and note it for the outcomes.
- **The leader or the image is wrong** (a request fails during the upgrade for a reason that is not the test's; a stop that drops requests): **stop and report to the controller.** That is a finding, not something to tune away. Do not raise a timeout, widen an expectation or add a retry to make it pass.

Keep a list of every change made and why: it goes into the outcomes document.

- [ ] **Step 5: Run it until it has passed twice in a row, timed**

```bash
bash docker/check-free-space.sh
time python e2e/leader-kind/run_e2e.py up && time python e2e/leader-kind/run_e2e.py run
time python e2e/leader-kind/run_e2e.py up && time python e2e/leader-kind/run_e2e.py run
```

Expected: two `passed` lines. Copy both whole, with the four `real` times. If one of them fails, it is not "flaky": find out why (Step 4), and the count of two starts again.

- [ ] **Step 6: Look by hand at what the scenario cannot say**

After the second pass, before `down`:

```bash
export KUBECONFIG="$PWD/e2e/leader-kind/work/kubeconfig"
kubectl -n swarmscribe-e2e get pods -o wide
kubectl -n swarmscribe-e2e get pods -l app.kubernetes.io/component=leader \
  -o jsonpath='{range .items[*]}{.metadata.name} {.spec.containers[0].image} {.status.containerStatuses[0].restartCount}{"\n"}{end}'
MSYS_NO_PATHCONV=1 kubectl -n swarmscribe-e2e exec deploy/leader-swarmscribe-leader -- \
  python -c "import os; print(sorted(os.listdir('/data/calls')), os.stat('/data').st_uid, oct(os.stat('/data').st_mode))"
MSYS_NO_PATHCONV=1 kubectl -n swarmscribe-e2e exec deploy/leader-swarmscribe-leader -- \
  sh -c 'cat /proc/1/status | grep -e VmRSS -e VmHWM; cat /sys/fs/cgroup/memory.peak 2>/dev/null'
kubectl -n swarmscribe-e2e get events --sort-by=.lastTimestamp | tail -25
kubectl -n swarmscribe-e2e top pod 2>&1 | head -5
helm --kubeconfig "$KUBECONFIG" -n swarmscribe-e2e history leader
unset KUBECONFIG
```

Write down: the pods and their restarts; the folder's owner and mode (it says how the claim was made writable, which differs by provisioner); the leader's memory (tini's is not the leader's: if `VmRSS` is tini's, read the leader's from `/proc/<its pid>/status` instead and say which); any event of type `Warning`; the release's two revisions. `kubectl top` will say the metrics API is not there: that is expected.

- [ ] **Step 7: The control run — see the test fail without the fix**

The same scenario with the leader's readiness as it was before L1. The commit that added `test_health.py` is L1's Task 1; its parent holds the old code.

```bash
bash docker/check-free-space.sh
old="$(git log --format=%H -1 --diff-filter=A -- packages/leader/tests/test_health.py)~1"
mkdir -p e2e/leader-kind/work/control
git show "$old:packages/leader/src/swarmscribe_leader/api/health.py" > e2e/leader-kind/work/control/health.py
git show "$old:packages/leader/src/swarmscribe_leader/app.py" > e2e/leader-kind/work/control/app.py
grep -n "revision != request.app.state.head_revision" e2e/leader-kind/work/control/health.py
cat > e2e/leader-kind/work/control/Dockerfile <<'EOF'
# CONTROL ONLY: the image under test with the readiness check it had before L1.
FROM swarmscribe-leader:kind
USER root
COPY health.py /app/.venv/lib/python3.12/site-packages/swarmscribe_leader/api/health.py
COPY app.py /app/.venv/lib/python3.12/site-packages/swarmscribe_leader/app.py
USER 10001:10001
EOF
docker build -t swarmscribe-leader:kind-control e2e/leader-kind/work/control
docker build -t swarmscribe-leader:kind-control-next --build-arg BASE=swarmscribe-leader:kind-control \
  -f e2e/leader-kind/next.Dockerfile e2e/leader-kind
export LEADER_IMAGE=swarmscribe-leader:kind-control NEXT_IMAGE=swarmscribe-leader:kind-control-next
python e2e/leader-kind/run_e2e.py up && python e2e/leader-kind/run_e2e.py run; echo "exit $?"
unset LEADER_IMAGE NEXT_IMAGE
```

Expected: the `grep` prints the old line (line 24); steps 1 to 4 of the scenario pass as before; then `FAILED: leader-swarmscribe-leader-... (the old version) is not Ready on the migrated database: the upgrade would take every serving pod away`, and `exit 1`.

Before `down`, see what a caller saw:

```bash
export KUBECONFIG="$PWD/e2e/leader-kind/work/kubeconfig"
kubectl -n swarmscribe-e2e get pods -l app.kubernetes.io/component=leader
kubectl -n swarmscribe-e2e get endpointslices -l kubernetes.io/service-name=leader-swarmscribe-leader -o yaml | grep -c "ready: true"
kubectl -n swarmscribe-e2e logs prober --tail=1
unset KUBECONFIG
```

Expected: both pods `0/1`; `0` ready addresses; a prober line whose `failed` is above zero. Copy all three.

**If the control run passes, the test does not see the defect: stop and report.** Do not go on to Step 8.

- [ ] **Step 8: `down`, and remove the control images**

```bash
python e2e/leader-kind/run_e2e.py down
docker image rm swarmscribe-leader:kind-control swarmscribe-leader:kind-control-next
kind get clusters
docker ps --format '{{.Names}}'
```

Expected: `deleted the cluster swarmscribe-leader-e2e`; the two lists are **exactly Step 1's**. Keep `swarmscribe-leader:kind`, `swarmscribe-leader:kind-next`, `swarmscribe-follower:leader-kind` for Task 3.

- [ ] **Step 9: Write the outcomes**

Create `docs/superpowers/plans/2026-10-05-leader-chart-outcomes.md` with this shape. Every `<...>` is filled from this task's own output; a row that was not run says "not run" and why. Delete nothing from "What this does not prove" unless this task really ran it.

```markdown
# Leader chart: the chart on a `kind` cluster, with the follower chart

The leader chart spec (section 11) has the chart installed on a `kind` cluster with a real
Postgres and a real volume claim, the follower chart joining it, and an upgrade with a
migration while requests flow. This is the record. CI repeats the run (job
`leader-kind-e2e`); the control run below is by hand. Repeat it, and add a section here,
when the leader's readiness, start-up or stop changes, or the chart's Deployment, hook or
NetworkPolicy does.

How: `e2e/leader-kind/run_e2e.py` (`up`, `run`, `down`), with the test's own Postgres, claim
and Secret (`postgres.yaml`), the leader chart with `leader-values.yaml` (two replicas, no
sign-in, plain http to the Service) and the follower chart with `follower-values.yaml` (two
followers, `tiny.en` baked in).

## The run of <date>

Machine: <Windows version>, Docker <version> (<VM memory>), kind <version> (Kubernetes
<version>), kubectl <version>, Helm <version>. Built from `leader-chart` at `<sha>`.
Images: `swarmscribe-leader:kind` <size>, `swarmscribe-leader:kind-next` <size>,
`swarmscribe-follower:leader-kind` <size>. C: had <N> GB free before and <N> after.

| Step | Result | `real` |
|---|---|---|
| `check-leader-image.sh` | <its last line> | |
| `up` (first, second) | <line> | <t>, <t> |
| `run` (first pass) | <the whole last line> | <t> |
| `run` (second pass) | <the whole last line> | <t> |
| `down` | <line> | <t> |

Runs before the two that passed: <how many, and for each failure, the FAILED line and
whether the driver, the chart or the leader was at fault>.

What was changed to get there:

- <file>: <what and why>. (Or: "Nothing: the plan's files passed as written.")

Seen by hand after the second pass: <pods and restarts>; `/data` is `<uid> <mode>`;
the leader held <N> MiB (<how it was read>); warnings in the events: <none, or which>.

## The control run: the same scenario without the fix

`swarmscribe-leader:kind-control` is the image under test with `api/health.py` and `app.py`
as they were at `<old sha>` (`revision != head` on line 24).

Result: <the FAILED line, whole>. At that moment: leader pods <n>/<n> Ready; ready addresses
behind the Service: <n>; the prober's last line: <line>.

So the scenario's step 5 fails on the defect and passes on the fix.

## What this does not prove

- **Shared storage on several nodes.** One node, kind's `local-path` class (ReadWriteOnce):
  two leader pods shared the claim only because they shared the node. A ReadWriteMany
  volume, and leader pods on two nodes, were not run.
- **An Ingress, TLS, or an ingress controller's body limit and logging.** The followers
  reached the leader's Service over plain http inside the cluster.
- **Sign-in.** The leader had no identity provider (`oidc.allowNone`): `swarmscribe-admin`
  was not used, no token was verified, and the role mapping the chart renders was not
  exercised. The pool token was made by calling the leader's own function inside its pod.
- **The console** in the same cluster.
- **A migration that changes the schema.** The upgrade's migration changed the revision
  only. Whether a real migration is compatible with the release before it is not tested by
  anything.
- **A kept-alive connection through a rollout.** Every request of the prober opened a new
  connection.
- **A network plugin other than kind's**, an IPv6 or dual-stack cluster, a node drain, and
  the PodDisruptionBudget doing anything.
- **Load.** The leader's requests and limits were not measured beyond the one figure above.
- **A recording of people talking**: the recordings are one five-second fixture repeated.
```

- [ ] **Step 10: Commit**

```bash
git add docs/superpowers/plans/2026-10-05-leader-chart-outcomes.md e2e/leader-kind deploy/helm/swarmscribe-leader packages/leader/tests/test_leader_kind_driver.py
git status --short
git commit -m "The leader chart on kind: the run, and the control run without the fix

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

`git status --short` before the commit must show only files this task meant to change; `e2e/leader-kind/work/` must not appear. In the commit message's body, one line for each correction made to the driver or the chart.

---

### Task 3: The CI job

**Files:**
- Modify: `.github/workflows/ci.yml` (the job `leader-image` becomes `leader-kind-e2e`)

**Interfaces:**
- Consumes: Tasks 1 and 2; L1's job `leader-image`.
- Produces: CI job `leader-kind-e2e`.

- [ ] **Step 1: Replace the job**

In `.github/workflows/ci.yml`, replace the whole job `leader-image` (its comment lines included) with:

```yaml
  # The real leader image (docker/leader.Dockerfile), built, checked, then installed with
  # the leader chart on a kind cluster beside the follower chart: a recording is transcribed
  # end to end and the leader is upgraded, with a migration, while requests flow
  # (e2e/leader-kind; docs/superpowers/plans/2026-10-05-leader-chart-outcomes.md).
  leader-kind-e2e:
    runs-on: ubuntu-latest
    timeout-minutes: 40
    env:
      # GitHub's runners have about 14 GB free; the 20 GB floor is the development machine's.
      FREE_GB_FLOOR: "5"
    steps:
      - uses: actions/checkout@v4
      - name: Check the disk has room
        run: bash docker/check-free-space.sh 5
      - name: Install Helm and kind, checked against pinned SHA-256 sums
        run: |
          mkdir -p "$RUNNER_TEMP/bin"
          curl -fsSL -o "$RUNNER_TEMP/helm.tgz" https://get.helm.sh/helm-v4.3.0-linux-amd64.tar.gz
          echo "86584a54def73570558f66f5111cc53dfed56689637ae32c1201205d494f54fb  $RUNNER_TEMP/helm.tgz" | sha256sum -c -
          tar -xzf "$RUNNER_TEMP/helm.tgz" -C "$RUNNER_TEMP/bin" --strip-components=1 linux-amd64/helm
          curl -fsSL -o "$RUNNER_TEMP/bin/kind" https://kind.sigs.k8s.io/dl/v0.30.0/kind-linux-amd64
          echo "517ab7fc89ddeed5fa65abf71530d90648d9638ef0c4cde22c2c11f8097b8889  $RUNNER_TEMP/bin/kind" | sha256sum -c -
          chmod +x "$RUNNER_TEMP/bin/kind"
          echo "$RUNNER_TEMP/bin" >> "$GITHUB_PATH"
      - name: Build the leader image
        run: docker build -t swarmscribe-leader:kind -f docker/leader.Dockerfile .
      - name: Check it (a serving leader stays ready on a newer schema; a starting one refuses it)
        run: bash docker/check-leader-image.sh swarmscribe-leader:kind
      - name: Build the next version's image (one more migration; test only)
        run: >-
          docker build -t swarmscribe-leader:kind-next
          --build-arg BASE=swarmscribe-leader:kind
          -f e2e/leader-kind/next.Dockerfile e2e/leader-kind
      - name: Build the follower image with tiny.en baked in
        run: >-
          docker build --build-arg MODELS=tiny.en -t swarmscribe-follower:leader-kind
          --target cpu -f docker/follower.Dockerfile .
      - name: Pull Postgres
        run: docker pull postgres:16
      - name: Create the cluster, load the images, start Postgres
        run: python3 e2e/leader-kind/run_e2e.py up
      - name: Run the scenario (installs both charts, transcribes, upgrades the leader under load)
        run: python3 e2e/leader-kind/run_e2e.py run
      - name: Show the cluster
        if: failure()
        env:
          KUBECONFIG: e2e/leader-kind/work/kubeconfig
        run: |
          kubectl -n swarmscribe-e2e get pods,jobs,endpointslices,networkpolicy,pdb -o wide || true
          kubectl -n swarmscribe-e2e get events --sort-by=.lastTimestamp | tail -40 || true
          kubectl -n swarmscribe-e2e describe pods -l app.kubernetes.io/instance=leader | tail -80 || true
          for pod in $(kubectl -n swarmscribe-e2e get pods -o name); do
            echo "=== $pod"; kubectl -n swarmscribe-e2e logs "$pod" --all-containers --tail=60 || true
          done
      - name: Delete the cluster
        if: always()
        run: python3 e2e/leader-kind/run_e2e.py down || true
```

The leaders and followers never log a token, a credential or a link (the scenario checks the leaders' logs for the pool token, the link key and `/v1/files/`), so printing the logs on failure is safe. `python3` and `kubectl` are on GitHub's Ubuntu runners; the driver uses the standard library only, so there is no `uv sync`.

- [ ] **Step 2: Check the workflow**

Run: `uv run --no-project --with pyyaml python -c "import yaml; jobs = yaml.safe_load(open('.github/workflows/ci.yml'))['jobs']; print(sorted(jobs)); print([s.get('name', s.get('uses')) for s in jobs['leader-kind-e2e']['steps']])"`

Expected: nine jobs, with `leader-kind-e2e` and without `leader-image`; twelve steps, from `actions/checkout@v4` to `Delete the cluster`.

Run: `grep -c "leader-image" .github/workflows/ci.yml README.md`
Expected: `0` for the workflow. The README still says `leader-image` (L1's "Leader image" section): change "CI runs it (job `leader-image`)" to "CI runs it (job `leader-kind-e2e`)", and the count for the README is then `0` too.

- [ ] **Step 3: The same steps, once, on this machine**

The job's commands differ from Task 2's only in the floor. Run its three scenario steps as the job does, to see that `up`, `run` and `down` work straight after each other from built images:

```bash
export PATH="$TEMP/chart-tools/windows-amd64:$TEMP/chart-tools:$PATH"
bash docker/check-free-space.sh
python e2e/leader-kind/run_e2e.py up && python e2e/leader-kind/run_e2e.py run; python e2e/leader-kind/run_e2e.py down
kind get clusters
```

Expected: `passed ...`; `deleted the cluster ...`; the clusters that were there before this plan, and no other.

**The job has not run on a GitHub runner** until the branch is pushed, which this plan does not do. Say so in the outcomes document ("The CI job: written, run step for step on the development machine, not yet run on a GitHub runner") and in the pull request. What may differ there: the runner's CPU count (the recording in hand may finish sooner: the scenario says "raise LONG_REPEATS" if so), its network plugin's timing, and `kind load` on a Linux Docker (the fallback is for Docker Desktop's image store).

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml README.md docs/superpowers/plans/2026-10-05-leader-chart-outcomes.md
git commit -m "CI: install the leader chart on kind and upgrade it under load

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The guide's last two sections, the roadmap and the follow-ups

**Files:**
- Modify: `README.md` ("Deploy the leader": the last subsection rewritten, one subsection added before it; the status table; one line in "Deploy a follower pool", "What has been run, and what has not")
- Modify: `docs/superpowers/roadmap.md` (item 6)
- Create: `docs/superpowers/plans/2026-10-05-leader-chart-followups.md`
- Modify: `docs/superpowers/plans/2026-10-04-follower-f1-followups.md` (the line about `leader.ca`)

**Interfaces:**
- Consumes: Task 2's outcomes document (every figure below comes from it); the spec's sections 10, 13 and 16.
- Produces: the finished guide.

- [ ] **Step 1: "The whole system on one cluster"**

In `README.md`, in "## Deploy the leader", immediately before "### What has been run, and what has not", add:

````markdown
### The whole system on one cluster

The three charts are separate releases, each in a namespace of its own. They meet at one
address, the leader's `publicUrl`:

| From | To | How | What allows it |
|---|---|---|---|
| a follower pod | the leader | `https://leader.example.org`, through the Ingress: `/v1/followers`, `/v1/jobs`, `/v1/files` | the follower chart's egress on 443; the leader chart's `networkPolicy.ingress.from` naming the ingress controller |
| the console | the leader | the same address, `/v1/admin` | the console chart's egress on 443; the same ingress peer |
| an administrator's machine | the leader | the same address, `/v1/admin` | the Ingress |
| the leader | its Postgres | 5432 | `networkPolicy.egress.postgres.peers` |
| the leader | the identity provider | 443 | the leader chart's HTTPS egress, rendered with sign-in |
| the leader | the recordings | a mounted volume | nothing: the node mounts it |

The leader never calls a follower or the console.

In order:

1. The leader: "Kubernetes, with the Helm chart" above, steps 1 to 3.
2. Sign in from your machine, add a location, create a pool token ("The first administrator,
   pool tokens and consoles").
3. A follower pool ("Deploy a follower pool"): put the pool token in a Secret in the pool's
   namespace and install the follower chart with `leader.url: https://leader.example.org`.
   The followers use the Ingress like any outside machine, because the leader builds its
   file links from `publicUrl` and a follower must be able to fetch them.
4. The console ("Deploy the fleet console"): create a console credential on the leader
   (`swarmscribe-admin console create --name fleet --max-role operator`), install the
   console chart, add its first administrator, and register the leader in it with that
   credential and `https://leader.example.org`.

A pool can use the leader's Service directly (`http://leader-swarmscribe-leader.<namespace>`)
only when the leader's own `publicUrl` is that same address, which no machine outside the
cluster can then use, and only with `leader.allowHttp: true` on the pool and
`allowHttpPublicUrl: true` on the leader. That is a test cluster's shape; it is the one the
`kind` test runs. With it, name the pool's pods in the leader's
`networkPolicy.ingress.from`, and add the leader's pod port (8080) to the pool's
`networkPolicy.egress.https.ports`: a NetworkPolicy is matched after the Service's address
translation, so the port that counts is the pod's, not the Service's 80.
````

- [ ] **Step 2: "What has been run, and what has not"**

Replace the whole subsection "### What has been run, and what has not" of "## Deploy the leader" with the text below. Every `<...>` comes from the outcomes document; if a sentence here says more than the outcomes document shows, change the sentence.

````markdown
### What has been run, and what has not

CI lints the chart, validates what it renders against the Kubernetes 1.33 schemas, runs
`deploy/helm/swarmscribe-leader/ci/check_render.py` (some 150 renders: what must hold and
what must be refused), builds the image and checks it (`docker/check-leader-image.sh`), and
installs the chart on a `kind` cluster (job `leader-kind-e2e`, `e2e/leader-kind`). The same
run was made on a development machine and is recorded in
`docs/superpowers/plans/2026-10-05-leader-chart-outcomes.md`.

What that run does, with two leader pods, a Postgres and a volume claim of the test's own,
and two followers from the follower chart with `tiny.en`:

- The migration hook runs before any leader pod and is removed; the pods are Ready in
  <N> s with no restart, as user 10001 under an init, on a read-only root filesystem.
  Nothing Helm keeps and no ConfigMap holds the link key or the database URL.
- Two followers register through the leader's Service in <N> s with a pool token from a
  Secret, and three recordings are transcribed once each through signed links served by the
  leader's pods. A recording the consent file does not name is never queued.
- The NetworkPolicy lets the followers in and nobody else (another pod's connection to a
  leader pod, and to the Service, times out), and lets a leader pod reach DNS and Postgres
  and nothing else (its connection to another pod's open port times out).
- **An upgrade with a migration, with requests flowing.** The hook migrates the database
  while the old pods serve; with the rollout held back they stay Ready on the newer schema
  for 30 seconds, twice as long as failed probes would take to drop them; then the pods are
  replaced one at a time. Of <N> requests sent through the Service in that time, each on a
  new connection, none failed; the Service never had fewer than <N> ready address(es); and a
  recording that was being transcribed was finished in one attempt.
- Run once by hand with the readiness check as it was before, the same scenario fails at
  the hold: both old pods go unready, the Service has no address, and requests fail.

What was not run:

- **Shared storage on several nodes.** The cluster had one node and a ReadWriteOnce claim.
  A ReadWriteMany volume, and leader pods on two nodes, have not been run anywhere.
- **An Ingress and TLS**, and so the request-logging and body-limit settings above: the
  followers used the Service over plain http.
- **Sign-in.** The test cluster has no identity provider: the leader ran without sign-in,
  and the pool token was made inside its pod. `swarmscribe-admin` against a leader in a
  cluster, and the chart's role mapping reaching a real sign-in, have not been run. (Sign-in
  itself is covered by the leader's tests and the console's Compose test.)
- **The console** in the same cluster as the leader.
- **A migration that changes the schema.** The test's migration changes the revision only.
- **A request on a connection that was already open** to a pod that is then stopped.
- A network plugin other than kind's; a node drain; the PodDisruptionBudget doing anything;
  the leader under load (its requests and limits are not measured).
- The CI job on a GitHub runner: it was run step for step on a development machine and
  has not yet run on GitHub. (Remove this line when it has, and say how long it took.)
````

- [ ] **Step 3: The status table and the follower guide**

In `README.md`, replace the status row written in L2

```markdown
| `swarmscribe-leader` image and Helm chart (`deploy/helm/swarmscribe-leader`) | Built; rendered and validated in CI; see "Deploy the leader" for what has been run |
```

with

```markdown
| `swarmscribe-leader` image and Helm chart (`deploy/helm/swarmscribe-leader`) | Built; installed on `kind` with the follower chart in CI; see "Deploy the leader" for what has and has not been run |
```

In "## Deploy a follower pool", subsection "### What has been run, and what has not", replace the item "- Autoscaling on queue depth belongs to the leader's chart (roadmap item 6)." with "- Autoscaling on queue depth is not built: it needs a queue-depth metric the leader does not expose yet (roadmap items 5 and 6)." and the item "- The kind test is run by hand, not in CI." with "- This chart's own kind test (`e2e/follower-kind`) is run by hand, not in CI; the leader's (`e2e/leader-kind`) installs this chart in CI." In the same subsection, the list of what was not run has an item that begins "A leader on a private CA (`leader.ca`), ...". Leave it: it is still true. Add a new item after that list's last item:

```markdown
- The leader's own chart has since been installed beside this one on `kind`
  ("Deploy the leader"), still over plain http: `leader.ca` remains unrun.
```

- [ ] **Step 4: The roadmap**

In `docs/superpowers/roadmap.md`, replace

```markdown
6. Helm chart and autoscaling.
```

with

```markdown
6. Helm chart and autoscaling. *The leader's chart is built
   (`2026-10-05-leader-chart-design.md`); with the console's and the
   follower's, every part now has one. Autoscaling of follower pools is
   open: it needs the queue-depth metric of item 5.*
```

- [ ] **Step 5: The follow-ups**

Create `docs/superpowers/plans/2026-10-05-leader-chart-followups.md`. Start from the spec's section 16 (F1 to F10), copied with their numbers, and add under "From the build" every item the three plans' reviews and Task 2 raised and did not fix: each with where it came from and what was seen. Mark as done any follow-up the build did do. Its first lines:

```markdown
# Leader chart: follow-ups

From the leader chart spec (section 16) and from building plans L1 to L3. None blocks merge.

## From the spec
```

In `docs/superpowers/plans/2026-10-04-follower-f1-followups.md`, at the end of the item that begins "`leader.ca` (a private CA), `models.volume: persistentVolumeClaim`", replace the sentence "The leader's chart (roadmap item 6) will bring a TLS leader to test the first against." with:

```markdown
The leader's chart is built, but its `kind` test runs plain http, so `leader.ca` is still
unrun (leader chart follow-up F7).
```

- [ ] **Step 6: Check the documents against each other**

```bash
grep -n "<N>\|<\.\.\.>\|<date>\|<sha>" README.md docs/superpowers/plans/2026-10-05-leader-chart-outcomes.md
grep -c "has not been installed on a cluster yet" README.md
grep -n "leader-kind-e2e" README.md .github/workflows/ci.yml | head
```

Expected: no placeholder left in either document (the one deliberate `<namespace>` in "The whole system on one cluster" is not matched by this pattern); `0`; the job named in both files.

Read "Deploy the leader" from top to bottom once, as an operator who has only the README. Every command must be one that exists (`swarmscribe-admin` subcommands are in `packages/leader/src/swarmscribe_leader/admin_cli/main.py:59-189`), and every claim about a cluster must be in the outcomes document or be marked as not run.

- [ ] **Step 7: Commit**

```bash
git add README.md docs/superpowers/roadmap.md docs/superpowers/plans/2026-10-05-leader-chart-followups.md docs/superpowers/plans/2026-10-04-follower-f1-followups.md
git commit -m "README: the three charts on one cluster, and what the kind run showed

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## For the owner

Three things this plan decides that are yours (they are in `2026-10-05-leader-chart-open-questions.md`): the `kind` install as a CI job (ruling 10); no shared-storage test (ruling 4); and that the test administers the leader from inside its pod because nothing else can without an identity provider (ruling 2).

## Self-Review

**Spec coverage.** Section 11's six installed checks are the scenario's six steps (Task 1's driver, in its docstring's order); the control run is Task 2, Step 7; the CI job is Task 3. Section 10's walk-through is Task 4, Step 1, with the test cluster's shape set apart from the production one. Section 13's "will not be proven" list is the outcomes document's last section and the README's, item for item. C8 and C9: the scenario, and `free_space()` in the driver plus `check-free-space.sh` before every Docker step.

**What is not done here.** A ReadWriteMany volume; an Ingress; sign-in; the console in the cluster (spec follow-ups F3, F7). Moving the older tests to the real image (F5).

**Type and name consistency.** `LEADER_NAME = "leader-swarmscribe-leader"` is L2's `fullname` for the release `leader`; `publicUrl` in `leader-values.yaml`, `leader.url` in `follower-values.yaml` and `LEADER_URL` in the driver are one string, and the driver's test checks it. `NEXT_REVISION = "9999"` in the driver, `REVISION = "9999"` in `next_migration.py`. The Secret `leader` with keys `database-url` and `link-key` (the chart's defaults); the claim `leader-data`; the Secret `pool-token` with key `pool-token` (the follower chart's default). The prober's label `swarmscribe-e2e/role: prober` in `pod_manifest` and in `leader-values.yaml`. Image tags `swarmscribe-leader:kind`, `:kind-next`, `swarmscribe-follower:leader-kind` in the driver, the values, the plan's commands and the CI job.

**Honesty.** The driver has not been run. Task 2 exists to run it, to record what happened in its own words, and to show the test failing without the fix; the README is written from that record and not from this plan.
