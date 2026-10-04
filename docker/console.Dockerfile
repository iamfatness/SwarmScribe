# syntax=docker/dockerfile:1
# swarmscribe-console: the fleet console's backend serving the built web app (fleet console
# spec, section 8). Build from the repository root:
#
#   docker build -t swarmscribe-console -f docker/console.Dockerfile .
#
# Base images are pinned by tag and digest: the tag says what it is, the digest makes the
# build repeatable. To move one, change both together; the new digest comes from
#
#   docker buildx imagetools inspect python:3.12-slim-bookworm

# --- the web app ----------------------------------------------------------------------
FROM node:24.15.0-bookworm-slim@sha256:4e6b70dd6cbfc88c8157ba19aa3d9f9cce6ba4703576d55459e45efcbc9c5f5d AS web
WORKDIR /web
# Dependencies first, from the manifests alone: this layer is rebuilt only when they change.
COPY packages/console-web/package.json packages/console-web/package-lock.json packages/console-web/.npmrc ./
RUN npm ci
COPY packages/console-web/ ./
# `vite build`, then scripts/check-dist.mjs refuses a build the console would serve wrongly.
RUN npm run build

# --- the Python environment -----------------------------------------------------------
FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3 AS build
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
RUN pip install --no-cache-dir "uv==0.12.22"
WORKDIR /app
# Every workspace member's manifest must be present for uv to read the lock; only the
# console and the two packages it imports (leader, protocol) are installed. The engine and
# its model libraries are never installed: check-console-image.sh proves it.
COPY pyproject.toml uv.lock ./
COPY packages/protocol/pyproject.toml packages/protocol/pyproject.toml
COPY packages/engine/pyproject.toml packages/engine/pyproject.toml
COPY packages/leader/pyproject.toml packages/leader/pyproject.toml
COPY packages/console/pyproject.toml packages/console/pyproject.toml
RUN uv sync --frozen --no-dev --package swarmscribe-console --no-install-workspace
COPY packages/protocol packages/protocol
COPY packages/leader packages/leader
COPY packages/console packages/console
# --no-editable: the three packages are installed into the environment as wheels, so the
# final image needs /app/.venv and nothing of the source tree.
RUN uv sync --frozen --no-dev --package swarmscribe-console --no-editable  && rm -f /app/.venv/.lock
# uv's lock file is created world-writable and has no use at run time.

# --- the image --------------------------------------------------------------------------
FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3
LABEL org.opencontainers.image.title="swarmscribe-console" \
      org.opencontainers.image.description="SwarmScribe fleet console: backend and web app" \
      org.opencontainers.image.source="https://github.com/iamfatness/SwarmScribe"
RUN groupadd --gid 10001 console \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent \
      --shell /usr/sbin/nologin console
COPY --from=build /app/.venv /app/.venv
COPY --from=web /web/dist /app/web
# Nothing is written at run time: the root filesystem can be read-only.
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SWARMSCRIBE_CONSOLE_STATIC_DIR=/app/web
WORKDIR /app
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).status == 200 else 1)"]
# `serve` is the default; `migrate` and `admins ...` are run by overriding the arguments:
#   docker run ... swarmscribe-console migrate
ENTRYPOINT ["swarmscribe-console"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8080"]
