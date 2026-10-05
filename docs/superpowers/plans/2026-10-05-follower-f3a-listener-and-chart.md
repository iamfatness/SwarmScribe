# Follower F3a — The Health Listener's Limits and the Kubernetes Chart Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the follower's health listener safe to put on a pod's address, and ship a Helm chart that runs a pool of followers on Kubernetes (`helm install` per pool against any reachable leader), linted, schema-validated and checked in CI.

**Architecture:** One standalone chart, `deploy/helm/swarmscribe-follower`, renders a Deployment, a NetworkPolicy, a ServiceAccount and (on request) a PodDisruptionBudget; one release is one pool. It never creates a Secret: the pool token is a file mounted from a Secret the operator made. The credential lives in a memory-backed `emptyDir`, in a subfolder the follower makes its own. The listener for `/healthz` and `/metrics` is bound to the pod's address for the kubelet, with no Service, and is hardened first: one request per connection, a deadline for the whole connection and a cap on connections. A Python script renders the chart and asserts what must hold, and CI runs it with `helm lint` and `kubeconform`.

**Tech Stack:** Helm 4.3.0, Kubernetes 1.27 or later (`policy/v1` with `unhealthyPodEvictionPolicy`), kubeconform 0.8.0, Python 3.12 with PyYAML for the render check, `http.server` (standard library), GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-04-follower-design.md` (build step 4, decision D17, sections 5.3, 5.5, 5.6, 5.7, 5.8, 6.5, 7, 8.2, 9 and the amendments after F2), with `docs/superpowers/plans/2026-10-04-follower-f1-followups.md` (sections "F3" and "Left open from the F2a final review") and `docs/superpowers/plans/2026-10-04-fleet-console-c4b-helm-chart.md` for the conventions of the project's charts.

**This plan is the first of two.** F3 as one plan would have passed 4000 lines, so it is split as F2 was. F3b (`2026-10-05-follower-f3b-kind-install-and-guide.md`) installs this plan's chart on a local `kind` cluster against a real leader, records the result, and writes the README's guide and the spec's amendments. This plan needs F0, F1, F2a and F2b merged (main at `96ffd19` or later): the images `swarmscribe-follower:cpu` and `:cuda` (user 10001, `tini` as PID 1, `SWARMSCRIBE_FOLLOWER_HEALTH_ADDR`, the memory guard), `CredentialStore.check_folder` and pool tokens.

## Global Constraints

- "A standalone chart, `deploy/helm/swarmscribe-follower`, built in plan F3 with the console chart's conventions: `values.schema.json`, a render check in CI, `kubeconform`, no Secret created by the chart, image named by the operator (not published), `NetworkPolicy` on by default. One release per pool" (spec 8.2).
- "`resources.limits."nvidia.com/gpu": 1`, a `nodeSelector` and `tolerations` from values, the `cuda` image, `runtimeClassName` from values"; "`maxSurge: 0` on GPU pools (a surge pod would need a GPU that is not there)"; "One process per GPU" (spec 8.2, D4).
- "Volumes: state (`emptyDir`, `medium: Memory`, small), scratch (`emptyDir`, `sizeLimit` from values), and for models nothing by default, because the model is baked into the image (R3); an `emptyDir` or an existing PVC can be chosen instead"; scratch is "an `emptyDir` with `sizeLimit: 20Gi`, on disk, never `medium: Memory`" (spec 8.2, 5.8).
- "The pool token (section 12.1) from `secrets.existingSecret`, mounted as a file (`SWARMSCRIBE_JOIN_TOKEN_FILE`)"; "`SWARMSCRIBE_FOLLOWER_ON_DRAINED=park`"; "`terminationGracePeriodSeconds: 900`; `SHUTDOWN_GRACE_SECONDS` derived from it" and "The follower's grace is always the platform's timeout minus 30 seconds or less" (spec 8.2, 5.6).
- "No readiness probe and no `Service`: nothing connects to a follower except the kubelet and Prometheus"; "`automountServiceAccountToken: false`, read-only root, capabilities dropped" (spec 8.2). The pods run as the image's user 10001 with the `RuntimeDefault` seccomp profile.
- "The chart (8.2) must therefore make the state `emptyDir` the follower's own" (spec 5.3): the state folder is `/var/lib/swarmscribe-follower/state`, a folder the follower creates inside the mount.
- "`/healthz` answers `200` while the supervisor has ticked in the last 30 seconds ... It reports that the threads are alive, not that the leader answered"; it "is 200 from the moment the follower starts supervising, so also while the model loads" (spec 9 and its amendment). Task 1 must not change what `/healthz` and `/metrics` answer.
- "The join token and the credential are never logged, never passed as arguments, never baked into an image" (spec 7). No token in a values file or a rendered manifest.
- "The same agent code runs in the CPU image, the CUDA image and installed directly on Linux and Windows" (spec 1): Task 1's code and tests run on Windows too.
- **The images are not published** (owner ruling). The chart takes the image as a value with no default.
- **Nothing here runs a follower on a GPU in Kubernetes**, and nothing may claim it: the chart's GPU path is rendered and schema-checked (F3b shows a `cuda` image exiting 3 in a pod on a cluster without GPUs).
- **The whole suite is the gate of every task that changes Python:** `uv run ruff check .` and `uv run pytest` over the whole repository (about 12 minutes; run focused tests while working and the whole suite once, before the task's commit). Python `>=3.11`; `ruff` line length 100, rules `E, F, I, UP, B`. **No test seam in production paths.**
- **The development machine** is Windows 11 with Git Bash. `uv` is not on the PATH: run it as `python -m uv` (the commands below are written `uv run ...`). Helm and kubeconform are standalone binaries in a scratch folder, never installed system-wide: see "The tools on the Windows machine" below, and run its `export PATH=...` line in every shell that uses them. No task of this plan needs Docker.
- **Work only in the worktree `C:\Users\walla\SwarmScribe-f3`**, on the branch `follower-f3`. Never touch `C:\Users\walla\SwarmScribe`, `C:\Users\walla\SwarmScribe-ui` or `C:\Users\walla\SwarmScribe-f2a-fix`. Do not push. Never use ports 8900 or 8901 (the listener's tests bind port 0: the system chooses).
- **Another agent uses Docker on this machine**: should you use Docker at all, never stop, remove or retag a container or an image you did not create, never run any `docker ... prune`, never restart Docker.
- **Capture, then match.** In a shell check under `set -euo pipefail`, never pipe a live `docker`, `kubectl` or `helm` command into `grep -q`: `grep -q` leaves at its first match, the producer dies of `SIGPIPE`, and `pipefail` fails a check that passed. Write the output to a variable or a file first, then match it. (This plan's checks are Python and capture everything; its shell commands are run by hand and read by eye.)
- Commit after each task with the message the task gives, ending with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

### The tools on the Windows machine

Helm and kubeconform are single binaries. They are already in `$TEMP/chart-tools` on the development machine; fetch whichever is missing (Git Bash), and never install one system-wide:

```bash
mkdir -p "$TEMP/chart-tools" && cd "$TEMP/chart-tools"
[ -x windows-amd64/helm.exe ] || { curl -sL -o helm.zip https://get.helm.sh/helm-v4.3.0-windows-amd64.zip && unzip -q -o helm.zip; }
[ -x kubeconform.exe ] || { curl -sL -o kc.zip https://github.com/yannh/kubeconform/releases/download/v0.8.0/kubeconform-windows-amd64.zip && unzip -q -o kc.zip; }
cd -
```

Then, in every shell that runs `helm` or `kubeconform`:

```bash
export PATH="$TEMP/chart-tools/windows-amd64:$TEMP/chart-tools:$PATH"
helm version --short   # v4.3.0+gbec5b06
```

(Checked on 2026-10-05: both addresses answer 200. CI's job `chart` already fetches the same two versions for Linux.)

## Measured before this plan was written

All of this plan's code ran on 2026-10-05 in a scratch folder outside the repository, built from `main` at `96ffd19` (Windows 11, Docker Desktop 29.8.1 with WSL 2 and 16.6 GB for the Docker VM, Helm 4.3.0, kubeconform 0.8.0): the listener change and its tests, the chart and its render check. The chart was also installed on a `kind` cluster (kind 0.30.0, Kubernetes 1.34.0) against a real leader, with F3b's scenario, which passed four times; the figures below that come from a pod are from there.

| What | Result |
|---|---|
| `test_health.py` with the six new tests, before the change | 5 failed, 34 passed (the sixth, a request sent in pieces, is a guard and passes) |
| the same, after | 39 passed in 15 s |
| The whole suite with every change of F3a and F3b | `3128 passed, 19 skipped, 5 deselected` in 11 min 26 s (`main` at `96ffd19` has 3110 passing tests; these plans add 18) |
| In a pod: a client sending one byte every half second | dropped after 5.0 s (before: held for ever) |
| In a pod: 20 silent connections, then a request | turned away in 0.01 s; 6 s later the listener answered 200 again; the pod was not restarted |
| `helm lint --strict`, CPU and GPU values | `1 chart(s) linted, 0 chart(s) failed` |
| `kubeconform -strict`, Kubernetes 1.33.0, both renders | `7 resources found in 2 files - Valid: 7, Invalid: 0, Errors: 0, Skipped: 0` |
| `check_render.py` | passes in 7 s; 16 one-line breakages of the templates each made it fail and name the property |
| `helm upgrade --install` on `kind`, two replicas | both pods Ready in 3 s, both registered 3 to 4 s after the install |
| The kubelet's probes with ingress closed to everyone | pass (the pods are Ready); another pod's request to port 9108 timed out; after `networkPolicy.ingress.from` named that pod it got 200, and no pod was restarted for it |
| A `cuda` image in a pod, no GPU on the node | `CrashLoopBackOff`, exit 3, `error: cuda was requested but no CUDA GPU is available`; with `gpu.enabled` the pod is `Pending`: `0/1 nodes are available: 1 Insufficient nvidia.com/gpu` |

**The state folder in a pod** (carry-over 3), read inside a running pod with `fsGroup: 10001`:

| Path | Owner and mode | |
|---|---|---|
| `/var/lib/swarmscribe-follower` (the `emptyDir`, `medium: Memory`) | `0:10001`, `3777` | root's: refused as the state folder itself |
| `/var/lib/swarmscribe-follower/state` | `10001:10001`, `2700` | made by the follower; passes `check_folder` (the set-group-id bit comes from the mount and is no write bit) |
| `.../state/credential.json` | `10001:10001`, `600` | |
| `/scratch` (the `emptyDir` on disk) | `0:10001`, `2777` | the scratch marker is written and the folder accepted |
| `/run/secrets/swarmscribe/pool-token` | `0:10001`, `440` | readable through the group only |

`doctor` in the pod: `state folder: ok`, `memory: 2500 MiB may be used (SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB)` (the test's limit), `joined: yes`. PID 1 is `tini`. The listener answered on the pod's address and refused loopback. A killed follower's container restarted (exit 137) and came back as the same follower without registering.

**Memory after a long recording** (carry-over 4). One follower in a pod, sampled every 3 s from `/proc/<pid>/status`; the long recordings are the speech fixture repeated:

| Moment (`tiny.en`, `int8`, 4 CPUs, limit 8Gi) | Held (RSS) | Peak so far |
|---|---|---|
| loaded and warmed up | 230 MiB | 282 MiB |
| after a 5-second recording | 261 MiB | 309 MiB |
| after one hour, mono | 432 MiB | 3784 MiB |
| after a second hour, mono | 460 MiB | 3899 MiB |
| after one hour, split | 472 MiB | 3899 MiB |
| after a third hour, mono (and 30 s later) | 442 MiB | 3899 MiB |

| Moment (`distil-large-v3`, `int8`, 4 CPUs, limit 3930Mi, the model read from the `emptyDir`) | Held (RSS) | Peak so far |
|---|---|---|
| loaded and warmed up | 1766 MiB | 1937 MiB |
| after half an hour, mono (transcribed in 360 s) | 1779 MiB | 3509 MiB |
| after a second half hour (349 s) | 1779 MiB | 3509 MiB |

Both half-hour recordings were accepted under 3930Mi, which is the new formula for half an hour (1730 + 400 + 1800); the cgroup's own count peaked at 3540 MiB.

**The old sizing refuses the second recording** (seen, not only computed): a `tiny.en` pod limited to 3990Mi, the README's table for one 61-minute recording (230 + 100 + 3660) with 30 MiB to spare, took the first such recording (169 s) and refused the second, identical one three times: `out_of_resources: OutOfMemory: a recording of 61 minutes needs about 4134 MiB here and this follower may use 3990 MiB (SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB)`. (The "hour" of these measurements is the 5-second fixture 720 times: 61 minutes.)

So the process does fall back after a job, but not all the way: it keeps between 13 and 242 MiB more than it held when the model was loaded, and that does not grow from job to job. The guard adds its estimate to what the process holds at that moment, so a pod sized as "model + 100 MiB + 3600 MiB per hour" (the README's table) takes its first long recording and can refuse the next. The chart's default and F3b's sizing guide therefore add 300 MiB for it: `model + 0.4 GiB + 3.6 GiB x hours` (ruling 9).

**A model downloaded at start-up costs memory.** With `models.volume: emptyDir` and an image without a model, a pod downloaded `distil-large-v3` (1.5 GB) from Hugging Face through the chart's NetworkPolicy and registered 30 s after it started; it then held 2257 MiB, where the same container restarted (the model now read from the `emptyDir`) held 1766 MiB. One more reason for baked models (R3); F3b's README says so.

## Rulings

Decisions this plan makes where the spec is silent or is departed from. Those marked **(owner)** are the owner's to overturn; each has the plan's recommendation built in, so nothing waits for an answer.

1. **The listener: one request per connection, five seconds for the whole connection, eight connections at once** (carry-over 1). Every answer carries `Connection: close`; a ninth connection is closed unanswered; a connection that has not finished in `REQUEST_SECONDS = 5.0` is dropped, however its bytes arrive and whether or not it reads its answer. Statuses and bodies are unchanged. A scraper loses keep-alive (one TCP handshake per scrape). The cap bounds threads; it cannot reserve a place for the kubelet, so a peer the NetworkPolicy lets in could still crowd the probe out by reconnecting for 30 s on end, which is why ruling 3 lets nobody in by default.
2. **The listener is bound to the pod's own address**, `[$(POD_IP)]:9108`, from the downward API, not to `0.0.0.0` (D18: "pod IP in the chart"). The brackets serve an IPv6 pod address and are stripped from an IPv4 one. Consequence: nothing listens on loopback in a pod, so `kubectl port-forward` does not reach it; F3b's README gives a `kubectl exec` line instead.
3. **`/metrics` is reachable by nobody unless the operator names who** (carry-over 2): no Service, a NetworkPolicy on by default whose ingress is empty unless `networkPolicy.ingress.from` lists peers (the monitoring namespace), and `metrics.scrapeAnnotations` (off) for the conventional `prometheus.io/*` pod annotations. No `PodMonitor`: it needs a CRD the chart cannot assume, and could not be tried here. The kubelet's probes pass regardless (they come from the pod's node; measured). **(owner)** Recommended as written. Overturning it later (a Service, a PodMonitor, open by default) is a values-and-template change with no migration; opening by default would expose an unauthenticated endpoint to every pod of a cluster that has no default-deny policy.
4. **The state folder is `/var/lib/swarmscribe-follower/state`**, inside a memory-backed `emptyDir` mounted where the image has its state folder, with `fsGroup: 10001` (carry-over 3; measured above). `HOME` and the working directory stay on the mount. No `/tmp` volume: nothing in the follower writes a temporary file (the Compose test has run it read-only since F2a), and Python's fallback is the working directory, which is this 16 MiB tmpfs.
5. **The pool token comes only from a Secret the operator created** (`poolToken.existingSecret`, required), as the console chart reads its secrets; the chart has no value for the token itself and the schema refuses one. The spec's value name `secrets.existingSecret` becomes `poolToken.existingSecret` (there is one secret). **(owner)** Recommended as written. A `poolToken.value` that the chart turns into a Secret would be easier for a first install and puts the pool's most powerful secret into values files and Helm's release history; adding it later is one template.
6. **A changed Secret does not roll the pods, and F3 does not "fix" that** (carry-over 7, the console chart's known follow-up). The token is a mounted file read only when a pod registers; the kubelet refreshes the file; running followers hold credentials and need nothing. A checksum annotation would restart every follower, and hand back every recording in hand, for a change that needs no restart; nor can a chart checksum a Secret it does not own. The one case that needs a restart is a pool revoked with `--revoke-followers`: revoked pods keep their credential and exit 4 until they are replaced, so F3b's README says `kubectl rollout restart` after putting the new token in the Secret (proven in F3b's scenario).
7. **Stopping** (carry-over 5): `terminationGracePeriodSeconds: 900` and `SWARMSCRIBE_FOLLOWER_SHUTDOWN_GRACE_SECONDS` = that minus 30 (the schema's minimum is 30). On `SIGTERM` the follower finishes a recording whose estimated time left fits, and otherwise hands it back without a counted attempt (spec 5.6); a second signal never comes from the kubelet. `ON_DRAINED=park`. **No PodDisruptionBudget by default**: an evicted follower loses only compute, and a budget of one would make a node drain wait up to the grace period per pod; `podDisruptionBudget.enabled` renders one (with `unhealthyPodEvictionPolicy: AlwaysAllow`, so a crash-looping pod never blocks a drain) for more than one replica.
8. **Rollouts: `maxUnavailable: 25%`, and `maxSurge` 25% on a CPU pool, 0 on a GPU pool.** The spec gives the surge for GPU pools only. A surge pod on a CPU pool keeps the pool's capacity through a rollout and costs one extra `followers` row per surge pod at most.
9. **Default size: requests 2 CPUs and 6Gi, limits 4 CPUs and 6Gi**, which serves recordings of up to one hour with `distil-large-v3` on a CPU (1770 + 400 + 3600 MiB = 5770 of 6144) or `large-v3` on a GPU, and refuses longer ones with a reason. The sizing guidance becomes `model + 0.4 GiB + 3.6 GiB x hours` (measured above). Memory request equals limit; the limit is passed to the guard from the downward API (`limits.memory` in MiB), and on a CPU pool `OMP_NUM_THREADS` is the CPU limit. **(owner)** Recommended as written. A smaller default starts cheaper pods that refuse more recordings; a larger one wastes memory on pools of short recordings. Changing it later is one line of `values.yaml` and a re-install.
10. **A GPU pool is `gpu.enabled: true`**: exactly one `nvidia.com/gpu` per pod, whatever `resources` says (D4), `SWARMSCRIBE_FOLLOWER_DEVICE=cuda`, no surge; `runtimeClassName`, `nodeSelector`, `tolerations` and `affinity` from values; `gpu.resource` renames the resource. A CPU pool does not set the device: the image decides, so a `cuda` image on a CPU pool, or on a node whose runtime hands over no GPU, exits 3 with `error: cuda was requested but no CUDA GPU is available` and the pod shows `CrashLoopBackOff` (measured). Not run on a GPU in Kubernetes.
11. **The startup probe allows two minutes, not the spec's thirty.** Since F2, `/healthz` answers 200 from the first seconds, while the model loads, so a slow model download can no longer be taken for a failed start; the startup probe only covers the interpreter's start (measured: Ready in 3 s). Liveness: every 10 s, 3 s timeout, three failures.
12. **Egress: DNS, and TCP 443 plus the port of `leader.url` to anywhere except the ranges the console chart also refuses** (loopback, link-local and metadata, reserved). The leader's file links may point at a storage service, and a NetworkPolicy cannot name a host. The leader's port is derived from the URL so that it cannot be forgotten (a leader on 8443, plain http on 8080 in a test cluster).
13. **`values.schema.json` allows no unknown value** (`additionalProperties: false` at the top and in every object the chart reads), unlike the console's: `replicas: 3` for `replicaCount` fails the render instead of silently deploying one pod.
14. **Settings are plain `env` entries of the Deployment, not a ConfigMap**: a changed setting changes the pod template and rolls the pods by itself, with no checksum annotation and one object fewer. `settings` and `extraEnv` refuse the names the chart sets.
15. **`tini` and the process namespace** (F2a follow-up "Decide with F3's chart"): the chart never sets `shareProcessNamespace`, the render check refuses it, and `tini` is PID 1 in the pod (measured), so its warning never appears. Nothing is silenced.
16. **A revoked pod loads its model again at every restart** (F1 follow-up M5) and nothing here changes that: Kubernetes' back-off (up to five minutes) spaces the restarts, and F3b's README says what the pod is doing and how to end it.

## Review Focus

Inputs and conditions the spec implies and that are most likely to bite a person running a pool, each pinned by a test in the task that owns the code:

1. **Something on the cluster network connects to the health port and stalls, trickles, or never reads**: it is dropped at five seconds, the ninth connection is turned away at once, and the next request is answered — Task 1, `test_a_client_that_trickles_bytes_is_dropped_at_the_deadline`, `test_no_more_than_the_cap_are_held_and_the_next_is_turned_away_at_once`, `test_held_places_free_themselves_at_the_deadline`, `test_a_client_that_never_reads_its_answer_does_not_hold_a_place`.
2. **A values file with a mistyped name, a token, or a setting the chart owns**: the render fails and says which — Task 2, `check_core` (`replicas=3`, `poolToken.value`, every owned name under `settings` and `extraEnv`).
3. **The second long recording on a pod**: the default memory must admit it, not only the first — Task 3, `test_the_default_memory_serves_an_hour_with_the_cpu_default_model`.
4. **A leader that is not on port 443** (8443, or plain http on 8080 inside a cluster): the NetworkPolicy must let the pods reach it, or they start and never register — Task 3, `check_network`.
5. **A follower setting renamed in `config.py` while the chart still passes the old name**: the follower ignores unknown variables, so a pool would silently run with a default — Task 3, `test_every_setting_the_chart_sets_is_a_setting_of_the_follower`.
6. **A GPU pool given two GPUs, or a surge pod, through values**: one GPU per pod and no surge, whatever `resources` says — Task 3, `check_gpu`.

## File Structure

```
packages/follower/src/swarmscribe_follower/health.py   (modify) the deadline and the cap
packages/follower/tests/test_health.py                 (modify) their tests
deploy/helm/swarmscribe-follower/
  Chart.yaml
  values.yaml                 every value, documented
  values.schema.json          types, ranges, no unknown value
  .helmignore
  ci/
    test-values.yaml          a CPU pool: what CI lints and renders with
    gpu-values.yaml           a GPU pool, on top of it (Task 3)
    check_render.py           what the rendered chart must hold
  templates/
    _helpers.tpl              names, labels, validation, resources, security contexts, refused ranges
    serviceaccount.yaml
    deployment.yaml
    NOTES.txt
    networkpolicy.yaml        Task 3
    pdb.yaml                  Task 3
packages/follower/tests/test_chart_files.py            (new, Task 3) the chart against follower and image
.github/workflows/ci.yml                               (modify, Task 3) job `chart`
```

---

### Task 1: The health listener — a deadline for the whole request, and a cap on connections

The chart (Task 2) puts the listener on the pod's address. Today a handler's timeout is per `recv`: a client that sends one byte every few seconds holds a thread for ever, and nothing bounds how many do. This task closes that first. What `/healthz` and `/metrics` answer does not change, except that every answer now says `Connection: close`.

**Files:**
- Modify: `packages/follower/src/swarmscribe_follower/health.py`
- Test: `packages/follower/tests/test_health.py`

**Interfaces:**
- Consumes: `HealthServer(address, *, healthy, metrics)` with `start()`, `close()`, `port`; the test file's `served` fixture and `ask(port, method, path)` (both exist).
- Produces: `swarmscribe_follower.health.REQUEST_SECONDS = 5.0` and `MAX_CONNECTIONS = 8` (module constants, read when a connection is accepted and when a listener is made). `HealthServer`'s signature and behaviour are otherwise unchanged; `main.py` and the image are not touched. Task 2's probes rely on a 3-second answer and F3b's README names the two figures.

- [ ] **Step 1: Write the failing tests**

In `packages/follower/tests/test_health.py`, replace

```python
from follower_testkit import FakeEngine, FakeLeader, make_agent, make_runner
```

with

```python
from follower_testkit import FakeEngine, FakeLeader, make_agent, make_runner
from swarmscribe_follower import health
```

and add at the end of the file:

```python
# --- a listener that others can reach (the chart puts it on the pod's address) -----------


def closed_within(connection, seconds):
    """Whether the other side closes `connection` within `seconds`, while this side keeps
    sending one byte every 50 ms (a request line that never ends)."""
    connection.settimeout(0.05)
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        try:
            connection.sendall(b"x")
            if connection.recv(1024) == b"":
                return True
        except TimeoutError:
            continue
        except OSError:  # reset: closed as well
            return True
    return False


def answers_within(port, seconds):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        try:
            if ask(port, "GET", "/healthz")[0] == 200:
                return True
        except (OSError, http.client.HTTPException):
            time.sleep(0.05)
    return False


def test_every_answer_closes_its_connection(served):
    port, _answer = served
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("GET", "/healthz")
        response = connection.getresponse()
        assert (response.status, response.read()) == (200, b"ok\n")
        assert response.getheader("Connection") == "close"
        assert connection.sock is None  # http.client saw the close and let go
    finally:
        connection.close()
    assert ask(port, "GET", "/nothing")[2]["connection"] == "close"
    assert ask(port, "POST", "/healthz")[2]["connection"] == "close"


def test_a_client_that_trickles_bytes_is_dropped_at_the_deadline(served, monkeypatch):
    # The old timeout was per read: one byte every few seconds held a thread for ever.
    monkeypatch.setattr(health, "REQUEST_SECONDS", 0.5)
    port, _answer = served
    slow = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        started = time.monotonic()
        slow.sendall(b"GET /healthz HTTP/1.1\r\nX-Slow: ")
        assert closed_within(slow, 5.0), "a trickling client was still held after 5 s"
        assert time.monotonic() - started < 3.0
    finally:
        slow.close()
    assert ask(port, "GET", "/healthz")[0] == 200


def test_a_request_inside_the_deadline_is_answered_however_it_arrives(served):
    port, _answer = served
    piecemeal = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        for piece in (b"GET /hea", b"lthz HTTP/1.1\r\n", b"Host: x\r\n", b"\r\n"):
            piecemeal.sendall(piece)
            time.sleep(0.05)
        assert piecemeal.recv(1024).startswith(b"HTTP/1.1 200 ")
    finally:
        piecemeal.close()


def test_no_more_than_the_cap_are_held_and_the_next_is_turned_away_at_once(monkeypatch):
    monkeypatch.setattr(health, "MAX_CONNECTIONS", 2)
    server = HealthServer(("127.0.0.1", 0), healthy=lambda: (True, "ok"), metrics=bytes)
    server.start()
    held = []
    try:
        for _ in range(2):
            silent = socket.create_connection(("127.0.0.1", server.port), timeout=5)
            silent.sendall(b"G")  # accepted and being read: it holds one of the two places
            held.append(silent)
        time.sleep(0.2)
        started = time.monotonic()
        with pytest.raises((OSError, http.client.HTTPException)):
            ask(server.port, "GET", "/healthz")
        assert time.monotonic() - started < 2.0  # turned away, not queued behind the others
        for silent in held:
            silent.close()
        assert answers_within(server.port, 5.0), "a freed place was not given to the next client"
    finally:
        for silent in held:
            silent.close()
        server.close()


def test_held_places_free_themselves_at_the_deadline(monkeypatch):
    monkeypatch.setattr(health, "MAX_CONNECTIONS", 2)
    monkeypatch.setattr(health, "REQUEST_SECONDS", 0.5)
    server = HealthServer(("127.0.0.1", 0), healthy=lambda: (True, "ok"), metrics=bytes)
    server.start()
    held = [socket.create_connection(("127.0.0.1", server.port), timeout=5) for _ in range(2)]
    try:
        # Nobody closes the two silent connections: the listener drops them itself.
        assert answers_within(server.port, 5.0), "two silent clients shut everyone else out"
    finally:
        for silent in held:
            silent.close()
        server.close()


def test_a_client_that_never_reads_its_answer_does_not_hold_a_place(monkeypatch):
    monkeypatch.setattr(health, "MAX_CONNECTIONS", 1)
    monkeypatch.setattr(health, "REQUEST_SECONDS", 0.5)
    big = b"x" * (32 * 1024 * 1024)  # far more than the socket buffers hold
    server = HealthServer(("127.0.0.1", 0), healthy=lambda: (True, "ok"), metrics=lambda: big)
    server.start()
    deaf = socket.create_connection(("127.0.0.1", server.port), timeout=5)
    try:
        deaf.sendall(b"GET /metrics HTTP/1.1\r\nHost: x\r\n\r\n")  # and never reads
        assert answers_within(server.port, 5.0), "a client that does not read held the place"
    finally:
        deaf.close()
        server.close()
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest packages/follower/tests/test_health.py -q`
Expected: `5 failed, 34 passed`. The five: `test_every_answer_closes_its_connection` (no `Connection: close`), `test_a_client_that_trickles_bytes_is_dropped_at_the_deadline` (`AttributeError: ... has no attribute 'REQUEST_SECONDS'`), and the three that set `MAX_CONNECTIONS` (the same error for that name). `test_a_request_inside_the_deadline_is_answered_however_it_arrives` passes already: it guards a request that arrives in pieces against the change.

If the count of tests differs (the file had 33 test items when this plan was written), go by "six more than before, five of them failing".

- [ ] **Step 3: Write the implementation**

Replace the whole of `packages/follower/src/swarmscribe_follower/health.py` with:

```python
"""An optional HTTP listener for /healthz and /metrics (follower spec D18, section 9).

Off unless SWARMSCRIBE_FOLLOWER_HEALTH_ADDR is set: a follower opens no port by default. The
image sets it to loopback, for its own HEALTHCHECK; a chart sets the pod's address, for the
kubelet and Prometheus. It serves two paths, reads no request body, keeps no access log and
says nothing about the leader, a job or a recording.

/healthz says that the follower's threads are alive, not that the leader answers: a leader
outage must never make a supervisor kill a follower that is transcribing.

In a pod the listener is on the pod's address, where anything the network lets through can
reach it. So a connection is one request and at most REQUEST_SECONDS in all, however slowly
its bytes arrive, and at most MAX_CONNECTIONS are held at once: one more is closed unanswered.
A client can keep a place for those seconds and no longer, and never a thread for good; a
probe that is turned away is asked again by the kubelet, which gives up only after several."""

import io
import ipaddress
import logging
import os
import socket
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .errors import EXIT_CONFIGURATION, FollowerExit
from .metrics import CONTENT_TYPE

logger = logging.getLogger(__name__)

Healthy = Callable[[], tuple[bool, str]]
"""(alive, one plain line saying so or what has stopped)."""
PLAIN = "text/plain; charset=utf-8"
REQUEST_SECONDS = 5.0  # a connection, from accepted to answered; a probe takes milliseconds
MAX_CONNECTIONS = 8  # held at once: a kubelet and a few scrapers never come near it


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets a second socket share a port that is being listened on, so a
    # taken port would go unnoticed; there it is left off. Elsewhere it only skips TIME_WAIT.
    allow_reuse_address = os.name != "nt"

    def __init__(
        self,
        address: tuple[str, int],
        healthy: Healthy,
        metrics: Callable[[], bytes],
    ) -> None:
        try:
            ipv6 = isinstance(ipaddress.ip_address(address[0]), ipaddress.IPv6Address)
        except ValueError:
            ipv6 = False  # a name: IPv4, as http.server has always done
        if ipv6:
            self.address_family = socket.AF_INET6
        self.healthy, self.metrics = healthy, metrics
        self._slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        super().__init__(address, _Handler)

    def process_request(self, request, client_address) -> None:
        """One thread per connection, as ThreadingHTTPServer does, but never more than
        MAX_CONNECTIONS of them: a connection beyond that is closed without an answer."""
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()  # no thread was started: nothing else will give it back
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


class _UntilDeadline(io.RawIOBase):
    """The connection, readable until a moment that never moves: each read may wait only for
    what is left of the time, so bytes that trickle in do not buy more of it."""

    def __init__(self, connection: socket.socket, deadline: float) -> None:
        self._connection, self._deadline = connection, deadline

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        left = self._deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError("the request took too long")
        self._connection.settimeout(left)
        return self._connection.recv_into(buffer)


class _Handler(BaseHTTPRequestHandler):
    server_version = "swarmscribe-follower"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    server: "_Server"
    timeout = REQUEST_SECONDS  # until setup() has put the deadline in its place

    def setup(self) -> None:
        super().setup()
        self._deadline = time.monotonic() + REQUEST_SECONDS
        self.rfile.close()
        self.rfile = io.BufferedReader(_UntilDeadline(self.connection, self._deadline))

    def _answer(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")  # one request per connection
        self.end_headers()
        if self.command != "HEAD":
            # A client that does not read its answer is not waited for either.
            self.connection.settimeout(max(0.001, self._deadline - time.monotonic()))
            self.wfile.write(body)

    def _route(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            alive, text = self.server.healthy()
            self._answer(200 if alive else 503, (text + "\n").encode(), PLAIN)
        elif path == "/metrics":
            self._answer(200, self.server.metrics(), CONTENT_TYPE)
        else:
            self._answer(404, b"not found\n", PLAIN)

    do_GET = _route  # noqa: N815 (the names http.server looks for)
    do_HEAD = _route  # noqa: N815

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """No access log: a probe every few seconds is not news."""


class HealthServer:
    def __init__(
        self, address: tuple[str, int], *, healthy: Healthy, metrics: Callable[[], bytes]
    ) -> None:
        self._address, self._healthy, self._metrics = address, healthy, metrics
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        """The port it listens on (the one asked for, or the one the system chose for 0)."""
        if self._server is None:
            raise RuntimeError("the health listener has not been started")
        return self._server.server_address[1]

    def start(self) -> None:
        host, port = self._address
        try:
            server = _Server((host, port), self._healthy, self._metrics)
        except OSError as error:
            raise FollowerExit(
                EXIT_CONFIGURATION,
                f"cannot listen on {host}:{port} for /healthz and /metrics"
                f" ({error.strerror or type(error).__name__}); change or unset"
                " SWARMSCRIBE_FOLLOWER_HEALTH_ADDR",
            ) from None
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, name="health", daemon=True)
        self._thread.start()
        logger.info("listening on %s:%d for /healthz and /metrics", host, self.port)

    def close(self) -> None:
        server, self._server = self._server, None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
```

What changed, so that a reviewer can see it at a glance: the docstring's last paragraph; `import io` and `import time`; `REQUEST_SECONDS` and `MAX_CONNECTIONS`; `_Server._slots`, `process_request` and `process_request_thread`; the class `_UntilDeadline`; `_Handler.timeout`, `_Handler.setup`, and in `_answer` the `Connection: close` header and the `settimeout` before the body is written. `HealthServer` is untouched.

How it works: `http.server` reads the request through `self.rfile`. `setup()` replaces it with a reader whose every `recv` may wait only for what is left of one deadline, so the request line and the headers together have `REQUEST_SECONDS`; when the time is up the read raises `TimeoutError`, which `handle_one_request` already turns into closing the connection. `Connection: close` makes `http.server` end the connection after the one answer (its error answers, 404 through `_answer` and 501 through `send_error`, say it too). The semaphore is taken before the handler thread is started and given back when it ends.

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest packages/follower/tests/test_health.py -q`
Expected: `39 passed` (in about 15 s; the deadline tests wait for half-second deadlines).

- [ ] **Step 5: The whole suite and the linter**

Run: `uv run ruff check . && uv run pytest`
Expected: `All checks passed!`, then `3116 passed, 19 skipped, 5 deselected` (six more passing than `main`'s 3110; if `main` has moved, go by "six more than before") and no failure.

- [ ] **Step 6: Commit**

```bash
git add packages/follower/src/swarmscribe_follower/health.py packages/follower/tests/test_health.py
git commit -m "fix(follower): the health listener drops a slow client at a deadline and caps its connections

A handler's timeout was per read, so one byte every few seconds held a thread
for ever, and nothing bounded how many did. A connection is now one request
and five seconds in all, and at most eight are held at once. Needed before the
chart puts the listener on the pod's address.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The chart — a pool's Deployment, its values and its render check

**Files:**
- Create: `deploy/helm/swarmscribe-follower/Chart.yaml`
- Create: `deploy/helm/swarmscribe-follower/.helmignore`
- Create: `deploy/helm/swarmscribe-follower/values.yaml`
- Create: `deploy/helm/swarmscribe-follower/values.schema.json`
- Create: `deploy/helm/swarmscribe-follower/ci/test-values.yaml`
- Create: `deploy/helm/swarmscribe-follower/ci/check_render.py`
- Create: `deploy/helm/swarmscribe-follower/templates/_helpers.tpl`
- Create: `deploy/helm/swarmscribe-follower/templates/serviceaccount.yaml`
- Create: `deploy/helm/swarmscribe-follower/templates/deployment.yaml`
- Create: `deploy/helm/swarmscribe-follower/templates/NOTES.txt`

**Interfaces:**
- Consumes: the follower image (`ENTRYPOINT ["/usr/bin/tini", "--", "swarmscribe-follower"]`, `CMD ["run"]`, user 10001, state folder `/var/lib/swarmscribe-follower`, `/scratch`, `/models`, port 9108) and its settings (`packages/follower/src/swarmscribe_follower/config.py`); Task 1's listener.
- Produces: named templates used by Task 3 — `swarmscribe-follower.fullname`, `.labels`, `.selectorLabels` (which include `app.kubernetes.io/component: follower`), `.leaderPort` (the TCP port of `leader.url`, as text), `.refusedV4`, `.refusedV6` (YAML lists of CIDRs), `.validate`. `ci/check_render.py` with sections `core`, `gpu`, `network` and the flag `--only`; this task makes `core` pass, Task 3 the other two. With release name `pool`, every object is named `pool-swarmscribe-follower`. `values.yaml` already holds the values Task 3's templates read (`podDisruptionBudget`, `networkPolicy`). F3b's values file and README use the value names defined here.

In every shell of this task, first (see "The tools on the Windows machine" in the Global Constraints):

```bash
export PATH="$TEMP/chart-tools/windows-amd64:$TEMP/chart-tools:$PATH"
```

- [ ] **Step 1: The chart's metadata, values and schema**

Create `deploy/helm/swarmscribe-follower/Chart.yaml`:

```yaml
apiVersion: v2
name: swarmscribe-follower
description: SwarmScribe followers - one pool of workers that transcribe recordings for a leader
type: application
version: 0.1.0
appVersion: "0.1.0"
kubeVersion: ">=1.27.0-0"
```

Create `deploy/helm/swarmscribe-follower/.helmignore`:

```
# Not part of the packaged chart: the values and the check that CI renders it with.
ci/
```

Create `deploy/helm/swarmscribe-follower/values.yaml`:

```yaml
# SwarmScribe followers: one release of this chart is one pool. The three things you must
# set are `image`, `leader.url` and `poolToken.existingSecret`.

# Names. fullnameOverride replaces <release>-swarmscribe-follower as the name of every object.
nameOverride: ""
fullnameOverride: ""

# Followers in the pool. Each takes one recording at a time; 0 parks the pool.
replicaCount: 1

# The images are not published: there is no working default. Build one with its model baked
# in (README, "Deploy a follower pool"), load or push it, then name it here. Both are required.
image:
  repository: ""
  tag: ""
  # "sha256:..." pins the image by digest and wins over the tag.
  digest: ""
  pullPolicy: IfNotPresent
imagePullSecrets: []

leader:
  # SWARMSCRIBE_LEADER_URL: the address followers reach the leader at, e.g.
  # https://leader.example.org. The leader's own file links are built from ITS
  # SWARMSCRIBE_PUBLIC_URL, which must be reachable from these pods too.
  url: ""
  # true accepts a plain http leader URL and plain http file links
  # (SWARMSCRIBE_FOLLOWER_ALLOW_HTTP=1): a test cluster only.
  allowHttp: false
  # A leader whose TLS certificate comes from a private CA: a ConfigMap you created that
  # holds the CA certificates as PEM, trusted in addition to the public roots.
  ca:
    existingConfigMap: ""
    key: ca.pem

# The pool token (`swarmscribe-admin pool-tokens create --name ... --pool ...`), in a Secret
# you created. The chart never creates a Secret: a values file and Helm's release history
# are no place for it. It is mounted as a file and read only when a pod registers, so a
# changed Secret reaches the next registration without a restart.
poolToken:
  existingSecret: ""
  key: pool-token

# The pool name the followers report (SWARMSCRIBE_FOLLOWER_POOL). The token decides the real
# pool; keep the two the same.
pool: default

# A GPU pool: one GPU per pod (one follower uses one GPU, whole), the device set to cuda,
# and no surge pod on a rollout. Use a `cuda` image; set nodeSelector, tolerations and
# runtimeClassName below as your cluster's GPU nodes need.
gpu:
  enabled: false
  # The extended resource the device plugin advertises.
  resource: nvidia.com/gpu
runtimeClassName: ""

# Other follower settings, without the SWARMSCRIBE_FOLLOWER_ prefix, for example
#   ALLOWED_MODELS: "large-v3"
#   LOG_FORMAT: text
# Names are upper case (^[A-Z][A-Z0-9_]*$). The render refuses a name the chart sets itself
# (STATE_DIR, SCRATCH_DIR, MODEL_DIR, HEALTH_ADDR, ON_DRAINED, SHUTDOWN_GRACE_SECONDS,
# MEMORY_LIMIT_MB, ALLOW_HTTP, POOL, DEVICE).
settings: {}

# Extra environment for the follower container, as a list of {name, value|valueFrom}, for
# example HTTPS_PROXY. A token, the leader's URL and the names the chart sets are refused.
extraEnv: []

# The port of /healthz and /metrics, on the pod's own address. There is no Service: nothing
# connects to a follower but the kubelet and, if you let it (networkPolicy.ingress.from),
# your Prometheus.
healthPort: 9108

# Memory decides the longest recording a follower takes. A job needs about 3.6 GiB per hour
# of recording beside the model, and a recording that cannot fit under the limit is refused
# (failed `out_of_resources`), not killed half-way. The memory limit is passed to the follower
# as SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB. Size it as
#   what the model holds once loaded + 0.4 GiB + 3.6 GiB x hours of the longest recording
# (README, "Sizing a pool"). The default serves recordings of up to one hour with
# distil-large-v3 on a CPU or large-v3 on a GPU. Keep the memory request equal to the limit:
# a follower uses all of it for a few seconds of every long recording.
# On a CPU pool the CPU limit is also the number of threads (OMP_NUM_THREADS).
resources:
  requests:
    cpu: "2"
    memory: 6Gi
  limits:
    cpu: "4"
    memory: 6Gi

# Working files: the recording being transcribed and its outputs, on the node's disk, emptied
# after every job. As large as the largest recording.
scratch:
  sizeLimit: 20Gi

# Where models come from.
#   none                   baked into the image (--build-arg MODELS=...): the default
#   emptyDir               downloaded by every new pod (needs egress to huggingface.co)
#   persistentVolumeClaim  a claim you created, holding the cache or filled on first use
models:
  volume: none
  sizeLimit: 10Gi
  existingClaim: ""

# How long a pod may take to stop. A follower finishes the recording in hand when its
# estimated time left fits, and hands it back otherwise; it is told 30 s less than this
# (SWARMSCRIBE_FOLLOWER_SHUTDOWN_GRACE_SECONDS), so handing back fits inside it.
terminationGracePeriodSeconds: 900

metrics:
  # true adds prometheus.io/scrape, /port and /path annotations to the pods.
  scrapeAnnotations: false

podDisruptionBudget:
  # Off: an evicted follower hands its recording back and another redoes it, so a budget
  # protects nothing but compute and slows every node drain by up to the grace period per
  # pod. Rendered only when enabled and replicaCount is above 1.
  enabled: false
  maxUnavailable: 1

networkPolicy:
  # Needs a network plugin that enforces NetworkPolicy; without one this object is accepted
  # and does nothing. It allows by address and port only, never by name.
  enabled: true
  ingress:
    # Who may reach /healthz and /metrics (NetworkPolicyPeer objects), e.g. your Prometheus:
    #   - namespaceSelector: {matchLabels: {kubernetes.io/metadata.name: monitoring}}
    # Empty: nobody. The kubelet's probes come from the pod's own node, which a
    # NetworkPolicy never cuts off. /metrics has no authentication.
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
    https:
      # The leader, the storage its file links point at, and (unless the model is baked)
      # huggingface.co. The port of leader.url is always allowed as well. Peers are matched
      # AFTER Service address translation: for a leader inside the cluster the port that
      # counts is its pod's, so list that here if it differs from the Service's.
      ports: [443]
      cidrs: ["0.0.0.0/0", "::/0"]
      # More ranges to cut out of 0.0.0.0/0 and ::/0.
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
```

Create `deploy/helm/swarmscribe-follower/values.schema.json`:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "global": {"type": "object"},
    "nameOverride": {"type": "string", "pattern": "^([a-z0-9]([-a-z0-9]*[a-z0-9])?)?$", "maxLength": 63},
    "fullnameOverride": {"type": "string", "pattern": "^([a-z0-9]([-a-z0-9]*[a-z0-9])?)?$"},
    "replicaCount": {"type": "integer", "minimum": 0},
    "image": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "repository": {"type": "string"},
        "tag": {"type": "string"},
        "digest": {"type": "string", "pattern": "^(sha256:[a-f0-9]{64})?$"},
        "pullPolicy": {"enum": ["Always", "IfNotPresent", "Never"]}
      }
    },
    "imagePullSecrets": {"type": "array"},
    "leader": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "url": {"type": "string"},
        "allowHttp": {"type": "boolean"},
        "ca": {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "existingConfigMap": {"type": "string"},
            "key": {"type": "string", "pattern": "^[A-Za-z0-9._-]+$"}
          }
        }
      }
    },
    "poolToken": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "existingSecret": {"type": "string"},
        "key": {"type": "string", "pattern": "^[A-Za-z0-9._-]+$"}
      }
    },
    "pool": {"type": "string", "minLength": 1, "maxLength": 100},
    "gpu": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "enabled": {"type": "boolean"},
        "resource": {"type": "string", "pattern": "^[a-z0-9]([a-z0-9.-]*[a-z0-9])?/[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$"}
      }
    },
    "runtimeClassName": {"type": "string"},
    "settings": {
      "type": "object",
      "additionalProperties": {"type": ["string", "number", "boolean"]}
    },
    "extraEnv": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["name"],
        "properties": {"name": {"type": "string", "minLength": 1}}
      }
    },
    "healthPort": {"type": "integer", "minimum": 1024, "maximum": 65535},
    "resources": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "requests": {"type": "object"},
        "limits": {"type": "object"}
      }
    },
    "scratch": {
      "type": "object",
      "additionalProperties": false,
      "properties": {"sizeLimit": {"type": "string", "minLength": 1}}
    },
    "models": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "volume": {"enum": ["none", "emptyDir", "persistentVolumeClaim"]},
        "sizeLimit": {"type": "string", "minLength": 1},
        "existingClaim": {"type": "string"}
      }
    },
    "terminationGracePeriodSeconds": {"type": "integer", "minimum": 30},
    "metrics": {
      "type": "object",
      "additionalProperties": false,
      "properties": {"scrapeAnnotations": {"type": "boolean"}}
    },
    "podDisruptionBudget": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "enabled": {"type": "boolean"},
        "maxUnavailable": {"type": ["integer", "string"], "minimum": 1}
      }
    },
    "networkPolicy": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "enabled": {"type": "boolean"},
        "ingress": {
          "type": "object",
          "additionalProperties": false,
          "properties": {"from": {"type": "array"}}
        },
        "egress": {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "dns": {
              "type": "object",
              "additionalProperties": false,
              "properties": {"peers": {"type": "array", "minItems": 1}}
            },
            "https": {
              "type": "object",
              "additionalProperties": false,
              "properties": {
                "ports": {
                  "type": "array",
                  "items": {"type": "integer", "minimum": 1, "maximum": 65535}
                },
                "cidrs": {"type": "array", "minItems": 1, "items": {"type": "string"}},
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
      "additionalProperties": false,
      "properties": {"create": {"type": "boolean"}, "name": {"type": "string"}}
    },
    "podAnnotations": {"type": "object"},
    "podLabels": {"type": "object"},
    "nodeSelector": {"type": "object"},
    "tolerations": {"type": "array"},
    "affinity": {"type": "object"}
  }
}
```

- [ ] **Step 2: Write the render check (the failing test)**

Create `deploy/helm/swarmscribe-follower/ci/test-values.yaml`:

```yaml
# Values the chart is linted and rendered with, locally and in CI. Nothing here is a real secret.
image:
  repository: swarmscribe-follower
  tag: cpu-distil-large-v3
leader:
  url: https://leader.example.org
  ca:
    existingConfigMap: leader-ca
poolToken:
  existingSecret: swarmscribe-pool-token
pool: cpu-pods
replicaCount: 2
settings:
  ALLOWED_MODELS: "distil-large-v3"
networkPolicy:
  ingress:
    from:
      - namespaceSelector:
          matchLabels:
            kubernetes.io/metadata.name: monitoring
  egress:
    https:
      extraExcept: ["10.96.0.0/12", "fd00:10:96::/112"]
```

Create `deploy/helm/swarmscribe-follower/ci/check_render.py`. It holds all three sections now; Task 3 makes `gpu` and `network` pass.

```python
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
```

- [ ] **Step 3: Run it to see it fail**

Run: `uv run --no-project --with pyyaml python deploy/helm/swarmscribe-follower/ci/check_render.py --only core`
Expected: a traceback ending in `FileNotFoundError: [Errno 2] No such file or directory: '...templates\\NOTES.txt'` (a chart without templates renders nothing, and the check then looks for the notes).

(`uv run python deploy/helm/swarmscribe-follower/ci/check_render.py ...` works too: PyYAML is in the workspace's environment.)

- [ ] **Step 4: Write the templates**

Create `deploy/helm/swarmscribe-follower/templates/_helpers.tpl`:

```
{{/* The app name label: the chart name, or nameOverride. */}}
{{- define "swarmscribe-follower.name" -}}
{{- regexReplaceAll "-+$" (default .Chart.Name .Values.nameOverride | trunc 63) "" }}
{{- end }}

{{/* The name of every object: at most 63 characters, the limit for a label value. A release
name has up to 53 characters and may hold dots, which an object name here may not. */}}
{{- define "swarmscribe-follower.fullname" -}}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if .Values.fullnameOverride }}
{{- regexReplaceAll "-+$" (.Values.fullnameOverride | trunc 63) "" }}
{{- else if contains $name .Release.Name }}
{{- regexReplaceAll "-+$" (.Release.Name | replace "." "-" | trunc 63) "" }}
{{- else }}
{{- regexReplaceAll "-+$" (printf "%s-%s" (.Release.Name | replace "." "-") $name | trunc 63) "" }}
{{- end }}
{{- end }}

{{- define "swarmscribe-follower.selectorLabels" -}}
app.kubernetes.io/name: {{ include "swarmscribe-follower.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: follower
{{- end }}

{{- define "swarmscribe-follower.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{ include "swarmscribe-follower.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "swarmscribe-follower.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "swarmscribe-follower.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{- define "swarmscribe-follower.image" -}}
{{- $repository := required "image.repository is required: the follower images are not published; build one and load it into the cluster (see the README)" .Values.image.repository }}
{{- if .Values.image.digest }}
{{- printf "%s@%s" $repository .Values.image.digest }}
{{- else }}
{{- printf "%s:%s" $repository (required "image.tag is required: the follower images are not published; build one and name its tag (see the README)" .Values.image.tag) }}
{{- end }}
{{- end }}

{{/* The TCP port of leader.url: the one it names, else 443 (https) or 80 (http). Fails the
render on a URL the follower itself would refuse. */}}
{{- define "swarmscribe-follower.leaderPort" -}}
{{- $url := required "leader.url is required: the address followers reach the leader at, e.g. https://leader.example.org" .Values.leader.url }}
{{- if not (regexMatch `^https?://(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?)(:[0-9]{1,5})?(/[A-Za-z0-9._~/-]*)?$` $url) }}
{{- fail (printf "leader.url must be http(s)://<host>[:port][/path] with no user, query, fragment or space (got %q)" $url) }}
{{- end }}
{{- if and (hasPrefix "http://" $url) (not .Values.leader.allowHttp) }}
{{- fail "leader.url must be https: the follower refuses a plain http leader unless leader.allowHttp is true (a test cluster only)" }}
{{- end }}
{{- $authority := regexFind `^https?://[^/]+` $url }}
{{- $port := regexFind `:[0-9]+$` $authority | trimPrefix ":" }}
{{- if $port }}
{{- $port }}
{{- else }}
{{- ternary "80" "443" (hasPrefix "http://" $url) }}
{{- end }}
{{- end }}

{{/* Fails the render on values that would deploy a pool that cannot start. */}}
{{- define "swarmscribe-follower.validate" -}}
{{- $_ := include "swarmscribe-follower.image" . }}
{{- $_ := include "swarmscribe-follower.leaderPort" . }}
{{- $_ := required "poolToken.existingSecret is required: the Secret holding the pool token (swarmscribe-admin pool-tokens create)" .Values.poolToken.existingSecret }}
{{- /* Names the chart sets itself. The follower reads its environment case-insensitively,
so every comparison is on the upper-cased name. */}}
{{- $owned := list "STATE_DIR" "SCRATCH_DIR" "MODEL_DIR" "HEALTH_ADDR" "ON_DRAINED" "SHUTDOWN_GRACE_SECONDS" "MEMORY_LIMIT_MB" "ALLOW_HTTP" "POOL" "DEVICE" }}
{{- $fixed := list "SWARMSCRIBE_LEADER_URL" "SWARMSCRIBE_JOIN_TOKEN" "SWARMSCRIBE_JOIN_TOKEN_FILE" "SWARMSCRIBE_LEADER_CA_FILE" "POD_IP" "OMP_NUM_THREADS" }}
{{- range $name, $_ := .Values.settings }}
{{- if not (regexMatch "^[A-Z][A-Z0-9_]*$" $name) }}
{{- fail (printf "settings key %q must match ^[A-Z][A-Z0-9_]*$ (an upper-case environment name without the SWARMSCRIBE_FOLLOWER_ prefix)" $name) }}
{{- end }}
{{- if has $name $owned }}
{{- fail (printf "settings.%s is set by the chart: use leader, pool, gpu, resources, healthPort or terminationGracePeriodSeconds" $name) }}
{{- end }}
{{- end }}
{{- range .Values.extraEnv }}
{{- $upper := upper (toString .name) }}
{{- if or (has $upper $fixed) (and (hasPrefix "SWARMSCRIBE_FOLLOWER_" $upper) (has (trimPrefix "SWARMSCRIBE_FOLLOWER_" $upper) $owned)) }}
{{- fail (printf "extraEnv %s collides with a variable the chart sets (and a token never goes in values: use poolToken.existingSecret)" .name) }}
{{- end }}
{{- end }}
{{- if and (eq .Values.models.volume "persistentVolumeClaim") (not .Values.models.existingClaim) }}
{{- fail "models.existingClaim is required with models.volume persistentVolumeClaim" }}
{{- end }}
{{- end }}

{{/* What the follower container may use: `resources`, and on a GPU pool exactly one GPU
(one follower uses one GPU, whole). */}}
{{- define "swarmscribe-follower.resources" -}}
{{- $resources := deepCopy .Values.resources }}
{{- if .Values.gpu.enabled }}
{{- $limits := deepCopy (default dict $resources.limits) }}
{{- $_ := set $limits .Values.gpu.resource 1 }}
{{- $_ := set $resources "limits" $limits }}
{{- end }}
{{- toYaml $resources }}
{{- end }}

{{/* fsGroup: the Secret's file is then readable by the follower's group and by nobody else. */}}
{{- define "swarmscribe-follower.podSecurityContext" -}}
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
fsGroup: 10001
fsGroupChangePolicy: OnRootMismatch
seccompProfile:
  type: RuntimeDefault
{{- end }}

{{- define "swarmscribe-follower.containerSecurityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: ["ALL"]
{{- end }}

{{/* Addresses no leader, storage service or model host lives at, cut out of 0.0.0.0/0: this
network, Alibaba Cloud metadata, loopback, the Azure platform address, link-local (cloud
metadata), multicast and reserved. A follower fetches the links its leader hands it; these are
the addresses such a link must never reach. The console chart cuts out the same. */}}
{{- define "swarmscribe-follower.refusedV4" -}}
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
{{- define "swarmscribe-follower.refusedV6" -}}
- ::/8
- 2001::/32
- 2002::/16
- fd00:ec2::254/128
- fe80::/10
- fec0::/10
- ff00::/8
{{- end }}
```

Create `deploy/helm/swarmscribe-follower/templates/serviceaccount.yaml`:

```yaml
{{- if .Values.serviceAccount.create }}
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {{ include "swarmscribe-follower.serviceAccountName" . }}
  labels:
    {{- include "swarmscribe-follower.labels" . | nindent 4 }}
# A follower never calls the Kubernetes API.
automountServiceAccountToken: false
{{- end }}
```

Create `deploy/helm/swarmscribe-follower/templates/deployment.yaml`:

```yaml
{{- include "swarmscribe-follower.validate" . }}
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "swarmscribe-follower.fullname" . }}
  labels:
    {{- include "swarmscribe-follower.labels" . | nindent 4 }}
spec:
  replicas: {{ .Values.replicaCount }}
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxUnavailable: 25%
      # A surge pod on a GPU pool would wait for a GPU that is not there.
      maxSurge: {{ ternary 0 "25%" .Values.gpu.enabled }}
  selector:
    matchLabels:
      {{- include "swarmscribe-follower.selectorLabels" . | nindent 6 }}
  template:
    metadata:
      labels:
        {{- include "swarmscribe-follower.labels" . | nindent 8 }}
        {{- with .Values.podLabels }}
        {{- toYaml . | nindent 8 }}
        {{- end }}
      {{- if or .Values.metrics.scrapeAnnotations .Values.podAnnotations }}
      annotations:
        {{- if .Values.metrics.scrapeAnnotations }}
        prometheus.io/scrape: "true"
        prometheus.io/port: {{ .Values.healthPort | toString | quote }}
        prometheus.io/path: /metrics
        {{- end }}
        {{- with .Values.podAnnotations }}
        {{- toYaml . | nindent 8 }}
        {{- end }}
      {{- end }}
    spec:
      serviceAccountName: {{ include "swarmscribe-follower.serviceAccountName" . }}
      automountServiceAccountToken: false
      enableServiceLinks: false
      terminationGracePeriodSeconds: {{ .Values.terminationGracePeriodSeconds }}
      {{- with .Values.runtimeClassName }}
      runtimeClassName: {{ . | quote }}
      {{- end }}
      {{- with .Values.imagePullSecrets }}
      imagePullSecrets:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      securityContext:
        {{- include "swarmscribe-follower.podSecurityContext" . | nindent 8 }}
      containers:
        - name: follower
          image: {{ include "swarmscribe-follower.image" . | quote }}
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          args: ["run"]
          ports:
            - name: health
              containerPort: {{ .Values.healthPort }}
              protocol: TCP
          env:
            - name: SWARMSCRIBE_LEADER_URL
              value: {{ .Values.leader.url | quote }}
            - name: SWARMSCRIBE_JOIN_TOKEN_FILE
              value: /run/secrets/swarmscribe/pool-token
            {{- if .Values.leader.ca.existingConfigMap }}
            - name: SWARMSCRIBE_LEADER_CA_FILE
              value: {{ printf "/etc/swarmscribe/leader-ca/%s" .Values.leader.ca.key | quote }}
            {{- end }}
            {{- if .Values.leader.allowHttp }}
            - name: SWARMSCRIBE_FOLLOWER_ALLOW_HTTP
              value: "1"
            {{- end }}
            - name: SWARMSCRIBE_FOLLOWER_POOL
              value: {{ .Values.pool | quote }}
            # A folder inside the mount, which the follower makes its own (0700). The mount
            # itself is root's, and a credential is never kept in a folder that is.
            - name: SWARMSCRIBE_FOLLOWER_STATE_DIR
              value: /var/lib/swarmscribe-follower/state
            # A Deployment restarts whatever exits, so a drained follower stays up instead.
            - name: SWARMSCRIBE_FOLLOWER_ON_DRAINED
              value: park
            - name: SWARMSCRIBE_FOLLOWER_SHUTDOWN_GRACE_SECONDS
              value: {{ sub (int .Values.terminationGracePeriodSeconds) 30 | toString | quote }}
            - name: POD_IP
              valueFrom:
                fieldRef:
                  fieldPath: status.podIP
            # The pod's own address, never loopback: the kubelet probes it from the node.
            # The brackets serve an IPv6 address and do no harm to an IPv4 one.
            - name: SWARMSCRIBE_FOLLOWER_HEALTH_ADDR
              value: "[$(POD_IP)]:{{ .Values.healthPort }}"
            - name: SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB
              valueFrom:
                resourceFieldRef:
                  containerName: follower
                  resource: limits.memory
                  divisor: 1Mi
            {{- if .Values.gpu.enabled }}
            - name: SWARMSCRIBE_FOLLOWER_DEVICE
              value: cuda
            {{- else }}
            - name: OMP_NUM_THREADS
              valueFrom:
                resourceFieldRef:
                  containerName: follower
                  resource: limits.cpu
                  divisor: "1"
            {{- end }}
            {{- range $name, $value := .Values.settings }}
            - name: SWARMSCRIBE_FOLLOWER_{{ $name }}
              value: {{ $value | toString | quote }}
            {{- end }}
            {{- with .Values.extraEnv }}
            {{- toYaml . | nindent 12 }}
            {{- end }}
          securityContext:
            {{- include "swarmscribe-follower.containerSecurityContext" . | nindent 12 }}
          # /healthz says the follower's threads are alive: from its first seconds, so also
          # while the model loads, and also while the leader is away. There is no readiness
          # probe: nothing routes to a follower.
          startupProbe:
            httpGet:
              path: /healthz
              port: health
            periodSeconds: 2
            timeoutSeconds: 3
            failureThreshold: 60
          livenessProbe:
            httpGet:
              path: /healthz
              port: health
            periodSeconds: 10
            timeoutSeconds: 3
            failureThreshold: 3
          resources:
            {{- include "swarmscribe-follower.resources" . | nindent 12 }}
          volumeMounts:
            - name: state
              mountPath: /var/lib/swarmscribe-follower
            - name: scratch
              mountPath: /scratch
            - name: pool-token
              mountPath: /run/secrets/swarmscribe
              readOnly: true
            {{- if ne .Values.models.volume "none" }}
            - name: models
              mountPath: /models
            {{- end }}
            {{- if .Values.leader.ca.existingConfigMap }}
            - name: leader-ca
              mountPath: /etc/swarmscribe/leader-ca
              readOnly: true
            {{- end }}
      volumes:
        # The credential: in memory, for as long as the pod. A restarted container finds it;
        # a new pod registers again with the pool token.
        - name: state
          emptyDir:
            medium: Memory
            sizeLimit: 16Mi
        - name: scratch
          emptyDir:
            sizeLimit: {{ .Values.scratch.sizeLimit | quote }}
        # The Secret as a volume, never a subPath: the kubelet then refreshes the file when
        # the Secret changes.
        - name: pool-token
          secret:
            secretName: {{ .Values.poolToken.existingSecret | quote }}
            defaultMode: 0440
            items:
              - key: {{ .Values.poolToken.key | quote }}
                path: pool-token
        {{- if eq .Values.models.volume "emptyDir" }}
        - name: models
          emptyDir:
            sizeLimit: {{ .Values.models.sizeLimit | quote }}
        {{- else if eq .Values.models.volume "persistentVolumeClaim" }}
        - name: models
          persistentVolumeClaim:
            claimName: {{ .Values.models.existingClaim | quote }}
        {{- end }}
        {{- if .Values.leader.ca.existingConfigMap }}
        - name: leader-ca
          configMap:
            name: {{ .Values.leader.ca.existingConfigMap | quote }}
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
```

Create `deploy/helm/swarmscribe-follower/templates/NOTES.txt`:

```
A SwarmScribe follower pool: {{ .Values.replicaCount }} follower(s) for {{ .Values.leader.url }}

  kubectl -n {{ .Release.Namespace }} get pods -l app.kubernetes.io/instance={{ .Release.Name }}
  kubectl -n {{ .Release.Namespace }} logs deploy/{{ include "swarmscribe-follower.fullname" . }}

A pod is Running and Ready as soon as the follower's threads are up. That it has loaded its
model and joined the leader is in its log (`registered`) and in the leader's follower list.

A pod that keeps restarting says why in the last line of its log:
  exit 2: a setting is wrong
  exit 3: no usable GPU, or the model is not in the image
  exit 4: the pool token was refused, or the follower was revoked

A drained follower stays Running and takes no work; delete the pod to replace it.
{{- if .Values.networkPolicy.enabled }}

A NetworkPolicy limits what the pods can reach and who can reach /metrics. It needs a
network plugin that enforces NetworkPolicy; check that yours does.
{{- end }}
```

- [ ] **Step 5: Lint, validate and check**

```bash
helm lint deploy/helm/swarmscribe-follower -f deploy/helm/swarmscribe-follower/ci/test-values.yaml --strict
helm template pool deploy/helm/swarmscribe-follower --namespace transcribe \
  -f deploy/helm/swarmscribe-follower/ci/test-values.yaml > "$TEMP/follower-cpu.yaml"
kubeconform -strict -summary -kubernetes-version 1.33.0 "$TEMP/follower-cpu.yaml"
uv run --no-project --with pyyaml python deploy/helm/swarmscribe-follower/ci/check_render.py --only core
```

Expected, in order:

```
==> Linting deploy/helm/swarmscribe-follower
[INFO] Chart.yaml: icon is recommended

1 chart(s) linted, 0 chart(s) failed
Summary: 2 resources found in 1 file - Valid: 2, Invalid: 0, Errors: 0, Skipped: 0
the rendered chart holds every required property (core)
```

(kubeconform fetches schemas from the network. The two resources are the ServiceAccount and the Deployment.)

Then see that the other two sections still fail, which is Task 3's starting point:

Run: `uv run --no-project --with pyyaml python deploy/helm/swarmscribe-follower/ci/check_render.py --only network`
Expected: exit status 1 and `expected one NetworkPolicy named pool-swarmscribe-follower, found 0`.

- [ ] **Step 6: See three refusals with your own eyes**

```bash
helm template pool deploy/helm/swarmscribe-follower
helm template pool deploy/helm/swarmscribe-follower -f deploy/helm/swarmscribe-follower/ci/test-values.yaml --set replicas=3
helm template pool deploy/helm/swarmscribe-follower -f deploy/helm/swarmscribe-follower/ci/test-values.yaml --set leader.url=http://leader.internal
```

Expected: each fails. The first names a required value (`image.repository is required: the follower images are not published; ...`); the second says `additional properties 'replicas' not allowed`; the third `leader.url must be https: the follower refuses a plain http leader unless leader.allowHttp is true (a test cluster only)`.

- [ ] **Step 7: The whole suite and the linter**

Run: `uv run ruff check . && uv run pytest`
Expected: `All checks passed!`, then the same count as at the end of Task 1 (`3116 passed, 19 skipped, 5 deselected`) and no failure. The check script is Python, so the gate applies; no test imports it yet (Task 3 adds tests that read the chart's files).

- [ ] **Step 8: Commit**

```bash
git add deploy/helm/swarmscribe-follower
git commit -m "feat(chart): swarmscribe-follower, one release per pool: the Deployment, its values and its render check

The pool token is a file from a Secret the operator made; the credential lives
in a memory-backed emptyDir, in a folder the follower makes its own; the
listener is on the pod's address for the kubelet, with no Service; a drained
follower parks; the memory limit is what the memory guard goes by.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The NetworkPolicy, the PodDisruptionBudget, GPU pools, and CI

**Files:**
- Create: `deploy/helm/swarmscribe-follower/templates/networkpolicy.yaml`
- Create: `deploy/helm/swarmscribe-follower/templates/pdb.yaml`
- Create: `deploy/helm/swarmscribe-follower/ci/gpu-values.yaml`
- Create: `packages/follower/tests/test_chart_files.py`
- Modify: `.github/workflows/ci.yml` (job `chart`)

**Interfaces:**
- Consumes (Task 2): the named templates `swarmscribe-follower.fullname`, `.labels`, `.selectorLabels`, `.leaderPort`, `.refusedV4`, `.refusedV6`; the values `networkPolicy.*`, `podDisruptionBudget.*`, `gpu.*`, `healthPort`, `replicaCount`; `ci/check_render.py` with its sections `gpu` and `network`, which this task makes pass; `ci/test-values.yaml`. `deployment.yaml` already renders a GPU pool from `gpu.enabled` (the device, the one GPU, no surge); this task adds the values that exercise it.
- Produces: the complete chart. `ci/gpu-values.yaml` is given after `ci/test-values.yaml` (`-f test-values.yaml -f gpu-values.yaml`). `packages/follower/tests/test_chart_files.py` ties the chart to `Settings`, the Dockerfile and the memory guard's figures, without Helm.

In every shell of this task, first:

```bash
export PATH="$TEMP/chart-tools/windows-amd64:$TEMP/chart-tools:$PATH"
```

- [ ] **Step 1: The GPU pool's values, and see the two sections fail**

Create `deploy/helm/swarmscribe-follower/ci/gpu-values.yaml`:

```yaml
# A GPU pool, given after test-values.yaml: what the `gpu` checks render the chart with.
image:
  repository: swarmscribe-follower
  tag: cuda-large-v3
pool: gpu-pods
replicaCount: 4
gpu:
  enabled: true
runtimeClassName: nvidia
nodeSelector:
  nvidia.com/gpu.present: "true"
tolerations:
  - key: nvidia.com/gpu
    operator: Exists
    effect: NoSchedule
resources:
  requests:
    cpu: "2"
    memory: 8Gi
  limits:
    memory: 8Gi
podDisruptionBudget:
  enabled: true
```

Run: `uv run --no-project --with pyyaml python deploy/helm/swarmscribe-follower/ci/check_render.py --only gpu,network`
Expected: exit status 1 and `expected one PodDisruptionBudget named pool-swarmscribe-follower, found 0`.

- [ ] **Step 2: The NetworkPolicy and the PodDisruptionBudget**

Create `deploy/helm/swarmscribe-follower/templates/networkpolicy.yaml`:

```yaml
{{- if .Values.networkPolicy.enabled }}
{{- $egress := .Values.networkPolicy.egress }}
{{- $v4 := include "swarmscribe-follower.refusedV4" . | fromYamlArray }}
{{- $v6 := include "swarmscribe-follower.refusedV6" . | fromYamlArray }}
{{- range $egress.https.extraExcept }}
{{- if contains ":" . }}
{{- $v6 = append $v6 . }}
{{- else }}
{{- $v4 = append $v4 . }}
{{- end }}
{{- end }}
{{- $ports := list (include "swarmscribe-follower.leaderPort" . | int) }}
{{- range $egress.https.ports }}
{{- $ports = append $ports (int .) }}
{{- end }}
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: {{ include "swarmscribe-follower.fullname" . }}
  labels:
    {{- include "swarmscribe-follower.labels" . | nindent 4 }}
spec:
  podSelector:
    matchLabels:
      {{- include "swarmscribe-follower.selectorLabels" . | nindent 6 }}
  policyTypes: ["Ingress", "Egress"]
  # /healthz and /metrics, and only for the peers named in values. The kubelet's probes come
  # from the pod's own node, which no NetworkPolicy cuts off.
  {{- if .Values.networkPolicy.ingress.from }}
  ingress:
    - ports:
        - protocol: TCP
          port: {{ .Values.healthPort }}
      from:
        {{- toYaml .Values.networkPolicy.ingress.from | nindent 8 }}
  {{- else }}
  ingress: []
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
    # The leader, the storage its links point at and the model host: the refused ranges are
    # cut out of "anywhere".
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
        {{- range $ports | uniq }}
        - protocol: TCP
          port: {{ . }}
        {{- end }}
    {{- with $egress.extra }}
    {{- toYaml . | nindent 4 }}
    {{- end }}
{{- end }}
```

Create `deploy/helm/swarmscribe-follower/templates/pdb.yaml`:

```yaml
{{- if and .Values.podDisruptionBudget.enabled (gt (int .Values.replicaCount) 1) }}
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: {{ include "swarmscribe-follower.fullname" . }}
  labels:
    {{- include "swarmscribe-follower.labels" . | nindent 4 }}
spec:
  maxUnavailable: {{ .Values.podDisruptionBudget.maxUnavailable }}
  # A pod that keeps restarting (revoked, or on a node without its GPU) must not hold up a
  # node drain (Kubernetes 1.27 and later).
  unhealthyPodEvictionPolicy: AlwaysAllow
  selector:
    matchLabels:
      {{- include "swarmscribe-follower.selectorLabels" . | nindent 6 }}
{{- end }}
```

- [ ] **Step 3: Lint, validate and check, as a CPU pool and as a GPU pool**

```bash
C=deploy/helm/swarmscribe-follower
helm lint $C -f $C/ci/test-values.yaml --strict
helm lint $C -f $C/ci/test-values.yaml -f $C/ci/gpu-values.yaml --strict
helm template pool $C --namespace transcribe -f $C/ci/test-values.yaml > "$TEMP/follower-cpu.yaml"
helm template pool $C --namespace transcribe -f $C/ci/test-values.yaml -f $C/ci/gpu-values.yaml > "$TEMP/follower-gpu.yaml"
kubeconform -strict -summary -kubernetes-version 1.33.0 "$TEMP/follower-cpu.yaml" "$TEMP/follower-gpu.yaml"
uv run --no-project --with pyyaml python $C/ci/check_render.py
```

Expected: `1 chart(s) linted, 0 chart(s) failed` twice, then

```
Summary: 7 resources found in 2 files - Valid: 7, Invalid: 0, Errors: 0, Skipped: 0
the rendered chart holds every required property (core, gpu, network)
```

(Seven: a NetworkPolicy, a ServiceAccount and a Deployment for each pool, and the GPU pool's PodDisruptionBudget. The check takes about 7 s: it renders the chart many times over.)

- [ ] **Step 4: See that the check can fail**

A check that passes at once has proved nothing yet. Break the chart in three places, one at a time, run the check, and put each line back:

1. In `templates/_helpers.tpl`, change `- 169.254.0.0/16` to `- 169.254.1.0/24`. Expected from `check_render.py`: `FAILED: NetworkPolicy: 169.254.169.254 is reachable` (and the same for `169.254.170.2`).
2. In `templates/networkpolicy.yaml`, change `  ingress: []` to `  ingress: [{}]`. Expected: `FAILED: NetworkPolicy: without peers, ingress is not closed to everyone`.
3. In `templates/deployment.yaml`, change `maxSurge: {{ ternary 0 "25%" .Values.gpu.enabled }}` to `maxSurge: 25%`. Expected: `FAILED: GPU pool: a rollout may surge (the new pod would wait for a GPU)`.

Then `git status --short deploy/helm/swarmscribe-follower` must list only this task's three new files (`ci/gpu-values.yaml`, `templates/networkpolicy.yaml`, `templates/pdb.yaml`: nothing of Task 2's is left changed), and the check must pass again.

- [ ] **Step 5: Tie the chart to the follower, without Helm**

Create `packages/follower/tests/test_chart_files.py`:

```python
"""What the follower chart must agree on with the follower and its image, checked without
Helm (the rest is deploy/helm/swarmscribe-follower/ci/check_render.py, which needs Helm)."""

import re
from pathlib import Path

import pytest
from swarmscribe_follower.config import Settings
from swarmscribe_follower.memory import job_mb

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[3]
CHART = ROOT / "deploy" / "helm" / "swarmscribe-follower"
DEPLOYMENT = (CHART / "templates" / "deployment.yaml").read_text(encoding="utf-8")
HELPERS = (CHART / "templates" / "_helpers.tpl").read_text(encoding="utf-8")
DOCKERFILE = (ROOT / "docker" / "follower.Dockerfile").read_text(encoding="utf-8")
UNPREFIXED = {
    "SWARMSCRIBE_LEADER_URL",
    "SWARMSCRIBE_JOIN_TOKEN_FILE",
    "SWARMSCRIBE_LEADER_CA_FILE",
}
OWN_SETTING = re.compile(r"- name: SWARMSCRIBE_FOLLOWER_([A-Z_]+)$", re.MULTILINE)
# Measured, in MiB (README, "Follower images" and "Sizing a pool").
DISTIL_LOADED, KEPT_AFTER_A_LONG_JOB = 1770, 300


def field_of(variable: str) -> str:
    if variable in UNPREFIXED:
        return variable.removeprefix("SWARMSCRIBE_").lower()
    return variable.removeprefix("SWARMSCRIBE_FOLLOWER_").lower()


def mebibytes(quantity: str) -> int:
    number, unit = re.fullmatch(r"(\d+)(Mi|Gi)", quantity).groups()
    return int(number) * (1024 if unit == "Gi" else 1)


def test_every_setting_the_chart_sets_is_a_setting_of_the_follower():
    # A renamed setting would otherwise be passed on and silently ignored (extra="ignore").
    set_by_chart = set(re.findall(r"- name: (SWARMSCRIBE_[A-Z_]+)$", DEPLOYMENT, re.MULTILINE))
    assert len(set_by_chart) >= 10
    for variable in sorted(set_by_chart):
        assert field_of(variable) in Settings.model_fields, variable


def test_every_setting_the_chart_keeps_for_itself_is_a_setting_of_the_follower():
    (owned,) = re.findall(r"\$owned := list ((?:\"[A-Z_]+\" ?)+)", HELPERS)
    names = re.findall(r'"([A-Z_]+)"', owned)
    assert len(names) == 10
    for name in names:
        assert name.lower() in Settings.model_fields, name
    for name in OWN_SETTING.findall(DEPLOYMENT):
        assert name in names, f"the chart sets {name} and lets `settings` set it again"


def test_the_state_folder_is_a_folder_inside_the_images_state_mount():
    (state,) = re.findall(r"SWARMSCRIBE_FOLLOWER_STATE_DIR=(\S+) \\", DOCKERFILE)
    assert f"mountPath: {state}\n" in DEPLOYMENT
    assert f"value: {state}/state\n" in DEPLOYMENT
    assert "mountPath: /scratch\n" in DEPLOYMENT and "mountPath: /models\n" in DEPLOYMENT


def test_the_chart_listens_on_the_images_port_by_default():
    (port,) = re.findall(r"SWARMSCRIBE_FOLLOWER_HEALTH_ADDR=127\.0\.0\.1:(\d+)", DOCKERFILE)
    values = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))
    assert values["healthPort"] == int(port)


def test_a_pod_address_in_brackets_is_an_address_the_follower_listens_on():
    for host in ("10.244.0.7", "fd00:10:244::7"):
        settings = Settings(leader_url="https://l.example.org", health_addr=f"[{host}]:9108")
        assert settings.health_address == (host, 9108)


def test_the_default_memory_serves_an_hour_with_the_cpu_default_model():
    values = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))
    limit = mebibytes(values["resources"]["limits"]["memory"])
    assert values["resources"]["requests"]["memory"] == values["resources"]["limits"]["memory"]
    # Also the second hour-long recording: the process keeps some of the first one's memory.
    assert DISTIL_LOADED + KEPT_AFTER_A_LONG_JOB + job_mb(3600, split=True) <= limit
    assert DISTIL_LOADED + job_mb(2 * 3600, split=False) > limit  # and it says no to two
```

Run: `uv run pytest packages/follower/tests/test_chart_files.py -q`
Expected: `6 passed`.

These fail when the follower and the chart drift apart: a setting renamed in `config.py` that the chart still passes (the follower ignores unknown variables, so nothing else would say so), a state folder moved in the Dockerfile, a default memory limit that no longer admits the hour it promises.

- [ ] **Step 6: CI**

In `.github/workflows/ci.yml`, at the end of the job `chart`, after the step `Check what the chart renders` (the file's last step), add:

```yaml
      - name: Lint the follower chart, as a CPU pool and as a GPU pool
        run: |
          helm lint deploy/helm/swarmscribe-follower \
            -f deploy/helm/swarmscribe-follower/ci/test-values.yaml --strict
          helm lint deploy/helm/swarmscribe-follower \
            -f deploy/helm/swarmscribe-follower/ci/test-values.yaml \
            -f deploy/helm/swarmscribe-follower/ci/gpu-values.yaml --strict
      - name: Validate the follower chart's manifests against the Kubernetes schemas
        run: |
          helm template pool deploy/helm/swarmscribe-follower --namespace transcribe \
            -f deploy/helm/swarmscribe-follower/ci/test-values.yaml > "$RUNNER_TEMP/follower-cpu.yaml"
          helm template pool deploy/helm/swarmscribe-follower --namespace transcribe \
            -f deploy/helm/swarmscribe-follower/ci/test-values.yaml \
            -f deploy/helm/swarmscribe-follower/ci/gpu-values.yaml > "$RUNNER_TEMP/follower-gpu.yaml"
          kubeconform -strict -summary -kubernetes-version 1.33.0 \
            "$RUNNER_TEMP/follower-cpu.yaml" "$RUNNER_TEMP/follower-gpu.yaml"
      - name: Check what the follower chart renders
        run: >-
          uv run --no-project --with pyyaml
          python deploy/helm/swarmscribe-follower/ci/check_render.py
```

and, in the same job, rename two of the existing steps so that the log says which chart a step is about: `Validate the rendered manifests against the Kubernetes schemas` becomes `Validate the console chart's manifests against the Kubernetes schemas`, and `Check what the chart renders` becomes `Check what the console chart renders` (`Lint the console chart` already says so).

Run: `uv run python -c "import yaml; steps = yaml.safe_load(open('.github/workflows/ci.yml'))['jobs']['chart']['steps']; print(len(steps)); print([s.get('name') for s in steps][-3:])"`
Expected:

```
9
['Lint the follower chart, as a CPU pool and as a GPU pool', "Validate the follower chart's manifests against the Kubernetes schemas", 'Check what the follower chart renders']
```

These steps were run on the Windows machine with the same versions of Helm and kubeconform (Step 3); on GitHub they run for the first time when the branch is pushed.

- [ ] **Step 7: The whole suite and the linter**

Run: `uv run ruff check . && uv run pytest`
Expected: `All checks passed!`, then `3122 passed, 19 skipped, 5 deselected` (six more passing than after Task 1) and no failure.

- [ ] **Step 8: Commit**

```bash
git add deploy/helm/swarmscribe-follower packages/follower/tests/test_chart_files.py .github/workflows/ci.yml
git commit -m "feat(chart): the follower pool's NetworkPolicy, GPU pools and an optional PodDisruptionBudget; CI checks the chart

Nobody reaches /metrics unless values name them; egress is the leader's port
and 443, minus the metadata and reserved ranges. A GPU pool is one GPU per pod
and never surges. CI lints, validates and checks both renders.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## For the owner

Not tasks; decisions, and what this plan could not prove.

- **Rulings marked (owner)** above: 3 (who reaches `/metrics`), 5 (the token only from an existing Secret), 9 (the default pod size, 6Gi).
- **The GPU path has never run on Kubernetes.** The `cuda` image runs on a GPU in Docker (F2), and the chart's GPU values render and validate; a pod with a GPU, the device plugin and a `RuntimeClass` have not been tried, because the only cluster here has no GPU. F3b's README says so. The first real GPU cluster is the test.
- **Pods are sized by their longest recording, and that is expensive**: 6Gi for an hour, 13Gi for three, on a GPU pool as well. The memory guard turns the out-of-memory kill into a refusal; the fix is in the engine (features computed in pieces), as F2b's plan said. Until then a pool for long recordings is a pool of large pods.
- **A pod is Ready before it has registered.** The spec has no readiness probe, on purpose, and `/healthz` is 200 while the model loads. `kubectl get pods` therefore cannot tell a follower that works from one that is still loading or cannot reach its leader; the leader's follower list can. A readiness signal ("registered and claiming") would need a second path on the listener and is not in the spec; say so if you want it.
- **The chart has no guide yet**: `values.yaml` documents every value, and F3b writes the README's section, after it has installed the chart.

## Self-Review

**Spec coverage.** D17 and 8.2 (a standalone chart, one release per pool, schema, render check in CI, kubeconform, no Secret, image by value, NetworkPolicy on): Tasks 2 and 3. 8.2's Deployment (rolling update, GPU pool, CPU pool with `OMP_NUM_THREADS`, the memory limit passed on, the three volumes, the token as a file, `park`, the grace period and the derived setting, probes, no readiness probe and no Service, no service-account token, read-only root): Task 2, checked by `check_core` and `check_gpu`. 8.2's NetworkPolicy: Task 3. 5.3 (the state `emptyDir` made the follower's own): Task 2. 5.7 and its amendment (the limit passed to the guard, a default that serves what it says): Tasks 2 and 3. D18 ("pod IP in the chart") and the carry-over that guards it: Tasks 1 and 2. Spec 10's "Lint, schema validation and a render check in CI": Task 3. The `kind` install, the README and the spec's amendments are F3b.

**The carry-overs.** 1 (slow clients, the cap): Task 1. 2 (`/metrics` exposure): ruling 3, Task 3. 3 (the state folder): ruling 4, measured, Task 2. 4 (memory after a long recording): measured, ruling 9, Tasks 2 and 3. 5 (stopping, draining, a budget): ruling 7, Tasks 2 and 3. 6 (GPU pools, exit 3): ruling 10, Tasks 2 and 3. 7 (the token, rotation): rulings 5 and 6, Task 2. 8 (the image by value): Task 2. 9 (a leader URL and a CA bundle): Task 2 (`leader.url`, `leader.ca`).

**Where this plan differs from the spec**, each to be written into the spec by F3b's last task: `poolToken.existingSecret` for `secrets.existingSecret`; a two-minute startup probe for a thirty-minute one; a surge on CPU pools; ingress for nobody by default rather than "from the monitoring namespace"; 0.4 GiB added to the sizing; the listener's deadline and cap.

**Not covered here:** installing the chart (F3b); a GPU in a pod (nowhere); the CI steps on a GitHub runner (run locally with the same versions of Helm and kubeconform; they run on GitHub when the branch is pushed).

**Placeholders.** None.

**Names.** `REQUEST_SECONDS`, `MAX_CONNECTIONS` (Task 1) are what the tests patch. The named templates `swarmscribe-follower.fullname`, `.labels`, `.selectorLabels`, `.leaderPort`, `.refusedV4`, `.refusedV6`, `.validate`, `.resources`, `.image`, `.podSecurityContext`, `.containerSecurityContext`, `.serviceAccountName` (Task 2) are the ones `deployment.yaml`, `serviceaccount.yaml`, `networkpolicy.yaml` and `pdb.yaml` include. The value names in `values.yaml`, `values.schema.json`, `ci/test-values.yaml`, `ci/gpu-values.yaml` and `check_render.py`'s `--set` lines are the same (`poolToken.existingSecret`, `leader.allowHttp`, `leader.ca.existingConfigMap`, `models.volume`, `models.existingClaim`, `networkPolicy.ingress.from`, `networkPolicy.egress.https.*`, `gpu.enabled`, `gpu.resource`, `healthPort`, `terminationGracePeriodSeconds`, `metrics.scrapeAnnotations`, `podDisruptionBudget.enabled`). `check_render.py`'s `OWNED` and `FIXED` are the two lists in `_helpers.tpl`'s `validate`, and `test_chart_files.py` reads the template's own list.
