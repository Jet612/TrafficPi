#!/usr/bin/env bash
# Build the static demo: the dashboard with no Pi behind it, answering from a recording of the
# made-up street, as plain files that any static host can serve.
#
#   scripts/build_static_demo.sh OUT_DIR [--warmup SECONDS] [--loop SECONDS] [--recording DIR]
#
# It starts a made-up street of its own (on ports 18100 and 18101, with its own data directory),
# saves a profile with a counting line, a stop sign and a zone, waits --warmup seconds (default 600)
# for counts and passes to build up, records it with scripts/record_demo.py, and stops it. Then it
# builds the dashboard with the demo switched on, in a copy of dashboard/ (dashboard/out is what the
# API serves, so it is never touched), and puts the recording beside the built pages in OUT_DIR.
# --recording DIR uses a recording made before (the --out of record_demo.py) instead.
#
# The site must be served from the root of its host (https://demo.example/, not
# https://example.github.io/TrafficPi/): the dashboard and the recording ask for /api/... and
# /demo/... from the root. Try it with:
#
#   python3 scripts/serve_static_demo.py OUT_DIR     # then open http://127.0.0.1:18200/
#
# The build runs on two cores at low priority under the heavy-job lock, like every heavy job on the
# Pi; the street is started outside the lock, so that it does not hold the lock while it runs.
set -euo pipefail
usage="usage: build_static_demo.sh OUT_DIR [--warmup SECONDS] [--loop SECONDS] [--recording DIR]"
out="${1:?$usage}"
shift
warmup=600
loop=45
recording=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --warmup) warmup="${2:?$usage}"; shift 2 ;;
    --loop) loop="${2:?$usage}"; shift 2 ;;
    --recording) recording="${2:?$usage}"; shift 2 ;;
    *) echo "$usage" >&2; exit 2 ;;
  esac
done

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
out="$(mkdir -p "$out" && cd "$out" && pwd)"
work="$(mktemp -d -t trafficpi-demo.XXXXXX)"
api_port=18100
vision_port=18101

stop_street() { # whatever listens on these ports is this script's own street
  local pids
  pids=$(ss -ltnp 2>/dev/null | grep -E ":($api_port|$vision_port)\b" | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u)
  for pid in $pids; do kill -TERM "$pid" 2>/dev/null || true; done
}
cleanup() {
  [[ -n "$recording" ]] || stop_street
  rm -rf "$work"
}
trap cleanup EXIT

heavy=(taskset -c "2,3" nice -n 15)

if [[ -z "$recording" ]]; then
  recording="$work/recording"
  password="$(head -c 18 /dev/urandom | base64)"
  # start_demo.sh refuses ports that are in use, so this never records a service that counts a real street.
  E2E_PASSWORD="$password" "${heavy[@]}" "$root/scripts/start_demo.sh" "$work/data" "$work/logs" "$api_port" "$vision_port"
  (cd "$root/backend" && E2E_PASSWORD="$password" "${heavy[@]}" uv run python ../scripts/record_demo.py \
    --api "http://127.0.0.1:$api_port" --out "$recording" --set-profile --warmup "$warmup" --loop "$loop")
  stop_street
fi
recorded_at="$(cat "$recording/demo/recorded_at")"

echo "building the dashboard with the demo switched on"
rsync -a --exclude node_modules --exclude .next --exclude out --exclude '*.tsbuildinfo' "$root/dashboard/" "$work/dashboard/"
(
  cd "$work/dashboard"
  "${heavy[@]}" pnpm install --frozen-lockfile --offline > "$work/install.log" 2>&1 || { tail -n 20 "$work/install.log" >&2; exit 1; }
  NEXT_TELEMETRY_DISABLED=1 NEXT_PUBLIC_TRAFFICPI_DEMO=1 NEXT_PUBLIC_TRAFFICPI_DEMO_AT="$recorded_at" \
    "${heavy[@]}" flock /tmp/trafficpi-heavy.lock pnpm build
)

find "$out" -mindepth 1 -delete
cp -r "$work/dashboard/out/." "$out/"
cp -r "$recording/." "$out/"
rm -f "$out/demo/recorded_at"
echo "the demo is in $out ($(du -sh "$out" | cut -f1)); try it with: python3 scripts/serve_static_demo.py $out"
