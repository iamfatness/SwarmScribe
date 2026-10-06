# Leader chart: follow-ups

From the leader chart spec (section 16) and from building plans L1 to L3. None blocks merge.
Plan L3 adds "From the build: L3", and marks what its runs settle.

**From the build: L1** is not repeated here. What L1's review raised and its fixes left open
is in `2026-10-03-leader-admin-followups.md`, under "From the review of the leader chart's
first plan (L1), 2026-10-06" (it arrives with the pull request of the branch
`leader-chart-l1-fixes`).

## From the spec

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

## From the build: L2

What the review of plan L2 (`.superpowers/sdd/2026-10-05-leader-chart/l2-review.md`) raised
and the fixes left open, and what the fixes themselves found. Each says what was seen.

### Not run (plan L3 runs these, or says it did not)

- **B1. "When the install or an upgrade fails" in the README is written from reading.** The
  pod states for a missing Secret and an unpullable image, what Helm 4 leaves after a failed
  pre-install hook, and whether `helm uninstall` then `helm install` is needed or
  `helm upgrade --install` is accepted on a failed first release: none was run (review I8).
  L3's Task 2 has the four failure runs; its Task 4 corrects the section from them.
- **B2. The ingress-nginx annotations** in the guide (`enable-access-log`, `proxy-body-size`,
  `proxy-request-buffering`, the two timeouts) are from that controller's documentation,
  not from a run. What Traefik needs is stated more loosely still.
- **B3. `?ssl=require` for TLS to Postgres** is from the driver's documentation (review m15).
- **B4. `fsGroup` where a driver honours it**: the cost of the first mount on a large volume
  is described, not measured (review m2). `storage.fsGroup: null` is rendered and checked;
  it has not been installed.
- **B5. A stop during a database outage, and sign-in behind a blocked egress or a proxy, on
  a cluster** (L1 review I2, M1). The image's own check now runs the first without a
  cluster. The guide states both as they are with L1's follow-up fixes: a leader ends about
  17 seconds after it is told to, and a blocked provider answers 503 in about 3 seconds.

### What the chart cannot do yet

- **B6. No way to mount a CA bundle.** A proxy that re-signs TLS needs `SSL_CERT_FILE` and a
  file inside the container; a Postgres with a private CA needs one too. The chart has no
  value for either (review I8, m15); the guide says "an image built with it". A value such
  as `extraCaBundle: {existingConfigMap, key}`, mounted read-only, would do both.
- **B7. `podLabels`, `podAnnotations` and `extraEnv` do not reach the migration Job** (review
  m16, I8). By design for `extraEnv` (the Job calls nobody but Postgres) and stated in
  `values.yaml`; on a mesh that injects a sidecar into every pod the hook never completes
  unless the mesh is told to leave the Job alone. A `migrate.podAnnotations` would let the
  operator say so in the chart.
- **B8. `service.type: NodePort` and `LoadBalancer` are still accepted** (review m14):
  `values.yaml` and the guide now say they publish plain HTTP and the probes. Refusing them
  outright, or behind a switch like `allowHttpPublicUrl`, was not done.
- **B9. `hostPath` is refused outright.** The review suggested taking it behind a switch
  that says "one node"; the fix refuses it. A one-node cluster uses a local PersistentVolume
  and a claim instead. Revisit if somebody needs it.
- **B10. The chart cannot see what an Ingress annotation does.** `ingress.paths` can no
  longer publish a probe (every path is `/v1` or under it, `Exact` or `Prefix`), but a
  rewrite or a server-snippet annotation still can. Stated in the guide; not enforceable.
- **B11. A `csi` volume source is trusted to be shared.** Kubernetes mounts an inline CSI
  volume per pod; whether that is one share or a fresh volume each time depends on the
  driver. `values.yaml` and the guide say which kind to use; the chart cannot tell.

### Stricter than the leader, on purpose

- **B12. Role entries.** The schema refuses a few spellings the leader accepts: a leading
  `@` (`@example.org`), a domain that starts or ends with a dot, and surrounding spaces.
  The check proves the other direction (nothing the schema takes is refused by the leader's
  own `_is_domain`, which it runs from `config.py`'s source).
- **B13. `settings` values.** A number, or a string of digits (a fraction for the two
  intervals). The leader would also read `"1_000"` or `" 120 "`; the chart refuses them.
- **B14. `extraEnv` refuses every `SWARMSCRIBE_` name**, not only the ones the chart owns
  (review m4). Every non-secret setting has a place under `settings`, which also reaches the
  migration Job.

### The checks

- **B15. The console's and the follower's render checks still assert that good things are
  present**, not what each object is (review I1 was about the leader's; its rework was not
  carried over). The follower chart's schema is closed at 17 levels; **the console chart's
  schema refuses no unknown key at all** (review, schema strictness: console 0).
- **B16. Closed.** The console's and the follower's checks had been running on the runner
  image's Helm 3 since they were written. L2's re-review ran every `helm template` call of
  all three checks under Helm 3.22.0 and Helm 4.3.0 (603 calls): the same exit status and
  the same parsed objects in every one; only blank lines and, in three refusals, the order
  of two schema errors differ. All three checks now refuse anything but Helm 4.
- **B17. The leader check runs Helm about 625 times and takes about a minute** on the
  development machine (the first 410 took 18 seconds on a runner). Running the renders in parallel would cut that;
  not done.
- **B18. `KIND_VALUES` in the leader check is a copy of plan L3's `leader-values.yaml`**,
  used until that file exists. L3 deletes the copy (its plan says so).
- **B19. The check validates the guide's example values with kubeconform only when
  `$KUBECONFORM` names it** (CI does). A local run without it prints that they were not
  validated.

### Elsewhere

- **B20. Done in L1's follow-up fixes**: `docker/check-leader-image.sh` no longer says "the
  chart gives none" of the arguments.
- **B21. Thousands of `tmp*/values.yaml` folders in `%TEMP%` on the development machine**,
  left by the three checks before they shared one scratch folder per run (review m6). They
  carry Python's default prefix, not one of the checks' own, so nothing here deletes them.

## From the build: L2, second review

`.superpowers/sdd/2026-10-05-leader-chart/l2-re-review.md`. Its findings R1 and M1 to M8 are
fixed; these are what stays.

- **B22. A branch nobody wrote a render for is still not seen.** The check now compares
  everything rendered, whole, for about thirty renders (`core_whole`), one for each branch
  the templates have today, and compares `values.yaml` with a hand-written `DEFAULTS`. A new
  `if` in a template needs a new render there; nothing tells the author so but the check's
  docstring and the README.
- **B23. A whole map set to null** (`ingress: null`, `networkPolicy.egress.dns: null`) is
  refused by the schema with a plain message (`missing property`). With the schema switched
  off it is still refused, but in Go's words (`nil pointer evaluating ...`): the templates'
  own guard reaches plain values only.
- **B24. Values an operator may ask for, which the chart does not have** (none promised by
  the spec): `priorityClassName`; `subPath` on a storage mount (one folder of a shared
  claim); annotations on the Service and on the chart's ServiceAccount (the way round for
  the second, `serviceAccount.create: false`, is in `values.yaml`); labels on every object;
  `hostAliases`; an Ingress whose certificate is not a Secret (the way round is
  `ingress.enabled: false` and an Ingress of your own).
- **B25. The ingress-nginx example.** The guide now labels it as an example for that
  controller only, untested, and says the Kubernetes project has announced the controller's
  retirement. That last statement was written from memory, not checked against a source
  here: verify it, and give an example for a maintained controller once one has been run.
- **B26. The README repeats defaults in prose** (the probes' numbers, the resources, the
  grace period, the connection count) and nothing ties them to `values.yaml`. The check's
  closing line on failure names the README; it does not compare it.
- **B27. `migrate.activeDeadlineSeconds` (300) equals Helm's default timeout (5 minutes)**,
  so which of the two ends a hung install is a race. L3 records both messages; a default a
  little under five minutes would make it predictable. Also for L3 to try:
  `helm.sh/hook-output-log-policy: hook-failed` on the Job.
- **B28. Statements that are true only once the L1 follow-up fixes are merged** (the pull
  request of `leader-chart-l1-fixes`). In the chart: the Job's comment on exit statuses, the
  Deployment's comment and `values.yaml` on the 10 and 17 seconds, the notes' "about 3
  seconds", and the refusal of a grace period under `preStopSleepSeconds` plus 25 (harmless
  before it). In the guide: exit status 2 with one line for a database that cannot be
  reached and no traceback; the 17-second stop on a silent database; the transfer cut after
  10 seconds; the 503 in about 3 seconds for all callers at once; the pointer to "Upgrading
  a leader" and to `packages/leader/tests/test_migration_compatibility.py`, neither of
  which exists on `main` until that pull request is merged. Merge that one first, or read
  those sentences as describing the next image.
