# syntax=docker/dockerfile:1
# swarmscribe-follower: the agent that takes recordings from a leader and transcribes them
# (follower spec, section 8.1). Build from the repository root; `cpu` is the default target:
#
#   docker build -t swarmscribe-follower:cpu  --target cpu  -f docker/follower.Dockerfile .
#   docker build -t swarmscribe-follower:cuda --target cuda -f docker/follower.Dockerfile .
#
# Without MODELS the follower downloads its model on first start into /models. With it, the
# models are downloaded now, checked against docker/models.lock.json, and the image never
# asks Hugging Face for anything (SWARMSCRIBE_FOLLOWER_OFFLINE=1); the first name is the
# start-up model. MODELS is names separated by commas, with no spaces (the build fails
# otherwise):
#
#   docker build --build-arg MODELS=large-v3 -t swarmscribe-follower:cuda-large-v3 \
#     --target cuda -f docker/follower.Dockerfile .
#
# The base image is pinned by tag and digest, as in console.Dockerfile; move both together.

FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3 AS manifests
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
RUN pip install --no-cache-dir "uv==0.12.22"
WORKDIR /app
# Every workspace member's manifest must be present for uv to read the lock; only the
# follower and the two packages it imports (engine, protocol) are installed. The leader, the
# console and their database and web libraries never are: check-follower-image.sh proves it.
COPY pyproject.toml uv.lock ./
COPY packages/protocol/pyproject.toml packages/protocol/pyproject.toml
COPY packages/engine/pyproject.toml packages/engine/pyproject.toml
COPY packages/leader/pyproject.toml packages/leader/pyproject.toml
COPY packages/console/pyproject.toml packages/console/pyproject.toml
COPY packages/follower/pyproject.toml packages/follower/pyproject.toml

# --- the third-party libraries, without GPU libraries -------------------------------------
FROM manifests AS deps-cpu
RUN uv sync --frozen --no-dev --package swarmscribe-follower --no-install-workspace

# --- the Python environment ---------------------------------------------------------------
FROM deps-cpu AS build-cpu
COPY packages/protocol packages/protocol
COPY packages/engine packages/engine
COPY packages/follower packages/follower
# --no-editable: the three packages are installed as wheels, so the final image needs
# /app/.venv and nothing of the source tree. uv's lock file is created world-writable.
RUN uv sync --frozen --no-dev --package swarmscribe-follower --no-editable \
 && rm -f /app/.venv/.lock

# --- the same, with cuBLAS from the nvidia wheel (the `cuda` extra) ----------------------
FROM manifests AS build-cuda
RUN uv sync --frozen --no-dev --package swarmscribe-follower --extra cuda --no-install-workspace
COPY packages/protocol packages/protocol
COPY packages/engine packages/engine
COPY packages/follower packages/follower
RUN uv sync --frozen --no-dev --package swarmscribe-follower --extra cuda --no-editable \
 && rm -f /app/.venv/.lock

# --- the baked models (an empty folder when MODELS is empty) ------------------------------
# From the libraries alone, not from the source: a code change does not download them again.
FROM deps-cpu AS models
ARG MODELS=""
# After `--`: whatever MODELS holds is a list of names, never an option of the script.
RUN --mount=type=bind,source=docker/fetch_models.py,target=/fetch/fetch_models.py \
    --mount=type=bind,source=docker/models.lock.json,target=/fetch/models.lock.json \
    /app/.venv/bin/python /fetch/fetch_models.py \
      --lock /fetch/models.lock.json --into /models -- "${MODELS}"

# --- what both images share --------------------------------------------------------------
FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3 AS runtime
# The follower's version (packages/follower/pyproject.toml). check-follower-image.sh compares
# the label with the package installed in the image, and fails when they differ.
ARG VERSION=0.1.0
LABEL org.opencontainers.image.title="swarmscribe-follower" \
      org.opencontainers.image.description="SwarmScribe follower: transcribes recordings for a leader" \
      org.opencontainers.image.source="https://github.com/iamfatness/SwarmScribe" \
      org.opencontainers.image.version="${VERSION}"
# An init as PID 1 (see ENTRYPOINT below). Debian's own package: 24 kB, no dependencies.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tini \
 && rm -rf /var/lib/apt/lists/*
# The state and scratch folders are the follower's alone (0700: the credential file is
# refused in a folder others can write to). /models is world-readable: it holds no secret.
RUN groupadd --gid 10001 follower \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /var/lib/swarmscribe-follower \
      --shell /usr/sbin/nologin follower \
 && install -d -o 10001 -g 10001 -m 0700 /var/lib/swarmscribe-follower /scratch \
 && install -d -o 10001 -g 10001 -m 0755 /models
ARG MODELS=""
# "1" when a model is baked, else empty: an ENV cannot hold a condition itself.
ARG BAKED=${MODELS:+1}
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/var/lib/swarmscribe-follower \
    SWARMSCRIBE_FOLLOWER_STATE_DIR=/var/lib/swarmscribe-follower \
    SWARMSCRIBE_FOLLOWER_SCRATCH_DIR=/scratch \
    SWARMSCRIBE_FOLLOWER_MODEL_DIR=/models \
    SWARMSCRIBE_FOLLOWER_STARTUP_MODEL=${MODELS%%,*} \
    SWARMSCRIBE_FOLLOWER_OFFLINE=${BAKED:-0} \
    HF_HUB_CACHE=/models \
    HF_HUB_OFFLINE=${BAKED:-0} \
    SWARMSCRIBE_FOLLOWER_HEALTH_ADDR=127.0.0.1:9108
# HF_HUB_*: the follower sets them itself; the engine CLI in the image (swarmscribe-engine)
# reads only these two, so a baked image runs it offline against the same cache.
# Before the environment: a code change does not move a 3 GB model layer.
COPY --from=models --chown=10001:10001 /models /models
WORKDIR /var/lib/swarmscribe-follower
USER 10001:10001
# Declared, so that `docker run --read-only` works as it is and neither the credential nor a
# recording is ever written into the container's own layer. /models is not declared: a
# volume there would copy a baked model on every start. Mount one to keep downloads.
VOLUME ["/var/lib/swarmscribe-follower", "/scratch"]
# /healthz says the follower's threads are alive; it answers while the model loads and
# while the leader is away. Loopback only: nothing outside the container can reach it. A
# follower started with the listener turned off (the variable set to nothing) is not probed.
# The probe's `python -c` child is reaped by the init every time.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3   CMD ["python", "-c", "import os, sys, urllib.request; a = os.environ.get('SWARMSCRIBE_FOLLOWER_HEALTH_ADDR', ''); sys.exit(0 if not a or urllib.request.urlopen('http://' + a + '/healthz', timeout=3).status == 200 else 1)"]
# tini is PID 1 and the follower its only child; no shell in between. The kernel gives PID 1
# no default action for a signal, so a SIGTERM that reached a follower running as PID 1
# before Python had installed its handlers was dropped: `docker stop` just after `docker run`
# was ignored, and the container was killed (137) at the end of the stop window, by then
# perhaps in the middle of a job. The follower now installs its handlers before it imports
# anything (entry.py) and then exits 0 on a stop; tini covers the tens of milliseconds
# before that, in which the interpreter itself starts: it forwards the signal, the child
# dies of it and the container exits 143. It also reaps what the follower leaves behind.
STOPSIGNAL SIGTERM
ENTRYPOINT ["/usr/bin/tini", "--", "swarmscribe-follower"]
CMD ["run"]

# --- swarmscribe-follower:cuda -------------------------------------------------------------
FROM runtime AS cuda
COPY --from=build-cuda /app/.venv /app/.venv
# CTranslate2 loads cuBLAS by name at the first inference; the driver's own libraries
# (libcuda) and nvidia-smi come from the NVIDIA container runtime (`--gpus all`). This image
# is for a GPU: without one it says so and exits 3, where `auto` would quietly use the CPU.
ENV LD_LIBRARY_PATH=/app/.venv/lib/python3.12/site-packages/nvidia/cublas/lib \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility \
    SWARMSCRIBE_FOLLOWER_DEVICE=cuda

# --- swarmscribe-follower:cpu (last: the default target) ----------------------------------
FROM runtime AS cpu
COPY --from=build-cpu /app/.venv /app/.venv
