# The leader alone (no engine, no model), for the Compose end-to-end test.
# Build from the repository root: docker build -t swarmscribe-leader:e2e -f e2e/compose/Dockerfile .
FROM python:3.12-slim
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy
RUN pip install --no-cache-dir "uv>=0.5,<1"
WORKDIR /app
# Dependencies first, from the manifests alone: this layer is rebuilt only when they change.
COPY pyproject.toml uv.lock ./
COPY packages/protocol/pyproject.toml packages/protocol/pyproject.toml
COPY packages/engine/pyproject.toml packages/engine/pyproject.toml
COPY packages/leader/pyproject.toml packages/leader/pyproject.toml
RUN uv sync --frozen --no-dev --package swarmscribe-leader --no-install-workspace
COPY . .
RUN uv sync --frozen --no-dev --package swarmscribe-leader
ENV PATH="/app/.venv/bin:${PATH}"
EXPOSE 8080
CMD ["swarmscribe-leader", "serve", "--host", "0.0.0.0", "--port", "8080"]
