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
