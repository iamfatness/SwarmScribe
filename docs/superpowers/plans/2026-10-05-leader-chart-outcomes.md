# Leader chart: the chart on a `kind` cluster, with the follower chart

The leader chart spec (section 11) has the chart installed on a `kind` cluster with a real
Postgres and a real volume claim, the follower chart joining it, and an upgrade with a
migration while requests flow. This is the record. CI repeats the scenario (job
`leader-kind-e2e`); the control run, the failure runs and the stops below are by hand.
Repeat it, and add a section here, when the leader's readiness, start-up or stop changes,
or the chart's Deployment, hook or NetworkPolicy does.

How: `e2e/leader-kind/run_e2e.py` (`up`, `run`, `down`), with the test's own Postgres, claim
and Secret (`postgres.yaml`), the leader chart with `leader-values.yaml` (two replicas, no
sign-in, plain http to the Service) and the follower chart with `follower-values.yaml` (two
followers, `tiny.en` baked in).

## The run of 2026-10-06

Machine: Windows 11 (`Microsoft Windows [Version 10.0.26200.9457]`), Docker Desktop (Engine
29.8.1, WSL 2, 16.6 GB and 28 CPUs for the VM), kind v0.30.0 (one node, image Kubernetes
v1.34.0, containerd 2.1.3, the network plugin kindnet, the storage class `standard` of
`rancher.io/local-path`), kubectl v1.33.0, Helm v4.3.0+gbec5b06. Built from the branch
`leader-chart-l3` at `0cc7d36` (the chart of pull request #24 with the leader of pull
request #26 merged in; neither was on `main`). Images: `swarmscribe-leader:kind` 341MB,
`swarmscribe-leader:kind-next` 341MB, `swarmscribe-follower:leader-kind` 938MB. C: had 160 GB
free before and after.

| Step | Result | `real` |
|---|---|---|
| `check-leader-image.sh` | `ok: swarmscribe-leader:kind holds the leader and swarmscribe-admin, without the engine, console or follower (340 MB)` | 1m20.184s |
| `up` (new cluster; then twice on the existing one) | `the cluster swarmscribe-leader-e2e is up, with Postgres and a volume claim in swarmscribe-e2e` | 1m51.263s; 1m56.227s, 1m56.396s |
| `run` (the plan's driver as written) | passed, first time | 1m48.050s |
| `run` (first of two in a row, final driver) | `passed (tiny.en on cpu): two leader pods were Ready in 11 s and two followers registered in 24 s; three recordings were transcribed once each and the one without consent never queued; the NetworkPolicy let followers in and nobody else, and let the leader reach only DNS and Postgres; an upgrade with a migration (hook 5 s, 44 s in all) kept the old pods Ready on the new schema for 30 s and never fewer than 2 ready address(es) behind the Service, failed none of 1026 requests and finished a recording in one attempt` | 2m12.885s |
| `run` (second in a row) | the same line with `hook 4 s` and `failed none of 1024 requests` | 2m12.664s |
| `down` | `deleted the cluster swarmscribe-leader-e2e` | 2.029s |

Runs before the two that passed: one, and it passed (the first line of `run` above, with
`hook 4 s, 44 s in all` and `1026 requests`). No run of the scenario failed on the fixed
leader. `kind load docker-image` worked for all four images; the driver's fallback was not
needed on this run.

What was changed to get there:

- `e2e/leader-kind/run_e2e.py`: `LONG_REPEATS` 144 to 240 (and the comment in
  `follower-values.yaml`). The plan's driver passed as written, but the followers' logs
  showed how little it had to spare: the twelve-minute recording was claimed at 14:29:41 and
  finished at 14:30:26, 45 seconds of work, and the scenario last checks that it is still
  leased about 38 seconds after it is claimed. Twenty minutes fits the followers' 2500Mi
  (1930 MiB by the driver's own test).
- `deploy/helm/swarmscribe-leader/ci/check_render.py`: it reads the `kind` test's values
  from their file (its copy `KIND_VALUES` is gone) and still compares what they render with
  whole objects; and it compares the notes without CRLF line ends. On a fresh Windows
  checkout (`core.autocrlf=true`) the check failed with `NOTES: not the expected text; lost
  [], new []`.
- `packages/leader/tests/test_leader_kind_driver.py`: one line over 100 characters, split.
- Nothing in the chart's templates or values, and nothing in the leader or the follower.

Seen by hand after the first pass:

- Pods: two leader pods of `swarmscribe-leader:kind-next`, 0 restarts; two followers and
  Postgres, 0 restarts.
- **The hook ran first.** Events of the install: the migration pod started at 14:28:50, `Job
  completed` at 14:28:52, the Deployment scaled up at 14:28:52, the leader pods were
  scheduled at 14:28:56 (four seconds waiting for the claim's volume, which `local-path`
  makes when its first pod is scheduled) and started at 14:28:57. Of the upgrade: migration
  pod started 14:29:43, `Job completed` 14:29:47, first new pod created 14:30:20 (after the
  30-second hold), the old pods stopped at 14:30:23 and 14:30:26.
- **`fsGroup` is not honoured by kind's `local-path`.** `/data` is `0:0 0o40777`: the
  provisioner makes a folder of the node that anybody may write, and the kubelet did not
  give it to group 10001. The leader ran as `10001:10001` with groups `[10001]`, and what it
  wrote is `10001:10001`, folders `0755`, files `0644`. So "`fsGroup` on a driver that
  honours it" was not run. The first mount took one second.
- The leader's memory, read from `/proc/11/status` in a pod some thirty seconds old (PID 1
  is tini, 1 MiB; PID 11 is the leader): `VmRSS` 98976 kB, `VmHWM` 99756 kB. The pod's
  cgroup: `memory.current` 78 MiB, `memory.peak` 120 MiB. Not a measurement under load.
- Events of type `Warning`: one, of the test's Postgres while it started (`Readiness probe
  failed: /var/run/postgresql:5432 - no response`). `kubectl top`: `error: Metrics API not
  available`, as expected.
- `helm history leader`: `1 superseded Install complete`, `2 deployed Upgrade complete`.
  `helm get values leader` holds the values file and the image tag; `helm get all leader`
  holds neither the link key nor the database URL (0 matches).

## The control run: the same scenario without the fix

`swarmscribe-leader:kind-control` is the image under test with `api/health.py` and `app.py`
as they were at `2fc774b` (`if revision != request.app.state.head_revision:` on line 24).

Result: steps 1 to 4 passed; then `FAILED: leader-swarmscribe-leader-7cbfc6bd5-ffwsq (the
old version) is not Ready on the migrated database: the upgrade would take every serving pod
away`. At that moment: leader pods 0/1 and 0/1 Ready; ready addresses behind the Service:
0; the prober's last line: `{"asked": 376, "failed": 6, "failures": ["15:06:06 GET
/v1/admin/login-config: URLError", "15:06:06 POST /v1/jobs/claim: URLError", ...]}` (every
request after the pods went unready fails; each waits out its five seconds, so the count
grows slowly).

So the scenario's step 5 fails on the defect and passes on the fix. Run once.

## The NetworkPolicy, and the hook under it

The scenario's step 4 answers whether kind's network plugin enforces a NetworkPolicy: it
does. A pod that is no peer met a timeout at a leader pod's 8080 and at the Service's 80; a
leader pod met a timeout at another pod's open port 8000; a follower's connection opened;
a leader pod reached `postgres:5432` by name (DNS, then Postgres). Three runs.

By hand, once, for the migration hook:

- **On a first install there is no policy yet when the hook runs.** A pre-install hook runs
  before the release's objects exist; after a failed first install the namespace held the
  failed Job and no NetworkPolicy (below). The hook reached Postgres with nothing in the
  way.
- **On an upgrade the previous revision's policy selects the hook's pod** (its
  `podSelector` is the release's `name` and `instance` labels, which the Job's pod carries,
  with `component: migrate`). The upgrade's migration reached Postgres under it in every
  run. A pod made by hand with the Job's three labels reached `postgres:5432` for the whole
  45 seconds it was watched, and met a timeout at another pod's open ports 5432 and 8000.
- **kindnet starts enforcing a moment after a pod starts.** That same pod's first two
  attempts, 0.01 and 0.26 seconds after its process started, *opened* a connection to the
  other pod's port 5432; from 2.5 seconds on every attempt timed out. A hook that connects
  in its first second can therefore reach an address the policy does not allow, on this
  plugin. Other plugins were not run.

## What Helm 4.3.0 does when the hook fails

Each run once, on the same cluster. Runs 1 to 3 in a namespace of their own
(`swarmscribe-e2e-fail`, with the test's Postgres and claim and at first no Secret), with
`--timeout 60s --set migrate.activeDeadlineSeconds=45` unless said. Runs 4 to 6 on the
scenario's own working install.

| # | Run | The pod | Helm's last line | `real` |
|---|---|---|---|---|
| 1 | first install, **no Secret** | `CreateContainerConfigError` for 45 s; event `Warning Failed ... Error: secret "leader" not found` | `Error: INSTALLATION FAILED: failed pre-install: resource Job/swarmscribe-e2e-fail/leader-swarmscribe-leader-migrate not ready. status: Failed, message: Job Failed. failed: 1/1` | 45.8 s |
| 2 | first install, **an image that is nowhere** (`image.tag=nope`, `pullPolicy: IfNotPresent`; the machine had a network) | `ErrImagePull` and `ImagePullBackOff` in turn; event `Failed to pull image "swarmscribe-leader:nope": failed to pull and unpack image "docker.io/library/swarmscribe-leader:nope": failed to resolve reference ...` | the same line as run 1 | 45.3 s |
| 3 | the same with the values file's `pullPolicy: Never`, and `--timeout 30s` (shorter than the Job's 45) | `ErrImageNeverPull`; event `Container image "swarmscribe-leader:nope" is not present with pull policy of Never` | `Error: INSTALLATION FAILED: failed pre-install: resource Job/... not ready. status: InProgress, message: Job in progress` and, on a line of its own, `context deadline exceeded` | 30.3 s |
| 4 | upgrade, **a wrong database password** (another Secret), default limits | four pods, each `Error`, exit status 2, started 0, 12, 32 and 72 s after the first | `Error: UPGRADE FAILED: pre-upgrade hooks failed: resource Job/swarmscribe-e2e/leader-swarmscribe-leader-migrate not ready. status: Failed, message: Job Failed. failed: 4/1` | 1m15.8s |
| 5 | upgrade, **a database address that never answers as Postgres** (another pod's open port), default limits | three pods, each `Error`, exit status 2 after 60 s (the driver's own connection timeout), started 0, 72 and 152 s after the first | `Error: UPGRADE FAILED: pre-upgrade hooks failed: resource Job/... not ready. status: InProgress, message: Job in progress` and `context deadline exceeded` | 5m0.3s |
| 6 | upgrade, **a migration that fails** (an image with one more migration: a `create table`, then `select 1/0`) | four pods, each `Error`, exit status 1 | the same line as run 4 | 1m15.0s |

What each left, and what got out:

- **A failed first install leaves a release in the state `failed` and the failed Job, and
  nothing else.** `helm list` (Helm 4 lists every state; it has no `--all`): `leader ...
  1 ... failed`. `helm status leader`: `STATUS: failed`, `DESCRIPTION: Release "leader"
  failed: failed pre-install: ...`. `kubectl get all,cm,networkpolicy,sa,pdb`: the Job
  (`Failed 0/1`), and nothing of the chart's besides: no ConfigMap, no Deployment, no
  Service, no NetworkPolicy, no ServiceAccount.
- **When the Job's deadline ends the wait, the pod is gone.** In runs 1 and 2 the Job
  controller deleted the waiting pod at the deadline: afterwards `kubectl describe pod -l
  app.kubernetes.io/component=migrate` printed `No resources found` and `kubectl logs
  job/...` printed `error: timed out waiting for the condition`. What still told the story
  was `kubectl get events` (`Warning Failed ... Error: secret "leader" not found`, then
  `Warning DeadlineExceeded ... Job was active longer than specified deadline`) and the
  Job's own condition (`Failed`, reason `DeadlineExceeded`). While the pod waits,
  `describe pod` shows the event.
- **After a failed first install, `helm upgrade` is accepted, with or without
  `--install`; `helm install` is refused.** With the Secret created: `helm install leader
  ...` printed `Error: INSTALLATION FAILED: release name check failed: cannot reuse a name
  that is still in use`; `helm upgrade leader ...` (after run 1) and `helm upgrade --install
  leader ...` (after runs 2 and 3) each printed `Release "leader" has been upgraded. Happy
  Helming!` and both pods became Ready. `helm history` then listed `1 superseded Release
  "leader" failed: failed pre-install: ...` and `2 deployed Upgrade complete`: the failed
  revision is kept, its state becomes `superseded` and its description stays.
  `helm uninstall` followed by `helm install` was not needed, and works too (next point).
- **The failed Job outlives `helm uninstall`, and the next install removes it.** After run
  1, `helm uninstall leader` printed `release "leader" uninstalled` and `kubectl get jobs`
  still listed `leader-swarmscribe-leader-migrate Failed`. The next `helm install` made a
  new Job (another UID, created at the install's own second): the hook's
  `before-hook-creation` policy deleted the old one, with its pods. A succeeding install or
  upgrade leaves no Job (`hook-succeeded`).
- **A failed upgrade changes nothing that serves.** After runs 4, 5 and 6 the two leader
  pods were the same pods, `1/1 Running`, 0 restarts; the followers stayed registered.
  `helm history leader` after them:

  ```
  1  superseded  Install complete
  2  deployed    Upgrade complete
  3  failed      Upgrade "leader" failed: pre-upgrade hooks failed: ... status: Failed, message: Job Failed. failed: 4/1
  4  failed      Upgrade "leader" failed: pre-upgrade hooks failed: ... status: InProgress, message: Job in progress c...
  5  failed      (the try with the log annotation, below)
  6  failed      Upgrade "leader" failed: pre-upgrade hooks failed: ... failed: 4/1
  ```

  Revision 2 stayed `deployed` through all four. The corrected `helm upgrade --install`
  went through in a few seconds as `7 deployed Upgrade complete`, with 5 and 6 still listed
  as `failed` above it (the earlier lines were not read again), and removed the failed Job
  and its pods.
- **Retries.** With the default `backoffLimit: 3` a failing migration made four pods in 75
  seconds (pauses of about 10, 20 and 40 seconds) before the Job failed and Helm returned.
  `kubectl logs job/leader-swarmscribe-leader-migrate` printed `Found 4 pods, using
  pod/...` and showed the first of them.
- **The log.** Run 4: `error: cannot connect to the database: DBAPIError:
  (sqlalchemy.dialects.postgresql.asyncpg.Error) password authentication failed for user
  "postgres"` and a second line `(Background on this error at: https://sqlalche.me/e/21/dbapi)`.
  Run 5: `error: cannot connect to the database: TimeoutError: `. Run 6: `error: the
  migration failed: DBAPIError: (sqlalchemy.dialects.***ql.asyncpg.Error) division by zero`,
  then `[SQL: select 1/0]` and the same `(Background ...)` line. No traceback and no password
  in any. (The `***ql` is the leader scrubbing the database password out of the message:
  the test's password is `postgres`, which is also part of the word `postgresql`.)
- **A failed migration is undone whole.** After run 6 the database was still at revision
  `9999` and the table the migration had created before it failed did not exist: the
  migration ran in one transaction. That is this one migration on Postgres; a migration
  that commits part-way was not run.
- **Which clock ends the wait.** With the defaults (the Job's `activeDeadlineSeconds` 300,
  Helm's `--timeout` 5 minutes), run 5: Helm's. It returned after 5m0.3s with `status:
  InProgress, message: Job in progress` and `context deadline exceeded`; the Job's own
  condition `DeadlineExceeded` is stamped the same second. Helm's clock starts first, so
  with equal limits Helm's message is the one seen. With the Job's deadline the shorter
  (runs 1 and 2): `status: Failed, message: Job Failed. failed: 1/1`. With Helm's the
  shorter (run 3): `Job in progress`, `context deadline exceeded`, and the Job went on
  waiting until its own deadline, 15 seconds after Helm had returned.
- **`helm.sh/hook-output-log-policy: hook-failed` works on Helm 4.3.0.** With that
  annotation added to the Job in a scratch copy of the chart (and `backoffLimit: 0`), the
  failing upgrade of run 4 printed the migration's own log before its error, in 3.4 s:

  ```
  level=INFO msg="error: cannot connect to the database: DBAPIError: (sqlalchemy.dialects.postgresql.asyncpg.Error) password authentication failed for user \"postgres\"\n(Background on this error at: https://sqlalche.me/e/21/dbapi)"
  level=WARN msg="upgrade failed" name=leader error="pre-upgrade hooks failed: resource Job/swarmscribe-e2e/leader-swarmscribe-leader-migrate not ready. status: Failed, message: Job Failed. failed: 1/1"
  Error: UPGRADE FAILED: pre-upgrade hooks failed: ...
  ```

  The chart does not carry the annotation: it is follow-up B29. Not tried: what it prints
  when several pods failed, or when the pod never started.
- Run 5 was meant to show a connection the NetworkPolicy drops. It did not: the hook's pods
  connected to the other pod in their first second, before kindnet enforced the policy (see
  above; the other pod's log shows Postgres' first bytes arriving), and each then waited 60
  seconds for an answer that never came. A database that silently drops the connection
  gives the same line and the same 60 seconds.

## Stops

By hand, once each, on the scenario's install after it had passed.

- **A leader pod deleted while it served a download.** The traffic to the follower pods was
  slowed to 4 Mbit/s on the node (`tc ... tbf` on the pods' interfaces) so that a 46 MB
  recording took long enough; the leader pod with the transfer was found by its socket's
  send queue and deleted at 10:42:55.07. The download went on for 15 seconds (5 of
  `preStop`, then the leader's 10 for requests in hand): the follower's file grew to
  17 498 112 bytes and stopped at 10:43:10. The pod's container ended at 14:43:10Z with
  **exit status 143**, `reason: Error`; the pod was gone a second later.
  **The follower then waited two minutes.** Nothing told it that the transfer had been cut:
  its file stayed at 17 498 112 bytes until 10:45:10, which is its own 120-second read
  timeout. Then it downloaded the recording again from the first byte (46 471 148 bytes by
  10:45:12, from the other pods, the slowing removed by then), transcribed it, and the job
  ended `completed`, **1 attempt, on the same lease**, the transcript holding both
  speakers' words. The follower logged nothing about the retry (it logs `job claimed` and
  `job completed` only). So: the retry works, and the job is not lost; but a transfer cut
  by a stop cost two minutes here, not seconds. The slowed link left 1.6 MB unsent in the
  leader's socket when it ended, and the pod's network was removed with it, so no end of
  connection reached the follower. Whether a cut on a fast link is noticed at once was not
  run. An upload was not cut: transcripts are a few kilobytes.
- **A rolling restart with a recording in hand** (`kubectl rollout restart`, the prober
  running): the rollout took 5.6 s and the old pods were gone after 11.9 s; the Service
  never had fewer than 2 ready addresses (26 samples); 0 of 366 requests failed; the
  recording was `leased` throughout and ended `completed` in 1 attempt. Both stopped
  containers ended with exit status 143.
- The scenario's own upgrade replaces both pods with a recording in hand in every run
  (above). Nothing asserts a stopped pod's exit status.

## The CI job

`leader-kind-e2e` replaces `leader-image`: it builds the leader image and runs
`check-leader-image.sh` on it, builds the next version's image and the follower's, then
`up`, `run` and `down`.

On the development machine the job's last three steps were run straight after each other
from built images on 2026-10-06 (`up && run; down`, a new cluster): `the cluster
swarmscribe-leader-e2e is up, ...`; `passed (tiny.en on cpu): ... (hook 5 s, 44 s in all)
... failed none of 1044 requests and finished a recording in one attempt`; `deleted the
cluster swarmscribe-leader-e2e`; 4m7.347s in all; `kind get clusters` then printed `No kind
clusters found.` That was the fourth pass of the scenario.

**The job has not run on a GitHub runner.** The workflow runs on pushes to `main` and on
pull requests only, so pushing this branch started nothing; it runs when the pull request is
opened. What may differ there: the runner's four slower cores (the recording in hand takes
longer, which is the safe direction; the scenario allows 420 seconds for it after the
rollout), kindnet's timing, and `kind load` on a Linux Docker (the driver's fallback is for
Docker Desktop's image store). If it proves flaky there it is to be removed, not retried
(owner's ruling 4).

## What this does not prove

- **Shared storage on several nodes.** One node, kind's `local-path` class (ReadWriteOnce):
  two leader pods shared the claim only because they shared the node. A ReadWriteMany
  volume, and leader pods on two nodes, were not run.
- **An Ingress, TLS, or an ingress controller's body limit and logging.** No ingress
  controller was installed. The followers reached the leader's Service over plain http
  inside the cluster.
- **Sign-in.** The leader had no identity provider (`oidc.allowNone`): `swarmscribe-admin`
  was not used, no token was verified, and the role mapping the chart renders was not
  exercised. The pool token was made by calling the leader's own function inside its pod.
- **A managed Postgres**, TLS to Postgres, or a Postgres outside the cluster: the database
  was a `postgres:16` pod of the test's own, keeping its data in the pod.
- **The console** in the same cluster.
- **A migration that changes the schema.** The upgrade's migration changed the revision
  only. Whether a real migration is compatible with the release before it is not tested by
  anything.
- **A kept-alive connection through a rollout.** Every request of the prober opened a new
  connection.
- **A network plugin other than kindnet**, an IPv6 or dual-stack cluster, a node drain, and
  the PodDisruptionBudget doing anything.
- **Load.** The leader's requests and limits were not measured beyond the one figure above.
- **A stop during a database outage, on the cluster.** The image's own check runs it (a
  leader on a silent database ends about 17 seconds after it is told to); a pod of the
  chart, with its `preStop` sleep and its grace period, was not stopped that way.
- **A transfer cut on a link that was not slowed, and an upload cut by a stop.**
- **Sign-in behind a blocked egress or a proxy**: Ready pods, and `503` from the admin API.
- **`fsGroup` on a driver that honours it** (kind's does not), on a shared volume, and how
  long a first mount of a large volume takes.
- **An ingress controller's annotations** (the guide's ingress-nginx example).
- **A failed hook with the chart's default limits on a first install**: the first-install
  failures were run with a 45-second deadline; the default limits were run on an upgrade
  (run 5).
- **A recording of people talking**: the recordings are one five-second fixture repeated.
- Each by-hand run above was made once. The scenario was run three times on the fixed
  leader and once on the control image.
