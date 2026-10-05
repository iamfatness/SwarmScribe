#!/usr/bin/env bash
# What a swarmscribe-follower image must hold, checked without a leader and without a GPU:
#
#   bash docker/check-follower-image.sh swarmscribe-follower:e2e cpu tiny.en
#   bash docker/check-follower-image.sh swarmscribe-follower:cuda cuda
#
# The second argument is the build target; the third is the first model baked in with
# MODELS, when there is one. CHECK_GPU=1 also runs a baked `cuda` image on this machine's
# GPU (never in CI). The Compose test (e2e/follower-compose) checks the rest by running it.
set -euo pipefail
# Git Bash on Windows would rewrite /scratch and friends into Windows paths.
export MSYS_NO_PATHCONV=1

image="${1:?usage: check-follower-image.sh <image> <cpu|cuda> [baked-model]}"
target="${2:?usage: check-follower-image.sh <image> <cpu|cuda> [baked-model]}"
baked="${3:-}"
state=/var/lib/swarmscribe-follower
# A leader that is never reached: every check here runs with --network none.
leader=(-e SWARMSCRIBE_LEADER_URL=https://leader.invalid)
locked=(--read-only --cap-drop ALL --security-opt no-new-privileges --network none)

fail() {
  echo "FAILED: $*" >&2
  exit 1
}

case "$target" in cpu | cuda) ;; *) fail "the target is '$target', not cpu or cuda" ;; esac

setting() {  # the value of one variable in the image's environment
  docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$image" \
    | sed -n "s/^$1=//p"
}

label() {  # the value of one of the image's labels; empty when it is not set
  docker inspect --format "{{with .Config.Labels}}{{index . \"$1\"}}{{end}}" "$image"
}

# --- what it says it is --------------------------------------------------------------------
# The labels are what a registry shows, and `source` is what links the package to the
# repository: a wrong one is not noticed by anything that runs.
[ "$(label org.opencontainers.image.title)" = "swarmscribe-follower" ] \
  || fail "the title label is '$(label org.opencontainers.image.title)', not swarmscribe-follower"
[ "$(label org.opencontainers.image.source)" = "https://github.com/iamfatness/SwarmScribe" ] \
  || fail "the source label is '$(label org.opencontainers.image.source)', not the repository"
case "$(label org.opencontainers.image.description)" in
  *SwarmScribe*follower*) ;;
  *) fail "the description label is '$(label org.opencontainers.image.description)'" ;;
esac
installed="$(docker run --rm --entrypoint python "$image" -c \
  'from importlib.metadata import version; print(version("swarmscribe-follower"))')"
[ -n "$installed" ] || fail "the image does not say which version of the follower it holds"
[ "$(label org.opencontainers.image.version)" = "$installed" ] \
  || fail "the version label is '$(label org.opencontainers.image.version)', the follower in the image is $installed"
version_said="$(docker run --rm "$image" --version)" \
  || fail "swarmscribe-follower --version does not run"
grep -q -x "swarmscribe-follower $installed" <<<"$version_said" \
  || fail "swarmscribe-follower --version does not say $installed"

# --- who it runs as, and where it may write --------------------------------------------
user="$(docker inspect --format '{{.Config.User}}' "$image")"
[ "$user" = "10001:10001" ] || fail "the image's user is '$user', not 10001:10001"
[ "$(docker run --rm --entrypoint id "$image" -u)" = "10001" ] \
  || fail "the container does not run as uid 10001"

volumes="$(docker inspect --format '{{range $path, $_ := .Config.Volumes}}{{println $path}}{{end}}' "$image" | sort | xargs)"
[ "$volumes" = "/scratch $state" ] \
  || fail "the declared volumes are '$volumes', not /scratch and $state"

[ "$(setting HOME)" = "$state" ] || fail "HOME is not the state folder"
[ "$(setting SWARMSCRIBE_FOLLOWER_STATE_DIR)" = "$state" ] || fail "the state folder is not set"
[ "$(setting SWARMSCRIBE_FOLLOWER_SCRATCH_DIR)" = "/scratch" ] || fail "the scratch folder is not set"
[ "$(setting SWARMSCRIBE_FOLLOWER_MODEL_DIR)" = "/models" ] || fail "the model folder is not set"

# The listener for /healthz and /metrics is on loopback only, and the image probes it.
[ "$(setting SWARMSCRIBE_FOLLOWER_HEALTH_ADDR)" = "127.0.0.1:9108" ] \
  || fail "the health listener is not on 127.0.0.1:9108"
healthcheck="$(docker inspect --format '{{json .Config.Healthcheck}}' "$image")"
grep -q '/healthz' <<<"$healthcheck" \
  || fail "the image has no HEALTHCHECK on /healthz"

# The credential is refused in a folder that others can write to, or that is not its own.
docker run --rm --entrypoint sh "$image" -c \
  'test "$(stat -c "%u:%g %a" /var/lib/swarmscribe-follower)" = "10001:10001 700" \
   && test "$(stat -c "%u:%g %a" /scratch)" = "10001:10001 700" \
   && test -z "$(find /var/lib/swarmscribe-follower /scratch -mindepth 1)"' \
  || fail "the state and scratch folders are not empty, 0700 and owned by 10001"

# --- what is in it, and what is not ------------------------------------------------------
docker run --rm --entrypoint python "$image" -c '
import swarmscribe_engine, swarmscribe_follower, swarmscribe_protocol  # noqa: F401
import av, ctranslate2, faster_whisper  # noqa: F401
' || fail "the follower, the engine or a model library does not import"

# The follower imports the engine and the protocol and nothing else internal (master spec,
# section 4): no leader, no console, none of their web and database libraries.
docker run --rm --entrypoint python "$image" -c '
import importlib.util
import sys

banned = ("swarmscribe_leader", "swarmscribe_console", "fastapi", "starlette", "uvicorn",
          "sqlalchemy", "asyncpg", "alembic", "jwt", "cryptography", "torch", "pytest")
found = [name for name in banned if importlib.util.find_spec(name)]
sys.exit(f"leader, console or test libraries in the image: {found}" if found else 0)
' || fail "the image carries the leader, the console or their libraries"

for tool in uv gcc cc make git curl; do
  if docker run --rm --entrypoint sh "$image" -c "command -v $tool" >/dev/null; then
    fail "a build tool ($tool) is in the final image"
  fi
done

# No pip cache, no test files of ours, no source tree: only the installed wheels.
if docker run --rm --entrypoint sh "$image" -c \
  'find / -xdev \( -path "*/.cache/pip*" -o -path /root/.cache -o -path /app/packages \) 2>/dev/null | grep .'; then
  fail "the image holds a pip cache or the source tree"
fi
if docker run --rm --entrypoint sh "$image" -c \
  'find /app -xdev \( -name "test_*.py" -o -name "conftest.py" -o -name tests \) -path "*swarmscribe*" 2>/dev/null | grep .'; then
  fail "the image holds test files of ours"
fi
# The installed SwarmScribe distributions: the follower and the two packages it imports.
dists="$(docker run --rm --entrypoint python "$image" -c '
from importlib.metadata import distributions
print(" ".join(sorted(d.metadata["Name"] for d in distributions() if d.metadata["Name"].startswith("swarmscribe"))))')"
[ "$dists" = "swarmscribe-engine swarmscribe-follower swarmscribe-protocol" ] \
  || fail "the installed SwarmScribe distributions are '$dists'"

# No secret is baked in: no token, credential or leader in the environment, and no file
# that looks like one (the state folder was checked empty above).
if docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$image" \
  | grep -E '^(SWARMSCRIBE_JOIN_TOKEN|SWARMSCRIBE_JOIN_TOKEN_FILE|SWARMSCRIBE_LEADER_URL|HF_TOKEN|HUGGING_FACE_HUB_TOKEN)='; then
  fail "the image's environment holds a token or a leader"
fi
if docker history --no-trunc --format '{{.CreatedBy}}' "$image" \
  | grep -E -i 'token|secret|password|credential\.json|SWARMSCRIBE_LEADER_URL'; then
  fail "the image's history mentions a token, a secret or a leader"
fi
if docker run --rm --entrypoint sh "$image" -c \
  'find / -xdev \( -name credential.json -o -name "*.env" -o -name token \) -not -path "/proc/*" 2>/dev/null | grep .'; then
  fail "the image holds a file that looks like a secret"
fi

# --- how it starts -------------------------------------------------------------------------
# An init is PID 1, and the follower its child: see the Dockerfile for why.
entrypoint="$(docker inspect --format '{{json .Config.Entrypoint}}' "$image")"
[ "$entrypoint" = '["/usr/bin/tini","--","swarmscribe-follower"]' ] \
  || fail "the entrypoint is $entrypoint, not tini and the follower"
[ "$(docker inspect --format '{{json .Config.Cmd}}' "$image")" = '["run"]' ] \
  || fail "the default command is not run"
docker run --rm --entrypoint /usr/bin/tini "$image" --version >/dev/null \
  || fail "tini does not run in the image"
# Read-only root filesystem, no network, no configuration: it names what is missing and
# exits 2, without a traceback.
status=0
output="$(docker run --rm "${locked[@]}" "$image" 2>&1)" || status=$?
[ "$status" = "2" ] || fail "run without configuration exited $status, not 2"
grep -q 'invalid configuration' <<<"$output" \
  || fail "run without configuration did not say so: $output"
if grep -q 'Traceback' <<<"$output"; then
  fail "run without configuration ended in a traceback"
fi

# The three writable paths as tmpfs, as a chart would give them: the state folder needs its
# owner and mode said (a plain tmpfs is root's and world-writable, and is refused).
ready="$(docker run --rm "${locked[@]}" "${leader[@]}" -e SWARMSCRIBE_FOLLOWER_DEVICE=cpu \
  --tmpfs "$state:uid=10001,gid=10001,mode=0700" \
  --tmpfs /scratch:uid=10001,gid=10001,mode=0700 \
  --tmpfs /models:uid=10001,gid=10001,mode=0755 \
  "$image" doctor --no-model --no-leader)" \
  || fail "doctor does not pass on a read-only root with tmpfs on the three writable paths"
grep -q '^result: ready$' <<<"$ready" \
  || fail "doctor does not pass on a read-only root with tmpfs on the three writable paths"

# --- the device ------------------------------------------------------------------------------
if [ "$target" = "cuda" ]; then
  [ "$(setting SWARMSCRIBE_FOLLOWER_DEVICE)" = "cuda" ] || fail "the cuda image does not ask for cuda"
  # The library the locked CTranslate2 loads by name at the first inference. It needs no
  # GPU to load, so this is checked everywhere; that it WORKS is checked on a GPU only.
  docker run --rm --entrypoint python "$image" -c 'import ctypes; ctypes.CDLL("libcublas.so.12")' \
    || fail "libcublas.so.12 does not load by name"
  # Without a GPU the cuda image must say so, not fall back to the CPU.
  status=0
  output="$(docker run --rm "${locked[@]}" "${leader[@]}" "$image" doctor --no-leader 2>&1)" || status=$?
  [ "$status" = "3" ] || fail "the cuda image without a GPU exited $status, not 3: $output"
  grep -q 'cuda was requested' <<<"$output" || fail "the cuda image did not name the device"
else
  [ -z "$(setting SWARMSCRIBE_FOLLOWER_DEVICE)" ] || fail "the cpu image sets a device"
  if docker run --rm --entrypoint python "$image" -c 'import ctypes; ctypes.CDLL("libcublas.so.12")' 2>/dev/null; then
    fail "the cpu image carries cuBLAS"
  fi
fi

# --- the model -------------------------------------------------------------------------------
if [ -z "$baked" ]; then
  [ -z "$(setting SWARMSCRIBE_FOLLOWER_STARTUP_MODEL)" ] || fail "a start-up model is set without a baked model"
  [ "$(setting SWARMSCRIBE_FOLLOWER_OFFLINE)" = "0" ] || fail "offline mode is on without a baked model"
  docker run --rm --entrypoint sh "$image" -c 'test -z "$(ls -A /models)"' \
    || fail "/models is not empty in an image without a baked model"
else
  [ "$(setting SWARMSCRIBE_FOLLOWER_STARTUP_MODEL)" = "$baked" ] \
    || fail "the start-up model is '$(setting SWARMSCRIBE_FOLLOWER_STARTUP_MODEL)', not $baked"
  [ "$(setting SWARMSCRIBE_FOLLOWER_OFFLINE)" = "1" ] || fail "a baked image is not offline"
  cached="$(docker run --rm "${locked[@]}" "${leader[@]}" -e SWARMSCRIBE_FOLLOWER_DEVICE=cpu \
    "$image" doctor --no-model --no-leader)" \
    || fail "the baked model $baked is not in /models"
  grep -E -q "^cached models: (.*, )?$baked(,|\$)" <<<"$cached" \
    || fail "the baked model $baked is not in /models"

  # The files in the image are the files the lock file names, hashed again INSIDE the image
  # (the build checked them once; this checks what was shipped), and no other file is there.
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  docker run --rm -i --entrypoint python "$image" -c '
import hashlib
import json
import sys
from pathlib import Path

entry = json.load(sys.stdin)[sys.argv[1]]
cache = Path("/models") / ("models--" + entry["repo"].replace("/", "--"))
snapshot = cache / "snapshots" / entry["revision"]
found = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in snapshot.iterdir()}
if found != entry["files"]:
    sys.exit("baked files differ from the lock file: " + str(sorted(set(found.items()) ^ set(entry["files"].items()))))
ref = (cache / "refs" / "main").read_text()
if ref != entry["revision"]:
    sys.exit("refs/main is " + ref + ", not " + entry["revision"])
' "$baked" < "$here/models.lock.json" \
    || fail "the baked $baked does not match docker/models.lock.json inside the image"

  gpu=()
  if [ "$target" = "cuda" ]; then
    gpu=(--gpus all)
  fi
  if [ "$target" = "cpu" ] || [ "${CHECK_GPU:-0}" = "1" ]; then
    # The model loads and runs a real inference with no network at all and nothing
    # writable but the two declared volumes: nothing is fetched at run time.
    output="$(docker run --rm "${locked[@]}" "${gpu[@]}" "${leader[@]}" "$image" doctor --no-leader 2>&1)" \
      || fail "doctor failed offline: $output"
    grep -q "^model: $baked (.*) loaded and ran\$" <<<"$output" \
      || fail "the baked model did not load and run offline: $output"
    grep -q '^result: ready$' <<<"$output" || fail "doctor is not ready: $output"
    if [ "$target" = "cuda" ]; then
      grep -q '^device: cuda (' <<<"$output" || fail "doctor did not run on the GPU: $output"
    fi

    name="follower-check-$$"
    trap 'docker rm -f "$name" "$name-early" "$name-proxy" >/dev/null 2>&1 || true' EXIT

    # A stop at once, before start-up has got anywhere: it must END the container, never be
    # lost. Lost means Docker kills it when the stop window closes (137), and with the long
    # window a follower is given (`--stop-timeout 930`) that is a quarter of an hour later,
    # in the middle of a job. 0: the follower's own handlers caught it (entry.py installs
    # them before anything is imported). 143: it came before the interpreter was up, and
    # the init ended the follower. The delays cover the 0.3 s in which it used to be lost.
    # A stop that lands in the model load waits for the load (it cannot be interrupted),
    # which is a second for tiny.en and far longer for a large model on a GPU.
    early_limit=3
    if [ "$target" = "cuda" ]; then
      early_limit=60
    fi
    for delay in 0 0 0 0.05 0.1 0.15 0.2 0.3 0.5; do
      docker run -d --name "$name-early" "${locked[@]}" "${gpu[@]}" "${leader[@]}" \
        -e SWARMSCRIBE_JOIN_TOKEN=not-a-token "$image" >/dev/null
      sleep "$delay"
      begun="$(date +%s)"
      docker stop --time 60 "$name-early" >/dev/null
      took="$(($(date +%s) - begun))"
      status="$(docker inspect --format '{{.State.ExitCode}}' "$name-early")"
      case "$status" in
        0 | 143) ;;
        *) fail "a stop $delay s after the start exited $status, not 0 or 143 (137: the stop was lost and it was killed)" ;;
      esac
      [ "$took" -lt "$early_limit" ] || fail "a stop $delay s after the start took $took s"
      early_log="$(docker logs "$name-early" 2>&1)"
      if grep -q 'Traceback' <<<"$early_log"; then
        fail "a stop $delay s after the start ended in a traceback"
      fi
      docker rm -f "$name-early" >/dev/null
    done

    # `docker stop` during start-up ends it with exit 0, well inside Docker's 10 s.
    # With no network the registration is retried for ever, which is where it is stopped.
    docker run -d --name "$name" "${locked[@]}" "${gpu[@]}" "${leader[@]}" \
      --health-interval 2s --health-start-period 1s \
      -e SWARMSCRIBE_JOIN_TOKEN=not-a-token "$image" >/dev/null
    for _ in $(seq 1 60); do
      logs="$(docker logs "$name" 2>&1)"
      grep -q 'loaded on' <<<"$logs" && break
      sleep 1
    done
    logs="$(docker logs "$name" 2>&1)"
    grep -q 'loaded on' <<<"$logs" || fail "the follower never loaded its model"
    # PID 1 is the init, and the follower is the only thing it started.
    pid1="$(docker exec "$name" cat /proc/1/cmdline | xargs -0 echo)" \
      || fail "PID 1 is not tini running the follower"
    grep -q -x '/usr/bin/tini -- swarmscribe-follower run' <<<"$pid1" \
      || fail "PID 1 is not tini running the follower"
    children="$(docker exec "$name" sh -c \
      'for s in /proc/[0-9]*/status; do grep -q "^PPid:[[:space:]]*1\$" "$s" && tr "\0" " " <"${s%status}cmdline" && echo; done; true')"
    grep -q 'bin/swarmscribe-follower run' <<<"$children" \
      || fail "the follower is not the child of PID 1: $children"

    # Healthy with no leader in reach: /healthz is about the follower's own threads.
    health=""
    for _ in $(seq 1 30); do
      health="$(docker inspect --format '{{.State.Health.Status}}' "$name")"
      [ "$health" = "healthy" ] && break
      sleep 1
    done
    [ "$health" = "healthy" ] || fail "Docker reports the follower $health while it waits for a leader"
    metrics="$(docker exec "$name" python -c \
      "import urllib.request; print(urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://127.0.0.1:9108/metrics', timeout=3).read().decode())")" \
      || fail "/metrics does not answer"
    grep -q '^swarmscribe_follower_state{state="idle"} 1.0$' <<<"$metrics" \
      || fail "/metrics does not show an idle follower"
    grep -q '^swarmscribe_follower_model_load_seconds [0-9]' <<<"$metrics" \
      || fail "/metrics does not show the model load"
    # Behind a proxy (HTTP_PROXY set) the image's own probe must still reach 127.0.0.1: it
    # asks for no proxy, so a follower that works is not reported unhealthy.
    docker run -d --name "$name-proxy" "${locked[@]}" "${gpu[@]}" "${leader[@]}" \
      --health-interval 2s --health-start-period 1s \
      -e HTTP_PROXY=http://127.0.0.1:9 -e http_proxy=http://127.0.0.1:9 \
      -e SWARMSCRIBE_JOIN_TOKEN=not-a-token "$image" >/dev/null \
      || fail "the follower did not start with a proxy configured"
    health=""
    for _ in $(seq 1 90); do
      health="$(docker inspect --format '{{.State.Health.Status}}' "$name-proxy")" \
        || fail "cannot read the health of the follower run with a proxy"
      [ "$health" = "healthy" ] && break
      sleep 1
    done
    [ "$health" = "healthy" ] || fail "Docker reports the follower $health when HTTP_PROXY is set (the probe went through the proxy)"
    docker rm -f "$name-proxy" >/dev/null
    begun="$(date +%s)"
    docker stop --time 8 "$name" >/dev/null
    took="$(($(date +%s) - begun))"
    status="$(docker inspect --format '{{.State.ExitCode}}' "$name")"
    [ "$status" = "0" ] || fail "docker stop during start-up exited $status, not 0 (137 is a kill)"
    [ "$took" -lt 3 ] || fail "docker stop during start-up took $took s"
    logs="$(docker logs "$name" 2>&1)"
    if grep -q 'not-a-token' <<<"$logs"; then
      fail "the join token is in the log"
    fi
  fi
fi

size="$(docker image inspect --format '{{.Size}}' "$image")"
echo "ok: $image is a $target follower${baked:+ with $baked baked in} ($((size / 1000000)) MB)"
