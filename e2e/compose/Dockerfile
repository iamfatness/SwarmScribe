# The leader alone (no engine, no model), for the Compose end-to-end test.
# Build from the repository root: docker build -t swarmscribe-leader:e2e -f e2e/compose/Dockerfile .
FROM python:3.12-slim
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy
RUN pip install --no-cache-dir "uv>=0.5,<1"
WORKDIR /app
COPY . .
RUN uv sync --frozen --no-dev --package swarmscribe-leader
ENV PATH="/app/.venv/bin:${PATH}"
EXPOSE 8080
CMD ["swarmscribe-leader", "serve", "--host", "0.0.0.0", "--port", "8080"]
