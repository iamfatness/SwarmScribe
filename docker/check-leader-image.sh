#!/usr/bin/env bash
# What the swarmscribe-leader image must hold, checked with a throwaway Postgres and no
# follower:
#
#   bash docker/check-leader-image.sh swarmscribe-leader:e2e
#
# The kind test (e2e/leader-kind) checks the rest by running it in a cluster: uploads and
# downloads under a read-only root filesystem, a rolling upgrade, a NetworkPolicy.
#
# Every step has a limit. A leader that keeps running where it should have refused, or that
# does not stop, fails the check with what was being checked and its last log lines; it
# never leaves the script waiting.
set -euo pipefail
# Git Bash on Windows would rewrite /usr/bin/tini and friends into Windows paths.
export MSYS_NO_PATHCONV=1

image="${1:?usage: check-leader-image.sh <image>}"
locked=(--read-only --cap-drop ALL --security-opt no-new-privileges)
command -v timeout >/dev/null || { echo "FAILED: this script needs timeout (coreutils)" >&2; exit 1; }

# Everything this script creates is named after its own process and removed when it ends.
name="leader-check-$$"
pg="leader-check-pg-$$"
net="leader-check-net-$$"
created=()  # containers
watched=()  # those whose last log lines a failure shows
cleanup() {
  # -v: postgres keeps its data in an anonymous volume, which would otherwise be left behind.
  if [ "${#created[@]}" -gt 0 ]; then
    docker unpause "$pg" >/dev/null 2>&1 || true
    docker rm -f -v "${created[@]}" >/dev/null 2>&1 || true
  fi
  docker network rm "$net" >/dev/null 2>&1 || true
}
trap cleanup EXIT

fail() {
  echo "FAILED: $*" >&2
  local container
  for container in ${watched[@]+"${watched[@]}"}; do
    docker inspect "$container" >/dev/null 2>&1 || continue
    echo "--- $container ($(docker inspect --format '{{.State.Status}}' "$container")), its last log lines:" >&2
    timeout 20 docker logs --tail 20 "$container" 2>&1 | sed 's/^/    /' >&2 || true
  done
  exit 1
}

# run_to_end <seconds> <what is being checked> <docker run arguments...>
# Runs a container to its end, for at most that long. Sets $status (its exit status),
# $stdout, and $output (stdout and stderr together). A container that is still running at
# the limit fails the check: nothing here waits on a `docker run` in the foreground.
step=0
run_to_end() {
  local limit="$1" what="$2" container
  shift 2
  step=$((step + 1))
  container="$name-step$step"
  created+=("$container")
  watched=("$container")
  docker run -d --name "$container" "$@" >/dev/null || fail "$what: the container did not start"
  status="$(timeout "$limit" docker wait "$container")" \
    || fail "$what: still running after $limit s (it should have ended by itself)"
  stdout="$(docker logs "$container" 2>/dev/null)"
  output="$(docker logs "$container" 2>&1)"
  docker rm -f -v "$container" >/dev/null
  watched=()
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
run_to_end 60 "asking the image for the leader's version" --entrypoint python "$image" -c \
  'from importlib.metadata import version; print(version("swarmscribe-leader"))'
installed="$stdout"
[ -n "$installed" ] || fail "the image does not say which version of the leader it holds"
[ "$(label org.opencontainers.image.version)" = "$installed" ] \
  || fail "the version label is '$(label org.opencontainers.image.version)', the leader in the image is $installed"

# --- who it runs as, and what is PID 1 ---------------------------------------------------
user="$(docker inspect --format '{{.Config.User}}' "$image")"
[ "$user" = "10001:10001" ] || fail "the image's user is '$user', not 10001:10001"
run_to_end 60 "asking the container who it runs as" --entrypoint id "$image" -u
[ "$stdout" = "10001" ] || fail "the container does not run as uid 10001 but as '$stdout'"
entrypoint="$(docker inspect --format '{{json .Config.Entrypoint}}' "$image")"
[ "$entrypoint" = '["/usr/bin/tini","--","swarmscribe-leader"]' ] \
  || fail "the entrypoint is $entrypoint, not tini and the leader"
# `serve` on 0.0.0.0:8080 is what runs when no arguments are given: `docker run` as the
# README writes it relies on that. (The chart passes the same arguments itself, with its
# own port.)
arguments="$(docker inspect --format '{{json .Config.Cmd}}' "$image")"
[ "$arguments" = '["serve","--host","0.0.0.0","--port","8080"]' ] \
  || fail "the default arguments are $arguments, not serve on 0.0.0.0:8080"
run_to_end 60 "running tini" --entrypoint /usr/bin/tini "$image" --version
[ "$status" = "0" ] || fail "tini does not run in the image: $output"
docker inspect --format '{{json .Config.Healthcheck}}' "$image" | grep -q '/healthz' \
  || fail "the image has no HEALTHCHECK on /healthz"
if docker inspect --format '{{json .Config.Healthcheck}}' "$image" | grep -q '/readyz'; then
  fail "the HEALTHCHECK asks /readyz: a database outage would mark every leader unhealthy"
fi
# The chart's preStop hook runs `sleep`.
run_to_end 60 "running sleep" --entrypoint sleep "$image" 0
[ "$status" = "0" ] || fail "there is no sleep in the image (the chart's preStop hook): $output"

# --- what it holds, and what it must not ---------------------------------------------------
# The leader imports the protocol package and nothing else of SwarmScribe; the engine and
# its model libraries, the console and the follower must never come with it (master spec,
# section 12: "slim Python image, no model libraries").
run_to_end 60 "listing the libraries in the image" --entrypoint python "$image" -c '
import importlib.util
import sys

needed = ("swarmscribe_leader", "swarmscribe_protocol", "alembic", "asyncpg", "uvicorn")
missing = [name for name in needed if not importlib.util.find_spec(name)]
banned = ("swarmscribe_engine", "swarmscribe_console", "swarmscribe_follower", "faster_whisper",
          "ctranslate2", "av", "onnxruntime", "torch", "numpy", "tokenizers", "huggingface_hub",
          "pytest")
found = [name for name in banned if importlib.util.find_spec(name)]
sys.exit(f"missing: {missing}; must not be there: {found}" if missing or found else 0)
'
[ "$status" = "0" ] || fail "the image does not hold exactly the leader and its libraries: $output"
# The migrations travel inside the wheel: without them `migrate` has nothing to run.
run_to_end 60 "listing the migrations in the image" --entrypoint python "$image" -c '
import sys
from swarmscribe_leader.db.migrate import MIGRATIONS, head_revision
versions = sorted(p.name for p in (MIGRATIONS / "versions").glob("*.py"))
sys.exit(0 if versions and head_revision() and (MIGRATIONS / "script.py.mako").is_file() else
         f"migrations in the image: {versions}")
'
[ "$status" = "0" ] || fail "the migrations are not in the image: $output"
# Nothing of the build or the source tree, wherever it was put: /app holds the environment
# and nothing else; no file outside it is named after SwarmScribe or is a test or a project
# file; and the leader's own packages in the environment carry no tests.
run_to_end 120 "looking for build tools, sources and tests in the image" --entrypoint sh "$image" -c '
command -v uv
[ "$(ls -A /app | tr "\n" " ")" = ".venv " ] || echo "/app holds: $(ls -A /app | tr "\n" " ")"
find / -xdev \( -path /proc -o -path /sys -o -path /dev -o -path /app/.venv \) -prune -o \
  \( -iname "*swarmscribe*" -o -name conftest.py -o -name "test_*.py" -o -name pyproject.toml \
     -o -name uv.lock \) -print 2>/dev/null
find /app/.venv/lib/python*/site-packages/swarmscribe_* \
  \( -name conftest.py -o -name "test_*.py" -o -name tests \) -print 2>/dev/null
find /app/.venv/lib/python*/site-packages -maxdepth 1 \( -name tests -o -name test \) -print 2>/dev/null
exit 0
'
[ -z "$stdout" ] || fail "build tools, sources or tests are in the final image: $(echo $stdout)"
# The process cannot change its own code, with or without a read-only root filesystem:
# nothing of the environment or of Python belongs to the image's user or is writable by it.
run_to_end 120 "looking for code the leader could overwrite" --entrypoint sh "$image" -c '
find /app /usr/local \( -writable -o -user 10001 -o -group 10001 \) ! -type l -print 2>/dev/null | head -5
'
[ -z "$stdout" ] || fail "the image's user owns or can write its own code: $(echo $stdout)"
# No setting and no secret baked in: every SWARMSCRIBE_* value comes from whoever runs it.
baked="$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$image" | cut -d= -f1 \
  | grep -E -i '^SWARMSCRIBE_|SECRET|PASSWORD|TOKEN|DATABASE_URL|(^|_)KEY$' | grep -v -x 'GPG_KEY' || true)"
[ -z "$baked" ] || fail "the image's environment holds settings or secrets: $(echo $baked)"
if docker history --no-trunc --format '{{.CreatedBy}}' "$image" | grep -q 'SWARMSCRIBE_[A-Z_]*='; then
  fail "a build step of the image sets a SWARMSCRIBE_* value (docker history shows it)"
fi

# --- without configuration, read-only, no network ---------------------------------------
run_to_end 60 "migrate without configuration" "${locked[@]}" --network none "$image" migrate
[ "$status" = "2" ] || fail "migrate without configuration exited $status, not 2"
grep -q 'invalid configuration' <<<"$output" \
  || fail "migrate without configuration did not say so: $output"
if grep -q 'Traceback' <<<"$output"; then
  fail "migrate without configuration ended in a traceback"
fi
run_to_end 60 "the leader's --help, read-only" "${locked[@]}" --network none "$image" --help
[ "$status" = "0" ] || fail "the leader does not run with --read-only --cap-drop ALL: $output"
run_to_end 60 "swarmscribe-admin --help, read-only" "${locked[@]}" --network none \
  --entrypoint swarmscribe-admin "$image" --help
[ "$status" = "0" ] || fail "swarmscribe-admin does not run in the image: $output"
# A database URL that is not one is named in a line, like any other setting: neither a
# traceback nor echoed (it holds the password).
other_settings=(-e SWARMSCRIBE_PUBLIC_URL=http://localhost:8080
  -e SWARMSCRIBE_LINK_KEY=check-only-link-key-0123456789abcdef)
run_to_end 60 "migrate with a malformed database URL" "${locked[@]}" --network none \
  -e SWARMSCRIBE_DATABASE_URL=postgresql://postgres:check-only-password@db:not-a-port/leader \
  "${other_settings[@]}" "$image" migrate
[ "$status" = "2" ] || fail "migrate with a malformed database URL exited $status, not 2: $output"
grep -q 'database_url' <<<"$output" || fail "a malformed database URL was not named: $output"
if grep -q -e 'Traceback' -e 'check-only-password' <<<"$output"; then
  fail "a malformed database URL ended in a traceback or was echoed"
fi
# A database that cannot be reached: one line and status 2, from migrate as from serve.
run_to_end 60 "migrate without a reachable database" "${locked[@]}" --network none \
  -e SWARMSCRIBE_DATABASE_URL=postgresql://postgres:check-only-password@127.0.0.1:1/leader \
  "${other_settings[@]}" "$image" migrate
[ "$status" = "2" ] || fail "migrate without a reachable database exited $status, not 2: $output"
if [ "$(grep -c '' <<<"$output")" != "1" ] || ! grep -q '^error: cannot connect to the database' <<<"$output"; then
  fail "migrate without a reachable database did not say so in one line: $output"
fi
if grep -q 'check-only-password' <<<"$output"; then
  fail "migrate without a reachable database echoed the password"
fi

# --- with a database ----------------------------------------------------------------------
# A throwaway Postgres on a network of its own with no way out of it (--internal): the
# leader reaches Postgres by its container name, nothing is published on the host, and the
# identity provider configured below cannot be reached, as behind an egress rule.
docker network create --internal "$net" >/dev/null
created+=("$pg")
docker run -d --name "$pg" --network "$net" -e POSTGRES_PASSWORD=pw -e POSTGRES_DB=leader \
  postgres:16 >/dev/null
watched=("$pg")
ready=""
for _ in $(seq 1 60); do
  if timeout 10 docker exec "$pg" pg_isready -h 127.0.0.1 -U postgres >/dev/null 2>&1; then
    ready=yes
    break
  fi
  sleep 1
done
[ -n "$ready" ] || fail "the throwaway Postgres was not ready after a minute"
leader_env=(-e "SWARMSCRIBE_DATABASE_URL=postgresql://postgres:pw@$pg:5432/leader"
  -e SWARMSCRIBE_PUBLIC_URL=http://localhost:8080
  -e SWARMSCRIBE_LINK_KEY=check-only-link-key-0123456789abcdef)
# Sign-in with Microsoft Entra ID, for the leaders that serve: a provider that nothing on
# this network can reach.
tenant="0f0e0d0c-0b0a-4908-8706-050403020100"
sign_in=(-e "SWARMSCRIBE_ENTRA_TENANT_ID=$tenant" -e SWARMSCRIBE_ENTRA_CLIENT_ID=check-only-client)
sql() { timeout 30 docker exec "$pg" psql -U postgres -d leader -t -A -c "$1"; }

# A leader never starts on a database it has not migrated.
run_to_end 90 "serve on an unmigrated database (it must refuse to start)" \
  "${locked[@]}" --network "$net" "${leader_env[@]}" "$image" serve
[ "$status" = "2" ] || fail "serve on an unmigrated database exited $status, not 2: $output"
grep -q 'run `swarmscribe-leader migrate`' <<<"$output" \
  || fail "serve on an unmigrated database did not say to migrate: $output"

run_to_end 120 "migrate" "${locked[@]}" --network "$net" "${leader_env[@]}" "$image" migrate
[ "$status" = "0" ] || fail "migrate against a database failed ($status): $output"
head="$(sql 'select version_num from alembic_version')"
grep -q -x "database is at revision $head" <<<"$output" \
  || fail "migrate did not report the revision it reached ($head): $output"
# A second run finds the schema current and does nothing.
run_to_end 120 "a second migrate" "${locked[@]}" --network "$net" "${leader_env[@]}" "$image" migrate
[ "$status" = "0" ] || fail "a second migrate failed ($status): $output"

created+=("$name" "$name-frozen" "$name-early")
docker run -d --name "$name" "${locked[@]}" --network "$net" "${leader_env[@]}" "${sign_in[@]}" \
  "$image" serve >/dev/null
watched=("$name")
probe() {  # path [container] -> the status code, asked from inside the container
  timeout 20 docker exec "${2:-$name}" python -c "
import sys, urllib.request, urllib.error
try:
    print(urllib.request.urlopen('http://127.0.0.1:8080' + sys.argv[1], timeout=8).status)
except urllib.error.HTTPError as e:
    print(e.code)
" "$1" || true
}
for _ in $(seq 1 30); do [ "$(probe /healthz 2>/dev/null)" = "200" ] && break; sleep 1; done
[ "$(probe /healthz)" = "200" ] || fail "/healthz is not 200"
# Ready with the identity provider out of reach: readiness is the database's alone, or an
# identity provider's outage would take every replica away from the followers ...
[ "$(probe /readyz)" = "200" ] \
  || fail "/readyz is not 200 on a migrated database (sign-in is configured and its provider is unreachable)"
[ "$(probe /v1/admin/login-config)" = "200" ] || fail "/v1/admin/login-config is not 200"
# ... and a sign-in is answered 503 meanwhile, in seconds: not accepted, not a 500, not a hang.
started=$SECONDS
answer="$(timeout 30 docker exec "$name" python -c "
import base64, json, sys, urllib.request, urllib.error
part = lambda value: base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b'=').decode()
token = '.'.join([part({'alg': 'RS256', 'kid': 'check-only', 'typ': 'JWT'}),
                  part({'iss': 'https://login.microsoftonline.com/' + sys.argv[1] + '/v2.0'}),
                  'c2lnbmF0dXJl'])
request = urllib.request.Request('http://127.0.0.1:8080/v1/admin/whoami',
                                 headers={'Authorization': 'Bearer ' + token})
try:
    print(urllib.request.urlopen(request, timeout=25).status)
except urllib.error.HTTPError as e:
    print(e.code, e.headers.get('Retry-After'))
" "$tenant" || true)"
[ "$answer" = "503 10" ] \
  || fail "a sign-in while the identity provider is unreachable was answered '$answer', not 503 with Retry-After"
[ $((SECONDS - started)) -le 12 ] \
  || fail "a sign-in while the identity provider is unreachable took $((SECONDS - started)) s to be answered 503"
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
run_to_end 90 "serve on a database that is AHEAD of this leader (it must refuse to start)" \
  "${locked[@]}" --network "$net" "${leader_env[@]}" "$image" serve
watched=("$name")
[ "$status" = "2" ] || fail "serve on a database that is ahead exited $status, not 2: $output"
grep -q 'database is ahead of this leader' <<<"$output" \
  || fail "serve on a database that is ahead did not say so: $output"
# A schema that is behind is not ready: 0001 is a revision every leader knows.
sql "update alembic_version set version_num = '0001'" >/dev/null
sleep 2
[ "$(probe /readyz)" = "503" ] || fail "/readyz is not 503 when the database is BEHIND this leader"
sql "update alembic_version set version_num = '$head'" >/dev/null
sleep 2
[ "$(probe /readyz)" = "200" ] || fail "/readyz did not come back to 200"

# A second serving leader, for the stop further down. Its reaper and scanner run every
# second, so that one of their steps is in the middle of a query when the database freezes.
docker run -d --name "$name-frozen" "${locked[@]}" --network "$net" "${leader_env[@]}" \
  -e SWARMSCRIBE_REAPER_INTERVAL_SECONDS=1 -e SWARMSCRIBE_SCANNER_INTERVAL_SECONDS=1 \
  "$image" serve >/dev/null
for _ in $(seq 1 30); do [ "$(probe /readyz "$name-frozen" 2>/dev/null)" = "200" ] && break; sleep 1; done
if [ "$(probe /readyz "$name-frozen")" != "200" ]; then
  watched=("$name-frozen")
  fail "a second leader did not become ready"
fi

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
watched=("$name-early")
case "$status" in
  143 | 0 | 2) ;;
  *) fail "a stop while waiting for the database exited $status (137: the stop was lost and it was killed)" ;;
esac
[ "$took" -lt 10 ] || fail "a stop while waiting for the database took $took s"

# A stop while SERVING, with the database still silent, in the worst case: a background step
# is in the middle of a query, and requests are in hand that wait for the database. The
# leader gives the requests 10 s and its own shutdown 7 s, then ends by itself (143), long
# before the stop window closes (137). Nothing it abandons needs finishing.
for _ in 1 2 3; do
  docker exec -d "$name-frozen" python -c "
import urllib.request
urllib.request.urlopen(urllib.request.Request(
    'http://127.0.0.1:8080/v1/followers/deregister', data=b'{}', method='POST',
    headers={'Authorization': 'Bearer check-only-no-such-credential',
             'Content-Type': 'application/json'}), timeout=120)
"
done
sleep 2
started=$SECONDS
docker stop --time 60 "$name-frozen" >/dev/null
took=$((SECONDS - started))
status="$(docker inspect --format '{{.State.ExitCode}}' "$name-frozen")"
docker unpause "$pg" >/dev/null
watched=("$name-frozen")
case "$status" in
  0 | 143) ;;
  *) fail "a stop while serving, with a database that does not answer, exited $status after $took s (137 is a kill: the leader never ended by itself)" ;;
esac
[ "$took" -lt 30 ] \
  || fail "a stop while serving, with a database that does not answer, took $took s (the leader's own limits add up to 17 s)"
watched=("$name")

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
logged="$(docker logs "$name" 2>&1; docker logs "$name-frozen" 2>&1)"
if grep -q -e 'check-only-link-key' -e ':pw@' <<<"$logged"; then
  fail "the leader's log holds the link key or the database password"
fi
# And no request: a file link carries its signed token in the URL, so an access log would
# write working links to wherever the logs go. Every probe above was a request.
if grep -q -e 'uvicorn\.access' -e '/readyz' -e 'HTTP/1' <<<"$logged"; then
  fail "the leader logs each request (signed file links would be in the log)"
fi

size="$(docker image inspect --format '{{.Size}}' "$image")"
echo "ok: $image holds the leader and swarmscribe-admin, without the engine, console or follower ($((size / 1000000)) MB)"
