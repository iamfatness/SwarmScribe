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

# Read-only operation with every capability dropped and no tmpfs at all: the image needs no
# writable path. `--help` goes through the whole import chain.
docker run --rm --read-only --cap-drop ALL --security-opt no-new-privileges --network none   "$image" --help >/dev/null   || fail "the console does not run with --read-only --cap-drop ALL"

# Probes answer HEAD like GET and never fall through to the web app (the unit tests build the
# app with a static folder; this is the image itself). A throwaway Postgres supplies the
# database; the console shares its network namespace, so it reaches it on 127.0.0.1.
pg="console-check-pg-$$"
name="console-check-$$"
cleanup() { docker rm -f "$name" "$pg" >/dev/null 2>&1 || true; }
trap cleanup EXIT
docker run -d --name "$pg" -e POSTGRES_PASSWORD=pw -e POSTGRES_DB=console postgres:16 >/dev/null
for _ in $(seq 1 60); do
  docker exec "$pg" pg_isready -h 127.0.0.1 -U postgres >/dev/null 2>&1 && break
  sleep 1
done
console_env=(-e SWARMSCRIBE_CONSOLE_DATABASE_URL=postgresql://postgres:pw@127.0.0.1:5432/console
  -e SWARMSCRIBE_CONSOLE_PUBLIC_URL=http://localhost:8080
  -e SWARMSCRIBE_CONSOLE_KEY=AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8
  -e SWARMSCRIBE_CONSOLE_ENTRA_TENANT_ID=0f0e0d0c-0b0a-4908-8706-050403020100
  -e SWARMSCRIBE_CONSOLE_ENTRA_CLIENT_ID=11111111-1111-4111-8111-111111111111
  -e SWARMSCRIBE_CONSOLE_ENTRA_CLIENT_SECRET=check-only-secret)
docker run --rm --read-only --cap-drop ALL --network "container:$pg" "${console_env[@]}"   "$image" migrate >/dev/null || fail "migrate against a database failed"
docker run -d --name "$name" --read-only --cap-drop ALL --network "container:$pg"   "${console_env[@]}" "$image" serve --host 127.0.0.1 >/dev/null
probe() {  # method path -> the status code, asked from inside the container
  docker exec "$name" python -c "
import sys, urllib.request, urllib.error
class Stay(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k): return None
req = urllib.request.Request('http://127.0.0.1:8080' + sys.argv[2], method=sys.argv[1])
try:
    print(urllib.request.build_opener(Stay).open(req, timeout=5).status)
except urllib.error.HTTPError as e:
    print(e.code)
" "$1" "$2"
}
for _ in $(seq 1 30); do [ "$(probe GET /healthz 2>/dev/null)" = "200" ] && break; sleep 1; done
[ "$(probe HEAD /healthz)" = "200" ] || fail "HEAD /healthz is not 200"
[ "$(probe HEAD /readyz)" = "200" ] || fail "HEAD /readyz is not 200 on a migrated database"
[ "$(probe GET /readyz/)" != "200" ] || fail "/readyz/ answers 200 (the web app's page)"
docker pause "$pg" >/dev/null
sleep 2  # past the one-second reuse of the last readiness answer
[ "$(probe HEAD /readyz)" = "503" ] || fail "HEAD /readyz is not 503 while the database is down"
[ "$(probe GET /readyz)" = "503" ] || fail "GET /readyz is not 503 while the database is down"
docker unpause "$pg" >/dev/null

size="$(docker image inspect --format '{{.Size}}' "$image")"
echo "ok: $image holds the console and the web app, without the engine ($((size / 1000000)) MB)"
