# SwarmScribe — Leader Helm Chart Design

Date: 2026-10-05
Status: draft for the owner's review. Built by plans L1 to L3 (section 15).
Roadmap item 6 ("Helm chart and autoscaling"): this is the chart; autoscaling is not in it.

The console and the follower each have an image, a chart and a guide. The leader is the one
part of SwarmScribe that cannot yet be deployed on Kubernetes. This document designs its
image, its chart, and the one change to the leader's own code that a rolling upgrade needs.

Every statement about what exists today names a file and a line, read on `main` at
`bc19dfd`. Section 13 says what the planner ran while writing this and what it did not.

## 1. What exists today

### 1.1 There is no leader image

The only Dockerfile that builds a leader is `e2e/compose/Dockerfile` (17 lines). Its first
line says what it is for: "The leader alone (no engine, no model), for the Compose
end-to-end test." It is not a deployable image:

- it runs as root (there is no `USER` line);
- it copies the whole repository into the image (`COPY . .`, line 13), tests and all, and
  installs the packages editable;
- the base image is `python:3.12-slim` by tag, not by digest (line 3), and `uv` is
  `>=0.5,<1` (line 5);
- it has no labels, no `HEALTHCHECK`, and nothing checks what it holds.

CI builds it three times, once in each of `compose-e2e`, `console-compose-e2e` and
`follower-compose-e2e` (`.github/workflows/ci.yml:40`, `:125`, `:151`); the follower
follow-ups note this (`plans/2026-10-04-follower-f1-followups.md:58`). The `kind` test of the
follower chart uses the same image and says so: "This is NOT how a leader is deployed"
(`e2e/follower-kind/leader.yaml:1-4`).

`docker/` holds `console.Dockerfile`, `follower.Dockerfile` and a check script for each.
There is no `docker/leader.Dockerfile`. Plan L1 adds it, with `docker/check-leader-image.sh`.

### 1.2 Settings

All of the leader's configuration is `SWARMSCRIBE_*` environment variables, read by one
class (`packages/leader/src/swarmscribe_leader/config.py:26-33`; unknown variables are
ignored, `extra="ignore"`).

| Variable (`SWARMSCRIBE_` +) | Default | Where | Notes |
|---|---|---|---|
| `DATABASE_URL` | required | `config.py:36` | secret; `postgresql://` is rewritten for asyncpg (`db/session.py:9-13`) |
| `PUBLIC_URL` | required | `config.py:37`, `:75-81` | absolute `http(s)` URL; every file link is built from it (`storage/local.py:198`) |
| `LINK_KEY` | required | `config.py:38`, `:83-88` | secret; at least 32 characters; signs file links (HMAC-SHA256, `storage/links.py:39-56`) |
| `LEASE_SECONDS` | 120 | `config.py:40` | |
| `HEARTBEAT_SECONDS` | 30 | `config.py:41` | must be shorter than the lease (`:162-166`) |
| `MAX_ATTEMPTS` | 3 | `config.py:42` | |
| `CLAIM_RETRY_AFTER` | 10 | `config.py:43` | |
| `REAPER_INTERVAL_SECONDS` | 15 | `config.py:44` | |
| `SCANNER_INTERVAL_SECONDS` | 30 | `config.py:45` | |
| `FOLLOWER_GONE_AFTER_SECONDS` | 600 | `config.py:46` | must be longer than the lease (`:168-172`) |
| `DOWNLOAD_LINK_TTL_SECONDS` | 1800 | `config.py:47` | |
| `UPLOAD_LINK_TTL_SECONDS` | 7200 | `config.py:48` | |
| `LINKS_REFRESH_MIN_SECONDS` | 60 | `config.py:50` | |
| `ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID` | unset | `config.py:53-54` | set together (`:176-177`); the tenant is a GUID (`:138-148`) |
| `ENTRA_CLIENT_SECRET` | unset | `config.py:55` | secret; only for Microsoft Graph on group overage |
| `GOOGLE_CLIENT_ID` | unset | `config.py:56` | |
| `GOOGLE_CLIENT_SECRET` | unset | `config.py:57` | secret; required with the client id (`:180-184`) |
| `GOOGLE_HOSTED_DOMAIN` | unset | `config.py:58` | |
| `GOOGLE_SERVICE_ACCOUNT` | unset | `config.py:59`, `:205-223` | secret; the JSON key itself, or the path of a file holding it |
| `ROLE_CACHE_SECONDS` | 300 | `config.py:60` | |
| `ROLE_{VIEWER,OPERATOR,ADMIN}_{ENTRA_GROUPS,GOOGLE_GROUPS,EMAILS,DOMAINS}` | empty | `config.py:62-73` | twelve comma-separated lists; which provider each needs is checked at `:189-200` |

The listening address is not a setting: it is `serve --host --port`, default
`0.0.0.0:8080` (`main.py:71-72`). There is no setting for the database pool: the engine is
made with SQLAlchemy's defaults (`db/session.py:16-17`), which are 5 connections and 10 of
overflow, so 15 per replica at most.

`migrate` reads the same settings as `serve` (`main.py:74-81`): a migration Job must be
given the public URL and the link key too, or it exits 2 with "invalid configuration".

### 1.3 The process

- `swarmscribe-leader serve` first compares the database's revision with its own head and
  refuses to start (exit 2) when the database is unreachable, behind, without a schema, or
  ahead (`main.py:30-50`, `:83-86`). Only then does it start uvicorn, with
  `proxy_headers=True` and `access_log=False` — "file-link URLs contain signed tokens and
  must never be logged" (`main.py:93-102`).
- The app (`app.py:25-102`) serves four routers: the probes (`api/health.py`), file links
  under `/v1/files` (`api/files.py:21`), the follower API under `/v1`
  (`api/follower.py:46`) and the admin API under `/v1/admin` (`api/admin.py:55`). It
  serves no documentation routes (`app.py:85-87`). Everything outside the probes is under
  `/v1`.
- JSON request bodies are limited to 1 MiB (`api/body_limit.py:7`); an upload through a
  file link to 512 MiB (`api/files.py:23`).

### 1.4 Health and readiness, and the defect

`GET /healthz` answers `200 {"status": "ok"}` and touches nothing (`api/health.py:10-12`).

`GET /readyz` (`api/health.py:15-28`) answers 200 only when all three hold:

1. the database answers `select 1` and the revision can be read (`:18-23`);
2. the database's revision **equals** this leader's head (`:24`);
3. every configured identity provider's signing keys have been fetched at least once
   (`:26-27`, through `auth/oidc.py:195-201` and `:215-218`).

Two things are wrong with that once the leader runs under a Deployment.

**(a) A pre-upgrade migration takes every serving pod out of service.** The migration is to
run as a pre-upgrade hook (master spec section 12, line 346). It finishes before any new pod
exists. From that moment the database's revision is the new one, the old pods' head is the
old one, the comparison at `health.py:24` fails, and after three failed probes every old pod
is unready at once. The Service has no endpoints until the first new pod is Ready. With
`maxUnavailable: 0` the old pods are still running, and nothing reaches them.

The console had the same defect and fixed it in C4a: "`/readyz` is ready when the database's
schema is this console's *or newer*" (`plans/2026-10-04-fleet-console-c4a-image-and-compose.md:39`;
`packages/console/src/swarmscribe_console/api/health.py:85-93`). The leader was not changed
then. The controller's brief for this design names the follow-up as "leader /readyz drops
old pods during a pre-upgrade migrate". No note with those words is in the repository; the
defect itself is the one line above, and it is fixed here (section 6).

**(b) Readiness waits for the identity provider.** This one is written down:
"`/readyz` waits for identity-provider metadata, so a replica restarted during a provider
outage also stops serving followers. Consider a separate admin-readiness signal"
(`plans/2026-10-03-leader-admin-followups.md:11-13`). The fetch has a 10 second timeout per
document (`auth/oidc.py:121`), which is why the README tells operators that the check "can
take about 20 seconds per configured identity provider; set probe timeouts accordingly"
(`README.md:153-155`). On Kubernetes this is worse than a slow probe: an identity provider's
outage, plus any reason for the pods to restart, stops transcription. It also means a leader
pod whose NetworkPolicy does not let it reach the identity provider is never Ready.

There is a third, smaller point. `/readyz` is anonymous and each call opens two database
connections with no time limit of its own. The console bounded its probe (one check at a
time, a 3 second limit, the answer reused for a second); the leader's is unbounded.

### 1.5 Migrations

Alembic, six revisions (`db/migrations/versions/0001…0006`). `swarmscribe-leader migrate`
runs `alembic upgrade head` (`db/migrate.py:25-26`, `main.py:78-81`) and prints the revision
it reached. Concurrent runs queue on a transaction-level advisory lock taken before Alembic
reads the current revision (`db/migrations/env.py:11-21`), so two Jobs cannot interleave.
There is no downgrade command.

How it is run today: by hand in development (`README.md:94-96`); as a one-shot Compose
service that the leaders wait for (`e2e/compose/docker-compose.yml:45-50`); and, in the
follower's `kind` test, as an init container of the single leader pod
(`e2e/follower-kind/leader.yaml:53-57`). Nothing runs it as a Helm hook yet.

### 1.6 Administration: there is no "first admin credential"

The leader stores no administrator. A person signs in with an ID token from Entra ID or
Google, and their role comes from the twelve `ROLE_*` lists in the leader's settings
(`api/admin_auth.py:87-120`; `auth/roles.py`). The first administrator is therefore
whoever `SWARMSCRIBE_ROLE_ADMIN_*` names. Nothing has to be created, and nothing can be:
there is no command that adds an administrator.

What an administrator does create, signed in with `swarmscribe-admin` (an HTTP client of the
admin API: `admin_cli/main.py:59-189`):

- pool tokens (`pool-tokens create`) and join tokens (`tokens create`) for followers;
- console credentials (`console create`), for a fleet console;
- locations, settings profiles, and so on.

`pool-tokens`, `console` and `profiles set` are refused for anything but a signed-in person
(`README.md:108-113`). So the path to a first pool token runs through an identity provider.
A leader with no provider configured starts and answers followers, but its admin API
answers 401 "sign-in is not configured on this leader" to everyone
(`packages/leader/tests/test_admin_auth.py:204-221`). The tests get round this by calling the
leader's own functions against its database from inside its container
(`e2e/follower-kind/admin.py`); that is a test's stand-in, not an operator's path.

`swarmscribe-admin` keeps its sign-in in `~/.config/swarmscribe/credentials.json`
(`admin_cli/credentials.py:45-49`). It is meant to run on the administrator's own machine.

### 1.7 Storage: a folder, and nothing else

The brief for this design assumes object storage ("S3 and whatever else is really
supported"). **The leader supports no object storage today, S3 or any other.** The one
backend is `local`: `storage/registry.py:12-22` builds a `LocalBackend` for
`backend == "local"` and for anything else raises "storage backend … is not available yet".
The specs name Azure Blob Storage and Google Cloud Storage as the cloud backends (master
spec line 52; leader spec line 24), never S3, and both are Plan B, not built
(`roadmap.md` item 5; README status table, "Built (local storage); cloud storage and
vocabulary next").

`LocalBackend` is "a folder on a filesystem every leader replica can see. The leader serves
its links" (`storage/local.py:37-38`). So:

- a location's root is an absolute path inside the leader's own filesystem
  (`swarmscribe-admin locations add NAME --root /path`, `admin_cli/main.py:78-80`);
- followers never see that filesystem: they download and upload through
  `GET`/`PUT /v1/files/<signed token>` on the leader (`api/files.py:91-183`), which is why
  the leader's public URL must be reachable from every follower;
- an upload is written to a temporary file beside its target and moved into place under a
  lock on the job's row (`api/files.py:128-141`, `:157`). The leader writes nowhere else.

"The local backend requires all leader replicas to see the same path (a shared volume in
Kubernetes)" (master spec, lines 293-294).

### 1.8 Background work, and whether two replicas are safe

Two loops run inside every replica (`app.py:53-68`): the reaper (expires leases, marks
silent followers gone) and the scanner (finds recordings, reads consent, queues jobs).
Each step runs under a Postgres session-level advisory lock, taken with
`pg_try_advisory_lock`, so one replica at a time does the work and the others skip the turn
(`background.py:11`, `:14-43`). There is no retention job and no scheduler beyond these two.

Everything else a replica shares with another is in Postgres:

- claims take job rows `FOR UPDATE SKIP LOCKED` (`jobs/store.py:110`, `:141`), so two
  replicas never hand one job to two followers;
- the reaper skips rows another transaction holds (`jobs/reaper.py:27`, `:65`);
- the rate limit on fresh links is a column, not a counter in memory
  (`jobs/store.py:189-196`);
- creating a console or a location serialises on a transaction advisory lock
  (`auth/consoles.py:86`, `ingest/locations.py:129`);
- file links are verified with the shared link key, so any replica serves any link.

What a replica holds in memory, and what it costs:

| In memory | Where | Effect with several replicas |
|---|---|---|
| each identity provider's signing keys | `auth/oidc.py:127-201` | each replica fetches its own; no effect on correctness |
| roles, for 300 s | `auth/roles.py:119` | a changed group membership takes up to five minutes per replica; already the documented behaviour (`README.md:117-118`) |
| directory access tokens | `auth/roles.py:212`, `:285` | none |
| the time the process started | `app.py:55`; `jobs/reaper.py:97-102` | a new replica expires nothing for one lease length; an older one may meanwhile. Intended |

**Two or more replicas are safe, on one condition the code cannot check: every replica sees
the same files.** This is not the planner's inference alone. The Compose test runs two
replicas behind nginx, kills one mid-run, and checks that every consented recording completes
exactly once (`README.md:323-327`; CI job `compose-e2e`). Its two replicas share one
bind-mounted folder (`e2e/compose/docker-compose.yml:18-19`).

### 1.9 Metrics

The leader has no `/metrics`. The specs plan one (master spec line 320; leader spec lines
353-355) under Plan B. `grep -ri "metrics\|prometheus" packages/leader/src` finds nothing.

### 1.10 The house style this chart must match

`deploy/helm/swarmscribe-console` and `deploy/helm/swarmscribe-follower` set it:

- no published image, so `image.repository` and `image.tag` are required and the render
  fails without them;
- the chart never renders a Secret; it names one the operator made;
- non-secret settings in a ConfigMap with a checksum annotation; a `settings` map and an
  `extraEnv` list that refuse secrets and names the chart owns, in any letter case;
- user 10001, read-only root filesystem, no capabilities, no privilege escalation,
  `RuntimeDefault` seccomp, no service-account token;
- a NetworkPolicy on by default that fails the render rather than silently cutting off a
  dependency;
- `values.schema.json`, and `ci/check_render.py`, which renders the chart and asserts what
  must hold and what must be refused (660 and 615 lines of cases);
- CI job `chart`: `helm lint --strict`, `kubeconform -strict`, the render check
  (`.github/workflows/ci.yml:191-238`).

Neither chart is installed on a cluster in CI. Both were installed on `kind` by hand and the
result recorded (C4b's ruling 11; `plans/2026-10-05-follower-f3b-kind-install-and-guide.md:80`,
ruling 2: "a local proof, recorded in an outcomes document, not a CI job"). There is no
follower `kind` job in CI, though the brief for this design speaks of one.

## 2. Goals

- An image of the leader to the standard of the other two, with a script that checks it.
- A chart that deploys the leader, and only the leader, on Kubernetes: Deployment,
  migration hook, Service, optional Ingress with TLS, ConfigMap, PodDisruptionBudget,
  NetworkPolicy, ServiceAccount.
- An upgrade that runs a migration and replaces the pods without a failed request.
- The three charts working together on one cluster, shown by a test that transcribes a
  recording end to end.
- A guide that says exactly what was run and what was not.

## 3. Decisions

### 3.1 The owner's rulings, carried over

| # | Ruling | Where it shows |
|---|---|---|
| O1 | Images are built and tested in CI and not published | `image.repository` and `image.tag` are required; the guide says to build and load |
| O2 | No KMS. Secrets come from Kubernetes Secrets the operator supplies; the chart never generates or prints one | `secrets.existingSecret`; the render check fails if a `Secret` is rendered |
| O3 | Consent gating is never weakened by a deployment option | the chart has no value that touches consent; the `kind` test queues a recording the consent file does not name and checks it is never transcribed |
| O4 | Plain, exact documentation of what was run and what was not | section 13; the outcomes document; the guide's last section |

### 3.2 The controller's defaults (the owner can overturn any of them)

| # | Default | What the code made of it |
|---|---|---|
| C1 | Postgres is external; the chart takes a connection Secret and never bundles a database | as stated. The Secret holds the whole URL under one key, as the console's does |
| C2 | Object storage is external; the chart takes its settings and its credentials Secret | **cannot be done as stated: the leader has no object storage (1.7).** The chart instead mounts volumes the operator provides (`storage.volumes`) and creates none. There are no storage credentials to take. When a cloud backend is built, its settings join the chart then (follow-up F1) |
| C3 | Migrations run as a pre-install and pre-upgrade hook Job from the same image; the rollout does not drop serving pods; the `/readyz` follow-up is fixed in the leader, with tests | as stated (sections 6 and 7) |
| C4 | The first admin credential is created by a documented one-off command, never by the chart, never printed or stored in a ConfigMap | **there is no such credential (1.6).** The first administrator is a role mapping: non-secret values (`roles.admin.*`) that become `SWARMSCRIBE_ROLE_ADMIN_*`. The one-off commands that do exist are the first pool token and the first console credential; they are run with `swarmscribe-admin` from the operator's machine, print their secret once to that terminal, and the chart has no part in them |
| C5 | Security posture equal to the other two charts | as stated (section 9) |
| C6 | A ServiceMonitor only if the leader really exposes metrics | it does not (1.9): no ServiceMonitor, no scrape annotations |
| C7 | Replica count decided from the code; refuse more than one if it is not safe | more than one is safe (1.8). Default 2, as the master spec asks ("2+ replicas", line 344). The condition, shared files, is one the chart cannot see; it is stated in `values.yaml`, in the guide and in `NOTES.txt` (3.3, R6) |
| C8 | Verification to the standard of the other charts, with a real install on `kind`, a real Postgres and a real S3-compatible store, a follower joining, a transcription, an upgrade under load, NetworkPolicy seen blocking, and a CI job | as stated, except the object store: there is none to test. The test uses a real PersistentVolumeClaim instead. The CI job is the first `kind` job in this repository (1.10) |
| C9 | Every Docker or `kind` step first checks that C: has 20 GB free | `docker/check-free-space.sh`; the `kind` driver does the same check itself before it creates a cluster or loads an image |

### 3.3 Rulings made in this design

Those marked **(owner)** are in the open-questions file.

- **R1. `/readyz` is ready when the schema is this leader's or newer.** As the console's.
  `serve` still refuses to *start* on a database that is ahead: staying in service is for a
  replica that is already serving.
- **R2. `/readyz` no longer waits for the identity provider. (owner)** It answers for the
  database alone. An administrator's call during an identity-provider outage is already
  answered `503` with `Retry-After` by the admin API itself (`api/admin_auth.py:114-120`).
  This reverses a line of the leader spec (section 12: "OIDC metadata fetched at least
  once"). The reason is 1.4 (b).
- **R3. `/readyz` is bounded**, as the console's: one database check at a time, 3 seconds at
  most, the answer reused for one second, one log line per cause per 30 seconds.
- **R4. The image has an init (tini) as PID 1**, as the follower's has. Without one, a
  SIGTERM that arrives while `serve` is still waiting for the database, or at any time
  during `migrate`, is dropped (PID 1 has no default signal action), and the pod is killed
  only when its grace period ends.
- **R5. A leader without sign-in cannot be rendered**, unless `oidc.allowNone: true` (for
  tests). With sign-in on, `roles.admin` must name someone. Both would otherwise deploy a
  leader nobody can administer, and say nothing.
- **R6. `storage.volumes` is required and the chart creates no volume.** Each entry is a
  claim the operator made, or a raw volume source (NFS, a CSI volume). An `emptyDir` is
  refused. The chart does not ask for an access mode and cannot verify one; a
  ReadWriteOnce claim works while every pod is on one node and hangs a rollout otherwise.
  `updateStrategy: Recreate` exists for that case, with its cost stated: every upgrade is an
  outage of the leader.
- **R7. The public URL is `https://` unless `allowHttpPublicUrl: true`.** Credentials, pool
  tokens and signed links travel on it. The leader's pods speak plain HTTP on 8080; TLS ends
  at the Ingress (or whatever the operator puts in front of the Service). The follower
  chart's `leader.allowHttp` is the same exception on the other side.
- **R8. The Ingress publishes `/v1` only.** Never `/`, never `/healthz` or `/readyz`. The
  render check reads the leader's routers and fails if one is ever outside the published
  paths.
- **R9. The NetworkPolicy's ingress peers are required**, or `anySource: true`. The follower
  chart's default lets nobody in; the console's lets anybody in. A leader that nobody can
  reach, with nothing said, is the worse surprise, so the render fails until the operator
  chooses.
- **R10. Egress to HTTPS is rendered only when sign-in is on.** The leader calls nothing
  else outside: its storage is mounted by the node, not reached by the pod.
- **R11. A `preStop` sleep (5 s by default) before the leader is told to stop**, so that the
  Service stops sending a pod new requests before it stops listening. Without it a rollout
  refuses a few connections however ready the new pods are.
- **R12. The `kind` install is a CI job. (owner)** This departs from the two earlier charts.
- **R13. The existing tests keep the old test image.** `e2e/compose/Dockerfile` stays for
  the three Compose tests and the follower's `kind` test; moving them is follow-up F5.

## 4. The image

`docker/leader.Dockerfile`, to the standard of `docker/console.Dockerfile`:

| | |
|---|---|
| Base | `python:3.12-slim-bookworm`, pinned by tag and digest, the same digest as the other two |
| Build stage | `uv==0.12.22`; every workspace member's `pyproject.toml` (uv needs them to read the lock); `uv sync --frozen --no-dev --package swarmscribe-leader --no-install-workspace`, then the sources of `protocol` and `leader` only (`src/`, never `tests/`), then `--no-editable` |
| Holds | `/app/.venv` with `swarmscribe-leader`, `swarmscribe-protocol` and their libraries; both entry points (`swarmscribe-leader`, `swarmscribe-admin`); the Alembic migrations, inside the wheel |
| Does not hold | the engine, any model library, the console, the follower, pytest, `uv`, the source tree |
| User | `10001:10001`, no home, no shell |
| Writes | nothing outside the volumes mounted into it; no `/tmp` needed |
| PID 1 | `tini`, with the leader as its only child (R4) |
| Entrypoint | `["/usr/bin/tini", "--", "swarmscribe-leader"]`, default arguments `serve --host 0.0.0.0 --port 8080`; `migrate` by overriding the arguments |
| Healthcheck | `GET /healthz` on loopback; never `/readyz` |
| Labels | title, description, source, version (compared with the installed package) |

`docker/check-leader-image.sh <image>` proves it, with a throwaway Postgres on a network of
its own: the labels; the user; the entrypoint; what is and is not installed; that the
migrations are in the wheel; `migrate` without configuration exits 2 without a traceback
under `--read-only --cap-drop ALL --network none`; `serve` refuses an unmigrated database;
`migrate` runs and reports the revision; `/healthz` and `/readyz` are 200; **with the
database's revision set ahead, the running leader stays 200 and a second one refuses to
start**; behind, 503; with the database frozen, 503 within the probe's timeout while
`/healthz` stays 200; a stop while waiting for a frozen database ends at once; a stop while
serving ends by itself; the log holds neither the link key nor the database password.

The image is not published (O1).

## 5. The chart's values

`deploy/helm/swarmscribe-leader`. One release is one leader deployment.

| Value | Default | Meaning |
|---|---|---|
| `nameOverride`, `fullnameOverride` | `""` | names; the base is cut to 55 characters so `-migrate` fits in 63 |
| `replicaCount` | `2` | at least 1 (section 8) |
| `image.repository`, `image.tag` | `""` | **required**; the image is not published |
| `image.digest` | `""` | `sha256:…`; wins over the tag |
| `image.pullPolicy` | `IfNotPresent` | |
| `imagePullSecrets` | `[]` | |
| `publicUrl` | `""` | **required**; `SWARMSCRIBE_PUBLIC_URL` and the Ingress host. Exactly `https://<lowercase DNS hostname>`, with `:port` only when the Ingress is off |
| `allowHttpPublicUrl` | `false` | accepts `http://`; a test cluster only; never with the Ingress |
| `secrets.existingSecret` | `""` | **required**; the Secret the operator made |
| `secrets.keys.databaseUrl` | `database-url` | `SWARMSCRIBE_DATABASE_URL` |
| `secrets.keys.linkKey` | `link-key` | `SWARMSCRIBE_LINK_KEY` |
| `secrets.keys.entraClientSecret` | `entra-client-secret` | read only when `oidc.entra.clientSecret` |
| `secrets.keys.googleClientSecret` | `google-client-secret` | read only when `oidc.google.enabled` |
| `secrets.keys.googleServiceAccount` | `google-service-account` | read only when `oidc.google.serviceAccount`; the JSON key |
| `oidc.allowNone` | `false` | renders a leader with no sign-in; tests only (R5) |
| `oidc.entra.enabled`, `.tenantId`, `.clientId` | off | Entra ID sign-in |
| `oidc.entra.clientSecret` | `false` | read the client secret (Microsoft Graph, for people in too many groups) |
| `oidc.google.enabled`, `.clientId`, `.hostedDomain` | off | Google sign-in |
| `oidc.google.serviceAccount` | `false` | read the Google Groups service-account key |
| `roles.{viewer,operator,admin}.{entraGroups,googleGroups,emails,domains}` | `[]` | the twelve `SWARMSCRIBE_ROLE_*` lists. `roles.admin` must name someone when sign-in is on |
| `settings` | `{}` | other non-secret settings without the prefix (`LEASE_SECONDS: "120"`); secrets and chart-owned names are refused |
| `extraEnv` | `[]` | extra environment (`HTTPS_PROXY`); the same refusals |
| `storage.volumes` | `[]` | **required**; `{name, mountPath, existingClaim \| volume, readOnly}` (R6) |
| `storage.fsGroup` | `10001` | the group the volumes are opened with |
| `storage.supplementalGroups` | `[]` | more groups, to read files another system wrote |
| `port` | `8080` | the pod's port |
| `service.type`, `service.port` | `ClusterIP`, `80` | |
| `ingress.enabled` | `true` | |
| `ingress.className`, `.annotations` | `""`, `{}` | the operator's controller |
| `ingress.tls.secretName` | `""` | **required** with the Ingress |
| `ingress.paths` | `[{/v1, Prefix}]` | never `/`, never a probe (R8) |
| `migrate.enabled` | `true` | the hook Job |
| `migrate.backoffLimit`, `.activeDeadlineSeconds`, `.resources` | `3`, `300`, 50m/128Mi | |
| `resources` | 100m / 256Mi, limit 512Mi | not measured under load (risk K6) |
| `updateStrategy` | `RollingUpdate` | or `Recreate` (R6) |
| `preStopSleepSeconds` | `5` | R11; 0 turns it off |
| `terminationGracePeriodSeconds` | `60` | must exceed the sleep |
| `podDisruptionBudget.enabled`, `.maxUnavailable` | `true`, `1` | rendered only for more than one replica |
| `networkPolicy.enabled` | `true` | section 9 |
| `networkPolicy.ingress.from` | `[]` | **required** with the policy, unless `anySource` |
| `networkPolicy.ingress.anySource` | `false` | |
| `networkPolicy.egress.dns.peers` | kube-dns | |
| `networkPolicy.egress.postgres.port`, `.peers` | `5432`, `[]` | peers **required** with the policy |
| `networkPolicy.egress.https.ports`, `.cidrs`, `.extraExcept` | `[443]`, everywhere | rendered only with sign-in (R10) |
| `networkPolicy.egress.extra` | `[]` | raw rules, e.g. a proxy |
| `serviceAccount.create`, `.name` | `true`, `""` | no token is mounted |
| `podAnnotations`, `podLabels`, `nodeSelector`, `tolerations`, `affinity` | empty | |
| `spreadAcrossNodes`, `topologySpreadConstraints` | `true`, `[]` | a soft spread for two or more replicas |

What the render refuses, each with a message: no image; no public URL, or one that is not
exactly a host; `http://` without the switch, or with the Ingress; a port with the Ingress;
no Secret; no sign-in without `allowNone`; sign-in with nobody under `roles.admin`; a role
list whose provider is off (the leader's own rule, `config.py:189-200`, so the mistake fails
the render and not every pod at start); a secret or a chart-owned name under `settings` or
`extraEnv`; no storage volume, an `emptyDir`, a mount at `/` or under `/app`, a name or path
used twice; an Ingress path of `/` or a probe; an Ingress without TLS; a NetworkPolicy
without Postgres peers or without ingress peers; a grace period no longer than the sleep.

## 6. Readiness, in the leader

`api/health.py` becomes, in outline, the console's:

```
/healthz   200 {"status": "ok"}; touches nothing
/readyz    one check at a time, at most 3 s, reused for 1 s:
             database unreachable or silent        503 {"status": "database unreachable"}
             revision == this leader's head        200 {"status": "ready"}
             no schema, or a revision this
               leader knows and is not its head    503 {"status": "database migrations are not current"}
             a revision this leader does not know  200 {"status": "ready"}   (a newer leader migrated it)
```

"A revision this leader knows" is `is_known_revision` (`db/migrate.py:39-45`), which `serve`
already uses to tell "behind" from "ahead" (`main.py:42-46`). The identity provider is not
asked (R2). Both answers carry `Cache-Control: no-store`. A failing check logs one line per
cause per 30 seconds, with the exception's type and never its text (it can hold the
database's address).

What this means for whoever writes a migration is unchanged from the console and is said in
the guide: **every migration must stay compatible with the previous release**, because the
previous release's pods run on the new schema for the length of the rollout. Add a column in
one release; drop the old one in a later release.

Tests (`packages/leader/tests/test_health.py`, new): ready when ahead; not ready when
behind, and before the first migration; "database unreachable" without the database's
address or password in the answer or the log, logged once; not waiting for the identity
provider, with the admin API still answering 503; fifty concurrent calls share one check; a
database that does not answer gets a 503 on time; `serve` still refuses to start when the
database is ahead. `test_readyz_needs_the_sign_in_metadata` is removed
(`tests/test_admin_auth.py:180-186`).

## 7. Migrations and upgrades

The migration is a Job with `helm.sh/hook: pre-install,pre-upgrade`, running
`swarmscribe-leader migrate` from the release's image.

- A hook runs before the release's own ConfigMap and ServiceAccount exist on a first
  install. So the Job carries the settings inline (the same map the ConfigMap is rendered
  from; the render check compares them), uses the namespace's default service account with
  no token, and reads the operator's Secret, which must exist before `helm install`.
- It mounts no storage volume. A migration touches the database only.
- `helm.sh/hook-delete-policy: before-hook-creation,hook-succeeded`: an old Job never
  blocks an upgrade; a Job that worked is removed; a Job that **failed is kept**, so
  `kubectl logs job/<name>-migrate` says why, and the failed hook fails the upgrade.
- Not an init container: replicas starting together would each run Alembic (they would
  queue on the advisory lock and be correct, but every pod start would then depend on
  holding a lock on the database).

An upgrade, step by step:

1. `helm upgrade` creates the Job. The old pods serve.
2. The Job migrates. The old pods now run on a newer schema and stay Ready (section 6).
3. Helm applies the release. The Deployment rolls with `maxUnavailable: 0, maxSurge: 1`: a
   new pod starts (`serve` finds the schema current), becomes Ready, and only then is an old
   pod stopped.
4. A stopping pod sleeps `preStopSleepSeconds` while still answering; the Service forgets
   it; then the leader gets SIGTERM, finishes the requests it has, and its background loops
   are given 10 seconds (`app.py:22`, `:70-80`).

What does not work, and is said plainly in the guide:

- **A rollback of the image after a migration.** The old image refuses to start on the
  newer schema, and there is no downgrade. Roll forward.
- **An old pod that restarts during the rollout** (a crash, an eviction) does not come
  back: `serve` refuses the newer schema. The other old pod and the new pods carry on.
- **A migration that breaks the previous release** breaks the serving pods for the length
  of the rollout. Nothing in the chart can prevent that.
- **A long-lived connection** to a pod that is stopping may be closed under a request sent
  at that instant. Followers retry (master spec, line 373). The test opens a new connection
  for every request and does not measure this.

## 8. Replicas and storage

`replicaCount` defaults to 2 and has no upper bound in the schema: the code is safe with
several (1.8). The chart cannot check the one condition, so it states it wherever an
operator will look, and offers the two honest configurations:

| The volume | Replicas | Strategy | Upgrades |
|---|---|---|---|
| ReadWriteMany (NFS, CephFS, Azure Files, Filestore) | 2 or more | `RollingUpdate` | no pod missing |
| ReadWriteOnce, all pods on one node (a one-node cluster) | 2 or more | `RollingUpdate` | no pod missing; the node is a single point of failure |
| ReadWriteOnce, several nodes | 1 | `Recreate` | the leader is away for the length of every upgrade |

The middle row is what the `kind` test runs (one node, kind's `local-path` class, which
offers ReadWriteOnce only). The first row is what the chart is designed for and is **not
run by anything here** (risk K1).

Volumes are opened with `fsGroup: 10001`. Volumes that ignore `fsGroup` (NFS) must be
exported writable by uid or gid 10001; `storage.supplementalGroups` lets the leader read
files another system wrote.

Postgres: at most 15 connections per replica, and one for the migration Job. The guide
gives the sum.

## 9. Security and the network

Pod: `runAsNonRoot`, uid and gid 10001, `fsGroup` (the storage), `RuntimeDefault` seccomp,
no service-account token. Container: read-only root filesystem, no privilege escalation,
all capabilities dropped. No `/tmp` volume: the image needs none, and the render check
fails if one appears. The pod never shares a host or process namespace.

Secrets: `SWARMSCRIBE_DATABASE_URL` and `SWARMSCRIBE_LINK_KEY` always, and up to three
sign-in secrets, each a `secretKeyRef` into the operator's Secret. Nothing secret is in the
ConfigMap, in the values, in `NOTES.txt` or in Helm's record of the release; the `kind` test
searches `helm get all` and every ConfigMap for the link key and the database password.

NetworkPolicy, selecting every pod of the release (the migration Job's too):

| Direction | Rule |
|---|---|
| Ingress | TCP on `port`, from `networkPolicy.ingress.from` (required), or from anyone with `anySource` |
| Egress | DNS, to kube-dns |
| Egress | Postgres, to `networkPolicy.egress.postgres.peers` (required), on its port |
| Egress | with sign-in only: TCP 443 to anywhere except loopback, link-local and metadata addresses, multicast and reserved ranges (the list the other two charts cut out) |
| Egress | `networkPolicy.egress.extra` |

What it cannot do is written beside it, as in the other charts: it needs a network plugin
that enforces it; it matches addresses, not names; peers are matched after Service address
translation; on a first install the hook runs before the policy exists.

The Ingress, and one thing the operator must do that the chart cannot:

- **File links carry their signed token in the URL path** (`/v1/files/<token>`). The leader
  itself never logs a request line (`main.py:93`), and the Compose tests' nginx is
  configured not to either (`e2e/compose/nginx.conf:1-5`). An ingress controller's access
  log is the operator's: left on, it holds tokens that work until they expire (30 minutes
  for a download, 2 hours for an upload, and an upload only while its job's lease is
  current). The values file, `NOTES.txt` and the guide all say to turn request logging off
  for this Ingress.
- Uploads are up to 512 MiB: the controller's body limit must allow it and request
  buffering should be off.
- The annotations for both are the controller's own; the chart stays neutral, as the
  console's does.

## 10. The three charts on one cluster: a walk-through

One cluster, one namespace per part. The leader is `https://leader.example.org`; the console
is `https://console.example.org`; an ingress controller serves both; the identity provider
is Entra ID. Postgres is a managed service outside the cluster with two databases. Recordings
are on an NFS export.

**Before anything is installed**

1. Build the three images and put them where the cluster can pull them (O1).
2. Entra ID: one app registration for the leader ("Allow public client flows", the `groups`
   claim), one for the console (a web app, redirect URI
   `https://console.example.org/auth/callback`). Note the object id of the group whose
   members administer the leader.
3. Two databases: `swarmscribe` for the leader, `swarmscribe_console` for the console
   (the console needs Postgres 14 or later; the leader's tests run on 16). Never one
   database for both.
4. A ReadWriteMany PersistentVolumeClaim `recordings` in the leader's namespace, on the NFS
   export, writable by gid 10001.

**The leader**

```
kubectl create namespace swarmscribe
kubectl -n swarmscribe create secret generic swarmscribe-leader \
  --from-literal=database-url='postgresql://leader:...@db.internal:5432/swarmscribe' \
  --from-literal=link-key="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
helm -n swarmscribe install leader deploy/helm/swarmscribe-leader -f leader-values.yaml
```

with `leader-values.yaml` holding the image, `publicUrl: https://leader.example.org`,
`secrets.existingSecret: swarmscribe-leader`, `oidc.entra` with the tenant and client ids,
`roles.admin.entraGroups: [<the group's object id>]`, the storage volume
(`{name: recordings, mountPath: /data/recordings, existingClaim: recordings}`), the Ingress
class and TLS Secret, and the NetworkPolicy's peers: Postgres's address, and for ingress the
ingress controller's namespace.

Helm runs the migration, then starts two pods. Nothing is printed but what to do next.

**The first administrator, a location, a pool token** — from the operator's own machine:

```
swarmscribe-admin --leader https://leader.example.org login --provider entra
swarmscribe-admin whoami                                  # role: admin
swarmscribe-admin locations add archive --root /data/recordings/archive
swarmscribe-admin pool-tokens create --name cpu-pods --pool default   # shown once
```

The administrator is admin because they are in the group that `roles.admin` names. Nothing
was created for them. The location's root is a folder under the mounted volume; it must
hold a `consent.txt`, and only what that file names is ever queued (O3).

**A follower pool** — the pool token goes into a Secret, by the operator's hand:

```
kubectl create namespace transcribe
kubectl -n transcribe create secret generic pool-token --from-literal=pool-token='<the token>'
helm -n transcribe install cpu deploy/helm/swarmscribe-follower -f pool-values.yaml
```

with `leader.url: https://leader.example.org`. The followers reach the leader **through the
Ingress**, at the same address the leader builds its file links from; that is the one
address every follower, inside the cluster or outside it, can use. So the follower pods'
traffic arrives at the leader from the ingress controller, and the leader's NetworkPolicy
needs only that one ingress peer. (A pool may instead use the Service directly,
`http://leader-swarmscribe-leader.swarmscribe`, only if the leader's own public URL is that
same address, which then no outside follower can use. That is the test cluster's shape, not
this one.)

**The console**

```
swarmscribe-admin console create --name fleet --max-role operator      # shown once
kubectl create namespace fleet
kubectl -n fleet create secret generic swarmscribe-console --from-literal=...
helm -n fleet install console deploy/helm/swarmscribe-console -f console-values.yaml
kubectl -n fleet exec deploy/console-swarmscribe-console -- \
  swarmscribe-console admins add entra_group <console admins' group id>
```

Then, signed in to the console, an administrator registers the leader with its URL and the
credential from the first line. The console calls the leader at
`https://leader.example.org`, through the Ingress, on 443, which its NetworkPolicy allows by
default. The leader never calls the console.

**What talks to what**

| From | To | How | Allowed by |
|---|---|---|---|
| a follower pod | the leader | HTTPS, the Ingress, `/v1/followers`, `/v1/jobs`, `/v1/files` | follower egress 443; leader ingress from the ingress controller |
| the console | the leader | HTTPS, the Ingress, `/v1/admin` | console egress 443; leader ingress from the ingress controller |
| an administrator | the leader | HTTPS, the Ingress, `/v1/admin` | the Ingress |
| the leader | Postgres | 5432 | leader egress to the Postgres peers |
| the leader | Entra ID, Microsoft Graph | 443 | leader egress 443 |
| the leader | recordings | the mounted volume | nothing: the node mounts it |
| the kubelet | every pod | probes | not cut off by most network plugins |

**An upgrade of the leader** is `helm upgrade` with the new image tag: section 7.

What of this has been run is in section 13. In short: the leader and the follower charts
together, on `kind`, over plain HTTP and the Service, without an identity provider. The
console in the same cluster, an Ingress, TLS and a real sign-in have not.

## 11. Verification

**Unit tests**, in the suite: the readiness tests of section 6.

**The image**: `docker/check-leader-image.sh` (section 4), in CI.

**The chart, rendered** (CI job `chart`, and locally before every commit):

- `helm lint --strict` with `ci/test-values.yaml`;
- `kubeconform -strict` on the rendered manifests;
- `ci/check_render.py`, five sections (core, storage, migrate, ingress, network): what must
  be rendered and more than a hundred sets of values that must be refused, the important
  ones with the message they must give. Among them: the ConfigMap holds only names that are settings of the leader
  (read from `config.py`); the Job's inline settings equal the ConfigMap's; the Ingress
  reaches every router the leader has and never a probe; one address from each refused
  range is cut out and none of an identity provider's is; a leader without sign-in has no
  HTTPS egress; a narrowed range carries no `except`.

**The chart, installed** (`e2e/leader-kind/run_e2e.py` on `kind`; CI job `leader-kind-e2e`):

1. Install with a real Postgres and a real PersistentVolumeClaim (the test's own; the chart
   brings neither). The hook runs first and is removed; two pods are Ready with no restart,
   uid 10001, tini as PID 1, the root filesystem read-only; neither `helm get all` nor any
   ConfigMap holds the link key or the database URL.
2. A pool token goes through a Secret to the **follower chart**; two followers register
   through the leader's Service.
3. Three consented recordings are transcribed once each, through file links served by the
   leader's pods; a recording the consent file does not name is never queued.
4. NetworkPolicy, from both sides: a follower connects; a pod that is no peer times out, at
   a leader pod and at the Service; a leader pod times out at another pod's open port, and
   reaches Postgres by name.
5. **An upgrade with a migration, under load.** A prober pod sends ten or more requests a
   second through the Service, each on a new connection (one answered from settings, one
   answered by a query on the database). A twelve-minute recording is in hand. The rollout
   is paused, `helm upgrade` runs the hook with an image that carries one more migration,
   and for 30 seconds the old pods are watched on the migrated database: both Ready, both
   answering 200 on `/readyz`, two ready addresses behind the Service. Thirty seconds is
   twice what three failed readiness probes take, so a leader with the defect of 1.4 (a)
   fails here. Then the rollout is resumed and the pods are replaced. No request fails, the
   Service never has no ready address, and the recording is finished in one attempt.
6. A new recording is transcribed by the upgraded leader; no leader log holds the pool
   token, the link key or a file link.

**A control run**, once, by hand, recorded in the outcomes document: the same scenario with
an image that has the old `health.py`, to see step 5 fail. A test that has never been seen
to fail proves less.

**What the test's shape leaves out** is listed in section 13 and repeated in the guide.

## 12. Non-goals

- Publishing an image (O1); a KMS (O2).
- A database in the chart, or a volume in the chart.
- Object storage of any kind, until the leader has a backend for it.
- Autoscaling (the other half of roadmap item 6): followers scale on a queue-depth metric
  the leader does not expose yet.
- Metrics, a ServiceMonitor, dashboards.
- A leader reachable only by an IP address: the public URL is a DNS name.
- An umbrella chart for all three. Each part has its own release cycle and may be in another
  cluster (C4b's ruling 1).
- The console and an identity provider in the `kind` test.
- A way to administer a leader without an identity provider (open question 3).

## 13. What the planner ran, and what it did not

Ran, on the development machine, 2026-10-05 (Helm v4.3.0, kubeconform v0.8.0, Python 3.12):

- Every file of the chart as plan L2 gives it, in a scratch folder outside the repository:
  `helm lint --strict`, `helm template`, `kubeconform -strict -kubernetes-version 1.33.0`
  (8 resources with the test values, 7 with the `kind` values, all valid), and
  `ci/check_render.py`, which passed in all five sections after two mistakes of its own were
  corrected (it expected a template's message where the schema refuses first, and it used a
  release name Helm itself refuses).
- The `kind` test's values against both charts (`helm template`, `kubeconform`), and the
  driver's own unit tests (12 passed; 1 skipped, which needs the follower package).
- `ruff check` on every Python file the plans give, with the repository's settings.
- `bash -n` on both shell scripts, and `docker/check-free-space.sh` itself (C: had 91 GB).
- Every file was then extracted back out of the three plans into an empty folder and the
  same checks repeated on those copies, so what the plans print is what was checked.
- The SHA-256 of `kind` v0.30.0 for Linux, for the CI job: the published sum and the sum of
  the downloaded file agree.

Did not run — the planner was told to run no Docker, no `kind`, no test suite and nothing
that starts Postgres:

- **No image was built.** `docker/leader.Dockerfile` and `docker/check-leader-image.sh` are
  written and unrun.
- **No leader test was run.** `api/health.py` and `tests/test_health.py` are written,
  linted, and unrun.
- **Nothing was installed on a cluster.** `e2e/leader-kind/run_e2e.py` has never been
  executed. It is modelled closely on `e2e/follower-kind/run_e2e.py`, which has run; what
  is new in it (the paused rollout, the prober, the endpoint watch, the fixture sent over
  `kubectl exec`) is where to expect corrections. Plan L3's Task 2 is its first run.

Will not be proven even when the plans are done (each is in the guide's last section):

- a ReadWriteMany volume, and leader pods on more than one node (risk K1);
- an Ingress, TLS, and an ingress controller's body limit and logging;
- a real identity provider, `swarmscribe-admin login` against a leader in a cluster, and
  the role mapping rendered by the chart reaching a real sign-in;
- the console in the same cluster;
- a network plugin other than kind's;
- a migration that changes the schema: the test's migration changes the revision only;
- a node drain, and the PodDisruptionBudget doing anything;
- load: the leader's requests and limits are the console's, unmeasured.

## 14. Risks

| # | Risk | Mitigation |
|---|---|---|
| K1 | Shared storage is assumed, not verified. Two replicas on different nodes with a ReadWriteOnce claim hang a rollout (Multi-Attach); two replicas that somehow see different files would each scan its own and mark the other's recordings missing | stated in `values.yaml`, the guide and `NOTES.txt`; `emptyDir` refused; `Recreate` offered. Not tested. A start-up check in the leader (follow-up F3) would turn it into an error |
| K2 | An ingress controller logs file-link tokens | stated three times; cannot be enforced by a chart. Tokens expire, and an upload link is useless without its lease |
| K3 | A migration incompatible with the previous release | a rule for authors, in the guide; nothing checks it (follow-up F4) |
| K4 | R2 hides a misconfigured identity provider: the pods are Ready and sign-in answers 503 | the admin API says so on the first call; the leader logs each failed fetch (`auth/oidc.py:169-172`). The alternative stops transcription during a provider's outage |
| K5 | The `kind` test is long and new, and may be flaky as a required CI job | it holds the rollout rather than racing it; its one racy quantity (ready addresses while pods are replaced) is asserted only never to be zero. If it flakes, R12 goes back to "by hand, recorded" |
| K6 | The leader's resource requests are a guess | said in `values.yaml` and the guide; follow-up F6 |
| K7 | `fsGroup` does nothing on NFS, and a recursive ownership change is slow on a large volume | `fsGroupChangePolicy: OnRootMismatch`; the guide says to export NFS writable by gid 10001 |
| K8 | The first administrator is a value. A wrong group id deploys a leader nobody can administer | the render refuses an empty `roles.admin`; a wrong one is fixed with `helm upgrade` and needs no data change |

## 15. Build plan

| Plan | Ends in | Tasks |
|---|---|---|
| L1 `2026-10-05-leader-chart-l1-image-and-readiness.md` | a leader that stays Ready through a migration, and an image of it that CI builds and checks | 4 |
| L2 `2026-10-05-leader-chart-l2-chart.md` | the chart, linted, validated and render-checked in CI, with its guide | 5 |
| L3 `2026-10-05-leader-chart-l3-kind-and-guide.md` | the chart installed on `kind` with the follower chart, an upgrade under load, a CI job, the outcomes on record | 4 |

Each plan works without the next. L2 needs L1's image only to be installed, not to be
rendered. L3 needs both.

## 16. Follow-ups

- **F1. Cloud storage in the chart**, when the leader has Azure and GCS backends (Plan B):
  credentials by workload identity or a Secret, egress to the storage endpoints, and the
  volume no longer required.
- **F2. Metrics**: `/metrics` in the leader (Plan B), then scrape annotations or a
  ServiceMonitor here, then follower autoscaling on queue depth (the rest of roadmap item 6).
- **F3. A shared-storage check in the leader**: a marker file written by one replica and
  looked for by the others, so that replicas which do not share a volume refuse to scan.
- **F4. A check on migrations**: at least a test that the previous release's code runs
  against the new schema.
- **F5. Move the three Compose tests and the follower's `kind` test to the real leader
  image** and delete `e2e/compose/Dockerfile`. They bind-mount a folder the image's user
  cannot write to as it stands, and `e2e/follower-kind/admin.py` reads a fixture from the
  source tree that the real image does not hold. This also ends "CI builds the leader image
  three times" (`follower-f1-followups.md:58`).
- **F6. Measure the leader** under a realistic queue and size its requests.
- **F7. The console and an identity provider in the `kind` test**, with the leader on TLS
  behind an ingress controller. This would also be the first run of the follower chart's
  `leader.ca` (`follower-f1-followups.md:69`).
- **F8. An admin-readiness signal** (the other half of `leader-admin-followups.md:11-13`):
  if R2 stands, a way to see from outside that sign-in is working, without gating traffic.
- **F9. Pin tini and the Dockerfile frontend by digest** in all three images
  (`follower-f1-followups.md:54`, M3).
- **F10. A database pool setting** in the leader, so that connections per replica are the
  operator's choice and not SQLAlchemy's default.
