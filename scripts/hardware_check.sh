#!/usr/bin/env bash
# The checks that need the camera and the accelerator, run with the vision service stopped (while it
# runs it owns both, and a second process cannot open them). Run it at a quiet hour: the Pi counts
# nothing while it runs.
#
#   scripts/hardware_check.sh [options]
#
#   --memtest MB          test that much RAM once with memtester (default 512; 0 skips)
#   --baseline SECONDS    run the camera and the model with no rules for that long and print what it
#                         cost (default 600; 0 skips): the figures of plan.md §7 to compare with
#   --hardware-tests      run the tests that need the hardware (pytest --hardware)
#   --clip FOLDER         a folder made by `trafficpi-vision record` (clip.mp4 and profile.json in
#                         it): run the clip through each of the three models, compare them, and
#                         replay each detection log through the profile's lines and stop sign
#   --seconds N           use only the first N seconds of the clip (default: all of it)
#   --log FILE            keep everything printed in FILE as well (default /tmp/trafficpi-hardware-check.log)
#
# Whatever happens, the vision service is started again at the end. It does not reboot, and it
# changes no setting. Look at `vcgencmd measure_temp` first: the memory test and the models load the
# board, and a Pi that is already at 80 degrees should be left to cool.
set -u
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
memtest_mb=512
baseline_s=600
hardware_tests=0
clip=""
seconds=0
log=/tmp/trafficpi-hardware-check.log

while [[ $# -gt 0 ]]; do
  case "$1" in
    --memtest) memtest_mb="${2:?--memtest wants a number of megabytes}"; shift ;;
    --baseline) baseline_s="${2:?--baseline wants a number of seconds}"; shift ;;
    --hardware-tests) hardware_tests=1 ;;
    --clip) clip="${2:?--clip wants a folder}"; shift ;;
    --seconds) seconds="${2:?--seconds wants a number of seconds}"; shift ;;
    --log) log="${2:?--log wants a file}"; shift ;;
    -h|--help) awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done
for number in "$memtest_mb" "$baseline_s" "$seconds"; do
  if [[ ! "$number" =~ ^[0-9]+$ ]]; then echo "a number is wanted, not '$number'" >&2; exit 2; fi
done
if [[ -n "$clip" && ! -f "$clip/clip.mp4" ]]; then echo "no clip.mp4 in $clip" >&2; exit 2; fi
# the script changes into backend/ below, so a path given relative to where it was started must be made whole now
if [[ -n "$clip" ]]; then clip="$(realpath "$clip")"; fi
log="$(realpath -m "$log")"

exec > >(tee -a "$log") 2>&1
export HAILORT_LOGGER_PATH=NONE  # HailoRT would otherwise write hailort.log into the current directory
cd "$root/backend" || exit 2

started_service=0
restore() {
  echo "== starting the vision service again: $(date +%T)"
  sudo systemctl start trafficpi-vision && started_service=1
  sleep 20
  curl -fsS --max-time 5 http://127.0.0.1:8001/health || echo "(the vision service does not answer yet: look at journalctl -u trafficpi-vision)"
  echo
  echo "== done: $(date +%T), service started: $started_service, $(vcgencmd measure_temp 2>/dev/null)"
}
trap restore EXIT

echo "== hardware check, $(date '+%Y-%m-%d %H:%M:%S'), $(vcgencmd measure_temp 2>/dev/null), $(git -C "$root" log -1 --oneline)"
echo "== stopping the vision service"
sudo systemctl stop trafficpi-vision
sleep 5

if (( memtest_mb > 0 )); then
  echo "== memtester ${memtest_mb}M, one pass (a line with FAILURE means a bad page: plan.md R16)"
  sudo memtester "${memtest_mb}M" 1 2>&1 | tail -30
fi

if (( hardware_tests )); then
  echo "== the tests that need the hardware"
  uv run pytest --hardware -q -p no:cacheprovider 2>&1 | tail -15
fi

if (( baseline_s > 0 )); then
  echo "== baseline: ${baseline_s} s of camera and model, no rules"
  uv run trafficpi-vision dry-run --seconds "$baseline_s" 2>&1 | grep -v "WARN V4L2" | tail -22
fi

if [[ -n "$clip" ]]; then
  limit=()
  if (( seconds > 0 )); then limit=(--seconds "$seconds"); fi
  # at the live service's score threshold, so that a replay is what the live pipeline would have done
  common=(--threshold 0.25 "${limit[@]}")
  echo "== the clip through three models: $clip"
  declare -A models=(
    [yolov8s]="--model yolov8s"
    [yolov6n]="--model yolov6n"
    [yolox]="--hef /usr/share/hailo-models/yolox_s_leaky_h8l_rpi.hef"
  )
  for name in yolov8s yolov6n yolox; do
    echo "-- redetect with $name: $(date +%T)"
    # shellcheck disable=SC2086  # the options are written out above, one word each
    uv run trafficpi-vision redetect "$clip/clip.mp4" ${models[$name]} "${common[@]}" --out "$clip/det-$name.jsonl" 2>&1 | grep -v "WARN V4L2" | tail -14
  done
  echo "== comparing the logs, frame by frame (scores of 0.25 and over)"
  for other in yolov6n yolox; do
    echo "-- yolov8s against $other"
    uv run trafficpi-vision compare "$clip/det-yolov8s.jsonl" "$clip/det-$other.jsonl" --min-score 0.25 2>&1 | tail -25
  done
  if [[ -f "$clip/profile.json" ]]; then
    for name in yolov8s yolov6n yolox; do
      echo "== replay of $name through the profile (counts and passes)"
      uv run trafficpi-vision replay "$clip/det-$name.jsonl" --profile "$clip/profile.json" 2>&1 | head -24
    done
  fi
fi
