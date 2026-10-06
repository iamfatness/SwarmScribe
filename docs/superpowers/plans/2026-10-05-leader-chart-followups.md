# Leader chart: follow-ups

From the leader chart spec (section 16) and from building plans L1 to L3. None blocks merge.
Plan L3 adds "From the build: L1" and "From the build: L3", and marks what its runs settle.

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
- **B5. A stop during a database outage with the chart's grace period**, and **sign-in
  behind a blocked egress or a proxy**, on a cluster (L1 review I2, M1). The guide words both
  so that they hold with or without the leader's own limits on a stop and on a connection.

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
- **B16. Those two checks had been running on the runner image's Helm 3**, unnoticed, since
  they were written: `uv run` put `/usr/local/bin` ahead of the pinned Helm's folder for its
  child. They only call `helm template`, which Helm 3 has, so they passed. All three now
  name the binary and refuse anything but Helm 4 (`deploy/helm/helm_tool.py`); nothing was
  found to differ, but nothing was compared either.
- **B17. The leader check runs Helm about 400 times and takes some 40 seconds** on the
  development machine. Running the renders in parallel would cut that;
  not done.
- **B18. `KIND_VALUES` in the leader check is a copy of plan L3's `leader-values.yaml`**,
  used until that file exists. L3 deletes the copy (its plan says so).
- **B19. The check validates the guide's example values with kubeconform only when
  `$KUBECONFORM` names it** (CI does). A local run without it prints that they were not
  validated.

### Elsewhere

- **B20. `docker/check-leader-image.sh:48` says "the chart gives none"** of the arguments;
  the chart passes `serve --host 0.0.0.0 --port <port>` (L1 review M5). Left to the L1
  follow-up fixes, which touch that script.
- **B21. Thousands of `tmp*/values.yaml` folders in `%TEMP%` on the development machine**,
  left by the three checks before they shared one scratch folder per run (review m6). They
  carry Python's default prefix, not one of the checks' own, so nothing here deletes them.
