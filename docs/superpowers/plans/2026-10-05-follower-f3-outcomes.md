# Follower F3: the chart on a `kind` cluster

The follower spec (section 10) has the chart installed on a local `kind` cluster with the
`cpu` image. This is the record. CI lints the chart, validates what it renders and checks
what must hold (job `chart`); it never installs it. Repeat this run, and add a section here,
when the chart's Deployment or NetworkPolicy changes, or the follower's start-up, stop or
listener does.

How: `e2e/follower-kind/run_e2e.py` (`up`, `run`, `no-gpu`, `down`), with a throwaway leader
and Postgres in the cluster (`e2e/follower-kind/leader.yaml`) and the chart installed with
`e2e/follower-kind/values.yaml`: two followers, `tiny.en` baked in, limits of 2 CPUs and
2500Mi, a 60-second grace period, the leader's lease at 8 seconds.

## The planner's run, 2026-10-05

Machine: Windows 11, Docker Desktop (Engine 29.8.1, WSL 2, 16.6 GB for the VM), kind v0.30.0
(node image Kubernetes v1.34.0, containerd 2.1.3), kubectl v1.33.0, Helm v4.3.0. Built in a
scratch folder from `main` at `96ffd19` with this plan's files.

| What | Result |
|---|---|
| `up` | 1 min 50 s on a new cluster; 1 min 20 s on an existing one (`down`: 2 s). `kind load docker-image` failed for the pulled `postgres:16` (`content digest ... not found`); the fallback worked |
| `run` | passed four times in its final form, 1 min 43 s to 1 min 50 s. An earlier run failed on the driver's own mistake (it expected no restarts of the drained pod, which was the one killed before), corrected |
| Both pods Ready; both registered | 3 s; 3 to 4 s after `helm upgrade --install` |
| The state mount, the state folder, the credential, the token file | `0:10001 3777`, `10001:10001 2700`, `10001:10001 600`, `0:10001 440` |
| The listener | answers on the pod's address; loopback refuses; PID 1 is `tini` |
| Another pod asking a follower's `/metrics` | timed out, with the pods Ready (the kubelet's probes pass); after `networkPolicy.ingress.from` named the leader's pod: 200 from it, still closed to the Postgres pod, no pod restarted |
| A follower killed mid-job (`SIGKILL` to the process) | container restarted, exit 137; the same follower afterwards, no `registered` line; the recording `expired`, then `completed`, 2 attempts |
| A pod deleted mid-job | gone in 1.8 to 2.1 s of its 60 s; the recording `released`, then `completed`, 1 attempt |
| Scale to 1, then to 2 | the new pod took over the `gone` row; the leader's list did not grow |
| One hour of silence under the 2500Mi limit | failed three times: `out_of_resources: OutOfMemory: a recording of 60 minutes needs about 4121 MiB here and this follower may use 2500 MiB (SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB)` |
| A drained follower | `swarmscribe_follower_state{state="draining"} 1.0`; the pod stayed Running and was not restarted; the next recording went to the other; deleting the pod brought a new, active follower and the row stayed `draining` |
| The pool token revoked with its followers | both pods `CrashLoopBackOff`, exit 4, `stopping: this follower has been revoked`; a new token in the Secret and `kubectl rollout restart` brought two new followers in 6 s |
| `no-gpu` | passed twice, 1 min 23 s and 1 min 25 s: the `cuda` image exited 3 with `error: cuda was requested but no CUDA GPU is available`; the GPU pool's pod `Pending`: `0/1 nodes are available: 1 Insufficient nvidia.com/gpu` |
| A trickling client, from a pod the policy let in | dropped after 5.0 s; with 20 silent connections open a request was turned away in 0.01 s, and 6 s later answered 200; no restart |
| `models.volume: emptyDir` with an image without a model | downloaded `distil-large-v3` through the NetworkPolicy and registered 30 s after the pod started |

Memory of one follower in a pod (`/proc/<pid>/status`, every 3 s):

| | Held (RSS) | Peak so far |
|---|---|---|
| `tiny.en` loaded | 230 MiB | 282 MiB |
| after one hour mono; a second; one hour split; a third mono | 432; 460; 472; 442 MiB | 3784; 3899; 3899; 3899 MiB |
| `distil-large-v3` read from a cache; after half an hour mono; after a second | 1766; 1779; 1779 MiB | 1937; 3509; 3509 MiB |
| `distil-large-v3` just downloaded by the same process | 2257 MiB | 2429 MiB |

The two half-hour recordings ran under a limit of 3930Mi (1730 + 400 + 1800) and took 360 and
349 s on 4 CPUs; the cgroup's own count peaked at 3540 MiB. A `tiny.en` pod limited to 3990Mi
(230 + 100 + 3660 for one 61-minute recording, the sizing before F3) took the first such
recording and refused the second: `a recording of 61 minutes needs about 4134 MiB here and
this follower may use 3990 MiB`. The "hour" here is the 5-second fixture 720 times: 61 minutes.

## The run of Task 2 (plan F3b)

Run on 2026-10-05, twice, on the same machine and the same images. Versions, from the commands:
`cmd /c ver`: `Microsoft Windows [Version 10.0.26200.9457]`; `docker version --format
'{{.Server.Version}}'`: `29.8.1`; `kind version`: `kind v0.30.0 go1.24.6 windows/amd64`;
`helm version --short`: `v4.3.0+gbec5b06`; `kubectl version --client`: `Client Version: v1.33.0`;
`kubectl version` against the cluster: `Server Version: v1.34.0` (containerd 2.1.3). Images, built
from this branch (`docker images`): `swarmscribe-leader:f3-e2e 515MB`,
`swarmscribe-follower:f3-e2e 938MB` (`cpu`, with `tiny.en`), `swarmscribe-follower:f3-cuda 2.55GB`.

### First run

`up` and `run` passed (`run` once). `no-gpu` failed twice and passed on the third attempt. Its
failures were in the driver, not the chart: right after the `cuda` container's first exit,
`kubectl logs --previous` printed `unable to retrieve container logs for containerd://...` and
exited 0, and the driver took that for the pod's last line (`FAILED: its last line does not say
why: unable to retrieve container logs for containerd://...`). Looked at by hand, the same pod
then logged `error: cuda was requested but no CUDA GPU is available` for `--previous` and for
the current container. The driver now waits until the log is readable (commit `b1648e9`), and
the third attempt passed with it. Durations of this run were not captured verbatim (each about 2
minutes for `up` and `run`, about 1 1/2 minutes for each `no-gpu` attempt) and are not relied on.
While diagnosing, one Helm release (`wrong-node`) was installed by hand with the driver's
values and uninstalled again.

### Second run, with the fixed driver, timed with `time`

| Step | Result | `real` |
|---|---|---|
| `up` | `the cluster swarmscribe-follower-e2e is up, with a leader in namespace swarmscribe-e2e` | 1m51.442s |
| `run` | passed: two pods Ready in 3 s and registered in 3 s; a pod deleted mid-job gone in 2.1 s | 1m52.346s |
| `no-gpu` | passed the first time (the fix works) | 1m25.562s |
| `down` | `deleted the cluster swarmscribe-follower-e2e` | 2.171s |

`run`'s last line, whole: `passed (tiny.en on cpu): two pods were Ready in 3 s and registered in
3 s; a killed follower came back as itself and its recording was redone; a pod deleted mid-job
was gone in 2.1 s and counted no attempt; an hour was refused by the memory guard; a drained pod
parked; revoked pods exited 4 and a new token brought the pool back`.

`no-gpu`'s last line, whole: `passed: without a GPU the cuda image exits 3 and says `error: cuda
was requested but no CUDA GPU is available`; a pod of a GPU pool stays Pending (Insufficient
nvidia.com/gpu)`. The driver passes that line only after it has seen the container's
`lastState.terminated.exitCode` equal 3 and read the last line of `kubectl logs --previous`; the
pods are uninstalled when it ends, so they were not looked at afterwards.

After `down`, `kind get clusters` printed `No kind clusters found.`

The first run also looked at a pod by hand (step 5). Its output, from that run:
`tini` as PID 1; `0:10001 3777 /var/lib/swarmscribe-follower`,
`10001:10001 2700 /var/lib/swarmscribe-follower/state`,
`10001:10001 600 /var/lib/swarmscribe-follower/state/credential.json`,
`0:10001 2777 /scratch`, `0:10001 440 /run/secrets/swarmscribe/pool-token`; `doctor --no-model`
ended `memory: 2500 MiB may be used (SWARMSCRIBE_FOLLOWER_MEMORY_LIMIT_MB)`,
`leader: answers`, `joined: yes`, `result: ready`.

The planner's table above is not reproduced beyond what these lines state.

## What this does not prove

- **A GPU pool on Kubernetes.** The cluster has no GPU: the device plugin, a `RuntimeClass`
  and a follower transcribing on a GPU in a pod were not run. The `cuda` image itself was
  run on a GPU in Docker (the F2 outcomes).
- Any cluster but kind on one node; a network plugin other than kind's; an IPv6 or
  dual-stack cluster (only the setting's parsing of a bracketed address is tested).
- A leader on TLS, and one on a private CA (`leader.ca`): the leader here is plain http
  inside the cluster.
- `models.volume: persistentVolumeClaim`, `metrics.scrapeAnnotations` with a Prometheus,
  and a PodDisruptionBudget: rendered and schema-checked only.
- A real node drain (`kubectl drain`): a pod delete takes the same path through the kubelet.
- A recording of people talking: the long recordings are one phrase repeated.
- The chart's default size (6Gi) with `distil-large-v3` and an hour-long recording: the
  figures it rests on were measured (above, with half-hour recordings), the hour itself
  was not run in a pod. `large-v3` on a GPU was not measured in a pod at all.
