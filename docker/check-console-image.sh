#!/usr/bin/env bash
# What the swarmscribe-console image must hold, checked without a database:
#
#   bash docker/check-console-image.sh swarmscribe-console:e2e
#
# The Compose test (e2e/console-compose) checks the rest by running it.
set -euo pipefail

image="${1:?usage: check-console-image.sh <image>}"

fail() {
  echo "FAILED: $*" >&2
  exit 1
}

user="$(docker inspect --format '{{.Config.User}}' "$image")"
[ "$user" = "10001:10001" ] || fail "the image's user is '$user', not 10001:10001"
[ "$(docker run --rm --entrypoint id "$image" -u)" = "10001" ] \
  || fail "the container does not run as uid 10001"

docker inspect --format '{{json .Config.Healthcheck}}' "$image" | grep -q '/healthz' \
  || fail "the image has no HEALTHCHECK on /healthz"

# The console imports the leader and protocol packages as libraries; the engine and its
# model libraries must never come with them (master spec, section 4).
docker run --rm --entrypoint python "$image" -c '
import importlib.util
import sys

banned = ("swarmscribe_engine", "faster_whisper", "ctranslate2", "av", "onnxruntime", "torch",
          "numpy", "tokenizers", "huggingface_hub")
found = [name for name in banned if importlib.util.find_spec(name)]
sys.exit(f"engine or model libraries in the image: {found}" if found else 0)
' || fail "the image carries engine or model libraries"

docker run --rm --entrypoint sh "$image" -c 'test -f "$SWARMSCRIBE_CONSOLE_STATIC_DIR/index.html"' \
  || fail "SWARMSCRIBE_CONSOLE_STATIC_DIR does not hold the web app's index.html"

if docker run --rm --entrypoint sh "$image" -c 'command -v node || command -v npm || command -v uv' >/dev/null; then
  fail "build tools (node, npm or uv) are in the final image"
fi

# Read-only root filesystem, no network, no configuration: the command still starts, names
# what is missing and exits 2, without a traceback.
status=0
output="$(docker run --rm --read-only --network none "$image" migrate 2>&1)" || status=$?
[ "$status" = "2" ] || fail "migrate without configuration exited $status, not 2"
echo "$output" | grep -q 'invalid configuration' \
  || fail "migrate without configuration did not say so: $output"
if echo "$output" | grep -q 'Traceback'; then
  fail "migrate without configuration ended in a traceback"
fi

size="$(docker image inspect --format '{{.Size}}' "$image")"
echo "ok: $image holds the console and the web app, without the engine ($((size / 1000000)) MB)"
