# Leader chart: questions for the owner

Five decisions the code cannot make. Each has a recommendation, which is what the spec
(`specs/2026-10-05-leader-chart-design.md`) and plans L1 to L3 are written to, and what
changes if you choose otherwise. Nothing else is open: where the code answered a question,
the spec gives the answer with its file and line.

## 1. The leader has no object storage. Ship a chart built on mounted volumes now?

The brief for this work assumed the leader stores recordings in object storage ("S3 and
whatever else is really supported") and that the chart would take its settings and a
credentials Secret. It does not: the only backend is a folder on a filesystem
(`packages/leader/src/swarmscribe_leader/storage/registry.py:12-22`). The specs plan Azure
Blob Storage and Google Cloud Storage under Plan B (roadmap item 5); they never mention S3.

So the chart as designed mounts volumes you provide (a PersistentVolumeClaim, or NFS), and
the `kind` test uses a real claim where the brief asked for a real S3-compatible store.

**Recommended: yes, ship it on volumes now.** It is what the leader can do today, it is what
the master spec describes for the local backend ("a shared volume in Kubernetes"), and
nothing in it has to be undone when a cloud backend arrives: the volume simply stops being
required.

*If you choose to wait for cloud storage:* L1 still goes ahead (the image and the readiness
fix stand on their own and are needed either way). L2 and L3 wait for Plan B, and the
leader stays undeployable on Kubernetes until then.

*If you want S3 specifically:* that is a third cloud backend to add to Plan B's scope. It is
not in any spec today, and it is a decision about the leader, not about this chart.

## 2. May `/readyz` stop waiting for the identity provider?

Today a leader is Ready only once every configured identity provider's signing keys have
been fetched (`api/health.py:26-27`). The leader spec asks for that (section 12), and a
follow-up already doubts it (`plans/2026-10-03-leader-admin-followups.md:11-13`). Under a
Deployment it means: an identity provider's outage, plus any reason for the pods to restart
(a node failure, an upgrade), takes the leader away from the followers, and transcription
stops until the provider is back. It also means a pod whose NetworkPolicy does not reach the
provider is never Ready.

**Recommended: yes.** `/readyz` answers for the database alone; an administrator's call
during an outage is already answered `503` with `Retry-After` by the admin API. This is what
the console does. The cost: a leader whose sign-in is misconfigured looks healthy until
somebody tries to sign in (risk K4; follow-up F8 is a way to see it without gating traffic).

*If you choose to keep it:* L1's Task 1 keeps the third check (its ruling 3 says exactly
what to restore), the chart's guide says a leader pod must reach its identity provider to
become Ready, and the readiness probe's timeout has to cover a slow provider (the README
says up to 20 seconds per provider today), which makes every database outage slower to see.

## 3. Should a leader be administrable without an identity provider?

There is no "first admin credential" to create: an administrator is whoever the role
mapping names, signed in through Entra ID or Google. That makes the first pool token depend
on a working identity provider, and a leader without one cannot be administered at all
except by calling its Python functions from inside the pod, which is what the tests do. The
console has a database-side command for its own bootstrap
(`swarmscribe-console admins add`); the leader has none.

**Recommended: no, not in this work.** The chart refuses to render a leader without sign-in
(unless `oidc.allowNone: true`, for tests), which keeps the one path there is honest. Anyone
who can run a command in the leader's pod already holds the database URL and the link key,
so a local command would add convenience, not a new power, but it would also add a second,
unaudited-by-person way to mint tokens, and that deserves its own small design.

*If you choose yes:* a new task in L1 adds `swarmscribe-leader pool-tokens create` (and
perhaps `consoles create`) working on the database directly, with tests and an audit entry
whose actor says it was local; the `kind` test uses it in place of its in-pod script; the
guide gains a "without an identity provider" section; and `oidc.allowNone` stops being
"tests only".

## 4. Should the `kind` install be a required CI job?

The console's and the follower's charts are installed on `kind` by hand and the result
recorded; both plans ruled a CI job out for now (C4b ruling 11; F3b ruling 2). The brief for
this work asks for a CI job, and L3 writes one (`leader-kind-e2e`): it builds three images,
creates a cluster, installs two charts, transcribes, and upgrades the leader under load.
The planner could not run it; its length on a GitHub runner is unknown (a guess from the
follower's Compose job and the follower's `kind` run: 12 to 20 minutes).

**Recommended: yes, for this chart.** What it proves, a rolling upgrade through a migration
with no failed request, is exactly what a later change to the leader's readiness, its stop
behaviour or the chart's rollout would break silently; no other test would notice. The risk
is a slow or flaky required job (risk K5); L3's ruling 10 says to delete it rather than
retry it if that happens.

*If you choose "by hand, recorded", as for the other two:* L3's Task 3 is dropped, L1's
smaller job `leader-image` (build and check the image, about three minutes) stays, and the
outcomes document is the only proof, to be repeated when the leader's readiness or the
chart's Deployment changes.

## 5. The chart cannot verify shared storage. Is "stated, not tested" enough for a first release?

Two or more leader replicas are safe only if every replica sees the same files. A chart
cannot see a claim's access mode at render time, and the `kind` test runs two replicas on
one node with a ReadWriteOnce claim, which works only because there is one node. So the
chart's default (two replicas) on a real cluster rests on a sentence in `values.yaml`, the
guide and the install notes (risk K1). Got wrong, the visible failure is a rollout that
hangs on "Multi-Attach error"; the invisible one, replicas with *different* volumes, would
have each mark the other's recordings missing.

**Recommended: accept it for the first release**, with three things already in the design:
`emptyDir` is refused; `updateStrategy: Recreate` with one replica is offered and its cost
stated; and the guide's table says which volume goes with which settings. Then do follow-up
F3 next: a start-up check in the leader (a marker file one replica writes and the others
look for) turns the invisible failure into a refusal to scan.

*If you want it tested before release:* L3 gains a task that runs an NFS server in the
`kind` cluster (a privileged pod; a second node so that the two replicas really are apart)
and repeats the scenario on a ReadWriteMany claim: perhaps half a day, and a heavier CI job.

*If you would rather the default were safe on any cluster:* default `replicaCount: 1` with
`updateStrategy: Recreate`, and make two replicas the documented step up once a shared
volume is in place. That contradicts the master spec's "2+ replicas" and makes every upgrade
of a default install an outage.

*A smaller question inside this one:* should the chart be able to create the
PersistentVolumeClaim itself (`storage.volumes[].create: {size, storageClass, accessMode}`)?
Recommended no: the recordings usually exist before the leader does, a claim made by a
release is deleted with it, and "the chart creates no storage" is easier to reason about.
