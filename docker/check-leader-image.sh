#!/usr/bin/env bash
# What the swarmscribe-leader image must hold, checked with a throwaway Postgres and no
# follower:
#
#   bash docker/check-leader-image.sh swarmscribe-leader:e2e
#
# The kind test (e2e/leader-kind) checks the rest by running it in a cluster: uploads and
# downloads under a read-only root filesystem, a rolling upgrade, a NetworkPolicy.
set -euo pipefail
# Git Bash on Windows would rewrite /usr/bin/tini and friends into Windows paths.
export MSYS_NO_PATHCONV=1

image="${1:?usage: check-leader-image.sh <image>}"
locked=(--read-only --cap-drop ALL --security-opt no-new-privileges)

fail() {
  echo "FAILED: $*" >&2
  exit 1
}

label() {  # the value of one of the image's labels; empty when it is not set
  docker inspect --format "{{with .Config.Labels}}{{index . \"$1\"}}{{end}}" "$image"
}

# --- what it says it is --------------------------------------------------------------------
[ "$(label org.opencontainers.image.title)" = "swarmscribe-leader" ] \
  || fail "the title label is '$(label org.opencontainers.image.title)', not swarmscribe-leader"
[ "$(label org.opencontainers.image.source)" = "https://github.com/iamfatness/SwarmScribe" ] \
  || fail "the source label is '$(label org.opencontainers.image.source)', not the repository"
case "$(label org.opencontainers.image.description)" in
  *SwarmScribe*leader*) ;;
  *) fail "the description label is '$(label org.opencontainers.image.description)'" ;;
esac
installed="$(docker run --rm --entrypoint python "$image" -c \
  'from importlib.metadata import version; print(version("swarmscribe-leader"))')"
[ -n "$installed" ] || fail "the image does not say which version of the leader it holds"
[ "$(label org.opencontainers.image.version)" = "$installed" ] \
  || fail "the version label is '$(label org.opencontainers.image.version)', the leader in the image is $installed"

# --- who it runs as, and what is PID 1 ---------------------------------------------------
user="$(docker inspect --format '{{.Config.User}}' "$image")"
[ "$user" = "10001:10001" ] || fail "the image's user is '$user', not 10001:10001"
[ "$(docker run --rm --entrypoint id "$image" -u)" = "10001" ] \
  || fail "the container does not run as uid 10001"
entrypoint="$(docker inspect --format '{{json .Config.Entrypoint}}' "$image")"
[ "$entrypoint" = '["/usr/bin/tini","--","swarmscribe-leader"]' ] \
  || fail "the entrypoint is $entrypoint, not tini and the leader"
# `serve` on 0.0.0.0:8080 is what runs when no arguments are given (the chart gives none).
arguments="$(docker inspect --format '{{json .Config.Cmd}}' "$image")"
[ "$arguments" = '["serve","--host","0.0.0.0","--port","8080"]' ]   || fail "the default arguments are $arguments, not serve on 0.0.0.0:8080"
docker run --rm --entrypoint /usr/bin/tini "$image" --version >/dev/null \
  || fail "tini does not run in the image"
docker inspect --format '{{json .Config.Healthcheck}}' "$image" | grep -q '/healthz' \
  || fail "the image has no HEALTHCHECK on /healthz"
if docker inspect --format '{{json .Config.Healthcheck}}' "$image" | grep -q '/readyz'; then
  fail "the HEALTHCHECK asks /readyz: a database outage would mark every leader unhealthy"
fi
# The chart's preStop hook runs `sleep`.
docker run --rm --entrypoint sleep "$image" 0 || fail "there is no sleep in the image (the chart's preStop hook)"

# --- what it holds, and what it must not ---------------------------------------------------
# The leader imports the protocol package and nothing else of SwarmScribe; the engine and
# its model libraries, the console and the follower must never come with it (master spec,
# section 12: "slim Python image, no model libraries").
docker run --rm --entrypoint python "$image" -c '
import importlib.util
import sys

needed = ("swarmscribe_leader", "swarmscribe_protocol", "alembic", "asyncpg", "uvicorn")
missing = [name for name in needed if not importlib.util.find_spec(name)]
banned = ("swarmscribe_engine", "swarmscribe_console", "swarmscribe_follower", "faster_whisper",
          "ctranslate2", "av", "onnxruntime", "torch", "numpy", "tokenizers", "huggingface_hub",
          "pytest")
found = [name for name in banned if importlib.util.find_spec(name)]
sys.exit(f"missing: {missing}; must not be there: {found}" if missing or found else 0)
' || fail "the image does not hold exactly the leader and its libraries"
# The migrations travel inside the wheel: without them `migrate` has nothing to run.
docker run --rm --entrypoint python "$image" -c '
import sys
from swarmscribe_leader.db.migrate import MIGRATIONS, head_revision
versions = sorted(p.name for p in (MIGRATIONS / "versions").glob("*.py"))
sys.exit(0 if versions and head_revision() and (MIGRATIONS / "script.py.mako").is_file() else
         f"migrations in the image: {versions}")
' || fail "the migrations are not in the image"
if docker run --rm --entrypoint sh "$image" -c 'command -v uv || test -e /app/packages || test -e /app/uv.lock' >/dev/null; then
  fail "build tools or the source tree are in the final image"
fi

# --- without configuration, read-only, no network ---------------------------------------
status=0
output="$(docker run --rm "${locked[@]}" --network none "$image" migrate 2>&1)" || status=$?
[ "$status" = "2" ] || fail "migrate without configuration exited $status, not 2"
grep -q 'invalid configuration' <<<"$output" \
  || fail "migrate without configuration did not say so: $output"
if grep -q 'Traceback' <<<"$output"; then
  fail "migrate without configuration ended in a traceback"
fi
docker run --rm "${locked[@]}" --network none "$image" --help >/dev/null \
  || fail "the leader does not run with --read-only --cap-drop ALL"
docker run --rm "${locked[@]}" --network none --entrypoint swarmscribe-admin "$image" --help >/dev/null \
  || fail "swarmscribe-admin does not run in the image"

# --- with a database ----------------------------------------------------------------------
# A throwaway Postgres on a network of its own; the leader reaches it by its container
# name, and nothing is published on the host.
pg="leader-check-pg-$$"
name="leader-check-$$"
net="leader-check-net-$$"
cleanup() {
  # -v: postgres keeps its data in an anonymous volume, which would otherwise be left behind.
  docker rm -f -v "$name" "$name-ahead" "$name-early" "$pg" >/dev/null 2>&1 || true
  docker network rm "$net" >/dev/null 2>&1 || true
}
trap cleanup EXIT
docker network create "$net" >/dev/null
docker run -d --name "$pg" --network "$net" -e POSTGRES_PASSWORD=pw -e POSTGRES_DB=leader \
  postgres:16 >/dev/null
for _ in $(seq 1 60); do
  docker exec "$pg" pg_isready -h 127.0.0.1 -U postgres >/dev/null 2>&1 && break
  sleep 1
done
leader_env=(-e "SWARMSCRIBE_DATABASE_URL=postgresql://postgres:pw@$pg:5432/leader"
  -e SWARMSCRIBE_PUBLIC_URL=http://localhost:8080
  -e SWARMSCRIBE_LINK_KEY=check-only-link-key-0123456789abcdef)
sql() { docker exec "$pg" psql -U postgres -d leader -t -A -c "$1"; }

# A leader never starts on a database it has not migrated.
status=0
output="$(docker run --rm "${locked[@]}" --network "$net" "${leader_env[@]}" "$image" serve 2>&1)" || status=$?
[ "$status" = "2" ] || fail "serve on an unmigrated database exited $status, not 2"
grep -q 'run `swarmscribe-leader migrate`' <<<"$output" \
  || fail "serve on an unmigrated database did not say to migrate: $output"

output="$(docker run --rm "${locked[@]}" --network "$net" "${leader_env[@]}" "$image" migrate 2>&1)" \
  || fail "migrate against a database failed: $output"
head="$(sql 'select version_num from alembic_version')"
grep -q -x "database is at revision $head" <<<"$output" \
  || fail "migrate did not report the revision it reached ($head): $output"
# A second run finds the schema current and does nothing.
docker run --rm "${locked[@]}" --network "$net" "${leader_env[@]}" "$image" migrate >/dev/null \
  || fail "a second migrate failed"

docker run -d --name "$name" "${locked[@]}" --network "$net" "${leader_env[@]}" \
  "$image" serve >/dev/null
probe() {  # path -> the status code, asked from inside the container
  docker exec "$name" python -c "
import sys, urllib.request, urllib.error
try:
    print(urllib.request.urlopen('http://127.0.0.1:8080' + sys.argv[1], timeout=8).status)
except urllib.error.HTTPError as e:
    print(e.code)
" "$1"
}
for _ in $(seq 1 30); do [ "$(probe /healthz 2>/dev/null)" = "200" ] && break; sleep 1; done
[ "$(probe /healthz)" = "200" ] || fail "/healthz is not 200: $(docker logs "$name" 2>&1 | tail -5)"
[ "$(probe /readyz)" = "200" ] || fail "/readyz is not 200 on a migrated database"
[ "$(probe /v1/admin/login-config)" = "200" ] || fail "/v1/admin/login-config is not 200"
# PID 1 is the init, and the leader is what it started.
pid1="$(docker exec "$name" cat /proc/1/cmdline | xargs -0 echo)"
grep -q -x '/usr/bin/tini -- swarmscribe-leader serve' <<<"$pid1" \
  || fail "PID 1 is not tini running the leader: $pid1"

# A rolling upgrade: a newer leader has migrated the database. The one that is serving stays
# Ready (or every old replica would drop out of the Service at once) ...
sql "update alembic_version set version_num = '9999_newer_leader'" >/dev/null
sleep 2  # past the one-second reuse of the last readiness answer
[ "$(probe /readyz)" = "200" ] || fail "/readyz is not 200 when the database is AHEAD of this leader"
# ... and one that is starting refuses to: an image rolled back after a migration stays down.
status=0
output="$(docker run --name "$name-ahead" "${locked[@]}" --network "$net" "${leader_env[@]}" "$image" serve 2>&1)" || status=$?
[ "$status" = "2" ] || fail "serve on a database that is ahead exited $status, not 2"
grep -q 'database is ahead of this leader' <<<"$output" \
  || fail "serve on a database that is ahead did not say so: $output"
# A schema that is behind is not ready: 0001 is a revision every leader knows.
sql "update alembic_version set version_num = '0001'" >/dev/null
sleep 2
[ "$(probe /readyz)" = "503" ] || fail "/readyz is not 503 when the database is BEHIND this leader"
sql "update alembic_version set version_num = '$head'" >/dev/null
sleep 2
[ "$(probe /readyz)" = "200" ] || fail "/readyz did not come back to 200"

# A database that stops answering: 503 inside the probe's timeout, and the process stays up.
docker pause "$pg" >/dev/null
sleep 2
started=$SECONDS
[ "$(probe /readyz)" = "503" ] || { docker unpause "$pg" >/dev/null; fail "/readyz is not 503 while the database is down"; }
[ $((SECONDS - started)) -le 6 ] || { docker unpause "$pg" >/dev/null; fail "/readyz took more than 6 s to say the database is down (its own limit is 3 s)"; }
[ "$(probe /healthz)" = "200" ] || { docker unpause "$pg" >/dev/null; fail "/healthz is not 200 while the database is down"; }

# A stop while a leader is still waiting for that database: tini forwards the signal and the
# container ends at once (143), not when the stop window closes (137).
docker run -d --name "$name-early" "${locked[@]}" --network "$net" "${leader_env[@]}" \
  "$image" serve >/dev/null
sleep 2
started=$SECONDS
docker stop --time 30 "$name-early" >/dev/null
took=$((SECONDS - started))
status="$(docker inspect --format '{{.State.ExitCode}}' "$name-early")"
docker unpause "$pg" >/dev/null
case "$status" in
  143 | 0 | 2) ;;
  *) fail "a stop while waiting for the database exited $status (137: the stop was lost and it was killed)" ;;
esac
[ "$took" -lt 10 ] || fail "a stop while waiting for the database took $took s"

# A stop while serving: the leader ends by itself, well inside the window. 0, or 143 when
# uvicorn, having finished its requests, hands the signal back to the default action; never
# 137, which is Docker's kill at the end of the window.
for _ in $(seq 1 15); do [ "$(probe /readyz 2>/dev/null)" = "200" ] && break; sleep 1; done
started=$SECONDS
docker stop --time 30 "$name" >/dev/null
took=$((SECONDS - started))
status="$(docker inspect --format '{{.State.ExitCode}}' "$name")"
case "$status" in
  0 | 143) ;;
  *) fail "docker stop while serving exited $status, not 0 or 143 (137 is a kill)" ;;
esac
[ "$took" -lt 15 ] || fail "docker stop while serving took $took s"
# No secret in what it logged: neither the link key nor the database password.
if docker logs "$name" 2>&1 | grep -q -e 'check-only-link-key' -e ':pw@'; then
  fail "the leader's log holds the link key or the database password"
fi

size="$(docker image inspect --format '{{.Size}}' "$image")"
echo "ok: $image holds the leader and swarmscribe-admin, without the engine, console or follower ($((size / 1000000)) MB)"
