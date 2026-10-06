# syntax=docker/dockerfile:1
# swarmscribe-leader: the leader (catalogue, consent, jobs, the follower and admin APIs) and
# the `swarmscribe-admin` CLI. No engine, no model libraries, no console, no follower. Build
# from the repository root:
#
#   docker build -t swarmscribe-leader -f docker/leader.Dockerfile .
#
# The base image is pinned by tag and digest, as in console.Dockerfile and
# follower.Dockerfile; move all three together. The new digest comes from
#
#   docker buildx imagetools inspect python:3.12-slim-bookworm

# --- the Python environment -----------------------------------------------------------
FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3 AS build
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
RUN pip install --no-cache-dir "uv==0.12.22"
WORKDIR /app
# Every workspace member's manifest must be present for uv to read the lock; only the leader
# and the one package it imports (protocol) are installed: check-leader-image.sh proves it.
COPY pyproject.toml uv.lock ./
COPY packages/protocol/pyproject.toml packages/protocol/pyproject.toml
COPY packages/engine/pyproject.toml packages/engine/pyproject.toml
COPY packages/leader/pyproject.toml packages/leader/pyproject.toml
COPY packages/console/pyproject.toml packages/console/pyproject.toml
COPY packages/follower/pyproject.toml packages/follower/pyproject.toml
RUN uv sync --frozen --no-dev --package swarmscribe-leader --no-install-workspace
# The sources only, never tests/: a test edit does not rebuild this layer.
COPY packages/protocol/src packages/protocol/src
COPY packages/leader/src packages/leader/src
# --no-editable: the two packages are installed into the environment as wheels (the
# migrations with them), so the final image needs /app/.venv and nothing of the source tree.
# uv's lock file is created world-writable and has no use at run time.
RUN uv sync --frozen --no-dev --package swarmscribe-leader --no-editable \
 && rm -f /app/.venv/.lock

# --- the image --------------------------------------------------------------------------
FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3
# The leader's version (packages/leader/pyproject.toml). check-leader-image.sh compares the
# label with the package installed in the image, and fails when they differ.
ARG VERSION=0.1.0
LABEL org.opencontainers.image.title="swarmscribe-leader" \
      org.opencontainers.image.description="SwarmScribe leader: catalogue, consent, jobs, follower and admin APIs" \
      org.opencontainers.image.source="https://github.com/iamfatness/SwarmScribe" \
      org.opencontainers.image.version="${VERSION}"
# An init as PID 1 (see ENTRYPOINT below). Debian's own package: 24 kB, no dependencies.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tini \
 && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid 10001 leader \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent \
      --shell /usr/sbin/nologin leader
COPY --from=build /app/.venv /app/.venv
# Nothing is written at run time outside the storage volumes you mount (uploads are written
# beside their target, inside the location's own folder): the root filesystem can be
# read-only, and there is no /tmp to provide.
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
USER 10001:10001
EXPOSE 8080
# /healthz says the process answers; it never asks the database (a database outage must not
# make an orchestrator restart every leader). The probe's child is reaped by the init.
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).status == 200 else 1)"]
# tini is PID 1 and the leader its only child. The kernel gives PID 1 no default action for
# a signal, so a SIGTERM that reaches a leader running as PID 1 before uvicorn has installed
# its handlers is dropped: that window is as long as `serve` waits for the database before
# it starts (up to a minute on a database that does not answer), and for all of `migrate`.
# With tini the signal is forwarded: in that window the leader dies of it at once (143).
# Once it is serving it stops by itself (143 again) in bounded steps: 10 s for the requests
# in hand (main.py), then 5 + 1 + 1 s for the background step that is running and the
# database connections (app.py). A database that answers makes that about half a second; one
# that does not cannot make it more than those 17 s. A stop window shorter than that
# (`docker stop` gives 10 s unless told `--time 30`) can still end in a kill (137) in that
# one case, which loses nothing.
STOPSIGNAL SIGTERM
# `serve` is the default; `migrate` is run by overriding the arguments, and the admin CLI by
# overriding the entrypoint:
#   docker run ... swarmscribe-leader migrate
#   docker run --rm --entrypoint swarmscribe-admin swarmscribe-leader --help
# (The CLI keeps a sign-in in a file under the caller's home folder, and this image's user
# has none: sign in from your own machine, not from the leader's container.)
ENTRYPOINT ["/usr/bin/tini", "--", "swarmscribe-leader"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8080"]
