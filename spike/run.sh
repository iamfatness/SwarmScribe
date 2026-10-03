#!/usr/bin/env bash
# rtpengine feasibility spike runner (GitHub Actions ubuntu runner, sudo available)
set -u
OUT=${OUT:-$PWD/out}
mkdir -p "$OUT/info"
SPOOL=/var/spool/rtpengine
KERNEL=0

log() { echo "::group::$*"; }
end() { echo "::endgroup::"; }

log "versions + help"
dpkg -l | grep -i rtpengine | tee "$OUT/info/dpkg.txt"
rtpengine --version 2>&1 | tee "$OUT/info/version.txt"
rtpengine --help > "$OUT/info/rtpengine-help.txt" 2>&1
rtpengine-recording --help > "$OUT/info/recording-help.txt" 2>&1
grep -iE 'record|mix|output|forward|tcp|subscri|notify|websocket|janus|kernel|table' "$OUT/info/recording-help.txt" "$OUT/info/rtpengine-help.txt" | head -150
end

log "kernel module"
uname -r
if sudo modprobe xt_RTPENGINE 2>&1 | tee "$OUT/info/modprobe.txt"; then :; fi
if lsmod | grep -qi rtpengine; then KERNEL=1; echo "KERNEL MODULE LOADED"; ls -la /proc/rtpengine || true; else echo "no kernel module"; fi
echo "$KERNEL" > "$OUT/info/kernel_loaded.txt"
end

sudo systemctl stop rtpengine-daemon rtpengine-recording-daemon ngcp-rtpengine-daemon 2>/dev/null || true
sudo pkill -f rtpengine || true

HELP_REC="$OUT/info/recording-help.txt"
has() { grep -q -- "$1" "$HELP_REC"; }

run_scenario() {
  local name=$1 method=$2 rtpflags=$3 recflags=$4 driverflags=$5
  log "scenario $name"
  local d="$OUT/$name"; mkdir -p "$d/rec"
  sudo rm -rf $SPOOL; sudo mkdir -p $SPOOL; sudo chmod 777 $SPOOL
  local table=-1; [ "$KERNEL" = 1 ] && table=0
  if [ "$method" = proc ] && [ -n "$recflags_ok" ]; then :; fi
  sudo rtpengine --foreground --log-stderr --log-level=7 --interface=127.0.0.1 --listen-ng=127.0.0.1:2223 \
     --port-min=40000 --port-max=40200 --table=$table --recording-dir=$SPOOL --recording-method=$method \
     $rtpflags > "$d/rtpengine.log" 2>&1 &
  sleep 1
  local recpid=""
  if [ "$method" = proc ]; then
    sudo rtpengine-recording --foreground --log-stderr --log-level=7 --spool-dir=$SPOOL --output-dir="$d/rec" \
      $recflags > "$d/recording.log" 2>&1 &
    recpid=$!
  fi
  sleep 1.5
  (cd "$d" && python3 "$GITHUB_WORKSPACE/spike/ng_drive.py" $driverflags) 2>&1 | tee "$d/driver.log"
  sleep 4
  sudo pkill -INT -f rtpengine-recording || true
  sleep 1
  sudo pkill -INT -x rtpengine || true
  sleep 1
  sudo pkill -9 -f rtpengine || true
  sudo cp -r $SPOOL "$d/spool" 2>/dev/null || true
  sudo chmod -R a+rwX "$d"
  echo "--- files:"; find "$d" -type f | sort
  echo "--- rtpengine.log tail"; tail -40 "$d/rtpengine.log"
  [ -f "$d/recording.log" ] && { echo "--- recording.log"; tail -60 "$d/recording.log"; }
  python3 "$GITHUB_WORKSPACE/spike/analyze.py" "$d/rec" "$d/tap" "$d/spool" 2>&1 | tee "$d/analysis.txt"
  end
}
recflags_ok=1

MIX=""; has mix-method && MIX="--mix-method=channels"
SCEN=${SCENARIOS:-"rtp_wav srtp_wav srtp_mp3 srtp_tap pcap"}
for s in $SCEN; do
  case $s in
    rtp_wav)  run_scenario rtp_wav  proc "" "--output-format=wav --output-mixed --output-single $MIX" "--plain" ;;
    srtp_wav) run_scenario srtp_wav proc "" "--output-format=wav --output-mixed --output-single $MIX" "" ;;
    srtp_mp3) run_scenario srtp_mp3 proc "" "--output-format=mp3 --output-mixed --output-single" "" ;;
    srtp_mixdirect) run_scenario srtp_mixdirect proc "" "--output-format=wav --output-mixed --mix-method=direct" "" ;;
    srtp_tap) run_scenario srtp_tap proc "" "--output-format=wav --output-mixed $MIX" "--subscribe" ;;
    pcap)     run_scenario pcap pcap "" "" "" ;;
  esac
done
exit 0
